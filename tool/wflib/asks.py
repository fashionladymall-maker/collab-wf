"""需要用户决定的事项（决定码）与聊天窗口呈报。

每件待用户决定的事项都有一个短的“决定码”，由事项本身的内容算出：事项变了，码就变了。
用户在聊天窗口回复“批准 K7P”，执行端把码和用户原话一起交给 wf；wf 只接受与当前待决事项一致的码。

这能防止的是“批准落到了错误的事项上”（含糊的“好的”、过期的批准、同时有两件待决事项时张冠李戴）。
它不能在技术上区分“用户本人”与“同一台电脑上的其他程序”：执行端仍然可以伪造原话。
防线是：原话被逐字记录，用户能在看板和审计中看到每一次批准用了什么原话（CAPABILITIES.md）。
"""
from __future__ import annotations

import hashlib

from . import machine, policy
from .core import Instance, WfError, now_iso, secs_since

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 去掉易混淆的 I、O、0、1
REMIND_H = 12


def _code(key: str) -> str:
    h = int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16)
    return "".join(ALPHABET[(h >> (5 * i)) % len(ALPHABET)] for i in range(3))


def _item(kind, key, title, why, reply, command, consequence, basis=""):
    c = _code(key)
    return {"code": c, "kind": kind, "key": key, "title": title, "why": why,
            "reply": [r.format(code=c) for r in reply], "command": command.format(code=c),
            "consequence": consequence, "basis": basis}


def pending(inst: Instance) -> list:
    """当前所有需要用户决定的事项。按紧迫程度排序：阻塞 > 等待批准 > 白名单与目标 > 人工确认 > 暂停。"""
    st = inst.state
    s = machine.status(inst)
    out = []
    if s["state"] == "BLOCKED":
        out.append(_item("unblock", f"unblock:{s['turn']}:{s['since']}", "流程阻塞，需要人处理",
                         s.get("block_reason") or "某一步失败次数超过上限",
                         ["已处理 {code}"], f"wf unblock --to {s.get('blocked_from') or 'COMPOSE'} --code {{code}} --said …",
                         "流程保持停住，不会自动继续", "wf status 显示该步的异常处置表"))
    if s["state"] == "AWAITING_USER":
        out.append(_item("approve", f"approve:{s['turn']}:{s['since']}", "需要你批准才能继续",
                         st.get("needs_text") or "驱动器停下来等你决定",
                         ["批准 {code}", "暂停"], "wf approve --code {code} --said …",
                         "驱动器保持停住，不会自动继续", "评审结果与证据在看板“本轮执行”"))
    ps = policy.status(inst, "policy")
    if ps["exists"] and not ps["approved"]:
        why = "白名单在批准后被改动，需要重新批准" if ps["changed_since_approval"] else "白名单尚未批准"
        out.append(_item("policy", f"policy:{ps['current']}", "批准自动执行的命令白名单", why,
                         ["批准 {code}"], "wf policy approve --code {code} --said …",
                         "驱动器不会自动执行任何操作", "workflow/policy.json"))
    gs = policy.status(inst, "goals")
    if gs["exists"] and not gs["approved"]:
        why = "目标在批准后被改动，需要重新批准" if gs["changed_since_approval"] else "目标尚未批准"
        out.append(_item("goals", f"goals:{gs['current']}", "批准目标与完成判据", why,
                         ["批准 {code}"], "wf goals approve --code {code} --said …",
                         "无法判定“做完了”", "workflow/goals.json"))
    elif gs["approved"]:
        from . import goals
        for g in goals.progress(inst)["items"]:
            if g["kind"] == "manual" and not g["ok"]:
                out.append(_item(f"confirm:{g['id']}", f"confirm:{g['id']}:{gs['current']}", f"确认目标 {g['id']}",
                                 g["title"], ["确认 {code}"], f"wf goals confirm {g['id']} --code {{code}} --said …",
                                 f"目标 {g['id']} 一直算未完成：{g.get('on_fail') or ''}", "workflow/goals.json"))
    if st.get("paused") and st["paused"].get("by") == "executor":   # 1.7.5（P1-A）：执行端发起的暂停交给执行端
        it = _item("unpause", f"unpause:{st['paused'].get('ts')}", "自动化已暂停（执行端发起）",
                   st["paused"].get("note") or "已暂停", ["继续 {code}"], "wf unpause --by executor --reason …",
                   "保持暂停，驱动器不会推进这个项目")
        it["initiator"] = "executor"
        out.append(it)
    elif st.get("paused"):
        out.append(_item("unpause", f"unpause:{st['paused'].get('ts')}", "自动化已暂停",
                         st["paused"].get("note") or "已暂停", ["继续 {code}"], "wf unpause --code {code} --said …",
                         "保持暂停，驱动器不会推进这个项目"))
    return out


