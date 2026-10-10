"""1.6.0：项目上下文同步（docs/SYNC.md）。

用户第 29–31 轮的要求：项目要自动同步到一个公开仓库，Claude 按链接就能读到最新的版本、进度与工具源码，不需要人贴文件。
分工与 review 适配器相同：工具决定“放什么、能不能放”，执行端的同步程序（local/transport.json 的 "sync"）只负责推送。

- 只放白名单内容：STATUS.md 与 README.md（工具生成）、项目的已提交文件（与仓库指纹同口径）、工具源码（版本清单内的文件）、
  已发出的本方消息、Claude 的回复、最新的工具版本包与项目版本包。
- 绝不放：local/（transport.json、会话凭据、快照）、workflow/evidence/ 的命令原文、approvals.json、状态与事件文件。
- 推送前对全部内容（含 zip 内的文本）跑发送前用的密钥扫描；命中就整次不同步，写进 local/sync/<项目>.json，
  由 wf ask / inbox 交给执行端。
- 内容没有变化就不调用同步程序。任何失败都不影响 wf tick。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import zipfile
from pathlib import Path

from .core import LOCAL, WF_HOME, git, now_iso, read_json, tool_version, write_json

SYNC_DIR = LOCAL / "sync"
README = """# {name} · 项目上下文（collab-wf 自动同步）

本仓库由 collab-wf 在每次内容变化后自动同步，是本项目的上下文载体：读这里就能知道最新的版本、进度与工具源码。

## 怎么读
1. `STATUS.md`：版本、轮次、最近一轮的结果、目标进度、待办，一屏看完。
2. `project/`：项目的已提交文件（PROJECT.md、CHANGELOG.md、workflow/ 下的白名单与目标等），与仓库指纹同口径。
3. `tool/`：当前安装的 collab-wf 工具源码（版本清单内的全部文件），可直接按路径读取。
4. `turns/turn-NNN.md`：执行端每轮发出的消息原文；`replies/reply-NNN.md`：Claude 每轮的回复原文。
5. `artifacts/`：最新的工具版本包与项目版本包。

