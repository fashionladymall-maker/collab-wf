"""B1-A（发送槽精确持有者）的模块测试：arbiter 的 token 接口，以及 driver 在授予、保存、外发、释放、收养各处的边界。

给既有自检聚合器调用：run_lane_a(_ok)，_ok(name, cond, detail="") 与 selftest._ok 相同。
单独运行：python3 -m wflib.selftest_lane_a。父进程只用 selftest.child_env 生成环境、启动隔离子进程（临时 local/、
WF_NOTIFY=0），自己不改任何环境变量、不读 local/；子进程里的读写都在临时 local/ 与临时项目目录中，适配器调用全部换成替身。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path


def _guard() -> str:
    """只在隔离的 local/ 里运行：WF_LOCAL_DIR 必须显式设置，且不能指向工具自己的 local/。"""
    from .core import LOCAL, WF_HOME
    if not os.environ.get("WF_LOCAL_DIR"):
        return "没有设置 WF_LOCAL_DIR，拒绝在真实 local/ 上运行"
    if Path(LOCAL).resolve() == (WF_HOME / "local").resolve():
        return "WF_LOCAL_DIR 指向工具自己的 local/，拒绝运行"
    return ""


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


def _sendable(td, name, turn=1, extra=None):
    inst = _inst(td, name, {"state": "SEND", "turn": turn, **(extra or {})})
    f = inst.outbox / f"turn-{turn:03d}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"【回报 · 第 {turn} 轮 · v0.1】档位：high\n\n正文\n", encoding="utf-8")
    inst.state["outgoing"] = {"turn": turn, "file": str(f), "kind": "report", "effort": "high"}
    inst.save()
    return inst


def _shared() -> Path:
    from .core import LOCAL
    return LOCAL / "shared"


def _fresh_shared() -> Path:
    d = _shared()
    for n in ("arbiter.json", "arbiter.jsonl", ".arbiter.json.tmp"):
        if (d / n).exists():
            (d / n).unlink()
    return d


def _arb() -> dict:
    p = _shared() / "arbiter.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _alog() -> list:
    p = _shared() / "arbiter.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def _holders(name=None) -> list:
    return [h for h in _arb().get("holders", []) if name is None or h.get("project") == name]


def _disk(inst) -> dict:
    return json.loads(inst.state_path.read_text(encoding="utf-8"))


def _rules(inst, prefix="SLOT_") -> list:
    if not inst.events_path.exists():
        return []
    evs = [json.loads(x) for x in inst.events_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [e.get("rule") for e in evs if e.get("type") == "auto" and str(e.get("rule", "")).startswith(prefix)]


def _iso(delta_s: int) -> str:
    from .core import now
    return (now() + timedelta(seconds=delta_s)).isoformat(timespec="seconds")


class _Fake:
    """适配器替身：记录调用的动作，按动作返回预设结果（列表依次取用，取完后返回空成功）。"""

    def __init__(self, **responses):
        self.calls, self.responses = [], responses

    def __call__(self, op, args, timeout=None, env_extra=None):
        self.calls.append(op)
        r = self.responses.get(op)
        if callable(r):
            r = r(op, args)
        if isinstance(r, list):
            r = r.pop(0) if r else None
        return r or {"code": 0, "raw": "", "data": {}}


@contextmanager
def _patched(call, cfg=None):
    from . import anomaly
    oc, ot = anomaly.call, anomaly.transport_cfg
    anomaly.call, anomaly.transport_cfg = call, (lambda: dict(cfg or {}))
    try:
        yield call
    finally:
        anomaly.call, anomaly.transport_cfg = oc, ot


SENT_OK = {"code": 0, "raw": "", "data": {"ref": "R1", "accepted": True}}
POLL_NONE = {"code": 0, "raw": "", "data": {"status": "none"}}


def _ctx(inst, pol):
    from . import core, driver, policy
    # 旧直调夹具以前只给内存策略；P2 真正消费批准文件。每项目只初始化一次，负例破坏后不修复。
    marker = inst.wf / ".test-policy-initialized"
    if not marker.exists():
        if _guard():
            raise RuntimeError(_guard())
        if not (inst.wf / "policy.json").exists():
            (inst.wf / "policy.json").write_text(json.dumps(pol), encoding="utf-8")
            policy.approve(inst, "policy", "lane-a direct-handler fixture")
        marker.touch()
    registry_key = str(core.REGISTRY)
    seen = getattr(_ctx, "_registries", set())
    if registry_key not in seen:
        if not core.REGISTRY.exists():
            core.REGISTRY.parent.mkdir(parents=True, exist_ok=True)
            core.REGISTRY.write_text('{"projects": []}', encoding="utf-8")
        seen.add(registry_key)
        _ctx._registries = seen
    return driver.Ctx(inst, pol, time.time() + 60)


@contextmanager
def _permission_fixture(inst, pol):
    """为直接调用 review/execute 的旧夹具建立一次真实批准策略和严格可读 registry。"""
    from . import core, policy
    inst.wf.mkdir(parents=True, exist_ok=True)
    (inst.wf / "policy.json").write_text(json.dumps(pol, ensure_ascii=False), encoding="utf-8")
    policy.approve(inst, "policy", "lane-a permission fixture")
    previous = core.REGISTRY
    core.REGISTRY = inst.wf / "test-registry.json"
    core.REGISTRY.write_text(json.dumps({"projects": []}), encoding="utf-8")
    try:
        yield
    finally:
        core.REGISTRY = previous


def _adapter_log(td, rows, end_newline=True, name="bridge.log.jsonl") -> Path:
    p = Path(td) / name
    text = "\n".join(r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in rows)
    p.write_text(text + ("\n" if end_newline and rows else ""), encoding="utf-8")
    return p


def _old_log(td):
    # 一份从授予之前就在写的适配器日志，只有别的项目的记录：本项目本轮没有外发
    return _adapter_log(td, [{"ts": _iso(-3600), "action": "send", "project": "other", "turn": 9},
                             {"ts": _iso(-3590), "action": "done", "project": "other", "turn": 9}])


# ---------------- arbiter：token 接口 ----------------

def _t_grant_release(_ok):
    from . import arbiter
    _fresh_shared()
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "la-gr", {"state": "SEND", "turn": 1})
        g1 = arbiter.acquire(a, 1)
        g2 = arbiter.acquire(a, 1)
        r1 = arbiter.release(a, "测试", lease_id=g1["lease_id"], turn=1)
        g3 = arbiter.acquire(a, 1)
        r2 = arbiter.release(a, "测试", lease_id=g3["lease_id"])   # turn 不给时取当前状态的轮次
        log = _alog()
    tok = g1.get("lease_id") or ""
    grants = [x for x in log if x.get("event") == "grant"]
    rels = [x for x in log if x.get("event") == "release"]
    ok = (g1["granted"] and len(tok) == 16 and all(c in "0123456789abcdef" for c in tok) and g1["attempt"] == 1
          and g1["granted_at"] and g1["reentry"] is False and g1["legacy"] is False
          and g1["premark"] is True and g1["send_marked_at"] is None
          and g2["granted"] and g2["lease_id"] == tok and g2["attempt"] == 1 and g2["reentry"] is True
          and r1 == {"released": True, "reason": "released"}
          and g3["lease_id"] not in (None, tok) and g3["attempt"] == 2 and r2["released"]
          and [x.get("lease_id") for x in grants] == [tok, g3["lease_id"]]
          and [x.get("lease_id") for x in rels] == [tok, g3["lease_id"]] and [x.get("attempt") for x in rels] == [1, 2]
          and _holders() == [])
    _ok("B1-A 同轮授予与精确释放：新授予给唯一 token 与 attempt，重入返回同一 token，带 token 释放只返回 released/reason，"
        "同轮再授予是新 token、attempt 加一，grant 与 release 日志按 token 成对",
        ok, json.dumps([g1, g2, r1, g3, r2, log], ensure_ascii=False)[:1500])


def _t_fifo_reclaim(_ok):
    from . import arbiter
    from .core import Instance
    _fresh_shared()
    peak = []
    with tempfile.TemporaryDirectory() as td:
        a, b, c = (_inst(td, f"la-q{x}", {"state": "SEND", "turn": 1}) for x in "abc")
        g1, g2, g3 = arbiter.acquire(a, 1), arbiter.acquire(b, 1), arbiter.acquire(c, 1)
        peak.append(len(_holders()))
        r = arbiter.release(a, "测试", lease_id=g1["lease_id"], turn=1)
        g4, g5 = arbiter.acquire(c, 1), arbiter.acquire(b, 1)
        peak.append(len(_holders()))
        bb = Instance(b.root)
        bb.state["state"] = "IDLE"
        bb.save()
        g6 = arbiter.acquire(c, 1)
        peak.append(len(_holders()))
        log1 = _alog()
        reclaimed = [x for x in log1 if x.get("event") == "reclaim"]
        # 两个槽：先到的两个拿到，第三个排队；租约到期的持有方被回收
        _fresh_shared()
        d, e, f = (_inst(td, f"la-q{x}", {"state": "SEND", "turn": 1}) for x in "def")
        h1, h2, h3 = arbiter.acquire(d, 2), arbiter.acquire(e, 2), arbiter.acquire(f, 2)
        peak.append(len(_holders()))
        s = _arb()
        for h in s["holders"]:
            if h["project"] == d.name:
                h["since"] = "2000-01-01T00:00:00+00:00"
        (_shared() / "arbiter.json").write_text(json.dumps(s), encoding="utf-8")
        h4 = arbiter.acquire(f, 2, lease_s=60)
        peak.append(len(_holders()))
        log = _alog()
    ok = (g1["granted"] and not g2["granted"] and g2["position"] == 1 and not g3["granted"] and g3["position"] == 2
          and r["released"] and not g4["granted"] and g5["granted"] and g6["granted"] and g6["lease_id"] != g5["lease_id"]
          and any(x.get("lease_id") == g5["lease_id"] for x in reclaimed)
          and h1["granted"] and h2["granted"] and not h3["granted"] and h4["granted"]
          and any(x.get("event") == "reclaim" and x.get("lease_id") == h1["lease_id"] for x in log)
          and max(peak[:3]) <= 1 and max(peak[3:]) <= 2
          and all(any(x.get("event") == ev for x in log1) for ev in ("wait", "grant", "release", "reclaim")))
    _ok("B1-A 仲裁行为不变：先到先得、队首优先、不插队、持有方离开发送或租约到期被回收（日志带被回收的 token），"
        "任何时刻持有数不超过上限",
        ok, json.dumps([g1, g2, g3, r, g4, g5, g6, h1, h2, h3, h4, peak], ensure_ascii=False)[:1500])


def _t_wrong_identity(_ok):
    from . import arbiter
    from .core import Instance
    _fresh_shared()
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "la-wa", {"state": "SEND", "turn": 5})
        b = _inst(td, "la-wb", {"state": "SEND", "turn": 5})
        c = _inst(td, "la-wc", {"state": "SEND", "turn": 1})
        ga = arbiter.acquire(a, 1)
        t = ga["lease_id"]
        res = {"错轮": arbiter.release(a, "测试", lease_id=t, turn=4),
               "错 token": arbiter.release(a, "测试", lease_id="0" * 16, turn=5),
               "无 token 遇新记录": arbiter.release(a, "测试"),
               "别的项目拿同一 token": arbiter.release(b, "测试", lease_id=t, turn=5)}
        kept1 = [h.get("lease_id") for h in _holders(a.name)]
        aa = Instance(a.root)
        aa.state["state"] = "IDLE"
        aa.save()
        gc = arbiter.acquire(c, 1)          # a 已不在发送：被回收，c 拿到
        res["回收后迟到"] = arbiter.release(a, "测试", lease_id=t, turn=5)
        kept2 = [h.get("lease_id") for h in _holders(c.name)]
        aa.state["state"] = "SEND"
        aa.save()
        ga2 = arbiter.acquire(a, 2)         # 同轮再拿到一次：新 token
        res["同轮新授予遇旧 token"] = arbiter.release(a, "测试", lease_id=t, turn=5)
        kept3 = [h.get("lease_id") for h in _holders(a.name)]
        res["新 token 释放"] = arbiter.release(a, "测试", lease_id=ga2["lease_id"], turn=5)
        misses = [x for x in _alog() if x.get("event") == "release_miss"]
    want = {"错轮": "turn_mismatch", "错 token": "token_mismatch", "无 token 遇新记录": "token_required",
            "别的项目拿同一 token": "not_held", "回收后迟到": "not_held", "同轮新授予遇旧 token": "token_mismatch"}
    ok = (all(res[k] == {"released": False, "reason": v} for k, v in want.items())
          and kept1 == [t] and gc["granted"] and kept2 == [gc["lease_id"]]
          and ga2["granted"] and ga2["lease_id"] != t and ga2["attempt"] == 2 and kept3 == [ga2["lease_id"]]
          and res["新 token 释放"]["released"] and len(misses) == len(want)
          and all(m.get("reason") for m in misses))
    _ok("B1-A 错轮、错 token、别的项目、回收后迟到、同轮新授予遇旧 token、无 token 调用遇新记录：一律不释放，"
        "返回原因并记 release_miss，被保护的新记录原样保留",
        ok, json.dumps([res, kept1, kept2, kept3, misses], ensure_ascii=False)[:1500])


def _t_legacy(_ok):
    from . import arbiter
    from .core import now_iso
    _fresh_shared()
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "la-la", {"state": "SEND", "turn": 2})
        b = _inst(td, "la-lb", {"state": "SEND", "turn": 1})
        since = now_iso()

        def put():
            (_shared() / "arbiter.json").write_text(json.dumps(
                {"holders": [{"project": a.name, "turn": 2, "root": str(a.root), "since": since}], "queue": []}),
                encoding="utf-8")
        put()
        gb = arbiter.acquire(b, 1)                               # 旧记录照样计入占用
        ga = arbiter.acquire(a, 1)                               # 重入旧记录：不自行补发 token
        after_ga = _holders(a.name)
        r_turn = arbiter.release(a, "测试", turn=1)              # 无 token 调用：轮次不对不释放
        r_tok = arbiter.release(a, "测试", lease_id="f" * 16, turn=2)   # 带 token 调用不能释放无 token 的旧记录
        r_old = arbiter.release(a, "测试")                       # 无 token 调用释放无 token 旧记录
        put()
        gm = arbiter.acquire(a, 1, migrate_legacy=True)         # 调用方已证明没有外发：补发 token
        after_gm = _holders(a.name)
        r_new = arbiter.release(a, "测试")                       # 迁移后是新记录：无 token 调用不能释放
        r_ok = arbiter.release(a, "测试", lease_id=gm["lease_id"], turn=2)
        migrated = [x for x in _alog() if x.get("event") == "migrate"]
    ok = (not gb["granted"] and ga["granted"] and ga["legacy"] is True and ga["lease_id"] is None and ga["reentry"]
          and ga["granted_at"] == since and len(after_ga) == 1 and "lease_id" not in after_ga[0]
          and r_turn == {"released": False, "reason": "turn_mismatch"}
          and r_tok == {"released": False, "reason": "legacy_holder"}
          and r_old == {"released": True, "reason": "released_legacy"}
          and gm["granted"] and gm["legacy"] is False and len(gm["lease_id"] or "") == 16 and gm["attempt"] == 1
          and gm["granted_at"] == since and after_gm[0].get("since") == since and after_gm[0].get("lease_id") == gm["lease_id"]
          and r_new == {"released": False, "reason": "token_required"} and r_ok["released"]
          and len(migrated) == 1 and migrated[0].get("lease_id") == gm["lease_id"])
    _ok("B1-A 旧记录迁移：无 token 的旧记录计入占用；重入时不自行补发 token；只有 migrate_legacy 才补发并保留原授予时刻；"
        "无 token 调用只释放无 token 旧记录，迁移后的新记录必须带 token 释放",
        ok, json.dumps([gb, ga, after_ga, r_turn, r_tok, r_old, gm, after_gm, r_new, r_ok], ensure_ascii=False)[:1500])


def _t_bad_file(_ok):
    from . import arbiter
    res = []
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "la-bf", {"state": "SEND", "turn": 1})
        for raw in (b"{oops", b"", b"[]", b'{"holders": "x", "queue": []}', b'{"holders": [{"turn": 1}], "queue": []}',
                    b"\xff\xfe"):
            _fresh_shared()
            p = _shared() / "arbiter.json"
            p.write_bytes(raw)
            g = arbiter.acquire(a, 1)
            r = arbiter.release(a, "测试", lease_id="a" * 16, turn=1)
            r0 = arbiter.release(a, "测试")
            m = arbiter.mark_sending(a, "a" * 16, 1)
            res.append((g["granted"], bool(g.get("error")), g.get("lease_id"), r, r0, p.read_bytes() == raw,
                        (_shared() / "arbiter.jsonl").exists(), m))
        _fresh_shared()
        g_missing = arbiter.acquire(a, 1)                        # 文件不存在：还没人拿过槽，照常授予
    bad = {"released": False, "reason": "arbiter_unreadable"}
    ok = (all(x[0] is False and x[1] and x[2] is None and x[3] == bad and x[4] == bad and x[5] and not x[6]
              and x[7] == {"marked": False, "reason": "arbiter_unreadable"} for x in res)
          and g_missing["granted"] and g_missing["lease_id"])
    _ok("B1-A 坏文件与缺文件：arbiter.json 不可读或结构不对时不授予、不释放、不改写也不记日志；文件不存在照常授予",
        ok, json.dumps([str(x) for x in res] + [g_missing], ensure_ascii=False)[:1500])


# ---------------- driver：授予 → 保存 → 外发 → 释放 ----------------

def _t_send_order_and_landing(_ok):
    from . import driver
    _fresh_shared()
    pol = {"shared_max_inflight": 1}
    seen = []
    with tempfile.TemporaryDirectory() as td:
        a = _sendable(td, "la-so", 1)

        def on_send(op, args):
            seen.append((_disk(a).get("send_slot"), (_disk(a).get("send_intent") or {}).get("status"), _holders(a.name)))
            return SENT_OK
        reply = Path(td) / "reply.md"
        reply.write_text("回复正文\n", encoding="utf-8")
        fake = _Fake(send=on_send, poll=[{"code": 0, "raw": "", "data": {"status": "done", "reply_file": str(reply),
                                                                          "files": []}}])
        with _patched(fake):
            r1 = driver.h_send(_ctx(a, pol))
            slot_wait = dict(_disk(a).get("send_slot") or {})
            state_wait = _disk(a).get("state")
            a.state.pop("poll_after", None)
            a.save()
            r2 = driver.h_wait_reply(_ctx(a, pol))
        st = _disk(a)
        log = _alog()
        rules = _rules(a)
    slot0, intent0, hold0 = seen[0] if seen else ({}, None, [])
    tok = (slot0 or {}).get("lease_id")
    ok = (fake.calls.count("send") == 1 and tok and intent0 == "pending" and [h.get("lease_id") for h in hold0] == [tok]
          and set(slot0) == {"lease_id", "turn", "attempt", "granted_at"} and slot0["turn"] == 1 and slot0["attempt"] == 1
          and hold0[0].get("send_marked_at") and hold0[0].get("send_marks") == 1
          and r1[0] == "progress" and state_wait == "WAIT_REPLY" and slot_wait.get("lease_id") == tok
          and r2[0] == "progress" and st.get("state") == "INTAKE" and "send_slot" not in st and _holders() == []
          and any(x.get("event") == "release" and x.get("lease_id") == tok and x.get("why") == "reply_landed" for x in log)
          and rules == [])
    _ok("B1-A 授予 → 保存 → 外发前标记 → 外发：调用适配器时磁盘上的 send_slot 与发送意图已落盘、与仲裁记录同一 token，且该 token 已有标记；"
        "send_slot 贯穿等回复，回复落地按 token 精确释放并清掉",
        ok, json.dumps([seen, r1, slot_wait, r2, st.get("state"), log, rules, fake.calls], ensure_ascii=False)[:1500])


def _t_failed_and_unknown(_ok):
    from . import driver
    pol = {"shared_max_inflight": 1, "send_unknown_wait": True}
    with tempfile.TemporaryDirectory() as td:
        _fresh_shared()
        a = _sendable(td, "la-fb", 1)
        fake = _Fake(send=[{"code": 2, "raw": "busy", "data": {}}])
        with _patched(fake):
            rb = driver.h_send(_ctx(a, pol))
        st_b, log_b, rules_b = _disk(a), _alog(), _rules(a)
        _fresh_shared()
        u = _sendable(td, "la-fu", 1)
        fake2 = _Fake(send=[{"code": 4, "raw": "timeout", "data": {}}], poll=[POLL_NONE])
        with _patched(fake2):
            ru1 = driver.h_send(_ctx(u, pol))
            tok = (_disk(u).get("send_slot") or {}).get("lease_id")
            u.state.pop("retry_at", None)
            u.save()
            ru2 = driver.h_send(_ctx(u, pol))   # 结果不明：只轮询核对，不重发
        st_u = _disk(u)
        hold_u = [h.get("lease_id") for h in _holders(u.name)]
    ok = (rb[0] == "wait" and "send_slot" not in st_b and _holders(a.name) == [] and st_b.get("send_intent") is None
          and any(x.get("event") == "release" and x.get("why") == "send_failed" for x in log_b) and rules_b == []
          and ru1[0] == "wait" and ru2[0] == "wait" and tok and fake2.calls.count("send") == 1 and "poll" in fake2.calls
          and (st_u.get("send_slot") or {}).get("lease_id") == tok and hold_u == [tok]
          and (st_u.get("send_intent") or {}).get("status") == "pending")
    _ok("B1-A 发送明确失败按 token 释放并清掉 send_slot；结果不明保留 send_slot 与持有记录，下一次只轮询核对、不重发",
        ok, json.dumps([rb, ru1, ru2, tok, hold_u, fake2.calls, st_u.get("send_slot")], ensure_ascii=False)[:1500])


def _t_zero_slots(_ok):
    from . import arbiter, driver
    with tempfile.TemporaryDirectory() as td:
        _fresh_shared()
        a = _sendable(td, "la-z0", 1)
        reply = Path(td) / "reply.md"
        reply.write_text("回复正文\n", encoding="utf-8")
        fake = _Fake(send=[SENT_OK], poll=[{"code": 0, "raw": "", "data": {"status": "done", "reply_file": str(reply),
                                                                             "files": []}}])
        with _patched(fake):
            r1 = driver.h_send(_ctx(a, {}))
            a.state.pop("poll_after", None)
            a.save()
            r2 = driver.h_wait_reply(_ctx(a, {"shared_max_inflight": 0}))
        untouched = not (_shared() / "arbiter.json").exists() and not (_shared() / "arbiter.jsonl").exists()
        st0 = _disk(a)
        # 仲裁改为关闭前留下的 send_slot：外发前按 token 退回
        b = _sendable(td, "la-z1", 1)
        g = arbiter.acquire(b, 1)
        b.state["send_slot"] = {"lease_id": g["lease_id"], "turn": 1, "attempt": g["attempt"], "granted_at": g["granted_at"]}
        b.save()
        fake2 = _Fake(send=[SENT_OK])
        with _patched(fake2):
            r3 = driver.h_send(_ctx(b, {"shared_max_inflight": 0}))
        st1, log1 = _disk(b), _alog()
    ok = (r1[0] == "progress" and r2[0] == "progress" and fake.calls.count("send") == 1 and untouched
          and "send_slot" not in st0 and st0.get("state") == "INTAKE" and _rules(a) == []
          and r3[0] == "progress" and fake2.calls.count("send") == 1 and "send_slot" not in st1 and _holders(b.name) == []
          and any(x.get("event") == "release" and x.get("why") == "arbitration_disabled" and x.get("lease_id") == g["lease_id"]
                  for x in log1))
    _ok("B1-A 0 槽配置：不拿槽、不碰仲裁文件、照常发送与收回复；仲裁关闭前留下的 send_slot 在外发前按 token 退回",
        ok, json.dumps([r1, r2, untouched, r3, fake.calls, fake2.calls, log1], ensure_ascii=False)[:1500])


def _t_lost_and_missing(_ok):
    from . import arbiter, driver
    pol = {"shared_max_inflight": 1}
    reply_done = lambda p: {"code": 0, "raw": "", "data": {"status": "done", "reply_file": str(p), "files": []}}   # noqa: E731
    with tempfile.TemporaryDirectory() as td:
        reply = Path(td) / "reply.md"
        reply.write_text("回复正文\n", encoding="utf-8")
        # 等回复期间租约到期、被别的项目回收：回复落地时释放未命中，记 SLOT_LEASE_LOST 与偏离，状态照常往下走
        _fresh_shared()
        a = _inst(td, "la-ll", {"state": "SEND", "turn": 3})
        g = arbiter.acquire(a, 1)
        a.state.update(state="WAIT_REPLY", send_ref="R1",
                       send_slot={"lease_id": g["lease_id"], "turn": 3, "attempt": g["attempt"], "granted_at": g["granted_at"]})
        a.save()
        s = _arb()
        s["holders"][0]["since"] = "2000-01-01T00:00:00+00:00"
        (_shared() / "arbiter.json").write_text(json.dumps(s), encoding="utf-8")
        b = _inst(td, "la-lb2", {"state": "SEND", "turn": 1})
        gb = arbiter.acquire(b, 1, lease_s=60)
        with _patched(_Fake(poll=[reply_done(reply)])):
            r1 = driver.h_wait_reply(_ctx(a, pol))
        st_a, rules_a = _disk(a), _rules(a)
        notes = (a.turn_dir(3) / "notes.jsonl").read_text(encoding="utf-8") if (a.turn_dir(3) / "notes.jsonl").exists() else ""
        hold_b = [h.get("lease_id") for h in _holders(b.name)]
        # 等回复时状态里没有 send_slot、仲裁里本项目的记录带 token：不借无 token 调用释放，记 SLOT_TOKEN_MISSING
        _fresh_shared()
        c = _inst(td, "la-lc", {"state": "SEND", "turn": 2})
        gc = arbiter.acquire(c, 1)
        c.state.update(state="WAIT_REPLY", send_ref="R2")
        c.save()
        gen = {"code": 0, "raw": "", "data": {"status": "generating"}}
        with _patched(_Fake(poll=[gen, reply_done(reply)])):
            r2a = driver.h_wait_reply(_ctx(c, pol))   # r2：还在生成时就要记缺 token，不等到落地
            rules_c0 = _rules(c)
            c.state.pop("poll_after", None)
            c.save()
            r2 = driver.h_wait_reply(_ctx(c, pol))
        st_c, rules_c = _disk(c), _rules(c)
        hold_c = [h.get("lease_id") for h in _holders(c.name)]
    ok = (gb["granted"] and r1[0] == "progress" and st_a.get("state") == "INTAKE" and "send_slot" not in st_a
          and rules_a == ["SLOT_LEASE_LOST"] and "SLOT_LEASE_LOST" in notes and hold_b == [gb["lease_id"]]
          and r2a[0] == "wait" and rules_c0 == ["SLOT_TOKEN_MISSING"]
          and r2[0] == "progress" and st_c.get("state") == "INTAKE" and rules_c == ["SLOT_TOKEN_MISSING"]
          and hold_c == [gc["lease_id"]])
    _ok("B1-A 回复落地时：租约已被回收记 SLOT_LEASE_LOST 与偏离、不碰新持有者、状态照常；缺 token 在等回复时即记 "
        "SLOT_TOKEN_MISSING（每轮一次），落地时不借无 token 调用释放带 token 的记录",
        ok, json.dumps([r1, rules_a, hold_b, r2, rules_c, hold_c], ensure_ascii=False)[:1500])


def _t_legacy_no_adopt(_ok):
    from . import driver
    pol = {"shared_max_inflight": 1}
    with tempfile.TemporaryDirectory() as td:
        _fresh_shared()
        logp = _old_log(td)
        c = _sendable(td, "la-lg", 3)
        (_shared() / "arbiter.json").write_text(json.dumps(
            {"holders": [{"project": c.name, "turn": 3, "root": str(c.root), "since": _iso(0)}], "queue": []}), encoding="utf-8")
        fc = _Fake(send=[SENT_OK])
        with _patched(fc, {"log": str(logp)}):
            rc = driver.h_send(_ctx(c, pol))
        hc = _holders(c.name)
        sc, rules = _disk(c), _rules(c)
    ok = (rc[0] == "user" and fc.calls.count("send") == 0 and len(hc) == 1 and "lease_id" not in hc[0]
          and "send_slot" not in sc and (sc.get("send_intent") or {}).get("origin") == "slot_token_missing"
          and rules == ["SLOT_TOKEN_MISSING"])
    _ok("B1-A r2 旧版无 token 记录没有外发前标记：即使有一份干净的适配器日志也不收养、不补发 token、不发送，记 SLOT_TOKEN_MISSING",
        ok, json.dumps([rc, fc.calls, hc, sc.get("send_intent"), rules], ensure_ascii=False)[:1500])


_CRASH = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
from wflib import anomaly, arbiter, driver
from wflib.core import Instance
root, point, sent = sys.argv[2], sys.argv[3], sys.argv[4]


def call(op, args, timeout=None, env_extra=None):
    if op == "send":
        with open(sent, "a") as f:
            f.write("send\n")
        return {"code": 0, "raw": "", "data": {"ref": "R1", "accepted": True}}
    return {"code": 0, "raw": "", "data": {}}


anomaly.call, anomaly.transport_cfg = call, (lambda: {})
inst = Instance(root)
if point == "71":       # 授予之后、第一次保存状态之前
    inst.save = lambda: os._exit(71)
elif point == "72":     # 状态已保存、外发前标记之前
    arbiter.mark_sending = lambda *a, **k: os._exit(72)
else:                   # 外发前标记已落盘、调用适配器之前
    real = arbiter.mark_sending

    def after(*a, **k):
        real(*a, **k)
        os._exit(73)
    arbiter.mark_sending = after
driver.h_send(driver.Ctx(inst, {"shared_max_inflight": 1}, time.time() + 60))
os._exit(0)
"""


