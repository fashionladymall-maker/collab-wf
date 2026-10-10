#!/usr/bin/env python3
"""collab-wf → GitHub 公开同步（执行端同步程序）

工具 1.6.0 起把「项目上下文同步」做进了工具本体（见工具内 docs/SYNC.md）：
**工具**负责收集内容、密钥扫描、内容摘要比较；**本程序**只负责推送。

调用方式（工具在每次 wf tick 推进完、写 inbox 之前发起）：

    gh_sync.py --project <名称> --turn <轮次> --dir <暂存目录>

约定（docs/SYNC.md 第 6 节）：
- 把仓库更新成与暂存目录一致：新增、修改、删除；
- 暂存目录之外的文件，本程序保留自己的源码（scripts/ 下不删），其余删除；
- 成功时退出码 0，标准输出**最后一行**输出 {"commit": "<提交号>"}；
- 失败时退出码非 0；
- 时间上限由环境变量 WF_ADAPTER_TIMEOUT_S 给出（约定在上限前 30 秒自行返回）。

为什么不用 git push：本机 github.com 被阻断（TCP 通、数据收不到），
只有 api.github.com 畅通，所以走 REST API（blobs → tree → commit → ref）。

踩过的坑（不要再犯）：
- 加锁用 flock。**不要用「写文件 + 删文件」** —— 本机宿主安全守卫会拦下 unlink（EACCES），
  锁会永久残留，之后所有同步被静默跳过。
- 空仓库不允许直接建 blob（409 Git Repository is empty）→ 必须先用 Contents API 播种。
- 播种那一次之后，不能再往 tree 里塞 .seed 删除项，否则 422 GitRPC::BadObjectState。
"""
import argparse
import base64
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

OWNER = "fashionladymall-maker"
REPO = "collab-wf"
BRANCH = "main"

GH = "/opt/homebrew/bin/gh" if Path("/opt/homebrew/bin/gh").exists() else "gh"
HOME = Path("/Users/admin/.workbuddy-ai/executor")
CACHE = HOME / "ghsync-cache.json"
LOCK = HOME / "ghsync.lock"
LOG = HOME / "ghsync.log"

KEEP_PREFIXES = ("scripts/",)   # 这些路径下、暂存目录里没有的文件不删

# 时间上限（docs/SYNC.md 第 6 节：工具通过 WF_ADAPTER_TIMEOUT_S 给出上限，本程序在上限前 30 秒自行返回）。
# 工具那边的 run_with_grace 超时后先 SIGTERM、15 秒宽限再 SIGKILL —— 被 SIGKILL 会留下
# 「blob 传了一半、缓存没写」的半成品，所以必须自己看着表收工。
RESERVE_S = 30.0
MIN_BUDGET_S = 60.0
API_TIMEOUT_S = 60.0    # 单次 GitHub API 调用上限，避免一次卡死吃光整个预算


def deadline_from_env() -> float | None:
    """本次允许干到什么时候（None = 不设上限）。"""
    try:
        budget = float(os.environ.get("WF_ADAPTER_TIMEOUT_S") or 0)
    except ValueError:
        budget = 0.0
    if budget <= 0:
        return None
    return time.time() + max(MIN_BUDGET_S, budget - RESERVE_S)


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')}  {msg}"
    print(line, file=sys.stderr)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def gh_api(method, path, payload=None, allow_fail=False):
    args = [GH, "api", "--method", method, path]
    if payload is not None:
        args += ["--input", "-"]
        p = subprocess.run(args, input=json.dumps(payload), capture_output=True, text=True,
                           timeout=API_TIMEOUT_S)
    else:
        p = subprocess.run(args, capture_output=True, text=True, timeout=API_TIMEOUT_S)
    if p.returncode != 0:
        if allow_fail:
            return None
        raise RuntimeError(f"[API 失败] {method} {path}\n{(p.stderr or '').strip()}")
    out = (p.stdout or "").strip()
    return json.loads(out) if out else {}


