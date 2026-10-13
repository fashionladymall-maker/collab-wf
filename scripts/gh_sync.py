#!/usr/bin/env python3
"""collab-wf → GitHub 公开同步（执行端同步程序）。

默认同步 fashionladymall-maker/collab-wf 的 main 分支，可用 --repo/--branch 指定。
保持单项目根目录镜像和 scripts/ 保留契约；--dry-run 只读取远端、输出计划。
成功同步最后一行输出 {"commit": "<提交号>"}，失败（包括锁忙）非零退出。
"""
import argparse
import base64
from contextlib import ExitStack
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from urllib.parse import quote

OWNER = "fashionladymall-maker"
REPO = "collab-wf"
BRANCH = "main"
GH = "/opt/homebrew/bin/gh" if Path("/opt/homebrew/bin/gh").exists() else "gh"
HOME = Path("/Users/admin/.workbuddy-ai/executor")
LOG = None
DEADLINE = None
KEEP_PREFIXES = ("scripts/",)
RESERVE_S = 30.0
API_TIMEOUT_S = 60.0
SENSITIVE_NAMES = {".git", ".ssh", ".aws", ".azure", ".gnupg", ".kube",
                   ".config", ".workbuddy-ai", "node_modules", ".venv", "venv",
                   "credentials", "credentials.json", "secrets", "secrets.json",
                   "secrets.yaml", "secrets.yml", "id_rsa", "id_ed25519"}


def deadline_from_env() -> float | None:
    """预留收尾时间；较小预算不能被提升为较大预算。"""
    try:
        budget = float(os.environ.get("WF_ADAPTER_TIMEOUT_S") or 0)
    except ValueError:
        budget = 0.0
    if not math.isfinite(budget):
        raise ValueError("WF_ADAPTER_TIMEOUT_S 必须是有限数值")
    if budget <= 0:
        return None
    return time.monotonic() + max(0.0, budget - RESERVE_S)


def check_deadline():
    if DEADLINE is not None and time.monotonic() >= DEADLINE:
        raise TimeoutError("总时间预算已耗尽，本次不再发起 API 请求")


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')}  {msg}"
    print(line, file=sys.stderr)
    try:
        if LOG is not None:
            with safe_open(LOG, "a") as f:
                f.write(line + "\n")
    except OSError:
        pass


def gh_api(method, path, payload=None, allow_fail=False):
    check_deadline()
    timeout = API_TIMEOUT_S
    if DEADLINE is not None:
        timeout = min(timeout, DEADLINE - time.monotonic())
        if timeout <= 0:
            raise TimeoutError("总时间预算已耗尽")
    args = [GH, "api", "--method", method, path]
    kwargs = {"capture_output": True, "text": True, "timeout": timeout}
    if payload is not None:
        args += ["--input", "-"]
        kwargs["input"] = json.dumps(payload)
    p = subprocess.run(args, **kwargs)
    if p.returncode != 0:
        # 仅不存在/空仓库允许进入播种逻辑，认证或网络失败绝不伪装成空仓库。
        if allow_fail and re.search(r"HTTP (404|409)\b", p.stderr or ""):
            return None
        raise RuntimeError(f"[API 失败] {method} {path}\n{(p.stderr or '').strip()}")
    out = (p.stdout or "").strip()
    return json.loads(out) if out else {}


def reject_symlink_path(path):
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f"拒绝符号链接路径：{part}")


def safe_open(path, mode):
    """不跟随最终路径符号链接，并且只接受普通文件。"""
    reject_symlink_path(path)
    flags = os.O_NOFOLLOW | os.O_NONBLOCK
    if mode == "r":
        flags |= os.O_RDONLY
    else:
        flags |= os.O_WRONLY | os.O_CREAT
        flags |= os.O_APPEND if mode == "a" else os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError(f"拒绝非普通文件：{path}")
    return os.fdopen(fd, mode, encoding="utf-8")