def _t_crash_points(_ok):
    from . import driver
    from .core import Instance, WF_HOME
    pol = {"shared_max_inflight": 1, "send_unknown_wait": True}
    out = {}
    with tempfile.TemporaryDirectory() as td:
        for point in ("71", "72", "73"):
            _fresh_shared()
            a = _sendable(td, f"la-cp{point}", 3)
            _ctx(a, pol)   # 子进程断点前先完成真实批准夹具，不让初始化写入成为假断点
            sent = Path(td) / f"sent-{point}.txt"
            rc = subprocess.run([sys.executable, "-c", _CRASH, str(WF_HOME), str(a.root), point, str(sent)],
                                env=dict(os.environ), cwd=str(WF_HOME), capture_output=True, timeout=120).returncode
            hold = _holders(a.name)
            tok = hold[0].get("lease_id") if hold else None
            marked0 = bool(hold and hold[0].get("send_marked_at"))
            b = Instance(a.root)   # 重启：从磁盘重新读
            fake = _Fake(send=[SENT_OK], poll=[POLL_NONE])
            with _patched(fake):
                r = driver.h_send(_ctx(b, pol))
            st, hold2 = _disk(b), _holders(a.name)
            out[point] = [rc, sent.exists(), marked0, r[0], fake.calls.count("send"), fake.calls.count("poll"),
                          bool(tok) and (st.get("send_slot") or {}).get("lease_id") == tok,
                          [h.get("lease_id") for h in hold2] == [tok], bool(hold2 and hold2[0].get("send_marked_at")), _rules(b)]
    ok = (out.get("71") == [71, False, False, "progress", 1, 0, True, True, True, ["SLOT_ADOPTED"]]
          and out.get("72") == [72, False, False, "wait", 0, 1, True, True, False, []]
          and out.get("73") == [73, False, True, "wait", 0, 1, True, True, True, []])
    _ok("B1-A r2 真实 os._exit 崩溃断点：授予后、保存前崩溃，租约没有外发前标记，重启收养同一 token 并只发一次；"
        "保存后或标记后崩溃，重启只按结果不明核对、不发送",
        ok, json.dumps(out, ensure_ascii=False)[:1500])


def _t_adopt_proof_table(_ok):
    from . import driver
    from .core import now_iso
    res = {}
    G = {"lease_id": "c" * 16, "premark": True, "send_marked_at": None, "legacy": False}
    with tempfile.TemporaryDirectory() as td:
        since = now_iso()
        cases = [
            ("对照：可以证明", G, {}, None, True),
            ("对照：上一轮的 send_ref", G, {"send_ref": "R0", "last_sent": {"turn": 2}}, None, True),
            ("旧版无 token 记录", {"lease_id": None, "legacy": True}, {}, None, False),
            ("不是按外发前标记协议授予", dict(G, premark=False), {}, None, False),
            ("租约写过外发前标记", dict(G, send_marked_at=since), {}, None, False),
            ("本轮已有发送意图", G, {"send_intent": {"turn": 3, "status": "pending"}}, None, False),
            ("本轮 last_sent", G, {"last_sent": {"turn": 3}, "send_ref": "R3"}, None, False),
            ("send_ref 无法归属", G, {"send_ref": "R?"}, None, False),
            ("已有另一个 send_slot", G, {"send_slot": {"lease_id": "b" * 16, "turn": 3}}, None, False),
            ("turns.jsonl 本轮已发送", G, {}, json.dumps({"turn": 3, "ts_sent": since, "status": "sent"}), False),
            ("turns.jsonl 有坏行", G, {}, "{坏行", False),
            ("不在发送状态", G, {"state": "WAIT_REPLY"}, None, False),
        ]
        for i, (name, g, extra, turns_line, want) in enumerate(cases):
            inst = _sendable(td, f"la-pt{i}", 3, extra)
            if turns_line:
                inst.turns_path.write_text(turns_line + "\n", encoding="utf-8")
            with _patched(_Fake(), {}):
                got = driver._slot_adoptable(inst, 3, g)
            res[name] = [got[0], want, got[1]]
    ok = all(v[0] is v[1] and v[2] for v in res.values())
    _ok("B1-A r2 收养证明只认租约自身的外发前标记：旧版记录、非 premark 租约、已标记租约、本轮意图或发送记录、"
        "无法归属的 send_ref、另一个 send_slot、turns.jsonl 已发送或损坏、不在发送状态，都不算证明",
        ok, json.dumps(res, ensure_ascii=False)[:1500])


def _t_unknown_no_adopt(_ok):
    from . import arbiter, driver
    from .core import Instance
    pol = {"shared_max_inflight": 1, "send_unknown_wait": True}
    with tempfile.TemporaryDirectory() as td:
        _fresh_shared()
        a = _sendable(td, "la-un", 4)
        message = driver._prepare_send(_ctx(a, pol), a.state["outgoing"])
        g = arbiter.acquire(a, 1)
        a.state["send_intent"] = {"turn": 4, "status": "pending", "ts": g["granted_at"],
                                  "file": a.state["outgoing"]["file"], "message": message}
        a.save()
        fake = _Fake(poll=[POLL_NONE, POLL_NONE, POLL_NONE], send=[SENT_OK])
        rs = []
        with _patched(fake):
            for _ in range(3):   # 每次重新打开 Instance，像三次独立唤醒
                b = Instance(a.root)
                b.state.pop("retry_at", None)
                b.save()
                rs.append(driver.h_send(_ctx(b, pol))[0])
        st = _disk(a)
        hold = [h.get("lease_id") for h in _holders(a.name)]
        notes = (a.turn_dir(4) / "notes.jsonl").read_text(encoding="utf-8")
        rules, unknown = _rules(a), _rules(a, "SEND_UNKNOWN")
    ok = (rs == ["wait"] * 3 and fake.calls.count("send") == 0 and fake.calls.count("poll") == 3
          and "send_slot" not in st and hold == [g["lease_id"]] and rules == ["SLOT_TOKEN_MISSING"]
          and unknown == ["SEND_UNKNOWN_WAIT"] * 3 and notes.count("[SLOT_TOKEN_MISSING]") == 1
          and (st.get("send_intent") or {}).get("status") == "pending")
    _ok("B1-A r2 结果不明且缺 token（BLOCK-3）：三次独立唤醒只轮询核对，不发送、不收养、不借用，SLOT_TOKEN_MISSING 与偏离每轮只记一次",
        ok, json.dumps([rs, fake.calls, hold, rules, unknown, st.get("send_slot")], ensure_ascii=False)[:1500])


def _t_no_borrow(_ok):
    from . import arbiter, driver
    pol = {"shared_max_inflight": 1, "send_unknown_wait": True}
    out = {}
    with tempfile.TemporaryDirectory() as td:
        reply = Path(td) / "reply.md"
        reply.write_text("回复正文\n", encoding="utf-8")
        for key in ("L1 被 L2 替代", "缺 send_slot 且租约已标记"):
            _fresh_shared()
            a = _sendable(td, f"la-nb{len(out)}", 2)
            g1 = arbiter.acquire(a, 1)
            if key == "L1 被 L2 替代":
                a.state["send_slot"] = {"lease_id": g1["lease_id"], "turn": 2, "attempt": g1["attempt"], "granted_at": g1["granted_at"]}
                a.save()
                arbiter.release(a, "测试", lease_id=g1["lease_id"], turn=2)
                live, mine = arbiter.acquire(a, 1)["lease_id"], g1["lease_id"]   # 同项目同轮的新授予 L2
            else:
                arbiter.mark_sending(a, g1["lease_id"], 2)                       # 这个租约下可能已经外发
                live, mine = g1["lease_id"], None
            message = driver._prepare_send(_ctx(a, pol), a.state["outgoing"])
            a.state["send_intent"] = {"turn": 2, "status": "accepted", "ts": g1["granted_at"],
                                      "file": a.state["outgoing"]["file"], "message": message}
            a.save()
            fake = _Fake(send=[SENT_OK], poll=[{"code": 0, "raw": "", "data": {"status": "done", "ref": "R9"}},
                                               {"code": 0, "raw": "", "data": {"status": "done", "reply_file": str(reply),
                                                                                "files": []}}])
            with _patched(fake):
                r1 = driver.h_send(_ctx(a, pol))
                slot1 = (_disk(a).get("send_slot") or {}).get("lease_id")
                a.state.pop("retry_at", None)
                a.save()
                r2 = driver.h_send(_ctx(a, pol))         # 结果不明的核对：轮询确认已被接受
                a.state.pop("poll_after", None)
                a.save()
                r3 = driver.h_wait_reply(_ctx(a, pol))   # 回复落地
            log = _alog()
            out[key] = {"r": [r1[0], r2[0], r3[0]], "state": _disk(a).get("state"), "send": fake.calls.count("send"),
                        "slot_kept": slot1 == mine, "holder": [h.get("lease_id") for h in _holders(a.name)] == [live],
                        "released_live": any(x.get("event") == "release" and x.get("lease_id") == live for x in log),
                        "rules": _rules(a)}
    ok = (len(out) == 2 and all(v["r"] == ["wait", "progress", "progress"] and v["state"] == "INTAKE" and v["send"] == 0
                                and v["slot_kept"] and v["holder"] and not v["released_live"] for v in out.values())
          and out["L1 被 L2 替代"]["rules"] == ["SLOT_TOKEN_MISSING", "SLOT_LEASE_LOST"]
          and out["缺 send_slot 且租约已标记"]["rules"] == ["SLOT_TOKEN_MISSING"])
    _ok("B1-A r2 不借 token（A-R1/BLOCK-2）：不能收养时不把仲裁里的 token 写进 send_slot；结果不明 → 轮询确认 → 回复落地，"
        "新持有记录保留、没有针对它的 release，旧 token 释放未命中记 SLOT_LEASE_LOST，缺 token 每轮只记一次",
        ok, json.dumps(out, ensure_ascii=False)[:1500])


def _t_mark_fail(_ok):
    from . import arbiter, driver
    pol = {"shared_max_inflight": 1}
    out = {}
    with tempfile.TemporaryDirectory() as td:
        _fresh_shared()
        a = _sendable(td, "la-mf", 1)
        real, stolen = a.save, []

        def save_then_reclaim():   # 状态已保存、标记之前，槽被回收
            real()
            if (a.state.get("send_intent") or {}).get("status") == "pending" and not stolen:
                s = _arb()
                s["holders"] = [h for h in s["holders"] if h.get("project") != a.name]
                (_shared() / "arbiter.json").write_text(json.dumps(s), encoding="utf-8")
                stolen.append(1)
        a.save = save_then_reclaim
        fake = _Fake(send=[SENT_OK])
        try:
            with _patched(fake):
                r = driver.h_send(_ctx(a, pol))
        finally:
            del a.save
        st = _disk(a)
        out["回收"] = [r[0], fake.calls.count("send"), "send_slot" in st, st.get("send_intent"), _rules(a)]
        _fresh_shared()
        b = _sendable(td, "la-mf2", 1)
        orig = arbiter._fsync_path

        def enospc(path):   # 标记写盘失败
            raise OSError(28, "No space left on device（测试替身）")
        arbiter._fsync_path = enospc
        fake2 = _Fake(send=[SENT_OK])
        try:
            with _patched(fake2):
                r2 = driver.h_send(_ctx(b, pol))
        finally:
            arbiter._fsync_path = orig
        st2, hb = _disk(b), _holders(b.name)
        out["写盘失败"] = [r2[0], fake2.calls.count("send"),
                       bool(hb) and (st2.get("send_slot") or {}).get("lease_id") == hb[0].get("lease_id"),
                       bool(hb and hb[0].get("send_marked_at")), st2.get("send_intent"), _rules(b)]
    ok = (out["回收"] == ["wait", 0, False, None, ["SLOT_LEASE_LOST"]]
          and out["写盘失败"] == ["wait", 0, True, False, None, []])
    _ok("B1-A r2 外发前标记写不成就不外发：标记前槽被回收，记 SLOT_LEASE_LOST 并清掉没发出的意图；标记写盘失败，保留 send_slot、不外发",
        ok, json.dumps(out, ensure_ascii=False)[:1500])


def _t_log_irrelevant(_ok):
    import builtins
    import errno
    from adapters.base import Adapter
    from . import arbiter, driver
    pol = {"shared_max_inflight": 1}
    with tempfile.TemporaryDirectory() as td:
        _fresh_shared()
        logp = _old_log(td)
        before, target, real_open = logp.read_bytes(), str(logp), builtins.open

        def enospc(file, mode="r", *args, **kwargs):
            if str(file) == target and "a" in mode:
                raise OSError(errno.ENOSPC, "No space left on device（测试替身）")
            return real_open(file, mode, *args, **kwargs)
        builtins.open = enospc
        try:
            dropped = Adapter({}, target).blog("send", project="la-li", turn=3)   # 适配器记账静默失败，旧日志仍然合法
        finally:
            builtins.open = real_open
        silent = dropped is None and logp.read_bytes() == before
        a = _sendable(td, "la-li", 3)
        outgoing = dict(a.state["outgoing"])
        fake = _Fake(send=[SENT_OK, SENT_OK])
        with _patched(fake, {"log": target}):
            r1 = driver.h_send(_ctx(a, pol))   # 真实外发路径：先持久标记，再调用适配器
        st = a.state                           # 模拟状态回退：本轮的发送记录全部丢失
        for k in ("send_slot", "send_intent", "last_sent", "send_ref"):
            st.pop(k, None)
        st.update(state="SEND", outgoing=outgoing)
        a.save()
        if a.turns_path.exists():
            a.turns_path.unlink()
        g = arbiter.acquire(a, 1)
        with _patched(_Fake(), {"log": target}):
            with_log = driver._slot_adoptable(a, 3, g)
        with _patched(_Fake(), {}):
            without_log = driver._slot_adoptable(a, 3, g)
        with _patched(fake, {"log": target}):
            r2 = driver.h_send(_ctx(a, pol))
        st2, rules = _disk(a), _rules(a)
    ok = (silent and r1[0] == "progress" and g.get("send_marked_at") and with_log[0] is False and without_log == with_log
          and r2[0] == "user" and fake.calls.count("send") == 1 and "send_slot" not in st2 and rules == ["SLOT_TOKEN_MISSING"])
    _ok("B1-A r2 适配器日志不作证据（A-R2/BLOCK-1）：日志追加静默失败后文件仍合法，但收养判定与日志无关；"
        "租约已有外发前标记时，状态回退也不收养、不重发",
        ok, json.dumps([silent, r1, with_log, without_log, r2, fake.calls, rules], ensure_ascii=False)[:1500])


def _t_save_window(_ok):
    from . import driver
    pol = {"shared_max_inflight": 1}
    err = None
    with tempfile.TemporaryDirectory() as td:
        _fresh_shared()
        a = _sendable(td, "la-sw", 1)
        ctx = _ctx(a, pol)
        fake = _Fake(send=[SENT_OK])

        def boom():
            raise OSError("磁盘写满（测试替身）")
        a.save = boom
        try:
            with _patched(fake):
                driver.h_send(ctx)
        except OSError as e:
            err = str(e)
        finally:
            del a.save
        log = _alog()
        grant = [x for x in log if x.get("event") == "grant"]
        rel = [x for x in log if x.get("event") == "release"]
    ok = (err and fake.calls.count("send") == 0 and _holders() == [] and len(grant) == 1 and len(rel) == 1
          and rel[0].get("lease_id") == grant[0].get("lease_id") and rel[0].get("why") == "save_failed")
    _ok("B1-A 保存窗口：新授予后状态落盘失败，不调用适配器，按同一 token 退回授予，错误照常抛出",
        ok, json.dumps([err, fake.calls, log], ensure_ascii=False)[:1500])


def _t_unreadable_in_driver(_ok):
    from . import driver
    pol = {"shared_max_inflight": 1}
    with tempfile.TemporaryDirectory() as td:
        _fresh_shared()
        p = _shared() / "arbiter.json"
        p.write_bytes(b"{oops")
        a = _sendable(td, "la-ur", 1)
        fake = _Fake(send=[SENT_OK])
        with _patched(fake):
            r1 = driver.h_send(_ctx(a, pol))
            a.state.pop("retry_at", None)
            a.save()
            r2 = driver.h_send(_ctx(a, pol))
        st = _disk(a)
        same = p.read_bytes() == b"{oops"
        rules = _rules(a, "ARBITER_")
    ok = (r1[0] == "wait" and r2[0] == "wait" and fake.calls.count("send") == 0 and same and "send_slot" not in st
          and st.get("send_intent") is None and rules == ["ARBITER_UNREADABLE"])
    _ok("B1-A 仲裁文件不可读时驱动器不发送、不改写文件，每轮只记一次 ARBITER_UNREADABLE",
        ok, json.dumps([r1, r2, fake.calls, rules], ensure_ascii=False)[:1500])


# ---------------- B2-A1：待办的原子代次 ----------------

def _snap(inst):
    sp, ep = inst.state_path, inst.events_path
    return (sp.read_bytes() if sp.exists() else b"", ep.read_bytes() if ep.exists() else b"")


def _rej(inst, fn) -> str:
    # 被拒绝时状态文件与事件流都必须一字不变
    from .core import WfError
    before = _snap(inst)
    try:
        fn()
    except WfError:
        return "unchanged" if _snap(inst) == before else "CHANGED"
    return "ACCEPTED"


