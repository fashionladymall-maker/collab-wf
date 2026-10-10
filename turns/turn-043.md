【回报 · 第 43 轮 · v1.5】档位：medium

① 环境障碍与偏离
- 【执行端 · 轮换后实测到的两个缺陷，请你在工具侧定夺】

缺陷 A（归属核对误判，已人工绕过）
- 现象：第 42 轮回报发出后，驱动器连续报「取回的不是本轮消息的回复（回复的父消息是 {"cid": "746c078a-…", "before": 3}，不是本轮发送的 {"cid": "746c078a-…", "before": 2}）」，第 1 次起就判 stale，按 policy `stale_reply_polls=3` 计，再两次就会走 `stale_reply` 异常。
- 根因：**这个新对话的 `chat_messages` index 不是连续的** —— 实际是 0（交接消息）、2（Claude 回复）、4（第 42 轮回报）、5（Claude 回复）。适配器发消息时把 `before` 记成「发送前会话里最大的 index」= 2；而回复侧算 `reply_parent` 用的是「父消息 index − 1」= 4 − 1 = 3。**只要 index 有跳号，这两个数就不相等**，于是把一条完全正确的回复判成「不是本轮的」。
- 处置：执行端把这条已落地的回复用 `wf mark replied --reply-file` 手工登记，流程已进入 INTAKE。**没有改工具、没有改状态机。**
- 建议：`before` 与 `reply_parent` 的比对不要建立在「index 连续」这个隐含前提上。可行的做法是发送时把父消息 uuid 一并记进 ref，回复侧按 uuid 链比对；或者把 `reply_parent` 定义成「父消息 index 本身」而不是减一。
- 旁证：旧对话 `7da4233f` 的 index 是连续的（43、44、45），所以这个缺陷一直没暴露。

缺陷 B（并发发送的「假成功」，见另一条 info 备注）
- SSE 只回一堆 `event: ping` 加一个 **`{"model":"", "completion":"读", "completion_type":"unparsed"}`** 的 `event: completion`；适配器按 `GEN_MARKS` 里的 `b"completion"` 判成「已接受」、`send_ref` 记成 `before: -1`，但**消息根本没落地**（会话里始终没有这条）。
- 触发条件：**上一条还在生成时又发一条**（本次是交接消息的回复还在生成，approve 后驱动器立刻发了 turn-041.md）。
- 建议：把「`completion` 的 `model` 为空」或 `completion_type == "unparsed"` 当作**未落地**而不是已接受；轮换流程里则要求「等交接消息的回复落地后再 approve」。
- 兜底现状：驱动器不会因此卡死 —— 下一次 poll 若拿到 done，它会把已有回复当本轮回复接上（第 41 轮就是这样自己走通的）。


说明（备注）：
- 【执行端 · codebuddy 实测（按上一轮回复 B 的四条判据）】

一、codebuddy 基本情况
- 路径：/Applications/WorkBuddy AI.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy
- `codebuddy --version` = 2.156.0
- `codebuddy --help` 全文已取到（12847 字节），要点如下：
  · 无头：`-p/--print`；`--output-format text|json|stream-json`；`--input-format text|stream-json`；`--json-schema`；`--include-partial-messages`
  · 权限：`--permission-mode <acceptEdits|bypassPermissions|default|plan|dontAsk|auto>`；`-y/--dangerously-skip-permissions`；`--tools` / `--allowedTools` / `--disallowedTools`
  · 会话：`--session-id <uuid>`、`--no-session-persistence`、`-c/--continue`、`-r/--resume`、`--fork-session`、`--max-turns`、`--effort <minimal|low|medium|high|xhigh|max>`
  · **后台会话管理器**：`--bg` / `--background`（detached）、`--name`、`--exec <cmd>`；配套子命令 `ps` / `logs` / `attach` / `kill` / `stop` / `rm` / `respawn` / `agents`
  · 服务：`--serve`、`--port <n>`（默认 auto-assign）、`--host`、`--auth <password|none>`；另有 `--acp`（Agent Client Protocol，stdio 或 streamable-http）
  · 其它子命令：`config` / `mcp` / `sandbox` / `plugin` / `project` / `doctor` / `update` / `install` / `cleanup` / `daemon` / `auto-mode`
  · **子命令列表里没有 `login`** —— 所以 `/login` 是会话内的交互式命令，不是命令行子命令。

二、B 判据第 1 条：**不成立**
```
$ codebuddy --port 0 -p "只回复 OK" --output-format json
Authentication required. Please use /login command to sign in to your account
```
（78 字节，立即退出，未进入生成。）
判据 2/3/4 因此无法测。

三、附带发现（供你判断）
- `--bg` + `ps/logs/kill` 这套后台会话管理器，看起来比"每次新起一个 `-p` 进程"更适合做事件驱动的唤醒通道（可以列出、查看、终止在跑的会话）。
- 桌面端本身是登录状态，但 CLI 用自己的一套配置目录（~/.codebuddy/），不复用桌面端的凭据。默认启动会去抢 127.0.0.1:53775（桌面端网关）→ EADDRINUSE 后挂住；`--port 0` 则不抢端口、独立运行，于是没有凭据。

四、结论与下一步
- 按你说的：**不替你登录**。已把"需要用户本人在终端跑一次 codebuddy 并执行 /login"这件事转告用户，等用户答复。
- 调度脚本**不实现**，等 B 判据成立。
- 已按你的要求在 local/transport.json 补上 `"sync_repo": "fashionladymall-maker/collab-wf"`（下一次轮换起用短消息）。


② 关键数字与终端原文

1) `wf goals` 原文。 —— 通过
```
$ wf goals
目标进度 4 / 7
  ✓ G1 [可执行] 工具文件与版本清单完全一致：通过
  ✓ G2 [可执行] 体检健康：驱动器心跳正常、没有错误项：通过
  ✓ G3a [可执行] 历史上出现过连续 3 轮无人工动作：通过
  ○ G3b [可执行] 历史上出现过连续 6 轮无人工动作：退出码 1，期望 0；输出不含“M1 无人值守：通过”
  ○ G3c [可执行] 历史上出现过连续 8 轮无人工动作：退出码 1，期望 0；输出不含“M1 无人值守：通过”
  ○ G3d [可执行] 历史上出现过连续 10 轮无人工动作：退出码 1，期望 0；输出不含“M1 无人值守：通过”
  ✓ G4 [可执行] 工具版本由驱动器自己构建并装上：通过
exit=0  耗时 0.4s
```

③ 需要决策的点
无。

④ 需要注意（Attention）
- 当前对话已累计约 7396 字符、2 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

附：仓库指纹 269936100986efd5（v1.5，7 个文件）
附：发送前额度 5 小时窗口 34.0%，7 天窗口 67.0%
