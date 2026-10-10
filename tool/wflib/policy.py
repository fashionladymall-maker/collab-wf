"""自主边界：用户批准的命令白名单 + 工具内置的停止底线 + 护栏命令。
设计原则：
- 底线写在代码里，项目的 policy.json 只能追加，不能删除或放宽。
- 白名单默认拒绝：不在白名单里的命令一律停下来问用户。
- 驱动器不能改自己的护栏：升级工具、改白名单与目标、批准、解除暂停等只能由用户执行。
- policy.json / goals.json 随版本交付，但只有用户批准过的内容（按 sha256）才生效；批准记录受保护，版本包无法覆盖。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path

from .core import Instance, WfError, now_iso, read_json, write_json

# 1.5.9：停止底线、版本包内容检查、护栏命令与命令拼接的定义集中在 floor.py（改动它的工具版本不能自动安装），
# 这里导入，原有名字保持可用
from .floor import CODE_SUFFIXES, DIFF_FLOOR, GUARDRAIL, META, STOP_FLOOR  # noqa: F401
# 只读的 wf 命令：驱动器可以在白名单允许时自动执行。其余 wf 命令会改变工作流状态，
# 由驱动器内部再调用会与驱动器自身的状态写入互相覆盖（F21），所以驱动器永远不执行它们，只列为手动步骤。
WF_READONLY = re.compile(r"^\s*wf\s+(version|fingerprint|doctor|status|explain|step|help|audit|decisions|effort|projects|"
                         r"bridge-check|goals\s*$|goals\s+show|policy\s*$|policy\s+show|conv\s*$|conv\s+show|"
                         r"transport(\s+(check|test))?\s*$|schedule\s+(status|plist))(\s|$)")
# 命令链与重定向（META）：一律需要用户批准，定义在 floor.py
DEFAULT_PIPE_FILTERS = ["head", "tail", "grep", "wc", "sort", "uniq", "cut"]
# 1.5.8：用户批准的“驱动器自动安装工具版本”只放行这一种形式（policy.json 的 auto_self_update 为 true，且白名单放行）；
# 改动护栏文件的版本由 release.self_update(auto=True) 拒绝，另一个模型系列审查阻止时也拒绝
AUTO_SELF_UPDATE = re.compile(r"^\s*wf\s+self-update\s+--auto\s+workflow/outbox/collab-wf-\d+\.\d+\.\d+\.zip\s*$")


def sha256(p: Path):
    p = Path(p)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def approvals_path(inst: Instance) -> Path:
    return inst.wf / "approvals.json"


def approvals(inst: Instance) -> dict:
    return read_json(approvals_path(inst), {}) or {}


def approve(inst: Instance, kind: str, note: str, by: str = "user") -> dict:
    """用户批准 policy 或 goals 的当前内容。只能由用户命令调用；驱动器没有调用路径。"""
    p = inst.wf / f"{kind}.json"
    if not p.exists():
        raise WfError(f"没有 workflow/{kind}.json，无可批准的内容。")
    if read_json(p) is None:
        raise WfError(f"workflow/{kind}.json 不是有效的 JSON。")
    a = approvals(inst)
    rec = {"sha256": sha256(p), "ts": now_iso(), "note": note}
    a.setdefault("history", []).append({"kind": kind, **rec})
    a[kind] = rec
    write_json(approvals_path(inst), a)
    inst.event("approve", kind=kind, sha=rec["sha256"][:12], note=note, by=by)   # 1.6.5：执行端代决时 by=executor
    return rec


def status(inst: Instance, kind: str) -> dict:
    """返回 {exists, approved, current, approved_sha, ts}；approved 只在内容与批准时完全一致时为真。"""
    p = inst.wf / f"{kind}.json"
    cur = sha256(p)
    rec = approvals(inst).get(kind) or {}
    return {"exists": cur is not None, "approved": bool(cur and rec.get("sha256") == cur), "current": cur,
            "approved_sha": rec.get("sha256"), "ts": rec.get("ts"), "changed_since_approval": bool(rec and cur != rec.get("sha256"))}


def load(inst: Instance):
    """只返回已批准的策略；未批准或被改动时返回 None。"""
    st = status(inst, "policy")
    return read_json(inst.wf / "policy.json") if st["approved"] else None


def classify(cmd: str, pol: dict) -> dict:
    """命令分级：ok（可自动执行）/ user（需要用户）。返回原因与类别。"""
    reasons, cats = [], []
    for cat, pat in STOP_FLOOR + [(s.get("category", "项目规定"), s["pattern"]) for s in pol.get("stop", [])]:
        if re.search(pat, cmd, re.I):
            cats.append(cat)
            reasons.append(f"属于必须停下的“{cat}”类")
    manual = False
    first = cmd.split("|")[0].strip()
    auto_su = (pol or {}).get("auto_self_update") is True and bool(AUTO_SELF_UPDATE.match(first))
    if auto_su:   # 1.5.8：只此一种 wf 写操作可由驱动器执行；仍须在白名单内（下面照常检查）
        cats.append("自动安装工具版本")
    elif re.match(r"^\s*wf(\s|$)", first) and not WF_READONLY.match(first) and not re.match(r"^\s*wf\s+self-check\s*$", first):
        manual = True
        cats.append("手动步骤")
        reasons.append("会改变工作流状态的 wf 命令，驱动器不执行，须在驱动器之外手动执行")
    elif GUARDRAIL.search(cmd):
        cats.append("护栏")
        reasons.append("会改变自动化自身的规则或状态，只能由用户执行")
    segments = [s.strip() for s in cmd.split("|")]
    if META.search(cmd.replace("2>&1", "")):
        reasons.append("包含命令拼接或重定向（; && || > < ` $( &），需要用户批准")
    else:
        allow = [re.compile(p) for p in pol.get("allow", [])]
        filters = pol.get("pipe_filters", DEFAULT_PIPE_FILTERS)
        if not any(a.search(segments[0]) for a in allow):
            reasons.append("不在已批准的命令白名单中")
        for seg in segments[1:]:
            if not seg or seg.split()[0] not in filters:
                reasons.append(f"管道后的“{seg.split()[0] if seg else ''}”不是允许的过滤命令")
    return {"cmd": cmd, "auto": not reasons, "reasons": reasons, "categories": cats, "manual": manual}


# 1.6.1：不扩大权限的白名单与目标变更由驱动器自动批准（用户第 35 轮：把“需要执行端做的事”降到零）。
# 判定：新内容给驱动器的能力不多于已批准的旧内容。四道底线在 floor.py，与这里无关；扩大权限的变更照旧停下等用户。
NEUTRAL_KEYS = {"_说明", "first_poll_after_s", "poll_interval_s", "send_unknown_wait", "page_screenshots", "stale_reply_polls",
                "min_send_gap_s",   # 1.6.5：发送间隔只是时间参数
                "executor_task_timeout_s", "landed_confirm_polls", "landed_poll_s"}   # 1.6.6：同为时间参数
UPPER_LIMITS = ("max_rounds_per_day", "max_consecutive_failed_rounds", "max_rounds_without_goal_progress",
                "command_timeout_s", "max_leaf_refusals")
ZERO_IS_UNLIMITED = ("max_conversation_turns", "max_conversation_chars")
GRANTS = {"auto_apply": True, "auto_self_update": False, "auto_new_conversation": False}   # 键 → 缺省值（1.6.4：自动开新对话也是授权）
MUST_KEEP = ("stop", "diff_stop", "gates")
MAY_SHRINK = ("allow", "pipe_filters")


def widening(old: dict, new: dict) -> list:
    """新白名单比旧的多给了哪些能力；空列表表示没有扩大（收紧或只改了时间类参数）。"""
    old, new = old or {}, new or {}
    why = []
    for k in MAY_SHRINK:
        dft = DEFAULT_PIPE_FILTERS if k == "pipe_filters" else []
        extra = [x for x in new.get(k, dft) if x not in old.get(k, dft)]
        if extra:
            why.append(f"{k} 新增：" + "、".join(str(x) for x in extra[:3]))
    for k in MUST_KEEP:
        keep = {json.dumps(x, sort_keys=True, ensure_ascii=False) for x in new.get(k, [])}
        gone = [x for x in old.get(k, []) if json.dumps(x, sort_keys=True, ensure_ascii=False) not in keep]
        if gone:
            why.append(f"{k} 去掉了 {len(gone)} 项")
    for k in UPPER_LIMITS:
        o, n = old.get(k), new.get(k)
        if n != o and not (isinstance(o, (int, float)) and isinstance(n, (int, float)) and n <= o):
            why.append(f"{k}：{o} → {n}")
    for k in ZERO_IS_UNLIMITED:
        inf = lambda v: float("inf") if not v else v   # noqa: E731  0 或缺省表示不限
        if inf(new.get(k)) > inf(old.get(k)):
            why.append(f"{k}：{old.get(k)} → {new.get(k)}")
    for k, dft in GRANTS.items():
        if new.get(k, dft) is True and old.get(k, dft) is not True:
            why.append(f"{k} 新开启")
    known = NEUTRAL_KEYS | set(UPPER_LIMITS) | set(ZERO_IS_UNLIMITED) | set(GRANTS) | set(MUST_KEEP) | set(MAY_SHRINK)
    for k in sorted((set(old) | set(new)) - known):
        if old.get(k) != new.get(k):
            why.append(f"{k} 有变化（不认识的键按扩大处理）")
    return why


def goals_widening(new: dict, pol: dict) -> list:
    """目标的检查命令由驱动器直接运行：只有每条都在已批准的白名单内可以自动执行，才不算扩大。"""
    from .goals import validate
    why = list(validate(new or {}))
    for x in (new or {}).get("goals", []):
        if x.get("kind") == "exec" and x.get("check") and not classify(x["check"], pol or {})["auto"]:
            why.append(f"{x.get('id')} 的检查命令不在已批准的白名单内：{x['check'][:80]}")
    return why


def change_widens(inst: Instance, kind: str, new: dict, pol: dict) -> list:
    """kind 为 policy 或 goals。当前内容未获批准时，任何改动都按扩大处理（没有可比的基准）。"""
    if not status(inst, kind)["approved"]:
        return [f"当前的 {kind}.json 未获批准，没有可比的基准"]
    if new is None:
        return [f"新的 {kind}.json 不是有效的 JSON"]
    return widening(read_json(inst.wf / "policy.json"), new) if kind == "policy" else goals_widening(new, pol)


def auto_approve(inst: Instance, kind: str, why: str) -> dict:
    """1.6.1：驱动器自动批准不扩大权限的变更（change_widens 为空时才调用）。记录与用户批准分开标明。"""
    p = inst.wf / f"{kind}.json"
    a = approvals(inst)
    rec = {"sha256": sha256(p), "ts": now_iso(), "note": f"驱动器自动批准（不扩大权限）：{why}", "by": "driver"}
    a.setdefault("history", []).append({"kind": kind, **rec})
    a[kind] = rec
    write_json(approvals_path(inst), a)
    inst.event("approve", kind=kind, sha=rec["sha256"][:12], note=rec["note"], by="driver")
    return rec


def release_findings(inst: Instance, src: Path, pol: dict) -> list:
    """检查版本包相对当前仓库新增的内容：命中停止规则，或改动了白名单与目标定义，都需要用户。"""
    out = []
    pats = list(DIFF_FLOOR) + [("项目规定", p) for p in pol.get("diff_stop", [])]
    for f in sorted(Path(src).rglob("*")):
        if not f.is_file() or f.name == "RELEASE.json" or "__pycache__" in f.parts:
            continue
        rel = str(f.relative_to(src))
        if rel in ("workflow/policy.json", "workflow/goals.json"):
            if sha256(f) != sha256(inst.root / rel):
                wid = change_widens(inst, rel.split("/")[-1][:-5], read_json(f), pol)   # 1.6.1：不扩大权限的改动自动批准
                if wid:
                    out.append({"file": rel, "why": "改动了白名单或目标定义，且扩大了自动执行的范围（生效前需要用户批准）："
                                + "；".join(wid[:4])})
            continue
        if f.suffix not in CODE_SUFFIXES and not f.name.startswith(".env"):
            continue
        try:
            new = f.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        old_p = inst.root / rel
        old = old_p.read_text(encoding="utf-8", errors="replace").splitlines() if old_p.exists() else []
        added = [l[1:] for l in difflib.unified_diff(old, new, lineterm="", n=0) if l.startswith("+") and not l.startswith("+++")]
        for line in added:
            for cat, pat in pats:
                if re.search(pat, line, re.I):
                    out.append({"file": rel, "why": f"新增内容命中“{cat}”：{line.strip()[:120]}"})
                    break
    return out
