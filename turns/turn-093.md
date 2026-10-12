【回报 · 第 93 轮 · v2.9】档位：high

① 环境障碍与偏离
- 唤醒投递连续失败 2 次：任务码 collab-wf-dev|task:T91-3 未能送达执行端；详见本机 wake.log

说明（备注）：
- 执行端待办 T91-2 已完成（I1 真实发送核对：在适配器动作日志里找到第 91 轮回报与本轮之后第一次发送的记录，逐字复制它们所用的对话（应为 collab-wf-dev 登记的对话）与告）：I1真实发送目标核对已完成（不是dry-run）。collab-wf-dev登记对话=021f6fff-de4b-42ac-878d-8f4838ba4143。第91轮动作日志第1397行逐字原文：{"ts": "2026-10-12T01:53:16.694073+00:00", "action": "send", "turn": 91, "chars": 2394, "detail": "发往 021f6fff-de4b-42ac-878d-8f4838ba4143"}。之后首次真实发送为第92轮，逐字原文：{"ts": "2026-10-12T02:42:29.014190+00:00", "action": "send", "turn": 92, "chars": 3869, "detail": "发往 021f6fff-de4b-42ac-878d-8f4838ba4143"}。两行均未出现warning字段；本次核对第91/92轮无conv_from_default。wf audit --n 10000筛第91/92轮，ADAPTER_WARNING count=0，第92轮有DONE→COMPOSE及COMPOSE→SEND记录。两个真实send均指向本项目登记对话，不是amy-local的d14def32-e622-42ae-b569-5c794870ad88。1.7.1证据生成的第92轮回报已核对，含②关键数字与终端原文、④需要注意；其中明确未审查而非假称pass。此次只证明实际发送选择与核对时无告警，不把send动作等同接受/回复落地；第92轮正在发送，交既有驱动器继续确认，绝不重发。T91-2结果写入备注随下一份标准回报带上，不重复kick。
- 执行端待办 T91-3 已完成（以上两项做完、1.7.1 装好后，用 wf kick --kind report（不带 --message）发回报，说明先用 wf note --kind in）：1.7.1实景回报结构验收完成：先wf note --kind info记录阶段核验与T91-2/T91-3前置环，再用wf kick --kind report（不带--message）从第91轮证据生成workflow/outbox/turn-092.md。逐字标题【回报 · 第 92 轮 · v2.9】档位：high；包含②关键数字与终端原文（c01-c06原文、退出码、耗时）和④需要注意，审查如实标unreviewed/no_session。第92轮实际send动作已于2026-10-12T02:42:29.014190+00:00出现，指向本项目登记对话，T91-1/T91-2现均完成。因T91-2需第92轮发送、T91-3需T91-2先完成，只先生成一次回报解除前置环；没有重复kick。本项完成仅指真实证据生成与启动发送，消息接受/回复落地仍交驱动器确认，不假称已收回复。T91-2最终结果已由并发执行端写入，随下一份标准回报带上。
- 执行端待办 T92-1 已完成（1.7.2 装好后，把 collab-wf-dev 切到自己的定时任务，按顺序做：① 运行 wf --project collab-wf-dev schedul）：已切换：① wf schedule plist 生成的 plist 存为 ~/Library/LaunchAgents/local.collab-wf.tick.collab-wf-dev.plist，plutil -lint OK，launchctl bootstrap gui/501 加载 exit=0，进程 94305 在跑；② 全局 local.collab-wf.tick 未动；③ 三项原文：『wf schedule status：collab-wf-dev：自己的定时任务 local.collab-wf.tick.collab-wf-dev，周期 60 秒；最近一次运行 0 分钟前，退出码 30；amy-local：已静音（wf.json auto=false，驱动器跳过），不计入』；『tick-collab-wf-dev.log：collab-wf-dev：exit=30 全部目标判据已通过』；『全局 tick.log：collab-wf-dev：exit=None 有自己的定时任务（wf schedule plist --project），由它推进』。未回滚。
- T92-2 · S2 接口评估（执行端视角，1.7.3 输入）。逐项（对应 ISOLATION.md 第 2 节第 6/7/8/10/12 项与第 4 节）：

【#6 收件箱·分项目视图】能实现，执行端改动小。现状 inbox.json 单文件、每项已带 project（wake_dispatch.py:232 已按 item.project 取值）。执行端继续读全局汇总（第 4 节也定『全局汇总不变，执行端读』），分项目视图只作为只读投影。接口意见①：视图必须由工具单方面生成与维护，执行端只读不写——否则出现两个真相源，违反教训 52（真相源必须唯一且实时）；执行端改 item 状态仍写全局文件。接口意见②：wf ask --project 请同时给机器可读输出（--json），便于看门狗与 wake 按项目过滤。

