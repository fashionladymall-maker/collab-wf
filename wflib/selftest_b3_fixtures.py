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
    # The temp base itself must be a sandbox (counterexample CX2: TMPDIR=/ allowed any absolute path).
    saved_env = {k: os.environ.get(k) for k in ("TMPDIR", "TEMP", "TMP", "HOME")}

    def with_env(env, fn):
        for k in ("TMPDIR", "TEMP", "TMP"):
            os.environ.pop(k, None)
        os.environ.update(env)
        try:
            return fn()
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    slash_target = Path(os.path.realpath(os.sep)) / ("b3-cx2-" + os.urandom(6).hex())
    _ok("B3 夹具：TMPDIR=/ 时拒绝（反例 CX2）且不创建",
        with_env({"TMPDIR": os.sep}, lambda: refused(lambda: fx.generate(slash_target))) and not slash_target.exists())
    fake_home = tmp / "fake-home"
    fake_home.mkdir(mode=0o700)
    _ok("B3 夹具：TMPDIR 指向 HOME 时拒绝且不创建",
        with_env({"TMPDIR": str(fake_home), "HOME": str(fake_home)},
                 lambda: refused(lambda: fx.generate(fake_home / "fixtures"))) and not (fake_home / "fixtures").exists())
    private_tmp = tmp / "private-tmp"
    private_tmp.mkdir(mode=0o700)

    def private_ok():
        fx.generate(private_tmp / "fixtures", scenarios=["S04a-quota-blocked-valid-episode"])
        return (private_tmp / "fixtures" / "S04a-quota-blocked-valid-episode" / "FIXTURE.json").is_file()
    _ok("B3 夹具：本人所有的私有临时目录（0700）作为 TMPDIR 时正常生成", with_env({"TMPDIR": str(private_tmp)}, private_ok))
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
    _ok("B3 夹具：verify() 对刚生成的全部场景无差异，清单记录目录及权限（S08 空安装库为 0700）",
        fx.verify(a) == {} and json.loads((a / "S08-rollback-target-missing" / "FIXTURE.json").read_text())["dirs"]
        .get("project/workflow/evidence/install") == "0700", fx.verify(a))
    v = tmp / "verify-neg"
    fx.generate(v, scenarios=["S05-dirty-variants", "S08-rollback-target-missing"])
    os.rmdir(v / "S08-rollback-target-missing" / "project" / "workflow" / "evidence" / "install")
    os.chmod(v / "S05-dirty-variants" / "project" / "notes", 0o700)
    (v / "S05-dirty-variants" / "project" / "app.py").write_text("changed\n")
    got = fx.verify(v)
    _ok("B3 夹具：verify() 发现被删的空 0700 安装库（反例 CX3）、目录权限变化与文件改动",
        "dirs:project/workflow/evidence/install" in got.get("S08-rollback-target-missing", [])
        and "dirs:project/notes" in got.get("S05-dirty-variants", [])
        and "files:project/app.py" in got.get("S05-dirty-variants", []), got)
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
    print(f"ℹ B3 现状记录（不计入通过数）：语法层 live._strict_log 接受重复 seq {seqs}；完整消费者 "
          f"live._capture_inputs/_revalidate_inputs 把重复或倒序判为 corrupt，见 SEQ scaffold 检查", flush=True)


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
    # The child's audit guard must refuse a write outside the fixture root, for str and PathLike.
    refused_all, detail = True, []
    for kind, stmt in (("str", "open(OUT, 'w')"), ("pathlike", "from pathlib import Path; Path(OUT).write_text('x')")):
        outside = tmp / f"outside-guard-{kind}.txt"
        guard = fx._TIER_R_CHILD.split("from datetime import timedelta")[0] + f"\nOUT = {str(outside)!r}\n{stmt}\n"
        sd = root / R_IDS[0]
        r = subprocess.run([sys.executable, "-c", guard, str(root), str(sd), "expired"], cwd=str(sd),
                           capture_output=True, text=True, timeout=60)
        refused_all &= r.returncode != 0 and "outside fixture root" in r.stderr and not outside.exists()
        detail.append(r.stderr[-160:])
    _ok("B3 夹具第二层：子进程审计钩子拒绝本进程写入夹具根目录之外（str 与 PathLike）", refused_all, detail)
    # Counterexample GUARD-R3: launched processes inherit no audit hook, so anything outside the
    # whitelist (git and the two stand-in scripts) must not be launched at all.
    prefix = fx._TIER_R_CHILD.split("from datetime import timedelta")[0]
    out_g = tmp / "grandchild-outside.txt"
    grand = "open(" + json.dumps(str(out_g)) + ", 'w').write('x')"
    cases = [("python-grandchild", "import subprocess\nsubprocess.run([sys.executable, '-c', " + json.dumps(grand)
              + "], cwd=str(SD))", False),
             ("os.system", "os.system('true')", False),
             ("shell=True", "import subprocess\nsubprocess.run('true', shell=True, cwd=str(SD))", False),
             ("git (whitelisted)", "import subprocess\nsubprocess.run(['git', '--version'], cwd=str(SD), check=True, "
              "capture_output=True)", True)]
    launch = {}
    sd = root / R_IDS[0]
    for name, stmt, allowed in cases:
        r = subprocess.run([sys.executable, "-c", prefix + "\n" + stmt + "\n", str(root), str(sd), "expired"],
                           cwd=str(sd), capture_output=True, text=True, timeout=60)
        launch[name] = (r.returncode == 0) if allowed else (r.returncode != 0 and "b3 fixture child" in r.stderr)
    _ok("B3 夹具第二层：白名单外的进程启动被拒绝（普通 Python 孙进程、os.system、shell=True），孙进程未能写出；"
        "白名单内 git 正常（反例 GUARD-R3）", all(launch.values()) and not out_g.exists(), launch)