def _events(inst, type_=None) -> list:
    if not inst.events_path.exists():
        return []
    evs = [json.loads(x) for x in inst.events_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [e for e in evs if type_ is None or e.get("type") == type_]


def _t_a1_identity(_ok):
    from . import tasks
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "la-a1i", {"state": "VERIFY", "turn": 5})
        added = tasks.add(a, 5, {"1": "改同步程序", "2": "核对日志"})
        g1, g2 = added[0]["generation"], added[1]["generation"]
        res = {"缺代次": _rej(a, lambda: tasks.done(a, "T5-1", "结果")),
               "legacy 写法碰新记录": _rej(a, lambda: tasks.done(a, "T5-1", "结果", generation="legacy")),
               "别的待办的代次": _rej(a, lambda: tasks.done(a, "T5-1", "结果", generation=g2)),
               "rev 不符": _rej(a, lambda: tasks.done(a, "T5-1", "结果", generation=g1, expect_rev=9))}
        p = tasks.progress(a, "T5-1", "做到一半", generation=g1, expect_rev=1)
        d = tasks.done(a, "t5-1", "好", generation=g1, expect_rev=2)   # 编号大小写照旧；单字结果照旧可用
        res.update({"done 后再 done": _rej(a, lambda: tasks.done(a, "T5-1", "再来", generation=g1)),
                    "done 后检查点": _rej(a, lambda: tasks.progress(a, "T5-1", "再改", generation=g1)),
                    "done 后取消": _rej(a, lambda: tasks.close(a, "T5-1", "cancelled", "不要了", generation=g1))})
        s = tasks.close(a, "T5-1", "superseded", "由新待办接替", by_task="T5-2", by_generation=g2, generation=g1)   # B2-FIX-A1：接替者已按代次登记，须写明其代次
        res["已取代后迟到 done"] = _rej(a, lambda: tasks.done(a, "T5-1", "迟到", generation=g1))
        q = tasks.progress(a, "T5-2", "第一步", generation=g2)
    ok = (all(len(x["generation"]) == 32 and x["project"] == a.name and x["rev"] == 1 for x in added) and g1 != g2
          and all(v == "unchanged" for v in res.values())
          and p["rev"] == 2 and tasks.status_of(p) == "open" and d["rev"] == 3 and d["note"] == "好"
          and s["status"] == "superseded" and s["was_done"]["note"] == "好" and s["superseded_by"] == "T5-2"
          and s["superseded_by_generation"] == g2 and s["rev"] == 4 and tasks.status_of(q) == "open")
    _ok("B2-A1 待办身份是（项目, 编号, 代次）：新记录带不复用的代次；缺代次、写 legacy、用别的待办的代次、rev 不符一律拒绝且不写；"
        "progress 永不完成；done 后只能 supersede 并保留 was_done；已取代的不能迟到 done",
        ok, json.dumps([res, p, d, s, q], ensure_ascii=False)[:1500])


def _t_a1_interleave(_ok):
    from . import tasks
    from .core import Instance
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "la-a1x", {"state": "VERIFY", "turn": 2})
        x = Instance(a.root)
        _ = x.state                                    # x 先读进缓存，之后不再重读
        y = Instance(a.root)
        ya = tasks.add(y, 2, {"1": "甲", "2": "乙"})
        xa = tasks.add(x, 3, {"1": "丙"})               # x 拿旧缓存登记另一轮：不能抹掉 y 登记的两项
        tasks.done(y, "T2-1", "甲完成", generation=ya[0]["generation"])
        tasks.progress(x, "T3-1", "丙做到一半", generation=xa[0]["generation"])   # x 改另一项：不能回退 y 的完成
        tasks.close(y, "T2-2", "cancelled", "不再需要", generation=ya[1]["generation"])
        final = {t["id"]: t for t in Instance(a.root).state["executor_tasks"]}
    ok = (set(final) == {"T2-1", "T2-2", "T3-1"} and tasks.status_of(final["T2-1"]) == "done"
          and tasks.status_of(final["T2-2"]) == "cancelled" and tasks.status_of(final["T3-1"]) == "open"
          and len(final["T3-1"].get("checkpoints") or []) == 1)
    _ok("B2-A1 两个实例在不同待办上交错 add / done / progress / cancel：每一次都在锁内按磁盘最新合并，各自的修改都保留",
        ok, json.dumps(final, ensure_ascii=False)[:1500])


def _t_a1_stale_cas(_ok):
    from . import tasks
    from .core import Instance
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "la-a1s", {"state": "VERIFY", "turn": 4})
        g1, g2 = [t["generation"] for t in tasks.add(a, 4, {"1": "甲", "2": "乙"})]
        x = Instance(a.root)
        _ = x.state                                    # x 的缓存里两项都未完成、rev=1
        y = Instance(a.root)
        tasks.done(y, "T4-1", "y 先完成", generation=g1)
        tasks.progress(y, "T4-2", "y 记了检查点", generation=g2)
        res = {"旧缓存再记完成": _rej(x, lambda: tasks.done(x, "T4-1", "x 也完成", generation=g1)),
               "旧缓存的 rev": _rej(x, lambda: tasks.done(x, "T4-2", "x 完成", generation=g2, expect_rev=1)),
               "旧缓存取消已完成": _rej(x, lambda: tasks.close(x, "T4-1", "cancelled", "x 取消", generation=g1))}
        xd = tasks.done(x, "T4-2", "x 按最新 rev 完成", generation=g2, expect_rev=2)
        done_ids = [e.get("id") for e in _events(a, "task_done")]
    ok = all(v == "unchanged" for v in res.values()) and xd["rev"] == 3 and done_ids == ["T4-1", "T4-2"]
    _ok("B2-A1 旧缓存的操作在锁内对最新记录校验：已完成的不能再完成或取消、旧 rev 被拒绝，拒绝时不写状态、不记完成事件",
        ok, json.dumps([res, xd, done_ids], ensure_ascii=False)[:1500])


def _t_a1_aba(_ok):
    from . import tasks
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "la-a1k", {"state": "VERIFY", "turn": 5})
        g1 = tasks.add(a, 5, {"1": "旧任务"})[0]["generation"]
        tasks.done(a, "T5-1", "做完", generation=g1)
        for t in tasks.add(a, 6, {str(i): f"批量 {i}" for i in range(1, tasks.KEEP_DONE + 2)}):
            tasks.close(a, t["id"], "cancelled", "测试裁剪", generation=t["generation"])
        tasks.add(a, 7, {"1": "触发裁剪"})
        trimmed = all(t["id"] != "T5-1" for t in _disk(a)["executor_tasks"])
        g2 = tasks.add(a, 5, {"1": "同编号的新任务"})[0]["generation"]
        res = {"旧代次": _rej(a, lambda: tasks.done(a, "T5-1", "旧命令", generation=g1)),
               "不带代次": _rej(a, lambda: tasks.done(a, "T5-1", "旧命令")),
               "legacy": _rej(a, lambda: tasks.progress(a, "T5-1", "旧命令", generation="legacy"))}
        n = tasks.progress(a, "T5-1", "新任务第一步", generation=g2)
    ok = trimmed and g2 != g1 and all(v == "unchanged" for v in res.values()) and n["rev"] == 2 and n["generation"] == g2
    _ok("B2-A1 KEEP_DONE 裁掉旧记录后同编号重新登记：新记录换新代次，旧代次、不带代次、legacy 的命令都不能命中它",
        ok, json.dumps([trimmed, res, n.get("rev")], ensure_ascii=False)[:1500])


def _t_a1_legacy(_ok):
    from . import tasks
    from .core import now_iso
    with tempfile.TemporaryDirectory() as td:
        ts = now_iso()
        a = _inst(td, "la-a1l", {"state": "VERIFY", "turn": 4, "executor_tasks": [
            {"id": "T4-1", "turn": 4, "text": "旧甲", "since": ts, "done": None, "note": ""},
            {"id": "T4-2", "turn": 4, "text": "旧乙", "since": ts, "done": None, "note": "", "depends_on": ["T4-1"],
             "checkpoints": [{"ts": ts, "note": "旧检查点", "by": "executor"}]}]})
        v = tasks.view(a)
        res = {"带代次碰旧记录": _rej(a, lambda: tasks.done(a, "T4-1", "结果", generation="0" * 32))}
        m = tasks.done(a, "T4-1", "旧命令照旧完成")    # 不带代次：旧记录兼容；第一次成功修改即迁移
        res["迁移后不带代次"] = _rej(a, lambda: tasks.close(a, "T4-1", "superseded", "换新的"))
        s = tasks.close(a, "T4-1", "superseded", "换新的", generation=m["generation"])
        l2 = tasks.progress(a, "T4-2", "继续", generation="legacy")   # 明确写 legacy 也可以
        v2 = tasks.view(a)
    t2 = next(t for t in v["tasks"] if t["id"] == "T4-2")
    ok = (all(t["legacy"] for t in v["tasks"]) and "--generation" not in t2["commands"]["done"] and t2["depends_legacy"]
          and not tasks.interrupted(t2) and all(x == "unchanged" for x in res.values())
          and len(m["generation"]) == 32 and m["rev"] == 1 and m.get("migrated_from_legacy") and m["project"] == a.name
          and s["rev"] == 2 and l2["rev"] == 1 and l2.get("migrated_from_legacy") and len(l2["checkpoints"]) == 2
          and not any(t["legacy"] for t in v2["tasks"]) and v["problem"] is None)
    _ok("B2-A1 旧记录明确兼容：不带代次或写 legacy 可以改，第一次成功修改就地迁移并返回新代次，之后必须带它；"
        "带代次碰旧记录拒绝；旧依赖标为旧引用；旧检查点不判为中断",
        ok, json.dumps([res, m, s.get("rev"), l2.get("rev"), t2.get("commands")], ensure_ascii=False)[:1500])


def _t_a1_cache(_ok):
    from . import tasks
    from .core import Instance
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "la-a1h", {"state": "VERIFY", "turn": 1})
        g = [t["generation"] for t in tasks.add(a, 1, {"1": "甲", "2": "乙"})]
        x, y = Instance(a.root), Instance(a.root)
        tasks.done(x, "T1-1", "x 完成", generation=g[0])          # x 的事务刷新了 x 的缓存与基线
        tasks.progress(y, "T1-2", "y 做到一半", generation=g[1])  # 之后 y 改了另一项
        x.state["a1_probe"] = "x"                                  # x 事务后改别的键，再 save、再 event
        x.save()
        x.event("a1_probe", by="driver")
        final = Instance(a.root).state
        t = {r["id"]: r for r in final["executor_tasks"]}
        mem = {r["id"]: r.get("rev") for r in x.state["executor_tasks"]}
        # 只裁剪、不新增的 add 不记事件：仍须把调用方的缓存与三方合并的基线刷新到写回的内容
        old = "2026-01-01T00:00:00+00:00"
        z = _inst(td, "la-a1h2", {"state": "VERIFY", "turn": 9, "executor_tasks": [
            {"id": f"T8-{i}", "turn": 8, "text": "旧", "since": old, "done": old, "note": "旧"} for i in range(1, tasks.KEEP_DONE + 3)]})
        nothing = tasks.add(z, 8, {"1": "已存在，不覆盖"})
        zd = _disk(z)["executor_tasks"]
        trim = nothing == [] and len(zd) == tasks.KEEP_DONE and z.state["executor_tasks"] == zd and z._base.get("executor_tasks") == zd
    ok = (tasks.status_of(t["T1-1"]) == "done" and t["T1-2"]["rev"] == 2 and len(t["T1-2"].get("checkpoints") or []) == 1
          and final.get("a1_probe") == "x" and mem == {"T1-1": 2, "T1-2": 2} and trim)
    _ok("B2-A1 任务事务之后调用方再 save / event：不会把旧的 executor_tasks 覆盖回去，别人后来的修改仍在",
        ok, json.dumps([t, mem, final.get("a1_probe"), trim], ensure_ascii=False)[:1500])


def _t_a1_corrupt(_ok):
    from . import tasks
    g = "f" * 32
    cases = {
        "不是列表": "x",
        "编号重复": [{"id": "T1-1", "turn": 1, "text": "a"}, {"id": "t1-1", "turn": 1, "text": "b"}],
        "缺编号": [{"turn": 1, "text": "a"}],
        "代次格式不对": [{"id": "T1-1", "turn": 1, "text": "a", "generation": "abc", "rev": 1}],
        "rev 不是正整数": [{"id": "T1-1", "turn": 1, "text": "a", "generation": g, "rev": 0}],
        "项目不符": [{"id": "T1-1", "turn": 1, "text": "a", "generation": g, "rev": 1, "project": "别的项目"}],
    }
    res = {}
    with tempfile.TemporaryDirectory() as td:
        for i, (name, bad) in enumerate(cases.items()):
            a = _inst(td, f"la-a1c{i}", {"state": "VERIFY", "turn": 1, "executor_tasks": bad})
            r = [_rej(a, lambda: tasks.add(a, 1, {"9": "新的"})),
                 _rej(a, lambda: tasks.done(a, "T1-1", "结果", generation=g)),
                 _rej(a, lambda: tasks.progress(a, "T1-1", "检查点", generation=g)),
                 _rej(a, lambda: tasks.close(a, "T1-1", "cancelled", "原因", generation=g))]
            res[name] = r + [bool(tasks.view(a)["problem"])]
        a = _inst(td, "la-a1cj", {"state": "VERIFY", "turn": 1})
        a.state_path.write_text("{坏的 JSON", encoding="utf-8")
        res["状态文件不是 JSON"] = [_rej(a, lambda: tasks.add(a, 1, {"1": "新的"})), True]
    ok = all(all(x == "unchanged" for x in v[:-1]) and v[-1] for v in res.values())
    _ok("B2-A1 待办记录损坏（不是列表、编号重复、缺编号、代次或 rev 不合格式、项目不符、状态文件坏）：一律拒绝写入且不改文件，列表照样能看",
        ok, json.dumps(res, ensure_ascii=False)[:1500])


def _t_a1_interrupt(_ok):
    import hashlib
    from . import tasks
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "la-a1r", {"state": "VERIFY", "turn": 3})
        g = tasks.add(a, 3, {"1": "长任务"})[0]["generation"]
        tasks.progress(a, "T3-1", "被额度打断，做到第二步", generation=g, kind="interrupted")
        tasks.progress(a, "T3-1", "还在等额度", generation=g)   # 普通检查点：仍未恢复
        res = {"中断未恢复": _rej(a, lambda: tasks.done(a, "T3-1", "完成", generation=g)),
               "空结果": _rej(a, lambda: tasks.done(a, "T3-1", "  ", generation=g))}
        tasks.progress(a, "T3-1", "恢复后做完第三步", generation=g, kind="resumed")
        (a.root / "out.txt").write_text("证据内容", encoding="utf-8")
        outside = Path(td) / "outside.txt"
        outside.write_text("x", encoding="utf-8")
        secret = "API_KEY=" + "Ab3" * 6
        res.update({"证据在项目外": _rej(a, lambda: tasks.done(a, "T3-1", "完成", generation=g, evidence=[str(outside)])),
                    "证据不存在": _rej(a, lambda: tasks.done(a, "T3-1", "完成", generation=g, evidence=["nope.txt"])),
                    "结果含密钥": _rej(a, lambda: tasks.done(a, "T3-1", f"完成 {secret}", generation=g)),
                    "检查点含密钥": _rej(a, lambda: tasks.progress(a, "T3-1", f"日志 {secret}", generation=g)),
                    "原因含密钥": _rej(a, lambda: tasks.close(a, "T3-1", "cancelled", f"因为 {secret}", generation=g))})
        d = tasks.done(a, "T3-1", "完成", generation=g, evidence=["out.txt", "A" * 64])
        leaked = any(secret in f.read_text(encoding="utf-8", errors="replace") for f in a.wf.rglob("*") if f.is_file())
    want = [{"ref": "out.txt", "sha256": hashlib.sha256("证据内容".encode("utf-8")).hexdigest()}, {"sha256": "a" * 64}]
    ok = (all(v == "unchanged" for v in res.values()) and d.get("evidence") == want and tasks.status_of(d) == "done"
          and d.get("interrupted_open") is False and len(d.get("checkpoints") or []) == 3 and not leaked)
    _ok("B2-A1 被打断 → 普通检查点仍未恢复 → 不能完成；记“已恢复”后才能完成；结果必填、证据须在项目内并记摘要；"
        "结果、检查点、原因里有密钥时拒绝且不落盘",
        ok, json.dumps([res, d.get("evidence"), leaked], ensure_ascii=False)[:1500])


def _t_a1_send_identity(_ok):
    from . import arbiter, driver, tasks
    from .core import Instance
    with tempfile.TemporaryDirectory() as td:
        _fresh_shared()
        a = _sendable(td, "la-a1d", 6)
        message = driver._prepare_send(_ctx(a, {"shared_max_inflight": 1, "send_unknown_wait": True}), a.state["outgoing"])
        g = arbiter.acquire(a, 1)
        a.state.update(send_slot={"lease_id": g["lease_id"], "turn": 6, "attempt": g["attempt"], "granted_at": g["granted_at"]},
                       send_intent={"turn": 6, "status": "pending", "ts": g["granted_at"],
                                    "file": a.state["outgoing"]["file"], "message": message},
                       send_ref="R5")
        a.save()
        mk = arbiter.mark_sending(a, g["lease_id"], 6)
        keys = ("state", "turn", "send_intent", "send_slot", "send_ref", "outgoing", "receipt")
        before, hold0 = {k: _disk(a).get(k) for k in keys}, _holders(a.name)
        fake = _Fake(poll=[POLL_NONE])
        with _patched(fake):
            t = tasks.add(a, 6, {"1": "与发送并行的执行端待办"})[0]
            b = Instance(a.root)
            tasks.progress(b, "T6-1", "做到一半", generation=t["generation"])
            tasks.done(b, "T6-1", "完成", generation=t["generation"])
            calls = list(fake.calls)
            after, hold1 = {k: _disk(a).get(k) for k in keys}, _holders(a.name)
            r = driver.h_send(_ctx(Instance(a.root), {"shared_max_inflight": 1, "send_unknown_wait": True}))
    ok = (mk["marked"] and before == after and hold0 == hold1 and bool(hold1 and hold1[0].get("send_marked_at"))
          and calls == [] and fake.calls.count("send") == 0 and r[0] == "wait")
    _ok("B2-A1 发送结果不明且租约已标记时改待办：发送意图、send_slot、send_ref、轮次与持有记录原样不动，不调用适配器；之后仍只轮询不发送",
        ok, json.dumps([before == after, hold0 == hold1, calls, fake.calls, r[0]], ensure_ascii=False)[:1500])


def _t_a1_cli(_ok):
    from . import tasks
    from .core import WF_HOME
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "la-a1c", {"state": "VERIFY", "turn": 2})
        g = tasks.add(a, 2, {"1": "命令行待办"})[0]["generation"]

        def wf(*args):
            r = subprocess.run([sys.executable, str(WF_HOME / "wf.py")] + list(args), cwd=str(a.root), env=dict(os.environ),
                               capture_output=True, text=True, timeout=60)
            return r.returncode, r.stdout + r.stderr
        rl, ol = wf("task")
        rj, oj = wf("--json", "task")
        rn, on = wf("task", "done", "T2-1", "--note", "结果")
        ri, oi = wf("task", "progress", "T2-1", "--generation", g, "--interrupted", "--note", "被打断")
        rd, od = wf("task", "done", "T2-1", "--generation", g, "--note", "结果")
        rr, orr = wf("task", "progress", "T2-1", "--generation", g, "--resumed", "--note", "恢复后完成")
        rx, ox = wf("--json", "task", "done", "T2-1", "--generation", g, "--rev", "3", "--note", "结果", "--evidence", "PROJECT.md")
        try:
            jl, jd = json.loads(oj[oj.index("{"):]), json.loads(ox[ox.index("{"):])
        except ValueError:
            jl, jd = {}, {}
    cmd = f"wf task done T2-1 --generation {g}"
    ok = (rl == 0 and cmd in ol and rj == 0 and (jl.get("tasks") or [{}])[0].get("commands", {}).get("done", "").startswith(cmd)
          and rn != 0 and g not in on and ri == 0 and rd != 0 and rr == 0 and rx == 0
          and jd.get("task", {}).get("rev") == 4 and (jd.get("task", {}).get("evidence") or [{}])[0].get("ref") == "PROJECT.md")
    _ok("B2-A1 命令行：wf task 与 --json 列出代次与可照抄的命令；不带 --generation 拒绝且不泄露代次；--interrupted / --resumed / --rev / --evidence 生效",
        ok, json.dumps([rl, rj, rn, ri, rd, rr, rx, ol[-300:], on[-200:], od[-200:], ox[-300:]], ensure_ascii=False)[:1500])


# ---------------- B2-FIX-A1：审查反例回归 ----------------

def _wf_cli(root, *args):
    from .core import WF_HOME
    r = subprocess.run([sys.executable, str(WF_HOME / "wf.py")] + list(args), cwd=str(root), env=dict(os.environ),
                       capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout + r.stderr


def _t_a1fix_copy_root(_ok):
    from . import tasks
    from .core import Instance
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "la-fr", {"state": "VERIFY", "turn": 1})
        g = tasks.add(a, 1, {"1": "甲", "2": "乙"})[0]["generation"]
        dst = Path(td) / "la-fr-copy"
        shutil.copytree(a.root, dst)
        c = Instance(dst)
        res = {"克隆上用原代次 done": _rej(c, lambda: tasks.done(c, "T1-1", "克隆上完成", generation=g)),
               "克隆上登记": _rej(c, lambda: tasks.add(c, 2, {"1": "新的"}))}
        vc = tasks.view(c)
        ok_orig = tasks.done(a, "T1-1", "原项目完成", generation=g)
        # 只有旧记录（没有代次）的项目被复制：旧兼容不变，迁移时绑定克隆自己的目录
        lg = _inst(td, "la-fr-legacy", {"state": "VERIFY", "turn": 1, "executor_tasks": [
            {"id": "T1-1", "turn": 1, "text": "旧", "since": "2026-01-01T00:00:00+00:00", "done": None, "note": ""}]})
        ldst = Path(td) / "la-fr-legacy-copy"
        shutil.copytree(lg.root, ldst)
        lc = Instance(ldst)
        m = tasks.done(lc, "T1-1", "旧命令在克隆上完成")
    ok = (c.name == a.name and str(c.root) != str(a.root) and all(v == "unchanged" for v in res.values())
          and "另一个项目目录" in (vc["problem"] or "") and all(not t["commands"] for t in vc["tasks"])
          and ok_orig["rev"] == 2 and ok_orig["root"] == str(a.root) and m["root"] == str(lc.root) and m["rev"] == 1)
    _ok("B2-FIX-A1 R1 同名项目复制到另一个根目录：原代次对克隆 done、登记都拒绝且不写，列表不给命令；原项目照常；只含旧记录的克隆仍按旧规则兼容",
        ok, json.dumps([res, vc["problem"], ok_orig.get("rev"), m.get("root") == str(lc.root)], ensure_ascii=False)[:1500])


def _t_a1fix_dirty_caller(_ok):
    from . import arbiter, tasks
    with tempfile.TemporaryDirectory() as td:
        _fresh_shared()
        a = _sendable(td, "la-fd", 6)
        g = arbiter.acquire(a, 1)
        slot = {"lease_id": g["lease_id"], "turn": 6, "attempt": g["attempt"], "granted_at": g["granted_at"]}
        a.state.update(send_slot=slot, send_intent={"turn": 6, "status": "pending", "ts": g["granted_at"],
                                                    "file": a.state["outgoing"]["file"]}, send_ref="R5")
        a.save()
        mk = arbiter.mark_sending(a, g["lease_id"], 6)
        keys = ("send_intent", "send_ref", "send_slot")
        disk0 = {k: _disk(a).get(k) for k in keys}
        t = tasks.add(a, 6, {"1": "与发送并行的执行端待办"})[0]
        a.state.update(send_intent=None, send_ref="OLD", send_slot=None)   # 调用方暂存、尚未保存的修改
        tasks.progress(a, "T6-1", "做到一半", generation=t["generation"])
        disk1 = {k: _disk(a).get(k) for k in keys}
        kept = (a.state.get("send_intent"), a.state.get("send_ref"), a.state.get("send_slot"))
        synced = a._base.get("executor_tasks") == _disk(a)["executor_tasks"] == a.state["executor_tasks"]
        a.save()                                                            # 调用方自己保存：它的修改照常落盘，待办不被盖回
        disk2 = _disk(a)
        seqs = [e.get("seq") for e in _events(a)]
        cps = (disk2["executor_tasks"][0].get("checkpoints") or [])
    ok = (mk["marked"] and disk0 == disk1 and kept == (None, "OLD", None) and synced
          and (disk2.get("send_intent"), disk2.get("send_ref"), disk2.get("send_slot")) == (None, "OLD", None)
          and len(cps) == 1 and len(seqs) == len(set(seqs)))
    _ok("B2-FIX-A1 R2 调用方内存里有未保存的发送字段时改待办：事务只提交待办与事件，磁盘上的发送意图、ref、slot 不变；"
        "调用方的修改不丢、之后由它自己保存；待办缓存与基线已同步，保存后不被盖回；事件序号不重复",
        ok, json.dumps([disk0 == disk1, kept, synced, len(cps), len(seqs) == len(set(seqs))], ensure_ascii=False)[:1500])


