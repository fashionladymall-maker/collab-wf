"""C5-G1: positive host production-trust registry and legacy install entries.

Every verifier in this module is a local selftest fixture, never a platform.
``unmarked`` fixtures reproduce the reviewed counterexample: the fixture
verifier output and its transport entry both omit ``synthetic`` and the bytes
are re-pinned, so B reports ``platform_verified`` with ``synthetic=False``.
Positive cases register that fixture under the explicit selftest registration
id ``selftest-registry-contract`` (class ``production``) in an isolated
temporary host. They exercise the registry contract only; the stored grant
names that selftest registration and is not evidence of a real platform.
"""
from __future__ import annotations

import json
import shlex
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from . import core, goals, live, ops, policy, release, review
from . import selftest_c5_integration as integration
from .selftest import _project_md

REGISTRATION_ID = "selftest-registry-contract"
PLATFORM = "selftest-contract-platform"
MODEL = "selftest-contract-model"
GOAL = {"id": "C5T", "title": "C5-G1 trust contract", "kind": "exec", "check": "true",
        "pass": {"exit": 0}, "means": "selftest fixture", "on_fail": "inspect",
        "live": {"rounds": 1, "segments": list(range(1, 11))}}


BASH_ORACLE = [
    "setup() { wf() { :; }; }; setup; wf self-update unused.zip",
    'f() { :; } >"$(wf self-update unused.zip)"',
    "command -v wf self-update", "command -V wf", "command -pv wf self-update x.zip",
    "f() { wf self-update used.zip; }; time f", "f() { wf self-update used.zip; }; time -p f",
    "setup() { f() { wf self-update used.zip; }; }; setup; f",
    "f() { :; }; setup() { f() { wf self-update used.zip; }; }; setup; f",
    'f() { :; } >"$(wf self-update used.zip)"; f',
    "(wf() { :; }); wf self-update leak.zip", 'echo "$(wf() { :; })"; wf self-update leak.zip',
    "setup() { wf() { :; }; }; setup | cat; wf self-update leak.zip",
    "setup() { wf() { :; }; }; setup & wait; wf self-update leak.zip",
    "setup() { wf() { :; }; }; setup && wf self-update shadowed.zip",
    "f() { wf apply x.zip; }; command f", "f() { wf apply x.zip; }; env f",
    "f() { wf apply x.zip; }; EXT_TIME f", "EXT_TIME wf self-update ext.zip",
    "wf() { :; }; command wf self-update real.zip", "wf() { :; }; env wf self-update real.zip",
    "helper() { wf self-update unused.zip; }; printf '%s\\n' ordinary", "helper() { wf self-update used.zip; }; helper",
    "wf() { echo shadow; }; wf apply x.zip", "g() { f; }; f() { wf apply late.zip; }; g",
    "eval 'f() { wf apply ev.zip; }'; f", "echo ';' wf apply x.zip", 'echo "$(wf apply sub.zip)"',
    "if true; then wf apply cond.zip; fi", "cat <<'EOF'\nwf apply x.zip\nEOF", "true | wf self-update pipe.zip",
]


class _Fixture(unittest.TestCase):
    """Two real protected installs (project + tool) through ops.run_exec."""
    # "unmarked": output and transport omit synthetic (reviewed counterexample);
    # "marked": B's explicitly synthetic fixture unchanged;
    # "output": only the verifier output says synthetic, transport omits it.
    mode = "unmarked"

    setUp_base = integration.C5IntegrationTests.setUp
    fixture = integration.C5IntegrationTests.fixture
    chain = integration.C5IntegrationTests.chain
    project = integration.C5IntegrationTests.project

    def setUp(self):
        self.setUp_base()
        self.env.vbehave(identity={"platform": PLATFORM, "session": "rev-sess-1"}, model_verified=MODEL)
        if self.mode != "marked":
            verifier = self.env.verifier
            raw = verifier.read_bytes()
            self.assertIn(b'"synthetic": True, ', raw)
            if self.mode == "unmarked":
                verifier.write_bytes(raw.replace(b'"synthetic": True, ', b""))
            cfg = json.loads(self.env.transport.read_bytes())
            cfg["review_verifier"].pop("synthetic")
            cfg["review_verifier"]["sha256"] = review._sha(verifier.read_bytes())
            self.env.transport.write_text(json.dumps(cfg), encoding="utf-8")
        self.registry = core.LOCAL / live.TRUST_REGISTRY_NAME

    def approved(self, inst, version):
        package = self.env.pkg(version, {
            "PROJECT.md": _project_md(version).encode(),
            "CHANGELOG.md": f"# CHANGELOG\n\n## {version} — test\n- installed\n".encode(),
            "app.py": version.encode(),
        })
        req = review.prepare(inst, package, kind="project", author="B")
        decision = review.run(req["request_id"])
        self.assertTrue(decision["allowed"], decision)
        self.assertIs(decision["provenance"]["synthetic"], self.mode != "unmarked")
        return req["request_id"]

    def registration(self, **change):
        row = {"id": REGISTRATION_ID, "class": "production",
               "verifier": {"path": str(self.env.verifier), "sha256": review._sha(self.env.verifier.read_bytes())},
               "identity": {"platform": PLATFORM, "models": [MODEL]}}
        row.update(change)
        return row

    def write_registry(self, *rows, document=None):
        doc = document if document is not None else {"schema": live.TRUST_SCHEMA, "registrations": list(rows)}
        raw = doc if isinstance(doc, bytes) else json.dumps(doc).encode()
        self.registry.write_bytes(raw)

    def evaluate(self, inst):
        return live._evaluate_snapshot(live._capture_inputs(inst, rounds=1))

    def run_goal(self, inst):
        core.write_json(inst.wf / "goals.json", {"goals": [GOAL]})
        policy.approve(inst, "goals", "C5-G1 selftest fixture", by="test")
        goals.run_checks(inst, by="test")
        return self.stored(inst)

    def stored(self, inst):
        return json.loads((inst.wf / "goals_state.json").read_bytes())["C5T"]["verified"]

    def item(self, inst):
        return goals.progress(core.Instance(inst.root))["items"][0]


