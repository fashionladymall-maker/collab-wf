"""C5 integration with real exec receipts and protected synthetic installs."""
from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from . import core, goals, live, ops, policy, review
from . import selftest_install_history as history_tests


class C5IntegrationTests(unittest.TestCase):
    project = history_tests.HistoryTests.project
    approved = history_tests.HistoryTests.approved
    chain = history_tests.HistoryTests.chain

    def setUp(self):
        history_tests.HistoryTests.setUp(self)
        # Match the ordinary tool checkout's local-state ignore without adding
        # an installation-owned source file to B's minimal package fixture.
        exclude = self.env.tool / ".git/info/exclude"
        exclude.write_bytes(exclude.read_bytes() + b"\n/local/\n")
        for module, name, value in ((live, "LOCAL", core.LOCAL), (core, "WF_HOME", self.env.tool),
                                    (goals, "_TOOL_ROOT", self.env.tool)):
            handle = patch.object(module, name, value)
            handle.start()
            self.addCleanup(handle.stop)
        handle = patch.dict(core.ACTOR, actor="driver")
        handle.start()
        self.addCleanup(handle.stop)

    def fixture(self, *, before_install=None, fail_project=False):
        inst, _, _ = self.chain(name="project with spaces", review_days=1, before_install=before_install,
                                fail=fail_project)
        self.chain(inst=inst, kind="tool", review_days=1)
        at = core.now()
        sent, done = at - timedelta(minutes=1), at + timedelta(seconds=2)
        iso = lambda value: value.isoformat()
        for kind, fields in (
            ("reply_binding", {"receipt": "ok", "deliveries": "ok"}),
            ("reply_checked", {}),
            ("round_result", {"ok": True}),
        ):
            inst.event(kind, by="driver", **fields)
        inst.turns_path.write_text(
            json.dumps({"turn": 1, "status": "sent", "from": "driver", "ts_sent": iso(sent)}) + "\n"
            + json.dumps({"turn": 1, "status": "done", "ts_done": iso(done)}) + "\n", encoding="utf-8")
        td = inst.turn_dir()
        for name, doc in (("intake.json", {"commands": ["protected project install", "protected tool install"]}),
                          ("send_meta.json", {"message_uuid": "c5-parent"}),
                          ("reply_meta.json", {"parent": "c5-parent", "ended_at": iso(done)})):
            (td / name).write_text(json.dumps(doc), encoding="utf-8")
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
        return inst

    def test_actual_chain_frozen_timing_and_synthetic(self):
        inst = self.fixture()
        snap = live._capture_inputs(inst, rounds=1)
        result = live._evaluate_snapshot(snap)
        self.assertTrue(result["report"]["summary"]["passed_strict"], result["report"])
        self.assertEqual(result["history_authorization"],
                         {"eligible": False, "complete": True, "synthetic": True, "why": ["synthetic_install_history"]})
        self.assertEqual(result["authorization"], "unavailable")
        self.assertEqual(live._evaluate_snapshot(snap), result)
        self.assertTrue(live._revalidate_inputs(inst, snap.descriptor)["valid"])
        # Frozen replay cannot read disk through mutable review helpers.
        with patch.object(review, "_read_bytes", side_effect=AssertionError("global read")), \
             patch.object(review, "_target", side_effect=AssertionError("current target")), \
             patch.object(review, "consume", side_effect=AssertionError("install write")), \
             patch.object(Path, "resolve", side_effect=AssertionError("frozen replay filesystem resolution")):
            self.assertEqual(live._evaluate_snapshot(snap), result)
        # Direct reporting takes the same structured route for commands whose
        # long Python invocation has no parseable final ZIP token.
        self.assertEqual(live.check(inst, rounds=1), result["report"])

    def test_receipt_metadata_and_raw_drift(self):
        inst = self.fixture()
        snap = live._capture_inputs(inst, rounds=1)
        directory = inst.turn_dir()
        for name in ("c01.install.jsonl", "c01.json", "c01.out"):
            path = directory / name
            raw = path.read_bytes()
            path.write_bytes(raw + b"\n")
            check = live._revalidate_inputs(inst, snap.descriptor)
            self.assertFalse(check["valid"], (name, check))
            path.write_bytes(raw)
            self.assertTrue(live._revalidate_inputs(inst, snap.descriptor)["valid"])

    def test_event_reference_cannot_replace_actual_receipt(self):
        inst = self.fixture()
        rows = live._strict_log(inst.events_path.read_bytes(), inst.events_path)
        event = next(row for row in rows if row["type"] == "exec")
        event["install_refs"][0]["prepared_sha256"] = "0" * 64
        inst.events_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        result = live.check(inst, rounds=1)
        self.assertFalse(result["passed"])
        self.assertEqual(result["input_problem"]["state"], "corrupt")

    def test_failed_project_install_is_not_hidden_by_successful_tool(self):
        inst = self.fixture(fail_project=True)
        result = live._evaluate_snapshot(live._capture_inputs(inst, rounds=1))
        entries = result["window_inputs"]["review_inputs"]["entries"]
        self.assertEqual([entry["state"] for entry in entries], ["failed", "valid"])
        self.assertFalse(result["report"]["summary"]["passed_strict"])
        self.assertFalse(result["history_authorization"]["eligible"])
        self.assertIn("install_history:failed", result["history_authorization"]["why"])

    def test_unsuccessful_unreferenced_exec_stays_unknown(self):
        inst = self.fixture()
        self.assertEqual(ops.run_exec(inst, "exit 124", by="driver"), 124)
        result = live._evaluate_snapshot(live._capture_inputs(inst, rounds=1))
        unknown = result["window_inputs"]["review_inputs"]["entries"][-1]
        self.assertEqual((unknown["state"], unknown["exit_code"], unknown["reference"]), ("unknown", 124, None))
        self.assertFalse(result["history_authorization"]["eligible"])
        self.assertIn("install_history:unknown", result["history_authorization"]["why"])

    def test_missing_corrupt_and_symlink_history(self):
        inst = self.fixture()
        snap = live._capture_inputs(inst, rounds=1)
        entry = snap.descriptor["review_inputs"]["entries"][0]
        path = inst.evidence / "install" / entry["reference"]["tx_id"] / "completed.json"
        raw, mode = path.read_bytes(), path.stat().st_mode & 0o777
        path.unlink()
        result = live._evaluate_snapshot(live._capture_inputs(inst, rounds=1))
        self.assertEqual(result["window_inputs"]["review_inputs"]["entries"][0]["state"], "unknown")
        path.write_bytes(b'{"duplicate":1,"duplicate":2}')
        result = live._evaluate_snapshot(live._capture_inputs(inst, rounds=1))
        self.assertEqual(result["window_inputs"]["review_inputs"]["entries"][0]["state"], "corrupt")
        path.unlink()
        outside = self.base / "completed-copy.json"
        outside.write_bytes(raw)
        path.symlink_to(outside)
        result = live.check(inst, rounds=1)
        self.assertEqual(result["input_problem"]["state"], "unsupported")
        path.unlink()
        path.write_bytes(raw)
        path.chmod(mode)
        # Recreating a file changes its identity; the old snapshot stays invalid.
        self.assertFalse(live._revalidate_inputs(inst, snap.descriptor)["valid"])
        fresh = live._capture_inputs(inst, rounds=1)
        self.assertTrue(live._evaluate_snapshot(fresh)["report"]["summary"]["passed_strict"])
        self.assertTrue(live._revalidate_inputs(inst, fresh.descriptor)["valid"])

    def test_changed_raw_material(self):
        inst = self.fixture()
        snap = live._capture_inputs(inst, rounds=1)
        selected = snap.descriptor["review_inputs"]["entries"][0]
        aid = selected["history"]["facts"]["identities"]["attempt_id"]
        path = core.LOCAL / "reviews/attempts" / aid / "verify_stderr.bin"
        raw = path.read_bytes()
        mode = path.stat().st_mode & 0o777
        path.chmod(0o600)
        path.write_bytes(raw + b"changed")
        self.assertFalse(live._revalidate_inputs(inst, snap.descriptor)["valid"])
        path.write_bytes(raw)
        path.chmod(mode)
        self.assertTrue(live._revalidate_inputs(inst, snap.descriptor)["valid"])

    def test_later_real_block_invalidates_frozen_progress(self):
        ready, proceed = threading.Event(), threading.Event()
        workers, errors, selected = [], [], []
        original = review._exec

        def paused(cmd, timeout):
            reply = original(cmd, timeout)
            if cmd[0] == str(self.env.runner) and selected:
                request = json.loads(Path(cmd[cmd.index("--request") + 1]).read_bytes())
                if request["request_id"] == selected[0]:
                    ready.set()
                    if not proceed.wait(30):
                        raise RuntimeError("C5 block registration barrier timed out")
            return reply

        def start(rid):
            selected.append(rid)
            self.env.behave(verdict="block")

            def register():
                try:
                    review.run(rid)
                except Exception as exc:
                    errors.append(exc)

            worker = threading.Thread(target=register)
            workers.append(worker)
            worker.start()
            self.assertTrue(ready.wait(10))
            self.env.behave(verdict="pass")

        with patch.object(review, "_exec", paused):
            try:
                inst = self.fixture(before_install=start)
                snap = live._capture_inputs(inst, rounds=1)
                self.assertTrue(live._evaluate_snapshot(snap)["report"]["summary"]["passed_strict"])
                proceed.set()
                workers[0].join(15)
                self.assertFalse(workers[0].is_alive())
                self.assertFalse(errors)
                self.assertFalse(live._revalidate_inputs(inst, snap.descriptor)["valid"])
                current = live._capture_inputs(inst, rounds=1)
                entry = current.descriptor["review_inputs"]["entries"][0]
                self.assertEqual(entry["state"], "failed", entry)
                self.assertIn("blocked", entry["history"]["diagnostics"])
            finally:
                proceed.set()
                for worker in workers:
                    worker.join(15)

    def test_post_read_mutation_barrier_rejects_mixed_snapshot(self):
        inst = self.fixture()
        snap = live._capture_inputs(inst, rounds=1)
        aid = snap.descriptor["review_inputs"]["entries"][0]["history"]["facts"]["identities"]["attempt_id"]
        path = core.LOCAL / "reviews/attempts" / aid / "verify_stdout.bin"
        raw, mode = path.read_bytes(), path.stat().st_mode & 0o777
        ready, done = self.base / "reader-ready", self.base / "writer-done"
        code = ("import pathlib,sys,time\n"
                "p,r,d=map(pathlib.Path,sys.argv[1:])\n"
                "until=time.monotonic()+15\n"
                "while not r.exists():\n"
                " if time.monotonic()>until:sys.exit(2)\n"
                " time.sleep(.01)\n"
                "p.chmod(0o600);p.write_bytes(p.read_bytes()+b'changed');d.touch()\n")
        original, triggered = live._file_source, []

        def barrier(source):
            value = original(source)
            if str(source) == str(path) and not triggered:
                triggered.append(True)
                ready.touch()
                until = time.monotonic() + 10
                while not done.exists() and time.monotonic() < until:
                    time.sleep(.01)
                self.assertTrue(done.exists(), "history mutation barrier timed out")
            return value

        worker = subprocess.Popen([sys.executable, "-c", code, str(path), str(ready), str(done)])
        try:
            with patch.object(live, "_file_source", barrier):
                with self.assertRaises(live._InputProblem) as error:
                    live._capture_inputs(inst, rounds=1)
                self.assertEqual(error.exception.state, "changed")
            self.assertEqual(worker.wait(timeout=10), 0)
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait()
            path.write_bytes(raw)
            path.chmod(mode)
        self.assertTrue(live._revalidate_inputs(inst, snap.descriptor)["valid"])

    def test_goal_progress_replays_and_cannot_grant_synthetic(self):
        inst = self.fixture()
        definition = {"id": "C5", "title": "C5 real synthetic chain", "kind": "exec", "check": "true",
                      "pass": {"exit": 0}, "means": "synthetic fixture", "on_fail": "inspect",
                      "live": {"rounds": 1, "segments": list(range(1, 11))}}
        peer = dict(definition, id="C5N", live={"rounds": 1, "segments": [4]})
        (inst.wf / "goals.json").write_text(json.dumps({"goals": [definition, peer]}), encoding="utf-8")
        policy.approve(inst, "goals", "synthetic C5 fixture", by="test")
        goals.run_checks(inst, by="test")
        state_path = inst.wf / "goals_state.json"
        records = json.loads(state_path.read_bytes())
        verified = records["C5"]["verified"]
        self.assertEqual(verified["binding"]["schema"], "live-collection/3", verified)
        self.assertFalse(verified["granted"])
        self.assertEqual(verified["why"], ["synthetic_install_history"])
        self.assertTrue(verified["collection"]["report"]["summary"]["passed_strict"])
        item = goals.progress(core.Instance(inst.root))["items"][0]
        self.assertEqual(item["layer"], "mechanical_pass", item)
        self.assertTrue(item["collection"]["valid"], item)
        # Tool cleanliness depends on index/ignore/attribute inputs as well as
        # status text. A harmless ignore comment still invalidates old proof.
        exclude = self.env.tool / ".git/info/exclude"
        old_exclude = exclude.read_bytes()
        exclude.write_bytes(old_exclude + b"\n# changed after live capture\n")
        for item in goals.progress(core.Instance(inst.root))["items"]:
            self.assertFalse(item["collection"]["valid"], item)
        exclude.write_bytes(old_exclude)
        for item in goals.progress(core.Instance(inst.root))["items"]:
            self.assertTrue(item["collection"]["valid"], item)
        # Rehash every public digest and evidence representation after forging
        # the claimed provenance. Fresh B replay still has the original bytes.
        verified["granted"] = True
        verified["why"] = []
        verified["binding"]["authorization"] = "available"
        verified["binding"]["authorization_complete"] = True
        collection = verified["collection"]
        collection.update(authorization="available", reason=None,
                          history_authorization={"eligible": True, "complete": True, "synthetic": False, "why": []})
        for entry in collection["window_inputs"]["review_inputs"]["entries"]:
            entry["history"]["facts"]["synthetic"] = False
            entry["history"]["facts"]["provenance"]["synthetic"] = False
        import hashlib
        digest = hashlib.sha256(live._canonical(collection["window_inputs"]).encode()).hexdigest()
        collection["inputs_sha256"] = verified["binding"]["window_inputs_sha256"] = digest
        evidence = inst.root / verified["binding"]["evidence"]
        core.write_json(evidence, {"goal": "C5", "summary": collection["report"]["summary"],
                                   "rounds": collection["report"]["rounds"], "collection": collection})
        verified["binding"]["evidence_sha256"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
        core.write_json(state_path, records)
        for item in goals.progress(core.Instance(inst.root))["items"]:
            self.assertNotEqual(item["layer"], "verified", item)
            self.assertFalse(item["collection"]["valid"], item)

    def test_precommit_closure_drift_is_persisted_as_denied(self):
        inst = self.fixture()
        definition = {"id": "C5", "title": "C5 commit boundary", "kind": "exec", "check": "true",
                      "pass": {"exit": 0}, "means": "synthetic fixture", "on_fail": "inspect",
                      "live": {"rounds": 1, "segments": list(range(1, 11))}}
        core.write_json(inst.wf / "goals.json", {"goals": [definition]})
        policy.approve(inst, "goals", "synthetic C5 fixture", by="test")
        original = goals._live_attest
        exclude = self.env.tool / ".git/info/exclude"

        def mutate_after_attest(*args, **kwargs):
            value = original(*args, **kwargs)
            exclude.write_bytes(exclude.read_bytes() + b"\n# changed before commit\n")
            return value

        with patch.object(goals, "_live_attest", mutate_after_attest):
            goals.run_checks(inst, by="test")
        verified = json.loads((inst.wf / "goals_state.json").read_bytes())["C5"]["verified"]
        self.assertTrue(verified["collection_validation"]["valid"])
        self.assertFalse(verified["closure_validation"]["valid"])
        self.assertFalse(verified["granted"])
        self.assertIn("提交前完整闭包 changed", verified["why"])
        self.assertFalse(goals.progress(core.Instance(inst.root))["items"][0]["collection"]["valid"])


if __name__ == "__main__":
    unittest.main()