def _t_a1fix_null_shape(_ok):
    from . import tasks
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "la-fn", {"state": "VERIFY", "turn": 1, "executor_tasks": None})
        res = {"null 上登记": _rej(a, lambda: tasks.add(a, 1, {"1": "新的"})),
               "null 上完成": _rej(a, lambda: tasks.done(a, "T1-1", "结果", generation="f" * 32))}
        problem = tasks.view(a)["problem"]
        b = _inst(td, "la-fn2", {"state": "VERIFY", "turn": 1})   # 缺键：旧兼容，按空表
        added = tasks.add(b, 1, {"1": "新的"})
    ok = all(v == "unchanged" for v in res.values()) and bool(problem) and len(added) == 1
    _ok("B2-FIX-A1 R3 executor_tasks 显式 null 不当空表：登记、完成都拒绝且不写；缺这个键仍按旧状态兼容为空表",
        ok, json.dumps([res, problem, len(added)], ensure_ascii=False)[:1500])


def _t_a1fix_evidence_alias(_ok):
    from . import tasks
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "la-fe", {"state": "VERIFY", "turn": 1})
        g = tasks.add(a, 1, {"1": "甲"})[0]["generation"]
        secret_name = "API_KEY=" + "Ab3" * 6
        (a.root / secret_name).write_text("证据", encoding="utf-8")
        os.symlink(a.root / secret_name, a.root / "evidence.txt")
        r = _rej(a, lambda: tasks.done(a, "T1-1", "完成", generation=g, evidence=["evidence.txt"]))
        leaked = any(secret_name in f.read_text(encoding="utf-8", errors="replace") for f in a.wf.rglob("*") if f.is_file())
        (a.root / "ok.txt").write_text("正常证据", encoding="utf-8")
        d = tasks.done(a, "T1-1", "完成", generation=g, evidence=["ok.txt"])
    ok = r == "unchanged" and not leaked and d["evidence"][0]["ref"] == "ok.txt"
    _ok("B2-FIX-A1 R4 证据别名指向疑似密钥文件名：按解析后实际写入的路径再查，拒绝且状态、事件、备注都不出现该名字；正常证据照常",
        ok, json.dumps([r, leaked, d.get("evidence")], ensure_ascii=False)[:1500])


def _t_a1fix_bad_shapes(_ok):
    from . import tasks
    from .core import Instance
    out = {}
    with tempfile.TemporaryDirectory() as td:
        cases = {"缺编号": [{"turn": 1, "text": "缺编号"}],
                 "检查点是对象": [{"id": "T1-1", "turn": 1, "text": "x", "checkpoints": {"a": 1}}],
                 "依赖不是列表": [{"id": "T1-1", "turn": 1, "text": "x", "depends_on": "T1-2"}]}
        for i, (name, bad) in enumerate(cases.items()):
            a = _inst(td, f"la-fs{i}", {"state": "VERIFY", "turn": 1, "executor_tasks": bad})
            out[name] = [a, None]
        n = _inst(td, "la-fs-new", {"state": "VERIFY", "turn": 1})   # 新协议记录被删掉正文
        gn = tasks.add(n, 1, {"1": "甲"})[0]["generation"]
        raw = _disk(n)
        del raw["executor_tasks"][0]["text"]
        n.state_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        out["新记录缺正文"] = [Instance(n.root), gn]
        res = {}
        for name, (a, gen) in out.items():
            rc, o = _wf_cli(a.root, "task")
            rj, oj = _wf_cli(a.root, "--json", "task")
            try:
                j = json.loads(oj[oj.index("{"):])
            except ValueError:
                j = {}
            w = [_rej(a, lambda: tasks.done(a, "T1-1", "结果", generation=gen or "f" * 32)),
                 _rej(a, lambda: tasks.progress(a, "T1-1", "检查点", generation=gen or "f" * 32))]
            notes = list(a.wf.rglob("notes.jsonl"))
            res[name] = [rc == 0 and "Traceback" not in o and "损坏" in o and "wf task done" not in o,
                         rj == 0 and bool(j.get("problem")) and all(not t.get("commands") for t in j.get("tasks", [])),
                         w, notes == []]
    ok = all(v[0] and v[1] and all(x == "unchanged" for x in v[2]) and v[3] for v in res.values())
    _ok("B2-FIX-A1 R5 坏数据只读可诊断：缺编号、检查点是对象、依赖不是列表、新记录缺正文时 wf task 与 --json 都能列出并说明损坏，"
        "不抛 traceback、不给可执行命令；修改在写之前就拒绝，状态、事件、备注都不变",
        ok, json.dumps(res, ensure_ascii=False)[:1500])


def _t_a1fix_successor(_ok):
    from . import tasks
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "la-fu", {"state": "VERIFY", "turn": 1, "executor_tasks": [
            {"id": "T0-1", "turn": 0, "text": "旧接替者", "since": "2026-01-01T00:00:00+00:00", "done": None, "note": ""}]})
        x = tasks.add(a, 1, {"1": "甲", "2": "乙", "3": "丙", "4": "丁"})
        g = {t["id"]: t["generation"] for t in x}
        s1 = tasks.add(a, 2, {"1": "接替者第一代"})[0]["generation"]
        res = {"裸 by_task 指向新协议接替者": _rej(a, lambda: tasks.close(a, "T1-1", "superseded", "被接替", by_task="T2-1",
                                                                       generation=g["T1-1"])),
               "接替者代次写错": _rej(a, lambda: tasks.close(a, "T1-1", "superseded", "被接替", by_task="T2-1",
                                                       by_generation="0" * 32, generation=g["T1-1"])),
               "取消带接替参数": _rej(a, lambda: tasks.close(a, "T1-1", "cancelled", "不要了", by_task="T2-1",
                                                       generation=g["T1-1"])),
               "未登记的接替者带代次": _rej(a, lambda: tasks.close(a, "T1-1", "superseded", "被接替", by_task="T9-9",
                                                            by_generation=s1, generation=g["T1-1"])),
               "旧接替者带代次": _rej(a, lambda: tasks.close(a, "T1-1", "superseded", "被接替", by_task="T0-1",
                                                        by_generation=s1, generation=g["T1-1"]))}
        tasks.done(a, "T2-1", "第一代接替者做完", generation=s1)   # 让第一代接替者被裁掉，同编号重新登记
        filler = [{"id": f"T8-{i}", "turn": 8, "text": "旧", "since": "2026-01-01T00:00:00+00:00",
                   "done": "2026-01-01T00:00:00+00:00", "note": "旧"} for i in range(1, tasks.KEEP_DONE + 1)]
        raw = _disk(a)
        raw["executor_tasks"] += filler
        a.state_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        from .core import Instance
        a = Instance(a.root)
        tasks.add(a, 9, {"1": "触发裁剪"})
        s2 = tasks.add(a, 2, {"1": "接替者第二代"})[0]["generation"]
        res["接替者被裁掉后用旧代次"] = _rej(a, lambda: tasks.close(a, "T1-1", "superseded", "被接替", by_task="T2-1",
                                                               by_generation=s1, generation=g["T1-1"]))
        ok1 = tasks.close(a, "T1-1", "superseded", "被接替", by_task="T2-1", by_generation=s2, generation=g["T1-1"])
        ok2 = tasks.close(a, "T1-2", "superseded", "旧接替者", by_task="T0-1", generation=g["T1-2"])
        ok3 = tasks.close(a, "T1-3", "superseded", "尚未登记的接替者", by_task="T7-7", generation=g["T1-3"])
        ok4 = tasks.close(a, "T1-4", "superseded", "旧接替者，明确写 legacy", by_task="T0-1", by_generation="legacy",
                          generation=g["T1-4"])
    ok = (all(v == "unchanged" for v in res.values()) and s1 != s2
          and (ok1["superseded_by_generation"], ok1["superseded_by_ref"]) == (s2, "generation")
          and (ok2["superseded_by_generation"], ok2["superseded_by_ref"]) == (None, "legacy")
          and (ok3["superseded_by_generation"], ok3["superseded_by_ref"]) == (None, "unregistered")
          and ok4["superseded_by_ref"] == "legacy")
    _ok("B2-FIX-A1 R6 接替者绑定：已按代次登记的接替者必须写明 --by-generation 且一致（裸编号、写错、被裁掉后的旧代次都拒绝）；"
        "旧接替者不写或写 legacy；未登记的保持 null、带代次拒绝；取消不接受接替参数",
        ok, json.dumps([res, ok1.get("superseded_by_ref"), ok2.get("superseded_by_ref"), ok3.get("superseded_by_ref")],
                       ensure_ascii=False)[:1500])


def _t_a1fix_cli_args(_ok):
    from . import tasks
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "la-fa", {"state": "VERIFY", "turn": 1})
        g = tasks.add(a, 1, {"1": "甲"})[0]["generation"]
        bad = {"progress 带 --by-generation": ("task", "progress", "T1-1", "--generation", g, "--by-generation", "bogus", "--note", "x"),
               "progress 带 --evidence": ("task", "progress", "T1-1", "--generation", g, "--evidence", "PROJECT.md", "--note", "x"),
               "done 带 --reason": ("task", "done", "T1-1", "--generation", g, "--note", "x", "--reason", "多余"),
               "done 带 --interrupted": ("task", "done", "T1-1", "--generation", g, "--note", "x", "--interrupted"),
               "cancel 带 --by-task": ("task", "cancel", "T1-1", "--generation", g, "--reason", "不要了", "--by-task", "T1-2"),
               "cancel 带 --note": ("task", "cancel", "T1-1", "--generation", g, "--reason", "不要了", "--note", "x"),
               "supersede 只有 --by-generation": ("task", "supersede", "T1-1", "--generation", g, "--reason", "接替", "--by-generation", g),
               "list 带 --generation": ("task", "list", "--generation", g),
               "list 带编号": ("task", "list", "T1-1")}
        res = {}
        for name, args in bad.items():
            before = _snap(a)
            rc, o = _wf_cli(a.root, *args)
            res[name] = [rc != 0, _snap(a) == before, "不接受" in o or "一起用" in o]
        rc_ok, _ = _wf_cli(a.root, "task", "progress", "T1-1", "--generation", g, "--note", "正常检查点")
    ok = all(all(v) for v in res.values()) and rc_ok == 0
    _ok("B2-FIX-A1 R7 命令行里不适用于该动作的参数一律拒绝且不写（--by-generation 只给 supersede 且要配 --by-task，--evidence 只给 done，"
        "--interrupted/--resumed 只给 progress，--note/--reason 各归其位，list 不接受编号与代次）",
        ok, json.dumps(res, ensure_ascii=False)[:1500])


def _t_a1fix2_checkpoint_types(_ok):
    # B2-FIX2-A1：检查点与状态字段的类型边界。坏数据：能看、说明损坏、不给命令、不抛 traceback，所有修改都在写之前拒绝
    from . import tasks
    from .core import Instance
    old = "2026-01-01T00:00:00+00:00"
    neg = {"kind=[]": {"cp": {"kind": []}}, "kind={}": {"cp": {"kind": {}}}, "kind=1": {"cp": {"kind": 1}},
           "kind=True": {"cp": {"kind": True}}, "kind=null": {"cp": {"kind": None}}, "kind=未知文本": {"cp": {"kind": "paused"}},
           "ts=[]": {"cp": {"ts": []}}, "by={}": {"cp": {"by": {}}}, "status=[x]": {"rec": {"status": ["x"]}},
           "status=未知文本": {"rec": {"status": "reopened"}},
           "旧记录 kind=[]": {"legacy": True, "cp": {"kind": []}},
           "旧记录 interrupted_open=文本": {"legacy": True, "rec": {"interrupted_open": "yes"}}}
    res = {}
    with tempfile.TemporaryDirectory() as td:
        for i, (name, case) in enumerate(neg.items()):
            if case.get("legacy"):
                a = _inst(td, f"la-ck{i}", {"state": "VERIFY", "turn": 1, "executor_tasks": [
                    {"id": "T1-1", "turn": 1, "text": "旧", "since": old, "done": None, "note": ""}]})
                gen = None
            else:
                a = _inst(td, f"la-ck{i}", {"state": "VERIFY", "turn": 1})
                gen = tasks.add(a, 1, {"1": "合法新任务"})[0]["generation"]
            raw = _disk(a)
            rec = raw["executor_tasks"][0]
            if "cp" in case:
                rec["checkpoints"] = [dict({"ts": old, "note": "checkpoint", "by": "executor"}, **case["cp"])]
            rec.update(case.get("rec", {}))
            a.state_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            a = Instance(a.root)
            v = tasks.view(a)
            rc, o = _wf_cli(a.root, "task")
            rj, oj = _wf_cli(a.root, "--json", "task")
            try:
                j = json.loads(oj[oj.index("{"):])
            except ValueError:
                j = {}
            w = [_rej(a, lambda: tasks.done(a, "T1-1", "结果", generation=gen)),
                 _rej(a, lambda: tasks.progress(a, "T1-1", "检查点", generation=gen)),
                 _rej(a, lambda: tasks.close(a, "T1-1", "cancelled", "不要了", generation=gen)),
                 _rej(a, lambda: tasks.add(a, 2, {"1": "新的"}))]
            res[name] = [bool(v["problem"]) and all(not t["commands"] for t in v["tasks"]),
                         rc == 0 and "Traceback" not in o and "损坏" in o and "wf task done" not in o,
                         rj == 0 and bool(j.get("problem")) and all(not t.get("commands") for t in j.get("tasks", [])),
                         all(x == "unchanged" for x in w), list(a.wf.rglob("notes.jsonl")) == []]
        # 正例：1.9.0 的旧检查点没有 kind，照旧兼容；迁移后新旧检查点混在一起也合法；新记录三种 kind 正常往下走
        lg = _inst(td, "la-ck-legacy", {"state": "VERIFY", "turn": 1, "executor_tasks": [
            {"id": "T1-1", "turn": 1, "text": "旧", "since": old, "done": None, "note": "",
             "checkpoints": [{"ts": old, "note": "旧检查点", "by": "executor"}]}]})
        v0 = tasks.view(lg)
        m = tasks.progress(lg, "T1-1", "继续")
        v1 = tasks.view(lg)
        rcl, ol = _wf_cli(lg.root, "task")
        n = _inst(td, "la-ck-new", {"state": "VERIFY", "turn": 1})
        g = tasks.add(n, 1, {"1": "长任务"})[0]["generation"]
        tasks.progress(n, "T1-1", "被打断", generation=g, kind="interrupted")
        tasks.progress(n, "T1-1", "已恢复", generation=g, kind="resumed")
        d = tasks.done(n, "T1-1", "完成", generation=g)
        vn = tasks.view(n)
    pos = (v0["problem"] is None and "done" in v0["tasks"][0]["commands"] and v1["problem"] is None
           and [c.get("kind") for c in m["checkpoints"]] == [None, "progress"] and rcl == 0 and "wf task done T1-1 --generation" in ol
           and tasks.status_of(d) == "done" and vn["problem"] is None)
    ok = all(all(x) for x in res.values()) and pos
    _ok("B2-FIX2-A1 检查点与状态的类型边界：kind 为列表、字典、数字、真假值、null、未知文本，ts/by 不是文本，status 不合法，"
        "interrupted_open 不是真假值时，列表能看并说明损坏、不给命令、不抛 traceback，修改全部写前拒绝；旧检查点缺 kind 照旧兼容",
        ok, json.dumps([res, pos, v0["problem"], v1["problem"]], ensure_ascii=False)[:1500])


# ---------------- B2-A2a：恢复预算与仲裁身份 ----------------
# 场景都在临时 WF_LOCAL_DIR 与临时项目目录里真实运行驱动器的处理函数；适配器换成替身脚本，按返回码控制；
# 时间流逝靠改写磁盘上的 since、until 模拟。并发用真实子进程与真实文件锁。

BUSY = {"code": 2, "raw": "busy", "data": {}}
NET_ERR = {"code": 4, "raw": "net", "data": {}}
QUOTA_HIT = {"code": 3, "raw": "quota", "data": {}}
POLL_GEN = {"code": 0, "raw": "", "data": {"status": "generating"}}
SEND_TIMEOUT = {"code": 4, "raw": "timeout", "data": {}}


def _ep(inst):
    return _disk(inst).get("recovery")


def _rewrite(inst, fn=None, **fields):
    """直接改磁盘上的状态（模拟时间流逝或外部改动），返回新读的 Instance。
    since=… 表示恢复期已经过了这么久：since、deadline、upgraded_at、last.ts 一起往前拨，记录内部关系保持一致（r2）。"""
    from .core import Instance, parse_iso
    raw = _disk(inst)
    for k, v in fields.items():
        if k == "since":
            ep = raw["recovery"]
            delta = parse_iso(ep["since"]) - parse_iso(v)
            for f in ("since", "deadline", "upgraded_at"):
                if ep.get(f):
                    ep[f] = (parse_iso(ep[f]) - delta).isoformat(timespec="seconds")
            if isinstance(ep.get("last"), dict) and ep["last"].get("ts"):
                ep["last"]["ts"] = (parse_iso(ep["last"]["ts"]) - delta).isoformat(timespec="seconds")
        else:
            raw[k] = v
    if fn:
        fn(raw)
    inst.state_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return Instance(inst.root)


@contextmanager
def _clock(offset_s):
    """把驱动器、core、recovery 看到的“现在”往后拨 offset_s 秒（磁盘内容不动，发送意图的 key 也不变）。"""
    from . import core, driver, recovery
    real = core.now
    saved = (core.now, driver.now, recovery._now)

    def fake():
        return real() + timedelta(seconds=offset_s)
    core.now = driver.now = recovery._now = fake
    try:
        yield
    finally:
        core.now, driver.now, recovery._now = saved


def _unknown_pending(td, name, turn):
    """一条结果不明、待确认的发送意图：授予、保存、外发前标记都已完成，持有带 token 的槽；适配器从未被调用。"""
    from . import arbiter, driver
    a = _sendable(td, name, turn)
    message = driver._prepare_send(_ctx(a, {"shared_max_inflight": 1, "send_unknown_wait": True}), a.state["outgoing"])
    g = arbiter.acquire(a, 1, 3600)
    a.state.update(send_slot={"lease_id": g["lease_id"], "turn": turn, "attempt": g["attempt"], "granted_at": g["granted_at"]},
                   send_intent={"turn": turn, "status": "pending", "ts": _iso(0),
                                "file": a.state["outgoing"]["file"], "message": message},
                   send_ref="R-prev")
    a.save()
    arbiter.mark_sending(a, g["lease_id"], turn)
    return a


def _due(inst):
    """跳过退避等待（驱动器在下一拍才会再来；这里直接让它到点）。"""
    inst.state.pop("retry_at", None)
    return inst


def _send_fields(st) -> dict:
    return {k: st.get(k) for k in ("send_ref", "send_intent", "send_slot")}


def _waiting_reply(td, name, turn, pol=None):
    """真实走一次授予、外发前标记、外发，停在等回复：持有带 token 的槽、有 ref。"""
    from . import driver
    a = _sendable(td, name, turn)
    with _patched(_Fake(send=[SENT_OK])):
        driver.h_send(_ctx(a, pol or {"shared_max_inflight": 1}))
    a.state.pop("poll_after", None)
    a.save()
    return a


def _same_name_pair(td, base, turn=1, state="SEND"):
    """同名、不同根目录的两个项目（例如复制出来的目录）。"""
    from .core import Instance
    out = []
    for d in ("root-a", "root-b"):
        root = Path(td) / d / base
        (root / "workflow").mkdir(parents=True)
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"{base}-{os.getpid()}"}), encoding="utf-8")
        (root / "PROJECT.md").write_text("# PROJECT.md\n", encoding="utf-8")
        i = Instance(root)
        i.state.update(state=state, turn=turn)
        i.save()
        out.append(i)
    return out


def _drop_quota_hold():
    from . import driver
    if driver.GLOBAL_QUOTA.exists():
        driver.GLOBAL_QUOTA.unlink()


def _delays(texts) -> list:
    import re as _re
    out = []
    for t in texts:
        m = _re.search(r"(\d+) 秒后", t)
        out.append(int(m.group(1)) if m else None)
    return out


# ---- 提供方（C1_PROVIDERS）：每类一个正例、一个反例，都实际运行 ----

def _p_busy_pos():
    from . import driver
    with tempfile.TemporaryDirectory() as td:
        _fresh_shared()
        a = _sendable(td, "a2-busy-p", 3)
        fake = _Fake(send=[BUSY, BUSY])
        with _patched(fake):
            r1 = driver.h_send(_ctx(a, {}))
            s1 = _ep(a)["since"]
            r2 = driver.h_send(_ctx(_due(a), {}))
            r3 = driver._anomaly(_ctx(a, {}), {"source": "transport", "code": 4}, "换成网络错误，仍是同一个恢复期")
        st = _disk(a)
    ep = st["recovery"]
    ok = (r1[0] == r2[0] == r3[0] == "wait" and st["state"] == "SEND" and ep["since"] == s1
          and ep["rules"] == {"SEND_BUSY": 2, "NET": 1} and ep["attempts"] == {"transient": 3, "quota": 0}
          and fake.calls.count("send") == 2)
    return ok, {"returns": [r1[0], r2[0], r3[0]], "state": st["state"], "since_kept": ep["since"] == s1, "rules": ep["rules"],
                "sends": fake.calls.count("send")}


def _p_busy_neg():
    from . import driver
    with tempfile.TemporaryDirectory() as td:
        _fresh_shared()
        a = _sendable(td, "a2-busy-n", 3)
        fake = _Fake(send=[BUSY] * 7)
        acts = []
        with _patched(fake):
            for _ in range(7):
                acts.append(driver.h_send(_ctx(_due(a), {}))[0])
                if a.state["state"] != "SEND":
                    break
        s7 = _disk(a)
        b = _sendable(td, "a2-busy-x", 3)
        fb = _Fake(send=[BUSY, BUSY])
        with _patched(fb):
            driver.h_send(_ctx(b, {}))
            b = _rewrite(b, since=_iso(-(14400 + 5)))   # 恢复期已过 4 小时
            rb = driver.h_send(_ctx(_due(b), {}))
        sb = _disk(b)
        ex = [e for e in _events(b, "auto") if e.get("rule") == "RECOVERY_EXHAUSTED"]
    ok = (acts == ["wait"] * 6 + ["user"] and s7["state"] == "BLOCKED" and "[SEND_BUSY]" in s7.get("block_reason", "")
          and "RECOVERY_EXHAUSTED" not in s7.get("block_reason", "") and s7["anomaly_counts"].get("3:SEND_BUSY") == 7
          and fake.calls.count("send") == 7 and s7["recovery"]["attempts"]["transient"] == 6
          and rb[0] == "user" and sb["state"] == "BLOCKED" and "RECOVERY_EXHAUSTED:transient" in sb.get("block_reason", "")
          and "损坏" not in sb.get("block_reason", "")
          and len(ex) == 1 and fb.calls.count("send") == 2)
    return ok, {"old_max": acts, "old_reason": s7.get("block_reason", "")[:60], "exhausted_reason": sb.get("block_reason", "")[:90],
                "sends_after_exhausted": fb.calls.count("send")}


