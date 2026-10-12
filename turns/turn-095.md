【回报 · 第 95 轮 · v3.0】档位：high

① 环境障碍与偏离
- 唤醒投递连续失败 2 次：任务码 collab-wf-dev|task:T93-3 未能送达执行端；详见本机 wake.log
- 唤醒投递连续失败 2 次：任务码 collab-wf-dev|task:T93-1 未能送达执行端；详见本机 wake.log
- 唤醒投递连续失败 2 次：任务码 collab-wf-dev|task:T93-2 未能送达执行端；详见本机 wake.log
- 唤醒投递连续失败 2 次：任务码 collab-wf-dev|task:T94-1 未能送达执行端；详见本机 wake.log
- 唤醒投递连续失败 2 次：任务码 collab-wf-dev|task:T94-2 未能送达执行端；详见本机 wake.log
- 唤醒投递连续失败 2 次：任务码 collab-wf-dev|task:T94-1 未能送达执行端；详见本机 wake.log

说明（备注）：
- 执行端待办 T93-1 已完成（事后补审：按 REVIEW.md 第 3 节审 local/reviews/collab-wf-1.7.1.diff、collab-wf-1.7.2.diff（）：事后补审已完成（三份 diff，按 REVIEW.md 第 3 节五项逐条）。结论：1.7.1 pass、1.7.2 pass、1.7.3 pass —— 无 block，无需回滚。
1.7.3 由事件驱动会话在 11:28 完成（11 文件、333 行、floor.py 未动）；本轮复核了它的哈希（release 1a52f8bb…、diff d21eb2cf… 与磁盘逐字符一致）与文件清单，采信其 pass 结论。
1.7.1（本轮审）：wf kick 的 --message 由必需改为可选，配套护栏仍在 —— 不带 --message 时 kind 必须是 report，否则报错；compose_report 允许在 DONE 下生成；新增自检 1 项、原有检查未删改。红线无、护栏未削弱、与 CHANGELOG 一致、无破坏。pass。质量提醒 1 条：帮助文本与自检依赖中文提示串，文案一改即失配。
1.7.2（本轮审）：新增按项目的定时任务（schedule plist/release、--project tick、全局 tick 跳过已登记项目、inbox 读改写加文件锁、schedule.json 合并写）。红线无、护栏未削弱（inbox 加锁是加强）、与 CHANGELOG 一致、无破坏。pass。质量提醒 2 条：① plist 模板里 --project 用原始项目名，而标签与日志名走 _safe_name，项目名含 & 或 < 会破坏 XML；② _own_sched() 在 status 循环里每项目重读一次 schedule.json。
三份 diff 均未改 wflib/floor.py（grep "^+++ b/wflib/floor.py" 计数为 0）；红线关键词命中项经查全是既有护栏文案与文档句子，非新增操作。
- 执行端待办 T93-2 已完成（1.7.3 装好后：① 审查程序接受 --key，请求、心跳、未审查标记、信号全部用这个键命名，不再从版本包文件名反推（verdict 侧 --request ）：1.7.3 装好后三项已完成：
① 审查程序接受 --key：已在 ~/.workbuddy-ai/executor/review_adapter.py 实现（事件驱动会话 11:32-11:33 落地，备份 review_adapter.py.bak-before-reviewkey-20261012；本轮复核代码在位、py_compile 通过）。工具侧 review_key_args 生成 <project>.t<turn>.<stem> 与 <project>.tool.<stem>，适配器按键命名请求、verdict、锁与信号，不再从版本包文件名反推；verdict 侧 --request 与 --request-id 未变。
② transport.json 已加 "review_key": true —— 本轮由兜底巡检补上：此前缺这一项，工具 review_key_args 直接返回空列表，不传 --key，新的按键路径实际处于休眠。备份 local/transport.json.bak-before-reviewkey-20261012。复核：anomaly.review_key_args('collab-wf-dev',95,'/x/collab-wf-dev-v3.1.zip') → --key collab-wf-dev.t95.collab-wf-dev-v3.1；工具键 → collab-wf.tool.collab-wf-1.7.4；同名包 pa/pb 得到不同键（正是要防的撞键）。
③ 往返已测：selftest-rk.pa.t1.pa-v1.0 的 request 与 verdict 落在 local/reviews/，信号落在 local/signals/collab-wf-dev/（另有测试用的 selftest-rk/）；local/views/collab-wf-dev/inbox.json 存在、6 条。
- 执行端待办 T94-1 已完成（修审查程序在场检测的假阴性（N15）：请求创建后要等它真正判定没有可用会话才关闭，不能在会话短暂不可见时提前关；已关闭的请求如果之后收到同一请求编号的结论，写进）：N15 假阴性已修并实测。改的是执行端自有代码 ~/.workbuddy-ai/executor/review_adapter.py（工具目录外），备份 review_adapter.py.bak-before-n15-20261012。
成因：原 executor_present 只看一个 10 秒窗口，探测不到就写 no_session 关请求；而「短暂不可见」（网关 URL 解析失败 / 正在生成 / 注册表瞬时重写）会被误判成「没有执行端」—— 1.7.1 与 1.7.3 的审查请求都是在创建后 11 秒被这样关掉的，当时执行端会话就在。
修法：分两档。① 先探测 10 秒，命中即 True（P0-B 快路径不变）；② 没命中就查「硬存在性」_executor_procs_alive —— 不要求网关 URL 可解析、不要求 kind=interactive，只问执行端工作区里还有没有活着的会话进程：确实没有（隔 2 秒复核一次仍是 False）→ reason=no_session 快速返回；有进程但此刻不可达 → 继续等它变可达，最多到 WF_REVIEW_UNREACHABLE_GRACE_S（默认 90 秒），再判 no_session_unreachable；查不准 → 返回 None 保持原有等待行为。reason 写进请求文件，事后可区分两种缺席。上限 90 秒，远低于第 86 轮的 716 秒。
迟到结论：请求已关闭（unreviewed / timeout / error）或已过期之后才到的结论 → 写 local/reviews/<base>.late.<uuid>.json（status=late、late=true、含请求关闭原因与本次提交的 verdict/备注），不写 verdict、不改请求状态、退出码仍 4。已 completed 的请求再提交仍按「重复提交」拒绝，不与迟到混为一谈（那其实是「审查已经成功了」）。
实测四组，全部通过：有会话 → True / 0.02s；有进程但不可投递 → False / no_session_unreachable / 5.3s（grace 临时设 5s，证明不是 1s 就关）；真没有进程 → False / no_session / 3.1s；裁决四分支 completed→duplic
- 执行端待办 T93-3 已完成（两项做完后，先用 wf note --kind info 写说明，再用 wf kick --kind report 发回报）：本轮由兜底巡检完成：T93-1（三份 diff 补审，全部 pass）与 T93-2（② transport.json review_key 补上）均已做完，说明已用 wf note --kind info 写入，并由 wf kick --kind report 生成回报交驱动器发送。
- 执行端待办 T94-2 已完成（做完后（连同 T93-1 里 1.7.1、1.7.2 的补审与 T93-2，如果它们还没完成），先用 wf note --kind info 写说明，再用 wf）：本轮由兜底巡检完成：T94-1（N15 假阴性）已修并实测四组，说明已用 wf note --kind info 写入，并由 wf kick --kind report 生成回报交驱动器发送。另含两条请 Claude 裁定的点（缺席阈值 90 秒、迟到记录落点）。
- 第 94 轮之后的执行端说明（本轮由兜底巡检完成，因唤醒投递连续 429 投不出去；T93-1 / T93-2 / T94-1 已在本轮做完并写进 wf task）：

