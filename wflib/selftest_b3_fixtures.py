"""Targeted checks for wflib.b3_fixtures (synthetic fixtures only).

Run: python3 -m wflib.selftest_b3_fixtures
The parent creates a temporary WF_LOCAL_DIR and runs the checks in a child, like
the lane self-tests. This suite is intentionally NOT registered in the aggregate
`wf selftest` (aggregation is owned by lane C); passing it is module-level
evidence about synthetic fixtures, never integration, Mac or production evidence.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

R_IDS = ["R01-review-expired", "R02-review-later-block", "R03-verifier-pin-changed"]


def _tmp(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix)).resolve()


def _walk_files(base: Path) -> dict:
    out = {}
    for dirpath, dirnames, filenames in os.walk(str(base)):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            p = Path(dirpath) / name
            out[p.relative_to(base).as_posix()] = p
    return out


def _sha(p: Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _git(cwd: Path, *args) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True).stdout.strip()


# ---------------------------------------------------------------- guards

def _t_guards(_ok, fx, tmp: Path):
    def refused(fn):
        try:
            fn()
        except fx.FixtureRefused:
            return True
        return False

    _ok("B3 夹具：相对路径根目录被拒绝", refused(lambda: fx.generate("relative/fixtures")))
    real = tmp / "real"
    real.mkdir()
    (tmp / "link").symlink_to(real, target_is_directory=True)
    _ok("B3 夹具：含符号链接（未完全解析）的根目录被拒绝，且未在目标中写入",
        refused(lambda: fx.generate(tmp / "link" / "x")) and not (real / "x").exists())
    busy = tmp / "busy"
    busy.mkdir()
    (busy / "keep.txt").write_text("existing\n")
    _ok("B3 夹具：非空根目录被拒绝，原有内容不变",
        refused(lambda: fx.generate(busy)) and sorted(p.name for p in busy.iterdir()) == ["keep.txt"])
    outside = Path(os.path.realpath(os.sep)) / ("b3-must-not-exist-" + os.urandom(6).hex())
    _ok("B3 夹具：系统临时目录之外的根目录被拒绝且不创建",
        refused(lambda: fx.generate(outside)) and not outside.exists())
    wb = tmp / "w" / ".workbuddy-ai" / "x"
    _ok("B3 夹具：路径含 .workbuddy-ai 时被拒绝且不创建",
        refused(lambda: fx.generate(wb)) and not (tmp / "w").exists())
    fake_tool = tmp / "fake-tool"
    fake_tool.mkdir()
    saved = fx._TOOL_ROOT
    fx._TOOL_ROOT = fake_tool
    try:
        inside_tool = refused(lambda: fx.generate(fake_tool / "fixtures"))
    finally:
        fx._TOOL_ROOT = saved
    _ok("B3 夹具：工具目录之内的根目录被拒绝", inside_tool and not (fake_tool / "fixtures").exists())
    _ok("B3 夹具：未知场景或未开启第二层时请求 R 场景被拒绝",
        refused(lambda: fx.generate(tmp / "u1", scenarios=["NOPE"]))
        and refused(lambda: fx.generate(tmp / "u2", scenarios=[R_IDS[0]])))


# ---------------------------------------------------------------- structure and determinism

def _t_structure(_ok, fx, a: Path, b: Path, index_a: dict, index_b: dict, tmp: Path):
    s_ids = [s["id"] for s in fx.SCENARIOS if s["tier"] == "S"]
    _ok("B3 夹具：第一层索引覆盖全部第一层场景", sorted(index_a["scenarios"]) == sorted(s_ids), sorted(index_a["scenarios"]))
    _ok("B3 夹具：索引声明 synthetic、非生产证据、无授权",
        index_a["synthetic"] is True and index_a["not_production_evidence"] is True and index_a["authorization"] == "none")
    same_norm = all(index_a["scenarios"][i]["normalized_sha256"] == index_b["scenarios"][i]["normalized_sha256"]
                    for i in s_ids)
    _ok("B3 夹具：不同根目录生成后，归一化摘要与 git 提交号逐场景一致", same_norm,
        [i for i in s_ids if index_a["scenarios"][i]["normalized_sha256"] != index_b["scenarios"][i]["normalized_sha256"]])
    problems = []
    for sid in s_ids:
        sd = a / sid
        doc = json.loads((sd / "FIXTURE.json").read_text(encoding="utf-8"))
        if not (doc["synthetic"] is True and doc["not_production_evidence"] is True and doc["authorization"] == "none"
                and doc["byte_reproducible"] is True and (sd / fx.MARKER).is_file()):
            problems.append(sid + ":markers")
        files = {k: v for k, v in _walk_files(sd).items() if k != "FIXTURE.json"}
        if set(files) != set(doc["files"]) or any(_sha(p) != doc["files"][k]["sha256"] for k, p in files.items()):
            problems.append(sid + ":manifest")
        wf = sd / "project" / "workflow" / "wf.json"
        if wf.exists() and not (json.loads(wf.read_text())["project"].startswith(fx.PREFIX)
                                and (sd / "project" / fx.MARKER).is_file()):
            problems.append(sid + ":project-marker")
        for repo, head in doc["git_heads"].items():
            if _git(sd / repo, "rev-parse", "HEAD") != head:
                problems.append(sid + ":git-head")
    _ok("B3 夹具：每个场景带合成标记，清单与磁盘逐文件一致（不多不少），git 提交号与记录一致", not problems, problems)
    # Same root twice: raw bytes identical, including FIXTURE.json and the index.
    first = {k: _sha(p) for k, p in _walk_files(a).items()}
    shutil.rmtree(a)
    fx.generate(a)
    second = {k: _sha(p) for k, p in _walk_files(a).items()}
    _ok("B3 夹具：同一根目录重复生成按字节完全一致", first == second,
        sorted(k for k in set(first) | set(second) if first.get(k) != second.get(k))[:10])
    declared = {s["validated_by"] for s in fx.SCENARIOS if s["validated_by"]}
    _ok("B3 夹具：声明了真实消费者校验的场景都有对应检查（不能只声明不验证）",
        declared <= set(CONSUMERS) | {"review_status"}, sorted(declared - set(CONSUMERS) - {"review_status"}))


def _t_source_has_no_secret_literal(_ok):
    from . import sync
    from .core import WF_HOME
    paths = [WF_HOME / "wflib" / "b3_fixtures.py", WF_HOME / "wflib" / "selftest_b3_fixtures.py",
             WF_HOME / "docs" / "B3_FIXTURES.md"]
    hits = [h for p in paths if p.exists() for h in sync._scan_text(p.name, p.read_text(encoding="utf-8"))]
    _ok("B3 夹具：生成器、本测试与文档源码不含密钥形态的字面值（合成值在运行时拼接）", not hits, hits)


def _t_isolation(_ok, tmp: Path):
    """Generate tier S in a child with an audit hook and check every write, subprocess and sensitive read."""
    from .core import WF_HOME
    root = tmp / "audit"
    local = os.path.realpath(os.environ.get("WF_LOCAL_DIR", str(tmp / "no-local")))
    forbidden = [local, os.path.realpath(str(WF_HOME / "local")),
                 os.path.realpath(os.path.expanduser("~/.workbuddy-ai"))]
    code = r'''
import json, os, sys
from pathlib import Path
FORBIDDEN = json.loads(sys.argv[2]); rec = []
def norm(p):
    return os.path.realpath(os.fsdecode(p)) if isinstance(p, (str, bytes)) else None
def hook(ev, args):
    if ev == "open":
        path, mode, flags = args
        q = norm(path)
        if q is None:
            return
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or \
            (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC))
        if writing:
            rec.append(["write", q])
        elif any(q == f or q.startswith(f + os.sep) for f in FORBIDDEN):
            rec.append(["forbidden-read", q])
    elif ev in ("os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.truncate", "os.rename", "os.replace",
                "os.symlink", "os.link", "shutil.copyfile", "shutil.move", "shutil.rmtree"):
        rec.append(["mutate:" + ev] + [norm(x) for x in args[:2] if isinstance(x, (str, bytes))])
    elif ev == "subprocess.Popen":
        rec.append(["popen", os.path.basename(str(args[0])), norm(args[2]) if args[2] else None])
sys.addaudithook(hook)
from wflib import b3_fixtures
b3_fixtures.generate(Path(sys.argv[1]))
print(json.dumps({"rec": rec, "mods": sorted(m for m in sys.modules if m.startswith("wflib"))}))
'''
    env = dict(os.environ, PYTHONPATH=str(WF_HOME), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-c", code, str(root), json.dumps(forbidden)], cwd=str(tmp), env=env,
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        _ok("B3 夹具：审计子进程运行", False, r.stderr[-1500:])
        return
    out = json.loads(r.stdout.strip().splitlines()[-1])
    inside = lambda q: q is not None and (q == str(root) or q.startswith(str(root) + os.sep))
    bad_writes = [x for x in out["rec"] if x[0] == "write" and not inside(x[1])]
    bad_mut = [x for x in out["rec"] if x[0].startswith("mutate:") and not all(inside(q) for q in x[1:])]
    bad_pop = [x for x in out["rec"] if x[0] == "popen" and not (x[1] == "git" and inside(x[2]))]
    reads = [x for x in out["rec"] if x[0] == "forbidden-read"]
    _ok("B3 夹具：第一层生成的全部写入与目录变更都在夹具根目录内", not bad_writes and not bad_mut,
        (bad_writes + bad_mut)[:5])
    _ok("B3 夹具：第一层只启动 git，且工作目录在夹具根目录内", not bad_pop, bad_pop[:5])
    _ok("B3 夹具：第一层不读取 WF_LOCAL_DIR、工具 local/ 或 ~/.workbuddy-ai", not reads, reads[:5])
    _ok("B3 夹具：第一层生成器不导入任何其他 wflib 模块", out["mods"] == ["wflib", "wflib.b3_fixtures"], out["mods"])


# ---------------------------------------------------------------- real consumers (tier S)

def _project(root: Path, sid: str) -> Path:
    return root / sid / "project"


def _c_s01(root, sid):
    from . import core, live
    inst = core.Instance(_project(root, sid))
    f = inst.events_path
    rows = live._strict_log(f.read_bytes(), f)
    cmds = [r.get("cmd", "") for r in rows if r["type"] == "exec"]
    ok = (inst.name.startswith("b3-synthetic-") and inst.state["state"] == "IDLE" and len(rows) == 4
          and not any("install_exec" in r for r in rows)
          and sum("wf self-update" in c for c in cmds) == 1 and any("wf.py\" self-update" in c for c in cmds)
          and any(" apply " in c for c in cmds))
    return ok, {"cmds": cmds}


def _c_state_fields(root, sid):
    from . import core
    st = core.Instance(_project(root, sid)).state
    if sid.startswith("S02a"):
        ok = st["state"] == "WAIT_REPLY" and "send_ref" not in st and "send_intent" not in st and "send_slot" in st
    elif sid.startswith("S02b"):
        ok = st["state"] == "WAIT_REPLY" and not ({"send_ref", "send_slot", "send_intent"} & set(st))
    else:
        si = st.get("send_intent") or {}
        ok = (st["state"] == "SEND" and si.get("status") == "accepted" and bool(si.get("ref"))
              and "send_ref" not in st and "send_slot" in st)
    return ok, sorted(st)


def _c_recovery(root, sid):
    from . import core, recovery
    ep = core.Instance(_project(root, sid)).state["recovery"]
    why = recovery._check(ep, datetime.fromisoformat("2026-01-15T08:00:00+00:00"))
    return (why == "") if sid.startswith("S04a") else (why != ""), why


def _c_dirty(root, sid):
    from . import core, release, sync
    p = _project(root, sid)
    dirty = sorted(release._dirty(p, core.DEFAULT_PROTECT))
    leak = sync._scan_text("notes/leak.txt", (p / "notes" / "leak.txt").read_text(encoding="utf-8"))
    clean = sync._scan_text("notes/new.txt", (p / "notes" / "new.txt").read_text(encoding="utf-8"))
    ok = (dirty == [" D docs/old.md", " M app.py", "?? notes/leak.txt", "?? notes/new.txt"]
          and len(leak) == 1 and not clean and (p / "ignored.log").exists() and (p / "data" / "keep.txt").exists())
    return ok, {"dirty": dirty, "leak_hits": len(leak)}


_TX_EXPECT = {"S07a": "未闭合", "S07b": None, "S07c": "不完整", "S07d": "身份损坏", "S07e": "hash 不符"}


def _c_tx(root, sid):
    from . import release
    from .core import WfError
    p = _project(root, sid)
    tx = next((p / "workflow" / "evidence" / "install").iterdir())
    envelope = release._read_record(tx / "prepared.json")["state"] == "prepared"
    try:
        release._InstallLock._check_incomplete(SimpleNamespace(root=p, recovery=False))
        got = None
    except WfError as e:
        got = str(e)
    want = _TX_EXPECT[sid[:4]]
    ok = envelope and ((got is None) if want is None else (got is not None and want in got))
    return ok, got


def _c_rollback(root, sid):
    from . import core, release
    from .core import WfError
    p = _project(root, sid)
    head = _git(p, "rev-parse", "HEAD")
    try:
        release.rollback(core.Instance(p), "v0.1", by="b3-fixture-test")
        got = "no error"
    except WfError as e:
        got = str(e)
    ok = "rollback_target_unverified" in got and _git(p, "rev-parse", "HEAD") == head and not _git(p, "status", "--porcelain")
    return ok, got


def _c_strict(root, sid):
    from . import live
    f = _project(root, sid) / "workflow" / "events.jsonl"
    try:
        live._strict_log(f.read_bytes(), f)
        return False, "accepted"
    except live._InputProblem as e:
        return e.state == "corrupt", e.state


CONSUMERS = {"s01_loads": _c_s01, "state_fields": _c_state_fields, "recovery_check": _c_recovery,
             "dirty_and_scan": _c_dirty, "tx_scan": _c_tx, "rollback": _c_rollback, "strict_log": _c_strict}


def _t_consumers(_ok, fx, root: Path):
    for s in fx.SCENARIOS:
        if s["tier"] != "S":
            continue
        if s["validated_by"] is None:
            print(f"ℹ B3 夹具缺口（不计入通过数）：{s['id']} 没有真实消费者校验：{s['intended']}", flush=True)
            continue
        try:
            ok, detail = CONSUMERS[s["validated_by"]](root, s["id"])
        except Exception as e:   # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}: {e}"
        _ok(f"B3 夹具真实消费者：{s['id']}（{s['validated_by']}）", ok, detail)
    from . import live
    f = _project(root, "S09b-events-duplicate-seq") / "workflow" / "events.jsonl"
    seqs = [r["seq"] for r in live._strict_log(f.read_bytes(), f)]
    print(f"ℹ B3 现状记录（不计入通过数）：live._strict_log 接受重复 seq {seqs}；目前没有消费者拒绝重复 seq", flush=True)


# ---------------------------------------------------------------- tier R

_STATUS = r'''
import json, sys
from pathlib import Path
res = json.loads(Path(sys.argv[1]).read_text())["tier_r_result"]
from wflib import review
review._tool_home = lambda: Path(res["tool"])
st = review.status(Path(res["project"]), res["request_id"], phase="install")
print(json.dumps({"allowed": st.get("allowed"), "reasons": st.get("reasons"), "verdict": st.get("verdict"),
                  "synthetic": (st.get("provenance") or {}).get("synthetic"),
                  "level": (st.get("provenance") or {}).get("level")}))
'''


def _t_tier_r(_ok, fx, tmp: Path):
    from .core import WF_HOME
    root = tmp / "tier-r"
    try:
        fx.generate(root, scenarios=R_IDS, tier_r=True)
    except Exception as e:   # noqa: BLE001
        _ok("B3 夹具第二层：经 B2 合成审查夹具生成审查库状态", False, f"{type(e).__name__}: {e}")
        return
    want = {"R01": lambda r: "expired" in r["reasons"],
            "R02": lambda r: "blocked" in r["reasons"] and r["verdict"] == "block",
            "R03": lambda r: any(x.startswith("verifier_") for x in r["reasons"])}
    for sid in R_IDS:
        sd = root / sid
        doc = json.loads((sd / "FIXTURE.json").read_text(encoding="utf-8"))
        env = dict(os.environ, PYTHONPATH=str(WF_HOME), PYTHONDONTWRITEBYTECODE="1",
                   WF_LOCAL_DIR=str(sd / "host" / "local"), HOME=str(sd / "home"), TMPDIR=str(sd / "tmp"))
        r = subprocess.run([sys.executable, "-c", _STATUS, str(sd / "FIXTURE.json")], cwd=str(sd), env=env,
                           capture_output=True, text=True, timeout=120)
        try:
            got = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            got = {"error": r.stderr[-800:]}
        res = doc["tier_r_result"]
        # When the verifier can no longer be validated, status falls back to provenance level
        # "unavailable" whose synthetic field is a default False: not evidence of non-synthetic.
        source_ok = got.get("synthetic") is True if sid != "R03-verifier-pin-changed" else \
            got.get("level") == "unavailable"
        ok = (r.returncode == 0 and res.get("synthetic") is True and res.get("allowed_at_review") is True
              and got.get("allowed") is False and source_ok and want[sid[:3]](got)
              and doc["synthetic"] is True and doc["byte_reproducible"] is False)
        _ok(f"B3 夹具第二层真实消费者：{sid}（审查时 allowed 且标 synthetic，现在 review.status 拒绝）", ok,
            {"status": got, "at_review": res.get("allowed_at_review")})
        if sid == "R03-verifier-pin-changed" and got.get("level") == "unavailable":
            print(f"ℹ B3 现状记录（不计入通过数）：{sid} 的 review.status 来源退回 unavailable，"
                  f"synthetic 显示为 {got.get('synthetic')}（默认值，不代表非合成；交 G1 信任工作参考）", flush=True)
    # The child's audit guard must refuse a write outside the fixture root.
    outside = tmp / "outside-guard.txt"
    guard = fx._TIER_R_CHILD.split("from datetime import timedelta")[0] + f"\nopen({str(outside)!r}, 'w')\n"
    sd = root / R_IDS[0]
    r = subprocess.run([sys.executable, "-c", guard, str(root), str(sd), "expired"], cwd=str(sd),
                       capture_output=True, text=True, timeout=60)
    _ok("B3 夹具第二层：子进程审计钩子拒绝写入夹具根目录之外",
        r.returncode != 0 and "outside fixture root" in r.stderr and not outside.exists(), r.stderr[-300:])


# ---------------------------------------------------------------- entry

def run_b3_fixtures(_ok) -> None:
    from . import b3_fixtures as fx
    tmp = _tmp("b3-fixture-test-")
    try:
        groups = [("guards", lambda: _t_guards(_ok, fx, tmp / "guards"))]
        (tmp / "guards").mkdir()
        index_a = fx.generate(tmp / "a")
        index_b = fx.generate(tmp / "b")
        groups += [("structure", lambda: _t_structure(_ok, fx, tmp / "a", tmp / "b", index_a, index_b, tmp)),
                   ("source", lambda: _t_source_has_no_secret_literal(_ok)),
                   ("isolation", lambda: _t_isolation(_ok, tmp)),
                   ("consumers", lambda: _t_consumers(_ok, fx, tmp / "b")),
                   ("tier-r", lambda: _t_tier_r(_ok, fx, tmp))]
        for name, fn in groups:
            try:
                fn()
            except Exception as e:   # noqa: BLE001
                _ok(f"B3 夹具检查组「{name}」运行出错", False, f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _child_main() -> int:
    res = []

    def _ok(n, cond, detail=""):
        res.append(bool(cond))
        print(f"{'✓' if cond else '✗'} {n}" + ("" if cond else f"\n    {str(detail)[:1500]}"), flush=True)
        return cond
    run_b3_fixtures(_ok)
    print(f"B3 夹具自检：{sum(res)} / {len(res)} 通过", flush=True)
    return 0 if res and all(res) else 1


def main() -> int:
    from .selftest import CHILD_FLAG, child_env
    if os.environ.get(CHILD_FLAG) == "1":
        return _child_main()
    from .core import WF_HOME
    tmp = _tmp("wf-b3-fixtures-")
    try:
        r = subprocess.run([sys.executable, "-m", "wflib.selftest_b3_fixtures"], cwd=str(WF_HOME),
                           env=child_env(os.environ, tmp / "local"), timeout=600)
        return r.returncode
    except subprocess.TimeoutExpired:
        print("B3 夹具自检子进程超过 600 秒没有结束，按未通过处理", flush=True)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
