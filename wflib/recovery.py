"""B2-A2a：可恢复异常的恢复期（恢复预算）。

此前的处置只有“按轮次:规则”的计数（同一轮内从不清零，换一轮就重来）与各规则自己的退避；跨规则、跨重启、跨轮次
没有统一的期限，额度用尽可以每小时试一次、无限循环。这里给可恢复的异常加一个总预算：

- 一次恢复期（episode）从第一次可恢复的异常开始，记在 state.recovery；since 永不改变，换规则、换轮次、驱动器重启、
  执行端写检查点都不会重开。只有两种正面证据结束它：send_accepted（通道确认接受且不是只有心跳，或结果不明后经轮询
  确认生成已开始或已完成）与 reply_landed（回复落地）。结束的恢复期移入 state.recovery_history，只留最近 10 条。
- 暂时性（SEND_BUSY、NET、POLL_NONE、POLL_READ_ERROR、SEND_UNKNOWN_WAIT）：默认 4 小时、24 次；
  额度（QUOTA）：默认 8 天、12 次试探。policy 只能调小，不能调大；非法值或不大于 0 取默认值。
- 暂时性的恢复期遇到额度用尽时升级为额度类，只升级一次；截止时刻按最初的 since 加额度窗口算，不从现在起算。
  所以截止时刻最多变一次，整个恢复期不超过 since 加 8 天。
- 超出期限或次数即“耗尽”：驱动器在原有处置之上改为阻塞（RECOVERY_EXHAUSTED:<类别>）。只会更严：原处置已经是
  阻塞的照旧，退避、计数、max、then 都不变。
- 额度等待（B2-A2a r2，MAIN 终裁）：resets_at 在未来时等到 max(resets_at, 现在 + 60 秒)；已过去、无效或缺失时，
  第 k 次额度试探等 min(2^(k−1), 24) 小时（第 1 次 1 小时）。都不越过恢复期截止时刻。刚收到“额度用尽”就说明那个
  已过去的时刻是陈旧信息，不再立即试探。
- 发送结果不明（SEND_UNKNOWN_WAIT）：同一个发送意图（驱动器传入的 key＝轮次加意图时刻）在本恢复期内只在第一次计 1 次，
  之后的核对只更新 last；记过的 key 最多 24 个。key 缺失或不合法时每次都计数，不能借此绕过预算。其余规则每次计 1。
- 截止时刻是硬上限：到点（now ≥ deadline）即耗尽。结果不明的核对时间也计入；它自己的一小时窗口照旧，两者谁先到以谁为准。
- 记录必须完整可信才使用：字段、类型、时序（since 不在未来、deadline 晚于 since 且不超过该类别的最长窗口、
  upgraded_at 与 last 的时刻落在合理范围）、计数关系（attempts 与 rules 一致、keys 不多于已计的结果不明次数）都要对。
  否则按“损坏”处理：原样保留、按耗尽停下等人看。任何解析、类型、时间计算出错都按损坏处理（失败关闭），不会抛到驱动器，
  也不会把坏记录改写成正常记录。时钟偏差容忍 60 秒，秒级截断容忍 1 秒，正常重启、时间推移都不会被误判为损坏。

本模块只读写 state.recovery 与 state.recovery_history，不碰发送意图、send_ref、send_slot、executor_tasks，
也不保存状态：由驱动器在原有的保存点落盘（core 的三方合并只写本进程改过的键）。
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from datetime import timedelta

from .core import now as _now
from .core import now_iso, parse_iso

SCHEMA = "recovery/1"
TRANSIENT_RULES = ("SEND_BUSY", "NET", "POLL_NONE", "POLL_READ_ERROR", "SEND_UNKNOWN_WAIT")
QUOTA_RULES = ("QUOTA",)
DEFAULTS = {"transient": {"window_s": 14400, "attempts": 24}, "quota": {"window_s": 691200, "attempts": 12}}
POLICY_KEYS = {"transient": ("recovery_transient_window_s", "recovery_transient_attempts"),
               "quota": ("recovery_quota_window_s", "recovery_quota_attempts")}
EVIDENCE = ("send_accepted", "reply_landed")
HISTORY_KEEP = 10
QUOTA_STEP_MAX_H = 24
QUOTA_MIN_WAIT_S = 60          # 未来的 resets_at 也至少等 60 秒，防止时钟偏差造成密集试探
CLOCK_SKEW_S = 60              # 校验“不在未来”时容忍的时钟偏差
KEYS_MAX = 24                  # 记过的发送意图 key 最多 24 个
KEY_MAX = 120                  # 单个 key 的最大长度；更长的视为不合法，每次都计数
DETAIL_MAX = 200
COUNT_MAX = 100000
RAW_MAX = 4000                 # 归档损坏记录时原样保留的上限，超过只留长度与摘要
FIELDS = {"schema", "id", "class", "since", "deadline", "upgraded_at", "attempts", "rules", "last", "keys"}
_HEX32 = re.compile(r"[0-9a-f]{32}")
_TOL = timedelta(seconds=1)    # 秒级截断


def classify(rule_id):
    """可恢复的规则返回 transient 或 quota，其余返回 None（不进恢复期，照原处置）。"""
    if not isinstance(rule_id, str):
        return None
    if rule_id in TRANSIENT_RULES:
        return "transient"
    if rule_id in QUOTA_RULES:
        return "quota"
    return None


def _lower(v, default: int) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return default
    try:
        iv = int(v)
    except (OverflowError, ValueError):
        return default
    return default if iv <= 0 else min(iv, default)


def limits(pol=None) -> dict:
    """各类别的窗口（秒）与次数上限；policy 只能调小。"""
    pol = pol if isinstance(pol, dict) else {}
    return {cls: {"window_s": _lower(pol.get(POLICY_KEYS[cls][0]), d["window_s"]),
                  "attempts": _lower(pol.get(POLICY_KEYS[cls][1]), d["attempts"])}
            for cls, d in DEFAULTS.items()}


def _iso(t) -> str:
    return t.isoformat(timespec="seconds")


def _ts(v):
    """合法时刻返回带时区的 datetime；不是文本、过长、解析不了、或加上最长窗口会越界（例如 9999 年）的返回 None。"""
    if not isinstance(v, str) or not v or len(v) > 64:
        return None
    try:
        d = parse_iso(v)
        if d is None:
            return None
        d + timedelta(seconds=2 * DEFAULTS["quota"]["window_s"])   # 越界探针：后面所有的加法都不会溢出
        d - timedelta(seconds=2 * DEFAULTS["quota"]["window_s"])
        return d
    except (ValueError, OverflowError, TypeError):
        return None


def _count(v, lo=0) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= COUNT_MAX


def _check_inner(ep, t) -> str:
    if not isinstance(ep, dict):
        return "不是对象"
    if any(not isinstance(k, str) for k in ep) or set(ep) - FIELDS:
        return "有未知字段"
    if ep.get("schema") != SCHEMA:
        return "schema 不符"
    if not (isinstance(ep.get("id"), str) and _HEX32.fullmatch(ep["id"])):
        return "id 不合法"
    cls = ep.get("class")
    if not (isinstance(cls, str) and cls in DEFAULTS):
        return "class 不合法"
    since, deadline = _ts(ep.get("since")), _ts(ep.get("deadline"))
    if since is None or deadline is None:
        return "since 或 deadline 不是合法时刻"
    skew = timedelta(seconds=CLOCK_SKEW_S)
    if since > t + skew:
        return "since 在未来"
    if not deadline > since:
        return "deadline 不晚于 since"
    if deadline - since > timedelta(seconds=DEFAULTS[cls]["window_s"]) + _TOL:
        return "deadline 超出该类别的最长窗口"
    up = ep.get("upgraded_at")
    if up is not None:
        u = _ts(up)
        if cls != "quota" or u is None or u + _TOL < since or u > deadline or u > t + skew:
            return "upgraded_at 不合法或时序不对"
    a, r = ep.get("attempts"), ep.get("rules")
    if not (isinstance(a, dict) and set(a) == set(DEFAULTS) and all(_count(a[k]) for k in DEFAULTS)):
        return "attempts 不合法"
    if not (isinstance(r, dict) and all(isinstance(k, str) and classify(k) and _count(v, 1) for k, v in r.items())):
        return "rules 不合法"
    if a["transient"] != sum(v for k, v in r.items() if classify(k) == "transient") \
            or a["quota"] != sum(v for k, v in r.items() if classify(k) == "quota"):
        return "attempts 与 rules 对不上"
    if a["transient"] + a["quota"] < 1:
        return "没有任何计数"
    if cls == "transient" and a["quota"]:
        return "暂时性恢复期里有额度试探"
    if cls == "quota" and (a["quota"] < 1 or (up is not None and a["transient"] < 1)):
        return "额度恢复期的计数与升级记录对不上"
    keys = ep.get("keys", [])
    if not (isinstance(keys, list) and len(keys) <= KEYS_MAX and all(isinstance(k, str) and 0 < len(k) <= KEY_MAX for k in keys)
            and len(set(keys)) == len(keys) and len(keys) <= r.get("SEND_UNKNOWN_WAIT", 0)):
        return "keys 不合法"
    last = ep.get("last")
    if not (isinstance(last, dict) and set(last) == {"ts", "rule", "detail"}):
        return "last 不合法"
    lt = _ts(last.get("ts"))
    if lt is None or lt + _TOL < since or lt > t + skew:
        return "last 的时刻不合法或时序不对"
    if not (isinstance(last.get("rule"), str) and last["rule"] in r and isinstance(last.get("detail"), str)
            and len(last["detail"]) <= DETAIL_MAX):
        return "last 的规则或说明不合法"
    return ""


def _check(ep, t) -> str:
    """合法返回空串，否则返回原因（短文本，不回显原始内容）。校验本身出错也按损坏处理。"""
    try:
        return _check_inner(ep, t)
    except Exception as e:   # noqa: BLE001  失败关闭
        return f"无法校验（{type(e).__name__}）"


def _exhausted(ep: dict, lim: dict, t) -> str:
    since = parse_iso(ep["since"])
    window = lim[ep["class"]]["window_s"]
    deadline = min(parse_iso(ep["deadline"]), since + timedelta(seconds=window))   # policy 事后调小也立即生效
    if t >= deadline:   # 硬上限：到点即耗尽
        return f"恢复期自 {ep['since']} 起已到 {window} 秒的上限（截止 {_iso(deadline)}）"
    for cls in ("transient", "quota"):
        if ep["attempts"][cls] > lim[cls]["attempts"]:
            return f"{'暂时性重试' if cls == 'transient' else '额度试探'}已到第 {ep['attempts'][cls]} 次，上限 {lim[cls]['attempts']} 次"
    return ""


def _corrupt(cls, why: str) -> dict:
    return {"episode": None, "exhausted": True, "corrupt": True, "class": cls,
            "why": f"恢复期记录（state.recovery）损坏或无法校验：{why}；原样保留，停下等人看"}


def _advance(cur, rule_id, cls, detail, key, lim, t):
    """返回 (新记录, "")；已过最长窗口、不能再升级时返回 (None, 原因)，此时记录不变。新记录是副本，原记录不动。"""
    if cur is None:
        ep = {"schema": SCHEMA, "id": uuid.uuid4().hex, "class": cls, "since": _iso(t),
              "deadline": _iso(t + timedelta(seconds=lim[cls]["window_s"])), "upgraded_at": None,
              "attempts": {"transient": 0, "quota": 0}, "rules": {}, "last": None, "keys": []}
    else:
        ep = copy.deepcopy(cur)
        ep.setdefault("keys", [])
        if ep["class"] == "transient" and cls == "quota":
            qdl = parse_iso(ep["since"]) + timedelta(seconds=lim["quota"]["window_s"])
            if t >= qdl:
                return None, f"恢复期自 {ep['since']} 起已到额度窗口 {lim['quota']['window_s']} 秒的上限，不再升级"
            # 只升级一次；截止时刻由最初的 since 推出，不从现在起算
            ep.update({"class": "quota", "upgraded_at": _iso(t), "deadline": _iso(qdl)})
    k = key if rule_id == "SEND_UNKNOWN_WAIT" and isinstance(key, str) and 0 < len(key) <= KEY_MAX else None
    if k is None or k not in ep["keys"]:
        ep["attempts"][cls] += 1
        ep["rules"][rule_id] = ep["rules"].get(rule_id, 0) + 1
        if k is not None and len(ep["keys"]) < KEYS_MAX:
            ep["keys"].append(k)
    # 同一发送意图的后续核对：不再计数，只更新 last
    ep["last"] = {"ts": _iso(t), "rule": rule_id, "detail": str(detail or "")[:DETAIL_MAX]}
    return ep, ""


def note(inst, rule_id, *, detail="", now=None, pol=None, key=None) -> dict:
    """记一次可恢复的异常：没有恢复期就新开，已有就累加次数、更新 last；since 永不改变。
    key 只对 SEND_UNKNOWN_WAIT 生效：同一个 key 在本恢复期内只计一次。返回 {episode, exhausted, why, class}；
    不可恢复的规则返回 episode=None。记录损坏或计算出错时返回 exhausted=True、corrupt=True，记录原样不动。
    只改内存中的状态，由调用方保存；从不抛出异常。"""
    cls = classify(rule_id)
    if cls is None:
        return {"episode": None, "exhausted": False, "why": "", "class": None}
    try:
        t = now or _now()
        st = inst.state
        lim = limits(pol)
        cur = st.get("recovery")
        if cur is not None:
            bad = _check(cur, t)
            if bad:
                return _corrupt(cls, bad)
        ep, why = _advance(cur, rule_id, cls, detail, key, lim, t)
        if ep is None:
            return {"episode": cur, "exhausted": True, "why": why, "class": cur["class"]}
        if _check(ep, t):   # 自检：算出来的记录必须仍然合法，否则不写入
            return _corrupt(cls, "更新后的记录不合法")
        why = _exhausted(ep, lim, t)
        st["recovery"] = ep
        return {"episode": ep, "exhausted": bool(why), "why": why, "class": ep["class"]}
    except Exception as e:   # noqa: BLE001  失败关闭：不抛到驱动器，按耗尽停下
        return _corrupt(cls, f"计算出错（{type(e).__name__}）")


def close(inst, *, evidence) -> dict | None:
    """只有正面证据能结束恢复期：send_accepted 或 reply_landed。没有恢复期时返回 None。只改内存，由调用方保存。
    损坏的记录也会归档（原样，过大时只留摘要），历史只留最近 10 条。"""
    if evidence not in EVIDENCE:
        raise ValueError(f"恢复期只能由 {'、'.join(EVIDENCE)} 结束，不能由 {evidence!r} 结束")
    try:
        st = inst.state
        ep = st.get("recovery")
        if ep is None:
            return None
        bad = _check(ep, _now())
        if bad:
            raw = json.dumps(ep, ensure_ascii=False, default=str)
            rec = ({"corrupt": True, "why": bad, "raw": ep} if len(raw) <= RAW_MAX else
                   {"corrupt": True, "why": bad, "raw_len": len(raw), "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()})
        else:
            rec = copy.deepcopy(ep)
        rec.update(closed_at=now_iso(), closed_by=evidence)
        hist = st.get("recovery_history")
        hist = list(hist) if isinstance(hist, list) else ([] if hist is None else [{"corrupt_history": type(hist).__name__}])
        st["recovery_history"] = (hist + [rec])[-HISTORY_KEEP:]
        st.pop("recovery", None)
        return rec
    except Exception:   # noqa: BLE001  归档失败时记录原样留着，不影响发送与回复的正常处理
        return None


def quota_until(inst, resets_at, *, now=None):
    """额度等待到何时（MAIN 终裁）：resets_at 在未来就等到 max(resets_at, 现在 + 60 秒)；已过去、无效或缺失时第 k 次
    额度试探等 min(2^(k−1), 24) 小时。都不越过恢复期截止时刻。从不抛出异常：出错时等 1 小时。"""
    t = now or _now()
    try:
        ep = inst.state.get("recovery")
        ok = ep is not None and not _check(ep, t)
        r = _ts(resets_at)
        if r is not None and r > t:
            until = max(r, t + timedelta(seconds=QUOTA_MIN_WAIT_S))
        else:
            k = max(1, ep["attempts"]["quota"]) if ok else 1
            until = t + timedelta(hours=min(2 ** min(k - 1, 10), QUOTA_STEP_MAX_H))
        if ok:
            dl = _ts(ep["deadline"])
            if until > dl:
                until = max(dl, t)   # 到点那一刻再试探，按硬上限即耗尽
        return until
    except Exception:   # noqa: BLE001
        return t + timedelta(hours=1)


def status(inst, *, now=None) -> dict | None:
    """给 explain 用：没有进行中的恢复期返回 None；损坏时给出诊断，从不抛出异常。"""
    try:
        ep = inst.state.get("recovery")
        if ep is None:
            return None
        bad = _check(ep, now or _now())
        if bad:
            return {"active": True, "corrupt": True, "why": bad,
                    "text": f"恢复期记录（state.recovery）损坏：{bad}；下一次可恢复的异常会直接停下等人看"}
        a, last = ep["attempts"], ep["last"]
        text = (f"恢复期（{'额度' if ep['class'] == 'quota' else '暂时性'}）自 {ep['since']} 起，截止 {ep['deadline']}；"
                f"已重试 {a['transient']} 次、额度试探 {a['quota']} 次；最近一次 {last['rule']}（{last['ts']}）")
        return {"active": True, "corrupt": False, "id": ep["id"], "class": ep["class"], "since": ep["since"],
                "deadline": ep["deadline"], "attempts": dict(a), "last": dict(last), "text": text}
    except Exception as e:   # noqa: BLE001
        return {"active": True, "corrupt": True, "why": type(e).__name__, "text": "恢复期记录无法读取，下一次可恢复的异常会直接停下等人看"}
