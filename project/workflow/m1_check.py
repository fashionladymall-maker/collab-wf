#!/usr/bin/env python3
"""M1“无人值守稳定运行”的可执行判据（collab-wf-dev v0.9；v1.1 起说明类备注不算人工动作；v1.2 起按历史最长连续计）。只读：只读 workflow/events.jsonl，不写任何文件。

用法（在项目根目录运行，由 goals.json 的 exec 目标调用）：
  python3 workflow/m1_check.py unattended --rounds 10
      历史上出现过连续 N 个完成的轮次（轮次号连续）全部验收通过，期间没有任何人工动作
      （事件的 by 不是 driver 或 wf；执行端的说明类备注 wf note --kind info 只是报告，不算），
      也没有停在“等待用户决定”或“阻塞”。按历史最长连续计（v1.2）：达成之后不会因为之后的人工动作
      （例如开新对话）而失效，目标进度只增不减，驱动器的目标熔断不会被它误触发。
  python3 workflow/m1_check.py auto-update
      驱动器至少成功执行过一次 wf self-update --auto（工具版本由驱动器自己装上）。
输出第一行是结论：“M1 …：通过（…）”或“M1 …：未通过：原因”。通过时退出码 0，否则 1。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

EVENTS = Path("workflow/events.jsonl")
AUTO_BY = {"driver", "wf"}
STOP_STATES = {"AWAITING_USER", "BLOCKED"}
AUTO_SU = re.compile(r"(^|\s)wf\s+self-update\s+--auto\s")


def load(path: Path) -> list:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def _turn(e):
    t = e.get("turn")
    return t if isinstance(t, int) else None


def unattended(events: list, rounds: int) -> tuple:
    results, bad = {}, {}
    for e in events:
        t = _turn(e)
        if t is None:
            continue
        if e.get("type") == "round_result":
            results[t] = bool(e.get("ok"))        # 同一轮多次判定时以最后一次为准
        if e.get("by") not in AUTO_BY and not (e.get("type") == "note" and e.get("kind") == "info"):
            bad.setdefault(t, []).append(f"第 {t} 轮有人工动作：seq {e.get('seq')} {e.get('type')} by={e.get('by')}")
        if e.get("type") == "transition" and e.get("to") in STOP_STATES:
            bad.setdefault(t, []).append(f"第 {t} 轮停在 {e.get('to')}：{e.get('reason') or ''}".rstrip("："))
    best, run, prev, last_break = (0, None, None), 0, None, ""
    for t in sorted(results):
        clean = results[t] and t not in bad
        if not clean:
            run, last_break = 0, "；".join(bad.get(t, [])[:2]) or f"第 {t} 轮验收未通过"
        else:
            run = run + 1 if (run and prev == t - 1) else 1
            if run > best[0]:
                best = (run, t - run + 1, t)
        prev = t
    if not results:
        return False, f"还没有完成的轮次，需要连续 {rounds} 轮"
    span = f"第 {best[1]}–{best[2]} 轮" if best[0] else "无"
    if best[0] >= rounds:
        return True, f"历史最长连续 {best[0]} 轮（{span}）全部通过且无人工动作，需要 {rounds} 轮"
    return False, f"历史最长连续 {best[0]} 轮（{span}），需要 {rounds} 轮" + (f"；最近一次中断：{last_break}" if last_break else "")


def auto_update(events: list) -> tuple:
    hits = [e for e in events if e.get("type") == "exec" and e.get("by") == "driver" and e.get("exit_code") == 0
            and AUTO_SU.search(str(e.get("cmd") or ""))]
    if not hits:
        return False, "驱动器还没有成功执行过 wf self-update --auto"
    e = hits[-1]
    return True, f"第 {_turn(e)} 轮由驱动器执行：{e.get('cmd')}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M1 无人值守稳定运行的判据")
    ap.add_argument("check", choices=["unattended", "auto-update"])
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--events", default=str(EVENTS))
    a = ap.parse_args(argv)
    events = load(Path(a.events))
    if a.check == "unattended":
        ok, why = unattended(events, max(1, a.rounds))
        name = "M1 无人值守"
    else:
        ok, why = auto_update(events)
        name = "M1 自动安装工具版本"
    print(f"{name}：通过（{why}）" if ok else f"{name}：未通过：{why}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
