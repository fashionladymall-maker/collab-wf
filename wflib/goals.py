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

SKELETON_VERSION = "default-goals/1"
# B2-C1：默认骨架的两条可执行判据是固定常量，只读、可经 policy.readonly_check；项目名永远不进入命令
_SKELETON_SELF_CHECK = "wf self-check"
_SKELETON_PROBES = "wf self-check --probes"


def default_goals(project: str, *, today: str | None = None) -> dict:
    """B2-C1：新项目的默认目标骨架（default-goals/1）。纯函数：不读时钟、不读写磁盘、不批准、不改正式目标；
    同样的 (project, today) 每次返回内容相同、互不共享的新 dict。today 为 None 时 since 为空字符串（不取当前日期）。
    骨架只到机械层：没有 live 映射（不会产生 verified），不预写不适用（NA 只能在目标获批后由 mark_na 记录）。
    由 B 的 init 在 workflow/goals.json 不存在时写入；是否生效仍由用户批准决定。"""
    if not isinstance(project, str):
        raise WfError(f"default_goals 的 project 必须是字符串，收到 {type(project).__name__}")
    if today is not None and not isinstance(today, str):
        raise WfError(f"default_goals 的 today 必须是字符串或 None，收到 {type(today).__name__}")
    return {
        "_说明": f"collab-wf 默认目标骨架（{SKELETON_VERSION}）。未经用户批准不生效；只到机械层，不含实景（verified）映射。",
        "_skeleton": SKELETON_VERSION,
        "phase": {"id": "P0", "title": "工程基线", "since": today if today is not None else "", "scope": project},
        "goals": [
            {"id": "BASE-SELF", "kind": "exec", "title": "工具文件与清单一致", "check": _SKELETON_SELF_CHECK,
             "pass": {"exit": 0}, "means": "安装的工具没有漂移", "on_fail": "交给 Claude 修复"},
            {"id": "BASE-PROBES", "kind": "exec", "title": "行为探针全部通过", "check": _SKELETON_PROBES,
             "pass": {"exit": 0, "contains": ["探针结果："]}, "means": "已安装代码的行为符合预期（机械层）",
             "on_fail": "交给 Claude 修复"},
            {"id": "BASE-SMOKE", "kind": "manual", "title": "项目冒烟命令已声明，或按四项记为不适用",
             "means": "项目自身有冒烟判据", "on_fail": "补冒烟命令或经 mark_na 记不适用"},
            {"id": "BASE-ACCEPT", "kind": "manual", "depends_on": "all_exec", "title": "用户验收本阶段",
             "means": "前置都满足后由用户本人确认", "on_fail": "按用户意见补齐"},
        ],
    }


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
        if r.get("ok") and _mech_valid(inst, g, x, r, st, {})[0]:   # B2-C2b：与 progress 同一个机械有效性判定
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


# ———————— B2-C2b：逐目标机械绑定 v2（mech-binding/2） ————————
# 新的可执行目标记录绑定：本目标定义（不含 title）与递归依赖目标的定义 hash、依赖闭包的内容摘要、证据文件 sha256。
# 闭包按检查命令推导；项目 python 脚本另由运行时审计（sys.addaudithook 记下的打开、列目录、换目录、子进程、网络）证明
# 实际读取的输入。无关目标变化可复用；相关变化、绑定不成立（unbound）、坏的或无法识别的绑定一律失败关闭、不复用。
# 只有明确的旧格式（没有 binding，或 binding 只有 goals_sha/evidence_sha256）按全局 goals_sha 兼容；
# 带 live 映射的目标写显式的 goals-sha/1（verified 仍按 B1 的全局绑定）。
MECH_SCHEMA = "mech-binding/2"
GLOBAL_SCHEMA = "goals-sha/1"
CLOSURE_RULE = "closure/5"
_LEGACY_KEYS = {"goals_sha", "evidence_sha256"}
_WF_TOOL_ONLY = ("wf self-check", "wf self-check --probes", "wf version")   # 只读工具自身
_WF_TOOL_PROJECT = ("wf ledger",)   # 只读工具与项目文件（PROJECT.md）
_PROJECT_EXCLUDE = (".git/", ".venv/", "workflow/state.json", "workflow/*.jsonl", "workflow/usage.json", "workflow/evidence/",
                    "workflow/outbox/", "workflow/approvals.json", "workflow/goals_state.json", "workflow/goals.json")
_BUDGET_FILES, _BUDGET_BYTES, _AUDIT_MAX = 20000, 256 * 1024 * 1024, 50000
_DEV_OK = ("/dev/null", "/dev/urandom", "/dev/random")
# 静态出现即无法绑定（读环境变量、标准输入、起子进程、网络）；或不允许收窄到 affects（动态导入与执行）
_STATIC_UNBOUND = ("subprocess", "os.system", "os.popen", "os.exec", "os.spawn", "os.posix_spawn", "os.fork", "os.environ",
                   "os.getenv", "os.putenv", "os.environb", "socket", "urllib", "http", "multiprocessing", "pty", "ctypes", "input")
_STATIC_NO_NARROW = ("__import__", "exec", "eval", "compile", "globals", "locals", "vars", "__builtins__", "builtins",
                     "importlib", "runpy", "sys.modules", "sys.path", "getattr", "setattr", "os.chdir")
_STAT_CALLS = ("exists", "isfile", "isdir", "islink", "lexists", "stat", "lstat", "access", "is_file", "is_dir", "is_symlink",
               "iterdir", "glob", "rglob", "iglob", "listdir", "scandir", "walk", "getsize", "getmtime", "samefile")


class _Unknown(Exception):
    """无法核对（读失败、超出预算、范围外的引用、坏输入）：一律不复用。"""


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _goal_def_sha(x: dict) -> str:
    """目标定义 hash：除 title 以外的全部字段（check、pass、means、on_fail、live、affects、depends_on 等）。"""
    return hashlib.sha256(_canon({k: v for k, v in x.items() if k != "title"}).encode("utf-8")).hexdigest()


def _dep_defs(g: dict, x: dict) -> dict:
    """递归依赖目标的定义 hash。按整张可达子图做深度优先三色标记：任何分支上的回边（含兄弟分支汇合后的环）都判为成环；
    依赖不存在或格式不对同样抛 _Unknown。"""
    by_id = {y.get("id"): y for y in g.get("goals") or [] if isinstance(y, dict)}
    color, out = {}, {}

    def visit(node):
        nid = node.get("id")
        color[nid] = "gray"
        dep = node.get("depends_on")
        if dep is not None:
            if not (dep == "all_exec" or isinstance(dep, list)):
                raise _Unknown(f"{nid} 的 depends_on 格式不对")
            for d in _deps(g, node):
                if d not in by_id:
                    raise _Unknown(f"依赖的目标 {d} 不存在")
                c = color.get(d)
                if c == "gray":
                    raise _Unknown(f"目标依赖成环：{nid} → {d}")
                if c is None:
                    visit(by_id[d])
                out[d] = _goal_def_sha(by_id[d])
        color[nid] = "black"
    visit(x)
    out.pop(x.get("id"), None)
    return dict(sorted(out.items()))


