"""1.9.0（W87，collab-wf-dev 的 LIVE.md）：实景验收的测量。只读，不改任何状态。

逐轮核对 LIVE.md 第 1 节的 10 段判据，证据都来自真实记录：事件流、轮次记录、证据目录、审查结论留档、同步历史、
适配器动作日志（transport.json 的 log）。拿不到证据的一段按“不达标（无法核对）”算——实景验收只认证据。
连续达标的轮数达到 --rounds（默认 3）才算通过；1.9.0 之前的轮次缺少新记录，会如实显示无法核对。"""
from __future__ import annotations

import json
from pathlib import Path

from .core import LOCAL, parse_iso, read_json

MANUAL_TYPES = ("transition", "exec", "apply", "rollback", "conv_new", "conv_bind", "send_intent_cleared",
                "resend_not_landed", "pause", "unpause", "ref_registered", "report_draft")
AUTO_BY = ("driver",)   # 1.9.1：wf 命令（含 kick 生成回报）是有人在命令行触发的，不算自动


def _manual(e: dict) -> bool:
    # 1.9.3（W93）：有事件身份（actor）时按它判——驱动器进程里的 wf 内部转换不算人工；没有身份的旧事件按 by 判
    if e.get("type") not in MANUAL_TYPES:
        return False
    if e.get("by") in ("executor", "user"):
        return True
    if e.get("actor"):
        return e["actor"] != "driver"
    return e.get("by") not in AUTO_BY


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


def _head_at(root, ts):
    """1.9.4（审查 concerns）：项目在 ts 时刻的 HEAD（沿第一父提交，取提交时刻不晚于 ts 的最近一个）；没有版本库或查不到时返回空。"""
    from .core import git
    if not ts or not (Path(root) / ".git").exists():
        return ""
    try:
        return (git(["rev-list", "-1", "--first-parent", f"--before={ts}", "HEAD"], root, check=False).stdout or "").strip()
    except OSError:
        return ""


def _review_timing(rr: dict, e: dict) -> str:
    """1.9.4（审查 concerns）：审查结论要在这次安装命令里、且在开始复制文件之前取得；空时刻不放过。返回问题说明，没有问题返回空。"""
    from datetime import timedelta
    rts, ets = parse_iso(rr.get("ts")), parse_iso(e.get("ts"))
    if not rts:
        return "（结论没有时刻）"
    if not ets:
        return "（安装命令没有时刻）"
    start = ets - timedelta(seconds=float(e.get("duration_s") or 0))
    if rts < start - timedelta(seconds=1):
        return "（结论早于这次安装命令，不是这次取得的）"
    inst_at = parse_iso(rr.get("install_started_at"))
    if inst_at and rts > inst_at:
        return "（结论晚于开始安装）"
    if not inst_at and rts > ets:
        return "（结论晚于安装命令结束）"
    return ""


def _adapter_log():
    from . import anomaly
    p = (anomaly.transport_cfg() or {}).get("log")
    return Path(p).expanduser() if p else None


def _mine(x: dict, name: str) -> bool:
    return x.get("project", name) == name


