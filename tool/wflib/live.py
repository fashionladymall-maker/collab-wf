"""1.9.0（W87，collab-wf-dev 的 LIVE.md）：实景验收的测量。只读，不改任何状态。

逐轮核对 LIVE.md 第 1 节的 10 段判据，证据都来自真实记录：事件流、轮次记录、证据目录、审查结论留档、同步历史、
适配器动作日志（transport.json 的 log）。拿不到证据的一段按“不达标（无法核对）”算——实景验收只认证据。
连续达标的轮数达到 --rounds（默认 3）才算通过；1.9.0 之前的轮次缺少新记录，会如实显示无法核对。"""
from __future__ import annotations

import json
from pathlib import Path

from .core import LOCAL, parse_iso, read_json

MANUAL_TYPES = ("transition", "exec", "apply", "rollback", "conv_new", "conv_bind", "send_intent_cleared",
                "resend_not_landed", "pause", "unpause", "ref_registered")
AUTO_BY = ("driver", "wf")


def _jsonl(p) -> list:
    out = []
    try:
        for line in Path(p).read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def _adapter_log():
    from . import anomaly
    p = (anomaly.transport_cfg() or {}).get("log")
    return Path(p).expanduser() if p else None


def _mine(x: dict, name: str) -> bool:
    return x.get("project", name) == name


def check(inst, rounds: int = 3, budget_s: int = 300) -> dict:
    from .tasks import status_of
    ev = _jsonl(inst.events_path)
    turns = _jsonl(inst.turns_path)
    sent, done = {}, {}
    for r in turns:
        t = r.get("turn")
        if r.get("status") == "sent":
            sent.setdefault(t, []).append(r)
        if r.get("ts_done"):
            done[t] = r
    logp = _adapter_log()
    alog = _jsonl(logp) if logp and logp.exists() else None
    hist = _jsonl(LOCAL / "sync" / f"{inst.name}.history.jsonl")
    tasks = inst.state.get("executor_tasks") or []
    cand = sorted(t for t in done if t in sent and (inst.evidence / f"turn-{int(t):03d}" / "intake.json").exists())
    results = []
    for t in (cand[-rounds:] if rounds else cand):
        crit = []

        def add(n, ok, why):
            crit.append({"n": n, "ok": bool(ok), "why": why})
        tev = [e for e in ev if e.get("turn") == t]
        if alog is None:
            add(1, False, "没有适配器动作日志（transport.json 的 log），无法核对发送次数")
        else:
            k = len([x for x in alog if x.get("action") == "send" and x.get("turn") == t and _mine(x, inst.name)])
            add(1, k == 1, f"适配器日志里本轮 send {k} 次")
        rb = [e for e in tev if e.get("type") == "reply_binding"]
        last = rb[-1] if rb else {}
        add(2, last.get("receipt") == "ok", "回执核对：" + (last.get("receipt") or "没有记录"))
        bad3 = [e.get("rule") for e in tev if e.get("type") == "auto" and e.get("rule") in ("RELEASE_MISSING", "FP_MISMATCH")]
        add(3, last.get("deliveries") in ("ok", "none") and not bad3,
            "交付核对：" + (last.get("deliveries") or "没有记录") + (("；" + "、".join(bad3)) if bad3 else ""))
        ups = [e for e in tev if e.get("type") == "exec" and "wf self-update" in str(e.get("cmd") or "")]
        if not ups:
            add(4, True, "本轮没有安装工具版本（不适用）")
        else:
            vs = []
            for e in ups:
                z = str(e.get("cmd")).split()[-1]
                vs.append((read_json(LOCAL / "reviews" / f"{Path(z).stem}.result.json", {}) or {}).get("verdict") or "没有记录")
            add(4, all(v in ("pass", "concerns") for v in vs), "安装前审查：" + "、".join(vs))
        man = [f"{e.get('type')}（{e.get('by')}）" for e in tev if e.get("type") in MANUAL_TYPES and e.get("by") not in AUTO_BY]
        add(5, not man, "人工改动：" + ("、".join(man[:5]) if man else "无"))
        notes = _jsonl(inst.evidence / f"turn-{int(t):03d}" / "notes.jsonl")
        stale = any("[PLAN_STALE]" in str(n.get("text")) for n in notes)
        stops = [str(e.get("rule")) for e in tev if e.get("type") == "auto" and e.get("action") in ("block", "await_user")]
        add(6, not stale and not stops, ("计划过时；" if stale else "") + ("停下：" + "、".join(stops) if stops else "没有停下"))
        nxt = ((sent.get(t + 1) or [{}])[0]).get("ts_sent")
        late = [x["id"] for x in tasks if x.get("turn") == t and (status_of(x) == "open"
                or (nxt and str(x.get("done") or x.get("closed") or "") > str(nxt)))]
        add(7, not late, "没按时关闭的待办：" + ("、".join(late) if late else "无"))
        src = [r.get("from") for r in sent.get(t, [])]
        add(8, src == ["driver"], "本轮消息的发送方：" + ("、".join(map(str, src)) or "没有记录"))
        if alog is None:
            add(9, False, "没有适配器动作日志，无法核对回复发现延迟")
        else:
            td = parse_iso(done[t].get("ts_done"))
            first = min((parse_iso(x.get("ts")) for x in alog if x.get("action") == "done" and x.get("turn") == t
                         and _mine(x, inst.name) and parse_iso(x.get("ts"))), default=None)
            lag = (td - first).total_seconds() if td and first else None
            add(9, lag is not None and lag <= budget_s,
                f"回复结束到接上 {int(lag)} 秒（预算 {budget_s} 秒）" if lag is not None else "日志里没有本轮的 done")
        hs = [h for h in hist if h.get("turn") == t and h.get("status") in ("pushed", "unchanged") and h.get("commit")]
        add(10, bool(hs), f"本轮同步：{hs[-1]['status']} {str(hs[-1]['commit'])[:12]}" if hs else "没有带提交号的同步记录")
        results.append({"turn": t, "ok": all(c["ok"] for c in crit), "criteria": crit})
    streak = 0
    for r in reversed(results):
        if not r["ok"]:
            break
        streak += 1
    return {"rounds": results, "streak": streak, "need": rounds, "passed": bool(results) and streak >= rounds}
