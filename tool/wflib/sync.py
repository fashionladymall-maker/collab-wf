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


def repo_for(inst) -> str:
    """1.7.6：项目对应的仓库。transport.json 有 sync_repos（项目 → owner/仓库）时只认它（没有条目就是没有仓库，
    绝不推到别的项目的仓库）；没有 sync_repos 时沿用全局 sync_repo。"""
    c = cfg()
    m = c.get("sync_repos")
    if isinstance(m, dict):
        return str(m.get(inst.name) or "").strip().strip("/")
    return str(c.get("sync_repo") or "").strip().strip("/")


def enabled(inst) -> bool:
    c = cfg()
    if isinstance(c.get("sync_repos"), dict) and not repo_for(inst):   # 1.7.6：按项目分仓库时，没有条目的项目不同步
        return False
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
        lines.append(f"- {goals.phase_title(p)}")   # 1.8.2（P0-B）：先写当前阶段；旧阶段单列为历史、不计入当前进度
        lines.append(f"- 进度 {p['done']} / {p['total']}" + ("" if p.get("approved") else "（目标尚未批准，不生效）"))
        lines += [f"- {'✓' if i['ok'] else '○'} {i['id']} {i['title']}：{i['detail']}" for i in p["items"]]
        lines += [f"- {h}" for h in goals.history_lines(p)]
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


def scan_detailed(files: dict) -> list:
    """1.7.0：与 scan() 相同，但每条命中带原因类别（secret 疑似密钥 / bad_zip 不是有效的 zip），阻断记录据此写原因，
    不再靠文案后缀区分（1.6.9 独立审查意见）。"""
    hits = []
    for name, data in sorted(files.items()):
        if name.endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    for m in zf.namelist():
                        try:
                            hits += [("secret", h) for h in _scan_text(f"{name}:{m}", zf.read(m).decode("utf-8"))]
                        except UnicodeDecodeError:
                            continue
            except zipfile.BadZipFile:
                hits.append(("bad_zip", f"{name}：不是有效的 zip"))
            continue
        try:
            hits += [("secret", h) for h in _scan_text(name, data.decode("utf-8"))]
        except UnicodeDecodeError:
            continue
    return hits


def scan(files: dict) -> list:
    """发送前用的密钥扫描，覆盖每个文本文件与 zip 内的每个文本成员。"""
    return [h for _, h in scan_detailed(files)]


def digest(files: dict) -> str:
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode("utf-8") + b"\0" + hashlib.sha256(files[name]).digest())
    return h.hexdigest()


def _stage_tree(stage: Path, files: dict) -> dict:
    """1.8.2（P0-H）：把暂存目录原地更新成与 files 一致：内容相同的不重写，多出的文件逐个删除，不整目录删除
    （整目录删除会触发宿主的批量删除确认）。安全边界（1.8.0 独立审查 block）：清单里的路径必须是相对路径、
    不含 . 与 ..；暂存目录及它到 local/sync 的上级都不能是符号链接；暂存目录里的符号链接一律只删链接本身、
    从不跟随写入；写文件一律“新 inode 临时文件 + 原子替换”，不截断旧 inode（硬链接）。任何一条不满足就拒绝，
    不写穿到暂存目录之外。残余风险：前提是单写者——检查与替换之间若有另一个有写权限的进程同时改暂存目录，
    这里不承诺防住（不宣称能防住拥有同等权限的攻击者）。"""
    import os
    import stat as _stat
    import tempfile
    from .core import WfError
    stage = Path(stage)
    for name in files:
        parts = str(name).split("/")
        if not name or "\\" in name or str(name).startswith("/") or any(x in ("", ".", "..") for x in parts):
            raise WfError(f"同步清单里有不安全的路径，拒绝写入：{name!r}")
    for q in [stage] + [q for q in stage.parents if q == SYNC_DIR or SYNC_DIR in q.parents]:
        if q.is_symlink():
            raise WfError(f"暂存目录或它的上级是符号链接，拒绝写入：{q}")
    stage.mkdir(parents=True, exist_ok=True)
    removed = written = 0
    for dirpath, dirnames, filenames in os.walk(stage, topdown=True, followlinks=False):
        d = Path(dirpath)
        for n in list(dirnames):
            if (d / n).is_symlink():
                os.unlink(d / n)   # 只删链接本身
                dirnames.remove(n)
                removed += 1
        for n in filenames:
            f = d / n
            if f.is_symlink() or f.relative_to(stage).as_posix() not in files:
                os.unlink(f)
                removed += 1
    for name, data in files.items():
        parts = name.split("/")
        cur = stage
        for part in parts[:-1]:
            cur = cur / part
            if cur.is_symlink() or (os.path.lexists(cur) and not cur.is_dir()):
                os.unlink(cur)
            if not os.path.lexists(cur):
                cur.mkdir()
        t = cur / parts[-1]
        if os.path.lexists(t) and not t.is_symlink():
            ls = os.lstat(t)
            if _stat.S_ISDIR(ls.st_mode):
                raise WfError(f"暂存目录里的 {name} 是目录，拒绝覆盖")
            if _stat.S_ISREG(ls.st_mode) and ls.st_nlink == 1 and t.read_bytes() == data:
                continue   # 内容相同、没有别的硬链接：不重写
        # 1.8.2（第 107 轮审查 block）：不在旧 inode 上截断重写（旧文件可能是指向暂存目录之外的硬链接）。
        # 先在同目录建私有的新文件（O_EXCL，新 inode）写完、落盘，再原子替换目录项：旧 inode 与它的其他链接不受影响；
        # 写失败时旧文件原样保留、临时文件删除。
        fd, tmp = tempfile.mkstemp(dir=str(cur), prefix=".wf-stage-", suffix=".tmp")
        try:
            os.fchmod(fd, 0o644)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, t)   # rename 不跟随链接：目标是链接时替换的是链接本身
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        written += 1
    return {"written": written, "removed": removed}