def check(inst, rounds: int = 3, budget_s: int = 300) -> dict:
    # 1.9.3：按执行端第 123 轮的复核再修——不跳过失败的轮次（按轮号连续计）；日志条目必须带项目字段；回复要核父子 uuid；
    # 审查核版本包哈希与先后；待办按事件核关闭时刻；发现延迟不得为负；同步要带当时的 HEAD；
    # 连续达标的轮次里必须至少各有一次工具安装与项目落地；人工判定看事件身份（actor），驱动器进程里的 wf 内部转换不算人工
    import hashlib
    ev = _jsonl(inst.events_path)
    turns = _jsonl(inst.turns_path)
    sent, done = {}, {}
    for r in turns:
        t = r.get("turn")
        if r.get("status") == "sent":
            sent.setdefault(t, []).append(r)
        if r.get("ts_done"):
            done[t] = r
    allt = sorted(t for t in sent if isinstance(t, int))
    if allt and allt[-1] not in done:
        allt = allt[:-1]   # 最新一轮还在等回复，不计
    logp = _adapter_log()
    alog = _jsonl(logp) if logp and logp.exists() else None
    has_proj = bool(alog) and any("project" in x for x in alog)
    hist = _jsonl(LOCAL / "sync" / f"{inst.name}.history.jsonl")
    state_tasks = {x.get("id"): x for x in (inst.state.get("executor_tasks") or [])}
    closes = {}
    for e in ev:
        if e.get("type") in ("task_done", "task_cancelled", "task_superseded") and e.get("id"):
            closes.setdefault(e["id"], str(e.get("ts") or ""))
    results = []
    for t in allt:
        crit = []

        def add(n, ok, why):
            crit.append({"n": n, "ok": bool(ok), "why": why})
        tdir = inst.evidence / f"turn-{int(t):03d}"
        if t not in done or not (tdir / "intake.json").exists():
            add(0, False, "这一轮没有回复或没有解析记录（失败的轮次也计入，不跳过）")
            results.append({"turn": t, "ok": False, "criteria": crit, "installs": False, "applies": False})
            continue
        tev = [e for e in ev if e.get("turn") == t]
        t0 = str(sent[t][0].get("ts_sent") or "")
        t1 = str(((sent.get(t + 1) or [{}])[0]).get("ts_sent") or "")
        if alog is None:
            add(1, False, "没有适配器动作日志（transport.json 的 log），无法核对发送次数")
        elif not has_proj:
            add(1, False, "适配器日志的条目没有 project 字段，无法归属到本项目")
        else:
            k = len([x for x in alog if x.get("action") == "send" and x.get("turn") == t and x.get("project") == inst.name])
            add(1, k == 1, f"适配器日志里本轮 send {k} 次")
        rb = [e for e in tev if e.get("type") == "reply_binding"]
        last = rb[-1] if rb else {}
        checked = any(e.get("type") == "reply_checked" for e in tev)
        meta = read_json(tdir / "reply_meta.json", {}) or {}
        smeta = read_json(tdir / "send_meta.json", {}) or {}
        if meta.get("parent") and smeta.get("message_uuid"):
            pc = meta["parent"] == smeta["message_uuid"]
            pwhy = "回复的父消息就是本轮发出的消息" if pc else "回复的父消息不是本轮发出的消息"
        else:
            pc, pwhy = False, "适配器没有给出发出消息的 uuid 或回复的 parent，父子关系无法核对"
        add(2, last.get("receipt") == "ok" and checked and pc,
            "回执：" + (last.get("receipt") or "没有记录") + ("；归属已核对" if checked else "；没有 reply_checked") + "；" + pwhy)
        bad3 = [e.get("rule") for e in tev if e.get("type") == "auto" and e.get("rule") in ("RELEASE_MISSING", "FP_MISMATCH")]
        add(3, last.get("deliveries") in ("ok", "none") and not bad3,
            "交付核对：" + (last.get("deliveries") or "没有记录") + (("；" + "、".join(bad3)) if bad3 else ""))
        ups = [e for e in tev if e.get("type") == "exec" and "wf self-update" in str(e.get("cmd") or "") and e.get("exit_code") == 0]
        if not ups:
            add(4, True, "本轮没有安装工具版本（不适用）")
        else:
            vs = []
            for e in ups:
                zp = inst.root / str(e.get("cmd")).split()[-1]
                rr = read_json(LOCAL / "reviews" / f"{zp.stem}.result.json", {}) or {}
                v = rr.get("verdict") or "没有记录"
                if v in ("pass", "concerns"):
                    if not zp.is_file() or hashlib.sha256(zp.read_bytes()).hexdigest() != rr.get("zip_sha256"):
                        v += "（版本包哈希对不上或已不在）"
                    else:
                        v += _review_timing(rr, e)   # 1.9.4：按安装开始的时刻比，空时刻不放过
                vs.append(v)
            add(4, all(v in ("pass", "concerns") for v in vs), "安装前审查：" + "、".join(vs))
        man = [f"{e.get('type')}（{e.get('by')}/{e.get('actor') or '?'}）" for e in tev if _manual(e)]
        add(5, not man, "人工改动：" + ("、".join(man[:5]) if man else "无"))
        cmds = (read_json(tdir / "intake.json", {}) or {}).get("commands") or []
        ran = [e for e in tev if e.get("type") == "exec" and e.get("by") == "driver"]
        rr6 = [e for e in tev if e.get("type") == "round_result"]
        notes = _jsonl(tdir / "notes.jsonl")
        stale = any("[PLAN_STALE]" in str(n.get("text")) for n in notes)
        stops = [str(e.get("rule")) for e in tev if e.get("type") == "auto" and e.get("action") in ("block", "await_user")]
        ok6 = len(ran) >= len(cmds) and bool(rr6) and rr6[-1].get("ok") is True and not stale and not stops
        add(6, ok6, f"命令 {len(ran)}/{len(cmds)} 条由驱动器执行；本轮结果 " + ("通过" if rr6 and rr6[-1].get("ok") else "没有或未通过")
            + ("；计划过时" if stale else "") + (("；停下：" + "、".join(stops)) if stops else ""))
        ids = {x for e in tev if e.get("type") == "tasks_opened" for x in (e.get("ids") or [])}
        ids |= {k for k, x in state_tasks.items() if x.get("turn") == t}
        late = []
        for i in sorted(ids):
            c = closes.get(i) or str((state_tasks.get(i) or {}).get("done") or (state_tasks.get(i) or {}).get("closed") or "")
            if not c or (t1 and c > t1):
                late.append(i)
        add(7, not late, "没按时关闭的待办：" + ("、".join(late) if late else "无"))
        src = [r.get("from") for r in sent.get(t, [])]
        add(8, src == ["driver"], "本轮消息的发送方：" + ("、".join(map(str, src)) or "没有记录"))
        end, td = parse_iso(meta.get("ended_at")), parse_iso(done[t].get("ts_done"))
        if not end or not td:
            add(9, False, "适配器没有给出服务端的回复结束时间（ended_at），无法核对发现延迟")
        else:
            lag = (td - end).total_seconds()
            add(9, 0 <= lag <= budget_s, f"回复结束到接上 {int(lag)} 秒（预算 {budget_s} 秒，不得为负）")
        hs = [h for h in hist if h.get("status") in ("pushed", "unchanged") and h.get("commit") and h.get("head")
              and str(h.get("ts") or "") >= t0 and (not t1 or str(h.get("ts") or "") < t1)]
        bound = [h for h in hs if (lambda x, y: bool(x) and (x.startswith(y) or y.startswith(x)))(
            _head_at(inst.root, h.get("ts")), str(h.get("head")))]   # 1.9.4：HEAD 要等于项目当时的 HEAD，不只是非空
        if bound:
            add(10, True, f"本轮同步：{bound[-1]['status']} {str(bound[-1]['commit'])[:12]}，HEAD {str(bound[-1]['head'])[:12]} 与项目当时的 HEAD 一致")
        elif hs:
            add(10, False, f"本轮同步记的 HEAD {str(hs[-1]['head'])[:12]} 与项目当时的 HEAD"
                f"（{_head_at(inst.root, hs[-1].get('ts'))[:12] or '查不到'}）不一致")
        else:
            add(10, False, "本轮时间窗里没有带提交号与 HEAD 的同步")
        applies = any(e.get("type") == "apply" and not _manual(e) for e in tev)
        results.append({"turn": t, "ok": all(c["ok"] for c in crit), "criteria": crit, "installs": bool(ups), "applies": applies})
    results = results[-rounds:] if rounds else results
    streak = 0
    for r in reversed(results):
        if not r["ok"]:
            break
        streak += 1
    win = results[-streak:] if streak else []
    missing = []
    if streak >= rounds:
        if not any(r["installs"] for r in win):
            missing.append("连续达标的轮次里没有一次工具安装")
        if not any(r["applies"] for r in win):
            missing.append("连续达标的轮次里没有一次项目落地")
    return {"rounds": results, "streak": streak, "need": rounds, "missing": missing,
            "passed": bool(results) and streak >= rounds and not missing}


