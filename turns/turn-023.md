【回报 · 第 23 轮 · v0.7】档位：high

① 环境障碍与偏离
无。

② 关键数字与终端原文

续传：之前的回报未能容纳的原文
（第 21 轮 c07，清单第 7 项，第 1/1 片，全文 sha256 前 12 位 3b385389af58）
````
$ cat /Users/admin/.workbuddy-ai/tools/collab-wf/docs/DRIVER.md
# DRIVER.md —— 驱动器：让协作流程自己跑

从 1.1.0 起，工作流不再需要执行端逐条敲命令。系统定时唤醒 `wf tick`（或单个项目的 `wf run`），驱动器读状态、做当前能做的事、记账，直到需要等待外部或需要用户为止。

## 1. 一次唤醒做什么

```
加锁（同一项目同时只有一个驱动器）→ 检查暂停与白名单 → 健康检查（声明的长期进程）
→ 循环：按当前状态调用对应处理 → 有进展就继续 → 等待外部 / 需要用户 / 时间用完就停
→ 记录本次做了什么、停在哪里、为什么 → 释放锁
```

退出码：0 有进展或无事可做；10 在等外部（Claude、计划节点、额度、退避）；20 需要用户；30 目标达成；1 工具内部错误。

各状态的自动处理：

| 状态 | 驱动器做什么 |
|---|---|
| 组织消息 | 计划节点到期：自动生成节点消息；回复中断：自动生成“从断点继续”；验收后：自动生成回报（含“需要注意”）；档位按角色规则；密钥扫描；对话体量达到上限则停下并准备恢复包 |
| 发送 | 全局限速；先落盘“发送意图”，再调用适配器发送；只有拿到“生成已开始”的正面证据才算发出（只有心跳按通道忙处理）；被拒退避重试；额度用尽进入等待额度重置 |
| 等待 Claude | 定时轮询适配器；完成后保存回复与交付文件；叶子被安全防护拒绝时自动回退到上一条健康消息并重发本轮消息；手动发出且未登记 ref 的轮次会停下提示 |
| 接收与解析 | 解析五个固定块与【驱动指令】；缺块就请 Claude 从断点继续（最多 2 次） |
| 执行前评审 | 每条命令过白名单与停止底线；版本包检查新增内容与版本号；可选的独立审查；有一项不通过就停下等用户 |
| 落地版本 | 门禁基线 → wf apply → 门禁复查；落地后回归则自动回退并回报；可选核对仓库指纹 |
| 执行与取证 | 逐条执行，每条前落盘“执行到第几条”；失败按异常字典处理 |
| 验收 | 按【驱动指令】中的判据逐项判定；运行目标检查；按“之后”决定回报、等待或空闲 |
| 等待计划节点 / 等待额度重置 | 到点自动继续 |
| 等待用户决定 / 阻塞 / 目标达成 | 不做任何事，说明需要谁做什么 |

## 2. 不会出现“状态说在做、其实没在做”

- 每次运行在 state.json 中登记进程号；看板实时核对它是否还活着。上一次运行被打断，下次运行会记录“驱动中断”并从记录处接续。
- 发送前先落盘发送意图。被打断后只轮询确认，绝不重发；通道没有任何“已接受或正在生成”的迹象时安全停机（F14）。
- 执行前先落盘当前命令。被打断的那条命令不会被重跑，记入回报由 Claude 决定。
- 落地版本中途失败会自动恢复工作区（F15）。
- 状态写入互斥（1.5.2）：手工 wf 命令与驱动器同时写状态时，在同一把状态锁内与磁盘合并，只写回各自改过的字段；事件序号在锁内分配，不会重复。驱动器每一步之前合并一次并检查暂停，手工 wf pause 不会再被覆盖。

## 3. 自主边界（写在代码里）

