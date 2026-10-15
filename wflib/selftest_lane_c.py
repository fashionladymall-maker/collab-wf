"""B1-C 车道自检：证据分层（mechanical_pass / verified / not_applicable）与 live.check 的结构化状态、机器摘要。

导出 run_lane_c(_ok)，由自检聚合器在它自己的隔离子进程里调用。单独运行（python -m wflib.selftest_lane_c）时，
先用 selftest.child_env 起一个隔离子进程（临时 local/、WF_NOTIFY=0），父进程的环境一项都不改、不读真实 local/。
这里的三轮合成夹具只证明结构与判定逻辑，不是真实 LIVE，不计入生产的 M2-R1。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@t"]


def _git(cwd, *args, env=None):
    return subprocess.run(["git", *GIT_ID, *args], cwd=str(cwd), capture_output=True, text=True, env=env)


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


def _tool_repo(td) -> Path:
    """夹具用的“工具版本库”：干净、已提交。测试里临时让 goals._TOOL_ROOT 指向它，验证真实的摘要与 dirty 计算。"""
    t = Path(td) / "tool"
    (t / "wflib").mkdir(parents=True)
    (t / "VERSION").write_text("0.0.0-fixture\n", encoding="utf-8")
    (t / "wflib" / "x.py").write_text("X = 1\n", encoding="utf-8")
    _git(t, "init", "-q")
    _git(t, "add", "-A")
    _git(t, "commit", "-qm", "fixture")
    return t


class _Live:
    """三轮（5、6、7）全部达标的合成夹具；mut 指定只破坏某一段，用来做逐段负例。"""

    def __init__(self, td, inst):
        from datetime import datetime, timedelta, timezone
        from .core import LOCAL
        self.td, self.a, self.LOCAL, self.td_ = Path(td), inst, LOCAL, timedelta
        self.base = datetime(2026, 10, 13, 6, 0, tzinfo=timezone.utc)
        self.zname = f"collab-wf-9.8.{os.getpid()}"
        a = inst
        gd = {**os.environ, "GIT_AUTHOR_DATE": self.iso(self.base - timedelta(days=1)),
              "GIT_COMMITTER_DATE": self.iso(self.base - timedelta(days=1))}
        _git(a.root, "init", "-q")
        _git(a.root, "add", "PROJECT.md")
        _git(a.root, "commit", "-qm", "init", env=gd)
        self.head = _git(a.root, "rev-parse", "HEAD").stdout.strip()
        self.lp = self.td / "bridge.jsonl"
        self.rp = LOCAL / "reviews" / f"{self.zname}.result.json"
        self.hp = LOCAL / "sync" / f"{a.name}.history.jsonl"

    @staticmethod
    def iso(dt):
        return dt.isoformat(timespec="seconds")

    def write(self, mut=None, extra=()):
        """extra：追加的安装尝试 (轮号, 退出码或 None, 相对基准的分钟)；None 表示结果未知（事件里没有 exit_code）。"""
        a, iso, td_ = self.a, self.iso, self.td_
        zp = a.root / "workflow" / "outbox" / f"{self.zname}.zip"
        zp.parent.mkdir(parents=True, exist_ok=True)
        zp.write_bytes(b"pkg")
        turns, events, alog, hist = [], [], [], []
        tasks = []
        for i, t in enumerate((5, 6, 7)):
            t0 = self.base + td_(minutes=30 * i)
            src = "executor" if (mut == 8 and t == 6) else "driver"
            turns += [{"turn": t, "status": "sent", "from": src, "ts_sent": iso(t0)},
                      {"turn": t, "status": "done", "ts_done": iso(t0 + td_(minutes=10))}]
            deliveries = "mismatch" if (mut == 3 and t == 6) else "ok"
            events += [{"type": "reply_binding", "turn": t, "receipt": "ok", "deliveries": deliveries, "by": "driver", "actor": "driver"},
                       {"type": "reply_checked", "turn": t, "by": "driver", "actor": "driver"},
                       {"type": "round_result", "turn": t, "ok": True, "by": "driver", "actor": "driver"}]
            alog.append({"action": "send", "turn": t, "ts": iso(t0), "project": a.name})
            head = ("f00d" * 10) if (mut == 10 and t == 6) else self.head
            hist.append({"turn": t, "status": "pushed", "commit": "abc1234def", "head": head, "ts": iso(t0 + td_(minutes=12))})
            d = a.evidence / f"turn-{t:03d}"
            d.mkdir(parents=True, exist_ok=True)
            cm = [f"wf self-update --auto workflow/outbox/{self.zname}.zip"] if t == 7 else []
            (d / "intake.json").write_text(json.dumps({"commands": cm}), encoding="utf-8")
            parent = "X" if (mut == 2 and t == 6) else f"H{t}"
            ended = t0 + td_(minutes=1 if (mut == 9 and t == 6) else 9)
            (d / "reply_meta.json").write_text(json.dumps({"ended_at": iso(ended), "parent": parent}), encoding="utf-8")
            (d / "send_meta.json").write_text(json.dumps({"message_uuid": f"H{t}"}), encoding="utf-8")
        if mut == 1:
            alog.append({"action": "send", "turn": 6, "ts": iso(self.base), "project": a.name})
        if mut == 5:
            events.append({"type": "exec", "turn": 6, "cmd": "ls", "exit_code": 0, "by": "executor", "actor": "cli"})
        if mut == 6:
            events.append({"type": "auto", "turn": 6, "rule": "TEST_STOP", "action": "await_user", "by": "driver", "actor": "driver"})
        if mut == 7:
            events.append({"type": "tasks_opened", "turn": 6, "ids": ["T6-1"], "by": "driver", "actor": "driver"})
        events.append({"type": "apply", "turn": 6, "by": "driver", "actor": "driver"})
        code = 1 if mut == "attempt_failed" else 0
        events.append({"type": "exec", "turn": 7, "cmd": f"wf self-update --auto workflow/outbox/{self.zname}.zip",
                       "exit_code": code, "by": "driver", "actor": "driver",
                       "ts": iso(self.base + td_(minutes=75)), "duration_s": 600})
        for t, code_x, minute in extra:
            e = {"type": "exec", "turn": t, "cmd": f"wf self-update --auto workflow/outbox/{self.zname}.zip",
                 "by": "driver", "actor": "driver", "ts": iso(self.base + td_(minutes=minute)), "duration_s": 1}
            if code_x is not None:
                e["exit_code"] = code_x
            events.append(e)
        verdict = "block" if mut == 4 else "pass"
        (self.LOCAL / "reviews").mkdir(parents=True, exist_ok=True)
        self.rp.write_text(json.dumps({"verdict": verdict, "ts": iso(self.base + td_(minutes=70)),
                                       "zip_sha256": hashlib.sha256(b"pkg").hexdigest()}), encoding="utf-8")
        (self.LOCAL / "sync").mkdir(parents=True, exist_ok=True)
        self.hp.write_text("".join(json.dumps(h) + "\n" for h in hist), encoding="utf-8")
        a.turns_path.write_text("".join(json.dumps(r) + "\n" for r in turns), encoding="utf-8")
        a.events_path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        self.lp.write_text("".join(json.dumps(x) + "\n" for x in alog), encoding="utf-8")
        a.state["executor_tasks"] = tasks
        a.save()

    def cleanup(self):
        for p in (self.rp, self.hp):
            try:
                p.unlink()
            except OSError:
                pass


def _with_transport(lp):
    from . import anomaly
    orig = anomaly.transport_cfg
    anomaly.transport_cfg = lambda: {"log": str(lp)}
    return lambda: setattr(anomaly, "transport_cfg", orig)


def _strip(prog: dict) -> dict:
    new_top, new_item = ("counts",), ("layer", "layer_why", "binding", "na")
    p = {k: v for k, v in prog.items() if k not in new_top}
    p["items"] = [{k: v for k, v in i.items() if k not in new_item} for i in prog.get("items") or []]
    return p


def _t_compat(_ok):
    """旧记录与旧 API：goals_state 里没有 layer/binding 的旧记录，原有键的值与含义不变；只能推出机械状态。"""
    from . import goals, policy
    with tempfile.TemporaryDirectory() as td:
        inst = _inst(td, "lc-compat", {"state": "IDLE", "turn": 2})
        g = {"goals": [
            {"id": "E1", "title": "机器", "kind": "exec", "check": "true", "pass": {"exit": 0}, "means": "m", "on_fail": "f"},
            {"id": "E2", "title": "机器2", "kind": "exec", "check": "true", "pass": {"exit": 0}, "means": "m", "on_fail": "f"},
            {"id": "E3", "title": "机器3", "kind": "exec", "check": "true", "pass": {"exit": 0}, "means": "m", "on_fail": "f"},
            {"id": "U1", "title": "人工", "kind": "manual", "means": "m", "on_fail": "f"}]}
        (inst.wf / "goals.json").write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
        policy.approve(inst, "goals", "测试", by="test")
        cur = policy.status(inst, "goals")["current"]
        old = {"E1": {"ok": True, "why": [], "ts": "2026-10-14T00:00:00+00:00", "exit": 0, "evidence": "x", "goals_sha": cur},
               "E2": {"ok": False, "why": ["退出码 1，期望 0"], "ts": "2026-10-14T00:00:00+00:00", "exit": 1, "goals_sha": cur},
               "E3": {"ok": True, "why": [], "exit": 0, "goals_sha": "0" * 64,
                      "verified": {"granted": True, "binding": {"goals_sha": "0" * 64}}}}
        (inst.wf / "goals_state.json").write_text(json.dumps(old), encoding="utf-8")
        goals.confirm(inst, "U1", "可以")
        p = goals.progress(inst)
        expect_items = [
            {"id": "E1", "title": "机器", "kind": "exec", "ok": True, "detail": "通过", "means": "m", "on_fail": "f"},
            {"id": "E2", "title": "机器2", "kind": "exec", "ok": False, "detail": "退出码 1，期望 0", "means": "m", "on_fail": "f"},
            {"id": "E3", "title": "机器3", "kind": "exec", "ok": False, "detail": "尚未检查", "means": "m", "on_fail": "f"},
            {"id": "U1", "title": "人工", "kind": "manual", "ok": True, "detail": "用户已确认：可以", "means": "m", "on_fail": "f"}]
        sp = _strip(p)
        layers = [i["layer"] for i in p["items"]]
        empty = goals.progress(_inst(td, "lc-empty"))
    _ok("B1-C 兼容：旧记录下 ok、detail、done、total、reached 与原含义一致；新增键只增不改；旧记录推不出 verified",
        sp["items"] == expect_items and sp["done"] == 2 and sp["total"] == 4 and sp["reached"] is False
        and set(sp) >= {"defined", "approved", "changed_since_approval", "items", "done", "total", "reached", "issues", "phase", "history"}
        and layers == ["mechanical_pass", "failed", "pending", "confirmed"]
        and p["counts"]["mechanical_pass"] == 1 and p["counts"]["verified"] == 0
        and empty == {"defined": False, "approved": False, "items": [], "done": 0, "total": 0, "reached": False},
        json.dumps([sp, layers, p.get("counts"), empty], ensure_ascii=False)[:900])


def _t_na(_ok):
    """NA：四项缺任一拒绝；exit 1 与超时 124 拒绝；手工写入的 NA 遇到失败记录也不生效；目标改动后失效；NA 不计入 ok/reached。"""
    from . import goals, policy
    from .core import WfError
    res = {}
    with tempfile.TemporaryDirectory() as td:
        inst = _inst(td, "lc-na", {"state": "IDLE", "turn": 2})
        g = {"goals": [
            {"id": "F1", "title": "失败", "kind": "exec", "check": "false", "pass": {"exit": 0}, "means": "m", "on_fail": "f"},
            {"id": "S1", "title": "超时", "kind": "exec", "check": "sleep 5", "pass": {"exit": 0}, "means": "m", "on_fail": "f"},
            {"id": "N1", "title": "未检查", "kind": "exec", "check": "true", "pass": {"exit": 0}, "means": "m", "on_fail": "f"},
            {"id": "U1", "title": "人工", "kind": "manual", "means": "m", "on_fail": "f"}]}
        gp = inst.wf / "goals.json"
        gp.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
        policy.approve(inst, "goals", "测试", by="test")
        full = dict(condition="本阶段不涉及", scope="仅 N1", reason="范围外", by="tester")
        miss = []
        for k in ("condition", "scope", "reason", "by"):
            try:
                goals.mark_na(inst, "U1", **dict(full, **{k: "  "}))
                miss.append("accepted")
            except WfError:
                miss.append("refused")
        res["miss"] = miss
        # 只跑 F1 与 S1：把 N1 暂时去掉，避免它产生记录（N1 用来验证“没有记录时可以 NA”）
        sub = dict(g, goals=[x for x in g["goals"] if x["id"] in ("F1", "S1")])
        gp.write_text(json.dumps(sub, ensure_ascii=False), encoding="utf-8")
        policy.approve(inst, "goals", "测试", by="test")
        goals.run_checks(inst, timeout=1, by="test")
        gs = json.loads((inst.wf / "goals_state.json").read_text(encoding="utf-8"))
        res["exits"] = [gs["F1"]["exit"], gs["S1"]["exit"]]
        gp.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
        policy.approve(inst, "goals", "测试", by="test")
        for gid in ("F1", "S1"):
            try:
                goals.mark_na(inst, gid, **full)
                res[gid] = "accepted"
            except WfError:
                res[gid] = "refused"
        # F1、S1 的失败记录写于只含 F1、S1 的目标文件；之后只新增了无关目标 N1、U1。B2-C2b 起这两条是逐目标 v2 记录，
        # 无关目标的变化不使它们失效：F1（只读程序 false，绑定整个项目）layer 为 failed；S1 的 sleep 无法推导依赖，
        # 绑定不成立（失败关闭）而为 pending。B1 时两者都按全局 goals_sha 为 pending。
        # 绕过 mark_na 直接写入当前 goals_sha 的 NA：失败记录在，NA 也不生效
        a = policy.approvals(inst)
        cur = policy.status(inst, "goals")["current"]
        a.setdefault("not_applicable", {})["F1"] = dict(full, ts="x", goals_sha=cur)
        policy.approvals_path(inst).write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
        goals.mark_na(inst, "N1", **full)
        goals.mark_na(inst, "U1", **full)
        p1 = goals.progress(inst)
        lay1 = {i["id"]: (i["layer"], i["ok"]) for i in p1["items"]}
        f1why = "；".join(next(i for i in p1["items"] if i["id"] == "F1")["layer_why"])
        g2 = json.loads(gp.read_text(encoding="utf-8"))
        g2["goals"][2]["title"] = "未检查（改）"
        gp.write_text(json.dumps(g2, ensure_ascii=False), encoding="utf-8")
        policy.approve(inst, "goals", "测试", by="test")
        p2 = goals.progress(inst)
        lay2 = {i["id"]: i["layer"] for i in p2["items"]}
        n1 = next(i for i in p1["items"] if i["id"] == "N1")
    _ok("B1-C 不适用：条件、排除范围、理由、判定人缺任一拒绝；exit 1 与超时 124 拒绝；失败记录在时直接写入的 NA 不生效；"
        "NA 不计入 ok 与 reached；目标改动后 NA 失效",
        res["miss"] == ["refused"] * 4 and res["exits"] == [1, 124] and res["F1"] == "refused" and res["S1"] == "refused"
        and lay1["F1"] == ("failed", False) and lay1["S1"] == ("pending", False) and "不能算不适用" in f1why
        and lay1["N1"] == ("not_applicable", False) and lay1["U1"] == ("not_applicable", False)
        and set((n1.get("na") or {})) >= {"condition", "scope", "reason", "by"}
        and p1["done"] == 0 and p1["reached"] is False and p1["counts"]["not_applicable"] == 2
        and lay2["N1"] == "pending" and lay2["U1"] == "pending",
        json.dumps([res, lay1, f1why, lay2, p1.get("counts")], ensure_ascii=False)[:900])


def _t_live_struct(_ok):
    """live.check：三轮合成夹具通过且有机器摘要；第 1–10 段逐段负例只破坏该段；第 4 段不适用/尝试失败如实区分；可 JSON 序列化。"""
    from . import live
    out = {}
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "lc-live", {"state": "IDLE", "turn": 8})
        fx = _Live(td, a)
        restore = _with_transport(fx.lp)
        try:
            fx.write()
            r = live.check(a, 3)
            out["ok"] = (r["passed"], r["streak"], r["summary"]["passed_strict"], r["summary"]["window"],
                         r["summary"]["segments"], r["summary"]["project"] == a.name)
            out["json"] = json.loads(json.dumps(r, ensure_ascii=False))["summary"] == r["summary"]
            c4 = {rd["turn"]: next(c for c in rd["criteria"] if c["n"] == 4) for rd in r["rounds"]}
            out["c4_na"] = (c4[5]["ok"], c4[5]["status"], sorted((c4[5].get("na") or {})))
            out["c4_inst"] = (c4[7]["ok"], c4[7]["status"])
            seg = {}
            for n in range(1, 11):
                fx.write(mut=n)
                rr = live.check(a, 3)
                tgt = 7 if n == 4 else 6
                rd = next(x for x in rr["rounds"] if x["turn"] == tgt)
                bad = sorted(c["n"] for c in rd["criteria"] if not c["ok"])
                seg[n] = (bad == [n], rr["passed"], rr["summary"]["passed_strict"])
            out["seg"] = seg
            fx.write(mut="attempt_failed")
            rr = live.check(a, 3)
            c = next(x for x in next(y for y in rr["rounds"] if y["turn"] == 7)["criteria"] if x["n"] == 4)
            out["attempt"] = (c["ok"], c["status"], "na" in c, rr["summary"]["segments"]["4"], rr["summary"]["passed_strict"])
            # r2（F1）：同轮成功安装不能遮掉失败、超时或结果未知的尝试；跨轮的失败也要让严格结果不通过（F2 的测量侧）
            mix = {"成功后超时": [(7, 124, 80)], "失败后成功": [(7, 1, 72)], "成功后未知": [(7, None, 80)],
                   "第6轮失败第7轮成功": [(6, 1, 40)]}
            out["mix"] = {}
            for k, ex in mix.items():
                fx.write(extra=ex)
                rr = live.check(a, 3)
                t = ex[0][0]
                c = next(x for x in next(y for y in rr["rounds"] if y["turn"] == t)["criteria"] if x["n"] == 4)
                out["mix"][k] = (rr["passed"], c["ok"], c["status"], rr["summary"]["segments"]["4"],
                                 rr["summary"]["passed_strict"], bool(rr["summary"]["strict_issues"]),
                                 (c.get("attempts") or {}).get("total", 0) - (c.get("attempts") or {}).get("ok", 0))
        finally:
            restore()
            fx.cleanup()
    good_seg = all(v == (True, False, False) for v in out.get("seg", {}).values()) and len(out.get("seg", {})) == 10
    _ok("B1-C 实景测量结构：三轮夹具通过并给出机器摘要（窗口、逐段、项目）；第 1–10 段逐段负例都只让该段不达标且整体不通过；"
        "没有安装时第 4 段为 not_applicable 并带四项；安装失败为 attempt_failed、ok 不变、严格摘要不通过；"
        "同轮成功安装遮不掉失败、超时（124）或结果未知的尝试，跨轮失败同样让严格摘要不通过（r2 F1）；摘要可经 --json 序列化",
        out.get("ok", (0,))[0] is True and out["ok"][1] == 3 and out["ok"][2] is True and out["ok"][3] == [5, 6, 7]
        and all(out["ok"][4].values()) and out["ok"][5] and out["json"]
        and out["c4_na"] == (True, "not_applicable", ["by", "condition", "reason", "scope"])
        and out["c4_inst"] == (True, "passed") and good_seg
        and out["attempt"][0] is True and out["attempt"][1] == "attempt_failed" and out["attempt"][2] is False
        and out["attempt"][3] is False and out["attempt"][4] is False
        and len(out.get("mix", {})) == 4
        and all(v == (True, True, "attempt_failed", False, False, True, 1) for v in out["mix"].values()),
        json.dumps(out, ensure_ascii=False, default=str)[:1500])


def _t_verified(_ok):
    """verified：只由 run_checks 直接调用 live.check 授予；任意 exit 0、evidence 字符串、未声明映射都不行；
    工具 dirty、工具摘要、项目指纹、目标变化与证据篡改都使绑定失效；工具没有版本库时不授予。"""
    from . import goals, policy
    out = {}
    old_root = goals._TOOL_ROOT
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "lc-ver", {"state": "IDLE", "turn": 8})
        fx = _Live(td, a)
        tool = _tool_repo(td)
        restore = _with_transport(fx.lp)
        gp = a.wf / "goals.json"

        def setg(gl):
            gp.write_text(json.dumps({"goals": gl}, ensure_ascii=False), encoding="utf-8")
            policy.approve(a, "goals", "测试", by="test")

        def lay(gid="R1"):
            return next(i for i in goals.progress(a)["items"] if i["id"] == gid)
        live_goal = {"id": "R1", "title": "实景", "kind": "exec", "check": "true", "pass": {"exit": 0}, "means": "m",
                     "on_fail": "f", "live": {"rounds": 3, "segments": list(range(1, 11))}}
        plain = {"id": "P1", "title": "任意成功命令", "kind": "exec", "check": "true", "pass": {"exit": 0},
                 "means": "m", "on_fail": "f", "evidence": "live"}
        try:
            goals._TOOL_ROOT = tool
            fx.write()
            setg([plain])
            goals.run_checks(a, by="test")
            out["plain"] = (lay("P1")["layer"], lay("P1")["ok"])
            setg([live_goal, plain])
            goals.run_checks(a, by="test")
            p = goals.progress(a)
            r1 = next(i for i in p["items"] if i["id"] == "R1")
            out["pos"] = (r1["layer"], r1["ok"], p["counts"]["verified"], p["reached"],
                          sorted(k for k in (r1.get("binding") or {}) if k in (
                              "project", "segments", "tool_digest", "tool_dirty", "project_fp", "goals_sha", "evidence_sha256")))
            ev = a.root / r1["binding"]["evidence"]
            keep = ev.read_bytes()
            ev.write_bytes(keep.replace(b'"passed": true', b'"passed": false'))
            out["tamper"] = lay()["layer"]
            ev.write_bytes(keep)
            out["restored"] = lay()["layer"]
            (tool / "wflib" / "x.py").write_text("X = 2\n", encoding="utf-8")
            out["tool_dirty"] = lay()["layer"]
            _git(tool, "commit", "-qam", "change")
            out["tool_changed"] = lay()["layer"]
            goals.run_checks(a, by="test")
            out["rebound"] = lay()["layer"]
            (a.root / "PROJECT.md").write_text("# PROJECT.md\n改动\n", encoding="utf-8")
            out["project_changed"] = lay()["layer"]
            goals.run_checks(a, by="test")
            out["rebound2"] = lay()["layer"]
            setg([dict(live_goal, title="实景（改）"), plain])
            out["goal_changed"] = lay()["layer"]
            goals.run_checks(a, by="test")
            fx.write(mut=6)
            goals.run_checks(a, by="test")
            x = lay()
            out["live_fail"] = (x["layer"], x["ok"])
            fx.write(mut="attempt_failed")
            goals.run_checks(a, by="test")
            out["attempt_failed"] = lay()["layer"]
            fx.write()
            (tool / "untracked.txt").write_text("u", encoding="utf-8")
            goals.run_checks(a, by="test")
            out["grant_dirty"] = lay()["layer"]
            (tool / "untracked.txt").unlink()
            goals._TOOL_ROOT = Path(td) / "no-git-tool"
            (Path(td) / "no-git-tool").mkdir()
            goals.run_checks(a, by="test")
            out["no_git"] = lay()["layer"]
            goals._TOOL_ROOT = tool
            # r2（F2）：旧 passed 为真而严格结果不通过时，只映射第 1 段的目标也不能授予
            narrow = dict(live_goal, id="R2", live={"rounds": 3, "segments": [1]})
            setg([live_goal, narrow, plain])
            fx.write()
            goals.run_checks(a, by="test")
            out["narrow_ok"] = lay("R2")["layer"]
            for k, ex in (("f2_cross", [(6, 1, 40)]), ("f1_same", [(7, 124, 80)])):
                fx.write(extra=ex)
                goals.run_checks(a, by="test")
                rec = json.loads((a.wf / "goals_state.json").read_text(encoding="utf-8"))
                out[k] = (lay("R1")["layer"], lay("R2")["layer"], lay("R2")["ok"],
                          rec["R1"]["verified"]["granted"], rec["R2"]["verified"]["granted"])   # 授予路径本身就拒绝
            # r2（F3）：被 .git/info/exclude 忽略、但实际在安装树里的新源码：git dirty 为空，摘要也必须变
            fx.write()
            goals.run_checks(a, by="test")
            before = (lay()["layer"], goals._tool_state()["digest"])
            with open(tool / ".git" / "info" / "exclude", "a", encoding="utf-8") as fh:
                fh.write("wflib/review_ignored_helper.py\n")
            (tool / "wflib" / "review_ignored_helper.py").write_text("H = 1\n", encoding="utf-8")
            ts = goals._tool_state()
            out["ignored"] = (before[0], ts["dirty"], ts["digest"] != before[1], lay()["layer"])
            (tool / "wflib" / "review_ignored_helper.py").unlink()
            # 选择规则：local/、__pycache__/、.pyc、MANIFEST.json 不计入摘要（与 release._tool_files 相同）
            for rel in ("local/x.json", "wflib/__pycache__/x.cpython-312.pyc", "wflib/y.pyc", "MANIFEST.json"):
                (tool / rel).parent.mkdir(parents=True, exist_ok=True)
                (tool / rel).write_text("z", encoding="utf-8")
            out["skip_rule"] = goals._tool_state()["digest"] == before[1]
            for rel in ("local/x.json", "wflib/__pycache__/x.cpython-312.pyc", "wflib/y.pyc", "MANIFEST.json"):
                (tool / rel).unlink()
            out["restored_after_ignored"] = lay()["layer"]
            # 已存证据的结构核对：机械证据被改动、或伪造的非规范证据配上重算的哈希，都不授予
            items = {i["id"]: i for i in goals.progress(a)["items"]}
            gsp = a.wf / "goals_state.json"
            gs_keep = gsp.read_bytes()
            mev = a.root / json.loads(gs_keep)["R1"]["evidence"]
            mkeep = mev.read_bytes()
            mev.write_text("$ true\nexit=0\n伪造\n", encoding="utf-8")
            out["mech_tamper"] = lay()["layer"]
            mev.write_bytes(mkeep)
            gsd = json.loads(gs_keep)
            fake = a.root / items["R1"]["binding"]["evidence"]
            fake_keep = fake.read_bytes()
            fake.write_text("hello", encoding="utf-8")
            gsd["R1"]["verified"]["binding"]["evidence_sha256"] = hashlib.sha256(b"hello").hexdigest()
            gsp.write_text(json.dumps(gsd), encoding="utf-8")
            out["forged_text"] = lay()["layer"]
            fake.write_bytes(fake_keep)
            gsp.write_bytes(gs_keep)
            out["restored_final"] = lay()["layer"]
            out["bad_spec"] = [bool(goals.validate({"goals": [dict(live_goal, live=v)]}))
                               for v in ({"rounds": 3, "segments": [0]}, {"rounds": 3, "segments": []}, {"segments": [1, 1]},
                                         {"rounds": "3", "segments": [1]}, "live", {"rounds": 3, "segments": [[1]]})]
            out["good_spec"] = [i for i in goals.validate({"goals": [live_goal]}) if "live" in i]
        finally:
            goals._TOOL_ROOT = old_root
            restore()
            fx.cleanup()
    _ok("B1-C verified：只由直接调用 live.check 的实测授予并绑定项目、实景段、工具摘要与 dirty、项目指纹、goals_sha、证据 sha256；"
        "任意 exit 0 与 evidence 字符串只到 mechanical_pass；证据篡改、工具 dirty 或改动、项目指纹、目标变化都使其失效；"
        "实景不达标、安装尝试失败、工具 dirty 或没有版本库时不授予；ok 与 reached 不变；"
        "r2：严格结果不通过时窄映射也不授予（F2）、同轮失败尝试不被成功遮掉（F1）、被 git 忽略的安装源码改变摘要（F3）、"
        "机械证据改动与非规范的伪造证据不授予、live 嵌套段配置返回校验问题",
        out.get("plain") == ("mechanical_pass", True)
        and out.get("pos", (None,))[0] == "verified" and out["pos"][1] is True and out["pos"][2] == 1 and out["pos"][3] is True
        and out["pos"][4] == ["evidence_sha256", "goals_sha", "project", "project_fp", "segments", "tool_digest", "tool_dirty"]
        and out["tamper"] == "mechanical_pass" and out["restored"] == "verified"
        and out["tool_dirty"] == "mechanical_pass" and out["tool_changed"] == "mechanical_pass" and out["rebound"] == "verified"
        and out["project_changed"] == "mechanical_pass" and out["rebound2"] == "verified"
        and out["goal_changed"] == "pending" and out["live_fail"] == ("mechanical_pass", True)
        and out["attempt_failed"] == "mechanical_pass" and out["grant_dirty"] == "mechanical_pass" and out["no_git"] == "mechanical_pass"
        and out["bad_spec"] == [True] * 6 and out["good_spec"] == []
        and out["narrow_ok"] == "verified" and out["f2_cross"] == ("mechanical_pass", "mechanical_pass", True, False, False)
        and out["f1_same"] == ("mechanical_pass", "mechanical_pass", True, False, False)
        and out["ignored"] == ("verified", [], True, "mechanical_pass") and out["skip_rule"] is True
        and out["restored_after_ignored"] == "verified" and out["mech_tamper"] == "mechanical_pass"
        and out["forged_text"] == "mechanical_pass" and out["restored_final"] == "verified",
        json.dumps(out, ensure_ascii=False, default=str)[:1500])


def _t_manifest_rule(_ok):
    """r2（F3）：goals 的工具摘要与 release._tool_files（MANIFEST 规则）在 WF_HOME 上选中完全相同的文件与哈希；只读。"""
    from . import goals, release
    from .core import WF_HOME
    a, b = goals._manifest_files(WF_HOME), release._tool_files()
    _ok("B1-C r2 工具摘要的文件选择与 MANIFEST 规则（release._tool_files）一致",
        bool(a) and a == b and "MANIFEST.json" not in a,
        json.dumps({"only_goals": sorted(set(a) - set(b))[:10], "only_release": sorted(set(b) - set(a))[:10],
                    "diff_hash": sorted(k for k in set(a) & set(b) if a[k] != b[k])[:10]}, ensure_ascii=False))


def _t_default_goals(_ok):
    """B2-C1：默认目标骨架 default_goals 的纯函数性质与边界（只在临时目录里读写，不起子进程、不执行探针）。"""
    import builtins
    import io
    import time as _time
    from . import goals, policy
    from .core import WfError
    f = goals.default_goals
    g1, g2 = f("demo", today="2026-10-15"), f("demo", today="2026-10-15")
    g1["goals"][0]["pass"]["exit"] = 9
    g1["goals"].append({"id": "X"})
    g1["phase"]["title"] = "改"
    g3 = f("demo", today="2026-10-15")
    _ok("B2-C1 骨架确定且互不共享：同样输入内容相同；改动一份返回值不影响另一份或之后的调用",
        g2 == g3 and json.dumps(g2, sort_keys=True, ensure_ascii=False) == json.dumps(g3, sort_keys=True, ensure_ascii=False)
        and g2 is not g3 and g2["goals"] is not g3["goals"] and g2["goals"][0]["pass"] is not g3["goals"][0]["pass"]
        and g2["goals"][0]["pass"]["exit"] == 0 and len(g2["goals"]) == 4 and g2["phase"]["title"] == "工程基线",
        json.dumps([g2, g3], ensure_ascii=False)[:600])

    # 不读时钟、不读写磁盘：调用期间让时钟与文件接口一律抛错
    calls, saved = [], {}

    def boom(name):
        def _f(*a, **k):
            calls.append(name)
            raise RuntimeError(f"default_goals 不应调用 {name}")
        return _f
    targets = [(builtins, "open"), (io, "open"), (_time, "time"), (_time, "localtime"), (_time, "strftime"),
               (goals, "now_iso"), (goals, "read_json"), (goals, "write_json"),
               (Path, "read_text"), (Path, "read_bytes"), (Path, "write_text"), (Path, "exists"), (os, "stat")]
    try:
        for mod, name in targets:
            saved[(mod, name)] = getattr(mod, name)
            setattr(mod, name, boom(f"{getattr(mod, '__name__', mod)}.{name}"))
        n1, n2 = f("demo"), f("demo", today=None)
        err = ""
    except Exception as e:   # noqa: BLE001
        n1 = n2 = None
        err = f"{type(e).__name__}: {e}"
    finally:
        for (mod, name), v in saved.items():
            setattr(mod, name, v)
    _ok("B2-C1 骨架不读时钟、不读写磁盘；today 为 None 时 since 为空字符串（不取当前日期）",
        not calls and not err and n1 == n2 and n1["phase"]["since"] == "" and g2["phase"]["since"] == "2026-10-15",
        json.dumps([calls, err], ensure_ascii=False)[:600])

    # 校验通过；判据固定且只读；不含正式 M2、实景映射、不适用、权限类键
    names = ["demo", "", "x; rm -rf /", "$(touch pwn)", "`id`", "a\nb\rc", "../../etc", '"\'', "项目 中文", "--probes", "&&"]
    bad = []
    for nm in names:
        g = f(nm, today="2026-10-15")
        dump = json.dumps(g, ensure_ascii=False)
        ex = [x for x in g["goals"] if x["kind"] == "exec"]
        man = [x for x in g["goals"] if x["kind"] == "manual"]
        probs = []
        if goals.validate(g):
            probs.append(("validate", goals.validate(g)))
        if [x["check"] for x in ex] != ["wf self-check", "wf self-check --probes"]:
            probs.append(("checks", [x["check"] for x in ex]))
        if not all(policy.readonly_check(x["check"]) and x["pass"].get("exit") == 0 for x in ex):
            probs.append("readonly/exit0")
        if [x["id"] for x in man] != ["BASE-SMOKE", "BASE-ACCEPT"] or man[1].get("depends_on") != "all_exec":
            probs.append("manual")
        if g["phase"]["scope"] != nm or g["phase"]["id"] != "P0" or g.get("_skeleton") != goals.SKELETON_VERSION:
            probs.append("phase")
        if any("live" in x or "verified" in x or "not_applicable" in x or "na" in x for x in g["goals"]):
            probs.append("live/na")
        if any(x["id"].startswith("M2") for x in g["goals"]) or '"M2' in dump.replace(json.dumps(nm, ensure_ascii=False), ""):
            probs.append("M2")
        if set(g) - {"_说明", "_skeleton", "phase", "goals"} or "history" in g:
            probs.append(("keys", sorted(g)))
        if nm and any(nm in x.get("check", "") for x in ex) and nm not in ("wf self-check", "--probes"):
            probs.append("injected")
        if probs:
            bad.append((nm, probs))
    _ok("B2-C1 骨架校验通过；两条可执行判据固定为只读的 wf self-check 与 wf self-check --probes 且要求 exit 0；"
        "冒烟与验收为人工目标；不含正式 M2、live 映射、不适用记录或权限类键；恶意项目名只进 phase.scope、不进命令",
        not bad and goals.SKELETON_VERSION == "default-goals/1", json.dumps(bad, ensure_ascii=False)[:800])

    rej = []
    for args, kw in (((None,), {}), ((123,), {}), ((b"x",), {}), (("demo",), {"today": 20261015}), (("demo",), {"today": b"x"})):
        try:
            f(*args, **kw)
            rej.append("accepted")
        except WfError:
            rej.append("refused")
    pos_only = False
    try:
        f("demo", "2026-10-15")
    except TypeError:
        pos_only = True
    _ok("B2-C1 骨架参数类型：project 非字符串、today 非字符串且非 None 都拒绝；today 只能按关键字传入",
        rej == ["refused"] * 5 and pos_only, json.dumps(rej))

    # 写进临时项目后：不会自行批准、不产生 verified 或 NA、不新建批准记录；进度只是待定
    with tempfile.TemporaryDirectory() as td:
        inst = _inst(td, "lc-c1", {"state": "IDLE", "turn": 1})
        before = sorted(str(p.relative_to(inst.root)) for p in inst.root.rglob("*"))
        sk = f(inst.name, today="2026-10-15")
        after_call = sorted(str(p.relative_to(inst.root)) for p in inst.root.rglob("*"))
        (inst.wf / "goals.json").write_text(json.dumps(sk, ensure_ascii=False, indent=1), encoding="utf-8")
        p = goals.progress(inst)
        st = policy.status(inst, "goals")
        no_appr = not policy.approvals_path(inst).exists()
    _ok("B2-C1 骨架不自授权限：调用不写任何文件；写入后目标未批准、reached 为假、没有 verified 或不适用、没有批准记录",
        before == after_call and st["approved"] is False and p["approved"] is False and p["reached"] is False
        and p["done"] == 0 and p["total"] == 4 and p["counts"]["verified"] == 0 and p["counts"]["not_applicable"] == 0
        and all(i["layer"] == "pending" for i in p["items"]) and no_appr and p["issues"] == [],
        json.dumps([before == after_call, st, p.get("counts"), [i["layer"] for i in p["items"]], no_appr], ensure_ascii=False)[:800])


def _mev(seq, turn, type_, actor="driver", **kw):
    e = {"seq": seq, "ts": f"2026-10-15T00:{seq // 60:02d}:{seq % 60:02d}+00:00", "type": type_, "turn": turn,
         "state": "X", "actor": actor, "pid": 1}
    e.update(kw)
    return e


# 合成夹具（不是实景）：三轮按协议关闭（转入 COMPOSE 开始、round_result、下一轮转入 COMPOSE 为边界），当前第 4 轮；
# 第 2 轮有一次命令行里的人工执行。spec 项为 (轮号, 类型, 字段)，seq 按顺序从 1 编号。
_M_SPEC = [(1, "transition", {"to": "COMPOSE"}), (1, "round_result", {"ok": True}),
           (2, "transition", {"to": "COMPOSE"}), (2, "exec", {"actor": "cli", "by": "executor", "cmd": "ls"}),
           (2, "round_result", {"ok": True}),
           (3, "transition", {"to": "COMPOSE"}), (3, "round_result", {"ok": True}),
           (4, "transition", {"to": "COMPOSE"})]


def _m_rows(spec=None):
    return [_mev(i, t, ty, **kw) for i, (t, ty, kw) in enumerate(spec or _M_SPEC, 1)]


def _m_turns(closed=(1, 2, 3), current=4, rows=None):
    """fix2：轮次记录的时间按真实写入顺序取自事件流——发送时刻等于本轮转入 COMPOSE 的时刻，回复落地时刻等于本轮
    round_result（没有时取本轮最后一条事件）的时刻。"""
    ev = rows or _m_rows()
    first = lambda t: next(e["ts"] for e in ev if e["turn"] == t)   # noqa: E731
    out = []
    for t in closed:
        mine = [e for e in ev if e["turn"] == t]
        rr = [e for e in mine if e["type"] == "round_result"]
        out += [{"turn": t, "status": "sent", "from": "driver", "ts_sent": first(t)},
                {"turn": t, "status": "done", "ts_done": (rr or mine)[-1]["ts"]}]
    if current:
        out.append({"turn": current, "status": "sent", "from": "driver", "ts_sent": first(current)})
    return out


def _metrics_fixture(inst, rows, state=None, turns=None):
    """合成夹具（不是实景）：直接写事件流、轮次记录与 state.json。rows/turns 里的字符串原样写成一行（用来造坏行）。"""
    inst.events_path.write_text("".join((r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)) + "\n"
                                        for r in rows), encoding="utf-8")
    if turns is not None:
        if inst.turns_path.is_dir():
            inst.turns_path.rmdir()
        inst.turns_path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in turns),
                                   encoding="utf-8")
    if state is not None:
        inst.state_path.write_text(state if isinstance(state, str) else json.dumps(state), encoding="utf-8")


def _m_state(seq=8, turn=4):
    return {"state": "IDLE", "turn": turn, "seq": seq}


def _m_sum(r) -> tuple:
    m = r["metrics"]["manual_rate"]
    return (m["value"], m["n"], m["coverage"], m["unknown"], r["window"]["rounds"], r["window"]["denominator_certain"])


def _t_metrics(_ok):
    """B2-C3：metrics.compute 的结构、可证指标与拒绝边界（合成夹具，不是实景）。"""
    from . import metrics
    from .core import WfError
    res = {}
    with tempfile.TemporaryDirectory() as td:
        lp = Path(td) / "bridge.jsonl"
        lp.write_text("", encoding="utf-8")
        restore = _with_transport(lp)
        try:
            a = _inst(td, "lc-m-empty", {"state": "IDLE", "turn": 0})
            r = metrics.compute(a)
            res["empty"] = (r["schema"], sorted(r), sorted(r["metrics"]),
                            all(sorted(m) == ["coverage", "diagnostic", "n", "source", "unknown", "value"]
                                for m in r["metrics"].values()),
                            all(m["value"] is None and m["unknown"] and m["n"] == 0 and m["coverage"] is None
                                for m in r["metrics"].values()),
                            r["sources"]["events"]["exists"], r["provenance"])
            b = _inst(td, "lc-m-ok", {"state": "IDLE", "turn": 4})
            _metrics_fixture(b, _m_rows(), _m_state(), _m_turns())
            r = metrics.compute(b)
            evb = b.events_path.read_bytes()
            res["ok"] = _m_sum(r) + (r["metrics"]["manual_rate"]["diagnostic"]["observed"]["rounds_with_manual"],
                                     r["sources"]["events"]["sha256"] == hashlib.sha256(evb).hexdigest())
            res["win"] = _m_sum(metrics.compute(b, first=2, last=3))
            res["win_cur"] = _m_sum(metrics.compute(b, first=4))
            # 事件流任何一处不完整：manual_rate 整体 unknown、n=0、coverage 为 None，不按轮局部放过
            corrupt = {}
            rows = _m_rows()
            corrupt["坏JSON行插入"] = (rows[:3] + ['{"seq": 99, broken'] + rows[3:], _m_state(), _m_turns())
            corrupt["无seq人工行插入"] = (rows[:3] + [json.dumps({"ts": "2026-10-15T00:00:30+00:00", "type": "exec", "turn": 2,
                                                              "actor": "cli", "by": "executor"})] + rows[3:],
                                       _m_state(), _m_turns())
            corrupt["倒序"] = (list(reversed(_m_rows())), _m_state(), _m_turns())
            dup = _m_rows()
            dup.insert(5, dict(dup[4]))
            corrupt["重复seq"] = (dup, _m_state(), _m_turns())
            gap = _m_rows()
            del gap[3]
            corrupt["缺口"] = (gap, _m_state(), _m_turns())
            for k, mut in (("缺ts", ("ts", None)), ("缺actor", ("actor", None)), ("轮号字符串", ("turn", "x")),
                           ("轮号布尔", ("turn", True)), ("轮号超前", ("turn", 9)),
                           ("显式别的项目", ("project", "other-project"))):
                rows = _m_rows()
                if mut[1] is None:
                    del rows[3][mut[0]]
                else:
                    rows[3][mut[0]] = mut[1]
                corrupt[k] = (rows, _m_state(), _m_turns())
            corrupt["末尾缺失"] = (_m_rows(), _m_state(seq=9), _m_turns())
            corrupt["state缺失"] = (_m_rows(), None, _m_turns())
            corrupt["state损坏"] = (_m_rows(), "{not json", _m_turns())
            corrupt["state无seq"] = (_m_rows(), {"state": "IDLE", "turn": 4}, _m_turns())
            corrupt["turns坏行"] = (_m_rows(), _m_state(), _m_turns() + ["{bad"])
            corrupt["turns轮号不在事件流"] = (_m_rows(), _m_state(), _m_turns() + [{"turn": 7, "status": "sent"}])
            corrupt["turns缺失"] = (_m_rows(), _m_state(), None)
            corrupt["turns读取错误"] = (_m_rows(), _m_state(), "DIR")
            res["corrupt"] = {}
            for k, (rows, stv, trn) in corrupt.items():
                c = _inst(td, f"lc-m-x{len(res['corrupt'])}", {"state": "IDLE", "turn": 4})
                _metrics_fixture(c, rows, None, None if trn in (None, "DIR") else trn)
                if trn == "DIR":
                    c.turns_path.mkdir()
                if stv is None:
                    c.state_path.unlink()
                else:
                    c.state_path.write_text(stv if isinstance(stv, str) else json.dumps(stv), encoding="utf-8")
                r = metrics.compute(c)
                m = r["metrics"]["manual_rate"]
                res["corrupt"][k] = (m["value"], m["n"], m["coverage"], m["unknown"], r["window"]["rounds"],
                                     r["window"]["denominator_certain"], bool(m["diagnostic"]["issues"]),
                                     r["metrics"]["first_pass_rate"]["diagnostic"]["observed"],
                                     r["metrics"]["rework_rate"]["diagnostic"]["observed"])
            # 事件流完整，但某轮不能证明已关闭：留在分母里、不计入样本
            nc = {}
            nc["缺round_result"] = ([x for x in _M_SPEC if not (x[0] == 3 and x[1] == "round_result")], None)
            nc["缺ts_done"] = (_M_SPEC, "drop3")
            nc["边界后迟到事件"] = (_M_SPEC + [(3, "note", {})], None)
            nc["不以COMPOSE开始"] = ([(1, "transition", {"to": "COMPOSE"}), (1, "round_result", {"ok": True}),
                                     (2, "transition", {"to": "COMPOSE"}), (2, "round_result", {"ok": True}),
                                     (3, "note", {}), (3, "round_result", {"ok": True}), (4, "transition", {"to": "COMPOSE"})],
                                    None)
            res["notclosed"] = {}
            for k, (spec, trn) in nc.items():
                c = _inst(td, f"lc-m-n{len(res['notclosed'])}", {"state": "IDLE", "turn": 4})
                rows = _m_rows(spec)
                trn = _m_turns(rows=rows)
                if k == "缺ts_done":
                    trn = [x for x in trn if not (x["turn"] == 3 and "ts_done" in x)]
                _metrics_fixture(c, rows, _m_state(seq=len(rows)), trn)
                r = metrics.compute(c)
                m = r["metrics"]["manual_rate"]
                res["notclosed"][k] = _m_sum(r) + (m["diagnostic"]["observed"]["rounds_counted"],)
            # 第 0 轮（接入/引导）不是正式轮
            z = _inst(td, "lc-m-zero", {"state": "IDLE", "turn": 1})
            zrows = _m_rows([(0, "init", {}), (1, "transition", {"to": "COMPOSE"})])
            _metrics_fixture(z, zrows, _m_state(seq=2, turn=1), [{"turn": 1, "status": "sent", "ts_sent": zrows[1]["ts"]}])
            res["zero"] = _m_sum(metrics.compute(z))
            # 一次日志不证物理一次发送；排队没有 ready/picked；返工没有事件也不等于没有返工
            spec = list(_M_SPEC)
            spec.insert(4, (2, "auto", {"rule": "FP_MISMATCH", "action": "block"}))
            rows = _m_rows(spec)
            _metrics_fixture(b, rows, _m_state(seq=len(rows)), _m_turns(rows=rows))
            lp.write_text("".join(json.dumps(x) + "\n" for x in (
                [{"action": "send", "turn": t, "project": b.name} for t in (1, 2, 3)]
                + [{"action": "send", "turn": 1, "project": "other-project"}, {"action": "send", "turn": 2}])) + "{bad\n",
                encoding="utf-8")
            r = metrics.compute(b)
            f, q, w = r["metrics"]["first_pass_rate"], r["metrics"]["queue_share"], r["metrics"]["rework_rate"]
            res["fp"] = (f["value"], f["unknown"], f["n"], f["coverage"], f["diagnostic"]["observed"]["rounds_with_one_logged_send"],
                         f["diagnostic"]["observed"]["rounds_with_round_result_ok"],
                         any("没有 project" in x for x in f["diagnostic"]["issues"]),
                         any("坏行" in x for x in f["diagnostic"]["issues"]), bool(f["diagnostic"]["why"]))
            res["queue"] = (q["value"], q["unknown"], q["n"], q["coverage"], "ready" in (q["diagnostic"]["why"] or ""))
            res["rework"] = (w["value"], w["unknown"], w["n"], w["diagnostic"]["observed"]["known_rework_events_by_round"],
                             "不代表没有返工" in (w["diagnostic"]["why"] or ""))
            rej = []
            for kw in ({"first": True}, {"first": -1}, {"first": "1"}, {"last": 1.0}, {"first": 3, "last": 2}):
                try:
                    metrics.compute(b, **kw)
                    rej.append("accepted")
                except WfError:
                    rej.append("refused")
            res["rej"] = rej
        finally:
            restore()
    _ok("B2-C3 指标结构（合成夹具，不是实景）：schema metrics/1；每项 value/unknown/n/coverage/source/diagnostic；"
        "空项目一律 unknown、n=0、coverage 为 None",
        res["empty"] == ("metrics/1", ["metrics", "project", "provenance", "schema", "sources", "window"],
                         ["first_pass_rate", "manual_rate", "queue_share", "rework_rate"], True, True, False,
                         "local_records_unattested"), json.dumps(res["empty"], ensure_ascii=False))
    _ok("B2-C3 人工介入率（合成夹具）：事件流完整、各轮按协议证明已关闭才计入；窗口按轮号闭区间、第 0 轮与当前轮不计；来源带 sha256",
        res["ok"] == (1 / 3, 3, 1.0, False, [1, 2, 3], True, [2], True)
        and res["win"] == (0.5, 2, 1.0, False, [2, 3], True) and res["win_cur"] == (None, 0, None, True, [], True)
        and res["zero"] == (None, 0, None, True, [], True),
        json.dumps([res["ok"], res["win"], res["win_cur"], res["zero"]], ensure_ascii=False))
    bad_c = {k: v for k, v in res["corrupt"].items()
             if v != (None, 0, None, True, [], False, True, {}, {})}
    _ok("B2-C3 fix1 事件流不完整一律整体 unknown（n=0、coverage 为 None、分母不可证、不给观测计数）：坏 JSON 行或无 seq 人工行插入、"
        "倒序、重复、缺口、缺 ts 或 actor、错轮号、显式别的项目、末尾缺失、state.json 缺失/损坏/无 seq、轮次记录坏行/不一致/缺失/读取错误",
        len(res["corrupt"]) == 19 and not bad_c, json.dumps(bad_c or res["corrupt"], ensure_ascii=False)[:1200])
    nc_expect = {"缺round_result": (0.5, 2, 2 / 3, False, [1, 2, 3], True, [1, 2]),
                 "缺ts_done": (0.5, 2, 2 / 3, False, [1, 2, 3], True, [1, 2]),
                 "边界后迟到事件": (0.5, 2, 2 / 3, False, [1, 2, 3], True, [1, 2]),
                 "不以COMPOSE开始": (0.0, 1, 1 / 3, False, [1, 2, 3], True, [1])}   # 第 2 轮的边界也不成立
    _ok("B2-C3 fix1 不能证明已关闭的轮次（缺 round_result、缺 ts_done、边界后仍有本轮事件、不以转入 COMPOSE 开始）留在分母里、不计入样本",
        res["notclosed"] == nc_expect, json.dumps(res["notclosed"], ensure_ascii=False)[:900])
    _ok("B2-C3 首次通过率：每轮恰好一条发送日志也不证明物理只发一次，value 为 None 只给观测计数；缺 project 与坏行有诊断；"
        "排队占比没有 ready/picked 协议时 unknown；返工率只给已知事件的观测计数，没有事件不当作没有返工",
        res["fp"] == (None, True, 0, None, [1, 2, 3], [1, 2, 3], True, True, True)
        and res["queue"] == (None, True, 0, None, True)
        and res["rework"] == (None, True, 0, {"2": {"auto:FP_MISMATCH": 1}}, True),
        json.dumps({k: res[k] for k in ("fp", "queue", "rework")}, ensure_ascii=False))
    _ok("B2-C3 窗口参数：布尔、负数、字符串、浮点、first 大于 last 都拒绝",
        res["rej"] == ["refused"] * 5, json.dumps(res["rej"]))


def _t_metrics_real_api(_ok):
    """B2-C3 fix1：用真实 API（Instance.event、machine.go、ops.mark_sent/mark_replied）走出的事件流与轮次记录（内容合成，不是实景）。"""
    from . import core as _core, machine, metrics, ops
    old_actor = _core.ACTOR.get("actor")
    res = {}
    with tempfile.TemporaryDirectory() as td:
        lp = Path(td) / "bridge.jsonl"
        lp.write_text("", encoding="utf-8")
        restore = _with_transport(lp)
        try:
            a = _inst(td, "lc-m-api", None)
            _core.ACTOR["actor"] = "cli"
            a.event("init", by="executor")
            machine.go(a, "COMPOSE", by="executor")
            res["boot"] = _m_sum(metrics.compute(a))
            _core.ACTOR["actor"] = "driver"
            ops.mark_sent(a, by="driver")
            res["boot_sent"] = _m_sum(metrics.compute(a))
            for t in (1, 2):
                if t == 2:
                    ops.mark_sent(a, by="driver")
                ops.mark_replied(a, by="driver")
                machine.go(a, "REVIEW", by="driver")
                if t == 1:
                    _core.ACTOR["actor"] = "cli"   # 命令行里的手工执行：人工介入
                    a.event("exec", cmd="ls", by="executor")
                    _core.ACTOR["actor"] = "driver"
                machine.go(a, "VERIFY", by="driver")
                a.event("round_result", ok=True, by="driver")
                machine.go(a, "COMPOSE", by="driver")
            r = metrics.compute(a)
            res["closed"] = _m_sum(r) + (r["metrics"]["manual_rate"]["diagnostic"]["observed"]["rounds_with_manual"],)
            st = json.loads(a.state_path.read_text(encoding="utf-8"))
            res["state"] = (st.get("turn"), st.get("seq"), len(a.events_path.read_text(encoding="utf-8").splitlines()))
        finally:
            _core.ACTOR["actor"] = old_actor or "cli"
            restore()
    _ok("B2-C3 fix1 真实 API：仅接入与进入第 1 轮时没有可计入的轮次（第 0 轮不算、未关闭的第 1 轮不算）；"
        "两轮按协议关闭后人工介入率为 1/2、coverage 为 1",
        res["boot"][:4] == (None, 0, None, True) and res["boot"][4] == []
        and res["boot_sent"] == (None, 0, None, True, [], True)
        and res["closed"] == (0.5, 2, 1.0, False, [1, 2], True, [1]) and res["state"][0] == 3 and res["state"][1] == res["state"][2],
        json.dumps(res, ensure_ascii=False)[:900])


def _m_real_round(td, name):
    """fix2：真实 API 走一轮并进入下一轮 COMPOSE（第 1 轮有一次命令行人工执行）；内容合成，不是实景。"""
    from . import core as _core, machine, ops
    old_actor = _core.ACTOR.get("actor")
    try:
        a = _inst(td, name, None)
        _core.ACTOR["actor"] = "cli"
        a.event("init", by="executor")
        machine.go(a, "COMPOSE", by="executor")
        _core.ACTOR["actor"] = "driver"
        ops.mark_sent(a, by="driver")
        ops.mark_replied(a, by="driver")
        machine.go(a, "REVIEW", by="driver")
        _core.ACTOR["actor"] = "cli"
        a.event("exec", cmd="ls", by="executor")
        _core.ACTOR["actor"] = "driver"
        machine.go(a, "VERIFY", by="driver")
        a.event("round_result", ok=True, by="driver")
        machine.go(a, "COMPOSE", by="driver")
    finally:
        _core.ACTOR["actor"] = old_actor or "cli"
    return a


def _t_metrics_time_identity(_ok):
    """B2-C3 fix2：闭合证据的时间与轮次记录的项目身份（真实 API 底稿上只改一处；内容合成，不是实景）。"""
    from datetime import timedelta
    from . import metrics
    from .core import parse_iso

    def jl(p):
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]

    def wl(p, rows):
        p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")

    def sent(rows):
        return [x for x in rows if x.get("status") == "sent" and x.get("turn") == 1]

    def done(rows):
        return [x for x in rows if "ts_done" in x and x.get("turn") == 1]

    def ev_ts(a, type_):
        return next(e["ts"] for e in jl(a.events_path) if e["type"] == type_ and e["turn"] == 1)

    def shift(ts, s):
        return (parse_iso(ts) + timedelta(seconds=s)).isoformat(timespec="seconds")
    cases = {
        "正例": ("turns", lambda a, r: None),
        "显式本项目": ("turns", lambda a, r: [x.update(project=a.name) for x in r]),
        "R1 ts_done 非日期": ("turns", lambda a, r: done(r)[0].update(ts_done="not-a-date")),
        "R2 ts_done 为 true": ("turns", lambda a, r: done(r)[0].update(ts_done=True)),
        "R3 发送记录缺 ts_sent": ("turns", lambda a, r: sent(r)[0].pop("ts_sent")),
        "R4 两条记录属于别的项目": ("turns", lambda a, r: [x.update(project="other-project") for x in r[:2]]),
        "未来 ts_done": ("turns", lambda a, r: done(r)[0].update(ts_done="2031-01-01T00:00:00+00:00")),
        "数字 ts_done": ("turns", lambda a, r: done(r)[0].update(ts_done=20261015)),
        "数字串 ts_done": ("turns", lambda a, r: done(r)[0].update(ts_done="20261015")),
        "逆时序 发送晚于回复": ("turns", lambda a, r: sent(r)[0].update(ts_sent=shift(done(r)[0]["ts_done"], 30))),
        "发送早于本轮开始": ("turns", lambda a, r: sent(r)[0].update(ts_sent=shift(ev_ts(a, "transition"), -3600))),
        "回复晚于 round_result": ("turns", lambda a, r: done(r)[0].update(ts_done=shift(ev_ts(a, "round_result"), 60))),
        "事件未来 ts": ("events", lambda a, r: next(x for x in r if x["type"] == "round_result").update(
            ts="2031-01-01T00:00:00+00:00")),
        "事件数字 ts": ("events", lambda a, r: next(x for x in r if x["type"] == "round_result").update(ts=1760000000)),
    }
    res = {}
    with tempfile.TemporaryDirectory() as td:
        lp = Path(td) / "bridge.jsonl"
        lp.write_text("", encoding="utf-8")
        restore = _with_transport(lp)
        try:
            for i, (k, (which, mut)) in enumerate(cases.items()):
                a = _m_real_round(td, f"lc-m-t{i}")
                p = a.turns_path if which == "turns" else a.events_path
                rows = jl(p)
                mut(a, rows)
                wl(p, rows)
                r = metrics.compute(a)
                m = r["metrics"]["manual_rate"]
                res[k] = _m_sum(r) + ("；".join(m["diagnostic"]["issues"])[:160],)
        finally:
            restore()
    whole = (None, 0, None, True, [], False)
    closure = (None, 0, 0.0, True, [1], True)
    exp = {"正例": ((1.0, 1, 1.0, False, [1], True), ""), "显式本项目": ((1.0, 1, 1.0, False, [1], True), ""),
           "R1 ts_done 非日期": (whole, "ts_done 不是有效时间"), "R2 ts_done 为 true": (whole, "ts_done 不是有效时间"),
           "R3 发送记录缺 ts_sent": (whole, "没有 ts_sent"), "R4 两条记录属于别的项目": (whole, "别的项目"),
           "未来 ts_done": (whole, "未来时刻"), "数字 ts_done": (whole, "不是有效时间"), "数字串 ts_done": (whole, ""),
           "逆时序 发送晚于回复": (closure, "时间先后颠倒"), "发送早于本轮开始": (closure, "早于本轮转入 COMPOSE"),
           "回复晚于 round_result": (closure, "晚于 round_result"),
           "事件未来 ts": (whole, "未来时刻"), "事件数字 ts": (whole, "缺少或无效 ts")}
    bad = {k: v for k, v in res.items() if v[:6] != exp[k][0] or (exp[k][1] not in v[6]) or (bool(exp[k][1]) and not v[6])}
    # 数字串 "20261015" 可被 fromisoformat 解析为日期：它要么被判为无效时间，要么时序不通过——都不能算满分样本
    num_str = res.get("数字串 ts_done", (None,) * 7)
    if num_str[:6] in (whole, closure):
        bad.pop("数字串 ts_done", None)
    _ok("B2-C3 fix2 闭合证据的时间与身份：ts_done 非日期、为 true 或数字、发送记录缺 ts_sent、显式别的项目、未来时刻、事件 ts 未来或为数字"
        "都使整体 unknown（n=0、coverage 为 None、分母不可证）并给出诊断；发送晚于回复、发送早于本轮开始、回复晚于 round_result "
        "的轮次不计入（coverage 0）；没有 project 或显式本项目的轮次记录照常计入",
        not bad and len(res) == len(exp), json.dumps(bad or res, ensure_ascii=False)[:1500])


def _t_metrics_readonly(_ok):
    """B2-C3：compute 只读——项目与 local/ 目录前后逐字节、mtime、权限一致；写文件、起进程、适配器调用一律未发生。"""
    import builtins
    import subprocess as _sp
    from . import anomaly, core, metrics
    from .core import LOCAL

    def snap(*roots):
        out = {}
        for root in roots:
            for p in sorted(Path(root).rglob("*")):
                stt = p.lstat()
                out[str(p)] = (stt.st_mode, stt.st_mtime_ns, stt.st_size,
                               hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
        return out
    calls = []

    def boom(name):
        def _f(*a, **k):
            calls.append(name)
            raise RuntimeError(f"metrics.compute 不应调用 {name}")
        return _f
    real_open = builtins.open

    def ro_open(file, mode="r", *a, **k):
        if any(c in str(mode) for c in "wax+"):
            calls.append(f"open({mode})")
            raise RuntimeError("metrics.compute 不应以写方式打开文件")
        return real_open(file, mode, *a, **k)
    with tempfile.TemporaryDirectory() as td:
        lp = Path(td) / "bridge.jsonl"
        lp.write_text(json.dumps({"action": "send", "turn": 1, "project": "x"}) + "\n", encoding="utf-8")
        restore = _with_transport(lp)
        b = _inst(td, "lc-m-ro", {"state": "IDLE", "turn": 4})
        _metrics_fixture(b, _m_rows(), _m_state(), _m_turns())
        LOCAL.mkdir(parents=True, exist_ok=True)
        before = snap(b.root, LOCAL, td)
        patches = [(builtins, "open", ro_open), (_sp, "run", boom("subprocess.run")), (_sp, "Popen", boom("subprocess.Popen")),
                   (anomaly, "call", boom("anomaly.call")), (core, "write_json", boom("write_json")),
                   (core, "append_jsonl", boom("append_jsonl")), (Path, "write_text", boom("Path.write_text")),
                   (Path, "write_bytes", boom("Path.write_bytes")), (Path, "mkdir", boom("Path.mkdir")),
                   (os, "replace", boom("os.replace")), (os, "rename", boom("os.rename")), (os, "remove", boom("os.remove"))]
        saved = []
        err = ""
        try:
            for mod, name, fn in patches:
                saved.append((mod, name, getattr(mod, name)))
                setattr(mod, name, fn)
            r = metrics.compute(b)
        except Exception as e:   # noqa: BLE001
            r, err = None, f"{type(e).__name__}: {e}"
        finally:
            for mod, name, v in reversed(saved):
                setattr(mod, name, v)
            restore()
        after = snap(b.root, LOCAL, td)
        state_mem = dict(b.state)
    _ok("B2-C3 只读：compute 前后项目、local/ 与临时目录逐字节、mtime、权限一致；没有写文件、建目录、起进程或调用适配器",
        not err and r is not None and before == after and not calls and r["metrics"]["manual_rate"]["n"] == 3
        and state_mem.get("turn") == 4,
        json.dumps([err, calls, sorted(set(before) ^ set(after))[:5],
                    [k for k in before if k in after and before[k] != after[k]][:5]], ensure_ascii=False)[:800])


def _t_goals_empty_guard(_ok):
    """B2-C2a：空集保护——至少一个可执行目标才可能达成；all_exec 为空集、目标未批准、可执行目标未通过时人工验收不就绪；
    默认骨架批准且两条机械判据通过后验收正常就绪、确认后达成。只在临时目录里，不执行真实判据命令。"""
    from . import asks, goals, policy
    from .core import WfError

    def mk(td, name, gl):
        inst = _inst(td, name, {"state": "IDLE", "turn": 1})
        (inst.wf / "goals.json").write_text(json.dumps(gl, ensure_ascii=False), encoding="utf-8")
        return inst

    def item(p, gid):
        return next(i for i in p["items"] if i["id"] == gid)

    def confirm(inst, gid):
        try:
            goals.confirm(inst, gid, "测试")
            return "confirmed"
        except WfError as e:
            return "refused:" + str(e)[:60]

    def pend(inst):
        return [str(x.get("kind")) for x in asks.pending(inst)]

    def fake_pass(inst, gids):
        """合成的机械记录（不是执行判据命令）：按当前 goals_sha 写入通过记录。"""
        cur = policy.status(inst, "goals")["current"]
        gs = {gid: {"ok": True, "why": [], "ts": "2026-10-15T00:00:00+00:00", "exit": 0, "goals_sha": cur} for gid in gids}
        (inst.wf / "goals_state.json").write_text(json.dumps(gs), encoding="utf-8")
    E = lambda gid, chk: {"id": gid, "title": gid, "kind": "exec", "check": chk, "pass": {"exit": 0}, "means": "m", "on_fail": "f"}   # noqa: E731
    M = lambda gid, **kw: dict({"id": gid, "title": gid, "kind": "manual", "means": "m", "on_fail": "f"}, **kw)   # noqa: E731
    res = {}
    with tempfile.TemporaryDirectory() as td:
        a = mk(td, "c2a-empty", {"goals": []})
        r0 = goals.progress(a)["reached"]
        policy.approve(a, "goals", "测试", by="test")
        p = goals.progress(a)
        res["empty"] = (r0, p["approved"], p["reached"], p["total"])

        b = mk(td, "c2a-manual", {"goals": [M("U1", depends_on="all_exec"), M("U2")]})
        policy.approve(b, "goals", "测试", by="test")
        u1 = item(goals.progress(b), "U1")
        res["manual_allexec"] = (u1.get("ready"), u1["ok"], "空集" in u1["detail"], "confirm:U1" in pend(b),
                                 confirm(b, "U1").startswith("refused"), confirm(b, "U2"))
        p = goals.progress(b)
        res["manual_allexec_after"] = (p["reached"], item(p, "U1")["ok"], item(p, "U2")["ok"], p["done"], p["total"])

        c = mk(td, "c2a-manual2", {"goals": [M("U2"), M("U3")]})
        policy.approve(c, "goals", "测试", by="test")
        res["manual_all_confirmed"] = (confirm(c, "U2"), confirm(c, "U3"))
        p = goals.progress(c)
        res["manual_all_confirmed_p"] = (p["done"], p["total"], p["approved"], p["reached"])

        d = mk(td, "c2a-fail", {"goals": [E("E1", "false"), M("U1", depends_on="all_exec")]})
        policy.approve(d, "goals", "测试", by="test")
        goals.run_checks(d, by="test")
        u1 = item(goals.progress(d), "U1")
        res["exec_fail"] = (u1.get("ready"), u1["ok"], "E1" in u1["detail"], "confirm:U1" in pend(d),
                            confirm(d, "U1").startswith("refused"), goals.progress(d)["reached"])

        e = mk(td, "c2a-pass", {"goals": [E("E1", "true"), M("U1", depends_on="all_exec")]})
        policy.approve(e, "goals", "测试", by="test")
        goals.run_checks(e, by="test")
        u1 = item(goals.progress(e), "U1")
        res["exec_pass"] = (u1.get("ready"), "confirm:U1" in pend(e), confirm(e, "U1"), goals.progress(e)["reached"])

        f = mk(td, "c2a-unapproved", {"goals": [E("E1", "true"), M("U1", depends_on="all_exec")]})
        fake_pass(f, ["E1"])
        p = goals.progress(f)
        u1 = item(p, "U1")
        res["unapproved"] = (item(p, "E1")["ok"], u1.get("ready"), "尚未批准" in u1["detail"], p["reached"],
                             "confirm:U1" in pend(f), confirm(f, "U1").startswith("refused"))

        h = mk(td, "c2a-contains", {"goals": [{"id": "E1", "title": "旧判据", "kind": "exec", "check": "echo ok",
                                               "pass": {"contains": ["ok"]}, "means": "m", "on_fail": "f"}]})
        policy.approve(h, "goals", "测试", by="test")
        goals.run_checks(h, by="test")
        p = goals.progress(h)
        res["contains_only"] = (item(p, "E1")["ok"], p["reached"], goals.judge({"contains": ["ok"]}, 3, "ok")[0])

        k = _inst(td, "c2a-default", {"state": "IDLE", "turn": 1})
        (k.wf / "goals.json").write_text(json.dumps(goals.default_goals(k.name, today="2026-10-15"), ensure_ascii=False),
                                         encoding="utf-8")
        acc0 = item(goals.progress(k), "BASE-ACCEPT")
        policy.approve(k, "goals", "测试", by="test")
        acc1 = item(goals.progress(k), "BASE-ACCEPT")
        fake_pass(k, ["BASE-SELF", "BASE-PROBES"])
        p = goals.progress(k)
        acc2, smoke = item(p, "BASE-ACCEPT"), item(p, "BASE-SMOKE")
        pend_k = pend(k)
        res["default"] = (acc0.get("ready"), acc1.get("ready"), "BASE-SELF" in acc1["detail"], acc2.get("ready"),
                          smoke.get("ready", True), "confirm:BASE-ACCEPT" in pend_k, "confirm:BASE-SMOKE" in pend_k,
                          p["reached"], confirm(k, "BASE-SMOKE"), confirm(k, "BASE-ACCEPT"))
        p = goals.progress(k)
        res["default_done"] = (p["done"], p["total"], p["reached"])
    _ok("B2-C2a 空目标即使批准也不达成；只有人工目标时即使全部确认也不达成",
        res["empty"] == (False, True, False, 0) and res["manual_all_confirmed"] == ("confirmed", "confirmed")
        and res["manual_all_confirmed_p"] == (2, 2, True, False), json.dumps({k: res[k] for k in (
            "empty", "manual_all_confirmed", "manual_all_confirmed_p")}, ensure_ascii=False))
    _ok("B2-C2a depends_on=all_exec 在没有可执行目标时不就绪、不通知、确认被前置保护拒绝，也不算完成；其他人工目标照常确认",
        res["manual_allexec"] == (False, False, True, False, True, "confirmed")
        and res["manual_allexec_after"] == (False, False, True, 1, 2),
        json.dumps({k: res[k] for k in ("manual_allexec", "manual_allexec_after")}, ensure_ascii=False))
    _ok("B2-C2a 可执行目标未通过、或目标未批准时人工验收不就绪、不通知、不能确认；可执行目标通过后照常就绪、确认后达成",
        res["exec_fail"] == (False, False, True, False, True, False)
        and res["exec_pass"] == (True, True, "confirmed", True)
        and res["unapproved"] == (True, False, True, False, False, True),
        json.dumps({k: res[k] for k in ("exec_fail", "exec_pass", "unapproved")}, ensure_ascii=False))
    _ok("B2-C2a 旧的只含 contains 的判据语义不变：没有 exit 条件时不要求退出码，可执行目标通过即可达成",
        res["contains_only"] == (True, True, True), json.dumps(res["contains_only"]))
    _ok("B2-C2a 默认骨架不是永久禁用：未批准与机械判据未通过时验收不就绪；批准且两条机械判据通过（合成记录）后验收就绪并通知，"
        "冒烟仍按人工规则；两项确认后达成",
        res["default"] == (False, False, True, True, True, True, True, False, "confirmed", "confirmed")
        and res["default_done"] == (4, 4, True), json.dumps({k: res[k] for k in ("default", "default_done")}, ensure_ascii=False))


def _t_mech_v2(_ok):
    """B2-C2b（含返工）：逐目标机械绑定 v2——无关目标变化复用；本目标与依赖定义、闭包内文件（新增、删除、权限、内容、
    链接去处）变化失效；绑定不成立（读失败、超预算、检查期间变化、运行时审计不通过、无法推导）一律失败关闭；
    只认明确的旧格式；兄弟分支汇合成环也能发现；progress 的 ok、layer、reached 一致。临时工具树与项目。"""
    from . import goals, policy
    from .core import WfError
    out, bad_consistency = {}, []
    old = (goals._TOOL_ROOT, goals._hash_file, goals._BUDGET_FILES)
    with tempfile.TemporaryDirectory() as td:
        tool = _tool_repo(td)
        for rel, txt in (("wflib/selftest_x.py", "T = 1\n"), ("dashboard/flow.html", "<p>x</p>\n"),
                         ("MANIFEST.json", "{}\n"), ("docs/a.md", "# a\n"), ("local/runtime.json", "{}\n")):
            (tool / rel).parent.mkdir(parents=True, exist_ok=True)
            (tool / rel).write_text(txt, encoding="utf-8")
        a = _inst(td, "c2b", {"state": "IDLE", "turn": 1})
        root = a.root
        files = {"scripts/check.py": "import sys\nprint(open(sys.argv[1]).read().strip())\n",
                 "scripts/dyn.py": "import subprocess\nprint('ok')\n",
                 "scripts/env.py": "import os\nprint(os.environ.get('HOME') and 'ok')\n",
                 "scripts/lit.py": "print(open('README.md').read())\n",
                 "scripts/imp.py": "import helper\nprint(helper.X)\n", "scripts/helper.py": "X = 'ok'\n",
                 "scripts/comp.py": "p = 'da' + 'ta/in.txt'\nprint(open(p).read().strip())\n",
                 "scripts/cd.py": "import os\nos.chdir('data')\nprint(open('in.txt').read().strip())\n",
                 "scripts/ex.py": "import os\nprint('ok', flush=True)\nos._exit(0)\n",
                 "scripts/st.py": "import os, sys\nprint('ok' if not os.path.exists(sys.argv[1]) else 'no')\n",
                 "scripts/w.py": "open('data/out.txt', 'w').write('x')\nprint('ok')\n",
                 "scripts/sp.py": "m = __import__('sub' + 'process')\nm.run(['true'])\nprint('ok')\n",
                 "data/in.txt": "ok\n", "README.md": "readme\n", "notes.txt": "ok\n"}
        for rel, txt in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(txt, encoding="utf-8")
        outside = Path(td) / "outside"
        outside.mkdir()
        (outside / "in.txt").write_text("ok\n", encoding="utf-8")
        os.symlink(root / "data", root / "alias")   # 项目内的符号链接：去处在项目内，可以绑定；affects 不许经过它
        E = lambda gid, chk, **kw: dict({"id": gid, "title": gid, "kind": "exec", "check": chk, "pass": {"exit": 0},   # noqa: E731
                                         "means": "m", "on_fail": "f"}, **kw)
        M = lambda gid, **kw: dict({"id": gid, "title": gid, "kind": "manual", "means": "m", "on_fail": "f"}, **kw)   # noqa: E731
        chk = "python3 scripts/check.py data/in.txt"
        base = [E("W1", "wf version"), E("P1", chk, affects=["scripts", "data/in.txt"]), E("P1n", chk, affects=["scripts/check.py",
                                                                                                                "data/in.txt"]),
                E("P2", chk), E("P3", chk, affects=["scripts", "data"]), E("U1", "cat notes.txt"), E("D1", "python3 scripts/dyn.py"),
                E("L1", "true", live={"rounds": 3, "segments": [1]}), E("X1", "true", depends_on=["W1"])]
        ids = [x["id"] for x in base]
        gp = a.wf / "goals.json"

        def setg(gl, approve=True):
            gp.write_text(json.dumps({"goals": gl}, ensure_ascii=False), encoding="utf-8")
            if approve:
                policy.approve(a, "goals", "测试", by="test")

        def prog(tag):
            p = goals.progress(a)
            for i in p["items"]:
                if i["kind"] == "exec" and (i["ok"] != (i["layer"] in ("mechanical_pass", "verified"))):
                    bad_consistency.append((tag, i["id"], i["ok"], i["layer"]))
            if p["reached"] and not (p["approved"] and all(i["ok"] for i in p["items"])):
                bad_consistency.append((tag, "reached", p["approved"]))
            return p

        def notok(tag, only=None):
            """不通过（ok 为假）的目标编号集合。"""
            p = prog(tag)
            return sorted(i["id"] for i in p["items"] if not i["ok"] and (only is None or i["id"] in only))

        def detail(gid):
            return next(i["detail"] for i in goals.progress(a)["items"] if i["id"] == gid)

        def gs():
            return json.loads((a.wf / "goals_state.json").read_text(encoding="utf-8"))

        def put(d):
            (a.wf / "goals_state.json").write_text(json.dumps(d), encoding="utf-8")

        def kinds():
            return {k: v.get("binding", {}).get("kind") or ("global" if v.get("binding", {}).get("schema") == goals.GLOBAL_SCHEMA
                                                            else "unbound" if v.get("binding", {}).get("unbound") else "?")
                    for k, v in gs().items()}
        try:
            goals._TOOL_ROOT = tool
            setg(base)
            goals.run_checks(a, by="test")
            out["kinds"] = kinds()
            out["s0"] = notok("s0", ids)
            out["deps_X1"] = sorted(gs()["X1"]["binding"].get("deps") or {})
            out["d1_detail"] = "依赖绑定不成立" in detail("D1")
            # 无关变化复用（重新批准前 reached 为假）；定义变化失效
            setg([dict(base[0], title="改过的标题")] + base[1:], approve=False)
            p = prog("s1u")
            out["s1u"] = (notok("s1u", ids), p["approved"], p["reached"])
            policy.approve(a, "goals", "测试", by="test")
            out["s1"] = notok("s1", ids)
            setg([dict(base[0], title="改过的标题")] + base[1:] + [E("I2", "true")])
            out["s2"] = notok("s2", ids)
            setg([dict(base[0], means="新的含义")] + base[1:])
            out["s3"] = (notok("s3", ids), "本目标的定义已改动" in detail("W1"))
            setg([base[0], dict(base[1], **{"pass": {"exit": 0, "contains": ["o"]}})] + base[2:])
            out["s4"] = notok("s4", ids)
            setg(base)
            goals.run_checks(a, by="test")
            out["s5"] = notok("s5", ids)
            seq = []
            for tag, path, new in (
                    ("dashboard", tool / "dashboard/flow.html", "<p>y</p>\n"), ("selftest", tool / "wflib/selftest_x.py", "T = 2\n"),
                    ("manifest", tool / "MANIFEST.json", "{\"x\": 1}\n"), ("docs", tool / "docs/a.md", "# b\n"),
                    ("tool_mode", tool / "wflib/x.py", 0o100), ("tool_local", tool / "local/runtime.json", "{\"y\": 2}\n"),
                    ("readme", root / "README.md", "changed\n"), ("data_content", root / "data/in.txt", "ok 2\n"),
                    ("data_mode", root / "data/in.txt", 0o100), ("runtime", a.wf / "events.jsonl", "{}\n")):
                existed = path.exists()
                keep_txt = path.read_bytes() if existed else None
                keep_mode = os.stat(path).st_mode & 0o7777 if existed else None   # 恢复原权限，不写死（随 umask 不同）
                if isinstance(new, int):
                    os.chmod(path, keep_mode ^ new)   # 翻转一个权限位：无论 umask 如何都确实改变了权限
                else:
                    path.write_text(new, encoding="utf-8")
                after = notok(tag, ids)
                if existed:
                    path.write_bytes(keep_txt)
                    os.chmod(path, keep_mode)
                else:
                    path.unlink()
                seq.append((tag, after, notok(tag + "-back", ids)))
            for tag, p in (("tool_new", tool / "wflib/new.py"), ("data_new", root / "data/new.txt"),
                           ("proj_new", root / "other.txt"), ("scripts_new", root / "scripts/new.py")):
                p.write_text("n\n", encoding="utf-8")
                after = notok(tag, ids)
                p.unlink()
                seq.append((tag, after, notok(tag + "-back", ids)))
            (root / "data/in.txt").rename(root / "data/in.bak")
            after = notok("data_del", ids)
            (root / "data/in.bak").rename(root / "data/in.txt")
            seq.append(("data_del", after, notok("data_del-back", ids)))
            os.symlink(outside / "in.txt", root / "ext")   # 新增指向项目外的链接：整个项目绑定无法核对
            after = notok("ext_link", ids)
            (root / "ext").unlink()
            seq.append(("ext_link", after, notok("ext_link-back", ids)))
            out["seq"] = seq
            # 坏 hash、读失败、证据被改：不复用
            raw = gs()
            d = json.loads(json.dumps(raw))
            d["W1"]["binding"]["state"]["tool"]["digest"] = "0" * 64
            d["P1"]["binding"]["goal_sha"] = "bad"
            put(d)
            out["badhash"] = notok("badhash", ids)
            put(raw)

            def flaky(p):
                if str(p).endswith("README.md"):
                    raise PermissionError("模拟读取失败")
                return old[1](p)
            goals._hash_file = flaky
            out["readfail"] = (notok("readfail", ids), "无法核对" in detail("P2"))
            goals._hash_file = old[1]
            evp = root / raw["W1"]["evidence"]
            keep = evp.read_bytes()
            evp.write_bytes(keep + b"x")
            out["evidence"] = notok("evidence", ids)
            evp.write_bytes(keep)
            out["restored"] = notok("restored", ids)
            # 反例 1：新执行时绑定不成立（读权限失败、超出预算、检查期间变化）必须失败关闭，恢复后重新检查才通过
            goals._hash_file = lambda p: (_ for _ in ()).throw(PermissionError("模拟")) if str(p).endswith("notes.txt") \
                else old[1](p)
            goals.run_checks(a, by="test")
            goals._hash_file = old[1]
            u1 = gs()["U1"]
            (root / "notes.txt").write_text("changed later\n", encoding="utf-8")
            out["perm"] = (u1["ok"], bool(u1["binding"].get("unbound")), "PermissionError" in u1["binding"].get("unbound", ""),
                           "U1" in notok("perm", ids), "依赖绑定不成立" in detail("U1"))
            (root / "notes.txt").write_text("ok\n", encoding="utf-8")
            goals.run_checks(a, by="test")
            out["perm_back"] = ("U1" in notok("perm_back", ids), gs()["U1"]["binding"].get("kind"))
            if hasattr(os, "geteuid") and os.geteuid() != 0:   # 非 root 时再用真实的 chmod 000 走一遍
                os.chmod(root / "notes.txt", 0)
                goals.run_checks(a, by="test")
                os.chmod(root / "notes.txt", 0o644)
                out["perm_real"] = ("U1" in notok("perm_real", ids), bool(gs()["U1"]["binding"].get("unbound")))
                goals.run_checks(a, by="test")
            else:
                out["perm_real"] = (True, True)   # root 读得到 000 的文件，真实路径由上面的模拟覆盖
            goals._BUDGET_FILES = 1
            goals.run_checks(a, by="test")
            goals._BUDGET_FILES = old[2]
            out["budget"] = (sorted(k for k, v in gs().items() if "超出核对预算" in str(v["binding"].get("unbound"))),
                             notok("budget", ids))
            goals.run_checks(a, by="test")
            out["budget_back"] = notok("budget_back", ids)
            # 反例 2：只认明确的旧格式；显式坏 schema、列表、null、去掉 schema 的 v2、非 live 目标用全局 schema 一律拒绝
            raw = gs()
            fmt = {}
            for tag, mut in (("bogus", lambda b: dict(b, schema="bogus")), ("list", lambda b: [b]), ("null", lambda b: None),
                             ("schema_removed", lambda b: {k: v for k, v in b.items() if k != "schema"}),
                             ("global_on_exec", lambda b: {"schema": goals.GLOBAL_SCHEMA, "goals_sha": b["goals_sha"],
                                                           "evidence_sha256": b["evidence_sha256"]}),
                             ("legacy_b1", lambda b: {"goals_sha": b["goals_sha"], "evidence_sha256": b["evidence_sha256"]})):
                d = json.loads(json.dumps(raw))
                d["W1"]["binding"] = mut(d["W1"]["binding"])
                put(d)
                fmt[tag] = "W1" in notok("fmt-" + tag, ids)
            d = json.loads(json.dumps(raw))
            d["W1"].pop("binding")
            put(d)
            fmt["legacy_pre_b1"] = "W1" in notok("fmt-pre", ids)
            put(raw)
            out["formats"] = fmt
            d = json.loads(json.dumps(raw))
            d["W1"]["binding"] = {"goals_sha": d["W1"]["goals_sha"], "evidence_sha256": d["W1"]["binding"]["evidence_sha256"]}
            put(d)
            setg(base + [E("I3", "true")])
            out["legacy_global"] = "W1" in notok("legacy_global", ids)
            # 依赖：未知依赖、兄弟分支汇合成环 → 绑定不成立；菱形（无环）正常
            g_cycle = {"goals": [M("A", depends_on=["B"]), M("B", depends_on=["A"]),
                                 E("X3", "true", depends_on=["A", "B"]), M("C"), M("A2", depends_on=["C"]),
                                 M("B2", depends_on=["C"]), E("X4", "true", depends_on=["A2", "B2"])]}
            res = []
            for gid in ("X3", "X4"):
                try:
                    res.append(sorted(goals._dep_defs(g_cycle, next(x for x in g_cycle["goals"] if x["id"] == gid))))
                except goals._Unknown as e:
                    res.append("成环" in str(e))
            out["cycle"] = res
            setg(base + [E("X2", "true", depends_on=["NOPE"])] + g_cycle["goals"])
            goals.run_checks(a, by="test")
            st2 = gs()
            out["dep_unbound"] = ("不存在" in str(st2["X2"]["binding"].get("unbound")), "成环" in str(st2["X3"]["binding"].get("unbound")),
                                  st2["X4"]["binding"].get("kind"), "X2" in notok("dep", ["X2"]), "X3" in notok("dep", ["X3"]))
            # 运行时审计：计算出的路径、读项目外、换目录、os._exit 丢记录、对非字面量路径的存在性检查、读环境变量、检查期间写文件
            rt = [E("R1", "python3 scripts/comp.py", affects=["scripts"]),
                  E("R2", f"python3 scripts/check.py {outside / 'in.txt'}"),
                  E("R3", "python3 scripts/cd.py"), E("R4", "python3 scripts/ex.py", affects=["scripts"]),
                  E("R5", "python3 scripts/st.py data/nope.txt", affects=["scripts"]),
                  E("R6", "python3 scripts/env.py"), E("R8", "python3 scripts/sp.py")]
            setg(base + rt)
            goals.run_checks(a, by="test")
            st3 = gs()
            out["runtime"] = {x["id"]: (st3[x["id"]]["binding"].get("kind") or "unbound") for x in rt}
            out["runtime_ok_flags"] = notok("runtime", [x["id"] for x in rt])
            out["runtime_why"] = {x["id"]: str(st3[x["id"]]["binding"].get("unbound", ""))[:30] for x in rt}
            # 检查期间写入项目：本轮所有绑定到整个项目的记录都不成立（单独跑，避免影响上面的用例）
            setg(base + [E("R7", "python3 scripts/w.py")])
            goals.run_checks(a, by="test")
            st4 = gs()
            out["write_during"] = ("变化" in str(st4["R7"]["binding"].get("unbound")), "变化" in str(st4["P2"]["binding"].get("unbound")),
                                   st4["W1"]["binding"].get("kind"))
            (root / "data/out.txt").unlink()
            # affects 目录里指向 affects 之外的链接：执行会跟随，无法只凭 affects 绑定
            setg(base)
            os.symlink(root / "notes.txt", root / "data/ln")
            goals.run_checks(a, by="test")
            out["aff_link"] = (str(gs()["P3"]["binding"].get("unbound", "")).startswith("符号链接"), "P3" in notok("aff_link", ids))
            (root / "data/ln").unlink()
            goals.run_checks(a, by="test")
            out["aff_link_back"] = notok("aff_link_back", ids)
            try:
                goals.mark_na(a, "W1", condition="c", scope="s", reason="r", by="t")
                out["na_on_pass"] = "accepted"
            except WfError:
                out["na_on_pass"] = "refused"
            # 静态推导
            P = lambda **kw: dict(base[1], **kw)   # noqa: E731
            specs = {
                "ok": P(), "dir": base[4], "none": base[3],
                "dotdot": P(affects=["scripts", "../x"]), "abs": P(affects=["scripts", "/etc/passwd"]),
                "no_script": P(affects=["data/in.txt"]), "link": P(check="python3 scripts/check.py alias/in.txt",
                                                                affects=["scripts", "alias/in.txt"]),
                "missing": P(affects=["scripts", "data/in.txt", "nope.txt"]), "not_list": P(affects="scripts"),
                "unnorm": P(affects=["scripts", "data/./in.txt"]), "runtime_area": P(affects=["scripts", "data/in.txt", "workflow/evidence"]),
                "literal": P(check="python3 scripts/lit.py", affects=["scripts/lit.py"]),
                "import_missing": P(check="python3 scripts/imp.py", affects=["scripts/imp.py"]),
                "import_ok": P(check="python3 scripts/imp.py", affects=["scripts/imp.py", "scripts/helper.py"]),
                "dynamic": P(check="python3 scripts/dyn.py", affects=["scripts"]), "env": P(check="python3 scripts/env.py"),
                "stat_nonliteral": P(check="python3 scripts/st.py data/nope.txt", affects=["scripts"]),
                "interp_path": P(check=".venv/bin/python3 scripts/check.py data/in.txt"),
                "wf_tool": base[0], "wf_probes": E("W3", "wf self-check --probes"), "wf_ledger": E("W4", "wf ledger"),
                "wf_doctor": E("W2", "wf doctor"), "cat": base[5], "cat_abs": E("Y1", "cat /etc/hostname"),
                "cat_var": E("Y2", "cat $HOME/x"), "cat_up": E("Y3", "cat ../x"), "git": E("Y4", "git log --oneline"),
                "chain": E("Y5", "true && true"), "other": E("Y6", "curl http://x")}

            def sk(x):
                s = goals._closure_spec(a, x)
                return s["kind"] + ("+" if s.get("narrow") else "-" if s["kind"] == "python" else "")
            out["closure"] = {k: sk(v) for k, v in specs.items()}
            setg([E("F1", "false"), E("W1", "wf version")])
            goals.run_checks(a, by="test")
            setg([E("F1", "false"), E("W1", "wf version"), E("I4", "true")])
            f1 = next(i for i in prog("failed_reuse")["items"] if i["id"] == "F1")
            out["failed_reuse"] = (f1["ok"], f1["layer"], f1["detail"].startswith("退出码"))
        finally:
            goals._TOOL_ROOT, goals._hash_file, goals._BUDGET_FILES = old
    proj = ["P2", "U1", "X1"]
    _ok("B2-C2b 新记录：wf 只读工具命令绑定整棵工具；affects 经静态核对与运行时审计确认后只绑定 affects；observer 的导入目录"
        "不冒充脚本输入（P1n 也可收窄）；只读程序绑定项目；静态出现子进程的脚本绑定不成立（不通过）；带 live 的目标用全局绑定",
        out.get("kinds") == {"W1": "tool", "P1": "affects", "P1n": "affects", "P2": "project", "P3": "affects", "U1": "project",
                             "D1": "unbound", "L1": "global", "X1": "project"}
        and out["s0"] == ["D1"] and out["deps_X1"] == ["W1"] and out["d1_detail"],
        json.dumps({k: out.get(k) for k in ("kinds", "s0", "deps_X1", "d1_detail")}, ensure_ascii=False))
    _ok("B2-C2b 无关目标变化复用（重新批准前 reached 为假）；本目标定义变化失效并连带依赖它的目标；旧式与全局绑定按全局失效",
        out["s1u"] == (["D1", "L1"], False, False) and out["s1"] == ["D1", "L1"] and out["s2"] == ["D1", "L1"]
        and out["s3"] == (["D1", "L1", "W1", "X1"], True) and out["s4"] == ["D1", "L1", "P1"] and out["s5"] == ["D1"],
        json.dumps({k: out[k] for k in ("s1u", "s1", "s2", "s3", "s4", "s5")}, ensure_ascii=False))
    exp_seq = {"dashboard": ["W1"], "selftest": ["W1"], "manifest": ["W1"], "docs": ["W1"], "tool_mode": ["W1"], "tool_local": [],
               "readme": proj, "data_content": ["P1", "P1n", "P3"] + proj, "data_mode": ["P1", "P1n", "P3"] + proj, "runtime": [],
               "tool_new": ["W1"], "data_new": ["P3"] + proj, "proj_new": proj, "scripts_new": ["P1", "P3"] + proj,
               "data_del": ["P1", "P1n", "P3"] + proj, "ext_link": proj}
    seq_bad = [(t, a2, b2) for t, a2, b2 in out.get("seq", [])
               if a2 != sorted(set(exp_seq.get(t, ["?"])) | {"D1"}) or b2 != ["D1"]]
    _ok("B2-C2b 依赖闭包：工具 dashboard、selftest、MANIFEST、docs、权限、新增文件只影响工具绑定（local/ 除外）；项目文件的内容、"
        "权限、新增、删除与新增的项目外链接按 affects 或整个项目精确失效；运行数据不影响；恢复原样（原权限）后恢复有效",
        not seq_bad and len(out.get("seq", [])) == 16, json.dumps(seq_bad or out.get("seq"), ensure_ascii=False)[:1500])
    _ok("B2-C2b 坏 hash、读取失败、证据被改动不复用；反例 1：新执行时读权限失败、超出预算都写成绑定不成立并失败关闭，文件之后再变也不复用，"
        "恢复后重新检查通过",
        out["badhash"] == ["D1", "P1", "W1"] and out["readfail"] == (sorted(["D1"] + proj), True) and out["evidence"] == ["D1", "W1"]
        and out["restored"] == ["D1"] and out["perm"] == (True, True, True, True, True) and out["perm_back"] == (False, "project")
        and out["perm_real"] == (True, True)
        and out["budget"][0] == ["P1", "P1n", "P2", "P3", "U1", "W1", "X1"] and out["budget"][1] == ["D1", "P1", "P1n", "P2", "P3",
                                                                                                      "U1", "W1", "X1"]
        and out["budget_back"] == ["D1"],
        json.dumps({k: out[k] for k in ("badhash", "readfail", "evidence", "restored", "perm", "perm_back", "perm_real", "budget",
                                        "budget_back")}, ensure_ascii=False))
    _ok("B2-C2b 反例 2：显式坏 schema、列表、null、去掉 schema 的 v2 绑定、非 live 目标用全局 schema 一律拒绝；只有明确的旧格式"
        "（B1 的两键 binding、B1 之前没有 binding）按全局 goals_sha 兼容，且无关目标变化即失效",
        out["formats"] == {"bogus": True, "list": True, "null": True, "schema_removed": True, "global_on_exec": True,
                           "legacy_b1": False, "legacy_pre_b1": False} and out["legacy_global"] is True,
        json.dumps({k: out[k] for k in ("formats", "legacy_global")}, ensure_ascii=False))
    _ok("B2-C2b 依赖图：兄弟分支汇合成环被发现，菱形依赖正常；未知依赖与成环都写成绑定不成立并失败关闭",
        out["cycle"] == [True, ["A2", "B2", "C"]] and out["dep_unbound"] == (True, True, "project", True, True),
        json.dumps({k: out[k] for k in ("cycle", "dep_unbound")}, ensure_ascii=False))
    _ok("B2-C2b 运行时审计：计算出的路径读到 affects 之外时退回整个项目；读项目外、换目录、os._exit 丢失审计记录、读环境变量、"
        "运行时才出现的子进程（静态看不出）、检查期间写入项目都使绑定不成立；对非字面量路径做存在性检查时不收窄；affects 目录里指向 affects 外的链接使绑定不成立",
        out["runtime"] == {"R1": "project", "R2": "unbound", "R3": "unbound", "R4": "unbound", "R5": "project", "R6": "unbound",
                           "R8": "unbound"}
        and out["runtime_ok_flags"] == ["R2", "R3", "R4", "R6", "R8"] and "子进程" in out["runtime_why"]["R8"]
        and out["write_during"] == (True, True, "tool")
        and out["aff_link"] == (True, True) and out["aff_link_back"] == ["D1"] and out["na_on_pass"] == "refused"
        and out["failed_reuse"] == (False, "failed", True),
        json.dumps({k: out[k] for k in ("runtime", "runtime_ok_flags", "runtime_why", "write_during", "aff_link", "aff_link_back",
                                        "na_on_pass", "failed_reuse")}, ensure_ascii=False))
    exp_cl = {"ok": "python+", "dir": "python+", "none": "python-", "dotdot": "python-", "abs": "python-", "no_script": "python-",
              "link": "python-", "missing": "python-", "not_list": "python-", "unnorm": "python-", "runtime_area": "python-",
              "literal": "python-", "import_missing": "python-", "import_ok": "python+", "dynamic": "unbound", "env": "unbound",
              "stat_nonliteral": "python-", "interp_path": "unbound", "wf_tool": "tool", "wf_probes": "tool",
              "wf_ledger": "tool+project", "wf_doctor": "unbound", "cat": "project", "cat_abs": "unbound", "cat_var": "unbound",
              "cat_up": "unbound", "git": "unbound", "chain": "unbound", "other": "unbound"}
    cl_bad = {k: v for k, v in out.get("closure", {}).items() if exp_cl.get(k) != v}
    _ok("B2-C2b 静态推导：affects 只有规范化、不越界、不经符号链接、覆盖脚本、本地模块与路径字面量时才可能收窄；其余 wf 子命令、git、"
        "范围外或带变量的参数、命令链与其他命令无法绑定",
        not cl_bad and len(out.get("closure", {})) == len(exp_cl), json.dumps(cl_bad or out.get("closure"), ensure_ascii=False))
    _ok("B2-C2b progress 的 ok、layer、reached 在以上全部状态下一致", not bad_consistency,
        json.dumps(bad_consistency[:10], ensure_ascii=False))


def _t_mech_runtime_inputs(_ok):
    """FIX2：真实解释器、模块、stat 输入及 observer 透明性；仅临时 venv/脚本，不触系统文件。"""
    import contextlib
    from . import goals, policy
    from .selftest import child_env
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)

        @contextlib.contextmanager
        def pythonpath(path):
            old = os.environ.get("PYTHONPATH")
            os.environ["PYTHONPATH"] = str(path)
            try:
                yield
            finally:
                if old is None:
                    os.environ.pop("PYTHONPATH", None)
                else:
                    os.environ["PYTHONPATH"] = old

        def make(name, source, cond=None, args=""):
            a = _inst(td, name)
            (a.root / "check.py").write_text(source, encoding="utf-8")
            x = {"id": "E", "title": name, "kind": "exec", "check": "python3 check.py" + args,
                 "pass": cond or {"exit": 0, "contains": ["GOOD"]}, "means": "fixture", "on_fail": "inspect",
                 "affects": ["check.py", "input.txt"] if "input.txt" in source else ["check.py"]}
            (a.wf / "goals.json").write_text(json.dumps({"goals": [x]}), encoding="utf-8")
            policy.approve(a, "goals", "isolated FIX2 fixture", by="test")
            return a, x

        def execute(a):
            goals.run_checks(a, timeout=30, by="test")
            return json.loads((a.wf / "goals_state.json").read_text(encoding="utf-8"))["E"]

        def current(a):
            p = goals.progress(a)
            return p["items"][0]["ok"] and p["reached"]

        def direct(a, x):
            env = dict(os.environ)
            env["PATH"] = str(a.root / ".venv/bin") + os.pathsep + env.get("PATH", "")
            r = subprocess.run(["bash", "-c", x["check"]], cwd=a.root, env=env, capture_output=True, text=True, timeout=30)
            return goals.judge(x["pass"], r.returncode, r.stdout + r.stderr)[0], r.stdout + r.stderr

        def venv(a):
            env = child_env(os.environ, td / "venv-local")
            r = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(a.root / ".venv")],
                               env=env, capture_output=True, text=True, timeout=60)
            if r.returncode:
                raise RuntimeError(r.stderr[-500:])
            r = subprocess.run([str(a.root / ".venv/bin/python3"), "-c",
                                "import sysconfig; print(sysconfig.get_path('purelib'))"],
                               env=env, capture_output=True, text=True, timeout=30)
            if r.returncode:
                raise RuntimeError(r.stderr[-500:])
            return Path(r.stdout.strip())

        a, x = make("venv-module", "import fixture_dep\nprint(fixture_dep.VALUE)\n")
        lib = venv(a)
        dep = lib / "fixture_dep.py"
        dep.write_text("VALUE = 'GOOD'\n", encoding="utf-8")
        r = execute(a)
        normal = r["ok"] and current(a)
        dep.write_text("VALUE = 'BAD'\n", encoding="utf-8")
        actual, text = direct(a, x)
        _ok("C2b FIX2 实际 venv 模块按字节绑定：初始正常、模块改变后不复用，直接运行判据也失败",
            normal and not current(a) and not actual, text)

        a, x = make("venv-data", "p = '.ve' + 'nv/value.txt'\nprint(open(p).read().strip())\n")
        venv(a)
        value = a.root / ".venv/value.txt"
        value.write_text("GOOD\n", encoding="utf-8")
        r = execute(a)
        normal = r["ok"] and current(a)
        value.write_text("BAD\n", encoding="utf-8")
        actual, text = direct(a, x)
        _ok("C2b FIX2 环境目录里的任意实际数据不豁免：初始可绑定，字节变化后 pending 且直接判据失败",
            normal and not current(a) and not actual, text)

        a, x = make("interpreter", "print('GOOD')\n")
        entry = a.root / ".venv/bin/python3"
        entry.parent.mkdir(parents=True)
        import shlex
        entry.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n', encoding="utf-8")
        entry.chmod(0o755)
        r = execute(a)
        normal = r["ok"] and current(a)
        entry.write_text('#!/bin/sh\nprintf "BAD\\n"\n', encoding="utf-8")
        actual, text = direct(a, x)
        _ok("C2b FIX2 命令实际解析的解释器入口绑定：同路径入口改字节不复用，未改系统解释器",
            normal and not current(a) and not actual, text)

        flag = td / "external-flag"
        a, x = make("external-stat", "import os, sys\nprint('GOOD' if not os.path.exists(sys.argv[1]) else 'BAD')\n",
                    args=" " + shlex.quote(str(flag)))
        r = execute(a)
        normal = r["ok"] and current(a)
        flag.write_text("present\n", encoding="utf-8")
        actual, text = direct(a, x)
        _ok("C2b FIX2 非字面量外部 exists 按实际路径绑定：初始正常，外部文件创建后失效，不能用整项目冒充闭包",
            normal and not current(a) and not actual, text)

        reuse = []
        for name, source in (("plain", "print(open('input.txt').read().strip())\n"),
                             ("pathlib", "from pathlib import Path\nprint(Path('input.txt').read_text().strip())\n")):
            a, x = make(name, source, {"exit": 0, "contains": ["GOOD"], "not_contains": ["Error in sitecustomize"]})
            (a.root / "input.txt").write_text("GOOD\n", encoding="utf-8")
            actual, text = direct(a, x)
            r = execute(a)
            (a.root / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
            reuse.append(actual and r["ok"] and r["binding"].get("kind") == "affects" and current(a))
        _ok("C2b FIX2 observer bootstrap/dump 不污染普通 open 与 pathlib 的 affects：无关文件变化仍可复用，输出判据与直接执行一致",
            all(reuse), repr(reuse))

        a, x = make("site-error", "print('GOOD')\n",
                    {"exit": 0, "contains": ["GOOD"], "not_contains": ["missing-required-component"]})
        (a.root / "sitecustomize.py").write_text("raise ImportError('missing-required-component')\n", encoding="utf-8")
        with pythonpath(a.root):
            actual, text = direct(a, x)
            r = execute(a)
            evidence = (a.root / r["evidence"]).read_text(encoding="utf-8")
        _ok("C2b FIX2 原 sitecustomize 内部 ImportError 不吞掉、不替换为 KeyError：真实输出与判据仍失败",
            not actual and not r["ok"] and "missing-required-component" in evidence and "KeyError" not in evidence,
            text + evidence)

        a, x = make("site-normal", "print('GOOD')\n")
        source = a.root / "site-input.txt"
        source.write_text("GOOD\n", encoding="utf-8")
        (a.root / "sitecustomize.py").write_text("print(open('site-input.txt').read().strip())\n", encoding="utf-8")
        with pythonpath(a.root):
            actual, text = direct(a, x)
            r = execute(a)
            normal = r["ok"] and current(a)
            source.write_text("BAD\n", encoding="utf-8")
            stale = not current(a)
        _ok("C2b FIX2 正常 sitecustomize 仍执行，实际读取的项目文件变化使旧结果失效",
            actual and normal and stale and text.count("GOOD") == 2, text)

        library = td / "external-library"
        library.mkdir()
        (library / "fixture_shadow.py").write_text("VALUE = 'GOOD'\n", encoding="utf-8")
        a, x = make("import-shadow", "import fixture_shadow\nprint(fixture_shadow.VALUE)\n")
        with pythonpath(library):
            r = execute(a)
            normal = r["ok"] and current(a)
            (a.root / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
            reuse = current(a)
            (a.root / "fixture_shadow.py").write_text("VALUE = 'BAD'\n", encoding="utf-8")
            actual, text = direct(a, x)
            stale = not current(a)
        _ok("C2b FIX2 模块搜索绑定：外部实际模块正常绑定，无关文件可复用，新建同名遮蔽模块后失效",
            normal and reuse and stale and not actual, text)

        a, x = make("missing-import", "try:\n import fixture_missing\nexcept ImportError:\n print('GOOD')\nelse:\n print('BAD')\n")
        r = execute(a)
        normal = r["ok"] and current(a)
        (a.root / "fixture_missing.py").write_text("VALUE = 1\n", encoding="utf-8")
        actual, text = direct(a, x)
        _ok("C2b FIX2 失败导入也绑定搜索候选：原先缺失的模块新增后失效且直接运行判据失败",
            normal and not current(a) and not actual, text)


def _t_mech_startup_inputs(_ok):
    """FIX3：仅临时 venv/site/中央缓存；不改宿主解释器配置。"""
    from . import goals, policy
    from .selftest import child_env
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)

        def make(name, source):
            a = _inst(td, name)
            (a.root / "check.py").write_text(source, encoding="utf-8")
            x = {"id": "E", "title": name, "kind": "exec", "check": "python3 check.py",
                 "pass": {"exit": 0, "contains": ["GOOD"]}, "means": "fixture", "on_fail": "inspect",
                 "affects": ["check.py"]}
            (a.wf / "goals.json").write_text(json.dumps({"goals": [x]}), encoding="utf-8")
            policy.approve(a, "goals", "isolated FIX3 fixture", by="test")
            env = child_env(os.environ, td / (name + "-local"))
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(a.root / ".venv")],
                           env=env, check=True, capture_output=True, text=True, timeout=60)
            py = a.root / ".venv/bin/python3"
            lib = Path(subprocess.check_output([str(py), "-c", "import site; print(site.getsitepackages()[0])"],
                                              env=env, text=True, timeout=30).strip())
            lib.mkdir(parents=True, exist_ok=True)
            return a, x, py, lib

        def current(a):
            return goals.progress(a)["reached"]

        def execute(a):
            goals.run_checks(a, timeout=30, by="test")
            return current(a)

        def direct(a, x, py):
            r = subprocess.run([str(py), "check.py"], cwd=a.root, env=dict(os.environ),
                               capture_output=True, text=True, timeout=30)
            return goals.judge(x["pass"], r.returncode, r.stdout + r.stderr)[0]

        good, bad = td / "good", td / "bad"
        for directory, value in ((good, "GOOD"), (bad, "BAD")):
            directory.mkdir()
            (directory / "fixture_pth.py").write_text("VALUE = " + repr(value) + "\n", encoding="utf-8")
        a, x, py, lib = make("pth-switch", "import fixture_pth\nprint(fixture_pth.VALUE)\n")
        pth = lib / "fixture.pth"
        pth.write_text(str(good) + "\n", encoding="utf-8")
        normal = execute(a)
        (lib / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
        reuse = current(a)
        pth.write_text(str(bad) + "\n", encoding="utf-8")
        _ok("C2b FIX3 真实 .pth 路径切换：初始可复用，无关 site 文件不影响，GOOD→BAD 后旧结果失效且直接判据失败",
            normal and reuse and not current(a) and not direct(a, x, py))

        source = "try:\n import fixture_pth\nexcept ImportError:\n print('GOOD')\nelse:\n print('BAD')\n"
        a, x, py, lib = make("pth-added", source)
        normal = execute(a)
        (lib / "added.pth").write_text(str(good) + "\n", encoding="utf-8")
        _ok("C2b FIX3 site 的 .pth 候选集合绑定：新增配置不能复用旧缺失导入的 GOOD",
            normal and not current(a) and not direct(a, x, py))

        a, x, py, lib = make("pth-missing-path", source)
        missing = td / "missing-pth-target"
        (lib / "missing.pth").write_text(str(missing) + "\n", encoding="utf-8")
        normal = execute(a)
        missing.mkdir()
        (missing / "fixture_pth.py").write_text("VALUE = 'BAD'\n", encoding="utf-8")
        _ok("C2b FIX3 .pth 指向的缺失路径也是启动输入：目录和模块新增后失效且直接判据失败",
            normal and not current(a) and not direct(a, x, py))

        a, x, py, lib = make("venv-config", "print('GOOD')\n")
        normal = execute(a)
        cfg = a.root / ".venv/pyvenv.cfg"
        cfg.write_text(cfg.read_text(encoding="utf-8") + "\n# changed configuration\n", encoding="utf-8")
        _ok("C2b FIX3 pyvenv.cfg 按实际配置字节绑定，修改后旧机械结果失效",
            normal and not current(a))

        a, x, py, lib = make("central-cache", "print('GOOD')\n")
        old = os.environ.get("PYTHONPYCACHEPREFIX")
        os.environ["PYTHONPYCACHEPREFIX"] = str(td / "central-cache")
        try:
            normal = execute(a)
            rec = json.loads((a.wf / "goals_state.json").read_text(encoding="utf-8"))["E"]
            files = (rec["binding"].get("runtime") or {}).get("files", [])
            owned = [r["path"] for r in files if "/wf-goal-audit-" in r["path"]]
            _ok("C2b FIX3 中央 pycache 下 observer 自有缓存与源码不入闭包，删除临时 audit 后普通脚本仍有效",
                normal and not owned and direct(a, x, py), repr(owned))
        finally:
            if old is None:
                os.environ.pop("PYTHONPYCACHEPREFIX", None)
            else:
                os.environ["PYTHONPYCACHEPREFIX"] = old

        a, x, py, lib = make("pth-code", "print('GOOD')\n")
        (lib / "code.pth").write_text("import os\n", encoding="utf-8")
        reached = execute(a)
        rec = json.loads((a.wf / "goals_state.json").read_text(encoding="utf-8"))["E"]
        _ok("C2b FIX3 观察前 .pth 执行代码暂失败关闭，不把未观察输入冒充完整闭包",
            rec["ok"] and not reached and ".pth" in rec["binding"].get("unbound", ""))


def _t_mech_disabled_pth(_ok):
    """FIX4：环境变体只作用于临时夹具；产品不改环境或原检查命令。"""
    import contextlib
    from . import goals, policy
    from .selftest import child_env
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        flag = td / "external-startup-input"
        flag.write_text("GOOD\n", encoding="utf-8")

        def template(default):
            return ("import os; var = 'SETUPTOOLS_USE_DISTUTILS'; enabled = os.environ.get(var, '"
                    + default + "') == 'local'; enabled and __import__('_distutils_hack').add_shim();")

        @contextlib.contextmanager
        def environment(**values):
            old = {k: os.environ.get(k) for k in values}
            try:
                for k, v in values.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
                yield
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

        def make(name, text):
            a = _inst(td, name)
            (a.root / "check.py").write_text("print('GOOD')\n", encoding="utf-8")
            x = {"id": "E", "title": name, "kind": "exec", "check": "python3 check.py",
                 "pass": {"exit": 0, "contains": ["GOOD"], "not_contains": ["BAD", "Error in sitecustomize"]},
                 "means": "fixture", "on_fail": "inspect", "affects": ["check.py"]}
            (a.wf / "goals.json").write_text(json.dumps({"goals": [x]}), encoding="utf-8")
            policy.approve(a, "goals", "isolated FIX4 fixture", by="test")
            env = child_env(os.environ, td / (name + "-local"))
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(a.root / ".venv")],
                           env=env, check=True, capture_output=True, text=True, timeout=60)
            py = a.root / ".venv/bin/python3"
            lib = Path(subprocess.check_output([str(py), "-c", "import site; print(site.getsitepackages()[0])"],
                                              env=env, text=True, timeout=30).strip())
            lib.mkdir(parents=True, exist_ok=True)
            (lib / "_distutils_hack.py").write_text(
                "def add_shim():\n print('SHIM ' + open(" + repr(str(flag)) + ").read().strip())\n", encoding="utf-8")
            pth = lib / "distutils-precedence.pth"
            pth.write_text(text, encoding="utf-8")
            return a, x, py, pth

        def current(a):
            return goals.progress(a)["reached"]

        def execute(a):
            goals.run_checks(a, timeout=30, by="test")
            rec = json.loads((a.wf / "goals_state.json").read_text(encoding="utf-8"))["E"]
            return rec, current(a), (a.root / rec["evidence"]).read_text(encoding="utf-8").split("\n", 2)[2]

        def direct(a, x, py):
            r = subprocess.run([str(py), "check.py"], cwd=a.root, env=dict(os.environ),
                               capture_output=True, text=True, timeout=30)
            text = r.stdout + r.stderr
            return goals.judge(x["pass"], r.returncode, text)[0], text

        for default, values in (("stdlib", (None, "stdlib", "", "other")), ("local", ("stdlib", "", "other"))):
            results = []
            for i, value in enumerate(values):
                with environment(SETUPTOOLS_USE_DISTUTILS=value):
                    a, x, py, pth = make("disabled-" + default + str(i), template(default) + " \t\r\n")
                    rec, reached, text = execute(a)
                    actual, output = direct(a, x, py)
                    results.append(rec["ok"] and reached and actual and text == output and "SHIM" not in output)
            _ok("C2b FIX4 完整 " + default + " 模板仅在真实条件为 False 时复用，缺失／空串不混淆，原输出不变",
                all(results), repr(results))

        with environment(SETUPTOOLS_USE_DISTUTILS=None):
            a, x, py, pth = make("env-change", template("stdlib") + "\n")
            rec, reached, text = execute(a)
            normal = rec["ok"] and reached
            with environment(SETUPTOOLS_USE_DISTUTILS="local"):
                stale = not current(a)
                rec, reached, text = execute(a)
                flag.write_text("BAD\n", encoding="utf-8")
                actual, output = direct(a, x, py)
                active = rec["ok"] and not current(a) and "未禁用" in rec["binding"].get("unbound", "")
                flag.write_text("GOOD\n", encoding="utf-8")
            _ok("C2b FIX4 真实环境改为 local 使旧记录失效，启用 shim 后新记录 unbound；外部启动输入确实能改变直接判据",
                normal and stale and active and not actual and "SHIM BAD" in output)

            a, x, py, pth = make("local-default", template("local") + "\n")
            rec, reached, text = execute(a)
            _ok("C2b FIX4 local 模板变量缺失时确实启用，不因文件名或模板已知而放行，原因可读",
                rec["ok"] and not current(a) and "SHIM GOOD" in text
                and "观察前输入无法证明" in rec["binding"].get("unbound", ""))

            for name, suffix in (("tail", " print(open(" + repr(str(flag)) + ").read());\n"),
                                 ("second-line", "\nimport os; print(open(" + repr(str(flag)) + ").read())\n"),
                                 ("comment-tail", " # only a comment\n"),
                                 ("assignment-tail", " enabled = False;\n")):
                a, x, py, pth = make(name, template("stdlib") + "\n")
                rec, reached, text = execute(a)
                normal = rec["ok"] and reached
                pth.write_text(template("stdlib") + suffix, encoding="utf-8")
                stale = not current(a)
                rec, reached, text = execute(a)
                actual_input = True
                if name in ("tail", "second-line"):
                    flag.write_text("BAD\n", encoding="utf-8")
                    actual, output = direct(a, x, py)
                    actual_input = not actual and "BAD" in output and not current(a)
                    flag.write_text("GOOD\n", encoding="utf-8")
                _ok("C2b FIX4 完整匹配边界 " + name + "：先有合法通过，改尾部／第二行后失效且重新检查不放行",
                    normal and stale and rec["ok"] and not current(a) and actual_input
                    and "未知执行代码" in rec["binding"].get("unbound", ""))

            a, x, py, pth = make("unknown-later-file", template("stdlib") + "\n")
            rec, reached, text = execute(a)
            normal = rec["ok"] and reached
            (pth.parent / "zz-unknown.pth").write_text("import\tos\n", encoding="utf-8")
            stale = not current(a)
            rec, reached, text = execute(a)
            _ok("C2b FIX4 必须扫描全部 .pth 和全部执行行，合法首文件不能遮住后续未知 import",
                normal and stale and rec["ok"] and not current(a)
                and "zz-unknown.pth" in rec["binding"].get("unbound", ""))

            a, x, py, pth = make("standalone-comment", "# standalone\n" + template("stdlib") + "\n# after\n")
            rec, reached, text = execute(a)
            normal = rec["ok"] and reached
            with environment(SETUPTOOLS_USE_DISTUTILS=""):
                stale = not current(a)
                rec, reached, text = execute(a)
                rebound = rec["ok"] and reached
            _ok("C2b FIX4 独立注释按 site 语义忽略；环境从缺失变为空串虽同为禁用，旧证据仍失效且可重新绑定",
                normal and stale and rebound)

            a, x, py, pth = make("unused-shim", template("stdlib") + "\n")
            rec, reached, text = execute(a)
            (pth.parent / "_distutils_hack.py").write_text("raise RuntimeError('UNEXPECTED SHIM IMPORT')\n", encoding="utf-8")
            actual, output = direct(a, x, py)
            _ok("C2b FIX4 禁用右支不主动导入或绑定 shim，未执行模块变化后仍可复用且原检查正常",
                rec["ok"] and reached and current(a) and actual and text == output == "GOOD\n")

            a, x, py, pth = make("pth-link-mode", template("stdlib") + "\n")
            rec, reached, text = execute(a)
            normal = rec["ok"] and reached
            mode = pth.stat().st_mode & 0o777
            pth.chmod(mode ^ 0o100)
            mode_stale = not current(a)
            pth.chmod(mode)
            restored = current(a)
            target = td / "same-template.pth"
            target.write_text(pth.read_text(encoding="utf-8"), encoding="utf-8")
            pth.unlink()
            pth.symlink_to(target)
            link_stale = not current(a)
            rec, reached, text = execute(a)
            _ok("C2b FIX4 认可 .pth 的权限与链接真实去处仍是输入，同字节换链接也失效，重新检查可绑定",
                normal and mode_stale and restored and link_stale and rec["ok"] and reached)

            a, x, py, pth = make("central-original-site", template("stdlib") + "\n")
            source = a.root / "site-input.txt"
            source.write_text("GOOD\n", encoding="utf-8")
            (a.root / "sitecustomize.py").write_text("print('SITE ' + open('site-input.txt').read().strip())\n",
                                                    encoding="utf-8")
            with environment(PYTHONPYCACHEPREFIX=str(td / "central"), PYTHONPATH=str(a.root)):
                actual, output = direct(a, x, py)
                rec, reached, text = execute(a)
                normal = rec["ok"] and reached
                files = (rec["binding"].get("runtime") or {}).get("files", [])
                owned = [r["path"] for r in files if "/wf-goal-audit-" in r["path"]]
                source.write_text("BAD\n", encoding="utf-8")
                stale = not current(a)
            _ok("C2b FIX4 禁用模板与中央 pycache 共存，observer 自有文件仍排除，原 sitecustomize 的输出和真实输入保留",
                normal and actual and text == output and "SITE GOOD" in output and not owned and stale,
                repr({"normal": normal, "unbound": rec["binding"].get("unbound"), "actual": actual, "same": text == output, "output": output,
                      "owned": owned, "stale": stale}))

            a, x, py, pth = make("nonstandard-environ",
                                "import os; os.environ = dict(os.environ)\n" + template("stdlib") + "\n")
            rec, reached, text = execute(a)
            _ok("C2b FIX4 无法确认标准 os.environ 前提时失败关闭，不套用模板证明",
                rec["ok"] and not current(a) and "CPython/site/os" in rec["binding"].get("unbound", ""))


TESTS = [("证据分层兼容", _t_compat), ("不适用四项", _t_na), ("实景测量结构", _t_live_struct), ("verified 绑定", _t_verified),
         ("工具摘要规则", _t_manifest_rule), ("目标骨架 C1", _t_default_goals), ("指标 C3", _t_metrics),
         ("指标真实 API C3", _t_metrics_real_api),
         ("指标时间与身份 C3", _t_metrics_time_identity), ("指标只读 C3", _t_metrics_readonly),
         ("目标空集保护 C2a", _t_goals_empty_guard), ("逐目标机械绑定 C2b", _t_mech_v2),
         ("运行输入与 observer C2b FIX2", _t_mech_runtime_inputs),
         ("site 启动与中央缓存 C2b FIX3", _t_mech_startup_inputs),
         ("禁用 distutils 模板 C2b FIX4", _t_mech_disabled_pth)]


def run_lane_c(_ok) -> None:
    """聚合器入口：调用方负责隔离（它自己的子进程、临时 local/、WF_NOTIFY=0）。一组出错只记这一组失败。"""
    for name, fn in TESTS:
        try:
            fn(_ok)
        except Exception as e:   # noqa: BLE001
            _ok(f"B1-C 检查组「{name}」运行出错", False, f"{type(e).__name__}: {e}")


def _child_main() -> int:
    res = []

    def _ok(n, cond, detail=""):
        res.append(bool(cond))
        print(f"{'✓' if cond else '✗'} {n}" + ("" if cond else f"\n    {str(detail)[:1500]}"), flush=True)
        return cond
    run_lane_c(_ok)
    print(f"B1-C 车道自检：{sum(res)} / {len(res)} 通过", flush=True)
    return 0 if res and all(res) else 1


def main() -> int:
    from .selftest import CHILD_FLAG, child_env
    if os.environ.get(CHILD_FLAG) == "1":
        return _child_main()
    from .core import WF_HOME
    tmp = Path(tempfile.mkdtemp(prefix="wf-lane-c-"))
    try:
        r = subprocess.run([sys.executable, "-m", "wflib.selftest_lane_c"], cwd=str(WF_HOME),
                           env=child_env(os.environ, tmp / "local"), timeout=600)
        return r.returncode
    except subprocess.TimeoutExpired:
        print("B1-C 车道自检子进程超过 600 秒没有结束，按未通过处理", flush=True)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