def _p_network_pos():
    from . import driver
    pol = {"shared_max_inflight": 1}
    with tempfile.TemporaryDirectory() as td:
        _fresh_shared()
        a = _waiting_reply(td, "a2-net-p", 4)
        before, hold = _send_fields(_disk(a)), _holders(a.name)
        fake = _Fake(poll=[NET_ERR])
        with _patched(fake):
            r = driver.h_wait_reply(_ctx(a, pol))
        st, hold2 = _disk(a), _holders(a.name)
    ok = (r[0] == "wait" and st["state"] == "WAIT_REPLY" and _send_fields(st) == before and before["send_ref"]
          and (before["send_slot"] or {}).get("lease_id") and hold2 == hold and st["recovery"]["rules"] == {"NET": 1}
          and "send" not in fake.calls)
    return ok, {"ret": r[0], "state": st["state"], "send_fields_kept": _send_fields(st) == before, "holder_kept": hold2 == hold}


def _p_network_neg():
    from . import driver
    pol = {"shared_max_inflight": 1}
    with tempfile.TemporaryDirectory() as td:
        _fresh_shared()
        a = _waiting_reply(td, "a2-net-n", 4)
        before, hold = _send_fields(_disk(a)), _holders(a.name)
        fake = _Fake(poll=[NET_ERR, NET_ERR])
        with _patched(fake):
            driver.h_wait_reply(_ctx(a, pol))
            a = _rewrite(a, since=_iso(-(14400 + 5)), retry_at=None)
            r = driver.h_wait_reply(_ctx(a, pol))
        st, hold2 = _disk(a), _holders(a.name)
    ok = (r[0] == "user" and st["state"] == "BLOCKED" and "RECOVERY_EXHAUSTED:transient" in st.get("block_reason", "")
          and "损坏" not in st.get("block_reason", "")
          and _send_fields(st) == before and hold2 == hold and "send" not in fake.calls)
    return ok, {"ret": r[0], "state": st["state"], "reason": st.get("block_reason", "")[:90],
                "send_fields_kept": _send_fields(st) == before, "holder_kept": hold2 == hold}


def _p_quota_pos():
    from . import arbiter, driver
    from .core import parse_iso
    pol = {"shared_max_inflight": 1}
    try:
        with tempfile.TemporaryDirectory() as td:
            _fresh_shared()
            a = _sendable(td, "a2-q-s", 5)
            ra = _iso(7200)
            with _patched(_Fake(send=[{"code": 3, "raw": "quota", "data": {"resets_at": ra}}])):
                r0 = driver.h_send(_ctx(a, {}))
            s0 = _disk(a)
            hold_q = json.loads(driver.GLOBAL_QUOTA.read_text(encoding="utf-8"))
            _drop_quota_hold()
            p = _waiting_reply(td, "a2-q-w", 6)
            before = _send_fields(_disk(p))
            tok = before["send_slot"]["lease_id"]
            fake = _Fake(poll=[QUOTA_HIT, POLL_GEN])
            with _patched(fake):
                r1 = driver.h_wait_reply(_ctx(p, pol))
                s1 = _disk(p)
                q = _sendable(td, "a2-q-other", 2)
                g = arbiter.acquire(q, 1, 3600)   # 另一项目在租约期内：不得把等额度的持有者当作过期回收
                held = [h.get("lease_id") for h in _holders(p.name)]
                p = _rewrite(p, until=_iso(-1))
                r2 = driver.h_quota_wait(_ctx(p, pol))
                p.state.pop("poll_after", None)
                p.save()
                r3 = driver.h_wait_reply(_ctx(p, pol))
            s3 = _disk(p)
    finally:
        _drop_quota_hold()
    ok = (r0[0] == "wait" and s0["state"] == "QUOTA_WAIT" and parse_iso(s0["until"]) == parse_iso(ra)
          and hold_q.get("set_by") == a.name and s0["recovery"]["class"] == "quota"
          and r1[0] == "wait" and s1["state"] == "QUOTA_WAIT" and s1.get("return_to") == "WAIT_REPLY" and _send_fields(s1) == before
          and not g["granted"] and held == [tok] and r2[0] == "progress" and r3[0] == "wait" and s3["state"] == "WAIT_REPLY"
          and _send_fields(s3) == before and fake.calls == ["poll", "poll"])
    return ok, {"send_quota_until_is_resets_at": parse_iso(s0.get("until")) == parse_iso(ra), "global_hold_by": hold_q.get("set_by"),
                "wait_reply_return_to": s1.get("return_to"), "fields_kept": _send_fields(s1) == before,
                "other_project_granted": g["granted"], "holder_kept": held == [tok], "after_resume": [r3[0], s3["state"]],
                "adapter_calls": fake.calls}


def _p_quota_neg():
    from . import driver, recovery
    from .core import now, parse_iso
    try:
        with tempfile.TemporaryDirectory() as td:
            _fresh_shared()
            a = _sendable(td, "a2-q-13", 7)
            for i in range(12):
                recovery.note(a, "QUOTA", detail=f"第 {i + 1} 次试探")
            a.save()
            with _patched(_Fake(send=[QUOTA_HIT])):
                r13 = driver.h_send(_ctx(a, {}))
            s13 = _disk(a)
            b = _sendable(td, "a2-q-8d", 7)
            with _patched(_Fake(send=[QUOTA_HIT, QUOTA_HIT])):
                driver.h_send(_ctx(b, {}))
                b = _rewrite(b, since=_iso(-(691200 + 5)), until=_iso(-1))   # 恢复期已过 8 天
                driver.h_quota_wait(_ctx(b, {}))
                _drop_quota_hold()
                r8 = driver.h_send(_ctx(b, {}))
            s8 = _disk(b)
            lim = recovery.limits({"recovery_quota_window_s": 30 * 86400, "recovery_quota_attempts": 100,
                                   "recovery_transient_window_s": -1, "recovery_transient_attempts": "x"})
            low = recovery.limits({"recovery_quota_window_s": 3600, "recovery_transient_attempts": 3})
            past, soon = [], []
            for nm, ra, box in (("a2-q-past", "2000-01-01T00:00:00+00:00", past), ("a2-q-soon", _iso(10), soon)):
                _drop_quota_hold()
                x = _sendable(td, nm, 8)
                t0 = now()
                with _patched(_Fake(send=[{"code": 3, "raw": "quota", "data": {"resets_at": ra}}])):
                    driver.h_send(_ctx(x, {}))
                box.append(round((parse_iso(_disk(x)["until"]) - t0).total_seconds(), 0))
            c = _sendable(td, "a2-q-k", 8)
            waits = []
            with _patched(_Fake(send=[QUOTA_HIT, QUOTA_HIT])):
                for _ in range(2):
                    _drop_quota_hold()
                    t0 = now()
                    driver.h_send(_ctx(c, {}))
                    waits.append(round((parse_iso(_disk(c)["until"]) - t0).total_seconds() / 3600, 2))
                    c = _rewrite(c, until=_iso(-1))
                    driver.h_quota_wait(_ctx(c, {}))
    finally:
        _drop_quota_hold()
    ok = (r13[0] == "user" and s13["state"] == "BLOCKED" and "RECOVERY_EXHAUSTED:quota" in s13.get("block_reason", "")
          and s13["recovery"]["attempts"]["quota"] == 13
          and r8[0] == "user" and s8["state"] == "BLOCKED" and "RECOVERY_EXHAUSTED:quota" in s8.get("block_reason", "")
          and "损坏" not in s8.get("block_reason", "")
          and len(past) == 1 and abs(past[0] - 3600) <= 5 and len(soon) == 1 and 55 <= soon[0] <= 65
          and lim == {"transient": {"window_s": 14400, "attempts": 24}, "quota": {"window_s": 691200, "attempts": 12}}
          and low["quota"]["window_s"] == 3600 and low["transient"]["attempts"] == 3
          and len(waits) == 2 and abs(waits[0] - 1) < 0.05 and abs(waits[1] - 2) < 0.05)
    return ok, {"probe13": s13.get("block_reason", "")[:80], "over_8d": s8.get("block_reason", "")[:80], "policy_30d": lim["quota"],
                "lowered": low, "waits_h": waits, "past_resets_at_wait_s": past, "resets_in_10s_wait_s": soon}


def _p_unknown_pos():
    from . import driver
    pol = {"send_unknown_wait": True, "shared_max_inflight": 1}
    with tempfile.TemporaryDirectory() as td:
        # A：同一发送意图每 120 秒核对一次，共 31 次（第 31 次在第 60 分钟）；期间不耗尽，最后按旧窗口停下
        _fresh_shared()
        a = _unknown_pending(td, "a2-u-31", 9)
        before = _send_fields(_disk(a))
        fake = _Fake(poll=lambda op, args: POLL_NONE)
        acts, ex_seen = [], False
        with _patched(fake):
            for i in range(31):
                with _clock(i * 120):
                    acts.append(driver.h_send(_ctx(a, pol))[0])
                    ex_seen = ex_seen or "RECOVERY_EXHAUSTED" in (a.state.get("block_reason") or "")
                if acts[-1] != "wait":
                    break
        sa = _disk(a)
        it = before["send_intent"]
        # B：第 55 分钟轮询到“生成中”：恢复期结束，不阻塞
        _fresh_shared()
        b = _unknown_pending(td, "a2-u-55", 9)
        fb = _Fake(poll=[POLL_NONE] * 28 + [POLL_GEN])
        with _patched(fb):
            for i in range(28):
                with _clock(i * 120):
                    driver.h_send(_ctx(b, pol))
            with _clock(55 * 60):   # 这一次核对发生在第 55 分钟（不等原定第 56 分钟的退避时刻）
                rb = driver.h_send(_ctx(_due(b), pol))
        sb = _disk(b)
        # C（I6）：只有心跳、accepted:false 都不结束恢复期；经轮询确认已开始才结束
        _fresh_shared()
        c = _sendable(td, "a2-u-acc", 9)
        fc = _Fake(send=[BUSY, {"code": 0, "raw": "", "data": {"accepted": False}}], poll=[POLL_GEN])
        with _patched(fc):
            driver.h_send(_ctx(c, {"send_unknown_wait": True}))
            since_c = _ep(c)["since"]
            driver.h_send(_ctx(_due(c), {"send_unknown_wait": True}))
            open_after_false = _ep(c)
            driver.h_send(_ctx(_due(c), {"send_unknown_wait": True}))
        sc = _disk(c)
        d = _sendable(td, "a2-u-hb", 9)
        fd = _Fake(send=[BUSY, {"code": 0, "raw": "", "data": {"status": "heartbeat_only"}}])
        with _patched(fd):
            driver.h_send(_ctx(d, {"send_unknown_wait": True}))
            since_d = _ep(d)["since"]
            driver.h_send(_ctx(_due(d), {"send_unknown_wait": True}))
        sd = _disk(d)
    hb = (sb.get("recovery_history") or [{}])[-1]
    hc = (sc.get("recovery_history") or [{}])[-1]
    ok = (acts == ["wait"] * 30 + ["user"] and not ex_seen and sa["state"] == "BLOCKED"
          and "[SEND_UNKNOWN_WAIT]" in sa.get("block_reason", "") and "RECOVERY_EXHAUSTED" not in sa.get("block_reason", "")
          and "send" not in fake.calls and fake.calls.count("poll") == 31 and _send_fields(sa) == before
          and sa["recovery"]["attempts"] == {"transient": 1, "quota": 0} and sa["recovery"]["rules"] == {"SEND_UNKNOWN_WAIT": 1}
          and sa["recovery"]["keys"] == [f"{it['turn']}:{it['ts']}"]
          and rb[0] == "progress" and sb["state"] == "WAIT_REPLY" and sb.get("recovery") is None
          and hb.get("closed_by") == "send_accepted" and "send" not in fb.calls
          and open_after_false and open_after_false["since"] == since_c and sc.get("recovery") is None
          and hc.get("closed_by") == "send_accepted" and hc.get("since") == since_c
          and sd.get("recovery") and sd["recovery"]["since"] == since_d and (sd.get("send_intent") or {}).get("status") == "pending")
    return ok, {"checks": len(acts), "stopped_at_min": (len(acts) - 1) * 2, "stop_reason": sa.get("block_reason", "")[:60],
                "exhausted_seen": ex_seen, "send_calls": fake.calls.count("send"), "fields_kept": _send_fields(sa) == before,
                "counted": sa["recovery"]["attempts"], "min55": [rb[0], sb["state"], hb.get("closed_by")],
                "accepted_false_keeps_open": bool(open_after_false), "heartbeat_keeps_open": bool(sd.get("recovery"))}


def _p_unknown_neg():
    from . import driver
    pol = {"send_unknown_wait": True, "shared_max_inflight": 1}
    with tempfile.TemporaryDirectory() as td:
        # 恢复期 since 已在 4 小时前（意图本身是新的）：再核对一次即按恢复期硬上限停下，三项保留、不发送
        _fresh_shared()
        a = _unknown_pending(td, "a2-u-4h", 9)
        before = _send_fields(_disk(a))
        fake = _Fake(poll=[POLL_NONE, POLL_NONE])
        with _patched(fake):
            driver.h_send(_ctx(a, pol))
            a = _rewrite(a, since=_iso(-14400), retry_at=None)
            r = driver.h_send(_ctx(a, pol))
        st = _disk(a)
        # 旧窗口（一小时）先到：按原规则停下，不重发
        _fresh_shared()
        b = _sendable(td, "a2-u-n", 9)
        fb = _Fake(send=[SEND_TIMEOUT], poll=[POLL_NONE])
        with _patched(fb):
            driver.h_send(_ctx(b, {"send_unknown_wait": True}))

            def _old(raw):
                raw["send_intent"]["ts"] = _iso(-7200)
            b = _rewrite(b, _old, retry_at=None)
            rb = driver.h_send(_ctx(b, {"send_unknown_wait": True}))
        sb = _disk(b)
    ok = (r[0] == "user" and st["state"] == "BLOCKED" and "RECOVERY_EXHAUSTED:transient" in st.get("block_reason", "")
          and "损坏" not in st.get("block_reason", "")
          and _send_fields(st) == before and "send" not in fake.calls
          and rb[0] == "user" and sb["state"] == "BLOCKED" and "[SEND_UNKNOWN_WAIT]" in sb.get("block_reason", "")
          and "RECOVERY_EXHAUSTED" not in sb.get("block_reason", "") and fb.calls == ["send", "poll"]
          and (sb.get("send_intent") or {}).get("status") == "pending")
    return ok, {"after_4h": [r[0], st["state"], st.get("block_reason", "")[:80]], "fields_kept": _send_fields(st) == before,
                "send_calls": fake.calls.count("send"), "old_window": [rb[0], sb.get("block_reason", "")[:50]]}


def _p_absent_pos():
    from . import driver, tasks
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "a2-x-p", {"state": "WAIT_SCHEDULE", "turn": 3, "await_executor": 3, "until": _iso(3600)})
        added = tasks.add(a, 3, {"1": "执行端要做的事", "2": "执行端被打断的事"})
        checkpoint = tasks.progress(a, "T3-2", "中断后尚未恢复", generation=added[1]["generation"], kind="interrupted")
        r = driver.h_wait_schedule(_ctx(a, {}))
        st = _disk(a)
        protected = next(t for t in st["executor_tasks"] if t["id"] == "T3-2")
    ok = (r[0] == "wait" and st["state"] == "WAIT_SCHEDULE" and st.get("await_executor") == 3 and "T3-1" in r[1]
          and st.get("recovery") is None and protected == checkpoint and tasks.interrupted(protected)
          and tasks.status_of(protected) == "open" and "T3-2" in r[1])
    return ok, {"ret": list(r), "state": st["state"], "interrupted_kept": protected == checkpoint}


def _p_absent_neg():
    from . import driver, tasks
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "a2-x-n", {"state": "WAIT_SCHEDULE", "turn": 3, "await_executor": 3, "until": _iso(-5)})
        added = tasks.add(a, 3, {"1": "执行端没做完的事", "2": "截止不是恢复也不是完成"})
        checkpoint = tasks.progress(a, "T3-2", "留下执行断点", generation=added[1]["generation"], kind="interrupted")
        r = driver.h_wait_schedule(_ctx(a, {}))
        st = _disk(a)
        notes = "\n".join(p.read_text(encoding="utf-8") for p in a.wf.rglob("notes.jsonl"))
        protected = next(t for t in st["executor_tasks"] if t["id"] == "T3-2")
        rejected = _rej(a, lambda: tasks.done(a, "T3-2", "截止不能算完成", generation=checkpoint["generation"]))
    t = (st.get("executor_tasks") or [{}])[0]
    ok = (st["state"] not in ("BLOCKED", "AWAITING_USER", "WAIT_SCHEDULE") and "await_executor" not in st
          and "T3-1" in notes and tasks.status_of(t) == "open" and "T3-2" in notes
          and protected == checkpoint and tasks.status_of(protected) == "open" and tasks.interrupted(protected)
          and rejected == "unchanged")
    return ok, {"ret": r[0], "state": st["state"], "task_status": tasks.status_of(t), "deviation_lists_task": "T3-1" in notes,
                "interrupted_kept": protected == checkpoint, "interrupted_done": rejected}


# ---- B2-A2b：每次观察都启动新进程，重载磁盘并实际重写临时 inbox ----

def _a2b_child(root, action):
    from . import driver, notify
    from .core import Instance
    inst = Instance(root)
    result = None
    if action == "deadline":
        result = driver.h_wait_schedule(_ctx(inst, {}))
    elif action != "observe":
        raise ValueError(action)
    rows = notify.write_inbox([inst], path=inst.root.parent / (inst.root.name + "-inbox.json"))
    return {"pid": os.getpid(), "result": result, "state": inst.state["state"], "turn": inst.state["turn"],
            "tasks": inst.state.get("executor_tasks") or [],
            "inbox": [r for r in rows if "|task:" in r["key"]]}


def _a2b_restart(inst, action="observe"):
    from .core import WF_HOME
    from .selftest import child_env
    code = ("import json,sys; from wflib.selftest_lane_a import _a2b_child; "
            "print(json.dumps(_a2b_child(sys.argv[1], sys.argv[2]), ensure_ascii=False))")
    r = subprocess.run([sys.executable, "-c", code, str(inst.root), action], cwd=str(WF_HOME),
                       env=child_env(os.environ, os.environ["WF_LOCAL_DIR"]), capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError(f"A2b restart exit={r.returncode}: {r.stdout}\n{r.stderr}")
    return json.loads(r.stdout)


def _a2b_visible(snapshot, task):
    return (next((t for t in snapshot["tasks"] if t["id"] == task["id"]), None) == task
            and any(r["key"].endswith("|task:" + task["id"]) for r in snapshot["inbox"]))


def _p_generation_pos():
    from . import tasks
    from .core import Instance
    with tempfile.TemporaryDirectory() as td:
        a, b = _inst(td, "a2b-gen-a"), _inst(td, "a2b-gen-b")
        ta, tb = [tasks.add(i, 3, {"1": "当前代次任务"})[0] for i in (a, b)]
        rc, out = _wf_cli(a.root, "task", "progress", ta["id"], "--generation", ta["generation"], "--rev", "1",
                          "--note", "真实CLI进程写检查点")
        p = Instance(a.root).state["executor_tasks"][0]
        done = tasks.done(Instance(a.root), ta["id"], "当前代次完成", generation=ta["generation"], expect_rev=2)
        snapshot = _a2b_restart(a)
    ok = (rc == 0 and ta["generation"] != tb["generation"] and len(ta["generation"]) == len(tb["generation"]) == 32
          and ta["project"] == a.name and tb["project"] == b.name and ta["root"] == str(a.root)
          and tb["root"] == str(b.root) and p["rev"] == 2 and done["rev"] == 3
          and tasks.status_of(done) == "done" and snapshot["tasks"] == [done] and snapshot["inbox"] == [])
    return ok, {"cli_exit": rc, "cli_output": out[-200:], "distinct_generation": ta["generation"] != tb["generation"],
                "rev": [ta["rev"], p["rev"], done["rev"]], "restart_pid": snapshot["pid"], "closed_inbox": snapshot["inbox"]}


def _p_generation_neg():
    from . import tasks
    from .core import Instance
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "a2b-gen-reuse")
        old = tasks.add(a, 3, {"1": "将被裁掉的旧任务"})[0]
        tasks.done(a, old["id"], "旧任务已完成", generation=old["generation"])
        for t in tasks.add(a, 4, {str(i): f"关闭记录{i}" for i in range(1, tasks.KEEP_DONE + 2)}):
            tasks.close(a, t["id"], "cancelled", "裁剪负对照", generation=t["generation"])
        tasks.add(a, 5, {"1": "触发只裁已关闭记录"})
        trimmed = all(t["id"] != old["id"] for t in _disk(a)["executor_tasks"])
        new = tasks.add(a, 3, {"1": "复用编号但必须换代次"})[0]
        b = _inst(td, "a2b-gen-other")
        other = tasks.add(b, 3, {"1": "另一项目同编号"})[0]
        rejected = {
            "pruned_generation": _rej(a, lambda: tasks.done(a, new["id"], "迟到旧命令", generation=old["generation"])),
            "other_project": _rej(a, lambda: tasks.done(a, new["id"], "错项目代次", generation=other["generation"])),
            "stale_rev": _rej(a, lambda: tasks.progress(a, new["id"], "旧修订", generation=new["generation"], expect_rev=2)),
        }
        before = _snap(a)
        rc, output = _wf_cli(a.root, "task", "done", new["id"], "--generation", old["generation"], "--note", "真实旧CLI命令")
        cli_unchanged = _snap(a) == before
        snapshot = _a2b_restart(Instance(a.root))
    ok = (trimmed and old["generation"] != new["generation"] and all(v == "unchanged" for v in rejected.values())
          and rc != 0 and cli_unchanged and _a2b_visible(snapshot, new) and tasks.status_of(new) == "open")
    return ok, {"trimmed": trimmed, "new_generation": new["generation"], "rejected": rejected,
                "old_cli_exit": rc, "old_cli_unchanged": cli_unchanged, "old_cli_output": output[-250:],
                "restart_pid": snapshot["pid"]}


def _p_interrupted_pos():
    from . import tasks
    from .core import Instance
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "a2b-interrupted", {"state": "WAIT_SCHEDULE", "turn": 3, "await_executor": 3, "until": _iso(-5)})
        initial = tasks.add(a, 3, {"1": "中断任务跨截止与轮次保留", "2": "已完成记录负对照"})
        protected = tasks.progress(a, "T3-1", "已做第一步；下一步核对结果；证据PROJECT.md",
                                   generation=initial[0]["generation"], kind="interrupted")
        tasks.done(a, "T3-2", "普通任务正常完成", generation=initial[1]["generation"])
        before = _a2b_restart(a)
        after = _a2b_restart(a, "deadline")
        restarted = _a2b_restart(a)
        a = Instance(a.root)
        report = Path(a.state["outgoing"]["file"]).read_text(encoding="utf-8")
        for t in tasks.add(a, 5, {str(i): f"裁剪关闭项{i}" for i in range(1, tasks.KEEP_DONE + 2)}):
            tasks.close(a, t["id"], "cancelled", "只裁已关闭记录", generation=t["generation"])
        tasks.add(a, 6, {"1": "新轮仍保留旧中断任务"})
        pruned = _a2b_restart(a)
        closed = [t for t in pruned["tasks"] if tasks.status_of(t) != "open"]
        snapshots = [before, after, restarted, pruned]
        visible = [_a2b_visible(s, protected) for s in snapshots]
        inbox_rows = [next((r for r in s["inbox"] if r["key"].endswith("|task:T3-1")), {}) for s in snapshots]
    ok = (all(visible) and tasks.status_of(protected) == "open" and tasks.interrupted(protected)
          and before["turn"] == 3 and after["turn"] == restarted["turn"] == pruned["turn"] == 4
          and after["state"] == "COMPOSE" and after["result"][0] == "progress"
          and "仍未完成" in report and "T3-1" in report
          and len(closed) == tasks.KEEP_DONE and all(t["id"] != "T3-2" for t in pruned["tasks"])
          and all(r.get("since") == inbox_rows[0].get("since") for r in inbox_rows)
          and all(s["pid"] != os.getpid() for s in snapshots))
    return ok, {"visible_at_restart_deadline_newturn_prune": visible, "turns": [s["turn"] for s in snapshots],
                "subprocess_pids": [s["pid"] for s in snapshots], "closed_kept": len(closed),
                "ordinary_closed_pruned": all(t["id"] != "T3-2" for t in pruned["tasks"]),
                "report_lists_unfinished": "仍未完成" in report and "T3-1" in report}