**白名单默认拒绝**：只有 workflow/policy.json 中、经用户批准的命令模式会被自动执行；其余一律停下等用户。
**停止底线（内置，项目无法关闭）**：资金、密钥、合规、实盘四类命令；版本包中新增的实盘开关、密钥赋值、提现转账调用。
**护栏命令只能由用户执行**：wf self-update、self-rollback、pin、policy、goals approve/confirm、approve、unpause、force、unblock、resume、undo、rollback、init。驱动器没有调用这些命令的路径，白名单也无法放行它们。
**会改变工作流状态的 wf 命令驱动器永远不执行**：apply、pin、wait、verify、goals check、self-update 等只能在驱动器之外手动执行；出现在回复中时，驱动器停下列为手动步骤，用户批准后跳过它们继续（F21）。只读的 wf 命令（version、fingerprint、doctor、status、explain、audit、decisions、effort 等）可以在白名单允许时自动执行。
**命令拼接与重定向需要批准**：; && || > < ` $( & 一律停下（2>&1 除外），防止在白名单命令后拼接任意命令；管道只允许接过滤命令（head、tail、grep、wc、sort、uniq、cut）。
**批准绑定内容**：policy.json、goals.json 的批准按 sha256 记录在 workflow/approvals.json（受保护，版本包无法覆盖）。内容一改动，批准立即失效，驱动器停止。
**熔断**：连续 3 轮验收未通过、连续 8 轮目标没有进展、每天自动发送超过 20 轮，都会停下等用户（数值在 policy.json 中设定）。

> 说明：这些限制能阻止驱动器自行越界，但无法在技术上区分“用户本人”和“同一台电脑上的其他程序”执行护栏命令。批准是一个有记录、可审计的动作，而不是密码学意义上的签名。

## 3a. 门禁与自动回退

policy.json 的 gates 是用户批准的检查（例如能否导入、能否编译、测试是否通过），版本包无法单方面修改：

```json
"gates": [{"name": "imports", "cmd": "python run_backtest.py --list", "pass": {"exit": 0, "contains": ["ema_trend_vol"]}}]
```

落地前先跑一遍作为基线，落地后再跑一遍。落地前通过、落地后失败，就是这个版本引起的回归：驱动器自动回退到上一版本，把该版本的标签改名为 vX.Y-reverted-时间，记入决策日志，并回报给 Claude。落地前就失败的门禁不会触发回退，只记入回报。

## 3b. 独立审查（可选）

transport.json 中配置 review 程序后，每个版本包落地前，驱动器把改动的 diff 交给它（TRANSPORT.md）。它来自执行端可以接入的另一个模型系列，结论只有三种：pass、concerns（写入“需要注意”）、block（停下等用户）。它不能代替门禁与判据，也不能单独放行任何东西。

## 3c. 档位、决策与复盘

每条消息的档位由 roles.json 的角色规则决定（DESIGN.md 第 7 节），角色、档位与理由写入轮次记录与决策日志。决策日志 workflow/decisions.jsonl 只追加不修改，记录档位选择、门禁结果、自动回退、熔断、对话体量、独立审查、等待节点等决定：`wf decisions --tsv`。`wf effort` 按档位与角色复盘轮次、通过率、消耗与耗时。

## 3d. 对话体量

policy.json 的 max_conversation_turns 或 max_conversation_chars 达到时，驱动器停下，并自动生成新对话恢复包（wf bootstrap）。用户开新对话后先 `wf conv new --note …`（它把新对话的起点记为下一轮，先发送会把说明消息那一轮排除在外），再上传 zip、发送说明消息，然后 `wf approve --note …`。

## 4. 目标与完成判据

workflow/goals.json 声明目标，每个目标是可执行判据（检查命令与通过条件）或人工判据（只能由用户确认）。只有用户批准后才生效：

```bash
wf goals                     # 进度
wf goals approve --note "已阅读并同意"
wf goals confirm G2 --note "已确认平台合规"
wf goals check               # 立即运行一次可执行判据
```

驱动器在每轮验收后运行可执行判据；全部通过（人工判据已确认、且目标定义已批准）即进入“目标达成”并停下。目标被改动后，之前的确认与检查结果全部失效。

## 5. 【驱动指令】：让回复可以被自动执行

Claude 的回复在五个固定块之外，增加第六块【驱动指令】，每行一个键：

```
发布：btc-quant-v0.12.zip        （本轮要落地的版本包，或“无”）
之后：回报 | 空闲 | 等待 2026-10-16T16:30:00+00:00 模拟盘第一周对账
验收 1：c1 exit=0 包含 "自检已通过"
验收 2：c2 exit=0 不含 "需重启" 匹配 "变更 \d+ 个文件"
指纹：00d7c558e45dcb25           （落地后仓库指纹应为此值）
超时：900                         （每条命令的超时秒数）
失败后继续：否
```

cN 指【本机执行】中第 N 条命令。【本机执行】只放驱动器可以执行的命令；工具升级、批准等需要人做的操作写在正文中，不放进命令块。没有判据的清单项记为“暂缓”，原文进入回报由 Claude 判断。（1.5.0 起：暂缓项按“文字中的 cN → 反引号中的命令原文 → 序号一一对应”自动关联证据；原文按预算完整嵌入，放不下的分片续传；键所在行前的列表编号会被容忍；指令块有内容却解析不出任何键时写进“需要注意”。）任何失败都会把“之后”改为回报。1.5.2 起：有判据的命令按判据判定，预期无匹配的检索写 exit=1，不算失败；整轮成败与熔断只计验收未通过的项和未关联到清单项的失败命令，没有判据的清单项由 Claude 看原文判断、不计入熔断。“需要用户决定”中只有显式标注“（前置）”的事项会让本轮停下等用户，“不阻塞”之类的字样不会。

## 6. 异常字典

工具根目录的 anomalies.json：症状 → 处置 → 次数上限 → 超限后的动作。项目可以在 workflow/anomalies.json 中追加规则（优先匹配）。处置包括退避重试、等待额度重置、回报给 Claude、请 Claude 从断点继续、按声明拉起进程、停下等用户、阻塞。没有匹配的情况一律安全停机并完整上报（UNKNOWN）。

## 7. 调度与唤醒

由系统定时器（不需要常驻进程）每隔几分钟运行一次：

```bash
wf schedule plist > ~/Library/LaunchAgents/local.collab-wf.tick.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.collab-wf.tick.plist
wf schedule status               # 各项目驱动器最近一次运行；超过 3 个周期视为定时器可能停了
```

生成的配置每 5 分钟运行 `wf tick --max-seconds 540`，使用绝对路径的 Python，日志写入 local/tick.log（超过 5 MB 自动轮转）。

定时器的失败模式：电脑睡眠时 launchd 不运行，醒来后补跑一次（驱动器幂等，不会重复做事）；用户注销后 LaunchAgent 不运行；launchd 的环境变量很少，所以配置里写明 PATH 与 Python 的绝对路径；一次运行超过间隔时，下一次会因为项目锁而跳过；定时器停了时，看板的驱动器卡片会变红（超过 15 分钟没有运行），`wf schedule status` 也会提示。

wf tick 推进全部已接入的项目：可以立即行动的先做（按 wf.json 的 priority），等待中的轮询一次，需要用户的跳过；wf.json 中 "auto": false 的项目不推进。任何一个项目遇到额度用尽，其他项目也会暂停发送直到重置。驱动器只运行无界面的子进程，不操作鼠标键盘、不开窗口、不截屏；传输适配器也必须遵守这一点（TRANSPORT.md）。

## 7a. 手动发送与驱动器接管（缺口 7）

一轮消息如果是在驱动器之外手动发出的，驱动器默认认不出它（它靠 ref 轮询）。两种做法：手动发送时用 `wf mark sent --ref <适配器返回的 ref>` 登记，驱动器就能接管轮询；或者整轮手动走到底，用 `wf mark replied` 收尾。没有 ref 又没手动收尾时，驱动器会停下并说明，而不是误判为“看不到”。

## 7b. 叶子被拒的自动恢复（§10）

通道的安全防护有时会拒绝一条消息（空回复、stop_reason=refusal），且无法从这条消息继续。适配器在 poll 时返回 `status: refused` 和 `parent_ref`（上一条健康消息）。驱动器据此回退到那条消息、重新组织并重发本轮消息，不新增轮次。同一轮连续被拒超过两次就停下问用户（可能是消息内容本身触发了防护）。1.5.0 起次数可配置：policy.json 的 max_leaf_refusals（默认 2，保持原行为）。建议设为 0，第一次被拒就停下由人查看那条消息：原样重发绕不开触发原因，只会多耗额度。

## 8. 用户的三个单命令

```bash
wf pause --note "原因"        # 暂停（立即生效，驱动器不再推进）
wf undo --note "原因"         # 回退到上一个版本并暂停
wf unpause --note "原因"      # 恢复
```

接管：暂停后，所有手动命令照常可用；恢复前用 wf explain 确认状态。

## 9. 审计

```bash
wf explain                    # 现在卡在哪、为什么、需要谁做什么
wf panel                      # 聊天窗口内联面板（HTML 小组件）
wf audit --n 60               # 自动执行过的一切：规则、动作、依据、结果
```

看板顶部的“驱动器”卡片显示同样的信息，以及最近一次运行做了什么、是否仍在运行。

## 10. 启用步骤（每个项目一次）

1. 配置传输适配器（每台机器一次）：local/transport.json，见 TRANSPORT.md；`wf transport` 检查。
2. Claude 交付 workflow/policy.json 与 workflow/goals.json；用户审阅后 `wf policy approve` 与 `wf goals approve`。
3. 系统定时器运行 `wf tick`。
4. 发起：`wf kick --message 任务文件`，或等计划节点到期。

## 11. 1.5.0 的可靠性约定

- 块标题必须位于行首（前面可有空白或 #、*、>、- 等标记）。同名标题出现多次时，按约定顺序取“在上一个块之后最早出现”的那个；只在行中间出现时退回旧规则并提示。正文中顺带提到块名，不会再顶掉真正的命令块。
- 回报原文：清单项没有判据时，按“文字中的 cN → 反引号中的命令 → 序号一一对应”关联证据；未关联的命令也附原文。原文完整嵌入、每段只出现一次；整份回报约 32000 字符的原文预算放不下的部分，分片写入 workflow/outbox/carry.json，下一份回报自动先补发。想尽快清空，安排一轮不执行命令的回报即可。
- 回复归属：适配器在 poll 完成时返回 reply_parent。它与本轮发送的 ref 不同，说明取回的是对话里已有的旧回复，驱动器不收下、继续等待，连续 3 次（policy 的 stale_reply_polls）后按 stale_reply 停下。不提供时行为不变，记 reply_unverified 事件，wf doctor 提示。
- 执行接续：驱动器被打断后接续时，若被标记的那条命令在证据里已有退出码与结束时间，记 exec_resumed，不再误报“执行时被中断”。
- 事件序号：先写状态再追加事件；被打断时只会跳号，不会重号。wf doctor 检查重复、倒序与坏行。
- 额度：轮次记录中的额度为空时同时写明原因（usage_note）；usage.json 的有效期与 wf doctor 的判定统一为 10 分钟；连续 3 条以上为空时 wf doctor 报警。
- 升级核验：wf self-update 安装后核对磁盘版本、每个文件与版本包一致、升级前在运行的看板已以新版本运行；任何一项不通过，就把该版本的标签改名为 wf-vX.Y.Z-failed-时间，并自动回滚到升级前的版本。核验写在新版本的代码里，所以从 1.5.0 升级到之后的版本时才生效；从 1.4.x 升到 1.5.0 这一次，需要手动核对 wf version、wf doctor 与 wf self-check。
- 无附件续接：wf bootstrap 的说明消息末尾附工具与项目的文件清单。通道不能上传附件时，Claude 据此点名读取。

## 12. 1.5.1 发送结果不明的处置

- 适配器超时先 SIGTERM、宽限后再 SIGKILL，并通过 WF_ADAPTER_TIMEOUT_S 告知上限（TRANSPORT.md 末节）。
- 发送结果不明（SEND_UNKNOWN）：默认照旧阻塞。policy.json 设 "send_unknown_wait": true 后改为 SEND_UNKNOWN_WAIT：保留发送意图，退避约一小时反复轮询确认，确认已送达就接续等待回复，仍不明再阻塞。任何情况下都不自动重发。
- 阻塞之后：想再核对一次用 wf unblock --to SEND；确认没有送达、需要重发用 wf resend（决定码加原话）。不要手改 state.json。
- 通道报忙：最可能的就是自己的上一条消息在生成。第 40 轮的回复用了 23 分钟，其间会话里看不到任何变化。
- wf note：本轮回报生成之后写的备注，会随下一份回报发出，写入时会提示。
- wf self-check 归为只读命令，驱动器可以直接执行（白名单 policy.json 仍需包含它）。
- 停在“等待用户”且等的是命令批准时，由驱动器推进的项目用 wf approve；wf resume 回到评审后驱动器会重新评审并再次停下（会给出提示）。
- 说明类备注（wf note --kind info）出现在回报 ① 段之后的“说明（备注）”中。

## 13. 1.5.2 并发、判据与续传

- 状态并发（W18）：状态的读-改-写在跨进程状态锁（local/locks/<项目>.state.lock）内完成，与磁盘三方合并，只写回本进程改过的字段；事件序号按磁盘上的最新值分配，追加也在锁内。驱动器每一步之前合并一次并检查暂停。
- 判据与熔断（W21）：见第 5 节。探查性的检索（可能无匹配）写判据 exit=1，或改用 sed、cat。
- 续传（W20）：续传先于本轮原文嵌入；一片都放不下时不输出空标题。
- 手工回报（W19）：wf report 生成的本轮草稿由驱动器接着发送。
- 开新对话：先 wf conv new，再发送说明消息（第 3d 节）。

## 14. 1.5.3 Claude 页面快照与对话记录

- 工作流看板新增“Claude 页面快照”与“对话记录”两个板块（只读）。快照：驱动器每次轮询、执行端每次 wf look，适配器都按 docs/SNAPSHOT.md 写出页面快照；内容变了就更新，没变只记录查看时间。对话记录：执行端与 Claude 之间收发的全文，以及用户的原话（wf said、用户批准时的原话），逐字显示。
- 快照只用于展示与取证，不参与状态判定。
- wf said --text "…"：执行端转述用户原话时逐字记下。它会改写对话记录，驱动器不会自动执行它（Claude 不能替用户说话）。

## 15. 1.5.4 页面截图

- 快照有文本与截图两种形态（用户第 13 轮要求）。驱动器每次轮询、wf look 每次查看，都同时请求文本快照与截图（WF_PAGE_SCREENSHOT）；适配器按 docs/SNAPSHOT.md 第 5 节截图：不切换、不滚动用户的标签页，不抢焦点；浏览器不可用时只写文本快照并说明原因。
- 截图与同一轮的文本快照成对保存，按轮保留最近 20 张；policy.json 的 page_screenshots 为 false 时轮询不再请求截图。wf look --full 截整页。

## 16. 1.5.6 驱动器心跳

- 判据与看板“驱动器”卡片相同：最近一次运行超过 15 分钟、白名单已批准、项目没有暂停，就判为停了。wf doctor 增加“驱动器心跳”一项（停了就警告），wf ask 主动呈报“驱动器停了”（每次停下只报一次）。
- 停了的常见原因是用手写循环唤醒：循环跑满次数会静默退出。唤醒只用系统定时器（第 7 节；docs/PROCESS.md 第 1 节第 3 步）。
- 计划节点只用来等外部条件；需要执行端手动操作时写“之后：空闲”，执行端做完用 wf kick --kind report 回报（PROCESS.md 第 8 节）。

## 17. 1.5.7 系统通知

- wf tick 推进完之后，对每个项目取与 wf ask 同一来源的待决事项与提示，新出现的弹成 macOS 通知（osascript）。每件事只通知一次，记在 local/notified.json，与 wf ask 的已读互不影响；事项处理掉后记录清除，再出现会再通知；发送失败不记为已通知。
- 关闭：环境变量 WF_NOTIFY=0，或 local/notify.json 写 {"enabled": false}。wf notify test 试发一条，wf notify status 查看状态。通知出任何问题都不影响 wf tick。

## 18. 1.5.8 自动安装工具版本、tick 内等回复

- 自动安装：policy.json 的 auto_self_update 为 true、并且白名单放行时，驱动器可以执行 wf self-update --auto workflow/outbox/collab-wf-X.Y.Z.zip——这是唯一一种可由驱动器执行的 wf 写操作。第 3 节的“护栏命令只能由用户执行”对其余命令不变。
- 安装前由当前已安装的代码检查：改动了护栏文件（policy.py、release.py、asks.py、anomalies.json）的版本拒绝自动安装，停下等用户确认后手动安装；配置了另一个模型系列审查时先审 diff，结论为 block 也拒绝；--auto 不允许跳过自检。安装之后的核验与失败自动回滚照旧（第 11 节）。
- tick 内等回复：wf tick 处在“发送”与“等待 Claude”时，下一次轮询的时刻若在本次唤醒的时间预算内，就在 tick 里等到那时再查，不交还给下一次唤醒；轮询间隔小于 30 秒（测试）或单独运行 wf run 时不这样做。
- 命令退出码为 0、只是没通过判据时，由验收报 CHECK_FAIL，不再同时报“命令执行失败”。
- 另一个模型系列审查的约定见 docs/REVIEW.md。
exit=0  耗时 0.0s
````

1) asks.py 全文。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ cat /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/asks.py
"""需要用户决定的事项（决定码）与聊天窗口呈报。

每件待用户决定的事项都有一个短的“决定码”，由事项本身的内容算出：事项变了，码就变了。
用户在聊天窗口回复“批准 K7P”，执行端把码和用户原话一起交给 wf；wf 只接受与当前待决事项一致的码。

这能防止的是“批准落到了错误的事项上”（含糊的“好的”、过期的批准、同时有两件待决事项时张冠李戴）。
它不能在技术上区分“用户本人”与“同一台电脑上的其他程序”：执行端仍然可以伪造原话。
防线是：原话被逐字记录，用户能在看板和审计中看到每一次批准用了什么原话（CAPABILITIES.md）。
"""
from __future__ import annotations

import hashlib

from . import machine, policy
from .core import Instance, WfError, now_iso, secs_since

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 去掉易混淆的 I、O、0、1
REMIND_H = 12


def _code(key: str) -> str:
    h = int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16)
    return "".join(ALPHABET[(h >> (5 * i)) % len(ALPHABET)] for i in range(3))


def _item(kind, key, title, why, reply, command, consequence, basis=""):
    c = _code(key)
    return {"code": c, "kind": kind, "key": key, "title": title, "why": why,
            "reply": [r.format(code=c) for r in reply], "command": command.format(code=c),
            "consequence": consequence, "basis": basis}


def pending(inst: Instance) -> list:
    """当前所有需要用户决定的事项。按紧迫程度排序：阻塞 > 等待批准 > 白名单与目标 > 人工确认 > 暂停。"""
    st = inst.state
    s = machine.status(inst)
    out = []
    if s["state"] == "BLOCKED":
        out.append(_item("unblock", f"unblock:{s['turn']}:{s['since']}", "流程阻塞，需要人处理",
                         s.get("block_reason") or "某一步失败次数超过上限",
                         ["已处理 {code}"], f"wf unblock --to {s.get('blocked_from') or 'COMPOSE'} --code {{code}} --said …",
                         "流程保持停住，不会自动继续", "wf status 显示该步的异常处置表"))
    if s["state"] == "AWAITING_USER":
        out.append(_item("approve", f"approve:{s['turn']}:{s['since']}", "需要你批准才能继续",
                         st.get("needs_text") or "驱动器停下来等你决定",
                         ["批准 {code}", "暂停"], "wf approve --code {code} --said …",
                         "驱动器保持停住，不会自动继续", "评审结果与证据在看板“本轮执行”"))
    ps = policy.status(inst, "policy")
    if ps["exists"] and not ps["approved"]:
        why = "白名单在批准后被改动，需要重新批准" if ps["changed_since_approval"] else "白名单尚未批准"
        out.append(_item("policy", f"policy:{ps['current']}", "批准自动执行的命令白名单", why,
                         ["批准 {code}"], "wf policy approve --code {code} --said …",
                         "驱动器不会自动执行任何操作", "workflow/policy.json"))
    gs = policy.status(inst, "goals")
    if gs["exists"] and not gs["approved"]:
        why = "目标在批准后被改动，需要重新批准" if gs["changed_since_approval"] else "目标尚未批准"
        out.append(_item("goals", f"goals:{gs['current']}", "批准目标与完成判据", why,
                         ["批准 {code}"], "wf goals approve --code {code} --said …",
                         "无法判定“做完了”", "workflow/goals.json"))
    elif gs["approved"]:
        from . import goals
        for g in goals.progress(inst)["items"]:
            if g["kind"] == "manual" and not g["ok"]:
                out.append(_item(f"confirm:{g['id']}", f"confirm:{g['id']}:{gs['current']}", f"确认目标 {g['id']}",
                                 g["title"], ["确认 {code}"], f"wf goals confirm {g['id']} --code {{code}} --said …",
                                 f"目标 {g['id']} 一直算未完成：{g.get('on_fail') or ''}", "workflow/goals.json"))
    if st.get("paused"):
        out.append(_item("unpause", f"unpause:{st['paused'].get('ts')}", "自动化已暂停",
                         st["paused"].get("note") or "已暂停", ["继续 {code}"], "wf unpause --code {code} --said …",
                         "保持暂停，驱动器不会推进这个项目"))
    return out


def notices(inst: Instance) -> list:
    """不需要决定、但应该让用户知道一次的事。"""
    s = machine.status(inst)
    out = []
    if s["state"] == "DONE":
        out.append({"key": f"done:{s['turn']}", "title": "目标已达成", "why": "全部目标判据已通过，自动化停在这里"})
    if s["state"] == "QUOTA_WAIT":
        out.append({"key": f"quota:{s.get('until')}", "title": "额度用尽，等待重置",
                    "why": f"将在 {s.get('until')} 后自动继续，不需要你做什么"})
    if s.get("stuck"):
        out.append({"key": f"stuck:{s['state']}:{s['since']}", "title": f"“{s['name']}”停留超过上限",
                    "why": "驱动器会按异常处置表处理；若一直这样，看板会显示原因"})
    from .heartbeat import driver_health   # 1.5.6：驱动器停了要让用户知道（手写循环会静默退出）
    hb = driver_health(inst)
    if hb["status"] == "stale":
        out.append({"key": f"driver_stale:{hb['last_ts']}", "title": "驱动器停了",
                    "why": f"最近一次运行在 {hb['age_s'] // 60} 分钟前，系统定时器可能没有运行。执行端用 wf schedule status 检查，"
                           "按 docs/PROCESS.md 第 1 节第 3 步安装定时器"})
    return out


def verify(inst: Instance, kind: str, code: str) -> dict:
    """确认码与当前待决事项一致。kind 可以是前缀（例如 confirm:G2）。"""
    items = pending(inst)
    code = (code or "").strip().upper()
    for it in items:
        if it["code"] == code and (it["kind"] == kind or it["kind"].startswith(kind + ":")):
            return it
    cur = "；".join(f"{it['code']}（{it['title']}）" for it in items) or "无"
    raise WfError(f"决定码“{code}”与当前待决事项不符，或已过期。当前待决事项：{cur}")


def render(inst: Instance, it: dict) -> str:
    s = machine.status(inst)
    alt = "；或回复：" + "、".join(f"「{r}」" for r in it["reply"][1:]) if len(it["reply"]) > 1 else ""
    lines = [f"【需要你决定 · {inst.name} · 决定码 {it['code']}】",
             f"事项：{it['title']}",
             f"现在：第 {s['turn']} 轮 · {s['name']}",
             f"原因：{it['why']}",
             f"你回复：「{it['reply'][0]}」{alt}",
             f"不处理会怎样：{it['consequence']}"]
    if it.get("basis"):
        lines.append(f"依据：{it['basis']}")
    return "\n".join(lines)


def ask(inst: Instance, only_new=False, mark=False) -> dict:
    """呈报：返回需要出现在聊天窗口里的内容。去重：同一事项只呈报一次，仍未处理时每 12 小时提醒一次。"""
    st = inst.state
    shown = st.setdefault("asks_shown", {})
    items, notes = pending(inst), notices(inst)
    live = {it["code"] for it in items} | {n["key"] for n in notes}
    for k in list(shown):
        if k not in live:
            del shown[k]
    out_items, out_notes = [], []
    for it in items:
        rec = shown.get(it["code"])
        if not only_new or rec is None or (secs_since(rec["ts"]) or 0) > REMIND_H * 3600:
            out_items.append(it)
    for n in notes:
        if not only_new or n["key"] not in shown:
            out_notes.append(n)
    if mark:
        for it in out_items:
            r = shown.get(it["code"], {"n": 0})
            shown[it["code"]] = {"ts": now_iso(), "n": r["n"] + 1}
        for n in out_notes:
            shown[n["key"]] = {"ts": now_iso(), "n": 1}
        inst.save()
    text = "\n\n".join([render(inst, it) for it in out_items] +
                       [f"【通知 · {inst.name}】{n['title']}：{n['why']}" for n in out_notes])
    return {"items": out_items, "notices": out_notes, "text": text}


def panel(inst: Instance) -> str:
    """一块自包含的 HTML 小组件，执行端内联渲染进 WorkBuddy 聊天窗口（不开浏览器）。
    只放用户真正要看的最小集：现在在哪、要不要他拍板（含决定码与照抄的话）、下一次自动检查时间。
    与每轮回报的分工：回报是“这一轮做了什么”，面板是“此刻的状态与待办”，不重复叙述过程。"""
    from . import driver
    from .core import parse_iso
    e = driver.explain(inst)
    items, notes = pending(inst), notices(inst)
    st = inst.state
    nxt = ""
    until = parse_iso(st.get("until")) if st.get("state") in ("WAIT_SCHEDULE", "QUOTA_WAIT") else None
    if until:
        nxt = f"下一次自动检查：{until.strftime('%m-%d %H:%M')}"
    elif st.get("paused"):
        nxt = "已暂停，不会自动推进"
    esc = lambda x: (str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    rows = ""
    for it in items:
        reply = esc(it["reply"][0])
        alt = "".join(f'<span class="k">{esc(r)}</span>' for r in it["reply"][1:])
        rows += (f'<div class="it"><div class="t">{esc(it["title"])}'
                 f'<span class="code">{esc(it["code"])}</span></div>'
                 f'<div class="w">{esc(it["why"])}</div>'
                 f'<div class="r">回复 <span class="k">{reply}</span>{alt}</div>'
                 f'<div class="c">不处理：{esc(it["consequence"])}</div></div>')
    for n in notes:
        rows += f'<div class="it note"><div class="t">{esc(n["title"])}</div><div class="w">{esc(n["why"])}</div></div>'
    if not rows:
        rows = '<div class="ok">暂时不需要你做什么。</div>'
    color = "#B4761A" if (items or st.get("paused")) else "#3B7D4F"
    return (
        '<div style="font:14px/1.5 -apple-system,system-ui,sans-serif;max-width:560px;'
        'border:1px solid #d9d4c8;border-radius:10px;overflow:hidden;margin:4px 0">'
        f'<div style="background:{color};color:#fff;padding:8px 12px;font-weight:600">'
        f'{esc(inst.name)} · {esc(e.get("where",""))}</div>'
        '<div style="padding:10px 12px">'
        f'<div style="color:#555;margin-bottom:8px">{esc(e.get("why",""))}'
        f'{("　·　" + esc(nxt)) if nxt else ""}</div>'
        '<style>'
        '.it{border-top:1px solid #eee;padding:8px 0}.it:first-of-type{border-top:0}'
        '.it .t{font-weight:600}.it .code{background:#2d2d2d;color:#fff;border-radius:4px;'
        'padding:1px 7px;margin-left:8px;font-family:ui-monospace,monospace;letter-spacing:1px}'
        '.it .w{color:#555;margin:2px 0}.it .r{margin:3px 0}.it .c{color:#999;font-size:12.5px}'
        '.k{background:#f0ece0;border:1px solid #d9d4c8;border-radius:4px;padding:1px 6px;margin-right:6px;'
        'font-family:ui-monospace,monospace}.note .t{color:#555;font-weight:500}'
        '.ok{color:#3B7D4F;padding:4px 0}'
        '</style>'
        f'{rows}</div></div>')
exit=0  耗时 0.0s
```

