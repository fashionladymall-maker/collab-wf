"""Real runner/verifier and installation counterexamples for the driver consumer."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch


def run(_ok):
    from . import anomaly, core, driver, driver_review, release, review
    from .core import Instance, WfError
    from .selftest import _project_md
    from .selftest_review import AUTHOR_SESSION, Env, _git

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        with patch.object(core, "LOCAL", base / "local"), patch.object(anomaly, "LOCAL", base / "local"):
            env = Env(base)
            with patch.object(review, "_tool_home", lambda: env.tool):
                def setup(name):
                    root = env.project(name)
                    (root / "PROJECT.md").write_text(_project_md("v0.1"))
                    _git(root, "add", "-A")
                    _git(root, "commit", "-qm", "valid baseline")
                    package = env.pkg(name, {"PROJECT.md": _project_md("v0.2").encode(),
                                            "CHANGELOG.md": b"# CHANGELOG\n\n## v0.2\n- test\n",
                                            "app.py": b"print('v2')\n"})
                    inst = Instance(root)
                    inst.state.update(state="APPLY", turn=1, release_path=str(package),
                                      conv={"conv": AUTHOR_SESSION})
                    inst.save()
                    return inst, package

                def ctx(inst, pol=None):
                    return driver.Ctx(inst, pol or {}, time.time() + 30)

                def count_requests():
                    return len(list((base / "local/reviews/requests").glob("*.json")))

                inst, package = setup("success")
                (inst.root / "dirty.txt").write_text("save before prepare")
                actual_apply = release.apply_zip
                seen = []

                def capture(*args, **kwargs):
                    seen.append(kwargs.get("review_id"))
                    return actual_apply(*args, **kwargs)

                with patch.object(release, "apply_zip", capture):
                    result = driver.h_apply(ctx(inst, {"gates": [{"name": "baseline",
                        "cmd": "echo baseline > gate-side.txt", "pass": {"exit": 0}}]}))
                record_dir = inst.root / "workflow/evidence/install/driver-review"
                request_record = json.loads(next(record_dir.glob("*-request.json")).read_text())
                req = review._load_request(request_record["request_id"])
                committed = _git(inst.root, "show", req["base"]["commit"] + ":dirty.txt")
                gate_saved = _git(inst.root, "show", req["base"]["commit"] + ":gate-side.txt")
                completed = [json.loads(p.read_text()) for p in
                             (inst.root / "workflow/evidence/install").glob("tx-*/completed.json")]
                _ok("驱动器强制审查：baseline gates 与 dirty 保存先于冻结；真实审查 exact ID 被安装消费并闭合",
                    result[0] == "progress" and seen == [request_record["request_id"]]
                    and committed == "save before prepare" and gate_saved == "baseline"
                    and completed[0]["request_id"] == seen[0]
                    and (inst.root / "app.py").read_bytes() == b"print('v2')\n"
                    and len(list(record_dir.glob("*-closed.json"))) == 1, str(result))

                before = count_requests()
                inst.state.update(state="APPLY", release_path=str(env.pkg("different")))
                inst.save()
                result = driver.h_apply(ctx(inst))
                _ok("驱动器 closed：同轮换包仍不复用旧授权、不重审、不重装",
                    result[0] == "user" and "driver_review_incomplete" in result[1]
                    and count_requests() == before and (inst.root / "app.py").read_bytes() == b"print('v2')\n",
                    str(result))

                failures = {}
                for name, behavior, remove_runner in (
                        ("block", {"verdict": "block"}, False),
                        ("timeout", {"sleep": 2}, False), ("no-runner", {}, True)):
                    inst, package = setup(name)
                    env.behave(**behavior)
                    env.set_transport(review_timeout_s=1)
                    if remove_runner:
                        cfg = json.loads(env.transport.read_text())
                        cfg.pop("review")
                        env.transport.write_text(json.dumps(cfg))
                    before_bytes = (inst.root / "app.py").read_bytes()
                    with patch.object(driver.Ctx, "time_left", lambda self: 1):
                        result = driver.h_apply(ctx(inst))
                    before = count_requests()
                    inst.state["state"] = "APPLY"
                    inst.save()
                    again = driver.h_apply(ctx(inst))
                    failures[name] = (result[0] == "user" and again[0] == "user"
                                      and count_requests() == before
                                      and "driver_review_incomplete" in again[1]
                                      and (inst.root / "app.py").read_bytes() == before_bytes)
                _ok("驱动器 block/timeout/无runner：目标不安装，失败重入不重发、不创建新请求",
                    all(failures.values()), str(failures))
                env.behave()
                env.set_transport()

                inst, package = setup("interrupted")
                calls = []

                def interrupted(rid, **kwargs):
                    calls.append(rid)
                    raise KeyboardInterrupt()

                with patch.object(review, "run", interrupted):
                    try:
                        driver.h_apply(ctx(inst))
                    except KeyboardInterrupt:
                        pass
                    result = driver.h_apply(ctx(inst))
                _ok("驱动器审查被中断：保留意图，下一次不再调用runner，给出fresh CLI恢复路径",
                    len(calls) == 1 and result[0] == "user" and "wf apply" in result[1]
                    and "driver_review_incomplete" in result[1]
                    and (inst.root / "app.py").read_bytes() == b"print('v1')\n", str(result))

                inst, package = setup("killed")
                env.behave(sleep=10)
                script = ("import time\nfrom pathlib import Path\nfrom wflib import driver\n"
                          "from wflib.core import Instance\n"
                          "inst = Instance(Path(" + repr(str(inst.root)) + "))\n"
                          "print(driver.h_apply(driver.Ctx(inst, {}, time.time()+30)), flush=True)\n")
                child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, start_new_session=True,
                    env={**os.environ, "WF_LOCAL_DIR": str(base / "local")})
                pending = []
                try:
                    until = time.monotonic() + 5
                    while time.monotonic() < until:
                        pending = [p for p in (base / "local/reviews/attempts").glob("*/attempt.json")
                                   if json.loads(p.read_text()).get("state") == "pending"]
                        if pending or child.poll() is not None:
                            break
                        time.sleep(0.02)
                finally:
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGKILL)
                    child.communicate(timeout=5)
                before = count_requests()
                attempts_before = len(list((base / "local/reviews/attempts").glob("*/attempt.json")))
                result = driver.h_apply(ctx(inst))
                _ok("驱动器真实pending进程被杀：重入不新建request/attempt、不重新送审或安装",
                    bool(pending) and result[0] == "user" and count_requests() == before
                    and len(list((base / "local/reviews/attempts").glob("*/attempt.json"))) == attempts_before
                    and "driver_review_incomplete" in result[1]
                    and (inst.root / "app.py").read_bytes() == b"print('v1')\n", str(result))
                env.behave()

                inst, package = setup("changed")
                real_run = review.run

                def mutate(rid, **kwargs):
                    decision = real_run(rid, **kwargs)
                    (inst.root / "app.py").write_text("external change\n")
                    return decision

                with patch.object(review, "run", mutate):
                    result = driver.h_apply(ctx(inst))
                _ok("驱动器审查后目标改变：install阶段重新核对并拒绝，不覆盖外部改动",
                    result[0] == "user" and (inst.root / "app.py").read_text() == "external change\n"
                    and not list((inst.root / "workflow/evidence/install").glob("tx-*/started.json")), str(result))

                inst, package = setup("tamper-active")

                def corrupt_record(rid, **kwargs):
                    decision = real_run(rid, **kwargs)
                    intent = next((inst.root / "workflow/evidence/install/driver-review").glob("*-intent.json"))
                    intent.chmod(0o600)
                    intent.write_text("{}")
                    return decision

                with patch.object(review, "run", corrupt_record):
                    result = driver.h_apply(ctx(inst))
                _ok("驱动器同次审查意图被改写：追加后续阶段前重读旧阶段，拒绝安装",
                    result[0] == "user" and "driver_review_corrupt" in result[1]
                    and (inst.root / "app.py").read_bytes() == b"print('v1')\n"
                    and not list((inst.root / "workflow/evidence/install").glob("tx-*/started.json")), str(result))

                inst, package = setup("lock")
                script = ("from pathlib import Path\nfrom wflib.core import Instance, WfError\n"
                          "from wflib.driver_review import Attempt\n"
                          "try:\n"
                          " with Attempt(Instance(Path(" + repr(str(inst.root)) + ")), Path("
                          + repr(str(package)) + ")): print('UNEXPECTED')\n"
                          "except WfError as e: print(e)\n")
                with driver_review.Attempt(inst, package):
                    child = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                                           env={**os.environ, "WF_LOCAL_DIR": str(base / "other-local")}, timeout=10)
                _ok("驱动器目标锁：不同WF_LOCAL_DIR的真实子进程同一root不能同时送审",
                    child.returncode == 0 and "driver_review_busy" in child.stdout, child.stdout + child.stderr)

                inst, package = setup("symlink")
                with driver_review.Attempt(inst, package) as attempt:
                    prefix, path = attempt.prefix, attempt.path
                outside = base / "outside.json"
                outside.write_text('{"keep": true}')
                (path / f"{prefix}-intent.json").symlink_to(outside)
                before = count_requests()
                result = driver.h_apply(ctx(inst))
                _ok("驱动器意图链接：拒绝读取/覆盖链接目标，不送审、不安装",
                    result[0] == "user" and count_requests() == before
                    and outside.read_text() == '{"keep": true}'
                    and (inst.root / "app.py").read_bytes() == b"print('v1')\n", str(result))

                inst, package = setup("bad-record")
                with driver_review.Attempt(inst, package) as attempt:
                    path, prefix = attempt.path, attempt.prefix
                (path / f"{prefix}-intent.json").write_text("{}")
                result = driver.h_apply(ctx(inst))
                _ok("驱动器损坏意图：失败关闭，不能当成未尝试并重新送审",
                    result[0] == "user" and "driver_review_corrupt" in result[1], str(result))

                inst, package = setup("author")
                conv_author = driver_review.author_for(inst)
                inst.state["conv"] = {}
                missing = False
                try:
                    driver_review.author_for(inst)
                except WfError as e:
                    missing = "author_identity_missing" in str(e)
                env.set_transport(driver_review={"schema": "driver-review/1", "author": "B"})
                explicit_author = driver_review.author_for(inst)
                inst.state["conv"] = {"conv": AUTHOR_SESSION}
                env.set_lanes({"B": {"sessions": [AUTHOR_SESSION]}, "other": {"sessions": [AUTHOR_SESSION]}})
                ambiguous = False
                try:
                    driver_review.author_for(inst)
                except WfError as e:
                    ambiguous = "author_identity_ambiguous" in str(e)
                _ok("驱动器作者兼容：已有conv唯一匹配无需新配置；可选版本化显式绑定；缺失/歧义不任选lane",
                    conv_author == explicit_author == "B" and missing and ambiguous,
                    str((conv_author, explicit_author, missing, ambiguous)))