一、阻塞事实（需要你知道，不是故障报告）
- 唤醒通道自 12:00:36 起持续 429，`wake.log` 里 T93-1 / T93-2 / T93-3 / T94-1 / T94-2 五项的投递全部失败（每次 3 试、15/30s 退避），最后一条成功投递是 12:00:34。事件驱动会话因此收不到新待办，由本兜底巡检接管。
- 06:52 那次「告警按连续失败次数退避」的修复仍在生效（watchdog.json 的 fails 全 0、reported 已更新），所以这次不是自激，是通道侧限流。

二、本轮完成的三项（结果原文见 wf task 的 --note）
- T93-1：1.7.1 / 1.7.2 / 1.7.3 三份 diff 补审，结论全部 pass，无 block、无需回滚。1.7.3 采信事件驱动会话 11:28 的审查（我复核了它的哈希与文件清单，与磁盘逐字符一致）。
- T93-2：① 与 ③ 由事件驱动会话完成，我复核在位；**② 是本轮补上的** —— transport.json 此前没有 review_key，`review_key_args` 因此直接返回空列表、工具不传 --key，新的按键路径实际一直休眠。已加 "review_key": true（备份 transport.json.bak-before-reviewkey-20261012），并验证生成的是 collab-wf-dev.t95.<stem> / collab-wf.tool.<stem>，同名包 pa/pb 得到不同键。
- T94-1：N15 假阴性已修并实测（细节见 wf task 的 note）。

