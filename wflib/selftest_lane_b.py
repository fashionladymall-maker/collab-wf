"""B1-B 车道测试：bootstrap 交接材料同源（r2 增加独立审查 R1–R5、r3 增加 R6–R7 的反例）；B2-B1a：init 默认骨架；
B2-B1b：强门禁“破坏性或跨项目”与权限扩大的两个提供方（C1_PROVIDERS，c1-provider/1）及变异对照。

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
import stat
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


# ---------------- B2-B1a：新项目的默认白名单与目标骨架 ----------------

def _reg_names() -> list:
    from .core import load_registry
    return [p.get("name") for p in (load_registry().get("projects") or [])]


def _init_cmds(pol: dict, gl: dict) -> list:
    return [g.get("cmd") for g in pol.get("gates", [])] + [x.get("check") for x in gl.get("goals", []) if x.get("kind") == "exec"]


def _t_b1a_init_new(_ok, base: Path):
    """新项目：两份默认文件、固定只读门禁 exit 0、未批准、无批准记录；只提交接入产生的文件，不碰用户其他改动。"""
    import re as _re
    from . import admin, goals, policy
    from .core import Instance, read_jsonl
    name = f"lb-b1a-new-{os.getpid()}"
    inst = admin.init_project(base / "p1", name)
    pol = json.loads((inst.wf / "policy.json").read_text(encoding="utf-8"))
    gl = json.loads((inst.wf / "goals.json").read_text(encoding="utf-8"))
    fixed = [c for _, c in admin.INIT_GATES]
    gates_ok = [(g.get("name"), g.get("cmd"), g.get("pass")) for g in pol.get("gates", [])] == \
        [(n, c, {"exit": 0}) for n, c in admin.INIT_GATES] and fixed == ["wf self-check", "wf self-check --probes"]
    ps, gs = policy.status(inst, "policy"), policy.status(inst, "goals")
    ev = [e for e in read_jsonl(inst.events_path) if e.get("type") == "init"]
    _ok("B2-B1a init 新项目：写出默认 policy.json（allow 空、授权全关、两道固定只读门禁 exit 0）与 goals.json（default-goals/1，"
        "today 为宿主日期），二者都未批准、没有批准记录，事件记下新建的文件",
        gates_ok and pol.get("allow") == [] and pol.get("auto_apply") is False and pol.get("auto_self_update") is False
        and pol.get("auto_new_conversation") is False and all(policy.readonly_check(c) for c in _init_cmds(pol, gl))
        and goals.validate(gl) == [] and gl.get("_skeleton") == goals.SKELETON_VERSION
        and bool(_re.fullmatch(r"\d{4}-\d{2}-\d{2}", gl.get("phase", {}).get("since", "")))
        and not ps["approved"] and not gs["approved"] and not (inst.wf / "approvals.json").exists()
        and policy.load(Instance(inst.root)) is None and len(ev) == 1
        and ev[0].get("defaults") == ["workflow/policy.json", "workflow/goals.json"]
        and ev[0].get("goals_skeleton") == goals.SKELETON_VERSION and ev[0].get("policy_defaults") == admin.INIT_POLICY_VERSION,
        json.dumps([pol.get("gates"), ps, gs, ev[-1:]], ensure_ascii=False, default=str)[:600])
    root = base / "p2"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "staged.txt").write_text("用户已暂存\n", encoding="utf-8")
    _git(root, "add", "staged.txt")
    (root / "user.txt").write_text("用户未跟踪\n", encoding="utf-8")
    admin.init_project(root, f"lb-b1a-git-{os.getpid()}")
    committed = sorted(_git(root, "show", "--name-only", "--format=", "HEAD").splitlines())
    status = _git(root, "status", "--porcelain")
    _ok("B2-B1a init 提交：只提交 wf.json、.gitignore 与本次新建的两份默认文件；用户已暂存与未跟踪的文件原样不动",
        committed == [".gitignore", "workflow/goals.json", "workflow/policy.json", "workflow/wf.json"]
        and "A  staged.txt" in status and "?? user.txt" in status,
        json.dumps([committed, status], ensure_ascii=False))


def _t_b1a_init_existing(_ok, base: Path):
    """已有 policy.json / goals.json 字节不变；只缺一份时只新建缺的那份；--adopt 不写默认文件、不调用骨架接口。"""
    from . import admin, goals
    from .core import read_jsonl
    root = base / "e1"
    (root / "workflow").mkdir(parents=True)
    mine_p, mine_g = b'{"allow": ["^echo "]}\n', b'{"goals": "user-owned, not even valid"}'
    (root / "workflow" / "policy.json").write_bytes(mine_p)
    (root / "workflow" / "goals.json").write_bytes(mine_g)
    inst = admin.init_project(root, f"lb-b1a-e1-{os.getpid()}")
    ev = [e for e in read_jsonl(inst.events_path) if e.get("type") == "init"][-1]
    root2 = base / "e2"
    (root2 / "workflow").mkdir(parents=True)
    (root2 / "workflow" / "policy.json").write_bytes(mine_p)
    admin.init_project(root2, f"lb-b1a-e2-{os.getpid()}")
    _ok("B2-B1a 已有文件不覆盖：两份都已存在时字节不变、事件记为保留；只有 policy.json 时只新建 goals.json",
        (root / "workflow" / "policy.json").read_bytes() == mine_p and (root / "workflow" / "goals.json").read_bytes() == mine_g
        and ev.get("defaults") == [] and ev.get("defaults_kept") == ["workflow/policy.json", "workflow/goals.json"]
        and (root2 / "workflow" / "policy.json").read_bytes() == mine_p
        and json.loads((root2 / "workflow" / "goals.json").read_text(encoding="utf-8")).get("_skeleton") == goals.SKELETON_VERSION,
        json.dumps(ev, ensure_ascii=False, default=str)[:400])
    root3 = base / "a1"
    root3.mkdir()
    (root3 / "PROJECT.md").write_text("# PROJECT.md\n", encoding="utf-8")
    calls = []
    with _patched(goals, "default_goals", lambda *a, **k: calls.append(a) or {}):
        admin.init_project(root3, f"lb-b1a-a1-{os.getpid()}", adopt=True)
    _ok("B2-B1a --adopt 维持原边界：不写 policy.json / goals.json，也不调用骨架接口",
        not (root3 / "workflow" / "policy.json").exists() and not (root3 / "workflow" / "goals.json").exists() and calls == [],
        str(calls))


def _t_b1a_init_refuse(_ok, base: Path):
    """骨架接口缺失、抛错、返回不合格内容时整次拒绝：不建目录、不写任何文件、不注册、不记事件；写到一半失败也不留半套。"""
    from . import admin, goals
    from .core import WfError
    good = goals.default_goals
    cases = {
        "接口缺失": None,
        "抛错": lambda *a, **k: (_ for _ in ()).throw(RuntimeError("测试注入")),
        "空目标": lambda *a, **k: {"goals": []},
        "检查命令被改": lambda p, today=None: {**good(p, today=today), "goals": [
            {**x, "check": "wf self-check; rm -rf ~"} if x.get("kind") == "exec" else x for x in good(p, today=today)["goals"]]},
        "非固定只读命令": lambda p, today=None: {**good(p, today=today), "goals": [
            {**x, "check": "cat README.md"} if x.get("kind") == "exec" else x for x in good(p, today=today)["goals"]]},
        "骨架版本不符": lambda p, today=None: {**good(p, today=today), "_skeleton": "default-goals/0"},
        "不是对象": lambda *a, **k: ["x"],
    }
    res = {}
    for i, (label, fn) in enumerate(cases.items()):
        root = base / f"r{i}"
        name = f"lb-b1a-r{i}-{os.getpid()}"
        try:
            if fn is None:
                saved = goals.__dict__.pop("default_goals")
                try:
                    admin.init_project(root, name)
                finally:
                    goals.default_goals = saved
            else:
                with _patched(goals, "default_goals", fn):
                    admin.init_project(root, name)
            res[label] = "接受了"
        except WfError as e:
            res[label] = "ok" if (not root.exists() and name not in _reg_names() and "没有写入任何文件" in str(e)) \
                else f"拒绝但有残留：{e}"
    root = base / "half"
    name = f"lb-b1a-half-{os.getpid()}"
    real = admin._write_new
    n = [0]

    def flaky(p, data):
        n[0] += 1
        if n[0] == 2:
            raise OSError(28, "测试注入：写第二份时磁盘满")
        return real(p, data)
    try:
        with _patched(admin, "_write_new", flaky):
            admin.init_project(root, name)
        res["写到一半"] = "没有失败"
    except OSError:
        res["写到一半"] = "ok" if not root.exists() and name not in _reg_names() else \
            f"有残留：{sorted(str(x.relative_to(root)) for x in root.rglob('*'))}"
    _ok("B2-B1a 骨架不可用时整次拒绝（缺失、抛错、空目标、命令被改、非固定命令、版本不符、类型不对）、写第二份失败时撤回第一份："
        "目录、文件、注册与事件都不留",
        all(v == "ok" for v in res.values()), json.dumps(res, ensure_ascii=False))
    root = base / "keep"
    (root / "workflow").mkdir(parents=True)
    (root / "workflow" / "policy.json").write_bytes(b"{}\n")
    try:
        with _patched(goals, "default_goals", cases["抛错"]):
            admin.init_project(root, f"lb-b1a-keep-{os.getpid()}")
        kept = "接受了"
    except WfError:
        kept = sorted(str(x.relative_to(root)) for x in root.rglob("*"))
    _ok("B2-B1a 拒绝时已有目录与文件原样保留（不删用户的目录或 policy.json）",
        kept == ["workflow", "workflow/policy.json"] and (root / "workflow" / "policy.json").read_bytes() == b"{}\n", str(kept))


def _t_b1a_init_safe(_ok, base: Path):
    """恶意项目名不进入任何命令；init 不执行任何门禁或 wf 子进程（只允许 git）。"""
    import subprocess as sp
    from . import admin
    evil = f'evil"; rm -rf ~; $(touch PWNED-{os.getpid()}) `id` | sh #'
    seen = []
    real_popen = sp.Popen

    class Spy(real_popen):
        def __init__(self, args, *a, **k):
            seen.append(args if isinstance(args, str) else [str(x) for x in args])
            super().__init__(args, *a, **k)
    root = base / "evil"
    root.mkdir()
    _git(root, "init", "-q")
    with _patched(sp, "Popen", Spy):
        inst = admin.init_project(root, evil)
    pol = json.loads((inst.wf / "policy.json").read_text(encoding="utf-8"))
    gl = json.loads((inst.wf / "goals.json").read_text(encoding="utf-8"))
    cmds = _init_cmds(pol, gl)
    flat = [" ".join(x) if isinstance(x, list) else x for x in seen]
    pwned = [str(p) for p in Path(base).rglob("PWNED-*")] + [str(p) for p in Path.cwd().glob("PWNED-*")]
    _ok("B2-B1a 恶意项目名：门禁与目标判据全是固定常量、不含项目名；没有被执行的痕迹",
        cmds and all(c in [x for _, x in admin.INIT_GATES] for c in cmds) and not any("evil" in c or "PWNED" in c for c in cmds)
        and not pwned and gl.get("phase", {}).get("scope") == evil,
        json.dumps([cmds, pwned], ensure_ascii=False))
    _ok("B2-B1a init 不执行门禁：期间启动的子进程只有 git，没有 wf self-check / wf.py / bin/wf",
        seen and all((x[0] if isinstance(x, list) else x.split()[0]) == "git" for x in seen)
        and not any("self-check" in f or "wf.py" in f or "/bin/wf" in f for f in flat),
        json.dumps(flat[:8], ensure_ascii=False))


def _tree_snapshot(top: Path) -> dict:
    """不跟随链接的完整快照：{相对路径: (类型, 字节或链接目标)}。"""
    out = {}
    if not os.path.lexists(top):
        return out
    for dp, dns, fns in os.walk(top, followlinks=False):
        for n in sorted(dns + fns):
            p = Path(dp) / n
            rel = str(p.relative_to(top))
            if os.path.islink(p):
                out[rel] = ("link", os.readlink(p))
            elif p.is_dir():
                out[rel] = ("dir", None)
            else:
                out[rel] = ("file", p.read_bytes())
    return out


def _t_b1a_workflow_link(_ok, base: Path):
    """B2-FIX-B1A：新建项目的 workflow 是符号链接（指向项目外、项目内、失效）或不是目录时，在任何写入之前拒绝；
    外部目录、项目文件、注册表都不变；普通 workflow 目录照常；--adopt 不写默认文件。"""
    from . import admin
    from .core import WfError, read_json, REGISTRY
    outside = base / "ext-target"   # 项目之外的目录
    outside.mkdir()
    (outside / "marker.txt").write_bytes(b"OUTSIDE-UNCHANGED\n")
    (outside / "sub").mkdir()
    (outside / "sub" / "keep.bin").write_bytes(bytes(range(64)))

    def setup(tag, kind):
        root = base / f"proj-{tag}"
        root.mkdir()
        (root / "user.txt").write_text("项目里用户的文件\n", encoding="utf-8")
        if kind == "outside":
            os.symlink(str(outside), root / "workflow")
        elif kind == "broken":
            os.symlink(str(base / "does-not-exist"), root / "workflow")
        elif kind == "internal":
            (root / "realwf").mkdir()
            (root / "realwf" / "x.txt").write_text("内部\n", encoding="utf-8")
            os.symlink("realwf", root / "workflow")
        elif kind == "file":
            (root / "workflow").write_text("不是目录\n", encoding="utf-8")
        return root
    res = {}
    for tag in ("outside", "broken", "internal", "file"):
        root = setup(tag, tag)
        name = f"lb-fix-{tag}-{os.getpid()}"
        before = (_tree_snapshot(outside), _tree_snapshot(root), read_json(REGISTRY, None))
        try:
            admin.init_project(root, name)
            res[tag] = "接受了"
        except Exception as e:   # noqa: BLE001  只有在写入前给出的 WfError 才算合格的拒绝
            after = (_tree_snapshot(outside), _tree_snapshot(root), read_json(REGISTRY, None))
            res[tag] = "ok" if (isinstance(e, WfError) and after == before and "没有写入任何文件" in str(e)) \
                else f"拒绝方式或结果不对（{type(e).__name__}，前后{'相同' if after == before else '不同'}）：{e}"
    _ok("B2-FIX-B1A workflow 是链接（指向项目外、失效、项目内）或普通文件时，新建项目在任何写入前拒绝："
        "外部目录逐字节不变、项目文件与链接不变、注册表不变、不记事件",
        all(v == "ok" for v in res.values()) and not (outside / "policy.json").exists()
        and not (outside / "goals.json").exists() and not (outside / "wf.json").exists(),
        json.dumps(res, ensure_ascii=False))
    root = base / "plain"
    (root / "workflow").mkdir(parents=True)
    inst = admin.init_project(root, f"lb-fix-plain-{os.getpid()}")
    plain_ok = (inst.wf / "policy.json").is_file() and (inst.wf / "goals.json").is_file() and not os.path.islink(root / "workflow")
    root = base / "adopt"
    root.mkdir()
    (root / "PROJECT.md").write_text("# PROJECT.md\n", encoding="utf-8")
    adopt_out = base / "adopt-outside"
    adopt_out.mkdir()
    os.symlink(str(adopt_out), root / "workflow")
    try:
        admin.init_project(root, f"lb-fix-adopt-{os.getpid()}", adopt=True)
        adopt_res = "ran"
    except WfError as e:
        adopt_res = f"refused：{e}"
    _ok("B2-FIX-B1A 普通（已存在的空）workflow 目录照常写两份默认文件；--adopt 维持原边界、不写默认白名单与目标",
        plain_ok and adopt_res == "ran" and not (adopt_out / "policy.json").exists() and not (adopt_out / "goals.json").exists(),
        json.dumps([plain_ok, adopt_res], ensure_ascii=False))


# ---------------- B2-B1b：强门禁“破坏性或跨项目”与权限扩大（两个 c1-provider/1 提供方） ----------------
# 只用临时目录：项目、项目根外的目录、指向根外的符号链接、另一个已注册项目都建在临时目录里；
# 注册表写进子进程的临时 local（WF_LOCAL_DIR），不碰真实注册表与项目。

USER_MARKS_B1B = ("必须停下的", "新增内容命中", "底线文件", "底线行为检查")   # 与 notify.USER_MARKS 同义，用于反查


def _b1b_env(td: Path) -> dict:
    root = td / "proj"
    for d in ("build/sub/deep", "docs/sub", "tools", "scripts"):
        (root / d).mkdir(parents=True, exist_ok=True)
    for f in ("a.txt", "notes.txt", "build/sub/x.o", "archive.tar"):
        (root / f).write_text("x\n", encoding="utf-8")
    outside = td / "outside"
    outside.mkdir()
    (outside / "deep").mkdir()
    (outside / "victim").write_text("不应被删\n", encoding="utf-8")
    os.symlink(outside, root / "linkout")
    os.symlink(outside, root / "build" / "link-out")   # r2：clean 目录里的链接项，通配展开后会经它出根
    os.symlink(outside / "deep", root / "link-parent")
    os.symlink(outside / "deep", root / "build" / "link-parent")
    os.symlink(root / "docs" / "sub", root / "link-in")
    os.symlink(root / "build" / "sub" / "deep", root / "build" / "link-in")
    other = td / "other"
    (other / "workflow").mkdir(parents=True)
    (td / "elsewhere" / "proj").mkdir(parents=True)
    from .policy import DEFAULT_PIPE_FILTERS
    pol = {"allow": [".*"], "pipe_filters": list(DEFAULT_PIPE_FILTERS), "auto_apply": True, "auto_self_update": True,
           "review_required": False, "clean_dirs": ["build", "linkout"]}
    return {"root": root, "outside": outside, "other": other, "pol": pol, "others": [{"name": "other", "root": str(other)}]}


def _pv_refusal(category: str):
    """隔离前提（_c1_guard）不足时的标准 c1-provider/1 拒绝结果：cases=[]、error 为拒绝原因；前提满足时返回 None。
    B-C1-OLD2-GUARD：公开提供方在业务导入、临时目录与任何夹具之前调用。"""
    reason = _c1_guard()
    if reason:
        return {"category": category, "provider": "B", "schema": "c1-provider/1", "cases": [], "error": reason}
    return None


def _pv(category: str, body, guard=False) -> dict:
    """c1-provider/1：每个用例都实际运行；抛异常的记 ran=False、ok=False；整体出错写 error。
    B-C1-PROVIDERS：error 只在真实成功时为 None——没有用例、缺正例或反例、用例重名、任一用例没运行或不成功，
    都写出诊断；guard=True 时隔离前提不足就不运行任何用例。"""
    cases, err = [], None
    refused = _pv_refusal(category) if guard else None
    if refused:
        return refused
    td = Path(tempfile.mkdtemp(prefix="wf-b1b-")).resolve()

    def case(name, polarity, fn):
        try:
            ok, detail = fn()
            cases.append({"name": name, "polarity": polarity, "ran": True, "ok": bool(ok), "detail": str(detail)[:300]})
        except Exception as e:   # noqa: BLE001
            cases.append({"name": name, "polarity": polarity, "ran": False, "ok": False,
                          "detail": f"{type(e).__name__}: {e}"[:300]})
    try:
        body(case, td)
    except Exception as e:   # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    finally:
        shutil.rmtree(td, ignore_errors=True)
    if err is None:
        names = [c["name"] for c in cases]
        bad = [c for c in cases if not (c["ran"] and c["ok"])]
        if not cases or {c["polarity"] for c in cases} != {"pos", "neg"}:
            err = "缺少实际运行的正例或反例"
        elif len(set(names)) != len(names):
            err = "用例名重复"
        elif bad:
            err = "用例未通过：" + "；".join(f"{c['name']}：{c['detail']}" for c in bad)[:600]
    return {"category": category, "provider": "B", "schema": "c1-provider/1", "cases": cases, "error": err}


def _rules(r: dict) -> list:
    return [h["rule"] for h in r.get("strong") or []]


def _b1b_pos(policy, cmd, pol, want, **kw):
    r = policy.classify(cmd, pol, **kw)
    ok = (not r["auto"]) and policy.STRONG_CATEGORY in r["categories"] and want in _rules(r) \
        and not any(m in x for x in r["reasons"] if "强门禁" in x for m in USER_MARKS_B1B)
    return ok, f"auto={r['auto']} rules={_rules(r)}"


def _b1b_neg(policy, cmd, pol, **kw):
    r = policy.classify(cmd, pol, **kw)
    return r["auto"] and not r["strong"], f"auto={r['auto']} rules={_rules(r)} reasons={r['reasons'][:2]}"


def _e2e_review(base: Path, name: str, cmds: list, pol: dict) -> tuple:
    """真实 driver.h_review：项目、已批准的 policy、本轮 intake.json 都是真的；返回 (状态, review.json, 批准事项的路由)。"""
    import time
    from . import asks, driver, notify, policy
    inst = _mk_project(base, name)
    (inst.wf / "policy.json").write_text(json.dumps(pol), encoding="utf-8")
    policy.approve(inst, "policy", "测试")
    inst.state.update(state="REVIEW", turn=1)
    inst.save()
    (inst.turn_dir() / "intake.json").write_text(json.dumps({"commands": cmds, "directives": {}, "user_decisions": []}),
                                                 encoding="utf-8")
    driver.h_review(driver.Ctx(inst, pol, time.time() + 60))
    rv = json.loads((inst.turn_dir() / "review.json").read_text(encoding="utf-8"))
    routes = [notify.route(i) for i in asks.pending(inst) if i["kind"] == "approve"]
    return inst.state["state"], rv, routes


POS_B1B = [
    ("项目根外递归删除", "rm -rf ../x", "RECURSIVE_DELETE"),
    ("规范化后在根外", "rm -rf /tmp/../srv/b1b-x", "OUTSIDE_WRITE"),
    ("clean_dirs 里的符号链接不生效", "rm -rf linkout/", "RECURSIVE_DELETE"),
    ("从 clean_dirs 经 .. 跳出", "rm -rf build/../docs", "RECURSIVE_DELETE"),
    ("删除整个项目根", "rm -rf .", "RECURSIVE_DELETE"),
    ("根目录通配", "rm -rf *", "RECURSIVE_DELETE"),
    ("引号拼接程序名", "r''m -rf ../x", "RECURSIVE_DELETE"),
    ("反斜杠程序名", "\\rm -rf ../x", "RECURSIVE_DELETE"),
    ("绝对路径程序", "/bin/rm -rf ../x", "RECURSIVE_DELETE"),
    ("env 赋值外壳", "env A=1 rm -rf ../x", "RECURSIVE_DELETE"),
    ("command 外壳", "command rm -rf ../x", "RECURSIVE_DELETE"),
    ("nohup 外壳", "nohup rm -rf ../x", "RECURSIVE_DELETE"),
    ("timeout 外壳", "timeout 5 rm -rf ../x", "RECURSIVE_DELETE"),
    ("前置环境变量自报项目", "WF_PROJECT=me rm -rf ../x", "RECURSIVE_DELETE"),
    ("注释自报在项目内", "rm -rf ../x # 项目内", "RECURSIVE_DELETE"),
    ("同名目录在别处", "rm -rf ../elsewhere/proj/x", "OUTSIDE_WRITE"),
    ("引号里的 # 不是注释", "rm -rf \"#\" ../q", "OUTSIDE_WRITE"),
    ("词中的 # 不是注释", "rm -rf x#y ../z", "OUTSIDE_WRITE"),
    ("强推", "git push --force origin main", "GIT_DESTRUCTIVE"),
    ("加号强推", "git push origin +main", "GIT_DESTRUCTIVE"),
    ("删除远端分支", "git push origin :main", "GIT_DESTRUCTIVE"),
    ("--delete", "git push --delete origin x", "GIT_DESTRUCTIVE"),
    ("git clean", "git clean -fdx", "GIT_DESTRUCTIVE"),
    ("reset --hard", "git reset --hard HEAD~1", "GIT_DESTRUCTIVE"),
    ("删除分支", "git branch -D x", "GIT_DESTRUCTIVE"),
    ("根外仓库提交", "git -C ../other commit -am x", "GIT_OUTSIDE"),
    ("launchctl bootout", "launchctl bootout gui/501/local.x", "GLOBAL_SERVICE"),
    ("pkill", "pkill -f wf", "GLOBAL_SERVICE"),
    ("kill", "kill 123", "GLOBAL_SERVICE"),
    ("brew services", "brew services stop x", "GLOBAL_SERVICE"),
    ("crontab -r", "crontab -r", "GLOBAL_SERVICE"),
    ("sudo", "sudo ls", "PRIV"),
    ("bash -c", "bash -c \"echo hi\"", "OPAQUE"),
    ("bash -lc 组合短参数", "bash -lc \"echo hi\"", "OPAQUE"),
    ("python -c", "python3 -c \"print(1)\"", "OPAQUE"),
    ("eval", "eval ls", "OPAQUE"),
    ("根外脚本", "bash /tmp/evil.sh", "OPAQUE"),
    ("命令替换", "echo $(rm -rf ../x)", "OPAQUE"),
    ("find -delete", "find . -name x -delete", "RECURSIVE_DELETE"),
    ("find -exec rm", "find . -exec rm {} \\;", "RECURSIVE_DELETE"),
    ("xargs rm", "ls | xargs rm -rf", "XARGS_WRITE"),
    ("重定向写到根外", "echo x > ../y", "OUTSIDE_WRITE"),
    ("sed -i 改根外文件", "sed -i 's/a/b/' /etc/hosts", "OUTSIDE_WRITE"),
    ("dd 写根外", "dd if=a.txt of=/tmp/x", "OUTSIDE_WRITE"),
    ("cd 到根外", "cd /tmp", "CHDIR"),
    ("多行命令", "echo hi #c\nrm -rf ../x", "MULTILINE"),
    ("引号不配对", "echo \"a", "UNPARSEABLE"),
    ("分开写的 -r -f", "rm -r -f ../x", "RECURSIVE_DELETE"),
    ("长参数 --recursive", "rm --recursive ../x", "RECURSIVE_DELETE"),
    ("-- 之后的操作数", "rm -rf -- ../x", "RECURSIVE_DELETE"),
    ("程序名来自变量", "$RM -rf ../x", "OPAQUE"),
    ("管道交给 shell 执行", "echo ls | bash", "OPAQUE"),
    ("~ 路径", "rm -rf ~/x", "OUTSIDE_WRITE"),
    ("cp -t 根外目录", "cp -t ../x a.txt", "OUTSIDE_WRITE"),
    ("mv 从根外搬入", "mv ../x a.txt", "OUTSIDE_WRITE"),
    ("perl -pi -e", "perl -pi -e 's/a/b/' notes.txt", "OPAQUE"),
    ("exec 外壳", "exec rm -rf ../x", "RECURSIVE_DELETE"),
    ("nice -n 外壳", "nice -n 5 rm -rf ../x", "RECURSIVE_DELETE"),
    ("xargs -I{}", "ls | xargs -I{} rm {}", "XARGS_WRITE"),
    # r2（常规独立审查 BLOCK-1..4 及同语义变体）
    ("通配后续段经符号链接出根", "rm -rf build/*/victim", "RECURSIVE_DELETE"),
    ("通配以 / 结尾会跟进链接", "rm -rf build/*/", "RECURSIVE_DELETE"),
    ("非递归删除经通配出根", "rm build/*/victim", "OUTSIDE_WRITE"),
    ("大括号展开出根", "rm -rf build/{x,../../outside}", "RECURSIVE_DELETE"),
    ("大括号拼出程序与参数", "{rm,-rf,../x}", "RECURSIVE_DELETE"),
    ("程序名用通配", "/bin/r? -rf ../x", "OPAQUE"),
    ("tar 连写 -xC 目录", "tar -xC ../outside -f archive.tar", "OUTSIDE_WRITE"),
    ("tar 分开写 -C", "tar -xf archive.tar -C ../outside", "OUTSIDE_WRITE"),
    ("tar -C 与值连写", "tar -x -C../outside -f archive.tar", "OUTSIDE_WRITE"),
    ("tar --directory=", "tar --directory=../outside -xf archive.tar", "OUTSIDE_WRITE"),
    ("tar 旧式写法", "tar xCf ../outside archive.tar", "OUTSIDE_WRITE"),
    ("tar 打包写到根外", "tar -czf ../out.tgz docs", "OUTSIDE_WRITE"),
    ("tar --to-command", "tar -xf archive.tar --to-command=sh", "OPAQUE"),
    ("tar 解包时改写成员路径", "tar -xf archive.tar -s ',^,../,'", "OUTSIDE_WRITE"),
    ("curl -o 连写", "curl -o../outside/file https://example.invalid/data", "OUTSIDE_WRITE"),
    ("curl -o 分开写", "curl -o ../outside/f https://example.invalid/data", "OUTSIDE_WRITE"),
    ("curl --output=", "curl --output=../outside/f https://example.invalid/data", "OUTSIDE_WRITE"),
    ("curl 组合短参数 -sSLo", "curl -sSLo../x https://example.invalid/data", "OUTSIDE_WRITE"),
    ("curl --output-dir", "curl --output-dir ../outside -O https://example.invalid/f", "OUTSIDE_WRITE"),
    ("curl -K 配置文件", "curl -K cfg https://example.invalid/data", "OPAQUE"),
    ("wget -P 连写", "wget -P../outside https://example.invalid/data", "OUTSIDE_WRITE"),
    ("wget -O 连写", "wget -O../outside/f https://example.invalid/data", "OUTSIDE_WRITE"),
    ("wget --directory-prefix=", "wget --directory-prefix=../outside https://example.invalid/data", "OUTSIDE_WRITE"),
    ("wget -e 执行指令", "wget -e robots=off https://example.invalid/data", "OPAQUE"),
    ("cp -t 连写", "cp -t../x a.txt", "OUTSIDE_WRITE"),
    ("touch -r 引用与目标都在根外", "touch -r /etc/hosts ../x", "OUTSIDE_WRITE"),
    ("macOS sed -i '' 改根外文件", "sed -i '' 's/a/b/' /etc/hosts", "OUTSIDE_WRITE"),
    # r3（边界独立审查四组及同语义变体）
    ("env -C 连写目录", "env -C../outside touch victim", "CHDIR"),
    ("env 组合短选项里带 -C", "env -iC../outside touch victim", "CHDIR"),
    ("env -C 分开写", "env -C ../outside touch victim", "CHDIR"),
    ("env --chdir 单独跟值", "env --chdir ../outside touch victim", "CHDIR"),
    ("env --chdir=", "env --chdir=../outside touch victim", "CHDIR"),
    ("env -S 连写", "env -S'rm -rf ../x'", "OPAQUE"),
    ("make -C 根外", "make -C ../other clean", "OPAQUE"),
    ("make -f 根外", "make -f /tmp/x.mk", "OPAQUE"),
    ("! 前缀的递归删除", "! rm -rf docs", "RECURSIVE_DELETE"),
    ("! 前缀的提权", "! sudo true", "PRIV"),
    ("{ } 命令组", "{ rm -rf ../x; }", "RECURSIVE_DELETE"),
    ("then 之后的命令", "then rm -rf ../x", "RECURSIVE_DELETE"),
    # r4：先解析符号链接再处理 ..，不能按字面折叠 link/..。
    ("符号链接后 .. 的 touch 仍在根外", "touch link-parent/../victim", "OUTSIDE_WRITE"),
    ("符号链接后 .. 的复制目标仍在根外", "cp a.txt link-parent/../copied", "OUTSIDE_WRITE"),
    ("clean_dirs 内链接后 .. 的删除仍在根外", "rm -rf build/link-parent/../victim", "OUTSIDE_WRITE"),
    ("删除链接带尾 slash 时跟随到根外", "rm -rf build/link-parent/", "OUTSIDE_WRITE"),
]
NEG_B1B = [
    ("clean_dirs 内递归删除", "rm -rf build/"), ("clean_dirs 内通配", "rm -rf build/sub/*.o"), ("clean_dirs 本身", "rm -rf build"),
    ("带注释的合法清理", "rm -rf build/ # 清理"), ("项目内删单个文件", "rm notes.txt"), ("项目内复制", "cp a.txt b.txt"),
    ("项目内移动", "mv a.txt docs/a.txt"), ("项目内建目录", "mkdir -p out/x"), ("项目内 touch", "touch docs/n.md"),
    ("读根外文件", "cat /etc/hosts"), ("git status", "git status"), ("git log", "git log -1"),
    ("launchctl list", "launchctl list"), ("普通 push", "git push origin main"), ("项目脚本", "python3 tools/build.py"),
    ("项目 shell 脚本", "bash scripts/x.sh"), ("python -m", "python3 -m json.tool a.txt"),
    ("引号里的危险文字", "echo \"rm -rf ../x\""), ("检索危险文字", "grep -n 'launchctl bootout' notes.txt"),
    ("引号里的 #", "echo '#'"), ("词中 #", "echo a#b"), ("$#", "echo $# x"),
    ("sed -i 的脚本以 / 开头", "sed -i '/^#/d' notes.txt"), ("find 只查", "find . -name '*.py'"), ("管道过滤", "ls | grep x"),
    ("kill -l", "kill -l"), ("自动安装的固定形式", "wf self-update --auto workflow/outbox/collab-wf-1.9.9.zip"),
    ("生产构建器形式", "python3 build_wf_198.py --out workflow/outbox"), ("2>&1", "echo x 2>&1"),
    ("python3 --version", "python3 --version"), ("bash --version", "bash --version"), ("command -v", "command -v rm"),
    ("读工具目录（非 local）", "cat " + str(WF_HOME / "VERSION")),
    # r2（常规独立审查 BLOCK-1..4 的正常用法对照）
    ("clean_dirs 内通配（链接项只删链接本身）", "rm -rf build/*"), ("clean_dirs 内大括号", "rm -rf build/{sub,nothing}"),
    ("clean_dirs 内序列展开", "rm -rf build/{a..c}"), ("tar 只列出", "tar -tf archive.tar"), ("tar 旧式只列出", "tar tf archive.tar"),
    ("tar 解包到项目内", "tar -xf archive.tar -C build/out"), ("tar 打包到项目内", "tar -czf build/out.tgz docs"),
    ("tar 解包到当前目录", "tar -xf archive.tar"),
    ("curl 输出到项目内", "curl -o build/out.json https://example.invalid/data"),
    ("curl 组合短参数输出到项目内", "curl -sSLo build/x https://example.invalid/data"),
    ("curl 只输出到标准输出", "curl -s https://example.invalid/data"),
    ("wget 存到项目内", "wget -P build https://example.invalid/data"), ("wget 输出到标准输出", "wget -qO- https://example.invalid/data"),
    ("cp 只读工具目录里的源", "cp " + str(WF_HOME / "VERSION") + " docs/tool-version"),
    ("touch -r 只读根外引用", "touch -r /etc/hosts docs/n.md"), ("macOS sed -i '' 的脚本以 / 开头", "sed -i '' '/^#/d' notes.txt"),
    ("sed -i.bak", "sed -i.bak 's/a/b/' notes.txt"), ("sed -i -e", "sed -i -e 's/a/b/' notes.txt"),
    ("cp 大括号目标在项目内", "cp a.txt build/{p,q}.txt"), ("install -m 的值不是路径", "install -m 644 a.txt build/a.txt"),
    ("mkdir -m 的值不是路径", "mkdir -m 755 out"), ("echo 路径文字", "echo /etc/hosts"),
    # r3（边界独立审查四组的正常用法对照）
    ("env 只设变量", "env MODE=test touch docs/victim"), ("env -u 分开写", "env -u MODE touch docs/victim"),
    ("env -u 连写", "env -uMODE touch docs/victim"), ("env -i", "env -i touch docs/victim"),
    ("make 项目内", "make build"), ("make -C 项目内", "make -C build"),
    ("! 前缀的合法清理", "! rm -rf build/sub"), ("引号里的 !", "echo \"!\""), ("文字里的 !", "echo 'hello!'"),
    ("! true", "! true"), ("! grep", "! grep -q x notes.txt"),
    # r4：普通 ..、根内链接后 .. 以及不跟随删除末段链接仍按真实目标判定。
    ("普通 .. 回到根内", "touch docs/../notes.txt"),
    ("根内链接后 .. 的写入", "touch link-in/../notes.txt"),
    ("clean_dirs 内链接后 .. 的清理", "rm -rf build/link-in/../cache"),
    ("删除末段链接本身", "rm -rf build/link-parent"),
]


def provider_destructive() -> dict:
    """C1 第 2 条 2S-destructive-or-crossproject（提供方 B）。
    B-C1-OLD2-GUARD：没有 WF_LOCAL_DIR 或它指向工具自己的 local/ 时，在业务导入与任何夹具之前以标准结果拒绝。"""
    refused = _pv_refusal("2S-destructive-or-crossproject")
    if refused:
        return refused
    from . import core, notify, policy

    def body(case, td):
        e = _b1b_env(td)
        root, pol, oth = e["root"], e["pol"], e["others"]
        for name, cmd, want in POS_B1B:
            case(f"命中：{name}（{cmd!r}）", "pos", lambda c=cmd, w=want: _b1b_pos(policy, c, pol, w, root=root, others=oth))
        for name, cmd in NEG_B1B:
            case(f"不误拦：{name}（{cmd!r}）", "neg", lambda c=cmd: _b1b_neg(policy, c, pol, root=root, others=oth))
        other = e["other"]
        case("命中：写进另一个已注册项目（显式 others）", "pos",
             lambda: _b1b_pos(policy, f"cp a.txt {other}/x", pol, "OTHER_PROJECT", root=root, others=oth))
        case("命中：读另一个项目的 workflow/", "pos",
             lambda: _b1b_pos(policy, f"cat {other}/workflow/state.json", pol, "OTHER_PROJECT", root=root, others=oth))
        case("命中：读工具运行状态目录 LOCAL", "pos",
             lambda: _b1b_pos(policy, f"cat {core.LOCAL}/transport.json", pol, "OTHER_PROJECT", root=root, others=oth))
        case("命中：cp 的源是 LOCAL（读也拦）", "pos",
             lambda: _b1b_pos(policy, f"cp {core.LOCAL}/transport.json docs/", pol, "OTHER_PROJECT", root=root, others=oth))
        case("命中：cp 写进工具目录", "pos",
             lambda: _b1b_pos(policy, f"cp a.txt {WF_HOME}/b1b-x", pol, "OTHER_PROJECT", root=root, others=oth))
        # r3：受保护目录不分大小写（macOS 默认文件系统不分大小写，换个大小写是同一个目录）
        case("命中：LOCAL 换个大小写也拦", "pos",
             lambda: _b1b_pos(policy, f"cat {str(core.LOCAL).swapcase()}/transport.json", pol, "OTHER_PROJECT",
                              root=root, others=oth))

        # r3：clean_dirs 的保护名按规范化后的路径段核对
        def norm_clean():
            bad = ["./workflow", "././workflow/", "./.git", "Workflow", "build/../workflow"]
            ok_dirs, probs = policy.clean_dirs({"clean_dirs": bad}, root)
            hits = [_b1b_pos(policy, cmd, {**pol, "clean_dirs": [cd]}, "RECURSIVE_DELETE", root=root, others=oth)[0]
                    for cd, cmd in (("./workflow", "rm -rf workflow"), ("././workflow/", "rm -rf ./workflow"),
                                    ("Workflow", "rm -rf Workflow"))]
            return ok_dirs == [] and len(probs) == len(bad) and all(hits), f"{ok_dirs} {len(probs)} {hits}"
        case("命中：./workflow、././workflow/、大小写变体都不能成为清理目录", "pos", norm_clean)

        def norm_clean_ok():
            ok_dirs, probs = policy.clean_dirs({"clean_dirs": ["./build", "build/./sub"]}, root)
            p2 = {**pol, "clean_dirs": ["./build"]}
            negs = [_b1b_neg(policy, cmd, p2, root=root, others=oth)[0] for cmd in ("rm -rf ./build/", "rm -rf build/sub")]
            return ok_dirs == ["build", "build/sub"] and not probs and all(negs), f"{ok_dirs} {probs} {negs}"
        case("不误拦：./build 规范化后正常生效", "neg", norm_clean_ok)

        # r3：递归操作的范围之内有另一个已注册项目（嵌套在 clean 目录里）
        n2 = td / "nested-case"
        for d in ("build/sub", "build/nested/workflow", "src"):
            (n2 / d).mkdir(parents=True, exist_ok=True)
        (n2 / "build" / "nested" / "workflow" / "state.json").write_text("{}\n", encoding="utf-8")
        on2 = [{"name": "nested", "root": str(n2 / "build" / "nested")}]
        p3 = {**pol, "clean_dirs": ["build"]}
        for cmd in ("rm -rf build", "rm -rf build/*", "mv build build2", "chmod -R 777 build", "rsync -a --delete src/ build/"):
            case(f"命中：递归范围包含另一个项目（{cmd!r}）", "pos",
                 lambda c=cmd: _b1b_pos(policy, c, p3, "OTHER_PROJECT", root=n2, others=on2))
        for cmd in ("rm -rf build/sub", "chmod -R 755 build/sub", "rsync -a --delete src/ build/sub/", "mv build/sub build/sub2"):
            case(f"不误拦：与另一个项目不相交（{cmd!r}）", "neg",
                 lambda c=cmd: _b1b_neg(policy, c, p3, root=n2, others=on2))

        def via_registry():
            core.register("b1b-other", other)
            try:
                return _b1b_pos(policy, f"cp a.txt {other}/y", pol, "OTHER_PROJECT", root=root)
            finally:
                reg = core.load_registry()
                reg["projects"] = [p for p in reg["projects"] if p.get("name") != "b1b-other"]
                core.write_json(core.REGISTRY, reg)
        case("命中：另一个项目来自注册表（others 缺省）", "pos", via_registry)
        case("命中：没有 root 时递归删除一律命中（无法核实 clean_dirs）", "pos",
             lambda: _b1b_pos(policy, "rm -rf build/", pol, "RECURSIVE_DELETE"))
        case("不误拦：没有 root 时项目内普通写照常", "neg", lambda: _b1b_neg(policy, "cp a.txt b.txt", pol))

        def bad_clean():
            p3 = {**pol, "clean_dirs": ["../x", "workflow", ".git", "/abs", "~/h"]}
            ok_dirs, probs = policy.clean_dirs(p3, root)
            hits = [_b1b_pos(policy, c, p3, "RECURSIVE_DELETE", root=root, others=oth)[0]
                    for c in ("rm -rf ../x/", "rm -rf workflow/", "rm -rf .git")]
            return ok_dirs == [] and len(probs) == 5 and all(hits), f"{ok_dirs} {len(probs)} {hits}"
        case("命中：clean_dirs 里的无效项一律不生效", "pos", bad_clean)

        def floor_kept():
            r = policy.classify("cat .env", pol, root=root, others=oth)
            why = "- 命令 1「cat .env」：" + "；".join(r["reasons"])
            return (not r["auto"] and "密钥" in r["categories"]
                    and notify.route({"kind": "approve", "why": why}) == "user"), str(r["categories"])
        case("命中：底线（密钥）仍停下且只交用户", "pos", floor_kept)

        def strong_route():
            r = policy.classify("rm -rf ../x", pol, root=root, others=oth)
            why = "- 命令 1「rm -rf ../x」：" + "；".join(r["reasons"])
            return (not r["auto"] and notify.route({"kind": "approve", "why": why}) == "executor"), why[:200]
        case("命中：强门禁先交执行端（不冒充底线、不自动执行）", "pos", strong_route)

        def compat():
            r = policy.classify("echo hi", pol)
            r2 = policy.classify("rm -rf build", {"allow": ["^echo "]})
            return (set(r) == {"cmd", "auto", "reasons", "categories", "manual", "strong"} and r["auto"]
                    and "不在已批准的命令白名单中" in r2["reasons"] and any("强门禁" in x for x in r2["reasons"])), \
                str(r2["reasons"])[:200]
        case("不误拦：两参数调用兼容，原有原因保留、只追加", "neg", compat)
        e2e = td / "e2e"
        e2e.mkdir()

        def e2e_pos():
            s, rv, routes = _e2e_review(e2e, "e2e-pos", ["rm -rf ../x"], pol)
            return s == "AWAITING_USER" and _rules(rv["rows"][0]) and routes == ["executor"], f"{s} {routes}"
        case("命中：真实 h_review 下 allow=.* 也停在等待批准、路由执行端", "pos", e2e_pos)

        def e2e_neg():
            s, rv, routes = _e2e_review(e2e, "e2e-neg", ["echo hi"], pol)
            return s != "AWAITING_USER" and rv["auto"] and routes == [], f"{s} {routes}"
        case("不误拦：真实 h_review 下普通命令照常通过", "neg", e2e_neg)
    return _pv("2S-destructive-or-crossproject", body, guard=True)


def provider_widening() -> dict:
    """C1 第 2 条 2S-widening（提供方 B）：权限扩大不自动批准，收紧照常自动批准。
    B-C1-OLD2-GUARD：隔离前提不足时，在业务导入与任何夹具之前以标准结果拒绝。"""
    refused = _pv_refusal("2S-widening")
    if refused:
        return refused
    import time
    from . import driver, policy
    old = {"allow": ["^a ", "^b "], "stop": [{"pattern": "x"}], "max_rounds_per_day": 24, "clean_dirs": ["build"],
           "pipe_filters": ["head", "tail", "grep"]}
    widen = [("allow 新增 .*", {**old, "allow": ["^a ", "^b ", ".*"]}),
             ("clean_dirs 新增", {**old, "clean_dirs": ["build", "dist"]}),
             ("删掉停止规则", {**old, "stop": []}),
             ("新开自动安装", {**old, "auto_self_update": True}),
             ("不认识的键", {**old, "foo": 1})]
    narrow = [("删 allow", {**old, "allow": ["^a "]}), ("删 clean_dirs", {**old, "clean_dirs": []}),
              ("调低每日上限", {**old, "max_rounds_per_day": 10}), ("只改时间参数", {**old, "poll_interval_s": 30})]

    def body(case, td):
        for name, new in widen:
            case(f"命中：{name}", "pos", lambda n=new: (bool(policy.widening(old, n)), policy.widening(old, n)))
        for name, new in narrow:
            case(f"不误拦：{name}", "neg", lambda n=new: (policy.widening(old, n) == [], policy.widening(old, n)))

        def setup(name):
            inst = _mk_project(td, name)
            (inst.wf / "policy.json").write_text(json.dumps(old), encoding="utf-8")
            policy.approve(inst, "policy", "测试")
            return inst

        def findings(new):
            inst = setup(f"rf-{len(json.dumps(new))}-{os.urandom(3).hex()}")
            src = td / f"src-{os.urandom(3).hex()}"
            (src / "workflow").mkdir(parents=True)
            (src / "workflow" / "policy.json").write_text(json.dumps(new), encoding="utf-8")
            return policy.release_findings(inst, src, old)
        case("命中：版本包里扩大的 policy.json 给出 finding", "pos",
             lambda: (bool(findings(widen[0][1])), "有 finding"))
        case("不误拦：版本包里收紧的 policy.json 没有 finding", "neg",
             lambda: (findings(narrow[0][1]) == [], "无 finding"))

        def auto_approve(new):
            inst = setup(f"aa-{os.urandom(3).hex()}")
            before = {"policy": {"approved": True, "sha": policy.sha256(inst.wf / "policy.json")}, "goals": {}}
            (inst.wf / "policy.json").write_text(json.dumps(new), encoding="utf-8")
            driver._auto_approve_neutral(driver.Ctx(inst, old, time.time() + 60), before, "v0.2")
            return policy.status(inst, "policy")["approved"]
        case("命中：扩大的变更落地后不被驱动器自动批准", "pos", lambda: (auto_approve(widen[1][1]) is False, "未批准"))
        case("不误拦：收紧的变更落地后由驱动器自动批准", "neg", lambda: (auto_approve(narrow[1][1]) is True, "已批准"))
    return _pv("2S-widening", body, guard=True)


# ---------------- M2-C1：B 车道另三类提供方（B-C1-PROVIDERS）：1-init-files、2S-unverified-install、6a-install-review ----------------
# 只消费现有业务 API（admin.init_project、review.prepare/run/final_check/consume、release.apply_zip），不重做业务。
# 每次运行都在私有临时宿主里：所有 wflib 模块中指向当前 LOCAL 的路径（含 REGISTRY）改指临时 local，结束时恢复；
# 审查用 selftest_review.Env 的合成 runner/核验器，决定里如实为 synthetic。

def _c1_guard() -> str:
    from .core import LOCAL, WF_HOME
    if not os.environ.get("WF_LOCAL_DIR"):
        return "没有设置 WF_LOCAL_DIR，拒绝在真实 local/ 上运行"
    if Path(LOCAL).resolve() == (WF_HOME / "local").resolve():
        return "WF_LOCAL_DIR 指向工具自己的 local/，拒绝运行"
    return ""


def _c1_wflib_modules() -> list:
    return [(n, m) for n, m in list(sys.modules.items()) if m is not None and (n == "wflib" or n.startswith("wflib."))]


def _c1_under(p: Path, root: Path) -> bool:
    return p == root or root in p.parents


def _c1_remap(value, host: Path, orig: Path):
    """宿主路径换回调用方 LOCAL 下的同一相对位置；返回 (新值, 是否改了)。只认 Path 与绝对路径字符串，以及一层容器。"""
    if isinstance(value, Path):
        try:
            return orig / value.relative_to(host), True
        except ValueError:
            return value, False
    if isinstance(value, str):
        h = str(host)
        if value == h or value.startswith(h + os.sep):
            return str(orig) + value[len(h):], True
        return value, False
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_c1_remap(v, host, orig) for v in value]
        return (type(value)(v for v, _ in items), True) if any(c for _, c in items) else (value, False)
    if isinstance(value, dict):
        items = [(_c1_remap(k, host, orig), _c1_remap(v, host, orig)) for k, v in value.items()]
        if any(kc or vc for (_, kc), (_, vc) in items):
            return {k: v for (k, _), (v, _) in items}, True
        return value, False
    return value, False


def _c1_host_refusal(old: Path, new: Path, registry) -> str:
    """进入宿主前的全部拒绝条件（B-C1-F4）；纯检查：不建目录、不改任何模块属性或环境变量，返回拒绝原因或空串。
    - 调用方 LOCAL 与宿主都必须是绝对路径；词法上相等、互相包含都拒绝；
    - REGISTRY 必须是 LOCAL 之下的 Path，且相对部分不含 ..（否则进入后会落到宿主之外）；
    - 解析后（跟随 symlink、按真实顺序处理 ..）宿主与 LOCAL、REGISTRY 交叠拒绝；
    - 已加载的 wflib 模块里进入前就有指向宿主的值时拒绝：退出时宿主路径会被换回 LOCAL，不能误改调用方原有的值。"""
    if not (old.is_absolute() and new.is_absolute()):
        return f"宿主 {new} 或调用方 LOCAL {old} 不是绝对路径，拒绝运行"
    if _c1_under(new, old) or _c1_under(old, new):
        return f"宿主 {new} 与调用方 LOCAL {old} 交叠，拒绝运行"
    if not isinstance(registry, Path) or not _c1_under(registry, old) or ".." in registry.relative_to(old).parts:
        return f"REGISTRY {registry} 不在 LOCAL {old} 之下，拒绝对真实注册表运行"
    old_real, new_real, registry_real = (p.resolve(strict=False) for p in (old, new, registry))
    if (_c1_under(new_real, old_real) or _c1_under(old_real, new_real)
            or _c1_under(new_real, registry_real) or _c1_under(registry_real, new_real)):
        return f"宿主解析路径 {new_real} 与调用方 LOCAL/REGISTRY 交叠，拒绝运行"
    pre = [f"{n}.{a}" for n, m in _c1_wflib_modules() for a, v in list(vars(m).items()) if _c1_remap(v, new, old)[1]]
    if pre:
        return f"进入前已有模块引用宿主 {new}：" + "、".join(pre[:8]) + "，拒绝运行"
    return ""


@contextlib.contextmanager
def _c1_host(td: Path):
    """私有宿主（B-C1-PROVIDERS-REWORK F1；B-C1-F4）。
    进入前：_c1_host_refusal 的全部拒绝都在建目录与改任何状态之前完成，拒绝时直接抛出原因——此时还没有进入，
    不执行下面的恢复与换回，所以调用方 core.LOCAL/REGISTRY、WF_LOCAL_DIR 与所有已加载模块的值都是原来的同一个对象。
    进入时：已导入的 wflib 模块里指向当前 LOCAL 的 Path 属性改指宿主；REGISTRY 必须随之落在宿主内，否则拒绝。
    退出时（只对已进入的宿主；正常、用例异常、初始化或执行异常都走 finally）：
    - 进入前已有的属性恢复为原来的同一个对象；
    - 上下文内才加载的模块（以及进入前已有模块里新出现的宿主引用）原地把宿主路径换回调用方 LOCAL 下的同一相对位置，
      所以宿主外早已持有的模块对象也一起恢复；不删 sys.modules 条目，不改与宿主无关的属性；
    - 之后扫描全部 wflib 模块，仍有任何宿主路径就抛错（失败关闭）。
    支持嵌套：内层的“调用方 LOCAL”就是外层宿主。"""
    from . import core
    old, new = Path(core.LOCAL), Path(td) / "host-local"
    refusal = _c1_host_refusal(old, new, core.REGISTRY)
    if refusal:
        raise RuntimeError(refusal)
    new.mkdir(parents=True)   # 失败时同样还没有改动任何状态，不进入恢复阶段
    patched, saved = [], os.environ.get("WF_LOCAL_DIR")
    try:
        for name, mod in _c1_wflib_modules():
            for attr, val in list(vars(mod).items()):
                if isinstance(val, Path) and _c1_under(val, old):
                    patched.append((mod, attr, val))
                    setattr(mod, attr, new / val.relative_to(old))
        if not _c1_under(Path(core.REGISTRY), new):
            raise RuntimeError(f"REGISTRY {core.REGISTRY} 不在 LOCAL {old} 之下，拒绝对真实注册表运行")
        os.environ["WF_LOCAL_DIR"] = str(new)
        yield new
    finally:
        for mod, attr, val in reversed(patched):
            setattr(mod, attr, val)
        if saved is None:
            os.environ.pop("WF_LOCAL_DIR", None)
        else:
            os.environ["WF_LOCAL_DIR"] = saved
        restored = {id(mod): {attr for m, attr, _ in patched if m is mod} for mod, _a, _v in patched}
        for name, mod in _c1_wflib_modules():
            keep = restored.get(id(mod), set())
            for attr, val in list(vars(mod).items()):
                if attr in keep:
                    continue
                remapped, changed = _c1_remap(val, new, old)
                if changed:
                    setattr(mod, attr, remapped)
        leaks = [f"{n}.{a}" for n, m in _c1_wflib_modules() for a, v in vars(m).items() if _c1_remap(v, new, old)[1]]
        if leaks:
            raise RuntimeError("退出宿主后仍有宿主路径：" + "、".join(leaks[:8]))


def _target_state(root: Path) -> tuple:
    """目标完整状态：每个路径的类型、mode 与字节（不进 .git），加 HEAD 与 git status。"""
    out = {}
    for dp, dns, fns in os.walk(root, followlinks=False):
        if Path(dp) == root:
            dns[:] = [d for d in dns if d != ".git"]
        for n in sorted(dns + fns):
            p = Path(dp) / n
            st = os.lstat(p)
            body = p.read_bytes() if stat.S_ISREG(st.st_mode) else os.readlink(p) if stat.S_ISLNK(st.st_mode) else None
            out[str(p.relative_to(root))] = (stat.S_IFMT(st.st_mode), stat.S_IMODE(st.st_mode), body)
    from .selftest_review import _git as rgit
    return out, rgit(root, "rev-parse", "HEAD"), rgit(root, "status", "--porcelain", "--untracked-files=all")


@contextlib.contextmanager
def _c1_review_host(td: Path):
    from . import review
    from .selftest_review import Env
    with _c1_host(td):
        env = Env(td / "review-env")
        with _patched(review, "_tool_home", lambda: env.tool):
            yield env


def _c1_project(env, td: Path, tag: str):
    from . import admin
    from .selftest import _project_md
    from .selftest_review import _git as rgit
    inst = admin.init_project(td / tag, name=f"c1b-{tag}-{os.getpid()}")
    rgit(inst.root, "init", "-q")
    rgit(inst.root, "add", "-A")
    rgit(inst.root, "commit", "-qm", "initial")
    pkg = env.pkg(f"c1b-{tag}", {"PROJECT.md": _project_md("v0.2").encode(),
                                 "CHANGELOG.md": "# CHANGELOG\n\n## v0.2 — c1b\n- installed\n".encode(),
                                 "app.py": ("v0.2-" + tag).encode()})
    return inst, pkg


def _c1_install(inst, pkg, rid) -> tuple:
    """真实 release.apply_zip；返回 (结果或拒绝原文, 目标前后完全相同, 新增的 started 记录)。"""
    from . import release
    from .core import WfError
    store = inst.root / "workflow" / "evidence" / "install"
    started0 = set(store.glob("tx-*/started.json"))
    before = _target_state(inst.root)
    try:
        r = release.apply_zip(inst, pkg, review_id=rid)
        outcome = "installed " + str(r.get("install_tx"))
    except WfError as e:
        outcome = "refused: " + str(e)
    return outcome, _target_state(inst.root) == before, sorted(set(store.glob("tx-*/started.json")) - started0)


def _c1_refused(outcome, same, started, want) -> tuple:
    return (outcome.startswith("refused") and want in outcome and same and not started,
            f"{outcome[:180]} | 目标字节/mode/HEAD/status 不变={same} | started={len(started)}")


def provider_init_files() -> dict:
    """C1 第 1 条 1-init-files（提供方 B）：新建项目的默认文件与写入边界。init 不执行门禁，本类不代表 1-init-gates-real。"""
    import subprocess as sp
    from . import admin, core, goals, policy
    from .core import Instance, WfError, read_json, read_jsonl

    def body(case, td):
        with _c1_host(td):
            def fresh_defaults():
                seen, real_popen = [], sp.Popen

                class Spy(real_popen):
                    def __init__(self, args, *a, **k):
                        seen.append(args if isinstance(args, str) else [str(x) for x in args])
                        super().__init__(args, *a, **k)
                root = td / "init-new"
                root.mkdir()
                _git(root, "init", "-q")
                with _patched(sp, "Popen", Spy):
                    inst = admin.init_project(root, f"c1b-init-new-{os.getpid()}")
                pol = json.loads((inst.wf / "policy.json").read_text(encoding="utf-8"))
                gl = json.loads((inst.wf / "goals.json").read_text(encoding="utf-8"))
                gates = [(g.get("name"), g.get("cmd"), g.get("pass")) for g in pol.get("gates", [])]
                ev = [e for e in read_jsonl(inst.events_path) if e.get("type") == "init"]
                flat = [" ".join(x) if isinstance(x, list) else x for x in seen]
                ok = (gates == [(n, c, {"exit": 0}) for n, c in admin.INIT_GATES]
                      and [c for _, c in admin.INIT_GATES] == ["wf self-check", "wf self-check --probes"]
                      and pol.get("allow") == [] and pol.get("auto_apply") is False and pol.get("auto_self_update") is False
                      and pol.get("auto_new_conversation") is False
                      and all(policy.readonly_check(c) for c in _init_cmds(pol, gl))
                      and goals.validate(gl) == [] and gl.get("_skeleton") == goals.SKELETON_VERSION
                      and not policy.status(inst, "policy")["approved"] and not policy.status(inst, "goals")["approved"]
                      and not (inst.wf / "approvals.json").exists() and policy.load(Instance(inst.root)) is None
                      and len(ev) == 1 and ev[0].get("defaults") == list(admin.INIT_DEFAULT_FILES)
                      and bool(seen) and all((x[0] if isinstance(x, list) else x.split()[0]) == "git" for x in seen)
                      and not any("self-check" in f or "wf.py" in f or "/bin/wf" in f for f in flat))
                return ok, json.dumps({"gates": gates, "allow": pol.get("allow"),
                                       "auto": [pol.get(k) for k in ("auto_apply", "auto_self_update", "auto_new_conversation")],
                                       "approved": [policy.status(inst, k)["approved"] for k in ("policy", "goals")],
                                       "subprocesses": sorted({(x[0] if isinstance(x, list) else x.split()[0]) for x in seen})},
                                      ensure_ascii=False)
            case("写出：新项目默认 policy/goals（allow 空、三项 auto 关、两道固定只读门禁 exit 0、都未批准、无自批准；init 只启动 git）",
                 "pos", fresh_defaults)

            def existing_kept():
                root = td / "init-existing"
                (root / "workflow").mkdir(parents=True)
                mine_p, mine_g = b'{"allow": ["^echo "]}\n', b'{"goals": "user-owned"}'
                (root / "workflow" / "policy.json").write_bytes(mine_p)
                (root / "workflow" / "goals.json").write_bytes(mine_g)
                inst = admin.init_project(root, f"c1b-init-existing-{os.getpid()}")
                ev = [e for e in read_jsonl(inst.events_path) if e.get("type") == "init"][-1]
                return ((root / "workflow" / "policy.json").read_bytes() == mine_p
                        and (root / "workflow" / "goals.json").read_bytes() == mine_g and ev.get("defaults") == []
                        and ev.get("defaults_kept") == list(admin.INIT_DEFAULT_FILES)), json.dumps(ev, ensure_ascii=False)[:200]
            case("不覆写：已有 policy/goals 字节不变，事件记为保留", "neg", existing_kept)

            def adopt_writes_none():
                root = td / "init-adopt"
                root.mkdir()
                (root / "PROJECT.md").write_text("# PROJECT.md\n", encoding="utf-8")
                calls = []
                with _patched(goals, "default_goals", lambda *a, **k: calls.append(a) or {}):
                    admin.init_project(root, f"c1b-init-adopt-{os.getpid()}", adopt=True)
                return (not (root / "workflow" / "policy.json").exists() and not (root / "workflow" / "goals.json").exists()
                        and calls == []), f"skeleton_calls={len(calls)}"
            case("不写：--adopt 不写默认文件、不调用骨架接口", "neg", adopt_writes_none)

            def refused_without_residue(tag, patch_obj, attr, value, exc):
                root, name = td / tag, f"c1b-{tag}-{os.getpid()}"
                reg = read_json(core.REGISTRY, None)
                try:
                    with _patched(patch_obj, attr, value):
                        admin.init_project(root, name)
                    return False, "接受了"
                except exc as e:
                    return (not root.exists() and read_json(core.REGISTRY, None) == reg), f"{type(e).__name__}: {str(e)[:120]}"
            case("不留半套：骨架不合格时整次拒绝，不建目录、不注册", "neg",
                 lambda: refused_without_residue("init-bad-skeleton", goals, "default_goals",
                                                 lambda *a, **k: {"goals": []}, WfError))
            real_write = admin._write_new
            n = [0]

            def flaky(p, data):
                n[0] += 1
                if n[0] == 2:
                    raise OSError(28, "c1b 注入：写第二份时磁盘满")
                return real_write(p, data)
            case("不留半套：写第二份默认文件失败时撤回第一份，不建目录、不注册", "neg",
                 lambda: refused_without_residue("init-half", admin, "_write_new", flaky, OSError))

            def linked_workflow():
                outside = td / "init-link-target"
                outside.mkdir()
                (outside / "marker.txt").write_bytes(b"OUTSIDE-UNCHANGED\n")
                root = td / "init-link"
                root.mkdir()
                os.symlink(str(outside), root / "workflow")
                before, reg = _tree_snapshot(outside), read_json(core.REGISTRY, None)
                try:
                    admin.init_project(root, f"c1b-init-link-{os.getpid()}")
                    return False, "接受了"
                except WfError as e:
                    return (_tree_snapshot(outside) == before and read_json(core.REGISTRY, None) == reg
                            and os.path.islink(root / "workflow")), str(e)[:120]
            case("写入前拒绝：workflow 是指向项目外的符号链接，链接目标与注册表都不变", "neg", linked_workflow)
    return _pv("1-init-files", body, guard=True)


def provider_unverified_install() -> dict:
    """C1 第 2 条 2S-unverified-install（提供方 B）：未经独立核验的安装被真实拒绝且目标完整不变；合法核验后可消费。"""
    from . import review

    def body(case, td):
        with _c1_review_host(td) as env:
            def none_rid():
                inst, pkg = _c1_project(env, td, "u-none")
                return _c1_refused(*_c1_install(inst, pkg, None), "unreviewed_install")
            case("拒绝：没有 review_id（未核验的安装）", "pos", none_rid)

            def claimed_only():
                from .selftest import _project_md
                inst, _pkg = _c1_project(env, td, "u-claimed")
                z = env.pkg("c1b-u-claimed-selfclaim", {
                    "PROJECT.md": _project_md("v0.2").encode(), "CHANGELOG.md": "# CHANGELOG\n\n## v0.2 — c1b\n- x\n".encode(),
                    "app.py": b"v0.2-claimed", "review.json": json.dumps(
                        {"verdict": "pass", "provenance": {"verified": True, "level": "platform_verified"}}).encode()})
                rid = review.prepare(inst, z, kind="project", author="B")["request_id"]   # 只有宿主请求，没有审查运行
                return _c1_refused(*_c1_install(inst, z, rid), "no_review")
            case("拒绝：包内自带 review.json 自称已核验、宿主没有审查运行", "pos", claimed_only)

            def pin_mismatch():
                inst, pkg = _c1_project(env, td, "u-pin")
                rid = review.prepare(inst, pkg, kind="project", author="B")["request_id"]
                env.set_transport(pinned="0" * 64)
                try:
                    d = review.run(rid)
                finally:
                    env.set_transport()
                ok, detail = _c1_refused(*_c1_install(inst, pkg, rid), "verifier_sha256_mismatch")
                return ok and not d["allowed"], detail
            case("拒绝：核验器 sha 与宿主固定值不符", "pos", pin_mismatch)

            def blocked():
                inst, pkg = _c1_project(env, td, "u-block")
                rid = review.prepare(inst, pkg, kind="project", author="B")["request_id"]
                env.behave(verdict="block")
                try:
                    d = review.run(rid)
                finally:
                    env.behave()
                ok, detail = _c1_refused(*_c1_install(inst, pkg, rid), "blocked")
                return ok and d["verdict"] == "block", detail
            case("拒绝：独立审查给出 block", "pos", blocked)

            def verified_consumed():
                inst, pkg = _c1_project(env, td, "u-ok")
                req = review.prepare(inst, pkg, kind="project", author="B")
                d = review.run(req["request_id"])
                outcome, same, started = _c1_install(inst, pkg, req["request_id"])
                recs = list((review._reviews() / "installs" / req["content_id"]).glob("*.json"))
                done = list((inst.root / "workflow" / "evidence" / "install").glob("tx-*/completed.json"))
                ok = (d["allowed"] and d["provenance"]["synthetic"] is True and outcome.startswith("installed")
                      and not same and len(started) == 1 and len(done) == 1 and len(recs) == 1
                      and (inst.root / "app.py").read_bytes() == b"v0.2-u-ok")
                return ok, f"{outcome[:80]} synthetic={d['provenance']['synthetic']} consumed={len(recs)} completed={len(done)}"
            case("可消费：合法独立核验（合成宿主，如实 synthetic）后安装真实写入并登记消费", "neg", verified_consumed)
    return _pv("2S-unverified-install", body, guard=True)


def _c1_flat(x, prefix=""):
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            out.update(_c1_flat(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    return {prefix: x}


def _c1_frozen_drift(frozen: dict, decision: dict) -> list:
    """安装开始绑定的决定与审查完成时的冻结决定逐字段比较（含 provenance 的审查身份、核验器 sha、模型）。
    只有两处合法差异：phase 由 review 变为 install；times.install_started 由空变为实际时刻。其余任何不同都算漂移。"""
    a, b, missing = _c1_flat(frozen), _c1_flat(decision), object()
    drift = sorted(k for k in set(a) | set(b) if k not in ("phase", "times.install_started")
                   and a.get(k, missing) != b.get(k, missing))
    if a.get("phase") != "review" or b.get("phase") != "install":
        drift.append("phase")
    if a.get("times.install_started") is not None or not isinstance(b.get("times.install_started"), str):
        drift.append("times.install_started")
    return drift


def provider_install_review() -> dict:
    """C1 第 6a 条 6a-install-review（提供方 B）：安装开始绑定冻结的审查身份；审查后包、基线、配置、规则漂移都在开始前拒绝。"""
    from . import review
    from .selftest_review import _git as rgit

    def body(case, td):
        with _c1_review_host(td) as env:
            def approved(tag):
                inst, pkg = _c1_project(env, td, tag)
                req = review.prepare(inst, pkg, kind="project", author="B")
                d = review.run(req["request_id"])
                if not d["allowed"]:
                    raise RuntimeError("fixture review not allowed: " + "；".join(d["reasons"]))
                return inst, pkg, req, d

            def bound():
                inst, pkg, req, d = approved("r-bind")
                frozen = json.loads(json.dumps(d))   # 独立观察源：真实 review.run 完成时返回的冻结决定（安装之前取得）
                outcome, _same, started = _c1_install(inst, pkg, req["request_id"])
                if len(started) != 1:
                    return False, outcome[:200]
                st = json.loads(started[0].read_bytes())
                prepared = json.loads((started[0].parent / "prepared.json").read_bytes())
                [rec] = [json.loads(p.read_bytes()) for p in (review._reviews() / "installs" / req["content_id"]).glob("*.json")]
                t = st["decision"]["times"]
                d_start, d_record = _c1_frozen_drift(frozen, st["decision"]), _c1_frozen_drift(frozen, rec["decision"])
                ok = (outcome.startswith("installed") and not d_start and not d_record and st["decision"] == rec["decision"]
                      and frozen["provenance"]["synthetic"] is True and frozen["request_id"] == req["request_id"]
                      and frozen["content_id"] == req["content_id"] and rec["attempt_id"] == frozen["attempt_id"]
                      and rec["request_id"] == frozen["request_id"] and rec["content_id"] == frozen["content_id"]
                      and rec["install_context"] == {"tx_id": started[0].parent.name, "target_root": str(inst.root),
                                                     "lock_token": prepared["lock"]["token"],
                                                     "prepared_sha256": prepared["body_sha256"]}
                      and t["review_started"] <= t["review_completed"] <= t["result_registered"] <= t["install_started"]
                      and rec["install_started"] == t["install_started"])
                return ok, json.dumps({"drift_vs_frozen": {"start": d_start, "record": d_record},
                                       "attempt": frozen["attempt_id"], "tx": started[0].parent.name}, ensure_ascii=False)[:300]
            case("绑定：安装开始记录与审查登记的冻结决定、attempt、时刻、事务上下文一致", "neg", bound)

            def package_drift():
                inst, pkg, req, _d = approved("r-pkg")
                env.pkg(f"c1b-r-pkg", {"PROJECT.md": b"# drifted\n", "app.py": b"drifted"})   # 同一路径换了字节
                return _c1_refused(*_c1_install(inst, pkg, req["request_id"]), "package_mismatch")
            case("拒绝：审查后调用方的包被换", "pos", package_drift)

            def base_drift():
                inst, pkg, req, _d = approved("r-base")
                (inst.root / "app.py").write_text("目标在审查后被改\n", encoding="utf-8")
                rgit(inst.root, "add", "-A")
                rgit(inst.root, "commit", "-qm", "drift after review")
                return _c1_refused(*_c1_install(inst, pkg, req["request_id"]), "material_changed；diff_changed")
            case("拒绝：审查后目标基线被改（已提交）", "pos", base_drift)

            def config_drift():
                inst, pkg, req, _d = approved("r-cfg")
                cfg = json.loads(inst.cfg_path.read_text(encoding="utf-8"))
                cfg["protect"] = ["secret/"]
                inst.cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
                rgit(inst.root, "add", "-A")
                rgit(inst.root, "commit", "-qm", "config after review")
                return _c1_refused(*_c1_install(inst, pkg, req["request_id"]), "material_changed；diff_changed")
            case("拒绝：审查后项目配置被改（已提交）", "pos", config_drift)

            def rules_drift():
                inst, pkg, req, _d = approved("r-rules")
                rules = env.tool / "docs" / "REVIEW.md"
                old = rules.read_bytes()
                rules.write_bytes(old + b"\n# rules changed after review\n")
                try:
                    return _c1_refused(*_c1_install(inst, pkg, req["request_id"]), "material_changed")
                finally:
                    rules.write_bytes(old)
            case("拒绝：审查后审查规则被改", "pos", rules_drift)
    return _pv("6a-install-review", body, guard=True)


C1_PROVIDERS_B1B = {"2S-destructive-or-crossproject": provider_destructive, "2S-widening": provider_widening}
C1_PROVIDERS_NEW = {"1-init-files": provider_init_files, "2S-unverified-install": provider_unverified_install,
                    "6a-install-review": provider_install_review}
C1_CASE_KEYS = {"name", "polarity", "ran", "ok", "detail"}


def _c1_shape_problems(r, category) -> list:
    """c1-provider/1 严格机器检查（BATCH2_CONTRACT provider_api）。"""
    if not isinstance(r, dict):
        return ["not a dict"]
    p = []
    if set(r) != {"category", "provider", "schema", "cases", "error"}:
        p.append("keys " + repr(sorted(r)))
    if r.get("schema") != "c1-provider/1" or r.get("provider") != "B" or r.get("category") != category:
        p.append("schema/provider/category")
    cases = r.get("cases")
    if not isinstance(cases, list):
        return p + ["cases not a list"]
    for c in cases:
        if not isinstance(c, dict) or set(c) != C1_CASE_KEYS:
            p.append("case keys")
            continue
        if not isinstance(c["name"], str) or not c["name"] or c["polarity"] not in ("pos", "neg") \
                or type(c["ran"]) is not bool or type(c["ok"]) is not bool or not isinstance(c["detail"], str):
            p.append("case types " + str(c.get("name")))
        if c["ok"] and not c["ran"]:
            p.append("ok without ran")
    names = [c.get("name") for c in cases if isinstance(c, dict)]
    if len(set(names)) != len(names):
        p.append("duplicate case names")
    success = (bool(cases) and {c.get("polarity") for c in cases if isinstance(c, dict)} == {"pos", "neg"}
               and all(isinstance(c, dict) and c.get("ran") is True and c.get("ok") is True for c in cases))
    if success and r.get("error") is not None:
        p.append("error on success")
    if not success and not (isinstance(r.get("error"), str) and r["error"]):
        p.append("missing error on failure")
    return p

C1_PROVIDERS = {**C1_PROVIDERS_B1B, **C1_PROVIDERS_NEW}


def _pv_ok(r: dict) -> tuple:
    cs = r.get("cases") or []
    pos = [c for c in cs if c["polarity"] == "pos"]
    neg = [c for c in cs if c["polarity"] == "neg"]
    bad = [c for c in cs if not (c["ran"] and c["ok"])]
    good = (r.get("schema") == "c1-provider/1" and not r.get("error") and pos and neg and not bad)
    return bool(good), f"pos {len(pos)} neg {len(neg)} 失败 {[(c['name'], c['detail']) for c in bad][:4]} error={r.get('error')}"


def _t_b1b_providers(_ok, base: Path):
    for cat, fn in C1_PROVIDERS_B1B.items():
        r = fn()
        good, detail = _pv_ok(r)
        _ok(f"B2-B1b 提供方 {cat}：c1-provider/1，正例与反例都实际运行且全部符合（{detail.split(' 失败')[0]}）", good, detail)


def _c1_failed(r, category, broken) -> bool:
    """失败关闭：结构仍合格，error 写出，被破坏的那一极至少有一个用例失败；未破坏的那一极全部仍是自己的真实成功。"""
    kept = "neg" if broken == "pos" else "pos"
    return (not _c1_shape_problems(r, category) and isinstance(r["error"], str) and bool(r["error"])
            and any(c["polarity"] == broken and not c["ok"] for c in r["cases"])
            and all(c["ran"] and c["ok"] for c in r["cases"] if c["polarity"] == kept))


def _t_c1b_providers(_ok, base: Path):
    """B-C1-PROVIDERS：五个 B 提供方各调用一次，严格结构检查；新三类各有真实正反例且全部成功、error=None。"""
    seen = {}
    for cat, fn in C1_PROVIDERS.items():
        r = fn()
        seen[cat] = r
        probs = _c1_shape_problems(r, cat)
        pos = [c for c in r["cases"] if c["polarity"] == "pos"]
        neg = [c for c in r["cases"] if c["polarity"] == "neg"]
        _ok(f"B-C1-PROVIDERS 提供方 {cat}：c1-provider/1 严格结构、provider=B、category 准确、正例 {len(pos)} 反例 {len(neg)} 都实际运行成功、error=None",
            not probs and r["error"] is None and pos and neg and all(c["ran"] and c["ok"] for c in r["cases"]),
            json.dumps([probs, r], ensure_ascii=False)[:1800])
    names = [c["name"] for cat in C1_PROVIDERS_NEW for c in seen[cat]["cases"]]
    _ok("B-C1-PROVIDERS 三个新类别的用例互不复用：用例名在三类之间不重复，每类的结果来自各自的真实运行",
        len(names) == len(set(names)) and all(seen[c]["category"] == c for c in C1_PROVIDERS_NEW), str(len(names)))


def _t_c1b_mutations(_ok, base: Path):
    """B-C1-PROVIDERS 受控变异：业务层分别破坏正例与反例，提供方必须失败关闭；guard 不足、用例异常、整体异常都不绿。"""
    from . import admin, review, selftest_review
    from .core import WfError
    real_policy = admin.init_default_policy
    with _patched(admin, "init_default_policy", lambda: {**real_policy(), "auto_apply": True}):
        r1 = provider_init_files()
    with _patched(admin, "_refuse_linked_workflow", lambda wf: None):
        r2 = provider_init_files()
    _ok("B-C1-PROVIDERS 1-init-files 变异：默认白名单打开 auto_apply 只让正例失败；去掉链接拒绝只让反例失败；两次都不绿",
        _c1_failed(r1, "1-init-files", "pos") and _c1_failed(r2, "1-init-files", "neg"),
        json.dumps([r1["cases"], r2["cases"]], ensure_ascii=False)[:1800])
    real_fc = review.final_check
    with _patched(review, "final_check", lambda rid, **kw: {**real_fc(rid, **kw), "allowed": True, "reasons": []}):
        r3 = provider_unverified_install()
    with _patched(review, "consume", lambda *a, **k: (_ for _ in ()).throw(WfError("c1b mutation: consume broken"))):
        r4 = provider_unverified_install()
    _ok("B-C1-PROVIDERS 2S-unverified-install 变异：安装前复核一律放行时未核验安装真实写入、正例失败；消费损坏时合法安装失败、反例失败",
        _c1_failed(r3, "2S-unverified-install", "pos") and _c1_failed(r4, "2S-unverified-install", "neg"),
        json.dumps([r3["cases"], r4["cases"]], ensure_ascii=False)[:1800])
    real_lir, real_consume = review.load_install_request, review.consume
    with _patched(review, "load_install_request", lambda rid, **kw: real_lir(rid, **{**kw, "zip_path": None})):
        r5 = provider_install_review()
    with _patched(review, "consume", lambda *a, **k: {**real_consume(*a, **k), "attempt_id": "c1b-mutated-attempt"}):
        r6 = provider_install_review()
    _ok("B-C1-PROVIDERS 6a-install-review 变异：不再核对调用方包时换包正例失败；开始记录不再绑定登记的 attempt 时绑定反例失败",
        _c1_failed(r5, "6a-install-review", "pos") and _c1_failed(r6, "6a-install-review", "neg"),
        json.dumps([r5["cases"], r6["cases"]], ensure_ascii=False)[:1800])
    saved = os.environ.pop("WF_LOCAL_DIR")
    try:
        guarded = {cat: fn() for cat, fn in C1_PROVIDERS_NEW.items()}
    finally:
        os.environ["WF_LOCAL_DIR"] = saved
    with _patched(admin, "init_project", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("c1b injected"))):
        raised = provider_init_files()
    with _patched(selftest_review, "Env", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("c1b env broken"))):
        broken = provider_install_review()
    _ok("B-C1-PROVIDERS 失败关闭：隔离前提不足时三类都不运行用例并写 error；用例抛异常记 ran=False；夹具整体抛异常写 error；都不绿",
        all(not _c1_shape_problems(r, c) and r["cases"] == [] and "WF_LOCAL_DIR" in r["error"] for c, r in guarded.items())
        and not _c1_shape_problems(raised, "1-init-files") and raised["error"]
        and all(c["ran"] is False and c["ok"] is False for c in raised["cases"])
        and not _c1_shape_problems(broken, "6a-install-review") and broken["cases"] == [] and "c1b env broken" in broken["error"],
        json.dumps([guarded, raised["error"], broken["error"]], ensure_ascii=False)[:1500])


def _t_c1b_host_restore(_ok, base: Path):
    """F1：上下文内才加载的模块（这里用真实 wflib/admin.py 以新模块名加载），宿主外保留它的引用；正常退出、用例异常、
    嵌套三种情况离开后，经该引用看到的路径都回到调用方 LOCAL；全部 wflib 模块不留宿主路径；调用方 local 字节与 mode 不变；
    REGISTRY 不在 LOCAL 之下时拒绝且不改动任何状态。"""
    import importlib.util
    from . import admin, core
    orig = {"LOCAL": core.LOCAL, "REGISTRY": core.REGISTRY, "env": os.environ.get("WF_LOCAL_DIR")}
    local_before = _tree_snapshot(Path(core.LOCAL))
    modes_before = {str(p): stat.S_IMODE(os.lstat(p).st_mode) for p in Path(core.LOCAL).rglob("*")}
    probes = []

    def load(tag):
        name = f"wflib._c1b_probe_{tag}_{os.getpid()}"
        spec = importlib.util.spec_from_file_location(name, admin.__file__)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        probes.append(name)
        spec.loader.exec_module(mod)
        return mod
    res = {}
    try:
        with _c1_host(base / "n") as host:
            held_n = load("normal")
            res["normal_inside"] = _c1_under(Path(held_n.LOCAL), host) and _c1_under(Path(held_n.REGISTRY), host)
        res["normal_after"] = (held_n.LOCAL, held_n.REGISTRY) == (orig["LOCAL"], orig["REGISTRY"])
        try:
            with _c1_host(base / "e") as host:
                held_e = load("exception")
                raise RuntimeError("c1b injected case exception")
        except RuntimeError as e:
            res["exception_propagated"] = "injected" in str(e)
        res["exception_after"] = (held_e.LOCAL, held_e.REGISTRY) == (orig["LOCAL"], orig["REGISTRY"])
        with _c1_host(base / "o") as outer:
            with _c1_host(base / "i") as inner:
                held_x = load("nested")
                res["nested_inside"] = _c1_under(Path(held_x.LOCAL), inner)
            res["nested_back_to_outer"] = held_x.LOCAL == outer and held_x.REGISTRY == outer / "registry.json"
        res["nested_after"] = (held_x.LOCAL, held_x.REGISTRY) == (orig["LOCAL"], orig["REGISTRY"])
        with _patched(core, "REGISTRY", base / "elsewhere" / "registry.json"):
            try:
                with _c1_host(base / "r"):
                    res["registry_outside"] = "entered"
            except RuntimeError as e:
                res["registry_outside"] = "refused" if "REGISTRY" in str(e) and core.LOCAL == orig["LOCAL"] else str(e)
        alias = base / "caller-local-alias"
        alias.symlink_to(Path(core.LOCAL), target_is_directory=True)
        alias_host = alias / "resolved-overlap-host"
        try:
            with _c1_host(alias_host):
                res["resolved_alias"] = "entered"
        except RuntimeError as e:
            res["resolved_alias"] = "refused" if "解析路径" in str(e) and core.LOCAL == orig["LOCAL"] else str(e)
        res["resolved_alias_no_write"] = not (Path(core.LOCAL) / "resolved-overlap-host" / "host-local").exists()
        hosts = [str(base / x) for x in ("n", "e", "o", "i", "r")]
        res["no_host_path_left"] = not [f"{n}.{a}" for n, m in _c1_wflib_modules() for a, v in vars(m).items()
                                        if any(_c1_remap(v, Path(h) / "host-local", Path("/"))[1] for h in hosts)]
        res["globals_restored"] = (core.LOCAL, core.REGISTRY, os.environ.get("WF_LOCAL_DIR")) == \
            (orig["LOCAL"], orig["REGISTRY"], orig["env"])
        res["caller_local_unchanged"] = _tree_snapshot(Path(core.LOCAL)) == local_before and \
            {str(p): stat.S_IMODE(os.lstat(p).st_mode) for p in Path(core.LOCAL).rglob("*")} == modes_before
    finally:
        import wflib
        for name in probes:
            sys.modules.pop(name, None)
            if hasattr(wflib, name.split(".", 1)[1]):
                delattr(wflib, name.split(".", 1)[1])
    _ok("B-C1-PROVIDERS-REWORK F1 宿主恢复：上下文内新加载的模块经宿主外引用在正常退出、用例异常、嵌套后都回到调用方 LOCAL；"
        "无宿主路径残留，全局与调用方 local（字节、mode）不变；REGISTRY 不在 LOCAL 下及 symlink alias 解析交叠时拒绝",
        all(v is True for k, v in res.items() if k not in ("registry_outside", "resolved_alias"))
        and res.get("registry_outside") == "refused" and res.get("resolved_alias") == "refused",
        json.dumps(res, ensure_ascii=False, default=str))
    saved = core.REGISTRY
    with _patched(core, "REGISTRY", base / "elsewhere2" / "registry.json"):
        rr = provider_unverified_install()
    _ok("B-C1-PROVIDERS-REWORK F1 宿主初始化失败（REGISTRY 不在 LOCAL 下）：提供方失败关闭，没有用例、error 写明原因",
        rr["cases"] == [] and rr["error"] and "REGISTRY" in rr["error"] and core.REGISTRY == saved
        and not _c1_shape_problems(rr, "2S-unverified-install"), json.dumps(rr, ensure_ascii=False)[:600])


def _c1_inventory(top: Path) -> dict:
    """不跟随链接的完整清单：{相对路径: (类型, mode, 字节或链接目标)}，含目录本身。"""
    out = {}
    for dp, dns, fns in os.walk(top, followlinks=False):
        for n in sorted(dns + fns):
            p = Path(dp) / n
            st = os.lstat(p)
            body = p.read_bytes() if stat.S_ISREG(st.st_mode) else os.readlink(p) if stat.S_ISLNK(st.st_mode) else None
            out[str(p.relative_to(top))] = (stat.S_IFMT(st.st_mode), stat.S_IMODE(st.st_mode), body)
    return out


def _c1_attr_objects() -> dict:
    return {(n, a): v for n, m in _c1_wflib_modules() for a, v in list(vars(m).items())}


def _c1_object_drift(before: dict, after: dict) -> list:
    """逐个模块属性按对象同一性（is）比较；值相等但换了对象也算变化。"""
    return sorted([f"{n}.{a}" for (n, a), v in before.items() if (n, a) not in after or after[(n, a)] is not v]
                  + [f"{n}.{a}(新增)" for (n, a) in after if (n, a) not in before])


@contextlib.contextmanager
def _c1_mkdir_spy():
    calls, real = [], (Path.mkdir, os.mkdir, os.makedirs)

    def path_mkdir(self, *a, **k):
        calls.append(("Path.mkdir", str(self)))
        return real[0](self, *a, **k)

    def os_mkdir(p, *a, **k):
        calls.append(("os.mkdir", str(p)))
        return real[1](p, *a, **k)

    def os_makedirs(p, *a, **k):
        calls.append(("os.makedirs", str(p)))
        return real[2](p, *a, **k)
    with _patched(Path, "mkdir", path_mkdir), _patched(os, "mkdir", os_mkdir), _patched(os, "makedirs", os_makedirs):
        yield calls


def _t_c1b_host_refusal(_ok, base: Path):
    """B-C1-F4：_c1_host 的每个前置拒绝都发生在建目录与改任何状态之前——拒绝原因是原样的前置原因（不被退出扫描覆盖），
    Path.mkdir/os.mkdir/os.makedirs 调用 0 次，场景根目录（类型、mode、字节、symlink 目标）不变，全部已加载 wflib 模块的
    每个属性都是原来的同一个对象（含 core.LOCAL/REGISTRY），WF_LOCAL_DIR 不变。contains-old（td/host-local/inside）是
    F3-N1 原反例。另有嵌套内层拒绝与可进入的对照（证明 mkdir 侦测与对象同一性侦测不是空转）。"""
    from . import core
    base = base.resolve()

    def mk(p: Path, *files):
        p.mkdir(parents=True, exist_ok=True)
        for f in files:
            (p / f).write_bytes(b"CALLER-" + f.encode() + b"\n")
        return p

    def contains_old(r):
        td = r / "parent"
        local = mk(td / "host-local" / "inside", "registry.json", "marker")
        return local, local / "registry.json", td, "宿主 /"

    def equal(r):
        td = r / "parent"
        local = mk(td / "host-local", "registry.json")
        return local, local / "registry.json", td, "宿主 /"

    def host_in_local(r):
        local = mk(r / "local", "registry.json")
        return local, local / "registry.json", local / "sub", "宿主 /"

    def symlink_alias(r):
        local = mk(r / "local", "registry.json")
        (r / "alias").symlink_to(local, target_is_directory=True)
        return local, local / "registry.json", r / "alias" / "sub", "宿主解析路径"

    def symlink_dotdot(r):
        local = mk(r / "local", "registry.json")
        mk(local / "deep")
        (r / "lnk").symlink_to(local / "deep", target_is_directory=True)
        return local, local / "registry.json", r / "lnk" / "..", "宿主解析路径"

    def local_is_symlink(r):
        real = mk(r / "real", "registry.json")
        (r / "locallink").symlink_to(real, target_is_directory=True)
        return r / "locallink", r / "locallink" / "registry.json", real / "x", "宿主解析路径"

    def registry_outside(r):
        local = mk(r / "local")
        mk(r / "elsewhere", "registry.json")
        return local, r / "elsewhere" / "registry.json", r / "td", "REGISTRY "

    def registry_dotdot(r):
        local = mk(r / "local")
        mk(r / "elsewhere", "registry.json")
        return local, local / ".." / "elsewhere" / "registry.json", r / "td", "REGISTRY "

    def registry_symlink_into_host(r):
        local = mk(r / "local")
        (local / "registry.json").symlink_to(r / "td" / "host-local" / "registry.json")
        return local, local / "registry.json", r / "td", "宿主解析路径"

    scenarios = [("contains-old：调用方 LOCAL = td/host-local/inside（F3-N1 原反例）", contains_old),
                 ("equal：td/host-local 就是调用方 LOCAL", equal),
                 ("宿主在调用方 LOCAL 之内", host_in_local),
                 ("规范化别名：经指向 LOCAL 的 symlink 进入", symlink_alias),
                 ("symlink 之后的 ..：解析后宿主落在 LOCAL 内", symlink_dotdot),
                 ("调用方 LOCAL 本身是 symlink，宿主在其真实目录内", local_is_symlink),
                 ("REGISTRY 在 LOCAL 之外", registry_outside),
                 ("REGISTRY 词法在 LOCAL 下但含 ..", registry_dotdot),
                 ("REGISTRY 是指向宿主内的 symlink", registry_symlink_into_host)]
    for i, (label, build) in enumerate(scenarios):
        r = base / f"s{i}"
        r.mkdir()
        local, registry, td, want = build(r)
        with _patched(core, "LOCAL", local), _patched(core, "REGISTRY", registry):
            inv, objs, env0 = _c1_inventory(r), _c1_attr_objects(), os.environ.get("WF_LOCAL_DIR")
            exc = None
            with _c1_mkdir_spy() as calls:
                try:
                    with _c1_host(td):
                        exc = "entered"
                except Exception as e:   # noqa: BLE001
                    exc = f"{type(e).__name__}: {e}"
            drift = _c1_object_drift(objs, _c1_attr_objects())
            obs = {"exc": exc[:200], "mkdir": calls, "inventory_same": _c1_inventory(r) == inv, "object_drift": drift,
                   "LOCAL_after": str(core.LOCAL), "LOCAL_is_caller": core.LOCAL is local,
                   "REGISTRY_is_caller": core.REGISTRY is registry, "env_same": os.environ.get("WF_LOCAL_DIR") == env0}
        _ok(f"B-C1-F4 前置拒绝 {label}：原样前置原因、mkdir 0 次、场景目录不变、全部模块属性同一对象、环境不变",
            exc.startswith("RuntimeError: " + want) and "退出宿主后" not in exc and not calls and obs["inventory_same"]
            and not drift and obs["LOCAL_is_caller"] and obs["REGISTRY_is_caller"] and obs["env_same"],
            json.dumps(obs, ensure_ascii=False, default=str)[:900])

    r = base / "preref"
    local, td = mk(r / "local", "registry.json"), r / "td"
    name = f"wflib._c1b_preref_{os.getpid()}"
    pre = type(sys)(name)
    pre.HOSTREF, pre.HOSTSTR = td / "host-local" / "cache", str(td / "host-local")
    held = (pre.HOSTREF, pre.HOSTSTR)
    sys.modules[name] = pre
    try:
        with _patched(core, "LOCAL", local), _patched(core, "REGISTRY", local / "registry.json"):
            inv, objs = _c1_inventory(r), _c1_attr_objects()
            with _c1_mkdir_spy() as calls:
                try:
                    with _c1_host(td):
                        exc = "entered"
                except Exception as e:   # noqa: BLE001
                    exc = f"{type(e).__name__}: {e}"
            drift = _c1_object_drift(objs, _c1_attr_objects())
            same_inv = _c1_inventory(r) == inv
    finally:
        sys.modules.pop(name, None)
    _ok("B-C1-F4 前置拒绝：进入前已加载模块里已有指向宿主的 Path/字符串时拒绝，这些值保持原对象，不建目录",
        exc.startswith("RuntimeError: 进入前已有模块引用宿主") and not calls and same_inv and not drift
        and pre.HOSTREF is held[0] and pre.HOSTSTR is held[1],
        json.dumps({"exc": exc[:200], "mkdir": calls, "drift": drift, "inventory_same": same_inv}, ensure_ascii=False))

    r = base / "nested"
    local = mk(r / "local", "registry.json")
    nest = {}
    with _patched(core, "LOCAL", local), _patched(core, "REGISTRY", local / "registry.json"):
        caller_objs, env0 = _c1_attr_objects(), os.environ.get("WF_LOCAL_DIR")
        with _c1_host(r / "outer") as outer:
            inv, objs, env1 = _c1_inventory(r), _c1_attr_objects(), os.environ.get("WF_LOCAL_DIR")
            with _c1_mkdir_spy() as calls:
                try:
                    with _c1_host(r / "outer"):   # 内层宿主 outer/host-local 就是此刻的调用方 LOCAL（外层宿主）
                        nest["exc"] = "entered"
                except Exception as e:   # noqa: BLE001
                    nest["exc"] = f"{type(e).__name__}: {e}"
            nest.update(mkdir=calls, inside_drift=_c1_object_drift(objs, _c1_attr_objects()),
                        inventory_same=_c1_inventory(r) == inv, still_outer=core.LOCAL == outer,
                        env_inside_same=os.environ.get("WF_LOCAL_DIR") == env1 == str(outer))
        nest.update(after_outer_drift=_c1_object_drift(caller_objs, _c1_attr_objects()),
                    env_after=os.environ.get("WF_LOCAL_DIR") == env0, LOCAL_is_caller=core.LOCAL is local)
    _ok("B-C1-F4 嵌套：外层宿主内的内层前置拒绝（equal）不改外层状态、不建目录；外层退出后调用方全部属性恢复为同一对象",
        nest["exc"].startswith("RuntimeError: 宿主 /") and "退出宿主后" not in nest["exc"] and not nest["mkdir"]
        and not nest["inside_drift"] and nest["inventory_same"] and nest["still_outer"] and nest["env_inside_same"]
        and not nest["after_outer_drift"] and nest["env_after"] and nest["LOCAL_is_caller"],
        json.dumps(nest, ensure_ascii=False, default=str)[:900])

    r = base / "control"
    local = mk(r / "local", "registry.json", "marker")
    ctl = {}
    with _patched(core, "LOCAL", local), _patched(core, "REGISTRY", local / "registry.json"):
        objs, env0, local_inv = _c1_attr_objects(), os.environ.get("WF_LOCAL_DIR"), _c1_inventory(local)
        with _c1_mkdir_spy() as calls:
            with _c1_host(r / "td") as host:
                ctl["inside"] = (core.LOCAL == host and core.REGISTRY == host / "registry.json"
                                 and os.environ.get("WF_LOCAL_DIR") == str(host))
                ctl["drift_detected_inside"] = _c1_object_drift(objs, _c1_attr_objects())
        ctl.update(mkdir=len(calls), after_drift=_c1_object_drift(objs, _c1_attr_objects()),
                   env_after=os.environ.get("WF_LOCAL_DIR") == env0, local_same=_c1_inventory(local) == local_inv,
                   LOCAL_is_caller=core.LOCAL is local)
    _ok("B-C1-F4 对照：不交叠的宿主照常进入（mkdir 被侦测到、宿主内对象变化被侦测到）且退出后同一对象恢复、调用方 local 不变",
        ctl["inside"] and ctl["mkdir"] > 0 and "wflib.core.LOCAL" in ctl["drift_detected_inside"]
        and not ctl["after_drift"] and ctl["env_after"] and ctl["local_same"] and ctl["LOCAL_is_caller"],
        json.dumps(ctl, ensure_ascii=False, default=str)[:900])


def _t_c1b_frozen_identity(_ok, base: Path):
    """F2：在真实 review.final_check 的允许决定里改冻结身份（审查者会话、核验器 sha、核验模型）——业务层变异，
    不碰检查函数。真实 consume 与 apply_zip 照常安装，两份持久记录都带改后的身份；6a 提供方必须失败关闭，只有绑定反例失败。"""
    from . import review
    real = review.final_check
    res = {}
    for label, change in (("reviewer_identity", lambda p: {**p, "reviewer_identity": {"platform": "synthetic",
                                                                                       "session": "c1b-swapped"}}),
                          ("verifier_sha256", lambda p: {**p, "verifier_sha256": "0" * 64}),
                          ("model_verified", lambda p: {**p, "model_verified": "c1b-swapped-model"})):
        def mutated(rid, _change=change, **kw):
            d = real(rid, **kw)
            return {**d, "provenance": _change(d["provenance"])} if d.get("allowed") else d
        with _patched(review, "final_check", mutated):
            r = provider_install_review()
        bound = next(c for c in r["cases"] if c["polarity"] == "neg")
        res[label] = {"fail_closed": _c1_failed(r, "6a-install-review", "neg"),
                      "installed_anyway": bound["detail"].startswith("{") and label in bound["detail"],
                      "detail": bound["detail"][:160]}
    _ok("B-C1-PROVIDERS-REWORK F2 冻结身份漂移（审查者、核验器 sha、核验模型）：真实安装照常完成，6a 提供方以冻结决定为独立来源逐项核对，"
        "三种漂移都失败关闭且只有绑定反例失败",
        all(v["fail_closed"] and v["installed_anyway"] for v in res.values()), json.dumps(res, ensure_ascii=False))


def _t_b1b_mutations(_ok, base: Path):
    """变异对照：把判定弄坏，提供方必须报失败（证明用例不是空转）。"""
    from . import policy
    with _patched(policy, "strong_gates", lambda *a, **k: []):
        r = provider_destructive()
    failed_pos = [c for c in r["cases"] if c["polarity"] == "pos" and not c["ok"]]
    _ok("B2-B1b 变异对照：去掉强门禁判定后，破坏性或跨项目的正例成批失败", len(failed_pos) >= len(POS_B1B), str(len(failed_pos)))
    with _patched(policy, "MAY_SHRINK", ("allow", "pipe_filters")):
        r = provider_widening()
    bad = [c["name"] for c in r["cases"] if not c["ok"]]
    _ok("B2-B1b 变异对照：clean_dirs 不按“只能收紧”处理时，删 clean_dirs 被误判为扩大", any("删 clean_dirs" in x for x in bad),
        str(bad))
    with _patched(policy, "_strip_comments", lambda s: s):
        hit_with_comment = bool(policy.strong_gates("rm -rf build/ # 清理", {"clean_dirs": ["build"]},
                                                    root=_b1b_env(base)["root"], others=[]))
    _ok("B2-B1b 变异对照：不去掉注释时，带注释的合法清理会被误拦（所以按 bash 的注释规则去掉注释）", hit_with_comment)
    e = _b1b_env(base / "m")
    real = bool(policy.strong_gates("rm -rf linkout/", {"clean_dirs": ["linkout", "build"]}, root=e["root"], others=[]))
    with _patched(os.path, "realpath", os.path.abspath):
        fooled = bool(policy.strong_gates("rm -rf linkout/", {"clean_dirs": ["linkout", "build"]}, root=e["root"], others=[]))
    _ok("B2-B1b 变异对照：不解析符号链接时，指向根外的 clean_dirs 会被放过；解析时命中", real and not fooled, f"{real} {fooled}")
    import glob as _glob
    e2 = _b1b_env(base / "m2")
    p2 = {"clean_dirs": ["build"]}
    real = bool(policy.strong_gates("rm -rf build/*/victim", p2, root=e2["root"], others=[]))
    with _patched(_glob, "glob", lambda *a, **k: []):
        fooled = bool(policy.strong_gates("rm -rf build/*/victim", p2, root=e2["root"], others=[]))
    _ok("B2-B1b r2 变异对照：只核通配前缀、不核实际匹配时，经 clean 目录里的链接出根的删除被放过；核实际匹配时命中",
        real and not fooled, f"{real} {fooled}")
    real = bool(policy.strong_gates("tar -xC ../outside -f archive.tar", p2, root=e2["root"], others=[]))
    with _patched(policy, "_SPEC", {**policy._SPEC, "tar": ("", set(), set(), set())}):
        fooled = bool(policy.strong_gates("tar -xC ../outside -f archive.tar", p2, root=e2["root"], others=[]))
    _ok("B2-B1b r2 变异对照：不按参数语义解析连写短参数时，tar -xC 的根外目录被放过；按语义解析时命中", real and not fooled,
        f"{real} {fooled}")
    real = bool(policy.strong_gates("! rm -rf docs", p2, root=e2["root"], others=[]))
    with _patched(policy, "_RESERVED", set()):
        fooled = bool(policy.strong_gates("! rm -rf docs", p2, root=e2["root"], others=[]))
    _ok("B2-B1b r3 变异对照：不剥掉命令前的保留字时，! rm -rf 被放过；剥掉时命中", real and not fooled, f"{real} {fooled}")
    real = bool(policy.strong_gates("env -C../outside touch victim", p2, root=e2["root"], others=[]))
    with _patched(policy, "_ENV_ARG", ""):
        fooled = bool(policy.strong_gates("env -C../outside touch victim", p2, root=e2["root"], others=[]))
    _ok("B2-B1b r3 变异对照：env 的 -C 不按带参短选项解析时，连写的根外目录被放过；按语义解析时命中", real and not fooled,
        f"{real} {fooled}")
    n3 = base / "m3"
    (n3 / "build" / "nested" / "workflow").mkdir(parents=True)
    on3 = [{"name": "nested", "root": str(n3 / "build" / "nested")}]
    real = bool(policy.strong_gates("rm -rf build", p2, root=n3, others=on3))
    with _patched(policy, "_contained", lambda rp, c: None):
        fooled = bool(policy.strong_gates("rm -rf build", p2, root=n3, others=on3))
    _ok("B2-B1b r3 变异对照：不查递归范围之内的受保护目录时，删除包含另一个项目的 clean 目录被放过；查时命中",
        real and not fooled, f"{real} {fooled}")


def _t_b1b_guard(_ok, base: Path):
    """B-C1-OLD2-GUARD：原两类公开提供方（provider_destructive、provider_widening）在隔离前提不足时——没有 WF_LOCAL_DIR，
    或 WF_LOCAL_DIR/LOCAL 指向工具自己的 local/——在业务导入、临时目录与任何夹具之前以标准 c1-provider/1 拒绝：
    cases=[]、error 写明原因；不建临时目录、不 mkdir、不导入 wflib 业务模块、不调用夹具，LOCAL、工具目录与临时目录不变。
    “工具自己的 local/”用 base 下的假工具目录模拟（core.WF_HOME/LOCAL 指向它），守卫失效时也写不到真实工具目录。
    对照：守卫关掉时同一组侦测器能看到临时目录、业务导入与夹具调用（侦测不是空转）。配置了私有 LOCAL 时原两类仍 198/13 全绿。"""
    import builtins
    from . import core
    me = sys.modules[__name__]
    fake_home = base.resolve() / "fake-tool"
    (fake_home / "local").mkdir(parents=True)
    (fake_home / "local" / "marker").write_bytes(b"TOOL-LOCAL\n")
    old2 = [("2S-destructive-or-crossproject", provider_destructive, 198), ("2S-widening", provider_widening, 13)]

    def observe(fn, wf_local, local, home, stop_fixtures=False):
        imports, temps, fixtures = [], [], []
        real_import, real_mkdtemp = builtins.__import__, tempfile.mkdtemp
        real_env, real_proj = me._b1b_env, me._mk_project

        def spy_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level > 0 or name.startswith("wflib"):
                imports.extend([name] if name else list(fromlist or ()))
            return real_import(name, globals, locals, fromlist, level)

        def spy_mkdtemp(*a, **k):
            temps.append(1)
            return real_mkdtemp(*a, **k)

        def fixture(real, tag):
            def f(*a, **k):
                fixtures.append(tag)
                if stop_fixtures:
                    raise RuntimeError("c1b old2 control stop")
                return real(*a, **k)
            return f
        saved_env = os.environ.get("WF_LOCAL_DIR")
        tmp_top = sorted(os.listdir(tempfile.gettempdir()))
        inv = (_c1_inventory(Path(local)), _c1_inventory(fake_home))
        try:
            if wf_local is None:
                os.environ.pop("WF_LOCAL_DIR", None)
            else:
                os.environ["WF_LOCAL_DIR"] = wf_local
            with _patched(core, "LOCAL", local), _patched(core, "WF_HOME", home), \
                    _patched(me, "_b1b_env", fixture(real_env, "_b1b_env")), \
                    _patched(me, "_mk_project", fixture(real_proj, "_mk_project")), \
                    _patched(tempfile, "mkdtemp", spy_mkdtemp), _c1_mkdir_spy() as mk, \
                    _patched(builtins, "__import__", spy_import):
                r = fn()
            mkdirs = list(mk)
        finally:
            if saved_env is None:
                os.environ.pop("WF_LOCAL_DIR", None)
            else:
                os.environ["WF_LOCAL_DIR"] = saved_env
        business = sorted({n for n in imports if n not in ("core", "wflib.core")})
        same = (_c1_inventory(Path(local)), _c1_inventory(fake_home)) == inv \
            and sorted(os.listdir(tempfile.gettempdir())) == tmp_top
        return r, {"error": r.get("error"), "n_cases": len(r.get("cases") or []), "temps": len(temps), "mkdir": mkdirs,
                   "business_imports": business, "fixtures": fixtures, "inventory_same": same,
                   "env_restored": os.environ.get("WF_LOCAL_DIR") == saved_env}

    envs = [("没有 WF_LOCAL_DIR", None, core.LOCAL, core.WF_HOME, "没有设置 WF_LOCAL_DIR"),
            ("WF_LOCAL_DIR 指向工具自己的 local/", str(fake_home / "local"), fake_home / "local", fake_home,
             "指向工具自己的 local/")]
    for label, wf_local, local, home, want in envs:
        for cat, fn, _n in old2:
            r, obs = observe(fn, wf_local, local, home)
            _ok(f"B-C1-OLD2-GUARD {cat}：{label}时以标准 c1-provider/1 拒绝（cases=[]、error 写明原因），"
                "业务导入、临时目录、mkdir、夹具都没有发生，LOCAL 与工具目录不变",
                not _c1_shape_problems(r, cat) and r["cases"] == [] and isinstance(r["error"], str) and want in r["error"]
                and not obs["temps"] and not obs["mkdir"] and not obs["business_imports"] and not obs["fixtures"]
                and obs["inventory_same"] and obs["env_restored"], json.dumps(obs, ensure_ascii=False, default=str)[:900])
    ctl = {}
    with _patched(me, "_c1_guard", lambda: ""):
        for cat, fn, _n in old2:
            r, obs = observe(fn, None, core.LOCAL, core.WF_HOME, stop_fixtures=True)
            ctl[cat] = {**obs, "mkdir": len(obs["mkdir"]), "error": str(r["error"])[:120]}
    _ok("B-C1-OLD2-GUARD 对照：守卫关掉时同一组侦测器看到临时目录、wflib 业务导入与夹具调用（夹具在写入前被截停），证明侦测不是空转",
        all(v["temps"] >= 1 and "policy" in v["business_imports"] and v["fixtures"] and v["error"] and v["env_restored"]
            for v in ctl.values()), json.dumps(ctl, ensure_ascii=False, default=str)[:900])
    full = {cat: fn() for cat, fn, _n in old2}
    _ok("B-C1-OLD2-GUARD 配置了私有 LOCAL：原两类照常运行，2S-destructive-or-crossproject 198 例、2S-widening 13 例，"
        "全部实际运行成功、error=None、结构合格",
        all(not _c1_shape_problems(full[cat], cat) and full[cat]["error"] is None and len(full[cat]["cases"]) == n
            and all(c["ran"] and c["ok"] for c in full[cat]["cases"]) for cat, _fn, n in old2),
        json.dumps({cat: [len(r["cases"]), r["error"]] for cat, r in full.items()}, ensure_ascii=False)[:600])


TESTS = [_t_clean, _t_dirty5, _t_freeze_once, _t_head_moved, _t_nogit, _t_failures, _t_compat, _t_short,
         _t_r1_attributes, _t_r1_verify, _t_r2_walk_errors, _t_r3_symlinks_git, _t_r3_symlinks_nogit,
         _t_r4_publish, _t_r5_short, _t_r6_restore_retry, _t_r7_parent_swap, _t_r7_capability,
         _t_b1a_init_new, _t_b1a_init_existing, _t_b1a_init_refuse, _t_b1a_init_safe, _t_b1a_workflow_link,
         _t_b1b_providers, _t_b1b_mutations, _t_c1b_providers, _t_c1b_mutations, _t_c1b_host_restore,
         _t_c1b_frozen_identity, _t_c1b_host_refusal, _t_b1b_guard]


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
