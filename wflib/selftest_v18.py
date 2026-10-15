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
from .selftest_send_fixtures import send_context, send_host

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
        added = tasks.add(inst, 7, {"1": "改同步程序", "2": "解冻另一个项目", "3": "回报"})
        gen = {x["id"]: x["generation"] for x in added}   # B2-A1：新记录按代次修改，代次取自刚登记返回的条目
        tasks.done(inst, "T7-2", "已解冻", generation=gen["T7-2"])
        errs = []
        # B2-A1：每次都带上正确代次，拒绝只能来自原本的原因（缺原因、已完成不能取消、缺结果），不是缺代次
        for fn in (lambda: tasks.close(inst, "T7-1", "cancelled", "", generation=gen["T7-1"]),
                   lambda: tasks.close(inst, "T7-2", "cancelled", "已被边界取代", generation=gen["T7-2"]),
                   lambda: tasks.done(inst, "T7-3", "", generation=gen["T7-3"])):
            try:
                fn()
                errs.append(False)
            except WfError:
                errs.append(True)
        c = tasks.close(inst, "T7-1", "cancelled", "改由新的待办承接", generation=gen["T7-1"])
        # 接替者 T8-1 尚未登记，没有代次可绑定：只给 by_task，不给 by_generation（不默认绑定任何一条）
        s = tasks.close(inst, "T7-2", "superseded", "用户边界：该项目不恢复", by_task="T8-1", generation=gen["T7-2"])
        again = False
        try:
            tasks.done(inst, "T7-1", "又做了", generation=gen["T7-1"])   # 带正确代次：拒绝来自“已取消”
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
            # 1.9.5：固定块齐全但因上限结束的回复不再照常执行，改为续接；不再挂“下一条补齐”
            no_exec = (inst.state.get("state") == "COMPOSE" and inst.state.get("compose_reason") == "continue"
                       and "limit_carry" not in inst.state)
            ctext = driver._compose_text(driver.Ctx(inst, {}, time.time() + 60))[0] if no_exec else ""
            # 旧的“下一条补齐”附加逻辑保留给升级前留下的状态，照旧检查它
            carry = {"turn": 5, "stop_reason": "tool_use_limit", "conv": "C1", "delivered": []}
            inst.state["limit_carry"] = dict(carry)
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
            inst.state["reply_completion"] = dict(meta, turn=inst.state.get("turn"))   # 1.9.5：续接已让轮次加一
            driver._limit_reply(driver.Ctx(inst, {}, time.time() + 60), False)
            capped = "limit_carry" not in inst.state and inst.state.get("limit_streak") == 3
            line = driver._limit_line(inst, 5)
        finally:
            anomaly.call = orig
    _ok("1.9.5 回复完整性：适配器给出的结束原因写入 reply_meta.json；因工具上限结束的回复即使固定块齐全也不落地、不执行，"
        "转为续接，续接消息写明结束原因、要求把命令与驱动指令完整重写；升级前留下的“下一条补齐”只在同一对话内附、用过即清、"
        "连续 3 次后不再附",
        r1[0] == "progress" and meta.get("completion_state") == "continuation_required"
        and meta.get("stop_reason") == "tool_use_limit" and r2[0] == "progress" and "REPLY_TRUNCATED" in r2[1] and no_exec
        and "tool_use_limit" in ctext and "完整写出" in ctext
        and "tool_use_limit" in body and "从断点补齐" in body and cleared and "从断点补齐" not in body2 and capped
        and "tool_use_limit" in line,
        json.dumps([r1, r2, meta, no_exec, ctext[-300:], body[-200:], body2, line], ensure_ascii=False)[:900])


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
    from unittest.mock import patch
    from . import core, driver, ops, policy
    from .core import Instance
    with tempfile.TemporaryDirectory() as td, patch.object(core, "REGISTRY", Path(td) / "registry.json"):
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
        core.REGISTRY.write_text(json.dumps({"projects": []}), encoding="utf-8")
        (a.wf / "policy.json").write_text(json.dumps(pol), encoding="utf-8")
        policy.approve(a, "policy", "v18 pause fixture")
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


def _t_manual_prereq(_ok):
    from . import asks, goals, policy
    from .core import WfError
    with tempfile.TemporaryDirectory() as td:
        inst = _inst(td, "v183-m", {"state": "IDLE", "turn": 2})
        g = {"phase": {"id": "M9", "title": "测试"}, "goals": [
            {"id": "E1", "title": "机器", "kind": "exec", "check": "true", "pass": {"exit": 0}, "means": "m", "on_fail": "f"},
            {"id": "U1", "title": "用户验收", "kind": "manual", "depends_on": "all_exec", "means": "m", "on_fail": "f"}]}
        (inst.wf / "goals.json").write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
        policy.approve(inst, "goals", "测试", by="test")
        u1 = next(i for i in goals.progress(inst)["items"] if i["id"] == "U1")
        k1 = [str(x.get("kind")) for x in asks.pending(inst)]
        refused = False
        try:
            goals.confirm(inst, "U1", "测试")
        except WfError:
            refused = True
        goals.run_checks(inst, by="test")
        u2 = next(i for i in goals.progress(inst)["items"] if i["id"] == "U1")
        k2 = [str(x.get("kind")) for x in asks.pending(inst)]
        bad = goals.validate({"goals": [dict(g["goals"][1], depends_on=["NOPE"])]})
    _ok("1.8.3 人工目标的前置：前置没满足时不算完成、不出现确认事项、wf goals confirm 拒绝；前置满足后才出现确认事项；"
        "depends_on 写错时校验不通过",
        u1.get("ready") is False and not u1["ok"] and "前置未满足" in u1["detail"] and "confirm:U1" not in k1
        and refused and u2.get("ready") is True and "confirm:U1" in k2 and any("depends_on" in x for x in bad),
        json.dumps([u1, k1, refused, u2, k2, bad], ensure_ascii=False)[:800])


def _t_owner_split(_ok):
    from datetime import datetime, timedelta, timezone
    from . import notify, tasks
    rc = notify.route({"kind": "confirm:U1", "key": "x", "why": "w"})
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "inbox.json"
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds")
        path.write_text(json.dumps({"items": [{"key": "p|task:T1-1", "since": old}]}), encoding="utf-8")
        items = notify._write_inbox_locked([], path, [{"key": "p|task:T1-1", "project": "p", "for": "executor",
                                                        "title": "t", "why": "w", "action": "", "code": ""}])
        inst = _inst(td, "v183-o", {"state": "IDLE", "turn": 1})
        tasks.add(inst, 1, {"1": "测试待办"})
        rows = notify.collect_inbox([inst])
    x = items[0]
    _ok("1.8.3 决定与投递分开：每一项都带 decision_owner 与 delivery_owner（一律执行端）；升级只让用户看见、决定归属不变；"
        "确认人工目标的决定归属是用户",
        rc == "user" and x.get("for") == "user" and x.get("escalated") and x.get("notify_user")
        and x.get("delivery_owner") == "executor" and x.get("decision_owner") == "executor" and rows
        and all(r.get("delivery_owner") == "executor" and r.get("decision_owner") for r in rows),
        json.dumps([rc, x, rows], ensure_ascii=False)[:800])


def _t_artifacts_pick(_ok):
    import zipfile as _zf
    from . import sync
    with tempfile.TemporaryDirectory() as td:
        inst = _inst(td, "v183-a", {"state": "IDLE", "turn": 2})
        d = inst.evidence / "turn-002" / "delivered"
        d.mkdir(parents=True)
        with _zf.ZipFile(d / "proj-v1.zip", "w") as z:
            z.writestr("proj-v1/PROJECT.md", "# P\n")
        time.sleep(0.05)
        with _zf.ZipFile(d / "collab-wf-9.9.9.zip", "w") as z:
            z.writestr("collab-wf/VERSION", "9.9.9\n")
        names = [n for n in sync.collect(inst) if n.startswith("artifacts/")]
    _ok("1.8.3 同步只把项目版本包放进 artifacts：Claude 附来比对的工具包不当成项目版本包",
        "artifacts/proj-v1.zip" in names and "artifacts/collab-wf-9.9.9.zip" not in names, str(names))