## 边界
- 内容由工具按白名单挑选，推送前全部经过发送前用的密钥扫描（zip 内的文本也扫），命中就整次不同步。
- 不含：local/（通道配置、会话凭据、页面快照）、workflow/evidence/（命令原文）、approvals.json、状态与事件文件。
- 本仓库是只读镜像，源头在执行端机器上；请不要在这里改文件。
"""


def cfg() -> dict:
    from . import anomaly
    return anomaly.transport_cfg()


def enabled(inst) -> bool:
    c = cfg()
    return bool(c.get("sync")) and inst.name in (c.get("sync_projects") or [])


def record_path(inst) -> Path:
    return SYNC_DIR / f"{inst.name}.json"


def record(inst) -> dict:
    return read_json(record_path(inst), {}) or {}


def _newest(paths):
    paths = [p for p in paths if p.is_file()]
    return sorted(paths, key=lambda p: (p.stat().st_mtime, p.name))[-1] if paths else None


def _last_round(inst) -> str:
    if not inst.events_path.exists():
        return "（无）"
    last = None
    for line in inst.events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if '"round_result"' not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "round_result":
            last = e
    if not last:
        return "（无）"
    ok = "通过" if last.get("ok") else f"未通过（{last.get('failed_items') or 0} 项）"
    return f"第 {last.get('turn')} 轮验收{ok}（{last.get('ts', '')}）"


def status_md(inst, turns, replies, artifacts) -> str:
    from . import goals, machine, notify, ops
    s = machine.status(inst)
    head = git(["log", "--oneline", "--decorate", "-1"], inst.root, check=False).stdout.strip() if (inst.root / ".git").exists() else ""
    fp = ops.fingerprint(inst.root)
    lines = [f"# {inst.name} · 项目状态（collab-wf 自动同步）", "",
             f"第 {s['turn']} 轮 · {s['name']}（{s['state']}）", "",
             "## 版本",
             f"- 工具：collab-wf {tool_version()}",
             f"- 项目：{inst.version() or '（未知）'}；{head or '（没有提交）'}",
             f"- 仓库指纹：{fp.get('digest')}（{len(fp.get('files') or [])} 个文件）", "",
             "## 最近一轮", f"- {_last_round(inst)}"]
    if turns:
        lines.append(f"- 本方最新消息：`turns/{turns[-1]}`")
    if replies:
        lines.append(f"- Claude 最新回复：`replies/{replies[-1]}`")
    p = goals.progress(inst)
    lines += ["", "## 目标"]
    if p.get("defined"):
        lines.append(f"- 进度 {p['done']} / {p['total']}" + ("" if p.get("approved") else "（目标尚未批准，不生效）"))
        lines += [f"- {'✓' if i['ok'] else '○'} {i['id']} {i['title']}：{i['detail']}" for i in p["items"]]
    else:
        lines.append("- 没有定义目标")
    items = [x for x in notify.collect_inbox([inst]) if "|sync_" not in x["key"]]   # 同步自身的提示不进 STATUS，免得自己引起变化
    lines += ["", "## 待办"]
    lines += [f"- [{x['for']}] {x['title']}：{str(x['why'])[:200]}" for x in items] or ["- 无"]
    lines += ["", "## 最新产物"] + ([f"- `artifacts/{a}`" for a in artifacts] or ["- 无"])
    return "\n".join(lines) + "\n"


def collect(inst) -> dict:
    """返回 {仓库内路径: bytes}。"""
    from . import ops, release
    files = {}
    for rel in ops._fp_files(inst.root):
        files[f"project/{rel}"] = (inst.root / rel).read_bytes()
    for rel in release._tool_files():
        files[f"tool/{rel}"] = (WF_HOME / rel).read_bytes()
    turns = []
    sent = set()
    if inst.turns_path.exists():
        for line in inst.turns_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("status") == "sent" and isinstance(r.get("turn"), int):
                sent.add(r["turn"])
    for t in sorted(sent):
        f = inst.outbox / f"turn-{t:03d}.md"
        if f.is_file():
            files[f"turns/{f.name}"] = f.read_bytes()
            turns.append(f.name)
    replies = []
    for d in sorted(inst.evidence.glob("turn-*")) if inst.evidence.exists() else []:
        r = d / "reply.md"
        if r.is_file():
            name = f"reply-{d.name.split('-', 1)[1]}.md"
            files[f"replies/{name}"] = r.read_bytes()
            replies.append(name)
    artifacts = []
    tz = _newest(list(inst.outbox.glob("collab-wf-[0-9]*.zip")) if inst.outbox.exists() else [])
    pz = _newest(list(inst.evidence.glob("turn-*/delivered/*.zip")) if inst.evidence.exists() else [])
    for z in (tz, pz):
        if z:
            files[f"artifacts/{z.name}"] = z.read_bytes()
            artifacts.append(z.name)
    files["STATUS.md"] = status_md(inst, turns, replies, artifacts).encode("utf-8")
    files["README.md"] = README.format(name=inst.name).encode("utf-8")
    return files


def _scan_text(name: str, text: str) -> list:
    from .ops import SECRET_PATTERNS
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for pat, why in SECRET_PATTERNS:
            for m in re.finditer(pat, line):
                s = m.group(0)
                if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", s) and why.startswith("64"):
                    continue   # 与发送前检查相同：git 提交号、sha256 摘要不是密钥
                out.append(f"{name} 第 {i} 行：{why}（{s[:4]}…{s[-2:] if len(s) > 8 else ''}）")
    return out


def scan(files: dict) -> list:
    """发送前用的密钥扫描，覆盖每个文本文件与 zip 内的每个文本成员。"""
    hits = []
    for name, data in sorted(files.items()):
        if name.endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    for m in zf.namelist():
                        try:
                            hits += _scan_text(f"{name}:{m}", zf.read(m).decode("utf-8"))
                        except UnicodeDecodeError:
                            continue
            except zipfile.BadZipFile:
                hits.append(f"{name}：不是有效的 zip")
            continue
        try:
            hits += _scan_text(name, data.decode("utf-8"))
        except UnicodeDecodeError:
            continue
    return hits


def digest(files: dict) -> str:
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode("utf-8") + b"\0" + hashlib.sha256(files[name]).digest())
    return h.hexdigest()


def run(inst, dry_run=False, force=False) -> dict:
    """收集、扫描，内容有变化时调用执行端的同步程序。结果写入 local/sync/<项目>.json 并返回。"""
    from . import anomaly
    rec = record(inst)
    files = collect(inst)
    hits = scan(files)
    d = digest(files)
    res = {"ts": now_iso(), "files": len(files), "bytes": sum(len(b) for b in files.values()), "digest": d[:16]}
    if hits:
        res.update(status="blocked", secrets=hits[:20])
    elif dry_run:
        res.update(status="dry_run")
    elif d == rec.get("pushed_digest") and not force:
        res.update(status="unchanged")
    else:
        stage = SYNC_DIR / inst.name / "tree"
        if stage.exists():
            shutil.rmtree(stage)
        for name, data in files.items():
            p = stage / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        r = anomaly.call("sync", ["--project", inst.name, "--turn", inst.state.get("turn", 0), "--dir", stage],
                         timeout=cfg().get("sync_timeout_s", 300))
        if r.get("code") == 0:
            res.update(status="pushed", detail=(r.get("data") or {}).get("commit") or "")
            rec["pushed_digest"], rec["pushed_ts"], rec["failures"] = d, res["ts"], 0
        else:
            rec["failures"] = int(rec.get("failures", 0)) + 1
            res.update(status="failed", detail=(r.get("raw") or "")[-300:])
    if not dry_run:
        rec["last"] = res
        if res["status"] != "blocked":
            rec.pop("blocked", None)
        else:
            rec["blocked"] = {"ts": res["ts"], "secrets": res["secrets"]}
        write_json(record_path(inst), rec)
    return res


def after_tick(projects) -> list:
    """wf tick 之后调用：对开启了同步的项目各同步一次。任何错误都不影响 wf tick。"""
    out = []
    for inst in projects:
        try:
            if enabled(inst):
                out.append({"project": inst.name, **run(inst)})
        except Exception as e:   # noqa: BLE001
            out.append({"project": inst.name, "status": "error", "detail": str(e)[:300]})
    return out


def notices(inst) -> list:
    """给 wf ask / inbox 的提示：同步因疑似密钥暂停、或连续失败。"""
    if not enabled(inst):
        return []
    rec = record(inst)
    out = []
    if rec.get("blocked"):
        b = rec["blocked"]
        out.append({"key": f"sync_blocked:{b['ts']}", "title": "项目同步已暂停：发现疑似密钥",
                    "why": "要同步的内容里有疑似密钥，整次没有推送。先查明来源并移除，再运行 wf sync。命中位置：" + "；".join(b["secrets"][:3])})
    if int(rec.get("failures", 0)) >= 3:
        last = rec.get("last") or {}
        out.append({"key": f"sync_failing:{rec.get('pushed_ts')}", "title": "项目同步连续失败",
                    "why": f"同步程序已连续失败 {rec['failures']} 次，最近一次：{str(last.get('detail', ''))[:160]}"})
    return out
