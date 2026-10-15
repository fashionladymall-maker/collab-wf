"""1.5.0 可靠性补强：回报保真、解析容错、回复归属、执行接续、额度空值原因、事件序号、升级后核验、无附件续接。

设计：这里只放纯函数和只读本地文件的函数，便于单测；驱动器、回报、体检与升级只在调用点接入。
只用标准库，兼容 Python 3.9+。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat as _stat
import time
from pathlib import Path

REPORT_BUDGET = 32000   # 一份回报中嵌入原文的字符预算（整份回报留出余量，发送前检查在 40000 字符时告警）
CHUNK = 30000           # 超出预算的原文按此大小分片，排入续传队列
USAGE_MAX_AGE_S = 600   # 额度数据的有效期，与 Instance.usage_snapshot 的默认值一致
CARRY_FILE = "carry.json"


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _read(p: Path) -> str:
    try:
        return Path(p).read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, IsADirectoryError):
        return ""


def _read_json(p: Path):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, IsADirectoryError):
        return None


def _write_json(p: Path, obj) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


# ---------------- 回复解析的容错 ----------------

def split_blocks(text: str, names) -> tuple:
    """把回复切成固定块。

    只承认位于行首的块标题（前面可以有空白、#、*、>、-），同一个块名出现多次时，按约定顺序挑选：
    依次为每个块找“在上一个块之后最早出现”的标题。这样正文里顺带提到的块名不会把真正的块顶掉
    （1.4.x 以最后一次出现为准，正文中提到块名就可能让整个命令块被一段闲话替换）。
    某个块名在行首一次都没出现、只在行中出现时，退回旧规则把行中出现也算作候选，并给出提示。
    返回 (blocks, warnings)。
    """
    names = list(names)
    alt = "|".join(re.escape(n) for n in names)
    line_pat = re.compile(r"^[ \t>#*\-•]*【(" + alt + r")】[ \t*]*", re.M)
    cands = {}
    for m in line_pat.finditer(text):
        cands.setdefault(m.group(1), []).append(m)
    warnings = []
    missing_line = [n for n in names if n not in cands and f"【{n}】" in text]
    if missing_line:
        inline_pat = re.compile(r"【(" + "|".join(re.escape(n) for n in missing_line) + r")】")
        for m in inline_pat.finditer(text):
            cands.setdefault(m.group(1), []).append(m)
        for n in missing_line:
            warnings.append(f"块标题「{n}」不在行首，按旧规则识别；请让块标题独占一行的开头")
    chosen, pos = {}, -1
    for n in names:
        nxt = next((m for m in cands.get(n, []) if m.start() > pos), None)
        if nxt is not None:
            chosen[n] = nxt
            pos = nxt.start()
    for n in names:
        if n not in chosen and cands.get(n):
            chosen[n] = cands[n][0]
            warnings.append(f"块「{n}」的位置与约定顺序不同")
    for n, ms in cands.items():
        extra = [m for m in ms if m is not chosen.get(n)]
        if extra:
            lines = [str(text.count("\n", 0, m.start()) + 1) for m in extra]
            warnings.append(f"块标题「{n}」在第 {'、'.join(lines[:5])} 行另有出现，已忽略")
    marks = sorted(chosen.values(), key=lambda m: m.start())
    blocks = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        blocks[m.group(1)] = text[m.end():end].strip()
    return blocks, warnings


def list_warnings(block: str, label: str) -> list:
    """编号列表的项号应当是 1..N。不连续通常意味着嵌套编号被当成了清单项，或抓取时丢了项。"""
    nums = [int(m.group(1)) for m in re.finditer(r"^\s*(\d+)[.、)]\s*(.+)$", block or "", re.M)]
    if nums and nums != list(range(1, len(nums) + 1)):
        return [f"「{label}」的编号不连续：{nums[:20]}（可能有嵌套编号或缺项，请核对项数）"]
    return []


def directive_line(raw: str) -> str:
    """驱动指令行的规范化：去掉列表符号与编号（“1.”“-”“(1)”）、加粗与反引号，以及“本轮之后”的“本轮”。"""
    s = raw.strip().replace("**", "").replace("`", "")
    s = re.sub(r"^(?:[-•*]\s*|\d+\s*[.、)]\s*|[(（]\d+[)）]\s*)+", "", s)
    s = re.sub(r"^本轮(?=之后)", "", s)
    return s.strip()


# ---------------- 证据关联与回报原文 ----------------

def _by_label(execs, n: int):
    """cN 指【本机执行】中第 N 条命令（label #N）。只要有任何一条带 label（驱动器执行），就只按 label 对应：
    第 44 轮第 1 条被跳过时，按证据编号回退会把 c01（其实是第 2 条）错配给清单第 1 项。"""
    for e in execs:
        if e.get("label") == f"#{n}":
            return e
    if any(e.get("label") for e in execs):
        return None
    cid = f"c{n:02d}"
    return next((e for e in execs if e.get("id") == cid), None)


def link_evidence(n: int, text: str, execs: list, n_items: int) -> list:
    """为没有证据的清单项找证据，返回证据编号列表。

    依次尝试：① 文字中的 cN（N 指【本机执行】中的第 N 条命令）；② 反引号中的命令与已执行命令逐字相同，
    或用 ... 省略中段（如 `cat .../ops.py`）；③ 清单项数与命令数相同时按序号一一对应。
    """
    out = []

    def add(e):
        if e is not None and e.get("id") and e["id"] not in out:
            out.append(e["id"])

    text = text or ""
    for m in re.finditer(r"(?<![A-Za-z0-9_])c(\d{1,3})(?![A-Za-z0-9_])", text):
        add(_by_label(execs, int(m.group(1))))
    if out:
        return out
    for m in re.finditer(r"`([^`\n]+)`", text):
        frag = m.group(1).strip()
        for e in execs:
            cmd = (e.get("cmd") or "").strip()
            if not cmd or not frag:
                continue
            if cmd == frag:
                add(e)
                continue
            parts = re.split(r"\.\.\.|…", frag)
            if len(parts) == 2 and parts[0].strip() and cmd.startswith(parts[0]) and cmd.endswith(parts[1]):
                add(e)
    if out:
        return out
    if n_items and n_items == len(execs) and 1 <= n <= len(execs):
        add(execs[n - 1])
    return out


def fence_for(body: str) -> str:
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", body)), default=0)
    return "`" * max(3, longest + 1)


def evidence_body(d: Path, e: dict) -> str:
    cid = e["id"]
    out = _read(Path(d) / f"{cid}.out").rstrip("\n")
    err = _read(Path(d) / f"{cid}.err").rstrip("\n")
    parts = [f"$ {e.get('cmd', '')}"]
    if out:
        parts.append(out)
    if err.strip():
        parts.append("[stderr]\n" + err)
    parts.append(f"exit={e.get('exit_code')}  耗时 {e.get('duration_s')}s")
    return "\n".join(parts)


def split_chunks(s: str, size: int = CHUNK) -> list:
    """按行切片，每片不超过 size 字符；超长的单行硬切。拼接全部分片等于原文。"""
    chunks, cur = [], ""
    for line in s.splitlines(keepends=True):
        while len(line) > size:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:size])
            line = line[size:]
        if cur and len(cur) + len(line) > size:
            chunks.append(cur)
            cur = ""
        cur += line
    if cur or not chunks:
        chunks.append(cur)
    return chunks


def load_carry(p: Path) -> list:
    q = _read_json(p)
    return q if isinstance(q, list) else []


def report_evidence(items: list, execs: list, d: Path, carry_path: Path, verdicts: dict, turn: int,
                    budget: int = REPORT_BUDGET) -> list:
    """生成回报 ② 段：清单项、关联的原文、未关联命令的原文、续传。返回行列表。

    原文完整嵌入，不截尾；整份回报的原文受 budget 约束，放不下的部分分片排入续传队列
    （workflow/outbox/carry.json），下一份回报自动先补发。每段原文只出现一次。
    """
    execs = [e for e in execs if isinstance(e, dict) and e.get("id")]
    L, embedded, queue = [], set(), []
    left = [int(budget)]
    by_id = {e["id"]: e for e in execs}

    def put(block: str) -> bool:
        if len(block) <= left[0]:
            L.append(block)
            left[0] -= len(block)
            return True
        return False

    def emit(cid: str, title: str):
        if cid in embedded:
            L.append(f"（{cid} 的原文已在上文给出）")
            return
        e = by_id.get(cid)
        if not e:
            L.append(f"（证据 {cid} 不存在）")
            return
        embedded.add(cid)
        body = evidence_body(d, e)
        f = fence_for(body)
        if put(f"{f}\n{body}\n{f}"):
            return
        chunks = split_chunks(body)
        digest = sha256_text(body)[:12]
        start = 0
        if len(chunks) > 1:
            first = chunks[0].rstrip("\n")
            ff = fence_for(first)
            note = f"（{cid} 原文共 {len(body)} 字符，分 {len(chunks)} 片，全文 sha256 前 12 位 {digest}；以下是第 1 片，其余排入续传）"
            if put(note + f"\n{ff}\n{first}\n{ff}"):
                start = 1
        if start == 0:
            L.append(f"（{cid} 原文共 {len(body)} 字符，全文 sha256 前 12 位 {digest}，超出本条回报的预算，"
                     f"已分 {len(chunks)} 片排入续传）")
        for k in range(start, len(chunks)):
            queue.append({"turn": turn, "cid": cid, "title": title, "part": k + 1, "parts": len(chunks),
                          "sha": digest, "text": chunks[k]})

    old = load_carry(carry_path)
    keep = []
    if old:
        # 1.5.2（W20）：续传先于本轮原文嵌入（文档写的是“下一份回报自动先补发”）；一片都放不下时不留空标题
        L.append("\n续传：之前的回报未能容纳的原文")
        mark = len(L)
        for q in old:
            body = (q.get("text") or "").rstrip("\n")
            f = fence_for(body)
            block = (f"（第 {q.get('turn')} 轮 {q.get('cid')}，{q.get('title', '')}，第 {q.get('part')}/{q.get('parts')} 片，"
                     f"全文 sha256 前 12 位 {q.get('sha')}）\n{f}\n{body}\n{f}")
            if keep or not put(block):
                keep.append(q)
        if len(L) == mark:
            L.pop()
    n_items = len(items)
    for i in items:
        verdict = verdicts.get(i.get("verdict"), "未验收")
        L.append(f"\n{i['n']}) {i['item']} —— {verdict}" + (f"：{i['note']}" if i.get("note") else ""))
        ev = [c.strip() for c in (i.get("evidence") or "").split(",") if c.strip()]
        if not ev:
            ev = link_evidence(i["n"], i.get("item", ""), execs, n_items)
        for cid in ev:
            emit(cid, f"清单第 {i['n']} 项")
    rest = [e for e in execs if e.get("id") and e["id"] not in embedded]
    if rest:
        L.append("\n其他命令（未关联到清单项）：")
        for e in rest:
            L.append(f"- {e['id']} exit={e.get('exit_code')} `{e.get('cmd')}`")
        for e in rest:
            emit(e["id"], "其他命令")
    pending = keep + queue
    if pending or old:
        _write_json(carry_path, pending)
    if pending:
        L.append(f"\n（续传队列中还有 {len(pending)} 片原文，会在后续回报中自动先发送；"
                 "需要尽快拿到时，安排一轮不执行命令的回报即可）")
    return L


def attention_extra(inst, turn) -> list:
    """回报“需要注意”的补充项：回复解析提示与驱动指令解析问题（只在有问题时出现）。"""
    out = []
    it = _read_json(Path(inst.evidence) / f"turn-{int(turn):03d}" / "intake.json") or {}
    for w in it.get("warnings") or []:
        out.append(f"回复解析提示：{w}")
    for e in (it.get("directives") or {}).get("errors") or []:
        out.append(f"驱动指令解析问题：{e}")
    return out


# ---------------- 回复归属与执行接续 ----------------

def reply_attribution(data: dict, send_ref) -> tuple:
    """核对轮询取回的回复是否回应本轮发送的消息。返回 (ok|stale|unverified, 说明)。

    适配器在 status=done 时返回 reply_parent（这条回复所回应的用户消息的 ref）。与本轮 send_ref 不同，
    说明取回的是对话里已有的旧回复（第 30 轮实际发生过）。不提供 reply_parent 时无法核实，行为不变。
    """
    parent = (data or {}).get("reply_parent")
    if parent in (None, ""):
        return "unverified", "适配器没有返回 reply_parent，无法核实这条回复回应的是本轮消息"
    if send_ref not in (None, "") and str(parent) != str(send_ref):
        return "stale", f"回复的父消息是 {parent}，不是本轮发送的 {send_ref}"
    return "ok", ""


def exec_completed(execs: list, n: int):
    """第 n 条命令（label #n）是否已经完整执行（证据里有退出码与结束时间）。是则返回该证据。"""
    for e in reversed(execs):
        if e.get("label") == f"#{n}" and e.get("exit_code") is not None and e.get("end"):
            return e
    return None


