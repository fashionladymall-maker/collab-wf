[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 create_file]
[工具 create_file]
[工具 create_file]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 present_files]
【本轮结论】

补丁和回归测试已交付，三个文件都在 outputs/watchdog-inbox-fix/ 下：

| 文件 | 字节 | SHA256 |
|---|---|---|
| apply_watchdog_inbox_patch.py（补丁程序） | 11385 | 1496d8fe24569c4b86e7d7733ea6353b62c57f8e07fc8c05eaafe320ed303f86 |
| test_watchdog_inbox.py（回归测试，只用标准库） | 17655 | 026dab8394bf411a6ce584bd9aec01a1c3c35e3dc7a89865a28ff98b41741c7a |
| watchdog_inbox_review.diff（只用于审阅） | 7789 | ff91bbd270f106d69dee687dc4cc5c3f1dedcc9c2cd2c823e5a9177070b582c1 |

**根因**

问题有两处：
1. check_project 把 inbox 里的所有条目都算作积压，包括工具在 DONE 时写入的“目标已达成”提示。这条提示由工具的 asks.notices 和 notify.collect_inbox 生成，结构固定：key 为 `<项目>|done:<轮次>`，project 为项目名，for=user，action 和 code 都为空。
2. 每个项目各读一遍全局 inbox，项目一多就重复计数。

asks 的 mark-shown 只影响通知是否呈现，不会把提示移出 inbox。所以能做的只有在看门狗里把条目分对类，而不是去清 inbox、标已读或伪造确认。

**修改内容（只改三处，其余检查一字未动）**

1. **check_project：** 删掉末尾读全局 inbox 的那一段。inflight、poll-late、send-unknown、fail-streak、no-progress、stuck-needs-human、state-unreadable 这些检查原样保留。
2. **新增函数：** 在 check_locks 之前新增 `inbox_item_is_normal_notice()`、`check_inbox(projects)` 和两个小工具函数。
3. **collect：** 在 check_locks 之前调用一次 `check_inbox(projects)`。check_jobs、check_tick_log、逐项目的 check_project、check_locks 的调用顺序不变。

**分类规则**

只有两类条目被排除在积压之外。判断依据是条目结构，再与该项目 state.json 的当前状态交叉核对，不看中文标题。

- **正常完成提示，排除的条件要同时满足：**
  - key 恰为 `<project>|done:<N>`，前缀与 project 字段一致；
  - for=user；
  - action、code 都为空，且没有 escalated 标记；
  - 该项目 state 为 DONE，且 turn 等于 N。

  其中任何一条不符，例如带了 action 或 code、状态不是 DONE、轮次对不上、项目对不上、state 读不出，这条都照常算作待办。
- **普通额度等待，排除的条件要同时满足：**
  - key 恰为 `<project>|quota:<until>`；
  - for=executor，action、code 都为空，且没有 escalated 标记；
  - 该项目 state 为 QUOTA_WAIT，且 state.until 与 key 里的 until 相同；
  - 现在还没超过 until 加 15 分钟余量。

  到点后迟迟没有恢复，就不再当作正常，照常告警。其他与额度相关的故障不走这条提示，不受影响。

下面这些照常可见：
- 真实的批准项、阻塞项、执行端待办、外部信号（无论有没有 action 或 code）、底线项、暂停、已升级的条目，以及无项目归属的信号。
- 读不懂的条目，例如不是对象，或 since 无法解析，单独报 `inbox-malformed`（high）。

**去重方式**

全局 inbox 每次巡检只读一次，每个条目只进入一个桶：
- 有 project 字段的，归入该项目；
- 没有 project 字段但 key 前缀是已注册项目的，归入该项目；
- 两者都没有的，归入全局桶（显示为 `inbox-backlog:-`），只算一次。

每个桶最多出一条告警，告警里写明件数、按处理人分的计数和最久的那件的 key。

**需要留意的行为变化**

- 告警的 key 从 `inbox-backlog` 变成了 `inbox-backlog:<项目>` 和 `inbox-backlog:-`，另外新增 `inbox-malformed`。我没有看到投递和 suppress_native_duplicates 的实现，如果那里按 `inbox-backlog` 的字面值匹配，应用补丁前需要在本机检索确认。
- 在你开新任务、状态刚离开 DONE、而 inbox 还没被下一次 tick 重写的那不到 60 秒里，旧的 done 提示会被当作待办。这是有意的“宁可多报”，最多出现一次短暂告警，下一次 tick 后自动消失。

