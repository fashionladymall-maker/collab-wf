【回报 · 第 11 轮 · v0.2】档位：high

主题：看板加「Claude 页面快照」与「对话记录」（用户新需求）。本条消息是「上轮执行结果回报 + 本轮新需求」的合体：第 10 轮要求的 1.5.2 已构建安装完成（见 ①、②），③ 起是用户刚提的新需求与配套环境事实。

① 环境障碍与偏离

- 【第 10 轮的构建与安装指令已提前完成，核验全绿；09:20 的计划节点不必再发】
第 10 轮要求：在 09:20 计划节点前运行 `python3 build_wf_152.py --out workflow/outbox`，末行是「构建通过」再运行 `wf self-update workflow/outbox/collab-wf-1.5.2.zip`。两项都已完成：
  - 构建器 155/155 全通过，产出 `workflow/outbox/collab-wf-1.5.2.zip`（48 个文件，225264 字节，sha256 前缀 b7a558637935）。
  - `wf self-update` 成功：1.5.1 → 1.5.2（commit d1a525b），看板随之重启（PID 47947 → 57439，端口不变 8765，加载版本 1.5.2）。
  - 未运行 `wf pin`：项目固定的主次版本 1.5 与工具 1.5.2 匹配，doctor 的「版本匹配」为 ✓。
  - 所以本轮提前结束等待、直接进入用户的新需求。

- 【1.5.2 装完后新增 31 条事件，零新增重号，倒序由 3 处降为 2 处】
1.5.1 时 doctor 报「共 344 条：重复 5 个、倒序 3 处」；现在报「共 375 条：重复 5 个、倒序 2 处」。三处历史异常（07:28–07:52）都发生在 1.5.2 安装之前，安装后新增的 31 条事件没有任何新增重号。W18 的跨进程锁（`core.py` 的 `state_lock()` + `event()` 在锁内从磁盘重读 seq）在真实负载下有效。

- 【本轮无阻塞、无异常】

② 关键数字与终端原文

1) 工具版本与可回滚标签
```
$ wf version
collab-wf 1.5.2（wf-v1.5.2）  安装位置：/Users/admin/.workbuddy-ai/tools/collab-wf
可回滚的版本：wf-v1.5.2、wf-v1.5.1、wf-v1.5.0、wf-v1.4.3、wf-v1.4.2、wf-v1.4.1、wf-v1.4.0、wf-v1.3.1、wf-v1.3.0、wf-v1.2.2、wf-v1.2.1、wf-v1.1.0、wf-v1.0.3、wf-v1.0.2、wf-v1.0.1、wf-v1.0.0
当前项目 collab-wf-dev 固定版本：1.5
exit=0  耗时 0.1s
```

2) 文件清单与安装包完全一致
```
$ wf self-check
对照 1.5.2 的文件清单：完全一致
exit=0  耗时 0.1s
```

3) doctor 全绿（两条 ! 均为预期）
```
$ wf doctor
✓ Python：3.13.12
✓ 工作流定义：workflow.json，14 个状态
✓ 工具版本：1.5.2（wf-v1.5.2）
✓ 看板进程：运行中（PID 57439，端口 8765，加载的版本 1.5.2）
✓ 版本匹配：项目固定 1.5，工具 1.5.2
✓ PROJECT.md：v0.2，阶段：新对话续接完成，1.5.2 构建器已交付
✓ 项目工作区：干净
✓ 当前步骤：WAIT_SCHEDULE（等待计划节点），第 10 轮
✓ 额度数据：6 分钟前更新
! 事件序号：共 375 条：重复 5 个、倒序 2 处、无法解析 0 行  → 进程曾在"写事件"与"写状态"之间被打断；1.5.0 起先写状态再追加事件，历史记录不改
! 回复归属：最近 1 轮的回复没有核实归属（适配器未返回 reply_parent）  → 按 TRANSPORT.md 末节让适配器在 poll 完成时返回 reply_parent
exit=0  耗时 0.2s
```
两条 ! 的解释：事件序号的三处异常全在 1.5.2 安装之前，按设计「历史记录不改」；回复归属是第 9 轮「ref 由 --since 重建」的预期结果，没有独立的 send_ref 可比。

③ 本轮新需求（用户原话，逐字）

> 在工作流看板里面，加上Claude页面快照，只要你去看一次，就更新一次快照，而且每次对话都原封不动的在工作流面板里面有对话记录。

用户另有一条明确的角色约束，逐字：

> 让claude做吧，你只做手脚

