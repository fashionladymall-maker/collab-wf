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
import glob
import json
import os
import posixpath
import re
import shlex
from pathlib import Path

from .core import LOCAL, WF_HOME, Instance, WfError, load_registry, now_iso, read_json, write_json

# 1.5.9：停止底线、版本包内容检查、护栏命令与命令拼接的定义集中在 floor.py（改动它的工具版本不能自动安装），
# 这里导入，原有名字保持可用
from .floor import CODE_SUFFIXES, DIFF_FLOOR, GUARDRAIL, META, STOP_FLOOR  # noqa: F401
# 只读的 wf 命令：驱动器可以在白名单允许时自动执行。其余 wf 命令会改变工作流状态，
# 由驱动器内部再调用会与驱动器自身的状态写入互相覆盖（F21），所以驱动器永远不执行它们，只列为手动步骤。
WF_READONLY = re.compile(r"^\s*wf\s+(version|fingerprint|doctor|status|explain|step|help|audit|decisions|effort|projects|ledger|live-check|"
                         r"bridge-check|goals\s*$|goals\s+show|policy\s*$|policy\s+show|conv\s*$|conv\s+show|"
                         r"transport(\s+(check|test))?\s*$|schedule\s+(status|plist))(\s|$)")
# 命令链与重定向（META）：一律需要用户批准，定义在 floor.py
DEFAULT_PIPE_FILTERS = ["head", "tail", "grep", "wc", "sort", "uniq", "cut"]
# 1.5.8：用户批准的“驱动器自动安装工具版本”只放行这一种形式（policy.json 的 auto_self_update 为 true，且白名单放行）；
# 改动护栏文件的版本由 release.self_update(auto=True) 拒绝，另一个模型系列审查阻止时也拒绝
AUTO_SELF_UPDATE = re.compile(r"^\s*wf\s+self-update\s+--auto\s+workflow/outbox/collab-wf-\d+\.\d+\.\d+\.zip\s*$")

# 1.9.4（审查 concerns）：目标的检查命令要能证明是只读的。允许三类：wf 的只读命令（含 self-check、live-check）、
# 只读程序（sed 只许 -n、git 只许查看类子命令）、项目里受版本管理的 python 脚本（相对路径、不含 ..，随项目版本经过评审）；
# 不许命令链与重定向；管道只能接过滤命令
READONLY_PROGRAMS = re.compile(r"^\s*(cat|head|tail|wc|grep|sort|uniq|cut|ls|true|false|test|echo|"
                               r"sed\s+-n|git\s+(-C\s+\S+\s+)?(log|status|diff|show|ls-tree|rev-parse))(\s|$)")
PROJECT_SCRIPT = re.compile(r"^\s*(\S*/)?python3(\.\d+)?\s+(?![/~])(?!.*\.\.)[A-Za-z0-9_][A-Za-z0-9_./-]*\.py(\s|$)")


def readonly_check(cmd: str) -> bool:
    cmd = str(cmd or "")
    if not cmd.strip() or META.search(cmd.replace("2>&1", "")):
        return False
    segs = [x.strip() for x in cmd.split("|")]
    first = segs[0]
    if re.match(r"^\s*wf(\s|$)", first):
        ok = bool(WF_READONLY.match(first) or re.match(r"^\s*wf\s+self-check(\s+--probes)?\s*$", first))
    else:
        ok = bool(READONLY_PROGRAMS.match(first) or PROJECT_SCRIPT.match(first))
    return ok and all(x and x.split()[0] in DEFAULT_PIPE_FILTERS for x in segs[1:])


def sha256(p: Path):
    p = Path(p)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def approvals_path(inst: Instance) -> Path:
    return inst.wf / "approvals.json"


def approvals(inst: Instance) -> dict:
    return read_json(approvals_path(inst), {}) or {}


def approve(inst: Instance, kind: str, note: str, by: str = "user") -> dict:
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
    inst.event("approve", kind=kind, sha=rec["sha256"][:12], note=note, by=by)   # 1.6.5：执行端代决时 by=executor
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


def classify(cmd: str, pol: dict, *, root=None, others=None) -> dict:
    """命令分级：ok（可自动执行）/ user（需要用户）。返回原因与类别。
    B2-B1b：另判强门禁“破坏性或跨项目”（strong_gates），在白名单之前判定，宽 allow、auto_apply、auto_self_update
    都不能放行；root 为项目根（命令在这里执行）时才核实符号链接与 clean_dirs，others 为其他项目（缺省读注册表）。
    两参数调用与原有返回键不变，只新增 strong。"""
    reasons, cats = [], []
    for cat, pat in STOP_FLOOR + [(s.get("category", "项目规定"), s["pattern"]) for s in pol.get("stop", [])]:
        if re.search(pat, cmd, re.I):
            cats.append(cat)
            reasons.append(f"属于必须停下的“{cat}”类")
    strong = strong_gates(cmd, pol or {}, root=root, others=others)   # B2-B1b：原因文字不含用户标记，按现有路由先交执行端
    if strong:
        cats.append(STRONG_CATEGORY)
        for h in strong:
            reasons.append(f"属于强门禁“{STRONG_CATEGORY}”（{h['rule']}）：{h['detail']}；需要明确批准，宽白名单与自动开关不能放行")
    manual = False
    first = cmd.split("|")[0].strip()
    auto_su = (pol or {}).get("auto_self_update") is True and bool(AUTO_SELF_UPDATE.match(first))
    if auto_su:   # 1.5.8：只此一种 wf 写操作可由驱动器执行；仍须在白名单内（下面照常检查）
        cats.append("自动安装工具版本")
    elif re.match(r"^\s*wf(\s|$)", first) and not WF_READONLY.match(first) and not re.match(r"^\s*wf\s+self-check(\s+--probes)?\s*$", first):
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
    return {"cmd": cmd, "auto": not reasons, "reasons": reasons, "categories": cats, "manual": manual, "strong": strong}


# 1.6.1：不扩大权限的白名单与目标变更由驱动器自动批准（用户第 35 轮：把“需要执行端做的事”降到零）。
# 判定：新内容给驱动器的能力不多于已批准的旧内容。四道底线在 floor.py，与这里无关；扩大权限的变更照旧停下等用户。
NEUTRAL_KEYS = {"_说明", "first_poll_after_s", "poll_interval_s", "send_unknown_wait", "page_screenshots", "stale_reply_polls",
                "min_send_gap_s",   # 1.6.5：发送间隔只是时间参数
                "executor_task_timeout_s", "landed_confirm_polls", "landed_poll_s",   # 1.6.6：同为时间参数
                "landed_timeout_s", "landed_min_polls"}   # 1.6.8（P2-E）：落地核对按墙钟，同为时间参数
