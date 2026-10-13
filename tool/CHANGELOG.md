# CHANGELOG（collab-wf）

## 1.8.7 — 2026-10-13

经验闭环检查、回复与交付同源（collab-wf-dev 第 113–116 轮；P0-D 的 M2-D1、W83）。
- 新增只读命令 wf ledger：台账里标“已解决”却缺根因、版本或回归证据的条目被列出，退出码 1。
- 回复正文与交付文件来自不同消息时不收下（适配器给出 files_message_uuid 时）。
- 回执码：驱动器发出的每条消息末尾附回执码，回复在驱动指令里写“回执：<码>”；不一致就在评审前停下。
- 交付核对：回复为每个交付文件写“交付 N：<文件名> <sha256>”；缺失、不符、要落地的版本包没声明，都在评审前停下。
- 1.8.6 这个版本号在第 115 轮被两条分叉的回复各用了一次、内容不同，都没有安装，作废不用。
- DRIVER.md 第 38 节、TRANSPORT.md 附录 1.8.7。自检新增 4 项；原有检查没有删改。本版不改 floor.py。

## 1.8.5 — 2026-10-13

回复发现延迟有界、待发消息核对、驱动器单实例（collab-wf-dev 第 105–113 轮；W66、W81、W82）。
- 发送结果不明时确认间隔最多 send_unknown_poll_s（默认 120 秒），按墙钟等满 send_unknown_window_s（默认 1 小时）才阻塞；确认时回复已结束就立即去取。
- 状态在“发送”却没有待发消息时不调用适配器。
- 按项目状态里的进程号保证同一项目只有一个驱动器；self-update 锁放在工具目录里。
- DRIVER.md 第 37 节。自检新增 3 项；1.8.4 的安装互斥自检随锁的位置改了路径。本版不改 floor.py。

## 1.8.4 — 2026-10-13

计划绑定环境、手工执行防重、安装互斥（collab-wf-dev 第 110–111 轮；W77、W78）。
- 每条命令启动前核对工具版本与项目指纹；被别处改动时剩下的命令不执行，记 PLAN_STALE 并回报。
- 驱动器正在推进时，手工 wf exec 被拒绝（先暂停）。
- wf self-update 全局互斥，拿不到锁立即失败。
- DRIVER.md 第 36 节。自检新增 3 项；原有检查没有删改。本版不改 floor.py。

## 1.8.3 — 2026-10-13

决定与投递分开、人工目标的前置（collab-wf-dev 第 107–109 轮；W70、W75、W76）。
- inbox 每一项带 decision_owner 与 delivery_owner（一律执行端）；2 小时升级只让用户看见（notify_user），决定归属不变；确认人工目标归用户决定。
- 人工目标可写 depends_on（列表或 "all_exec"）：前置没满足时不算完成、不通知、不能确认。
- 同步的 artifacts/ 只放项目版本包，同名不重复。
- DRIVER.md 第 35 节。自检新增 3 项；原有检查没有删改。本版不改 floor.py。

## 1.8.2 — 2026-10-13

交接与目标的硬门（collab-wf-dev 第 104–107 轮；需求 P0-A、P0-B、P0-E、P0-F、P0-H 的工具侧第一批）。1.8.0 构建过（255/255），安装前独立审查发现暂存目录会跟随符号链接写穿（block）；1.8.1（257/257）修了链接，审查又实证普通文件的硬链接会被 O_TRUNC 写穿（block）。两版都没有安装，版本号不复用；本版在 1.7.6 上重新构建。
- 暂存目录安全：拒绝不安全路径与链接上级，链接只删不跟随；写入用新 inode 临时文件加原子替换，不截断旧 inode；file 链接、dir 链接、根链接、路径穿越、硬链接、写失败都有负向测试。
- 执行与取证时每条命令启动前重读暂停，停线边界写明（第 106 轮：暂停后下一条命令仍被执行）。
- 阶段目标：goals.json 可声明 phase 与 history；wf goals、STATUS.md 先写当前阶段，历史阶段单列、不计入当前进度。
- 待办可取消与被取代（wf task cancel / supersede，必须写原因；已完成的只能改记为被取代并保留原记录）；wf task done 必须写结果；关闭的待办不再进 inbox。
- NO_REF：wf approve 拒绝；只能补登记 ref（wf mark sent --ref）或整轮手动收尾（wf mark replied）；同一轮再次出现转为阻塞。
- 同步：退出码 0 但没有提交号记 unconfirmed，不更新已推送摘要；unchanged 带提交号；暂存目录原地更新，不整目录删除。
- 交接短消息：只在有提交号时生成，链接钉在提交上、只链接已推送的文件；没有时写明原因，由长消息兜底；wf bootstrap 打印所用路线。
- 回复完整性：消费适配器给出的 stop_reason 与 completion_state；因上限结束且固定块齐全时照常推进，并在下一条消息末尾请 Claude 先从断点补齐（同一对话内，连续 3 次后不再自动附）。
- wf self-check --probes：在临时 local/ 里跑本版六组行为探针。
- 文档：WORKFLOW.md 第 10 节改为三条交接路线与两道门（去掉过时的“不引入远程仓库”）；SYNC.md 第 6、7 节；DRIVER.md 第 34 节；TRANSPORT.md 附录 1.8.2；PROCESS.md 第 9 节。
- 自检新增 9 项；改了一条旧断言（1.6.3 短消息：链接由分支 main 改为提交号，模拟的推送结果带提交号与清单），其余原有检查没有删改。本版不改 floor.py。

## 1.7.6 — 2026-10-12

不再空转的续接、执行端的补充说明、按项目分仓库（collab-wf-dev 第 100 轮；amy-local 第 7–11 轮实测）。
- 续接标记用过即清（进入新一轮、生成续接消息后都清）；续接消息带块规格与上一轮没识别到的块；连续 3 轮续接仍缺固定块就停下。
- 块名别名（【结论】等）按规定块名识别并提示。
- wf tell --text：执行端的补充说明附在下一条发出的消息末尾。
- transport.json 的 sync_repos：每个项目推到自己的仓库，同步程序多收 --repo；没有条目的项目不同步。
- DRIVER.md 第 33 节、SYNC.md。自检新增 2 项；原有检查没有删改。本版不改 floor.py。