def _p_interrupted_neg():
    from . import tasks
    from .core import Instance
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "a2b-interrupted-negative", {"state": "WAIT_SCHEDULE", "turn": 3, "await_executor": 3, "until": _iso(-5)})
        t = tasks.add(a, 3, {"1": "中断后必须显式恢复"})[0]
        tasks.progress(a, t["id"], "被打断", generation=t["generation"], kind="interrupted")
        rejected = {"before_deadline": _rej(a, lambda: tasks.done(a, t["id"], "不能提前完成", generation=t["generation"]))}
        deadline = _a2b_restart(a, "deadline")
        a = Instance(a.root)
        rejected["after_deadline_newturn"] = _rej(a, lambda: tasks.done(a, t["id"], "到期也不是完成", generation=t["generation"]))
        p = tasks.progress(a, t["id"], "普通进度不是恢复", generation=t["generation"])
        before = _snap(a)
        rc, output = _wf_cli(a.root, "task", "done", t["id"], "--generation", t["generation"], "--note", "CLI也不能绕过")
        cli_unchanged = _snap(a) == before
        still_open = _a2b_restart(a)
        resumed = tasks.progress(Instance(a.root), t["id"], "显式恢复后核对完成", generation=t["generation"], kind="resumed")
        done = tasks.done(Instance(a.root), t["id"], "恢复后真实完成", generation=t["generation"])
        closed = _a2b_restart(a)
    ok = (all(v == "unchanged" for v in rejected.values()) and deadline["turn"] == 4
          and tasks.interrupted(p) and tasks.status_of(p) == "open" and _a2b_visible(still_open, p)
          and rc != 0 and cli_unchanged and not tasks.interrupted(resumed) and tasks.status_of(done) == "done"
          and closed["tasks"] == [done] and closed["inbox"] == [])
    return ok, {"done_rejections": rejected, "cli_exit": rc, "cli_unchanged": cli_unchanged,
                "cli_output": output[-250:], "ordinary_progress_still_interrupted": tasks.interrupted(p),
                "explicit_resume_allows_done": tasks.status_of(done), "closed_inbox": closed["inbox"],
                "subprocess_pids": [deadline["pid"], still_open["pid"], closed["pid"]]}


def _find_code(obj, kind="approve"):
    if isinstance(obj, dict):
        if obj.get("code") and kind in str(obj.get("kind", "")):
            return obj["code"]
        obj = list(obj.values())
    if isinstance(obj, list):
        for x in obj:
            c = _find_code(x, kind)
            if c:
                return c
    return None


def _p_cred_pos():
    from . import asks, driver
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "a2-cred-p", {"state": "REVIEW", "turn": 2})
        a.turn_dir().mkdir(parents=True, exist_ok=True)
        (a.turn_dir() / "intake.json").write_text(json.dumps({"commands": ["cat .env"], "directives": {}}), encoding="utf-8")
        pol = {"allow": [r"^cat \.env$"]}
        with _permission_fixture(a, pol):
            r = driver.h_review(_ctx(a, pol))   # 白名单放行也不行：底线“密钥”要用户批准
        st = _disk(a)
        code = _find_code(asks.pending(a)) or next((x.get("code") for x in asks.pending(a) if x.get("code")), None)
        rc_a, oa = _wf_cli(a.root, "approve", "--code", code or "X-0000", "--by", "executor", "--reason", "执行端代批")
        st2 = _disk(a)
    ok = (r[0] == "user" and st["state"] == "AWAITING_USER" and "密钥" in st.get("needs_text", "") and bool(code)
          and rc_a != 0 and st2["state"] == "AWAITING_USER")
    return ok, {"ret": r[0], "state": st["state"], "needs": st.get("needs_text", "")[:80], "code": code,
                "executor_approve_rc": rc_a, "executor_approve_out": oa.strip()[-160:], "state_after": st2["state"]}


def _p_cred_neg():
    from . import driver
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "a2-cred-n", {"state": "REVIEW", "turn": 2})
        a.turn_dir().mkdir(parents=True, exist_ok=True)
        (a.turn_dir() / "intake.json").write_text(json.dumps({"commands": ["echo hi"], "directives": {}}), encoding="utf-8")
        pol = {"allow": [r"^echo hi$"]}
        with _permission_fixture(a, pol):
            r = driver.h_review(_ctx(a, pol))
        st = _disk(a)
        rv = json.loads((a.turn_dir() / "review.json").read_text(encoding="utf-8"))
    ok = st["state"] not in ("AWAITING_USER", "BLOCKED") and rv.get("accepted") is True
    return ok, {"ret": r[0], "state": st["state"], "review_accepted": rv.get("accepted")}


def _p_wrong_pos():
    from . import arbiter, driver, ops
    with tempfile.TemporaryDirectory() as td:
        _fresh_shared()
        b = _inst(td, "a2-stale", {"state": "EXECUTE", "turn": 4, "exec_binding": {"turn": 4, "tool": "0.0.0", "fp": None}})
        b.turn_dir().mkdir(parents=True, exist_ok=True)
        (b.turn_dir() / "intake.json").write_text(json.dumps({"commands": ["echo two"], "directives": {}}), encoding="utf-8")
        pol = {"allow": [r"^echo two$"]}
        with _permission_fixture(b, pol):
            r = driver.h_execute(driver.Ctx(b, pol, time.time() + 120))
        n = len(ops.list_execs(b))
        p1, p2 = _same_name_pair(td, "a2-twin")
        g1 = arbiter.acquire(p1, 1, 3600)
        g2 = arbiter.acquire(p2, 1, 3600)
        hold = _holders(p1.name)
    ok = (r[0] == "progress" and n == 0 and "工具版本" in r[1] and g1["granted"] and not g2["granted"] and not g2["reentry"]
          and [h.get("lease_id") for h in hold] == [g1["lease_id"]] and hold[0].get("root") == str(p1.root))
    return ok, {"plan_stale": r[1][:80], "executed": n, "twin_granted": g2["granted"], "twin_reentry": g2["reentry"],
                "holder_root_is_p1": hold[0].get("root") == str(p1.root) if hold else None}


def _p_wrong_neg():
    from . import arbiter
    with tempfile.TemporaryDirectory() as td:
        _fresh_shared()
        p1, _p2 = _same_name_pair(td, "a2-twin-n")
        g1 = arbiter.acquire(p1, 1, 3600)
        g1b = arbiter.acquire(p1, 1, 3600)
        r = arbiter.release(p1, "done", lease_id=g1["lease_id"], turn=1)
    ok = g1["granted"] and g1b["granted"] and g1b["reentry"] and g1b["lease_id"] == g1["lease_id"] and r["released"]
    return ok, {"reentry": g1b["reentry"], "same_token": g1b["lease_id"] == g1["lease_id"], "released": r["released"]}


def _provider(category, pos, neg):
    def run() -> dict:
        why = _guard()
        out = {"schema": "c1-provider/1", "category": category, "lane": "A", "card": "B2-A2a"}
        for kind, fn in (("positive", pos), ("negative", neg)):
            if why:
                out[kind] = {"ran": False, "ok": False, "evidence": why}
                continue
            try:
                ok, ev = fn()
                out[kind] = {"ran": True, "ok": bool(ok), "evidence": ev}
            except Exception:
                import traceback
                out[kind] = {"ran": True, "ok": False, "evidence": traceback.format_exc()[-1500:]}
            finally:
                _fresh_shared()
        out["ok"] = out["positive"]["ok"] and out["negative"]["ok"]
        return out
    run.__name__ = "provider_" + category.replace("-", "_")
    return run


def _a2b_provider(category, pos, neg):
    """新类别同时满足共享契约和既有 positive/negative 调用方，不改变旧提供方格式。"""
    legacy = _provider(category, pos, neg)

    def run():
        out = legacy()
        out.update(provider="A", card="B2-A2b", error=None)
        out["cases"] = [{"name": category + "-" + kind, "polarity": polarity,
                         "ran": out[kind]["ran"], "ok": out[kind]["ok"],
                         "detail": json.dumps(out[kind]["evidence"], ensure_ascii=False, default=str)}
                        for kind, polarity in (("positive", "pos"), ("negative", "neg"))]
        if not out["ok"]:
            out["error"] = "; ".join(c["detail"] for c in out["cases"] if not c["ok"])
        return out
    run.__name__ = legacy.__name__
    return run


C1_PROVIDERS = {
    "2R-busy": _provider("2R-busy", _p_busy_pos, _p_busy_neg),
    "2R-network": _provider("2R-network", _p_network_pos, _p_network_neg),
    "2R-quota": _provider("2R-quota", _p_quota_pos, _p_quota_neg),
    "2R-send-unknown": _provider("2R-send-unknown", _p_unknown_pos, _p_unknown_neg),
    "2R-executor-absent": _provider("2R-executor-absent", _p_absent_pos, _p_absent_neg),
    "2S-credential": _provider("2S-credential", _p_cred_pos, _p_cred_neg),
    "2S-wrong-project": _provider("2S-wrong-project", _p_wrong_pos, _p_wrong_neg),
    "6b-task-generation": _a2b_provider("6b-task-generation", _p_generation_pos, _p_generation_neg),
    "6b-interrupted": _a2b_provider("6b-interrupted", _p_interrupted_pos, _p_interrupted_neg),
}


def _t_a2_providers(_ok):
    for cat, fn in C1_PROVIDERS.items():
        r = fn()
        _ok(f"B2-A2a 提供方 {cat}：正例与反例都实际运行并通过（c1-provider/1）",
            r["ok"] and r["positive"]["ran"] and r["negative"]["ran"], json.dumps(r, ensure_ascii=False, default=str)[:1800])


def _t_a2b_provider_contract(_ok):
    for cat in ("6b-task-generation", "6b-interrupted"):
        r = C1_PROVIDERS[cat]()
        _ok(f"B2-A2b 提供方 {cat}：cases/provider/error 与旧 positive/negative 双接口都来自实际正反例",
            r["schema"] == "c1-provider/1" and r["provider"] == "A" and r["category"] == cat and r["error"] is None
            and r["ok"] and len(r["cases"]) == 2 and {c["polarity"] for c in r["cases"]} == {"pos", "neg"}
            and all(c["ran"] is True and c["ok"] is True and c["detail"] for c in r["cases"]),
            json.dumps(r, ensure_ascii=False, default=str)[:2200])


@contextmanager
def _permission_case(td, name, command="echo hi", pol=None, state="EXECUTE", accepted=False):
    from . import driver
    inst = _inst(td, name, {"state": state, "turn": 1, "exec_next": 0,
                            "turn_flags": {"review_accepted": accepted}})
    inst.turn_dir().mkdir(parents=True, exist_ok=True)
    (inst.turn_dir() / "intake.json").write_text(
        json.dumps({"commands": [command], "directives": {}}), encoding="utf-8")
    pol = pol if pol is not None else {"allow": [r"^echo hi$"]}
    with _permission_fixture(inst, pol):
        yield inst, driver.Ctx(inst, pol, time.time() + 60)


def _t_permission_consumption(_ok):
    """B1b P1：真实 handler 消费批准字节/注册表；只用临时项目，不调用 transport。"""
    from unittest.mock import patch
    from . import core, driver, ops, policy
    with tempfile.TemporaryDirectory() as td:
        with _permission_case(td, "p1-normal", state="REVIEW") as (a, ctx):
            review = driver.h_review(ctx)
            rec = json.loads((a.turn_dir() / "review.json").read_text(encoding="utf-8"))
            execute = driver.h_execute(ctx)   # 真正执行无副作用的 echo，证明正例不是全拦截。
            _ok("B1b P1 合法空 registry + 当前批准策略：评审与真实 echo 执行通过",
                review[0] == execute[0] == "progress" and rec["accepted"] and rec["rows"][0]["auto"]
                and a.state["exec_next"] == 1 and len(ops.list_execs(a)) == 1,
                str([review, execute, rec]))

        for command, allowed in (("rm -rf build", True), ("rm -rf ../p1-other", False)):
            name = "p1-clean" if allowed else "p1-cross"
            with _permission_case(td, name, command, {"allow": [".*"], "clean_dirs": ["build"]},
                                  state="REVIEW") as (a, ctx):
                (a.root / "build").mkdir()
                other = Path(td) / "p1-other"
                other.mkdir(exist_ok=True)
                # 同名不同根不能按名字去重；目标注册项也可以是尚未接入的普通目录。
                core.REGISTRY.write_text(json.dumps({"projects": [
                    {"name": a.name, "root": str(a.root)}, {"name": a.name, "root": str(other)}]}), encoding="utf-8")
                r = driver.h_review(ctx)
                rec = json.loads((a.turn_dir() / "review.json").read_text(encoding="utf-8"))
                cl = rec["rows"][0]
                a.state["state"] = "EXECUTE"
                a.save()
                with patch.object(ops, "run_exec", return_value=0) as run:
                    ex = driver.h_execute(ctx)
                _ok("B1b P1 review/execute root+others：" + ("合法 clean_dirs 放行" if allowed else "同名另一项目强门禁拒绝"),
                    (cl["auto"] and rec["accepted"] and r[0] == ex[0] == "progress" and run.call_count == 1)
                    if allowed else (not cl["auto"] and not rec["accepted"] and r[0] == ex[0] == "user"
                                     and run.call_count == 0 and any(x["rule"] == "OTHER_PROJECT" for x in cl["strong"])),
                    str([r, ex, cl]))

        bad_registry = ["{broken", "[]", "{}", '{"projects": {}}', '{"projects": [null]}',
                        '{"projects": [{"name": "x", "root": "relative"}]}',
                        '{"projects": [{"name": "", "root": "/tmp/x"}]}', None]
        for state in ("REVIEW", "EXECUTE"):
            for i, text in enumerate(bad_registry):
                with _permission_case(td, "p1-reg-%s-%d" % (state, i), state=state, accepted=True) as (a, ctx):
                    if text is None:
                        core.REGISTRY.unlink()
                    else:
                        core.REGISTRY.write_text(text, encoding="utf-8")
                    with patch.object(ops, "run_exec", return_value=0) as run:
                        r = driver.h_review(ctx) if state == "REVIEW" else driver.h_execute(ctx)
                    _ok("B1b P1 %s 坏/缺 registry %d：不跳过坏行，不退回空表" % (state, i),
                        r[0] == "user" and a.state["state"] == "AWAITING_USER" and run.call_count == 0
                        and "注册表" in a.state.get("needs_text", "") and a.state.get("exec_next", 0) == 0,
                        str(r))
        with _permission_case(td, "p1-reg-io") as (a, ctx):
            original = Path.read_text
            def denied(path, *args, **kwargs):
                if path == core.REGISTRY:
                    raise PermissionError("test unreadable registry")
                return original(path, *args, **kwargs)
            with patch.object(Path, "read_text", denied), patch.object(ops, "run_exec", return_value=0) as run:
                r = driver.h_execute(ctx)
            _ok("B1b P1 registry 真实读取异常按未知归属阻断", r[0] == "user" and run.call_count == 0
                and "注册表" in r[1], str(r))

        malformed = [[], {"allow": ".*"}, {"allow": ["["]}, {"stop": [None]},
                     {"pipe_filters": "cat"}, {"clean_dirs": {}}, {"auto_self_update": "true"}]
        for i, pol in enumerate(malformed):
            with _permission_case(td, "p1-pol-bad-%d" % i, pol=pol) as (a, ctx):
                with patch.object(ops, "run_exec", return_value=0) as run:
                    r = driver.h_execute(ctx)
                _ok("B1b P1 已批准但配置坏 %d 仍失败关闭" % i, r[0] == "user" and run.call_count == 0
                    and "白名单" in r[1], str(r))

        for mutation in ("unapproved", "revoked", "approved_narrower", "symlink"):
            command = "cp source target/file" if mutation == "symlink" else "echo hi"
            with _permission_case(td, "p1-fresh-" + mutation, command, {"allow": [".*"]}, state="REVIEW") as (a, ctx):
                if mutation == "symlink":
                    (a.root / "inside").mkdir()
                    (a.root / "target").symlink_to(a.root / "inside", target_is_directory=True)
                review = driver.h_review(ctx)
                original_save = a.save
                fired = []
                def save():
                    original_save()
                    if a.state.get("exec_inflight") and not fired:
                        fired.append(True)
                        if mutation == "symlink":
                            other = Path(td) / "outside"
                            other.mkdir(exist_ok=True)
                            (a.root / "target").unlink()
                            (a.root / "target").symlink_to(other, target_is_directory=True)
                        elif mutation == "revoked":
                            policy.approvals_path(a).write_text("{}", encoding="utf-8")
                        else:
                            (a.wf / "policy.json").write_text(json.dumps({"allow": ["^false$"]}), encoding="utf-8")
                            if mutation == "approved_narrower":
                                policy.approve(a, "policy", "test deliberate narrowing")
                with patch.object(a, "save", save), patch.object(ops, "run_exec", return_value=0) as run:
                    r = driver.h_execute(ctx)
                _ok("B1b P1 review 后最终 save 期间变化 %s：紧前复核拒绝，不留假 inflight/不推进" % mutation,
                    review[0] == "progress" and fired == [True] and r[0] == "user" and run.call_count == 0
                    and a.state["exec_next"] == 0 and not a.state.get("exec_inflight")
                    and a.state["state"] == "AWAITING_USER", str([review, r, a.state.get("exec_inflight")]))

        for command, skip in (("wf pause", True), ("wf pause; cat .env", False), ("sudo echo hi", False)):
            with _permission_case(td, "p1-manual-%d" % len(command), command, {"allow": [".*"]}) as (a, ctx):
                with patch.object(ops, "run_exec", return_value=0) as run:
                    r = driver.h_execute(ctx)
                _ok("B1b P1 manual 不能遮住底线/strong：" + command,
                    run.call_count == 0 and (r[0] == "progress" and a.state["exec_next"] == 1 if skip
                                            else r[0] == "user" and a.state["exec_next"] == 0), str(r))

        with _permission_case(td, "p1-review-accepted", "sudo echo hi", {"allow": [".*"]},
                              state="REVIEW", accepted=True) as (a, ctx):
            r = driver.h_review(ctx)
            _ok("B1b P1 review_accepted 快捷路径不能绕过当前强门禁", r[0] == "user"
                and a.state["state"] == "AWAITING_USER" and "强门禁" in r[1], str(r))

        for command, allowed in (("echo hi", True), ("rm -rf ../other", False)):
            with _permission_case(td, "p1-health-%s" % allowed, command, {"allow": [".*"]}, state="IDLE") as (a, ctx):
                a.cfg["processes"] = [{"name": "service", "pidfile": "service.pid", "match": "service", "start": command}]
                did, launched = [], []
                original_run = subprocess.run
                def subprocess_run(args, *args2, **kw):
                    if args[:2] == ["bash", "-c"]:
                        launched.append(args)
                        return subprocess.CompletedProcess(args, 0, "ok", "")
                    return original_run(args, *args2, **kw)
                with patch.object(driver, "pid_alive", return_value=False), patch.object(subprocess, "run", subprocess_run):
                    driver._health(ctx, did)
                _ok("B1b P1 health 当前 root/策略：" + ("合法启动可达" if allowed else "跨根强门禁不启动"),
                    (len(launched) == 1 and a.state["state"] == "IDLE") if allowed else
                    (not launched and a.state["state"] == "AWAITING_USER"), str(did))


def _t_p2_message_binding(_ok):
    # Build the negative payload at runtime so tool-source synchronization
    # does not mistake the fixture source for a credential-bearing message.
    secret_payload = "API" + "_KEY=" + "abcdefghijklmnop\n"
    from unittest.mock import patch
    from . import driver, ops
    from .core import now_iso
    with tempfile.TemporaryDirectory() as td:
        a = _sendable(td, "p2-normal", 1)
        pol = {"shared_max_inflight": 0}
        ctx = _ctx(a, pol)
        fake = _Fake(send=SENT_OK)
        with _patched(fake):
            r = driver.h_send(ctx)
        calls = [x for x in fake.calls if x == "send"]
        _ok("B1b P2 正常发送只调用一次且适配器读取绑定快照",
            r[0] == "progress" and calls == ["send"] and a.state["state"] == "WAIT_REPLY"
            and a.state.get("send_intent") is None and a.state.get("last_sent", {}).get("file", "").endswith(".md"),
            str([r, fake.calls, a.state.get("last_sent")]))

        for label, mutate in (
            ("secret", lambda f: f.write_text(secret_payload, encoding="utf-8")),
            ("outside", lambda f: None),
        ):
            b = _sendable(td, "p2-" + label, 1)
            if label == "outside":
                out = Path(td) / "outside.md"
                out.write_text("version v0.1\n档位：high\n", encoding="utf-8")
                b.state["outgoing"]["file"] = str(out)
                b.save()
            else:
                mutate(Path(b.state["outgoing"]["file"]))
            f = _Fake(send=SENT_OK)
            with _patched(f):
                rb = driver.h_send(_ctx(b, pol))
            _ok("B1b P2 新发送前拒绝" + label + "正文且零 send",
                rb[0] == "user" and f.calls.count("send") == 0 and b.state["state"] == "BLOCKED",
                str([rb, f.calls]))

        for label, reason in (("compose-secret", None), ("resend-secret", "resend")):
            e = _sendable(td, "p2-" + label, 1)
            e.state["state"] = "COMPOSE"
            if reason:
                e.state["compose_reason"] = reason
            Path(e.state["outgoing"]["file"]).write_text(
                "【回报 · 第 1 轮 · v0.1】档位：high\n" + secret_payload, encoding="utf-8")
            e.save()
            ef = _Fake(send=SENT_OK)
            with _patched(ef):
                retn = driver.h_compose(_ctx(e, pol))
                if reason and retn[0] == "progress":
                    retn = driver.h_send(_ctx(e, pol))
            _ok("B1b P2 " + label + " 统一密钥硬停",
                retn[0] == "user" and ef.calls.count("send") == 0 and e.state["state"] in ("BLOCKED", "SEND"),
                str([retn, ef.calls]))

        c = _sendable(td, "p2-unknown", 1)
        cctx = _ctx(c, {"shared_max_inflight": 0, "send_unknown_wait": True})
        msg = driver._prepare_send(cctx, c.state["outgoing"])
        c.state["send_intent"] = {"turn": 1, "status": "pending", "ts": now_iso(),
                                  "file": c.state["outgoing"]["file"], "message": msg}
        c.state["outgoing"]["file"] = str(Path(td) / "new-secret.md")
        Path(c.state["outgoing"]["file"]).write_text(secret_payload, encoding="utf-8")
        c.save()
        pf = _Fake(poll=POLL_NONE)
        with _patched(pf, {"poll_since": True}):
            rc = driver.h_send(_ctx(c, {"shared_max_inflight": 0, "send_unknown_wait": True}))
        _ok("B1b P2 未知发送优先旧绑定，不把当前新秘密草稿用于 poll_since",
            rc[0] == "wait" and pf.calls.count("send") == 0,
            str([rc, pf.calls]))

        race = _sendable(td, "p2-race", 1)
        original_save = race.save
        changed = []
        def mutate_after_intent():
            original_save()
            if race.state.get("send_intent") and not changed:
                changed.append(True)
                Path(race.state["outgoing"]["file"]).write_text(
                    secret_payload, encoding="utf-8")
        race.save = mutate_after_intent
        rf = _Fake(send=SENT_OK)
        with _patched(rf):
            rr = driver.h_send(_ctx(race, {"shared_max_inflight": 0}))
        del race.save
        _ok("B1b P2 保存后正文替换：最终绑定复核拒绝且零 send",
            rr[0] == "user" and changed and rf.calls.count("send") == 0,
            str([rr, rf.calls]))

        d = _sendable(td, "p2-legacy", 1)
        d.state["send_intent"] = {"turn": 1, "status": "pending", "ts": now_iso(), "file": d.state["outgoing"]["file"]}
        d.save()
        lf = _Fake(poll=POLL_NONE)
        with _patched(lf, {"poll_since": True}):
            rd = driver.h_send(_ctx(d, {"shared_max_inflight": 0, "send_unknown_wait": True}))
        _ok("B1b P2 legacy 无 ref/绑定硬停且不回退 outgoing",
            rd[0] == "user" and lf.calls.count("poll") == 0 and d.state["state"] == "BLOCKED",
            str([rd, lf.calls]))


