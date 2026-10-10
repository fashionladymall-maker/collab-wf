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


def approve(inst: Instance, kind: str, note: str) -> dict:
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
    inst.event("approve", kind=kind, sha=rec["sha256"][:12], note=note, by="user")
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
                out.append({"file": rel, "why": "改动了白名单或目标定义（生效前需要用户批准）"})
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