# ---------------------------------------------------------------- SEQ: event sequence through the real consumer
# The S09 layout fixtures lack transport, adapter log, sync history and turns, so they cannot drive
# live._capture_inputs. This minimal isolated scaffold satisfies those prerequisites, writes events
# through the real core.event, and injects a crash only by SIGKILL between the real save() and the
# real events append. Duplicate/decreasing logs are byte edits of real core.event output.

_SEQ_CHILD = r'''
import inspect, json, os, signal, subprocess, sys
from pathlib import Path
CASE, BASE, PHASE = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
from wflib import core, live
ROOT = BASE / "project"
NAME = "b3-synthetic-seq-" + CASE.replace("_", "-")
MODERN_CHECK = "_capture_inputs(" in inspect.getsource(live.check)

def git(*a):
    env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(BASE / "no-home"), "LC_ALL": "C",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "b3-synthetic",
           "GIT_AUTHOR_EMAIL": "b3-synthetic@fixture.invalid", "GIT_COMMITTER_NAME": "b3-synthetic",
           "GIT_COMMITTER_EMAIL": "b3-synthetic@fixture.invalid"}
    subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", *a], cwd=str(ROOT), env=env,
                   check=True, capture_output=True)

def build(n, with_exec=False):
    (ROOT / "workflow").mkdir(parents=True)
    (ROOT / "workflow" / "wf.json").write_text(json.dumps({"project": NAME, "created": core.now_iso(), "protect": []}))
    (ROOT / "PROJECT.md").write_text("# PROJECT.md\nsynthetic SEQ scaffold (B3)\n")
    (ROOT / ".gitignore").write_text("workflow/state.json\nworkflow/*.jsonl\nworkflow/evidence/\n")
    git("init", "-q"); git("add", "-A"); git("commit", "-q", "-m", "synthetic seq scaffold")
    (ROOT / "workflow" / "turns.jsonl").write_bytes(b"")
    core.LOCAL.mkdir(parents=True, exist_ok=True)
    adapter = core.LOCAL / "adapter.jsonl"
    adapter.write_bytes(b"")
    (core.LOCAL / "transport.json").write_text(json.dumps({"log": str(adapter)}))
    (core.LOCAL / "sync").mkdir(exist_ok=True)
    (core.LOCAL / "sync" / (NAME + ".history.jsonl")).write_bytes(b"")
    inst = core.Instance(ROOT)
    for i in range(n):
        if with_exec and i == 1:
            inst.event("exec", cid="c01", cmd="true", exit_code=0, duration_s=0.0, by="driver",
                       install_exec={"schema": "install-exec/1", "subject": {"project": NAME, "root": str(ROOT)},
                                     "turn": 0, "exec": {"id": "c01", "nonce": "0" * 32}}, install_refs=[],
                       install_receipt_error=None)
        else:
            inst.event("b3_synthetic", by="test", note="seq scaffold")
    return core.Instance(ROOT)

def lines():
    return inst_path().read_bytes().split(b"\n")[:-1]

def inst_path():
    return ROOT / "workflow" / "events.jsonl"

def evaluate(inst, baseline):
    out = {"seqs": [json.loads(l)["seq"] for l in lines()],
           "state_seq": json.loads((ROOT / "workflow" / "state.json").read_text())["seq"],
           "modern_check_route": MODERN_CHECK}
    try:
        out["strict_log_accepts"] = len(live._strict_log(inst_path().read_bytes(), inst_path())) >= 0
    except live._InputProblem as e:
        out["strict_log_accepts"] = False
    try:
        snap = live._capture_inputs(inst, rounds=1)
        ev = live._evaluate_snapshot(snap)
        out["capture"] = {"state": "collected"}
        out["replay"] = {"state": ev["state"], "authorization": ev["authorization"],
                         "history_authorization": ev.get("history_authorization")}
        r = live._revalidate_inputs(inst, snap.descriptor)
        out["revalidate_self"] = {"valid": r["valid"], "state": r["state"]}
    except live._InputProblem as e:
        out["capture"] = {"state": e.state, "source": str(e.source), "detail": e.detail}
    if baseline is not None:
        r = live._revalidate_inputs(inst, baseline)
        out["revalidate_baseline"] = {k: r.get(k) for k in ("valid", "state", "detail")}
    if CASE.startswith("check_"):
        if MODERN_CHECK:
            rep = live.check(inst, rounds=1)
            out["check"] = {"input_problem": rep.get("input_problem"), "passed": rep.get("passed")}
        else:
            out["check"] = "not routed through _capture_inputs on this tree"
    return out

if PHASE == "kill":                       # build, freeze a baseline, then die between real save and append
    inst = build(7)
    (BASE / "baseline.json").write_text(json.dumps(live._capture_inputs(inst, rounds=1).descriptor))
    events = str(inst.events_path)
    real = core.append_jsonl
    core.append_jsonl = lambda path, row: os.kill(os.getpid(), signal.SIGKILL) if str(path) == events else real(path, row)
    inst.event("b3_synthetic", by="test", note="killed after save, before append")
    raise SystemExit("not killed")
baseline = None
if CASE in ("continuous", "duplicate", "decreasing", "normal_continue"):
    inst = build(7)
    baseline = live._capture_inputs(inst, rounds=1).descriptor
    raw = lines()
    if CASE == "duplicate":
        inst_path().write_bytes(b"\n".join(raw + [raw[-1]]) + b"\n")          # seq 1..7,7
    elif CASE == "decreasing":
        raw[3], raw[4] = raw[4], raw[3]                                          # seq 1,2,3,5,4,6,7
        inst_path().write_bytes(b"\n".join(raw) + b"\n")
    elif CASE == "normal_continue":
        core.Instance(ROOT).event("b3_synthetic", by="test", note="normal continuation")
    if CASE == "continuous":
        baseline = None
elif CASE in ("state_ahead", "crash_continue"):
    baseline = json.loads((BASE / "baseline.json").read_text())
    if CASE == "crash_continue":
        core.Instance(ROOT).event("b3_synthetic", by="test", note="ordinary continuation after the crash")
elif CASE in ("check_continuous_exec", "check_duplicate_exec"):
    inst = build(7, with_exec=True)
    if CASE == "check_duplicate_exec":
        raw = lines()
        inst_path().write_bytes(b"\n".join(raw + [raw[-1]]) + b"\n")
print(json.dumps(evaluate(core.Instance(ROOT), baseline)))
'''