# ---- 不变量 ----

def _t_a2_recovery_unit(_ok):
    from . import driver, recovery, tasks
    from .core import WF_HOME, now, parse_iso
    rules = {r.get("id") for r in json.loads((WF_HOME / "anomalies.json").read_text(encoding="utf-8")).get("rules", [])}
    ids = set(recovery.TRANSIENT_RULES + recovery.QUOTA_RULES) <= rules
    cls = [recovery.classify(x) for x in ("SEND_BUSY", "NET", "POLL_NONE", "POLL_READ_ERROR", "SEND_UNKNOWN_WAIT", "QUOTA",
                                          "SEND_UNKNOWN", "NO_REF", None)]
    t0 = now()
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "a2-unit", {"state": "SEND", "turn": 1})
        dls, ex = [], []
        for rule, dt in (("SEND_BUSY", 0), ("NET", 1800), ("QUOTA", 3600), ("QUOTA", 7200), ("SEND_BUSY", 18000),
                         ("NET", 691200 + 1)):
            r = recovery.note(a, rule, detail=rule, now=t0 + timedelta(seconds=dt))
            dls.append(r["episode"]["deadline"])
            ex.append(r["exhausted"])
        ep = a.state["recovery"]
        since = parse_iso(ep["since"])
        i1 = (ep["since"] == t0.isoformat(timespec="seconds") and len(set(dls)) == 2
              and parse_iso(dls[0]) - since == timedelta(seconds=14400) and parse_iso(dls[2]) - since == timedelta(seconds=691200)
              and ep["upgraded_at"] == (t0 + timedelta(seconds=3600)).isoformat(timespec="seconds")
              and ex == [False, False, False, False, False, True] and ep["attempts"] == {"transient": 4, "quota": 2}
              and ep["rules"] == {"SEND_BUSY": 2, "NET": 2, "QUOTA": 2})
        b = _inst(td, "a2-unit-b", {"state": "SEND", "turn": 1})
        caps = [recovery.note(b, "NET", now=t0 + timedelta(seconds=i))["exhausted"] for i in range(25)]
        c = _inst(td, "a2-unit-c", {"state": "SEND", "turn": 1})
        low = [recovery.note(c, "NET", now=t0, pol={"recovery_transient_attempts": 2})["exhausted"] for _ in range(3)]
        bad = []
        for ev in ("progress", "heartbeat_only", "accepted_false", None):
            try:
                recovery.close(b, evidence=ev)
                bad.append(ev)
            except ValueError:
                pass
        still = b.state.get("recovery") is not None
        d = _inst(td, "a2-unit-d", {"state": "SEND", "turn": 1})
        for i in range(12):
            recovery.note(d, "NET", now=t0)
            recovery.close(d, evidence="reply_landed" if i % 2 else "send_accepted")
        hist = d.state.get("recovery_history") or []
        e = _inst(td, "a2-unit-e", {"state": "SEND", "turn": 1})
        seq = []
        for _ in range(12):
            recovery.note(e, "QUOTA", now=t0)
            seq.append((recovery.quota_until(e, None, now=t0) - t0).total_seconds() / 3600)
        fut = recovery.quota_until(e, (t0 + timedelta(hours=3)).isoformat(), now=t0) - t0
        past = recovery.quota_until(e, (t0 - timedelta(hours=3)).isoformat(), now=t0) - t0
        far = recovery.quota_until(e, (t0 + timedelta(days=30)).isoformat(), now=t0)
        junk = recovery.quota_until(e, 12345, now=t0) - t0
        f = _inst(td, "a2-unit-f", {"state": "SEND", "turn": 1, "recovery": {"schema": "x"}})
        cor = recovery.note(f, "NET")
        g = _inst(td, "a2-unit-g", {"state": "VERIFY", "turn": 1})
        recovery.note(g, "NET", now=t0)
        g.save()
        snap = json.dumps(g.state["recovery"], sort_keys=True)
        x = tasks.add(g, 1, {"1": "执行端待办"})[0]
        tasks.progress(g, "T1-1", "做到一半", generation=x["generation"])
        kept = json.dumps(_disk(g).get("recovery"), sort_keys=True) == snap
        on = driver.explain(g).get("recovery")
        h = _inst(td, "a2-unit-h", {"state": "VERIFY", "turn": 1})
        off = "recovery" not in driver.explain(h) and recovery.status(h) is None
    _ok("B2-A2a 规则分类：五条暂时性、一条额度，其余不进恢复期；这些规则编号都确实在 anomalies.json 里",
        ids and cls == ["transient"] * 5 + ["quota", None, None, None], json.dumps([sorted(rules)[:40], cls], ensure_ascii=False))
    _ok("B2-A2a I1 since 不变；截止时刻只在暂时性升级为额度时变一次，且由最初的 since 推出；超过 since 加 8 天即耗尽",
        i1, json.dumps([ep, dls, ex], ensure_ascii=False, default=str)[:1500])
    _ok("B2-A2a 次数上限：暂时性第 25 次耗尽；policy 只能调小（调成 2 次时第 3 次耗尽）",
        caps == [False] * 24 + [True] and low == [False, False, True], json.dumps([caps, low]))
    _ok("B2-A2a 只有 send_accepted 与 reply_landed 能结束恢复期；结束的移入历史，只留最近 10 条",
        bad == [] and still and len(hist) == 10 and all(x.get("closed_by") in recovery.EVIDENCE for x in hist),
        json.dumps([bad, still, len(hist)], ensure_ascii=False))
    _ok("B2-A2a 额度等待：没有、过去或不可解析的 resets_at 都按第 k 次 min(2^(k−1), 24) 小时（1、2、4…，12 次约 8.3 天；"
        "r2 按 MAIN 终裁，过去的时刻不再立即试探）；未来的 resets_at 照用；不越过恢复期截止时刻",
        seq == [1, 2, 4, 8, 16, 24, 24, 24, 24, 24, 24, 24] and fut == timedelta(hours=3) and past == timedelta(hours=24)
        and junk == timedelta(hours=24) and far == parse_iso(e.state["recovery"]["deadline"]),
        json.dumps([seq, str(fut), str(past), str(far), str(junk)], ensure_ascii=False))
    _ok("B2-A2a 恢复期记录损坏按已耗尽处理（只会更严），记录原样保留",
        cor["exhausted"] and f.state.get("recovery") == {"schema": "x"}, json.dumps(cor, ensure_ascii=False, default=str)[:400])
    _ok("B2-A2a I8 执行端写待办、记检查点不碰恢复期；explain 在恢复期中另给一行 recovery，没有时不给",
        kept and bool(on) and "恢复期" in on and off, json.dumps([kept, on, off], ensure_ascii=False))


def _t_a2_old_rules(_ok):
    # I3：计数、退避序列、max 与 then 与旧版逐项相同（期望值直接由未改动的 anomalies.json 推出）
    from . import driver
    from .core import WF_HOME
    rules = {r["id"]: r for r in json.loads((WF_HOME / "anomalies.json").read_text(encoding="utf-8"))["rules"]}

    def expect(rid):
        r = rules[rid]
        b = r["backoff_s"]
        return [b[min(i, len(b) - 1)] for i in range(r["max"])], r["then"]
    got = {}
    with tempfile.TemporaryDirectory() as td:
        _fresh_shared()
        a = _sendable(td, "a2-old-busy", 2)
        texts, acts = [], []
        with _patched(_Fake(send=[BUSY] * 7)):
            for _ in range(7):
                r = driver.h_send(_ctx(_due(a), {}))
                acts.append(r[0])
                texts.append(r[1])
                if a.state["state"] != "SEND":
                    break
        got["SEND_BUSY"] = (_delays(texts[:-1]), _disk(a)["state"], _disk(a)["anomaly_counts"])
        for rid, facts, final in (("NET", {"source": "transport", "code": 4}, "BLOCKED"),
                                  ("POLL_READ_ERROR", {"source": "driver", "kind": "poll_read_error"}, "AWAITING_USER")):
            b = _inst(td, f"a2-old-{rid.lower()}", {"state": "WAIT_REPLY", "turn": 2, "send_ref": "R"})
            mx = rules[rid]["max"]
            outs = [driver._anomaly(_ctx(b, {}), facts, rid) for _ in range(mx + 1)]
            got[rid] = (_delays(x[1] for x in outs[:-1]), _disk(b)["state"], _disk(b)["anomaly_counts"], outs[-1][0])
    eb, tb = expect("SEND_BUSY")
    en, tn = expect("NET")
    ep, tp = expect("POLL_READ_ERROR")
    ok = (got["SEND_BUSY"][0] == eb and tb == "block" and got["SEND_BUSY"][1] == "BLOCKED" and got["SEND_BUSY"][2] == {"2:SEND_BUSY": 7}
          and got["NET"][0] == en and tn == "block" and got["NET"][1] == "BLOCKED" and got["NET"][2] == {"2:NET": 6}
          and got["POLL_READ_ERROR"][0] == ep and tp == "await_user" and got["POLL_READ_ERROR"][1] == "AWAITING_USER"
          and got["POLL_READ_ERROR"][2] == {"2:POLL_READ_ERROR": 13})
    _ok("B2-A2a I3 退避序列、计数、max 与 then 与 anomalies.json 逐项相同（SEND_BUSY 6 次后阻塞、NET 5 次后阻塞、"
        "POLL_READ_ERROR 12 次后转用户），都没有被恢复期提前改写",
        ok, json.dumps([got, eb, en, ep], ensure_ascii=False)[:1500])


def _t_a2_arbiter_identity(_ok):
    # I5：同名不同规范 root 之间不重入、不当作本项目回收、不用旧调用释放、不打外发前标记；同 root 照旧
    from . import arbiter
    with tempfile.TemporaryDirectory() as td:
        _fresh_shared()
        p1, p2 = _same_name_pair(td, "a2-id", 1)
        g1 = arbiter.acquire(p1, 1, 3600)
        g2 = arbiter.acquire(p2, 1, 3600)
        rel_tok = arbiter.release(p2, "冒用", lease_id=g1["lease_id"], turn=1)
        rel_old = arbiter.release(p2, "冒用", turn=1)
        mk = arbiter.mark_sending(p2, g1["lease_id"], 1)
        p2.state["turn"] = 2
        p2.save()
        g2b = arbiter.acquire(p2, 1, 3600)   # 同名另一目录进入新一轮：不能把 p1 的记录当作“本项目旧一轮”回收
        mid = [(h.get("lease_id"), h.get("root")) for h in _holders(p1.name)]
        reclaims = [x for x in _alog() if x.get("event") == "reclaim"]
        g1r = arbiter.acquire(p1, 1, 3600)
        r1 = arbiter.release(p1, "done", lease_id=g1["lease_id"], turn=1)
        g2c = arbiter.acquire(p2, 1, 3600)
        _fresh_shared()
        (_shared() / "arbiter.json").write_text(json.dumps({"holders": [{"project": p1.name, "turn": 1, "since": _iso(0),
                                                                         "lease_id": "b" * 16}], "queue": []}), encoding="utf-8")
        l1 = arbiter.acquire(p1, 1, 3600)
        l2 = arbiter.acquire(p1, 1, 3600)
        nolog = [x for x in _alog() if x.get("event") == "legacy_no_root"]
        legacy = [h.get("lease_id") for h in _holders(p1.name) if not h.get("root")]
        q = _inst(td, "a2-id-q", {"state": "QUOTA_WAIT", "turn": 3, "return_to": "WAIT_REPLY"})
        live_q = arbiter._live({"project": q.name, "turn": 3, "root": str(q.root)}, arbiter.LIVE)
        q.state["return_to"] = None
        q.save()
        stale_q = arbiter._live({"project": q.name, "turn": 3, "root": str(q.root)}, arbiter.LIVE)
    ok = (g1["granted"] and not g2["granted"] and not g2["reentry"] and g2["position"] == 1
          and (rel_tok["released"], rel_tok["reason"]) == (False, "not_held") and not rel_old["released"]
          and (mk["marked"], mk["reason"]) == (False, "not_held") and not g2b["granted"] and mid == [(g1["lease_id"], str(p1.root))]
          and reclaims == [] and g1r["reentry"] and g1r["lease_id"] == g1["lease_id"] and r1["released"]
          and g2c["granted"] and g2c["lease_id"] != g1["lease_id"]
          and not l1["granted"] and not l2["granted"] and len(nolog) == 1 and legacy == ["b" * 16]
          and live_q == "live" and stale_q == "stale")
    _ok("B2-A2a I5 仲裁身份绑定规范 root：同名另一目录不重入、不冒用 token 或旧调用释放、不打外发前标记、进入新一轮也不回收对方；"
        "同 root 重入照旧；没有 root 的旧记录按外部处理且只记一次 legacy_no_root；等额度时按 return_to 判断活跃",
        ok, json.dumps([g2, rel_tok, rel_old, mk, g2b, mid, reclaims, g1r.get("reentry"), g2c.get("granted"), l1.get("granted"),
                        len(nolog), legacy, live_q, stale_q], ensure_ascii=False, default=str)[:1500])


_CC_ARB = r'''
import json, sys, time
sys.path.insert(0, sys.argv[1])
from wflib import arbiter
from wflib.core import Instance
inst, n, start = Instance(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
while time.time() < start:
    time.sleep(0.002)
got, rel, miss = 0, 0, []
for i in range(n):
    g = arbiter.acquire(inst, 1, 3600)
    if g["granted"]:
        got += 1
        time.sleep(0.01)
        r = arbiter.release(inst, "cc", lease_id=g["lease_id"], turn=inst.state.get("turn"))
        rel += 1 if r["released"] else 0
        if not r["released"]:
            miss.append(r["reason"])
    time.sleep(0.004)
print(json.dumps({"got": got, "rel": rel, "miss": miss}))
'''

_CC_REC = r'''
import json, sys, time
sys.path.insert(0, sys.argv[1])
from wflib import driver
from wflib.core import Instance
root, n, start = sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
while time.time() < start:
    time.sleep(0.002)
seen = []
for i in range(n):
    inst = Instance(root)
    driver._anomaly(driver.Ctx(inst, {}, time.time() + 60), {"source": "transport", "code": 4}, "concurrent net %d" % i)
    seen.append(inst.state["recovery"]["since"])
    time.sleep(0.01)
print(json.dumps({"since": seen}))
'''

_CC_TASKS = r'''
import json, sys, time
sys.path.insert(0, sys.argv[1])
from wflib import tasks
from wflib.core import Instance
root, n, start = sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
while time.time() < start:
    time.sleep(0.002)
for i in range(n):
    tasks.add(Instance(root), 5, {str(i + 1): "concurrent task %d" % i})
    time.sleep(0.007)
print(json.dumps({"added": n}))
'''

_CC_ACQ = r'''
import json, sys, time
sys.path.insert(0, sys.argv[1])
from wflib import arbiter
from wflib.core import Instance
inst, n = Instance(sys.argv[2]), int(sys.argv[3])
res = []
for i in range(n):
    g = arbiter.acquire(inst, 1, 3600)
    res.append(bool(g["granted"]))
    if g["granted"]:
        break
    time.sleep(0.01)
print(json.dumps({"granted": res}))
'''


def _children(specs) -> list:
    """同时启动若干真实子进程（各自的解释器、各自的文件锁），返回各自最后一行 JSON 输出。"""
    from .core import WF_HOME
    procs = [subprocess.Popen([sys.executable, "-c", code, str(WF_HOME)] + [str(x) for x in args], cwd=str(WF_HOME),
                              env=dict(os.environ), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for code, args in specs]
    out = []
    for p in procs:
        o, e = p.communicate(timeout=180)
        try:
            out.append(json.loads(o.strip().splitlines()[-1]))
        except (ValueError, IndexError):
            out.append({"error": (e or o)[-800:], "rc": p.returncode})
    return out


def _t_a2_concurrency(_ok):
    from . import driver
    pol = {"shared_max_inflight": 1}
    with tempfile.TemporaryDirectory() as td:
        # 1）同名不同 root 的两个项目在 max=1 下交替 acquire、release
        _fresh_shared()
        p1, p2 = _same_name_pair(td, "a2-cc1", 1)
        r = _children([(_CC_ARB, [p1.root, 25, time.time() + 0.8]), (_CC_ARB, [p2.root, 25, time.time() + 0.8])])
        active, peak, bad = set(), 0, []
        for x in _alog():
            if x.get("event") == "grant":
                active.add(x.get("lease_id"))
                peak = max(peak, len(active))
            elif x.get("event") == "release":
                active.discard(x.get("lease_id"))
            elif x.get("event") in ("reclaim", "release_miss", "mark_miss"):
                bad.append(x)
        c1 = (all("error" not in x for x in r) and r[0].get("got", 0) >= 1 and r[1].get("got", 0) >= 1
              and all(x.get("got") == x.get("rel") and not x.get("miss") for x in r) and peak == 1 and not bad
              and _holders(p1.name) == [])
        _ok("B2-A2a 并发 1：同名不同 root 的两个子进程在 max=1 下交替 acquire、release，互不释放、互不回收，任一时刻至多一个持有者",
            c1, json.dumps([r, peak, bad[:3]], ensure_ascii=False)[:1200])
        # 2）驱动器开恢复期并保存，同时另一进程只写 executor_tasks
        _fresh_shared()
        a = _inst(td, "a2-cc2", {"state": "WAIT_REPLY", "turn": 5, "send_ref": "R"})
        start = time.time() + 0.8
        r = _children([(_CC_REC, [a.root, 4, start]), (_CC_TASKS, [a.root, 6, start])])
        st = _disk(a)
        sinces = r[0].get("since") or []
        ids = sorted(t["id"] for t in st.get("executor_tasks") or [])
        c2 = (len(sinces) == 4 and len(set(sinces)) == 1 and (st.get("recovery") or {}).get("since") == sinces[0]
              and st["recovery"]["attempts"] == {"transient": 4, "quota": 0} and st["recovery"]["rules"] == {"NET": 4}
              and ids == [f"T5-{i}" for i in range(1, 7)] and st.get("anomaly_counts", {}).get("5:NET") == 4)
        _ok("B2-A2a 并发 2（I8）：驱动器开恢复期并保存的同时另一进程只写 executor_tasks：两个键都在，since 不变，计数不丢",
            c2, json.dumps([r, st.get("recovery"), ids, st.get("anomaly_counts")], ensure_ascii=False, default=str)[:1200])
        # 3）恢复期中途驱动器重启：新进程继续累计，since 不变
        b = _inst(td, "a2-cc3", {"state": "WAIT_REPLY", "turn": 5, "send_ref": "R"})
        x1 = _children([(_CC_REC, [b.root, 1, 0])])[0]
        x2 = _children([(_CC_REC, [b.root, 1, 0])])[0]
        sb = _disk(b)
        c3 = (x1.get("since") and x1.get("since") == x2.get("since") and sb["recovery"]["since"] == x1["since"][0]
              and sb["recovery"]["attempts"]["transient"] == 2)
        _ok("B2-A2a 并发 3：恢复期中途驱动器重启（两个先后的子进程），新进程接着累计次数，since 不变、不重开",
            c3, json.dumps([x1, x2, sb.get("recovery")], ensure_ascii=False, default=str)[:900])
        # 4）P 在 QUOTA_WAIT、Q 在租约内反复 acquire：P 的持有记录一直保留；P 回复落地按 token 释放后 Q 才拿到
        _fresh_shared()
        try:
            p = _waiting_reply(td, "a2-cc4-p", 6)
            tok = _disk(p)["send_slot"]["lease_id"]
            reply = Path(td) / "reply-cc4.md"
            reply.write_text("回复正文\n", encoding="utf-8")
            fake = _Fake(poll=[QUOTA_HIT, {"code": 0, "raw": "", "data": {"status": "done", "reply_file": str(reply), "files": []}}])
            with _patched(fake):
                driver.h_wait_reply(_ctx(p, pol))
                in_quota = _disk(p).get("state")
                q = _sendable(td, "a2-cc4-q", 2)
                during = _children([(_CC_ACQ, [q.root, 15])])[0]
                held = [h.get("lease_id") for h in _holders(p.name)]
                p = _rewrite(p, until=_iso(-1))
                driver.h_quota_wait(_ctx(p, pol))
                p.state.pop("poll_after", None)
                p.save()
                landed = driver.h_wait_reply(_ctx(p, pol))
            after = _children([(_CC_ACQ, [q.root, 1])])[0]
            sp = _disk(p)
        finally:
            _drop_quota_hold()
        c4 = (in_quota == "QUOTA_WAIT" and during.get("granted") == [False] * 15 and held == [tok] and landed[0] == "progress"
              and sp["state"] == "INTAKE" and "send_slot" not in sp and sp.get("recovery") is None
              and (sp.get("recovery_history") or [{}])[-1].get("closed_by") == "reply_landed"
              and after.get("granted") == [True] and fake.calls == ["poll", "poll"])
        _ok("B2-A2a 并发 4（I4）：P 在等回复时额度用尽转 QUOTA_WAIT，Q 的子进程在租约内反复 acquire 都拿不到、P 的持有记录一直在；"
            "P 恢复后只轮询，回复落地按 token 释放并结束恢复期，Q 随即拿到",
            c4, json.dumps([in_quota, during, held == [tok], landed, sp.get("state"), after, fake.calls], ensure_ascii=False)[:1200])


# ---------------- B2-A2a r2：MAIN 终裁（额度时刻、同一意图只计一次、硬上限）与独立审查 R1/R2（坏记录失败关闭） ----------------

def _t_a2r2_unknown_key(_ok):
    from . import driver, recovery
    from .core import now
    t0 = now()
    U = "SEND_UNKNOWN_WAIT"
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "a2r2-k1", {"state": "SEND", "turn": 1})
        same = [recovery.note(a, U, key="1:T", now=t0 + timedelta(seconds=i))["exhausted"] for i in range(31)]
        ea = a.state["recovery"]
        b = _inst(td, "a2r2-k25", {"state": "SEND", "turn": 1})
        dist = [recovery.note(b, U, key=f"1:T{i}", now=t0 + timedelta(seconds=i))["exhausted"] for i in range(25)]
        eb = b.state["recovery"]
        c = _inst(td, "a2r2-knone", {"state": "SEND", "turn": 1})
        none = [recovery.note(c, U, key=None, now=t0)["exhausted"] for _ in range(25)]
        d = _inst(td, "a2r2-kbad", {"state": "SEND", "turn": 1})
        for bad in ([], 123, "x" * 121, "", {"k": 1}):
            recovery.note(d, U, key=bad, now=t0)
        ed = d.state["recovery"]
        e = _inst(td, "a2r2-knet", {"state": "SEND", "turn": 1})
        for _ in range(3):
            recovery.note(e, "NET", key="1:T", now=t0)
        ee = e.state["recovery"]
        f = _inst(td, "a2r2-kfull", {"state": "SEND", "turn": 1})
        for i in range(24):
            recovery.note(f, U, key=f"k{i}", now=t0)
        again = recovery.note(f, U, key="k0", now=t0)
        n24 = f.state["recovery"]["attempts"]["transient"]
        new25 = recovery.note(f, U, key="k24", now=t0)
        ef = f.state["recovery"]
        g = _inst(td, "a2r2-kdrv", {"state": "SEND", "turn": 2,
                                    "send_intent": {"turn": 2, "status": "pending", "ts": 12345, "file": "x"}})
        for _ in range(3):
            driver._anomaly(_ctx(_due(g), {"send_unknown_wait": True}), {"source": "driver", "kind": "send_outcome_unknown_wait"},
                            "意图时刻不是文本")
        eg = _disk(g)["recovery"]
    ok_same = (same == [False] * 31 and ea["attempts"] == {"transient": 1, "quota": 0} and ea["rules"] == {U: 1}
               and ea["keys"] == ["1:T"] and ea["last"]["ts"] == (t0 + timedelta(seconds=30)).isoformat(timespec="seconds"))
    ok_dist = dist == [False] * 24 + [True] and eb["attempts"]["transient"] == 25 and len(eb["keys"]) == 24
    ok_none = none == [False] * 24 + [True]
    ok_bad = ed["attempts"]["transient"] == 5 and ed["keys"] == [] and ee["attempts"]["transient"] == 3 and ee["keys"] == []
    ok_full = (not again["exhausted"] and n24 == 24 and new25["exhausted"] and ef["attempts"]["transient"] == 25
               and len(ef["keys"]) == 24)
    ok_drv = eg["attempts"]["transient"] == 3 and eg["keys"] == []
    _ok("B2-A2a r2 同一发送意图只计一次：同一 key 记 31 次只算 1 次、只更新 last；25 个不同 key 第 25 次耗尽；"
        "key 缺失、类型或长度不合法、其他规则带 key 都每次计数，不能绕过预算；记过的 key 最多 24 个",
        ok_same and ok_dist and ok_none and ok_bad and ok_full and ok_drv,
        json.dumps([ok_same, ok_dist, ok_none, ok_bad, ok_full, ok_drv, ea["attempts"], eb["attempts"], len(eb["keys"]),
                    ed["attempts"], ee["attempts"], eg["attempts"]], ensure_ascii=False))


