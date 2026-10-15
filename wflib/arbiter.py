"""1.8.8（P0-G，collab-wf-dev 的 ISOLATION.md 第 5 节 S4）：同一账号上的发送槽仲裁。

多个项目共用一个账号与通道。policy 的 shared_max_inflight（默认 0＝不仲裁，行为与 1.8.7 相同）大于 0 时，发送前先拿槽：
- 拿槽在 local/shared/send.lock 的文件锁里完成，只持有几毫秒；状态在 local/shared/arbiter.json，留痕在 arbiter.jsonl
  （wait、grant、release、reclaim、unknown）。
- 先到先得：等待队列按先后排，队首拿到槽才轮到下一个；拿不到不算失败，按 30 秒退避再看。
- 释放：本项目这一轮的回复落地、或发送明确失败时释放。
- 崩溃与过期回收：持有方已不在“发送 / 等回复”（含从这两步停下的等待用户与阻塞），或换到了新的一轮，或租约（shared_lease_s，
  默认 3600 秒）到期，就回收；读不到持有方状态（例如项目目录不在了）记一次 unknown，等租约到期再回收。
  排队的项目已不在“发送”，也从队列里去掉，不会让死掉的等待挡住后面的人。

B1-A（发送槽精确持有者）：
- 每次新授予生成唯一的 lease_id，并记下本项目本轮第几次授予（attempt）与 granted_at；同项目同轮重入返回同一个 token。
- release 校验项目、轮次与 token：带 token 时三者都对上才释放；不带 token 的旧调用只释放没有 token 的旧记录，
  不能释放新记录。没对上不算成功：返回原因，并在 arbiter.jsonl 记 release_miss。
- 没有 token 的旧记录照样计入占用、照样回收；只有调用方已证明这次授予之后没有外发时，才用 migrate_legacy 补发 token。
- arbiter.json 读不了或结构不对时不授予、不释放、不改写（返回 error / arbiter_unreadable），等人修复，
  不再把坏文件当成空表覆盖。
- r2 外发前标记：新授予带 premark=true，表示这个租约下的每次外发之前，都先由 mark_sending 在锁内为该 token 写入
  send_marked_at 并 fsync（文件与目录）；写不成就不外发。所以 premark 租约上没有标记，就是它下面没有发生过外发的证据。
  适配器动作日志不能证明没有外发（追加可能静默失败），不作为证据。"""
from __future__ import annotations

import fcntl
import json
import os
import secrets
from contextlib import contextmanager
from pathlib import Path

from .core import LOCAL, now_iso, read_json, secs_since

LIVE = ("SEND", "WAIT_REPLY")


class Unreadable(Exception):
    """B1-A：arbiter.json 存在，但读不了、不是 JSON，或 holders / queue 的结构不对。"""


def _dir() -> Path:
    d = LOCAL / "shared"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def _lock():
    fh = open(_dir() / "send.lock", "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def _load() -> dict:
    # B1-A：文件不存在是“还没人拿过槽”；存在却读不了、不是 JSON 或结构不对，一律不可读（此前当作空表，会超发并覆盖证据）
    p = _dir() / "arbiter.json"
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"holders": [], "queue": [], "attempt_seq": {}}
    except (OSError, UnicodeDecodeError) as e:
        raise Unreadable(f"读不了 arbiter.json（{type(e).__name__}）")
    try:
        s = json.loads(raw)
    except ValueError:
        raise Unreadable("arbiter.json 不是合法的 JSON")
    if not isinstance(s, dict):
        raise Unreadable("arbiter.json 顶层不是对象")
    holders, queue = s.get("holders", []), s.get("queue", [])
    if not isinstance(holders, list) or not isinstance(queue, list) \
            or not all(isinstance(x, dict) and x.get("project") for x in holders + queue):
        raise Unreadable("arbiter.json 的 holders / queue 结构不对")
    seq = s.get("attempt_seq")
    seq = {k: v for k, v in seq.items() if isinstance(v, dict)} if isinstance(seq, dict) else {}
    return {"holders": list(holders), "queue": list(queue), "attempt_seq": seq}


