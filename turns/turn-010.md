【回报 · 第 10 轮 · v0.2】档位：high

① 环境障碍与偏离
- 【第 9 轮发送事故 + 新发现 W21：驱动器不认「确定没送到」这个结论，白等约 55 分钟】
事故：第 9 轮回报 08:07:25 发出（26374 字符），SSE 流读了 300 秒只拿到 **309 个 event: ping、0 个 event: completion**，08:12:57 按「未拿到生成已开始的凭据」报错（budget=300s 生效）。这是缺陷 28 的同型签名，但本次会话本身是健康的：model=claude-opus-5-5、settings 十项齐全（paprika_mode=extended）、safeguard_fallback=null、额度 5 小时窗口 42% 未耗尽、报文 26374 字符也不算大（第 4 轮 33991 字符成功过）。所以是服务端一次「收了 POST、回了 200、但生成始终没启动」的瞬态。
**确定没有送达**的三条证据：① current_leaf 仍是 01a124d6-bd8b-7b2b-a313-00e012dcbc7c = 第 8 轮那条回复（idx 17），没动；② 会话 chat_messages 仍是 16 条、最后一条是 idx 17（08:04:47），没有第 9 轮的用户消息；③ completion_status 的 is_pending=false。对照第 1 轮同型签名（07:14 那次）——那次消息 **2 秒**就落地了，所以「ping-only」本身不等于没送到；但这次过了 8 分钟仍无消息、leaf 未动，可以判定没送到。
我做的处置：① 发现 local/transport.json **没有 poll_since**，所以驱动器轮询时根本不带 --since/--message，适配器的定位代码从来没被调用过 —— 已启用 poll_since=true（备份 /tmp/transport.json.bak-pre-pollsinc；transport.json 不在工具清单里，wf self-check 仍「完全一致」）。启用后实测驱动器会发出的那次调用：poll --ref "" --since 08:07:05 --message turn-009.md --prev-ref，返回 {"status":"none","landed":false} —— 从「没法查」变成了**确定性结论**。
W21（新）：驱动器在 SEND_UNKNOWN_WAIT 里只认 status ∈ {generating, done}，其余一律按「结果不明」继续退避（backoff [120,300,600,600,600,600,600]，max 7）。**于是即使适配器已经用内容比对给出了 landed=false 的确定答案，驱动器仍会白等约 55 分钟才阻塞。** 建议：适配器在「做了内容比对且没找到」时返回一个区别于 none 的状态（例如 not_landed），驱动器见到它直接转 BLOCKED，让人用 wf resend 决定。这样既保留「绝不自动重发」的安全底线，又不浪费一小时。
现状：状态机停在 SEND，send_intent 仍 pending，outgoing 仍指向 turn-009.md，retry_at 08:15:32。已按异常上报，等用户授权后用 wf block + wf resend（决定码由 asks.py:39 从状态自动生成）重发第 9 轮。
- 【★ 更正上一条：我说「确定没有送达」是错的，消息其实落地了；并撤回 W21】
更正：上一条 deviation 里我写「**确定没有送达**」并给了三条证据（current_leaf 未动 / chat_messages 只有 16 条 / is_pending=false）——**这个结论是错的**。事实：第 9 轮回报 **08:07:27 就落地了**（idx 18，26374 字符），Claude 也在 **08:18:36 回复了**（idx 19，2022 字符，交付 collab-wf-dev-v0.2.zip 与 build_wf_152.py）。
真正的根因是**会话 JSON 的可见性延迟**，不是没送达：① 会话顶层 `updated_at` 在我 08:14 和 08:20 两次查询里**都是 08:07:27** —— 它反映的是写入时刻，是准的；② 但 `chat_messages` 在 08:16:45 驱动器那次 --since 轮询时**仍只有 16 条**（适配器日志原文：「按 --since 2026-10-10T08:07:05+00:00 未找到本轮消息（会话共 16 条）」），直到 08:22:11 才变成 18 条；③ `current_leaf` 也同步滞后（08:14 是 01a124d6=idx17，08:20 才是 01a124da=idx19）。也就是说：**同一个会话对象里，updated_at 是新的，chat_messages 与 current_leaf 是旧的** —— 我拿后两者当「没送到」的判据，等于拿一个会滞后的字段下结论。
这是我**第三次**犯同型错误（W03 → 教训 8 → 教训 25 → 这次）。教训要再收紧：`updated_at` 变了就等于有写入，可信；`chat_messages` 的条数与 `current_leaf` **可能滞后数分钟，不能用来判「没送到」**；唯一可靠的正面判据仍是 **SSE 流里出现 `event: completion`**，而「没看到 completion」只说明「启动与否未知」，**永远不能推出「没送达」**。正确的做法是等 —— 这次从发出到消息可见隔了约 15 分钟。
**撤回 W21**：我上一条建议「适配器用内容比对确认没找到时返回 not_landed，驱动器直接转 BLOCKED」。**这条建议是错的，请勿采纳** —— 因为「内容比对没找到」恰恰可能是可见性延迟造成的假阴性，采纳它会在消息其实已落地时触发重发，正是第 40 轮那种重复发送。**1.5.1 现在这套「绝不自动重发、反复轮询约一小时再阻塞」的保守设计是对的，应当保留。** 我这条建议的教训是：在把「查不到」升级成「确定没有」之前，必须先证明查询本身是可靠的。
好消息（本轮的净收益）：**poll_since 一开，驱动器就自动自愈了**。08:22:11-12 的日志：download collab-wf-dev-v0.2.zip(8064B)、download build_wf_152.py(33027B)、done「reply index=19，交付 2 个文件，reply_parent={"cid":"f895aab8…","before":17}，**ref 由 --since 重建**」→ SEND 自动转 WAIT_REPLY，全程无人干预。也就是说：适配器的 --since 定位 + transport.json 的 poll_since 这一对组合，正好治的就是「发送结果不明」这条因果链 —— 这验证了 1.5.1 那两项约定的价值。

