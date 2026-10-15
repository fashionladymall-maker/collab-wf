"""B2a：wflib/review.py 的单模块测试（独立模块，聚合器由 C 接入）。

run_review(_ok) 供聚合器调用（它本身在隔离子进程里运行）；单独运行 `python3 -m wflib.selftest_review` 时，经
selftest.child_env 起隔离子进程（临时 local、WF_NOTIFY=0），父进程环境一项都不改。所有项目、工具目录、审查程序与
核验器都是临时替身；核验器标 synthetic，任何 allowed 只证明本模块的判定逻辑，不是实景审查通过。不调用真实平台。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import warnings
import zipfile
from datetime import timedelta
from pathlib import Path

from .core import WF_HOME

GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
AUTHOR_SESSION = "author-sess-1"
REVIEWER = {"platform": "synthetic", "session": "rev-sess-1"}

RUNNER = r'''#!{py}
import json, os, sys, time
a = sys.argv[1:]
def arg(n):
    return a[a.index(n) + 1]
here = os.path.dirname(os.path.abspath(__file__))
b = json.load(open(os.path.join(here, "behavior.json")))
if b.get("sleep"):
    time.sleep(b["sleep"])
if b.get("garbage"):
    print("不是 JSON")
    sys.exit(0)
req = json.load(open(arg("--request")))
out = {{"request_id": req["request_id"], "content_id": req["content_id"], "nonce": arg("--nonce"),
       "attempt_id": arg("--attempt"), "verdict": b.get("verdict", "pass"), "notes": ["synthetic"],
       "model": b.get("model", "synthetic-claimed")}}
out.update(b.get("extra", {{}}))
if b.get("bad_nonce"):
    out["nonce"] = "0" * 32
print("审查日志（替身）")
print(json.dumps(out, ensure_ascii=False))
if b.get("exit"):
    sys.exit(b["exit"])
'''

VERIFIER = r'''#!{py}
import hashlib, json, os, sys
a = sys.argv[1:]
def arg(n):
    return a[a.index(n) + 1]
here = os.path.dirname(os.path.abspath(__file__))
b = json.load(open(os.path.join(here, "vbehavior.json")))
req = json.load(open(arg("--request")))
raw = open(arg("--raw"), "rb").read()
res = open(arg("--result"), "rb").read()
try:
    r = json.loads(res.decode("utf-8")) if res else {{}}
except ValueError:
    r = {{}}
out = {{"request_id": req["request_id"], "content_id": req["content_id"], "nonce": r.get("nonce"),
       "attempt_id": r.get("attempt_id"), "raw_sha256": hashlib.sha256(raw).hexdigest(),
       "result_sha256": hashlib.sha256(res).hexdigest(), "level": b.get("level", "platform_verified"),
       "ok": b.get("ok", True), "synthetic": True, "model_verified": b.get("model_verified", "synthetic-model"),
       "reviewer_identity": b.get("identity", {identity})}}
if b.get("bad_raw"):
    out["raw_sha256"] = "0" * 64
print(json.dumps(out, ensure_ascii=False))
'''


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _git(cwd, *args) -> str:
    r = subprocess.run(["git"] + GIT_ID + list(args), cwd=str(cwd), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}：{r.stderr.strip()}")
    return r.stdout.strip()


@contextlib.contextmanager
def _patched(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


def _zip(path: Path, top: str, files: dict, raw_infos=()) -> Path:
    """files：{相对路径: 字节 或 (字节, 权限)}；raw_infos：[(ZipInfo, 字节)] 原样写入（用于构造不合格的包）。"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel, v in files.items():
                data, mode = (v, 0o644) if isinstance(v, bytes) else v
                info = zipfile.ZipInfo(f"{top}/{rel}", (2026, 1, 1, 0, 0, 0))
                info.external_attr = (stat.S_IFREG | mode) << 16
                zf.writestr(info, data)
            for info, data in raw_infos:
                zf.writestr(info, data)
    return path


class Env:
    """一组临时替身：工具目录（含 docs/REVIEW.md）、项目、审查程序、核验器、transport.json 与 lanes.json。"""

    def __init__(self, base: Path):
        from . import core
        self.base = base
        self.tool = base / "tool"
        (self.tool / "docs").mkdir(parents=True)
        (self.tool / "VERSION").write_text("9.9.9\n", encoding="utf-8")
        (self.tool / "docs" / "REVIEW.md").write_text("审查规则 v1\n", encoding="utf-8")
        _git(self.tool, "init", "-q")
        _git(self.tool, "add", "-A")
        _git(self.tool, "commit", "-qm", "tool")
        self.bin = base / "bin"
        self.bin.mkdir()
        self.runner = self.bin / "runner.py"
        self.verifier = self.bin / "verifier.py"
        self.runner.write_text(RUNNER.format(py=sys.executable), encoding="utf-8")
        self.verifier.write_text(VERIFIER.format(py=sys.executable, identity=json.dumps(REVIEWER)), encoding="utf-8")
        os.chmod(self.runner, 0o755)
        os.chmod(self.verifier, 0o755)
        self.behave()
        self.vbehave()
        self.local = Path(core.LOCAL)
        self.transport = self.local / "transport.json"
        self.lanes = self.local / "lanes.json"
        self.set_transport()
        self.set_lanes({"B": {"sessions": [AUTHOR_SESSION]}})

    def behave(self, **b):
        (self.bin / "behavior.json").write_text(json.dumps(b), encoding="utf-8")

    def vbehave(self, **b):
        (self.bin / "vbehavior.json").write_text(json.dumps(b), encoding="utf-8")

    def set_transport(self, verifier=True, pinned=None, **extra):
        cfg = {"review": str(self.runner), **extra}
        if verifier:
            cfg["review_verifier"] = {"path": str(self.verifier), "synthetic": True,
                                      "sha256": pinned or _sha(self.verifier.read_bytes())}
        self.local.mkdir(parents=True, exist_ok=True)
        self.transport.write_text(json.dumps(cfg), encoding="utf-8")

    def set_lanes(self, lanes):
        self.lanes.write_text(json.dumps({"lanes": lanes}), encoding="utf-8")

    def project(self, name: str, protect=()):
        root = self.base / name
        (root / "workflow").mkdir(parents=True)
        (root / "workflow" / "wf.json").write_text(json.dumps({"project": name, "created": "2026-10-15T00:00:00+00:00",
                                                              "protect": list(protect)}), encoding="utf-8")
        (root / "PROJECT.md").write_text("# PROJECT.md\n", encoding="utf-8")
        (root / "app.py").write_text("print('v1')\n", encoding="utf-8")
        (root / ".gitignore").write_text("workflow/state.json\nworkflow/*.jsonl\n", encoding="utf-8")
        _git(root, "init", "-q")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "v1")
        return root

    def pkg(self, name: str, files=None, **kw) -> Path:
        files = files if files is not None else {"PROJECT.md": b"# PROJECT.md\n", "app.py": b"print('v2')\n",
                                                 "NAME.txt": name.encode("utf-8")}
        return _zip(self.base / f"{name}.zip", "p", files, **kw)

    def cleanup(self):
        for p in (self.transport, self.lanes):
            try:
                p.unlink()
            except OSError:
                pass


def _requests_count() -> int:
    from . import review
    d = review._reviews() / "requests"
    return len([p for p in d.glob("rq-*.json")]) if d.exists() else 0


# ---------------------------------------------------------------------------

def _t_package_rules(_ok, env: Env):
    from . import review
    from .core import WfError
    root = env.project("pr")

    def info(name, attr=stat.S_IFREG | 0o644):
        i = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
        i.external_attr = attr << 16
        return i
    bad = {
        "重复路径": dict(files={"a.txt": b"1"}, raw_infos=[(info("p/a.txt"), b"2")]),
        "路径穿越": dict(files={"a.txt": b"1"}, raw_infos=[(info("p/../escape.txt"), b"x")]),
        "绝对路径": dict(files={"a.txt": b"1"}, raw_infos=[(info("/etc/x"), b"x")]),
        "反斜杠": dict(files={"a.txt": b"1"}, raw_infos=[(info("p\\b.txt"), b"x")]),
        "符号链接": dict(files={"a.txt": b"1"}, raw_infos=[(info("p/l", stat.S_IFLNK | 0o777), b"/etc/passwd")]),
        "大小写冲突": dict(files={"A.txt": b"1", "a.txt": b"2"}),
        "文件与目录冲突": dict(files={"d": b"1", "d/x": b"2"}),
        "两个顶层": dict(files={"a.txt": b"1"}, raw_infos=[(info("q/b.txt"), b"x")]),
        "顶层是文件": dict(files={}, raw_infos=[(info("solo.txt"), b"x")]),
        "setuid": dict(files={"a.txt": (b"1", 0o4755)}),
        "RELEASE.json 损坏": dict(files={"a.txt": b"1", "RELEASE.json": b"{not json"}),
        "delete 不是列表": dict(files={"a.txt": b"1", "RELEASE.json": b'{"delete": "x"}'}),
        "delete 越界": dict(files={"a.txt": b"1", "RELEASE.json": b'{"delete": ["../x"]}'}),
    }
    res = {}
    for i, (label, kw) in enumerate(bad.items()):
        z = env.pkg(f"bad{i}", **kw)
        n0 = _requests_count()
        try:
            review.prepare(root, z, kind="project", author="B")
            res[label] = "接受了"
        except WfError:
            res[label] = "ok" if _requests_count() == n0 else "拒绝但留下了请求"
    good = env.pkg("good", {"a.txt": b"1", "bin/x.sh": (b"#!/bin/sh\n", 0o755)},
                   raw_infos=[(info("__MACOSX/p/._a.txt"), b"junk")])
    pkg = review.read_package(good, kind="project", protect=[])
    _ok("B2a 包解析：重复、穿越、绝对、反斜杠、符号链接、大小写冲突、文件/目录冲突、多个顶层、顶层文件、setuid、"
        "RELEASE.json 损坏或 delete 不合格都拒绝且不留请求；合格包记权限、忽略 __MACOSX",
        all(v == "ok" for v in res.values()) and pkg["ignored"] == ["__MACOSX/p/._a.txt"]
        and {e["path"]: e.get("mode") for e in pkg["entries"] if e["type"] == "file"} == {"a.txt": 0o644, "bin/x.sh": 0o755},
        json.dumps(res, ensure_ascii=False))


