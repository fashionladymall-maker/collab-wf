【回报 · 第 12 轮 · v0.2】档位：high

① 环境障碍与偏离
- 【新发现 W22：回报标题行的版本号正则只接受两段 vX.Y，三段版本号永远匹配失败；且失败后驱动器报「工具缺陷」，归因误导】

现象：我用 wf kick --kind report 发起第 11 轮，消息首行写成「【回报 · 第 11 轮 · v1.5.2】档位：high」（v1.5.2 是工具当前版本，见 wf version 输出）。驱动器发送前校验不通过，直接 BLOCKED：
  [OUTGOING_STRUCTURE] 自动生成的回报结构不完整（工具缺陷），不发送。 缺少标题行：【回报 · 第 N 轮 · vX.Y】
改成「v0.2」（项目版本，两段）后立即通过：wf check-outgoing 输出「✓ 可以发送（7008 字符）」。

根因（已用最小复现定位）：wflib/ops.py:509 的判据是
    if not re.search(r"【回报 · 第 \d+ 轮 · v\d+\.\d+】", text):
正则尾部是 v\d+\.\d+】——**只吃两段**。文本「v1.5.2】」在 v1 之后遇到第二个点号，正则要求紧跟「】」而实际是「.」，回溯也无法救，必然失败。实测把首行单独取出、逐字符比对码点，两边的分隔符都是 U+00B7、空格都是 U+0020，字符本身没问题，问题就是段数。

两点建议（请 Claude 裁定）：
1. 判据放宽为允许可选的第三段，例如 v\d+\.\d+(\.\d+)?】。理由：工具自己的版本号是三段（wf version 输出 1.5.2），项目版本将来也可能升到 v0.2.1，届时同一个坑会再踩一次。第 10 轮能用 v0.2 通过纯属巧合（项目恰好是两段）。
2. 提示语改掉「（工具缺陷）」这个措辞。这次的失败是**执行端消息格式不符合约定**，不是工具缺陷；而提示语把归因写死成工具缺陷，会把排查方向带偏（我第一反应就是去翻 ops.py 找 bug，实际先该做的是检查自己的标题行）。建议改成中性的「标题行不符合约定格式：需要「【回报 · 第 N 轮 · vX.Y】」，当前首行为 …」，并回显实际首行。

补充：本轮不因此改动工具（执行端不写代码），只记录事实与建议。

② 关键数字与终端原文

