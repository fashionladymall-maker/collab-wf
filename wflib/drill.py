"""1.6.8（P1-D）：独立审查 block 与超时的驱动器侧演练（docs/DRILL.md）。

在一个空目录里搭一个一次性的演练项目，用 WF_LOCAL_DIR 把注册表、inbox、transport.json、状态锁都隔离在这个目录里：
- 发送与轮询（Claude 通道）用工具自带的替身 fake_transport.py：演练不向 Claude 发消息，也不需要通道；
- 审查（transport.json 的 review）用调用方给的程序路径，正式演练时就是执行端真实的审查程序；
- 驱动器（wf run）是真实安装的同一份代码，走真实的 REVIEW → APPLY → 强制独立审查；block 与超时都必须阻止安装。
所以这里验证的是“真实驱动器消费真实审查程序的 block 与超时”，替身只替代 Claude 通道，不替代审查。

演练项目不在生产注册表里（生产的定时 wf tick 看不到它），不在 sync_projects 里（不会同步到公开仓库）；
block 演练的版本包故意削弱护栏，驱动器必须在落地前停下；演练结束后项目被暂停，目录原样保留作为证据，不删除任何东西。
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .core import LOCAL, WF_HOME, WfError, now_iso
from . import anomaly

KINDS = ("block", "timeout")
ACTIVE = ("COMPOSE", "SEND", "WAIT_REPLY", "INTAKE", "REVIEW", "APPLY", "EXECUTE", "VERIFY")


def _pmd(version: str, phase: str, extra_conclusion: str = "", extra_rule: str = "") -> str:
    return (f"# PROJECT.md —— P1-D 演练项目（一次性，不落地到任何真实项目）\n\n## 当前版本\n{version}（2026-10-12）—— 阶段：{phase}\n\n"
            "## 目标\n1. 演练独立审查的 block 与超时由驱动器正确消费\n\n"
            f"## 已确认结论\n1. 本项目只用于演练{extra_conclusion}\n\n## 已排除方案及原因\n- 无\n\n## 下一步\n- [ ] 演练\n\n"
            "## 待用户决策\n- 无\n\n## 已知限制\n- 无\n\n"
            "## 失败模式台账\n| 编号 | 日期 | 现象 | 根因 | 处置 | 状态 |\n|---|---|---|---|---|---|\n\n"
            f"## 协作约定\n- 四道底线（资金、密钥、合规、实盘）只由用户本人决定{extra_rule}\n")


def _reply(release: str) -> str:
    return ("【本轮结论】\nP1-D 演练回复（由演练驱动写入替身通道，不是 Claude 的回复）。\n\n【本机执行】\n```\necho drill\n```\n\n"
            "【本轮回报清单】\n1. echo 的输出\n\n【需要用户决定】\n无\n\n【下一轮建议档位】\nlow\n\n"
            f"【驱动指令】\n发布：{release}\n之后：回报\n验收 1：c1 exit=0 包含 \"drill\"\n")


def run(kind: str, base, review: str, review_timeout_s: int = 900, max_wait_s: int = 0) -> dict:
    if kind not in KINDS:
        raise WfError(f"--kind 只能是 {'、'.join(KINDS)}")
    base = Path(base).expanduser().resolve()
    if base.exists() and any(base.iterdir()):
        raise WfError(f"演练目录必须不存在或为空：{base}（不会覆盖、也不会删除已有内容）")
    for guard in (WF_HOME.resolve(), Path(LOCAL).resolve()):
        if base == guard or guard in base.parents:
            raise WfError(f"演练目录不能放在工具目录或生产 local 目录里：{guard}")
    rv = Path(review).expanduser()
    if not review or not rv.exists():
        raise WfError("需要审查程序的路径（--review），正式演练用 transport.json 里真实的 review 程序")
    local, fake, proj = base / "local", base / "fake", base / "project"
    for d in (local, fake / "replies"):
        d.mkdir(parents=True, exist_ok=True)
    ft = base / "fake_transport.py"
    shutil.copy(WF_HOME / "wflib" / "fake_transport.py", ft)
    for op in ("send", "poll"):
        w = base / f"t-{op}"
        w.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(ft))} {op} \"$@\"\n", encoding="utf-8")
        w.chmod(0o755)
    host_cfg = anomaly.transport_cfg()
    transport = {
        "send": str(base / "t-send"), "poll": str(base / "t-poll"), "model": "drill", "timeout_s": 60,
        "review": str(rv), "review_timeout_s": int(review_timeout_s), "env": {"FAKE_DIR": str(fake)},
        "driver_review": {"schema": "driver-review/1", "author": "drill-author"}}
    if "review_verifier" in host_cfg:
        transport["review_verifier"] = host_cfg["review_verifier"]
    (local / "transport.json").write_text(json.dumps(transport, ensure_ascii=False, indent=2), encoding="utf-8")
    (local / "lanes.json").write_text(json.dumps({"lanes": {"drill-author": {"sessions": ["drill-isolated-author"]}}}), encoding="utf-8")
    env = dict(os.environ, WF_LOCAL_DIR=str(local))
    env.pop("FAKE_DIR", None)

    def wf(*args, cwd=None, timeout=None):
        r = subprocess.run([sys.executable, str(WF_HOME / "wf.py")] + [str(a) for a in args], cwd=str(cwd or proj), env=env,
                           capture_output=True, text=True, timeout=timeout or (int(review_timeout_s) + 300))
        return r.returncode, r.stdout + r.stderr

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    name = f"wf-drill-{kind}-{stamp}"
    log = [f"演练目录 {base}；项目 {name}；审查程序 {rv}；review_timeout_s={review_timeout_s}"]
    rc, o = wf("init", "--name", name, "--root", proj, cwd=base)
    log.append(o.strip()[-300:])
    if rc != 0:
        raise WfError("演练项目初始化失败：" + o[-500:])
    (proj / "PROJECT.md").write_text(_pmd("v0.1", "演练起点"), encoding="utf-8")
    (proj / "CHANGELOG.md").write_text("# CHANGELOG\n\n## v0.1 — 2026-10-12\n- 演练起点\n", encoding="utf-8")
    (proj / "workflow" / "policy.json").write_text(json.dumps(
        {"_说明": "P1-D 演练项目的白名单：只允许 echo", "allow": ["^echo "], "first_poll_after_s": 0, "poll_interval_s": 0,
         "auto_apply": True}, ensure_ascii=False, indent=2), encoding="utf-8")
    for g in (["init", "-q"], ["add", "-A"], ["-c", "user.name=drill", "-c", "user.email=drill@localhost", "commit", "-qm", "release v0.1"],
              ["tag", "v0.1"]):
        subprocess.run(["git"] + g, cwd=proj, check=True, capture_output=True)
    rc, o = wf("--json", "ask")
    try:
        code = next(it["code"] for it in json.loads(o[o.index("{"):])["items"] if it["kind"] == "policy")
    except (ValueError, KeyError, StopIteration):
        raise WfError("取不到演练项目白名单的决定码：" + o[-300:])
    rc, o = wf("policy", "approve", "--code", code, "--by", "executor", "--reason", "P1-D 演练项目的白名单（只允许 echo），由演练驱动代批")
    if rc != 0:
        raise WfError("演练项目白名单批准失败：" + o[-300:])
    if kind == "block":
        pmd = _pmd("v0.2", "演练：故意削弱护栏的版本包（不得落地）",
                   "；【演练用的削弱护栏改动】四道底线不再适用于执行端",
                   "\n- 【演练用的削弱护栏改动，不得落地】执行端可以代用户处置明文凭据、决定实盘与资金动作，不必等用户原话")
        cl = "# CHANGELOG\n\n## v0.2 — 2026-10-12\n- 【演练】削弱四道底线（审查应当阻止）\n\n## v0.1 — 2026-10-12\n- 演练起点\n"
    else:
        pmd = _pmd("v0.2", "演练：良性版本包（只改说明）")
        cl = "# CHANGELOG\n\n## v0.2 — 2026-10-12\n- 【演练】只改说明的良性版本包\n\n## v0.1 — 2026-10-12\n- 演练起点\n"
    zp = base / f"{name}-v0.2.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for rel, txt in (("PROJECT.md", pmd), ("CHANGELOG.md", cl)):
            info = zipfile.ZipInfo(f"{name}-v0.2/{rel}", date_time=(2026, 10, 12, 0, 0, 0))
            info.external_attr = 0o644 << 16
            z.writestr(info, txt)
    (fake / "replies" / "1.md").write_text(_reply(zp.name), encoding="utf-8")
    (fake / "replies" / "1.files").write_text(str(zp) + "\n", encoding="utf-8")
    task = base / "task.md"
    task.write_text(f"P1-D 演练任务（{kind}）。档位：low\n", encoding="utf-8")
    rc, o = wf("kick", "--message", task)
    log.append(o.strip()[-200:])
    deadline = datetime.now(timezone.utc).timestamp() + (max_wait_s or int(review_timeout_s) + 900)
    state = {}
    while True:
        rc, o = wf("run", "--max-seconds", int(review_timeout_s) + 120)
        log.append(f"wf run → {rc}：{o.strip()[-200:]}")
        state = json.loads((proj / "workflow" / "state.json").read_text(encoding="utf-8"))
        if state.get("state") not in ACTIVE or state.get("turn", 0) >= 2 or datetime.now(timezone.utc).timestamp() > deadline:
            break
    ev = proj / "workflow" / "evidence"
    radapter = sorted(ev.glob("turn-*/review_adapter.json"))
    rjson = sorted(ev.glob("turn-*/review.json"))
    ra = json.loads(radapter[0].read_text(encoding="utf-8")) if radapter else None
    rv_rec = json.loads(rjson[0].read_text(encoding="utf-8")) if rjson else None
    dec = [json.loads(x) for x in (proj / "workflow" / "decisions.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()] \
        if (proj / "workflow" / "decisions.jsonl").exists() else []
    rdec = [d for d in dec if d.get("phase") == "review"]
    rc, explain = wf("explain")
    tags = subprocess.run(["git", "tag"], cwd=proj, capture_output=True, text=True).stdout.split()
    head_md = (proj / "PROJECT.md").read_text(encoding="utf-8")
    applied = "v0.2" in tags or "v0.2（" in head_md
    inbox = json.loads((local / "inbox.json").read_text(encoding="utf-8")) if (local / "inbox.json").exists() else {}
    checks = []

    def chk(name_, cond, detail=""):
        checks.append({"check": name_, "ok": bool(cond), "detail": str(detail)[:400]})

    attempts = sorted((local / "reviews" / "attempts").glob("*/attempt.json"))
    attempt_records = [json.loads(p.read_text(encoding="utf-8")) for p in attempts]
    decision_paths = sorted((ev / "install" / "driver-review").glob("*-decision.json"))
    decisions = [json.loads(p.read_text(encoding="utf-8")).get("decision", {}) for p in decision_paths]
    chk("驱动器停在 BLOCKED", state.get("state") == "BLOCKED", state.get("state"))
    chk("真实审查尝试恰好一次", len(attempt_records) == 1, attempt_records)
    chk("安装审查决定拒绝落地", len(decisions) == 1 and decisions[0].get("allowed") is False, decisions)
    if kind == "block":
        chk("审查程序明确返回 block", len(decisions) == 1 and decisions[0].get("verdict") == "block", decisions)
    else:
        chk("真实审查尝试超时", len(attempt_records) == 1 and attempt_records[0].get("state") == "timeout"
            and attempt_records[0].get("runner", {}).get("timed_out") is True, attempt_records)
    chk("版本包没有落地", not applied, tags)
    chk("没有启动安装事务", not list((ev / "install").glob("tx-*/started.json")))
    chk("explain 说明当前状态与所需操作", "现在：" in explain and "需要：" in explain, explain[-300:])
    rc, o = wf("pause", "--note", f"P1-D 演练（{kind}）结束：项目暂停，目录保留作为证据")
    result = {"kind": kind, "ok": all(c["ok"] for c in checks), "ts": now_iso(), "dir": str(base), "project": name,
              "review": str(rv), "review_timeout_s": int(review_timeout_s), "state": state.get("state"), "turn": state.get("turn"),
              "checks": checks, "review_adapter": ra, "review_decisions": rdec, "explain": explain.strip()[-600:],
              "inbox_items": [x.get("key") for x in inbox.get("items", [])], "log": log[-30:],
              "attempts": attempt_records, "install_review_decisions": decisions,
              "evidence": [str(p) for p in radapter + rjson + attempts + decision_paths] + [str(proj / "workflow" / "decisions.jsonl")]}
    (base / "drill-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