【#7 信号·分项目目录】能实现，改动小到中。现状 local/signals/ 平铺（例：amy-local_handoff-blocked-93787d5c.json），写入方是执行端程序经 wf signal add（review_adapter.py:169 实测调用 wf --project <p> signal add --key <key>）。接口意见①：只要 wf signal add 在带 --project 时自动落到 signals/<名>/，执行端程序一行都不用改——写路径应完全由工具决定，执行端不感知；接口意见②：『旧位置兼容读一个版本』要定义清楚谁读——建议工具提供统一的 signal 读取命令（如 wf signal list --project），执行端与看门狗都不直接 glob 目录；迁移完成时旧位置残留文件由工具一次性搬移，不要求执行端清理；接口意见③：看门狗写信号同样经 wf 命令（watchdog 现在就是），保持。

【#8 审查键】能实现，review_adapter 需加一个 --key 参数（小改动）。现状：request 子命令从 release zip 文件名推键——这正是在 I4 场景（两项目同名包）会撞车的环节；verdict 子命令收 --request <路径> + --request-id（review_adapter.py:256-259）。接口意见①：--key 必须由工具显式传入，废除从文件名反推；接口意见②：verdict 侧 --request <路径> 保留不变（路径本身已唯一），--request-id 继续作身份核验；接口意见③：review heartbeat 文件、unreviewed 标记文件、signal add 的 --key 全部用同一个键，避免一套逻辑两套命名；接口意见④：键里含轮次 t<N>，重试/重开轮次时键会变——工具要定义『同一包在同一轮内重审』是否复用键（建议复用，避免遗留 pending 请求悬空）。

【#10 唤醒·按项目】能实现，是执行端最大的一项改动。现状单目标模型：discover_sessions() + pick_target() 全局挑一个会话，PROJECT_CWD 写死 collab-wf-dev 工作区（wake_dispatch.py:66）。改法：pick_target 从『挑一个』改为『按 item.project 挑候选（会话 cwd 命中 project_root(project)）』——project_root() 已存在（wake_dispatch.py:210，查 registry），复用即可。接口意见①：某项目没有开窗口时按 P0-D 处理（不消耗 attempts、按 tick 重试），且 no_gateway 告警的 key 要从全局改为『project+key』，否则 A 项目的没窗口会挡住 B 项目的告警去重；接口意见②：投递失败只影响该项目——per-key 状态结构天然支持，保持；接口意见③：请工具给 wake_dispatch 提供 project→会话 的权威判据之外，再加一个 wf 侧命令（如 wf wake status --project <名>）以便看门狗观察分项目唤醒状态；接口意见④：执行端会话的发现靠 cwd，用户为一个项目开的窗口必须把工作区开在该项目目录下——这个约束要写进用户可见的说明，否则 I6 无法成立。

【#12 审查程序参数化】基本已完成。review_adapter request 子命令的 --project、--turn 现在就是必填参数（review_adapter.py:250-251），项目/轮次/键都从参数取；第 2 节说『工具侧的 review_adapter.json 本来就写进本项目本轮的证据目录』，与现状一致。剩余工作与 #8 是同一件事（补 --key）。

【第 4 节目录布局】与现状吻合，执行端无新增目录需求。唯一提醒：local/projects/<名>/inbox.json 与项目工作树 ~/.workbuddy-ai/projects/<名>/ 是两个完全不同的东西，文档里请显式写清，避免后续误把视图目录当项目目录。

【实施顺序建议】#7/#8/#12 先做（工具+执行端各一处小改，可同版交付）；#6 其次；#10 最后且独立验收 I6。S2 全部落地前，S3 已先行完成（T92-1），无依赖冲突。
- 执行端待办 T92-2 已完成（S2 接口评估（作为 1.7.3 的输入，做法同 T89-2）：读 ISOLATION.md 第 2 节第 6–8、10、12 项与第 4 节，评估 S2 对执）：S2 接口评估已写入 wf note --kind info（全文）。结论：#6/#7/#12 能实现且执行端改动小；#8 需 review_adapter 加 --key（小改，废除从 zip 文件名反推键）；#10 能实现且改动最大（pick_target 按项目 cwd 分流，project_root 已存在可复用，no_gateway 告警 key 要改 project+key，用户开窗口须开在项目目录下）。四条接口意见与实施顺序建议（#7/#8/#12 先行，#6 次之，#10 最后独立验收 I6）已逐条写全。
- T92-3 · 前置说明：T92-1（S3 项目定时任务）与 T92-2（S2 接口评估）均已完成，结果详见各自 task done 与 info 备注（T92-2 全文在前一条 info）。本回报由 wf kick --kind report 从证据生成，含终端原文与需要注意。

② 关键数字与终端原文

