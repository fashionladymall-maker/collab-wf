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
        # F1、S1 的失败记录来自改动前的目标（goals_sha 已变），layer 按原规则是 pending；
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
        and lay1["F1"] == ("pending", False) and lay1["S1"] == ("pending", False) and "不能算不适用" in f1why
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


TESTS = [("证据分层兼容", _t_compat), ("不适用四项", _t_na), ("实景测量结构", _t_live_struct), ("verified 绑定", _t_verified),
         ("工具摘要规则", _t_manifest_rule), ("目标骨架 C1", _t_default_goals)]


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
