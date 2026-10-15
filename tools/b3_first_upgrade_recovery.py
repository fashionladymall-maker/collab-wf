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
import importlib.util
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
    """Absolute, alias-free (no symlink component), existing real directory.

    The path is judged exactly as given: realpath of the raw string (a symlink component followed by '..'
    resolves as the operating system resolves it) must equal its lexical normalisation. Counterexample
    (2026-10-16): '<hop>/..' with hop -> <install>/wflib was normalised to the hop's parent first.
    """
    raw = os.fspath(path)
    if not os.path.isabs(raw):
        raise Refused(f"{what}_not_absolute", raw)
    norm, real = os.path.normpath(raw), os.path.realpath(raw)
    if real != norm:
        raise Refused(f"{what}_path_alias", f"{raw} -> {real}")
    try:
        st = os.lstat(raw)
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


def _head(root: Path) -> dict:
    return {"commit": _git(root, "rev-parse", "--verify", "HEAD^{commit}").strip(),
            "tree": _git(root, "rev-parse", "--verify", "HEAD^{tree}").strip()}


def _identity(root: Path) -> dict:
    st = os.lstat(root)
    return {"dev": st.st_dev, "ino": st.st_ino}


def _forms(p) -> set:
    """Lexical and canonical forms of a configured absolute path; both take part in every boundary check.

    The canonical form is realpath of the path exactly as configured, so a symlink component followed by
    '..' resolves the way the operating system and core resolve it. The lexical form is a separate
    normalisation of the same raw string and is never fed back into realpath. Counterexample (2026-10-16):
    WF_LOCAL_DIR '<local-hop>/../docs' with local-hop -> <install>/wflib was normalised to '<base>/docs'
    before realpath, while core used <install>/docs; plan/apply rewrote five docs files.
    """
    raw = os.fspath(p)
    if not os.path.isabs(raw):
        raise Refused("path_not_absolute", raw)
    return {Path(os.path.normpath(raw)), Path(os.path.realpath(raw))}


def _registry_projects(local: Path) -> list:
    """Registered project roots exactly as recorded; the registry protocol is {"projects": [{"root": abs}, ...]}.

    Counterexamples (2026-10-16): {} was read as zero projects, and relative or empty roots were resolved
    against the process cwd. projects is required, every root must be a non-empty absolute path; other
    fields (name, added) stay free for compatibility with registries written by core.register.
    """
    reg = local / "registry.json"
    if not os.path.lexists(reg):
        return []
    if not stat.S_ISREG(os.lstat(reg).st_mode):
        raise Refused("registry_not_regular", str(reg))
    try:
        doc = json.loads(reg.read_bytes())
    except (ValueError, UnicodeError, OSError) as e:
        raise Refused("registry_unreadable", f"{reg}: {e}"[:200]) from None
    if not isinstance(doc, dict) or not isinstance(doc.get("projects"), list):
        raise Refused("registry_unreadable", f"{reg}: top level must be an object with a projects list")
    out = []
    for i, row in enumerate(doc["projects"]):
        value = row.get("root") if isinstance(row, dict) else None
        if not isinstance(value, str) or not value or "\0" in value or not os.path.isabs(value):
            raise Refused("registry_unreadable", f"{reg}: projects[{i}].root must be a non-empty absolute path")
        out.append(Path(value))   # kept as recorded: core resolves it with OS semantics (symlink, then '..')
    return out


def _state_roots(root: Path) -> list:
    """Runtime state the runner must never write, kept in lexical form (canonical forms derive from it)."""
    roots = [("install_local", root / "local")]
    raw = os.environ.get("WF_LOCAL_DIR")
    if raw is None:
        local = root / "local"
    else:
        if not raw or not os.path.isabs(raw):   # core would resolve it against the cwd
            raise Refused("wf_local_not_absolute", repr(raw))
        local = Path(raw)   # kept as configured: core reads LOCAL through the raw path (symlink, then '..')
        if _forms(local) != _forms(root / "local"):
            roots.append(("wf_local", local))
    for proj in _registry_projects(local):
        if any(_overlap(a, b) for a in _forms(proj) for b in _forms(root)):
            raise Refused("project_overlaps_install_root", str(proj))
        roots.append(("project_workflow", proj / "workflow"))
    return roots


