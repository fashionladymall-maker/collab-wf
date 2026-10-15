"""B-owned installation history verification over an injected frozen reader.

No filesystem, subprocess, current-target status, consume, or repair calls.
Reader operations are bytes(path), directory(path), git(root, argv). The caller
owns stable collection and replay, including path/ancestor identity and Git
execution environment. A valid synthetic history is never production evidence.
"""
import json
import re
from datetime import datetime
from pathlib import Path

from . import core, review

_TX = re.compile(r"tx-[0-9a-f]{32}\Z")
_OID = re.compile(r"[0-9a-f]{40}\Z")


class _Invalid(Exception):
    def __init__(self, state, why):
        self.state, self.why = state, why


def _need(ok, why, state="corrupt"):
    if not ok:
        raise _Invalid(state, why)


def _json(raw):
    def pairs(rows):
        doc = {}
        for key, value in rows:
            _need(key not in doc, "duplicate_json_key")
            doc[key] = value
        return doc
    def constant(_):
        raise _Invalid("corrupt", "nonfinite_json")
    doc = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    _need(isinstance(doc, dict), "json_not_object")
    return doc


def _digest(obj):
    return review._sha(review._canon(obj))


def _time(value):
    _need(isinstance(value, str), "time_missing")
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _need(t.tzinfo is not None and t.utcoffset() is not None, "time_without_zone")
    return t


def _absolute(value):
    _need(isinstance(value, str) and Path(value).is_absolute()
          and str(Path(value)) == value and ".." not in Path(value).parts, "path_not_canonical")
    return Path(value)


class _Read:
    def __init__(self, reader):
        self.reader, self.sources, self.selectors = reader, {}, {}

    def directory(self, path):
        key = str(path)
        doc = self.reader.directory(key)
        _need(isinstance(doc, dict), "directory_descriptor")
        _need(doc.get("state") in ("present", "absent"), "directory_" + str(doc.get("state")), "unknown")
        rows = doc.get("members", [])
        _need(isinstance(rows, list), "directory_members")
        names = []
        for row in rows:
            _need(isinstance(row, (list, tuple)) and len(row) == 3, "directory_member")
            name, kind, mode = row
            _need(isinstance(name, str) and name not in ("", ".", "..")
                  and "/" not in name and "\0" not in name, "directory_name")
            _need(kind in ("file", "dir"), "unsupported_source_type", "unsupported")
            _need(type(mode) is int, "directory_mode")
            names.append(name)
        _need(names == sorted(set(names)), "directory_member_order")
        _need(doc["state"] != "absent" or not rows, "absent_directory_members")
        self.selectors[key] = {"path": key, "state": doc["state"], "members": rows}
        return {row[0]: row[1:] for row in rows}

    def bytes(self, path):
        path = Path(path)
        members = self.directory(path.parent)
        entry = members.get(path.name)
        _need(entry is None or entry[0] == "file", "source_not_regular")
        raw = self.reader.bytes(str(path))
        _need(raw is None or isinstance(raw, bytes), "reader_bytes_type")
        _need((entry is None) == (raw is None), "source_membership_changed", "changed")
        self.sources[str(path)] = {"path": str(path), "state": "absent" if raw is None else "present",
                                  "sha256": None if raw is None else review._sha(raw),
                                  "size": None if raw is None else len(raw),
                                  "mode": None if entry is None else entry[1]}
        return raw

    def doc(self, path, hashed=False):
        raw = self.bytes(path)
        _need(raw is not None, "source_missing:" + Path(path).name, "unknown")
        doc = _json(raw)
        if hashed:
            _need(doc.get("body_sha256") == _digest({k: v for k, v in doc.items() if k != "body_sha256"}),
                  "record_hash:" + Path(path).name)
        return doc

    def git(self, root, argv):
        out = self.reader.git(str(root), argv)
        _need(isinstance(out, dict) and type(out.get("returncode")) is int
              and isinstance(out.get("stdout"), bytes) and isinstance(out.get("stderr"), bytes), "git_result")
        key = "git:" + str(root) + ":" + json.dumps(argv)
        self.selectors[key] = {"kind": "git", "root": str(root), "argv": argv, "returncode": out["returncode"],
                               "stdout_sha256": review._sha(out["stdout"]),
                               "stderr_sha256": review._sha(out["stderr"])}
        _need(out["returncode"] == 0 and not out["stderr"], "git_history_unavailable", "unknown")
        return out["stdout"]