class ProductionTrustTests(_Fixture):
    def test_unmarked_fixture_without_registry_is_denied(self):
        """Reviewed counterexample: omitted synthetic + re-pin no longer grants."""
        inst = self.fixture()
        result = self.evaluate(inst)
        self.assertEqual(result["history_authorization"],
                         {"eligible": True, "complete": True, "synthetic": False, "why": []})
        self.assertEqual(result["production_trust"]["state"], "absent")
        self.assertEqual(result["authorization"], "unavailable")
        self.assertEqual(result["reason"], "production_trust_registry_absent")
        verified = self.run_goal(inst)
        self.assertEqual(verified["binding"]["schema"], "live-collection/3")
        self.assertIs(verified["granted"], False)
        self.assertEqual(verified["why"], ["production_trust_registry_absent"])
        self.assertEqual(verified["binding"]["production_trust"]["state"], "absent")
        self.assertIsNone(verified["binding"]["production_trust"]["registration"])
        item = self.item(inst)
        self.assertEqual(item["layer"], "mechanical_pass", item)
        self.assertTrue(item["collection"]["valid"], item)

    def test_positive_registration_grants_and_registry_is_frozen(self):
        inst = self.fixture()
        denied = self.run_goal(inst)
        self.assertIs(denied["granted"], False)
        # Registering later never upgrades the stored denial: the registry is
        # a frozen window input, so the old collection becomes invalid.
        self.write_registry(self.registration())
        item = self.item(inst)
        self.assertNotEqual(item["layer"], "verified", item)
        self.assertFalse(item["collection"]["valid"], item)
        verified = self.run_goal(inst)
        self.assertIs(verified["granted"], True, verified["why"])
        self.assertEqual(verified["why"], [])
        trust = verified["binding"]["production_trust"]
        self.assertEqual((trust["state"], trust["registration"], trust["class"]),
                         ("trusted", REGISTRATION_ID, "production"))
        self.assertEqual(trust["registry_sha256"], review._sha(self.registry.read_bytes()))
        item = self.item(inst)
        self.assertEqual(item["layer"], "verified", item)
        # Any in-place byte change invalidates; restoring identical bytes in
        # the same file restores the frozen identity.
        raw = self.registry.read_bytes()
        self.registry.write_bytes(raw + b"\n")
        item = self.item(inst)
        self.assertNotEqual(item["layer"], "verified", item)
        self.assertFalse(item["collection"]["valid"], item)
        self.registry.write_bytes(raw)
        self.assertEqual(self.item(inst)["layer"], "verified")
        # Frozen replay reads the registry from the snapshot, not the disk.
        snap = live._capture_inputs(inst, rounds=1)
        before = live._evaluate_snapshot(snap)
        self.registry.unlink()
        self.assertEqual(live._evaluate_snapshot(snap), before)
        self.assertFalse(live._revalidate_inputs(inst, snap.descriptor)["valid"])
        self.assertNotEqual(self.item(inst)["layer"], "verified")
        # A registry written again (here with different bytes, so the check does
        # not depend on inode reuse) keeps the old grant invalid until a fresh
        # collection is committed.
        self.registry.write_bytes(raw + b" ")
        self.assertFalse(self.item(inst)["collection"]["valid"])
        self.assertIs(self.run_goal(inst)["granted"], True)
        self.assertEqual(self.item(inst)["layer"], "verified")
        # A forged stored registration identity is re-evaluated and rejected.
        state_path = inst.wf / "goals_state.json"
        records = json.loads(state_path.read_bytes())
        records["C5T"]["verified"]["binding"]["production_trust"]["registration"] = "forged"
        core.write_json(state_path, records)
        item = self.item(inst)
        self.assertNotEqual(item["layer"], "verified", item)
        self.assertFalse(item["collection"]["valid"], item)

    def test_registry_negative_matrix(self):
        inst = self.fixture()
        sha = review._sha(self.env.verifier.read_bytes())
        other = self.base / "other-verifier.py"
        cases = [
            ("corrupt", b'{"schema": "production-trust/1", "registrations": [', "production_trust_registry_corrupt"),
            ("unsupported", {"schema": "production-trust/2", "registrations": [self.registration()]},
             "production_trust_registry_schema_unsupported"),
            ("corrupt", {"schema": live.TRUST_SCHEMA, "registrations": []}, "production_trust_registry_invalid:registrations"),
            ("corrupt", {"schema": live.TRUST_SCHEMA, "registrations": [self.registration()], "allow_all": True},
             "production_trust_registry_invalid:schema"),
            ("corrupt", [self.registration(note="x")], "production_trust_registry_invalid:registration"),
            ("corrupt", [self.registration(), self.registration(id="twin", verifier={"path": str(other), "sha256": sha})],
             "production_trust_registry_invalid:duplicate_sha256"),
            ("corrupt", [self.registration(verifier={"path": "relative/verifier.py", "sha256": sha})],
             "production_trust_registry_invalid:verifier"),
            ("unregistered", [self.registration(verifier={"path": str(other), "sha256": sha})],
             "production_trust_verifier_unregistered"),
            ("mismatch", [self.registration(verifier={"path": str(self.env.verifier), "sha256": "0" * 64})],
             "production_trust_verifier_sha256_mismatch"),
            ("synthetic", [self.registration(**{"class": "synthetic"})], "production_trust_registration_synthetic"),
            ("mismatch", [self.registration(identity={"platform": "another-platform", "models": [MODEL]})],
             "production_trust_platform_mismatch"),
            ("mismatch", [self.registration(identity={"platform": PLATFORM, "models": ["another-model"]})],
             "production_trust_model_mismatch"),
        ]
        for state, payload, reason in cases:
            if isinstance(payload, list):
                self.write_registry(*payload)
            else:
                self.write_registry(document=payload)
            result = self.evaluate(inst)
            trust = result["production_trust"]
            self.assertTrue(result["history_authorization"]["eligible"], reason)
            self.assertEqual((trust["state"], trust["granted"], trust["why"]), (state, False, [reason]), reason)
            self.assertEqual((result["authorization"], result["reason"]), ("unavailable", reason), reason)
        self.write_registry(self.registration())
        self.assertEqual(self.evaluate(inst)["authorization"], "available")

    def test_symlinked_registry_is_unsupported(self):
        inst = self.fixture()
        target = self.base / "registry-target.json"
        target.write_text(json.dumps({"schema": live.TRUST_SCHEMA, "registrations": [self.registration()]}))
        self.registry.symlink_to(target)
        with self.assertRaises(live._InputProblem) as error:
            live._capture_inputs(inst, rounds=1)
        self.assertEqual(error.exception.state, "unsupported")


class MarkedSyntheticTests(_Fixture):
    mode = "marked"
    trust_reason = "production_trust_transport_synthetic"

    def test_marked_fixture_cannot_be_registered_into_production(self):
        """An explicitly synthetic fixture stays synthetic even if registered."""
        inst = self.fixture()
        self.write_registry(self.registration())
        result = self.evaluate(inst)
        self.assertEqual(result["history_authorization"]["why"], ["synthetic_install_history"])
        self.assertEqual(result["production_trust"]["why"], [self.trust_reason])
        self.assertFalse(result["production_trust"]["granted"])
        self.assertEqual(result["reason"], "synthetic_install_history")
        verified = self.run_goal(inst)
        self.assertIs(verified["granted"], False)
        self.assertEqual(verified["why"], ["synthetic_install_history"])
        self.assertEqual(self.item(inst)["layer"], "mechanical_pass")


class OutputSyntheticTests(MarkedSyntheticTests):
    """The verifier output alone says synthetic; transport omits the flag."""
    mode = "output"
    trust_reason = "production_trust_history_synthetic"