def _check_runtime_layout(root: Path, runtime: list):
    """No runtime state root may overlap installed code; only the lexical root/local/ is excluded from code.

    Counterexample (2026-10-16): WF_LOCAL_DIR = <install root>/docs; plan and apply succeeded, five docs
    files were rewritten or deleted, and the plan still reported never_written_by_runner. Inside, equal and
    containing positions are refused, in lexical and canonical form, before anything is created or written.
    A conflicting layout is refused, never repaired: no state is moved or deleted.
    """
    excluded = Path(os.path.normpath(str(root / "local")))
    code = _forms(root)
    for kind, prot in runtime:
        for f in _forms(prot):
            if f == excluded or excluded in f.parents:
                continue
            for r in code:
                if _overlap(f, r):
                    raise Refused("protected_state_overlaps_install_code", f"{kind} {f} / install root {r}")


def _check_snapshot_boundary(snap: Path, root: Path, runtime: list):
    """The snapshot (manifest, blobs, journal) must neither lie in nor contain the install root or any runtime root.

    Counterexamples (2026-10-16): a snapshot inside WF_LOCAL_DIR was frozen; a frozen snapshot moved into it
    received journal records; and WF_LOCAL_DIR configured as a symlink *inside* the snapshot passed because
    only canonical forms were compared. Lexical and canonical forms of both sides are compared.
    """
    for kind, prot in [("install_root", root)] + runtime:
        for pf in _forms(prot):
            for sf in _forms(snap):
                if _overlap(sf, pf):
                    raise Refused("snapshot_overlaps_protected_state", f"{kind} {pf} / snapshot {sf}")


def _check_write_set(root: Path, runtime: list, derived: dict, snapshot_sha256: str, base_tree: str):
    """Every path the restore may create, replace, delete or clean must lie outside every runtime root."""
    rels = {w["path"] for w in derived["writes"]} | {d["path"] for d in derived["deletes"]}
    rels |= {_temp_rel(snapshot_sha256, base_tree, w["path"]) for w in derived["writes"]}
    rels |= {"/".join(w["path"].split("/")[:i]) for w in derived["writes"] for i in range(1, w["path"].count("/") + 1)}
    prot = [(kind, f) for kind, p in runtime for f in _forms(p)]
    for rel in sorted(rels):
        for t in _forms(root / rel):
            for kind, f in prot:
                if t == f or f in t.parents:
                    raise Refused("restore_writes_protected_state", f"{rel} -> {kind} {f}")


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

def _qualified_old_source(root: Path, files: dict, rows: list) -> dict:
    """The old install must pass its own release manifest before it can be frozen as a recovery source.

    Counterexample (2026-10-16): MANIFEST.files.VERSION = 64 zeros was recorded as a mismatch and still
    frozen. Every listed file must exist with that sha256, every tracked file except MANIFEST.json must
    be listed, and the manifest version must equal VERSION.
    """
    if "MANIFEST.json" not in files or "VERSION" not in files:
        raise Refused("old_source_manifest_missing", "MANIFEST.json and VERSION must be tracked")
    try:
        doc = json.loads(_regular_under(root, "MANIFEST.json").read_bytes())
        listed, mver = doc["files"], doc["version"]
        if not isinstance(listed, dict) or not isinstance(mver, str) \
                or any(not isinstance(k, str) or not isinstance(v, str) for k, v in listed.items()):
            raise ValueError("files must map paths to sha256 strings")
    except (ValueError, KeyError, TypeError, UnicodeError) as e:
        raise Refused("old_source_manifest_corrupt", str(e)[:200]) from None
    by = {r["path"]: r["sha256"] for r in rows}
    version = _regular_under(root, "VERSION").read_text(encoding="utf-8").strip()
    check = {"listed": len(listed), "version": mver,
             "mismatch": sorted(p for p, h in listed.items() if by.get(p) != h),
             "unlisted": sorted(set(by) - {"MANIFEST.json"} - set(listed)),
             "version_matches": mver == version}
    if check["mismatch"] or check["unlisted"] or not check["version_matches"]:
        raise Refused("old_source_manifest_mismatch", json.dumps(check, sort_keys=True)[:600])
    return check