def _t_plan_stale(_ok):
    import re as _re
    import shlex
    from unittest.mock import patch
    from . import core, driver, ops, policy
    from .core import Instance
    with tempfile.TemporaryDirectory() as td, patch.object(core, "REGISTRY", Path(td) / "registry.json"):
        a = _inst(td, "v184-s", {"state": "EXECUTE", "turn": 3})
        (a.root / "pause_now.py").write_text(
            "import json\np='workflow/state.json'\nd=json.load(open(p,encoding='utf-8'))\n"
            "d['paused']={'note':'测试','by':'executor'}\njson.dump(d,open(p,'w',encoding='utf-8'),ensure_ascii=False)\n",
            encoding="utf-8")
        py = shlex.quote(sys.executable)
        a.turn_dir().mkdir(parents=True, exist_ok=True)
        (a.turn_dir() / "intake.json").write_text(json.dumps({"commands": [f"{py} pause_now.py", "echo two"], "directives": {}}),
                                                  encoding="utf-8")
        pol = {"allow": ["^" + _re.escape(py) + r" pause_now\.py$", r"^echo two$"]}
        core.REGISTRY.write_text(json.dumps({"projects": []}), encoding="utf-8")
        (a.wf / "policy.json").write_text(json.dumps(pol), encoding="utf-8")
        policy.approve(a, "policy", "v18 stale fixture")
        a.state["exec_binding"] = driver._plan_binding(a, with_fp=False)
        a.save()
        r1 = driver.h_execute(driver.Ctx(a, pol, time.time() + 120))
        (a.root / "PROJECT.md").write_text("# PROJECT.md\n人工改动\n", encoding="utf-8")   # 暂停期间别处改了项目
        s = Instance(a.root)
        s.state.pop("paused", None)
        s.save()
        r2 = driver.h_execute(driver.Ctx(a, pol, time.time() + 120))
        n2, st2, fr = len(ops.list_execs(a)), a.state.get("state"), a.state.get("force_report")
        notes = (a.turn_dir() / "notes.jsonl").read_text(encoding="utf-8") if (a.turn_dir() / "notes.jsonl").exists() else ""
        b = _inst(td, "v184-t", {"state": "EXECUTE", "turn": 4, "exec_binding": {"turn": 4, "tool": "0.0.0", "fp": None}})
        (b.wf / "policy.json").write_text(json.dumps(pol), encoding="utf-8")
        policy.approve(b, "policy", "v18 stale tool fixture")
        b.turn_dir().mkdir(parents=True, exist_ok=True)
        (b.turn_dir() / "intake.json").write_text(json.dumps({"commands": ["echo two"], "directives": {}}), encoding="utf-8")
        r3 = driver.h_execute(driver.Ctx(b, pol, time.time() + 120))
        n3 = len(ops.list_execs(b))
    _ok("1.8.4 计划过时：暂停期间项目被别处改动，恢复后剩下的命令不执行、记 PLAN_STALE 并回报；"
        "计划批准后工具版本被换掉，一条也不执行",
        r1[0] == "user" and r2[0] == "progress" and n2 == 1 and st2 == "VERIFY" and fr and "PLAN_STALE" in notes
        and r3[0] == "progress" and n3 == 0 and "工具版本" in r3[1],
        json.dumps([r1, r2, n2, st2, fr, r3, n3], ensure_ascii=False)[:700])


def _t_exec_guard(_ok):
    from datetime import datetime, timezone
    from . import ops
    from .core import WfError
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        with tempfile.TemporaryDirectory() as td:
            a = _inst(td, "v184-g", {"state": "EXECUTE", "turn": 2,
                                     "driver": {"inflight": {"pid": p.pid, "started": datetime.now(timezone.utc).isoformat()}}})
            refused = False
            try:
                ops.run_exec(a, "echo hi", by="executor")
            except WfError:
                refused = True
            a.state["paused"] = {"note": "测试", "by": "executor"}
            a.save()
            rc = ops.run_exec(a, "echo hi", by="executor")
    finally:
        p.kill()
        p.wait()
    _ok("1.8.4 手工执行防重：驱动器正在推进时，手工 wf exec 被拒绝；暂停后可以手工执行", refused and rc == 0, str([refused, rc]))


def _t_update_mutex(_ok):
    from . import release
    from .core import WfError
    from unittest.mock import patch
    msg = ""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        with patch.object(release, "WF_HOME", root), release._InstallLock(root, "tool"):
            try:
                # A nonempty request reaches the real target lock before package I/O.
                release.self_update(Path("/nonexistent/collab-wf-9.9.9.zip"), review_id="selftest-lock")
            except WfError as e:
                msg = str(e)
    _ok("安装互斥：同 root 的 self-update 在读包前立即失败、不排队、不安装", "install_busy" in msg, msg)


@send_host("_inst")
def _t_reply_latency(_ok):
    from datetime import datetime, timedelta, timezone
    from . import anomaly, driver
    from .core import parse_iso
    orig = anomaly.call
    res = {}

    def fake(op, args, timeout=None, env_extra=None):
        return {"code": 0, "raw": "", "data": dict(fake.data)} if op == "poll" else {"code": 0, "raw": "", "data": {}}
    pol = {"send_unknown_wait": True, "send_unknown_poll_s": 60, "first_poll_after_s": 120}
    try:
        anomaly.call = fake
        with tempfile.TemporaryDirectory() as td:
            def mk(name, age_s):
                ts = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat(timespec="seconds")
                a = _inst(td, name, {"state": "SEND", "turn": 3, "send_intent": {"turn": 3, "status": "pending", "ts": ts}})
                f = a.outbox / "turn-003.md"
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("【回报 · 第 3 轮 · v0.1】档位：high\n\n正文\n", encoding="utf-8")
                a.state["outgoing"] = {"turn": 3, "file": str(f), "kind": "report", "effort": "high"}
                # A current pending attempt retains its original checked snapshot.
                a.state["send_intent"]["message"] = driver._prepare_send(
                    send_context(a, pol, time.time() + 60), a.state["outgoing"])
                a.save()
                return a
            fake.data = {"status": "none"}
            a = mk("v185-a", 200)
            r1 = driver.h_send(send_context(a, pol, time.time() + 60))
            ra = parse_iso(a.state.get("retry_at"))
            res["gap"] = (ra - datetime.now(timezone.utc)).total_seconds() if ra else None
            b = mk("v185-b", 4000)
            driver.h_send(send_context(b, pol, time.time() + 60))
            res["late"] = b.state.get("state")
            fake.data = {"status": "done", "ref": "R3"}
            c = mk("v185-c", 300)
            r3 = driver.h_send(send_context(c, pol, time.time() + 60))
            pa = parse_iso(c.state.get("poll_after"))
            res["pickup"] = (pa - datetime.now(timezone.utc)).total_seconds() if pa else None
            res["c_state"] = c.state.get("state")
    finally:
        anomaly.call = orig
    _ok("1.8.5 回复发现延迟有界：发送结果不明时确认间隔不超过 send_unknown_poll_s、按墙钟等满窗口才阻塞；"
        "确认时回复已结束就立即去取，不再等 first_poll_after_s",
        r1[0] == "wait" and res["gap"] is not None and res["gap"] <= 61 and res["late"] == "BLOCKED"
        and r3[0] == "progress" and res["c_state"] == "WAIT_REPLY" and res["pickup"] is not None and res["pickup"] <= 1,
        json.dumps([r1, r3, res], ensure_ascii=False, default=str)[:700])


def _t_outgoing_missing(_ok):
    from . import anomaly, driver
    orig, calls = anomaly.call, []

    def fake(op, args, timeout=None, env_extra=None):
        calls.append(op)
        return {"code": 0, "raw": "", "data": {}}
    try:
        anomaly.call = fake
        with tempfile.TemporaryDirectory() as td:
            a = _inst(td, "v185-o", {"state": "SEND", "turn": 9, "outgoing": None})
            r = driver.h_send(driver.Ctx(a, {}, time.time() + 60))
            stt = a.state.get("state")
    finally:
        anomaly.call = orig
    _ok("1.8.5 待发消息核对：状态在“发送”却没有待发消息时，不调用适配器，安全停下并说明",
        "send" not in calls and r[0] == "user" and stt in ("BLOCKED", "AWAITING_USER"), json.dumps([r, calls, stt], ensure_ascii=False))


def _t_single_driver(_ok):
    from datetime import datetime, timezone
    from . import driver
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "wf.py"])
    try:
        with tempfile.TemporaryDirectory() as td:
            a = _inst(td, "v185-d", {"state": "IDLE", "turn": 1,
                                     "driver": {"inflight": {"pid": p.pid, "started": datetime.now(timezone.utc).isoformat()}}})
            code, info = driver.run(a, max_seconds=5)
    finally:
        p.kill()
        p.wait()
    _ok("1.8.5 驱动器单实例：项目状态里登记的另一个驱动器还在运行时，本次跳过（即使两边的 local/ 不同）",
        code == 10 and "另一个驱动器" in str(info.get("note")), json.dumps([code, info.get("note")], ensure_ascii=False))


