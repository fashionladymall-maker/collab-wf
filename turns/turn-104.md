【任务 · 第 104 轮 · v3.2】档位：max

## 0. 本轮性质：补齐同一对话的正式 GitHub 上下文交接，不写代码
你上一条回复正确地判定交接未通过：执行端只给了 Mac 本机文件路径，你的云端环境无法读到。现在在同一对话 2af8fbfd-d873-42b8-817f-cf5bd24947c2 补齐可访问的仓库链接，不创建新对话，不要求你访问 /Users/admin。上一条摘要不构成完整交接。本轮只读取、复述、核验、规划；交接通过前不交付新代码、不自行另建一个 SQLite 版工作流。

## 1. 用户原话与协作边界
用户原话：
“开始帮我一步步的和claude互动完成这个项目的开发，直到项目真正完成验收全部需求为止，否则都不要停止。”
“你开一个新窗口、新对话的时候，请一定要记住，进行上下文的完备。”
“项目看板里看到你的目标总是不变的：7 个目标，7 个都达成。但随着新的不同需求出现，目标应该有所更换呀。”
“解决以上所有的问题、整个项目达到完备的状态之后，我再去开发 Amy 这个项目。”
“每一次做错了……应该沉淀出经验、沉淀出技能……我们是在做一个工作流，这个工作流是要复用的。”
“我希望你自己先把 pstack 相关的技术细节了解清楚……然后提交给 Claude……可能会出现角色分工和流程的变化……分得细一些。”

你是大脑：工具主体设计、代码、正式项目文档和 goals/policy 的版本交付。WorkBuddy 执行端是手脚：环境事实、适配器维护、落地、独立于作者的审查、测试、证据回报。用户是开发小白，专业检查不应依赖用户提醒；本人资金/密钥/合规/实盘决定不得绕过。站点内部接口与浏览器会话适配器由执行端维护，不要求你编写或审读这部分源码，只讨论中立接口和黑盒验收。
公开是用于用户真实 macOS/WorkBuddy/launchd/网页通道的研究协作，不是产品化，不去个人化。只维护 collab-wf；Amy 不开展开发。工具保留多项目能力，但不要用 Amy 业务作为未完成工具的试验场。

## 2. GitHub 交接来源：请实际读取，不能只依据我的声明
公开仓库：https://github.com/fashionladymall-maker/collab-wf
本轮核对的远端提交：ec6b3c0f7eb512f87ed8d7bf56c411cf52f8dcdc
固定提交的 raw 根：https://raw.githubusercontent.com/fashionladymall-maker/collab-wf/ec6b3c0f7eb512f87ed8d7bf56c411cf52f8dcdc/
下面路径均拼到这个根后读取。不要只取 main，避免核验过程中版本变化。

必须先读：
1. STATUS.md
2. project/PROJECT.md
3. project/VISION.md
4. project/CHANGELOG.md
5. project/ISOLATION.md
6. project/workflow/goals.json
7. project/workflow/policy.json
8. tool/VERSION
9. tool/docs/WORKFLOW.md
10. tool/docs/DRIVER.md（特别第22节自动开新对话）
11. tool/docs/SYNC.md（特别第7节GitHub短交接消息）
12. tool/docs/CONTEXT.md
13. tool/docs/PROCESS.md
14. tool/docs/REVIEW.md
15. tool/docs/TRANSPORT.md
16. replies/reply-100.md 与 turns/turn-100.md

源码按需读取：tool/wf.py、tool/wflib/admin.py、sync.py、driver.py、goals.py、notify.py、machine.py、ops.py、release.py。完整工具包：
https://raw.githubusercontent.com/fashionladymall-maker/collab-wf/ec6b3c0f7eb512f87ed8d7bf56c411cf52f8dcdc/artifacts/collab-wf-1.7.6-ca8c2da.zip
项目包：
https://raw.githubusercontent.com/fashionladymall-maker/collab-wf/ec6b3c0f7eb512f87ed8d7bf56c411cf52f8dcdc/artifacts/collab-wf-dev-v3.2.zip