## 1.7.5 — 2026-10-12

执行端解除它自己发起的暂停（P1-A；collab-wf-dev 第 98 轮，用户授权）。
- wf pause --by executor --note …：记下发起方；执行端发起的暂停在 inbox 里交给执行端，wf unpause --by executor --reason … 解除，事件记 by=executor。
- 用户发起的暂停（含没有记录发起方的旧暂停）仍只认用户原话；驱动器仍没有解除暂停的路径，floor.py 不变。
- DRIVER.md 第 32 节；第 26 节的表与第 31 节（review-late 信号回报后清除）相应更新。
- 自检新增 1 项；原有检查没有删改。

## 1.7.4 — 2026-10-12

执行端事后补审 1.7.1–1.7.3 的质量意见（collab-wf-dev 第 95 轮）。
- review_key_args 增加显式 kind 参数，工具版本改用 kind="tool"；键的格式不变，turn 为 "tool" 的旧写法仍兼容。
- schedule plist 的项目名按 XML 转义；schedule status 只读一次 schedule.json。
- DRIVER.md 第 31 节：审查程序的在场判定分档与迟到结论的去处。
- 自检新增 1 项；原有检查没有删改。本版不改 floor.py。

## 1.7.3 — 2026-10-12

多项目隔离 S2 的工具侧：按项目寻址（collab-wf-dev 第 93 轮，执行端接口评估 T92-2）。
- 信号按项目分目录（local/signals/<项目>/），旧位置带项目的读取时搬入；wf --project <名> signal list | clear 只看本项目。inbox 里信号的 key 不变。
- 分项目只读视图 local/views/<项目>/inbox.json；全局 inbox 不变。
- transport.json 声明 review_key 时给审查程序传 --key（项目版本 <项目>.t<轮次>.<包名>，工具版本 collab-wf.tool.<包名>）。
- 项目版本的审查等待输出开始行与周期心跳。
- DRIVER.md 第 30 节、TRANSPORT.md review 一节、USER.md“同时跑几个项目时”。自检新增 2 项；原有检查没有删改。本版不改 floor.py。

## 1.7.2 — 2026-10-12

多项目隔离 S3：每个项目自己的定时任务（collab-wf-dev 第 92 轮，ISOLATION.md 第 6 节）。
- wf --project <名> schedule plist：生成这个项目的 launchd 配置（标签 local.collab-wf.tick.<名>、日志 local/tick-<名>.log）并登记；wf --project <名> schedule release 撤回。
- wf --project <名> tick 只推进这一个项目；全局 wf tick 跳过已登记的项目。
- inbox 仍汇总全部项目，读改写加文件锁（local/inbox.json.lock）。
- schedule.json 改为合并写，保留各项目的登记；schedule status 按项目自己的周期判断。
- DRIVER.md 第 29 节。自检新增 1 项；原有检查没有删改。本版不改 floor.py。

## 1.7.1 — 2026-10-12

目标达成后由证据生成回报（collab-wf-dev 第 89–91 轮）。
- wf kick --kind report 可以不带 --message：由本轮证据生成标准回报（终端原文、需要注意、执行端备注与待办结果），驱动器接着发送；任务类 kick 仍必须带 --message。
- wf report 在 DONE 下可用。
- DONE 与待办做完时的提示改为推荐上面的命令。
- DRIVER.md 第 28 节。自检新增 1 项；原有检查没有删改。本版不改 floor.py。

## 1.7.0 — 2026-10-12

多项目隔离第一阶段 S1：会话按项目绑定（collab-wf-dev 第 88–90 轮，ISOLATION.md 第 3 节）。
- transport.json 的 per_project_conv 打开后：send、poll（含结果不明时的确认轮询）、snapshot 带 --conv（本项目登记的对话）；conv_new 带 --project。没打开时行为不变。
- wf conv bind --conv <id> --note …：只登记对话，不换对话、不清计数；wf conv new 可带 --conv；wf conv 显示对话 id。
- 适配器输出的 warning 进“需要注意”（ADAPTER_WARNING），同一轮同一动作同一告警只记一次。
- wf transport test：声明 per_project_conv 时检查 poll 接受 --conv。
- TRANSPORT.md 附录：选对话的顺序 --conv > projects[--project] > --prev-ref 所属对话 > 默认对话（执行端第 90 轮意见），告警字段。DRIVER.md 第 27 节。
- 1.6.9 审查意见：同步阻断记录带结构化原因 reasons（secret / bad_zip），提示标题以它为准，旧记录仍按文案判断；unreviewed 文件名的文档写法更正。
- 自检替身记录每次调用的参数；自检新增 4 项；原有检查没有删改。本版不改 floor.py。

## 1.6.9 — 2026-10-12

加固批次（collab-wf-dev 第 86–88 轮，执行端需求甲）。
- P0-A：wf self-update 等独立审查时输出开始行与周期心跳（transport.json 的 review_heartbeat_s，默认 30 秒）；没有结论时输出原因并写 local/reviews/<版本包名去掉 .zip>.unreviewed.json，照常安装；运行新版本自检前先输出一行。
- N8（P1-C）：发送结果不明（意图未结束）期间，SEND 的停留上限按 65 分钟计（machine.SEND_UNKNOWN_DWELL_S），不再误报“停留超过上限”。
- N9：已暂停或 wf.json auto=false 的项目不报“停留超过上限”、看门狗不报停滞、心跳不判“停了”；wf schedule status 把 auto=false 标为“已静音”。
- N6、N7（P1-B）：同步暂停提示按实际命中写标题；key 由命中内容算出，不再随时间变化。
- N10（P1-D）：wf conv new 清掉 send_ref 与 resend_parent，与驱动器自动开新对话一致。
- 文档：DRIVER.md 第 26 节（含“人不在场时各道门的处理”）。
- 自检新增 5 项；原有检查没有删改。本版不改 floor.py。P0-C、P0-D、P1-A 不在本版（见 DRIVER.md 第 26 节）。

## 1.6.8 — 2026-10-12