def freeze(install_root, snapshot_dir, *, review_id=None, package=None) -> dict:
    root = _directory(install_root, "install_root")
    raw = os.fspath(snapshot_dir)
    if not os.path.isabs(raw):
        raise Refused("snapshot_not_absolute", raw)
    snap = Path(os.path.normpath(raw))
    if os.path.realpath(raw) != str(snap):   # judged as given: a symlink component before '..' is an alias
        raise Refused("snapshot_path_alias", f"{raw} -> {os.path.realpath(raw)}")
    parent = _directory(snap.parent, "snapshot_parent")
    snap = parent / snap.name
    if _overlap(snap, root):
        raise Refused("snapshot_overlaps_install_root", f"{snap} / {root}")
    runtime = _state_roots(root)
    _check_runtime_layout(root, runtime)
    _check_snapshot_boundary(snap, root, runtime)
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
    manifest_check = _qualified_old_source(root, files, rows)
    residue = _disk_vs(_code_inventory(root), {r["path"]: (r["sha256"], r["mode"]) for r in rows})
    if residue:
        raise Refused("unexplained_code_residue", ", ".join(residue[:8]))
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
    bd = snap / "blobs"
    if not os.path.lexists(bd) or not stat.S_ISDIR(os.lstat(bd).st_mode):
        raise Refused("snapshot_blobs_not_directory", str(bd))
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


# ---------------- trusted derivation ----------------
# The recovery plan is derived only from trusted inputs: the frozen manifest (bound by the
# --snapshot-sha256 recorded at freeze time) and content-addressed git objects of the current
# HEAD tree. The journal is a mutable, untrusted copy of that plan: it is accepted only if it is
# byte-for-byte the derived plan, and it is fully validated before anything is written,
# deleted or cleaned up.

def _git_raw(root: Path, *args, inp=None) -> bytes:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_ENV)
    r = subprocess.run(["git", *GIT_CFG, *args], cwd=str(root), env=env, input=inp, capture_output=True)
    if r.returncode != 0:
        raise Refused("git_failed", f"git {' '.join(args[:3])}: {r.stderr.decode(errors='replace').strip()[:300]}")
    return r.stdout


def _tree_listing(root: Path, tree: str) -> dict:
    """path -> (mode, blob) of a git tree object; independent of the index and of the disk."""
    out = {}
    for row in filter(None, _git_raw(root, "ls-tree", "-r", "-z", "--full-tree", tree).split(b"\0")):
        meta, path = row.split(b"\t", 1)
        mode, kind, blob = meta.decode("ascii").split(" ")
        path = path.decode("utf-8")
        if path.split("/")[0] in PROTECTED:
            raise Refused("protected_state_tracked", path)
        if kind != "blob" or mode not in ("100644", "100755"):
            raise Refused("unsupported_tracked_mode", f"{mode} {path}")
        out[_safe_rel(path)] = (mode, blob)
    return out


def _blob_sha256(root: Path, blobs) -> dict:
    """sha256 of git blob contents, read and re-hashed from the object store in one batch."""
    blobs = sorted(set(blobs))
    if not blobs:
        return {}
    raw = _git_raw(root, "cat-file", "--batch", inp=("\n".join(blobs) + "\n").encode("ascii"))
    out, pos = {}, 0
    for b in blobs:
        nl = raw.index(b"\n", pos)
        head = raw[pos:nl].decode("ascii", errors="replace").split(" ")
        if len(head) != 3 or head[0] != b or head[1] != "blob":
            raise Refused("git_object_unavailable", b)
        size = int(head[2])
        data = raw[nl + 1:nl + 1 + size]
        if len(data) != size or _git_blob(data) != b:
            raise Refused("git_object_corrupt", b)
        out[b] = _sha256(data)
        pos = nl + 1 + size + 1
    return out


def _derive_plan(snap: dict, base: dict, base_sha: dict) -> dict:
    frozen = {r["path"]: r for r in snap["files"]}
    writes = [{"path": p, "mode": r["mode"], "post": r["sha256"],
               "pre": base_sha[base[p][1]] if p in base else None}
              for p, r in sorted(frozen.items()) if base.get(p) != (r["mode"], r["git_blob"])]
    deletes = [{"path": p, "pre": base_sha[b]} for p, (_m, b) in sorted(base.items()) if p not in frozen]
    return {"writes": writes, "deletes": deletes}


def _plan_sha256(snapshot_sha256: str, target_id: str, base_head: dict, derived: dict) -> str:
    return _sha256(_canon({"snapshot_sha256": snapshot_sha256, "target_id": target_id, "base_head": base_head,
                           "writes": derived["writes"], "deletes": derived["deletes"]}))


