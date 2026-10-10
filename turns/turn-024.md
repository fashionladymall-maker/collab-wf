【回报 · 第 24 轮 · v0.7】档位：high

① 环境障碍与偏离
- [EXEC_FAIL] 命令执行失败。回报给 Claude。 第 8 条命令「grep -n 护栏 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py」退出码 1
- [EXEC_FAIL] 命令执行失败。回报给 Claude。 第 9 条命令「grep -n 护栏 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py」退出码 1
- 命令 c08 退出码 1：`grep -n 护栏 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py`
- 命令 c09 退出码 1：`grep -n 护栏 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py`

② 关键数字与终端原文

1) build_wf_158.py 全文，放不下的部分续传。 —— 通过
```
$ cat workflow/evidence/turn-020/delivered/build_wf_158.py
#!/usr/bin/env python3
"""collab-wf 1.5.8 构建器（collab-wf-dev 第 20 轮交付）：自动安装工具版本、tick 内等回复、独立审查约定 REVIEW.md。

做什么：在已安装工具的 git 克隆上生成 1.5.8 版本包，不改动已安装的工具。
  1. 克隆 --src（默认 ~/.workbuddy-ai/tools/collab-wf），核对克隆停在 wf-v1.5.7；
  2. 核对已知文件的 sha256（policy.py、release.py 自 1.5.1 未改动，其余由 Claude 逐字节重建计算），任何一个不符就整体中止；
  3. 写入新文件 docs/REVIEW.md，逐处应用锚点改动（每处原文必须恰好出现一次，否则整体中止）；
  4. 写 VERSION，编译全部 .py，用临时的 WF_LOCAL_DIR 跑完整自检（wf selftest），全部通过才打包；
  5. 打包为 <out>/collab-wf-1.5.8.zip，日志写入 <out>/build-wf-1.5.8.log。
本版改动了护栏文件（policy.py、release.py），按它自己引入的规则也需要手动安装：wf self-update <out>/collab-wf-1.5.8.zip。

用法（在项目根目录运行）：
  python3 build_wf_158.py --out workflow/outbox
"""
from __future__ import annotations

import argparse
import hashlib
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

NEW_VERSION = "1.5.8"
BASE_TAG = "wf-v1.5.7"

# 已知文件在 1.5.7 中的 sha256 前 12 位
EXPECT = {
    'VERSION': '2ba0c4acefd3',
    'wflib/notify.py': '2c51155771e1',
    'wflib/policy.py': '02a960418d19',
    'wflib/release.py': 'f56d8cb2ce64',
    'docs/PROCESS.md': 'd7b13591a8e8',
    'wflib/core.py': 'f3b69bba8d4e',
}

# 整文件写入的新文件
NEW_FILES = {
    'docs/REVIEW.md': r'''# REVIEW.md —— 另一个模型系列审查（独立审查适配器的约定，1.5.8）

用户第 19 轮批准开启：由执行端用它自己所用的模型（与 Claude 不同的模型系列），审 Claude 交付的每个版本包。审查只能阻止或提醒，不能放行（DESIGN.md 第 6 节、PROCESS.md 第 5 节）。审查程序由执行端实现，工具只规定接口与处置。

## 1. 何时调用

- 项目版本包：驱动器在落地前调用（DRIVER.md 第 3b 节，已实现）。
- 工具版本：驱动器自动安装工具版本（wf self-update --auto）之前调用；block 时拒绝安装（DRIVER.md 第 18 节）。
- 没有版本包的轮次不调用，所以成本只随版本数增加，不随轮次增加。

## 2. 怎么调用

- 配置：local/transport.json 写 "review": "<审查程序>"（模块方式的适配器按子命令 review 接收）；可选 "review_timeout_s"（默认 900）。
- 参数：项目版本包是 `review --project <名称> --turn <轮次> --release <版本包 zip> --diff <diff 文件>`；工具版本多一个 `--kind tool`（--project 为 collab-wf，--turn 为 0）。环境变量 WF_ADAPTER_TIMEOUT_S 给出时间上限。
- 输入：diff 文件是版本包相对当前仓库的统一 diff（最多 40 万字符），审查以它为主；需要上下文时可以解开 zip 看完整文件。

## 3. 审什么

审查者逐条回答：

1. 红线：有没有新增资金、密钥、合规、实盘相关的操作，例如密钥赋值、实盘开关、提现转账调用。
2. 护栏：有没有削弱白名单判定、停止底线、批准与决定码、自升级核验、异常处置，或者让驱动器能执行原本只能由用户执行的命令。
3. 夹带：改动与 Claude 在回复里说的改动是否一致，有没有说明之外的改动。
4. 破坏：有没有删除数据、改写 git 历史、向外部网络发送数据之类的操作。
5. 质量：明显的缺陷、遗漏的测试、与文档不一致（只提醒）。

1–4 命中任何一条，结论为 block；只有 5，结论为 concerns；都没有，结论为 pass。

## 4. 输出

标准输出最后一行是 JSON：`{"verdict": "pass" | "concerns" | "block", "notes": ["……"], "model": "<模型名>"}`，退出码 0。notes 每条一句话，写明文件与位置。

## 5. 处置（只能阻止，不能放行）

- block：驱动器停在“等待用户决定”，看板与 wf ask 显示审查意见；用户可以批准继续（wf approve，带决定码与原话），或者让 Claude 重做。
- concerns：写进“需要注意”与决策日志，照常继续；Claude 在下一轮回报里看到。
- pass：照常继续。
- 审查不可用（超时、输出不是上面的格式）：决策日志记“未审查”，按规则继续，不因审查程序的故障卡住流程。
- 结论保存在 workflow/evidence/turn-NNN/review_adapter.json，看板“执行端 / 适配器”卡片显示“独立审查：已配置”。
''',
}

# 整文件替换（本版没有）
REPLACE_FILES = {}

# 可选改动应用后写进 CHANGELOG 的说明（本版没有）
OPTIONAL_NOTES = {}

# (文件, 名称, 原文, 新文, 是否可选)：按顺序应用；原文必须在当前文本中恰好出现一次
EDITS = [
    ('wflib/driver.py', 'driver-run-linger-sig',
r'''def run(inst: Instance, max_seconds=600, max_steps=40) -> tuple:
''',
r'''def run(inst: Instance, max_seconds=600, max_steps=40, linger=False) -> tuple:
''', False),
    ('wflib/driver.py', 'driver-run-linger-call',
r'''        return _run_locked(inst, max_seconds, max_steps)
''',
r'''        return _run_locked(inst, max_seconds, max_steps, linger)
''', False),
    ('wflib/driver.py', 'driver-linger-helper',
r'''def _run_locked(inst, max_seconds, max_steps):
''',
r'''def _linger_due(st: dict, ctx):
    """1.5.7：wf tick 内等回复。只在“发送”与“等待 Claude”两个状态、轮询间隔不小于 30 秒时：下一次轮询的时刻在本次
    时间预算内，就睡到那时再查，不交还给下一次唤醒（此前发现回复要等 300 秒的唤醒间隔，约七成轮次时间是空等）。"""
    if st.get("state") not in ("WAIT_REPLY", "SEND") or int(ctx.pol.get("poll_interval_s", 120)) < 30:
        return None
    due = [t for t in (parse_iso(st.get("poll_after")), parse_iso(st.get("retry_at"))) if t]
    if not due:
        return None
    wait = max(1.0, (min(due) - now()).total_seconds())
    return wait if wait + 15 <= ctx.time_left() else None


def _run_locked(inst, max_seconds, max_steps, linger=False):
''', False),
    ('wflib/driver.py', 'driver-linger-loop',
r'''                    if kind != "progress" or steps >= max_steps or ctx.time_left() <= 0:
                        break
''',
r'''                    if kind != "progress" or steps >= max_steps or ctx.time_left() <= 0:
                        due = _linger_due(inst.state, ctx) if (linger and kind == "wait" and steps < max_steps) else None
                        if due is not None:   # 1.5.7：在本次唤醒内等到下一次轮询
                            time.sleep(due)
                            continue
                        break
''', False),
    ('wflib/driver.py', 'driver-tick-linger',
r'''        code, info = run(inst, max_seconds=min(left, max_seconds))
''',
r'''        code, info = run(inst, max_seconds=min(left, max_seconds), linger=True)
''', False),
    ('wflib/driver.py', 'driver-exec-check-not-exit',
r'''        if not _exec_ok(inst, d, f"#{i}", rc):   # 1.5.2（W21）：有判据的命令以判据为准
            _anomaly(ctx, {"source": "exec", "code": rc}, f"第 {i} 条命令「{cmds[i - 1]}」退出码 {rc}")
''',
r'''        if not _exec_ok(inst, d, f"#{i}", rc):   # 1.5.2（W21）：有判据的命令以判据为准
            if rc not in (0, None):   # 1.5.7：退出码为 0、只是没通过判据时由验收报 CHECK_FAIL，不再报“命令执行失败，退出码 0”
                _anomaly(ctx, {"source": "exec", "code": rc}, f"第 {i} 条命令「{cmds[i - 1]}」退出码 {rc}")
''', False),
    ('wflib/policy.py', 'policy-auto-su-const',
r'''DEFAULT_PIPE_FILTERS = ["head", "tail", "grep", "wc", "sort", "uniq", "cut"]
''',
r'''DEFAULT_PIPE_FILTERS = ["head", "tail", "grep", "wc", "sort", "uniq", "cut"]
# 1.5.8：用户批准的“驱动器自动安装工具版本”只放行这一种形式（policy.json 的 auto_self_update 为 true，且白名单放行）；
# 改动护栏文件的版本由 release.self_update(auto=True) 拒绝，另一个模型系列审查阻止时也拒绝
AUTO_SELF_UPDATE = re.compile(r"^\s*wf\s+self-update\s+--auto\s+workflow/outbox/collab-wf-\d+\.\d+\.\d+\.zip\s*$")
''', False),
    ('wflib/policy.py', 'policy-auto-su-classify',
r'''    if re.match(r"^\s*wf(\s|$)", first) and not WF_READONLY.match(first) and not re.match(r"^\s*wf\s+self-check\s*$", first):
''',
r'''    auto_su = (pol or {}).get("auto_self_update") is True and bool(AUTO_SELF_UPDATE.match(first))
    if auto_su:   # 1.5.8：只此一种 wf 写操作可由驱动器执行；仍须在白名单内（下面照常检查）
        cats.append("自动安装工具版本")
    elif re.match(r"^\s*wf(\s|$)", first) and not WF_READONLY.match(first) and not re.match(r"^\s*wf\s+self-check\s*$", first):
''', False),
    ('wflib/release.py', 'release-guard-helpers',
r'''def self_update(zip_path, skip_test=False, allow_same=False) -> dict:
''',
r'''# 1.5.8：驱动器自动安装工具版本时，改动了这些文件的版本必须由用户确认后手动安装（护栏：白名单判定、批准与决定码、
# 安装与回滚核验、异常处置）。其余改动交给另一个模型系列审查（docs/REVIEW.md）。判断由当前已安装的代码在安装前做。
GUARD_FILES = ("wflib/policy.py", "wflib/release.py", "wflib/asks.py", "anomalies.json")


def guard_changes(new_files: dict, cur_files: dict) -> list:
    """新版本相对当前安装改动了哪些护栏文件（新增、删除或内容不同）。"""
    return [f for f in GUARD_FILES if new_files.get(f) != cur_files.get(f)]


def _review_tool(src: Path, zip_path) -> tuple:
    """1.5.8：自动安装前请另一个模型系列审查工具版本的 diff。返回 (结论, 说明)；没有配置审查时返回 ("none", "")。"""
    import difflib
    from . import anomaly
    from .core import LOCAL
    if not anomaly.transport_cfg().get("review"):
        return "none", ""
    parts = []
    for f in sorted(Path(src).rglob("*")):
        rel = str(f.relative_to(src)).replace(os.sep, "/")
        if not f.is_file() or "__pycache__" in f.parts or rel in ("MANIFEST.json", "RELEASE.json"):
            continue
        old_p = WF_HOME / rel
        try:
            new = f.read_text(encoding="utf-8").splitlines()
            old = old_p.read_text(encoding="utf-8").splitlines() if old_p.exists() else []
        except UnicodeDecodeError:
            continue
        parts += list(difflib.unified_diff(old, new, f"a/{rel}", f"b/{rel}", lineterm=""))
    rd = LOCAL / "reviews"
    rd.mkdir(parents=True, exist_ok=True)
    diff = rd / f"{Path(zip_path).stem}.diff"
    diff.write_text("\n".join(parts)[:400000], encoding="utf-8")
    r = anomaly.call("review", ["--kind", "tool", "--project", "collab-wf", "--turn", "0", "--release", zip_path, "--diff", diff],
                     timeout=anomaly.transport_cfg().get("review_timeout_s", 900))
    data = r.get("data") or {}
    if r.get("code") != 0 or data.get("verdict") not in ("pass", "concerns", "block"):
        return "unavailable", (r.get("raw") or "")[:200]
    write_json(rd / f"{Path(zip_path).stem}.json", data)
    return data["verdict"], "；".join(data.get("notes", [])[:5])


def self_update(zip_path, skip_test=False, allow_same=False, auto=False) -> dict:
''', False),
    ('wflib/release.py', 'release-auto-checks',
r'''        if not skip_test:
            env = dict(os.environ)
''',
r'''        if auto:   # 1.5.8：驱动器自动安装（policy.json 的 auto_self_update 与白名单放行 wf self-update --auto）
            if skip_test:
                raise WfError("自动安装不允许跳过自检")
            guard = guard_changes(src_files, _tool_files())
            if guard:
                raise WfError("这个版本改动了护栏文件（" + "、".join(guard) + "），自动安装已拒绝："
                              "请用户确认后，由执行端手动运行 wf self-update")
            verdict, notes = _review_tool(src, zip_path)
            if verdict == "block":
                raise WfError(f"另一个模型系列审查认为应阻止，自动安装已拒绝：{notes}")
        if not skip_test:
            env = dict(os.environ)
''', False),
    ('wf.py', 'wf-help-self-update-auto',
r'''  wf version | wf self-init | wf self-update <zip> | wf self-rollback <版本> | wf selftest
''',
r'''  wf version | wf self-init | wf self-update [--auto] <zip> | wf self-rollback <版本> | wf selftest
''', False),
    ('wf.py', 'wf-self-update-auto-arg',
r'''    x = sub.add_parser("self-update"); x.add_argument("zip"); x.add_argument("--skip-test", action="store_true")
''',
r'''    x = sub.add_parser("self-update"); x.add_argument("zip"); x.add_argument("--skip-test", action="store_true")
    x.add_argument("--auto", action="store_true")   # 1.5.8：驱动器自动安装；改动护栏文件的版本会被拒绝（DRIVER.md 第 18 节）
''', False),
    ('wf.py', 'wf-self-update-auto-call',
r'''        r = release.self_update(Path(args.zip), args.skip_test, args.allow_same)
''',
r'''        r = release.self_update(Path(args.zip), args.skip_test, args.allow_same, auto=bool(getattr(args, "auto", False)))
''', False),
    ('docs/PROCESS.md', 'process-auto-install',
r'''- 通知：1.5.7 起 wf tick 跑完后，把新出现的待决事项与提示弹成系统通知，每件事只通知一次；wf notify test 试发一条，wf notify status 查看是否开启。''',
r'''- 通知：1.5.7 起 wf tick 跑完后，把新出现的待决事项与提示弹成系统通知，每件事只通知一次；wf notify test 试发一条，wf notify status 查看是否开启。
- 自动安装工具版本（1.5.8，用户批准后开启）：Claude 交付构建器，驱动器在 EXECUTE 里自己构建（python3 …/build_wf_*.py --out workflow/outbox）、自己安装（wf self-update --auto workflow/outbox/collab-wf-X.Y.Z.zip）、自己核对（wf version、wf self-check）。改动护栏文件的版本、或者另一个模型系列审查认为应阻止的版本，安装会被拒绝，停下等用户。''', False),
    ('docs/DRIVER.md', 'driver-doc-section18',
r'''- 关闭：环境变量 WF_NOTIFY=0，或 local/notify.json 写 {"enabled": false}。wf notify test 试发一条，wf notify status 查看状态。通知出任何问题都不影响 wf tick。''',
r'''- 关闭：环境变量 WF_NOTIFY=0，或 local/notify.json 写 {"enabled": false}。wf notify test 试发一条，wf notify status 查看状态。通知出任何问题都不影响 wf tick。

## 18. 1.5.8 自动安装工具版本、tick 内等回复

- 自动安装：policy.json 的 auto_self_update 为 true、并且白名单放行时，驱动器可以执行 wf self-update --auto workflow/outbox/collab-wf-X.Y.Z.zip——这是唯一一种可由驱动器执行的 wf 写操作。第 3 节的“护栏命令只能由用户执行”对其余命令不变。
- 安装前由当前已安装的代码检查：改动了护栏文件（policy.py、release.py、asks.py、anomalies.json）的版本拒绝自动安装，停下等用户确认后手动安装；配置了另一个模型系列审查时先审 diff，结论为 block 也拒绝；--auto 不允许跳过自检。安装之后的核验与失败自动回滚照旧（第 11 节）。
- tick 内等回复：wf tick 处在“发送”与“等待 Claude”时，下一次轮询的时刻若在本次唤醒的时间预算内，就在 tick 里等到那时再查，不交还给下一次唤醒；轮询间隔小于 30 秒（测试）或单独运行 wf run 时不这样做。
- 命令退出码为 0、只是没通过判据时，由验收报 CHECK_FAIL，不再同时报“命令执行失败”。
- 另一个模型系列审查的约定见 docs/REVIEW.md。''', False),
    ('wflib/selftest_v15.py', 'selftest-v158-funcs',
r'''

TESTS = [''',
r'''

def _t_v158_linger(_ok):
    from datetime import timedelta
    from . import driver
    from .core import now

    class C:
        def __init__(self, pol, left):
            self.pol, self._left = pol, left

        def time_left(self):
            return self._left
    soon = (now() + timedelta(seconds=60)).isoformat(timespec="seconds")
    w = driver._linger_due({"state": "WAIT_REPLY", "poll_after": soon}, C({"poll_interval_s": 120}, 500))
    _ok("1.5.8 等回复：下一次轮询在本次唤醒的时间内，就在 tick 里等到那时（此前要等 300 秒的唤醒间隔）",
        w is not None and 50 <= w <= 61
        and driver._linger_due({"state": "WAIT_REPLY", "poll_after": soon}, C({"poll_interval_s": 120}, 30)) is None
        and driver._linger_due({"state": "IDLE", "poll_after": soon}, C({"poll_interval_s": 120}, 500)) is None
        and driver._linger_due({"state": "WAIT_REPLY", "poll_after": soon}, C({"poll_interval_s": 0}, 500)) is None, str(w))
    from .core import WF_HOME
    r = (WF_HOME / "docs" / "REVIEW.md").read_text(encoding="utf-8") if (WF_HOME / "docs" / "REVIEW.md").exists() else ""
    _ok("1.5.8 文档：REVIEW.md 写明独立审查的调用、审查清单、输出与只能阻止的处置",
        "## 3. 审什么" in r and '"verdict"' in r and "## 5. 处置" in r and "--kind tool" in r)


def _t_v158_auto_update(_ok):
    from . import policy, release
    allow = r"^wf self-update --auto workflow/outbox/collab-wf-[0-9]+\.[0-9]+\.[0-9]+\.zip$"
    pol = {"allow": [allow], "auto_self_update": True}
    c = "wf self-update --auto workflow/outbox/collab-wf-1.5.9.zip"
    ok1 = policy.classify(c, pol)
    no_flag = policy.classify(c, {"allow": [allow]})
    other = policy.classify("wf self-update --auto /tmp/evil.zip", pol)
    plain = policy.classify("wf self-update workflow/outbox/collab-wf-1.5.9.zip", pol)
    _ok("1.5.8 自动安装工具版本：只有 policy 开启 auto_self_update 且白名单放行时，wf self-update --auto <outbox 里的版本包> 才自动执行",
        ok1["auto"] and not ok1["manual"] and not no_flag["auto"] and not other["auto"] and not plain["auto"],
        f"{ok1['reasons']} | {no_flag['reasons']} | {other['reasons']} | {plain['reasons']}")
    cur = {"wflib/policy.py": "a", "wflib/driver.py": "b", "wflib/notify.py": "c"}
    _ok("1.5.8 自动安装工具版本：改动护栏文件（白名单判定、批准、安装核验、异常处置）的版本要用户确认",
        release.guard_changes({**cur, "wflib/driver.py": "B", "wflib/new.py": "n"}, cur) == []
        and release.guard_changes({**cur, "wflib/policy.py": "A"}, cur) == ["wflib/policy.py"]
        and release.guard_changes({**cur, "anomalies.json": "x"}, cur) == ["anomalies.json"])


TESTS = [''', False),
    ('wflib/selftest_v15.py', 'selftest-v158-register',
r'''         ("1.5.6 驱动器心跳", _t_v156_heartbeat), ("1.5.7 系统通知", _t_v157_notify)]''',
r'''         ("1.5.6 驱动器心跳", _t_v156_heartbeat), ("1.5.7 系统通知", _t_v157_notify),
         ("1.5.8 等回复与审查文档", _t_v158_linger), ("1.5.8 自动安装工具版本", _t_v158_auto_update)]''', False),
    ('CHANGELOG.md', 'changelog',
r'''# CHANGELOG（collab-wf）

## 1.5.7 — 2026-10-10''',
r'''# CHANGELOG（collab-wf）

## 1.5.8 — 2026-10-10

全自动、不需要用户提醒（collab-wf-dev 第 19–20 轮：交付物要执行端手动安装，而执行端只在用户发话时被唤醒；状态机一秒跑完一轮，约七成时间在等唤醒）。
- 自动安装工具版本（用户第 19 轮批准）：policy.json 的 auto_self_update 为 true、白名单放行时，驱动器可以执行 wf self-update --auto workflow/outbox/collab-wf-X.Y.Z.zip。安装前由当前已安装的代码检查：改动了护栏文件（policy.py、release.py、asks.py、anomalies.json）的版本拒绝，停下等用户；配置了另一个模型系列审查时先审 diff，block 也拒绝；--auto 不允许跳过自检。
- 效率：wf tick 在“发送”与“等待 Claude”时，下一次轮询若在本次时间预算内，就在 tick 里等到那时再查；发现回复的延迟从约 300 秒降到轮询间隔（120 秒）以内。空闲时不轮询。
- 命令退出码为 0、只是没通过判据时不再报“命令执行失败，退出码 0”，由验收报 CHECK_FAIL。
- 新增 docs/REVIEW.md：另一个模型系列审查的约定（何时调用、参数、审查清单、输出、只能阻止的处置）。
- 文档：DRIVER.md 新增第 18 节；PROCESS.md 第 8 节写明自动安装。
- 第 19 轮交付的同名 1.5.7 构建器（含上述效率与审查文档）因 1.5.7 已安装而未能构建，内容并入本版。
- 自检新增 4 项；原有检查没有删改。

## 1.5.7 — 2026-10-10''', False),
]


class Log:
    def __init__(self, path: Path):
        self.path = path
        self.f = open(path, "w", encoding="utf-8")

    def __call__(self, *a):
        s = " ".join(str(x) for x in a)
        print(s, flush=True)
        self.f.write(s + "\n")
        self.f.flush()


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def git(args, cwd):
    r = subprocess.run(["git"] + list(args), cwd=str(cwd), capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{(r.stderr or r.stdout).strip()}")
    return r.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description="collab-wf 1.5.8 构建器")
    ap.add_argument("--src", default=str(Path.home() / ".workbuddy-ai" / "tools" / "collab-wf"), help="已安装工具的目录")
    ap.add_argument("--out", default=".", help="版本包与日志的输出目录")
    ap.add_argument("--keep", action="store_true", help="保留临时克隆，便于排查")
    a = ap.parse_args()
    out_dir = Path(a.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    log = Log(out_dir / f"build-wf-{NEW_VERSION}.log")
    work = Path(tempfile.mkdtemp(prefix="wf158-build-"))
    clone = work / "collab-wf"

    def abort(msg: str) -> int:
        log(f"构建中止：{msg}")
        log(f"临时目录保留在 {work}（可删除）")
        return 1

    log(f"collab-wf {NEW_VERSION} 构建器；Python {sys.version.split()[0]}（{sys.executable}）")
    src = Path(a.src).expanduser().resolve()
    log(f"[1/6] 克隆 {src}")
    git(["clone", "-q", str(src), str(clone)], work)
    head = git(["rev-parse", "HEAD"], clone).strip()
    base = git(["rev-parse", f"{BASE_TAG}^{{commit}}"], clone).strip()
    if head != base:
        return abort(f"克隆的 HEAD {head[:7]} 不是 {BASE_TAG}（{base[:7]}）。工具版本已变化，需要 Claude 重新核对锚点。")
    log(f"      HEAD {head[:7]} = {BASE_TAG}")

    log("[2/6] 核对要改的文件")
    for rel, pre in EXPECT.items():
        got = sha256(clone / rel)[:12]
        if got != pre:
            return abort(f"{rel} 的 sha256 前 12 位是 {got}，期望 {pre}")
        log(f"      ✓ {rel}  {got}")

    log(f"[3/6] 应用 {len(EDITS)} 处锚点改动，写入 {len(NEW_FILES)} 个新文件")
    texts, notes = {}, []
    for i, (rel, name, old, new, optional) in enumerate(EDITS, 1):
        t = texts.get(rel)
        if t is None:
            t = (clone / rel).read_text(encoding="utf-8")
        n = t.count(old)
        if n != 1:
            if optional:
                log(f"      - {i:2d}. {rel}：{name}（可选，原文命中 {n} 次，跳过）")
                continue
            return abort(f"第 {i} 处（{rel}，{name}）原文命中 {n} 次，应为 1 次")
        texts[rel] = t.replace(old, new, 1)
        if optional and name in OPTIONAL_NOTES:
            notes.append(OPTIONAL_NOTES[name])
        log(f"      ✓ {i:2d}. {rel}：{name}")
    if "CHANGELOG.md" in texts:
        texts["CHANGELOG.md"] = texts["CHANGELOG.md"].replace("{{OPTIONAL_NOTES}}", "".join(notes))
    for rel, t in texts.items():
        (clone / rel).write_text(t, encoding="utf-8")
    for rel, t in NEW_FILES.items():
        p = clone / rel
        if p.exists():
            return abort(f"新文件 {rel} 已经存在")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(t, encoding="utf-8")
        log(f"      + {rel}（新文件，{len(t.encode('utf-8'))} 字节）")
    for rel, t in REPLACE_FILES.items():
        (clone / rel).write_text(t, encoding="utf-8")
        log(f"      = {rel}（整文件替换，{len(t.encode('utf-8'))} 字节）")
    if NEW_FILES:
        git(["add", "--"] + list(NEW_FILES), clone)
    (clone / "VERSION").write_text(NEW_VERSION + "\n", encoding="utf-8")
    log(f"      VERSION → {NEW_VERSION}")
    log(git(["diff", "--stat", "HEAD"], clone).rstrip())

    log("[4/6] 编译")
    tracked = [x for x in git(["ls-files", "-z"], clone).split("\0") if x]
    for rel in tracked:
        if rel.endswith(".py"):
            try:
                py_compile.compile(str(clone / rel), doraise=True)
            except py_compile.PyCompileError as e:
                return abort(f"编译失败：{rel}\n{e}")
    log(f"      ✓ {sum(1 for r in tracked if r.endswith('.py'))} 个 .py 文件")

    log("[5/6] 完整自检（wf selftest，隔离的 WF_LOCAL_DIR）")
    env = dict(os.environ)
    env["WF_LOCAL_DIR"] = str(work / "local")
    env.pop("VIRTUAL_ENV", None)
    r = subprocess.run([sys.executable, "wf.py", "selftest"], cwd=str(clone), env=env, capture_output=True, text=True,
                       timeout=1200)
    full = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
    log.f.write("----- 自检完整输出 -----\n" + full + "\n----- 自检输出结束 -----\n")
    tail = "\n".join(full.rstrip().splitlines()[-40:])
    print(tail, flush=True)
    fails = [l for l in full.splitlines() if l.lstrip().startswith("✗")]
    if fails:
        log("      未通过的检查：")
        for l in fails[:40]:
            log("      " + l.strip())
    if r.returncode != 0:
        return abort(f"自检未通过（退出码 {r.returncode}），未打包。完整输出见日志。")
    log(f"      ✓ 自检通过（退出码 0）")

    log("[6/6] 打包")
    zpath = out_dir / f"collab-wf-{NEW_VERSION}.zip"
    top = f"collab-wf-{NEW_VERSION}"
    names = [rel for rel in tracked if rel != "MANIFEST.json"]
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in names:
            zi = zipfile.ZipInfo.from_file(str(clone / rel), arcname=f"{top}/{rel}")
            zi.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(zi, (clone / rel).read_bytes())
    with zipfile.ZipFile(zpath) as zf:
        bad = zf.testzip()
        count = len(zf.namelist())
    if bad:
        return abort(f"版本包自检失败：{bad}")
    log(f"      {zpath}（{count} 个文件，{zpath.stat().st_size} 字节，sha256 {sha256(zpath)[:12]}）")
    log("构建通过。安装：wf self-update " + str(zpath))
    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
exit=0  耗时 0.0s
```

