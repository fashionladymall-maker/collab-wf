"""B1-B 车道测试：bootstrap 交接材料同源（r2 增加独立审查 R1–R5、r3 增加 R6–R7 的反例）。

覆盖：有版本库时一次固定提交（工具 zip、WORKFLOW.md、模板、VERSION、清单同源，生成期间 HEAD 移动不影响）；
现场未提交改动只声明、冻结后另出只读覆盖包（hash 对应冻结字节、不可安装、不被正式同步与自动安装选中）；
没有版本库时用同一次冻结快照；冻结失败、git 读取失败、写到一半失败都不留半成品、不记事件；项目 dirty 照旧拒绝；
旧调用与旧返回键保留。

run_lane_b(_ok) 供自检聚合器调用（它本身就在隔离子进程里运行）。单独运行 `python3 -m wflib.selftest_lane_b` 时，
先经 selftest.child_env 起一个隔离子进程（临时 local、WF_NOTIFY=0），父进程的环境一项都不改。
所有工具目录与项目都建在临时目录里；不读写真实 local、注册表与项目。只用标准库。
"""
from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from .core import WF_HOME

GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
DIRTY5 = ["CHANGELOG.md", "anomalies.json", "docs/CAPABILITIES.md", "docs/WORKFLOW.md", "wflib/driver.py"]
TEMPLATE = ("【测试续接】{reason}\n项目 {zip} {head} {version}\n工具 {tool_zip} {tool_version}\n位置 {state}\n指纹 {fingerprint}\n"
            "{last_turn}\n===== WORKFLOW.md =====\n{workflow_md}\n===== PROJECT.md =====\n{project_md}\n"
            "===== 文件清单 =====\n{tool_manifest}\n")
BIN = bytes(range(256))


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _git(cwd, *args) -> str:
    r = subprocess.run(["git"] + GIT_ID + list(args), cwd=str(cwd), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}：{r.stderr.strip()}")
    return r.stdout.strip()


def _mk_home(base: Path, with_git: bool = True) -> Path:
    """一个最小的工具目录：含模板、WORKFLOW.md、VERSION、可执行文件、二进制文件、MANIFEST.json 与 local/（应被排除）。"""
    home = base / "tool"
    files = {
        "VERSION": "9.9.9\n", "docs/WORKFLOW.md": "WORKFLOW 基线 v1\n", "templates/bootstrap.md": TEMPLATE,
        "CHANGELOG.md": "# CHANGELOG 基线\n", "anomalies.json": "{\"v\": 1}\n", "docs/CAPABILITIES.md": "能力 基线\n",
        "wflib/__init__.py": "", "wflib/driver.py": "X = 1\n", "MANIFEST.json": "{}\n",
        ".gitignore": "local/\n__pycache__/\n*.pyc\n", "local/secret.txt": "不应出现在任何材料里\n",
    }
    for rel, text in files.items():
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (home / "bin").mkdir()
    (home / "bin" / "wf").write_text("#!/bin/sh\necho wf\n", encoding="utf-8")
    os.chmod(home / "bin" / "wf", 0o755)
    (home / "assets").mkdir()
    (home / "assets" / "bin.dat").write_bytes(BIN)
    if with_git:
        _git(home, "init", "-q")
        _git(home, "add", "-A")
        _git(home, "commit", "-qm", "基线")
    return home