def _t_ledger(_ok):
    from . import ledger, policy
    text = ("## 失败模式台账\n| 编号 | 日期 | 现象 | 根因 | 处置 | 状态 |\n|---|---|---|---|---|---|\n"
            "| A1 | d | 现象 | 根因 | 1.8.2：新 inode 加原子替换，负向探针 | 已解决（探针覆盖） |\n"
            "| A2 | d | 现象 | 根因 | 加了一条约定 | 已解决 |\n"
            "| A3 | d | 现象 | 待核对 | 1.8.5，自检 | 已解决 |\n"
            "| A4 | d | 现象 | 根因 | 约定 | 已规避（约定） |\n"
            "| A5 | d | 现象 | 根因 | 1.9 | 好像好了 |\n"
            "| A6 | d | 只有三列 |\n\n## 协作约定\n")
    r = ledger.check_rows(ledger.parse(text))
    bad = {v["id"] for v in r["violations"]}
    ro = not (policy.classify("wf ledger", {}) or {}).get("manual")
    _ok("1.8.7 经验闭环检查：标“已解决”却缺根因、版本或回归证据的条目被列出；约定类写“已规避”不算违规；"
        "状态写法不认识、列数不对也列出；wf ledger 是只读命令",
        r["rows"] == 6 and bad == {"A2", "A3", "A5", "A6"} and ro, str([r, ro])[:600])


def _t_reply_source(_ok):
    from . import anomaly, driver
    orig = anomaly.call
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "v186-r", {"state": "WAIT_REPLY", "turn": 5, "send_ref": "R5", "sent_at": "2026-10-12T00:00:00+00:00"})
        rf = Path(td) / "reply.md"
        rf.write_text(REPLY6, encoding="utf-8")

        def fake(op, args, timeout=None, env_extra=None):
            if op == "poll":
                return {"code": 0, "raw": "", "data": {"status": "done", "reply_file": str(rf), "files": [], "reply_parent": "R5",
                                                       "message_uuid": "m-1", "files_message_uuid": "m-2"}}
            return {"code": 0, "raw": "", "data": {}}
        try:
            anomaly.call = fake
            r = driver.h_wait_reply(driver.Ctx(a, {}, time.time() + 60))
            stt = a.state.get("state")
        finally:
            anomaly.call = orig
    _ok("1.8.7 回复与交付同源：适配器报告正文与交付文件来自不同消息时，不收下回复、安全停下",
        r[0] == "user" and stt in ("BLOCKED", "AWAITING_USER"), json.dumps([r, stt], ensure_ascii=False))


def _t_receipt(_ok):
    from . import driver, ops
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "v187-r", {"state": "COMPOSE", "turn": 4})
        f = a.outbox / "turn-004.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("【回报 · 第 4 轮 · v0.1】档位：high\n\n正文\n", encoding="utf-8")
        out = {"turn": 4, "file": str(f)}
        driver._append_receipt(a, out)
        driver._append_receipt(a, out)
        body = f.read_text(encoding="utf-8")
        code = (a.state.get("receipt") or {}).get("code") or ""
        d_ok = ops.parse_directives(f"发布：无\n回执：{code}\n之后：回报\n")
        d_bad = ops.parse_directives("发布：无\n回执：deadbeef\n之后：回报\n")
        e1 = driver._receipt_and_deliveries(a, d_ok)
        e2 = driver._receipt_and_deliveries(a, d_bad)
    _ok("1.8.7 回执核对：发出的消息末尾附回执码（同一轮只附一次）；回复写回同一个码时放行，写错就在评审前停下",
        body.count("回执码：") == 1 and len(code) == 8 and d_ok["receipt"] == code and e1 == "" and "不一致" in e2,
        json.dumps([body[-120:], code, e1, e2], ensure_ascii=False))


def _t_delivery_check(_ok):
    import hashlib
    from . import driver, ops
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "v187-d", {"state": "INTAKE", "turn": 6})
        dd = Path(td) / "delivered"
        dd.mkdir()
        z = dd / "proj-v1.zip"
        z.write_bytes(b"zip bytes")
        h = hashlib.sha256(b"zip bytes").hexdigest()
        a.state["delivered"] = [str(z)]
        a.save()
        good = ops.parse_directives(f"发布：proj-v1.zip\n交付 1：proj-v1.zip {h}\n之后：回报\n")
        wrong = ops.parse_directives(f"发布：proj-v1.zip\n交付 1：proj-v1.zip {'0' * 64}\n之后：回报\n")
        miss = ops.parse_directives(f"发布：proj-v1.zip\n交付 1：other.py {h}\n之后：回报\n")
        r = [driver._receipt_and_deliveries(a, x) for x in (good, wrong, miss)]
    _ok("1.8.7 交付核对：声明的交付文件 sha256 一致才放行；不符、没送达、要落地的版本包没声明，都在评审前停下",
        r[0] == "" and "不符" in r[1] and "没有送达" in r[2] and "没有写在交付行里" in r[2], json.dumps(r, ensure_ascii=False))


@send_host("_inst")
def _t_arbiter(_ok):
    from . import anomaly, arbiter, driver
    from .core import Instance, LOCAL
    shared = LOCAL / "shared"
    for n in ("arbiter.json", "arbiter.jsonl"):
        if (shared / n).exists():
            (shared / n).unlink()
    orig, calls = anomaly.call, []
    with tempfile.TemporaryDirectory() as td:
        a, b, c = (_inst(td, f"v188-{x}", {"state": "SEND", "turn": 1}) for x in "abc")
        g1, g2, g3 = arbiter.acquire(a, 1), arbiter.acquire(b, 1), arbiter.acquire(c, 1)
        arbiter.release(a, "测试", lease_id=g1["lease_id"], turn=1)   # IC-3（B1-A 契约）：释放新授予必须带本次 lease_id 与轮号
        g4, g5 = arbiter.acquire(c, 1), arbiter.acquire(b, 1)
        bb = Instance(b.root)
        bb.state["state"] = "IDLE"
        bb.save()
        g6 = arbiter.acquire(c, 1)
        log = (shared / "arbiter.jsonl").read_text(encoding="utf-8")
        d = _inst(td, "v188-d", {"state": "SEND", "turn": 2})
        f = d.outbox / "turn-002.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("【回报 · 第 2 轮 · v0.1】档位：high\n\n正文\n", encoding="utf-8")
        d.state["outgoing"] = {"turn": 2, "file": str(f), "kind": "report", "effort": "high"}
        d.save()
        try:
            anomaly.call = lambda op, args, timeout=None, env_extra=None: calls.append(op) or {"code": 0, "raw": "", "data": {}}
            r = driver.h_send(send_context(d, {"shared_max_inflight": 1}, time.time() + 60))
        finally:
            anomaly.call = orig
    ok = (g1["granted"] and not g2["granted"] and g2["position"] == 1 and not g3["granted"] and g3["position"] == 2
          and not g4["granted"] and g5["granted"] and g6["granted"]
          and all(f'"event": "{e}"' in log for e in ("wait", "grant", "release", "reclaim"))
          and r[0] == "wait" and "send" not in calls)
    _ok("1.8.8 共享仲裁（S4）：同一账号的发送槽先到先得；释放后队首先拿到，后来的不能插队；持有方已不在发送或等回复时被回收；"
        "wait、grant、release、reclaim 都留痕；槽被占用时 h_send 不调用适配器",
        ok, json.dumps([g1, g2, g3, g4, g5, g6, r, calls], ensure_ascii=False)[:800])
    # IC-3：显式的旧记录夹具（升级前写下的、没有 lease_id 的持有记录）。不带 token 的旧式调用只能释放这种旧记录；
    # 遇到带 lease_id 的新记录必须拒绝、记录保持不变；带上实际 token 才能释放新记录。
    for n in ("arbiter.json", "arbiter.jsonl"):
        if (shared / n).exists():
            (shared / n).unlink()
    with tempfile.TemporaryDirectory() as td:
        e = _inst(td, "v188-e", {"state": "SEND", "turn": 3})
        f2 = _inst(td, "v188-f", {"state": "SEND", "turn": 1})
        shared.mkdir(parents=True, exist_ok=True)
        legacy = {"project": e.name, "turn": 3, "root": str(e.root), "since": "2026-10-14T00:00:00+00:00"}
        (shared / "arbiter.json").write_text(json.dumps({"holders": [legacy], "queue": []}, ensure_ascii=False), encoding="utf-8")
        r_legacy = arbiter.release(e, "测试：旧记录")
        held_after_legacy = [h.get("project") for h in json.loads((shared / "arbiter.json").read_text(encoding="utf-8"))["holders"]]
        gf = arbiter.acquire(f2, 1)
        r_notoken = arbiter.release(f2, "测试：旧式调用遇到新记录")
        hs = json.loads((shared / "arbiter.json").read_text(encoding="utf-8"))["holders"]
        still = [h for h in hs if h.get("project") == f2.name and h.get("lease_id") == gf.get("lease_id")]
        r_token = arbiter.release(f2, "测试：带 token", lease_id=gf.get("lease_id"), turn=1)
        after = [h for h in json.loads((shared / "arbiter.json").read_text(encoding="utf-8"))["holders"] if h.get("project") == f2.name]
    _ok("IC-3 共享仲裁旧记录：不带 token 的旧式释放只能释放没有 lease_id 的旧记录",
        isinstance(r_legacy, dict) and r_legacy.get("released") is True and e.name not in held_after_legacy,
        json.dumps([r_legacy, held_after_legacy], ensure_ascii=False)[:600])
    _ok("IC-3 共享仲裁新记录：不带 token 的旧式释放遇到带 lease_id 的新授予被拒绝、记录保持不变；带实际 token 才释放",
        gf.get("granted") is True and bool(gf.get("lease_id")) and isinstance(r_notoken, dict) and r_notoken.get("released") is False
        and len(still) == 1 and isinstance(r_token, dict) and r_token.get("released") is True and not after,
        json.dumps([gf, r_notoken, still, r_token, after], ensure_ascii=False)[:800])


