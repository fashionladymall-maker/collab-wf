"""B3 pure-synthetic state fixture generator.

Everything written here is synthetic. A fixture is never production evidence,
never an installation authorization and never a substitute for a real round.

Tier S (structural) uses the standard library only and does not import any
wflib module, so generating it cannot read or touch the caller's LOCAL, the
tool's install state or any executor (WorkBuddy) data. Given the same root it
is byte-identical; after replacing the root path with "<ROOT>" it is identical
across roots. Git repositories use fixed identities and dates, so commit IDs
are stable.

Tier R (review stores) runs the existing B2 synthetic review harness
(wflib.selftest_review.Env: stand-in runner and verifier that declare
synthetic=true) in a child process whose LOCAL, HOME and TMPDIR are inside the
fixture root, with an audit hook that refuses any write outside that root. It
is semantically reproducible, not byte-identical (real nonces and clocks).

Usage:
    from wflib import b3_fixtures
    index = b3_fixtures.generate(Path(tempfile.mkdtemp()).resolve(), tier_r=True)

See docs/B3_FIXTURES.md for the scenario matrix, expectations and gaps.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath

SCHEMA = "b3-fixture/1"
INDEX_SCHEMA = "b3-fixture-index/1"
GENERATOR = "wflib.b3_fixtures/1"
CLOCK = "2026-01-15T08:00:00+00:00"          # synthetic clock, unrelated to any real round
MARKER = "B3_SYNTHETIC_FIXTURE.txt"
MARKER_TEXT = ("B3 synthetic fixture. Not production evidence, not an installation "
               "authorization, not a real round.\n")
PREFIX = "b3-synthetic-"
INDEX = "B3_FIXTURES_INDEX.json"
_TOOL_ROOT = Path(__file__).resolve().parent.parent   # used only to refuse writes, never read for state
_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "b3-synthetic", "GIT_AUTHOR_EMAIL": "b3-synthetic@fixture.invalid",
    "GIT_AUTHOR_DATE": CLOCK, "GIT_COMMITTER_NAME": "b3-synthetic",
    "GIT_COMMITTER_EMAIL": "b3-synthetic@fixture.invalid", "GIT_COMMITTER_DATE": CLOCK,
}
_GIT_FLAGS = ["-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
              "-c", "core.autocrlf=false", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]


class FixtureRefused(Exception):
    """The requested root or operation is outside the synthetic sandbox."""


# ---------------------------------------------------------------- safety

def _temp_base() -> str:
    # Same candidate order as tempfile.gettempdir(), but read-only: gettempdir() writes and
    # removes a probe file in the temp directory on first use, which is a write outside root.
    for name in ("TMPDIR", "TEMP", "TMP"):
        d = os.environ.get(name)
        if d and os.path.isdir(d):
            return d
    for d in ("/tmp", "/var/tmp", "/usr/tmp"):
        if os.path.isdir(d):
            return d
    raise FixtureRefused("no temp directory available")


def _guard_root(root) -> Path:
    p = Path(root)
    if not p.is_absolute():
        raise FixtureRefused("fixture root must be an absolute path")
    real = Path(os.path.realpath(str(p)))
    if str(real) != str(p):
        raise FixtureRefused("fixture root must be fully resolved (no symlink components or '..'); "
                             "pass Path(...).resolve()")
    if any(part.lower() == ".workbuddy-ai" for part in real.parts):
        raise FixtureRefused("fixture root must not be inside a .workbuddy-ai directory")
    tmp = Path(os.path.realpath(_temp_base()))
    if tmp not in real.parents:
        raise FixtureRefused(f"fixture root must be strictly inside the system temp directory {tmp}")
    if real == _TOOL_ROOT or _TOOL_ROOT in real.parents:
        raise FixtureRefused("fixture root must not be inside the tool checkout")
    if os.path.lexists(str(real)):
        if not stat.S_ISDIR(os.lstat(str(real)).st_mode):
            raise FixtureRefused("fixture root exists and is not a directory")
        if any(True for _ in os.scandir(str(real))):
            raise FixtureRefused("fixture root must be empty")
    else:
        if not real.parent.is_dir():
            raise FixtureRefused("fixture root parent does not exist")
        os.mkdir(str(real), 0o755)
    return real


class _Writer:
    """All writes go through here: relative paths only, no symlinks, no escape."""

    def __init__(self, root: Path):
        self.root = root

    def path(self, rel) -> Path:
        r = PurePosixPath(str(rel))
        if r.is_absolute() or ".." in r.parts or not r.parts:
            raise FixtureRefused(f"bad fixture path {rel!r}")
        return self.root.joinpath(*r.parts)

    def mkdir(self, rel, mode=0o755) -> Path:
        cur = self.root
        for part in PurePosixPath(str(rel)).parts:
            cur = cur / part
            if os.path.lexists(str(cur)):
                if not stat.S_ISDIR(os.lstat(str(cur)).st_mode):
                    raise FixtureRefused(f"non-directory in fixture path {cur}")
            else:
                os.mkdir(str(cur), mode)
                os.chmod(str(cur), mode)
        return cur

    def write(self, rel, data, mode=0o644, *, replace=False) -> Path:
        p = self.path(rel)
        self.mkdir(PurePosixPath(str(rel)).parent) if PurePosixPath(str(rel)).parent.parts else None
        if isinstance(data, str):
            data = data.encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        if replace:
            if os.path.lexists(str(p)) and not stat.S_ISREG(os.lstat(str(p)).st_mode):
                raise FixtureRefused(f"refusing to replace non-file {p}")
            flags |= os.O_TRUNC
        else:
            flags |= os.O_EXCL
        fd = os.open(str(p), flags, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        os.chmod(str(p), mode)
        return p

    def remove(self, rel) -> None:
        p = self.path(rel)
        if not stat.S_ISREG(os.lstat(str(p)).st_mode):
            raise FixtureRefused(f"refusing to remove non-file {p}")
        os.unlink(str(p))


def _git(root: Path, cwd: Path, *args) -> str:
    exe = shutil.which("git")
    if not exe:
        raise FixtureRefused("git executable unavailable")
    if cwd != root and root not in cwd.parents:
        raise FixtureRefused("git cwd outside fixture root")
    env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(root / ".no-home"), "LC_ALL": "C",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
           "GIT_OPTIONAL_LOCKS": "0", **_GIT_IDENTITY}
    r = subprocess.run([exe, *_GIT_FLAGS, *args], cwd=str(cwd), env=env, capture_output=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.decode('utf-8', 'replace')[:300]}")
    return r.stdout.decode("utf-8").strip()


# ---------------------------------------------------------------- encodings

def _hid(label: str, n: int = 32) -> str:
    return hashlib.sha256(("b3-synthetic:" + label).encode("utf-8")).hexdigest()[:n]


def _canon(obj) -> bytes:
    # Same canonical form as review._canon (sorted keys, no ASCII escaping, compact).
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _record(body: dict) -> bytes:
    # Same envelope as release._record_new: body plus body_sha256 over its canonical form.
    return _canon(dict(body, body_sha256=hashlib.sha256(_canon(body)).hexdigest()))


def _doc(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _jsonl(rows) -> bytes:
    return b"".join(json.dumps(r, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n" for r in rows)


def _synthetic_secret() -> str:
    # Built at runtime so no secret-shaped literal exists in source (same rule as PR28).
    return "s" + "k-" + "B3SYNTHETICFIXTURE" + "0" * 12 + "NOTAREALKEY"


# ---------------------------------------------------------------- project helpers

_GITIGNORE = ("workflow/state.json\nworkflow/*.jsonl\nworkflow/usage.json\nworkflow/evidence/\n"
              "workflow/outbox/\nworkflow/goals_state.json\nworkflow/approvals.json\n")


def _project(w: _Writer, sid: str, *, state=None, events=None, turns=None, git=False, files=None) -> Path:
    name = PREFIX + sid.lower()
    base = "project"
    w.write(f"{base}/workflow/wf.json", _doc({"project": name, "created": CLOCK, "protect": []}))
    w.write(f"{base}/{MARKER}", MARKER_TEXT)
    w.write(f"{base}/PROJECT.md", f"# PROJECT.md\n\nSynthetic B3 fixture project {name}.\n")
    w.write(f"{base}/.gitignore", _GITIGNORE)
    for rel, data in (files or {}).items():
        w.write(f"{base}/{rel}", data)
    root = w.path(base)
    if git:
        _git(w.root, root, "init", "-q", "--template=")
        _git(w.root, root, "add", "-A")
        _git(w.root, root, "commit", "-q", "-m", "synthetic baseline")
    if state is not None:
        w.write(f"{base}/workflow/state.json", _doc(state))
    if events is not None:
        w.write(f"{base}/workflow/events.jsonl", events if isinstance(events, bytes) else _jsonl(events))
    if turns is not None:
        w.write(f"{base}/workflow/turns.jsonl", _jsonl(turns))
    return root


def _install_store(w: _Writer) -> Path:
    # Same levels and modes as release._install_store for a project target.
    w.mkdir("project/workflow/evidence")
    return w.mkdir("project/workflow/evidence/install", mode=0o700)


def _state(state: str, turn: int, seq: int, **extra) -> dict:
    out = {"schema": 1, "state": state, "since": CLOCK, "turn": turn, "seq": seq, "attempts": 0}
    out.update(extra)
    return out


def _event(seq: int, type_: str, turn: int, state: str, **fields) -> dict:
    ev = {"seq": seq, "ts": CLOCK, "type": type_, "turn": turn, "state": state, "actor": "driver", "pid": 4242}
    ev.update(fields)
    return ev


# ---------------------------------------------------------------- tier S scenarios

def _s01(w):
    w.write("host/tool/VERSION", "1.9.7\n")
    w.write("host/tool/MANIFEST.json", _doc({"version": "1.9.7", "files": {
        "VERSION": hashlib.sha256(b"1.9.7\n").hexdigest()}}))
    w.write(f"host/tool/{MARKER}", MARKER_TEXT)
    root = w.path("project")
    events = [
        _event(1, "exec", 11, "EXECUTE", cid="c01", by="driver", exit_code=0, duration_s=1.0,
               cmd="/synthetic/host/tool/bin/wf self-update /synthetic/pkg/collab-wf-1.9.8.zip"),
        _event(2, "exec", 11, "EXECUTE", cid="c02", by="driver", exit_code=0, duration_s=1.0,
               cmd='python3 "/synthetic host/tool dir/wf.py" self-update "/synthetic pkg/collab-wf-1.9.8.zip"'),
        _event(3, "exec", 11, "EXECUTE", cid="c03", by="driver", exit_code=0, duration_s=0.5,
               cmd='/synthetic/host/tool/bin/wf apply "/synthetic pkg/project-v0.2.zip"'),
        _event(4, "apply", 11, "APPLY", version="v0.2", commit="0" * 40, changed=1, by="driver"),
    ]
    w.write("host/local/registry.json", _doc({"projects": [
        {"name": PREFIX + "s01-legacy-host-1.9.7", "root": str(root), "added": CLOCK}]}))
    _project(w, "S01-legacy-host-1.9.7", state=_state("IDLE", 11, 4), events=events,
             turns=[{"turn": 11, "status": "done", "from": "driver", "ts_done": CLOCK}])


def _s02(variant):
    def build(w):
        st = _state("WAIT_REPLY", 7, 3, poll_after=CLOCK)
        if variant == "a":
            st["send_slot"] = {"lease_id": _hid("s02a-lease"), "turn": 7, "attempt": 1, "granted_at": CLOCK}
        _project(w, f"S02{variant}-wait-reply-no-ref", state=st,
                 events=[_event(1, "send", 7, "SEND", by="driver"), _event(2, "auto", 7, "SEND", rule="SYNTHETIC",
                                                                       action="report", by="driver",
                                                                       detail="synthetic fixture")])
    return build


def _s03(w):
    ref = "synthetic-ref-" + _hid("s03-ref", 16)
    st = _state("SEND", 7, 2, send_slot={"lease_id": _hid("s03-lease"), "turn": 7, "attempt": 1, "granted_at": CLOCK},
                send_intent={"turn": 7, "status": "accepted", "ts": CLOCK, "file": "workflow/outbox/turn-007.md",
                             "message": {"synthetic_placeholder": True}, "ref": ref})
    _project(w, "S03-send-accepted-intent-crash", state=st, events=[_event(1, "send", 7, "SEND", by="driver")])


def _quota_episode(corrupt: bool) -> dict:
    ep = {"schema": "recovery/1", "id": _hid("s04-episode"), "class": "quota",
          "since": "2026-01-14T08:00:00+00:00", "deadline": "2026-01-22T08:00:00+00:00",
          "upgraded_at": None, "attempts": {"transient": 0, "quota": 3}, "rules": {"QUOTA": 3},
          "last": {"ts": "2026-01-15T07:00:00+00:00", "rule": "QUOTA", "detail": "synthetic quota fixture"},
          "keys": []}
    if corrupt:
        ep["deadline"] = "2026-03-01T08:00:00+00:00"   # beyond the 8-day quota window
    return ep


def _s04(corrupt):
    def build(w):
        st = _state("BLOCKED", 49, 2, return_to="SEND", blocked_from="SEND",
                    reason="[QUOTA] synthetic fixture: quota recovery gate (not a real round)",
                    recovery=_quota_episode(corrupt))
        _project(w, "S04" + ("b-quota-blocked-corrupt-episode" if corrupt else "a-quota-blocked-valid-episode"),
                 state=st, events=[_event(1, "auto", 49, "SEND", rule="QUOTA", action="wait", by="driver",
                                          detail="synthetic fixture"),
                                   _event(2, "state", 49, "BLOCKED", by="driver", detail="synthetic fixture")])
    return build


def _s05(w):
    root = _project(w, "S05-dirty-variants", git=True, files={
        "app.py": "print('v1')\n", "docs/old.md": "old\n", "data/keep.txt": "protected v1\n"})
    w.write("project/app.py", "print('v1 modified')\n", replace=True)          # tracked, modified
    w.remove("project/docs/old.md")                                               # tracked, deleted
    w.write("project/notes/new.txt", "untracked synthetic note\n")                # untracked
    w.write("project/notes/leak.txt", "token_note = " + _synthetic_secret() + "\n")  # untracked, secret-shaped
    w.write("project/data/keep.txt", "protected v2\n", replace=True)              # protected path (data/)
    w.write("project/ignored.log", "ignored synthetic log\n")                     # ignored via .gitignore below
    w.write("project/.git/info/exclude", "ignored.log\n", replace=True)
    return root


def _s06(w):
    binding = {"schema": "live-collection/2", "project": PREFIX + "s06-legacy-live-collection-v2",
               "segments": [4], "authorization": "unavailable", "authorization_complete": False,
               "content_complete": False}
    record = {"verified": {"granted": False, "why": ["install_history_unavailable"], "ts": CLOCK,
                           "binding": binding, "collection": {"state": "collected", "authorization": "unavailable"}}}
    legacy_b1 = {"verified": {"granted": True, "binding": {"goals_sha": _hid("s06-goals", 64), "tool_digest": None}}}
    _project(w, "S06-legacy-live-collection-v2", state=_state("IDLE", 3, 0),
             files={"workflow/goals.json": _doc({"goals": [
                 {"id": "L2", "title": "synthetic", "kind": "exec", "check": "true", "pass": {"exit": 0},
                  "means": "synthetic", "on_fail": "inspect", "live": {"rounds": 1, "segments": [4]}},
                 {"id": "B1", "title": "synthetic", "kind": "exec", "check": "true", "pass": {"exit": 0},
                  "means": "synthetic", "on_fail": "inspect"}]})})
    w.write("project/workflow/goals_state.json", _doc({"L2": record, "B1": legacy_b1}))


def _s07(case):
    def build(w):
        root = _project(w, "S07" + case, git=True)
        tx = "tx-" + _hid("s07" + case)
        base = f"project/workflow/evidence/install/{tx}"
        _install_store(w)
        w.mkdir(base, mode=0o700)
        prepared = {"schema": "install_tx/1", "state": "prepared", "tx_id": tx, "kind": "project",
                    "target_root": str(root), "request_id": "rq-" + _hid("s07-rq" + case),
                    "content_id": _hid("s07-content" + case, 64), "structural_fixture": "b3-synthetic"}
        if case.startswith("d"):
            prepared["tx_id"] = "tx-" + _hid("s07-other")              # identity mismatch with directory
        prepared_raw = _record(prepared)
        psha = json.loads(prepared_raw)["body_sha256"]
        w.write(f"{base}/prepared.json", prepared_raw, 0o400)
        terminal = {"b": "failed", "c": "partial", "e": "failed"}.get(case[0])
        if terminal:
            body = {"schema": "install_tx/1", "state": terminal, "tx_id": tx, "prepared_sha256": psha,
                    "error": {"type": "SyntheticFailure", "message": "synthetic fixture"}}
            raw = _record(body)
            if case.startswith("e"):
                raw = raw.replace(b"synthetic fixture", b"tampered fixture")   # breaks body_sha256
            w.write(f"{base}/{terminal}.json", raw, 0o400)
    return build


def _s08(w):
    root = _project(w, "S08-rollback-target-missing", git=True, files={"app.py": "print('v0.1')\n"})
    _git(w.root, root, "tag", "v0.1")
    w.write("project/app.py", "print('v0.2')\n", replace=True)
    _git(w.root, root, "add", "-A")
    _git(w.root, root, "commit", "-q", "-m", "synthetic v0.2")
    _install_store(w)   # store exists (as in any real project) but holds no baseline for v0.1


def _s09(case):
    def build(w):
        rows = [_event(1, "state", 2, "IDLE", by="driver"), _event(2, "state", 2, "INTAKE", by="driver")]
        if case.startswith("a"):
            raw = _jsonl(rows) + b'{"seq": 3, "ts": "' + CLOCK.encode() + b'", "type": "exe'
        else:
            raw = _jsonl(rows + [_event(2, "state", 2, "EXECUTE", by="driver")])
        _project(w, "S09" + case, state=_state("INTAKE", 2, 3), events=raw)
    return build


# ---------------------------------------------------------------- scenario registry
# validated_by names a check in wflib/selftest_b3_fixtures.py that runs the real consumer;
# None means layout only: no real consumer check exists yet (a declared gap, not a pass).

SCENARIOS = [
    {"id": "S01-legacy-host-1.9.7", "tier": "S", "build": _s01,
     "models": "old 1.9.7 host before PR31: exec events without install_exec; two installer spellings; legacy apply",
     "intended": "first-upgrade input; modern C5 must report every legacy installer spelling as unknown, not drop it",
     "validated_by": "s01_loads"},
    {"id": "S02a-wait-reply-no-ref-slot-present", "tier": "S", "build": _s02("a"),
     "models": "WAIT_REPLY without send_ref (send_slot present)",
     "intended": "driver.h_wait_reply takes the NO_REF anomaly branch; B3 NO_REF adjustment pending (A lane)",
     "validated_by": "state_fields"},
    {"id": "S02b-wait-reply-no-ref-slot-missing", "tier": "S", "build": _s02("b"),
     "models": "WAIT_REPLY without send_ref and without send_slot (reading of 'dual missing'; A lane to confirm)",
     "intended": "NO_REF plus missing-slot evidence; must not borrow a lease or resend",
     "validated_by": "state_fields"},
    {"id": "S03-send-accepted-intent-crash", "tier": "S", "build": _s03,
     "models": "SEND with send_intent.status=accepted and ref, crash before send_ref was persisted",
     "intended": "must poll/confirm only, never treat as unsent; B3 crash protection pending (A lane); "
                 "message is a placeholder, not the real _prepare_send shape",
     "validated_by": "state_fields"},
    {"id": "S04a-quota-blocked-valid-episode", "tier": "S", "build": _s04(False),
     "models": "BLOCKED with a valid quota recovery/1 episode (shape of a quota-gated round; turn 49, not 149)",
     "intended": "recovery episode valid at the synthetic clock; fixture never resumes anything",
     "validated_by": "recovery_check"},
    {"id": "S04b-quota-blocked-corrupt-episode", "tier": "S", "build": _s04(True),
     "models": "BLOCKED with a quota episode whose deadline exceeds the 8-day window",
     "intended": "recovery._check rejects the episode", "validated_by": "recovery_check"},
    {"id": "S05-dirty-variants", "tier": "S", "build": _s05,
     "models": "worktree: tracked modified, tracked deleted, untracked, secret-shaped untracked, protected, ignored",
     "intended": "release._dirty lists non-protected changes; sync._scan_text flags the secret-shaped file; "
                 "ignored file absent", "validated_by": "dirty_and_scan"},
    {"id": "S06-legacy-live-collection-v2", "tier": "S", "build": _s06,
     "models": "goals_state with a live-collection/2 record and a B1-style two-key binding",
     "intended": "must be recollected under live-collection/3; never grant", "validated_by": None},
    {"id": "S07a-install-tx-prepared-only", "tier": "S", "build": _s07("a-prepared-only"),
     "models": "install store: prepared without terminal record (structural: no baseline)",
     "intended": "install lock scan refuses: unclosed transaction", "validated_by": "tx_scan"},
    {"id": "S07b-install-tx-failed-closed", "tier": "S", "build": _s07("b-failed-closed"),
     "models": "install store: prepared + failed (shape of Claude CL-CT5 hidden failure)",
     "intended": "lock scan treats it as closed; C5 must still surface it in its window (open defect CL-CT5)",
     "validated_by": "tx_scan"},
    {"id": "S07c-install-tx-partial", "tier": "S", "build": _s07("c-partial"),
     "models": "install store: prepared + partial", "intended": "lock scan refuses: incomplete",
     "validated_by": "tx_scan"},
    {"id": "S07d-install-tx-identity-mismatch", "tier": "S", "build": _s07("d-identity-mismatch"),
     "models": "install store: prepared.tx_id differs from its directory", "intended": "lock scan refuses: identity",
     "validated_by": "tx_scan"},
    {"id": "S07e-install-tx-hash-tampered", "tier": "S", "build": _s07("e-hash-tampered"),
     "models": "install store: failed record whose body no longer matches body_sha256",
     "intended": "lock scan refuses: hash mismatch", "validated_by": "tx_scan"},
    {"id": "S08-rollback-target-missing", "tier": "S", "build": _s08,
     "models": "git project with tag v0.1 but no install store/baseline to restore from",
     "intended": "release.rollback refuses and leaves HEAD and worktree unchanged", "validated_by": "rollback"},
    {"id": "S09a-events-truncated", "tier": "S", "build": _s09("a-events-truncated"),
     "models": "events.jsonl whose last record is truncated", "intended": "live._strict_log returns corrupt",
     "validated_by": "strict_log"},
    {"id": "S09b-events-duplicate-seq", "tier": "S", "build": _s09("b-events-duplicate-seq"),
     "models": "events.jsonl with two records sharing seq=2",
     "intended": "no current consumer rejects duplicate seq (declared gap)", "validated_by": None},
    {"id": "R01-review-expired", "tier": "R", "case": "expired",
     "models": "B2 synthetic review completed more than REUSE_DAYS ago",
     "intended": "not eligible for install now", "validated_by": "review_status"},
    {"id": "R02-review-later-block", "tier": "R", "case": "later_block",
     "models": "B2 synthetic pass, then a later block registered for the same content",
     "intended": "earlier pass no longer eligible", "validated_by": "review_status"},
    {"id": "R03-verifier-pin-changed", "tier": "R", "case": "pin_changed",
     "models": "B2 synthetic pass, then the host verifier pin changed", "intended": "not eligible; verifier changed",
     "validated_by": "review_status"},
]


# ---------------------------------------------------------------- tier R (child process)

_TIER_R_CHILD = r'''
import json, os, sys
from pathlib import Path
ROOT = os.path.realpath(sys.argv[1]); SD = Path(sys.argv[2]); CASE = sys.argv[3]

def _inside(p):
    if isinstance(p, bytes):
        p = os.fsdecode(p)
    if not isinstance(p, str) or p == os.devnull:
        return True
    q = os.path.realpath(p)
    return q == ROOT or q.startswith(ROOT + os.sep)

def _guard(event, args):
    if event == "open":
        path, mode, flags = args
        if isinstance(path, int):
            return
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or \
            (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC))
        if writing and not _inside(path):
            raise PermissionError("b3 fixture child: write outside fixture root: %r" % (path,))
    elif event in ("os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.truncate", "os.utime"):
        if not _inside(args[0]):
            raise PermissionError("b3 fixture child: %s outside fixture root: %r" % (event, args[0]))
    elif event in ("os.rename", "os.replace", "os.symlink", "os.link", "shutil.copyfile", "shutil.move"):
        if not (_inside(args[0]) and _inside(args[1])):
            raise PermissionError("b3 fixture child: %s outside fixture root" % event)
    elif event == "subprocess.Popen":
        cwd = args[2]
        if cwd is not None and not _inside(cwd):
            raise PermissionError("b3 fixture child: subprocess cwd outside fixture root")

sys.addaudithook(_guard)
from datetime import timedelta
from unittest.mock import patch
from wflib import core, review
from wflib.selftest_review import Env
env = Env(SD / "host")
review._tool_home = lambda: env.tool
project = env.project("b3-synthetic-r-" + CASE.replace("_", "-"))
pkg = env.pkg("v0.2")
out = {"case": CASE, "project": str(project), "tool": str(env.tool), "local": str(core.LOCAL)}
if CASE == "expired":
    at = review._now() - timedelta(days=review.REUSE_DAYS + 1)
    with patch.object(review, "_now", lambda: at):
        req = review.prepare(project, pkg, kind="project", author="B")
        d = review.run(req["request_id"])
    out.update(request_id=req["request_id"], allowed_at_review=d.get("allowed"), synthetic=d["provenance"]["synthetic"])
elif CASE == "later_block":
    req = review.prepare(project, pkg, kind="project", author="B")
    d = review.run(req["request_id"])
    env.behave(verdict="block")
    req2 = review.prepare(project, pkg, kind="project", author="B")
    d2 = review.run(req2["request_id"])
    out.update(request_id=req["request_id"], later_request_id=req2["request_id"], allowed_at_review=d.get("allowed"),
               later_verdict=d2.get("verdict"), synthetic=d["provenance"]["synthetic"])
elif CASE == "pin_changed":
    req = review.prepare(project, pkg, kind="project", author="B")
    d = review.run(req["request_id"])
    env.set_transport(pinned="0" * 64)
    out.update(request_id=req["request_id"], allowed_at_review=d.get("allowed"), synthetic=d["provenance"]["synthetic"])
else:
    raise SystemExit("unknown case")
print(json.dumps(out, sort_keys=True))
'''


def _tier_r(w: _Writer, sid: str, case: str) -> dict:
    sd = w.mkdir(sid)
    for d in ("home", "tmp", "host"):
        w.mkdir(f"{sid}/{d}")
    w.write(f"{sid}/{MARKER}", MARKER_TEXT)
    env = {"PATH": os.environ.get("PATH", os.defpath), "PYTHONPATH": str(_TOOL_ROOT), "PYTHONDONTWRITEBYTECODE": "1",
           "WF_LOCAL_DIR": str(sd / "host" / "local"), "WF_NOTIFY": "0", "HOME": str(sd / "home"),
           "TMPDIR": str(sd / "tmp"), "LC_ALL": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_TERMINAL_PROMPT": "0"}
    r = subprocess.run([sys.executable, "-c", _TIER_R_CHILD, str(w.root), str(sd), case], cwd=str(sd), env=env,
                       capture_output=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"tier R {case} failed: {r.stderr.decode('utf-8', 'replace')[-1500:]}")
    return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])


# ---------------------------------------------------------------- manifest

def _walk(base: Path):
    out = {}
    for dirpath, dirnames, filenames in os.walk(str(base)):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for name in sorted(filenames):
            p = Path(dirpath) / name
            rel = p.relative_to(base).as_posix()
            if rel == "FIXTURE.json":
                continue
            st = os.lstat(str(p))
            if not stat.S_ISREG(st.st_mode):
                raise FixtureRefused(f"unexpected non-file in fixture: {p}")
            out[rel] = p
    return out


def _normalize(raw: bytes, root: str):
    """Root-independent view of a file, for cross-root comparison only.

    Hash-envelope records (top-level body_sha256, as written by release._record_new) bind the
    absolute target root, so their own and derived digests change with the root. Their view
    drops body_sha256 and replaces every *sha256 digest by <DERIVED>; exact bytes are covered
    by same-root regeneration and the real release._read_record envelope check.
    """
    if raw.startswith(b"{") and b'"body_sha256"' in raw:
        try:
            doc = json.loads(raw)
        except ValueError:
            doc = None
        if isinstance(doc, dict) and "body_sha256" in doc:
            def walk(v, key=""):
                if isinstance(v, dict):
                    return {k: walk(x, k) for k, x in v.items() if k != "body_sha256"}
                if isinstance(v, list):
                    return [walk(x) for x in v]
                if isinstance(v, str):
                    if key.endswith("sha256") and len(v) == 64:
                        return "<DERIVED>"
                    return v.replace(root, "<ROOT>")
                return v
            return _canon(walk(doc)), "envelope"
    return raw.replace(root.encode("utf-8"), b"<ROOT>"), "root-path"


def _manifest(root: Path, sd: Path) -> dict:
    files, heads = {}, {}
    marker = str(root).encode("utf-8")
    for rel, p in _walk(sd).items():
        raw = p.read_bytes()
        normalized, how = _normalize(raw, str(root))
        files[rel] = {"sha256": hashlib.sha256(raw).hexdigest(),
                      "normalized_sha256": hashlib.sha256(normalized).hexdigest(), "normalization": how,
                      "mode": format(stat.S_IMODE(os.lstat(str(p)).st_mode), "04o")}
    for dirpath, dirnames, _ in os.walk(str(sd)):
        if ".git" in dirnames:
            repo = Path(dirpath)
            heads[repo.relative_to(sd).as_posix() or "."] = _git(root, repo, "rev-parse", "HEAD")
            dirnames.remove(".git")
    return {"files": files, "git_heads": heads}


# ---------------------------------------------------------------- entry point

def generate(root, *, scenarios=None, tier_r=False) -> dict:
    """Generate synthetic fixtures under an empty, resolved temp-dir root. Returns the index."""
    root = _guard_root(root)
    w = _Writer(root)
    wanted = set(scenarios) if scenarios is not None else None
    selected = [s for s in SCENARIOS if (wanted is None or s["id"] in wanted) and (tier_r or s["tier"] == "S")]
    if wanted is not None and len(selected) != len(wanted):
        missing = sorted(wanted - {s["id"] for s in selected})
        raise FixtureRefused(f"unknown or tier-R scenarios without tier_r=True: {missing}")
    w.write(MARKER, MARKER_TEXT)
    index = {"schema": INDEX_SCHEMA, "synthetic": True, "not_production_evidence": True, "authorization": "none",
             "generator": GENERATOR, "clock": CLOCK, "root_placeholder": "<ROOT>", "scenarios": {}}
    for s in selected:
        sid = s["id"]
        sub = _Writer(w.mkdir(sid))
        extra = {}
        if s["tier"] == "S":
            sub.write(MARKER, MARKER_TEXT)
            s["build"](sub)
        else:
            extra = {"tier_r_result": _tier_r(w, sid, s["case"])}
        sd = root / sid
        doc = {"schema": SCHEMA, "synthetic": True, "not_production_evidence": True, "authorization": "none",
               "generator": GENERATOR, "clock": CLOCK if s["tier"] == "S" else "real clock (tier R)",
               "scenario": sid, "tier": s["tier"], "models": s["models"], "intended": s["intended"],
               "validated_by": s["validated_by"],
               "byte_reproducible": s["tier"] == "S", **extra, **_manifest(root, sd)}
        raw = _doc(doc)
        sub.write("FIXTURE.json", raw)
        view = {"scenario": sid, "tier": s["tier"], "git_heads": doc["git_heads"],
                "files": {rel: [f["normalized_sha256"], f["mode"]] for rel, f in doc["files"].items()}}
        index["scenarios"][sid] = {"fixture_sha256": hashlib.sha256(raw).hexdigest(),
                                   "normalized_sha256": hashlib.sha256(_canon(view)).hexdigest(),
                                   "byte_reproducible": s["tier"] == "S"}
    w.write(INDEX, _doc(index))
    return index


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Generate B3 synthetic fixtures (never production evidence).")
    ap.add_argument("root", help="empty, fully resolved directory inside the system temp dir")
    ap.add_argument("--tier-r", action="store_true", help="also build review-store scenarios via the B2 harness")
    ap.add_argument("--scenario", action="append", help="limit to these scenario IDs")
    a = ap.parse_args()
    print(json.dumps(generate(a.root, scenarios=a.scenario, tier_r=a.tier_r), ensure_ascii=False, indent=2))
