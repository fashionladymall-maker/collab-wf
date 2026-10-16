"""Focused tests of the lane C C1 provider adapter (synthetic groups only).

These tests exercise the recording/replay and fail-closed mechanics with small
synthetic groups; they call no product API. Real category evidence comes from
the recorded lane C assertions themselves.
"""
from __future__ import annotations

import itertools
import json
import os
import signal
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from . import selftest_lane_c as lc

C_CATEGORIES = {"1-empty-guard", "3-layers", "4-na", "5-binding-impact", "5-window-inputs", "6a-live-timing"}


class _Isolated(unittest.TestCase):
    def setUp(self):
        records = patch.object(lc, "_GROUP_RECORDS", {})
        records.start()
        self.addCleanup(records.stop)


class ConjTests(_Isolated):
    def test_conj_equals_and_chain_and_records_parts(self):
        for values in itertools.product((True, False), repeat=3):
            sink = {}
            with patch.object(lc, "_SUB_SINK", sink):
                got = lc._conj(*((f"p{i}", (lambda v=v: v)) for i, v in enumerate(values)))
            self.assertIs(got, all(values))
            self.assertEqual([sink[f"p{i}"]["ok"] for i in range(3)], list(values))
            self.assertTrue(all(sink[f"p{i}"]["ran"] for i in range(3)))

    def test_conj_raises_only_where_the_chain_would(self):
        def boom():
            raise KeyError("missing")
        sink = {}
        with patch.object(lc, "_SUB_SINK", sink):
            self.assertFalse(lc._conj(("a", lambda: False), ("b", boom)))   # never reached by `and`
        self.assertEqual((sink["b"]["ran"], sink["b"]["ok"]), (False, False))
        sink = {}
        with patch.object(lc, "_SUB_SINK", sink), self.assertRaises(KeyError):
            lc._conj(("a", lambda: True), ("b", boom))   # reached by `and`: same exception
        self.assertEqual((sink["a"]["ok"], sink["b"]["ran"]), (True, False))

    def test_duplicate_sub_id_is_rejected(self):
        with patch.object(lc, "_SUB_SINK", {}), self.assertRaises(RuntimeError):
            lc._conj(("a", lambda: True), ("a", lambda: True))


def _synthetic_group(calls, *, raise_after=None, sleep=0.0):
    def group(_ok):
        calls.append(1)
        _ok("syn assertion one", True, "d1")
        _ok("syn assertion two", lc._conj(("syn.sub-pos", lambda: True), ("syn.sub-neg", lambda: True)), {"k": 2})
        if sleep:
            time.sleep(sleep)
        if raise_after:
            raise ValueError(raise_after)
        _ok("syn assertion three", False, "d3")
    group.__name__ = "_t_syn_" + str(id(calls))
    return group


class RunOnceTests(_Isolated):
    def test_group_runs_once_and_replay_is_identical(self):
        calls, live_calls = [], []
        group = _synthetic_group(calls)
        group(lambda n, c, d="": live_calls.append((n, bool(c), d)) or c)
        calls.clear()
        with patch.object(lc, "TESTS", [("syn", group)]):
            replayed = []
            lc.run_lane_c(lambda n, c, d="": replayed.append((n, bool(c), d)) or c)
            again = []
            lc.run_lane_c(lambda n, c, d="": again.append((n, bool(c), d)) or c)
            record = lc._run_group("syn", group)
        self.assertEqual(calls, [1])   # one business execution for lane replay x2 and provider reads
        self.assertEqual(replayed, live_calls)
        self.assertEqual(again, live_calls)
        self.assertEqual(sorted(record["subs"]), ["syn.sub-neg", "syn.sub-pos"])

    def test_group_error_is_replayed_with_original_name(self):
        group = _synthetic_group([], raise_after="boom")
        with patch.object(lc, "TESTS", [("syn", group)]):
            seen = []
            lc.run_lane_c(lambda n, c, d="": seen.append((n, bool(c), d)) or c)
        self.assertEqual(seen[-1], ("B1-C 检查组「syn」运行出错", False, "ValueError: boom"))


