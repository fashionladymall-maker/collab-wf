# collab-wf 全流程能力验收报告委派包

档位：high

## 任务性质
这是对 WorkBuddy 执行端 + Claude 作者端 + collab-wf 协作工作流整体能力的系统验收，不是普通 Papertrade 业务报告。Papertrade、教程、工具迭代和历史故障均只是测试样本；最终报告应评估“工作流是否真正形成闭环”。

## 当前真实环境
- 执行端：WorkBuddy AI（国际版），本机 macOS；当前模型 GPT 系列 gpt-6.1-sol。
- 作者/方案端：Claude 网页通道，固定模型 Opus 5.5。
- 工具：collab-wf 1.6.6；项目 collab-wf-dev；当前第79轮已 DONE，目标7/7。
- 项目指纹：b848fba7f3776a69；工具 self-check：1.6.6 文件清单完全一致。
- 工作区：/Users/admin/WorkBuddy AI/2026-10-09-22-29-37
- 工作流证据根目录：/Users/admin/.workbuddy-ai/projects/collab-wf-dev/workflow/evidence/

## 目标
生成一份完整、诚实、可审计的中文 HTML 测试报告，并同时生成一份纯文本/Markdown式结论摘要（若篇幅允许可合并为 HTML 内附录）。报告需要回答：
1. 这套工作流是否真的走完 understand → plan → delegate → receive → review → apply → execute → verify → report → continue？
2. 每个阶段谁负责、用什么证据证明、哪些是真通过、哪些只是部分通过或未覆盖？
3. 过去真实任务（工具迭代、自动升级、独立审查、看门狗、事件唤醒、Papertrade 图解和 r4）分别验证了什么？
4. 用户只说自然语言任务后，执行端能否自动路由，而不让用户成为命令行操作员？
5. 剩余缺口、故障模式、不可宣称的结论和下一步改进是什么？

## 必须覆盖的章节
1. 执行摘要：总体评级，明确不能写“全部完美”。建议分级：已验证通过 / 有条件通过 / 已知关注 / 未完成实景覆盖。
2. 测试对象、范围、非目标和安全边界：不连接钱包、不签名、不入金、不交易、不操作真实资金、不处理用户凭据、不删除凭据候选。
3. 角色与责任矩阵：Requester、Claude author/planner、WorkBuddy executor/coordinator、独立 reviewer（GPT系列）、watchdog；说明同一执行端兼任多个角色时不等于独立审查。
4. 持久上下文与记忆：PROJECT.md、MEMORY.md、日记、workflow、evidence、CHANGELOG、docs，以及唯一真相源和快照滞后问题。
5. 生命周期状态机：COMPOSE、SEND、WAIT_REPLY、INTAKE、REVIEW、APPLY、EXECUTE、VERIFY、DONE、IDLE、WAIT_SCHEDULE、AWAITING_USER、QUOTA_WAIT、BLOCKED；说明每个停顿如何给出原因、下一责任人、证据和安全动作。
6. 目标与可执行判据：G1/G2/G3a/G3b/G3c/G3d/G4，列出 7/7 通过的实际命令和意义；指出 M1 判据不代表所有边界都已覆盖。
7. 委派包与结构化交接：任务包是否含目标、版本/指纹、已知事实、未验证事实、非目标、输出路径、执行端命令、验收和下一步；回报六块结构（结果、改动、证据、限制、决策、下一步）。
8. 真实交付与接收：至少覆盖第76轮研究、第77-79轮 Papertrade；作者自测与本机验收必须严格区分；说明迟到回复和交付目录/哈希核对。
9. 三层验证：机器检查、真实目标环境产物验证、不同模型系列独立审查。必须列出实际数字：Papertrade 动态图解 9图/31场景/147步，真实浏览器无错误/无外部请求/桌面手机无横溢出，r4 pass_with_concerns；独立审查 v2.2 为 concerns，block/timeout/完全无执行端退化仍未实景闭环。
10. 失败与恢复能力：W01-W45按类别汇总，不必逐条抄全文，但至少覆盖 W05/W16/W18/W23/W28/W30/W32/W36/W38/W39/W41/W42/W43/W44/W45；写清根因、修复、是否真实回归、剩余边界。特别说明结果不明不得重发、迟到回复归属、事件/心跳/注册表漏投修复、完成通知误分类修复。
11. 看门狗、inbox、事件唤醒与可见性：独立 watchdog、signals、inbox、wake、退避、无会话退化、完成通知分类；区分“已投递”和“已记已报”。
12. 安全底线与人工升级：资金、密钥/凭据、合规、实盘；用户已授权执行端代办非凭据事项，但不能据此越过四道底线；明文凭据候选仍不自动处置。
13. 版本、自更新、审计与回滚：1.5.x→1.6.6，构建、自检、文件清单、指纹、自动安装、policy、floor.probe、独立审查、回滚/保留旧版本；区分 skill、runtime、project 三种版本。
14. 用户体验与自然语言路由：普通任务直接完成；项目型/需要作者设计、代码、报告、长期状态、文件交付、独立验收的任务自动走工作流；用户不需要 wf 命令；可选覆盖短语；指出此前“直接做/走工作流”混淆及已固化修复。
15. 结果矩阵：能力项、证据、状态（PASS / CONDITIONAL / CONCERNS / UNREVIEWED / NOT_TESTED）、限制。
16. 完整审计轨迹：列出关键证据文件路径，不泄露凭据值或站点内部机制；说明哪些证据为本机、哪些为作者容器、哪些为历史记录。
17. 改进计划：按 P0/P1/P2 或短中长期排序，只提出可由执行端自行推进的事项；涉及凭据/资金/合规的事项单独标记为用户本人动作。
18. 最终验收结论：这次测试报告本身的验收判据、报告作者、执行端验收人、独立审查状态和下一步负责人。

