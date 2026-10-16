"""端到端自检：在临时目录里用真实命令走完整条流程。不读写真实注册表与项目。
wf self-update 在安装新版本之前会先运行新版本的自检。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from .core import WF_HOME

RESULTS = []



def _user(_wf, env, proj, kind, *cmd):
    """像执行端一样：先用 wf ask 取当前待决事项的决定码，再带着码与“用户原话”执行。"""
    rc, o = _wf(env, proj, "--json", "ask")
    try:
        items = json.loads(o[o.index("{"):])["items"]
    except (ValueError, KeyError):
        items = []
    code = next((it["code"] for it in items if it["kind"] == kind or it["kind"].startswith(kind + ":")), "NONE")
    return _wf(env, proj, *cmd, "--code", code, "--said", "测试：用户原话")

def _ok(name, cond, detail=""):
    RESULTS.append((bool(cond), name, detail))
    print(f"  {'✓' if cond else '✗'} {name}" + (f"  —— {detail}" if detail and not cond else ""), flush=True)
    return cond


def _wf(env, cwd, *args, wf=None):
    r = subprocess.run([sys.executable, str(wf or WF_HOME / "wf.py")] + list(args), cwd=str(cwd), env=env,
                       capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout + r.stderr


def _state(proj):
    return json.loads((proj / "workflow" / "state.json").read_text(encoding="utf-8"))


def _project_md(version, phase="测试"):
    return (f"# PROJECT.md —— demo\n\n## 当前版本\n{version}（2026-01-01）—— 阶段：{phase}\n\n## 目标\n1. 测试\n\n"
            "## 已确认结论\n1. 无\n\n## 已排除方案及原因\n- 无\n\n## 下一步\n- [ ] 测试\n\n## 待用户决策\n- 无\n\n"
            "## 已知限制\n- 无\n\n## 失败模式台账\n| 编号 | 日期 | 现象 | 根因 | 处置 | 状态 |\n|---|---|---|---|---|---|\n\n"
            "## 协作约定\n- 无\n")


def _make_zip(path: Path, top: str, files: dict):
    with zipfile.ZipFile(path, "w") as z:
        for rel, content in files.items():
            info = zipfile.ZipInfo(f"{top}/{rel}")
            info.external_attr = (0o755 if rel.endswith(".sh") else 0o644) << 16
            z.writestr(info, content)


def _synthetic_review_host(env, root):
    """Selftest-only host programs; never borrow the user's runner or verifier."""
    from .selftest_review import AUTHOR_SESSION, REVIEWER, RUNNER, VERIFIER
    host = Path(root) / "review-host"
    host.mkdir()
    runner, verifier = host / "runner.py", host / "verifier.py"
    runner.write_text(RUNNER.format(py=sys.executable), encoding="utf-8")
    verifier.write_text(VERIFIER.format(py=sys.executable, identity=json.dumps(REVIEWER)), encoding="utf-8")
    for path in (runner, verifier):
        path.chmod(0o755)
    (host / "behavior.json").write_text("{}")
    (host / "vbehavior.json").write_text("{}")
    local = Path(env["WF_LOCAL_DIR"])
    local.mkdir(parents=True, exist_ok=True)
    (local / "transport.json").write_text(json.dumps({
        "review": str(runner),
        "review_verifier": {"path": str(verifier), "synthetic": True,
                            "sha256": hashlib.sha256(verifier.read_bytes()).hexdigest()},
    }), encoding="utf-8")
    (local / "lanes.json").write_text(json.dumps({"lanes": {"selftest": {"sessions": [AUTHOR_SESSION]}}}),
                                    encoding="utf-8")


def _reviewed_request(env, cwd, package, *, kind="project", wf=None):
    """Freeze after the last target write; install consumes this exact request."""
    rc, prepared = _wf(env, cwd, "review", "prepare", str(package), "--kind", kind,
                       "--author", "selftest", "--json", wf=wf)
    if rc:
        raise RuntimeError(f"synthetic prepare failed: {prepared}")
    rid = json.loads(prepared)["request_id"]
    rc, output = _wf(env, cwd, "review", "run", rid, "--json", wf=wf)
    decision = json.loads(output) if rc == 0 else {}
    if (rc or not decision.get("allowed") or not (decision.get("provenance") or {}).get("synthetic")):
        raise RuntimeError(f"synthetic review refused: {output}")
    return rid


REPLY = """【本轮结论】
测试回复。

【本机执行】
```bash
echo hello
exit 3
rm -rf build
```

【本轮回报清单】
1. echo 的输出
2. 第二条命令的退出码

【需要用户决定】
1. 是否继续

【下一轮建议档位】medium。
"""


