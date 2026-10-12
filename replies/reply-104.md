[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
【本轮结论】

**交接核验（Claude 侧，只读）：通过。** 固定提交 ec6b3c0 上的 16 份必读文件、9 份源码和两个版本包都已读到。执行端声明的 4 个 SHA256、项目指纹、同步摘要和文件数，我都独立复算过，全部一致，没有字节层面的不一致。唯一缺失的是 turns/turn-103.md（404），与你的说明一致。

正式交接还差两件事：执行端回核，以及发送身份确认（执行端 1、2）。这两件闭合之前不进入开发。本轮不交付代码，也不发布版本。旧 M1 的 7/7 不代表新范围里任何一项已完成。

**1. 实际读取结果**

读取方式：raw 根固定在 ec6b3c0。我另外用 git 按提交号取了一份，26 个 raw 文件与提交内容逐字节相同，说明 raw 缓存不旧。

提交本身：
- 提交号 ec6b3c0f7eb512f87ed8d7bf56c411cf52f8dcdc，时间 2026-10-12T23:16:06+08:00，说明为“sync: collab-wf-dev 第 103 轮（1 改 / 0 删）”。
- 树内共 275 个文件，即同步导出的 274 个，加上同步程序自留的 scripts/gh_sync.py（SYNC.md 第 6 节允许它保留）。
- turns 只有 001–100。replies 有 97 份，缺 050、052、054，与台账一致：W41 记第 50 轮被拦，W42 记第 52、54 轮未落地。
- main 现在已前进到 79dd38a。那是第 104 轮的同步，只改了 STATUS.md 的一行：“第 103 轮 · 空闲” 改为 “第 104 轮 · 发送（SEND）”。这与“本轮由驱动器正式发送”一致，但不能代替 ref 核对。这个提交没有用于核验。

| 文件 | 字节 | SHA256（★为全长，其余为前 16 位） |
|---|---|---|
| STATUS.md | 1007 | 7cf0ae0fd3e3d79e |
| project/PROJECT.md | 89020 | ★8ec7620375584106b6290dd2547a69d115de0e281bc26657988ead32716ba850（与执行端一致） |
| project/VISION.md | 6479 | ec4636b5652a20f4 |
| project/CHANGELOG.md | 13322 | e82a1be534765eae |
| project/ISOLATION.md | 13871 | c43a9ed10900cc0f |
| project/workflow/goals.json | 3808 | e1c1b7884ec83dc0 |
| project/workflow/policy.json | 1954 | f40bf52ea82e13d8 |
| tool/VERSION | 6 | ★406b9efb67a070bc32c23c3ac6c886a8d25cfac10b1861ef92e4166708aa6ce1（内容为 1.7.6，一致） |
| tool/docs/WORKFLOW.md | 11145 | ★03b908f9ace6a713612464166f8c1448ad5d46739034b2fda6d4b6c24bbe88e8（一致） |
| tool/docs/DRIVER.md | 48185 | 5613f51a30ac162c |
| tool/docs/SYNC.md | 4365 | 1bf2bbcf08af3433 |
| tool/docs/CONTEXT.md | 4579 | 023b7caf16cc6fb2 |
| tool/docs/PROCESS.md | 8654 | 66003673dd94cfe2 |
| tool/docs/REVIEW.md | 3099 | 2de6e6e50e047cb5 |
| tool/docs/TRANSPORT.md | 13222 | 43a72f24b546e1a6 |
| replies/reply-100.md | 3838 | 36013b12499a5971 |
| turns/turn-100.md | 17639 | 7d09cd2b79d28012 |
| turns/turn-103.md | — | 404，远端树中不存在 |
| artifacts/collab-wf-1.7.6-ca8c2da.zip | 353578 | ★410760212d0cde2d5c1ff9ccecec1b47184ff275be2fd7bac3a3a4e091e0396e（一致） |
| artifacts/collab-wf-dev-v3.2.zip | 102750 | ★c0363142b1c9f5c12e1883d5a6c8a01079094afcc9636ea53122ec527b4ade94（你未给期望值） |

源码（SHA256 前 16 位）：

| 文件 | SHA256 前 16 位 |
|---|---|
| wf.py | 542b65fbb727e6d4 |
| admin.py | dca27da8c9a3f08f |
| sync.py | 62f40570cbe7ea25 |
| driver.py | b7cdc2a86b65d227 |
| goals.py | 6ba4dc4d458d0a30 |
| notify.py | 7143cf62c013480f |
| machine.py | 30983edcdf38598a |
| ops.py | 35daf20ec26035e1 |
| release.py | 7b4e94f9ac3a1ad3 |

**包内核对：**
- 工具包：完整性测试通过，包注释是工具提交 ca8c2dac…。63 个文件逐个与 MANIFEST.json 中的 sha256 相符，也与仓库 tool/ 目录逐字节相同。
- 项目包：只有 PROJECT.md 和 CHANGELOG.md 两个文件，与 project/ 下的同名文件相同。它是交付包，不是项目全量，所以指纹不按它算。

**我核不了的内容：**
- 项目提交 3b87d22：镜像里没有项目的 git 历史，只有工具生成的 STATUS.md 这样写。
- 1.7.6 的“构建 249/249”、安装前审查 timeout、事后补审 pass：镜像里没有这些原文。
- 适配器修复和 10 项离线回归：按边界不审读。
- 对话 id、ref、本回复的结束原因：我在对话里看不到。

**2. 复述**

依据：VISION v1.4 第〇、一节；WORKFLOW 第 1、12 节；PROJECT.md 的“当前版本”和“下一步”；你本条消息的第 1–4 节。

- **定位**：collab-wf 是你在自己的 macOS 上做研究型开发用的协作工作流工具。
  - 公开仓库 fashionladymall-maker/collab-wf 是上下文载体。公开不等于产品化，也不去个人化。
  - 只维护 collab-wf，同时保留多项目能力。
  - Amy 在 collab-wf 完备前不开发，之后只作全链路验收案例。
- **真实环境**：
  - 运行在你的 macOS 上。collab-wf-dev 有自己的 launchd 定时任务，间隔 60 秒。Python 用 3.13.12 的绝对路径。
  - 工具目录是 ~/.workbuddy-ai/tools/collab-wf，项目目录是 ~/.workbuddy-ai/projects/collab-wf-dev。
  - 网页对话通道和它的适配器在工具目录外，由执行端维护。
- **角色**：
  - Claude 是大脑：负责工具设计、全部工具代码、PROJECT 与 CHANGELOG，以及作为项目版本交付的 goals 与 policy。
  - 执行端（WorkBuddy）是手脚：负责环境事实、适配器、同步与审查程序、唤醒、落地、另一模型系列的独立审查、测试和证据。
  - 驱动器只推进例行步骤，不写代码。
  - 你只决定资金、密钥、合规、平台与通道，以及最终验收。专业检查不依赖你提醒。
- **版本**：
  - 工具实际为 1.7.6。PROJECT.md“当前版本”的第 4、6 行仍写 1.7.5，下一个项目版本修正。修正时审查结论如实写成“安装前 timeout，事后补审 pass”，不能写成安装前通过。
  - 项目为 v3.2，指纹 278a0f3bc21d1f27。
- **阶段**：
  - 旧 M1 共 7 项：G1、G2、G3a–d、G4。第 71 轮达成，第 98 轮由用户授权执行端代验。
  - G3 按历史最长连续段计，只增不减，所以看板会永远停在 7/7。
  - M2 当时只定了方向（把 I1–I8 写成判据），一直没有交付。
  - 新阶段就是你本次列出的 P0-A…P1-N，加上 tell 补充的内容。目前全部为 open，没有任何一项 DONE。
- **旧轮回为何失效**：
  - 仓库能证实两点：远端没有 turn-101 到 turn-104；bootstrap-short 引用的 turn-103 返回 404。
  - NO_REF 循环在代码层可以复现（见第 4 节第 4 条）。
  - 其余细节目前只能采用你的陈述，等执行端 2 补原文。
- **边界**：
  - 本轮只读取、复述、核验、规划。
  - 不交付代码和版本，不另建 SQLite 版工作流，不审读适配器源码。
  - 不恢复 Amy，也不删它的备份。
  - 本窗口不改其他窗口的全局项目。
  - 底线事项只由你本人决定。

**3. 指纹复算（两种口径，不能混用）**

- **项目指纹**（ops.py 中的 _fp_files 和 fingerprint）：
  - 取文件范围：已跟踪的文件（没有 .git 时遍历目录）；排除 FP_EXCLUDE 列出的路径（.git/、.env、data/、state/、logs/、workflow/evidence/、workflow/outbox/、workflow/*.jsonl、approvals.json、goals_state.json 等）和 *.pyc。
  - 计算方法：每个文件写一行“sha256 两个空格 路径”，按路径排序后拼接，再取整体 sha256 的前 16 位。
  - 复算结果：我先导入工具自己的、未改动的函数算了一次，又独立重写算了一次，两次都是 **278a0f3bc21d1f27**，共 10 个文件。
  - 这 10 个文件是：.gitignore、CHANGELOG.md、ISOLATION.md、PROJECT.md、VISION.md、papertrade/make_r5.py、papertrade/pt5_regress.js、workflow/goals.json、workflow/m1_check.py、workflow/policy.json。
- **同步摘要**（sync.py 中的 digest）：
  - 计算方法：文件按路径排序，依次累加“路径、\0、文件 sha256”，取前 16 位。
  - 范围：collect() 收集的 274 个文件，即 project 10、tool 63、已发出的 turns 100、replies 97、artifacts 2，加 STATUS 和 README，共 3,034,126 字节。
  - 复算结果：在固定提交上为 **4c31d7433c926c59**，与你给的值一致。
  - tell 中 14:58 那次 dry-run 的 ff5232c5fa0c56c9（3,034,139 字节）是更早的一份内容，与本提交差 13 字节，不对应这个提交。
- **“unchanged”的含义**：它只是和本机记录的“上次推送摘要”比较，并没有读回远端。这一次远端复算确实一致，但一般情况下它不能当作远端证据（见第 4 节第 3 条）。

**4. 读代码确认的事实（根因证据，均以 ec6b3c0 为准）**

1. **交接文档过时、互相矛盾。** WORKFLOW.md 第 10 节只写了 ZIP 长消息路线，末段还写“不引入远程仓库”，与 SYNC.md 第 7 节的 GitHub 短消息路线矛盾。PROCESS.md 第 9 节、DESIGN.md 第 4 节也同样过时。
2. **手动生成的短消息不可靠（turn-103 的 404 属于这一类）。** admin.py 的 _bootstrap_short 有三个问题：
   - 同步抛出异常时直接吞掉，也不区分同步结果是 blocked 还是 failed，照样生成短消息。
   - 链接用的是分支 main，不是提交号，内容可能漂移。
   - “最近一轮”的链接直接取本机已发出的最大轮次，不检查这一轮是否真的推送上去了。

   驱动器自动开新对话时会检查同步状态必须是 pushed 或 unchanged；手动 wf bootstrap 这条路不检查。
3. **“pushed”和“unchanged”可能是假的。**
   - 远端的 gh_sync.py 把 OWNER、REPO、BRANCH 写死，没有 --repo 参数。
   - 拿不到锁时它直接 return 0，也不输出 commit。
   - 而 sync.run 只要看到退出码 0，就记为 pushed 并更新 pushed_digest，之后同样内容都会报 unchanged。
4. **NO_REF 循环。**
   - h_wait_reply 发现没有 send_ref 就报 NO_REF。它的处置是 await_user，没有次数上限。
   - wf approve 会把 consecutive_failed 和 rounds_no_progress 清零，然后回到 WAIT_REPLY；但 ref 仍然为空，所以下一次 tick 又是 NO_REF。
   - 也就是说，批准本身把“无进展”的计数抹掉了。
5. **待办没有“取消”状态。**
   - tasks.py 里待办只有“未完成”和“done”两种，没有 cancelled 或 superseded，所以取消一项待办只能标成 done。
   - 另外，ec6b3c0 的 STATUS 写着“待办：无”，而执行端说 T100-1 还没验收。这意味着 T100 的待办要么已被标成 done，要么根本没有登记，由执行端 5 查明。
6. **目标没有阶段概念，门禁为空。** goals.json 只有 M1，goals.py 里没有阶段概念；G3 只增不减；policy.json 的 gates 为空。
7. **本轮的指纹键不会被机器核对。** 【驱动指令】中的“指纹”只在落地版本包时核对（没有版本包时，h_apply 直接返回）。本轮发布为无，所以这个值只是声明，由执行端 1 手动核对。
8. **工具分不清回复是怎么结束的。** TRANSPORT.md 中 poll 的返回结果不包含回复的结束原因，所以工具无法区分正常结束、被截断和因工具上限而结束。
9. **升级后可能漏投。** notify.py 在 2 小时后把 for 从 executor 改成 user。这一个字段同时管“谁来决定”和“发给谁”，执行端如果按 for 过滤，这一项就会漏投。
10. **很多旧缺陷只靠约定防住。** 台账里有不少条目只靠约定就标了“已规避”，例如 W24、W29、W31、W40、W45、W54。PROCESS.md 第 10 节也自认“本版写成约定”。这些条目需要重新评级。

**5. 覆盖表**

下表全部是待做事项，不表示已实现。owner 列写“主”为主责，“配”为配合。

| 需求 | 影响（现状） | owner | 依赖 | 拟验收 | 缺材料 |
|---|---|---|---|---|---|
| P0-A 正式交接 | 新对话首轮可能读不到或读错上下文（第 4 节第 1、2 条） | 主 Claude（路线、生成器、核验清单）；配执行端（发送、绑定、回核） | P0-H、P0-E | 分三条路线：G 走 GitHub 短消息，前提是推送带 commit、链接钉提交号；Z 走长消息加 ZIP，通道不能上传时按清单点名、执行端回传原文；D 为受控降级，执行端有界分片回传原文。任何路线都不准只发摘要或本机路径。发送前门与开发前门分开（第 6 节）。负向测试：未推送或链接不在清单内时不出短消息。实景：一次完整的新对话交接 | short.md 全文、同步记录、第 101–104 轮记录（执行端 2、3） |
| P0-B 动态目标 | 看板永远 7/7、待办为无；取消只能标 done（第 4 节第 5、6 条） | 主 Claude；配执行端、用户批准 | 无（首批） | 账本字段：id、phase、supersedes、scope、owner、acceptance、evidence、status、blocked_by、next_action、需求 ID。旧阶段只读。缺配置不准 READY，缺证据不准 DONE。支持 cancelled 与 superseded。看板和 STATUS 显示当前阶段。目标变更有审计。目标达成后不自动开新任务 | 批准方式（需要用户决定第 1 项） |
| P0-C 工程治理 | gates 为空；角色只写在文档里；异常处置走两个极端 | 主 Claude；配执行端（审查、测试） | P0-B、P1-M | 每个角色有准入与退出判据，并能机器检查。新项目默认带门禁基线。强门禁只用于凭据安全、权限、破坏性操作、错误项目、未验证安装，放在 policy 层，不改 floor.py。可恢复的等待用有界退避加检查点。两类情况各有负向测试 | pstack 研究（执行端 4） |
| P0-D 经验闭环 | 很多条目靠约定标为“已规避”（第 4 节第 10 条） | 主 Claude（账本 schema、检查器）；配执行端（证据、复现） | P0-B | 每条缺陷必须走完：证据、根因或假设、临时处置、永久修复、回归测试、负向测试、版本与回滚、实景验收。缺任一项，工具拒绝标 resolved。W 与 N 系列全量重新评级。新缺陷从 W58 编号。规则自动用于下一个项目 | N 系列完整清单；本机 W57 同号条目的映射 |
| P0-E 手动/ref/轮次 | NO_REF 与批准互相循环；tell 和 mark sent 的语义被误解（第 4 节第 4 条） | 主 Claude；配执行端 | 无（首批） | 没有 ref 不进 WAIT_REPLY，保留整轮手动收尾。没有解决动作的批准一律拒绝。同一原因第 2 次出现就判为无进展。tell 显示排队、已附、已发三种状态。防止旧 tick 与手工操作并发时互相覆盖写回。重放第 101–103 轮作为回归 | 第 101–103 轮事件（执行端 2） |
| P0-F 不明/长生成/上限 | 工具分不清截断与工具上限（第 4 节第 8 条）；第 100 轮出现重复送达 | 主 Claude；配执行端（适配器提供中立的结束原因和结构化的成功证据） | P0-E | 发送结果分 5 类，回复完整性分 4 类，各有状态与处置。不凭页面静止或 is_pending=false 重发。因工具上限结束的回复：保存全文，给出 continue 与交付清单，只在上下文已核验时续接。重复送达去重。换对话或换账号后，旧意图转为 superseded。黑盒测试：中间页或 HTML 不算成功 | 本回复与第 100 轮两条回复的结束原因原文 |
| P0-G S4 仲裁 | 只有设计（ISOLATION.md 第 5 节）；300 秒无凭据的因果尚未证实 | 主 Claude；配执行端（受控实测） | P0-F；第二个测试项目（不用 Amy） | 账号级原子预留，状态为 wait、grant、release、unknown。FIFO 排队，设等待上限，租约过期回收。限速与额度统一处理。全程写入 arbiter.jsonl。受控回归能复现或排除“互饿” | 重叠时段的日志原文；测试项目安排 |
| P0-H 同步仓库 | busy 时返回 0；pushed 和 unchanged 可能是假的；仓库写死（第 4 节第 3 条） | 主执行端（gh_sync）；配 Claude（成功语义、白名单）、用户（公开范围） | 与 P0-A 并行 | 忙或失败一律非 0。成功必须带 commit，并可选读回远端核对摘要。支持 --repo，cache、lock、log 按项目、目标、分支隔离。同一仓库的多个项目不互删。安全删除拦截只能经有记录的决定放行。密钥扫描覆盖新增内容。每项都有负向测试 | 本机 gh_sync 哈希与拦截原文（执行端 3）；公开范围（需要用户决定第 2 项） |
| P1-I 生命周期 | “识别项目”或“白名单已批准”被当成配置完成 | 主 Claude；配执行端 | P0-B、P0-C | 从 init 到 archive 各阶段有状态。一条命令逐项核对：git、版本固定、policy 与 goals、hook、usage、绑定、调度、观察者、证据与回滚、独立仓库。缺项不准 READY | 无 |
| P1-J inbox/唤醒 | for 被改写导致漏投（第 4 节第 9 条）；存在竞态窗口，但尚未证实 | 主 Claude；配执行端（唤醒程序） | P0-E | decision_owner 与 delivery_owner 分开。锁内重读，读失败可见。按 project、key、request_id 寻址，没有专属会话时不串投到别的项目。pin、fallback、wake 锁与告警标签都改正。配并发测试 | 唤醒过滤条件的中立描述 |
| P1-K 看门狗 | native 错误在复检后消失；探针异常被当成对话故障 | 主 Claude；配执行端 | P1-J | 按项目检查 launchd、日志、心跳。按（项目，检查项）去重，并持久化状态。错误要确认后才清除。看门狗本身有独立观察者。探针失败不触发新建对话或换号（负向测试） | 一次“复检后消失”的原文 |
| P1-L 发布升级 | 事后补审容易被当成安装前已审查 | 主 Claude（release.py）；配执行端 | P0-C | self_update 全局互斥。unreviewed 如实记录，不能改写成安装前 pass；补审另行记录。版本包 sha、diff、项目、请求互相绑定。等待有界、可恢复。做回滚演练 | 1.7.6 的安装与补审原文 |
| P1-M pstack | DESIGN.md 第 4 节只有仓库名和条目数，没有提交号、取证日期、文件哈希 | 主执行端研究，之后由 Claude 融合 | 无 | 来源钉住，原文摘录，对照表逐条标注状态 | 全部材料（执行端 4） |
| P1-N 范围/幂等 | 本窗口的操作改动了全局（Amy）；待办消失或进入 IDLE 被当成完成 | 主 Claude（范围、删除门禁）；配执行端；用户授权 | P0-B、P0-C | 每个动作带 scope。跨项目或全局的破坏性操作走强门禁，并需用户原话授权。取消一律走 cancelled 或 superseded。“完成”只认验收证据。共享浏览器同一时间只允许一个写操作方。Amy 的偏离写入台账，不算业务验收通过 | Amy 时间线（执行端 5） |

**tell ①–⑩ 的归属：**

| tell | 归属 |
|---|---|
| ① | P0-A |
| ② | P0-H、P0-A |
| ③ | P0-E |
| ④ | P0-B |
| ⑤ | P0-F |
| ⑥ | P0-F 的适配器黑盒验收 |
| ⑦ | P0-F、P0-E |
| ⑧ | P0-B、P1-N |
| ⑨ | P0-B、P1-I |
| ⑩ | P0-D |

tell 要求“设计、编码、验收并提交完整方案”。按本轮规则，我把它拆开处理：本轮先给覆盖表和增量计划；代码、测试和升级包等交接闭合后再交付；证据没齐之前，不宣称专业基线已完成。

**6. 下一版最小增量与 M2 目标的交付要求**

总原则：在 1.7.6 的基础上只加不删；floor.py 和四道底线不改；不新建存储；不碰适配器内部。

**增量 1：工具 1.8.0，主题是“交接与目标的硬门”。**

- **E1（对应 P0-E）**
  - 没有 ref 的发送只能走整轮手动收尾，不能进入 WAIT_REPLY。
  - NO_REF 只能用两种方式解除：补登记 ref，或执行 mark replied。不带这两种动作的批准一律拒绝。
  - 由异常引起的等待，批准后不清零无进展计数；同一原因第 2 次出现，就转入 BLOCKED。
  - tell 显示三种状态：排队、已附、已发。
  - 回归测试：重放第 101–103 轮。
- **B1（对应 P0-B）**
  - goals 增加 phase、supersedes、history 三个字段，并兼容旧格式：旧的 goals.json 视为阶段 M1。
  - STATUS、看板和 wf goals 都显示“当前阶段：完成数/总数”，旧阶段放在“历史”下。
  - 待办增加 cancelled 和 superseded 两种状态，都必须写原因；done 必须写结果。
  - 目标或阶段发生变更时写审计事件。
- **H1（对应 P0-H 和 P0-A 的工具侧）**
  - 只有带 commit 的成功才算 pushed。退出码为 0 但没有 commit 的，记为 unconfirmed，不更新推送摘要。
  - 短消息只在 pushed 时生成：链接钉在提交号上，只列出已推送的文件，并附上 commit、同步摘要、项目指纹和必备字段。
  - 不满足条件时只发长消息，并写明为什么降级。
  - 交接轮的回复里如果出现发布或非只读命令，intake 拒收，请 Claude 重答，最多 1 次。
- **通用要求**
  - 每一项都配正向和负向自检；原有的 249 项一项不删。
  - 用构建器交付，安装前由执行端审查。
  - 审查超时就如实记为“未审查”，不写成通过。

**增量 2：项目 v3.3，只改文档和数据，不改行为。**

- PROJECT.md：
  - 工具版本改为 1.7.6，审查结论如实写。
  - 阶段改为“M1 历史；M2 定义中”。
  - 台账从 W58 开始新增以下条目，状态只标 open 或 mitigated：tell 中的 ①–⑨、Amy 的偏离、turn-103 的 404、链接没有钉提交号、busy 时返回 0。
  - 新增需求账本，覆盖 P0-A…P1-N，每条记录：来源原话、owner、依赖、验收方式、证据、状态。

**M2 目标的交付要求：**
- 在 1.8.0 装好之后，用新格式交付。
- 每个目标都要引用需求 ID，并写明证据类型：机器判据、负向测试或真实运行态。
- 检查只用只读命令；如果需要把新命令加入白名单，按“扩大权限”走审批。
- M1 冻结为历史，不计入当前进度；目标未批准时，看板显示“未生效”。
- M2 的完成标准：P0-A 到 P0-H 每一项的机器判据和负向测试都通过，并且各有一次真实运行态的证据。

**两道门的划分：**
- **发送前门**：只用机器检查交接包是否完整，包括 commit、摘要、指纹、必备字段，以及链接是否都在已推送清单里。这道门不需要 Claude 参与，所以交接消息总能发出去。
- **开发前门**：由 Claude 首轮核验，加上执行端回核来闭合。这道门没闭合之前，只能做复述、核验和计划。

这样划分，既满足 tell 中“未核验禁止 SEND”的要求，也满足 P0-A“门禁不能做成死循环”的要求。

**推进顺序：**
1. 执行端 1–5 完成，交接闭合。
2. 交付并安装 1.8.0。
3. 交付 v3.3。
4. 批准 M2 目标。
5. 做 P0-C、P0-D、P0-F。
6. 做 P0-G。
7. 做 P1 各项。

**7. 本回复的读取完整性**

本回复读取时用到了工具调用上限，但必读文件、源码和两个版本包都在上限之前读完并核对过。以下内容只读了一部分，我没有据此下任何结论：
- PROJECT.md 的“已确认结论”：只读了相关的几条。
- TRANSPORT.md：只读了 poll 一节。
- ISOLATION.md：没读第 3、4、6 节。
- turn-100.md：没读后半部分。
- release.py 和 watchdog.py：只做了定位检索。

下一轮需要时我会点名读取。这件事本身就是 P0-F 中“因工具上限结束，要明确交付清单”的一个实例。

【本机执行】
无

【本轮回报清单】
1. 执行端 1 的回核结果：五个 sha256，wf version、wf fingerprint、wf goals 的原文，项目 HEAD，逐项写一致或不一致；第 2 节复述逐条标注。
2. 执行端 2 的身份绑定：对话 id，第 104 轮的 ref 与 reply_parent，本回复的结束原因，第 101–104 轮的记录与相关事件。
3. 执行端 3 的同步证据：同步记录、两次推送的日志、拦截与放行的原文、short 消息全文、本机 gh_sync 的哈希。
4. 执行端 4 的 pstack 来源与对照表。
5. 执行端 5 的 Amy 偏离时间线，以及 T100-1、T100-2、T100-3 的状态。

【需要用户决定】
1. M2 目标账本交付后，由你本人批准，还是授权执行端代批（以 --by executor 执行，如实记为执行端代决）。不阻塞本轮。
2. 是否允许把目标账本、失败与经验账本、交接核验记录同步到公开仓库（命令原文和证据目录仍然不公开）。不阻塞本轮；扩展 P0-H 的同步白名单之前需要你决定。

【下一轮建议档位】
max

【驱动指令】
发布：无
执行端 1：只读回核本回复第 1、3 节：在本机对 tool/VERSION、项目 PROJECT.md、tool/docs/WORKFLOW.md 和两个版本包计算 sha256；运行 wf version、wf fingerprint、wf goals；确认项目 HEAD 是否仍为 3b87d22（tag v3.2）。再对第 2 节的复述逐条标“无误”或“有误”，有误的附原文。不改任何文件。结果写进 wf task done
执行端 2：核对发送身份，只引用已发生的事：wf conv 的原文，确认 collab-wf-dev 当前绑定的对话是否为 2af8fbfd…；第 104 轮发送的 ref 与本回复的 reply_parent 是否一致；本回复的结束原因原文。另列出第 101–104 轮在 turns.jsonl 中的 turn、status、ref、对话 id 字段，以及 wf audit 里相关的批准与状态转换事件，不含正文。结果写进 wf task done
执行端 3：补交同步证据原文，不写凭据和接口细节：local/sync/collab-wf-dev.json 中的 last、pushed_digest、failures；推送 ec6b3c0 和 79dd38a 两次的同步日志行，以及各自是 tick 触发还是手工触发；14:58 那次 dry-run（ff5232c5fa0c56c9）和安全删除拦截的原文，以及后来是如何放行的；bootstrap-20261012-232240-short.md 全文；本机 gh_sync 的 sha256，以及它是否与远端 scripts/ 下的版本相同。结果写进 wf task done
执行端 4：补研究 pstack，只研究、不改代码：给出实际使用的仓库地址、提交号、取证时间和关键文件的 sha256；摘录角色分工、准入与退出、审查与合并授权、平台期与熔断相关原文的要点，包括 DESIGN.md 第 4 节提到的 autonomous-run、autopilot-full、shipping、07-overnight；对照 DESIGN.md 第 4 节和 PROCESS.md 第 10 节，逐条标注“已核实”“已过时”或“未找到”。结果写进 wf task done
执行端 5：整理 Amy 偏离的事实时间线，不恢复、不删备份：每个动作的时间、执行者、所依据的用户原话原文（以 wf said 的记录为准）、影响范围（目录、注册与映射、定时任务、信号、待办）；备份的位置和可恢复性，只核对是否存在及大小；以及 T100-1、T100-2、T100-3 的当前状态。结果写进 wf task done
执行端 6：执行端 1–5 都做完后，先用 wf note --kind info 写明：T100-2 已被用户边界取代，没有标 done，等 cancelled 或 superseded 状态上线后再正式关闭。然后用 wf kick --kind report 发回报，不必等旧待办超时
之后：等执行端
指纹：278a0f3bc21d1f27