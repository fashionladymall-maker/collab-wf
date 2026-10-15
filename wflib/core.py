"""基础设施：路径、时间、JSON 读写、注册表、项目实例、事件记录。只用标准库，兼容 Python 3.9+。"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ACTOR = {"actor": "cli"}   # 1.9.3（W93）：事件是谁写的——驱动器进程里为 driver，命令行为 cli

try:
    import fcntl
except ImportError:   # 非 POSIX 平台：状态锁退化为进程内计数
    fcntl = None

WF_HOME = Path(__file__).resolve().parent.parent
# 机器本地数据（注册表、看板进程信息）不进工具的 git，回滚工具时不受影响。测试时可用环境变量隔离。
LOCAL = Path(os.environ.get("WF_LOCAL_DIR", str(WF_HOME / "local")))
REGISTRY = LOCAL / "registry.json"
DEF_PATH = WF_HOME / "workflow.json"
SCHEMA = 1
_HELD: dict = {}   # 1.5.2：本进程已持有的状态锁（路径 -> [文件, 层数]），用于重入

# 落地版本时永不覆盖的路径（项目可在 workflow/wf.json 的 protect 中追加）
DEFAULT_PROTECT = [
    ".git/", ".venv/", ".env", "data/", "state/", "logs/", "results/",
    "workflow/wf.json", "workflow/state.json", "workflow/*.jsonl", "workflow/usage.json",
    "workflow/evidence/", "workflow/outbox/", "workflow/approvals.json", "workflow/goals_state.json",
]


class WfError(Exception):
    """可预期的错误：打印给用户看，不打印堆栈。"""


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def parse_iso(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def secs_since(s):
    d = parse_iso(s)
    return None if d is None else (now() - d).total_seconds()


def tool_version() -> str:
    return (WF_HOME / "VERSION").read_text(encoding="utf-8").strip()


def read_json(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(p: Path, obj) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def append_jsonl(p: Path, obj) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(p: Path, limit=None):
    p = Path(p)
    if not p.exists():
        return []
    rows = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"_bad_line": line[:200]})
    return rows[-limit:] if limit else rows


def git(args, cwd, check=True):
    r = subprocess.run(["git"] + list(args), cwd=str(cwd), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise WfError(f"git {' '.join(args)} 失败：{(r.stderr or r.stdout).strip()}")
    return r


def git_identity():
    return ["-c", "user.name=collab-wf", "-c", "user.email=collab-wf@local"]


# ---------------- 注册表 ----------------

def load_registry() -> dict:
    return read_json(REGISTRY, {"projects": []})


def register(name: str, root: Path) -> None:
    reg = load_registry()
    reg["projects"] = [p for p in reg["projects"] if p["name"] != name and Path(p["root"]) != Path(root)]
    reg["projects"].append({"name": name, "root": str(Path(root).resolve()), "added": now_iso()})
    write_json(REGISTRY, reg)


def find_root(start: Path):
    p = Path(start).resolve()
    for d in [p] + list(p.parents):
        if (d / "workflow" / "wf.json").exists():
            return d
    return None


def open_project(name=None, cwd=None) -> "Instance":
    if name:
        for p in load_registry()["projects"]:
            if p["name"] == name:
                return Instance(Path(p["root"]))
        raise WfError(f"注册表中没有项目 {name}。用 wf projects 查看，或在项目目录里运行。")
    root = find_root(Path(cwd or os.getcwd()))
    if root is None:
        raise WfError("当前目录不在任何已接入的项目中。先 cd 到项目目录，或用 --project 指定，或用 wf init 接入。")
    return Instance(root)


# ---------------- 项目实例 ----------------

class Instance:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.wf = self.root / "workflow"
        self.cfg_path = self.wf / "wf.json"
        self.cfg = read_json(self.cfg_path)
        if self.cfg is None:
            raise WfError(f"{self.root} 未接入工作流（缺少 workflow/wf.json）。")
        self.name = self.cfg["project"]
        self.state_path = self.wf / "state.json"
        self.events_path = self.wf / "events.jsonl"
        self.turns_path = self.wf / "turns.jsonl"
        self.transcript_path = self.wf / "transcript.jsonl"
        self.usage_path = self.wf / "usage.json"
        self.evidence = self.wf / "evidence"
        self.outbox = self.wf / "outbox"
        self._state = None
        self._base = None   # 1.5.2：读入（或上次写入）时磁盘上的状态，用于三方合并

    @property
    def state(self) -> dict:
        if self._state is None:
            disk = read_json(self.state_path)
            self._state = disk or {
                "schema": SCHEMA, "state": "IDLE", "since": now_iso(), "turn": 0, "seq": 0, "attempts": 0}
            self._base = json.loads(json.dumps(disk)) if disk else {}
        return self._state

    @contextmanager
    def state_lock(self, timeout=30.0):
        """1.5.2（W18）：状态的读-改-写在跨进程锁内完成。手工 wf 命令与驱动器并发时不再重号、不再互相覆盖。
        同一进程内可重入（同一项目的多个 Instance 共用一把锁）。"""
        key = str(LOCAL / "locks" / f"{self.name}.state.lock")
        held = _HELD.get(key)
        if held:
            held[1] += 1
            try:
                yield
            finally:
                held[1] -= 1
            return
        Path(key).parent.mkdir(parents=True, exist_ok=True)
        fh = open(key, "a")
        if fcntl is not None:
            deadline = time.time() + timeout
            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, PermissionError):
                    if time.time() > deadline:
                        fh.close()
                        raise WfError(f"等待状态锁超过 {int(timeout)} 秒：{key}")
                    time.sleep(0.05)
        _HELD[key] = [fh, 1]
        try:
            yield
        finally:
            _HELD.pop(key, None)
            if fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()

    def save(self) -> None:
        """写状态。1.5.2（W18）：在状态锁内与磁盘上的当前内容三方合并——只写回本进程改过的字段，
        其他进程在此期间写入的字段（例如 wf pause）保留；seq 取两边较大者。合并结果原地更新内存中的字典，
        已有的引用（ctx.st 等）继续有效。"""
        with self.state_lock():
            st = self.state
            disk = read_json(self.state_path) or {}
            base = self._base or {}
            merged = {k: (st[k] if k in st and st[k] == v else v) for k, v in disk.items()}
            for k in set(st) | set(base):
                if k == "seq":
                    continue
                if k in st and (k not in base or st[k] != base[k]):
                    merged[k] = st[k]
                elif k not in st and k in base:
                    merged.pop(k, None)
            merged["seq"] = max(int(disk.get("seq") or 0), int(st.get("seq") or 0))
            write_json(self.state_path, merged)
            for k in [k for k in st if k not in merged]:
                del st[k]
            for k, v in merged.items():
                if st.get(k) is not v:
                    st[k] = v
            self._base = json.loads(json.dumps(merged))

    def protect(self):
        return DEFAULT_PROTECT + list(self.cfg.get("protect", []))

    def event(self, type_: str, **fields) -> dict:
        # 1.5.0：先写状态再追加事件，中途被打断时只会跳号。1.5.2（W18）：序号在状态锁内按磁盘上的最新值分配，
        # 追加也在锁内完成，并发的手工命令与驱动器不再撞号，文件顺序与序号一致
        with self.state_lock():
            st = self.state
            disk_seq = int((read_json(self.state_path) or {}).get("seq") or 0)
            st["seq"] = max(disk_seq, int(st.get("seq") or 0)) + 1
            self.save()
            ev = {"seq": st["seq"], "ts": now_iso(), "type": type_, "turn": st.get("turn", 0), "state": st.get("state"),
                  "actor": ACTOR.get("actor", "cli"), "pid": os.getpid()}   # 1.9.3（W93）：事件身份
            ev.update({k: v for k, v in fields.items() if v is not None})
            append_jsonl(self.events_path, ev)
        return ev

    def turn_dir(self, turn=None) -> Path:
        t = self.state.get("turn", 0) if turn is None else turn
        d = self.evidence / f"turn-{int(t):03d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def usage_snapshot(self, max_age_s=600):
        """读取执行端写入的 usage.json；超过 max_age_s 未更新视为不可信，返回 None（只记录实测值）。"""
        if not self.usage_path.exists():
            return None
        age = now().timestamp() - self.usage_path.stat().st_mtime
        if age > max_age_s:
            return None
        u = read_json(self.usage_path) or {}
        out = {}
        for lim in u.get("limits", []):
            if lim.get("name") == "session":
                out["session"] = lim.get("used_pct")
            elif lim.get("name") in ("weekly", "weekly_all"):
                out["weekly"] = lim.get("used_pct")
        return out or None

    def version(self):
        from .projectmd import parse_project_md
        p = self.root / "PROJECT.md"
        return parse_project_md(p.read_text(encoding="utf-8")).get("version") if p.exists() else None


def matches_protect(rel: str, patterns) -> bool:
    import fnmatch
    rel = rel.replace(os.sep, "/")
    for pat in patterns:
        if pat.endswith("/"):
            if rel == pat[:-1] or rel.startswith(pat):
                return True
        elif fnmatch.fnmatch(rel, pat) or rel == pat:
            return True
    return False


def pid_alive(pid_file: Path, match: str):
    """pid 文件存在、进程存活、且命令行包含 match 时返回 pid；否则 None（防 PID 复用误判）。"""
    try:
        pid = int(Path(pid_file).read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return None
    r = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    return pid if match in r.stdout else None