def _t_review_gate(_ok):
    from . import anomaly, core, review, release
    from .core import Instance, WfError
    from .selftest_review import Env
    from unittest.mock import patch
    res = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        with patch.object(core, "LOCAL", root / "local"), patch.object(anomaly, "LOCAL", root / "local"):
            env = Env(root)
            with patch.object(review, "_tool_home", lambda: env.tool):
                target = env.project("gate")
                package = env.pkg("gate")
                before = (target / "app.py").read_bytes()
                for cfg in ({}, {"review_required": False}, {"review_required": True}):
                    env.transport.write_text(json.dumps(cfg))
                    req = review.prepare(target, package, kind="project", author="B")
                    decision = review.run(req["request_id"])
                    denied = False
                    try:
                        release.apply_zip(Instance(target), package, review_id=req["request_id"])
                    except WfError:
                        denied = True
                    res.append(not decision["allowed"] and "runner_unavailable" in decision["reasons"]
                               and denied and (target / "app.py").read_bytes() == before)
                env.set_transport()
                fresh = review.prepare(target, package, kind="project", author="B")
                positive = review.run(fresh["request_id"])
    _ok("安装前审查：无 runner 时始终拒绝、目标不变（review_required 不能旁路）；临时 synthetic runner/verifier 正例仍可审查",
        all(res) and positive["allowed"] and positive["provenance"]["synthetic"],
        json.dumps({"negative": res, "positive": positive}, ensure_ascii=False))


@send_host("_inst")
def _t_heartbeat_no_resend(_ok):
    from . import anomaly, driver
    orig, calls = anomaly.call, []

    def fake(op, args, timeout=None, env_extra=None):
        calls.append(op)
        if op == "send":
            return {"code": fake.send_code, "raw": "心跳", "data": {"status": "heartbeat_only"}}
        if op == "poll":
            return {"code": 0, "raw": "", "data": {"status": "done", "ref": "R1"}}
        return {"code": 0, "raw": "", "data": {}}
    res = []
    try:
        anomaly.call = fake
        with tempfile.TemporaryDirectory() as td:
            for code in (0, 2):
                fake.send_code = code
                a = _inst(td, f"v189-h{code}", {"state": "SEND", "turn": 2})
                f = a.outbox / "turn-002.md"
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("【回报 · 第 2 轮 · v0.1】档位：high\n\n正文\n", encoding="utf-8")
                a.state["outgoing"] = {"turn": 2, "file": str(f), "kind": "report", "effort": "high"}
                a.save()
                pol = {"send_unknown_wait": True}
                n0 = len(calls)
                r1 = driver.h_send(send_context(a, pol, time.time() + 60))
                kept = (a.state.get("send_intent") or {}).get("status") == "pending"
                a.state.pop("retry_at", None)
                a.save()
                r2 = driver.h_send(send_context(a, pol, time.time() + 60))
                res.append([code, r1[0], kept, r2[0], a.state.get("state"), calls[n0:].count("send")])
    finally:
        anomaly.call = orig
    _ok("1.8.9 心跳不重发：发送只收到心跳（退出码 0 或 2）时保留发送意图、按结果不明轮询确认，确认已送达就接上，整个过程只发一次",
        all(x[2] and x[3] == "progress" and x[4] == "WAIT_REPLY" and x[5] == 1 for x in res), json.dumps(res, ensure_ascii=False))


def _t_live_check(_ok):
    import hashlib
    from datetime import datetime, timedelta, timezone
    from . import anomaly, core, live
    from .core import LOCAL
    orig = anomaly.transport_cfg
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, f"v192-l{os.getpid()}", {"state": "IDLE", "turn": 8})
        base = datetime(2026, 10, 13, 6, 0, tzinfo=timezone.utc)
        iso = lambda dt: dt.isoformat(timespec="seconds")
        zname = f"collab-wf-9.9.{os.getpid()}"
        zp = a.root / "workflow" / "outbox" / f"{zname}.zip"
        zp.parent.mkdir(parents=True, exist_ok=True)
        zp.write_bytes(b"pkg")
        # 1.9.4：同步记的 HEAD 要等于项目当时的 HEAD——给临时项目建版本库，提交时刻早于这三轮
        gd = {**os.environ, "GIT_AUTHOR_DATE": iso(base - timedelta(days=1)), "GIT_COMMITTER_DATE": iso(base - timedelta(days=1))}
        subprocess.run(["git", "init", "-q"], cwd=a.root)
        subprocess.run(["git", "add", "-A"], cwd=a.root)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=a.root, env=gd)
        real_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=a.root, capture_output=True, text=True).stdout.strip()
        turns, events, alog, hist = [], [], [], []
        for i, t in enumerate((5, 6, 7)):
            t0 = base + timedelta(minutes=30 * i)
            turns += [{"turn": t, "status": "sent", "from": "driver", "ts_sent": iso(t0)},
                      {"turn": t, "status": "done", "ts_done": iso(t0 + timedelta(minutes=10))}]
            events += [{"type": "reply_binding", "turn": t, "receipt": "ok", "deliveries": "ok", "by": "driver", "actor": "driver"},
                       {"type": "reply_checked", "turn": t, "by": "driver", "actor": "driver"},
                       {"type": "transition", "turn": t, "by": "wf", "actor": "driver"},
                       {"type": "round_result", "turn": t, "ok": True, "by": "driver", "actor": "driver"}]
            alog += [{"action": "send", "turn": t, "ts": iso(t0), "project": a.name}]
            hist.append({"turn": t, "status": "pushed", "commit": "abc1234def", "head": real_head, "ts": iso(t0 + timedelta(minutes=12))})
            d = a.evidence / f"turn-{t:03d}"
            d.mkdir(parents=True, exist_ok=True)
            cm = [f"wf self-update --auto workflow/outbox/{zname}.zip"] if t == 7 else []
            (d / "intake.json").write_text(json.dumps({"commands": cm}), encoding="utf-8")
            (d / "reply_meta.json").write_text(json.dumps({"ended_at": iso(t0 + timedelta(minutes=9)), "parent": f"H{t}"}), encoding="utf-8")
            (d / "send_meta.json").write_text(json.dumps({"message_uuid": f"H{t}"}), encoding="utf-8")
        events.append({"type": "apply", "turn": 6, "by": "driver", "actor": "driver"})
        events.append({"type": "exec", "turn": 7, "cmd": f"wf self-update --auto workflow/outbox/{zname}.zip", "exit_code": 0,
                       "by": "driver", "actor": "driver", "ts": iso(base + timedelta(minutes=75)), "duration_s": 600})
        (LOCAL / "reviews").mkdir(parents=True, exist_ok=True)
        rp = LOCAL / "reviews" / f"{zname}.result.json"
        rgood = {"verdict": "pass", "ts": iso(base + timedelta(minutes=70)), "zip_sha256": hashlib.sha256(b"pkg").hexdigest()}
        rp.write_text(json.dumps(rgood), encoding="utf-8")
        (LOCAL / "sync").mkdir(parents=True, exist_ok=True)
        hp = LOCAL / "sync" / f"{a.name}.history.jsonl"
        hp.write_text("".join(json.dumps(h) + "\n" for h in hist), encoding="utf-8")
        a.turns_path.write_text("".join(json.dumps(r) + "\n" for r in turns), encoding="utf-8")
        a.events_path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        lp = Path(td) / "bridge.jsonl"
        wl = lambda xs: lp.write_text("".join(json.dumps(x) + "\n" for x in xs), encoding="utf-8")
        res = {}
        old_actor = core.ACTOR.get("actor")
        try:
            anomaly.transport_cfg = lambda: {"log": str(lp)}
            wl(alog)
            res["ok"] = live.check(a, 3)
            wl(alog + [{"action": "send", "turn": 6, "ts": iso(base), "project": a.name}])
            res["dup"] = live.check(a, 3)
            wl([{k: v for k, v in x.items() if k != "project"} for x in alog])
            res["noproj"] = live.check(a, 3)
            wl(alog)
            a.events_path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events if e.get("type") != "apply"),
                                     encoding="utf-8")
            res["noapply"] = live.check(a, 3)
            a.events_path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
            # 1.9.4：审查结论早于这次安装命令（不是这次取得的）、没有时刻、晚于开始安装，都不通过
            rp.write_text(json.dumps(dict(rgood, ts=iso(base + timedelta(minutes=10)))), encoding="utf-8")
            res["oldreview"] = live.check(a, 3)
            rp.write_text(json.dumps(dict(rgood, ts="")), encoding="utf-8")
            res["notime"] = live.check(a, 3)
            rp.write_text(json.dumps(dict(rgood, install_started_at=iso(base + timedelta(minutes=68)))), encoding="utf-8")
            res["afterinstall"] = live.check(a, 3)
            rp.write_text(json.dumps(dict(rgood, install_started_at=iso(base + timedelta(minutes=72)))), encoding="utf-8")
            res["beforeinstall"] = live.check(a, 3)
            rp.write_text(json.dumps(rgood), encoding="utf-8")
            # 1.9.4：同步记的 HEAD 与项目当时的 HEAD 不一致，不通过
            hp.write_text("".join(json.dumps(dict(h, head="f00d" * 10)) + "\n" for h in hist), encoding="utf-8")
            res["badhead"] = live.check(a, 3)
            hp.write_text("".join(json.dumps(h) + "\n" for h in hist), encoding="utf-8")
            core.ACTOR["actor"] = "driver"
            e1 = a.event("note", by="wf")
            core.ACTOR["actor"] = "cli"
            e2 = a.event("note", by="wf")
        finally:
            anomaly.transport_cfg = orig
            core.ACTOR["actor"] = old_actor or "cli"
            hp.unlink()
            rp.unlink()
    ok = (res["ok"]["passed"] and res["ok"]["streak"] == 3 and not res["dup"]["passed"] and res["dup"]["streak"] == 1
          and not res["noproj"]["passed"] and not res["noapply"]["passed"] and res["noapply"]["missing"]
          and not res["oldreview"]["passed"] and not res["notime"]["passed"] and not res["afterinstall"]["passed"]
          and res["beforeinstall"]["passed"] and not res["badhead"]["passed"]
          and e1.get("actor") == "driver" and e2.get("actor") == "cli"
          and not live._manual({"type": "transition", "by": "wf", "actor": "driver"})
          and live._manual({"type": "transition", "by": "wf", "actor": "cli"}))
    _ok("1.9.4 实景测量：证据齐全的三轮通过（驱动器进程里的 wf 转换不算人工）；一轮发两次、日志没有项目字段、"
        "连续达标的轮次里没有项目落地、审查结论不是这次安装时取得的或没有时刻或晚于开始安装、同步记的 HEAD 与项目当时的不符，"
        "都不通过；事件带上写入者身份",
        ok, json.dumps({k: [v["streak"], v["passed"], v.get("missing")] for k, v in res.items()}, ensure_ascii=False)[:600])