**补丁程序的安全设计**

- 先核对原文件的 SHA256，默认绑定 f3eabf7785dedf1e62d324a109987d73de7eac56bfeb35f352760e184c21c13c，不符就停下，不写任何东西。
- 两处锚点文本必须各出现恰好一次，否则停下；文件是 CRLF 换行，或者已经打过补丁，也会停下。
- 默认只预演；加 `--write` 才会写入。写入时先备份为 `watchdog.py.bak-<原 SHA 前 12 位>`，再原子替换，并做语法自检，不在目标目录留下 __pycache__。
- 全程不读写 inbox 或 state，不发通知，不运行 watchdog。

**容器检查结果（只是 Claude 自测，不是本机结果）**

我手上只有你贴出的这几个函数，所以在容器里拼了一个测试替身：头部的常量和 read_json、now 按你的描述补齐，函数体逐字取自你贴出的源码。它的 SHA256 是 c8a005c0…d754，和本机文件不同；测试时用 `--expect-sha` 覆盖了默认绑定。在这个替身上的结果：
- 预演时两处锚点都命中；写入后 SHA256 为 e74906c1…77b3；对已打过补丁的文件再运行会被拒绝。
- 补丁后的替身上，Python 3.12.3 运行 36 个测试全部通过，包括按 09:07:07 写入、09:40 巡检复现的 done:74 实况。
- 同一套测试在未打补丁的替身上运行：3 个失败、29 个错误，说明这些测试确实能抓住旧行为。

测试覆盖的情形：
- done 提示超过 30 分钟不报；
- 真实的用户批准超过 30 分钟要报；
- 执行端待办要报；
- 没有 action 和 code 的外部信号要报；
- 多个项目时各自只算一次，check_project 不再读 inbox，collect 只读一次 inbox；
- 无项目归属的信号可见，而且只报一次；
- 带 action 或 code、已升级、状态不是 DONE、轮次对不上、项目前缀对不上、state 读不出的 done 提示，都不会被忽略；
- 标题不参与分类；
- 额度等待的正常、超时、状态不符、until 不一致、带 action 五种情况；
- 结构畸形的条目和 inbox 文件；
- check_jobs、check_tick_log 和 check_project 原有的告警保持不变。

所有外部依赖都做了模拟，包括读 JSON、当前时间、launchctl、tick.log 的修改时间。

**待本机确认的边界**

- 本机文件的锚点是否与你贴出的源码逐字一致。不一致时补丁程序会安全停下，这时可以对照 watchdog_inbox_review.diff 手工应用。
- 本机常量（INBOX_BACKLOG_S 等）是什么值。测试按模块里的常量计算阈值，不写死数字。
- 投递和压重逻辑里有没有引用 `inbox-backlog` 的字面值。
- 本机运行 `WATCHDOG_PY=<本机 watchdog.py> python3 -m unittest -v test_watchdog_inbox.py` 的结果，以及修复后 `collect()` 在 done:74 实况下是否不再报积压。

本轮没有改动 goals、policy、floor、项目版本和教程；r3 教程仍待执行端本机验收，与 M1 的 7/7 是两件不相干的事。

【本机执行】
```
wf goals
wf fingerprint
```

【本轮回报清单】
1. `wf goals` 原文，应为 7/7。
2. `wf fingerprint` 原文，应仍为 b848fba7f3776a69。

【需要用户决定】
1. M1 最终验收：驱动器停在“目标达成”，确认只认你本人的原话。本项不阻塞本轮。
2. 执行端自查发现执行端目录下有明文凭据的候选文件，其中一个是权限过宽的旧备份。是删除、收紧权限还是保留，由你决定。执行端没有动它们。本项不阻塞本轮。

【下一轮建议档位】
medium

【驱动指令】
发布：无
之后：空闲
验收 1：c1 exit=0 包含 "目标进度 7 / 7"
验收 2：c2 exit=0 包含 "b848fba7f3776a69"
指纹：b848fba7f3776a69