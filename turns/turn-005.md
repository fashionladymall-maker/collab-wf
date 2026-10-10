【回报 · 第 5 轮 · v0.1】档位：high

① 环境障碍与偏离
- [EXEC_FAIL] 命令执行失败。回报给 Claude。 第 7 条命令「grep -n carry /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/ops.py」退出码 1
- [EXEC_FAIL] 命令执行失败。回报给 Claude。 第 10 条命令「grep -n 前置 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py」退出码 1
- [EXEC_FAIL] 命令执行失败。回报给 Claude。 第 11 条命令「grep -n 前置 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_v15.py」退出码 1
- [EXEC_FAIL] 命令执行失败。回报给 Claude。 第 12 条命令「grep -n 前置 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py」退出码 1
- 命令 c07 退出码 1：`grep -n carry /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/ops.py`
- 命令 c10 退出码 1：`grep -n 前置 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py`
- 命令 c11 退出码 1：`grep -n 前置 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_v15.py`
- 命令 c12 退出码 1：`grep -n 前置 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py`

② 关键数字与终端原文

1) core.py 全文，含 def event。 —— 通过
```
$ cat /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/core.py
"""基础设施：路径、时间、JSON 读写、注册表、项目实例、事件记录。只用标准库，兼容 Python 3.9+。"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

WF_HOME = Path(__file__).resolve().parent.parent
# 机器本地数据（注册表、看板进程信息）不进工具的 git，回滚工具时不受影响。测试时可用环境变量隔离。
LOCAL = Path(os.environ.get("WF_LOCAL_DIR", str(WF_HOME / "local")))
REGISTRY = LOCAL / "registry.json"
DEF_PATH = WF_HOME / "workflow.json"
SCHEMA = 1

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

    @property
    def state(self) -> dict:
        if self._state is None:
            self._state = read_json(self.state_path) or {
                "schema": SCHEMA, "state": "IDLE", "since": now_iso(), "turn": 0, "seq": 0, "attempts": 0}
        return self._state

    def save(self) -> None:
        write_json(self.state_path, self.state)

    def protect(self):
        return DEFAULT_PROTECT + list(self.cfg.get("protect", []))

    def event(self, type_: str, **fields) -> dict:
        st = self.state
        st["seq"] = int(st.get("seq", 0)) + 1
        ev = {"seq": st["seq"], "ts": now_iso(), "type": type_, "turn": st.get("turn", 0), "state": st.get("state")}
        ev.update({k: v for k, v in fields.items() if v is not None})
        self.save()   # 1.5.0：先写状态再追加事件。中途被打断时只会跳号，不会出现重复的序号
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
exit=0  耗时 0.0s
```

2) driver.py 顶层函数索引。 —— 通过
```
$ grep -n ^def /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
47:def run(inst: Instance, max_seconds=600, max_steps=40) -> tuple:
62:def _run_locked(inst, max_seconds, max_steps):
119:def explain(inst: Instance) -> dict:
153:def _anomaly(ctx: Ctx, facts: dict, detail: str):
198:def _wait_retry(ctx):
208:def _health(ctx: Ctx, did: list):
240:def _exec_env(inst):
251:def h_idle(ctx):
255:def h_done(ctx):
259:def h_user(ctx):
264:def h_wait_schedule(ctx):
277:def h_quota_wait(ctx):
288:def _compose_text(ctx):
316:def h_compose(ctx):
368:def h_send(ctx):
431:def _finalize_send(ctx, out, note):
449:def _quota(inst):
456:def h_wait_reply(ctx):
559:def h_intake(ctx):
575:def _vt(v):
580:def h_review(ctx):
634:def _independent_review(ctx, src, zip_path) -> list:
664:def _after_review(ctx, it, d):
676:def _to_report(ctx):
693:def _clear_turn(inst):
700:def h_apply(ctx):
753:def _run_gates(ctx, gates, label) -> dict:
773:def h_execute(ctx):
827:def h_verify(ctx):
902:def _breaker(ctx, why):
920:def tick(projects: list, max_seconds=900) -> tuple:
exit=0  耗时 0.0s
```

3) driver.py 中含 lock 的行（锁与 blocking 判断的位置）。 —— 通过
```
$ grep -n lock /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
49:    (LOCAL / "locks").mkdir(exist_ok=True)
50:    lock = open(LOCAL / "locks" / f"{inst.name}.lock", "w")
52:        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
53:    except BlockingIOError:
56:        return _run_locked(inst, max_seconds, max_steps)
58:        fcntl.flock(lock, fcntl.LOCK_UN)
59:        lock.close()
62:def _run_locked(inst, max_seconds, max_steps):
105:                machine.go(inst, "BLOCKED", by="driver", reason=note[:300], block_reason=note[:500],
106:                           blocked_from=inst.state["state"], force=True)
135:        return {"where": where, "why": s["block_reason"] or "", "who": "用户",
136:                "what": "排除原因后 wf unblock --to <状态> --note …（wf status 显示该步的处置表）"}
162:        act = r.get("then", "block")
191:        machine.go(inst, target, by="driver", reason=text[:300], block_reason=text[:800],
192:                   blocked_from=inst.state["state"], force=inst.state["state"] in ("BLOCKED", "DONE"))
261:    return "user", s.get("needs_text") or s.get("block_reason") or "等待用户"
542:                       block_reason="同一轮消息连续被通道的安全防护拒绝，可能是消息内容触发了防护", blocked_from="WAIT_REPLY")
543:            inst.state["needs_text"] = "同一轮消息连续被安全防护拒绝。请检查消息内容后 wf unblock。"
565:    if res["blocks_missing"]:
566:        return _anomaly(ctx, {"source": "intake", "kind": "missing_blocks"}, "缺少：" + "、".join(res["blocks_missing"]))
617:    blocking = [x for x in it.get("user_decisions", []) if "前置" in x or "阻塞" in x]
618:    findings += [f"Claude 标注的前置决定：{x}" for x in blocking]
654:    if r["code"] != 0 or r["data"].get("verdict") not in ("pass", "concerns", "block"):
660:           "review_adapter.json", "阻止" if v == "block" else ("有关注点" if v == "concerns" else "通过"))
661:    return [f"独立审查（{model}）认为应阻止：" + "；".join(r["data"].get("notes", [])[:5])] if v == "block" else []
exit=0  耗时 0.0s
```