def _t_task_progress(_ok):
    from . import tasks
    from .core import WfError
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "v190-p", {"state": "VERIFY", "turn": 3})
        gen = {x["id"]: x["generation"] for x in tasks.add(a, 3, {"1": "长任务"})}   # B2-A1：代次取自刚登记返回的条目
        t = tasks.progress(a, "T3-1", "做完第一步；下一步核对日志；证据在 outputs/x.txt", generation=gen["T3-1"])
        empty = False
        try:
            tasks.progress(a, "T3-1", "", generation=gen["T3-1"])   # 带正确代次：拒绝来自空检查点
        except WfError:
            empty = True
        still_open = [x["id"] for x in tasks.open_items(a)] == ["T3-1"]
    _ok("1.9.0 待办检查点：执行端被打断时可以留下检查点，待办仍保持未关闭；空检查点被拒绝",
        len(t.get("checkpoints") or []) == 1 and empty and still_open, str([t.get("checkpoints"), empty, still_open]))


@send_host("_inst")
def _t_send_code4(_ok):
    from . import anomaly, driver
    orig, calls = anomaly.call, []

    def fake(op, args, timeout=None, env_extra=None):
        calls.append(op)
        if op == "send":
            return {"code": 4, "raw": "超时", "data": {}}
        return {"code": 0, "raw": "", "data": {"status": "done", "ref": "R"}}
    try:
        anomaly.call = fake
        with tempfile.TemporaryDirectory() as td:
            a = _inst(td, "v190-c4", {"state": "SEND", "turn": 2})
            f = a.outbox / "turn-002.md"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("【回报 · 第 2 轮 · v0.1】档位：high\n\n正文\n", encoding="utf-8")
            a.state["outgoing"] = {"turn": 2, "file": str(f), "kind": "report", "effort": "high"}
            a.save()
            r1 = driver.h_send(send_context(a, {"send_unknown_wait": True}, time.time() + 60))
            kept = (a.state.get("send_intent") or {}).get("status") == "pending"
            a.state.pop("retry_at", None)
            a.save()
            r2 = driver.h_send(send_context(a, {"send_unknown_wait": True}, time.time() + 60))
    finally:
        anomaly.call = orig
    _ok("1.9.0 发送暂时性错误：按结果不明保留意图、提示“再核对，不重发”，确认已送达就接上，只发一次",
        kept and "不重发" in r1[1] and r2[0] == "progress" and calls.count("send") == 1, json.dumps([r1, r2, calls], ensure_ascii=False))


def _t_task_markers(_ok):
    from . import notify, tasks
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "v191-m", {"state": "VERIFY", "turn": 4})
        tasks.add(a, 4, {"1": "核对日志", "2": "（依赖 1）根据日志改参数", "3": "（不阻塞）连续 3 轮不做手工改动"})
        ts = {t["id"]: t for t in a.state["executor_tasks"]}
        block = [t["id"] for t in tasks.blocking_items(a)]
        rows = {r["key"].split("|")[-1]: r for r in notify.collect_inbox([a])}
    _ok("1.9.1 待办标记：（不阻塞）的待办不让驱动器等它、照样进 inbox；（依赖 M）写成明确的 depends_on 字段，inbox 原样带出",
        ts["T4-2"].get("depends_on") == ["T4-1"] and ts["T4-2"]["text"] == "根据日志改参数" and ts["T4-3"].get("nonblocking")
        and block == ["T4-1", "T4-2"] and rows.get("task:T4-2", {}).get("depends_on") == ["T4-1"]
        and rows.get("task:T4-3", {}).get("nonblocking") is True,
        json.dumps([ts, block, list(rows)], ensure_ascii=False)[:700])


def _t_no_desktop_notify(_ok):
    # 1.9.4（W95）：1.9.3 的这条检查吞掉异常、按 hasattr 调一个不存在的 run_once，证明不了真实的通知路径。
    # 现在走真实路径 notify.after_tick：把平台与 osascript 换成测试替身（替身只记录、不执行），
    # 先在 WF_NOTIFY=1 下确认这条路径确实会发通知（对照），再在自检的 WF_NOTIFY=0 下确认一条都不发。
    from . import notify
    sent = []
    orig = (notify.subprocess.run, notify.sys.platform, notify.shutil.which)
    old_env = os.environ.get("WF_NOTIFY")

    class _Done:
        returncode = 0
        stdout = stderr = ""

    def spy(args, *a, **k):
        if args and args[0] == "osascript":
            sent.append(list(args))
            return _Done()
        return orig[0](args, *a, **k)
    res = {}
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "v194-n", {"state": "BLOCKED", "turn": 1, "block_reason": "测试：自检里的阻塞"})
        try:
            notify.subprocess.run = spy
            notify.sys.platform = "darwin"
            notify.shutil.which = lambda name, *x, **k: "/usr/bin/osascript" if name == "osascript" else orig[2](name, *x, **k)
            os.environ["WF_NOTIFY"] = "1"
            res["control"] = len(notify.after_tick([a], store=Path(td) / "n1.json"))
            res["control_calls"] = len(sent)
            os.environ["WF_NOTIFY"] = "0"
            res["off"] = len(notify.after_tick([a], store=Path(td) / "n2.json"))
            res["off_calls"] = len(sent) - res["control_calls"]
        finally:
            notify.subprocess.run, notify.sys.platform, notify.shutil.which = orig
            if old_env is None:
                os.environ.pop("WF_NOTIFY", None)
            else:
                os.environ["WF_NOTIFY"] = old_env
    _ok("1.9.4 自检不发真实系统通知：对照组（WF_NOTIFY=1、替身 osascript）确实会发；WF_NOTIFY=0 时一条不发；"
        "自检进程本身从第一条检查起就是 WF_NOTIFY=0",
        res.get("control_calls", 0) >= 1 and res.get("off_calls") == 0 and res.get("off") == 0 and old_env == "0"
        and os.environ.get("WF_NOTIFY") == "0",
        json.dumps([res, old_env], ensure_ascii=False))