def _save(s: dict, durable: bool = False) -> None:
    p = _dir() / "arbiter.json"
    tmp = p.with_name(".arbiter.json.tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    if durable:   # B1-A r2：外发前标记必须在调用适配器之前真正落盘
        _fsync_path(tmp)
    os.replace(tmp, p)
    if durable:
        _fsync_path(p.parent)


def _fsync_path(path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        if hasattr(fcntl, "F_FULLFSYNC"):   # macOS 的 fsync 不保证写到盘上，先用 F_FULLFSYNC
            try:
                fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
                return
            except OSError:
                pass
        os.fsync(fd)
    finally:
        os.close(fd)


def _log(**kw) -> None:
    with open(_dir() / "arbiter.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now_iso(), "resource": "send", **kw}, ensure_ascii=False) + "\n")


def _state_of(rec: dict):
    root = rec.get("root")
    return read_json(Path(root) / "workflow" / "state.json") if root else None


def _canon(root) -> str:
    try:
        return os.path.realpath(str(root)) if root else ""
    except (OSError, ValueError):
        return ""


def _same(h, me: dict) -> bool:
    """B2-A2a：同一项目 = 项目名相同，且两边都有 root、规范化后相同。同名不同目录的、没有 root 的旧记录都按外部持有者处理。"""
    if not isinstance(h, dict) or h.get("project") != me.get("project"):
        return False
    a = _canon(h.get("root"))
    return bool(a) and a == _canon(me.get("root"))


def _live(rec: dict, states) -> str:
    st = _state_of(rec)
    if not st:
        return "unknown"
    s = st.get("state")
    if s in ("AWAITING_USER", "BLOCKED", "QUOTA_WAIT"):   # B2-A2a：等额度时按 return_to 判断，租约期内不当作过期
        s = st.get("return_to") or st.get("blocked_from") or s
    return "live" if st.get("turn") == rec.get("turn") and s in states else "stale"


def _next_attempt(s: dict, project: str, turn) -> int:
    # B1-A：本项目本轮第几次授予（换了一轮从 1 重新计）。只用于留痕与核对，唯一性由 lease_id 保证
    rec = s["attempt_seq"].get(project) or {}
    try:
        n = int(rec.get("n") or 0) if rec.get("turn") == turn else 0
    except (TypeError, ValueError):
        n = 0
    s["attempt_seq"][project] = {"turn": turn, "n": n + 1}
    return n + 1


def _lease(h: dict) -> dict:
    return {"lease_id": h.get("lease_id"), "attempt": h.get("attempt"), "granted_at": h.get("granted_at") or h.get("since"),
            "premark": h.get("premark") is True, "send_marked_at": h.get("send_marked_at")}


def _token_miss(mine: list, lease_id) -> str:
    # 带 token 的调用没有命中时的原因（release 与 mark_sending 共用）
    return ("turn_mismatch" if any(h.get("lease_id") == lease_id for h in mine)
            else "token_mismatch" if any(h.get("lease_id") for h in mine)
            else "legacy_holder" if mine else "not_held")


def acquire(inst, max_inflight: int = 1, lease_s: int = 3600, migrate_legacy: bool = False) -> dict:
    """返回 granted、position、holder（1.8.8），以及 B1-A 的 lease_id、attempt、granted_at、reentry、legacy。
    新授予生成唯一 token；同项目同轮重入返回已有的 token（reentry=True）。已有记录没有 token（旧版留下的）时，
    返回 lease_id=None、legacy=True，不自行补发：只有 migrate_legacy=True（调用方已证明这次授予之后没有外发）才补发。
    arbiter.json 不可读时不授予、不改写，返回 error。r2：另返回 premark 与 send_marked_at（只增不改）。"""
    me = {"project": inst.name, "turn": inst.state.get("turn"), "root": str(inst.root)}
    none = {"lease_id": None, "attempt": None, "granted_at": None, "reentry": False, "legacy": False, "premark": False,
            "send_marked_at": None}
    with _lock():
        try:
            s = _load()
        except Unreadable as e:
            return {"granted": False, "position": 0, "holder": "（仲裁状态不可读）", "error": str(e), **none}
        keep = []
        for h in s["holders"]:
            if _same(h, me):
                if h.get("turn") == me["turn"]:
                    keep.append(h)
                else:
                    _log(event="reclaim", project=h.get("project"), turn=h.get("turn"), lease_id=h.get("lease_id"),
                         why="同一项目已进入新的一轮")
                continue
            v = "stale" if (secs_since(h.get("since")) or 0) > lease_s else _live(h, LIVE)
            if v == "stale":
                _log(event="reclaim", project=h.get("project"), turn=h.get("turn"), lease_id=h.get("lease_id"),
                     why="持有方已不在发送或等回复，或租约到期")
                continue
            if v == "unknown" and not h.get("unknown_logged"):
                h["unknown_logged"] = True
                if not h.get("root"):   # B2-A2a：没有 root 的旧记录按外部处理，最多多占到租约期满，不误释放
                    _log(event="legacy_no_root", project=h.get("project"), turn=h.get("turn"), lease_id=h.get("lease_id"),
                         why="持有记录没有 root，认不出是不是本项目，按外部持有者等租约到期")
                else:
                    _log(event="unknown", project=h.get("project"), turn=h.get("turn"), why="读不到持有方的状态，等租约到期再回收")
            keep.append(h)
        q = []
        for x in s["queue"]:
            if _same(x, me):
                if x.get("turn") == me["turn"]:
                    q.append(x)
                continue
            if _live(x, ("SEND",)) == "live" and (secs_since(x.get("since")) or 0) <= lease_s:
                q.append(x)
        s["holders"], s["queue"] = keep, q
        mine = next((h for h in keep if _same(h, me)), None)
        if mine is not None:
            if not mine.get("lease_id") and migrate_legacy:
                mine.update(lease_id=secrets.token_hex(8), attempt=_next_attempt(s, me["project"], me["turn"]),
                            granted_at=mine.get("since") or now_iso(), migrated_at=now_iso())
                _log(event="migrate", project=me["project"], turn=me["turn"], lease_id=mine["lease_id"],
                     attempt=mine["attempt"])
            _save(s)
            if not mine.get("lease_id"):
                return {"granted": True, "position": 0, "holder": me["project"], **none,
                        "granted_at": mine.get("since"), "reentry": True, "legacy": True}
            return {"granted": True, "position": 0, "holder": me["project"], **_lease(mine), "reentry": True, "legacy": False}
        new = not any(_same(x, me) for x in q)
        if new:
            q.append({**me, "since": now_iso()})
        if len(keep) < max_inflight and _same(q[0], me):
            x = q.pop(0)
            ts = now_iso()
            h = {**me, "since": ts, "lease_id": secrets.token_hex(8), "attempt": _next_attempt(s, me["project"], me["turn"]),
                 "granted_at": ts, "premark": True}
            keep.append(h)
            _log(event="grant", project=me["project"], turn=me["turn"], lease_id=h["lease_id"], attempt=h["attempt"],
                 waited_s=int(secs_since(x.get("since")) or 0))
            _save(s)
            return {"granted": True, "position": 0, "holder": me["project"], **_lease(h), "reentry": False, "legacy": False}
        pos = next(i for i, x in enumerate(q) if _same(x, me)) + 1
        holder = "、".join(f"{h.get('project')}#{h.get('turn')}" for h in keep) or "（无）"
        if new:
            _log(event="wait", project=me["project"], turn=me["turn"], holder=holder, position=pos,
                 reason=f"inflight {len(keep)}/{max_inflight}")
        _save(s)
        return {"granted": False, "position": pos, "holder": holder, **none}


def release(inst, why: str, lease_id=None, turn=None) -> dict:
    """B1-A：精确释放，返回 {"released": bool, "reason": str}。
    带 lease_id：项目、轮次（turn 不给时取当前状态的轮次）与 token 三者都对上才释放；对不上的一律不动
    （迟到的、错轮的、错 token 的都不能释放新记录）。
    不带 lease_id（1.8.8 的旧调用）：只释放本项目没有 token 的旧记录（给了 turn 时轮次也要一致），遇到带 token 的记录不释放。
    没有释放时返回原因（not_held、turn_mismatch、token_mismatch、legacy_holder、token_required、arbiter_unreadable），
    并在 arbiter.jsonl 记 release_miss；不可读时不改写文件。"""
    name = inst.name
    me = {"project": name, "root": str(inst.root)}   # B2-A2a：只认同名且同一规范目录的记录
    if lease_id and turn is None:
        turn = inst.state.get("turn")
    with _lock():
        try:
            s = _load()
        except Unreadable:
            return {"released": False, "reason": "arbiter_unreadable"}
        mine = [h for h in s["holders"] if _same(h, me)]
        if lease_id:
            hit = [h for h in mine if h.get("lease_id") == lease_id and h.get("turn") == turn]
            reason = _token_miss(mine, lease_id)
        else:
            hit = [h for h in mine if not h.get("lease_id") and (turn is None or h.get("turn") == turn)]
            reason = ("token_required" if any(h.get("lease_id") for h in mine)
                      else "turn_mismatch" if mine else "not_held")
        if not hit:
            _log(event="release_miss", project=name, turn=turn, lease_id=lease_id, why=why, reason=reason)
            return {"released": False, "reason": reason}
        s["holders"] = [h for h in s["holders"] if not any(h is x for x in hit)]
        for h in hit:
            _log(event="release", project=name, turn=h.get("turn"), lease_id=h.get("lease_id"), attempt=h.get("attempt"),
                 why=why, **({} if lease_id else {"legacy": True}))
        _save(s)
        return {"released": True, "reason": "released" if lease_id else "released_legacy"}


def mark_sending(inst, lease_id, turn=None) -> dict:
    """B1-A r2：外发前必经的持久标记，返回 {"marked": bool, "reason": str}。项目、轮次（不给时取当前状态的轮次）与 token
    都对上才写：在锁内给该持有记录写 send_marked_at、send_marks，并把 arbiter.json 连同目录 fsync 之后才返回 marked=True。
    驱动器只在 marked=True 之后调用适配器 send；没对上（not_held、turn_mismatch、token_mismatch、legacy_holder）、
    不可读（arbiter_unreadable）或写盘失败（io_error）都返回 False，调用方不得外发。"""
    name = inst.name
    me = {"project": name, "root": str(inst.root)}   # B2-A2a
    if turn is None:
        turn = inst.state.get("turn")
    with _lock():
        try:
            s = _load()
        except Unreadable:
            return {"marked": False, "reason": "arbiter_unreadable"}
        mine = [h for h in s["holders"] if _same(h, me)]
        hit = next((h for h in mine if lease_id and h.get("lease_id") == lease_id and h.get("turn") == turn), None)
        if hit is None:
            reason = _token_miss(mine, lease_id)
            _log(event="mark_miss", project=name, turn=turn, lease_id=lease_id, reason=reason)
            return {"marked": False, "reason": reason}
        try:
            n = int(hit.get("send_marks") or 0) + 1
        except (TypeError, ValueError):
            n = 1
        hit.update(send_marked_at=now_iso(), send_marks=n)
        try:
            _save(s, durable=True)
        except OSError as e:
            return {"marked": False, "reason": f"io_error:{type(e).__name__}"}
        _log(event="send_mark", project=name, turn=turn, lease_id=lease_id, attempt=hit.get("attempt"), n=n)
        return {"marked": True, "reason": "marked"}
