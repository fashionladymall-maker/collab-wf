"""1.5.3：Claude 页面快照与对话记录（工作流看板“Claude 页面快照”“对话记录”两个板块的数据）。

页面快照由通道适配器按 docs/SNAPSHOT.md 写出（中立格式，工具不依赖站点的原始字段）；wf 只负责收下、去重、
保留与提供给看板。快照只用于展示与取证，不参与任何状态判定：会话的消息列表在生成结束前不会出现新消息
（collab-wf-dev 第 10 轮），不能用它判断消息是否送达。
存放位置：workflow/evidence/page/（已被 git 忽略、受落地保护、不计入仓库指纹，也不进恢复包）。
1.5.4：快照有“文本 + 截图”两种形态（用户第 13 轮要求）；截图由适配器写成 PNG，与同一轮的文本快照成对保存。"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
from pathlib import Path

from .core import Instance, WfError, append_jsonl, now_iso, open_project, read_json, read_jsonl, write_json

SCHEMA = "collab-wf.snapshot/1"
KEEP_DEFAULT = 50          # 按轮保留的快照份数（policy.json 的 page_snapshot_keep 可改）
ENV = "WF_PAGE_SNAPSHOT"   # 告诉适配器把快照写到哪里（见 docs/SNAPSHOT.md）
SHOT_ENV = "WF_PAGE_SCREENSHOT"            # 1.5.4：告诉适配器把截图（PNG）写到哪里
SHOT_MODE_ENV = "WF_PAGE_SCREENSHOT_MODE"  # 1.5.4：full 时截整页（wf look --full），默认截最新的一屏
SHOT_KEEP_DEFAULT = 20                     # 按轮保留的截图份数（policy.json 的 page_screenshot_keep 可改）
SHOT_MAX_BYTES = 12 * 1024 * 1024
PNG_SIG = b"\x89PNG\r\n\x1a\n"


def page_dir(inst: Instance) -> Path:
    return inst.wf / "evidence" / "page"


def incoming_path(inst: Instance) -> Path:
    return inst.turn_dir() / "page-incoming.json"


def incoming_shot_path(inst: Instance) -> Path:
    return inst.turn_dir() / "page-incoming.png"


def _policy(inst: Instance) -> dict:
    return read_json(inst.wf / "policy.json", {}) or {}


def poll_env(inst: Instance, shots=None, mode=None) -> dict:
    """调用适配器（poll、snapshot）之前：清掉上一次残留的临时文件，返回要传给适配器的环境变量。
    1.5.4：同时给出截图路径；policy.json 的 page_screenshots 为 false 时，轮询不请求截图（wf look 总是请求）。"""
    p, png = incoming_path(inst), incoming_shot_path(inst)
    for f in (p, png):
        try:
            if f.exists():
                f.unlink()
        except OSError:
            pass
    env = {ENV: str(p)}
    if shots is None:
        shots = _policy(inst).get("page_screenshots", True) is not False
    if shots:
        env[SHOT_ENV] = str(png)
        if mode:
            env[SHOT_MODE_ENV] = mode
    return env


def after_poll(inst: Instance, source="poll", status=None, keep=None, shot_keep=None):
    """调用适配器之后：适配器写出了快照或截图就收下，然后删除临时文件。两样都没有写出时返回 None。
    快照只用于展示：这里的任何问题都不会影响驱动器。"""
    p, png = incoming_path(inst), incoming_shot_path(inst)
    if not p.exists() and not png.exists():
        return None
    try:
        if p.exists():
            res = ingest(inst, p, source=source, status=status, keep=keep)
        else:
            res = {"ok": True, "changed": False, "messages": None, "text": False}
        res["shot"] = ingest_shot(inst, png if png.exists() else None, res.pop("shot_info", None), source, keep=shot_keep)
        return res
    except Exception as e:   # noqa: BLE001
        return {"ok": False, "problems": [f"{type(e).__name__}: {e}"]}
    finally:
        for f in (p, png):
            try:
                f.unlink()
            except OSError:
                pass


def validate(snap) -> list:
    if not isinstance(snap, dict):
        return ["快照不是 JSON 对象"]
    problems = []
    if snap.get("schema") != SCHEMA:
        problems.append(f"schema 应为 {SCHEMA}")
    msgs = snap.get("messages")
    if not isinstance(msgs, list):
        return problems + ["缺少 messages 列表"]
    for i, m in enumerate(msgs, 1):
        if not isinstance(m, dict) or m.get("role") not in ("human", "assistant") or not isinstance(m.get("blocks"), list):
            problems.append(f"第 {i} 条消息格式不对：需要 role（human 或 assistant）与 blocks 列表")
            break
    return problems


def content_sha(snap: dict) -> str:
    """内容指纹：只看消息与当前叶子，抓取时间不同不算变化。"""
    body = {"leaf": (snap.get("conversation") or {}).get("current_leaf"), "messages": snap.get("messages")}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _keep(inst: Instance, keep) -> int:
    if keep is None:
        keep = (read_json(inst.wf / "policy.json", {}) or {}).get("page_snapshot_keep", KEEP_DEFAULT)
    try:
        return max(1, int(keep))
    except (TypeError, ValueError):
        return KEEP_DEFAULT


def _turn_files(d: Path, ext="json") -> list:
    out = []
    for p in d.glob(f"turn-*.{ext}"):
        n = p.stem[5:]
        if n.isdigit():
            out.append((int(n), p))
    return sorted(out)


def ingest(inst: Instance, src, source="poll", status=None, keep=None) -> dict:
    """收下一份快照：内容变了就原样（逐字节）保存为最新快照与本轮快照；没变只记录这次查看。每次都记一行 checks.jsonl。"""
    src = Path(src)
    d = page_dir(inst)
    d.mkdir(parents=True, exist_ok=True)
    turn = int(inst.state.get("turn", 0))
    ts = now_iso()
    try:
        snap = json.loads(src.read_text(encoding="utf-8"))
        problems = validate(snap)
    except (ValueError, OSError) as e:
        snap, problems = None, [f"快照不是有效的 JSON：{e}"]
    if problems:
        append_jsonl(d / "checks.jsonl", {"ts": ts, "turn": turn, "source": source, "status": status, "ok": False,
                                          "problems": problems[:5]})
        return {"ok": False, "problems": problems, "shot_info": snap.get("screenshot") if isinstance(snap, dict) else None}
    with inst.state_lock():
        sha = content_sha(snap)
        meta = read_json(d / "meta.json", {}) or {}
        changed = sha != meta.get("sha")
        tf = d / f"turn-{turn:03d}.json"
        if changed:
            shutil.copyfile(src, d / "latest.json")
            shutil.copyfile(src, tf)
            conv = snap.get("conversation") or {}
            meta.update(sha=sha, changed_at=ts, changed_turn=turn, messages=len(snap["messages"]),
                        captured_at=snap.get("captured_at"),
                        conversation={k: conv.get(k) for k in ("id", "name", "model", "updated_at", "current_leaf")})
        elif not tf.exists():
            shutil.copyfile(src, tf)   # 本轮第一次看到（内容与上一轮末相同）
        meta.update(checked_at=ts, checked_turn=turn, source=source, status=status, looks=int(meta.get("looks", 0)) + 1)
        write_json(d / "meta.json", meta)
        files = _turn_files(d)
        for _, p in files[:-_keep(inst, keep)]:
            try:
                p.unlink()
            except OSError:
                pass
    append_jsonl(d / "checks.jsonl", {"ts": ts, "turn": turn, "source": source, "status": status, "ok": True,
                                      "changed": changed, "sha": sha[:12], "messages": len(snap["messages"])})
    return {"ok": True, "changed": changed, "sha": sha[:12], "messages": len(snap["messages"]),
            "shot_info": snap.get("screenshot")}


def _shot_keep(inst: Instance, keep) -> int:
    if keep is None:
        keep = _policy(inst).get("page_screenshot_keep", SHOT_KEEP_DEFAULT)
    try:
        return max(1, int(keep))
    except (TypeError, ValueError):
        return SHOT_KEEP_DEFAULT


def ingest_shot(inst: Instance, png, info=None, source="poll", keep=None) -> dict:
    """1.5.4：收下一张截图。原样保存为 latest.png 与本轮的 turn-NNN.png；没有截图时记下原因，保留上一张。"""
    d = page_dir(inst)
    d.mkdir(parents=True, exist_ok=True)
    info = info if isinstance(info, dict) else {}
    turn = int(inst.state.get("turn", 0))
    ts = now_iso()
    with inst.state_lock():
        meta = read_json(d / "meta.json", {}) or {}
        if png is not None:
            data = Path(png).read_bytes()
            if not data.startswith(PNG_SIG) or len(data) < 24:
                rec = {"status": "bad", "reason": "适配器写出的截图不是 PNG 文件"}
            elif len(data) > SHOT_MAX_BYTES:
                rec = {"status": "too_big", "reason": f"截图 {len(data)} 字节，超过上限 {SHOT_MAX_BYTES} 字节，没有收下"}
            else:
                w, h = struct.unpack(">II", data[16:24])
                shutil.copyfile(png, d / "latest.png")
                shutil.copyfile(png, d / f"turn-{turn:03d}.png")
                rec = {"status": "ok", "bytes": len(data), "width": w, "height": h, "mode": info.get("mode"),
                       "captured_at": info.get("captured_at") or ts}
                meta["shot"] = {**rec, "at": ts, "turn": turn, "source": source}
                for _, f in _turn_files(d, "png")[:-_shot_keep(inst, keep)]:
                    try:
                        f.unlink()
                    except OSError:
                        pass
        else:
            st = str(info.get("status") or "none")
            rec = {"status": st, "reason": info.get("reason") or ("适配器这次没有给出截图" if st == "none" else "")}
        meta["shot_last"] = {**rec, "at": ts, "turn": turn, "source": source}
        write_json(d / "meta.json", meta)
    return rec


def shot_file(project, turn=None):
    """看板接口 /api/page-image 用：最新截图或指定轮次的截图路径；没有时返回 None。"""
    try:
        inst = _inst(project)
    except WfError:
        return None
    d = page_dir(inst)
    if turn not in (None, ""):
        try:
            f = d / f"turn-{int(turn):03d}.png"
        except (TypeError, ValueError):
            return None
    else:
        f = d / "latest.png"
    return f if f.is_file() else None


def look(inst: Instance, full=False) -> dict:
    """wf look：看一次 Claude 页面（适配器的 snapshot 动作），更新文本快照与截图。不发送任何消息，不改变工作流状态。"""
    from . import anomaly
    st = inst.state
    r = anomaly.call("snapshot", ["--project", inst.name, "--ref", st.get("send_ref") or "", "--out", inst.turn_dir()]
                     + anomaly.conv_args(inst),   # 1.7.0（ISOLATION.md S1）
                     timeout=120, env_extra=poll_env(inst, shots=True, mode="full" if full else None))
    if r.get("kind") == "not_configured":
        after_poll(inst, "look")
        return {"ok": False, "note": "适配器没有提供 snapshot 动作：按 docs/SNAPSHOT.md 实现后再运行 wf look"
                                     "（驱动器每次轮询时仍会顺带更新快照）"}
    res = after_poll(inst, "look", (r.get("data") or {}).get("status"))
    if res is None:
        return {"ok": False, "note": f"适配器没有写出快照（退出码 {r.get('code')}）：{(r.get('raw') or '')[:200]}"}
    if not res.get("ok"):
        return {"ok": False, "note": "快照不符合 docs/SNAPSHOT.md：" + "；".join(res.get("problems", []))}
    sh = res.get("shot") or {}
    shot = (f"截图已更新（{sh.get('width')}×{sh.get('height')}）" if sh.get("status") == "ok"
            else f"本次没有截图：{sh.get('reason') or sh.get('status')}")
    text = ("文本快照没有写出" if res.get("text") is False else
            f"{res['messages']} 条消息，" + ("内容有更新" if res["changed"] else "内容没有变化，已记录这次查看"))
    return {"ok": True, "note": f"已看一次：{text}；{shot}"}


def record_user(inst: Instance, text, kind="chat", title=None, code=None, event=True) -> dict:
    """把用户的原话逐字写进对话记录（role=user）。wf said 与用户批准（决定码加原话）都走这里。"""
    text = "" if text is None else str(text)
    if not text.strip():
        raise WfError("需要用户的原话（逐字）")
    rec = {"ts": now_iso(), "role": "user", "turn": int(inst.state.get("turn", 0)), "text": text, "kind": kind}
    if title:
        rec["title"] = title
    if code:
        rec["code"] = code
    append_jsonl(inst.transcript_path, rec)
    if event:
        inst.event("user_said", kind=kind, text=text[:300], by="user")
    return rec


def _inst(x):
    return x if isinstance(x, Instance) else open_project(x)


def api_page(project, turn=None, meta_only=False) -> dict:
    """看板接口 /api/page：最新快照（或指定轮次的快照）及其元信息。"""
    try:
        inst = _inst(project)
    except WfError as e:
        return {"error": str(e)}
    d = page_dir(inst)
    meta = read_json(d / "meta.json", {}) or {}
    out = {"meta": meta, "turns": [n for n, _ in _turn_files(d)], "shots": [n for n, _ in _turn_files(d, "png")]}
    if meta_only:
        return out
    if turn not in (None, ""):
        try:
            n = int(turn)
        except (TypeError, ValueError):
            return {**out, "error": f"轮次不对：{turn}"}
        out["turn"] = n
        out["snapshot"] = read_json(d / f"turn-{n:03d}.json", None)
    else:
        out["snapshot"] = read_json(d / "latest.json", None)
    return out


def api_transcript(project, after=0) -> dict:
    """看板接口 /api/transcript：对话记录全文（逐字），按序号增量读取。"""
    try:
        inst = _inst(project)
    except WfError as e:
        return {"error": str(e)}
    rows = read_jsonl(inst.transcript_path)
    try:
        after = max(0, int(after or 0))
    except (TypeError, ValueError):
        after = 0
    reset = after > len(rows)
    return {"count": len(rows), "reset": reset, "entries": rows if reset else rows[after:]}
