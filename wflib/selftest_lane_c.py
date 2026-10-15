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


TESTS = [("证据分层兼容", _t_compat), ("不适用四项", _t_na), ("实景测量结构", _t_live_struct), ("verified 绑定", _t_verified),
         ("工具摘要规则", _t_manifest_rule), ("目标骨架 C1", _t_default_goals), ("指标 C3", _t_metrics),
         ("指标真实 API C3", _t_metrics_real_api),
         ("指标时间与身份 C3", _t_metrics_time_identity), ("指标只读 C3", _t_metrics_readonly),
         ("目标空集保护 C2a", _t_goals_empty_guard)]


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
