"""FX-TX driver: 16 real install chains (project/tool x completed/failed/partial/recovered x receipt/none)
plus the CL-CT5 zero-exec window, each consumed by three real consumers:

1. ``release._InstallLock`` (its ``_check_incomplete`` runs on enter);
2. ``review.collect_install_history`` over a capture reader, with the actual receipt binding only;
3. ``live._capture_inputs`` -> ``live._evaluate_snapshot`` on a real round window, plus frozen replay.

``python -B -S -m wflib.selftest_install_fixtures [--mode new|old] [--out FILE]`` starts one private child
(setsid, ``-S``, own HOME/TMPDIR/WF_LOCAL_DIR) importing from this source tree. ``--mode new`` enforces the
expectations below; ``--mode old`` (an older tree with these two files added) only records what each
consumer actually does. Everything is synthetic; nothing is authorised.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CHILD = "WF_FX_TX_CHILD"


def _window(inst, tag: str):
    """A real round-1 window (same inputs as the C5 fixtures) on ``inst``; installs already happened."""
    from datetime import timedelta
    from . import core
    from .selftest_review import _git
    inst = core.Instance(inst.root)
    inst.state.update(state="EXECUTE", turn=1)
    inst.save()
    at = core.now()
    sent, done = at - timedelta(minutes=1), at + timedelta(seconds=2)
    for kind, fields in (("reply_binding", {"receipt": "ok", "deliveries": "ok"}), ("reply_checked", {}),
                         ("round_result", {"ok": True})):
        inst.event(kind, by="driver", **fields)
    inst.turns_path.write_text(json.dumps({"turn": 1, "status": "sent", "from": "driver", "ts_sent": sent.isoformat()}) + "\n"
                               + json.dumps({"turn": 1, "status": "done", "ts_done": done.isoformat()}) + "\n", encoding="utf-8")
    td = inst.turn_dir()
    for name, doc in (("intake.json", {"commands": []}), ("send_meta.json", {"message_uuid": f"fx-{tag}"}),
                      ("reply_meta.json", {"parent": f"fx-{tag}", "ended_at": done.isoformat()})):
        (td / name).write_text(json.dumps(doc), encoding="utf-8")
    if not (td / "notes.jsonl").exists():
        (td / "notes.jsonl").write_bytes(b"")
    adapter = inst.root.parent / f"adapter-{tag}.jsonl"
    adapter.write_text(json.dumps({"action": "send", "turn": 1, "ts": sent.isoformat(), "project": inst.name,
                                   "root": str(inst.root)}) + "\n", encoding="utf-8")
    tp = core.LOCAL / "transport.json"
    cfg = json.loads(tp.read_bytes())
    cfg["log"] = str(adapter)
    tp.write_text(json.dumps(cfg), encoding="utf-8")
    head = _git(inst.root, "rev-parse", "HEAD")
    sync = core.LOCAL / "sync" / (inst.name + ".history.jsonl")
    sync.parent.mkdir(parents=True, exist_ok=True)
    sync.write_text(json.dumps({"turn": 1, "status": "pushed", "commit": head, "head": head, "ts": done.isoformat(),
                                "project": inst.name, "root": str(inst.root)}) + "\n", encoding="utf-8")
    return inst


def _lock(d) -> str:
    """Real ``_InstallLock`` (its ``_check_incomplete`` runs on enter). For a recovered transaction the fixture also
    re-checks the target against the transaction baseline while holding the lock: the product lock only checks the
    records, so a target rewritten after recovery would otherwise pass as a closed recovery."""
    from . import install_fixtures as fx, release
    from .core import WfError
    try:
        with release._InstallLock(Path(d["target"]), d["kind"]):
            if d["outcome"] == "recovered":
                problems = fx.verify_recovered(d["target"], d["kind"], d["store"], d["tx"])
                if problems:
                    return "refused (fixture, under the lock): recovered target diverged: " + "; ".join(problems[:3])
            return "allowed"
    except WfError as e:
        return "refused: " + str(e)[:200]


def _product_lock(d) -> str:
    from . import release
    from .core import WfError
    try:
        with release._InstallLock(Path(d["target"]), d["kind"]):
            return "allowed"
    except WfError as e:
        return "refused: " + str(e)[:200]


def _reader():
    from . import live, live_history
    return live_history.CaptureReader(lambda p: Path(p).read_bytes() if os.path.lexists(p) else None, live._directory_source)


def _history(d, env) -> dict:
    from . import review
    host = {"local": str(env.local), "tool_root": d["tool_root"]}
    if not d["with_receipt"]:
        prepared = json.loads((Path(d["store"]) / d["tx"] / "prepared.json").read_bytes())
        ref = {"schema": "install-ref/1", "tx_id": d["tx"], "request_id": prepared["request_id"],
               "content_id": prepared["content_id"], "package_sha256": prepared["package_sha256"],
               "target": {"kind": d["kind"], "root": d["target"]}, "prepared_sha256": prepared["body_sha256"]}
        fake = {"subject": {"project": "fx-fabricated", "root": d["subject"] or d["target"]}, "turn": 1,
                "exec": {"id": "c01", "nonce": "0" * 32}, "target": ref["target"], "host": host}
        h = review.collect_install_history(fake, ref, _reader())
        return {"binding": "not_bound: no actual invocation/reference",
                "prepared_derived_ref_with_fabricated_exec": {"state": h["state"], "completed": h["facts"].get("completed"),
                                                              "diagnostics": h["diagnostics"][:3]}}
    ref, inv = d["ref"], d["exec"]["invocation"]
    ctx = {k: inv[k] for k in ("subject", "turn", "exec")}
    ctx.update(target=ref["target"], host=host)
    h = review.collect_install_history(ctx, ref, _reader())
    wrong = dict(ctx, exec={"id": inv["exec"]["id"], "nonce": "f" * 32})
    w = review.collect_install_history(wrong, ref, _reader())
    f = h["facts"]
    return {"binding": "receipt", "state": h["state"], "completed": f.get("completed"),
            "authorized_at_start": f.get("authorized_at_start"), "synthetic": f.get("synthetic"),
            "diagnostics": h["diagnostics"][:3], "wrong_nonce": {"state": w["state"], "diagnostics": w["diagnostics"][:2]}}


def _live(inst, tool_root, before=None) -> dict:
    from unittest.mock import patch
    from . import core, live, release
    try:
        with patch.object(core, "WF_HOME", Path(tool_root)), patch.object(release, "WF_HOME", Path(tool_root)):
            snap = live._capture_inputs(inst, rounds=1)
            res = live._evaluate_snapshot(snap)
            with patch.object(live, "_file_source", side_effect=AssertionError("replay read disk")), \
                 patch.object(live, "_directory_source", side_effect=AssertionError("replay listed disk")), \
                 patch.object(Path, "resolve", side_effect=AssertionError("replay resolved a path")):
                replay_same = live._evaluate_snapshot(snap) == res
            revalidated = live._revalidate_inputs(inst, snap.descriptor)["valid"]
            stale = (live._revalidate_inputs(inst, before.descriptor)["valid"] if before is not None else None)
    except Exception as e:   # noqa: BLE001 — recorded, the expectation check decides
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}
    ri = snap.descriptor["review_inputs"]
    rows = [{"seq": r.get("seq"), "turn": r.get("turn"), "ref_tx": (r.get("reference") or {}).get("tx_id"),
             "state": r.get("state"), "store": (r.get("transaction") or {}).get("store"),
             "tx": (r.get("transaction") or {}).get("id"), "attribution": (r.get("transaction") or {}).get("attribution")}
            for r in ri.get("entries", [])]
    rounds = res["report"].get("rounds") or [{}]
    crit = next((c for c in rounds[0].get("criteria", []) if c.get("n") == 4), {})
    return {"review_inputs": ri.get("schema") or ri.get("state"), "entries": rows,
            "criterion4": {"status": crit.get("status"), "attempts": crit.get("attempts")},
            "authorization": res.get("authorization"), "reason": res.get("reason"), "replay_same": replay_same,
            "fresh_descriptor_valid": revalidated, "old_descriptor_valid": stale, "_snap": snap}


def _expect(d, c1, c2, c3) -> list:
    """Expectations for the modern tree; derived from the transaction itself, not from other tests' totals."""
    p = []
    if (c1 == "allowed") != (d["outcome"] != "partial") or (d["outcome"] == "partial" and "install_incomplete" not in c1):
        p.append(f"lock {c1}")
    if d["with_receipt"]:
        clean = d["outcome"] == "completed"
        if clean and not (c2["state"] == "valid" and c2["completed"] and c2["authorized_at_start"] and c2["synthetic"] is True):
            p.append(f"history completed {c2}")
        if not clean and c2["state"] == "valid":
            p.append(f"history {d['outcome']} counted as valid")
        if c2["wrong_nonce"]["state"] == "valid":
            p.append("wrong nonce valid")
    elif c2["prepared_derived_ref_with_fabricated_exec"]["state"] == "valid":
        p.append("prepared-derived ref with fabricated exec became valid")
    if d["worker"].get("refused") or any(x["executable"] not in d["worker"]["allowed"] for x in d["worker"]["spawned"]):
        p.append("worker spawn outside the gate")
    if d["outcome"] == "recovered" and d.get("recovered_check") != []:
        p.append(f"recovered not independently verified: {d.get('recovered_check')}")
    if "error" in c3:
        p.append("live " + c3["error"])
        return p
    mine = [r for r in c3["entries"] if d["tx"] in (r["ref_tx"], r["tx"])]
    if not mine:
        p.append("transaction not visible in the window")
    if any(r["state"] == "valid" for r in mine) and d["outcome"] != "completed":
        p.append("non-completed transaction valid in the window")
    if c3["authorization"] != "unavailable" or not c3["replay_same"]:
        p.append(f"authorization {c3['authorization']} replay {c3['replay_same']}")
    return p