def _temp_rel(snapshot_sha256: str, base_tree: str, rel: str) -> str:
    """The only temp name this runner may create or clean up for rel: registered by derivation."""
    name = ".b3-recovery-" + _sha256(_canon([snapshot_sha256, base_tree, rel]))[:32] + ".tmp"
    return rel.rsplit("/", 1)[0] + "/" + name if "/" in rel else name


# ---------------- complete disk inventory ----------------

def _code_inventory(root: Path) -> dict:
    """Every path below the install root except the protected .git/ and local/, ignore rules notwithstanding.

    Allowed non-code: *.pyc inside a __pycache__ directory (interpreter cache, not importable without
    its source). Everything else that is not a frozen code file is residue; it is reported and refused,
    never deleted.
    """
    code, cache, odd = {}, [], []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        rel_dir = here.relative_to(root)
        if here == root:
            dirnames[:] = [d for d in dirnames if d not in PROTECTED]
        for d in list(dirnames):
            if not stat.S_ISDIR(os.lstat(here / d).st_mode):
                odd.append((rel_dir / d).as_posix())
                dirnames.remove(d)
        for name in filenames:
            fp = here / name
            rel = (rel_dir / name).as_posix()
            st = os.lstat(fp)
            if not stat.S_ISREG(st.st_mode):
                odd.append(rel)
            elif "__pycache__" in rel.split("/")[:-1] and name.endswith(".pyc"):
                cache.append(rel)
            else:
                code[rel] = (st, fp)
    return {"code": code, "cache": sorted(cache), "non_regular": sorted(odd)}


def _bytecode_context() -> dict:
    """The interpreter and cache context a plan's restart conclusion is established for: this runner's own.

    Bytecode caches are looked up where *this* interpreter, with *this* environment, would read them.
    Another interpreter, cache tag, pycache_prefix or HOME (Apple's default prefix lives under HOME) is a
    different context for which nothing is established.
    """
    return {"executable": sys.executable, "version": sys.version.split()[0],
            "cache_tag": sys.implementation.cache_tag, "pycache_prefix": sys.pycache_prefix,
            "HOME": os.environ.get("HOME"), "PYTHONPYCACHEPREFIX": os.environ.get("PYTHONPYCACHEPREFIX")}


def _context_bytecode(root: Path, inv: dict) -> tuple:
    """Existing caches this interpreter would read for installed sources, outside the install root.

    Counterexample (2026-10-16, Apple Python 3.9.6 on m4): the default sys.pycache_prefix
    ($HOME/Library/Caches/com.apple.python) holds caches outside the install root and without a
    __pycache__ segment; an UNCHECKED_HASH cache there ran a marker on plain and -B imports while the
    install-root scan found nothing and the plan said operator_may_restart_old_code. Only the exact
    cache_from_source paths of installed sources (optimisation '', 1, 2) are inspected; nothing in HOME
    or elsewhere is enumerated, and nothing is deleted. Caches inside the install root are covered by the
    install-root scan. An entry that cannot be inspected leaves the context not established.
    """
    found, problems = [], []
    if sys.implementation.cache_tag is None:
        return found, problems
    for rel in sorted(inv["code"]):
        if not rel.endswith(".py"):
            continue
        for opt in ("", 1, 2):
            try:
                cache = Path(importlib.util.cache_from_source(str(root / rel), optimization=opt))
            except (NotImplementedError, ValueError) as e:
                problems.append(f"{rel}: {type(e).__name__}")
                continue
            if _overlap(cache, root) or root in cache.parents:
                continue
            try:
                st = os.lstat(cache)
            except FileNotFoundError:
                continue
            except OSError as e:
                problems.append(f"{cache}: {type(e).__name__}")
                continue
            if not stat.S_ISREG(st.st_mode):
                problems.append(f"{cache}: not a regular file")
                continue
            try:
                digest = _sha256(cache.read_bytes())
            except OSError as e:
                problems.append(f"{cache}: {type(e).__name__}")
                continue
            found.append({"path": str(cache), "source": rel, "location": "pycache_prefix", "sha256": digest})
    return found, problems


def _disk_vs(inv: dict, expect: dict) -> list:
    """Compare the full inventory with path -> (sha256, mode); exec bit compared as git does."""
    out = [f"non_regular:{p}" for p in inv["non_regular"]]
    out += [f"extra:{p}" for p in sorted(set(inv["code"]) - set(expect))]
    out += [f"missing:{p}" for p in sorted(set(expect) - set(inv["code"]))]
    for rel in sorted(set(expect) & set(inv["code"])):
        st, fp = inv["code"][rel]
        sha, mode = expect[rel]
        if _sha256(fp.read_bytes()) != sha:
            out.append(f"content:{rel}")
        elif bool(st.st_mode & 0o100) != (mode == "100755"):
            out.append(f"mode:{rel}")
    return out