def notices(inst: Instance) -> list:
    """不需要决定、但应该让用户知道一次的事。"""
    s = machine.status(inst)
    out = []
    if s["state"] == "DONE":
        why = "全部目标判据已通过，自动化停在这里"
        # 1.6.7：目标达成那一轮登记的执行端待办做完后，结果写进这条提示。key 不变（done:<轮次>），交给用户、
        # 没有 action 与 code，看门狗仍按正常完成提示识别；DONE 不自动回报
        fin = [t for t in (inst.state.get("executor_tasks") or []) if t.get("turn") == s["turn"] and t.get("done")]
        if fin:
            why += "；本轮执行端待办已完成：" + "；".join(f"{t['id']}：{(t.get('note') or '没有写说明')[:80]}" for t in fin[:5])
        out.append({"key": f"done:{s['turn']}", "title": "目标已达成", "why": why})
    if s["state"] == "QUOTA_WAIT":
        out.append({"key": f"quota:{s.get('until')}", "title": "额度用尽，等待重置",
                    "why": f"将在 {s.get('until')} 后自动继续，不需要你做什么"})
    if s.get("stuck") and not inst.state.get("paused") and inst.cfg.get("auto") is not False:
        # 1.6.9（N9）：与看门狗一致，暂停期间不报停滞；auto=false 的项目驱动器不推进，停留本来就不变
        out.append({"key": f"stuck:{s['state']}:{s['since']}", "title": f"“{s['name']}”停留超过上限",
                    "why": "驱动器会按异常处置表处理；若一直这样，看板会显示原因"})
    for t in (inst.state.get("executor_tasks") or []):   # 1.6.6：写给执行端的待办，做完前一直在 inbox 里
        if not t.get("done"):
            out.append({"key": f"task:{t['id']}", "title": f"执行端待办 {t['id']}", "why": t.get("text") or "",
                        "action": f"做完后：wf task done {t['id']} --note \"结果\""})
    from .heartbeat import driver_health   # 1.5.6：驱动器停了要让用户知道（手写循环会静默退出）
    hb = driver_health(inst)
    if hb["status"] == "stale":
        out.append({"key": f"driver_stale:{hb['last_ts']}", "title": "驱动器停了",
                    "why": f"最近一次运行在 {hb['age_s'] // 60} 分钟前，系统定时器可能没有运行。执行端用 wf schedule status 检查，"
                           "按 docs/PROCESS.md 第 1 节第 3 步安装定时器"})
    try:   # 1.6.0：项目同步因疑似密钥暂停、或连续失败（docs/SYNC.md）
        from . import sync
        out += sync.notices(inst)
    except Exception:   # noqa: BLE001
        pass
    return out


def verify(inst: Instance, kind: str, code: str) -> dict:
    """确认码与当前待决事项一致。kind 可以是前缀（例如 confirm:G2）。"""
    items = pending(inst)
    code = (code or "").strip().upper()
    for it in items:
        if it["code"] == code and (it["kind"] == kind or it["kind"].startswith(kind + ":")):
            return it
    cur = "；".join(f"{it['code']}（{it['title']}）" for it in items) or "无"
    raise WfError(f"决定码“{code}”与当前待决事项不符，或已过期。当前待决事项：{cur}")


def executor_decision(inst: Instance, kind: str, item: dict, reason: str) -> str:
    """1.6.5（W43）：执行端代决。依据用户第 44 轮的决策顺位：原本要用户决定的事，先由执行端判断、决定并执行，
    做不到的才交给用户。只限交给执行端的事项（notify.route）；四道底线、撤销暂停、目标达成仍只认用户原话。
    事件如实记 by=executor（M1 判据把它算作人工动作）。返回写进记录的说明。"""
    from . import notify, roles
    reason = (reason or "").strip()
    if not reason:
        raise WfError("执行端代决需要 --reason：写明执行端为什么这样决定")
    if notify.route(item) != "executor":
        raise WfError("这件事必须由用户决定（四道底线、撤销暂停或目标达成），执行端不能代决：请转告用户，用 --said 记下用户原话")
    inst.event("executor_decision", kind=kind, code=item["code"], reason=reason[:500], title=item["title"], by="executor")
    roles.decide(inst, "executor", f"{item['title']}（决定码 {item['code']}）", f"执行端代决：{reason[:300]}", "", "已执行",
                 by="executor")
    return f"执行端代决：{reason}"


def render(inst: Instance, it: dict) -> str:
    s = machine.status(inst)
    alt = "；或回复：" + "、".join(f"「{r}」" for r in it["reply"][1:]) if len(it["reply"]) > 1 else ""
    lines = [f"【需要你决定 · {inst.name} · 决定码 {it['code']}】",
             f"事项：{it['title']}",
             f"现在：第 {s['turn']} 轮 · {s['name']}",
             f"原因：{it['why']}",
             f"你回复：「{it['reply'][0]}」{alt}",
             f"不处理会怎样：{it['consequence']}"]
    if it.get("basis"):
        lines.append(f"依据：{it['basis']}")
    return "\n".join(lines)


