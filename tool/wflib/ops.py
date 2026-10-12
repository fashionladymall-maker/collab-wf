"""日常操作：记账（发送/收到）、执行取证、解析回复、执行前评审、验收、回报草稿、发送前检查。"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import fidelity, machine
from .core import Instance, WfError, append_jsonl, now_iso, read_json, read_jsonl, secs_since, write_json

BLOCKS = ["本轮结论", "本机执行", "本轮回报清单", "需要用户决定", "下一轮建议档位"]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]


# ---------------- 记账：发送与收到 ----------------

def _conv_add(inst: Instance, chars: int):
    c = inst.state.setdefault("conv", {"since_turn": 1, "since": now_iso(), "chars": 0, "turns": 0})
    c["chars"] = int(c.get("chars", 0)) + int(chars)


def mark_sent(inst: Instance, message_file=None, kind=None, effort=None, model=None,
              usage_session=None, usage_weekly=None, by="executor", role=None, reasons=None, ref=None) -> dict:
    st = inst.state
    if st["state"] == "COMPOSE":
        if message_file:
            res = check_outgoing(Path(message_file), kind or "task")
            if res["secrets"]:
                raise WfError("消息中疑似含有密钥，禁止记为已发送（CP-1）：\n  " + "\n  ".join(res["secrets"]))
        machine.go(inst, "SEND", by=by)
    if inst.state["state"] != "SEND":
        raise WfError(f"当前是 {inst.state['state']}，只有 COMPOSE 或 SEND 状态可以记为已发送。")
    usage = {}
    if usage_session is not None:
        usage["session"] = usage_session
    if usage_weekly is not None:
        usage["weekly"] = usage_weekly
    usage = usage or inst.usage_snapshot()
    turn = inst.state["turn"]
    text = Path(message_file).read_text(encoding="utf-8") if message_file else None
    if text is not None:
        append_jsonl(inst.transcript_path, {"ts": now_iso(), "role": "executor", "turn": turn, "text": text})
        d = inst.turn_dir()
        (d / "message.md").write_text(text, encoding="utf-8")
    append_jsonl(inst.turns_path, {"turn": turn, "ts_sent": now_iso(), "from": by, "kind": kind, "role": role,
                                   "effort": effort, "model": model, "effort_reasons": reasons,
                                   "usage_before": usage, "status": "sent",
                                   **({} if usage else {"usage_note": fidelity.usage_reason(inst.usage_path)})})
    inst.state["sent_at"] = now_iso()
    if ref is not None:
        inst.state["send_ref"] = ref   # 手动发送时登记 ref，驱动器据此可接续本轮（缺口 7）
    _conv_add(inst, len(text or ""))
    inst.state["conv"]["turns"] = int(inst.state["conv"].get("turns", 0)) + 1
    machine.go(inst, "WAIT_REPLY", by=by)
    return inst.state


def mark_replied(inst: Instance, reply_file=None, tools=None, usage_session=None, usage_weekly=None,
                 status="done", by="executor") -> dict:
    st = inst.state
    if st["state"] != "WAIT_REPLY":
        raise WfError(f"当前是 {st['state']}，只有 WAIT_REPLY 状态可以记为已收到回复。")
    usage = {}
    if usage_session is not None:
        usage["session"] = usage_session
    if usage_weekly is not None:
        usage["weekly"] = usage_weekly
    usage = usage or inst.usage_snapshot()
    dur = secs_since(st.get("sent_at"))
    turn = st["turn"]
    if reply_file:
        text = Path(reply_file).read_text(encoding="utf-8")
        rec = {"ts": now_iso(), "role": "claude", "turn": turn, "text": text}
        if tools:
            rec["tools"] = tools
        append_jsonl(inst.transcript_path, rec)
        (inst.turn_dir() / "reply.md").write_text(text, encoding="utf-8")
        _conv_add(inst, len(text))
    append_jsonl(inst.turns_path, {"turn": turn, "ts_done": now_iso(), "usage_after": usage,
                                   "duration_s": int(dur) if dur is not None else None, "status": status,
                                   **({} if usage else {"usage_note": fidelity.usage_reason(inst.usage_path)})})
    machine.go(inst, "INTAKE", by=by)
    return inst.state


# ---------------- 执行与取证 ----------------

def run_exec(inst: Instance, cmd: str, label=None, timeout=None, by="executor") -> int:
    st = inst.state
    if st["state"] in ("REVIEW", "APPLY"):
        if not st.get("turn_flags", {}).get("review_accepted"):
            raise WfError("本轮评审尚未接受。先运行 wf review，确认后 wf review --accept。")
        machine.go(inst, "EXECUTE", by=by)
    elif st["state"] != "EXECUTE":
        raise WfError(f"当前是 {st['state']}（{machine.sdef(st['state'])['name']}），不在执行阶段。"
                      "需要在本步之外运行命令时，直接在终端执行即可，不会记入证据。")
    d = inst.turn_dir()
    n = len(list(d.glob("c*.json"))) + 1
    cid = f"c{n:02d}"
    out_p, err_p = d / f"{cid}.out", d / f"{cid}.err"
    env = dict(os.environ)
    from .core import WF_HOME
    env["PATH"] = str(WF_HOME / "bin") + os.pathsep + env.get("PATH", "")   # 子 shell 不读 ~/.zshrc，这里保证 wf 可用
    venv = inst.root / ".venv"
    if (venv / "bin").exists():
        env["VIRTUAL_ENV"] = str(venv)
        env["PATH"] = str(venv / "bin") + os.pathsep + env.get("PATH", "")
    meta = {"id": cid, "cmd": cmd, "label": label, "cwd": str(inst.root), "start": now_iso()}
    write_json(d / f"{cid}.json", meta)
    print(f"[wf exec {cid}] $ {cmd}", flush=True)
    t0 = time.time()
    interrupted = False
    with open(out_p, "wb") as fo, open(err_p, "wb") as fe:
        # 输出直接写文件（不用管道），后台启动的子进程不会让这里卡住
        p = subprocess.Popen(["bash", "-c", cmd], cwd=str(inst.root), stdout=fo, stderr=fe, env=env)
        pos = [0, 0]

        def pump():
            for i, (path, stream) in enumerate(((out_p, sys.stdout), (err_p, sys.stderr))):
                with open(path, "rb") as f:
                    f.seek(pos[i])
                    chunk = f.read()
                    pos[i] += len(chunk)
                if chunk:
                    stream.write(chunk.decode("utf-8", "replace"))
                    stream.flush()
        try:
            while True:
                try:
                    rc = p.wait(timeout=0.3)
                    break
                except subprocess.TimeoutExpired:
                    pump()
                    if timeout and time.time() - t0 > timeout:
                        p.send_signal(signal.SIGINT)
                        try:
                            rc = p.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            p.kill()
                            rc = p.wait()
                        interrupted = True
                        break
        except KeyboardInterrupt:
            interrupted = True
            try:
                rc = p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
                rc = p.wait()
        pump()
    meta.update(end=now_iso(), duration_s=round(time.time() - t0, 1), exit_code=rc,
                interrupted=interrupted or None, out_bytes=out_p.stat().st_size, err_bytes=err_p.stat().st_size)
    write_json(d / f"{cid}.json", meta)
    inst.event("exec", cid=cid, cmd=cmd[:300], exit_code=rc, duration_s=meta["duration_s"],
               interrupted=interrupted or None, by=by)
    print(f"[wf exec {cid}] exit={rc}  耗时 {meta['duration_s']}s  证据：{d.relative_to(inst.root)}/{cid}.*", flush=True)
    return rc


def list_execs(inst: Instance, turn=None) -> list:
    d = inst.turn_dir(turn)
    return [read_json(p) for p in sorted(d.glob("c*.json"))]


# ---------------- 解析回复 ----------------

def parse_directives(body: str) -> dict:
    """解析【驱动指令】块。每行“键：值”，键为 发布 / 之后 / 验收 N / 执行端 N / 指纹 / 超时 / 失败后继续。"""
    d = {"release": None, "after": None, "until": None, "wait_reason": None, "checks": {}, "fingerprint": None,
         "timeout": None, "continue_on_error": False, "errors": [], "executor_tasks": {}}
    for raw in body.splitlines():
        line = fidelity.directive_line(raw)   # 1.5.0：容忍行首编号、加粗与“本轮之后”
        m = re.match(r"^(发布|之后|验收\s*\d+|执行端\s*\d+|指纹|超时|失败后继续)\s*[:：]\s*(.*)$", line)
        if not m:
            continue
        d["_keys"] = d.get("_keys", 0) + 1
        key, val = re.sub(r"\s+", "", m.group(1)), m.group(2).strip()
        if key == "发布":
            d["release"] = None if val in ("无", "") else val.split()[0]
        elif key == "之后":
            if val.startswith("等待"):
                mm = re.match(r"^等待\s+(\S+)\s*(.*)$", val)
                if mm:
                    d.update(after="wait", until=mm.group(1), wait_reason=mm.group(2) or "计划节点")
                else:
                    d["errors"].append(f"无法解析：{line}")
            elif val.startswith("回报"):
                d["after"] = "report"
            elif val.startswith("空闲"):
                d["after"] = "idle"
            elif val.startswith("等执行端"):   # 1.6.6：等执行端待办做完，自动回报
                d["after"] = "executor"
            else:
                d["errors"].append(f"无法解析：{line}")
        elif key.startswith("执行端"):   # 1.6.6：写给执行端的待办（第 58 轮：正文里的事做完了没人接）
            n = int(re.sub(r"\D", "", key))
            if val:
                d["executor_tasks"][str(n)] = val
            else:
                d["errors"].append(f"执行端 {n} 没有写事项：{line}")
        elif key.startswith("验收"):
            n = int(re.sub(r"\D", "", key))
            cm = re.search(r"\bc(\d+)\b", val)
            cond = {}
            em = re.search(r"exit\s*=\s*(\d+)", val)
            if em:
                cond["exit"] = int(em.group(1))
            for word, k in (("包含", "contains"), ("不含", "not_contains"), ("匹配", "regex")):
                vals = re.findall(word + r'\s*["“](.*?)["”]', val)
                if vals:
                    cond[k] = vals
            if not cm or not cond:
                d["errors"].append(f"验收 {n} 缺少命令编号或条件：{line}")
            else:
                d["checks"][str(n)] = {"cmd": int(cm.group(1)), "cond": cond}
        elif key == "指纹":
            d["fingerprint"] = val.split()[0] if val else None
        elif key == "超时":
            try:
                d["timeout"] = float(re.sub(r"[^\d.]", "", val))
            except ValueError:
                d["errors"].append(f"无法解析：{line}")
        elif key == "失败后继续":
            d["continue_on_error"] = val.startswith(("是", "yes", "true"))
    if body.strip() and not d.get("_keys"):
        d["errors"].append("驱动指令块有内容，但没有解析出任何键：每行应为“键：值”，键为 发布、之后、验收 N、指纹、超时、失败后继续")
    d.pop("_keys", None)
    return d


BLOCK_ALIASES = {"结论": "本轮结论", "本轮小结": "本轮结论", "回报清单": "本轮回报清单", "需要你决定": "需要用户决定",
                 "用户决定": "需要用户决定", "建议档位": "下一轮建议档位", "下一轮档位": "下一轮建议档位"}


def _alias_blocks(text: str) -> tuple:
    """1.7.6：行首的块标题写成常见别名（例如【结论】）而规定的块名不在行首时，按规定的块名识别，并给出提示。"""
    warnings = []
    for a, canon in BLOCK_ALIASES.items():
        if re.search(r"^[ \t>#*\-•]*【" + re.escape(canon) + "】", text, re.M):
            continue
        pat = re.compile(r"^([ \t>#*\-•]*)【" + re.escape(a) + "】", re.M)
        if pat.search(text):
            text = pat.sub(lambda m, c=canon: m.group(1) + "【" + c + "】", text, count=1)
            warnings.append(f"块标题「{a}」按别名识别为「{canon}」；请用规定的块名")
    return text, warnings


def parse_reply(text: str) -> dict:
    # 1.5.0：块标题须位于行首，正文中顺带提到的块名不再顶掉真正的块（fidelity.split_blocks）
    text, alias_w = _alias_blocks(text)   # 1.7.6
    blocks, warnings = fidelity.split_blocks(text, BLOCKS + ["驱动指令"])
    warnings = alias_w + warnings
    cmds = []
    for code in re.findall(r"```(?:bash|sh|shell|zsh)?\n(.*?)```", blocks.get("本机执行", ""), re.S):
        buf = ""
        for line in code.splitlines():
            s = line.rstrip()
            if not s.strip() or s.strip().startswith("#"):
                continue
            if s.endswith("\\"):
                buf += s[:-1] + " "
                continue
            cmds.append((buf + s).strip())
            buf = ""
    numbered = lambda b: [m.group(2).strip() for m in re.finditer(r"^\s*(\d+)[.、)]\s*(.+)$", b, re.M)]
    eff = None
    m = re.search(r"\b(" + "|".join(EFFORTS) + r")\b", blocks.get("下一轮建议档位", ""), re.I)
    if m:
        eff = m.group(1).lower()
    zips = sorted(set(re.findall(r"[\w.\-]+-v\d+(?:\.\d+)+\.zip", text)))
    return {
        "blocks_found": [b for b in BLOCKS if b in blocks],
        "blocks_missing": [b for b in BLOCKS if b not in blocks],
        "commands": cmds,
        "checklist": numbered(blocks.get("本轮回报清单", "")),
        "user_decisions": numbered(blocks.get("需要用户决定", "")),
        "effort": eff,
        "zips": zips,
        "no_exec": bool(re.match(r"^\s*无", blocks.get("本机执行", ""))),
        "directives": parse_directives(blocks.get("驱动指令", "")) if "驱动指令" in blocks else None,
        "blocks": blocks,
        "warnings": warnings + fidelity.list_warnings(blocks.get("本轮回报清单", ""), "本轮回报清单")
                    + fidelity.list_warnings(blocks.get("需要用户决定", ""), "需要用户决定"),
    }


def intake(inst: Instance, reply_file: Path, by="executor") -> dict:
    st = inst.state
    if st["state"] != "INTAKE":
        raise WfError(f"当前是 {st['state']}，intake 需要在 INTAKE 状态运行（先 wf mark replied）。")
    res = parse_reply(Path(reply_file).read_text(encoding="utf-8"))
    write_json(inst.turn_dir() / "intake.json", res)
    inst.event("intake", missing=res["blocks_missing"] or None, commands=len(res["commands"]),
               checklist=len(res["checklist"]), effort=res["effort"], by=by)
    if not res["blocks_missing"]:
        machine.go(inst, "REVIEW", by="wf", reason="固定块齐全")
    return res


# ---------------- 执行前评审 ----------------

RISK_RULES = [
    ("需用户批准", r"--confirm-real-money|mode:\s*live|live\.mode|\.env\b|API_?KEY|API_?SECRET|withdraw|transfer|提现|转账",
     "涉及资金、密钥或实盘"),
    ("谨慎", r"(^|[\s;&|])rm\s|rmdir|git\s+(reset\s+--hard|push\b.*--force|clean\s+-[a-z]*f|rebase|filter-branch)|shutil\.rmtree",
     "删除文件或改写 git 历史"),
    ("谨慎", r"stop_paper|start_paper|kill\s|pkill|killall|launchctl|reboot|shutdown", "启停进程"),
    ("谨慎", r"pip\s+install|npm\s+install|brew\s+install|apt(-get)?\s+install", "安装软件包"),
    ("谨慎", r"(^|\s)sudo\s", "提权执行"),
]


def classify(cmd: str):
    hits = []
    for level, pat, why in RISK_RULES:
        if re.search(pat, cmd, re.I):
            hits.append((level, why))
    if any(h[0] == "需用户批准" for h in hits):
        return "需用户批准", [h[1] for h in hits]
    if hits:
        return "谨慎", [h[1] for h in hits]
    return "一般", []


def review(inst: Instance, accept=False, user_approved=None, by="executor") -> dict:
    st = inst.state
    if st["state"] != "REVIEW":
        raise WfError(f"当前是 {st['state']}，评审需要在 REVIEW 状态进行。")
    it = read_json(inst.turn_dir() / "intake.json", {}) or {}
    rows = []
    for c in it.get("commands", []):
        level, why = classify(c)
        rows.append({"cmd": c, "level": level, "why": why})
    need_user = [r for r in rows if r["level"] == "需用户批准"]
    res = {"rows": rows, "need_user": len(need_user), "caution": sum(r["level"] == "谨慎" for r in rows),
           "decisions": it.get("user_decisions", []), "accepted": False, "user_approved": user_approved}
    if accept:
        if need_user and not user_approved:
            write_json(inst.turn_dir() / "review.json", res)
            machine.go(inst, "AWAITING_USER", by="wf", reason=f"{len(need_user)} 条命令需要用户批准（RV-1）")
            res["moved_to"] = "AWAITING_USER"
            return res
        res["accepted"] = True
        st.setdefault("turn_flags", {})["review_accepted"] = True
        inst.event("review", accepted=True, need_user=len(need_user) or None, user_approved=user_approved, by=by)
    write_json(inst.turn_dir() / "review.json", res)
    return res


# ---------------- 验收与备注 ----------------

VERDICTS = {"pass": "通过", "fail": "未通过", "deviation": "有偏离", "skip": "暂缓"}


def verify_items(inst: Instance) -> list:
    it = read_json(inst.turn_dir() / "intake.json", {}) or {}
    v = read_json(inst.turn_dir() / "verify.json", {}) or {}
    return [{"n": i + 1, "item": t, **(v.get(str(i + 1)) or {})} for i, t in enumerate(it.get("checklist", []))]


def verify_set(inst: Instance, n: int, verdict: str, evidence=None, note=None, by="executor") -> dict:
    if verdict not in VERDICTS:
        raise WfError(f"结论只能是：{'、'.join(VERDICTS)}")
    st = inst.state
    post_hoc = False
    if st["state"] == "EXECUTE":
        machine.go(inst, "VERIFY", by=by)
    elif st["state"] in ("WAIT_SCHEDULE", "IDLE"):
        post_hoc = True   # 有些清单项只能在转换之后观察（例如"进入等待后看板的状态"），允许事后补记
    elif st["state"] != "VERIFY":
        raise WfError(f"当前是 {st['state']}，验收需要在 EXECUTE 之后进行（等待计划节点或空闲时可以事后补记）。")
    items = verify_items(inst)
    if not 1 <= n <= max(len(items), 0):
        raise WfError(f"序号 {n} 不在回报清单范围内（共 {len(items)} 项）。用 wf verify list 查看。")
    p = inst.turn_dir() / "verify.json"
    v = read_json(p, {}) or {}
    v[str(n)] = {"verdict": verdict, "evidence": evidence, "note": note, "ts": now_iso(), "post_hoc": post_hoc or None}
    write_json(p, v)
    inst.event("verify", item=n, verdict=verdict, evidence=evidence, post_hoc=post_hoc or None, by=by)
    return v[str(n)]


def add_note(inst: Instance, kind: str, text: str, by="executor") -> None:
    if kind not in ("deviation", "decision", "info"):
        raise WfError("kind 只能是 deviation（偏离）、decision（需要决策）、info（说明）")
    append_jsonl(inst.turn_dir() / "notes.jsonl", {"ts": now_iso(), "kind": kind, "text": text})
    inst.event("note", kind=kind, text=text[:300], by=by)
    if by == "executor" and inst.state.get("state") in ("COMPOSE", "SEND", "WAIT_REPLY", "BLOCKED", "AWAITING_USER",
                                                        "QUOTA_WAIT", "WAIT_SCHEDULE", "IDLE"):
        # 1.5.1：本轮回报已经生成，这条备注会随下一份回报发出。明确说出来，免得以为丢了（第 41 轮）
        print(f"提示：当前是 {inst.state.get('state')}，本轮回报已生成；这条备注记在第 {inst.state.get('turn')} 轮，"
              "会随下一份回报发出。", file=sys.stderr)


# ---------------- 回报草稿 ----------------

def _tail(p: Path, lines=60) -> str:
    if not p.exists():
        return ""
    t = p.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(t) > lines:
        return f"……（前 {len(t) - lines} 行省略，全文见 {p.name}）\n" + "\n".join(t[-lines:])
    return "\n".join(t)


def attention(inst: Instance, turn: int) -> list:
    """本轮需要用户与 Claude 注意的事项：自动处置、自动回退、无机器判据的清单项、待批准内容、上下文体量、独立审查意见。"""
    from . import policy, roles
    items = []
    for e in read_jsonl(inst.events_path):
        if e.get("turn") != turn:
            continue
        if e.get("type") == "auto" and e.get("action") not in ("ignore",):
            items.append(f"自动处置 {e.get('rule')} → {e.get('action')}：{e.get('detail', '')}")
    for r in roles.decisions(inst, turn=turn):
        if r.get("phase") in ("gate", "revert", "breaker", "context", "review", "effort") and r.get("result") not in ("", "通过"):
            items.append(f"决策（{r['phase']}）：{r['decision']}。理由：{r['why']}" + (f"。证据：{r['evidence']}" if r.get("evidence") else ""))
    skipped = [i["n"] for i in verify_items_for(inst, turn) if i.get("verdict") == "skip"]
    if skipped:
        items.append(f"清单第 {', '.join(map(str, skipped))} 项没有机器判据，结论需要 Claude 根据原文判断")
    for k in ("policy", "goals"):
        st = policy.status(inst, k)
        if st["exists"] and not st["approved"]:
            items.append(f"{k}.json " + ("在批准后被改动，未生效" if st["changed_since_approval"] else "尚未批准，未生效"))
    rv = read_json(inst.turn_dir(turn) / "review_adapter.json")
    if rv:
        items.append(f"独立审查（reviewed by {rv.get('model') or '未注明模型'}）：{rv.get('verdict')}"
                     + (f"；{'；'.join(rv.get('notes', [])[:5])}" if rv.get("notes") else ""))
    items += fidelity.attention_extra(inst, turn)
    c = inst.state.get("conv") or {}
    if c.get("chars"):
        items.append(f"当前对话已累计约 {c['chars']} 字符、{c.get('turns', 0)} 轮"
                     "（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）")
    return items


def verify_items_for(inst: Instance, turn: int) -> list:
    d = inst.turn_dir(turn)
    it = read_json(d / "intake.json", {}) or {}
    v = read_json(d / "verify.json", {}) or {}
    return [{"n": i + 1, "item": t, **(v.get(str(i + 1)) or {})} for i, t in enumerate(it.get("checklist", []))]


def compose_report(inst: Instance, out=None, by="executor", effort=None) -> Path:
    st = inst.state
    # 1.7.1：目标达成（DONE）后也能由证据生成回报，不必手写（collab-wf-dev 第 89–91 轮）
    if st["state"] not in ("VERIFY", "EXECUTE", "APPLY", "REVIEW", "INTAKE", "WAIT_SCHEDULE", "IDLE", "DONE"):
        raise WfError(f"当前是 {st['state']}，回报草稿在一轮执行之后生成。")
    exec_turn = st["turn"]
    d = inst.turn_dir(exec_turn)
    it = read_json(d / "intake.json", {}) or {}
    items = verify_items(inst)
    execs = list_execs(inst, exec_turn)
    notes = read_jsonl(d / "notes.jsonl")
    devs = [n["text"] for n in notes if n["kind"] == "deviation"]
    decs = [n["text"] for n in notes if n["kind"] == "decision"]
    infos = [n["text"] for n in notes if n["kind"] == "info"]   # 1.5.1：说明类备注此前收下却不呈现（第 43 轮）
    failed = [e for e in execs if e.get("exit_code") not in (0, None)]
    bad = [i for i in items if i.get("verdict") in ("fail", "deviation")]
    pending = [i for i in items if not i.get("verdict")]
    if effort is None:
        from . import roles
        effort = roles.choose(inst, "report_fail" if (devs or failed or bad) else "report_ok",
                              claude_suggestion=it.get("effort"),
                              consecutive_failed=st.get("consecutive_failed", 0))["effort"]
    version = inst.version() or "v?"
    attn = attention(inst, exec_turn)
    machine.go(inst, "COMPOSE", by="wf", reason="生成回报草稿")
    new_turn = inst.state["turn"]

    L = [f"【回报 · 第 {new_turn} 轮 · {version}】档位：{effort}", ""]
    L.append("① 环境障碍与偏离")
    body = [f"- {t}" for t in devs]
    body += [f"- 命令 {e['id']} 退出码 {e['exit_code']}：`{e['cmd']}`" for e in failed]
    body += [f"- 清单第 {i['n']} 项{VERDICTS[i['verdict']]}：{i['item']}" + (f"（{i['note']}）" if i.get("note") else "")
             for i in bad]
    L += body or ["无。"]
    if infos:
        L += ["", "说明（备注）："] + [f"- {t}" for t in infos]
    L += ["", "② 关键数字与终端原文"]
    # 1.5.0：原文按预算完整嵌入（不再只取末尾 60 行），暂缓项与未关联命令也附原文，放不下的分片续传
    L += fidelity.report_evidence(items, execs, d, inst.outbox / fidelity.CARRY_FILE, VERDICTS, exec_turn)
    L += ["", "③ 需要决策的点"]
    L += [f"{k + 1}. {t}" for k, t in enumerate(decs)] or ["无。"]
    L += ["", "④ 需要注意（Attention）"]
    L += [f"- {a}" for a in attn] or ["- 无"]
    if pending:
        nums = ', '.join(str(i['n']) for i in pending)
        L += ["", f"（清单第 {nums} 项本轮没有执行到验收，原因见 ①）" if by == "driver"
              else f"（草稿提示：清单第 {nums} 项尚未验收，发送前请补全或说明）"]
    try:
        fp = fingerprint(inst.root)
        L += ["", f"附：仓库指纹 {fp['digest']}（{version}，{fp['count']} 个文件）"]
    except Exception:
        pass
    u = inst.usage_snapshot()
    if u:
        L += [f"附：发送前额度 5 小时窗口 {u.get('session')}%，7 天窗口 {u.get('weekly')}%"]
    text = "\n".join(L) + "\n"
    out = Path(out) if out else inst.outbox / f"turn-{new_turn:03d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    inst.event("report_draft", file=str(out.relative_to(inst.root)) if inst.root in out.resolve().parents else str(out),
               effort=effort, by=by)
    return out


# ---------------- 发送前检查 ----------------

SECRET_PATTERNS = [
    (r"(?i)\b(api[_-]?key|api[_-]?secret|secret[_-]?key|access[_-]?token|password|passwd)\b\s*[:=]\s*['\"]?[A-Za-z0-9/+_\-]{12,}", "疑似密钥赋值"),
    (r"\b[A-Za-z0-9]{64}\b", "64 位字母数字串（币安密钥格式）"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥"),
    (r"\bsk-[A-Za-z0-9_\-]{20,}", "sk- 开头的密钥"),
    (r"\bAKIA[0-9A-Z]{16}\b", "云服务访问密钥"),
]


def check_outgoing(path: Path, kind="report") -> dict:
    text = Path(path).read_text(encoding="utf-8")
    secrets, problems, warnings = [], [], []
    for i, line in enumerate(text.splitlines(), 1):
        for pat, why in SECRET_PATTERNS:
            for m in re.finditer(pat, line):
                s = m.group(0)
                if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", s) and why.startswith("64"):
                    # git 提交号、sha256 摘要是十六进制，不是币安密钥（大小写混合字母数字）
                    continue
                masked = s[:4] + "…" + s[-2:] if len(s) > 8 else "…"
                secrets.append(f"第 {i} 行：{why}（{masked}）")
    if kind == "report":
        if not re.search(r"【回报 · 第 \d+ 轮 · v\d+\.\d+(?:\.\d+)?】", text):
            problems.append("缺少标题行：【回报 · 第 N 轮 · vX.Y】")
        for k in ("①", "②", "③"):
            if k not in text:
                problems.append(f"缺少 {k} 段")
        if "```" not in text:
            warnings.append("② 段没有终端原文代码块（CP-2）")
    else:
        if not re.search(r"v\d+\.\d+", text):
            problems.append("没有写明当前版本号")
    if not re.search(r"档位[:：]?\s*(" + "|".join(EFFORTS) + ")", text, re.I):
        problems.append("没有写明档位（例如“档位：high”）")
    if len(text) > 40000:
        warnings.append(f"长度 {len(text)} 字符，超过 40000（CP-3）")
    return {"ok": not secrets and not problems, "secrets": secrets, "problems": problems, "warnings": warnings,
            "chars": len(text)}


# ---------------- 仓库指纹与按需文件包 ----------------
# 指纹只覆盖 Claude 交付的内容：排除执行端独有的文件（接入配置、版本清单、运行数据），两边可以直接比较。

FP_EXCLUDE = [".git/", ".venv/", ".env", "data/", "state/", "logs/", "results/", "RELEASE.json",
              "workflow/wf.json", "workflow/releases/", "workflow/state.json", "workflow/evidence/",
              "workflow/outbox/", "workflow/usage.json", "workflow/*.jsonl", "workflow/approvals.json",
              "workflow/goals_state.json"]


def _fp_files(root: Path) -> list:
    from .core import git, matches_protect
    root = Path(root)
    if (root / ".git").exists():
        files = git(["ls-files"], root).stdout.splitlines()
    else:
        files = []
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in ("__pycache__", ".git", ".venv")]
            for fn in fns:
                files.append(str(Path(dp, fn).relative_to(root)).replace(os.sep, "/"))
    return sorted(f for f in files if not matches_protect(f, FP_EXCLUDE) and not f.endswith(".pyc")
                  and (root / f).is_file())


def fingerprint(root: Path) -> dict:
    import hashlib
    rows = []
    for f in _fp_files(root):
        rows.append((f, hashlib.sha256((Path(root) / f).read_bytes()).hexdigest()))
    digest = hashlib.sha256("".join(f"{h}  {f}\n" for f, h in rows).encode()).hexdigest()[:16]
    return {"digest": digest, "count": len(rows), "files": [{"path": f, "sha": h[:12]} for f, h in rows]}


def snapshot(root: Path, paths, max_chars=40000) -> str:
    """按 Claude 指定的文件生成有界的文件包（文本），用于指纹不一致或 Claude 容器重置后补齐。"""
    from .core import matches_protect
    root = Path(root)
    out, total, skipped = [], 0, []
    for rel in paths:
        rel = rel.strip()
        f = (root / rel).resolve()
        if not rel or root.resolve() not in f.parents or matches_protect(rel, FP_EXCLUDE) or not f.is_file():
            skipped.append(f"{rel}（不存在或不在可发送范围内）")
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped.append(f"{rel}（不是文本文件）")
            continue
        block = f"===== {rel} =====\n{text.rstrip()}\n"
        if total + len(block) > max_chars:
            skipped.append(f"{rel}（超出 {max_chars} 字符上限，下次再发）")
            continue
        out.append(block)
        total += len(block)
    head = f"【文件包】{len(out)} 个文件，{total} 字符；仓库指纹 {fingerprint(root)['digest']}\n"
    tail = ("\n未包含：" + "；".join(skipped) + "\n") if skipped else ""
    return head + "\n".join(out) + tail


# ---------------- 桥接工具日志自检 ----------------
# 只读取执行端桥接工具写的本地日志（JSONL），核对 USAGE.md 第 4 节的行为约定；不访问通道。

# aborted：自己的发送被中断、结果未知（与 abort「人为中止他人的生成」区分）。它是发送的一种终态。
BRIDGE_ACTIONS = {"send", "accepted", "rejected", "generating", "done", "retry", "abort", "aborted", "download", "quota", "error",
                  "poll", "conv_new", "snapshot"}   # 1.5.0：适配器会记录 poll，此前被 R6 误报为无法解析；1.5.2：conv_new（新建对话）；1.5.3：snapshot


R3_GRACE_S = 900   # 1.6.8：发送后等结果的宽限（秒）。适配器发送阶段最长约 420 秒（W06），留出余量


def bridge_check(log: Path, hours=None, grace_s=None, at=None) -> list:
    """at：判定所用的“现在”（默认当前时间，自检用固定值）；hours 只过滤日志窗口，不是飞行宽限。"""
    from datetime import timedelta
    from .core import now, parse_iso
    ref_now = at or now()
    res = []
    ok = lambda rule, cond, msg: res.append({"rule": rule, "ok": bool(cond), "msg": msg})
    log = Path(log)
    if not log.exists():
        ok("R6 有记录", False, f"日志不存在：{log}")
        return res
    raw = log.read_text(encoding="utf-8").splitlines()
    evs, bad = [], []
    since = ref_now - timedelta(hours=hours) if hours else None
    for i, line in enumerate(raw, 1):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            t = parse_iso(e.get("ts"))
            if e.get("action") not in BRIDGE_ACTIONS or t is None:
                raise ValueError
        except (ValueError, json.JSONDecodeError):
            bad.append(i)
            continue
        if since is None or t >= since:
            e["_line"], e["_t"] = i, t
            evs.append(e)
    ok("R6 有记录", evs and not bad, f"{len(evs)} 条动作" + (f"；{len(bad)} 行无法解析（第 {', '.join(map(str, bad[:5]))} 行）" if bad else ""))
    # R1 同一时间只有一条消息在途
    inflight, v1 = None, []
    for e in evs:
        a = e["action"]
        if a == "send":
            if inflight is not None:
                v1.append(e["_line"])
            inflight = e
        elif a in ("done", "error", "abort", "aborted", "rejected") and inflight is not None:   # 被拒的发送不在途（1.5.0）
            inflight = None
    ok("R1 一条在途", not v1, "未发现重叠发送" if not v1 else f"上一条未结束又发送：第 {', '.join(map(str, v1[:5]))} 行")
    # R3 每次发送都有结果。1.6.8：发送之后还没有结果、也还没有新的发送时，距 at 不超过宽限（grace_s，默认 R3_GRACE_S）
    # 视为仍在飞行，不报；超过宽限照常报（此前作为最后一条的发送永远不报，之后跟着 poll、quota 的又会立即误报）。
    # 结果出现之前又有新的发送，一律报，与宽限无关。
    v3, flying = [], []
    g = R3_GRACE_S if grace_s is None else float(grace_s)
    for i, e in enumerate(evs):
        if e["action"] != "send":
            continue
        nxt = next((x for x in evs[i + 1:] if x["action"] in ("accepted", "rejected", "error", "aborted", "send")), None)
        if nxt is not None and nxt["action"] != "send":
            continue
        if nxt is None and (ref_now - e["_t"]).total_seconds() <= g:
            flying.append(e["_line"])
            continue
        v3.append(e["_line"])
    fly = f"；另有 {len(flying)} 条发送在 {int(g)} 秒宽限内等待结果（第 {', '.join(map(str, flying[:5]))} 行）" if flying else ""
    ok("R3 发送有结果", not v3, ("每次发送都有接受、拒绝、错误或中断记录" if not v3 else
                               f"发送后没有结果记录（超过 {int(g)} 秒宽限，或结果出现前又有新的发送）：第 {', '.join(map(str, v3[:5]))} 行") + fly)
    # R2 不中止生成：中止前须连续被拒至少 10 分钟，且期间没有生成迹象
    v2 = []
    for i, e in enumerate(evs):
        if e["action"] != "abort":
            continue
        prev = evs[:i]
        streak = []
        for x in reversed(prev):
            if x["action"] == "rejected":
                streak.append(x)
            elif x["action"] in ("retry",):
                continue
            else:
                break
        gen = [x for x in prev if x["action"] == "generating" and streak and x["_t"] >= streak[-1]["_t"]]
        span = (e["_t"] - streak[-1]["_t"]).total_seconds() if streak else 0
        if span < 600 or gen:
            v2.append(f"第 {e['_line']} 行（连续被拒 {int(span // 60)} 分钟" + ("，期间有生成迹象" if gen else "") + "）")
    ok("R2 不中止生成", not v2, "没有中止动作，或中止前已连续被拒满 10 分钟且无生成迹象" if not v2 else "；".join(v2[:5]))
    # R4 只记录实测值
    v4 = [e["_line"] for e in evs if e["action"] == "quota" and (e.get("estimated") or
          (e.get("value") is not None and not isinstance(e.get("value"), (int, float))))]
    ok("R4 只记实测值", not v4, "额度记录均为原值或缺失" if not v4 else f"疑似估算或非数值：第 {', '.join(map(str, v4[:5]))} 行")
    # R5 日志中没有凭据
    hits = []
    for i, line in enumerate(raw, 1):
        for pat, why in SECRET_PATTERNS:
            m = re.search(pat, line)
            if m and not (re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", m.group(0)) and why.startswith("64")):
                hits.append(f"第 {i} 行：{why}")
    ok("R5 无凭据", not hits, "未发现疑似凭据" if not hits else "；".join(hits[:5]))
    return res