def _tree(rd, root, identity, rows, protect, txdir):
    _need(isinstance(rows, list), "tree_rows")
    bypath = {}
    for row in rows:
        p = row["path"]
        _need(isinstance(p, str) and p and not p.startswith("/")
              and not any(x in ("", ".", "..") for x in p.split("/")) and p not in bypath, "tree_path")
        _need(row["type"] in ("file", "dir"), "historical_symlink_unsupported", "unsupported")
        bypath[p] = row
        if row["type"] == "file":
            _need(review._SHA.fullmatch(row["sha256"]) is not None, "blob_identity")
            raw = rd.bytes(txdir / "blobs" / row["sha256"])
            _need(raw is not None and review._sha(raw) == row["sha256"] and len(raw) == row["size"], "blob_changed")
    commit, tree = identity["commit"], identity["tree"]
    _need(isinstance(commit, str) and _OID.fullmatch(commit)
          and isinstance(tree, str) and _OID.fullmatch(tree), "git_identity")
    _need(rd.git(root, ["rev-parse", "--verify", commit + "^{commit}"]).strip() == commit.encode(), "commit_mismatch")
    _need(rd.git(root, ["rev-parse", "--verify", commit + "^{tree}"]).strip() == tree.encode(), "tree_mismatch")
    for entry in filter(None, rd.git(root, ["ls-tree", "-rz", "--full-tree", commit]).split(b"\0")):
        meta, rel = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode("ascii").split()
        path = rel.decode("utf-8")
        if core.matches_protect(path, protect) or (root / ".git") == (root / path):
            continue
        _need(kind == "blob" and mode in ("100644", "100755"), "git_entry_unsupported", "unsupported")
        row = bypath.get(path)
        _need(row is not None and row["type"] == "file", "git_path_missing")
        raw = rd.git(root, ["cat-file", "blob", oid])
        _need(review._sha(raw) == row["sha256"] and bool(row["mode"] & 0o111) == (mode == "100755"),
              "git_blob_mismatch")
    return bypath


def _material(req, pkg):
    rel = pkg["release"] or {}
    return {"schema": review.SCHEMA_MATERIAL, "target": req["target"], "base": req["base"], "rules": req["rules"],
            "package": {"entries": pkg["entries"], "ignored": pkg["ignored"],
                        "release_json_sha256": rel.get("json_sha256"), "delete": rel.get("delete", []),
                        "wf_config": rel.get("wf_config", {}), "release_keys": rel.get("keys", [])}}


def _plan(req, pkg, baseline):
    """Recompute the copy/delete/config plan from the saved base and frozen ZIP."""
    from .release import _config_bytes
    before = baseline["paths"]
    want = {r["path"]: dict(r) for r in before}

    def parents(rel):
        for parent in reversed(Path(rel).parents):
            name = parent.as_posix()
            if name == ".":
                continue
            if name not in want:
                want[name] = {"path": name, "type": "dir", "mode": None}
            _need(want[name]["type"] == "dir", "plan_parent")

    for row in pkg["entries"]:
        if row["type"] == "file" and row["installed"]:
            parents(row["path"])
            want[row["path"]] = {k: row[k] for k in ("path", "type", "size", "sha256")}
            want[row["path"]]["mode"] = row["mode"] if row["mode"] is not None else 0o644
    manifest = pkg["release"] or {}
    delete = manifest.get("delete", [])
    for rel in delete:
        rel = rel.rstrip("/")
        want = {p: r for p, r in want.items() if p != rel and not p.startswith(rel + "/")}
    cfg, sha = None, None
    if req["kind"] == "project":
        raw = baseline["config"].encode()
        cfg = _json(raw)
        changed = False
        for key, val in manifest.get("wf_config", {}).items():
            if key in ("processes", "protect", "hook"):
                cfg[key], changed = val, True
        sha = review._sha(_config_bytes(cfg) if changed else raw)
        if manifest:
            paths = review._extra_touched(req["target"], pkg)
            _need(len(paths) == 1, "archive_identity")
            archive = paths[0]
            parents(archive)
            entry = next(e for e in pkg["entries"] if e["path"] == "RELEASE.json")
            raw = pkg["data"]["RELEASE.json"]
            want[archive] = {"path": archive, "type": "file", "mode": entry["mode"] or 0o644,
                             "size": len(raw), "sha256": review._sha(raw)}
    return {"before": before, "expected": sorted(want.values(), key=lambda r: r["path"]), "delete": delete,
            "config_after": cfg, "config_sha256": sha}


