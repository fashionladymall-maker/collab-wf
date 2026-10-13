"""1.8.2 的自检（collab-wf-dev 第 105 轮）：阶段目标、待办取消与取代、NO_REF 防循环、同步凭证、钉在提交上的短消息、
回复完整性。同一组函数也是 wf self-check --probes 的行为探针：在临时 local/ 里对已安装的代码跑一遍，不碰本机状态。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .core import WF_HOME

REPLY6 = ("【本轮结论】\n测试。\n\n【本机执行】\n无\n\n【本轮回报清单】\n无\n\n【需要用户决定】\n无\n\n"
          "【下一轮建议档位】\nhigh\n\n【驱动指令】\n发布：无\n之后：回报\n")


def _inst(td, name, state=None):
    from .core import Instance
    root = Path(td) / name
    (root / "workflow").mkdir(parents=True)
    (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"{name}-{os.getpid()}"}), encoding="utf-8")
    (root / "PROJECT.md").write_text("# PROJECT.md\n", encoding="utf-8")
    inst = Instance(root)
    if state:
        inst.state.update(state)
        inst.save()
    return inst


def _t_phase_goals(_ok):
    from . import goals, sync
    with tempfile.TemporaryDirectory() as td:
        inst = _inst(td, "v180-g", {"state": "IDLE", "turn": 3})
        old = {"goals": [{"id": "G1", "title": "旧目标", "kind": "manual", "means": "m", "on_fail": "f"}]}
        (inst.wf / "goals.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        p_old = goals.progress(inst)
        new = {"phase": {"id": "M2", "title": "专业基线", "supersedes": "M1", "since": "2026-10-13"},
               "goals": [{"id": "B1", "title": "阶段目标", "kind": "exec", "check": "true", "pass": {"exit": 0},
                          "means": "m", "on_fail": "f"}],
               "history": [{"id": "M1", "title": "无人值守稳定运行", "status": "done", "closed": "2026-10-12", "summary": "7/7"}]}
        (inst.wf / "goals.json").write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
        p_new = goals.progress(inst)
        bad = goals.validate({"phase": {"title": "缺编号"}, "goals": new["goals"]})
        dup = goals.validate({"phase": {"id": "M2", "title": "t"}, "goals": new["goals"], "history": [{"id": "M2", "title": "x"}]})
        status = sync.status_md(inst, [], [], [])
    t_old, t_new, hist = goals.phase_title(p_old), goals.phase_title(p_new), goals.history_lines(p_new)
    _ok("1.8.2 阶段目标：goals.json 可声明当前阶段与历史阶段；旧格式照实标“未声明”；历史阶段不计入当前进度；"
        "STATUS.md 先写当前阶段、再写进度、最后列历史",
        p_old.get("phase") is None and "未声明" in t_old and (p_new.get("phase") or {}).get("id") == "M2"
        and "当前阶段 M2 专业基线" in t_new and "取代 M1" in t_new and p_new["total"] == 1 and len(hist) == 1
        and "历史阶段 M1" in hist[0] and "已完成" in hist[0] and not goals.validate(new)
        and any("phase" in x for x in bad) and any("重复" in x for x in dup)
        and "当前阶段 M2" in status and "历史阶段 M1" in status and "进度 0 / 1" in status,
        json.dumps([t_old, t_new, hist, bad, dup, status[-400:]], ensure_ascii=False)[:900])


def _t_tasks_close(_ok):
    from . import notify, tasks
    from .core import WfError
    with tempfile.TemporaryDirectory() as td:
        inst = _inst(td, "v180-t", {"state": "VERIFY", "turn": 7})
        tasks.add(inst, 7, {"1": "改同步程序", "2": "解冻另一个项目", "3": "回报"})
        tasks.done(inst, "T7-2", "已解冻")
        errs = []
        for fn in (lambda: tasks.close(inst, "T7-1", "cancelled", ""),
                   lambda: tasks.close(inst, "T7-2", "cancelled", "已被边界取代"),
                   lambda: tasks.done(inst, "T7-3", "")):
            try:
                fn()
                errs.append(False)
            except WfError:
                errs.append(True)
        c = tasks.close(inst, "T7-1", "cancelled", "改由新的待办承接")
        s = tasks.close(inst, "T7-2", "superseded", "用户边界：该项目不恢复", by_task="T8-1")
        again = False
        try:
            tasks.done(inst, "T7-1", "又做了")
        except WfError:
            again = True
        left = [t["id"] for t in tasks.open_items(inst)]
        keys = " ".join(x["key"] for x in notify.collect_inbox([inst]))
        ev = (inst.root / "workflow" / "events.jsonl").read_text(encoding="utf-8")
        notes = (inst.turn_dir() / "notes.jsonl").read_text(encoding="utf-8")
    _ok("1.8.2 待办取消与取代：必须写原因；未完成的可取消；已完成的只能改记为被取代、原完成记录保留；取消后不能再记完成；"
        "完成必须写结果；关闭的待办不再进 inbox；事件与备注如实记录",
        errs == [True, True, True] and c["status"] == "cancelled" and s["status"] == "superseded"
        and (s.get("was_done") or {}).get("note") == "已解冻" and s.get("superseded_by") == "T8-1" and again
        and left == ["T7-3"] and "task:T7-3" in keys and "task:T7-1" not in keys and "task:T7-2" not in keys
        and '"task_cancelled"' in ev and '"task_superseded"' in ev and "已取消" in notes and "被取代" in notes,
        json.dumps([errs, left, keys], ensure_ascii=False)[:600])


def _t_no_ref(_ok):
    from . import driver, machine, ops
    from .core import Instance, WfError
    sent = "2026-10-12T00:00:00+00:00"
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "v180-n", {"state": "WAIT_REPLY", "turn": 4, "sent_at": sent})
        r1 = driver.h_wait_reply(driver.Ctx(a, {}, time.time() + 60))
        st1 = dict(a.state)
        env = {**os.environ, "WF_LOCAL_DIR": str(Path(td) / "local-cli")}
        env.pop("VIRTUAL_ENV", None)
        cp = subprocess.run([sys.executable, str(WF_HOME / "wf.py"), "approve", "--code", "X", "--said", "测试"],
                            cwd=str(a.root), env=env, capture_output=True, text=True, timeout=120)
        a = Instance(a.root)
        refused = cp.returncode != 0 and "ref" in (cp.stdout + cp.stderr) and a.state.get("state") == "AWAITING_USER"
        machine.resume(a, "测试：绕过批准回到等待", by="test")
        r2 = driver.h_wait_reply(driver.Ctx(a, {}, time.time() + 60))
        st2 = dict(a.state)
        b = _inst(td, "v180-n2", {"state": "WAIT_REPLY", "turn": 2, "sent_at": sent})
        driver.h_wait_reply(driver.Ctx(b, {}, time.time() + 60))
        noref_err = False
        try:
            ops.mark_sent(b, ref=None)
        except WfError:
            noref_err = True
        ops.mark_sent(b, ref="ref-123")
        bs = dict(b.state)
        c = _inst(td, "v180-n3", {"state": "WAIT_REPLY", "turn": 2, "sent_at": sent})
        driver.h_wait_reply(driver.Ctx(c, {}, time.time() + 60))
        rf = Path(td) / "reply.md"
        rf.write_text("回复\n", encoding="utf-8")
        ops.mark_replied(c, str(rf))
        cs = dict(c.state)
    _ok("1.8.2 NO_REF 防循环：没有 ref 时停下并记下规则；wf approve 拒绝（批准解决不了）；绕过批准后同一轮再次 NO_REF 转为阻塞；"
        "补登记 ref 回到等待回复、整轮手动收尾进入解析，都清掉规则标记",
        r1[0] == "user" and st1.get("state") == "AWAITING_USER" and st1.get("await_rule") == "NO_REF" and refused
        and r2[0] == "user" and st2.get("state") == "BLOCKED" and noref_err and bs.get("state") == "WAIT_REPLY"
        and bs.get("send_ref") == "ref-123" and not bs.get("await_rule") and cs.get("state") == "INTAKE"
        and not cs.get("await_rule"),
        json.dumps([r1, st1.get("state"), st1.get("await_rule"), cp.returncode, (cp.stdout + cp.stderr)[-300:], r2,
                    st2.get("state"), bs.get("state"), cs.get("state")], ensure_ascii=False)[:900])


def _t_sync_commit(_ok):
    from . import anomaly, sync
    orig = (sync.cfg, sync.SYNC_DIR, anomaly.call, sync.collect)
    calls = []

    def fake_call(op, args, timeout=None, env_extra=None):
        calls.append(op)
        return {"code": 0, "data": dict(fake_call.data), "raw": "测试"}
    fake_call.data = {}
    with tempfile.TemporaryDirectory() as td:
        inst = _inst(td, "v180-s", {"state": "IDLE", "turn": 1})
        files = {"STATUS.md": b"s1\n", "project/a.txt": b"a\n", "turns/turn-001.md": b"t\n"}
        try:
            sync.cfg = lambda: {"sync": "测试同步程序", "sync_projects": [inst.name]}
            sync.SYNC_DIR = Path(td) / "local-sync"
            sync.collect = lambda i: dict(files)
            anomaly.call = fake_call
            r1 = sync.run(inst)
            rec1 = sync.record(inst)
            stage = sync.SYNC_DIR / inst.name / "tree"
            (stage / "old").mkdir(parents=True, exist_ok=True)
            (stage / "old" / "stale.md").write_text("旧\n", encoding="utf-8")
            keep = stage / "project" / "a.txt"
            m0 = keep.stat().st_mtime_ns if keep.exists() else None
            time.sleep(0.05)
            fake_call.data = {"commit": "0123456789abcdef0123456789abcdef01234567"}
            r2 = sync.run(inst)
            rec2 = sync.record(inst)
            m1 = keep.stat().st_mtime_ns if keep.exists() else -1
            r3 = sync.run(inst)
            stale_gone = not (stage / "old" / "stale.md").exists()
        finally:
            sync.cfg, sync.SYNC_DIR, anomaly.call, sync.collect = orig
    _ok("1.8.2 同步凭证：退出码 0 但没有提交号记 unconfirmed、不更新已推送摘要；有提交号才算 pushed 并记下提交号；"
        "内容没变时 unchanged 带上那次的提交号；暂存目录原地更新：多出的旧文件逐个删除、内容相同的不重写",
        r1["status"] == "unconfirmed" and "pushed_digest" not in rec1 and rec1.get("failures") == 1
        and r2["status"] == "pushed" and str(r2.get("commit", "")).startswith("0123456")
        and rec2.get("pushed_commit") == r2.get("commit") and r3["status"] == "unchanged" and r3.get("commit") == r2.get("commit")
        and sorted(r3.get("names") or []) == sorted(files) and stale_gone and m0 is not None and m1 == m0 and len(calls) == 2,
        json.dumps([r1, r2, r3, rec1, m0, m1, calls], ensure_ascii=False, default=str)[:900])


def _t_short_pinned(_ok):
    from . import admin, sync
    from .core import Instance
    orig = (sync.cfg, sync.run)
    sha = "fedcba9876543210fedcba9876543210fedcba98"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "proj"
        (root / "workflow" / "evidence" / "turn-007").mkdir(parents=True)
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"v180-bs-{os.getpid()}"}), encoding="utf-8")
        (root / "PROJECT.md").write_text("# PROJECT.md\n", encoding="utf-8")
        (root / ".gitignore").write_text("workflow/\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=root)
        subprocess.run(["git", "add", "-A"], cwd=root)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "release v0.1"], cwd=root)
        (root / "workflow" / "turns.jsonl").write_text(json.dumps({"turn": 7, "status": "sent"}) + "\n"
                                                       + json.dumps({"turn": 8, "status": "sent"}) + "\n", encoding="utf-8")
        (root / "workflow" / "evidence" / "turn-007" / "reply.md").write_text("回复\n", encoding="utf-8")
        inst = Instance(root)
        inst.state.update(state="COMPOSE", turn=9)
        inst.save()
        seq, calls = [], []

        def fake_run(i, **k):
            calls.append(bool(k.get("force")))
            return seq.pop(0)
        try:
            sync.cfg = lambda: {"sync": "/x/gh_sync.py", "sync_projects": [inst.name], "sync_repo": "me/repo"}
            sync.run = fake_run
            seq[:] = [{"status": "unconfirmed", "detail": "忙"}]
            b1 = admin.bootstrap(inst, "测试")
            seq[:] = [{"status": "pushed", "commit": sha, "digest": "d2", "files": 4,
                       "names": ["STATUS.md", "project/PROJECT.md", "turns/turn-007.md", "replies/reply-007.md"]}]
            b2 = admin.bootstrap(inst, "测试")
            short = b2["short"].read_text(encoding="utf-8") if b2.get("short") else ""
            n0 = len(calls)
            seq[:] = [{"status": "unchanged", "commit": "", "digest": "d2", "files": 4, "names": []},
                      {"status": "pushed", "commit": sha, "digest": "d3", "files": 1, "names": ["STATUS.md"]}]
            b3 = admin.bootstrap(inst, "测试")
            forced = calls[n0:] == [False, True]
            msg_ok = b1["message"].exists()
        finally:
            sync.cfg, sync.run = orig
    _ok("1.8.2 交接短消息：同步没有提交号时不生成并写明原因（长消息兜底）；有提交号时链接钉在提交上、不用分支，"
        "只链接已推送的文件；旧记录没有提交号时先补推一次",
        b1.get("short") is None and "unconfirmed" in (b1.get("short_why") or "") and msg_ok
        and f"https://raw.githubusercontent.com/me/repo/{sha}/STATUS.md" in short and "/main/" not in short
        and f"{sha}/turns/turn-007.md" in short and "turn-008" not in short and f"{sha}/replies/reply-007.md" in short
        and "档位：max" in short and "当前阶段" in short and "不要进入业务开发" in short and bool(b3.get("short")) and forced,
        json.dumps([b1.get("short_why"), short[:700], calls], ensure_ascii=False, default=str)[:1000])


def _t_reply_limit(_ok):
    from . import anomaly, driver
    orig = anomaly.call
    with tempfile.TemporaryDirectory() as td:
        inst = _inst(td, "v180-r", {"state": "WAIT_REPLY", "turn": 5, "send_ref": "R5", "sent_at": "2026-10-12T00:00:00+00:00",
                                    "conv": {"conv": "C1", "turns": 1, "chars": 10}})
        rf = Path(td) / "poll-reply.md"
        rf.write_text(REPLY6, encoding="utf-8")

        def fake_call(op, args, timeout=None, env_extra=None):
            if op == "poll":
                return {"code": 0, "raw": "", "data": {"status": "done", "reply_file": str(rf), "files": [],
                                                       "reply_parent": "R5", "stop_reason": "tool_use_limit",
                                                       "completion_state": "continuation_required",
                                                       "message_uuid": "m-1", "parent": "R5"}}
            return {"code": 0, "raw": "", "data": {}}
        try:
            anomaly.call = fake_call
            r1 = driver.h_wait_reply(driver.Ctx(inst, {}, time.time() + 60))
            mp = inst.turn_dir(5) / "reply_meta.json"
            meta = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
            r2 = driver.h_intake(driver.Ctx(inst, {}, time.time() + 60))
            carry = dict(inst.state.get("limit_carry") or {})
            out = Path(td) / "next.md"
            out.write_text("【回报 · 第 6 轮 · v0.1】档位：high\n\n正文\n", encoding="utf-8")
            driver._append_limit_carry(inst, {"file": str(out), "turn": 6})
            body = out.read_text(encoding="utf-8")
            cleared = "limit_carry" not in inst.state
            inst.state["limit_carry"] = dict(carry, conv="C0")
            out2 = Path(td) / "next2.md"
            out2.write_text("消息\n", encoding="utf-8")
            driver._append_limit_carry(inst, {"file": str(out2), "turn": 6})
            body2 = out2.read_text(encoding="utf-8")
            inst.state["limit_streak"] = 2
            inst.state["reply_completion"] = dict(meta)
            driver._limit_reply(driver.Ctx(inst, {}, time.time() + 60), False)
            capped = "limit_carry" not in inst.state and inst.state.get("limit_streak") == 3
            line = driver._limit_line(inst, 5)
        finally:
            anomaly.call = orig
    _ok("1.8.2 回复完整性：适配器给出的结束原因写入 reply_meta.json；因工具上限结束、固定块齐全时照常解析，并在下一条消息末尾"
        "请 Claude 先从断点补齐（只在同一对话内，连续 3 次后不再自动附）；续接消息写明上一轮的结束原因",
        r1[0] == "progress" and meta.get("completion_state") == "continuation_required"
        and meta.get("stop_reason") == "tool_use_limit" and r2[0] == "progress" and carry.get("stop_reason") == "tool_use_limit"
        and "tool_use_limit" in body and "从断点补齐" in body and cleared and "从断点补齐" not in body2 and capped
        and "tool_use_limit" in line,
        json.dumps([r1, r2, meta, carry, body[-300:], body2, line], ensure_ascii=False)[:900])


def _t_stage_symlink(_ok):
    from . import sync
    from .core import WfError
    orig = sync.SYNC_DIR
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        sync.SYNC_DIR = base / "sync"
        outside = base / "outside"
        outside.mkdir()
        (outside / "safe.txt").write_text("原文\n", encoding="utf-8")
        stage = sync.SYNC_DIR / "p" / "tree"
        stage.mkdir(parents=True)
        os.symlink(outside / "safe.txt", stage / "safe.txt")
        os.symlink(outside, stage / "linked-dir")
        try:
            sync._stage_tree(stage, {"safe.txt": b"new\n", "linked-dir/new.txt": b"x\n", "a/b.txt": b"b\n"})
            ok1 = ((outside / "safe.txt").read_text(encoding="utf-8") == "原文\n" and not (outside / "new.txt").exists()
                   and not (stage / "safe.txt").is_symlink() and (stage / "safe.txt").read_bytes() == b"new\n"
                   and not (stage / "linked-dir").is_symlink() and (stage / "linked-dir" / "new.txt").read_bytes() == b"x\n")
            os.symlink(outside, stage / "stray-link")
            sync._stage_tree(stage, {"a/b.txt": b"b\n"})
            ok2 = (not os.path.lexists(stage / "stray-link") and (outside / "safe.txt").exists()
                   and not os.path.lexists(stage / "safe.txt") and (stage / "a" / "b.txt").exists())
            bad = []
            for name in ("../escape.txt", "/abs.txt", "a/../../x.txt", "", "a//b.txt"):
                try:
                    sync._stage_tree(stage, {name: b"z"})
                    bad.append(False)
                except WfError:
                    bad.append(True)
            esc = not (sync.SYNC_DIR / "p" / "escape.txt").exists() and not (base / "x.txt").exists()
            real = base / "real"
            real.mkdir()
            os.symlink(real, sync.SYNC_DIR / "q")
            try:
                sync._stage_tree(sync.SYNC_DIR / "q" / "tree", {"x.txt": b"1"})
                rootref = False
            except WfError:
                rootref = True
            ok3 = rootref and not (real / "tree").exists()
        finally:
            sync.SYNC_DIR = orig
    _ok("1.8.2 暂存目录安全：文件链接与目录链接都不写穿（只删链接本身、改写真实文件）；多出的链接只删链接；"
        "不安全路径（..、绝对路径、空段）拒绝；暂存目录的上级是链接时拒绝",
        ok1 and ok2 and bad == [True] * 5 and esc and ok3, json.dumps([ok1, ok2, bad, esc, ok3]))


def _t_stage_hardlink(_ok):
    from . import sync
    orig = (sync.SYNC_DIR, os.replace)
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        sync.SYNC_DIR = base / "sync"
        outside = base / "outside.txt"
        outside.write_text("外部原文\n", encoding="utf-8")
        stage = sync.SYNC_DIR / "p" / "tree"
        stage.mkdir(parents=True)
        os.link(outside, stage / "inside.txt")
        same = base / "same.txt"
        same.write_text("同样内容\n", encoding="utf-8")
        os.link(same, stage / "same.txt")
        try:
            sync._stage_tree(stage, {"inside.txt": b"new\n", "same.txt": "同样内容\n".encode("utf-8")})
            ok1 = (outside.read_text(encoding="utf-8") == "外部原文\n" and (stage / "inside.txt").read_bytes() == b"new\n"
                   and os.stat(stage / "inside.txt").st_nlink == 1 and os.stat(stage / "same.txt").st_nlink == 1
                   and same.read_text(encoding="utf-8") == "同样内容\n")
            (stage / "keep.txt").write_bytes(b"old\n")

            def boom(a, b):
                raise OSError("测试：替换失败")
            os.replace = boom
            failed = False
            try:
                sync._stage_tree(stage, {"inside.txt": b"new\n", "same.txt": "同样内容\n".encode("utf-8"), "keep.txt": b"NEW\n"})
            except OSError:
                failed = True
            os.replace = orig[1]
            ok2 = (failed and (stage / "keep.txt").read_bytes() == b"old\n"
                   and not [p for p in stage.iterdir() if p.name.startswith(".wf-stage-")])
        finally:
            sync.SYNC_DIR, os.replace = orig
    _ok("1.8.2 暂存目录硬链接：指向暂存目录之外的硬链接不被写穿（新 inode 写好后原子替换，外部文件不变、链接被断开）；"
        "内容相同但有硬链接时也替换；替换失败时旧文件原样保留、不留临时文件",
        ok1 and ok2, json.dumps([ok1, ok2]))


def _t_pause_between(_ok):
    import re as _re
    import shlex
    from . import driver, ops
    from .core import Instance
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "v181-p", {"state": "EXECUTE", "turn": 3})
        (a.root / "pause_now.py").write_text(
            "import json\np='workflow/state.json'\nd=json.load(open(p,encoding='utf-8'))\n"
            "d['paused']={'note':'测试：命令之间暂停','by':'executor'}\n"
            "json.dump(d,open(p,'w',encoding='utf-8'),ensure_ascii=False)\n", encoding="utf-8")
        py = shlex.quote(sys.executable)
        cmds = [f"{py} pause_now.py", "echo two"]
        a.turn_dir().mkdir(parents=True, exist_ok=True)
        (a.turn_dir() / "intake.json").write_text(json.dumps({"commands": cmds, "directives": {}}), encoding="utf-8")
        pol = {"allow": ["^" + _re.escape(py) + r" pause_now\.py$", r"^echo two$"]}
        r1 = driver.h_execute(driver.Ctx(a, pol, time.time() + 120))
        n1, nxt = len(ops.list_execs(a)), a.state.get("exec_next")
        s = Instance(a.root)
        s.state.pop("paused", None)
        s.save()
        r2 = driver.h_execute(driver.Ctx(a, pol, time.time() + 120))
        n2, st2 = len(ops.list_execs(a)), a.state.get("state")
    _ok("1.8.2 命令之间重读暂停：一条命令执行中别的进程暂停了项目，下一条命令之前停下、不再执行；恢复后从停下的那条继续",
        r1[0] == "user" and n1 == 1 and nxt == 1 and r2[0] == "progress" and n2 == 2 and st2 == "VERIFY",
        json.dumps([r1, n1, nxt, r2, n2, st2], ensure_ascii=False)[:600])


TESTS = [("阶段目标", _t_phase_goals), ("待办取消", _t_tasks_close), ("NO_REF", _t_no_ref),
         ("同步凭证", _t_sync_commit), ("交接短消息", _t_short_pinned), ("回复完整性", _t_reply_limit),
         ("暂存目录安全", _t_stage_symlink), ("暂存目录硬链接", _t_stage_hardlink), ("命令间暂停", _t_pause_between)]


def run_v18(_ok) -> None:
    print("  —— 1.8.2 新增检查 ——", flush=True)
    for name, fn in TESTS:
        try:
            fn(_ok)
        except Exception as e:   # 一组检查出错只记这一组失败，不中断整个自检
            _ok(f"1.8.2 检查组「{name}」运行出错", False, f"{type(e).__name__}: {e}")


def probes_main() -> int:
    """wf self-check --probes：在临时 local/ 里（子进程）跑上面的检查，逐项输出“探针 名称：通过/未通过”。只读：不碰本机状态。"""
    if os.environ.get("WF_PROBE_CHILD") != "1":
        with tempfile.TemporaryDirectory(prefix="wf-probes-") as td:
            env = {**os.environ, "WF_LOCAL_DIR": str(Path(td) / "local"), "WF_PROBE_CHILD": "1"}
            env.pop("VIRTUAL_ENV", None)
            r = subprocess.run([sys.executable, str(WF_HOME / "wf.py"), "self-check", "--probes"], env=env, cwd=td,
                               capture_output=True, text=True, timeout=600)
            sys.stdout.write(r.stdout)
            if r.returncode != 0 and r.stderr:
                sys.stdout.write(r.stderr[-1500:])
            return r.returncode
    print(f"collab-wf {(WF_HOME / 'VERSION').read_text().strip()} 行为探针（临时 local/，不碰本机状态）")
    passed = 0
    for name, fn in TESTS:
        res = []

        def _ok(n, cond, detail=""):
            res.append((bool(cond), n, detail))
            return cond
        err = ""
        try:
            fn(_ok)
        except Exception as e:   # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        good = bool(res) and all(x[0] for x in res) and not err
        passed += 1 if good else 0
        why = err or "；".join(str(x[2])[:200] for x in res if not x[0])
        print(f"探针 {name}：{'通过' if good else '未通过'}" + ("" if good else f"（{why[:400]}）"), flush=True)
    print(f"探针结果：{passed} / {len(TESTS)} 通过")
    return 0 if passed == len(TESTS) else 1