def _check_resume_disk(inv, base, base_sha, derived, blobs_dir: Path, snapshot_sha256, base_tree) -> list:
    """An interrupted apply: every file must be explained by the derived plan; returns registered temps."""
    writes = {w["path"]: w for w in derived["writes"]}
    deletes = {d["path"]: d for d in derived["deletes"]}
    temps = {_temp_rel(snapshot_sha256, base_tree, p): w for p, w in writes.items()}
    concurrent, problems, found = [], [f"non_regular:{p}" for p in inv["non_regular"]], []
    for rel, (st, fp) in sorted(inv["code"].items()):
        data = fp.read_bytes()
        if rel in temps:   # a temp this runner registered for exactly one write; must be a prefix of that blob
            if not (blobs_dir / temps[rel]["post"]).read_bytes().startswith(data):
                problems.append(f"foreign_temp:{rel}")
            found.append(rel)
            continue
        # (content, git mode) of every file; type and set are covered by the inventory itself.
        # Counterexample (2026-10-16): VERSION chmod 0755 outside the writes passed, the restore
        # commit was made, and only the final status check failed.
        now = (_sha256(data), "100755" if st.st_mode & 0o100 else "100644")
        if rel in writes:
            w = writes[rel]
            allowed = {(w["post"], w["mode"])} | ({(w["pre"], base[rel][0])} if rel in base else set())
            if now not in allowed:
                concurrent.append(rel)
        elif rel in deletes:
            if now != (deletes[rel]["pre"], base[rel][0]):
                concurrent.append(rel)
        elif rel in base:
            want = (base_sha[base[rel][1]], base[rel][0])
            if now != want:
                problems.append(f"{'content' if now[0] != want[0] else 'mode'}:{rel}")
        else:
            problems.append(f"extra:{rel}")
    concurrent += [p for p, w in writes.items() if p not in inv["code"] and w["pre"] is not None]
    problems += [f"missing:{p}" for p in sorted(set(base) - set(writes) - set(deletes) - set(inv["code"]))]
    if concurrent:
        raise Refused("blocked_concurrent_change", ", ".join(sorted(concurrent)[:8]))
    if problems:
        raise Refused("blocked_unexplained_change", ", ".join(problems[:8]))
    return sorted(found)


# ---------------- journal (untrusted) ----------------

_APPLY_KEYS = {"schema", "state", "at", "target_id", "base_head", "snapshot_sha256", "plan_sha256", "writes", "deletes"}


def _jdir(snap_dir: Path, create=False):
    j = snap_dir / "journal"
    if not os.path.lexists(j):
        if not create:
            return None
        os.mkdir(j, 0o700)
    if not stat.S_ISDIR(os.lstat(j).st_mode):   # lstat: a symlinked journal is refused, never followed
        raise Refused("journal_not_directory", str(j))
    return j


def _journal_read(snap_dir: Path) -> dict:
    out = {"failed": []}
    j = _jdir(snap_dir)
    if j is None:
        return out
    for entry in sorted(os.listdir(j)):
        if not stat.S_ISREG(os.lstat(j / entry).st_mode):
            raise Refused("journal_corrupt", f"non-regular journal entry {entry}")
        if entry in ("apply.json", "completed.json"):
            try:
                doc = json.loads((j / entry).read_bytes())
            except (ValueError, UnicodeError) as e:
                raise Refused("journal_corrupt", f"{entry}: {e}"[:200]) from None
            if not isinstance(doc, dict):
                raise Refused("journal_corrupt", entry)
            out[entry] = doc
        elif re.fullmatch(r"failed-[0-9]+\.json", entry):
            out["failed"].append(entry)
        else:
            raise Refused("journal_corrupt", f"unknown journal entry {entry}")
    return out


def _journal_write(snap_dir: Path, name: str, doc: dict):
    j = _jdir(snap_dir, create=True)
    dfd = os.open(j, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o400, dir_fd=dfd)
        with os.fdopen(fd, "wb") as f:
            f.write(_canon(doc))
            f.flush()
            os.fsync(f.fileno())
    finally:
        os.close(dfd)


