"""驱动器的端到端自检：用替身传输适配器模拟通道与 Claude，跑完整的多轮自动推进与各类异常。"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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

def _task_generation(_wf, env, proj, tid):
    """B2-A1：像执行端一样，先用 wf --json task 取该编号现在的代次，再带着它修改。
    取不到（没有这项、旧记录没有代次、输出不是 JSON）时返回 None；调用处仍会带一个对不上的代次，命令随后被拒绝，不改走旧记录。"""
    rc, o = _wf(env, proj, "--json", "task")
    try:
        data, _ = json.JSONDecoder().raw_decode(o[o.index("{"):])
    except ValueError:
        return None
    for t in data.get("tasks") or []:
        if str(t.get("id", "")).upper() == str(tid).upper() and t.get("generation"):
            return t["generation"]
    return None


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
    _ok("explain 回答卡在哪、为什么、需要谁做什么", "现在：" in o and "需要：执行端" in o, o)   # 1.6.5：与 inbox 路由一致（白名单外命令先交执行端）
    # IC-B1b-P1：批准评审不能绕过当前命令权限。先明确批准项目内清理，再继续原额度场景。
    pp = p / "workflow" / "policy.json"
    approved = json.loads(pp.read_text())
    approved["allow"].append(r"^rm -rf build$")
    approved["clean_dirs"] = ["build"]
    pp.write_text(json.dumps(approved))
    _user(_wf, env, p, "policy", "policy", "approve")
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
    quota_started = datetime.now(timezone.utc)
    rc, o = _wf(env, p, "run")
    quota_finished = datetime.now(timezone.utc)
    _ok("额度用尽：进入等待额度重置（本次唤醒到此为止，不连续重试）", rc == 10 and _state(p)["state"] == "QUOTA_WAIT", o)
    quota_state = _state(p)
    quota_hold_path = local / "quota_hold.json"
    quota_hold = json.loads(quota_hold_path.read_text())
    try:
        quota_until = datetime.fromisoformat(quota_state.get("until") or "")
    except (TypeError, ValueError):
        quota_until = None
    _ok("额度旧重置时刻不立即试探：首次等待约一小时，项目与全局等待一致",
        quota_until is not None and quota_until.tzinfo is not None
        and quota_started + timedelta(seconds=3599) <= quota_until <= quota_finished + timedelta(seconds=3601)
        and quota_hold.get("until") == quota_state.get("until"),
        json.dumps({"project_until": quota_state.get("until"), "global_until": quota_hold.get("until"),
                    "started": quota_started.isoformat(), "finished": quota_finished.isoformat()}, ensure_ascii=False))
    # IC-A2a-Q：仅推进临时夹具的两处等待时刻，模拟额度重置；保留其余状态和原有重发断言。
    quota_past = (quota_finished - timedelta(seconds=60)).isoformat()
    quota_state["until"] = quota_past
    quota_hold["until"] = quota_past
    (p / "workflow" / "state.json").write_text(json.dumps(quota_state))
    quota_hold_path.write_text(json.dumps(quota_hold))
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
    _ok("暂停时驱动器不做任何事（退出码 20）", rc == 20 and "暂停" in o, o)
    _user(_wf, env, p, "unpause", "unpause")

    # 白名单被改动 → 停
    pol = p / "workflow" / "policy.json"
    pol.write_text(pol.read_text().replace("^false$", "^.*$"))
    rc, o = _wf(env, p, "run")
    _ok("白名单在批准后被改动：立即停止自动执行", rc == 20 and "被改动" in o, o)
    subprocess.run(["git", "checkout", "--", "workflow/policy.json"], cwd=p)

    # 第二个项目：发送中途被打断的两种情况
    q = tmp / "auto2"
    _wf(env, tmp, "init", "--name", "auto2", "--root", str(q))
    (q / "workflow" / "policy.json").write_text((p / "workflow" / "policy.json").read_text())
    _user(_wf, env, q, "policy", "policy", "approve")
    _wf(env, q, "kick", "--message", str(task))
    st = json.loads((q / "workflow" / "state.json").read_text())
    st["state"] = "SEND"
    st["send_intent"] = {"turn": st["turn"], "status": "pending", "ts": "2026-01-01T00:00:00+00:00"}
    (q / "workflow" / "state.json").write_text(json.dumps(st))
    before = len(list((fake / "inbox").glob("*.md")))
    rc, o = _wf(env, q, "run")
    _ok("发送被打断且通道无迹象：不重发，安全停机（F14）", rc == 20 and _state(q)["state"] == "BLOCKED"
        and len(list((fake / "inbox").glob("*.md"))) == before and "SEND_UNKNOWN" in o, o)
    s = json.loads((fake / "status.json").read_text())
    s["turns"][f"auto2:{st['turn']}"] = {"k": 99, "polls": -10}
    (fake / "status.json").write_text(json.dumps(s))
    st = json.loads((q / "workflow" / "state.json").read_text())
    st.update(state="SEND", send_intent={"turn": st["turn"], "status": "pending", "ts": "2026-01-01T00:00:00+00:00",
                                         "ref": json.dumps({"key": f"auto2:{st['turn']}", "k": 99})})
    (q / "workflow" / "state.json").write_text(json.dumps(st))
    rc, o = _wf(env, q, "run")
    _ok("发送被打断但通道显示已在生成：确认为已发送，不重发", _state(q)["state"] == "WAIT_REPLY"
        and _state(q).get("send_ref") and len(list((fake / "inbox").glob("*.md"))) == before, o)

    # 执行中途被打断：不重跑那条命令
    st = json.loads((q / "workflow" / "state.json").read_text())
    tdir = q / "workflow" / "evidence" / f"turn-{st['turn']:03d}"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "intake.json").write_text(json.dumps({"commands": ["echo first", "echo second"], "checklist": ["x"],
                                                  "directives": {"after": "report", "checks": {}}}))
    st.update(state="EXECUTE", exec_inflight={"i": 0, "cmd": "echo first", "ts": "2026-01-01T00:00:00+00:00"},
              turn_flags={"review_accepted": True})
    (q / "workflow" / "state.json").write_text(json.dumps(st))
    rc, o = _wf(env, q, "run")
    outs = [x.read_text().strip() for x in sorted(tdir.glob("c*.out"))]
    _ok("执行被打断：不重跑那条命令，继续后续并记入回报", outs == ["second"] and "EXEC_INTERRUPTED" in
        (q / "workflow" / "events.jsonl").read_text(), o + str(outs))

    # 锁：同一项目不会被两个驱动器同时推进
    lockf = Path(env["WF_LOCAL_DIR"]) / "locks" / "auto2.lock"
    lockf.parent.mkdir(parents=True, exist_ok=True)
    with open(lockf, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        rc, o = _wf(env, q, "run")
    _ok("同一项目同时只有一个驱动器（退出码 10）", rc == 10 and "另一个驱动器" in o, o)

    # 多项目调度
    rc, o = _wf(env, tmp, "tick")
    _ok("tick 推进全部项目并汇总退出码", rc in (0, 10, 20, 30) and "auto：" in o and "auto2：" in o, o)


def run_v12(tmp: Path, env: dict, _wf, _ok, _state, _project_md, _make_zip):
    """1.2 新增：门禁回归自动回退、全局限速、对话体量上限、独立审查、适配器一致性检查、决策与复盘。"""
    print("  —— 门禁、限速、对话体量、独立审查 ——", flush=True)
    fake = tmp / "fake"
    local = Path(env["WF_LOCAL_DIR"])
    tcfg = json.loads((local / "transport.json").read_text())

    def next_k():
        st = json.loads((fake / "status.json").read_text()) if (fake / "status.json").exists() else {"accepted": 0}
        return st["accepted"] + 1

    def setup(name, pol, files=None):
        root = tmp / name
        _wf(env, tmp, "init", "--name", name, "--root", str(root))
        for rel, txt in (files or {}).items():
            (root / rel).write_text(txt)
        (root / "workflow" / "policy.json").write_text(json.dumps(pol))
        subprocess.run(["git", "init", "-q"], cwd=root)
        subprocess.run(["git", "add", "-A"], cwd=root)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "release v0.1"], cwd=root)
        subprocess.run(["git", "tag", "v0.1"], cwd=root)
        _user(_wf, env, root, "policy", "policy", "approve")
        return root

    base_pol = {"allow": [r"^echo "], "first_poll_after_s": 0, "poll_interval_s": 0}
    task = tmp / "task12.md"
    task.write_text("任务 v0.1。档位：high\n", encoding="utf-8")

    # A. 门禁：落地前通过、落地后失败 → 自动回退并回报
    g = setup("gate", {**base_pol, "gates": [{"name": "app-ok", "cmd": "cat app.py", "pass": {"contains": ["ok"]}}]},
              {"app.py": "print('ok')\n"})
    _wf(env, g, "kick", "--message", str(task))
    _wf(env, g, "run")
    z = tmp / "gate-v0.2.zip"
    _make_zip(z, "gate", {"PROJECT.md": _project_md("v0.2"), "CHANGELOG.md": "# CHANGELOG\n\n## v0.2 — t\n- x\n",
                          "app.py": "print('bad')\n", "workflow/policy.json": (g / "workflow" / "policy.json").read_text()})
    k = next_k() - 1
    (fake / "replies" / f"{k}.md").write_text(_reply(["echo after"], ["输出"], "发布：gate-v0.2.zip\n之后：回报\n验收 1：c1 exit=0"),
                                              encoding="utf-8")
    (fake / "replies" / f"{k}.files").write_text(str(z))
    rc, o = _wf(env, g, "run")
    tags = subprocess.run(["git", "tag"], cwd=g, capture_output=True, text=True).stdout
    dec = (g / "workflow" / "decisions.jsonl").read_text() if (g / "workflow" / "decisions.jsonl").exists() else ""
    last_msg = sorted((fake / "inbox").glob("*gate*.md"))[-1].read_text()
    _ok("门禁回归：自动回退到上一版本、改名版本标签、记入决策并在回报的“需要注意”中说明",
        (g / "app.py").read_text() == "print('ok')\n" and "v0.2-reverted-" in tags and "\nv0.2\n" not in "\n" + tags
        and '"phase": "revert"' in dec and "GATE_REGRESSION" in last_msg and "④ 需要注意" in last_msg
        and _state(g)["state"] == "WAIT_REPLY", o + tags)

    # B. 对话体量上限 → 停下，自动生成恢复包；开新对话后继续
    c = setup("ctx", {**base_pol, "max_conversation_turns": 1})
    _wf(env, c, "kick", "--message", str(task))
    _wf(env, c, "run")
    k = next_k() - 1
    (fake / "replies" / f"{k}.md").write_text(_reply(["echo hi"], ["输出"], "发布：无\n之后：回报\n验收 1：c1 exit=0"), encoding="utf-8")
    rc, o = _wf(env, c, "run")
    boots = list((c / "workflow" / "outbox").glob("bootstrap-*.md"))
    _ok("对话体量达到上限：停下等用户，并已生成新对话恢复包", rc == 20 and _state(c)["state"] == "AWAITING_USER"
        and "CONTEXT_FULL" in o and boots, o)
    had_ref = bool(_state(c).get("send_ref"))
    _wf(env, c, "conv", "new", "--note", "测试：已开新对话")
    _ok("1.6.9（N10）：手工 wf conv new 清掉旧对话的 send_ref，新对话首轮不再拿它当 --prev-ref",
        had_ref and not _state(c).get("send_ref"), json.dumps(_state(c).get("conv"), ensure_ascii=False))
    _user(_wf, env, c, "approve", "approve")
    rc, o = _wf(env, c, "run")
    _ok("开新对话并批准后继续发送", _state(c)["state"] == "WAIT_REPLY" and (_state(c).get("conv") or {}).get("turns") == 1, o)

    # B2. 1.6.4：policy 开启 auto_new_conversation 时，对话到上限由驱动器自己开新对话（适配器 conv_new），不再停下等人
    cnw = tmp / "t-conv_new"
    cnw.write_text(f"#!/bin/sh\nexec {__import__('shlex').quote(sys.executable)} {__import__('shlex').quote(str(tmp / 'ft.py'))} conv_new \"$@\"\n")
    cnw.chmod(0o755)
    (local / "transport.json").write_text(json.dumps({**tcfg, "conv_new": str(cnw)}))

    def _manual(root, turn):
        bad = []
        for line in (root / "workflow" / "events.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("turn") == turn and e.get("by") not in ("driver", "wf") \
                    and not (e.get("type") == "note" and e.get("kind") == "info"):
                bad.append(f"{e.get('type')} by={e.get('by')}")
        return bad

    a2 = setup("ctxauto", {**base_pol, "max_conversation_turns": 1, "auto_new_conversation": True})
    _wf(env, a2, "kick", "--message", str(task))
    _wf(env, a2, "run")
    k = next_k() - 1
    (fake / "replies" / f"{k}.md").write_text(_reply(["echo hi"], ["输出"], "发布：无\n之后：回报\n验收 1：c1 exit=0"), encoding="utf-8")
    rc, o = _wf(env, a2, "run")
    s2 = _state(a2)
    t2 = s2.get("turn") or 0
    sent2 = sorted((fake / "inbox").glob("*-ctxauto-*.md"))
    last2 = sent2[-1].read_text(encoding="utf-8") if sent2 else ""
    _ok("1.6.4 对话到上限且开启 auto_new_conversation：驱动器调用 conv_new、计数归零、发出续接消息（附本轮回报），这一轮没有人工事件",
        s2["state"] == "WAIT_REPLY" and (s2.get("conv") or {}).get("by") == "driver" and (s2.get("conv") or {}).get("turns") == 1
        and len(sent2) == 2 and "项目续接" in last2 and "【回报 · 第" in last2 and not _manual(a2, t2)
        and (a2 / "workflow" / "outbox" / f"turn-{t2:03d}-report.md").exists(), o + str(_manual(a2, t2)))

    # B3. 1.6.4：conv_new 失败时退回原来的做法：停下等人、写明原因，计数与本轮回报都不变
    (fake / "conv_new_fail").write_text("1")
    a3 = setup("ctxfail", {**base_pol, "max_conversation_turns": 1, "auto_new_conversation": True})
    _wf(env, a3, "kick", "--message", str(task))
    _wf(env, a3, "run")
    k = next_k() - 1
    (fake / "replies" / f"{k}.md").write_text(_reply(["echo hi"], ["输出"], "发布：无\n之后：回报\n验收 1：c1 exit=0"), encoding="utf-8")
    rc, o = _wf(env, a3, "run")
    s3 = _state(a3)
    rep3 = a3 / "workflow" / "outbox" / f"turn-{(s3.get('turn') or 0):03d}.md"
    _ok("1.6.4 conv_new 失败：退回停下等人（CONTEXT_FULL）并写明原因，计数与本轮回报不变",
        rc == 20 and s3["state"] == "AWAITING_USER" and "CONTEXT_FULL" in o and "自动开新对话未成功" in o
        and (s3.get("conv") or {}).get("turns") == 1 and not s3.get("conv_switch")
        and rep3.exists() and rep3.read_text(encoding="utf-8").startswith("【回报"), o)
    (local / "transport.json").write_text(json.dumps(tcfg))

    # B4. 1.7.0（ISOLATION.md S1）：没声明 per_project_conv 时任何调用都不带 --conv（兼容旧适配器）；声明后，
    #     自动开新对话带 --project，之后的发送与轮询带适配器返回的对话；适配器的 warning 进“需要注意”
    calls_f = fake / "calls.jsonl"
    n0 = len(calls_f.read_text(encoding="utf-8").splitlines()) if calls_f.exists() else 0
    before = [json.loads(x) for x in calls_f.read_text(encoding="utf-8").splitlines()] if calls_f.exists() else []
    (local / "transport.json").write_text(json.dumps({**tcfg, "conv_new": str(cnw), "per_project_conv": True}))
    (fake / "poll_warning").write_text("conv_from_default")
    a4 = setup("ctxconv", {**base_pol, "max_conversation_turns": 1, "auto_new_conversation": True})
    _wf(env, a4, "kick", "--message", str(task))
    _wf(env, a4, "run")
    k = next_k() - 1
    (fake / "replies" / f"{k}.md").write_text(_reply(["echo hi"], ["输出"], "发布：无\n之后：回报\n验收 1：c1 exit=0"), encoding="utf-8")
    rc, o = _wf(env, a4, "run")
    s4 = _state(a4)
    cid = (s4.get("conv") or {}).get("conv")

    def _proj(c):
        a = c.get("argv") or []
        return a[a.index("--project") + 1] if "--project" in a and a.index("--project") + 1 < len(a) else None

    def _conv(c):
        a = c.get("argv") or []
        return a[a.index("--conv") + 1] if "--conv" in a and a.index("--conv") + 1 < len(a) else None
    calls = [json.loads(x) for x in calls_f.read_text(encoding="utf-8").splitlines()][n0:]
    mine = [c for c in calls if _proj(c) == "ctxconv"]
    sends = [c for c in mine if c["op"] == "send"]
    cn = [c for c in mine if c["op"] == "conv_new"]
    polls_after = [c for c in mine[mine.index(cn[0]) + 1:] if c["op"] == "poll"] if cn else []
    warns = []
    for line in (a4 / "workflow" / "events.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "auto" and e.get("rule") == "ADAPTER_WARNING":
            warns.append((e.get("turn"), e.get("detail")))
    _ok("1.7.0 per_project_conv：之前的调用都不带 --conv；声明后 conv_new 带 --project，换对话后的发送与轮询带新对话；"
        "适配器告警进“需要注意”",
        not any("--conv" in (c.get("argv") or []) for c in before)
        and cid and len(cn) == 1 and len(sends) == 2 and _conv(sends[0]) is None and _conv(sends[1]) == cid
        and polls_after and all(_conv(c) == cid for c in polls_after)
        and warns and all(w[1] == "poll：conv_from_default" for w in warns), json.dumps([cid, mine[-6:], warns], ensure_ascii=False)[:900])

    # B5. 1.7.0：wf conv bind 只登记对话（计数与 send_ref 不变）；wf conv new --conv 登记新对话、计数归零、清 send_ref
    t_before = (s4.get("conv") or {}).get("turns")
    ref_before = s4.get("send_ref")
    _wf(env, a4, "conv", "bind", "--conv", "conv-manual", "--note", "测试：迁移登记")
    s5 = _state(a4)
    _wf(env, a4, "conv", "new", "--conv", "conv-new2", "--note", "测试：手工换对话")
    s6 = _state(a4)
    rc7, o7 = _wf(env, a4, "conv")
    _ok("1.7.0 wf conv bind 只登记对话（计数、send_ref 不变）；wf conv new --conv 登记新对话并归零、清 send_ref；wf conv 显示对话 id",
        (s5.get("conv") or {}).get("conv") == "conv-manual" and (s5.get("conv") or {}).get("turns") == t_before
        and s5.get("send_ref") == ref_before and (s6.get("conv") or {}).get("conv") == "conv-new2"
        and (s6.get("conv") or {}).get("turns") == 0 and not s6.get("send_ref") and "conv-new2" in o7,
        json.dumps([s5.get("conv"), s6.get("conv"), o7[-200:]], ensure_ascii=False))
    (fake / "poll_warning").unlink()
    (local / "transport.json").write_text(json.dumps(tcfg))

    # B4. 1.6.5（W42）：上一条回复刚落地时不立即发下一条，等满 min_send_gap_s
    gp = setup("gap", {**base_pol, "min_send_gap_s": 3600})
    _wf(env, gp, "kick", "--message", str(task))
    _wf(env, gp, "run")
    k = next_k() - 1
    (fake / "replies" / f"{k}.md").write_text(_reply(["echo hi"], ["输出"], "发布：无\n之后：回报\n验收 1：c1 exit=0"), encoding="utf-8")
    rc, o = _wf(env, gp, "run")
    sg = _state(gp)
    _ok("1.6.5 发送间隔：上一条回复刚落地时不立即发下一条，等满 min_send_gap_s（W42）",
        sg["state"] == "SEND" and bool(sg.get("replied_at")) and bool(sg.get("retry_at")) and "等满" in o, o)

    # B5. 1.6.6：写给执行端的待办可追踪：之后为空闲时不进空闲，等执行端 wf task done 后自动回报
    et = setup("etask", base_pol)
    _wf(env, et, "kick", "--message", str(task))
    _wf(env, et, "run")
    k = next_k() - 1
    (fake / "replies" / f"{k}.md").write_text(_reply(["echo hi"], ["输出"],
        "发布：无\n执行端 1：测试：把看门狗接上\n之后：空闲\n验收 1：c1 exit=0"), encoding="utf-8")
    rc, o = _wf(env, et, "run")
    waiting = _state(et)["state"] == "WAIT_SCHEDULE"
    rc, oa = _wf(env, et, "--json", "ask")
    g6 = _task_generation(_wf, env, et, "T1-1")   # B2-A1：新记录必须带它现在的代次
    rc6, o6 = _wf(env, et, "task", "done", "T1-1", "--generation", g6 or "未取到代次", "--note", "测试：已接上")
    rc7, o7 = _wf(env, et, "run")
    sent5 = sorted((fake / "inbox").glob("*-etask-*.md"))
    last5 = sent5[-1].read_text(encoding="utf-8") if sent5 else ""
    _ok("1.6.6 执行端待办：之后为空闲时不进空闲而是等待、待办进 inbox；wf task done 后驱动器自动生成并发出回报，回报带完成说明",
        waiting and "task:T1-1" in oa and rc6 == 0 and _state(et)["state"] == "WAIT_REPLY"
        and "测试：已接上" in last5 and "T1-1" in last5, o + oa + o6 + o7)

    # B6. 1.6.6：执行端待办到最迟时间仍未完成：照常生成回报，并写明未完成的待办
    et2 = setup("etask2", {**base_pol, "executor_task_timeout_s": 1})
    _wf(env, et2, "kick", "--message", str(task))
    _wf(env, et2, "run")
    k = next_k() - 1
    (fake / "replies" / f"{k}.md").write_text(_reply(["echo hi"], ["输出"],
        "发布：无\n执行端 1：测试：一件没做完的事\n之后：等执行端\n验收 1：c1 exit=0"), encoding="utf-8")
    rc, o = _wf(env, et2, "run")
    w2 = _state(et2)["state"] == "WAIT_SCHEDULE"
    __import__("time").sleep(1.5)
    rc, o2 = _wf(env, et2, "run")
    sent6 = sorted((fake / "inbox").glob("*-etask2-*.md"))
    last6 = sent6[-1].read_text(encoding="utf-8") if sent6 else ""
    _ok("1.6.6 执行端待办到最迟时间仍未完成：照常生成回报并写明未完成的待办",
        w2 and _state(et2)["state"] == "WAIT_REPLY" and "仍未完成" in last6 and "T1-1" in last6, o + o2)

    # B7. 1.6.7（第 80、81 轮）：目标已达成时执行端待办仍登记进 inbox；DONE 不变、不发送；做完有提示；无待办时行为不变
    gj7 = json.dumps({"goals": [{"id": "G1", "title": "已交付", "kind": "exec", "check": "cat done.txt",
                                 "pass": {"exit": 0, "contains": ["ok"]}, "means": "完成", "on_fail": "继续"}]}, ensure_ascii=False)
    pol7 = {**base_pol, "allow": [r"^echo ", r"^cat "]}

    def _done_setup(name):
        root = setup(name, pol7, {"done.txt": "ok\n", "workflow/goals.json": gj7})
        _user(_wf, env, root, "goals", "goals", "approve")
        _wf(env, root, "kick", "--message", str(task))
        _wf(env, root, "run")
        return root

    def _sent(name):
        return len(sorted((fake / "inbox").glob(f"*-{name}-*.md")))

    dt = _done_setup("dtask")
    k = next_k() - 1
    (fake / "replies" / f"{k}.md").write_text(_reply(["echo hi"], ["输出"],
        "发布：无\n执行端 1：测试：目标达成后的待办\n之后：等执行端\n验收 1：c1 exit=0"), encoding="utf-8")
    n7 = _sent("dtask")
    rc7, o7 = _wf(env, dt, "run")
    s7 = _state(dt)
    tid7 = f"T{s7.get('turn')}-1"
    rc, oa7 = _wf(env, dt, "--json", "ask")
    ev7 = (dt / "workflow" / "events.jsonl").read_text(encoding="utf-8")
    _ok("1.6.7 ① 目标已达成时执行端待办仍登记（T<轮次>-1、tasks_opened 事件）并进 inbox（wf ask 可见 task:<编号>）",
        any(t.get("id") == tid7 and not t.get("done") for t in s7.get("executor_tasks") or [])
        and f"task:{tid7}" in oa7 and '"tasks_opened"' in ev7, o7 + oa7)
    rc, oe7 = _wf(env, dt, "explain")
    _ok("1.6.7 ② 待办登记不改变目标达成：仍进入 DONE（退出码 30），不等待、不发送；explain 先说待办、交给执行端",
        rc7 == 30 and s7["state"] == "DONE" and s7.get("await_executor") is None and _sent("dtask") == n7
        and "需要：执行端" in oe7 and "执行端待办" in oe7, o7 + oe7)
    g8 = _task_generation(_wf, env, dt, tid7)   # B2-A1：新记录必须带它现在的代次
    rc8, o8 = _wf(env, dt, "task", "done", tid7, "--generation", g8 or "未取到代次", "--note", "测试：DONE 下已做完")
    rc9, o9 = _wf(env, dt, "run")
    rc, oa9 = _wf(env, dt, "--json", "ask")
    _ok("1.6.7 ③ DONE 下待办做完：wf task done 说明不会自动回报，结果写进“目标已达成”提示；仍停在 DONE、不发送",
        rc8 == 0 and "不会自动回报" in o8 and rc9 == 30 and _state(dt)["state"] == "DONE"
        and "测试：DONE 下已做完" in oa9 and f"task:{tid7}" not in oa9 and _sent("dtask") == n7, o8 + o9 + oa9)
    # B7⑤ 1.7.1：DONE 下 wf kick --kind report 不带 --message：由证据生成标准回报（含 ② 原文、执行端备注与待办结果），驱动器接着发出
    rc13, o13 = _wf(env, dt, "kick")
    _wf(env, dt, "note", "--kind", "info", "测试：执行端的说明")
    rc11, o11 = _wf(env, dt, "kick", "--kind", "report")
    t11 = _state(dt).get("turn") or 0
    rep11 = dt / "workflow" / "outbox" / f"turn-{t11:03d}.md"
    txt11 = rep11.read_text(encoding="utf-8") if rep11.exists() else ""
    rc12, o12 = _wf(env, dt, "run")
    _ok("1.7.1 DONE 下 wf kick --kind report：由证据生成标准回报（② 原文、执行端说明、待办结果），驱动器接着发出；"
        "任务类 kick 仍必须带 --message",
        rc11 == 0 and txt11.startswith("【回报 · 第") and "② 关键数字与终端原文" in txt11 and "echo hi" in txt11
        and "测试：执行端的说明" in txt11 and "测试：DONE 下已做完" in txt11 and "④ 需要注意" in txt11
        and _sent("dtask") == n7 + 1 and _state(dt)["state"] in ("WAIT_REPLY", "SEND") and rc13 != 0 and "--message" in o13,
        o11 + o12 + o13 + txt11[:600])
    dn = _done_setup("dnone")
    k = next_k() - 1
    (fake / "replies" / f"{k}.md").write_text(_reply(["echo hi"], ["输出"], "发布：无\n之后：空闲\n验收 1：c1 exit=0"),
                                              encoding="utf-8")
    rc10, o10 = _wf(env, dn, "run")
    rc, oa10 = _wf(env, dn, "--json", "ask")
    rc, oe10 = _wf(env, dn, "explain")
    _ok("1.6.7 ④ 没有执行端待办时目标达成的行为不变：DONE、无 tasks_opened 事件、提示原文不变、explain 仍交给用户",
        rc10 == 30 and _state(dn)["state"] == "DONE" and not _state(dn).get("executor_tasks")
        and '"tasks_opened"' not in (dn / "workflow" / "events.jsonl").read_text(encoding="utf-8")
        and "全部目标判据已通过，自动化停在这里" in oa10 and "本轮执行端待办" not in oa10 and "需要：用户" in oe10,
        o10 + oa10 + oe10)

    # B8. 1.6.8：目标判据通过、但本轮验收未通过：不进入 DONE，照常回报；待办照样登记；下一轮干净时照常进入 DONE
    df = _done_setup("dfail")
    k = next_k() - 1
    (fake / "replies" / f"{k}.md").write_text(_reply(["echo hi"], ["输出"],
        "发布：无\n执行端 1：测试：失败轮的待办\n之后：空闲\n验收 1：c1 exit=0 包含 \"不会出现的字样\""), encoding="utf-8")
    n8 = _sent("dfail")
    rc8, o8 = _wf(env, df, "run")
    s8 = _state(df)
    last8 = sorted((fake / "inbox").glob("*-dfail-*.md"))[-1].read_text(encoding="utf-8")
    dec8 = (df / "workflow" / "decisions.jsonl").read_text(encoding="utf-8")
    _ok("1.6.8 目标判据通过但本轮验收未通过：不进入 DONE、照常发出回报（写明未通过的清单项），待办照样登记，决策日志说明原因",
        s8["state"] == "WAIT_REPLY" and _sent("dfail") == n8 + 1 and "清单第 1 项" in last8
        and any(str(t.get("id", "")).endswith("-1") for t in s8.get("executor_tasks") or []) and "先回报，不进入目标达成" in dec8,
        o8 + last8[:400])
    k = next_k() - 1
    (fake / "replies" / f"{k}.md").write_text(_reply(["echo hi"], ["输出"], "发布：无\n之后：空闲\n验收 1：c1 exit=0 包含 \"hi\""),
                                              encoding="utf-8")
    rc9, o9 = _wf(env, df, "run")
    _ok("1.6.8 下一轮干净且目标仍达成：照常进入 DONE（退出码 30）", rc9 == 30 and _state(df)["state"] == "DONE", o9)

    # C. 独立审查适配器认为应阻止 → 停下等用户
    rv = tmp / "t-review"
    rv.write_text('#!/bin/sh\necho \'{"verdict": "block", "model": "test-reviewer", "notes": ["测试：发现问题"]}\'\n')
    rv.chmod(0o755)
    (local / "transport.json").write_text(json.dumps({**tcfg, "review": str(rv)}))
    r = setup("rev", base_pol)
    _wf(env, r, "kick", "--message", str(task))
    _wf(env, r, "run")
    z = tmp / "rev-v0.2.zip"
    _make_zip(z, "rev", {"PROJECT.md": _project_md("v0.2"), "CHANGELOG.md": "# CHANGELOG\n\n## v0.2 — t\n- x\n",
                         "app.py": "print(1)\n", "workflow/policy.json": (r / "workflow" / "policy.json").read_text()})
    k = next_k() - 1
    (fake / "replies" / f"{k}.md").write_text(_reply(["echo x"], ["输出"], "发布：rev-v0.2.zip\n之后：回报"), encoding="utf-8")
    (fake / "replies" / f"{k}.files").write_text(str(z))
    rc, o = _wf(env, r, "run")
    _ok("独立审查认为应阻止：不落地，停下等用户并注明审查模型", rc == 20 and "test-reviewer" in o
        and not (r / "app.py").exists(), o)
    (local / "transport.json").write_text(json.dumps(tcfg))

    # D. 全局限速
    (local / "transport.json").write_text(json.dumps({**tcfg, "rate": {"window_s": 600, "max_sends": 1}}))
    q = setup("rate", base_pol)
    _wf(env, q, "kick", "--message", str(task))
    rc, o = _wf(env, q, "run")
    _ok("全局限速：窗口内已达上限时推迟发送（退出码 10）", rc == 10 and "全局限速" in o and _state(q)["state"] == "SEND", o)
    (local / "transport.json").write_text(json.dumps(tcfg))

    # E. 适配器一致性检查（不发送消息）
    blog = tmp / "bridge.jsonl"
    blog.write_text(json.dumps({"ts": "2099-01-01T00:00:00+00:00", "action": "send"}) + "\n"
                    + json.dumps({"ts": "2099-01-01T00:00:01+00:00", "action": "accepted"}) + "\n"
                    + json.dumps({"ts": "2099-01-01T00:10:00+00:00", "action": "done"}) + "\n")
    (local / "transport.json").write_text(json.dumps({**tcfg, "log": str(blog)}))
    rc, o = _wf(env, tmp, "transport", "test")
    _ok("适配器一致性检查：poll 对不存在的轮次返回 none 或 no_ref、幂等、quota 格式、动作日志合规", rc == 0 and "✗" not in o, o)
    (local / "transport.json").write_text(json.dumps(tcfg))

    # E1. 1.6.4：空 ref 的 poll 返回 no_ref 也算合规（TRANSPORT.md 1.5.1 的约定；此前一致性检查误判为 ✗）
    (local / "transport.json").write_text(json.dumps({**tcfg, "log": str(blog),
                                                      "env": {**(tcfg.get("env") or {}), "FAKE_NO_REF": "1"}}))
    rc, o = _wf(env, tmp, "transport", "test")
    _ok("1.6.4 适配器一致性检查：空 ref 的 poll 返回 no_ref 视为合规", rc == 0 and "✓ poll 对不存在的轮次返回 none 或 no_ref" in o, o)
    (local / "transport.json").write_text(json.dumps(tcfg))

    # E2. 会改变工作流状态的 wf 命令：驱动器不执行，用户批准后也只跳过（F21）
    m = setup("manual", {**base_pol, "allow": [r"^echo ", r"^wf (version|apply|pin)( |$)"]})
    _wf(env, m, "kick", "--message", str(task))
    _wf(env, m, "run")
    k = next_k() - 1
    (fake / "replies" / f"{k}.md").write_text(_reply(["wf version", "wf pin", "echo done"], ["版本", "固定", "完成"],
        "发布：无\n之后：空闲\n验收 1：c1 exit=0 包含 \"collab-wf\"\n验收 3：c3 exit=0 包含 \"done\""), encoding="utf-8")
    rc, o = _wf(env, m, "run")
    stop_ok = rc == 20 and "wf pin" in o and "手动执行" in o
    _user(_wf, env, m, "approve", "approve")
    rc, o2 = _wf(env, m, "run")
    ev = m / "workflow" / "evidence" / f"turn-{_state(m)['turn']:03d}"
    cmds = [json.loads(x.read_text())["cmd"] for x in sorted(ev.glob("c*.json"))]
    _ok("会改变工作流状态的 wf 命令：即使在白名单中也停下；批准后跳过不执行，其余照常（F21）",
        stop_ok and cmds == ["wf version", "echo done"] and _state(m)["state"] == "IDLE", o + o2 + str(cmds))

    # E3. 缺口 7：手动发出的轮次，驱动器凭 --ref 接续；没有 ref 则明确停下
    h = setup("handoff", base_pol)
    import subprocess as _sp
    # 没有 ref：驱动器停下说明，而不是误判“看不到”
    _wf(env, h, "go", "COMPOSE")
    msgf = tmp / "hmsg.md"; msgf.write_text("任务。档位：high\n", encoding="utf-8")
    _wf(env, h, "mark", "sent", "--message-file", str(msgf))
    rc, o = _wf(env, h, "run")
    _ok("手动发送且无 ref：驱动器明确停下（NO_REF），不误判为看不到", rc == 20 and "NO_REF" in o
        and _state(h)["state"] in ("AWAITING_USER", "BLOCKED"), o)
    # 有 ref：驱动器接续手动发出的轮次（直接把该轮在 echo 适配器里登记为“可返回 done”）
    h2 = setup("handoff2", base_pol)
    _wf(env, h2, "go", "COMPOSE")
    st = json.loads((h2 / "workflow" / "state.json").read_text())
    s2 = json.loads((fake / "status.json").read_text()); s2["accepted"] += 1
    k2 = s2["accepted"]; key2 = f"handoff2:{st['turn']}"
    s2["turns"][key2] = {"k": k2, "polls": 5}   # polls >= 1 → 下一次 poll 直接 done
    (fake / "status.json").write_text(json.dumps(s2))
    (fake / "replies" / f"{k2}.md").write_text(_reply(["echo ok"], ["输出"], "发布：无\n之后：空闲\n验收 1：c1 exit=0 包含 \"ok\""), encoding="utf-8")
    _wf(env, h2, "mark", "sent", "--message-file", str(msgf), "--ref", json.dumps({"key": key2, "k": k2}))
    rc, o = _wf(env, h2, "run")
    _ok("手动发送且带 ref：驱动器接续轮询、收到回复并继续", "收到回复" in o
        and _state(h2)["state"] in ("IDLE", "WAIT_SCHEDULE", "DONE"), o)


    # F. 决策日志与档位复盘
    rc, o = _wf(env, g, "decisions", "--tsv")
    _ok("决策日志可导出为 TSV（ts、轮次、阶段、决定、理由、证据、结果）", rc == 0 and o.startswith("ts\tturn\tphase") and "revert" in o, o[:400])
    rc, o = _wf(env, g, "effort")
    _ok("档位规则与复盘统计", rc == 0 and "预算" in o and "按档位" in o, o)
    rc, o = _wf(env, tmp, "schedule", "status")
    _ok("定时器状态检查：列出各项目驱动器最近一次运行", "最近一次运行" in o, o)

    # H. 1.4：聊天窗口内联面板 wf panel
    rc, o = _wf(env, h, "panel")   # h 此时处于暂停
    import html.parser as _hp
    class _P(_hp.HTMLParser):
        def __init__(s): super().__init__(); s.n=0; s.text=""
        def handle_starttag(s,t,a): s.n+=1
        def handle_data(s,d): s.text+=d
    pp=_P(); pp.feed(o)
    _ok("wf panel 生成合法 HTML 小组件，含项目名、状态与暂停待办的决定码",
        rc==0 and o.strip().startswith("<div") and pp.n>5 and "panel" not in o.lower()[:50]
        and h.name in o and ("继续" in o or "暂停" in o), o[:200])


    # G. 1.3：send 只有心跳 → 不当作已接受；叶子被拒 → 回退重发；手动 ref → 驱动器可接管
    import importlib
    ft_src = (WF_HOME / "wflib" / "fake_transport.py").read_text()
    # 变体适配器：send 读 fake/send_mode 决定返回 heartbeat_only / refused-on-poll / normal
    ft2 = tmp / "ft2.py"
    ft2.write_text(ft_src.replace(
        '''        print(json.dumps({"ref": f"r-{turn}"}))
        return''',
        '''        mode = (D / "send_mode").read_text().strip() if (D / "send_mode").exists() else ""
'''
        '''        if mode == "heartbeat":
            print(json.dumps({"status": "heartbeat_only"})); sys.exit(2)
'''
        '''        print(json.dumps({"ref": f"r-{turn}", "accepted": True}))
        return''').replace(
        '''        if t["polls"] < 2:
            print(json.dumps({"status": "generating"}))
            return''',
        '''        if (D / "poll_refused").exists():
            print(json.dumps({"status": "refused", "parent_ref": "healthy-1"})); return
        if (D / "poll_never").exists():
            print(json.dumps({"status": "none"})); return
'''
        '''        if t["polls"] < 2:
            print(json.dumps({"status": "generating"}))
            return'''))
    ft2.chmod(0o755)
    for op in ("send", "poll", "quota"):
        (tmp / f"t2-{op}").write_text(f"#!/bin/sh\nexec {sys.executable} {ft2} {op} \"$@\"\n")
        (tmp / f"t2-{op}").chmod(0o755)
    (local / "transport.json").write_text(json.dumps({**tcfg, "send": str(tmp / "t2-send"),
                                                      "poll": str(tmp / "t2-poll"), "quota": str(tmp / "t2-quota")}))
    h = setup("hb", base_pol)
    (fake / "send_mode").write_text("heartbeat")
    _wf(env, h, "kick", "--message", str(task))
    rc, o = _wf(env, h, "run")
    # 1.8.9（W84）改了这条旧断言：只有心跳不再按“通道忙”退避重发，而是保留意图、按结果不明等待确认（仍不当作已接受）
    _ok("send 只有心跳：不当作已接受；1.8.9 起保留发送意图、按结果不明等待确认，不重发（缺口 11-3、F22、W84）",
        _state(h)["state"] in ("SEND", "BLOCKED") and (_state(h).get("send_intent") or {}).get("status") == "pending"
        and "不重发" in o, o)
    (fake / "send_mode").unlink()
    r = setup("refuse", base_pol)
    _wf(env, r, "kick", "--message", str(task))
    _wf(env, r, "run")
    (fake / "poll_refused").write_text("1")
    rc, o = _wf(env, r, "run")
    _ok("叶子被安全防护拒绝：回退并准备重发（§10）", "回退" in o or "LEAF_REFUSED" in o, o)
    (fake / "poll_refused").unlink()
    dec = (r / "workflow" / "decisions.jsonl").read_text() if (r / "workflow" / "decisions.jsonl").exists() else ""
    _ok("叶子被拒记入决策日志（recover）", '"phase": "recover"' in dec, dec[-300:])

    # C2. 独立落地核对（缺口 11/§3）：send 返回 0，但消息始终不出现在会话 → 连续 none 后停下
    (local / "transport.json").write_text(json.dumps({**tcfg, "send": str(tmp / "t2-send"),
                                                      "poll": str(tmp / "t2-poll"), "quota": str(tmp / "t2-quota")}))
    nl = setup("notland", {**base_pol, "landed_confirm_polls": 2, "landed_poll_s": 0})
    (fake / "poll_never").write_text("1")
    _wf(env, nl, "kick", "--message", str(task))
    rc1, o1 = _wf(env, nl, "run")   # 发送（心跳适配器已关，这次正常 accepted）→ WAIT_REPLY
    rc2, o2 = _wf(env, nl, "run")   # 第 1 次 poll：none
    rc3, o3 = _wf(env, nl, "run")   # 第 2 次 poll：none → 达到上限 → 阻塞
    (fake / "poll_never").unlink()
    _ok("独立落地核对：send 报已接受但消息始终不出现 → 连续 none 后停下（缺口 11/§3）",
        _state(nl)["state"] == "BLOCKED" and "SEND_NOT_LANDED" in (o1 + o2 + o3), o2 + o3)
    # C2b. 1.6.5（W43）：没落地的消息用 wf resend 恢复并重发；执行端代决（--by executor --reason），如实记 by=executor
    rc, oa = _wf(env, nl, "--json", "ask")
    try:
        nl_items = json.loads(oa[oa.index("{"):])["items"]
    except (ValueError, KeyError):
        nl_items = []
    nl_code = next((it["code"] for it in nl_items if it["kind"] == "unblock"), "NONE")
    rc4, o4 = _wf(env, nl, "resend", "--code", nl_code, "--by", "executor", "--reason", "测试：执行端核对过会话，这条确实没有落地")
    rc5, o5 = _wf(env, nl, "run")
    evn = (nl / "workflow" / "events.jsonl").read_text(encoding="utf-8")
    _ok("1.6.5 发送后没有落地：wf resend 由执行端代决恢复本轮消息并重新发送，事件如实记 by=executor（W43）",
        rc4 == 0 and _state(nl)["state"] == "WAIT_REPLY" and '"resend_not_landed"' in evn and '"executor_decision"' in evn
        and '"by": "executor"' in evn, o4 + o5)
    # C2c. 1.6.8（P2-E）：设了 landed_timeout_s 时按墙钟判定落地核对
    lw = setup("landwall", {**base_pol, "landed_confirm_polls": 1, "landed_poll_s": 0, "landed_timeout_s": 3600})
    (fake / "poll_never").write_text("1")
    _wf(env, lw, "kick", "--message", str(task))
    _wf(env, lw, "run")
    rc, ow1 = _wf(env, lw, "run")
    _ok("1.6.8 墙钟落地核对：landed_confirm_polls 已满但没到 landed_timeout_s，不放弃、仍在等回复",
        _state(lw)["state"] == "WAIT_REPLY" and "SEND_NOT_LANDED" not in ow1, ow1)
    lw2 = setup("landwall2", {**base_pol, "landed_poll_s": 1, "landed_timeout_s": 1, "landed_min_polls": 3})
    _wf(env, lw2, "kick", "--message", str(task))
    _wf(env, lw2, "run")
    n_lw2 = len(list((fake / "inbox").glob("*-landwall2-*.md")))
    __import__("time").sleep(1.3)
    rc, ow2 = _wf(env, lw2, "run")
    s_mid = _state(lw2)
    mid = s_mid["state"] == "WAIT_REPLY" and 1 <= int(s_mid.get("send_unconfirmed") or 0) < 3
    ow3 = ""
    for _ in range(6):
        if _state(lw2)["state"] != "WAIT_REPLY":
            break
        __import__("time").sleep(1.1)
        rc, o_ = _wf(env, lw2, "run")
        ow3 += o_
    (fake / "poll_never").unlink()
    _ok("1.6.8 墙钟落地核对：时间已到但核对次数不足 landed_min_polls 时不放弃；时间与次数都满足才停在 SEND_NOT_LANDED，且没有重发",
        mid and _state(lw2)["state"] == "BLOCKED" and "SEND_NOT_LANDED" in ow3 and "距发送约" in ow3
        and len(list((fake / "inbox").glob("*-landwall2-*.md"))) == n_lw2 == 1, ow2 + ow3 + str(s_mid.get("send_unconfirmed")))
    (local / "transport.json").write_text(json.dumps(tcfg))

    # C2d. 1.6.8（P1-D）：wf drill review 在隔离目录里让真实驱动器消费审查程序的 block 与超时
    rvb = tmp / "t-review-drill-block"
    rvb.write_text('#!/bin/sh\necho \'{"verdict": "block", "model": "test-reviewer", "notes": ["测试：削弱了四道底线"]}\'\n')
    rvb.chmod(0o755)
    rvt = tmp / "t-review-drill-slow"
    rvt.write_text("#!/bin/sh\nsleep 6\necho '{\"verdict\": \"pass\"}'\n")
    rvt.chmod(0o755)
    rcb, ob = _wf(env, tmp, "drill", "review", "--kind", "block", "--dir", str(tmp / "drill-b"), "--review", str(rvb))
    resb = json.loads((tmp / "drill-b" / "drill-result.json").read_text(encoding="utf-8")) if (tmp / "drill-b" / "drill-result.json").exists() else {}
    _ok("1.6.8 演练 block：驱动器停在 AWAITING_USER、审查意见可见、版本包没有落地；演练项目已暂停、不在生产注册表里",
        rcb == 0 and resb.get("ok") and resb.get("state") == "AWAITING_USER"
        and resb.get("project", "x") not in (local / "registry.json").read_text(encoding="utf-8"), ob[-800:])
    rct, ot = _wf(env, tmp, "drill", "review", "--kind", "timeout", "--dir", str(tmp / "drill-t"), "--review", str(rvt),
                  "--review-timeout-s", "2")
    rest = json.loads((tmp / "drill-t" / "drill-result.json").read_text(encoding="utf-8")) if (tmp / "drill-t" / "drill-result.json").exists() else {}
    _ok("1.6.8 演练超时：决策日志记“未审查”、没有结论文件、良性版本包照常落地并继续",
        rct == 0 and rest.get("ok"), ot[-800:])
    rcx, ox = _wf(env, tmp, "drill", "review", "--kind", "block", "--dir", str(tmp / "drill-b"), "--review", str(rvb))
    _ok("1.6.8 演练目录不空时拒绝运行（不覆盖、不删除已有证据）", rcx != 0 and "必须不存在或为空" in ox, ox[-300:])

    # 手动发送登记 ref → 驱动器从 WAIT_REPLY 接管
    k = setup("manualref", base_pol)
    kk = next_k()
    (fake / "replies" / f"{kk}.md").write_text(_reply(["echo hi"], ["输出"], "发布：无\n之后：空闲\n验收 1：c1 exit=0"), encoding="utf-8")
    msg = k / "workflow" / "outbox" / "m.md"
    msg.parent.mkdir(parents=True, exist_ok=True)
    msg.write_text("手动消息 档位：high\n")
    # 手动走通道发送，拿到 ref，再登记
    sr = subprocess.run([tcfg["send"], "--project", "manualref", "--turn", "1", "--message", str(msg),
                         "--effort", "high", "--model", "t"], capture_output=True, text=True, env={**env, **{k2: str(v) for k2, v in (tcfg.get("env") or {}).items()}})
    ref = json.loads(sr.stdout.strip().splitlines()[-1]).get("ref")
    _wf(env, k, "go", "COMPOSE"); _wf(env, k, "mark", "sent", "--message-file", str(msg), "--kind", "task", "--effort", "high", "--ref", ref)
    rc, o = _wf(env, k, "run")
    _ok("手动发送并登记 ref：驱动器接管轮询直到完成（缺口 7）", _state(k)["state"] in ("IDLE", "WAIT_REPLY", "DONE")
        and _state(k).get("send_ref") == ref or "收到回复" in o, o)

    # /api/projects 暴露 paused（缺口 5）
    _wf(env, h, "pause", "--note", "测试暂停")
    import urllib.request as _u
    port2 = __import__("socket").socket(); port2.bind(("127.0.0.1", 0)); pn = port2.getsockname()[1]; port2.close()
    srv2 = subprocess.Popen([sys.executable, str(WF_HOME / "wf.py"), "serve", "--port", str(pn)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                _u.urlopen(f"http://127.0.0.1:{pn}/api/projects", timeout=1); break
            except Exception:
                __import__("time").sleep(0.1)
        pj = json.loads(_u.urlopen(f"http://127.0.0.1:{pn}/api/projects", timeout=5).read())
        row = next((x for x in pj["projects"] if x["name"] == "hb"), {})
        _ok("看板项目列表显示已暂停（缺口 5）", row.get("paused") is True and row.get("needs_user") is True, str(row))
    finally:
        srv2.terminate(); srv2.wait(timeout=10)


def run_v14(tmp: Path, env: dict, _wf, _ok, _state, _project_md, _make_zip):
    """1.4：决定码、聊天呈报去重、模块方式适配器、通道适配器位置检查、看板进程版本。"""
    print("  —— 决定码与呈报、模块适配器、看板版本 ——", flush=True)
    local = Path(env["WF_LOCAL_DIR"])
    tcfg = json.loads((local / "transport.json").read_text())
    p = tmp / "codes"
    _wf(env, tmp, "init", "--name", "codes", "--root", str(p))
    (p / "workflow" / "policy.json").write_text(json.dumps({"allow": [r"^echo "]}))
    subprocess.run(["git", "init", "-q"], cwd=p)
    subprocess.run(["git", "add", "-A"], cwd=p)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "release v0.1"], cwd=p)

    rc, o = _wf(env, p, "ask")
    _ok("呈报模板：决定码、事项、原因、你回复什么、不处理会怎样", "决定码" in o and "你回复：「批准" in o
        and "不处理会怎样" in o, o)
    rc, o = _wf(env, p, "policy", "approve", "--code", "ZZZ", "--said", "同意")
    _ok("错误的决定码被拒绝，并列出当前待决事项", rc != 0 and "不符" in o and "白名单" in o, o)
    rc, o = _wf(env, p, "--json", "ask")
    code = json.loads(o[o.index("{"):])["items"][0]["code"]
    rc, o = _wf(env, p, "policy", "approve", "--code", code, "--said", "   ")
    _ok("没有用户原话不能批准", rc != 0 and "原话" in o, o)
    (p / "workflow" / "policy.json").write_text(json.dumps({"allow": [r"^echo ", r"^ls "]}))
    rc, o = _wf(env, p, "policy", "approve", "--code", code, "--said", "同意")
    _ok("事项内容变了，旧决定码作废（防止批准落到改动后的内容上）", rc != 0 and "不符" in o, o)
    rc, o = _user(_wf, env, p, "policy", "policy", "approve")
    ev = (p / "workflow" / "events.jsonl").read_text()
    _ok("用正确的码与原话批准：逐字记录原话与码", rc == 0 and '"user_decision"' in ev and "测试：用户原话" in ev, o)

    _wf(env, p, "pause", "--note", "测试")
    rc1, o1 = _wf(env, p, "ask", "--new", "--mark-shown")
    rc2, o2 = _wf(env, p, "ask", "--new", "--mark-shown")
    _ok("呈报去重：同一事项只呈报一次（未处理时 12 小时后再提醒）", "自动化已暂停" in o1 and o2.strip() == "", o1 + "|" + o2)
    rc, o = _user(_wf, env, p, "unpause", "unpause")
    _ok("继续也需要决定码", rc == 0 and not _state(p).get("paused"), o)

    # 模块方式适配器（工具自带的示例模块），以及通道适配器位置检查
    echo = tmp / "echo"
    echo.mkdir()
    (local / "transport.json").write_text(json.dumps({"module": "adapters.example_echo", "timeout_s": 30,
                                                      "env": {"ECHO_DIR": str(echo)}}))
    rc, o = _wf(env, tmp, "transport", "test")
    _ok("模块方式适配器：一致性检查通过（无需三个可执行壳）", "✓ 适配器模块可找到" in o and "✓ poll 对不存在的轮次返回 none 或 no_ref" in o, o)
    q = tmp / "modrun"
    _wf(env, tmp, "init", "--name", "modrun", "--root", str(q))
    (q / "workflow" / "policy.json").write_text(json.dumps({"allow": [r"^echo "], "first_poll_after_s": 0, "poll_interval_s": 0}))
    _user(_wf, env, q, "policy", "policy", "approve")
    t = tmp / "t14.md"
    t.write_text("任务 v0.1 档位：high\n", encoding="utf-8")
    _wf(env, q, "kick", "--message", str(t))
    rc, o = _wf(env, q, "run")
    _ok("模块方式适配器驱动一轮：发送并进入等待 Claude", _state(q)["state"] == "WAIT_REPLY"
        and list((echo / "inbox").glob("*.md")), o)
    (local / "transport.json").write_text(json.dumps({"module": "adapters.base"}))
    rc, o = _wf(env, tmp, "transport")
    _ok("通道适配器放在工具目录内：报警", rc != 0 and "工具目录内" in o, o)
    (local / "transport.json").write_text(json.dumps(tcfg))

    # 看板进程加载的版本与磁盘版本不一致：doctor 报警
    import socket as _s
    so = _s.socket(); so.bind(("127.0.0.1", 0)); port = so.getsockname()[1]; so.close()
    _wf(env, tmp, "serve", "--daemon", "--port", str(port))
    info = local / "serve.json"
    for _ in range(30):
        if info.exists():
            break
        __import__("time").sleep(0.1)
    d = json.loads(info.read_text())
    d["version"] = "0.0.1"
    info.write_text(json.dumps(d))
    rc, o = _wf(env, p, "doctor")
    _wf(env, tmp, "serve", "--stop", "--port", str(port))
    _ok("看板仍在运行旧版本代码：wf doctor 报警（缺口 12）", "旧版本" in o, o)