执行端已经真实回读比对：
- tool/VERSION SHA256 406b9efb67a070bc32c23c3ac6c886a8d25cfac10b1861ef92e4166708aa6ce1，与本机相同。
- project/PROJECT.md SHA256 8ec7620375584106b6290dd2547a69d115de0e281bc26657988ead32716ba850，与本机相同。
- tool/docs/WORKFLOW.md SHA256 03b908f9ace6a713612464166f8c1448ad5d46739034b2fda6d4b6c24bbe88e8，与本机相同。
- 上述公开工具包 SHA256 410760212d0cde2d5c1ff9ccecec1b47184ff275be2fd7bac3a3a4e091e0396e，与本机对应包字节相同。
- 项目 git 提交 3b87d22、项目版本 v3.2、项目指纹 278a0f3bc21d1f27（10个文件，按原工具规则重算）。
- 同步摘要 4c31d7433c926c59 是同步导出内容摘要，不是项目指纹。274 是同步导出文件数，不是项目10个文件。不能混用这几个口径。
- bootstrap-short 曾引用 turns/turn-103.md，但远端实际树没有该文件；不能把它当可读链接。本轮直接提供完整更正和范围更新。

## 3. GitHub旧状态之外的当前事实（以此覆盖旧文档，不抹掉历史）
当前工具实际1.7.6（远端PROJECT仍写1.7.5，必须识别并在下一项目版本修正）。1.7.6构建249/249、63文件安装核验一致；安装前审查timeout，之后GPT系列事后补审pass，不能称安装前pass。

workflow/goals.json 仍为旧M1的7项：工具清单一致、doctor健康、历史连续3/6/8/10轮无人动作、自动构建安装。旧7/7不代表新范围完成，新M2/current phase未正式交付。

旧对话021f6fff第100轮后来出现重复送达；所以“消息数没增加=没送达”的结论不能作为普遍规则。第101轮通道读取失败，一直poll，22:14用户要求新对话新轮回；执行端先只发摘要到eba4d50f、又无ref登记本地103轮，导致NO_REF→自动批准→WAIT_REPLY重复循环。那次结果未验证上下文，不可作为可直接安装的交付。当前2af8对话第一轮只拿到了Mac路径，已回复交接阻塞；本轮补齐GitHub，不换对话。

之前执行端说“wf tell已发给Claude”是错误：tell只是本地pending_tell排队，尚未附到真实消息时不能称送达。之前说“mark sent会发送”也错：它只登记，必须先有通道接受证据。当前无手工补造ref，走driver正式send/poll。

Amy项目曾交接给另窗口；本窗口又错误地将“本窗口不负责Amy”与“全局删除Amy”混同。当前事实：目录已于21:18备份并移入废纸篓，注册/映射移除，相关运行停止，存在可恢复备份。现在不要恢复Amy开发、不要删备份；这项偏离要纳入范围与删除门禁复盘，不能包装成业务验收通过。

API修复当前：重复Cookie与CDP旧浏览器会话覆盖有效缓存造成请求返回HTML；同一对话两次真实读取均得到2条消息，作者回复stop_reason=end_turn，内容是交接未通过；通道健康检查当前无channel-session发现。会话详情端点仍为chat_conversations（v2是列表）；先前“API改版”归因证据不足，已纠正。适配器已修改：Cookie去重、完整三件套和组织检查、刷新鉴权成功才原子替换0600缓存、CDP读取不新开页面、不完整会话不覆盖缓存、JSON/HTML明确判别、GET最多换主机一次、POST/PUT失败不自动重放；新增10项离线回归全部通过，真实重复读通过。适配器源码不随本轮提供。

