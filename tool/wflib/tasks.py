"""1.6.6：执行端待办（collab-wf-dev 第 58 轮：回复正文里写给执行端的事，执行端做完了却没有接上回报，
流程停在空闲；服务都健康，看门狗也看不见，业务却停了）。

Claude 在【驱动指令】里写“执行端 N：<事项>”。驱动器验收后把它们登记在状态的 executor_tasks 里，每一项都进 inbox
（交给执行端，超过 2 小时转给用户）。执行端做完一项就 wf task done <编号> --note "结果"，结果作为说明写进回报。
“之后：等执行端”或“之后：空闲”而又有未完成的待办时，驱动器停在等待；全部做完（或到了最迟时间）就自动生成回报，
不再需要执行端记得 wf kick。
"""
from __future__ import annotations

from .core import Instance, WfError, append_jsonl, now_iso

TIMEOUT_S = 14400   # 最迟等 4 小时；policy.json 的 executor_task_timeout_s 可改（时间参数，改动不算扩大权限）
KEEP_DONE = 30      # 只保留最近 30 项已完成的待办


def add(inst: Instance, turn: int, items: dict) -> list:
    """登记本轮的执行端待办（编号 T<轮次>-<序号>）；同一项不重复登记。返回新登记的。"""
    if not items:
        return []
    st = inst.state
    cur = st.setdefault("executor_tasks", [])
    have = {t.get("id") for t in cur}
    added = []
    for n in sorted(items, key=lambda x: int(x)):
        tid = f"T{turn}-{n}"
        if tid in have:
            continue
        t = {"id": tid, "turn": turn, "text": str(items[n])[:500], "since": now_iso(), "done": None, "note": ""}
        cur.append(t)
        added.append(t)
    done = [t for t in cur if status_of(t) != "open"]   # 1.8.2：已完成、已取消、已取代都算已关闭
    if len(done) > KEEP_DONE:
        drop = {t["id"] for t in done[:len(done) - KEEP_DONE]}
        cur[:] = [t for t in cur if t["id"] not in drop]
    if added:
        inst.event("tasks_opened", ids=[t["id"] for t in added], by="driver")
    inst.save()
    return added


STATUS_CN = {"open": "未完成", "done": "完成", "cancelled": "取消", "superseded": "被取代"}


def status_of(t: dict) -> str:
    """1.8.2：待办的状态 open / done / cancelled / superseded；1.8.2 之前的记录只有 done 字段。"""
    s = t.get("status")
    if s in ("cancelled", "superseded"):
        return s
    return "done" if t.get("done") else "open"


def open_items(inst: Instance) -> list:
    return [t for t in (inst.state.get("executor_tasks") or []) if status_of(t) == "open"]


def done(inst: Instance, tid: str, note: str = "") -> dict:
    """执行端完成一项待办：记 by=executor 的事件，并把结果作为说明写进当前轮的备注（随下一份回报发出）。"""
    want = (tid or "").strip().upper()
    for t in inst.state.get("executor_tasks") or []:
        if str(t.get("id", "")).upper() != want:
            continue
        if status_of(t) in ("cancelled", "superseded"):
            raise WfError(f"{t['id']} 已{STATUS_CN[status_of(t)]}（{t.get('reason') or '没有写原因'}），不能再记为完成")
        if t.get("done"):
            raise WfError(f"{t['id']} 已在 {t['done']} 完成")
        if not (note or "").strip():   # 1.8.2（P0-B）：完成必须写结果，不再出现“没有写说明”的完成
            raise WfError("记为完成必须写结果：wf task done <编号> --note \"结果\"；不再需要的待办用 wf task cancel 或 wf task supersede")
        t["done"], t["note"], t["status"] = now_iso(), (note or "").strip()[:1000], "done"
        append_jsonl(inst.turn_dir() / "notes.jsonl",
                     {"ts": now_iso(), "kind": "info",
                      "text": f"执行端待办 {t['id']} 已完成（{t['text'][:80]}）：{t['note'] or '没有写说明'}"})
        inst.event("task_done", id=t["id"], note=t["note"][:300], by="executor")
        inst.save()
        return t
    ids = "、".join(t["id"] for t in open_items(inst)) or "无"
    raise WfError(f"没有这个待办：{tid}。未完成的待办：{ids}")


def close(inst: Instance, tid: str, kind: str, reason: str, by: str = "executor", by_task: str = "") -> dict:
    """1.8.2（P0-B、P1-N）：不再需要的待办如实关闭——cancelled（取消）或 superseded（被取代），必须写原因，
    不再用 done 冒充。已经记为 done 的待办只能改记为 superseded（做过、但已被新的边界或新的待办取代）：
    原来的完成时刻与说明保留在 was_done 里，不抹掉历史。"""
    if kind not in ("cancelled", "superseded"):
        raise WfError("只能记为 cancelled 或 superseded")
    reason = (reason or "").strip()
    if len(reason) < 2:
        raise WfError(f"{'取消' if kind == 'cancelled' else '记为被取代'}必须写原因：--reason \"原因\"")
    want = (tid or "").strip().upper()
    for t in inst.state.get("executor_tasks") or []:
        if str(t.get("id", "")).upper() != want:
            continue
        cur = status_of(t)
        if cur in ("cancelled", "superseded"):
            raise WfError(f"{t['id']} 已{STATUS_CN[cur]}（{t.get('reason') or ''}）")
        if cur == "done" and kind == "cancelled":
            raise WfError(f"{t['id']} 已在 {t['done']} 记为完成，不能取消；它若已被新的边界或待办取代，用 wf task supersede {t['id']} --reason …")
        if cur == "done":
            t["was_done"] = {"done": t.get("done"), "note": t.get("note", "")}
            t["done"] = None
        t.update(status=kind, closed=now_iso(), reason=reason[:500], closed_by=by)
        if by_task:
            t["superseded_by"] = str(by_task).strip().upper()[:40]
        append_jsonl(inst.turn_dir() / "notes.jsonl",
                     {"ts": now_iso(), "kind": "info",
                      "text": f"执行端待办 {t['id']} 已{STATUS_CN[kind]}（{t['text'][:80]}）：{reason[:300]}"
                              + (f"；原先在 {t['was_done']['done']} 记为完成" if cur == "done" else "")
                              + (f"；取代它的是 {t['superseded_by']}" if t.get("superseded_by") else "")})
        inst.event("task_" + kind, id=t["id"], reason=reason[:300], was_done=True if cur == "done" else None,
                   superseded_by=t.get("superseded_by"), by=by)
        inst.save()
        return t
    ids = "、".join(t["id"] for t in open_items(inst)) or "无"
    raise WfError(f"没有这个待办：{tid}。未完成的待办：{ids}")