UPPER_LIMITS = ("max_rounds_per_day", "max_consecutive_failed_rounds", "max_rounds_without_goal_progress",
                "command_timeout_s", "max_leaf_refusals")
ZERO_IS_UNLIMITED = ("max_conversation_turns", "max_conversation_chars")
GRANTS = {"auto_apply": True, "auto_self_update": False, "auto_new_conversation": False}   # 键 → 缺省值（1.6.4：自动开新对话也是授权）
MUST_KEEP = ("stop", "diff_stop", "gates")
MAY_SHRINK = ("allow", "pipe_filters", "clean_dirs")   # B2-B1b：clean_dirs 新增算扩大，删除不算


def widening(old: dict, new: dict) -> list:
    """新白名单比旧的多给了哪些能力；空列表表示没有扩大（收紧或只改了时间类参数）。"""
    old, new = old or {}, new or {}
    why = []
    for k in MAY_SHRINK:
        dft = DEFAULT_PIPE_FILTERS if k == "pipe_filters" else []
        extra = [x for x in new.get(k, dft) if x not in old.get(k, dft)]
        if extra:
            why.append(f"{k} 新增：" + "、".join(str(x) for x in extra[:3]))
    for k in MUST_KEEP:
        keep = {json.dumps(x, sort_keys=True, ensure_ascii=False) for x in new.get(k, [])}
        gone = [x for x in old.get(k, []) if json.dumps(x, sort_keys=True, ensure_ascii=False) not in keep]
        if gone:
            why.append(f"{k} 去掉了 {len(gone)} 项")
    for k in UPPER_LIMITS:
        o, n = old.get(k), new.get(k)
        if n != o and not (isinstance(o, (int, float)) and isinstance(n, (int, float)) and n <= o):
            why.append(f"{k}：{o} → {n}")
    for k in ZERO_IS_UNLIMITED:
        inf = lambda v: float("inf") if not v else v   # noqa: E731  0 或缺省表示不限
        if inf(new.get(k)) > inf(old.get(k)):
            why.append(f"{k}：{old.get(k)} → {new.get(k)}")
    for k, dft in GRANTS.items():
        if new.get(k, dft) is True and old.get(k, dft) is not True:
            why.append(f"{k} 新开启")
    known = NEUTRAL_KEYS | set(UPPER_LIMITS) | set(ZERO_IS_UNLIMITED) | set(GRANTS) | set(MUST_KEEP) | set(MAY_SHRINK)
    for k in sorted((set(old) | set(new)) - known):
        if old.get(k) != new.get(k):
            why.append(f"{k} 有变化（不认识的键按扩大处理）")
    return why


def goals_widening(new: dict, pol: dict) -> list:
    """目标的检查命令由驱动器直接运行：只有每条都在已批准的白名单内可以自动执行，才不算扩大。"""
    from .goals import validate
    why = list(validate(new or {}))
    for x in (new or {}).get("goals", []):
        if x.get("kind") == "exec" and x.get("check") and not classify(x["check"], pol or {})["auto"]:
            why.append(f"{x.get('id')} 的检查命令不在已批准的白名单内：{x['check'][:80]}")
    return why


def change_widens(inst: Instance, kind: str, new: dict, pol: dict) -> list:
    """kind 为 policy 或 goals。当前内容未获批准时，任何改动都按扩大处理（没有可比的基准）。"""
    if not status(inst, kind)["approved"]:
        return [f"当前的 {kind}.json 未获批准，没有可比的基准"]
    if new is None:
        return [f"新的 {kind}.json 不是有效的 JSON"]
    return widening(read_json(inst.wf / "policy.json"), new) if kind == "policy" else goals_widening(new, pol)


def auto_approve(inst: Instance, kind: str, why: str) -> dict:
    """1.6.1：驱动器自动批准不扩大权限的变更（change_widens 为空时才调用）。记录与用户批准分开标明。"""
    p = inst.wf / f"{kind}.json"
    a = approvals(inst)
    rec = {"sha256": sha256(p), "ts": now_iso(), "note": f"驱动器自动批准（不扩大权限）：{why}", "by": "driver"}
    a.setdefault("history", []).append({"kind": kind, **rec})
    a[kind] = rec
    write_json(approvals_path(inst), a)
    inst.event("approve", kind=kind, sha=rec["sha256"][:12], note=rec["note"], by="driver")
    return rec


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
                wid = change_widens(inst, rel.split("/")[-1][:-5], read_json(f), pol)   # 1.6.1：不扩大权限的改动自动批准
                if wid:
                    out.append({"file": rel, "why": "改动了白名单或目标定义，且扩大了自动执行的范围（生效前需要用户批准）："
                                + "；".join(wid[:4])})
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


# ---------------- B2-B1b：强门禁“破坏性或跨项目”（词法判定） ----------------
# 范围：按 shell 词法把命令拆成简单命令，先做 bash 的大括号展开，再对已知程序按各自的参数语义区分读与写：
# 删除、写入、git 改写、全局服务、提权与内联代码。这是明确范围内的判定，不是 shell 沙箱：
# 任意脚本内部做什么、未列入规则的程序写到哪里、程序自身参数的语义（例如 --project）都判定不了；
# 无法解析或无法静态确定的一律按命中处理（需要明确批准），不当作“已证明安全”。
# 命中后 classify 不再自动执行；原因文字不含 notify.USER_MARKS，按现有路由先交执行端，底线类仍只认用户。
# 没有给出项目根（root）时只做词法判断：递归删除一律命中（clean_dirs 豁免要核实符号链接，必须有 root）。
# r2（常规独立审查 BLOCK-1..4）：通配符按当前实际匹配逐个解析（含后续路径段里的符号链接）；删除按目录项本身定位，
# 写入按跟随符号链接后的位置定位；各程序按参数语义解析连写的短参数（-xC 目录、-o文件、-P目录、-t目录）；
# 只有写目标按“写”核对，读源只按“读”核对（LOCAL 与另一个项目的 workflow/ 读取仍然拦）。
# r3（边界独立审查四组）：clean_dirs 的保护名按规范化后的路径段核对（./workflow 也不行）；受保护目录的比较不分大小写
# （macOS 默认文件系统不分大小写，宁可多拦）；env 的 -C/-u/-S 按带参短选项解析（含连写）；递归删除、移走目录、
# 递归改权限时，另查“受保护目录落在操作范围之内”；命令前的 shell 保留字（!、{、then、do 等）不改变后面命令的判定。
STRONG_CATEGORY = "破坏性或跨项目"
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish", "csh", "tcsh"}
_INLINE_LETTERS = {"perl": "eE", "ruby": "e", "osascript": "e", "php": "r"}   # 内联代码的短参数字母；shell 与 python 是 c
_NODE_LIKE = {"node", "nodejs", "bun", "deno"}
_PRIV = {"sudo", "su", "doas", "pkexec", "runuser"}
_WRAPPERS = {"env", "command", "builtin", "exec", "nohup", "time", "nice", "timeout", "stdbuf", "caffeinate", "busybox"}
_WRITE_ALL = {"rm", "rmdir", "unlink", "chmod", "chown", "chgrp", "touch", "mkdir", "truncate", "shred", "tee", "mv"}
_WRITE_DEST = {"cp", "ln", "install", "rsync", "scp"}
_WRITE_ANY = _WRITE_ALL | _WRITE_DEST | {"dd", "sed", "tar", "unzip", "curl", "wget", "perl"}
_DELETE = {"rm", "rmdir", "unlink"}
_GIT_READ = {"log", "status", "diff", "show", "ls-tree", "rev-parse", "ls-files", "cat-file", "describe", "blame",
             "shortlog", "grep", "ls-remote", "merge-base", "for-each-ref", "count-objects"}
