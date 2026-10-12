"""异常字典的匹配，以及传输适配器的调用（wf 不访问通道，只调用执行端提供的适配器程序）。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .core import LOCAL, WF_HOME, Instance, WfError, read_json


# ---------------- 异常字典 ----------------

def rules(inst: Instance) -> list:
    base = (read_json(WF_HOME / "anomalies.json", {}) or {}).get("rules", [])
    proj = (read_json(inst.wf / "anomalies.json", {}) or {}).get("rules", []) if inst else []
    return proj + base


def match(inst: Instance, facts: dict) -> dict:
    """facts 例：{"source": "transport", "op": "send", "code": 2, "text": "…"}。返回第一条匹配的规则。"""
    for r in rules(inst):
        ok = True
        for k, v in r.get("match", {}).items():
            if k == "text":
                if not re.search(v, str(facts.get("text", "")), re.I):
                    ok = False
                    break
            elif facts.get(k) != v:
                ok = False
                break
        if ok:
            return r
    return {"id": "UNKNOWN", "action": "block", "why": "没有匹配的规则"}


# ---------------- 传输适配器 ----------------
# local/transport.json：
# {"send": "/path/send", "poll": "/path/poll", "quota": "/path/quota", "timeout_s": 120,
#  "model": "claude-opus-5-5", "env": {"CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR": "/Users/x/tmp"}}
# 约定见 docs/TRANSPORT.md。

def transport_cfg() -> dict:
    return read_json(LOCAL / "transport.json", {}) or {}


def adapter_location() -> dict:
    """适配器在哪里，以及是否违反“通道适配器放在工具目录之外”的边界。"""
    cfg = transport_cfg()
    mod = cfg.get("module")
    if not mod:
        return {"mode": "executables" if cfg.get("send") else "none", "inside_tool": False}
    mpath = Path(os.path.expanduser(cfg.get("module_path") or ""))
    rel = Path(*mod.split("."))
    cands = []
    if str(mpath) not in ("", "."):
        cands += [mpath / f"{rel}.py", mpath / rel / "__init__.py"]
    cands += [WF_HOME / f"{rel}.py", WF_HOME / rel / "__init__.py"]
    found = next((c for c in cands if c.exists()), None)
    inside = bool(found and str(found.resolve()).startswith(str(WF_HOME.resolve()) + os.sep))
    allowed = mod == "adapters.example_echo"
    return {"mode": "module", "module": mod, "file": str(found) if found else None,
            "inside_tool": inside and not allowed, "example": allowed}


def driver_env() -> dict:
    env = dict(os.environ)
    env.update({k: str(v) for k, v in (transport_cfg().get("env") or {}).items()})
    env["PATH"] = str(WF_HOME / "bin") + os.pathsep + env.get("PATH", "")
    return env


# 1.6.2：这些动作不属于对话通道，可以在 transport.json 里单独写程序路径（写 true 时仍按模块方式的子命令调用）
PROGRAM_OPS = ("sync", "review")


def conv_args(inst) -> list:
    """1.7.0（ISOLATION.md S1）：transport.json 声明 per_project_conv 时，把本项目登记的对话（state.conv.conv）交给适配器。
    没声明时返回空列表：旧适配器不认识 --conv，传了会被当成错误。"""
    if not transport_cfg().get("per_project_conv"):
        return []
    cid = (inst.state.get("conv") or {}).get("conv")
    return ["--conv", str(cid)] if cid else []


def review_key_args(project: str, turn, zip_path) -> list:
    """1.7.3（ISOLATION.md S2，执行端第 93 轮意见）：transport.json 声明 review_key 时，由工具给审查程序显式传键，
    审查程序不再从版本包文件名反推（两个项目同名的版本包会撞）。同一轮同一个包重审时键相同。"""
    if not transport_cfg().get("review_key"):
        return []
    stem = Path(str(zip_path)).name
    stem = stem[:-4] if stem.endswith(".zip") else stem
    return ["--key", f"{project}.tool.{stem}" if str(turn) == "tool" else f"{project}.t{turn}.{stem}"]


def conv_new_args(inst) -> list:
    """1.7.0（ISOLATION.md S1）：声明 per_project_conv 时，conv_new 带上项目名，适配器只改该项目的绑定、不改默认对话。"""
    return ["--project", inst.name] if transport_cfg().get("per_project_conv") else []


def call(op: str, args: list, timeout=None, env_extra=None) -> dict:
    """调用适配器（send/poll/quota 三个可执行程序之一）。返回 {"code": 退出码, "data": JSON 或 {}, "raw": 原始输出}。
    适配器缺失、超时、输出不是 JSON 时，kind 分别为 not_configured / timeout / bad_output（由异常字典处置）。"""
    cfg = transport_cfg()
    env = driver_env()
    mod = cfg.get("module")
    prog = cfg.get(op)
    if mod and op in PROGRAM_OPS and isinstance(prog, str) and prog.strip():
        # 1.6.2（第 36 轮）：sync、review 在 transport.json 里写了程序路径时，即使通道是模块方式也直接运行该程序；
        # 此前一律按模块方式调用，而通道适配器的动作表只有 send/poll/quota，wf sync 必然报“未知动作：sync”
        mod = None
    if mod:
        # 模块方式：python -m <模块>；通道适配器放在工具目录之外（module_path），工具目录只提供 adapters.base 接口
        mpath = os.path.expanduser(cfg.get("module_path") or "")
        env["PYTHONPATH"] = os.pathsep.join(x for x in (mpath, str(WF_HOME), env.get("PYTHONPATH", "")) if x)
        env["WF_TRANSPORT_JSON"] = str(LOCAL / "transport.json")
        cmd = [sys.executable, "-m", mod, op] + [str(x) for x in args]
    else:
        prog = cfg.get(op)
        if not prog:
            return {"code": -1, "data": {}, "raw": f"未配置 {op} 适配器", "kind": "not_configured"}
        cmd = [prog] + [str(x) for x in args]
    t = timeout or cfg.get("timeout_s", 120)
    env["WF_ADAPTER_TIMEOUT_S"] = str(int(t))   # 1.5.1：告诉适配器时间上限，约定它在上限前 30 秒自行返回
    env.update({k: str(v) for k, v in (env_extra or {}).items()})   # 1.5.3：例如 WF_PAGE_SNAPSHOT（docs/SNAPSHOT.md）
    from . import fidelity
    try:
        rc, so, se, timed_out = fidelity.run_with_grace(cmd, t, env, grace=int(cfg.get("grace_s", 15)))
    except FileNotFoundError:
        return {"code": -1, "data": {}, "raw": f"适配器不存在：{cmd[0]}", "kind": "not_configured"}
    if timed_out:
        # 1.5.1：先 SIGTERM、宽限期后才 SIGKILL，适配器有机会补写日志（1.4.x 直接 SIGKILL，日志断在半空）
        return {"code": 4, "data": {}, "raw": f"适配器 {op} 超过 {t} 秒未返回（已先发 SIGTERM）", "kind": "timeout"}
    r = subprocess.CompletedProcess(cmd, rc, so, se)
    raw = (r.stdout or "").strip()
    last = raw.splitlines()[-1] if raw else ""
    try:
        data = json.loads(last) if last else {}
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        return {"code": r.returncode, "data": {}, "raw": raw + (r.stderr or ""), "kind": "bad_output"}
    return {"code": r.returncode, "data": data, "raw": raw[-2000:] + (r.stderr or "")[-1000:]}
