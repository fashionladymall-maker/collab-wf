#!/usr/bin/env python3
"""collab-wf：Claude × 执行端 协作工作流工具。运行 wf help 查看全部命令。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wflib import asks, machine, ops, release  # noqa: E402
from wflib.core import WfError, load_registry, open_project, tool_version  # noqa: E402

HELP = """collab-wf {v} —— Claude × 执行端 协作工作流

日常一轮（按顺序）：
  wf status                          现在走到哪一步、退出条件、下一步做什么
  wf report                          根据本轮证据生成回报草稿（进入 COMPOSE，轮次 +1）
  wf check-outgoing <文件> [--kind report|task]   发送前检查：密钥扫描、结构、版本与档位
  wf mark sent --message-file F --kind report --effort high --model M     发送成功后记账
  wf mark replied --reply-file F     回复结束后记账（耗时自动计算）
  wf intake <回复文件>               解析五个固定块；齐全时自动进入 REVIEW
  wf review [--accept] [--user-approved "用户原话"]   命令风险分级；接受后才能执行
  wf apply <zip>                     落地项目版本（先校验后复制、按清单删除、提交并打标签）
  wf exec -- <命令>                  执行并自动保存输出、退出码、耗时
  wf verify list | set N pass|fail|deviation|skip [--evidence c01,c02] [--note …]
  wf note --kind deviation|decision|info "文字"
  wf wait --until 2026-10-16T08:00:00+08:00 --reason "第一周对账"

状态控制：
  wf go <状态> [--reason …]          按工作流定义转换
  wf fail --reason … [--symptom SD-1]   记录本步失败；达到上限自动进入 BLOCKED
  wf block --reason … / wf unblock --to <状态> --note …
  wf resume --note "用户决定：…" [--to <状态>]   从 AWAITING_USER / QUOTA_WAIT 恢复
  wf force <状态> --reason …         人工更正（会被标记）
  wf step [状态]                     查看某一步的做法、条件、证据与异常处置

自动驱动（由系统定时唤醒调用）：
  wf run [--max-seconds 600]          推进当前项目到需要等待或需要用户为止（退出码 0/10/20/30）
  wf tick [--max-seconds 900]         推进全部项目（多项目调度）；有自己定时任务的项目跳过（1.7.2）
  wf --project <名> tick              只推进这一个项目（它自己的定时任务用，1.7.2）
  wf explain                          现在卡在哪、为什么、需要谁做什么
  wf kick --message <任务文件>         发起一轮新任务，交给驱动器
  wf kick --kind report               由本轮证据生成标准回报（含终端原文与“需要注意”），交给驱动器发送（1.7.1）
  wf approve --note "…"               用户批准待执行的内容或解除熔断
  wf pause / wf unpause --note / wf undo --note    暂停、恢复、回退上一个版本并暂停
  wf audit [--n 40]                   自动执行过的一切：做了什么、依据什么、结果如何
  wf goals [check | approve --note | confirm <编号> --note]    目标与完成判据
  wf policy [approve --note]          自动执行的命令白名单
  wf transport [test]                 检查传输适配器配置 / 黑盒一致性检查（不发送消息）
  wf effort                           档位与角色规则，以及按档位、角色的复盘统计
  wf decisions [--tsv] [--turn N]     决策日志：做了什么、为什么、证据、结果
  wf conv [new --note … [--conv <id>]]  当前对话的体量与对话 id；开新对话后记录起点（--conv 同时登记新对话）
  wf conv bind --conv <id> --note …   只登记本项目当前所在的对话（不换对话、不清计数；1.7.0，迁移用）
  wf schedule plist | status          生成系统定时器配置（launchd）/ 检查定时器是否在运行
  wf --project <名> schedule plist | release  这个项目自己的定时任务：生成配置并登记 / 撤回登记（1.7.2）

项目与版本：
  wf init --name N [--root 路径] [--adopt [--turn N --state S]]   新建或接入项目
  wf projects                        已接入的项目
  wf rollback --to vX.Y              项目回滚到某个版本（生成新提交，不改写历史）
  wf pin                             项目固定到当前工具版本
  wf bootstrap --reason …            生成开新对话的说明消息与仓库 zip
  wf doctor                          健康检查
  wf fingerprint [--files]           仓库指纹（回报草稿自动附带；与 Claude 的副本比对是否同步）
  wf look [--full]                   看一次 Claude 页面，更新看板上的文本快照与截图（--full 截整页；适配器的 snapshot 动作，见 docs/SNAPSHOT.md）
  wf said --text "…" [--kind chat]   逐字记下用户的原话（看板“对话记录”里的“用户”）
  wf signal add|clear|list …         外部信号：把错误、卡死、疑难投进 inbox（--key --title [--why] [--for executor|user] [--ttl 秒]）
  wf watchdog [--write]              卡死判定（只读）；--write 写成信号并重写 inbox，由独立于驱动器的定时进程调用
  wf task [list] | wf task done <编号> --note "结果"   执行端待办（Claude 在驱动指令里写“执行端 N：…”，做完逐项勾掉）
  wf notify test | status            系统通知：发一条测试通知 / 查看是否开启（wf tick 跑完后自动通知新的待决事项）
  wf sync [--dry-run] [--force]      项目上下文同步到执行端配置的仓库（docs/SYNC.md）
  wf snapshot <文件,…> [--max-chars N] [--out F]   按 Claude 的要求生成有界的文件包
  wf bridge-check --log <桥接日志> [--hours 24] [--grace-s 900]    核对桥接工具日志是否满足行为约定
  wf drill review --kind block|timeout --dir <空目录> --review <审查程序>    独立审查的驱动器侧演练（docs/DRILL.md）

工具自身：
  wf version | wf self-init | wf self-update [--auto] <zip> | wf self-rollback <版本> | wf selftest
  wf serve [--port 8765] [--daemon | --stop | --status]   工作流看板