def _t_a2r2_deadline_quota(_ok):
    from . import recovery
    from .core import now, parse_iso
    t0 = now().replace(microsecond=0)
    with tempfile.TemporaryDirectory() as td:
        hard = {}
        for dt in (14399, 14400, 14401):
            a = _inst(td, f"a2r2-d{dt}", {"state": "SEND", "turn": 1})
            recovery.note(a, "NET", now=t0)
            hard[dt] = recovery.note(a, "NET", now=t0 + timedelta(seconds=dt))["exhausted"]
        hq = {}
        for dt in (691199, 691200):
            b = _inst(td, f"a2r2-q{dt}", {"state": "SEND", "turn": 1})
            recovery.note(b, "QUOTA", now=t0)
            hq[dt] = recovery.note(b, "QUOTA", now=t0 + timedelta(seconds=dt))["exhausted"]
        c = _inst(td, "a2r2-qu", {"state": "SEND", "turn": 1})
        recovery.note(c, "QUOTA", now=t0)
        dl = parse_iso(c.state["recovery"]["deadline"])

        def wait(ra, tt=t0):
            return (recovery.quota_until(c, ra, now=tt) - tt).total_seconds()
        cases = {"future_3h": wait((t0 + timedelta(hours=3)).isoformat()), "future_10s": wait((t0 + timedelta(seconds=10)).isoformat()),
                 "past_1h": wait((t0 - timedelta(hours=1)).isoformat()), "y2000": wait("2000-01-01T00:00:00+00:00"),
                 "invalid": wait("not-a-time"), "int": wait(12345), "none": wait(None), "y9999": wait("9999-12-31T23:59:59+00:00")}
        caps = [recovery.quota_until(c, (t0 + timedelta(days=30)).isoformat(), now=t0) == dl,
                recovery.quota_until(c, None, now=dl - timedelta(minutes=30)) == dl, recovery.quota_until(c, None, now=dl) == dl]
        at = recovery.note(c, "QUOTA", now=dl)
        d = _inst(td, "a2r2-qk2", {"state": "SEND", "turn": 1})
        recovery.note(d, "QUOTA", now=t0)
        recovery.note(d, "QUOTA", now=t0)
        k2 = (recovery.quota_until(d, "2000-01-01T00:00:00+00:00", now=t0) - t0).total_seconds()
    ok = (hard == {14399: False, 14400: True, 14401: True} and hq == {691199: False, 691200: True}
          and cases == {"future_3h": 10800, "future_10s": 60, "past_1h": 3600, "y2000": 3600, "invalid": 3600, "int": 3600,
                        "none": 3600, "y9999": 3600}
          and caps == [True, True, True] and at["exhausted"] and "上限" in at["why"] and k2 == 7200)
    _ok("B2-A2a r2 硬上限与额度时刻（MAIN 终裁）：到截止那一刻即耗尽（14399 秒否、14400 与 14401 秒是；8 天同理）；"
        "未来的 resets_at 照用但不短于 60 秒；过去、无效、缺失、越界的都按第 k 次退避（1 小时、2 小时…）；都不越过截止时刻",
        ok, json.dumps([hard, hq, cases, caps, at.get("why"), k2], ensure_ascii=False, default=str))


def _t_a2r2_corrupt(_ok):
    import copy as _cp
    from . import driver, recovery
    from .core import Instance, now, parse_iso
    t = now().replace(microsecond=0)

    def iso(x):
        return x.isoformat(timespec="seconds")
    with tempfile.TemporaryDirectory() as td:
        a = _inst(td, "a2r2-base", {"state": "SEND", "turn": 1})
        recovery.note(a, "NET", detail="基准", now=t - timedelta(minutes=10))
        T = _cp.deepcopy(a.state["recovery"])
        recovery.note(a, "QUOTA", detail="基准", now=t - timedelta(minutes=5))
        Q = _cp.deepcopy(a.state["recovery"])
        since, qdl = parse_iso(T["since"]), parse_iso(Q["deadline"])
        fut = t + timedelta(days=365)

        def m(base, **kv):
            e = _cp.deepcopy(base)
            e.update(kv)
            return e
        V = {
            "class=[]": m(T, **{"class": []}), "class={}": m(T, **{"class": {}}), "class=bogus": m(T, **{"class": "bogus"}),
            "last=[damaged]": m(T, last=["damaged"]), "last=damaged": m(T, last="damaged"), "last=1": m(T, last=1),
            "last=[]": m(T, last=[]), "last 缺字段": m(T, last={"ts": T["last"]["ts"]}),
            "last.ts 早于 since": m(T, last=dict(T["last"], ts=iso(since - timedelta(hours=1)))),
            "last.rule 未计数": m(T, last=dict(T["last"], rule="QUOTA")),
            "未来 since（暂时性 4h）": m(T, since=iso(fut), deadline=iso(fut + timedelta(hours=4)), last=dict(T["last"], ts=iso(fut))),
            "未来 since（额度 8d）": m(Q, since=iso(fut), deadline=iso(fut + timedelta(days=8)), upgraded_at=iso(fut),
                                    last=dict(Q["last"], ts=iso(fut))),
            "since 超出时钟偏差 120 秒": m(T, since=iso(t + timedelta(seconds=120)), deadline=iso(t + timedelta(seconds=120 + 14400)),
                                      last=dict(T["last"], ts=iso(t + timedelta(seconds=120)))),
            "未来 since 且 deadline 早一天": m(T, since=iso(fut), deadline=iso(fut - timedelta(days=1))),
            "未来 since 且 deadline==since": m(T, since=iso(fut), deadline=iso(fut)),
            "deadline 早于 since": m(T, deadline=iso(since - timedelta(days=1))),
            "deadline==since": m(T, deadline=T["since"]),
            "deadline 虚增一年": m(T, since=iso(t - timedelta(hours=5)), deadline=iso(t + timedelta(days=365)),
                                 last=dict(T["last"], ts=iso(t - timedelta(hours=5)))),
            "upgraded_at 早于 since": m(Q, upgraded_at=iso(since - timedelta(hours=1))),
            "upgraded_at 晚于 deadline": m(Q, upgraded_at=iso(qdl + timedelta(hours=1))),
            "upgraded_at 在未来": m(Q, upgraded_at=iso(t + timedelta(days=1))),
            "暂时性带 upgraded_at": m(T, upgraded_at=T["since"]),
            "since=deadline=9999": m(T, since="9999-12-31T23:59:59+00:00", deadline="9999-12-31T23:59:59+00:00"),
            "since 不是文本": m(T, since=12345),
            "attempts 与 rules 不符": m(T, attempts={"transient": 3, "quota": 0}),
            "attempts 为负": m(T, attempts={"transient": -1, "quota": 0}),
            "attempts 是真假值": m(T, attempts={"transient": True, "quota": 0}),
            "attempts 是列表": m(T, attempts=[1, 0]),
            "rules 未知规则": m(T, rules={"NET": 1, "BOGUS": 1}),
            "rules 计数为 0": m(T, rules={"NET": 0}, attempts={"transient": 0, "quota": 0}),
            "rules 是列表": m(T, rules=[["NET", 1]]),
            "keys 过多": m(T, keys=[f"k{i}" for i in range(25)], rules={"NET": 1, "SEND_UNKNOWN_WAIT": 25},
                         attempts={"transient": 26, "quota": 0}),
            "keys 非文本": m(T, keys=[1], rules={"NET": 1, "SEND_UNKNOWN_WAIT": 1}, attempts={"transient": 2, "quota": 0}),
            "keys 重复": m(T, keys=["k", "k"], rules={"NET": 1, "SEND_UNKNOWN_WAIT": 2}, attempts={"transient": 3, "quota": 0}),
            "keys 多于已计次数": m(T, keys=["k1"]),
            "额度类没有额度计数": m(T, **{"class": "quota", "deadline": iso(since + timedelta(days=8))}),
            "未知字段": m(T, extra=1), "id 不合法": m(T, id="x"), "schema 不符": m(T, schema="recovery/0"),
            "顶层是列表": [1, 2], "顶层是文本": "damaged", "顶层是数字": 7,
        }
        res, bad = {}, []
        iso_now = iso(t)
        sentinel = {"send_ref": "R-sentinel", "send_intent": {"turn": 2, "status": "accepted", "ts": iso_now, "file": "x"},
                    "send_slot": {"lease_id": "c" * 16, "turn": 2, "attempt": 1, "granted_at": iso_now},
                    "executor_tasks": [{"id": "T1-1", "turn": 1, "text": "哨兵", "since": iso_now, "done": None, "note": ""}]}
        for i, (name, v) in enumerate(V.items()):
            p = _inst(td, f"a2r2-c{i}", dict({"state": "WAIT_REPLY", "turn": 2, "recovery": v}, **_cp.deepcopy(sentinel)))
            raw0 = json.dumps(_disk(p)["recovery"], sort_keys=True)
            row = {}
            try:
                s_ = recovery.status(p)
                ex_ = driver.explain(p).get("recovery") or ""
                n_ = recovery.note(p, "NET", detail="x")
                row["status"] = bool(s_ and s_.get("corrupt"))
                row["explain"] = "损坏" in ex_
                row["note"] = bool(n_["exhausted"] and n_.get("corrupt")) and json.dumps(p.state.get("recovery"), sort_keys=True) == raw0
                q = Instance(p.root)
                r_ = driver._anomaly(_ctx(q, {}), {"source": "transport", "code": 4}, "网络错误")
                d = _disk(q)
                row["anomaly"] = (r_[0] == "user" and d["state"] == "BLOCKED" and "RECOVERY_EXHAUSTED" in d.get("block_reason", "")
                                  and "损坏" in d.get("block_reason", ""))
                row["kept"] = (json.dumps(d.get("recovery"), sort_keys=True) == raw0
                               and all(d.get(k) == sentinel[k] for k in sentinel))
            except Exception as e:   # noqa: BLE001
                row["raised"] = f"{type(e).__name__}: {e}"[:120]
            res[name] = row
            if row.get("raised") or not all(row.get(k) for k in ("status", "explain", "note", "anomaly", "kept")):
                bad.append(name)
        # 正常记录不得误判：r1 形状（没有 keys）、秒精度、重启、时间推移、合法升级、调小的窗口、60 秒内的时钟偏差
        P = {}
        b1 = _inst(td, "a2r2-p1", {"state": "SEND", "turn": 1})
        recovery.note(b1, "NET")
        r1shape = _cp.deepcopy(b1.state["recovery"])
        r1shape.pop("keys")
        b2 = _inst(td, "a2r2-p1b", {"state": "WAIT_REPLY", "turn": 1, "recovery": r1shape})
        n = recovery.note(b2, "NET")
        P["r1 形状"] = (not recovery.status(b2)["corrupt"] and not n["exhausted"] and n["episode"]["id"] == r1shape["id"]
                       and n["episode"]["since"] == r1shape["since"])
        tt = now() - timedelta(minutes=5)
        micro = {"schema": "recovery/1", "id": "0" * 32, "class": "transient", "since": tt.isoformat(),
                 "deadline": (tt + timedelta(hours=4)).isoformat(), "upgraded_at": None, "attempts": {"transient": 1, "quota": 0},
                 "rules": {"NET": 1}, "last": {"ts": tt.isoformat(timespec="seconds"), "rule": "NET", "detail": ""}}
        b3 = _inst(td, "a2r2-p2", {"state": "WAIT_REPLY", "turn": 1, "recovery": micro})
        P["秒精度"] = not recovery.status(b3)["corrupt"] and not recovery.note(b3, "NET")["exhausted"]
        b4 = _inst(td, "a2r2-p3", {"state": "WAIT_REPLY", "turn": 1})
        recovery.note(b4, "NET")
        b4.save()
        s4 = b4.state["recovery"]["since"]
        n4 = recovery.note(Instance(b4.root), "NET")
        P["重启"] = not n4["exhausted"] and n4["episode"]["since"] == s4 and n4["episode"]["attempts"]["transient"] == 2
        b5 = _inst(td, "a2r2-p4", {"state": "WAIT_REPLY", "turn": 1})
        recovery.note(b5, "NET", now=t)
        later = t + timedelta(days=30)
        n5 = recovery.note(b5, "NET", now=later)
        P["时间推移"] = (not recovery.status(b5, now=later)["corrupt"] and n5["exhausted"] and not n5.get("corrupt")
                        and "上限" in n5["why"])
        b6 = _inst(td, "a2r2-p5", {"state": "WAIT_REPLY", "turn": 1})
        recovery.note(b6, "NET", now=t)
        recovery.note(b6, "QUOTA", now=t + timedelta(hours=1))
        up = b6.state["recovery"]["upgraded_at"]
        recovery.note(b6, "QUOTA", now=t + timedelta(hours=2))
        P["合法升级"] = (not recovery.status(b6, now=t + timedelta(hours=2))["corrupt"] and b6.state["recovery"]["upgraded_at"] == up
                        and b6.state["recovery"]["since"] == iso(t))
        b7 = _inst(td, "a2r2-p6", {"state": "WAIT_REPLY", "turn": 1})
        recovery.note(b7, "NET", pol={"recovery_transient_window_s": 3600})
        P["调小的窗口"] = not recovery.status(b7)["corrupt"]
        sk = t + timedelta(seconds=30)
        skew = {"schema": "recovery/1", "id": "1" * 32, "class": "transient", "since": iso(sk),
                "deadline": iso(sk + timedelta(hours=4)), "upgraded_at": None, "attempts": {"transient": 1, "quota": 0},
                "rules": {"NET": 1}, "last": {"ts": iso(sk), "rule": "NET", "detail": ""}}
        b8 = _inst(td, "a2r2-p7", {"state": "WAIT_REPLY", "turn": 1, "recovery": skew})
        P["30 秒时钟偏差"] = not recovery.status(b8)["corrupt"]
    _ok(f"B2-A2a r2 坏恢复记录失败关闭（{len(V)} 种：class/last 类型、未来 since、矛盾时序、9999 越界、计数关系、keys、未知字段、顶层类型）："
        "status 与 explain 不崩并显示损坏；note 不改写记录；真实 _anomaly 转 RECOVERY_EXHAUSTED 阻塞，原记录与发送三项、待办原样保留",
        not bad, json.dumps({k: res[k] for k in (bad or list(res)[:3])}, ensure_ascii=False)[:1500])
    _ok("B2-A2a r2 正常记录不误判：r1 形状、秒精度、重启、时间推移（30 天后按期限耗尽而非损坏）、合法升级、调小的窗口、60 秒内的时钟偏差",
        all(P.values()), json.dumps(P, ensure_ascii=False))


def _t_a2gpt_intent_identity(_ok):
    """去重只接受当前轮次、非空且可解析的意图时刻；坏身份仍按每次核对计数。"""
    import copy as _cp
    from . import driver
    from .core import now_iso
    ts = now_iso()
    variants = {
        "合法身份": {"turn": 2, "ts": ts},
        "空时刻": {"turn": 2, "ts": ""},
        "非法时刻": {"turn": 2, "ts": "not-a-timestamp"},
        "缺时刻": {"turn": 2},
        "缺轮次": {"ts": ts},
        "错轮次": {"turn": 1, "ts": ts},
        "真假值轮次": {"turn": True, "ts": ts},
        "小数轮次": {"turn": 2.0, "ts": ts},
    }
    results = {}
    with tempfile.TemporaryDirectory() as td:
        for index, (label, identity) in enumerate(variants.items()):
            inst = _inst(td, f"a2gpt-id-{index}", {"state": "SEND", "turn": 2,
                "send_intent": {"status": "pending", **identity}, "send_ref": "synthetic-ref",
                "send_slot": {"turn": 2, "lease_id": "synthetic-token"}})
            protected = _cp.deepcopy(_send_fields(inst.state))
            fake = _Fake()
            with _patched(fake):
                for _ in range(25):
                    result = driver._anomaly(_ctx(_due(inst), {"send_unknown_wait": True}),
                        {"source": "driver", "kind": "send_outcome_unknown_wait"}, "synthetic identity probe")
            st = _disk(inst)
            ep = st["recovery"]
            valid = label == "合法身份"
            expected_keys = [f"2:{ts}"] if valid else []
            passed = (ep["attempts"]["transient"] == (1 if valid else 25) and ep["keys"] == expected_keys
                and st["state"] == ("SEND" if valid else "BLOCKED")
                and result[0] == ("wait" if valid else "user")
                and ("RECOVERY_EXHAUSTED:transient" in result[1]) == (not valid)
                and _send_fields(st) == protected and fake.calls == [])
            results[label] = {"ok": passed, "count": ep["attempts"]["transient"], "state": st["state"],
                              "keys": ep["keys"], "adapter_calls": fake.calls}
    _ok("B2-A2a GPT 最小修复：仅当前整型轮次与非空可解析意图时刻获得去重 key；合法身份 25 次只计 1 次；"
        "空/非法/缺失时刻、缺轮次/错轮次/非整型轮次都逐次计数并在第 25 次耗尽；发送三项不变、适配器调用 0 次",
        all(row["ok"] for row in results.values()), json.dumps(results, ensure_ascii=False))


TESTS = [
    ("同轮授予与精确释放", _t_grant_release), ("先到先得与回收", _t_fifo_reclaim), ("错身份不释放", _t_wrong_identity),
    ("旧记录迁移", _t_legacy), ("坏文件与缺文件", _t_bad_file), ("授予保存外发与落地释放", _t_send_order_and_landing),
    ("失败释放与结果不明", _t_failed_and_unknown), ("0 槽配置", _t_zero_slots), ("落地时租约丢失与缺 token", _t_lost_and_missing),
    ("旧记录不收养", _t_legacy_no_adopt), ("崩溃断点", _t_crash_points), ("收养证明", _t_adopt_proof_table),
    ("结果不明且缺 token", _t_unknown_no_adopt), ("不借 token", _t_no_borrow), ("外发前标记失败", _t_mark_fail),
    ("适配器日志不作证据", _t_log_irrelevant), ("保存失败退回", _t_save_window), ("仲裁文件不可读", _t_unreadable_in_driver),
    ("B2-A1 代次与生命周期", _t_a1_identity), ("B2-A1 两个实例交错", _t_a1_interleave), ("B2-A1 旧缓存锁内校验", _t_a1_stale_cas),
    ("B2-A1 裁剪后同编号", _t_a1_aba), ("B2-A1 旧记录兼容与迁移", _t_a1_legacy), ("B2-A1 事务后缓存刷新", _t_a1_cache),
    ("B2-A1 损坏拒绝写入", _t_a1_corrupt), ("B2-A1 中断、证据与密钥", _t_a1_interrupt),
    ("B2-A1 发送身份不受影响", _t_a1_send_identity), ("B2-A1 命令行", _t_a1_cli),
    ("B2-FIX-A1 R1 复制根目录", _t_a1fix_copy_root), ("B2-FIX-A1 R2 调用方脏字段", _t_a1fix_dirty_caller),
    ("B2-FIX-A1 R3 显式 null", _t_a1fix_null_shape), ("B2-FIX-A1 R4 证据别名", _t_a1fix_evidence_alias),
    ("B2-FIX-A1 R5 坏数据只读", _t_a1fix_bad_shapes), ("B2-FIX-A1 R6 接替者代次", _t_a1fix_successor),
    ("B2-FIX-A1 R7 不适用参数", _t_a1fix_cli_args), ("B2-FIX2-A1 检查点与状态类型", _t_a1fix2_checkpoint_types),
    ("B2-A2a 恢复期单元与不变量", _t_a2_recovery_unit), ("B2-A2a I3 旧规则逐项相同", _t_a2_old_rules),
    ("B2-A2a I5 仲裁身份", _t_a2_arbiter_identity), ("B2-A2a 提供方", _t_a2_providers),
    ("B2-A2a 并发子进程", _t_a2_concurrency),
    ("B2-A2a r2 同一意图只计一次", _t_a2r2_unknown_key), ("B2-A2a r2 硬上限与额度时刻", _t_a2r2_deadline_quota),
    ("B2-A2a r2 坏恢复记录", _t_a2r2_corrupt),
    ("B2-A2a GPT 意图身份校验", _t_a2gpt_intent_identity),
    ("B2-A2b 提供方契约", _t_a2b_provider_contract),
    ("B2-A-IC-B1b P1 权限消费", _t_permission_consumption),
    ("B2-A-IC-B1b P2 正文绑定", _t_p2_message_binding),
]


def run_lane_a(_ok) -> None:
    print("  —— B1-A 发送槽精确持有者 ——", flush=True)
    why = _guard()
    if why:
        _ok("B1-A 隔离前提：只在临时 local/ 里运行", False, why)
        return
    from .core import LOCAL
    sl = LOCAL / "send_log.jsonl"   # 替身发送会追加全局限速记录：结束后恢复原样，不影响聚合器里后面的检查
    before = sl.read_bytes() if sl.exists() else None
    try:
        for name, fn in TESTS:
            try:
                fn(_ok)
            except Exception:   # 一组检查出错只记这一组失败，不中断其余检查
                import traceback
                _ok(f"B1-A 检查组「{name}」运行出错", False, traceback.format_exc()[-1500:])
            finally:
                _fresh_shared()   # 不把仲裁文件留给后面的检查
    finally:
        if before is None:
            if sl.exists():
                sl.unlink()
        else:
            sl.write_bytes(before)


def main() -> int:
    from . import selftest
    if os.environ.get(selftest.CHILD_FLAG) != "1":
        tmp = Path(tempfile.mkdtemp(prefix="wf-lane-a-"))
        try:
            r = subprocess.run([sys.executable, "-m", "wflib.selftest_lane_a"], cwd=str(selftest.WF_HOME),
                               env=selftest.child_env(os.environ, tmp / "local"), timeout=1200)
            return r.returncode
        except subprocess.TimeoutExpired:
            print("B1-A 模块测试子进程超过 1200 秒没有结束，按未通过处理", flush=True)
            return 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    res = []

    def _ok(name, cond, detail=""):
        res.append(bool(cond))
        print(f"  {'✓' if cond else '✗'} {name}" + (f"  —— {detail}" if detail and not cond else ""), flush=True)
        return cond
    run_lane_a(_ok)
    print(f"\nB1-A 模块测试：{sum(res)} / {len(res)} 通过")
    return 0 if res and all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