def _journal_path(rel) -> str:
    try:
        return _safe_rel(rel)
    except Refused:
        raise Refused("journal_unsafe_path", str(rel)[:200]) from None


def _check_apply_record(rec: dict, *, root, ident, head, snapshot_sha256, derived):
    if set(rec) != _APPLY_KEYS or rec.get("schema") != APPLY_SCHEMA or rec.get("state") != "started" \
            or not isinstance(rec.get("at"), str):
        raise Refused("journal_corrupt", "apply.json schema/keys")
    if not isinstance(rec["base_head"], dict) or set(rec["base_head"]) != {"commit", "tree"}:
        raise Refused("journal_corrupt", "apply.json base_head")
    seen = set()
    for kind, keys in (("writes", {"path", "mode", "pre", "post"}), ("deletes", {"path", "pre"})):
        if not isinstance(rec[kind], list):
            raise Refused("journal_corrupt", f"apply.json {kind}")
        for row in rec[kind]:
            if not isinstance(row, dict) or set(row) != keys or not isinstance(row["path"], str):
                raise Refused("journal_corrupt", f"apply.json {kind} record")
            rel = _journal_path(row["path"])
            if rel in seen:
                raise Refused("journal_duplicate_path", rel)
            seen.add(rel)
    if rec["snapshot_sha256"] != snapshot_sha256:
        raise Refused("journal_plan_mismatch", "snapshot_sha256")
    if rec["base_head"] != head:
        raise Refused("blocked_head_moved_during_interrupted_apply", head["commit"])
    if rec["target_id"] != _target_id(root, ident, head, snapshot_sha256):
        raise Refused("journal_plan_mismatch", "target_id does not match root identity, base head and snapshot")
    if rec["writes"] != derived["writes"] or rec["deletes"] != derived["deletes"]:
        raise Refused("journal_plan_mismatch", "writes/deletes differ from the plan derived from the frozen "
                                               "manifest and the base tree")
    if rec["plan_sha256"] != _plan_sha256(snapshot_sha256, rec["target_id"], head, derived):
        raise Refused("journal_plan_mismatch", "plan_sha256")


def _check_completed(rec: dict, head: dict, snapshot_sha256: str):
    if rec.get("schema") != APPLY_SCHEMA or rec.get("state") != "completed" or rec.get("head") != head \
            or rec.get("snapshot_sha256", snapshot_sha256) != snapshot_sha256:
        raise Refused("journal_corrupt", "completed.json does not describe the current restored head")


def _current_sha(root, rel):
    p = _regular_under(root, rel, may_be_absent=True)
    return None if p is None else _sha256(p.read_bytes())


def _public(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}


# ---------------- plan ----------------