def send_unknown_kind(pol: dict) -> str:
    """发送结果不明时的处置类别：policy 的 send_unknown_wait 为真时先自动等待并反复确认（约一小时），否则照旧阻塞。"""
    return "send_outcome_unknown_wait" if (pol or {}).get("send_unknown_wait") else "send_outcome_unknown"


def run_with_grace(cmd, timeout, env=None, grace=15):
    """运行适配器：超时先 SIGTERM，宽限 grace 秒后才 SIGKILL。
    1.4.x 用 subprocess.run(timeout=…)，到时直接 SIGKILL，适配器的信号处理与 atexit 都来不及补写日志（第 40 轮）。
    返回 (退出码, stdout, stderr, 是否超时)。"""
    import subprocess
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True, env=env)
    try:
        out, err = p.communicate(timeout=timeout)
        return p.returncode, out or "", err or "", False
    except subprocess.TimeoutExpired:
        p.terminate()
        try:
            out, err = p.communicate(timeout=grace)
        except subprocess.TimeoutExpired:
            p.kill()
            out, err = p.communicate()
        return p.returncode, out or "", err or "", True


# ---------------- 额度、事件序号、体检 ----------------

def usage_reason(p: Path, max_age: int = USAGE_MAX_AGE_S) -> str:
    p = Path(p)
    if not p.exists():
        return "usage.json 不存在（桥接没有写入额度）"
    age = time.time() - p.stat().st_mtime
    if age > max_age:
        return f"usage.json 已 {int(age // 60)} 分钟未更新（超过 {max_age // 60} 分钟视为不可信，只记实测值）"
    return "usage.json 中没有可识别的额度字段"