class ProviderTests(_Isolated):
    def provider(self, group, sources):
        mapping = {"syn-cat": [(f"case{i}", pol, src) for i, (pol, src) in enumerate(sources)]}
        tests = [("syn", group)]
        with patch.object(lc, "TESTS", tests), patch.object(lc, "C1_MAP", mapping), \
                patch.dict(os.environ, {"WF_LOCAL_DIR": "/tmp/isolated-c1-test"}), \
                patch.object(lc, "_c1_guard", lambda: ""):
            return lc._c1_provider("syn-cat")()

    def test_all_recorded_cases_pass(self):
        group = _synthetic_group([])
        r = self.provider(group, [("pos", ("sub", group.__name__, "syn.sub-pos")),
                                  ("neg", ("ok=", group.__name__, "syn assertion one"))])
        self.assertEqual((r["schema"], r["provider"], r["error"]), ("c1-provider/1", "C", None))
        self.assertTrue(all(c["ran"] is True and c["ok"] is True for c in r["cases"]))

    def test_failed_missing_ambiguous_and_error_cases_fail_closed(self):
        group = _synthetic_group([], raise_after="boom")
        r = self.provider(group, [("pos", ("sub", group.__name__, "syn.sub-pos")),
                                  ("neg", ("ok=", group.__name__, "syn assertion three")),   # never reached
                                  ("neg", ("ok", group.__name__, "syn assertion")),          # ambiguous prefix
                                  ("neg", ("sub", group.__name__, "syn.absent"))])
        by = {c["name"]: c for c in r["cases"]}
        # FIX2: the group raised, so even case0 (recorded ok before the error) is not evidence.
        for name in ("syn-cat/case0", "syn-cat/case1", "syn-cat/case2", "syn-cat/case3"):
            self.assertEqual((by[name]["ran"], by[name]["ok"]), (False, False), by[name])
            self.assertIn("ValueError: boom", by[name]["detail"])
        self.assertIn("4 case(s) not ran+ok", r["error"])
        self.assertIn("did not complete cleanly: ValueError: boom", r["error"])

    def test_failed_assertion_is_ran_but_not_ok(self):
        group = _synthetic_group([])
        r = self.provider(group, [("pos", ("sub", group.__name__, "syn.sub-pos")),
                                  ("neg", ("ok=", group.__name__, "syn assertion three"))])
        case = r["cases"][1]
        self.assertEqual((case["ran"], case["ok"]), (True, False))
        self.assertIsNotNone(r["error"])

    def test_timeout_fails_closed(self):
        group = _synthetic_group([], sleep=3)
        with patch.object(lc, "C1_GROUP_TIMEOUT_S", 0.3):
            started = time.monotonic()
            r = self.provider(group, [("pos", ("sub", group.__name__, "syn.sub-pos")),
                                      ("neg", ("ok=", group.__name__, "syn assertion one"))])
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertTrue(all(c["ran"] is False and c["ok"] is False for c in r["cases"]), r)
        self.assertIn("timed out", r["cases"][0]["detail"])

    def test_non_bool_and_missing_polarity_fail_closed(self):
        group = _synthetic_group([])
        with patch.object(lc, "_c1_case", lambda source: (1, "yes", "x")):
            r = self.provider(group, [("pos", ("sub", group.__name__, "syn.sub-pos"))])
        self.assertEqual((r["cases"][0]["ran"], r["cases"][0]["ok"]), (False, False))
        self.assertIn("no neg case", r["error"])

    def test_guard_refuses_real_local(self):
        group = _synthetic_group([])
        mapping = {"syn-cat": [("case0", "pos", ("sub", group.__name__, "syn.sub-pos"))]}
        env = {k: v for k, v in os.environ.items() if k != "WF_LOCAL_DIR"}
        with patch.object(lc, "TESTS", [("syn", group)]), patch.object(lc, "C1_MAP", mapping), \
                patch.dict(os.environ, env, clear=True):
            r = lc._c1_provider("syn-cat")()
        self.assertEqual((r["cases"][0]["ran"], r["cases"][0]["ok"]), (False, False))
        self.assertIn("WF_LOCAL_DIR", r["error"])
        self.assertEqual(lc._GROUP_RECORDS, {})   # nothing ran