def _window_for(observed):
    from . import admin, core
    from .selftest_review import _git
    if observed.get("subject"):
        return core.Instance(Path(observed["subject"]))
    window = admin.init_project(Path(observed["case"]) / "window", name=f"fx-window-{Path(observed['case']).name}")
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "initial")):
        _git(window.root, *args)
    return window


def _tamper_negatives(env) -> dict:
    """PROJECT.md rewritten at the recovery return boundary (real recovery, controlled write right after it
    returns): the chain refuses, and lock, history and live all fail closed on the observed files."""
    from . import install_fixtures as fx
    out = {}
    for receipt in (True, False):
        name = f"project/recovered/{'receipt' if receipt else 'no-receipt'} + tamper"
        try:
            fx.chain(env, kind="project", outcome="recovered", with_receipt=receipt, _inject="tamper_after_recover")
            out[name] = {"chain": "ACCEPTED (bad)"}
            continue
        except fx.FixtureError as e:
            o = e.observed
            if not o:
                out[name] = {"chain": f"error without transaction: {e}"}
                continue
            row = {"chain": "refused: " + str(e)[:200], "recovered_check": o.get("recovered_check"), "fault": o.get("fault")}
        d = dict(o, worker={"spawned": [], "refused": []})
        row["product_lock_raw"] = _product_lock(d)
        row["lock"] = _lock(d)
        if receipt:
            d["ref"] = o.get("ref") or (o["exec"]["install_refs"] or [None])[0]
        row["history"] = _history(d, env)
        c3 = _live(_window(_window_for(o), Path(o["case"]).name + "-tamper"), o["tool_root"])
        c3.pop("_snap", None)
        mine = [r for r in c3.get("entries", []) if o["tx"] in (r["ref_tx"], r["tx"])]
        row["live"] = {"rows": mine, "authorization": c3.get("authorization"), "error": c3.get("error")}
        h = row["history"]
        row["fail_closed"] = {
            "chain": row["chain"].startswith("refused") and any("PROJECT.md" in x for x in (o.get("recovered_check") or [])),
            "lock": row["lock"].startswith("refused"),
            "history": (h.get("state") if receipt else h["prepared_derived_ref_with_fabricated_exec"]["state"]) != "valid",
            "live": bool(mine) and all(r["state"] != "valid" for r in mine) and c3.get("authorization") == "unavailable"}
        out[name] = row
    return out


