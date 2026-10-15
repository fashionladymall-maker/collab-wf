"""看板服务：一个进程服务所有已接入的项目。
页面：/ 工作流视图（运行图、当前步、时间线、事件）；/project.html 项目视图。
只监听 127.0.0.1；项目文件经 /files/<项目>/<路径> 访问，屏蔽 .env、.venv、.git、state/。"""
from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import anomaly, driver, goals, machine, ops, policy, roles
from .core import LOCAL, WF_HOME, Instance, WfError, git, load_registry, now_iso, read_json, read_jsonl, tool_version
from .projectmd import parse_project_md, validate

BLOCKED_PARTS = (".env", ".venv", ".git", "/state/", "__pycache__")
PID_FILE = LOCAL / "serve.pid"
LOG_FILE = LOCAL / "serve.log"
SERVE_INFO = LOCAL / "serve.json"
# 本进程启动时加载的工具版本。磁盘上的 VERSION 可能已被升级，而常驻进程仍在运行旧代码（缺口 12）。
SERVER_VERSION = tool_version()


def _instance(name):
    for p in load_registry()["projects"]:
        if p["name"] == name:
            return Instance(Path(p["root"]))
    raise WfError(f"未知项目 {name}")


def _summary(p):
    try:
        inst = Instance(Path(p["root"]))
        s = machine.status(inst)
        return {"name": inst.name, "root": str(inst.root), "state": s["state"], "state_name": s["name"],
                "turn": s["turn"], "dwell_s": s["dwell_s"], "stuck": s["stuck"], "overdue_s": s["overdue_s"],
                "owner_name": s["owner_name"], "version": inst.version(), "pin": inst.cfg.get("wf_version"),
                "paused": bool(inst.state.get("paused")),
                "needs_user": s["state"] in ("AWAITING_USER", "BLOCKED") or bool(inst.state.get("paused"))}
    except Exception as e:  # 项目目录被移动或损坏时仍然显示一行
        return {"name": p["name"], "root": p["root"], "error": str(e)}


def api_projects():
    return {"tool_version": tool_version(), "server_version": SERVER_VERSION,
            "projects": [_summary(p) for p in load_registry()["projects"]],
            "generated_at": now_iso()}


def api_flow(name):
    inst = _instance(name)
    s = machine.status(inst)
    tl = machine.timeline(inst)
    evs = read_jsonl(inst.events_path, limit=80)
    d = inst.turn_dir()
    it = read_json(d / "intake.json")
    rv = read_json(d / "review.json")
    vf = read_json(d / "verify.json") or {}
    execs = [read_json(x) for x in sorted(d.glob("c*.json"))]
    p = inst.root / "PROJECT.md"
    pm = parse_project_md(p.read_text(encoding="utf-8")) if p.exists() else {"decisions": []}
    return {
        "generated_at": now_iso(), "tool_version": tool_version(), "server_version": SERVER_VERSION,
        "pin": inst.cfg.get("wf_version"),
        "project": inst.name, "version": inst.version(), "status": s, "timeline": tl, "events": evs,
        "turn_detail": {
            "intake": {k: it[k] for k in ("blocks_missing", "commands", "checklist", "user_decisions", "effort", "zips")}
            if it else None,
            "review": rv, "verify": vf, "execs": execs,
            "checklist": [{"n": i + 1, "item": t, **(vf.get(str(i + 1)) or {})}
                          for i, t in enumerate((it or {}).get("checklist", []))],
        },
        "open_decisions": [x["text"] for x in pm.get("decisions", []) if not x["done"]],
        "driver": (inst.state.get("driver") or {}).get("last"),
        "driver_inflight": _inflight(inst),
        "explain": driver.explain(inst),
        "paused": inst.state.get("paused"),
        "goals": goals.progress(inst),
        "policy": policy.status(inst, "policy"),
        "agents": _agents(inst, s),
        "decisions": roles.decisions(inst, n=25),
        "attention": ops.attention(inst, s["turn"]),
        "conv": inst.state.get("conv"),
        "asks": _asks(inst),
        "next_wake": _next_wake(inst),
        "tools_progress": _tools_progress(inst),
    }


