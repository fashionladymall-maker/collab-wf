"""目标与完成判据。
workflow/goals.json 示例：
{"goals": [
  {"id": "G1", "title": "模拟盘实现与回测逐根一致", "kind": "exec",
   "check": "python reconcile.py", "pass": {"exit": 0, "contains": ["实现一致"]},
   "means": "实现正确", "on_fail": "继续迭代"},
  {"id": "G2", "title": "交易平台合规可用", "kind": "manual", "means": "可以进入实盘评审", "on_fail": "暂停实盘评审"}
]}
- exec 类：由驱动器在验收后运行 check 命令，按 pass 条件判定；输出存为证据。
- manual 类：只能由用户用 wf goals confirm 确认；确认绑定在当时的目标内容上，目标一改动确认即失效。
- 目标只有在用户批准后才生效（wf goals approve）；未批准时可以显示，但不能据此判定完成。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import policy
from .core import WF_HOME, Instance, WfError, now_iso, read_json, write_json


def load(inst: Instance):
    return read_json(inst.wf / "goals.json")


def validate(g: dict) -> list:
    issues = []
    if not isinstance(g, dict) or not isinstance(g.get("goals"), list) or not g["goals"]:
        return ["goals.json 需要非空的 goals 列表"]
    ids = set()
    for x in g["goals"]:
        gid = x.get("id")
        if not gid or gid in ids:
            issues.append(f"目标编号缺失或重复：{gid}")
        ids.add(gid)
        if x.get("kind") not in ("exec", "manual"):
            issues.append(f"{gid}：kind 只能是 exec 或 manual")
        if x.get("kind") == "exec":
            if not x.get("check"):
                issues.append(f"{gid}：exec 类目标需要 check 命令")
            elif policy.GUARDRAIL.search(x["check"]):
                issues.append(f"{gid}：check 命令不能是护栏命令")
            if not isinstance(x.get("pass"), dict):
                issues.append(f"{gid}：需要 pass 条件（exit / contains / not_contains / regex）")
        for k in ("title", "means", "on_fail"):
            if not x.get(k):
                issues.append(f"{gid}：缺少 {k}")
    ph = g.get("phase")   # 1.8.2（P0-B）：当前阶段与历史阶段
    if ph is not None and (not isinstance(ph, dict) or not str(ph.get("id") or "").strip()
                           or not str(ph.get("title") or "").strip()):
        issues.append("phase（当前阶段）需要 id 与 title")
    hist = g.get("history")
    if hist is not None:
        if not isinstance(hist, list) or not all(isinstance(h, dict) and h.get("id") and h.get("title") for h in hist):
            issues.append("history 是历史阶段的列表，每项需要 id 与 title")
        elif isinstance(ph, dict) and any(h.get("id") == ph.get("id") for h in hist):
            issues.append("当前阶段的编号不能与历史阶段重复")
    return issues


def judge(cond: dict, exit_code: int, out: str) -> tuple:
    """按条件判定命令结果。返回 (是否通过, 不通过的原因列表)。验收与目标共用。"""
    why = []
    if "exit" in cond and exit_code != cond["exit"]:
        why.append(f"退出码 {exit_code}，期望 {cond['exit']}")
    for s in cond.get("contains", []):
        if s not in out:
            why.append(f"输出不含“{s}”")
    for s in cond.get("not_contains", []):
        if s in out:
            why.append(f"输出含有“{s}”")
    for r in cond.get("regex", []):
        if not re.search(r, out, re.M):
            why.append(f"输出不匹配 /{r}/")
    return not why, why


def _confirmations(inst: Instance) -> dict:
    return (policy.approvals(inst).get("confirmations") or {})


def confirm(inst: Instance, gid: str, note: str) -> dict:
    g = load(inst)
    if not g or gid not in {x["id"] for x in g["goals"]}:
        raise WfError(f"没有目标 {gid}")
    x = next(x for x in g["goals"] if x["id"] == gid)
    if x["kind"] != "manual":
        raise WfError(f"{gid} 是可执行判据，由检查命令判定，不能人工确认")
    st = policy.status(inst, "goals")
    if not st["approved"]:
        raise WfError("目标尚未批准或批准后被改动，先 wf goals approve")
    a = policy.approvals(inst)
    a.setdefault("confirmations", {})[gid] = {"ts": now_iso(), "note": note, "goals_sha": st["current"]}
    write_json(policy.approvals_path(inst), a)
    inst.event("goal_confirm", goal=gid, note=note, by="user")
    return a["confirmations"][gid]


def run_checks(inst: Instance, timeout=900, by="driver") -> dict:
    """运行全部 exec 类目标的检查命令，结果写入 workflow/goals_state.json 并留存证据。"""
    g = load(inst)
    st = policy.status(inst, "goals")
    if not g or not st["approved"]:
        return progress(inst)
    d = inst.evidence / "goals"
    d.mkdir(parents=True, exist_ok=True)
    gs = read_json(inst.wf / "goals_state.json", {}) or {}
    env = dict(os.environ)
    env["PATH"] = str(WF_HOME / "bin") + os.pathsep + env.get("PATH", "")
    venv = inst.root / ".venv"
    if (venv / "bin").exists():
        env["VIRTUAL_ENV"] = str(venv)
        env["PATH"] = str(venv / "bin") + os.pathsep + env["PATH"]
    for x in g["goals"]:
        if x["kind"] != "exec":
            continue
        stamp = time.strftime("%Y%m%d-%H%M%S")
        t0 = time.time()
        try:
            r = subprocess.run(["bash", "-c", x["check"]], cwd=str(inst.root), capture_output=True, text=True,
                               timeout=timeout, env=env)
            code, out = r.returncode, r.stdout + r.stderr
        except subprocess.TimeoutExpired as e:
            code, out = 124, (e.stdout or "") if isinstance(e.stdout, str) else ""
        ok, why = judge(x["pass"], code, out)
        ev = d / f"{stamp}-{x['id']}.txt"
        ev.write_text(f"$ {x['check']}\nexit={code}\n{out}", encoding="utf-8")
        gs[x["id"]] = {"ok": ok, "why": why, "ts": now_iso(), "exit": code, "evidence": str(ev.relative_to(inst.root)),
                       "duration_s": round(time.time() - t0, 1), "goals_sha": st["current"]}
        inst.event("goal_check", goal=x["id"], ok=ok, why="；".join(why) or None, by=by)
    write_json(inst.wf / "goals_state.json", gs)
    return progress(inst)


def progress(inst: Instance) -> dict:
    g = load(inst)
    st = policy.status(inst, "goals")
    if not g:
        return {"defined": False, "approved": False, "items": [], "done": 0, "total": 0, "reached": False}
    gs = read_json(inst.wf / "goals_state.json", {}) or {}
    conf = _confirmations(inst)
    items = []
    for x in g["goals"]:
        if x["kind"] == "exec":
            r = gs.get(x["id"]) or {}
            valid = r.get("goals_sha") == st["current"]
            ok = bool(r.get("ok")) and valid
            detail = ("通过" if ok else ("；".join(r.get("why", [])) or "未通过")) if valid and r else "尚未检查"
        else:
            c = conf.get(x["id"]) or {}
            ok = c.get("goals_sha") == st["current"] and st["approved"]
            detail = f"用户已确认：{c.get('note')}" if ok else "等待用户确认"
        items.append({"id": x["id"], "title": x["title"], "kind": x["kind"], "ok": ok, "detail": detail,
                      "means": x.get("means"), "on_fail": x.get("on_fail")})
    done = sum(1 for i in items if i["ok"])
    return {"defined": True, "approved": st["approved"], "changed_since_approval": st["changed_since_approval"],
            "items": items, "done": done, "total": len(items), "reached": st["approved"] and done == len(items),
            "issues": validate(g), "phase": _phase(g), "history": _history(g)}


def _phase(g: dict):
    ph = g.get("phase") if isinstance(g, dict) else None
    if not isinstance(ph, dict) or not ph.get("id"):
        return None
    return {k: ph.get(k) for k in ("id", "title", "since", "supersedes", "requirements", "scope") if ph.get(k) is not None}


def _history(g: dict) -> list:
    out = []
    for h in ((g.get("history") or []) if isinstance(g, dict) else []):
        if isinstance(h, dict) and h.get("id"):
            out.append({k: h.get(k) for k in ("id", "title", "status", "closed", "summary") if h.get(k) is not None})
    return out


def phase_title(prog: dict) -> str:
    """1.8.2（P0-B）：当前阶段的标题行。旧格式（没有 phase）照实说明，不把这份目标当成之后新范围的进度。"""
    ph = prog.get("phase")
    if ph:
        return (f"当前阶段 {ph['id']} {ph.get('title') or ''}".rstrip()
                + (f"（取代 {ph['supersedes']}）" if ph.get("supersedes") else ""))
    return "当前阶段：未声明（旧格式的目标文件；进度只代表这份文件里的目标，不代表之后的新范围）"


def history_lines(prog: dict) -> list:
    cn = {"done": "已完成", "superseded": "已被取代", "cancelled": "已取消", "closed": "已结束"}
    return [f"历史阶段 {h['id']} {h.get('title') or ''}：{cn.get(h.get('status'), h.get('status') or '已结束')}"
            + (f"（{h['closed']}）" if h.get("closed") else "") + (f"；{h['summary']}" if h.get("summary") else "")
            for h in prog.get("history") or []]