_SEQ_CORRUPT = "duplicate/out-of-order event sequence"
_SEQ_UNKNOWN = "event sequence gap/order/state mismatch"


def _t_seq_consumers(_ok, tmp: Path):
    import signal
    from .core import WF_HOME
    res = {}
    for case in ("continuous", "duplicate", "decreasing", "state_ahead", "crash_continue", "normal_continue",
                 "check_continuous_exec", "check_duplicate_exec"):
        base = tmp / case
        base.mkdir()
        env = dict(os.environ, PYTHONPATH=str(WF_HOME), PYTHONDONTWRITEBYTECODE="1", WF_NOTIFY="0",
                   WF_LOCAL_DIR=str(base / "local"))
        row = {}
        if case in ("state_ahead", "crash_continue"):
            k = subprocess.run([sys.executable, "-c", _SEQ_CHILD, case, str(base), "kill"], env=env, cwd=str(base),
                               capture_output=True, text=True, timeout=120)
            row["kill_rc"] = k.returncode
        r = subprocess.run([sys.executable, "-c", _SEQ_CHILD, case, str(base), "run"], env=env, cwd=str(base),
                           capture_output=True, text=True, timeout=120)
        try:
            row.update(json.loads(r.stdout.strip().splitlines()[-1]))
        except (ValueError, IndexError):
            row["error"] = r.stderr[-800:]
        res[case] = row
        cap = row.get("capture") or {}
        print(f"ℹ SEQ {case}: seqs={row.get('seqs')} state.seq={row.get('state_seq')} kill_rc={row.get('kill_rc')} | "
              f"_capture_inputs={cap.get('state')} {cap.get('detail') or ''} | replay={row.get('replay')} | "
              f"revalidate(self)={row.get('revalidate_self')} | revalidate(baseline)={row.get('revalidate_baseline')} | "
              f"_strict_log accepts={row.get('strict_log_accepts')}", flush=True)

    def corrupt(row):
        cap = row.get("capture") or {}
        return (cap.get("state") == "corrupt" and cap.get("detail") == _SEQ_CORRUPT
                and cap.get("source", "").endswith("workflow/events.jsonl") and "replay" not in row
                and (row.get("revalidate_baseline") or {}).get("valid") is False
                and row["revalidate_baseline"].get("state") == "corrupt")

    def unknown(row):
        cap = row.get("capture") or {}
        return (cap.get("state") == "unknown" and cap.get("detail") == _SEQ_UNKNOWN
                and cap.get("source", "").endswith("workflow/events.jsonl") and "replay" not in row
                and (row.get("revalidate_baseline") or {}).get("valid") is False
                and row["revalidate_baseline"].get("state") == "unknown")

    def collected(row):
        return ((row.get("capture") or {}).get("state") == "collected" and (row.get("replay") or {}).get("state") == "collected"
                and row["replay"].get("authorization") == "unavailable"
                and (row.get("revalidate_self") or {}).get("valid") is True)
    c = res["continuous"]
    _ok("SEQ 连续：真实 core.event 写出 1..7、state.seq=7，完整 _capture_inputs→回放→_revalidate_inputs 走通（collected/stable），"
        "授权仍为 unavailable（证明前置条件齐备，负例不是假覆盖）",
        collected(c) and c.get("seqs") == list(range(1, 8)) and c.get("state_seq") == 7, c)
    d = res["duplicate"]
    _ok("SEQ 重复：同一 scaffold 上重复一条真实事件（1..7,7），_capture_inputs 与对基线的 _revalidate_inputs 都判 corrupt",
        corrupt(d) and d.get("seqs") == list(range(1, 8)) + [7] and d.get("strict_log_accepts") is True, d)
    o = res["decreasing"]
    _ok("SEQ 倒序：交换两条真实事件（…3,5,4,6…），_capture_inputs 与 _revalidate_inputs 都判 corrupt",
        corrupt(o) and o.get("seqs") == [1, 2, 3, 5, 4, 6, 7], o)
    a = res["state_ahead"]
    _ok("SEQ state 领先：真实 SIGKILL 落在 save 与 append 之间（rc=-9），state.seq=8 而事件 1..7，判 unknown，授权未获得",
        a.get("kill_rc") == -signal.SIGKILL and a.get("seqs") == list(range(1, 8)) and a.get("state_seq") == 8 and unknown(a), a)
    k = res["crash_continue"]
    _ok("SEQ 事故续写：SIGKILL 后由真实 core.event 正常续写，事件 1..7,9、state.seq=9（内部缺号，state=max），仍判 unknown",
        k.get("kill_rc") == -signal.SIGKILL and k.get("seqs") == list(range(1, 8)) + [9] and k.get("state_seq") == 9 and unknown(k), k)
    n = res["normal_continue"]
    _ok("SEQ 正常续写：真实 core.event 续写到 1..8、state.seq=8，完整采集 collected；对旧基线 changed，对自身 stable；授权 unavailable",
        collected(n) and n.get("seqs") == list(range(1, 9)) and n.get("state_seq") == 8
        and (n.get("revalidate_baseline") or {}).get("state") == "changed", n)
    ce, cd = res["check_continuous_exec"], res["check_duplicate_exec"]
    if cd.get("modern_check_route") is True:
        pc, pd = (ce.get("check") or {}).get("input_problem") or {}, (cd.get("check") or {}).get("input_problem") or {}
        _ok("SEQ check()：本 tree 的 check 在含 install_exec 的 exec 事件时经由 _capture_inputs；重复 seq 得到 input_problem=corrupt，"
            "同一 scaffold 的连续日志得到的不是 seq 原因",
            pd.get("state") == "corrupt" and pd.get("detail") == _SEQ_CORRUPT and pc.get("detail") not in (_SEQ_CORRUPT, _SEQ_UNKNOWN)
            and (cd.get("check") or {}).get("passed") is False, {"duplicate": pd, "continuous": pc})
    else:
        print("ℹ SEQ check()（不计入通过数）：本 tree 的 live.check 不经由 _capture_inputs，直接读日志；"
              "这里不把 check 计为重复 seq 的拒绝者", flush=True)