4) driver.py 中含“前置”的行（W15 那一行）。 —— 通过
```
$ grep -n 前置 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
617:    blocking = [x for x in it.get("user_decisions", []) if "前置" in x or "阻塞" in x]
618:    findings += [f"Claude 标注的前置决定：{x}" for x in blocking]
exit=0  耗时 0.0s
```

5) fidelity.py 第 395–410 行（seq_check）。 —— 通过
```
$ sed -n 395,410p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/fidelity.py
        k += 1
    return k


def seq_check(events_path: Path) -> dict:
    rows, bad = _jsonl(events_path)
    seqs = [r.get("seq") for r in rows if isinstance(r.get("seq"), int)]
    pairs = list(zip(seqs, seqs[1:]))
    return {"count": len(seqs), "dup": len(seqs) - len(set(seqs)), "backwards": sum(1 for a, b in pairs if b <= a),
            "gaps": sum(1 for a, b in pairs if b > a + 1), "bad_lines": bad}


def unverified_replies(events_path: Path, last: int = 10) -> int:
    rows, _ = _jsonl(events_path)
    marks = [r for r in rows if r.get("type") in ("reply_unverified", "reply_checked")]
    return sum(1 for r in marks[-last:] if r.get("type") == "reply_unverified")
exit=0  耗时 0.0s
```

6) fidelity.py 中的续传逻辑位置。 —— 通过
```
$ grep -n carry /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/fidelity.py
18:CARRY_FILE = "carry.json"
209:def load_carry(p: Path) -> list:
214:def report_evidence(items: list, execs: list, d: Path, carry_path: Path, verdicts: dict, turn: int,
219:    （workflow/outbox/carry.json），下一份回报自动先补发。每段原文只出现一次。
278:    old = load_carry(carry_path)
291:        _write_json(carry_path, pending)
438:    q = load_carry(Path(inst.outbox) / CARRY_FILE)
exit=0  耗时 0.0s
```

7) ops.py 中的续传逻辑位置；没有匹配也照原样回报。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n carry /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/ops.py
exit=1  耗时 0.0s
```

8) wf.py 中 compose_report 的调用位置。 —— 通过
```
$ grep -n compose_report /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
534:        p = ops.compose_report(_p(args), args.out)
exit=0  耗时 0.0s
```

9) release.py 顶层函数索引。 —— 通过
```
$ grep -n ^def /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/release.py
21:def _extract(zip_path: Path, dest: Path) -> Path:
49:def _dirty(root: Path, protect) -> list:
61:def _copy_tree(src: Path, dst: Path, protect, skip=("RELEASE.json",), written=None) -> list:
78:def _delete(root: Path, rels, protect, done=None) -> list:
97:def _commit(root: Path, message: str, tag=None):
112:def _restart_hints(inst: Instance, changed) -> list:
125:def apply_zip(inst: Instance, zip_path, by="executor") -> dict:
172:def _restore_after_failure(root, tracked, has_head, written, delete_list, extra, inst, cfg_before) -> str:
206:def _find_ref(root: Path, to: str) -> str:
218:def restore_tree(repo: Path, ref: str, message: str, protect=()) -> tuple:
235:def rollback(inst: Instance, to: str, by="executor") -> dict:
254:def self_init() -> str:
266:def _vtuple(v: str):
270:def self_update(zip_path, skip_test=False, allow_same=False) -> dict:
318:def _restart_board() -> bool:
335:def self_rollback(tag: str) -> dict:
349:def self_versions() -> list:
359:def _tool_files() -> dict:
373:def write_manifest() -> Path:
379:def self_check() -> dict:
exit=0  耗时 0.0s
```

10) 自检中与“前置”相关的断言（selftest_driver.py）；没有匹配也照原样回报。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n 前置 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py
exit=1  耗时 0.0s
```

11) 同上（selftest_v15.py）。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n 前置 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_v15.py
exit=1  耗时 0.0s
```

12) 同上（selftest.py）。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n 前置 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py
exit=1  耗时 0.0s
```

③ 需要决策的点
无。

④ 需要注意（Attention）
- 自动处置 EXEC_FAIL → report：第 7 条命令「grep -n carry /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/ops.py」退出码 1
- 自动处置 EXEC_FAIL → report：第 10 条命令「grep -n 前置 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py」退出码 1
- 自动处置 EXEC_FAIL → report：第 11 条命令「grep -n 前置 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_v15.py」退出码 1
- 自动处置 EXEC_FAIL → report：第 12 条命令「grep -n 前置 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py」退出码 1
- 清单第 7, 10, 11, 12 项没有机器判据，结论需要 Claude 根据原文判断
- 当前对话已累计约 84513 字符、4 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

附：仓库指纹 4d1aa87b9b11c651（v0.1，4 个文件）