def sealed(path, body):
    """Write a store record exactly as release._record_new seals it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(review._canon(dict(body, body_sha256=release._digest(body))))


class TransactionInventoryTests(_Fixture):
    def blocked_after_prepare(self, inst):
        """Third real apply_zip(by=driver), no exec: prepared, then a real block."""
        from unittest.mock import patch
        package = self.env.pkg("v0.3", {"PROJECT.md": _project_md("v0.3").encode(),
                                         "CHANGELOG.md": b"# CHANGELOG\n\n## v0.3 - t\n- blocked\n",
                                         "app.py": b"v0.3"})
        rid = review.prepare(inst, package, kind="project", author="B")["request_id"]
        self.assertTrue(review.run(rid)["allowed"])
        original = release._InstallTx.__init__

        def prepared_then_block(tx, *args, **kwargs):
            original(tx, *args, **kwargs)
            self.env.behave(verdict="block")
            review.run(rid)
            self.env.behave(verdict="pass")

        with patch.object(release._InstallTx, "__init__", prepared_then_block):
            with self.assertRaises(core.WfError):
                release.apply_zip(core.Instance(inst.root), review_id=rid, by="driver")

    def test_execless_failed_transaction_blocks_and_counts(self):
        inst = self.fixture()
        self.write_registry(self.registration())
        seqs = len(live._strict_log(inst.events_path.read_bytes(), inst.events_path))
        self.assertEqual(self.evaluate(inst)["authorization"], "available")
        self.blocked_after_prepare(inst)
        self.assertEqual(len(live._strict_log(inst.events_path.read_bytes(), inst.events_path)), seqs)
        store = inst.evidence / "install"
        failed = [p.name for p in store.iterdir() if (p / "failed.json").exists()]
        self.assertEqual(len(failed), 1)
        self.assertEqual(sorted(x.name for x in (store / failed[0]).glob("*.json")),
                         ["baseline.json", "failed.json", "prepared.json"])
        snap = live._capture_inputs(inst, rounds=1)
        result = live._evaluate_snapshot(snap)
        rows = [r for r in result["window_inputs"]["review_inputs"]["entries"] if r.get("transaction")]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["seq"], row["turn"], row["reference"], row["state"], row["diagnostics"]),
                         (None, 1, None, "failed", [live.TRANSACTION_DIAGNOSTIC]))
        self.assertEqual((row["transaction"]["store"], row["transaction"]["id"], row["transaction"]["attribution"]),
                         ("project", failed[0], "turn"))
        self.assertEqual({r["record"]: r["state"] for r in row["transaction"]["records"]},
                         {"baseline.json": None, "failed.json": "failed", "prepared.json": "prepared"})
        self.assertEqual((result["authorization"], result["reason"]), ("unavailable", "install_history:failed"))
        criterion = next(c for c in result["report"]["rounds"][0]["criteria"] if c["n"] == 4)
        self.assertEqual(criterion["status"], "attempt_failed")
        self.assertEqual(criterion["attempts"], {"total": 2, "ok": 1, "timeout": 0, "unknown": 0,
                                                 "unbound_transactions": 1, "failed": 1})
        self.assertIn(failed[0], criterion["why"])
        self.assertFalse(result["report"]["summary"]["passed_strict"])
        from unittest.mock import patch
        with patch.object(live, "_file_source", side_effect=AssertionError("replay read disk")), \
             patch.object(Path, "resolve", side_effect=AssertionError("replay resolved a path")):
            self.assertEqual(live._evaluate_snapshot(snap), result)
        self.assertTrue(live._revalidate_inputs(inst, snap.descriptor)["valid"])
        (store / failed[0] / "failed.json").chmod(0o600)
        (store / failed[0] / "failed.json").write_bytes(b"{}")
        self.assertFalse(live._revalidate_inputs(inst, snap.descriptor)["valid"])
        self.assertEqual(self.evaluate(inst)["window_inputs"]["review_inputs"]["entries"][-1]["state"], "corrupt")

    def test_store_records_attribution_restore_and_corrupt(self):
        inst = self.fixture()
        self.write_registry(self.registration())
        tool_store = self.env.tool / "local" / "install"
        self.assertTrue(tool_store.is_dir())
        # Time unknown (no prepared record): blocks even outside any round.
        lost = tool_store / ("tx-" + "a" * 32)
        sealed(lost / "failed.json", {"schema": "install_tx/1", "state": "failed", "tx_id": lost.name})
        result = self.evaluate(inst)
        row = next(r for r in result["window_inputs"]["review_inputs"]["entries"] if r.get("transaction"))
        self.assertEqual((row["turn"], row["state"], row["transaction"]["attribution"], row["diagnostics"]),
                         (None, "corrupt", "time_unknown",
                          [live.TRANSACTION_DIAGNOSTIC, "transaction_record_missing:prepared.json"]))
        self.assertTrue(result["history_authorization"]["eligible"])
        self.assertEqual(result["install_transactions"]["why"],
                         ["install_transaction_corrupt", "install_transaction_time_unknown"])
        self.assertEqual(result["reason"], "install_transaction_corrupt；install_transaction_time_unknown")
        for path in sorted(lost.iterdir()):
            path.unlink()
        lost.rmdir()
        # Before the first round: listed, not blocking.
        old = tool_store / ("tx-" + "b" * 32)
        sealed(old / "prepared.json", {"schema": "install_tx/1", "state": "prepared", "tx_id": old.name,
                                       "lock": {"acquired_at": "2020-01-01T00:00:00+00:00"}})
        result = self.evaluate(inst)
        row = next(r for r in result["window_inputs"]["review_inputs"]["entries"] if r.get("transaction"))
        self.assertEqual((row["turn"], row["state"], row["transaction"]["attribution"]),
                         (None, "unknown", "before_first_turn"))
        self.assertEqual(result["authorization"], "available")
        # A restore job inside the round is an unbound target mutation.
        restore = inst.evidence / "install" / ("restore-" + "c" * 32)
        sealed(restore / "prepared.json", {"schema": "install_restore/1", "state": "prepared",
                                           "restore_id": restore.name, "prepared_at": core.now().isoformat()})
        result = self.evaluate(inst)
        row = next(r for r in result["window_inputs"]["review_inputs"]["entries"]
                   if (r.get("transaction") or {}).get("id") == restore.name)
        self.assertEqual((row["turn"], row["state"], row["transaction"]["job"]), (1, "unknown", "restore"))
        self.assertEqual((result["authorization"], result["reason"]), ("unavailable", "install_history:unknown"))
        # An unsealed record is corrupt, never a bound or successful attempt.
        bad = inst.evidence / "install" / ("tx-" + "d" * 32)
        bad.mkdir()
        (bad / "prepared.json").write_text(json.dumps({"state": "completed"}))
        rows = {r["transaction"]["id"]: r for r in self.evaluate(inst)["window_inputs"]["review_inputs"]["entries"]
                if r.get("transaction")}
        self.assertEqual((rows[bad.name]["state"], rows[bad.name]["diagnostics"]),
                         ("corrupt", [live.TRANSACTION_DIAGNOSTIC, "transaction_record_corrupt:prepared.json"]))


def prepared_body(txid, **change):
    body = {"schema": "install_tx/1", "state": "prepared", "tx_id": txid,
            "lock": {"acquired_at": core.now().isoformat()}}
    body.update(change)
    return body


class TransactionProtocolTests(_Fixture):
    def test_record_shapes_are_validated_not_crashing(self):
        """Seal is not enough: schema/state/id/lock/link must match the writer."""
        import shutil
        inst = self.fixture()
        self.write_registry(self.registration())
        self.assertEqual(self.evaluate(inst)["authorization"], "available")
        store = inst.evidence / "install"
        tx = "tx-" + "e" * 32
        restore = "restore-" + "e" * 32
        cases = [
            ("lock-string", tx, {"prepared.json": prepared_body(tx, lock="not-an-object")},
             "transaction_record_field:prepared.json:lock"),
            ("lock-empty", tx, {"prepared.json": prepared_body(tx, lock={})}, "transaction_record_field:prepared.json:lock"),
            ("lock-naive", tx, {"prepared.json": prepared_body(tx, lock={"acquired_at": "2026-10-15T00:00:00"})},
             "transaction_record_field:prepared.json:lock"),
            ("schema", tx, {"prepared.json": prepared_body(tx, schema="install_tx/9")},
             "transaction_record_schema:prepared.json"),
            ("state", tx, {"prepared.json": prepared_body(tx, state="completed")},
             "transaction_record_field:prepared.json:state"),
            ("tx_id", tx, {"prepared.json": prepared_body("tx-" + "f" * 32)}, "transaction_record_field:prepared.json:tx_id"),
            ("unknown-record", tx, {"prepared.json": prepared_body(tx),
                                    "mystery.json": {"schema": "install_tx/1", "state": "prepared", "tx_id": tx}},
             "transaction_record_unknown:mystery.json"),
            ("unlinked-failed", tx, {"prepared.json": prepared_body(tx),
                                     "failed.json": {"schema": "install_tx/1", "state": "failed", "tx_id": tx,
                                                     "prepared_sha256": "0" * 64}},
             "transaction_record_field:failed.json:prepared_sha256"),
            ("baseline-schema", tx, {"prepared.json": prepared_body(tx),
                                     "baseline.json": {"schema": "baseline/9", "tx_id": tx}},
             "transaction_record_schema:baseline.json"),
            ("bad-id", "tx-" + "Z" * 32, {"prepared.json": prepared_body("tx-" + "Z" * 32)}, "transaction_id_invalid"),
            ("restore-time", restore, {"prepared.json": {"schema": "install_restore/1", "state": "prepared",
                                                         "restore_id": restore}},
             "transaction_record_field:prepared.json:prepared_at"),
        ]
        for label, name, records, diagnostic in cases:
            for record, body in records.items():
                sealed(store / name / record, body)
            snap = live._capture_inputs(inst, rounds=1)
            result = live._evaluate_snapshot(snap)
            row = next(r for r in result["window_inputs"]["review_inputs"]["entries"]
                       if (r.get("transaction") or {}).get("id") == name)
            self.assertEqual(row["state"], "corrupt", label)
            self.assertIn(diagnostic, row["diagnostics"], label)
            self.assertIsNone(row["reference"], label)
            self.assertIn("install_transaction_corrupt", result["install_transactions"]["why"], label)
            self.assertEqual(result["authorization"], "unavailable", label)
            if label == "lock-string":
                # A bad field is never an ordinary unknown time that could be ignored.
                self.assertEqual((row["turn"], row["transaction"]["attribution"]), (None, "time_unknown"))
                self.assertEqual(result["reason"], "install_transaction_corrupt；install_transaction_time_unknown")
                from unittest.mock import patch
                with patch.object(live, "_file_source", side_effect=AssertionError("replay read disk")):
                    self.assertEqual(live._evaluate_snapshot(snap), result)
            shutil.rmtree(store / name)
        # A record that is a directory is explicit, too.
        (store / tx / "prepared.json").mkdir(parents=True)
        row = next(r for r in self.evaluate(inst)["window_inputs"]["review_inputs"]["entries"]
                   if (r.get("transaction") or {}).get("id") == tx)
        self.assertEqual((row["state"], row["diagnostics"]),
                         ("corrupt", [live.TRANSACTION_DIAGNOSTIC, "transaction_record_not_file:prepared.json"]))
        shutil.rmtree(store / tx)
        self.assertEqual(self.evaluate(inst)["authorization"], "available")


def live_history_reader(inst):
    """Strict capture reader over current disk, as live._capture_inputs builds it."""
    from .live_history import CaptureReader
    return CaptureReader(lambda path: live._file_source(path)[1], live._directory_source)


class LegacyEntryTests(_Fixture):
    def fake_entries(self):
        space = self.base / "space path"
        space.mkdir()
        wf_py = space / "wf.py"
        wf_py.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        fake_bin = self.base / "fake bin"
        fake_bin.mkdir()
        wf = fake_bin / "wf"
        wf.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        wf.chmod(0o755)
        return wf_py, wf

    def drill_with_child_receipt(self):
        """A wf.py drill entry whose inner run leaks a child-project receipt."""
        folder = self.base / "drill entry"
        folder.mkdir()
        script = folder / "wf.py"
        script.write_text(
            "import json, os, sys\n"
            "ctx = json.loads(os.environ['WF_INSTALL_EXEC_CONTEXT'])\n"
            "child = sys.argv[sys.argv.index('--dir') + 1] + '/project'\n"
            "ref = {'schema': 'install-ref/1', 'tx_id': 'tx-' + '1' * 32, 'request_id': 'rq-' + '1' * 32,\n"
            "       'content_id': '1' * 64, 'package_sha256': '1' * 64,\n"
            "       'target': {'kind': 'project', 'root': child}, 'prepared_sha256': '1' * 64}\n"
            "row = {'schema': 'install-exec-receipt/1', 'exec': ctx, 'install_ref': ref}\n"
            "os.write(int(os.environ['WF_INSTALL_RECEIPT_FD']), (json.dumps(row) + '\\n').encode())\n",
            encoding="utf-8")
        return script

    def test_real_legacy_execs_without_receipt_are_unknown(self):
        inst = self.fixture()
        wf_py, wf = self.fake_entries()
        commands = {
            "tool": shlex.join([sys.executable, str(wf_py), "self-update", "legacy.zip"]),
            "project": shlex.join([str(wf), "apply", "legacy.zip"]),
            "long": ": " + "x" * 320 + " && " + shlex.join([str(wf), "--project", inst.name, "apply", "l.zip"]),
            "echo": "echo 'wf self-update legacy.zip'",
            "plain": "true",
            "drill": shlex.join([sys.executable, str(wf_py), "drill", "review", "--kind", "block",
                                 "--dir", str(self.base / "drill-a"), "--review", str(self.env.runner)]),
            "drill_receipt": shlex.join([sys.executable, str(self.drill_with_child_receipt()), "--json", "drill",
                                         "review", "--kind", "timeout", "--dir", str(self.base / "drill-b"),
                                         "--review", str(self.env.runner)]),
        }
        seqs = {}
        for name, command in commands.items():
            self.assertEqual(ops.run_exec(inst, command, by="driver"), 0, name)
            seqs[name] = live._strict_log(inst.events_path.read_bytes(), inst.events_path)[-1]["seq"]
        result = self.evaluate(inst)
        rows = {row["seq"]: row for row in result["window_inputs"]["review_inputs"]["entries"]}
        for name, kind in (("tool", "tool"), ("project", "project"), ("long", "project")):
            row = rows[seqs[name]]
            self.assertEqual((row["state"], row["reference"], row["diagnostics"]),
                             ("unknown", None, ["installation_receipt_has_no_transaction"]), name)
            self.assertEqual(row["legacy_command"]["kind"], kind, name)
            self.assertNotIn("install_ref", row)
        self.assertNotIn(seqs["echo"], rows)
        self.assertNotIn(seqs["plain"], rows)
        # Inner review: the child-project ref really reached the parent receipt
        # but is not grafted; the parent row stays unknown with reference=None.
        events = {e["seq"]: e for e in live._strict_log(inst.events_path.read_bytes(), inst.events_path)}
        self.assertEqual(events[seqs["drill"]]["install_refs"], [])
        leaked = events[seqs["drill_receipt"]]["install_refs"]
        self.assertEqual(leaked[0]["target"], {"kind": "project", "root": str(self.base / "drill-b" / "project")})
        for name, diagnostics in (("drill", [live.INNER_REVIEW_DIAGNOSTIC]),
                                  ("drill_receipt", [live.INNER_REVIEW_DIAGNOSTIC, live.INNER_REVIEW_RECEIPT_DIAGNOSTIC])):
            row = rows[seqs[name]]
            self.assertEqual((row["state"], row["reference"], row["diagnostics"], row["exit_code"]),
                             ("unknown", None, diagnostics, 0), name)
            self.assertEqual(row["legacy_command"]["kind"], "inner_review", name)
            self.assertNotIn("history", row)
        self.assertFalse(any(row.get("reference") == leaked[0] for row in rows.values()))
        # Without the inner-review boundary B's provider would reject the foreign
        # root for the whole window instead of keeping the attempt visible.
        from .live_history import collect_bound_histories
        with self.assertRaises(live._InputProblem) as error:
            collect_bound_histories(inst, [events[seqs["drill_receipt"]]], live_history_reader(inst),
                                    local=core.LOCAL, tool_root=self.env.tool)
        self.assertEqual(error.exception.state, "corrupt")
        self.assertIn("install_history:unknown", result["history_authorization"]["why"])
        self.assertEqual(result["authorization"], "unavailable")
        criterion = next(c for c in result["report"]["rounds"][0]["criteria"] if c["n"] == 4)
        self.assertFalse(criterion["ok"])
        self.assertIn("installation_receipt_has_no_transaction", criterion["why"])
        self.assertEqual(criterion["attempts"]["total"], 2)   # protected tool install + legacy tool entry; drill is not a tool try
        # Frozen replay reproduces the same rows from the snapshot.
        snap = live._capture_inputs(inst, rounds=1)
        self.assertEqual(live._evaluate_snapshot(snap)["window_inputs"]["review_inputs"],
                         result["window_inputs"]["review_inputs"])

    def test_real_drill_review_parent_exec_is_unknown(self):
        """CT3: the actual wf.py drill review, outer exit 0, no parent entry before."""
        inst = self.fixture()
        wf_py = Path(live.__file__).resolve().parent.parent / "wf.py"
        seqs = {}
        for kind in ("block", "timeout"):
            command = shlex.join([sys.executable, str(wf_py), "drill", "review", "--kind", kind,
                                  "--dir", str(self.base / f"real drill {kind}"), "--review", str(self.env.runner),
                                  "--review-timeout-s", "3"]) + " || true"
            self.assertEqual(ops.run_exec(inst, command, by="driver", timeout=240), 0)
            event = live._strict_log(inst.events_path.read_bytes(), inst.events_path)[-1]
            self.assertEqual(event["install_refs"], [])
            self.assertTrue((self.base / f"real drill {kind}" / "drill-result.json").exists())
            seqs[kind] = event["seq"]
        result = self.evaluate(inst)
        rows = {r["seq"]: r for r in result["window_inputs"]["review_inputs"]["entries"]}
        for kind, seq in seqs.items():
            self.assertEqual((rows[seq]["state"], rows[seq]["reference"], rows[seq]["diagnostics"],
                              rows[seq]["legacy_command"]["kind"]),
                             ("unknown", None, [live.INNER_REVIEW_DIAGNOSTIC], "inner_review"), kind)
        self.assertIn("install_history:unknown", result["history_authorization"]["why"])

    def legacy_only_fixture(self, legacy=None):
        """Same live window as the C5 fixture but with no structured receipt."""
        from datetime import timedelta
        from .selftest_review import _git
        inst = self.project("legacy only window")
        inst.state.update(state="EXECUTE", turn=1)
        inst.save()
        at = core.now()
        sent, done = at - timedelta(minutes=1), at + timedelta(seconds=2)
        for kind, fields in (("reply_binding", {"receipt": "ok", "deliveries": "ok"}),
                             ("reply_checked", {}), ("round_result", {"ok": True})):
            inst.event(kind, by="driver", **fields)
        legacy = legacy if legacy is not None else {
            "project": "wf apply legacy.zip",
            "tool_failed": shlex.join([sys.executable, "/space path/wf.py", "self-update", "legacy.zip"]),
            "drill": shlex.join([sys.executable, "/space path/wf.py", "drill", "review", "--kind", "block",
                                 "--dir", "/d", "--review", "/r"]),
            "echo": "echo 'wf self-update legacy.zip'",
            "tool_absent": "wf self-update --auto workflow/outbox/missing.zip",
        }
        for name, command in legacy.items():
            inst.event("exec", cid=f"c{len(name):02d}", cmd=command, exit_code=1 if name == "tool_failed" else 0,
                       duration_s=0.1, by="driver")
        inst.turns_path.write_text(
            json.dumps({"turn": 1, "status": "sent", "from": "driver", "ts_sent": sent.isoformat()}) + "\n"
            + json.dumps({"turn": 1, "status": "done", "ts_done": done.isoformat()}) + "\n", encoding="utf-8")
        td = inst.turn_dir()
        for name, doc in (("intake.json", {"commands": list(legacy.values())}),
                          ("send_meta.json", {"message_uuid": "c5-parent"}),
                          ("reply_meta.json", {"parent": "c5-parent", "ended_at": done.isoformat()})):
            (td / name).write_text(json.dumps(doc), encoding="utf-8")
        (td / "notes.jsonl").write_bytes(b"")
        adapter = self.base / "legacy-adapter.jsonl"
        adapter.write_text(json.dumps({"action": "send", "turn": 1, "ts": sent.isoformat(),
                                       "project": inst.name, "root": str(inst.root)}) + "\n", encoding="utf-8")
        cfg = json.loads(self.env.transport.read_bytes())
        cfg["log"] = str(adapter)
        self.env.transport.write_text(json.dumps(cfg), encoding="utf-8")
        head = _git(inst.root, "rev-parse", "HEAD")
        sync = core.LOCAL / "sync" / (inst.name + ".history.jsonl")
        sync.parent.mkdir(parents=True, exist_ok=True)
        sync.write_text(json.dumps({"turn": 1, "status": "pushed", "commit": head, "head": head,
                                    "ts": done.isoformat(), "project": inst.name, "root": str(inst.root)}) + "\n",
                        encoding="utf-8")
        events = live._strict_log(inst.events_path.read_bytes(), inst.events_path)
        self.assertFalse(any("install_exec" in e for e in events))
        return inst, {name: next(e["seq"] for e in events if e.get("cmd") == command)
                      for name, command in legacy.items()}

    def test_pure_legacy_window_goes_through_history_capture(self):
        inst, seqs = self.legacy_only_fixture()
        snap = live._capture_inputs(inst, rounds=1)
        descriptor = snap.descriptor
        self.assertEqual(descriptor["review_inputs"]["schema"], "live-install-history/1")
        self.assertIs(descriptor["complete"], False)   # collected, but no structured receipts
        self.assertEqual(descriptor["trust_inputs"]["schema"], live.TRUST_INPUTS_SCHEMA)
        rows = {row["seq"]: row for row in descriptor["review_inputs"]["entries"]}
        self.assertEqual({name: (rows[seq]["state"], rows[seq]["reference"], rows[seq]["diagnostics"],
                                 rows[seq]["legacy_command"]["kind"])
                          for name, seq in seqs.items() if name != "echo"},
                         {"project": ("unknown", None, ["legacy_install_reference_unavailable"], "project"),
                          "tool_failed": ("unknown", None, ["legacy_install_reference_unavailable"], "tool"),
                          "drill": ("unknown", None, [live.INNER_REVIEW_DIAGNOSTIC], "inner_review"),
                          "tool_absent": ("unknown", None, ["legacy_install_reference_unavailable"], "tool")})
        self.assertNotIn(seqs["echo"], rows)
        # An exit-0 legacy self-update whose package is gone is frozen as
        # absence and reported, instead of aborting the whole collection.
        absent = {s["path"]: s["state"] for s in descriptor["sources"]}
        self.assertEqual(absent[str(inst.root / "workflow/outbox/missing.zip")], "absent")
        result = live._evaluate_snapshot(snap)
        criterion = next(c for c in result["report"]["rounds"][0]["criteria"] if c["n"] == 4)
        self.assertFalse(criterion["ok"])
        self.assertIn("没有记录", criterion["why"])
        self.assertIn("install_history:unknown", result["history_authorization"]["why"])
        self.assertEqual(result["authorization"], "unavailable")
        # Capture and replay share the source: frozen replay rebuilds the same
        # rows without disk, and fresh capture reproduces the same descriptor.
        from unittest.mock import patch
        with patch.object(live, "_file_source", side_effect=AssertionError("replay read disk")):
            self.assertEqual(live._evaluate_snapshot(snap), result)
        self.assertTrue(live._revalidate_inputs(inst, descriptor)["valid"])
        # The report itself is unchanged by collecting the history rows.
        self.assertEqual(live.check(inst, rounds=1), result["report"])
        verified = self.run_goal(inst)
        self.assertIs(verified["granted"], False)
        self.assertIn("install_history:unknown", verified["why"])

    def test_uncalled_function_definition_is_not_an_install(self):
        """Real ops.run_exec: definitions, shadowing and definition redirections run no wf."""
        inst = self.fixture()
        self.assertTrue(self.evaluate(inst)["report"]["summary"]["passed_strict"])

        def stores():
            return [sorted(p.name for p in store.iterdir())
                    for store in (inst.evidence / "install", self.env.tool / "local" / "install")]

        before = stores()
        for command in ("helper() { wf self-update unused.zip; }; printf '%s\\n' ordinary",
                        "setup() { wf() { :; }; }; setup; wf self-update unused.zip",
                        'f() { :; } >"$(wf self-update unused.zip)"'):
            self.assertEqual(ops.run_exec(inst, command, by="driver"), 0, command)
            seq = live._strict_log(inst.events_path.read_bytes(), inst.events_path)[-1]["seq"]
            self.assertEqual(stores(), before, command)
            result = self.evaluate(inst)
            self.assertFalse([r for r in result["window_inputs"]["review_inputs"]["entries"] if r.get("seq") == seq],
                             command)
            criterion = next(c for c in result["report"]["rounds"][0]["criteria"] if c["n"] == 4)
            self.assertEqual(criterion["attempts"], {"total": 1, "ok": 1, "timeout": 0, "unknown": 0, "failed": 0})
            self.assertTrue(result["report"]["summary"]["passed_strict"], command)
            self.assertEqual(live.check(inst, rounds=1), result["report"])

    def test_classifier_agrees_with_real_bash_argv_trace(self):
        """Stand-in argv trace: a wf on PATH that only records its arguments."""
        import shutil
        import subprocess
        import tempfile
        if shutil.which("bash") is None:
            self.skipTest("bash unavailable")
        for command in BASH_ORACLE:
            with tempfile.TemporaryDirectory() as folder:
                folder = Path(folder)
                (folder / "bin").mkdir()
                trace = folder / "trace"
                (folder / "bin" / "wf").write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$WF_TRACE"\n')
                (folder / "bin" / "time").write_text('#!/bin/sh\nexec "$@"\n')   # stand-in external time
                for name in ("wf", "time"):
                    (folder / "bin" / name).chmod(0o755)
                command = command.replace("EXT_TIME", str(folder / "bin" / "time"))
                subprocess.run(["bash", "-c", command], cwd=folder, capture_output=True, timeout=20,
                               env={"PATH": f"{folder / 'bin'}:/usr/bin:/bin", "WF_TRACE": str(trace),
                                    "HOME": str(folder)})
                calls = trace.read_text().splitlines() if trace.exists() else []
                kinds = {"self-update": "tool", "apply": "project"}
                executed = next((kinds[c.split()[0]] for c in calls if c.split() and c.split()[0] in kinds), None)
                self.assertEqual((live._legacy_install(command) or {}).get("kind"), executed, (command, calls))

    def test_pre_receipt_events_and_classifier(self):
        reader = SimpleNamespace(bytes=lambda path: None,
                                 directory=lambda path: {"state": "absent", "members": []})
        inst = SimpleNamespace(name="p", root=Path("/p"), evidence=Path("/p/workflow/evidence"))
        events = [
            {"type": "exec", "seq": 1, "turn": 1, "cmd": "wf apply legacy.zip", "exit_code": 0},
            {"type": "exec", "seq": 2, "turn": 1, "cmd": "python3 '/space path/wf.py' self-update legacy.zip",
             "exit_code": 0},
            {"type": "exec", "seq": 3, "turn": 1, "cmd": "echo 'wf self-update legacy.zip'", "exit_code": 0},
            {"type": "exec", "seq": 4, "turn": 1, "cmd": "pytest -q", "exit_code": 0},
            {"type": "exec", "seq": 5, "turn": 1, "exit_code": 0,
             "cmd": "python3 '/space path/wf.py' drill review --kind timeout --dir /d --review /r"},
            {"type": "exec", "seq": 6, "turn": 1, "cmd": "echo 'wf drill review'", "exit_code": 0},
        ]
        bound = live._bound_histories(inst, events, reader, local="/l", tool_root="/t")
        self.assertEqual([(row["seq"], row["state"], row["legacy_command"]["kind"], row["diagnostics"])
                          for row in bound["entries"]],
                         [(1, "unknown", "project", ["legacy_install_reference_unavailable"]),
                          (2, "unknown", "tool", ["legacy_install_reference_unavailable"]),
                          (5, "unknown", "inner_review", [live.INNER_REVIEW_DIAGNOSTIC])])
        self.assertEqual([live._install_try(e, None, []) for e in events], [False, True, False, False, False, False])
        self.assertTrue(live._history_required(events[:1]))
        self.assertTrue(live._history_required(events[4:5]))
        self.assertFalse(live._history_required([events[2], events[3], events[5]]))
        positives = {
            "wf apply x.zip": ("project", "x.zip"),
            "cd sub && wf self-update --auto workflow/outbox/c.zip": ("tool", "workflow/outbox/c.zip"),
            "'/opt/collab wf/bin/wf' --project=p --json apply --review-id rq-1 'my pkg.zip'": ("project", "my pkg.zip"),
            "env A=1 timeout 60 python3 -u '/x y/wf.py' self-update z.zip": ("tool", "z.zip"),
            "bash -lc 'wf apply nested.zip'": ("project", "nested.zip"),
            "make build\nwf apply n.zip": ("project", "n.zip"),
            "wf self-update": ("tool", None),
            "wf --json drill review --kind block --dir /d --review /r": ("inner_review", None),
            "cd /x && python3 '/a b/wf.py' drill --kind timeout review --dir /d --review /r": ("inner_review", None),
            "wf apply x.zip && wf drill review --kind block --dir /d --review /r": ("inner_review", None),
            "wf apply x.zip; wf self-update y.zip": ("project", "x.zip"),
            'echo "$(wf apply sub.zip)"': ("project", "sub.zip"),
            "echo `wf self-update tick.zip`": ("tool", "tick.zip"),
            "if true; then wf apply cond.zip; fi": ("project", "cond.zip"),
            "{ wf apply group.zip; }": ("project", "group.zip"),
            "2>/dev/null wf apply fd.zip": ("project", "fd.zip"),
            "wf apply r.zip > log 2>&1": ("project", "r.zip"),
            "cat <<EOF\n$(wf apply here.zip)\nEOF": ("project", "here.zip"),
            "echo x # note\nwf apply after.zip": ("project", "after.zip"),
            "helper() { wf self-update used.zip; }; helper": ("tool", "used.zip"),
            "function h { wf apply fn.zip; }\nh": ("project", "fn.zip"),
            'f() { echo "$(wf apply sub.zip)"; }; f': ("project", "sub.zip"),
            'f() { wf apply out.zip; }; echo "$(f)"': ("project", "out.zip"),
            "g() { f; }; f() { wf apply late.zip; }; g": ("project", "late.zip"),
            "wf() { echo shadow; }; command wf apply real.zip": ("project", "real.zip"),
            "f() ( wf apply paren.zip ); f > log": ("project", "paren.zip"),
            "f() { wf self-update used.zip; }; time f": ("tool", "used.zip"),
            "setup() { f() { wf self-update used.zip; }; }; setup; f": ("tool", "used.zip"),
            "f() { :; }; setup() { f() { wf self-update used.zip; }; }; setup; f": ("tool", "used.zip"),
            'f() { :; } >"$(wf self-update used.zip)"; f': ("tool", "used.zip"),
            "(wf() { :; }); wf self-update leak.zip": ("tool", "leak.zip"),
        }
        for command, (kind, argument) in positives.items():
            got = live._legacy_install(command)
            self.assertEqual((got["kind"], got["argument"], got["parsed"]), (kind, argument, True), command)
        for command in ("echo 'wf apply x.zip'", "grep 'wf self-update' log", "python3 -c 'print(1)'",
                        "python3 wf.py selftest", "wf status", "make apply", "exit 124", "echo 'oops",
                        "echo 'wf drill review'", "wf drill", "wf drill other --kind block",
                        "echo ';' wf apply x.zip", "echo '&&' wf self-update y.zip", "printf '%s\\n' '|' wf apply x",
                        "echo hi > wf apply x.zip", "make # wf apply x.zip", "cat <<'EOF'\nwf apply x.zip\nEOF",
                        "cat <<EOF\nwf apply x.zip\nEOF", 'echo "a; wf apply x.zip"', "echo a\\;wf apply x",
                        "cat <<< wf apply x",
                        "helper() { wf self-update unused.zip; }; printf '%s\\n' ordinary",
                        "function helper { wf apply x.zip; }", "f() ( wf apply x.zip )",
                        'f() { echo "$(wf apply x.zip)"; }', "f() {\ncat <<EOF\n$(wf apply x.zip)\nEOF\n}",
                        "wf() { echo shadow; }; wf apply x.zip", "helper; helper() { wf apply x.zip; }",
                        "setup() { wf() { :; }; }; setup; wf self-update unused.zip",
                        'f() { :; } >"$(wf self-update unused.zip)"', "command -v wf self-update",
                        "f() { wf apply x.zip; }; command f", "f() { wf apply x.zip; }; env f"):
            self.assertIsNone(live._legacy_install(command), command)
        unparsable = live._legacy_install("wf apply 'x.zip")
        self.assertEqual((unparsable["parsed"], unparsable["kind"], unparsable["argument"]), (False, None, None))


class ExeclessWindowTests(_Fixture):
    legacy_only_fixture = LegacyEntryTests.legacy_only_fixture
    blocked_after_prepare = TransactionInventoryTests.blocked_after_prepare

    def ordinary(self):
        """Pre-receipt round whose only exec is ``true`` (no receipt, no legacy entry)."""
        inst, seqs = self.legacy_only_fixture({"plain": "true"})
        self.assertFalse(live._history_required(live._strict_log(inst.events_path.read_bytes(), inst.events_path)))
        return inst

    def test_ordinary_window_lists_execless_failed_transaction(self):
        inst = self.ordinary()
        before = live._capture_inputs(inst, rounds=1)
        result = live._evaluate_snapshot(before)
        self.assertEqual(before.descriptor["review_inputs"],
                         {"state": "unavailable", "reason": "install_history_unavailable",
                          "transaction_stores": [
                              {"store": "project", "path": str(inst.evidence / "install"), "state": "absent"},
                              {"store": "tool", "path": str(self.env.tool / "local" / "install"), "state": "absent"}]})
        self.assertEqual(next(c for c in result["report"]["rounds"][0]["criteria"] if c["n"] == 4)["status"],
                         "not_applicable")
        self.assertEqual(live.check(inst, rounds=1), result["report"])
        self.blocked_after_prepare(inst)
        # The frozen store listing gates history: the old window is now stale.
        self.assertFalse(live._revalidate_inputs(inst, before.descriptor)["valid"])
        snap = live._capture_inputs(inst, rounds=1)
        result = live._evaluate_snapshot(snap)
        inputs = snap.descriptor["review_inputs"]
        self.assertEqual(inputs["schema"], "live-install-history/1")
        self.assertIs(snap.descriptor["complete"], False)
        rows = [r for r in inputs["entries"] if r.get("transaction")]
        self.assertEqual([(r["seq"], r["turn"], r["reference"], r["state"], r["transaction"]["store"])
                          for r in rows], [(None, 1, None, "failed", "project")])
        criterion = next(c for c in result["report"]["rounds"][0]["criteria"] if c["n"] == 4)
        self.assertEqual(criterion["status"], "attempt_failed")
        self.assertEqual(criterion["attempts"], {"total": 1, "ok": 0, "timeout": 0, "unknown": 0,
                                                 "unbound_transactions": 1, "failed": 1})
        self.assertIn(rows[0]["transaction"]["id"], criterion["why"])
        self.assertEqual(result["authorization"], "unavailable")
        self.assertIn("install_history:failed", result["history_authorization"]["why"])
        self.assertEqual([s["state"] for s in result["install_transactions"]["stores"]], ["present", "absent"])
        from unittest.mock import patch
        with patch.object(live, "_file_source", side_effect=AssertionError("replay read disk")), \
             patch.object(Path, "resolve", side_effect=AssertionError("replay resolved a path")):
            self.assertEqual(live._evaluate_snapshot(snap), result)
        self.assertTrue(live._revalidate_inputs(inst, snap.descriptor)["valid"])
        verified = self.run_goal(inst)
        self.assertIs(verified["granted"], False)

    def direct_matches_frozen(self, legacy):
        from unittest.mock import patch
        inst, _ = self.legacy_only_fixture(legacy)
        events = live._strict_log(inst.events_path.read_bytes(), inst.events_path)
        self.assertEqual(sum(e["type"] == "exec" for e in events), len(legacy))
        self.assertIsNone(live._direct_history_route(inst, events))
        before = live._capture_inputs(inst, rounds=1)
        direct = live.check(inst, rounds=1)
        self.assertEqual(direct, live._evaluate_snapshot(before)["report"])
        self.assertNotIn("authorization", direct)   # ordinary window: unchanged legacy report
        self.blocked_after_prepare(inst)
        self.assertIs(live._revalidate_inputs(inst, before.descriptor)["valid"], False)
        self.assertEqual(live._direct_history_route(inst, events), "strict")
        calls = []
        original = live._capture_inputs

        def counted(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        with patch.object(live, "_capture_inputs", counted):
            direct = live.check(inst, rounds=1)
        self.assertEqual(len(calls), 1)   # the same frozen collection, no second path
        snap = live._capture_inputs(inst, rounds=1)
        result = live._evaluate_snapshot(snap)
        self.assertEqual(direct, result["report"])
        criterion = next(c for c in direct["rounds"][0]["criteria"] if c["n"] == 4)
        self.assertEqual(criterion["status"], "attempt_failed")
        self.assertEqual(criterion["attempts"], {"total": 1, "ok": 0, "timeout": 0, "unknown": 0,
                                                 "unbound_transactions": 1, "failed": 1})
        unbound = direct["authorization"]["unbound_transactions"]
        self.assertEqual([(u["store"], u["state"], u["turn"], u["attribution"]) for u in unbound],
                         [("project", "failed", 1, "turn")])
        self.assertIn(unbound[0]["id"], criterion["why"])
        self.assertEqual(direct["authorization"]["state"], "unavailable")
        self.assertIn("install_history:failed", direct["authorization"]["why"])
        self.assertFalse(direct["summary"]["passed_strict"])
        with patch.object(live, "_file_source", side_effect=AssertionError("replay read disk")), \
             patch.object(live, "_directory_source", side_effect=AssertionError("replay listed disk")):
            self.assertEqual(live._evaluate_snapshot(snap), result)
        return inst

    def test_direct_check_zero_exec_window_lists_execless_transaction(self):
        self.direct_matches_frozen({})

    def test_direct_check_true_only_window_lists_execless_transaction(self):
        inst = self.direct_matches_frozen({"plain": "true"})
        # A transaction window never falls back to a report that omits it.
        cfg = json.loads(self.env.transport.read_bytes())
        adapter = Path(cfg["log"])
        adapter.write_text(adapter.read_text().replace('"root"', '"elsewhere"'), encoding="utf-8")
        report = live.check(inst, rounds=1)
        self.assertEqual((report["rounds"], report["passed"], report["input_problem"]["state"]), ([], False, "unknown"))
        self.assertNotIn("authorization", report)

    def test_direct_check_pure_legacy_window(self):
        inst, _ = self.legacy_only_fixture()
        events = live._strict_log(inst.events_path.read_bytes(), inst.events_path)
        self.assertEqual(live._direct_history_route(inst, events), "legacy")
        result = self.evaluate(inst)
        direct = live.check(inst, rounds=1)
        self.assertEqual(direct, result["report"])
        self.assertEqual(direct["authorization"]["state"], "unavailable")
        self.assertIn("install_history:unknown", direct["authorization"]["why"])
        # When strict capture is impossible the legacy report stays available,
        # with the uncollected history made explicit.
        cfg = json.loads(self.env.transport.read_bytes())
        adapter = Path(cfg["log"])
        adapter.write_text(adapter.read_text().replace('"root"', '"elsewhere"'), encoding="utf-8")
        report = live.check(inst, rounds=1)
        self.assertTrue(report["rounds"])
        self.assertEqual((report["authorization"]["state"], report["authorization"]["why"],
                          report["authorization"]["input_problem"]["state"]),
                         ("unavailable", ["install_history_uncollected"], "unknown"))
        self.assertEqual({k: v for k, v in report.items() if k != "authorization"},
                         live.check(inst, rounds=1, _route=False))

    def stale_legacy_route(self, hook):
        """Pure-legacy window routed ``legacy``; a real blocked apply lands at ``hook``."""
        from unittest.mock import patch
        inst, _ = self.legacy_only_fixture()
        events = live._strict_log(inst.events_path.read_bytes(), inst.events_path)
        self.assertEqual(live._direct_history_route(inst, events), "legacy")
        adapter = Path(json.loads(self.env.transport.read_bytes())["log"])
        adapter.write_text(adapter.read_text().replace('"root"', '"elsewhere"'), encoding="utf-8")
        fired = []
        original = getattr(live, hook)

        def interleave(*args, **kwargs):
            if not fired and (hook == "_capture_inputs" or kwargs.get("_route") is False):
                fired.append(1)
                self.blocked_after_prepare(inst)   # real apply_zip, real review block
            return original(*args, **kwargs)

        with patch.object(live, hook, interleave):
            report = live.check(inst, rounds=1)
        self.assertEqual(fired, [1])
        failed = [p for p in (inst.evidence / "install").iterdir() if (p / "failed.json").exists()]
        self.assertEqual(len(failed), 1)
        self.assertEqual(live._strict_log(inst.events_path.read_bytes(), inst.events_path), events)
        # Never the cached legacy fallback that would omit the new transaction.
        self.assertEqual((report["rounds"], report["passed"], report["input_problem"]["state"]), ([], False, "unknown"))
        self.assertNotIn("authorization", report)
        self.assertEqual(live.check(inst, rounds=1), report)

    def test_capture_failure_rechecks_route_when_tx_appears_before_capture(self):
        self.stale_legacy_route("_capture_inputs")

    def test_capture_failure_rechecks_route_when_tx_appears_during_fallback(self):
        self.stale_legacy_route("check")

    def test_store_and_record_faults_are_explicit(self):
        import shutil
        from unittest.mock import patch
        inst = self.ordinary()

        def store_row(result):
            rows = [r for r in result["window_inputs"]["review_inputs"]["entries"] if r.get("transaction")]
            self.assertEqual(len(rows), 1, rows)
            self.assertIn("install_transaction_corrupt", result["install_transactions"]["why"])
            self.assertEqual(result["authorization"], "unavailable")
            return rows[0]

        # Tool store is a symlink.
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        tool_store = self.env.tool / "local" / "install"
        tool_store.parent.mkdir(parents=True, exist_ok=True)
        tool_store.symlink_to(elsewhere, target_is_directory=True)
        snap = live._capture_inputs(inst, rounds=1)
        result = live._evaluate_snapshot(snap)
        row = store_row(result)
        self.assertEqual((row["transaction"]["store"], row["state"], row["diagnostics"]),
                         ("tool", "corrupt", [live.TRANSACTION_DIAGNOSTIC, "transaction_store_unsupported:symlink directory"]))
        self.assertEqual(result["install_transactions"]["stores"][1]["state"], "fault")
        with patch.object(live, "_directory_source", side_effect=AssertionError("replay listed disk")):
            self.assertEqual(live._evaluate_snapshot(snap), result)
        self.assertEqual(live.check(inst, rounds=1), result["report"])
        self.assertEqual([(u["store"], u["state"]) for u in result["report"]["authorization"]["unbound_transactions"]],
                         [("tool", "corrupt")])
        self.assertEqual(result["report"]["authorization"]["state"], "unavailable")
        tool_store.unlink()
        # Project store is not a directory.
        store = inst.evidence / "install"
        store.write_text("not a directory")
        row = store_row(self.evaluate(inst))
        self.assertEqual(row["diagnostics"],
                         [live.TRANSACTION_DIAGNOSTIC, "transaction_store_unreadable:NotADirectoryError"])
        store.unlink()
        # A transaction directory with a symlinked record.
        tx = store / ("tx-" + "f" * 32)
        tx.mkdir(parents=True)
        (tx / "prepared.json").symlink_to(elsewhere)
        row = store_row(self.evaluate(inst))
        self.assertEqual((row["transaction"]["id"], row["diagnostics"]),
                         (tx.name, [live.TRANSACTION_DIAGNOSTIC, "transaction_directory_unsupported:directory member type"]))
        (tx / "prepared.json").unlink()
        # An unreadable record (fault injected at capture only) replays from
        # the frozen fault without touching disk, and fresh revalidation sees
        # that the fault no longer holds.
        sealed(tx / "prepared.json", prepared_body(tx.name))
        original = live._file_source
        target = str(tx / "prepared.json")

        def unreadable(path):
            if str(path) == target:
                raise live._InputProblem("unreadable", path, "PermissionError")
            return original(path)

        with patch.object(live, "_file_source", unreadable):
            snap = live._capture_inputs(inst, rounds=1)
        result = live._evaluate_snapshot(snap)
        row = store_row(result)
        self.assertEqual(row["diagnostics"], [live.TRANSACTION_DIAGNOSTIC, "transaction_record_unreadable:prepared.json"])
        self.assertEqual(snap.descriptor["review_inputs"]["transaction_faults"],
                         [{"fault": True, "op": "bytes", "path": target, "state": "unreadable", "detail": "PermissionError"}])
        with patch.object(live, "_file_source", side_effect=AssertionError("replay read disk")):
            self.assertEqual(live._evaluate_snapshot(snap), result)
        self.assertFalse(live._revalidate_inputs(inst, snap.descriptor)["valid"])
        readable = [r for r in self.evaluate(inst)["window_inputs"]["review_inputs"]["entries"] if r.get("transaction")]
        self.assertEqual((readable[0]["state"], readable[0]["transaction"]["attribution"]), ("unknown", "turn"))
        shutil.rmtree(store)


if __name__ == "__main__":
    unittest.main()