def _asks(inst):
    from . import asks
    try:
        return {"items": asks.pending(inst), "notices": asks.notices(inst)}
    except Exception as e:
        return {"items": [], "notices": [], "error": str(e)}


def _next_wake(inst):
    """下一次自动检查大约在什么时候（定时器间隔来自 wf schedule plist 写下的配置）。"""
    from .core import parse_iso
    from datetime import timedelta
    sched = read_json(LOCAL / "schedule.json", {}) or {}
    last = ((inst.state.get("driver") or {}).get("last") or {}).get("ts")
    iv = sched.get("interval")
    if not (last and iv):
        return None
    t = parse_iso(last)
    return (t + timedelta(seconds=int(iv))).isoformat(timespec="seconds") if t else None


def _tools_progress(inst):
    """工具调用进度：最近几轮里 Claude 的工具调用、执行端执行的命令、驱动器的自动处置。"""
    rows = {}
    for m in read_jsonl(inst.transcript_path, limit=400):
        if m.get("role") == "claude" and m.get("tools"):
            r = rows.setdefault(m.get("turn"), {"turn": m.get("turn"), "claude": {}, "cmds": 0, "failed": 0, "auto": 0})
            for t in m["tools"]:
                r["claude"][t.get("name", "?")] = r["claude"].get(t.get("name", "?"), 0) + 1
    for e in read_jsonl(inst.events_path):
        if e.get("type") in ("exec", "auto"):
            r = rows.setdefault(e.get("turn"), {"turn": e.get("turn"), "claude": {}, "cmds": 0, "failed": 0, "auto": 0})
            if e["type"] == "exec":
                r["cmds"] += 1
                r["failed"] += 1 if e.get("exit_code") not in (0, None) else 0
            else:
                r["auto"] += 1
    keys = sorted(k for k in rows if isinstance(k, int))[-6:]
    return [rows[k] for k in reversed(keys)]


def _agents(inst, s):
    """Agent 进度：每个参与者现在在做什么、最近一次动作、健康状况。"""
    from .core import pid_alive, secs_since
    st = inst.state
    out = []
    pm = parse_project_md((inst.root / "PROJECT.md").read_text(encoding="utf-8")) if (inst.root / "PROJECT.md").exists() else {"decisions": []}
    open_dec = [x for x in pm.get("decisions", []) if not x["done"]]
    need = s["state"] in ("AWAITING_USER", "BLOCKED") or bool(st.get("paused"))
    out.append({"name": "用户", "role": "决策与批准", "status": "warn" if (need or open_dec) else "ok",
                "state": ("需要你处理：" + (st.get("needs_text") or s.get("block_reason") or "已暂停")[:120]) if need
                else (f"{len(open_dec)} 项待决定" if open_dec else "暂无待处理事项"), "at": None, "metrics": []})
    turns = _latest_per_turn(read_jsonl(inst.turns_path))
    last = turns[-1] if turns else {}
    if s["state"] == "WAIT_REPLY":
        cst, ctext = "busy", f"正在处理第 {s['turn']} 轮（已等待 {s['dwell_s'] // 60} 分钟）"
    else:
        cst, ctext = ("ok", f"最近一次回复：第 {last.get('turn')} 轮") if last.get("ts_done") else ("off", "尚无记录")
    tr = read_jsonl(inst.transcript_path, limit=400)
    tools = sum(len(m.get("tools") or []) for m in tr if m.get("role") == "claude")
    out.append({"name": "Claude", "role": "设计与编码", "status": cst, "state": ctext, "at": last.get("ts_done") or last.get("ts_sent"),
                "metrics": [f"轮次 {len(turns)}", f"上一轮档位 {last.get('effort') or '—'}（{last.get('role') or '—'}）",
                            f"工具调用 {tools} 次"]})
    cfg = anomaly.transport_cfg()
    sends = [r for r in read_jsonl(LOCAL / "send_log.jsonl", limit=500) if r.get("project") == inst.name]
    out.append({"name": "执行端 / 适配器", "role": "通道收发（无界面）",
                "status": "ok" if cfg.get("send") and cfg.get("poll") else "off",
                "state": "适配器已配置" if cfg.get("send") else "未配置传输适配器（local/transport.json）",
                "at": sends[-1]["ts"] if sends else None,
                "metrics": [f"自动发送 {len(sends)} 次", "独立审查：已配置" if cfg.get("review") else "独立审查：未配置",
                            f"限速：{cfg['rate']['max_sends']} 条 / {cfg['rate'].get('window_s', 600)} 秒" if cfg.get("rate") else "限速：未设置"]})
    d = st.get("driver") or {}
    lastrun = d.get("last") or {}
    age = secs_since(lastrun.get("ts")) if lastrun else None
    inf = _inflight(inst)
    if inf and inf["alive"]:
        dst, dtext = "busy", f"正在运行（PID {inf['pid']}）"
    elif age is None:
        dst, dtext = "off", "从未运行（需要系统定时器运行 wf tick）"
    elif age > 900 and policy.status(inst, "policy")["approved"] and not st.get("paused"):
        dst, dtext = "bad", f"最近一次运行在 {int(age // 60)} 分钟前，定时器可能没有运行（wf schedule status）"
    else:
        dst, dtext = ("ok" if lastrun.get("exit") in (0, 10, 30) else "warn"), f"最近一次：{lastrun.get('note', '')[:100]}"
    autos = sum(1 for e in read_jsonl(inst.events_path) if e.get("type") == "auto")
    out.append({"name": "驱动器", "role": "按状态自动推进", "status": dst, "state": dtext, "at": lastrun.get("ts"),
                "metrics": [f"退出码 {lastrun.get('exit', '—')}", f"自动处置 {autos} 次",
                            "已暂停" if st.get("paused") else ("白名单已批准" if policy.status(inst, "policy")["approved"] else "白名单未批准")]})
    for proc in inst.cfg.get("processes", []):
        pid = pid_alive(inst.root / proc["pidfile"], proc["match"])
        out.append({"name": proc["name"], "role": "长期运行的项目进程", "status": "ok" if pid else "bad",
                    "state": f"运行中（PID {pid}）" if pid else "未运行", "at": None, "metrics": []})
    return out