1) driver.py 第 368–560 行（h_send、h_wait_reply 中的 poll 调用）。 —— 通过
```
$ sed -n 368,560p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
    if b.get("date") != today:
        b.update(date=today, rounds=0)
    limit = int(ctx.pol.get("max_rounds_per_day", 20))
    if b["rounds"] >= limit:
        return _anomaly(ctx, {"source": "budget", "kind": "daily_limit"},
                        f"今日自动发送已达 {limit} 轮（policy.max_rounds_per_day），为防止失控已停下")
    machine.go(inst, "SEND", by="driver", reason="消息已通过发送前检查")
    return "progress", f"消息已就绪（{out['kind']}，档位 {out['effort']}）"


def h_send(ctx):
    inst, st = ctx.inst, ctx.st
    w = _wait_retry(ctx)
    if w:
        return w
    out = st.get("outgoing") or {}
    turn = st["turn"]
    intent = st.get("send_intent")
    tdir = inst.turn_dir()
    if intent and intent.get("turn") == turn and intent.get("status") == "pending":
        pargs = ["--project", inst.name, "--turn", turn, "--ref", intent.get("ref") or "", "--out", tdir / "delivered"]
        if anomaly.transport_cfg().get("poll_since"):
            # 1.5.1：没有 ref 时，让适配器按“发送时刻之后、内容与本轮消息相同的那条消息”定位（transport.json 的 poll_since）
            pargs += ["--since", intent.get("ts") or "", "--message", intent.get("file") or out.get("file") or "",
                      "--prev-ref", st.get("send_ref") or ""]   # 上一轮的 ref 原样交回，适配器可据此找到会话
        r = anomaly.call("poll", pargs)
        status = r["data"].get("status")
        if r["code"] == 0 and status in ("generating", "done"):
            intent.update(status="accepted", ref=r["data"].get("ref") or intent.get("ref"))
            return _finalize_send(ctx, out, "发送曾被中断，经轮询确认消息已被接受")
        # 1.5.1：policy 的 send_unknown_wait 为真时先自动等待并反复确认（长回复可达半小时以上），绝不自动重发
        return _anomaly(ctx, {"source": "driver", "kind": fidelity.send_unknown_kind(ctx.pol)},
                        f"发送意图 {intent.get('ts')} 结果不明；轮询返回 {status or r['raw'][:120]}")
    rate = anomaly.transport_cfg().get("rate") or {}
    if rate.get("max_sends"):
        win = int(rate.get("window_s", 600))
        recent = [r for r in read_jsonl(LOCAL / "send_log.jsonl", limit=500) if (secs_since(r.get("ts")) or 1e9) < win]
        if len(recent) >= int(rate["max_sends"]):
            until = parse_iso(recent[0]["ts"]) + timedelta(seconds=win)
            st["retry_at"] = until.isoformat(timespec="seconds")
            inst.save()
            decide(inst, "rate", f"推迟发送到 {st['retry_at']}", f"{win} 秒内已发送 {len(recent)} 条（上限 {rate['max_sends']}）")
            return "wait", f"全局限速：{win} 秒内已发送 {len(recent)} 条，推迟到 {st['retry_at']}"
    st["send_intent"] = {"turn": turn, "status": "pending", "ts": now_iso(), "file": out.get("file")}
    inst.save()
    _quota(inst)
    send_args = ["--project", inst.name, "--turn", turn, "--message", out.get("file"),
                 "--effort", out.get("effort") or "high",
                 "--model", out.get("model") or anomaly.transport_cfg().get("model", "")]
    if st.get("resend_parent"):
        send_args += ["--parent", st["resend_parent"]]
    r = anomaly.call("send", send_args)
    code = r["code"]
    if code == 0:
        # 不只信退出码：适配器必须给出“生成已开始”的正面证据，否则按“通道忙”处理（缺口 11-3、F22）
        if r["data"].get("accepted") is False or r["data"].get("status") == "heartbeat_only":
            st["send_intent"] = None
            inst.save()
            return _anomaly(ctx, {"source": "transport", "op": "send", "code": 2, "text": r["raw"]},
                            "适配器返回 0 但未确认生成开始（只有心跳）")
        st["send_intent"].update(status="accepted", ref=r["data"].get("ref"))
        st.pop("resend_parent", None)
        return _finalize_send(ctx, out, "通道已接受")
    if code != 4:  # 4 = 暂时性错误或超时：结果可能不明，保留意图留待下次轮询确认
        st["send_intent"] = None
    inst.save()
    facts = {"source": "transport", "op": "send", "code": code, "resets_at": r["data"].get("resets_at"),
             "kind": r.get("kind"), "text": r["raw"]}
    if r.get("kind") in ("not_configured", "bad_output"):
        facts = {"source": "transport", "kind": r["kind"], "text": r["raw"]}
    return _anomaly(ctx, facts, r["raw"][:300])


def _finalize_send(ctx, out, note):
    inst, st = ctx.inst, ctx.st
    model = out.get("model") or anomaly.transport_cfg().get("model")
    append_jsonl(LOCAL / "send_log.jsonl", {"ts": now_iso(), "project": inst.name, "turn": st["turn"]})
    st["send_ref"] = st["send_intent"].get("ref")
    b = st.setdefault("budget", {"date": now().strftime("%Y-%m-%d"), "rounds": 0})
    b["rounds"] = b.get("rounds", 0) + 1
    inst.save()
    ops.mark_sent(inst, out.get("file"), out.get("kind"), out.get("effort"), model, by="driver",
                  role=out.get("role"), reasons=out.get("reasons"))
    st = inst.state
    st["send_intent"] = None
    st["outgoing"] = None
    st["poll_after"] = (now() + timedelta(seconds=int(ctx.pol.get("first_poll_after_s", 60)))).isoformat(timespec="seconds")
    inst.save()
    return "progress", note


def _quota(inst):
    if anomaly.transport_cfg().get("quota"):
        r = anomaly.call("quota", ["--project", inst.name, "--out", inst.usage_path], timeout=60)
        if r["code"] != 0:
            inst.event("auto", rule="QUOTA_READ", action="ignore", detail=r["raw"][:200], by="driver")


def h_wait_reply(ctx):
    inst, st = ctx.inst, ctx.st
    w = _wait_retry(ctx)
    if w:
        return w
    if not st.get("send_ref"):
        # 本轮是在驱动器之外手动发出的，没有登记 ref，无法自动接续（缺口 7）
        return _anomaly(ctx, {"source": "driver", "kind": "no_ref"},
                        "本轮消息是手动发出的、没有登记 ref，驱动器无法轮询接续。请手动 wf mark replied，"
                        "或下次用 wf mark sent --ref（适配器返回的 ref）发送")
    pa = st.get("poll_after")
    if pa and (secs_since(pa) or 0) < 0:
        return "wait", "Claude 正在处理（稍后检查）"
    tdir = inst.turn_dir()
    r = anomaly.call("poll", ["--project", inst.name, "--turn", st["turn"], "--ref", st.get("send_ref") or "",
                              "--out", tdir / "delivered"])
    status = r["data"].get("status")
    interval = int(ctx.pol.get("poll_interval_s", 120))
    # 独立落地核对（缺口 11/§3）：send 返回 0 只是适配器的说法。这里由 wf 查会话确认消息真的出现了。
    # landed 字段（适配器可给出）或 generating/done 都算“确实落地”；若连续 none，说明消息没落地，按 POLL_NONE 退避/阻塞。
    if status in ("generating", "done") or r["data"].get("landed"):
        st.pop("send_unconfirmed", None)
    elif status == "none":
        n = st.get("send_unconfirmed", 0) + 1
        st["send_unconfirmed"] = n
        if n >= int(ctx.pol.get("landed_confirm_polls", 5)):
            st["send_unconfirmed"] = 0
            st.pop("send_ref", None)
            inst.save()
            return _anomaly(ctx, {"source": "driver", "kind": "send_not_landed"},
                            f"适配器曾报告已接受，但连续 {n} 次轮询都看不到这条消息出现在会话中（缺口 11/§3）")
        # 落地未确认：用较短的间隔尽快再查一次，不进入 POLL_NONE 的长退避
        st["poll_after"] = (now() + timedelta(seconds=int(ctx.pol.get("landed_poll_s", 60)))).isoformat(timespec="seconds")
        inst.save()
        return "wait", f"已发送但消息尚未出现在会话中，第 {n} 次核对，稍后再查"
    if r["code"] == 0 and status == "generating":
        st["poll_after"] = (now() + timedelta(seconds=interval)).isoformat(timespec="seconds")
        dwell = machine.status(inst)["dwell_s"]
        if dwell > 3 * (machine.sdef("WAIT_REPLY").get("max_dwell_s") or 2700):
            return _anomaly(ctx, {"source": "dwell", "state": "WAIT_REPLY"}, f"已等待 {dwell // 60} 分钟")
        inst.save()
        return "wait", f"Claude 正在生成（已等待 {dwell // 60} 分钟）"
    if r["code"] == 0 and status == "done":
        rf = r["data"].get("reply_file")
        if not rf or not Path(rf).exists():
            return _anomaly(ctx, {"source": "transport", "kind": "bad_output"}, f"轮询报告完成，但回复文件不存在：{rf}")
        verdict, why = fidelity.reply_attribution(r["data"], st.get("send_ref"))
        if verdict == "stale":
            # 1.5.0：取回的不是本轮消息的回复（第 30 轮取回了对话里已有的上一条回复）：不收下，继续等
            n = int(st.get("stale_polls", 0)) + 1
            st["stale_polls"] = n
            st["poll_after"] = (now() + timedelta(seconds=interval)).isoformat(timespec="seconds")
            inst.save()
            if n >= int(ctx.pol.get("stale_reply_polls", 3)):
                st["stale_polls"] = 0
                inst.save()
                return _anomaly(ctx, {"source": "driver", "kind": "stale_reply"},
                                f"连续 {n} 次取回的都不是本轮消息的回复：{why}")
            return "wait", f"取回的不是本轮消息的回复（{why}），第 {n} 次，稍后再查"
        st.pop("stale_polls", None)
        inst.event("reply_unverified" if verdict == "unverified" else "reply_checked", note=why or None, by="driver")
        st["delivered"] = [str(Path(f)) for f in r["data"].get("files", []) if Path(f).exists()]
        if r["data"].get("other_deliveries"):
            # 同一轮里有多次生成各自交付了文件（例如一次被安全防护拦下后续写、一次正常回复）。
            # 只采用最后一条完整回复交付的文件，并记录下来（§7）。
            n = r["data"]["other_deliveries"]
            decide(inst, "delivery", f"本轮存在 {n} 套其他交付，只采用最后一条完整回复的文件",
                   "同一轮的多次生成会在同一个容器里各自交付文件；以最后一条完整回复为准", ", ".join(Path(f).name for f in st["delivered"]),
                   "已记录")
            inst.event("auto", rule="MULTI_DELIVERY", action="report", detail=f"其他交付 {n} 套", by="driver")
            ops.add_note(inst, "deviation", f"[MULTI_DELIVERY] 本轮存在 {n} 套其他交付，只采用了最后一条完整回复的文件", by="driver")
        st.pop("poll_after", None)
        inst.save()
        _quota(inst)
        ops.mark_replied(inst, rf, r["data"].get("tools"), by="driver")
        return "progress", f"收到回复（交付文件 {len(st['delivered'])} 个）"
    if status == "refused":
        # 叶子被通道的安全防护拒绝，无法从它继续：回退到上一条健康消息，重新组织并重发本轮消息（§10）
        cnt = st.setdefault("anomaly_counts", {})
        key = f"{st['turn']}:LEAF_REFUSED"
        cnt[key] = cnt.get(key, 0) + 1
        inst.event("auto", rule="LEAF_REFUSED", action="recover", attempt=cnt[key], detail=r["raw"][:200], by="driver")
        decide(inst, "recover", "叶子被安全防护拒绝，回退并重发本轮", "通道对该消息返回拒绝，无法从它继续",
               r["data"].get("parent_ref") or "", "重新组织消息")
        if cnt[key] > int(ctx.pol.get("max_leaf_refusals", 2)):   # 1.5.0：可配置；建议设为 0，第一次被拒就停下
            machine.go(inst, "BLOCKED", by="driver", reason="叶子连续被安全防护拒绝", force=True,
                       block_reason="同一轮消息连续被通道的安全防护拒绝，可能是消息内容触发了防护", blocked_from="WAIT_REPLY")
            inst.state["needs_text"] = "同一轮消息连续被安全防护拒绝。请检查消息内容后 wf unblock。"
            inst.save()
            return "user", "叶子连续被安全防护拒绝，已停下等用户"
        st.pop("send_ref", None)
        st["send_intent"] = None
        st["resend_parent"] = r["data"].get("parent_ref")
        st["compose_reason"] = "resend"
        inst.save()
exit=0  耗时 0.0s
```