改进批次一的第二个工具版本（collab-wf-dev 第 82–84 轮）。
- 目标判据通过、但本轮有验收未通过或命令失败时不进入 DONE，照常回报；下一轮干净时照常进入 DONE（driver.py h_verify）。执行端待办照样先登记，结果不明不重发不变。
- P2-E：落地核对可按墙钟计时：policy 的 landed_timeout_s 与 landed_min_polls（时间参数，不算扩大权限）；没设时沿用 landed_confirm_polls。SEND_NOT_LANDED 仍不自动重发。
- wf bridge-check：R3 对在途发送给有限宽限（--grace-s，默认 900 秒），超过照常报；结果出现前又有新的发送一律报；作为最后一条的旧发送不再被漏报。判定时刻可固定（自检用）。
- P1-D：wf drill review --kind block|timeout（wflib/drill.py、docs/DRILL.md）：WF_LOCAL_DIR 隔离的一次性演练项目，替身只替代 Claude 通道，审查用真实程序；结束后暂停项目、保留目录。
- 构建器：自检在副本上运行，打包时过滤 local/、.git、__pycache__、*.pyc，并核对版本包成员集合等于文件清单加 MANIFEST.json（W46）。
- 文档：DRIVER.md 第 25 节、DRILL.md。
- 自检新增 15 项；原有检查没有删改。本版不改 floor.py。

## 1.6.7 — 2026-10-11

目标已达成时执行端待办不再丢失（collab-wf-dev 第 80 轮发现、第 81 轮真实复现：DONE 后 wf task 与 inbox 都没有 T81-1）。
- 驱动器验收时先登记“执行端 N”，再判定目标（wflib/driver.py 的 h_verify）。登记不参与目标判定与熔断；熔断停下时待办同样可见。
- DONE 的语义不变：目标达成、自动化停下、等用户；不等待、不自动回报、不推进版本。目标达成且登记了待办时记一条决策。
- wf explain：DONE 下仍有未完成的执行端待办时先说待办（交给执行端）。wf task done：DONE 下写明不会自动回报。
- inbox 的“目标已达成”提示：目标达成那一轮的待办做完后附上结果；key 不变（done:<轮次>），仍交给用户、没有 action 与 code。
- 文档：DRIVER.md 第 24 节。
- 自检新增 4 项（目标达成时登记、DONE 不变、做完有提示、无待办时不变）；原有检查没有删改。本版不改 floor.py。

## 1.6.6 — 2026-10-10

执行端待办可以追踪（collab-wf-dev 第 58 轮：正文里写给执行端的事做完了却没人接上回报，流程停在空闲，看门狗也看不见）。
- 【驱动指令】新增“执行端 N：<事项>”与“之后：等执行端”。驱动器验收后把待办登记为 T<轮次>-<序号>，进 inbox（交给执行端，超过 2 小时转给用户）。
- 新增 wflib/tasks.py 与 wf task [list] | wf task done <编号> --note "结果"：完成结果写进回报，事件记 by=executor。
- 有未完成的待办时，“之后：等执行端 / 空闲”改为等待，全部做完自动生成回报并发出，不再需要 wf kick；到最迟时间（policy 的 executor_task_timeout_s，默认 4 小时）照常回报并列出没做完的。
- policy：executor_task_timeout_s、landed_confirm_polls、landed_poll_s 归为时间参数，改动不算扩大权限。
- 文档：DRIVER.md 第 5、24 节，WORKFLOW.md 第 5 节。
- 自检新增 3 项（解析与登记、等待后自动回报、到期照常回报）；原有检查没有删改。本版不改 floor.py。

## 1.6.5 — 2026-10-10

卡死与错误都进 inbox（collab-wf-dev 用户第 53 轮：“任何一个环节卡死，我希望都有信息到 inbox”），以及第 52 轮暴露的发送缺口（W42、W43）。
- 新增 wflib/signals.py 与 wf signal add|clear|list：外部信号存在 local/signals/，由写 inbox 的同一处合并，独立进程投递的不会被 wf tick 冲掉。
- 新增 wflib/watchdog.py 与 wf watchdog [--write]：驱动器停摆、驱动器进程卡住、命令超时未结束、该轮询没轮询、发送结果不明、同一步停留过久，纯只读判定；--write 写成信号、恢复后清除，并立即重写 inbox。由独立于驱动器的定时进程调用。
- inbox：交给执行端的事超过 2 小时还在，转给用户（额度等待除外）。
- 发送间隔：policy.json 的 min_send_gap_s（默认 0；时间参数，改动不算扩大权限）。上一条回复落地后不满这个秒数不发下一条（W42：23 秒后就发送，接到了残留的生成）。
- wf resend 覆盖 SEND_NOT_LANDED：恢复本轮消息为待发并重新发送（此前只能阻塞，unblock --to WAIT_REPLY 会一直空等）。
- 执行端代决：approve、unblock、resend、policy approve、goals approve 可用 --by executor --reason，事件记 by=executor；只限交给执行端的事项，四道底线、撤销暂停、目标达成仍只认用户原话（W43）。
- wf explain 的“需要谁”与 inbox 路由一致。原有检查“explain 回答卡在哪……”的期望随之由“需要：用户”改为“需要：执行端”（该场景是白名单外命令，路由本来就交给执行端）。
- 文档：DRIVER.md 第 23 节；anomalies.json 的 SEND_NOT_LANDED 说明。
- 自检新增 5 项（外部信号与升级、看门狗、执行端代决、发送间隔、没落地的重发）；除上面那一处期望外，原有检查没有删改。本版不改 floor.py。

## 1.6.4 — 2026-10-10

