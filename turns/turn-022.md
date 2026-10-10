【回报 · 第 22 轮 · v0.7】档位：high

① 环境障碍与偏离
- 【驱动指令】有无法解析的行：验收 1 缺少命令编号或条件：验收 1：exit=0；验收 2 缺少命令编号或条件：验收 2：exit=0；验收 3 缺少命令编号或条件：验收 3：exit=0；验收 4 缺少命令编号或条件：验收 4：exit=0；验收 5 缺少命令编号或条件：验收 5：exit=0；验收 6 缺少命令编号或条件：验收 6：exit=0；验收 7 缺少命令编号或条件：验收 7：exit=0

② 关键数字与终端原文

1) doctor 显示工具 1.5.8、PROJECT.md v0.7，并给出当前指纹。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ wf doctor
✓ Python：3.12.13
✓ 工作流定义：workflow.json，14 个状态
✓ 工具版本：1.5.8（wf-v1.5.8）
✓ 看板进程：运行中（PID 79276，端口 8765，加载的版本 1.5.8）
✓ 版本匹配：项目固定 1.5，工具 1.5.8
✓ PROJECT.md：v0.7，阶段：1.5.7 已安装，1.5.8（自动安装工具版本、tick 内等回复）构建器已交付
✓ 项目工作区：干净
✓ 当前步骤：EXECUTE（执行与取证），第 21 轮
✓ 驱动器心跳：正在运行（PID 83068）
✓ 额度数据：2 分钟前更新
! 事件序号：共 814 条：重复 5 个、倒序 2 处、无法解析 0 行  → 进程曾在“写事件”与“写状态”之间被打断；1.5.0 起先写状态再追加事件，历史记录不改
exit=0  耗时 0.8s
```

2) 最近 3 个提交的原文，说明 HEAD 和 v0.7 标签的关系。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ git log --oneline --decorate -3
2a5e72f (HEAD -> main) policy: 放行 wf self-update --auto（1.5.8 自动安装），开启 auto_self_update
ed4d6ca (tag: v0.7) release v0.7
df19fc2 (tag: v0.6) release v0.6
exit=0  耗时 0.1s
```