2) driver.py 中 classify 的调用位置。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n classify /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
242:        c = policy.classify(start, ctx.pol) if start else {"auto": False, "reasons": ["没有声明 start 命令"]}
622:        cl = policy.classify(c, ctx.pol)
652:    review_rec = {"rows": [policy.classify(c, ctx.pol) for c in it.get("commands", [])], "findings": findings,
834:        if policy.classify(cmds[i], ctx.pol).get("manual"):
exit=0  耗时 0.1s
```

3) wf.py 中 notify 的调用位置。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n notify /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
66:  wf notify test | status            系统通知：发一条测试通知 / 查看是否开启（wf tick 跑完后自动通知新的待决事项）
316:    x = sub.add_parser("notify"); x.add_argument("action", choices=["test", "status"])
664:        from wflib import notify   # 1.5.7：新出现的待决事项与提示弹成系统通知（每件事只通知一次）
665:        notify.after_tick(insts)
716:    elif c == "notify":
717:        from wflib import notify
719:            ok = notify.send("collab-wf", "这是一条测试通知：wf tick 跑完后，会这样提示需要你处理的事。", "通知测试")
722:            print(notify.status_text())
exit=0  耗时 0.0s
```

4) wf.py 中 schedule plist 的位置。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n plist /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
54:  wf schedule plist | status          生成系统定时器配置（launchd）/ 检查定时器是否在运行
204:    if args.action == "plist":
210:<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
211:<plist version="1.0">
234:</plist>''')
235:        print(f"<!-- 保存为 ~/Library/LaunchAgents/{label}.plist，然后：launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/{label}.plist -->",
332:    x = sub.add_parser("schedule"); x.add_argument("action", choices=["plist", "status"])
exit=0  耗时 0.0s
```

5) wf.py 中 self_update 的位置。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n self_update /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
916:        r = release.self_update(Path(args.zip), args.skip_test, args.allow_same, auto=bool(getattr(args, "auto", False)))
exit=0  耗时 0.0s
```

6) selftest_v15.py 中 1.5.8 测试的注册位置。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n v158 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_v15.py
620:def _t_v158_linger(_ok):
644:def _t_v158_auto_update(_ok):
673:         ("1.5.8 等回复与审查文档", _t_v158_linger), ("1.5.8 自动安装工具版本", _t_v158_auto_update)]
exit=0  耗时 0.1s
```

③ 需要决策的点
无。

④ 需要注意（Attention）
- 清单第 1, 2, 3, 4, 5, 6 项没有机器判据，结论需要 Claude 根据原文判断
- 当前对话已累计约 47746 字符、2 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

附：仓库指纹 65f6fad7a591346c（v0.7，4 个文件）
附：发送前额度 5 小时窗口 18.0%，7 天窗口 65.0%