2) anomaly.call 的定义（适配器怎样被调用、返回什么）。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n -A 30 def.call /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/anomaly.py
75:def call(op: str, args: list, timeout=None) -> dict:
76-    """调用适配器（send/poll/quota 三个可执行程序之一）。返回 {"code": 退出码, "data": JSON 或 {}, "raw": 原始输出}。
77-    适配器缺失、超时、输出不是 JSON 时，kind 分别为 not_configured / timeout / bad_output（由异常字典处置）。"""
78-    cfg = transport_cfg()
79-    env = driver_env()
80-    mod = cfg.get("module")
81-    if mod:
82-        # 模块方式：python -m <模块>；通道适配器放在工具目录之外（module_path），工具目录只提供 adapters.base 接口
83-        mpath = os.path.expanduser(cfg.get("module_path") or "")
84-        env["PYTHONPATH"] = os.pathsep.join(x for x in (mpath, str(WF_HOME), env.get("PYTHONPATH", "")) if x)
85-        env["WF_TRANSPORT_JSON"] = str(LOCAL / "transport.json")
86-        cmd = [sys.executable, "-m", mod, op] + [str(x) for x in args]
87-    else:
88-        prog = cfg.get(op)
89-        if not prog:
90-            return {"code": -1, "data": {}, "raw": f"未配置 {op} 适配器", "kind": "not_configured"}
91-        cmd = [prog] + [str(x) for x in args]
92-    t = timeout or cfg.get("timeout_s", 120)
93-    env["WF_ADAPTER_TIMEOUT_S"] = str(int(t))   # 1.5.1：告诉适配器时间上限，约定它在上限前 30 秒自行返回
94-    from . import fidelity
95-    try:
96-        rc, so, se, timed_out = fidelity.run_with_grace(cmd, t, env, grace=int(cfg.get("grace_s", 15)))
97-    except FileNotFoundError:
98-        return {"code": -1, "data": {}, "raw": f"适配器不存在：{cmd[0]}", "kind": "not_configured"}
99-    if timed_out:
100-        # 1.5.1：先 SIGTERM、宽限期后才 SIGKILL，适配器有机会补写日志（1.4.x 直接 SIGKILL，日志断在半空）
101-        return {"code": 4, "data": {}, "raw": f"适配器 {op} 超过 {t} 秒未返回（已先发 SIGTERM）", "kind": "timeout"}
102-    r = subprocess.CompletedProcess(cmd, rc, so, se)
103-    raw = (r.stdout or "").strip()
104-    last = raw.splitlines()[-1] if raw else ""
105-    try:
exit=0  耗时 0.0s
```

3) ops.py 第 30–95 行（mark_sent、mark_replied 写 transcript 的位置与格式）。 —— 通过
```
$ sed -n 30,95p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/ops.py
    if st["state"] == "COMPOSE":
        if message_file:
            res = check_outgoing(Path(message_file), kind or "task")
            if res["secrets"]:
                raise WfError("消息中疑似含有密钥，禁止记为已发送（CP-1）：\n  " + "\n  ".join(res["secrets"]))
        machine.go(inst, "SEND", by=by)
    if inst.state["state"] != "SEND":
        raise WfError(f"当前是 {inst.state['state']}，只有 COMPOSE 或 SEND 状态可以记为已发送。")
    usage = {}
    if usage_session is not None:
        usage["session"] = usage_session
    if usage_weekly is not None:
        usage["weekly"] = usage_weekly
    usage = usage or inst.usage_snapshot()
    turn = inst.state["turn"]
    text = Path(message_file).read_text(encoding="utf-8") if message_file else None
    if text is not None:
        append_jsonl(inst.transcript_path, {"ts": now_iso(), "role": "executor", "turn": turn, "text": text})
        d = inst.turn_dir()
        (d / "message.md").write_text(text, encoding="utf-8")
    append_jsonl(inst.turns_path, {"turn": turn, "ts_sent": now_iso(), "from": by, "kind": kind, "role": role,
                                   "effort": effort, "model": model, "effort_reasons": reasons,
                                   "usage_before": usage, "status": "sent",
                                   **({} if usage else {"usage_note": fidelity.usage_reason(inst.usage_path)})})
    inst.state["sent_at"] = now_iso()
    if ref is not None:
        inst.state["send_ref"] = ref   # 手动发送时登记 ref，驱动器据此可接续本轮（缺口 7）
    _conv_add(inst, len(text or ""))
    inst.state["conv"]["turns"] = int(inst.state["conv"].get("turns", 0)) + 1
    machine.go(inst, "WAIT_REPLY", by=by)
    return inst.state


