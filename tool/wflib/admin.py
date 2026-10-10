"""接入项目、健康检查、新对话的上下文恢复包。"""
from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import machine
from .core import (DEF_PATH, LOCAL, REGISTRY, SCHEMA, WF_HOME, Instance, WfError, git, load_registry, now_iso,
                   pid_alive, read_json, read_jsonl, register, secs_since, tool_version, write_json)
from .projectmd import validate_dir
from . import fidelity

GITIGNORE_LINES = ["workflow/state.json", "workflow/*.jsonl", "workflow/usage.json", "workflow/evidence/",
                   "workflow/outbox/", "workflow/approvals.json", "workflow/goals_state.json"]


def _minor(v: str) -> str:
    return ".".join(v.split(".")[:2])


def init_project(root: Path, name=None, adopt=False, turn=None, state=None) -> Instance:
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    wf = root / "workflow"
    if (wf / "wf.json").exists():
        raise WfError(f"{root} 已接入工作流。")
    name = name or root.name
    if not adopt:
        if (root / "PROJECT.md").exists():
            raise WfError("目录里已有 PROJECT.md。接入已有项目请加 --adopt。")
        tpl = (WF_HOME / "templates" / "PROJECT.template.md").read_text(encoding="utf-8")
        today = datetime.now().strftime("%Y-%m-%d")
        (root / "PROJECT.md").write_text(tpl.replace("<项目名>", name).replace("YYYY-MM-DD", today), encoding="utf-8")
        (root / "CHANGELOG.md").write_text(f"# CHANGELOG\n\n## v0.1 — {today}\n- 项目创建\n", encoding="utf-8")
    cfg = {"project": name, "wf_version": _minor(tool_version()), "schema": SCHEMA, "created": now_iso(),
           "protect": [], "processes": [], "hook": "workflow/project_hook.py"}
    write_json(wf / "wf.json", cfg)
    gi = root / ".gitignore"
    lines = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    add = [l for l in GITIGNORE_LINES if l not in lines]
    if add:
        gi.write_text("\n".join(lines + ["# collab-wf 实例数据（本机运行状态，不进版本库）"] + add) + "\n", encoding="utf-8")
    if (root / ".git").exists():
        # 只提交接入产生的两个文件，不触碰其他未提交的改动
        from .core import git_identity
        git(["add", "--", "workflow/wf.json", ".gitignore"], root)
        if git(["diff", "--cached", "--quiet"], root, check=False).returncode != 0:
            git(git_identity() + ["commit", "-q", "-m", f"接入 collab-wf {tool_version()}", "--",
                                  "workflow/wf.json", ".gitignore"], root)
    inst = Instance(root)
    st = inst.state
    if adopt:
        known = [t.get("turn", 0) for t in read_jsonl(inst.turns_path) if isinstance(t.get("turn"), int)]
        st["turn"] = int(turn) if turn is not None else (max(known) if known else 0)
        st["state"] = (state or "IDLE").upper()
        machine.sdef(st["state"])
    st["since"] = now_iso()
    inst.save()
    inst.event("adopt" if adopt else "init", to=st["state"], by="executor",
               reason=f"接入工作流 {tool_version()}" + ("（已有项目，不回填历史事件）" if adopt else ""))
    register(name, root)
    return inst