通用选项：--project <名称> 指定项目（默认按当前目录查找）；--json 输出 JSON
"""


def _p(args):
    return open_project(getattr(args, "project", None))


def _print_status(s):
    m, sec = divmod(s["dwell_s"], 60)
    h, m = divmod(m, 60)
    dwell = f"{h} 小时 {m} 分" if h else f"{m} 分 {sec} 秒"
    print(f"项目 {s['project']} · 第 {s['turn']} 轮 · {s['state']}（{s['name']}）· 负责：{s['owner_name']} · 已停留 {dwell}")
    if s["stuck"]:
        print(f"⚠ 停留超过上限 {s['max_dwell_s'] // 60} 分钟")
    if s["attempts"]:
        print(f"⚠ 本步已失败 {s['attempts']} 次（上限 {s['max_attempts'] or '无'}）")
    if s["until"]:
        print(f"等待到 {s['until']}：{s['wait_reason'] or ''}" + (f"（已逾期 {s['overdue_s'] // 3600} 小时）" if s["overdue_s"] else ""))
    if s["block_reason"]:
        print(f"阻塞原因：{s['block_reason']}（来自 {s['blocked_from']}）")
    if s["state"] == "REVIEW":
        print(f"评审：{'已接受' if s['review_accepted'] else '未接受'}")
    print(f"\n{s['summary']}")
    print("\n怎么做：")
    for x in s["how"]:
        print(f"  - {x}")
    print("退出条件：")
    for x in s["exit"]:
        print(f"  - {x}")
    print(f"可进入：{'、'.join(s['next'])}")
    if s["stuck"] or s["attempts"] or s["state"] == "BLOCKED":
        print("\n本步已知异常与处置：")
        for p in s["problems"]:
            print(f"  [{p['id']}] {p['symptom']} → 判定：{p['judge']} → 处置：{p['action']}" + (f"（台账 {p['ledger']}）" if p.get("ledger") else ""))


def _record_user(inst, kind, item, said):
    """逐字记录用户原话与决定码，写入事件流与决策日志（用户在看板与审计中可以核对）。"""
    from wflib import roles
    said = (said or "").strip()
    if not said:
        raise WfError("需要 --said：用户在聊天窗口里的原话（逐字）；交给执行端的事项也可以由执行端代决：--by executor --reason …")
    inst.event("user_decision", kind=kind, code=item["code"], said=said[:500], title=item["title"], by="user")
    roles.decide(inst, "user", f"{item['title']}（决定码 {item['code']}）", f"用户原话：{said[:300]}", "", "已执行", by="user")
    from wflib import page
    page.record_user(inst, said, kind=kind, title=item["title"], code=item["code"], event=False)   # 1.5.3：对话记录里也有“用户”


def _decide_as(inst, kind, item, args) -> tuple:
    """1.6.5（W43）：默认要用户原话（--said）；--by executor --reason 为执行端代决，只限交给执行端的事项。返回 (说明, by)。"""
    if getattr(args, "by", None) == "executor":
        return asks.executor_decision(inst, kind, item, getattr(args, "reason", None)), "executor"
    _record_user(inst, kind, item, getattr(args, "said", None))
    return args.said, "user"


def _by_args(x):
    x.add_argument("--by", choices=["executor"], help="执行端代决（只限交给执行端的事项），须同时写 --reason")
    x.add_argument("--reason", help="执行端代决的理由")


def tool_now():
    from wflib.core import now_iso
    return now_iso()


def _print_explain(e):
    print(f"\n现在：{e['where']}\n原因：{e['why']}\n需要：{e['who']} —— {e['what']}")


def _print_audit(inst, n):
    from wflib.core import read_jsonl
    names = {"auto": "自动处置", "tick": "驱动", "transition": "转换", "approve": "用户批准", "goal_check": "目标检查",
             "goal_confirm": "用户确认", "pause": "暂停", "unpause": "恢复", "apply": "落地", "exec": "执行",
             "verify": "验收", "review": "评审", "driver_interrupted": "驱动中断", "rollback": "回滚"}
    rows = [e for e in read_jsonl(inst.events_path) if e.get("type") in names][-n:]
    for e in rows:
        t = e.get("type")
        if t == "auto":
            d = f"规则 {e.get('rule')} → {e.get('action')}（第 {e.get('attempt')} 次）：{e.get('detail', '')}"
        elif t == "tick":
            d = f"exit={e.get('exit')}：{e.get('note', '')}"
        elif t == "transition":
            d = f"{e.get('frm')} → {e.get('to')}：{e.get('reason') or ''}" + ("（人工更正）" if e.get("forced") else "")
        elif t == "exec":
            d = f"{e.get('cid')} exit={e.get('exit_code')} {e.get('cmd')}"
        elif t == "verify":
            d = f"清单第 {e.get('item')} 项：{e.get('verdict')}"
        elif t == "goal_check":
            d = f"{e.get('goal')}：{'通过' if e.get('ok') else '未通过'} {e.get('why') or ''}"
        else:
            d = " ".join(f"{k}={v}" for k, v in e.items() if k not in ("seq", "ts", "type", "turn", "state", "by"))
        print(f"{e['ts'][5:16].replace('T', ' ')}  第 {e.get('turn')} 轮  [{e.get('by', '')}] {names[t]}：{d}")


def _transport_test() -> int:
    """传输适配器的黑盒一致性检查：不发送任何消息，只验证约定中可以安全验证的部分。"""
    import os as _os
    import tempfile as _tf
    import time as _time
    from wflib import anomaly
    from wflib.core import LOCAL
    cfg = anomaly.transport_cfg()
    res = []
    ok = lambda name, cond, msg="": res.append((bool(cond), name, msg))
    loc = anomaly.adapter_location()
    if loc["mode"] == "module":
        ok("适配器模块可找到", loc["file"], f"{loc['module']} 未找到（检查 module 与 module_path）")
        ok("通道适配器位于工具目录之外", not loc["inside_tool"], f"{loc['file']} 在工具目录内，会随工具打包与覆盖")
    else:
        for op in ("send", "poll"):
            p = cfg.get(op)
            ok(f"{op} 已配置且可执行", p and _os.access(p, _os.X_OK), p or "未配置")
    with _tf.TemporaryDirectory() as td:
        t0 = _time.time()
        r = anomaly.call("poll", ["--project", "__wf_probe__", "--turn", "999999", "--ref", "", "--out", td], timeout=60)
        # 1.6.4：空 ref 返回 no_ref 是 TRANSPORT.md 1.5.1 的约定（“没法查”不是“查了没有”），此前这里误判为不合规
        ok("poll 对不存在的轮次返回 none 或 no_ref（不能报告完成或生成中）", r["code"] == 0 and r["data"].get("status") in ("none", "no_ref"),
           f"退出码 {r['code']}，输出 {r['raw'][:160]}")
        ok("poll 在 60 秒内返回", _time.time() - t0 < 60)
        r2 = anomaly.call("poll", ["--project", "__wf_probe__", "--turn", "999999", "--ref", "", "--out", td], timeout=60)
        ok("poll 幂等（重复调用结果相同）", r2["data"].get("status") == r["data"].get("status"))
        if cfg.get("per_project_conv"):   # 1.7.0（ISOLATION.md S1）：适配器认得 --conv，且不会因此报告完成或生成中
            r4 = anomaly.call("poll", ["--project", "__wf_probe__", "--turn", "999999", "--ref", "", "--out", td,
                                       "--conv", "__wf_probe_conv__"], timeout=60)
            ok("声明了 per_project_conv：poll 接受 --conv，对不存在的轮次返回 none 或 no_ref",
               r4["code"] == 0 and r4["data"].get("status") in ("none", "no_ref"), f"退出码 {r4['code']}，输出 {r4['raw'][:160]}")
        if cfg.get("quota"):
            out = Path(td) / "usage.json"
            r3 = anomaly.call("quota", ["--project", "__wf_probe__", "--out", out], timeout=60)
            data = __import__("json").loads(out.read_text()) if out.exists() else {}
            ok("quota 写出约定格式（limits 列表）", r3["code"] == 0 and isinstance(data.get("limits"), list), r3["raw"][:160])
    log = cfg.get("log")
    if log:
        from wflib import ops as _ops
        bad = [x for x in _ops.bridge_check(Path(_os.path.expanduser(log)), 24) if not x["ok"]]
        ok("桥接动作日志满足行为约定（最近 24 小时）", not bad, "；".join(f"{x['rule']}：{x['msg']}" for x in bad))
    else:
        ok("transport.json 中声明了动作日志路径（log）", False, "未声明，无法自动核对行为约定")
    for good, name, msg in res:
        print(f"{'✓' if good else '✗'} {name}" + (f"  —— {msg}" if msg and not good else ""))
    print("说明：发送与“正在生成”的判据无法在不消耗额度的情况下验证，由桥接日志与驱动器的异常处置兜底。")
    return 0 if all(g for g, _, _ in res) else 1


def _safe_name(name) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(name or ""))[:80] or "_"


def _own_sched() -> dict:
    """1.7.2（ISOLATION.md S3）：登记了自己定时任务的项目（local/schedule.json 的 projects）。"""
    from wflib.core import LOCAL, read_json
    return dict(((read_json(LOCAL / "schedule.json", {}) or {}).get("projects")) or {})


def _schedule(args):
    from wflib.core import LOCAL, WF_HOME, load_registry, secs_since
    from wflib.core import Instance
    if args.action == "plist":
        from wflib.core import write_json as _wj
        from wflib.core import read_json as _rj, now_iso as _now
        sj = _rj(LOCAL / "schedule.json", {}) or {}   # 1.7.2：合并写，保留各项目自己的定时任务记录
        py = sys.executable
        label = "local.collab-wf.tick"
        pargs, logf = "", LOCAL / "tick.log"
        if args.project:   # 1.7.2（ISOLATION.md S3）：这个项目自己的定时任务
            if args.project not in [p["name"] for p in load_registry()["projects"]]:
                raise WfError(f"注册表里没有项目 {args.project}（wf projects 列出全部）")
            label = f"local.collab-wf.tick.{_safe_name(args.project)}"
            pargs = f"    <string>--project</string>\n    <string>{args.project}</string>\n"
            logf = LOCAL / f"tick-{_safe_name(args.project)}.log"
            sj.setdefault("projects", {})[args.project] = {"interval": args.interval, "max_seconds": args.max_seconds,
                                                            "label": label, "since": _now()}
        else:
            sj.update(interval=args.interval, max_seconds=args.max_seconds)
        _wj(LOCAL / "schedule.json", sj)
        print(f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{py}</string>
    <string>{WF_HOME / "wf.py"}</string>
{pargs}    <string>tick</string>
    <string>--max-seconds</string>
    <string>{args.max_seconds}</string>
  </array>
  <key>StartInterval</key><integer>{args.interval}</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>{WF_HOME / "bin"}:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    <key>LANG</key><string>en_US.UTF-8</string>
  </dict>
  <key>StandardOutPath</key><string>{logf}</string>
  <key>StandardErrorPath</key><string>{logf}</string>
</dict>
</plist>''')
        print(f"<!-- 保存为 ~/Library/LaunchAgents/{label}.plist，然后：launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/{label}.plist -->",
              file=sys.stderr)
        if args.project:
            print(f"<!-- 登记后全局定时任务不再推进 {args.project}；请立即加载上面的配置，否则它没人推进（心跳会在 15 分钟后报停）。"
                  f"撤回：wf --project {args.project} schedule release，再 launchctl bootout gui/$(id -u)/{label} -->", file=sys.stderr)
        return
    if args.action == "release":   # 1.7.2：撤回某个项目自己的定时任务，回到全局定时任务推进
        from wflib.core import read_json as _rj, write_json as _wj
        if not args.project:
            raise WfError("用法：wf --project <名> schedule release")
        sj = _rj(LOCAL / "schedule.json", {}) or {}
        rec = (sj.get("projects") or {}).pop(args.project, None)
        _wj(LOCAL / "schedule.json", sj)
        if rec:
            print(f"已撤回 {args.project} 自己的定时任务登记，全局定时任务下次起推进它；"
                  f"另请卸载：launchctl bootout gui/$(id -u)/{rec.get('label')}")
        else:
            print(f"{args.project} 没有登记自己的定时任务")
        return
    reg = load_registry()["projects"]
    worst = 0
    for p in reg:
        try:
            inst = Instance(Path(p["root"]))
        except WfError as e:
            print(f"  {p['name']}：不可用（{e}）")
            continue
        if inst.cfg.get("auto") is False:   # 1.6.9（N9）：驱动器按设计跳过它，旧的运行记录不说明定时器停了
            print(f"  {inst.name}：已静音（wf.json auto=false，驱动器跳过），不计入")
            continue
        last = (inst.state.get("driver") or {}).get("last") or {}
        age = secs_since(last.get("ts")) if last else None
        if age is None:
            print(f"  {inst.name}：驱动器从未运行")
            worst = max(worst, 1)
        else:
            own = _own_sched().get(inst.name) or {}   # 1.7.2：有自己定时任务的项目按它自己的周期判断
            iv = int(own.get("interval") or args.interval)
            stale = age > 3 * iv
            if own:
                print(f"  {inst.name}：自己的定时任务 {own.get('label')}，周期 {iv} 秒")
            worst = max(worst, 1 if stale else 0)
            print(f"  {inst.name}：最近一次运行 {int(age // 60)} 分钟前，退出码 {last.get('exit')}" + ("  ← 超过 3 个周期，定时器可能没有运行" if stale else ""))
    log = LOCAL / "tick.log"
    if log.exists():
        print(f"日志：{log}（{log.stat().st_size // 1024} KB）")
    sys.exit(worst)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wf", add_help=False)
    ap.add_argument("--project")
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("help")
    sub.add_parser("version")
    sub.add_parser("status")
    x = sub.add_parser("step"); x.add_argument("sid", nargs="?")
    x = sub.add_parser("go"); x.add_argument("to"); x.add_argument("--reason")
    x = sub.add_parser("force"); x.add_argument("to"); x.add_argument("--reason", required=True)
    x = sub.add_parser("fail"); x.add_argument("--reason", required=True); x.add_argument("--symptom")
    x = sub.add_parser("block"); x.add_argument("--reason", required=True)
    x = sub.add_parser("unblock"); x.add_argument("--to", required=True); x.add_argument("--code", required=True)
    x.add_argument("--said", "--note", dest="said"); _by_args(x)
    x = sub.add_parser("resume"); x.add_argument("--note", required=True); x.add_argument("--to")
    x = sub.add_parser("resend"); x.add_argument("--code", required=True)
    x.add_argument("--said", "--note", dest="said"); _by_args(x)
    x = sub.add_parser("wait"); x.add_argument("--until", required=True); x.add_argument("--reason", required=True)
    x = sub.add_parser("mark"); x.add_argument("what", choices=["sent", "replied"])
    x.add_argument("--message-file"); x.add_argument("--reply-file"); x.add_argument("--kind")
    x.add_argument("--effort", choices=ops.EFFORTS); x.add_argument("--model")
    x.add_argument("--usage-session", type=float); x.add_argument("--usage-weekly", type=float)
    x.add_argument("--tools-json", help="回复中的工具调用摘要，JSON 数组 [{name, summary}]")
    x.add_argument("--ref", help="适配器 send 返回的 ref；手动发送时登记它，驱动器才能接续本轮")
    x = sub.add_parser("intake"); x.add_argument("reply_file")
    x = sub.add_parser("review"); x.add_argument("--accept", action="store_true"); x.add_argument("--user-approved")
    x = sub.add_parser("apply"); x.add_argument("zip")
    x = sub.add_parser("rollback"); x.add_argument("--to", required=True)
    x = sub.add_parser("exec"); x.add_argument("--label"); x.add_argument("--timeout", type=float)
    x.add_argument("command", nargs=argparse.REMAINDER)
    x = sub.add_parser("verify"); x.add_argument("action", choices=["list", "set"]); x.add_argument("n", nargs="?", type=int)
    x.add_argument("verdict", nargs="?"); x.add_argument("--evidence"); x.add_argument("--note")
    x = sub.add_parser("note"); x.add_argument("--kind", required=True); x.add_argument("text")
    x = sub.add_parser("report"); x.add_argument("--out")
    x = sub.add_parser("check-outgoing"); x.add_argument("file"); x.add_argument("--kind", default="report", choices=["report", "task"])
    x = sub.add_parser("init"); x.add_argument("--name"); x.add_argument("--root", default=".")
    x.add_argument("--adopt", action="store_true"); x.add_argument("--turn", type=int); x.add_argument("--state")
    sub.add_parser("projects")
    sub.add_parser("pin")
    x = sub.add_parser("bootstrap"); x.add_argument("--reason", required=True)
    sub.add_parser("doctor")
    x = sub.add_parser("fingerprint"); x.add_argument("--files", action="store_true"); x.add_argument("--path")
    x = sub.add_parser("snapshot"); x.add_argument("paths", nargs="+"); x.add_argument("--max-chars", type=int, default=40000)
    x.add_argument("--out")
    x = sub.add_parser("bridge-check"); x.add_argument("--log"); x.add_argument("--hours", type=float)
    x.add_argument("--list-actions", action="store_true")
    x.add_argument("--grace-s", type=float, help="发送后等结果的宽限秒数（默认 900）")   # 1.6.8
    x = sub.add_parser("drill"); x.add_argument("what", choices=["review"]); x.add_argument("--kind", required=True, choices=["block", "timeout"])
    x.add_argument("--dir", required=True); x.add_argument("--review", required=True)
    x.add_argument("--review-timeout-s", type=int, default=900)   # 1.6.8（P1-D）：docs/DRILL.md
    x = sub.add_parser("run"); x.add_argument("--max-seconds", type=float, default=600); x.add_argument("--max-steps", type=int, default=40)
    x = sub.add_parser("tick"); x.add_argument("--max-seconds", type=float, default=900)
    sub.add_parser("explain")
    x = sub.add_parser("kick"); x.add_argument("--message"); x.add_argument("--kind", default="task", choices=["task", "report"])
    x.add_argument("--effort", default="high", choices=ops.EFFORTS)
    x = sub.add_parser("approve"); x.add_argument("--code", required=True); x.add_argument("--said", "--note", dest="said"); _by_args(x)
    x = sub.add_parser("pause"); x.add_argument("--note", default="")
    x = sub.add_parser("said"); x.add_argument("--text", required=True); x.add_argument("--kind", default="chat")
    x = sub.add_parser("notify"); x.add_argument("action", choices=["test", "status"])
    x = sub.add_parser("sync"); x.add_argument("--dry-run", action="store_true"); x.add_argument("--force", action="store_true")
    x = sub.add_parser("look"); x.add_argument("--full", action="store_true")
    x = sub.add_parser("unpause"); x.add_argument("--code", required=True); x.add_argument("--said", "--note", dest="said", required=True)
    x = sub.add_parser("undo"); x.add_argument("--note", required=True)
    x = sub.add_parser("audit"); x.add_argument("--n", type=int, default=40)
    x = sub.add_parser("goals"); x.add_argument("action", nargs="?", default="show", choices=["show", "check", "approve", "confirm"])
    x.add_argument("gid", nargs="?"); x.add_argument("--said", "--note", dest="said"); x.add_argument("--code"); _by_args(x)
    x = sub.add_parser("policy"); x.add_argument("action", nargs="?", default="show", choices=["show", "approve"])
    x.add_argument("--said", "--note", dest="said"); x.add_argument("--code"); _by_args(x)
    x = sub.add_parser("ask"); x.add_argument("--new", action="store_true"); x.add_argument("--mark-shown", action="store_true")
    sub.add_parser("panel")
    x = sub.add_parser("signal"); x.add_argument("action", choices=["add", "clear", "list"]); x.add_argument("--key")
    x.add_argument("--title"); x.add_argument("--why", default=""); x.add_argument("--for", dest="for_", default="executor",
                                                                                 choices=["executor", "user"])
    x.add_argument("--action-text", dest="action_text", default=""); x.add_argument("--ttl", type=int, default=0)
    x = sub.add_parser("watchdog"); x.add_argument("--write", action="store_true")
    x = sub.add_parser("task"); x.add_argument("action", nargs="?", default="list", choices=["list", "done"])
    x.add_argument("tid", nargs="?"); x.add_argument("--note", default="")
    x = sub.add_parser("transport"); x.add_argument("action", nargs="?", default="check", choices=["check", "test"])
    sub.add_parser("effort")
    x = sub.add_parser("decisions"); x.add_argument("--n", type=int, default=40); x.add_argument("--turn", type=int)
    x.add_argument("--tsv", action="store_true")
    x = sub.add_parser("conv"); x.add_argument("action", nargs="?", default="show", choices=["show", "new", "bind"]); x.add_argument("--note")
    x.add_argument("--conv")   # 1.7.0（ISOLATION.md S1）：本项目的对话 id
    x = sub.add_parser("schedule"); x.add_argument("action", choices=["plist", "status", "release"])
    x.add_argument("--interval", type=int, default=300); x.add_argument("--max-seconds", type=int, default=540)
    sub.add_parser("self-init")
    sub.add_parser("self-check")
    sub.add_parser("self-manifest")
    x = sub.add_parser("self-update"); x.add_argument("zip"); x.add_argument("--skip-test", action="store_true")
    x.add_argument("--auto", action="store_true")   # 1.5.8：驱动器自动安装；改动护栏文件的版本会被拒绝（DRIVER.md 第 18 节）
    x.add_argument("--allow-same", action="store_true")
    x = sub.add_parser("self-rollback"); x.add_argument("tag")
    sub.add_parser("selftest")
    x = sub.add_parser("serve"); x.add_argument("--port", type=int, default=8765)
    g = x.add_mutually_exclusive_group(); g.add_argument("--daemon", action="store_true")
    g.add_argument("--stop", action="store_true"); g.add_argument("--status", action="store_true")

    args = ap.parse_args(argv)
    c = args.cmd or "help"
    out = lambda obj: print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))

    if c == "help":
        print(HELP.format(v=tool_version()))
    elif c == "version":
        from wflib.core import WF_HOME, git
        desc = git(["describe", "--tags", "--always", "--dirty"], WF_HOME, check=False).stdout.strip() \
            if (WF_HOME / ".git").exists() else "未初始化版本库"
        print(f"collab-wf {tool_version()}（{desc}）  安装位置：{WF_HOME}")
        vs = release.self_versions()
        if vs:
            print("可回滚的版本：" + "、".join(v["tag"] for v in vs))
        try:
            inst = open_project(args.project)
            print(f"当前项目 {inst.name} 固定版本：{inst.cfg.get('wf_version')}")
        except WfError:
            pass
    elif c == "status":
        s = machine.status(_p(args))
        out(s) if args.json else _print_status(s)
    elif c == "step":
        d = machine.definition()
        sid = (args.sid or _p(args).state["state"]).upper()
        sd = machine.sdef(sid)
        if args.json:
            out(sd)
        else:
            print(f"{sid}（{sd['name']}）负责：{d['owners'].get(sd.get('owner'))}\n{sd.get('summary', '')}")
            for title, key in (("怎么做", "how"), ("进入条件", "entry"), ("退出条件", "exit"), ("证据", "evidence")):
                print(f"{title}：")
                for v in sd.get(key, []) or ["（无）"]:
                    print(f"  - {v}")
            if sd.get("max_dwell_s"):
                print(f"停留上限：{sd['max_dwell_s'] // 60} 分钟")
            if sd.get("max_attempts"):
                print(f"失败上限：{sd['max_attempts']} 次（达到后自动进入 BLOCKED）")
            print(f"可进入：{'、'.join(sd.get('next', []))}\n已知异常：")
            for p in sd.get("problems", []):
                print(f"  [{p['id']}] {p['symptom']}\n      判定：{p['judge']}\n      处置：{p['action']}"
                      + (f"\n      台账：{p['ledger']}" if p.get("ledger") else ""))
    elif c == "go":
        inst = _p(args)
        if args.to.upper() == "COMPOSE" and inst.state["state"] == "VERIFY":
            print("提示：回报通常用 wf report 生成（它会自动进入 COMPOSE）。")
        machine.go(inst, args.to, reason=args.reason)
        _print_status(machine.status(inst))
    elif c == "force":
        inst = _p(args)
        machine.go(inst, args.to, reason=args.reason, force=True, by="manual")
        print(f"已人工更正到 {args.to.upper()}（已记录为人工更正）")
    elif c == "fail":
        inst = _p(args)
        st = machine.fail(inst, args.reason, symptom=args.symptom)
        print(f"已记录失败。当前：{st['state']}" + ("（已达失败上限，自动阻塞）" if st["state"] == "BLOCKED" else f"，第 {st['attempts']} 次"))
    elif c == "block":
        inst = _p(args)
        machine.go(inst, "BLOCKED", reason=args.reason, block_reason=args.reason, blocked_from=inst.state["state"])
        print(f"已阻塞：{args.reason}")
    elif c == "unblock":
        inst = _p(args)
        if inst.state["state"] != "BLOCKED":
            raise WfError("当前不是 BLOCKED")
        it = asks.verify(inst, "unblock", args.code)
        note, by = _decide_as(inst, "unblock", it, args)
        machine.resume(inst, note, to=args.to, by=by)
        print(f"已解除阻塞，进入 {args.to.upper()}")
    elif c == "resend":
        # 1.5.1：发送结果不明、确认没有送达时，清除发送意图并重发（不再手改 state.json）。
        # 1.6.5（W43）：也覆盖“适配器报已接受、却始终没有落地”（SEND_NOT_LANDED）：恢复本轮消息为待发
        inst = _p(args)
        st = inst.state
        intent = st.get("send_intent") or {}
        nl = st.get("not_landed") or {}
        if st["state"] != "BLOCKED" or not (intent.get("status") == "pending" or nl.get("turn") == st.get("turn")):
            raise WfError("当前没有因“发送结果不明”或“发送后没有落地”而阻塞的消息，不需要 resend。")
        it = asks.verify(inst, "unblock", args.code)
        note, by = _decide_as(inst, "unblock", it, args)
        if intent.get("status") == "pending":
            st["send_intent"] = None
            inst.event("send_intent_cleared", note=note, intent_ts=intent.get("ts"), by=by)
            msg = "已清除结果不明的发送意图；驱动器下次运行会重新发送本轮消息"
        else:
            f = Path(nl.get("file") or "")
            if not nl.get("file") or not f.is_file():
                raise WfError(f"找不到本轮发出的消息原文：{nl.get('file')}")
            st["outgoing"] = {k: nl.get(k) for k in ("turn", "file", "kind", "effort")}
            st.pop("not_landed", None)
            st.pop("send_ref", None)
            cv = st.get("conv") or {}
            if cv.get("turns"):
                cv["turns"] = int(cv["turns"]) - 1   # 没落地的那一次不算进对话体量
            inst.event("resend_not_landed", note=note, file=f.name, by=by)
            msg = "已恢复本轮消息为待发；驱动器下次运行会重新发送（上一次发送没有落地）"
        inst.save()
        machine.resume(inst, note, to="SEND", by=by)
        print(msg)
    elif c == "resume":
        inst = _p(args)
        if inst.state.get("state") == "AWAITING_USER" and inst.state.get("return_to") == "REVIEW" and not args.to:
            # 1.5.1：手动流程里回到评审再 wf review --accept 是正常用法；但驱动器会重新评审、再次停下（第 43 轮原地打转）
            print("提示：回到评审后，驱动器会重新评审并再次停下。由驱动器推进时，要批准命令请用 "
                  "wf approve --code <决定码> --said <原话>（wf ask 可查决定码）。", file=__import__("sys").stderr)
        st = machine.resume(inst, args.note, to=args.to)
        print(f"已恢复到 {st['state']}")
    elif c == "wait":
        from wflib.core import parse_iso
        if parse_iso(args.until) is None:
            raise WfError("时间格式应为 ISO 8601，例如 2026-10-16T08:00:00+08:00")
        inst = _p(args)
        machine.go(inst, "WAIT_SCHEDULE", reason=args.reason, until=parse_iso(args.until).isoformat(), wait_reason=args.reason)
        print(f"等待到 {args.until}：{args.reason}")
    elif c == "mark":
        inst = _p(args)
        if args.what == "sent":
            ops.mark_sent(inst, args.message_file, args.kind, args.effort, args.model, args.usage_session, args.usage_weekly, ref=args.ref)
            print(f"已记录发送（第 {inst.state['turn']} 轮），进入 WAIT_REPLY"
                  + ("" if args.ref else "。提示：未提供 --ref，驱动器无法自动接续本轮；要全自动请让适配器返回 ref 并传入"))
        else:
            tools = json.loads(args.tools_json) if args.tools_json else None
            ops.mark_replied(inst, args.reply_file, tools, args.usage_session, args.usage_weekly)
            print("已记录收到回复，进入 INTAKE。下一步：wf intake <回复文件>")
    elif c == "intake":
        inst = _p(args)
        r = ops.intake(inst, Path(args.reply_file))
        if args.json:
            out({k: v for k, v in r.items() if k != "blocks"})
        else:
            print(f"固定块：齐全" if not r["blocks_missing"] else f"缺少固定块：{'、'.join(r['blocks_missing'])}（IN-1）")
            print(f"命令 {len(r['commands'])} 条；回报清单 {len(r['checklist'])} 项；需要用户决定 {len(r['user_decisions'])} 项；"
                  f"建议档位 {r['effort'] or '未写明'}；交付文件 {', '.join(r['zips']) or '无'}")
            for i, cmd in enumerate(r["commands"], 1):
                print(f"  {i:>2}. {cmd}")
            print(f"当前：{inst.state['state']}")
    elif c == "review":
        inst = _p(args)
        r = ops.review(inst, accept=args.accept, user_approved=args.user_approved)
        if args.json:
            out(r)
        else:
            for i, row in enumerate(r["rows"], 1):
                why = f"（{'、'.join(row['why'])}）" if row["why"] else ""
                print(f"  {i:>2}. [{row['level']}] {row['cmd']}{why}")
            print(f"需用户批准 {r['need_user']} 条，谨慎 {r['caution']} 条")
            if r.get("moved_to"):
                print("有需要用户批准的命令，已进入 AWAITING_USER。用户批准后：wf resume --note \"用户原话\"，"
                      "再 wf review --accept --user-approved \"用户原话\"")
            elif r["accepted"]:
                print("评审已接受。下一步：有交付版本 wf apply <zip>，否则 wf exec -- <命令>")
            else:
                print("确认无误后：wf review --accept")
    elif c == "apply":
        inst = _p(args)
        st = inst.state["state"]
        if st == "REVIEW":
            if not inst.state.get("turn_flags", {}).get("review_accepted"):
                raise WfError("本轮评审尚未接受：先 wf review，确认后 wf review --accept")
            machine.go(inst, "APPLY", by="wf")
        elif st not in ("APPLY", "EXECUTE"):
            raise WfError(f"当前是 {st}，落地版本需要在评审接受之后进行。")
        try:
            r = release.apply_zip(inst, Path(args.zip))
        except WfError as e:
            machine.fail(inst, str(e).splitlines()[0], symptom="AP")
            raise
        if inst.state["state"] == "APPLY":
            machine.go(inst, "EXECUTE", by="wf", reason=f"已落地 {r['version']}")
        if args.json:
            out(r)
        else:
            if r["commit"]:
                print(f"已落地并提交：release {r['version']}（{r['commit']}），变更 {len(r['changed'])} 个文件（A 新增 / M 修改 / D 删除）")
                for f in r["changed"][:30]:
                    print(f"  {f}")
            else:
                print(f"没有变化，无需提交（{r['version']}）")
            for f in r["deleted"]:
                print(f"  已按清单删除：{f}")
            if r.get("config"):
                print(f"  已按清单更新 workflow/wf.json：{'、'.join(r['config'])}")
            if r.get("notes"):
                print(f"版本说明：{r['notes']}")
            for h in r["hints"]:
                print(f"提示：{h}")
    elif c == "rollback":
        r = release.rollback(_p(args), args.to)
        print(f"已回滚：{r['from']} → {r['to']}（新提交 {r['commit']}），变更 {len(r['changed'])} 个文件")
        for h in r["hints"]:
            print(f"提示：{h}")
    elif c == "exec":
        cmd = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not cmd:
            raise WfError("用法：wf exec -- <命令>")
        sys.exit(ops.run_exec(_p(args), " ".join(cmd) if len(cmd) > 1 else cmd[0], args.label, args.timeout))
    elif c == "verify":
        inst = _p(args)
        if args.action == "list":
            items = ops.verify_items(inst)
            execs = {e["id"]: e for e in ops.list_execs(inst)}
            for i in items:
                v = ops.VERDICTS.get(i.get("verdict"), "未验收")
                print(f"  {i['n']:>2}. [{v}] {i['item']}" + (f"  证据 {i['evidence']}" if i.get("evidence") else ""))
            if execs:
                print("本轮命令证据：" + "  ".join(f"{k}(exit={v['exit_code']})" for k, v in execs.items()))
        else:
            if args.n is None or not args.verdict:
                raise WfError("用法：wf verify set <序号> pass|fail|deviation|skip [--evidence c01] [--note …]")
            ops.verify_set(inst, args.n, args.verdict, args.evidence, args.note)
            left = [i for i in ops.verify_items(inst) if not i.get("verdict")]
            print(f"已记录第 {args.n} 项：{ops.VERDICTS[args.verdict]}。" + (f"还有 {len(left)} 项未验收。" if left else "清单已全部验收，可以 wf report。"))
    elif c == "note":
        ops.add_note(_p(args), args.kind, args.text)
        print("已记录")
    elif c == "report":
        p = ops.compose_report(_p(args), args.out)
        print(f"回报草稿：{p}\n检查补充后：wf check-outgoing {p} --kind report")
    elif c == "check-outgoing":
        r = ops.check_outgoing(Path(args.file), args.kind)
        if args.json:
            out(r)
        else:
            for s in r["secrets"]:
                print(f"✗ 密钥：{s}")
            for s in r["problems"]:
                print(f"✗ {s}")
            for s in r["warnings"]:
                print(f"! {s}")
            print(("✓ 可以发送" if r["ok"] else "✗ 不能发送") + f"（{r['chars']} 字符）")
        sys.exit(0 if r["ok"] else 2)
    elif c == "init":
        from wflib.admin import init_project
        inst = init_project(Path(args.root), args.name, args.adopt, args.turn, args.state)
        print(f"已接入：{inst.name}（{inst.root}），当前 {inst.state['state']}，第 {inst.state['turn']} 轮，固定工具版本 {inst.cfg['wf_version']}")
    elif c == "projects":
        reg = load_registry()["projects"]
        if not reg:
            print("还没有接入任何项目。用 wf init 接入。")
        for p in reg:
            try:
                from wflib.core import Instance
                s = machine.status(Instance(Path(p["root"])))
                print(f"  {p['name']:<20} {s['state']:<14} 第 {s['turn']} 轮  {p['root']}")
            except WfError as e:
                print(f"  {p['name']:<20} 不可用：{e}")
    elif c == "pin":
        from wflib.admin import _minor
        from wflib.core import write_json
        inst = _p(args)
        old = inst.cfg.get("wf_version")
        inst.cfg["wf_version"] = _minor(tool_version())
        write_json(inst.cfg_path, inst.cfg)
        inst.event("pin", frm=old, to=inst.cfg["wf_version"])
        print(f"项目 {inst.name} 固定版本：{old} → {inst.cfg['wf_version']}（wf.json 有变化，请随下次版本一起提交）")
    elif c == "bootstrap":
        from wflib.admin import bootstrap
        r = bootstrap(_p(args), args.reason)
        print(f"说明消息：{r['message']}（{r['chars']} 字符）\n项目 zip：{r['zip']}\n工具 zip：{r['tool_zip']}\n"
              "在新对话中上传这两个 zip 并发送说明消息（档位 max）；桥接工具指向新对话后运行 wf conv new --note …")
    elif c == "doctor":
        from wflib.admin import doctor
        try:
            inst = open_project(args.project)
        except WfError:
            inst = None
        checks = doctor(inst)
        if args.json:
            out(checks)
        else:
            mark = {"ok": "✓", "warn": "!", "fail": "✗"}
            for ck in checks:
                print(f"{mark[ck['level']]} {ck['item']}：{ck['msg']}" + (f"  → {ck['hint']}" if ck["hint"] else ""))
        sys.exit(1 if any(ck["level"] == "fail" for ck in checks) else 0)
    elif c == "fingerprint":
        root = Path(args.path) if args.path else _p(args).root
        fp = ops.fingerprint(root)
        if args.json:
            out(fp)
        else:
            print(f"仓库指纹 {fp['digest']}（{fp['count']} 个文件）")
            if args.files:
                for f in fp["files"]:
                    print(f"  {f['sha']}  {f['path']}")
    elif c == "snapshot":
        paths = [x for p in args.paths for x in p.split(",")]
        text = ops.snapshot(_p(args).root, paths, args.max_chars)
        chk_hits = [ln for ln in text.splitlines() if any(re.search(pat, ln) for pat, _ in ops.SECRET_PATTERNS)]
        if chk_hits:
            raise WfError("文件包中疑似含有凭据，已拒绝生成。请检查这些文件。")
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"文件包：{args.out}（{len(text)} 字符）")
        else:
            print(text)
    elif c == "drill":   # 1.6.8（P1-D）：独立审查 block 与超时的驱动器侧演练，隔离在 --dir 里（docs/DRILL.md）
        from wflib import drill
        r = drill.run(args.kind, Path(args.dir), args.review, args.review_timeout_s)
        if args.json:
            out(r)
        else:
            for x in r["checks"]:
                print(f"{'✓' if x['ok'] else '✗'} {x['check']}" + ("" if x["ok"] else f"  —— {x['detail']}"))
            print(f"\n演练（{r['kind']}）{'通过' if r['ok'] else '未通过'}：项目 {r['project']}，状态 {r['state']}；"
                  f"结果与证据清单见 {Path(r['dir']) / 'drill-result.json'}；演练项目已暂停，目录保留")
        if not r["ok"]:
            sys.exit(1)
    elif c == "bridge-check":
        if args.list_actions:
            acts = sorted(ops.BRIDGE_ACTIONS)
            out({"actions": acts}) if args.json else print(" ".join(acts))
            return
        if not args.log:
            raise WfError("用法：wf bridge-check --log <日志>，或 wf bridge-check --list-actions")
        res = ops.bridge_check(Path(args.log), args.hours, args.grace_s)
        if args.json:
            out(res)
        else:
            for r in res:
                print(f"{'✓' if r['ok'] else '✗'} {r['rule']}：{r['msg']}")
        sys.exit(0 if all(r["ok"] for r in res) else 1)
    elif c == "run":
        from wflib import driver
        inst = _p(args)
        code, info = driver.run(inst, args.max_seconds, args.max_steps)
        if args.json:
            out({"exit": code, **info})
        else:
            for line in info["did"]:
                print(line)
            if not info["did"] and info.get("note"):
                print(info["note"])
            _print_explain(info.get("explain") or driver.explain(inst))
        sys.exit(code)
    elif c == "tick":
        from wflib import driver
        from wflib.core import LOCAL
        _log = LOCAL / (f"tick-{_safe_name(args.project)}.log" if args.project else "tick.log")   # 1.7.2：各写各的日志
        if _log.exists() and _log.stat().st_size > 5 * 1024 * 1024:
            import os as _os
            _os.replace(_log, _log.with_name(_log.name + ".1"))
        from wflib.core import Instance
        insts = []
        for p in load_registry()["projects"]:
            try:
                insts.append(Instance(Path(p["root"])))
            except WfError as e:
                print(f"跳过 {p['name']}：{e}")
        # 1.7.2（ISOLATION.md S3）：wf --project <名> tick 只推进这一个项目；不带 --project 时跳过有自己定时任务的项目。
        # inbox 仍汇总全部项目：只写本项目会把别的项目的待办冲掉，而执行端以“key 从 inbox 消失”判定做完
        own = set(_own_sched())
        if args.project:
            drive = [i for i in insts if i.name == args.project]
            if not drive:
                raise WfError(f"注册表里没有项目 {args.project}（wf projects 列出全部）")
            pre = []
        else:
            drive = [i for i in insts if i.name not in own]
            pre = [{"project": i.name, "exit": None, "note": "有自己的定时任务（wf schedule plist --project），由它推进"}
                   for i in insts if i.name in own]
        code, rows = driver.tick(drive, args.max_seconds)
        rows = pre + rows
        from wflib import sync as _sync   # 1.6.0：项目上下文同步（docs/SYNC.md），先于 inbox
        _sync.after_tick(drive)
        from wflib import notify   # 1.5.7：新出现的待决事项与提示弹成系统通知（每件事只通知一次）
        notify.after_tick(insts)
        if args.json:
            out({"exit": code, "projects": rows})
        else:
            for r in rows:
                print(f"{r['project']}：exit={r['exit']}  {r['note']}")
        sys.exit(code)
    elif c == "ask":
        r = asks.ask(_p(args), only_new=args.new, mark=args.mark_shown)
        if args.json:
            out({"items": r["items"], "notices": r["notices"], "text": r["text"]})
        elif r["text"]:
            print(r["text"])
    elif c == "panel":
        print(asks.panel(_p(args)))
    elif c == "signal":
        from wflib import signals
        if args.action == "add":
            if not args.key or not args.title:
                raise WfError("用法：wf signal add --key <键> --title <标题> [--why …] [--for executor|user] [--ttl 秒]")
            r = signals.add(args.key, args.title, args.why, for_=args.for_, project=getattr(args, "project", None) or "",
                            action=args.action_text, source="cli", ttl_s=args.ttl)
            out(r) if args.json else print(f"已写入信号 {r['key']}（交给 {r['for']}）；下一次 wf tick 或 wf watchdog --write 时进入 inbox")
        elif args.action == "clear":
            if not args.key:
                raise WfError("用法：wf signal clear --key <键>")
            print("已清除" if signals.clear(args.key) else "没有这个信号")
        else:
            rows = signals.load()
            if args.json:
                out({"signals": rows})
            else:
                for r in rows:
                    print(f"{r['key']}  [{r.get('for')}] {r.get('title')}：{r.get('why') or ''}（自 {r.get('since')}，来源 {r.get('source')}）")
                if not rows:
                    print("没有信号")
    elif c == "task":
        from wflib import tasks
        inst = _p(args)
        if args.action == "done":
            if not args.tid:
                raise WfError("用法：wf task done <编号> --note \"结果\"（wf task 列出未完成的待办）")
            t = tasks.done(inst, args.tid, args.note)
            left = tasks.open_items(inst)
            tail = ("，驱动器下次运行时自动生成回报" if inst.state.get("await_executor") is not None else
                    "；目标已达成，自动化停在这里，不会自动回报：结果已写进 inbox 的“目标已达成”提示与 wf task，"
                    "需要交给 Claude 时 wf kick --kind report（由证据生成带原文的回报；说明先用 wf note --kind info 写）" if inst.state.get("state") == "DONE" else "")   # 1.6.7；1.7.1 提示改为由证据生成
            print(f"已完成 {t['id']}；" + (f"还有 {len(left)} 项未完成" if left else "全部待办已完成" + tail))
        else:
            rows = inst.state.get("executor_tasks") or []
            if args.json:
                out({"tasks": rows})
            else:
                for t in rows:
                    print(f"{'✓' if t.get('done') else '○'} {t['id']}  {t['text']}" + (f"  —— {t['note']}" if t.get("note") else ""))
                if not rows:
                    print("没有执行端待办")
    elif c == "watchdog":
        from wflib import watchdog
        from wflib.core import Instance
        insts = []
        for p in load_registry()["projects"]:
            try:
                insts.append(Instance(Path(p["root"])))
            except WfError as e:
                print(f"跳过 {p['name']}：{e}")
        rows = watchdog.run(insts, write=args.write)
        if args.json:
            out({"findings": rows, "thresholds": watchdog.THRESHOLDS, "written": bool(args.write)})
        else:
            for r in rows:
                print(f"[{r['project']}] {r['check']}：{r['title']}——{r['why']}")
            print(f"发现 {len(rows)} 项停滞" + ("，已写成信号并重写 inbox" if args.write else "") if rows else "没有发现停滞")
    elif c == "explain":
        from wflib import driver
        e = driver.explain(_p(args))
        out(e) if args.json else _print_explain(e)
    elif c == "kick":
        inst = _p(args)
        st = inst.state
        if st["state"] not in ("IDLE", "WAIT_SCHEDULE", "DONE"):
            raise WfError(f"当前是 {st['state']}，只有空闲、等待计划节点或目标达成时可以发起新任务。")
        if not args.message:
            # 1.7.1：回报不必手写——由本轮证据生成标准回报（② 终端原文、④ 需要注意、执行端备注与待办结果），驱动器接着发送
            if args.kind != "report":
                raise WfError("用法：wf kick --message <任务文件>；回报可以不带 --message：wf kick --kind report")
            p = ops.compose_report(inst, by="executor")
            print(f"已由证据生成第 {inst.state['turn']} 轮回报：{p}\n驱动器下次运行时发送（wf run 可立即推进）；"
                  "要补充说明，先 wf note --kind info \"…\" 再运行本命令")
            return
        chk = ops.check_outgoing(Path(args.message), args.kind)
        if chk["secrets"]:
            raise WfError("任务消息中疑似含有凭据，拒绝发起：\n  " + "\n  ".join(chk["secrets"]))
        machine.go(inst, "COMPOSE", by="user", reason="发起新任务")
        dst = inst.outbox / f"turn-{inst.state['turn']:03d}.md"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(Path(args.message).read_text(encoding="utf-8"), encoding="utf-8")
        inst.state["outgoing"] = {"turn": inst.state["turn"], "file": str(dst), "kind": args.kind, "effort": args.effort}
        inst.save()
        print(f"已发起第 {inst.state['turn']} 轮任务，驱动器下次运行时发送（wf run 可立即推进）")
    elif c == "approve":
        inst = _p(args)
        st = inst.state
        if st["state"] != "AWAITING_USER":
            raise WfError(f"当前是 {st['state']}，没有等待用户决定的事项。")
        it = asks.verify(inst, "approve", args.code)
        note, by = _decide_as(inst, "approve", it, args)
        if st.get("return_to") == "REVIEW":
            st.setdefault("turn_flags", {})["review_accepted"] = True
            inst.event("review", accepted=True, user_approved=note, by=by)
        st["consecutive_failed"], st["rounds_no_progress"] = 0, 0
        if st.get("budget"):
            st["budget"]["rounds"] = 0
        st.pop("needs_text", None)
        inst.save()
        st = machine.resume(inst, note, by=by)
        print(f"已批准，回到 {st['state']}。驱动器下次运行时继续。")
    elif c == "notify":
        from wflib import notify
        if args.action == "test":
            ok = notify.send("collab-wf", "这是一条测试通知：wf tick 跑完后，会这样提示需要你处理的事。", "通知测试")
            print("已发出测试通知" if ok else "没有发出：只支持 macOS 的 osascript")
        else:
            print(notify.status_text())
    elif c == "sync":   # 1.6.0：项目上下文同步（docs/SYNC.md）
        from wflib import sync
        inst = _p(args)
        if not args.dry_run and not sync.enabled(inst):
            raise WfError(f"项目 {inst.name} 没有开启同步：local/transport.json 需要 sync 与 sync_projects（docs/SYNC.md）")
        r = sync.run(inst, dry_run=args.dry_run, force=args.force)
        print(f"同步：{r['status']}，{r['files']} 个文件，{r['bytes']} 字节，内容摘要 {r['digest']}"
              + (f"；{r['detail']}" if r.get("detail") else ""))
        for s in r.get("secrets", []):
            print("  疑似密钥：" + s)
        sys.exit(1 if r["status"] in ("blocked", "failed") else 0)
    elif c == "said":
        from wflib import page
        page.record_user(_p(args), args.text, kind=args.kind)
        print("已逐字记下用户的原话（看板“对话记录”里的“用户”）。")
    elif c == "look":
        from wflib import page
        r = page.look(_p(args), full=bool(getattr(args, "full", False)))
        if not r["ok"]:
            raise WfError(r["note"])
        print(r["note"])
    elif c == "pause":
        inst = _p(args)
        inst.state["paused"] = {"ts": tool_now(), "note": args.note}
        inst.event("pause", note=args.note, by="user")
        print("已暂停自动化。驱动器不会再推进这个项目，直到 wf unpause。手动命令仍可使用。")
    elif c == "unpause":
        inst = _p(args)
        if not inst.state.get("paused"):
            raise WfError("当前没有暂停。")
        it = asks.verify(inst, "unpause", args.code)
        _record_user(inst, "unpause", it, args.said)
        inst.state.pop("paused", None)
        inst.event("unpause", note=args.said, by="user")
        print("已恢复自动化。")
    elif c == "undo":
        inst = _p(args)
        from wflib.core import git
        subjects = git(["log", "--format=%s", "--grep=^release v"], inst.root, check=False).stdout.splitlines()
        vers = [l.split()[1] for l in subjects if l.startswith("release v")]
        if len(vers) < 2:
            raise WfError("没有可以回退到的上一个版本。")
        inst.state["paused"] = {"ts": tool_now(), "note": f"undo：{args.note}"}
        inst.event("pause", note=f"undo：{args.note}", by="user")
        r = release.rollback(inst, vers[1])
        print(f"已暂停自动化并回退：{r['from']} → {vers[1]}（新提交 {r['commit']}）。检查后 wf unpause。")
        for h in r["hints"]:
            print(f"提示：{h}")
    elif c == "audit":
        _print_audit(_p(args), args.n)
    elif c == "goals":
        from wflib import goals, policy
        inst = _p(args)
        if args.action == "approve":
            g = goals.load(inst)
            iss = goals.validate(g) if g else ["没有 workflow/goals.json"]
            if iss:
                raise WfError("目标定义有问题，不能批准：\n  " + "\n  ".join(iss))
            if not args.code or not (args.said or getattr(args, "by", None)):
                raise WfError("批准需要 --code（wf ask 显示的决定码）与 --said（用户原话）；交给执行端的事项也可以 --by executor --reason …")
            it = asks.verify(inst, "goals", args.code)
            note, by = _decide_as(inst, "goals", it, args)
            r = policy.approve(inst, "goals", note, by=by)
            print(f"已批准目标定义（sha256 {r['sha256'][:12]}）。今后对 goals.json 的任何改动都需要重新批准。")
        elif args.action == "confirm":
            if not args.gid or not args.code or not args.said:
                raise WfError("用法：wf goals confirm <目标编号> --code <决定码> --said \"用户原话\"")
            it = asks.verify(inst, f"confirm:{args.gid}", args.code)
            _record_user(inst, f"confirm:{args.gid}", it, args.said)
            goals.confirm(inst, args.gid, args.said)
            print(f"已确认 {args.gid}")
        else:
            prog = goals.run_checks(inst, by="user") if args.action == "check" else goals.progress(inst)
            if args.json:
                out(prog)
            elif not prog["defined"]:
                print("没有定义目标（workflow/goals.json）")
            else:
                print(f"目标进度 {prog['done']} / {prog['total']}" + ("" if prog["approved"] else
                      ("（目标在批准后被改动，需重新批准）" if prog.get("changed_since_approval") else "（目标尚未批准，不生效）")))
                for i in prog["items"]:
                    print(f"  {'✓' if i['ok'] else '○'} {i['id']} [{'可执行' if i['kind'] == 'exec' else '人工'}] {i['title']}：{i['detail']}")
                for x in prog.get("issues", []):
                    print(f"  ! {x}")
    elif c == "policy":
        from wflib import policy
        inst = _p(args)
        if args.action == "approve":
            if not args.code or not (args.said or getattr(args, "by", None)):
                raise WfError("批准需要 --code（wf ask 显示的决定码）与 --said（用户原话）；交给执行端的事项也可以 --by executor --reason …")
            it = asks.verify(inst, "policy", args.code)
            note, by = _decide_as(inst, "policy", it, args)
            r = policy.approve(inst, "policy", note, by=by)
            print(f"已批准白名单（sha256 {r['sha256'][:12]}）。今后对 policy.json 的任何改动都需要重新批准。")
        else:
            stt = policy.status(inst, "policy")
            pol = __import__("json").loads((inst.wf / "policy.json").read_text(encoding="utf-8")) if stt["exists"] else {}
            print("白名单：" + ("已批准" if stt["approved"] else "批准后被改动，未生效" if stt["changed_since_approval"] else "未批准" if stt["exists"] else "不存在"))
            for a in pol.get("allow", []):
                print(f"  允许 {a}")
            print("内置停止底线（不可关闭）：" + "、".join(c for c, _ in policy.STOP_FLOOR) + "；护栏命令只能由用户执行；命令拼接与重定向需要批准")
            for k in ("auto_apply", "max_rounds_per_day", "max_consecutive_failed_rounds", "max_rounds_without_goal_progress",
                      "command_timeout_s", "poll_interval_s"):
                if k in pol:
                    print(f"  {k} = {pol[k]}")
    elif c == "transport" and args.action == "test":
        sys.exit(_transport_test())
    elif c == "effort":
        from wflib import roles
        inst = _p(args)
        cfg = roles.config(inst)
        if args.json:
            out({"config": cfg, "stats": roles.stats(inst)})
        else:
            print(f"预算：{cfg.get('budget')}（深度角色使用 {cfg.get('budgets', {}).get(cfg.get('budget'))}）；"
                  f"5 小时窗口用量 ≥ {cfg.get('cap_when_quota_above_pct')}% 时，非失败情况封顶 high")
            for name, r in cfg["roles"].items():
                print(f"  {name:<11} {r.get('effort'):<7} {r.get('doc', '')}")
            st = roles.stats(inst)
            for grp, title in (("effort", "按档位"), ("role", "按角色")):
                if st.get(grp):
                    print(f"复盘（{title}，只含实测值）：")
                    for k, v in st[grp].items():
                        print(f"  {k:<11} 轮次 {v['turns']:>3}  平均消耗 {v['avg_session_pct'] if v['avg_session_pct'] is not None else '—'}%"
                              f"  平均耗时 {v['avg_duration_s'] or '—'} 秒  通过率 {v['pass_rate'] if v['pass_rate'] is not None else '—'}")
    elif c == "decisions":
        from wflib import roles
        rows = roles.decisions(_p(args), turn=args.turn, n=args.n)
        if args.json:
            out(rows)
        elif args.tsv:
            print("ts\tturn\tphase\tdecision\twhy\tevidence\tresult")
            for r in rows:
                print("\t".join(str(r.get(k, "")).replace("\t", " ").replace("\n", " ")
                                for k in ("ts", "turn", "phase", "decision", "why", "evidence", "result")))
        else:
            for r in rows:
                print(f"{r['ts'][5:16].replace('T', ' ')} 第 {r['turn']} 轮 [{r['phase']}] {r['decision']}。理由：{r['why']}"
                      + (f"。证据：{r['evidence']}" if r.get("evidence") else "") + (f"。结果：{r['result']}" if r.get("result") else ""))
    elif c == "conv":
        from wflib import roles
        inst = _p(args)
        cst = inst.state.get("conv") or {}
        if args.action == "new":
            if not args.note:
                raise WfError("用法：wf conv new --note \"新对话已建立，已上传恢复包\"")
            old = dict(cst)
            inst.state["conv"] = {"since_turn": inst.state.get("turn", 0) + 1, "since": tool_now(), "chars": 0, "turns": 0,
                                  "previous": {k: old.get(k) for k in ("since_turn", "since", "chars", "turns")}}
            if getattr(args, "conv", None):   # 1.7.0（ISOLATION.md S1）：手工换对话时同时登记新对话
                inst.state["conv"]["conv"] = args.conv
            # 1.6.9（N10）：与驱动器自动开新对话（driver.py）一致：上一轮的 ref 属于旧对话，不再作为 --prev-ref 交给适配器；
            # 否则新对话首轮的轮询会去查旧对话，消息已落地也确认不到，最后阻塞，而 wf resend 会重复发送
            inst.state.pop("send_ref", None)
            inst.state.pop("resend_parent", None)
            inst.event("conv_new", note=args.note, conv=getattr(args, "conv", None), by="user")
            roles.decide(inst, "context", "开始新对话", args.note, "", "计数已归零", by="user")
            print("已记录新对话的起点，计数归零。如驱动器停在等待用户决定，接着运行 wf approve --note …")
        elif args.action == "bind":
            # 1.7.0（ISOLATION.md S1）：只登记本项目当前所在的对话；不换对话、不清计数、不动本轮的发送意图（迁移用）
            if not args.conv or not args.note:
                raise WfError("用法：wf conv bind --conv <对话 id> --note \"说明\"")
            c2 = dict(inst.state.get("conv") or {})
            c2["conv"] = args.conv
            inst.state["conv"] = c2
            inst.event("conv_bind", conv=args.conv, note=args.note, by="user")
            print(f"已登记本项目的对话 {args.conv}（只登记：不换对话、不清计数）。transport.json 声明 per_project_conv 后，"
                  "发送、轮询与快照都会带上它")
        else:
            from wflib import policy as _pol
            pol = __import__("json").loads((inst.wf / "policy.json").read_text(encoding="utf-8")) if (inst.wf / "policy.json").exists() else {}
            print(f"当前对话：自第 {cst.get('since_turn', 1)} 轮起，{cst.get('turns', 0)} 轮，约 {cst.get('chars', 0)} 字符")
            print(f"对话 id：{cst.get('conv') or '（未登记）'}")   # 1.7.0
            print(f"上限：{pol.get('max_conversation_turns') or '未设置'} 轮 / {pol.get('max_conversation_chars') or '未设置'} 字符")
    elif c == "schedule":
        _schedule(args)
    elif c == "transport":
        from wflib import anomaly
        import os as _os
        cfg = anomaly.transport_cfg()
        if not cfg:
            print("没有 local/transport.json（驱动器无法收发消息）。格式见 docs/TRANSPORT.md。")
            sys.exit(1)
        bad = 0
        loc = anomaly.adapter_location()
        if loc["mode"] == "module":
            print(f"{'✓' if loc['file'] else '✗'} 适配器模块：{loc['module']}（{loc['file'] or '未找到'}）")
            bad += 0 if loc["file"] else 1
            if loc["inside_tool"]:
                bad += 1
                print("✗ 通道适配器位于工具目录内：它会随 wf bootstrap 与 self-update 被打包和覆盖。请移到工具目录之外，并用 module_path 指向（见 adapters/README.md）")
        else:
            for op in ("send", "poll", "quota"):
                p = cfg.get(op)
                good = bool(p) and _os.access(p, _os.X_OK)
                bad += 0 if good or op == "quota" else 1
                print(f"{'✓' if good else ('-' if op == 'quota' else '✗')} {op}：{p or '未配置'}")
        print(f"环境变量：{', '.join((cfg.get('env') or {}).keys()) or '无'}；模型：{cfg.get('model') or '未设置'}")
        sys.exit(1 if bad else 0)
    elif c == "self-check":
        r = release.self_check()
        if args.json:
            out(r)
        elif r.get("error"):
            print(r["error"])
        else:
            print(f"对照 {r['version']} 的文件清单：" + ("完全一致" if r["ok"] else "有出入"))
            for k, title in (("extra", "多出（不在清单中，可能是旧交付的残留，或放错位置的通道适配器）"),
                             ("missing", "缺失"), ("changed", "内容被改动")):
                if r[k]:
                    print(f"{title}：")
                    for f in r[k]:
                        print(f"  {f}")
            if r["extra"]:
                print("说明：wf 不会自动删除这些文件。按 docs/AUDIT.md 的结论处理：通道适配器移到工具目录之外，其余确认无用后删除。")
        sys.exit(0 if r.get("ok") else 1)
    elif c == "self-manifest":
        print(f"已写入 {release.write_manifest()}")
    elif c == "self-init":
        print(f"工具版本库已就绪：wf-v{tool_version()}（{release.self_init()}）")
    elif c == "self-update":
        r = release.self_update(Path(args.zip), args.skip_test, args.allow_same, auto=bool(getattr(args, "auto", False)))
        print(f"工具已升级：{r['from']} → {r['to']}（{r['commit']}），自检{'已通过' if r['tested'] else '已跳过'}")
        if r.get("board_restarted"):
            print("看板已重启以加载新版本")
        chk = release.self_check()
        if not chk.get("ok") and not chk.get("error"):
            print(f"提示：工具目录与新版本的文件清单有出入（多出 {len(chk['extra'])}、缺失 {len(chk['missing'])}、改动 {len(chk['changed'])}），运行 wf self-check 查看")
    elif c == "self-rollback":
        r = release.self_rollback(args.tag)
        print(f"工具已回滚：{r['from']} → {r['to']}（新提交 {r['commit']}）。已接入项目的固定版本请用 wf doctor 核对。")
        from wflib import server as _srv
        m = _srv.restart_if_running()
        if m:
            print(f"看板已重启以加载回滚后的版本：{m}")
    elif c == "selftest":
        from wflib.selftest import run
        sys.exit(run())
    elif c == "serve":
        from wflib import server
        if args.daemon or args.stop or args.status:
            print(server.daemon("start" if args.daemon else "stop" if args.stop else "status", args.port))
        else:
            server.serve_foreground(args.port)


if __name__ == "__main__":
    try:
        main()
    except WfError as e:
        print(f"wf：{e}", file=sys.stderr)
        sys.exit(1)
    except BrokenPipeError:
        # 输出被管道提前关闭（例如 wf version | head -1），不是错误
        import os
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
