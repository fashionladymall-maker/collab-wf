"""FX-TX: reusable behavioural fixtures for complete install transactions (synthetic, never production).

``chain(env, *, kind, outcome, with_receipt)`` drives the real release writer
(``release.apply_zip`` for projects, ``release.self_update`` for the tool) in an
isolated worker process and returns a structured description of the files it
actually produced. Nothing here writes JSON records by hand, re-seals, deletes
records or edits state: outcomes come from real transactions with one declared
fault hook each.

Env protocol (``make_env``): the caller creates a fresh, empty base directory
and runs this module in a process whose ``WF_LOCAL_DIR`` is ``<base>/local``.
The env is never discovered from or completed with the caller's real LOCAL,
registered projects or tool install. It consists of
  base, local                 the isolated host
  host                        selftest_review.Env: synthetic review runner and
                              verifier (verifier pinned, synthetic=true), lanes
                              and transport in ``local``; its tool repo carries
                              the review rules for project installs
  python                      interpreter used for every worker (sha256 recorded)
and is valid only while ``core.LOCAL`` still equals ``local``. The dependency on
the test module ``selftest_review`` is pinned (source, signatures, templates);
drift refuses before anything is written and needs a re-review.

Isolation is in-process only, not an OS sandbox. Every worker installs a
subprocess gate before importing wflib: ``subprocess.Popen`` resolves the
program that would actually run (``executable`` if given, otherwise ``argv[0]``
through the child's PATH) to a real path and accepts only the pinned git, the
synthetic runner/verifier and the worker interpreter; ``argv[0]`` must resolve
to the same program; the working directory and the HOME/TMPDIR/WF_LOCAL_DIR/
GIT_CONFIG_GLOBAL of the child must lie inside the case or the env's host
roots; ``shell``, ``preexec_fn``, ``pass_fds`` and ``close_fds=False`` are
refused, and the install exec context/receipt FD variables are removed from
every child environment. An audit hook refuses ``os.system``/``exec``/
``spawn``/``posix_spawn``/``fork`` and any ``subprocess.Popen`` event that did
not pass the gate. ``audit_probe`` runs the gate against real bypass attempts.

A ``recovered`` chain is only described after the parent independently checks
the recovered target against the transaction's baseline (every inventory row's
type/mode/size/sha, control rows, product inventory digest, ``HEAD^{tree}``
equal to the baseline tree, clean worktree); a mismatch raises FixtureError
with the observed files so consumers can be shown failing closed.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

SCHEMA = "install-fixture-chain/1"
ENV_SCHEMA = "install-fixture-env/1"
KINDS = ("project", "tool")
OUTCOMES = ("completed", "failed", "partial", "recovered")

# Reviewed against parent 6f8fa4d9d6eeb83b3016d5529a9882e24fbf187e (tree 1c2c6931…):
# wflib/selftest_review.py Env/_zip/_git and the RUNNER/VERIFIER templates.
ENV_CONTRACT = {
    "module": "wflib.selftest_review",
    "source_sha256": {
        "Env": "95cb27ce03c2787d57ac136b9f9ab84cbcd0260d2dff1e1443d6c40e8bbdf09f",
        "_zip": "38abd54fd4e80619c80bc0cd3430abab74560437e90eaecfae4414ef72dd9830",
        "_git": "6579f01f3ccc2c0356e5288261d6b3cc48d2e07f47dc69e8126de57f18a2a712",
        "RUNNER": "8835899984e750ae40a7655cc4acae5bf0fb308e7c267e0720a2777e55395cbb",
        "VERIFIER": "65e5eb9f9dda8c86bf628181a228b765729f5a55c89710613969a7b3a8799250",
    },
    "signatures": {
        "Env.__init__": "(self, base: 'Path')",
        "Env.pkg": "(self, name: 'str', files=None, **kw) -> 'Path'",
        "Env.behave": "(self, **b)",
        "Env.vbehave": "(self, **b)",
        "Env.set_transport": "(self, verifier=True, pinned=None, **extra)",
        "_zip": "(path: 'Path', top: 'str', files: 'dict', raw_infos=()) -> 'Path'",
    },
}
PRODUCT_SOURCES = ("release.py", "review.py", "install_history.py", "ops.py", "live.py", "live_history.py", "core.py")


class FixtureRefused(Exception):
    """A precondition does not hold; the refusing step wrote nothing."""


class FixtureError(Exception):
    """A real transaction did not produce the requested outcome; nothing is relabelled.
    ``observed`` carries the actual case/target/store/tx when a transaction exists."""

    def __init__(self, message, observed=None):
        super().__init__(message)
        self.observed = observed


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def env_contract_problems() -> list:
    from . import selftest_review as sr
    got = {"Env": inspect.getsource(sr.Env), "_zip": inspect.getsource(sr._zip), "_git": inspect.getsource(sr._git),
           "RUNNER": sr.RUNNER, "VERIFIER": sr.VERIFIER}
    problems = [f"changed {sr.__name__}:{k}" for k, v in got.items()
                if _sha(v.encode()) != ENV_CONTRACT["source_sha256"][k]]
    sigs = {"Env.__init__": sr.Env.__init__, "Env.pkg": sr.Env.pkg, "Env.behave": sr.Env.behave,
            "Env.vbehave": sr.Env.vbehave, "Env.set_transport": sr.Env.set_transport, "_zip": sr._zip}
    problems += [f"signature {k}" for k, fn in sigs.items() if str(inspect.signature(fn)) != ENV_CONTRACT["signatures"][k]]
    return problems


class IsolatedEnv:
    def __init__(self, base: Path, local: Path, host, python: str):
        self.schema, self.base, self.local, self.host, self.python = ENV_SCHEMA, base, local, host, python
        self.cases = base / "cases"
        self.counter = 0

    def describe(self) -> dict:
        return {"schema": self.schema, "base": str(self.base), "local": str(self.local), "review_tool": str(self.host.tool),
                "runner": str(self.host.runner), "verifier": str(self.host.verifier), "python": self.python,
                "python_sha256": _sha(Path(self.python).read_bytes())}


def make_env(base, *, python=None) -> IsolatedEnv:
    from . import core
    base = Path(base)
    if not base.is_absolute() or os.path.realpath(base) != str(base):
        raise FixtureRefused("env base must be an absolute, resolved path")
    local = base / "local"
    if Path(core.LOCAL) != local or os.environ.get("WF_LOCAL_DIR") != str(local):
        raise FixtureRefused(f"WF_LOCAL_DIR must already be {local} in this process (env is never taken from the real LOCAL)")
    if not base.is_dir() or any(p.name != "local" for p in base.iterdir()) or (local.exists() and any(local.iterdir())):
        raise FixtureRefused("env base must be empty apart from an empty local/")
    for name in ("WF_INSTALL_EXEC_CONTEXT", "WF_INSTALL_RECEIPT_FD"):
        if name in os.environ:
            raise FixtureRefused(f"inherited {name} must be removed before building fixtures")
    problems = env_contract_problems()
    if problems:
        raise FixtureRefused("install fixture dependency drift: " + "; ".join(problems)
                             + ". Re-review the dependency, then update ENV_CONTRACT.")
    from .selftest_review import Env
    local.mkdir(exist_ok=True)
    host = Env(base / "review-host")
    cfg = json.loads(host.transport.read_bytes())
    v = cfg.get("review_verifier") or {}
    if v.get("synthetic") is not True or v.get("sha256") != _sha(host.verifier.read_bytes()) or v.get("path") != str(host.verifier):
        raise FixtureRefused("synthetic review host did not configure a pinned synthetic verifier")
    py = python or sys.executable
    return IsolatedEnv(base, local, host, os.path.realpath(py))


# ---------------------------------------------------------------- worker

GUARD = r'''
import inspect, json, os, shutil, subprocess, sys, threading
from pathlib import Path
spec = json.loads(sys.argv[1])
ROOTS = [os.path.realpath(p) for p in spec["write_roots"]]
ALLOWED = {os.path.realpath(p) for p in spec["allowed_exec"]} | {os.path.realpath(spec["git"])}
FORBIDDEN_ENV = ("WF_INSTALL_EXEC_CONTEXT", "WF_INSTALL_RECEIPT_FD")
PATH_ENV = ("HOME", "TMPDIR", "WF_LOCAL_DIR", "GIT_CONFIG_GLOBAL")
spawned, refused, _gate = [], [], threading.local()

def _inside(path):
    path = os.path.realpath(os.fsdecode(path))
    return any(path == r or path.startswith(r + os.sep) for r in ROOTS)

def _resolve(program, env, cwd):
    """Resolve using the directory in which POSIX exec will actually run."""
    program = os.fsdecode(os.fspath(program))
    if os.sep not in program:
        # Relative and empty PATH entries are relative to the child's cwd, not
        # the worker's cwd. Preserve symlink/.. semantics until realpath.
        path = os.pathsep.join(os.path.realpath(os.path.join(cwd, part))
                               for part in env.get("PATH", os.defpath).split(os.pathsep))
        program = shutil.which(program, path=path)
        if program is None:
            return None
    return os.path.realpath(os.path.join(cwd, program))

class GuardRefused(RuntimeError):
    pass

_Popen = subprocess.Popen
_sig = inspect.signature(_Popen.__init__)
_init = _Popen.__init__

def _gated_init(self, *args, **kwargs):
    b = _sig.bind(self, *args, **kwargs)
    b.apply_defaults()
    a = b.arguments
    try:
        argv = [a["args"]] if isinstance(a["args"], (str, bytes, os.PathLike)) else list(a["args"])
        argv = [os.fsdecode(os.fspath(x)) for x in argv]
        if not argv or not argv[0] or any("\0" in x for x in argv):
            raise ValueError("empty argv/program or NUL")
        env = a["env"]
        child = {os.fsdecode(k): os.fsdecode(v) for k, v in
                 (env if env is not None else os.environ).items()}
        raw_cwd = os.fsdecode(os.fspath(a["cwd"])) if a["cwd"] is not None else os.getcwd()
        cwd = os.path.realpath(os.path.join(os.getcwd(), raw_cwd))
        program = _resolve(a["executable"] if a["executable"] is not None else argv[0], child, cwd)
        first = _resolve(argv[0], child, cwd)
    except (TypeError, ValueError, AttributeError) as exc:
        raise GuardRefused("fx-tx worker refused invalid process paths/environment: " + str(exc)) from exc
    problems = []
    if a["shell"]:
        problems.append("shell=True")
    if program is None or program not in ALLOWED:
        problems.append("executable not allowed: %s" % program)
    if a["executable"] is not None and first != program:
        problems.append("argv[0] %s does not name executable %s" % (first, program))
    if not _inside(cwd):
        problems.append("cwd outside the case: " + cwd)
    if a["preexec_fn"] is not None:
        problems.append("preexec_fn")
    if a["pass_fds"] or a["close_fds"] is not True:
        problems.append("inherited fds: pass_fds=%r close_fds=%r" % (tuple(a["pass_fds"]), a["close_fds"]))
    for key in PATH_ENV:
        if key in child and not _inside(os.path.join(cwd, child[key])):
            problems.append("%s outside the case: %s" % (key, child[key]))
    stripped = sorted(k for k in FORBIDDEN_ENV if k in child)
    record = {"argv": argv[:6], "executable": program, "cwd": cwd, "env": "explicit" if env is not None else "inherited",
              "stripped": stripped}
    if problems:
        refused.append(dict(record, problems=problems))
        raise GuardRefused("fx-tx worker refused subprocess: " + "; ".join(problems))
    for key in FORBIDDEN_ENV:
        child.pop(key, None)
    a["env"] = child
    # Pass the exact paths that were checked to Popen. In particular, leaving
    # argv[0] relative would make Popen resolve it a second time after chdir.
    a["args"], a["cwd"], a["executable"] = argv, cwd, program
    spawned.append(record)
    _gate.ok = (program, tuple(argv))
    try:
        b.arguments.pop("self", None)
        _init(self, *b.args[1:], **b.kwargs)
    finally:
        _gate.ok = None

subprocess.Popen.__init__ = _gated_init
_BLOCKED = ("os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork", "os.forkpty", "pty.spawn")

def audit(event, args):
    approved = getattr(_gate, "ok", None)
    if event == "subprocess.Popen":
        argv = args[1] if isinstance(args[1], (list, tuple)) else [args[1]]
        argv = tuple(os.fsdecode(x) if isinstance(x, bytes) else str(x) for x in argv)
        if approved is None or approved[1] != argv or os.fsdecode(args[0]) != approved[0]:
            raise GuardRefused("fx-tx worker: subprocess.Popen outside the gate")
    elif event in _BLOCKED and approved is None:
        raise GuardRefused("fx-tx worker: %s refused" % event)
sys.addaudithook(audit)
'''

PROBE = r'''
import os, subprocess
probe = spec["probe"]
results = {}
def attempt(name, fn):
    try:
        fn()
        results[name] = "accepted"
    except GuardRefused as e:
        results[name] = "refused: " + str(e)[:200]
    except Exception as e:
        results[name] = "error: %s: %s" % (type(e).__name__, str(e)[:200])
fake_bin = probe["fake_bin"]
attempt("executable override (argv[0]=git, executable=probe)",
        lambda: subprocess.run(["git", "x"], executable=probe["exec"], timeout=10))
attempt("git resolved to a fake binary through PATH",
        lambda: subprocess.run(["git", "--version"], env=dict(os.environ, PATH=fake_bin + os.pathsep + os.environ["PATH"]), timeout=10))
attempt("absolute path to a non-allowed program", lambda: subprocess.run([probe["exec"]], timeout=10))
attempt("cwd outside the case", lambda: subprocess.run(["git", "--version"], cwd=probe["outside"], timeout=10))
attempt("HOME outside the case", lambda: subprocess.run(["git", "--version"], env=dict(os.environ, HOME=probe["outside"]), timeout=10))
fd = os.open(probe["fd_file"], os.O_WRONLY | os.O_CREAT, 0o600)
attempt("pass_fds", lambda: subprocess.run(["git", "--version"], pass_fds=(fd,), timeout=10))
attempt("close_fds=False", lambda: subprocess.run(["git", "--version"], close_fds=False, timeout=10))
os.close(fd)
attempt("shell=True", lambda: subprocess.run("git --version", shell=True, timeout=10))
attempt("preexec_fn", lambda: subprocess.run(["git", "--version"], preexec_fn=lambda: None, timeout=10))
attempt("os.system", lambda: os.system(probe["exec"]))
attempt("os.posix_spawn", lambda: os.waitpid(os.posix_spawn(probe["exec"], [probe["exec"]], dict(os.environ)), 0))
attempt("ungated original Popen.__init__ (audit backstop)",
        lambda: _init(subprocess.Popen.__new__(subprocess.Popen), [probe["exec"]]))
SEE = "import os;print('WF_INSTALL_EXEC_CONTEXT' in os.environ or 'WF_INSTALL_RECEIPT_FD' in os.environ)"
os.environ["WF_INSTALL_EXEC_CONTEXT"] = "{}"
os.environ["WF_INSTALL_RECEIPT_FD"] = "3"
control = subprocess.run([sys.executable, "-S", "-c", SEE], capture_output=True, text=True, timeout=30)
results["control: allowed interpreter, inherited env sanitised"] = "accepted rc=%s child_sees_context=%s" % (
    control.returncode, control.stdout.strip())
control = subprocess.run([sys.executable, "-S", "-c", SEE], env=dict(os.environ), capture_output=True, text=True, timeout=30)
results["control: allowed interpreter, explicit env sanitised"] = "accepted rc=%s child_sees_context=%s" % (
    control.returncode, control.stdout.strip())
os.environ.pop("WF_INSTALL_EXEC_CONTEXT")
os.environ.pop("WF_INSTALL_RECEIPT_FD")
control = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=10)
results["control: real git inside the case"] = "accepted rc=%s %s" % (control.returncode, control.stdout.strip()[:40])
print("FXTX-PROBE " + json.dumps({"results": results, "spawned": spawned, "refused": refused}), flush=True)
'''

WORKER = GUARD + r'''
from wflib import core, release, review, server
if str(core.LOCAL) != spec["local"]:
    raise SystemExit("fx-tx worker: LOCAL mismatch")
tool = Path(spec["tool"])
review._tool_home = lambda: tool
if spec["kind"] == "tool":
    core.WF_HOME = release.WF_HOME = tool
    server.info = lambda: {}
    release._restart_board = lambda: False   # stand-in: no service restart (synthetic)
notes = []
behavior = Path(spec["behavior"])
if spec["outcome"] == "failed":
    original = release._InstallTx.__init__
    def prepared_then_block(tx, *a, **k):
        original(tx, *a, **k)
        notes.append({"hook": "release._InstallTx.__init__", "stage": "prepared written, before consume",
                      "action": "real review.run registers a block for the same request", "tx": tx.tx_id})
        saved = behavior.read_bytes()
        behavior.write_text(json.dumps({"verdict": "block"}))
        try:
            notes[-1]["block_decision"] = review.run(spec["review_id"]).get("verdict")
        finally:
            behavior.write_bytes(saved)
    release._InstallTx.__init__ = prepared_then_block
if spec["outcome"] in ("partial", "recovered"):
    def complete_fault(tx, actual):
        notes.append({"hook": "release._InstallTx.complete", "stage": "after consume/started, target write and commit",
                      "action": "raise before the completed record", "tx": tx.tx_id})
        raise core.WfError("fx-tx injected fault before completed record")
    release._InstallTx.complete = complete_fault
if spec["outcome"] == "partial":
    def recover_fault(lock, path):
        notes.append({"hook": "release._recover_tx", "stage": "automatic recovery after the partial record",
                      "action": "raise; recovery left open", "tx": Path(path).name})
        raise core.WfError("fx-tx injected recovery fault")
    release._recover_tx = recover_fault
if spec.get("inject") == "tamper_after_recover":
    _real_recover = release._recover_tx
    def tamper_after_return(lock, path):
        result = _real_recover(lock, path)
        target = Path(lock.root) / "PROJECT.md"
        target.write_bytes(target.read_bytes() + b"\n<!-- fx-tx: rewritten at the recovery return boundary -->\n")
        notes.append({"hook": "release._recover_tx (return)", "stage": "after the real recovery returned",
                      "action": "negative test: rewrite PROJECT.md", "tx": Path(path).name})
        return result
    release._recover_tx = tamper_after_return
out = {"notes": notes, "modules": {m: sys.modules["wflib." + m].__file__ for m in ("core", "release", "review")},
       "exec_context_env": os.environ.get("WF_INSTALL_EXEC_CONTEXT") is not None}
try:
    if spec["kind"] == "project":
        r = release.apply_zip(core.Instance(Path(spec["project"])), review_id=spec["review_id"], by="driver")
    else:
        r = release.self_update(review_id=spec["review_id"], skip_test=True)
    out.update(rc=0, result={k: r.get(k) for k in ("install_tx", "install_ref", "version", "to", "commit")})
except core.WfError as e:
    out.update(rc=1, error=str(e)[:800])
out["spawned"] = spawned
out["refused"] = refused
print("FXTX-RESULT " + json.dumps(out, default=str), flush=True)
sys.exit(out["rc"])
'''


def _worker_env(env: IsolatedEnv, case: Path) -> dict:
    bind = case / "bin"
    bind.mkdir(exist_ok=True)
    if not (bind / "python3").exists():
        os.symlink(env.python, bind / "python3")
    (case / "home").mkdir(exist_ok=True)
    (case / "tmp").mkdir(mode=0o700, exist_ok=True)
    gitcfg = case / "home" / ".gitconfig"
    if not gitcfg.exists():
        gitcfg.write_text("[user]\n\tname = fx-tx\n\temail = fx-tx@localhost\n", encoding="utf-8")
    return {"PATH": f"{bind}{os.pathsep}/usr/bin{os.pathsep}/bin", "HOME": str(case / "home"), "TMPDIR": str(case / "tmp"),
            "WF_LOCAL_DIR": str(env.local), "WF_NOTIFY": "0", "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(gitcfg), "GIT_TERMINAL_PROMPT": "0"}


def _records(store: Path, before: set) -> list:
    out = []
    for tx in sorted(p for p in store.glob("tx-*") if p.name not in before) if store.exists() else []:
        out.append({"tx": tx.name, "path": str(tx),
                    "records": {p.name: _sha(p.read_bytes()) for p in sorted(tx.glob("*.json"))}})
    return out


def _classify(rec: dict) -> str:
    names = set(rec["records"])
    if "recovered.json" in names:
        return "recovered"
    if "partial.json" in names:
        return "partial"
    if "failed.json" in names:
        return "failed"
    if "completed.json" in names:
        return "completed"
    return "open"


def _tree(root: Path) -> dict:
    out = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in (".git",) and not (Path(dp) == root / "workflow" / "evidence" and d == "install")
                  and not (Path(dp) == root / "local" and d == "install")]
        for n in fns:
            p = Path(dp) / n
            if not p.is_symlink():
                out[str(p.relative_to(root))] = _sha(p.read_bytes())
    return out


def _git(root: Path, *args) -> str:
    from .selftest_review import _git as g
    return g(root, *args)


def _git_path(wenv: dict) -> str:
    found = shutil.which("git", path=wenv["PATH"])
    if found is None:
        raise FixtureRefused("git not found on the worker PATH")
    return os.path.realpath(found)


def _write_roots(env: IsolatedEnv, case: Path) -> list:
    return [str(case), str(env.local), str(env.host.base)]


def _snapshot(paths, skip=()) -> dict:
    """(type, mode, size, sha256) for every path under ``paths`` except the ``skip`` subtrees."""
    out = {}
    skip = [os.path.realpath(s) for s in skip]
    for top in paths:
        for dp, dns, fns in os.walk(top):
            dns[:] = [d for d in dns if os.path.realpath(os.path.join(dp, d)) not in skip]
            for n in dns + fns:
                p = os.path.join(dp, n)
                st = os.lstat(p)
                body = _sha(open(p, "rb").read()) if os.path.isfile(p) and not os.path.islink(p) else \
                    (os.readlink(p) if os.path.islink(p) else None)
                out[p] = (st.st_mode, st.st_size if not os.path.isdir(p) else None, body)
    return out


def audit_probe(env, *, outside) -> dict:
    """Run the worker subprocess gate against real bypass attempts in its own case. ``outside`` is a directory
    outside the env (a canary); nothing may appear there or anywhere in the env outside the probe case."""
    if not isinstance(env, IsolatedEnv) or env.schema != ENV_SCHEMA:
        raise FixtureRefused("env must come from install_fixtures.make_env")
    env.counter += 1
    case = env.cases / f"{env.counter:02d}-audit-probe"
    case.mkdir(parents=True)
    wenv = _worker_env(env, case)
    outside = Path(outside)
    marker_dir = outside / "markers"
    marker_dir.mkdir(parents=True, exist_ok=True)
    probe_exec = outside / "probe-exec.sh"   # would leave a marker outside the case if it ever ran
    probe_exec.write_text(f"#!/bin/sh\necho ran > {marker_dir}/ran-$$\n", encoding="utf-8")
    probe_exec.chmod(0o755)
    fake_bin = case / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "git").write_text(f"#!/bin/sh\necho ran > {marker_dir}/fake-git-$$\n", encoding="utf-8")
    (fake_bin / "git").chmod(0o755)
    spec = {"write_roots": _write_roots(env, case), "git": _git_path(wenv),
            "allowed_exec": [str(env.host.runner), str(env.host.verifier), env.python, sys.executable],
            "probe": {"exec": str(probe_exec), "fake_bin": str(fake_bin), "outside": str(outside),
                      "fd_file": str(case / "fd-probe")}}
    watched = [str(env.base), str(outside)]
    before = _snapshot(watched, skip=[case])
    argv = [env.python, "-S", "-c", GUARD + PROBE, json.dumps(spec)]
    r = subprocess.run(argv, cwd=str(case), env=wenv, capture_output=True, text=True, timeout=120, close_fds=True)
    after = _snapshot(watched, skip=[case])
    line = next((x for x in r.stdout.splitlines() if x.startswith("FXTX-PROBE ")), None)
    if line is None:
        raise FixtureError(f"audit probe produced no result (rc={r.returncode}): {(r.stdout + r.stderr)[-1500:]}")
    got = json.loads(line[len("FXTX-PROBE "):])
    changed = sorted(set(before) ^ set(after) | {p for p in before if p in after and before[p] != after[p]})
    return {"case": str(case), "rc": r.returncode, "results": got["results"], "spawned": got["spawned"],
            "refused": got["refused"], "markers": sorted(p.name for p in marker_dir.iterdir()),
            "changed_outside_case": changed, "argv": argv[:3] + ["<GUARD+PROBE>", "<spec>"], "cwd": str(case),
            "env_keys": sorted(wenv), "write_roots": spec["write_roots"], "git": spec["git"]}


def verify_recovered(target, kind, store, tx) -> list:
    """Independent check that a recovered target equals its transaction baseline. Returns problems ([] = ok).
    Sources: the baseline's own rows read from disk here (lstat + bytes), git, and the product inventory digest."""
    from . import release, review
    target, path = Path(target), Path(store) / tx
    problems = []
    try:
        baseline = json.loads((path / "baseline.json").read_bytes())
        recovered = json.loads((path / "recovered.json").read_bytes())
    except (OSError, ValueError) as e:
        return [f"records unreadable: {type(e).__name__}"]

    def row_problem(row):
        p = target / row["path"]
        if row.get("type") == "absent":
            return f"unexpected {row['path']}" if os.path.lexists(p) else None
        if not os.path.lexists(p):
            return f"missing {row['path']}"
        if row.get("type") == "approvals":   # a projection; compared by the product control set below
            return None if os.path.isfile(p) and not os.path.islink(p) else f"type {row['path']}"
        st = os.lstat(p)
        kind_now = "symlink" if os.path.islink(p) else "dir" if os.path.isdir(p) else "file" if os.path.isfile(p) else "other"
        if kind_now != row.get("type"):
            return f"type {row['path']}: {kind_now} != {row.get('type')}"
        if row.get("mode") is not None and (st.st_mode & 0o7777) != row["mode"]:
            return f"mode {row['path']}: {oct(st.st_mode & 0o7777)} != {oct(row['mode'])}"
        if kind_now == "file":
            data = p.read_bytes()
            if row.get("sha256") != _sha(data) or (row.get("size") is not None and row["size"] != len(data)):
                return f"content {row['path']}"
        if kind_now == "symlink" and row.get("target") is not None and os.readlink(p) != row["target"]:
            return f"link {row['path']}"
        return None
    problems += [x for x in (row_problem(r) for r in baseline.get("paths", [])) if x]
    problems += ["control " + x for x in (row_problem(r) for r in baseline.get("control", [])) if x]
    try:
        tree_now = _git(target, "rev-parse", "HEAD^{tree}")
        head_now = _git(target, "rev-parse", "HEAD")
        status = _git(target, "status", "--porcelain", "--untracked-files=all")
    except Exception as e:   # noqa: BLE001 — any git failure is a failed verification
        return problems + [f"git: {type(e).__name__}"]
    if tree_now != baseline.get("identity", {}).get("tree"):
        problems.append(f"HEAD tree {tree_now} != baseline tree {baseline.get('identity', {}).get('tree')}")
    if head_now != (recovered.get("actual") or {}).get("commit"):
        problems.append("HEAD is not the recovered commit")
    if status.strip():
        problems.append("worktree not clean: " + " | ".join(status.splitlines()[:5]))
    try:
        digest = release._digest(release._inventory(target, kind, baseline["target"]["protect"]))
        if digest != baseline.get("inventory_sha256") or digest != recovered.get("inventory_sha256"):
            problems.append("product inventory digest differs from baseline/recovered")
        if review._control(review._target(target, kind)) != baseline.get("control"):
            problems.append("product control set differs from baseline")
    except Exception as e:   # noqa: BLE001
        problems.append(f"product inventory unavailable: {type(e).__name__}: {str(e)[:120]}")
    return problems


