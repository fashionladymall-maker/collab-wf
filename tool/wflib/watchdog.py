"""1.6.5：卡死与停滞的判定（collab-wf-dev 用户第 53 轮：“任何一个环节卡死，我希望都有信息到 inbox”）。

check() 只读：只看项目状态、驱动器心跳与 policy，不写任何东西。run(write=True) 把结果写成 watchdog:<项目>:<检查> 信号
（local/signals/），恢复后自动清除，并立即重写 inbox：驱动器停了、wf tick 不再运行时，inbox 也能收到。
由独立于驱动器的定时进程调用 wf watchdog --write：驱动器卡死时自己报不出来。阈值见下方与 docs/DRIVER.md 第 23 节。
连续验收未通过与目标长期没有进展已有熔断，照旧以“阻塞”进 inbox；项目锁是 fcntl 锁，进程退出即释放，不会残留，
进程活着却卡住由 driver_hung 覆盖。
"""
from __future__ import annotations

from .core import Instance, read_json, secs_since
from .heartbeat import STALE_S

DRIVER_HUNG_S = 1500     # 驱动器进程还活着、这次运行超过 25 分钟（wf tick --max-seconds 540 的近三倍），且不是在执行命令
EXEC_GRACE_S = 300       # 一条命令超过 command_timeout_s 再加 5 分钟仍未结束
POLL_OVERDUE_S = 600     # 等回复时，到了 poll_after 10 分钟还没有轮询
SEND_UNKNOWN_S = 3600    # 发送结果不明超过 60 分钟
# 同一步停留的上限（秒）。空闲、完成、有到期时间的等待、等用户、阻塞不在其中（后两者本来就在 inbox 里）；执行由 exec_overrun 管
DWELL_S = {"WAIT_REPLY": 2700, "SEND": 3600, "COMPOSE": 1800, "INTAKE": 1800, "REVIEW": 1800, "APPLY": 1800, "VERIFY": 1800}
THRESHOLDS = {"driver_stale_s": STALE_S, "driver_hung_s": DRIVER_HUNG_S, "exec_grace_s": EXEC_GRACE_S,
              "poll_overdue_s": POLL_OVERDUE_S, "send_unknown_s": SEND_UNKNOWN_S, "dwell_s": DWELL_S}


def check(inst: Instance) -> list:
    """一个项目当前的停滞。返回 [{check, key, project, for, title, why}]；没有停滞时为空。"""
    from . import heartbeat, machine
    st = inst.state
    out = []

    def add(name, title, why):
        out.append({"check": name, "key": f"watchdog:{inst.name}:{name}", "project": inst.name, "for": "executor",
                    "title": title, "why": why})

    if st.get("paused"):
        return out   # 暂停本身已在 inbox 里（交给用户）；暂停期间不报停滞
    s = machine.status(inst)
    pol = read_json(inst.wf / "policy.json", {}) or {}
    tmo = int(pol.get("command_timeout_s", 1800) or 1800)
    hb = heartbeat.driver_health(inst)
    if hb["status"] == "stale":
        add("driver_stale", "驱动器停了",
            f"最近一次 wf tick 在 {int(hb['age_s'] or 0) // 60} 分钟前（超过 {STALE_S // 60} 分钟）：系统定时器可能没有加载或在报错。"
            "执行端：wf schedule status，并看 local/tick.log")
    ex = st.get("exec_inflight") or {}
    ex_age = (secs_since(ex["ts"]) or 0) if ex.get("ts") else None
    inf = (st.get("driver") or {}).get("inflight") or {}
    if hb["status"] == "running" and inf.get("started"):
        age = secs_since(inf["started"]) or 0
        in_cmd = s["state"] == "EXECUTE" and ex_age is not None and ex_age < tmo + EXEC_GRACE_S
        if age > DRIVER_HUNG_S and not in_cmd:
            add("driver_hung", "驱动器进程卡住了",
                f"PID {hb.get('pid')} 这一次运行已 {int(age) // 60} 分钟（上限 {DRIVER_HUNG_S // 60} 分钟），也不是在执行命令")
    if s["state"] == "EXECUTE" and ex_age is not None and ex_age > tmo + EXEC_GRACE_S:
        add("exec_overrun", "命令超时仍未结束",
            f"第 {int(ex.get('i') or 0) + 1} 条命令「{str(ex.get('cmd') or '')[:80]}」已运行 {int(ex_age) // 60} 分钟，"
            f"超过 command_timeout_s（{tmo} 秒）再加 {EXEC_GRACE_S // 60} 分钟")
    pa = st.get("poll_after")
    if s["state"] == "WAIT_REPLY" and pa and (secs_since(pa) or 0) > POLL_OVERDUE_S:
        add("poll_overdue", "该轮询却没有轮询",
            f"本该在 {pa} 之后轮询，已过 {int(secs_since(pa) or 0) // 60} 分钟仍没有轮询")
    it = st.get("send_intent") or {}
    if it.get("status") == "pending" and it.get("ts") and (secs_since(it["ts"]) or 0) > SEND_UNKNOWN_S:
        add("send_unknown", "发送结果不明超过 1 小时",
            f"发送意图 {it['ts']} 至今没有确认落地，也没有确认失败。不要重复发送：先核对会话，确认没有送达再用 wf resend")
    lim = DWELL_S.get(s["state"])
    if lim and s["dwell_s"] > lim:
        add("dwell", f"“{s['name']}”停留过久",
            f"第 {s['turn']} 轮停在 {s['state']} 已 {int(s['dwell_s']) // 60} 分钟（上限 {lim // 60} 分钟）")
    return out


def run(insts, write=False) -> list:
    """检查全部项目。write 为真时写成信号、清除已恢复的、并重写 inbox。"""
    findings = []
    for inst in insts:
        try:
            findings += check(inst)
        except Exception as e:   # noqa: BLE001  一个项目读不出来，本身就是要报的事
            name = getattr(inst, "name", "?")
            findings.append({"check": "error", "key": f"watchdog:{name}:error", "project": name, "for": "executor",
                             "title": "看门狗读不出这个项目的状态", "why": f"{type(e).__name__}: {e}"[:300]})
    if write:
        from . import notify, signals
        live = set()
        for f in findings:
            signals.add(f["key"], f["title"], f["why"], for_=f["for"], project=f["project"], source="watchdog")
            live.add(f["key"])
        names = {getattr(i, "name", "") for i in insts}
        for rec in signals.load():
            k = str(rec.get("key") or "")
            parts = k.split(":")
            if k.startswith("watchdog:") and len(parts) >= 3 and parts[1] in names and k not in live:
                signals.clear(k)
        try:
            notify.write_inbox(insts)
        except Exception:   # noqa: BLE001  inbox 写不出来不影响判定结果
            pass
    return findings
