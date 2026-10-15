"""PROJECT.md 的固定结构：解析与校验（工作流级规则，所有项目通用）。"""
from __future__ import annotations

import re
from pathlib import Path

REQUIRED = ["当前版本", "目标", "已确认结论", "已排除方案及原因", "下一步",
            "待用户决策", "已知限制", "失败模式台账", "协作约定"]
VERSION_RE = re.compile(r"^(v\d+\.\d+)（(\d{4}-\d{2}-\d{2})）—— 阶段：(.+)$")
FAIL_HEADER = ["编号", "日期", "现象", "根因", "处置", "状态"]


def parse_project_md(text: str) -> dict:
    sections, order, cur = {}, [], None
    for line in text.splitlines():
        m = re.match(r"^## (.+?)\s*$", line)
        if m:
            cur = m.group(1)
            order.append(cur)
            sections[cur] = []
        elif cur:
            sections[cur].append(line)
    sections = {k: "\n".join(v).strip() for k, v in sections.items()}
    first = sections.get("当前版本", "").splitlines()[:1]
    vm = VERSION_RE.match(first[0].strip()) if first else None
    checklist = lambda body: [{"done": m.group(1).lower() == "x", "text": m.group(2).strip()}
                              for m in re.finditer(r"^\s*- \[( |x|X)\] (.+)$", body, re.M)]
    failures = []
    rows = [l for l in sections.get("失败模式台账", "").splitlines() if l.strip().startswith("|")]
    if len(rows) >= 2:
        hdr = [c.strip() for c in rows[0].strip("|").split("|")]
        for r in rows[2:]:
            cells = [c.strip() for c in r.strip("|").split("|")]
            failures.append(dict(zip(hdr, cells)))
    return {
        "order": order, "sections": sections,
        "version": vm.group(1) if vm else None, "date": vm.group(2) if vm else None,
        "phase": vm.group(3) if vm else None,
        "next_steps": checklist(sections.get("下一步", "")),
        "decisions": checklist(sections.get("待用户决策", "")),
        "failures": failures,
        "failure_header": [c.strip() for c in rows[0].strip("|").split("|")] if rows else [],
    }


def validate(p: dict, changelog_text=None) -> list:
    issues = []
    missing = [s for s in REQUIRED if s not in p["sections"]]
    if missing:
        issues.append(f"缺少章节：{'、'.join(missing)}")
    present = [s for s in p["order"] if s in REQUIRED]
    if present != [s for s in REQUIRED if s in present]:
        issues.append("章节顺序与规范不一致")
    for s in REQUIRED:
        if s in p["sections"] and not p["sections"][s]:
            issues.append(f"章节为空：{s}（没有内容请写“无”）")
    if not p["version"]:
        issues.append("“当前版本”第一行格式应为：v0.5（2026-10-09）—— 阶段：xxx")
    if "下一步" in p["sections"] and not p["next_steps"]:
        issues.append("“下一步”需要至少一个复选项（- [ ] 或 - [x]）")
    if p["failure_header"] and p["failure_header"] != FAIL_HEADER:
        issues.append(f"失败模式台账表头应为：{' | '.join(FAIL_HEADER)}")
    if changelog_text and p["version"]:
        m = re.search(r"^## (v\d+\.\d+)", changelog_text, re.M)
        if m and m.group(1) != p["version"]:
            issues.append(f"版本不一致：PROJECT.md 为 {p['version']}，CHANGELOG.md 最新为 {m.group(1)}")
    return issues


def validate_dir(root: Path) -> tuple:
    """返回 (解析结果, 问题列表)。"""
    root = Path(root)
    pm, cl = root / "PROJECT.md", root / "CHANGELOG.md"
    if not pm.exists():
        return None, ["缺少 PROJECT.md"]
    p = parse_project_md(pm.read_text(encoding="utf-8"))
    return p, validate(p, cl.read_text(encoding="utf-8") if cl.exists() else None)
