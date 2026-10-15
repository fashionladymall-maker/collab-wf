"""1.5.9：停止底线（资金、密钥、合规、实盘）集中在这一个文件里，policy.py 从这里导入，原有名字保持可用。

- 驱动器自动安装工具版本（wf self-update --auto）时，改动了本文件的版本一律拒绝，须由用户确认后手动安装：
  底线本身就属于那四类事，按用户的约定只能由用户决定。
- 其余文件的改动可以自动安装，但安装前由“当前已安装”的 probe() 对候选版本做行为检查：候选版本对 FLOOR_CASES 必须全部停下，
  仍把本文件的改动判为要用户确认，自动安装仍做这两项检查，驱动器的执行前评审仍经过 classify。
  用例写在已安装的本文件里，候选版本无法连同检查一起改掉。
- 边界：行为检查能拦住意外的削弱与回归，拦不住刻意绕开检查的代码；后者靠“改本文件要用户确认”与另一个模型系列审查
  （docs/REVIEW.md）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# 四类必须停下来的事：无论项目怎么配置都生效
STOP_FLOOR = [
    ("资金", r"withdraw|transfer|提现|转账|出金|入金|deposit"),
    ("密钥", r"(^|[\s/'\"])\.env\b|API_?KEY|API_?SECRET|SECRET_?KEY|ACCESS_?TOKEN|passw(or)?d|私钥|private[_ ]key"),
    ("合规", r"\bvpn\b|proxychains|--proxy\b|socks5://|绕过|地域限制"),
    ("实盘", r"--confirm-real-money|mode:\s*live|live\.mode|实盘"),
]
# 版本包内容检查用的技术性模式（只查代码与配置文件的新增行；文档里的说明文字不算）
DIFF_FLOOR = [
    ("实盘", r"--confirm-real-money|^\s*mode:\s*live\b|live\.mode\s*=\s*['\"]?live"),
    ("密钥", r"(API_?KEY|API_?SECRET|SECRET_?KEY|ACCESS_?TOKEN)\s*[:=]\s*['\"]?[A-Za-z0-9/+_\-]{12,}"),
    ("资金", r"\.(withdraw|transfer)\s*\(|create_withdrawal|sapi/v1/capital/withdraw"),
]
CODE_SUFFIXES = {".py", ".sh", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".js", ".ts", ".env"}
# 护栏命令：只有用户能执行（驱动器执行这些等于自己改自己的规则）
GUARDRAIL = re.compile(r"^\s*wf\s+(self-update|self-rollback|self-init|pin|policy|goals\s+(approve|confirm)|approve|"
                       r"unpause|force|unblock|resume|undo|rollback|init)\b")
# 命令链与重定向：一律需要用户批准（防止在白名单命令后面拼接任意命令）
META = re.compile(r";|&&|\|\||`|\$\(|>|<|(^|[^&])&($|[^&])")

# 底线用例：每一条都必须被候选版本的 classify 判为“需要用户”，并归入对应类别
FLOOR_CASES = [
    ("资金", "python3 tools/pay.py withdraw --all"),
    ("资金", "python3 run.py --action 转账"),
    ("密钥", "cat .env"),
    ("密钥", "grep -n API_KEY config.yaml"),
    ("合规", "proxychains python3 run.py"),
    ("合规", "python3 run.py --proxy socks5://127.0.0.1:1080"),
    ("实盘", "python3 run.py --confirm-real-money"),
    ("实盘", "python3 run.py --mode 实盘"),
]

# 在候选版本的目录里运行（隔离的子进程，不与已安装的模块混用）
_PROBE = r"""
import inspect, json, sys
root, cases = sys.argv[1], json.loads(sys.argv[2])
sys.path.insert(0, root)
from wflib import driver, policy, release
out = []
pol = {"allow": [".*"], "pipe_filters": ["head", "tail", "grep", "wc", "sort", "uniq", "cut"],
       "auto_self_update": True, "stop": []}
for cat, cmd in cases:
    c = policy.classify(cmd, pol)
    if c.get("auto") or cat not in (c.get("categories") or []):
        out.append("classify 没有拦住“%s”类：%s" % (cat, cmd))
if "wflib/floor.py" not in release.guard_changes({"wflib/floor.py": "new"}, {"wflib/floor.py": "old"}):
    out.append("guard_changes 不再把 floor.py 的改动判为要用户确认")
su = inspect.getsource(release.self_update)
if "guard_changes(" not in su or "probe(" not in su:
    out.append("self_update 的自动安装分支不再调用 guard_changes 与 floor.probe")
if "policy.classify(" not in inspect.getsource(driver.h_review):
    out.append("驱动器的执行前评审不再调用 policy.classify")
print("FLOOR_PROBE " + json.dumps(out, ensure_ascii=False))
"""


def probe(root) -> list:
    """对 root 目录里的候选版本做底线行为检查。返回问题列表；空列表表示通过。检查本身无法运行也算不通过。"""
    root = Path(root)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory() as td:
        env["WF_LOCAL_DIR"] = td
        try:
            r = subprocess.run([sys.executable, "-c", _PROBE, str(root), json.dumps(FLOOR_CASES, ensure_ascii=False)],
                               cwd=str(root), env=env, capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.SubprocessError) as e:
            return [f"底线行为检查无法运行：{e}"]
    for line in reversed((r.stdout or "").splitlines()):
        if line.startswith("FLOOR_PROBE "):
            try:
                return [str(x) for x in json.loads(line[len("FLOOR_PROBE "):])]
            except ValueError:
                break
    return [f"底线行为检查没有给出结果（退出码 {r.returncode}）：{(r.stderr or r.stdout or '')[-300:]}"]