2) selftest_v15.py 第 600–680 行（1.5.8 测试与注册表）。 —— 通过
```
$ sed -n 600,680p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_v15.py
        inst.save()
        got = []
        snd = lambda t, m, s: got.append((t, m, s)) or True
        store = Path(td) / "notified.json"
        r1 = notify.after_tick([inst], sender=snd, store=store)
        r2 = notify.after_tick([inst], sender=snd, store=store)
        _ok("1.5.7 系统通知：新出现的待决事项通知一次，之后不重复（第 17 轮把执行端的通知胶水收进工具）",
            len(r1) >= 1 and any("白名单" in x["subtitle"] + x["message"] for x in r1) and r2 == [], f"{r1} {r2}")
        pol.unlink()
        notify.after_tick([inst], sender=snd, store=store)
        keys = json.loads(store.read_text(encoding="utf-8"))["keys"]
        pol.write_text(json.dumps({"allow": []}), encoding="utf-8")
        rf = notify.after_tick([inst], sender=lambda *a: False, store=store)
        keys2 = json.loads(store.read_text(encoding="utf-8"))["keys"]
        _ok("1.5.7 系统通知：事项处理掉后清除记录，发送失败不记为已通知（下次重试）",
            keys == {} and rf == [] and keys2 == {}, f"{keys} {rf} {keys2}")
    _ok("1.5.7 系统通知：AppleScript 字符串转义引号、反斜杠与换行", notify._osa('a"b\\c\nd') == '"a\\"b\\\\c d"',
        notify._osa('a"b\\c\nd'))


def _t_v158_linger(_ok):
    from datetime import timedelta
    from . import driver
    from .core import now

    class C:
        def __init__(self, pol, left):
            self.pol, self._left = pol, left

        def time_left(self):
            return self._left
    soon = (now() + timedelta(seconds=60)).isoformat(timespec="seconds")
    w = driver._linger_due({"state": "WAIT_REPLY", "poll_after": soon}, C({"poll_interval_s": 120}, 500))
    _ok("1.5.8 等回复：下一次轮询在本次唤醒的时间内，就在 tick 里等到那时（此前要等 300 秒的唤醒间隔）",
        w is not None and 50 <= w <= 61
        and driver._linger_due({"state": "WAIT_REPLY", "poll_after": soon}, C({"poll_interval_s": 120}, 30)) is None
        and driver._linger_due({"state": "IDLE", "poll_after": soon}, C({"poll_interval_s": 120}, 500)) is None
        and driver._linger_due({"state": "WAIT_REPLY", "poll_after": soon}, C({"poll_interval_s": 0}, 500)) is None, str(w))
    from .core import WF_HOME
    r = (WF_HOME / "docs" / "REVIEW.md").read_text(encoding="utf-8") if (WF_HOME / "docs" / "REVIEW.md").exists() else ""
    _ok("1.5.8 文档：REVIEW.md 写明独立审查的调用、审查清单、输出与只能阻止的处置",
        "## 3. 审什么" in r and '"verdict"' in r and "## 5. 处置" in r and "--kind tool" in r)


def _t_v158_auto_update(_ok):
    from . import policy, release
    allow = r"^wf self-update --auto workflow/outbox/collab-wf-[0-9]+\.[0-9]+\.[0-9]+\.zip$"
    pol = {"allow": [allow], "auto_self_update": True}
    c = "wf self-update --auto workflow/outbox/collab-wf-1.5.9.zip"
    ok1 = policy.classify(c, pol)
    no_flag = policy.classify(c, {"allow": [allow]})
    other = policy.classify("wf self-update --auto /tmp/evil.zip", pol)
    plain = policy.classify("wf self-update workflow/outbox/collab-wf-1.5.9.zip", pol)
    _ok("1.5.8 自动安装工具版本：只有 policy 开启 auto_self_update 且白名单放行时，wf self-update --auto <outbox 里的版本包> 才自动执行",
        ok1["auto"] and not ok1["manual"] and not no_flag["auto"] and not other["auto"] and not plain["auto"],
        f"{ok1['reasons']} | {no_flag['reasons']} | {other['reasons']} | {plain['reasons']}")
    cur = {"wflib/policy.py": "a", "wflib/driver.py": "b", "wflib/notify.py": "c"}
    _ok("1.5.8 自动安装工具版本：改动护栏文件（白名单判定、批准、安装核验、异常处置）的版本要用户确认",
        release.guard_changes({**cur, "wflib/driver.py": "B", "wflib/new.py": "n"}, cur) == []
        and release.guard_changes({**cur, "wflib/policy.py": "A"}, cur) == ["wflib/policy.py"]
        and release.guard_changes({**cur, "anomalies.json": "x"}, cur) == ["anomalies.json"])


TESTS = [("解析容错", _t_blocks), ("驱动指令", _t_directives), ("清单编号", _t_lists), ("证据关联", _t_link),
         ("回报原文", _t_report), ("分片", _t_chunks), ("回复归属", _t_attr), ("执行接续", _t_exec),
         ("桥接自检", _t_bridge), ("额度与事件", _t_usage_seq), ("升级核验", _t_receipt), ("文件口径", _t_tree),
         ("1.5.1 关联与处置", _t_v151), ("1.5.1 超时宽限", _t_grace), ("1.5.1 只读命令", _t_readonly),
         ("1.5.1 备注提示", _t_note), ("1.5.1 确认重发", _t_resend), ("1.5.1 批准与说明", _t_resume_info),
         ("1.5.2 并发写状态", _t_v152_state), ("1.5.2 事件序号", _t_v152_seq), ("1.5.2 前置标记", _t_v152_blocking),
         ("1.5.2 续传优先", _t_v152_carry), ("1.5.2 判据与熔断", _t_v152_exec), ("1.5.2 桥接动作", _t_v152_bridge),
         ("1.5.3 页面快照", _t_v153_page), ("1.5.3 对话记录", _t_v153_talk), ("1.5.3 看板板块", _t_v153_board),
         ("1.5.4 页面截图", _t_v154_shot), ("1.5.5 流程与上下文文档", _t_v155_docs),
         ("1.5.6 驱动器心跳", _t_v156_heartbeat), ("1.5.7 系统通知", _t_v157_notify),
         ("1.5.8 等回复与审查文档", _t_v158_linger), ("1.5.8 自动安装工具版本", _t_v158_auto_update)]


def run_v15(_ok) -> None:
    print("  —— 1.5.x 新增检查 ——", flush=True)
    for name, fn in TESTS:
        try:
            fn(_ok)
exit=0  耗时 0.0s
```