# ---------------------------------------------------------------- GUARD rework (2026-10-16)

def _snapshot(d: Path) -> list:
    return sorted((p.relative_to(d).as_posix(), _sha(p) if p.is_file() else "dir") for p in d.rglob("*"))


def _t_local_guard(_ok, fx, tmp: Path):
    """Counterexample GUARD-R1: the configured LOCAL (and aliases/ancestors) must never be a root."""
    def refused(fn):
        try:
            fn()
        except fx.FixtureRefused:
            return True
        return False
    saved = os.environ.get("WF_LOCAL_DIR")
    try:
        local = tmp / "cfg-local"
        local.mkdir()
        os.environ["WF_LOCAL_DIR"] = str(local)
        itself = refused(lambda: fx.generate(local)) and list(local.iterdir()) == []
        (local / "sentinel.json").write_bytes(b'{"keep": true}\n')
        snap = _snapshot(local)
        sub = refused(lambda: fx.generate(local / "sub")) and _snapshot(local) == snap
        _ok("B3 夹具：WF_LOCAL_DIR 本体（空）与其子目录在写入前被拒绝，LOCAL 成员与字节不变（反例 GUARD-R1）",
            itself and sub, {"itself": itself, "sub": sub, "after": _snapshot(local)})
        real = tmp / "real-local"
        real.mkdir()
        (tmp / "alias-local").symlink_to(real, target_is_directory=True)
        os.environ["WF_LOCAL_DIR"] = str(tmp / "alias-local")
        alias = refused(lambda: fx.generate(real)) and refused(lambda: fx.generate(real / "x")) and list(real.iterdir()) == []
        os.environ["WF_LOCAL_DIR"] = str(tmp / "anc" / "deep" / "local")
        ancestor = refused(lambda: fx.generate(tmp / "anc")) and not (tmp / "anc").exists()
        _ok("B3 夹具：经符号链接别名配置的 LOCAL 与 LOCAL 的祖先目录同样在写入前被拒绝", alias and ancestor,
            {"alias": alias, "ancestor": ancestor})
        os.environ["WF_LOCAL_DIR"] = str(local)
        fx.generate(tmp / "unrelated", scenarios=["S04a-quota-blocked-valid-episode"])
        _ok("B3 夹具：配置了 WF_LOCAL_DIR 时，与之无关的临时根目录照常生成",
            (tmp / "unrelated" / "S04a-quota-blocked-valid-episode" / "FIXTURE.json").is_file()
            and _snapshot(local) == snap)
    finally:
        if saved is None:
            os.environ.pop("WF_LOCAL_DIR", None)
        else:
            os.environ["WF_LOCAL_DIR"] = saved


