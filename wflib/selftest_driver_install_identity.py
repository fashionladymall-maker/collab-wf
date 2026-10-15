"""C5-D1: the driver's own in-process installation carries a real exec identity.

Every test runs the real ``driver.h_apply`` against a real reviewed package with
explicitly synthetic runner/verifier fixtures, then reads the evidence only
through C's unchanged ``live``/``live_history`` protocol. Synthetic provenance
never becomes a production grant. Export ``run_unittests(_ok)`` lets an
aggregator owner register this suite without this module touching one.
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from . import core, driver, driver_review, live, live_history, ops, release, review
from . import selftest_c5_integration as c5
from . import selftest_install_history as history_tests
from .selftest import _project_md
from .selftest_review import AUTHOR_SESSION


def _events(inst):
    return [json.loads(line) for line in inst.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]


# Real SIGKILL in the protected consumer, after part of the tree is written.
KILL_IN_COPY = (
    "real = release._copy_tree\n"
    "def die(*a, **k):\n"
    "    real(*a, **k)\n"
    "    os.kill(os.getpid(), signal.SIGKILL)\n"
    "release._copy_tree = die\n")
# Real SIGKILL in the exact commit window: finish() has written end/exit 0
# metadata for the in-process invocation, its exec event is not yet appended.
KILL_BEFORE_EXEC_EVENT = (
    "real = ops._exec_event\n"
    "def die(inst, meta, by, **extra):\n"
    "    if meta.get('runner') == 'in-process':\n"
    "        os.kill(os.getpid(), signal.SIGKILL)\n"
    "    return real(inst, meta, by, **extra)\n"
    "ops._exec_event = die\n")


class DriverInstallIdentityTests(unittest.TestCase):
    project = c5.C5IntegrationTests.project
    chain = c5.C5IntegrationTests.chain

    def setUp(self):
        c5.C5IntegrationTests.setUp(self)

    # ---------------- fixtures ----------------

    def staged(self, name="driver project"):
        inst = self.project(name)
        package = self.env.pkg("v0.2-" + name.replace(" ", "-"), {
            "PROJECT.md": _project_md("v0.2").encode(),
            "CHANGELOG.md": b"# CHANGELOG\n\n## v0.2 \xe2\x80\x94 test\n- installed\n",
            "app.py": b"v0.2",
        })
        inst.state.update(state="APPLY", turn=1, release_path=str(package), conv={"conv": AUTHOR_SESSION})
        inst.save()
        return inst, package

    def apply(self, inst):
        return driver.h_apply(driver.Ctx(core.Instance(inst.root), {}, time.time() + 120))

    def reapply(self, inst):
        fresh = core.Instance(inst.root)
        fresh.state["state"] = "APPLY"
        fresh.save()
        return self.apply(fresh)

    def finish_round(self, inst, commands):
        """Same report-side inputs as C's fixture; installation inputs stay real."""
        from datetime import timedelta
        at = core.now()
        sent, done = at - timedelta(minutes=1), at + timedelta(seconds=2)
        iso = lambda value: value.isoformat()
        for kind, fields in (("reply_binding", {"receipt": "ok", "deliveries": "ok"}),
                             ("reply_checked", {}), ("round_result", {"ok": True})):
            inst.event(kind, by="driver", **fields)
        inst.turns_path.write_text(
            json.dumps({"turn": 1, "status": "sent", "from": "driver", "ts_sent": iso(sent)}) + "\n"
            + json.dumps({"turn": 1, "status": "done", "ts_done": iso(done)}) + "\n", encoding="utf-8")
        td = inst.turn_dir()
        for name, doc in (("intake.json", {"commands": commands}),
                          ("send_meta.json", {"message_uuid": "d1-parent"}),
                          ("reply_meta.json", {"parent": "d1-parent", "ended_at": iso(done)})):
            (td / name).write_text(json.dumps(doc), encoding="utf-8")
        if not (td / "notes.jsonl").exists():
            (td / "notes.jsonl").write_bytes(b"")
        adapter = self.base / "adapter.jsonl"
        adapter.write_text(json.dumps({"action": "send", "turn": 1, "ts": iso(sent),
                                       "project": inst.name, "root": str(inst.root)}) + "\n", encoding="utf-8")
        transport = core.LOCAL / "transport.json"
        cfg = json.loads(transport.read_bytes())
        cfg["log"] = str(adapter)
        transport.write_text(json.dumps(cfg), encoding="utf-8")
        from .selftest_review import _git
        head = _git(inst.root, "rev-parse", "HEAD")
        sync = core.LOCAL / "sync" / (inst.name + ".history.jsonl")
        sync.parent.mkdir(parents=True, exist_ok=True)
        sync.write_text(json.dumps({"turn": 1, "status": "pushed", "commit": head, "head": head,
                                    "ts": iso(done), "project": inst.name, "root": str(inst.root)}) + "\n",
                        encoding="utf-8")

    def histories(self, inst):
        return live_history.collect_bound_histories(inst, _events(inst), history_tests.Reader(),
                                                    local=core.LOCAL, tool_root=self.env.tool)

    def driver_execs(self, inst):
        return [e for e in _events(inst) if e["type"] == "exec" and e.get("cmd", "").startswith(ops.INPROCESS_PREFIX)]

    def tx_dirs(self, inst):
        return sorted(p.name for p in (inst.evidence / "install").glob("tx-*"))

    def kill_driver(self, inst, hook):
        code = (
            "import os, signal, time\nfrom pathlib import Path\n"
            "from wflib import core, anomaly, review, release, driver, live, ops\n"
            f"core.LOCAL = anomaly.LOCAL = live.LOCAL = Path({str(core.LOCAL)!r})\n"
            f"review._tool_home = lambda: Path({str(self.env.tool)!r})\n"
            "core.ACTOR['actor'] = 'driver'\n" + hook +
            f"inst = core.Instance(Path({str(inst.root)!r}))\n"
            "driver.h_apply(driver.Ctx(inst, {}, time.time() + 120))\n")
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent), WF_LOCAL_DIR=str(core.LOCAL))
        child = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=120)
        self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr.decode(errors="replace")[-2000:])

    # ---------------- positive: real h_apply through C5 ----------------

    def test_real_h_apply_history_chain_is_valid_and_synthetic_never_grants(self):
        inst, _ = self.staged()
        result = self.apply(inst)
        self.assertEqual(result[0], "progress", result)
        inst = core.Instance(inst.root)
        self.assertEqual((inst.root / "app.py").read_text(), "v0.2")
        [exec_event] = self.driver_execs(inst)
        [apply_event] = [e for e in _events(inst) if e["type"] == "apply"]
        # The identity was allocated before the protected consumer and handed to
        # it explicitly; prepared.exec, the receipt and both events agree.
        invocation = exec_event["install_exec"]
        self.assertEqual((exec_event["by"], exec_event["actor"], exec_event["exit_code"]), ("driver", "driver", 0))
        self.assertEqual(apply_event["install_exec"], invocation)
        self.assertEqual(exec_event["install_refs"], [apply_event["install_ref"]])
        tx = inst.evidence / "install" / apply_event["install_ref"]["tx_id"]
        self.assertEqual(json.loads((tx / "prepared.json").read_bytes())["exec"], invocation)
        meta = json.loads((inst.turn_dir() / (exec_event["cid"] + ".json")).read_bytes())
        self.assertEqual((meta["runner"], meta["label"], meta["install_exec"]), ("in-process", "apply", invocation))
        self.assertNotIn("WF_INSTALL_EXEC_CONTEXT", os.environ)
        closed = json.loads(next((inst.evidence / "install/driver-review").glob("*-closed.json")).read_bytes())
        installing = json.loads(next((inst.evidence / "install/driver-review").glob("*-installing.json")).read_bytes())
        self.assertEqual((closed["install_exec"], installing["install_exec"]), (invocation, invocation))
        self.assertEqual(closed["install_ref"], apply_event["install_ref"])

        self.chain(inst=inst, kind="tool", review_days=1)
        inst = core.Instance(inst.root)
        self.finish_round(inst, ["protected tool install"])
        snap = live._capture_inputs(inst, rounds=1)
        evaluated = live._evaluate_snapshot(snap)
        self.assertTrue(evaluated["report"]["summary"]["passed_strict"], evaluated["report"])
        self.assertTrue(evaluated["report"]["rounds"][-1]["applies"])
        entries = evaluated["window_inputs"]["review_inputs"]["entries"]
        self.assertEqual(sorted(e["reference"]["target"]["kind"] for e in entries), ["project", "tool"])
        self.assertTrue(all(e["state"] == "valid" for e in entries), entries)
        self.assertEqual(evaluated["history_authorization"],
                         {"eligible": False, "complete": True, "synthetic": True, "why": ["synthetic_install_history"]})
        self.assertEqual(evaluated["authorization"], "unavailable")
        self.assertTrue(live._revalidate_inputs(inst, snap.descriptor)["valid"])
        with patch.object(review, "_read_bytes", side_effect=AssertionError("global read")), \
             patch.object(review, "consume", side_effect=AssertionError("install write")):
            self.assertEqual(live._evaluate_snapshot(snap), evaluated)
        # The in-process receipt is held to the same raw-byte protocol.
        receipt = inst.turn_dir() / (exec_event["cid"] + ".install.jsonl")
        raw = receipt.read_bytes()
        receipt.chmod(0o600)
        receipt.write_bytes(raw.replace(b'"prepared_sha256":"', b'"prepared_sha256":"0', 1))
        self.assertEqual(live.check(inst, rounds=1)["input_problem"]["state"], "corrupt")
        receipt.write_bytes(raw)

    # ---------------- failure / interruption visibility ----------------

    def test_explicit_identity_and_inherited_env_are_mutually_exclusive(self):
        inst, _ = self.staged("ambiguous")
        foreign = {"schema": "install-exec/1", "subject": {"project": inst.name, "root": str(inst.root)},
                   "turn": 1, "exec": {"id": "c99", "nonce": "f" * 32}}
        with patch.dict(os.environ, {"WF_INSTALL_EXEC_CONTEXT": json.dumps(foreign)}):
            result = self.apply(inst)
        self.assertEqual(result[0], "user")
        self.assertIn("install_exec_ambiguous", result[1])
        self.assertEqual(self.tx_dirs(inst), [])
        self.assertFalse((inst.root / "app.py").exists())   # baseline has no app.py
        [event] = self.driver_execs(core.Instance(inst.root))
        self.assertEqual((event["exit_code"], event["install_refs"]), (1, []))
        [entry] = self.histories(core.Instance(inst.root))["entries"]
        self.assertEqual((entry["state"], entry["diagnostics"]),
                         ("unknown", ["unsuccessful_exec_has_no_transaction_reference"]))

    def test_failed_install_is_recorded_and_never_reinstalled(self):
        inst, _ = self.staged("failed")
        with patch.object(release._InstallTx, "complete", side_effect=core.WfError("injected completion failure")):
            result = self.apply(inst)
        self.assertEqual(result[0], "user")
        self.assertFalse((inst.root / "app.py").exists())   # baseline has no app.py
        txs = self.tx_dirs(inst)
        self.assertEqual(len(txs), 1)
        self.assertTrue((inst.evidence / "install" / txs[0] / "recovered.json").exists())
        [event] = self.driver_execs(core.Instance(inst.root))
        self.assertEqual(event["exit_code"], 1)
        self.assertEqual([ref["tx_id"] for ref in event["install_refs"]], txs)
        histories = self.histories(core.Instance(inst.root))
        self.assertEqual([e["state"] for e in histories["entries"]], ["failed"])
        why = live_history.window_authorization(histories, _events(inst), {"rounds": [{"turn": 1}]})["why"]
        self.assertIn("install_history:failed", why)
        again = self.reapply(inst)
        self.assertEqual(again[0], "user")
        self.assertIn("driver_review_incomplete", again[1])
        self.assertEqual(self.tx_dirs(inst), txs)
        self.assertEqual(len(self.driver_execs(core.Instance(inst.root))), 1)

    def test_keyboard_interrupt_is_recorded_once(self):
        inst, _ = self.staged("interrupted")
        real = release._copy_tree

        def interrupt(*args, **kwargs):
            real(*args, **kwargs)
            raise KeyboardInterrupt()

        with patch.object(release, "_copy_tree", interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.apply(inst)
        [event] = self.driver_execs(core.Instance(inst.root))
        self.assertEqual((event["exit_code"], event["interrupted"], len(event["install_refs"])), (130, True, 1))
        self.assertFalse((inst.root / "app.py").exists())   # baseline has no app.py
        txs = self.tx_dirs(inst)
        again = self.reapply(inst)
        self.assertIn("driver_review_incomplete", again[1])
        self.assertEqual((self.tx_dirs(inst), len(self.driver_execs(core.Instance(inst.root)))), (txs, 1))
        [entry] = self.histories(core.Instance(inst.root))["entries"]
        self.assertTrue(live_history.entry_problem(entry))

    def test_killed_driver_leaves_visible_invocation_closed_once_without_reinstall(self):
        inst, _ = self.staged("killed")
        self.kill_driver(inst, KILL_IN_COPY)
        orphans = [json.loads(p.read_bytes()) for p in inst.turn_dir().glob("c*.json")]
        self.assertEqual([(m["runner"], "end" in m) for m in orphans], [("in-process", False)])
        self.assertEqual(self.driver_execs(core.Instance(inst.root)), [])
        txs = self.tx_dirs(inst)
        self.assertEqual(len(txs), 1)
        first = self.reapply(inst)
        self.assertEqual(first[0], "user")
        self.assertIn("driver_review_incomplete", first[1])
        self.assertIn(orphans[0]["id"], first[1])
        [event] = self.driver_execs(core.Instance(inst.root))
        self.assertEqual((event.get("exit_code"), event["interrupted"], event["recovered"]), (None, True, True))
        self.assertEqual(event["install_exec"], orphans[0]["install_exec"])
        self.assertEqual(len(event["install_refs"]), 1)
        second = self.reapply(inst)
        self.assertEqual(second[0], "user")
        self.assertEqual(len(self.driver_execs(core.Instance(inst.root))), 1)
        self.assertEqual(self.tx_dirs(inst), txs)
        [entry] = self.histories(core.Instance(inst.root))["entries"]
        self.assertTrue(live_history.entry_problem(entry))

    def test_kill_after_success_metadata_before_exec_event_is_never_success(self):
        inst, _ = self.staged("commit window")
        self.kill_driver(inst, KILL_BEFORE_EXEC_EVENT)
        [meta] = [json.loads(p.read_bytes()) for p in inst.turn_dir().glob("c*.json")]
        # The exact window: success metadata is on disk, the exec event is not.
        self.assertEqual((meta["runner"], meta["exit_code"], meta["interrupted"], "end" in meta),
                         ("in-process", 0, None, True))
        self.assertEqual(self.driver_execs(core.Instance(inst.root)), [])
        txs = self.tx_dirs(inst)
        self.assertTrue((inst.evidence / "install" / txs[0] / "completed.json").exists())
        first = self.reapply(inst)
        self.assertEqual(first[0], "user")
        self.assertIn("driver_review_incomplete", first[1])
        self.assertIn("install_exec_interrupted", first[1])
        [event] = self.driver_execs(core.Instance(inst.root))
        self.assertEqual((event.get("exit_code"), event["interrupted"], event["recovered"], event["turn"]),
                         (None, True, True, 1))
        recovered = json.loads((inst.turn_dir() / (meta["id"] + ".json")).read_bytes())
        self.assertEqual((recovered["exit_code"], recovered["interrupted"]), (None, True))
        self.assertEqual((recovered["unconfirmed"]["exit_code"], recovered["unconfirmed"]["end"]), (0, meta["end"]))
        histories = self.histories(core.Instance(inst.root))
        [entry] = histories["entries"]
        self.assertEqual(entry["state"], "valid")   # the transaction itself completed
        self.assertEqual(live_history.entry_problem(entry), "installation_invocation_unsuccessful")
        why = live_history.window_authorization(histories, _events(inst), {"rounds": [{"turn": 1}]})["why"]
        self.assertIn("install_history:installation_invocation_unsuccessful", why)
        self.assertIn("apply_install_reference_unavailable", why)
        self.reapply(inst)
        self.assertEqual(len(self.driver_execs(core.Instance(inst.root))), 1)
        self.assertEqual(self.tx_dirs(inst), txs)

    def test_orphan_from_earlier_turn_is_recorded_before_next_attempt(self):
        inst, package = self.staged("cross turn")
        self.kill_driver(inst, KILL_BEFORE_EXEC_EVENT)
        [meta] = [json.loads(p.read_bytes()) for p in inst.turn_dir().glob("c*.json")]
        later = core.Instance(inst.root)
        later.state.update(state="APPLY", turn=2)
        later.save()
        for _ in range(2):
            with driver_review.Attempt(core.Instance(inst.root), package):
                pass
        [event] = self.driver_execs(core.Instance(inst.root))
        self.assertEqual((event["turn"], event["install_exec"], event.get("exit_code"), event["interrupted"]),
                         (1, meta["install_exec"], None, True))
        [entry] = self.histories(core.Instance(inst.root))["entries"]
        self.assertEqual((entry["turn"], live_history.entry_problem(entry)), (1, "installation_invocation_unsuccessful"))

    # ---------------- consumer parameter contract ----------------

    def test_exec_receipt_contract(self):
        root = self.base / "receipt"
        root.mkdir()
        context = {"schema": "install-exec/1", "subject": {"project": "p", "root": str(root)},
                   "turn": 1, "exec": {"id": "c01", "nonce": "a" * 32}}
        path = root / "c01.install.jsonl"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            receipt = release.ExecReceipt(context, fd)
            ref = {"schema": "install-ref/1", "tx_id": "tx-" + "0" * 32}
            receipt.emit(ref)
            with self.assertRaisesRegex(core.WfError, "install_exec_reused"):
                receipt.emit(ref)
            for bad in (dict(context, extra=1), dict(context, exec={"id": "x1", "nonce": "a" * 32}),
                        dict(context, exec={"id": "c01", "nonce": "A" * 32}), dict(context, turn=0)):
                with self.assertRaisesRegex(core.WfError, "install_exec_invalid"):
                    release.ExecReceipt(bad, fd)
        finally:
            os.close(fd)
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(rows, [{"schema": "install-exec-receipt/1", "exec": context, "install_ref": ref}])
        read, write = os.pipe()
        try:
            with self.assertRaisesRegex(core.WfError, "install_exec_invalid"):
                release.ExecReceipt(context, write)
        finally:
            os.close(read)
            os.close(write)


def run_unittests(_ok):
    """Aggregator adapter (same shape as lane C's C5 adapter); not registered here."""
    import io
    from contextlib import redirect_stderr, redirect_stdout
    outcomes = []

    class Result(unittest.TestResult):
        def addSuccess(self, test):
            super().addSuccess(test)
            outcomes.append(("C5-D1 driver install identity: " + test._testMethodName, True, ""))

        def addFailure(self, test, error):
            super().addFailure(test, error)
            outcomes.append(("C5-D1 driver install identity: " + test._testMethodName, False,
                             self._exc_info_to_string(error, test)))

        addError = addFailure

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        unittest.defaultTestLoader.loadTestsFromTestCase(DriverInstallIdentityTests).run(Result())
    for outcome in outcomes:
        _ok(*outcome)


if __name__ == "__main__":
    unittest.main()
