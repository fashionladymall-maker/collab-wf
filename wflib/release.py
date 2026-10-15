"""版本管理：项目版本落地与回滚；工具自身的初始化、升级与回滚。
设计要点：先校验、后复制（校验失败时仓库不被改动）；删除只按交付清单进行、用 Python 完成（不调用 rm）；
回滚生成新的提交而不改写历史。"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from .core import (WF_HOME, Instance, WfError, git, git_identity, matches_protect, now_iso, pid_alive,
                   read_json, tool_version, write_json)
from .projectmd import parse_project_md, validate
from . import fidelity, review


def _digest(obj) -> str:
    return review._sha(review._canon(obj))


def _install_store(root: Path, kind: str, *, create=True) -> Path:
    """目标自有的元数据，不能随调用方 LOCAL 切换；每级目录均拒绝链接。"""
    root = Path(root).resolve(strict=True)
    parts = ("local", "install") if kind == "tool" else ("workflow", "evidence", "install")
    cur = root
    for part in parts:
        cur = cur / part
        if create:
            try:
                cur.mkdir(mode=0o700)
                for directory in (cur, cur.parent):
                    fd = os.open(str(directory), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
            except FileExistsError:
                pass
        if not stat.S_ISDIR(os.lstat(cur).st_mode):
            raise WfError(f"安装元数据目录不是普通目录：{cur}")
    return cur


def _record_new(path: Path, body: dict) -> dict:
    """append-only、hash 封装、fsync；记录失败绝不等价于成功。调用者持目标锁。"""
    doc = dict(body, body_sha256=_digest(body))
    review._write_new(path, review._canon(doc), 0o400)
    try:
        for p in (path, path.parent, path.parent.parent):
            fd = os.open(str(p), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    except BaseException:
        path.unlink()
        raise
    return doc


def _read_record(path: Path) -> dict:
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise WfError(f"安装记录类型不符：{path}")
    doc = json.loads(path.read_bytes())
    if not isinstance(doc, dict) or doc.get("body_sha256") != _digest(
            {k: v for k, v in doc.items() if k != "body_sha256"}):
        raise WfError(f"安装记录 hash 不符：{path}")
    return doc


class _InstallLock:
    """先取目标锁，再进 review.consume 的短锁；持至结果核验与终态记录完成。"""

    def __init__(self, root, kind, *, recovery=False):
        self.root = Path(root).resolve(strict=True)
        self.kind = kind
        self.recovery = recovery
        self.fd = None

    def __enter__(self):
        import fcntl
        try:
            if not os.path.lexists(self.root / ".git"):
                raise WfError("受保护安装要求先显式初始化 Git（install_requires_git）")
            self.root_identity = (os.lstat(self.root).st_dev, os.lstat(self.root).st_ino)
            self.gitdir = self._gitdir()
            self.gitdir_identity = (os.lstat(self.gitdir).st_dev, os.lstat(self.gitdir).st_ino)
            lock_dir = self.gitdir / "wf-install"
            lock_dir.mkdir(mode=0o700, exist_ok=True)
            if not stat.S_ISDIR(os.lstat(lock_dir).st_mode):
                raise WfError("安装锁目录不是普通目录")
            self.lockdir_identity = (os.lstat(lock_dir).st_dev, os.lstat(lock_dir).st_ino)
            self.path = lock_dir / (review._sha(os.fsencode(str(self.root))) + ".lock")
            self.fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            if not stat.S_ISREG(os.fstat(self.fd).st_mode):
                raise WfError("安装锁不是普通文件")
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise WfError("同一目标已有安装/恢复进行（install_busy）") from e
            # 先只持有 Git 共享锁；请求/材料拒绝不创建目标用户可见元数据目录。
            self.store = None
            self.store_identity = None
            self.token = secrets.token_hex(16)
            self.acquired_at = review._iso(review._now())
            self.verify()
            self._check_incomplete()
            return self
        except BaseException:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            raise

    def _gitdir(self):
        if stat.S_ISLNK(os.lstat(self.root / ".git").st_mode):
            raise WfError("目标 .git 不允许符号链接（install_git_changed）")
        return Path(git(["rev-parse", "--absolute-git-dir"], self.root).stdout.strip()).resolve(strict=True)

    def verify(self):
        at_root = os.lstat(self.root)
        if (at_root.st_dev, at_root.st_ino) != self.root_identity or self._gitdir() != self.gitdir:
            raise WfError("安装目标/Git 身份改变（install_git_changed）")
        at_git = os.lstat(self.gitdir)
        at_lockdir = os.lstat(self.path.parent)
        if ((at_git.st_dev, at_git.st_ino) != self.gitdir_identity
                or (at_lockdir.st_dev, at_lockdir.st_ino) != self.lockdir_identity
                or not stat.S_ISDIR(at_lockdir.st_mode)):
            raise WfError("安装 Git/锁目录被替换（install_git_changed）")
        st = os.fstat(self.fd)
        at = os.lstat(self.path)
        if (st.st_dev, st.st_ino) != (at.st_dev, at.st_ino) or st.st_nlink != 1:
            raise WfError("安装锁已被替换（install_lock_changed）")
        if self.store is not None:
            cur = _install_store(self.root, self.kind, create=False)
            st_dir = os.lstat(cur)
            if (st_dir.st_dev, st_dir.st_ino) != self.store_identity:
                raise WfError("安装元数据目录已变")

    def _check_incomplete(self):
        if git(["ls-files", "--", "workflow/evidence/install", "local/install"], self.root).stdout.strip():
            raise WfError("安装元数据不得被 Git 跟踪（install_metadata_tracked）")
        stores = {}
        for kind in ("project", "tool"):
            path = self.root / ("workflow/evidence/install" if kind == "project" else "local/install")
            if os.path.lexists(path):
                stores[kind] = _install_store(self.root, kind, create=False)
        for kind, directory in stores.items():
            for job in sorted(directory.glob("restore-*")):
                if not stat.S_ISDIR(os.lstat(job).st_mode):
                    raise WfError("回滚事务目录类型损坏（install_incomplete）")
                prepared = _read_record(job / "prepared.json")
                if (prepared.get("schema") != "install_restore/1" or prepared.get("restore_id") != job.name
                        or prepared.get("target_root") != str(self.root) or prepared.get("kind") != kind):
                    raise WfError("回滚事务身份损坏（install_incomplete）")
                if os.path.lexists(job / "partial.json"):
                    partial = _read_record(job / "partial.json")
                    if (partial.get("prepared_sha256") != prepared["body_sha256"]
                            or partial.get("restore_id") != job.name or partial.get("state") != "partial"):
                        raise WfError("回滚失败记录损坏（install_incomplete）")
                if os.path.lexists(job / "completed.json"):
                    completed = _read_record(job / "completed.json")
                    if (completed.get("state") != "completed" or completed.get("restore_id") != job.name
                            or completed.get("prepared_sha256") != prepared["body_sha256"]
                            or completed.get("inventory_sha256") != prepared["inventory_sha256"]):
                        raise WfError("回滚完成证据损坏（install_incomplete）")
                else:
                    if not self.recovery:
                        raise WfError(f"上次回滚未闭合（install_incomplete）：{job.name}")
            for p in sorted(directory.glob("tx-*")):
                if not stat.S_ISDIR(os.lstat(p).st_mode):
                    raise WfError("安装事务目录类型损坏（install_incomplete）")
                if os.path.lexists(p / "recovered.json"):
                    baseline = _verified_baseline(self, p, kind)
                    recovered = _read_record(p / "recovered.json")
                    if (recovered.get("schema") != "install_recovered/1"
                            or recovered.get("tx_id") != p.name
                            or recovered.get("baseline_sha256") != baseline["body_sha256"]
                            or recovered.get("inventory_sha256") != baseline["inventory_sha256"]
                            or recovered.get("state") != "recovered"):
                        raise WfError("恢复记录损坏（install_incomplete）")
                    continue
                if not os.path.lexists(p / "prepared.json"):
                    if self.recovery:
                        _verified_baseline(self, p, kind)
                        continue
                    raise WfError(f"上次安装准备未闭合（install_incomplete）：{p.name}")
                prepared = _read_record(p / "prepared.json")
                if (prepared.get("schema") != "install_tx/1" or prepared.get("target_root") != str(self.root)
                        or prepared.get("kind") != kind or prepared.get("tx_id") != p.name):
                    raise WfError("安装事务身份损坏（install_incomplete）")
                terminal = [p / n for n in ("completed.json", "failed.json", "partial.json") if os.path.lexists(p / n)]
                if "baseline_sha256" in prepared:
                    _verified_baseline(self, p, kind)
                if len(terminal) != 1:
                    if self.recovery and not terminal:
                        _verified_baseline(self, p, kind)
                        continue
                    raise WfError(f"上次安装未闭合，须先核验恢复（install_incomplete）：{p.name}")
                rec = _read_record(terminal[0])
                if (rec.get("prepared_sha256") != prepared["body_sha256"] or rec.get("tx_id") != p.name
                        or rec.get("state") not in ("completed", "failed", "partial")):
                    raise WfError(f"上次安装不完整，须先恢复（install_incomplete）：{p.name}")
                if rec["state"] == "partial":
                    if self.recovery:
                        _verified_baseline(self, p, kind)
                        continue
                    raise WfError(f"上次安装不完整，须先恢复（install_incomplete）：{p.name}")
                if rec["state"] == "completed":
                    started = _read_record(p / "started.json")
                    if (rec.get("schema") != "install_completed/1"
                            or started.get("prepared_sha256") != prepared["body_sha256"]
                            or rec.get("started_sha256") != started["body_sha256"]
                            or started.get("decision_sha256") != _digest(started.get("decision"))
                            or rec.get("target") != prepared["request"]["target"]
                            or rec.get("package_sha256") != prepared["package_sha256"]
                            or rec["actual"].get("tree_sha256") != _digest(rec["actual"]["files"])):
                        raise WfError(f"安装完成证据不符（install_incomplete）：{p.name}")

    def activate(self):
        if self.store is None:
            self.store = _install_store(self.root, self.kind)
            self.store_identity = (os.lstat(self.store).st_dev, os.lstat(self.store).st_ino)
            self.verify()
            self._check_incomplete()
        return self
    def __exit__(self, *a):
        import fcntl
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


def _inventory(root: Path, kind, protect) -> list:
    exclude = review._tool_excluded if kind == "tool" else (
        lambda r, d: r == ".git" or matches_protect(r + ("/" if d else ""), protect)
        or matches_protect(r, protect))
    rows = review._walk_states(root, exclude=exclude)
    if any(r["type"] not in ("file", "dir", "symlink") for r in rows):
        raise WfError("安装实际树有未知/不安全路径（install_inventory_unknown）")
    return rows


def _config_bytes(cfg) -> bytes:
    # 与 core.write_json 的实际编码一致；不使用缓存配置作为安装过滤语义。
    return json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8")


def _fresh_instance(root: Path, req=None) -> Instance:
    inst = Instance(root)
    raw = inst.cfg_path.read_bytes()
    if json.loads(raw) != inst.cfg:
        raise WfError("读取项目配置期间内容改变（config_changed）")
    if req is not None and (review._sha(raw) != req["base"]["config_sha256"]
                            or inst.protect() != req["base"]["protect"]):
        raise WfError("项目配置与最终审查材料不一致（config_changed）")
    return inst


def _install_plan(req, pkg, src, root, kind, cfg=None, archive=None) -> dict:
    """按 copy → delete → 配置/归档的真实顺序构建结果，包含未被包覆盖的原树。"""
    protect = req["base"]["protect"]
    before = _inventory(root, kind, protect)
    want = {r["path"]: dict(r) for r in before}

    def parents(rel):
        for parent in reversed(Path(rel).parents):
            name = parent.as_posix()
            if name == ".":
                continue
            if name not in want:
                want[name] = {"path": name, "type": "dir", "mode": None}
            elif want[name]["type"] != "dir":
                raise WfError(f"安装目标上级不是目录：{name}")

    for e in pkg["entries"]:
        if e["type"] == "file" and e["installed"]:
            parents(e["path"])
            want[e["path"]] = {"path": e["path"], "type": "file", "size": e["size"],
                               "sha256": e["sha256"], "mode": stat.S_IMODE((src / e["path"]).stat().st_mode)}
    delete = (pkg["release"] or {}).get("delete", [])
    for rel in delete:
        rel = rel.rstrip("/")
        for path in list(want):
            if path == rel or path.startswith(rel + "/"):
                del want[path]
    cfg_after = None
    if cfg is not None:
        cfg_after = json.loads(json.dumps(cfg))
        changed = False
        for key, value in (pkg["release"] or {}).get("wf_config", {}).items():
            if key in ("processes", "protect", "hook"):
                cfg_after[key] = value
                changed = True
        config_sha = review._sha(_config_bytes(cfg_after) if changed else (root / "workflow/wf.json").read_bytes())
        if archive and pkg["release"]:
            parents(archive)
            raw = pkg["data"]["RELEASE.json"]
            want[archive] = {"path": archive, "type": "file", "mode": stat.S_IMODE((src / "RELEASE.json").stat().st_mode),
                             "size": len(raw), "sha256": review._sha(raw)}
    else:
        config_sha = None
        # 不通过 write_manifest 修补送审包；冻结包必须交付完整、匹配合并结果的清单。
        if "MANIFEST.json" not in pkg["data"] or "MANIFEST.json" not in want:
            raise WfError("工具包必须交付 MANIFEST.json（manifest_required）")
        manifest = json.loads(pkg["data"]["MANIFEST.json"])
        hashes = {r: s["sha256"] for r, s in want.items() if s["type"] == "file" and r != "MANIFEST.json"}
        version = pkg["data"].get("VERSION", b"").decode("utf-8").strip()
        if not isinstance(manifest, dict) or manifest.get("version") != version or manifest.get("files") != hashes:
            raise WfError("工具清单与已审查安装结果不一致（manifest_mismatch）")
    return {"before": before, "expected": sorted(want.values(), key=lambda r: r["path"]), "delete": delete,
            "config_after": cfg_after, "config_sha256": config_sha}


def _verify_install(root, kind, protect, plan) -> dict:
    actual = _inventory(root, kind, protect)
    want = {r["path"]: r for r in plan["expected"]}
    got = {r["path"]: r for r in actual}
    if set(want) != set(got):
        raise WfError("安装结果路径集合与审查计划不一致（install_tree_mismatch）")
    for rel, expected in want.items():
        for key, val in expected.items():
            if key == "mode" and val is None:  # 新建目录 mode 不在 B2a 的包语义中，完成记录仍保存实际值。
                continue
            if got[rel].get(key) != val:
                raise WfError(f"安装结果与审查计划不一致（install_tree_mismatch）：{rel}/{key}")
    config_sha = None
    if kind == "project":
        config_sha = review._sha((root / "workflow/wf.json").read_bytes())
        if config_sha != plan["config_sha256"]:
            raise WfError("安装配置与审查计划不一致（install_config_mismatch）")
    deleted = []
    for rel in plan["delete"]:
        st, _ = review._state_of(root, rel.rstrip("/"))
        # 配置/归档是删除后的既定写入；其余删除目标必须消失。
        if st["type"] != "absent" and rel.rstrip("/") not in got:
            raise WfError(f"删除结果不一致：{rel}")
        deleted.append(st)
    control = review._control(review._target(root, kind))
    if any(s["type"] in ("unknown", "unsafe", "special") for s in control):
        raise WfError("安装后控制文件不可核验")
    return {"tree_sha256": _digest(actual), "files": actual, "deleted": deleted,
            "config_sha256": config_sha, "control": control}


def _refs(root):
    refs = {}
    for name in git(["for-each-ref", "--format=%(refname:short)", "refs/tags"], root).stdout.splitlines():
        refs[name] = git(["rev-parse", "--verify", name + "^{commit}"], root).stdout.strip()
    return refs


def _snapshot(lock, directory, protect):
    """只保存安装可写树；保护树不复制、不用于恢复，控制文件另作身份核验。"""
    rows = _inventory(lock.root, lock.kind, protect)
    blobs = directory / "blobs"
    blobs.mkdir(mode=0o700, exist_ok=True)
    for row in rows:
        if row["type"] != "file":
            continue
        state, data = review._state_of(lock.root, row["path"])
        if state != row:
            raise WfError("保存恢复基线时文件改变（baseline_changed）")
        dest = blobs / row["sha256"]
        if not dest.exists():
            review._write_new(dest, data, 0o400)
        if review._sha(dest.read_bytes()) != row["sha256"]:
            raise WfError("恢复基线字节不符（rollback_target_unverified）")
        fd = os.open(str(dest), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    fd = os.open(str(blobs), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    if rows != _inventory(lock.root, lock.kind, protect):
        raise WfError("保存恢复基线时目标树改变（baseline_changed）")
    return rows


def _identity(root):
    return {"commit": git(["rev-parse", "--verify", "HEAD^{commit}"], root).stdout.strip(),
            "tree": git(["rev-parse", "--verify", "HEAD^{tree}"], root).stdout.strip()}


def _new_baseline(lock, path, tx_id, req):
    target = review._target(lock.root, lock.kind)
    identity = _identity(lock.root)
    if identity["commit"] != req["base"]["commit"] or identity["tree"] != req["base"]["tree"]:
        raise WfError("基线 Git 与审查目标不一致（baseline_changed）")
    rows = _snapshot(lock, path, target["protect"])
    all_states = review._walk_states(lock.root, exclude=lambda r, d: r == ".git"
        or r == "local/install" or r == "workflow/evidence/install")
    protected = [r for r in all_states if matches_protect(r["path"], target["protect"])
                 or (r["type"] == "dir" and matches_protect(r["path"] + "/", target["protect"]))]
    if any(r["type"] in ("unknown", "unsafe", "special") for r in protected):
        raise WfError("保护路径状态不能作为已验证基线（baseline_changed）")
    config = None
    config_state = None
    if lock.kind == "project":
        state, data = review._state_of(lock.root, "workflow/wf.json")
        if state["type"] != "file" or state["sha256"] != target["config_sha256"]:
            raise WfError("基线配置不安全（baseline_changed）")
        # 配置仅供失败事务撤销自身允许的配置合并；普通回滚永不写 protected。
        config = data.decode("utf-8")
        config_state = state
    touched = req["base"]["touched"]
    runtime = None
    if lock.kind == "tool":
        from . import server
        try:
            info = server.info()
            runtime = dict({k: info.get(k) for k in ("running", "pid", "port", "version", "stale")}, known=True)
        except Exception as e:
            runtime = {"known": False, "error": f"{type(e).__name__}: {e}"}
    return _record_new(path / "baseline.json", {
        "schema": "baseline/1", "baseline_id": "baseline-" + secrets.token_hex(16),
        "tx_id": tx_id, "target": target, "identity": identity, "refs": _refs(lock.root),
        "protect_sha256": _digest(target["protect"]), "config_sha256": target["config_sha256"],
        "config": config, "config_state": config_state, "paths": rows, "inventory_sha256": _digest(rows),
        "protected_paths": protected, "protected_paths_sha256": _digest(protected),
        "control": review._control(target), "touched": touched, "runtime": runtime,
        "delete_closure_sha256": _digest([s for s in touched if s["role"] == "delete"]),
        "verified_at": review._iso(review._now()), "source": "preinstall-verified", "status": "verified"})


def _verify_snapshot(root, directory, identity, rows):
    commit = identity.get("commit", "")
    if (not isinstance(commit, str) or len(commit) != 40
            or git(["rev-parse", "--verify", commit + "^{commit}"], root).stdout.strip() != commit
            or git(["rev-parse", "--verify", commit + "^{tree}"], root).stdout.strip() != identity.get("tree")):
        raise WfError("恢复 commit/tree 不符（rollback_target_unverified）")
    seen = set()
    for row in rows:
        rel = row.get("path", "")
        if (not rel or rel in seen or rel.startswith("/") or any(x in ("", ".", "..") for x in rel.split("/"))
                or row.get("type") not in ("file", "dir", "symlink")):
            raise WfError("恢复路径不合法（rollback_target_unverified）")
        seen.add(rel)
        if row["type"] == "file":
            blob = directory / "blobs" / row["sha256"]
            if (not stat.S_ISREG(os.lstat(blob).st_mode)
                    or review._sha(blob.read_bytes()) != row["sha256"]):
                raise WfError("恢复文件快照不符（rollback_target_unverified）")
    # 记录的 tracked 文件必须确实来自绑定的不可变 tree；ignored/untracked 由单独 blob 绑定。
    entries = git(["ls-tree", "-rz", "--full-tree", commit], root).stdout.split("\0")
    bypath = {r["path"]: r for r in rows}
    for entry in filter(None, entries):
        meta, rel = entry.split("\t", 1)
        row = bypath.get(rel)
        if row is None:
            continue
        mode, typ, oid = meta.split()
        if typ != "blob":
            raise WfError("基线含不支持 Git 类型（rollback_target_unverified）")
        data = subprocess.run(["git", "cat-file", "blob", oid], cwd=root, capture_output=True, check=True).stdout
        if mode == "120000":
            valid = row["type"] == "symlink" and os.fsencode(row["target"]) == data
        else:
            valid = (row["type"] == "file" and review._sha(data) == row["sha256"]
                     and bool(row["mode"] & 0o111) == (mode == "100755"))
        if not valid:
            raise WfError("恢复清单与 Git tree 不符（rollback_target_unverified）")


def _verified_baseline(lock, path, kind=None):
    baseline = _read_record(path / "baseline.json")
    target = baseline.get("target", {})
    if (baseline.get("schema") != "baseline/1" or baseline.get("status") != "verified"
            or baseline.get("source") != "preinstall-verified" or baseline.get("tx_id") != path.name
            or target.get("root") != str(lock.root) or target.get("kind") != (kind or lock.kind)
            or baseline.get("protect_sha256") != _digest(target.get("protect"))
            or baseline.get("inventory_sha256") != _digest(baseline.get("paths"))
            or baseline.get("protected_paths_sha256") != _digest(baseline.get("protected_paths"))
            or baseline.get("config_sha256") != target.get("config_sha256")
            or baseline.get("delete_closure_sha256") != _digest(
                [s for s in baseline.get("touched", []) if s["role"] == "delete"])):
        raise WfError("安装前基线不完整（rollback_target_unverified）")
    if target["kind"] == "project" and review._sha(baseline["config"].encode("utf-8")) != baseline["config_sha256"]:
        raise WfError("基线配置快照不符（rollback_target_unverified）")
    if target["kind"] == "project" and (baseline.get("config_state", {}).get("sha256") != baseline["config_sha256"]
                                       or baseline["config_state"].get("type") != "file"):
        raise WfError("基线配置状态不符（rollback_target_unverified）")
    _verify_snapshot(lock.root, path, baseline["identity"], baseline["paths"])
    if os.path.lexists(path / "prepared.json"):
        prepared = _read_record(path / "prepared.json")
        if (prepared.get("baseline_sha256") != baseline["body_sha256"]
                or prepared.get("baseline_id") != baseline["baseline_id"]
                or prepared.get("tx_id") != path.name):
            raise WfError("基线与安装事务脱离（rollback_target_unverified）")
    return baseline


def _select_restore_target(lock, selector):
    """标签只是已登记身份的名称；未登记 commit、任意新标签及移动标签均无授权。"""
    directory = _install_store(lock.root, lock.kind, create=False)
    commit = None
    if not str(selector).startswith("baseline-"):
        ref = _find_ref(lock.root, str(selector))
        commit = git(["rev-parse", "--verify", ref + "^{commit}"], lock.root).stdout.strip()
        if git(["show-ref", "--verify", "--quiet", "refs/tags/" + str(selector)],
               lock.root, check=False).returncode == 0:
            for recorded_path in directory.glob("tx-*"):
                for name in ("baseline.json", "completed.json"):
                    if os.path.lexists(recorded_path / name):
                        recorded = _read_record(recorded_path / name)
                        old = recorded.get("refs", {}).get(str(selector))
                        if old is not None and old != commit:
                            raise WfError("标签已偏离登记身份（rollback_target_unverified）")
    for path in sorted(directory.glob("tx-*")):
        baseline = _verified_baseline(lock, path)
        candidates = [(baseline["baseline_id"], baseline["identity"], baseline["paths"], baseline,
                       baseline["refs"], baseline["target"]["protect"])]
        if os.path.lexists(path / "completed.json"):
            completed = _read_record(path / "completed.json")
            prepared = _read_record(path / "prepared.json")
            started = _read_record(path / "started.json")
            actual = completed.get("actual", {})
            if (completed.get("schema") != "install_completed/1" or completed.get("state") != "completed"
                    or completed.get("tx_id") != path.name
                    or completed.get("prepared_sha256") != prepared["body_sha256"]
                    or completed.get("started_sha256") != started["body_sha256"]
                    or started.get("prepared_sha256") != prepared["body_sha256"]
                    or started.get("decision_sha256") != _digest(started.get("decision"))
                    or actual.get("tree_sha256") != _digest(actual.get("files"))
                    or completed.get("baseline_sha256") != baseline["body_sha256"]):
                raise WfError("成功安装证据不完整（rollback_target_unverified）")
            _verify_snapshot(lock.root, path, actual, actual["files"])
            candidates.append((path.name, actual, actual["files"], completed, completed["refs"],
                               completed["protect"]))
        for bid, identity, rows, record, refs, protect in candidates:
            named = selector == bid
            if not named and commit == identity["commit"]:
                named = (str(selector) == commit or refs.get(str(selector)) == commit
                         or refs.get("wf-v" + str(selector).lstrip("v")) == commit)
            if named:
                return {"path": path, "baseline": baseline, "identity": identity, "paths": rows,
                        "record": record, "protect": protect, "is_baseline": record is baseline}
    raise WfError("目标没有完整安装/基线授权（rollback_target_unverified）")


def _restore_snapshot(lock, selected, protect, message, *, preflight=False):
    """先完成全部 nofollow/protect 预检，再写精确快照；不执行 checkout。"""
    lock.verify()
    rows = selected["paths"]
    _verify_snapshot(lock.root, selected["path"], selected["identity"], rows)
    # 用原始完整树判断 protected，不能以已过滤 inventory 隐藏待覆盖 protected。
    current = review._walk_states(lock.root, exclude=lambda r, d: r == ".git"
        or r == "local/install" or r.startswith("local/install/")
        or r == "workflow/evidence/install" or r.startswith("workflow/evidence/install/"))
    if any(r["type"] not in ("file", "dir", "symlink") for r in current):
        raise WfError("恢复现场不可核验（rollback_target_unverified）")
    target = {r["path"]: r for r in rows}
    before = {r["path"]: r for r in current}
    # 删/改只涉及安装树，当前保护内容保留；目标欲写入当前保护路径则整次拒绝。
    changing = []
    for rel, row in target.items():
        if before.get(rel) != row:
            changing.append(rel)
    original = {r["path"]: r for r in _inventory(lock.root, lock.kind, selected["protect"])}
    removing = set(original) - set(target)
    for rel in changing + sorted(removing):
        if (matches_protect(rel, protect) or matches_protect(rel + "/", protect)
                or any(matches_protect(name + "/", protect) or matches_protect(name, protect)
                       for name in (p.as_posix() for p in Path(rel).parents) if name != ".")):
            raise WfError(f"回滚将改动受保护路径（rollback_protected）：{rel}")
        for parent in Path(rel).parents:
            name = parent.as_posix()
            if name == ".":
                continue
            st = before.get(name)
            if st and st["type"] != "dir" and name not in target:
                raise WfError(f"恢复上级不是目录（rollback_target_unverified）：{name}")
    # 目录替换/删除不得暗中递归删除任何保留或 protected 后代。
    replace_dirs = {r for r in changing if before.get(r, {}).get("type") == "dir"
                    and target[r]["type"] != "dir"} | {r for r in removing if before[r]["type"] == "dir"}
    for rel in replace_dirs:
        if any(p.startswith(rel + "/") and p not in removing for p in before):
            raise WfError(f"恢复目录含保留路径（rollback_protected）：{rel}")
    if preflight:
        return
    lock.verify()
    remove = removing | {r for r in changing if r in before and before[r]["type"] != target[r]["type"]}
    removed = []
    for rel in sorted(remove, key=lambda p: (p.count("/"), p), reverse=True):
        p = lock.root / rel
        if os.path.lexists(p):
            if stat.S_ISDIR(os.lstat(p).st_mode):
                p.rmdir()
            else:
                p.unlink()
            removed.append(rel)
    for row in sorted(rows, key=lambda r: (r["path"].count("/"), r["path"])):
        rel = row["path"]
        if rel not in changing:
            continue
        p = lock.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if row["type"] == "dir":
            p.mkdir(exist_ok=True)
            p.chmod(row["mode"])
        elif row["type"] == "symlink":
            if os.path.lexists(p):
                p.unlink()
            p.symlink_to(row["target"])
        else:
            _restore_file(p, (selected["path"] / "blobs" / row["sha256"]).read_bytes(), row["mode"])
    lock.verify()
    actual = _inventory(lock.root, lock.kind, selected["protect"])
    if actual != rows:
        raise WfError("恢复后树与基线不一致（rollback_tree_mismatch）")
    head, changed = _commit(lock.root, message)
    return head, changed, removed


def _restore_file(path, data, mode):
    """原子替换普通文件，避免 VERSION/config 在中断点消失；也不写穿现有 hardlink。"""
    temp = path.parent / (".wf-restore-" + secrets.token_hex(16) + ".tmp")
    try:
        review._write_new(temp, data, mode)
        temp.chmod(mode)
        fd = os.open(str(temp), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, path)
        fd = os.open(str(path.parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.lexists(temp):
            temp.unlink()


def _recover_tx(lock, path):
    baseline = _verified_baseline(lock, path)
    protect = baseline["target"]["protect"]
    restore_config = False
    if lock.kind == "project":
        cfg_path = lock.root / "workflow/wf.json"
        state, raw = review._state_of(lock.root, "workflow/wf.json")
        if state["type"] != "file":
            raise WfError("恢复配置类型不安全（rollback_protected）")
        if state != baseline["config_state"]:
            prepared = _read_record(path / "prepared.json")
            after = prepared["plan"]["config_sha256"]
            if review._sha(raw) != after or state["mode"] not in (0o600, baseline["config_state"]["mode"]):
                raise WfError("事务之外的配置变更不可恢复（rollback_protected）")
            restore_config = True
    current_control = review._control(review._target(lock.root, lock.kind))
    ignore = ("workflow/wf.json",) if restore_config else ()
    if ([r for r in current_control if r["path"] not in ignore]
            != [r for r in baseline["control"] if r["path"] not in ignore]):
        # 工具 VERSION/MANIFEST 正是安装内容，由下方完整树恢复核验。
        if lock.kind != "tool":
            raise WfError("事务之外的控制文件变更不可恢复（rollback_control_mismatch）")
    selected = {"path": path, "identity": baseline["identity"], "paths": baseline["paths"],
                "protect": baseline["target"]["protect"]}
    _restore_snapshot(lock, selected, protect, f"recover {path.name}", preflight=True)
    if restore_config:
        # 唯一允许的保护文件写入：预检通过后撤销本事务已审查的 wf_config 合并。
        _restore_file(cfg_path, baseline["config"].encode("utf-8"), baseline["config_state"]["mode"])
    head, changed, removed = _restore_snapshot(lock, selected, protect, f"recover {path.name}")
    if review._control(review._target(lock.root, lock.kind)) != baseline["control"]:
        raise WfError("恢复控制文件与基线不符（rollback_control_mismatch）")
    runtime = None
    if lock.kind == "tool":
        from . import server
        expected = baseline.get("runtime") or {}
        try:
            restarted = _restart_board()
            info = server.info()
            needed = bool(expected.get("running") or expected.get("pid"))
            runtime = {"restart_attempted": True, "restart_result": restarted,
                       "expected_running": needed, "disk_version": tool_version(),
                       "observed": {k: info.get(k) for k in ("running", "pid", "version", "stale")},
                       "verified": bool(expected.get("known")) and (
                           (bool(info.get("running")) and info.get("version") == tool_version()
                            and not info.get("stale")) if needed else not bool(info.get("pid")))}
        except Exception as e:
            runtime = {"restart_attempted": True, "verified": False, "error": f"{type(e).__name__}: {e}"}
    if not os.path.lexists(path / "recovered.json"):
        _record_new(path / "recovered.json", {
            "schema": "install_recovered/1", "state": "recovered", "tx_id": path.name,
            "baseline_id": baseline["baseline_id"], "baseline_sha256": baseline["body_sha256"],
            "verified_at": review._iso(review._now()), "actual": _identity(lock.root),
            "inventory_sha256": _digest(_inventory(lock.root, lock.kind, protect)),
            "runtime": runtime,
            "boundary": "仅核验安装可写树与控制文件；不宣称 protected/ignored/untracked/空目录的全文件系统恢复"})
    return head, changed, removed


class _InstallTx:
    def __init__(self, lock, req, src, plan, decision):
        lock.activate().verify()
        self.lock, self.req, self.plan = lock, req, plan
        self.tx_id = "tx-" + secrets.token_hex(16)
        self.path = lock.store / self.tx_id
        self.path.mkdir(mode=0o700)
        self.started = False
        self.baseline = _new_baseline(lock, self.path, self.tx_id, req)
        self.prepared = _record_new(self.path / "prepared.json", {
            "schema": "install_tx/1", "state": "prepared", "tx_id": self.tx_id,
            "request_id": req["request_id"], "content_id": req["content_id"], "kind": req["kind"],
            "target_root": str(lock.root), "target_scope": req["target"]["scope"],
            "package_sha256": req["package"]["sha256"], "material_sha256": req["material_sha256"],
            "baseline_id": self.baseline["baseline_id"], "baseline_sha256": self.baseline["body_sha256"],
            "base": req["base"], "rules_sha256": req["rules"]["sha256"],
            "protect_sha256": _digest(req["base"]["protect"]), "extracted_root": str(src),
            "delete_closure_sha256": _digest([s for s in req["base"]["touched"] if s["role"] == "delete"]),
            "lock": {"path": str(lock.path), "token": lock.token, "acquired_at": lock.acquired_at},
            "times": decision["times"], "decision_sha256": _digest(decision), "request": req, "plan": plan})

    def consume(self, target, src):
        self.lock.verify()
        decision = review.consume(self.req["request_id"], target=target, extracted_dir=src,
                                  install_context={"tx_id": self.tx_id, "target_root": str(self.lock.root),
                                                   "lock_token": self.lock.token,
                                                   "prepared_sha256": self.prepared["body_sha256"]})
        # 即使后续 started 记录写失败，也不能把已开始的消费当作成功。
        self.started = True
        self.decision = decision
        self.started_record = _record_new(self.path / "started.json", {
            "schema": "install_tx/1", "state": "started", "tx_id": self.tx_id,
            "prepared_sha256": self.prepared["body_sha256"], "decision": decision,
            "decision_sha256": _digest(decision), "times": decision["times"]})
        self.lock.verify()

    def complete(self, actual):
        self.lock.verify()
        rows = _snapshot(self.lock, self.path, self.req["base"]["protect"])
        if rows != actual["files"]:
            raise WfError("保存成功安装恢复快照时树改变（install_tree_mismatch）")
        commit = git(["rev-parse", "--verify", "HEAD^{commit}"], self.lock.root).stdout.strip()
        tree = git(["rev-parse", "--verify", "HEAD^{tree}"], self.lock.root).stdout.strip()
        return _record_new(self.path / "completed.json", {
            "schema": "install_completed/1", "state": "completed", "tx_id": self.tx_id,
            "prepared_sha256": self.prepared["body_sha256"], "started_sha256": self.started_record["body_sha256"],
            "request_id": self.req["request_id"], "content_id": self.req["content_id"],
            "target": self.req["target"], "package_sha256": self.req["package"]["sha256"],
            "baseline_id": self.baseline["baseline_id"], "baseline_sha256": self.baseline["body_sha256"],
            "refs": _refs(self.lock.root), "protect": self.req["base"]["protect"],
            "times": dict(self.decision["times"], install_completed=review._iso(review._now())),
            "actual": dict(actual, commit=commit, tree=tree)})

    def fail(self, exc, recovery=None):
        state = "partial" if self.started else "failed"
        if recovery is None:
            recovery = ("可能存在部分写入；不得作为成功安装，须核验基线恢复（rollback_target_unverified）"
                        if self.started else "尚未开始目标写入")
        return _record_new(self.path / f"{state}.json", {
            "schema": "install_tx/1", "state": state, "tx_id": self.tx_id,
            "prepared_sha256": self.prepared["body_sha256"], "failed_at": review._iso(review._now()),
            "error": {"type": type(exc).__name__, "message": str(exc)[:2000]},
            "recovery": recovery})


def _review_source(review_id, *, target, kind, dest, zip_path=None) -> Path:
    """B2b-1：调用方 ZIP 只作一致性断言，所有安装字节从请求冻结快照取得。"""
    if not review_id:
        raise WfError("安装需要 review_id（unreviewed_install）")
    try:
        review.load_install_request(review_id, target=target, kind=kind, zip_path=zip_path)
        src = review.extract_snapshot(review_id, dest)
        _verify_review_source(review_id, src)
        return src
    except OSError as e:
        raise WfError(f"审查包准备失败（{type(e).__name__}）：{e}") from e


def _verify_review_source(review_id, src) -> None:
    try:
        bad = review.verify_extracted(review_id, src)
    except OSError as e:
        raise WfError(f"审查包解包复核失败（{type(e).__name__}）：{e}") from e
    if bad:
        raise WfError("审查包解包复核失败：" + "；".join(bad[:6]))


def _allow_review_install(review_id, *, target, kind, src) -> dict:
    """只核许可，不写消费；最终消费必须在目标锁内紧接首次目标写入之前。"""
    if src is None:
        raise WfError("安装前审查需要实际解包目录（extracted_dir_required）")
    try:
        review.load_install_request(review_id, target=target, kind=kind)
        decision = review.final_check(review_id, target=target, extracted_dir=src)
        if not decision["allowed"]:
            raise WfError("安装前审查未通过，不能安装：" + "；".join(decision["reasons"][:6]))
        return decision
    except OSError as e:
        raise WfError(f"安装前审查失败（{type(e).__name__}）：{e}") from e


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
    planned = []
    for rel in rels or []:
        review._norm_member(rel)
        # 先 nofollow 读取顶层；绝不能 resolve symlink 后删除真实目标。
        state, _ = review._state_of(root, rel.rstrip("/"))
        if state["type"] in ("symlink", "unsafe", "unknown", "special"):
            raise WfError(f"删除顶层不是安全普通路径（delete_unsafe）：{rel}")
        p = (root / rel).resolve()
        if root.resolve() not in p.parents:
            raise WfError(f"删除路径越界：{rel}")
        closure = review._walk_states(root, rel.rstrip("/")) if state["type"] == "dir" else []
        for row in [state] + closure:
            path = row["path"]
            if (row["type"] in ("unknown", "unsafe", "special")
                    or matches_protect(path, protect)
                    or (row["type"] == "dir" and matches_protect(path + "/", protect))):
                raise WfError(f"删除闭包含受保护/未知路径（delete_unsafe）：{path}")
        planned.append((rel, p))
    for rel, p in planned:
        # 写前复核顶层，内部 symlink 由 rmtree 删除链接本身。
        if p.is_symlink() or (root / rel.rstrip("/")).is_symlink():
            raise WfError(f"删除顶层链接改变（delete_unsafe）：{rel}")
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
    git(["add", "-A", "--", ".", ":(exclude)workflow/evidence/install", ":(exclude)local/install"], root)
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

def apply_zip(inst: Instance, zip_path=None, by="executor", *, review_id=None) -> dict:
    if not review_id:
        raise WfError("安装需要 review_id（unreviewed_install）")
    caller = inst
    root = Path(inst.root).resolve(strict=True)
    with _InstallLock(root, "project") as lock:
        # 不信任调用者缓存的 Instance；锁内重新读取 wf.json 与 protect。
        inst = _fresh_instance(root)
        protect = inst.protect()
        if (root / ".git").exists():
            dirty = _dirty(root, protect)
            if dirty:
                raise WfError("工作区有未提交的改动，已停止（AP-2）：\n  " + "\n  ".join(dirty[:20]))
        with tempfile.TemporaryDirectory() as td:
            src = _review_source(review_id, target=inst, kind="project", dest=Path(td), zip_path=zip_path)
            req = review._load_request(review_id)
            pkg = review._snapshot_package(req)
            if not (src / "PROJECT.md").exists():
                raise WfError("zip 中没有 PROJECT.md（AP-4）")
            p = parse_project_md((src / "PROJECT.md").read_text(encoding="utf-8"))
            cl = src / "CHANGELOG.md"
            issues = validate(p, cl.read_text(encoding="utf-8") if cl.exists() else None)
            if issues:
                raise WfError("PROJECT.md 校验未通过，未改动仓库（AP-1）：\n  - " + "\n  - ".join(issues))
            manifest = read_json(src / "RELEASE.json", {}) or {}
            inst = _fresh_instance(root, req)
            protect = inst.protect()
            written, deleted, cfg_keys = [], [], []
            rel_json = f"workflow/releases/{p['version']}.json"
            decision = _allow_review_install(review_id, target=inst, kind="project", src=src)
            plan = _install_plan(req, pkg, src, root, "project", cfg=inst.cfg, archive=rel_json)
            tx = _InstallTx(lock, req, src, plan, decision)
            try:
                tx.consume(inst, src)
                inst = _fresh_instance(root, req)
                lock.verify()
                _copy_tree(src, root, protect, written=written)
                lock.verify()
                _delete(root, manifest.get("delete"), protect, done=deleted)
                if manifest.get("wf_config"):
                    for k, v in manifest["wf_config"].items():
                        if k in ("processes", "protect", "hook"):
                            inst.cfg[k] = v
                            cfg_keys.append(k)
                    if cfg_keys:
                        write_json(inst.cfg_path, inst.cfg)
                if manifest:
                    (root / "workflow" / "releases").mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src / "RELEASE.json", root / rel_json)
                lock.verify()
                head, changed = _commit(root, f"release {p['version']}", tag=p["version"])
                res = {"version": p["version"], "commit": head, "changed": changed, "deleted": deleted,
                       "config": cfg_keys, "hints": _restart_hints(inst, changed), "notes": manifest.get("notes"),
                       "install_tx": tx.tx_id}
                actual = _verify_install(root, "project", protect, plan)
                inst.event("apply", version=p["version"], commit=head, changed=len(changed), deleted=deleted or None, by=by)
                tx.complete(actual)
            except BaseException as e:
                note = "安装已消费，结果未登记成功。" if tx.started else "尚未写目标内容。"
                try:
                    tx.fail(e, note)
                except Exception as rec_e:
                    note += f"；事务记录失败：{rec_e}"
                if tx.started:
                    try:
                        _recover_tx(lock, tx.path)
                        inst = _fresh_instance(root)
                        note += "；已核验恢复安装前基线；保护树不宣称全文件系统恢复。"
                    except Exception as restore_e:
                        note += f"；基线恢复失败：{restore_e}；须人工核验。"
                if not isinstance(e, Exception):
                    raise
                raise WfError(f"落地失败：{e}\n{note}")
        caller.cfg = json.loads(json.dumps(inst.cfg))
        return res


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


def _pending_restores(lock):
    out = []
    for kind in ("project", "tool"):
        directory = lock.root / ("local/install" if kind == "tool" else "workflow/evidence/install")
        if not os.path.lexists(directory):
            continue
        for path in directory.glob("restore-*"):
            if not os.path.lexists(path / "completed.json"):
                out.append((path, _read_record(path / "prepared.json")))
    return out


def _public_restore(lock, selector, protect, message):
    selected = _select_restore_target(lock, selector)
    path = selected["path"]
    pending_installs = []
    for kind in ("project", "tool"):
        directory = lock.root / ("local/install" if kind == "tool" else "workflow/evidence/install")
        if os.path.lexists(directory):
            pending_installs.extend(p for p in directory.glob("tx-*") if not any(
                os.path.lexists(p / n) for n in ("completed.json", "failed.json", "recovered.json")))
    if pending_installs and (pending_installs != [path] or not selected["is_baseline"]):
        raise WfError("须先恢复未闭合安装自身的基线（install_incomplete）")
    pending = (not os.path.lexists(path / "completed.json")
               and not os.path.lexists(path / "failed.json")
               and not os.path.lexists(path / "recovered.json"))
    if pending and selected["is_baseline"]:
        if _pending_restores(lock):
            raise WfError("另有回滚未闭合（install_incomplete）")
        return _recover_tx(lock, path)
    target = review._target(lock.root, lock.kind)
    if protect != selected["protect"]:
        raise WfError("回滚保护规则与已验证目标不同（rollback_protected）")
    control = selected["record"].get("control", selected["record"].get("actual", {}).get("control"))
    # 项目控制/配置受到保护：在任何目标写入前核验；工具控制文件在安装树内恢复。
    if lock.kind == "project" and review._control(target) != control:
        raise WfError("回滚将改变保护配置/控制文件（rollback_protected）")
    _restore_snapshot(lock, selected, protect, message, preflight=True)
    jobs = _pending_restores(lock)
    if jobs:
        if len(jobs) != 1:
            raise WfError("存在多个未闭合回滚（install_incomplete）")
        job, prepared = jobs[0]
        if (prepared["kind"] != lock.kind or prepared["identity"] != selected["identity"]
                or prepared["source_sha256"] != selected["record"]["body_sha256"]
                or prepared["protect_sha256"] != _digest(protect)):
            raise WfError("须继续原回滚目标（install_incomplete）")
    else:
        lock.activate()
        job = lock.store / ("restore-" + secrets.token_hex(16))
        job.mkdir(mode=0o700)
        prepared = _record_new(job / "prepared.json", {
            "schema": "install_restore/1", "state": "prepared", "restore_id": job.name,
            "target_root": str(lock.root), "kind": lock.kind, "selector": str(selector),
            "identity": selected["identity"], "source_sha256": selected["record"]["body_sha256"],
            "inventory_sha256": _digest(selected["paths"]), "protect_sha256": _digest(protect),
            "lock_token": lock.token, "prepared_at": review._iso(review._now())})
    try:
        result = _restore_snapshot(lock, selected, protect, message)
        _record_new(job / "completed.json", {
            "schema": "install_restore/1", "state": "completed", "restore_id": job.name,
            "prepared_sha256": prepared["body_sha256"],
            "inventory_sha256": _digest(_inventory(lock.root, lock.kind, selected["protect"])),
            "identity": _identity(lock.root), "completed_at": review._iso(review._now()),
            "runtime": "not_verified"})
        return result
    except BaseException as e:
        if not os.path.lexists(job / "partial.json"):
            _record_new(job / "partial.json", {
                "schema": "install_restore/1", "state": "partial", "restore_id": job.name,
                "prepared_sha256": prepared["body_sha256"],
                "failed_at": review._iso(review._now()),
                "error": {"type": type(e).__name__, "message": str(e)[:2000]}})
        raise


def restore_tree(repo: Path, ref: str, message: str, protect=(), *, kind=None) -> tuple:
    """兼容 helper 也必须查目标自有安装授权并持锁；普通 commit 不构成许可。"""
    kind = kind or ("project" if (Path(repo) / "workflow/wf.json").is_file() else "tool")
    with _InstallLock(repo, kind, recovery=True) as lock:
        return _public_restore(lock, ref, list(protect), message)


def rollback(inst: Instance, to: str, by="executor", *, baseline_id=None) -> dict:
    root = Path(inst.root).resolve(strict=True)
    with _InstallLock(root, "project", recovery=True) as lock:
        fresh = _fresh_instance(root)
        selector = baseline_id or to
        selected = _select_restore_target(lock, selector)
        pending = not any(os.path.lexists(selected["path"] / n)
                          for n in ("completed.json", "failed.json", "recovered.json"))
        if not pending and not _pending_restores(lock):
            dirty = _dirty(root, fresh.protect())
            if dirty:
                raise WfError("工作区有未提交的改动，不能回滚：\n  " + "\n  ".join(dirty[:20]))
        cur = fresh.version()
        head, changed, removed = _public_restore(lock, selector, fresh.protect(), f"rollback to {to} (from {cur})")
        fresh = _fresh_instance(root)
        res = {"from": cur, "to": to, "commit": head, "changed": changed, "removed": removed,
               "hints": _restart_hints(fresh, changed)}
        fresh.event("rollback", frm=cur, to_version=to, commit=head, by=by)
        inst.cfg = json.loads(json.dumps(fresh.cfg))
        return res


# ---------------- 工具自身 ----------------

SELF_PROTECT = review.TOOL_PROTECT


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
    from . import anomaly as _an   # 1.9.5：开启 review_required 时，到时没有结论是不安装，不能写“继续安装”
    print(f"开始等独立审查（上限 {limit_s} 秒；到时没有结论"
          + ("按 review_required 不安装）" if _an.transport_cfg().get("review_required") else "按“未审查”继续安装）"),
          file=sys.stderr, flush=True)
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
        print(f"本次未审查：{why}；" + ("按 review_required 不安装" if anomaly.transport_cfg().get("review_required") else "照常安装")
              + f"，可事后按 {diff} 补审", file=sys.stderr, flush=True)   # 1.9.3：开启 review_required 时不再写“照常安装”
        return "unavailable", why
    print(f"独立审查结论：{data['verdict']}", file=sys.stderr, flush=True)
    write_json(rd / f"{Path(zip_path).stem}.json", data)
    return data["verdict"], "；".join(data.get("notes", [])[:5])


def _record_review(zip_path, verdict, notes) -> None:
    # 1.9.0（LIVE.md 判据 4）：这次自动安装在安装前拿到的审查结论，按版本包留一条；只记录，不改变处置
    import hashlib
    from .core import LOCAL, now_iso
    try:
        rd = LOCAL / "reviews"
        rd.mkdir(parents=True, exist_ok=True)
        write_json(rd / f"{Path(zip_path).stem}.result.json",
                   {"ts": now_iso(), "verdict": verdict, "notes": str(notes)[:500],
                    "zip_sha256": hashlib.sha256(Path(zip_path).read_bytes()).hexdigest()})
    except OSError:
        pass


def _mark_install_start(zip_path) -> None:
    # 1.9.4（审查 concerns）：实景测量要核“审查结论早于安装”，此前只能拿安装命令结束的时刻比，这里记下开始复制文件的时刻
    from .core import LOCAL, now_iso
    p = LOCAL / "reviews" / f"{Path(zip_path).stem}.result.json"
    try:
        rr = read_json(p, {}) or {}
        if rr:
            rr["install_started_at"] = now_iso()
            write_json(p, rr)
    except OSError:
        pass


def selftest_failure_report(new, stdout, stderr, log_dir) -> tuple:
    """1.9.4（W95）：新版本自检未通过时，完整输出写进文件，提示里列出全部 ✗ 行与文件路径
    （此前只回显最后 3000 个字符，第 125 轮失败的那一行就不在里面）。返回 (提示, 文件路径)。"""
    from .core import now_iso
    stdout, stderr = stdout or "", stderr or ""
    path = None
    try:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"selftest-{new}-{now_iso().replace(':', '').replace('+', 'Z')}.log"
        path.write_text(f"===== stdout =====\n{stdout}\n===== stderr =====\n{stderr}\n", encoding="utf-8")
    except OSError:
        path = None
    fails = [ln for ln in stdout.splitlines() if ln.lstrip().startswith("✗")]
    summary = next((ln for ln in reversed(stdout.splitlines()) if ln.startswith("自检结果")), "")
    msg = (f"新版本 {new} 的自检未通过，未安装。{summary}\n未通过的项（共 {len(fails)} 条）：\n"
           + ("\n".join(fails[:60]) or "（输出里没有 ✗ 行，见完整输出）")
           + (f"\n……另有 {len(fails) - 60} 条，见完整输出" if len(fails) > 60 else "")
           + (f"\n完整输出：{path}" if path else "\n完整输出没能写进文件；末尾原文：\n" + stdout[-3000:])
           + (f"\nstderr 末尾：\n{stderr[-1500:]}" if stderr.strip() else ""))
    return msg, path


def _review_gate(verdict, notes) -> None:
    # 1.8.8（P1-L）：transport.json 写了 "review_required": true 时，审查没有结论（没配审查或审查不在场、超时）就不安装，
    # 而不是按“未审查”继续；结论为 pass 或 concerns 照常，block 照旧拒绝。默认不开启，行为与 1.8.7 相同。
    if verdict in ("none", "unavailable"):
        from . import anomaly as _an
        if (_an.transport_cfg() or {}).get("review_required"):
            raise WfError(f"transport.json 要求安装前审查（review_required），本次没有审查结论（{verdict}：{notes}），不安装；"
                          "审查到场后重跑 wf self-update --auto")


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


def self_update(zip_path=None, skip_test=False, allow_same=False, auto=False, *, review_id=None) -> dict:
    if not review_id:
        raise WfError("安装需要 review_id（unreviewed_install）")
    with _InstallLock(WF_HOME, "tool") as lock:
        if not (WF_HOME / ".git").exists():
            raise WfError("工具尚未初始化版本库，先运行 wf self-init")
        if _dirty(WF_HOME, SELF_PROTECT):
            raise WfError("工具目录有未提交的改动，已停止。用 git -C 工具目录 status 查看。")
        cur = tool_version()
        with tempfile.TemporaryDirectory() as td:
            src = _review_source(review_id, target=WF_HOME, kind="tool", dest=Path(td) / "candidate",
                                 zip_path=zip_path)
            src_files = fidelity.tree_hashes(src)   # 1.5.0：落地后逐个核对，确认装上的就是这个版本包
            new = (src / "VERSION").read_text(encoding="utf-8").strip()
            if _vtuple(new) < _vtuple(cur) or (_vtuple(new) == _vtuple(cur) and not allow_same):
                raise WfError(f"新版本 {new} 不高于当前版本 {cur}。回到旧版本请用 wf self-rollback。")
            _allow_review_install(review_id, target=WF_HOME, kind="tool", src=src)
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
                # floor.probe 会执行候选代码，进入下一步前先核实际测试目录。
                _verify_review_source(review_id, src)
            if not skip_test:
                print(f"正在运行新版本 {new} 的完整自检", file=sys.stderr, flush=True)   # 1.6.9（P0-A）：长步骤先说一声
                env = dict(os.environ)
                env.pop("WF_LOCAL_DIR", None)
                r = subprocess.run([sys.executable, str(src / "wf.py"), "selftest"], capture_output=True, text=True,
                                   env=env, timeout=600)
                if r.returncode != 0:
                    msg, _ = selftest_failure_report(new, r.stdout, r.stderr, WF_HOME / "local" / "selftest-logs")
                    raise WfError(msg)
            # 先拒绝测试目录被改动的情况，再从冻结包重建独立安装目录；不能靠重新解包掩盖测试变异。
            _verify_review_source(review_id, src)
            src = _review_source(review_id, target=WF_HOME, kind="tool", dest=Path(td) / "install")
            src_files = fidelity.tree_hashes(src)
            manifest = read_json(src / "RELEASE.json", {}) or {}
            req = review._load_request(review_id)
            pkg = review._snapshot_package(req)
            decision = _allow_review_install(review_id, target=WF_HOME, kind="tool", src=src)
            plan = _install_plan(req, pkg, src, WF_HOME, "tool")
            tx = _InstallTx(lock, req, src, plan, decision)
            try:
                tx.consume(WF_HOME, src)
                lock.verify()
                _copy_tree(src, WF_HOME, SELF_PROTECT)
                lock.verify()
                _delete(WF_HOME, manifest.get("delete"), SELF_PROTECT)
                _verify_install(WF_HOME, "tool", SELF_PROTECT, plan)
                # MANIFEST 必须是精确冻结的交付内容；只作核验，不再落地后自我修补。
                check = self_check()
                if not check["ok"] or check.get("version") != new:
                    raise WfError(f"落地清单核验失败：{check}")
                lock.verify()
                head, changed = _commit(WF_HOME, f"wf {new}", tag=f"wf-v{new}")
                from . import server
                board_was_running = bool((fidelity.safe_call(server.info, {}) or {}).get("pid"))
                restarted = _restart_board()
                receipt = fidelity.post_install_receipt(new, src_files, _tool_files(), board_was_running,
                                                        lambda: fidelity.safe_call(server.info, {}), WF_HOME)
                if not receipt["ok"]:
                    raise WfError("升级后 receipt 未通过：" + "；".join(receipt["problems"]))
                actual = _verify_install(WF_HOME, "tool", SELF_PROTECT, plan)
                tx.complete(actual)
            except BaseException as e:
                record_error = None
                try:
                    tx.fail(e)
                except Exception as rec_e:
                    record_error = str(rec_e)
                recovery_note = "尚未消费安装"
                if tx.started:
                    try:
                        _recover_tx(lock, tx.path)
                        runtime = _read_record(tx.path / "recovered.json").get("runtime") or {}
                        recovery_note = ("磁盘已核验恢复安装前基线；运行状态已核验" if runtime.get("verified")
                                         else "磁盘已核验恢复安装前基线；看板运行版本未核验")
                    except Exception as restore_e:
                        recovery_note = f"基线恢复失败：{restore_e}；须人工核验"
                if not isinstance(e, Exception):
                    raise
                if record_error:
                    recovery_note += f"；失败记录写入失败：{record_error}"
                raise WfError(f"升级失败（未登记成功）：{e}；{recovery_note}") from e
            print(f"升级核验通过：磁盘版本 {receipt['version']}，{receipt['files_checked']} 个文件与版本包一致，"
                  + (f"看板已加载 {new}" if board_was_running else "看板未在运行")
                  + ("；" + "；".join(receipt["notes"]) if receipt["notes"] else ""), file=sys.stderr, flush=True)
            return {"from": cur, "to": new, "commit": head, "changed": changed, "tested": not skip_test,
                    "board_restarted": restarted, "receipt": receipt, "install_tx": tx.tx_id}


def _restart_board() -> bool:
    """看板是常驻进程，升级只换了磁盘文件，进程仍跑旧代码（F25）。这里用子进程调用新的 wf.py 重启它，
    让新代码立刻生效，而不是等下一次升级。看板没在跑就什么都不做。"""
    from . import server
    i = server.info()
    if not i.get("pid"):
        return False
    port = i.get("port") or 8765
    try:
        stopped = subprocess.run(
            [sys.executable, str(WF_HOME / "wf.py"), "serve", "--stop"],
            capture_output=True, timeout=30)
        if stopped.returncode != 0:
            return False
        started = subprocess.run(
            [sys.executable, str(WF_HOME / "wf.py"), "serve", "--daemon", "--port", str(port)],
            capture_output=True, timeout=30)
        return started.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def self_rollback(tag: str, *, baseline_id=None) -> dict:
    with _InstallLock(WF_HOME, "tool", recovery=True) as lock:
        selector = baseline_id or tag
        if not baseline_id and not tag.startswith(("wf-v", "baseline-")) and len(tag) != 40:
            selector = "wf-v" + tag.lstrip("v")
        selected = _select_restore_target(lock, selector)
        pending = not any(os.path.lexists(selected["path"] / n)
                          for n in ("completed.json", "failed.json", "recovered.json"))
        if not pending and not _pending_restores(lock) and _dirty(WF_HOME, SELF_PROTECT):
            raise WfError("工具目录有未提交的改动，不能回滚。")
        cur = tool_version()
        head, changed, removed = _public_restore(lock, selector, SELF_PROTECT, f"rollback to {tag} (from {cur})")
        from . import server
        try:
            before_runtime = fidelity.safe_call(server.info, {}) or {}
        except Exception:
            before_runtime = {}
        expected_version = tool_version()
        restarted = _restart_board()
        runtime = {
            "restart_attempted": bool(before_runtime.get("pid")),
            "restart_result": restarted,
            "disk_version": expected_version,
            "verified": False,
        }
        if not runtime["restart_attempted"]:
            runtime["status"] = "not_running"
        else:
            try:
                observed = fidelity.safe_call(server.info, {}) or {}
                runtime["observed"] = {
                    k: observed.get(k) for k in ("running", "pid", "version", "stale")
                }
                runtime["verified"] = bool(restarted and observed.get("running")
                                          and observed.get("version") == expected_version
                                          and not observed.get("stale"))
                runtime["status"] = "verified" if runtime["verified"] else "unknown"
            except Exception as e:
                runtime["status"] = "unknown"
                runtime["error"] = f"{type(e).__name__}: {e}"
        return {"from": cur, "to": tool_version(), "commit": head, "changed": changed,
                "removed": removed, "board_restarted": restarted, "runtime": runtime}


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