3) selftest_v15.py 第 1–40 行（导入与辅助函数）。 —— 通过
```
$ sed -n 1,40p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_v15.py
"""1.5.0 新增自检：解析容错、证据关联与续传、回复归属、执行接续、桥接日志、额度与事件序号、升级核验。
都是纯函数或临时目录内的文件操作，不启动子进程，几秒内完成。由 selftest.run() 开头调用，结果计入总数。
其中几条用真实发生过的情形做回归样例（见各条名称）。"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from . import fidelity, ops

FENCE = "`" * 3

REPLY_MENTION = (
    "【本轮结论】\n正文里顺带提到【本机执行】，不应被当成块标题。\n\n"
    "【本机执行】\n" + FENCE + "bash\necho a\necho b\n" + FENCE + "\n\n"
    "【本轮回报清单】\n1. c1 的原文\n2. c2 的原文\n\n"
    "【需要用户决定】\n无\n\n"
    "【下一轮建议档位】\nhigh\n\n"
    "【驱动指令】\n发布：无\n之后：回报\n说明：命令只写在【本机执行】里。\n"
)

REPLY_DUP_LINE = REPLY_MENTION.replace("说明：命令只写在【本机执行】里。", "【本机执行】这一行在行首重复出现，应被忽略。")

REPLY_INLINE = "【本轮结论】结论【本机执行】无【本轮回报清单】无【需要用户决定】无【下一轮建议档位】low"


