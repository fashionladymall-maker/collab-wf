"""驱动器：wf run（单个项目）/ wf tick（全部项目）的实现。
每次被系统定时唤醒：读状态 → 判断这一步能否自动做 → 做 → 记账 → 继续，直到需要等待外部、需要用户、或用完本次时间预算。

可靠性约定：
- 幂等与可重入：每一步开始前先把“意图”落盘（发送意图、执行到第几条），被中断后下次能判断做到了哪里。
- 绝不重复发送：发送意图存在而结果不明时，只轮询确认，不重发；仍不明就停下来（F14）。
- 绝不重跑被打断的命令：标记为中断，回报给 Claude 决定。
- 同一项目同一时间只有一个驱动器（文件锁，进程退出即释放）。
- 退出码：0 有进展或无事可做；10 在等外部（Claude、计划节点、额度、退避）；20 需要用户；30 目标达成；1 工具内部错误。
"""
from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from . import anomaly, fidelity, goals, machine, ops, page, policy, release, roles
from .core import (LOCAL, Instance, WfError, append_jsonl, git, now, now_iso, parse_iso, pid_alive, read_json,
                   read_jsonl, secs_since, write_json)
from .roles import decide
from .projectmd import parse_project_md

EXIT = {"progress": 0, "idle": 0, "wait": 10, "user": 20, "done": 30}
GLOBAL_QUOTA = LOCAL / "quota_hold.json"


class Ctx:
    def __init__(self, inst: Instance, pol: dict, deadline: float):
        self.inst, self.pol, self.deadline = inst, pol, deadline

    @property
    def st(self):
        return self.inst.state

    def time_left(self):
        return self.deadline - time.time()


# ---------------- 入口 ----------------