def collect_stage(stage: Path) -> dict:
    """先验证整个目录的 scope，再读取普通文件；不跟随符号链接。"""
    stage = Path(os.path.abspath(stage))
    reject_symlink_path(stage)
    if not stage.is_dir():
        raise ValueError(f"暂存目录不存在：{stage}")
    protected = (Path("/"), Path.home(), HOME.absolute(), Path.cwd())
    if any(stage == p or stage in p.parents for p in protected):
        raise ValueError(f"拒绝危险暂存根目录：{stage}")
    # 注：不检查 stage 完整路径是否含敏感目录名（如 .workbuddy-ai），
    # 因为工具自己的合法暂存目录可能位于其中。
    # 真正的敏感文件/目录检查在 os.walk 阶段针对 stage 内部逐项进行。
    files = []
    for root, dirs, names in os.walk(stage, followlinks=False):
        check_deadline()
        for name in sorted(dirs + names):
            p = Path(root) / name
            lower = name.lower()
            if lower in SENSITIVE_NAMES or lower.startswith(".env") or lower.endswith((".pem", ".key", ".p12", ".pfx")):
                raise ValueError(f"暂存目录含禁止同步的敏感路径：{p.relative_to(stage)}")
            s = p.lstat()
            if stat.S_ISLNK(s.st_mode):
                raise ValueError(f"暂存目录含符号链接：{p.relative_to(stage)}")
            if stat.S_ISREG(s.st_mode):
                files.append((p, s))
            elif not stat.S_ISDIR(s.st_mode):
                raise ValueError(f"暂存目录含非普通文件：{p.relative_to(stage)}")
    out = {}
    for p, expected in sorted(files):
        check_deadline()
        reject_symlink_path(p)
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as f:
            actual = os.fstat(f.fileno())
            if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                raise ValueError(f"暂存路径在检查后变化：{p.relative_to(stage)}")
            out[p.relative_to(stage).as_posix()] = f.read()
    return out