def _t_blocks(_ok):
    r = ops.parse_reply(REPLY_MENTION)
    _ok("解析：正文中出现的块名不顶掉真正的命令块（1.4.x 会丢失全部命令）",
        r["commands"] == ["echo a", "echo b"] and not r["blocks_missing"], str(r["commands"]))
    _ok("解析：块名出现在驱动指令之后时，驱动指令仍完整", (r["directives"] or {}).get("after") == "report",
        str(r["directives"]))
    r2 = ops.parse_reply(REPLY_DUP_LINE)
    _ok("解析：行首重复的块标题被忽略并给出提示",
        r2["commands"] == ["echo a", "echo b"] and any("另有出现" in w for w in r2.get("warnings", [])),
        str(r2.get("warnings")))
    r3 = ops.parse_reply(REPLY_INLINE)
exit=0  耗时 0.0s
```

4) driver.py 第 605–660 行（评审时如何记录停下的原因）。 —— 通过
```
$ sed -n 605,660p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
    return (int(m.group(1)), int(m.group(2))) if m else None


def _blocking_decisions(items) -> list:
    """1.5.2（W15）：只有显式标记“（前置）”的用户决定会让本轮停下等用户；“这一项不阻塞”之类的文字不再误判。"""
    return [x for x in (items or []) if re.search(r"[（(]前置[)）]", str(x))]


