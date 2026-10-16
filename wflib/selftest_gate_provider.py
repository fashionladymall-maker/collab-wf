"""Targeted tests for the real default-gate provider (C-B3-GATE-PROVIDER, candidate matrix B3-07).

The class runs the real provider once (fresh tool copies, real `wf self-check` and `wf self-check --probes`,
real driver._run_gates). No test method starts the complete `wf selftest`. Registered method by method in
wflib/selftest.py; not registered at WF_PROBE_DEPTH=1.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from . import selftest_v18 as v18
from .core import WF_HOME

EXPECT = {"G0-default-gates": ([0, 0], [True, True]), "G1-manifest-drift": ([1, 0], [False, True]),
          "G2-NO_REF-consumer": ([0, 1], [True, False])}


def _source_snapshot() -> dict:
    out = {}
    for p in sorted(WF_HOME.rglob("*")):
        rel = p.relative_to(WF_HOME)
        if rel.parts[0] in (".git", "local") or "__pycache__" in rel.parts or p.suffix == ".pyc":
            continue
        if p.is_file() and not p.is_symlink():
            out[str(rel)] = hashlib.sha256(p.read_bytes()).hexdigest() + oct(p.stat().st_mode & 0o777)
    return out


def _alive(pid: int) -> bool:
    """A killed but unreaped process (state Z, e.g. under a container init that does not reap) is not alive."""
    stat_path = Path(f"/proc/{pid}/stat")
    if Path("/proc/self/stat").exists():
        try:
            return stat_path.read_text().rsplit(")", 1)[1].split()[0] != "Z"
        except (FileNotFoundError, ProcessLookupError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    env.update(extra)
    return env


class GateProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # One real provider run for this class run; nothing is carried across runs, trees or cases.
        cls.env_before, cls.src_before = dict(os.environ), _source_snapshot()
        cls.tmp_before = set(Path(tempfile.gettempdir()).glob("wf-gate-provider-*"))
        cls.res = v18.C1_PROVIDERS[v18.GATE_CATEGORY]()
        cls.env_after, cls.src_after = dict(os.environ), _source_snapshot()
        cls.tmp_after = set(Path(tempfile.gettempdir()).glob("wf-gate-provider-*"))
        cls.details = {c["name"]: json.loads(c["detail"]) for c in cls.res.get("cases", [])
                       if str(c.get("detail", "")).startswith("{")}

    def test_real_g0_g1_g2_gates_and_driver_evidence(self):
        self.assertEqual(v18.provider_problems(self.res), [], json.dumps(self.res, ensure_ascii=False)[:3000])
        for name, (rcs, oks) in EXPECT.items():
            d = self.details[name]
            self.assertEqual((d["gate_rc"], d["driver_ok"], d["failed_checks"]), (rcs, oks, []), name)
            self.assertEqual(d["evidence"], {"turn_dir": "workflow/evidence/turn-000", "gate_events": 2,
                                             "files": [f"gate-{name}-1.txt", f"gate-{name}-2.txt"]}, name)
            argv = {s["tag"]: s["argv"] for s in d["steps"]}
            self.assertEqual((argv["self-manifest"][0], argv["self-manifest"][2:], argv["gate1"], argv["gate2"],
                              argv["init"][2:4], argv["init"][5]),
                             (sys.executable, ["self-manifest"], ["bash", "-c", "wf self-check"],
                              ["bash", "-c", "wf self-check --probes"], ["init", "--name"], "--root"), name)
            self.assertTrue(all(d["checks"][k] for k in ("policy_gates_are_init_gates", "policy_allow_empty_auto_off",
                                                         "no_approvals", "init_made_no_gate_event_or_log",
                                                         "no_goal_state_written")), name)
        g0, g1, g2 = (self.details[n] for n in EXPECT)
        self.assertEqual((g0["gate1_manifest_sections"], g1["gate1_manifest_sections"], g2["gate1_manifest_sections"]),
                         ({}, {"changed": ["README.md"]}, {}))
        self.assertEqual((g1["mutation"]["file"], g1["mutation"]["appended_after_manifest"]), ("README.md", True))
        self.assertEqual({k: g2["mutation"][k] for k in ("file", "rule", "field", "old", "new")},
                         {"file": "anomalies.json", "rule": "NO_REF", "field": "action", "old": "await_user", "new": "block"})
        self.assertEqual((g0["no_ref_consumer"]["state"], g2["no_ref_consumer"]["state"],
                          g2["no_ref_consumer"]["rule_action"]), ("AWAITING_USER", "BLOCKED", "block"))
        self.assertEqual((len(g2["gate2_probe"]["failed"]), g2["gate2_probe"]["failed"][0].split("：")[0]),
                         (1, "探针 NO_REF"))

    def test_active_groups_and_denominators_by_depth(self):
        names0 = [n for n, _ in v18.active_groups(0)]
        names1 = [n for n, _ in v18.active_groups(1)]
        old = [n for n, _ in v18.TESTS]
        # Depth 0 = the old probes + 真实默认门禁 + 门禁基线 (strict 21-category aggregate, C-C1-STRICT21);
        # depth 1 = the old probes only. Counts come from the one selector, never from a constant.
        self.assertEqual((len(names0), len(names1), names1, names0[:len(old)], names0[-2:]),
                         (len(old) + 2, len(old), old, old, [v18.GATE_GROUP, v18.BASELINE_GROUP]))
        self.assertEqual(set(names0) - set(names1), {v18.GATE_GROUP, v18.BASELINE_GROUP})
        self.assertIn(v18.BASELINE_GROUP, names0)      # registered at depth 0 ...
        self.assertNotIn(v18.BASELINE_GROUP, names1)   # ... and never nested
        self.assertEqual(len(set(names0)), len(names0))
        probe = self.details["G0-default-gates"]["gate2_probe"]   # a real depth-1 `wf self-check --probes`
        self.assertEqual((probe["groups"], probe["gate_group_present"], probe["total"]),
                         (len(old), False, f"探针结果：{len(old)} / {len(old)} 通过"))
        self.assertIs(probe["baseline_group_present"], False)   # the real nested run excludes 门禁基线 too

    def test_bad_depth_is_refused_before_any_spawn(self):
        bad = ["2", "3", "-1", "", "x", "01", " 1", "1 ", "+1"]
        spies = [mock.patch.object(v18.subprocess, "run", side_effect=AssertionError("spawn")),
                 mock.patch.object(v18.subprocess, "Popen", side_effect=AssertionError("spawn")),
                 mock.patch.object(v18.tempfile, "mkdtemp", side_effect=AssertionError("copy dir")),
                 mock.patch.object(v18.tempfile, "TemporaryDirectory", side_effect=AssertionError("copy dir")),
                 mock.patch.object(v18, "_copy_tool", side_effect=AssertionError("copy"))]
        from . import selftest as st
        for value in bad:
            env = {v18.PROBE_DEPTH_ENV: value, "WF_PROBE_CHILD": "1", "WF_SELFTEST_CHILD": "1"}
            with mock.patch.dict(os.environ, env), spies[0], spies[1], spies[2], spies[3], spies[4], \
                    mock.patch.object(st, "_run_checks", side_effect=AssertionError("selftest ran")):
                got, rec = [], []
                v18.run_v18(lambda n, c, d="": rec.append(bool(c)))
                res = v18.real_default_gates_provider()
                got = (v18.probes_main(), st.run(), rec, res["cases"], res["error"] is not None)
            self.assertEqual(got, (3, 3, [False], [], True), repr(value))
        with mock.patch.dict(os.environ, {v18.PROBE_DEPTH_ENV: "1"}), spies[1], spies[2], spies[4]:
            res = v18.real_default_gates_provider()   # nested direct call: refused before copy/spawn, not "passed"
        self.assertEqual((res["cases"], "depth" in res["error"], v18.provider_problems(res) != []), ([], True, True))
        with tempfile.TemporaryDirectory() as td:   # real CLI: rc 3, also with the child markers set
            for argv in ([sys.executable, str(WF_HOME / "wf.py"), "self-check", "--probes"],
                         [sys.executable, str(WF_HOME / "wf.py"), "selftest"]):
                r = subprocess.run(argv, cwd=td, capture_output=True, text=True, timeout=60,
                                   env=_clean_env(WF_PROBE_DEPTH="2", WF_PROBE_CHILD="1", WF_SELFTEST_CHILD="1",
                                                  WF_LOCAL_DIR=str(Path(td) / "local"), WF_NOTIFY="0"))
                self.assertEqual((r.returncode, v18.PROBE_DEPTH_ENV in r.stdout, os.listdir(td)), (3, True, []), argv)

    def test_provider_verdict_fails_closed(self):
        good = copy.deepcopy(self.res)
        self.assertEqual(v18.provider_problems(good), [])

        def bad(mutate):
            r = copy.deepcopy(good)
            mutate(r)
            return r
        variants = {
            "no pos": bad(lambda r: r.__setitem__("cases", r["cases"][1:])),
            "no neg": bad(lambda r: r.__setitem__("cases", r["cases"][:1])),
            "ran str": bad(lambda r: r["cases"][0].__setitem__("ran", "true")),
            "ok int": bad(lambda r: r["cases"][1].__setitem__("ok", 1)),
            "ran false": bad(lambda r: r["cases"][2].__setitem__("ran", False)),
            "ok false": bad(lambda r: r["cases"][0].__setitem__("ok", False)),
            "error": bad(lambda r: r.__setitem__("error", "boom")),
            "schema": bad(lambda r: r.__setitem__("schema", "c1-provider/0")),
            "category": bad(lambda r: r.__setitem__("category", "1-init-files")),
            "provider": bad(lambda r: r.__setitem__("provider", "A")),
            "empty": bad(lambda r: r.__setitem__("cases", [])),
            "unknown case": bad(lambda r: r["cases"].append({"name": "G9", "polarity": "neg", "ran": True,
                                                              "ok": True, "detail": ""})),
            "not object": [good],
        }
        self.assertEqual({k for k, v in variants.items() if not v18.provider_problems(v)}, set())
        for value in (variants["ok false"], variants["error"]):   # the group reports failure, never 通过
            rec = []
            with mock.patch.dict(v18.C1_PROVIDERS, {v18.GATE_CATEGORY: lambda value=value: value}):
                v18._t_real_default_gates(lambda n, c, d="": rec.append(bool(c)))
            self.assertEqual(rec, [False])

    def test_isolation_parent_env_source_and_temp_unchanged(self):
        self.assertEqual(self.env_after, self.env_before)
        self.assertEqual(self.src_after, self.src_before)   # the source MANIFEST.json included
        self.assertEqual(self.tmp_after - self.tmp_before, set())
        for name, d in self.details.items():
            env = d["env"]
            self.assertTrue(env["PATH"].split(os.pathsep)[0].endswith(f"/{name}/build/bin"), name)
            self.assertEqual({k for k in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "WF_PROBE_CHILD",
                                          "WF_SELFTEST_CHILD") if k in env}, set(), name)
            self.assertEqual((env[v18.PROBE_DEPTH_ENV], env["WF_NOTIFY"], Path(env["WF_LOCAL_DIR"]).name), ("0", "0", "local"))
            for s in d["steps"]:
                self.assertFalse(Path(s["cwd"]) == WF_HOME or WF_HOME in Path(s["cwd"]).parents, s)

    def test_timeouts_and_errors_are_not_counted_as_pass(self):
        with tempfile.TemporaryDirectory() as td:
            pidfile = Path(td) / "pid"
            r = v18._bounded(["bash", "-c", f"sleep 30 & echo $! > {pidfile}; sleep 30"], cwd=td,
                             env=_clean_env(), timeout=1)
            time.sleep(0.2)
            alive = _alive(int(pidfile.read_text()))
            self.assertEqual((r["rc"], r["timed_out"], alive), (None, True, False))   # whole group stopped
            with self.assertRaises(TimeoutError), mock.patch.object(v18.subprocess, "Popen",
                                                                    side_effect=AssertionError("spawn")):
                v18._bounded(["true"], cwd=td, env=_clean_env(), timeout=0)
        with mock.patch.object(v18, "_gate_case", side_effect=RuntimeError("synthetic case failure")):
            res = v18.real_default_gates_provider()
        self.assertEqual([(c["ran"], c["ok"]) for c in res["cases"]], [(False, False)] * 3)
        self.assertTrue(v18.provider_problems(res))
        with mock.patch.object(v18, "PROVIDER_BUDGET_S", 0):
            res = v18.real_default_gates_provider()
        self.assertEqual([(c["ran"], c["ok"], "TimeoutError" in c["detail"]) for c in res["cases"]],
                         [(False, False, True)] * 3)
        self.assertTrue(v18.provider_problems(res))


if __name__ == "__main__":
    unittest.main()
