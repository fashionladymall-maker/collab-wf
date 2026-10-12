#!/usr/bin/env python3
"""自检用的传输适配器替身：模拟通道与按脚本回复的 Claude。只用于 wf selftest，不连接任何网络。
状态目录由环境变量 FAKE_DIR 指定：
  replies/<k>.md        第 k 次被接受的发送对应的回复
  replies/<k>.files     （可选）该回复交付的文件路径，每行一个
  next_send_code        （可选）下一次发送返回的退出码（用后删除），例如 3 表示额度用尽
  status.json           记录每轮的发送与轮询次数
  conv_new_fail         （可选）下一次 conv_new 失败（用后删除）；环境变量 FAKE_NO_REF 设置时，空 ref 的 poll 返回 no_ref（1.6.4）
"""
import json
import os
import shutil
import sys
from pathlib import Path

D = Path(os.environ["FAKE_DIR"])


def arg(name, default=None):
    a = sys.argv[2:]
    return a[a.index(name) + 1] if name in a else default


def load():
    p = D / "status.json"
    return json.loads(p.read_text()) if p.exists() else {"accepted": 0, "turns": {}}


def save(s):
    (D / "status.json").write_text(json.dumps(s))


def _warn() -> dict:
    """1.7.0：poll_warning 文件存在时，poll 的完成输出带上 warning（自检核对“需要注意”）。"""
    w = D / "poll_warning"
    return {"warning": w.read_text().strip()} if w.exists() else {}


def main():
    op = sys.argv[1]
    try:   # 1.7.0：记下每次调用的参数（自检核对 --conv、--project）；记不下也不影响替身本身
        with open(D / "calls.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"op": op, "argv": sys.argv[2:]}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    s = load()
    if op == "send":
        ctl = D / "next_send_code"
        if ctl.exists():
            code = int(ctl.read_text().strip())
            ctl.unlink()
            print(json.dumps({"resets_at": "2000-01-01T00:00:00+00:00"} if code == 3 else {}))
            sys.exit(code)
        turn = f'{arg("--project")}:{arg("--turn")}'
        s["accepted"] += 1
        s["turns"][turn] = {"k": s["accepted"], "polls": 0}
        (D / "inbox").mkdir(exist_ok=True)
        shutil.copy(arg("--message"), D / "inbox" / f"{s['accepted']:02d}-{turn.replace(':', '-')}.md")
        save(s)
        print(json.dumps({"ref": f"r-{turn}"}))
        return
    if op == "poll":
        if os.environ.get("FAKE_NO_REF") and arg("--ref") == "":
            print(json.dumps({"status": "no_ref"}))
            return
        turn = f'{arg("--project")}:{arg("--turn")}'
        t = s["turns"].get(turn)
        if not t:
            print(json.dumps({"status": "none"}))
            return
        t["polls"] += 1
        save(s)
        if t["polls"] < 2:
            print(json.dumps({"status": "generating"}))
            return
        out = Path(arg("--out"))
        out.mkdir(parents=True, exist_ok=True)
        src = D / "replies" / f"{t['k']}.md"
        reply = out / "reply.md"
        shutil.copy(src, reply)
        files = []
        fl = D / "replies" / f"{t['k']}.files"
        if fl.exists():
            for line in fl.read_text().splitlines():
                if line.strip():
                    dst = out / Path(line.strip()).name
                    shutil.copy(line.strip(), dst)
                    files.append(str(dst))
        print(json.dumps({"status": "done", "reply_file": str(reply), "files": files, **_warn(),
                          "tools": [{"name": "bash_tool", "summary": "测试"}]}))
        return
    if op == "conv_new":
        ctl = D / "conv_new_fail"
        if ctl.exists():
            ctl.unlink()
            print(json.dumps({"ok": False, "error": "测试：建对话失败"}))
            sys.exit(4)
        s["convs"] = s.get("convs", 0) + 1
        save(s)
        print(json.dumps({"ok": True, "conv": f"conv-{s['convs']}", "settings_ok": True}))
        return
    if op == "quota":
        Path(arg("--out")).write_text(json.dumps({"ts": "2026-01-01T00:00:00+00:00", "limits": [
            {"name": "session", "label": "5 小时窗口", "used_pct": 10 + s["accepted"]}]}))
        return
    sys.exit(9)


if __name__ == "__main__":
    main()
