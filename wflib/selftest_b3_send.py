"""A-B3-SEND: accepted-intent crash protection and WAIT_REPLY NO_REF without ref/intent.

Every scenario runs the real ``driver.h_send`` / ``_prepare_send`` / ``h_wait_reply``
against the real ``fake_transport.py`` (thin wrappers only strip ``ref`` for the
"adapter accepted without ref" case). Each test gets a fresh temporary LOCAL:
every ``wflib`` module path bound under the original LOCAL is re-pointed, and
real-kill children inherit the same WF_LOCAL_DIR. Adapter send/poll counts come
from the fake adapter's own ``calls.jsonl``. No production send or tick.
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import stat

from . import arbiter, core, driver, machine, policy

ROOT = Path(__file__).resolve().parent.parent
ADAPTER = r'''import json, os, subprocess, sys
fake = os.environ["FAKE_DIR"]
r = subprocess.run([sys.executable, {ft!r}] + sys.argv[1:], capture_output=True, text=True)
out = r.stdout
if sys.argv[1] == "send" and r.returncode == 0 and os.path.exists(os.path.join(fake, "strip_ref")):
    data = json.loads(out)
    data.pop("ref", None)
    out = json.dumps(data) + "\n"
sys.stdout.write(out)
sys.stderr.write(r.stderr)
sys.exit(r.returncode)
'''
KILL_AT_MARK_SENT = ("from wflib import ops\n"
                     "ops.mark_sent = lambda *a, **k: os.kill(os.getpid(), signal.SIGKILL)\n")
KILL_AT_POLL = ("from wflib import anomaly\n"
                "real = anomaly.call\n"
                "def call(op, *a, **k):\n"
                "    if op == 'poll':\n"
                "        os.kill(os.getpid(), signal.SIGKILL)\n"
                "    return real(op, *a, **k)\n"
                "anomaly.call = call\n")


class B3SendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wf-b3-send-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.local = self.base / "local"
        self.local.mkdir()
        old = core.LOCAL
        for name, module in list(sys.modules.items()):
            if name != "wflib" and not name.startswith("wflib."):
                continue
            for attr, value in list(vars(module).items()):
                if isinstance(value, Path):
                    try:
                        rel = value.relative_to(old)
                    except ValueError:
                        continue
                    p = patch.object(module, attr, self.local / rel)
                    p.start()
                    self.addCleanup(p.stop)
        p = patch.dict(os.environ, {"WF_LOCAL_DIR": str(self.local), "WF_NOTIFY": "0"})
        p.start()
        self.addCleanup(p.stop)
        p = patch.dict(core.ACTOR, actor="driver")
        p.start()
        self.addCleanup(p.stop)
        assert core.LOCAL == self.local and driver.LOCAL == self.local and core.REGISTRY == self.local / "registry.json"
        self.fake = self.base / "fake"
        (self.fake / "replies").mkdir(parents=True)
        (self.fake / "replies" / "1.md").write_text("回复正文\n", encoding="utf-8")
        adapter = self.base / "adapter.py"
        adapter.write_text(ADAPTER.format(ft=str(ROOT / "wflib" / "fake_transport.py")), encoding="utf-8")
        for op in ("send", "poll", "quota"):
            w = self.base / f"t-{op}"
            w.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(adapter))} {op} \"$@\"\n")
            w.chmod(0o755)
        self.transport()
        core.REGISTRY.write_text('{"projects": []}', encoding="utf-8")

    # ---------------- fixtures ----------------

    def transport(self, poll_since=False):
        cfg = {"send": str(self.base / "t-send"), "poll": str(self.base / "t-poll"),
               "quota": str(self.base / "t-quota"), "model": "test-model", "timeout_s": 30,
               "env": {"FAKE_DIR": str(self.fake)}}
        if poll_since:
            cfg["poll_since"] = True
        (self.local / "transport.json").write_text(json.dumps(cfg), encoding="utf-8")

    def project(self, name, pol, turn=1):
        root = self.base / name
        (root / "workflow").mkdir(parents=True)
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": name}), encoding="utf-8")
        (root / "PROJECT.md").write_text("# PROJECT.md\n", encoding="utf-8")
        inst = core.Instance(root)
        (inst.wf / "policy.json").write_text(json.dumps(pol), encoding="utf-8")
        policy.approve(inst, "policy", "B3 isolated send fixture")
        reg = json.loads(core.REGISTRY.read_text(encoding="utf-8"))
        reg["projects"].append({"name": name, "root": str(inst.root)})
        core.REGISTRY.write_text(json.dumps(reg), encoding="utf-8")
        f = inst.outbox / f"turn-{turn:03d}.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"【回报 · 第 {turn} 轮 · v0.1】档位：high\n\n{name} 正文\n", encoding="utf-8")
        inst.state.update(state="SEND", turn=turn, outgoing={"turn": turn, "file": str(f), "kind": "report",
                                                             "effort": "high"})
        inst.save()
        return core.Instance(root)

    def ctx(self, inst, pol):
        return driver.Ctx(core.Instance(inst.root), pol, time.time() + 60)

    def disk(self, inst):
        return json.loads(inst.state_path.read_text(encoding="utf-8"))

    def calls(self, op, project=None):
        p = self.fake / "calls.jsonl"
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
        return [r["argv"] for r in rows if r["op"] == op
                and (project is None or r["argv"][r["argv"].index("--project") + 1] == project)]

    def rules(self, inst, prefix=""):
        if not inst.events_path.exists():
            return []
        evs = [json.loads(x) for x in inst.events_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        return [e.get("rule") for e in evs if e.get("type") == "auto" and str(e.get("rule", "")).startswith(prefix)]

    def send_log(self):
        p = self.local / "send_log.jsonl"
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    def kill(self, inst, pol, hook, handler="h_send"):
        code = ("import os, signal, time\nfrom pathlib import Path\nfrom wflib import core, driver\n"
                "core.ACTOR['actor'] = 'driver'\n" + hook +
                f"inst = core.Instance(Path({str(inst.root)!r}))\n"
                f"driver.{handler}(driver.Ctx(inst, {pol!r}, time.time() + 60))\n")
        env = dict(os.environ, PYTHONPATH=str(ROOT), WF_LOCAL_DIR=str(self.local))
        child = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=120)
        self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr.decode(errors="replace")[-2000:])

    def accepted_residue(self, name, pol):
        inst = self.project(name, pol)
        self.kill(inst, pol, KILL_AT_MARK_SENT)
        st = self.disk(inst)
        # Exact window: the channel accepted, _finalize_send saved, mark_sent never ran.
        self.assertEqual((st["state"], st["send_intent"]["status"], st["send_intent"]["logged"]), ("SEND", "accepted", True))
        self.assertTrue(st["send_ref"])
        self.assertEqual(len(self.calls("send", name)), 1)
        return inst

    # ---------------- normal path ----------------

    def test_normal_send_then_reply(self):
        pol = {"first_poll_after_s": 0, "poll_interval_s": 0}
        inst = self.project("normal", pol)
        r = driver.h_send(self.ctx(inst, pol))
        st = self.disk(inst)
        self.assertEqual((r[0], st["state"], st["send_ref"], st["send_intent"]), ("progress", "WAIT_REPLY", "r-normal:1", None))
        self.assertTrue(st["last_sent"]["sent_ts"])
        self.assertEqual(len(self.calls("send")), 1)
        self.assertEqual(driver.h_wait_reply(self.ctx(inst, pol))[0], "wait")
        self.assertEqual(driver.h_wait_reply(self.ctx(inst, pol))[0], "progress")
        self.assertEqual((self.disk(inst)["state"], len(self.calls("send")), len(self.send_log())), ("INTAKE", 1, 1))

    # ---------------- S02a: accepted intent crash protection ----------------

    def test_accepted_residue_after_real_sigkill_never_resends(self):
        pol = {"first_poll_after_s": 0, "poll_interval_s": 0}
        inst = self.accepted_residue("accepted", pol)
        r = driver.h_send(self.ctx(inst, pol))
        st = self.disk(inst)
        self.assertEqual((r[0], st["state"], len(self.calls("send"))), ("progress", "WAIT_REPLY", 1))
        self.assertEqual(len(self.calls("poll")), 1)   # verified by poll, not by a second send
        self.assertIn("SEND_ACCEPTED_UNFINISHED", self.rules(inst))
        self.assertEqual((len(self.send_log()), st["budget"]["rounds"]), (1, 1))   # finalize is not double counted
        self.assertEqual(driver.h_wait_reply(self.ctx(inst, pol))[0], "progress")
        self.assertEqual((self.disk(inst)["state"], len(self.calls("send"))), ("INTAKE", 1))

    def test_accepted_residue_with_arbitration_keeps_lease_and_never_takes_new_one(self):
        pol = {"first_poll_after_s": 0, "poll_interval_s": 0, "shared_max_inflight": 1}
        inst = self.accepted_residue("arb-held", pol)
        token = self.disk(inst)["send_slot"]["lease_id"]
        r = driver.h_send(self.ctx(inst, pol))
        holders = [h["lease_id"] for h in json.loads((self.local / "shared/arbiter.json").read_text())["holders"]]
        self.assertEqual((r[0], self.disk(inst)["state"], len(self.calls("send")), holders), ("progress", "WAIT_REPLY", 1, [token]))
        self.assertEqual(self.disk(inst)["send_slot"]["lease_id"], token)

        pol = dict(pol, shared_max_inflight=2)   # arb-held keeps its slot until its reply lands
        other = self.accepted_residue("arb-lost", pol)
        lost = self.disk(other)["send_slot"]
        arbiter.release(core.Instance(other.root), "test: lease reclaimed", lease_id=lost["lease_id"], turn=1)
        r = driver.h_send(self.ctx(other, pol))
        st = self.disk(other)
        state = json.loads((self.local / "shared/arbiter.json").read_text())
        mine = [h for h in state["holders"] if h.get("project") == "arb-lost"]
        log = [json.loads(x) for x in (self.local / "shared/arbiter.jsonl").read_text().splitlines()]
        self.assertEqual((r[0], st["state"], len(self.calls("send", "arb-lost")), mine), ("progress", "WAIT_REPLY", 1, []))
        self.assertEqual(st["send_slot"], lost)   # the transient grant never replaced the attempt's slot
        self.assertTrue(any(x.get("event") == "release" and x.get("why") == "accepted_intent_no_resend" for x in log))

    # ---------------- S02b: pending / unknown outcome ----------------

    def test_pending_unknown_only_polls_and_recovery_budget_survives_kill(self):
        pol = {"first_poll_after_s": 0, "poll_interval_s": 0, "send_unknown_wait": True}
        inst = self.project("pending", pol)
        (self.fake / "next_send_code").write_text("4")
        r = driver.h_send(self.ctx(inst, pol))
        first = self.disk(inst)
        self.assertEqual((r[0], first["send_intent"]["status"], len(self.calls("send"))), ("wait", "pending", 1))
        episode = {k: first["recovery"][k] for k in ("id", "since", "deadline")}
        for kill in (True, False, False):
            again = core.Instance(inst.root)
            again.state.pop("retry_at", None)
            again.save()
            if kill:
                self.kill(inst, pol, KILL_AT_POLL)
            else:
                self.assertEqual(driver.h_send(self.ctx(inst, pol))[0], "wait")
        st = self.disk(inst)
        self.assertEqual({k: st["recovery"][k] for k in ("id", "since", "deadline")}, episode)
        self.assertEqual(st["send_intent"], first["send_intent"])
        self.assertEqual((len(self.calls("send")), len(self.calls("poll"))), (1, 2))
        self.assertEqual(self.rules(inst, "SEND_UNKNOWN"), ["SEND_UNKNOWN_WAIT"] * 3)

    # ---------------- S03: WAIT_REPLY without send_ref and without send_intent ----------------

    def test_driver_sent_without_ref_is_located_not_no_ref(self):
        pol = {"first_poll_after_s": 0, "poll_interval_s": 0}
        self.transport(poll_since=True)
        (self.fake / "strip_ref").write_text("1")
        inst = self.project("noref-driver", pol)
        self.assertEqual(driver.h_send(self.ctx(inst, pol))[0], "progress")
        st = self.disk(inst)
        # Both fields missing in WAIT_REPLY: no send_ref, no send_intent.
        self.assertEqual((st["state"], st.get("send_ref"), st.get("send_intent")), ("WAIT_REPLY", None, None))
        self.assertEqual(driver.h_wait_reply(self.ctx(inst, pol))[0], "wait")
        self.assertEqual(driver.h_wait_reply(self.ctx(inst, pol))[0], "progress")
        self.assertEqual((st["last_sent"]["message"]["schema"], st["last_sent"]["file"]),
                         ("driver-message/1", st["last_sent"]["message"]["snapshot"]))
        self.assertEqual(len(st["last_sent"]["snapshot_stat"]), 4)
        argv = self.calls("poll")[0]
        self.assertEqual(argv[argv.index("--since") + 1], st["last_sent"]["sent_ts"])
        self.assertEqual(argv[argv.index("--message") + 1], st["last_sent"]["file"])
        self.assertEqual((self.disk(inst)["state"], len(self.calls("send")), self.rules(inst, "NO_REF")), ("INTAKE", 1, []))

    def test_double_missing_stays_bounded_no_ref_and_keeps_markers(self):
        pol = {"first_poll_after_s": 0, "poll_interval_s": 0, "shared_max_inflight": 1}
        (self.fake / "strip_ref").write_text("1")
        inst = self.project("noref-nosince", pol)   # transport has no poll_since: cannot locate
        self.assertEqual(driver.h_send(self.ctx(inst, pol))[0], "progress")
        slot = self.disk(inst)["send_slot"]
        r1 = driver.h_wait_reply(self.ctx(inst, pol))
        a = core.Instance(inst.root)
        self.assertEqual((r1[0], a.state["state"], a.state["await_rule"]), ("user", "AWAITING_USER", "NO_REF"))
        machine.resume(a, "test: bypass approval back to waiting", by="test")
        r2 = driver.h_wait_reply(self.ctx(inst, pol))
        st = self.disk(inst)
        holders = [h["lease_id"] for h in json.loads((self.local / "shared/arbiter.json").read_text())["holders"]]
        self.assertEqual((r2[0], st["state"], st["send_slot"], holders), ("user", "BLOCKED", slot, [slot["lease_id"]]))
        self.assertEqual((self.calls("poll"), len(self.calls("send")), self.rules(inst, "NO_REF")), ([], 1, ["NO_REF", "NO_REF"]))

        self.transport(poll_since=True)
        manual = self.project("manual", pol, turn=4)   # sent outside the driver: no intent, no last_sent
        manual.state.update(state="WAIT_REPLY", sent_at=core.now_iso())
        manual.save()
        r = driver.h_wait_reply(self.ctx(manual, pol))
        self.assertEqual((r[0], self.disk(manual)["await_rule"], self.calls("poll")), ("user", "NO_REF", []))

    # ---------------- A-B3-SEND-FIX: independent review blocks ----------------

    def test_present_but_invalid_intents_stop_without_send_or_lease(self):
        pol = {"first_poll_after_s": 0, "poll_interval_s": 0, "shared_max_inflight": 10}
        ts = core.now_iso()
        cases = {"empty-dict": (1, {}), "empty-list": (1, []), "zero": (1, 0),
                 "bogus-status": (1, {"status": "bogus", "turn": 1, "ts": ts}),
                 "old-turn": (2, {"status": "pending", "turn": 1, "ts": ts}),
                 "missing-status": (1, {"turn": 1, "ts": ts})}
        for name, (turn, value) in cases.items():
            inst = self.project("bad-" + name, pol, turn=turn)
            inst.state["send_intent"] = value
            inst.save()
            r = driver.h_send(self.ctx(inst, pol))
            st = self.disk(inst)
            self.assertEqual((r[0], st["state"], st["send_intent"]), ("user", "BLOCKED", value), name)
            self.assertEqual(self.calls("send", "bad-" + name), [], name)
        arb = self.local / "shared" / "arbiter.json"
        holders = json.loads(arb.read_text())["holders"] if arb.exists() else []
        log = (self.local / "shared" / "arbiter.jsonl")
        events = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        self.assertEqual([h for h in holders if str(h.get("project", "")).startswith("bad-")], [])
        self.assertEqual([e for e in events if str(e.get("project", "")).startswith("bad-")], [])

    def test_last_sent_identity_is_reverified_before_locating(self):
        pol = {"first_poll_after_s": 0, "poll_interval_s": 0, "shared_max_inflight": 10}
        self.transport(poll_since=True)
        (self.fake / "strip_ref").write_text("1")
        for name in ("snapshot-content", "same-name-replacement", "snapshot-mode", "source-content",
                     "last-sent-turn", "last-sent-path"):
            proj = "tamper-" + name
            inst = self.project(proj, pol)
            self.assertEqual(driver.h_send(self.ctx(inst, pol))[0], "progress", name)
            st = self.disk(inst)
            self.assertEqual((st["state"], st.get("send_ref"), st.get("send_intent")), ("WAIT_REPLY", None, None))
            ls = json.loads(json.dumps(st["last_sent"]))
            snap, src = Path(ls["file"]), Path(ls["message"]["source_file"])
            if name == "snapshot-content":
                snap.write_bytes(snap.read_bytes() + b"tampered\n")
            elif name == "same-name-replacement":   # identical bytes and mode, different file
                tmp = snap.with_name(snap.name + ".new")
                tmp.write_bytes(snap.read_bytes())
                os.chmod(tmp, stat.S_IMODE(os.lstat(snap).st_mode))
                os.replace(tmp, snap)
            elif name == "snapshot-mode":
                snap.chmod(0o644)
            elif name == "source-content":
                src.write_bytes(src.read_bytes() + b"x")
            else:
                if name == "last-sent-turn":
                    ls["turn"] = 2
                else:
                    other = snap.with_name("send-" + "0" * 32 + ".md")
                    other.write_bytes(snap.read_bytes())
                    other.chmod(0o600)
                    ls["file"] = str(other)
                a = core.Instance(inst.root)
                a.state["last_sent"] = ls
                a.save()
            kept = self.disk(inst)
            r1 = driver.h_wait_reply(self.ctx(inst, pol))
            a = core.Instance(inst.root)
            self.assertEqual((r1[0], a.state["state"], a.state.get("await_rule")), ("user", "AWAITING_USER", "NO_REF"), name)
            machine.resume(a, "test: bypass approval back to waiting", by="test")
            r2 = driver.h_wait_reply(self.ctx(inst, pol))
            st = self.disk(inst)
            holders = [h["lease_id"] for h in json.loads((self.local / "shared/arbiter.json").read_text())["holders"]
                       if h.get("project") == proj]
            self.assertEqual((r2[0], st["state"]), ("user", "BLOCKED"), name)
            self.assertEqual((st["last_sent"], st["send_slot"], holders),
                             (kept["last_sent"], kept["send_slot"], [kept["send_slot"]["lease_id"]]), name)
            self.assertEqual((self.calls("poll", proj), len(self.calls("send", proj))), ([], 1), name)
            self.assertEqual(self.rules(inst, "NO_REF"), ["NO_REF", "NO_REF"], name)

    # ---------------- cross-project / old generation / old protocol ----------------

    def test_foreign_old_and_legacy_intents_are_never_resent(self):
        pol = {"first_poll_after_s": 0, "poll_interval_s": 0}
        source = self.accepted_residue("source", pol)
        intent = self.disk(source)["send_intent"]
        cases = {"foreign root": (1, intent), "old turn": (2, dict(intent, turn=1)),
                 "legacy no status": (1, {k: v for k, v in intent.items() if k != "status"})}
        for name, (turn, it) in cases.items():
            inst = self.project("target-" + name.replace(" ", "-"), pol, turn=turn)
            inst.state["send_intent"] = it
            inst.save()
            r = driver.h_send(self.ctx(inst, pol))
            st = self.disk(inst)
            self.assertEqual((r[0], st["state"], st["send_intent"]), ("user", "BLOCKED", it), name)
        self.assertEqual(len(self.calls("send")), 1)


def run_unittests(_ok):
    """Aggregator adapter, same shape as the C5 adapters; not registered here."""
    import io
    from contextlib import redirect_stderr, redirect_stdout
    outcomes = []

    class Result(unittest.TestResult):
        def addSuccess(self, test):
            super().addSuccess(test)
            outcomes.append(("A-B3-SEND: " + test._testMethodName, True, ""))

        def addFailure(self, test, error):
            super().addFailure(test, error)
            outcomes.append(("A-B3-SEND: " + test._testMethodName, False, self._exc_info_to_string(error, test)))

        addError = addFailure

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        unittest.defaultTestLoader.loadTestsFromTestCase(B3SendTests).run(Result())
    for outcome in outcomes:
        _ok(*outcome)


if __name__ == "__main__":
    unittest.main()