def _t_writer_paths(_ok, fx, tmp: Path):
    """Counterexample GUARD-R2: every write primitive shares one path rule, not only write()."""
    root = tmp / "root"
    root.mkdir()
    w = fx._Writer(root)
    before = _snapshot(tmp)
    bad = ["../outside", "a/../../outside", "/b3-abs-outside", "", ".", "a/\x00b"]
    calls = {"path": lambda r: w.path(r), "mkdir": lambda r: w.mkdir(r),
             "write": lambda r: w.write(r, "x"), "remove": lambda r: w.remove(r)}
    leaks = []
    for name, fn in calls.items():
        for rel in bad:
            try:
                fn(rel)
                leaks.append([name, rel, "accepted"])
            except fx.FixtureRefused:
                pass
            except Exception as e:   # noqa: BLE001
                leaks.append([name, rel, type(e).__name__])
    _ok("B3 夹具：path/mkdir/write/remove 统一拒绝 ..、绝对路径、空路径、. 与 NUL，根目录内外都没有新增（反例 GUARD-R2）",
        not leaks and _snapshot(tmp) == before and not Path("/b3-abs-outside").exists(), leaks)
    w.mkdir("ok/deep")
    w.write("ok/deep/f.txt", "y")
    w.remove("ok/deep/f.txt")
    _ok("B3 夹具：合法的嵌套相对路径照常可建、可写、可删", (root / "ok" / "deep").is_dir()
        and not (root / "ok" / "deep" / "f.txt").exists())


