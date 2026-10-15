"""C-owned capture/replay boundary for historical installation verification.

This module reads material only. Historical meanings belong to review's history
provider; callers cannot use a stored result instead of replaying that provider.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import re
import shutil
import subprocess


def _problem(state, source, detail):
    from .live import _InputProblem
    return _InputProblem(state, source, detail)


def _canonical(value):
    from .live import _canonical as encode
    return encode(value)


def _query_key(root, argv):
    if not isinstance(argv, list) or any(not isinstance(x, str) for x in argv):
        raise _problem("unsupported", root, "historical Git argv must be a string list")
    oid = r"[0-9a-f]{40}(?:[0-9a-f]{24})?"
    allowed = ((len(argv) == 3 and argv[:2] == ["rev-parse", "--verify"]
                and re.fullmatch(oid + r"\^\{(?:commit|tree)\}", argv[2]))
               or (len(argv) == 4 and argv[:3] == ["ls-tree", "-rz", "--full-tree"]
                   and re.fullmatch(oid, argv[3]))
               or (len(argv) == 3 and argv[:2] == ["cat-file", "blob"]
                   and re.fullmatch(oid, argv[2])))
    if not allowed:
        raise _problem("unsupported", root, "historical Git query is outside the read-only protocol")
    root = Path(root).absolute()
    if str(root) != str(root.resolve()):
        raise _problem("unsupported", root, "historical Git root is not canonical")
    return _canonical([str(root), argv])


def _run(executable, root, args, environment):
    argv = [executable, "--no-replace-objects", "-c", "core.hooksPath=/dev/null", *args]
    process = None
    try:
        import selectors
        import time
        process = subprocess.Popen(argv, cwd=str(root), env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        replies = {"stdout": bytearray(), "stderr": bytearray()}
        limits = {"stdout": 64 * 1024 * 1024, "stderr": 1024 * 1024}
        deadline = time.monotonic() + 15
        with selectors.DefaultSelector() as pending:
            pending.register(process.stdout, selectors.EVENT_READ, "stdout")
            pending.register(process.stderr, selectors.EVENT_READ, "stderr")
            while pending.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, 15)
                for selected, _ in pending.select(remaining):
                    chunk = os.read(selected.fileobj.fileno(), 65536)
                    if not chunk:
                        pending.unregister(selected.fileobj)
                        continue
                    stream = selected.data
                    if len(replies[stream]) + len(chunk) > limits[stream]:
                        raise _problem("unsupported", root, "historical Git reply budget exceeded")
                    replies[stream].extend(chunk)
        returncode = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        return {"returncode": returncode, **{key: bytes(value) for key, value in replies.items()}}
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _problem("unknown", root, "historical Git read failed: " + type(exc).__name__) from exc
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
            process.stderr.close()


class CaptureReader:
    """Record every provider dependency, including explicit absence and queries."""

    def __init__(self, source, directory, *, max_bytes=256 * 1024 * 1024):
        self._source, self._directory = source, directory
        self._directories, self._repositories, self._queries = {}, {}, {}
        self._selector_members = 0
        self._reply_bytes, self._max_bytes = 0, max_bytes

    def bytes(self, path):
        return self._source(Path(path).absolute())

    def directory(self, path):
        key = str(Path(path).absolute())
        if key not in self._directories:
            descriptor = self._directory(Path(key))
            self._selector_members += len(descriptor["members"])
            if len(self._directories) >= 20000 or self._selector_members > 20000:
                raise _problem("unsupported", path, "historical selector budget exceeded")
            self._directories[key] = descriptor
        # A provider cannot mutate the captured descriptor by retaining it.
        import json
        return json.loads(_canonical(self._directories[key]))

    def _repository(self, root):
        key = str(root)
        if key in self._repositories:
            return self._repositories[key]
        gd = root / ".git"
        if not gd.is_dir() or gd.is_symlink():
            raise _problem("unsupported", gd, "ordinary historical repository required")
        for rel in ("shallow", "objects/info/alternates", "objects/info/http-alternates", "commondir"):
            if self.bytes(gd / rel) is not None:
                raise _problem("unsupported", gd / rel, "historical Git indirection")
        self.bytes(gd / "config")
        executable = shutil.which("git", path=os.defpath)
        if not executable:
            raise _problem("unknown", gd, "Git executable unavailable")
        executable = str(Path(executable).resolve())
        if self.bytes(executable) is None:
            raise _problem("unknown", executable, "Git executable disappeared")
        environment = {"PATH": os.defpath, "HOME": "/nonexistent", "LC_ALL": "C",
                       "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull,
                       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_OPTIONAL_LOCKS": "0",
                       "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}
        config = _run(executable, root, ["config", "--local", "--no-includes", "--list"], environment)
        if config["returncode"] != 0 or config["stderr"]:
            raise _problem("unknown", gd, "historical Git config unavailable")
        try:
            lines = config["stdout"].decode("utf-8").splitlines()
        except UnicodeError as exc:
            raise _problem("corrupt", gd, "historical Git config is not UTF-8") from exc
        if any(line.lower().startswith(("include.", "includeif.", "extensions.", "core.worktree="))
               or re.match(r"remote\..*\.(?:promisor|partialclonefilter)=", line.lower()) for line in lines):
            # Partial clone promisor configuration and remote lazy object
            # fetching are outside this ordinary, local object protocol.
            raise _problem("unsupported", gd, "historical Git config indirection/remotes/extensions")
        replace = _run(executable, root, ["for-each-ref", "--format=%(refname)", "refs/replace"], environment)
        if replace["returncode"] != 0 or replace["stdout"].strip() or replace["stderr"]:
            raise _problem("unsupported", gd, "historical Git replacement refs")
        record = {"root": key, "executable": executable, "environment": environment,
                  "config": _encode_reply(config), "replace_refs": _encode_reply(replace)}
        self._repositories[key] = record
        return record

    def git(self, root, argv):
        key = _query_key(root, argv)
        if key not in self._queries:
            root = Path(root).absolute()
            repository = self._repository(root)
            reply = _run(repository["executable"], root, argv, repository["environment"])
            self._reply_bytes += len(reply["stdout"]) + len(reply["stderr"])
            if self._reply_bytes > self._max_bytes or len(self._queries) >= 20000:
                raise _problem("unsupported", root, "historical Git aggregate budget exceeded")
            self._queries[key] = {"root": str(root), "argv": list(argv), **_encode_reply(reply)}
        return _decode_reply(self._queries[key])

    @property
    def descriptor(self):
        return {"schema": "install-history-reader/1",
                "directories": [self._directories[key] for key in sorted(self._directories)],
                "repositories": [self._repositories[key] for key in sorted(self._repositories)],
                "queries": [self._queries[key] for key in sorted(self._queries)]}

    def revalidate(self):
        """Re-run the complete query envelope; file/selector owners also replay."""
        fresh = CaptureReader(self._source, self._directory, max_bytes=self._max_bytes)
        for record in self._queries.values():
            fresh.git(record["root"], record["argv"])
        for path in self._directories:
            # Do not use the memoized descriptor from capture.
            fresh.directory(path)
        if fresh.descriptor != self.descriptor:
            raise _problem("changed", "install_history", "historical Git query envelope changed before freeze")


def _encode_reply(reply):
    return {"returncode": reply["returncode"],
            "stdout_b64": base64.b64encode(reply["stdout"]).decode("ascii"),
            "stderr_b64": base64.b64encode(reply["stderr"]).decode("ascii")}


def _decode_reply(record):
    return {"returncode": record["returncode"],
            "stdout": base64.b64decode(record["stdout_b64"], validate=True),
            "stderr": base64.b64decode(record["stderr_b64"], validate=True)}


class FrozenReader:
    """Provider API over an immutable live snapshot; no disk or subprocess reads."""

    def __init__(self, snapshot, descriptor):
        import json
        self._snapshot = snapshot
        self._descriptor = json.loads(_canonical(descriptor))
        self._directories = {row["path"]: row for row in self._descriptor["directories"]}
        self._queries = {_query_key(row["root"], row["argv"]): row for row in self._descriptor["queries"]}

    def bytes(self, path):
        return self._snapshot.bytes(path)

    def directory(self, path):
        import json
        key = str(Path(path).absolute())
        if key not in self._directories:
            raise _problem("unsupported", path, "directory read outside frozen historical set")
        return json.loads(_canonical(self._directories[key]))

    def git(self, root, argv):
        key = _query_key(root, argv)
        if key not in self._queries:
            raise _problem("unsupported", root, "Git read outside frozen historical set")
        return _decode_reply(self._queries[key])


def exec_binding(inst, event, reader):
    """Cross-check the event against its actual invocation files and receipts.

    The result binds an invocation to references only. The history provider must
    still establish every transaction, review and authorization fact.
    """
    from .live import _strict_json, _strict_log
    invocation = event.get("install_exec")
    if not isinstance(invocation, dict) or set(invocation) != {"schema", "subject", "turn", "exec"} \
            or invocation.get("schema") != "install-exec/1" \
            or invocation.get("subject") != {"project": inst.name, "root": str(inst.root)} \
            or type(invocation.get("turn")) is not int or invocation["turn"] != event.get("turn"):
        raise _problem("corrupt", inst.events_path, "installation invocation subject/turn mismatch")
    identity = invocation.get("exec")
    if not isinstance(identity, dict) or set(identity) != {"id", "nonce"} \
            or not isinstance(identity.get("id"), str) or not re.fullmatch(r"c[0-9]{2,}", identity["id"]) \
            or not isinstance(identity.get("nonce"), str) or not re.fullmatch(r"[0-9a-f]{32}", identity["nonce"]) \
            or event.get("cid") != identity["id"]:
        raise _problem("corrupt", inst.events_path, "installation invocation id/nonce mismatch")
    directory = Path(inst.evidence) / f"turn-{invocation['turn']:03d}"
    path = directory / (identity["id"] + ".json")
    raw = reader.bytes(path)
    if raw is None:
        raise _problem("unknown", path, "installation invocation metadata absent")
    metadata = _strict_json(raw, path)
    if metadata.get("id") != identity["id"] or metadata.get("cwd") != str(inst.root) \
            or metadata.get("install_exec") != invocation or not isinstance(metadata.get("cmd"), str) \
            or metadata["cmd"][:300] != event.get("cmd") \
            or any(metadata.get(key) != event.get(key) for key in
                   ("exit_code", "duration_s", "interrupted", "install_refs", "install_receipt_error")):
        raise _problem("corrupt", path, "installation event and invocation metadata disagree")
    if event.get("install_receipt_error") is not None:
        raise _problem("corrupt", path, "installation invocation receipt was rejected")
    refs = event.get("install_refs")
    if not isinstance(refs, list) or any(not isinstance(ref, dict) for ref in refs):
        raise _problem("corrupt", path, "installation references are not an object list")
    receipt = directory / (identity["id"] + ".install.jsonl")
    raw = reader.bytes(receipt)
    if raw is None:
        raise _problem("unknown", receipt, "installation invocation receipt absent")
    rows = _strict_log(raw, receipt)
    if any(set(row) != {"schema", "exec", "install_ref"}
           or row.get("schema") != "install-exec-receipt/1" or row.get("exec") != invocation for row in rows) \
            or [row.get("install_ref") for row in rows] != refs:
        raise _problem("corrupt", receipt, "installation invocation receipt and references disagree")
    if len({_canonical(ref) for ref in refs}) != len(refs):
        raise _problem("corrupt", receipt, "duplicate installation transaction reference")
    for suffix, length in ((".out", "out_bytes"), (".err", "err_bytes")):
        output = directory / (identity["id"] + suffix)
        raw = reader.bytes(output)
        if raw is None:
            raise _problem("unknown", output, "installation invocation output absent")
        if type(metadata.get(length)) is not int or metadata[length] != len(raw):
            raise _problem("corrupt", output, "installation invocation output size mismatch")
    return {"invocation": invocation, "references": refs,
            "start": metadata.get("start"), "end": metadata.get("end")}


def collect_bound_histories(inst, events, reader, *, local, tool_root):
    """Resolve structural receipts through B's provider, preserving every try."""
    from . import review
    entries, seen = [], set()
    provider = getattr(review, "collect_install_history", None)
    for event in events:
        if event.get("type") != "exec":
            continue
        invocation = event.get("install_exec")
        legacy_install = "wf self-update" in str(event.get("cmd") or "")
        if invocation is None:
            if legacy_install:
                entries.append({"seq": event["seq"], "turn": event.get("turn"), "reference": None,
                                "state": "unknown", "diagnostics": ["legacy_install_reference_unavailable"]})
            continue
        binding = exec_binding(inst, event, reader)
        key = (binding["invocation"]["turn"], binding["invocation"]["exec"]["id"])
        nonce = binding["invocation"]["exec"]["nonce"]
        if key in seen or nonce in seen:
            raise _problem("corrupt", inst.events_path, "duplicate installation invocation identity")
        seen.update((key, nonce))
        if not binding["references"] and (legacy_install or event.get("exit_code") != 0 or event.get("interrupted")):
            entries.append({"seq": event["seq"], "turn": event.get("turn"), "reference": None,
                            "state": "unknown", "exit_code": event.get("exit_code"),
                            "interrupted": event.get("interrupted"),
                            "diagnostics": ["installation_receipt_has_no_transaction" if legacy_install
                                            else "unsuccessful_exec_has_no_transaction_reference"]})
        for ref in binding["references"]:
            target = ref.get("target")
            if not isinstance(target, dict) or target.get("kind") not in ("tool", "project"):
                raise _problem("corrupt", inst.events_path, "installation receipt target kind invalid")
            expected = str(Path(tool_root if target["kind"] == "tool" else inst.root).absolute())
            if target != {"kind": target["kind"], "root": expected}:
                raise _problem("corrupt", inst.events_path, "installation receipt target root mismatch")
            if provider is None:
                raise _problem("unsupported", "install_history", "historical verification provider unavailable")
            context = {key: binding["invocation"][key] for key in ("subject", "turn", "exec")}
            context.update(target=target, host={"local": str(Path(local).absolute()),
                                               "tool_root": str(Path(tool_root).absolute())})
            history = provider(context, ref, reader)
            if not isinstance(history, dict) or history.get("schema") != "install-history/1":
                raise _problem("unsupported", "install_history", "historical verification protocol invalid")
            entries.append({"seq": event["seq"], "turn": event.get("turn"), "reference": ref,
                            "invocation": binding["invocation"], "start": binding["start"], "end": binding["end"],
                            "event_ts": event.get("ts"), "duration_s": event.get("duration_s"),
                            "exit_code": event.get("exit_code"), "interrupted": event.get("interrupted"),
                            "state": history.get("state"), "history": history})
    return {"schema": "live-install-history/1", "entries": entries}