_LAUNCHCTL_READ = {"list", "print", "print-cache", "print-disabled", "version", "help", "blame", "getenv", "error"}
_SYSTEMCTL_READ = {"status", "show", "list-units", "list-unit-files", "list-timers", "is-active", "is-enabled",
                   "is-failed", "cat", "help"}
_GLOBAL_KILL = {"kill", "pkill", "killall"}
_POWER = {"shutdown", "reboot", "halt", "poweroff"}
_PUNCT = set("();<>|&")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_GLOB = re.compile(r"[*?\[]")
_GLOB_CAP = 2000     # 一个通配符最多核对这么多个当前匹配；更多就按无法确定处理
_RESERVED = {"!", "{", "}", "(", ")", "then", "else", "elif", "do", "if", "while", "until", "coproc"}   # 命令前的保留字
_ENV_ARG = "CuS"     # env 带参数的短选项：-C 目录、-u 名字、-S 字符串（可连写：-C../x、-iC../x）
_BRACE_CAP = 256     # 一个词最多展开成这么多个

# 程序 → 参数语义：带参数的短选项字母、带参数的长选项、哪些选项的值是写目标、哪些选项让判定无法进行
_SPEC = {
    "cp": ("tS", {"--target-directory", "--suffix"}, {"-t", "--target-directory"}, set()),
    "mv": ("tS", {"--target-directory", "--suffix"}, {"-t", "--target-directory"}, set()),
    "ln": ("tS", {"--target-directory", "--suffix"}, {"-t", "--target-directory"}, set()),
    "install": ("tSmog", {"--target-directory", "--suffix", "--mode", "--owner", "--group"},
                {"-t", "--target-directory"}, set()),
    "rsync": ("eTB", {"--rsh", "--temp-dir", "--block-size", "--rsync-path", "--filter", "--exclude", "--include",
                      "--exclude-from", "--include-from", "--files-from", "--backup-dir", "--partial-dir", "--log-file",
                      "--password-file", "--compare-dest", "--copy-dest", "--link-dest"},
              {"-T", "--temp-dir", "--backup-dir", "--partial-dir", "--log-file"}, set()),
    "scp": ("iFPocljJS", set(), set(), {"-S"}),
    "touch": ("trd", {"--reference", "--date"}, set(), set()),
    "mkdir": ("m", {"--mode"}, set(), set()),
    "truncate": ("sr", {"--size", "--reference"}, set(), set()),
    "shred": ("ns", {"--iterations", "--size", "--random-source"}, set(), set()),
    "chmod": ("", {"--reference"}, set(), set()),
    "chown": ("", {"--reference", "--from"}, set(), set()),
    "chgrp": ("", {"--reference"}, set(), set()),
    "tee": ("", set(), set(), set()),
    "rm": ("", set(), set(), set()),
    "rmdir": ("", set(), set(), set()),
    "unlink": ("", set(), set(), set()),
    "unzip": ("dP", set(), {"-d"}, set()),
    "curl": ("AbcCdDeEFHKmoPQrtTuUwxXyYz",
             {"--output", "--cookie-jar", "--dump-header", "--output-dir", "--trace", "--trace-ascii", "--stderr",
              "--libcurl", "--etag-save", "--config", "--data", "--data-binary", "--data-raw", "--header", "--user",
              "--user-agent", "--referer", "--request", "--proxy", "--upload-file", "--cookie", "--form",
              "--max-time", "--range", "--write-out", "--url"},
             {"-o", "--output", "-c", "--cookie-jar", "-D", "--dump-header", "--output-dir", "--trace",
              "--trace-ascii", "--stderr", "--libcurl", "--etag-save"},
             {"-K", "--config"}),
    "wget": ("aBDeiIlOoPQRtTUwXA",
             {"--output-document", "--output-file", "--append-output", "--directory-prefix", "--save-cookies",
              "--warc-file", "--execute", "--config", "--input-file", "--base", "--tries", "--timeout", "--wait",
              "--quota", "--user-agent", "--level", "--accept", "--reject", "--domains", "--header"},
             {"-O", "--output-document", "-o", "--output-file", "-a", "--append-output", "-P", "--directory-prefix",
              "--save-cookies", "--warc-file"},
             {"-e", "--execute", "--config"}),
    "tar": ("bCfFgHIKLNTVXs",
            {"--directory", "--file", "--files-from", "--exclude-from", "--to-command", "--use-compress-program",
             "--checkpoint-action", "--listed-incremental", "--transform", "--xform", "--format", "--blocking-factor",
             "--label", "--newer", "--exclude", "--info-script", "--new-volume-script"},
            set(), {"-F", "-I", "--to-command", "--use-compress-program", "--info-script", "--new-volume-script"}),
}


