"""状态机：合法转换、失败计数与自动阻塞、恢复、状态查询、轮次时间线。"""
from __future__ import annotations

from .core import DEF_PATH, Instance, WfError, now_iso, parse_iso, read_json, read_jsonl, secs_since

_DEF = None


SEND_UNKNOWN_DWELL_S = 3900   # 1.6.9（N8）


def definition() -> dict:
    global _DEF
    if _DEF is None:
        d = read_json(DEF_PATH)
        if not d or "states" not in d:
            raise WfError(f"工作流定义缺失或损坏：{DEF_PATH}")
        for sid in d["loop"] + d["holds"]:
            if sid not in d["states"]:
                raise WfError(f"工作流定义缺少状态 {sid}")
        _DEF = d
    return _DEF


def sdef(sid: str) -> dict:
    d = definition()
    if sid not in d["states"]:
        raise WfError(f"未知状态 {sid}。可用：{'、'.join(d['states'])}")
    return d["states"][sid]


def allowed(cur: str, to: str) -> bool:
    d = definition()
    return to in sdef(cur).get("next", []) or to in d.get("global_targets", [])


def go(inst: Instance, to: str, by="executor", reason=None, force=False, **extra) -> dict:
    to = to.upper()
    sdef(to)
    st = inst.state
    cur = st.get("state", "IDLE")
    if not force and not allowed(cur, to):
        if to == "WAIT_SCHEDULE" and cur in ("COMPOSE", "SEND", "WAIT_REPLY", "INTAKE"):
            raise WfError(f"现在是 {cur}（{sdef(cur)['name']}），还不能设置计划节点：消息已经或即将发出，"
                          "下一步取决于 Claude 的回复。等回复到达、解析并评审后，在 REVIEW 或 VERIFY 之后再 wf wait。"
                          "如果本轮不需要回报，应在验收后直接 wf wait，而不是先发回报。")
        nxt = "、".join(sdef(cur).get("next", []))
        raise WfError(f"不能从 {cur}（{sdef(cur)['name']}）直接进入 {to}。允许进入：{nxt}。"
                      f"如果确实需要更正，用 wf force {to} --reason \"原因\"（会被标记为人工更正）。")
    if to == "COMPOSE" and st.get("compose_reason") != "resend":
        st["turn"] = int(st.get("turn", 0)) + 1
        st["turn_flags"] = {}
        st.pop("compose_reason", None)   # 1.7.6：续接、计划节点的标记只属于设置它的那一次，不带进新的一轮
    if to == "REVIEW":
        st.pop("continue_streak", None)   # 1.7.6：回复固定块齐全，续接计数归零
    if to not in ("BLOCKED", "AWAITING_USER", "QUOTA_WAIT"):
        st.pop("return_to", None)
    if to in ("BLOCKED", "AWAITING_USER", "QUOTA_WAIT"):
        st["return_to"] = cur
    st.update(prev=cur, state=to, since=now_iso(), attempts=0)
    for k in ("until", "wait_reason", "block_reason", "blocked_from"):
        st.pop(k, None)
    for k, v in extra.items():
        if v is not None:
            st[k] = v
    inst.event("transition", frm=cur, to=to, by=by, reason=reason, forced=True if force else None)
    return st


def fail(inst: Instance, reason: str, symptom=None, by="executor") -> dict:
    st = inst.state
    cur = st["state"]
    st["attempts"] = int(st.get("attempts", 0)) + 1
    inst.event("fail", symptom=symptom, reason=reason, attempt=st["attempts"], by=by)
    limit = sdef(cur).get("max_attempts")
    if limit and st["attempts"] >= limit:
        go(inst, "BLOCKED", by="wf", reason=f"{cur} 失败 {st['attempts']} 次（上限 {limit}）：{reason}",
           block_reason=f"{sdef(cur)['name']} 失败 {st['attempts']} 次：{reason}", blocked_from=cur)
    return st