def _jsonl(p: Path) -> tuple:
    rows, bad = [], 0
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    bad += 1
    except FileNotFoundError:
        pass
    return rows, bad


def usage_null_streak(turns_path: Path) -> int:
    rows, _ = _jsonl(turns_path)
    k = 0
    for r in reversed(rows):
        key = "usage_before" if "usage_before" in r else ("usage_after" if "usage_after" in r else None)
        if key is None:
            continue
        if r.get(key):
            break
        k += 1
    return k


def seq_check(events_path: Path) -> dict:
    rows, bad = _jsonl(events_path)
    seqs = [r.get("seq") for r in rows if isinstance(r.get("seq"), int)]
    pairs = list(zip(seqs, seqs[1:]))
    return {"count": len(seqs), "dup": len(seqs) - len(set(seqs)), "backwards": sum(1 for a, b in pairs if b < a),
            "gaps": sum(1 for a, b in pairs if b > a + 1), "bad_lines": bad}


def unverified_replies(events_path: Path, last: int = 10) -> int:
    rows, _ = _jsonl(events_path)
    marks = [r for r in rows if r.get("type") in ("reply_unverified", "reply_checked")]
    return sum(1 for r in marks[-last:] if r.get("type") == "reply_unverified")


def doctor_extra(inst) -> list:
    """补充体检项：[(级别, 项目, 说明, 建议)]。"""
    out = []
    p = Path(inst.usage_path)
    if p.exists():
        age = time.time() - p.stat().st_mtime
        ok = age <= USAGE_MAX_AGE_S
        out.append(("ok" if ok else "warn", "额度数据", f"{int(age // 60)} 分钟前更新",
                    "" if ok else f"超过 {USAGE_MAX_AGE_S // 60} 分钟未更新：轮次记录中的额度会是空值（只记实测值）；"
                                  "请让桥接在每次查询额度后写入 usage.json"))
    else:
        out.append(("warn", "额度数据", "没有 usage.json", "桥接没有写入额度：轮次记录中的额度全部为空值，档位统计无法进行"))
    k = usage_null_streak(inst.turns_path)
    if k >= 3:
        out.append(("warn", "额度记录", f"最近 {k} 条轮次记录的额度为空", "先修好额度采集，档位与消耗的统计才有意义"))
    s = seq_check(inst.events_path)
    if s["dup"] or s["backwards"] or s["bad_lines"]:
        out.append(("warn", "事件序号", f"共 {s['count']} 条：重复 {s['dup']} 个、倒序 {s['backwards']} 处、无法解析 {s['bad_lines']} 行",
                    "进程曾在“写事件”与“写状态”之间被打断；1.5.0 起先写状态再追加事件，历史记录不改"))
    elif s["count"]:
        out.append(("ok", "事件序号", f"{s['count']} 条，无重复" + (f"（{s['gaps']} 处跳号）" if s["gaps"] else ""), ""))
    u = unverified_replies(inst.events_path)
    if u:
        out.append(("warn", "回复归属", f"最近 {u} 轮的回复没有核实归属（适配器未返回 reply_parent）",
                    "按 TRANSPORT.md 末节让适配器在 poll 完成时返回 reply_parent"))
    q = load_carry(Path(inst.outbox) / CARRY_FILE)
    if q:
        out.append(("warn", "续传队列", f"{len(q)} 片原文待发", "下一份回报会自动先发送"))
    return out