def _t_no_host_request(_ok, env: Env):
    from . import review
    root = env.project("nh")
    z = env.pkg("nh", {"PROJECT.md": b"# P\n", "review.json":
                       json.dumps({"verdict": "pass", "provenance": {"verified": True, "level": "platform_verified"}}).encode()})
    req = review.prepare(root, z, kind="project", author="B")
    d1 = review.status(root, req["request_id"])
    d2 = review.status(None, content_id=req["content_id"])
    _ok("B2a 包内自带 review.json（pass、provenance.verified=true）没有宿主审查请求的运行：不允许，状态 none",
        not d1["allowed"] and d1["state"] == "none" and "no_review" in d1["reasons"] and not d2["allowed"]
        and d1["verdict"] is None, json.dumps(d1, ensure_ascii=False)[:400])


def _t_synthetic_allowed(_ok, env: Env):
    from . import review
    root = env.project("ok")
    req = review.prepare(root, env.pkg("ok"), kind="project", author="B")
    d = review.run(req["request_id"])
    t = d["times"]
    _ok("B2a 合成正例（synthetic）：宿主请求 → 审查 pass → 固定 sha 的核验器 platform_verified → 身份不与作者重叠 → allowed；"
        "决定里如实标 synthetic，区分 model_claimed 与 model_verified，记下三个审查时刻",
        d["allowed"] and d["state"] == "completed" and d["verdict"] == "pass" and d["provenance"]["synthetic"] is True
        and d["provenance"]["level"] == "platform_verified" and d["provenance"]["model_claimed"] == "synthetic-claimed"
        and d["provenance"]["model_verified"] == "synthetic-model" and d["provenance"]["reviewer_identity"] == REVIEWER
        and all(t.get(k) for k in ("review_started", "review_completed", "result_registered")) and t["install_started"] is None,
        json.dumps(d, ensure_ascii=False)[:500])
    with tempfile.TemporaryDirectory() as td:
        ex = review.extract_snapshot(req["request_id"], td)
        f1 = review.final_check(req["request_id"], target=root, extracted_dir=ex)
        c = review.consume(req["request_id"], target=root, extracted_dir=ex)
        (ex / "app.py").write_text("被改了\n", encoding="utf-8")
        f2 = review.final_check(req["request_id"], target=root, extracted_dir=ex)
    recs = list((review._reviews() / "installs" / req["content_id"]).glob("*.json"))
    _ok("B2a 安装锁内复核：final_check 核快照、目标与解包目录；consume 记下第四个时刻 install_started；"
        "测试后解包目录被改，再次复核即拒绝",
        f1["allowed"] and c["allowed"] and c["times"]["install_started"] and len(recs) == 1
        and not f2["allowed"] and "extracted_changed:app.py" in f2["reasons"], json.dumps(f2["reasons"], ensure_ascii=False))


def _t_self_claims(_ok, env: Env):
    from . import review
    secret = env.base / "secret.txt"
    secret.write_bytes(b"SECRET-MARKER-91c2\n")
    env.behave(model="claude-opus-9-ultra", extra={"provenance": {"verified": True, "level": "platform_verified"},
                                                   "raw_path": str(secret), "verified": True})
    env.vbehave(level="local_invocation")
    root = env.project("sc")
    req = review.prepare(root, env.pkg("sc"), kind="project", author="B")
    d = review.run(req["request_id"])
    adir = review._attempt_dir(d["attempt_id"])
    leaked = any(b"SECRET-MARKER-91c2" in p.read_bytes() for p in adir.iterdir() if p.is_file()
                 and p.name not in ("result.json", "stdout.bin", "attempt.json"))
    _ok("B2a 自报伪装：结果自称 provenance.verified、给 raw_path、自报任意模型；核验器只给 local_invocation → 不允许；"
        "自报字段只记诊断、不读 raw_path 指向的文件；model_verified 为空",
        not d["allowed"] and "provenance_local_invocation" in d["reasons"] and d["provenance"]["model_verified"] is None
        and d["provenance"]["model_claimed"] == "claude-opus-9-ultra" and any("被忽略" in x for x in d["diagnostics"])
        and not leaked, json.dumps([d["reasons"], d["diagnostics"]], ensure_ascii=False)[:500])
    env.behave()
    env.vbehave()


def _t_verifier_problems(_ok, env: Env):
    from . import review
    res = {}
    cases = [("缺核验器", dict(verifier=False), {}, {}, "verifier_unavailable"),
             ("sha 不符", dict(pinned="0" * 64), {}, {}, "verifier_sha256_mismatch"),
             ("原文 hash 错", {}, {}, {"bad_raw": True}, "verify_echo_mismatch"),
             ("nonce 错", {}, {"bad_nonce": True}, {}, "result_echo_mismatch"),
             ("宿主背书", {}, {}, {"level": "host_attested"}, "provenance_host_attested"),
             ("核验不通过", {}, {}, {"ok": False}, "verify_not_ok"),
             ("结果不是 JSON", {}, {"garbage": True}, {}, "result_invalid")]
    for i, (label, tcfg, beh, vbeh, want) in enumerate(cases):
        env.set_transport(**tcfg)
        env.behave(**beh)
        env.vbehave(**vbeh)
        root = env.project(f"vp{i}")
        req = review.prepare(root, env.pkg(f"vp{i}"), kind="project", author="B")
        d = review.run(req["request_id"])
        res[label] = "ok" if (not d["allowed"] and any(r.startswith(want) for r in d["reasons"])) else d["reasons"]
    env.set_transport()
    env.behave()
    env.vbehave()
    _ok("B2a 来源核验：缺核验器、核验器 sha 与宿主固定值不符、原文 hash 错、nonce 错、只有宿主背书、核验不通过、"
        "结果不是 JSON，都不允许",
        all(v == "ok" for v in res.values()), json.dumps(res, ensure_ascii=False))


def _t_identity(_ok, env: Env):
    from . import review
    res = {}
    cases = [("审查者就是作者", "B", {"identity": {"platform": "synthetic", "session": AUTHOR_SESSION}}, "reviewer_is_author"),
             ("没有作者", None, {}, "author_identity_missing"),
             ("作者未登记", "Z", {}, "author_identity_missing"),
             ("审查者身份缺会话", "B", {"identity": {"platform": "synthetic"}}, "reviewer_identity_missing")]
    for i, (label, author, vbeh, want) in enumerate(cases):
        env.vbehave(**vbeh)
        root = env.project(f"id{i}")
        req = review.prepare(root, env.pkg(f"id{i}"), kind="project", author=author)
        d = review.run(req["request_id"])
        res[label] = "ok" if (not d["allowed"] and want in d["reasons"]) else d["reasons"]
    env.vbehave()
    _ok("B2a 身份隔离：审查者会话与作者登记会话相同、缺作者身份、作者未登记、审查者缺会话，都不允许",
        all(v == "ok" for v in res.values()), json.dumps(res, ensure_ascii=False))