def run(inst, dry_run=False, force=False) -> dict:
    """收集、扫描，内容有变化时调用执行端的同步程序。结果写入 local/sync/<项目>.json 并返回。"""
    from . import anomaly
    rec = record(inst)
    files = collect(inst)
    detailed = scan_detailed(files)   # 1.7.0：命中带原因类别
    hits = [h for _, h in detailed]
    d = digest(files)
    res = {"ts": now_iso(), "files": len(files), "bytes": sum(len(b) for b in files.values()), "digest": d[:16]}
    if hits:
        res.update(status="blocked", secrets=hits[:20], reasons=sorted({k for k, _ in detailed}))
    elif dry_run:
        res.update(status="dry_run")
    elif d == rec.get("pushed_digest") and not force:
        res.update(status="unchanged", commit=rec.get("pushed_commit") or "")   # 1.8.2：带上那次推送的提交号
    else:
        stage = SYNC_DIR / inst.name / "tree"
        _stage_tree(stage, files)   # 1.8.2：原地更新，不整目录删除（整目录删除会触发宿主的批量删除确认）
        rargs = ["--project", inst.name, "--turn", inst.state.get("turn", 0), "--dir", stage]
        if isinstance(cfg().get("sync_repos"), dict):   # 1.7.6：按项目分仓库时告诉同步程序推到哪个仓库
            rargs += ["--repo", repo_for(inst)]
        r = anomaly.call("sync", rargs,
                         timeout=cfg().get("sync_timeout_s", 300))
        commit = str((r.get("data") or {}).get("commit") or "").strip()
        if r.get("code") == 0 and commit:
            res.update(status="pushed", detail=commit, commit=commit)
            rec["pushed_digest"], rec["pushed_ts"], rec["failures"], rec["pushed_commit"] = d, res["ts"], 0, commit
        elif r.get("code") == 0:
            # 1.8.2（P0-H）：退出码 0 却没有提交号（例如同步程序拿不到锁就跳过）：不算推送成功、不更新已推送摘要，
            # 下一次照常再推；计入连续失败（3 次进 inbox）
            rec["failures"] = int(rec.get("failures", 0)) + 1
            res.update(status="unconfirmed", detail="同步程序退出码 0，但没有返回提交号（可能是忙、跳过或没有推送），不记为已推送")
        else:
            rec["failures"] = int(rec.get("failures", 0)) + 1
            res.update(status="failed", detail=(r.get("raw") or "")[-300:])
    if not dry_run:
        rec["last"] = res
        if res["status"] != "blocked":
            rec.pop("blocked", None)
        else:
            rec["blocked"] = {"ts": res["ts"], "secrets": res["secrets"], "reasons": res.get("reasons") or []}
        write_json(record_path(inst), rec)
    res["names"] = sorted(files)   # 1.8.2：这次（或与上次推送相同的）文件清单，只返回、不写进记录；短消息只链接其中的文件
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
        hits = [str(h) for h in (b.get("secrets") or [])]
        badzip = [h for h in hits if h.endswith("不是有效的 zip")]
        secret = [h for h in hits if h not in badzip]
        if isinstance(b.get("reasons"), list):   # 1.7.0：有结构化原因时以它为准；旧记录仍按文案判断
            secret = hits if "secret" in b["reasons"] else []
            badzip = hits if "bad_zip" in b["reasons"] else []
        # 1.6.9（N6、N7）：标题按实际命中写；key 由命中内容算出，同一原因不随每次 tick 的时间变成新键、反复唤醒
        key = "sync_blocked:" + hashlib.sha256("\n".join(sorted(hits)).encode("utf-8")).hexdigest()[:12]
        if secret:
            out.append({"key": key, "title": "项目同步已暂停：发现疑似密钥" + ("，另有无效 zip" if badzip else ""),
                        "why": "要同步的内容里有疑似密钥，整次没有推送。先查明来源并移除，再运行 wf sync。命中位置：" + "；".join((secret + badzip)[:3])})
        else:
            out.append({"key": key, "title": "项目同步已暂停：版本包不是有效的 zip",
                        "why": "要同步的版本包打不开（可能下载到了错误的内容），整次没有推送。核对并移走或替换后再运行 wf sync。命中：" + "；".join(badzip[:3])})
    if int(rec.get("failures", 0)) >= 3:
        last = rec.get("last") or {}
        out.append({"key": f"sync_failing:{rec.get('pushed_ts')}", "title": "项目同步连续失败",
                    "why": f"同步程序已连续失败 {rec['failures']} 次，最近一次：{str(last.get('detail', ''))[:160]}"})
    return out