def _hash_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _covered(rel: str, aff) -> bool:
    return any(rel == a or rel.startswith(a + "/") for a in aff)


def _tree_rows(root: Path, rels, skip, inside, budget: list) -> list:
    """逐项（不跟随符号链接）记下类型、权限与内容 hash。符号链接的去处也必须在绑定范围内（由 inside 判定）——
    指向范围外、或指向被排除的区域时无法绑定（执行时会跟随链接读到范围外的内容）。"""
    import stat as _st
    from .core import matches_protect
    rows, rroot = [], os.path.realpath(root)

    def add(rel):
        p = Path(root) / rel
        try:
            s = os.lstat(p)
        except FileNotFoundError:
            rows.append(f"M\t{rel}")
            return
        except OSError as e:
            raise _Unknown(f"读取 {rel} 失败：{type(e).__name__}")
        mode = _st.S_IMODE(s.st_mode)
        if _st.S_ISLNK(s.st_mode):
            try:
                target = os.readlink(p)
            except OSError as e:
                raise _Unknown(f"读取链接 {rel} 失败：{type(e).__name__}")
            real = os.path.realpath(p)
            if not os.path.exists(real):
                rows.append(f"L\t{rel}\t{mode:o}\t{target}\tdangling")
                return
            rt = os.path.relpath(real, rroot).replace(os.sep, "/") if (real + os.sep).startswith(rroot + os.sep) else None
            if rt is None or not inside(rt):
                raise _Unknown(f"符号链接 {rel} 指向绑定范围外")
            rows.append(f"L\t{rel}\t{mode:o}\t{target}\tin")
        elif _st.S_ISREG(s.st_mode):
            budget[0] += 1
            budget[1] += s.st_size
            if budget[0] > _BUDGET_FILES or budget[1] > _BUDGET_BYTES:
                raise _Unknown("相关文件超出核对预算")
            try:
                rows.append(f"F\t{rel}\t{mode:o}\t{_hash_file(p)}")
            except OSError as e:
                raise _Unknown(f"读取 {rel} 失败：{type(e).__name__}")
        elif _st.S_ISDIR(s.st_mode):
            rows.append(f"D\t{rel}\t{mode:o}")
            try:
                names = sorted(os.listdir(p))
            except OSError as e:
                raise _Unknown(f"读取目录 {rel} 失败：{type(e).__name__}")
            for n in names:
                sub = f"{rel}/{n}" if rel != "." else n
                if n == "__pycache__" or n.endswith(".pyc") or matches_protect(sub, skip) or matches_protect(sub + "/", skip):
                    continue
                add(sub)
        else:
            rows.append(f"O\t{rel}\t{mode:o}")
    for rel in rels:
        add(rel)
    return rows


def _digest(root: Path, rels, skip, inside) -> dict:
    rows = _tree_rows(Path(root), rels, skip, inside, [0, 0])
    return {"digest": hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest(), "entries": len(rows)}


def _not_skipped(skip):
    from .core import matches_protect
    return lambda rel: rel != "." and not (matches_protect(rel, skip) or matches_protect(rel + "/", skip)
                                           or "/__pycache__/" in f"/{rel}/")


def _cached(cache: dict, key: str, fn):
    if key not in cache:
        try:
            cache[key] = fn()
        except _Unknown as e:
            cache[key] = e
    if isinstance(cache[key], _Unknown):
        raise cache[key]
    return cache[key]


def _tool_digest(cache: dict) -> dict:
    """整棵工具的实际内容（含 selftest、dashboard、docs 与 MANIFEST.json；只排除 .git、local/ 与字节码）。"""
    skip = [".git/", "local/"]
    return _cached(cache, "mech_tool", lambda: _digest(Path(_TOOL_ROOT), ["."], skip, _not_skipped(skip)))


def _project_digest(inst: Instance, cache: dict) -> dict:
    """整个项目的实际内容（含未跟踪文件）；排除 .git、.venv（解释器环境）与工具写的运行数据（目标定义另由定义 hash 绑定）。"""
    skip = list(_PROJECT_EXCLUDE)
    return _cached(cache, "mech_project", lambda: _digest(inst.root, ["."], skip, _not_skipped(skip)))


def _norm_affects(root: Path, affects) -> tuple:
    """affects 的规范化与越界检查：相对路径、已规范化、不含 ..、不指向排除区域、逐级不经过符号链接、留在项目内且存在。"""
    from .core import matches_protect
    if not isinstance(affects, list) or not affects or len(affects) > 200:
        return None, "affects 需要 1–200 项的路径列表"
    out = []
    rr = os.path.realpath(root)
    for a in affects:
        if not isinstance(a, str) or not a or "\x00" in a or "\\" in a or a.startswith(("/", "~")):
            return None, f"affects 项不是项目内的相对路径：{str(a)[:60]}"
        n = os.path.normpath(a)
        if n != a.rstrip("/") or n in (".", "") or any(part == ".." for part in n.split("/")):
            return None, f"affects 项没有规范化或越界：{a}"
        if matches_protect(n, _PROJECT_EXCLUDE) or matches_protect(n + "/", _PROJECT_EXCLUDE):
            return None, f"affects 项指向排除区域：{a}"
        cur = Path(root)
        for part in n.split("/"):
            cur = cur / part
            if os.path.islink(cur):
                return None, f"affects 项经过符号链接：{a}"
        if not os.path.lexists(cur) or not (os.path.realpath(cur) + os.sep).startswith(rr + os.sep):
            return None, f"affects 项不存在或越出项目：{a}"
        out.append(n)
    return sorted(set(out)), ""