1) 构建通过，自检 243/243，成员集合一致，floor.py 未改动；记下版本包的 sha256。 —— 通过
```
$ /Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-092/delivered/build_wf_172.py --out workflow/outbox
构建通过：collab-wf 1.7.2，自检 243/243，63 个文件 + MANIFEST.json；成员集合核对一致、没有 local/.git/__pycache__/*.pyc；floor.py 未改动；版本包 collab-wf-1.7.2.zip sha256 e8f80f9045c4d94b79622b70e609386d5a48123cdfae75b68c1e473e9d5489ba
exit=0  耗时 148.6s
```

2) 驱动器自动安装 1.7.2，升级核验通过；审查结论以原文为准。 —— 通过
```
$ wf self-update --auto workflow/outbox/collab-wf-1.7.2.zip
工具已升级：1.7.1 → 1.7.2（f044fa1），自检已通过
看板已重启以加载新版本
[stderr]
开始等独立审查（上限 300 秒；到时没有结论按“未审查”继续安装）
仍在等独立审查：已 30 秒 / 上限 300 秒
仍在等独立审查：已 60 秒 / 上限 300 秒
仍在等独立审查：已 90 秒 / 上限 300 秒
仍在等独立审查：已 120 秒 / 上限 300 秒
仍在等独立审查：已 150 秒 / 上限 300 秒
仍在等独立审查：已 181 秒 / 上限 300 秒
仍在等独立审查：已 211 秒 / 上限 300 秒
仍在等独立审查：已 241 秒 / 上限 300 秒
本次未审查：{"status": "unreviewed", "reason": "review deadline elapsed"}；照常安装，可事后按 /Users/admin/.workbuddy-ai/tools/collab-wf/local/reviews/collab-wf-1.7.2.diff 补审
正在运行新版本 1.7.2 的完整自检
升级核验通过：磁盘版本 1.7.2，63 个文件与版本包一致，看板已加载 1.7.2
exit=0  耗时 451.9s
```

3) 工具版本为 1.7.2。 —— 通过
```
$ wf version
collab-wf 1.7.2（wf-v1.7.2）  安装位置：/Users/admin/.workbuddy-ai/tools/collab-wf
可回滚的版本：wf-v1.7.2、wf-v1.7.1、wf-v1.7.0、wf-v1.6.9、wf-v1.6.8、wf-v1.6.7、wf-v1.6.6、wf-v1.6.5、wf-v1.6.4、wf-v1.6.3、wf-v1.6.2、wf-v1.6.1、wf-v1.6.0、wf-v1.5.9、wf-v1.5.8、wf-v1.5.7、wf-v1.5.6、wf-v1.5.5、wf-v1.5.4、wf-v1.5.3、wf-v1.5.2、wf-v1.5.1、wf-v1.5.0、wf-v1.4.3、wf-v1.4.2、wf-v1.4.1、wf-v1.4.0、wf-v1.3.1、wf-v1.3.0、wf-v1.2.2、wf-v1.2.1、wf-v1.1.0、wf-v1.0.3、wf-v1.0.2、wf-v1.0.1、wf-v1.0.0
当前项目 collab-wf-dev 固定版本：1.7
exit=0  耗时 0.6s
```

4) self-check 完全一致。 —— 通过
```
$ wf self-check
对照 1.7.2 的文件清单：完全一致
exit=0  耗时 0.6s
```

5) 仓库指纹仍为 95674b23bab4790e（本轮不落地项目版本）。 —— 通过
```
$ wf fingerprint
仓库指纹 95674b23bab4790e（10 个文件）
exit=0  耗时 0.3s
```

6) `wf goals` 原文，应为 7/7。 —— 通过
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
exit=0  耗时 0.3s
```

③ 需要决策的点
无。

④ 需要注意（Attention）
- 自动处置 NET → retry：响应头: {'Access-Control-Expose-Headers': 'X-Bard-Mode', 'Alt-Svc': 'h3=":443"; ma=2592000', 'Cache-Control': 'no-cache, no-transform', 'Cf-Cache-Status': 'DYNAMIC', 'Cf-Ray': 'a492c067281433ad-LAX', 'Content-Language': '', 'Content-Type': 'text/event-stream', 'Date': 'Mon, 12 Oct 2026 02:42:30 GMT', '
- 自动处置 SEND_UNKNOWN_WAIT → retry：发送意图 2026-10-12T02:42:20+00:00 结果不明；轮询返回 none
- 自动处置 SEND_UNKNOWN_WAIT → retry：发送意图 2026-10-12T02:42:20+00:00 结果不明；轮询返回 none
- 当前对话已累计约 90367 字符、6 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

附：仓库指纹 95674b23bab4790e（v2.9，10 个文件）