3) policy.json 全文。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ cat workflow/policy.json
{
  "_说明": "collab-wf-dev 的自动执行白名单（正则，匹配整条命令）。只有用户批准后才生效；任何改动都需要重新批准。内置停止底线与护栏命令不受本文件影响。",
  "allow": [
    "^wf (version|fingerprint|doctor|status|explain|bridge-check|goals|audit|self-check)( |$)",
    "^(/Users/admin/\\.workbuddy-ai/binaries/python/versions/3\\.13\\.12/bin/)?python3 (build_wf_[0-9]+\\.py|workflow/evidence/turn-[0-9]+/delivered/build_wf_[0-9]+\\.py) --out workflow/outbox$",
    "^wf self-update --auto workflow/outbox/collab-wf-[0-9]+\\.[0-9]+\\.[0-9]+\\.zip$",
    "^git (log|status|diff|show)( |$)",
    "^git -C /Users/admin/\\.workbuddy-ai/tools/collab-wf (log|status|diff|show|ls-tree)( |$)",
    "^(?!.*\\.\\.)(?!.*/\\.(?!workbuddy-ai/tools/collab-wf/))(?!.*collab-wf/local/)(cat|head|tail|wc)( -n [0-9]+| -l)? (/Users/admin/\\.workbuddy-ai/tools/collab-wf/|[A-Za-z0-9_])[A-Za-z0-9_./-]*$",
    "^(?!.*\\.\\.)(?!.*/\\.(?!workbuddy-ai/tools/collab-wf/))(?!.*collab-wf/local/)sed -n [0-9]+,[0-9]+p (/Users/admin/\\.workbuddy-ai/tools/collab-wf/|[A-Za-z0-9_])[A-Za-z0-9_./-]*$",
    "^(?!.*\\.\\.)(?!.*/\\.(?!workbuddy-ai/tools/collab-wf/))(?!.*collab-wf/local/)grep -n( -[ABC] [0-9]+)? [^-\\s;&<>`$|\\\\'\\\"][^\\s;&<>`$|\\\\'\\\"]* (/Users/admin/\\.workbuddy-ai/tools/collab-wf/|[A-Za-z0-9_])[A-Za-z0-9_./-]*$"
  ],
  "pipe_filters": [
    "head",
    "tail",
    "grep",
    "wc",
    "sort",
    "uniq",
    "cut"
  ],
  "diff_stop": [],
  "auto_apply": true,
  "auto_self_update": true,
  "max_rounds_per_day": 24,
  "max_consecutive_failed_rounds": 3,
  "max_rounds_without_goal_progress": 8,
  "command_timeout_s": 1800,
  "first_poll_after_s": 120,
  "poll_interval_s": 120,
  "max_conversation_turns": 20,
  "send_unknown_wait": true,
  "max_leaf_refusals": 0,
  "gates": []
}
exit=0  耗时 0.0s
```

4) policy.py 全文（1.5.8）。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ cat /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/policy.py
"""自主边界：用户批准的命令白名单 + 工具内置的停止底线 + 护栏命令。
设计原则：
- 底线写在代码里，项目的 policy.json 只能追加，不能删除或放宽。
- 白名单默认拒绝：不在白名单里的命令一律停下来问用户。
- 驱动器不能改自己的护栏：升级工具、改白名单与目标、批准、解除暂停等只能由用户执行。
- policy.json / goals.json 随版本交付，但只有用户批准过的内容（按 sha256）才生效；批准记录受保护，版本包无法覆盖。
"""
from __future__ import annotations

import difflib
import hashlib
import re
from pathlib import Path

from .core import Instance, WfError, now_iso, read_json, write_json

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
# 只读的 wf 命令：驱动器可以在白名单允许时自动执行。其余 wf 命令会改变工作流状态，
# 由驱动器内部再调用会与驱动器自身的状态写入互相覆盖（F21），所以驱动器永远不执行它们，只列为手动步骤。
WF_READONLY = re.compile(r"^\s*wf\s+(version|fingerprint|doctor|status|explain|step|help|audit|decisions|effort|projects|"
                         r"bridge-check|goals\s*$|goals\s+show|policy\s*$|policy\s+show|conv\s*$|conv\s+show|"
                         r"transport(\s+(check|test))?\s*$|schedule\s+(status|plist))(\s|$)")
# 命令链与重定向：一律需要用户批准（防止在白名单命令后面拼接任意命令）
META = re.compile(r";|&&|\|\||`|\$\(|>|<|(^|[^&])&($|[^&])")
DEFAULT_PIPE_FILTERS = ["head", "tail", "grep", "wc", "sort", "uniq", "cut"]
# 1.5.8：用户批准的“驱动器自动安装工具版本”只放行这一种形式（policy.json 的 auto_self_update 为 true，且白名单放行）；
# 改动护栏文件的版本由 release.self_update(auto=True) 拒绝，另一个模型系列审查阻止时也拒绝
AUTO_SELF_UPDATE = re.compile(r"^\s*wf\s+self-update\s+--auto\s+workflow/outbox/collab-wf-\d+\.\d+\.\d+\.zip\s*$")


def sha256(p: Path):
    p = Path(p)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def approvals_path(inst: Instance) -> Path:
    return inst.wf / "approvals.json"


def approvals(inst: Instance) -> dict:
    return read_json(approvals_path(inst), {}) or {}


def approve(inst: Instance, kind: str, note: str) -> dict:
    """用户批准 policy 或 goals 的当前内容。只能由用户命令调用；驱动器没有调用路径。"""
    p = inst.wf / f"{kind}.json"
    if not p.exists():
        raise WfError(f"没有 workflow/{kind}.json，无可批准的内容。")
    if read_json(p) is None:
        raise WfError(f"workflow/{kind}.json 不是有效的 JSON。")
    a = approvals(inst)
    rec = {"sha256": sha256(p), "ts": now_iso(), "note": note}
    a.setdefault("history", []).append({"kind": kind, **rec})
    a[kind] = rec
    write_json(approvals_path(inst), a)
    inst.event("approve", kind=kind, sha=rec["sha256"][:12], note=note, by="user")
    return rec


def status(inst: Instance, kind: str) -> dict:
    """返回 {exists, approved, current, approved_sha, ts}；approved 只在内容与批准时完全一致时为真。"""
    p = inst.wf / f"{kind}.json"
    cur = sha256(p)
    rec = approvals(inst).get(kind) or {}
    return {"exists": cur is not None, "approved": bool(cur and rec.get("sha256") == cur), "current": cur,
            "approved_sha": rec.get("sha256"), "ts": rec.get("ts"), "changed_since_approval": bool(rec and cur != rec.get("sha256"))}


def load(inst: Instance):
    """只返回已批准的策略；未批准或被改动时返回 None。"""
    st = status(inst, "policy")
    return read_json(inst.wf / "policy.json") if st["approved"] else None


def classify(cmd: str, pol: dict) -> dict:
    """命令分级：ok（可自动执行）/ user（需要用户）。返回原因与类别。"""
    reasons, cats = [], []
    for cat, pat in STOP_FLOOR + [(s.get("category", "项目规定"), s["pattern"]) for s in pol.get("stop", [])]:
        if re.search(pat, cmd, re.I):
            cats.append(cat)
            reasons.append(f"属于必须停下的“{cat}”类")
    manual = False
    first = cmd.split("|")[0].strip()
    auto_su = (pol or {}).get("auto_self_update") is True and bool(AUTO_SELF_UPDATE.match(first))
    if auto_su:   # 1.5.8：只此一种 wf 写操作可由驱动器执行；仍须在白名单内（下面照常检查）
        cats.append("自动安装工具版本")
    elif re.match(r"^\s*wf(\s|$)", first) and not WF_READONLY.match(first) and not re.match(r"^\s*wf\s+self-check\s*$", first):
        manual = True
        cats.append("手动步骤")
        reasons.append("会改变工作流状态的 wf 命令，驱动器不执行，须在驱动器之外手动执行")
    elif GUARDRAIL.search(cmd):
        cats.append("护栏")
        reasons.append("会改变自动化自身的规则或状态，只能由用户执行")
    segments = [s.strip() for s in cmd.split("|")]
    if META.search(cmd.replace("2>&1", "")):
        reasons.append("包含命令拼接或重定向（; && || > < ` $( &），需要用户批准")
    else:
        allow = [re.compile(p) for p in pol.get("allow", [])]
        filters = pol.get("pipe_filters", DEFAULT_PIPE_FILTERS)
        if not any(a.search(segments[0]) for a in allow):
            reasons.append("不在已批准的命令白名单中")
        for seg in segments[1:]:
            if not seg or seg.split()[0] not in filters:
                reasons.append(f"管道后的“{seg.split()[0] if seg else ''}”不是允许的过滤命令")
    return {"cmd": cmd, "auto": not reasons, "reasons": reasons, "categories": cats, "manual": manual}


def release_findings(inst: Instance, src: Path, pol: dict) -> list:
    """检查版本包相对当前仓库新增的内容：命中停止规则，或改动了白名单与目标定义，都需要用户。"""
    out = []
    pats = list(DIFF_FLOOR) + [("项目规定", p) for p in pol.get("diff_stop", [])]
    for f in sorted(Path(src).rglob("*")):
        if not f.is_file() or f.name == "RELEASE.json" or "__pycache__" in f.parts:
            continue
        rel = str(f.relative_to(src))
        if rel in ("workflow/policy.json", "workflow/goals.json"):
            if sha256(f) != sha256(inst.root / rel):
                out.append({"file": rel, "why": "改动了白名单或目标定义（生效前需要用户批准）"})
            continue
        if f.suffix not in CODE_SUFFIXES and not f.name.startswith(".env"):
            continue
        try:
            new = f.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        old_p = inst.root / rel
        old = old_p.read_text(encoding="utf-8", errors="replace").splitlines() if old_p.exists() else []
        added = [l[1:] for l in difflib.unified_diff(old, new, lineterm="", n=0) if l.startswith("+") and not l.startswith("+++")]
        for line in added:
            for cat, pat in pats:
                if re.search(pat, line, re.I):
                    out.append({"file": rel, "why": f"新增内容命中“{cat}”：{line.strip()[:120]}"})
                    break
    return out
exit=0  耗时 0.0s
```

5) release.py 全文（1.5.8），放不下的部分续传。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ cat /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/release.py
"""版本管理：项目版本落地与回滚；工具自身的初始化、升级与回滚。
设计要点：先校验、后复制（校验失败时仓库不被改动）；删除只按交付清单进行、用 Python 完成（不调用 rm）；
回滚生成新的提交而不改写历史。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from .core import (WF_HOME, Instance, WfError, git, git_identity, matches_protect, now_iso, pid_alive,
                   read_json, tool_version, write_json)
from .projectmd import parse_project_md, validate
from . import fidelity


def _extract(zip_path: Path, dest: Path) -> Path:
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise WfError(f"找不到文件：{zip_path}")
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise WfError(f"不是有效的 zip 文件：{zip_path}")
    bad = zf.testzip()
    if bad:
        raise WfError(f"zip 内文件损坏：{bad}")
    tops = set()
    for n in zf.namelist():
        if n.startswith("/") or ".." in Path(n).parts:
            raise WfError(f"zip 中有越界路径，拒绝处理：{n}")
        tops.add(n.split("/")[0])
    tops.discard("__MACOSX")
    if len(tops) != 1:
        raise WfError(f"zip 应该只有一个顶层目录，实际有：{sorted(tops)}")
    zf.extractall(dest)
    # zipfile 不会恢复 Unix 权限：按 zip 中记录的权限补回（否则可执行脚本会丢失执行位）
    for info in zf.infolist():
        mode = (info.external_attr >> 16) & 0o777
        if mode and not info.is_dir():
            os.chmod(dest / info.filename, mode)
    return dest / tops.pop()


def _dirty(root: Path, protect) -> list:
    r = git(["status", "--porcelain", "--untracked-files=all"], root, check=False)
    out = []
    for line in r.stdout.splitlines():
        rel = line[3:].strip().strip('"')
        if " -> " in rel:
            rel = rel.split(" -> ")[1]
        if not matches_protect(rel, protect):
            out.append(line)
    return out


def _copy_tree(src: Path, dst: Path, protect, skip=("RELEASE.json",), written=None) -> list:
    written = [] if written is None else written
    for dirpath, dirnames, filenames in os.walk(src):
        rel_dir = Path(dirpath).relative_to(src)
        dirnames[:] = [d for d in dirnames if d != "__pycache__"
                       and not matches_protect(str(rel_dir / d) + "/", protect)]
        for fn in filenames:
            rel = str(rel_dir / fn) if str(rel_dir) != "." else fn
            if fn in skip and str(rel_dir) == "." or matches_protect(rel, protect) or fn.endswith(".pyc"):
                continue
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(dirpath) / fn, target)
            written.append(rel)
    return written


def _delete(root: Path, rels, protect, done=None) -> list:
    done = [] if done is None else done
    if os.environ.get("WF_TEST_FAIL_DELETE"):
        raise OSError("测试：模拟删除阶段失败")
    for rel in rels or []:
        p = (root / rel).resolve()
        if root.resolve() not in p.parents:
            raise WfError(f"删除路径越界：{rel}")
        if matches_protect(rel, protect):
            raise WfError(f"删除清单包含受保护路径：{rel}")
        if p.is_dir():
            shutil.rmtree(p)
            done.append(rel)
        elif p.exists():
            p.unlink()
            done.append(rel)
    return done


def _commit(root: Path, message: str, tag=None):
    if not (root / ".git").exists():
        git(["init", "-q"], root)
    git(["add", "-A"], root)
    status = [l.split("\t") for l in git(["diff", "--cached", "--name-status"], root).stdout.splitlines() if l]
    changed = [f"{x[0][0]} {x[-1]}" for x in status]
    if not changed:
        return None, []
    git(git_identity() + ["commit", "-q", "-m", message], root)
    head = git(["rev-parse", "--short", "HEAD"], root).stdout.strip()
    if tag and not git(["tag", "-l", tag], root).stdout.strip():
        git(["tag", tag], root)
    return head, changed


def _restart_hints(inst: Instance, changed) -> list:
    hints = []
    for proc in inst.cfg.get("processes", []):
        pats = proc.get("code_paths", [])
        paths = [c.split(" ", 1)[1] if " " in c else c for c in changed]
        hit = [c for c in paths if any(c == p or c.startswith(p) for p in pats)]
        if hit and pid_alive(inst.root / proc["pidfile"], proc["match"]):
            hints.append(f"{proc['name']}正在运行，且相关代码有变化（{', '.join(hit[:5])}）。需重启才会生效：{proc.get('restart', '')}")
    return hints


# ---------------- 项目 ----------------

def apply_zip(inst: Instance, zip_path, by="executor") -> dict:
    root = inst.root
    protect = inst.protect()
    if (root / ".git").exists():
        dirty = _dirty(root, protect)
        if dirty:
            raise WfError("工作区有未提交的改动，已停止（AP-2）：\n  " + "\n  ".join(dirty[:20]))
    with tempfile.TemporaryDirectory() as td:
        src = _extract(Path(zip_path), Path(td))
        if not (src / "PROJECT.md").exists():
            raise WfError("zip 中没有 PROJECT.md（AP-4）")
        p = parse_project_md((src / "PROJECT.md").read_text(encoding="utf-8"))
        cl = src / "CHANGELOG.md"
        issues = validate(p, cl.read_text(encoding="utf-8") if cl.exists() else None)
        if issues:
            raise WfError("PROJECT.md 校验未通过，未改动仓库（AP-1）：\n  - " + "\n  - ".join(issues))
        manifest = read_json(src / "RELEASE.json", {}) or {}
        tracked = set(git(["ls-files"], root).stdout.splitlines()) if (root / ".git").exists() else None
        has_head = tracked is not None and git(["rev-parse", "-q", "--verify", "HEAD"], root, check=False).returncode == 0
        cfg_before = json.loads(json.dumps(inst.cfg))
        written, deleted, cfg_keys = [], [], []
        rel_json = f"workflow/releases/{p['version']}.json"
        try:
            _copy_tree(src, root, protect, written=written)
            _delete(root, manifest.get("delete"), protect, done=deleted)
            if manifest.get("wf_config"):
                # 项目级工具配置随版本交付（wf.json 本身受保护，不会被 zip 覆盖），只合并允许的键
                for k, v in manifest["wf_config"].items():
                    if k in ("processes", "protect", "hook"):
                        inst.cfg[k] = v
                        cfg_keys.append(k)
                if cfg_keys:
                    write_json(inst.cfg_path, inst.cfg)
            if manifest:
                (root / "workflow" / "releases").mkdir(parents=True, exist_ok=True)
                shutil.copy2(src / "RELEASE.json", root / rel_json)
            head, changed = _commit(root, f"release {p['version']}", tag=p["version"])
        except Exception as e:
            note = _restore_after_failure(root, tracked, has_head, written, manifest.get("delete") or [],
                                          ["workflow/wf.json", rel_json], inst, cfg_before)
            raise WfError(f"落地失败：{e}\n{note}")
    res = {"version": p["version"], "commit": head, "changed": changed, "deleted": deleted, "config": cfg_keys,
           "hints": _restart_hints(inst, changed), "notes": manifest.get("notes")}
    inst.event("apply", version=p["version"], commit=head, changed=len(changed), deleted=deleted or None, by=by)
    return res


def _restore_after_failure(root, tracked, has_head, written, delete_list, extra, inst, cfg_before) -> str:
    """落地中途失败时，把本次触及的文件恢复到落地前，使工作区回到干净状态、可以直接重试。"""
    if tracked is None or not has_head:
        return "仓库还没有提交记录，无法自动恢复，请人工检查工作区后再重试。"
    touched = set(written) | set(extra)
    for d in delete_list:
        d = d.rstrip("/")
        touched |= {t for t in tracked if t == d or t.startswith(d + "/")}
    restore = sorted(t for t in touched if t in tracked)
    remove = sorted(t for t in touched if t not in tracked)
    errors = []
    for i in range(0, len(restore), 200):
        r = git(["checkout", "HEAD", "--"] + restore[i:i + 200], root, check=False)
        if r.returncode != 0:
            errors.append(r.stderr.strip())
    removed = 0
    for rel in remove:
        f = root / rel
        try:
            if f.is_file():
                f.unlink()
                removed += 1
        except OSError as e:
            errors.append(f"{rel}: {e}")
    inst.cfg = cfg_before
    if "workflow/wf.json" not in tracked:
        write_json(inst.cfg_path, cfg_before)
    left = _dirty(root, inst.protect())
    if errors or left:
        detail = "；".join(errors[:3]) + (f"；仍有 {len(left)} 处未恢复：{'、'.join(l[3:] for l in left[:5])}" if left else "")
        return f"自动恢复不完整（{detail}）。请人工处理后再重试。"
    return f"已自动恢复工作区（恢复 {len(restore)} 个文件，移除 {removed} 个新文件），排除原因后可以直接重试。"


def _find_ref(root: Path, to: str) -> str:
    if git(["rev-parse", "-q", "--verify", f"refs/tags/{to}"], root, check=False).returncode == 0:
        return to
    r = git(["log", "--format=%H", f"--grep=^release {to}$"], root, check=False)
    shas = r.stdout.split()
    if shas:
        return shas[0]
    if git(["rev-parse", "-q", "--verify", to + "^{commit}"], root, check=False).returncode == 0:
        return to
    raise WfError(f"找不到版本 {to}（既不是标签，也没有“release {to}”提交，也不是提交号）")


def restore_tree(repo: Path, ref: str, message: str, protect=()) -> tuple:
    """把工作区恢复成 ref 时的内容，并作为一个新提交记录（不改写历史）。"""
    at_ref = set(git(["ls-tree", "-r", "--name-only", ref], repo).stdout.splitlines())
    now_files = set(git(["ls-files"], repo).stdout.splitlines())
    git(["checkout", ref, "--", "."], repo)
    removed = []
    for rel in sorted(now_files - at_ref):
        if matches_protect(rel, protect):
            continue
        p = repo / rel
        if p.exists():
            p.unlink()
            removed.append(rel)
    head, changed = _commit(repo, message)
    return head, changed, removed


def rollback(inst: Instance, to: str, by="executor") -> dict:
    root = inst.root
    dirty = _dirty(root, inst.protect())
    if dirty:
        raise WfError("工作区有未提交的改动，不能回滚：\n  " + "\n  ".join(dirty[:20]))
    ref = _find_ref(root, to)
    cur = inst.version()
    head, changed, removed = restore_tree(root, ref, f"rollback to {to} (from {cur})", inst.protect())
    res = {"from": cur, "to": to, "commit": head, "changed": changed, "removed": removed,
           "hints": _restart_hints(inst, changed)}
    inst.event("rollback", frm=cur, to_version=to, commit=head, by=by)
    return res


# ---------------- 工具自身 ----------------

SELF_PROTECT = [".git/", "local/"]


def self_init() -> str:
    if not (WF_HOME / ".git").exists():
        git(["init", "-q"], WF_HOME)
    gi = WF_HOME / ".gitignore"
    if not gi.exists():
        gi.write_text("local/\n__pycache__/\n*.pyc\n", encoding="utf-8")
    (WF_HOME / "local").mkdir(exist_ok=True)
    v = tool_version()
    head, _ = _commit(WF_HOME, f"wf {v}", tag=f"wf-v{v}")
    return head or git(["rev-parse", "--short", "HEAD"], WF_HOME).stdout.strip()


def _vtuple(v: str):
    return tuple(int(x) for x in v.strip().split("."))


# 1.5.8：驱动器自动安装工具版本时，改动了这些文件的版本必须由用户确认后手动安装（护栏：白名单判定、批准与决定码、
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
    if not (WF_HOME / ".git").exists():
        raise WfError("工具尚未初始化版本库，先运行 wf self-init")
    if _dirty(WF_HOME, SELF_PROTECT):
        raise WfError("工具目录有未提交的改动，已停止。用 git -C 工具目录 status 查看。")
    cur = tool_version()
    with tempfile.TemporaryDirectory() as td:
        src = _extract(Path(zip_path), Path(td))
        src_files = fidelity.tree_hashes(src)   # 1.5.0：落地后逐个核对，确认装上的就是这个版本包
        new = (src / "VERSION").read_text(encoding="utf-8").strip()
        if _vtuple(new) < _vtuple(cur) or (_vtuple(new) == _vtuple(cur) and not allow_same):
            raise WfError(f"新版本 {new} 不高于当前版本 {cur}。回到旧版本请用 wf self-rollback。")
        if auto:   # 1.5.8：驱动器自动安装（policy.json 的 auto_self_update 与白名单放行 wf self-update --auto）
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
            env.pop("WF_LOCAL_DIR", None)
            r = subprocess.run([sys.executable, str(src / "wf.py"), "selftest"], capture_output=True, text=True,
                               env=env, timeout=600)
            if r.returncode != 0:
                raise WfError(f"新版本 {new} 的自检未通过，未安装：\n{r.stdout[-3000:]}\n{r.stderr[-2000:]}")
        manifest = read_json(src / "RELEASE.json", {}) or {}
        _copy_tree(src, WF_HOME, SELF_PROTECT)
        _delete(WF_HOME, manifest.get("delete"), SELF_PROTECT)
    # 落地后按实际安装的树重算清单，使它一定等于新版本——打包时忘了重算也不会让 self-check 失准（③-1）
    write_manifest()
    head, changed = _commit(WF_HOME, f"wf {new}", tag=f"wf-v{new}")
    from . import server
    board_was_running = bool((fidelity.safe_call(server.info, {}) or {}).get("pid"))
    restarted = _restart_board()
    # 1.5.0：升级本身要可验证（F25）——磁盘版本、文件与版本包逐个一致、看板以新版本运行；不通过就自动回滚
    receipt = fidelity.post_install_receipt(new, src_files, _tool_files(), board_was_running,
                                            lambda: fidelity.safe_call(server.info, {}), WF_HOME)
    if not receipt["ok"]:
        failed_tag = f"wf-v{new}-failed-{fidelity.stamp()}"
        git(["tag", failed_tag, f"wf-v{new}"], WF_HOME, check=False)
        git(["tag", "-d", f"wf-v{new}"], WF_HOME, check=False)
        try:
            self_rollback(f"wf-v{cur}")
            rb = f"已自动回滚到 {cur}；失败版本的标签改名为 {failed_tag}"
        except WfError as e:
            rb = f"自动回滚失败：{e}。请手动运行 wf self-rollback {cur}"
        raise WfError(f"升级到 {new} 后核验未通过：" + "；".join(receipt["problems"]) + "。" + rb)
    print(f"升级核验通过：磁盘版本 {receipt['version']}，{receipt['files_checked']} 个文件与版本包一致，"
          + (f"看板已加载 {new}" if board_was_running else "看板未在运行")
          + ("；" + "；".join(receipt["notes"]) if receipt["notes"] else ""), file=sys.stderr, flush=True)
    return {"from": cur, "to": new, "commit": head, "changed": changed, "tested": not skip_test,
            "board_restarted": restarted, "receipt": receipt}


def _restart_board() -> bool:
    """看板是常驻进程，升级只换了磁盘文件，进程仍跑旧代码（F25）。这里用子进程调用新的 wf.py 重启它，
    让新代码立刻生效，而不是等下一次升级。看板没在跑就什么都不做。"""
    from . import server
    i = server.info()
    if not i.get("pid"):
        return False
    port = i.get("port") or 8765
    try:
        subprocess.run([sys.executable, str(WF_HOME / "wf.py"), "serve", "--stop"], capture_output=True, timeout=30)
        subprocess.run([sys.executable, str(WF_HOME / "wf.py"), "serve", "--daemon", "--port", str(port)],
                       capture_output=True, timeout=30)
        return True
    except (subprocess.TimeoutExpired, OSError):
        return False


def self_rollback(tag: str) -> dict:
    if _dirty(WF_HOME, SELF_PROTECT):
        raise WfError("工具目录有未提交的改动，不能回滚。")
    if not tag.startswith("wf-v"):
        tag = "wf-v" + tag.lstrip("v")
    if git(["rev-parse", "-q", "--verify", f"refs/tags/{tag}"], WF_HOME, check=False).returncode != 0:
        tags = git(["tag", "-l", "wf-v*"], WF_HOME).stdout.split()
        raise WfError(f"没有标签 {tag}。可回滚到：{', '.join(tags) or '无'}")
    cur = tool_version()
    head, changed, removed = restore_tree(WF_HOME, tag, f"rollback to {tag} (from {cur})", SELF_PROTECT)
    restarted = _restart_board()
    return {"from": cur, "to": tool_version(), "commit": head, "changed": changed, "removed": removed, "board_restarted": restarted}


def self_versions() -> list:
    if not (WF_HOME / ".git").exists():
        return []
    r = git(["for-each-ref", "--sort=-creatordate", "--format=%(refname:short)\t%(creatordate:iso8601)", "refs/tags/wf-v*"],
            WF_HOME, check=False)
    return [dict(zip(("tag", "date"), l.split("\t"))) for l in r.stdout.splitlines()]


# ---------------- 工具文件清单与自检（§7：两套交付的收尾）----------------

def _tool_files() -> dict:
    import hashlib
    out = {}
    for f in sorted(WF_HOME.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(WF_HOME)
        parts = set(rel.parts)
        if parts & {".git", "local", "__pycache__"} or f.suffix == ".pyc" or str(rel) == "MANIFEST.json":
            continue
        out[str(rel).replace(os.sep, "/")] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def write_manifest() -> Path:
    p = WF_HOME / "MANIFEST.json"
    write_json(p, {"version": tool_version(), "files": _tool_files()})
    return p


def self_check() -> dict:
    """对照随版本发布的 MANIFEST.json：多出的文件（可能是旧交付的残留，或放错位置的通道适配器）、缺失的、被改动的。只列出，不删除。"""
    m = read_json(WF_HOME / "MANIFEST.json")
    if not m:
        return {"ok": False, "error": "没有 MANIFEST.json（1.4.0 之前的版本不带清单）"}
    now_files = _tool_files()
    want = m.get("files", {})
    extra = sorted(set(now_files) - set(want))
    missing = sorted(set(want) - set(now_files))
    changed = sorted(k for k in set(want) & set(now_files) if want[k] != now_files[k])
    return {"ok": not (extra or missing or changed), "version": m.get("version"), "extra": extra,
            "missing": missing, "changed": changed}
exit=0  耗时 0.0s
```

6) notify.py 全文。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ cat /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/notify.py
"""1.5.7：系统通知。wf tick 推进完之后，把各项目新出现的待决事项与提示（与 wf ask 同一来源）弹成 macOS 通知。
每件事只通知一次（记在 local/notified.json，与 wf ask 的已读记录互不影响）；事项消失后记录随之清除，再出现会再通知。
用户的诉求是看得到进度、不接受默默执行；执行端此前用本地胶水做这件事（collab-wf-dev 第 17 轮），现在收进工具。
关闭：环境变量 WF_NOTIFY=0，或 local/notify.json 写 {"enabled": false}。通知出任何问题都不影响 wf tick。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .core import LOCAL, now_iso, read_json, write_json

STORE = LOCAL / "notified.json"
CONFIG = LOCAL / "notify.json"


def enabled() -> bool:
    if os.environ.get("WF_NOTIFY", "").strip().lower() in ("0", "off", "false", "no"):
        return False
    if (read_json(CONFIG, {}) or {}).get("enabled") is False:
        return False
    return sys.platform == "darwin" and shutil.which("osascript") is not None


def _osa(s) -> str:
    """AppleScript 字符串字面量：转义反斜杠与双引号，换行改成空格。"""
    s = str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")
    return f'"{s}"'


def send(title, message, subtitle="") -> bool:
    script = f"display notification {_osa(str(message)[:220])} with title {_osa(str(title)[:80])}"
    if subtitle:
        script += f" subtitle {_osa(str(subtitle)[:80])}"
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def collect(inst) -> list:
    """一个项目当前应当让用户知道的事：待决事项（需要你决定）与提示（不需要决定）。"""
    from . import asks
    out = []
    for it in asks.pending(inst):
        out.append({"key": f"{inst.name}|{it['code']}", "subtitle": f"需要你决定：{it['title']}",
                    "message": f"{it['why']}（决定码 {it['code']}）"})
    for n in asks.notices(inst):
        out.append({"key": f"{inst.name}|{n['key']}", "subtitle": n["title"], "message": n["why"]})
    return out


def after_tick(projects, sender=None, store=None) -> list:
    """wf tick 之后调用：新出现的事各通知一次。返回本次发出的通知。"""
    try:
        if sender is None:
            if not enabled():
                return []
            sender = send
        store = store or STORE
        rec = read_json(store, {}) or {}
        seen = rec.get("keys") or {}
        live, sent = set(), []
        for inst in projects:
            try:
                items = collect(inst)
            except Exception:   # noqa: BLE001  某个项目读不出来，不影响其他项目
                continue
            for x in items:
                live.add(x["key"])
                if x["key"] in seen:
                    continue
                if sender(f"collab-wf · {inst.name}", x["message"], x["subtitle"]):
                    seen[x["key"]] = now_iso()
                    sent.append(x)
        names = {getattr(i, "name", "") for i in projects}
        for k in list(seen):
            if k.split("|", 1)[0] in names and k not in live:
                del seen[k]
        write_json(store, {"keys": seen, "updated": now_iso()})
        return sent
    except Exception:   # noqa: BLE001
        return []


def status_text() -> str:
    rec = read_json(STORE, {}) or {}
    on = enabled()
    why = "" if on else "（只支持 macOS 的 osascript；或被 WF_NOTIFY=0、local/notify.json 关闭）"
    return f"系统通知：{'开启' if on else '关闭'}{why}；已通知、仍未处理的事 {len(rec.get('keys') or {})} 件"
exit=0  耗时 0.0s
```

7) DRIVER.md 全文，放不下的部分续传。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
（c07 原文共 10020 字符，全文 sha256 前 12 位 3b385389af58，超出本条回报的预算，已分 1 片排入续传）

（续传队列中还有 1 片原文，会在后续回报中自动先发送；需要尽快拿到时，安排一轮不执行命令的回报即可）

③ 需要决策的点
无。

④ 需要注意（Attention）
- 自动处置 CONTEXT_FULL → await_user：当前对话已 20 轮、约 322064 字符，达到上限（policy：20 轮 / 0 字符）。恢复包已在 workflow/outbox/（bootstrap-20261010-190216.md，collab-wf-dev-v0.7-ed4d6ca.zip）：用户开新对话、上传 zip、发送说明消息后运行 wf conv new --note …，再 wf approve
- 决策（context）：停下，建议开新对话。理由：当前对话已 20 轮、约 322064 字符，达到上限（policy：20 轮 / 0 字符）。证据：bootstrap-20261010-190216.md，collab-wf-dev-v0.7-ed4d6ca.zip
- 决策（context）：开始新对话。理由：上下文到上限：上一个对话已 20 轮、约 322064 字符（policy max_conversation_turns=20）。已开新对话 a9c96593，发送恢复包。
- 决策（context）：开始新对话。理由：上下文到上限后开新对话：7da4233f-d409-495c-afa0-21440ad4da7d（接口创建，首条不带 effort 才发得出去）
- 清单第 1, 2, 3, 4, 5, 6, 7 项没有机器判据，结论需要 Claude 根据原文判断
- 驱动指令解析问题：验收 1 缺少命令编号或条件：验收 1：exit=0
- 驱动指令解析问题：验收 2 缺少命令编号或条件：验收 2：exit=0
- 驱动指令解析问题：验收 3 缺少命令编号或条件：验收 3：exit=0
- 驱动指令解析问题：验收 4 缺少命令编号或条件：验收 4：exit=0
- 驱动指令解析问题：验收 5 缺少命令编号或条件：验收 5：exit=0
- 驱动指令解析问题：验收 6 缺少命令编号或条件：验收 6：exit=0
- 驱动指令解析问题：验收 7 缺少命令编号或条件：验收 7：exit=0
- 当前对话已累计约 11810 字符、1 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

附：仓库指纹 65f6fad7a591346c（v0.7，4 个文件）
附：发送前额度 5 小时窗口 18.0%，7 天窗口 65.0%