def _script_static(root: Path, script: str, args: list, aff) -> tuple:
    """项目脚本的静态核对：返回 (结论, 原因)。结论 unbound：出现读环境变量、标准输入、子进程、网络等无法绑定的写法；
    no_narrow：动态导入或执行、对非字面量路径做存在性检查等，不能只凭静态证据收窄；narrow_fail：本地模块、命令参数或
    路径字面量不在 affects 内；ok：可收窄（仍需运行时审计确认）。"""
    import ast
    rr = os.path.realpath(root)

    def rel_of(p):
        rp = os.path.realpath(p)
        if not (rp + os.sep).startswith(rr + os.sep) and rp != rr:
            return None
        return os.path.relpath(rp, rr).replace(os.sep, "/")

    def check_path(s, base, where) -> str:
        if not s or "\x00" in s or len(s) > 4096 or s.startswith(("http://", "https://")) or s == "/":
            return ""
        for cand in ([s] if os.path.isabs(s) else [os.path.join(rr, s), os.path.join(base, s)]):
            if os.path.lexists(cand):
                rel = rel_of(cand)
                if rel is None or os.path.islink(cand):
                    return f"{where} 引用了项目外或经符号链接的路径：{s[:60]}"
                if rel == ".":
                    return f"{where} 引用了整个项目目录"
                if aff is None or not _covered(rel, aff):
                    return f"{where} 引用的 {rel} 不在 affects 内"
        return ""
    narrow, no_narrow = "", ""
    for a in args:
        v = a.split("=", 1)[1] if a.startswith("-") and "=" in a else a
        narrow = narrow or check_path(v, rr, "命令参数")
    sdir = os.path.dirname(os.path.join(rr, script))
    queue, seen = [script], set()
    while queue:
        rel = queue.pop()
        if rel in seen:
            continue
        seen.add(rel)
        if len(seen) > 200:
            return "unbound", "本地模块过多，无法核对"
        p = os.path.join(rr, rel)
        if os.path.islink(p) or not os.path.isfile(p):
            return "unbound", f"脚本或模块 {rel} 不是普通文件"
        if aff is None or not _covered(rel, aff):
            narrow = narrow or f"脚本或模块 {rel} 不在 affects 内"
        try:
            tree = ast.parse(Path(p).read_bytes(), filename=rel)
        except (OSError, SyntaxError, ValueError) as e:
            return "unbound", f"{rel} 无法解析：{type(e).__name__}"
        mdir = os.path.dirname(p)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Name):
                names = [node.id]
            elif isinstance(node, ast.Attribute):
                parts, cur = [node.attr], node.value
                while isinstance(cur, ast.Attribute):
                    parts.append(cur.attr)
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    parts.append(cur.id)
                names = [".".join(reversed(parts))]
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (node.module or "") if isinstance(node, ast.ImportFrom) else ""
                if isinstance(node, ast.ImportFrom) and node.level:
                    base = mdir
                    for _ in range(node.level - 1):
                        base = os.path.dirname(base)
                    mods = [(base, (mod.split(".") if mod else []) + [a.name]) for a in node.names]
                    names = []
                else:
                    full = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                        [mod] + [f"{mod}.{a.name}" for a in node.names]
                    names = full
                    mods = [(sdir, m.split(".")) for m in full]
                for base, parts in mods:
                    for i in range(1, len(parts) + 1):
                        for c in (os.path.join(base, *parts[:i]) + ".py", os.path.join(base, *parts[:i], "__init__.py")):
                            if os.path.lexists(c):
                                r2 = rel_of(c)
                                if r2 is None:
                                    return "unbound", f"{rel} 导入了项目外的模块"
                                queue.append(r2)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                narrow = narrow or check_path(node.value, mdir, rel)
            elif isinstance(node, ast.Call):
                fn = node.func
                nm = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
                if nm in _STAT_CALLS:
                    lit = node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                    if not lit:   # 存在性检查与列目录不一定留下打开记录：参数不是字面量时不能只凭证据收窄
                        no_narrow = no_narrow or f"{rel} 对非字面量路径调用 {nm}"
            for nm in names:
                if any(nm == b or nm.startswith(b + ".") or (b in ("os.exec", "os.spawn") and nm.startswith(b))
                       for b in _STATIC_UNBOUND):
                    return "unbound", f"{rel} 使用了无法绑定的 {nm}（环境变量、标准输入、子进程或网络）"
                if any(nm == b or nm.startswith(b + ".") for b in _STATIC_NO_NARROW):
                    no_narrow = no_narrow or f"{rel} 使用了动态写法 {nm}"
    if no_narrow:
        return "no_narrow", no_narrow
    return ("narrow_fail", narrow) if narrow else ("ok", "")


def _closure_spec(inst: Instance, x: dict) -> dict:
    """由检查命令推导静态闭包：tool / tool+project / python（再由运行时审计定为 affects、project 或 unbound）/
    project（只读程序且参数都在项目内）/ unbound（无法绑定：其余 wf 子命令、git、范围外或变量参数、其他命令）。"""
    import shlex
    cmd = str(x.get("check") or "").strip()
    norm = " ".join(cmd.split())
    if norm in _WF_TOOL_ONLY:
        return {"kind": "tool", "why": "只读工具自身的 wf 命令"}
    if norm in _WF_TOOL_PROJECT:
        return {"kind": "tool+project", "why": "只读工具与项目文件的 wf 命令"}
    if re.match(r"^wf(\s|$)", norm):
        return {"kind": "unbound", "why": "该 wf 子命令会读取运行状态（local/、运行数据），无法绑定"}
    if policy.META.search(cmd.replace("2>&1", "")):
        return {"kind": "unbound", "why": "检查命令含命令链、重定向或替换，无法推导"}
    try:
        toks = shlex.split(cmd.replace("|", " "))
    except ValueError:
        return {"kind": "unbound", "why": "检查命令无法解析"}
    if policy.PROJECT_SCRIPT.match(cmd) and "|" not in cmd:
        if "/" in toks[0]:
            return {"kind": "unbound", "why": "路径形式的解释器无法核对"}
        script, args = os.path.normpath(toks[1]), toks[2:]
        aff, why = (None, "没有声明 affects") if x.get("affects") is None else _norm_affects(inst.root, x.get("affects"))
        res, swhy = _script_static(inst.root, script, args, aff)
        if res == "unbound":
            return {"kind": "unbound", "why": swhy}
        try:
            entry = _python_entry(inst, toks[0])
        except _Unknown as e:
            return {"kind": "unbound", "why": str(e)}
        if aff is not None and res == "ok":
            return {"kind": "python", "narrow": True, "affects": aff, "entry": entry,
                    "why": "affects 静态核对通过，待运行时审计确认"}
        return {"kind": "python", "narrow": False, "entry": entry, "why": why or swhy}
    if toks and toks[0] == "git":
        return {"kind": "unbound", "why": "git 会读取项目外的配置与 .git 内部数据，无法绑定"}
    if policy.READONLY_PROGRAMS.match(cmd):
        for t in toks[1:]:
            v = t.split("=", 1)[1] if t.startswith("-") and "=" in t else t
            if v in _DEV_OK:
                continue
            if v.startswith(("/", "~")) or "$" in v or any(part == ".." for part in v.split("/")):
                return {"kind": "unbound", "why": f"参数 {v[:40]} 指向项目外或含变量，无法绑定"}
        return {"kind": "project", "why": "只读程序只读取项目内的参数文件"}
    return {"kind": "unbound", "why": "无法从检查命令推导依赖"}


def _closure_state(inst: Instance, kind: str, aff, cache: dict) -> dict:
    out = {}
    if kind in ("tool", "tool+project"):
        out["tool"] = _tool_digest(cache)
    if kind in ("project", "tool+project"):
        out["project"] = _project_digest(inst, cache)
    if kind == "affects":
        out["affects"] = _digest(inst.root, aff, list(_PROJECT_EXCLUDE), lambda rel: _covered(rel, aff))
    return out


