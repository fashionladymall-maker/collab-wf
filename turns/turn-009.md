【回报 · 第 9 轮 · v0.1】档位：high

① 环境障碍与偏离
无。

② 关键数字与终端原文

1) selftest_driver.py 第 1–135 行（测试脚手架与 consecutive_failed 那一例）。 —— 通过
````
$ sed -n 1,135p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py
"""驱动器的端到端自检：用替身传输适配器模拟通道与 Claude，跑完整的多轮自动推进与各类异常。"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .core import WF_HOME



def _user(_wf, env, proj, kind, *cmd):
    """像执行端一样：先用 wf ask 取当前待决事项的决定码，再带着码与“用户原话”执行。"""
    rc, o = _wf(env, proj, "--json", "ask")
    try:
        items = json.loads(o[o.index("{"):])["items"]
    except (ValueError, KeyError):
        items = []
    code = next((it["code"] for it in items if it["kind"] == kind or it["kind"].startswith(kind + ":")), "NONE")
    return _wf(env, proj, *cmd, "--code", code, "--said", "测试：用户原话")

def _reply(cmds, checklist, directives, effort="medium"):
    code = "\n".join(cmds)
    items = "\n".join(f"{i}. {c}" for i, c in enumerate(checklist, 1)) or "无"
    return (f"【本轮结论】测试回复。\n\n【本机执行】\n```bash\n{code}\n```\n\n【本轮回报清单】\n{items}\n\n"
            f"【需要用户决定】\n无\n\n【下一轮建议档位】{effort}\n\n【驱动指令】\n{directives}\n")


def run(tmp: Path, env: dict, _wf, _ok, _state, _project_md, _make_zip):
    print("  —— 驱动器 ——", flush=True)
    fake = tmp / "fake"
    (fake / "replies").mkdir(parents=True)
    ft = tmp / "ft.py"
    shutil.copy(WF_HOME / "wflib" / "fake_transport.py", ft)
    ft.chmod(0o755)
    for op in ("send", "poll", "quota"):
        w = tmp / f"t-{op}"
        w.write_text(f"#!/bin/sh\nexec {__import__('shlex').quote(sys.executable)} {__import__('shlex').quote(str(ft))} {op} \"$@\"\n")
        w.chmod(0o755)
    local = Path(env["WF_LOCAL_DIR"])
    local.mkdir(parents=True, exist_ok=True)
    (local / "transport.json").write_text(json.dumps({
        "send": str(tmp / "t-send"), "poll": str(tmp / "t-poll"), "quota": str(tmp / "t-quota"),
        "model": "test-model", "timeout_s": 30, "env": {"FAKE_DIR": str(fake)}}))

    p = tmp / "auto"
    _wf(env, tmp, "init", "--name", "auto", "--root", str(p))
    (p / "workflow" / "policy.json").write_text(json.dumps({
        "allow": [r"^echo ", r"^wf (version|fingerprint)( |$)", r"^cat ", r"^false$"],
        "first_poll_after_s": 0, "poll_interval_s": 0, "max_rounds_per_day": 20,
        "max_consecutive_failed_rounds": 3, "command_timeout_s": 60}))
    (p / "workflow" / "goals.json").write_text(json.dumps({"goals": [
        {"id": "G1", "title": "交付 done.txt", "kind": "exec", "check": "cat done.txt", "pass": {"exit": 0, "contains": ["ok"]},
         "means": "完成", "on_fail": "继续迭代"}]}, ensure_ascii=False))
    subprocess.run(["git", "init", "-q"], cwd=p)
    subprocess.run(["git", "add", "-A"], cwd=p)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "release v0.1"], cwd=p)

    rc, o = _wf(env, p, "run")
    _ok("驱动器：白名单未批准时不做任何事（退出码 20）", rc == 20 and "白名单" in o, o)
    _user(_wf, env, p, "policy", "policy", "approve")
    _user(_wf, env, p, "goals", "goals", "approve")
    rc, o = _wf(env, p, "transport")
    _ok("传输适配器配置检查", rc == 0, o)

    # 第 1 轮：发起任务 → 发送 → 生成中
    task = tmp / "task.md"
    task.write_text("任务：开始 v0.1。档位：high\n", encoding="utf-8")
    _wf(env, p, "kick", "--message", str(task))
    rc, o = _wf(env, p, "run")
    _ok("发起后自动发送，Claude 生成中时退出码 10", rc == 10 and _state(p)["state"] == "WAIT_REPLY"
        and len(list((fake / "inbox").glob("*.md"))) == 1, o)

    # Claude 第 1 次回复：落地 v0.2、执行两条命令、全部通过后等待一个已过期的计划节点
    z2 = tmp / "auto-v0.2.zip"
    _make_zip(z2, "auto", {"PROJECT.md": _project_md("v0.2"), "CHANGELOG.md": "# CHANGELOG\n\n## v0.2 — t\n- x\n",
                           "app.py": "print('v0.2')\n", "workflow/policy.json": (p / "workflow" / "policy.json").read_text(),
                           "workflow/goals.json": (p / "workflow" / "goals.json").read_text()})
    (fake / "replies" / "1.md").write_text(_reply(["echo hello", "wf version"], ["echo 的输出", "工具版本"],
        "发布：auto-v0.2.zip\n之后：等待 2000-01-01T00:00:00+00:00 第二节点\n"
        "验收 1：c1 exit=0 包含 \"hello\"\n验收 2：c2 exit=0 包含 \"collab-wf\""), encoding="utf-8")
    (fake / "replies" / "1.files").write_text(str(z2))
    (fake / "replies" / "2.md").write_text(_reply(["echo step2", "rm -rf build", "false"], ["输出", "删除", "失败的命令"],
        "发布：无\n之后：回报\n验收 1：c1 exit=0 包含 \"step2\"\n验收 3：c3 exit=0"), encoding="utf-8")
    rc, o = _wf(env, p, "run")
    ev1 = p / "workflow" / "evidence" / "turn-001"
    v1 = json.loads((ev1 / "verify.json").read_text()) if (ev1 / "verify.json").exists() else {}
    _ok("自动完成一整轮：解析、评审、落地、执行、按判据验收、计划节点到期后自动发出下一条",
        rc == 10 and (p / "app.py").exists() and v1.get("1", {}).get("verdict") == "pass"
        and v1.get("2", {}).get("verdict") == "pass" and _state(p)["state"] == "WAIT_REPLY" and _state(p)["turn"] == 2
        and "计划节点" in sorted((fake / "inbox").glob("*.md"))[-1].read_text(), o)

    # 第 2 轮：含白名单外命令 → 停下等用户
    rc, o = _wf(env, p, "run")
    _ok("白名单外的命令：停下等用户（退出码 20），并说明需要批准什么", rc == 20 and _state(p)["state"] == "AWAITING_USER"
        and "rm -rf build" in o and "不在已批准的命令白名单" in o, o)
    rc, o = _wf(env, p, "explain")
    _ok("explain 回答卡在哪、为什么、需要谁做什么", "现在：" in o and "需要：用户" in o, o)
    (fake / "next_send_code").write_text("3")
    (fake / "replies" / "3.md").write_text(_reply(["cat done.txt"], ["done.txt 内容"],
        "发布：auto-v0.3.zip\n之后：回报\n验收 1：c1 exit=0 包含 \"ok\""), encoding="utf-8")
    z3 = tmp / "auto-v0.3.zip"
    _make_zip(z3, "auto", {"PROJECT.md": _project_md("v0.3"), "CHANGELOG.md": "# CHANGELOG\n\n## v0.3 — t\n- x\n",
                           "app.py": "print('v0.2')\n", "done.txt": "ok\n",
                           "workflow/policy.json": (p / "workflow" / "policy.json").read_text(),
                           "workflow/goals.json": (p / "workflow" / "goals.json").read_text()})
    (fake / "replies" / "3.files").write_text(str(z3))
    rc, o = _user(_wf, env, p, "approve", "approve")
    rc, o = _wf(env, p, "run")
    _ok("额度用尽：进入等待额度重置（本次唤醒到此为止，不连续重试）", rc == 10 and _state(p)["state"] == "QUOTA_WAIT", o)
    rc, o2 = _wf(env, p, "run")
    o += o2
    ev2 = p / "workflow" / "evidence" / "turn-002"
    v2 = json.loads((ev2 / "verify.json").read_text()) if (ev2 / "verify.json").exists() else {}
    events = [json.loads(l) for l in (p / "workflow" / "events.jsonl").read_text().splitlines()]
    _ok("用户批准后执行；失败自动回报；额度用尽自动等待并在重置后重发（不重复发送）",
        v2.get("3", {}).get("verdict") == "fail" and v2.get("2", {}).get("verdict") == "skip"
        and any(e.get("rule") == "QUOTA" for e in events) and _state(p)["state"] == "WAIT_REPLY"
        and _state(p)["turn"] == 3 and len(list((fake / "inbox").glob("*.md"))) == 3
        and _state(p).get("consecutive_failed") == 1, o)
    rc, o = _wf(env, p, "run")
    _ok("目标判据通过：自动进入目标达成（退出码 30）", rc == 30 and _state(p)["state"] == "DONE", o)
    rc, o = _wf(env, p, "goals")
    _ok("目标进度显示 1 / 1", "1 / 1" in o, o)
    rc, o = _wf(env, p, "audit")
    _ok("审计记录：自动处置、驱动、用户批准都可追溯", "自动处置" in o and "驱动" in o and "用户批准" in o, o[-600:])

    # 回退与暂停
    rc, o = _wf(env, p, "undo", "--note", "测试回退")
    _ok("undo：回退到上一个版本并暂停", rc == 0 and _state(p).get("paused") and not (p / "done.txt").exists(), o)
    rc, o = _wf(env, p, "run")
exit=0  耗时 0.0s
````

2) selftest_v15.py 全文。 —— 通过
```
$ cat /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_v15.py
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


TESTS = [("解析容错", _t_blocks), ("驱动指令", _t_directives), ("清单编号", _t_lists), ("证据关联", _t_link),
         ("回报原文", _t_report), ("分片", _t_chunks), ("回复归属", _t_attr), ("执行接续", _t_exec),
         ("桥接自检", _t_bridge), ("额度与事件", _t_usage_seq), ("升级核验", _t_receipt), ("文件口径", _t_tree),
         ("1.5.1 关联与处置", _t_v151), ("1.5.1 超时宽限", _t_grace), ("1.5.1 只读命令", _t_readonly),
         ("1.5.1 备注提示", _t_note), ("1.5.1 确认重发", _t_resend), ("1.5.1 批准与说明", _t_resume_info)]


def run_v15(_ok) -> None:
    print("  —— 1.5.x 新增检查 ——", flush=True)
    for name, fn in TESTS:
        try:
            fn(_ok)
        except Exception as e:   # 一组检查出错只记这一组失败，不中断整个自检
            _ok(f"1.5.0 检查组「{name}」运行出错", False, f"{type(e).__name__}: {e}")
exit=0  耗时 0.0s
```

3) projectmd.py 全文。 —— 通过
```
$ cat /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/projectmd.py
"""PROJECT.md 的固定结构：解析与校验（工作流级规则，所有项目通用）。"""
from __future__ import annotations

import re
from pathlib import Path

REQUIRED = ["当前版本", "目标", "已确认结论", "已排除方案及原因", "下一步",
            "待用户决策", "已知限制", "失败模式台账", "协作约定"]
VERSION_RE = re.compile(r"^(v\d+\.\d+)（(\d{4}-\d{2}-\d{2})）—— 阶段：(.+)$")
FAIL_HEADER = ["编号", "日期", "现象", "根因", "处置", "状态"]


def parse_project_md(text: str) -> dict:
    sections, order, cur = {}, [], None
    for line in text.splitlines():
        m = re.match(r"^## (.+?)\s*$", line)
        if m:
            cur = m.group(1)
            order.append(cur)
            sections[cur] = []
        elif cur:
            sections[cur].append(line)
    sections = {k: "\n".join(v).strip() for k, v in sections.items()}
    first = sections.get("当前版本", "").splitlines()[:1]
    vm = VERSION_RE.match(first[0].strip()) if first else None
    checklist = lambda body: [{"done": m.group(1).lower() == "x", "text": m.group(2).strip()}
                              for m in re.finditer(r"^\s*- \[( |x|X)\] (.+)$", body, re.M)]
    failures = []
    rows = [l for l in sections.get("失败模式台账", "").splitlines() if l.strip().startswith("|")]
    if len(rows) >= 2:
        hdr = [c.strip() for c in rows[0].strip("|").split("|")]
        for r in rows[2:]:
            cells = [c.strip() for c in r.strip("|").split("|")]
            failures.append(dict(zip(hdr, cells)))
    return {
        "order": order, "sections": sections,
        "version": vm.group(1) if vm else None, "date": vm.group(2) if vm else None,
        "phase": vm.group(3) if vm else None,
        "next_steps": checklist(sections.get("下一步", "")),
        "decisions": checklist(sections.get("待用户决策", "")),
        "failures": failures,
        "failure_header": [c.strip() for c in rows[0].strip("|").split("|")] if rows else [],
    }


def validate(p: dict, changelog_text=None) -> list:
    issues = []
    missing = [s for s in REQUIRED if s not in p["sections"]]
    if missing:
        issues.append(f"缺少章节：{'、'.join(missing)}")
    present = [s for s in p["order"] if s in REQUIRED]
    if present != [s for s in REQUIRED if s in present]:
        issues.append("章节顺序与规范不一致")
    for s in REQUIRED:
        if s in p["sections"] and not p["sections"][s]:
            issues.append(f"章节为空：{s}（没有内容请写“无”）")
    if not p["version"]:
        issues.append("“当前版本”第一行格式应为：v0.5（2026-10-09）—— 阶段：xxx")
    if "下一步" in p["sections"] and not p["next_steps"]:
        issues.append("“下一步”需要至少一个复选项（- [ ] 或 - [x]）")
    if p["failure_header"] and p["failure_header"] != FAIL_HEADER:
        issues.append(f"失败模式台账表头应为：{' | '.join(FAIL_HEADER)}")
    if changelog_text and p["version"]:
        m = re.search(r"^## (v\d+\.\d+)", changelog_text, re.M)
        if m and m.group(1) != p["version"]:
            issues.append(f"版本不一致：PROJECT.md 为 {p['version']}，CHANGELOG.md 最新为 {m.group(1)}")
    return issues


def validate_dir(root: Path) -> tuple:
    """返回 (解析结果, 问题列表)。"""
    root = Path(root)
    pm, cl = root / "PROJECT.md", root / "CHANGELOG.md"
    if not pm.exists():
        return None, ["缺少 PROJECT.md"]
    p = parse_project_md(pm.read_text(encoding="utf-8"))
    return p, validate(p, cl.read_text(encoding="utf-8") if cl.exists() else None)
exit=0  耗时 0.0s
```

③ 需要决策的点
无。

④ 需要注意（Attention）
- 当前对话已累计约 170509 字符、8 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

附：仓库指纹 4d1aa87b9b11c651（v0.1，4 个文件）
附：发送前额度 5 小时窗口 41.0%，7 天窗口 60.0%
