[工具 create_file]
[工具 bash_tool]
[工具 bash_tool]
[工具 present_files]
【本轮结论】

第 28 轮五项都通过。三种记录的格式现在都清楚了：

- events.jsonl：每行带 `seq`、`ts`、`type`、`turn`、`state`、`by`。`by` 的取值见过 driver 和 wf；人工动作是 `user`（决策日志里第 21 轮那条）。每轮以一条 `round_result`（`ok`）收尾。exec 事件带 `cmd` 和 `exit_code`。
- decisions.jsonl：带 `phase`、`decision`、`why`、`by`。
- turns.jsonl：带发送与完成时间、耗时、额度。

本轮交付 collab-wf-dev-v0.9.zip，内容三处：

1. **新增 `workflow/m1_check.py`。** 只读，只解析 events.jsonl，不写任何文件，有两个判据：
   - `unattended --rounds 10`：最近 10 个完成的轮次，轮次号连续、全部验收通过，期间没有 `by` 不属于 driver 或 wf 的事件，也没有转入 AWAITING_USER 或 BLOCKED；
   - `auto-update`：驱动器至少成功执行过一次 `wf self-update --auto`。

   输出第一行是「M1 …：通过（…）」或「未通过：原因」，退出码 0 或 1。我在本地用构造的事件测了 9 种情形，包括轮次不足、轮次不连续、有人工批准、停在等待用户、安装失败、坏行，结果都符合预期。
2. **PROJECT.md 更新。** W27 改为已解决（间隔靠 `--interval` 设定）；新增 W32；「下一步」写明定时器重载和 M1 判据的进度。
3. **CHANGELOG 记入 v0.9。**

落地后共 5 个文件，指纹应为 `3bf65e461368ea9e`。

**goals.json 本轮先不交付，原因有两个。**

1. 批准目标后，`max_rounds_without_goal_progress=8` 就会生效。而「连续 10 轮无人介入」至少要跑 10 轮才可能通过。如果熔断按「通过的目标数没有增加」来计，第 8 轮就会误停。
2. 全部目标达成后，项目会进入「目标达成」并停下。M1 之后我们还要继续做 1.6.0，需要确认这个停下的行为是不是我们想要的。

所以本轮先读驱动器里这两处逻辑，下一轮再定稿 goals.json。

**W32 说明。** 第 25–27 轮我在正文里请执行端配 inbox 定时自动化、补备注、重载定时器，都没有回应。原因很简单：驱动器自动推进的时候，执行端根本不会被唤醒。这件事需要执行端在下次被用户唤醒时一次性配好。配好之前，我不会把关键路径放在执行端的手动步骤上。

【本机执行】
```
grep -n without_goal /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
grep -n DONE /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
grep -n run_checks /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
```

【本轮回报清单】
1. 驱动器里 max_rounds_without_goal_progress 的使用位置。
2. 驱动器进入目标达成（DONE）的位置。
3. 驱动器调用 goals.run_checks 的位置。

【需要用户决定】
1. 不是决定，是一件请用户转达的事。下次你和执行端（WorkBuddy）说话时，请让它一次性做完三件事：
   - 按 DRIVER.md 第 19 节，配好读取 `local/inbox.json` 的定时自动化；
   - 用 `wf schedule plist --interval 60` 重新生成定时器配置并重载；
   - 用 `wf note` 报告这两件事的结果。

   配好之后，这条链就不再需要你转达任何事。本项不阻塞本轮。

【下一轮建议档位】
high

【驱动指令】
发布：collab-wf-dev-v0.9.zip
之后：回报
验收 1：c1 exit=0
验收 2：c2 exit=0
验收 3：c3 exit=0
指纹：3bf65e461368ea9e
失败后继续：是