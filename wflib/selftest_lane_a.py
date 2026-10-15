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
    from . import driver
    return driver.Ctx(inst, pol, time.time() + 60)


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
        g = arbiter.acquire(a, 1)
        a.state["send_intent"] = {"turn": 4, "status": "pending", "ts": g["granted_at"], "file": a.state["outgoing"]["file"]}
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
        fake = _Fake(send=[SENT_OK])

        def boom():
            raise OSError("磁盘写满（测试替身）")
        a.save = boom
        try:
            with _patched(fake):
                driver.h_send(_ctx(a, pol))
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


TESTS = [
    ("同轮授予与精确释放", _t_grant_release), ("先到先得与回收", _t_fifo_reclaim), ("错身份不释放", _t_wrong_identity),
    ("旧记录迁移", _t_legacy), ("坏文件与缺文件", _t_bad_file), ("授予保存外发与落地释放", _t_send_order_and_landing),
    ("失败释放与结果不明", _t_failed_and_unknown), ("0 槽配置", _t_zero_slots), ("落地时租约丢失与缺 token", _t_lost_and_missing),
    ("旧记录不收养", _t_legacy_no_adopt), ("崩溃断点", _t_crash_points), ("收养证明", _t_adopt_proof_table),
    ("结果不明且缺 token", _t_unknown_no_adopt), ("不借 token", _t_no_borrow), ("外发前标记失败", _t_mark_fail),
    ("适配器日志不作证据", _t_log_irrelevant), ("保存失败退回", _t_save_window), ("仲裁文件不可读", _t_unreadable_in_driver),
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
                               env=selftest.child_env(os.environ, tmp / "local"), timeout=600)
            return r.returncode
        except subprocess.TimeoutExpired:
            print("B1-A 模块测试子进程超过 600 秒没有结束，按未通过处理", flush=True)
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