# ---------------------------------------------------------------- tier R dependency contract (FX-R)

def _copy_tool(src: Path, dst: Path):
    shutil.copytree(str(src / "wflib"), str(dst / "wflib"), ignore=shutil.ignore_patterns("__pycache__"))


def _edit(path: Path, old: str, new: str):
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise AssertionError(f"{path.name}: expected exactly one {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def _t_tier_r_contract(_ok, fx, tmp: Path):
    from .core import WF_HOME
    _ok("B3 夹具第二层依赖契约：当前 Env 与 review 接口与 14 项固定指纹一致",
        fx._tier_r_contract_problems() == [] and len(fx.TIER_R_CONTRACT["components"]) == 14,
        fx._tier_r_contract_problems())
    drift = tmp / "drift-tool"
    _copy_tool(WF_HOME, drift)
    _edit(drift / "wflib" / "selftest_review.py", 'self.bin = base / "bin"', 'self.bin = base / "bin-drift"')
    _edit(drift / "wflib" / "review.py",
          'def status(target, request_id=None, *, content_id=None, phase="review", now=None) -> dict:',
          'def status(target, request_id=None, *, content_id=None, phase="review", now=None, strict=False) -> dict:')
    _edit(drift / "wflib" / "review.py", "\nREUSE_DAYS = 7\n", "\n")
    problems = fx._tier_r_contract_problems(drift)
    _ok("B3 夹具第二层依赖契约：Env 改动、review.status 签名改动、REUSE_DAYS 缺失都被逐项识别",
        {"changed wflib/selftest_review.py:class:Env", "changed wflib/review.py:signature:status",
         "missing wflib/review.py:assign:REUSE_DAYS"} <= set(problems), problems)
    target = tmp / "drift-fixtures"
    saved = fx._TOOL_ROOT
    fx._TOOL_ROOT = drift
    try:
        try:
            fx.generate(target, scenarios=[R_IDS[0]], tier_r=True)
            msg = "generated"
        except fx.FixtureRefused as e:
            msg = str(e)
    finally:
        fx._TOOL_ROOT = saved
    _ok("B3 夹具第二层依赖契约：依赖漂移时生成明确失败，且在写入任何夹具之前失败",
        "tier R dependency drift" in msg and "class:Env" in msg and not target.exists(), msg[:400])
    # Behavioural layer: pins refreshed without re-review must still fail when the stand-in stops
    # declaring itself synthetic (the CT4 shape).
    sem = tmp / "semantic-tool"
    _copy_tool(WF_HOME, sem)
    _edit(sem / "wflib" / "selftest_review.py", '"path": str(self.verifier), "synthetic": True,', '"path": str(self.verifier),')
    _edit(sem / "wflib" / "selftest_review.py", '"ok": b.get("ok", True), "synthetic": True,',
          '"ok": b.get("ok", True), "synthetic": False,')
    target2 = tmp / "semantic-fixtures"
    saved_root, saved_pins = fx._TOOL_ROOT, fx.TIER_R_CONTRACT
    fx._TOOL_ROOT, fx.TIER_R_CONTRACT = sem, fx.tier_r_contract(sem)
    try:
        try:
            fx.generate(target2, scenarios=[R_IDS[0]], tier_r=True)
            msg2 = "generated"
        except fx.FixtureRefused as e:
            msg2 = str(e)
    finally:
        fx._TOOL_ROOT, fx.TIER_R_CONTRACT = saved_root, saved_pins
    _ok("B3 夹具第二层依赖契约：只刷新指纹而未复审时，行为契约仍拦下不再自报 synthetic 的替身",
        "behavioural contract" in msg2 and "synthetic" in msg2, msg2[:400])


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
                   ("tier-r", lambda: _t_tier_r(_ok, fx, tmp)),
                   ("tier-r-contract", lambda: _t_tier_r_contract(_ok, fx, tmp)),
                   ("local-guard", lambda: (tmp / "local-guard").mkdir() or _t_local_guard(_ok, fx, tmp / "local-guard")),
                   ("writer-paths", lambda: (tmp / "writer").mkdir() or _t_writer_paths(_ok, fx, tmp / "writer")),
                   ("seq-consumers", lambda: (tmp / "seq").mkdir() or _t_seq_consumers(_ok, tmp / "seq"))]
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
