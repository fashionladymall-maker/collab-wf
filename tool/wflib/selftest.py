"""端到端自检：在临时目录里用真实命令走完整条流程。不读写真实注册表与项目。
wf self-update 在安装新版本之前会先运行新版本的自检。"""
from __future__ import annotations

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


def run() -> int:
    print(f"collab-wf 自检（{(WF_HOME / 'VERSION').read_text().strip()}，Python {sys.version.split()[0]}）")
    from .selftest_v15 import run_v15   # 1.5.0 新增检查：纯函数与解析回归，计入总数
    run_v15(_ok)
    from .selftest_v18 import run_v18   # 1.8.2 新增检查，计入总数
    run_v18(_ok)
    tmp = Path(tempfile.mkdtemp(prefix="wf-selftest-"))
    env = {**os.environ, "WF_LOCAL_DIR": str(tmp / "local")}
    env.pop("VIRTUAL_ENV", None)
    try:
        proj = tmp / "demo"
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
        rc, o = _wf(env, proj, "apply", str(z2))
        _ok("落地版本：提交、打标签、按清单删除、受保护文件不被覆盖、保留执行权限",
            rc == 0 and (proj / "app.py").exists() and not (proj / "old.txt").exists()
            and "evil" not in (proj / "workflow" / "state.json").read_text()
            and "v0.2" in subprocess.run(["git", "tag"], cwd=proj, capture_output=True, text=True).stdout
            and _state(proj)["state"] == "EXECUTE" and os.access(proj / "run.sh", os.X_OK), o)
        bad = tmp / "demo-bad.zip"
        _make_zip(bad, "demo", {"PROJECT.md": "# 坏的\n", "app.py": "print('bad')\n"})
        rc, o = _wf(env, proj, "apply", str(bad))
        _ok("校验失败时不改动仓库（先校验后复制）", rc != 0 and (proj / "app.py").read_text() == "print('v0.2')\n", o)
        slip = tmp / "slip.zip"
        with zipfile.ZipFile(slip, "w") as zf:
            zf.writestr("../evil.txt", "x")
        rc, o = _wf(env, proj, "apply", str(slip))
        _ok("拒绝越界路径的 zip", rc != 0 and not (tmp / "evil.txt").exists(), o)
        z3 = tmp / "demo-v0.3.zip"
        _make_zip(z3, "demo", {"PROJECT.md": _project_md("v0.3"), "CHANGELOG.md": "# CHANGELOG\n\n## v0.3 — t\n- x\n",
                               "app.py": "print('v0.3')\n", "new3.txt": "n", "run.sh": "echo run\n",
                               "RELEASE.json": json.dumps({"delete": ["old.txt"]})})
        (proj / "old.txt").write_text("again")
        subprocess.run(["git", "add", "-A"], cwd=proj)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "restore old.txt"], cwd=proj)
        rc, o = _wf({**env, "WF_TEST_FAIL_DELETE": "1"}, proj, "apply", str(z3))
        st_porcelain = subprocess.run(["git", "status", "--porcelain"], cwd=proj, capture_output=True, text=True).stdout
        clean = all(l[3:].startswith("workflow/") for l in st_porcelain.splitlines())
        _ok("删除阶段失败：自动恢复工作区（F15）", rc != 0 and "已自动恢复" in o and clean
            and (proj / "app.py").read_text() == "print('v0.2')\n" and not (proj / "new3.txt").exists()
            and (proj / "old.txt").exists(), o + st_porcelain)
        rc, o = _wf(env, proj, "apply", str(z3))
        _ok("恢复后直接重试成功", rc == 0 and (proj / "app.py").read_text() == "print('v0.3')\n"
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
        _make_zip(up, "collab-wf", files)
        rc, o = _wf(env, tmp, "self-update", str(up), "--skip-test", wf=cwf)
        _ok(f"工具升级 {cur} → {nxt}", rc == 0 and (copy / "VERSION").read_text().strip() == nxt, o)
        man = json.loads((copy / "MANIFEST.json").read_text())
        rc_sc, o_sc = _wf(env, tmp, "self-check", wf=cwf)
        _ok("升级后 MANIFEST 随安装的版本自动重算，self-check 通过（③-1）",
            man.get("version") == nxt and "NEWFILE.txt" in man.get("files", {}) and rc_sc == 0, o_sc + str(man.get("version")))
        rc, o = _wf(env, tmp, "self-update", str(up), "--skip-test", wf=cwf)
        _ok("拒绝重复安装同一版本", rc != 0, o)
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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    n_ok = sum(1 for r in RESULTS if r[0])
    print(f"\n自检结果：{n_ok} / {len(RESULTS)} 通过")
    return 0 if n_ok == len(RESULTS) else 1
