#!/usr/bin/env python3
"""B3 first-upgrade code recovery (external runner, stdlib only).

An install made by the 1.9.7 installer has no modern verified baseline, so the
new ``self-rollback`` cannot return to it, and its ``wf-v*`` tags are movable
names, not evidence. This runner restores tool *code* only from a snapshot frozen
before the first upgrade:

  freeze  before upgrading: clean git install root -> complete path/mode/sha256/git-blob
          manifest plus content-addressed blobs in a snapshot directory outside the root.
  plan    (default, read-only) verifies snapshot, target identity and current tree; prints
          four independent conclusions: code_recovery, state_preservation, schema_compat,
          restart.
  apply   writes only with --snapshot-sha256 (recorded at freeze time, never re-derived from
          disk) and --confirm-target (the identity printed by plan for the current tree).

Never touched: ``.git/`` internals other than one restore commit, ``local/``, WF_LOCAL_DIR,
registered projects. No tag is read or moved. The old executor is never started. The old
installer has no review-id gate; any review recorded at freeze is external evidence only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

SCHEMA = "b3-first-upgrade-snapshot/1"
APPLY_SCHEMA = "b3-first-upgrade-apply/1"
PROTECTED = ("local", ".git")
GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_OPTIONAL_LOCKS": "0",
           "GIT_TERMINAL_PROMPT": "0", "GIT_LITERAL_PATHSPECS": "1", "LC_ALL": "C"}
GIT_CFG = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
           "-c", "core.autocrlf=false", "-c", "core.symlinks=true"]


class Refused(Exception):
    """A precondition does not hold; nothing was written by the refusing step."""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def _canon(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _git(root: Path, *args, check=True, inp=None) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_ENV)
    r = subprocess.run(["git", *GIT_CFG, *args], cwd=str(root), env=env, input=inp, capture_output=True)
    if check and r.returncode != 0:
        raise Refused("git_failed", f"git {' '.join(args[:3])}: {r.stderr.decode(errors='replace').strip()[:300]}")
    return r.stdout.decode("utf-8", errors="strict")


def _directory(path, what) -> Path:
    """Absolute, alias-free (no symlink component), existing real directory."""
    p = Path(path)
    if not p.is_absolute():
        raise Refused(f"{what}_not_absolute", str(p))
    norm = os.path.normpath(str(p))
    if os.path.realpath(norm) != norm:
        raise Refused(f"{what}_path_alias", f"{norm} -> {os.path.realpath(norm)}")
    try:
        st = os.lstat(norm)
    except FileNotFoundError:
        raise Refused(f"{what}_missing", norm) from None
    if not stat.S_ISDIR(st.st_mode):
        raise Refused(f"{what}_not_directory", norm)
    return Path(norm)


def _overlap(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def _safe_rel(rel: str) -> str:
    parts = rel.split("/")
    if (not rel or rel.startswith("/") or "\\" in rel or "\0" in rel
            or any(x in ("", ".", "..") for x in parts) or parts[0] in PROTECTED):
        raise Refused("unsafe_path", rel)
    return rel


def _regular_under(root: Path, rel: str, *, may_be_absent=False):
    """lstat every component below root; only real dirs and a regular final file pass."""
    cur = root
    parts = rel.split("/")
    for i, part in enumerate(parts):
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            if may_be_absent:
                return None
            raise Refused("tracked_file_missing", rel) from None
        last = i == len(parts) - 1
        if stat.S_ISLNK(st.st_mode) or (not last and not stat.S_ISDIR(st.st_mode)) \
                or (last and not stat.S_ISREG(st.st_mode)):
            raise Refused("non_regular_path", rel)
    return cur


def _repo(root: Path):
    gd = root / ".git"
    if not os.path.lexists(gd) or not stat.S_ISDIR(os.lstat(gd).st_mode):
        raise Refused("not_an_ordinary_git_install", str(root))
    for rel in ("shallow", "objects/info/alternates", "commondir"):
        if os.path.lexists(gd / rel):
            raise Refused("unsupported_git_indirection", rel)
    if _git(root, "rev-parse", "--show-toplevel").strip() != str(root):
        raise Refused("git_toplevel_mismatch", str(root))


def _tracked(root: Path) -> dict:
    out = {}
    raw = _git(root, "ls-files", "-s", "-z")
    for row in filter(None, raw.split("\0")):
        meta, path = row.split("\t", 1)
        mode, blob, stage = meta.split(" ")
        if stage != "0":
            raise Refused("unmerged_index", path)
        if mode not in ("100644", "100755"):
            raise Refused("unsupported_tracked_mode", f"{mode} {path}")
        out[_safe_rel(path) if path.split("/")[0] not in PROTECTED else path] = (mode, blob)
    leaked = [p for p in out if p.split("/")[0] in PROTECTED]
    if leaked:
        raise Refused("protected_state_tracked", ", ".join(leaked[:5]))
    return out


def _clean(root: Path):
    dirty = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none")
    if dirty:
        raise Refused("dirty_or_unknown_worktree", dirty.replace("\0", " ")[:300])


def _dirty_paths(root: Path) -> set:
    raw = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    return {row[3:] for row in raw.split("\0") if row}


def _head(root: Path) -> dict:
    return {"commit": _git(root, "rev-parse", "--verify", "HEAD^{commit}").strip(),
            "tree": _git(root, "rev-parse", "--verify", "HEAD^{tree}").strip()}


def _identity(root: Path) -> dict:
    st = os.lstat(root)
    return {"dev": st.st_dev, "ino": st.st_ino}


def _state_roots(root: Path) -> list:
    """Runtime state the runner must never write; recorded only to report changes."""
    roots = [("install_local", root / "local")]
    local = os.environ.get("WF_LOCAL_DIR")
    local = Path(os.path.realpath(local)) if local else root / "local"
    if local != root / "local":
        roots.append(("wf_local", local))
    reg = local / "registry.json"
    if reg.is_file() and not reg.is_symlink():
        for row in json.loads(reg.read_text(encoding="utf-8")).get("projects", []):
            proj = Path(os.path.realpath(row["root"]))
            if _overlap(proj, root):
                raise Refused("project_overlaps_install_root", str(proj))
            roots.append(("project_workflow", proj / "workflow"))
    return roots


def _inventory(path: Path) -> dict:
    files = {}
    if not path.exists():
        return files
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for name in sorted(dirnames + filenames):
            p = Path(dirpath) / name
            rel = str(p.relative_to(path))
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                files[rel] = {"type": "symlink", "target": os.readlink(p)}
            elif stat.S_ISREG(st.st_mode):
                files[rel] = {"type": "file", "size": st.st_size, "sha256": _sha256(p.read_bytes())}
    return files


def _state(root: Path) -> list:
    return [{"kind": kind, "root": str(p), "files": _inventory(p)} for kind, p in _state_roots(root)]


# ---------------- freeze ----------------

def freeze(install_root, snapshot_dir, *, review_id=None, package=None) -> dict:
    root = _directory(install_root, "install_root")
    snap = Path(os.path.normpath(str(snapshot_dir)))
    if not snap.is_absolute():
        raise Refused("snapshot_not_absolute", str(snap))
    parent = _directory(snap.parent, "snapshot_parent")
    snap = parent / snap.name
    if _overlap(snap, root):
        raise Refused("snapshot_overlaps_install_root", f"{snap} / {root}")
    if os.path.lexists(snap):
        raise Refused("snapshot_dir_exists", str(snap))
    _repo(root)
    files = _tracked(root)
    _clean(root)
    head = _head(root)
    rows = []
    blobs = {}
    for rel, (mode, blob) in sorted(files.items()):
        data = _regular_under(root, rel).read_bytes()
        if _git_blob(data) != blob:
            raise Refused("worktree_differs_from_index", rel)
        sha = _sha256(data)
        rows.append({"path": rel, "mode": mode, "size": len(data), "sha256": sha, "git_blob": blob})
        blobs[sha] = data
    mf = root / "MANIFEST.json"
    manifest_check = None
    if "MANIFEST.json" in files:
        listed = json.loads(mf.read_bytes()).get("files", {})
        by = {r["path"]: r["sha256"] for r in rows}
        manifest_check = {"listed": len(listed), "mismatch": sorted(p for p, h in listed.items() if by.get(p) != h)}
    gate = None
    if review_id or package:
        gate = {"review_id": review_id, "package": str(package) if package else None,
                "package_sha256": _sha256(Path(package).read_bytes()) if package else None,
                "enforced_by_old_host": False}
    doc = {"schema": SCHEMA, "created_at": _now(), "install_root": str(root), "root_identity": _identity(root),
           "git": head, "version": (root / "VERSION").read_text(encoding="utf-8").strip()
           if "VERSION" in files else None, "manifest_check": manifest_check, "files": rows,
           "protected": [p + "/" for p in PROTECTED], "state": _state(root), "external_gate": gate}
    os.mkdir(snap, 0o700)   # must not exist: never merge into an old snapshot
    (snap / "blobs").mkdir(mode=0o700)
    for sha, data in blobs.items():
        p = snap / "blobs" / sha
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o400)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    raw = _canon(doc)
    fd = os.open(snap / "manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    loaded = _load_snapshot(snap, _sha256(raw))
    if _head(root) != head or _tracked(root) != files:
        raise Refused("install_root_changed_during_freeze", str(root))
    return {"result": "frozen", "snapshot_dir": str(snap), "snapshot_sha256": _sha256(raw),
            "files": len(loaded["files"]), "git": head, "version": doc["version"],
            "manifest_check": manifest_check, "external_gate": gate}


def _load_snapshot(snap, expected_sha256) -> dict:
    snap = _directory(snap, "snapshot_dir")
    if not re.fullmatch(r"[0-9a-f]{64}", str(expected_sha256 or "")):
        raise Refused("snapshot_sha256_required")
    mp = snap / "manifest.json"
    if not os.path.lexists(mp) or not stat.S_ISREG(os.lstat(mp).st_mode):
        raise Refused("snapshot_manifest_missing", str(mp))
    raw = mp.read_bytes()
    if _sha256(raw) != expected_sha256:
        raise Refused("snapshot_manifest_hash_mismatch", _sha256(raw))
    try:
        doc = json.loads(raw)
    except ValueError as e:
        raise Refused("snapshot_manifest_corrupt", str(e)) from None
    if doc.get("schema") != SCHEMA or not isinstance(doc.get("files"), list):
        raise Refused("snapshot_schema_unsupported", str(doc.get("schema")))
    seen = set()
    for row in doc["files"]:
        rel = _safe_rel(row["path"])
        if rel in seen or row.get("mode") not in ("100644", "100755"):
            raise Refused("snapshot_manifest_corrupt", rel)
        seen.add(rel)
        bp = snap / "blobs" / row["sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) or not os.path.lexists(bp) \
                or not stat.S_ISREG(os.lstat(bp).st_mode):
            raise Refused("snapshot_blob_missing", rel)
        data = bp.read_bytes()
        if _sha256(data) != row["sha256"] or len(data) != row["size"] or _git_blob(data) != row["git_blob"]:
            raise Refused("snapshot_blob_corrupt", rel)
    doc["_dir"] = snap
    return doc


# ---------------- plan ----------------

def _target_id(root, ident, head, snapshot_sha256) -> str:
    return _sha256(_canon({"install_root": str(root), "identity": ident, "head": head, "snapshot": snapshot_sha256}))


def _journal(snap: Path) -> dict:
    j = snap / "journal"
    out = {}
    for name in ("apply.json", "completed.json"):
        p = j / name
        if os.path.lexists(p):
            if not stat.S_ISREG(os.lstat(p).st_mode):
                raise Refused("journal_corrupt", name)
            out[name] = json.loads(p.read_bytes())
    out["failed"] = sorted(p.name for p in j.glob("failed-*.json")) if j.is_dir() else []
    return out


def _current_sha(root, rel):
    p = _regular_under(root, rel, may_be_absent=True)
    return None if p is None else _sha256(p.read_bytes())


def plan(install_root, snapshot_dir, snapshot_sha256) -> dict:
    snap = _load_snapshot(snapshot_dir, snapshot_sha256)
    root = _directory(install_root, "install_root")
    if str(root) != snap["install_root"] or _identity(root) != snap["root_identity"]:
        raise Refused("install_root_identity_mismatch", str(root))
    if _overlap(snap["_dir"], root):
        raise Refused("snapshot_overlaps_install_root", str(snap["_dir"]))
    _repo(root)
    journal = _journal(snap["_dir"])
    current = _tracked(root)
    head = _head(root)
    frozen = {r["path"]: r for r in snap["files"]}
    if "apply.json" not in journal:
        _clean(root)   # resume after an interrupted apply is validated against the recorded plan instead
    writes = [{"path": p, "mode": r["mode"], "post": r["sha256"], "pre": _current_sha(root, p)}
              for p, r in sorted(frozen.items()) if current.get(p) != (r["mode"], r["git_blob"])]
    deletes = [{"path": p, "pre": _current_sha(root, p)} for p in sorted(current) if p not in frozen]
    state_now = _state(root)
    state_diff = _state_diff(snap["state"], state_now)
    restored = head["tree"] == snap["git"]["tree"] and not writes and not deletes
    if "completed.json" in journal and not restored:
        raise Refused("completed_record_but_tree_differs", head["tree"])
    code = ("already_restored" if restored else "resume_interrupted_apply" if "apply.json" in journal
            else "restorable")
    schema = ("unchanged_since_freeze" if not state_diff
              else "unverified: newer code wrote runtime state after freeze; old code compatibility not proven")
    return {
        "result": "plan", "snapshot_sha256": snapshot_sha256, "install_root": str(root), "head": head,
        "frozen": {"git": snap["git"], "version": snap["version"], "files": len(frozen)},
        "target_id": _target_id(root, _identity(root), head, snapshot_sha256),
        "writes": writes, "deletes": deletes, "journal": {k: bool(v) if k != "failed" else v for k, v in journal.items()},
        "conclusions": {
            "code_recovery": code,
            "state_preservation": {"status": "never_written_by_runner",
                                   "roots": [{"kind": s["kind"], "root": s["root"], "files": len(s["files"])}
                                             for s in state_now]},
            "schema_compat": {"status": schema, "changed_since_freeze": state_diff},
            "restart": {"auto_start": False,
                        "status": ("operator_may_restart_old_code" if restored and not state_diff
                                   else "not_established"),
                        "requires": ["code_recovery == already_restored",
                                     "schema_compat == unchanged_since_freeze, or an explicit review of every "
                                     "changed runtime state file against the old code",
                                     "no driver/board process from the newer code still running (not checked)"]},
        },
        "external_gate": snap.get("external_gate") or {"status": "none_recorded", "enforced_by_old_host": False},
        "_snapshot": snap,
    }


def _state_diff(before: list, after: list) -> list:
    b = {(s["kind"], s["root"]): s["files"] for s in before}
    out = []
    for s in after:
        old = b.get((s["kind"], s["root"]), {})
        for rel in sorted(set(old) | set(s["files"])):
            if old.get(rel) != s["files"].get(rel):
                out.append({"kind": s["kind"], "root": s["root"], "path": rel,
                            "change": "created" if rel not in old else "deleted" if rel not in s["files"] else "modified"})
    return out


# ---------------- apply ----------------

def _write_new(path: Path, doc: dict):
    path.parent.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(fd, "wb") as f:
        f.write(_canon(doc))
        f.flush()
        os.fsync(f.fileno())


def _restore_file(root: Path, rel: str, data: bytes, mode: str):
    target = root / rel
    cur = root
    for part in rel.split("/")[:-1]:
        cur = cur / part
        if os.path.lexists(cur):
            if not stat.S_ISDIR(os.lstat(cur).st_mode):
                raise Refused("non_regular_path", rel)
        else:
            os.mkdir(cur, 0o755)
    tmp = target.parent / f".b3-recovery-{os.getpid()}-{target.name}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o755 if mode == "100755" else 0o644)
        os.replace(tmp, target)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def apply(install_root, snapshot_dir, snapshot_sha256, confirm_target) -> dict:
    p = plan(install_root, snapshot_dir, snapshot_sha256)
    snap, root = p["_snapshot"], Path(p["install_root"])
    jdir = snap["_dir"] / "journal"
    journal = _journal(snap["_dir"])
    if p["conclusions"]["code_recovery"] == "already_restored":
        if "apply.json" in journal and "completed.json" not in journal:
            _write_new(jdir / "completed.json", {"schema": APPLY_SCHEMA, "state": "completed", "at": _now(),
                                                 "head": p["head"], "note": "recorded on re-entry"})
        return {k: v for k, v in p.items() if k != "_snapshot"} | {"result": "already_restored"}
    if "apply.json" in journal:
        rec = journal["apply.json"]
        if confirm_target != rec["target_id"]:
            raise Refused("confirm_target_mismatch", "resume must confirm the recorded target")
        writes, deletes = rec["writes"], rec["deletes"]
        if rec["base_head"] != p["head"]:
            raise Refused("blocked_head_moved_during_interrupted_apply", p["head"]["commit"])
        recorded = {w["path"] for w in writes} | {d["path"] for d in deletes}
        for w in writes:   # only this runner's own temp files, next to paths it was restoring
            for leftover in (root / w["path"]).parent.glob(".b3-recovery-*"):
                if stat.S_ISREG(os.lstat(leftover).st_mode):
                    os.unlink(leftover)
        unexplained = sorted(_dirty_paths(root) - recorded)
        if unexplained:
            raise Refused("blocked_unexplained_change", ", ".join(unexplained[:5]))
    else:
        if confirm_target != p["target_id"]:
            raise Refused("confirm_target_mismatch", "take target_id from a plan of the current install root")
        writes, deletes = p["writes"], p["deletes"]
        _write_new(jdir / "apply.json", {"schema": APPLY_SCHEMA, "state": "started", "at": _now(),
                                         "target_id": p["target_id"], "base_head": p["head"],
                                         "snapshot_sha256": snapshot_sha256, "writes": writes, "deletes": deletes})
    blobs = snap["_dir"] / "blobs"
    try:
        for w in writes:
            now = _current_sha(root, w["path"])
            if now == w["post"]:
                st = os.lstat(root / w["path"])
                if bool(st.st_mode & 0o100) == (w["mode"] == "100755"):
                    continue
            elif now != w["pre"]:
                raise Refused("blocked_concurrent_change", w["path"])
            _restore_file(root, w["path"], (blobs / w["post"]).read_bytes(), w["mode"])
        for d in deletes:
            now = _current_sha(root, d["path"])
            if now is None:
                continue
            if now != d["pre"]:
                raise Refused("blocked_concurrent_change", d["path"])
            os.unlink(root / d["path"])
        paths = "\0".join(sorted({w["path"] for w in writes} | {d["path"] for d in deletes})) + "\0"
        _git(root, "add", "-A", "--pathspec-from-file=-", "--pathspec-file-nul", inp=paths.encode())
        if _git(root, "write-tree").strip() != snap["git"]["tree"]:
            raise Refused("restored_tree_mismatch", "index tree differs from the frozen tree")
        _git(root, "-c", "user.name=b3-first-upgrade-recovery", "-c", "user.email=recovery@localhost",
             "commit", "-q", "--no-verify", "-m",
             f"b3 first-upgrade recovery: restore frozen {snap['version']} tree {snap['git']['tree']}\n\n"
             f"snapshot sha256 {snapshot_sha256}; from {p['head']['commit']}")
    except BaseException as e:   # record, never convert into success; the original error propagates
        n = len(list(jdir.glob("failed-*.json"))) + 1
        _write_new(jdir / f"failed-{n}.json", {"schema": APPLY_SCHEMA, "state": "failed", "at": _now(),
                                               "error": f"{type(e).__name__}: {e}"[:2000]})
        raise
    head = _head(root)
    if head["tree"] != snap["git"]["tree"]:
        raise Refused("restored_tree_mismatch", head["tree"])
    _clean(root)
    _write_new(jdir / "completed.json", {"schema": APPLY_SCHEMA, "state": "completed", "at": _now(), "head": head})
    after = plan(install_root, snapshot_dir, snapshot_sha256)
    return {k: v for k, v in after.items() if k != "_snapshot"} | {"result": "restored", "restore_commit": head["commit"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--install-root", required=True)
    f.add_argument("--snapshot-dir", required=True)
    f.add_argument("--external-review-id")
    f.add_argument("--package")
    for name in ("plan", "apply"):
        x = sub.add_parser(name)
        x.add_argument("--install-root", required=True)
        x.add_argument("--snapshot-dir", required=True)
        x.add_argument("--snapshot-sha256", required=True)
        if name == "apply":
            x.add_argument("--confirm-target", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "freeze":
            out = freeze(a.install_root, a.snapshot_dir, review_id=a.external_review_id, package=a.package)
        elif a.cmd == "plan":
            out = {k: v for k, v in plan(a.install_root, a.snapshot_dir, a.snapshot_sha256).items() if k != "_snapshot"}
        else:
            out = apply(a.install_root, a.snapshot_dir, a.snapshot_sha256, a.confirm_target)
    except Refused as e:
        print(json.dumps({"result": "refused", "code": e.code, "detail": e.detail}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
