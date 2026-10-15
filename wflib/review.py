"""独立审查基础设施（review/1）。B2b-1/2/3 为 release apply/update 接入冻结包、目标安装锁、
最终核验与消费/事务记录；安全回滚基线仍待 B2b-4，A3/A4 另行接线。

一、公开接口（冻结）
  prepare(target, zip_path, *, kind, author=None) -> request
      kind："tool"（target 为 None 或工具目录）或 "project"（target 为 Instance 或项目根目录）。
      把 zip 冻结为 LOCAL/releases/<sha256>.zip（只读，同一份字节只存一份），解析出完整的安装语义，生成完整 diff，
      持久化 LOCAL/reviews/requests/<request_id>.json（写后不再改动）。author 是 LOCAL/lanes.json 里登记的作者车道名。
      包不合格（路径穿越、重复或大小写冲突路径、文件与目录冲突、符号链接或特殊类型、加密、CRC 错、多个顶层目录、
      RELEASE.json 损坏）时抛 WfError，不留请求。
  run(request_id, *, timeout_s=None) -> decision
      先核快照 hash 与当前目标材料；再调用宿主配置的审查程序（transport.json 的 review，新协议参数见 _runner_cmd），
      宿主自己捕获 stdout/stderr 并保存、记 hash；结果合法时再调用宿主固定 sha256 的核验器（review_verifier）。
  status(target, request_id=None, *, content_id=None, phase="review", now=None) -> decision
      每次都从宿主记录与原文件重算（重算 hash、重新解析结果与核验输出），不信任调用方给的 dict 或缓存字段。
      phase="install" 时 target 必填，并额外核快照。
  final_check(request_id, *, target, extracted_dir=None, now=None) -> decision
      供安装锁内调用（测试前后都可以调）：重核快照、目标 base/配置/rules、来源与结论；给出 extracted_dir 时
      逐个核解包目录（字节与权限；只容忍 __pycache__ 与 *.pyc）。
  consume(request_id, *, target, extracted_dir, now=None, install_context=None) -> decision
      在真正复制文件之前调用：final_check 不通过时抛 WfError；通过时写安装开始记录（第四个时刻 install_started）。
      release 在目标锁内传 install_context 关联事务与锁；此短锁不覆盖复制/提交，事务 completed 由消费者另核。
  cancel(request_id) -> int          把该请求仍在进行的尝试记为 cancelled，返回条数。
  read_package(zip_path, *, kind=None, protect=None) -> dict    唯一的包解析与安装语义判定（B2b 必须改用它）。
  extract_snapshot(request_id, dest) -> Path   从冻结快照（先核 hash）按解析结果写出文件与权限，返回包的根目录。
  verify_extracted(request_id, root) -> list    解包目录与包内容逐项核对，返回问题列表。
  load_install_request(request_id, *, target, kind, zip_path=None) -> request
      只读适配器：核请求、快照、入口 kind/目标与当前全部材料。zip_path 若提供，只作 raw SHA 一致性断言；
      不作为解包来源。返回的 request 不是安装许可，消费者仍须最终核验与 consume。

二、decision 的键（冻结）
  schema（review-decision/1）、request_id、content_id、phase、state、verdict、allowed、reasons（list）、
  provenance{level, synthetic, model_claimed, model_verified, reviewer_identity, verifier_sha256}、
  times{review_started, review_completed, result_registered, install_started}、expires_at、attempt_id、diagnostics。
  state ∈ none | pending | completed | cancelled | superseded | timeout | late（请求最近一次尝试的运行状态）。
  verdict ∈ None | pass | concerns | block（block 粘性：同一 content 只要登记过 block，之后任何 pass 都不改变）。
  provenance.level ∈ platform_verified | host_attested | local_invocation | unknown | unavailable；
  只有 platform_verified 才可能 allowed；host_attested（含 HMAC 宿主背书）只说明宿主背书，不证明平台模型。
  allowed 只在：结论 pass/concerns、尝试 completed 且审查程序退出码为 0、全部原文与送审材料（请求、diff）hash 复核一致、
  核验器此刻仍按宿主当前配置可用且字节与固定 sha256 一致（配置被删、被改或核验器被换都失效）、核验输出回显
  （request/content/nonce/attempt/原文 hash/结果 hash）一致、level=platform_verified、审查者会话身份已证且不与
  当前请求作者（以及该次审查所属请求的作者）按 lanes.json 当前登记的会话重叠、7 天内、规则来源存在且未变、
  目标材料（含工作区实际内容、权限与删除状态）与 diff 重算一致、没有登记过 block 时为真。任何一项未知都不通过。
  consume 在与登记审查结果相同的锁内重算并写安装记录：登记 block 与最终消费按锁的先后线性排列。

  材料闭包（content_id 与安装前复核都按实际字节重算，与 git 是否跟踪、是否被忽略无关）：
  - 安装会写入的每个路径的实际状态；RELEASE.json delete 的每个路径及其完整递归闭包（与 release._delete 相同：
    目录整棵删除，含被忽略、未跟踪的文件与空目录；逐项记类型、权限、内容 hash、链接目标，链接不跟随）；
  - 版本库目标：HEAD commit/tree，加上 git status 的每个改动的实际状态；git status 有任何告警即按未知处理；
  - 工具目标：整个工具目录的实际清单（与 release._tool_files 同口径排除 .git、local/、__pycache__、*.pyc；
    MANIFEST.json 也纳入）；没有版本库的目标：整个目录的实际清单（受保护路径与 .git 除外）；
  - 安装门禁实际读取的控制文件：项目的 workflow/wf.json、policy.json、goals.json 全部字节与 approvals.json 中
    policy/goals 两条批准记录（driver 的 policy.load/status 与 _auto_approve_neutral 只读这两条）；工具的 VERSION 与
    MANIFEST.json（self_update 比较版本、self_check 对清单）。
  - 不纳入（安装链不读、或只是运行记录）：workflow/state.json、*.jsonl、usage.json、goals_state.json、evidence/、
    outbox/、approvals.json 的 confirmations/not_applicable/history、project_hook.py（只有看板读）、LOCAL 下除核验器
    配置与 lanes.json 以外的文件（这两项每次判定时按当前内容重核）。
  任何递归枚举遇到列不了的目录、读不了的文件都记为 unknown，结论不通过；不会静默跳过。

三、信任边界（如实说明）
  信任根是宿主：当前已安装的本模块、LOCAL/transport.json 里明确配置并固定 sha256 的外部核验器、LOCAL/lanes.json。
  候选包里的任何程序、配置、review.json、结果自带的 provenance/raw_path/verified 一律不作为依据（只记诊断）。
  威胁模型是防误用与自报伪装；不声称能防同一用户恶意改写宿主文件或核验器，测试替身也不声称 OS 隔离。
  测试替身核验器标 synthetic，决定里如实带出，不能当作实景通过的证据。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import unicodedata
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import core
from .core import Instance, WfError

SCHEMA_REQUEST = "review-request/1"
SCHEMA_MATERIAL = "review-material/1"
SCHEMA_ATTEMPT = "review-attempt/1"
SCHEMA_DECISION = "review-decision/1"
STATES = ("none", "pending", "completed", "cancelled", "superseded", "timeout", "late")
VERDICTS = ("pass", "concerns", "block")
LEVELS = ("platform_verified", "host_attested", "local_invocation", "unknown", "unavailable")
REUSE_DAYS = 7
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
TOOL_PROTECT = [".git/", "local/"]     # release.SELF_PROTECT 直接引用此统一规则
TOOL_IDENTITY = "collab-wf"
RULES_PATH = "docs/REVIEW.md"

_RID = re.compile(r"^rq-[0-9a-f]{32}$")
_AID = re.compile(r"^at-[0-9a-f]{32}$")
_CID = re.compile(r"^[0-9a-f]{64}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_SELF_CLAIM_KEYS = ("provenance", "raw_path", "verified", "model_verified", "reviewer_identity", "level")


# ---------------- 基础工具 ----------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat()


def _parse_iso(s):
    try:
        t = datetime.fromisoformat(str(s))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _tool_home() -> Path:
    return Path(core.WF_HOME)


def _local() -> Path:
    return Path(core.LOCAL)


def _reviews() -> Path:
    return _local() / "reviews"


def _releases() -> Path:
    return _local() / "releases"


def _check_id(rx, value, label) -> str:
    if not isinstance(value, str) or not rx.fullmatch(value):
        raise WfError(f"{label} 格式不对：{str(value)[:80]!r}")
    return value


def _read_bytes(p: Path):
    try:
        return Path(p).read_bytes()
    except OSError:
        return None


def _write_new(p: Path, data: bytes, mode: int = 0o644) -> None:
    """只新建、不覆盖；写到一半失败时删掉本次新建的文件。"""
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except BaseException:
        try:
            os.unlink(p)
        except OSError:
            pass
        raise


def _write_atomic(p: Path, data: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{secrets.token_hex(6)}.tmp")
    _write_new(tmp, data)
    os.replace(tmp, p)


class _Lock:
    """宿主状态（尝试状态、按内容的登记）的短临界区。"""

    def __enter__(self):
        import fcntl
        p = _reviews() / ".lock"
        p.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(p, "a+")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        import fcntl
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


# ---------------- 包解析（唯一的安装语义） ----------------

def _norm_member(name: str) -> str:
    """校验 zip 成员名并返回规范化路径；不合格抛 WfError。"""
    if not name or "\x00" in name or "\\" in name:
        raise WfError(f"zip 成员名不合格：{name!r}")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise WfError(f"zip 中有绝对路径：{name!r}")
    body = name[:-1] if name.endswith("/") else name
    parts = body.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise WfError(f"zip 中有越界或不规范的路径：{name!r}")
    return body


def _fold(p: str) -> str:
    return unicodedata.normalize("NFC", p).casefold()


def _hier_conflicts(entries) -> None:
    """按大小写不敏感、Unicode 规范化后的层级检查冲突（macOS 默认文件系统即如此）：同一目录的不同写法、
    文件与（任意写法的）目录同名，一律在冻结时拒绝，不等到安装时报错。"""
    dirs, files = {}, {}
    for e in entries:
        parts = e["path"].split("/")
        for i in range(1, len(parts)):
            pre = "/".join(parts[:i])
            dirs.setdefault(_fold(pre), set()).add(pre)
        if e["type"] == "dir":
            dirs.setdefault(_fold(e["path"]), set()).add(e["path"])
        else:
            files[_fold(e["path"])] = e["path"]
    for k, forms in sorted(dirs.items()):
        if len(forms) > 1:
            raise WfError(f"zip 中同一目录有不同写法（大小写或 Unicode 规范化后相同）：{sorted(forms)}")
        if k in files:
            raise WfError(f"zip 中文件与目录冲突（大小写或 Unicode 规范化后相同）：{files[k]} / {sorted(forms)[0]}")


def _installed(rel: str, kind, protect) -> bool:
    """与 release._copy_tree 相同的复制规则：根目录的 RELEASE.json、__pycache__、*.pyc、受保护路径不复制。"""
    if rel == "RELEASE.json" or rel.endswith(".pyc") or "__pycache__" in rel.split("/"):
        return False
    return not core.matches_protect(rel, protect or [])


def read_package(zip_path, *, kind=None, protect=None) -> dict:
    """解析一个版本包并给出完整的安装语义。返回：
    {sha256, bytes, top, entries:[{path, type: file|dir, mode, size, sha256, installed}], ignored:[成员名],
     release:{json_sha256, delete, wf_config, keys} 或 None, data:{path: 字节}（data 不进入任何身份或请求文件）}。
    规则（任何一条不满足就抛 WfError）：只有一个顶层目录（__MACOSX 记为 ignored）；成员名不得绝对、越界、含反斜杠；
    不得重复，规范化（NFC + casefold）后不得冲突；文件路径不得同时是另一成员的上级目录；不得是符号链接或特殊文件；
    不得加密；CRC 必须正确；总大小有上限；RELEASE.json 若存在必须是 JSON 对象、delete 为字符串列表。"""
    raw = _read_bytes(Path(zip_path))
    if raw is None:
        raise WfError(f"读不了版本包：{zip_path}")
    if len(raw) > MAX_PACKAGE_BYTES:
        raise WfError("版本包超过大小上限")
    import io
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise WfError(f"不是有效的 zip 文件：{zip_path}")
    infos = zf.infolist()
    seen, folded, tops, entries, ignored, data = set(), {}, set(), [], [], {}
    total = 0
    for info in infos:
        body = _norm_member(info.filename)
        if body.split("/")[0] == "__MACOSX":
            ignored.append(info.filename)
            continue
        if info.flag_bits & 0x1:
            raise WfError(f"zip 成员加密：{info.filename}")
        if body in seen:
            raise WfError(f"zip 中有重复路径：{body}")
        seen.add(body)
        key = unicodedata.normalize("NFC", body).casefold()
        if key in folded and folded[key] != body:
            raise WfError(f"zip 中有大小写或 Unicode 规范化后冲突的路径：{folded[key]} / {body}")
        folded[key] = body
        attr = (info.external_attr >> 16) & 0xFFFF
        ftype = stat.S_IFMT(attr)
        is_dir = info.filename.endswith("/")
        if ftype == stat.S_IFLNK:
            raise WfError(f"zip 中有符号链接（安装时会被写成普通文件，审查与安装语义不一致）：{body}")
        if ftype not in (0, stat.S_IFREG, stat.S_IFDIR) or (ftype == stat.S_IFDIR and not is_dir):
            raise WfError(f"zip 中有特殊类型的成员：{body}")
        tops.add(body.split("/")[0])
        if is_dir:
            entries.append({"path": body, "type": "dir"})
            continue
        try:
            b = zf.read(info)
        except (zipfile.BadZipFile, RuntimeError, OSError) as e:
            raise WfError(f"zip 成员损坏：{body}：{e}")
        total += len(b)
        if total > MAX_PACKAGE_BYTES:
            raise WfError("版本包解压后超过大小上限")
        if attr & 0o7000:
            raise WfError(f"zip 成员带特殊权限位（setuid/setgid/sticky）：{body}")
        if "/" not in body:
            raise WfError(f"zip 顶层必须是目录，不能是文件：{body}")
        mode = attr & 0o777 if attr & 0o777 else None
        entries.append({"path": body, "type": "file", "mode": mode, "size": len(b), "sha256": _sha(b)})
        data[body] = b
    if len(tops) != 1:
        raise WfError(f"zip 应该只有一个顶层目录，实际有：{sorted(tops)}")
    _hier_conflicts(entries)
    top = tops.pop()
    files = {e["path"] for e in entries if e["type"] == "file"}
    for f in files:
        prefix = f + "/"
        if any(o.startswith(prefix) for o in seen):
            raise WfError(f"zip 中文件与目录冲突：{f}")
    out, rel_data = [], {}
    for e in entries:
        if e["path"] == top:
            continue
        if not e["path"].startswith(top + "/"):
            raise WfError(f"zip 成员不在顶层目录下：{e['path']}")
        rel = e["path"][len(top) + 1:]
        if any(rel == p or rel.startswith(p + "/")
               for p in (".git/wf-install", "local/install", "workflow/evidence/install")):
            raise WfError(f"版本包不得携带宿主安装事务元数据：{rel}")
        x = dict(e, path=rel)
        if e["type"] == "file":
            x["installed"] = _installed(rel, kind, protect)
            rel_data[rel] = data[e["path"]]
        out.append(x)
    release = None
    if "RELEASE.json" in rel_data:
        try:
            man = json.loads(rel_data["RELEASE.json"].decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise WfError("RELEASE.json 不是有效的 JSON")
        if not isinstance(man, dict):
            raise WfError("RELEASE.json 必须是 JSON 对象")
        delete = man.get("delete") or []
        if not isinstance(delete, list) or not all(isinstance(x, str) for x in delete):
            raise WfError("RELEASE.json 的 delete 必须是字符串列表")
        for d in delete:
            _norm_member(d)
        wf_config = man.get("wf_config") or {}
        if not isinstance(wf_config, dict):
            raise WfError("RELEASE.json 的 wf_config 必须是对象")
        release = {"json_sha256": _sha(rel_data["RELEASE.json"]), "delete": sorted(delete),
                   "wf_config": wf_config, "keys": sorted(man)}
    out.sort(key=lambda x: (x["path"], x["type"]))
    return {"sha256": _sha(raw), "bytes": len(raw), "top": top, "entries": out, "ignored": sorted(ignored),
            "release": release, "data": rel_data}


# ---------------- 目标与材料 ----------------

def _git_full(args, cwd) -> tuple:
    """返回 (stdout, stderr)；退出码非 0 抛 WfError。"""
    r = subprocess.run(["git"] + list(args), cwd=str(cwd), capture_output=True)
    if r.returncode != 0:
        raise WfError(f"git {' '.join(args[:2])} 失败：{r.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return r.stdout.decode("utf-8", "surrogateescape"), r.stderr.decode("utf-8", "replace")


def _git(args, cwd) -> str:
    return _git_full(args, cwd)[0]


def _target(target, kind) -> dict:
    """规范化目标：{kind, root, scope, project:{name, created} 或 None, tool:{identity} 或 None, protect, config_sha256}。"""
    if kind == "tool":
        root = Path(os.path.realpath(target if target is not None else _tool_home()))
        if not (root / "VERSION").is_file():
            raise WfError(f"{root} 不是工具目录（没有 VERSION）")
        return {"kind": "tool", "root": str(root), "scope": "tool", "project": None,
                "tool": {"identity": TOOL_IDENTITY}, "protect": list(TOOL_PROTECT),
                "config_sha256": _sha(_canon({"protect": TOOL_PROTECT}))}
    if kind == "project":
        inst = target if isinstance(target, Instance) else Instance(Path(target))
        root = Path(os.path.realpath(inst.root))
        raw = _read_bytes(root / "workflow" / "wf.json")
        try:
            cfg = json.loads(raw.decode("utf-8")) if raw is not None else None
        except (UnicodeDecodeError, ValueError):
            cfg = None
        if not isinstance(cfg, dict) or not cfg.get("project"):
            raise WfError(f"{root} 没有有效的 workflow/wf.json，无法确定项目身份")
        protect = list(core.DEFAULT_PROTECT) + [str(x) for x in (cfg.get("protect") or [])]
        return {"kind": "project", "root": str(root), "scope": f"project:{cfg['project']}",
                "project": {"name": str(cfg["project"]), "created": str(cfg.get("created") or "")}, "tool": None,
                "protect": protect, "config_sha256": _sha(raw)}
    raise WfError(f"kind 只能是 tool 或 project，不是 {kind!r}")


def _state_of(root: Path, rel: str) -> tuple:
    """不跟随链接地读取目标里一个路径的实际状态。返回 (状态, 字节或 None)。
    类型：file（sha256/size/mode）、absent、symlink（target）、dir、special、unsafe（上级不是普通目录）、unknown（读不了）。"""
    parts = rel.split("/")
    cur = Path(root)
    try:
        for part in parts[:-1]:
            cur = cur / part
            st = os.lstat(cur)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                return {"path": rel, "type": "unsafe", "why": f"上级 {part} 不是普通目录"}, None
        p = cur / parts[-1]
        st = os.lstat(p)
        if stat.S_ISLNK(st.st_mode):
            return {"path": rel, "type": "symlink", "target": os.readlink(p)}, None
        if stat.S_ISDIR(st.st_mode):
            return {"path": rel, "type": "dir", "mode": st.st_mode & 0o777}, None
        if not stat.S_ISREG(st.st_mode):
            return {"path": rel, "type": "special"}, None
        fd = os.open(str(p), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
        with os.fdopen(fd, "rb") as f:
            b = f.read()
            m = os.fstat(f.fileno()).st_mode
        return {"path": rel, "type": "file", "sha256": _sha(b), "size": len(b), "mode": m & 0o777}, b
    except FileNotFoundError:
        return {"path": rel, "type": "absent"}, None
    except NotADirectoryError:
        return {"path": rel, "type": "unsafe", "why": "上级不是目录"}, None
    except OSError as e:
        return {"path": rel, "type": "unknown", "why": str(e)[:160]}, None


def _source_state(root: Path, rel: str) -> tuple:
    """按实际读取语义绑定控制/工具源。跟随父目录和叶链接，记录完整链接链及最终文件；
    悬空链接、循环、非普通文件和读取失败一律 unknown。删除状态仍用 _state_of，不跟随链接。"""
    parts = (Path(root).absolute() / rel).parts
    cur = Path(parts[0])
    pending = [(part, False) for part in parts[1:]]
    required = False
    links = []
    try:
        while pending:
            part, required = pending.pop(0)
            cur = cur / part
            st = os.lstat(cur)
            if stat.S_ISLNK(st.st_mode):
                target = os.readlink(cur)
                links.append({"path": str(cur), "target": target})
                if len(links) > 40:
                    raise OSError("链接链超过 40 层或循环")
                # 不用 Path(target).parts：它会丢掉尾部 / 或 /. 的“必须是目录”语义。
                target_parts = [part for part in target.split("/") if part]
                if target.endswith("/"):
                    target_parts.append(".")
                cur = Path("/") if target.startswith("/") else cur.parent
                pending = [(part, True) for part in target_parts] + pending
            elif pending and not stat.S_ISDIR(st.st_mode):
                raise OSError("源路径上级不是目录")
        if not stat.S_ISREG(st.st_mode):
            raise OSError("实际读取源不是普通文件")
        fd = os.open(str(cur), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise OSError("实际读取源不是普通文件")
            b = f.read()
            mode = os.fstat(f.fileno()).st_mode & 0o777
        out = {"path": rel, "type": "file", "sha256": _sha(b), "size": len(b), "mode": mode}
        if links:
            out["source"] = {"links": links, "resolved": str(cur)}
        return out, b
    except FileNotFoundError as e:
        if not required:   # 缺的是原路径的可选组件，非链接本身所指的目标（悬空）
            out = {"path": rel, "type": "absent"}
            if links:
                out["source"] = {"links": links, "resolved": str(cur.joinpath(*(p for p, _ in pending)))}
            return out, None
        return {"path": rel, "type": "unknown", "why": str(e)[:160], "source": {"links": links}}, None
    except OSError as e:
        return {"path": rel, "type": "unknown", "why": str(e)[:160], "source": {"links": links}}, None


_scandir = os.scandir   # 单独成名字：测试可以注入列目录失败（root 用户不受 chmod 000 限制时用）


def _walk_states(root: Path, rel: str = "", exclude=None) -> list:
    """不跟随链接地递归列出 rel（目录，空串为根）之下的全部后代，含空目录；每项给出实际状态（目录记权限）。
    exclude(rel, is_dir) 为真的项连同其下全部跳过。列不了的目录、lstat 失败的项一律记 unknown，不静默跳过。"""
    out = []

    def rec(rel_dir):
        path = Path(root) / rel_dir if rel_dir else Path(root)
        try:
            with _scandir(path) as it:
                ents = sorted(it, key=lambda e: e.name)
        except OSError as e:
            out.append({"path": rel_dir or ".", "type": "unknown", "why": f"列不了目录：{e}"[:160]})
            return
        for ent in ents:
            r = f"{rel_dir}/{ent.name}" if rel_dir else ent.name
            try:
                st = os.lstat(path / ent.name)
            except OSError as e:
                out.append({"path": r, "type": "unknown", "why": str(e)[:160]})
                continue
            is_dir = stat.S_ISDIR(st.st_mode)
            if exclude is not None and exclude(r, is_dir):
                continue
            if is_dir:
                out.append({"path": r, "type": "dir", "mode": st.st_mode & 0o777})
                rec(r)
            else:
                out.append(_state_of(root, r)[0])
    rec(rel)
    return out


def _tool_excluded(rel: str, is_dir: bool) -> bool:
    """与 release._tool_files 相同的口径（MANIFEST.json 例外：作为控制文件纳入）。"""
    return bool(set(rel.split("/")) & {".git", "local", "__pycache__"}) or (not is_dir and rel.endswith(".pyc"))


def _inventory(root: Path, exclude, *, consuming=False) -> dict:
    rows = _walk_states(root, "", exclude)
    sources = []
    if consuming:
        # release._tool_files 读取文件链接；目录链接不会被递归。本模块保守拒绝非文件/坏链接。
        for i, row in enumerate(rows):
            if row["type"] == "symlink":
                rows[i] = _source_state(root, row["path"])[0]
                sources.append(rows[i])
    return {"inventory_sha256": _sha(_canon(rows)), "inventory_count": len(rows),
            "inventory_unknown": [r["path"] for r in rows if r["type"] == "unknown"],
            **({"inventory_sources": sources} if consuming else {})}


CONTROL_FILES = {"project": ("workflow/wf.json", "workflow/policy.json", "workflow/goals.json"),
                 "tool": ("VERSION", "MANIFEST.json")}
APPROVALS = "workflow/approvals.json"


def _control(t: dict) -> list:
    """安装门禁实际读取的控制文件（不论是否被 git 忽略、是否在包里）。approvals.json 只取安装链读取的 policy/goals
    两条批准记录；可读但不是 JSON 对象时按整份字节绑定并记 corrupt（policy.approvals 会把它当作未批准）。"""
    root = Path(t["root"])
    out = [_source_state(root, rel)[0] for rel in CONTROL_FILES[t["kind"]]]
    if t["kind"] == "project":
        st, b = _source_state(root, APPROVALS)
        if st["type"] == "file":
            try:
                doc = json.loads(b.decode("utf-8"))
                if not isinstance(doc, dict):
                    raise ValueError
                proj = {k: doc.get(k) for k in ("policy", "goals")}
                st = {k: v for k, v in st.items() if k not in ("sha256", "size")}
                st.update(type="approvals", projection=proj)
            except (UnicodeDecodeError, ValueError):
                st["corrupt"] = True
        out.append(st)
    return out


def _extra_touched(t: dict, pkg: dict) -> list:
    """安装语义里另外会写的路径：项目版本带 RELEASE.json 时复制到 workflow/releases/<版本>.json。"""
    if t["kind"] != "project" or not pkg.get("release") or "PROJECT.md" not in pkg["data"]:
        return []
    try:
        from .projectmd import parse_project_md
        v = parse_project_md(pkg["data"]["PROJECT.md"].decode("utf-8")).get("version")
    except Exception:   # noqa: BLE001  解析不了时由安装本身拒绝
        return []
    return [f"workflow/releases/{v}.json"] if isinstance(v, str) and v and "/" not in v and v not in (".", "..") else []


def _base(t: dict, pkg: dict) -> tuple:
    """目标的实际基准（见模块说明“材料闭包”）。返回 (基准, {路径: (状态, 字节)})。"""
    root = Path(t["root"])
    side = {}
    write = sorted({e["path"] for e in pkg["entries"] if e["type"] == "file" and e.get("installed")}
                   | set(_extra_touched(t, pkg)))
    delete = sorted(set((pkg.get("release") or {}).get("delete", [])))
    touched = []
    for role, rels in (("write", write), ("delete", delete)):
        for rel in rels:
            st, b = _state_of(root, rel)
            st["role"] = role
            if role == "delete" and st["type"] == "dir":
                st["closure"] = _walk_states(root, rel)   # 与 release._delete 一致：整棵目录都会被删
            touched.append(st)
            side[rel] = (st, b)
    out = {"touched": touched, "control": _control(t)}
    if (root / ".git").exists():
        try:
            commit = _git(["rev-parse", "--verify", "HEAD^{commit}"], root).strip()
            tree = _git(["rev-parse", "--verify", commit + "^{tree}"], root).strip()
            raw, err = _git_full(["status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"], root)
        except WfError as e:
            out.update(vcs="git", commit=None, tree=None, worktree=None, error=str(e)[:200])
            return out, side
        if err.strip():   # 例如列不了某个目录：git status 只告警、不报错，未跟踪内容会被漏掉
            out.update(vcs="git", commit=commit, tree=tree, worktree=None, error=f"git status 告警：{err.strip()[:200]}")
            return out, side
        worktree = []
        for rec in raw.split("\0"):
            if len(rec) > 3 and not core.matches_protect(rec[3:], t["protect"]):
                st, _ = _state_of(root, rec[3:])
                st["status"] = rec[:2]
                worktree.append(st)
        out.update(vcs="git", commit=commit, tree=tree, worktree=sorted(worktree, key=lambda x: x["path"]))
        if t["kind"] == "tool":   # self_update 按文件系统读整个工具目录（_tool_files），不只看 git
            out.update(_inventory(root, _tool_excluded, consuming=True))
    else:
        protect = t["protect"]
        out.update(vcs="none", commit=None, tree=None,
                   **(_inventory(root, _tool_excluded, consuming=True) if t["kind"] == "tool" else
                      _inventory(root, lambda r, d: r == ".git" or core.matches_protect(r + ("/" if d else ""), protect)
                                 or core.matches_protect(r, protect))))
    return out, side


def _rules() -> dict:
    b = _read_bytes(_tool_home() / RULES_PATH)
    return {"path": RULES_PATH, "sha256": _sha(b) if b is not None else None}


def _material(t: dict, pkg: dict) -> tuple:
    """返回 (材料, 目标路径的实际字节)。材料完整决定 content_id。"""
    ident = {k: t[k] for k in ("kind", "root", "scope", "project", "tool")}
    rel = pkg["release"] or {}
    base, side = _base(t, pkg)
    return {"schema": SCHEMA_MATERIAL, "target": ident, "base": dict(base, config_sha256=t["config_sha256"],
                                                                     protect=t["protect"]),
            "rules": _rules(),
            "package": {"entries": pkg["entries"], "ignored": pkg["ignored"],
                        "release_json_sha256": rel.get("json_sha256"), "delete": rel.get("delete", []),
                        "wf_config": rel.get("wf_config", {}), "release_keys": rel.get("keys", [])}}, side


def _material_problems(base: dict, rules: dict) -> list:
    """材料里任何未知或不安全的部分：不通过（不把未知当成没问题）。"""
    out = []
    if not isinstance(base, dict) or base.get("error") or (base.get("vcs") == "git" and base.get("worktree") is None):
        out.append("base_unknown")
        base = base if isinstance(base, dict) else {}
    closures = [c for st in base.get("touched") or [] for c in st.get("closure") or []]
    for st in (base.get("touched") or []) + (base.get("worktree") or []) + (base.get("control") or []) + closures:
        if st.get("type") == "unknown":
            out.append(f"base_unknown:{st.get('path')}")
    for p in base.get("inventory_unknown") or []:
        out.append(f"base_unknown:{p}")
    protect = base.get("protect") or []
    for st in base.get("touched") or []:
        bad = ("unsafe", "symlink", "special", "dir") if st.get("role") == "write" else ("unsafe", "symlink", "special")
        if st.get("type") in bad:
            out.append(f"base_unsafe:{st.get('path')}")
        if st.get("role") == "delete":   # release._delete 只查顶层是否受保护；整棵删除会连带删掉受保护的后代
            for c in [st] + list(st.get("closure") or []):
                if core.matches_protect(c.get("path", ""), protect):
                    out.append(f"base_unsafe:delete_protected:{c.get('path')}")
    if not isinstance(rules, dict) or not rules.get("sha256"):
        out.append("rules_unknown")
    return out


def _content_id(material: dict) -> str:
    return _sha(_canon(material))


def _state_txt(st: dict) -> str:
    t = st.get("type")
    if t == "file":
        return f"file sha256 {st.get('sha256')} mode {oct(st.get('mode') or 0)}" + (" （内容不是有效 JSON）" if st.get("corrupt") else "")
    if t == "dir":
        return f"dir mode {oct(st.get('mode') or 0)}" if st.get("mode") is not None else "dir"
    if t == "symlink":
        return f"symlink -> {st.get('target')}"
    return t + (f"（{st.get('why')}）" if st.get("why") else "")


def _mode_txt(m) -> str:
    return oct(m) if m is not None else "默认（解压时由 umask 决定，通常 0o644）"


def _full_diff(t: dict, material: dict, pkg: dict, side: dict) -> bytes:
    """完整的送审材料（不截断）：相对目标实际状态（不是 HEAD）的每个写入文件的内容变化与权限变化（只改权限也写明）、
    类型替换、删除清单与被删对象的实际状态、不安装的成员与原因、wf_config、RELEASE.json 原文、目标现有的未提交改动。"""
    lines = []
    new = {e["path"]: e for e in pkg["entries"] if e["type"] == "file" and e["installed"]}
    for rel in sorted(new):
        e, nb = new[rel], pkg["data"][rel]
        st, ob = side.get(rel, ({"type": "unknown"}, None))
        nm = e.get("mode")
        if st["type"] == "file":
            same_bytes = ob == nb
            same_mode = (nm if nm is not None else 0o644) == st.get("mode")
            if same_bytes and same_mode:
                continue
            if not same_mode:
                lines.append(f"mode change {rel}: {oct(st['mode'])} -> {_mode_txt(nm)}")
            if same_bytes:
                continue
        elif st["type"] == "absent":
            lines.append(f"new file {rel}: mode {_mode_txt(nm)}")
        else:
            what = st["type"] + (f" -> {st.get('target')}" if st["type"] == "symlink" else "")
            lines.append(f"replace {rel}: 目标现在是 {what}，安装后是普通文件（mode {_mode_txt(nm)}）")
        try:
            ot = (ob or b"").decode("utf-8").splitlines()
            nt = nb.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            lines.append(f"Binary {rel}: {(_sha(ob) if ob is not None else '（无）')} -> {e['sha256']}")
            continue
        lines += list(difflib.unified_diff(ot, nt, f"a/{rel}" if ob is not None else "/dev/null", f"b/{rel}",
                                           lineterm=""))
    for d in (pkg["release"] or {}).get("delete", []):
        st = side.get(d, ({"type": "unknown"}, None))[0]
        lines.append(f"delete {d}: 目标现在是 {_state_txt(st)}")
        for c in st.get("closure") or []:
            lines.append(f"  delete {c['path']}: {_state_txt(c)}")
    for e in pkg["entries"]:
        if e["type"] == "file" and not e["installed"]:
            lines.append(f"not installed {e['path']}: sha256 {e['sha256']}（按安装规则不复制）")
    rel = pkg["release"] or {}
    if rel:
        lines.append("wf_config: " + json.dumps(rel.get("wf_config", {}), ensure_ascii=False, sort_keys=True))
        lines.append("===== RELEASE.json 原文 =====")
        lines += pkg["data"]["RELEASE.json"].decode("utf-8", "replace").splitlines()
    lines.append("===== 安装门禁读取的控制文件（已绑定进审查身份） =====")
    for st in (material.get("base") or {}).get("control") or []:
        txt = json.dumps(st, ensure_ascii=False, sort_keys=True)
        lines.append(f"{st['path']}: {txt}")
    for st in (material.get("base") or {}).get("inventory_sources") or []:
        lines.append("tool source: " + json.dumps(st, ensure_ascii=False, sort_keys=True))
    wt = (material.get("base") or {}).get("worktree") or []
    if wt:
        lines.append("===== 目标现有的未提交改动（安装不改动它们，但已绑定进审查身份） =====")
        for st in wt:
            lines.append(f"{st.get('status', '??')} {st['path']}: {st['type']}"
                         + (f" sha256 {st['sha256']} mode {oct(st['mode'])}" if st["type"] == "file" else ""))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _freeze(zip_path) -> tuple:
    """把 zip 冻结到 LOCAL/releases/<sha>.zip（只读）。同一份字节只存一份；已有快照被改动时拒绝。"""
    raw = _read_bytes(Path(zip_path))
    if raw is None:
        raise WfError(f"读不了版本包：{zip_path}")
    sha = _sha(raw)
    dst = _releases() / f"{sha}.zip"
    if os.path.lexists(dst):
        cur = _read_bytes(dst)
        if os.path.islink(dst) or cur is None or _sha(cur) != sha:
            raise WfError(f"冻结快照 {dst.name} 已存在但内容不符，拒绝使用")
    else:
        try:
            _write_new(dst, raw, 0o444)
        except FileExistsError:
            return _freeze(zip_path)
    try:
        os.chmod(dst, 0o444)
    except OSError:
        pass
    return dst, sha


def _snapshot_ok(req: dict) -> list:
    p = _releases() / f"{req['package']['sha256']}.zip"
    b = _read_bytes(p)
    if os.path.islink(p) or b is None:
        return ["snapshot_missing"]
    return [] if _sha(b) == req["package"]["sha256"] else ["snapshot_changed"]


# ---------------- 请求 ----------------

def _request_path(rid: str) -> Path:
    return _reviews() / "requests" / f"{_check_id(_RID, rid, 'request_id')}.json"


def _load_request(rid: str) -> dict:
    b = _read_bytes(_request_path(rid))
    if b is None:
        raise WfError(f"没有审查请求 {rid}")
    try:
        doc = json.loads(b.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise WfError(f"审查请求 {rid} 损坏（不是 JSON）")
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA_REQUEST or doc.get("request_id") != rid:
        raise WfError(f"审查请求 {rid} 损坏（格式不对）")
    body = {k: v for k, v in doc.items() if k != "body_sha256"}
    if doc.get("body_sha256") != _sha(_canon(body)):
        raise WfError(f"审查请求 {rid} 损坏（内容 hash 不符）")
    _check_id(_CID, doc.get("content_id"), "content_id")
    return doc


def prepare(target, zip_path, *, kind, author=None) -> dict:
    t = _target(target, kind)
    snap, sha = _freeze(zip_path)
    pkg = read_package(snap, kind=kind, protect=t["protect"])
    if pkg["sha256"] != sha:
        raise WfError("冻结快照在解析时发生变化，拒绝")
    material, side = _material(t, pkg)
    cid = _content_id(material)
    rid = "rq-" + secrets.token_hex(16)
    diff = _full_diff(t, material, pkg, side)
    dpath = _reviews() / "requests" / f"{rid}.diff"
    _write_new(dpath, diff, 0o444)
    body = {"schema": SCHEMA_REQUEST, "request_id": rid, "content_id": cid, "kind": kind,
            "target": material["target"], "base": material["base"], "rules": material["rules"],
            "package": {"sha256": sha, "bytes": pkg["bytes"], "top": pkg["top"],
                        "snapshot": f"releases/{sha}.zip", "entries_sha256": _sha(_canon(material["package"])),
                        "n_entries": len(pkg["entries"]), "release": material["package"]["release_json_sha256"]},
            "diff": {"path": f"reviews/requests/{rid}.diff", "sha256": _sha(diff), "bytes": len(diff)},
            "author": author if isinstance(author, str) and author else None,
            "prepared_at": _iso(_now()), "material_sha256": cid}
    doc = dict(body, body_sha256=_sha(_canon(body)))
    try:
        _write_new(_request_path(rid), _canon(doc), 0o444)
    except BaseException:
        try:
            os.unlink(dpath)
        except OSError:
            pass
        raise
    with _Lock():
        _index_add(cid, request_id=rid)
    return json.loads(_canon(doc))


def _current_content(req: dict, target) -> tuple:
    """按请求记录的目标真实重算当前材料、content_id 与送审 diff。target 为 None 时用请求里的根目录重建目标。
    返回 (content_id, 问题列表)：目标不符、材料变化、diff 变化、材料里有未知或不安全的部分。"""
    kind = req["kind"]
    tgt = target if target is not None else (req["target"]["root"])
    try:
        t = _target(tgt, kind)
    except WfError as e:
        return None, [f"target_unknown:{str(e)[:80]}"]
    if t["root"] != req["target"]["root"]:
        return None, ["target_mismatch"]
    snap = _releases() / f"{req['package']['sha256']}.zip"
    pkg = read_package(snap, kind=kind, protect=t["protect"])
    material, side = _material(t, pkg)
    cid = _content_id(material)
    probs = _material_problems(material["base"], material["rules"])
    if cid != req["content_id"]:
        probs.append("material_changed")
    if _sha(_full_diff(t, material, pkg, side)) != req["diff"]["sha256"]:
        probs.append("diff_changed")
    return cid, probs


# ---------------- 按内容的登记（只是索引；结论每次从原文件重算） ----------------

def _index_path(cid: str) -> Path:
    return _reviews() / "content" / f"{_check_id(_CID, cid, 'content_id')}.json"


def _index(cid: str) -> dict:
    b = _read_bytes(_index_path(cid))
    try:
        doc = json.loads(b.decode("utf-8")) if b else {}
    except (UnicodeDecodeError, ValueError):
        doc = {"corrupt": True}
    return doc if isinstance(doc, dict) else {"corrupt": True}


def _index_add(cid: str, request_id=None, attempt_id=None) -> None:
    doc = _index(cid)
    doc.setdefault("requests", [])
    doc.setdefault("attempts", [])
    if request_id and request_id not in doc["requests"]:
        doc["requests"].append(request_id)
    if attempt_id and attempt_id not in doc["attempts"]:
        doc["attempts"].append(attempt_id)
    doc["content_id"] = cid
    _write_atomic(_index_path(cid), _canon(doc))


# ---------------- 尝试 ----------------

def _attempt_dir(aid: str) -> Path:
    return _reviews() / "attempts" / _check_id(_AID, aid, "attempt_id")


def _load_attempt(aid: str):
    b = _read_bytes(_attempt_dir(aid) / "attempt.json")
    try:
        doc = json.loads(b.decode("utf-8")) if b else None
    except (UnicodeDecodeError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("schema") == SCHEMA_ATTEMPT and doc.get("attempt_id") == aid else None


def _save_attempt(doc: dict) -> None:
    _write_atomic(_attempt_dir(doc["attempt_id"]) / "attempt.json", _canon(doc))


def _attempts_of(cid: str, problems=None) -> list:
    """按内容列出尝试。索引损坏、登记的尝试读不了或不属于该内容时，记入 problems（结论按不通过处理，不静默丢弃）。"""
    out = []
    idx = _index(cid)
    if idx.get("corrupt") and problems is not None:
        problems.append("index_corrupt")
    for aid in idx.get("attempts", []):
        a = _load_attempt(aid) if isinstance(aid, str) and _AID.fullmatch(aid) else None
        if a is not None and a.get("content_id") == cid:
            out.append(a)
        elif problems is not None:
            problems.append("attempt_record_corrupt")
    return out


def cancel(request_id: str) -> int:
    req = _load_request(request_id)
    n = 0
    with _Lock():
        for a in _attempts_of(req["content_id"]):
            if a.get("request_id") == request_id and a.get("state") == "pending":
                a["state"] = "cancelled"
                a["times"]["cancelled_at"] = _iso(_now())
                _save_attempt(a)
                n += 1
    return n


def _host_program(spec, label: str, pinned=None) -> tuple:
    """宿主配置的外部程序：绝对路径、普通可执行文件；不得位于冻结快照、审查状态目录或工具目录（local/ 除外）之内。
    返回 (realpath, sha256)；pinned 给出时实际 sha256 必须相同。不合格返回 (None, 原因)。"""
    if not isinstance(spec, str) or not spec.strip():
        return None, f"{label}_unavailable"
    p = Path(spec)
    if not p.is_absolute():
        return None, f"{label}_not_absolute"
    real = Path(os.path.realpath(p))
    try:
        st = os.stat(real)
    except OSError:
        return None, f"{label}_missing"
    if not stat.S_ISREG(st.st_mode) or not os.access(real, os.X_OK):
        return None, f"{label}_not_executable"
    home = Path(os.path.realpath(_tool_home()))
    for bad in (Path(os.path.realpath(_releases())), Path(os.path.realpath(_reviews()))):
        if real == bad or bad in real.parents:
            return None, f"{label}_inside_host_state"
    if (home in real.parents) and not (home / "local" in real.parents):
        return None, f"{label}_inside_tool"
    b = _read_bytes(real)
    if b is None:
        return None, f"{label}_unreadable"
    h = _sha(b)
    if pinned is not None and h != pinned:
        return None, f"{label}_sha256_mismatch"
    return (str(real), h), ""


def _transport() -> dict:
    from . import anomaly
    cfg = anomaly.transport_cfg()
    return cfg if isinstance(cfg, dict) else {}


def _runner_cmd(prog: str, req: dict, att: dict) -> list:
    d = _attempt_dir(att["attempt_id"])
    return [prog, "review", "--request", str(d / "request.json"), "--diff", str(d / "diff.patch"),
            "--package", str(_local() / req["package"]["snapshot"]), "--nonce", att["nonce"], "--attempt", att["attempt_id"]]


def _diff_file_problems(req: dict) -> tuple:
    """宿主保存的 diff 文件与请求记录是否一致。返回 (问题列表, 字节)。"""
    b = _read_bytes(_local() / req["diff"]["path"])
    if b is None:
        return ["diff_file_missing"], None
    return ([] if _sha(b) == req["diff"]["sha256"] else ["diff_file_changed"]), b


def _exec(cmd: list, timeout: int) -> dict:
    env = dict(os.environ)
    env["WF_REVIEW_PROTOCOL"] = "review/1"
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
        return {"code": r.returncode, "stdout": r.stdout or b"", "stderr": r.stderr or b"", "timed_out": False}
    except subprocess.TimeoutExpired as e:
        return {"code": None, "stdout": e.stdout or b"", "stderr": e.stderr or b"", "timed_out": True}
    except OSError as e:
        return {"code": None, "stdout": b"", "stderr": str(e).encode(), "timed_out": False, "error": str(e)}


def _last_json(b: bytes):
    try:
        lines = [x for x in b.decode("utf-8").splitlines() if x.strip()]
    except UnicodeDecodeError:
        return None, b""
    if not lines:
        return None, b""
    last = lines[-1].strip().encode("utf-8")
    try:
        obj = json.loads(last.decode("utf-8"))
    except ValueError:
        return None, last
    return (obj if isinstance(obj, dict) else None), last


def run(request_id: str, *, timeout_s=None) -> dict:
    req = _load_request(request_id)
    pre = _snapshot_ok(req)
    if not pre:
        _, pre = _current_content(req, None)
    dprob, diff_bytes = _diff_file_problems(req)
    pre = pre + dprob + _material_problems(req.get("base"), req.get("rules"))
    if pre:   # 材料与送审 diff 都核过才送审：不让审查者看到与请求不同的材料
        return _decide(req, None, phase="review", extra=list(dict.fromkeys(pre)))
    cfg = _transport()
    prog, why = _host_program(cfg.get("review"), "runner")
    if prog is None:
        return _decide(req, None, phase="review", extra=[why])
    timeout = int(timeout_s or cfg.get("review_timeout_s") or 900)
    with _Lock():
        for a in _attempts_of(req["content_id"]):
            if a.get("request_id") == request_id and a.get("state") == "pending":
                a["state"] = "superseded"
                a["times"]["superseded_at"] = _iso(_now())
                _save_attempt(a)
        aid = "at-" + secrets.token_hex(16)
        att = {"schema": SCHEMA_ATTEMPT, "attempt_id": aid, "request_id": request_id, "content_id": req["content_id"],
               "nonce": secrets.token_hex(16), "state": "pending",
               "times": {"review_started": _iso(_now()), "review_completed": None, "result_registered": None},
               "runner": {"path": prog[0], "sha256": prog[1]}, "files": {}, "diagnostics": []}
        _attempt_dir(aid).mkdir(parents=True, exist_ok=False)
        req_bytes = _canon(req)
        _write_new(_attempt_dir(aid) / "request.json", req_bytes, 0o444)   # 送审的就是核过的这份字节
        _write_new(_attempt_dir(aid) / "diff.patch", diff_bytes, 0o444)
        att["sent"] = {"request": {"sha256": _sha(req_bytes)}, "diff": {"sha256": _sha(diff_bytes)}}
        _save_attempt(att)
        _index_add(req["content_id"], attempt_id=aid)
    r = _exec(_runner_cmd(prog[0], req, att), timeout)
    done_at = _iso(_now())
    d = _attempt_dir(aid)
    _write_new(d / "stdout.bin", r["stdout"], 0o444)
    _write_new(d / "stderr.bin", r["stderr"], 0o444)
    result, line = _last_json(r["stdout"])
    _write_new(d / "result.json", line, 0o444)
    with _Lock():
        att = _load_attempt(aid) or att
        att["files"] = {"stdout": {"sha256": _sha(r["stdout"]), "bytes": len(r["stdout"])},
                        "stderr": {"sha256": _sha(r["stderr"]), "bytes": len(r["stderr"])},
                        "result": {"sha256": _sha(line), "bytes": len(line)}}
        att["runner"].update(exit=r["code"], timed_out=r["timed_out"])
        att["times"]["review_completed"] = done_at
        if r["code"] != 0 and not r["timed_out"]:
            att["diagnostics"].append(f"审查程序退出码 {r['code']}" + (f"：{r.get('error')}" if r.get("error") else "")
                                      + "（原文与 stderr 已保存，结论不采用）")
        if len(r["stdout"]) > MAX_OUTPUT_BYTES:
            att["diagnostics"].append("审查输出超过上限")
        if isinstance(result, dict):
            claims = [k for k in _SELF_CLAIM_KEYS if k in result]
            if claims:
                att["diagnostics"].append("结果自带的 " + "、".join(claims) + " 被忽略（不作为来源依据，也不读取其指向的文件）")
            att["verdict_claimed"] = result.get("verdict")
            att["model_claimed"] = result.get("model") if isinstance(result.get("model"), str) else None
        if att["state"] != "pending":
            att["late_of"] = att["state"]
            att["state"] = "late"
        elif r["timed_out"]:
            att["state"] = "timeout"
        else:
            att["state"] = "completed"
            att["times"]["result_registered"] = _iso(_now())
        _save_attempt(att)
    if att["state"] == "completed" and r["code"] == 0:
        _verify(req, att)
    return _decide(req, None, phase="review")


def _verify(req: dict, att: dict) -> None:
    """调用宿主固定 sha256 的外部核验器：verify --request --result --raw；保存其原始输出与 hash。"""
    cfg = _transport().get("review_verifier")
    d = _attempt_dir(att["attempt_id"])
    info = {"configured": isinstance(cfg, dict)}
    if isinstance(cfg, dict):
        pinned = cfg.get("sha256") if isinstance(cfg.get("sha256"), str) and _SHA.fullmatch(cfg.get("sha256")) else None
        prog, why = _host_program(cfg.get("path"), "verifier", pinned=pinned) if pinned else (None, "verifier_sha256_not_pinned")
        info.update(pinned=pinned, synthetic=bool(cfg.get("synthetic")), why=why)
        if prog is not None:
            info.update(path=prog[0], sha256=prog[1])
            r = _exec([prog[0], "verify", "--request", str(d / "request.json"),
                       "--result", str(d / "result.json"), "--raw", str(d / "stdout.bin")],
                      int(cfg.get("timeout_s") or 120))
            _write_new(d / "verify_stdout.bin", r["stdout"], 0o444)
            _write_new(d / "verify_stderr.bin", r["stderr"], 0o444)
            info.update(exit=r["code"], timed_out=r["timed_out"],
                        stdout={"sha256": _sha(r["stdout"]), "bytes": len(r["stdout"])},
                        stderr={"sha256": _sha(r["stderr"]), "bytes": len(r["stderr"])})
    with _Lock():
        a = _load_attempt(att["attempt_id"]) or att
        a["verifier"] = info
        _save_attempt(a)


# ---------------- 结论（每次从宿主原文件重算） ----------------

def _lanes() -> dict:
    b = _read_bytes(_local() / "lanes.json")
    try:
        doc = json.loads(b.decode("utf-8")) if b else {}
    except (UnicodeDecodeError, ValueError):
        return {}
    return doc.get("lanes") if isinstance(doc, dict) and isinstance(doc.get("lanes"), dict) else {}


def _file_ok(d: Path, name: str, rec) -> bool:
    b = _read_bytes(d / name)
    return isinstance(rec, dict) and b is not None and _sha(b) == rec.get("sha256")


def _verifier_now() -> dict:
    """宿主此刻的核验器配置与实际字节（每次结论都重算一次，不沿用历史记录）。"""
    cfg = _transport().get("review_verifier")
    if not isinstance(cfg, dict):
        return {"why": "verifier_unavailable_now"}
    pinned = cfg.get("sha256") if isinstance(cfg.get("sha256"), str) and _SHA.fullmatch(cfg.get("sha256")) else None
    if not pinned:
        return {"why": "verifier_sha256_not_pinned_now"}
    prog, why = _host_program(cfg.get("path"), "verifier", pinned=pinned)
    if prog is None:
        return {"why": why + "_now"}
    return {"path": prog[0], "sha256": prog[1], "pinned": pinned, "synthetic": bool(cfg.get("synthetic")), "why": ""}


def _evaluate(req, a: dict, now: datetime, cur_req: dict, trust: dict) -> dict:
    """单个尝试：从原文件重算结论与来源。req 是该尝试所属的请求（可能为 None：读不了），cur_req 是正在判定的请求，
    trust 是宿主此刻的核验器。返回 {verdict, eligible, reasons, provenance, expires_at}。"""
    d = _attempt_dir(a["attempt_id"])
    reasons, prov = [], {"level": "unavailable", "synthetic": False, "model_claimed": a.get("model_claimed"),
                         "model_verified": None, "reviewer_identity": None, "verifier_sha256": None}
    files = a.get("files") or {}
    verdict = None
    if req is None:
        reasons.append("attempt_request_corrupt")
        req = {"request_id": a.get("request_id"), "content_id": a.get("content_id"), "author": None,
               "diff": {"sha256": None}}
    sent = a.get("sent") or {}
    if not (_file_ok(d, "request.json", sent.get("request")) and _file_ok(d, "diff.patch", sent.get("diff"))
            and sent.get("diff", {}).get("sha256") == req["diff"]["sha256"]
            and sent.get("request", {}).get("sha256") == _sha(_canon(req))):
        reasons.append("sent_material_mismatch")
    code = (a.get("runner") or {}).get("exit")
    if code != 0:
        reasons.append(f"runner_exit_{code}")
    if not all(_file_ok(d, f"{k}.bin" if k != "result" else "result.json", files.get(k)) for k in ("stdout", "stderr", "result")):
        reasons.append("raw_hash_mismatch")
    res, _ = _last_json(_read_bytes(d / "result.json") or b"")
    if not isinstance(res, dict):
        reasons.append("result_invalid")
    else:
        echo = {"request_id": req["request_id"], "content_id": req["content_id"], "nonce": a.get("nonce"),
                "attempt_id": a["attempt_id"]}
        bad = [k for k, v in echo.items() if res.get(k) != v]
        if bad:
            reasons.append("result_echo_mismatch:" + ",".join(bad))
        if res.get("verdict") in VERDICTS:
            verdict = res["verdict"]
        else:
            reasons.append("verdict_unknown")
    v = a.get("verifier") or {}
    if not v.get("configured"):
        reasons.append("verifier_unavailable")
    elif v.get("why"):
        reasons.append(v["why"])
    elif v.get("sha256") != v.get("pinned") or not v.get("pinned"):
        reasons.append("verifier_sha256_mismatch")
    elif trust.get("why") or any(trust.get(k) != v.get(k) for k in ("path", "sha256", "pinned", "synthetic")):
        reasons.append(trust.get("why") or "verifier_changed")   # 宿主当前核验器被删、被改配置或被换字节：失效
    else:
        prov["verifier_sha256"] = v.get("sha256")
        prov["synthetic"] = bool(v.get("synthetic"))
        if not _file_ok(d, "verify_stdout.bin", v.get("stdout")):
            reasons.append("verify_output_hash_mismatch")
        out, _ = _last_json(_read_bytes(d / "verify_stdout.bin") or b"")
        if not isinstance(out, dict) or v.get("exit") != 0:
            reasons.append("verify_output_invalid")
        else:
            want = {"request_id": req["request_id"], "content_id": req["content_id"], "nonce": a.get("nonce"),
                    "attempt_id": a["attempt_id"], "raw_sha256": files.get("stdout", {}).get("sha256"),
                    "result_sha256": files.get("result", {}).get("sha256")}
            bad = [k for k, val in want.items() if out.get(k) != val]
            if bad:
                reasons.append("verify_echo_mismatch:" + ",".join(bad))
            level = out.get("level") if out.get("level") in LEVELS else "unknown"
            if out.get("ok") is not True:
                reasons.append("verify_not_ok")
                level = "unknown" if level == "platform_verified" else level
            prov["level"] = level if not bad else "unknown"
            prov["synthetic"] = prov["synthetic"] or bool(out.get("synthetic"))
            if prov["level"] == "platform_verified":
                prov["model_verified"] = out.get("model_verified") if isinstance(out.get("model_verified"), str) else None
                ident = out.get("reviewer_identity")
                prov["reviewer_identity"] = ident if isinstance(ident, dict) else None
    if prov["level"] != "platform_verified":
        reasons.append(f"provenance_{prov['level']}")
    else:
        ident = prov["reviewer_identity"] or {}
        sess = ident.get("session")
        lanes = _lanes()

        def sessions_of(author):
            lane = lanes.get(author) if isinstance(author, str) and author.strip() else None
            sessions = lane.get("sessions") if isinstance(lane, dict) else None
            return sessions if (isinstance(sessions, list) and sessions
                                and all(isinstance(x, str) and x.strip() for x in sessions)) else None
        cur_author = cur_req.get("author")
        mine, original = sessions_of(cur_author), sessions_of(req.get("author"))
        if not isinstance(sess, str) or not sess.strip():
            reasons.append("reviewer_identity_missing")
        if mine is None or original is None:
            reasons.append("author_identity_missing")
        elif isinstance(sess, str) and sess in set(mine) | set(original):
            reasons.append("reviewer_is_author")   # 按当前请求的作者（以及原请求的作者）、lanes.json 当前登记判定
    completed = _parse_iso(a.get("times", {}).get("review_completed"))
    if completed is None or now > completed + timedelta(days=REUSE_DAYS):
        reasons.append("expired")
    if a.get("state") != "completed":
        reasons.append(f"state_{a.get('state')}")
    return {"verdict": verdict, "eligible": not reasons and verdict in ("pass", "concerns"), "reasons": reasons,
            "provenance": prov, "expires_at": _iso(completed + timedelta(days=REUSE_DAYS)) if completed else None}


def _decide(req: dict, target, *, phase: str, now=None, extra=None) -> dict:
    now = now or _now()
    reasons = list(extra or [])
    problems = []
    atts = sorted(_attempts_of(req["content_id"], problems), key=lambda a: a.get("times", {}).get("review_started") or "")
    reasons += [x for x in dict.fromkeys(problems)]
    reasons += _material_problems(req.get("base"), req.get("rules"))
    if _rules().get("sha256") != (req.get("rules") or {}).get("sha256"):
        reasons.append("rules_changed")
    reasons += _diff_file_problems(req)[0]
    reasons = list(dict.fromkeys(reasons))
    mine = [a for a in atts if a.get("request_id") == req["request_id"]]
    latest = mine[-1] if mine else None
    trust = _verifier_now()
    evals = {a["attempt_id"]: _evaluate(req if a.get("request_id") == req["request_id"] else
                                        _safe_request(a.get("request_id")), a, now, req, trust) for a in atts}
    block = [a for a in atts if a.get("state") in ("completed", "late") and evals[a["attempt_id"]]["verdict"] == "block"]
    ok = [a for a in atts if evals[a["attempt_id"]]["eligible"]]
    chosen = ok[-1] if ok else (latest or (atts[-1] if atts else None))   # 本请求没有尝试时，用同内容最近一次说明原因
    ev = evals.get(chosen["attempt_id"]) if chosen else None
    verdict = "block" if block else (ev["verdict"] if ev else None)
    if block:
        reasons.append("blocked")
    if not atts:
        reasons.append("no_review")
    elif not ok:
        reasons += [r for r in (ev["reasons"] if ev else []) if r not in reasons]
    allowed = bool(ok) and not block and not reasons
    if not allowed and not reasons:
        reasons.append("no_eligible_review")
    diags = []
    for a in atts:
        diags += [f"{a['attempt_id']}: {x}" for x in a.get("diagnostics", [])]
    times = dict((chosen or {}).get("times") or {})
    return {"schema": SCHEMA_DECISION, "request_id": req["request_id"], "content_id": req["content_id"], "phase": phase,
            "state": latest.get("state") if latest else "none", "verdict": verdict, "allowed": allowed,
            "reasons": reasons, "provenance": ev["provenance"] if ev else
            {"level": "unavailable", "synthetic": False, "model_claimed": None, "model_verified": None,
             "reviewer_identity": None, "verifier_sha256": None},
            "times": {"review_started": times.get("review_started"), "review_completed": times.get("review_completed"),
                      "result_registered": times.get("result_registered"), "install_started": None},
            "expires_at": ev["expires_at"] if ev else None, "attempt_id": chosen["attempt_id"] if chosen else None,
            "diagnostics": diags}


def _safe_request(rid):
    try:
        return _load_request(rid)
    except WfError:
        return None


def status(target, request_id=None, *, content_id=None, phase="review", now=None) -> dict:
    if phase not in ("review", "install"):
        raise WfError(f"phase 只能是 review 或 install，不是 {phase!r}")
    if request_id is None:
        if content_id is None:
            raise WfError("status 需要 request_id 或 content_id")
        reqs = [r for r in (_safe_request(x) for x in _index(_check_id(_CID, content_id, "content_id")).get("requests", [])
                            if isinstance(x, str) and _RID.fullmatch(x)) if r]
        if not reqs:
            raise WfError(f"没有 content_id {content_id} 的审查请求")
        req = sorted(reqs, key=lambda r: r.get("prepared_at") or "")[-1]
    else:
        req = _load_request(request_id)
        if content_id is not None and content_id != req["content_id"]:
            raise WfError("request_id 与 content_id 不一致")
    extra = _snapshot_ok(req)
    if phase == "install" and target is None:
        raise WfError("phase=install 需要 target")
    if target is not None and not extra:
        _, extra = _current_content(req, target)
    return _decide(req, target, phase=phase, now=now, extra=extra)


# ---------------- 安装锁内复核与消费 ----------------

def _snapshot_package(req: dict) -> dict:
    """只解析该请求的冻结包；解析后的 raw SHA 与送审安装语义也必须一致。"""
    bad = _snapshot_ok(req)
    if bad:
        raise WfError("冻结快照不可用：" + "、".join(bad))
    pkg = read_package(_releases() / f"{req['package']['sha256']}.zip",
                       kind=req["kind"], protect=req["base"]["protect"])
    rel = pkg["release"] or {}
    semantics = {"entries": pkg["entries"], "ignored": pkg["ignored"],
                 "release_json_sha256": rel.get("json_sha256"), "delete": rel.get("delete", []),
                 "wf_config": rel.get("wf_config", {}), "release_keys": rel.get("keys", [])}
    expected = {"sha256": pkg["sha256"], "bytes": pkg["bytes"], "top": pkg["top"],
                "snapshot": f"releases/{pkg['sha256']}.zip", "entries_sha256": _sha(_canon(semantics)),
                "n_entries": len(pkg["entries"]), "release": rel.get("json_sha256")}
    if req["package"] != expected:
        raise WfError("审查请求的冻结包身份或安装语义不符（package_mismatch）")
    return pkg


def load_install_request(request_id: str, *, target, kind, zip_path=None) -> dict:
    """核实真实消费者的请求/包/目标绑定，不执行审查、不写开始记录，也不授予安装许可。
    caller ZIP 只允许作为旧调用的 raw SHA 断言；安装字节必须来自 extract_snapshot。"""
    try:
        req = _load_request(request_id)
        if kind not in ("project", "tool") or req["kind"] != kind:
            raise WfError("安装入口与审查请求 kind 不一致（kind_mismatch）")
        t = _target(target, kind)
        ident = {k: t[k] for k in ("kind", "root", "scope", "project", "tool")}
        if req["target"] != ident:
            raise WfError("安装目标与审查请求不一致（target_mismatch）")
        pkg = _snapshot_package(req)
        if zip_path is not None:
            raw = _read_bytes(Path(zip_path))
            if raw is None or _sha(raw) != pkg["sha256"]:
                raise WfError("调用方 ZIP 与审查请求的冻结包不一致（package_mismatch）")
        material, side = _material(t, pkg)
        bad = _material_problems(material["base"], material["rules"])
        cid = _content_id(material)
        if (cid != req["content_id"] or req["material_sha256"] != cid
                or req["base"] != material["base"] or req["rules"] != material["rules"]):
            bad.append("material_changed")
        if _sha(_full_diff(t, material, pkg, side)) != req["diff"]["sha256"]:
            bad.append("diff_changed")
        if bad:
            raise WfError("安装请求材料核验失败：" + "；".join(bad[:6]))
        return req
    except (OSError, KeyError, TypeError, ValueError) as e:
        raise WfError(f"安装请求不可用（{type(e).__name__}）：{e}") from e


def extract_snapshot(request_id: str, dest) -> Path:
    req = _load_request(request_id)
    pkg = _snapshot_package(req)
    root = Path(dest) / pkg["top"]
    root.mkdir(parents=True, exist_ok=False)
    for e in pkg["entries"]:
        p = root / e["path"]
        if e["type"] == "dir":
            p.mkdir(parents=True, exist_ok=True)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        _write_new(p, pkg["data"][e["path"]], 0o644)
        if e.get("mode") is not None:
            os.chmod(p, e["mode"] & 0o777)
    return root


def verify_extracted(request_id: str, root) -> list:
    if root is None:
        raise WfError("解包复核需要实际目录（extracted_dir_required）")
    try:
        if not stat.S_ISDIR(os.lstat(root).st_mode):
            return ["extracted_root_unsafe"]
    except OSError as e:
        return [f"extracted_root_unknown:{type(e).__name__}"]
    req = _load_request(request_id)
    bad = _snapshot_ok(req)
    if bad:
        return bad
    pkg = _snapshot_package(req)
    want = {e["path"]: e for e in pkg["entries"] if e["type"] == "file"
            and not ("__pycache__" in e["path"].split("/") or e["path"].endswith(".pyc"))}   # 与安装规则一致：不复制
    rows = _walk_states(Path(root), "", lambda r, d: "__pycache__" in r.split("/") or (not d and r.endswith(".pyc")))
    got, problems = {}, []
    for st in rows:   # 列不了的目录、读不了的文件都报出来，不静默跳过
        t = st["type"]
        if t == "file":
            got[st["path"]] = st
        elif t == "symlink":
            problems.append(f"extracted_symlink:{st['path']}")
        elif t != "dir":
            problems.append(f"extracted_{t}:{st['path']}")
    for rel in sorted(set(want) - set(got)):
        problems.append(f"extracted_missing:{rel}")
    for rel in sorted(set(got) - set(want)):
        problems.append(f"extracted_extra:{rel}")
    for rel in sorted(set(want) & set(got)):
        if got[rel].get("sha256") != want[rel]["sha256"]:
            problems.append(f"extracted_changed:{rel}")
        elif want[rel].get("mode") is not None and got[rel].get("mode") != (want[rel]["mode"] & 0o777):
            problems.append(f"extracted_mode:{rel}")
    return problems


def final_check(request_id: str, *, target, extracted_dir=None, now=None) -> dict:
    d = status(target, request_id, phase="install", now=now)
    if extracted_dir is not None:
        bad = verify_extracted(request_id, extracted_dir)
        if bad:
            d["reasons"] = d["reasons"] + bad
            d["allowed"] = False
    return d


def consume(request_id: str, *, target, extracted_dir, now=None, install_context=None) -> dict:
    """在登记审查结果所用的同一把锁内：重算全部结论（final_check）并写安装开始记录。登记 block 与消费因此按锁的先后
    线性排列：先登记的 block 一定被看到；锁释放之前任何结果都登记不进来。"""
    if extracted_dir is None:
        raise WfError("消费审查需要实际解包目录（extracted_dir_required）")
    with _Lock():
        d = final_check(request_id, target=target, extracted_dir=extracted_dir, now=now)
        if not d["allowed"]:
            raise WfError("安装前审查未通过，不能安装：" + "；".join(d["reasons"][:6]))
        t = _iso(now or _now())
        d["times"]["install_started"] = t
        rec = {"schema": "review-install/1", "request_id": request_id, "content_id": d["content_id"],
               "attempt_id": d["attempt_id"], "install_started": t, "decision": d,
               "decision_sha256": _sha(_canon(d))}
        if install_context is not None:
            if not isinstance(install_context, dict):
                raise WfError("install_context 必须是对象")
            rec["install_context"] = dict(install_context)
        path = _reviews() / "installs" / d["content_id"] / f"{secrets.token_hex(8)}.json"
        _write_new(path, _canon(rec), 0o444)
        for p in (path, path.parent):
            fd = os.open(str(p), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    return d
