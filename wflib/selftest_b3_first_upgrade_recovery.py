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
        rc, out, _ = self.apply(sha, p["target_id"])
        self.assertEqual((rc, out["code"]), (2, "blocked_concurrent_change"))
        self.assertTrue((self.snap / "journal" / "failed-1.json").exists())
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


if __name__ == "__main__":
    unittest.main()