对话达到上限时由驱动器自己开新对话（collab-wf-dev 第 44 轮：执行端按用户的决策顺位决定做；这是此前唯一固定需要人的停顿）。
- policy.json 新增 auto_new_conversation（默认关闭；开启属于授权，按扩大权限处理，需要批准）。开启后，对话达到上限时驱动器生成恢复包，选短消息（同步已推送或内容没变时）或长消息，把本轮原本要发的消息原样附在后面，调用适配器的 conv_new 新建对话并切换，计数归零，续接消息作为本轮消息发送。事件与决策都记 by=driver。
- 任何一步不成立都不切换，退回原来的做法（停下等人），详情写明原因；conv_new 超时属于结果不明，不再自动重试，避免重复建对话。
- 驱动器生成恢复包时，bootstrap 事件改记 by=driver（此前记 executor，M1 判据会把它算成人工动作）。
- 修正（collab-wf-dev 第 50 轮执行端报告）：wf transport test 对空 ref 的 poll 只认 none，与 TRANSPORT.md 1.5.1 的约定（返回 no_ref）矛盾；改为 none 或 no_ref 都算合规。
- 文档：DRIVER.md 第 22 节、TRANSPORT.md 附录（conv_new 接口）；CONTEXT.md、WORKFLOW.md、DESIGN.md 与 anomalies.json 的 CONTEXT_FULL 说明补充。
- 自检新增 4 项（自动开新对话成功与失败两条路径、空 ref 返回 no_ref、授权判定与消息选择）；两处旧断言随检查项的新名称更新，判定不变；原有检查没有删改。本版不改 floor.py。

## 1.6.3 — 2026-10-10

开新对话改用“短消息 + 公开仓库链接”（用户第 31 轮：“以后都用这个仓库做这个项目的上下文”）。
- transport.json 写了 sync_repo 且项目开启同步时，wf bootstrap（以及对话体量达到上限时自动生成的恢复包）先同步一次，再生成 bootstrap-<时间>-short.md：STATUS、PROJECT、VISION、WORKFLOW、DRIVER、最近一轮双方消息与工具源码的 raw 链接，加上当前版本、指纹与最近一轮的验收结论。长消息与 zip 照旧生成，作为后备。
- 对话体量达到上限时的提示里写明短消息的文件名。
- 文档：SYNC.md 新增第 7 节。自检新增 1 项；原有检查没有删改。

## 1.6.2 — 2026-10-10

修正（collab-wf-dev 第 36 轮执行端报告）：通道是模块方式时，anomaly.call 把所有动作都交给通道适配器，而通道适配器只有 send/poll/quota，wf sync 必然失败（“未知动作：sync”），与 docs/SYNC.md 的说明矛盾。
- sync、review 在 transport.json 里写了程序路径时，直接运行该程序；写 true 时仍按模块方式的子命令调用；通道动作不变。
- 文档：SYNC.md、REVIEW.md 写明两种写法。
- 自检新增 1 项；原有检查没有删改。执行端在适配器里加的 sync 转发继续可用，可以保留，也可以改为直接写程序路径。

## 1.6.1 — 2026-10-10

把“需要人”的停顿降到底线本身（collab-wf-dev 第 34–35 轮：流程因等执行端批准与处理 AP-2 空转 9 分钟，用户要求彻底解决）。
- 不扩大权限的白名单与目标变更由驱动器自动批准：评审不再停下，落地后自动批准并标明 by=driver。判定见 policy.widening 与 goals_widening；扩大权限的照旧停下。
- 落地前工作区有未经交付的改动时，存成单独的提交并记入回报，不再阻塞（含疑似密钥时不存，照旧停下）。
- 每日自动轮次达到上限后，等到第二天自动继续，不再停下等人；上限本身不变。
- 原则不变：任何自动路径都不解除因四道底线而停下的等待、不改动底线、不撤销暂停。本版不改 floor.py。
- 文档：DRIVER.md 新增第 21 节。
- 自检新增 5 项；原有检查没有删改。

## 1.6.0 — 2026-10-10

项目上下文同步（collab-wf-dev 第 29–31 轮：用户要求项目自动同步到公开仓库，让 Claude 按链接读到最新的版本、进度与工具源码；执行端的 gh_sync.py 经 Claude 审计后融入工具）。
- 新增 wflib/sync.py 与 docs/SYNC.md：wf tick 之后，对开启了同步的项目按白名单收集内容（STATUS.md、README.md、项目已提交文件、工具源码、已发出的消息、回复、最新版本包），推送前做密钥扫描（含 zip 内文本），内容有变化才调用执行端的同步程序（transport.json 的 sync）。
- 审计 gh_sync.py 时发现并在此修正：它的 README 写“经过发送前密钥扫描”，实际没有扫描；它会上传 outbox 里所有 turn-*.md，包括被发送前检查拦下、从未发出的草稿。工具版只放 turns.jsonl 记为已发出的消息，并真正扫描全部内容。
- 新增 wf sync [--dry-run] [--force]；wf ask 与 inbox 新增“项目同步已暂停：发现疑似密钥”“项目同步连续失败”两种提示（交给执行端）。
- 文档：DRIVER.md 新增第 20 节；新增 docs/SYNC.md。
- 自检新增 4 项；原有检查没有删改。

## 1.5.9 — 2026-10-10

用户要求护栏改动也全自动、通知送到执行端而不是用户（collab-wf-dev 第 21–24 轮）。本版让停下来问用户的只剩四道底线本身。
- 新增 wflib/floor.py：停止底线、版本包内容检查、护栏命令与命令拼接的定义集中到这里；policy.py 改为导入，原有名字保持可用。
- 自动安装的护栏文件从 policy.py、release.py、asks.py、anomalies.json 缩为 floor.py 一个；改动它的版本仍停下等用户。
- 自动安装前新增底线行为检查（floor.probe）：由当前已安装的代码在隔离子进程里检查候选版本——对每条底线用例都停下、仍守住 floor.py、自动安装仍做这两项检查、执行前评审仍经过 classify。补上了 1.5.8 只改 driver.py 就能绕开底线的缺口。
- 执行端待办信号：wf tick 之后写 local/inbox.json，每件待办标明交给用户（底线命中、撤销暂停、目标达成）还是执行端（其余），供执行端的定时自动化读取。不受通知开关影响。
- 文档：DRIVER.md 第 18 节更正护栏文件，新增第 19 节。
- 自检：新增 5 项；改动 1 项原有检查的断言（1.5.8 自动安装工具版本：policy.py、anomalies.json 不再算护栏文件，floor.py 算），原因同上；其余没有删改。
- 本版改动了 1.5.8 的护栏文件，需要最后一次手动安装。

## 1.5.8 — 2026-10-10