def _inflight(inst):
    inf = (inst.state.get("driver") or {}).get("inflight")
    if not inf:
        return None
    try:
        os.kill(int(inf["pid"]), 0)
        alive = True
    except (ProcessLookupError, PermissionError, ValueError, TypeError):
        alive = False
    return {**inf, "alive": alive}


# ---------- 项目视图（原项目看板的数据，改为按项目读取） ----------

def _parse_changelog(text):
    out, cur = [], None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = {"title": line[3:].strip(), "items": []}
            out.append(cur)
        elif cur and line.startswith("- "):
            cur["items"].append(line[2:].strip())
    return out


def _latest_per_turn(rows):
    by, order = {}, []
    for r in rows:
        k = r.get("turn", id(r))
        if k not in by:
            order.append(k)
        by[k] = {**by.get(k, {}), **{a: b for a, b in r.items() if b is not None}}
    return [by[k] for k in order]


def _effort_stats(turns):
    agg = {}
    for t in turns:
        e = t.get("effort")
        if not e:
            continue
        a = agg.setdefault(e, {"turns": 0, "pct": [], "dur": [], "per_min": []})
        a["turns"] += 1
        b, af = t.get("usage_before") or {}, t.get("usage_after") or {}
        d = af["session"] - b["session"] if af.get("session") is not None and b.get("session") is not None else None
        dur = t.get("duration_s")
        if d is not None and d >= 0:
            a["pct"].append(d)
            if dur:
                a["per_min"].append(d / (dur / 60))
        if dur:
            a["dur"].append(dur)
    avg = lambda v: round(sum(v) / len(v), 2) if v else None
    return {k: {"turns": v["turns"], "samples": len(v["pct"]), "avg_session_pct": avg(v["pct"]),
                "avg_duration_s": avg(v["dur"]), "pct_per_min": avg(v["per_min"])} for k, v in agg.items()}


def _hook(inst):
    hp = inst.root / inst.cfg.get("hook", "workflow/project_hook.py")
    if not hp.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location(f"hook_{inst.name}", hp)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.collect(inst.root)
    except Exception as e:
        return {"title": "项目进程", "status": "bad", "summary": f"project_hook.py 出错：{e}"}