def h_review(ctx):
    inst, st = ctx.inst, ctx.st
    it = read_json(inst.turn_dir() / "intake.json", {}) or {}
    d = it.get("directives") or {}
    flags = st.setdefault("turn_flags", {})
    if flags.get("review_accepted"):
        return _after_review(ctx, it, d)
    findings = []
    for i, c in enumerate(it.get("commands", []), 1):
        cl = policy.classify(c, ctx.pol)
        if not cl["auto"]:
            findings.append(f"命令 {i}「{c}」：" + "；".join(cl["reasons"]))
    rel = d.get("release")
    if rel:
        cand = [p for p in st.get("delivered", []) if Path(p).name == Path(rel).name]
        if not cand:
            r = _anomaly(ctx, {"source": "review", "kind": "release_not_delivered"}, f"指令要求落地 {rel}，但本轮交付文件中没有")
            return _to_report(ctx) if r[0] == "report" else r
        with tempfile.TemporaryDirectory() as td:
            try:
                src = release._extract(Path(cand[0]), Path(td))
            except WfError as e:
                r = _anomaly(ctx, {"source": "apply", "text": str(e)}, str(e))
                return _to_report(ctx) if r[0] == "report" else r
            newv = parse_project_md((src / "PROJECT.md").read_text(encoding="utf-8")).get("version") \
                if (src / "PROJECT.md").exists() else None
            curv = inst.version()
            if _vt(newv) is None or (_vt(curv) and _vt(newv) <= _vt(curv)):
                r = _anomaly(ctx, {"source": "review", "kind": "version_not_next"}, f"当前 {curv}，交付 {newv}")
                return _to_report(ctx) if r[0] == "report" else r
            for f in policy.release_findings(inst, src, ctx.pol):
                findings.append(f"版本包 {f['file']}：{f['why']}")
            if anomaly.transport_cfg().get("review"):
                findings += _independent_review(ctx, src, cand[0])
        if not ctx.pol.get("auto_apply", True):
            findings.append(f"版本包 {rel}：policy 设定落地版本需要用户批准")
        st["release_path"] = cand[0]
    blocking = _blocking_decisions(it.get("user_decisions", []))
    findings += [f"Claude 标注的前置决定：{x}" for x in blocking]
    review_rec = {"rows": [policy.classify(c, ctx.pol) for c in it.get("commands", [])], "findings": findings,
                  "auto": not findings, "ts": now_iso()}
    write_json(inst.turn_dir() / "review.json", {**review_rec, "need_user": len(findings),
                                                   "caution": 0, "accepted": not findings})
    if findings:
        text = "以下内容需要用户批准后才会执行：\n" + "\n".join(f"- {x}" for x in findings)
        machine.go(inst, "AWAITING_USER", by="driver", reason=f"{len(findings)} 项需要用户批准")
        inst.state["needs_text"] = text
        inst.save()
