先读 REVIEW.md 与 AUDIT.md 第 4 节，再看驱动指令的解析代码，以便给出可落地的方案。
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
执行端说在提交 35fc773 里勾选了 PROJECT.md 的两项，所以我先从公开仓库取最新的 PROJECT.md 作为 v2.1 的基础，避免指纹对不上（W25、W35 的教训）。
[工具 bash_tool]
[工具 bash_tool]
当前项目指纹是 f7a603aa5b35b818，v2.1 就以这份同步下来的 PROJECT.md 为底。下面写 1.6.6 构建器：执行端待办可追踪。先核对锚点在 1.6.5 中都唯一：
[工具 bash_tool]
[工具 create_file]
[工具 bash_tool]
[工具 bash_tool]
1.6.6 也走通了：构建 212/212，模拟自动安装通过，61 个文件一致。接着在同步下来的 PROJECT.md 上整理 v2.1：
[工具 bash_tool]
[工具 bash_tool]
[工具 present_files]
【本轮结论】

第 57 轮的事项我确认已经完成：白名单已批准，三项 policy 生效，看门狗已接入 `wf watchdog --write`，self-check 完全一致。漏接回报的原因你说得对：那件事写在我回复的正文里，工具没有任何地方能跟踪它。这不只是执行端的疏忽，是工具缺一个出口，本轮从工具侧补上（记为 W44）。

**本轮交付 build_wf_166.py（sha256 2f90878e6e2a），工具 1.6.6：执行端待办可以追踪。**
- 我在【驱动指令】里写“执行端 N：<事项>”。驱动器验收后把每一件登记为待办，编号 T<轮次>-<序号>，进 inbox（交给执行端；超过 2 小时没处理就转给用户）。
- 你做完一件就运行 `wf task done <编号> --note "结果"`，结果会写进回报，事件如实记 by=executor。
- 有未完成的待办时，“之后：空闲”或“之后：等执行端”都不会进入空闲，而是停在等待；全部做完，驱动器自动生成回报并发出，不再需要你记得 `wf kick`。到最迟时间（默认 4 小时，policy 的 `executor_task_timeout_s`）还没做完的，照常回报并列出来。

验收判据就是新增的 3 项自检：指令能解析、待办登记后不重复；等待后自动回报且带完成说明；到期照常回报。我在 1.6.5 的复制品上端到端构建，自检 212/212。模拟自动安装：1.6.5 的 `guard_changes` 与 `floor.probe` 都为空，61 个文件逐一一致。本版不改 floor.py。

**独立审查（REVIEW.md）的评估。** 工具侧早已就绪，缺的只是审查程序。直接调用另一家模型需要密钥，那属于用户的事。不需要密钥的做法是经 inbox 交给执行端审：
- 审查程序被调用时写一条信号，附上 diff 路径。
- 你被唤醒后按 REVIEW.md 第 3 节审 diff，把结论写进 `local/reviews/<版本包名>.verdict.json`。
- 程序在时限内等到结论就输出那一行 JSON；等不到就以非 0 退出，工具记“未审查”，不卡流程。

前提是你所用的模型不是 Claude 系列，请你确认。方案和验收判据写在已确认结论 36。AUDIT.md 第 4 节是一次性自查，你跑完回报结果即可。这两件事等 1.6.6 装上后，我用“执行端 N：”正式派给你，届时就能追踪。

本轮同时发布 v2.1，只改 PROJECT.md 与 CHANGELOG.md，以你提交 35fc773 之后的 PROJECT.md 为底：
- 新增已确认结论 35（执行端待办可追踪）、36（独立审查方案与验收）。
- “下一步”清理：v2.0 policy 标为已生效；1.6.4 改记为“核心与适配器已实现、policy 已开启，待首次真实回归”，不写成已端到端通过；读取“给执行端：”段落一项由 1.6.6 承接。
- 新增 W44；W43 标为已解决。

落地后仓库指纹应为 232ec63bff52c2d0。

本对话已发 18 轮，第 20 轮后驱动器会第一次真实地自动开新对话，那就是 1.6.4 的真实回归。其余优先级照你列的顺序排：长回复判据的回归、时间制落地核对、单通道锁、构建器工具化、自恢复。

本轮命令只用白名单内的，之后为回报，不会进入空闲。

【本机执行】
```
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-058/delivered/build_wf_166.py --out workflow/outbox
wf self-update --auto workflow/outbox/collab-wf-1.6.6.zip
wf version
wf self-check
wf goals
```

【本轮回报清单】
1. 构建通过，自检 212/212。
2. 驱动器自动安装 1.6.6，升级核验通过。
3. 工具版本为 1.6.6。
4. self-check 完全一致。
5. `wf goals` 原文。

【需要用户决定】
无。

【下一轮建议档位】
high

【驱动指令】
发布：collab-wf-dev-v2.1.zip
之后：回报
验收 1：c1 exit=0 包含 "构建通过"
验收 2：c2 exit=0 包含 "升级核验通过"
验收 3：c3 exit=0 包含 "collab-wf 1.6.6"
验收 4：c4 exit=0 包含 "完全一致"
验收 5：c5 exit=0
指纹：232ec63bff52c2d0
超时：1800