def _t_material(_ok, env: Env):
    from . import review
    root = env.project("mt")
    other = env.project("mt2")
    req = review.prepare(root, env.pkg("mt"), kind="project", author="B")
    ok0 = review.run(req["request_id"])["allowed"]
    wrong_root = review.status(other, req["request_id"], phase="install")
    (root / "app.py").write_text("print('v1.1')\n", encoding="utf-8")
    _git(root, "commit", "-qam", "base moved")
    moved = review.status(root, req["request_id"], phase="install")
    root2 = env.project("mt3")
    req2 = review.prepare(root2, env.pkg("mt3"), kind="project", author="B")
    review.run(req2["request_id"])
    (root2 / "workflow" / "wf.json").write_text(json.dumps({"project": "mt3", "created": "2026-10-15T00:00:00+00:00",
                                                           "protect": ["data/"]}), encoding="utf-8")
    cfg = review.status(root2, req2["request_id"], phase="install")
    root3 = env.project("mt4")
    req3 = review.prepare(root3, env.pkg("mt4"), kind="project", author="B")
    review.run(req3["request_id"])
    (env.tool / "docs" / "REVIEW.md").write_text("审查规则 v2\n", encoding="utf-8")
    rules = review.status(root3, req3["request_id"], phase="install")
    (env.tool / "docs" / "REVIEW.md").write_text("审查规则 v1\n", encoding="utf-8")
    _ok("B2a 材料绑定：审查通过后项目 base 前进、wf.json 配置改动、已安装工具 REVIEW.md 改动、换到另一个项目，都使结论失效",
        ok0 and not moved["allowed"] and "material_changed" in moved["reasons"] and not cfg["allowed"]
        and "material_changed" in cfg["reasons"] and not rules["allowed"] and "material_changed" in rules["reasons"]
        and not wrong_root["allowed"] and "target_mismatch" in wrong_root["reasons"],
        json.dumps([moved["reasons"], cfg["reasons"], rules["reasons"], wrong_root["reasons"]], ensure_ascii=False))
    base_files = {"PROJECT.md": b"# P\n", "img.bin": bytes(range(256)), "run.sh": (b"#!/bin/sh\n", 0o755),
                  "RELEASE.json": b'{"delete": ["old.txt"]}'}
    variants = {
        "原样（另一份 zip 字节）": base_files,
        "delete 不同": {**base_files, "RELEASE.json": b'{"delete": ["other.txt"]}'},
        "权限不同": {**base_files, "run.sh": (b"#!/bin/sh\n", 0o644)},
        "二进制不同": {**base_files, "img.bin": bytes(range(255)) + b"\x00"},
        "wf_config 不同": {**base_files, "RELEASE.json": b'{"delete": ["old.txt"], "wf_config": {"hook": "x.py"}}'},
    }
    root4 = env.project("mt5")
    first = review.prepare(root4, env.pkg("mt5a", base_files), kind="project", author="B")
    cids = {}
    for i, (label, files) in enumerate(variants.items()):
        time.sleep(0.01)
        r = review.prepare(root4, env.pkg(f"mt5v{i}", files), kind="project", author="B")
        cids[label] = (r["content_id"] == first["content_id"], r["package"]["sha256"] == first["package"]["sha256"])
    other_target = review.prepare(env.project("mt6"), env.pkg("mt6", base_files), kind="project", author="B")
    _ok("B2a 内容身份：同样内容的另一份 zip 得到同一 content_id；delete 清单、权限、二进制字节、wf_config、目标项目不同"
        "都得到不同的 content_id",
        cids["原样（另一份 zip 字节）"][0] and all(not v[0] for k, v in cids.items() if k != "原样（另一份 zip 字节）")
        and other_target["content_id"] != first["content_id"], json.dumps(cids, ensure_ascii=False))


def _t_block_late_timeout(_ok, env: Env):
    from . import review
    root = env.project("bl")
    req = review.prepare(root, env.pkg("bl"), kind="project", author="B")
    env.behave(verdict="block")
    d1 = review.run(req["request_id"])
    env.behave(verdict="pass")
    d2 = review.run(req["request_id"])
    req_b = review.prepare(root, env.pkg("bl"), kind="project", author="B")
    d3 = review.run(req_b["request_id"])
    _ok("B2a block 粘性：同一内容登记过 block 后，之后（含另一个请求）再审出 pass 也不允许",
        not d1["allowed"] and d1["verdict"] == "block" and not d2["allowed"] and d2["verdict"] == "block"
        and "blocked" in d2["reasons"] and req_b["content_id"] == req["content_id"] and not d3["allowed"],
        json.dumps([d2["reasons"], d3["reasons"]], ensure_ascii=False))
    root_t = env.project("to")
    req_t = review.prepare(root_t, env.pkg("to"), kind="project", author="B")
    env.behave(sleep=3)
    dt = review.run(req_t["request_id"], timeout_s=1)
    env.behave()
    root_l = env.project("lt")
    req_l = review.prepare(root_l, env.pkg("lt"), kind="project", author="B")
    env.behave(sleep=2)
    box = {}
    th = threading.Thread(target=lambda: box.setdefault("d", review.run(req_l["request_id"])))
    th.start()
    time.sleep(0.8)
    n = review.cancel(req_l["request_id"])
    th.join(30)
    late = box.get("d") or {}
    env.behave()
    fresh = review.run(req_l["request_id"])
    _ok("B2a 超时与晚到：超时 → timeout 不允许；被取消后才完成的 pass 记为 late、不允许；之后新的合法审查才允许",
        dt["state"] == "timeout" and not dt["allowed"] and n == 1 and late.get("state") == "late" and not late.get("allowed")
        and fresh["allowed"] and fresh["state"] == "completed",
        json.dumps([dt["state"], n, late.get("state"), late.get("reasons"), fresh["reasons"]], ensure_ascii=False))


def _t_corrupt_and_ids(_ok, env: Env):
    from . import review
    from .core import WfError
    root = env.project("cr")
    req = review.prepare(root, env.pkg("cr"), kind="project", author="B")
    d = review.run(req["request_id"])
    rp = review._request_path(req["request_id"])
    orig = rp.read_bytes()
    os.chmod(rp, 0o644)
    rp.write_bytes(orig.replace(b'"author":"B"', b'"author":"Q"'))
    try:
        review.status(root, req["request_id"])
        corrupt_req = "接受了"
    except WfError as e:
        corrupt_req = "ok" if "损坏" in str(e) else str(e)
    rp.write_bytes(orig)
    ap = review._attempt_dir(d["attempt_id"]) / "attempt.json"
    a_orig = ap.read_bytes()
    ap.write_bytes(b"{broken")
    d_corrupt = review.status(root, req["request_id"])
    ap.write_bytes(a_orig)
    res = {}
    for bad in ("../../etc/passwd", "rq-" + "g" * 32, "rq-" + "0" * 31, "/abs/rq-" + "0" * 32):
        try:
            review.status(None, bad)
            res[bad] = "接受了"
        except WfError:
            res[bad] = "ok"
    try:
        review.status(None, content_id="../" + "0" * 61)
        res["content_id 穿越"] = "接受了"
    except WfError:
        res["content_id 穿越"] = "ok"
    _ok("B2a 损坏与路径：请求被改（hash 不符）拒绝；尝试记录损坏按不通过处理；request_id / content_id 不合格（穿越、非十六进制、"
        "长度不对、绝对路径）一律拒绝",
        d["allowed"] and corrupt_req == "ok" and not d_corrupt["allowed"] and "attempt_record_corrupt" in d_corrupt["reasons"]
        and all(v == "ok" for v in res.values()), json.dumps([corrupt_req, d_corrupt["reasons"], res], ensure_ascii=False))