def entry_problem(entry):
    """C checks the invocation interval; B checks original review/install time."""
    from datetime import datetime, timedelta
    if entry.get("state") != "valid":
        return entry.get("state") or "unknown"
    facts = entry.get("history", {}).get("facts", {})
    if facts.get("completed") is not True or facts.get("authorized_at_start") is not True:
        return "historical_install_not_authorized"
    if type(facts.get("synthetic")) is not bool:
        return "historical_provenance_unknown"
    if entry.get("exit_code") != 0 or entry.get("interrupted"):
        return "installation_invocation_unsuccessful"

    def stamp(value):
        if not isinstance(value, str):
            raise ValueError("timestamp missing")
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp needs timezone")
        return value

    try:
        start, end, event_at = stamp(entry["start"]), stamp(entry["end"]), stamp(entry["event_ts"])
        install = facts["times"]
        began, completed = stamp(install["install_started"]), stamp(install["install_completed"])
        # ops metadata currently records whole seconds; B records microseconds.
        if not start <= began <= completed < end + timedelta(seconds=1) or end < start or event_at < end:
            return "installation_outside_invocation"
        duration = entry["duration_s"]
        if type(duration) not in (int, float) or abs((end - start).total_seconds() - duration) > 1:
            return "installation_invocation_duration_mismatch"
    except (KeyError, ValueError, TypeError):
        return "installation_invocation_time_unknown"
    return ""