def plan(install_root, snapshot_dir, snapshot_sha256) -> dict:
    snap = _load_snapshot(snapshot_dir, snapshot_sha256)
    root = _directory(install_root, "install_root")
    ident = _identity(root)
    if str(root) != snap["install_root"] or ident != snap["root_identity"]:
        raise Refused("install_root_identity_mismatch", str(root))
    if _overlap(snap["_dir"], root):
        raise Refused("snapshot_overlaps_install_root", str(snap["_dir"]))
    runtime = _state_roots(root)   # plan precedes every apply step, including journal writes
    _check_runtime_layout(root, runtime)
    _check_snapshot_boundary(snap["_dir"], root, runtime)
    _repo(root)
    journal = _journal_read(snap["_dir"])
    head = _head(root)
    base = _tree_listing(root, head["tree"])
    base_sha = _blob_sha256(root, [b for _m, b in base.values()])
    derived = _derive_plan(snap, base, base_sha)
    _check_write_set(root, runtime, derived, snapshot_sha256, head["tree"])
    frozen = {r["path"]: (r["sha256"], r["mode"]) for r in snap["files"]}
    inv = _code_inventory(root)
    temps = []
    if head["tree"] == snap["git"]["tree"]:
        # Never trust HEAD/index alone: the disk must be the frozen code, byte for byte, with no residue.
        drift = _disk_vs(inv, frozen)
        if drift:
            raise Refused("restored_disk_drift", ", ".join(drift[:8]))
        _clean(root)
        code = "already_restored"
    elif "apply.json" in journal:
        _check_apply_record(journal["apply.json"], root=root, ident=ident, head=head,
                            snapshot_sha256=snapshot_sha256, derived=derived)
        temps = _check_resume_disk(inv, base, base_sha, derived, snap["_dir"] / "blobs", snapshot_sha256, head["tree"])
        code = "resume_interrupted_apply"
    else:
        _clean(root)
        residue = _disk_vs(inv, {p: (base_sha[b], m) for p, (m, b) in base.items()})
        if residue:
            raise Refused("unexplained_code_residue", ", ".join(residue[:8]))
        code = "restorable"
    if "completed.json" in journal:
        if code != "already_restored":
            raise Refused("completed_record_but_target_differs", head["tree"])
        _check_completed(journal["completed.json"], head, snapshot_sha256)
    restored = code == "already_restored"
    state_now = _state(root)
    state_diff = _state_diff(snap["state"], state_now)
    schema = ("unchanged_since_freeze" if not state_diff
              else "unverified: newer code wrote runtime state after freeze; old code compatibility not proven")
    target_id = _target_id(root, ident, head, snapshot_sha256)
    # Bytecode caches cannot be bound to the frozen source: an unchecked-hash pyc (or a timestamp pyc
    # with forged header fields) executes without reading the source, and -B/PYTHONDONTWRITEBYTECODE
    # still read existing caches. They are listed, kept and block "may restart"; code_recovery covers
    # source files only.
    caches = [{"path": c, "location": "install_root", "sha256": _sha256((root / c).read_bytes())}
              for c in inv["cache"]]
    context = _bytecode_context()
    external, uninspectable = _context_bytecode(root, inv)
    caches += external
    blockers = ([] if restored else ["code_recovery_not_already_restored"]) \
        + (["runtime_state_changed_since_freeze"] if state_diff else []) \
        + ([f"unbound_bytecode_cache:{len(caches)}"] if caches else []) \
        + ([f"bytecode_cache_context_not_established:{len(uninspectable)}"] if uninspectable else [])
    return {
        "result": "plan", "snapshot_sha256": snapshot_sha256, "install_root": str(root), "head": head,
        "frozen": {"git": snap["git"], "version": snap["version"], "files": len(frozen)},
        "target_id": target_id, "plan_sha256": _plan_sha256(snapshot_sha256, target_id, head, derived),
        "writes": derived["writes"], "deletes": derived["deletes"],
        "inventory": {"code_files": len(inv["code"]), "bytecode_cache_unverified": caches,
                      "bytecode_cache_uninspectable": uninspectable, "bytecode_context": context,
                      "registered_temps_found": temps},
        "journal": {k: bool(v) if k != "failed" else v for k, v in journal.items()},
        "conclusions": {
            "code_recovery": code,
            "state_preservation": {"status": "never_written_by_runner",
                                   "roots": [{"kind": s["kind"], "root": s["root"],
                                              "canonical": os.path.realpath(s["root"]), "files": len(s["files"])}
                                             for s in state_now]},
            "schema_compat": {"status": schema, "changed_since_freeze": state_diff},
            "restart": {"auto_start": False,
                        "status": "operator_may_restart_old_code" if not blockers else "not_established",
                        "blockers": blockers, "established_for": context,
                        "requires": ["code_recovery == already_restored (source files, bytes and exec bit)",
                                     "schema_compat == unchanged_since_freeze, or an explicit review of every "
                                     "changed runtime state file against the old code",
                                     "no Python bytecode cache below the install root, nor at the cache paths "
                                     "of installed sources in the established_for context (pycache_prefix): "
                                     "caches are not bound to the frozen source and are never deleted by the runner",
                                     "restart with the interpreter and environment in established_for only; "
                                     "another interpreter, cache tag, pycache_prefix or HOME is not established",
                                     "no driver/board process from the newer code still running (not checked)"]},
        },
        "external_gate": snap.get("external_gate") or {"status": "none_recorded", "enforced_by_old_host": False},
        "_snapshot": snap, "_derived": derived, "_temps": temps,
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

def _restore_file(root: Path, rel: str, data: bytes, mode: str, temp_rel: str):
    target = root / rel
    cur = root
    for part in rel.split("/")[:-1]:
        cur = cur / part
        if os.path.lexists(cur):
            if not stat.S_ISDIR(os.lstat(cur).st_mode):
                raise Refused("non_regular_path", rel)
        else:
            os.mkdir(cur, 0o755)
    tmp = root / temp_rel
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
    p = plan(install_root, snapshot_dir, snapshot_sha256)   # complete validation; nothing has been written
    snap, root = p["_snapshot"], Path(p["install_root"])
    sdir, derived, head = snap["_dir"], p["_derived"], p["head"]
    journal = _journal_read(sdir)
    if p["conclusions"]["code_recovery"] == "already_restored":
        if "apply.json" in journal and "completed.json" not in journal:
            _journal_write(sdir, "completed.json", {"schema": APPLY_SCHEMA, "state": "completed", "at": _now(),
                                                    "head": head, "snapshot_sha256": snapshot_sha256,
                                                    "note": "recorded on re-entry"})
        return _public(p) | {"result": "already_restored"}
    if "apply.json" in journal:
        if confirm_target != journal["apply.json"]["target_id"]:
            raise Refused("confirm_target_mismatch", "resume must confirm the recorded target")
    else:
        if confirm_target != p["target_id"]:
            raise Refused("confirm_target_mismatch", "take target_id from a plan of the current install root")
        _journal_write(sdir, "apply.json", {"schema": APPLY_SCHEMA, "state": "started", "at": _now(),
                                            "target_id": p["target_id"], "base_head": head,
                                            "snapshot_sha256": snapshot_sha256, "plan_sha256": p["plan_sha256"],
                                            "writes": derived["writes"], "deletes": derived["deletes"]})
    blobs = sdir / "blobs"
    try:
        for rel in p["_temps"]:   # only registered temps, validated by plan before any write
            if os.path.lexists(root / rel) and stat.S_ISREG(os.lstat(root / rel).st_mode):
                os.unlink(root / rel)
        for w in derived["writes"]:
            now = _current_sha(root, w["path"])
            if now == w["post"]:
                st = os.lstat(root / w["path"])
                if bool(st.st_mode & 0o100) == (w["mode"] == "100755"):
                    continue
            elif now != w["pre"]:
                raise Refused("blocked_concurrent_change", w["path"])
            _restore_file(root, w["path"], (blobs / w["post"]).read_bytes(), w["mode"],
                          _temp_rel(snapshot_sha256, head["tree"], w["path"]))
        for d in derived["deletes"]:
            now = _current_sha(root, d["path"])
            if now is None:
                continue
            if now != d["pre"]:
                raise Refused("blocked_concurrent_change", d["path"])
            os.unlink(root / d["path"])
        paths = "\0".join(sorted({w["path"] for w in derived["writes"]} | {d["path"] for d in derived["deletes"]})) + "\0"
        _git(root, "add", "-A", "--pathspec-from-file=-", "--pathspec-file-nul", inp=paths.encode())
        if _git(root, "write-tree").strip() != snap["git"]["tree"]:
            raise Refused("restored_tree_mismatch", "index tree differs from the frozen tree")
        _git(root, "-c", "user.name=b3-first-upgrade-recovery", "-c", "user.email=recovery@localhost",
             "commit", "-q", "--no-verify", "-m",
             f"b3 first-upgrade recovery: restore frozen {snap['version']} tree {snap['git']['tree']}\n\n"
             f"snapshot sha256 {snapshot_sha256}; from {head['commit']}")
    except BaseException as e:   # record, never convert into success; the original error propagates
        n = len(_journal_read(sdir)["failed"]) + 1
        _journal_write(sdir, f"failed-{n}.json", {"schema": APPLY_SCHEMA, "state": "failed", "at": _now(),
                                                  "error": f"{type(e).__name__}: {e}"[:2000]})
        raise
    new_head = _head(root)
    if new_head["tree"] != snap["git"]["tree"]:
        raise Refused("restored_tree_mismatch", new_head["tree"])
    _clean(root)
    drift = _disk_vs(_code_inventory(root), {r["path"]: (r["sha256"], r["mode"]) for r in snap["files"]})
    if drift:
        raise Refused("restored_disk_drift", ", ".join(drift[:8]))
    _journal_write(sdir, "completed.json", {"schema": APPLY_SCHEMA, "state": "completed", "at": _now(),
                                            "head": new_head, "snapshot_sha256": snapshot_sha256,
                                            "plan_sha256": p["plan_sha256"]})
    after = plan(install_root, snapshot_dir, snapshot_sha256)
    return _public(after) | {"result": "restored", "restore_commit": new_head["commit"]}


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
            out = _public(plan(a.install_root, a.snapshot_dir, a.snapshot_sha256))
        else:
            out = apply(a.install_root, a.snapshot_dir, a.snapshot_sha256, a.confirm_target)
    except Refused as e:
        print(json.dumps({"result": "refused", "code": e.code, "detail": e.detail}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
