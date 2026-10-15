#!/usr/bin/env python3
"""example_echo —— 示例传输适配器，用本地文件模拟一条通道，不联网。
只用于 wf selftest 跑完整驱动循环，也是写真实适配器的参照。
状态目录由 cfg["env"]["ECHO_DIR"] 指定：
  replies/<k>.md / <k>.files   第 k 次被接受的发送对应的回复与交付文件
  next_send_code               下一次发送返回的退出码（用后删除）
  status.json                  每次发送与轮询的计数
真实适配器把这里的本地文件操作换成对具体通道的请求，接口和退出码保持不变。
"""
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from adapters.base import OK, UNKNOWN, Adapter, main  # noqa: E402


class EchoAdapter(Adapter):
    name = "example_echo"

    def _dir(self):
        d = Path(os.path.expanduser((self.cfg.get("env") or {}).get("ECHO_DIR", "")))
        if not d:
            raise RuntimeError("example_echo 需要 env.ECHO_DIR")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _status(self, d):
        p = d / "status.json"
        return json.loads(p.read_text()) if p.exists() else {"accepted": 0, "turns": {}}

    def send(self, a):
        d = self._dir()
        ctl = d / "next_send_code"
        if ctl.exists():
            code = int(ctl.read_text().strip())
            ctl.unlink()
            self.blog("rejected" if code in (2, 3, 5) else "error", detail=f"注入退出码 {code}")
            return code, ({"resets_at": "2000-01-01T00:00:00+00:00"} if code == 3 else {"reason": f"注入 {code}"})
        s = self._status(d)
        key = f'{a.get("project")}:{a.get("turn")}'
        s["accepted"] += 1
        s["turns"][key] = {"k": s["accepted"], "polls": 0}
        (d / "status.json").write_text(json.dumps(s))
        (d / "inbox").mkdir(exist_ok=True)
        if a.get("message") and os.path.exists(a["message"]):
            shutil.copy(a["message"], d / "inbox" / f'{s["accepted"]:02d}-{key.replace(":", "-")}.md')
        self.blog("send", detail=key)
        self.blog("accepted", detail=f"k={s['accepted']}")
        return OK, {"ref": json.dumps({"key": key, "k": s["accepted"]})}

    def poll(self, a):
        d = self._dir()
        s = self._status(d)
        try:
            key = json.loads(a.get("ref") or "{}")["key"]
        except Exception:
            return OK, {"status": "none"}
        t = s["turns"].get(key)
        if not t:
            return OK, {"status": "none"}
        t["polls"] += 1
        (d / "status.json").write_text(json.dumps(s))
        if t["polls"] < 2:
            return OK, {"status": "generating"}
        out = Path(a.get("out") or ".")
        out.mkdir(parents=True, exist_ok=True)
        src = d / "replies" / f'{t["k"]}.md'
        if not src.exists():
            return OK, {"status": "generating"}
        reply = out / "reply.md"
        shutil.copy(src, reply)
        files = []
        fl = d / "replies" / f'{t["k"]}.files'
        if fl.exists():
            for line in fl.read_text().splitlines():
                if line.strip():
                    dst = out / Path(line.strip()).name
                    shutil.copy(line.strip(), dst)
                    files.append(str(dst))
        self.blog("done", detail=f"reply k={t['k']}")
        return OK, {"status": "done", "reply_file": str(reply), "files": files,
                    "tools": [{"name": "bash_tool", "summary": "示例"}]}

    def quota(self, a):
        d = self._dir()
        s = self._status(d)
        payload = {"ts": "2026-01-01T00:00:00+00:00",
                   "limits": [{"name": "session", "label": "5 小时窗口", "used_pct": 10 + s["accepted"]}]}
        if a.get("out"):
            Path(a["out"]).write_text(json.dumps(payload, ensure_ascii=False))
        self.blog("quota", value=payload["limits"][0]["used_pct"], detail="session")
        return OK, {"ok": True, "limits": payload["limits"]}


if __name__ == "__main__":
    main(EchoAdapter)