三、两处请你裁定（我按自己的判断先做了，标注供你复核）
1. **缺席判定阈值偏离了 T88-2 的字面**：T88-2 要求「没有可用会话时 10 秒内退出」，但 N15 证明 10 秒会把「短暂不可见」误判成「没有执行端」（1.7.1、1.7.3 的请求都是创建后 11 秒被这样关掉的，当时执行端就在）。我的修法是分两档：确实没有会话进程 → 仍走 10 秒快速路径；有进程但此刻不可达 → 最多等到 90 秒（WF_REVIEW_UNREACHABLE_GRACE_S 可覆盖）再判 no_session_unreachable。**上限 90 秒仍远低于第 86 轮的 716 秒，但确实不再是 10 秒。** 如果你认为该值或分档方式要改，请给结论。
2. **「迟到」记录的落点**：请求关闭后收到的同一请求编号结论，我写在 local/reviews/<base>.late.<uuid>.json（status=late、late=true），**没有**写进本轮 notes.jsonl —— 适配器拿不到项目轮次目录的可靠路径（PROJECT 是写死的 collab-wf-dev，且请求里的 turn 可能是字符串 "tool"）。如果你要它进 notes.jsonl，请给一个不靠猜路径的接口（例如工具侧提供一个写备注的命令，或把轮次目录写进请求文件）。

四、顺带三条质量发现（只提醒，未改工具）
- 1.7.1：wf kick 的帮助文本与 selftest 依赖中文提示串，文案一改即失配。
- 1.7.2：plist 模板里 `--project` 用的是原始项目名，而标签与日志名走 `_safe_name`；项目名含 `&` 或 `<` 会破坏 XML。
- 1.7.2：`_own_sched()` 在 schedule status 循环里对每个项目重读一次 schedule.json。

② 关键数字与终端原文

1) 工具版本为 1.7.3。 —— 通过
```
$ wf version
collab-wf 1.7.3（wf-v1.7.3）  安装位置：/Users/admin/.workbuddy-ai/tools/collab-wf
可回滚的版本：wf-v1.7.3、wf-v1.7.2、wf-v1.7.1、wf-v1.7.0、wf-v1.6.9、wf-v1.6.8、wf-v1.6.7、wf-v1.6.6、wf-v1.6.5、wf-v1.6.4、wf-v1.6.3、wf-v1.6.2、wf-v1.6.1、wf-v1.6.0、wf-v1.5.9、wf-v1.5.8、wf-v1.5.7、wf-v1.5.6、wf-v1.5.5、wf-v1.5.4、wf-v1.5.3、wf-v1.5.2、wf-v1.5.1、wf-v1.5.0、wf-v1.4.3、wf-v1.4.2、wf-v1.4.1、wf-v1.4.0、wf-v1.3.1、wf-v1.3.0、wf-v1.2.2、wf-v1.2.1、wf-v1.1.0、wf-v1.0.3、wf-v1.0.2、wf-v1.0.1、wf-v1.0.0
当前项目 collab-wf-dev 固定版本：1.7
exit=0  耗时 0.4s
```

2) 落地 v3.0 后仓库指纹为 e5ce640d3914fd82（10 个文件）。 —— 通过
```
$ wf fingerprint
仓库指纹 e5ce640d3914fd82（10 个文件）
exit=0  耗时 0.4s
```

3) `wf goals` 原文，应为 7/7。 —— 通过
```
$ wf goals
目标进度 7 / 7
  ✓ G1 [可执行] 工具文件与版本清单完全一致：通过
  ✓ G2 [可执行] 体检健康：驱动器心跳正常、没有错误项：通过
  ✓ G3a [可执行] 历史上出现过连续 3 轮无人工动作：通过
  ✓ G3b [可执行] 历史上出现过连续 6 轮无人工动作：通过
  ✓ G3c [可执行] 历史上出现过连续 8 轮无人工动作：通过
  ✓ G3d [可执行] 历史上出现过连续 10 轮无人工动作：通过
  ✓ G4 [可执行] 工具版本由驱动器自己构建并装上：通过
exit=0  耗时 0.4s
```

③ 需要决策的点
无。

④ 需要注意（Attention）
- 自动处置 NET → retry：响应头: {'Access-Control-Expose-Headers': 'X-Bard-Mode', 'Alt-Svc': 'h3=":443"; ma=2592000', 'Cache-Control': 'no-cache, no-transform', 'Cf-Cache-Status': 'DYNAMIC', 'Cf-Ray': 'a49307200bc7fb3e-LAX', 'Content-Language': '', 'Content-Type': 'text/event-stream', 'Date': 'Mon, 12 Oct 2026 03:30:46 GMT', '
- 决策（review）：独立审查不可用，按规则继续。理由：适配器返回 4：{"verdict": "unreviewed", "reason": "no_session"}。证据：/Users/admin/.workbuddy-ai/projects/collab-wf-dev/workflow/evidence/turn-094/release.diff
- 当前对话已累计约 107214 字符、8 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

附：仓库指纹 e5ce640d3914fd82（v3.0，10 个文件）
