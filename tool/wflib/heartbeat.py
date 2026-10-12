"""1.5.6：驱动器心跳。判据与看板“驱动器”卡片相同：最近一次运行超过 15 分钟、白名单已批准、项目没有暂停，就判为停了。
手写的循环跑满次数会静默退出，状态停在原地，看起来像卡死（collab-wf-dev 第 12–14 轮）；这里让它在 wf doctor 与 wf ask 里也看得见。
唤醒请用系统定时器（docs/PROCESS.md 第 1 节第 3 步）。"""
from __future__ import annotations

import os

from .core import Instance, secs_since

STALE_S = 900


def driver_health(inst: Instance) -> dict:
    """返回 {status: running | ok | stale | never, age_s, last_ts, last_exit, approved, paused, pid}。"""
    from . import policy
    st = inst.state
    d = st.get("driver") or {}
    last = d.get("last") or {}
    inf = d.get("inflight") or {}
    alive = False
    if inf.get("pid"):
        try:
            os.kill(int(inf["pid"]), 0)
            alive = True
        except (ProcessLookupError, PermissionError, ValueError, TypeError):
            alive = False
    age = secs_since(last.get("ts")) if last.get("ts") else None
    approved = bool(policy.status(inst, "policy").get("approved"))
    paused = bool(st.get("paused"))
    if alive:
        status = "running"
    elif age is None:
        status = "never"
    elif age > STALE_S and approved and not paused and inst.cfg.get("auto") is not False:   # 1.6.9（N9）
        status = "stale"
    else:
        status = "ok"
    return {"status": status, "age_s": int(age) if age is not None else None, "last_ts": last.get("ts"),
            "last_exit": last.get("exit"), "approved": approved, "paused": paused,
            "pid": inf.get("pid") if alive else None}