def collect_stage(stage: Path) -> dict:
    """暂存目录 → {仓库内路径: bytes}。"""
    out = {}
    for p in sorted(stage.rglob("*")):
        if p.is_file():
            out[p.relative_to(stage).as_posix()] = p.read_bytes()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="collab-wf 项目上下文同步（推送端）")
    ap.add_argument("--project", required=True)
    ap.add_argument("--turn", default="")
    ap.add_argument("--dir", required=True, help="工具生成的暂存目录")
    a = ap.parse_args()

    HOME.mkdir(parents=True, exist_ok=True)
    stage = Path(a.dir)
    if not stage.is_dir():
        log(f"暂存目录不存在：{stage}")
        return 2
    deadline = deadline_from_env()

    target = collect_stage(stage)
    # 约定：同步程序保留自己的源码（scripts/gh_sync.py 由本程序自己维护）
    target["scripts/gh_sync.py"] = Path(__file__).resolve().read_bytes()

    lock_fd = open(LOCK, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("上一次同步仍在进行，跳过")
        lock_fd.close()
        return 0

    try:
        ref = gh_api("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}", allow_fail=True)
        base_commit = base_tree = None
        seeded = False
        if ref:
            base_commit = ref["object"]["sha"]
            base_tree = gh_api("GET", f"/repos/{OWNER}/{REPO}/git/commits/{base_commit}")["tree"]["sha"]
        else:
            seed = gh_api("PUT", f"/repos/{OWNER}/{REPO}/contents/.seed", {
                "message": "chore: 初始化仓库",
                "content": base64.b64encode(b"seed\n").decode(),
                "branch": BRANCH,
            })
            base_commit = seed["commit"]["sha"]
            base_tree = gh_api("GET", f"/repos/{OWNER}/{REPO}/git/commits/{base_commit}")["tree"]["sha"]
            seeded = True
            log("空仓库：已播种初始提交")

        remote = {}
        if base_tree:
            t = gh_api("GET", f"/repos/{OWNER}/{REPO}/git/trees/{base_tree}?recursive=1")
            remote = {e["path"]: e["sha"] for e in t.get("tree", []) if e.get("type") == "blob"}

        # 文件级变更检测（缓存 sha256）
        digests = {rp: hashlib.sha256(b).hexdigest() for rp, b in target.items()}
        old = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
        changed = sorted(rp for rp, d in digests.items() if old.get(rp) != d)
        removed = sorted(p for p in remote
                         if p not in target and p != ".seed"
                         and not p.startswith(KEEP_PREFIXES))

        if not changed and not removed and not seeded:
            log(f"无变化（{len(target)} 个文件已是最新）")
            print(json.dumps({"commit": base_commit or ""}))
            return 0

        entries = []
        for i, rp in enumerate(changed):
            if deadline and time.time() > deadline:
                log(f"时间上限将到：{len(changed)} 个待传文件只传了 {i} 个，本次不提交（下次接着传）")
                return 3
            blob = gh_api("POST", f"/repos/{OWNER}/{REPO}/git/blobs",
                          {"content": base64.b64encode(target[rp]).decode(), "encoding": "base64"})
            entries.append({"path": rp, "mode": "100644", "type": "blob", "sha": blob["sha"]})
            log(f"  put {blob['sha'][:10]}  {len(target[rp]):>7}B  {rp}")
        if deadline and time.time() > deadline:
            log("时间上限将到：文件已传完但来不及提交，本次不提交（下次接着传）")
            return 3
        for rp in removed:
            entries.append({"path": rp, "mode": "100644", "type": "blob", "sha": None})
            log(f"  del  {rp}")
        if seeded:
            entries.append({"path": ".seed", "mode": "100644", "type": "blob", "sha": None})

        tree_req = {"tree": entries}
        if base_tree:
            tree_req["base_tree"] = base_tree
        tree = gh_api("POST", f"/repos/{OWNER}/{REPO}/git/trees", tree_req)

        msg = f"sync: {a.project} 第 {a.turn} 轮（{len(changed)} 改 / {len(removed)} 删）"
        commit_req = {"message": msg, "tree": tree["sha"]}
        if base_commit:
            commit_req["parents"] = [base_commit]
        commit = gh_api("POST", f"/repos/{OWNER}/{REPO}/git/commits", commit_req)

        if base_commit:
            gh_api("PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}",
                   {"sha": commit["sha"], "force": False})
        else:
            gh_api("POST", f"/repos/{OWNER}/{REPO}/git/refs",
                   {"ref": f"refs/heads/{BRANCH}", "sha": commit["sha"]})

        CACHE.write_text(json.dumps(digests, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"已同步：commit {commit['sha'][:12]}  https://github.com/{OWNER}/{REPO}")
        print(json.dumps({"commit": commit["sha"]}))     # 约定：最后一行 JSON
        return 0
    except Exception as e:                                # noqa: BLE001
        log(f"同步失败：{e}")
        return 1
    finally:
        # 只关 fd：flock 随 fd 关闭自动释放。绝不 unlink 锁文件。
        lock_fd.close()


if __name__ == "__main__":
    sys.exit(main())
