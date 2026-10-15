"""接入项目、健康检查、新对话的上下文恢复包。"""
from __future__ import annotations

import hashlib
import os
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


def _tmp_for(final: Path) -> Path:
    # B1-B：临时文件以点开头，不会被 collab-wf-[0-9]*.zip、bootstrap-*.md 这类按名字选文件的地方误选
    return final.with_name(f".{final.name}.tmp-{os.getpid()}")


def _unlink(p) -> None:
    try:
        Path(p).unlink()
    except OSError:
        pass


def _rename(src, dst) -> None:
    """发布用的原子改名（单独成函数：测试在第 N 次改名时注入失败）。"""
    os.replace(src, dst)


def _restore(bak: Path, final: Path) -> None:
    """失败时把副本改回原名（单独成函数：测试可以注入恢复失败）。"""
    os.replace(bak, final)


def _keep_old(final: Path):
    """r2（独立审查 R4）：覆盖前给已有的同名文件留一个可恢复的副本（硬链接，原文件在被替换前一直可读；
    不支持硬链接时复制）。没有旧文件时返回 None。
    r3（独立审查 R6）：副本名每次唯一，绝不删除或覆盖已存在的副本——以前恢复失败留下的旧字节要跨重试保留，
    由人工核对后处理；本函数只会删除自己这一次刚建、复制到一半失败的那一个。"""
    import secrets
    if not os.path.lexists(final):
        return None
    for _ in range(16):
        bak = final.with_name(f".{final.name}.bak-{os.getpid()}-{secrets.token_hex(6)}")
        if os.path.lexists(bak):
            continue
        try:
            os.link(final, bak)
        except FileExistsError:
            continue
        except OSError:
            try:
                with open(final, "rb") as src, open(bak, "xb") as dst:   # x：名字已被占用时失败，不覆盖
                    shutil.copyfileobj(src, dst)
                shutil.copystat(final, bak)
            except FileExistsError:
                continue
            except BaseException:
                _unlink(bak)   # 复制到一半失败：只删本次新建的半截副本
                raise
        return bak
    raise WfError(f"无法为 {final.name} 生成唯一的副本名，未落地")


def _publish(pending) -> None:
    """把 [(临时文件, 最终文件)] 依次改名落地。任何一步失败：已替换的同名旧文件从副本原样恢复，本次新落地的
    删除，临时文件清掉，然后抛出原错误——旧的一套完整材料保持原样，不留新的半套。全部成功后才删副本。
    r3（R6）：只清理本次调用自己建立的副本；恢复失败的副本保留并在错误里写明，之后的调用不会动它。"""
    placed = []   # [(最终文件, 副本或 None)]，只记已经改名成功的
    backups = []
    try:
        for tmp, final in pending:
            bak = _keep_old(final)
            if bak is not None:
                backups.append(bak)
            _rename(tmp, final)
            placed.append((final, bak))
    except BaseException:
        stuck = []
        for final, bak in reversed(placed):
            try:
                if bak is not None:
                    _restore(bak, final)
                else:
                    os.unlink(final)
            except OSError as e:
                stuck.append(f"{final.name}（副本 {bak.name if bak else '无'}：{e}）")
        for tmp, _ in pending:
            _unlink(tmp)
        restored = {b for _, b in placed if b is not None}
        for bak in backups:
            if bak not in restored:
                _unlink(bak)
        if stuck:
            raise WfError("落地中途失败，且以下文件没能恢复原样，旧内容的副本保留在 outbox 中（之后的生成不会删除或"
                          "覆盖它们，请人工核对后处理）：" + "；".join(stuck))
        raise
    for bak in backups:
        _unlink(bak)