# ---------------- 升级后核验 ----------------

def tree_hashes(root: Path) -> dict:
    """与 release._tool_files 相同的口径（排除 .git、local、__pycache__、*.pyc、MANIFEST.json），另排除 RELEASE.json。"""
    root = Path(root)
    out = {}
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(root)
        if set(rel.parts) & {".git", "local", "__pycache__"} or f.suffix == ".pyc":
            continue
        r = str(rel).replace(os.sep, "/")
        if r in ("MANIFEST.json", "RELEASE.json"):
            continue
        out[r] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def post_install_receipt(new: str, src_files: dict, installed: dict, board_was_running: bool, board_info,
                         wf_home: Path, wait_s: float = 20, sleep=time.sleep) -> dict:
    """升级落地后的核验回执：磁盘版本、文件与版本包一致、看板以新版本运行（升级前在运行时）。"""
    problems, notes = [], []
    disk = _read(Path(wf_home) / "VERSION").strip()
    if disk != new:
        problems.append(f"磁盘上的 VERSION 是 {disk or '空'}，不是 {new}")
    changed = sorted(k for k, h in src_files.items() if installed.get(k) != h)
    if changed:
        problems.append(f"{len(changed)} 个文件与版本包不一致：{', '.join(changed[:8])}")
    extra = sorted(set(installed) - set(src_files))
    if extra:
        notes.append(f"工具目录中有版本包之外的文件（旧版本残留或放错位置，未删除）：{', '.join(extra[:8])}")
    board = None
    if board_was_running:
        deadline = time.time() + wait_s
        while True:
            board = board_info() or {}
            if board.get("running") and str(board.get("version")) == new and not board.get("stale"):
                break
            if time.time() >= deadline:
                problems.append("看板没有以新版本运行（" + "，".join(f"{k}={board.get(k)}" for k in ("running", "pid", "version", "stale")) + "）")
                break
            sleep(1)
    return {"ok": not problems, "version": disk, "problems": problems, "notes": notes, "board": board,
            "files_checked": len(src_files)}


# ---------------- 无附件续接 ----------------

def tool_manifest_text(wf_home: Path, project_root=None, *, ref=None, files=None) -> str:
    """新对话恢复包附带的文件清单：通道不能上传附件时，Claude 据此点名要读的文件。
    B1-B：ref 给出提交时按该提交的内容列（不读工作区）；files 给出冻结的 {路径: (权限, 字节)} 时按冻结内容列。
    两者都不给时与此前完全相同（读工作区）。口径与 tree_hashes 相同。"""
    lines = ["工具文件（路径、字节数、sha256 前 12 位）："]
    if ref is not None and files is None:
        files = commit_files(wf_home, ref)
    if files is not None:
        for rel in sorted(files):
            if _manifest_rel(rel):
                data = files[rel][1]
                lines.append(f"- {rel}  {len(data)}  {hashlib.sha256(data).hexdigest()[:12]}")
    else:
        for rel, h in tree_hashes(wf_home).items():
            lines.append(f"- {rel}  {(Path(wf_home) / rel).stat().st_size}  {h[:12]}")
    if project_root is not None:
        try:
            from .ops import fingerprint
            fp = fingerprint(project_root)
            lines += ["", f"项目文件（指纹 {fp['digest']}，{fp['count']} 个；路径、sha256 前 12 位）："]
            lines += [f"- {x['path']}  {x['sha']}" for x in fp["files"]]
        except Exception as e:  # 清单只是辅助信息，失败不影响恢复包
            lines.append(f"（项目文件清单生成失败：{e}）")
    return "\n".join(lines)


# ---------------- 交接材料同源（B1-B）----------------
# 恢复包里的工具 zip 此前取自工具 HEAD，而说明消息里的 WORKFLOW.md、模板、VERSION 与文件清单取自工作区：工具目录有
# 未提交改动时，同一份恢复包的几部分不同源。这里一次固定来源：有版本库时是一个提交（之后 HEAD 怎么移动都不影响已
# 固定的内容），没有版本库时是一次冻结的快照（每个文件只读一次，后续全部用这份字节）。工作区相对该提交的改动另行
# 冻结、逐个记 sha256，并可打成只读的覆盖包；覆盖包不是版本包，不能安装。
# r2（独立审查 R1–R3）：工具 zip 直接由冻结的字节写出，不再调用 git archive（它会读取当时的 .gitattributes 与
# .git/info/attributes）；遍历目录出错即整次失败；符号链接一律按链接本身冻结（记目标字符串），从不读取链接目标，
# 也不经过任何一级被换成链接的父目录。

SOURCE_SKIP_PARTS = {".git", "local", "__pycache__"}   # 与 _tool_zip、tree_hashes 排除的目录相同
OVERLAY_PREFIX = "wf-dirty-overlay-"                     # 不以 collab-wf- 开头：同步与自动安装都按 collab-wf-<版本> 选包
LINK_MODE = 0o120777                                     # 冻结源里符号链接的权限位（与 zip、git 的约定相同）；内容是目标字符串
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_walk = os.walk                                          # 单独成名字：测试可以注入目录遍历错误