def _runtime_stamp(path, follow=True):
    """读取时的短期戳只用于检查期间变化；不含会被读取本身改变的 atime。"""
    try:
        s = os.stat(path, follow_symlinks=follow)
        return [s.st_mode, s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as e:
        raise _Unknown(f"读取运行输入 {path} 失败：{type(e).__name__}")


def _runtime_file(path, expected=None, check_stamp=False) -> dict:
    """精确文件绑定，包含入口链接的实际去处；不按解释器前缀豁免实际字节。"""
    import stat
    path = os.path.abspath(path)
    stamp = _runtime_stamp(path)
    if check_stamp and stamp != expected:
        raise _Unknown(f"检查期间运行输入发生变化：{path}")
    out = {"path": path, "realpath": os.path.realpath(path)}
    if stamp is None:
        return dict(out, absent=True)
    if not stat.S_ISREG(stamp[0]) or stamp[3] > _BUDGET_BYTES:
        raise _Unknown(f"运行输入不是普通文件或超出预算：{path}")
    try:
        digest = _hash_file(Path(path))
    except OSError as e:
        raise _Unknown(f"读取运行输入 {path} 失败：{type(e).__name__}")
    if _runtime_stamp(path) != stamp:
        raise _Unknown(f"摘要期间运行输入发生变化：{path}")
    return dict(out, mode=stat.S_IMODE(stamp[0]), sha256=digest)


def _check_env(inst: Instance) -> dict:
    env = dict(os.environ)
    env["PATH"] = str(WF_HOME / "bin") + os.pathsep + env.get("PATH", "")
    venv = inst.root / ".venv"
    if (venv / "bin").exists():
        env["VIRTUAL_ENV"] = str(venv)
        env["PATH"] = str(venv / "bin") + os.pathsep + env["PATH"]
    return env


def _python_entry(inst: Instance, command: str) -> dict:
    import shutil
    # bash 在项目 cwd 中解析相对 PATH 项；which 的调用进程 cwd 则不一定是项目。
    path = os.pathsep.join(os.path.abspath(inst.root / p) if not os.path.isabs(p) else p
                           for p in _check_env(inst)["PATH"].split(os.pathsep))
    entry = shutil.which(command, path=path)
    if not entry:
        raise _Unknown("找不到检查命令实际使用的解释器")
    return dict(_runtime_file(entry), environment_sha256=hashlib.sha256(_canon(_check_env(inst)).encode("utf-8")).hexdigest())


def _import_names(path, candidates):
    try:
        return sorted(set(os.listdir(path)) & set(candidates))
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as e:
        raise _Unknown(f"读取模块搜索目录失败：{path}（{type(e).__name__}）")


def _site_directory(path):
    try:
        names = sorted(n for n in os.listdir(path) if n.endswith(".pth") and not n.startswith("."))
    except (FileNotFoundError, NotADirectoryError):
        names = None
    except OSError as e:
        raise _Unknown(f"读取 site 启动目录失败：{path}（{type(e).__name__}）")
    return {"realpath": os.path.realpath(path), "names": names}


def _startup_current(startup):
    return {"directories": {p: _site_directory(p) for p in startup["directories"]},
            "paths": {p: [os.path.realpath(p), os.path.exists(p)] for p in startup["paths"]}}


def _runtime_binding(audit: dict) -> dict:
    stamps = audit["files"]
    paths = sorted(set(audit.get("bound_files") or []))
    if len(paths) > _BUDGET_FILES:
        raise _Unknown("运行输入超出核对预算")
    files = [_runtime_file(p, stamps[p], True) for p in paths]
    stats = audit.get("stats") or []
    for row in stats:
        if not isinstance(row, list) or len(row) != 3 or not isinstance(row[0], str):
            raise _Unknown("运行时 stat 记录损坏")
        if _runtime_stamp(row[0], row[1]) != row[2]:
            raise _Unknown(f"检查期间 stat 输入发生变化：{row[0]}")
    candidates = audit.get("candidates")
    dirs = audit.get("import_dirs")
    if not isinstance(candidates, list) or not isinstance(dirs, dict):
        raise _Unknown("模块搜索输入缺失")
    searches = []
    for p, names in sorted(dirs.items()):
        if names is not None and not isinstance(names, list):
            raise _Unknown("模块搜索目录不可读")
        prior = None if names is None else sorted(set(names) & set(candidates))
        if _import_names(p, candidates) != prior:
            raise _Unknown(f"检查期间模块搜索输入发生变化：{p}")
        searches.append({"path": p, "names": prior})
    startup = audit.get("startup")
    if not isinstance(startup, dict) or _startup_current(startup) != startup:
        raise _Unknown("site 启动输入缺失或在检查期间改变")
    return {"files": files, "stats": stats, "candidates": candidates, "searches": searches, "startup": startup}


def _runtime_current(binding: dict) -> dict:
    if not isinstance(binding, dict) or not all(isinstance(binding.get(k), list)
                                              for k in ("files", "stats", "candidates", "searches")):
        raise _Unknown("运行输入绑定缺失或损坏")
    return {"files": [_runtime_file(r["path"]) for r in binding["files"]],
            "stats": [[r[0], r[1], _runtime_stamp(r[0], r[1])] for r in binding["stats"]],
            "candidates": binding["candidates"],
            "searches": [{"path": r["path"], "names": _import_names(r["path"], binding["candidates"])}
                         for r in binding["searches"]],
            "startup": _startup_current(binding["startup"])}


# observer 只用已加载的 sys/os、冻结 importlib 与内建 atexit；不导入 sysconfig/json/pathlib。
# 初始化与 dump 不进入脚本事件。文件打开时先记 stat 戳，父进程在戳仍一致时读取字节摘要。
# stat/lstat/access 的 Python 入口补足 CPython audit 没有覆盖的存在性读取；不改变返回值或异常。
_AUDIT_SITE = r'''
import sys, os, atexit
_wf_log = os.environ.get("WF_GOAL_AUDIT")
_wf_ev, _wf_over, _wf_files, _wf_stats = [], [False], {}, []
_wf_dirs, _wf_sources, _wf_imports = {}, {}, set()
_wf_busy = [True]
_wf_stat, _wf_lstat, _wf_access = os.stat, os.lstat, os.access
_wf_external = sys.modules["_frozen_importlib_external"]
_wf_bootstrap = sys.modules["_frozen_importlib"]
_wf_here = os.path.dirname(os.path.abspath(__file__))
_wf_stdlib = os.path.dirname(os.__file__)
_wf_observer = sys.modules[__name__]
_wf_owned = {os.path.realpath(__file__)}
_wf_cache = getattr(_wf_observer, "__cached__", None)
if isinstance(_wf_cache, str):
    _wf_owned.add(os.path.realpath(_wf_cache))
    try:
        _wf_owned.add(os.path.realpath(_wf_external.source_from_cache(_wf_cache)))
    except (ValueError, NotImplementedError):
        pass
_wf_startup = {"directories": {}, "paths": {}}
_wf_startup_files, _wf_startup_errors = [], []
def _wf_stamp(p, follow=True):
    try:
        s = _wf_stat(p, follow_symlinks=follow)
        return [s.st_mode, s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]
    except (FileNotFoundError, NotADirectoryError):
        return None
def _wf_remember(p):
    p = os.path.abspath(p)
    if os.path.realpath(p) in _wf_owned:
        return
    if p not in _wf_files:
        _wf_files[p] = _wf_stamp(p)
    if p.endswith(".pyc"):
        try:
            source = _wf_external.source_from_cache(p)
            _wf_sources[p] = source
            if os.path.realpath(source) not in _wf_owned and source not in _wf_files:
                _wf_files[source] = _wf_stamp(source)
        except (ValueError, NotImplementedError):
            pass
def _wf_modules():
    out = []
    for m in list(sys.modules.values()):
        if m is _wf_observer:
            continue
        p = getattr(m, "__file__", None)
        if isinstance(p, str):
            out.append(os.path.abspath(p))
            cached = getattr(m, "__cached__", None)
            if isinstance(cached, str):
                out.append(os.path.abspath(cached))
    return sorted(set(out))
def _wf_dir(p):
    p = os.path.abspath(p)
    if p not in _wf_dirs:
        try:
            _wf_dirs[p] = os.listdir(p)
        except (FileNotFoundError, NotADirectoryError):
            _wf_dirs[p] = None
        except OSError:
            _wf_dirs[p] = "unknown"
def _wf_standard_module(module, name):
    spec = getattr(module, "__spec__", None)
    filename = os.path.join(_wf_stdlib, name + ".py")
    if (module is not sys.modules.get(name) or getattr(spec, "name", None) != name
            or os.path.realpath(getattr(module, "__file__", "")) != os.path.realpath(filename)):
        return False
    loader = spec.loader
    return ((loader is _wf_bootstrap.FrozenImporter and spec.origin == "frozen")
            or (type(loader) is _wf_external.SourceFileLoader
                and os.path.realpath(spec.origin) == os.path.realpath(filename)
                and os.path.realpath(loader.path) == os.path.realpath(filename)))
def _wf_standard_startup(site):
    # 只确认正常 CPython/site/os 的可观察前提；不支持替换启动实现或预启动任意执行代码。
    if (sys.implementation.name != "cpython" or sys.flags.no_site
            or not _wf_standard_module(site, "site") or not _wf_standard_module(os, "os")
            or getattr(site, "os", None) is not os or getattr(site, "sys", None) is not sys
            or type(os.environ) is not os._Environ):
        return False
    mapping = getattr(sys.modules.get("_collections_abc"), "Mapping", None)
    if getattr(os.environ.get, "__func__", None) is not getattr(mapping, "get", None):
        return False
    for name in ("main", "addpackage", "addsitedir", "getsitepackages", "execsitecustomize"):
        fn = getattr(site, name, None)
        code = getattr(fn, "__code__", None)
        if (getattr(fn, "__globals__", None) is not site.__dict__ or code is None
                or not (code.co_filename == "<frozen site>"
                        or os.path.realpath(code.co_filename) == os.path.realpath(site.__file__))):
            return False
    return True
def _wf_pth_problem(line):
    # 完整行精确匹配；仅忽略明确的 ASCII 行尾空白，不允许后缀代码／注释或宽 AST 求值。
    for default in ("stdlib", "local"):
        template = ("import os; var = 'SETUPTOOLS_USE_DISTUTILS'; enabled = os.environ.get(var, '"
                    + default + "') == 'local'; enabled and __import__('_distutils_hack').add_shim();")
        if line.rstrip(" \t\r\n") == template:
            if os.environ.get("SETUPTOOLS_USE_DISTUTILS", default) != "local":
                return None  # 已加载 os，赋值与字符串比较后 and 右支不执行；未主动导入 shim。
            return "启动 .pth 的 distutils 执行分支未禁用，观察前输入无法证明"
    return "启动 .pth 含未知执行代码，观察前输入无法证明"
def _wf_site_inputs():
    site = sys.modules["site"]
    if not _wf_standard_startup(site):
        _wf_startup_errors.append("无法确认标准 CPython/site/os 启动前提，启动输入无法证明")
        return
    dirs = list(site.getsitepackages())
    if site.ENABLE_USER_SITE:
        user = site.getusersitepackages()
        dirs.extend([user] if isinstance(user, str) else user)
    executable = os.environ.get("__PYVENV_LAUNCHER__", sys.executable)
    exe_dir = os.path.dirname(os.path.abspath(executable))
    for parent in (exe_dir, os.path.dirname(exe_dir)):
        p = os.path.join(parent, "pyvenv.cfg")
        _wf_startup_files.append(p)
        _wf_remember(p)
    for directory in sorted(set(dirs)):
        directory = os.path.abspath(directory)
        try:
            names = sorted(n for n in os.listdir(directory) if n.endswith(".pth") and not n.startswith("."))
        except (FileNotFoundError, NotADirectoryError):
            names = None
        _wf_startup["directories"][directory] = {"realpath": os.path.realpath(directory), "names": names}
        for name in names or []:
            p = os.path.join(directory, name)
            _wf_startup_files.append(p)
            _wf_remember(p)
            with open(p) as f:
                for line in f:
                    if line.startswith("#") or not line.strip():
                        continue
                    if line.startswith(("import ", "import\t")):
                        problem = _wf_pth_problem(line)
                        if problem:
                            _wf_startup_errors.append(problem + "：" + p)
                        continue
                    target = os.path.abspath(os.path.join(directory, line.rstrip()))
                    _wf_startup["paths"][target] = [os.path.realpath(target), os.path.exists(target)]
try:
    _wf_site_inputs()
except Exception as _wf_error:
    _wf_startup_errors.append("无法核对 site 启动输入：" + type(_wf_error).__name__)
_wf_initial = _wf_modules()
for _wf_p in _wf_initial + [sys.executable]:
    _wf_remember(_wf_p)
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _wf_here]
# 查找本来的 sitecustomize 是 observer 自身的引导，不能污染脚本输入。
for _wf_p in sys.path:
    if isinstance(_wf_p, str):
        _wf_dir(_wf_p or ".")
_wf_site = _wf_external.PathFinder.find_spec("sitecustomize", sys.path)
def _wf_hook(ev, args):
    if _wf_busy[0]:
        return
    _wf_busy[0] = True
    try:
        if ev == "import" and args and isinstance(args[0], str):
            _wf_imports.add(args[0])
            for search in sys.path:
                if isinstance(search, str):
                    _wf_dir(search or ".")
        if ev in ("open", "os.listdir", "os.scandir", "os.chdir", "builtins.input") or ev.startswith(
                ("subprocess.", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.popen", "socket.", "ctypes.")):
            if len(_wf_ev) >= %d:
                _wf_over[0] = True
                return
            a0 = args[0] if args else None
            if isinstance(a0, int) or a0 is None:
                p = None if isinstance(a0, int) else "."
            else:
                p = os.fsdecode(a0) if isinstance(a0, (str, bytes)) else os.fsdecode(os.fspath(a0))
            if ev == "open" and p is not None:
                _wf_remember(p)
            if ev in ("os.listdir", "os.scandir"):
                caller = sys._getframe(1).f_code.co_filename
                if caller.startswith("<frozen importlib.") or caller.endswith("/importlib/_bootstrap_external.py"):
                    _wf_dir(p or ".")
                    ev = "import-dir"
            _wf_ev.append([ev, p])
    except Exception:
        _wf_ev.append(["hook-error", None])
    finally:
        _wf_busy[0] = False
def _wf_observe_stat(fn, p, args, kw, follow):
    if _wf_busy[0]:
        return fn(p, *args, **kw)
    _wf_busy[0] = True
    try:
        try:
            if isinstance(p, int) or kw.get("dir_fd") is not None:
                _wf_ev.append(["hook-error", None])
            else:
                path = os.path.abspath(os.fsdecode(os.fspath(p)))
                if len(_wf_ev) + len(_wf_stats) >= %d:
                    _wf_over[0] = True
                else:
                    _wf_stats.append([path, follow, _wf_stamp(path, follow)])
        except Exception:
            _wf_ev.append(["hook-error", None])
        return fn(p, *args, **kw)
    finally:
        _wf_busy[0] = False
def _wf_stat_call(p, *args, **kw):
    return _wf_observe_stat(_wf_stat, p, args, kw, kw.get("follow_symlinks", True))
def _wf_lstat_call(p, *args, **kw):
    return _wf_observe_stat(_wf_lstat, p, args, kw, False)
def _wf_access_call(p, *args, **kw):
    return _wf_observe_stat(_wf_access, p, args, kw, kw.get("follow_symlinks", True))
os.stat, os.lstat, os.access = _wf_stat_call, _wf_lstat_call, _wf_access_call
sys.addaudithook(_wf_hook)
def _wf_dump():
    _wf_busy[0] = True
    candidates = {"sitecustomize.py", "sitecustomize.pyc", "sitecustomize"}
    suffixes = [".py", ".pyc"] + sys.modules["_imp"].extension_suffixes()
    for name in set(sys.modules) | _wf_imports:
        last = name.rsplit(".", 1)[-1]
        candidates.add(last)
        candidates.update(last + suffix for suffix in suffixes)
    tmp = _wf_log + ".tmp"
    with open(tmp, "w") as f:
        f.write(repr({"events": _wf_ev, "overflow": _wf_over[0], "files": _wf_files, "stats": _wf_stats,
                      "modules": _wf_modules(), "initial": _wf_initial, "stdlib": _wf_stdlib,
                      "startup": _wf_startup, "startup_files": _wf_startup_files, "startup_errors": _wf_startup_errors,
                      "import_dirs": _wf_dirs, "candidates": sorted(candidates), "cache_sources": _wf_sources,
                      "executable": sys.executable, "cwd": os.getcwd(), "version": sys.version}))
    os.replace(tmp, _wf_log)
atexit.register(_wf_dump)
_wf_busy[0] = False
if _wf_site is not None:
    _wf_original = _wf_bootstrap.module_from_spec(_wf_site)
    sys.modules["sitecustomize"] = _wf_original
    _wf_site.loader.exec_module(_wf_original)
''' % (_AUDIT_MAX, _AUDIT_MAX)


def _audit_kind(inst: Instance, spec: dict, audit, audit_dir: str) -> tuple:
    """按运行时审计确定最终闭包：返回 (kind, 原因)。读到范围外或排除区域、网络、标准输入、换目录、起子进程、
    审计记录缺失或损坏都是 unbound；narrow 且所有项目内输入都在 affects 内为 affects，否则 project。"""
    from .core import matches_protect
    if not isinstance(audit, dict) or not isinstance(audit.get("events"), list) or not isinstance(audit.get("files"), dict):
        return "unbound", "运行时审计记录缺失或损坏（脚本可能用 os._exit 退出）"
    if audit.get("overflow"):
        return "unbound", "运行时审计事件超出上限"
    if audit.get("startup_errors"):
        return "unbound", "；".join(audit["startup_errors"])
    rr = os.path.realpath(inst.root)
    stdlib = os.path.realpath(str(audit.get("stdlib") or ""))
    modules = set(audit.get("modules") or []) | set(audit.get("initial") or [])
    bound = set(audit.get("startup_files") or [])
    ad = os.path.realpath(audit_dir)
    narrow_bad = ""
    # 实际加载的模块文件／其缓存（含系统 sitecustomize 的依赖）逐文件绑定，绝不豁免所在目录。
    def runtime(p):
        return p in modules
    for p in audit["files"]:
        if p == audit.get("executable") or runtime(p):
            bound.add(p)
    for item in audit["events"]:
        if not isinstance(item, list) or len(item) != 2:
            return "unbound", "运行时审计记录损坏"
        ev, p = item
        if ev == "hook-error":
            return "unbound", "运行时审计钩子出错"
        if ev == "os.chdir":
            return "unbound", "脚本切换了工作目录，相对路径无法核对"
        if ev == "builtins.input":
            return "unbound", "脚本读取了标准输入"
        if ev.startswith("socket."):
            return "unbound", "脚本使用了网络"
        if ev.startswith(("subprocess.", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.popen", "ctypes.")):
            return "unbound", f"脚本启动了子进程或外部代码（{ev}），子进程的输入无法审计"
        if p is None:
            continue
        if ev == "import-dir":
            continue   # 模块实际文件另行核对，不把 importlib 的查找当成脚本列目录
        rp = os.path.realpath(os.path.join(rr, p))
        if rp in _DEV_OK or (rp + os.sep).startswith(ad + os.sep) or rp == ad:
            continue
        absolute = os.path.abspath(os.path.join(rr, p))
        if ev == "open" and (runtime(absolute) or absolute == audit.get("executable")):
            bound.add(absolute)
            continue
        if ev in ("os.listdir", "os.scandir") and (rp + os.sep).startswith(stdlib + os.sep):
            # import 搜索目录不是文件内容；实际导入文件仍逐项绑定。脚本显式列目录不允许靠此前缀豁免。
            return "unbound", f"脚本或导入读取了外部目录：{p[:80]}"
        if not ((rp + os.sep).startswith(rr + os.sep) or rp == rr):
            return "unbound", f"脚本读取了项目外的路径：{p[:80]}"
        rel = os.path.relpath(rp, rr).replace(os.sep, "/")
        if ev == "open" and rel.startswith(".venv/"):
            bound.add(absolute)
            narrow_bad = narrow_bad or "实际读取了 affects 之外的解释器环境文件"
            continue
        if "/__pycache__/" in f"/{rel}" and ".pyc" in rel:
            continue   # 字节码缓存，源文件另有记录
        if rel != "." and (matches_protect(rel, _PROJECT_EXCLUDE) or matches_protect(rel + "/", _PROJECT_EXCLUDE)):
            return "unbound", f"脚本读取了不在绑定范围内的 {rel}"
        if spec.get("narrow") and (rel == "." or not _covered(rel, spec["affects"])):
            narrow_bad = narrow_bad or f"运行时读取了 affects 之外的 {rel}"
    # .pyc 的对应源码在打开缓存前已取戳，防止执行后才把新源码误记成实际输入。
    for p in list(bound):
        source = (audit.get("cache_sources") or {}).get(p)
        if source in audit["files"]:
            bound.add(source)
    audit["bound_files"] = sorted(bound)
    if spec.get("narrow") and not narrow_bad:
        return "affects", "affects 经静态核对与运行时审计确认"
    return "project", narrow_bad or spec.get("why") or "按整个项目绑定"


def _binding_core(inst: Instance, g: dict, x: dict) -> dict:
    return {"schema": MECH_SCHEMA, "closure_rule": CLOSURE_RULE, "goal_id": x["id"], "goal_sha": _goal_def_sha(x),
            "deps": _dep_defs(g, x), "static": _closure_spec(inst, x)}


def _legacy_binding(r: dict) -> bool:
    """明确的旧格式：没有 binding 键（B1 之前），或 binding 是只含 goals_sha/evidence_sha256 的 dict（B1）。"""
    if "binding" not in r:
        return True
    b = r["binding"]
    return isinstance(b, dict) and "schema" not in b and set(b) <= _LEGACY_KEYS


def _mech_valid(inst: Instance, g: dict, x: dict, r: dict, st: dict, cache: dict) -> tuple:
    """B2-C2b：机械记录当前是否仍然有效——progress、_layer、mark_na 都用它，避免 ok 与 layer 矛盾。"""
    if not r:
        return False, ["尚未检查"]
    if _legacy_binding(r):
        return (True, []) if r.get("goals_sha") == st["current"] else (False, ["目标文件已改动（旧记录按全局绑定）"])
    b = r.get("binding")
    if not isinstance(b, dict):
        return False, ["绑定格式无法识别"]
    if b.get("schema") == GLOBAL_SCHEMA:
        if not isinstance(x.get("live"), dict):
            return False, ["只有带 live 映射的目标使用全局绑定"]
        return (True, []) if r.get("goals_sha") == st["current"] and b.get("goals_sha") == st["current"] else \
            (False, ["目标文件已改动（全局绑定）"])
    if b.get("schema") != MECH_SCHEMA:
        return False, ["绑定格式无法识别"]
    key = ("mech_valid", x.get("id"))
    if key in cache:
        return cache[key]
    why = []
    try:
        if b.get("unbound"):
            why.append(f"依赖绑定不成立：{b.get('unbound')}")
        else:
            if b.get("closure_rule") != CLOSURE_RULE or b.get("goal_id") != x.get("id"):
                why.append("绑定的规则或目标编号不一致")
            if isinstance(x.get("live"), dict):
                why.append("带 live 映射的目标不使用逐目标绑定")
            if b.get("goal_sha") != _goal_def_sha(x):
                why.append("本目标的定义已改动")
            if b.get("deps") != _dep_defs(g, x):
                why.append("依赖目标的定义已改动")
            if b.get("static") != _closure_spec(inst, x):
                why.append("依赖闭包的静态推导已改变")
            if (b.get("static") or {}).get("kind") == "python" and b.get("runtime") != _runtime_current(b.get("runtime")):
                why.append("解释器、实际运行库／环境文件或 stat 输入已改变")
            kind, aff = b.get("kind"), b.get("affects")
            if kind not in ("tool", "tool+project", "project", "affects") or (kind == "affects") != isinstance(aff, list):
                why.append("绑定的闭包类型无效")
            elif b.get("state") != _closure_state(inst, kind, aff, cache):
                why.append("依赖闭包内的文件已改变（新增、删除、权限、内容或链接去处）")
        es = b.get("evidence_sha256")
        if not es or _sha256_file(inst.root / str(r.get("evidence") or "")) != es:
            why.append("机械检查的证据文件缺失或被改动")
    except _Unknown as e:
        why.append(f"无法核对：{e}")
    except Exception as e:   # noqa: BLE001 —— 坏输入一律不复用
        why.append(f"无法核对：{type(e).__name__}")
    cache[key] = (not why, why)
    return cache[key]


def run_checks(inst: Instance, timeout=900, by="driver") -> dict:
    """运行全部 exec 类目标的检查命令，结果写入 workflow/goals_state.json 并留存证据。"""
    g = load(inst)
    st = policy.status(inst, "goals")
    if not g or not st["approved"]:
        return progress(inst)
    d = inst.evidence / "goals"
    d.mkdir(parents=True, exist_ok=True)
    gs = read_json(inst.wf / "goals_state.json", {}) or {}
    env = _check_env(inst)
    import shutil
    import tempfile
    cores, pre, pre_cache = {}, {}, {}
    for x in g["goals"]:   # B2-C2b：检查前先算各目标的静态绑定与候选闭包的内容摘要，检查后再算一次
        if x["kind"] != "exec" or isinstance(x.get("live"), dict):
            continue
        try:
            cores[x["id"]] = core = _binding_core(inst, g, x)
        except _Unknown as e:
            cores[x["id"]] = e
            continue
        k = core["static"]["kind"]
        for kind in (("project", "affects") if k == "python" else (k,)):
            if kind == "affects" and not core["static"].get("narrow") or kind == "unbound":
                continue
            try:
                pre[(x["id"], kind)] = _closure_state(inst, kind, core["static"].get("affects"), pre_cache)
            except _Unknown as e:
                pre[(x["id"], kind)] = e
    ran, audits = [], {}
    for x in g["goals"]:
        if x["kind"] != "exec":
            continue
        stamp = time.strftime("%Y%m%d-%H%M%S")
        t0 = time.time()
        core = cores.get(x["id"])
        audit_dir, run_env = None, env
        if isinstance(core, dict) and core["static"]["kind"] == "python":   # B2-C2b：项目脚本在运行时审计下执行
            audit_dir = tempfile.mkdtemp(prefix="wf-goal-audit-")
            Path(audit_dir, "sitecustomize.py").write_text(_AUDIT_SITE, encoding="utf-8")
            run_env = dict(env, WF_GOAL_AUDIT=str(Path(audit_dir, "audit.json")), PYTHONDONTWRITEBYTECODE="1",
                           PYTHONPATH=audit_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""))
        try:
            r = subprocess.run(["bash", "-c", x["check"]], cwd=str(inst.root), capture_output=True, text=True,
                               timeout=timeout, env=run_env)
            code, out = r.returncode, r.stdout + r.stderr
        except subprocess.TimeoutExpired as e:
            code, out = 124, (e.stdout or "") if isinstance(e.stdout, str) else ""
        if audit_dir:
            import ast
            try:
                audit = ast.literal_eval(Path(audit_dir, "audit.json").read_text(encoding="utf-8"))
            except (OSError, ValueError, SyntaxError):
                audit = None
            audits[x["id"]] = (audit, audit_dir)
            shutil.rmtree(audit_dir, ignore_errors=True)
        ok, why = judge(x["pass"], code, out)
        ev = d / f"{stamp}-{x['id']}.txt"
        ev.write_text(f"$ {x['check']}\nexit={code}\n{out}", encoding="utf-8")
        live = isinstance(x.get("live"), dict)
        gs[x["id"]] = {"ok": ok, "why": why, "ts": now_iso(), "exit": code, "evidence": str(ev.relative_to(inst.root)),
                       "duration_s": round(time.time() - t0, 1), "goals_sha": st["current"],
                       "layer": "mechanical_pass" if ok else "failed",   # B1-C：记录层面只到机械层
                       "binding": {"schema": GLOBAL_SCHEMA if live else MECH_SCHEMA, "goals_sha": st["current"],
                                   "evidence_sha256": _sha256_file(ev)}}
        if live:   # B1-C：声明了实景映射的目标，另由 live.check 实测决定能否授予 verified
            gs[x["id"]]["verified"] = _live_attest(inst, x, st, d, stamp)
        ran.append(x)
        inst.event("goal_check", goal=x["id"], ok=ok, why="；".join(why) or None, by=by)
    post_cache = {}
    for x in ran:   # B2-C2b：写 v2 绑定；推导不了、运行时审计不通过、检查期间有变化时写 unbound（失败关闭，不复用）
        if isinstance(x.get("live"), dict):
            continue
        rec, core, unb = gs[x["id"]], cores.get(x["id"]), ""
        base = {"schema": MECH_SCHEMA, "closure_rule": CLOSURE_RULE, "goal_id": x["id"], "goal_sha": _goal_def_sha(x),
                "goals_sha": st["current"], "evidence_sha256": rec["binding"]["evidence_sha256"]}
        kind = aff = state = runtime = None
        if isinstance(core, _Unknown):
            unb = str(core)
        elif core["static"]["kind"] == "unbound":
            unb = core["static"]["why"]
        else:
            kind = core["static"]["kind"]
            if kind == "python":
                audit, adir = audits.get(x["id"], (None, ""))
                kind, kwhy = _audit_kind(inst, core["static"], audit, adir)
                if kind == "unbound":
                    unb = kwhy
                else:
                    try:
                        runtime = _runtime_binding(audit)
                        if core["static"] != _closure_spec(inst, x):
                            unb = "检查期间解释器入口或静态输入发生变化"
                    except (KeyError, TypeError, _Unknown) as e:
                        unb = f"运行输入无法绑定：{e}"
            if not unb:
                aff = core["static"].get("affects") if kind == "affects" else None
                s0 = pre.get((x["id"], kind))
                try:
                    state = _closure_state(inst, kind, aff, post_cache)
                except _Unknown as e:
                    unb = str(e)
                if not unb and isinstance(s0, _Unknown):
                    unb = str(s0)
                elif not unb and s0 != state:
                    unb = "检查期间依赖闭包内的文件发生变化"
        if unb:
            rec["binding"] = dict(base, unbound=unb)
        else:
            rec["binding"] = dict(base, deps=core["deps"], static=core["static"], kind=kind, affects=aff, state=state)
            if runtime is not None:
                rec["binding"]["runtime"] = runtime
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
    cache = {}
    for x in g["goals"]:
        if x["kind"] == "exec":
            r = gs.get(x["id"]) or {}
            valid, vwhy = _mech_valid(inst, g, x, r, st, cache)   # B2-C2b：v2 记录按逐目标绑定，旧记录按全局 goals_sha
            ok = bool(r.get("ok")) and valid
            detail = ("通过" if ok else ("；".join(r.get("why", [])) or "未通过")) if valid and r else (
                "尚未检查" if not r or _legacy_binding(r) else "尚未检查：" + "；".join(vwhy))
        else:
            c = conf.get(x["id"]) or {}
            ok = c.get("goals_sha") == st["current"] and st["approved"]
            detail = f"用户已确认：{c.get('note')}" if ok else "等待用户确认"
        items.append({"id": x["id"], "title": x["title"], "kind": x["kind"], "ok": ok, "detail": detail,
                      "means": x.get("means"), "on_fail": x.get("on_fail")})
    okmap = {i["id"]: i["ok"] for i in items}
    for x, i in zip(g["goals"], items):   # 1.8.3（W75）：前置没满足的人工目标不算完成、不通知、不能确认
        if x["kind"] != "manual" or x.get("depends_on") is None:
            continue
        deps = _deps(g, x)
        miss = [d for d in deps if not okmap.get(d)]
        why = ["、".join(miss)] if miss else []
        if not deps:   # B2-C2a：all_exec 在没有可执行目标时是空集，不能因 all([]) 而就绪、通知或被确认
            why.append("没有可执行目标（前置为空集）")
        if not st["approved"]:   # B2-C2a：目标未批准（或批准后被改动）时，人工验收不就绪
            why.append("目标尚未批准或批准后被改动")
        i["ready"] = not why
        if why:
            i["ok"] = False
            i["detail"] = "前置未满足：" + "；".join(why) + "（未到确认的时候，不通知）"
    na_all = policy.approvals(inst).get("not_applicable") or {}
    for x, i in zip(g["goals"], items):   # B1-C：分层只新增键，不改 ok
        _layer(inst, g, x, i, gs.get(x["id"]) or {}, na_all.get(x["id"]), st, cache)
    done = sum(1 for i in items if i["ok"])
    counts = {k: sum(1 for i in items if i["layer"] == k) for k in LAYERS}
    return {"defined": True, "approved": st["approved"], "changed_since_approval": st["changed_since_approval"],
            "items": items, "done": done, "total": len(items),
            # B2-C2a：至少要有一个可执行目标才可能达成——空目标、只有人工目标时即使批准、全部确认也不算达成
            "reached": st["approved"] and done == len(items) and any(x.get("kind") == "exec" for x in g["goals"]),
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
        valid = _mech_valid(inst, g, x, r, st, cache)[0]   # B2-C2b：与 progress 的 ok 用同一个判定
        if r:
            rb = r.get("binding")   # B2-C2b：绑定可能是坏格式（列表、null 等）；展示时不崩溃，判定已由 _mech_valid 拒绝
            b = dict(rb) if isinstance(rb, dict) else {"goals_sha": r.get("goals_sha")} if "binding" not in r else \
                {"unrecognized": True}
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
