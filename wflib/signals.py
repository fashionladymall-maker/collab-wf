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


def _dir(project: str = "") -> Path:
    """1.7.3（ISOLATION.md S2）：带项目的信号放在 local/signals/<项目>/，没有项目的仍在 local/signals/。"""
    if not project:
        return SIG_DIR
    return SIG_DIR / (re.sub(r"[^A-Za-z0-9_.-]", "_", str(project))[:80] or "_")


def _path(key: str, project: str = "") -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.@+-]", "_", str(key))[:100] or "_"
    return _dir(project) / f"{safe}-{hashlib.sha1(str(key).encode('utf-8')).hexdigest()[:8]}.json"


def add(key: str, title: str, why: str = "", for_: str = "executor", project: str = "", action: str = "",
        source: str = "external", ttl_s: int = 0) -> dict:
    """写入或更新一条信号。同一个 key 再次写入时保留第一次出现的时间（since）。"""
    if not str(key or "").strip() or not str(title or "").strip():
        raise WfError("信号需要 --key 与 --title")
    if for_ not in FOR:
        raise WfError(f"--for 只能是 {' 或 '.join(FOR)}")
    p = _path(key, project or "")
    old = read_json(p, {}) or {}
    if not old and project:   # 1.7.3：旧位置（平铺）里同一件事的 since 接着用
        old = read_json(_path(key), {}) or {}
    rec = {"key": str(key), "project": project or "", "for": for_, "title": str(title), "why": str(why or ""),
           "action": str(action or ""), "source": source, "since": old.get("since") or now_iso(), "ts": now_iso()}
    if ttl_s and int(ttl_s) > 0:
        rec["expires"] = (now() + timedelta(seconds=int(ttl_s))).isoformat(timespec="seconds")
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json(p, rec)
    if project:   # 1.7.3：写进分目录后，旧位置的同一件事删掉
        legacy = _path(key)
        if legacy.exists() and (read_json(legacy, {}) or {}).get("project") == project:
            try:
                legacy.unlink()
            except OSError:
                pass
    return rec


def clear(key: str, project=None) -> bool:
    """清除一条信号。给了 project 只清这个项目的（以及旧位置里属于它的）；没给就清所有位置里这个 key 的（与 1.7.2 相同）。"""
    gone = False
    paths = [_path(key, project), _path(key)] if project else \
        [_path(key)] + [d / _path(key).name for d in (SIG_DIR.iterdir() if SIG_DIR.exists() else []) if d.is_dir()]
    for p in paths:
        if p.exists():
            rec = read_json(p, {}) or {}
            if project and p.parent == SIG_DIR and rec.get("project") != project:
                continue
            try:
                p.unlink()
                gone = True
            except OSError:
                pass
    return gone


def _migrate() -> None:
    """1.7.3：旧位置（local/signals/ 平铺）里带项目的信号，一次性搬进 local/signals/<项目>/；目标已有就删掉旧的。"""
    if not SIG_DIR.exists():
        return
    for p in sorted(SIG_DIR.glob("*.json")):
        rec = read_json(p, None)
        if not isinstance(rec, dict) or not rec.get("project"):
            continue
        dst = _dir(rec["project"]) / p.name
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                p.unlink()
            else:
                p.replace(dst)
        except OSError:
            continue


def load() -> list:
    """当前全部有效信号；过期的顺手删除。"""
    out = []
    if not SIG_DIR.exists():
        return out
    _migrate()
    files = sorted(SIG_DIR.glob("*.json")) + sorted(SIG_DIR.glob("*/*.json"))   # 1.7.3：平铺与分项目目录
    for p in files:
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