def _negatives(env) -> dict:
    """Refusals happen before any case directory exists; dependency drift is never auto-accepted."""
    from unittest.mock import patch
    from . import core, install_fixtures as fx, selftest_review as sr
    res = {}

    def attempt(name, **kw):
        before = sorted(p.name for p in env.cases.glob("*")) if env.cases.exists() else []
        try:
            fx.chain(env, **{"kind": "project", "outcome": "completed", "with_receipt": True, **kw})
            res[name] = "accepted"
        except fx.FixtureRefused as e:
            after = sorted(p.name for p in env.cases.glob("*")) if env.cases.exists() else []
            res[name] = "refused, nothing written" if after == before else f"refused but wrote {set(after) - set(before)}"
            res[name + ":why"] = str(e)[:160]
    for label, value in (("with_receipt=1", 1), ("with_receipt=0", 0), ("with_receipt='yes'", "yes")):
        attempt(label, with_receipt=value)
    attempt("kind=unknown", kind="service")
    attempt("outcome=unknown", outcome="aborted")
    with patch.dict(os.environ, {"WF_INSTALL_EXEC_CONTEXT": "{}"}):
        attempt("inherited exec context")
    with patch.object(core, "LOCAL", env.base / "elsewhere"):
        attempt("LOCAL not the env's")
    with patch.object(sr, "RUNNER", sr.RUNNER + "# drift\n"):
        attempt("dependency drift: RUNNER template")
    with patch.object(sr.Env, "pkg", lambda self, name, files=None, *, extra=None: None):
        attempt("dependency drift: Env.pkg signature")
    try:
        fx.chain(object(), kind="project", outcome="completed", with_receipt=True)
        res["foreign env object"] = "accepted"
    except fx.FixtureRefused:
        res["foreign env object"] = "refused, nothing written"
    return res