# 1.9.4（W95）：自检的外部副作用隔离改在进程边界上做。1.9.3 在 run_v15、run_v18 之后才设 WF_NOTIFY=0，
# 而 run_v18 里的检查要求它已经是 0：构建器预先设了 0，掩盖了这个顺序问题，真实安装时（没有预设）自检 278/279。
# 现在 wf selftest 先在一个子进程里跑全部检查：子进程从第一条检查起就是 WF_NOTIFY=0、WF_LOCAL_DIR 指向临时目录；
# 父进程自己的环境一项都不改，所以不需要恢复，也不会漏恢复。
CHILD_FLAG = "WF_SELFTEST_CHILD"
ISOLATED = {"WF_NOTIFY": "0"}   # 有外部副作用的开关：自检里一律关闭（WF_NOTIFY：macOS 通知）


def child_env(base: dict, local_dir: str) -> dict:
    """由父进程的环境生成自检子进程的环境；不改动 base 本身。"""
    env = dict(base)
    env.update(ISOLATED)
    env["WF_LOCAL_DIR"] = str(local_dir)
    env[CHILD_FLAG] = "1"
    env.pop("VIRTUAL_ENV", None)
    return env


def _run_install_regressions():
    """Register existing unittest cases in the isolated selftest result stream."""
    from .selftest_install_commit import InstallCommitTests
    from .selftest_install_history import HistoryTests
    _register_unittests(InstallCommitTests, HistoryTests)


def _run_gate_provider_tests():
    """Register each gate-provider unittest method once. At depth 1 none is registered: they run the real
    provider, which must not run nested; nothing is reported as passed for them in that case."""
    from .selftest_v18 import probe_depth
    if probe_depth() != 0:
        return
    from .selftest_gate_provider import GateProviderTests
    _register_unittests(GateProviderTests)


def _register_unittests(*classes):
    """One _ok per test method; errors, failures, skips, expected failures and unexpected successes all fail.
    A class list that yields zero methods fails too (it can never vanish from the count)."""
    import io
    import unittest
    from contextlib import redirect_stdout, redirect_stderr

    cases = [test for cls in classes
             for test in unittest.defaultTestLoader.loadTestsFromTestCase(cls)]
    if not cases:
        _ok(f"unittest 登记：{', '.join(c.__name__ for c in classes)} 没有任何测试方法，按未通过处理", False, "0 cases")
        return
    result = unittest.TestResult()
    output = io.StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        unittest.TestSuite(cases).run(result)
    problems = {test.id(): detail for test, detail in result.errors + result.failures
                + result.skipped + result.expectedFailures}
    problems.update((test.id(), "unexpected success") for test in result.unexpectedSuccesses)
    case_ids = {test.id() for test in cases}
    suite_errors = [detail for test, detail in result.errors + result.failures if test.id() not in case_ids]
    if result.testsRun != len(cases):
        suite_errors.append(f"suite ran {result.testsRun} of {len(cases)} registered cases")
    suite_detail = "\n".join(suite_errors)
    for test in cases:
        detail = suite_detail or problems.get(test.id(), "")
        _ok(test.id(), not suite_errors and test.id() not in problems,
            detail + output.getvalue()[-2000:] if detail else "")


# ---------------------------------------------------------------- suite registration (C-C1-STRICT21-IMPLEMENT)
# Every component suite is either registered below (actual entry, scope, depth policy, budget) or listed in
# ACCEPTANCE_SUITES with the reason it is not in the regular count. Budgets are finite and measured on the
# composed parent (Linux, one CPU; SEND 5 s, B3 fixtures 6.6 s, FX-TX 24 s, C provider focused ~5 s, aggregate
# focused ~25 s): several times the observed time, never "no timeout".
REGULAR_SUITES = (
    # (label, kind, entry, depths, budget_s, reason)
    ("A-B3-SEND", "unittest", ["wflib.selftest_b3_send:B3SendTests"], (0, 1), 300,
     "9 unittest methods; real driver.h_send/_prepare_send/h_wait_reply against fake_transport; no Gate"),
    ("B3 夹具自检", "okfn", "wflib.selftest_b3_fixtures:run_b3_fixtures", (0, 1), 300,
     "custom _ok entry (not unittest); each actual _ok recorded in order, strict bool; 0 assertions fail"),
    ("FX-TX 16 链与 CL-CT5", "fxtx", None, (0, 1), 180,
     "own entry python -B -S -m wflib.selftest_install_fixtures --mode new (setsid child, source read-only digest);"
     " each structured report verdict is one assertion; no Gate, no wf selftest recursion"),
    ("C1 提供方 focused", "unittest", ["wflib.selftest_c1_providers:*"], (0, 1), 120,
     "lane C provider adapter contract tests; not delegated by lane C (live timing is, so it is not listed)"),
    ("C1 严格聚合 focused", "unittest", ["wflib.selftest_c1_aggregate:*"], (0,), 600,
     "strict aggregate registry/validation/process-boundary tests; synthetic providers; spawns aggregate jobs, "
     "so depth 0 only (never nested)"),
)
ACCEPTANCE_SUITES = (
    # (label, entry, inputs, reason not in the regular count)
    ("B3 first-upgrade recovery (Upgrade19)", "python3 -m unittest wflib.selftest_b3_first_upgrade_recovery",
     "git objects fafef7e6 (old tree) and the candidate tree (B3_CANDIDATE_TREE) in this checkout's .git",
     "needs historical Git objects that an installed package (no .git) and a plain clone do not carry; "
     "missing objects make setUpClass skip, which must never count as a pass"),
    ("FX-S06 behaviour (S06)", "python3 -m unittest wflib.selftest_b3_s06",
     "B3_S06_LEGACY_V2 and B3_S06_LEGACY_B1: two caller-prepared full Git checkouts (never fetched here)",
     "needs two historical full checkouts; the product selftest must not require the network or old versions"),
)