def mark_replied(inst: Instance, reply_file=None, tools=None, usage_session=None, usage_weekly=None,
                 status="done", by="executor") -> dict:
    st = inst.state
    if st["state"] != "WAIT_REPLY":
        raise WfError(f"当前是 {st['state']}，只有 WAIT_REPLY 状态可以记为已收到回复。")
    usage = {}
    if usage_session is not None:
        usage["session"] = usage_session
    if usage_weekly is not None:
        usage["weekly"] = usage_weekly
    usage = usage or inst.usage_snapshot()
    dur = secs_since(st.get("sent_at"))
    turn = st["turn"]
    if reply_file:
        text = Path(reply_file).read_text(encoding="utf-8")
        rec = {"ts": now_iso(), "role": "claude", "turn": turn, "text": text}
        if tools:
            rec["tools"] = tools
        append_jsonl(inst.transcript_path, rec)
        (inst.turn_dir() / "reply.md").write_text(text, encoding="utf-8")
        _conv_add(inst, len(text))
    append_jsonl(inst.turns_path, {"turn": turn, "ts_done": now_iso(), "usage_after": usage,
                                   "duration_s": int(dur) if dur is not None else None, "status": status,
                                   **({} if usage else {"usage_note": fidelity.usage_reason(inst.usage_path)})})
    machine.go(inst, "INTAKE", by=by)
    return inst.state