② 关键数字与终端原文

1) 落地 v0.2 后，指纹为 5534b5c98537025e。 —— 通过
```
$ wf fingerprint
仓库指纹 5534b5c98537025e（4 个文件）
exit=0  耗时 0.1s
```

2) HEAD 带 v0.2 标签。 —— 通过
```
$ git log --oneline --decorate -n 3
5a4cdf1 (HEAD -> main, tag: v0.2) release v0.2
e87a419 (tag: v0.1) 接入 collab-wf 1.5.1
41700ad chore: 工具层迁出为独立项目 collab-wf-dev（PROJECT.md / CHANGELOG.md / workflow/policy.json）
exit=0  耗时 0.0s
```

3) doctor 显示 PROJECT.md 为 v0.2，且校验通过。 —— 通过
```
$ wf doctor
✓ Python：3.13.12
✓ 工作流定义：workflow.json，14 个状态
✓ 工具版本：1.5.1（wf-v1.5.1）
✓ 看板进程：运行中（PID 47947，端口 8765，加载的版本 1.5.1）
✓ 版本匹配：项目固定 1.5，工具 1.5.1
✓ PROJECT.md：v0.2，阶段：新对话续接完成，1.5.2 构建器已交付
✓ 项目工作区：干净
✓ 当前步骤：EXECUTE（执行与取证），第 9 轮
! 额度数据：17 分钟前更新  → 超过 10 分钟未更新：轮次记录中的额度会是空值（只记实测值）；请让桥接在每次查询额度后写入 usage.json
! 事件序号：共 344 条：重复 5 个、倒序 3 处、无法解析 0 行  → 进程曾在“写事件”与“写状态”之间被打断；1.5.0 起先写状态再追加事件，历史记录不改
! 回复归属：最近 1 轮的回复没有核实归属（适配器未返回 reply_parent）  → 按 TRANSPORT.md 末节让适配器在 poll 完成时返回 reply_parent
exit=0  耗时 0.2s
```

③ 需要决策的点
无。

④ 需要注意（Attention）
- 自动处置 NET → retry：响应头: {'Access-Control-Expose-Headers': 'X-Bard-Mode', 'Alt-Svc': 'h3=":443"; ma=2592000', 'Cache-Control': 'no-cache, no-transform', 'Cf-Cache-Status': 'DYNAMIC', 'Cf-Ray': 'a48421a8fcbdd0c6-LAX', 'Content-Language': '', 'Content-Type': 'text/event-stream', 'Date': 'Sat, 10 Oct 2026 08:07:27 GMT', '
- 自动处置 SEND_UNKNOWN_WAIT → retry：发送意图 2026-10-10T08:07:05+00:00 结果不明；轮询返回 no_ref
- 自动处置 SEND_UNKNOWN_WAIT → retry：发送意图 2026-10-10T08:07:05+00:00 结果不明；轮询返回 none
- 当前对话已累计约 198905 字符、9 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

附：仓库指纹 5534b5c98537025e（v0.2，4 个文件）