def doctor(inst=None) -> list:
    """返回 [(级别, 项目, 说明, 建议)]，级别：ok / warn / fail。"""
    out = []
    add = lambda lvl, item, msg, hint="": out.append({"level": lvl, "item": item, "msg": msg, "hint": hint})
    v = sys.version_info
    add("ok" if v >= (3, 9) else "fail", "Python", f"{v.major}.{v.minor}.{v.micro}", "" if v >= (3, 9) else "需要 3.9 或更高")
    if shutil.which("git") is None:
        add("fail", "git", "未安装", "安装 git")
    try:
        machine.definition()
        add("ok", "工作流定义", f"{DEF_PATH.name}，{len(machine.definition()['states'])} 个状态")
    except WfError as e:
        add("fail", "工作流定义", str(e))
    tv = tool_version()
    if (WF_HOME / ".git").exists():
        r = git(["describe", "--tags", "--always", "--dirty"], WF_HOME, check=False)
        desc = r.stdout.strip()
        add("warn" if "dirty" in desc else "ok", "工具版本", f"{tv}（{desc}）",
            "工具目录有未提交改动，升级与回滚会被拒绝" if "dirty" in desc else "")
    else:
        add("warn", "工具版本", f"{tv}（未初始化版本库）", "运行 wf self-init")
    try:
        from . import server
        si = server.info()
        if si["running"]:
            add("warn" if si["stale"] else "ok", "看板进程",
                f"运行中（PID {si['pid']}，端口 {si['port']}，加载的版本 {si['version']}）",
                "看板仍在运行旧版本的代码：wf serve --stop 后 wf serve --daemon" if si["stale"] else "")
    except Exception:
        pass
    if inst is None:
        return out
    pin = inst.cfg.get("wf_version")
    if pin == _minor(tv):
        add("ok", "版本匹配", f"项目固定 {pin}，工具 {tv}")
    elif tv.split(".")[0] != str(pin).split(".")[0]:
        add("fail", "版本匹配", f"项目固定 {pin}，工具 {tv}（主版本不同）", "数据格式可能不兼容：回滚工具（wf self-rollback）或确认后 wf pin")
    else:
        add("warn", "版本匹配", f"项目固定 {pin}，工具 {tv}", "阅读工具 CHANGELOG 后 wf pin 更新固定版本")
    p, issues = validate_dir(inst.root)
    add("ok" if not issues else "fail", "PROJECT.md", f"{p['version'] if p else '?'}，阶段：{p['phase'] if p else '?'}"
        if not issues else "；".join(issues), "" if not issues else "把校验输出回报给 Claude")
    if (inst.root / ".git").exists():
        from .release import _dirty
        d = _dirty(inst.root, inst.protect())
        add("ok" if not d else "warn", "项目工作区", "干净" if not d else f"{len(d)} 个未提交改动",
            "" if not d else "下次 wf apply 会被拒绝；说明来源后由 Claude 决定")
    s = machine.status(inst)
    if s["stuck"]:
        add("warn", "当前步骤", f"{s['state']}（{s['name']}）已停留 {s['dwell_s'] // 60} 分钟，超过 {s['max_dwell_s'] // 60} 分钟",
            "查看 wf status 中该步的异常处置表")
    elif s["overdue_s"]:
        add("warn", "计划节点", f"已逾期 {s['overdue_s'] // 3600} 小时：{s['wait_reason']}", "进入 COMPOSE 处理")
    elif s["state"] == "BLOCKED":
        add("fail", "当前步骤", f"阻塞：{s['block_reason']}", "排除原因后 wf unblock --to <状态> --note …")
    else:
        add("ok", "当前步骤", f"{s['state']}（{s['name']}），第 {s['turn']} 轮")
    for proc in inst.cfg.get("processes", []):
        pid = pid_alive(inst.root / proc["pidfile"], proc["match"])
        expected = proc.get("expected", True)
        if pid:
            add("ok", proc["name"], f"运行中（PID {pid}）")
        else:
            add("fail" if expected else "ok", proc["name"], "未运行", proc.get("start", "") if expected else "")
    # 1.5.6：驱动器心跳（与看板“驱动器”卡片同一判据；手写循环会静默退出）
    from .heartbeat import driver_health
    hb = driver_health(inst)
    if hb["status"] == "stale":
        add("warn", "驱动器心跳", f"最近一次运行在 {hb['age_s'] // 60} 分钟前，系统定时器可能没有运行",
            "wf schedule status 检查；安装定时器见 docs/PROCESS.md 第 1 节第 3 步")
    elif hb["status"] == "running":
        add("ok", "驱动器心跳", f"正在运行（PID {hb['pid']}）")
    elif hb["status"] == "never":
        add("ok", "驱动器心跳", "还没有运行记录（装好系统定时器后会出现）")
    else:
        add("ok", "驱动器心跳", ("已暂停" if hb["paused"] else f"最近一次运行在 {hb['age_s'] // 60} 分钟前")
            + (f"（退出码 {hb['last_exit']}）" if hb.get("last_exit") is not None else ""))
    # 1.5.0：额度有效期与记账统一为 10 分钟；另查额度空值、事件序号、回复归属、续传队列
    for lvl, item, msg, hint in fidelity.doctor_extra(inst):
        add(lvl, item, msg, hint)
    return out