def _t_selftest_env(_ok):
    # 1.9.4（W95）：自检子进程的环境由 child_env 生成，父进程的环境一项不改；当前进程里的检查不写工具目录自己的 local/
    from . import core, selftest
    base = {"WF_NOTIFY": "1", "VIRTUAL_ENV": "/v", "KEEP": "k"}
    snap = dict(base)
    env = selftest.child_env(base, "/tmp/x/local")
    base2 = {"KEEP": "k"}
    unset = selftest.child_env(base2, "/tmp/y/local")
    local_ok = core.LOCAL.resolve() != (core.WF_HOME / "local").resolve()
    _ok("1.9.4 自检环境隔离：子进程 WF_NOTIFY=0、WF_LOCAL_DIR 指向临时目录、去掉 VIRTUAL_ENV；父进程的环境字典原样不动"
        "（原来是 1 的仍是 1，原来没有的仍没有）；本进程的检查不写工具目录自己的 local/",
        env["WF_NOTIFY"] == "0" and env["WF_LOCAL_DIR"] == "/tmp/x/local" and "VIRTUAL_ENV" not in env
        and env.get("KEEP") == "k" and base == snap and unset["WF_NOTIFY"] == "0" and "WF_NOTIFY" not in base2
        and os.environ.get("WF_NOTIFY") == "0" and local_ok,
        json.dumps([env, base, str(core.LOCAL), os.environ.get("WF_NOTIFY")], ensure_ascii=False))


def _t_actor_restore(_ok):
    # 1.9.4（审查 concerns）：driver.run 把事件身份记作 driver，结束后（含出错）要恢复原值，不能留在进程里
    from . import core, driver
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "v194-a", {"state": "IDLE", "turn": 1})
        before = core.ACTOR.get("actor")
        driver.run(a, max_seconds=5)
        after = core.ACTOR.get("actor")
        orig = driver._run_body

        def boom(*x, **k):
            raise RuntimeError("测试：驱动器中途出错")
        driver._run_body = boom
        try:
            try:
                driver.run(a, max_seconds=5)
            except RuntimeError:
                pass
        finally:
            driver._run_body = orig
        after_err = core.ACTOR.get("actor")
    _ok("1.9.4 事件身份：driver.run 结束后、以及中途出错后，ACTOR 都恢复为进入前的值",
        before == "cli" and after == "cli" and after_err == "cli", str([before, after, after_err]))


@send_host("_inst")
def _t_send_meta_poll(_ok):
    # 1.9.4（审查 concerns）：发送结果不明、之后经轮询确认送达时，也记下发出消息的 uuid（适配器在 poll 里给出 message_uuid）
    from . import anomaly, driver
    orig = anomaly.call
    out = {}
    for case, poll_uuid in (("with", "U-7"), ("without", None)):
        def fake(op, args, timeout=None, env_extra=None, _u=poll_uuid):
            if op == "send":
                return {"code": 4, "raw": "超时", "data": {}}
            d = {"status": "done", "ref": "R"}
            if _u:
                d["message_uuid"] = _u
            return {"code": 0, "raw": "", "data": d}
        try:
            anomaly.call = fake
            with tempfile.TemporaryDirectory() as td:
                a = _inst(td, f"v194-sm-{case}", {"state": "SEND", "turn": 2})
                f = a.outbox / "turn-002.md"
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("【回报 · 第 2 轮 · v0.1】档位：high\n\n正文\n", encoding="utf-8")
                a.state["outgoing"] = {"turn": 2, "file": str(f), "kind": "report", "effort": "high"}
                a.save()
                driver.h_send(send_context(a, {"send_unknown_wait": True}, time.time() + 60))
                a.state.pop("retry_at", None)
                a.save()
                driver.h_send(send_context(a, {"send_unknown_wait": True}, time.time() + 60))
                mp = a.turn_dir(2) / "send_meta.json"
                out[case] = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else None
        finally:
            anomaly.call = orig
    w = out.get("with") or {}
    _ok("1.9.4 发送结果不明后经轮询确认：poll 给了 message_uuid 就记进 send_meta.json（注明经轮询确认）；没给就不编造",
        w.get("message_uuid") == "U-7" and w.get("confirmed_by") == "poll" and out.get("without") is None,
        json.dumps(out, ensure_ascii=False))


def _t_sync_history_head(_ok):
    # 1.9.4：1.9.1 写同步历史时用错了 git 的参数顺序（TypeError，外层只接 OSError），只有带 .git 的项目才会走到；
    # 1.8.2 的同步测试用的是没有 .git 的临时项目，所以没测到（执行端第 128 轮在本机手工改正，本版正式收进来）
    from . import anomaly, sync
    orig = (sync.cfg, sync.SYNC_DIR, anomaly.call, sync.collect)
    res, head, err = {}, "", ""
    with tempfile.TemporaryDirectory() as td:
        inst = _inst(td, "v194-sh", {"state": "IDLE", "turn": 3})
        g = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(["git", "init", "-q"], cwd=inst.root)
        subprocess.run(["git", "add", "-A"], cwd=inst.root)
        subprocess.run(g + ["commit", "-qm", "init"], cwd=inst.root)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=inst.root, capture_output=True, text=True).stdout.strip()
        try:
            sync.cfg = lambda: {"sync": "测试同步程序", "sync_projects": [inst.name]}
            sync.SYNC_DIR = Path(td) / "local-sync"
            sync.collect = lambda i: {"STATUS.md": b"s\n"}
            anomaly.call = lambda op, args, timeout=None, env_extra=None: {"code": 0, "raw": "",
                                                                           "data": {"commit": "0123456789abcdef"}}
            try:
                res = sync.run(inst)
            except Exception as e:   # noqa: BLE001  正是要测出来的错误
                err = f"{type(e).__name__}: {e}"
            hist = sync.SYNC_DIR / f"{inst.name}.history.jsonl"
            rows = [json.loads(x) for x in hist.read_text(encoding="utf-8").splitlines()] if hist.exists() else []
        finally:
            sync.cfg, sync.SYNC_DIR, anomaly.call, sync.collect = orig
    _ok("1.9.4 同步历史：带 .git 的项目推送后不出错，历史记录里的 head 等于项目当时的 HEAD",
        not err and res.get("status") == "pushed" and rows and rows[-1].get("head") == head and len(head) == 40,
        json.dumps([err, res.get("status"), rows[-1:] if rows else None, head], ensure_ascii=False)[:600])


def _t_goal_check_readonly(_ok):
    # 1.9.4（审查 concerns）：目标的检查命令要能证明是只读的——只拒“要人批准的 wf 命令”不够（rm、重定向都能混进来）
    from . import goals, policy
    base = {"id": "X", "title": "t", "kind": "exec", "pass": {"exit": 0}, "means": "m", "on_fail": "f"}
    cases = {"wf self-check --probes": True, "wf live-check --rounds 3": True, "wf goals": True,
             "python3 workflow/m1_check.py --json": True, "grep -n x workflow/a.txt | wc -l": True,
             "rm -rf build": False, "wf apply x.zip": False, "wf self-check > out.txt": False,
             "cat a.txt | sh": False, "python3 /tmp/x.py": False, "python3 ../x.py": False, "curl -X POST http://x": False}
    got = {}
    for cmd, want in cases.items():
        iss = goals.validate({"goals": [dict(base, check=cmd)]})
        got[cmd] = (not iss, policy.readonly_check(cmd))
    _ok("1.9.4 目标的检查命令必须能证明只读：wf 的只读命令、只读程序、项目里受版本管理的 python 脚本可以；"
        "rm、要人批准的 wf 命令、重定向、管道接非过滤命令、项目外的脚本都不行",
        all(got[c] == (w, w) for c, w in cases.items()), json.dumps(got, ensure_ascii=False)[:700])


def _t_selftest_failure_report(_ok):
    # 1.9.4（W95）：自检失败时，安装命令只回显最后 3000 个字符，失败的那一行可能不在里面（第 125 轮 278/279 就是这样）。
    # 现在完整输出写进文件，提示里列出全部 ✗ 行与文件路径
    from . import release
    with tempfile.TemporaryDirectory() as td:
        lines = ["collab-wf 自检（9.9.9）", "  ✗ 很早就失败的一项  —— 细节"] + [f"  ✓ 第 {i} 项" for i in range(400)]
        lines += ["  ✗ 最后失败的一项", "", "自检结果：400 / 402 通过"]
        out = "\n".join(lines) + "\n"
        msg, path = release.selftest_failure_report("9.9.9", out, "stderr 原文\n", Path(td))
        full = Path(path).read_text(encoding="utf-8") if path and Path(path).exists() else ""
    _ok("1.9.4 自检失败的报告：完整输出写进文件；提示里有全部 ✗ 行（包括远在末尾 3000 字符之外的）与文件路径",
        "很早就失败的一项" in msg and "最后失败的一项" in msg and str(path) in msg and out in full and "stderr 原文" in full,
        msg[:600])


