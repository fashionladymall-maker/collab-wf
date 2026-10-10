"""1.5.7：系统通知。wf tick 推进完之后，把各项目新出现的待决事项与提示（与 wf ask 同一来源）弹成 macOS 通知。
每件事只通知一次（记在 local/notified.json，与 wf ask 的已读记录互不影响）；事项消失后记录随之清除，再出现会再通知。
用户的诉求是看得到进度、不接受默默执行；执行端此前用本地胶水做这件事（collab-wf-dev 第 17 轮），现在收进工具。
关闭：环境变量 WF_NOTIFY=0，或 local/notify.json 写 {"enabled": false}。通知出任何问题都不影响 wf tick。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .core import LOCAL, now_iso, read_json, secs_since, write_json

STORE = LOCAL / "notified.json"
CONFIG = LOCAL / "notify.json"
INBOX = LOCAL / "inbox.json"   # 1.5.9：执行端的待办信号（执行端的定时自动化读取它）；与 macOS 通知无关，总是写入


def enabled() -> bool:
    if os.environ.get("WF_NOTIFY", "").strip().lower() in ("0", "off", "false", "no"):
        return False
    if (read_json(CONFIG, {}) or {}).get("enabled") is False:
        return False
    return sys.platform == "darwin" and shutil.which("osascript") is not None


def _osa(s) -> str:
    """AppleScript 字符串字面量：转义反斜杠与双引号，换行改成空格。"""
    s = str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")
    return f'"{s}"'


def send(title, message, subtitle="") -> bool:
    script = f"display notification {_osa(str(message)[:220])} with title {_osa(str(title)[:80])}"
    if subtitle:
        script += f" subtitle {_osa(str(subtitle)[:80])}"
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def collect(inst) -> list:
    """一个项目当前应当让用户知道的事：待决事项（需要你决定）与提示（不需要决定）。"""
    from . import asks
    out = []
    for it in asks.pending(inst):
        out.append({"key": f"{inst.name}|{it['code']}", "subtitle": f"需要你决定：{it['title']}",
                    "message": f"{it['why']}（决定码 {it['code']}）"})
    for n in asks.notices(inst):
        out.append({"key": f"{inst.name}|{n['key']}", "subtitle": n["title"], "message": n["why"]})
    return out


def after_tick(projects, sender=None, store=None, inbox=None) -> list:
    """wf tick 之后调用：新出现的事各通知一次。返回本次发出的通知。
    1.5.9：先写执行端的待办信号 local/inbox.json，不受通知开关影响；只给了 store 的调用（自检）不写真实的 inbox。"""
    if inbox is not None or store is None:
        try:
            write_inbox(projects, inbox)
        except Exception:   # noqa: BLE001  信号写不出来不影响 wf tick
            pass
    try:
        if sender is None:
            if not enabled():
                return []
            sender = send
        store = store or STORE
        rec = read_json(store, {}) or {}
        seen = rec.get("keys") or {}
        live, sent = set(), []
        for inst in projects:
            try:
                items = collect(inst)
            except Exception:   # noqa: BLE001  某个项目读不出来，不影响其他项目
                continue
            for x in items:
                live.add(x["key"])
                if x["key"] in seen:
                    continue
                if sender(f"collab-wf · {inst.name}", x["message"], x["subtitle"]):
                    seen[x["key"]] = now_iso()
                    sent.append(x)
        names = {getattr(i, "name", "") for i in projects}
        for k in list(seen):
            if k.split("|", 1)[0] in names and k not in live:
                del seen[k]
        write_json(store, {"keys": seen, "updated": now_iso()})
        return sent
    except Exception:   # noqa: BLE001
        return []


# 1.5.9：交给用户的事（由执行端转告，执行端不替用户决定）：四道底线命中、撤销暂停、目标达成；其余都交给执行端
USER_MARKS = ("必须停下的", "新增内容命中", "底线文件", "底线行为检查")
ESCALATE_S = 7200   # 1.6.5：交给执行端的事超过 2 小时还在，转给用户（“没人接”本身也要让人看见）


def route(it: dict) -> str:
    """一件待办该由谁处理：user 或 executor。"""
    kind, key = str(it.get("kind") or ""), str(it.get("key") or "")
    if kind == "unpause" or key.startswith("done:"):
        return "user"
    return "user" if any(m in str(it.get("why") or "") for m in USER_MARKS) else "executor"


def collect_inbox(projects) -> list:
    """全部项目当前的待办（与 wf ask 同一来源），每件标明交给谁。"""
    from . import asks
    out = []
    for inst in projects:
        try:
            rows = [{"key": f"{inst.name}|{it['code']}", "project": inst.name, "for": route(it), "title": it["title"],
                     "why": it["why"], "action": it["command"], "code": it["code"]} for it in asks.pending(inst)]
            rows += [{"key": f"{inst.name}|{n['key']}", "project": inst.name, "for": route(n), "title": n["title"],
                      "why": n["why"], "action": "", "code": ""} for n in asks.notices(inst)]
        except Exception:   # noqa: BLE001  某个项目读不出来，不影响其他项目
            continue
        out += rows
    try:   # 1.6.5：外部信号（看门狗与其它独立进程投递的错误、卡死、疑难），见 wflib/signals.py
        from . import signals
        for s in signals.load():
            out.append({"key": f"signal|{s['key']}", "project": s.get("project") or "", "for": s.get("for") or "executor",
                        "title": s.get("title") or "", "why": s.get("why") or "", "action": s.get("action") or "",
                        "code": "", "source": s.get("source") or "external"})
    except Exception:   # noqa: BLE001  信号读不出来不影响其余待办
        pass
    return out


def write_inbox(projects, path=None, items=None) -> list:
    """覆盖写 inbox：当前全部待办；同一件事保留第一次出现的时间（since），处理掉的事随之消失。"""
    path = path or INBOX
    old = {x.get("key"): x for x in ((read_json(path, {}) or {}).get("items") or []) if isinstance(x, dict)}
    items = collect_inbox(projects) if items is None else [dict(x) for x in items]
    for x in items:
        x["since"] = (old.get(x["key"]) or {}).get("since") or now_iso()
        # 1.6.5（用户第 53 轮）：交给执行端的事超过 2 小时还在，升级给用户；额度等待本来就会持续数小时，除外
        if x.get("for") == "executor" and (secs_since(x["since"]) or 0) > ESCALATE_S \
                and not str(x.get("key") or "").split("|", 1)[-1].startswith("quota:"):
            x["for"], x["escalated"] = "user", True
            x["title"] = f"{x.get('title') or ''}（交给执行端超过 {ESCALATE_S // 3600} 小时仍未处理，已转给用户）"
    write_json(path, {"updated": now_iso(), "items": items})
    return items


def status_text() -> str:
    rec = read_json(STORE, {}) or {}
    on = enabled()
    why = "" if on else "（只支持 macOS 的 osascript；或被 WF_NOTIFY=0、local/notify.json 关闭）"
    return f"系统通知：{'开启' if on else '关闭'}{why}；已通知、仍未处理的事 {len(rec.get('keys') or {})} 件"