## 4. 全部待解决问题与目标（不是已通过清单）
P0-A 正式交接：从仓库raw/commit读取、版本/指纹核对、发送身份绑定、上下文复述与确认；区分规范旧版ZIP路线、GitHub短消息路线和失败后的受控降级，不能笼统宣布所有路径硬门禁。交接消息本身必须允许发出，禁止的是未核验时进入业务开发，不可设计成死循环门禁。
P0-B 动态需求/阶段/目标：新需求→版本化阶段目标→验收→看板；旧M1作为历史，不能新范围仍旧7/7/DONE。需求到任务到验收证据可追踪。缺配置不准READY、缺验收不准DONE，取消任务必须cancelled/superseded，不准task done伪成功。
P0-C 默认工程治理：需求澄清/架构/实现/独立审查/测试/发布/运维角色职责分明、准入退出、风险分级；强门禁用于凭据安全/权限/破坏性操作/错误项目/未经验证安装，普通可恢复等待用有界退避与检查点；不要无条件“所有异常全停死”。
P0-D 经验闭环：失败→证据→根因/假设→临时处置→永久修复→回归测试→负向测试→版本/回滚→实景验收；未闭环只能open/mitigated，不能resolved；下一项目自动应用规则，不依赖用户提醒。
P0-E 手动发送/ref/轮次：保留文档允许的整轮手动收尾，不机械禁止一切手动；无ref不能伪装自动轮询，必须人工接收/可靠身份恢复。NO_REF不可通过循环approve消除；同原因反复批准须识别无进展；旧tick与手工修状态并发必须防写回污染。
P0-F 发送不明/长生成/工具上限：区分未发出、接受未落地、结果不明、断点、拒绝；不凭页面静止或is_pending=false重发；回复若因工具上限结束，要保存完整回复，明确continue和交付清单，只有上下文已验证才续接。
P0-G S4共享仲裁：账号级原子reservation/inflight/FIFO、wait/grant/release/unknown、公平和崩溃恢复、限速竞争；两项目真实重叠时有过300s无凭据，但归因为并发互饿尚需可控回归证明，不把相关性包装成唯一根因。
P0-H 同步独立仓库：1.7.6工具侧已有sync_repos/repo_for/admin短链接；执行端gh_sync --repo和项目/目标/分支级cache/lock/log仍缺，busy返回0导致pushed误报须修；same repo根镜像有互删风险；状态、已发送正文、包、密钥扫描与允许公开范围必须保持安全边界。
P1-I 项目全生命周期：init→plan→configure→ready→develop→review→verify→release→operate→archive；Git、版本固定、policy/goals、hook声明一致、usage采集、绑定、调度、观察者、证据/回滚、独立repo逐项检查，不用“识别项目/白名单批准”代称配置成功。
P1-J inbox/通知/唤醒：for=user升级后执行端过滤导致丢递送；decision_owner与delivery_owner分离；时间/依赖/故障不等于忽略；旧Instance缓存汇总存在竞态窗口（未制造生产竞态证明），锁内重读、read failure可见；project/key/request_id准确寻址、无专属会话不串投；全局pin/fallback/wake锁及host告警错标要修。
P1-K watchdog：按project检查专属launchd/日志/心跳，native错误不能在复检消失；按(project,check)去重、持久状态锁、自身被独立观察；不同步浏览器/CDP探针结果不等于对话故障，更不应自动新建/换号。
P1-L 发布升级：共享self_update互斥、版本固定/原子切换；审查timeout如实unreviewed、有界并可恢复，不能长期事后补审当安装前审查；sha/diff/project/request身份绑定；实际安装环境测试与rollback保持。
P1-M pstack研究：现有PROCESS/DESIGN/PROJECT已提pstack，但用户要求重新彻底研究插件逻辑/角色流程；当前尚未核实具体pstack仓库/版本，不能凭名称编造或把已有说明当研究完成。先读取仓库原先相关内容，列出已知来源和待查项，执行端补研究后你再设计融合。
P1-N 范围/动作幂等：本窗口范围不应改变其他窗口全局项目；未授权删除/停服务不得发生；旧失败归档后取消相关任务，不把待办消失或IDLE当项目真正完成；执行每步先核对既有成果，再读回复，不无限查询、不反复建会话、不并发操作同一共享浏览器。

## 5. 本轮交付和验收（只读交接，不安装代码）
请按现有六块输出【本轮结论】【本机执行】【本轮回报清单】【需要用户决定】【下一轮建议档位】【驱动指令】。
1. 明确哪些GitHub文件实际读取成功、哪些缺失；引用文件章节、实际SHA而非我的期望值。
2. 复述collab-wf真正定位/角色边界/真实macOS环境、工具1.7.6/项目v3.2、旧M1和待做新阶段的区别。
3. 项目指纹按tool中的实际算法复算，明确不同于同步摘要。
4. 对本消息P0-A..P1-N给覆盖表：需求ID、影响、owner、先后依赖、拟验收、缺材料；不得遗漏，也不要声称全部已实现。
5. 提出下一版最小增量计划和正式阶段goals交付需求，不整套重写，不擅自升级安全底线，不写适配器内部接口代码。
6. 本轮无需本机执行命令，版本发布无，之后等执行端核验本轮交接；不能仍用旧7/7宣布新需求DONE。若网络无法读取raw，在同一对话报告具体失败，由执行端回传缺的文件原文，不重新开对话。