def _request(rd, local, rid):
    _need(isinstance(rid, str) and review._RID.fullmatch(rid), "request_id")
    req = rd.doc(local / "reviews/requests" / (rid + ".json"), True)
    _need(req["schema"] == review.SCHEMA_REQUEST and req["request_id"] == rid, "request_identity")
    _need(review._CID.fullmatch(req["content_id"]) is not None, "content_id")
    digest = req["package"]["sha256"]
    _need(review._SHA.fullmatch(digest) is not None, "package_id")
    raw = rd.bytes(local / "releases" / (digest + ".zip"))
    _need(raw is not None and review._sha(raw) == digest, "package_changed")
    pkg = review._package_bytes(raw, kind=req["kind"], protect=req["base"]["protect"])
    mat = _material(req, pkg)
    _need(_digest(mat) == req["content_id"] == req["material_sha256"], "material_mismatch")
    _need(_digest(mat["package"]) == req["package"]["entries_sha256"], "package_entries_mismatch")
    _need(not review._material_problems(req["base"], req["rules"]), "material_incomplete", "unknown")
    diff = rd.bytes(local / "reviews/requests" / (rid + ".diff"))
    _need(diff is not None and review._sha(diff) == req["diff"]["sha256"], "diff_changed")
    return req, pkg, mat, diff


def _trust(rd, local, home):
    cfg = rd.doc(local / "transport.json").get("review_verifier")
    _need(isinstance(cfg, dict), "verifier_unavailable_now", "unknown")
    p = _absolute(cfg["path"])
    _need(isinstance(cfg.get("sha256"), str) and review._SHA.fullmatch(cfg["sha256"]), "verifier_pin")
    for bad in (local / "reviews", local / "releases"):
        _need(p != bad and bad not in p.parents, "verifier_inside_host_state")
    _need(home not in p.parents or home / "local" in p.parents, "verifier_inside_tool")
    raw = rd.bytes(p)
    _need(raw is not None and rd.sources[str(p)]["mode"] & 0o111, "verifier_unavailable_now", "unknown")
    _need(review._sha(raw) == cfg["sha256"], "verifier_changed", "changed")
    return {"path": str(p), "sha256": review._sha(raw), "pinned": cfg["sha256"],
            "synthetic": bool(cfg.get("synthetic")), "why": ""}


