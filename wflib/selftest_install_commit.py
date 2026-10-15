"""Isolated real-Git regressions for installation commits; no live verifier."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import admin, core, release, review
from .selftest import _project_md
from .selftest_review import Env, _git


class InstallCommitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wf-install-commit-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        local = self.base / "local"
        for key, value in (("LOCAL", local), ("REGISTRY", local / "registry.json")):
            p = patch.object(core, key, value)
            p.start()
            self.addCleanup(p.stop)
        self.env = Env(self.base)
        p = patch.object(review, "_tool_home", lambda: self.env.tool)
        p.start()
        self.addCleanup(p.stop)

    def project(self, name):
        inst = admin.init_project(self.base / name, name=name)
        _git(inst.root, "init", "-q")
        _git(inst.root, "add", "-A")
        _git(inst.root, "commit", "-qm", "initial")
        self.assertIn("workflow/evidence/", (inst.root / ".gitignore").read_text())
        return inst

    def approved(self, inst, version):
        package = self.env.pkg(version, {
            "PROJECT.md": _project_md(version).encode(),
            "CHANGELOG.md": f"# CHANGELOG\n\n## {version} — test\n- installed\n".encode(),
            "app.py": version.encode(),
        })
        req = review.prepare(inst, package, kind="project", author="B")
        decision = review.run(req["request_id"])
        self.assertTrue(decision["allowed"], decision)
        self.assertTrue(decision["provenance"]["synthetic"])
        return req["request_id"]

    def test_default_ignore_install_and_recovery(self):
        inst = self.project("default")
        rid = self.approved(inst, "v0.2")
        result = release.apply_zip(inst, review_id=rid)
        self.assertTrue(result["commit"])
        self.assertEqual((inst.root / "app.py").read_text(), "v0.2")
        tx = inst.evidence / "install" / result["install_tx"]
        self.assertEqual(json.loads((tx / "completed.json").read_bytes())["state"], "completed")
        self.assertNotIn("workflow/evidence", _git(inst.root, "ls-files"))
        rid2 = self.approved(inst, "v0.3")
        # Fail after the new tree has actually been committed, forcing a real
        # restore commit through the same ignored-parent boundary.
        with patch.object(release._InstallTx, "complete", side_effect=core.WfError("test post-commit failure")):
            with self.assertRaisesRegex(core.WfError, "已核验恢复安装前基线"):
                release.apply_zip(inst, review_id=rid2)
        self.assertEqual((inst.root / "app.py").read_text(), "v0.2")
        recovered = list((inst.evidence / "install").glob("tx-*/recovered.json"))
        self.assertEqual(len(recovered), 1)
        self.assertEqual(_git(inst.root, "status", "--porcelain"), "")

    def test_unignored_transactions_and_literal_paths(self):
        inst = self.project("unignored")
        (inst.root / ".gitignore").write_text("")
        for prefix in ("local/install", "workflow/evidence/install"):
            p = inst.root / prefix / "tx-example/prepared.json"
            p.parent.mkdir(parents=True)
            p.write_text("transaction")
        odd = "literal [*] : name\nnext.txt"
        (inst.root / odd).write_text("ordinary")
        magic = ":(glob)*.txt"
        (inst.root / magic).write_text("literal magic")
        (inst.root / "gone.txt").write_text("old")
        release._commit(inst.root, "first")
        (inst.root / "gone.txt").unlink()
        (inst.root / odd).write_text("changed")
        release._commit(inst.root, "second")
        names = _git(inst.root, "ls-files", "-z").split("\0")
        self.assertIn(odd, names)
        self.assertIn(magic, names)
        self.assertNotIn("gone.txt", names)
        self.assertFalse(any("/install/" in p for p in names))

    def test_tracked_and_prestaged_transaction_records(self):
        inst = self.project("tracked")
        txn = inst.root / "workflow/evidence/install/tracked.json"
        txn.parent.mkdir(parents=True)
        txn.write_text("old")
        _git(inst.root, "add", "-f", "--", "workflow/evidence/install/tracked.json")
        _git(inst.root, "commit", "-qm", "historical tracked metadata")
        txn.write_text("new")
        (inst.root / "app.py").write_text("new app")
        release._commit(inst.root, "ordinary change")
        self.assertEqual(_git(inst.root, "show", "HEAD:workflow/evidence/install/tracked.json"), "old")
        _git(inst.root, "add", "-f", "--", "workflow/evidence/install/tracked.json")
        old_head = _git(inst.root, "rev-parse", "HEAD")
        with self.assertRaisesRegex(core.WfError, "install_metadata_staged"):
            release._commit(inst.root, "must refuse")
        self.assertEqual(_git(inst.root, "rev-parse", "HEAD"), old_head)

    def test_empty_repository_initialization(self):
        root = self.base / "empty"
        root.mkdir()
        self.assertEqual(release._commit(root, "empty"), (None, []))
        (root / "first.txt").write_text("first")
        head, changed = release._commit(root, "first")
        self.assertTrue(head)
        self.assertEqual(changed, ["A first.txt"])

    def test_tool_self_init_ignored_local(self):
        root = self.base / "new-tool"
        (root / "local/install/tx-example").mkdir(parents=True)
        (root / "local/install/tx-example/prepared.json").write_text("transaction")
        (root / "VERSION").write_text("0.1.0")
        with patch.object(release, "WF_HOME", root), patch.object(core, "WF_HOME", root):
            head = release.self_init()
            self.assertTrue(head)
            self.assertEqual(release.self_init(), head)
        self.assertIn("local/", (root / ".gitignore").read_text())
        self.assertNotIn("local/", _git(root, "ls-files"))


if __name__ == "__main__":
    unittest.main()
