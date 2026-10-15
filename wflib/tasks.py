"""1.6.6：执行端待办（collab-wf-dev 第 58 轮：回复正文里写给执行端的事，执行端做完了却没有接上回报，
流程停在空闲；服务都健康，看门狗也看不见，业务却停了）。

Claude 在【驱动指令】里写“执行端 N：<事项>”。驱动器验收后把它们登记在状态的 executor_tasks 里，每一项都进 inbox
（交给执行端，超过 2 小时转给用户）。执行端做完一项就 wf task done <编号> --note "结果"，结果作为说明写进回报。
“之后：等执行端”或“之后：空闲”而又有未完成的待办时，驱动器停在等待；全部做完（或到了最迟时间）就自动生成回报，
不再需要执行端记得 wf kick。

B2-A1（任务原子代次）：
- 待办的身份是（项目, 编号, 代次）。新登记的每一项带 generation（uuid4 的 32 位十六进制，不复用）、project 与 rev。
  编号 T<轮>-<序> 不是代次：KEEP_DONE 裁掉旧记录后同一编号可能再次登记，旧代次的命令不能命中新记录。
- 对新记录，done / cancel / supersede / progress 必须带当前代次（Python 关键字 generation=，CLI --generation），
  不带、带错、或写 legacy 都拒绝，不会默认绑定最新的一条。expect_rev（CLI --rev）只是同一代次内的附加校验。
- 旧记录（没有 generation 字段）：不带代次或明确写 generation="legacy" 时按旧规则处理；第一次成功修改时就地迁移，
  补上 generation、project、rev 与 migrated_from_legacy，返回的记录里带新代次，之后的命令必须带它。
  带代次去碰旧记录、或不带代次去碰新记录，一律拒绝。
- 所有修改（含 add）都在 state_lock 内重读磁盘上的状态、校验、只改目标那一条，再写回并刷新调用方的内存与基线；
  任何拒绝都不写状态、不记事件。列表损坏（不是列表、缺编号、编号重复、代次或 rev 不合格式、项目不符）时一律拒绝写入。
- 检查点有类型 progress / interrupted / resumed；“被打断”之后要先记 resumed 才能记完成。progress 永远不关闭待办。
- 完成必须写结果（与 1.8.2 相同：非空）；--evidence 可多次：项目内存在的文件（记相对路径与 sha256），或 64 位十六进制摘要。
- 结果、原因、检查点与证据路径里疑似有密钥（ops.SECRET_PATTERNS）时拒绝，不落盘。

B2-FIX-A1（审查返工）：
- 身份再绑定规范化的项目根目录：新记录带 root。复制或移动到别的目录后，旧代次一律拒绝、不写（将来另做显式迁移）。
- 提交只写待办：在锁内用新读的 Instance 写 executor_tasks 与事件，不顺带写调用方内存里其它未保存的字段；
  调用方的待办缓存与三方合并基线就地同步，其它字段与基线原样留给调用方自己保存。
- executor_tasks 缺键按空表兼容，显式 null 或形状不对都拒绝写；写之前先把后续要用的字段（编号、正文、检查点、依赖等）
  全部校验并把备注、事件预先构造好，不会提交了一半再因坏数据报错；列表坏了也能看，但不给可执行命令。
- 证据按解析后的相对路径（实际写入的字段）再查一次密钥。
- 接替者已按代次登记时，--by-generation 必填且必须一致；接替者是旧记录时不带或写 legacy；未登记的保持 null，以后也不解析。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from .core import Instance, WfError, append_jsonl, now_iso

TIMEOUT_S = 14400   # 最迟等 4 小时；policy.json 的 executor_task_timeout_s 可改（时间参数，改动不算扩大权限）
KEEP_DONE = 30      # 只保留最近 30 项已完成的待办
LEGACY = "legacy"   # B2-A1：明确指向旧记录（没有代次）的写法
CHECKPOINT_KINDS = ("progress", "interrupted", "resumed")
_GEN = re.compile(r"[0-9a-f]{32}")
_HEX64 = re.compile(r"[0-9a-fA-F]{64}")


# ---------------- 只读辅助（B2-A1） ----------------

def generation_of(t: dict):
    g = t.get("generation") if isinstance(t, dict) else None
    return g if isinstance(g, str) and _GEN.fullmatch(g) else None


def is_legacy(t: dict) -> bool:
    return isinstance(t, dict) and "generation" not in t


def rev_of(t: dict) -> int:
    r = t.get("rev") if isinstance(t, dict) else None
    return r if isinstance(r, int) and not isinstance(r, bool) and r >= 1 else 0


def interrupted(t: dict) -> bool:
    """最近一个“被打断”检查点之后还没有“已恢复”。旧记录的检查点没有类型，不会被误判为中断。"""
    return bool(isinstance(t, dict) and t.get("interrupted_open"))


def commands(t: dict) -> dict:
    """给执行端照抄的命令。新记录带当前代次；旧记录不带（第一次修改后迁移，之后要用新代次）。"""
    tid = t.get("id")
    ref = f"{tid}" if is_legacy(t) else f"{tid} --generation {t.get('generation')}"
    sup = f'wf task supersede {ref} --reason "原因" --by-task <接替的编号> --by-generation <接替者的代次>'
    st = status_of(t)
    if st == "open":
        c = {"progress": f'wf task progress {ref} --note "做到哪一步、下一步、证据在哪"',
             "interrupted": f'wf task progress {ref} --interrupted --note "被打断时做到哪一步、下一步、证据在哪"',
             "cancel": f'wf task cancel {ref} --reason "原因"', "supersede": sup}
        if interrupted(t):
            c["resumed"] = f'wf task progress {ref} --resumed --note "恢复后做了什么、证据在哪"'
        else:
            c["done"] = f'wf task done {ref} --note "结果"'
        return c
    return {"supersede": sup} if st == "done" else {}


def view(inst: Instance) -> dict:
    """wf task 与 --json 用：按磁盘内容列出，原记录只增不改地加上 legacy、status_effective、commands、depends_legacy。
    列表或状态文件坏了照样列出能列的部分并给出原因，此时不给任何可执行命令。"""
    try:
        disk = _disk_state(inst)
        rows = disk.get("executor_tasks")
        problem = _check_list(rows, inst.name, inst.root) if "executor_tasks" in disk else ""
    except WfError as e:
        rows, problem = None, str(e)
    out = []
    for t in rows if isinstance(rows, list) else []:
        if isinstance(t, dict):
            out.append(dict(t, legacy=is_legacy(t), status_effective=status_of(t), commands={} if problem else commands(t),
                            depends_legacy=bool(t.get("depends_on")) and not isinstance(t.get("depends_on_generation"), dict)))
    return {"project": inst.name, "tasks": out, "problem": problem or None}


# ---------------- 事务（B2-A1） ----------------

def _disk_state(inst: Instance) -> dict:
    p = inst.state_path
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise WfError(f"状态文件读不了或不是合法 JSON（{type(e).__name__}）；没有写入待办")
    if not isinstance(d, dict):
        raise WfError("状态文件顶层不是对象；没有写入待办")
    return d


def _same_root(stored, root: Path) -> bool:
    if not isinstance(stored, str) or not stored:
        return False
    if stored == str(root):
        return True
    try:
        return os.path.samefile(stored, root)
    except OSError:
        return False


_STATUSES = ("done", "cancelled", "superseded")   # 1.8.2 起写入的状态值；更早的记录没有 status


def _checkpoint_problem(c) -> str:
    # B2-FIX2-A1：检查点字段的兼容约定。note 必须是文本；ts、by 有就必须是文本；kind 缺省按 progress
    # （1.9.0 的旧检查点没有 kind，照旧兼容），有就必须是 progress / interrupted / resumed 之一——
    # 显式 null、列表、字典、数字、真假值、未知文本都算损坏
    if not isinstance(c, dict):
        return "不是对象"
    if not isinstance(c.get("note"), str):
        return "缺内容（note）"
    for k in ("ts", "by"):
        if k in c and not isinstance(c[k], str):
            return f"的 {k} 不是文本"
    if "kind" in c and not (isinstance(c["kind"], str) and c["kind"] in CHECKPOINT_KINDS):
        return f"的类型（kind）不合法，只能是 {'、'.join(CHECKPOINT_KINDS)}"
    return ""


def _check_list(tasks, project: str, root: Path) -> str:
    # 写之前校验整张列表，包括后续备注、事件、列表显示要用到的字段；有问题就整体拒绝写
    if not isinstance(tasks, list):
        return "executor_tasks 不是列表"
    seen = set()
    for i, t in enumerate(tasks, 1):
        if not isinstance(t, dict):
            return f"第 {i} 项不是对象"
        tid = t.get("id")
        if not isinstance(tid, str) or not tid.strip():
            return f"第 {i} 项缺编号"
        key = tid.strip().upper()
        if key in seen:
            return f"编号 {tid} 重复"
        seen.add(key)
        if not isinstance(t.get("text"), str):
            return f"{tid} 缺正文"
        for k in ("note", "reason", "superseded_by", "done"):
            if t.get(k) is not None and not isinstance(t.get(k), str):
                return f"{tid} 的 {k} 不是文本"
        if t.get("status") is not None and t.get("status") not in _STATUSES:
            return f"{tid} 的状态（status）不合法"
        if "interrupted_open" in t and not isinstance(t["interrupted_open"], bool):
            return f"{tid} 的 interrupted_open 不是真假值"
        cps = t.get("checkpoints")
        if cps is not None and not isinstance(cps, list):
            return f"{tid} 的检查点不是列表"
        for j, c in enumerate(cps or [], 1):
            bad = _checkpoint_problem(c)
            if bad:
                return f"{tid} 的第 {j} 个检查点{bad}"
        deps = t.get("depends_on")
        if deps is not None and not (isinstance(deps, list) and all(isinstance(d, str) for d in deps)):
            return f"{tid} 的依赖不是编号列表"
        if "generation" in t:
            if generation_of(t) is None:
                return f"{tid} 的代次格式不对"
            if rev_of(t) < 1:
                return f"{tid} 的 rev 不是正整数"
            if t.get("project") != project:
                return f"{tid} 登记在项目 {t.get('project')!r} 下，不是 {project!r}"
            if not _same_root(t.get("root"), root):
                return f"{tid} 登记在另一个项目目录（{t.get('root')}）下，可能是复制或移动来的；为防止串用旧代次，不写入"
    return ""


def _tasks_from(disk: dict, inst: Instance) -> list:
    if "executor_tasks" not in disk:
        return []   # 旧状态没有这个键：按空表兼容；显式 null 或坏形状由下面拒绝
    tasks = disk["executor_tasks"]
    problem = _check_list(tasks, inst.name, inst.root)
    if problem:
        raise WfError(f"待办记录损坏：{problem}；没有写入。请先修复 workflow/state.json 的 executor_tasks")
    return [dict(t) for t in tasks]


def _transaction(inst: Instance, build):
    """在状态锁内：读磁盘、校验、由 build 构造好全部结果（含备注与事件），再用新读的 Instance 只提交待办与事件。
    调用方内存里与待办无关的未保存修改不会被顺带写盘；调用方的待办缓存与基线就地同步到提交后的内容，
    之后调用方 save / event 不会把旧列表盖回来。build 返回 None 表示无需写。"""
    with inst.state_lock():
        out = build(_tasks_from(_disk_state(inst), inst))
        if out is None:
            return None
        fresh = Instance(inst.root)
        if fresh.name != inst.name:
            raise WfError("项目配置在操作期间变了（名称不同）；没有写入待办")
        fresh.state["executor_tasks"] = out["tasks"]
        fresh.save()
        for line in out["notes"]:
            append_jsonl(fresh.turn_dir() / "notes.jsonl", {"ts": now_iso(), "kind": "info", "text": line})
        for typ, fields in out["events"]:
            fresh.event(typ, **fields)
        committed = json.dumps(out["tasks"], ensure_ascii=False)
        inst.state["executor_tasks"] = json.loads(committed)
        if getattr(inst, "_base", None) is not None:
            inst._base["executor_tasks"] = json.loads(committed)
        return out["result"]


def _find(tasks: list, tid: str):
    want = (tid or "").strip().upper()
    return next(((i, t) for i, t in enumerate(tasks) if t["id"].strip().upper() == want), (None, None))


def _resolve(inst: Instance, tasks: list, tid: str, generation, expect_rev):
    idx, t = _find(tasks, tid)
    if t is None:
        ids = "、".join(x["id"] for x in tasks if status_of(x) == "open") or "无"
        raise WfError(f"没有这个待办：{tid}。未完成的待办：{ids}")
    g = generation.strip() if isinstance(generation, str) else generation
    if is_legacy(t):
        if g not in (None, "", LEGACY):
            raise WfError(f"{t['id']} 是旧记录（没有代次），命令里的代次对不上；旧记录不带 --generation，或写 --generation legacy")
    elif g in (None, "", LEGACY):
        raise WfError(f"{t['id']} 已按代次登记：必须带它现在的代次（wf task 列出），不会默认绑定最新的一条")
    elif g != t["generation"]:
        raise WfError(f"{t['id']} 的代次对不上：命令里的代次不是这条待办现在的代次（可能是旧代次的待办被裁掉后同编号重新登记）；"
                      "先 wf task 查看")
    if expect_rev is not None and expect_rev != rev_of(t):
        raise WfError(f"{t['id']} 已经更新：当前 rev {rev_of(t)}，命令要求 rev {expect_rev}；先 wf task 查看")
    return idx, t


def _migrate(nt: dict, inst: Instance) -> bool:
    # 旧记录第一次成功修改：就地补上代次与目录身份，返回 True；之后的命令必须带这个代次
    if not is_legacy(nt):
        return False
    nt.update(generation=uuid.uuid4().hex, project=inst.name, root=str(inst.root), migrated_from_legacy=now_iso())
    return True


def _no_secret(text, what: str) -> None:
    from .ops import SECRET_PATTERNS   # 只读引用（ops 冻结），延迟导入避免循环
    for line in str(text or "").splitlines():
        for pat, why in SECRET_PATTERNS:
            for m in re.finditer(pat, line):
                if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", m.group(0)) and why.startswith("64"):
                    continue   # 与 check_outgoing 相同：git 提交号、sha256 摘要不是密钥
                raise WfError(f"{what}里疑似有密钥（{why}），没有写入；去掉后重试（CP-1）")


def _evidence(inst: Instance, items) -> list:
    out = []
    for raw in items or []:
        s = str(raw).strip()
        if not s:
            raise WfError("证据不能为空")
        if _HEX64.fullmatch(s):
            out.append({"sha256": s.lower()})
            continue
        _no_secret(s, "证据")
        p = Path(s).expanduser()
        p = p if p.is_absolute() else inst.root / p
        try:
            rp = p.resolve(strict=True)
            rel = rp.relative_to(inst.root)
        except (OSError, RuntimeError):
            raise WfError(f"证据不存在：{s}")
        except ValueError:
            raise WfError(f"证据不在项目内：{s}")
        if not rp.is_file():
            raise WfError(f"证据不是文件：{s}")
        ref = str(rel).replace(os.sep, "/")
        _no_secret(ref, "证据（解析后的路径）")   # 别名或链接指向的真实文件名也要查：写进状态的是它
        h = hashlib.sha256()
        with open(rp, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        out.append({"ref": ref, "sha256": h.hexdigest()})
    return out


# ---------------- 登记与修改 ----------------

def add(inst: Instance, turn: int, items: dict) -> list:
    """登记本轮的执行端待办（编号 T<轮次>-<序号>）；同一项不重复登记，已有记录不覆盖。返回新登记的。
    B2-A1：在状态锁内按磁盘最新内容合并；新记录带 generation、project、root、rev=1；依赖按当时的代次绑定。"""
    if not items:
        return []
    keys = sorted(items, key=lambda x: int(x))

    def build(tasks):
        have = {t["id"].strip().upper() for t in tasks}
        added = []
        for n in keys:
            tid = f"T{turn}-{n}"
            if tid.upper() in have:
                continue
            t = {"id": tid, "turn": turn, "text": str(items[n])[:500], "since": now_iso(), "done": None, "note": "",
                 "generation": uuid.uuid4().hex, "project": inst.name, "root": str(inst.root), "rev": 1}
            _markers(t, turn)   # 1.9.1（W91）：（不阻塞）、（依赖 M）
            tasks.append(t)
            added.append(t)
            have.add(tid.upper())
        index = {t["id"].strip().upper(): t for t in tasks}
        for t in added:
            if t.get("depends_on"):   # 依赖按登记时的代次绑定；指向旧记录或尚未登记的记为 null
                t["depends_on_generation"] = {d: generation_of(index.get(d.upper(), {})) for d in t["depends_on"]}
        closed = [t for t in tasks if status_of(t) != "open"]   # 1.8.2：已完成、已取消、已取代都算已关闭
        drop = {t["id"] for t in closed[:max(0, len(closed) - KEEP_DONE)]}
        if not added and not drop:
            return None
        events = [("tasks_opened", {"ids": [t["id"] for t in added], "generations": [t["generation"] for t in added],
                                    "by": "driver"})] if added else []
        return {"result": added, "tasks": [t for t in tasks if t["id"] not in drop], "notes": [], "events": events}
    return _transaction(inst, build) or []


STATUS_CN = {"open": "未完成", "done": "完成", "cancelled": "取消", "superseded": "被取代"}


def status_of(t: dict) -> str:
    """1.8.2：待办的状态 open / done / cancelled / superseded；1.8.2 之前的记录只有 done 字段。"""
    s = t.get("status")
    if s in ("cancelled", "superseded"):
        return s
    return "done" if t.get("done") else "open"


def open_items(inst: Instance) -> list:
    return [t for t in (inst.state.get("executor_tasks") or []) if status_of(t) == "open"]


def done(inst: Instance, tid: str, note: str = "", *, generation=None, evidence=None, expect_rev=None,
         by: str = "executor") -> dict:
    """执行端完成一项待办：记 by=executor 的事件，并把结果作为说明写进当前轮的备注（随下一份回报发出）。
    B2-A1：新记录必须带当前代次；“被打断”而没有恢复记录时不能记完成；证据可选，路径须在项目内。"""
    note = (note or "").strip()
    if not note:   # 1.8.2（P0-B）：完成必须写结果，不再出现“没有写说明”的完成
        raise WfError("记为完成必须写结果：wf task done <编号> --generation <代次> --note \"结果\"；"
                      "不再需要的待办用 wf task cancel 或 wf task supersede")
    _no_secret(note, "结果")
    ev = _evidence(inst, evidence)

    def build(tasks):
        i, t = _resolve(inst, tasks, tid, generation, expect_rev)
        st = status_of(t)
        if st in ("cancelled", "superseded"):
            raise WfError(f"{t['id']} 已{STATUS_CN[st]}（{t.get('reason') or '没有写原因'}），不能再记为完成"
                          + (f"；接替它的是 {t['superseded_by']}" if t.get("superseded_by") else ""))
        if t.get("done"):
            raise WfError(f"{t['id']} 已在 {t['done']} 完成")
        if interrupted(t):
            raise WfError(f"{t['id']} 最近的检查点是“被打断”，还没有恢复记录：先 wf task progress {t['id']} --generation <代次> "
                          "--resumed --note \"恢复后做了什么、证据在哪\"，再记完成")
        nt = dict(t)
        migrated = _migrate(nt, inst)
        nt.update(done=now_iso(), note=note[:1000], status="done", rev=rev_of(t) + 1)
        if ev:
            nt["evidence"] = ev
        tasks[i] = nt
        return {"result": nt, "tasks": tasks,
                "notes": [f"执行端待办 {nt['id']} 已完成（{nt['text'][:80]}）：{nt['note']}"],
                "events": [("task_done", {"id": nt["id"], "note": nt["note"][:300], "generation": nt["generation"],
                                          "rev": nt["rev"], "evidence": len(ev) or None, "migrated": migrated or None,
                                          "by": by})]}
    return _transaction(inst, build)


def close(inst: Instance, tid: str, kind: str, reason: str, by: str = "executor", by_task: str = "", *,
          generation=None, by_generation=None, expect_rev=None) -> dict:
    """1.8.2（P0-B、P1-N）：不再需要的待办如实关闭——cancelled（取消）或 superseded（被取代），必须写原因，
    不再用 done 冒充。已经记为 done 的待办只能改记为 superseded（做过、但已被新的边界或新的待办取代）：
    原来的完成时刻与说明保留在 was_done 里，不抹掉历史。
    B2-A1：新记录必须带当前代次。接替者已按代次登记时 by_generation 必填且一致；接替者是旧记录时不带或写 legacy；
    接替者尚未登记时记为 null（superseded_by_ref=unregistered），以后也不解析。取消不接受接替参数。"""
    if kind not in ("cancelled", "superseded"):
        raise WfError("只能记为 cancelled 或 superseded")
    reason = (reason or "").strip()
    if len(reason) < 2:
        raise WfError(f"{'取消' if kind == 'cancelled' else '记为被取代'}必须写原因：--reason \"原因\"")
    _no_secret(reason, "原因")
    succ_id = str(by_task).strip().upper()[:40] if by_task else ""
    bg = by_generation.strip() if isinstance(by_generation, str) else by_generation
    if kind == "cancelled" and (succ_id or bg):
        raise WfError("取消不接受 --by-task / --by-generation；被新的待办接替请用 wf task supersede")
    if bg and not succ_id:
        raise WfError("--by-generation 要和 --by-task 一起用")
    _no_secret(succ_id, "接替编号")

    def build(tasks):
        i, t = _resolve(inst, tasks, tid, generation, expect_rev)
        cur = status_of(t)
        if cur in ("cancelled", "superseded"):
            raise WfError(f"{t['id']} 已{STATUS_CN[cur]}（{t.get('reason') or ''}）")
        if cur == "done" and kind == "cancelled":
            raise WfError(f"{t['id']} 已在 {t['done']} 记为完成，不能取消；它若已被新的边界或待办取代，用 wf task supersede {t['id']} --reason …")
        succ_gen, succ_ref = None, None
        if succ_id:
            _, succ = _find(tasks, succ_id)
            if succ is None:
                if bg not in (None, ""):
                    raise WfError(f"接替的待办 {succ_id} 还没有登记，无法核对 --by-generation")
                succ_ref = "unregistered"
            elif is_legacy(succ):
                if bg not in (None, "", LEGACY):
                    raise WfError(f"接替的 {succ['id']} 是旧记录（没有代次）：--by-generation 只能不写或写 legacy")
                succ_ref = "legacy"
            elif bg in (None, "", LEGACY):
                raise WfError(f"接替的 {succ['id']} 已按代次登记：必须用 --by-generation 写明它现在的代次（wf task 列出），"
                              "不会默认绑定最新的一条")
            elif bg != succ["generation"]:
                raise WfError(f"接替的 {succ['id']} 的代次对不上：可能是它被裁掉后同编号重新登记；先 wf task 查看")
            else:
                succ_gen, succ_ref = succ["generation"], "generation"
        nt = dict(t)
        migrated = _migrate(nt, inst)
        if cur == "done":
            nt["was_done"] = {"done": t.get("done"), "note": t.get("note", "")}
            nt["done"] = None
        nt.update(status=kind, closed=now_iso(), reason=reason[:500], closed_by=by, rev=rev_of(t) + 1)
        if succ_id:
            nt.update(superseded_by=succ_id, superseded_by_generation=succ_gen, superseded_by_ref=succ_ref)
        tasks[i] = nt
        line = (f"执行端待办 {nt['id']} 已{STATUS_CN[kind]}（{nt['text'][:80]}）：{reason[:300]}"
                + (f"；原先在 {nt['was_done']['done']} 记为完成" if cur == "done" else "")
                + (f"；取代它的是 {nt['superseded_by']}" if nt.get("superseded_by") else ""))
        return {"result": nt, "tasks": tasks, "notes": [line],
                "events": [("task_" + kind, {"id": nt["id"], "reason": reason[:300], "was_done": True if cur == "done" else None,
                                             "superseded_by": nt.get("superseded_by"),
                                             "superseded_by_generation": nt.get("superseded_by_generation"),
                                             "generation": nt["generation"], "rev": nt["rev"], "migrated": migrated or None,
                                             "by": by})]}
    return _transaction(inst, build)


def progress(inst: Instance, tid: str, note: str, by: str = "executor", *, generation=None, kind: str = "progress",
             expect_rev=None) -> dict:
    # 1.9.0（W88）：执行端做到一半被打断（额度、模型、会话）时留下检查点：做到哪一步、下一步、证据在哪。不关闭待办。
    # B2-A1：检查点分 progress / interrupted / resumed；progress 永远不记完成。
    note = (note or "").strip()
    if not note:
        raise WfError("检查点要写内容：wf task progress <编号> --generation <代次> --note \"做到哪一步、下一步、证据在哪\"")
    if kind not in CHECKPOINT_KINDS:
        raise WfError(f"检查点类型只能是 {'、'.join(CHECKPOINT_KINDS)}")
    _no_secret(note, "检查点")

    def build(tasks):
        i, t = _resolve(inst, tasks, tid, generation, expect_rev)
        if status_of(t) != "open":
            raise WfError(f"{t['id']} 已关闭（{STATUS_CN[status_of(t)]}），不再记检查点")
        nt = dict(t)
        migrated = _migrate(nt, inst)
        cps = list(t.get("checkpoints") or [])
        cps.append({"ts": now_iso(), "note": note[:500], "by": by, "kind": kind})
        nt["checkpoints"] = cps[-20:]
        if kind != "progress":
            nt["interrupted_open"] = kind == "interrupted"
        nt["rev"] = rev_of(t) + 1
        tasks[i] = nt
        return {"result": nt, "tasks": tasks, "notes": [],
                "events": [("task_progress", {"id": nt["id"], "note": note[:200], "kind": kind, "generation": nt["generation"],
                                              "rev": nt["rev"], "migrated": migrated or None, "by": by})]}
    return _transaction(inst, build)


def _markers(t: dict, turn: int) -> None:
    # 1.9.1（W91）：待办正文开头可以写“（不阻塞）”与“（依赖 1、2）”。不阻塞的待办不让驱动器等它（持续性的义务不要写成
    # 要等它做完的待办——第 120 轮的 T120-2 要等未来 3 轮才能完成，驱动器却等它关闭才发下一轮，互相卡死）；
    # 依赖写成明确的字段 depends_on，唤醒程序据此判断先后，不再从说明文字里猜（第 121 轮执行端修过的误判）。
    import re
    txt = t["text"]
    while True:
        m = re.match(r"^\s*[（(]\s*(不阻塞|依赖\s*([0-9、,，\s]+))\s*[）)]\s*", txt)
        if not m:
            break
        if m.group(1) == "不阻塞":
            t["nonblocking"] = True
        else:
            t["depends_on"] = [f"T{turn}-{k}" for k in re.findall(r"\d+", m.group(2))]
        txt = txt[m.end():]
    t["text"] = txt


def blocking_items(inst: Instance) -> list:
    # 1.9.1（W91）：驱动器“等执行端”只等这些；不阻塞的待办照样进 inbox、照样要关闭，但不挡下一轮
    return [t for t in open_items(inst) if not t.get("nonblocking")]