def is_link_mode(mode) -> bool:
    return bool(mode) and _stat.S_ISLNK(mode)


def _manifest_rel(rel: str) -> bool:
    """清单口径（与 tree_hashes 相同）：排除 .git、local、__pycache__、*.pyc、MANIFEST.json、RELEASE.json。"""
    return not (set(rel.split("/")) & SOURCE_SKIP_PARTS) and not rel.endswith(".pyc") \
        and rel not in ("MANIFEST.json", "RELEASE.json")


def _git_bytes(args, cwd, inp=None) -> bytes:
    """以字节方式运行 git（core.git 是文本方式，读 blob 会损坏二进制内容）。失败抛 WfError。"""
    import subprocess
    from .core import WfError
    try:
        r = subprocess.run(["git"] + list(args), cwd=str(cwd), input=inp, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        raise WfError(f"git {' '.join(args[:2])} 无法运行：{e}")
    if r.returncode != 0:
        raise WfError(f"git {' '.join(args[:2])} 失败：{r.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return r.stdout


def _fd_capable() -> bool:
    """r3（独立审查 R7）：以目录描述符为锚的读取需要 openat/fstatat/readlinkat 与 O_NOFOLLOW。macOS、Linux 都有；
    没有时冻结一律拒绝，不退回按路径读取。单独成函数：测试可以模拟不支持的平台。"""
    try:
        return bool(getattr(os, "O_NOFOLLOW", 0)) and os.open in os.supports_dir_fd \
            and os.stat in os.supports_dir_fd and os.readlink in os.supports_dir_fd
    except AttributeError:
        return False


class _NotInside(Exception):
    """路径的某一级上级不是工具目录内的普通目录（被换成链接、文件或已不存在）。"""


_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _open_root(home: Path) -> int:
    """打开工具根目录作为锚（之后所有读取都相对这个描述符，不再按路径解析上级目录）。调用方负责关闭。"""
    from .core import WfError
    if not _fd_capable():
        raise WfError("当前平台不支持以目录描述符为锚的安全读取（openat/fstatat/readlinkat 与 O_NOFOLLOW），"
                      "拒绝冻结工具目录；没有生成任何交接材料")
    try:
        fd = os.open(str(home), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError as e:
        raise WfError(f"无法打开工具目录 {home}：{e}；没有生成任何交接材料")
    if not _stat.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        raise WfError(f"{home} 不是目录；没有生成任何交接材料")
    return fd


def _open_parent(root_fd: int, rel: str) -> tuple:
    """从根目录描述符起逐级以 O_NOFOLLOW|O_DIRECTORY 打开上级目录，返回 (最后一级上级目录的描述符, 文件名)。
    任何一级是链接、不是目录或不存在时抛 _NotInside。打开后上级目录即被固定：之后路径再被换成链接，读到的仍是
    这里已打开的目录对象，不会到工具目录之外。调用方负责关闭返回的描述符。"""
    import errno
    parts = rel.split("/")
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            try:
                nfd = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except OSError as e:
                if e.errno in (errno.ELOOP, errno.ENOTDIR, errno.ENOENT, getattr(errno, "EMLINK", -1)):
                    raise _NotInside(part)
                raise
            os.close(fd)
            fd = nfd
            if not _stat.S_ISDIR(os.fstat(fd).st_mode):
                raise _NotInside(part)
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def _read_file(dir_fd: int, name: str) -> tuple:
    """在已固定的上级目录里读一个普通文件，不跟随链接（O_NOFOLLOW + fstat 复核类型）。返回 (字节, st_mode)。
    单独成函数：测试可以注入读取失败或在这一刻替换上级目录。"""
    from .core import WfError
    fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), dir_fd=dir_fd)
    try:
        st = os.fstat(fd)
        if not _stat.S_ISREG(st.st_mode):
            raise WfError(f"{name} 不是普通文件")
        chunks = []
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks), st.st_mode
    finally:
        os.close(fd)


def _inside(real_home: str, path: str) -> bool:
    return path == real_home or path.startswith(real_home.rstrip(os.sep) + os.sep)


def _freeze_rel(root_fd: int, rel: str, what: str = "冻结工具目录失败", missing_ok: bool = False):
    """以根目录描述符为锚冻结一个路径：链接记 (LINK_MODE, 目标字符串)，普通文件记 (权限, 字节)。
    missing_ok（git 现场改动用）：上级不是目录内普通目录、路径不存在或已是目录时返回 None（与 git 的“已删除”一致）；
    否则这些情况与其他类型、读不了一样整次失败（WfError）。从不按路径解析上级目录，因而不会读到工具目录之外。"""
    from .core import WfError
    try:
        dfd, name = _open_parent(root_fd, rel)
    except _NotInside as e:
        if missing_ok:
            return None
        raise WfError(f"{what}（{rel}）：上级 {e} 已不是工具目录内的普通目录（可能被换成链接或已消失）；没有生成任何交接材料")
    except OSError as e:
        raise WfError(f"{what}（{rel}）：{e}；没有生成任何交接材料")
    try:
        try:
            st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        except FileNotFoundError:
            if missing_ok:
                return None
            raise
        if _stat.S_ISLNK(st.st_mode):
            return LINK_MODE, os.fsencode(os.readlink(name, dir_fd=dfd))
        if _stat.S_ISDIR(st.st_mode) and missing_ok:
            return None
        if not _stat.S_ISREG(st.st_mode):
            raise WfError(f"{what}（{rel}）：不是普通文件或符号链接；没有生成任何交接材料")
        data, mode = _read_file(dfd, name)
        return mode & 0o777, data
    except FileNotFoundError:
        raise WfError(f"{what}（{rel}）：文件在冻结过程中消失；没有生成任何交接材料")
    except WfError:
        raise
    except OSError as e:
        raise WfError(f"{what}（{rel}）：{e}；没有生成任何交接材料")
    finally:
        os.close(dfd)