def _t_snapshot_and_race(_ok, env: Env):
    from . import review
    from .core import WfError
    root = env.project("sn")
    z = env.pkg("sn")
    r1 = review.prepare(root, z, kind="project", author="B")
    r2 = review.prepare(root, z, kind="project", author="B")
    snap = review._releases() / f"{r1['package']['sha256']}.zip"
    mode = stat.S_IMODE(os.stat(snap).st_mode)
    box = {}
    ts = [threading.Thread(target=lambda k=k, r=r: box.__setitem__(k, review.run(r["request_id"]))) for k, r in (("a", r1), ("b", r2))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    idx = review._index(r1["content_id"])
    both = review._load_request(r1["request_id"]) and review._load_request(r2["request_id"])
    _ok("B2a 竞争：同一内容两个请求互不覆盖，两次审查各有自己的尝试记录，快照只存一份且只读",
        r1["request_id"] != r2["request_id"] and r1["content_id"] == r2["content_id"] and both
        and set(idx.get("requests", [])) >= {r1["request_id"], r2["request_id"]} and len(idx.get("attempts", [])) == 2
        and box.get("a", {}).get("allowed") and box.get("b", {}).get("allowed") and mode & 0o222 == 0,
        json.dumps([idx, oct(mode)], ensure_ascii=False)[:400])
    snap_bytes = snap.read_bytes()
    os.chmod(snap, 0o644)
    with open(snap, "ab") as f:
        f.write(b"tamper")
    st = review.status(root, r1["request_id"], phase="install")
    rr = review.run(r1["request_id"])
    try:
        with tempfile.TemporaryDirectory() as td:
            review.extract_snapshot(r1["request_id"], td)
        ex = "接受了"
    except WfError:
        ex = "ok"
    snap.write_bytes(snap_bytes)   # 恢复，避免影响同一临时 local 里的其他用例
    os.chmod(snap, 0o444)
    _ok("B2a 只读快照被改：安装期复核、再次审查、解包都拒绝",
        not st["allowed"] and "snapshot_changed" in st["reasons"] and not rr["allowed"]
        and "snapshot_changed" in rr["reasons"] and ex == "ok", json.dumps([st["reasons"], rr["reasons"]], ensure_ascii=False))


def _t_reuse(_ok, env: Env):
    from . import review
    root = env.project("ru")
    req = review.prepare(root, env.pkg("ru"), kind="project", author="B")
    d = review.run(req["request_id"])
    done = review._parse_iso(d["times"]["review_completed"])
    in6 = review.status(root, req["request_id"], phase="install", now=done + timedelta(days=6))
    in8 = review.status(root, req["request_id"], phase="install", now=done + timedelta(days=8))
    code = ("import json, sys\nfrom pathlib import Path\nfrom wflib import review\n"
            "review._tool_home = lambda: Path(sys.argv[2])\n"   # 同一宿主：工具目录（规则来源）相同
            "d = review.status(None, sys.argv[1])\nprint(json.dumps({'allowed': d['allowed'], 'reasons': d['reasons']}))")
    r = subprocess.run([sys.executable, "-c", code, req["request_id"], str(env.tool)], cwd=str(WF_HOME), capture_output=True,
                       text=True, env=dict(os.environ), timeout=60)
    try:
        cross = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        cross = {"raw": r.stdout + r.stderr}
    _ok("B2a 复用期：同一材料 6 天后仍允许、8 天后过期；另起一个进程（跨命令）读同一宿主记录得到同样结论",
        d["allowed"] and in6["allowed"] and not in8["allowed"] and "expired" in in8["reasons"] and cross.get("allowed") is True,
        json.dumps([in8["reasons"], cross], ensure_ascii=False)[:300])


def _t_tool_target(_ok, env: Env):
    from . import review
    files = {"VERSION": b"9.9.10\n", "docs/REVIEW.md": "审查规则 v1\n".encode(), "local/x": b"protected"}
    z = _zip(env.base / "tool.zip", "collab-wf", files)
    req = review.prepare(None, z, kind="tool", author="B")
    ents = {e["path"]: e for e in review.read_package(z, kind="tool", protect=review.TOOL_PROTECT)["entries"]}
    _ok("B2a 工具目标：目标身份是工具（非项目），base 取工具目录自己的提交与 tree，local/ 下的成员记为不安装",
        req["target"]["kind"] == "tool" and req["target"]["tool"] == {"identity": "collab-wf"}
        and req["base"]["commit"] == _git(env.tool, "rev-parse", "HEAD") and req["base"]["tree"] == _git(env.tool, "rev-parse", "HEAD^{tree}")
        and ents["local/x"]["installed"] is False and ents["VERSION"]["installed"] is True,
        json.dumps(req["target"], ensure_ascii=False))


# ---------------- B2-FIX-B2A：独立复审反例 ----------------

def _attempts_for(cid: str, rid: str) -> list:
    from . import review
    out = []
    for aid in review._index(cid).get("attempts", []):
        a = review._load_attempt(aid)
        if a and a.get("request_id") == rid:
            out.append(a)
    return out


def _t_fix_dirty_binding(_ok, env: Env):
    from . import review
    res = {}
    for label, files in (("包不含该文件", {"PROJECT.md": b"# P\n", "NAME.txt": b"dy1"}),
                         ("包含该文件", {"PROJECT.md": b"# P\n", "app.py": b"print('v2')\n"})):
        root = env.project(f"dy{len(res)}")
        (root / "app.py").write_text("print('dirty-1')\n", encoding="utf-8")   # 送审前就是 dirty
        z = env.pkg(f"dy{len(res)}", files)
        req = review.prepare(root, z, kind="project", author="B")
        ok0 = review.run(req["request_id"])["allowed"]
        (root / "app.py").write_text("print('dirty-2')\n", encoding="utf-8")   # 同一路径改成不同字节
        f1 = review.final_check(req["request_id"], target=root)
        cid2 = review.prepare(root, z, kind="project", author="B")["content_id"]
        os.chmod(root / "app.py", 0o755)
        f2 = review.final_check(req["request_id"], target=root)
        (root / "app.py").unlink()
        f3 = review.final_check(req["request_id"], target=root)
        res[label] = (ok0, not f1["allowed"] and "material_changed" in f1["reasons"], cid2 != req["content_id"],
                      not f2["allowed"], not f3["allowed"])
    root = env.project("dyig")
    with open(root / ".gitignore", "a", encoding="utf-8") as f:
        f.write("build/\n")
    _git(root, "commit", "-qam", "ignore build")
    (root / "build").mkdir()
    (root / "build" / "out.txt").write_text("被忽略的旧内容\n", encoding="utf-8")   # git status 看不到，但安装会覆盖它
    req = review.prepare(root, env.pkg("dyig", {"PROJECT.md": b"# P\n", "build/out.txt": b"new\n"}), kind="project",
                         author="B")
    ok0 = review.run(req["request_id"])["allowed"]
    (root / "build" / "out.txt").write_text("被忽略的内容又变了\n", encoding="utf-8")
    f1 = review.final_check(req["request_id"], target=root)
    res["安装会覆盖被忽略的文件"] = (ok0, not f1["allowed"] and "material_changed" in f1["reasons"])
    _ok("B2a-FIX 1 工作区实际内容绑定：送审前已 dirty 的文件之后再改字节、只改权限、删除，消费复核都失效；"
        "新 prepare 的 content_id 也不同（包里有没有该文件都一样）；安装会覆盖的被忽略文件变化也失效",
        all(all(v) for v in res.values()), json.dumps(res, ensure_ascii=False))


def _t_fix_mode_diff(_ok, env: Env):
    from . import review
    root = env.project("md")
    (root / "run.sh").write_bytes(b"#!/bin/sh\necho hi\n")
    os.chmod(root / "run.sh", 0o644)
    _git(root, "add", "run.sh")
    _git(root, "commit", "-qm", "run.sh 0644")
    r755 = review.prepare(root, env.pkg("md755", {"PROJECT.md": b"# PROJECT.md\n", "run.sh": (b"#!/bin/sh\necho hi\n", 0o755)}),
                          kind="project", author="B")
    r644 = review.prepare(root, env.pkg("md644", {"PROJECT.md": b"# PROJECT.md\n", "run.sh": (b"#!/bin/sh\necho hi\n", 0o644)}),
                          kind="project", author="B")
    d755 = (review._local() / r755["diff"]["path"]).read_text(encoding="utf-8")
    d644 = (review._local() / r644["diff"]["path"]).read_text(encoding="utf-8")
    _ok("B2a-FIX 2 只改权限：字节与 HEAD 相同、0o644→0o755 时送审 diff 明确写出权限变化；权限相同则不出现；content_id 不同",
        "mode change run.sh: 0o644 -> 0o755" in d755 and "run.sh" not in d644.split("not installed")[0]
        and r755["content_id"] != r644["content_id"], json.dumps([d755[:300], d644[:200]], ensure_ascii=False))


def _t_fix_rules_missing(_ok, env: Env):
    from . import review
    rules = env.tool / "docs" / "REVIEW.md"
    saved = rules.read_bytes()
    rules.unlink()
    try:
        root = env.project("rm")
        req = review.prepare(root, env.pkg("rm"), kind="project", author="B")
        d = review.run(req["request_id"])
        f = review.final_check(req["request_id"], target=root)
    finally:
        rules.write_bytes(saved)
    _ok("B2a-FIX 3 规则来源 docs/REVIEW.md 缺失：rules 记为未知，审查与消费都不允许",
        req["rules"]["sha256"] is None and not d["allowed"] and "rules_unknown" in d["reasons"] and not f["allowed"],
        json.dumps([d["reasons"], f["reasons"]], ensure_ascii=False))


def _t_fix_diff_tamper(_ok, env: Env):
    from . import review
    root = env.project("dt")
    req = review.prepare(root, env.pkg("dt"), kind="project", author="B")
    dp = review._local() / req["diff"]["path"]
    orig = dp.read_bytes()
    os.chmod(dp, 0o644)
    dp.write_bytes(orig + "+ 审查者看到的是被改过的材料\n".encode("utf-8"))
    d1 = review.run(req["request_id"])
    n_att = len(_attempts_for(req["content_id"], req["request_id"]))
    dp.write_bytes(orig)
    d2 = review.run(req["request_id"])
    att = _attempts_for(req["content_id"], req["request_id"])[-1]
    sent = review._attempt_dir(att["attempt_id"]) / "diff.patch"
    sent_ok = sent.is_file() and _sha(sent.read_bytes()) == req["diff"]["sha256"]
    dp.write_bytes(orig + b"x")
    d3 = review.status(root, req["request_id"], phase="install")
    dp.write_bytes(orig)
    _ok("B2a-FIX 4 送审 diff 被改：不送审（没有尝试）；送审的是核过 hash 的副本；登记后再改 diff，消费复核也拒绝",
        not d1["allowed"] and "diff_file_changed" in d1["reasons"] and n_att == 0 and d2["allowed"] and sent_ok
        and not d3["allowed"] and "diff_file_changed" in d3["reasons"],
        json.dumps([d1["allowed"], d1["reasons"], n_att, d2["reasons"], sent_ok, d3["allowed"], d3["reasons"]],
                   ensure_ascii=False))


def _t_fix_casefold(_ok, env: Env):
    from . import review
    from .core import WfError
    root = env.project("cf")
    cases = {"文件 D 与目录 d": {"D": b"1", "d/x": b"2"}, "目录 A 与目录 a": {"A/x": b"1", "a/y": b"2"},
             "文件 X.txt 与目录 x.txt": {"X.txt": b"1", "x.txt/y": b"2"},
             "NFC 与 NFD 目录": {"caf\u00e9/x": b"1", "cafe\u0301/y": b"2"}}
    res = {}
    for i, (label, files) in enumerate(cases.items()):
        try:
            review.prepare(root, env.pkg(f"cf{i}", files), kind="project", author="B")
            res[label] = "接受了"
        except WfError:
            res[label] = "ok"
    good = review.prepare(root, env.pkg("cfok", {"Docs/a.txt": b"1", "Docs/b.txt": b"2"}), kind="project", author="B")
    _ok("B2a-FIX 5 大小写 / Unicode 规范化后的层级冲突在冻结时拒绝（文件与目录、同一目录不同写法）；同一写法的目录照常",
        all(v == "ok" for v in res.values()) and good["content_id"], json.dumps(res, ensure_ascii=False))


def _t_fix_pycache(_ok, env: Env):
    from . import review
    root = env.project("pc")
    files = {"PROJECT.md": b"# P\n", "app.py": b"print(1)\n", "__pycache__/app.cpython-39.pyc": b"\x00pyc",
             "lib/x.pyc": b"\x00x"}
    req = review.prepare(root, env.pkg("pc", files), kind="project", author="B")
    snap = review._releases() / f"{req['package']['sha256']}.zip"
    ents = {e["path"]: e for e in review.read_package(snap, kind="project", protect=[])["entries"] if e["type"] == "file"}
    with tempfile.TemporaryDirectory() as td:
        ex = review.extract_snapshot(req["request_id"], td)
        v1 = review.verify_extracted(req["request_id"], ex)
        (ex / "__pycache__" / "new.cpython-39.pyc").write_bytes(b"generated by tests")
        v2 = review.verify_extracted(req["request_id"], ex)
    diff = (review._local() / req["diff"]["path"]).read_text(encoding="utf-8")
    _ok("B2a-FIX 6 合法包含 __pycache__/*.pyc：记为不安装、送审材料写明；解包后复核不报缺失，测试生成的 pyc 也不算多出",
        v1 == [] and v2 == [] and ents["lib/x.pyc"]["installed"] is False and ents["app.py"]["installed"] is True
        and "not installed lib/x.pyc" in diff, json.dumps([v1, v2], ensure_ascii=False))


def _t_fix_runner_exit(_ok, env: Env):
    from . import review
    env.behave(exit=17)
    try:
        root = env.project("rx")
        req = review.prepare(root, env.pkg("rx"), kind="project", author="B")
        d = review.run(req["request_id"])
    finally:
        env.behave()
    adir = review._attempt_dir(d["attempt_id"]) if d.get("attempt_id") else None
    raw = (adir / "stdout.bin").read_bytes() if adir else b""
    _ok("B2a-FIX 7 审查程序打印合法 pass 但退出码 17：不允许；原文与诊断保留",
        not d["allowed"] and "runner_exit_17" in d["reasons"] and b'"verdict": "pass"' in raw
        and any("退出码 17" in x for x in d["diagnostics"]), json.dumps([d["reasons"], d["diagnostics"]], ensure_ascii=False))


def _t_fix_trust_now(_ok, env: Env):
    from . import review
    root = env.project("tn")
    req = review.prepare(root, env.pkg("tn"), kind="project", author="B")
    ok0 = review.run(req["request_id"])["allowed"]
    orig = env.verifier.read_bytes()
    env.verifier.write_bytes(orig + "\n# 字节被换\n".encode("utf-8"))
    a = review.final_check(req["request_id"], target=root)
    env.verifier.write_bytes(orig)
    env.set_transport(verifier=False)
    b = review.final_check(req["request_id"], target=root)
    env.verifier.write_bytes(orig + "\n# 换成另一个并重新固定\n".encode("utf-8"))
    env.set_transport()
    c = review.final_check(req["request_id"], target=root)
    env.verifier.write_bytes(orig)
    env.set_transport()
    back = review.final_check(req["request_id"], target=root)
    env.set_lanes({"B": {"sessions": [AUTHOR_SESSION, REVIEWER["session"]]}})
    e = review.final_check(req["request_id"], target=root)
    env.set_lanes({"B": {"sessions": [AUTHOR_SESSION]}})
    (env.tool / "docs" / "REVIEW.md").write_text("审查规则 v9\n", encoding="utf-8")
    f = review.status(None, req["request_id"])
    (env.tool / "docs" / "REVIEW.md").write_text("审查规则 v1\n", encoding="utf-8")
    _ok("B2a-FIX 可信源重核：审查后核验器字节被换、配置被删、换成另一个并重新固定，消费复核都失效；恢复后才允许；"
        "lanes 当前登记把审查者会话归入作者、规则改动，也失效",
        ok0 and not a["allowed"] and any(r.startswith("verifier_sha256_mismatch") for r in a["reasons"])
        and not b["allowed"] and "verifier_unavailable_now" in b["reasons"] and not c["allowed"]
        and "verifier_changed" in c["reasons"] and back["allowed"] and not e["allowed"] and "reviewer_is_author" in e["reasons"]
        and not f["allowed"] and "rules_changed" in f["reasons"],
        json.dumps([a["reasons"], b["reasons"], c["reasons"], e["reasons"], f["reasons"]], ensure_ascii=False))


def _t_fix_identity_current(_ok, env: Env):
    from . import review
    env.set_lanes({"B": {"sessions": [AUTHOR_SESSION]}, "R": {"sessions": [REVIEWER["session"]]}})
    try:
        root = env.project("ic")
        z = env.pkg("ic")
        r1 = review.prepare(root, z, kind="project", author="B")
        d1 = review.run(r1["request_id"])
        r2 = review.prepare(root, z, kind="project", author="R")   # 新请求的作者正是旧审查者
        s2 = review.status(root, r2["request_id"], phase="install")
        s1 = review.status(root, r1["request_id"], phase="install")
    finally:
        env.set_lanes({"B": {"sessions": [AUTHOR_SESSION]}})
    _ok("B2a-FIX 身份按当前请求判定：同一内容的新请求作者是旧审查者车道时，旧审查对新请求不算独立（原请求不受影响）",
        d1["allowed"] and r2["content_id"] == r1["content_id"] and not s2["allowed"] and "reviewer_is_author" in s2["reasons"]
        and s1["allowed"], json.dumps([s2["reasons"], s1["reasons"]], ensure_ascii=False))


def _race(env: Env, tag: str, use_process: bool) -> dict:
    """受控交错：consume 在复核之后（用 Event 停住），另一个线程或进程对同一内容登记 block，再放行 consume。
    记录：block 是否在 consume 停住期间完成登记、安装记录是否写下、block 的登记时刻与安装开始时刻的先后。"""
    from . import review
    root = env.project(tag)
    z = env.pkg(tag)
    r1 = review.prepare(root, z, kind="project", author="B")
    ok0 = review.run(r1["request_id"])["allowed"]
    r2 = review.prepare(root, z, kind="project", author="B")
    td = Path(tempfile.mkdtemp(prefix="wf-race-"))
    ex = review.extract_snapshot(r1["request_id"], td)
    entered, release = threading.Event(), threading.Event()
    real_fc = review.final_check

    def fc(*a, **k):
        out = real_fc(*a, **k)
        if threading.current_thread().name == "consumer":
            entered.set()
            release.wait(60)
        return out
    box = {}

    def consumer():
        try:
            box["c"] = review.consume(r1["request_id"], target=root, extracted_dir=ex)
        except Exception as e:   # noqa: BLE001
            box["c_err"] = str(e)
    env.behave(verdict="block")
    proc = None
    try:
        with _patched(review, "final_check", fc):
            t1 = threading.Thread(target=consumer, name="consumer")
            t1.start()
            entered.wait(60)
            if use_process:
                code = ("import sys\nfrom pathlib import Path\nfrom wflib import review\n"
                        "review._tool_home = lambda: Path(sys.argv[2])\nreview.run(sys.argv[1])\n")
                proc = subprocess.Popen([sys.executable, "-c", code, r2["request_id"], str(env.tool)], cwd=str(WF_HOME),
                                        env=dict(os.environ), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                done = None
                t_end = time.monotonic() + 4
                while time.monotonic() < t_end and done is None:
                    done = proc.poll()
                    time.sleep(0.05)
                finished_while_paused = done is not None
            else:
                t2 = threading.Thread(target=lambda: box.__setitem__("r", review.run(r2["request_id"])))
                t2.start()
                t2.join(4)
                finished_while_paused = not t2.is_alive()
            release.set()
            t1.join(60)
            if use_process:
                proc.wait(60)
            else:
                t2.join(60)
    finally:
        release.set()
        env.behave()
        shutil.rmtree(td, ignore_errors=True)
    blocks = [a for a in _attempts_for(r1["content_id"], r2["request_id"]) if a.get("verdict_claimed") == "block"]
    reg = [review._parse_iso(a["times"].get("result_registered")) for a in blocks if a["times"].get("result_registered")]
    inst = box.get("c", {}).get("times", {}).get("install_started")
    t_inst = review._parse_iso(inst) if inst else None
    after = review.status(root, r1["request_id"], phase="install")
    try:
        review.consume(r1["request_id"], target=root, extracted_dir=root)
        later = "consumed"
    except Exception:   # noqa: BLE001
        later = "refused"
    linear = bool(reg) and (t_inst is None or all(t > t_inst for t in reg))
    return {"ok0": ok0, "finished_while_paused": finished_while_paused, "consumed": "c" in box,
            "linear": linear, "blocked_after": "blocked" in after["reasons"], "later_consume": later}


def _t_fix_consume_race(_ok, env: Env):
    th = _race(env, "rc1", use_process=False)
    pr = _race(env, "rc2", use_process=True)

    def good(r):
        return r["ok0"] and not r["finished_while_paused"] and r["consumed"] and r["linear"] and r["blocked_after"] \
            and r["later_consume"] == "refused"
    _ok("B2a-FIX 消费与登记互斥（受控交错，线程与进程各一）：consume 复核后停住期间，另一方的 block 登记不进来；"
        "放行后安装记录先于 block 登记（线性），之后状态为 block，再消费被拒绝",
        good(th) and good(pr), json.dumps({"线程": th, "进程": pr}, ensure_ascii=False))


# ---------------- B2-FIX2-B2A：材料闭包反例 ----------------

def _mode_of(p: Path) -> int:
    return stat.S_IMODE(os.lstat(p).st_mode)


def _flip_cases(review, req, root, z, cases: dict) -> dict:
    """逐个做改动 → 复核与新 prepare 的 content_id → 恢复。返回 {标签: (复核失效, content_id 改变)}。"""
    res = {}
    for label, (do, undo) in cases.items():
        do()
        try:
            f = review.final_check(req["request_id"], target=root)
            cid2 = review.prepare(root, z, kind=req["kind"], author="B")["content_id"]
            res[label] = (not f["allowed"] and "material_changed" in f["reasons"], cid2 != req["content_id"])
        finally:
            undo()
    return res


def _t_fix2_delete_closure(_ok, env: Env):
    from . import review
    root = env.project("dc")
    with open(root / ".gitignore", "a", encoding="utf-8") as f:
        f.write("scratch/\n")
    _git(root, "commit", "-qam", "ignore scratch")
    sc = root / "scratch"
    (sc / "sub").mkdir(parents=True)
    (sc / "empty").mkdir()
    (sc / "kept-result.txt").write_text("结果 A\n", encoding="utf-8")
    (sc / "sub" / "x.txt").write_text("x\n", encoding="utf-8")
    os.symlink("../app.py", sc / "link")
    km, xm = _mode_of(sc / "kept-result.txt"), _mode_of(sc / "sub" / "x.txt")
    z = env.pkg("dc", {"PROJECT.md": b"# P\n", "RELEASE.json": b'{"delete": ["scratch"]}'})
    req = review.prepare(root, z, kind="project", author="B")
    ok0 = review.run(req["request_id"])["allowed"]
    diff = (review._local() / req["diff"]["path"]).read_text(encoding="utf-8")

    def write(p, text, mode):
        p.write_text(text, encoding="utf-8")
        os.chmod(p, mode)

    def relink(target):
        (sc / "link").unlink()
        os.symlink(target, sc / "link")
    cases = {
        "被忽略目录里的内容变化": (
            lambda: write(sc / "kept-result.txt", "结果 B\n", km), lambda: write(sc / "kept-result.txt", "结果 A\n", km)),
        "新增文件": (lambda: (sc / "new.txt").write_text("n\n", encoding="utf-8"), lambda: (sc / "new.txt").unlink()),
        "删除文件": (lambda: (sc / "sub" / "x.txt").unlink(), lambda: write(sc / "sub" / "x.txt", "x\n", xm)),
        "权限变化": (lambda: os.chmod(sc / "kept-result.txt", km ^ 0o100), lambda: os.chmod(sc / "kept-result.txt", km)),
        "空目录被删": (lambda: (sc / "empty").rmdir(), lambda: (sc / "empty").mkdir()),
        "链接目标变化": (lambda: relink("../PROJECT.md"), lambda: relink("../app.py")),
        "类型变化": (lambda: ((sc / "empty").rmdir(), (sc / "empty").write_text("", encoding="utf-8")),
                 lambda: ((sc / "empty").unlink(), (sc / "empty").mkdir())),
    }
    res = _flip_cases(review, req, root, z, cases)
    back = review.final_check(req["request_id"], target=root)
    _ok("B2a-FIX2 1 删除闭包：delete 指向被 .gitignore 忽略的目录时，整棵目录（内容、新增、删除、权限、空目录、链接目标、类型）"
        "都绑定；任一变化复核失效且 content_id 改变；恢复后复核又通过；送审材料逐项列出将删除的后代",
        ok0 and all(all(v) for v in res.values()) and back["allowed"] and "delete scratch/kept-result.txt" in diff
        and "delete scratch/link: symlink -> ../app.py" in diff and "delete scratch/empty: dir" in diff,
        json.dumps([res, back["reasons"]], ensure_ascii=False))
    root2 = env.project("dcp")
    req2 = review.prepare(root2, env.pkg("dcp", {"PROJECT.md": b"# P\n", "RELEASE.json": b'{"delete": ["workflow"]}'}),
                          kind="project", author="B")
    d2 = review.run(req2["request_id"])
    _ok("B2a-FIX2 1 删除整棵目录会连带删掉受保护的后代（release._delete 只查顶层）：不送审、不允许",
        not d2["allowed"] and "base_unsafe:delete_protected:workflow/wf.json" in d2["reasons"], json.dumps(d2["reasons"], ensure_ascii=False))


def _t_fix2_control_files(_ok, env: Env):
    from . import review
    root = env.project("cfx")
    with open(root / ".gitignore", "a", encoding="utf-8") as f:
        f.write("workflow/policy.json\nworkflow/goals.json\n")
    _git(root, "commit", "-qam", "ignore policy/goals")
    wf = root / "workflow"
    (wf / "policy.json").write_text('{"allow": []}\n', encoding="utf-8")
    (wf / "goals.json").write_text('{"goals": []}\n', encoding="utf-8")
    appr = {"policy": {"sha256": "a" * 64}, "goals": None, "confirmations": {}, "history": []}
    (wf / "approvals.json").write_text(json.dumps(appr), encoding="utf-8")
    z = env.pkg("cfx", {"PROJECT.md": b"# P\n", "app.py": b"print('v2')\n"})   # 包不写这些控制文件
    req = review.prepare(root, z, kind="project", author="B")
    ok0 = review.run(req["request_id"])["allowed"]

    def put(name, text):
        return lambda: (wf / name).write_text(text, encoding="utf-8")
    cases = {
        "policy.json（被忽略）": (put("policy.json", '{"allow": [".*"]}\n'), put("policy.json", '{"allow": []}\n')),
        "goals.json（被忽略）": (put("goals.json", '{"goals": [1]}\n'), put("goals.json", '{"goals": []}\n')),
        "approvals.json 的 policy 批准": (
            put("approvals.json", json.dumps({**appr, "policy": {"sha256": "b" * 64}})), put("approvals.json", json.dumps(appr))),
    }
    res = _flip_cases(review, req, root, z, cases)
    (wf / "approvals.json").write_text(json.dumps({**appr, "confirmations": {"G1": {"ts": "x"}}, "history": [1]}),
                                       encoding="utf-8")
    for name, text in (("state.json", '{"turn": 9}'), ("events.jsonl", '{"seq": 1}\n'), ("goals_state.json", '{"G": 1}')):
        (wf / name).write_text(text, encoding="utf-8")
    logs = review.final_check(req["request_id"], target=root)
    _ok("B2a-FIX2 2 控制文件：被 .gitignore 忽略且包不写的 policy.json、goals.json，以及 approvals.json 的批准记录变化，"
        "复核都失效且 content_id 改变；只改 approvals 的确认/历史与运行记录（state.json、events.jsonl、goals_state.json）不算",
        ok0 and all(all(v) for v in res.values()) and logs["allowed"], json.dumps([res, logs["reasons"]], ensure_ascii=False))
    tool = env.tool
    (tool / ".gitignore").write_text("VERSION\nMANIFEST.json\nnotes.txt\n", encoding="utf-8")
    _git(tool, "rm", "-q", "--cached", "VERSION")
    _git(tool, "add", ".gitignore")
    _git(tool, "commit", "-qm", "ignore VERSION/MANIFEST")
    (tool / "MANIFEST.json").write_text('{"version": "9.9.9"}\n', encoding="utf-8")
    (tool / "notes.txt").write_text("n1\n", encoding="utf-8")
    tz = _zip(env.base / "tool-cf.zip", "collab-wf", {"docs/extra.md": b"x\n"})
    treq = review.prepare(None, tz, kind="tool", author="B")
    tok0 = review.run(treq["request_id"])["allowed"]

    def tput(name, text):
        return lambda: (tool / name).write_text(text, encoding="utf-8")
    tcases = {
        "VERSION（被忽略）": (tput("VERSION", "9.9.10\n"), tput("VERSION", "9.9.9\n")),
        "MANIFEST.json（被忽略）": (tput("MANIFEST.json", '{"version": "0"}\n'), tput("MANIFEST.json", '{"version": "9.9.9"}\n')),
        "工具目录里其他被忽略的文件": (tput("notes.txt", "n2\n"), tput("notes.txt", "n1\n")),
    }
    tres = {}
    for label, (do, undo) in tcases.items():
        do()
        try:
            f = review.final_check(treq["request_id"], target=tool)
            cid2 = review.prepare(None, tz, kind="tool", author="B")["content_id"]
            tres[label] = (not f["allowed"] and "material_changed" in f["reasons"], cid2 != treq["content_id"])
        finally:
            undo()
    (tool / "local").mkdir(exist_ok=True)
    (tool / "local" / "x.json").write_text("{}", encoding="utf-8")
    tback = review.final_check(treq["request_id"], target=tool)
    _ok("B2a-FIX2 2 工具目标：被忽略的 VERSION、MANIFEST.json 与工具目录里其他被忽略文件变化，复核都失效且 content_id 改变；"
        "local/ 下的本机数据不算",
        tok0 and all(all(v) for v in tres.values()) and tback["allowed"], json.dumps([tres, tback["reasons"]], ensure_ascii=False))


def _t_fix2_unreadable(_ok, env: Env):
    from . import review
    root = env.base / "ng"
    (root / "workflow").mkdir(parents=True)
    (root / "workflow" / "wf.json").write_text(json.dumps({"project": "ng", "created": "2026-10-15T00:00:00+00:00"}),
                                               encoding="utf-8")
    (root / "PROJECT.md").write_text("# PROJECT.md\n", encoding="utf-8")
    (root / "inner").mkdir()
    (root / "inner" / "secret.txt").write_text("s1\n", encoding="utf-8")
    sm = _mode_of(root / "inner" / "secret.txt")
    z = env.pkg("ng", {"PROJECT.md": b"# P\n"})
    real = getattr(review, "_scandir", None)   # 旧实现没有注入点：只能靠下面的真实 chmod（非 root）对照

    def fake(path):
        if Path(path).name == "inner":
            raise PermissionError(13, "测试注入：列不了目录", str(path))
        return real(path)
    dbad = None
    if real is not None:
        with _patched(review, "_scandir", fake):
            bad = review.prepare(root, z, kind="project", author="B")
            dbad = review.run(bad["request_id"])
    req = review.prepare(root, z, kind="project", author="B")   # 恢复可读后的同一合法目标
    ok0 = review.run(req["request_id"])["allowed"]

    def write(text, mode):
        (root / "inner" / "secret.txt").write_text(text, encoding="utf-8")
        os.chmod(root / "inner" / "secret.txt", mode)
    cases = {
        "内部内容": (lambda: write("s2\n", sm), lambda: write("s1\n", sm)),
        "新增文件": (lambda: (root / "inner" / "n.txt").write_text("n\n", encoding="utf-8"),
                 lambda: (root / "inner" / "n.txt").unlink()),
        "删除文件": (lambda: (root / "inner" / "secret.txt").unlink(), lambda: write("s1\n", sm)),
        "权限": (lambda: os.chmod(root / "inner" / "secret.txt", sm ^ 0o100), lambda: os.chmod(root / "inner" / "secret.txt", sm)),
        "新增空目录": (lambda: (root / "inner" / "e").mkdir(), lambda: (root / "inner" / "e").rmdir()),
    }
    res = _flip_cases(review, req, root, z, cases)
    real_chmod = None
    if os.geteuid() != 0:   # 真实不可读目录：只有非 root 时 chmod 000 才真的读不了
        inner_mode = _mode_of(root / "inner")
        os.chmod(root / "inner", 0)
        try:
            rbad = review.prepare(root, z, kind="project", author="B")
            rd = review.run(rbad["request_id"])
            real_chmod = (not rd["allowed"] and "base_unknown:inner" in rd["reasons"])
        finally:
            os.chmod(root / "inner", inner_mode)
    else:
        print("  （当前以 root 运行：真实 chmod 000 反例不适用，已由注入列目录失败覆盖）", flush=True)
    back = review.final_check(req["request_id"], target=root)
    inj = None if dbad is None else (not dbad["allowed"] and "base_unknown:inner" in dbad["reasons"])
    _ok("B2a-FIX2 3 无版本库目标：列不了的子目录记为未知、不允许（注入" + ("；真实 chmod 000" if real_chmod is not None else "")
        + "）；恢复可读后同一合法目标通过；内部内容、新增、删除、权限、空目录变化都使复核失效且 content_id 改变",
        inj is not False and (inj or real_chmod) and ok0 and all(all(v) for v in res.values())
        and back["allowed"] and real_chmod in (None, True),
        json.dumps([inj, res, real_chmod], ensure_ascii=False))
    ereq = review.prepare(root, env.pkg("ngx", {"PROJECT.md": b"# P\n", "lib/a.py": b"A = 1\n"}), kind="project", author="B")
    with tempfile.TemporaryDirectory() as td:
        ex = review.extract_snapshot(ereq["request_id"], td)
        v_ok = review.verify_extracted(ereq["request_id"], ex)
        ereal = getattr(review, "_scandir", None)

        def efake(path):
            if Path(path).name == "lib":
                raise PermissionError(13, "测试注入：列不了目录", str(path))
            return ereal(path)
        if ereal is not None:
            with _patched(review, "_scandir", efake):
                v_bad = review.verify_extracted(ereq["request_id"], ex)
        else:
            v_bad = None
    _ok("B2a-FIX2 3 解包复核：列不了的子目录报 extracted_unknown（不静默当作缺失或跳过）；可读时照常无问题",
        v_ok == [] and v_bad is not None and "extracted_unknown:lib" in v_bad, json.dumps([v_ok, v_bad], ensure_ascii=False))
    groot = env.project("gu")
    (groot / "ud").mkdir()
    (groot / "ud" / "f.txt").write_text("u\n", encoding="utf-8")
    real_gf = getattr(review, "_git_full", None)

    def warn(args, cwd):
        out, err = real_gf(args, cwd)
        return (out, "warning: could not open directory 'ud/': Permission denied\n") if args[:1] == ["status"] else (out, err)
    gd = None
    if real_gf is not None:
        with _patched(review, "_git_full", warn):
            g1 = review.prepare(groot, env.pkg("gu", {"PROJECT.md": b"# P\n"}), kind="project", author="B")
            gd = review.run(g1["request_id"])
    git_real = None
    if os.geteuid() != 0:
        ud_mode = _mode_of(groot / "ud")
        os.chmod(groot / "ud", 0)
        try:
            g2 = review.prepare(groot, env.pkg("gu2", {"PROJECT.md": b"# P\n"}), kind="project", author="B")
            gd2 = review.run(g2["request_id"])
            git_real = not gd2["allowed"] and "base_unknown" in gd2["reasons"]
        finally:
            os.chmod(groot / "ud", ud_mode)
    _ok("B2a-FIX2 3 版本库目标：git status 只告警（例如列不了未跟踪目录）时按未知处理、不允许" +
        ("（注入；真实 chmod 000）" if git_real is not None else "（注入）"),
        (gd is None or (not gd["allowed"] and "base_unknown" in gd["reasons"])) and (gd is not None or git_real)
        and git_real in (None, True), json.dumps([gd and gd["reasons"], git_real], ensure_ascii=False))


def _t_fix3_control_links(_ok, env: Env):
    from . import review
    results = {}
    base = env.base.resolve()   # /var 等祖先别名不属于此处刻意构造的双链
    for name in ("policy", "goals", "approvals"):
        root = env.project("link-" + name).resolve()
        source = base / (name + "-source")
        source.write_text('{"policy":{"sha256":"a"},"goals":[]}', encoding="utf-8")
        middle = base / (name + "-middle")
        middle.symlink_to(source.name)   # 相对链接链，不只测试一次绝对链接
        leaf = root / "workflow" / (name + ".json")
        leaf.symlink_to(middle)
        z = env.pkg("link-" + name)
        req = review.prepare(root, z, kind="project", author="B")
        first = review.run(req["request_id"])["allowed"]
        state = next(s for s in req["base"]["control"] if s["path"] == "workflow/" + name + ".json")
        bound = state.get("source") == {"links": [
            {"path": str(leaf), "target": str(middle)},
            {"path": str(middle), "target": source.name},
        ], "resolved": str(source)}
        def changed():
            d = review.final_check(req["request_id"], target=root)
            newer = review.prepare(root, z, kind="project", author="B")
            return not d["allowed"] and newer["content_id"] != req["content_id"]
        source.write_text('{"policy":{"sha256":"b"},"goals":[1]}', encoding="utf-8")
        byte_change = changed()
        source.write_text('{"policy":{"sha256":"a"},"goals":[]}', encoding="utf-8")
        mode = _mode_of(source)
        os.chmod(source, 0o400 if mode != 0o400 else 0o600)
        mode_change = changed()
        os.chmod(source, mode)
        source.unlink()
        dangling = changed()
        bad = review.prepare(root, z, kind="project", author="B")
        before_bad = review.run(bad["request_id"])
        source.mkdir()
        nonfile = changed()
        source.rmdir()
        source.symlink_to(middle.name)   # source -> middle -> source
        loop = changed()
        source.unlink()
        source.write_text('{"policy":{"sha256":"a"},"goals":[]}', encoding="utf-8")
        os.chmod(source, mode)
        unreadable = True
        if os.geteuid() != 0:
            os.chmod(source, 0)
            try:
                unreadable = changed()
            finally:
                os.chmod(source, mode)
        tails = []
        for suffix in ("/", "/."):
            middle.unlink()
            middle.symlink_to(source.name + suffix)
            actual_failed = False
            try:
                leaf.read_bytes()
            except OSError:
                actual_failed = True
            tail_req = review.prepare(root, z, kind="project", author="B")
            tail_decision = review.run(tail_req["request_id"])
            tails.append(actual_failed and changed() and not tail_decision["allowed"]
                         and "base_unknown:workflow/" + name + ".json" in tail_decision["reasons"])
        middle.unlink()
        middle.symlink_to(source.name)
        back = review.final_check(req["request_id"], target=root)["allowed"]
        results[name] = [first, bound, byte_change, mode_change, dangling, nonfile, loop, unreadable, back, *tails,
                         not before_bad["allowed"] and "base_unknown:workflow/" + name + ".json" in before_bad["reasons"]]
    _ok("B2a-FIX3 控制链接链：policy/goals/approvals 绑定实际源、字节与权限；悬空、目录、循环、真实不可读、普通文件链接尾部 / 或 /. 均拒绝；恢复后复用",
        all(all(x) for x in results.values()), json.dumps(results, ensure_ascii=False))
    # workflow 本身作为父目录链接；读取失败注入覆盖可读权限之外的实际 I/O 失败
    root = env.project("parent-link")
    (root / "workflow").rename(env.base / "external-workflow")
    (root / "workflow").symlink_to(env.base / "external-workflow", target_is_directory=True)
    (root / "workflow" / "policy.json").write_bytes(b"A")
    z = env.pkg("parent-link")
    req = review.prepare(root, z, kind="project", author="B")
    first = review.run(req["request_id"])["allowed"]
    (root / "workflow" / "policy.json").write_bytes(b"B")
    changed = not review.final_check(req["request_id"], target=root)["allowed"]
    (root / "workflow" / "policy.json").write_bytes(b"A")
    real_open = os.open
    def fail(path, *args, **kwargs):
        if Path(path).name == "policy.json":
            raise OSError("synthetic source read failure")
        return real_open(path, *args, **kwargs)
    with _patched(review.os, "open", fail):
        failed = review.final_check(req["request_id"], target=root)
    _ok("B2a-FIX3 控制父目录链接按实际源绑定，实际 I/O 失败 unknown 拒绝",
        first and changed and not failed["allowed"] and "base_unknown:workflow/policy.json" in failed["reasons"],
        json.dumps(failed["reasons"], ensure_ascii=False))


def _t_fix3_ancestor_links(_ok, env: Env):
    from . import review
    base = env.base.resolve()
    physical, alias = base / "ancestor-physical", base / "ancestor-alias"
    physical.mkdir()
    alias.symlink_to(physical, target_is_directory=True)
    results = {}
    for name in ("policy", "goals", "approvals"):
        root = env.project("ancestor-" + name).resolve()
        source, middle = alias / (name + "-source"), alias / (name + "-middle")
        original = b'{"policy":{"sha256":"a"},"goals":[]}'
        source.write_bytes(original)
        middle.symlink_to(source.name)
        leaf = root / "workflow" / (name + ".json")
        leaf.symlink_to(middle)
        z = env.pkg("ancestor-" + name)
        req = review.prepare(root, z, kind="project", author="B")
        first = review.run(req["request_id"])["allowed"]
        state = next(s for s in req["base"]["control"] if s["path"] == "workflow/" + name + ".json")
        exact = state.get("source") == {"links": [
            {"path": str(leaf), "target": str(middle)},
            {"path": str(alias), "target": str(physical)},
            {"path": str(physical / middle.name), "target": source.name},
        ], "resolved": str(physical / source.name)}
        source.write_bytes(b'{"policy":{"sha256":"b"},"goals":[1]}')
        changed = review.final_check(req["request_id"], target=root)
        newer = review.prepare(root, z, kind="project", author="B")
        source.write_bytes(original)
        back = review.final_check(req["request_id"], target=root)
        results[name] = [first, exact, not changed["allowed"], newer["content_id"] != req["content_id"], back["allowed"]]
    _ok("B2a-FIX3 受控祖先别名：policy/goals/approvals 精确绑定三条 path/target 的完整顺序与最终源；源改变拒绝且身份变化，恢复可复用",
        all(all(x) for x in results.values()), json.dumps(results, ensure_ascii=False))


def _t_fix3_tool_links(_ok, env: Env):
    from . import review
    results = {}
    for name in ("VERSION", "MANIFEST.json", "linked-notes.txt"):
        tool = env.base / ("linked-tool-" + name)
        shutil.copytree(env.tool, tool)
        leaf = tool / name
        if leaf.exists():
            leaf.unlink()
        source = env.base / ("tool-source-" + name)
        source.write_bytes(b"9.9.9\n" if name == "VERSION" else b"A")
        middle = env.base / ("tool-middle-" + name)
        middle.symlink_to(source.name)
        leaf.symlink_to(middle)
        z = env.pkg("linked-tool-" + name, {"new.py": b"pass\n"})
        req = review.prepare(tool, z, kind="tool", author="B")
        first = review.run(req["request_id"])["allowed"]
        original = source.read_bytes()
        mode = _mode_of(source)
        def changed():
            d = review.final_check(req["request_id"], target=tool)
            try:
                newer = review.prepare(tool, z, kind="tool", author="B")
            except review.WfError:   # VERSION 悬空/目录时目标身份本身已不可证明
                return not d["allowed"]
            return not d["allowed"] and newer["content_id"] != req["content_id"]
        source.write_bytes(b"9.9.10\n" if name == "VERSION" else b"B")
        byte_change = changed()
        source.write_bytes(original)
        os.chmod(source, 0o400 if mode != 0o400 else 0o600)
        mode_change = changed()
        os.chmod(source, mode)
        source.unlink()
        dangling = changed()
        source.mkdir()
        nonfile = changed()
        source.rmdir()
        source.symlink_to(middle.name)
        loop = changed()
        source.unlink()
        source.write_bytes(original)
        os.chmod(source, mode)
        unreadable = True
        if os.geteuid() != 0:
            os.chmod(source, 0)
            try:
                unreadable = changed()
            finally:
                os.chmod(source, mode)
        back = review.final_check(req["request_id"], target=tool)
        results[name] = [first, byte_change, mode_change, dangling, nonfile, loop, unreadable, back["allowed"]]
    _ok("B2a-FIX3 工具 VERSION/MANIFEST 与普通工具文件链接均绑定实际源；字节/权限改变、悬空/循环/目录/真实不可读均拒绝，恢复可用",
        all(all(x) for x in results.values()), json.dumps(results, ensure_ascii=False))


def _t_fix3_sessions(_ok, env: Env):
    from . import review
    from .core import WfError
    root = env.project("strict-sessions")
    z = env.pkg("strict-sessions")
    req = review.prepare(root, z, kind="project", author="B")
    first = review.run(req["request_id"])["allowed"]
    env.set_lanes({"B": {"sessions": [AUTHOR_SESSION]}, "C": {"sessions": ["author-C"]}})
    current = review.prepare(root, z, kind="project", author="C")
    reuse = review.final_check(current["request_id"], target=root)["allowed"]
    results = []
    extracted = review.extract_snapshot(current["request_id"], env.base / "extract-sessions")
    for author in ("B", "C"):
        for sessions in (REVIEWER["session"], None, {}, [], [None], [{}], [""], [" "], ["ok", 1]):
            lanes = {"B": {"sessions": [AUTHOR_SESSION]}, "C": {"sessions": ["author-C"]}}
            lanes[author] = {"sessions": sessions}
            env.set_lanes(lanes)
            d = review.final_check(current["request_id"], target=root)
            refused = False
            try:
                review.consume(current["request_id"], target=root, extracted_dir=extracted)
            except WfError:
                refused = True
            results.append(not d["allowed"] and "author_identity_missing" in d["reasons"] and refused)
        lanes = {"B": {"sessions": [AUTHOR_SESSION]}, "C": {"sessions": ["author-C"]}}
        lanes[author] = {"sessions": [REVIEWER["session"]]}
        env.set_lanes(lanes)
        d = review.final_check(current["request_id"], target=root)
        results.append(not d["allowed"] and "reviewer_is_author" in d["reasons"])
    installs = list((review._reviews() / "installs" / current["content_id"]).glob("*.json"))
    env.set_lanes({"B": {"sessions": [AUTHOR_SESSION]}, "C": {"sessions": ["author-C"]}})
    back = review.consume(current["request_id"], target=root, extracted_dir=extracted)
    _ok("B2a-FIX3 原/当前作者 sessions 必须为非空字符串列表；坏结构 final_check 和 consume 拒绝且不写记录；独立复用消费恢复可用，任一作者与审查者重叠拒绝",
        first and reuse and req["content_id"] == current["content_id"] and all(results) and not installs and back["allowed"],
        json.dumps({"checks": results, "installs_before_valid": len(installs)}, ensure_ascii=False))
    env.set_lanes({"B": {"sessions": [AUTHOR_SESSION]}})


TESTS = [_t_package_rules, _t_no_host_request, _t_synthetic_allowed, _t_self_claims, _t_verifier_problems, _t_identity,
         _t_material, _t_block_late_timeout, _t_corrupt_and_ids, _t_snapshot_and_race, _t_reuse, _t_tool_target,
         _t_fix_dirty_binding, _t_fix_mode_diff, _t_fix_rules_missing, _t_fix_diff_tamper, _t_fix_casefold, _t_fix_pycache,
         _t_fix_runner_exit, _t_fix_trust_now, _t_fix_identity_current, _t_fix_consume_race,
         _t_fix2_delete_closure, _t_fix2_control_files, _t_fix2_unreadable,
         _t_fix3_control_links, _t_fix3_tool_links, _t_fix3_sessions, _t_fix3_ancestor_links]


def run_review(_ok) -> None:
    """供聚合器调用：在隔离子进程里运行；每组用例自己的临时目录，异常记为未通过，不中断其余用例。"""
    from . import review
    from .selftest import CHILD_FLAG
    if os.environ.get(CHILD_FLAG) != "1":
        _ok("B2a 审查模块测试只在隔离子进程里运行（WF_SELFTEST_CHILD=1）", False,
            "请经 wf selftest 或 python3 -m wflib.selftest_review 运行")
        return
    for t in TESTS:
        td = Path(tempfile.mkdtemp(prefix="wf-review-"))
        env = None
        try:
            env = Env(td)
            with _patched(review, "_tool_home", lambda env=env: env.tool):
                t(_ok, env)
        except Exception as e:   # noqa: BLE001
            _ok(f"B2a {t.__name__} 运行出错", False, f"{type(e).__name__}: {e}")
        finally:
            if env is not None:
                env.cleanup()
            for p in Path(td).rglob("*"):
                try:
                    os.chmod(p, 0o755 if p.is_dir() else 0o644)
                except OSError:
                    pass
            shutil.rmtree(td, ignore_errors=True)


def main() -> int:
    from . import selftest
    if os.environ.get(selftest.CHILD_FLAG) != "1":
        tmp = Path(tempfile.mkdtemp(prefix="wf-review-env-"))
        try:
            r = subprocess.run([sys.executable, "-m", "wflib.selftest_review"], cwd=str(WF_HOME),
                               env=selftest.child_env(os.environ, tmp / "local"), timeout=900)
            return r.returncode
        except subprocess.TimeoutExpired:
            print("审查模块测试子进程超过 900 秒没有结束，按未通过处理", flush=True)
            return 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    results = []

    def _ok(name, cond, detail=""):
        results.append(bool(cond))
        print(f"  {'✓' if cond else '✗'} {name}" + (f"  —— {detail}" if detail and not cond else ""), flush=True)
        return cond
    print(f"collab-wf B2a 审查模块测试（{(WF_HOME / 'VERSION').read_text().strip()}，Python {sys.version.split()[0]}）")
    run_review(_ok)
    print(f"B2a 审查模块测试结果：{sum(results)} / {len(results)} 通过")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