def resume(inst: Instance, note: str, to=None, by="user") -> dict:
    st = inst.state
    if st["state"] not in ("BLOCKED", "AWAITING_USER", "QUOTA_WAIT"):
        raise WfError(f"当前是 {st['state']}，不需要恢复。")
    target = (to or st.get("return_to") or "IDLE").upper()
    if to:
        # 明确指定去向：按正常转换（进入 COMPOSE 意味着一条新消息，轮次加一）
        return go(inst, target, by=by, reason=note, force=st["state"] == "BLOCKED")
    # 回到进入前的步骤：不增加轮次、不重置该步以外的标记
    cur = st["state"]
    st.update(prev=cur, state=target, since=now_iso(), attempts=0)
    for k in ("return_to", "block_reason", "blocked_from", "until", "wait_reason"):
        st.pop(k, None)
    inst.event("transition", frm=cur, to=target, by=by, reason=note)
    return st


def status(inst: Instance) -> dict:
    d = definition()
    st = dict(inst.state)
    sid = st.get("state", "IDLE")
    sd = sdef(sid)
    dwell = secs_since(st.get("since")) or 0
    limit = sd.get("max_dwell_s")
    if sid == "SEND" and limit and (st.get("send_intent") or {}).get("status") == "pending":
        # 1.6.9（N8）：结果不明时 SEND_UNKNOWN_WAIT 会反复确认约一小时（绝不自动重发），按 900 秒报停留过久是误报；
        # 放宽到 65 分钟，与看门狗的 send_unknown（60 分钟）衔接
        limit = max(limit, SEND_UNKNOWN_DWELL_S)
    until = parse_iso(st.get("until"))
    overdue = None
    if until is not None:
        overdue = secs_since(st.get("until"))
    flags = st.get("turn_flags", {})
    return {
        "project": inst.name, "turn": st.get("turn", 0), "state": sid, "name": sd["name"],
        "owner": sd.get("owner"), "owner_name": d["owners"].get(sd.get("owner"), sd.get("owner")),
        "group": "loop" if sid in d["loop"] else "hold",
        "since": st.get("since"), "dwell_s": int(dwell), "max_dwell_s": limit,
        "stuck": bool(limit and dwell > limit),
        "attempts": st.get("attempts", 0), "max_attempts": sd.get("max_attempts"),
        "until": st.get("until"), "wait_reason": st.get("wait_reason"),
        "overdue_s": int(overdue) if overdue and overdue > 0 else None,
        "block_reason": st.get("block_reason"), "blocked_from": st.get("blocked_from"),
        "return_to": st.get("return_to"), "prev": st.get("prev"),
        "review_accepted": bool(flags.get("review_accepted")),
        "summary": sd.get("summary"), "how": sd.get("how", []), "exit": sd.get("exit", []),
        "entry": sd.get("entry", []), "evidence": sd.get("evidence", []),
        "problems": sd.get("problems", []), "next": sd.get("next", []),
    }


def timeline(inst: Instance, turns=12) -> dict:
    """由事件流计算每轮在各状态的停留时间（只用实际记录的事件）。"""
    evs = [e for e in read_jsonl(inst.events_path) if e.get("type") in ("transition", "adopt")]
    segs = []
    for i, e in enumerate(evs):
        start = parse_iso(e["ts"])
        end = parse_iso(evs[i + 1]["ts"]) if i + 1 < len(evs) else None
        to = e.get("to") or e.get("state")
        turn = e.get("turn", 0)
        segs.append({"turn": turn, "state": to, "start": e["ts"], "end": evs[i + 1]["ts"] if end else None,
                     "dur_s": int((end - start).total_seconds()) if end else int(secs_since(e["ts"]) or 0),
                     "open": end is None, "forced": bool(e.get("forced"))})
    keep = sorted({s["turn"] for s in segs})[-turns:]
    stats = {}
    for s in segs:
        if s["open"]:
            continue
        a = stats.setdefault(s["state"], {"n": 0, "total_s": 0, "max_s": 0})
        a["n"] += 1
        a["total_s"] += s["dur_s"]
        a["max_s"] = max(a["max_s"], s["dur_s"])
    for a in stats.values():
        a["avg_s"] = int(a["total_s"] / a["n"]) if a["n"] else 0
    fails = {}
    for e in read_jsonl(inst.events_path):
        if e.get("type") == "fail":
            fails[e.get("state")] = fails.get(e.get("state"), 0) + 1
    for k, v in fails.items():
        stats.setdefault(k, {"n": 0, "total_s": 0, "max_s": 0, "avg_s": 0})["fails"] = v
    return {"segments": [s for s in segs if s["turn"] in keep], "stats": stats}