def _mk_project(base: Path, name: str):
    from .core import Instance
    root = base / name
    (root / "workflow").mkdir(parents=True)
    (root / "workflow" / "wf.json").write_text(json.dumps({"project": f"{name}-{os.getpid()}"}), encoding="utf-8")
    (root / "PROJECT.md").write_text("# PROJECT.md\n", encoding="utf-8")
    (root / ".gitignore").write_text("workflow/\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "release v0.1")
    return Instance(root)


@contextlib.contextmanager
def _patched(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


def _zip_tree(z: Path, td: Path) -> str:
    """按 zip 内容（含权限）重算 git tree。"""
    from . import release
    d = release._extract(z, td)
    _git(d, "init", "-q")
    _git(d, "add", "-A", "-f")
    return _git(d, "write-tree")


def _zread(z: Path, name: str) -> bytes:
    with zipfile.ZipFile(z) as zf:
        return zf.read(name)


def _listing(d: Path) -> dict:
    return {p.name: _sha(p.read_bytes()) for p in sorted(d.iterdir())} if d.exists() else {}


def _events(inst, type_="bootstrap") -> list:
    from .core import read_jsonl
    return [e for e in read_jsonl(inst.events_path) if e.get("type") == type_]


def _tree_of(home: Path) -> dict:
    return {str(p.relative_to(home)).replace(os.sep, "/"): _sha(p.read_bytes())
            for p in sorted(home.rglob("*")) if p.is_file() and ".git" not in p.relative_to(home).parts}


# ---------------------------------------------------------------------------

def _t_clean(_ok, base: Path):
    from . import admin
    home = _mk_home(base)
    inst = _mk_project(base, "lb-clean")
    commit, tree = _git(home, "rev-parse", "HEAD"), _git(home, "rev-parse", "HEAD^{tree}")
    with _patched(admin, "WF_HOME", home):
        b = admin.bootstrap(inst, "测试")
    ts = b.get("tool_source") or {}
    old_keys = {"message", "zip", "tool_zip", "chars", "short", "short_why"} <= set(b)
    _ok("B1-B 返回值：原有六个键都在，新增 tool_source、tool_zip_sha256、tool_overlay、tool_overlay_sha256",
        old_keys and {"tool_source", "tool_zip_sha256", "tool_overlay", "tool_overlay_sha256"} <= set(b), str(sorted(b)))
    zt = _zip_tree(b["tool_zip"], base / "x-clean")
    _ok("B1-B 干净工具目录：工具 zip 按内容重算的 tree 等于固定提交的 tree，sha256 与返回值一致",
        ts.get("kind") == "git" and ts.get("commit") == commit and ts.get("tree") == tree and zt == tree
        and b["tool_zip_sha256"] == _sha(b["tool_zip"].read_bytes()) and b["tool_zip"].name == f"collab-wf-9.9.9-{commit[:7]}.zip",
        json.dumps([ts.get("commit"), ts.get("tree"), zt, b["tool_zip"].name], ensure_ascii=False))
    msg = b["message"].read_text(encoding="utf-8")
    bin_line = f"- assets/bin.dat  256  {_sha(BIN)[:12]}"
    _ok("B1-B 干净工具目录：正文、模板、版本与清单取自同一提交；清单不含 local/ 与 MANIFEST.json；声明写明一致、无覆盖包",
        msg.startswith("【测试续接】测试") and "WORKFLOW 基线 v1" in msg and "工具 " + b["tool_zip"].name + " 9.9.9" in msg
        and bin_line in msg and "local/secret.txt" not in msg and "- MANIFEST.json" not in msg and commit in msg
        and "冻结时运行现场与该提交一致" in msg and b["tool_overlay"] is None and ts.get("dirty") == [],
        msg[-800:])
    ev = (_events(inst) or [{}])[-1]
    _ok("B1-B 事件：bootstrap 事件新增来源字段（提交、tree、zip sha256、现场改动数）",
        ev.get("tool_commit") == commit and ev.get("tool_tree") == tree and ev.get("tool_dirty") == 0
        and ev.get("tool_zip_sha256") == b["tool_zip_sha256"] and ev.get("tool_kind") == "git" and "tool_overlay" not in ev,
        json.dumps(ev, ensure_ascii=False)[:400])
    _ok("B1-B 落地后 outbox 里没有临时文件", not [n for n in _listing(inst.outbox) if n.startswith(".")],
        str(list(_listing(inst.outbox))))


def _t_dirty5(_ok, base: Path):
    from . import admin, ops, policy, release, sync
    from .core import WfError
    home = _mk_home(base)
    inst = _mk_project(base, "lb-dirty")
    commit, tree = _git(home, "rev-parse", "HEAD"), _git(home, "rev-parse", "HEAD^{tree}")
    base_wf = (home / "docs" / "WORKFLOW.md").read_bytes()
    for rel in DIRTY5:
        with open(home / rel, "a", encoding="utf-8") as f:
            f.write("现场改动（来源未知）\n")
    before = _tree_of(home)
    with _patched(admin, "WF_HOME", home):
        b = admin.bootstrap(inst, "测试")
        before_refuse = _listing(inst.outbox)
        try:
            admin.bootstrap(inst, "测试", on_tool_dirty="refuse")
            refused = False
        except WfError as e:
            refused = "on_tool_dirty=refuse" in str(e)
    after = _tree_of(home)
    ts = b["tool_source"]
    st = _git(home, "status", "--porcelain")
    _ok("B1-B 复刻五个现场改动：逐个识别为 M，工作区字节在生成前后完全不变（既不采用也不回退）",
        [e["path"] for e in ts["dirty"]] == sorted(DIRTY5) and all(e["status"] == "M" for e in ts["dirty"])
        and before == after and len(st.splitlines()) == 5
        and all(e["sha256"] == before[e["path"]] for e in ts["dirty"]), json.dumps(ts["dirty"], ensure_ascii=False)[:600])
    msg = b["message"].read_text(encoding="utf-8")
    zt = _zip_tree(b["tool_zip"], base / "x-dirty")
    _ok("B1-B 有现场改动时，工具 zip、正文 WORKFLOW.md 与清单仍取自提交，不混入现场内容",
        zt == tree and _zread(b["tool_zip"], "collab-wf/docs/WORKFLOW.md") == base_wf and "WORKFLOW 基线 v1" in msg
        and "现场改动（来源未知）" not in msg and f"- docs/WORKFLOW.md  {len(base_wf)}  {_sha(base_wf)[:12]}" in msg
        and ts["commit"] == commit, msg[-1200:])
    ov = b["tool_overlay"]
    data = ov.read_bytes() if ov and ov.exists() else b""
    meta = json.loads(_zread(ov, "OVERLAY.json")) if data else {}
    diff = _zread(ov, "DIRTY.diff") if data else b""
    try:
        release._extract(ov, base / "x-ov")
        extract_refused = False
    except WfError:
        extract_refused = True
    files_ok = bool(data) and all(_zread(ov, "files/" + rel) == (home / rel).read_bytes() for rel in DIRTY5)
    _ok("B1-B 只读覆盖包：hash 是落盘字节的 hash，清单逐个对应冻结的现场字节，diff 有 hash，标明不可安装",
        bool(data) and _sha(data) == b["tool_overlay_sha256"] == ts["overlay"]["sha256"] and meta.get("installable") is False
        and meta.get("base_commit") == commit and [e["sha256"] for e in meta.get("entries", [])] == [before[r] for r in sorted(DIRTY5)]
        and _sha(diff) == meta.get("diff_sha256") == ts["overlay"]["diff_sha256"] and "+现场改动（来源未知）" in diff.decode()
        and files_ok and (os.stat(ov).st_mode & 0o222) == 0,
        json.dumps([b.get("tool_overlay_sha256"), meta.get("installable"), meta.get("diff_sha256")], ensure_ascii=False))
    au = policy.AUTO_SELF_UPDATE.match(f"wf self-update --auto workflow/outbox/{ov.name}")
    try:
        names = set(sync.collect(inst))
        sync_ok = f"artifacts/{b['tool_zip'].name}" in names and not any(ov.name in n for n in names)
        sync_why = str(sorted(n for n in names if n.startswith("artifacts/")))
    except Exception as e:   # noqa: BLE001
        sync_ok, sync_why = False, f"sync.collect 出错：{e}"
    _ok("B1-B 覆盖包不可安装、不被选中：release._extract 拒绝，不匹配 collab-wf-[0-9]*.zip 与自动安装白名单，"
        "sync.collect 只带正式工具 zip",
        extract_refused and not fnmatch.fnmatch(ov.name, "collab-wf-[0-9]*.zip") and not au and sync_ok, sync_why)
    chk = ops.check_outgoing(b["message"], "task")
    _ok("B1-B 声明：正文逐个列出现场改动与覆盖包 sha256，写明不随消息发送；返回值只记“已生成”；发送前检查不把这些 hash 当密钥",
        all(rel in msg for rel in DIRTY5) and b["tool_overlay_sha256"] in msg and "不随本消息发送" in msg
        and ts["delivery"]["overlay"] == "generated_not_attached" and chk["secrets"] == [],
        json.dumps(chk["secrets"], ensure_ascii=False) + msg[-600:])
    _ok("B1-B on_tool_dirty=refuse：拒绝并写明原因，outbox 不变", refused and _listing(inst.outbox) == before_refuse)


def _t_freeze_once(_ok, base: Path):
    """生成途中现场又被改了一次：覆盖包与声明仍是冻结时的那份字节，不会把前后两次改动混成一份证据。"""
    from . import admin
    home = _mk_home(base)
    inst = _mk_project(base, "lb-freeze")
    first = "第一次现场改动\n"
    (home / "docs" / "WORKFLOW.md").write_text(first, encoding="utf-8")
    orig = admin._write_tool_zip

    def hook(src, dest):
        (home / "docs" / "WORKFLOW.md").write_text("第二次现场改动\n", encoding="utf-8")
        return orig(src, dest)
    with _patched(admin, "WF_HOME", home), _patched(admin, "_write_tool_zip", hook):
        b = admin.bootstrap(inst, "测试")
    ov = b["tool_overlay"]
    meta = json.loads(_zread(ov, "OVERLAY.json"))
    msg = b["message"].read_text(encoding="utf-8")
    h1 = _sha(first.encode())
    _ok("B1-B 冻结一次：生成途中现场再变，覆盖包内容、清单 hash 与正文声明仍是冻结时的字节",
        _zread(ov, "files/docs/WORKFLOW.md") == first.encode() and meta["entries"][0]["sha256"] == h1
        and h1[:12] in msg and "第二次现场改动" not in msg and "第二次现场改动" not in _zread(ov, "DIRTY.diff").decode(),
        json.dumps(meta["entries"], ensure_ascii=False))


def _t_head_moved(_ok, base: Path):
    from . import admin
    home = _mk_home(base)
    inst = _mk_project(base, "lb-moved")
    commit, tree = _git(home, "rev-parse", "HEAD"), _git(home, "rev-parse", "HEAD^{tree}")
    orig = admin._write_tool_zip
    moved = {}

    def hook(src, dest):
        (home / "VERSION").write_text("9.9.10\n", encoding="utf-8")
        (home / "docs" / "WORKFLOW.md").write_text("WORKFLOW v2（移动后）\n", encoding="utf-8")
        _git(home, "commit", "-qam", "生成途中的新提交")
        moved["head"] = _git(home, "rev-parse", "HEAD")
        return orig(src, dest)
    with _patched(admin, "WF_HOME", home), _patched(admin, "_write_tool_zip", hook):
        b = admin.bootstrap(inst, "测试")
    ts = b["tool_source"]
    msg = b["message"].read_text(encoding="utf-8")
    zt = _zip_tree(b["tool_zip"], base / "x-moved")
    _ok("B1-B 生成期间 HEAD 移动：zip、WORKFLOW.md、VERSION、清单都仍取自最初固定的提交，并写明 HEAD 已移动",
        ts["commit"] == commit and zt == tree and _zread(b["tool_zip"], "collab-wf/VERSION") == b"9.9.9\n"
        and "WORKFLOW 基线 v1" in msg and "WORKFLOW v2" not in msg and "9.9.10" not in b["tool_zip"].name
        and ts["head_moved"] is True and ts["head_now"] == moved.get("head") and "HEAD 已移动" in msg and ts["dirty"] == [],
        json.dumps([ts["commit"], ts["head_now"], moved.get("head"), zt == tree], ensure_ascii=False))


def _t_nogit(_ok, base: Path):
    from . import admin, fidelity
    home = _mk_home(base, with_git=False)
    inst = _mk_project(base, "lb-nogit")
    orig = admin._write_tool_zip

    def hook(src, dest):   # 冻结之后改工作区：zip 与正文都不应看到
        (home / "docs" / "WORKFLOW.md").write_text("冻结之后的改动\n", encoding="utf-8")
        return orig(src, dest)
    with _patched(admin, "WF_HOME", home), _patched(admin, "_write_tool_zip", hook):
        b = admin.bootstrap(inst, "测试")
    ts = b["tool_source"]
    msg = b["message"].read_text(encoding="utf-8")
    with zipfile.ZipFile(b["tool_zip"]) as zf:
        names = sorted(n[len("collab-wf/"):] for n in zf.namelist())
        mode = (zf.getinfo("collab-wf/bin/wf").external_attr >> 16) & 0o777
    src = fidelity.freeze_tool_source(home)   # 重新冻结只用来核对清单口径与摘要算法
    want = hashlib.sha256("".join(f"{h}  {r}\n" for r, h in src["manifest"].items()).encode()).hexdigest()[:16]
    _ok("B1-B 没有版本库：zip、正文、模板、版本与清单来自同一次冻结快照，冻结后的工作区改动不进入任何材料",
        ts["kind"] == "snapshot" and ts["commit"] is None and ts["tree"] is None and ts["commit_verified"] is False
        and b["tool_zip"].name == "collab-wf-9.9.9.zip" and "local/secret.txt" not in names and "bin/wf" in names
        and "MANIFEST.json" in names and mode == 0o755 and _zread(b["tool_zip"], "collab-wf/docs/WORKFLOW.md") == "WORKFLOW 基线 v1\n".encode()
        and "WORKFLOW 基线 v1" in msg and "冻结之后的改动" not in msg and b["tool_overlay"] is None,
        json.dumps([ts["kind"], names, mode], ensure_ascii=False))
    _ok("B1-B 没有版本库：声明写明冻结快照摘要、未核对任何原生提交；摘要按指纹的行格式重算一致",
        "没有版本库" in msg and "未核对任何原生提交" in msg and ts["digest"] in msg and len(want) == 16
        and src["kind"] == "snapshot", msg[-500:])


def _t_failures(_ok, base: Path):
    from . import admin, fidelity
    from .core import WfError

    def attempt(inst, patches):
        before = _listing(inst.outbox)
        n_ev = len(_events(inst))
        err = None
        with contextlib.ExitStack() as es:
            for obj, name, val in patches:
                es.enter_context(_patched(obj, name, val))
            try:
                admin.bootstrap(inst, "测试")
            except (WfError, OSError) as e:
                err = e
        return err, _listing(inst.outbox) == before, len(_events(inst)) == n_ev

    # 1. 没有版本库、冻结时有一个文件读不了
    home = _mk_home(base / "f1", with_git=False)
    inst = _mk_project(base / "f1", "lb-f1")
    real = fidelity._read_file

    def bad_read(dir_fd, name):   # r3：在已固定的上级目录里读文件
        if name == "WORKFLOW.md":
            raise PermissionError(13, "测试：读不了")
        return real(dir_fd, name)
    err, same, no_ev = attempt(inst, [(admin, "WF_HOME", home), (fidelity, "_read_file", bad_read)])
    _ok("B1-B 冻结失败：没有版本库时任一文件读不了就整次拒绝，outbox 不变、不记事件",
        isinstance(err, WfError) and "冻结工具目录失败" in str(err) and same and no_ev, str(err))

    # 2. git 读取对象失败
    home = _mk_home(base / "f2")
    inst = _mk_project(base / "f2", "lb-f2")
    real_git = fidelity._git_bytes

    def bad_git(args, cwd, inp=None):
        if args and args[0] == "cat-file":
            raise WfError("git cat-file 失败：测试注入")
        return real_git(args, cwd, inp)
    err, same, no_ev = attempt(inst, [(admin, "WF_HOME", home), (fidelity, "_git_bytes", bad_git)])
    _ok("B1-B git 读取失败：拒绝，outbox 不变、不记事件", isinstance(err, WfError) and same and no_ev, str(err))

    # 3. 写工具 zip 写到一半失败：已写的项目 zip 临时文件也要清掉；同名旧文件保持原样
    home = _mk_home(base / "f3")
    inst = _mk_project(base / "f3", "lb-f3")
    inst.outbox.mkdir(parents=True, exist_ok=True)
    short = _git(home, "rev-parse", "--short", "HEAD")
    (inst.outbox / f"collab-wf-9.9.9-{short}.zip").write_bytes(b"OLD")
    (inst.outbox / "keep.txt").write_text("保留\n", encoding="utf-8")

    def half(src, dest):
        Path(dest).write_bytes(b"PARTIAL")
        raise OSError(28, "测试：磁盘满")
    err, same, no_ev = attempt(inst, [(admin, "WF_HOME", home), (admin, "_write_tool_zip", half)])
    _ok("B1-B 写到一半失败：临时文件全部清掉，同名旧工具 zip 与其他文件原样保留，不记事件",
        isinstance(err, OSError) and same and no_ev
        and (inst.outbox / f"collab-wf-9.9.9-{short}.zip").read_bytes() == b"OLD", str(err))

    # 4. 项目工作区 dirty：照旧拒绝
    home = _mk_home(base / "f4")
    inst = _mk_project(base / "f4", "lb-f4")
    (inst.root / "untracked.txt").write_text("x\n", encoding="utf-8")
    err, same, no_ev = attempt(inst, [(admin, "WF_HOME", home)])
    _ok("B1-B 项目工作区有未提交改动：照旧拒绝", isinstance(err, WfError) and "工作区有未提交的改动" in str(err)
        and same and no_ev, str(err))

    # 5. 参数取值错误：在读任何东西之前就拒绝
    try:
        with _patched(admin, "WF_HOME", home):
            admin.bootstrap(inst, "测试", on_tool_dirty="adopt")
        bad = None
    except WfError as e:
        bad = e
    _ok("B1-B on_tool_dirty 只接受 declare / refuse", bad is not None and "declare" in str(bad), str(bad))


def _t_compat(_ok, base: Path):
    from . import admin, fidelity
    home = _mk_home(base)
    commit = _git(home, "rev-parse", "HEAD")
    tree = _git(home, "rev-parse", "HEAD^{tree}")
    (home / "docs" / "WORKFLOW.md").write_text("工作区改动\n", encoding="utf-8")
    with _patched(admin, "WF_HOME", home):
        z = admin._tool_zip(base / "out")
    old_style = ["工具文件（路径、字节数、sha256 前 12 位）："] + [
        f"- {rel}  {(home / rel).stat().st_size}  {h[:12]}" for rel, h in fidelity.tree_hashes(home).items()]
    by_wt = fidelity.tool_manifest_text(home)
    by_ref = fidelity.tool_manifest_text(home, ref=commit)
    _ok("B1-B 兼容：_tool_zip(outdir) 旧调用仍可用且取已提交内容；tool_manifest_text 不给 ref 时与原实现逐行相同，"
        "给 ref 时按提交",
        _zip_tree(z, base / "x-compat") == tree and by_wt == "\n".join(old_style) and "工作区改动" not in by_ref
        and _sha("工作区改动\n".encode())[:12] in by_wt and _sha("WORKFLOW 基线 v1\n".encode())[:12] in by_ref
        and not [n for n in os.listdir(base / "out") if n.startswith(".")],
        by_ref[:300])
    inst = _mk_project(base, "lb-compat")
    (home / "docs" / "WORKFLOW.md").write_text("WORKFLOW 基线 v1\n", encoding="utf-8")
    with _patched(admin, "WF_HOME", home):
        b = admin.bootstrap(inst, "测试", by="driver")
    ev = (_events(inst) or [{}])[-1]
    _ok("B1-B 兼容：驱动器的 bootstrap(inst, why, by=\"driver\") 照旧，事件 by=driver",
        ev.get("by") == "driver" and b["message"].exists() and b["zip"].exists(), json.dumps(ev, ensure_ascii=False)[:300])


def _t_short(_ok, base: Path):
    from . import admin, sync
    home = _mk_home(base)
    inst = _mk_project(base, "lb-short")
    commit = _git(home, "rev-parse", "HEAD")
    (home / "wflib" / "driver.py").write_text("X = 2\n", encoding="utf-8")
    inst.state.update(state="COMPOSE", turn=3)
    inst.save()
    sha = "0123456789abcdef0123456789abcdef01234567"
    with _patched(admin, "WF_HOME", home), \
            _patched(sync, "cfg", lambda: {"sync": "/x/gh_sync.py", "sync_projects": [inst.name], "sync_repo": "me/repo"}), \
            _patched(sync, "run", lambda i, **k: {"status": "pushed", "commit": sha, "digest": "d", "files": 1,
                                                  "names": ["STATUS.md"]}):
        b = admin.bootstrap(inst, "测试")
    short = b["short"].read_text(encoding="utf-8") if b.get("short") else ""
    lines = [admin._short_source_line(None), admin._short_source_line({"kind": "snapshot", "digest": "abcd", "dirty": []}),
             admin._short_source_line({"kind": "git", "commit": "c1", "tree": "t1", "dirty": []})]
    _ok("B1-B 短消息：工具版本取自固定来源；写明附件取自哪个提交、冻结时有几个未提交改动；仓库 tool/ 未与该提交逐文件核对",
        "collab-wf 9.9.9" in short and commit in short and "1 个未提交改动" in short and "wflib/driver.py" in short
        and "/main/" not in short and "未核对提交" in lines[1] and "冻结时现场没有未提交改动" in lines[2]
        and "与该提交一致" not in lines[2] and "未与该提交逐文件核对" in lines[2] and "安装现场" in lines[0],
        short[-600:])


# ---------------- r2：独立审查 R1–R5 的反例 ----------------

OUT_MARK = b"OUTSIDE-MARKER-7f3a"   # 工具目录之外的合成内容；任何材料里都不应出现


def _git_entries(home: Path, commit: str) -> dict:
    """提交的完整递归清单 {路径: (git 权限, blob id)}。与 zip 逐项相等即等价于 tree 相等（git 不存空目录）。"""
    out = {}
    for rec in _git(home, "ls-tree", "-r", "-z", "--full-tree", commit).split("\0"):
        if rec:
            meta, _, path = rec.partition("\t")
            mode, typ, oid = meta.split()
            if typ == "blob":
                out[path] = (mode, oid)
    return out


def _zip_entries(z: Path, prefix: str = "collab-wf/") -> dict:
    """按 zip 的条目算出 {路径: (git 权限, blob id)}：链接记 120000，属主可执行记 100755，其余 100644。"""
    import stat as st_
    out = {}
    with zipfile.ZipFile(z) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            data = zf.read(info)
            attr = info.external_attr >> 16
            mode = "120000" if st_.S_ISLNK(attr) else ("100755" if attr & 0o100 else "100644")
            oid = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
            out[info.filename[len(prefix):]] = (mode, oid)
    return out


def _manifest_in_message(msg: str) -> dict:
    """从说明消息里取出工具文件清单 {路径: (字节数, sha256 前 12 位)}。"""
    out, on = {}, False
    for ln in msg.splitlines():
        if ln.startswith("工具文件（路径、字节数、sha256 前 12 位）"):
            on = True
            continue
        if on:
            if not ln.startswith("- "):
                break
            rel, size, h = ln[2:].split("  ")
            out[rel] = (int(size), h)
    return out


def _zip_manifest(z: Path, prefix: str = "collab-wf/") -> dict:
    from . import fidelity
    out = {}
    with zipfile.ZipFile(z) as zf:
        for info in zf.infolist():
            rel = info.filename[len(prefix):]
            if not info.is_dir() and fidelity._manifest_rel(rel):
                data = zf.read(info)
                out[rel] = (len(data), _sha(data)[:12])
    return out


def _all_bytes(z: Path) -> bytes:
    with zipfile.ZipFile(z) as zf:
        return b"".join(zf.read(n) for n in zf.namelist())


def _t_r1_attributes(_ok, base: Path):
    """R1：冻结之后才出现的 .git/info/attributes、已提交的 .gitattributes 都不能改变工具 zip 的内容。"""
    from . import admin
    home = _mk_home(base)
    (home / ".gitattributes").write_text("docs/WORKFLOW.md export-ignore\nVERSION export-subst\n", encoding="utf-8")
    os.symlink("WORKFLOW.md", home / "docs" / "link.md")   # 已提交的链接：zip 里也要是链接本身
    _git(home, "add", "-A")
    _git(home, "commit", "-qm", "带属性与链接")
    commit = _git(home, "rev-parse", "HEAD")
    inst = _mk_project(base, "lb-r1")
    orig = admin._write_tool_zip

    def hook(src, dest):   # 冻结之后、写 zip 之前改现场属性
        (home / ".git" / "info").mkdir(parents=True, exist_ok=True)
        (home / ".git" / "info" / "attributes").write_text("* export-ignore\nbin/wf export-ignore\n", encoding="utf-8")
        return orig(src, dest)
    with _patched(admin, "WF_HOME", home), _patched(admin, "_write_tool_zip", hook):
        b = admin.bootstrap(inst, "测试")
    msg = b["message"].read_text(encoding="utf-8")
    want, got = _git_entries(home, commit), _zip_entries(b["tool_zip"])
    diff = sorted(set(want.items()) ^ set(got.items()))
    _ok("B1-B r2 R1：现场 .git/info/attributes 与已提交 .gitattributes 的 export-ignore/export-subst 不改变工具 zip；"
        "zip 逐项（路径、权限、blob）等于固定提交，链接仍是链接",
        not diff and got.get("docs/link.md", ("",))[0] == "120000" and "docs/WORKFLOW.md" in got and "bin/wf" in got,
        json.dumps(diff[:6], ensure_ascii=False))
    wf_zip = _zread(b["tool_zip"], "collab-wf/docs/WORKFLOW.md").decode("utf-8")
    _ok("B1-B r2 R1：正文 WORKFLOW.md、消息里的工具清单与 zip 同源（清单逐行等于 zip 内容，WORKFLOW 原文一致）",
        _manifest_in_message(msg) == _zip_manifest(b["tool_zip"]) and ("===== WORKFLOW.md =====\n" + wf_zip) in msg
        and "附件已逐项核对" in msg,
        json.dumps([len(_manifest_in_message(msg)), len(_zip_manifest(b["tool_zip"]))]))


def _t_r1_verify(_ok, base: Path):
    """R1：写出来的 zip 与固定来源不一致（少文件、改权限、改字节）时拒绝，不留产物、不记事件。"""
    from . import admin, fidelity
    from .core import WfError
    home = _mk_home(base)
    inst = _mk_project(base, "lb-r1v")
    cases = {
        "少一个文件": lambda f: {k: v for k, v in f.items() if k != "docs/WORKFLOW.md"},
        "权限不同": lambda f: {**f, "bin/wf": (0o644, f["bin/wf"][1])},
        "字节不同": lambda f: {**f, "VERSION": (f["VERSION"][0], b"0.0.1\n")},
    }
    results = {}
    for name, mutate in cases.items():
        def bad_writer(src, dest, mutate=mutate):
            Path(dest).write_bytes(fidelity.source_zip_bytes({**src, "files": mutate(src["files"])}))
        before, n_ev = _listing(inst.outbox), len(_events(inst))
        try:
            with _patched(admin, "WF_HOME", home), _patched(admin, "_write_tool_zip", bad_writer):
                admin.bootstrap(inst, "测试")
            results[name] = "接受了"
        except WfError as e:
            results[name] = "ok" if ("与固定来源不一致" in str(e) and _listing(inst.outbox) == before
                                     and len(_events(inst)) == n_ev) else f"拒绝但有残留：{e}"
    _ok("B1-B r2 R1：工具 zip 少文件、权限不同、字节不同都被核对拦下，outbox 不变、不记事件",
        all(v == "ok" for v in results.values()), json.dumps(results, ensure_ascii=False))


def _t_r2_walk_errors(_ok, base: Path):
    """R2：没有版本库时，列目录出错（根目录或子目录）、文件中途消失都整次拒绝；不能静默少一个目录。"""
    from . import admin, fidelity
    from .core import WfError
    real_walk = os.walk

    def injecting(target):
        def walk(top, onerror=None, followlinks=False):
            if target == ".":
                if onerror:
                    onerror(PermissionError(13, "测试注入：列不了根目录", str(top)))
                return
            for dp, dns, fns in real_walk(top, onerror=onerror, followlinks=followlinks):
                if target in dns:
                    dns.remove(target)   # os.walk 列不了子目录时的行为：交给 onerror，然后跳过
                    if onerror:
                        onerror(PermissionError(13, "测试注入：列不了目录", os.path.join(dp, target)))
                yield dp, dns, fns
        return walk

    def attempt(home, inst, patches):
        before, n_ev = _listing(inst.outbox), len(_events(inst))
        err = None
        with contextlib.ExitStack() as es:
            es.enter_context(_patched(admin, "WF_HOME", home))
            for obj, name, val in patches:
                es.enter_context(_patched(obj, name, val))
            try:
                admin.bootstrap(inst, "测试")
            except WfError as e:
                err = e
        return err, _listing(inst.outbox) == before and len(_events(inst)) == n_ev

    home = _mk_home(base / "a", with_git=False)
    (home / "hidden").mkdir()
    (home / "hidden" / "required.py").write_text("X = 1\n", encoding="utf-8")
    inst = _mk_project(base / "a", "lb-r2")
    e1, clean1 = attempt(home, inst, [(fidelity, "_walk", injecting("hidden"))])
    e2, clean2 = attempt(home, inst, [(fidelity, "_walk", injecting("."))])
    real_read = fidelity._read_file

    def vanish(dir_fd, name):   # r3：在已固定的上级目录里读文件
        if name == "required.py":
            raise FileNotFoundError(2, "测试注入：文件消失", name)
        return real_read(dir_fd, name)
    e3, clean3 = attempt(home, inst, [(fidelity, "_read_file", vanish)])
    _ok("B1-B r2 R2：子目录列不了、根目录列不了、文件冻结中消失，都整次拒绝并写明位置；outbox 不变、不记事件",
        e1 is not None and "hidden" in str(e1) and "无法列出" in str(e1) and clean1
        and e2 is not None and "无法列出" in str(e2) and clean2
        and e3 is not None and "消失" in str(e3) and "required.py" in str(e3) and clean3,
        json.dumps([str(e1), str(e2), str(e3)], ensure_ascii=False))
    src = fidelity.freeze_tool_source(home)
    _ok("B1-B r2 R2：没有注入时同一目录正常冻结，hidden/required.py 在快照里（正例）",
        "hidden/required.py" in src["files"], str(sorted(src["files"]))[:300])
    if os.geteuid() != 0:   # 真实权限反例：root 不受目录权限限制，此时只由上面的注入用例覆盖
        os.chmod(home / "hidden", 0)
        try:
            e4, clean4 = attempt(home, inst, [])
        finally:
            os.chmod(home / "hidden", 0o755)
        _ok("B1-B r2 R2：真实不可读目录（chmod 000，非 root）整次拒绝，不产出少了目录的快照",
            e4 is not None and "hidden" in str(e4) and clean4, str(e4))
    else:
        print("  （当前以 root 运行：真实 chmod 000 反例不适用，已由注入 onerror 的用例覆盖）", flush=True)


def _t_r3_symlinks_git(_ok, base: Path):
    """R3：有版本库时，现场的链接（指向目录外、指向 local/、失效、把已跟踪文件或目录换成链接）只记链接本身。"""
    from . import admin
    home = _mk_home(base)
    inst = _mk_project(base, "lb-r3")
    outside = base / "outside.txt"
    outside.write_bytes(OUT_MARK + " 目录外文件\n".encode("utf-8"))
    outdir = base / "outdir"
    outdir.mkdir()
    (outdir / "driver.py").write_bytes(OUT_MARK + " 目录外目录\n".encode("utf-8"))
    (outdir / "__init__.py").write_bytes(OUT_MARK + b"\n")
    os.symlink(str(outside), home / "untracked-link")
    os.symlink("local/secret.txt", home / "local-link")
    os.symlink("does-not-exist", home / "broken-link")
    (home / "docs" / "CAPABILITIES.md").unlink()
    os.symlink(str(outside), home / "docs" / "CAPABILITIES.md")
    shutil.rmtree(home / "wflib")
    os.symlink(str(outdir), home / "wflib")
    with _patched(admin, "WF_HOME", home):
        b = admin.bootstrap(inst, "测试")
    ov = b["tool_overlay"]
    meta = json.loads(_zread(ov, "OVERLAY.json"))
    ent = {e["path"]: e for e in meta["entries"]}
    blob = _all_bytes(ov) + _all_bytes(b["tool_zip"]) + b["message"].read_bytes()
    secret = "不应出现在任何材料里".encode("utf-8")
    with zipfile.ZipFile(ov) as zf:
        file_members = [n for n in zf.namelist() if n.startswith("files/")]
    _ok("B1-B r2 R3：覆盖包、工具 zip、正文都不含目录外内容与 local/ 内容；链接只在清单里记目标字符串",
        OUT_MARK not in blob and secret not in blob and file_members == [],
        json.dumps([file_members, OUT_MARK in blob, secret in blob], ensure_ascii=False))
    _ok("B1-B r2 R3：未跟踪链接记为 A/symlink；已跟踪文件换成链接记为 M（file→symlink）；目录换成链接时其下文件按已删除记",
        ent.get("untracked-link", {}).get("type") == "symlink" and ent["untracked-link"].get("target") == str(outside)
        and ent.get("local-link", {}).get("target") == "local/secret.txt"
        and ent.get("broken-link", {}).get("target") == "does-not-exist"
        and ent.get("docs/CAPABILITIES.md", {}).get("status") == "M" and ent["docs/CAPABILITIES.md"].get("base_type") == "file"
        and ent["docs/CAPABILITIES.md"].get("type") == "symlink"
        and ent.get("wflib/driver.py", {}).get("status") == "D" and ent.get("wflib", {}).get("type") == "symlink",
        json.dumps(ent, ensure_ascii=False)[:900])


def _t_r3_symlinks_nogit(_ok, base: Path):
    """R3：没有版本库时，指向文件或目录的链接都按链接本身冻结，不读取、不进入目标。"""
    import stat as st_
    from . import admin
    home = _mk_home(base, with_git=False)
    inst = _mk_project(base, "lb-r3n")
    outside = base / "outside.txt"
    outside.write_bytes(OUT_MARK + b"\n")
    outdir = base / "outdir"
    outdir.mkdir()
    (outdir / "inner.py").write_bytes(OUT_MARK + b"\n")
    os.symlink(str(outside), home / "ext-link")
    os.symlink(str(outdir), home / "extdir")
    with _patched(admin, "WF_HOME", home):
        b = admin.bootstrap(inst, "测试")
    with zipfile.ZipFile(b["tool_zip"]) as zf:
        attrs = {n[len("collab-wf/"):]: zf.getinfo(n).external_attr >> 16 for n in zf.namelist()}
        data = {n[len("collab-wf/"):]: zf.read(n) for n in zf.namelist()}
    blob = _all_bytes(b["tool_zip"]) + b["message"].read_bytes()
    _ok("B1-B r2 R3：无版本库快照里链接记为链接（zip 条目为链接、内容是目标字符串），不进入目录外目录、不复制目标内容",
        st_.S_ISLNK(attrs.get("ext-link", 0)) and data.get("ext-link") == str(outside).encode()
        and st_.S_ISLNK(attrs.get("extdir", 0)) and "extdir/inner.py" not in attrs and OUT_MARK not in blob,
        json.dumps({k: oct(v) for k, v in attrs.items() if "ext" in k}, ensure_ascii=False))


def _t_r4_publish(_ok, base: Path):
    """R4：落地（改名）在第 1…N 步、或给旧文件留副本时失败：旧的整套材料逐字节保持，引用的文件都在，不留新的半套。"""
    from . import admin
    home = _mk_home(base)
    (home / "CHANGELOG.md").write_text("现场改动\n", encoding="utf-8")   # 有覆盖包：一共 4 个落地步骤
    inst = _mk_project(base, "lb-r4")
    with _patched(admin, "WF_HOME", home):
        b0 = admin.bootstrap(inst, "测试")
    for f in (b0["zip"], b0["tool_zip"]):   # 模拟上一次是另一份内容的同名包
        f.write_bytes(b"OLD-" + f.name.encode())
    old = _listing(inst.outbox)
    old_msg = b0["message"].read_text(encoding="utf-8")
    real = admin._rename
    report = {}
    for n in (1, 2, 3, 4):
        calls = [0]

        def flaky(src, dst, n=n):
            calls[0] += 1
            if calls[0] == n:
                raise OSError(5, f"测试注入：第 {n} 次改名失败")
            real(src, dst)
        n_ev = len(_events(inst))
        try:
            with _patched(admin, "WF_HOME", home), _patched(admin, "_rename", flaky):
                admin.bootstrap(inst, "测试")
            report[n] = "没有失败"
        except OSError:
            report[n] = "ok" if (_listing(inst.outbox) == old and len(_events(inst)) == n_ev) else \
                f"残留或丢失：{sorted(set(old) ^ set(_listing(inst.outbox)))}"
    real_keep = admin._keep_old
    kc = [0]

    def keep_fail(final):
        kc[0] += 1
        if kc[0] == 2:
            raise OSError(28, "测试注入：留副本失败")
        return real_keep(final)
    try:
        with _patched(admin, "WF_HOME", home), _patched(admin, "_keep_old", keep_fail):
            admin.bootstrap(inst, "测试")
        report["keep"] = "没有失败"
    except OSError:
        report["keep"] = "ok" if _listing(inst.outbox) == old else "残留或丢失"
    refs_ok = b0["zip"].name in old_msg and b0["tool_zip"].name in old_msg and b0["zip"].exists() and b0["tool_zip"].exists()
    _ok("B1-B r2 R4：第 1/2/3/4 次改名失败、第 2 次留副本失败，outbox 逐字节回到失败前（旧包未被删或改），不记事件",
        all(v == "ok" for v in report.values()) and refs_ok, json.dumps(report, ensure_ascii=False))
    with _patched(admin, "WF_HOME", home):
        b1 = admin.bootstrap(inst, "测试")
    names = list(_listing(inst.outbox))
    _ok("B1-B r2 R4：之后正常生成时覆盖旧同名包，副本与临时文件全部清掉",
        b1["tool_zip"].read_bytes() != b"OLD-" + b1["tool_zip"].name.encode() and not [n for n in names if n.startswith(".")],
        str(names))


def _t_r5_short(_ok, base: Path):
    """R5：短消息不得声称同步镜像等于固定提交；HEAD 在生成期间移动时写明。"""
    from . import admin, sync
    home = _mk_home(base)
    inst = _mk_project(base, "lb-r5")
    inst.state.update(state="COMPOSE", turn=3)
    inst.save()
    orig = admin._write_tool_zip

    def hook(src, dest):
        (home / "docs" / "WORKFLOW.md").write_text("WORKFLOW v2\n", encoding="utf-8")
        _git(home, "commit", "-qam", "生成途中的新提交")
        return orig(src, dest)
    fake = {"status": "pushed", "commit": "0123456789abcdef0123456789abcdef01234567", "digest": "d", "files": 1,
            "names": ["STATUS.md"]}
    with _patched(admin, "WF_HOME", home), _patched(admin, "_write_tool_zip", hook), \
            _patched(sync, "cfg", lambda: {"sync": "/x/gh_sync.py", "sync_projects": [inst.name], "sync_repo": "me/repo"}), \
            _patched(sync, "run", lambda i, **k: dict(fake)):
        b = admin.bootstrap(inst, "测试")
    short = b["short"].read_text(encoding="utf-8") if b.get("short") else ""
    head_now = _git(home, "rev-parse", "HEAD")
    _ok("B1-B r2 R5：HEAD 生成期间移动时，短消息写明已移动到哪个提交、tool/ 未与固定提交逐文件核对，不声称一致",
        b["tool_source"]["head_moved"] is True and "HEAD 已移动" in short and head_now in short
        and "与该提交一致" not in short and "未与该提交逐文件核对" in short, short[-500:])


# ---------------- r3：独立审查 R6–R7 的反例 ----------------

def _t_r6_restore_retry(_ok, base: Path):
    """R6：恢复失败留下的旧内容副本，跨后续（失败或成功的）重试保留，不被删除或覆盖。"""
    from . import admin
    from .core import WfError
    d = base / "pub"
    d.mkdir()
    f1, f2 = d / "first.zip", d / "second.zip"
    f1.write_bytes(b"OLD-0")
    f2.write_bytes(b"OLD-1")

    def pending(tag):
        out = []
        for i, f in enumerate((f1, f2)):
            t = admin._tmp_for(f)
            t.write_bytes(f"{tag}-{i}".encode())
            out.append((t, f))
        return out

    def baks():
        return {p.name: p.read_bytes() for p in sorted(d.iterdir()) if ".bak-" in p.name}
    real_rename = admin._rename

    def run(tag, fail):
        rc = [0]

        def rename(src, dst):
            rc[0] += 1
            if fail and rc[0] == 2:
                raise OSError(5, "测试注入：第 2 次改名失败")
            real_rename(src, dst)

        def restore(bak, final):
            raise OSError(5, "测试注入：恢复失败")
        try:
            with _patched(admin, "_rename", rename), _patched(admin, "_restore", restore if fail else admin._restore):
                admin._publish(pending(tag))
            return None
        except WfError as e:
            return str(e)
    e1 = run("NEW", True)
    first = baks()
    e2 = run("FAIL", True)   # 同进程再失败一次
    second = baks()
    e3 = run("RETRY", False)   # 再正常重试
    third = baks()
    named = [n for n in first if n in (e1 or "")]
    _ok("B1-B r3 R6：恢复失败后错误写明的旧内容副本（OLD-0）在之后的失败重试与成功重试后仍在、字节不变；"
        "每次副本名唯一，成功重试只清理自己建的副本",
        e1 is not None and list(first.values()) == [b"OLD-0"] and len(named) == 1
        and e2 is not None and all(second.get(n) == v for n, v in first.items()) and len(second) == 2
        and e3 is None and third == second and f1.read_bytes() == b"RETRY-0" and f2.read_bytes() == b"RETRY-1"
        and not [p.name for p in d.iterdir() if ".tmp-" in p.name],
        json.dumps([e1, {k: v.decode() for k, v in third.items()}], ensure_ascii=False)[:600])


def _t_r7_parent_swap(_ok, base: Path):
    """R7：冻结读取期间把上级目录换成指向目录外的链接：在固定上级目录之前换 → 拒绝（无 git）或按已删除记（git）；
    在固定之后、打开文件之前换（独立审查的探针边界）→ 只读到已固定的目录内对象。任何情况下都不出现目录外字节。"""
    from . import admin, fidelity
    from .core import WfError
    outdir = base / "outdir"
    outdir.mkdir()
    (outdir / "bin.dat").write_bytes(OUT_MARK + b" parent-swap\n")

    def swap(home):
        os.rename(home / "assets", home / "old_assets")
        os.symlink(str(outdir), home / "assets")

    def before_fix(home):
        real, once = fidelity._open_parent, [True]

        def f(root_fd, rel):
            if rel == "assets/bin.dat" and once[0]:
                once[0] = False
                swap(home)
            return real(root_fd, rel)
        return (fidelity, "_open_parent", f)

    def after_fix(home):
        real, once = fidelity._read_file, [True]

        def f(dir_fd, name):
            if name == "bin.dat" and once[0]:
                once[0] = False
                swap(home)
            return real(dir_fd, name)
        return (fidelity, "_read_file", f)

    def run(tag, git, hook, dirty=False):
        home = _mk_home(base / tag, with_git=git)
        if dirty:
            (home / "assets" / "bin.dat").write_bytes(b"INTERNAL-DIRTY\n")
        inst = _mk_project(base / tag, "lb-" + tag)
        before, n_ev = _listing(inst.outbox), len(_events(inst))
        with _patched(admin, "WF_HOME", home), _patched(*hook(home)):
            try:
                b = admin.bootstrap(inst, "测试")
            except WfError as e:
                return {"err": str(e), "clean": _listing(inst.outbox) == before and len(_events(inst)) == n_ev}
        blob = _all_bytes(b["tool_zip"]) + b["message"].read_bytes() + (_all_bytes(b["tool_overlay"]) if b["tool_overlay"] else b"")
        return {"err": None, "outside": OUT_MARK in blob, "b": b}
    a = run("a", False, before_fix)
    bb = run("b", False, after_fix)
    _ok("B1-B r3 R7：无版本库，上级目录在固定前被换成外部链接 → 整次拒绝且不留产物；在固定后、打开文件前被换 → "
        "只读到已固定的目录内字节；都不出现目录外内容",
        a["err"] is not None and "assets" in a["err"] and a["clean"]
        and bb["err"] is None and not bb["outside"] and _zread(bb["b"]["tool_zip"], "collab-wf/assets/bin.dat") == BIN,
        json.dumps([a.get("err"), bb.get("err"), bb.get("outside")], ensure_ascii=False))
    c = run("c", True, before_fix, dirty=True)
    dd = run("d", True, after_fix, dirty=True)

    def entry(r):
        meta = json.loads(_zread(r["b"]["tool_overlay"], "OVERLAY.json"))
        return {e["path"]: e for e in meta["entries"]}.get("assets/bin.dat", {})
    ok_c = c["err"] is None and not c["outside"] and entry(c).get("status") == "D"
    ok_d = dd["err"] is None and not dd["outside"] and entry(dd).get("sha256") == _sha(b"INTERNAL-DIRTY\n") \
        and _zread(dd["b"]["tool_overlay"], "files/assets/bin.dat") == b"INTERNAL-DIRTY\n"
    _ok("B1-B r3 R7：有版本库的现场改动，上级目录在固定前被换 → 按已删除记、不读目标；固定后被换 → 覆盖包里是"
        "冻结的目录内字节；覆盖包、工具 zip、正文都没有目录外内容",
        ok_c and ok_d, json.dumps([c.get("err"), c.get("outside"), dd.get("err"), dd.get("outside")], ensure_ascii=False))


def _t_r7_capability(_ok, base: Path):
    """R7：平台不支持以目录描述符为锚的读取时，凡需要读工作区的冻结一律拒绝，不退回按路径读取；不需要读工作区时照常。"""
    from . import admin, fidelity
    from .core import WfError
    res = {}
    for tag, git, dirty in (("nogit", False, False), ("gitdirty", True, True), ("gitclean", True, False)):
        home = _mk_home(base / tag, with_git=git)
        if dirty:
            (home / "CHANGELOG.md").write_text("现场改动\n", encoding="utf-8")
        inst = _mk_project(base / tag, "lb-cap-" + tag)
        with _patched(admin, "WF_HOME", home), _patched(fidelity, "_fd_capable", lambda: False):
            try:
                admin.bootstrap(inst, "测试")
                res[tag] = "ok"
            except WfError as e:
                res[tag] = "refused" if "不支持以目录描述符为锚" in str(e) and not _listing(inst.outbox) else f"其他：{e}"
    _ok("B1-B r3 R7：不支持 openat 等能力时，无版本库与有现场改动的冻结都拒绝；干净提交（只读对象库）照常生成",
        res == {"nogit": "refused", "gitdirty": "refused", "gitclean": "ok"}, json.dumps(res, ensure_ascii=False))


TESTS = [_t_clean, _t_dirty5, _t_freeze_once, _t_head_moved, _t_nogit, _t_failures, _t_compat, _t_short,
         _t_r1_attributes, _t_r1_verify, _t_r2_walk_errors, _t_r3_symlinks_git, _t_r3_symlinks_nogit,
         _t_r4_publish, _t_r5_short, _t_r6_restore_retry, _t_r7_parent_swap, _t_r7_capability]


def run_lane_b(_ok) -> None:
    """供自检聚合器调用：每个用例在自己的临时目录里运行，用例抛出的异常记为未通过，不中断其余用例。"""
    from .selftest import CHILD_FLAG
    if os.environ.get(CHILD_FLAG) != "1":
        _ok("B1-B 车道测试只在隔离子进程里运行（WF_SELFTEST_CHILD=1）", False,
            "请经 wf selftest 或 python3 -m wflib.selftest_lane_b 运行")
        return
    for t in TESTS:
        td = Path(tempfile.mkdtemp(prefix="wf-lane-b-"))
        try:
            t(_ok, td)
        except Exception as e:   # noqa: BLE001
            _ok(f"B1-B {t.__name__} 运行出错", False, f"{type(e).__name__}: {e}")
        finally:
            shutil.rmtree(td, ignore_errors=True)


def main() -> int:
    from . import selftest
    if os.environ.get(selftest.CHILD_FLAG) != "1":
        tmp = Path(tempfile.mkdtemp(prefix="wf-lane-b-env-"))
        try:
            r = subprocess.run([sys.executable, "-m", "wflib.selftest_lane_b"], cwd=str(WF_HOME),
                               env=selftest.child_env(os.environ, tmp / "local"), timeout=600)
            return r.returncode
        except subprocess.TimeoutExpired:
            print("B 车道测试子进程超过 600 秒没有结束，按未通过处理", flush=True)
            return 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    results = []

    def _ok(name, cond, detail=""):
        results.append(bool(cond))
        print(f"  {'✓' if cond else '✗'} {name}" + (f"  —— {detail}" if detail and not cond else ""), flush=True)
        return cond
    print(f"collab-wf B1-B 车道测试（{(WF_HOME / 'VERSION').read_text().strip()}，Python {sys.version.split()[0]}）")
    run_lane_b(_ok)
    print(f"B 车道测试结果：{sum(results)} / {len(results)} 通过")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