def _run_registered_suites():
    """Run REGULAR_SUITES for this depth in bounded child jobs; print the acceptance-only suites (not run)."""
    from .selftest_v18 import probe_depth
    from . import selftest_c1_aggregate as agg
    depth = probe_depth()
    counts = []
    for label, kind, entry, depths, budget, _reason in REGULAR_SUITES:
        if depth not in depths:
            print(f"  （深度 {depth} 不运行、不登记：{label}）", flush=True)
            continue
        if kind == "unittest":
            counts.append(agg.register_unittest_job(_ok, label, entry, total_s=budget))
        elif kind == "fxtx":
            counts.append(agg.register_fxtx(_ok, label, total_s=budget))
        else:
            counts.append(agg.register_okfn_job(_ok, label, entry, total_s=budget))
    for c in counts:
        print(f"  {c['label']}：登记 {c.get('loaded', c.get('asserts'))}，实际运行 {c.get('run', c.get('asserts'))}，"
              f"通过 {c['passed']}", flush=True)
    for label, entry, inputs, reason in ACCEPTANCE_SUITES:
        print(f"  （验收扩展，未纳入常规计数、此处未运行：{label}；入口 {entry}；输入 {inputs}）", flush=True)


def _run_lane_c_once():
    """Lane C runs once per run. At depth 0 inside a selftest run it ran in the strict-aggregate job child
    (the same records the six C providers read) and is replayed here in order; otherwise it runs in-process."""
    from .selftest_v18 import probe_depth
    from . import selftest_c1_aggregate as agg
    run = agg.current_run()
    if probe_depth() == 0 and run is not None and run.lane_c:
        agg.replay_lane_c(run, _ok)
        return
    from .selftest_lane_c import run_lane_c
    run_lane_c(_ok)


SELFTEST_CHILD_BUDGET_S = 1500 + 1500   # old cap (parent measured 431 s) + registered-suite caps (sum 1500)


def run() -> int:
    from .selftest_v18 import ProbeDepthError, probe_depth
    try:   # before CHILD_FLAG: being inside the isolation child is never a way around the depth bound
        probe_depth()
    except ProbeDepthError as e:
        print(f"自检未运行：{e}", flush=True)
        return ProbeDepthError.exit_code
    if os.environ.get(CHILD_FLAG) != "1":
        tmp = Path(tempfile.mkdtemp(prefix="wf-selftest-env-"))
        try:
            from .selftest_c1_aggregate import run_bounded
            r = run_bounded([sys.executable, str(WF_HOME / "wf.py"), "selftest"], cwd=Path.cwd(),
                            env=child_env(os.environ, tmp / "local"), timeout=SELFTEST_CHILD_BUDGET_S)
            if r["timed_out"]:   # the whole process tree is stopped and killed, not only the direct child
                print(f"自检子进程超过 {SELFTEST_CHILD_BUDGET_S} 秒没有结束，已结束整个进程树，按未通过处理", flush=True)
                return 1
            return r["rc"]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return _run_checks()


def _run_checks() -> int:
    from . import selftest_c1_aggregate as agg
    with agg.run_scope(lane_c=True):   # one run: Gate group, 门禁基线 and the lane C replay share one execution
        return _run_all_checks()


