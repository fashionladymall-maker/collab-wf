"""B2-C3：首版可核查指标（metrics/1）。只读：不创建、不写任何文件，不改 state 与 goals_state，不调用发送、适配器或外部命令。

冻结的返回结构：
  {"schema": "metrics/1", "project", "provenance": "local_records_unattested",
   "window": {"requested": {"first", "last"}, "current_turn", "rounds", "denominator_certain"},
   "sources": {"events" | "turns" | "state" | "adapter_log": {"path", "exists", "bytes", "sha256", "lines",
               "bad_count", "bad_lines", "error"}},
   "metrics": {名称: {"value", "unknown", "n", "coverage", "source", "diagnostic": {"why", "issues", "observed"}}}}

来源边界（按现有源码里实际存在的协议）：
- 只有事件流（workflow/events.jsonl）有完整性协议：core.Instance.event 在状态锁内先把 seq 写进 state.json、再追加一行，
  每行带 seq、ts、type、turn、actor。fix1：整条事件流必须完整才计算——文件顺序的 seq 恰为 1、2、…、N 且等于 state.json
  的 seq，每行可解析且字段齐全，没有显式写着别的项目的行；任何一处不满足，manual_rate 整体 unknown（不按轮局部放过）。
  轮次记录（turns.jsonl）必须存在、可读、没有坏行，且轮号与事件流一致。
- 一轮“已关闭”要按协议证明（fix1）：轮号 ≥ 1（第 0 轮是接入/引导，不是正式轮）；turns.jsonl 有本轮的 sent 与 ts_done；
  事件流里本轮以转入 COMPOSE 开始，有 round_result，其后出现下一轮（轮号 +1）转入 COMPOSE 的边界事件，
  边界之后不再有本轮的事件。证明不了的轮次留在分母里、不计入样本。
- 时间与身份（fix2）：作为闭合证据的时间只接受可解析的时间字符串（布尔、数字等一律无效），不得晚于读取时刻 5 分钟以上；
  turns.jsonl 的 sent 记录必须有 ts_sent，带 ts_sent/ts_done 的记录其值必须有效，显式写着别的项目的行使整条轮次记录不完整。
  时间先后按现有真实写入顺序核对：本轮转入 COMPOSE ≤ 各次发送 ts_sent，最早发送 ≤ 最早回复 ts_done，最后发送 ≤ 最后回复，
  最后回复 ≤ round_result ≤ 下一轮转入 COMPOSE；不满足的轮次不计入样本。
- 适配器日志写失败可能被吞（B1 已知），日志条数不能证明物理发送次数；任务就绪/领取（ready/picked）没有事件协议；
  返工没有完整的判定协议。这几项只给观测计数（diagnostic.observed），value 为 None。
- 读到的都是本地记录，不是出处证明（provenance 固定为 local_records_unattested）；合成夹具的数字不是实景。

规则：缺身份、缺时间、坏行、错轮次、序号缺口或重复所在的轮次一律不计入，不推算、不补零；分母本身不可证时 coverage 为 None。
任务身份（含将来的代次）本卡不消费，不按裸任务编号合并。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .core import WfError, parse_iso

SCHEMA = "metrics/1"
PROVENANCE = "local_records_unattested"
METRICS = ("queue_share", "first_pass_rate", "rework_rate", "manual_rate")
_REWORK_RULES = ("FP_MISMATCH", "RELEASE_MISSING")
_REWORK_TYPES = ("resend_not_landed", "task_superseded")
_BAD_LIST = 20
_FUTURE_SKEW_S = 300   # fix2：比读取时刻晚 5 分钟以上的时间视为未来时刻（无效）


def _is_int(v) -> bool:
    return type(v) is int


def _ts(v):
    """fix2：只接受可解析的时间字符串；布尔、数字等一律无效（parse_iso 会把数字转成字符串再解析，不能直接用）。"""
    return parse_iso(v) if isinstance(v, str) and v else None


def _future(d, now) -> bool:
    return (d - now).total_seconds() > _FUTURE_SKEW_S


def _check_window(first, last) -> None:
    for name, v in (("first", first), ("last", last)):
        if v is not None and (not _is_int(v) or v < 0):
            raise WfError(f"metrics 窗口的 {name} 必须是非负整数或 None，收到 {v!r}")
    if first is not None and last is not None and first > last:
        raise WfError(f"metrics 窗口 first={first} 大于 last={last}")


def _src(p) -> dict:
    return {"path": str(p), "exists": False, "bytes": None, "sha256": None, "lines": 0, "bad_count": 0,
            "bad_lines": [], "error": None}


def _read_bytes(p, src: dict):
    try:
        raw = Path(p).read_bytes()
    except FileNotFoundError:
        src["error"] = "不存在"
        return None
    except OSError as e:
        src["error"] = f"读取失败：{type(e).__name__}"
        return None
    src.update(exists=True, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    return raw


def _read_jsonl(p) -> tuple:
    """返回 ([(行号, dict)], 来源记录)。哈希与解析来自同一份字节；不能解析为对象的行记为坏行（不跳过、不假装完整）。"""
    src = _src(p)
    raw = _read_bytes(p, src)
    rows = []
    if raw is None:
        return rows, src
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        src["error"] = "不是 UTF-8"
        return rows, src
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    src["lines"] = len(lines)
    for i, line in enumerate(lines, 1):
        try:
            obj = json.loads(line)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            rows.append((i, obj))
        else:
            src["bad_count"] += 1
            if len(src["bad_lines"]) < _BAD_LIST:
                src["bad_lines"].append(i)
    return rows, src


def _read_state(p) -> tuple:
    src = _src(p)
    raw = _read_bytes(p, src)
    if raw is None:
        return None, src
    try:
        st = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        st = None
    if not isinstance(st, dict) or not _is_int(st.get("seq")) or not _is_int(st.get("turn")):
        src["error"] = "缺少整数 seq 或 turn"
        return None, src
    return st, src


def _adapter_rows(name: str) -> tuple:
    from . import anomaly
    try:
        p = (anomaly.transport_cfg() or {}).get("log")
    except Exception as e:   # noqa: BLE001 —— 读不到配置只影响观测计数
        s = _src("transport.json")
        s["error"] = f"读取失败：{type(e).__name__}"
        return [], s
    if not p:
        s = _src("")
        s["error"] = "没有配置适配器日志"
        return [], s
    return _read_jsonl(Path(p).expanduser())


def _metric(src_ids, why=None) -> dict:
    return {"value": None, "unknown": True, "n": 0, "coverage": None, "source": list(src_ids),
            "diagnostic": {"why": why, "issues": [], "observed": {}}}


def _events_integrity(ev: list, s_ev: dict, st, name: str, now) -> list:
    """fix1：整条事件流的完整性。返回问题列表；空列表表示完整。"""
    probs = []
    if not s_ev["exists"] or s_ev["error"]:
        return [f"事件流不可用：{s_ev['error'] or '不存在'}"]
    if s_ev["bad_count"]:
        probs.append(f"事件流坏行 {s_ev['bad_count']} 行：第 " + "、".join(map(str, s_ev["bad_lines"])) + " 行")
    if st is None:
        probs.append("state.json 不可用（缺失、损坏或没有整数 seq、turn），无法核对事件流末尾与当前轮")
    expect = 1
    for ln, e in ev:
        sq = e.get("seq")
        if not _is_int(sq) or sq != expect:
            probs.append(f"事件流第 {ln} 行的 seq 为 {sq!r}，按文件顺序应为 {expect}（缺序号、倒序、重复或缺口）")
            break
        expect += 1
    for ln, e in ev:
        bad = []
        t = e.get("turn")
        if not _is_int(t) or t < 0 or (st is not None and t > st["turn"]):
            bad.append("轮号无效")
        d = _ts(e.get("ts"))
        if not d:
            bad.append("缺少或无效 ts")
        elif _future(d, now):
            bad.append("ts 是未来时刻")
        if not isinstance(e.get("type"), str):
            bad.append("缺少 type")
        if not (isinstance(e.get("actor"), str) and e["actor"]):
            bad.append("缺少身份 actor")
        if "project" in e and e.get("project") != name:
            bad.append(f"显式属于别的项目 {str(e.get('project'))[:60]}")
        if bad:
            probs.append(f"事件流第 {ln} 行：" + "、".join(bad))
            if len(probs) > _BAD_LIST:
                break
    if st is not None and not probs and len(ev) != st["seq"]:
        probs.append(f"state.json 的 seq {st['seq']} 与事件流行数 {len(ev)} 不一致（末尾缺失或多余）")
    return probs


def _turns_integrity(tr: list, s_tr: dict, ev_turns: set, cur, name: str, now) -> list:
    if not s_tr["exists"] or s_tr["error"]:
        return [f"轮次记录不可用：{s_tr['error'] or '不存在'}"]
    probs = []
    if s_tr["bad_count"]:
        probs.append(f"轮次记录坏行 {s_tr['bad_count']} 行：第 " + "、".join(map(str, s_tr["bad_lines"])) + " 行")
    for ln, r in tr:
        t = r.get("turn")
        if not _is_int(t) or t < 0:
            probs.append(f"轮次记录第 {ln} 行没有有效轮号")
        elif t not in ev_turns and t != cur:
            probs.append(f"轮次记录第 {ln} 行的第 {t} 轮在事件流里不存在")
        if "project" in r and r.get("project") != name:   # fix2：旧记录没有 project 字段可以接受；显式写着别的项目不行
            probs.append(f"轮次记录第 {ln} 行显式属于别的项目 {str(r.get('project'))[:60]}")
        for k in ("ts_sent", "ts_done"):   # fix2：作为闭合证据的时间必须是有效、不在未来的时间字符串
            if k in r:
                d = _ts(r.get(k))
                if not d:
                    probs.append(f"轮次记录第 {ln} 行的 {k} 不是有效时间：{str(r.get(k))[:40]}")
                elif _future(d, now):
                    probs.append(f"轮次记录第 {ln} 行的 {k} 是未来时刻：{r.get(k)}")
        if r.get("status") == "sent" and "ts_sent" not in r:
            probs.append(f"轮次记录第 {ln} 行是发送记录却没有 ts_sent")
    return probs[:_BAD_LIST]


def _closed(t: int, ev: list, tr: list) -> str:
    """fix1：证明第 t 轮已关闭；返回不能证明的原因，空字符串表示已证明。ev 已通过完整性检查（按 seq 有序）。"""
    if t < 1:
        return "第 0 轮是接入/引导，不是正式轮"
    if not any(r.get("turn") == t and r.get("status") == "sent" for _, r in tr):
        return "轮次记录里没有本轮的发送（sent）"
    if not any(r.get("turn") == t and "ts_done" in r for _, r in tr):
        return "轮次记录里没有本轮的回复落地（ts_done）"
    mine = [e for _, e in ev if e["turn"] == t]
    if not mine or mine[0].get("type") != "transition" or mine[0].get("to") != "COMPOSE":
        return "本轮的第一条事件不是转入 COMPOSE"
    rr = [e["seq"] for e in mine if e.get("type") == "round_result"]
    if not rr:
        return "本轮没有 round_result"
    nxt = next((e for _, e in ev if e["seq"] > rr[-1] and e["turn"] > t), None)
    if not nxt or nxt.get("type") != "transition" or nxt.get("to") != "COMPOSE" or nxt["turn"] != t + 1:
        return "round_result 之后没有下一轮（轮号 +1）转入 COMPOSE 的边界事件"
    if any(e["seq"] > nxt["seq"] for e in mine):
        return "边界之后仍有本轮的事件，不能证明本轮已关闭"
    # fix2：按现有真实写入顺序核对时间先后（时间都已通过有效性检查）
    tc, trr, tb = _ts(mine[0].get("ts")), _ts(next(e for e in mine if e["seq"] == rr[-1]).get("ts")), _ts(nxt.get("ts"))
    sent = [_ts(r.get("ts_sent")) for _, r in tr if r.get("turn") == t and r.get("status") == "sent"]
    done = [_ts(r.get("ts_done")) for _, r in tr if r.get("turn") == t and "ts_done" in r]
    if None in (tc, trr, tb) or None in sent or None in done:
        return "闭合证据里有无效时间"
    if min(sent) < tc:
        return "发送时刻早于本轮转入 COMPOSE"
    if min(done) < min(sent) or max(done) < max(sent):
        return "回复落地时刻早于发送时刻（时间先后颠倒）"
    if max(done) > trr:
        return "回复落地时刻晚于 round_result"
    if not tc <= trr <= tb:
        return "本轮开始、round_result 与下一轮边界的时间先后颠倒"
    return ""


def compute(inst, *, first: int | None = None, last: int | None = None) -> dict:
    """只读计算本项目的首版指标。窗口按轮号闭区间 [first, last] 取；第 0 轮与当前轮不是可计入的正式已关闭轮。"""
    from datetime import datetime, timezone
    from .live import _manual
    _check_window(first, last)
    now = datetime.now(timezone.utc)
    ev, s_ev = _read_jsonl(inst.events_path)
    tr, s_tr = _read_jsonl(inst.turns_path)
    st, s_st = _read_state(inst.state_path)
    al, s_al = _adapter_rows(inst.name)
    out = {"schema": SCHEMA, "project": inst.name, "provenance": PROVENANCE,
           "window": {"requested": {"first": first, "last": last}, "current_turn": st["turn"] if st else None,
                      "rounds": [], "denominator_certain": False},
           "sources": {"events": s_ev, "turns": s_tr, "state": s_st, "adapter_log": s_al}, "metrics": {}}

    def in_win(t) -> bool:
        return (first is None or t >= first) and (last is None or t <= last)

    issues = _events_integrity(ev, s_ev, st, inst.name, now)
    ev_ok = not issues
    if ev_ok:
        ev_turns = {e["turn"] for _, e in ev}
        issues = _turns_integrity(tr, s_tr, ev_turns, st["turn"], inst.name, now)
    whole = not issues   # 事件流、state.json、轮次记录都完整：分母可证
    rounds = sorted(t for t in {e["turn"] for _, e in ev} if 1 <= t < st["turn"] and in_win(t)) if whole else []
    out["window"]["rounds"] = rounds
    out["window"]["denominator_certain"] = whole

    # —— manual_rate：事件流完整、且本轮已按协议证明关闭，才计入 ——
    m = _metric(["events", "state", "turns"])
    good, why_rounds, manual_rounds = [], {}, []
    if whole:
        for t in rounds:
            why = _closed(t, ev, tr)
            if why:
                why_rounds[t] = why
                continue
            good.append(t)
            if any(_manual(e) for _, e in ev if e["turn"] == t):   # 已证明边界之后没有本轮的事件
                manual_rounds.append(t)
        m["n"] = len(good)
        if good:
            m["value"], m["unknown"] = len(manual_rounds) / len(good), False
        if rounds:
            m["coverage"] = len(good) / len(rounds)
        m["diagnostic"]["why"] = None if good else (
            "窗口内没有已结束的正式轮次（第 0 轮与当前轮不计）" if not rounds else "窗口内没有能证明已关闭的轮次")
    else:
        m["diagnostic"]["why"] = "事件流、state.json 或轮次记录不完整，无法证明分母与各轮完整，整体不计算"
    m["diagnostic"]["issues"] = issues + [f"第 {t} 轮不计入：{w}" for t, w in sorted(why_rounds.items())]
    m["diagnostic"]["observed"] = {"rounds_counted": good, "rounds_with_manual": manual_rounds}
    out["metrics"]["manual_rate"] = m

    # —— queue_share：没有任务就绪/领取事件协议 ——
    q = _metric([], "没有可证的任务就绪（ready_ts）与领取（picked_ts）事件协议，无法计算排队占比；"
                    "WAIT_REPLY、额度或仲裁等待不是这个口径")
    out["metrics"]["queue_share"] = q

    # —— first_pass_rate：只给观测计数（事件流不完整时连观测计数也不给） ——
    f = _metric(["events", "adapter_log"], "物理只发送一次不可证：适配器日志写失败可能被吞，日志条数不代表物理发送次数；"
                                            "也没有返工的完整判定协议")
    sends, al_issue = {}, []
    for ln, r in al:
        if r.get("action") != "send":
            continue
        if "project" not in r:
            al_issue.append(f"适配器日志第 {ln} 行没有 project")
            continue
        if r.get("project") != inst.name:
            continue
        t = r.get("turn")
        if not _is_int(t):
            al_issue.append(f"适配器日志第 {ln} 行没有整数轮号")
            continue
        if t in rounds:
            sends[t] = sends.get(t, 0) + 1
    if s_al["bad_count"]:
        al_issue.append(f"适配器日志坏行 {s_al['bad_count']} 行")
    if not whole:
        al_issue.insert(0, "事件流、state.json 或轮次记录不完整：不给观测计数")
    f["diagnostic"]["issues"] = al_issue
    f["diagnostic"]["observed"] = {} if not whole else {
        "rounds_with_round_result_ok": sorted(t for t in rounds if any(
            e["turn"] == t and e.get("type") == "round_result" and e.get("ok") is True for _, e in ev)),
        "rounds_with_one_logged_send": sorted(t for t, c in sends.items() if c == 1),
        "logged_sends_by_round": {str(t): c for t, c in sorted(sends.items())}}
    out["metrics"]["first_pass_rate"] = f

    # —— rework_rate：只给已知返工事件的观测计数；没有事件不等于没有返工 ——
    r = _metric(["events"], "返工没有完整的判定协议（版本重交付、计划过时笔记等不在有序号的事件流里）；"
                            "下面只是已知返工事件的观测计数，没有事件不代表没有返工")
    seen_rw = {}
    if whole:
        for _, e in ev:
            t = e["turn"]
            k = (f"auto:{e.get('rule')}" if e.get("type") == "auto" and e.get("rule") in _REWORK_RULES
                 else e.get("type") if e.get("type") in _REWORK_TYPES else None)
            if k and t in rounds:
                seen_rw.setdefault(str(t), {}).setdefault(k, 0)
                seen_rw[str(t)][k] += 1
    else:
        r["diagnostic"]["issues"] = ["事件流、state.json 或轮次记录不完整：不给观测计数"]
    r["diagnostic"]["observed"] = {} if not whole else {"known_rework_events_by_round": seen_rw}
    out["metrics"]["rework_rate"] = r
    return out
