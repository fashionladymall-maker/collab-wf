【回报 · 第 18 轮 · v0.5】档位：high

① 环境障碍与偏离
无。

② 关键数字与终端原文

1) wf.py 中 tick 的解析与分派位置。 —— 通过
```
$ grep -n tick /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
42:  wf tick [--max-seconds 900]         推进全部项目（多项目调度）
133:    names = {"auto": "自动处置", "tick": "驱动", "transition": "转换", "approve": "用户批准", "goal_check": "目标检查",
141:        elif t == "tick":
207:        label = "local.collab-wf.tick"
217:    <string>tick</string>
230:  <key>StandardOutPath</key><string>{LOCAL / "tick.log"}</string>
231:  <key>StandardErrorPath</key><string>{LOCAL / "tick.log"}</string>
254:    log = LOCAL / "tick.log"
308:    x = sub.add_parser("tick"); x.add_argument("--max-seconds", type=float, default=900)
646:    elif c == "tick":
649:        _log = LOCAL / "tick.log"
652:            _os.replace(_log, LOCAL / "tick.log.1")
660:        code, rows = driver.tick(insts, args.max_seconds)
exit=0  耗时 0.0s
```

2) driver.py 的 tick 函数全文（多项目调度与返回值）。 —— 通过
```
$ grep -n -A 45 ^def.tick /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
966:def tick(projects: list, max_seconds=900) -> tuple:
967-    """一次唤醒推进多个项目。顺序：可以立即行动的在前（按 priority 从高到低），等待中的其后，需要用户的跳过。"""
968-    t0 = time.time()
969-    rows = []
970-    for inst in projects:
971-        if inst.cfg.get("auto") is False:
972-            rows.append((inst, -1, "未启用自动驱动（wf.json auto=false）"))
973-            continue
974-        s = inst.state
975-        st_name = s.get("state", "IDLE")
976-        actionable = st_name in ("COMPOSE", "SEND", "INTAKE", "REVIEW", "APPLY", "EXECUTE", "VERIFY") or (
977-            st_name in ("WAIT_SCHEDULE", "QUOTA_WAIT") and parse_iso(s.get("until")) and now() >= parse_iso(s["until"])) or (
978-            st_name == "WAIT_REPLY")
979-        rank = (2 if actionable else 1) * 1000 + int(inst.cfg.get("priority", 0))
980-        rows.append((inst, rank, ""))
981-    rows.sort(key=lambda r: -r[1])
982-    results, codes = [], []
983-    for inst, rank, skip in rows:
984-        if skip:
985-            results.append({"project": inst.name, "exit": None, "note": skip})
986-            continue
987-        left = max_seconds - (time.time() - t0)
988-        if left <= 5:
989-            results.append({"project": inst.name, "exit": None, "note": "本次唤醒时间已用完，下次再推进"})
990-            continue
991-        code, info = run(inst, max_seconds=min(left, max_seconds))
992-        codes.append(code)
993-        results.append({"project": inst.name, "exit": code, "note": info["note"], "explain": info.get("explain")})
994-    if not codes:
995-        overall = 0
996-    elif 20 in codes:
997-        overall = 20
998-    elif 0 in codes:
999-        overall = 0
1000-    elif all(c == 30 for c in codes):
1001-        overall = 30
1002-    else:
1003-        overall = 10
1004-    return overall, results
exit=0  耗时 0.0s
```

3) asks.py 第 114–142 行（wf ask 的"只看新的"与标记已读）。 —— 通过
```
$ sed -n 114,142p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/asks.py
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
exit=0  耗时 0.0s
```

③ 需要决策的点
无。

④ 需要注意（Attention）
- 当前对话已累计约 293233 字符、17 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

附：仓库指纹 3345de3ba3bf580a（v0.5，4 个文件）
附：发送前额度 5 小时窗口 5.0%，7 天窗口 64.0%