# ---------------- 执行与取证 ----------------

def run_exec(inst: Instance, cmd: str, label=None, timeout=None, by="executor") -> int:
    st = inst.state
    if st["state"] in ("REVIEW", "APPLY"):
exit=0  耗时 0.0s
```

4) wf.py 第 100–125 行（_record_user）。 —— 通过
```
$ sed -n 100,125p /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
        print(f"  - {x}")
    print(f"可进入：{'、'.join(s['next'])}")
    if s["stuck"] or s["attempts"] or s["state"] == "BLOCKED":
        print("\n本步已知异常与处置：")
        for p in s["problems"]:
            print(f"  [{p['id']}] {p['symptom']} → 判定：{p['judge']} → 处置：{p['action']}" + (f"（台账 {p['ledger']}）" if p.get("ledger") else ""))


def _record_user(inst, kind, item, said):
    """逐字记录用户原话与决定码，写入事件流与决策日志（用户在看板与审计中可以核对）。"""
    from wflib import roles
    said = (said or "").strip()
    if not said:
        raise WfError("需要 --said：用户在聊天窗口里的原话（逐字）")
    inst.event("user_decision", kind=kind, code=item["code"], said=said[:500], title=item["title"], by="user")
    roles.decide(inst, "user", f"{item['title']}（决定码 {item['code']}）", f"用户原话：{said[:300]}", "", "已执行", by="user")


