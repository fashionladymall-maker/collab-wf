# ISOLATION.md —— 多项目并行与全链路隔离（设计 v1）

collab-wf-dev 第 89 轮（v2.8）起草，第 91 轮（v2.9）更新：S1 已实现并迁移。需求来源：执行端第 87–88 轮转述的用户原话与需求包乙（R1–R5、本机实测 15 项、判据 I1–I8，第 88 轮回报全文）。本文件是设计与分阶段计划；代码随各阶段的工具版本交付，验收以 I1–I8 的本机实测为准。

## 1. 原则

1. 一套工具，显式的项目上下文。每一次跨进程调用（通道适配器、审查程序、同步程序、唤醒）都带上项目名与该项目自己的对话；共享文件里的每条记录都带 project 字段。不保留“当前默认项目”“默认对话”这类隐式的全局状态；旧字段只在迁移期兼容读取。
2. 资源分三类处理：
   - 项目私有：按项目分区，或者本来就在项目目录里。
   - 全局但按项目寻址：一个文件里多条记录，每条带 project，读取时按项目过滤。
   - 物理共用：一个通道账号、一个 Claude 账号的额度、这台机器。这类隔离不掉，只能仲裁：排队、留痕、看得见谁在用、下一个轮到谁。
3. 分工不变（PROJECT.md 结论 30a）：选会话、建对话、唤醒会话、审查程序由执行端实现；Claude 只定接口与黑盒判据；工具负责把项目上下文传对、把记录分开。
4. 不改 wflib/floor.py，不放宽任何权限，不做多份工具副本，不改用户正在用的产品、通道与环境；只做“加”，旧行为在迁移期保留。
5. 分阶段：每一阶段可独立验收、可回滚。

## 2. 隔离模型（逐项对应需求包乙第二节的 15 项）

| # | 资源 | 现状 | 设计：持有者 · 位置 · 怎样按项目寻址 | 阶段 |
|---|---|---|---|---|
| 1 | 会话 | 适配器配置只有一个默认对话 | 执行端适配器 · 配置增加 projects 映射（第 3 节）· 工具每次 send、poll、snapshot 带 --conv（本项目的对话），适配器收到就只用它 | S1 |
| 2 | --project | 只用于日志 | 适配器选会话的顺序：--conv；没有时查 projects[--project]；都没有才用旧的默认对话，并在输出里告警（只在迁移期） | S1 |
| 3 | conv_new | 改写全局默认对话 | 工具调用时带 --project；适配器只写 projects[该项目]，不再改默认对话；返回的新对话由工具记进本项目状态 | S1 |
| 4 | transport.json | 单份 | 保持单份（通道与账号本来就共用）；可选 projects.<名> 覆盖模型与超时，只加不改 | S4 |
| 5 | 发送限速 | 全局发送记录计数 | 物理共用（同一账号）：保持全局计数，加发送仲裁（第 5 节） | S4 |
| 6 | 收件箱 | 一个 inbox.json 装所有项目 | 保留全局汇总（执行端只有一个）；每项已有 project；新增 local/projects/<名>/inbox.json 分项目视图；wf ask --project 只列本项目 | S2 |
| 7 | 信号 | 全局目录，文件名带项目 | 写入 local/signals/<名>/；读取兼容旧位置一个版本 | S2 |
| 8 | 审查请求 | 键是版本包名，跨项目同名会互相覆盖 | 工具多传 --key <项目>.t<轮次>.<包名去扩展名>（工具自身的版本用 collab-wf.tool.<包名去扩展名>）；审查程序的请求、diff、结论文件一律按这个键命名 | S2 |
| 9 | 调度 | 一个定时任务串行推进全部项目，一条长命令占满整次 tick | 每个项目一个定时任务：wf schedule plist --project <名> 生成标签 local.collab-wf.tick.<名>，运行 wf tick --project <名>；项目锁本来就按项目，同一项目不会被两个进程同时推进；全局定时任务保留给尚未迁移的项目 | S3 |
| 10 | 唤醒 | 单实例，投递到单个执行端会话 | 执行端：按 inbox 项的 project 找该项目登记的执行端会话投递；投递失败只影响该项目；工具提供 project 字段与分项目视图 | S2（执行端） |
| 11 | 看门狗 | 单实例，判定已按项目 | 保持单实例与判定；信号写入分项目目录 | S2 |
| 12 | 审查程序 | 写死了项目与目录 | 执行端：项目、轮次、键都从参数取；工具侧的 review_adapter.json 本来就写进本项目本轮的证据目录 | S2（执行端） |
| 13 | 注册表 | 单文件 | 保留 | — |
| 14 | 看板 | 单实例 | 保留（只读，已按项目展示）；S4 增加共用资源占用一栏 | S4 |
| 15 | 通道凭据 | 一份 | 物理共用，不动；是否多账号属于用户本人的凭据决定，不是本方案的前提 | — |