全自动、不需要用户提醒（collab-wf-dev 第 19–20 轮：交付物要执行端手动安装，而执行端只在用户发话时被唤醒；状态机一秒跑完一轮，约七成时间在等唤醒）。
- 自动安装工具版本（用户第 19 轮批准）：policy.json 的 auto_self_update 为 true、白名单放行时，驱动器可以执行 wf self-update --auto workflow/outbox/collab-wf-X.Y.Z.zip。安装前由当前已安装的代码检查：改动了护栏文件（policy.py、release.py、asks.py、anomalies.json）的版本拒绝，停下等用户；配置了另一个模型系列审查时先审 diff，block 也拒绝；--auto 不允许跳过自检。
- 效率：wf tick 在“发送”与“等待 Claude”时，下一次轮询若在本次时间预算内，就在 tick 里等到那时再查；发现回复的延迟从约 300 秒降到轮询间隔（120 秒）以内。空闲时不轮询。
- 命令退出码为 0、只是没通过判据时不再报“命令执行失败，退出码 0”，由验收报 CHECK_FAIL。
- 新增 docs/REVIEW.md：另一个模型系列审查的约定（何时调用、参数、审查清单、输出、只能阻止的处置）。
- 文档：DRIVER.md 新增第 18 节；PROCESS.md 第 8 节写明自动安装。
- 第 19 轮交付的同名 1.5.7 构建器（含上述效率与审查文档）因 1.5.7 已安装而未能构建，内容并入本版。
- 自检新增 4 项；原有检查没有删改。

## 1.5.7 — 2026-10-10

看得到进度，不默默执行（collab-wf-dev 第 17 轮：执行端的本地胶水 tick_notify.py 在换成官方定时器配置后失效，收进工具）。
- 新增 wflib/notify.py：wf tick 跑完后，把各项目新出现的待决事项与提示（与 wf ask 同一来源，包括需要决定、阻塞、暂停、目标达成、额度等待、停留超时、驱动器停了）弹成 macOS 通知；每件事只通知一次，事项处理掉后清除记录；发送失败不记为已通知。
- 新增 wf notify test | status；可用 WF_NOTIFY=0 或 local/notify.json 关闭。
- 文档：DRIVER.md 新增第 17 节；PROCESS.md 第 8 节写明通知。
- 自检新增 3 项；原有检查没有删改。

## 1.5.6 — 2026-10-10

驱动器停了要看得见（collab-wf-dev 第 12–14 轮：手写循环跑满次数静默退出，状态停在原地，看起来像卡死）。
- 新增 wflib/heartbeat.py：驱动器心跳，判据与看板“驱动器”卡片相同（超过 15 分钟没有运行、白名单已批准、没有暂停）。
- wf doctor 增加“驱动器心跳”；wf ask 主动呈报“驱动器停了”（每次停下只报一次）。
- 文档：DRIVER.md 新增第 16 节；PROCESS.md 第 8 节写明 doctor、wf ask 的提示，定时器与适配器所用的解释器，以及计划节点只等外部条件（需要执行端手动操作时写“之后：空闲”）。
- 自检新增 2 项；原有检查没有删改。

## 1.5.5 — 2026-10-10

用户第 14 轮的两个问题：按 pstack 方式重塑开发流程；Claude 的上下文怎样解决。
- 新增 docs/PROCESS.md：按 pstack 方式开发软件的标准流程（开工、每一轮、卡住与平台期、角色与档位、独立验证三层、把教训变成结构、无人值守、不适用的做法、对照表），每一条写明做什么、为什么、谁做、怎样算做到了。
- 新增 docs/CONTEXT.md：Claude 的上下文方案与给用户的说明（记在哪里、何时开新对话、恢复包与送达、开新对话的步骤）。
- WORKFLOW.md 与 DESIGN.md 指向这两份文档；恢复包里的 WORKFLOW.md 因此也带上指引。
- SNAPSHOT.md：写明截图的前置条件（调试端口、websocket-client 等模块），缺少时按降级处理并写明安装方法；自检加一条“不是骨架屏”。
- anomalies.json：OUTGOING_STRUCTURE 的说明去掉“（工具缺陷）”，改为先核对标题行与 ①②③ 三段（W22 余项）。
- 更正：1.5.4 的自检实际新增 8 项（当时写成 7 项）。
- 自检新增 2 项；原有检查没有删改。

## 1.5.4 — 2026-10-10

用户澄清（collab-wf-dev 第 13 轮）：Claude 页面快照要“文本 + 截图”两种形态。
- page.py：轮询与 wf look 同时请求截图（环境变量 WF_PAGE_SCREENSHOT，wf look --full 截整页）；截图原样保存为 latest.png 与本轮的 turn-NNN.png，与文本快照成对；没有截图时记下原因并保留上一张；按轮保留最近 20 张，单张超过 12 MB 不收；policy.json 的 page_screenshots 可关闭轮询时的截图。
- 看板：快照板块顶部显示最近一张截图（点击看原图），本次没有截图时写明原因；新增只读接口 /api/page-image。
- docs/SNAPSHOT.md 新增第 5 节（截图约定：不打扰用户、降级、频率）；DRIVER.md 新增第 15 节。
- 自检新增 7 项；原有检查没有删改。

## 1.5.3 — 2026-10-10

用户需求（collab-wf-dev 第 11 轮）：工作流看板加 Claude 页面快照与对话记录。
- 新增 wflib/page.py：收下适配器写出的页面快照（中立格式，见新文档 docs/SNAPSHOT.md），内容变了才重写、每次查看都记录，按轮保留最近 50 份，存放在 workflow/evidence/page/。快照只用于展示，不参与状态判定。
- 驱动器每次轮询时通过环境变量 WF_PAGE_SNAPSHOT 让适配器顺带写快照（anomaly.call 新增 env_extra）；新增 wf look（适配器的 snapshot 动作），随时看一次。
- 对话记录补上用户方向：新增 wf said；用户批准时的原话也写入 transcript（此前项目页的“用户”筛选永远为空）。
- 看板：flow.html 新增“Claude 页面快照”“对话记录”两个板块；新增只读接口 /api/page、/api/transcript。内容按纯文本逐字显示、全部转义，长文、思考与工具调用默认折叠。
- 桥接动作名新增 snapshot。
- 回报标题行接受三段版本号（W22）。
- 文档：新增 docs/SNAPSHOT.md；DRIVER.md 新增第 14 节。
- 自检新增 10 项；原有检查没有删改。