===== 执行端补充说明（wf tell）=====
- 2026-10-12T14:38 【重要补充：动态目标与新对话上下文】本次不要沿用 workflow/goals.json 的旧 M1 7/7 作为全局完成判据。当前 goals.json 的 7 项只覆盖旧里程碑『M1 无人值守稳定运行』，而不是本次新需求。请在本轮设计中加入目标账本/阶段指针机制：每次用户新需求、范围变化、故障恢复、新轮回都必须创建或更新当前阶段 goals，并记录 supersedes、scope、owner、acceptance、evidence、status、blocked_by、next_action；旧目标只能标为历史阶段完成，不得继续让看板显示『7/7 全部目标达成』来代表新阶段完成。新对话必须携带完整交接包：项目身份、最新 PROJECT/MEMORY、当前工具版本、当前阶段目标、未完成任务、失败账本、发送/对话ID、前轮为何失效、用户边界和下一步。请把动态目标、上下文交接、阶段切换、目标达成后防止无任务自动继续、目标变更审计都设计成代码和验收测试，并让看板实时显示当前阶段而非固定旧目标。
- 2026-10-12T14:39 【用户最新边界】用户已明确表示：在 collab-wf 完备前，不开始开发 Amy；Amy 目前已备份归档/移入废纸篓，之后只作为工作流全链路验收案例，不得提前恢复。
- 2026-10-12T14:58 【GitHub交接事实纠正】你说得对：本项目的正式交接不是把 zip 附件直接塞进对话，而是先 wf sync 把项目已提交文件同步到公开 GitHub，再生成 bootstrap-short/full，交接消息给 Claude 项目仓库 raw 链接和指纹，让它按清单读取并核验。当前 transport.json 的唯一同步仓库是 fashionladymall-maker/collab-wf，sync_projects 只有 collab-wf-dev；wf sync --dry-run 已输出 274 文件/3034139 字节/摘要 ff5232c5fa0c56c9，但真实 wf sync 被 safe-delete bulk confirmation 拦截，尚未声称推送成功。执行端 gh_sync.py 仍硬编码 OWNER/REPO/BRANCH，T100-1 尚未验收。请把 GitHub 交接流程作为本轮 P0：动态目标与交接门禁必须以同步成功/commit/digest/raw链接为准；没有 GitHub 同步成功证据不得称“交接完成”。同时修复 gh_sync --repo、sync_repos、项目状态/目标/证据的安全白名单同步与审计。
- 2026-10-12T15:03 【用户硬要求，必须进入正式工作流设计】以后所有流程必须清晰明确、不能模棱两可、不能绕过既定交接；每一次失败都要转成可复用经验、缺陷记录、回归判据或门禁规则，不能只在聊天里道歉。开发必须规范：需求→动态目标→完整上下文/GitHub交接→Claude方案/代码→独立审查→执行端落地→机器验收→真实运行态验收→记录/回报→下一阶段；所有状态和失败可审计、可回滚、fail-closed。请把以下硬门禁纳入代码和测试：1) GitHub同步成功(commit/digest/raw链接)前禁止把新对话交接称完成；2) bootstrap/PROJECT/WORKFLOW/CONTEXT/失败账本/当前目标/下一步未核验前禁止SEND；3) 手动发送没有ref必须拒绝进入WAIT_REPLY，不能反复NO_REF→自动批准循环；4) 目标必须动态阶段化，旧7/7只能标历史，范围变化后重建目标；5) 每次故障必须落入经验/缺陷账本并生成回归测试；6) 新对话第一轮必须只做上下文复述、指纹核对、阶段计划，不得直接写业务代码。请设计、编码、验收并提交完整实施方案，未完成不要宣称流程完备。
- 2026-10-12T15:19 【最高优先级·专业度/可复用性硬要求】用户再次明确：错误不能靠用户提醒一次、我下次临时改一次；每次错误必须沉淀成可复用经验、技能/规则、自动回归测试、状态机门禁、版本证据，保证以后同类问题不是靠记忆而是机制自动防住。请把 collab-wf 的开发基础本身做专业：建立默认工程门禁基线（即使用户没提醒也自动执行）、缺陷/经验账本、错误模式→根因→修复→回归→防复发规则映射、动态阶段目标、GitHub交接状态机、上下文核验、无ref fail-closed、独立审查、回滚、真实运行态验收。当前已知错误必须全部纳入：①手动新对话只发摘要未走GitHub/bootstrap正式交接；②wf sync被安全确认拦截后仍能手工绕过；③无ref手动发送进入WAIT_REPLY并NO_REF循环；④旧7/7目标沿用新需求；⑤回复不完整/工具上限后继续等待却无完整接续策略；⑥车队中间页误判为线路成功；⑦通道/账号切换后旧发送意图仍长期等待；⑧Amy清理后旧任务仍残留；⑨动态目标/基础项目配置缺失；⑩所有历史 W/N 缺陷。要求你交付：机制设计、数据结构、状态机、代码改动、自检/回归测试、升级包、版本与回滚证据；没有全部证据不要宣称专业基线完成。