def commit_files(wf_home: Path, commit: str) -> dict:
    """按提交读出全部文件：{路径: (权限, 字节)}。普通文件权限是 0o644/0o755，符号链接是 LINK_MODE（内容为目标）。
    只读对象库，不读工作区、不看 HEAD。子模块（gitlink）不在其中。"""
    from .core import WfError
    raw = _git_bytes(["ls-tree", "-r", "-z", "--full-tree", commit], wf_home)
    entries = []
    for rec in raw.split(b"\0"):
        if not rec:
            continue
        meta, _, path = rec.partition(b"\t")
        mode, typ, oid = meta.decode().split()
        if typ != "blob":
            continue
        m = int(mode, 8)
        entries.append((path.decode("utf-8", "surrogateescape"), LINK_MODE if _stat.S_ISLNK(m) else (m & 0o777 or 0o644), oid))
    if not entries:
        return {}
    out = _git_bytes(["cat-file", "--batch"], wf_home, inp=("\n".join(e[2] for e in entries) + "\n").encode())
    files, pos = {}, 0
    for rel, mode, oid in entries:
        nl = out.find(b"\n", pos)
        head = out[pos:nl].decode().split() if nl >= 0 else []
        if len(head) != 3 or head[0] != oid or head[1] != "blob":
            raise WfError(f"读取提交 {commit[:12]} 的文件 {rel} 失败：{' '.join(head) or '输出不完整'}")
        size = int(head[2])
        data = out[nl + 1:nl + 1 + size]
        if len(data) != size:
            raise WfError(f"读取提交 {commit[:12]} 的文件 {rel} 失败：内容不完整")
        files[rel] = (mode, data)
        pos = nl + 1 + size + 1
    return files


def _freeze_dir(home: Path) -> dict:
    """没有版本库时冻结工具目录：每个文件读一次。列目录失败、文件读不了或中途消失都整次失败（不产出残缺的快照）；
    指向目录的链接与指向文件的链接一样只记链接本身，不进入、不读取目标。r3：内容一律经 _freeze_rel 以工具根目录
    描述符为锚读取，遍历期间上级目录被换成链接时整次拒绝，不会读到目录外字节。"""
    from .core import WfError
    home = Path(home)
    real_home = os.path.realpath(home)

    def rel_of(path) -> str:
        try:
            return str(Path(path).relative_to(home)).replace(os.sep, "/") or "."
        except ValueError:
            return str(path)

    def onerror(e):
        raise WfError(f"冻结工具目录失败（无法列出 {rel_of(getattr(e, 'filename', '') or home)}）：{e}；没有生成任何交接材料")
    files = {}
    root_fd = _open_root(home)
    try:
        for dp, dns, fns in _walk(home, onerror=onerror, followlinks=False):
            if not _inside(real_home, os.path.realpath(dp)):
                raise WfError(f"冻结工具目录失败（{rel_of(dp)} 已不在工具目录内）；没有生成任何交接材料")
            keep = []
            for d in sorted(dns):
                if d in SOURCE_SKIP_PARTS:
                    continue
                p = Path(dp) / d
                try:
                    st = os.lstat(p)
                except OSError as e:
                    raise WfError(f"冻结工具目录失败（{rel_of(p)}）：{e}；没有生成任何交接材料")
                if _stat.S_ISLNK(st.st_mode):
                    files[rel_of(p)] = _freeze_rel(root_fd, rel_of(p))
                else:
                    keep.append(d)
            dns[:] = keep
            for fn in sorted(fns):
                if fn in SOURCE_SKIP_PARTS:
                    continue
                rel = rel_of(Path(dp) / fn)
                files[rel] = _freeze_rel(root_fd, rel)
    finally:
        os.close(root_fd)
    return files


def _wt_mode(st_mode: int) -> int:
    return 0o755 if st_mode & 0o100 else 0o644   # 与 git 相同：只看属主执行位


def _kind_of(x):
    return None if x is None else ("symlink" if is_link_mode(x[0]) else "file")


def _frozen_dirty(home: Path, commit: str, files: dict) -> tuple:
    """工作区相对 commit（不是当时的 HEAD）的改动，读一次冻结。返回 (条目列表, {路径: (权限, 字节) 或 None})。
    链接只记目标字符串；父目录被换成链接的路径按已删除记。"""
    names = set()
    for args in (["diff", "--name-only", "-z", "--no-renames", commit, "--"],
                 ["ls-files", "--others", "--exclude-standard", "-z"]):
        names |= {n.decode("utf-8", "surrogateescape") for n in _git_bytes(args, home).split(b"\0") if n}
    entries, frozen = [], {}
    rels = [r for r in sorted(names) if not (set(r.split("/")) & SOURCE_SKIP_PARTS) and not r.endswith(".pyc")]
    root_fd = _open_root(home) if rels else None   # r3（R7）：以工具根目录描述符为锚读取
    try:
        for rel in rels:
            cur = _freeze_rel(root_fd, rel, "冻结工作区改动失败", missing_ok=True)
            if cur is not None and not is_link_mode(cur[0]):
                cur = (_wt_mode(cur[0]), cur[1])
            _add_dirty(entries, frozen, rel, cur, files.get(rel))
    finally:
        if root_fd is not None:
            os.close(root_fd)
    return entries, frozen


