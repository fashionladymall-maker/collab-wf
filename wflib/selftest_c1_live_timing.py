"""C1 6a-live-timing focused cases on real exec/install/review chains.

Covers what the existing suites do not: a review that has already expired at
install start (the real protected install refuses it and the attempt stays a
visible unknown history row), and drift of the frozen package, the baseline
(base proof), the request base config and the installed config after a valid
installation. Every installation runs through ops.run_exec and the real
release/review code; review provenance is the explicitly synthetic selftest
verifier and never a production grant.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from . import core, live, ops, release, review
from . import selftest_install_history as history_tests


class LiveTimingTests(unittest.TestCase):
    setUp = history_tests.HistoryTests.setUp
    project = history_tests.HistoryTests.project
    approved = history_tests.HistoryTests.approved
    chain = history_tests.HistoryTests.chain
    collect = history_tests.HistoryTests.collect

    def install_exec(self, inst, rid):
        """Real exec of the protected project install at the real current time."""
        code = ("from pathlib import Path\n"
                "from wflib import core, review, release, anomaly\n"
                f"core.LOCAL=Path({str(core.LOCAL)!r})\n"
                "anomaly.LOCAL=core.LOCAL\n"
                f"review._tool_home=lambda:Path({str(self.env.tool)!r})\n"
                f"release.apply_zip(core.Instance(Path({str(inst.root)!r})),review_id={rid!r},by='driver')\n")
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent))
        with patch.dict(os.environ, env):
            rc = ops.run_exec(inst, shlex.join([sys.executable, "-c", code]), by="driver")
        return rc, live._strict_log(inst.events_path.read_bytes(), inst.events_path)[-1]

    def reviewed_days_ago(self, inst, days):
        at = review._now()
        with patch.object(review, "_now", lambda: at - timedelta(days=days)):
            return self.approved(inst, "v0.2")

    def test_review_expired_at_install_start_is_refused_and_stays_unknown(self):
        inst = self.project("expired at start")
        inst.state.update(state="EXECUTE", turn=1)
        inst.save()
        rid = self.reviewed_days_ago(inst, review.REUSE_DAYS + 1)
        rc, event = self.install_exec(inst, rid)
        self.assertNotEqual(rc, 0)
        self.assertEqual(event["install_refs"], [])
        out = (inst.turn_dir() / (event["cid"] + ".err")).read_text(encoding="utf-8", errors="replace") \
            + (inst.turn_dir() / (event["cid"] + ".out")).read_text(encoding="utf-8", errors="replace")
        self.assertIn("expired", out)
        store = inst.evidence / "install"
        self.assertFalse(store.exists() and any((p / "completed.json").exists() for p in store.iterdir()))
        # C's consumers keep the refused attempt visible and unauthorized.
        reader = history_tests.Reader()
        bound = live._bound_histories(inst, [event], reader, local=core.LOCAL, tool_root=self.env.tool,
                                      turns=[{"turn": 1, "status": "sent", "ts_sent": event["ts"]}])
        rows = [row for row in bound["entries"] if row.get("seq") == event["seq"]]
        self.assertEqual([(r["state"], r["reference"], r["diagnostics"]) for r in rows],
                         [("unknown", None, ["unsuccessful_exec_has_no_transaction_reference"])])
        from .live_history import entry_problem, window_authorization
        self.assertEqual(entry_problem(rows[0]), "unknown")
        decision = window_authorization(bound, [event], {"rounds": [{"turn": 1}]})
        self.assertFalse(decision["eligible"])
        self.assertIn("install_history:unknown", decision["why"])

    def test_review_valid_at_install_start_after_six_days(self):
        """Contrast: the same chain with a review still inside the window installs."""
        _, context, ref = self.chain(review_days=review.REUSE_DAYS - 1)
        result = self.collect(context, ref)
        self.assertEqual(result["state"], "valid", result["diagnostics"])
        self.assertTrue(result["facts"]["authorized_at_start"])
        self.assertTrue(result["facts"]["synthetic"])   # selftest verifier: never a production grant

    def drifted(self, path, mutate):
        mode = path.stat().st_mode & 0o777
        raw = path.read_bytes()
        path.chmod(0o600)
        path.write_bytes(mutate(raw))
        path.chmod(mode)
        return raw, mode

    def restore(self, path, raw, mode):
        path.chmod(0o600)
        path.write_bytes(raw)
        path.chmod(mode)

    def reseal(self, raw, change):
        doc = json.loads(raw)
        doc.pop("body_sha256", None)
        change(doc)
        return review._canon(dict(doc, body_sha256=release._digest(doc)))

    def check_drift(self, context, ref, path, mutate, states=("corrupt", "changed", "unknown", "failed")):
        self.assertEqual(self.collect(context, ref)["state"], "valid")
        raw, mode = self.drifted(path, mutate)
        try:
            result = self.collect(context, ref)
            self.assertIn(result["state"], states, result)
            self.assertNotEqual(result["state"], "valid")
        finally:
            self.restore(path, raw, mode)
        self.assertEqual(self.collect(context, ref)["state"], "valid")
        return result

    def test_frozen_package_drift_invalidates_history(self):
        _, context, ref = self.chain(review_days=1)
        package = core.LOCAL / "releases" / (ref["package_sha256"] + ".zip")
        self.assertTrue(package.is_file())
        result = self.check_drift(context, ref, package, lambda raw: raw + b"\0")
        self.assertTrue(result["diagnostics"], result)

    def test_baseline_drift_invalidates_history(self):
        inst, context, ref = self.chain(review_days=1)
        baseline = inst.evidence / "install" / ref["tx_id"] / "baseline.json"
        # Whitespace is not a content change (records are compared parsed).
        self.assertEqual(self.collect(context, ref)["state"], "valid")

        def unsealed(raw):
            doc = json.loads(raw)
            doc["identity"]["commit"] = "0" * 40   # content changed, old seal kept
            return json.dumps(doc).encode()
        raw_result = self.check_drift(context, ref, baseline, unsealed)
        self.assertIn("record_hash:baseline.json", raw_result["diagnostics"])
        # Resealed with a different base identity: the prepared link still rejects it.
        resealed = self.check_drift(context, ref, baseline, lambda raw: self.reseal(
            raw, lambda doc: doc["identity"].update(commit="0" * 40)))
        self.assertIn("baseline_identity", resealed["diagnostics"])

    def test_request_base_config_drift_invalidates_history(self):
        _, context, ref = self.chain(review_days=1)
        request = core.LOCAL / "reviews" / "requests" / (ref["request_id"] + ".json")
        self.assertTrue(request.is_file())

        def change(raw):
            doc = json.loads(raw)
            doc["base"]["config_sha256"] = "0" * 64
            return json.dumps(doc).encode()
        result = self.check_drift(context, ref, request, change)
        self.assertTrue(result["diagnostics"], result)

    def test_installed_config_drift_invalidates_history(self):
        inst, context, ref = self.chain(review_days=1)
        completed = inst.evidence / "install" / ref["tx_id"] / "completed.json"
        result = self.check_drift(context, ref, completed, lambda raw: self.reseal(
            raw, lambda doc: doc["actual"].update(config_sha256="0" * 64)))
        self.assertIn("installed_config_mismatch", result["diagnostics"])


if __name__ == "__main__":
    unittest.main()