def _source_identity() -> dict:
    tree = Path(__file__).resolve().parent.parent
    files = {n: _sha((tree / "wflib" / n).read_bytes()) for n in PRODUCT_SOURCES}
    return {"tree": str(tree), "wflib_sha256": files, "fixture_sha256": _sha(Path(__file__).read_bytes())}


def chain(env, *, kind, outcome, with_receipt, before_install=None, _inject=None) -> dict:
    """One real install transaction in its own case directory. ``before_install(inst)`` (optional) runs after the
    target exists and before the install; CL-CT5 uses it to freeze a zero-exec window first. ``_inject`` is for
    negative tests only (``"tamper_after_recover"``: rewrite PROJECT.md after the real recovery returns); such a
    chain never yields a description."""
    from . import admin, core, ops, review
    from .selftest import _project_md
    from .selftest_review import _zip
    if not isinstance(env, IsolatedEnv) or env.schema != ENV_SCHEMA:
        raise FixtureRefused("env must come from install_fixtures.make_env")
    if kind not in KINDS or outcome not in OUTCOMES or type(with_receipt) is not bool:
        raise FixtureRefused("kind/outcome must be known and with_receipt a real bool")
    if _inject not in (None, "tamper_after_recover") or (_inject and (kind, outcome) != ("project", "recovered")):
        raise FixtureRefused("unknown or inapplicable injection")
    if Path(core.LOCAL) != env.local or os.environ.get("WF_LOCAL_DIR") != str(env.local):
        raise FixtureRefused("this process no longer uses the env's isolated LOCAL")
    if any(k in os.environ for k in ("WF_INSTALL_EXEC_CONTEXT", "WF_INSTALL_RECEIPT_FD")):
        raise FixtureRefused("inherited install exec/receipt environment must be removed")
    problems = env_contract_problems()
    if problems:
        raise FixtureRefused("install fixture dependency drift: " + "; ".join(problems))
    env.counter += 1
    case = env.cases / f"{env.counter:02d}-{kind}-{outcome}-{'receipt' if with_receipt else 'no-receipt'}"
    case.mkdir(parents=True)
    wenv = _worker_env(env, case)
    saved_home = review._tool_home
    try:
        if kind == "project":
            inst = admin.init_project(case / "project", name=f"fx-{case.name}-{os.getpid()}")
            for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "initial")):
                _git(inst.root, *args)
            target, store, tool = inst.root, inst.root / "workflow" / "evidence" / "install", env.host.tool
            files = {"PROJECT.md": _project_md("v0.2").encode(),
                     "CHANGELOG.md": "# CHANGELOG\n\n## v0.2 - fx-tx\n- installed\n".encode(),
                     "app.py": f"v0.2 {case.name}\n".encode()}
            subject = inst
        else:
            tool = case / "tool"
            (tool / "docs").mkdir(parents=True)
            base_files = {"VERSION": b"9.9.9\n", "docs/REVIEW.md": (env.host.tool / "docs" / "REVIEW.md").read_bytes()}
            for rel, data in base_files.items():
                (tool / rel).write_bytes(data)
            manifest = {"version": "9.9.9", "files": {p: _sha(d) for p, d in base_files.items()}}
            (tool / "MANIFEST.json").write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")
            _git(tool, "init", "-q")
            (tool / ".git" / "info").mkdir(parents=True, exist_ok=True)   # same as the C5 fixture: local/ is ignored
            with open(tool / ".git" / "info" / "exclude", "a", encoding="utf-8") as f:
                f.write("\n/local/\n")
            for args in (("add", "-A"), ("commit", "-qm", "tool 9.9.9")):
                _git(tool, *args)
            target, store = tool, tool / "local" / "install"
            files = {"VERSION": b"9.9.10\n", "docs/REVIEW.md": base_files["docs/REVIEW.md"]}
            files["MANIFEST.json"] = json.dumps({"version": "9.9.10", "files": {p: _sha(d) for p, d in files.items()}},
                                                sort_keys=True, separators=(",", ":")).encode()
            subject = admin.init_project(case / "subject", name=f"fx-{case.name}-subject-{os.getpid()}") if with_receipt else None
            if subject is not None:
                for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "initial")):
                    _git(subject.root, *args)
        review._tool_home = lambda: tool
        if before_install is not None:
            before_install(subject if subject is not None else None)
        pkg = _zip(case / "package.zip", "p", files)
        req = review.prepare(target if kind == "tool" else inst, pkg, kind=kind, author="B")
        decision = review.run(req["request_id"])
        if not decision["allowed"] or decision["provenance"]["synthetic"] is not True:
            raise FixtureError("synthetic review did not allow the fixture package: " + "；".join(decision["reasons"]))
        events_path = subject.events_path if subject is not None else None
        events_before = events_path.read_bytes() if events_path is not None and events_path.exists() else None
        tree_before, before = _tree(target), {p.name for p in store.glob("tx-*")} if store.exists() else set()
        spec = {"kind": kind, "outcome": outcome, "review_id": req["request_id"], "local": str(env.local),
                "tool": str(tool), "behavior": str(env.host.bin / "behavior.json"),
                "project": str(target) if kind == "project" else None, "inject": _inject,
                "write_roots": _write_roots(env, case), "git": _git_path(wenv),
                "allowed_exec": [str(env.host.runner), str(env.host.verifier), env.python, sys.executable]}
        argv = [env.python, "-S", "-c", WORKER, json.dumps(spec)]
        exec_info, receipt_info = None, None
        if with_receipt:
            subject.state.update(state="EXECUTE", turn=subject.state.get("turn") or 1)
            subject.save()
            saved_env = dict(os.environ)
            os.environ.clear()
            os.environ.update(wenv)
            try:
                rc = ops.run_exec(subject, shlex.join(argv), label="fx-tx", by="driver")
            finally:
                os.environ.clear()
                os.environ.update(saved_env)
            meta_path = max(subject.turn_dir().glob("c[0-9]*.json"), key=lambda p: int(p.stem[1:]))
            meta = json.loads(meta_path.read_bytes())
            out_text = (meta_path.parent / f"{meta['id']}.out").read_text(encoding="utf-8", errors="replace")
            rpath = meta_path.parent / f"{meta['id']}.install.jsonl"
            rows = [json.loads(x) for x in rpath.read_text(encoding="utf-8").splitlines() if x.strip()]
            ev = [e for e in core.read_jsonl(subject.events_path) if e.get("type") == "exec" and e.get("cid") == meta["id"]]
            exec_info = {"cid": meta["id"], "metadata": str(meta_path), "metadata_sha256": _sha(meta_path.read_bytes()),
                         "invocation": meta["install_exec"], "exit_code": meta["exit_code"], "event": ev[-1] if ev else None,
                         "install_refs": meta["install_refs"], "install_receipt_error": meta["install_receipt_error"]}
            receipt_info = {"path": str(rpath), "sha256": _sha(rpath.read_bytes()), "rows": rows}
            launch = {"via": "ops.run_exec", "cmd_argv": argv[:3] + ["<WORKER>", "<spec>"], "cwd": str(subject.root),
                      "inherited_fds": "receipt fd from ops.run_exec only (pass_fds)"}
        else:
            r = subprocess.run(argv, cwd=str(case), env=wenv, capture_output=True, text=True, timeout=300, close_fds=True)
            rc, out_text = r.returncode, r.stdout + r.stderr
            launch = {"via": "subprocess (direct release call, no ops, no exec context/FD)",
                      "cmd_argv": argv[:3] + ["<WORKER>", "<spec>"], "cwd": str(case), "inherited_fds": "none (close_fds=True)"}
        line = next((x for x in out_text.splitlines() if x.startswith("FXTX-RESULT ")), None)
        if line is None:
            raise FixtureError(f"worker produced no result (rc={rc}): {out_text[-1500:]}")
        worker = json.loads(line[len("FXTX-RESULT "):])
        recs = _records(store, before)
        if len(recs) != 1:
            raise FixtureError(f"expected exactly one new transaction, found {len(recs)}")
        actual = _classify(recs[0])
        if actual != outcome:
            raise FixtureError(f"real transaction ended as {actual}, not {outcome}: {worker.get('error')}")
        tx = recs[0]["tx"]
        observed = {"case": str(case), "kind": kind, "outcome": outcome, "with_receipt": with_receipt,
                    "target": str(target), "store": str(store), "tool_root": str(tool), "tx": tx,
                    "records": recs[0]["records"], "subject": str(subject.root) if subject is not None else None,
                    "fault": worker.get("notes"), "exec": exec_info, "receipt": receipt_info,
                    "worker_refused": worker.get("refused")}
        if worker.get("refused"):
            raise FixtureError("worker subprocess gate refused a spawn", observed)
        recovered_check = None
        if outcome == "recovered":
            recovered_check = verify_recovered(target, kind, store, tx)
            if recovered_check:
                raise FixtureError("recovered target does not match its baseline: " + "; ".join(recovered_check[:4]),
                                   dict(observed, recovered_check=recovered_check))
        if _inject:
            raise FixtureError("injected tamper was not detected", observed)
        prepared = json.loads((store / tx / "prepared.json").read_bytes())
        ref = None
        if with_receipt:
            refs = exec_info["install_refs"]
            if exec_info["install_receipt_error"] is not None or [row.get("install_ref") for row in receipt_info["rows"]] != refs \
                    or len(refs) != 1 or refs[0]["tx_id"] != tx or prepared.get("exec") != exec_info["invocation"]:
                raise FixtureError("exec receipt does not bind the real transaction")
            ref = refs[0]
            observed["ref"] = ref
        elif prepared.get("exec") is not None:
            raise FixtureError("no-receipt chain carries an exec identity")
        return {
            "schema": SCHEMA, "synthetic": True, "authorization": "none", "kind": kind, "outcome": outcome,
            "with_receipt": with_receipt, "case": str(case), "target": str(target), "store": str(store),
            "tool_root": str(tool), "review": {"request_id": req["request_id"], "content_id": req["content_id"],
                                               "attempt_id": decision["attempt_id"], "synthetic": True},
            "tx": tx, "records": recs[0]["records"], "prepared_exec": prepared.get("exec"),
            "worker": {"rc": rc, "worker_rc": worker.get("rc"), "error": worker.get("error"), "launch": launch,
                       "env_keys": sorted(wenv), "spawned": worker.get("spawned"), "refused": worker.get("refused"),
                       "modules": worker.get("modules"), "exec_context_env_seen": worker.get("exec_context_env"),
                       "write_roots": spec["write_roots"], "git": spec["git"],
                       "allowed": sorted({os.path.realpath(p) for p in spec["allowed_exec"]} | {spec["git"]})},
            "recovered_check": recovered_check,
            "fault": worker.get("notes"), "exec": exec_info, "receipt": receipt_info, "ref": ref,
            "subject": str(subject.root) if subject is not None else None,
            "events_sha256": {"before": _sha(events_before) if events_before is not None else None,
                              "after": _sha(events_path.read_bytes()) if events_path is not None and events_path.exists() else None},
            "target_tree_changed": _tree(target) != tree_before,
            "dependency_contract": {"env": ENV_CONTRACT, "sources": _source_identity()},
        }
    finally:
        review._tool_home = saved_home
