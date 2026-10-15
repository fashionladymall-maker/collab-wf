"""Real protected-install chains with explicitly synthetic review provenance."""
import json
import os
import shlex
import stat
import subprocess
import sys
import threading
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from . import anomaly, core, ops, release, review
from . import selftest_install_commit as commit_tests
from .selftest_review import _git


class Reader:
    """Test frozen-reader implementation; production collection belongs to C."""
    def __init__(self):
        self.files, self.dirs, self.queries = {}, {}, {}

    def bytes(self, path):
        if path not in self.files:
            p = Path(path)
            self.files[path] = p.read_bytes() if p.exists() else None
        return self.files[path]

    def directory(self, path):
        if path not in self.dirs:
            p = Path(path)
            rows = []
            if p.exists():
                for child in sorted(p.iterdir()):
                    mode = child.lstat().st_mode
                    rows.append([child.name, "file" if stat.S_ISREG(mode) else
                                 "dir" if stat.S_ISDIR(mode) else "unsupported", stat.S_IMODE(mode)])
            self.dirs[path] = {"state": "present" if p.exists() else "absent", "members": rows}
        return self.dirs[path]

    def git(self, root, argv):
        key = (root, tuple(argv))
        if key not in self.queries:
            r = subprocess.run(["git"] + argv, cwd=root, capture_output=True)
            self.queries[key] = {"returncode": r.returncode, "stdout": r.stdout, "stderr": r.stderr}
        return self.queries[key]