def ask(inst: Instance, only_new=False, mark=False) -> dict:
    """呈报：返回需要出现在聊天窗口里的内容。去重：同一事项只呈报一次，仍未处理时每 12 小时提醒一次。"""
    st = inst.state
    shown = st.setdefault("asks_shown", {})
    items, notes = pending(inst), notices(inst)
    live = {it["code"] for it in items} | {n["key"] for n in notes}
    for k in list(shown):
        if k not in live:
            del shown[k]
    out_items, out_notes = [], []
    for it in items:
        rec = shown.get(it["code"])
        if not only_new or rec is None or (secs_since(rec["ts"]) or 0) > REMIND_H * 3600:
            out_items.append(it)
    for n in notes:
        if not only_new or n["key"] not in shown:
            out_notes.append(n)
    if mark:
        for it in out_items:
            r = shown.get(it["code"], {"n": 0})
            shown[it["code"]] = {"ts": now_iso(), "n": r["n"] + 1}
        for n in out_notes:
            shown[n["key"]] = {"ts": now_iso(), "n": 1}
        inst.save()
    text = "\n\n".join([render(inst, it) for it in out_items] +
                       [f"【通知 · {inst.name}】{n['title']}：{n['why']}" for n in out_notes])
    return {"items": out_items, "notices": out_notes, "text": text}


def panel(inst: Instance) -> str:
    """一块自包含的 HTML 小组件，执行端内联渲染进 WorkBuddy 聊天窗口（不开浏览器）。
    只放用户真正要看的最小集：现在在哪、要不要他拍板（含决定码与照抄的话）、下一次自动检查时间。
    与每轮回报的分工：回报是“这一轮做了什么”，面板是“此刻的状态与待办”，不重复叙述过程。"""
    from . import driver
    from .core import parse_iso
    e = driver.explain(inst)
    items, notes = pending(inst), notices(inst)
    st = inst.state
    nxt = ""
    until = parse_iso(st.get("until")) if st.get("state") in ("WAIT_SCHEDULE", "QUOTA_WAIT") else None
    if until:
        nxt = f"下一次自动检查：{until.strftime('%m-%d %H:%M')}"
    elif st.get("paused"):
        nxt = "已暂停，不会自动推进"
    esc = lambda x: (str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    rows = ""
    for it in items:
        reply = esc(it["reply"][0])
        alt = "".join(f'<span class="k">{esc(r)}</span>' for r in it["reply"][1:])
        rows += (f'<div class="it"><div class="t">{esc(it["title"])}'
                 f'<span class="code">{esc(it["code"])}</span></div>'
                 f'<div class="w">{esc(it["why"])}</div>'
                 f'<div class="r">回复 <span class="k">{reply}</span>{alt}</div>'
                 f'<div class="c">不处理：{esc(it["consequence"])}</div></div>')
    for n in notes:
        rows += f'<div class="it note"><div class="t">{esc(n["title"])}</div><div class="w">{esc(n["why"])}</div></div>'
    if not rows:
        rows = '<div class="ok">暂时不需要你做什么。</div>'
    color = "#B4761A" if (items or st.get("paused")) else "#3B7D4F"
    return (
        '<div style="font:14px/1.5 -apple-system,system-ui,sans-serif;max-width:560px;'
        'border:1px solid #d9d4c8;border-radius:10px;overflow:hidden;margin:4px 0">'
        f'<div style="background:{color};color:#fff;padding:8px 12px;font-weight:600">'
        f'{esc(inst.name)} · {esc(e.get("where",""))}</div>'
        '<div style="padding:10px 12px">'
        f'<div style="color:#555;margin-bottom:8px">{esc(e.get("why",""))}'
        f'{("　·　" + esc(nxt)) if nxt else ""}</div>'
        '<style>'
        '.it{border-top:1px solid #eee;padding:8px 0}.it:first-of-type{border-top:0}'
        '.it .t{font-weight:600}.it .code{background:#2d2d2d;color:#fff;border-radius:4px;'
        'padding:1px 7px;margin-left:8px;font-family:ui-monospace,monospace;letter-spacing:1px}'
        '.it .w{color:#555;margin:2px 0}.it .r{margin:3px 0}.it .c{color:#999;font-size:12.5px}'
        '.k{background:#f0ece0;border:1px solid #d9d4c8;border-radius:4px;padding:1px 6px;margin-right:6px;'
        'font-family:ui-monospace,monospace}.note .t{color:#555;font-weight:500}'
        '.ok{color:#3B7D4F;padding:4px 0}'
        '</style>'
        f'{rows}</div></div>')