def _add_dirty(entries: list, frozen: dict, rel: str, cur, base) -> None:
    if base == cur:
        return   # 只是时间戳变了
    status = "D" if cur is None else ("A" if base is None else "M")
    e = {"path": rel, "status": status, "type": _kind_of(cur), "base_type": _kind_of(base),
         "sha256": hashlib.sha256(cur[1]).hexdigest() if cur else None,
         "bytes": len(cur[1]) if cur else None, "mode": oct(cur[0]) if cur else None,
         "base_sha256": hashlib.sha256(base[1]).hexdigest() if base else None,
         "base_mode": oct(base[0]) if base else None}
    if cur and is_link_mode(cur[0]):
        e["target"] = cur[1].decode("utf-8", "replace")
    entries.append(e)
    frozen[rel] = cur


def freeze_tool_source(wf_home: Path) -> dict:
    """一次固定交接用的工具来源。返回：
    kind（git / snapshot）、commit、tree、version、files（{路径: (权限, 字节)}）、manifest（{路径: sha256}，清单口径）、
    digest（按 ops.fingerprint 的行格式对 manifest 求 sha256 前 16 位）、commit_verified（材料是否确由该提交读出）、
    dirty（相对该提交的工作区改动，冻结后的 sha256）、dirty_frozen（{路径: (权限, 字节) 或 None}）。"""
    from .core import WfError
    home = Path(wf_home)
    if (home / ".git").exists():
        commit = _git_bytes(["rev-parse", "--verify", "HEAD^{commit}"], home).decode().strip()
        tree = _git_bytes(["rev-parse", "--verify", commit + "^{tree}"], home).decode().strip()
        short = _git_bytes(["rev-parse", "--short", commit], home).decode().strip()
        files = commit_files(home, commit)
        kind = "git"
    else:
        commit = tree = short = None
        files = _freeze_dir(home)
        kind = "snapshot"
    if "VERSION" not in files or is_link_mode(files["VERSION"][0]):
        raise WfError("固定的工具来源里没有 VERSION（或它不是普通文件），无法生成交接材料")
    manifest = {rel: hashlib.sha256(files[rel][1]).hexdigest() for rel in sorted(files) if _manifest_rel(rel)}
    digest = hashlib.sha256("".join(f"{h}  {rel}\n" for rel, h in manifest.items()).encode()).hexdigest()[:16]
    dirty, frozen = _frozen_dirty(home, commit, files) if kind == "git" else ([], {})
    return {"kind": kind, "home": str(home), "commit": commit, "tree": tree, "short": short,
            "version": files["VERSION"][1].decode("utf-8", "replace").strip(),
            "files": files, "manifest": manifest, "digest": digest, "commit_verified": kind == "git",
            "dirty": dirty, "dirty_frozen": frozen}


def source_text(src: dict, rel: str) -> str:
    """从固定来源取一个文本文件（模板、WORKFLOW.md）。来源里没有或是链接时抛 WfError，不退回读工作区。"""
    from .core import WfError
    if rel not in src["files"] or is_link_mode(src["files"][rel][0]):
        raise WfError(f"固定的工具来源（{src.get('commit') or '冻结快照'}）里没有普通文件 {rel}")
    return src["files"][rel][1].decode("utf-8")


def _zip_attr(mode: int) -> int:
    return (LINK_MODE if is_link_mode(mode) else (_stat.S_IFREG | ((mode or 0o644) & 0o7777))) << 16


def _zip_bytes(members) -> bytes:
    """确定性 zip：固定时间戳、按给定顺序写入、记录权限（链接按 zip 约定记为链接，内容是目标字符串）。"""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, mode, data in members:
            info = zipfile.ZipInfo(name, _ZIP_EPOCH)
            info.external_attr = _zip_attr(mode)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return buf.getvalue()


def source_zip_bytes(src: dict, prefix: str = "collab-wf/") -> bytes:
    """工具 zip：全部内容来自固定来源的冻结字节（有提交时即该提交的全部 blob）。不调用 git archive，
    因而不受 .gitattributes、.git/info/attributes 的 export-ignore / export-subst 等现场设置影响。"""
    return _zip_bytes([(prefix + rel, src["files"][rel][0], src["files"][rel][1]) for rel in sorted(src["files"])])


snapshot_zip_bytes = source_zip_bytes   # r1 的名字，保留


def verify_zip_source(zip_path: Path, src: dict, prefix: str = "collab-wf/") -> list:
    """核对一个工具 zip 与固定来源逐项一致：路径集合、每个条目的字节与权限（含链接类型）。返回问题列表（空表示一致）。"""
    import zipfile
    problems = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            got = {}
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if not info.filename.startswith(prefix):
                    problems.append(f"条目不在 {prefix} 下：{info.filename}")
                    continue
                got[info.filename[len(prefix):]] = (info.external_attr >> 16, zf.read(info))
    except (OSError, zipfile.BadZipFile) as e:
        return [f"工具 zip 无法读取：{e}"]
    want = src["files"]
    for rel in sorted(set(want) - set(got)):
        problems.append(f"zip 缺少 {rel}")
    for rel in sorted(set(got) - set(want)):
        problems.append(f"zip 多出 {rel}")
    for rel in sorted(set(want) & set(got)):
        if got[rel][1] != want[rel][1]:
            problems.append(f"{rel} 内容与固定来源不同")
        elif got[rel][0] != _zip_attr(want[rel][0]) >> 16:
            problems.append(f"{rel} 权限或类型与固定来源不同（{oct(got[rel][0])} ≠ {oct(_zip_attr(want[rel][0]) >> 16)}）")
    return problems