## 3. 会话绑定（S1）

适配器配置（执行端的文件；这里只定形状）：

```json
{
  "default_conversation": "<旧字段：迁移期只读兼容>",
  "projects": {
    "collab-wf-dev": {"conversation": "<对话 id>", "since": "<ISO 时刻>"},
    "amy-local": {"conversation": "<对话 id>", "since": "<ISO 时刻>"}
  }
}
```

工具侧（1.7.0）：

- 本项目状态的 conv.conv 记当前对话。驱动器自动开新对话时已经从适配器的返回值记下；手工换对话时用 `wf conv new --note … --conv <id>`。只登记、不换对话、不清计数时用新命令 `wf conv bind --conv <id> --note …`（迁移用）。
- 每次 send、poll、snapshot 都带 `--conv <id>`（有值时）；conv_new 带 `--project <名>`。只有 transport.json 声明 `"per_project_conv": true` 时才这样传：适配器升级之前不传，避免它把不认识的参数当错误。
- `wf transport test` 增加两项黑盒检查：给了 --conv 时动作日志里用的就是它；`conv_new --project A` 之后 projects 里其他项目的条目不变。
- 适配器（执行端）：选对话的完整顺序是 --conv > projects[--project] > --prev-ref 所属对话 > 默认对话；--prev-ref 只给定位下界，不决定对话（执行端第 90 轮意见，已写进工具 TRANSPORT.md 附录“1.7.0”）。退到默认对话时在输出 JSON 里加 `"warning": "conv_from_default"`，--prev-ref 所属对话与选定对话不同时加 `"warning": "conv_mismatch"`；工具把 warning 写进回报的“需要注意”。

迁移：

1. 执行端备份适配器配置，写入 projects.collab-wf-dev = 当前默认对话。
2. 装上 1.7.0 后，执行端运行一次 `wf conv bind --conv <当前对话> --note "迁移到按项目绑定"`（只登记，不换对话、不清计数、不动本轮的发送意图）。
3. amy-local：执行端用 conv_new --project amy-local 为它建自己的对话（或把它已有的对话写进 projects.amy-local）；I1 本机实测通过后，才把 amy-local 的 wf.json 改回 auto:true。

第 91 轮迁移结果（执行端回报）：适配器已按上面的顺序升级；projects.collab-wf-dev 已登记、默认对话未变；wf conv bind 已登记；per_project_conv 已打开，wf transport test 新增项通过；conv_new --project amy-local 新建了 amy-local 的对话，collab-wf-dev 的条目不变；amy-local 仍 auto:false。待闭合：回报里 conv_new 输出的对话 id 与配置里登记的不一致，需按原文核对；真实发送用本项目对话的核对放在第 92 轮。

回滚：适配器在没有 --conv、projects 里也没有条目时退回旧的默认对话；工具回到 1.6.9 后不再传 --conv，行为与现在相同；适配器配置用备份恢复；amy-local 回到 auto:false。

## 4. 本机目录（S2）

```
local/
  transport.json  registry.json  schedule.json  serve.*  notify.json   全局，单份（不变）
  send_log.jsonl  quota_hold.json                                     账号级记录（不变，记录带 project）
  shared/arbiter.jsonl  shared/send.lock                              共用资源的排队与占用（S4，新增）
  inbox.json                                                          全局汇总（不变，执行端读）
  projects/<名>/inbox.json                                            分项目视图（S2，新增）
  signals/<名>/<键>.json                                              分项目信号（S2；旧位置兼容读一个版本）
  reviews/<键>.*                                                      审查请求、diff 与结论按键命名（S2）
  sync/<名>.json  locks/<名>.*                                        已按项目（不变）
```

WF_LOCAL_DIR 保留：它让演练与自检整体用另一套 local/（wf drill、自检），不是多项目的隔离手段；多项目靠显式的项目名与分区路径。

## 5. 共用资源仲裁（S4）

- 发送槽：发送前在 local/shared/send.lock（文件锁，只在核对与登记的几毫秒内持有）里核对全局限速、额度等待与在途数。policy 的 shared_max_inflight（默认 1）限制同一账号同时在生成的对话数；同一账号多个对话同时生成能否被通道接受，由执行端实测后再调高。拿不到槽时写一条等待记录、按 retry_at 等待，不算失败。
- 次序：先到先得。最早一条还没拿到槽的等待记录优先；同一项等待超过 2 小时，照常写进 inbox（与现有的升级阈值相同）。
- 释放：本项目这一轮的回复落地（或发送被判定失败）时释放。
- 留痕（local/shared/arbiter.jsonl，每行一条）：