# ---- C-C1-SIX-FIX2 fail-closed counter-examples (adapter contract fault injection, synthetic only) ----
NON_BOOL = ("truthy", 1, None)


def _probe_group(calls, *, cond=True, raises=False, sleep=0.0, conj_value=True):
    """Shape of the independent review's fail_closed_probe.py group, with a call counter."""
    def group(ok):
        calls.append(1)
        ok("pos", cond)
        ok("neg", True)
        ok("agg", lc._conj(("probe.sub", lambda: conj_value)))
        if raises:
            raise RuntimeError("after-selected-success")
        if sleep:
            time.sleep(sleep)
    group.__name__ = "_probe_group_" + str(id(calls))
    return group


def _probe_mapping(group, *, with_sub=False):
    cases = [("pos", "pos", ("ok=", group.__name__, "pos")), ("neg", "neg", ("ok=", group.__name__, "neg"))]
    if with_sub:
        cases.append(("sub", "pos", ("sub", group.__name__, "probe.sub")))
    return {"probe": cases}


class FailClosedProbeTests(_Isolated):
    """The three review probes now fail closed; strict-bool, error-free, main-thread groups keep their result."""

    def run_probe(self, group, *, with_sub=False, budget=0.01, threaded=False):
        result, timing = {}, {}
        with patch.object(lc, "TESTS", [("probe", group)]), patch.object(lc, "C1_MAP", _probe_mapping(group, with_sub=with_sub)), \
                patch.object(lc, "C1_GROUP_TIMEOUT_S", budget), patch.object(lc, "_c1_guard", lambda: ""):
            def run():
                started = time.monotonic()
                result.update(lc._c1_provider("probe")())
                timing["inner"] = time.monotonic() - started
            started = time.monotonic()
            if threaded:
                before = threading.active_count()
                worker = threading.Thread(target=run, name="c1-probe-worker")
                worker.start()
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(threading.active_count(), before)   # no worker left behind
            else:
                run()
            timing["outer"] = time.monotonic() - started
            record = dict(lc._GROUP_RECORDS[group.__name__])
        return result, timing, record

    def assert_all_closed(self, result, text):
        self.assertTrue(result["cases"])
        for case in result["cases"]:
            self.assertIs(case["ran"], False, case)
            self.assertIs(case["ok"], False, case)
        self.assertIsNotNone(result["error"])
        self.assertIn(text, result["error"])

    def test_control_strict_bool_group_still_succeeds(self):
        calls = []
        result, _, record = self.run_probe(_probe_group(calls), with_sub=True, budget=5)
        self.assertEqual(calls, [1])
        self.assertIsNone(result["error"], result)
        self.assertTrue(all(c["ran"] is True and c["ok"] is True for c in result["cases"]))
        self.assertEqual([a["cond_type"] for a in record["asserts"]], ["bool", "bool", "bool"])
        self.assertEqual((record["deadline_enforced"], record["executed"], record["error"]), (True, True, None))

    def test_post_success_exception_fails_closed(self):
        calls = []
        result, _, record = self.run_probe(_probe_group(calls, raises=True), with_sub=True, budget=5)
        self.assertEqual(calls, [1])   # ran once; every selected assertion was ok before the raise
        self.assertEqual([a["ok"] for a in record["asserts"]], [True, True, True])
        self.assertEqual(record["subs"]["probe.sub"]["ok"], True)
        self.assertEqual(record["error"], "RuntimeError: after-selected-success")
        self.assert_all_closed(result, "RuntimeError: after-selected-success")
        self.assertTrue(all("group error" in c["detail"] for c in result["cases"]), result)

    def test_non_main_thread_is_refused_promptly_without_running(self):
        calls = []
        result, timing, record = self.run_probe(_probe_group(calls, sleep=0.08), budget=0.01, threaded=True)
        self.assertEqual(calls, [])   # refused before any business operation
        self.assertLess(timing["inner"], 0.05, timing)   # does not wait for the 0.08 s group
        self.assertEqual((record["executed"], record["deadline_enforced"], record["timed_out"]), (False, False, False))
        self.assertIn("main thread", record["error"])
        self.assert_all_closed(result, "_C1DeadlineUnavailable")
        self.assertFalse(lc._DEADLINE_ACTIVE)

    def test_main_thread_short_deadline_interrupts_the_group(self):
        calls = []
        handler = signal.getsignal(signal.SIGALRM)
        result, timing, record = self.run_probe(_probe_group(calls, sleep=0.08), budget=0.01)
        self.assertEqual(calls, [1])
        self.assertLess(timing["outer"], 0.08, timing)   # interrupted, not waited out
        self.assertEqual((record["timed_out"], record["deadline_enforced"]), (True, True))
        self.assert_all_closed(result, "_C1Timeout: lane C group exceeded 0.01s")
        self.assertTrue(all("group timed out" in c["detail"] for c in result["cases"]), result)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        self.assertIs(signal.getsignal(signal.SIGALRM), handler)
        self.assertFalse(lc._DEADLINE_ACTIVE)

    def test_non_bool_assertion_conditions_fail_closed(self):
        for value in NON_BOOL:
            with self.subTest(value=value):
                lc._GROUP_RECORDS.clear()
                calls = []
                result, _, record = self.run_probe(_probe_group(calls, cond=value), budget=5)
                pos = record["asserts"][0]
                self.assertEqual((pos["ok"], pos["cond_type"]), (False, type(value).__name__))
                self.assertIn("non-bool assertion condition", pos["detail"])
                case = result["cases"][0]
                self.assertEqual((case["ran"], case["ok"]), (True, False), case)
                self.assertIn("non-bool", case["detail"])
                self.assertEqual((result["cases"][1]["ran"], result["cases"][1]["ok"]), (True, True))
                self.assertIn("1 case(s) not ran+ok: probe/pos", result["error"])

    def test_non_bool_sub_assertion_results_fail_closed(self):
        for value in NON_BOOL:
            with self.subTest(value=value):
                lc._GROUP_RECORDS.clear()
                result, _, record = self.run_probe(_probe_group([], conj_value=value), with_sub=True, budget=5)
                sub = record["subs"]["probe.sub"]
                self.assertEqual((sub["ran"], sub["ok"]), (True, False))
                self.assertIn(f"non-bool sub-assertion result ({type(value).__name__}", sub["detail"])
                self.assertEqual(record["asserts"][2]["ok"], False)   # the chain itself fails closed too
                case = next(c for c in result["cases"] if c["name"] == "probe/sub")
                self.assertEqual((case["ran"], case["ok"]), (True, False))
                self.assertIsNotNone(result["error"])

    def test_lane_replay_uses_the_same_strict_value(self):
        group = _probe_group([], cond="truthy")
        seen = []
        with patch.object(lc, "TESTS", [("probe", group)]):
            lc.run_lane_c(lambda n, c, d="": seen.append((n, c, d)) or c)
        self.assertEqual([(n, c) for n, c, _ in seen], [("pos", False), ("neg", True), ("agg", True)])
        self.assertIn("non-bool assertion condition (str", seen[0][2])