def _strip_comments(cmd: str) -> str:
    """按 bash 的规则去掉注释：只有不在引号里、位于词首（前面是空白、行首或控制符）的 # 才开始注释，到行尾为止。
    词中间的 #（a#b、$#）与引号里的 # 都是普通字符。shlex 自己的注释规则与 bash 不同（词中间的 # 也会吞掉后文），所以不用它。"""
    out, q, i, n = [], None, 0, len(cmd)
    prev = " "
    while i < n:
        ch = cmd[i]
        if q is None and ch == "#" and (prev.isspace() or prev in _PUNCT):
            while i < n and cmd[i] not in "\r\n":
                i += 1
            continue
        out.append(ch)
        if ch == "\\" and q != "'" and i + 1 < n:
            out.append(cmd[i + 1])
            prev = cmd[i + 1]
            i += 2
            continue
        if q:
            if ch == q:
                q = None
        elif ch in ("'", '"'):
            q = ch
        prev = ch
        i += 1
    return "".join(out)


def _tokens(cmd: str) -> list:
    text = _strip_comments(cmd).replace("\r", "\n").replace("\n", " ; ")   # 换行在 bash 里分隔命令（MULTILINE 另记）
    lex = shlex.shlex(text, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def _simple_commands(toks: list) -> list:
    """按控制符拆成简单命令，返回 [(words, redirs)]；redirs 为 [("w"|"r", 目标)]。"""
    out, words, redirs, i = [], [], [], 0
    while i < len(toks):
        t = toks[i]
        if t and set(t) <= _PUNCT:
            if ">" in t or "<" in t:
                nxt = toks[i + 1] if i + 1 < len(toks) else ""
                if t in ("<<", "<<-", "<<<") or (t.endswith("&") and (nxt.isdigit() or nxt == "-")):
                    i += 2   # here-doc 的定界符、here-string、文件描述符复制：不是文件
                    continue
                redirs.append(("w" if ">" in t else "r", nxt))
                i += 2
                continue
            if words or redirs:
                out.append((words, redirs))
            words, redirs = [], []
            i += 1
            continue
        words.append(t)
        i += 1
    if words or redirs:
        out.append((words, redirs))
    return out


def _brace_split(inner: str):
    parts, depth, cur = [], 0, ""
    for ch in inner:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def _brace_seq(inner: str):
    m = re.fullmatch(r"(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?", inner)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        step = abs(int(m.group(3) or 1)) or 1
        if abs(b - a) // step + 1 > _BRACE_CAP:
            return None
        return [str(x) for x in (range(a, b + 1, step) if a <= b else range(a, b - 1, -step))]
    m = re.fullmatch(r"([A-Za-z])\.\.([A-Za-z])", inner)
    if m:
        a, b = ord(m.group(1)), ord(m.group(2))
        return [chr(x) for x in (range(a, b + 1) if a <= b else range(a, b - 1, -1))]
    return [] if ".." not in inner else None   # 有 .. 但不是能展开的序列：无法判定


def _braces(word: str):
    """bash 的大括号展开（在其他展开之前、按词进行）：返回展开后的词列表；超过上限或无法判定时返回 None。
    {a,b}、嵌套、{1..3}、{a..c} 都展开；没有逗号也不是序列的 {x} 照字面保留。"""
    depth, start = 0, -1
    for i, ch in enumerate(word):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                inner, pre, post = word[start + 1:i], word[:start], word[i + 1:]
                parts = _brace_split(inner)
                if len(parts) == 1:
                    seq = _brace_seq(inner)
                    if seq is None:
                        return None
                    if not seq:
                        rest = _braces(post)
                        return None if rest is None else [pre + "{" + inner + "}" + x for x in rest]
                    parts = seq
                out = []
                for p in parts:
                    sub = _braces(pre + p + post)
                    if sub is None:
                        return None
                    out += sub
                    if len(out) > _BRACE_CAP:
                        return None
                return out
    return [word]


def _within(p: str, base: str) -> bool:
    base = base.rstrip(os.sep) or os.sep
    return p == base or p.startswith(base + os.sep)


def _within_ci(p: str, base: str) -> bool:
    """r3：受保护目录的比较不分大小写。macOS 默认文件系统不分大小写，换个大小写就是同一个目录；
    这里只用于拦截一侧，宁可在区分大小写的系统上多拦。"""
    return _within(p.casefold(), base.casefold())


class _Ctx:
    def __init__(self, pol, root, others):
        self.pol = pol or {}
        self.root = os.path.realpath(str(root)) if root else None
        self.local = os.path.realpath(str(LOCAL))
        self.home = os.path.realpath(str(WF_HOME))
        self.home_local = os.path.realpath(str(WF_HOME / "local"))
        rows = []
        if others is None and self.root:
            try:
                rows = [(str(p.get("name") or ""), str(p.get("root") or ""))
                        for p in (load_registry() or {}).get("projects") or [] if isinstance(p, dict)]
            except Exception:   # noqa: BLE001 注册表读不了：只少了“另一个项目”这一项依据，不放宽别的判定
                rows = []
        elif others:
            for o in others:
                rows.append((str(o.get("name") or ""), str(o.get("root") or "")) if isinstance(o, dict) else ("", str(o)))
        self.others = []
        for name, r in rows:
            if not r:
                continue
            rr = os.path.realpath(r)
            if self.root and (rr == self.root or _within(self.root, rr)):
                continue   # 本项目自己，或包含本项目的上级目录
            self.others.append((name or os.path.basename(rr), rr))
        self.clean, self.clean_problems = _clean_real(self.pol, self.root)


def clean_dirs(pol: dict, root=None) -> tuple:
    """B2-B1b：policy 的 clean_dirs（允许递归删除的项目内目录）。返回 (有效目录的相对路径, 问题)。
    有效条件：字符串、相对路径、没有 .. 段、不是空或 .、不是 workflow 或 .git 及其下级；给了 root 时，
    该目录的 realpath 必须等于按字面拼出的路径（中间没有符号链接）、在根内且不等于根，也不在 LOCAL 或工具目录里。"""
    ok, problems = [], []
    raw = (pol or {}).get("clean_dirs", [])
    if not isinstance(raw, list):
        return [], [f"clean_dirs 不是列表：{str(raw)[:60]}"]
    rroot = os.path.realpath(str(root)) if root else None
    for e in raw:
        if not isinstance(e, str) or not e.strip():
            problems.append(f"clean_dirs 中 {e!r} 不是非空字符串，未生效")
            continue
        segs = [s for s in e.strip().split("/") if s]
        norm = posixpath.normpath(e.strip())
        nsegs = [s.casefold() for s in norm.split("/") if s]   # r3：按规范化后的路径段核对（./workflow、././workflow/ 都是 workflow）
        if e.strip().startswith(("/", "~")) or "\x00" in e or ".." in segs or norm in (".", "") or not nsegs \
                or nsegs[0] == "workflow" or ".git" in nsegs or _GLOB.search(e) or "{" in e:
            problems.append(f"clean_dirs 中 {e!r} 不是项目内的普通相对目录（绝对路径、~、..、通配、workflow、.git 都不行），未生效")
            continue
        if rroot:
            lit = os.path.normpath(os.path.join(rroot, norm))
            real = os.path.realpath(lit)
            loc = (os.path.realpath(str(LOCAL)), os.path.realpath(str(WF_HOME)))
            if real != lit or not _within(real, rroot) or real == rroot or any(_within(real, x) for x in loc):
                problems.append(f"clean_dirs 中 {e!r} 经符号链接或越出项目根（{real}），未生效")
                continue
        ok.append(norm)
    return ok, problems


def _clean_real(pol, rroot) -> tuple:
    if not rroot:
        return [], []
    ok, problems = clean_dirs(pol, rroot)
    return [os.path.realpath(os.path.join(rroot, x)) for x in ok], problems


def _locate(path: str, follow: bool) -> str:
    """绝对路径 → 实际作用的位置。跟随（写入、读取）：整条路径解析符号链接。不跟随（删除）：按目录项本身定位，
    只解析它的上级目录；以 / 或 /. 结尾、或末段是 . / .. 时，删除也会进到链接指向的目录里，所以照样跟随。"""
    trailing = path.endswith("/") or path.endswith("/.") or posixpath.basename(path.rstrip("/")) in (".", "..")
    # 必须先解析链接再处理 ..；提前 normpath 会把 link/.. 错当成链接所在的上级目录。
    if follow or trailing:
        return os.path.realpath(path)
    return os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))