## 必须引用的当前证据
- PROJECT.md、项目 MEMORY.md、2026-10-11 日志、workflow/evidence/turn-076、turn-077、turn-078、turn-079。
- `wf status`：第79轮 DONE（目标达成）。
- `wf explain`：全部目标判据通过，停止原因为目标达成。
- `wf goals`：G1/G2/G3a/G3b/G3c/G3d/G4 = 7/7。
- `wf fingerprint`：b848fba7f3776a69。
- `wf self-check`：1.6.6 文件清单完全一致。
- `wf audit --n 60`、`wf decisions --tsv`、`wf task list`。
- Papertrade 文件与截图：
  - outputs/papertrade-visual-learning-20261011.html
  - outputs/papertrade-business-research-20261011-r4.html
  - outputs/papertrade-visual-learning-desktop-full.png
  - outputs/papertrade-visual-learning-mobile.png
  - outputs/papertrade-r4-mobile.png
- Papertrade 结论：本机独立验收 `pass_with_concerns`；r4 的 EPS=1e-9 正债权边界仍待业务修复或明确计量单位，未擅自改业务源。

## 报告口径要求
- 中文、结构化、面向用户本人当前 macOS + WorkBuddy + Claude 环境；不要去个人化，不写成陌生人产品说明书。
- 不把作者容器自测冒充本机通过；不把工具目标7/7冒充工具全部完美；不把一次真实 concerns 审查冒充 block/timeout/无执行端全覆盖。
- 对每项事实标注：本机实测、作者自测、官方规则、历史证据、推断或未验证。
- 报告可包含附录表格、时间线、流程图和证据索引；必须离线自包含，无外部资源。
- 生成文件名：`outputs/collab-wf-capability-acceptance-report-20261011.html`，必要时另生成 `outputs/collab-wf-capability-acceptance-summary-20261011.txt`。
- 交付后不要把作者自测当最终通过；由执行端做 node --check、真实浏览器桌面/手机、外部资源、关键路径和页面错误验收。
- 完成后在报告中明确“下一步与负责人”：执行端可自行做的直接列入；用户本人动作只列凭据/资金/合规/实盘类。

## 驱动指令
发布：报告文件 + 摘要文件
之后：等执行端本机验收
验收 1：c1 exit=0 且输出包含“测试报告”
验收 2：c2 exit=0 且输出包含“PASS”与“CONCERNS”
验收 3：c3 exit=0 且输出包含“工作流”与“下一步”
指纹：b848fba7f3776a69