def _last_turn_summary(inst: Instance) -> str:
    """最近一轮 Claude 的回报清单与执行端的验收结论（新对话里的 Claude 看不到本地文件，必须写进消息）。"""
    dirs = sorted(d for d in inst.evidence.glob("turn-*") if (d / "intake.json").exists()) if inst.evidence.exists() else []
    if not dirs:
        return "无记录"
    d = dirs[-1]
    it = read_json(d / "intake.json", {}) or {}
    vf = read_json(d / "verify.json", {}) or {}
    names = {"pass": "通过", "fail": "未通过", "deviation": "有偏离", "skip": "暂缓"}
    lines = [f"第 {int(d.name.split('-')[1])} 轮（Claude 建议档位 {it.get('effort') or '未写明'}）："]
    if it.get("blocks_missing"):   # 1.5.0：上一轮回复缺块时写明，避免恢复包里只剩一行标题
        rp = d / "reply.md"
        head = (rp.read_text(encoding="utf-8", errors="replace") if rp.exists() else "")[:500]
        lines.append(f"  该轮回复缺少固定块：{'、'.join(it['blocks_missing'])}，所以没有清单与验收结论。回复开头：{head!r}")
    for i, item in enumerate(it.get("checklist", []), 1):
        v = vf.get(str(i)) or {}
        lines.append(f"  {i}. {item} —— {names.get(v.get('verdict'), '未验收')}" + (f"（{v['note']}）" if v.get("note") else ""))
    for i, dec in enumerate(it.get("user_decisions", []), 1):
        lines.append(f"  待用户决定 {i}：{dec}")
    return "\n".join(lines)


def bootstrap(inst: Instance, reason: str, by: str = "executor") -> dict:
    """生成开新对话所需的全部材料：说明消息 + 仓库 zip（git archive，只含已提交文件，不含数据与密钥）。"""
    if not (inst.root / ".git").exists():
        raise WfError("项目还没有 git 版本库，无法打包。")
    from .release import _dirty
    if _dirty(inst.root, inst.protect()):
        raise WfError("工作区有未提交的改动，打包会遗漏它们。先处理后再生成。")
    head = git(["rev-parse", "--short", "HEAD"], inst.root).stdout.strip()
    ver = inst.version() or "v?"
    inst.outbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    zpath = inst.outbox / f"{inst.name}-{ver}-{head}.zip"
    git(["archive", "--format=zip", f"--prefix={inst.name}/", "-o", str(zpath), "HEAD"], inst.root)
    s = machine.status(inst)
    from .ops import fingerprint
    fp = fingerprint(inst.root)
    tool_zip = _tool_zip(inst.outbox)
    last = _last_turn_summary(inst)
    tpl = (WF_HOME / "templates" / "bootstrap.md").read_text(encoding="utf-8")
    text = (tpl.replace("{reason}", reason).replace("{zip}", zpath.name).replace("{head}", head)
            .replace("{version}", ver).replace("{tool_zip}", tool_zip.name).replace("{tool_version}", tool_version())
            .replace("{state}", f"{s['state']}（{s['name']}），第 {s['turn']} 轮"
                     + (f"；等待到 {s['until']}：{s['wait_reason'] or ''}" if s.get("until") else ""))
            .replace("{fingerprint}", f"{fp['digest']}（{fp['count']} 个文件）")
            .replace("{last_turn}", last)
            .replace("{workflow_md}", (WF_HOME / "docs" / "WORKFLOW.md").read_text(encoding="utf-8"))
            .replace("{project_md}", (inst.root / "PROJECT.md").read_text(encoding="utf-8"))
            .replace("{tool_manifest}", fidelity.tool_manifest_text(WF_HOME, inst.root)))
    mpath = inst.outbox / f"bootstrap-{stamp}.md"
    mpath.write_text(text, encoding="utf-8")
    short = _bootstrap_short(inst, reason, ver, head, s, fp, last, stamp)   # 1.6.3：短消息 + 公开仓库链接
    inst.event("bootstrap", file=mpath.name, zip=zpath.name, tool_zip=tool_zip.name, reason=reason,
               short=short.name if short else None, by=by)   # 1.6.4：驱动器生成时记 by=driver
    return {"message": mpath, "zip": zpath, "tool_zip": tool_zip, "chars": len(text), "short": short}