所以本轮执行端只做勘察与事实提供，**方案与代码全部请 Claude 出**。需求拆成两件事：
  (a) **Claude 页面快照**：执行端每次去读 Claude 会话（即适配器 poll 一次），就把那一刻的页面内容存一份快照，看板上能看到。
  (b) **对话记录**：每次对话都要原封不动地出现在「工作流面板」里。

④ 环境事实（执行端实测，供设计参考）

A. 看板现状
  - 文件：`~/.workbuddy-ai/tools/collab-wf/dashboard/flow.html`（31620 字节，主看板，访问 `/` 会 302 到它）、`dashboard/project.html`（20553 字节，项目页）。两者都在工具根目录、受 MANIFEST 清单管理。
  - 服务端：`wflib/server.py`，路由有 `/api/projects`、`/api/definition`、`/api/flow?p=<项目>`、`/api/project?p=<项目>`、`/api/doctor?p=<项目>`、`/files/<项目>/<相对路径>`。启动方式 `wf serve --port 8765`（另有 `--daemon/--stop/--status`）。
  - flow.html 现有 13 个板块：需要你决定 / 驱动器 / 目标 / 工具调用进度 / Agent 进度 / 现在 / 步骤说明 / 轮次时间线 / 本轮执行 / 事件流 / 需要注意 / 决策日志 / 各步骤统计。**没有对话记录板块**，也**没有任何快照板块**。
  - flow.html 只请求 `/api/projects`、`/api/flow`、`/api/definition` 三个接口。
  - `api_flow()` 返回的与对话相关的字段只有 `"conv": inst.state.get("conv")`，内容是元数据（轮次数、累计字符数），**不含正文**。
  - project.html **已经有「对话记录」板块**（第 139 行起）：标题旁有筛选器「全部 / 用户 / Claude / 执行端」，消息卡片含角色、轮次、时间、正文（超过 600 字折叠为「展开全文（N 字）」）、以及工具调用标签。数据取自 `api_project()` 返回的 `transcript` 字段，实现是 `read_jsonl(inst.transcript_path, limit=300)`。

B. 对话记录现状（落盘）
  - 文件：`<项目根>/workflow/transcript.jsonl`，当前 296386 字节、19 条。
  - 角色分布：`claude` 10 条、`executor` 9 条，**没有 `user` 条**。
  - 字段：`{ts, role, turn, text, tools?}`。
  - 写入点共三处，其中只有两处在写：
    - executor 方向：`wflib/ops.py:47`，`mark_sent()` 里把本轮 message.md 全文追加为 `{role:"executor", ...}`。
    - claude 方向：`wflib/ops.py:81`，`mark_replied()` 里把 reply.md 全文追加为 `{role:"claude", ...}`。
    - user 方向：`wf.py:108` 的 `_record_user()` **只写事件流（`inst.event("user_decision", ...)`）和决策日志（`roles.decide(...)`），不写 transcript**。后果是 project.html 的「用户」筛选器**永远是空的**。
  - 也就是说：transcript 已经具备「双向全文」（executor 与 claude 两个方向都是整篇原文），缺的是用户方向；而「工作流主看板」flow.html 完全没有接入它。

