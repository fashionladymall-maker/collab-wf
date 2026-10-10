【回报 · 第 7 轮 · v0.1】档位：high

① 环境障碍与偏离
无。

② 关键数字与终端原文

1) driver.py 第 1–46 行（导入、退出码、Ctx）。 —— 通过
```
$ sed -n 1,46p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
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

from . import anomaly, fidelity, goals, machine, ops, policy, release, roles
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
exit=0  耗时 0.0s
```

2) driver.py 第 316–367 行（h_compose）。 —— 通过
```
$ sed -n 316,367p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
def h_compose(ctx):
    inst, st = ctx.inst, ctx.st
    out = st.get("outgoing")
    if st.get("compose_reason") == "resend" and out:
        st.pop("compose_reason", None)
        machine.go(inst, "SEND", by="driver", reason="重发上一条消息（叶子被拒后）")
        return "progress", "重发上一条消息"
    if not out or out.get("turn") != st["turn"]:
        body, kind, effort = _compose_text(ctx)
        if body is None:
            return "user", "有一条待发消息需要内容：用户或执行端用 wf kick --message <任务文件> 指定"
        p = inst.outbox / f"turn-{st['turn']:03d}.md"
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
        try:
            from .admin import bootstrap
            b = bootstrap(inst, why)
            ev = f"{b['message'].name}，{b['zip'].name}"
        except WfError as e:
            ev = f"恢复包生成失败：{e}"
        decide(inst, "context", "停下，建议开新对话", why, ev, "等待用户")
        return _anomaly(ctx, {"source": "context", "kind": "conversation_full"},
                        why + f"。恢复包已在 workflow/outbox/（{ev}）：用户开新对话、上传 zip、发送说明消息后运行 wf conv new --note …，再 wf approve")
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
        return _anomaly(ctx, {"source": "budget", "kind": "daily_limit"},
                        f"今日自动发送已达 {limit} 轮（policy.max_rounds_per_day），为防止失控已停下")
    machine.go(inst, "SEND", by="driver", reason="消息已通过发送前检查")
    return "progress", f"消息已就绪（{out['kind']}，档位 {out['effort']}）"
exit=0  耗时 0.0s
```

3) driver.py 第 580–700 行（h_review、_to_report、_clear_turn）。 —— 通过
```
$ sed -n 580,700p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
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
    blocking = [x for x in it.get("user_decisions", []) if "前置" in x or "阻塞" in x]
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
    failed = bool(st.get("force_report")) or any(
        (e or {}).get("exit_code") not in (0, None) for e in ops.list_execs(inst)) or any(
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


def h_apply(ctx):
exit=0  耗时 0.0s
```

4) driver.py 第 827–901 行（h_verify，整轮成败的判定）。 —— 通过
```
$ sed -n 827,901p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
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
    exec_failed = any(x.get("exit_code") not in (0, None) for x in execs.values())
    round_failed = bool(failed_items or exec_failed or st.get("force_report"))
    if failed_items:
        inst.event("auto", rule="CHECK_FAIL", action="report", detail=f"{failed_items} 项验收未通过", by="driver")
    inst.event("round_result", ok=not round_failed, failed_items=failed_items or None, by="driver")
    # 熔断：同一问题连续多轮没有解决
    st["consecutive_failed"] = st.get("consecutive_failed", 0) + 1 if round_failed else 0
    limit = int(ctx.pol.get("max_consecutive_failed_rounds", 3))
    # 目标
    prog = goals.run_checks(inst, timeout=ctx.pol.get("command_timeout_s", 900)) if policy.status(inst, "goals")["approved"] else goals.progress(inst)
    if prog.get("reached"):
        machine.go(inst, "DONE", by="driver", reason="全部目标判据通过")
        _clear_turn(inst)
        return "done", "全部目标判据通过"
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
    after = d.get("after") or "report"
    if round_failed:
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
exit=0  耗时 0.0s
```

5) wf.py 第 520–545 行（wf report）。 —— 通过
```
$ sed -n 520,545p /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
                v = ops.VERDICTS.get(i.get("verdict"), "未验收")
                print(f"  {i['n']:>2}. [{v}] {i['item']}" + (f"  证据 {i['evidence']}" if i.get("evidence") else ""))
            if execs:
                print("本轮命令证据：" + "  ".join(f"{k}(exit={v['exit_code']})" for k, v in execs.items()))
        else:
            if args.n is None or not args.verdict:
                raise WfError("用法：wf verify set <序号> pass|fail|deviation|skip [--evidence c01] [--note …]")
            ops.verify_set(inst, args.n, args.verdict, args.evidence, args.note)
            left = [i for i in ops.verify_items(inst) if not i.get("verdict")]
            print(f"已记录第 {args.n} 项：{ops.VERDICTS[args.verdict]}。" + (f"还有 {len(left)} 项未验收。" if left else "清单已全部验收，可以 wf report。"))
    elif c == "note":
        ops.add_note(_p(args), args.kind, args.text)
        print("已记录")
    elif c == "report":
        p = ops.compose_report(_p(args), args.out)
        print(f"回报草稿：{p}\n检查补充后：wf check-outgoing {p} --kind report")
    elif c == "check-outgoing":
        r = ops.check_outgoing(Path(args.file), args.kind)
        if args.json:
            out(r)
        else:
            for s in r["secrets"]:
                print(f"✗ 密钥：{s}")
            for s in r["problems"]:
                print(f"✗ {s}")
            for s in r["warnings"]:
exit=0  耗时 0.0s
```

6) wf.py 第 700–730 行（pause、unpause、undo）。 —— 通过
```
$ sed -n 700,730p /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
            st["budget"]["rounds"] = 0
        st.pop("needs_text", None)
        inst.save()
        st = machine.resume(inst, args.said, by="user")
        print(f"已批准，回到 {st['state']}。驱动器下次运行时继续。")
    elif c == "pause":
        inst = _p(args)
        inst.state["paused"] = {"ts": tool_now(), "note": args.note}
        inst.event("pause", note=args.note, by="user")
        print("已暂停自动化。驱动器不会再推进这个项目，直到 wf unpause。手动命令仍可使用。")
    elif c == "unpause":
        inst = _p(args)
        if not inst.state.get("paused"):
            raise WfError("当前没有暂停。")
        it = asks.verify(inst, "unpause", args.code)
        _record_user(inst, "unpause", it, args.said)
        inst.state.pop("paused", None)
        inst.event("unpause", note=args.said, by="user")
        print("已恢复自动化。")
    elif c == "undo":
        inst = _p(args)
        from wflib.core import git
        subjects = git(["log", "--format=%s", "--grep=^release v"], inst.root, check=False).stdout.splitlines()
        vers = [l.split()[1] for l in subjects if l.startswith("release v")]
        if len(vers) < 2:
            raise WfError("没有可以回退到的上一个版本。")
        inst.state["paused"] = {"ts": tool_now(), "note": f"undo：{args.note}"}
        inst.event("pause", note=f"undo：{args.note}", by="user")
        r = release.rollback(inst, vers[1])
        print(f"已暂停自动化并回退：{r['from']} → {vers[1]}（新提交 {r['commit']}）。检查后 wf unpause。")
        for h in r["hints"]:
exit=0  耗时 0.0s
```

③ 需要决策的点
无。

④ 需要注意（Attention）
- 当前对话已累计约 127200 字符、6 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

附：仓库指纹 4d1aa87b9b11c651（v0.1，4 个文件）
附：发送前额度 5 小时窗口 38.0%，7 天窗口 60.0%
