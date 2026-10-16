"""C1 strict 21-category aggregate and suite registration helpers (C-C1-STRICT21-IMPLEMENT-20261016-1).

Implementation first, focused tests at the end of this module (``python -m unittest wflib.selftest_c1_aggregate``).

* Contract and registry: docs/BATCH2_CONTRACT.json ``required_categories`` flattened in order must list exactly
  21 distinct categories; the four sources (lane A, lane B, lane C, Gate) are imported and their C1_PROVIDERS
  collected as a list with their source, never merged with dict.update, so a duplicate, unknown, missing,
  wrong-owner or non-callable provider and an import failure each stay visible.
* Strict validation of every raw result (schema/category/provider, ``error`` present and None, non-empty
  ``cases`` list, every case with a unique non-empty name, str detail, pos|neg polarity and ``ran``/``ok`` that
  are the bool True, at least one pos and one neg). Nothing is filled in: a category that was not called, was
  refused or timed out has that state and no ran=true.
* One process boundary per run: a single job child (its main thread runs every provider exactly once; lane C
  groups run once there and its six providers read the same records). The parent enforces a total and a
  per-step deadline and on expiry stops then kills the whole process tree (also setsid'd descendants); lines
  arriving after the deadline are ignored. The child kills any descendant a step leaves behind and exits by
  itself if its parent dies or its own absolute deadline passes.
* Run scope: one AggregateRun per ``wf selftest`` / ``wf self-check --probes`` child, bound to its pid, LOCAL
  and source; the Gate group, the baseline group, the selftest lane C replay and any report read the same
  stored result. Nothing is cached across runs, LOCAL directories or source trees.
* The same job runner executes unittest classes and ``_ok``-style module entries for the selftest
  registrations, and the explicitly separate acceptance-extension suites.

Synthetic adapter checks only: 21/21 here is C1 probe evidence, never production trust, platform attestation,
upgrade/rollback, R1 or U1 evidence.
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
import unittest.mock
import uuid
from pathlib import Path

from .core import WF_HOME

SCHEMA = "c1-provider/1"
CARD = "C-C1-STRICT21-IMPLEMENT-20261016-1"
REQUIRED_COUNT = 21
# (owner, module, provider label). Gate's provider label is "C" (C owns the gate provider).
SOURCES = (("A", "wflib.selftest_lane_a", "A"), ("B", "wflib.selftest_lane_b", "B"),
           ("C", "wflib.selftest_lane_c", "C"), ("Gate", "wflib.selftest_v18", "C"))
OWNERS = {
    "A": ("2R-busy", "2R-network", "2R-quota", "2R-send-unknown", "2R-executor-absent", "2S-credential",
          "2S-wrong-project", "6b-task-generation", "6b-interrupted"),
    "B": ("2S-destructive-or-crossproject", "2S-widening", "1-init-files", "2S-unverified-install",
          "6a-install-review"),
    "C": ("1-empty-guard", "3-layers", "4-na", "5-binding-impact", "5-window-inputs", "6a-live-timing"),
    "Gate": ("1-init-gates-real",),
}
CASE_KEYS = ("name", "polarity", "ran", "ok", "detail")
FD_ENV = "WF_C1_JOB_FD"
EVIDENCE_ENV = "WF_C1_AGG_EVIDENCE_DIR"

# Budgets measured on the composed parent (Linux, one CPU; see the card evidence): lane C 229 assertions
# ~220 s, Gate provider ~21 s (own 420 s cap), A/B providers < 1 s each, C providers after lane C < 1 s.
LANE_C_STEP_S = 900            # the complete lane C matrix inside the aggregation child (C's groups cap at 420 s each)
PROVIDER_STEP_S = 600          # one provider call; the first C provider runs C groups when lane C was not replayed
GATE_STEP_S = 480              # Gate provider: its own 420 s budget plus process start and cleanup
AGGREGATE_BUDGET_S = 1500      # whole aggregation child: lane C + 21 providers, all steps together
STEP_GRACE_S = 30              # child self-destruct margin beyond the parent's deadline


class AggregateRefused(Exception):
    """The aggregation cannot run with an enforceable deadline here (depth, platform); nothing was started."""


# ---------------------------------------------------------------- contract and registry

def contract_order(path=None):
    """(order, problems): required_categories flattened in file order; exactly 21 distinct names."""
    path = Path(path) if path else WF_HOME / "docs" / "BATCH2_CONTRACT.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:   # noqa: BLE001
        return [], [f"contract unreadable: {type(e).__name__}: {e}"]
    groups = doc.get("required_categories") if type(doc) is dict else None
    if type(groups) is not dict:
        return [], ["contract required_categories is not an object"]
    order, problems = [], []
    for group, cats in groups.items():
        if type(cats) is not list or not all(type(c) is str and c for c in cats):
            problems.append(f"contract group {group!r} is not a list of non-empty strings")
            continue
        order.extend(cats)
    dups = sorted({c for c in order if order.count(c) > 1})
    if dups:
        problems.append(f"contract lists duplicate categories {dups}")
    if len(order) != REQUIRED_COUNT:
        problems.append(f"contract lists {len(order)} categories, exactly {REQUIRED_COUNT} required")
    return order, problems


def build_registry(order, sources=SOURCES, owners=OWNERS, importer=importlib.import_module):
    """Collect every (source, category, fn) as a list; per-category state; structural problems."""
    problems, owner_of = [], {}
    for owner, cats in owners.items():
        for cat in cats:
            if cat in owner_of:
                problems.append(f"owner map assigns {cat} to both {owner_of[cat]} and {owner}")
            owner_of.setdefault(cat, owner)
    if set(owner_of) != set(order):
        problems.append(f"owner map/contract mismatch: missing {sorted(set(order) - set(owner_of))}, "
                        f"extra {sorted(set(owner_of) - set(order))}")
    entries, import_failed, labels = [], {}, {}
    for owner, module, label in sources:
        labels[owner] = label
        try:
            mod = importer(module)
        except Exception as e:   # noqa: BLE001
            import_failed[owner] = f"import {module} failed: {type(e).__name__}: {e}"
            problems.append(import_failed[owner])
            continue
        reg = getattr(mod, "C1_PROVIDERS", None)
        if type(reg) is not dict:
            import_failed[owner] = f"{module}.C1_PROVIDERS is {type(reg).__name__}, not a dict"
            problems.append(import_failed[owner])
            continue
        for cat, fn in reg.items():
            entries.append({"owner": owner, "module": module, "label": label, "category": cat, "fn": fn})
    states = {}
    for cat in order:
        found = [e for e in entries if e["category"] == cat]
        owner = owner_of.get(cat)
        if len(found) > 1:
            states[cat] = ("duplicate", [f"registered {len(found)} times: "
                                         + ", ".join(f"{e['owner']}:{e['module']}" for e in found)])
        elif not found:
            states[cat] = ("import-failed", [import_failed[owner]]) if owner in import_failed else \
                ("missing", [f"no provider registered (expected owner {owner})"])
        elif found[0]["owner"] != owner:
            states[cat] = ("wrong-owner", [f"registered by {found[0]['owner']} ({found[0]['module']}), "
                                           f"frozen owner is {owner}"])
        elif not callable(found[0]["fn"]):
            states[cat] = ("non-callable", [f"{found[0]['module']}.C1_PROVIDERS[{cat!r}] is "
                                            f"{type(found[0]['fn']).__name__}"])
        else:
            states[cat] = ("registered", [])
    for e in entries:
        if e["category"] not in order:
            problems.append(f"unknown category {e['category']!r} registered by {e['owner']} ({e['module']})")
    for cat, (state, why) in states.items():
        if state != "registered":
            problems.append(f"{cat}: {state}: {'; '.join(why)}")
    callable_entries = {cat: next(e for e in entries if e["category"] == cat)
                        for cat, (state, _) in states.items() if state == "registered"}
    return {"order": list(order), "states": states, "problems": problems, "entries": callable_entries,
            "labels": {cat: labels.get(owner_of.get(cat)) for cat in order},
            "owners": {cat: owner_of.get(cat) for cat in order},
            "summary": [{"owner": e["owner"], "module": e["module"], "category": e["category"] if type(
                e["category"]) is str else repr(e["category"])} for e in entries]}


# ---------------------------------------------------------------- strict result validation

def validate_result(raw, category, label):
    """All problems of one raw provider result (empty list = the category passed)."""
    if type(raw) is not dict:
        return [f"result is {type(raw).__name__}, not an object"]
    p = [f"missing field {k}" for k in ("schema", "category", "provider", "cases", "error") if k not in raw]
    for key, want in (("schema", SCHEMA), ("category", category), ("provider", label)):
        if key in raw and (type(raw[key]) is not str or raw[key] != want):
            p.append(f"{key} {raw[key]!r} != {want!r}")
    if "error" in raw and raw["error"] is not None:
        p.append(f"error is not None: {str(raw['error'])[:300]!r}")
    cases = raw.get("cases")
    if type(cases) is not list or not cases:
        return p + [f"cases is {'an empty list' if cases == [] else type(cases).__name__}, not a non-empty list"]
    names, polarities = set(), set()
    for i, c in enumerate(cases):
        if type(c) is not dict:
            p.append(f"case[{i}] is {type(c).__name__}, not an object")
            continue
        missing = [k for k in CASE_KEYS if k not in c]
        if missing:
            p.append(f"case[{i}] missing {missing}")
        name = c.get("name")
        if type(name) is not str or not name:
            p.append(f"case[{i}] name {name!r} is not a non-empty string")
        elif name in names:
            p.append(f"case name {name!r} is duplicated")
        else:
            names.add(name)
        tag = name if type(name) is str and name else f"case[{i}]"
        if "detail" in c and type(c["detail"]) is not str:
            p.append(f"{tag} detail is {type(c['detail']).__name__}, not a string")
        pol = c.get("polarity")
        if type(pol) is not str or pol not in ("pos", "neg"):
            p.append(f"{tag} polarity {pol!r} is not 'pos' or 'neg'")
        else:
            polarities.add(pol)
        for key in ("ran", "ok"):
            if key not in c:
                continue
            if type(c[key]) is not bool:
                p.append(f"{tag} {key} {c[key]!r} is {type(c[key]).__name__}, not the bool True")
            elif c[key] is not True:
                p.append(f"{tag} {key}=False")
    for pol in ("pos", "neg"):
        if pol not in polarities:
            p.append(f"no {pol} case")
    return p


# ---------------------------------------------------------------- process tree control

def _ps_table():
    exe = "/bin/ps" if os.path.exists("/bin/ps") else "ps"
    out = subprocess.run([exe, "-A", "-o", "pid=,ppid=,pgid="], capture_output=True, text=True, timeout=60).stdout
    table = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and all(x.lstrip("-").isdigit() for x in parts):
            table[int(parts[0])] = (int(parts[1]), int(parts[2]))
    return table


def descendants(root, table=None):
    table = _ps_table() if table is None else table
    found, frontier = set(), {root}
    while frontier:
        nxt = {pid for pid, (ppid, _) in table.items() if ppid in frontier and pid not in found and pid != root}
        found |= nxt
        frontier = nxt
    return found


def kill_tree(root, include_root=True):
    """Stop the whole tree first (so nothing forks away), then kill every member and its foreign groups.

    Works for descendants in other sessions/groups (start_new_session) as long as they are still linked to
    ``root`` by ppid when this runs. Returns the killed pids."""
    me, mygroup, stopped = os.getpid(), os.getpgrp(), {}
    try:
        for _ in range(50):
            table = _ps_table()
            tree = descendants(root, table) | ({root} if include_root and root in table else set())
            new = {pid for pid in tree if pid not in stopped and pid != me}
            if not new:
                break
            for pid in sorted(new):
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(pid, signal.SIGSTOP)
                stopped[pid] = table[pid][1]
    except (OSError, subprocess.SubprocessError):   # no ps: fall back to the root's group
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if include_root and os.getpgid(root) not in (mygroup, 0, 1):
                os.killpg(os.getpgid(root), signal.SIGKILL)
    for pid in stopped:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)
    for pgid in set(stopped.values()):
        if pgid not in (mygroup, 0, 1):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, signal.SIGKILL)
    for pid in stopped:   # a SIGSTOPped process still receives SIGKILL; continue anything that survived
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGCONT)
    return sorted(stopped)


def _overlaps(a, b):
    a, b = Path(os.path.realpath(a)), Path(os.path.realpath(b))
    return a == b or a in b.parents or b in a.parents


def run_bounded(argv, *, cwd, env, timeout, capture=False):
    """Run argv in the caller's process group (outer group kills still reach it); on expiry the whole tree
    (including setsid'd descendants) is stopped and killed. Returns rc (None on timeout) and output."""
    t0 = time.monotonic()
    pipe = subprocess.PIPE if capture else None
    p = subprocess.Popen([str(a) for a in argv], cwd=str(cwd), env=env, stdout=pipe, stderr=pipe,
                         stdin=subprocess.DEVNULL)
    killed = []
    try:
        out, err = p.communicate(timeout=timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        killed = kill_tree(p.pid)
        out, err = p.communicate()
        rc = None
    return {"rc": rc, "timed_out": rc is None, "seconds": round(time.monotonic() - t0, 2), "killed": killed,
            "stdout": (out or b"").decode("utf-8", "replace") if capture else "",
            "stderr": (err or b"").decode("utf-8", "replace") if capture else ""}


# ---------------------------------------------------------------- job runner (parent side)

def _private_dirs(prefix):
    base = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    dirs = {k: base / k for k in ("home", "tmp", "local", "logs")}
    for d in dirs.values():
        d.mkdir()
    from . import core
    for name, d in [("base", base)] + list(dirs.items()):
        for other in (WF_HOME, core.LOCAL):
            if _overlaps(d, other):
                raise AggregateRefused(f"private {name} {d} overlaps {other}")
    (dirs["home"] / ".gitconfig").write_text("[user]\n\tname = c1-aggregate\n\temail = c1-aggregate@fixture.invalid\n",
                                            encoding="utf-8")
    return base, dirs


def _deadline_support():
    if os.name != "posix" or not hasattr(os, "killpg") or not hasattr(select, "select"):
        return "no POSIX process-group control here: an enforceable deadline is impossible"
    return ""


def run_job(job, *, total_s, step_s=None, extra_env=None, keep=None):
    """Run one job child; return its events and the deadline outcome. ``step_s(name)`` bounds each step."""
    why = _deadline_support()
    if why:
        raise AggregateRefused(why)
    base, dirs = _private_dirs("wf-c1-job-")
    t0 = time.monotonic()
    total_deadline = t0 + total_s
    job = dict(job, deadline_epoch=time.time() + total_s + STEP_GRACE_S, parent_pid=os.getpid())
    spec = base / "job.json"
    spec.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    from .selftest_v18 import probe_depth   # validated; a job child inherits the caller's depth, never a lower one
    env.update({"HOME": str(dirs["home"]), "TMPDIR": str(dirs["tmp"]), "WF_LOCAL_DIR": str(dirs["local"]),
                "WF_NOTIFY": "0", "WF_PROBE_DEPTH": str(probe_depth())})
    env.update(extra_env or {})
    r, w = os.pipe()
    env[FD_ENV] = str(w)
    out_f, err_f = open(dirs["logs"] / "stdout", "wb"), open(dirs["logs"] / "stderr", "wb")
    proc = subprocess.Popen([sys.executable, "-m", "wflib.selftest_c1_aggregate", "--job", str(spec)],
                            cwd=str(WF_HOME), env=env, stdout=out_f, stderr=err_f, stdin=subprocess.DEVNULL,
                            pass_fds=(w,))
    os.close(w)
    events, late, buf = [], [], b""
    step, step_deadline, timed_out, killed, protocol = None, None, None, [], []
    try:
        while True:
            now = time.monotonic()
            limit = min(total_deadline, step_deadline or total_deadline)
            if now >= limit:
                timed_out = {"step": step, "kind": "step" if step_deadline and step_deadline < total_deadline
                             else "total", "after_s": round(now - t0, 2)}
                killed = kill_tree(proc.pid)
                break
            ready, _, _ = select.select([r], [], [], min(1.0, limit - now))
            if not ready:
                continue
            chunk = os.read(r, 1 << 16)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    ev = json.loads(line)
                except ValueError:
                    protocol.append(f"unparsable event line {line[:120]!r}")
                    continue
                ev["t"] = round(time.monotonic() - t0, 3)
                events.append(ev)
                if ev.get("ev") == "start":
                    step = ev.get("step")
                    step_deadline = time.monotonic() + (step_s(step) if step_s else total_s)
                elif ev.get("ev") == "end":
                    step, step_deadline = None, None
        if timed_out:   # whatever is still in the pipe arrived after the deadline: recorded, never counted
            with contextlib.suppress(OSError):
                while True:
                    ready, _, _ = select.select([r], [], [], 0.5)
                    chunk = os.read(r, 1 << 16) if ready else b""
                    if not chunk:
                        break
                    late.append(chunk.decode("utf-8", "replace")[:400])
        try:
            rc = proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            killed += kill_tree(proc.pid)
            rc = proc.wait()
        killed += kill_tree(proc.pid)   # nothing started by the job may outlive it
    finally:
        os.close(r)
        out_f.close()
        err_f.close()
    res = {"events": events, "timed_out": timed_out, "late": late, "killed": sorted(set(killed)), "rc": rc,
           "protocol_problems": protocol, "seconds": round(time.monotonic() - t0, 2),
           "stdout_tail": (dirs["logs"] / "stdout").read_bytes()[-3000:].decode("utf-8", "replace"),
           "stderr_tail": (dirs["logs"] / "stderr").read_bytes()[-3000:].decode("utf-8", "replace"),
           "private_base": str(base)}
    if keep:
        import shutil
        shutil.copytree(dirs["logs"], Path(keep) / "job-logs", dirs_exist_ok=True)
    _rmtree(base)
    return res


def _rmtree(path):
    import shutil

    def onerror(func, p, exc):
        with contextlib.suppress(OSError):
            os.chmod(p, 0o700)
            func(p)
    shutil.rmtree(path, onerror=onerror)


# ---------------------------------------------------------------- job child

class _Emitter:
    def __init__(self, fd):
        self.fd = fd

    def __call__(self, ev):
        data = (json.dumps(ev, ensure_ascii=False, default=repr) + "\n").encode("utf-8")
        while data:
            data = data[os.write(self.fd, data):]


def _watchdog(parent_pid, deadline_epoch):
    """Child-side last resort: exit with the whole subtree if the parent died or the deadline passed."""
    def loop():
        while True:
            time.sleep(1)
            if os.getppid() != parent_pid or time.time() > deadline_epoch:
                with contextlib.suppress(Exception):
                    kill_tree(os.getpid(), include_root=False)
                os._exit(97)
    threading.Thread(target=loop, name="c1-job-watchdog", daemon=True).start()


def _leftovers():
    """Kill and report any descendant a step left running (no background writer survives a step)."""
    try:
        pids = descendants(os.getpid())
        table = _ps_table()
    except (OSError, subprocess.SubprocessError) as e:
        return [f"descendant scan failed: {e}"]
    pids = {p for p in pids if p in table}
    if not pids:
        return []
    exe = "/bin/ps" if os.path.exists("/bin/ps") else "ps"
    cmd = subprocess.run([exe, "-o", "pid=,args=", "-p", ",".join(map(str, sorted(pids)))],
                         capture_output=True, text=True).stdout.strip().splitlines()
    kill_tree(os.getpid(), include_root=False)
    return cmd or [str(p) for p in sorted(pids)]


def _jsonable(raw):
    try:
        json.dumps(raw, ensure_ascii=False)
        return raw, None
    except (TypeError, ValueError) as e:
        return repr(raw)[:4000], f"result is not JSON-serialisable ({type(e).__name__}: {e}); repr kept"


def _strict_ok_recorder(emit, kind):
    def _ok(name, cond, detail=""):
        strict = type(cond) is bool
        emit({"ev": kind, "name": str(name), "ok": cond if strict else False, "cond_type": type(cond).__name__,
              "detail": (detail if isinstance(detail, str) else str(detail)) if strict else
              f"non-bool condition ({type(cond).__name__}) fails closed; original detail: {str(detail)[:1500]}"})
        return cond
    return _ok


def _child_aggregate(job, emit):
    order, cproblems = contract_order(job.get("contract"))
    sources = [tuple(s) for s in job.get("sources", SOURCES)]
    owners = {k: tuple(v) for k, v in job.get("owners", OWNERS).items()}
    reg = build_registry(order, sources, owners)
    emit({"ev": "registry", "order": order, "contract_problems": cproblems, "problems": reg["problems"],
          "states": {c: list(v) for c, v in reg["states"].items()}, "summary": reg["summary"],
          "labels": reg["labels"], "owners": reg["owners"]})
    if cproblems:
        return
    if job.get("lane_c"):
        emit({"ev": "start", "step": "lane-c"})
        try:
            from .selftest_lane_c import run_lane_c
            run_lane_c(_strict_ok_recorder(emit, "lane_c_ok"))
            emit({"ev": "lane_c_done", "leftovers": _leftovers()})
        except BaseException as e:   # noqa: BLE001 - reported, never counted
            emit({"ev": "lane_c_error", "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]})
        emit({"ev": "end", "step": "lane-c"})
    calls = {}
    for cat in order:
        entry = reg["entries"].get(cat)
        if entry is None:
            continue
        emit({"ev": "start", "step": cat})
        calls[cat] = calls.get(cat, 0) + 1
        t0 = time.monotonic()
        raw, exc = None, None
        try:
            raw = entry["fn"]()
        except BaseException as e:   # noqa: BLE001 - reported as an exception state, never as a result
            exc = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-2000:]}"
        seconds = round(time.monotonic() - t0, 3)
        value, conv = _jsonable(raw) if exc is None else (None, None)
        emit({"ev": "result", "category": cat, "raw": value, "json_problem": conv, "exception": exc,
              "seconds": seconds, "calls": calls[cat], "leftovers": _leftovers()})
        emit({"ev": "end", "step": cat})
    emit({"ev": "done", "calls": calls})


def _load_targets(targets):
    """``module:Class`` or ``module:*`` (every TestCase class defined in that module, in definition order)."""
    classes, errors = [], []
    for target in targets:
        module, _, name = target.partition(":")
        try:
            mod = importlib.import_module(module)
            if name == "*":
                found = [o for o in vars(mod).values() if isinstance(o, type) and issubclass(o, unittest.TestCase)
                         and o.__module__ == mod.__name__]
                if not found:
                    raise LookupError(f"{module} defines no unittest.TestCase class")
                classes.extend(found)
                continue
            obj = getattr(mod, name)
            if not (isinstance(obj, type) and issubclass(obj, unittest.TestCase)):
                raise TypeError(f"{target} is not a unittest.TestCase class")
            classes.append(obj)
        except Exception as e:   # noqa: BLE001
            errors.append(f"{target}: {type(e).__name__}: {e}")
    return classes, errors


def _child_unittest(job, emit):
    classes, errors = _load_targets(job["targets"])
    cases = [t for cls in classes for t in unittest.defaultTestLoader.loadTestsFromTestCase(cls)]
    emit({"ev": "loaded", "ids": [t.id() for t in cases], "load_errors": errors})
    if errors or not cases:
        return

    class Result(unittest.TestResult):
        def startTest(self, test):
            super().startTest(test)
            emit({"ev": "start", "step": test.id()})

        def _out(self, test, outcome, detail=""):
            emit({"ev": "test", "id": test.id(), "outcome": outcome, "detail": detail[-3000:]})

        def addSuccess(self, test):
            super().addSuccess(test)
            self._out(test, "success")

        def addFailure(self, test, err):
            super().addFailure(test, err)
            self._out(test, "failure", self._exc_info_to_string(err, test))

        def addError(self, test, err):
            super().addError(test, err)
            self._out(test, "error", self._exc_info_to_string(err, test))

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            self._out(test, "skip", str(reason))

        def addExpectedFailure(self, test, err):
            super().addExpectedFailure(test, err)
            self._out(test, "expectedFailure", self._exc_info_to_string(err, test))

        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            self._out(test, "unexpectedSuccess")

        def stopTest(self, test):
            super().stopTest(test)
            emit({"ev": "end", "step": test.id()})
    result = Result()
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        unittest.TestSuite(cases).run(result)
    emit({"ev": "summary", "testsRun": result.testsRun, "leftovers": _leftovers(), "output_tail": output.getvalue()[-2000:]})


def _child_okfn(job, emit):
    module, _, name = job["target"].partition(":")
    fn = getattr(importlib.import_module(module), name)
    emit({"ev": "start", "step": job["target"]})
    try:
        fn(_strict_ok_recorder(emit, "ok"))
        emit({"ev": "okfn_done", "leftovers": _leftovers()})
    except BaseException as e:   # noqa: BLE001
        emit({"ev": "okfn_error", "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]})
    emit({"ev": "end", "step": job["target"]})


def _job_main(spec_path):
    emit = _Emitter(int(os.environ[FD_ENV]))
    job = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    sys.path[:0] = [p for p in job.get("sys_path", []) if p not in sys.path]
    _watchdog(job["parent_pid"], job["deadline_epoch"])
    emit({"ev": "hello", "pid": os.getpid(), "pgid": os.getpgrp(), "sid": os.getsid(0), "uid": os.getuid(),
          "python": sys.version.split()[0], "executable": sys.executable, "main_thread": True,
          "depth": os.environ.get("WF_PROBE_DEPTH")})
    try:
        {"aggregate": _child_aggregate, "unittest": _child_unittest, "okfn": _child_okfn}[job["kind"]](job, emit)
    finally:
        emit({"ev": "bye", "leftovers": _leftovers()})
    return 0


# ---------------------------------------------------------------- aggregation (parent side)

def _step_budget(step):
    return LANE_C_STEP_S if step == "lane-c" else GATE_STEP_S if step == "1-init-gates-real" else PROVIDER_STEP_S


def aggregate(*, lane_c=False, job_overrides=None, total_s=None, step_s=None):
    """Run the strict aggregation once in one job child; return the full per-run record (never raises for a
    provider/registry fault; raises AggregateRefused only before anything is started)."""
    from .selftest_v18 import ProbeDepthError, probe_depth
    try:
        depth = probe_depth()
    except ProbeDepthError as e:
        raise AggregateRefused(str(e)) from None
    if depth != 0:
        raise AggregateRefused(f"WF_PROBE_DEPTH={depth}: the strict aggregate runs only at depth 0 (never nested)")
    job = {"kind": "aggregate", "lane_c": bool(lane_c)}
    job.update(job_overrides or {})
    started = time.time()
    res = run_job(job, total_s=total_s or AGGREGATE_BUDGET_S, step_s=step_s or _step_budget)
    rec = interpret(res, lane_c=bool(lane_c))
    rec["started_epoch"] = started
    _maybe_export(rec)
    return rec


def interpret(res, *, lane_c):
    """Turn the job events into the per-category record; only events before the deadline are used."""
    evs = res["events"]
    hello = next((e for e in evs if e.get("ev") == "hello"), None)
    regev = next((e for e in evs if e.get("ev") == "registry"), None)
    rec = {"card": CARD, "required": REQUIRED_COUNT, "job": {k: res[k] for k in (
        "timed_out", "late", "killed", "rc", "protocol_problems", "seconds", "stdout_tail", "stderr_tail")},
        "hello": hello, "order": [], "categories": {}, "problems": list(res["protocol_problems"]), "lane_c": None}
    if res["timed_out"]:
        rec["problems"].append(f"deadline: {res['timed_out']['kind']} budget expired during "
                               f"{res['timed_out']['step']!r}; process tree killed {res['killed']}")
    if res["late"]:
        rec["problems"].append(f"{len(res['late'])} chunk(s) arrived after the deadline and were ignored")
    if regev is None:
        rec["problems"].append(f"job produced no registry (rc={res['rc']}): {res['stderr_tail'][-600:]}")
        return _finish(rec)
    rec["order"] = regev["order"]
    rec["problems"] += [f"contract: {p}" for p in regev["contract_problems"]] + [f"registry: {p}" for p in regev["problems"]]
    rec["registry"] = {k: regev[k] for k in ("summary", "states", "labels", "owners")}
    results = {}
    for e in evs:
        if e.get("ev") == "result":
            if e["category"] in results:
                rec["problems"].append(f"protocol: second result for {e['category']}")
            results.setdefault(e["category"], e)
    started = [e.get("step") for e in evs if e.get("ev") == "start"]
    for cat in rec["order"]:
        state, why = regev["states"].get(cat, ("missing", ["not in registry"]))
        entry = {"category": cat, "owner": regev["owners"].get(cat), "label": regev["labels"].get(cat),
                 "state": state, "problems": list(why), "calls": 0, "seconds": None, "raw": None}
        ev = results.get(cat)
        if state != "registered":
            pass
        elif ev is None:
            entry["state"] = "timeout" if res["timed_out"] and res["timed_out"]["step"] == cat else \
                ("interrupted" if cat in started else "not-called")
            entry["problems"].append({"timeout": "provider exceeded its deadline; process tree killed",
                                      "interrupted": "provider started but produced no result (job ended)",
                                      "not-called": "provider was not called in this run (job ended earlier)"}
                                     [entry["state"]])
        else:
            entry.update(calls=ev["calls"], seconds=ev["seconds"], raw=ev["raw"])
            if ev["exception"]:
                entry["state"], entry["problems"] = "exception", [ev["exception"][-1500:]]
            else:
                probs = ([ev["json_problem"]] if ev["json_problem"] else []) + \
                    validate_result(ev["raw"], cat, regev["labels"].get(cat))
                if ev["calls"] != 1:
                    probs.append(f"provider called {ev['calls']} times in this run, exactly once required")
                if ev["leftovers"]:
                    probs.append(f"left running descendants (killed): {ev['leftovers'][:5]}")
                entry["state"] = "failed" if probs else "passed"
                entry["problems"] = probs
                cases = ev["raw"].get("cases") if type(ev["raw"]) is dict else None
                if type(cases) is list:
                    entry["case_count"] = len(cases)
                    entry["pos"] = sum(1 for c in cases if type(c) is dict and c.get("polarity") == "pos")
                    entry["neg"] = sum(1 for c in cases if type(c) is dict and c.get("polarity") == "neg")
                    entry["bad_cases"] = [c.get("name") for c in cases if type(c) is dict and not (
                        c.get("ran") is True and c.get("ok") is True)][:20]
        rec["categories"][cat] = entry
    if lane_c:
        asserts = [e for e in evs if e.get("ev") == "lane_c_ok"]
        done = any(e.get("ev") == "lane_c_done" for e in evs)
        err = next((e for e in evs if e.get("ev") == "lane_c_error"), None)
        lane_left = next((e.get("leftovers") for e in evs if e.get("ev") == "lane_c_done"), [])
        rec["lane_c"] = {"asserts": [(a["name"], a["ok"], a["detail"]) for a in asserts], "complete": done and not err,
                         "error": err and err["error"], "leftovers": lane_left,
                         "timed_out": bool(res["timed_out"] and res["timed_out"]["step"] == "lane-c")}
    return _finish(rec)


def _finish(rec):
    cats = rec["categories"]
    rec["passed"] = sum(1 for c in cats.values() if c["state"] == "passed")
    rec["all_pass"] = (not rec["problems"] and len(rec["order"]) == REQUIRED_COUNT and len(cats) == REQUIRED_COUNT
                       and rec["passed"] == REQUIRED_COUNT
                       and all(c["calls"] == 1 for c in cats.values()))
    return rec


def _maybe_export(rec):
    keep = os.environ.get(EVIDENCE_ENV)
    if not keep:
        return
    kp = Path(keep)
    if not kp.is_absolute() or _overlaps(kp, WF_HOME) or kp.exists():
        rec["problems"].append(f"{EVIDENCE_ENV} must be a new absolute path outside the tool; not exported")
        _finish(rec)
        return
    kp.mkdir(parents=True)
    (kp / "aggregate.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=repr), encoding="utf-8")
    for cat, c in rec["categories"].items():
        (kp / f"provider-{cat}.json").write_text(json.dumps(c["raw"], ensure_ascii=False, indent=1, default=repr),
                                                encoding="utf-8")


def summary_lines(rec):
    """One line per required category (contract order), then the run-level problems."""
    lines = []
    for cat in rec["order"]:
        c = rec["categories"][cat]
        head = f"{cat}：{'通过' if c['state'] == 'passed' else '未通过'}（{c['owner']}，state={c['state']}，calls={c['calls']}"
        if "case_count" in c:
            head += f"，cases={c['case_count']} pos={c['pos']} neg={c['neg']}"
        if c["seconds"] is not None:
            head += f"，{c['seconds']}s"
        head += "）"
        if c["state"] != "passed":
            head += "：" + "；".join(str(p) for p in c["problems"])[:500]
        lines.append(head)
    return lines + [f"运行级问题：{p}"[:600] for p in rec["problems"]]


# ---------------------------------------------------------------- run scope

class AggregateRun:
    """One run: bound to this pid, LOCAL and source; the aggregation executes at most once."""

    def __init__(self, *, lane_c):
        from . import core
        self.run_id, self.lane_c, self.pid = uuid.uuid4().hex, lane_c, os.getpid()
        self.local, self.source = os.path.realpath(core.LOCAL), os.path.realpath(WF_HOME)
        self.record, self.refused, self.executions, self.reads = None, None, 0, 0

    def matches(self):
        from . import core
        return (self.pid == os.getpid() and self.local == os.path.realpath(core.LOCAL)
                and self.source == os.path.realpath(WF_HOME))

    def ensure(self):
        self.reads += 1
        if self.record is None and self.refused is None:
            self.executions += 1
            try:
                self.record = aggregate(lane_c=self.lane_c)
            except AggregateRefused as e:
                self.refused = str(e)
            except Exception as e:   # noqa: BLE001 - an infrastructure fault is a failed run, never a pass
                self.refused = f"aggregate infrastructure error: {type(e).__name__}: {e}"
        if self.record is None:
            return _refused_record(self.refused)
        self.record["run"] = {"run_id": self.run_id, "executions": self.executions, "reads": self.reads}
        return self.record


def _refused_record(why):
    return _finish({"card": CARD, "required": REQUIRED_COUNT, "order": [], "categories": {}, "lane_c": None,
                    "problems": [f"refused: {why}"], "job": None})


_ACTIVE = None


@contextlib.contextmanager
def run_scope(*, lane_c):
    global _ACTIVE
    previous = _ACTIVE
    _ACTIVE = AggregateRun(lane_c=lane_c)
    try:
        yield _ACTIVE
    finally:
        _ACTIVE = previous


@contextlib.contextmanager
def ensure_scope(*, lane_c):
    """Reuse the caller's run if it is valid here, else open one for the duration of the block."""
    run = current_run()
    if run is not None:
        yield run
    else:
        with run_scope(lane_c=lane_c) as run:
            yield run


def current_run():
    return _ACTIVE if _ACTIVE is not None and _ACTIVE.matches() else None


def replay_lane_c(run, _ok):
    """The lane C assertions of this run's aggregation child, in order, then any incompleteness."""
    rec = run.ensure()
    lane = rec.get("lane_c")
    if lane is None:
        _ok("B1-C 车道检查未运行（严格聚合子进程未产生车道 C 结果）", False, "；".join(rec["problems"])[:1500])
        return
    for name, ok, detail in lane["asserts"]:
        _ok(name, ok, detail)
    if not lane["complete"] or lane["leftovers"]:
        _ok("B1-C 车道检查未完整运行（严格聚合子进程）", False, json.dumps(
            {k: lane[k] for k in ("error", "timed_out", "leftovers")}, ensure_ascii=False)[:1500])


# ---------------------------------------------------------------- selftest registrations (child jobs)

def register_unittest_job(_ok, label, targets, *, total_s, extra_env=None):
    """One _ok per loaded test id; load errors, zero methods, suite-level errors, skips, expected failures,
    unexpected successes, partial runs and timeouts all fail closed. Returns the counts."""
    try:
        res = run_job({"kind": "unittest", "targets": list(targets)}, total_s=total_s, extra_env=extra_env)
    except AggregateRefused as e:
        _ok(f"{label}：未运行（{e}）", False, str(e))
        return {"label": label, "loaded": 0, "run": 0, "passed": 0, "refused": str(e)}
    evs = res["events"]
    loaded = next((e for e in evs if e.get("ev") == "loaded"), None)
    if loaded is None or loaded["load_errors"] or not loaded["ids"]:
        why = (loaded or {}).get("load_errors") or ("0 个测试方法" if loaded else f"未载入：{res['stderr_tail'][-800:]}")
        _ok(f"{label}：未登记（{str(why)[:200]}）", False, json.dumps(
            {"why": why, "timed_out": res["timed_out"], "rc": res["rc"]}, ensure_ascii=False)[:1500])
        return {"label": label, "loaded": len((loaded or {}).get("ids") or []), "run": 0, "passed": 0}
    ids = loaded["ids"]
    outcomes = {}
    suite_errors = []
    for e in evs:
        if e.get("ev") == "test":
            if e["id"] in ids and e["id"] not in outcomes:
                outcomes[e["id"]] = (e["outcome"], e["detail"])
            elif e["id"] in outcomes:
                outcomes[e["id"]] = ("error", (outcomes[e["id"]][1] + "\n" + e["detail"])[-3000:])
            else:   # setUpClass / tearDownClass / module fixtures: not one of the loaded methods
                suite_errors.append(f"{e['id']}: {e['outcome']}: {e['detail'][-800:]}")
    summary = next((e for e in evs if e.get("ev") == "summary"), None)
    if res["timed_out"]:
        suite_errors.append(f"timeout after {res['timed_out']['after_s']}s in {res['timed_out']['step']}; killed {res['killed']}")
    if summary is None:
        suite_errors.append(f"suite did not finish (rc={res['rc']}): {res['stderr_tail'][-600:]}")
    elif summary["testsRun"] != len(ids):
        suite_errors.append(f"suite ran {summary['testsRun']} of {len(ids)} loaded cases")
    if summary and summary.get("leftovers"):
        suite_errors.append(f"left running descendants (killed): {summary['leftovers'][:5]}")
    missing = [i for i in ids if i not in outcomes]
    if missing and not suite_errors:
        suite_errors.append(f"{len(missing)} loaded case(s) produced no outcome")
    suite_detail = "\n".join(suite_errors)
    passed = 0
    for tid in ids:
        outcome, detail = outcomes.get(tid, ("not-run", ""))
        good = not suite_errors and outcome == "success"
        passed += good
        _ok(tid, good, "" if good else (suite_detail + "\n" if suite_detail else "") + f"{outcome}: {detail}"[-2500:])
    return {"label": label, "loaded": len(ids), "run": summary["testsRun"] if summary else 0, "passed": passed,
            "seconds": res["seconds"], "suite_errors": suite_errors}


def register_okfn_job(_ok, label, target, *, total_s):
    """Run a module's ``fn(_ok)`` entry once in a bounded child and record each actual _ok in order (strict
    bool); an error, a timeout, an incomplete run or zero assertions add a failing assertion."""
    try:
        res = run_job({"kind": "okfn", "target": target}, total_s=total_s)
    except AggregateRefused as e:
        _ok(f"{label}：未运行（{e}）", False, str(e))
        return {"label": label, "asserts": 0, "passed": 0}
    evs = res["events"]
    asserts = [e for e in evs if e.get("ev") == "ok"]
    for a in asserts:
        _ok(a["name"], a["ok"], a["detail"])
    done = next((e for e in evs if e.get("ev") == "okfn_done"), None)
    err = next((e for e in evs if e.get("ev") == "okfn_error"), None)
    problems = []
    if err:
        problems.append(err["error"] + "\n" + err["trace"][-1200:])
    if res["timed_out"]:
        problems.append(f"timeout after {res['timed_out']['after_s']}s; killed {res['killed']}")
    if done is None and not err and not res["timed_out"]:
        problems.append(f"entry did not finish (rc={res['rc']}): {res['stderr_tail'][-800:]}")
    if done and done.get("leftovers"):
        problems.append(f"left running descendants (killed): {done['leftovers'][:5]}")
    if not asserts:
        problems.append("0 assertions recorded")
    if problems:
        _ok(f"{label}：未完整运行", False, "\n".join(problems)[:2500])
    return {"label": label, "asserts": len(asserts), "passed": sum(1 for a in asserts if a["ok"] is True),
            "seconds": res["seconds"], "problems": problems}


def register_fxtx(_ok, label, *, total_s):
    """FX-TX through its own entry (python -B -S -m wflib.selftest_install_fixtures --mode new) in a bounded
    process; each chain/consumer verdict of its JSON report is one assertion; no report fails closed."""
    base, dirs = _private_dirs("wf-fxtx-reg-")
    out = dirs["tmp"] / "fx-tx-report.json"
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(dirs["home"]), "TMPDIR": str(dirs["tmp"]),
           "WF_LOCAL_DIR": str(dirs["local"]), "WF_NOTIFY": "0", "LC_ALL": "C.UTF-8"}
    try:
        r = run_bounded([sys.executable, "-B", "-S", "-m", "wflib.selftest_install_fixtures", "--mode", "new",
                         "--out", str(out)], cwd=WF_HOME, env=env, timeout=total_s, capture=True)
        report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    except (OSError, ValueError) as e:
        r, report = {"rc": None, "timed_out": False, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}, None
    finally:
        _rmtree(base)
    verdicts = fxtx_verdicts(report)
    for name, good, detail in verdicts:
        _ok(name, good, detail)
    problems = []
    if r["timed_out"]:
        problems.append(f"timeout after {total_s}s; killed {r['killed']}")
    if r["rc"] != 0:
        problems.append(f"entry rc={r['rc']}: {(r['stdout'] + r['stderr'])[-1200:]}")
    if not verdicts:
        problems.append("no chain verdicts in the report")
    if problems:
        _ok(f"{label}：未完整运行", False, "\n".join(problems)[:2500])
    return {"label": label, "asserts": len(verdicts), "passed": sum(1 for v in verdicts if v[1] is True),
            "rc": r["rc"], "seconds": r.get("seconds")}


def fxtx_verdicts(report):
    """(name, ok, detail) from the FX-TX JSON report's own structured verdicts; ok is the bool True only.

    One per chain (no error and an empty problems list), one per CL-CT5 check, one per tamper row (every
    fail_closed value True), one per audit check, the relative-cwd probe, one per negative, the expected chain
    count (KINDS x OUTCOMES x receipt/none from wflib.install_fixtures) and the report's own failures list."""
    if type(report) is not dict:
        return []
    out = []
    try:
        from .install_fixtures import KINDS, OUTCOMES
        want = len(KINDS) * len(OUTCOMES) * 2
    except Exception as e:   # noqa: BLE001
        want, out = None, [("FX-TX 夹具常量可读", False, f"{type(e).__name__}: {e}")]
    chains = report.get("chains") if type(report.get("chains")) is list else []
    out.append(("FX-TX 模式 new 且链数等于 KINDS×OUTCOMES×2", report.get("mode") == "new" and want is not None
                and len(chains) == want, f"mode={report.get('mode')!r} chains={len(chains)} want={want}"))
    for c in chains:
        good = type(c) is dict and "error" not in c and c.get("problems") == []
        out.append((f"FX-TX 链 {c.get('case') if type(c) is dict else c}", good,
                    "" if good else json.dumps({k: c.get(k) for k in ("error", "problems")} if type(c) is dict else c,
                                               ensure_ascii=False, default=str)[:1500]))
    cl = report.get("cl_ct5")
    if type(cl) is not dict or "error" in cl or type(cl.get("checks")) is not dict or not cl["checks"]:
        out.append(("FX-TX CL-CT5", False, json.dumps(cl, ensure_ascii=False, default=str)[:1500]))
    else:
        out += [(f"FX-TX CL-CT5 {k}", v is True, "" if v is True else repr(v)) for k, v in cl["checks"].items()]
    tamper = report.get("tamper_negatives")
    for name, row in (tamper.items() if type(tamper) is dict else []):
        fc = row.get("fail_closed") if type(row) is dict else None
        good = type(fc) is dict and bool(fc) and all(v is True for v in fc.values())
        out.append((f"FX-TX 篡改负例 {name}", good, "" if good else json.dumps(row, ensure_ascii=False, default=str)[:1200]))
    audit = report.get("audit_checks")
    out += [(f"FX-TX 审计 {k}", v is True, "" if v is True else repr(v))
            for k, v in (audit.items() if type(audit) is dict else [])]
    rel = report.get("relative_cwd_probe")
    out.append(("FX-TX 相对 cwd 可执行审计", type(rel) is dict and rel.get("ok") is True,
                json.dumps(rel, ensure_ascii=False, default=str)[:800]))
    neg = report.get("negatives")
    out += [(f"FX-TX 负例 {k}", v == "refused, nothing written", "" if v == "refused, nothing written" else repr(v))
            for k, v in (neg.items() if type(neg) is dict else []) if not str(k).endswith(":why")]
    fails = report.get("failures")
    out.append(("FX-TX 报告自身 failures 为空", fails == [], json.dumps(fails, ensure_ascii=False, default=str)[:1500]))
    return out


# ================================================================ focused tests (synthetic providers only)
# Fault-injection of the aggregate's own contract; no real provider runs here except the read-only registry
# check of the four real sources. Results are adapter evidence, never production or business evidence.

_FAKE_COMMON = """import os, subprocess, sys, time
def good():
    return [{"name": "p1", "polarity": "pos", "ran": True, "ok": True, "detail": "x"},
            {"name": "n1", "polarity": "neg", "ran": True, "ok": True, "detail": "y"}]
def mk(cat, label, mode="good"):
    def fn():
        counter = os.environ.get("C1FAKE_COUNTER")
        if counter:
            with open(os.path.join(counter, cat), "a") as f:
                f.write(str(os.getpid()) + "\\n")
        res = {"schema": "c1-provider/1", "category": cat, "provider": label, "cases": good(), "error": None}
        if mode == "raise":
            raise RuntimeError("fake provider exploded")
        if mode == "none":
            return None
        if mode == "list":
            return [res]
        if mode == "error_empty":
            res["error"] = ""
        if mode == "ok_one":
            res["cases"][1]["ok"] = 1
        if mode == "sandwich":
            res["cases"] = [good()[0], dict(good()[1], name="mid", ok=False), dict(good()[1], name="n2")]
        if mode == "sleep":
            time.sleep(60)
        if mode == "late_writer":
            subprocess.Popen([sys.executable, "-c", "import sys,time; time.sleep(3); open(sys.argv[1],'w').write('late')",
                              os.environ["C1FAKE_LATE"]], start_new_session=True)
            time.sleep(60)
        if mode == "leftover":
            p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True)
            with open(os.environ["C1FAKE_PIDFILE"], "w") as f:
                f.write(str(p.pid))
        return res
    return fn
"""


def _fake_world(root, modes=None, *, extra=None, dup=None, move=None, noncallable=None, import_fail=None,
                not_dict=None, drop=None):
    """A synthetic contract (21 categories) and four synthetic source modules shaped like A/B/C/Gate."""
    modes = modes or {}
    pkg = "c1fake_" + uuid.uuid4().hex[:10]
    d = Path(root) / pkg
    d.mkdir(parents=True)
    (d / "__init__.py").write_text("", encoding="utf-8")
    (d / "common.py").write_text(_FAKE_COMMON, encoding="utf-8")
    sizes = (("A", 9, "A"), ("B", 5, "B"), ("C", 6, "C"), ("Gate", 1, "C"))
    cats = {o: [f"{o.lower()}-{i:02d}" for i in range(1, n + 1)] for o, n, _ in sizes}
    order = cats["A"][:5] + cats["B"][:2] + cats["A"][5:] + cats["B"][2:] + cats["C"] + cats["Gate"]
    contract = Path(root) / f"{pkg}-contract.json"
    contract.write_text(json.dumps({"required_categories": {"g1": order[:7], "g2": order[7:14], "g3": order[14:]}}),
                        encoding="utf-8")
    regs = {o: {c: (lab, modes.get(c, "good")) for c in cats[o]} for o, _, lab in sizes}
    labels = {o: lab for o, _, lab in sizes}
    for owner, cat in (drop or []):
        regs[owner].pop(cat)
    for src, dst, cat in (move or []):
        regs[dst][cat] = regs[src].pop(cat)
    for owner, cat in (dup or []) + (extra or []):
        regs[owner][cat] = (labels[owner], "good")
    sources = []
    for owner, _, label in sizes:
        mod = f"src_{owner.lower()}"
        body = [f"from {pkg}.common import mk"]
        if owner in (import_fail or []):
            body.append("raise ImportError('synthetic import failure')")
        items = ", ".join(f"{c!r}: " + ("42" if (owner, c) in (noncallable or []) else f"mk({c!r}, {lab!r}, {m!r})")
                          for c, (lab, m) in regs[owner].items())
        body.append(f"C1_PROVIDERS = list({{{items}}}.items())" if owner in (not_dict or [])
                    else f"C1_PROVIDERS = {{{items}}}")
        (d / f"{mod}.py").write_text("\n".join(body) + "\n", encoding="utf-8")
        sources.append((owner, f"{pkg}.{mod}", label))
    return {"contract": str(contract), "sources": [list(s) for s in sources],
            "owners": {o: cats[o] for o, _, _ in sizes}, "sys_path": [str(root)], "order": order, "cats": cats}


def _naive_validate(raw, category, label):
    """The weak shape an aggregator must not have: any pos ok and any neg ok, truthiness, no structure."""
    try:
        cases = raw.get("cases") or []
        return [] if any(c.get("polarity") == "pos" and c.get("ok") for c in cases) and \
            any(c.get("polarity") == "neg" and c.get("ok") for c in cases) else ["naive: no pos/neg ok"]
    except Exception:   # noqa: BLE001
        return ["naive: unreadable"]


def _good(cat="x-01", label="A"):
    return {"schema": SCHEMA, "category": cat, "provider": label, "error": None,
            "cases": [{"name": "p", "polarity": "pos", "ran": True, "ok": True, "detail": ""},
                      {"name": "n", "polarity": "neg", "ran": True, "ok": True, "detail": ""}]}


def _mutants():
    """(label, raw) faults that the strict validator must reject."""
    out = [("none", None), ("list", [_good()]), ("str", "ok")]
    for k in ("schema", "category", "provider", "cases", "error"):
        r = _good()
        del r[k]
        out.append((f"missing {k}", r))
    for k, v in (("schema", "c1-provider/2"), ("category", "x-02"), ("provider", "B"), ("error", ""),
                 ("error", "boom"), ("error", False), ("cases", []), ("cases", {}), ("cases", "p,n")):
        r = _good()
        r[k] = v
        out.append((f"{k}={v!r}", r))
    for key in CASE_KEYS:
        r = _good()
        del r["cases"][0][key]
        out.append((f"case missing {key}", r))
    for key, values in (("ran", (False, 1, "true", None, 0)), ("ok", (False, 1, "true", None, 0)),
                        ("polarity", ("POS", "x", None, 1)), ("name", ("", None, 3)), ("detail", (None, 1, ["x"]))):
        for v in values:
            r = _good()
            r["cases"][1][key] = v
            out.append((f"case {key}={v!r}", r))
    r = _good()
    r["cases"][1]["name"] = "p"
    out.append(("duplicate case name", r))
    r = _good()
    r["cases"][1]["polarity"] = "pos"
    out.append(("only pos", r))
    r = _good()
    r["cases"][0]["polarity"] = "neg"
    out.append(("only neg", r))
    r = _good()
    r["cases"].insert(1, {"name": "mid", "polarity": "neg", "ran": True, "ok": False, "detail": "fails"})
    out.append(("failing case between successes", r))
    r = _good()
    r["cases"].insert(1, {"name": "mid", "polarity": "pos", "ran": False, "ok": False, "detail": "not run"})
    out.append(("ran=False case between successes", r))
    r = _good()
    r["cases"][0] = "p"
    out.append(("case not an object", r))
    return out


class _NoExport(unittest.TestCase):
    def setUp(self):
        env = {k: v for k, v in os.environ.items() if k != EVIDENCE_ENV}
        patcher = unittest.mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = Path(tempfile.mkdtemp(prefix="c1agg-test-")).resolve()
        self.addCleanup(_rmtree, self.tmp)


class ContractRegistryTests(_NoExport):
    def test_real_contract_is_exactly_21_in_file_order(self):
        order, problems = contract_order()
        doc = json.loads((WF_HOME / "docs" / "BATCH2_CONTRACT.json").read_text(encoding="utf-8"))
        self.assertEqual(problems, [])
        self.assertEqual(order, [c for g in doc["required_categories"].values() for c in g])
        self.assertEqual(len(order), 21)
        self.assertEqual(sorted(order), sorted(c for cats in OWNERS.values() for c in cats))

    def test_bad_contracts_fail_closed(self):
        for name, groups in (("twenty", {"g": [f"c{i}" for i in range(20)]}),
                             ("twenty-two", {"g": [f"c{i}" for i in range(22)]}),
                             ("duplicate", {"g": [f"c{i}" for i in range(20)] + ["c0"]}),
                             ("not-a-list", {"g": "c1"}), ("empty-name", {"g": [""] + [f"c{i}" for i in range(20)]})):
            path = self.tmp / f"{name}.json"
            path.write_text(json.dumps({"required_categories": groups}), encoding="utf-8")
            self.assertTrue(contract_order(path)[1], name)
        (self.tmp / "broken.json").write_text("{", encoding="utf-8")
        self.assertTrue(contract_order(self.tmp / "broken.json")[1])

    def test_real_sources_register_exactly_the_frozen_owner_map(self):
        order, _ = contract_order()
        reg = build_registry(order)   # imports the real modules; calls no provider
        self.assertEqual(reg["problems"], [])
        self.assertEqual({c: s for c, (s, _) in reg["states"].items()}, {c: "registered" for c in order})
        self.assertEqual({o: sorted(c for c in order if reg["owners"][c] == o) for o in OWNERS},
                         {o: sorted(cats) for o, cats in OWNERS.items()})
        self.assertEqual(sorted(e["module"] for e in reg["summary"]).count("wflib.selftest_lane_a"), 9)

    def test_registry_faults_each_stay_visible(self):
        w = _fake_world(self.tmp, extra=[("C", "zz-unknown")], dup=[("B", "a-01")], move=[("A", "B", "a-02")],
                        noncallable=[("C", "c-01")], drop=[("Gate", "gate-01")])
        importlib.invalidate_caches()
        sys.path.insert(0, w["sys_path"][0])
        self.addCleanup(sys.path.remove, w["sys_path"][0])
        reg = build_registry(w["order"], [tuple(s) for s in w["sources"]], w["owners"])
        states = {c: s for c, (s, _) in reg["states"].items()}
        self.assertEqual((states["a-01"], states["a-02"], states["c-01"], states["gate-01"]),
                         ("duplicate", "wrong-owner", "non-callable", "missing"))
        self.assertTrue(any("zz-unknown" in p for p in reg["problems"]))
        self.assertTrue(any("A:" in p and "B:" in p for p in reg["problems"] if "a-01" in p))   # both sources named
        self.assertNotIn("a-01", reg["entries"])   # an ambiguous category is never called

    def test_import_failure_and_non_dict_registry(self):
        w = _fake_world(self.tmp, import_fail=["B"], not_dict=["C"])
        sys.path.insert(0, w["sys_path"][0])
        self.addCleanup(sys.path.remove, w["sys_path"][0])
        reg = build_registry(w["order"], [tuple(s) for s in w["sources"]], w["owners"])
        self.assertTrue(all(reg["states"][c][0] == "import-failed" for c in w["cats"]["B"] + w["cats"]["C"]))
        self.assertTrue(any("synthetic import failure" in p for p in reg["problems"]))
        self.assertTrue(any("not a dict" in p for p in reg["problems"]))


class ValidationTests(unittest.TestCase):
    def test_good_result_passes_with_accepted_extra_fields(self):
        r = _good()
        r.update(lane="A", card="X", positive={}, negative={}, ok=True)   # accepted compatibility fields
        self.assertEqual(validate_result(r, "x-01", "A"), [])

    def test_every_mutant_is_rejected(self):
        for label, raw in _mutants():
            self.assertTrue(validate_result(raw, "x-01", "A"), label)

    def test_the_mutants_discriminate_a_naive_aggregator(self):
        missed = [label for label, raw in _mutants() if not _naive_validate(raw, "x-01", "A")]
        self.assertGreaterEqual(len(missed), 20, missed)   # the naive shape lets these through; the strict one does not
        for needed in ("error='boom'", "case ok=1", "case ok='true'", "failing case between successes",
                       "duplicate case name", "provider='B'", "missing error"):
            self.assertIn(needed, missed)

    def test_interpret_states_and_late_chunks(self):
        reg = {"ev": "registry", "order": ["a", "b", "c", "d"], "contract_problems": [], "problems": [],
               "states": {c: ["registered", []] for c in "abcd"}, "summary": [], "labels": dict.fromkeys("abcd", "A"),
               "owners": dict.fromkeys("abcd", "A")}
        good = lambda c, calls=1: {"ev": "result", "category": c, "raw": _good(c), "json_problem": None,
                                   "exception": None, "seconds": 0.1, "calls": calls, "leftovers": []}
        res = {"events": [reg, good("a"), {"ev": "start", "step": "b"}, good("b", calls=2), {"ev": "start", "step": "c"}],
               "timed_out": {"step": "c", "kind": "step", "after_s": 2.0}, "late": ["{\"ev\": \"result\"}"],
               "killed": [123], "rc": None, "protocol_problems": [], "seconds": 2.0, "stdout_tail": "", "stderr_tail": ""}
        rec = interpret(res, lane_c=False)
        self.assertEqual([rec["categories"][c]["state"] for c in "abcd"], ["passed", "failed", "timeout", "not-called"])
        self.assertIn("exactly once", "".join(rec["categories"]["b"]["problems"]))
        self.assertTrue(any("after the deadline" in p for p in rec["problems"]))
        self.assertFalse(rec["all_pass"])
        self.assertIsNone(rec["categories"]["c"]["raw"])   # nothing filled in for a timed-out category


class IsolatedRunTests(_NoExport):
    def run_world(self, w, **kw):
        overrides = {k: w[k] for k in ("contract", "sources", "owners", "sys_path")}
        return aggregate(job_overrides=overrides, **kw)

    def test_all_pass_each_provider_called_exactly_once(self):
        w = _fake_world(self.tmp)
        counter = self.tmp / "calls"
        counter.mkdir()
        os.environ["C1FAKE_COUNTER"] = str(counter)
        rec = self.run_world(w)
        self.assertTrue(rec["all_pass"], rec["problems"])
        self.assertEqual(rec["passed"], 21)
        self.assertEqual(sorted(p.name for p in counter.iterdir()), sorted(w["order"]))
        self.assertTrue(all(len(p.read_text().split()) == 1 for p in counter.iterdir()))
        self.assertEqual(rec["hello"]["pid"], int(next(counter.iterdir()).read_text().split()[0]))   # one child

    def test_run_scope_executes_once_and_a_new_run_does_not_reuse(self):
        w = _fake_world(self.tmp)
        counter = self.tmp / "calls"
        counter.mkdir()
        os.environ["C1FAKE_COUNTER"] = str(counter)
        overrides = {k: w[k] for k in ("contract", "sources", "owners", "sys_path")}
        real = aggregate
        with unittest.mock.patch(__name__ + ".aggregate", lambda **kw: real(job_overrides=overrides, **kw)):
            with run_scope(lane_c=False) as first:
                a, b = first.ensure(), current_run().ensure()
            with run_scope(lane_c=False) as second:
                c = second.ensure()
        self.assertIs(a, b)
        self.assertEqual((first.executions, first.reads, second.executions), (1, 2, 1))
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertNotEqual(a["hello"]["pid"], c["hello"]["pid"])
        self.assertTrue(all(len(p.read_text().split()) == 2 for p in counter.iterdir()))   # once per run
        self.assertIsNone(current_run())

    def test_scope_is_bound_to_local_and_source(self):
        from . import core
        with run_scope(lane_c=False) as run:
            self.assertIs(current_run(), run)
            with unittest.mock.patch.object(core, "LOCAL", self.tmp / "other-local"):
                self.assertIsNone(current_run())
            run.pid = -1
            self.assertIsNone(current_run())

    def test_registry_structure_error_blocks_a_21_of_21(self):
        rec = self.run_world(_fake_world(self.tmp, extra=[("A", "zz-unknown")]))
        self.assertEqual(rec["passed"], 21)
        self.assertFalse(rec["all_pass"])
        self.assertTrue(any("zz-unknown" in p for p in rec["problems"]))

    def test_bad_results_fail_only_their_category(self):
        modes = {"a-01": "raise", "a-02": "none", "a-03": "list", "b-01": "error_empty", "b-02": "ok_one",
                 "c-01": "sandwich"}
        rec = self.run_world(_fake_world(self.tmp, modes))
        states = {c: rec["categories"][c]["state"] for c in modes}
        self.assertEqual(states, {"a-01": "exception", "a-02": "failed", "a-03": "failed", "b-01": "failed",
                                  "b-02": "failed", "c-01": "failed"})
        self.assertEqual(rec["passed"], 21 - len(modes))
        self.assertFalse(rec["all_pass"])
        self.assertTrue(all(rec["categories"][c]["calls"] == 1 for c in rec["order"]))

    def test_timeout_kills_the_tree_and_no_late_write_lands(self):
        late = self.tmp / "late-write"
        os.environ["C1FAKE_LATE"] = str(late)
        w = _fake_world(self.tmp, {"b-03": "late_writer"})
        t0 = time.monotonic()
        rec = self.run_world(w, step_s=lambda step: 2.0)
        self.assertLess(time.monotonic() - t0, 30)
        cats = rec["categories"]
        self.assertEqual(cats["b-03"]["state"], "timeout")
        after = w["order"][w["order"].index("b-03") + 1:]
        self.assertTrue(after and all(cats[c]["state"] == "not-called" and cats[c]["calls"] == 0 for c in after))
        self.assertTrue(all(cats[c]["raw"] is None for c in after + ["b-03"]))
        self.assertGreaterEqual(len(rec["job"]["killed"]), 2)   # the job child and its setsid grandchild
        time.sleep(4)
        self.assertFalse(late.exists())   # the grandchild was killed before its delayed write
        self.assertFalse(rec["all_pass"])

    def test_total_budget_is_enforced_too(self):
        rec = self.run_world(_fake_world(self.tmp, {"a-01": "sleep"}), total_s=3, step_s=lambda step: 600)
        self.assertEqual(rec["categories"]["a-01"]["state"], "timeout")
        self.assertIn("total", rec["problems"][0])

    def test_descendant_left_after_a_good_result_fails_the_category(self):
        pidfile = self.tmp / "leftover.pid"
        os.environ["C1FAKE_PIDFILE"] = str(pidfile)
        rec = self.run_world(_fake_world(self.tmp, {"c-02": "leftover"}))
        cat = rec["categories"]["c-02"]
        self.assertEqual(cat["state"], "failed")
        self.assertIn("left running descendants", "".join(cat["problems"]))
        pid = int(pidfile.read_text())
        time.sleep(0.5)
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        self.assertTrue(state == "" or state.startswith("Z"), state)   # gone, or a killed zombie awaiting reaping

    def test_depth_other_than_0_is_refused_before_anything_starts(self):
        for value in ("1", "2", "-1", "01", "", "x", " 0"):
            os.environ["WF_PROBE_DEPTH"] = value
            with unittest.mock.patch(__name__ + ".run_job", side_effect=AssertionError("spawned")):
                with self.assertRaises(AggregateRefused):
                    aggregate()
            with run_scope(lane_c=False) as run:
                rec = run.ensure()
            self.assertFalse(rec["all_pass"])
            self.assertEqual(rec["categories"], {})   # refused: no category is reported as run
        os.environ["WF_PROBE_DEPTH"] = "0"


class SuiteRegistrationTests(_NoExport):
    def module(self, body):
        name = "c1fakesuite_" + uuid.uuid4().hex[:10]
        (self.tmp / f"{name}.py").write_text("import os, sys, time, unittest\n" + body, encoding="utf-8")
        sys.path.insert(0, str(self.tmp))
        self.addCleanup(sys.path.remove, str(self.tmp))
        os.environ["PYTHONPATH"] = str(self.tmp)
        return name

    def register(self, target, total_s=60):
        seen = []
        counts = register_unittest_job(lambda n, c, d="": seen.append((n, c, d)) or c, "fake", [target], total_s=total_s)
        return seen, counts

    def test_clean_suite_registers_each_method_once(self):
        m = self.module("class T(unittest.TestCase):\n def test_a(self): pass\n def test_b(self): pass\n")
        seen, counts = self.register(f"{m}:T")
        self.assertEqual([(n, c) for n, c, _ in seen], [(f"{m}.T.test_a", True), (f"{m}.T.test_b", True)])
        self.assertEqual((counts["loaded"], counts["run"], counts["passed"]), (2, 2, 2))

    def test_suite_level_faults_fail_every_method(self):
        bodies = {
            "setUpClass": "class T(unittest.TestCase):\n @classmethod\n def setUpClass(cls): raise RuntimeError('x')\n"
                          " def test_a(self): pass\n",
            "tearDownClass": "class T(unittest.TestCase):\n @classmethod\n def tearDownClass(cls): raise RuntimeError('x')\n"
                             " def test_a(self): pass\n",
            "partial-exit": "class T(unittest.TestCase):\n def test_a(self): pass\n def test_b(self): os._exit(0)\n"
                            " def test_c(self): pass\n",
        }
        for label, body in bodies.items():
            seen, _ = self.register(f"{self.module(body)}:T")
            self.assertTrue(seen and all(c is False for _, c, _ in seen), (label, seen))

    def test_method_level_non_passes_fail(self):
        m = self.module("class T(unittest.TestCase):\n"
                        " def test_ok(self): pass\n"
                        " @unittest.skip('s')\n def test_skip(self): pass\n"
                        " @unittest.expectedFailure\n def test_xfail(self): raise AssertionError\n"
                        " @unittest.expectedFailure\n def test_xpass(self): pass\n"
                        " def test_fail(self): self.fail('f')\n def test_error(self): raise KeyError\n")
        seen, _ = self.register(f"{m}:T")
        self.assertEqual({n.rsplit('.', 1)[1]: c for n, c, _ in seen},
                         {"test_ok": True, "test_skip": False, "test_xfail": False, "test_xpass": False,
                          "test_fail": False, "test_error": False})

    def test_zero_methods_import_errors_and_timeout_fail_closed(self):
        empty = self.module("class T(unittest.TestCase):\n pass\n")
        seen, counts = self.register(f"{empty}:T")
        self.assertEqual((len(seen), seen[0][1], counts["loaded"]), (1, False, 0))
        seen, _ = self.register("c1fake_does_not_exist_" + uuid.uuid4().hex[:6] + ":T")
        self.assertEqual((len(seen), seen[0][1]), (1, False))
        slow = self.module("class T(unittest.TestCase):\n def test_a(self): pass\n def test_slow(self): time.sleep(60)\n")
        t0 = time.monotonic()
        seen, _ = self.register(f"{slow}:T", total_s=3)
        self.assertLess(time.monotonic() - t0, 30)
        self.assertTrue(all(c is False for _, c, _ in seen) and len(seen) == 2)
        self.assertIn("timeout", seen[0][2])

    def test_okfn_entry_records_actual_asserts_strictly(self):
        m = self.module("def entry(_ok):\n _ok('a', True)\n _ok('b', 1, 'int one')\n _ok('c', False, 'no')\n"
                        "def boom(_ok):\n _ok('a', True)\n raise RuntimeError('mid')\n"
                        "def nothing(_ok):\n pass\n")
        for target, expect, extra in ((f"{m}:entry", [("a", True), ("b", False), ("c", False)], False),
                                      (f"{m}:boom", [("a", True)], True), (f"{m}:nothing", [], True)):
            seen = []
            register_okfn_job(lambda n, c, d="": seen.append((n, c, d)) or c, "fake-ok", target, total_s=60)
            self.assertEqual([(n, c) for n, c, _ in seen[:len(expect)]], expect, target)
            self.assertEqual(len(seen), len(expect) + (1 if extra else 0), target)
            if extra:
                self.assertIs(seen[-1][1], False)
            if target.endswith(":entry"):
                self.assertIn("non-bool condition (int)", seen[1][2])


class GateSelectorTests(_NoExport):
    def test_one_selector_for_both_depths_and_the_nested_count(self):
        import inspect
        from . import selftest_v18 as v18
        d0, d1 = [n for n, _ in v18.active_groups(0)], [n for n, _ in v18.active_groups(1)]
        self.assertEqual(d0[-2:], [v18.GATE_GROUP, v18.BASELINE_GROUP])
        self.assertEqual((len(d0), len(d1)), (len(v18.TESTS) + 2, len(v18.TESTS)))
        self.assertFalse(set(d1) & v18.NESTED_EXCLUDED)
        src = inspect.getsource(v18._gate_case)
        self.assertIn("len(active_groups(1))", src)
        self.assertNotIn("len(TESTS)", src)

    def test_replaced_gate_provider_is_called_even_inside_a_run(self):
        from . import selftest_v18 as v18
        bad = {"schema": SCHEMA, "category": v18.GATE_CATEGORY, "provider": "C", "error": "boom", "cases": []}
        with run_scope(lane_c=False) as run, \
                unittest.mock.patch.object(AggregateRun, "ensure", side_effect=AssertionError("run consulted")), \
                unittest.mock.patch.dict(v18.C1_PROVIDERS, {v18.GATE_CATEGORY: lambda: bad}):
            rec = []
            v18._t_real_default_gates(lambda n, c, d="": rec.append(c))
        self.assertEqual(rec, [False])
        self.assertEqual(run.executions, 0)

    def test_invalid_depth_exits_3_before_spawning(self):
        for argv, child_flag in ((["self-check", "--probes"], "WF_PROBE_CHILD"), (["selftest"], "WF_SELFTEST_CHILD")):
            for value in ("2", "-1", "01", "", "x"):
                for flagged in (False, True):
                    env = dict(os.environ, WF_PROBE_DEPTH=value, WF_NOTIFY="0", WF_LOCAL_DIR=str(self.tmp / "local"))
                    if flagged:
                        env[child_flag] = "1"
                    r = run_bounded([sys.executable, str(WF_HOME / "wf.py"), *argv], cwd=WF_HOME, env=env, timeout=60,
                                    capture=True)
                    self.assertEqual(r["rc"], 3, (argv, value, flagged, r["stdout"][-300:]))
                    self.assertIn("未运行", r["stdout"])


# ---------------------------------------------------------------- CLI

def _acceptance_inputs():
    """Preflight of each acceptance-extension suite's inputs: (label, runnable, why, runner)."""
    def git_has(tree):
        r = subprocess.run(["git", "-C", str(WF_HOME), "cat-file", "-e", tree + "^{tree}"], capture_output=True)
        return r.returncode == 0
    from .selftest_b3_first_upgrade_recovery import CANDIDATE_TREE, OLD_TREE
    up_missing = [t for t in (OLD_TREE, CANDIDATE_TREE) if not git_has(t)]
    s06 = {k: os.environ.get(k) for k in ("B3_S06_LEGACY_V2", "B3_S06_LEGACY_B1")}
    s06_missing = [k for k, v in s06.items() if not v or not (Path(v) / ".git").exists()]
    return [
        ("B3 first-upgrade recovery (Upgrade19)", not up_missing,
         f"git objects missing in {WF_HOME}/.git: {up_missing}" if up_missing else "inputs present",
         lambda ok: register_unittest_job(ok, "Upgrade19", ["wflib.selftest_b3_first_upgrade_recovery:*"], total_s=1800)),
        ("FX-S06 behaviour (S06)", not s06_missing,
         f"set to full Git checkouts: {s06_missing}" if s06_missing else "inputs present",
         lambda ok: register_unittest_job(ok, "S06", ["wflib.selftest_b3_s06:*"], total_s=1800)),
    ]


def acceptance_main():
    """Mandatory candidate-acceptance extension: runs each suite whose inputs are present; a missing input is
    BLOCKED (exit 2), never skipped as a pass; any failure exits 1; only all-ran-and-passed exits 0."""
    results, blocked = [], []
    for label, runnable, why, runner in _acceptance_inputs():
        if not runnable:
            print(f"BLOCKED {label}：{why}（未运行，不计通过）", flush=True)
            blocked.append(label)
            continue
        seen = []

        def _ok(n, c, d=""):
            seen.append((n, c is True))
            print(f"{'✓' if c is True else '✗'} {n}" + ("" if c is True else f"\n    {str(d)[:1500]}"), flush=True)
            return c
        counts = runner(_ok)
        good = bool(seen) and all(c for _, c in seen)
        results.append((label, good, counts))
        print(f"{label}：{sum(c for _, c in seen)} / {len(seen)} 通过", flush=True)
    if blocked:
        print(f"验收扩展：BLOCKED（缺输入 {blocked}）", flush=True)
        return 2
    return 0 if results and all(g for _, g, _ in results) else 1


def main(argv):
    if argv[:1] == ["--job"]:
        return _job_main(argv[1])
    if argv[:1] == ["--acceptance"]:
        return acceptance_main()
    if argv[:1] == ["--strict21"]:
        with run_scope(lane_c="--lane-c" in argv) as run:
            rec = run.ensure()
        for line in summary_lines(rec):
            print(line, flush=True)
        print(f"C1 严格聚合：{rec['passed']} / {REQUIRED_COUNT} 通过；总判定 {'通过' if rec['all_pass'] else '未通过'}", flush=True)
        return 0 if rec["all_pass"] else 1
    return unittest.main(module=__name__, argv=[sys.argv[0]] + argv, exit=False).result.wasSuccessful() is False


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