## 1.5.2 — 2026-10-10

collab-wf-dev 第 1–9 轮（工具层独立后的新对话）暴露的问题：
- 状态并发（W18）：状态的读-改-写放进跨进程状态锁，与磁盘三方合并、只写回本进程改过的字段；事件序号在锁内按磁盘最新值加 1，追加也在锁内。驱动器主循环每一步之前合并并检查暂停（第 1 轮手工 wf pause 被驱动器的过期状态覆盖，三次重号）。seq_check 的倒序判定改为严格小于，一个重号不再同时计为倒序。
- 判据与熔断（W21）：有判据的命令按判据判定（预期无匹配写 exit=1）；整轮成败与熔断只计验收未通过与未关联命令的失败，暂缓项不再一票否决；回报档位按同一口径。任何失败仍让本轮之后改为回报。
- 续传（W20）：续传先于本轮原文嵌入，与文档一致；一片都放不下时不输出空标题。
- 手工回报（W19）：wf report 生成的本轮草稿由驱动器接着发送；COMPOSE 的提示语不再推荐必然失败的 wf kick。
- 需要用户决定（W15）：只认显式标记“（前置）”，“不阻塞”等字样不再误停。
- 桥接动作名新增 conv_new（新建对话）。
- 文档：解析“五个固定块与【驱动指令】”；开新对话先 wf conv new 再发送说明消息；DRIVER.md 第 5 节写明判据口径，新增第 13 节。
- 自检新增 11 项；原有检查没有删改。

## 1.5.1 — 2026-10-10

发送结果不明这条因果链（第 40 轮事故）与 1.5.0 安装后发现的问题：
- 适配器超时：先 SIGTERM，宽限期（transport.json 的 grace_s，默认 15 秒）后才 SIGKILL；通过 WF_ADAPTER_TIMEOUT_S 告知适配器时间上限。
- 发送结果不明：新增 SEND_UNKNOWN_WAIT（policy 的 send_unknown_wait 开启时生效），约一小时内反复轮询确认，绝不自动重发；可选 poll --since/--message 定位没有 ref 的消息（transport.json 的 poll_since）。
- 新增 wf resend：确认没有送达时，凭决定码与用户原话清除发送意图并重发，取代手改 state.json。
- SEND_BUSY、SEND_UNKNOWN 与 TRANSPORT.md 的说明写入第 40 轮教训：通道报忙最可能是自己的消息在生成；会话无变化不能证明没有在生成。
- 证据关联：驱动器执行的命令只按 label 对应，跳过的命令不再导致错配（第 44 轮）。
- wf self-check 归为只读命令；wf note 在回报生成之后写入时提示去向；说明类备注进入回报。
- wf resume 在等待命令批准时提示改用 wf approve（由驱动器推进时 resume 会原地打转）。
- TRANSPORT.md：空 ref 时 poll 返回 no_ref，不返回 none。
- 自检新增 11 项（超时宽限、确认重发、只读命令、备注提示等）。

## 1.5.0 — 2026-10-10

可靠性与可验证性（btc-quant 第 29–40 轮实测暴露的问题）：
- 回报保真：暂缓项与未关联命令也附原文；原文按约 32000 字符的预算完整嵌入，不再只取末尾 60 行；每段只出现一次；放不下的分片续传（workflow/outbox/carry.json）；原文含反引号时外层围栏自动加长。
- 解析容错：块标题须位于行首，正文中提到块名不再顶掉命令块；驱动指令容忍行首编号、“本轮之后”与加粗；指令块有内容却解析不出键、清单编号不连续时，写进“需要注意”。
- 回复归属：poll 可返回 reply_parent，与本轮 send_ref 不符时不收下、继续轮询（TRANSPORT.md 末节）。
- 执行接续：被打断的那条命令已有退出码时，不再误报 EXEC_INTERRUPTED。
- 事件与体检：先写状态再追加事件，杜绝重号；wf doctor 检查事件序号、额度空值连续数、回复归属与续传队列；额度有效期统一为 10 分钟。
- 升级核验：wf self-update 安装后核对版本、文件与看板，不通过就自动回滚并改名失败标签（从 1.5.0 升级到之后的版本时生效）。
- 桥接自检：poll 为合法动作；被拒后重发不算重叠发送。
- 叶子被拒：重发次数可配置（policy.max_leaf_refusals，默认 2 保持原行为，建议 0）。
- 无附件续接：wf bootstrap 附工具与项目的文件清单。
- 新增 wflib/fidelity.py（纯函数）与 wflib/selftest_v15.py（新增检查，计入 wf selftest 总数）；原有检查没有删改。
- 交付方式：补丁构建器（锚点逐处核对，任何一处不符就整体中止）在工具仓库的克隆上生成版本包并先跑自检。

## 1.4.3 — 2026-10-10
- 修复 R1 没跟上 aborted：1.4.2 把 aborted 加进白名单并让 R3 认它,却漏了 R1 的终态集合,导致一次中断后此后每次发送都被 R1 判为「上一条未结束」且不自愈。现在 R1 的终态集合含 aborted（③-1）
- wf bridge-check --list-actions：输出权威的动作名清单,供适配器 selfcheck 比对,避免适配器用到清单外的名字（如 poll）而 R6 报「无法解析」（③-2）
- 裁定记入 AUDIT.md：动作名白名单保持小集,适配器只写白名单内的名字,poll/refused 不进白名单（refused 的处理在驱动器侧的 poll 返回状态,不在动作日志）；已写入的日志行不修改,格式不符的历史行随 24 小时窗口自然滑出
- 自检 95 项

## 1.4.2 — 2026-10-10
- self-update / self-rollback 落地后按实际安装的树重算 MANIFEST.json，清单一定等于新版本；打包时忘了重算也不会让 wf self-check 失准（③-1）
- 动作日志新增 aborted（自己的发送被中断、结果未知，区别于 abort「人为中止他人生成」），bridge-check 的 R3 接受它为发送终态（③-2）
- 裁定记入 AUDIT.md：bridge-check 不加历史白名单（会淹没真违规，历史条目 24 小时自动滑出窗口，按行号时间戳区分新旧即可）；手动轮次发出后由执行端登记 --ref 并让 tick 持续轮询，不另加会与驱动器重复的自动轮询
- 自检 94 项