def _review_history(rd, local, home, req, started, facts):
    cid, aid = req["content_id"], started["decision"]["attempt_id"]
    idx = rd.doc(local / "reviews/content" / (cid + ".json"))
    _need(idx.get("content_id") == cid, "index_identity")
    for field, rx in (("requests", review._RID), ("attempts", review._AID)):
        ids = idx[field]
        _need(isinstance(ids, list) and len(ids) == len(set(ids))
              and all(isinstance(i, str) and rx.fullmatch(i) for i in ids), "index_corrupt")
    # Selectors are closed over the actual registries, not only old index IDs.
    requests = {}
    for name, (kind, _mode) in rd.directory(local / "reviews/requests").items():
        if name.endswith(".json"):
            d = rd.doc(local / "reviews/requests" / name, True)
            if d.get("content_id") == cid:
                requests[d["request_id"]] = _request(rd, local, d["request_id"])[0]
    _need(set(requests) == set(idx["requests"]) and req["request_id"] in requests, "request_index_mismatch")
    attempts = {}
    for name, (kind, _mode) in rd.directory(local / "reviews/attempts").items():
        if not review._AID.fullmatch(name):
            continue
        _need(kind == "dir", "attempt_directory")
        a = rd.doc(local / "reviews/attempts" / name / "attempt.json")
        if a.get("content_id") == cid:
            _need(a["attempt_id"] == name and a["request_id"] in requests, "attempt_identity")
            attempts[name] = a
    _need(set(attempts) == set(idx["attempts"]) and aid in attempts, "attempt_index_mismatch")
    trust = _trust(rd, local, home)
    lanes = rd.doc(local / "lanes.json").get("lanes")
    _need(isinstance(lanes, dict), "lanes_invalid")
    rules = rd.bytes(home / review.RULES_PATH)
    _need(rules is not None and review._sha(rules) == req["rules"]["sha256"], "rules_changed", "changed")
    when = _time(started["times"]["install_started"])
    chosen = None
    facts["attempts"] = []
    for ident, a in attempts.items():
        folder = local / "reviews/attempts" / ident
        # Bind every raw member, including stderr of the verifier and outputs
        # of unsuccessful attempts. Missing declared outputs are corrupt.
        for name, (kind, _mode) in rd.directory(folder).items():
            _need(kind == "file", "attempt_member_unsupported", "unsupported")
            rd.bytes(folder / name)
        for group, mapping in (("files", {"stdout": "stdout.bin", "stderr": "stderr.bin", "result": "result.json"}),
                               ("sent", {"request": "request.json", "diff": "diff.patch"}),
                               ("verifier", {"stdout": "verify_stdout.bin", "stderr": "verify_stderr.bin"})):
            for key, filename in mapping.items():
                record = a.get(group, {}).get(key)
                if record is not None:
                    raw = rd.bytes(folder / filename)
                    _need(isinstance(record, dict) and raw is not None
                          and review._sha(raw) == record.get("sha256"), "attempt_raw_changed")
        ev = review._evaluate(requests[a["request_id"]], a, when, req, trust,
                              reader=rd.bytes, attempt_dir=folder, lanes=lanes)
        facts["attempts"].append({"attempt_id": ident, "state": a["state"], "verdict": ev["verdict"]})
        _need(not (a["state"] in ("completed", "late") and ev["verdict"] == "block"), "blocked", "failed")
        if ident == aid:
            chosen = (a, ev)
    a, ev = chosen
    _need(ev["eligible"], "review_revalidation:" + ",".join(ev["reasons"]), "failed")
    times = a["times"]
    _need(_time(times["review_started"]) <= _time(times["review_completed"])
          <= _time(times["result_registered"]) <= when, "review_install_timing", "failed")
    _need(all(started["times"][k] == times[k] for k in ("review_started", "review_completed", "result_registered")),
          "decision_times_mismatch")
    decision = started["decision"]
    _need(decision["provenance"] == ev["provenance"] and decision["verdict"] == ev["verdict"]
          and decision["expires_at"] == ev["expires_at"], "decision_provenance_changed", "changed")
    facts.update(synthetic=ev["provenance"]["synthetic"], provenance=ev["provenance"],
                 expires_at=ev["expires_at"], authorized_at_start=True)


