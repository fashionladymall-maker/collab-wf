"""FX-S06 behaviour tests: real historical producers, the current consumer, same-project recollection.

Not registered in the aggregate selftest. Needs two caller-prepared ordinary full Git checkouts (this
module never fetches): B3_S06_LEGACY_V2 = b523be4..., B3_S06_LEGACY_B1 = 6b21d10... Synthetic L0/L1
evidence only; never production evidence and never an authorization.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from . import b3_fixtures as fx

TOOL = Path(fx.__file__).resolve().parent.parent
CHANGED_WHY = "window_inputs：窗口来源、字节或选择器已改变"
UNSUPPORTED_WHY = "旧版或未知实景凭证缺少完整严格输入闭包，必须重新采集；granted/summary 自报不能授权"


def _digest(root: Path, *, skip=(".git",)) -> dict:
    out = {}
    if not root.exists():
        return out
    for d, dirs, files in os.walk(str(root)):
        dirs[:] = sorted(x for x in dirs if x not in skip and x != "__pycache__")
        for f in sorted(files):
            p = Path(d) / f
            if p.suffix == ".pyc":
                continue
            st = os.lstat(str(p))
            out[str(p.relative_to(root))] = (os.readlink(str(p)) if os.path.islink(str(p))
                                             else hashlib.sha256(p.read_bytes()).hexdigest(), oct(st.st_mode & 0o7777))
    return out


def _temp_entries(prefixes=("wf-install-commit-", "wf-")) -> set:
    base = Path(tempfile.gettempdir())
    return {p.name for p in base.iterdir() if p.name.startswith(prefixes)}


class S06BehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v2_src, cls.b1_src = os.environ.get("B3_S06_LEGACY_V2"), os.environ.get("B3_S06_LEGACY_B1")
        if not cls.v2_src or not cls.b1_src:
            raise RuntimeError("FX-S06 behaviour tests need B3_S06_LEGACY_V2 and B3_S06_LEGACY_B1: ordinary full Git "
                               "checkouts of b523be4 and 6b21d10 prepared by the caller (see docs/B3_FIXTURES.md)")
        cls.before = {"environ": dict(os.environ), "tool": _digest(TOOL), "tool_local": _digest(TOOL / "local", skip=()),
                      "configured_local": _digest(Path(os.environ["WF_LOCAL_DIR"]), skip=()) if os.environ.get("WF_LOCAL_DIR") else None,
                      "legacy_v2": _digest(Path(cls.v2_src), skip=()), "legacy_b1": _digest(Path(cls.b1_src), skip=()),
                      "temp": _temp_entries()}
        cls.base = Path(tempfile.mkdtemp(prefix="b3-s06-")).resolve()
        cls.v2 = fx.build_live_v2(cls.base / "v2", legacy_tool_root=cls.v2_src)
        cls.b1 = fx._build_live_b1(cls.base / "b1", legacy_b1_root=cls.b1_src)
        cls.v2_read = fx._s06_consume(cls.base / "v2", fx.S06_V2_ID, "read")
        cls.b1_read = fx._s06_consume(cls.base / "b1", fx.S06_B1_ID, "read")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def project(self, built):
        return Path(built["context"]["project"])

    # ---- real producers
    def test_v2_is_real_b523_output_and_its_own_consumer_accepted_it(self):
        rec, v = self.v2["record"], self.v2["record"]["verified"]
        self.assertEqual((sorted(rec["binding"]), rec["binding"]["schema"], v["binding"]["schema"], v["collection"]["state"],
                          v["collection"]["closure"]["complete"], v["granted"]),
                         (["evidence_sha256", "goals_sha", "schema"], "goals-sha/1", "live-collection/2", "collected", True, False))
        old = self.v2["old_items"][0]
        self.assertEqual((old["collection"]["valid"], old["collection"]["state"], old["layer"]), (True, "stable", "mechanical_pass"))
        sd = self.base / "v2" / fx.S06_V2_ID
        produced = json.loads((sd / fx.S06_PRODUCER).read_text(encoding="utf-8"))
        self.assertEqual((produced["result"]["wflib"], produced["provenance"]["producer_run"]["rc"],
                          produced["provenance"]["pin"]["commit"]),
                         (str(Path(self.v2_src) / "wflib"), 0, "b523be470cd3252cd72210bf04cad8b76f62aa15"))
        doc = json.loads((sd / "FIXTURE.json").read_text(encoding="utf-8"))
        self.assertEqual((doc["tier"], doc["synthetic"], doc["not_production_evidence"], doc["authorization"],
                          doc["byte_reproducible"]), ("B", True, True, "none", False))

    def test_b1_is_real_6b21_output_and_current_consumer_says_unsupported(self):
        rec = self.b1["record"]
        self.assertEqual((sorted(rec["binding"]), "schema" in rec["verified"]["binding"], rec["verified"]["granted"]),
                         (["evidence_sha256", "goals_sha"], False, False))
        r = self.b1_read
        self.assertEqual((r["direct"]["valid"], r["direct"]["state"], r["direct"]["why"], r["direct"]["authorization"]),
                         (False, "unsupported", [UNSUPPORTED_WHY], "unavailable"))
        self.assertEqual((r["item"]["layer"], r["item"]["collection"]["state"], r["verified_count"], r["consumer_bytes_unchanged"]),
                         ("mechanical_pass", "unsupported", 0, True))
        self.assertEqual(fx.verify(self.base / "b1"), {})

    # ---- current consumer on the same bytes
    def test_current_consumer_reads_v2_as_changed_and_never_grants(self):
        r = self.v2_read
        self.assertEqual((r["record"]["verified_schema"], r["record"]["collection_state"], r["record"]["granted"]),
                         ("live-collection/2", "collected", False))
        self.assertEqual((r["direct"]["valid"], r["direct"]["state"], r["direct"]["why"], r["direct"]["authorization"]),
                         (False, "changed", [CHANGED_WHY], "unavailable"))
        self.assertEqual((r["item"]["layer"], r["item"]["collection"], r["verified_count"], r["consumer_bytes_unchanged"]),
                         ("mechanical_pass", r["direct"], 0, True))
        d = r["descriptor_delta"]   # structural: the current collector re-discovers a different window descriptor
        self.assertTrue(d.get("changed_keys") or d.get("keys_only_recorded") or d.get("keys_only_current"), d)
        self.assertEqual(fx.verify(self.base / "v2"), {})

    def test_same_project_recollection_is_stable_but_synthetic_and_tampering_is_refused(self):
        root = self.base / "v2-recollect"
        built = fx.build_live_v2(root, legacy_tool_root=self.v2_src)
        first = fx._s06_consume(root, fx.S06_V2_ID, "read")
        self.assertEqual(first["direct"]["state"], "changed")
        r = fx._s06_consume(root, fx.S06_V2_ID, "recollect")
        self.assertEqual(r["process"]["cwd"], first["process"]["cwd"])   # same fixture, same context
        self.assertEqual((r["record"]["verified_schema"], r["record"]["collection_state"], r["record"]["granted"]),
                         ("live-collection/3", "collected", False))
        self.assertEqual((r["direct"]["valid"], r["direct"]["state"], r["direct"]["authorization"], r["direct"]["granted"]),
                         (True, "stable", "unavailable", False))
        self.assertEqual((r["item"]["layer"], r["item"]["collection"]["valid"], r["verified_count"]), ("mechanical_pass", True, 0))
        e = r["evaluation"]
        self.assertEqual((e["passed_strict"], e["history"]["complete"], e["history"]["synthetic"],
                          e["production_trust"]["granted"], e["production_trust"]["why"], e["authorization"]),
                         (True, True, True, False, ["production_trust_transport_synthetic"], "unavailable"))
        project = self.project(built)
        state = project / "workflow" / "goals_state.json"
        raw, mode = state.read_bytes(), state.stat().st_mode & 0o7777
        records = json.loads(raw)
        gid = built["context"]["goal_id"]
        evidence = project / records[gid]["verified"]["binding"]["evidence"]
        ev_raw = evidence.read_bytes()
        seen = {}
        try:   # (1) live evidence bytes
            evidence.write_bytes(ev_raw + b"\n")
            seen["evidence_bytes"] = fx._s06_consume(root, fx.S06_V2_ID, "read", require_verify=False)["direct"]
        finally:
            evidence.write_bytes(ev_raw)
        for name, mutate in (("granted_self_report", lambda d: d[gid]["verified"].__setitem__("granted", True)),
                             ("content_with_self_consistent_hash", None)):
            doc = json.loads(raw)
            if mutate:
                mutate(doc)
            else:   # (3) rewrite the evidence content and recompute the record's own evidence hashes
                forged = ev_raw.replace(b'"passed_strict": true', b'"passed_strict": false') + b"\n"
                evidence.write_bytes(forged)
                h = hashlib.sha256(forged).hexdigest()
                doc[gid]["verified"]["binding"]["evidence_sha256"] = h
                doc[gid]["verified"]["granted"] = True
            try:
                state.write_text(json.dumps(doc), encoding="utf-8")
                os.chmod(str(state), mode)
                seen[name] = fx._s06_consume(root, fx.S06_V2_ID, "read", require_verify=False)["direct"]
            finally:
                state.write_bytes(raw)
                os.chmod(str(state), mode)
                evidence.write_bytes(ev_raw)
        for name, direct in seen.items():
            self.assertEqual((direct["valid"], direct.get("granted", False), direct["authorization"]),
                             (False, False, "unavailable"), (name, direct))
            self.assertIn(direct["state"], ("corrupt", "changed"), (name, direct))
        restored = fx._s06_consume(root, fx.S06_V2_ID, "read", require_verify=False)   # fresh process: no cache
        self.assertEqual((restored["direct"]["valid"], restored["direct"]["state"], restored["verified_count"]),
                         (True, "stable", 0))
        self.assertTrue(fx.verify(root))   # recollection rewrote the stored result: the /2 manifest no longer matches

    def test_v2_tampering_is_caught_by_verify_and_by_the_consumer_without_verify(self):
        root, sd = self.base / "v2", self.base / "v2" / fx.S06_V2_ID
        project = self.project(self.v2)
        state = project / "workflow" / "goals_state.json"
        raw, mode = state.read_bytes(), state.stat().st_mode & 0o7777
        gid = self.v2["context"]["goal_id"]
        evidence = project / json.loads(raw)[gid]["verified"]["binding"]["evidence"]
        ev_raw = evidence.read_bytes()
        rel = lambda p: str(p.relative_to(sd))
        try:
            evidence.write_bytes(ev_raw + b"\n")
            self.assertIn(f"files:{rel(evidence)}", fx.verify(root)[fx.S06_V2_ID])
            with self.assertRaises(fx.FixtureRefused):
                fx._s06_consume(root, fx.S06_V2_ID, "read")
            direct = fx._s06_consume(root, fx.S06_V2_ID, "read", require_verify=False)
            self.assertEqual((direct["direct"]["valid"], direct["direct"]["authorization"], direct["verified_count"]),
                             (False, "unavailable", 0))
        finally:
            evidence.write_bytes(ev_raw)
        doc = json.loads(raw)
        doc[gid]["verified"]["granted"] = True
        try:
            state.write_text(json.dumps(doc), encoding="utf-8")
            os.chmod(str(state), mode)
            self.assertIn(f"files:{rel(state)}", fx.verify(root)[fx.S06_V2_ID])
            direct = fx._s06_consume(root, fx.S06_V2_ID, "read", require_verify=False)["direct"]
            self.assertEqual((direct["valid"], direct["state"], direct["authorization"]), (False, "corrupt", "unavailable"))
        finally:
            state.write_bytes(raw)
            os.chmod(str(state), mode)
        self.assertEqual(fx.verify(root), {})
        again = fx._s06_consume(root, fx.S06_V2_ID, "read")
        self.assertEqual((again["direct"]["state"], again["verified_count"]), ("changed", 0))

    # ---- inputs refused before anything is built or spawned
    def test_bad_inputs_are_refused_before_any_build(self):
        work = Path(tempfile.mkdtemp(prefix="b3-s06-bad-")).resolve()
        try:
            shallow, dirty = work / "shallow", work / "dirty"
            for d in (shallow, dirty):
                shutil.copytree(self.v2_src, str(d), symlinks=True)
            (shallow / ".git" / "shallow").write_text("b523be470cd3252cd72210bf04cad8b76f62aa15\n")
            (dirty / "wflib" / "goals.py").write_bytes((dirty / "wflib" / "goals.py").read_bytes() + b"\n# edit\n")
            untracked = work / "untracked"
            shutil.copytree(self.v2_src, str(untracked), symlinks=True)
            (untracked / "stray.txt").write_text("x\n")
            cases = {"wrong commit": self.b1_src, "relative": "legacy-v2", "missing": str(work / "absent"),
                     "shallow": str(shallow), "dirty": str(dirty), "untracked": str(untracked)}
            with mock.patch.object(fx, "_run_child", side_effect=AssertionError("spawned")):
                for name, src in cases.items():
                    root = work / f"root-{name.replace(' ', '-')}"
                    with self.assertRaises(fx.FixtureRefused, msg=name):
                        fx.build_live_v2(root, legacy_tool_root=src)
                    self.assertFalse(root.exists(), name)
                goals_src = (TOOL / "wflib" / "goals.py").read_text(encoding="utf-8")
                drifted = goals_src.replace('"""One strict consumer for collection/display/authorization; no legacy grant."""',
                                            '"""One strict consumer (edited)."""')
                self.assertNotEqual(drifted, goals_src)
                self.assertEqual(fx._s06_interface_problems(sources={"wflib/goals.py": drifted}),
                                 ["wflib/goals.py::_live_binding_valid: definition changed"])
                key = "wflib/live.py::_capture_inputs"
                with mock.patch.dict(fx.S06_INTERFACES, {key: ("0" * 64, fx.S06_INTERFACES[key][1])}):
                    with self.assertRaises(fx.FixtureRefused):
                        fx.build_live_v2(work / "root-drift", legacy_tool_root=self.v2_src)
                self.assertFalse((work / "root-drift").exists())
            self.assertEqual(fx._s06_interface_problems(), [])
        finally:
            shutil.rmtree(str(work), ignore_errors=True)

    def test_default_s_tier_s06_remains_layout_and_imports_nothing_else(self):
        sids = [s["id"] for s in fx.SCENARIOS]
        self.assertIn("S06-legacy-live-collection-v2", sids)
        self.assertFalse({fx.S06_V2_ID, fx.S06_B1_ID} & set(sids))
        r = subprocess.run([sys.executable, "-c", "import sys, wflib.b3_fixtures; "
                            "print(sorted(m for m in sys.modules if m.startswith('wflib')))"],
                           cwd=str(TOOL), capture_output=True, text=True, timeout=60,
                           env={k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)})
        self.assertEqual(r.stdout.strip(), "['wflib', 'wflib.b3_fixtures']", r.stderr)

    def test_z_sources_locals_and_environment_unchanged(self):
        after = {"environ": dict(os.environ), "tool": _digest(TOOL), "tool_local": _digest(TOOL / "local", skip=()),
                 "configured_local": _digest(Path(os.environ["WF_LOCAL_DIR"]), skip=()) if os.environ.get("WF_LOCAL_DIR") else None,
                 "legacy_v2": _digest(Path(self.v2_src), skip=()), "legacy_b1": _digest(Path(self.b1_src), skip=()),
                 "temp": _temp_entries()}
        for key in ("environ", "tool", "tool_local", "configured_local", "legacy_v2", "legacy_b1"):
            self.assertEqual(after[key], self.before[key], key)
        self.assertEqual(after["temp"] - self.before["temp"], set())   # scaffold temp dirs live inside fixture roots
        for built, pin in ((self.v2, "live-collection/2"), (self.b1, "B1")):
            self.assertEqual(fx._legacy_tree_problems(Path(built["provenance"]["pin"] and
                             (self.v2_src if pin != "B1" else self.b1_src)), fx.LEGACY_PRODUCERS[pin]), [])
            base = Path(built["context"]["base"])
            self.assertTrue(Path(built["root"]) in base.parents, base)


if __name__ == "__main__":
    unittest.main()