def _paths(tok: str, c: _Ctx, follow: bool = True) -> tuple:
    """返回 (状态, 实际位置列表)。状态：inside、outside、unresolved、unknown（没有 root 的项目内相对路径）。
    通配符：没有 root 时只看前缀；给了 root 时还逐个核对当前的实际匹配（含后续路径段里的符号链接），
    匹配太多按无法确定处理。删除（follow=False）按目录项本身定位。"""
    if "$" in tok or "`" in tok:
        return "unresolved", []
    s = tok
    if s.startswith("~"):
        s = os.path.expanduser(s)
        if s.startswith("~"):
            return "unresolved", []
    cands, m = [], _GLOB.search(s)
    if m:
        if ".." in s[m.start():].split("/"):
            return "unresolved", []
        pre = s[:m.start()]
        base = pre if pre.endswith("/") or not pre else (posixpath.dirname(pre) or ".")
        cands.append((base or ".", True))   # 通配所在的目录本身
        if os.path.isabs(s) or c.root:
            pat = s if os.path.isabs(s) else os.path.join(c.root, s)
            try:
                hits = glob.glob(pat)
            except (OSError, ValueError, RecursionError):
                return "unresolved", []
            if len(hits) > _GLOB_CAP:
                return "unresolved", []
            cands += [(h, follow) for h in hits]
    else:
        cands.append((s, follow))
    states, locs = [], []
    for cand, fol in cands:
        if os.path.isabs(cand):
            loc = _locate(cand, fol)
        elif c.root:
            loc = _locate(os.path.join(c.root, cand) + ("/" if cand.endswith("/") else ""), fol)
        else:
            norm = posixpath.normpath(cand or ".")
            states.append("outside" if norm == ".." or norm.startswith("../") else "unknown")
            continue
        locs.append(loc)
        states.append("inside" if c.root and _within(loc, c.root) else "outside")
    for k in ("unresolved", "outside", "unknown"):
        if k in states:
            return k, locs
    return "inside", locs


def _contained(rp: str, c: _Ctx):
    """r3：递归操作（递归删除、移走目录、递归改权限）的范围 rp 之内是否有受保护的目录。"""
    if _within_ci(c.local, rp) or _within_ci(c.home_local, rp):
        return "工具的运行状态目录（LOCAL）"
    if _within_ci(c.home, rp) and not (c.root and _within_ci(c.home, c.root)):
        return "工具目录"
    for name, r in c.others:
        if _within_ci(r, rp):
            return f"另一个已注册项目（{name}）"
    return None


def _protected(tok: str, c: _Ctx, write: bool, follow: bool = True, contains: bool = False):
    st, locs = _paths(tok, c, follow)
    for rp in locs:
        if _within_ci(rp, c.local) or _within_ci(rp, c.home_local):
            return f"{tok} 落在工具的运行状态目录（LOCAL）"
        for name, r in c.others:
            if _within_ci(rp, os.path.join(r, "workflow")):
                return f"{tok} 落在另一个已注册项目（{name}）的 workflow/"
            if write and _within_ci(rp, r):
                return f"{tok} 写进另一个已注册项目（{name}）"
        if write and _within_ci(rp, c.home) and not (c.root and _within_ci(rp, c.root)):
            return f"{tok} 写进工具目录"
        if contains:
            inside = _contained(rp, c)
            if inside:
                return f"{tok} 的递归范围包含{inside}"
    return None


def _parse(args: list, short_arg: str = "", long_arg=frozenset(), optional_attached: str = "") -> tuple:
    """按参数语义解析：返回 ([(选项名, 值或 None)], 操作数)。支持连写的短参数（-xC 目录、-o文件、-sSLo 文件）、
    --长选项=值、带参数的长选项后跟值、-- 之后全部是操作数。optional_attached 里的字母只吃同一个词里剩下的部分。"""
    opts, ops_, i, raw = [], [], 0, False
    while i < len(args):
        a = args[i]
        if raw or a == "-" or not a.startswith("-"):
            ops_.append(a)
        elif a == "--":
            raw = True
        elif a.startswith("--"):
            name, sep, val = a.partition("=")
            if sep:
                opts.append((name, val))
            elif name in long_arg and i + 1 < len(args):
                opts.append((name, args[i + 1]))
                i += 1
            else:
                opts.append((name, None))
        else:
            body = a[1:]
            for j, ch in enumerate(body):
                rest = body[j + 1:]
                if ch in short_arg:
                    if rest:
                        opts.append(("-" + ch, rest))
                    elif i + 1 < len(args):
                        opts.append(("-" + ch, args[i + 1]))
                        i += 1
                    else:
                        opts.append(("-" + ch, None))
                    break
                if ch in optional_attached:
                    opts.append(("-" + ch, rest or None))
                    break
                opts.append(("-" + ch, None))
        i += 1
    return opts, ops_


def _operands(args: list) -> list:
    return _parse(args)[1]


