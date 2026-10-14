# collab-wf-dev · 项目状态（collab-wf 自动同步）

第 146 轮 · 等待计划节点（WAIT_SCHEDULE）

## 版本
- 工具：collab-wf 1.9.7
- 项目：v3.12；924204a (HEAD -> main, tag: v3.12) release v3.12
- 仓库指纹：b66b4395f4223063（12 个文件）

## 最近一轮
- 第 146 轮验收通过（2026-10-14T12:26:53+00:00）
- 本方最新消息：`turns/turn-146.md`
- Claude 最新回复：`replies/reply-146.md`

## 目标
- 当前阶段 M2 专业基线（第一批）：交接、动态目标与防循环（取代 M1）
- 进度 9 / 12
- ✓ M2-B1 阶段目标：看板与 STATUS 先显示当前阶段，旧阶段只作历史：通过
- ✓ M2-B2 待办可取消、可被取代，完成必须写结果：通过
- ✓ M2-E1 NO_REF 不再循环：通过
- ✓ M2-F1 回复完整性：识别工具上限并有界续接：通过
- ✓ M2-H1 同步凭证：没有提交号不算推送：通过
- ✓ M2-A1 交接短消息钉在提交上、只链已推送的文件：通过
- ✓ M2-F2 回复发现延迟有界：通过
- ○ M2-C1 默认工程门禁基线与角色准入退出：输出不含“探针 门禁基线：通过”
- ✓ M2-D1 经验闭环：台账缺项不得标已解决：通过
- ✓ M2-G1 共享资源仲裁 S4：通过
- ○ M2-R1 实景：连续 3 个正式轮次全链路达标（LIVE.md）：退出码 1，期望 0；输出不含“——通过”
- ○ M2-U1 用户验收 M2：前置未满足：M2-C1、M2-R1（未到确认的时候，不通知）
- 历史阶段 M1 无人值守稳定运行：已完成（2026-10-12）；7/7：G1 工具文件与清单一致、G2 体检健康、G3a–d 历史最长连续 3/6/8/10 轮无人工动作、G4 驱动器自建自装；第 71 轮达成，第 98 轮由用户授权执行端代为验收；原判据见 v3.2 的 goals.json（git 历史）

## 待办
- [executor] 执行端待办 T137-1：为“执行端对话一停就续接”做宿主能力实证，只读、不改调度：宿主有没有可靠的“轮次结束/空闲”事件或回调（写明入口与实测原文）；如果有，触发后怎样证明唤醒被消费（不以 PID 存活或投递 runId 代替）；如果没有，独立观察器能做到的最短检测周期与它判断“空闲且仍有可推进待办”的依据。结果用 wf task done 写回，设计等结果出来再定。
- [executor] 执行端待办 T141-2：为三会话 180 秒巡查取宿主事实原文，只读、不启用任何 hook、不 resume、不改 launchd：① 官方 CLI 的可执行文件路径、版本，resume 与非交互（print）模式的帮助原文；② 对一个已存在的会话 resume 时是追加到原会话还是分叉，图形界面开着时是否允许、并发 resume 时会怎样（引用源码行号）；③ hook 的配置位置与格式，Stop、SessionEnd 
- [executor] 执行端待办 T142-2：为巡查主体补三项原文，只读或只在一次性测试会话上做：① hook 配置的确切 JSON 结构（含事件名、matcher、command 字段、timeout），以及项目级配置被实际读取的完整路径（附源码行号）；② hook 的输入方式（是否经 stdin 传 JSON）、退出码含义、超时与失败时的宿主行为；③ 新建一个一次性测试会话（不用 main、repair、live），让一个 codebud
- [executor] 执行端待办 T144-1：按 patrol-0.1.zip 里 README 的“安装”在演练模式下落地并验收（resume_enabled 保持 false，不 resume 任何会话、不运行 wf tick、不改已有 launchd 任务）：① 解压到 /Users/admin/.workbuddy-ai/adapter/patrol/，核对包的 sha256 后运行 patrol.py selftest，应为 16/
- [executor] 执行端待办 T144-2：适配器接上实景测量需要的字段：发送日志按项目与轮次记录、发送时给出用户消息的 uuid、poll 完成时给出回复的 parent 与可信的 ended_at；改完用一轮真实收发核对 reply_meta.json 与 send_meta.json 的原文，写进 wf task done。
- [executor] 执行端待办 T146-1：审读并离线核验 patrol-0.3.zip，只在临时目录里做，不启用 resume、hook、launchd，不碰 main、repair、live 会话，也不装到 adapter 目录：① 核对包的 sha256 与 patrol.py（23a15c7d…）、patrol_hook.py（59851c49…）的 sha256；② 运行 patrol.py selftest，应为 32/32；③
- [executor] 告警隔离复核未闭合：此前清除过早：独立复核发现正文推断归属、共享父目录错选和关闭投递误记成功。已修三处并离线回归，仍缺全局运维告警递送与真实双项目验收。

## 最新产物
- `artifacts/collab-wf-1.9.7.zip`
- `artifacts/collab-wf-dev-v3.12.zip`
