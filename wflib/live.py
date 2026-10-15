"""1.9.0（W87，collab-wf-dev 的 LIVE.md）：实景验收的测量。只读，不改任何状态。

逐轮核对 LIVE.md 第 1 节的 10 段判据，证据都来自真实记录：事件流、轮次记录、证据目录、审查结论留档、同步历史、
适配器动作日志（transport.json 的 log）。拿不到证据的一段按“不达标（无法核对）”算——实景验收只认证据。
连续达标的轮数达到 --rounds（默认 3）才算通过；1.9.0 之前的轮次缺少新记录，会如实显示无法核对。

B1-C：每段新增 status（passed | failed | not_applicable | attempt_failed），第 4 段不适用时带 na 四项；返回结构新增 summary
（机器摘要，经现有 wf --json live-check 输出）。ok、why、streak、passed 与人工输出都不变。summary 是测量报告，不是凭证：
goals 只在本进程内直接调用 check() 时采用它，从不读取文件里的摘要。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .core import LOCAL, parse_iso, read_json

MANUAL_TYPES = ("transition", "exec", "apply", "rollback", "conv_new", "conv_bind", "send_intent_cleared",
                "resend_not_landed", "pause", "unpause", "ref_registered", "report_draft")
AUTO_BY = ("driver",)   # 1.9.1：wf 命令（含 kick 生成回报）是有人在命令行触发的，不算自动


def _manual(e: dict) -> bool:
    # 1.9.3（W93）：有事件身份（actor）时按它判——驱动器进程里的 wf 内部转换不算人工；没有身份的旧事件按 by 判
    if e.get("type") not in MANUAL_TYPES:
        return False
    if e.get("by") in ("executor", "user"):
        return True
    if e.get("actor"):
        return e["actor"] != "driver"
    return e.get("by") not in AUTO_BY


def _jsonl(p) -> list:
    out = []
    try:
        for line in Path(p).read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def _head_at(root, ts):
    """1.9.4（审查 concerns）：项目在 ts 时刻的 HEAD（沿第一父提交，取提交时刻不晚于 ts 的最近一个）；没有版本库或查不到时返回空。"""
    from .core import git
    if not ts or not (Path(root) / ".git").exists():
        return ""
    try:
        return (git(["rev-list", "-1", "--first-parent", f"--before={ts}", "HEAD"], root, check=False).stdout or "").strip()
    except OSError:
        return ""


def _review_timing(rr: dict, e: dict) -> str:
    """1.9.4（审查 concerns）：审查结论要在这次安装命令里、且在开始复制文件之前取得；空时刻不放过。返回问题说明，没有问题返回空。"""
    from datetime import timedelta
    rts, ets = parse_iso(rr.get("ts")), parse_iso(e.get("ts"))
    if not rts:
        return "（结论没有时刻）"
    if not ets:
        return "（安装命令没有时刻）"
    start = ets - timedelta(seconds=float(e.get("duration_s") or 0))
    if rts < start - timedelta(seconds=1):
        return "（结论早于这次安装命令，不是这次取得的）"
    inst_at = parse_iso(rr.get("install_started_at"))
    if inst_at and rts > inst_at:
        return "（结论晚于开始安装）"
    if not inst_at and rts > ets:
        return "（结论晚于安装命令结束）"
    return ""


def _adapter_log():
    from . import anomaly
    p = (anomaly.transport_cfg() or {}).get("log")
    return Path(p).expanduser() if p else None


def _mine(x: dict, name: str) -> bool:
    return x.get("project", name) == name


def check(inst, rounds: int = 3, budget_s: int = 300, *, _inputs=None) -> dict:
    # 1.9.3：按执行端第 123 轮的复核再修——不跳过失败的轮次（按轮号连续计）；日志条目必须带项目字段；回复要核父子 uuid；
    # 审查核版本包哈希与先后；待办按事件核关闭时刻；发现延迟不得为负；同步要带当时的 HEAD；
    # 连续达标的轮次里必须至少各有一次工具安装与项目落地；人工判定看事件身份（actor），驱动器进程里的 wf 内部转换不算人工
    import hashlib
    # C2c-1: the optional frozen reader never opens current files during evaluation.
    read_doc = _inputs.json if _inputs else read_json
    read_log = _inputs.jsonl if _inputs else _jsonl
    exists = _inputs.exists if _inputs else lambda p: Path(p).exists()
    read_bytes = _inputs.bytes if _inputs else lambda p: Path(p).read_bytes()
    head_at = _inputs.head_at if _inputs else _head_at
    state = _inputs.state if _inputs else inst.state
    ev = read_log(inst.events_path)
    if _inputs is None and any(e.get("type") == "exec" and e.get("install_exec") is not None for e in ev):
        try:
            return _evaluate_snapshot(_capture_inputs(inst, rounds=rounds, budget_s=budget_s))["report"]
        except _InputProblem as exc:
            missing = [f"实景输入 {exc.state}：{exc.source}：{exc.detail}"]
            return {"rounds": [], "streak": 0, "need": rounds, "missing": missing, "passed": False,
                    "summary": _summary(inst, [], [], rounds, 0, missing, False),
                    "input_problem": {"state": exc.state, "source": exc.source, "detail": exc.detail}}
    histories = _inputs.install_histories(inst, ev) if _inputs else None
    history_entries = (histories or {}).get("entries", [])
    turns = read_log(inst.turns_path)
    sent, done = {}, {}
    for r in turns:
        t = r.get("turn")
        if r.get("status") == "sent":
            sent.setdefault(t, []).append(r)
        if r.get("ts_done"):
            done[t] = r
    allt = sorted(t for t in sent if isinstance(t, int))
    if allt and allt[-1] not in done:
        allt = allt[:-1]   # 最新一轮还在等回复，不计
    logp = _inputs.adapter if _inputs else _adapter_log()
    alog = read_log(logp) if logp and exists(logp) else None
    has_proj = bool(alog) and any("project" in x for x in alog)
    hist = read_log(LOCAL / "sync" / f"{inst.name}.history.jsonl")
    state_tasks = {x.get("id"): x for x in (state.get("executor_tasks") or [])}
    closes = {}
    for e in ev:
        if e.get("type") in ("task_done", "task_cancelled", "task_superseded") and e.get("id"):
            closes.setdefault(e["id"], str(e.get("ts") or ""))
    results = []
    for t in allt:
        crit = []

        def add(n, ok, why, status=None, na=None):
            c = {"n": n, "ok": bool(ok), "why": why, "status": status or ("passed" if ok else "failed")}
            if na:
                c["na"] = na
            crit.append(c)
        tdir = inst.evidence / f"turn-{int(t):03d}"
        if t not in done or not exists(tdir / "intake.json"):
            add(0, False, "这一轮没有回复或没有解析记录（失败的轮次也计入，不跳过）")
            results.append({"turn": t, "ok": False, "criteria": crit, "installs": False, "applies": False})
            continue
        tev = [e for e in ev if e.get("turn") == t]

        def bound_auto_apply(event):
            if event.get("type") != "apply" or histories is None:
                return False
            from .live_history import entry_problem
            return any(not entry_problem(row) and row["reference"]["target"]["kind"] == "project"
                       and event.get("install_ref") == row["reference"]
                       and event.get("install_exec") == row["invocation"]
                       and any(parent.get("seq") == row["seq"] and parent.get("type") == "exec"
                               and parent.get("actor") == "driver" and parent.get("by") == "driver" for parent in tev)
                       for row in history_entries if row.get("turn") == t)

        t0 = str(sent[t][0].get("ts_sent") or "")
        t1 = str(((sent.get(t + 1) or [{}])[0]).get("ts_sent") or "")
        if alog is None:
            add(1, False, "没有适配器动作日志（transport.json 的 log），无法核对发送次数")
        elif not has_proj:
            add(1, False, "适配器日志的条目没有 project 字段，无法归属到本项目")
        else:
            k = len([x for x in alog if x.get("action") == "send" and x.get("turn") == t and x.get("project") == inst.name])
            add(1, k == 1, f"适配器日志里本轮 send {k} 次")
        rb = [e for e in tev if e.get("type") == "reply_binding"]
        last = rb[-1] if rb else {}
        checked = any(e.get("type") == "reply_checked" for e in tev)
        meta = read_doc(tdir / "reply_meta.json", {}) or {}
        smeta = read_doc(tdir / "send_meta.json", {}) or {}
        if meta.get("parent") and smeta.get("message_uuid"):
            pc = meta["parent"] == smeta["message_uuid"]
            pwhy = "回复的父消息就是本轮发出的消息" if pc else "回复的父消息不是本轮发出的消息"
        else:
            pc, pwhy = False, "适配器没有给出发出消息的 uuid 或回复的 parent，父子关系无法核对"
        add(2, last.get("receipt") == "ok" and checked and pc,
            "回执：" + (last.get("receipt") or "没有记录") + ("；归属已核对" if checked else "；没有 reply_checked") + "；" + pwhy)
        bad3 = [e.get("rule") for e in tev if e.get("type") == "auto" and e.get("rule") in ("RELEASE_MISSING", "FP_MISMATCH")]
        add(3, last.get("deliveries") in ("ok", "none") and not bad3,
            "交付核对：" + (last.get("deliveries") or "没有记录") + (("；" + "、".join(bad3)) if bad3 else ""))
        tries = [e for e in tev if e.get("type") == "exec"
                 and ("wf self-update" in str(e.get("cmd") or "")
                      or any(row.get("seq") == e.get("seq")
                             and (row.get("reference") or {}).get("target", {}).get("kind") == "tool"
                             for row in history_entries))]
        ups = [e for e in tries if e.get("exit_code") == 0]
        # B1-C r2（F1）：任何失败、超时或结果未知的安装尝试都留在结构化状态里，不被同轮的成功安装遮掉
        att = {"total": len(tries), "ok": len(ups),
               "timeout": sum(1 for e in tries if e.get("exit_code") == 124),
               "unknown": sum(1 for e in tries if not isinstance(e.get("exit_code"), int))}
        att["failed"] = att["total"] - att["ok"] - att["timeout"] - att["unknown"]
        bad_try = att["total"] > att["ok"]
        if not ups and tries:
            # B1-C：安装尝试失败或超时不是“没有安装”，不能记为不适用；ok 与说明保持原样，结构化状态如实区分
            add(4, True, "本轮没有安装工具版本（不适用）", status="attempt_failed")
        elif not ups:
            add(4, True, "本轮没有安装工具版本（不适用）", status="not_applicable",
                na={"condition": "本轮没有 wf self-update 命令", "scope": "第 4 段（审查在先）",
                    "reason": "没有安装工具版本，审查在先无从核对", "by": "live.check"})
        else:
            vs = []
            for e in ups:
                if e.get("install_exec") is not None and histories is not None:
                    from .live_history import entry_problem
                    entries = [row for row in history_entries if row.get("seq") == e.get("seq")
                               and (row.get("reference") or {}).get("target", {}).get("kind") == "tool"]
                    problems = [entry_problem(row) for row in entries]
                    vs.append("pass" if entries and not any(problems)
                              else "历史安装无法核对（" + "、".join([p for p in problems if p] or ["missing_receipt"]) + "）")
                    continue
                zp = inst.root / str(e.get("cmd")).split()[-1]
                rr = read_doc(LOCAL / "reviews" / f"{zp.stem}.result.json", {}) or {}
                v = rr.get("verdict") or "没有记录"
                if v in ("pass", "concerns"):
                    if not exists(zp) or hashlib.sha256(read_bytes(zp)).hexdigest() != rr.get("zip_sha256"):
                        v += "（版本包哈希对不上或已不在）"
                    else:
                        v += _review_timing(rr, e)   # 1.9.4：按安装开始的时刻比，空时刻不放过
                vs.append(v)
            ok4 = all(v in ("pass", "concerns") for v in vs)
            add(4, ok4, "安装前审查：" + "、".join(vs), status="attempt_failed" if (ok4 and bad_try) else None)
        if tries:
            crit[-1]["attempts"] = att
        man = [f"{e.get('type')}（{e.get('by')}/{e.get('actor') or '?'}）"
               for e in tev if _manual(e) and not bound_auto_apply(e)]
        add(5, not man, "人工改动：" + ("、".join(man[:5]) if man else "无"))
        cmds = (read_doc(tdir / "intake.json", {}) or {}).get("commands") or []
        ran = [e for e in tev if e.get("type") == "exec" and e.get("by") == "driver"]
        rr6 = [e for e in tev if e.get("type") == "round_result"]
        notes = read_log(tdir / "notes.jsonl")
        stale = any("[PLAN_STALE]" in str(n.get("text")) for n in notes)
        stops = [str(e.get("rule")) for e in tev if e.get("type") == "auto" and e.get("action") in ("block", "await_user")]
        ok6 = len(ran) >= len(cmds) and bool(rr6) and rr6[-1].get("ok") is True and not stale and not stops
        add(6, ok6, f"命令 {len(ran)}/{len(cmds)} 条由驱动器执行；本轮结果 " + ("通过" if rr6 and rr6[-1].get("ok") else "没有或未通过")
            + ("；计划过时" if stale else "") + (("；停下：" + "、".join(stops)) if stops else ""))
        ids = {x for e in tev if e.get("type") == "tasks_opened" for x in (e.get("ids") or [])}
        ids |= {k for k, x in state_tasks.items() if x.get("turn") == t}
        late = []
        if _inputs:
            keys = {(i, g) for e in tev if e.get("type") == "tasks_opened"
                    for i, g in zip(e.get("ids") or [], e.get("generations") or [])}
            keys |= {(x["id"], x["generation"]) for x in state_tasks.values() if x.get("turn") == t}
            generation_closes = {(e.get("id"), e.get("generation")): e.get("ts")
                                 for e in ev if e.get("type") in ("task_done", "task_cancelled", "task_superseded")}
            for key in sorted(keys):
                c = generation_closes.get(key)
                if not c or (t1 and parse_iso(c) > parse_iso(t1)):
                    late.append(key[0])
        else:
            for i in sorted(ids):
                c = closes.get(i) or str((state_tasks.get(i) or {}).get("done") or (state_tasks.get(i) or {}).get("closed") or "")
                if not c or (t1 and c > t1):
                    late.append(i)
        add(7, not late, "没按时关闭的待办：" + ("、".join(late) if late else "无"))
        src = [r.get("from") for r in sent.get(t, [])]
        add(8, src == ["driver"], "本轮消息的发送方：" + ("、".join(map(str, src)) or "没有记录"))
        end, td = parse_iso(meta.get("ended_at")), parse_iso(done[t].get("ts_done"))
        if not end or not td:
            add(9, False, "适配器没有给出服务端的回复结束时间（ended_at），无法核对发现延迟")
        else:
            lag = (td - end).total_seconds()
            add(9, 0 <= lag <= budget_s, f"回复结束到接上 {int(lag)} 秒（预算 {budget_s} 秒，不得为负）")
        hs = [h for h in hist if h.get("status") in ("pushed", "unchanged") and h.get("commit") and h.get("head")
              and ((parse_iso(h.get("ts")) >= parse_iso(t0) and (not t1 or parse_iso(h.get("ts")) < parse_iso(t1))) if _inputs
                   else (str(h.get("ts") or "") >= t0 and (not t1 or str(h.get("ts") or "") < t1)))]
        bound = [h for h in hs if (head_at(inst.root, h.get("ts")) == h["head"] if _inputs else
                 (lambda x, y: bool(x) and (x.startswith(y) or y.startswith(x)))(
                     head_at(inst.root, h.get("ts")), str(h.get("head"))))]
        if bound:
            add(10, True, f"本轮同步：{bound[-1]['status']} {str(bound[-1]['commit'])[:12]}，HEAD {str(bound[-1]['head'])[:12]} 与项目当时的 HEAD 一致")
        elif hs:
            add(10, False, f"本轮同步记的 HEAD {str(hs[-1]['head'])[:12]} 与项目当时的 HEAD"
                f"（{head_at(inst.root, hs[-1].get('ts'))[:12] or '查不到'}）不一致")
        else:
            add(10, False, "本轮时间窗里没有带提交号与 HEAD 的同步")
        applies = any(e.get("type") == "apply" and (not _manual(e) or bound_auto_apply(e)) for e in tev)
        results.append({"turn": t, "ok": all(c["ok"] for c in crit), "criteria": crit, "installs": bool(ups), "applies": applies})
    results = results[-rounds:] if rounds else results
    streak = 0
    for r in reversed(results):
        if not r["ok"]:
            break
        streak += 1
    win = results[-streak:] if streak else []
    missing = []
    if streak >= rounds:
        if not any(r["installs"] for r in win):
            missing.append("连续达标的轮次里没有一次工具安装")
        if not any(r["applies"] for r in win):
            missing.append("连续达标的轮次里没有一次项目落地")
    passed = bool(results) and streak >= rounds and not missing
    return {"rounds": results, "streak": streak, "need": rounds, "missing": missing, "passed": passed,
            "summary": _summary(inst, results, win, rounds, streak, missing, passed)}


def _summary(inst, results: list, win: list, rounds: int, streak: int, missing: list, passed: bool) -> dict:
    """B1-C：机器摘要。segments 按连续达标窗口逐段计：窗口里每一轮该段都 ok 且状态是 passed 或 not_applicable 才为真
    （attempt_failed 不算）。rounds_sha256 是本次逐轮结果的规范 JSON 摘要。"""
    segs = {}
    for n in range(1, 11):
        segs[str(n)] = bool(win) and all(
            any(c.get("n") == n and c.get("ok") and c.get("status") in ("passed", "not_applicable") for c in r["criteria"])
            for r in win)
    canon = json.dumps(results, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    strict_issues = [f"第 {r['turn']} 轮第 {c.get('n')} 段：{c.get('status')}" for r in win for c in r["criteria"]
                     if not (c.get("ok") and c.get("status") in ("passed", "not_applicable"))]
    return {"schema": "live-summary/1", "project": inst.name, "need": rounds, "streak": streak,
            "window": [r["turn"] for r in win], "segments": segs, "installs": any(r["installs"] for r in win),
            "applies": any(r["applies"] for r in win), "missing": list(missing), "passed": passed,
            "passed_strict": passed and all(segs.values()) and not strict_issues, "strict_issues": strict_issues,
            "rounds_sha256": hashlib.sha256(canon.encode("utf-8")).hexdigest()}


# A window captures report inputs. Goals additionally require the complete
# tool/project closure and the C5 history protocol before authorization.
WINDOW_SCHEMA = "window-inputs/1"
WINDOW_RULE = "live-read-set/1"
CLOSURE_SCHEMA = "live-closure/1"
CLOSURE_RULE = "all-content/1"


class _InputProblem(Exception):
    def __init__(self, state, source, detail):
        self.state, self.source, self.detail = state, str(source), str(detail)
        super().__init__(f"{state}: {source}: {detail}")


def _canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _strict_json(raw, source):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError(f"duplicate key {key}")
            out[key] = value
        return out

    def constant(value):
        raise ValueError(f"nonfinite number {value}")

    try:
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
        # Reject overflows such as 1e999, too.
        _canonical(obj)
        if not isinstance(obj, dict):
            raise ValueError("record must be an object")
        return obj
    except (ValueError, UnicodeError, OverflowError, RecursionError) as e:
        raise _InputProblem("corrupt", source, e) from e


def _strict_log(raw, source):
    rows = []
    # split(b"\n") distinguishes a final LF from an empty record; splitlines()
    # would silently accept extra whitespace and non-LF separators.
    lines = raw.split(b"\n")
    if lines[-1] == b"":
        lines.pop()
    for number, line in enumerate(lines, 1):
        if not line.strip():
            raise _InputProblem("corrupt", source, f"line {number}: blank record")
        rows.append(_strict_json(line, f"{source}:line {number}"))
    return rows


def _log_coverage(raw):
    lines, offset, spans = raw.split(b"\n"), 0, []
    if lines[-1] == b"":
        lines.pop()
    for number, line in enumerate(lines, 1):
        spans.append([number, offset, len(line)])
        offset += len(line) + 1
    return {"encoding": "utf-8", "extent": "full-file", "records": spans,
            "terminal_lf": raw.endswith(b"\n")}


def _file_source(path):
    """One frozen read with descriptor.  Symlinks are explicitly unsupported."""
    import os
    import stat
    p = Path(path).absolute()
    desc = {"path": str(p), "realpath": str(p.resolve()), "kind": "file",
            "state": "absent", "sha256": None, "size": None, "mode": None}
    try:
        if str(p) != str(p.resolve()):
            raise _InputProblem("unsupported", p, "symlink source")
        before = p.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise _InputProblem("unsupported", p, "source is not a regular file")
        with p.open("rb") as f:
            opened = os.fstat(f.fileno())
            raw = f.read(64 * 1024 * 1024 + 1)
            after = os.fstat(f.fileno())
        current = p.lstat()
        fields = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if not (fields(before) == fields(opened) == fields(after) == fields(current)):
            raise _InputProblem("changed", p, "file changed during read")
        if len(raw) > 64 * 1024 * 1024:
            raise _InputProblem("unsupported", p, "source exceeds 64 MiB budget")
        desc.update(state="present", sha256=hashlib.sha256(raw).hexdigest(),
                    size=len(raw), mode=stat.S_IMODE(current.st_mode),
                    identity=[current.st_dev, current.st_ino])
        return desc, raw
    except FileNotFoundError:
        return desc, None
    except _InputProblem:
        raise
    except OSError as e:
        raise _InputProblem("unreadable", p, type(e).__name__) from e


def _directory_source(path):
    import stat
    p = Path(path).absolute()
    if str(p) != str(p.resolve()):
        raise _InputProblem("unsupported", p, "symlink directory")
    try:
        members = []
        for child in sorted(p.iterdir()):
            s = child.lstat()
            kind = "file" if stat.S_ISREG(s.st_mode) else "dir" if stat.S_ISDIR(s.st_mode) else "unsupported"
            if kind == "unsupported":
                raise _InputProblem("unsupported", child, "directory member type")
            members.append([child.name, kind, stat.S_IMODE(s.st_mode)])
        return {"path": str(p), "realpath": str(p.resolve()), "kind": "directory",
                "state": "present", "members": members,
                "sha256": hashlib.sha256(_canonical(members).encode()).hexdigest()}
    except FileNotFoundError:
        return {"path": str(p), "realpath": str(p.resolve()), "kind": "directory", "state": "absent", "members": []}
    except OSError as e:
        raise _InputProblem("unreadable", p, type(e).__name__) from e


def collect_install_materials(inst, *, tool_root=None):
    """Freeze B2b's ordinary history files without making an authorization.

    This preparatory reader collects bytes, parsed JSON objects and directory
    selectors.  It does not establish a historical review decision or link an
    exec event to a transaction.  Neither live.check nor goals consumes it to
    grant verification.  Missing stores are explicit inputs.
    """
    from .core import WF_HOME
    tool = Path(tool_root if tool_root is not None else WF_HOME).absolute()
    project = Path(inst.root).absolute()
    roots = {"tool_install": tool / "local/install",
             "project_install": project / "workflow/evidence/install",
             "reviews": Path(LOCAL).absolute() / "reviews",
             "releases": Path(LOCAL).absolute() / "releases"}
    fixed = (Path(LOCAL).absolute() / "transport.json",
             Path(LOCAL).absolute() / "lanes.json", tool / "docs/REVIEW.md")
    files, documents, directories = {}, {}, {}
    total_bytes = 0

    def read(path):
        nonlocal total_bytes
        key = str(path)
        if key in files:
            return
        source, raw = _file_source(path)
        files[key] = source
        if raw is not None:
            total_bytes += len(raw)
            if len(files) > 20000 or total_bytes > 256 * 1024 * 1024:
                raise _InputProblem("unsupported", path, "install material budget exceeded")
            if path.suffix == ".json":
                documents[key] = _strict_json(raw, path)

    def visit(path):
        if len(directories) >= 20000:
            raise _InputProblem("unsupported", path, "install material directory budget exceeded")
        desc = _directory_source(path)
        directories[str(path)] = desc
        if desc["state"] == "absent":
            return
        for name, kind, _mode in desc["members"]:
            child = path / name
            if kind == "dir":
                visit(child)
            elif kind == "file":
                read(child)
            else:
                raise _InputProblem("unsupported", child, "install material member type")

    for path in roots.values():
        # _directory_source rejects aliased paths and symlinks before descent.
        visit(path)
    for path in fixed:
        read(path)
    transport = documents.get(str(fixed[0]), {})
    verifier = transport.get("review_verifier")
    if isinstance(verifier, dict) and isinstance(verifier.get("path"), str):
        path = Path(verifier["path"]).expanduser()
        if not path.is_absolute():
            raise _InputProblem("unsupported", fixed[0], "absolute review verifier path required")
        read(path)
    # Re-read the same files and selectors.  The comparison is optimistic
    # stability, not an OS transaction or protection against malicious ABA.
    for key, source in files.items():
        if _file_source(key)[0] != source:
            raise _InputProblem("changed", key, "install material changed during collection")
    for key, source in directories.items():
        if _directory_source(key) != source:
            raise _InputProblem("changed", key, "install material selector changed during collection")
    descriptor = {"schema": "install-materials/1", "complete": False,
                  "subject": {"project": inst.name, "root": str(project), "tool_root": str(tool),
                              "local": str(Path(LOCAL).absolute())},
                  "stores": {k: str(v) for k, v in roots.items()},
                  "sources": sorted(files.values(), key=lambda s: s["path"]),
                  "directories": sorted(directories.values(), key=lambda s: s["path"])}
    return {"state": "collected", "authorization": "unavailable", "descriptor": descriptor,
            "materials_sha256": hashlib.sha256(_canonical(descriptor).encode()).hexdigest(),
            "documents": documents,
            "pending": ["historical_review_provenance", "exec_transaction_association",
                        "installation_chain_validation"]}


def _git_inputs(root, timestamps, *, include_status=False):
    """Controlled ordinary local Git only.  Every query is replayed on validation."""
    import os
    import re
    import shutil
    import subprocess
    root = Path(root)
    gd = root / ".git"
    if not gd.is_dir() or gd.is_symlink():
        raise _InputProblem("unsupported", gd, "ordinary repository required (no gitfile/worktree)")
    for rel in ("shallow", "objects/info/alternates", "objects/info/http-alternates", "commondir"):
        if (gd / rel).exists():
            raise _InputProblem("unsupported", gd / rel, f"unsupported Git {rel}")
    if (gd / "refs" / "replace").exists() and any((gd / "refs" / "replace").iterdir()):
        raise _InputProblem("unsupported", gd, "Git replace refs")
    config_source, _ = _file_source(gd / "config")
    head_source, _ = _file_source(gd / "HEAD")
    status_sources = [_file_source(gd / rel)[0] for rel in ("index", "info/exclude", "info/attributes")] \
        if include_status else []
    executable = shutil.which("git", path=os.defpath)
    if not executable:
        raise _InputProblem("unknown", gd, "Git executable unavailable")
    executable = str(Path(executable).resolve())
    binary, _ = _file_source(executable)
    env = {"PATH": os.defpath, "HOME": "/nonexistent", "LC_ALL": "C",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}
    records = []

    def query(args):
        argv = [executable, "--no-replace-objects", "-c", "core.hooksPath=/dev/null",
                "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", *args]
        try:
            r = subprocess.run(argv, cwd=str(root), env=env, capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise _InputProblem("unknown", gd, f"Git read failed: {type(e).__name__}") from e
        try:
            out, err = r.stdout.decode("utf-8"), r.stderr.decode("utf-8")
        except UnicodeError as e:
            raise _InputProblem("corrupt", gd, "Git output is not UTF-8") from e
        records.append({"argv": argv, "rc": r.returncode, "stdout": out, "stderr": err})
        if r.returncode != 0 or err.strip():
            raise _InputProblem("unknown", gd, f"Git read failed: {args[0]} rc={r.returncode} {err[:200]}")
        return out.strip()

    config = query(["config", "--local", "--no-includes", "--list"])
    if any(line.lower().startswith(("include.", "includeif.", "extensions.", "core.worktree="))
           for line in config.splitlines()):
        raise _InputProblem("unsupported", gd, "Git config indirection/extensions")
    if include_status and any(line.lower().startswith(("filter.", "submodule.", "core.attributesfile=", "core.excludesfile="))
                              for line in config.splitlines()):
        raise _InputProblem("unsupported", gd, "Git status external filters/attributes/excludes/submodules")
    if query(["for-each-ref", "--format=%(refname)", "refs/replace"]):
        raise _InputProblem("unsupported", gd, "Git replace refs")
    fmt = query(["rev-parse", "--show-object-format"])
    width = {"sha1": 40, "sha256": 64}.get(fmt)
    if width is None:
        raise _InputProblem("unsupported", gd, "Git object format")
    head = query(["rev-parse", "--verify", "HEAD"])
    heads = []
    for ts in sorted(set(timestamps)):
        oid = query(["rev-list", "-1", "--first-parent", f"--before={ts}", "HEAD"])
        if not re.fullmatch(r"[0-9a-f]{" + str(width) + "}", oid):
            raise _InputProblem("unknown", gd, f"no full historical OID at {ts}")
        heads.append([ts, oid])
    if not re.fullmatch(r"[0-9a-f]{" + str(width) + "}", head):
        raise _InputProblem("unknown", gd, "no full HEAD OID")
    spine = query(["log", "--first-parent", "--format=%H%x00%P%x00%cI", "HEAD"])
    status = query(["status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=all"]).splitlines() \
        if include_status else None
    if _file_source(gd / "config")[0] != config_source or _file_source(gd / "HEAD")[0] != head_source \
            or _file_source(executable)[0] != binary \
            or any(_file_source(row["path"])[0] != row for row in status_sources):
        raise _InputProblem("changed", gd, "Git config/HEAD/executable changed during query")
    return {"root": str(root.resolve()), "gitdir": str(gd.resolve()), "head": head, "format": fmt,
            "executable": binary, "environment": env, "queries": records, "heads": heads,
            "first_parent_history": spine, "config_source": config_source, "head_source": head_source,
            "status": status, "status_sources": status_sources}


class _FrozenInputs:
    """Private immutable snapshot: bytes/tuples/canonical JSON, never caller reports."""
    __slots__ = ("root", "project", "raw", "descriptor_json")

    def __init__(self, root, project, raw, descriptor):
        object.__setattr__(self, "root", str(root))
        object.__setattr__(self, "project", project)
        object.__setattr__(self, "raw", tuple(sorted(raw.items())))
        object.__setattr__(self, "descriptor_json", _canonical(descriptor))

    def __setattr__(self, key, value):
        raise AttributeError("frozen live inputs")

    @property
    def descriptor(self):
        return json.loads(self.descriptor_json)

    def bytes(self, path):
        key = str(Path(path).absolute())
        items = dict(self.raw)
        if key not in items:
            raise _InputProblem("unsupported", path, "read outside frozen set")
        return items[key]

    def exists(self, path):
        return self.bytes(path) is not None

    def json(self, path, default=None):
        raw = self.bytes(path)
        return default if raw is None else _strict_json(raw, path)

    def jsonl(self, path):
        raw = self.bytes(path)
        return [] if raw is None else _strict_log(raw, path)

    @property
    def adapter(self):
        return Path(self.descriptor["adapter"])

    @property
    def state(self):
        return self.json(Path(self.root) / "workflow" / "state.json")

    def head_at(self, root, ts):
        if str(Path(root).resolve()) != self.root:
            raise _InputProblem("corrupt", root, "Git root mismatch")
        return dict(self.descriptor["git"]["heads"]).get(ts, "")

    def install_histories(self, inst, events):
        from .live_history import FrozenReader, collect_bound_histories
        inputs = self.descriptor["review_inputs"]
        if inputs.get("schema") != "live-install-history/1":
            return None
        replay = collect_bound_histories(inst, events, FrozenReader(self, inputs["reader"]),
                                        local=inputs["host"]["local"], tool_root=inputs["host"]["tool_root"])
        if replay != {"schema": inputs["schema"], "entries": inputs["entries"]}:
            raise _InputProblem("corrupt", "install_history", "stored history differs from frozen replay")
        return replay


def _capture_inputs(inst, *, rounds=3, budget_s=300, output_contract=None):
    """Freeze strict report inputs, including modern C5 execution histories."""
    from datetime import datetime, timezone
    import re
    from .core import Instance
    if type(rounds) is not int or rounds <= 0 or type(budget_s) is not int or budget_s < 0:
        raise _InputProblem("corrupt", "parameters", "rounds/budget must be integers (not bool)")
    # The contract only names protocol outputs. It never filters the read set.
    contract = output_contract or {"protocol": "live-output/1", "replace_slots": [], "evidence": []}
    if isinstance(contract, dict) and contract.get("protocol") == "live-output/2":
        _validate_output_contract(inst, contract)
        output_names = list(contract["evidence"].values())
    elif not isinstance(contract, dict) or set(contract) != {"protocol", "replace_slots", "evidence"} \
            or contract["protocol"] != "live-output/1":
        raise _InputProblem("corrupt", "output_contract", "unknown output contract")
    else:
        if not isinstance(contract["replace_slots"], list) or not isinstance(contract["evidence"], list) \
                or any(not isinstance(v, str) or not v for v in contract["replace_slots"]):
            raise _InputProblem("corrupt", "output_contract", "invalid output members")
        output_names = contract["evidence"]
    for value in output_names:
        if not isinstance(value, str) or not value.startswith("workflow/evidence/goals/") \
                or Path(value).name != value.removeprefix("workflow/evidence/goals/") or not value.endswith("-live.json"):
            raise _InputProblem("corrupt", "output_contract", "invalid live output path")
    raw, sources, directories = {}, [], []
    captured_bytes = 0

    def source(path, kind, required=True):
        nonlocal captured_bytes
        path = Path(path).absolute()
        key = str(path)
        if key in raw:
            return raw[key]
        if len(raw) >= 20000:
            raise _InputProblem("unsupported", path, "snapshot source budget exceeded")
        desc, data = _file_source(path)
        desc["format"] = kind
        if data is not None:
            captured_bytes += len(data)
            if captured_bytes > 256 * 1024 * 1024:
                raise _InputProblem("unsupported", path, "snapshot source budget exceeded")
            if kind == "jsonl":
                desc["coverage"] = _log_coverage(data)
        raw[key] = data
        sources.append(desc)
        if data is None and required:
            raise _InputProblem("unknown", path, "required source absent")
        if data is not None:
            if kind == "json":
                _strict_json(data, path)
            elif kind == "jsonl":
                _strict_log(data, path)
        return data

    def doc(path):
        return _strict_json(source(path, "json"), path)

    def log(path):
        return _strict_log(source(path, "jsonl"), path)

    def stamp(value, origin):
        try:
            if not isinstance(value, str):
                raise ValueError("timestamp must be a string")
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timestamp needs timezone")
            return parsed.astimezone(timezone.utc)
        except (ValueError, TypeError) as e:
            raise _InputProblem("corrupt", origin, str(e)) from e

    with inst.state_lock():
        # Validate the wf source before constructing a fresh Instance.
        cfg = doc(inst.wf / "wf.json")
        if not isinstance(cfg.get("project"), str) or not cfg["project"]:
            raise _InputProblem("corrupt", inst.cfg_path, "missing project identity")
        fresh = Instance(inst.root)
        if fresh.name != inst.name:
            raise _InputProblem("changed", inst.cfg_path, "project identity changed")
        state = doc(fresh.state_path)
        events, turns = log(fresh.events_path), log(fresh.turns_path)
        transport = doc(LOCAL / "transport.json")
        lp = transport.get("log")
        if not isinstance(lp, str) or not Path(lp).expanduser().is_absolute():
            raise _InputProblem("unknown", LOCAL / "transport.json", "absolute adapter log source required")
        adapter = Path(lp).expanduser()
        alog = log(adapter)
        hist = log(LOCAL / "sync" / f"{fresh.name}.history.jsonl")
        # Full log files and all candidate rounds are captured before selection.
        sent, done = {}, {}
        for line, row in enumerate(turns, 1):
            t = row.get("turn")
            if type(t) is not int or t < 0:
                raise _InputProblem("corrupt", fresh.turns_path, f"line {line}: invalid turn")
            if row.get("status") == "sent":
                stamp(row.get("ts_sent"), f"turn {t} sent")
                sent.setdefault(t, []).append(row)
            if row.get("ts_done"):
                stamp(row["ts_done"], f"turn {t} done")
                done[t] = row
        seqs = []
        for line, row in enumerate(events, 1):
            seq = row.get("seq")
            if type(seq) is not int or seq <= 0 or not isinstance(row.get("type"), str):
                raise _InputProblem("unknown", fresh.events_path, f"line {line}: event sequence/type unavailable")
            seqs.append(seq)
            stamp(row.get("ts"), f"events line {line}")
            if "turn" in row and (type(row["turn"]) is not int or row["turn"] < 0):
                raise _InputProblem("corrupt", fresh.events_path, f"line {line}: invalid turn")
            if "duration_s" in row:
                import math
                duration = row["duration_s"]
                if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
                    raise _InputProblem("corrupt", fresh.events_path, f"line {line}: invalid duration")
            if "exit_code" in row and row["exit_code"] is not None and type(row["exit_code"]) is not int:
                raise _InputProblem("corrupt", fresh.events_path, f"line {line}: invalid exit code")
        if len(set(seqs)) != len(seqs) or seqs != sorted(seqs):
            raise _InputProblem("corrupt", fresh.events_path, "duplicate/out-of-order event sequence")
        if seqs != list(range(1, len(seqs) + 1)) or type(state.get("seq")) is not int or state["seq"] != len(seqs):
            raise _InputProblem("unknown", fresh.events_path, "event sequence gap/order/state mismatch")
        for origin, rows in ((fresh.events_path, events), (fresh.turns_path, turns), (adapter, alog)):
            for row in rows:
                if "project" in row and row["project"] != fresh.name or "root" in row and row["root"] != str(fresh.root):
                    raise _InputProblem("corrupt", origin, "project/root conflict")
        # Shared adapter records need an explicit root; the fixed per-project
        # sync source is attributed by its source path and wf identity.
        if any(row.get("project") != fresh.name or row.get("root") != str(fresh.root) for row in alog):
            raise _InputProblem("unknown", adapter, "adapter project/root attribution unavailable")
        for origin, rows in ((adapter, alog), (LOCAL / "sync" / f"{fresh.name}.history.jsonl", hist)):
            for line, row in enumerate(rows, 1):
                if "turn" in row and (type(row["turn"]) is not int or row["turn"] < 0):
                    raise _InputProblem("corrupt", origin, f"line {line}: invalid turn")
                stamp(row.get("ts"), f"{origin}:line {line}")
        for t in sorted(sent):
            td = fresh.evidence / f"turn-{t:03d}"
            directories.append(_directory_source(td))
            intake = doc(td / "intake.json")
            if not isinstance(intake.get("commands", []), list) or any(
                    not isinstance(c, str) for c in intake.get("commands", [])):
                raise _InputProblem("corrupt", td / "intake.json", "commands must be a string list")
            meta = doc(td / "reply_meta.json")
            stamp(meta.get("ended_at"), td / "reply_meta.json")
            smeta = doc(td / "send_meta.json")
            if not isinstance(meta.get("parent"), str) or not isinstance(smeta.get("message_uuid"), str):
                raise _InputProblem("unknown", td, "message identity unavailable")
            log(td / "notes.jsonl")
        # Legacy report reads remain explicit. Structural receipts never fall
        # back to parsing a command's final token.
        import shlex
        for event in events:
            if event.get("type") == "exec" and event.get("install_exec") is None \
                    and "wf self-update" in str(event.get("cmd") or "") and event.get("exit_code") == 0:
                try:
                    tokens = shlex.split(event["cmd"])
                except ValueError as e:
                    raise _InputProblem("corrupt", fresh.events_path, str(e)) from e
                if not tokens or " " in tokens[-1]:
                    raise _InputProblem("unsupported", fresh.events_path, "legacy install path parser")
                zp = fresh.root / tokens[-1]
                source(zp, "bytes")
                doc(LOCAL / "reviews" / f"{zp.stem}.result.json")
        task_rows = state.get("executor_tasks", [])
        if not isinstance(task_rows, list) or any(not isinstance(x, dict) for x in task_rows):
            raise _InputProblem("corrupt", fresh.state_path, "executor_tasks must be object list")
        opened, closed = set(), set()
        for event in events:
            if event.get("type") == "tasks_opened":
                ids, gens = event.get("ids"), event.get("generations")
                if not isinstance(ids, list) or not isinstance(gens, list) or len(ids) != len(gens) \
                        or any(not isinstance(x, str) or not x for x in ids + gens):
                    raise _InputProblem("unknown", fresh.events_path, "task generation pairing unavailable")
                if any(not re.fullmatch(r"[0-9a-f]{32}", generation) for generation in gens):
                    raise _InputProblem("unknown", fresh.events_path, "A1 UUID generation unavailable")
                opened.update(zip(ids, gens))
            elif event.get("type") in ("task_done", "task_cancelled", "task_superseded"):
                if not isinstance(event.get("id"), str) or not event.get("id") \
                        or not isinstance(event.get("generation"), str) or not event.get("generation"):
                    raise _InputProblem("unknown", fresh.events_path, "task close generation unavailable")
                closed.add((event.get("id"), event["generation"]))
        seen_tasks = set()
        for task in task_rows:
            key = (task.get("id"), task.get("generation"))
            if task.get("project") != fresh.name or task.get("root") != str(fresh.root):
                raise _InputProblem("unknown", fresh.state_path, "task project/root attribution unavailable")
            if not isinstance(key[0], str) or not isinstance(key[1], str) \
                    or not re.fullmatch(r"[0-9a-f]{32}", key[1]) or key not in opened:
                raise _InputProblem("unknown", fresh.state_path, "task generation/open event unavailable")
            if key[0] in seen_tasks:
                raise _InputProblem("corrupt", fresh.state_path, "duplicate task id")
            seen_tasks.add(key[0])
            if type(task.get("rev")) is not int or task["rev"] <= 0:
                raise _InputProblem("corrupt", fresh.state_path, "invalid task revision")
            if "turn" in task and (type(task["turn"]) is not int or task["turn"] < 0):
                raise _InputProblem("corrupt", fresh.state_path, "invalid task turn")
            is_closed = bool(task.get("done")) or task.get("status") in ("cancelled", "superseded")
            if is_closed != (key in closed):
                raise _InputProblem("unknown", fresh.state_path, "task state/event generation conflict")
        selection = [{"turn": t, "sent": len(sent[t]), "done": t in done,
                      "t0": sent[t][0]["ts_sent"], "t1": (sent.get(t + 1) or [{}])[0].get("ts_sent"),
                      "excluded": "latest_pending" if t == max(sent) and t not in done else None}
                     for t in sorted(sent)]
        git_inputs = _git_inputs(fresh.root, [h["ts"] for h in hist if h.get("status") in ("pushed", "unchanged")])
        history_reader = None
        review_inputs = {"state": "unavailable", "reason": "install_history_unavailable"}
        if any(event.get("type") == "exec" and event.get("install_exec") is not None for event in events):
            from .core import WF_HOME
            from .live_history import CaptureReader, collect_bound_histories

            def history_directory(path):
                desc = _directory_source(path)
                previous = next((row for row in directories if row["path"] == desc["path"]), None)
                if previous is not None and previous != desc:
                    raise _InputProblem("changed", path, "history selector changed during collection")
                if previous is None:
                    directories.append(desc)
                return desc

            history_reader = CaptureReader(lambda path: source(path, "bytes", required=False), history_directory)
            review_inputs = collect_bound_histories(fresh, events, history_reader, local=LOCAL, tool_root=WF_HOME)
            review_inputs.update(host={"local": str(Path(LOCAL).absolute()), "tool_root": str(Path(WF_HOME).absolute())},
                                 reader=history_reader.descriptor)
        descriptor = {"schema": WINDOW_SCHEMA, "rule": WINDOW_RULE,
                      "complete": review_inputs.get("schema") == "live-install-history/1",
                      "subject": {"root": str(fresh.root), "project": fresh.name, "created": cfg.get("created"),
                                  "local": str(LOCAL.resolve())},
                      "parameters": {"rounds": rounds, "budget_s": budget_s},
                      "sources": sorted(sources, key=lambda s: s["path"]),
                      "directories": sorted(directories, key=lambda s: s["path"]),
                      "adapter": str(adapter.absolute()), "selection": selection, "git": git_inputs,
                      "output_contract": contract, "task_protocol": "A1-uuid/1",
                      "review_inputs": review_inputs}
        # Re-read bytes and member boundaries; no after-the-fact hash can be
        # paired with data evaluated earlier. No global ACID/ABA claim is made.
        if history_reader is not None:
            history_reader.revalidate()
        for desc in sources:
            current, data = _file_source(desc["path"])
            current["format"] = desc["format"]
            if desc["format"] == "jsonl" and data is not None:
                current["coverage"] = _log_coverage(data)
            if current != desc:
                raise _InputProblem("changed", desc["path"], "source changed before freeze")
        for desc in directories:
            if _directory_source(desc["path"]) != desc:
                raise _InputProblem("changed", desc["path"], "directory members changed before freeze")
        if _git_inputs(fresh.root, [h[0] for h in git_inputs["heads"]]) != git_inputs:
            raise _InputProblem("changed", fresh.root, "Git changed before freeze")
        return _FrozenInputs(fresh.root, fresh.name, raw, descriptor)


def _evaluate_snapshot(snapshot):
    """Only frozen bytes and captured Git replies enter the ten-segment evaluator."""
    from types import SimpleNamespace
    root = Path(snapshot.root)
    inst = SimpleNamespace(root=root, name=snapshot.project, evidence=root / "workflow" / "evidence",
                           events_path=root / "workflow" / "events.jsonl", turns_path=root / "workflow" / "turns.jsonl")
    params = snapshot.descriptor["parameters"]
    report = check(inst, params["rounds"], params["budget_s"], _inputs=snapshot)
    result = {"state": "collected", "window_inputs": snapshot.descriptor,
              "inputs_sha256": hashlib.sha256(snapshot.descriptor_json.encode()).hexdigest(),
              "report": report, "authorization": "unavailable", "reason": "install_history_unavailable"}
    history = snapshot.descriptor["review_inputs"]
    if history.get("schema") == "live-install-history/1":
        from .live_history import window_authorization
        authorization = window_authorization(history, snapshot.jsonl(inst.events_path), report)
        result.update(history_authorization=authorization,
                      authorization="available" if authorization["eligible"] else "unavailable",
                      reason=None if authorization["eligible"] else "；".join(authorization["why"]))
    return result


def _revalidate_inputs(inst, descriptor):
    """Re-discover selectors and sources; never merely trust the old file list."""
    try:
        if not isinstance(descriptor, dict) or descriptor.get("schema") != WINDOW_SCHEMA \
                or descriptor.get("rule") != WINDOW_RULE:
            raise _InputProblem("unsupported", "window_inputs", "unknown input protocol")
        params = descriptor["parameters"]
        snap = _capture_inputs(inst, rounds=params["rounds"], budget_s=params["budget_s"],
                               output_contract=descriptor["output_contract"])
        current = snap.descriptor
        same = current == descriptor
        return {"valid": same, "state": "stable" if same else "changed",
                "inputs_sha256": hashlib.sha256(snap.descriptor_json.encode()).hexdigest()}
    except _InputProblem as e:
        return {"valid": False, "state": e.state, "source": e.source, "detail": e.detail}
    except (KeyError, TypeError, ValueError) as e:
        return {"valid": False, "state": "corrupt", "source": "window_inputs", "detail": type(e).__name__}


def _validate_output_contract(inst, contract):
    """Only the normal all-exec run can name output slots and exact new proofs."""
    import re
    if not isinstance(contract, dict) or set(contract) != {"protocol", "run_id", "replace_slots", "evidence"} \
            or contract.get("protocol") != "live-output/2" \
            or not isinstance(contract.get("run_id"), str) \
            or not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{12}", contract["run_id"]):
        raise _InputProblem("corrupt", "output_contract", "invalid live-output/2 contract")
    desc, raw = _file_source(inst.wf / "goals.json")
    if raw is None:
        raise _InputProblem("unknown", desc["path"], "goals definition absent")
    g = _strict_json(raw, desc["path"])
    if not isinstance(g.get("goals"), list):
        raise _InputProblem("corrupt", desc["path"], "goals list required")
    goals = g["goals"]
    if any(not isinstance(x, dict) or not isinstance(x.get("id"), str)
           or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", x["id"]) for x in goals):
        raise _InputProblem("unsupported", desc["path"], "safe goal ids required for protocol outputs")
    ids = [x["id"] for x in goals]
    if len(ids) != len(set(ids)):
        raise _InputProblem("corrupt", desc["path"], "duplicate goal id")
    replaced = [x["id"] for x in goals if x.get("kind") == "exec"]
    evidence = {x["id"]: f"workflow/evidence/goals/{contract['run_id']}-{x['id']}-live.json"
                for x in goals if x.get("kind") == "exec" and isinstance(x.get("live"), dict)}
    if contract["replace_slots"] != replaced or contract["evidence"] != evidence:
        raise _InputProblem("corrupt", "output_contract", "output members do not match actual goals/run")
    return contract


def _record_core(record):
    """Mechanical output payload, excluding the one protocol-owned live field."""
    if not isinstance(record, dict):
        raise _InputProblem("corrupt", "goals_state", "result slot must be an object")
    core = {k: v for k, v in record.items() if k != "verified"}
    expected = {"ok", "why", "ts", "exit", "evidence", "duration_s", "goals_sha", "layer", "binding"}
    if set(core) != expected or type(core.get("ok")) is not bool or type(core.get("exit")) is not int \
            or not isinstance(core.get("why"), list) or any(not isinstance(s, str) for s in core["why"]) \
            or not isinstance(core.get("binding"), dict):
        raise _InputProblem("corrupt", "goals_state", "invalid mechanical result shape")
    for key in ("ts", "evidence", "goals_sha", "layer"):
        if not isinstance(core.get(key), str) or not core[key]:
            raise _InputProblem("corrupt", "goals_state", f"invalid result {key}")
    if type(core.get("duration_s")) not in (int, float) or core["duration_s"] < 0:
        raise _InputProblem("corrupt", "goals_state", "invalid result duration")
    _canonical(core)
    return core


def _closure_inputs(inst, snapshot, *, tool_root, records):
    """Freeze every tool/project file except exact protocol outputs and Git.

    Git is bound by its controlled query envelope. The mixed goals_state file is
    projected: untouched slots remain inputs; replacement slots are separately
    checked as output records. No runtime directory or ignored-file exclusion.
    """
    import stat
    contract = _validate_output_contract(inst, snapshot.descriptor["output_contract"])
    state_path = inst.wf / "goals_state.json"
    _, raw_state = _file_source(state_path)
    on_disk = {} if raw_state is None else _strict_json(raw_state, state_path)
    projection = {k: v for k, v in on_disk.items() if k not in contract["replace_slots"]}
    cores = {}
    for gid in contract["replace_slots"]:
        if gid not in records:
            raise _InputProblem("corrupt", state_path, f"replacement result absent: {gid}")
        cores[gid] = _record_core(records[gid])
    ignored_outputs = {str((inst.root / p).absolute()) for p in contract["evidence"].values()}
    ignored_outputs.add(str(state_path.absolute()))
    seen_files, total_bytes = 0, 0
    actual_read = {s["path"]: s for s in snapshot.descriptor["sources"]}

    def tree(root, output_paths):
        nonlocal seen_files, total_bytes
        root = Path(root).absolute()
        sources, directories = [], []
        if str(root) != str(root.resolve()):
            raise _InputProblem("unsupported", root, "closure root symlink")

        def visit(directory):
            nonlocal seen_files, total_bytes
            try:
                s = directory.lstat()
                if not stat.S_ISDIR(s.st_mode):
                    raise _InputProblem("unsupported", directory, "closure directory required")
                members = []
                children = sorted(directory.iterdir())
                for child in children:
                    key = str(child)
                    if key in output_paths:
                        continue
                    info = child.lstat()
                    if child == root / ".git":
                        members.append([child.name, "git-envelope"])
                        continue
                    if stat.S_ISLNK(info.st_mode):
                        raise _InputProblem("unsupported", child, "closure symlink")
                    if stat.S_ISDIR(info.st_mode):
                        members.append([child.name, "directory", stat.S_IMODE(info.st_mode)])
                        visit(child)
                    elif stat.S_ISREG(info.st_mode):
                        desc, data = _file_source(child)
                        if data is None:
                            raise _InputProblem("changed", child, "closure member disappeared")
                        seen_files += 1
                        total_bytes += len(data)
                        if seen_files > 20000 or total_bytes > 256 * 1024 * 1024:
                            raise _InputProblem("unsupported", child, "closure budget exceeded")
                        if key in actual_read:
                            overlap = {k: v for k, v in actual_read[key].items() if k not in ("format", "coverage")}
                            if overlap != desc:
                                raise _InputProblem("changed", child, "evaluated bytes differ from closure bytes")
                        sources.append(desc)
                        members.append([child.name, "file", stat.S_IMODE(info.st_mode)])
                    else:
                        raise _InputProblem("unsupported", child, "closure special file")
                directories.append({"path": str(directory), "mode": stat.S_IMODE(s.st_mode),
                                    "identity": [s.st_dev, s.st_ino], "members": members})
            except OSError as e:
                raise _InputProblem("unreadable", directory, type(e).__name__) from e
        visit(root)
        return {"root": str(root), "sources": sorted(sources, key=lambda s: s["path"]),
                "directories": sorted(directories, key=lambda s: s["path"])}

    tool = tree(tool_root, set())
    project = tree(inst.root, ignored_outputs)
    git_tool = _git_inputs(tool_root, [], include_status=True)
    descriptor = {"schema": CLOSURE_SCHEMA, "rule": CLOSURE_RULE, "complete": True,
                  "subject": snapshot.descriptor["subject"], "tool": tool, "project": project,
                  "git_tool": git_tool, "git_project": snapshot.descriptor["git"],
                  "output_contract": contract, "state_projection": projection,
                  "state_slots_read": sorted(projection), "output_slots_replaced": contract["replace_slots"],
                  "output_record_cores": cores}
    # Re-discover all members and bytes before sealing the closure.
    seen_files, total_bytes = 0, 0
    if tree(tool_root, set()) != tool or tree(inst.root, ignored_outputs) != project \
            or _git_inputs(tool_root, [], include_status=True) != git_tool:
        raise _InputProblem("changed", inst.root, "closure changed during collection")
    _, state_again = _file_source(state_path)
    again = {} if state_again is None else _strict_json(state_again, state_path)
    if {k: v for k, v in again.items() if k not in contract["replace_slots"]} != projection:
        raise _InputProblem("changed", state_path, "input result slots changed during collection")
    validation = _revalidate_inputs(inst, snapshot.descriptor)
    if not validation["valid"]:
        raise _InputProblem(validation["state"], validation.get("source", "window_inputs"),
                            validation.get("detail", "live read set changed during closure collection"))
    return descriptor


def _revalidate_closure(inst, snapshot, closure, *, tool_root, records):
    try:
        if not isinstance(closure, dict) or closure.get("schema") != CLOSURE_SCHEMA \
                or closure.get("rule") != CLOSURE_RULE or closure.get("complete") is not True:
            raise _InputProblem("unsupported", "closure", "unknown/incomplete closure protocol")
        current = _closure_inputs(inst, snapshot, tool_root=tool_root, records=records)
        return {"valid": current == closure, "state": "stable" if current == closure else "changed",
                "closure_sha256": hashlib.sha256(_canonical(current).encode()).hexdigest()}
    except _InputProblem as e:
        return {"valid": False, "state": e.state, "source": e.source, "detail": e.detail}
    except (KeyError, TypeError, ValueError) as e:
        return {"valid": False, "state": "corrupt", "source": "closure", "detail": type(e).__name__}