def _collect(rd, context, ref, facts):
    _need(ref.get("schema") == "install-ref/1" and _TX.fullmatch(ref.get("tx_id", "")), "install_ref")
    _need(context["target"] == ref["target"], "target_mismatch")
    root = _absolute(ref["target"]["root"])
    kind = ref["target"]["kind"]
    _need(kind in ("tool", "project"), "target_kind")
    local, home = (_absolute(context["host"][k]) for k in ("local", "tool_root"))
    store = root / ("local/install" if kind == "tool" else "workflow/evidence/install")
    txdir = store / ref["tx_id"]
    facts.update(target=ref["target"], identities={k: ref[k] for k in
                 ("tx_id", "request_id", "content_id", "package_sha256")}, transactions=[])
    # Keep unsuccessful and other same-package attempts visible.
    for name, (typ, _mode) in rd.directory(store).items():
        if not name.startswith(("tx-", "restore-")):
            continue
        _need(typ == "dir", "transaction_directory")
        states = []
        for member, (mtyp, _mmode) in rd.directory(store / name).items():
            if member.endswith(".json"):
                _need(mtyp == "file", "transaction_record")
                doc = rd.doc(store / name / member, True)
                states.append({"record": member, "state": doc.get("state"), "request_id": doc.get("request_id")})
        facts["transactions"].append({"id": name, "records": states})
    prepared = rd.doc(txdir / "prepared.json", True)
    _need(prepared["schema"] == "install_tx/1" and prepared["state"] == "prepared"
          and prepared["body_sha256"] == ref["prepared_sha256"]
          and prepared["target_root"] == str(root) and prepared["kind"] == kind, "prepared_identity")
    _need(all(prepared[k] == ref[k] for k in ("tx_id", "request_id", "content_id", "package_sha256")),
          "prepared_ref_mismatch")
    expected_exec = {"schema": "install-exec/1", **{k: context[k] for k in ("subject", "turn", "exec")}}
    _need(prepared.get("exec") == expected_exec, "exec_identity_missing_or_mismatch", "unknown")
    _need(type(context["turn"]) is int and context["turn"] > 0
          and re.fullmatch(r"[0-9a-f]{32}", context["exec"]["nonce"]), "exec_identity")
    for name in ("failed.json", "partial.json", "recovered.json"):
        _need(rd.bytes(txdir / name) is None, "install_" + name[:-5], "failed")
    req, pkg, material, diff = _request(rd, local, ref["request_id"])
    _need(req == prepared["request"] and req["content_id"] == ref["content_id"]
          and req["target"]["root"] == str(root) and req["kind"] == kind
          and req["package"]["sha256"] == ref["package_sha256"], "transaction_request_mismatch")
    baseline = rd.doc(txdir / "baseline.json", True)
    _need(baseline["schema"] == "baseline/1" and baseline["source"] == "preinstall-verified"
          and baseline["status"] == "verified" and baseline["tx_id"] == ref["tx_id"]
          and baseline["body_sha256"] == prepared["baseline_sha256"]
          and baseline["baseline_id"] == prepared["baseline_id"], "baseline_identity")
    base = req["base"]
    _need(baseline["target"]["root"] == str(root) and baseline["target"]["kind"] == kind
          and baseline["target"]["protect"] == base["protect"]
          and baseline["protect_sha256"] == _digest(base["protect"])
          and baseline["config_sha256"] == base["config_sha256"]
          and baseline["identity"] == {"commit": base["commit"], "tree": base["tree"]}
          and baseline["touched"] == base["touched"] and baseline["control"] == base["control"],
          "baseline_request_mismatch")
    _need(baseline["inventory_sha256"] == _digest(baseline["paths"])
          and baseline["protected_paths_sha256"] == _digest(baseline["protected_paths"])
          and baseline["delete_closure_sha256"] == _digest([s for s in base["touched"] if s["role"] == "delete"]),
          "baseline_inventory_mismatch")
    control_blobs = baseline.get("control_blobs")
    _need(isinstance(control_blobs, list) and len(control_blobs) == len(base["control"]),
          "historical_control_bytes_unavailable", "unknown")
    for row, source in zip(base["control"], control_blobs):
        _need(source["path"] == row["path"], "control_source_identity")
        if source["type"] == "absent":
            _need(source == row, "control_source_mismatch")
            continue
        _need(source["type"] == "file", "control_source_unknown", "unknown")
        raw = rd.bytes(txdir / "blobs" / source["sha256"])
        _need(raw is not None and review._sha(raw) == source["sha256"]
              and len(raw) == source["size"], "control_blob_changed")
        expected = dict(source)
        if row["type"] == "approvals":
            approvals = _json(raw)
            expected = {k: v for k, v in source.items() if k not in ("size", "sha256")}
            expected.update(type="approvals", projection={k: approvals.get(k) for k in ("policy", "goals")})
        _need(expected == row, "control_projection_mismatch")
    before = _tree(rd, root, baseline["identity"], baseline["paths"], base["protect"], txdir)
    _need(prepared["plan"]["before"] == baseline["paths"], "plan_baseline_mismatch")
    _need(prepared["plan"] == _plan(req, pkg, baseline), "plan_package_mismatch")
    if kind == "tool":
        _need(base["inventory_sha256"] == baseline["inventory_sha256"]
              and base["inventory_count"] == len(before), "tool_baseline_inventory")
    else:
        _need(review._sha(baseline["config"].encode()) == base["config_sha256"], "baseline_config")
    side = {}
    for row in base["touched"]:
        bare = {k: v for k, v in row.items() if k not in ("role", "closure")}
        actual = before.get(row["path"], {"path": row["path"], "type": "absent"})
        _need(bare == actual, "baseline_touched_mismatch")
        if row["type"] == "dir" and row["role"] == "delete":
            closure = [r for p, r in sorted(before.items()) if p.startswith(row["path"] + "/")]
            _need(row["closure"] == closure, "baseline_delete_closure")
        side[row["path"]] = (row, rd.bytes(txdir / "blobs" / row["sha256"]) if row["type"] == "file" else None)
    _need(review._full_diff(req["target"], material, pkg, side) == diff, "historical_diff_mismatch")
    started = rd.doc(txdir / "started.json", True)
    _need(started["schema"] == "install_tx/1" and started["state"] == "started"
          and started["tx_id"] == ref["tx_id"] and started["prepared_sha256"] == prepared["body_sha256"]
          and started["decision_sha256"] == _digest(started["decision"])
          and started["times"] == started["decision"]["times"], "started_identity")
    dec = started["decision"]
    _need(dec["request_id"] == req["request_id"] and dec["content_id"] == req["content_id"]
          and dec["phase"] == "install" and dec["allowed"] is True, "started_decision")
    installs, matches = local / "reviews/installs" / req["content_id"], []
    for name, (typ, _mode) in rd.directory(installs).items():
        _need(typ == "file" and name.endswith(".json"), "review_install_member")
        rec = rd.doc(installs / name)
        _need(rec.get("schema") == "review-install/1"
              and rec.get("decision_sha256") == _digest(rec["decision"]), "review_install_hash")
        if (rec.get("install_context") or {}).get("tx_id") == ref["tx_id"]:
            matches.append(rec)
    _need(len(matches) == 1, "review_install_link_missing_or_duplicate")
    rec = matches[0]
    _need(rec["decision"] == dec and rec["request_id"] == req["request_id"]
          and rec["attempt_id"] == dec["attempt_id"] and rec["content_id"] == req["content_id"]
          and rec["install_started"] == dec["times"]["install_started"]
          and rec["install_context"] == {"tx_id": ref["tx_id"], "target_root": str(root),
              "lock_token": prepared["lock"]["token"], "prepared_sha256": prepared["body_sha256"]}, "review_install_link")
    done = rd.doc(txdir / "completed.json", True)
    _need(done["schema"] == "install_completed/1" and done["state"] == "completed"
          and done["tx_id"] == ref["tx_id"] and done["request_id"] == req["request_id"]
          and done["content_id"] == req["content_id"] and done["target"] == req["target"]
          and done["package_sha256"] == ref["package_sha256"]
          and done["prepared_sha256"] == prepared["body_sha256"]
          and done["started_sha256"] == started["body_sha256"]
          and done["baseline_sha256"] == baseline["body_sha256"]
          and done["baseline_id"] == baseline["baseline_id"] and done["protect"] == base["protect"], "completed_identity")
    _need(all(done["times"][k] == v for k, v in started["times"].items()), "completed_times")
    _need(_time(baseline["verified_at"]) <= _time(done["times"]["install_started"])
          <= _time(done["times"]["install_completed"]), "install_timing", "failed")
    actual = done["actual"]
    after = _tree(rd, root, actual, actual["files"], base["protect"], txdir)
    _need(actual["tree_sha256"] == _digest(actual["files"]), "completed_inventory")
    expected = {r["path"]: r for r in prepared["plan"]["expected"]}
    _need(set(expected) == set(after) and all(all(v == after[p].get(k) for k, v in row.items()
              if not (k == "mode" and v is None)) for p, row in expected.items()), "installed_plan_mismatch")
    _need(actual["config_sha256"] == prepared["plan"]["config_sha256"], "installed_config_mismatch")
    deleted = [after.get(p.rstrip("/"), {"path": p.rstrip("/"), "type": "absent"})
               for p in prepared["plan"]["delete"]]
    _need(actual["deleted"] == deleted, "installed_delete_mismatch")
    control = [dict(row) for row in base["control"]]
    if kind == "tool":
        control = [after.get(row["path"], {"path": row["path"], "type": "absent"}) for row in control]
    else:
        from .release import _config_bytes
        raw = baseline["config"].encode()
        if review._sha(raw) != actual["config_sha256"]:
            raw = _config_bytes(prepared["plan"]["config_after"])
        control[0].update(sha256=review._sha(raw), size=len(raw))
    _need(actual["control"] == control, "installed_control_mismatch")
    facts.update(completed=True, times=done["times"], base=base, rules=req["rules"])
    facts["identities"]["attempt_id"] = dec["attempt_id"]
    _review_history(rd, local, home, req, started, facts)


def collect_install_history(context, install_ref, reader):
    """Return facts, never an install grant; replays only captured reader inputs."""
    rd = _Read(reader)
    facts = {"completed": False, "authorized_at_start": False, "synthetic": None}
    result = {"schema": "install-history/1", "state": "unknown", "facts": facts,
              "sources": [], "selectors": [], "diagnostics": []}
    try:
        _collect(rd, context, install_ref, facts)
        result["state"] = "valid"
    except _Invalid as exc:
        result.update(state=exc.state, diagnostics=[exc.why])
    except (OSError, ValueError, TypeError, KeyError, AttributeError, core.WfError) as exc:
        result.update(state="corrupt", diagnostics=["history_boundary:" + type(exc).__name__])
    result["sources"] = [rd.sources[k] for k in sorted(rd.sources)]
    result["selectors"] = [rd.selectors[k] for k in sorted(rd.selectors)]
    return result