class StrictConjTests(unittest.TestCase):
    def test_non_bool_part_fails_the_chain_in_any_position(self):
        for value in NON_BOOL + (0, "", [1]):
            for position in range(3):
                with self.subTest(value=value, position=position):
                    parts = [(f"p{i}", (lambda: True)) for i in range(3)]
                    parts[position] = (f"p{position}", lambda v=value: v)
                    sink = {}
                    with patch.object(lc, "_SUB_SINK", sink):
                        got = lc._conj(*parts)
                    self.assertIs(got, False)
                    self.assertEqual((sink[f"p{position}"]["ran"], sink[f"p{position}"]["ok"]), (True, False))
                    self.assertIn("non-bool sub-assertion result", sink[f"p{position}"]["detail"])
                    for i in range(3):
                        self.assertIs(type(sink[f"p{i}"]["ok"]), bool)

    def test_after_false_non_bool_part_is_recorded_not_ok(self):
        sink = {}
        with patch.object(lc, "_SUB_SINK", sink):
            self.assertIs(lc._conj(("a", lambda: False), ("b", lambda: 1)), False)
        self.assertEqual((sink["b"]["ran"], sink["b"]["ok"]), (True, False))
        self.assertIn("non-bool", sink["b"]["detail"])


class DeadlineRefusalTests(_Isolated):
    def refused(self, calls):
        group = _probe_group(calls)
        record = lc._run_group("probe", group)
        self.assertEqual(calls, [])
        self.assertEqual((record["executed"], record["deadline_enforced"], record["asserts"]), (False, False, []))
        self.assertIn("refused before running", record["error"])
        return record

    def test_nested_deadline_is_refused(self):
        inner_calls, seen = [], {}

        def outer(_ok):
            seen["inner"] = lc._run_group("inner", _probe_group(inner_calls))
            _ok("outer ran", True)
        outer.__name__ = "_t_outer_nested_probe"
        record = lc._run_group("outer", outer)
        self.assertEqual((record["error"], record["deadline_enforced"]), (None, True))
        self.assertEqual(inner_calls, [])
        self.assertIn("nested inside another lane C group deadline", seen["inner"]["error"])
        self.assertFalse(lc._DEADLINE_ACTIVE)

    def test_missing_setitimer_is_refused(self):
        with patch.object(signal, "setitimer", None):
            record = self.refused([])
        self.assertIn("unavailable", record["error"])

    def test_foreign_itimer_is_refused_and_left_armed(self):
        previous = signal.signal(signal.SIGALRM, lambda *_: None)
        try:
            signal.setitimer(signal.ITIMER_REAL, 30)
            record = self.refused([])
            self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 0)   # not cancelled by lane C
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        self.assertIn("foreign ITIMER_REAL", record["error"])

    def test_refused_group_is_replayed_as_a_group_error(self):
        group = _probe_group([])
        seen = []

        def lane():
            with patch.object(lc, "TESTS", [("probe", group)]):
                lc.run_lane_c(lambda n, c, d="": seen.append((n, c, d)) or c)
        worker = threading.Thread(target=lane)
        worker.start()
        worker.join(2)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][:2], ("B1-C 检查组「probe」运行出错", False))
        self.assertIn("main thread", seen[0][2])


