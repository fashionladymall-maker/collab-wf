【本轮结论】

第 27 轮两项都通过。

**唤醒间隔不需要 1.5.10。** `wf schedule plist` 本来就有 `--interval`（默认 300）和 `--max-seconds`（默认 540）两个参数，而且生成时会把间隔记进 `local/schedule.json`，`wf schedule status` 判断定时器是否停了也用这个值。所以「被覆盖回 300 秒」只是重新生成时没带参数。执行端在驱动器之外手动做一次即可（命令里有重定向，驱动器不会执行）：

```
wf schedule plist --interval 60 > ~/Library/LaunchAgents/local.collab-wf.tick.plist
launchctl bootout gui/$(id -u)/local.collab-wf.tick
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.collab-wf.tick.plist
```

间隔 60 秒、时间预算 540 秒时，前一次 tick 还在等回复，后一次会因为项目锁直接跳过，不会重复做事。W27 随之了结，下个项目版本更新台账。

**M1 判据：先看清数据再起草。** goals.py 里的 exec 类目标由驱动器在每轮验收后用 `bash -c` 运行，按 exit、contains、not_contains、regex 判定。我在意两件事：

1. 目标批准之后，`max_rounds_without_goal_progress=8` 会生效。判据写错、永远过不了，驱动器第 8 轮就会停下。
2. 全部判据通过后，项目会进入「目标达成」并停下。

所以判据只能建立在我见过原文的输出上。「工具完整」和「体检健康」两条现在就能写，用 self-check 的「完全一致」和 doctor 的 ✓、!、✗。「连续 N 轮无人工介入」和「工具版本被驱动器自动装上过」两条，要从事件和决策记录里算出来。我打算写成一个只读脚本 `workflow/m1_check.py` 放进项目，由它解析这些记录并给出结论，所以本轮先读这几个文件的真实格式。下一轮交付 v0.9：goals.json、m1_check.py，以及 PROJECT.md 的更新。

执行端请用 `wf note` 补两件事：inbox 的定时自动化配好没有，间隔多少；上面三条命令做完了没有。

【本机执行】
```
tail -n 15 workflow/events.jsonl
tail -n 6 workflow/decisions.jsonl
tail -n 3 workflow/turns.jsonl
wf audit --n 10
wf status
```

【本轮回报清单】
1. events.jsonl 最近 15 条（事件字段与 by 的取值）。
2. decisions.jsonl 最近 6 条。
3. turns.jsonl 最近 3 条。
4. wf audit 最近 10 条（自动执行记录的格式）。
5. wf status 原文。

【需要用户决定】
无。

【下一轮建议档位】
high

【驱动指令】
发布：无
之后：回报
验收 1：c1 exit=0
验收 2：c2 exit=0
验收 3：c3 exit=0
验收 4：c4 exit=0
验收 5：c5 exit=0
失败后继续：是