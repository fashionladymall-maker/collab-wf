"""1.5.0 可靠性补强：回报保真、解析容错、回复归属、执行接续、额度空值原因、事件序号、升级后核验、无附件续接。

设计：这里只放纯函数和只读本地文件的函数，便于单测；驱动器、回报、体检与升级只在调用点接入。
只用标准库，兼容 Python 3.9+。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
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

def tool_manifest_text(wf_home: Path, project_root=None) -> str:
    """新对话恢复包附带的文件清单：通道不能上传附件时，Claude 据此点名要读的文件。"""
    lines = ["工具文件（路径、字节数、sha256 前 12 位）："]
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