C. Claude 页面快照现状
  - **完全没有这个能力**。适配器 poll 时其实已经拿到了完整会话对象，但只落盘了 `reply.md`（最后一条回复的正文）和它带的附件，**会话快照从未落盘**。
  - 数据源已经在手：`~/.workbuddy-ai/adapter/aimonkey.py` 的 `poll()` 第 292 行 `c = cb.fetch_conversation(cid)`；`claude_bridge.py:247` 的 `fetch_conversation(cid, full=True)` 实际请求 `GET /api/organizations/{org}/chat_conversations/{cid}?rendering_mode=messages&render_all_tools=true`。
  - 实测会话 `f895aab8-4622-4f82-a496-deca223195b2`（"collab-wf 工具层开发"）的返回结构：
    - 顶层 17 个字段：`uuid`、`name`、`summary`、`model`、`created_at`、`updated_at`、`settings`、`is_starred`、`is_archived`、`is_wiggle_enabled`、`workspace_upgraded`、`is_multiplayer`、`effective_thinking_mode`、`is_temporary`、`platform`、`current_leaf_message_uuid`、`chat_messages`。
    - `chat_messages` 20 条，**`index` 取值 0–21 且有跳号（实测序列 0,2,4,5,6,7,8,…,21），`index` 不是数组下标**，定位必须用这个字段本身。
    - 每条消息 13 个字段：`uuid`、`text`、`content[]`、`sender`（human / assistant）、`index`、`created_at`、`updated_at`、`truncated`、`stop_reason`、`attachments`、`files`、`sync_sources`、`parent_message_uuid`。**`text` 字段实测常为空**，正文在 `content[]` 里，必须走 `cb.blocks_text(m)` 取值。
    - `content[]` 的 block 类型计数：`text` 20、`thinking` 21、`tool_use` 27、`tool_result` 27。也就是说完整页面比 transcript 丰富得多 —— transcript 只有最终正文，快照能同时拿到思考与工具调用/结果。
    - 正文体量：human 消息 13083–33991 字，assistant 消息 1198–3082 字；**一次全量快照约 200–250KB**。
  - poll 的触发时机：驱动器在 WAIT_REPLY 期间轮询；`local/transport.json` 已启用 `poll_since: true`，所以轮询会带上 `--since` / `--message` / `--prev-ref`。
  - 适配器 CLI 现有动作是 `send` / `poll` / `quota`；`poll` 的入参是 `--out`（输出目录）、`--ref`、`--since`、`--message`、`--prev-ref`。适配器动作名有白名单约束（`BRIDGE_ACTIONS`，目前是 abort/aborted/accepted/done/download/error/generating/poll/quota/rejected/retry/send），写白名单外的动作名会让 R6 判「无法解析」。
  - 另需留意：适配器任何改动都要同步 `~/.workbuddy-ai/adapter/adapter.json` 里的 sha256 与字节数，并跑 `python3 aimonkey.py selfcheck`；工具层任何改动都要同步 MANIFEST.json 并跑 `wf self-check`。

D. 一个已确认的取舍约束（来自第 10 轮已确认结论）
  - 会话 JSON 的 `chat_messages` 条数与 `current_leaf_message_uuid` **会滞后数分钟**，`updated_at` 是准的。所以任何「用快照条数判断消息是否送达」的设计都不要做；快照只作为展示与取证，不参与状态判定。

⑤ 需要决策的点（请 Claude 裁定，执行端不预设方案）

1. 「工作流面板」指哪个页面 —— 执行端意见：从用户原话「在工作流看板里面，加上……而且……在工作流面板里面有对话记录」看，两处措辞都指向**主看板 flow.html**（它是访问 `/` 的默认页），而 project.html 其实已经有对话记录了。但用户很可能并不知道 project.html 的存在，所以也可能他想要的就是「在他天天看的那个页面上能看到」。可选：只在 flow.html 加；或 flow.html 加摘要 + 跳转 project.html 看全文；或两处都加。
2. 「原封不动」的口径 —— 执行端意见：现有 transcript 的正文就是逐字原文（message.md / reply.md 全文），但**不含 thinking 与工具调用**；而会话快照能拿到 `text` + `thinking` + `tool_use` + `tool_result`。用户说的「原封不动」是否要求把 thinking 与工具调用也展示出来？如果要，快照就是唯一的来源。
3. 快照的落盘与保留策略 —— 执行端意见：会话已经 20 条消息、单次全量约 200–250KB，而每轮会 poll 多次（WAIT_REPLY 期间反复轮询）。若每次 poll 都整份落盘，累积速度很快。需要定：按轮次存还是覆盖「最新一份」；要不要做去重（内容未变就不重复写）；要不要限制大小或只保留最近 N 份。
4. 快照与看板的连接方式 —— 执行端意见：看板是纯静态 HTML + 一个只读 JSON 接口，所以快照要么由接口读取并返回给前端渲染，要么作为文件通过现有的 `/files/<项目>/<相对路径>` 路由给前端自己取。请裁定用哪种，以及是否要新增接口。
5. 是否顺带补上 user 方向 —— 执行端意见：project.html 的「用户」筛选器现在永远是空的（`_record_user()` 不写 transcript）。这属于本轮需求「每次对话都原封不动有记录」的直接缺口，建议一并处理，但请 Claude 判断是否纳入本轮范围。

⑥ 需要注意（Attention）
  - 无自动处置、无异常、无阻塞；本轮执行端只做了只读勘察（读文件、`wf version` / `wf self-check` / `wf doctor`、一次只读的 `fetch_conversation` GET）。
  - 用户当前没有待决定事项（PROJECT.md 决策区为空）。

【本机执行】
无（等 Claude 出方案后再落地）

【本轮回报清单】
- 第 10 轮要求的 1.5.2 已构建并安装完成（见 ② 的三段终端原文）

【需要用户决定】
无

【下一轮建议档位】high

【驱动指令】
发布：无
之后：无（请 Claude 在本轮给出看板快照与对话记录的方案与代码）