def _run_all_checks() -> int:
    print(f"collab-wf 自检（{(WF_HOME / 'VERSION').read_text().strip()}，Python {sys.version.split()[0]}）")
    from .selftest_v15 import run_v15   # 1.5.0 新增检查：纯函数与解析回归，计入总数
    run_v15(_ok)
    from .selftest_v18 import run_v18   # 1.8.2 新增检查，计入总数
    run_v18(_ok)
    # IC-2（批次 1）：只接入已合入 integ/batch1 的车道检查，计入总数；与上面一样运行在本子进程的隔离环境里
    # （WF_NOTIFY=0、临时 WF_LOCAL_DIR）。尚未合入的车道不在这里导入。
    print("  —— B1-C 车道检查（证据分层与不适用）——", flush=True)
    _run_lane_c_once()
    print("  —— B1-B 车道检查（bootstrap 材料同源）——", flush=True)
    from .selftest_lane_b import run_lane_b
    run_lane_b(_ok)
    print("  —— B1-A 车道检查（发送槽精确持有者）——", flush=True)
    from .selftest_lane_a import run_lane_a
    run_lane_a(_ok)
    print("  —— B2 独立审查与安装历史回归 ——", flush=True)
    from .selftest_review import run_review
    run_review(_ok)
    _run_install_regressions()
    _run_gate_provider_tests()
    print("  —— 组件 suite 登记（C1 严格聚合卡）——", flush=True)
    _run_registered_suites()
    tmp = Path(tempfile.mkdtemp(prefix="wf-selftest-"))
    env = {**os.environ, "WF_LOCAL_DIR": str(tmp / "local"), "WF_NOTIFY": "0"}   # 子进程里本来就是 0，这里照旧写明
    env.pop("VIRTUAL_ENV", None)
    try:
        proj = tmp / "demo"
        _synthetic_review_host(env, tmp)
        # 1. 新建项目
        rc, o = _wf(env, tmp, "init", "--name", "demo", "--root", str(proj))
        _ok("新建项目", rc == 0 and (proj / "PROJECT.md").exists() and (proj / "workflow" / "wf.json").exists(), o)
        rc, o = _wf(env, proj, "doctor")
        _ok("新项目体检通过", "✓ PROJECT.md" in o, o)
        (proj / "old.txt").write_text("to be deleted")
        subprocess.run(["git", "init", "-q"], cwd=proj)
        subprocess.run(["git", "add", "-A"], cwd=proj)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "release v0.1"], cwd=proj)
        subprocess.run(["git", "tag", "v0.1"], cwd=proj)

        # 2. 一轮完整流程
        rc, o = _wf(env, proj, "go", "COMPOSE")
        _ok("进入 COMPOSE，轮次 +1", rc == 0 and _state(proj)["turn"] == 1, o)
        msg = tmp / "msg.md"
        msg.write_text("任务：测试 v0.1。档位：high\n", encoding="utf-8")
        rc, o = _wf(env, proj, "check-outgoing", str(msg), "--kind", "task")
        _ok("任务消息检查通过", rc == 0, o)
        rc, o = _wf(env, proj, "mark", "sent", "--message-file", str(msg), "--kind", "task", "--effort", "high",
                    "--model", "test", "--usage-session", "10", "--usage-weekly", "20")
        _ok("记账：已发送 → WAIT_REPLY", rc == 0 and _state(proj)["state"] == "WAIT_REPLY", o)
        reply = tmp / "reply.md"
        reply.write_text(REPLY, encoding="utf-8")
        time.sleep(1.1)
        rc, o = _wf(env, proj, "mark", "replied", "--reply-file", str(reply), "--usage-session", "14")
        turns = [json.loads(l) for l in (proj / "workflow" / "turns.jsonl").read_text().splitlines()]
        _ok("记账：已收到 → INTAKE，耗时为实测值", rc == 0 and _state(proj)["state"] == "INTAKE"
            and turns[-1].get("duration_s", 0) >= 1, o)
        rc, o = _wf(env, proj, "intake", str(reply))
        _ok("解析回复：五块齐全、3 条命令、2 项清单 → REVIEW", rc == 0 and _state(proj)["state"] == "REVIEW"
            and "命令 3 条" in o and "回报清单 2 项" in o, o)
        rc, o = _wf(env, proj, "exec", "--", "echo early")
        _ok("评审未接受时拒绝执行", rc != 0 and "评审尚未接受" in o, o)
        rc, o = _wf(env, proj, "review")
        _ok("风险分级：rm 标为谨慎", "[谨慎] rm -rf build" in o, o)
        rc, o = _wf(env, proj, "review", "--accept")
        _ok("评审接受", rc == 0 and "已接受" in o, o)
        z2 = tmp / "demo-v0.2.zip"
        _make_zip(z2, "demo", {"PROJECT.md": _project_md("v0.2"), "CHANGELOG.md": "# CHANGELOG\n\n## v0.2 — t\n- x\n",
                               "app.py": "print('v0.2')\n", "run.sh": "echo run\n",
                               "RELEASE.json": json.dumps({"delete": ["old.txt"], "notes": "测试"}),
                               "workflow/state.json": "{\"evil\": true}"})
        # CLI apply otherwise moves REVIEW -> APPLY before consuming the request.
        # Finalize that write first, then freeze and review the exact installation.
        rc, o = _wf(env, proj, "go", "APPLY")
        _ok("进入 APPLY 后再冻结安装审查材料", rc == 0, o)
        rid2 = _reviewed_request(env, proj, z2)
        rc, o = _wf(env, proj, "apply", str(z2), "--review-id", rid2)
        _ok("落地版本：提交、打标签、按清单删除、受保护文件不被覆盖、保留执行权限",
            rc == 0 and (proj / "app.py").exists() and not (proj / "old.txt").exists()
            and "evil" not in (proj / "workflow" / "state.json").read_text()
            and "v0.2" in subprocess.run(["git", "tag"], cwd=proj, capture_output=True, text=True).stdout
            and _state(proj)["state"] == "EXECUTE" and os.access(proj / "run.sh", os.X_OK), o)
        bad = tmp / "demo-bad.zip"
        _make_zip(bad, "demo", {"PROJECT.md": "# 坏的\n", "app.py": "print('bad')\n"})
        rc, o = _wf(env, proj, "force", "APPLY", "--reason", "selftest：项目格式负例")
        _ok("项目格式负例先固定 APPLY 状态", rc == 0, o)
        bad_id = _reviewed_request(env, proj, bad)
        rc, o = _wf(env, proj, "apply", str(bad), "--review-id", bad_id)
        _ok("真实审查后项目格式校验失败不改动仓库（先校验后复制）",
            rc != 0 and "PROJECT.md 校验未通过" in o and (proj / "app.py").exists()
            and (proj / "app.py").read_text() == "print('v0.2')\n", o)
        slip = tmp / "slip.zip"
        with zipfile.ZipFile(slip, "w") as zf:
            zf.writestr("../evil.txt", "x")
        requests = Path(env["WF_LOCAL_DIR"]) / "reviews" / "requests"
        before_requests = set(requests.glob("*.json"))
        rc, o = _wf(env, proj, "review", "prepare", str(slip), "--kind", "project", "--author", "selftest", "--json")
        _ok("prepare 真实拒绝越界路径 zip、不生成请求", rc != 0 and "越界或不规范的路径" in o
            and set(requests.glob("*.json")) == before_requests
            and not (tmp / "evil.txt").exists(), o)
        z3 = tmp / "demo-v0.3.zip"
        _make_zip(z3, "demo", {"PROJECT.md": _project_md("v0.3"), "CHANGELOG.md": "# CHANGELOG\n\n## v0.3 — t\n- x\n",
                               "app.py": "print('v0.3')\n", "new3.txt": "n", "run.sh": "echo run\n",
                               "RELEASE.json": json.dumps({"delete": ["old.txt"]})})
        (proj / "old.txt").write_text("again")
        subprocess.run(["git", "add", "-A"], cwd=proj)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "restore old.txt"], cwd=proj)
        rc, o = _wf(env, proj, "force", "APPLY", "--reason", "selftest：删除失败注入")
        _ok("删除失败注入前固定 APPLY 状态", rc == 0, o)
        rid3 = _reviewed_request(env, proj, z3)
        rc, o = _wf({**env, "WF_TEST_FAIL_DELETE": "1"}, proj, "apply", str(z3), "--review-id", rid3)
        st_porcelain = subprocess.run(["git", "status", "--porcelain"], cwd=proj, capture_output=True, text=True).stdout
        clean = all(l[3:].startswith("workflow/") for l in st_porcelain.splitlines())
        _ok("删除阶段失败：核验恢复安装前基线（F15）", rc != 0 and "已核验恢复安装前基线" in o and clean
            and (proj / "app.py").exists() and (proj / "app.py").read_text() == "print('v0.2')\n"
            and not (proj / "new3.txt").exists()
            and (proj / "old.txt").exists(), o + st_porcelain)
        # A failed CLI call writes failure state; retry is a fresh reviewed request.
        rc, o = _wf(env, proj, "force", "APPLY", "--reason", "selftest：核验恢复后重试")
        _ok("恢复后先固定重试状态再冻结新请求", rc == 0, o)
        retry_id = _reviewed_request(env, proj, z3)
        rc, o = _wf(env, proj, "apply", str(z3), "--review-id", retry_id)
        _ok("恢复后使用新请求重试成功", rc == 0 and retry_id != rid3
            and (proj / "app.py").read_text() == "print('v0.3')\n"
            and not (proj / "old.txt").exists(), o)
        rc, o = _wf({**env, "PATH": "/usr/bin:/bin"}, proj, "exec", "--", "wf version")
        _ok("wf exec 内可以直接调用 wf（不依赖 shell 配置文件）", rc == 0 and "collab-wf" in o, o)
        rc, o = _wf(env, proj, "exec", "--", "echo hello")
        rc2, o2 = _wf(env, proj, "exec", "--", "echo to-stderr >&2; exit 3")
        ev = proj / "workflow" / "evidence" / "turn-001"
        _ok("执行取证：输出、错误、退出码", rc == 0 and rc2 == 3 and (ev / "c02.out").read_text().strip() == "hello"
            and "to-stderr" in (ev / "c03.err").read_text() and json.loads((ev / "c03.json").read_text())["exit_code"] == 3, o + o2)
        t0 = time.time()
        rc, o = _wf(env, proj, "exec", "--", "sleep 30 > /dev/null 2>&1 & echo started")
        _ok("后台子进程不会让执行卡住", rc == 0 and time.time() - t0 < 10, o)
        rc, o = _wf(env, proj, "verify", "set", "1", "pass", "--evidence", "c02")
        _ok("验收 → VERIFY", rc == 0 and _state(proj)["state"] == "VERIFY", o)
        rc, o = _wf(env, proj, "wait", "--until", "2030-01-01T00:00:00+00:00", "--reason", "测试")
        rc2, o2 = _wf(env, proj, "verify", "set", "2", "fail", "--evidence", "c03", "--note", "退出码 3")
        vj = json.loads((proj / "workflow" / "evidence" / "turn-001" / "verify.json").read_text())
        _ok("进入等待后可以事后补记验收（不改变状态）", rc == 0 and rc2 == 0 and vj["2"].get("post_hoc")
            and _state(proj)["state"] == "WAIT_SCHEDULE", o + o2)
        _wf(env, proj, "note", "--kind", "decision", "是否继续")
        rc, o = _wf(env, proj, "report")
        drafts = sorted((proj / "workflow" / "outbox").glob("turn-*.md"))
        text = drafts[-1].read_text(encoding="utf-8") if drafts else ""
        _ok("回报草稿：第 2 轮、high 档、嵌入原文与退出码、含决策点",
            rc == 0 and "第 2 轮" in text and "档位：high" in text and "hello" in text and "exit=3" in text
            and "是否继续" in text and _state(proj)["turn"] == 2, o)
        rc, o = _wf(env, proj, "check-outgoing", str(drafts[-1]), "--kind", "report")
        _ok("回报草稿通过发送前检查", rc == 0, o)
        rc, o = _wf(env, proj, "fingerprint")
        import re as _re
        m = _re.search(r"[0-9a-f]{16}", o)
        digest = m.group(0) if m else "?"
        plain = tmp / "plain-copy"
        shutil.copytree(proj, plain, ignore=shutil.ignore_patterns(".git"))
        rc2, o2 = _wf(env, tmp, "fingerprint", "--path", str(plain))
        _ok("仓库指纹：git 仓库与普通目录副本一致，并写入回报草稿", rc == 0 and rc2 == 0 and digest in o2
            and f"仓库指纹 {digest}" in text, o + o2)
        (proj / ".env").write_text("SECRET=1")
        rc, o = _wf(env, proj, "snapshot", "PROJECT.md,app.py", ".env", "--max-chars", "100000")
        _ok("文件包：包含指定文件，拒绝 .env", rc == 0 and "===== app.py" in o and "===== .env" not in o
            and ".env（不存在或不在可发送范围内）" in o, o)
        good = tmp / "bridge-good.jsonl"
        good.write_text("\n".join(json.dumps(x) for x in [
            {"ts": "2026-01-01T00:00:00+00:00", "action": "send"}, {"ts": "2026-01-01T00:00:01+00:00", "action": "accepted"},
            {"ts": "2026-01-01T00:00:05+00:00", "action": "generating"}, {"ts": "2026-01-01T00:20:00+00:00", "action": "done"},
            {"ts": "2026-01-01T00:20:01+00:00", "action": "quota", "value": 33}]) + "\n")
        rc, o = _wf(env, tmp, "bridge-check", "--log", str(good))
        aborted = tmp / "bridge-aborted.jsonl"
        aborted.write_text("\n".join(json.dumps(x) for x in [
            {"ts": "2026-01-01T00:00:00+00:00", "action": "send"},
            {"ts": "2026-01-01T00:00:02+00:00", "action": "aborted", "detail": "收到信号 15；发送结果未知"},
            {"ts": "2026-01-01T00:05:00+00:00", "action": "send"},
            {"ts": "2026-01-01T00:05:01+00:00", "action": "accepted"},
            {"ts": "2026-01-01T00:20:00+00:00", "action": "done"}]) + "\n")
        rc_ab, o_ab = _wf(env, tmp, "bridge-check", "--log", str(aborted))
        _ok("桥接日志自检：aborted 是发送终态，R1 与 R3 都通过（③-1/③-2）",
            rc_ab == 0 and "✗ R3" not in o_ab and "✗ R1" not in o_ab, o_ab)
        rc_la, o_la = _wf(env, tmp, "bridge-check", "--list-actions")
        _ok("bridge-check --list-actions 暴露权威动作清单（供适配器自检比对）",
            rc_la == 0 and "aborted" in o_la and "send" in o_la and "poll" in o_la.split(), o_la)
        bad = tmp / "bridge-bad.jsonl"
        bad.write_text("\n".join(json.dumps(x) for x in [
            {"ts": "2026-01-01T00:00:00+00:00", "action": "send"}, {"ts": "2026-01-01T00:00:01+00:00", "action": "accepted"},
            {"ts": "2026-01-01T00:01:00+00:00", "action": "send"}, {"ts": "2026-01-01T00:01:01+00:00", "action": "rejected"},
            {"ts": "2026-01-01T00:02:00+00:00", "action": "abort"},
            {"ts": "2026-01-01T00:03:00+00:00", "action": "quota", "value": 30, "estimated": True}]) + "\nnot json\n")
        rc2, o2 = _wf(env, tmp, "bridge-check", "--log", str(bad))
        _ok("桥接日志自检：合规日志通过；重叠发送、提前中止、估算值、坏行均被发现",
            rc == 0 and rc2 == 1 and "✗ R1" in o2 and "✗ R2" in o2 and "✗ R4" in o2 and "✗ R6" in o2, o + o2)
        (proj / ".env").unlink()

        rc, o = _wf(env, proj, "go", "IDLE")
        subprocess.run(["git", "add", "-A"], cwd=proj)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "wip"], cwd=proj)
        rc, o = _wf(env, proj, "bootstrap", "--reason", "测试")
        bm = sorted((proj / "workflow" / "outbox").glob("bootstrap-*.md"))
        bt = bm[-1].read_text(encoding="utf-8") if bm else ""
        bz = sorted((proj / "workflow" / "outbox").glob("demo-*.zip"))
        tz = sorted((proj / "workflow" / "outbox").glob("collab-wf-*.zip"))
        tnames = zipfile.ZipFile(tz[-1]).namelist() if tz else []
        _ok("新对话恢复包：说明消息含第 10 节、上一轮清单与验收、仓库指纹；项目 zip 不含 .env；另附工具本身的 zip（含 DESIGN.md，不含 local/）",
            rc == 0 and "第 10 节" in bt and "第 1 轮" in bt and "未通过" in bt and digest in bt and bz
            and ".env" not in " ".join(zipfile.ZipFile(bz[-1]).namelist())
            and "collab-wf/docs/DESIGN.md" in tnames and not any("/local/" in n for n in tnames) and "DESIGN.md" in bt,
            o + bt[:600] + str(tnames[:5]))
        _wf(env, proj, "go", "COMPOSE")

        # 3. 安全与异常
        leak = tmp / "leak.md"
        leak.write_text("【回报 · 第 2 轮 · v0.2】档位：high\n① ② ③\n```\nx\n```\nkey " + "aB3" * 21 + "x\n", encoding="utf-8")
        rc, o = _wf(env, proj, "check-outgoing", str(leak))
        rc2, o2 = _wf(env, proj, "mark", "sent", "--message-file", str(leak))
        _ok("密钥扫描：拦截检查与发送记账", rc == 2 and rc2 != 0 and "密钥" in o2 and _state(proj)["state"] == "COMPOSE", o + o2)
        rc, o = _wf(env, proj, "go", "APPLY")
        _ok("拒绝非法转换并给出允许的去向", rc != 0 and "允许进入" in o, o)
        _wf(env, proj, "go", "SEND")
        rc, o = _wf(env, proj, "wait", "--until", "2030-01-01T00:00:00+00:00", "--reason", "x")
        _ok("发出消息后设置计划节点：拒绝并说明正确顺序", rc != 0 and "REVIEW 或 VERIFY 之后" in o, o)
        for _ in range(3):
            rc, o = _wf(env, proj, "fail", "--reason", "环境被占用", "--symptom", "SD-1")
        _ok("失败 3 次自动阻塞", _state(proj)["state"] == "BLOCKED", o)
        rc, o = _wf(env, proj, "status")
        _ok("阻塞时显示原因与处置表", "阻塞原因" in o and "处置" in o, o)
        rc, o = _user(_wf, env, proj, "unblock", "unblock", "--to", "COMPOSE")
        _ok("解除阻塞", rc == 0 and _state(proj)["state"] == "COMPOSE", o)
        _wf(env, proj, "go", "IDLE")
        rc, o = _wf(env, proj, "wait", "--until", "2030-01-01T00:00:00+00:00", "--reason", "测试计划节点")
        _ok("进入计划等待", rc == 0 and _state(proj)["state"] == "WAIT_SCHEDULE", o)

        # 4. 需要用户批准
        _wf(env, proj, "go", "COMPOSE")
        _wf(env, proj, "mark", "sent", "--message-file", str(msg))
        r2 = tmp / "reply2.md"
        r2.write_text(REPLY.replace("exit 3", "python run_live.py --confirm-real-money"), encoding="utf-8")
        _wf(env, proj, "mark", "replied", "--reply-file", str(r2))
        _wf(env, proj, "intake", str(r2))
        rc, o = _wf(env, proj, "review", "--accept")
        _ok("实盘命令需要用户批准 → AWAITING_USER", _state(proj)["state"] == "AWAITING_USER", o)
        rc, o = _wf(env, proj, "resume", "--note", "用户：暂不执行")
        _ok("用户决定后回到评审", rc == 0 and _state(proj)["state"] == "REVIEW", o)

        # 5. 项目回滚
        _wf(env, proj, "force", "IDLE", "--reason", "测试回滚")
        rc, o = _wf(env, proj, "rollback", "--to", "v0.1")
        _ok("回滚到 v0.1（恢复删除的文件、移除新增文件）", rc == 0 and (proj / "old.txt").exists()
            and not (proj / "app.py").exists() and not (proj / "new3.txt").exists(), o)
        rc, o = _wf(env, proj, "rollback", "--to", "v0.2")
        _ok("再回滚到 v0.2", rc == 0 and (proj / "app.py").exists(), o)

        # 6. 看板服务
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        (proj / ".env").write_text("SECRET=1")
        srv = subprocess.Popen([sys.executable, str(WF_HOME / "wf.py"), "serve", "--port", str(port)], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            base = f"http://127.0.0.1:{port}"
            for _ in range(50):
                try:
                    urllib.request.urlopen(base + "/api/projects", timeout=1)
                    break
                except Exception:
                    time.sleep(0.1)
            get = lambda p: json.loads(urllib.request.urlopen(base + p, timeout=5).read())
            pj = get("/api/projects")
            fl = get("/api/flow?p=demo")
            pr = get("/api/project?p=demo")
            _ok("看板接口：项目列表、工作流、项目视图", pj["projects"][0]["name"] == "demo"
                and fl["status"]["state"] and fl["timeline"]["segments"] and pr["project"]["name"] == "demo")
            code = lambda p: (lambda r: r.status)(urllib.request.urlopen(base + p, timeout=5))
            try:
                code("/files/demo/.env"); blocked = False
            except urllib.error.HTTPError as e:
                blocked = e.code == 403
            _ok("看板屏蔽 .env，开放 PROJECT.md 与工作流文档", blocked and code("/files/demo/PROJECT.md") == 200
                and code("/files/demo/@wf/docs/WORKFLOW.md") == 200 and code("/") == 200 and code("/project.html") == 200)
        finally:
            srv.terminate()
            srv.wait(timeout=10)

        # 7. 工具自身的升级与回滚（在副本上进行）
        copy = tmp / "collab-wf"
        shutil.copytree(WF_HOME, copy, ignore=shutil.ignore_patterns(".git", "local", "__pycache__"))
        cwf = copy / "wf.py"
        rc, o = _wf(env, tmp, "self-manifest", wf=cwf)
        _ok("工具副本安装前清单与实际字节一致", rc == 0, o)
        rc, o = _wf(env, tmp, "self-init", wf=cwf)
        _ok("工具副本初始化版本库", rc == 0, o)
        cur = (copy / "VERSION").read_text().strip()
        major, minor, patch = cur.split(".")
        nxt = f"{major}.{minor}.{int(patch) + 1}"
        up = tmp / f"collab-wf-{nxt}.zip"
        files = {}
        for p in copy.rglob("*"):
            if p.is_file() and ".git" not in p.parts and "local" not in p.parts and "__pycache__" not in p.parts:
                files[str(p.relative_to(copy))] = p.read_bytes()
        files["VERSION"] = (nxt + "\n").encode()
        files["NEWFILE.txt"] = b"new"
        # MANIFEST is reviewed package content; the installer must never repair it.
        files["MANIFEST.json"] = json.dumps({
            "version": nxt,
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()
                      if name != "MANIFEST.json"},
        }).encode()
        _make_zip(up, "collab-wf", files)
        update_id = _reviewed_request(env, tmp, up, kind="tool", wf=cwf)
        rc, o = _wf(env, tmp, "self-update", str(up), "--skip-test", "--review-id", update_id, wf=cwf)
        _ok(f"工具升级 {cur} → {nxt}", rc == 0 and (copy / "VERSION").read_text().strip() == nxt, o)
        man = json.loads((copy / "MANIFEST.json").read_text())
        rc_sc, o_sc = _wf(env, tmp, "self-check", wf=cwf)
        _ok("包内预生成的 MANIFEST 原样安装，self-check 通过（③-1）",
            (copy / "MANIFEST.json").read_bytes() == files["MANIFEST.json"]
            and man.get("version") == nxt and "NEWFILE.txt" in man.get("files", {}) and rc_sc == 0,
            o_sc + str(man.get("version")))
        same_id = _reviewed_request(env, tmp, up, kind="tool", wf=cwf)
        rc, o = _wf(env, tmp, "self-update", str(up), "--skip-test", "--review-id", same_id, wf=cwf)
        _ok("真实审查后仍拒绝重复安装同一版本", rc != 0 and "不高于" in o, o)
        rc, o = _wf(env, tmp, "self-rollback", cur, wf=cwf)
        _ok(f"工具回滚到 {cur}（移除新增文件）", rc == 0 and (copy / "VERSION").read_text().strip() == cur
            and not (copy / "NEWFILE.txt").exists(), o)
        # 8. 驱动器
        from . import selftest_driver
        dtmp = tmp / "driver"
        dtmp.mkdir()
        denv = {**env, "WF_LOCAL_DIR": str(dtmp / "local")}
        try:
            selftest_driver.run(dtmp, denv, _wf, _ok, _state, _project_md, _make_zip)
            selftest_driver.run_v12(dtmp, denv, _wf, _ok, _state, _project_md, _make_zip)
            selftest_driver.run_v14(dtmp, denv, _wf, _ok, _state, _project_md, _make_zip)
        except Exception as e:  # 自检本身出错也要计为失败
            import traceback
            _ok("驱动器自检运行", False, traceback.format_exc()[-1500:])
    except Exception:
        import traceback
        _ok("端到端夹具完成运行", False, traceback.format_exc())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    n_ok = sum(1 for r in RESULTS if r[0])
    print(f"\n自检结果：{n_ok} / {len(RESULTS)} 通过")
    return 0 if n_ok == len(RESULTS) else 1