def bootstrap(inst: Instance, reason: str, by: str = "executor", on_tool_dirty: str = "declare") -> dict:
    """生成开新对话所需的全部材料：说明消息 + 仓库 zip（git archive，只含已提交文件，不含数据与密钥）。
    B1-B：工具材料一次固定来源（有版本库时是一个提交，没有时是一次冻结快照），工具 zip、WORKFLOW.md、模板、VERSION
    与文件清单全部取自它，生成期间 HEAD 移动也不影响。工具目录有未提交改动时默认 declare：只交付该提交的内容，
    在消息末尾逐个写明现场改动及其 sha256，另出只读覆盖包（不可安装、不随消息发送）；on_tool_dirty="refuse" 时拒绝。
    所有文件先写临时名，全部成功才改名落地；任何一步失败都不留半成品、不记事件。
    返回值保留原有的键，新增 tool_source、tool_zip_sha256、tool_overlay、tool_overlay_sha256（只增不改）。"""
    if on_tool_dirty not in ("declare", "refuse"):
        raise WfError(f"on_tool_dirty 只能是 declare 或 refuse，不是 {on_tool_dirty!r}")
    if not (inst.root / ".git").exists():
        raise WfError("项目还没有 git 版本库，无法打包。")
    from .release import _dirty
    if _dirty(inst.root, inst.protect()):
        raise WfError("工作区有未提交的改动，打包会遗漏它们。先处理后再生成。")
    home = WF_HOME
    src = fidelity.freeze_tool_source(home)
    if src["dirty"] and on_tool_dirty == "refuse":
        raise WfError("工具目录相对提交 " + str(src["commit"])[:12] + " 有未提交的改动（"
                      + "、".join(e["path"] for e in src["dirty"][:8]) + "），按 on_tool_dirty=refuse 不生成交接材料。")
    pcommit = git(["rev-parse", "--verify", "HEAD^{commit}"], inst.root).stdout.strip()   # 项目也只固定一次
    head = git(["rev-parse", "--short", pcommit], inst.root).stdout.strip()
    ver = inst.version() or "v?"
    inst.outbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    zpath = inst.outbox / f"{inst.name}-{ver}-{head}.zip"
    tool_zip = inst.outbox / _tool_zip_name(src)
    mpath = inst.outbox / f"bootstrap-{stamp}.md"
    opath = (inst.outbox / f"{fidelity.OVERLAY_PREFIX}{stamp}-{src['short'] or 'snapshot'}.zip") if src["dirty"] else None
    pending = []   # [(临时文件, 最终文件)]；全部写好后由 _publish 一次落地
    try:
        ztmp = _tmp_for(zpath)
        pending.append((ztmp, zpath))
        git(["archive", "--format=zip", f"--prefix={inst.name}/", "-o", str(ztmp), pcommit], inst.root)
        ttmp = _tmp_for(tool_zip)
        pending.append((ttmp, tool_zip))
        _write_tool_zip(src, ttmp)
        bad = fidelity.verify_zip_source(ttmp, src)   # r2（R1）：附件与正文、清单所用的冻结来源逐项一致才继续
        if bad:
            raise WfError("工具 zip 与固定来源不一致，未生成交接材料：" + "；".join(bad[:5]))
        tool_sha = hashlib.sha256(ttmp.read_bytes()).hexdigest()
        overlay = None
        if opath is not None:
            data, meta = fidelity.overlay_bytes(src)   # 只用冻结的字节；hash 就是写下去的这份字节的 hash
            otmp = _tmp_for(opath)
            pending.append((otmp, opath))
            otmp.write_bytes(data)
            try:
                os.chmod(otmp, 0o444)   # r2：落地前设只读；设不了时如实记录，不声称只读
                read_only = True
            except OSError:
                read_only = False
            overlay = {"name": opath.name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                       "diff_sha256": meta["diff_sha256"], "installable": False, "read_only": read_only}
        summary = fidelity.source_summary(src)
        summary["head_moved"], summary["head_now"] = False, None
        if src["kind"] == "git":
            now_head = git(["rev-parse", "--verify", "HEAD^{commit}"], home, check=False).stdout.strip()
            if now_head != src["commit"]:
                summary["head_moved"], summary["head_now"] = True, now_head or "（读不到）"
        summary["overlay"] = overlay
        summary["delivery"] = {"tool_zip": "generated", "overlay": "generated_not_attached" if overlay else None,
                               "note": "这里只记生成；是否发送、接收方是否读到，由发送方另行记证"}
        s = machine.status(inst)
        from .ops import fingerprint
        fp = fingerprint(inst.root)
        last = _last_turn_summary(inst)
        tpl = fidelity.source_text(src, "templates/bootstrap.md")
        text = (tpl.replace("{reason}", reason).replace("{zip}", zpath.name).replace("{head}", head)
                .replace("{version}", ver).replace("{tool_zip}", tool_zip.name).replace("{tool_version}", src["version"])
                .replace("{state}", f"{s['state']}（{s['name']}），第 {s['turn']} 轮"
                         + (f"；等待到 {s['until']}：{s['wait_reason'] or ''}" if s.get("until") else ""))
                .replace("{fingerprint}", f"{fp['digest']}（{fp['count']} 个文件）")
                .replace("{last_turn}", last)
                .replace("{workflow_md}", fidelity.source_text(src, "docs/WORKFLOW.md"))
                .replace("{project_md}", (inst.root / "PROJECT.md").read_text(encoding="utf-8"))
                .replace("{tool_manifest}", fidelity.tool_manifest_text(home, inst.root, files=src["files"])))
        text += fidelity.source_declaration(summary, tool_zip.name, overlay)
        mtmp = _tmp_for(mpath)
        pending.append((mtmp, mpath))
        mtmp.write_text(text, encoding="utf-8")
    except BaseException:
        for tmp, _ in pending:
            _unlink(tmp)
        raise
    _publish(pending)   # r2（R4）：后段改名失败时恢复旧的同名文件，不删已有的完整材料
    short, short_why = _bootstrap_short(inst, reason, ver, head, s, fp, last, stamp, tool=summary)   # 1.6.3；1.8.2
    inst.event("bootstrap", file=mpath.name, zip=zpath.name, tool_zip=tool_zip.name, reason=reason,
               short=short.name if short else None, short_skipped=short_why or None, by=by,   # 1.6.4：驱动器生成时记 by=driver
               tool_kind=summary["kind"], tool_commit=summary["commit"], tool_tree=summary["tree"],
               tool_digest=summary["digest"], tool_zip_sha256=tool_sha, tool_dirty=len(summary["dirty"]),
               tool_head_moved=summary["head_moved"], tool_overlay=overlay["name"] if overlay else None,
               tool_overlay_sha256=overlay["sha256"] if overlay else None,
               tool_overlay_read_only=overlay["read_only"] if overlay else None)
    return {"message": mpath, "zip": zpath, "tool_zip": tool_zip, "chars": len(text), "short": short, "short_why": short_why,
            "tool_source": summary, "tool_zip_sha256": tool_sha, "tool_overlay": opath,
            "tool_overlay_sha256": overlay["sha256"] if overlay else None}


