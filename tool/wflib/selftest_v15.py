"""1.5.0 新增自检：解析容错、证据关联与续传、回复归属、执行接续、桥接日志、额度与事件序号、升级核验。
都是纯函数或临时目录内的文件操作，不启动子进程，几秒内完成。由 selftest.run() 开头调用，结果计入总数。
其中几条用真实发生过的情形做回归样例（见各条名称）。"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from . import fidelity, ops

FENCE = "`" * 3

REPLY_MENTION = (
    "【本轮结论】\n正文里顺带提到【本机执行】，不应被当成块标题。\n\n"
    "【本机执行】\n" + FENCE + "bash\necho a\necho b\n" + FENCE + "\n\n"
    "【本轮回报清单】\n1. c1 的原文\n2. c2 的原文\n\n"
    "【需要用户决定】\n无\n\n"
    "【下一轮建议档位】\nhigh\n\n"
    "【驱动指令】\n发布：无\n之后：回报\n说明：命令只写在【本机执行】里。\n"
)

REPLY_DUP_LINE = REPLY_MENTION.replace("说明：命令只写在【本机执行】里。", "【本机执行】这一行在行首重复出现，应被忽略。")

REPLY_INLINE = "【本轮结论】结论【本机执行】无【本轮回报清单】无【需要用户决定】无【下一轮建议档位】low"


def _t_blocks(_ok):
    r = ops.parse_reply(REPLY_MENTION)
    _ok("解析：正文中出现的块名不顶掉真正的命令块（1.4.x 会丢失全部命令）",
        r["commands"] == ["echo a", "echo b"] and not r["blocks_missing"], str(r["commands"]))
    _ok("解析：块名出现在驱动指令之后时，驱动指令仍完整", (r["directives"] or {}).get("after") == "report",
        str(r["directives"]))
    r2 = ops.parse_reply(REPLY_DUP_LINE)
    _ok("解析：行首重复的块标题被忽略并给出提示",
        r2["commands"] == ["echo a", "echo b"] and any("另有出现" in w for w in r2.get("warnings", [])),
        str(r2.get("warnings")))
    r3 = ops.parse_reply(REPLY_INLINE)
    _ok("解析：块标题都在行中间时退回旧规则（兼容）",
        not r3["blocks_missing"] and r3["effort"] == "low" and r3["no_exec"], str(r3["blocks_missing"]))


def _t_directives(_ok):
    d = ops.parse_directives("1. 本轮之后：回报\n2. 验收 1：c1 exit=0\n- **超时**：60\n")
    _ok("驱动指令：容忍行首编号、“本轮之后”与加粗（第 29–31 轮三次失效的格式）",
        d["after"] == "report" and "1" in d["checks"] and d["timeout"] == 60.0, str(d))
    d2 = ops.parse_directives("本轮之后照常回报，计划节点不变。\n")
    _ok("驱动指令：有内容却解析不出任何键时报错（不再静默）",
        any("没有解析出任何键" in e for e in d2["errors"]), str(d2["errors"]))
    d3 = ops.parse_directives("发布：无\n之后：空闲\n")
    _ok("驱动指令：标准格式不受影响", d3["after"] == "idle" and not d3["errors"], str(d3))


def _t_lists(_ok):
    _ok("清单编号连续时没有提示", fidelity.list_warnings("1. a\n2. b\n", "本轮回报清单") == [])
    _ok("清单编号不连续时提示（嵌套编号或缺项）", bool(fidelity.list_warnings("1. a\n2. b\n1. c\n", "本轮回报清单")))


def _t_link(_ok):
    ex = [{"id": "c01", "label": "#1", "cmd": "cat /x/wflib/ops.py"},
          {"id": "c02", "label": "#2", "cmd": "git log --oneline -3"}]
    _ok("证据关联：文字中的 cN", fidelity.link_evidence(1, "c2 的原文", ex, 5) == ["c02"])
    _ok("证据关联：反引号中的完整命令", fidelity.link_evidence(1, "`git log --oneline -3` 的原文", ex, 5) == ["c02"])
    _ok("证据关联：反引号中用 ... 省略的命令", fidelity.link_evidence(1, "`cat .../ops.py` 全文", ex, 5) == ["c01"])
    _ok("证据关联：项数与命令数相同时按序号对应", fidelity.link_evidence(2, "第二项", ex, 2) == ["c02"])
    _ok("证据关联：对不上时不乱关联", fidelity.link_evidence(2, "第二项 abc12", ex, 3) == [])


def _t_report(_ok):
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "c01.out").write_text("hello-unique-marker\n", encoding="utf-8")
        big = "".join(f"line {i:05d} " + "x" * 90 + "\n" for i in range(500))   # 约 5 万字符
        (d / "c02.out").write_text(big, encoding="utf-8")
        (d / "c03.out").write_text("before\n" + FENCE + "python\nprint(1)\n" + FENCE + "\nafter\n", encoding="utf-8")
        execs = [{"id": f"c0{k}", "label": f"#{k}", "cmd": f"cmd{k}", "exit_code": 0, "duration_s": 0.0} for k in (1, 2, 3)]
        items = [{"n": 1, "item": "c1 原文", "verdict": "skip"}, {"n": 2, "item": "c1 再看一次", "verdict": "pass"},
                 {"n": 3, "item": "c2 长输出", "verdict": "skip"}]
        carry = d / "outbox" / "carry.json"
        L = fidelity.report_evidence(items, execs, d, carry, ops.VERDICTS, 7)
        text = "\n".join(L)
        _ok("回报：短原文完整嵌入且只出现一次（第 31 轮出现过重复尾部）", text.count("hello-unique-marker") == 1, text[:300])
        _ok("回报：同一证据第二次引用时不重复嵌入", "c01 的原文已在上文给出" in text)
        _ok("回报：未关联的命令也附原文（第 32–33 轮回报零原文）", "其他命令（未关联到清单项）" in text and "print(1)" in text)
        _ok("回报：原文里含三个反引号时外层围栏自动加长", "\n" + "`" * 4 + "\n" in text)
        _ok("回报：超出预算的长原文先发第 1 片，其余排入续传",
            "第 1 片" in text and len(fidelity.load_carry(carry)) == 1 and len(text) < fidelity.REPORT_BUDGET + 3000,
            f"len={len(text)} carry={len(fidelity.load_carry(carry))}")
        L2 = fidelity.report_evidence([], [], d, carry, ops.VERDICTS, 8)
        t2 = "\n".join(L2)
        joined = "".join(q for q in [text, t2])
        _ok("回报：下一份回报自动补发续传，队列清空", "第 2/2 片" in t2 and fidelity.load_carry(carry) == [], t2[:200])
        _ok("回报：分片拼接后与原文一致", "line 00000" in joined and "line 00499" in joined)


def _t_chunks(_ok):
    s = "short\n" * 10 + "y" * 250 + "\n" + "tail\n"
    ch = fidelity.split_chunks(s, size=100)
    _ok("分片：拼接等于原文，且每片不超过上限", "".join(ch) == s and all(len(c) <= 100 for c in ch), str([len(c) for c in ch]))


def _t_attr(_ok):
    _ok("回复归属：父消息与本轮一致时通过", fidelity.reply_attribution({"reply_parent": "m2"}, "m2")[0] == "ok")
    _ok("回复归属：父消息不是本轮消息时判为旧回复（第 30 轮取回了上一条回复）",
        fidelity.reply_attribution({"reply_parent": "m1"}, "m2")[0] == "stale")
    _ok("回复归属：适配器不提供时记为未核实，行为不变", fidelity.reply_attribution({}, "m2")[0] == "unverified")


def _t_exec(_ok):
    done = [{"id": "c03", "label": "#3", "exit_code": 0, "end": "2026-10-10T03:48:01+00:00"}]
    _ok("执行接续：命令已有退出码时不算中断（第 31 轮误报）", (fidelity.exec_completed(done, 3) or {}).get("id") == "c03")
    _ok("执行接续：没有退出码时仍按中断处理", fidelity.exec_completed([{"id": "c03", "label": "#3"}], 3) is None)


def _t_bridge(_ok):
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "bridge.jsonl"
        acts = ["send", "rejected", "retry", "send", "accepted", "generating", "poll", "done"]
        rows = [{"ts": f"2026-10-10T05:00:{k:02d}+00:00", "action": a} for k, a in enumerate(acts)]
        log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        res = {r["rule"].split()[0]: r for r in ops.bridge_check(log)}
        _ok("桥接自检：poll 是合法动作（R6 曾把它误报为无法解析）", res["R6"]["ok"], res["R6"]["msg"])
        _ok("桥接自检：被拒后重发不算重叠发送（R1 曾因此误报）", res["R1"]["ok"], res["R1"]["msg"])
        # 1.6.8：R3 的飞行宽限（时钟、终态、新的发送、过滤窗口的边界）
        from datetime import datetime, timedelta, timezone
        t0 = datetime(2026, 10, 10, 5, 0, 0, tzinfo=timezone.utc)

        def r3(rows, at, **kw):
            log.write_text("\n".join(json.dumps({"ts": (t0 + timedelta(seconds=s)).isoformat(), "action": a}) for s, a in rows) + "\n",
                           encoding="utf-8")
            return {r["rule"].split()[0]: r for r in ops.bridge_check(log, at=t0 + timedelta(seconds=at), **kw)}["R3"]
        fly = [(0, "send"), (30, "poll"), (40, "quota")]
        a = r3(fly, 60)
        _ok("R3 宽限：发送后只有 poll、quota、还在宽限内（60 秒）→ 不报，并写明在等待", a["ok"] and "宽限内等待结果" in a["msg"], a["msg"])
        _ok("R3 宽限：恰好等于宽限（900 秒）不报，超过 1 秒（901 秒）照常报", r3(fly, 900)["ok"] and not r3(fly, 901)["ok"])
        _ok("R3 宽限：只有一条发送且已过期（1 小时）照常报（此前作为最后一条的发送永远不报）", not r3([(0, "send")], 3600)["ok"])
        _ok("R3 终态：有接受、拒绝、错误或中断记录时，过多久都不报",
            all(r3([(0, "send"), (5, "poll"), (9, x)], 86400)["ok"] for x in ("accepted", "rejected", "error", "aborted")))
        b = r3([(0, "send"), (5, "poll"), (10, "send"), (12, "accepted")], 20)
        _ok("R3 新的发送：结果出现前又有新的发送，在宽限内也照常报第一条", not b["ok"] and "第 1 行" in b["msg"], b["msg"])
        _ok("R3 宽限可调：--grace-s 0 时 60 秒前的在途发送照常报", not r3(fly, 60, grace_s=0)["ok"])
        _ok("R3 过滤窗口不是宽限：窗口外的旧发送不检查；窗口内超过宽限的照常报",
            r3([(0, "send"), (5, "poll")], 7200, hours=1)["ok"] and not r3([(0, "send"), (5, "poll")], 7200, hours=3)["ok"]
            and not r3([(0, "send"), (5, "poll")], 1800, hours=1)["ok"])
        _ok("R3 时钟：发送时间晚于判定时刻（时钟偏差）按在途处理、不报", r3([(120, "send")], 0)["ok"])


def _t_usage_seq(_ok):
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _ok("额度：没有 usage.json 时写明原因", "不存在" in fidelity.usage_reason(d / "usage.json"))
        p = d / "usage.json"
        p.write_text("{}", encoding="utf-8")
        old = time.time() - 3600
        os.utime(p, (old, old))
        _ok("额度：过期时写明分钟数", "分钟未更新" in fidelity.usage_reason(p))
        t = d / "turns.jsonl"
        t.write_text("\n".join(json.dumps(x) for x in [
            {"turn": 1, "usage_before": {"session": 3}}, {"turn": 2, "usage_before": None},
            {"turn": 2, "usage_after": None}, {"turn": 3, "usage_before": None}]) + "\n", encoding="utf-8")
        _ok("额度：连续空值计数", fidelity.usage_null_streak(t) == 3)
        ev = d / "events.jsonl"
        ev.write_text("\n".join(json.dumps({"seq": s}) for s in (1, 2, 2, 4)) + "\nnot json\n", encoding="utf-8")
        s = fidelity.seq_check(ev)
        _ok("事件序号：发现重复、跳号与坏行", s["dup"] == 1 and s["gaps"] == 1 and s["bad_lines"] == 1, str(s))


def _t_receipt(_ok):
    with tempfile.TemporaryDirectory() as td:
        h = Path(td)
        (h / "VERSION").write_text("1.5.0\n", encoding="utf-8")
        r = fidelity.post_install_receipt("1.5.0", {"a.py": "h1"}, {"a.py": "h1", "old.py": "h9"}, False, dict, h)
        _ok("升级核验：一致时通过，残留文件只提示", r["ok"] and r["notes"], str(r))
        r2 = fidelity.post_install_receipt("1.5.0", {"a.py": "h1"}, {"a.py": "h2"}, False, dict, h)
        _ok("升级核验：文件与版本包不一致时不通过", not r2["ok"])
        r3 = fidelity.post_install_receipt("1.5.0", {"a.py": "h1"}, {"a.py": "h1"}, True,
                                           lambda: {"running": True, "version": "1.4.3"}, h, wait_s=0, sleep=lambda s: None)
        _ok("升级核验：看板仍跑旧版本时不通过（F25）", not r3["ok"] and "看板" in "".join(r3["problems"]), str(r3["problems"]))
        r4 = fidelity.post_install_receipt("1.5.0", {}, {}, False, dict, h / "nowhere")
        _ok("升级核验：VERSION 不对时不通过", not r4["ok"])


def _t_tree(_ok):
    with tempfile.TemporaryDirectory() as td:
        h = Path(td)
        for rel in ("a.py", "MANIFEST.json", "RELEASE.json", "local/x", ".git/y", "pkg/__pycache__/z.pyc", "pkg/b.py"):
            (h / rel).parent.mkdir(parents=True, exist_ok=True)
            (h / rel).write_text(rel, encoding="utf-8")
        _ok("工具文件口径：排除清单、本机数据与缓存", sorted(fidelity.tree_hashes(h)) == ["a.py", "pkg/b.py"],
            str(sorted(fidelity.tree_hashes(h))))


def _t_v151(_ok):
    ex = [{"id": "c01", "label": "#2", "cmd": "wf version"}, {"id": "c02", "label": "#3", "cmd": "wf doctor"}]
    _ok("1.5.1 证据关联：第 1 条被跳过时，c1 不会错配到 c01（第 44 轮）", fidelity.link_evidence(1, "c1 的原文", ex, 9) == [])
    _ok("1.5.1 证据关联：其余按 label 正确对应", fidelity.link_evidence(3, "c3 的原文", ex, 9) == ["c02"])
    _ok("1.5.1 发送结果不明：默认照旧阻塞；policy 开启 send_unknown_wait 后改为自动等待",
        fidelity.send_unknown_kind({}) == "send_outcome_unknown"
        and fidelity.send_unknown_kind({"send_unknown_wait": True}) == "send_outcome_unknown_wait")
    from .core import WF_HOME
    rules = {r.get("id"): r for r in (json.loads((WF_HOME / "anomalies.json").read_text(encoding="utf-8")).get("rules") or [])}
    w = rules.get("SEND_UNKNOWN_WAIT") or {}
    _ok("1.5.1 异常字典：SEND_UNKNOWN_WAIT 退避重查约一小时、不重发，之后阻塞",
        w.get("action") == "retry" and sum(w.get("backoff_s", [])) >= 3000 and w.get("then") == "block", str(w)[:200])


def _t_grace(_ok):
    import sys
    with tempfile.TemporaryDirectory() as td:
        mark = Path(td) / "term.txt"
        script = Path(td) / "slow.py"
        script.write_text("import signal, sys, time, pathlib\n"
                          "def h(*a):\n    pathlib.Path(sys.argv[1]).write_text('term')\n    sys.exit(0)\n"
                          "signal.signal(signal.SIGTERM, h)\nprint('started', flush=True)\ntime.sleep(30)\n", encoding="utf-8")
        t0 = time.time()
        rc, out, err, timed = fidelity.run_with_grace([sys.executable, str(script), str(mark)], 2, None, grace=5)
        _ok("1.5.1 适配器超时：先 SIGTERM，适配器能在宽限期内收尾（1.4.x 直接 SIGKILL）",
            timed and mark.exists() and time.time() - t0 < 10, f"timed={timed} mark={mark.exists()} rc={rc}")


def _t_readonly(_ok):
    from . import policy
    _ok("1.5.1 wf self-check 是只读命令，驱动器不再当作手动步骤（第 44 轮）",
        not (policy.classify("wf self-check", {}) or {}).get("manual"), str(policy.classify("wf self-check", {})))


def _t_note(_ok):
    import contextlib
    import io
    from .core import Instance
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "workflow").mkdir()
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": "n"}), encoding="utf-8")
        inst = Instance(root)
        inst.state.update(state="WAIT_REPLY", turn=5)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ops.add_note(inst, "decision", "回报生成之后写的备注")
        _ok("1.5.1 wf note：回报生成之后写备注时，说明它会随下一份回报发出", "下一份回报" in buf.getvalue(), buf.getvalue())


def _t_resend(_ok):
    import subprocess
    import sys
    from .core import WF_HOME
    with tempfile.TemporaryDirectory() as td:
        env = {**os.environ, "WF_LOCAL_DIR": str(Path(td) / "local")}
        env.pop("VIRTUAL_ENV", None)

        def wf(cwd, *a):
            return subprocess.run([sys.executable, str(WF_HOME / "wf.py"), *a], cwd=str(cwd), env=env,
                                  capture_output=True, text=True, timeout=60)
        proj = Path(td) / "rs"
        wf(td, "init", "--name", "rs", "--root", str(proj))
        stp = proj / "workflow" / "state.json"
        st = json.loads(stp.read_text(encoding="utf-8"))
        st.update(state="BLOCKED", turn=3, block_reason="测试：发送结果不明", blocked_from="SEND", return_to="SEND",
                  send_intent={"turn": 3, "status": "pending", "ts": "2026-10-10T05:16:07+00:00", "file": "x.md"})
        stp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        r = wf(proj, "--json", "ask")
        o = r.stdout
        try:
            items = json.loads(o[o.index("{"):])["items"]
        except (ValueError, KeyError):
            items = []
        code = next((it["code"] for it in items if it["kind"] == "unblock" or str(it["kind"]).startswith("unblock:")), "NONE")
        r = wf(proj, "resend", "--code", code, "--said", "测试：确认未送达，需要重发")
        st2 = json.loads(stp.read_text(encoding="utf-8"))
        _ok("1.5.1 wf resend：凭决定码与用户原话清除结果不明的发送意图并回到发送（不再手改 state.json）",
            r.returncode == 0 and st2.get("send_intent") is None and st2.get("state") == "SEND", (r.stdout + r.stderr)[-300:])
        r = wf(proj, "resend", "--code", code, "--said", "再来一次")
        _ok("1.5.1 wf resend：没有结果不明的发送意图时拒绝", r.returncode != 0, (r.stdout + r.stderr)[-200:])


def _t_resume_info(_ok):
    import subprocess
    import sys
    from .core import WF_HOME, Instance
    with tempfile.TemporaryDirectory() as td:
        env = {**os.environ, "WF_LOCAL_DIR": str(Path(td) / "local")}
        env.pop("VIRTUAL_ENV", None)
        proj = Path(td) / "rv"
        subprocess.run([sys.executable, str(WF_HOME / "wf.py"), "init", "--name", "rv", "--root", str(proj)],
                       cwd=td, env=env, capture_output=True, text=True, timeout=60)
        stp = proj / "workflow" / "state.json"
        st = json.loads(stp.read_text(encoding="utf-8"))
        st.update(state="AWAITING_USER", return_to="REVIEW", turn=2)
        stp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run([sys.executable, str(WF_HOME / "wf.py"), "resume", "--note", "测试"], cwd=str(proj), env=env,
                           capture_output=True, text=True, timeout=60)
        st2 = json.loads(stp.read_text(encoding="utf-8"))
        _ok("1.5.1 wf resume 回到评审时提示改用 wf approve（手动流程照常可用；驱动器下会原地打转，第 43 轮）",
            r.returncode == 0 and "wf approve" in (r.stdout + r.stderr) and st2.get("state") == "REVIEW",
            (r.stdout + r.stderr)[-200:])
        root = Path(td) / "ri"
        (root / "workflow").mkdir(parents=True)
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": "ri"}), encoding="utf-8")
        inst = Instance(root)
        inst.state.update(state="VERIFY", turn=1)
        inst.save()
        d = inst.turn_dir()
        (d / "intake.json").write_text(json.dumps({"checklist": [], "effort": "high"}), encoding="utf-8")
        (d / "notes.jsonl").write_text(json.dumps({"ts": "t", "kind": "info", "text": "说明类备注-测试标记"}, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
        text = ops.compose_report(inst, by="driver", effort="high").read_text(encoding="utf-8")
        _ok("1.5.1 说明类备注出现在回报中（此前收下却不呈现）", "说明类备注-测试标记" in text, text[:300])


def _t_v152_state(_ok):
    from .core import Instance
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "workflow").mkdir()
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"v152-state-{os.getpid()}"}), encoding="utf-8")
        a = Instance(root)                      # 驱动器：读入状态后进入长等待
        a.state.update(state="SEND", turn=4)
        a.event("t0")
        b = Instance(root)                      # 手工命令：wf pause
        b.state["paused"] = {"ts": "t", "note": "测试暂停"}
        b.event("pause")
        Instance(root).event("note")            # 手工命令：wf note
        a.event("transition")                   # 驱动器带着过期的内存状态醒来
        seqs = [json.loads(x)["seq"] for x in (root / "workflow" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        disk = json.loads((root / "workflow" / "state.json").read_text(encoding="utf-8"))
        _ok("1.5.2 并发写状态：事件序号不重复且按写入顺序递增（W18，第 1–5 轮三次重号）", seqs == [1, 2, 3, 4], str(seqs))
        _ok("1.5.2 并发写状态：驱动器带着过期状态写回时，不覆盖手工写入的暂停（W18，第 1 轮 wf pause 丢失）",
            bool(disk.get("paused")) and bool(a.state.get("paused")) and disk.get("state") == "SEND", str(disk)[:300])
        c = Instance(root)
        c.state.pop("paused", None)
        c.event("unpause")
        a.state["turn"] = 5
        a.save()
        disk = json.loads((root / "workflow" / "state.json").read_text(encoding="utf-8"))
        _ok("1.5.2 并发写状态：其他进程删除的键不会被写回，本进程改动的键照常写入",
            "paused" not in disk and "paused" not in a.state and disk.get("turn") == 5 and disk.get("seq") == 5,
            str(disk)[:300])


def _t_v152_seq(_ok):
    with tempfile.TemporaryDirectory() as td:
        ev = Path(td) / "events.jsonl"
        ev.write_text("\n".join(json.dumps({"seq": s}) for s in (1, 2, 2, 4, 3)) + "\n", encoding="utf-8")
        s = fidelity.seq_check(ev)
        _ok("1.5.2 事件序号：一个重号只计为重复，不再同时计为倒序", s["dup"] == 1 and s["backwards"] == 1 and s["gaps"] == 1,
            str(s))


def _t_v152_blocking(_ok):
    from . import driver
    items = ["是否继续运行模拟盘：这一项不阻塞", "（前置）批准本项目的白名单", "(前置) 确认交易平台", "说明里出现阻塞二字"]
    got = driver._blocking_decisions(items)
    _ok("1.5.2 需要用户决定：只认显式标记（前置），“不阻塞”等字样不再误停（W15，第 47 轮）", got == items[1:3], str(got))


def _t_v152_carry(_ok):
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        carry = d / "outbox" / "carry.json"
        carry.parent.mkdir(parents=True)
        old = [{"turn": 1, "cid": "c12", "title": "清单第 12 项", "part": 2, "parts": 2, "sha": "abc", "text": "carry-marker\n"}]
        carry.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        (d / "c01.out").write_text("y" * (fidelity.REPORT_BUDGET + 500) + "\n", encoding="utf-8")
        execs = [{"id": "c01", "label": "#1", "cmd": "cmd1", "exit_code": 0, "duration_s": 0.0}]
        items = [{"n": 1, "item": "c1 大输出", "verdict": "skip"}]
        text = "\n".join(fidelity.report_evidence(items, execs, d, carry, ops.VERDICTS, 9))
        q = fidelity.load_carry(carry)
        _ok("1.5.2 续传先于本轮原文发送（W20，第 3 轮续传被本轮证据挤到后面）",
            "carry-marker" in text and "1) c1 大输出" in text and text.index("carry-marker") < text.index("1) c1 大输出")
            and all(x.get("turn") == 9 for x in q), text[:300])
        carry.write_text(json.dumps([dict(old[0], text="z" * (fidelity.REPORT_BUDGET + 100))], ensure_ascii=False),
                         encoding="utf-8")
        t2 = "\n".join(fidelity.report_evidence([], [], d, carry, ops.VERDICTS, 10))
        _ok("1.5.2 续传一片都放不下时不留空标题，原片仍在队列",
            "续传：之前的回报未能容纳的原文" not in t2 and len(fidelity.load_carry(carry)) == 1, t2[:200])


def _t_v152_exec(_ok):
    from . import driver
    from .core import Instance
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "workflow").mkdir()
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"v152-exec-{os.getpid()}"}), encoding="utf-8")
        inst = Instance(root)
        inst.state.update(state="EXECUTE", turn=1)
        inst.save()
        (inst.turn_dir() / "intake.json").write_text(json.dumps({"checklist": [], "effort": "high"}), encoding="utf-8")
        rc = ops.run_exec(inst, "false", label="#1", timeout=30, by="driver")
        d1 = ops.parse_directives("验收 1：c1 exit=1\n")
        d0 = ops.parse_directives("验收 1：c1 exit=0\n")
        _ok("1.5.2 有判据的命令按判据判定：预期无匹配写 exit=1 时不算失败（W21，第 4–5 轮）",
            rc == 1 and driver._exec_ok(inst, d1, "#1", rc) and not driver._exec_ok(inst, d0, "#1", rc), f"rc={rc}")
        _ok("1.5.2 没有判据的命令仍按退出码判定", not driver._exec_ok(inst, {}, "#1", rc))
        f1, f0 = driver._exec_failures(inst, d1), driver._exec_failures(inst, {})
        _ok("1.5.2 判据通过的命令不计入失败；没有判据的失败命令照常计入", f1 == (False, False) and f0[0], f"{f1} {f0}")


def _t_v152_bridge(_ok):
    _ok("1.5.2 桥接动作名含 conv_new（新建对话）", "conv_new" in ops.BRIDGE_ACTIONS)


def _snap153(n):
    return {"schema": "collab-wf.snapshot/1", "captured_at": "2026-10-10T10:00:00+00:00",
            "conversation": {"id": "c1", "name": "测试会话", "model": "m", "updated_at": "t", "current_leaf": f"u{n}"},
            "messages": [{"index": i, "id": f"u{i}", "parent": None, "role": "human" if i % 2 == 0 else "assistant",
                          "created_at": "t", "stop_reason": None, "truncated": False,
                          "blocks": [{"type": "text", "text": f"第 {i} 条\n  原样 <b>&\"'"},
                                     {"type": "thinking", "text": "思考内容"}]} for i in range(n)]}


def _t_v153_page(_ok):
    from . import page
    from .core import Instance
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "workflow").mkdir()
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"v153-page-{os.getpid()}"}), encoding="utf-8")
        inst = Instance(root)
        inst.state.update(state="WAIT_REPLY", turn=3)
        inst.save()
        inc = Path(page.poll_env(inst)[page.ENV])
        _ok("1.5.3 页面快照：适配器没写出快照时不做任何事", page.after_poll(inst, "poll", "generating") is None
            and not page.page_dir(inst).exists())
        raw = json.dumps(_snap153(4), ensure_ascii=False, indent=1)
        inc.write_text(raw, encoding="utf-8")
        r1 = page.after_poll(inst, "poll", "generating")
        d = page.page_dir(inst)
        _ok("1.5.3 页面快照：收下后逐字节原样保存，并删除临时文件",
            bool(r1) and r1["ok"] and r1["changed"] and (d / "latest.json").read_text(encoding="utf-8") == raw
            and not inc.exists(), str(r1))
        inc = Path(page.poll_env(inst)[page.ENV])
        inc.write_text(raw.replace("2026-10-10T10:00:00", "2026-10-10T10:05:00"), encoding="utf-8")
        r2 = page.after_poll(inst, "poll", "done")
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        _ok("1.5.3 页面快照：内容没变时不重写，只记录这次查看（看一次记一次）",
            r2["ok"] and not r2["changed"] and meta.get("looks") == 2 and (d / "latest.json").read_text(encoding="utf-8") == raw
            and len((d / "checks.jsonl").read_text(encoding="utf-8").splitlines()) == 2, str(meta)[:300])
        inc = Path(page.poll_env(inst)[page.ENV])
        inc.write_text('{"schema": "x"}', encoding="utf-8")
        r3 = page.after_poll(inst, "poll", None)
        _ok("1.5.3 页面快照：格式不符时不覆盖已有快照", not r3["ok"] and (d / "latest.json").read_text(encoding="utf-8") == raw,
            str(r3))
        for t in range(4, 9):
            inst.state["turn"] = t
            inst.save()
            inc = Path(page.poll_env(inst)[page.ENV])
            inc.write_text(json.dumps(_snap153(t), ensure_ascii=False), encoding="utf-8")
            page.after_poll(inst, "poll", "done", keep=3)
        names = sorted(p.name for p in d.glob("turn-*.json"))
        _ok("1.5.3 页面快照：按轮只保留最近 N 份", names == ["turn-006.json", "turn-007.json", "turn-008.json"], str(names))
        a, am = page.api_page(inst), page.api_page(inst, meta_only=True)
        _ok("1.5.3 看板接口 /api/page：最新快照、指定轮次、只取元信息",
            a["snapshot"]["conversation"]["current_leaf"] == "u8" and a["turns"] == [6, 7, 8] and "snapshot" not in am
            and page.api_page(inst, turn=7)["snapshot"]["conversation"]["current_leaf"] == "u7", str(a.get("turns")))


def _t_v153_talk(_ok):
    from . import page
    from .core import Instance, append_jsonl
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "workflow").mkdir()
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"v153-talk-{os.getpid()}"}), encoding="utf-8")
        inst = Instance(root)
        inst.state.update(state="IDLE", turn=2)
        inst.save()
        said = "在工作流看板里面，加上快照。\n  原样保留空格、换行 <tag> & “引号”"
        page.record_user(inst, said, kind="chat")
        append_jsonl(inst.transcript_path, {"ts": "t", "role": "executor", "turn": 2, "text": "回报"})
        append_jsonl(inst.transcript_path, {"ts": "t", "role": "claude", "turn": 2, "text": "回复"})
        t = page.api_transcript(inst)
        _ok("1.5.3 对话记录：用户原话逐字写入（role=user，此前“用户”筛选永远为空）",
            t["count"] == 3 and t["entries"][0]["role"] == "user" and t["entries"][0]["text"] == said, str(t["entries"][0])[:200])
        t2, t9 = page.api_transcript(inst, after=1), page.api_transcript(inst, after=9)
        _ok("1.5.3 看板接口 /api/transcript：按序号增量读取；序号超出时整份重给",
            [e["role"] for e in t2["entries"]] == ["executor", "claude"] and t9["reset"] and len(t9["entries"]) == 3)
        evs = [json.loads(x) for x in inst.events_path.read_text(encoding="utf-8").splitlines()]
        _ok("1.5.3 wf said 同时记入事件流", any(e.get("type") == "user_said" for e in evs))


def _t_v153_board(_ok):
    from .core import WF_HOME
    html = (WF_HOME / "dashboard" / "flow.html").read_text(encoding="utf-8")
    srv = (WF_HOME / "wflib" / "server.py").read_text(encoding="utf-8")
    _ok("1.5.3 工作流看板含“Claude 页面快照”“对话记录”两个板块，并接上两个只读接口",
        'id="pagebox"' in html and 'id="talkbox"' in html and "/api/page" in html and "/api/transcript" in html
        and '"/api/page"' in srv and '"/api/transcript"' in srv)


def _png154(w, h):
    import struct
    import zlib
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    chunk = lambda t, b: struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def _t_v154_shot(_ok):
    from . import page
    from .core import Instance
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "workflow").mkdir()
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"v154-shot-{os.getpid()}"}), encoding="utf-8")
        inst = Instance(root)
        inst.state.update(state="WAIT_REPLY", turn=5)
        inst.save()
        env = page.poll_env(inst)
        _ok("1.5.4 截图：轮询时同时给出文本快照与截图的路径", page.ENV in env and page.SHOT_ENV in env, str(env))
        snap = _snap153(2)
        snap["screenshot"] = {"status": "ok", "mode": "viewport", "captured_at": "2026-10-10T11:00:00+00:00"}
        Path(env[page.ENV]).write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        png = _png154(1280, 900)
        Path(env[page.SHOT_ENV]).write_bytes(png)
        r = page.after_poll(inst, "poll", "done")
        d = page.page_dir(inst)
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        _ok("1.5.4 截图：原样保存为 latest.png 与本轮 turn-NNN.png，与文本快照成对",
            r["ok"] and r["shot"]["status"] == "ok" and (d / "latest.png").read_bytes() == png
            and (d / "turn-005.png").exists() and (d / "turn-005.json").exists()
            and meta["shot"]["width"] == 1280 and meta["shot"]["height"] == 900, str(r))
        env = page.poll_env(inst)
        snap["screenshot"] = {"status": "unavailable", "reason": "浏览器调试端口连不上"}
        Path(env[page.ENV]).write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        r2 = page.after_poll(inst, "poll", "done")
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        _ok("1.5.4 截图降级：浏览器不可用时只更新文本快照，记下原因，保留上一张截图",
            r2["ok"] and meta["shot_last"]["status"] == "unavailable" and "调试端口" in meta["shot_last"]["reason"]
            and (d / "latest.png").read_bytes() == png and meta["shot"]["width"] == 1280, str(meta.get("shot_last")))
        env = page.poll_env(inst)
        Path(env[page.SHOT_ENV]).write_bytes(b"not a png at all, just text bytes")
        r3 = page.after_poll(inst, "poll", "done")
        _ok("1.5.4 截图：不是 PNG 时不收下，已有截图不变", r3["shot"]["status"] == "bad" and (d / "latest.png").read_bytes() == png,
            str(r3))
        for t in (6, 7, 8):
            inst.state["turn"] = t
            inst.save()
            env = page.poll_env(inst)
            Path(env[page.SHOT_ENV]).write_bytes(_png154(100 + t, 50))
            page.after_poll(inst, "look", None, shot_keep=2)
        names = sorted(p.name for p in d.glob("turn-*.png"))
        a = page.api_page(inst, meta_only=True)
        _ok("1.5.4 截图：只有截图没有文本时也收下；按轮只保留最近 N 张",
            names == ["turn-007.png", "turn-008.png"] and a["shots"] == [7, 8] and a["meta"]["shot"]["width"] == 108, str(names))
        _ok("1.5.4 看板接口：取最新截图与指定轮次的截图，没有时返回空",
            page.shot_file(inst) == d / "latest.png" and page.shot_file(inst, 7) == d / "turn-007.png"
            and page.shot_file(inst, 5) is None)
        (root / "workflow" / "policy.json").write_text(json.dumps({"page_screenshots": False}), encoding="utf-8")
        _ok("1.5.4 截图：policy 关闭后轮询不再请求截图，wf look 仍然请求",
            page.SHOT_ENV not in page.poll_env(inst) and page.SHOT_ENV in page.poll_env(inst, shots=True)
            and page.poll_env(inst, shots=True, mode="full").get(page.SHOT_MODE_ENV) == "full")
    from .core import WF_HOME
    html = (WF_HOME / "dashboard" / "flow.html").read_text(encoding="utf-8")
    srv = (WF_HOME / "wflib" / "server.py").read_text(encoding="utf-8")
    _ok("1.5.4 看板：快照板块显示截图，接口 /api/page-image 只读返回 PNG",
        "/api/page-image" in html and "本次查看没有新截图" in html and '"/api/page-image"' in srv and '"image/png"' in srv)


def _t_v155_docs(_ok):
    from .core import WF_HOME
    d = WF_HOME / "docs"
    p = (d / "PROCESS.md").read_text(encoding="utf-8") if (d / "PROCESS.md").exists() else ""
    c = (d / "CONTEXT.md").read_text(encoding="utf-8") if (d / "CONTEXT.md").exists() else ""
    _ok("1.5.5 文档：PROCESS.md（按 pstack 方式开发软件）与 CONTEXT.md（上下文与开新对话）随工具发布",
        "## 1. 开工" in p and "## 5. 独立验证" in p and "## 10. 对照表" in p
        and "## 1. 给用户的说明" in c and "## 3. 何时开新对话" in c and "## 4. 恢复包与送达" in c)
    w = (d / "WORKFLOW.md").read_text(encoding="utf-8")
    _ok("1.5.5 文档：WORKFLOW.md 指向两份新文档（恢复包里的说明消息因此也带上指引）",
        "PROCESS.md" in w and "CONTEXT.md" in w)


def _t_v156_heartbeat(_ok):
    from datetime import timedelta
    from . import admin, asks, heartbeat, policy
    from .core import Instance, now
    orig = policy.status
    policy.status = lambda inst, kind: {"exists": True, "approved": True, "changed_since_approval": False, "current": "t"}
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "workflow").mkdir()
            (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"v156-hb-{os.getpid()}"}), encoding="utf-8")
            inst = Instance(root)
            inst.state.update(state="WAIT_SCHEDULE", turn=13)
            inst.save()
            h0 = heartbeat.driver_health(inst)
            inst.state["driver"] = {"last": {"ts": (now() - timedelta(minutes=20)).isoformat(timespec="seconds"), "exit": 10},
                                    "inflight": None}
            inst.save()
            h1 = heartbeat.driver_health(inst)
            doc = [x for x in admin.doctor(inst) if x["item"] == "驱动器心跳"]
            nts = [n for n in asks.notices(inst) if n["title"] == "驱动器停了"]
            _ok("1.5.6 驱动器心跳：超过 15 分钟没有运行就判为停了，doctor 警告，wf ask 主动呈报（第 12–14 轮循环静默退出）",
                h0["status"] == "never" and h1["status"] == "stale" and bool(doc) and doc[0]["level"] == "warn" and len(nts) == 1,
                f"{h0} {h1} {doc} {nts}")
            inst.state["paused"] = {"ts": "t", "note": "测试"}
            inst.save()
            h2 = heartbeat.driver_health(inst)
            inst.state.pop("paused", None)
            inst.state["driver"]["last"]["ts"] = (now() - timedelta(minutes=2)).isoformat(timespec="seconds")
            inst.save()
            h3 = heartbeat.driver_health(inst)
            _ok("1.5.6 驱动器心跳：暂停中、或刚运行过，都不算停了", h2["status"] == "ok" and h3["status"] == "ok", f"{h2} {h3}")
    finally:
        policy.status = orig


def _t_v157_notify(_ok):
    from . import notify
    from .core import Instance
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "workflow").mkdir()
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"v157-nt-{os.getpid()}"}), encoding="utf-8")
        pol = root / "workflow" / "policy.json"
        pol.write_text(json.dumps({"allow": []}), encoding="utf-8")
        inst = Instance(root)
        inst.state.update(state="IDLE", turn=1)
        inst.save()
        got = []
        snd = lambda t, m, s: got.append((t, m, s)) or True
        store = Path(td) / "notified.json"
        r1 = notify.after_tick([inst], sender=snd, store=store)
        r2 = notify.after_tick([inst], sender=snd, store=store)
        _ok("1.5.7 系统通知：新出现的待决事项通知一次，之后不重复（第 17 轮把执行端的通知胶水收进工具）",
            len(r1) >= 1 and any("白名单" in x["subtitle"] + x["message"] for x in r1) and r2 == [], f"{r1} {r2}")
        pol.unlink()
        notify.after_tick([inst], sender=snd, store=store)
        keys = json.loads(store.read_text(encoding="utf-8"))["keys"]
        pol.write_text(json.dumps({"allow": []}), encoding="utf-8")
        rf = notify.after_tick([inst], sender=lambda *a: False, store=store)
        keys2 = json.loads(store.read_text(encoding="utf-8"))["keys"]
        _ok("1.5.7 系统通知：事项处理掉后清除记录，发送失败不记为已通知（下次重试）",
            keys == {} and rf == [] and keys2 == {}, f"{keys} {rf} {keys2}")
    _ok("1.5.7 系统通知：AppleScript 字符串转义引号、反斜杠与换行", notify._osa('a"b\\c\nd') == '"a\\"b\\\\c d"',
        notify._osa('a"b\\c\nd'))


def _t_v158_linger(_ok):
    from datetime import timedelta
    from . import driver
    from .core import now

    class C:
        def __init__(self, pol, left):
            self.pol, self._left = pol, left

        def time_left(self):
            return self._left
    soon = (now() + timedelta(seconds=60)).isoformat(timespec="seconds")
    w = driver._linger_due({"state": "WAIT_REPLY", "poll_after": soon}, C({"poll_interval_s": 120}, 500))
    _ok("1.5.8 等回复：下一次轮询在本次唤醒的时间内，就在 tick 里等到那时（此前要等 300 秒的唤醒间隔）",
        w is not None and 50 <= w <= 61
        and driver._linger_due({"state": "WAIT_REPLY", "poll_after": soon}, C({"poll_interval_s": 120}, 30)) is None
        and driver._linger_due({"state": "IDLE", "poll_after": soon}, C({"poll_interval_s": 120}, 500)) is None
        and driver._linger_due({"state": "WAIT_REPLY", "poll_after": soon}, C({"poll_interval_s": 0}, 500)) is None, str(w))
    from .core import WF_HOME
    r = (WF_HOME / "docs" / "REVIEW.md").read_text(encoding="utf-8") if (WF_HOME / "docs" / "REVIEW.md").exists() else ""
    _ok("1.5.8 文档：REVIEW.md 写明独立审查的调用、审查清单、输出与只能阻止的处置",
        "## 3. 审什么" in r and '"verdict"' in r and "## 5. 处置" in r and "--kind tool" in r)


def _t_v158_auto_update(_ok):
    from . import policy, release
    allow = r"^wf self-update --auto workflow/outbox/collab-wf-[0-9]+\.[0-9]+\.[0-9]+\.zip$"
    pol = {"allow": [allow], "auto_self_update": True}
    c = "wf self-update --auto workflow/outbox/collab-wf-1.5.9.zip"
    ok1 = policy.classify(c, pol)
    no_flag = policy.classify(c, {"allow": [allow]})
    other = policy.classify("wf self-update --auto /tmp/evil.zip", pol)
    plain = policy.classify("wf self-update workflow/outbox/collab-wf-1.5.9.zip", pol)
    _ok("1.5.8 自动安装工具版本：只有 policy 开启 auto_self_update 且白名单放行时，wf self-update --auto <outbox 里的版本包> 才自动执行",
        ok1["auto"] and not ok1["manual"] and not no_flag["auto"] and not other["auto"] and not plain["auto"],
        f"{ok1['reasons']} | {no_flag['reasons']} | {other['reasons']} | {plain['reasons']}")
    cur = {"wflib/policy.py": "a", "wflib/driver.py": "b", "wflib/notify.py": "c", "wflib/floor.py": "f"}
    _ok("1.5.8 自动安装工具版本：改动护栏文件的版本要用户确认（1.5.9 起护栏文件只有 floor.py，其余由底线行为检查把关）",
        release.guard_changes({**cur, "wflib/driver.py": "B", "wflib/new.py": "n"}, cur) == []
        and release.guard_changes({**cur, "wflib/policy.py": "A"}, cur) == []
        and release.guard_changes({**cur, "anomalies.json": "x"}, cur) == []
        and release.guard_changes({**cur, "wflib/floor.py": "F"}, cur) == ["wflib/floor.py"])


def _t_v159_floor(_ok):
    import shutil
    from . import floor, policy, release
    from .core import WF_HOME
    _ok("1.5.9 底线集中在 floor.py：policy 用的就是 floor 的定义；自动安装只有改 floor.py 才要用户确认",
        policy.STOP_FLOOR is floor.STOP_FLOOR and policy.DIFF_FLOOR is floor.DIFF_FLOOR
        and policy.GUARDRAIL is floor.GUARDRAIL and policy.META is floor.META and release.GUARD_FILES == ("wflib/floor.py",))
    probs = floor.probe(WF_HOME)
    _ok("1.5.9 底线行为检查：当前代码对全部底线用例停下，自动安装仍检查 floor.py 并调用行为检查", probs == [], "；".join(probs))
    with tempfile.TemporaryDirectory() as td:
        cand = Path(td) / "cand"
        shutil.copytree(WF_HOME, cand, ignore=shutil.ignore_patterns(".git", "local", "__pycache__"))
        p = cand / "wflib" / "policy.py"
        t = p.read_text(encoding="utf-8")
        t2 = t.replace("for cat, pat in STOP_FLOOR + ", "for cat, pat in [] + ", 1)
        p.write_text(t2, encoding="utf-8")
        weak = floor.probe(cand)
    _ok("1.5.9 底线行为检查：候选版本的 classify 不再检查底线时，检查不通过（自动安装会被拒绝）",
        t2 != t and len(weak) >= len(floor.FLOOR_CASES), "；".join(weak[:3]))


def _t_v159_inbox(_ok):
    from . import notify
    _ok("1.5.9 待办分流：底线命中、撤销暂停、目标达成交给用户，其余交给执行端",
        notify.route({"kind": "approve", "why": "- 命令 1「cat .env」：属于必须停下的“密钥”类"}) == "user"
        and notify.route({"kind": "approve", "why": "- 命令 1「ls」：不在已批准的命令白名单中"}) == "executor"
        and notify.route({"kind": "approve", "why": "- 版本包 a.py：新增内容命中“实盘”：mode: live"}) == "user"
        and notify.route({"kind": "unblock", "why": "某一步失败次数超过上限"}) == "executor"
        and notify.route({"kind": "unpause", "why": "已暂停"}) == "user"
        and notify.route({"key": "done:12", "why": "全部目标判据已通过"}) == "user"
        and notify.route({"key": "driver_stale:x", "why": "驱动器停了"}) == "executor")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "inbox.json"
        a = {"key": "p|K7P", "project": "p", "for": "executor", "title": "t", "why": "w", "action": "", "code": "K7P"}
        b = {**a, "key": "p|X2Q", "code": "X2Q"}
        first = notify.write_inbox([], p, items=[a])
        time.sleep(1.1)
        second = notify.write_inbox([], p, items=[a, b])
        third = notify.write_inbox([], p, items=[b])
        data = json.loads(p.read_text(encoding="utf-8"))
    _ok("1.5.9 执行端待办信号：同一件事保留首次出现时间，新事另记，处理掉的事从 inbox 消失",
        second[0]["since"] == first[0]["since"] and second[1]["since"] != first[0]["since"]
        and [x["key"] for x in data["items"]] == ["p|X2Q"] and third[0]["since"] == second[1]["since"],
        json.dumps(data, ensure_ascii=False))


def _t_v160_sync(_ok):
    import io
    import zipfile
    from . import anomaly, asks, sync
    from .core import Instance
    orig = (sync.cfg, sync.SYNC_DIR, anomaly.call)
    calls = []

    def fake_call(op, args, timeout=None, env_extra=None):
        calls.append([str(a) for a in args])
        return {"code": 0 if fake_call.ok else 1, "data": {"commit": "abc123"} if fake_call.ok else {}, "raw": "测试"}
    fake_call.ok = True
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "proj"
        (root / "workflow" / "evidence" / "turn-001" / "delivered").mkdir(parents=True)
        (root / "workflow" / "outbox").mkdir(parents=True)
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"v160-sync-{os.getpid()}"}), encoding="utf-8")
        (root / "a.txt").write_text("项目文件\n", encoding="utf-8")
        (root / "workflow" / "approvals.json").write_text("{}", encoding="utf-8")
        (root / "workflow" / "turns.jsonl").write_text(json.dumps({"turn": 1, "status": "sent"}) + "\n", encoding="utf-8")
        (root / "workflow" / "outbox" / "turn-001.md").write_text("回报\n", encoding="utf-8")
        (root / "workflow" / "outbox" / "turn-002.md").write_text("还没发出的草稿\n", encoding="utf-8")
        reply = root / "workflow" / "evidence" / "turn-001" / "reply.md"
        reply.write_text("回复\n", encoding="utf-8")
        (root / "workflow" / "evidence" / "turn-001" / "c01.out").write_text("命令原文\n", encoding="utf-8")
        inst = Instance(root)
        inst.state.update(state="IDLE", turn=1)
        inst.save()
        sync.cfg = lambda: {"sync": "测试同步程序", "sync_projects": [inst.name]}
        sync.SYNC_DIR = Path(td) / "local-sync"
        anomaly.call = fake_call
        try:
            files = sync.collect(inst)
            names = set(files)
            _ok("1.6.0 同步内容：只放白名单（项目已提交文件、工具源码、已发出的消息、回复、STATUS 与 README）",
                {"project/a.txt", "tool/VERSION", "turns/turn-001.md", "replies/reply-001.md", "STATUS.md", "README.md"} <= names
                and not any(n.startswith(("project/workflow/evidence", "project/workflow/outbox")) or "approvals" in n
                            or n.endswith(".jsonl") or n == "turns/turn-002.md" or "c01.out" in n for n in names)
                and "## 目标" in files["STATUS.md"].decode("utf-8"), str(sorted(n for n in names if not n.startswith("tool/"))))
            reply.write_text("回复\napi_key = " + "abcdefghijklmnop1234\n", encoding="utf-8")   # 拆开写：本文件也会被同步扫描
            r1 = sync.run(inst)
            nb = [n["key"] for n in asks.notices(inst)]
            reply.write_text("回复\n", encoding="utf-8")
            r2 = sync.run(inst)
            r3 = sync.run(inst)
            _ok("1.6.0 同步：发现疑似密钥就整次不推送并提示；移除后推送，内容没变不再调用同步程序",
                r1["status"] == "blocked" and any(k.startswith("sync_blocked:") for k in nb) and r2["status"] == "pushed"
                and r3["status"] == "unchanged" and len(calls) == 1 and "--dir" in calls[0]
                and (Path(calls[0][calls[0].index("--dir") + 1]) / "replies" / "reply-001.md").exists()
                and not any(n["key"].startswith("sync_blocked:") for n in asks.notices(inst)), f"{r1} {r2} {r3} {calls}")
            fake_call.ok = False
            fails = [sync.run(inst, force=True)["status"] for _ in range(3)]
            nf = [n["key"] for n in asks.notices(inst)]
            sync.cfg = lambda: {}
            off = sync.after_tick([inst])
            _ok("1.6.0 同步：连续失败 3 次后提示执行端；没有开启同步的项目什么都不做",
                fails == ["failed"] * 3 and any(k.startswith("sync_failing:") for k in nf) and off == [], f"{fails} {nf} {off}")
            z = io.BytesIO()
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("x/config.py", "SECRET_KEY = '" + "abcdefghijklmnop1234'\n")
            hits = sync.scan({"artifacts/p.zip": z.getvalue(), "a.md": ("提交 " + "a" * 64 + "\n").encode("utf-8")})
            _ok("1.6.0 同步：zip 内的文本也扫描；sha256 摘要不算密钥", len(hits) == 1 and "p.zip:x/config.py" in hits[0], str(hits))
        finally:
            sync.cfg, sync.SYNC_DIR, anomaly.call = orig


def _t_v161_neutral(_ok):
    from . import driver, policy
    from .core import Instance
    old = {"allow": ["^a ", "^b "], "max_rounds_per_day": 24, "max_conversation_turns": 20, "stop": [{"pattern": "x"}]}
    w = policy.widening
    _ok("1.6.1 白名单变更：收紧或只改时间类参数不算扩大；新增命令、放宽上限、去掉停止规则、新开自动安装、不认识的键都算",
        w(old, {**old, "allow": ["^a "]}) == [] and w(old, {**old, "max_rounds_per_day": 10, "poll_interval_s": 30}) == []
        and w(old, {**old, "max_conversation_turns": 10}) == []
        and all(w(old, x) for x in ({**old, "allow": ["^a ", "^b ", "^rm "]}, {**old, "max_rounds_per_day": 30},
                                    {**old, "max_conversation_turns": 0}, {**old, "stop": []},
                                    {**old, "auto_self_update": True}, {**old, "foo": 1})),
        str([w(old, {**old, "foo": 1}), w(old, {**old, "max_conversation_turns": 0})]))
    pol = {"allow": [r"^wf (doctor|self-check)( |$)"]}
    g = lambda chk: {"goals": [{"id": "G1", "title": "t", "kind": "exec", "check": chk, "pass": {"exit": 0},
                                "means": "m", "on_fail": "f"}]}
    _ok("1.6.1 目标变更：检查命令都在已批准的白名单内才不算扩大（目标的检查命令由驱动器直接运行）",
        policy.goals_widening(g("wf doctor"), pol) == [] and policy.goals_widening(g("python3 x.py"), pol) != []
        and policy.goals_widening({"goals": []}, pol) != [])
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "workflow").mkdir()
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"v161-nt-{os.getpid()}"}), encoding="utf-8")
        (root / "workflow" / "policy.json").write_text(json.dumps(old), encoding="utf-8")
        inst = Instance(root)
        inst.state.update(state="APPLY", turn=1)
        inst.save()
        policy.approve(inst, "policy", "测试")
        src = root / "src"
        (src / "workflow").mkdir(parents=True)
        (src / "workflow" / "policy.json").write_text(json.dumps({**old, "allow": ["^a "]}), encoding="utf-8")
        f1 = policy.release_findings(inst, src, old)
        (src / "workflow" / "policy.json").write_text(json.dumps({**old, "allow": ["^a ", "^rm "]}), encoding="utf-8")
        f2 = policy.release_findings(inst, src, old)
        ctx = driver.Ctx(inst, old, time.time() + 60)
        before = {"policy": {"approved": True, "sha": policy.sha256(root / "workflow" / "policy.json")}, "goals": {}}
        (root / "workflow" / "policy.json").write_text(json.dumps({**old, "allow": ["^a "]}), encoding="utf-8")
        driver._auto_approve_neutral(ctx, before, "v0.2")
        s1 = policy.status(inst, "policy")
        before = {"policy": {"approved": True, "sha": policy.sha256(root / "workflow" / "policy.json")}, "goals": {}}
        (root / "workflow" / "policy.json").write_text(json.dumps({**old, "allow": ["^a ", "^rm "]}), encoding="utf-8")
        driver._auto_approve_neutral(ctx, before, "v0.3")
        s2 = policy.status(inst, "policy")
        evs = [json.loads(l) for l in inst.events_path.read_text(encoding="utf-8").splitlines()]
    _ok("1.6.1 不扩大权限的白名单变更：评审不再停下，落地后由驱动器自动批准并记明；扩大的照旧停下、不自动批准",
        f1 == [] and len(f2) == 1 and "扩大" in f2[0]["why"] and s1["approved"] and not s2["approved"]
        and any(e.get("type") == "approve" and e.get("by") == "driver" for e in evs), f"{f1} {f2} {s1} {s2}")


def _t_v161_unattended(_ok):
    import subprocess
    from datetime import timedelta
    from . import driver, policy
    from .core import Instance, now
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "workflow").mkdir()
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"v161-un-{os.getpid()}"}), encoding="utf-8")
        (root / "a.txt").write_text("a\n", encoding="utf-8")
        g = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(["git", "init", "-q"], cwd=root)
        subprocess.run(["git", "add", "-A"], cwd=root)
        subprocess.run(g + ["commit", "-qm", "release v0.1"], cwd=root)
        inst = Instance(root)
        inst.state.update(state="APPLY", turn=1)
        inst.save()
        ctx = driver.Ctx(inst, {}, time.time() + 60)
        (root / "notes.md").write_text("执行端手写的说明\n", encoding="utf-8")
        saved = driver._save_dirty(ctx)
        clean = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout.strip()
        msg = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=root, capture_output=True, text=True).stdout
        (root / "cfg.py").write_text("api_key = " + "abcdefghijklmnop1234\n", encoding="utf-8")   # 拆开写：本文件也会被同步扫描
        saved2 = driver._save_dirty(ctx)
        left = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout
        _ok("1.6.1 落地前工作区有改动：存成单独的提交并记入回报，不再阻塞（第 34 轮 AP-2）；含疑似密钥时不存，照旧停下",
            saved == ["notes.md"] and "未经交付" in msg and not any(l.endswith("notes.md") for l in clean.splitlines())
            and saved2 == [] and "cfg.py" in left, f"{saved} {clean!r} {msg!r} {saved2} {left!r}")
        out = root / "workflow" / "outbox" / "turn-001.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("【任务 · 第 1 轮 · v0.1】档位：high\n\n内容\n", encoding="utf-8")
        inst.state.update(state="COMPOSE", outgoing={"turn": 1, "file": str(out), "kind": "task", "effort": "high"},
                          budget={"date": now().strftime("%Y-%m-%d"), "rounds": 5})
        inst.save()
        r = driver.h_compose(driver.Ctx(inst, {"max_rounds_per_day": 5}, time.time() + 60))
        tomorrow = (now() + timedelta(days=1)).strftime("%Y-%m-%d")
        st = inst.state
        _ok("1.6.1 每日轮次上限：上限不变，但不再停下等人，等到第二天自动继续",
            r[0] == "wait" and st["state"] == "QUOTA_WAIT" and str(st.get("until", "")).startswith(tomorrow), f"{r} {st.get('until')}")


def _t_v162_program_ops(_ok):
    from . import anomaly, fidelity
    orig = (anomaly.transport_cfg, fidelity.run_with_grace)
    seen = []

    def fake_run(cmd, t, env, grace=15):
        seen.append(list(cmd))
        return 0, '{"ok": true}\n', "", False
    try:
        fidelity.run_with_grace = fake_run
        anomaly.transport_cfg = lambda: {"module": "my_adapter", "sync": "/opt/x/gh_sync.py", "review": True}
        r1 = anomaly.call("sync", ["--project", "p"])
        anomaly.call("send", ["--turn", "1"])
        anomaly.call("review", ["--project", "p"])
        anomaly.transport_cfg = lambda: {"sync": "/opt/x/gh_sync.py"}
        anomaly.call("sync", ["--project", "p"])
    finally:
        anomaly.transport_cfg, fidelity.run_with_grace = orig
    _ok("1.6.2 适配器调用：sync、review 写了程序路径时直接运行它，通道动作与写 true 的仍按模块方式（第 36 轮“未知动作：sync”）",
        r1.get("code") == 0 and seen[0][0] == "/opt/x/gh_sync.py" and seen[1][1:4] == ["-m", "my_adapter", "send"]
        and seen[2][1:4] == ["-m", "my_adapter", "review"] and seen[3][0] == "/opt/x/gh_sync.py", str(seen))


def _t_v163_bootstrap_short(_ok):
    import subprocess
    from . import admin, sync
    from .core import Instance
    orig = (sync.cfg, sync.run)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "workflow").mkdir()
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"v163-bs-{os.getpid()}"}), encoding="utf-8")
        (root / "PROJECT.md").write_text("# PROJECT.md\n", encoding="utf-8")
        (root / ".gitignore").write_text("workflow/\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=root)
        subprocess.run(["git", "add", "-A"], cwd=root)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "release v0.1"], cwd=root)
        (root / "workflow" / "turns.jsonl").write_text(json.dumps({"turn": 7, "status": "sent"}) + "\n", encoding="utf-8")
        (root / "workflow" / "evidence" / "turn-007").mkdir(parents=True)
        (root / "workflow" / "evidence" / "turn-007" / "reply.md").write_text("回复\n", encoding="utf-8")
        inst = Instance(root)
        inst.state.update(state="COMPOSE", turn=8)
        inst.save()
        calls = []
        try:
            sync.run = lambda i, **k: calls.append(i.name) or {"status": "pushed"}
            sync.cfg = lambda: {"sync": "/x/gh_sync.py", "sync_projects": [inst.name], "sync_repo": "me/repo"}
            b1 = admin.bootstrap(inst, "测试")
            short = b1["short"].read_text(encoding="utf-8") if b1.get("short") else ""
            sync.cfg = lambda: {}
            b2 = admin.bootstrap(inst, "测试")
            long_ok = b1["message"].exists() and b2["message"].exists()
        finally:
            sync.cfg, sync.run = orig
    _ok("1.6.3 开新对话的短消息：开启同步并写了 sync_repo 时先同步、再生成带 raw 链接的短消息；没开启时照旧只有长消息",
        bool(b1.get("short")) and calls == [inst.name] and "https://raw.githubusercontent.com/me/repo/main/STATUS.md" in short
        and "turns/turn-007.md" in short and "replies/reply-007.md" in short and len(short) < 4000
        and b2.get("short") is None and long_ok, short[:300])


def _t_v164_newconv(_ok):
    from . import driver, policy
    old = {"allow": ["^a "], "auto_apply": True}
    on = {**old, "auto_new_conversation": True}
    w_on, w_off = policy.widening(old, on), policy.widening(on, old)
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "long.md").write_text("【项目续接 · 新对话】档位：max\n长消息 v1.0\n", encoding="utf-8")
        (d / "short.md").write_text("【项目续接 · 新对话】档位：high\n短消息 v1.0\n", encoding="utf-8")
        b = {"message": d / "long.md", "short": d / "short.md"}
        t1, e1, w1 = driver._newconv_message(b, "【回报 · 第 9 轮 · v1.0】档位：medium\n", "pushed")
        t2, e2, w2 = driver._newconv_message(b, "", "failed")
        t3, e3, w3 = driver._newconv_message({"message": d / "long.md", "short": None}, "", "")
    _ok("1.6.4 开新对话：开启 auto_new_conversation 算扩大权限（要批准）、关闭不算；同步成功才用短消息，否则用长消息；本轮原本要发的消息原样附在后面",
        any("auto_new_conversation" in x for x in w_on) and w_off == []
        and w1 == "短消息" and e1 == "high" and "短消息 v1.0" in t1 and "【回报 · 第 9 轮" in t1
        and w2 == "长消息" and e2 == "max" and "附：" not in t2 and w3 == "长消息",
        str([w_on, w_off, w1, e1, w2, e2, w3]))


def _t_v165_signals(_ok):
    from . import notify, signals
    orig = signals.SIG_DIR
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        try:
            signals.SIG_DIR = d / "signals"
            signals.add("ext:demo", "测试：外部信号", "看门狗发现的问题", for_="executor", project="p1", source="test")
            signals.add("ext:old", "过期的信号", "x", ttl_s=60)
            p_old = signals._path("ext:old")
            rec = json.loads(p_old.read_text(encoding="utf-8"))
            rec["expires"] = "2000-01-01T00:00:00+00:00"
            p_old.write_text(json.dumps(rec), encoding="utf-8")
            rows = notify.collect_inbox([])
            keys = [r["key"] for r in rows]
            demo = next((r for r in rows if r["key"] == "signal|ext:demo"), {})
            inbox = d / "inbox.json"
            inbox.write_text(json.dumps({"items": [{"key": "p1|AAA", "since": "2000-01-01T00:00:00+00:00"},
                                                   {"key": "p1|quota:x", "since": "2000-01-01T00:00:00+00:00"}]}),
                             encoding="utf-8")
            base = {"project": "p1", "for": "executor", "why": "", "action": ""}
            items = notify.write_inbox([], inbox, items=[{**base, "key": "p1|AAA", "title": "旧事项", "code": "AAA"},
                                                         {**base, "key": "p1|quota:x", "title": "额度", "code": ""}])
            esc = {x["key"]: x for x in items}
            cleared = signals.clear("ext:demo")
            after = [r["key"] for r in notify.collect_inbox([])]
            gone_old = not p_old.exists()
        finally:
            signals.SIG_DIR = orig
    _ok("1.6.5 外部信号：写入后出现在 inbox（交给谁、项目保留），过期的自动丢弃，clear 后消失；交给执行端超过 2 小时的事升级给用户（额度等待除外）",
        "signal|ext:demo" in keys and demo.get("for") == "executor" and demo.get("project") == "p1"
        and "signal|ext:old" not in keys and gone_old and cleared and "signal|ext:demo" not in after
        and esc["p1|AAA"]["for"] == "user" and esc["p1|AAA"].get("escalated") is True
        and esc["p1|quota:x"]["for"] == "executor", json.dumps([keys, items], ensure_ascii=False)[:600])


def _t_v165_watchdog(_ok):
    from . import notify, signals, watchdog
    from .core import Instance, now_iso
    orig = (signals.SIG_DIR, notify.INBOX)
    past = "2000-01-01T00:00:00+00:00"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "proj"
        (root / "workflow").mkdir(parents=True)
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"v165-wd-{os.getpid()}"}), encoding="utf-8")
        (root / "PROJECT.md").write_text("# PROJECT.md\n", encoding="utf-8")
        inst = Instance(root)
        try:
            signals.SIG_DIR, notify.INBOX = Path(td) / "signals", Path(td) / "inbox.json"
            inst.state.update(state="WAIT_REPLY", since=past, turn=3, poll_after=past, send_ref="r")
            inst.save()
            f1 = {f["check"] for f in watchdog.check(inst)}
            w1 = watchdog.run([inst], write=True)
            inbox1 = notify.INBOX.read_text(encoding="utf-8") if notify.INBOX.exists() else ""
            inst.state.update(state="SEND", since=now_iso(), send_intent={"turn": 3, "status": "pending", "ts": past})
            inst.state.pop("poll_after", None)
            inst.save()
            f2 = {f["check"] for f in watchdog.check(inst)}
            inst.state.update(state="IDLE", since=now_iso(), send_intent=None)
            inst.save()
            f3 = [f["check"] for f in watchdog.check(inst)]
            watchdog.run([inst], write=True)
            left = [r["key"] for r in signals.load() if str(r.get("key", "")).startswith("watchdog:")]
        finally:
            signals.SIG_DIR, notify.INBOX = orig
    _ok("1.6.5 看门狗：等回复停留过久、该轮询没轮询、发送结果不明都能判出；--write 写成信号并重写 inbox，恢复后信号自动清除",
        {"dwell", "poll_overdue"} <= f1 and len(w1) >= 2 and "signal|watchdog:" in inbox1
        and "send_unknown" in f2 and f3 == [] and left == [], str([f1, f2, f3, left]))


def _t_v165_executor_decision(_ok):
    from . import asks
    from .core import Instance, WfError
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "proj"
        (root / "workflow").mkdir(parents=True)
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"v165-ex-{os.getpid()}"}), encoding="utf-8")
        (root / "PROJECT.md").write_text("# PROJECT.md\n", encoding="utf-8")
        inst = Instance(root)
        floor_item = {"kind": "approve", "key": "approve:1:a", "code": "ABC", "title": "需要你批准才能继续",
                      "why": "- 命令 1「cat .env」：属于必须停下的“密钥”类"}
        exec_item = {**floor_item, "key": "approve:1:b", "code": "DEF", "why": "- 命令 1「ls」：不在已批准的命令白名单中"}
        refused = no_reason = False
        try:
            asks.executor_decision(inst, "approve", floor_item, "测试：想代决底线事项")
        except WfError:
            refused = True
        try:
            asks.executor_decision(inst, "approve", exec_item, "  ")
        except WfError:
            no_reason = True
        note = asks.executor_decision(inst, "approve", exec_item, "测试：只读命令，执行端判断可以放行")
        evp = root / "workflow" / "events.jsonl"
        ev = evp.read_text(encoding="utf-8") if evp.exists() else ""
    _ok("1.6.5 执行端代决：只限交给执行端的事项、必须写理由，如实记 by=executor；四道底线仍只认用户原话",
        refused and no_reason and note.startswith("执行端代决") and '"executor_decision"' in ev
        and '"by": "executor"' in ev and '"ABC"' not in ev, ev[-400:])


def _t_v166_tasks(_ok):
    from . import tasks
    from .core import Instance, WfError
    d = ops.parse_directives("发布：无\n执行端 1：把看门狗改成 wf watchdog --write\n2. 执行端 2：回报：结果\n之后：等执行端\n")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "proj"
        (root / "workflow").mkdir(parents=True)
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"v166-t-{os.getpid()}"}), encoding="utf-8")
        (root / "PROJECT.md").write_text("# PROJECT.md\n", encoding="utf-8")
        inst = Instance(root)
        inst.state.update(state="VERIFY", turn=5)
        inst.save()
        a1 = tasks.add(inst, 5, d["executor_tasks"])
        a2 = tasks.add(inst, 5, d["executor_tasks"])   # 同一项不重复登记
        t = tasks.done(inst, "t5-1", "已改好")
        left = [x["id"] for x in tasks.open_items(inst)]
        errs = 0
        for tid in ("T5-1", "T9-9"):
            try:
                tasks.done(inst, tid, "")
            except WfError:
                errs += 1
        notes = (inst.turn_dir() / "notes.jsonl").read_text(encoding="utf-8")
        ev = (root / "workflow" / "events.jsonl").read_text(encoding="utf-8")
    _ok("1.6.6 执行端待办：驱动指令“执行端 N：”与“之后：等执行端”能解析；登记不重复、编号 T<轮>-<序>、完成写进备注并记 by=executor",
        d["executor_tasks"] == {"1": "把看门狗改成 wf watchdog --write", "2": "回报：结果"} and d["after"] == "executor"
        and not d["errors"] and len(a1) == 2 and a2 == [] and t["id"] == "T5-1" and left == ["T5-2"] and errs == 2
        and "执行端待办 T5-1 已完成" in notes and '"task_done"' in ev and '"by": "executor"' in ev,
        json.dumps([d, left, errs], ensure_ascii=False)[:500])


def _v169_inst(td, name, cfg_extra=None):
    from .core import Instance
    root = Path(td) / name
    (root / "workflow").mkdir(parents=True)
    (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"{name}-{os.getpid()}", **(cfg_extra or {})}),
                                               encoding="utf-8")
    (root / "PROJECT.md").write_text("# PROJECT.md\n", encoding="utf-8")
    return Instance(root)


def _t_v169_quiet(_ok):
    from . import asks, watchdog
    past = "2000-01-01T00:00:00+00:00"
    with tempfile.TemporaryDirectory() as td:
        a = _v169_inst(td, "v169-a")
        a.state.update(state="SEND", since=past, turn=2)
        a.save()
        k1 = [n["key"] for n in asks.notices(a) if n["key"].startswith("stuck:")]
        a.state["paused"] = {"by": "user", "ts": past}
        a.save()
        k2 = [n["key"] for n in asks.notices(a) if n["key"].startswith("stuck:")]
        b = _v169_inst(td, "v169-b", {"auto": False})
        b.state.update(state="COMPOSE", since=past, turn=2)
        b.save()
        k3 = [n["key"] for n in asks.notices(b) if n["key"].startswith("stuck:")]
        w3 = watchdog.check(b)
    _ok("1.6.9（N9）：停留过久照常提示；已暂停的项目、wf.json auto=false 的项目不再被判“停留超过上限”，看门狗同样不报",
        len(k1) == 1 and k2 == [] and k3 == [] and w3 == [], str([k1, k2, k3, w3]))


def _t_v169_send_dwell(_ok):
    from . import machine
    from datetime import timedelta
    from .core import now
    with tempfile.TemporaryDirectory() as td:
        a = _v169_inst(td, "v169-send")
        t20 = (now() - timedelta(minutes=20)).isoformat(timespec="seconds")
        t70 = (now() - timedelta(minutes=70)).isoformat(timespec="seconds")
        a.state.update(state="SEND", since=t20, turn=3, send_intent={"turn": 3, "status": "pending", "ts": t20})
        a.save()
        s1 = machine.status(a)
        a.state.update(send_intent=None)
        a.save()
        s2 = machine.status(a)
        a.state.update(since=t70, send_intent={"turn": 3, "status": "pending", "ts": t70})
        a.save()
        s3 = machine.status(a)
    _ok("1.6.9（N8）：发送结果不明的 20 分钟不算停留过久；没有待定意图时照旧按 15 分钟判；结果不明超过 65 分钟仍会报",
        not s1["stuck"] and s1["max_dwell_s"] == machine.SEND_UNKNOWN_DWELL_S and s2["stuck"] and s3["stuck"],
        str([s1["stuck"], s1["max_dwell_s"], s2["stuck"], s3["stuck"]]))


def _t_v169_sync_notice(_ok):
    from . import sync
    orig = (sync.enabled, sync.record)
    recs = []
    try:
        sync.enabled = lambda inst: True
        sync.record = lambda inst: recs[-1]
        recs.append({"blocked": {"ts": "2026-10-12T01:00:00+00:00", "secrets": ["artifacts/x.zip：不是有效的 zip"]}})
        n1 = sync.notices(None)
        recs.append({"blocked": {"ts": "2026-10-12T01:01:00+00:00", "secrets": ["artifacts/x.zip：不是有效的 zip"]}})
        n2 = sync.notices(None)
        recs.append({"blocked": {"ts": "2026-10-12T01:02:00+00:00", "secrets": ["a.txt 第 1 行：疑似私钥（abcd…ef）"]}})
        n3 = sync.notices(None)
    finally:
        sync.enabled, sync.record = orig
    _ok("1.6.9（N6、N7）：只有无效 zip 时标题不写“疑似密钥”；同一原因不同时刻 key 相同；真有疑似密钥时照旧写明",
        len(n1) == 1 and "不是有效的 zip" in n1[0]["title"] and "密钥" not in n1[0]["title"]
        and n1[0]["key"] == n2[0]["key"] and n1[0]["key"].startswith("sync_blocked:")
        and "疑似密钥" in n3[0]["title"] and n3[0]["key"] != n1[0]["key"], json.dumps([n1, n2, n3], ensure_ascii=False)[:600])


def _t_v169_review_heartbeat(_ok):
    import contextlib
    import io
    from . import anomaly, core, release
    orig = (anomaly.transport_cfg, anomaly.call, core.LOCAL)
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src"
        (src / "wflib").mkdir(parents=True)
        (src / "wflib" / "x.py").write_text("x = 1\n", encoding="utf-8")

        def slow(op, args, timeout=None, env_extra=None):
            time.sleep(2.6)
            return {"code": 4, "data": {"verdict": "unreviewed"}, "raw": "测试：没有可用会话"}

        def quick(op, args, timeout=None, env_extra=None):
            return {"code": 0, "data": {"verdict": "pass", "notes": []}, "raw": ""}
        err1, err2 = io.StringIO(), io.StringIO()
        try:
            core.LOCAL = Path(td) / "local"
            anomaly.transport_cfg = lambda: {"review": "测试审查程序", "review_timeout_s": 300, "review_heartbeat_s": 1}
            anomaly.call = slow
            t0 = time.monotonic()
            with contextlib.redirect_stderr(err1):
                v1 = release._review_tool(src, Path(td) / "collab-wf-9.9.9.zip")
            took = time.monotonic() - t0
            anomaly.call = quick
            with contextlib.redirect_stderr(err2):
                v2 = release._review_tool(src, Path(td) / "collab-wf-9.9.8.zip")
            unrev = core.LOCAL / "reviews" / "collab-wf-9.9.9.unreviewed.json"
            rec = json.loads(unrev.read_text(encoding="utf-8")) if unrev.exists() else {}
        finally:
            anomaly.transport_cfg, anomaly.call, core.LOCAL = orig
    e1, e2 = err1.getvalue(), err2.getvalue()
    _ok("1.6.9（P0-A）：等审查期间按周期输出心跳；没有结论时按“未审查”返回、写明原因并留档；有结论时不输出心跳",
        v1[0] == "unavailable" and e1.count("仍在等独立审查") >= 2 and "开始等独立审查" in e1 and "本次未审查" in e1
        and "没有可用会话" in rec.get("reason", "") and took < 10
        and v2[0] == "pass" and "仍在等独立审查" not in e2 and "独立审查结论：pass" in e2, e1[-400:] + " | " + e2[-200:])


def _t_v170_conv_args(_ok):
    from . import anomaly

    class _I:
        name = "p170"
        state = {"conv": {"conv": "c-1"}}
    orig = anomaly.transport_cfg
    try:
        anomaly.transport_cfg = lambda: {}
        off = (anomaly.conv_args(_I()), anomaly.conv_new_args(_I()))
        anomaly.transport_cfg = lambda: {"per_project_conv": True}
        on = (anomaly.conv_args(_I()), anomaly.conv_new_args(_I()))
        _I.state = {"conv": {}}
        none = anomaly.conv_args(_I())
    finally:
        anomaly.transport_cfg = orig
    _ok("1.7.0 对话参数：没声明 per_project_conv 时不传；声明后传 --conv 与 conv_new 的 --project；没登记对话时不传 --conv",
        off == ([], []) and on == (["--conv", "c-1"], ["--project", "p170"]) and none == [], str([off, on, none]))


def _t_v170_warning_dedupe(_ok):
    from . import driver
    with tempfile.TemporaryDirectory() as td:
        a = _v169_inst(td, "v170-warn")
        a.state.update(state="WAIT_REPLY", turn=4, turn_flags={})
        a.save()
        for _ in range(3):
            driver._adapter_warning(a, "poll", {"data": {"status": "generating", "warning": "conv_mismatch"}})
        driver._adapter_warning(a, "send", {"data": {"warning": "conv_mismatch"}})
        driver._adapter_warning(a, "poll", {"data": {"status": "none"}})
        ev = [json.loads(x) for x in (a.root / "workflow" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    got = [(e.get("turn"), e.get("detail")) for e in ev if e.get("type") == "auto" and e.get("rule") == "ADAPTER_WARNING"]
    _ok("1.7.0 适配器告警：同一轮同一动作的同一告警只记一次，不同动作分别记，没有告警不记",
        got == [(4, "poll：conv_mismatch"), (4, "send：conv_mismatch")], str(got))


def _t_v170_sync_reasons(_ok):
    from . import sync
    d = sync.scan_detailed({"artifacts/x.zip": b"not a zip", "a.txt": "普通文字\n".encode("utf-8")})
    orig = (sync.enabled, sync.record)
    recs = []
    try:
        sync.enabled = lambda inst: True
        sync.record = lambda inst: recs[-1]
        recs.append({"blocked": {"ts": "t1", "secrets": ["artifacts/x.zip 打不开"], "reasons": ["bad_zip"]}})
        n1 = sync.notices(None)
        recs.append({"blocked": {"ts": "t2", "secrets": ["a.txt 第 1 行：命中", "artifacts/x.zip：不是有效的 zip"],
                                 "reasons": ["bad_zip", "secret"]}})
        n2 = sync.notices(None)
    finally:
        sync.enabled, sync.record = orig
    _ok("1.7.0 同步阻断原因：扫描结果带类别；有结构化原因时标题以它为准（不看文案后缀），两类都有时都写明",
        [k for k, _ in d] == ["bad_zip"] and sync.scan({"artifacts/x.zip": b"not a zip"}) == ["artifacts/x.zip：不是有效的 zip"]
        and "不是有效的 zip" in n1[0]["title"] and "密钥" not in n1[0]["title"]
        and "疑似密钥" in n2[0]["title"] and "无效 zip" in n2[0]["title"], json.dumps([d, n1, n2], ensure_ascii=False)[:600])


TESTS = [("解析容错", _t_blocks), ("驱动指令", _t_directives), ("清单编号", _t_lists), ("证据关联", _t_link),
         ("回报原文", _t_report), ("分片", _t_chunks), ("回复归属", _t_attr), ("执行接续", _t_exec),
         ("桥接自检", _t_bridge), ("额度与事件", _t_usage_seq), ("升级核验", _t_receipt), ("文件口径", _t_tree),
         ("1.5.1 关联与处置", _t_v151), ("1.5.1 超时宽限", _t_grace), ("1.5.1 只读命令", _t_readonly),
         ("1.5.1 备注提示", _t_note), ("1.5.1 确认重发", _t_resend), ("1.5.1 批准与说明", _t_resume_info),
         ("1.5.2 并发写状态", _t_v152_state), ("1.5.2 事件序号", _t_v152_seq), ("1.5.2 前置标记", _t_v152_blocking),
         ("1.5.2 续传优先", _t_v152_carry), ("1.5.2 判据与熔断", _t_v152_exec), ("1.5.2 桥接动作", _t_v152_bridge),
         ("1.5.3 页面快照", _t_v153_page), ("1.5.3 对话记录", _t_v153_talk), ("1.5.3 看板板块", _t_v153_board),
         ("1.5.4 页面截图", _t_v154_shot), ("1.5.5 流程与上下文文档", _t_v155_docs),
         ("1.5.6 驱动器心跳", _t_v156_heartbeat), ("1.5.7 系统通知", _t_v157_notify),
         ("1.5.8 等回复与审查文档", _t_v158_linger), ("1.5.8 自动安装工具版本", _t_v158_auto_update),
         ("1.5.9 底线集中与行为检查", _t_v159_floor), ("1.5.9 执行端待办信号", _t_v159_inbox),
         ("1.6.0 项目上下文同步", _t_v160_sync),
         ("1.6.1 不扩大权限的变更自动批准", _t_v161_neutral), ("1.6.1 无人值守的两处停顿", _t_v161_unattended),
         ("1.6.2 适配器调用的程序路径", _t_v162_program_ops),
         ("1.6.3 开新对话的短消息", _t_v163_bootstrap_short),
         ("1.6.4 驱动器自己开新对话", _t_v164_newconv),
         ("1.6.5 外部信号与升级", _t_v165_signals), ("1.6.5 看门狗", _t_v165_watchdog),
         ("1.6.5 执行端代决", _t_v165_executor_decision), ("1.6.6 执行端待办", _t_v166_tasks),
         ("1.6.9 暂停与静音不报停滞", _t_v169_quiet), ("1.6.9 结果不明不算停留过久", _t_v169_send_dwell),
         ("1.6.9 同步暂停提示", _t_v169_sync_notice), ("1.6.9 审查心跳", _t_v169_review_heartbeat),
         ("1.7.0 对话参数", _t_v170_conv_args), ("1.7.0 同步阻断原因", _t_v170_sync_reasons),
         ("1.7.0 适配器告警去重", _t_v170_warning_dedupe)]


def run_v15(_ok) -> None:
    print("  —— 1.5.x 新增检查 ——", flush=True)
    for name, fn in TESTS:
        try:
            fn(_ok)
        except Exception as e:   # 一组检查出错只记这一组失败，不中断整个自检
            _ok(f"1.5.0 检查组「{name}」运行出错", False, f"{type(e).__name__}: {e}")