def tool_now():
    from wflib.core import now_iso
    return now_iso()


def _print_explain(e):
    print(f"\n现在：{e['where']}\n原因：{e['why']}\n需要：{e['who']} —— {e['what']}")
exit=0  耗时 0.0s
```

5) server.py 的函数索引。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n def /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/server.py
29:def _instance(name):
36:def _summary(p):
49:def api_projects():
55:def api_flow(name):
95:def _asks(inst):
103:def _next_wake(inst):
116:def _tools_progress(inst):
121:            r = rows.setdefault(m.get("turn"), {"turn": m.get("turn"), "claude": {}, "cmds": 0, "failed": 0, "auto": 0})
126:            r = rows.setdefault(e.get("turn"), {"turn": e.get("turn"), "claude": {}, "cmds": 0, "failed": 0, "auto": 0})
136:def _agents(inst, s):
189:def _inflight(inst):
203:def _parse_changelog(text):
214:def _latest_per_turn(rows):
224:def _effort_stats(turns):
230:        a = agg.setdefault(e, {"turns": 0, "pct": [], "dur": [], "per_min": []})
246:def _hook(inst):
259:def _summ(m, n=80):
264:def api_project(name):
275:    def last(role):
314:def _mtime(p):
320:    def __init__(self, *a, **kw):
323:    def _json(self, obj, code=200):
332:    def _text(self, code, msg):
340:    def _file(self, path: Path):
350:    def do_GET(self):
357:            if path == "/api/definition":
358:                return self._json(machine.definition())
387:    def log_message(self, *a):
391:def serve_foreground(port: int):
399:    def _term(*_a):
410:def _running():
420:def daemon(action: str, port: int) -> str:
449:def info() -> dict:
458:def restart_if_running() -> str:
exit=0  耗时 0.0s
```

6) server.py 的路由。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n api/ /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/server.py
355:            if path == "/api/projects":
357:            if path == "/api/definition":
359:            if path == "/api/flow":
361:            if path == "/api/project":
363:            if path == "/api/doctor":
exit=0  耗时 0.0s
```

7) server.py 的监听地址。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n -A 3 HTTPServer /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/server.py
13:from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
14-from pathlib import Path
15-from urllib.parse import parse_qs, unquote, urlparse
16-
--
392:    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
393-    try:
394-        from .core import write_json
395-        write_json(SERVE_INFO, {"pid": os.getpid(), "port": port, "version": SERVER_VERSION, "started": now_iso()})
exit=0  耗时 0.0s
```

8) server.py 怎样提供 dashboard 文件。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n dashboard /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/server.py
321:        super().__init__(*a, directory=str(WF_HOME / "dashboard"), **kw)
exit=0  耗时 0.0s
```

9) server.py 中 transcript 的读取位置。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n transcript /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/server.py
119:    for m in read_jsonl(inst.transcript_path, limit=400):
153:    tr = read_jsonl(inst.transcript_path, limit=400)
270:    transcript = read_jsonl(inst.transcript_path, limit=300)
276:        return next((m for m in reversed(transcript) if m.get("role") == role), None)
309:        "git": gl, "actors": actors, "hook": hook, "transcript": transcript, "turns": turns[-50:],
exit=0  耗时 0.0s
```