```json
{"ts": "...", "resource": "send", "project": "amy-local", "turn": 12, "event": "wait", "holder": "collab-wf-dev#90", "position": 1, "reason": "inflight 1/1"}
{"ts": "...", "resource": "send", "project": "amy-local", "turn": 12, "event": "grant", "waited_s": 840}
{"ts": "...", "resource": "send", "project": "collab-wf-dev", "turn": 90, "event": "release", "why": "reply_landed"}
```

- 额度：额度用尽本来就是账号级的，所有项目一起等；记录照写，reason 为 quota。
- 机器：每个项目的 tick 各自独立，不做负载仲裁；看板显示各项目 tick 的耗时。

## 6. 调度与唤醒（S3、S2）

- 每个项目一个定时任务（第 2 节第 9 项）。一个项目的长命令、长等待、阻塞只占用它自己的 tick，不拖慢别的项目；P0-C（单条命令被 tick 剩余预算截断）因此不再需要半途截断——截断 self-update 会留下装了一半的版本。
- `wf tick --project <名>` 只推进这一个项目；不带 --project 时行为不变。`wf schedule status` 按项目列出各自的定时任务与最近一次运行。
- 唤醒（执行端实现）：inbox 每项带 project；执行端按 project 找该项目登记的会话投递；没有会话时按 tick 节奏重试（P0-D），只影响该项目。

## 7. 判据与测试（I1–I8）

| 判据 | 工具自检（替身通道，两个项目） | 本机实测（执行端） |
|---|---|---|
| I1 A 换对话后 B 仍进自己的对话 | 两个项目的 send 参数里 --conv 各是各的；A 自动开新对话后 B 的对话记录不变 | A 运行 conv_new 后，适配器配置里 B 的条目不变；B 下一轮的动作日志用 B 的对话 |
| I2 通知不串项目 | wf ask --project A 只出现 A 的项 | 分项目 inbox 视图各自只含本项目 |
| I3 长等待不占对方的调度 | A 的命令运行中，B 的 tick 照常推进（两个进程） | 两个定时任务各自的运行记录与节奏 |
| I4 同名包不覆盖 | 两个项目交付同名版本包，两份审查键不同、文件都在 | local/reviews/ 两套文件齐全 |
| I5 A 阻塞时 B 照常 | A 置为 BLOCKED，B 完成一轮 | 同左，真实通道 |
| I6 唤醒各投各的 | （执行端范围） | 唤醒日志里每次投递的目标会话与 project 一致 |
| I7 审查结论各写各的 | review_adapter.json 的 project、turn 与本轮一致 | 同左 |
| I8 共用资源排队可见 | 两个项目同时要发送：arbiter.jsonl 有 wait、grant、release，holder 正确 | 同左，真实通道 |

## 8. 分阶段

| 阶段 | 工具版本 | 内容 | 验收 | 回滚 |
|---|---|---|---|---|
| S1 会话按项目绑定 | 1.7.0 | --conv、conv_new --project、wf conv bind、transport test 两项、TRANSPORT.md 约定 | I1；之后 amy-local 才改回 auto:true | 适配器退回默认对话；工具回到 1.6.9 |
| S2 按项目寻址 | 1.7.2 | 分项目 inbox 视图、wf ask --project、信号分目录、审查键 | I2、I4、I7（执行端：I6、审查程序参数化） | 兼容读取旧位置；工具回到 1.7.1 |
| S3 每项目调度 | 1.7.3 | wf tick --project、wf schedule plist --project、schedule status 按项目 | I3、I5 | 重新加载全局定时任务 |
| S4 共用资源仲裁 | 1.7.4 | 发送槽、arbiter.jsonl、看板一栏、transport.json 的项目覆盖 | I8 | shared_max_inflight 设为不限，行为同现在 |

1.7.1 用于目标达成后由证据生成回报（与隔离无关，插在 S1 之后）。S2 与 S3 可以对调：如果更急的是“一个卡住不拖累另一个”，就先做 S3。每个阶段的构建器都附自检，原有检查不删改，floor.py 不动；P2-F 的演练（无会话退化、多项目并行）放在对应阶段的验收里做。

## 9. 不在本设计内

多账号（用户本人的凭据决定）；拆仓库或多份工具；改四道底线；改用户正在用的产品、通道与环境。