class MappingTests(unittest.TestCase):
    def test_each_source_consumed_once_with_one_polarity(self):
        groups = {fn.__name__ for _, fn in lc.TESTS}
        seen = {}
        for category, cases in lc.C1_MAP.items():
            self.assertTrue(any(p == "pos" for _, p, _ in cases) and any(p == "neg" for _, p, _ in cases), category)
            for name, polarity, source in cases:
                self.assertIn(polarity, ("pos", "neg"))
                self.assertIn(source[0], ("sub", "ok", "ok="))
                self.assertIn(source[1], groups, source)
                self.assertNotIn(source, seen, (category, name, seen.get(source)))
                seen[source] = (category, name)

    def test_categories_are_contract_c_categories(self):
        contract = json.loads((Path(lc.__file__).resolve().parent.parent / "docs" / "BATCH2_CONTRACT.json")
                              .read_text(encoding="utf-8"))
        required = {c for group in contract["required_categories"].values() for c in group}
        self.assertTrue(C_CATEGORIES <= required)
        self.assertTrue(set(lc.C1_MAP) <= C_CATEGORIES)
        self.assertEqual(set(lc.C1_PROVIDERS), set(lc.C1_MAP))
        self.assertEqual(set(lc.C1_MAP), C_CATEGORIES)   # both groups present: exactly the six C categories


if __name__ == "__main__":
    unittest.main()