def _relative_cwd_probe(base: Path) -> dict:
    """Real executable canaries; every directory, including the outside canary, is disposable."""
    import shlex
    from . import install_fixtures as fx
    root = base / "relative-child-cwd"
    origin, other, outside = root / "case" / "origin", root / "case" / "other", root / "outside-case"
    for d in (origin / "bin", other / "bin", other / "anchor", outside):
        d.mkdir(parents=True)
    marker = outside / "UNAPPROVED_EXECUTABLE_RAN"
    allowed = origin / "bin" / "allowed"
    allowed.write_text("#!/bin/sh\nprintf allowed\n")
    allowed.chmod(0o755)
    for path in (other / "bin" / "allowed", other / "allowed"):
        path.write_text("#!/bin/sh\nprintf wrong > " + shlex.quote(str(marker)) + "\n")
        path.chmod(0o755)
    (origin / "allowed").symlink_to(allowed)
    (origin / "hop").symlink_to(other / "anchor", target_is_directory=True)
    spec = {"write_roots": [str(origin), str(other)], "allowed_exec": [str(allowed)],
            "git": os.path.realpath(shutil.which("git"))}
    worker_env = {"PATH": "/usr/bin:/bin", "HOME": str(origin), "TMPDIR": str(origin)}
    cases = [
        ("relative argv / absolute child cwd", '["bin/allowed"]', "str(other)", None, None, False),
        ("relative override", '["bin/allowed"]', "str(other)", '"bin/allowed"', None, False),
        ("relative PATH", '["allowed"]', "str(other)", None, '"bin:/usr/bin:/bin"', False),
        ("empty PATH entry", '["allowed"]', "str(other)", None, '":/usr/bin:/bin"', False),
        ("relative child cwd", '["bin/allowed"]', '"../other"', None, None, False),
        ("bytes child cwd", '["bin/allowed"]', "os.fsencode(other)", None, None, False),
        ("Path child cwd", '["bin/allowed"]', "other", None, None, False),
        ("symlink dotdot executable", '["hop/../bin/allowed"]', "origin", None, None, False),
        ("invalid cwd type", '["bin/allowed"]', "7", None, None, False),
        ("control relative argv / None cwd", '["bin/allowed"]', "None", None, None, True),
        ("control relative PATH", '["allowed"]', "origin", None, '"bin:/usr/bin:/bin"', True),
        ("control empty PATH entry", '["allowed"]', "origin", None, '":/usr/bin:/bin"', True),
        ("control override / Path cwd", '["bin/allowed"]', "origin", '"bin/allowed"', None, True),
        ("control canonical symlink dotdot cwd", '["bin/allowed"]',
         'origin / "hop" / ".." / ".." / "origin"', None, None, True),
    ]
    # The last cwd walks from other/anchor through the actual symlink target
    # back to case/origin, exercising realpath before lexical normalisation.
    before = fx._snapshot([root], skip=[root / "case"])
    results = []
    for name, argv, cwd, executable, path, accepted in cases:
        tail = "\norigin=Path(%r); other=Path(%r)\n" % (str(origin), str(other))
        tail += "child=dict(os.environ)\n"
        if path is not None:
            tail += "child['PATH']=" + path + "\n"
        call = "subprocess.run(%s,cwd=%s,env=child,capture_output=True,text=True,timeout=10%s)" % (
            argv, cwd, ",executable=" + executable if executable else "")
        tail += ("try:\n r=" + call + "\n result={'status':'accepted','rc':r.returncode,'stdout':r.stdout}\n"
                 "except GuardRefused as exc:\n result={'status':'refused','why':str(exc)}\n"
                 "except Exception as exc:\n result={'status':'error','why':type(exc).__name__+': '+str(exc)}\n"
                 "print(json.dumps(dict(result,spawned=spawned,refused=refused)))\n")
        r = subprocess.run([sys.executable, "-B", "-S", "-c", fx.GUARD + tail, json.dumps(spec)],
                           cwd=origin, env=worker_env, capture_output=True, text=True, timeout=20)
        try:
            row = json.loads(r.stdout)
        except ValueError:
            row = {"status": "worker error", "stderr": r.stderr[-2000:]}
        good = r.returncode == 0 and (row["status"] == "accepted" and row["rc"] == 0
                                     and row["stdout"] == "allowed" if accepted else row["status"] == "refused")
        if accepted and good:
            good = bool(row["spawned"]) and all(s["executable"] == str(allowed) for s in row["spawned"])
        results.append({"case": name, "expected_accepted": accepted, "ok": good, "result": row})
    after = fx._snapshot([root], skip=[root / "case"])
    return {"cases": results, "outside_marker_created": marker.exists(),
            "outside_case_unchanged": before == after,
            "ok": all(r["ok"] for r in results) and not marker.exists() and before == after}