def dirty_diff_bytes(src: dict) -> bytes:
    """工作区改动的可读 diff，只用冻结的字节生成（不再读工作区）。仅供阅读，不是可直接应用的补丁
    （按行比较，不保留行尾差异）；逐文件原字节与 sha256 以覆盖包里的 files/ 与 OVERLAY.json 为准。"""
    import difflib
    out = []
    for e in src["dirty"]:
        rel = e["path"]
        base = src["files"].get(rel)
        cur = src["dirty_frozen"].get(rel)
        if (base and is_link_mode(base[0])) or (cur and is_link_mode(cur[0])):
            def show(x):
                return "（无）" if x is None else (("链接 -> " + x[1].decode("utf-8", "replace")) if is_link_mode(x[0])
                                                 else f"普通文件 sha256 {hashlib.sha256(x[1]).hexdigest()}")
            out.append(f"symlink {rel}: {show(base)} => {show(cur)}")
            continue
        old_b = base[1] if base else b""
        new_b = cur[1] if cur else b""
        if e["mode"] != e["base_mode"] and e["mode"] and e["base_mode"]:
            out.append(f"mode {rel}: {e['base_mode']} -> {e['mode']}")
        try:
            old_t, new_t = old_b.decode("utf-8").splitlines(), new_b.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            out.append(f"Binary files a/{rel} and b/{rel} differ ({e['base_sha256']} -> {e['sha256']})")
            continue
        out += list(difflib.unified_diff(old_t, new_t, f"a/{rel}" if base else "/dev/null",
                                         f"b/{rel}" if e["status"] != "D" else "/dev/null", lineterm=""))
    return ("\n".join(out) + "\n").encode("utf-8") if out else b""


def overlay_bytes(src: dict) -> tuple:
    """只读覆盖包：冻结的现场改动 + 清单 + diff。返回 (zip 字节, 清单 dict)。
    包内有多个顶层条目、没有 VERSION 与 wf.py：release._extract 按“只能有一个顶层目录”拒绝，不能被当作版本包安装。
    链接只在 OVERLAY.json 里记目标字符串，不作为 zip 条目、更不读取目标内容；files/ 下只有冻结的普通文件字节。"""
    diff = dirty_diff_bytes(src)
    meta = {"kind": "collab-wf-dirty-overlay", "installable": False, "base_kind": src["kind"],
            "base_commit": src["commit"], "base_tree": src["tree"], "base_version": src["version"],
            "entries": src["dirty"], "diff_sha256": hashlib.sha256(diff).hexdigest(),
            "diff_note": "DIRTY.diff 仅供阅读，不是可直接应用的补丁；以 files/ 的原字节与 entries 的 sha256 为准",
            "note": "运行现场相对 base_commit 的未提交改动，冻结一次后打包；作者、授权与审查来源未知，不是版本包，不可安装。"}
    members = [("NOT-INSTALLABLE.txt", 0o444, ("这是现场未提交改动的只读留证包，不是 collab-wf 版本包，不可安装。\n"
                                                f"基线提交 {src['commit']}，tree {src['tree']}。\n").encode("utf-8")),
               ("OVERLAY.json", 0o444, json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")),
               ("DIRTY.diff", 0o444, diff)]
    for e in src["dirty"]:
        cur = src["dirty_frozen"].get(e["path"])
        if cur is not None and not is_link_mode(cur[0]):
            members.append(("files/" + e["path"], 0o444, cur[1]))
    return _zip_bytes(members), meta


def source_summary(src: dict) -> dict:
    """可写进返回值与事件的来源摘要（不含文件字节）。"""
    return {"kind": src["kind"], "commit": src["commit"], "tree": src["tree"], "version": src["version"],
            "digest": src["digest"], "files": len(src["manifest"]), "commit_verified": src["commit_verified"],
            "dirty": [dict(e) for e in src["dirty"]]}


def source_declaration(summary: dict, tool_zip_name: str, overlay: dict = None) -> str:
    """说明消息末尾的【工具来源】声明：附件、正文与清单取自哪里；现场改动有哪些、是否随消息提供。"""
    lines = ["", "===== 工具来源（同源声明） ====="]
    if summary["kind"] == "git":
        lines.append(f"附件 {tool_zip_name}、上文的 WORKFLOW.md、本消息模板、工具版本 {summary['version']} 与文件清单，"
                     f"全部取自工具提交 {summary['commit']}（tree {summary['tree']}）的冻结内容；附件已逐项核对与之一致。")
    else:
        lines.append(f"工具目录没有版本库：附件 {tool_zip_name}、上文的 WORKFLOW.md、本消息模板、工具版本 {summary['version']} "
                     f"与文件清单取自同一次冻结快照（{summary['files']} 个清单文件，摘要 {summary['digest']}）；"
                     "没有提交号，未核对任何原生提交。")
    if summary.get("head_moved"):
        lines.append(f"生成期间工具 HEAD 已移动到 {summary.get('head_now')}；以上材料仍全部取自上面固定的提交。")
    if summary["dirty"]:
        lines.append(f"冻结时运行现场相对该提交有 {len(summary['dirty'])} 个未提交改动，内容不在附件与正文中"
                     "（作者、授权、审查来源未知，既未采用也未回退）：")
        for e in summary["dirty"]:
            tail = f"  链接 -> {e['target']}" if e.get("target") is not None else ""
            lines.append(f"- {e['status']} {e['path']}  现场 {(e['sha256'] or '（已删除）')[:12]}  "
                         f"基线 {(e['base_sha256'] or '（无）')[:12]}{tail}")
        if overlay:
            lines.append(f"现场改动已冻结为只读覆盖包 {overlay['name']}（sha256 {overlay['sha256']}，diff sha256 "
                         f"{overlay['diff_sha256']}），留在执行端 outbox，不随本消息发送，不可安装；需要时请点名索取。"
                         + ("" if overlay.get("read_only", True) else "（注意：覆盖包未能设为只读）"))
    elif summary["kind"] == "git":
        lines.append("冻结时运行现场与该提交一致，没有未提交改动。")
    return "\n".join(lines) + "\n"