def state_hash(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def ensure_dir(path):
    reject_symlink_path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    reject_symlink_path(path)


def acquire_lock(stack, path):
    # flock 由关闭 fd 释放；不删除锁文件。
    reject_symlink_path(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError(f"拒绝非普通锁文件：{path}")
    stream = stack.enter_context(os.fdopen(fd, "a+", encoding="utf-8"))
    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def git_blob_sha(content):
    return hashlib.sha1(b"blob " + str(len(content)).encode("ascii") + b"\0" + content).hexdigest()


def main() -> int:
    global LOG, DEADLINE
    LOG = None
    DEADLINE = None
    ap = argparse.ArgumentParser(description="collab-wf 项目上下文同步（推送端）")
    ap.add_argument("--project", required=True)
    ap.add_argument("--turn", default="")
    ap.add_argument("--dir", required=True, help="工具生成的独立暂存目录")
    ap.add_argument("--repo", default=f"{OWNER}/{REPO}", help="目标 owner/repo")
    ap.add_argument("--branch", default=BRANCH)
    ap.add_argument("--dry-run", action="store_true", help="只读远端并计算同步计划，不写远端")
    a = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9_.-]+", a.repo) or a.repo.split("/")[1] in (".", ".."):
        ap.error("--repo 必须是合法的 owner/repo")
    if (not a.branch or a.branch == "@" or a.branch.startswith(("-", "/"))
            or a.branch.endswith(("/", ".")) or any(c in a.branch for c in " ~^:?*[\\")
            or any(ord(c) < 32 or ord(c) == 127 for c in a.branch)
            or any(x in a.branch for x in ("..", "@{", "//"))
            or any(p.startswith(".") or p.endswith(".lock") for p in a.branch.split("/"))):
        ap.error("--branch 不是合法的 Git 分支名")
    if not a.project.strip():
        ap.error("--project 不能为空")
    repo = a.repo.lower()
    prefix = f"/repos/{repo}"
    branch = quote(a.branch, safe="")
    state = HOME / "ghsync-state"
    project_state = state / "projects" / state_hash(a.project, repo, a.branch)
    remote_state = state / "remotes" / state_hash(repo, a.branch)
    try:
        DEADLINE = deadline_from_env()
        ensure_dir(project_state)
        ensure_dir(remote_state)
        LOG = project_state / "ghsync.log"
        target = collect_stage(Path(a.dir))
        # 旧契约：同步程序保留自己的源码，仅此一项允许来自 stage 之外。
        target["scripts/gh_sync.py"] = Path(__file__).resolve().read_bytes()
        with ExitStack() as stack:
            try:
                acquire_lock(stack, project_state / "ghsync.lock")
                acquire_lock(stack, remote_state / "root.lock")
            except BlockingIOError:
                log("目标项目或仓库分支已有同步在进行，本次未提交")
                return 4
            owner_path = remote_state / "root-owner.json"
            if owner_path.exists() or owner_path.is_symlink():
                with safe_open(owner_path, "r") as f:
                    owner = json.load(f)
                if owner != {"project": a.project, "repo": repo, "branch": a.branch}:
                    raise ValueError("拒绝跨项目根目录镜像：该仓库分支已绑定其他项目")
            elif not a.dry_run:
                with safe_open(owner_path, "w") as f:
                    json.dump({"project": a.project, "repo": repo, "branch": a.branch}, f, ensure_ascii=False)
            ref = gh_api("GET", f"{prefix}/git/ref/heads/{branch}", allow_fail=True)
            base_commit = base_tree = None
            seeded = False
            if ref:
                base_commit = ref["object"]["sha"]
                base_tree = gh_api("GET", f"{prefix}/git/commits/{base_commit}")["tree"]["sha"]
            elif not a.dry_run:
                seed = gh_api("PUT", f"{prefix}/contents/.seed", {
                    "message": "chore: 初始化仓库",
                    "content": base64.b64encode(b"seed\n").decode(), "branch": a.branch,
                })
                base_commit = seed["commit"]["sha"]
                base_tree = gh_api("GET", f"{prefix}/git/commits/{base_commit}")["tree"]["sha"]
                seeded = True
                log("空仓库：已播种初始提交")
            remote = {}
            if base_tree:
                t = gh_api("GET", f"{prefix}/git/trees/{base_tree}?recursive=1")
                if t.get("truncated"):
                    raise ValueError("远端目录树被截断，拒绝计算根目录镜像")
                if any(e.get("type") == "commit" for e in t.get("tree", [])):
                    raise ValueError("远端含子模块，拒绝根目录镜像")
                remote = {e["path"]: e for e in t.get("tree", []) if e.get("type") == "blob"}
            digests = {rp: hashlib.sha256(b).hexdigest() for rp, b in target.items()}
            # 缓存仅记账：无变化必须基于实际远端 Git blob SHA 与 mode 判断。
            changed = sorted(rp for rp, b in target.items()
                             if remote.get(rp, {}).get("sha") != git_blob_sha(b)
                             or remote.get(rp, {}).get("mode") != "100644")
            removed = sorted(p for p in remote if p not in target and p != ".seed"
                             and not p.startswith(KEEP_PREFIXES))
            if a.dry_run:
                log(f"dry-run：{len(changed)} 改 / {len(removed)} 删，不写远端")
                print(json.dumps({"dry_run": True, "repo": repo, "branch": a.branch,
                                  "base_commit": base_commit, "changed": changed,
                                  "removed": removed, "needs_seed": not bool(ref)}, ensure_ascii=False))
                return 0
            if not changed and not removed and not seeded:
                log(f"无变化（{len(target)} 个文件已是最新）")
                print(json.dumps({"commit": base_commit or ""}))
                return 0
            entries = []
            for rp in changed:
                blob = gh_api("POST", f"{prefix}/git/blobs", {
                    "content": base64.b64encode(target[rp]).decode(), "encoding": "base64"})
                entries.append({"path": rp, "mode": "100644", "type": "blob", "sha": blob["sha"]})
                log(f"  put {blob['sha'][:10]}  {len(target[rp]):>7}B  {rp}")
            check_deadline()
            for rp in removed:
                entries.append({"path": rp, "mode": "100644", "type": "blob", "sha": None})
                log(f"  del  {rp}")
            if seeded:
                entries.append({"path": ".seed", "mode": "100644", "type": "blob", "sha": None})
            tree_req = {"tree": entries}
            if base_tree:
                tree_req["base_tree"] = base_tree
            tree = gh_api("POST", f"{prefix}/git/trees", tree_req)
            msg = f"sync: {a.project} 第 {a.turn} 轮（{len(changed)} 改 / {len(removed)} 删）"
            commit_req = {"message": msg, "tree": tree["sha"]}
            if base_commit:
                commit_req["parents"] = [base_commit]
            commit = gh_api("POST", f"{prefix}/git/commits", commit_req)
            if base_commit:
                gh_api("PATCH", f"{prefix}/git/refs/heads/{branch}", {"sha": commit["sha"], "force": False})
            else:
                gh_api("POST", f"{prefix}/git/refs", {"ref": f"refs/heads/{a.branch}", "sha": commit["sha"]})
            with safe_open(project_state / "ghsync-cache.json", "w") as f:
                json.dump(digests, f, ensure_ascii=False, indent=1)
            log(f"已同步：commit {commit['sha'][:12]}  {repo}@{a.branch}")
            print(json.dumps({"commit": commit["sha"]}))
            return 0
    except (TimeoutError, subprocess.TimeoutExpired) as e:
        log(f"同步超时，本次未确认提交：{e}")
        return 3
    except Exception as e:
        log(f"同步失败：{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
