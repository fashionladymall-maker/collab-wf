"""1.6.5：外部信号（collab-wf-dev 用户第 53 轮：“各类错误，各类需要人工执行的，需要执行器执行的，都发到 inbox……
任何一个环节卡死，我希望都有信息到 inbox”）。

inbox 由 wf tick 覆盖写，外部进程直接写进去，下一次就被冲掉。信号放在 local/signals/，一件事一个文件，
由写 inbox 的同一处合并（notify.collect_inbox，inbox 里的 key 为 signal|<键>）。所以独立的看门狗与其它程序都可以投递；
处理掉后用 wf signal clear 清除，带有效期的到期自动丢弃。信号只在本机，不经过任何通道。
"""
from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from pathlib import Path

from .core import LOCAL, WfError, now, now_iso, parse_iso, read_json, write_json

SIG_DIR = LOCAL / "signals"
FOR = ("executor", "user")


def _path(key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.@+-]", "_", str(key))[:100] or "_"
    return SIG_DIR / f"{safe}-{hashlib.sha1(str(key).encode('utf-8')).hexdigest()[:8]}.json"


def add(key: str, title: str, why: str = "", for_: str = "executor", project: str = "", action: str = "",
        source: str = "external", ttl_s: int = 0) -> dict:
    """写入或更新一条信号。同一个 key 再次写入时保留第一次出现的时间（since）。"""
    if not str(key or "").strip() or not str(title or "").strip():
        raise WfError("信号需要 --key 与 --title")
    if for_ not in FOR:
        raise WfError(f"--for 只能是 {' 或 '.join(FOR)}")
    p = _path(key)
    old = read_json(p, {}) or {}
    rec = {"key": str(key), "project": project or "", "for": for_, "title": str(title), "why": str(why or ""),
           "action": str(action or ""), "source": source, "since": old.get("since") or now_iso(), "ts": now_iso()}
    if ttl_s and int(ttl_s) > 0:
        rec["expires"] = (now() + timedelta(seconds=int(ttl_s))).isoformat(timespec="seconds")
    SIG_DIR.mkdir(parents=True, exist_ok=True)
    write_json(p, rec)
    return rec


def clear(key: str) -> bool:
    p = _path(key)
    if p.exists():
        p.unlink()
        return True
    return False


def load() -> list:
    """当前全部有效信号；过期的顺手删除。"""
    out = []
    if not SIG_DIR.exists():
        return out
    for p in sorted(SIG_DIR.glob("*.json")):
        rec = read_json(p, None)
        if not isinstance(rec, dict) or not rec.get("key"):
            continue
        exp = parse_iso(rec.get("expires")) if rec.get("expires") else None
        if exp and now() > exp:
            try:
                p.unlink()
            except OSError:
                pass
            continue
        out.append(rec)
    return out
