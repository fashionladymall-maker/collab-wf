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


# 1.5.8：驱动器自动安装工具版本时，改动了这些文件的版本必须由用户确认后手动安装。判断由当前已安装的代码在安装前做。
# 1.5.9：只剩底线文件 floor.py（底线属于资金、密钥、合规、实盘，只能由用户决定）；其余文件的改动由当前已安装的
# floor.probe 对候选版本做行为检查，再交给另一个模型系列审查（docs/REVIEW.md）。
GUARD_FILES = ("wflib/floor.py",)


def guard_changes(new_files: dict, cur_files: dict) -> list:
    """新版本相对当前安装改动了哪些护栏文件（新增、删除或内容不同）。"""
    return [f for f in GUARD_FILES if new_files.get(f) != cur_files.get(f)]


def _with_heartbeat(fn, limit_s: int, every_s: int):
    """1.6.9（P0-A）：在后台线程里运行 fn，期间每 every_s 秒向 stderr 输出一行心跳，让“在等”与“卡死”可区分。"""
    import threading
    import time
    box = {}

    def target():
        try:
            box["r"] = fn()
        except Exception as e:   # noqa: BLE001
            box["e"] = e
    print(f"开始等独立审查（上限 {limit_s} 秒；到时没有结论按“未审查”继续安装）", file=sys.stderr, flush=True)
    t0 = time.monotonic()
    th = threading.Thread(target=target, daemon=True)
    th.start()
    while True:
        th.join(every_s)
        if not th.is_alive():
            break
        print(f"仍在等独立审查：已 {int(time.monotonic() - t0)} 秒 / 上限 {limit_s} 秒", file=sys.stderr, flush=True)
    if "e" in box:
        raise box["e"]
    return box.get("r") or {}


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
    tmo = int(anomaly.transport_cfg().get("review_timeout_s", 900) or 900)
    hb = max(1, int(anomaly.transport_cfg().get("review_heartbeat_s", 30) or 30))
    r = _with_heartbeat(lambda: anomaly.call("review", ["--kind", "tool", "--project", "collab-wf", "--turn", "0",
                                                        "--release", zip_path, "--diff", diff]
                                             + anomaly.review_key_args("collab-wf", 0, zip_path, kind="tool"),   # 1.7.4
                                             timeout=tmo), tmo, hb)
    data = r.get("data") or {}
    if r.get("code") != 0 or data.get("verdict") not in ("pass", "concerns", "block"):
        why = (r.get("raw") or "")[:200] or f"审查程序退出码 {r.get('code')}"
        # 1.6.9（P0-A）：未审查照常安装（不变），但写明原因并留档，事后可按 diff 补审
        write_json(rd / f"{Path(zip_path).stem}.unreviewed.json",
                   {"ts": now_iso(), "release": str(zip_path), "diff": str(diff), "reason": why})
        print(f"本次未审查：{why}；照常安装，可事后按 {diff} 补审", file=sys.stderr, flush=True)
        return "unavailable", why
    print(f"独立审查结论：{data['verdict']}", file=sys.stderr, flush=True)
    write_json(rd / f"{Path(zip_path).stem}.json", data)
    return data["verdict"], "；".join(data.get("notes", [])[:5])


_UPDATE_LOCK = []


def _hold_update_lock() -> None:
    # 1.8.4（W78）：同一时间只允许一个 self-update（第 110 轮驱动器与执行端各跑了一次）。拿不到锁立即失败，不排队；
    # 锁随进程结束释放。self_update 的函数体保持不变（底线行为检查要看到它调用 guard_changes 与 floor.probe）。
    import fcntl
    from .core import LOCAL
    if _UPDATE_LOCK:
        return
    lp = WF_HOME / "local" / "locks" / "self-update.lock"   # 1.8.5（W82）：放在工具目录里，不随 WF_LOCAL_DIR 变
    lp.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lp, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise WfError("另一个 wf self-update 正在进行（同一时间只允许一个），本次没有安装（W78）")
    _UPDATE_LOCK.append(fh)


def self_update(zip_path, skip_test=False, allow_same=False, auto=False) -> dict:
    _hold_update_lock()   # 1.8.4（W78）
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
                raise WfError("这个版本改动了底线文件（" + "、".join(guard) + "），自动安装已拒绝："
                              "底线属于资金、密钥、合规、实盘，须由用户确认后手动运行 wf self-update")
            from . import floor   # 1.5.9：用已安装的底线用例检查候选版本的行为
            probs = floor.probe(src)
            if probs:
                raise WfError("候选版本没有通过底线行为检查，自动安装已拒绝：" + "；".join(probs[:5]))
            verdict, notes = _review_tool(src, zip_path)
            if verdict == "block":
                raise WfError(f"另一个模型系列审查认为应阻止，自动安装已拒绝：{notes}")
        if not skip_test:
            print(f"正在运行新版本 {new} 的完整自检", file=sys.stderr, flush=True)   # 1.6.9（P0-A）：长步骤先说一声
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