def _t_explain_executor_wait(_ok):
    # 1.9.5（W101）：驱动器在等执行端的待办时，explain 写明“需要：执行端”与待办编号，不再是“脚本、无需操作”
    from . import driver, tasks
    orig = driver.policy.status
    driver.policy.status = lambda inst, kind: {"approved": True, "exists": True, "changed_since_approval": False}
    try:
        e1, e2 = _explain_wait_case(driver, tasks)
    finally:
        driver.policy.status = orig
    _ok("1.9.5 等执行端时的说明：有未完成的阻塞待办时写“执行端”与待办编号、说明定时器不会替执行端完成；"
        "只剩不阻塞的待办时照旧",
        e1.get("who") == "执行端" and "T4-1" in e1.get("why", "") and "T4-2" not in e1.get("why", "")
        and "wf task done" in e1.get("what", "") and e2.get("who") != "执行端",
        json.dumps([e1, e2], ensure_ascii=False)[:600])


def _explain_wait_case(driver, tasks):
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "v195-x", {"state": "WAIT_SCHEDULE", "turn": 4, "wait_until": "2026-10-14T10:00:00+00:00",
                                 "wait_reason": "等执行端"})
        tasks.add(a, 4, {"1": "核对日志", "2": "（不阻塞）顺带整理"})
        e1 = driver.explain(a)
        for t in a.state["executor_tasks"]:
            if t["id"] == "T4-1":
                t["done"] = "2026-10-14T09:00:00+00:00"
        a.save()
        e2 = driver.explain(a)
    return e1, e2


def _t_tell_lossless(_ok):
    # 1.9.5（W98）：wf tell 不再按 4000 字符静默截断：超长分段并写明全文长度与 sha256；超过上限拒绝排队
    import hashlib
    from . import driver, ops
    from .core import WfError
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "v195-t", {"state": "IDLE", "turn": 2})
        long = "  \n" + "".join(f"第{i:05d}行原文；" for i in range(1200)) + "\n\n  "   # 约 1.2 万字，首尾带空白
        r = ops.queue_tell(a, long)
        q = list(a.state.get("pending_tell") or [])
        k = len(q)
        rebuilt = ""
        for i, x in enumerate(q, 1):   # 1.9.6：按段头、段尾精确拆回，必须逐字等于原文（含首尾空白）
            head = f"（第 {i}/{k} 段；全文 {len(long)} 字，sha256 {r['sha256']}）"
            body_i = x["text"][len(head):] if x["text"].startswith(head) else None
            if body_i is None:
                rebuilt = None
                break
            if i == k:
                body_i = body_i[:-len("（全文结束）")] if body_i.endswith("（全文结束）") else None
            if body_i is None:
                rebuilt = None
                break
            rebuilt += body_i
        f = a.outbox / "turn-003.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("【回报 · 第 3 轮 · v0.1】档位：high\n\n正文\n", encoding="utf-8")
        driver._append_tell(a, {"file": str(f), "turn": 3})
        body = f.read_text(encoding="utf-8")
        try:
            ops.queue_tell(a, "x" * (ops.TELL_MAX + 1))
            refused = False
        except WfError as e:
            refused = "没有排队" in str(e)
        left = a.state.get("pending_tell")
    joined = "".join(x["text"] for x in q)
    _ok("1.9.6 wf tell 无损：超过 4000 字分段排队，每段写明第几段、全文长度与完整 sha256；按段头段尾拆回后逐字等于原文"
        "（含首尾空白）；超过单次上限拒绝、不截断",
        rebuilt == long and r["parts"] == len(q) >= 3 and r["chars"] == len(long)
        and r["sha256"] == hashlib.sha256(long.encode()).hexdigest()
        and all(long[i * ops.TELL_PART:(i + 1) * ops.TELL_PART] in q[i]["text"] for i in range(len(q)))
        and f"第 1/{len(q)} 段" in joined and "全文结束" in joined and all(x["text"] in body for x in q) and refused and not left,
        json.dumps([r, len(q), refused], ensure_ascii=False))


def _t_watchdog_scope(_ok):
    # 1.9.5：wf watchdog --project 只检查这个项目；重写 inbox 时带上全部项目，不抹掉别的项目的事项
    from . import notify, watchdog
    calls = {}
    orig = (watchdog.check, notify.write_inbox)
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "v195-wa", {"state": "IDLE", "turn": 1})
        b = _inst(td, "v195-wb", {"state": "IDLE", "turn": 1})
        try:
            watchdog.check = lambda inst: calls.setdefault("checked", []).append(inst.name) or []
            notify.write_inbox = lambda projects, path=None, items=None, **k: calls.setdefault("inbox", []).extend(i.name for i in projects)
            watchdog.run([a], write=True, inbox_insts=[a, b])
        finally:
            watchdog.check, notify.write_inbox = orig
        ib = Path(td) / "inbox.json"
        ghost = {"key": "ghost|x", "project": "ghost-proj", "for": "executor", "title": "读不出的项目原有的事项",
                 "since": "2026-10-14T00:00:00+00:00"}
        ib.write_text(json.dumps({"items": [ghost]}), encoding="utf-8")
        notify.write_inbox([a], path=ib, keep_projects={"ghost-proj"})
        # 1.9.7：原样保留——逐字段相等（此前会经过 2 小时升级循环被改写字段）
        kept = [x for x in json.loads(ib.read_text(encoding="utf-8"))["items"] if x.get("key") == "ghost|x"] == [ghost]
        notify.write_inbox([a], path=ib)
        dropped = not any(x.get("key") == "ghost|x" for x in json.loads(ib.read_text(encoding="utf-8"))["items"])
    _ok("1.9.7 看门狗按项目：只检查指定的项目；重写 inbox 时带上全部项目；这次读不出状态的项目，inbox 里原有的事项逐字段原样保留（不经升级循环）",
        calls.get("checked") == [a.name] and sorted(calls.get("inbox") or []) == sorted([a.name, b.name]) and kept and dropped,
        json.dumps([calls, kept, dropped], ensure_ascii=False))


def _t_truncated_atomic(_ok):
    # 1.9.6（1.9.5 安装前审查 block）：完整性判据必须在转入 REVIEW 之前；评审、落地、执行共用守卫；命令行 intake 同样拦下
    from . import driver, ops
    from .core import WfError
    res = {}
    with tempfile.TemporaryDirectory() as td:
        rf = Path(td) / "reply.md"
        rf.write_text(REPLY6, encoding="utf-8")
        cut = {"turn": 5, "stop_reason": "max_tokens", "completion_state": "continuation_required"}
        a = _inst(td, "v196-i", {"state": "INTAKE", "turn": 5, "reply_completion": dict(cut)})
        r = ops.intake(a, rf, by="executor")   # 命令行 wf intake 走的就是这里
        res["cli_state"], res["cli_trunc"] = a.state["state"], r.get("truncated")
        a.state["state"] = "REVIEW"   # 模拟升级前遗留、或在转入 REVIEW 之后崩溃重启
        a.save()
        try:
            ops.review(a, accept=True)
            res["cli_review"] = "放行了"
        except WfError as e:
            res["cli_review"] = "拦下" if "没有完成" in str(e) else str(e)
        for stname, h in (("REVIEW", driver.h_review), ("APPLY", driver.h_apply), ("EXECUTE", driver.h_execute)):
            b = _inst(td, f"v196-{stname.lower()}", {"state": stname, "turn": 5, "reply_completion": dict(cut),
                                                     "release_path": None})
            b.turn_dir().mkdir(parents=True, exist_ok=True)
            write = ops.parse_reply(REPLY6)
            (b.turn_dir() / "intake.json").write_text(json.dumps(write, ensure_ascii=False), encoding="utf-8")
            out = h(driver.Ctx(b, {}, time.time() + 60))
            evs = [json.loads(x) for x in b.events_path.read_text(encoding="utf-8").splitlines()] if b.events_path.exists() else []
            res[stname] = (out[1][:40], b.state["state"], sum(1 for e in evs if e.get("type") in ("exec", "apply")))
    _ok("1.9.6 没完成的回复进不了评审：命令行 intake 不转入 REVIEW；在 REVIEW、APPLY、EXECUTE 里重启时，"
        "评审、落地、执行都拦下并转为续接，一条命令都不执行",
        res.get("cli_state") == "INTAKE" and res.get("cli_trunc") == "max_tokens" and res.get("cli_review") == "拦下"
        and all(res.get(k) and "REPLY_TRUNCATED" in res[k][0] and res[k][1] == "COMPOSE" and res[k][2] == 0
                for k in ("REVIEW", "APPLY", "EXECUTE")),
        json.dumps(res, ensure_ascii=False)[:700])