def _short_letters(args: list) -> set:
    s = set()
    for a in args:
        if a == "--":
            break
        if a.startswith("-") and not a.startswith("--") and len(a) > 1:
            s |= set(a[1:])
    return s


def _check_target(tok: str, c: _Ctx, hit, rule="OUTSIDE_WRITE", follow: bool = True, contains: bool = False) -> None:
    st, _ = _paths(tok, c, follow)
    if st == "unresolved":
        hit(rule, f"写目标 {tok} 含变量、命令替换、越界或过多的通配，无法静态确定")
    elif st == "outside":
        hit(rule, f"写目标 {tok} 在项目根之外" + ("" if c.root else "（未提供项目根，按词法判定）"))
    why = _protected(tok, c, True, follow, contains)
    if why:
        hit("OTHER_PROJECT", why)


def _unwrap(prog: str, args: list, hit):
    """剥掉不改变语义的外壳命令，返回剩下的 words；返回 None 表示已按命中处理。"""
    i = 0
    if prog == "env":
        # r3：env 的选项按带参短选项解析：-C 目录、-u 名字、-S 字符串，值可以连写（-C../x、-uNAME）、可以跟在组合短选项后
        # （-iC../x）；--chdir、--unset、--split-string 可带 = 或单独跟值；-- 之后、第一个不是选项也不是 NAME=VALUE 的词起是命令
        chdir = split = False
        while i < len(args):
            a = args[i]
            if a == "--":
                i += 1
                break
            if a.startswith("--"):
                name = a.split("=", 1)[0]
                if name in ("--chdir", "--split-string"):
                    chdir, split = chdir or name == "--chdir", split or name == "--split-string"
                i += 1 + (1 if "=" not in a and name in ("--chdir", "--unset", "--split-string") else 0)
                continue
            if a.startswith("-") and a != "-":
                body, step = a[1:], 1
                for j, ch in enumerate(body):
                    if ch in _ENV_ARG:
                        chdir, split = chdir or ch == "C", split or ch == "S"
                        if not body[j + 1:]:
                            step = 2   # 值是下一个词
                        break
                i += step
                continue
            if _ASSIGN.match(a):
                i += 1
                continue
            break
        if split:
            hit("OPAQUE", "env -S 把字符串拆成命令执行，无法静态判定")
            return None
        if chdir:
            hit("CHDIR", "env -C 改变工作目录，之后的相对路径无法按项目根判定")
            return None
        return args[i:]
    if prog in ("command", "builtin", "nohup", "caffeinate", "busybox"):
        while i < len(args) and args[i].startswith("-"):
            if args[i] in ("-v", "-V") and prog == "command":
                return []   # command -v 只打印路径
            i += 1
        return args[i:]
    if prog == "exec":
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] == "-a" else 1
        return args[i:]
    if prog == "time":
        while i < len(args) and args[i].startswith("-"):
            i += 1
        return args[i:]
    if prog == "nice":
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in ("-n", "--adjustment") else 1
        return args[i:]
    if prog == "timeout":
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in ("-s", "--signal", "-k", "--kill-after") else 1
        return args[i + 1:]   # 跳过时长
    if prog == "stdbuf":
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in ("-i", "-o", "-e") else 1
        return args[i:]
    return args