10) flow.html 的取数调用。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n api/ /Users/admin/.workbuddy-ai/tools/collab-wf/dashboard/flow.html
408:  const r = await (await fetch("/api/projects", {cache: "no-store"})).json();
420:    const r = await fetch(`/api/flow?p=${encodeURIComponent(PROJ)}`, {cache: "no-store"});
434:    DEF = await (await fetch("/api/definition", {cache: "no-store"})).json();
exit=0  耗时 0.0s
```

11) flow.html 中一个现有板块的写法（以"需要注意"为例）。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n -A 14 需要注意 /Users/admin/.workbuddy-ai/tools/collab-wf/dashboard/flow.html
140:    <section><h2>需要注意 <small id="attnmeta">本轮</small></h2><div class="card" id="attn"></div></section>
141-    <section><h2>决策日志 <small>做了什么、为什么、证据、结果</small></h2><div class="card tw" id="decs"></div></section>
142-  </div>
143-
144-  <section><h2>各步骤统计 <small>来自事件记录，只含实测值</small></h2><div class="card tw" id="stats"></div></section>
145-</div>
146-
147-<script>
148-const $ = id => document.getElementById(id);
149-const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
150-const COLORS = {COMPOSE:"#6C8EBF", SEND:"#4F7CAC", WAIT_REPLY:"#E8A317", INTAKE:"#7FA37A", REVIEW:"#A7884F",
151-  APPLY:"#8C6BB1", EXECUTE:"#3E8E7E", VERIFY:"#5C9E5C", IDLE:"#AEB8C1", WAIT_SCHEDULE:"#C5CDD4",
152-  AWAITING_USER:"#D9925B", QUOTA_WAIT:"#B9A6C9", BLOCKED:"#C2392F"};
153-const POS = {COMPOSE:[160,70], SEND:[390,70], WAIT_REPLY:[620,70], INTAKE:[850,70],
154-  REVIEW:[850,210], APPLY:[620,210], EXECUTE:[390,210], VERIFY:[160,210]};
--
366:  $("attn").innerHTML = a.length ? `<ul class="plain">${a.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p class="empty">本轮没有需要注意的事项。</p>`;
367-  const d = (FLOW.decisions || []).slice().reverse();
368-  $("decs").innerHTML = d.length ? `<table><tr><th>时间</th><th>阶段</th><th>决定与理由</th><th>结果</th></tr>${d.map(r => `<tr>
369-    <td style="white-space:nowrap">${esc(clock(r.ts))}<br><span class="meta">第 ${esc(r.turn)} 轮</span></td><td>${esc(r.phase)}</td>
370-    <td>${esc(r.decision)}<br><span class="meta">${esc(r.why)}${r.evidence ? "；证据：" + esc(r.evidence) : ""}</span></td>
371-    <td>${esc(r.result || "")}</td></tr>`).join("")}</table>` : `<p class="empty">还没有决策记录。</p>`;
372-}
373-
374-function renderAsks() {
375-  const a = FLOW.asks || {items: [], notices: []};
376-  const items = a.items || [], notes = a.notices || [];
377-  $("askwrap").hidden = !(items.length || notes.length);
378-  $("asks").innerHTML = items.map(it => `<div class="ask">
379-      <h3>${esc(it.title)}　<span class="code">${esc(it.code)}</span></h3>
380-      <p style="margin:0 0 4px">${esc(it.why)}</p>
exit=0  耗时 0.0s
```

12) flow.html 的 body 位置。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n body /Users/admin/.workbuddy-ai/tools/collab-wf/dashboard/flow.html
18:html, body { margin: 0; background: var(--panel); color: var(--ink); font: 15px/1.6 var(--font); font-variant-numeric: tabular-nums; }
102:<body>
448:</body>
exit=0  耗时 0.0s
```

13) flow.html 的行数。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ wc -l /Users/admin/.workbuddy-ai/tools/collab-wf/dashboard/flow.html
     449 /Users/admin/.workbuddy-ai/tools/collab-wf/dashboard/flow.html