def _t_poll_read_error(_ok):
    # 1.9.6：适配器读不了通道（read_error）不等于“没有回复”：不计入未落地、不清 ref、不重发，单独处置
    from . import anomaly, driver
    orig = anomaly.call
    calls = []

    def fake(op, args, timeout=None, env_extra=None):
        calls.append(op)
        if op == "poll":
            return {"code": 0, "raw": "<html>", "data": {"status": "read_error", "error": "text/html 482 字节"}}
        return {"code": 0, "raw": "", "data": {"ref": "NEW"}}
    res = {}
    try:
        anomaly.call = fake
        with tempfile.TemporaryDirectory() as td:
            a = _inst(td, "v196-r1", {"state": "SEND", "turn": 2})
            f = a.outbox / "turn-002.md"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("【回报 · 第 2 轮 · v0.1】档位：high\n\n正文\n", encoding="utf-8")
            a.state["outgoing"] = {"turn": 2, "file": str(f), "kind": "report", "effort": "high"}
            a.state["send_intent"] = {"turn": 2, "status": "pending", "ts": "2026-10-14T08:19:56+00:00", "ref": "R2", "file": str(f)}
            a.save()
            o1 = driver.h_send(driver.Ctx(a, {"send_unknown_wait": True}, time.time() + 60))
            res["send"] = (o1[1][:60], a.state["state"], (a.state.get("send_intent") or {}).get("status"), "send" in calls)
            b = _inst(td, "v196-r2", {"state": "WAIT_REPLY", "turn": 3, "send_ref": "R3", "sent_at": "2026-10-14T08:00:00+00:00"})
            o2 = driver.h_wait_reply(driver.Ctx(b, {}, time.time() + 60))
            res["wait"] = (o2[1][:60], b.state["state"], b.state.get("send_ref"), b.state.get("send_unconfirmed"))
    finally:
        anomaly.call = orig
    _ok("1.9.6 读不了通道：发送待确认与等待回复时，轮询返回 read_error 都按 POLL_READ_ERROR 处置；"
        "发送意图仍待确认、不重发，ref 保留，不计入“没落地”",
        "POLL_READ_ERROR" in res["send"][0] and res["send"][1] == "SEND" and res["send"][2] == "pending" and not res["send"][3]
        and "POLL_READ_ERROR" in res["wait"][0] and res["wait"][1] == "WAIT_REPLY" and res["wait"][2] == "R3"
        and not res["wait"][3],
        json.dumps(res, ensure_ascii=False))


def _t_read_error_window(_ok):
    # 1.9.7（1.9.6 安装前审查 block）：读不了通道的时段不计入“结果不明”窗口与“没落地”的墙钟；
    # 故障恢复后第一次读到 none，不立刻判超时、不清 ref。对照组（没有读取故障）照旧按墙钟判定
    from datetime import timedelta
    from . import anomaly, driver
    from .core import now
    orig = anomaly.call
    iso = lambda dt: dt.isoformat(timespec="seconds")
    anomaly.call = lambda op, args, timeout=None, env_extra=None: {"code": 0, "raw": "", "data": {"status": "none"}}
    res = {}
    try:
        with tempfile.TemporaryDirectory() as td:
            for case, rec in (("comp", True), ("ctrl", False)):
                a = _inst(td, f"v197-s-{case}", {"state": "SEND", "turn": 2})
                f = a.outbox / "turn-002.md"
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("【回报 · 第 2 轮 · v0.1】档位：high\n\n正文\n", encoding="utf-8")
                a.state["outgoing"] = {"turn": 2, "file": str(f), "kind": "report", "effort": "high"}
                a.state["send_intent"] = {"turn": 2, "status": "pending", "ts": iso(now() - timedelta(hours=2)), "ref": "R2",
                                          "file": str(f)}
                if rec:
                    a.state["read_error"] = {"turn": 2, "total_s": 0.0, "since": iso(now() - timedelta(minutes=114))}
                a.save()
                driver.h_send(driver.Ctx(a, {"send_unknown_wait": True, "send_unknown_window_s": 3600}, time.time() + 60))
                res[f"send-{case}"] = (a.state["state"], (a.state.get("send_intent") or {}).get("status"))
                b = _inst(td, f"v197-w-{case}", {"state": "WAIT_REPLY", "turn": 3, "send_ref": "R3",
                                                 "sent_at": iso(now() - timedelta(hours=2))})
                if rec:
                    b.state["read_error"] = {"turn": 3, "total_s": 0.0, "since": iso(now() - timedelta(minutes=117))}
                    b.save()
                driver.h_wait_reply(driver.Ctx(b, {"landed_timeout_s": 600, "landed_min_polls": 1}, time.time() + 60))
                res[f"wait-{case}"] = (b.state["state"], b.state.get("send_ref"), round(driver._read_error_secs(b.state)))
    finally:
        anomaly.call = orig
    _ok("1.9.7 读取故障的时段从墙钟里扣掉：长时间 read_error 恢复后第一次读到 none，发送待确认仍在窗口内重试、"
        "等待回复不判没落地、ref 保留；对照组（同样的墙钟、没有读取故障）照旧超时",
        res["send-comp"] == ("SEND", "pending") and res["send-ctrl"][0] != "SEND"
        and res["wait-comp"][0] == "WAIT_REPLY" and res["wait-comp"][1] == "R3" and res["wait-comp"][2] >= 6900
        and res["wait-ctrl"][1] is None,
        json.dumps(res, ensure_ascii=False))


def _t_completion_boundaries(_ok):
    # 1.9.7（审查 concerns）：完整性判据的边界——适配器没给结束信息、结束信息属于旧轮次、结束状态“未知”时照常进入评审；
    # 结束原因是上限却标成 normal（自相矛盾）时从严，按没有完成处理
    from . import driver, ops
    out = {}
    with tempfile.TemporaryDirectory() as td:
        rf = Path(td) / "reply.md"
        rf.write_text(REPLY6, encoding="utf-8")
        cases = {"none": None, "old_turn": {"turn": 4, "stop_reason": "max_tokens", "completion_state": "continuation_required"},
                 "contradict": {"stop_reason": "max_tokens", "completion_state": "normal"},
                 "unknown": {"stop_reason": None, "completion_state": "unknown", "ended_at": "2026-10-14T00:00:00+00:00"}}
        for name, meta in cases.items():
            a = _inst(td, f"v197-b-{name}", {"state": "INTAKE", "turn": 5})
            a.turn_dir().mkdir(parents=True, exist_ok=True)
            if name == "old_turn":
                a.state["reply_completion"] = dict(meta)
                a.save()
            elif meta is not None:
                driver._reply_meta(a, meta)
                a.save()
            r = ops.intake(a, rf, by="executor")
            out[name] = (a.state["state"], r.get("truncated"), (a.state.get("reply_completion") or {}).get("contradiction"))
    _ok("1.9.7 完整性判据的边界：没有结束信息、结束信息属于旧轮次、结束状态未知，都照常进入评审；"
        "结束原因是上限却标成 normal，从严按没有完成处理、不进入评审",
        out["none"][0] == "REVIEW" and out["old_turn"][0] == "REVIEW" and out["unknown"][0] == "REVIEW"
        and out["contradict"][0] == "INTAKE" and out["contradict"][1] == "max_tokens" and out["contradict"][2] is True,
        json.dumps(out, ensure_ascii=False))


TESTS = [("阶段目标", _t_phase_goals), ("待办取消", _t_tasks_close), ("NO_REF", _t_no_ref),
         ("同步凭证", _t_sync_commit), ("交接短消息", _t_short_pinned), ("回复完整性", _t_reply_limit),
         ("暂存目录安全", _t_stage_symlink), ("暂存目录硬链接", _t_stage_hardlink), ("命令间暂停", _t_pause_between),
         ("人工目标前置", _t_manual_prereq), ("决定与投递分开", _t_owner_split), ("产物挑选", _t_artifacts_pick),
         ("计划过时", _t_plan_stale), ("手工执行防重", _t_exec_guard), ("安装互斥", _t_update_mutex),
         ("回复发现延迟", _t_reply_latency), ("待发消息核对", _t_outgoing_missing), ("驱动器单实例", _t_single_driver),
         ("经验闭环", _t_ledger), ("回复与交付同源", _t_reply_source),
         ("回执核对", _t_receipt), ("交付核对", _t_delivery_check),
         ("共享仲裁", _t_arbiter), ("安装前审查必需", _t_review_gate),
         ("心跳不重发", _t_heartbeat_no_resend),
         ("实景测量", _t_live_check), ("待办检查点", _t_task_progress), ("发送暂时性错误", _t_send_code4),
         ("待办标记", _t_task_markers),
         ("自检不发通知", _t_no_desktop_notify), ("自检环境隔离", _t_selftest_env), ("事件身份恢复", _t_actor_restore),
         ("不明发送的消息标识", _t_send_meta_poll), ("同步历史HEAD", _t_sync_history_head),
         ("目标检查只读", _t_goal_check_readonly), ("自检失败报告", _t_selftest_failure_report),
         ("等执行端的说明", _t_explain_executor_wait), ("补充说明无损", _t_tell_lossless), ("看门狗按项目", _t_watchdog_scope),
         ("完整性前门原子", _t_truncated_atomic), ("读不了通道", _t_poll_read_error),
         ("读取故障时段", _t_read_error_window), ("完整性判据边界", _t_completion_boundaries)]


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
            env = {**os.environ, "WF_LOCAL_DIR": str(Path(td) / "local"), "WF_PROBE_CHILD": "1", "WF_NOTIFY": "0"}
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