def _summ(m, n=80):
    t = (m.get("summary") or m.get("text") or "").strip().replace("\n", " ")
    return t[:n] + ("…" if len(t) > n else "")


def api_project(name):
    inst = _instance(name)
    root = inst.root
    rd = lambda f: (root / f).read_text(encoding="utf-8") if (root / f).exists() else ""
    pmd = parse_project_md(rd("PROJECT.md"))
    cl = rd("CHANGELOG.md")
    transcript = read_jsonl(inst.transcript_path, limit=300)
    turns = _latest_per_turn(read_jsonl(inst.turns_path))
    hook = _hook(inst)
    s = machine.status(inst)

    def last(role):
        return next((m for m in reversed(transcript) if m.get("role") == role), None)
    lc, le, lu = last("claude"), last("executor"), last("user")
    open_dec = [x for x in pmd["decisions"] if not x["done"]]
    actors = [
        {"name": "用户", "status": "warn" if open_dec else "ok",
         "state": f"{len(open_dec)} 项待决定" if open_dec else "暂无待决定事项", "at": lu.get("ts") if lu else None},
        {"name": "Claude", "status": "busy" if s["state"] == "WAIT_REPLY" else ("ok" if lc else "off"),
         "state": "正在处理执行端的消息" if s["state"] == "WAIT_REPLY" else (_summ(lc) if lc else "尚无记录"),
         "at": lc.get("ts") if lc else None},
        {"name": "执行端", "status": "ok" if le else "off", "state": _summ(le) if le else "尚无记录",
         "at": le.get("ts") if le else None},
        {"name": (hook or {}).get("title", "项目进程"), "status": (hook or {}).get("status", "off"),
         "state": (hook or {}).get("summary", "未配置项目钩子"), "at": (hook or {}).get("at")},
    ]
    gl = []
    if (root / ".git").exists():
        r = git(["log", "-8", "--pretty=%h\t%cI\t%s"], root, check=False)
        gl = [dict(zip(["hash", "time", "subject"], l.split("\t"))) for l in r.stdout.splitlines()]
    docs = []
    for p in sorted(root.glob("*.md")):
        docs.append({"path": p.name, "kind": "文档", "mtime": _mtime(p)})
    for p in sorted((WF_HOME / "docs").glob("*.md")):
        docs.append({"path": f"@wf/docs/{p.name}", "kind": "工作流文档", "mtime": _mtime(p)})
    res = root / "results"
    if res.exists():
        for f in sorted((f for f in res.iterdir() if f.suffix in (".md", ".png", ".csv")),
                        key=lambda f: f.stat().st_mtime, reverse=True)[:40]:
            docs.append({"path": f"results/{f.name}", "kind": "结果", "mtime": _mtime(f)})
    return {
        "generated_at": now_iso(),
        "project": {"name": inst.name, "version": pmd["version"], "phase": pmd["phase"], "date": pmd["date"]},
        "project_md_issues": validate(pmd, cl), "sections": pmd["sections"], "next_steps": pmd["next_steps"],
        "decisions": pmd["decisions"], "failures": pmd["failures"], "changelog": _parse_changelog(cl)[:10],
        "git": gl, "actors": actors, "hook": hook, "transcript": transcript, "turns": turns[-50:],
        "effort_stats": _effort_stats(turns), "usage": read_json(inst.usage_path), "docs": docs,
    }


