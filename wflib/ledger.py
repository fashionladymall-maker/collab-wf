"""1.8.7（P0-D，M2-D1）：失败模式台账的检查器。读 PROJECT.md 的“失败模式台账”表（编号 | 日期 | 现象 | 根因 | 处置 | 状态），
按 PROCESS.md 第 6 节的口径核对：标“已解决”的条目必须写明根因、给出机制所在的版本，并有回归证据（自检、探针、回归测试）；
否则只能写“修复已交付”“已缓解”“已规避（约定）”等。检查器只读，不改台账；通过检查不等于业务验收。"""
from __future__ import annotations

import re
from pathlib import Path

KNOWN = ("已解决", "修复已交付", "已缓解", "已规避", "已回归", "待处理", "待开发", "待执行端", "待核对", "open")
VERSION = re.compile(r"\d+\.\d+(?:\.\d+)?")
REGRESSION = re.compile(r"自检|探针|回归|测试|probe|selftest")
PENDING_CAUSE = ("", "待核对", "待查", "未知")
COLS = ("id", "date", "symptom", "cause", "fix", "status")


def parse(text: str) -> list:
    lines = text.split("\n")
    start = next((i for i, l in enumerate(lines) if l.strip().startswith("## 失败模式台账")), None)
    if start is None:
        return []
    rows = []
    for l in lines[start + 1:]:
        if l.startswith("## "):
            break
        s = l.strip()
        if not s.startswith("|") or s.startswith("|---") or s.startswith("| 编号"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 6:
            rows.append({"id": cells[0] if cells else "?", "problem": f"只有 {len(cells)} 列，应为 6 列"})
            continue
        rows.append(dict(zip(COLS, cells[:5] + [" | ".join(cells[5:])])))
    return rows


def check_rows(rows: list) -> dict:
    out, counts = [], {}
    for r in rows:
        if r.get("problem"):
            out.append({"id": r["id"], "why": r["problem"]})
            continue
        st = r["status"]
        key = next((k for k in KNOWN if st.startswith(k)), "")
        counts[key or "其他"] = counts.get(key or "其他", 0) + 1
        if not key:
            out.append({"id": r["id"], "why": f"状态写法不认识：{st[:40]}"})
            continue
        if key == "已解决":
            evidence = r["fix"] + " " + st
            miss = []
            if r["cause"] in PENDING_CAUSE or "待核对" in r["cause"] or "待执行端" in r["cause"]:
                miss.append("根因")
            if not VERSION.search(evidence):
                miss.append("机制所在的版本")
            if not REGRESSION.search(evidence):
                miss.append("回归证据（自检、探针或回归测试）")
            if miss:
                out.append({"id": r["id"], "why": "标了“已解决”，但缺少：" + "、".join(miss)})
    return {"rows": len(rows), "by_status": counts, "violations": out}


def check(inst) -> dict:
    p = Path(inst.root) / "PROJECT.md"
    return check_rows(parse(p.read_text(encoding="utf-8") if p.exists() else ""))