def window_authorization(histories, events, report):
    """History eligibility for the selected strict window, never a goal grant."""
    # Include failed rounds in the evaluated candidate window so their attempts
    # remain diagnostic even when the passing streak is empty.
    window = [row.get("turn") for row in report.get("rounds", [])]
    reasons = []
    if not histories or histories.get("schema") != "live-install-history/1":
        return {"eligible": False, "complete": False, "synthetic": None,
                "why": ["install_history_unavailable"]}
    entries = [row for row in histories["entries"] if row.get("turn") in window]
    kinds, synthetic = set(), False
    for entry in entries:
        problem = entry_problem(entry)
        if problem:
            reasons.append("install_history:" + problem)
            continue
        kinds.add(entry["reference"]["target"]["kind"])
        synthetic |= entry["history"]["facts"]["synthetic"]
    for event in events:
        if event.get("type") != "apply" or event.get("turn") not in window:
            continue
        if not any(not entry_problem(row) and row["reference"]["target"]["kind"] == "project"
                   and event.get("install_ref") == row["reference"]
                   and event.get("install_exec") == row["invocation"] for row in entries):
            reasons.append("apply_install_reference_unavailable")
    if kinds != {"tool", "project"}:
        reasons.append("tool_and_project_install_history_required")
    complete = not reasons
    if synthetic:
        reasons.append("synthetic_install_history")
    return {"eligible": complete and not synthetic, "complete": complete, "synthetic": synthetic,
            "why": sorted(set(reasons))}
