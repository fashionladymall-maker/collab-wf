"""B3 first-upgrade recovery drill: real 1.9.7 installer, real candidate, isolated copies.

Old code: git tree fafef7e6 (native source 818ecd95, carried by base/tool-818ecd9 as
fd3f22d). The install root is that tree extracted from git objects and initialised
by the old ``wf.py self-init`` itself, which must reproduce the same tree id. The
candidate package is the integration tree 7b3b6cef (VERSION is still 1.9.7, so the
old installer's own --allow-same is used; --skip-test skips only the old installer's
"run the candidate selftest" step). Everything runs with a private HOME, WF_LOCAL_DIR,
install root and two projects; no credentials, no adapter, nothing is started.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "b3_first_upgrade_recovery.py"
OLD_TREE = "fafef7e6007d544aa897890b7271eae5cf29b051"
OLD_NATIVE_COMMIT = "818ecd959f86132c4292be9e426ae1aa2435cf4e"
CANDIDATE_TREE = os.environ.get("B3_CANDIDATE_TREE", "7b3b6cef8f3452c794e861d230243ff7bda3ef28")
KILL_AFTER_3 = r'''import importlib.util, os, signal, sys
spec = importlib.util.spec_from_file_location("rec", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
real, n = m._restore_file, [0]
def die(*a, **k):
    real(*a, **k); n[0] += 1
    if n[0] == 3:
        os.kill(os.getpid(), signal.SIGKILL)
m._restore_file = die
sys.exit(m.main(sys.argv[2:]))
'''


KILL_IN_REPLACE = r'''import importlib.util, os, signal, sys
spec = importlib.util.spec_from_file_location("rec", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
real, n = os.replace, [0]
def die(src, dst):
    n[0] += 1
    if n[0] == 3:   # third restore: temp written and fsynced, rename not yet done
        os.kill(os.getpid(), signal.SIGKILL)
    return real(src, dst)
m.os.replace = die
sys.exit(m.main(sys.argv[2:]))
'''


def _runner():
    import importlib.util
    spec = importlib.util.spec_from_file_location("b3_first_upgrade_recovery", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _code_bytes(path: Path) -> dict:
    """Every non-.git entry with content hash and mode (symlinks by target): byte-preservation checks."""
    out = {}
    for p in sorted(path.rglob("*")):
        rel = p.relative_to(path)
        if rel.parts[0] == ".git":
            continue
        if p.is_symlink():
            out[str(rel)] = "symlink:" + os.readlink(p)
        elif p.is_file():
            out[str(rel)] = hashlib.sha256(p.read_bytes()).hexdigest() + oct(p.stat().st_mode & 0o777)
    return out


def _git_has(obj):
    return subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", obj + "^{tree}"], capture_output=True).returncode == 0


def _tree_bytes(path: Path) -> dict:
    out = {}
    if path.exists():
        for p in sorted(path.rglob("*")):
            if p.is_file() and not p.is_symlink():
                out[str(p.relative_to(path))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class FirstUpgradeRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [t for t in (OLD_TREE, CANDIDATE_TREE) if not _git_has(t)]
        if missing:
            raise unittest.SkipTest(f"git objects unavailable (fetch base/tool-818ecd9 and the candidate): {missing}")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wf-b3-first-upgrade-")
        self.addCleanup(self.temp.cleanup)
        b = self.base = Path(os.path.realpath(self.temp.name))
        self.home, self.local, self.root = b / "home", b / "local", b / "install" / "collab-wf"
        self.snap = b / "snapshots" / "first-upgrade"
        for d in (self.home, self.local, self.root, self.snap.parent):
            d.mkdir(parents=True)
        (self.home / ".gitconfig").write_text("[user]\n\tname = drill\n\temail = drill@localhost\n")
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home),
                    "WF_LOCAL_DIR": str(self.local), "WF_NOTIFY": "0", "LC_ALL": "C.UTF-8"}
        arc = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", OLD_TREE], capture_output=True, check=True)
        subprocess.run(["tar", "-x", "-C", str(self.root)], input=arc.stdout, check=True)
        self.wf("self-init")
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}"), OLD_TREE)   # exactly the native old tree
        self.old_commit = self.git("rev-parse", "HEAD")
        for name in ("p1", "p2"):
            (b / name).mkdir()
            self.wf("init", "--name", name, "--root", str(b / name))

    # ---------------- helpers ----------------

    def wf(self, *args, check=True):
        r = subprocess.run([sys.executable, str(self.root / "wf.py"), *args], cwd=str(self.base), env=self.env,
                           capture_output=True, text=True, timeout=300)
        if check and r.returncode != 0:
            self.fail(f"wf {' '.join(args)} -> {r.returncode}\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
        return r

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], env=self.env, capture_output=True, text=True,
                              check=True).stdout.strip()

    def tool(self, *args, script=None):
        argv = [sys.executable, "-c", script, str(TOOL), *args] if script else [sys.executable, str(TOOL), *args]
        r = subprocess.run(argv, env=self.env, capture_output=True, text=True, timeout=300)
        try:
            out = json.loads(r.stdout) if r.stdout.strip() else None
        except ValueError:
            out = None
        return r.returncode, out, r

    def freeze(self, **kw):
        extra = sum(([f"--{k.replace('_', '-')}", str(v)] for k, v in kw.items()), [])
        rc, out, r = self.tool("freeze", "--install-root", str(self.root), "--snapshot-dir", str(self.snap), *extra)
        self.assertEqual((rc, out and out["result"]), (0, "frozen"), r.stderr[-1500:])
        return out["snapshot_sha256"]

    def upgrade(self):
        pkg = self.base / "candidate.zip"
        subprocess.run(["git", "-C", str(REPO), "archive", "--format=zip", "--prefix=collab-wf/", "-o", str(pkg),
                        CANDIDATE_TREE], check=True)
        r = self.wf("self-update", str(pkg), "--allow-same", "--skip-test")
        self.assertTrue((self.root / "wflib" / "install_history.py").exists(), r.stdout)
        self.assertNotEqual(self.git("rev-parse", "HEAD^{tree}"), OLD_TREE)
        return pkg

    def state(self):
        return {str(p): _tree_bytes(p) for p in (self.root / "local", self.local, self.base / "p1" / "workflow",
                                                  self.base / "p2" / "workflow")}

    def everything(self):
        """Install code (ignored files included), protected and project state, HEAD and journal bytes."""
        j = self.snap / "journal"
        return {"code": _code_bytes(self.root), "state": self.state(), "head": self.git("rev-parse", "HEAD"),
                "journal": _code_bytes(j) if j.is_dir() and not j.is_symlink() else None}

    def refused(self, cmd, sha, target=None):
        args = [cmd, "--install-root", str(self.root), "--snapshot-dir", str(self.snap), "--snapshot-sha256", sha]
        if cmd == "apply":
            args += ["--confirm-target", target]
        rc, out, r = self.tool(*args)
        self.assertEqual(rc, 2, r.stdout[-800:] + r.stderr[-800:])
        return out["code"], out.get("detail", "")

    def plan(self, sha):
        rc, out, r = self.tool("plan", "--install-root", str(self.root), "--snapshot-dir", str(self.snap),
                               "--snapshot-sha256", sha)
        self.assertEqual(rc, 0, r.stdout + r.stderr)
        return out

    def apply(self, sha, target, script=None):
        return self.tool("apply", "--install-root", str(self.root), "--snapshot-dir", str(self.snap),
                         "--snapshot-sha256", sha, "--confirm-target", target, script=script)

    # ---------------- drill ----------------

    def test_real_first_upgrade_gap_then_recovery_from_frozen_tree(self):
        pkg = self.base / "candidate.zip"
        subprocess.run(["git", "-C", str(REPO), "archive", "--format=zip", "--prefix=collab-wf/", "-o", str(pkg),
                        CANDIDATE_TREE], check=True)
        sha = self.freeze(external_review_id="rq-external-drill", package=pkg)
        frozen = json.loads((self.snap / "manifest.json").read_bytes())
        self.assertEqual((frozen["git"]["tree"], frozen["version"], frozen["manifest_check"]["mismatch"]),
                         (OLD_TREE, "1.9.7", []))
        self.assertFalse(frozen["external_gate"]["enforced_by_old_host"])
        self.upgrade()
        upgraded = self.git("rev-parse", "HEAD")
        # The gap: the new self-rollback cannot use the old tag; nothing restored.
        rb = self.wf("self-rollback", "wf-v1.9.7", check=False)
        self.assertNotEqual(rb.returncode, 0, rb.stdout + rb.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), upgraded)
        # Newer runtime state (synthetic, labelled) must survive recovery byte for byte.
        (self.local / "shared").mkdir(exist_ok=True)
        (self.local / "shared" / "arbiter.json").write_text('{"holders": [], "synthetic_newer_schema": 1}')
        st = self.base / "p1" / "workflow" / "state.json"
        doc = json.loads(st.read_text())
        doc["send_slot"] = {"lease_id": "synthetic-newer-field", "turn": 1}
        st.write_text(json.dumps(doc))
        self.git("tag", "-f", "wf-v1.9.7", "HEAD")   # a moved tag is not evidence; the runner never reads tags
        before = self.state()
        p = self.plan(sha)
        c = p["conclusions"]
        self.assertEqual(c["code_recovery"], "restorable")
        self.assertTrue(c["schema_compat"]["status"].startswith("unverified"))
        changed = {(x["kind"], x["path"]) for x in c["schema_compat"]["changed_since_freeze"]}
        self.assertTrue({("wf_local", "shared/arbiter.json"), ("project_workflow", "state.json")} <= changed, changed)
        self.assertEqual((c["restart"]["auto_start"], c["restart"]["status"]), (False, "not_established"))
        rc, out, _ = self.apply(sha, "0" * 64)
        self.assertEqual((rc, out["code"]), (2, "confirm_target_mismatch"))
        self.assertFalse((self.snap / "journal" / "apply.json").exists())
        self.assertEqual(self.git("rev-parse", "HEAD"), upgraded)
        rc, out, r = self.apply(sha, p["target_id"])
        self.assertEqual((rc, out["result"]), (0, "restored"), r.stdout[-1500:] + r.stderr[-1500:])
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}"), OLD_TREE)
        self.assertEqual(self.git("rev-parse", "HEAD^"), upgraded)
        self.assertEqual(self.git("rev-parse", "wf-v1.9.7"), upgraded)   # tag untouched
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all"), "")
        self.assertEqual(self.state(), before)
        # VERSION is 1.9.7 on both sides, so it cannot discriminate; the tree id above does.
        self.assertIn("1.9.7", self.wf("version").stdout)
        self.assertFalse((self.root / "wflib" / "install_history.py").exists())   # candidate-only code removed
        check = self.wf("self-check", check=False)
        self.assertEqual(check.returncode, 0, check.stdout[-1500:] + check.stderr[-1500:])
        again = self.plan(sha)["conclusions"]
        self.assertEqual((again["code_recovery"], again["restart"]["status"]), ("already_restored", "not_established"))
        rc, out, _ = self.apply(sha, p["target_id"])
        self.assertEqual((rc, out["result"]), (0, "already_restored"))

    def test_interrupted_apply_real_sigkill_never_claims_success_and_resumes_safely(self):
        sha = self.freeze()
        self.upgrade()
        upgraded = self.git("rev-parse", "HEAD")
        p = self.plan(sha)
        rc, _, r = self.apply(sha, p["target_id"], script=KILL_AFTER_3)
        self.assertEqual(rc, -signal.SIGKILL, r.stderr[-1500:])
        self.assertTrue((self.snap / "journal" / "apply.json").exists())
        self.assertFalse((self.snap / "journal" / "completed.json").exists())
        self.assertEqual(self.git("rev-parse", "HEAD"), upgraded)   # no restore commit, no false success
        self.assertEqual(self.plan(sha)["conclusions"]["code_recovery"], "resume_interrupted_apply")
        rec = json.loads((self.snap / "journal" / "apply.json").read_bytes())
        pending = next(w for w in rec["writes"]
                       if w["pre"] and hashlib.sha256((self.root / w["path"]).read_bytes()).hexdigest() == w["pre"])
        victim = self.root / pending["path"]
        keep = victim.read_bytes()
        victim.write_bytes(keep + b"\n# concurrent edit\n")
        before = self.everything()
        rc, out, _ = self.apply(sha, p["target_id"])
        self.assertEqual((rc, out["code"]), (2, "blocked_concurrent_change"))
        # Caught by the full validation that precedes every write: zero writes, no failed record.
        self.assertEqual(self.everything(), before)
        self.assertFalse((self.snap / "journal" / "failed-1.json").exists())
        self.assertEqual(self.git("rev-parse", "HEAD"), upgraded)
        victim.write_bytes(keep)
        rc, out, r = self.apply(sha, p["target_id"])
        self.assertEqual((rc, out["result"]), (0, "restored"), r.stdout[-1500:] + r.stderr[-1500:])
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}"), OLD_TREE)
        self.assertTrue((self.snap / "journal" / "completed.json").exists())

    def test_refusals_write_nothing(self):
        head = self.git("rev-parse", "HEAD")
        cases = {}
        rc, out, _ = self.tool("freeze", "--install-root", str(self.root), "--snapshot-dir", str(self.root / "snap"))
        cases["snapshot inside root"] = out["code"]
        alias = self.base / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        rc, out, _ = self.tool("freeze", "--install-root", str(alias), "--snapshot-dir", str(self.snap))
        cases["aliased root"] = out["code"]
        (self.root / "stray.txt").write_text("untracked")
        rc, out, _ = self.tool("freeze", "--install-root", str(self.root), "--snapshot-dir", str(self.snap))
        cases["untracked file"] = out["code"]
        (self.root / "stray.txt").unlink()
        (self.root / "local" / "tracked.json").write_text("{}")
        self.git("add", "-f", "local/tracked.json")
        rc, out, _ = self.tool("freeze", "--install-root", str(self.root), "--snapshot-dir", str(self.snap))
        cases["protected state tracked"] = out["code"]
        self.git("rm", "-q", "--cached", "local/tracked.json")
        (self.root / "link").symlink_to("VERSION")
        self.git("add", "link")
        rc, out, _ = self.tool("freeze", "--install-root", str(self.root), "--snapshot-dir", str(self.snap))
        cases["tracked symlink"] = out["code"]
        self.git("rm", "-q", "--cached", "link")
        (self.root / "link").unlink()
        self.assertFalse(self.snap.exists())
        sha = self.freeze()
        rc, out, _ = self.tool("freeze", "--install-root", str(self.root), "--snapshot-dir", str(self.snap))
        cases["snapshot exists"] = out["code"]
        self.upgrade()
        upgraded = self.git("rev-parse", "HEAD")
        rc, out, _ = self.tool("plan", "--install-root", str(self.root), "--snapshot-dir", str(self.snap),
                               "--snapshot-sha256", "f" * 64)
        cases["manifest hash"] = out["code"]
        (self.root / "stray.txt").write_text("unknown")
        rc, out, _ = self.tool("plan", "--install-root", str(self.root), "--snapshot-dir", str(self.snap),
                               "--snapshot-sha256", sha)
        cases["dirty after upgrade"] = out["code"]
        (self.root / "stray.txt").unlink()
        blob = next((self.snap / "blobs").iterdir())
        blob.chmod(0o600)
        blob.write_bytes(blob.read_bytes() + b"x")
        rc, out, _ = self.tool("plan", "--install-root", str(self.root), "--snapshot-dir", str(self.snap),
                               "--snapshot-sha256", sha)
        cases["corrupt blob"] = out["code"]
        self.assertEqual(cases, {
            "snapshot inside root": "snapshot_overlaps_install_root", "aliased root": "install_root_path_alias",
            "untracked file": "dirty_or_unknown_worktree", "protected state tracked": "protected_state_tracked",
            "tracked symlink": "unsupported_tracked_mode", "snapshot exists": "snapshot_dir_exists",
            "manifest hash": "snapshot_manifest_hash_mismatch", "dirty after upgrade": "dirty_or_unknown_worktree",
            "corrupt blob": "snapshot_blob_corrupt"})
        self.assertEqual(self.git("rev-parse", "HEAD"), upgraded)
        self.assertNotEqual(head, upgraded)
        self.assertFalse((self.snap / "journal").exists())

    # ---------------- independent-review counterexamples (B3-MAIN-UPGRADE-FIX-20261016-1) ----------------

    def test_untrusted_journal_is_validated_before_any_write(self):
        """Traversal/absolute/protected/duplicate/conflicting/tampered/malformed/symlinked journal: zero writes."""
        sha = self.freeze()
        self.upgrade()
        p = self.plan(sha)
        rc, _, r = self.apply(sha, p["target_id"], script=KILL_AFTER_3)
        self.assertEqual(rc, -signal.SIGKILL, r.stderr[-800:])
        jdir = self.snap / "journal"
        j = jdir / "apply.json"
        original = j.read_bytes()
        j.chmod(0o600)
        sentinel = self.local / "runtime-sentinel.json"
        sentinel.write_bytes(b'{"keep": "runtime"}\n')
        s_sha = hashlib.sha256(sentinel.read_bytes()).hexdigest()
        blobs = [row["sha256"] for row in json.loads((self.snap / "manifest.json").read_bytes())["files"]]

        def variant(mutate):
            doc = json.loads(original)
            mutate(doc)
            return json.dumps(doc).encode()

        def other_post(d):
            d["writes"][0]["post"] = next(b for b in blobs if b != d["writes"][0]["post"])
        cases = {
            "traversal": (variant(lambda d: d["writes"].append({"path": "../../local/runtime-sentinel.json",
                                                                 "mode": "100644", "pre": s_sha, "post": blobs[0]})),
                          "journal_unsafe_path"),
            "absolute": (variant(lambda d: d["writes"].append({"path": str(sentinel), "mode": "100644", "pre": s_sha,
                                                                "post": blobs[0]})), "journal_unsafe_path"),
            "protected": (variant(lambda d: d["writes"].append({"path": "local/runtime-x.json", "mode": "100644",
                                                                 "pre": None, "post": blobs[0]})), "journal_unsafe_path"),
            "duplicate": (variant(lambda d: d["writes"].append(dict(d["writes"][0]))), "journal_duplicate_path"),
            "write_and_delete": (variant(lambda d: d["deletes"].append({"path": d["writes"][0]["path"],
                                                                         "pre": d["writes"][0]["pre"]})),
                                 "journal_duplicate_path"),
            "post_tampered": (variant(other_post), "journal_plan_mismatch"),
            "pre_tampered": (variant(lambda d: d["writes"][0].__setitem__("pre", "0" * 64)), "journal_plan_mismatch"),
            "write_dropped": (variant(lambda d: d["writes"].pop()), "journal_plan_mismatch"),
            "malformed": (b"{not json", "journal_corrupt"),
        }
        results = {}
        for name, (raw, want) in cases.items():
            j.write_bytes(raw)
            before = self.everything()
            got = (self.refused("plan", sha)[0], self.refused("apply", sha, p["target_id"])[0])
            results[name] = (got, self.everything() == before)
            self.assertEqual(results[name], ((want, want), True), name)
        self.assertEqual(sentinel.read_bytes(), b'{"keep": "runtime"}\n')
        # apply.json replaced by a symlink to a valid copy outside the snapshot: refused, copy untouched.
        copy = self.local / "apply-copy.json"
        copy.write_bytes(original)
        j.unlink()
        j.symlink_to(copy)
        before = self.everything()
        self.assertEqual(self.refused("apply", sha, p["target_id"])[0], "journal_corrupt")
        self.assertEqual((self.everything(), copy.read_bytes()), (before, original))
        j.unlink()
        j.write_bytes(original)
        j.chmod(0o400)
        # The genuine journal still resumes to the frozen tree.
        rc, out, r = self.apply(sha, p["target_id"])
        self.assertEqual((rc, out["result"]), (0, "restored"), r.stdout[-1500:])
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}"), OLD_TREE)
        self.assertEqual(self.wf("self-check", check=False).returncode, 0)

    def test_journal_and_snapshot_symlinks_are_refused(self):
        sha = self.freeze()
        self.upgrade()
        target = self.local / "journal-target"
        target.mkdir()
        (self.snap / "journal").symlink_to(target, target_is_directory=True)
        p_target = "0" * 64
        before = self.everything()
        got = (self.refused("plan", sha)[0], self.refused("apply", sha, p_target)[0])
        self.assertEqual((got, list(target.iterdir()), self.everything()),
                         (("journal_not_directory", "journal_not_directory"), [], before))
        (self.snap / "journal").unlink()
        blobs, moved = self.snap / "blobs", self.base / "blobs-elsewhere"
        blobs.rename(moved)
        blobs.symlink_to(moved, target_is_directory=True)
        self.assertEqual(self.refused("plan", sha)[0], "snapshot_blobs_not_directory")
        blobs.unlink()
        moved.rename(blobs)
        alias = self.base / "snapshot-alias"
        alias.symlink_to(self.snap, target_is_directory=True)
        rc, out, _ = self.tool("plan", "--install-root", str(self.root), "--snapshot-dir", str(alias),
                               "--snapshot-sha256", sha)
        self.assertEqual((rc, out["code"]), (2, "snapshot_dir_path_alias"))
        self.assertEqual(self.plan(sha)["conclusions"]["code_recovery"], "restorable")

    def test_ignored_code_residue_is_never_hidden_or_deleted(self):
        sha = self.freeze()
        self.upgrade()
        exclude = self.root / ".git" / "info" / "exclude"
        exclude.parent.mkdir(exist_ok=True)
        with open(exclude, "a") as fh:
            fh.write("stray-code.py\n")
        stray = self.root / "stray-code.py"
        stray.write_text("print('residue')\n")
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all"), "")   # git hides it
        before = self.everything()
        p_code, p_detail = self.refused("plan", sha)
        a_code, _ = self.refused("apply", sha, "0" * 64)
        self.assertEqual((p_code, a_code, "extra:stray-code.py" in p_detail, self.everything()),
                         ("unexplained_code_residue", "unexplained_code_residue", True, before))
        stray.unlink()   # the operator decides; the runner never deletes it
        p = self.plan(sha)
        rc, out, _ = self.apply(sha, p["target_id"])
        self.assertEqual((rc, out["result"]), (0, "restored"))
        cache = self.root / "wflib" / "__pycache__" / "extra_from_newer.cpython-39.pyc"
        cache.parent.mkdir(exist_ok=True)
        cache.write_bytes(b"\x00interpreter cache\x00")
        self.assertEqual(self.plan(sha)["conclusions"]["code_recovery"], "already_restored")   # allowed cache
        self.assertEqual(self.wf("self-check", check=False).returncode, 0)
        stray.write_text("print('residue')\n")   # ignored residue after a completed restore
        before = self.everything()
        p_code, p_detail = self.refused("plan", sha)
        self.assertEqual((p_code, "extra:stray-code.py" in p_detail, self.refused("apply", sha, "0" * 64)[0]),
                         ("restored_disk_drift", True, "restored_disk_drift"))
        self.assertEqual(self.everything(), before)
        self.assertNotEqual(self.wf("self-check", check=False).returncode, 0)   # agrees: not restored

    def test_tracked_disk_drift_after_completed_is_not_restored(self):
        sha = self.freeze()
        self.upgrade()
        p = self.plan(sha)
        self.assertEqual(self.apply(sha, p["target_id"])[1]["result"], "restored")
        driver = self.root / "wflib" / "driver.py"
        keep = driver.read_bytes()
        driver.write_bytes(keep + b"\n# drift after restore\n")
        before = self.everything()
        p_code, p_detail = self.refused("plan", sha)
        self.assertEqual((p_code, "content:wflib/driver.py" in p_detail, self.refused("apply", sha, p["target_id"])[0],
                          self.everything()), ("restored_disk_drift", True, "restored_disk_drift", before))
        self.assertNotEqual(self.wf("self-check", check=False).returncode, 0)
        driver.write_bytes(keep)
        version = self.root / "VERSION"
        version.chmod(0o755)   # exec-bit drift is drift too (git mode semantics)
        self.assertEqual(self.refused("plan", sha), ("restored_disk_drift", "mode:VERSION"))
        version.chmod(0o644)
        self.assertEqual(self.plan(sha)["conclusions"]["code_recovery"], "already_restored")
        self.assertEqual(self.wf("self-check", check=False).returncode, 0)

    def test_only_registered_runner_temps_are_cleaned(self):
        sha = self.freeze()
        self.upgrade()
        p = self.plan(sha)
        rc, _, r = self.apply(sha, p["target_id"], script=KILL_AFTER_3)
        self.assertEqual(rc, -signal.SIGKILL, r.stderr[-800:])
        user = self.root / ".b3-recovery-user-notes.txt"   # matches the old glob, not registered
        user.write_text("user notes, not a runner temp\n")
        before = self.everything()
        code, detail = self.refused("apply", sha, p["target_id"])
        self.assertEqual((code, "extra:.b3-recovery-user-notes.txt" in detail, self.everything()),
                         ("blocked_unexplained_change", True, before))
        user.unlink()   # operator decision
        rc, out, _ = self.apply(sha, p["target_id"])
        self.assertEqual((rc, out["result"]), (0, "restored"))

    def test_sigkill_between_temp_write_and_rename_resumes_via_registered_temp(self):
        sha = self.freeze()
        self.upgrade()
        p = self.plan(sha)
        rc, _, r = self.apply(sha, p["target_id"], script=KILL_IN_REPLACE)
        self.assertEqual(rc, -signal.SIGKILL, r.stderr[-800:])
        rec = json.loads((self.snap / "journal" / "apply.json").read_bytes())
        runner = _runner()
        temps = [(runner._temp_rel(sha, rec["base_head"]["tree"], w["path"]), w) for w in rec["writes"]]
        left = [(t, w) for t, w in temps if (self.root / t).exists()]
        self.assertEqual(len(left), 1, temps)
        temp, w = left[0]
        intended = (self.snap / "blobs" / w["post"]).read_bytes()
        self.assertEqual((self.root / temp).read_bytes(), intended)
        plan = self.plan(sha)
        self.assertEqual((plan["conclusions"]["code_recovery"], plan["inventory"]["registered_temps_found"]),
                         ("resume_interrupted_apply", [temp]))
        (self.root / temp).chmod(0o600)
        (self.root / temp).write_bytes(intended + b"tampered")   # no longer a prefix of the intended blob
        before = self.everything()
        code, detail = self.refused("apply", sha, p["target_id"])
        self.assertEqual((code, f"foreign_temp:{temp}" in detail, self.everything()),
                         ("blocked_unexplained_change", True, before))
        (self.root / temp).write_bytes(intended)
        rc, out, r = self.apply(sha, p["target_id"])
        self.assertEqual((rc, out["result"]), (0, "restored"), r.stdout[-1500:])
        self.assertFalse((self.root / temp).exists())
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}"), OLD_TREE)
        self.assertEqual(self.wf("self-check", check=False).returncode, 0)

    def test_freeze_requires_a_qualified_old_source(self):
        clean = self.git("rev-parse", "HEAD")
        manifest = self.root / "MANIFEST.json"

        def attempt():
            before = _code_bytes(self.root)
            rc, out, _ = self.tool("freeze", "--install-root", str(self.root), "--snapshot-dir", str(self.snap))
            self.assertEqual((rc, self.snap.exists(), _code_bytes(self.root)), (2, False, before))
            return out["code"], out.get("detail", "")

        def commit(msg):
            self.git("add", "-A")
            self.git("commit", "-q", "-m", msg)

        def reset():
            self.git("reset", "-q", "--hard", clean)
        doc = json.loads(manifest.read_bytes())
        doc["files"]["VERSION"] = "0" * 64
        manifest.write_text(json.dumps(doc))
        commit("tamper VERSION hash")
        code, detail = attempt()
        self.assertEqual((code, '"mismatch": ["VERSION"]' in detail), ("old_source_manifest_mismatch", True))
        reset()
        (self.root / "wflib" / "unlisted.py").write_text("x = 1\n")
        commit("unlisted tracked file")
        code, detail = attempt()
        self.assertEqual((code, "wflib/unlisted.py" in detail), ("old_source_manifest_mismatch", True))
        reset()
        doc = json.loads(manifest.read_bytes())
        doc["version"] = "9.9.9"
        manifest.write_text(json.dumps(doc))
        commit("manifest version differs")
        self.assertEqual(attempt()[0], "old_source_manifest_mismatch")
        reset()
        exclude = self.root / ".git" / "info" / "exclude"
        exclude.parent.mkdir(exist_ok=True)
        exclude.write_text("stray-code.py\n")
        (self.root / "stray-code.py").write_text("print('ignored residue at freeze')\n")
        code, detail = attempt()
        self.assertEqual((code, detail), ("unexplained_code_residue", "extra:stray-code.py"))
        (self.root / "stray-code.py").unlink()
        sha = self.freeze()
        frozen = json.loads((self.snap / "manifest.json").read_bytes())
        self.assertEqual((frozen["manifest_check"]["mismatch"], frozen["manifest_check"]["unlisted"],
                          frozen["manifest_check"]["version_matches"]), ([], [], True))
        self.assertEqual(len(sha), 64)


if __name__ == "__main__":
    unittest.main()
