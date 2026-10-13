"""1.8.8（P0-G，collab-wf-dev 的 ISOLATION.md 第 5 节 S4）：同一账号上的发送槽仲裁。

多个项目共用一个账号与通道。policy 的 shared_max_inflight（默认 0＝不仲裁，行为与 1.8.7 相同）大于 0 时，发送前先拿槽：
- 拿槽在 local/shared/send.lock 的文件锁里完成，只持有几毫秒；状态在 local/shared/arbiter.json，留痕在 arbiter.jsonl
  （wait、grant、release、reclaim、unknown）。
- 先到先得：等待队列按先后排，队首拿到槽才轮到下一个；拿不到不算失败，按 30 秒退避再看。
- 释放：本项目这一轮的回复落地、或发送明确失败时释放。
- 崩溃与过期回收：持有方已不在“发送 / 等回复”（含从这两步停下的等待用户与阻塞），或换到了新的一轮，或租约（shared_lease_s，
  默认 3600 秒）到期，就回收；读不到持有方状态（例如项目目录不在了）记一次 unknown，等租约到期再回收。
  排队的项目已不在“发送”，也从队列里去掉，不会让死掉的等待挡住后面的人。"""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path

from .core import LOCAL, now_iso, read_json, secs_since

LIVE = ("SEND", "WAIT_REPLY")


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
    s = read_json(_dir() / "arbiter.json", {}) or {}
    return {"holders": list(s.get("holders") or []), "queue": list(s.get("queue") or [])}


def _save(s: dict) -> None:
    p = _dir() / "arbiter.json"
    tmp = p.with_name(".arbiter.json.tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _log(**kw) -> None:
    with open(_dir() / "arbiter.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now_iso(), "resource": "send", **kw}, ensure_ascii=False) + "\n")


def _state_of(rec: dict):
    root = rec.get("root")
    return read_json(Path(root) / "workflow" / "state.json") if root else None


def _live(rec: dict, states) -> str:
    st = _state_of(rec)
    if not st:
        return "unknown"
    s = st.get("state")
    if s in ("AWAITING_USER", "BLOCKED"):
        s = st.get("return_to") or st.get("blocked_from") or s
    return "live" if st.get("turn") == rec.get("turn") and s in states else "stale"


def acquire(inst, max_inflight: int = 1, lease_s: int = 3600) -> dict:
    me = {"project": inst.name, "turn": inst.state.get("turn"), "root": str(inst.root)}
    with _lock():
        s = _load()
        keep = []
        for h in s["holders"]:
            if h.get("project") == me["project"]:
                if h.get("turn") == me["turn"]:
                    keep.append(h)
                else:
                    _log(event="reclaim", project=h.get("project"), turn=h.get("turn"), why="同一项目已进入新的一轮")
                continue
            v = "stale" if (secs_since(h.get("since")) or 0) > lease_s else _live(h, LIVE)
            if v == "stale":
                _log(event="reclaim", project=h.get("project"), turn=h.get("turn"), why="持有方已不在发送或等回复，或租约到期")
                continue
            if v == "unknown" and not h.get("unknown_logged"):
                h["unknown_logged"] = True
                _log(event="unknown", project=h.get("project"), turn=h.get("turn"), why="读不到持有方的状态，等租约到期再回收")
            keep.append(h)
        q = []
        for x in s["queue"]:
            if x.get("project") == me["project"]:
                if x.get("turn") == me["turn"]:
                    q.append(x)
                continue
            if _live(x, ("SEND",)) == "live" and (secs_since(x.get("since")) or 0) <= lease_s:
                q.append(x)
        s["holders"], s["queue"] = keep, q
        if any(h.get("project") == me["project"] for h in keep):
            _save(s)
            return {"granted": True, "position": 0, "holder": me["project"]}
        new = not any(x.get("project") == me["project"] for x in q)
        if new:
            q.append({**me, "since": now_iso()})
        if len(keep) < max_inflight and q[0].get("project") == me["project"]:
            x = q.pop(0)
            keep.append({**me, "since": now_iso()})
            _log(event="grant", project=me["project"], turn=me["turn"], waited_s=int(secs_since(x.get("since")) or 0))
            _save(s)
            return {"granted": True, "position": 0, "holder": me["project"]}
        pos = next(i for i, x in enumerate(q) if x.get("project") == me["project"]) + 1
        holder = "、".join(f"{h.get('project')}#{h.get('turn')}" for h in keep) or "（无）"
        if new:
            _log(event="wait", project=me["project"], turn=me["turn"], holder=holder, position=pos,
                 reason=f"inflight {len(keep)}/{max_inflight}")
        _save(s)
        return {"granted": False, "position": pos, "holder": holder}


def release(inst, why: str) -> None:
    with _lock():
        s = _load()
        keep = [h for h in s["holders"] if h.get("project") != inst.name]
        if len(keep) != len(s["holders"]):
            s["holders"] = keep
            _log(event="release", project=inst.name, turn=inst.state.get("turn"), why=why)
            _save(s)