def _git(args: list, c: _Ctx, hit) -> None:
    i, dirs = 0, []
    while i < len(args):
        a = args[i]
        if a == "-C" and i + 1 < len(args):
            dirs.append(args[i + 1])
            i += 2
        elif a in ("-c", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(args):
            if a != "-c":
                dirs.append(args[i + 1])
            i += 2
        elif a.startswith(("--git-dir=", "--work-tree=")):
            dirs.append(a.split("=", 1)[1])
            i += 1
        elif a.startswith("-"):
            i += 1
        else:
            break
    sub, rest = (args[i], args[i + 1:]) if i < len(args) else ("", [])
    for d in dirs:
        why = _protected(d, c, sub not in _GIT_READ)
        if why:
            hit("OTHER_PROJECT", f"git 指向 {why}")
        st, _ = _paths(d, c)
        if st in ("outside", "unresolved") and sub not in _GIT_READ:
            hit("GIT_OUTSIDE", f"git -C {d} {sub}：在项目根之外做非只读的 git 操作")
    letters = _short_letters(rest)
    if sub == "push":
        if letters & {"f", "d"} or any(a in ("--force", "--mirror", "--delete", "--prune") or a.startswith("--force")
                                       for a in rest):
            hit("GIT_DESTRUCTIVE", "git push 带强推、镜像或删除远端引用")
        for a in _operands(rest)[1:]:
            if a.startswith(("+", ":")):
                hit("GIT_DESTRUCTIVE", f"git push 的引用 {a} 是强推或删除")
    elif sub == "clean" and ("f" in letters or "--force" in rest):
        hit("GIT_DESTRUCTIVE", "git clean -f 删除未跟踪的文件")
    elif sub == "reset" and "--hard" in rest:
        hit("GIT_DESTRUCTIVE", "git reset --hard 丢弃工作区改动")
    elif sub == "branch" and (letters & {"d", "D"} or "--delete" in rest):
        hit("GIT_DESTRUCTIVE", "git branch 删除分支")
    elif sub == "tag" and ("d" in letters or "--delete" in rest):
        hit("GIT_DESTRUCTIVE", "git tag 删除标签")
    elif sub in ("filter-branch", "filter-repo"):
        hit("GIT_DESTRUCTIVE", f"git {sub} 改写历史")
    elif sub == "update-ref" and "d" in letters:
        hit("GIT_DESTRUCTIVE", "git update-ref -d 删除引用")
    elif sub == "reflog" and rest[:1] and rest[0] in ("expire", "delete"):
        hit("GIT_DESTRUCTIVE", f"git reflog {rest[0]} 删除引用日志")


def _tar(args: list, c: _Ctx, hit) -> None:
    short, longs, _w, opaque = _SPEC["tar"]
    if args and not args[0].startswith("-") and re.fullmatch(r"[A-Za-z]+", args[0]):
        # 旧式写法（tar xCf 目录 归档）：首个词是字母串，带参数的字母按顺序依次取后面的词
        letters, queue, opts = args[0], list(args[1:]), []
        for ch in letters:
            if ch in short and queue:
                opts.append(("-" + ch, queue.pop(0)))
            else:
                opts.append(("-" + ch, None))
        more, ops_ = _parse(queue, short, longs)
        opts += more
    else:
        opts, ops_ = _parse(args, short, longs)
    names = {n for n, _ in opts}
    for n, v in opts:
        if n in opaque or (n == "--checkpoint-action" and str(v or "").startswith("exec")):
            hit("OPAQUE", f"tar {n} 会执行外部命令或脚本，无法静态判定")
    extract = bool(names & {"-x", "--extract", "--get"})
    create = bool(names & {"-c", "-r", "-u", "-A", "--create", "--append", "--update", "--catenate",
                           "--concatenate", "--delete"})
    if extract and names & {"-s", "--transform", "--xform"}:
        hit("OUTSIDE_WRITE", "tar 解包时改写成员路径（-s/--transform），写目标无法静态确定")
    for n, v in opts:
        if v is None:
            continue
        if extract and n in ("-C", "--directory"):
            _check_target(v, c, hit)
        if create and n in ("-f", "--file"):
            _check_target(v, c, hit)
        if n in ("-g", "--listed-incremental"):
            _check_target(v, c, hit)


def _sed(args: list, c: _Ctx, hit) -> None:
    a2 = list(args)
    for i, a in enumerate(a2[:-1]):   # BSD（macOS）写法：-i 后面单独一个扩展名参数（常见 -i ''）
        if a == "-i" and (a2[i + 1] == "" or re.fullmatch(r"\.[A-Za-z0-9_~-]{0,12}", a2[i + 1])):
            a2[i + 1:i + 2] = []
            break
    opts, ops_ = _parse(a2, "efl", {"--expression", "--file", "--line-length"}, optional_attached="i")
    names = {n for n, _ in opts}
    if not (names & {"-i"} or any(n.startswith("--in-place") for n in names)):
        return
    script_given = bool(names & {"-e", "-f", "--expression", "--file"})
    for t in (ops_ if script_given else ops_[1:]):
        _check_target(t, c, hit)


def _generic(prog: str, args: list, c: _Ctx, hit) -> None:
    short, longs, wopts, opaque = _SPEC[prog]
    opts, ops_ = _parse(args, short, longs)
    for n, v in opts:
        if n in opaque:
            hit("OPAQUE", f"{prog} {n} 让目标或要执行的内容来自别处，无法静态判定")
        if n in wopts and v is not None:
            _check_target(v, c, hit)
    if prog in ("rsync", "scp") and any(re.match(r"^[^/]*:", a) for a in ops_):
        hit("OUTSIDE_WRITE", f"{prog} 涉及远端位置，超出项目根")
    if prog in ("cp", "ln", "install", "rsync", "scp"):
        has_t = any(n in wopts and n in ("-t", "--target-directory") for n, _ in opts)
        if prog == "install" and any(n in ("-d", "--directory") for n, _ in opts):
            for t in ops_:
                _check_target(t, c, hit)
        elif not has_t and len(ops_) >= 2:
            _check_target(ops_[-1], c, hit, contains=prog == "rsync" and any(n.startswith("--delete") for n, _ in opts))
        return
    if prog == "mv":
        has_t = any(n in ("-t", "--target-directory") for n, _ in opts)
        for t in (ops_ if has_t else ops_[:-1]):
            _check_target(t, c, hit, follow=False, contains=True)   # 源会被移走：按目录项本身定位；r3：源目录之内的受保护目录也随之移走
        if not has_t and ops_:
            _check_target(ops_[-1], c, hit)
        return
    if prog in ("curl", "wget", "unzip"):
        return
    recursive = prog in ("chmod", "chown", "chgrp") and any(n in ("-R", "--recursive") for n, _ in opts)
    for t in ops_:
        _check_target(t, c, hit, follow=prog not in _DELETE, contains=recursive)


def _words(words: list, c: _Ctx, hit, depth: int = 0) -> None:
    exp = []
    for w in words:
        e = _braces(w)
        if e is None:
            hit("OPAQUE", f"{w} 的大括号展开过多或无法判定")
            return
        exp += e
    words = exp
    while words and (_ASSIGN.match(words[0]) or words[0] in _RESERVED):   # r3：! rm …、{ rm …; }、then rm … 都还是 rm
        words = words[1:]
    if not words:
        return
    if depth > 6:
        hit("OPAQUE", "命令外壳嵌套过深，无法判定")
        return
    if "$" in words[0] or "`" in words[0] or _GLOB.search(words[0]):
        hit("OPAQUE", f"程序名 {words[0]} 来自变量、命令替换或通配，无法静态判定")
        return
    prog, args = posixpath.basename(words[0]), words[1:]
    if prog in _WRAPPERS:
        rest = _unwrap(prog, args, hit)
        if rest:
            _words(rest, c, hit, depth + 1)
        return
    if prog in _PRIV:
        hit("PRIV", f"{prog} 提权或换用户执行")
        return
    for a in args:   # 任何程序读 LOCAL、另一个项目的 workflow/ 都算跨项目；写目标由下面各程序按参数语义另行核对
        for tok in [a] + ([a.split("=", 1)[1]] if a.startswith("-") and "=" in a else []):
            why = _protected(tok, c, False)
            if why:
                hit("OTHER_PROJECT", why)
    letters = _short_letters(args)
    if prog == "eval":
        hit("OPAQUE", "eval 执行拼出来的字符串，无法静态判定")
        return
    is_py = re.match(r"^python[0-9.]*$", prog) is not None
    if prog in _SHELLS or is_py or prog in _INLINE_LETTERS or prog in _NODE_LIKE or prog in ("source", "."):
        inline = (prog in _SHELLS or is_py) and "c" in letters
        inline = inline or (prog in _INLINE_LETTERS and letters & set(_INLINE_LETTERS[prog]))
        inline = inline or (prog in _NODE_LIKE and any(a in ("-e", "-p", "--eval", "--print", "eval") for a in args))
        if inline:
            hit("OPAQUE", f"{prog} 执行内联代码，无法静态判定")
            return
        if is_py and ("m" in letters or "-m" in args):
            return
        if any(a in ("--version", "-V", "-version", "--help", "-h") for a in args):
            return
        ops_ = _operands(args)
        if not ops_ and prog not in ("source", "."):
            hit("OPAQUE", f"{prog} 没有脚本参数，从标准输入读入代码，无法静态判定")
        elif ops_:
            if ops_[0] == "-":
                hit("OPAQUE", f"{prog} 从标准输入读入代码，无法静态判定")
            else:
                st, _ = _paths(ops_[0], c)
                if st in ("outside", "unresolved"):
                    hit("OPAQUE", f"{prog} 运行项目根之外（或无法确定位置）的脚本 {ops_[0]}")
            if prog == "perl" and "i" in letters:
                for t in ops_[1:]:
                    _check_target(t, c, hit)
        return
    if prog == "xargs":
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("-I", "-L", "-n", "-P", "-s", "-E", "-d", "-a"):
                i += 2
            elif a.startswith("-"):
                i += 1
            else:
                break
        sub = args[i:]
        if sub:
            sp = posixpath.basename(sub[0])
            if sp in _WRITE_ANY or sp in _PRIV or sp in _SHELLS or sp in _GLOBAL_KILL or sp == "git":
                hit("XARGS_WRITE", f"xargs 把标准输入里的路径交给 {sp}，目标无法静态确定")
            _words(sub, c, hit, depth + 1)
        return
    if prog == "find":
        if "-delete" in args:
            hit("RECURSIVE_DELETE", "find -delete 批量删除")
        for i, a in enumerate(args):
            if a in ("-exec", "-execdir", "-ok", "-okdir") and i + 1 < len(args):
                sp = posixpath.basename(args[i + 1])
                if sp in _WRITE_ANY or sp in _SHELLS or sp in _PRIV:
                    hit("RECURSIVE_DELETE" if sp in ("rm", "rmdir", "unlink", "shred") else "XARGS_WRITE",
                        f"find {a} {sp}：对查找结果批量执行写操作")
        return
    if prog in _GLOBAL_KILL:
        if not (prog == "kill" and args and all(a in ("-l", "-L", "-0") or a.isdigit() for a in args)
                and any(a in ("-l", "-L", "-0") for a in args)):
            hit("GLOBAL_SERVICE", f"{prog} 结束进程")
        return
    if prog in _POWER:
        hit("GLOBAL_SERVICE", f"{prog} 关机或重启")
        return
    if prog == "launchctl":
        ops_ = _operands(args)
        if not ops_ or ops_[0] not in _LAUNCHCTL_READ:
            hit("GLOBAL_SERVICE", f"launchctl {ops_[0] if ops_ else ''}：改动系统服务")
        return
    if prog == "systemctl":
        ops_ = _operands(args)
        if ops_ and ops_[0] not in _SYSTEMCTL_READ:
            hit("GLOBAL_SERVICE", f"systemctl {ops_[0]}：改动系统服务")
        return
    if prog == "brew":
        ops_ = _operands(args)
        if ops_[:1] == ["services"] and len(ops_) > 1 and ops_[1] in ("start", "stop", "restart", "run", "kill", "cleanup"):
            hit("GLOBAL_SERVICE", f"brew services {ops_[1]}：改动系统服务")
        return
    if prog == "crontab":
        if not (args and all(a in ("-l", "-u") or not a.startswith("-") for a in args) and "-l" in args):
            hit("GLOBAL_SERVICE", "crontab 改动定时任务")
        return
    if prog in ("cd", "pushd"):
        ops_ = _operands(args)
        if ops_:
            st, _ = _paths(ops_[0], c)
            if st in ("outside", "unresolved"):
                hit("CHDIR", f"{prog} {ops_[0]}：切到项目根之外，之后的相对路径无法按项目根判定")
        return
    if prog == "git":
        _git(args, c, hit)
        return
    if prog == "rm":
        recursive = bool(letters & {"r", "R"}) or "--recursive" in args
        for t in _operands(args):
            if recursive:
                st, locs = _paths(t, c, follow=False)
                if not c.root:
                    hit("RECURSIVE_DELETE", f"rm -r {t}：未提供项目根，无法核实 clean_dirs 豁免（符号链接），按命中处理")
                elif st != "inside" or not locs or not all(any(_within(rp, d) for d in c.clean) for rp in locs):
                    hit("RECURSIVE_DELETE", f"rm -r {t}：不能证明全部实际目标都在有效的 clean_dirs 目录内"
                        + (f"（{'；'.join(c.clean_problems)[:120]}）" if c.clean_problems else ""))
            _check_target(t, c, hit, follow=False, contains=recursive)
        return
    if prog in ("make", "gmake"):   # r3：-C 改变工作目录并执行那里的 makefile，-f 指定 makefile：在根外就是执行根外的代码
        opts, _ops = _parse(args, "CfI", {"--directory", "--file", "--makefile", "--include-dir"})
        for n, v in opts:
            if n in ("-C", "--directory", "-f", "--file", "--makefile") and v is not None:
                st, _ = _paths(v, c)
                if st in ("outside", "unresolved"):
                    hit("OPAQUE", f"{prog} {n} {v}：执行项目根之外（或无法确定位置）的 makefile")
        return
    if prog == "tar":
        _tar(args, c, hit)
        return
    if prog == "sed":
        _sed(args, c, hit)
        return
    if prog in _SPEC:
        _generic(prog, args, c, hit)
        return
    if prog == "dd":
        for a in args:
            if a.startswith("of="):
                _check_target(a[3:], c, hit)
        return


def strong_gates(cmd: str, pol: dict, *, root=None, others=None) -> list:
    """B2-B1b：强门禁“破坏性或跨项目”的词法判定。返回 [{"rule", "detail"}]，空列表表示没有命中。
    规则：MULTILINE、UNPARSEABLE、OPAQUE、PRIV、RECURSIVE_DELETE、OUTSIDE_WRITE、OTHER_PROJECT、GIT_DESTRUCTIVE、
    GIT_OUTSIDE、GLOBAL_SERVICE、XARGS_WRITE、CHDIR。只读：给了 root 且没有给 others 时只读注册表；
    给了 root 时会列出通配符的当前匹配（只读目录，不执行命令）。"""
    cmd = str(cmd or "")
    hits, seen = [], set()

    def hit(rule, detail):
        k = (rule, detail)
        if k not in seen:
            seen.add(k)
            hits.append({"rule": rule, "detail": str(detail)[:240]})
    if "\n" in cmd or "\r" in cmd:
        hit("MULTILINE", "命令里有换行：bash -c 会把它当成多条命令执行")
    if "$(" in cmd or "`" in cmd:
        hit("OPAQUE", "命令替换（$( ) 或反引号）的内容无法静态判定")
    try:
        toks = _tokens(cmd)
    except ValueError as e:
        hit("UNPARSEABLE", f"无法按 shell 词法解析（{e}），按命中处理")
        return hits
    c = _Ctx(pol, root, others)
    for words, redirs in _simple_commands(toks):
        for kind, target in redirs:
            if kind == "w":
                _check_target(target, c, hit)
            else:
                why = _protected(target, c, False)
                if why:
                    hit("OTHER_PROJECT", why)
        _words(words, c, hit)
    return hits