def _bootstrap_short(inst: Instance, reason: str, ver: str, head: str, s: dict, fp: dict, last: str, stamp: str,
                     tool: dict = None):
    """1.6.3：项目开启了同步、并在 transport.json 写了仓库时，另外生成一条短的续接消息（WORKFLOW.md 第 10 节路线 G）。
    1.8.2（P0-A、P0-H）：只有这次同步确有提交号（推送成功，或内容与上一次带提交号的推送相同）才生成；链接钉在那个
    提交上、不用分支，读的过程中内容不会变；只链接已推送清单里的文件（第 103 轮的短消息引用了没推上去的 turn-103）。
    同步失败、被拦、没有提交号时不生成，由长消息兜底（路线 Z）。返回 (路径或 None, 没生成的原因或 "")。
    B1-B：tool 是 bootstrap 固定的工具来源摘要；给出时版本取自它，并写明仓库 tool/ 与附件来源的关系。"""
    import re as _re
    from . import goals as _goals, sync, tasks as _tasks
    repo = sync.repo_for(inst)   # 1.7.6：按项目分仓库时用本项目的仓库
    if not repo or not sync.enabled(inst):
        return None, ""
    try:
        res = sync.run(inst)
        if res.get("status") == "unchanged" and not res.get("commit"):
            res = sync.run(inst, force=True)   # 1.8.2 之前的推送记录没有提交号：补推一次，拿到提交号
    except Exception as e:   # noqa: BLE001
        res = {"status": "error", "detail": str(e)[:200]}
    commit = str(res.get("commit") or "")
    if res.get("status") not in ("pushed", "unchanged") or not _re.fullmatch(r"[0-9a-f]{7,40}", commit):
        return None, (f"同步结果 {res.get('status')}" + ("" if commit else "，没有提交号")
                      + (f"：{str(res.get('detail'))[:160]}" if res.get("detail") else ""))
    names = set(res.get("names") or [])
    raw = f"https://raw.githubusercontent.com/{repo}/{commit}"
    turns = sorted(n for n in names if _re.fullmatch(r"turns/turn-\d{3}\.md", n))
    replies = sorted(n for n in names if _re.fullmatch(r"replies/reply-\d{3}\.md", n))
    prog = _goals.progress(inst)
    left = _tasks.open_items(inst)
    conv = (inst.state.get("conv") or {}).get("conv") or "（未登记）"

    def ln(rel):
        return f"{raw}/{rel}" if rel in names else f"（{rel} 不在这次推送里）"
    lines = ["【项目续接 · 新对话】档位：max", "",
             f"上一个对话因 {reason} 结束。项目上下文在公开仓库 {repo}，固定在同步提交 {commit}"
             f"（同步摘要 {res.get('digest')}，{res.get('files')} 个文件）。下面的链接都钉在这个提交上，不读分支：", "",
             f"1. 进度总览：{ln('STATUS.md')}",
             f"2. 项目状态与计划：{ln('project/PROJECT.md')} 、{ln('project/VISION.md')}",
             f"3. 协作规则：{ln('tool/docs/WORKFLOW.md')}（第 10 节：交接路线与新对话首轮的要求）；"
             f"【驱动指令】格式：{ln('tool/docs/DRIVER.md')} 第 5 节",
             "4. 最近一轮：" + (f"我方消息 {raw}/{turns[-1]}" if turns else "我方消息（这次推送里没有）")
             + (f" ；你的回复 {raw}/{replies[-1]}" if replies else ""),
             f"5. 工具源码：{raw}/tool/ 下按路径读取（例如 tool/wflib/driver.py）；版本包在 artifacts/",
             _short_source_line(tool), "",
             f"当前：工具 collab-wf {tool['version'] if tool else tool_version()}，项目 {ver}（提交 {head}），仓库指纹 {fp['digest']}（{fp['count']} 个文件）；"
             f"工作流 {s['state']}（{s['name']}），第 {s['turn']} 轮；本项目登记的对话 {conv}。",
             _goals.phase_title(prog) + (f"，进度 {prog['done']} / {prog['total']}" if prog.get("defined") else "（没有定义目标）")
             + "；未完成的执行端待办：" + ("；".join(f"{t['id']} {t['text'][:40]}" for t in left[:5]) or "无") + "。",
             "失败台账：PROJECT.md 的“失败模式台账”；用户边界：VISION.md 第〇、一节与 PROJECT.md 的“协作约定”。", "",
             "最近一轮的回报清单与验收结论：", last, "",
             "本轮请只做三件事：复述当前版本、阶段、待用户决策与工具边界；按上面的提交复算仓库指纹（ops.fingerprint 的规则）"
             "与同步摘要（sync.digest 的规则），列出读到与缺失的文件；给出下一轮指令。本轮不要交付新版本，也不要进入业务开发"
             "（WORKFLOW.md 第 10 节）。"]
    p = inst.outbox / f"bootstrap-{stamp}-short.md"
    tmp = _tmp_for(p)
    try:
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:   # B1-B：写不下去时不留半截短消息，由长消息兜底
        _unlink(tmp)
        return None, f"短消息写入失败：{e}"
    return p, ""