def _mtime(p):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WF_HOME / "dashboard"), **kw)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code, msg):
        body = msg.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path):
        ctype = "text/plain; charset=utf-8" if path.suffix in (".md", ".txt", ".csv", ".log", ".json", ".jsonl", ".py") \
            else self.guess_type(str(path))
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        path = unquote(u.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if path == "/api/projects":
                return self._json(api_projects())
            if path == "/api/definition":
                return self._json(machine.definition())
            if path == "/api/flow":
                return self._json(api_flow(q.get("p", "")))
            if path == "/api/project":
                return self._json(api_project(q.get("p", "")))
            if path == "/api/page-image":   # 1.5.4：页面截图（PNG，只读）
                from . import page
                qs = parse_qs(urlparse(self.path).query)
                f = page.shot_file((qs.get("p") or [""])[0], (qs.get("turn") or [""])[0] or None)
                if f is None:
                    return self._text(404, "没有截图")
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if path in ("/api/page", "/api/transcript"):   # 1.5.3：Claude 页面快照与对话记录（只读）
                from . import page
                qs = parse_qs(urlparse(self.path).query)
                one = lambda k, d="": (qs.get(k) or [d])[0]
                if path == "/api/page":
                    return self._json(page.api_page(one("p"), turn=one("turn") or None, meta_only=one("meta") == "1"))
                return self._json(page.api_transcript(one("p"), after=one("after", "0")))
            if path == "/api/doctor":
                from .admin import doctor
                return self._json({"checks": doctor(_instance(q["p"]) if q.get("p") else None)})
            if path.startswith("/files/"):
                parts = path[len("/files/"):].split("/", 1)
                if len(parts) != 2 or ".." in parts[1] or any(b in "/" + parts[1] for b in BLOCKED_PARTS):
                    return self._text(403, "该路径不对看板开放（.env、.venv、.git、state/ 均被屏蔽）")
                if parts[1].startswith("@wf/docs/"):
                    f = (WF_HOME / "docs" / parts[1][len("@wf/docs/"):]).resolve()
                    base = (WF_HOME / "docs").resolve()
                else:
                    base = _instance(parts[0]).root
                    f = (base / parts[1]).resolve()
                if base not in f.parents or not f.is_file():
                    return self._text(404, "文件不存在")
                return self._file(f)
        except WfError as e:
            return self._json({"error": str(e)}, 404)
        except Exception as e:
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        if path == "/":
            self.path = "/flow.html" + (("?" + u.query) if u.query else "")
        return super().do_GET()

    def log_message(self, *a):
        pass


def serve_foreground(port: int):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    try:
        from .core import write_json
        write_json(SERVE_INFO, {"pid": os.getpid(), "port": port, "version": SERVER_VERSION, "started": now_iso()})
    except OSError:
        pass
    print(f"工作流看板：http://127.0.0.1:{port}/   （Ctrl+C 停止）", flush=True)
    def _term(*_a):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


def _running():
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None
    r = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    return pid if "wf.py" in r.stdout and "serve" in r.stdout else None


def daemon(action: str, port: int) -> str:
    LOCAL.mkdir(parents=True, exist_ok=True)
    pid = _running()
    if action == "status":
        return f"看板运行中（PID {pid}）：http://127.0.0.1:{port}/" if pid else "看板未运行"
    if action == "stop":
        if not pid:
            PID_FILE.write_text("")
            return "看板未运行"
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            time.sleep(0.2)
            if not _running():
                break
        PID_FILE.write_text("")
        return f"看板已停止（PID {pid}）"
    if pid:
        return f"看板已在运行（PID {pid}）：http://127.0.0.1:{port}/"
    with open(LOG_FILE, "ab") as log:
        p = subprocess.Popen([sys.executable, str(WF_HOME / "wf.py"), "serve", "--port", str(port)],
                             stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True,
                             env={**os.environ, "WF_LOCAL_DIR": str(LOCAL)})
    PID_FILE.write_text(str(p.pid))
    time.sleep(1.0)
    if p.poll() is not None:
        return f"看板启动失败，见 {LOG_FILE}"
    return f"看板已启动（PID {p.pid}）：http://127.0.0.1:{port}/"


def info() -> dict:
    """常驻看板进程的信息：是否在运行、端口、它加载的版本是否与磁盘上的工具版本一致。"""
    from .core import read_json
    i = read_json(SERVE_INFO, {}) or {}
    pid = _running()
    return {"running": bool(pid), "pid": pid, "port": i.get("port", 8765), "version": i.get("version"),
            "stale": bool(pid) and i.get("version") != tool_version()}


def restart_if_running() -> str:
    """工具升级或回滚后调用：看板在运行就重启，让它加载新代码（缺口 12）。"""
    i = info()
    if not i["running"]:
        return ""
    daemon("stop", i["port"])
    return daemon("start", i["port"])