## 1.4.1 — 2026-10-10
- wf panel：聊天窗口内联面板（自包含 HTML 小组件），只显示现在在哪、要不要用户拍板（含决定码与照抄的话）、下一次自动检查时间；与每轮回报分工，不重复叙述过程
- F25 修正：self-update/self-rollback 在内部用子进程调用新 wf.py 重启看板，新代码立即生效（原实现由调用方旧进程重启，晚一版）；server.info 的 stale 判据改为“在跑且版本≠工具版本或读不到版本”，不再在最该报警时静默通过
- 修复 wf panel 一处局部 import 遮蔽模块级 asks、导致 wf unblock 崩溃的问题
- 裁定记入 AUDIT.md：删除工具外的旧 transport.py、stop_response 只作人工一次性脚本不进接口、被中止发送补写终态、备份不放 /tmp、自查命令排除检查器自身
- 自检 92 项

## 1.4.0 — 2026-10-10
- 唯一的权威版本：取代第 20 轮同一轮里两次生成各自交付的两套 1.3.0（下方两条 1.3.0 记录），以及第 21 轮未交付的 1.3.1（AUDIT.md 第 1 节）
- 在 WorkBuddy 聊天窗口里操作：docs/CHAT.md（执行端约定）、intents.json（用户的话 → 命令）、USER.md 重写为“只需要会说六句话”
- 决定码：每件待决事项有由内容算出的码；批准、继续、确认、解除阻塞必须带 --code 与 --said（用户原话），码不对或事项已变即拒绝；原话逐字记入事件流与决策日志
- wf ask：按固定模板呈报待决事项与通知，带去重（同一事项只呈报一次，未处理时 12 小时后提醒）
- 看板：顶部“需要你决定”（决定码与要回复的话）、下一次自动检查时间、工具调用进度；项目列表显示已暂停
- 看板进程版本（缺口 12）：记录常驻进程加载的版本；self-update / self-rollback 后自动重启看板；版本不一致时看板红色告警、wf doctor 报警
- 适配器：恢复通道无关的 adapters/base.py 与示例 example_echo.py，支持模块方式（module + module_path，适配器放在工具目录之外）；修复 anomaly.py 缺 import sys 与 base.main() 的相对导入；通道适配器放在工具目录内时报警
- 发送独立落地核对的门槛放宽为连续 5 次、间隔 60 秒（生成初期可能暂时没有可靠的“正在生成”信号）
- 同一轮多次交付：只采用最后一条完整回复的文件，记 MULTI_DELIVERY（TRANSPORT.md other_deliveries）
- MANIFEST.json 与 wf self-check：列出工具目录中多出、缺失、被改动的文件（只列出，不删除）
- 自检 91 项

## 1.3.0 — 2026-10-10（第二次生成交付的一套，声明指纹 1bbb0a2504d2615e）
- 发送只有心跳不算发出：适配器返回 0 还须给出“生成已开始”的正面证据，否则按通道忙处理（缺口 11-3、F22）
- 叶子被安全防护拒绝时自动恢复：poll 返回 refused + parent_ref，驱动器回退到上一条健康消息并重发本轮消息，不新增轮次；连续被拒超过两次停下问用户（§10）
- 手动发送可登记 ref（wf mark sent --ref），驱动器据此接管轮询；无 ref 又未手动收尾时明确停下而非误判（缺口 7）
- 看板项目列表显示“已暂停 / 需处理”（缺口 5）
- 新增 docs/USER.md（面向用户本人的操作说明，缺口 4）与 docs/AUDIT.md（执行端自建代码逐份审计与收编边界）
- TRANSPORT.md：send 必须有正面证据；poll 增加 refused 状态与 parent_ref；send 增加 --parent；适配器只允许调用通道客户端的公开函数，不得复制其逻辑（缺口 11）
- 自检扩充到 79 项（含心跳不算发出、叶子被拒回退、手动 ref 接管、项目列表暂停）

## 1.3.0 — 2026-10-10（第一次生成交付、已在本机落地的一套，指纹 f296cf5162cad6da）
- 执行端代码审计与收编：docs/AUDIT.md 给出 12 个自建文件的逐份结论（改造收编 1、保留 2、删除 7、合并 3→1）
- 适配器收编：新增 adapters/ 子包（base.py 基类 + example_echo.py 示例）；local/transport.json 支持 module 方式指向适配器，旧的三可执行程序方式仍兼容；wf adapters 列出内置适配器；selftest 改用内置 example_echo（删除 wflib/fake_transport.py）
- 缺口 4：新增 docs/USER.md（写给用户本人的操作说明）
- 缺口 5：看板 /api/projects 增加 paused，项目下拉显示“（已暂停）”，顶部增加暂停提示条
- 缺口 7：wf mark sent --ref 让手动发出的轮次也能被驱动器接续；无 ref 时驱动器明确停下（NO_REF）而非误判“看不到”
- 缺口 8：开新对话链路写入 USER.md 第五节；bootstrap 的两个压缩包已在 selftest 验证
- adapters/base.py 不提供任何中止生成的路径，BUSY 只返回退出码（F22 在工具侧落实）
- 自检扩充到 76 项

## 1.2.2 — 2026-10-10
- wf bootstrap 同时打包 collab-wf 工具本身（已提交的版本，不含 local/），新对话中的 Claude 可以继续开发工具；说明消息写明下一个计划节点，并要求新对话第一轮只做核对、不交付版本
- TRANSPORT.md：清理“孤儿锁”不属于适配器接口；只有拿到正面的接受证据才返回 0（F22）
- 自检 72 项

## 1.2.1 — 2026-10-10
- 会改变工作流状态的 wf 命令（apply、pin、wait、verify、goals check、self-update 等），驱动器一律不执行，即使在白名单中或经用户批准也只列为手动步骤并跳过；只读的 wf 命令照常（F21：避免与驱动器自身的状态写入互相覆盖）
- 驱动器自动发出的回报中，未验收的清单项改为说明“本轮没有执行到验收，原因见 ①”
- 自检 72 项

