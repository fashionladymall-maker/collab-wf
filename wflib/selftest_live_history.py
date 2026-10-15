"""Real filesystem/Git probes for the C5 capture and replay boundary."""
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile


def run_reader_tests(ok):
    from . import live
    from .live_history import CaptureReader, FrozenReader

    with tempfile.TemporaryDirectory(prefix="wf-history-reader-") as td:
        root = Path(td).resolve()
        repo = root / "repo"
        repo.mkdir()

        def git(*args):
            return subprocess.run(["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                                   *args], cwd=str(repo), capture_output=True, check=True).stdout.strip().decode()

        git("init", "-q")
        binary = b"\x00\xff\x80history\n"
        (repo / "blob").write_bytes(binary)
        git("add", "blob")
        git("commit", "-qm", "original")
        oid = git("rev-parse", "HEAD")
        blob = git("rev-parse", "HEAD:blob")
        tree = git("rev-parse", "HEAD^{tree}")
        # Ordinary repositories commonly have remotes. Merely recording a URL
        # must not turn local, complete objects into a network dependency.
        git("remote", "add", "origin", "https://example.invalid/unused.git")

        def reader():
            raw, sources = {}, {}

            def source(path):
                key = str(path)
                if key not in raw:
                    sources[key], raw[key] = live._file_source(path)
                return raw[key]

            return CaptureReader(source, live._directory_source), raw, sources

        def state(call):
            try:
                call()
                return "accepted"
            except live._InputProblem as exc:
                return exc.state

        capture, raw, sources = reader()
        absent = root / "absent"
        ok("C5 reader: explicit missing file and selector are frozen",
           capture.bytes(absent) is None and capture.directory(absent)["state"] == "absent")
        got = capture.git(repo, ["cat-file", "blob", blob])
        ok("C5 reader: binary historical blob is preserved byte for byte",
           got == {"returncode": 0, "stdout": binary, "stderr": b""})
        listing = capture.git(repo, ["ls-tree", "-rz", "--full-tree", tree])
        commit = capture.git(repo, ["rev-parse", "--verify", oid + "^{commit}"])
        ok("C5 reader: full historical commit and NUL tree replies are captured",
           commit["stdout"].strip() == oid.encode() and listing["stdout"].endswith(b"blob\x00"))
        snapshot = live._FrozenInputs(repo, "fixture", raw, {})
        frozen = FrozenReader(snapshot, capture.descriptor)
        # Move and modify current HEAD. Frozen historical data stays original.
        (repo / "blob").write_bytes(b"new current content")
        git("commit", "-qam", "new head")
        ok("C5 reader: replay uses captured object replies after current HEAD changes",
           frozen.git(repo, ["cat-file", "blob", blob]) == got
           and frozen.bytes(absent) is None and frozen.directory(absent)["state"] == "absent")
        ok("C5 reader: uncollected bytes, selectors and Git replies fail closed",
           all(state(fn) == "unsupported" for fn in (
               lambda: frozen.bytes(root / "uncollected"),
               lambda: frozen.directory(root),
               lambda: frozen.git(repo, ["cat-file", "blob", "1" * 40]))))
        ok("C5 reader: HEAD, abbreviated IDs, config and write queries are forbidden",
           all(state(lambda argv=argv: capture.git(repo, argv)) == "unsupported" for argv in (
               ["rev-parse", "--verify", "HEAD^{commit}"],
               ["cat-file", "blob", blob[:8]], ["config", "core.hooksPath", "evil"],
               ["update-ref", "refs/heads/main", oid])))
        first = capture.descriptor
        capture.git(repo, ["cat-file", "blob", blob])
        ok("C5 reader: repeated query retains a single deterministic envelope",
           capture.descriptor == first)
        capture.revalidate()
        ok("C5 reader: changing current HEAD alone leaves historical envelope stable",
           capture.descriptor == first)
        missing = capture.git(repo, ["cat-file", "blob", "0" * 40])
        frozen = FrozenReader(live._FrozenInputs(repo, "fixture", raw, {}), capture.descriptor)
        ok("C5 reader: missing historical object keeps the original failure reply",
           missing["returncode"] != 0
           and frozen.git(repo, ["cat-file", "blob", "0" * 40]) == missing)
        git("config", "core.abbrev", "12")
        ok("C5 reader: changed repository configuration rejects capture",
           state(capture.revalidate) == "changed")
        git("config", "--unset", "core.abbrev")
        other, _, _ = reader()
        other.git(repo, ["cat-file", "blob", blob])
        git("replace", oid, git("rev-parse", "HEAD"))
        ok("C5 reader: newly installed replacement refs reject revalidation",
           state(other.revalidate) == "unsupported")
        git("replace", "-d", oid)
        alias = root / "alias"
        alias.symlink_to(repo, target_is_directory=True)
        ok("C5 reader: a real symlink root remains unsupported",
           state(lambda: reader()[0].git(alias, ["cat-file", "blob", blob])) == "unsupported")
        status = live._git_inputs(repo, [], include_status=True)
        exclude = repo / ".git/info/exclude"
        old = exclude.read_bytes()
        exclude.write_bytes(old + b"\n# changed ignore dependency\n")
        changed = live._git_inputs(repo, [], include_status=True)
        ok("C5 reader: status binds index and ignore/attribute bytes even when clean output is unchanged",
           status["status"] == changed["status"] == [] and status != changed
           and {Path(row["path"]).name for row in status["status_sources"]} == {"index", "exclude", "attributes"})
        exclude.write_bytes(old)
        sentinel, hook = root / "unexpected-fsmonitor", root / "fsmonitor-hook"
        hook.write_text("#!/bin/sh\ntouch " + str(sentinel) + "\n")
        hook.chmod(0o755)
        git("config", "core.fsmonitor", str(hook))
        controlled = live._git_inputs(repo, [], include_status=True)
        ok("C5 reader: controlled status disables repository fsmonitor execution",
           controlled["status"] == [] and not sentinel.exists())
        git("config", "--unset", "core.fsmonitor")
        git("config", "filter.fixture.clean", str(hook))
        ok("C5 reader: status rejects external filter configuration before execution",
           state(lambda: live._git_inputs(repo, [], include_status=True)) == "unsupported" and not sentinel.exists())
        git("config", "--remove-section", "filter.fixture")
        # A config include must never influence a historical object lookup.
        git("config", "include.path", str(root / "external-config"))
        ok("C5 reader: config includes are rejected before provider object reads",
           state(lambda: reader()[0].git(repo, ["cat-file", "blob", blob])) == "unsupported")
