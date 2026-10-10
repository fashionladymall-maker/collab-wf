"""适配器基类：收发消息的通道无关骨架（随工具发布，只含接口，不含任何具体通道）。
具体通道的子类实现 send / poll / quota 三个方法，返回 (退出码, 要输出的 JSON)。
基类负责参数解析、JSONL 记账、退出码常量、把结果打印为约定格式。wf 只通过退出码和这份 JSON 与适配器交互。
"""
from __future__ import annotations

import json
import os
import sys
import time

OK, BUSY, QUOTA, UNKNOWN, REJECTED = 0, 2, 3, 4, 5

QUOTA_HINTS = ("usage limit", "rate limit", "quota", "too many requests", "429")
# “通道正忙”的信号（含疑似孤儿锁）。适配器据此返回 BUSY；是否为孤儿锁、要不要清理由 wf 与人判断，
# 适配器永不自行中止任何进行中的生成（TRANSPORT.md，F22）。
BUSY_HINTS = ("in use", "already running", "in this conversation", "code execution")


def parse(argv) -> dict:
    out, i = {}, 0
    while i < len(argv):
        if argv[i].startswith("--"):
            out[argv[i][2:]] = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
        else:
            i += 1
    return out


def classify(text: str) -> int:
    low = (text or "").lower()
    if any(h in low for h in BUSY_HINTS):
        return BUSY
    if any(h in low for h in QUOTA_HINTS):
        return QUOTA
    return REJECTED


class Adapter:
    """通道适配器基类。子类实现 send/poll/quota；log_path 指向动作日志。"""

    name = "base"

    def __init__(self, cfg: dict, log_path=None):
        self.cfg = cfg or {}
        self.log_path = log_path or cfg.get("log")

    def blog(self, action: str, **fields):
        if not self.log_path:
            return
        import datetime
        rec = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "action": action}
        rec.update({k: v for k, v in fields.items() if v is not None})
        try:
            path = os.path.expanduser(self.log_path)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # 子类实现以下三个方法，返回 (退出码, dict)。
    def send(self, a: dict):
        raise NotImplementedError

    def poll(self, a: dict):
        raise NotImplementedError

    def quota(self, a: dict):
        return OK, {"ok": False, "reason": "该适配器未实现 quota"}

    def run(self, argv) -> int:
        if not argv:
            print(self.__class__.__doc__ or "adapter")
            return 1
        action, a = argv[0], parse(argv[1:])
        fn = {"send": self.send, "poll": self.poll, "quota": self.quota}.get(action)
        if not fn:
            print(f"未知动作：{action}", file=sys.stderr)
            return 1
        try:
            code, obj = fn(a)
        except Exception as e:  # 适配器内部异常不能让 wf 收到脏输出
            self.blog("error", detail=f"{type(e).__name__}: {e}")
            print(json.dumps({"reason": f"适配器异常：{type(e).__name__}"}, ensure_ascii=False), flush=True)
            return UNKNOWN
        print(json.dumps(obj, ensure_ascii=False), flush=True)
        return code


def main(adapter_cls):
    """适配器模块的入口：python -m <模块> <action> --k v ...
    配置由 wf 通过环境变量 WF_TRANSPORT_JSON 指向的文件传入（不导入 wflib，避免与工具代码耦合）。"""
    path = os.environ.get("WF_TRANSPORT_JSON", "")
    try:
        cfg = json.loads(open(path, encoding="utf-8").read()) if path else {}
    except (OSError, ValueError):
        cfg = {}
    sys.exit(adapter_cls(cfg, cfg.get("log")).run(sys.argv[1:]))