class HistoryTests(unittest.TestCase):
    def setUp(self):
        commit_tests.InstallCommitTests.setUp(self)

    project = commit_tests.InstallCommitTests.project
    approved = commit_tests.InstallCommitTests.approved

    def chain(self, name="history", *, review_days=0, install_days=0, fail=False, kind="project",
              before_install=None, after_install=None, by="driver", inst=None):
        inst = self.project(name) if inst is None else inst
        inst.state.update(state="EXECUTE", turn=1)
        inst.save()
        at = review._now()
        with patch.object(review, "_now", lambda: at - timedelta(days=review_days)):
            if kind == "project":
                rid = self.approved(inst, "v0.2")
            else:
                files = {"VERSION": b"9.9.9\n", "docs/REVIEW.md": (self.env.tool / "docs/REVIEW.md").read_bytes()}
                manifest = {"version": "9.9.9", "files": {p: review._sha(b) for p, b in files.items()}}
                (self.env.tool / "MANIFEST.json").write_bytes(review._canon(manifest))
                _git(self.env.tool, "add", "-A")
                _git(self.env.tool, "commit", "-qm", "complete baseline manifest")
                files["VERSION"] = b"9.9.10\n"
                files["MANIFEST.json"] = review._canon({
                    "version": "9.9.10", "files": {p: review._sha(b) for p, b in files.items()}})
                req = review.prepare(self.env.tool, self.env.pkg("tool-update", files), kind="tool", author="B")
                rid = req["request_id"]
                self.assertTrue(review.run(rid)["allowed"])
        if before_install:
            before_install(rid)
        # The installer runs in a real exec subprocess and inherits the receipt
        # FD. Only host locations and synthetic clock/failure fixtures are set.
        code = (
            "from pathlib import Path\n"
            "from datetime import timedelta\n"
            "from unittest.mock import patch\n"
            "from wflib import core, review, release, server, anomaly\n"
            f"core.LOCAL=Path({str(core.LOCAL)!r})\n"
            "anomaly.LOCAL=core.LOCAL\n"
            f"review._tool_home=lambda:Path({str(self.env.tool)!r})\n"
            f"at=review._now()-timedelta(days={install_days})\n"
            "with patch.object(review, '_now', lambda: at):\n"
        )
        if kind == "tool":
            code += (f" core.WF_HOME=release.WF_HOME=Path({str(self.env.tool)!r})\n"
                     " server.info=lambda:{}\n"
                     " release._restart_board=lambda:False\n"
                     f" release.self_update(review_id={rid!r},skip_test=True)\n")
        elif fail:
            code += " with patch.object(release._InstallTx,'complete',side_effect=core.WfError('test failure')):\n "
        if kind == "project":
            code += f" release.apply_zip(core.Instance(Path({str(inst.root)!r})),review_id={rid!r},by={by!r})\n"
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent))
        with patch.dict(os.environ, env):
            rc = ops.run_exec(inst, shlex.join([sys.executable, "-c", code]), by="driver")
        if after_install:
            after_install()
        self.assertEqual(rc, 1 if fail else 0)
        meta_file = max(inst.turn_dir().glob("c[0-9]*.json"), key=lambda p: int(p.stem[1:]))
        meta = json.loads(meta_file.read_bytes())
        self.assertIsNone(meta["install_receipt_error"])
        self.assertEqual(len(meta["install_refs"]), 1)
        ref = meta["install_refs"][0]
        context = {k: meta["install_exec"][k] for k in ("subject", "turn", "exec")}
        context.update(target=ref["target"], host={"local": str(core.LOCAL), "tool_root": str(self.env.tool)})
        return inst, context, ref

    def collect(self, context, ref, reader=None):
        return review.collect_install_history(context, ref, reader or Reader())

    def test_real_exec_previous_day_review(self):
        inst, context, ref = self.chain(review_days=1)
        rd = Reader()
        result = self.collect(context, ref, rd)
        self.assertEqual(result["state"], "valid", result["diagnostics"])
        self.assertTrue(result["facts"]["authorized_at_start"])
        self.assertTrue(result["facts"]["completed"])
        self.assertTrue(result["facts"]["synthetic"])
        # Frozen replay cannot open the current target or invoke mutable review.
        with patch.object(review, "_read_bytes", side_effect=AssertionError("global read")), \
             patch.object(review, "_target", side_effect=AssertionError("current target")), \
             patch.object(review, "consume", side_effect=AssertionError("write")):
            self.assertEqual(self.collect(context, ref, rd), result)
        ev = [json.loads(x) for x in inst.events_path.read_text().splitlines() if json.loads(x)["type"] == "exec"][-1]
        self.assertEqual(ev["install_refs"], [ref])
        self.assertEqual(ev["install_exec"]["exec"], context["exec"])

    def test_expiry_is_evaluated_at_install_start(self):
        _, context, ref = self.chain(review_days=9, install_days=8)
        result = self.collect(context, ref)
        self.assertEqual(result["state"], "valid", result["diagnostics"])

    def test_tool_install_uses_same_history_protocol(self):
        _, context, ref = self.chain(kind="tool", review_days=1)
        result = self.collect(context, ref)
        self.assertEqual(result["state"], "valid", result["diagnostics"])
        self.assertEqual(result["facts"]["target"]["kind"], "tool")
        self.assertTrue(result["facts"]["synthetic"])

    def test_current_block_registered_after_install_invalidates(self):
        ready, proceed = threading.Event(), threading.Event()
        thread, errors = [], []
        original_exec = review._exec

        def paused(cmd, timeout):
            if cmd[0] == str(self.env.runner):
                ready.set()
                if not proceed.wait(15):
                    raise RuntimeError("test barrier timeout")
            return original_exec(cmd, timeout)

        def start(rid):
            self.env.behave(verdict="block")
            def run():
                try:
                    with patch.object(review, "_exec", paused):
                        review.run(rid)
                except Exception as exc:
                    errors.append(exc)
            worker = threading.Thread(target=run)
            thread.append(worker)
            worker.start()
            self.assertTrue(ready.wait(10))

        def finish():
            proceed.set()
            thread[0].join(15)
            self.assertFalse(thread[0].is_alive())
            self.assertFalse(errors)

        try:
            _, context, ref = self.chain(before_install=start, after_install=finish)
            result = self.collect(context, ref)
            self.assertEqual(result["state"], "failed", result)
            self.assertIn("blocked", result["diagnostics"])
        finally:
            proceed.set()
            if thread:
                thread[0].join(15)

    def test_failed_install_cannot_be_hidden(self):
        _, context, ref = self.chain(fail=True)
        result = self.collect(context, ref)
        self.assertEqual(result["state"], "failed", result)
        self.assertFalse(result["facts"]["completed"])

    def test_exact_reference_and_nonce(self):
        _, context, ref = self.chain()
        wrong = dict(context, exec=dict(context["exec"], nonce="0" * 32))
        self.assertEqual(self.collect(wrong, ref)["state"], "unknown")
        bad = dict(ref, request_id="rq-" + "0" * 32)
        self.assertEqual(self.collect(context, bad)["state"], "corrupt")

    def test_same_package_same_target_two_invocations(self):
        inst, first_context, first_ref = self.chain()
        _, second_context, second_ref = self.chain(inst=inst)
        self.assertEqual(first_ref["package_sha256"], second_ref["package_sha256"])
        self.assertNotEqual(first_ref["tx_id"], second_ref["tx_id"])
        self.assertEqual(self.collect(first_context, first_ref)["state"], "valid")
        self.assertEqual(self.collect(second_context, second_ref)["state"], "valid")
        self.assertNotEqual(self.collect(second_context, first_ref)["state"], "valid")

    def test_late_registration_cannot_authorize_history(self):
        _, context, ref = self.chain()
        result = self.collect(context, ref)
        aid = result["facts"]["identities"]["attempt_id"]
        path = core.LOCAL / "reviews/attempts" / aid / "attempt.json"
        att = json.loads(path.read_bytes())
        att["times"]["result_registered"] = review._iso(review._now() + timedelta(days=1))
        path.write_bytes(review._canon(att))
        result = self.collect(context, ref)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["diagnostics"], ["review_install_timing"])

    def test_started_only_corrupt_blob_and_index(self):
        _, context, ref = self.chain()
        txdir = Path(ref["target"]["root"]) / "workflow/evidence/install" / ref["tx_id"]
        done = txdir / "completed.json"
        saved = done.read_bytes()
        done.unlink()
        result = self.collect(context, ref)
        self.assertEqual(result["state"], "unknown")
        self.assertIn("source_missing:completed.json", result["diagnostics"])
        done.write_bytes(saved)
        blob = next((txdir / "blobs").iterdir())
        saved_blob = blob.read_bytes()
        blob.chmod(0o600)
        blob.write_bytes(b"corrupt")
        self.assertEqual(self.collect(context, ref)["state"], "corrupt")
        blob.write_bytes(saved_blob)
        idx = core.LOCAL / "reviews/content" / (ref["content_id"] + ".json")
        doc = json.loads(idx.read_bytes())
        doc["attempts"] = []
        idx.write_bytes(review._canon(doc))
        self.assertEqual(self.collect(context, ref)["diagnostics"], ["attempt_index_mismatch"])

    def test_current_lanes_and_rules_are_rechecked(self):
        _, context, ref = self.chain()
        lanes = self.env.lanes.read_bytes()
        self.env.set_lanes({"B": {"sessions": ["rev-sess-1"]}})
        self.assertNotEqual(self.collect(context, ref)["state"], "valid")
        self.env.lanes.write_bytes(lanes)
        (self.env.tool / review.RULES_PATH).write_text("new rules")
        self.assertEqual(self.collect(context, ref)["state"], "changed")

    def test_raw_and_current_trust_invalidate(self):
        _, context, ref = self.chain()
        result = self.collect(context, ref)
        self.assertEqual(result["state"], "valid", result["diagnostics"])
        aid = result["facts"]["identities"]["attempt_id"]
        path = core.LOCAL / "reviews/attempts" / aid / "verify_stderr.bin"
        raw = path.read_bytes()
        path.chmod(0o600)
        path.write_bytes(b"changed")
        self.assertNotEqual(self.collect(context, ref)["state"], "valid")
        path.write_bytes(raw)
        self.env.verifier.write_text(self.env.verifier.read_text() + "\n# changed\n")
        self.assertEqual(self.collect(context, ref)["state"], "changed")


if __name__ == "__main__":
    unittest.main()
