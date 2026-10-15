"""档位与角色（借鉴 pstack 的“按角色分配模型与推理预算”），以及决策日志（借鉴 show-me-your-work 的 decisions.tsv）。

我们的条件与 pstack 不同：只有一个 Claude 对话、没有子代理、额度有限。所以“多模型”落在两处：
- 同一模型的不同档位按角色分配（设计、实现、修复、升级、节点、续接、探测），由预算统一调节；
- 可选的“独立审查”适配器（另一个模型系列，见 TRANSPORT.md），只能增加阻力，不能单独放行。
"""
from __future__ import annotations

from .core import WF_HOME, Instance, append_jsonl, now_iso, read_json, read_jsonl

ORDER = ["low", "medium", "high", "xhigh", "max"]
DEFAULT_ROLES = {
    "budget": "large",
    "roles": {
        "design": {"effort": "max", "doc": "方案设计、架构、跨模块改动"},
        "build": {"effort": "high", "doc": "常规实现与改动"},
        "fix": {"effort": "high", "doc": "上一轮有失败，第一次修复"},
        "escalate": {"effort": "max", "doc": "同一问题连续失败，升级处理"},
        "checkpoint": {"effort": "medium", "doc": "计划节点到期、例行回报（全部通过）"},
        "continue": {"effort": "same", "doc": "回复中断后请 Claude 从断点继续，沿用上一轮档位"},
        "probe": {"effort": "low", "doc": "通道探测、确认收到"},
    },
    "budgets": {"unlimited": "max", "large": "max", "medium": "xhigh", "small": "high"},
    "cap_when_quota_above_pct": 85,
}


def _rank(e):
    return ORDER.index(e) if e in ORDER else ORDER.index("high")


def config(inst: Instance) -> dict:
    base = read_json(WF_HOME / "roles.json") or DEFAULT_ROLES
    proj = read_json(inst.wf / "roles.json") if inst else None
    if proj:
        merged = dict(base)
        merged.update({k: v for k, v in proj.items() if k != "roles"})
        merged["roles"] = {**base.get("roles", {}), **proj.get("roles", {})}
        return merged
    return base


def choose(inst: Instance, situation: str, claude_suggestion=None, prev_effort=None, consecutive_failed=0) -> dict:
    """选择下一条消息的角色与档位，并给出理由（写入决策日志）。
    situation：kick_design / kick / report_ok / report_fail / checkpoint / continue / probe"""
    cfg = config(inst)
    roles = cfg["roles"]
    deep = cfg.get("budgets", {}).get(cfg.get("budget", "large"), "max")
    reasons = []
    if situation == "report_fail":
        role = "escalate" if consecutive_failed >= 2 else "fix"
        reasons.append(f"上一轮有失败（连续 {consecutive_failed} 轮）")
    elif situation == "kick_design":
        role = "design"
    elif situation in ("checkpoint", "report_ok"):
        role = "checkpoint"
    elif situation == "continue":
        role = "continue"
    elif situation == "probe":
        role = "probe"
    else:
        role = "build"
    base = roles.get(role, {}).get("effort", "high")
    if base == "same":
        base = prev_effort or "high"
    if role in ("design", "escalate"):
        base = deep
        reasons.append(f"预算 {cfg.get('budget')} 下深度角色使用 {deep}")
    effort = base
    if claude_suggestion in ORDER and role not in ("continue", "probe"):
        if _rank(claude_suggestion) > _rank(effort):
            reasons.append(f"Claude 建议 {claude_suggestion}，高于角色默认 {effort}，采用较高者")
            effort = claude_suggestion
        elif _rank(claude_suggestion) < _rank(effort) and role in ("checkpoint", "build") and situation != "report_fail":
            reasons.append(f"Claude 建议 {claude_suggestion}，例行情况下采用建议")
            effort = claude_suggestion
    u = inst.usage_snapshot() if inst else None
    cap = cfg.get("cap_when_quota_above_pct", 85)
    if u and u.get("session") is not None and u["session"] >= cap and situation != "report_fail" and _rank(effort) > _rank("high"):
        reasons.append(f"5 小时窗口已用 {u['session']}%（≥ {cap}%），非失败情况封顶为 high")
        effort = "high"
    return {"role": role, "effort": effort, "model": roles.get(role, {}).get("model"), "reasons": reasons or ["角色默认"]}


def stats(inst: Instance) -> dict:
    """事后复盘：按档位与角色统计轮次、通过率、额度消耗、耗时。只用实测值。"""
    by = {}
    order = []
    for r in read_jsonl(inst.turns_path):
        k = r.get("turn")
        if k not in by:
            order.append(k)
        by[k] = {**by.get(k, {}), **{a: b for a, b in r.items() if b is not None}}
    passed = {}
    for e in read_jsonl(inst.events_path):
        if e.get("type") == "round_result":
            passed[e.get("turn")] = bool(e.get("ok"))
    out = {}
    for k in order:
        t = by[k]
        for key in (("effort", t.get("effort")), ("role", t.get("role"))):
            if not key[1]:
                continue
            a = out.setdefault(key[0], {}).setdefault(key[1], {"turns": 0, "pct": [], "dur": [], "ok": 0, "judged": 0})
            a["turns"] += 1
            b, af = t.get("usage_before") or {}, t.get("usage_after") or {}
            if b.get("session") is not None and af.get("session") is not None and af["session"] >= b["session"]:
                a["pct"].append(af["session"] - b["session"])
            if t.get("duration_s"):
                a["dur"].append(t["duration_s"])
            if k in passed:
                a["judged"] += 1
                a["ok"] += 1 if passed[k] else 0
    avg = lambda v: round(sum(v) / len(v), 2) if v else None
    return {grp: {name: {"turns": a["turns"], "avg_session_pct": avg(a["pct"]), "avg_duration_s": avg(a["dur"]),
                         "pass_rate": round(a["ok"] / a["judged"], 2) if a["judged"] else None, "samples": len(a["pct"])}
                  for name, a in d.items()} for grp, d in out.items()}


# ---------------- 决策日志 ----------------

def decide(inst: Instance, phase: str, decision: str, why: str, evidence: str = "", result: str = "", by="driver"):
    """追加一条决策：做了什么、为什么、证据在哪、结果如何。只追加，不修改历史。"""
    row = {"ts": now_iso(), "turn": inst.state.get("turn", 0), "phase": phase, "decision": decision, "why": why,
           "evidence": evidence, "result": result, "by": by}
    append_jsonl(inst.wf / "decisions.jsonl", row)
    return row


def decisions(inst: Instance, turn=None, n=None) -> list:
    rows = read_jsonl(inst.wf / "decisions.jsonl")
    if turn is not None:
        rows = [r for r in rows if r.get("turn") == turn]
    return rows[-n:] if n else rows