exit=0  耗时 0.0s
```

14) project.html 第 130–215 行（现有的对话记录渲染）。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ sed -n 130,215p /Users/admin/.workbuddy-ai/tools/collab-wf/dashboard/project.html

  <div class="grid">
    <div>
      <section><h2>下一步 <small id="stepcount"></small></h2><div class="panel"><div class="progress"><i id="stepbar" style="width:0"></i></div><ul class="steps" id="steps"></ul></div></section>
      <section id="hooksec"><h2 id="hooktitle">项目进程</h2><div class="panel" id="hook"></div></section>
      <section><h2>额度与档位 <small id="usageat"></small></h2><div class="panel" id="usage"></div></section>
    </div>
    <div>
      <section>
        <h2>对话记录 <div class="filters" id="filters" role="group" aria-label="按角色筛选">
          <button data-f="all" aria-pressed="true">全部</button><button data-f="user" aria-pressed="false">用户</button>
          <button data-f="claude" aria-pressed="false">Claude</button><button data-f="executor" aria-pressed="false">执行端</button></div></h2>
        <div class="talk" id="talk"></div>
      </section>
    </div>
  </div>

  <section><h2>轮次明细 <small>来自 workflow/turns.jsonl，最新的在上</small></h2><div class="tablewrap" id="turns"></div></section>

  <section><h2>失败模式台账 <small>来自 PROJECT.md</small></h2><div class="tablewrap" id="fails"></div></section>

  <div class="grid">
    <section><h2>版本记录</h2><div class="panel cl" id="versions"></div></section>
    <section><h2>文档与结果 <small>点击在新标签页打开</small></h2><div class="panel"><ul class="docs" id="docs"></ul>
      <details><summary>PROJECT.md 全部章节</summary><div id="pmd"></div></details></div></section>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const ROLE = {user: "用户", claude: "Claude", executor: "执行端"};
let filter = "all", last = null;
const P = new URLSearchParams(location.search).get("p") || "";
document.getElementById("flowlink").href = `/?p=${encodeURIComponent(P)}`;

function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso)) / 1000;
  if (isNaN(s)) return "";
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
}
const fmtDur = s => s == null ? "—" : s < 60 ? `${Math.round(s)} 秒` : `${Math.floor(s / 60)} 分 ${String(Math.round(s % 60)).padStart(2, "0")} 秒`;
const clock = iso => iso ? new Date(iso).toLocaleString("zh-CN", {hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"}) : "";

function spark(points) {
  if (!points || points.length < 2) return "";
  const v = points.map(p => p[1]), lo = Math.min(...v), hi = Math.max(...v), r = hi - lo || 1;
  const d = v.map((y, i) => `${(i / (v.length - 1) * 300).toFixed(1)},${(56 - (y - lo) / r * 50).toFixed(1)}`).join(" ");
  const up = v[v.length - 1] >= v[0];
  return `<svg class="spark" viewBox="0 0 300 60" preserveAspectRatio="none" role="img" aria-label="权益曲线，最低 ${lo.toFixed(2)}，最高 ${hi.toFixed(2)}">
    <polyline points="${d}" fill="none" stroke="var(${up ? "--ok" : "--bad"})" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
}

function statusTag(s) {
  const t = String(s || "");
  const cls = /已解决|已规避/.test(t) ? "t-ok" : /待用户|观察|处理/.test(t) ? "t-warn" : /未解决|失败/.test(t) ? "t-bad" : "t-off";
  return `<span class="tag ${cls}">${esc(t)}</span>`;
}

function render(st) {
  last = st;
  $("pname").textContent = st.project.name;
  $("pver").textContent = st.project.version || "版本未知";
  $("pphase").textContent = st.project.phase ? `阶段：${st.project.phase}` : "";
  $("meta").textContent = `${st.project.name}，更新于 ${new Date(st.generated_at).toLocaleTimeString("zh-CN", {hour12: false})}，每 20 秒自动刷新`;
  $("issues").innerHTML = st.project_md_issues.length
    ? `<div class="issues">PROJECT.md 不符合规范：${st.project_md_issues.map(esc).join("；")}</div>` : "";

  const open = st.decisions.filter(d => !d.done);
  $("ask").innerHTML = open.length
    ? `<h2>需要你决定（${open.length}）</h2><ol>${open.map(d => `<li>${esc(d.text)}</li>`).join("")}</ol>`
    : `<div class="none">目前没有需要你决定的事</div>`;

  $("strips").innerHTML = st.actors.map(a => `<div class="strip" data-s="${esc(a.status)}">
    <div class="bar"></div><div class="who">${esc(a.name)}</div><div class="what">${esc(a.state)}</div>
    <div class="when" title="${esc(clock(a.at))}">${esc(ago(a.at))}</div></div>`).join("");

  const steps = st.next_steps, done = steps.filter(s => s.done).length, nowIdx = steps.findIndex(s => !s.done);
  $("stepcount").textContent = `${done} / ${steps.length} 完成`;
  $("stepbar").style.width = steps.length ? `${done / steps.length * 100}%` : "0";
  $("steps").innerHTML = steps.map((s, i) => `<li class="${s.done ? "done" : i === nowIdx ? "now" : ""}">
    <span>${s.done ? "✓" : i === nowIdx ? "▸" : "○"}</span><span>${esc(s.text)}</span></li>`).join("");
exit=0  耗时 0.0s
```

③ 需要决策的点
无。

④ 需要注意（Attention）
- 自动处置 OUTGOING_STRUCTURE → block：缺少标题行：【回报 · 第 N 轮 · vX.Y】
- 清单第 2, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14 项没有机器判据，结论需要 Claude 根据原文判断
- 当前对话已累计约 215083 字符、11 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

附：仓库指纹 5534b5c98537025e（v0.2，4 个文件）
附：发送前额度 5 小时窗口 49.0%，7 天窗口 61.0%
