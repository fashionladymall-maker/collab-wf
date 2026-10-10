[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 present_files]
【本轮结论】

第 43 轮的检查通过，但这一轮有一次人工动作（执行端手动运行 `wf mark replied`），所以 G3 的连续计数要从下一轮重新开始。

我对照了 TRANSPORT.md 的原文。执行端报告的两个缺陷都在适配器一侧，工具不改。按约定，ref 对 wf 来说是不透明的，wf 只核对 reply_parent 与 send_ref 是否相等，这一层行为是对的。

**W38（回复被误判为不是本轮的）**：适配器用两套规则生成这两个值。send_ref 记的是发送前会话里最大的 index，reply_parent 记的是父消息的 index 减一。新对话的 index 有跳号，两者就对不上。修法是两者按同一规则生成，例如都用父消息的 uuid，不依赖 index 连续。你提的两种修法都可以，我更推荐 uuid。

**W39（消息没送出却报已接受）**：TRANSPORT.md 规定退出码 0 必须有生成开始的正面证据。model 为空、completion_type 为 unparsed 的 completion 不算正面证据，应按“通道忙”返回（accepted:false 或 heartbeat_only），交给 wf 退避。

还要更正一点：你说第 41 轮“自己走通”，实际上是归属错了。第 41 轮的消息根本没有落地，驱动器把交接消息的回复当成了第 41 轮的回复。这次碰巧无害，因为那条回复本来就是回答第 41 轮要问的内容。W38 修好后，同样的情形会被 reply_parent 核对拦下，而不是被误收。

触发 W39 的另一半原因在流程上。约定改为：等交接消息的回复落地后，再运行 `wf approve`。待用户决策第 2 项（让驱动器自己开新对话）如果获批，这个时序由驱动器掌握，W39 的触发条件也就没了。

codebuddy 方面，B 判据第 1 条不成立：需要你本人登录。在你登录之前，不实现调度脚本。`--bg` 加 `ps/logs/kill` 那一套确实比裸的 `-p` 更适合做唤醒通道，但它同样要先登录，等判据成立后再一起定。

本轮发布 v1.6，只改 PROJECT.md 与 CHANGELOG.md：
- 新增已确认结论 30（唤醒方案与 codebuddy 实测结果）
- 失败模式台账新增 W38、W39
- 开新对话的约定补上“回复落地后再 approve”
- sync_repo 一项标为已完成
- 待用户决策列出两项，都不阻塞

我已用工具的 projectmd.validate 校验通过，落地后的仓库指纹应为 2286d70e1726b5ad。

给执行端：
- 按 W38、W39 修适配器。修完后运行 `wf transport test` 与 `wf bridge-check`，原文用 `wf note --kind info` 回报。我不审读适配器代码，只按约定核对。
- 下次轮换时，先等交接消息的回复落地，再运行 `wf approve`。

【本机执行】
```
wf goals
```

【本轮回报清单】
1. `wf goals` 原文。

【需要用户决定】
1. 是否同意由程序无人值守调用 codebuddy。同意的话，需要你本人在终端运行一次 codebuddy 并执行 /login；执行端不经手凭据。本项不阻塞本轮。
2. 是否让驱动器在对话达到上限时自己开新对话（工具新增功能）。本项不阻塞本轮。

【下一轮建议档位】
medium

【驱动指令】
发布：collab-wf-dev-v1.6.zip
之后：回报
验收 1：c1 exit=0
指纹：2286d70e1726b5ad