## 1.2.0 — 2026-10-10
- 总设计 DESIGN.md：全部需求的落实情况、pstack 研究与对照、所有权与审计方式、剩余风险
- 门禁与自动回退：用户批准的 gates 在落地前后各跑一次，回归则自动回退并改名版本标签（借鉴 pstack“不凭作者自己的结论合并”）
- 可选的独立审查适配器：另一个模型系列审查版本包改动，只能阻止或提醒，不能放行
- 档位与角色：roles.json 按角色分配档位、预算统一调节、高用量封顶；每轮记录角色、档位与理由；wf effort 复盘
- 决策日志 decisions.jsonl（可导出 TSV）与回报中的“④ 需要注意”（借鉴 show-me-your-work）
- 全局限速（transport.json 的 rate）；对话体量上限，达到时停下并自动准备新对话恢复包；wf conv
- wf transport test：传输适配器的黑盒一致性检查（不发送消息）
- wf schedule plist / status：系统定时器配置与运行状态；tick 日志自动轮转
- 看板新增“Agent 进度”“需要注意”“决策日志”；驱动器超过 15 分钟未运行时提示定时器可能停了
- 修复：从停下的步骤恢复时不再重复进入该步骤（避免组织消息时轮次误增）
- workflow/ 下的全部运行记录（*.jsonl）统一受保护、不进版本库
- 自检扩充到 71 项

## 1.1.0 — 2026-10-10
- 驱动器：wf run（单项目）/ wf tick（多项目），由系统定时唤醒；幂等、可重入、可中断；退出码 0/10/20/30/1
- 发送意图与执行进度先落盘：被打断后不重发、不重跑，结果不明时安全停机
- 自主边界写进代码：用户批准的命令白名单（默认拒绝）、内置停止底线（资金、密钥、合规、实盘）、护栏命令只能由用户执行、命令拼接需批准、版本包新增内容检查、熔断与每日上限
- 目标与完成判据：workflow/goals.json（可执行 / 人工），用户批准后生效、内容改动即失效；进度与“目标达成”状态
- 异常字典 anomalies.json：症状 → 处置 → 上限 → 超限动作；项目可追加；未知情况安全停机
- 【驱动指令】：回复中的第六个固定块（发布、之后、逐项验收判据、指纹、超时）
- 传输适配器接口（TRANSPORT.md）：wf 不访问通道
- 新命令：run、tick、explain、kick、approve、pause、unpause、undo、audit、goals、policy、transport
- 看板新增“驱动器”与“目标”卡片；新状态“目标达成”
- 文档：DRIVER.md、TRANSPORT.md；自检扩充到 62 项（含驱动器全流程与中断场景）

## 1.0.3 — 2026-10-10
- wf exec 把工具的 bin 目录前置到子 shell 的 PATH，指令里的 wf 命令在 wf exec 中原样可用，不依赖 shell 配置文件（F18）
- 消息发出后、回复到达前设置计划节点时，给出正确顺序的说明（来源状态不放宽）；文档写明“需要回报”与“验收后直接等待”二选一
- wf bootstrap：章节号改为第 10 节；说明消息附上最近一轮的回报清单与验收结论、仓库指纹（F19）
- 自检新增 3 项（43 项）

## 1.0.2 — 2026-10-10
- 修复验收死锁：进入等待计划节点或空闲后，可以对本轮清单事后补记验收（记为事后补记，不改变状态）；回报草稿可在这两个状态下生成；新增 VF-5（F17）
- wf fingerprint：仓库指纹，只覆盖 Claude 交付的文件；回报草稿自动附带，用于确认两边副本同步
- wf snapshot：按 Claude 点名生成有界的文件包（默认 40000 字符，拒绝 .env 与运行数据、含疑似凭据时拒绝）
- wf bridge-check：核对桥接工具本地日志是否满足行为约定（一条在途、发送有结果、不提前中止生成、只记实测值、无凭据、日志完整），只读本地日志、不访问通道
- 自检新增 4 项（40 项）

## 1.0.1 — 2026-10-10
- wf apply 中途失败（例如删除阶段被系统拦截）时自动恢复本次触及的文件，工作区回到落地前，排除原因后可直接重试（F15）
- 发送步骤的退出条件改为“通道已接受消息”，不再以“消息在会话中可见”为准；新增 WR-0：看不到消息不等于丢失（F14）
- SD-1：任何情况下不得中止进行中的生成；孤儿锁需连续 10 分钟被拒且无生成迹象
- USAGE.md 第 4 节：桥接工具的行为约定
- 输出接到管道并被提前关闭时（如 wf version | head -1）不再打印报错
- 自检新增 2 项（36 项）

## 1.0.0 — 2026-10-10
- 首个版本：从 btc-quant 项目中抽出协作工作流，做成独立工具
- 13 个状态的工作流定义（workflow.json）：8 步主循环 + 5 个等待或阻塞状态；每步含负责方、做法、进入与退出条件、证据、停留上限、失败上限、已知异常的判定与处置
- 状态机：非法转换拒绝并提示去向；失败达上限自动阻塞；需用户批准的命令自动进入等待用户决定；人工更正单独标记
- 自动记账：wf mark 记录发送与收到（实测耗时与额度）；wf exec 自动保存命令、输出、退出码、耗时
- 回复解析（五个固定块）、命令风险分级、逐项验收、回报草稿自动生成、发送前密钥扫描
- 版本：wf apply（先校验后复制、按清单删除、提交并打标签）、wf rollback；工具 self-update（先自检后安装）与 self-rollback
- 工作流看板（运行图、当前步骤、步骤说明、轮次时间线、本轮执行、事件流、统计）与项目视图，一个服务覆盖所有项目
- wf doctor 健康检查、wf bootstrap 新对话上下文恢复包、wf selftest 端到端自检
- bin/wf 启动器：加入 PATH 后可在任意位置（包括 wf exec 内部）调用 wf
- 全部使用 Python 标准库编写，不再依赖 bash 脚本（避免 macOS bash 3.2 与 Linux bash 5 的差异）