def _bootstrap_short(inst: Instance, reason: str, ver: str, head: str, s: dict, fp: dict, last: str, stamp: str):
    """1.6.3：项目开启了同步、并在 transport.json 写了 sync_repo（owner/仓库）时，另外生成一条短的续接消息：
    项目上下文在公开仓库里（用户第 31 轮：“以后都用这个仓库做这个项目的上下文”），新对话按链接读取，不需要附件。
    生成前先同步一次，保证链接里是最新内容。没有开启同步时返回 None，长消息照旧。"""
    from . import sync
    c = sync.cfg()
    repo = str(c.get("sync_repo") or "").strip().strip("/")
    if not repo or not sync.enabled(inst):
        return None
    try:
        sync.run(inst)
    except Exception:   # noqa: BLE001  同步失败不妨碍生成消息；仓库里是上一次同步的内容
        pass
    raw = f"https://raw.githubusercontent.com/{repo}/{c.get('sync_branch') or 'main'}"
    sent = []
    if inst.turns_path.exists():
        for line in inst.turns_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"sent"' in line:
                try:
                    r = __import__("json").loads(line)
                except ValueError:
                    continue
                if r.get("status") == "sent" and isinstance(r.get("turn"), int):
                    sent.append(r["turn"])
    replies = sorted(d.name.split("-", 1)[1] for d in inst.evidence.glob("turn-*") if (d / "reply.md").is_file()) \
        if inst.evidence.exists() else []
    lines = [f"【项目续接 · 新对话】档位：high", "",
             f"上一个对话因 {reason} 结束。项目上下文在公开仓库 {repo}，请直接按链接读取，不需要附件：", "",
             f"1. 进度总览：{raw}/STATUS.md",
             f"2. 项目状态与计划：{raw}/project/PROJECT.md 、{raw}/project/VISION.md",
             f"3. 协作规则：{raw}/tool/docs/WORKFLOW.md ；【驱动指令】格式：{raw}/tool/docs/DRIVER.md 第 5 节",
             (f"4. 最近一轮：我方消息 {raw}/turns/turn-{max(sent):03d}.md" if sent else "4. 最近一轮：我方消息（无）")
             + (f" ；你的回复 {raw}/replies/reply-{replies[-1]}.md" if replies else ""),
             f"5. 工具源码：{raw}/tool/ 下按路径读取（例如 tool/wflib/driver.py）；版本包在 artifacts/", "",
             f"当前：工具 collab-wf {tool_version()}，项目 {ver}（提交 {head}），仓库指纹 {fp['digest']}（{fp['count']} 个文件）；"
             f"工作流 {s['state']}（{s['name']}），第 {s['turn']} 轮。", "",
             "最近一轮的回报清单与验收结论：", last, "",
             "本轮请只做三件事：复述当前版本、阶段、待用户决策与工具边界；按仓库内容核对仓库指纹；给出下一轮指令。"
             "本轮不要交付新版本。raw 链接偶尔有几分钟的缓存，内容对不上时稍后重读。"]
    p = inst.outbox / f"bootstrap-{stamp}-short.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _tool_zip(outdir: Path) -> Path:
    """把 collab-wf 工具本身打包（已提交的版本；没有版本库时按目录打包），新对话里的 Claude 需要它继续开发工具。"""
    import zipfile
    outdir.mkdir(parents=True, exist_ok=True)
    if (WF_HOME / ".git").exists():
        head = git(["rev-parse", "--short", "HEAD"], WF_HOME).stdout.strip()
        z = outdir / f"collab-wf-{tool_version()}-{head}.zip"
        git(["archive", "--format=zip", "--prefix=collab-wf/", "-o", str(z), "HEAD"], WF_HOME)
        return z
    z = outdir / f"collab-wf-{tool_version()}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in WF_HOME.rglob("*"):
            rel = f.relative_to(WF_HOME)
            if f.is_file() and not ({"local", ".git", "__pycache__"} & set(rel.parts)):
                zf.write(f, f"collab-wf/{rel}")
    return z