def run(inst: Instance, max_seconds=600, max_steps=40, linger=False) -> tuple:
    LOCAL.mkdir(parents=True, exist_ok=True)
    (LOCAL / "locks").mkdir(exist_ok=True)
    lock = open(LOCAL / "locks" / f"{inst.name}.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 10, {"note": "另一个驱动器正在推进这个项目，本次跳过", "did": [], "explain": explain(inst)}
    try:
        return _run_locked(inst, max_seconds, max_steps, linger)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def _linger_due(st: dict, ctx):
    """1.5.7：wf tick 内等回复。只在“发送”与“等待 Claude”两个状态、轮询间隔不小于 30 秒时：下一次轮询的时刻在本次
    时间预算内，就睡到那时再查，不交还给下一次唤醒（此前发现回复要等 300 秒的唤醒间隔，约七成轮次时间是空等）。"""
    if st.get("state") not in ("WAIT_REPLY", "SEND") or int(ctx.pol.get("poll_interval_s", 120)) < 30:
        return None
    due = [t for t in (parse_iso(st.get("poll_after")), parse_iso(st.get("retry_at"))) if t]
    if not due:
        return None
    wait = max(1.0, (min(due) - now()).total_seconds())
    return wait if wait + 15 <= ctx.time_left() else None


def _run_locked(inst, max_seconds, max_steps, linger=False):
    # transport.json 中的环境变量对驱动器自身也生效（例如本机删除守卫的状态目录，F16）：落地版本在本进程内删除文件
    os.environ.update({k: str(v) for k, v in (anomaly.transport_cfg().get("env") or {}).items()})
    st = inst.state
    drv = st.setdefault("driver", {})
    prev = drv.get("inflight")
    if prev and prev.get("pid") != os.getpid():
        inst.event("driver_interrupted", note=f"上一次驱动（PID {prev.get('pid')}，{prev.get('started')}）未正常结束，已从记录处接续",
                   by="driver")
    drv["inflight"] = {"pid": os.getpid(), "started": now_iso()}
    inst.save()
    did, kind, note = [], "idle", ""
    try:
        if st.get("paused"):
            kind, note = "user", f"已暂停：{st['paused'].get('note') or ''}（用户运行 wf unpause 恢复）"
        else:
            pol = policy.load(inst)
            if pol is None:
                ps = policy.status(inst, "policy")
                kind = "user"
                note = ("白名单 workflow/policy.json 已在批准后被改动，自动执行已停止：用户审阅后运行 wf policy approve"
                        if ps["changed_since_approval"] else
                        "没有已批准的白名单：用户审阅 workflow/policy.json 后运行 wf policy approve" if ps["exists"] else
                        "项目没有 workflow/policy.json（自动执行的命令白名单），驱动器不会自动执行任何操作")
            else:
                ctx = Ctx(inst, pol, time.time() + max_seconds)
                _health(ctx, did)
                steps = 0
                while True:
                    inst.save()   # 1.5.2（W18）：每一步之前与磁盘合并，看到其他进程的写入（例如 wf pause）
                    if inst.state.get("paused"):
                        kind, note = "user", f"已暂停：{inst.state['paused'].get('note') or ''}（用户运行 wf unpause 恢复）"
                        break
                    s = inst.state["state"]
                    kind, note = HANDLERS[s](ctx)
                    if kind == "report":
                        did.append(f"[{s}] {note}")
                        kind, note = _to_report(ctx)
                    did.append(f"[{s}] {note}")
                    steps += 1
                    if kind != "progress" or steps >= max_steps or ctx.time_left() <= 0:
                        due = _linger_due(inst.state, ctx) if (linger and kind == "wait" and steps < max_steps) else None
                        if due is not None:   # 1.5.7：在本次唤醒内等到下一次轮询
                            time.sleep(due)
                            continue
                        break
    except WfError as e:
        kind, note = "user", f"工具报告错误：{e}"
        did.append(note)
        try:
            if inst.state["state"] not in ("BLOCKED", "AWAITING_USER"):
                machine.go(inst, "BLOCKED", by="driver", reason=note[:300], block_reason=note[:500],
                           blocked_from=inst.state["state"], force=True)
        except WfError:
            pass
    code = EXIT.get(kind, 1)
    st = inst.state
    st.setdefault("driver", {})["inflight"] = None
    st["driver"]["last"] = {"ts": now_iso(), "exit": code, "kind": kind, "note": note, "did": did[-12:],
                            "explain": explain(inst)}
    inst.save()
    inst.event("tick", exit=code, note=note[:300], steps=len(did), by="driver")
    return code, {"note": note, "did": did, "explain": st["driver"]["last"]["explain"]}


def explain(inst: Instance) -> dict:
    """回答用户唯一关心的问题：现在卡在哪、为什么、需要谁做什么。"""
    s = machine.status(inst)
    st = inst.state
    where = f"第 {s['turn']} 轮 · {s['name']}（{s['state']}）"
    ps = policy.status(inst, "policy")
    if not ps["approved"] and not st.get("paused"):
        why = ("白名单在批准后被改动，自动执行已停止" if ps["changed_since_approval"] else
               "白名单尚未批准，驱动器不会自动执行任何操作" if ps["exists"] else "项目没有白名单 workflow/policy.json")
        return {"where": where, "why": why, "who": "用户", "what": "审阅 workflow/policy.json 后 wf policy approve --note …"}
    if st.get("paused"):
        return {"where": where, "why": f"用户暂停了自动化：{st['paused'].get('note') or ''}", "who": "用户", "what": "wf unpause --note …"}
    if s["state"] == "AWAITING_USER":
        who = _who(inst, "approve")   # 1.6.5（W43）：与 inbox 的路由一致：底线类交给用户，其余先由执行端处理
        return {"where": where, "why": st.get("needs_text") or "需要用户决定", "who": who,
                "what": ("决定后运行 wf approve --code <决定码> --said <用户原话>；拒绝则 wf pause 并接管" if who == "用户" else
                         "执行端按决策顺位处理：wf approve --code <决定码> --by executor --reason <理由>；做不到的才转告用户")}
    if s["state"] == "BLOCKED":
        who = _who(inst, "unblock")
        return {"where": where, "why": s["block_reason"] or "", "who": who,
                "what": "排除原因后 wf unblock --to <状态> --code <决定码> "
                        + ("--said <用户原话>" if who == "用户" else "--by executor --reason <理由>")
                        + "（wf status 显示该步的处置表；发送后没有落地用 wf resend）"}
    if s["state"] == "DONE":
        from . import tasks as _tasks   # 1.6.7：目标达成后仍有执行端待办时先说待办（DONE 不变，做完也不会自动回报）
        left = _tasks.open_items(inst)
        if left:
            return {"where": where, "why": f"全部目标判据已通过；另有 {len(left)} 项执行端待办未完成："
                    + "；".join(f"{t['id']} {t['text'][:30]}" for t in left[:3]), "who": "执行端",
                    "what": "做完一项 wf task done <编号> --note \"结果\"；目标已达成，做完不会自动回报，需要继续时 wf kick"}
        return {"where": where, "why": "全部目标判据已通过", "who": "用户", "what": "验收结果；需要继续时 wf kick"}
    if s["state"] == "WAIT_REPLY":
        return {"where": where, "why": "Claude 正在处理消息", "who": "Claude", "what": "无需操作，驱动器会定时检查"}
    if s["state"] in ("WAIT_SCHEDULE", "QUOTA_WAIT"):
        return {"where": where, "why": f"等待到 {s['until']}：{s['wait_reason'] or ''}", "who": "脚本",
                "what": "无需操作，到点后驱动器自动继续"}
    if st.get("retry_at") and (secs_since(st["retry_at"]) or 0) < 0:
        return {"where": where, "why": f"退避重试中，下次 {st['retry_at']}", "who": "脚本", "what": "无需操作"}
    if s["state"] == "IDLE":
        return {"where": where, "why": "没有待办，也没有计划节点", "who": "用户", "what": "需要推进时 wf kick --message 任务文件"}
    return {"where": where, "why": s["summary"], "who": "驱动器", "what": "无需操作，下次唤醒时继续"}


def _who(inst: Instance, kind: str) -> str:
    """1.6.5：一件待决事项该谁处理，按 inbox 的同一条路由（notify.route）回答“用户”或“执行端”。"""
    from . import asks, notify
    try:
        it = next((x for x in asks.pending(inst) if x["kind"] == kind), None)
    except Exception:   # noqa: BLE001
        it = None
    return "用户" if it is None or notify.route(it) == "user" else "执行端"


# ---------------- 异常处置 ----------------

def _anomaly(ctx: Ctx, facts: dict, detail: str):
    inst, st = ctx.inst, ctx.st
    r = anomaly.match(inst, facts)
    key = f"{st.get('turn', 0)}:{r['id']}"
    counts = st.setdefault("anomaly_counts", {})
    counts[key] = counts.get(key, 0) + 1
    n = counts[key]
    act = r["action"]
    if r.get("max") and n > r["max"]:
        act = r.get("then", "block")
    inst.event("auto", rule=r["id"], action=act, attempt=n, detail=detail[:300],
               facts={k: v for k, v in facts.items() if k != "text"} or None, by="driver")
    why = r.get("why", "")
    if act == "retry":
        b = r.get("backoff_s", [60])
        delay = b[min(n - 1, len(b) - 1)]
        st["retry_at"] = (now() + timedelta(seconds=delay)).isoformat(timespec="seconds")
        inst.save()
        return "wait", f"[{r['id']}] {why} {delay} 秒后重试（第 {n} 次）"
    if act == "quota_wait":
        until = parse_iso(facts.get("resets_at")) or (now() + timedelta(hours=1))
        write_json(GLOBAL_QUOTA, {"until": until.isoformat(), "set_by": inst.name, "ts": now_iso()})
        machine.go(inst, "QUOTA_WAIT", by="driver", reason=f"[{r['id']}] {why}", until=until.isoformat(),
                   wait_reason="额度重置")
        return "wait", f"[{r['id']}] 额度用尽，等待到 {until.isoformat(timespec='minutes')}"
    if act == "report":
        ops.add_note(inst, "deviation", f"[{r['id']}] {why} {detail}".strip(), by="driver")
        st["force_report"] = True
        inst.save()
        return "report", f"[{r['id']}] 已记入回报：{detail[:120]}"
    if act == "continue":
        machine.go(inst, "COMPOSE", by="driver", reason=f"[{r['id']}] {why}")
        inst.state["compose_reason"] = "continue"
        inst.save()
        return "progress", f"[{r['id']}] 请 Claude 从断点继续"
    target = "AWAITING_USER" if act == "await_user" else "BLOCKED"
    text = f"[{r['id']}] {why} {detail}".strip()
    if inst.state["state"] != target:
        machine.go(inst, target, by="driver", reason=text[:300], block_reason=text[:800],
                   blocked_from=inst.state["state"], force=inst.state["state"] in ("BLOCKED", "DONE"))
    inst.state["needs_text"] = text
    inst.save()
    return "user", text


def _wait_retry(ctx):
    ra = ctx.st.get("retry_at")
    if ra and (secs_since(ra) or 0) < 0:
        return "wait", f"退避重试中，下次 {ra}"
    ctx.st.pop("retry_at", None)
    return None


# ---------------- 健康检查 ----------------

def _health(ctx: Ctx, did: list):
    inst, st = ctx.inst, ctx.st
    if st["state"] in ("APPLY", "EXECUTE", "BLOCKED", "AWAITING_USER"):
        return
    for proc in inst.cfg.get("processes", []):
        if not proc.get("expected", True) or pid_alive(inst.root / proc["pidfile"], proc["match"]):
            continue
        today = now().strftime("%Y-%m-%d")
        hk = st.setdefault("health", {})
        key = f"{today}:{proc['name']}"
        hk[key] = hk.get(key, 0) + 1
        r = anomaly.match(inst, {"source": "health", "kind": "process_down"})
        limit = r.get("max", 2)
        start = proc.get("start")
        c = policy.classify(start, ctx.pol) if start else {"auto": False, "reasons": ["没有声明 start 命令"]}
        if hk[key] > limit or not c["auto"]:
            why = f"{proc['name']}不在运行，" + ("今天已自动拉起 %d 次仍失败" % limit if hk[key] > limit else "；".join(c["reasons"]))
            did.append(f"[健康] {why}")
            _anomaly(ctx, {"source": "health", "kind": "process_down_unrecoverable"}, why)
            return
        d = inst.evidence / "health"
        d.mkdir(parents=True, exist_ok=True)
        r2 = subprocess.run(["bash", "-c", start], cwd=str(inst.root), capture_output=True, text=True,
                            timeout=120, env=_exec_env(inst))
        (d / f"{time.strftime('%Y%m%d-%H%M%S')}-{proc['name']}.txt").write_text(
            f"$ {start}\nexit={r2.returncode}\n{r2.stdout}{r2.stderr}", encoding="utf-8")
        inst.event("auto", rule="PROC_DOWN", action="restart", attempt=hk[key], detail=f"{proc['name']}：{start} → exit {r2.returncode}",
                   by="driver")
        did.append(f"[健康] {proc['name']}不在运行，已执行 {start}（exit {r2.returncode}）")
        inst.save()


def _exec_env(inst):
    env = anomaly.driver_env()
    venv = inst.root / ".venv"
    if (venv / "bin").exists():
        env["VIRTUAL_ENV"] = str(venv)
        env["PATH"] = str(venv / "bin") + os.pathsep + env["PATH"]
    return env


# ---------------- 各状态的处理 ----------------

def h_idle(ctx):
    return "idle", "空闲：没有待办，也没有计划节点"


def h_done(ctx):
    return "done", "全部目标判据已通过"


def h_user(ctx):
    s = ctx.st
    return "user", s.get("needs_text") or s.get("block_reason") or "等待用户"


def _wait_executor(ctx, until):
    """1.6.6：等执行端把待办做完；全部做完、或到了最迟时间，自动生成本轮回报。"""
    from . import tasks as _tasks
    inst, st = ctx.inst, ctx.st
    left = _tasks.open_items(inst)
    if left and until and now() < until:
        return "wait", f"等执行端完成 {len(left)} 项待办：" + "；".join(f"{t['id']} {t['text'][:30]}" for t in left[:3])
    st.pop("await_executor", None)
    if left:
        ops.add_note(inst, "deviation", "执行端待办到最迟时间仍未完成：" + "；".join(f"{t['id']}（{t['text'][:60]}）" for t in left),
                     by="driver")
    inst.save()
    decide(inst, "after", "执行端待办已全部完成，自动生成回报" if not left else "执行端待办到最迟时间仍未完成，照常回报",
           "", "", "回报")
    return _to_report(ctx)


def h_wait_schedule(ctx):
    st = ctx.st
    until = parse_iso(st.get("until"))
    if st.get("await_executor") is not None:   # 1.6.6
        if st["await_executor"] == st.get("turn"):
            return _wait_executor(ctx, until)
        st.pop("await_executor", None)   # 早先一轮留下的标记（中间有人 wf kick 过），作废
    if until and now() < until:
        return "wait", f"等待计划节点 {until.isoformat(timespec='minutes')}：{st.get('wait_reason') or ''}"
    reason = st.get("wait_reason") or "计划节点"
    machine.go(ctx.inst, "COMPOSE", by="driver", reason=f"计划节点到期：{reason}")
    ctx.st["compose_reason"] = "checkpoint"
    ctx.st["checkpoint_reason"] = reason
    ctx.inst.save()
    return "progress", f"计划节点到期：{reason}"


def h_quota_wait(ctx):
    st = ctx.st
    until = parse_iso(st.get("until"))
    if until and now() < until:
        return "wait", f"等待额度重置 {until.isoformat(timespec='minutes')}"
    ret = st.get("return_to") or "SEND"
    machine.resume(ctx.inst, "额度已重置，继续", to=None, by="driver") if ret in ("SEND", "WAIT_REPLY") else \
        machine.go(ctx.inst, "COMPOSE", by="driver", reason="额度已重置")
    return "progress", f"额度已重置，回到 {ctx.st['state']}"


def _compose_text(ctx):
    inst, st = ctx.inst, ctx.st
    ver = inst.version() or "v?"
    reason = st.get("compose_reason")
    fp = ops.fingerprint(inst.root)
    hook = []
    for proc in inst.cfg.get("processes", []):
        pid = pid_alive(inst.root / proc["pidfile"], proc["match"])
        hook.append(f"{proc['name']}：{'运行中（PID %d）' % pid if pid else '未运行'}")
    prog = goals.progress(inst)
    gl = f"目标进度：{prog['done']} / {prog['total']}" + ("" if prog["approved"] else "（目标未批准）") if prog["defined"] else "目标：未定义"
    if reason == "checkpoint":
        ch = roles.choose(inst, "checkpoint")
        decide(inst, "effort", f"节点消息使用 {ch['effort']}（角色 {ch['role']}）", "；".join(ch["reasons"]))
        body = (f"【计划节点 · 第 {st['turn']} 轮 · {ver}】档位：{ch['effort']}\n\n"
                f"计划节点“{st.get('checkpoint_reason')}”已到。这条消息由驱动器自动发出。\n\n"
                f"- 工作流：上一轮已完成，等待本节点的指令\n- " + "\n- ".join(hook or ["没有声明长期运行的进程"]) +
                f"\n- {gl}\n- 仓库指纹 {fp['digest']}（{fp['count']} 个文件）\n\n"
                "请给出本节点的【本机执行】【本轮回报清单】与【驱动指令】。\n")
        return body, "task", ch["effort"]
    if reason == "continue":
        last_eff = (read_json(inst.turn_dir(st["turn"] - 1) / "intake.json", {}) or {}).get("effort") or "high"
        body = (f"【续接 · 第 {st['turn']} 轮 · {ver}】档位：{last_eff}\n\n上一轮回复在中途中断或缺少固定块"
                f"（这条消息由驱动器自动发出）。请从断点继续，并给出完整的六个固定块。\n\n附：仓库指纹 {fp['digest']}\n")
        return body, "task", last_eff
    return None, None, None


def _newconv_message(b: dict, original: str, sync_status: str) -> tuple:
    """1.6.4：选开新对话的第一条消息，并把本轮原本要发出的消息原样附在后面。返回 (正文, 档位, "短消息" 或 "长消息")。
    短消息只给公开仓库的链接，所以只在这次同步已推送、或内容没变时用；否则用长消息（自带 WORKFLOW、PROJECT 全文与文件清单）。"""
    use_short = bool(b.get("short")) and sync_status in ("pushed", "unchanged")
    text = Path(b["short"] if use_short else b["message"]).read_text(encoding="utf-8")
    m = re.search(r"档位：(low|medium|high|xhigh|max)", text[:400])
    if original.strip():
        text = text.rstrip("\n") + "\n\n===== 附：本轮原本要发出的消息（原样）=====\n\n" + original
    return text, (m.group(1) if m else "max"), ("短消息" if use_short else "长消息")


def _new_conversation(ctx, out, why, b) -> tuple:
    """1.6.4：对话达到上限时由驱动器开新对话。返回 (新的待发消息, "") 或 (None, 没成功的原因)。
    顺序：续接消息先通过发送前检查 → 记下意图 → 适配器 conv_new（新建对话、写全 settings 并回读核对、把默认对话切过去）
    → 计数归零、事件记 by=driver → 续接消息作为本轮消息照常发送。任何一步不成立都不切换，由调用方退回原来的做法：停下等人。
    适配器调用超时属于结果不明（可能已经建好），保留意图、不再自动重试，避免重复建对话。"""
    inst, st = ctx.inst, ctx.st
    turn = st["turn"]
    pend = st.get("conv_switch") or {}
    if pend.get("turn") == turn and pend.get("status") == "pending":
        return None, "上次自动开新对话的调用中途中断，结果不明（可能已经建好了一个新对话）；为避免重复建对话，不再自动重试"
    if not b:
        return None, "恢复包没有生成"
    sync_status = ""
    if b.get("short"):
        from . import sync
        sync_status = str(((sync.record(inst) or {}).get("last") or {}).get("status") or "")
    src = Path(out["file"]) if out and out.get("file") else None
    original = src.read_text(encoding="utf-8") if src and src.is_file() else ""
    text, effort, which = _newconv_message(b, original, sync_status)
    draft = inst.outbox / f"turn-{turn:03d}-newconv.md"
    draft.write_text(text, encoding="utf-8")
    chk = ops.check_outgoing(draft, "task")
    if chk["secrets"] or chk["problems"]:
        return None, "续接消息没有通过发送前检查：" + "；".join((chk["secrets"] + chk["problems"])[:3])
    st["conv_switch"] = {"turn": turn, "status": "pending", "ts": now_iso()}
    inst.save()
    r = anomaly.call("conv_new", ["--note", f"collab-wf {inst.name} 第 {turn} 轮：{why}"])
    data = r.get("data") or {}
    if r["code"] != 0 or data.get("ok") is not True:
        if r.get("kind") != "timeout":   # 明确失败：按约定默认对话保持不变，意图作废；超时则结果不明，保留意图
            st.pop("conv_switch", None)
        inst.save()
        return None, f"适配器 conv_new 退出码 {r['code']}：{(data.get('error') or r.get('raw') or '')[:200]}"
    target = inst.outbox / f"turn-{turn:03d}.md"
    if src and src.is_file() and src.resolve() == target.resolve():
        os.replace(target, inst.outbox / f"turn-{turn:03d}-report.md")   # 本轮回报原件留档；全文已附在续接消息里
    os.replace(draft, target)   # 同步与对话记录按 turn-NNN.md 取“实际发出的消息”
    old = dict(st.get("conv") or {})
    st["conv"] = {"since_turn": turn, "since": now_iso(), "chars": 0, "turns": 0, "by": "driver", "conv": data.get("conv"),
                  "previous": {k: old.get(k) for k in ("since_turn", "since", "chars", "turns")}}
    st.pop("conv_switch", None)
    st.pop("send_ref", None)   # 上一轮的 ref 属于旧对话，不再作为 --prev-ref 交给适配器
    new_out = st["outgoing"] = {"turn": turn, "file": str(target), "kind": "task", "effort": effort}
    inst.event("conv_new", note=f"驱动器自动开新对话（{which}" + ("，附本轮原本要发出的消息）" if original.strip() else "）"),
               conv=data.get("conv"), by="driver")
    decide(inst, "context", "自动开新对话", why,
           f"新对话 {data.get('conv') or '（适配器未给出 id）'}；续接消息 {target.name}（{which}）", "计数已归零，续接消息作为本轮消息发送")
    inst.save()
    return new_out, ""


def h_compose(ctx):
    inst, st = ctx.inst, ctx.st
    out = st.get("outgoing")
    if st.get("compose_reason") == "resend" and out:
        st.pop("compose_reason", None)
        machine.go(inst, "SEND", by="driver", reason="重发上一条消息（叶子被拒后）")
        return "progress", "重发上一条消息"
    if not out or out.get("turn") != st["turn"]:
        body, kind, effort = _compose_text(ctx)
        p = inst.outbox / f"turn-{st['turn']:03d}.md"
        if body is None and p.exists():
            # 1.5.2（W19）：wf report 手工生成的本轮回报草稿，由驱动器接着发送（此前停在这里，并提示一个必然失败的 wf kick）
            m = re.search(r"档位：(low|medium|high|max)", p.read_text(encoding="utf-8")[:400])
            kind, effort = "report", (m.group(1) if m else "high")
        elif body is None:
            return "user", ("有一条待发消息需要内容：执行端用 wf report 生成本轮回报草稿，驱动器会接着发送"
                            "（wf kick 只能在空闲、等待计划节点或目标达成时使用）")
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        out = st["outgoing"] = {"turn": st["turn"], "file": str(p), "kind": kind, "effort": effort}
        inst.save()
    chk = ops.check_outgoing(Path(out["file"]), out["kind"])
    if chk["secrets"]:
        return _anomaly(ctx, {"source": "compose", "kind": "secret"}, "；".join(chk["secrets"]))
    if chk["problems"] and out["kind"] == "report":
        return _anomaly(ctx, {"source": "compose", "kind": "structure"}, "；".join(chk["problems"]))
    c = st.get("conv") or {}
    mt, mc = int(ctx.pol.get("max_conversation_turns", 0)), int(ctx.pol.get("max_conversation_chars", 0))
    if (mt and c.get("turns", 0) >= mt) or (mc and c.get("chars", 0) >= mc):
        why = f"当前对话已 {c.get('turns', 0)} 轮、约 {c.get('chars', 0)} 字符，达到上限（policy：{mt} 轮 / {mc} 字符）"
        b = None
        try:
            from .admin import bootstrap
            b = bootstrap(inst, why, by="driver")   # 1.6.4：驱动器生成的恢复包记 by=driver（此前记 executor，M1 判据会把它算成人工动作）
            ev = f"{b['message'].name}，{b['zip'].name}" + (f"；短消息 {b['short'].name}（只发这一条即可）" if b.get("short") else "")
        except WfError as e:
            ev = f"恢复包生成失败：{e}"
        new_out, fail = None, ""
        if ctx.pol.get("auto_new_conversation") is True:
            # 1.6.4（collab-wf-dev 第 44 轮，执行端按用户的决策顺位决定）：驱动器自己开新对话，不再停下等人
            new_out, fail = _new_conversation(ctx, out, why, b)
        if not new_out:
            decide(inst, "context", "停下，建议开新对话", why + (f"；自动开新对话未成功：{fail}" if fail else ""), ev, "等待用户")
            return _anomaly(ctx, {"source": "context", "kind": "conversation_full"},
                            why + (f"。自动开新对话未成功：{fail}" if fail else "")
                            + f"。恢复包已在 workflow/outbox/（{ev}）：用户开新对话、上传 zip、发送说明消息后运行 wf conv new --note …，再 wf approve")
        out = new_out
    if st.pop("conv_switch", None) is not None:   # 1.6.4：对话已不在上限（自动或手动开了新对话），中断留下的意图作废
        inst.save()
    hold = read_json(GLOBAL_QUOTA, {}) or {}
    if hold.get("until") and parse_iso(hold["until"]) and now() < parse_iso(hold["until"]):
        machine.go(inst, "SEND", by="driver", reason="待发")
        machine.go(inst, "QUOTA_WAIT", by="driver", reason=f"额度暂停中（{hold.get('set_by')} 触发）",
                   until=hold["until"], wait_reason="额度重置")
        return "wait", f"额度暂停中，等待到 {hold['until']}"
    b = st.setdefault("budget", {})
    today = now().strftime("%Y-%m-%d")
    if b.get("date") != today:
        b.update(date=today, rounds=0)
    limit = int(ctx.pol.get("max_rounds_per_day", 20))
    if b["rounds"] >= limit:
        # 1.6.1：上限不变，但不再停下等人：等到第二天（UTC 零点）自动继续（用户第 35 轮：把“需要执行端做的事”降到零）
        tomorrow = (now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        decide(inst, "budget", "今日自动轮次已达上限，等到明天继续", f"policy.max_rounds_per_day = {limit}", "",
               f"等待到 {tomorrow.isoformat(timespec='minutes')}")
        machine.go(inst, "SEND", by="driver", reason="待发")
        machine.go(inst, "QUOTA_WAIT", by="driver", reason=f"今日自动轮次已达上限（{limit}），明天继续",
                   until=tomorrow.isoformat(), wait_reason="每日轮次上限")
        return "wait", f"今日自动轮次已达上限（{limit}），等待到 {tomorrow.isoformat(timespec='minutes')}"
    machine.go(inst, "SEND", by="driver", reason="消息已通过发送前检查")
    return "progress", f"消息已就绪（{out['kind']}，档位 {out['effort']}）"


def h_send(ctx):
    inst, st = ctx.inst, ctx.st
    w = _wait_retry(ctx)
    if w:
        return w
    out = st.get("outgoing") or {}
    turn = st["turn"]
    intent = st.get("send_intent")
    tdir = inst.turn_dir()
    gap = int(ctx.pol.get("min_send_gap_s", 0) or 0)   # 1.6.5（W42）：默认 0，不改变原有行为
    ra = st.get("replied_at")
    if gap > 0 and ra and not (intent and intent.get("turn") == turn and intent.get("status") == "pending"):
        waited = secs_since(ra) or 0
        if 0 <= waited < gap:
            st["retry_at"] = (now() + timedelta(seconds=int(gap - waited) + 1)).isoformat(timespec="seconds")
            inst.save()
            return "wait", (f"上一条回复 {int(waited)} 秒前才落地，等满 {gap} 秒再发"
                            "（min_send_gap_s，W42：太早发送会接到上一条残留的生成，被误判为已接受）")
    if intent and intent.get("turn") == turn and intent.get("status") == "pending":
        pargs = ["--project", inst.name, "--turn", turn, "--ref", intent.get("ref") or "", "--out", tdir / "delivered"]
        if anomaly.transport_cfg().get("poll_since"):
            # 1.5.1：没有 ref 时，让适配器按“发送时刻之后、内容与本轮消息相同的那条消息”定位（transport.json 的 poll_since）
            pargs += ["--since", intent.get("ts") or "", "--message", intent.get("file") or out.get("file") or "",
                      "--prev-ref", st.get("send_ref") or ""]   # 上一轮的 ref 原样交回，适配器可据此找到会话
        r = anomaly.call("poll", pargs, env_extra=page.poll_env(inst))   # 1.5.3：顺带写 Claude 页面快照
        page.after_poll(inst, "poll", r["data"].get("status"))
        status = r["data"].get("status")
        if r["code"] == 0 and status in ("generating", "done"):
            intent.update(status="accepted", ref=r["data"].get("ref") or intent.get("ref"))
            return _finalize_send(ctx, out, "发送曾被中断，经轮询确认消息已被接受")
        # 1.5.1：policy 的 send_unknown_wait 为真时先自动等待并反复确认（长回复可达半小时以上），绝不自动重发
        return _anomaly(ctx, {"source": "driver", "kind": fidelity.send_unknown_kind(ctx.pol)},
                        f"发送意图 {intent.get('ts')} 结果不明；轮询返回 {status or r['raw'][:120]}")
    rate = anomaly.transport_cfg().get("rate") or {}
    if rate.get("max_sends"):
        win = int(rate.get("window_s", 600))
        recent = [r for r in read_jsonl(LOCAL / "send_log.jsonl", limit=500) if (secs_since(r.get("ts")) or 1e9) < win]
        if len(recent) >= int(rate["max_sends"]):
            until = parse_iso(recent[0]["ts"]) + timedelta(seconds=win)
            st["retry_at"] = until.isoformat(timespec="seconds")
            inst.save()
            decide(inst, "rate", f"推迟发送到 {st['retry_at']}", f"{win} 秒内已发送 {len(recent)} 条（上限 {rate['max_sends']}）")
            return "wait", f"全局限速：{win} 秒内已发送 {len(recent)} 条，推迟到 {st['retry_at']}"
    st["send_intent"] = {"turn": turn, "status": "pending", "ts": now_iso(), "file": out.get("file")}
    inst.save()
    _quota(inst)
    send_args = ["--project", inst.name, "--turn", turn, "--message", out.get("file"),
                 "--effort", out.get("effort") or "high",
                 "--model", out.get("model") or anomaly.transport_cfg().get("model", "")]
    if st.get("resend_parent"):
        send_args += ["--parent", st["resend_parent"]]
    r = anomaly.call("send", send_args)
    code = r["code"]
    if code == 0:
        # 不只信退出码：适配器必须给出“生成已开始”的正面证据，否则按“通道忙”处理（缺口 11-3、F22）
        if r["data"].get("accepted") is False or r["data"].get("status") == "heartbeat_only":
            st["send_intent"] = None
            inst.save()
            return _anomaly(ctx, {"source": "transport", "op": "send", "code": 2, "text": r["raw"]},
                            "适配器返回 0 但未确认生成开始（只有心跳）")
        st["send_intent"].update(status="accepted", ref=r["data"].get("ref"))
        st.pop("resend_parent", None)
        return _finalize_send(ctx, out, "通道已接受")
    if code != 4:  # 4 = 暂时性错误或超时：结果可能不明，保留意图留待下次轮询确认
        st["send_intent"] = None
    inst.save()
    facts = {"source": "transport", "op": "send", "code": code, "resets_at": r["data"].get("resets_at"),
             "kind": r.get("kind"), "text": r["raw"]}
    if r.get("kind") in ("not_configured", "bad_output"):
        facts = {"source": "transport", "kind": r["kind"], "text": r["raw"]}
    return _anomaly(ctx, facts, r["raw"][:300])


def _finalize_send(ctx, out, note):
    inst, st = ctx.inst, ctx.st
    model = out.get("model") or anomaly.transport_cfg().get("model")
    append_jsonl(LOCAL / "send_log.jsonl", {"ts": now_iso(), "project": inst.name, "turn": st["turn"]})
    st["send_ref"] = st["send_intent"].get("ref")
    b = st.setdefault("budget", {"date": now().strftime("%Y-%m-%d"), "rounds": 0})
    b["rounds"] = b.get("rounds", 0) + 1
    inst.save()
    ops.mark_sent(inst, out.get("file"), out.get("kind"), out.get("effort"), model, by="driver",
                  role=out.get("role"), reasons=out.get("reasons"))
    st = inst.state
    st["last_sent"] = {"turn": st["turn"], "file": out.get("file"), "kind": out.get("kind"), "effort": out.get("effort")}
    st["send_intent"] = None
    st["outgoing"] = None
    st["poll_after"] = (now() + timedelta(seconds=int(ctx.pol.get("first_poll_after_s", 60)))).isoformat(timespec="seconds")
    inst.save()
    return "progress", note


def _quota(inst):
    if anomaly.transport_cfg().get("quota"):
        r = anomaly.call("quota", ["--project", inst.name, "--out", inst.usage_path], timeout=60)
        if r["code"] != 0:
            inst.event("auto", rule="QUOTA_READ", action="ignore", detail=r["raw"][:200], by="driver")


def _landed_since(st):
    """1.6.8（P2-E）：落地核对的墙钟起点：本轮发送时刻（mark_sent 写的 sent_at）；缺失时用第一次核对不到的时刻。"""
    from .core import parse_iso as _pi
    return _pi(st.get("sent_at")) or _pi(st.setdefault("send_unconfirmed_since", now_iso()))


def _landed_gave_up(ctx, n: int) -> bool:
    """1.6.8（P2-E）：落地核对何时放弃（进入 SEND_NOT_LANDED；仍不自动重发，恢复只用 wf resend）。
    policy 有 landed_timeout_s（秒，> 0）时按墙钟：距发送已满 landed_timeout_s，且至少核对了 landed_min_polls 次
    （默认 3，避免长时间没被唤醒后只查一次就下结论）；此时 landed_confirm_polls 不再起作用。
    没有 landed_timeout_s 时沿用 landed_confirm_polls 次数（旧行为，兼容）。"""
    pol, st = ctx.pol, ctx.st
    lt = int(pol.get("landed_timeout_s", 0) or 0)
    if lt > 0:
        return (now() - _landed_since(st)).total_seconds() >= lt and n >= max(1, int(pol.get("landed_min_polls", 3) or 1))
    return n >= int(pol.get("landed_confirm_polls", 5))


def h_wait_reply(ctx):
    inst, st = ctx.inst, ctx.st
    w = _wait_retry(ctx)
    if w:
        return w
    if not st.get("send_ref"):
        # 本轮是在驱动器之外手动发出的，没有登记 ref，无法自动接续（缺口 7）
        return _anomaly(ctx, {"source": "driver", "kind": "no_ref"},
                        "本轮消息是手动发出的、没有登记 ref，驱动器无法轮询接续。请手动 wf mark replied，"
                        "或下次用 wf mark sent --ref（适配器返回的 ref）发送")
    pa = st.get("poll_after")
    if pa and (secs_since(pa) or 0) < 0:
        return "wait", "Claude 正在处理（稍后检查）"
    tdir = inst.turn_dir()
    r = anomaly.call("poll", ["--project", inst.name, "--turn", st["turn"], "--ref", st.get("send_ref") or "",
                              "--out", tdir / "delivered"], env_extra=page.poll_env(inst))   # 1.5.3：顺带写页面快照
    page.after_poll(inst, "poll", r["data"].get("status"))
    status = r["data"].get("status")
    interval = int(ctx.pol.get("poll_interval_s", 120))
    # 独立落地核对（缺口 11/§3）：send 返回 0 只是适配器的说法。这里由 wf 查会话确认消息真的出现了。
    # landed 字段（适配器可给出）或 generating/done 都算“确实落地”；若连续 none，说明消息没落地，按 POLL_NONE 退避/阻塞。
    if status in ("generating", "done") or r["data"].get("landed"):
        st.pop("send_unconfirmed", None)
        st.pop("send_unconfirmed_since", None)
    elif status == "none":
        n = st.get("send_unconfirmed", 0) + 1
        st["send_unconfirmed"] = n
        if _landed_gave_up(ctx, n):   # 1.6.8（P2-E）：设了 landed_timeout_s 时按墙钟
            span = int((now() - _landed_since(st)).total_seconds())
            st["send_unconfirmed"] = 0
            st.pop("send_unconfirmed_since", None)
            st.pop("send_ref", None)
            st["not_landed"] = dict(st.get("last_sent") or {}, turn=st["turn"])   # 1.6.5（W43）：留给 wf resend 恢复本轮消息
            inst.save()
            return _anomaly(ctx, {"source": "driver", "kind": "send_not_landed"},
                            f"适配器曾报告已接受，但连续 {n} 次轮询（距发送约 {span // 60} 分钟）都看不到这条消息出现在会话中（缺口 11/§3）")
        # 落地未确认：用较短的间隔尽快再查一次，不进入 POLL_NONE 的长退避
        st["poll_after"] = (now() + timedelta(seconds=int(ctx.pol.get("landed_poll_s", 60)))).isoformat(timespec="seconds")
        inst.save()
        return "wait", f"已发送但消息尚未出现在会话中，第 {n} 次核对，稍后再查"
    if r["code"] == 0 and status == "generating":
        st["poll_after"] = (now() + timedelta(seconds=interval)).isoformat(timespec="seconds")
        dwell = machine.status(inst)["dwell_s"]
        if dwell > 3 * (machine.sdef("WAIT_REPLY").get("max_dwell_s") or 2700):
            return _anomaly(ctx, {"source": "dwell", "state": "WAIT_REPLY"}, f"已等待 {dwell // 60} 分钟")
        inst.save()
        return "wait", f"Claude 正在生成（已等待 {dwell // 60} 分钟）"
    if r["code"] == 0 and status == "done":
        rf = r["data"].get("reply_file")
        if not rf or not Path(rf).exists():
            return _anomaly(ctx, {"source": "transport", "kind": "bad_output"}, f"轮询报告完成，但回复文件不存在：{rf}")
        verdict, why = fidelity.reply_attribution(r["data"], st.get("send_ref"))
        if verdict == "stale":
            # 1.5.0：取回的不是本轮消息的回复（第 30 轮取回了对话里已有的上一条回复）：不收下，继续等
            n = int(st.get("stale_polls", 0)) + 1
            st["stale_polls"] = n
            st["poll_after"] = (now() + timedelta(seconds=interval)).isoformat(timespec="seconds")
            inst.save()
            if n >= int(ctx.pol.get("stale_reply_polls", 3)):
                st["stale_polls"] = 0
                inst.save()
                return _anomaly(ctx, {"source": "driver", "kind": "stale_reply"},
                                f"连续 {n} 次取回的都不是本轮消息的回复：{why}")
            return "wait", f"取回的不是本轮消息的回复（{why}），第 {n} 次，稍后再查"
        st.pop("stale_polls", None)
        inst.event("reply_unverified" if verdict == "unverified" else "reply_checked", note=why or None, by="driver")
        st["delivered"] = [str(Path(f)) for f in r["data"].get("files", []) if Path(f).exists()]
        if r["data"].get("other_deliveries"):
            # 同一轮里有多次生成各自交付了文件（例如一次被安全防护拦下后续写、一次正常回复）。
            # 只采用最后一条完整回复交付的文件，并记录下来（§7）。
            n = r["data"]["other_deliveries"]
            decide(inst, "delivery", f"本轮存在 {n} 套其他交付，只采用最后一条完整回复的文件",
                   "同一轮的多次生成会在同一个容器里各自交付文件；以最后一条完整回复为准", ", ".join(Path(f).name for f in st["delivered"]),
                   "已记录")
            inst.event("auto", rule="MULTI_DELIVERY", action="report", detail=f"其他交付 {n} 套", by="driver")
            ops.add_note(inst, "deviation", f"[MULTI_DELIVERY] 本轮存在 {n} 套其他交付，只采用了最后一条完整回复的文件", by="driver")
        st.pop("poll_after", None)
        st["replied_at"] = now_iso()   # 1.6.5（W42）：发下一条之前据此留出间隔
        inst.save()
        _quota(inst)
        ops.mark_replied(inst, rf, r["data"].get("tools"), by="driver")
        return "progress", f"收到回复（交付文件 {len(st['delivered'])} 个）"
    if status == "refused":
        # 叶子被通道的安全防护拒绝，无法从它继续：回退到上一条健康消息，重新组织并重发本轮消息（§10）
        cnt = st.setdefault("anomaly_counts", {})
        key = f"{st['turn']}:LEAF_REFUSED"
        cnt[key] = cnt.get(key, 0) + 1
        inst.event("auto", rule="LEAF_REFUSED", action="recover", attempt=cnt[key], detail=r["raw"][:200], by="driver")
        decide(inst, "recover", "叶子被安全防护拒绝，回退并重发本轮", "通道对该消息返回拒绝，无法从它继续",
               r["data"].get("parent_ref") or "", "重新组织消息")
        if cnt[key] > int(ctx.pol.get("max_leaf_refusals", 2)):   # 1.5.0：可配置；建议设为 0，第一次被拒就停下
            machine.go(inst, "BLOCKED", by="driver", reason="叶子连续被安全防护拒绝", force=True,
                       block_reason="同一轮消息连续被通道的安全防护拒绝，可能是消息内容触发了防护", blocked_from="WAIT_REPLY")
            inst.state["needs_text"] = "同一轮消息连续被安全防护拒绝。请检查消息内容后 wf unblock。"
            inst.save()
            return "user", "叶子连续被安全防护拒绝，已停下等用户"
        st.pop("send_ref", None)
        st["send_intent"] = None
        st["resend_parent"] = r["data"].get("parent_ref")
        st["compose_reason"] = "resend"
        inst.save()
        machine.go(inst, "COMPOSE", by="driver", reason="叶子被安全防护拒绝，回退重发")
        return "progress", "叶子被安全防护拒绝，已回退，将重发本轮消息"
    facts = {"source": "transport", "op": "poll", "code": r["code"], "status": status, "text": r["raw"]}
    if r.get("kind") in ("not_configured", "bad_output"):
        facts = {"source": "transport", "kind": r["kind"], "text": r["raw"]}
    return _anomaly(ctx, facts, f"轮询结果：{status or r['raw'][:160]}")


def h_intake(ctx):
    inst, st = ctx.inst, ctx.st
    reply = inst.turn_dir() / "reply.md"
    if not reply.exists():
        return _anomaly(ctx, {"source": "intake", "kind": "no_reply_file"}, "本轮没有保存的回复全文")
    res = ops.intake(inst, reply, by="driver")
    if res["blocks_missing"]:
        return _anomaly(ctx, {"source": "intake", "kind": "missing_blocks"}, "缺少：" + "、".join(res["blocks_missing"]))
    d = res.get("directives") or {}
    if d.get("errors"):
        ops.add_note(inst, "deviation", "【驱动指令】有无法解析的行：" + "；".join(d["errors"]), by="driver")
        st["force_report"] = True
        inst.save()
    return "progress", f"解析回复：{len(res['commands'])} 条命令，{len(res['checklist'])} 项清单"


def _vt(v):
    m = re.match(r"v(\d+)\.(\d+)", v or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _blocking_decisions(items) -> list:
    """1.5.2（W15）：只有显式标记“（前置）”的用户决定会让本轮停下等用户；“这一项不阻塞”之类的文字不再误判。"""
    return [x for x in (items or []) if re.search(r"[（(]前置[)）]", str(x))]


def h_review(ctx):
    inst, st = ctx.inst, ctx.st
    it = read_json(inst.turn_dir() / "intake.json", {}) or {}
    d = it.get("directives") or {}
    flags = st.setdefault("turn_flags", {})
    if flags.get("review_accepted"):
        return _after_review(ctx, it, d)
    findings = []
    for i, c in enumerate(it.get("commands", []), 1):
        cl = policy.classify(c, ctx.pol)
        if not cl["auto"]:
            findings.append(f"命令 {i}「{c}」：" + "；".join(cl["reasons"]))
    rel = d.get("release")
    if rel:
        cand = [p for p in st.get("delivered", []) if Path(p).name == Path(rel).name]
        if not cand:
            r = _anomaly(ctx, {"source": "review", "kind": "release_not_delivered"}, f"指令要求落地 {rel}，但本轮交付文件中没有")
            return _to_report(ctx) if r[0] == "report" else r
        with tempfile.TemporaryDirectory() as td:
            try:
                src = release._extract(Path(cand[0]), Path(td))
            except WfError as e:
                r = _anomaly(ctx, {"source": "apply", "text": str(e)}, str(e))
                return _to_report(ctx) if r[0] == "report" else r
            newv = parse_project_md((src / "PROJECT.md").read_text(encoding="utf-8")).get("version") \
                if (src / "PROJECT.md").exists() else None
            curv = inst.version()
            if _vt(newv) is None or (_vt(curv) and _vt(newv) <= _vt(curv)):
                r = _anomaly(ctx, {"source": "review", "kind": "version_not_next"}, f"当前 {curv}，交付 {newv}")
                return _to_report(ctx) if r[0] == "report" else r
            for f in policy.release_findings(inst, src, ctx.pol):
                findings.append(f"版本包 {f['file']}：{f['why']}")
            if anomaly.transport_cfg().get("review"):
                findings += _independent_review(ctx, src, cand[0])
        if not ctx.pol.get("auto_apply", True):
            findings.append(f"版本包 {rel}：policy 设定落地版本需要用户批准")
        st["release_path"] = cand[0]
    blocking = _blocking_decisions(it.get("user_decisions", []))
    findings += [f"Claude 标注的前置决定：{x}" for x in blocking]
    review_rec = {"rows": [policy.classify(c, ctx.pol) for c in it.get("commands", [])], "findings": findings,
                  "auto": not findings, "ts": now_iso()}
    write_json(inst.turn_dir() / "review.json", {**review_rec, "need_user": len(findings),
                                                   "caution": 0, "accepted": not findings})
    if findings:
        text = "以下内容需要用户批准后才会执行：\n" + "\n".join(f"- {x}" for x in findings)
        machine.go(inst, "AWAITING_USER", by="driver", reason=f"{len(findings)} 项需要用户批准")
        inst.state["needs_text"] = text
        inst.save()
        return "user", text
    flags["review_accepted"] = True
    inst.event("review", accepted=True, auto=True, by="driver")
    return _after_review(ctx, it, d)


def _independent_review(ctx, src, zip_path) -> list:
    """可选：另一个模型系列审查本轮版本包的改动。它只能增加阻力（阻止或提出关注点），不能单独放行。"""
    import difflib
    inst = ctx.inst
    parts = []
    for f in sorted(Path(src).rglob("*")):
        if not f.is_file() or f.suffix not in policy.CODE_SUFFIXES | {".md"} or f.name == "RELEASE.json":
            continue
        rel = str(f.relative_to(src))
        old_p = inst.root / rel
        try:
            new = f.read_text(encoding="utf-8").splitlines()
            old = old_p.read_text(encoding="utf-8").splitlines() if old_p.exists() else []
        except UnicodeDecodeError:
            continue
        parts += list(difflib.unified_diff(old, new, f"a/{rel}", f"b/{rel}", lineterm=""))
    diff = inst.turn_dir() / "release.diff"
    diff.write_text("\n".join(parts)[:400000], encoding="utf-8")
    r = anomaly.call("review", ["--project", inst.name, "--turn", inst.state["turn"], "--release", zip_path,
                                "--diff", diff], timeout=anomaly.transport_cfg().get("review_timeout_s", 900))
    if r["code"] != 0 or r["data"].get("verdict") not in ("pass", "concerns", "block"):
        decide(inst, "review", "独立审查不可用，按规则继续", f"适配器返回 {r['code']}：{r['raw'][:120]}", str(diff), "未审查")
        return []
    write_json(inst.turn_dir() / "review_adapter.json", r["data"])
    v, model = r["data"]["verdict"], r["data"].get("model") or "未注明模型"
    decide(inst, "review", f"独立审查结论：{v}", "；".join(r["data"].get("notes", [])[:3]) or "无说明",
           "review_adapter.json", "阻止" if v == "block" else ("有关注点" if v == "concerns" else "通过"))
    return [f"独立审查（{model}）认为应阻止：" + "；".join(r["data"].get("notes", [])[:5])] if v == "block" else []


def _after_review(ctx, it, d):
    inst, st = ctx.inst, ctx.st
    if st.get("release_path"):
        machine.go(inst, "APPLY", by="driver", reason="评审通过")
        return "progress", "评审通过，落地版本"
    if it.get("commands"):
        machine.go(inst, "EXECUTE", by="driver", reason="评审通过")
        return "progress", "评审通过，开始执行"
    machine.go(inst, "VERIFY", by="driver", reason="本轮没有命令")
    return "progress", "本轮没有命令，直接验收"


def _to_report(ctx):
    inst, st = ctx.inst, ctx.st
    it = read_json(inst.turn_dir() / "intake.json", {}) or {}
    failed = bool(st.get("force_report")) or _exec_failures(inst, it.get("directives") or {})[0] or any(
        i.get("verdict") in ("fail", "deviation") for i in ops.verify_items(inst))
    ch = roles.choose(inst, "report_fail" if failed else "report_ok", claude_suggestion=it.get("effort"),
                      consecutive_failed=st.get("consecutive_failed", 0))
    decide(inst, "effort", f"回报使用 {ch['effort']}（角色 {ch['role']}）", "；".join(ch["reasons"]))
    p = ops.compose_report(inst, by="driver", effort=ch["effort"])
    eff = ch["effort"]
    inst.state["outgoing"] = {"turn": inst.state["turn"], "file": str(p), "kind": "report", "effort": eff,
                              "role": ch["role"], "reasons": ch["reasons"], "model": ch.get("model")}
    _clear_turn(inst)
    return "progress", f"已生成回报（档位 {eff}）"


def _clear_turn(inst):
    st = inst.state
    for k in ("exec_next", "exec_inflight", "release_path", "force_report", "delivered", "needs_text"):
        st.pop(k, None)
    inst.save()


def _save_dirty(ctx) -> list:
    """1.6.1（第 34 轮 AP-2）：落地前工作区有未提交的改动时，存成一个单独的提交并记入回报，不再阻塞等人。
    改动里有疑似密钥时不存、什么都不做，由 apply_zip 照旧报 AP-2。返回存下的文件。"""
    inst = ctx.inst
    if not (inst.root / ".git").exists():
        return []
    rows = release._dirty(inst.root, inst.protect())
    files = [r[3:].strip().strip('"').split(" -> ")[-1] for r in rows]
    if not files:
        return []
    from . import sync
    hits = []
    for rel in files:
        p = inst.root / rel
        if p.is_file():
            try:
                hits += sync._scan_text(rel, p.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                continue
    if hits:
        decide(inst, "apply", "工作区改动含疑似密钥，不自动保存", "；".join(hits[:3]), "", "停下等人")
        return []
    from .core import git_identity
    git(["add", "-A", "--"] + files, inst.root)
    git(git_identity() + ["commit", "-q", "-m", f"wf: 落地前保存工作区里未经交付的改动（{len(files)} 个文件）"], inst.root)
    head = git(["rev-parse", "--short", "HEAD"], inst.root).stdout.strip()
    detail = "、".join(files[:8]) + (f" 等 {len(files)} 个" if len(files) > 8 else "")
    inst.event("auto", rule="APPLY_DIRTY", action="save", detail=detail, commit=head, by="driver")
    decide(inst, "apply", "落地前自动保存工作区改动", detail, head, "已提交，继续落地")
    ops.add_note(inst, "deviation", f"[APPLY_DIRTY] 落地前工作区有未经交付的改动，已自动存成提交 {head}：{detail}。"
                 "仓库指纹会包含这些文件", by="driver")
    return files


def _auto_approve_neutral(ctx, before: dict, version) -> None:
    """1.6.1：版本包改动了白名单或目标、但没有扩大权限（评审时已判定）时，落地后由驱动器自动批准。"""
    inst = ctx.inst
    for kind in ("policy", "goals"):
        b = before.get(kind) or {}
        if not b.get("approved") or policy.sha256(inst.wf / f"{kind}.json") == b.get("sha"):
            continue
        # 评审时已对版本包里的同一份内容判定过；这里只确认确实没有扩大
        new = read_json(inst.wf / f"{kind}.json")
        if new is None:
            continue
        wid = policy.widening(ctx.pol, new) if kind == "policy" else policy.goals_widening(new, ctx.pol)
        if wid:
            continue
        policy.auto_approve(inst, kind, f"随 {version} 落地；没有新增命令、没有放宽上限")
        decide(inst, "approve", f"自动批准 {kind}.json（不扩大权限）", f"随 {version} 落地", f"workflow/{kind}.json", "已批准")


def h_apply(ctx):
    inst, st = ctx.inst, ctx.st
    path = st.get("release_path")
    if not path:
        machine.go(inst, "EXECUTE", by="driver", reason="没有待落地的版本")
        return "progress", "没有待落地的版本"
    gates = ctx.pol.get("gates") or []
    prev = inst.version()
    base = _run_gates(ctx, gates, "baseline") if gates else {}
    try:
        before = {k: {"approved": policy.status(inst, k)["approved"], "sha": policy.sha256(inst.wf / f"{k}.json")}
                  for k in ("policy", "goals")}
        _save_dirty(ctx)   # 1.6.1：工作区里未经交付的改动先存成一个提交（含疑似密钥时不存，照旧停下）
        r = release.apply_zip(inst, Path(path), by="driver")
        _auto_approve_neutral(ctx, before, r.get("version"))
    except WfError as e:
        res = _anomaly(ctx, {"source": "apply", "text": str(e)}, str(e).splitlines()[0])
        return _to_report(ctx) if res[0] == "report" else res
    st = inst.state
    st.pop("release_path", None)
    if gates:
        after = _run_gates(ctx, gates, "after")
        regress = [g for g, ok in after.items() if not ok and base.get(g)]
        still = [g for g, ok in after.items() if not ok and not base.get(g)]
        if regress:
            rb = release.rollback(inst, prev, by="driver")
            tag = r["version"]
            if git(["rev-parse", "-q", "--verify", f"refs/tags/{tag}"], inst.root, check=False).returncode == 0:
                git(["tag", f"{tag}-reverted-{now().strftime('%Y%m%d%H%M%S')}", tag], inst.root, check=False)
                git(["tag", "-d", tag], inst.root, check=False)
            decide(inst, "revert", f"自动回退 {tag} → {prev}", f"门禁在落地前通过、落地后失败：{'、'.join(regress)}",
                   f"evidence/turn-{st['turn']:03d}/gate-*", f"已回退（{rb['commit']}）")
            inst.event("auto", rule="GATE_REGRESSION", action="revert", detail=f"{tag} 使门禁 {'、'.join(regress)} 失败，已回退到 {prev}",
                       by="driver")
            ops.add_note(inst, "deviation", f"[GATE_REGRESSION] {tag} 落地后门禁 {'、'.join(regress)} 失败（落地前通过），已自动回退到 {prev}", by="driver")
            st = inst.state
            st["force_report"] = True
            inst.save()
            return _to_report(ctx)
        if still:
            decide(inst, "gate", f"门禁 {'、'.join(still)} 未通过，但落地前就未通过", "不是本次版本引起的回归，不回退",
                   f"evidence/turn-{st['turn']:03d}/gate-*", "继续执行并回报")
            ops.add_note(inst, "deviation", f"门禁 {'、'.join(still)} 在落地前后都未通过（不是本次版本引起）", by="driver")
            st = inst.state
            st["force_report"] = True
        else:
            decide(inst, "gate", f"{r['version']} 通过全部门禁", f"{len(after)} 道门禁", f"evidence/turn-{st['turn']:03d}/gate-after-*", "通过")
        inst.save()
    d = (read_json(inst.turn_dir() / "intake.json", {}) or {}).get("directives") or {}
    if d.get("fingerprint"):
        fp = ops.fingerprint(inst.root)["digest"]
        if fp != d["fingerprint"]:
            _anomaly(ctx, {"source": "apply", "kind": "fingerprint_mismatch"}, f"声明 {d['fingerprint']}，实际 {fp}")
    machine.go(inst, "EXECUTE", by="driver", reason=f"已落地 {r['version']}")
    return "progress", f"已落地 {r['version']}（{r['commit'] or '无变化'}）"


def _run_gates(ctx, gates, label) -> dict:
    """门禁：用户批准的检查（写在 policy.json 的 gates 中，版本包无法单方面修改）。返回 {门禁名: 是否通过}。"""
    inst = ctx.inst
    out = {}
    d = inst.turn_dir()
    for i, g in enumerate(gates, 1):
        name = g.get("name") or f"gate{i}"
        try:
            r = subprocess.run(["bash", "-c", g["cmd"]], cwd=str(inst.root), capture_output=True, text=True,
                               timeout=g.get("timeout_s", ctx.pol.get("command_timeout_s", 900)), env=_exec_env(inst))
            code, text = r.returncode, r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            code, text = 124, "超时"
        ok, why = goals.judge(g.get("pass", {"exit": 0}), code, text)
        (d / f"gate-{label}-{i}.txt").write_text(f"$ {g['cmd']}\nexit={code}\n{text}", encoding="utf-8")
        out[name] = ok
        inst.event("gate", label=label, gate=name, ok=ok, why="；".join(why) or None, by="driver")
    return out


def h_execute(ctx):
    inst = ctx.inst
    st = inst.state
    it = read_json(inst.turn_dir() / "intake.json", {}) or {}
    d = it.get("directives") or {}
    cmds = it.get("commands", [])
    inflight = st.get("exec_inflight")
    if inflight:
        st.pop("exec_inflight", None)
        st["exec_next"] = inflight["i"] + 1
        inst.save()
        done = fidelity.exec_completed(ops.list_execs(inst), inflight["i"] + 1)
        if done is not None:
            # 1.5.0：命令其实已执行完（证据里有退出码），驱动器是在命令结束之后被打断的，不算命令中断（第 31 轮误报）
            inst.event("exec_resumed", cid=done.get("id"), exit_code=done.get("exit_code"),
                       note=f"第 {inflight['i'] + 1} 条命令已执行完毕，中断发生在命令结束之后", by="driver")
            if done.get("exit_code") not in (0, None):
                _anomaly(ctx, {"source": "exec", "code": done["exit_code"]},
                         f"第 {inflight['i'] + 1} 条命令「{inflight['cmd']}」退出码 {done['exit_code']}")
        else:
            _anomaly(ctx, {"source": "driver", "kind": "exec_interrupted"},
                     f"第 {inflight['i'] + 1} 条命令「{inflight['cmd']}」执行时驱动器被中断，未重跑")
        st = inst.state
    i = st.get("exec_next", 0)
    failed = False
    timeout = d.get("timeout") or ctx.pol.get("command_timeout_s", 900)
    while i < len(cmds):
        if ctx.time_left() <= 0:
            return "progress", f"已执行到第 {i} 条，本次时间用完，下次继续"
        if policy.classify(cmds[i], ctx.pol).get("manual"):
            ops.add_note(inst, "info", f"第 {i + 1} 条「{cmds[i]}」是手动步骤，驱动器未执行（F21）", by="driver")
            decide(inst, "exec", f"跳过第 {i + 1} 条：{cmds[i]}", "会改变工作流状态的 wf 命令只能在驱动器之外手动执行", "", "已跳过")
            i += 1
            st = inst.state
            st["exec_next"] = i
            inst.save()
            continue
        st["exec_inflight"] = {"i": i, "cmd": cmds[i], "ts": now_iso()}
        inst.save()
        rc = ops.run_exec(inst, cmds[i], label=f"#{i + 1}", timeout=timeout, by="driver")
        st = inst.state
        st.pop("exec_inflight", None)
        i += 1
        st["exec_next"] = i
        inst.save()
        if not _exec_ok(inst, d, f"#{i}", rc):   # 1.5.2（W21）：有判据的命令以判据为准
            if rc not in (0, None):   # 1.5.7：退出码为 0、只是没通过判据时由验收报 CHECK_FAIL，不再报“命令执行失败，退出码 0”
                _anomaly(ctx, {"source": "exec", "code": rc}, f"第 {i} 条命令「{cmds[i - 1]}」退出码 {rc}")
            failed = True
            if not d.get("continue_on_error"):
                break
    machine.go(inst, "VERIFY", by="driver", reason="执行结束" + ("（有失败）" if failed else ""))
    return "progress", f"执行结束：{i} / {len(cmds)} 条" + ("，有失败" if failed else "")


def _exec_ok(inst, d, label, rc) -> bool:
    """1.5.2（W21）：一条命令算不算成功。有判据（验收 N：cN …）的命令以判据为准，例如预期无匹配的 grep 写 exit=1；
    没有判据的命令以退出码为准。"""
    n = str(label).lstrip("#")
    chk = next((c for c in (d.get("checks") or {}).values() if str(c.get("cmd")) == n), None)
    if not chk:
        return rc in (0, None)
    e = next((x for x in ops.list_execs(inst) if x and str(x.get("label")) == f"#{n}"), None)
    if not e:
        return rc in (0, None)
    vdir = inst.turn_dir()
    out = ""
    for suf in (".out", ".err"):
        f = vdir / f"{e['id']}{suf}"
        if f.exists():
            out += f.read_text(encoding="utf-8", errors="replace")
    return goals.judge(chk["cond"], e.get("exit_code"), out)[0]


def _exec_failures(inst, d) -> tuple:
    """1.5.2（W21）：返回 (有没有失败的命令, 有没有未关联到清单项的失败命令)。整轮成败与熔断只看后者与验收结果；
    关联到清单项、但没有判据的命令（暂缓项）由 Claude 看原文判断，不再一票否决整轮。"""
    linked = {str(i.get("n")) for i in ops.verify_items(inst)} | {str(c.get("cmd")) for c in (d.get("checks") or {}).values()}
    bad = [str(e.get("label") or "").lstrip("#") for e in ops.list_execs(inst)
           if e and not _exec_ok(inst, d, e.get("label") or "", e.get("exit_code"))]
    return bool(bad), any(n not in linked for n in bad)


def h_verify(ctx):
    inst = ctx.inst
    st = inst.state
    it = read_json(inst.turn_dir() / "intake.json", {}) or {}
    d = it.get("directives") or {}
    execs = {e.get("label"): e for e in ops.list_execs(inst) if e}
    vdir = inst.turn_dir()
    failed_items = 0
    for item in ops.verify_items(inst):
        if item.get("verdict"):
            continue
        chk = (d.get("checks") or {}).get(str(item["n"]))
        if not chk:
            ops.verify_set(inst, item["n"], "skip", note="没有机器判据，原文见回报，由 Claude 判断", by="driver")
            continue
        e = execs.get(f"#{chk['cmd']}")
        if not e:
            ops.verify_set(inst, item["n"], "fail", note=f"对应的命令 c{chk['cmd']} 没有执行", by="driver")
            failed_items += 1
            continue
        out = (vdir / f"{e['id']}.out").read_text(encoding="utf-8", errors="replace") + \
            (vdir / f"{e['id']}.err").read_text(encoding="utf-8", errors="replace")
        ok, why = goals.judge(chk["cond"], e.get("exit_code"), out)
        ops.verify_set(inst, item["n"], "pass" if ok else "fail", evidence=e["id"], note=None if ok else "；".join(why),
                       by="driver")
        if not ok:
            failed_items += 1
    st = inst.state
    # 1.5.2（W21）：整轮成败与熔断只计验收未通过的项与未关联到清单项的失败命令（与文档“连续 3 轮验收未通过”一致）；
    # 任何失败的命令仍会让本轮之后改为回报
    exec_failed, unlinked_failed = _exec_failures(inst, d)
    round_failed = bool(failed_items or unlinked_failed or st.get("force_report"))
    if failed_items:
        inst.event("auto", rule="CHECK_FAIL", action="report", detail=f"{failed_items} 项验收未通过", by="driver")
    inst.event("round_result", ok=not round_failed, failed_items=failed_items or None, by="driver")
    # 熔断：同一问题连续多轮没有解决
    st["consecutive_failed"] = st.get("consecutive_failed", 0) + 1 if round_failed else 0
    limit = int(ctx.pol.get("max_consecutive_failed_rounds", 3))
    # 1.6.7（collab-wf-dev 第 80、81 轮）：先登记执行端待办，再判定目标。此前登记在目标判定之后，目标已达成时直接
    # 进入 DONE 并返回，待办被静默丢弃（不进 inbox，wf task 也看不到）。登记只写待办与 tasks_opened 事件，
    # 不参与目标判定、熔断与“之后”的选择。
    from . import tasks as _tasks
    added = _tasks.add(inst, st["turn"], d.get("executor_tasks") or {})
    # 目标
    prog = goals.run_checks(inst, timeout=ctx.pol.get("command_timeout_s", 900)) if policy.status(inst, "goals")["approved"] else goals.progress(inst)
    if prog.get("reached") and (round_failed or exec_failed):
        # 1.6.8（第 82、83 轮发现）：目标判据通过、但本轮有验收未通过或命令失败时，不进入 DONE，照常回报。此前目标判定在前，
        # 失败被 DONE 静默吞掉。DONE 只在“目标达成且本轮干净”时进入；下一轮干净且目标仍达成，照常进入 DONE。
        # 执行端待办已在上面登记；熔断照常计数。
        decide(inst, "after", "目标判据通过，但本轮有失败：先回报，不进入目标达成",
               f"验收未通过 {failed_items} 项；命令失败：{'有' if exec_failed else '无'}", "verify.json", "回报")
    if prog.get("reached") and not (round_failed or exec_failed):
        if added:
            # DONE 的语义不变：目标达成、自动化停下、等用户。待办留在 inbox 里可见；不等待、不自动回报、不推进版本
            decide(inst, "after", f"目标已达成：{len(added)} 项执行端待办已登记进 inbox，不等待、不自动回报",
                   "全部目标判据通过；Claude 的【驱动指令】列了执行端待办", "；".join(t["id"] for t in added), "目标达成")
        machine.go(inst, "DONE", by="driver", reason="全部目标判据通过")
        _clear_turn(inst)
        return "done", "全部目标判据通过" + (f"；已登记 {len(added)} 项执行端待办（wf task）" if added else "")
    if prog.get("approved"):
        best = st.get("best_progress", 0)
        if prog["done"] > best:
            st["best_progress"], st["rounds_no_progress"] = prog["done"], 0
        else:
            st["rounds_no_progress"] = st.get("rounds_no_progress", 0) + 1
    inst.save()
    if st["consecutive_failed"] >= limit:
        decide(inst, "breaker", "熔断：停下等用户", f"连续 {st['consecutive_failed']} 轮验收未通过", "看板“本轮执行”", "等待用户")
        return _breaker(ctx, f"连续 {st['consecutive_failed']} 轮验收未通过（policy.max_consecutive_failed_rounds = {limit}）")
    np_limit = int(ctx.pol.get("max_rounds_without_goal_progress", 8))
    if prog.get("approved") and st.get("rounds_no_progress", 0) >= np_limit:
        return _breaker(ctx, f"连续 {st['rounds_no_progress']} 轮目标没有进展（policy.max_rounds_without_goal_progress = {np_limit}）")
    after = d.get("after") or "report"   # 1.6.7：执行端待办已在目标判定之前登记
    if round_failed or exec_failed:
        after = "report"
    left = _tasks.open_items(inst)
    if after in ("idle", "executor") and left:
        # 1.6.6（第 58 轮）：有执行端待办时不进空闲：等它们做完自动生成回报（此前要执行端记得 wf kick，漏了就停在空闲）
        lim = int(ctx.pol.get("executor_task_timeout_s", 0) or 0) or _tasks.TIMEOUT_S
        until = now() + timedelta(seconds=lim)
        _clear_turn(inst)
        st["await_executor"] = st["turn"]
        decide(inst, "after", f"等执行端完成 {len(left)} 项待办，做完自动回报", "Claude 的【驱动指令】列了执行端待办",
               "；".join(t["id"] for t in left), f"最迟等到 {until.isoformat(timespec='minutes')}")
        machine.go(inst, "WAIT_SCHEDULE", by="driver", reason="等执行端完成待办", until=until.isoformat(),
                   wait_reason="执行端待办")
        return "progress", f"验收通过，等执行端完成 {len(left)} 项待办（最迟 {until.isoformat(timespec='minutes')}）"
    if after == "executor":
        after = "report"
    if after == "wait":
        until = parse_iso(d.get("until"))
        if until is None:
            ops.add_note(inst, "deviation", f"【驱动指令】的等待时间无法解析：{d.get('until')}", by="driver")
            return _to_report(ctx)
        _clear_turn(inst)
        decide(inst, "after", f"等待到 {until.isoformat(timespec='minutes')}", f"Claude 的【驱动指令】：{d.get('wait_reason')}；本轮全部通过", "", "等待")
        machine.go(inst, "WAIT_SCHEDULE", by="driver", reason=d.get("wait_reason"), until=until.isoformat(),
                   wait_reason=d.get("wait_reason"))
        return "progress", f"验收通过，等待到 {until.isoformat(timespec='minutes')}"
    if after == "idle":
        _clear_turn(inst)
        machine.go(inst, "IDLE", by="driver", reason="验收通过，Claude 指定本轮之后空闲")
        return "progress", "验收通过，进入空闲"
    return _to_report(ctx)


def _breaker(ctx, why):
    inst = ctx.inst
    machine.go(inst, "AWAITING_USER", by="driver", reason=why)
    inst.state["needs_text"] = why + "。自动化已停下：用户查看本轮证据（看板“本轮执行”）后决定继续（wf approve）或接管（wf pause）。"
    inst.save()
    return "user", why


HANDLERS = {
    "IDLE": h_idle, "DONE": h_done, "AWAITING_USER": h_user, "BLOCKED": h_user,
    "WAIT_SCHEDULE": h_wait_schedule, "QUOTA_WAIT": h_quota_wait, "COMPOSE": h_compose, "SEND": h_send,
    "WAIT_REPLY": h_wait_reply, "INTAKE": h_intake, "REVIEW": h_review, "APPLY": h_apply,
    "EXECUTE": h_execute, "VERIFY": h_verify,
}


# ---------------- 多项目调度 ----------------

def tick(projects: list, max_seconds=900) -> tuple:
    """一次唤醒推进多个项目。顺序：可以立即行动的在前（按 priority 从高到低），等待中的其后，需要用户的跳过。"""
    t0 = time.time()
    rows = []
    for inst in projects:
        if inst.cfg.get("auto") is False:
            rows.append((inst, -1, "未启用自动驱动（wf.json auto=false）"))
            continue
        s = inst.state
        st_name = s.get("state", "IDLE")
        actionable = st_name in ("COMPOSE", "SEND", "INTAKE", "REVIEW", "APPLY", "EXECUTE", "VERIFY") or (
            st_name in ("WAIT_SCHEDULE", "QUOTA_WAIT") and parse_iso(s.get("until")) and now() >= parse_iso(s["until"])) or (
            st_name == "WAIT_REPLY")
        rank = (2 if actionable else 1) * 1000 + int(inst.cfg.get("priority", 0))
        rows.append((inst, rank, ""))
    rows.sort(key=lambda r: -r[1])
    results, codes = [], []
    for inst, rank, skip in rows:
        if skip:
            results.append({"project": inst.name, "exit": None, "note": skip})
            continue
        left = max_seconds - (time.time() - t0)
        if left <= 5:
            results.append({"project": inst.name, "exit": None, "note": "本次唤醒时间已用完，下次再推进"})
            continue
        code, info = run(inst, max_seconds=min(left, max_seconds), linger=True)
        codes.append(code)
        results.append({"project": inst.name, "exit": code, "note": info["note"], "explain": info.get("explain")})
    if not codes:
        overall = 0
    elif 20 in codes:
        overall = 20
    elif 0 in codes:
        overall = 0
    elif all(c == 30 for c in codes):
        overall = 30
    else:
        overall = 10
    return overall, results
