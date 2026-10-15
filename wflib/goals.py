"""目标与完成判据。
workflow/goals.json 示例：
{"goals": [
  {"id": "G1", "title": "模拟盘实现与回测逐根一致", "kind": "exec",
   "check": "python reconcile.py", "pass": {"exit": 0, "contains": ["实现一致"]},
   "means": "实现正确", "on_fail": "继续迭代"},
  {"id": "G2", "title": "交易平台合规可用", "kind": "manual", "means": "可以进入实盘评审", "on_fail": "暂停实盘评审"}
]}
- exec 类：由驱动器在验收后运行 check 命令，按 pass 条件判定；输出存为证据。
- manual 类：只能由用户用 wf goals confirm 确认；确认绑定在当时的目标内容上，目标一改动确认即失效。
- 目标只有在用户批准后才生效（wf goals approve）；未批准时可以显示，但不能据此判定完成。

B1-C（证据分层与 NA）：ok、reached、done、total 的含义不变；新增 layer、counts、binding、na 等键，只增不改。
- layer：pending | failed | mechanical_pass | verified | not_applicable | confirmed（人工目标由用户确认）。
- mechanical_pass：检查命令按 pass 条件通过（即原来的 ok）。任何命令退出码 0 都只到这一层。
- verified：只由 run_checks 在本进程内直接调用 live.check 得到的结果授予；目标要声明 "live": {"rounds", "segments"}，
  并绑定项目、实景段、工具文件摘要与是否 dirty、项目指纹、goals_sha、证据文件 sha256。任何一项变化或证据被改动即失效，
  退回 mechanical_pass。goals_state.json 里的旧记录没有这些绑定，只能推出原有的机械状态。
- not_applicable：mark_na 记录条件、排除范围、理由、判定人四项；最近一次检查失败或超时（退出码非 0、124）不能记 NA。
  NA 不计入 ok，也不计入 reached。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import policy
from .core import WF_HOME, Instance, WfError, now_iso, read_json, write_json

LAYERS = ("pending", "failed", "mechanical_pass", "verified", "not_applicable", "confirmed")
NA_FIELDS = ("condition", "scope", "reason", "by")
LIVE_SEGMENTS = tuple(range(1, 11))
_TOOL_ROOT = WF_HOME   # 工具文件摘要与 dirty 的来源；自检里只在隔离子进程内临时指向夹具版本库


def load(inst: Instance):
    return read_json(inst.wf / "goals.json")


def validate(g: dict) -> list:
    issues = []
    if not isinstance(g, dict) or not isinstance(g.get("goals"), list) or not g["goals"]:
        return ["goals.json 需要非空的 goals 列表"]
    ids = set()
    for x in g["goals"]:
        gid = x.get("id")
        if not gid or gid in ids:
            issues.append(f"目标编号缺失或重复：{gid}")
        ids.add(gid)
        if x.get("kind") not in ("exec", "manual"):
            issues.append(f"{gid}：kind 只能是 exec 或 manual")
        if x.get("kind") == "exec":
            if not x.get("check"):
                issues.append(f"{gid}：exec 类目标需要 check 命令")
            elif policy.GUARDRAIL.search(x["check"]):
                issues.append(f"{gid}：check 命令不能是护栏命令")
            if not isinstance(x.get("pass"), dict):
                issues.append(f"{gid}：需要 pass 条件（exit / contains / not_contains / regex）")
        for k in ("title", "means", "on_fail"):
            if not x.get(k):
                issues.append(f"{gid}：缺少 {k}")
    from . import policy as _pol   # 1.9.3：目标的检查命令也必须是只读命令
    for x in g["goals"]:
        # 1.9.4（审查 concerns）：只拒“要人批准的 wf 命令”证明不了只读，改为按 policy.readonly_check 的允许清单判定
        if isinstance(x, dict) and x.get("kind") == "exec" and x.get("check") and not _pol.readonly_check(str(x["check"])):
            issues.append(f"{x.get('id')}：检查命令 {x['check']} 不能证明是只读的（只允许 wf 的只读命令、只读程序、"
                          "项目里的 python 脚本，不许命令链与重定向），不能做目标判据")
    ph = g.get("phase")   # 1.8.2（P0-B）：当前阶段与历史阶段
    if ph is not None and (not isinstance(ph, dict) or not str(ph.get("id") or "").strip()
                           or not str(ph.get("title") or "").strip()):
        issues.append("phase（当前阶段）需要 id 与 title")
    hist = g.get("history")
    if hist is not None:
        if not isinstance(hist, list) or not all(isinstance(h, dict) and h.get("id") and h.get("title") for h in hist):
            issues.append("history 是历史阶段的列表，每项需要 id 与 title")
        elif isinstance(ph, dict) and any(h.get("id") == ph.get("id") for h in hist):
            issues.append("当前阶段的编号不能与历史阶段重复")
    for x in g["goals"]:   # B1-C：实景映射（可选）；只声明要核对哪些段，verified 仍只由 live.check 的实测结果授予
        lv = x.get("live") if isinstance(x, dict) else None
        if lv is None:
            continue
        segs = lv.get("segments") if isinstance(lv, dict) else None
        rounds = lv.get("rounds", 3) if isinstance(lv, dict) else None
        if x.get("kind") != "exec":
            issues.append(f"{x.get('id')}：live 只用于可执行目标")
        elif (not isinstance(segs, list) or not segs
              or any(type(n) is not int or n not in LIVE_SEGMENTS for n in segs)   # r2：先核类型再去重，嵌套列表不抛异常
              or len(set(segs)) != len(segs)
              or type(rounds) is not int or not 1 <= rounds <= 20):
            issues.append(f"{x.get('id')}：live 需要 segments（1–10 的不重复整数列表）与 rounds（1–20 的整数）")
    ids = {x.get("id") for x in g["goals"]}
    for x in g["goals"]:   # 1.8.3（W75）：人工目标的前置
        dep = x.get("depends_on")
        if dep is None:
            continue
        if x.get("kind") != "manual":
            issues.append(f"{x.get('id')}：depends_on 只用于人工目标")
        elif dep != "all_exec" and (not isinstance(dep, list) or not dep or any(d not in ids or d == x.get("id") for d in dep)):
            issues.append(f"{x.get('id')}：depends_on 要么是 \"all_exec\"，要么是本文件里其他目标编号的列表")
    return issues


def _deps(g: dict, x: dict) -> list:
    dep = x.get("depends_on")
    if dep == "all_exec":
        return [y["id"] for y in g["goals"] if y.get("kind") == "exec"]
    return list(dep) if isinstance(dep, list) else []


def judge(cond: dict, exit_code: int, out: str) -> tuple:
    """按条件判定命令结果。返回 (是否通过, 不通过的原因列表)。验收与目标共用。"""
    why = []
    if "exit" in cond and exit_code != cond["exit"]:
        why.append(f"退出码 {exit_code}，期望 {cond['exit']}")
    for s in cond.get("contains", []):
        if s not in out:
            why.append(f"输出不含“{s}”")
    for s in cond.get("not_contains", []):
        if s in out:
            why.append(f"输出含有“{s}”")
    for r in cond.get("regex", []):
        if not re.search(r, out, re.M):
            why.append(f"输出不匹配 /{r}/")
    return not why, why


def _confirmations(inst: Instance) -> dict:
    return (policy.approvals(inst).get("confirmations") or {})


def confirm(inst: Instance, gid: str, note: str) -> dict:
    g = load(inst)
    if not g or gid not in {x["id"] for x in g["goals"]}:
        raise WfError(f"没有目标 {gid}")
    x = next(x for x in g["goals"] if x["id"] == gid)
    if x["kind"] != "manual":
        raise WfError(f"{gid} 是可执行判据，由检查命令判定，不能人工确认")
    early = [i for i in progress(inst)["items"] if i["id"] == gid and i.get("ready") is False]
    if early:   # 1.8.3（W75）
        raise WfError(f"{gid} 现在还不能确认：{early[0]['detail']}")
    st = policy.status(inst, "goals")
    if not st["approved"]:
        raise WfError("目标尚未批准或批准后被改动，先 wf goals approve")
    a = policy.approvals(inst)
    a.setdefault("confirmations", {})[gid] = {"ts": now_iso(), "note": note, "goals_sha": st["current"]}
    write_json(policy.approvals_path(inst), a)
    inst.event("goal_confirm", goal=gid, note=note, by="user")
    return a["confirmations"][gid]


def _failed_record(r: dict) -> bool:
    """最近一次检查失败、超时或门禁未过：退出码非 0（含超时 124）或判定未通过。"""
    return bool(r) and (r.get("exit") != 0 or not r.get("ok"))


def mark_na(inst: Instance, gid: str, condition: str, scope: str, reason: str, by: str) -> dict:
    """B1-C：把目标记为不适用（not_applicable）。四项都要写；最近一次检查失败或超时不能记 NA；
    记录绑定当时的 goals_sha，目标一改动即失效。NA 不计入 ok 与 reached。"""
    fields = {"condition": condition, "scope": scope, "reason": reason, "by": by}
    miss = [k for k, v in fields.items() if not isinstance(v, str) or not v.strip()]
    if miss:
        raise WfError("不适用（NA）缺少：" + "、".join(miss) + "（条件、排除范围、理由、判定人都要写）")
    g = load(inst)
    if not g or gid not in {x.get("id") for x in g.get("goals") or []}:
        raise WfError(f"没有目标 {gid}")
    x = next(x for x in g["goals"] if x.get("id") == gid)
    st = policy.status(inst, "goals")
    if not st["approved"]:
        raise WfError("目标尚未批准或批准后被改动，先 wf goals approve")
    if x.get("kind") == "exec":
        r = (read_json(inst.wf / "goals_state.json", {}) or {}).get(gid) or {}
        if _failed_record(r):
            raise WfError(f"{gid} 最近一次检查未通过（退出码 {r.get('exit')}）：检查失败或超时不能记为不适用")
        if r.get("ok") and r.get("goals_sha") == st["current"]:
            raise WfError(f"{gid} 已按当前目标通过检查，不需要记为不适用")
    a = policy.approvals(inst)
    rec = {k: v.strip()[:500] for k, v in fields.items()}
    rec.update(ts=now_iso(), goals_sha=st["current"])
    a.setdefault("not_applicable", {})[gid] = rec
    write_json(policy.approvals_path(inst), a)
    inst.event("goal_not_applicable", goal=gid, reason=rec["reason"][:200], judged_by=rec["by"], by="user")
    return rec


def _sha256_file(p: Path):
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        return None


def _manifest_files(root) -> dict:
    """r2（F3）：与 release._tool_files() 相同的文件选择规则（那里固定读 WF_HOME，这里可指定根目录），只读。
    安装树里的普通文件逐个 sha256，排除 .git、local、__pycache__ 下的文件、.pyc 与 MANIFEST.json 本身；
    被 git 忽略但实际在安装树里的源码也计入。"""
    root = Path(root)
    out = {}
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(root)
        if set(rel.parts) & {".git", "local", "__pycache__"} or f.suffix == ".pyc" or str(rel) == "MANIFEST.json":
            continue
        out[str(rel).replace(os.sep, "/")] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def _tool_state(root=None) -> dict:
    """工具实际安装文件的摘要（按 MANIFEST 的文件选择规则）与 git dirty 状态（单独绑定）。
    没有版本库或 git 读不出时 dirty 为 None（无法确认），不授予 verified。只读。"""
    from .core import git
    root = Path(root or _TOOL_ROOT)
    out = {"root": str(root), "digest": None, "head": None, "dirty": None, "files": 0}
    try:
        files = _manifest_files(root)
    except OSError:
        return out
    out.update(digest=hashlib.sha256("".join(f"{h}  {p}\n" for p, h in sorted(files.items())).encode()).hexdigest(),
               files=len(files))
    if not (root / ".git").exists():
        return out
    try:
        stt = git(["status", "--porcelain", "--untracked-files=all"], root, check=False)
        head = git(["rev-parse", "HEAD"], root, check=False)
    except OSError:
        return out
    if stt.returncode == 0:
        out.update(head=(head.stdout.strip() or None) if head.returncode == 0 else None,
                   dirty=[ln for ln in stt.stdout.splitlines() if ln.strip()])
    return out


def _project_fp(inst: Instance):
    from . import ops
    try:
        return ops.fingerprint(inst.root)["digest"]
    except Exception:   # noqa: BLE001 —— 算不出指纹时绑定不成立
        return None


def _live_attest(inst: Instance, x: dict, st: dict, d: Path, stamp: str) -> dict:
    """B1-C：verified 的唯一来源。在本进程内直接调用 live.check（受控的规范路径），不读命令输出、不读外部摘要文件。"""
    from . import live
    spec = x.get("live") or {}
    segs = [int(n) for n in spec.get("segments") or []]
    rounds = int(spec.get("rounds", 3))
    why = []
    try:
        r = live.check(inst, rounds)
    except Exception as e:   # noqa: BLE001
        r = {}
        why.append(f"live.check 运行出错：{type(e).__name__}: {e}"[:300])
    sm = r.get("summary") if isinstance(r, dict) else None
    if not isinstance(sm, dict) or sm.get("schema") != "live-summary/1":
        why.append("live.check 没有返回机器摘要")
        sm = {}
    if sm and sm.get("project") != inst.name:
        why.append("实景摘要的项目与本项目不一致")
    if sm and not sm.get("passed"):
        why.append(f"实景验收未通过（连续 {sm.get('streak')} / {sm.get('need')} 轮）")
    if sm and sm.get("passed") and not sm.get("passed_strict"):   # r2（F2）：整体严格结果不通过时，窄映射不能绕过
        why.append("实景严格结果未通过：" + "；".join(map(str, (sm.get("strict_issues") or [])[:5])))
    bad = [n for n in segs if not (sm.get("segments") or {}).get(str(n))]
    if sm and bad:
        why.append("这些实景段在连续达标窗口里没有全部达标：" + "、".join(map(str, bad)))
    tool = _tool_state()
    if tool["dirty"] is None:
        why.append("无法确认工具是否有未提交改动（没有版本库或 git 读取失败）")
    elif tool["dirty"]:
        why.append(f"工具有 {len(tool['dirty'])} 处未提交改动")
    fp = _project_fp(inst)
    if not fp:
        why.append("算不出项目指纹")
    ev = d / f"{stamp}-{x['id']}-live.json"
    ev.write_text(json.dumps({"goal": x["id"], "summary": sm, "rounds": (r or {}).get("rounds")}, ensure_ascii=False,
                             sort_keys=True, indent=1), encoding="utf-8")
    binding = {"project": inst.name, "segments": segs, "rounds": rounds, "tool_digest": tool["digest"],
               "tool_head": tool["head"], "tool_dirty": tool["dirty"] != [], "project_fp": fp, "goals_sha": st["current"],
               "summary_sha256": sm.get("rounds_sha256"), "evidence": str(ev.relative_to(inst.root)),
               "evidence_sha256": _sha256_file(ev)}
    return {"granted": not why, "why": why, "ts": now_iso(), "binding": binding}


def run_checks(inst: Instance, timeout=900, by="driver") -> dict:
    """运行全部 exec 类目标的检查命令，结果写入 workflow/goals_state.json 并留存证据。"""
    g = load(inst)
    st = policy.status(inst, "goals")
    if not g or not st["approved"]:
        return progress(inst)
    d = inst.evidence / "goals"
    d.mkdir(parents=True, exist_ok=True)
    gs = read_json(inst.wf / "goals_state.json", {}) or {}
    env = dict(os.environ)
    env["PATH"] = str(WF_HOME / "bin") + os.pathsep + env.get("PATH", "")
    venv = inst.root / ".venv"
    if (venv / "bin").exists():
        env["VIRTUAL_ENV"] = str(venv)
        env["PATH"] = str(venv / "bin") + os.pathsep + env["PATH"]
    for x in g["goals"]:
        if x["kind"] != "exec":
            continue
        stamp = time.strftime("%Y%m%d-%H%M%S")
        t0 = time.time()
        try:
            r = subprocess.run(["bash", "-c", x["check"]], cwd=str(inst.root), capture_output=True, text=True,
                               timeout=timeout, env=env)
            code, out = r.returncode, r.stdout + r.stderr
        except subprocess.TimeoutExpired as e:
            code, out = 124, (e.stdout or "") if isinstance(e.stdout, str) else ""
        ok, why = judge(x["pass"], code, out)
        ev = d / f"{stamp}-{x['id']}.txt"
        ev.write_text(f"$ {x['check']}\nexit={code}\n{out}", encoding="utf-8")
        gs[x["id"]] = {"ok": ok, "why": why, "ts": now_iso(), "exit": code, "evidence": str(ev.relative_to(inst.root)),
                       "duration_s": round(time.time() - t0, 1), "goals_sha": st["current"],
                       "layer": "mechanical_pass" if ok else "failed",   # B1-C：记录层面只到机械层
                       "binding": {"goals_sha": st["current"], "evidence_sha256": _sha256_file(ev)}}
        if isinstance(x.get("live"), dict):   # B1-C：声明了实景映射的目标，另由 live.check 实测决定能否授予 verified
            gs[x["id"]]["verified"] = _live_attest(inst, x, st, d, stamp)
        inst.event("goal_check", goal=x["id"], ok=ok, why="；".join(why) or None, by=by)
    write_json(inst.wf / "goals_state.json", gs)
    return progress(inst)


def progress(inst: Instance) -> dict:
    g = load(inst)
    st = policy.status(inst, "goals")
    if not g:
        return {"defined": False, "approved": False, "items": [], "done": 0, "total": 0, "reached": False}
    gs = read_json(inst.wf / "goals_state.json", {}) or {}
    conf = _confirmations(inst)
    items = []
    for x in g["goals"]:
        if x["kind"] == "exec":
            r = gs.get(x["id"]) or {}
            valid = r.get("goals_sha") == st["current"]
            ok = bool(r.get("ok")) and valid
            detail = ("通过" if ok else ("；".join(r.get("why", [])) or "未通过")) if valid and r else "尚未检查"
        else:
            c = conf.get(x["id"]) or {}
            ok = c.get("goals_sha") == st["current"] and st["approved"]
            detail = f"用户已确认：{c.get('note')}" if ok else "等待用户确认"
        items.append({"id": x["id"], "title": x["title"], "kind": x["kind"], "ok": ok, "detail": detail,
                      "means": x.get("means"), "on_fail": x.get("on_fail")})
    okmap = {i["id"]: i["ok"] for i in items}
    for x, i in zip(g["goals"], items):   # 1.8.3（W75）：前置没满足的人工目标不算完成、不通知、不能确认
        deps = _deps(g, x) if x["kind"] == "manual" else []
        if deps:
            miss = [d for d in deps if not okmap.get(d)]
            i["ready"] = not miss
            if miss:
                i["ok"] = False
                i["detail"] = "前置未满足：" + "、".join(miss) + "（未到确认的时候，不通知）"
    na_all = policy.approvals(inst).get("not_applicable") or {}
    cache = {}
    for x, i in zip(g["goals"], items):   # B1-C：分层只新增键，不改 ok
        _layer(inst, g, x, i, gs.get(x["id"]) or {}, na_all.get(x["id"]), st, cache)
    done = sum(1 for i in items if i["ok"])
    counts = {k: sum(1 for i in items if i["layer"] == k) for k in LAYERS}
    return {"defined": True, "approved": st["approved"], "changed_since_approval": st["changed_since_approval"],
            "items": items, "done": done, "total": len(items), "reached": st["approved"] and done == len(items),
            "issues": validate(g), "phase": _phase(g), "history": _history(g), "counts": counts}


def _na_valid(na, r: dict, x: dict, st: dict) -> tuple:
    if not isinstance(na, dict):
        return False, []
    why = [f"缺少 {k}" for k in NA_FIELDS if not str(na.get(k) or "").strip()]
    if na.get("goals_sha") != st["current"]:
        why.append("目标在记录 NA 之后改动过")
    if x.get("kind") == "exec" and _failed_record(r):
        why.append(f"最近一次检查未通过（退出码 {r.get('exit')}），失败或超时不能算不适用")
    return not why, why


def _verified_valid(inst: Instance, r: dict, x: dict, st: dict, cache: dict) -> tuple:
    v = r.get("verified")
    if not isinstance(x.get("live"), dict):
        return False, []
    if not isinstance(v, dict):
        return False, ["还没有 live.check 的实测记录"]
    if not v.get("granted"):
        return False, list(v.get("why") or ["实测未授予 verified"])
    b = v.get("binding") or {}
    why = []
    if b.get("project") != inst.name:
        why.append("绑定的项目不一致")
    if b.get("goals_sha") != st["current"]:
        why.append("目标已改动")
    spec = x["live"]
    if b.get("segments") != [int(n) for n in spec.get("segments") or []] or b.get("rounds") != int(spec.get("rounds", 3)):
        why.append("目标声明的实景段与绑定不一致")
    ev = inst.root / str(b.get("evidence") or "")
    if not b.get("evidence") or not b.get("evidence_sha256") or _sha256_file(ev) != b.get("evidence_sha256"):
        why.append("实景证据文件缺失或被改动")
    else:
        why += _evidence_problems(inst, ev, b, spec)
    if "tool" not in cache:
        cache["tool"] = _tool_state()
    t = cache["tool"]
    if t["dirty"] is None:
        why.append("无法确认工具是否有未提交改动")
    elif t["dirty"] or b.get("tool_dirty") is not False:
        why.append("工具有未提交改动")
    if not b.get("tool_digest") or t["digest"] != b.get("tool_digest"):
        why.append("工具文件摘要与绑定不一致")
    if "fp" not in cache:
        cache["fp"] = _project_fp(inst)
    if not b.get("project_fp") or cache["fp"] != b.get("project_fp"):
        why.append("项目指纹与绑定不一致")
    return not why, why


def _evidence_problems(inst: Instance, ev: Path, b: dict, spec: dict) -> list:
    """r2：拒绝明显不是 live.check 规范输出、或与绑定对不上的已存证据。只是结构与一致性核对，不是来源证明——
    能改写本地状态的人仍可伪造自洽的文件（出处与独立执行证明属于 B2）。"""
    try:
        doc = json.loads(ev.read_text(encoding="utf-8"))
        sm, rounds = doc["summary"], doc["rounds"]
        canon = json.dumps(rounds, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (OSError, ValueError, KeyError, TypeError):
        return ["实景证据不是 live.check 的规范输出"]
    why = []
    if not isinstance(sm, dict) or sm.get("schema") != "live-summary/1" or sm.get("project") != inst.name:
        why.append("实景证据的摘要格式或项目不对")
    elif hashlib.sha256(canon.encode("utf-8")).hexdigest() != sm.get("rounds_sha256") \
            or sm.get("rounds_sha256") != b.get("summary_sha256"):
        why.append("实景证据的逐轮结果与摘要或绑定不一致")
    elif not sm.get("passed_strict") or not all((sm.get("segments") or {}).get(str(n)) for n in spec.get("segments") or []):
        why.append("实景证据记录的是未严格通过的测量")
    return why


def _layer(inst, g, x, i, r, na, st, cache) -> None:
    """按条目补 layer、binding、na、layer_why；不改 i["ok"]。旧记录没有绑定，只能推出原有的机械状态。"""
    i["layer_why"] = []
    i["binding"] = None
    i["na"] = None
    if x["kind"] == "exec":
        valid = bool(r) and r.get("goals_sha") == st["current"]
        if r:
            b = dict(r.get("binding") or {"goals_sha": r.get("goals_sha")})
            es = b.get("evidence_sha256")
            b["evidence_intact"] = (_sha256_file(inst.root / str(r.get("evidence") or "")) == es) if es else None
            i["binding"] = b
        if i["ok"]:
            vok, vwhy = _verified_valid(inst, r, x, st, cache)
            mech_intact = (i["binding"] or {}).get("evidence_intact")
            if vok and mech_intact is False:   # r2：机械检查的证据被改动时也不授予 verified
                vok, vwhy = False, vwhy + ["机械检查的证据文件缺失或被改动"]
            i["layer"] = "verified" if vok else "mechanical_pass"
            i["layer_why"] = vwhy
            if vok:
                i["binding"] = dict((r.get("verified") or {}).get("binding") or {}, evidence_intact=True,
                                    mechanical_evidence_intact=mech_intact)
            return
        nok, nwhy = _na_valid(na, r, x, st)
        if nok:
            i["layer"], i["na"] = "not_applicable", {k: na.get(k) for k in NA_FIELDS + ("ts",)}
            return
        i["layer"] = "failed" if valid else "pending"
        i["layer_why"] = nwhy
        return
    if i["ok"]:
        i["layer"] = "confirmed"
        return
    nok, nwhy = _na_valid(na, r, x, st)
    if nok:
        i["layer"], i["na"] = "not_applicable", {k: na.get(k) for k in NA_FIELDS + ("ts",)}
    else:
        i["layer"], i["layer_why"] = "pending", nwhy


def _phase(g: dict):
    ph = g.get("phase") if isinstance(g, dict) else None
    if not isinstance(ph, dict) or not ph.get("id"):
        return None
    return {k: ph.get(k) for k in ("id", "title", "since", "supersedes", "requirements", "scope") if ph.get(k) is not None}


def _history(g: dict) -> list:
    out = []
    for h in ((g.get("history") or []) if isinstance(g, dict) else []):
        if isinstance(h, dict) and h.get("id"):
            out.append({k: h.get(k) for k in ("id", "title", "status", "closed", "summary") if h.get(k) is not None})
    return out


def phase_title(prog: dict) -> str:
    """1.8.2（P0-B）：当前阶段的标题行。旧格式（没有 phase）照实说明，不把这份目标当成之后新范围的进度。"""
    ph = prog.get("phase")
    if ph:
        return (f"当前阶段 {ph['id']} {ph.get('title') or ''}".rstrip()
                + (f"（取代 {ph['supersedes']}）" if ph.get("supersedes") else ""))
    return "当前阶段：未声明（旧格式的目标文件；进度只代表这份文件里的目标，不代表之后的新范围）"


def history_lines(prog: dict) -> list:
    cn = {"done": "已完成", "superseded": "已被取代", "cancelled": "已取消", "closed": "已结束"}
    return [f"历史阶段 {h['id']} {h.get('title') or ''}：{cn.get(h.get('status'), h.get('status') or '已结束')}"
            + (f"（{h['closed']}）" if h.get("closed") else "") + (f"；{h['summary']}" if h.get("summary") else "")
            for h in prog.get("history") or []]