def _short_source_line(tool) -> str:
    """B1-B：短消息里的来源说明。仓库 tool/ 是同步时的安装现场，不一定等于恢复包附件所取的提交。"""
    if not tool:
        return "工具来源：仓库 tool/ 是同步时的安装现场。"
    if tool["kind"] != "git":
        return (f"工具来源：工具目录没有版本库，恢复包取自一次冻结快照（摘要 {tool['digest']}），未核对提交；"
                "仓库 tool/ 是之后同步时采集的安装现场，未与快照逐文件核对。")
    # r2（独立审查 R5）：冻结时的现场状态只说明冻结那一刻；仓库 tool/ 是之后同步时采集的现场，二者没有逐文件核对，
    # 不能声称“与该提交一致”。HEAD 在生成期间移动时明确写出。
    line = f"工具来源：恢复包附件取自提交 {tool['commit']}（tree {tool['tree']}）的冻结内容"
    if tool["dirty"]:
        line += (f"；冻结时现场相对该提交有 {len(tool['dirty'])} 个未提交改动（"
                 + "、".join(e["path"] for e in tool["dirty"][:6]) + "），未经审查，以提交内容为准")
    else:
        line += "；冻结时现场没有未提交改动"
    if tool.get("head_moved"):
        line += f"；生成期间工具 HEAD 已移动到 {tool.get('head_now')}，仓库 tool/ 很可能已不同于该提交"
    return line + "。仓库 tool/ 是之后同步时采集的安装现场，未与该提交逐文件核对。"


def _tool_zip_name(src: dict) -> str:
    return f"collab-wf-{src['version']}-{src['short']}.zip" if src["kind"] == "git" else f"collab-wf-{src['version']}.zip"


def _write_tool_zip(src: dict, dest: Path) -> None:
    """B1-B：按固定来源写工具 zip。r2（独立审查 R1）：有提交与无提交都直接用冻结的字节写，不再调用 git archive，
    不受当时的 .gitattributes / .git/info/attributes（export-ignore、export-subst 等）影响。"""
    Path(dest).write_bytes(fidelity.source_zip_bytes(src))


def _tool_zip(outdir: Path, src: dict = None) -> Path:
    """把 collab-wf 工具本身打包（已提交的版本；没有版本库时按目录打包），新对话里的 Claude 需要它继续开发工具。
    B1-B：src 是 fidelity.freeze_tool_source 的结果；不给时当场固定（兼容旧调用）。先写临时名再改名，失败不留半成品。"""
    src = src or fidelity.freeze_tool_source(WF_HOME)
    outdir.mkdir(parents=True, exist_ok=True)
    z = outdir / _tool_zip_name(src)
    tmp = _tmp_for(z)
    try:
        _write_tool_zip(src, tmp)
        bad = fidelity.verify_zip_source(tmp, src)
        if bad:
            raise WfError("工具 zip 与固定来源不一致：" + "；".join(bad[:5]))
        os.replace(tmp, z)
    except BaseException:
        _unlink(tmp)
        raise
    return z