exit=0  耗时 0.0s
```

5) wf.py 第 195–240 行（schedule plist）。 —— 通过
（c05 原文共 1934 字符，全文 sha256 前 12 位 2bce316a1006，超出本条回报的预算，已分 1 片排入续传）

6) CHANGELOG.md 第 1–20 行。 —— 通过
```
$ sed -n 1,20p /Users/admin/.workbuddy-ai/tools/collab-wf/CHANGELOG.md
# CHANGELOG（collab-wf）

## 1.5.8 — 2026-10-10

全自动、不需要用户提醒（collab-wf-dev 第 19–20 轮：交付物要执行端手动安装，而执行端只在用户发话时被唤醒；状态机一秒跑完一轮，约七成时间在等唤醒）。
- 自动安装工具版本（用户第 19 轮批准）：policy.json 的 auto_self_update 为 true、白名单放行时，驱动器可以执行 wf self-update --auto workflow/outbox/collab-wf-X.Y.Z.zip。安装前由当前已安装的代码检查：改动了护栏文件（policy.py、release.py、asks.py、anomalies.json）的版本拒绝，停下等用户；配置了另一个模型系列审查时先审 diff，block 也拒绝；--auto 不允许跳过自检。
- 效率：wf tick 在“发送”与“等待 Claude”时，下一次轮询若在本次时间预算内，就在 tick 里等到那时再查；发现回复的延迟从约 300 秒降到轮询间隔（120 秒）以内。空闲时不轮询。
- 命令退出码为 0、只是没通过判据时不再报“命令执行失败，退出码 0”，由验收报 CHECK_FAIL。
- 新增 docs/REVIEW.md：另一个模型系列审查的约定（何时调用、参数、审查清单、输出、只能阻止的处置）。
- 文档：DRIVER.md 新增第 18 节；PROCESS.md 第 8 节写明自动安装。
- 第 19 轮交付的同名 1.5.7 构建器（含上述效率与审查文档）因 1.5.7 已安装而未能构建，内容并入本版。
- 自检新增 4 项；原有检查没有删改。