def _child(base: Path, out: Path, mode: str) -> int:
    from . import core, install_fixtures as fx, live
    t0 = time.time()
    env = fx.make_env(base / "env")
    report = {"env": env.describe(), "mode": mode, "uid": os.getuid(), "sid": os.getsid(0), "pid": os.getpid(),
              "python": sys.version.split()[0], "executable": sys.executable, "chains": [], "cl_ct5": None}
    failures = []
    for kind in fx.KINDS:
        for outcome in fx.OUTCOMES:
            for receipt in (True, False):
                name = f"{kind}/{outcome}/{'receipt' if receipt else 'no-receipt'}"
                s = time.time()
                try:
                    d = fx.chain(env, kind=kind, outcome=outcome, with_receipt=receipt)
                except Exception as e:   # noqa: BLE001
                    report["chains"].append({"case": name, "error": f"{type(e).__name__}: {str(e)[:600]}"})
                    failures.append(name + " chain")
                    continue
                c1, c2 = _lock(d), _history(d, env)
                window = core.Instance(Path(d["subject"])) if d["subject"] else None
                if window is None:   # tool install without exec: an ordinary project window, tool store via WF_HOME
                    from . import admin
                    from .selftest_review import _git
                    window = admin.init_project(Path(d["case"]) / "window", name=f"fx-window-{Path(d['case']).name}")
                    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "initial")):
                        _git(window.root, *args)
                c3 = _live(_window(window, Path(d["case"]).name), d["tool_root"])
                c3.pop("_snap", None)
                problems = _expect(d, c1, c2, c3) if mode == "new" else []
                failures += [f"{name}: {x}" for x in problems]
                report["chains"].append({"case": name, "tx": d["tx"], "records": sorted(d["records"]), "worker_rc": d["worker"]["rc"],
                                         "fault": d["fault"], "prepared_exec": d["prepared_exec"] is not None,
                                         "receipt_rows": len(d["receipt"]["rows"]) if d["receipt"] else None,
                                         "spawned": sorted({os.path.basename(x["argv"][0]) for x in d["worker"]["spawned"] or []}),
                                         "lock": c1, "history": c2, "live": c3, "problems": problems,
                                         "seconds": round(time.time() - s, 2), "chain": d})
    # CL-CT5: a ready zero-exec window frozen first, then a real failed install without receipt.
    seen = {}

    def freeze(inst):
        from unittest.mock import patch
        from . import release
        w = _window(inst, "cl-ct5")
        with patch.object(core, "WF_HOME", env.host.tool), patch.object(release, "WF_HOME", env.host.tool):
            seen["before"] = live._capture_inputs(w, rounds=1)
            seen["first"] = live._evaluate_snapshot(seen["before"])
        seen["events"] = w.events_path.read_bytes()
    try:
        d = fx.chain(env, kind="project", outcome="failed", with_receipt=False, before_install=freeze)
        inst = core.Instance(Path(d["target"]))
        events_after = inst.events_path.read_bytes()
        c3 = _live(inst, d["tool_root"], before=seen["before"])
        c3.pop("_snap", None)
        stale, fresh_valid = c3.get("old_descriptor_valid"), c3.get("fresh_descriptor_valid")
        first_c4 = next(c for c in seen["first"]["report"]["rounds"][0]["criteria"] if c["n"] == 4)
        cl = {"tx": d["tx"], "records": sorted(d["records"]), "events_unchanged": events_after == seen["events"],
              "target_tree_changed": d["target_tree_changed"], "exec_events": sum(1 for x in events_after.splitlines()
                                                                                 if b'"type": "exec"' in x),
              "before_criterion4": first_c4.get("status"), "old_descriptor_valid_after_tx": stale,
              "new_descriptor_valid": fresh_valid, "live": c3, "lock": _lock(d), "history": _history(d, env)}
        report["cl_ct5"] = cl
        if mode == "new":
            want = {"total": 1, "ok": 0, "timeout": 0, "unknown": 0, "unbound_transactions": 1, "failed": 1}
            rows = [r for r in c3.get("entries", []) if r["tx"] == d["tx"]]
            checks = {"three records": cl["records"] == ["baseline.json", "failed.json", "prepared.json"],
                      "events bytes unchanged": cl["events_unchanged"], "target unchanged": not cl["target_tree_changed"],
                      "zero exec": cl["exec_events"] == 0, "visible failed/unknown": bool(rows) and rows[0]["state"] in ("failed", "unknown"),
                      "attempts": c3.get("criterion4", {}).get("attempts") == want,
                      "unavailable": c3.get("authorization") == "unavailable", "old descriptor stale": stale is False,
                      "new descriptor valid": fresh_valid is True, "replay": c3.get("replay_same") is True}
            cl["checks"] = checks
            failures += [f"CL-CT5: {k}" for k, v in checks.items() if not v]
    except Exception as e:   # noqa: BLE001
        report["cl_ct5"] = {"error": f"{type(e).__name__}: {str(e)[:600]}"}
        failures.append("CL-CT5 chain")
    tamper = _tamper_negatives(env)
    report["tamper_negatives"] = tamper
    for name, row in tamper.items():
        failures += [f"tamper {name}: {k} not fail-closed" for k, v in (row.get("fail_closed") or {"chain": False}).items() if not v]
    probe = fx.audit_probe(env, outside=base / "outside-canary")
    report["audit_probe"] = probe
    bypass = {k: v for k, v in probe["results"].items() if not k.startswith("control")}
    controls = {k: v for k, v in probe["results"].items() if k.startswith("control")}
    report["audit_checks"] = {
        "every bypass refused": all(v.startswith("refused") for v in bypass.values()),
        "no marker from any bypass program": probe["markers"] == [],
        "nothing written outside the probe case": probe["changed_outside_case"] == [],
        "real git accepted": controls.get("control: real git inside the case", "").startswith("accepted rc=0"),
        "inherited child env sanitised": controls.get("control: allowed interpreter, inherited env sanitised", "")
                                         == "accepted rc=0 child_sees_context=False",
        "explicit child env sanitised": controls.get("control: allowed interpreter, explicit env sanitised", "")
                                        == "accepted rc=0 child_sees_context=False"}
    failures += [f"audit: {k}" for k, v in report["audit_checks"].items() if not v]
    report["relative_cwd_probe"] = _relative_cwd_probe(base)
    if not report["relative_cwd_probe"]["ok"]:
        failures.append("relative child cwd executable audit")
    report["negatives"] = _negatives(env)
    bad = [k for k, v in report["negatives"].items() if not k.endswith(":why") and v != "refused, nothing written"]
    failures += [f"negative {k}: {report['negatives'][k]}" for k in bad]
    report.update(failures=failures, seconds=round(time.time() - t0, 1))
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    for c in report["chains"]:
        print(f"{'✓' if not c.get('problems') and 'error' not in c else '✗'} {c['case']}: tx={c.get('tx')} "
              f"records={c.get('records')} lock={str(c.get('lock'))[:40]} "
              f"history={(c.get('history') or {}).get('state') or (c.get('history') or {}).get('binding')} "
              f"live={(c.get('live') or {}).get('criterion4')} auth={(c.get('live') or {}).get('authorization')}"
              + (f" ERROR {c['error']}" if "error" in c else ""), flush=True)
    print("CL-CT5:", json.dumps({k: v for k, v in (report["cl_ct5"] or {}).items() if k in ("checks", "error", "records")},
                                ensure_ascii=False), flush=True)
    for name, row in report["tamper_negatives"].items():
        print(f"tamper {name}: fail_closed={row.get('fail_closed')} product_lock_raw={row.get('product_lock_raw')}",
              flush=True)
    print("audit probe:", json.dumps(report["audit_checks"], ensure_ascii=False), flush=True)
    for k, v in report["audit_probe"]["results"].items():
        print(f"   {k}: {v[:110]}", flush=True)
    print("negatives:", json.dumps({k: v for k, v in report["negatives"].items() if not k.endswith(":why")},
                                   ensure_ascii=False), flush=True)
    print(f"FX-TX {mode}: chains {len(report['chains'])}, failures {len(failures)}", flush=True)
    for f in failures:
        print("  ✗", f, flush=True)
    return 0 if mode == "old" or not failures else 1


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("new", "old"), default="new")
    ap.add_argument("--out")
    ap.add_argument("--child-base")
    a = ap.parse_args(argv)
    tree = Path(__file__).resolve().parent.parent
    if os.environ.get(CHILD) == "1":
        return _child(Path(a.child_base), Path(a.out), a.mode)
    if not sys.flags.dont_write_bytecode:
        # The source tree must stay read-only; the import system would cache bytecode next to the sources.
        print("FX-TX refused: run with python -B (no bytecode written into the source tree)", flush=True)
        return 2
    base = Path(tempfile.mkdtemp(prefix="wf-fx-tx-")).resolve()
    os.chmod(base, 0o700)
    for d in ("home", "tmp", "env/local", "bin"):
        (base / d).mkdir(parents=True, exist_ok=True)
    os.symlink(os.path.realpath(sys.executable), base / "bin" / "python3")
    out = Path(a.out).resolve() if a.out else base / "fx-tx-report.json"
    env = {"PATH": f"{base / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin", "HOME": str(base / "home"),
           "TMPDIR": str(base / "tmp"), "WF_LOCAL_DIR": str(base / "env" / "local"), "WF_NOTIFY": "0",
           "LC_ALL": "C.UTF-8", "PYTHONPATH": str(tree), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(base / "home" / ".gitconfig"), CHILD: "1"}
    (base / "home" / ".gitconfig").write_text("[user]\n\tname = fx-tx\n\temail = fx-tx@localhost\n", encoding="utf-8")
    argv = [os.path.realpath(sys.executable), "-S", "-m", "wflib.selftest_install_fixtures", "--mode", a.mode,
            "--out", str(out), "--child-base", str(base)]
    def digest():
        """Every path in the source tree (sources, caches, anything new), with type, mode and content."""
        import hashlib
        h = hashlib.sha256()
        for dp, dns, fns in sorted(os.walk(tree)):
            dns.sort()
            for n in sorted(dns + fns):
                p = Path(dp) / n
                st = os.lstat(p)
                h.update(str(p.relative_to(tree)).encode() + b"\0" + oct(st.st_mode).encode() + b"\0")
                if p.is_file() and not p.is_symlink():
                    h.update(p.read_bytes())
        return h.hexdigest()
    before = digest()
    t = time.time()
    try:
        r = subprocess.run(argv, cwd=str(tree), env=env, timeout=1800, start_new_session=True)
        rc = r.returncode
    except subprocess.TimeoutExpired:
        rc = 124
    after = digest()
    print(f"FX-TX child: argv={argv[:3]}... cwd={tree} rc={rc} seconds={time.time() - t:.1f} report={out}", flush=True)
    print(f"FX-TX source tree read-only: {before == after} (every path, mode and byte; sha256 {after})", flush=True)
    if before != after:
        rc = rc or 1
    if not a.out:
        shutil.rmtree(base, ignore_errors=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