## 1.5.7 — 2026-10-10

看得到进度，不默默执行（collab-wf-dev 第 17 轮：执行端的本地胶水 tick_notify.py 在换成官方定时器配置后失效，收进工具）。
- 新增 wflib/notify.py：wf tick 跑完后，把各项目新出现的待决事项与提示（与 wf ask 同一来源，包括需要决定、阻塞、暂停、目标达成、额度等待、停留超时、驱动器停了）弹成 macOS 通知；每件事只通知一次，事项处理掉后清除记录；发送失败不记为已通知。
- 新增 wf notify test | status；可用 WF_NOTIFY=0 或 local/notify.json 关闭。
- 文档：DRIVER.md 新增第 17 节；PROCESS.md 第 8 节写明通知。
- 自检新增 3 项；原有检查没有删改。
exit=0  耗时 0.0s
```

7) selftest_v15.py 中提到「护栏」的位置，可能没有匹配。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n 护栏 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_v15.py
657:    _ok("1.5.8 自动安装工具版本：改动护栏文件（白名单判定、批准、安装核验、异常处置）的版本要用户确认",
exit=0  耗时 0.0s
```

8) selftest_driver.py 中提到「护栏」的位置，可能没有匹配。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n 护栏 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py
exit=1  耗时 0.0s
```

9) selftest.py 中提到「护栏」的位置，可能没有匹配。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n 护栏 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py
exit=1  耗时 0.0s
```

10) core.py 中 LOCAL 的定义。 —— 通过
（c10 原文共 265 字符，全文 sha256 前 12 位 f48f65b09da6，超出本条回报的预算，已分 1 片排入续传）

（续传队列中还有 2 片原文，会在后续回报中自动先发送；需要尽快拿到时，安排一轮不执行命令的回报即可）

③ 需要决策的点
无。

④ 需要注意（Attention）
- 自动处置 EXEC_FAIL → report：第 8 条命令「grep -n 护栏 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py」退出码 1
- 自动处置 EXEC_FAIL → report：第 9 条命令「grep -n 护栏 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py」退出码 1
- 清单第 7, 8, 9 项没有机器判据，结论需要 Claude 根据原文判断
- 当前对话已累计约 72324 字符、3 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

附：仓库指纹 65f6fad7a591346c（v0.7，4 个文件）
附：发送前额度 5 小时窗口 18.0%，7 天窗口 65.0%
