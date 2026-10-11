【回报 · 第 88 轮 · v2.6】档位：high

① 环境障碍与偏离
- 第87轮按你的指示与 WORKFLOW §10 开了新对话（原因 W48：旧对话 ad11a27a… 的 download-file 端点对任意 path 恒返回同一份 18884 字节内容，任何文件都取不回）。执行端用适配器 conv_new 建立 021f6fff-de4b-42ac-878d-8f4838ba4143（settings 回读逐项一致），bridge.json 已切换（备份 bridge.json.bak-before-conv-20261012-071937），并 wf conv new 归零计数。第87轮消息 = 工具生成的续接说明 bootstrap-20261012-022237.md（35734 字符）+ 一段附录（预告本回报将附上甲/乙全文），共 36540 字符，档位 max。
- ★ 新缺陷 N10（第87轮实测，已并入甲清单的 P1-D）：手工 wf conv new 不清 state.send_ref（wf.py:987-995 只重置 conv 计数），而工具自己的自动换对话路径有这一步（driver.py:430，注释明写"上一轮的 ref 属于旧对话"）。后果链：send_ref 仍指旧对话 → driver.py:537 把它当 --prev-ref 传下去 → aimonkey.py:347-348 据此把 cid 定为旧对话 → poll 永远"按 --since 未找到本轮消息（会话共 14 条）"，wf look 也看错会话 → 退避 5 次后 BLOCKED，而此时 wf resend（wf.py:456-459 对 pending intent = 清意图 + 重发）会重复发送一条已落地的消息。这直接违反 WORKFLOW §10 的规程本身（§10 明写"执行端把桥接工具指向新对话，先运行 wf conv new --note …"）。处置：按 driver.py:430 的同一语义清掉 state.send_ref（备份 state.json.bak-before-clearsendref-20261012），未改其他状态、未伪造任何用户原话。清理后按同一组参数轮询立即返回 status=done / landed=true / ref.cid=021f6fff…，驱动器于 23:31:34 正常收敛为 WAIT_REPLY。
- 发送结果不明但已落地（再次印证"不可见 ≠ 未送达"）：适配器 send 退出码 4（SSE 只收到 completion_type=unparsed 的 completion，60s 窗口内无正面凭据），工具按 NET（退避 30/60/120/300）→ SEND_UNKNOWN_WAIT 反复轮询确认、绝不自动重发（driver.py:544-546，符合设计）。地面真相：新对话 chat_messages = 2 条（index0 human = 本轮消息、index1 assistant = 你的回复 23:23:16Z）。
- review_timeout_s 改动的时刻与备份位置（你要求写明）：改动时刻 2026-10-12 02:12 CST；文件 local/transport.json；配置项 review_timeout_s 由 900 改为 300；备份 local/transport.json.bak-before-review-timeout-20261012。作用：anomaly.call（anomaly.py:101-102）自动经 WF_ADAPTER_TIMEOUT_S 下传，只改一处即全局对齐——适配器 270s 优雅输出 unreviewed 退出（码 4），self-update 按"未审查"继续、不阻塞安装；连带 c02 总耗时约 466s < tick 540s 预算。第86轮的 c02 实测 912s（其中 716s 在等审查结论），是这次止血的直接起因。
- 第87轮是"复述轮"，未交付任何新版本（按 §10 第 3 条）。附件仍无法上传，你通过公开同步仓库读到了原文，核对结果与执行端一致。

② 关键数字与终端原文

1) 工具版本为 1.6.8。 —— 通过（exit=0，0.6s）
2) 仓库指纹仍为 b848fba7f3776a69。 —— 通过（exit=0，0.4s）
3) `wf goals` 原文，应为 7/7。 —— 通过（exit=0，0.4s）
```
$ wf version
collab-wf 1.6.8（wf-v1.6.8）  安装位置：/Users/admin/.workbuddy-ai/tools/collab-wf
可回滚的版本：wf-v1.6.8、wf-v1.6.7、wf-v1.6.6、wf-v1.6.5、wf-v1.6.4、wf-v1.6.3、wf-v1.6.2、wf-v1.6.1、wf-v1.6.0、wf-v1.5.9、wf-v1.5.8、wf-v1.5.7、wf-v1.5.6、wf-v1.5.5、wf-v1.5.4、wf-v1.5.3、wf-v1.5.2、wf-v1.5.1、wf-v1.5.0、wf-v1.4.3、wf-v1.4.2、wf-v1.4.1、wf-v1.4.0、wf-v1.3.1、wf-v1.3.0、wf-v1.2.2、wf-v1.2.1、wf-v1.1.0、wf-v1.0.3、wf-v1.0.2、wf-v1.0.1、wf-v1.0.0
当前项目 collab-wf-dev 固定版本：1.6

$ wf fingerprint
仓库指纹 b848fba7f3776a69（7 个文件）

$ wf goals
目标进度 7 / 7
  ✓ G1 [可执行] 工具文件与版本清单完全一致：通过
  ✓ G2 [可执行] 体检健康：驱动器心跳正常、没有错误项：通过
  ✓ G3a [可执行] 历史上出现过连续 3 轮无人工动作：通过
  ✓ G3b [可执行] 历史上出现过连续 6 轮无人工动作：通过
  ✓ G3c [可执行] 历史上出现过连续 8 轮无人工动作：通过
  ✓ G3d [可执行] 历史上出现过连续 10 轮无人工动作：通过
  ✓ G4 [可执行] 工具版本由驱动器自己构建并装上：通过

```

③ 需要决策的点
1. 甲（工具加固需求，9 项）与乙（多项目隔离设计包）全文见下，请据此定 1.6.9 的范围。执行端意见：P0-A～P0-D 是"避免再卡"的核心，建议优先；P1-D（N10）是第87轮新发现的"卡 + 重复发送"组合，建议并入同一版本；P1-A（unpause 由发起方决定）你已在第86轮定为 1.6.9 内容，与甲里的 P1-A 是同一项。
2. T86-2 未完成（生成器缺失，见下附一）——需要不依赖下载通道的 make_r5.py 落地路径（或直接给出 708e7dd4… 的内容）。执行端意见：并入 W48 处置。
3. 三项用户决策（执行端目录下明文凭据候选文件的处置 / M1 最终验收 / 是否授权执行端解除自己发起的暂停 + amy-local 的 6X2）仍待用户本人一句话，均不阻塞。

附：仓库指纹 b848fba7f3776a69（v2.6，7 个文件）

════════════════════════════════════════
附一：T86-1 至 T86-3 结果原文（不摘要）
════════════════════════════════════════

----- T86-1（已完成）-----
【事项】核对本轮 verify.json 六项是否全部 pass（内存里的 1.6.7 会把失败吞进 DONE），记下 c01、c02 的末行与本机 1.6.8 版本包的 sha256（应为 c5733bc3…），用 wf task done T86-1 --note 写下

【结果原文】
逐条核对第86轮六项验收，全部真实 pass，退出码全 0，无失败被 DONE 吞掉：1 build_wf_168.py exit0（自检231/231、63文件、floor.py未改动）；2 self-update exit0（1.6.7→1.6.8）；3 version exit0=1.6.8；4 self-check exit0=完全一致；5 fingerprint exit0=b848fba7f3776a69；6 goals exit0=7/7。
c01 末行：构建通过：collab-wf 1.6.8，自检 231/231，63 个文件 + MANIFEST.json；成员集合核对一致、没有 local/.git/__pycache__/*.pyc；floor.py 未改动；版本包 sha256 c5733bc3cd28f0ff91e8ac3a3cc68521805b6e6541fec6c7580194ea43cfa4cc。
c02 末行：工具已升级：1.6.7 → 1.6.8（048f960），自检已通过；升级核验通过：磁盘版本 1.6.8，63 个文件与版本包一致，看板已加载 1.6.8。
版本包独立复核（不采信自述）：workflow/outbox/collab-wf-1.6.8.zip，326604 字节，sha256 c5733bc3…，与要求值及 c01 自述一致。
需注意（非失败）：c02 耗时 912s，超过 tick 预算 540s，其中 716s 在等独立审查结论；exit=0、interrupted=null，不影响本轮 pass，但属 N2 缺陷（tick 预算不约束单条命令），已记入加固需求。

----- T86-2（已完成）-----
【事项】P1-C：查找 r4 原件（sha256 1d580864a913f907d6f6c1aacd78d61a13db11976af47c13eb8f3fc995f8794f，72438 字节），先看 workflow/evidence/turn-079/delivered/。脚本用本机已核验的 make_r5.py（708e7dd4…）和 pt5_regress.js（f0d3cec4…，与 rejected/ 里那份坏 zip 的字节相同）。找到 r4 就生成 r5，跑 24 项回归，并做真实浏览器桌面与 390 宽度检查；找不到就如实写“未找到”。用 wf task done T86-2 --note 写下

【结果原文】
T86-2 结果：未能生成 r5——不是找不到 r4，而是生成器缺失。
1 r4 原件：已找到并核对——workflow/evidence/turn-079/delivered/papertrade-business-research-20261011-r4.html，72438 字节，sha256 1d580864a913f907d6f6c1aacd78d61a13db11976af47c13eb8f3fc995f8794f，与要求完全一致。
2 pt5_regress.js：已找到且正确——workflow/evidence/turn-083/delivered/pt5_regress.js，18884 字节，sha256 f0d3cec46bc7f4a9cf75c1a316b84384c52d2c1dad421095dbf6537583d239ca，与要求一致。
3 make_r5.py：未找到真品。本机两处同名文件（turn-083/delivered/ 与 outputs/dl-diagnose-20261012/）都是 18884 字节、sha256 f0d3cec4… 的内容，且文件头是 JS 的 use strict 而非 Python——即第 83 轮下载时被 W48 的恒定坏内容覆盖，真品（应为 708e7dd4…、19964 字节）从未到手。dl-diagnose 目录里连故意不存在的 NO_SUCH_FILE_zzz.bin 都是同一份字节，可佐证该端点对任意路径恒返回同一内容。
4 因此 r5 未生成、24 项回归与浏览器宽度检查均未执行——不声称完成。
5 后续：需要作者给出不依赖下载通道的 make_r5.py 落地路径（或直接给出 708e7dd4… 的内容），才能继续 P1-C。已并入 W48 处置。

----- T86-3（已完成）-----
【事项】P0-A 实景（最后做）：先在 T86-3 未完成时记下 wf status（应为第 86 轮 DONE）、wf task list、local/inbox.json 里的 task:T86-3（应带 action）、wf explain（应写“需要：执行端”）；然后 wf task done T86-3 --note "P0-A 实景"，再记下 done:86 的 why 是否附上三项结果、状态是否仍为 DONE。最后按 WORKFLOW 第 10 节开新对话（wf bootstrap --reason "下载端点冻结，W48"、wf conv new），在说明消息后附上三项结果与多项目隔离设计包

【结果原文】
P0-A 实景验证通过。基线记录（在 T86-3 未完成时采集）：1 wf status = 第 86 轮 · DONE（目标达成）· 负责：用户 · 已停留 5 分 5 秒；2 wf explain = 需要：执行端 —— 做完一项 wf task done <编号> --note 结果；目标已达成，做完不会自动回报，需要继续时 wf kick；3 wf task list = ○ T86-3 未完成；4 inbox.json 的 task:T86-3 = for=executor 且带 action（做完后：wf task done T86-3 --note 结果）。即 P0-A 三项特性——待办在 inbox 里带 action、explain 明确写需要执行端、DONE 下状态与待办仍可读——在实景中全部成立。三项待办 T86-1/T86-2/T86-3 至此全部完成。

════════════════════════════════════════
附二：甲 —— 工具加固需求（9 项）全文
════════════════════════════════════════

# 派发：工具加固 —— 消除"静默卡死"（第 86 轮附带需求）

> 发起：执行端（WorkBuddy AI）
> 时间：2026-10-12 02:20（本地）
> 触发：用户原话「又卡死了」「这次 bug 有点多，你总结优化避免再卡」
> 前置：1.6.8 已由驱动器自动构建并安装成功（`wf version` = 1.6.8，`self-check` 完全一致，`fingerprint` = b848fba7f3776a69，`goals` = 7/7）
> 要求：**这是"先把工具设计好"主线的加固包**，请给出方案与代码；执行端负责落地、验收、回报。

---

## 0. 一句话结论

**本轮不是死锁，是"设计上允许的长时间静默等待"**：`wf self-update --auto` 在等一个**必须由执行端本人**写出的独立审查结论，而执行端没有常驻进程、当时也没开会话窗口 → 没有任何东西能推进它 → 干等 15 分钟，且期间**零输出**。用户看到的就是"卡死"。

**根本矛盾：自动化流程里放了一道"必须人工（执行端）在场"的同步门，却没有为"人不在场"设计快速退路。**

---

## 1. 卡点完整证据链（可复核）

### 1.1 现场
```
tick.log:  [wf exec c02] $ wf self-update --auto workflow/outbox/collab-wf-1.6.8.zip
ps:        43560  wf.py self-update --auto …      启动 18:00:23 UTC（02:00:23 本地），无任何输出
events:    seq 3724  exec c02 exit_code=0 duration_s=912.0
           seq 3725  tick exit=0  note="已执行到第 2 条，本次时间用完"
```

### 1.2 那 912 秒花在哪
| 阶段 | 起止（本地） | 耗时 | 说明 |
|---|---|---|---|
| 等独立审查结论 | 02:00:23 → 02:12:19 | ~716s | 执行端本人在 02:12:19 才写出 verdict |
| 安装前自检 | 02:12:19 → 02:15:35 | ~196s | 1.6.8 的 `wf selftest`（231 项） |
| **合计** | | **912s** | 其中 **78% 是在等人** |

### 1.3 为什么没人推进它
```
release.py:336   verdict, notes = _review_tool(src, zip_path)      ← 同步阻塞在这
release.py:304   anomaly.call("review", […], timeout=review_timeout_s=900)
review_adapter.py:137-146  轮询 verdict 文件，budget = 900-30 = 870s
wake.log:  [02:00:26] KEY signal|review:collab-wf-1.6.8.zip 无可用会话（用户可能没开窗口），本轮不投递
           [02:03:54] [ALERT] 唤醒投递找不到可用会话：任务码 signal|review:collab-wf-1.6.8.zip 已等待…已退回每小时巡检
ps:        44133  WorkBuddy 会话进程   启动于 02:10:13（= 用户发"又卡死了"之后才出现）
wake.log:  [02:13:55] KEY amy-local|stuck:COMPOSE… → 已投递（目标 pid=44133）
```

**关键推论**：`pick_target()`（wake_dispatch.py:361）只按"进程存活 + interactive + cwd 命中执行端工作区"挑会话，**不看 key、不看 project**。02:00–02:10 期间执行端一个会话窗口都没开 → 投递必然失败 → 审查请求悬空 → 只能等 870s 超时。

**注意**：超时（unreviewed）本身不阻塞安装（`_review_tool` 返回 `"unavailable"`，只有 `"block"` 才拒绝）。所以**即便我不在场，c02 也会在 02:15:23 左右自己继续**——不会永久死锁。问题是**这 15 分钟是纯浪费，且完全静默**。

### 1.4 次生问题：tick 被单条命令独占
```
tick 预算：wf tick --max-seconds 540（launchd plist）
实际占用：912s（c02 一条命令）
原因：driver.tick() 只在"命令之间"检查剩余预算（driver.py:1242 left = max_seconds - elapsed），
      而 run() → ops.run_exec() 执行单条命令时用的是 policy 的 command_timeout_s = 1800，
      没有把 tick 的剩余预算传下去 → 一条长命令可以把 tick 拖到任意久。
后果：这 15 分钟里 tick 不巡检其他项目、不推进任何别的步骤。
```

---

## 2. 优化需求（按优先级）

### P0-A　审查等待必须"快退"，不能长时间静默
**问题**：`review_timeout_s` 默认 900s，期间零输出。
**要求**：
1. 审查等待期间**每 30s 至少输出一行心跳**（形如 `仍在等独立审查：已 120s / 上限 300s`），让"在等"与"卡死"可区分。
2. 默认等待窗口下调，或**自适应**：先短窗口（如 180s）等一次，超时即按 `unreviewed` 继续，**并把审查请求留在 inbox 供事后补审**，不要用一次长等待赌执行端在场。
3. 明确记录：`unreviewed` 不阻塞安装（现状正确），但**必须在证据里显式写"本轮未审查 + 原因"**，便于事后补。

### P0-B　`wf self-update --auto` 不应让"人工审查"成为安装路径上的同步阻塞点
**问题**：`self-update` 是驱动器自动跑的，却在中间插了一道"必须执行端本人写 verdict"的同步门。
**要求（请评估两种，给推荐）**：
- **方案 1（异步审查）**：安装先行，标记 `pending_review`；审查结论回来后若是 `block`，自动回滚到上一版并告警。
- **方案 2（在场检测）**：发审查请求后先探测"执行端是否在场"（复用 `pick_target()` 的能力）；**不在场就直接按 unreviewed 继续**，不浪费任何等待。

### P0-C　tick 预算必须约束单条命令
**问题**：见 1.4。
**要求**：
1. `run()/tick()` 把**剩余预算**传给 `ops.run_exec()`，命令超时取 `min(命令自身超时, 剩余预算)`；
2. 命令被预算截断时，事件里要显式写 `truncated_by_budget`，并保证**半途状态可恢复**（例如 self-update 是原子操作，不能留半装）；
3. 若某命令天然需要超过 tick 预算（如 self-update），应在**启动前**就判定并给出可见提示，而不是跑到一半被砍。

### P0-D　唤醒投递失败要有"自愈"，不能只写一条 deviation
**问题**：`wake.log` 里同一 key 连续多轮"无可用会话"，只写了 deviation + 退回每小时巡检。
**要求**：
1. 对 `for=executor` 的**阻塞性**事项（如审查请求），在它等待期间**提高重试频率**（跟随 tick 节奏，而不是每小时）；
2. 一旦有会话出现（如用户打开窗口），**立刻补投**——本轮 02:13:55 做到了，但那是下一个 300s 周期，晚了 3 分钟；
3. 关键：**投递成功 ≠ 已处理**，要能追踪到"执行端已读取该请求"，否则仍然靠人。

### P1-A　`wf unpause` 必须允许执行端代决
**问题**：`wf.py:337` 的 `unpause` 子命令**没有 `_by_args`**，走的是 `asks.verify` + `_record_user`（写 `role=user` + `by=user` 事件）。
**后果**：执行端自己 `wf pause` 造成的暂停，**无法由执行端自己解除**，会一直挂 `for=user` 待办（实例：`amy-local|6X2`）。这直接违反用户契约「先执行端，做不到才人工」。
**要求**：给 `unpause` 加 `_by_args` 并走 `_decide_as`；**`wf pause` 生成的待办 `for` 应按发起方决定**（执行端发起的就该 `for=executor`）。
> 执行端**没有**手改 `state.json`、**没有**伪造用户原话——等你的方案。

### P1-B　`sync` 告警的两个误报
1. `wflib/sync.py notices()` 标题**写死"疑似密钥"**，实际命中是"不是有效的 zip"——属误报式告警，会误导处置。
2. `sync_blocked` 的 key **绑了 `ts`**，每 tick 产生新键 → 唤醒层反复投递同一条。
**要求**：告警标题按**实际命中类型**生成；`sync_blocked` 的 key 绑**原因**而非时间戳。

### P1-C　SEND 停留阈值与"结果不明"窗口不一致
**问题**：SEND 的 `max_dwell_s = 900` 与"发送结果不明"的等待窗口不一致 → **每轮长生成都误报一次**。
**要求**：两处窗口统一，或让 SEND 的停留判定理解"生成中"这一状态。

### P1-D　手工 `wf conv new` 不清 `send_ref`（第87轮实测，见 N10）
**问题**：`wf.py` 的 `conv new` 不 `pop("send_ref")`，而 `driver.py:430` 的自动路径会。手工换对话后，新对话第一轮的 `--prev-ref` 仍指旧对话 → 轮询永远查不到本轮消息，`wf look` 也看错会话。
**要求**：`wf conv new` 补上与 `driver.py:430` 一致的清理；并让 `poll` 在"`--prev-ref` 的 cid ≠ 当前默认对话"时**告警**（现在静默用了旧 cid）。
**风险提示**：这个缺陷会把"发送成功"变成"永远确认不到"，最终 `BLOCKED`，而 `wf resend` 会**重复发送**——正是"卡 + 重复"的组合。

---

## 3. 执行端已做的临时止血（请评估是否保留）

```
文件：~/.workbuddy-ai/tools/collab-wf/local/transport.json
改动：review_timeout_s  900 → 300
备份：同目录 transport.json.bak-before-review-timeout-20261012
```
**理由**：`anomaly.call`（anomaly.py:101-102）会自动把该值通过 `WF_ADAPTER_TIMEOUT_S` 传给适配器，所以只改一处即全局对齐：
- 适配器 budget = 300-30 = 270s，**优雅输出 `unreviewed` 并退出（码 4）**；
- `_review_tool` 收到后返回 `"unavailable"` → **不阻塞安装** → 继续；
- 连带效果：c02 总耗时 270+196 ≈ 466s **< tick 540s 预算**，不再拖垮 tick。

**代价**：执行端在场时写 verdict 的时间窗口从 15 分钟压到 5 分钟；超时记为"未审查"，事后可补。
**请评估**：若 P0-A/P0-B 落地（异步 + 心跳），这个值应该恢复多少、或是否应改成自适应。

---

## 4. 期望产出

1. **P0-A/B/C/D 的方案与代码**（1.6.9 版本包），含自检用例：
   - 审查等待期有心跳（可断言 stdout 含周期心跳行）；
   - 执行端不在场时 self-update **在 X 秒内**按 unreviewed 继续（可断言总耗时上限）；
   - tick 剩余预算能截断单条命令（可断言 `truncated_by_budget`）；
   - 唤醒投递失败后的补投时机可测。
2. **P1-A/B/C/D** 的修复（可并入同一版本）。
3. 明确回答：**"人不在场时，哪些门可以自动过、哪些必须等"**——给出这张表，执行端据此写进 `docs/`。

## 5. 附：本轮 bug 清单（含既有）

| 编号 | 问题 | 状态 |
|---|---|---|
| N1 | 审查门同步等待 + 执行端无常驻 → 静默 15 分钟 | 本需求 P0-A/B |
| N2 | tick 预算不约束单条命令（912s > 540s） | 本需求 P0-C |
| N3 | 长命令执行期间零输出（观感 = 卡死） | 本需求 P0-A |
| N4 | 唤醒投递"无可用会话"只写 deviation、退回每小时 | 本需求 P0-D |
| N5 | `wf unpause` 无 `_by_args`，执行端无法代决 | 本需求 P1-A |
| N6 | `sync` 告警标题写死"疑似密钥"（误报） | 本需求 P1-B |
| N7 | `sync_blocked` key 绑 ts → 反复投递 | 本需求 P1-B |
| N8 | SEND `max_dwell_s` 与"结果不明"窗口不一致 | 本需求 P1-C |
| W48 | 对话级下载端点冻结（任意 path 恒返回同一内容） | 已坐实；本机确定性重建已核验；**待你给"不依赖下载通道"的落地路径** |
| W46 | 1.6.7 包夹带 16 个空 `local` 锁 | 1.6.8 构建器已修，待下个包复核 |
| **N10** | **手工 `wf conv new` 不清 `send_ref` → 新对话首轮轮询查旧会话，消息已落地却永远确认不到**（第87轮实测）：`wf.py:987-995` 的 `conv new` 只重置 `conv` 计数，**没有** `st.pop("send_ref")`；而工具自己的自动换对话路径有这一步（`driver.py:430`，注释明写"上一轮的 ref 属于旧对话，不再作为 `--prev-ref` 交给适配器"）。后果：`driver.py:537` 把旧对话 ref 作为 `--prev-ref` 传下去，`aimonkey.py:347-348` 据此把 `cid` 定为**旧对话**，`poll` 于是"按 `--since` 未找到本轮消息（会话共 14 条）"，`wf look` 同样看错会话。若不处理，退避 5 次后 `BLOCKED`，而此时 `wf resend` 会**重复发送一条已落地的消息**（`wf.py:456-459` 对 pending intent 是"清意图 + 重发"）。**违反 WORKFLOW §10 的规程本身**（§10 明写"执行端把桥接工具指向新对话，先运行 `wf conv new --note …`"）。第87轮执行端按 `driver.py:430` 的同一语义手工清掉该字段（备份 `state.json.bak-before-clearsendref-20261012`），未改其他状态。 | 待修：`wf conv new` 里补 `st.pop("send_ref", None)`（与 `driver.py:430` 对齐）；并考虑 `poll` 在 `--prev-ref` 的 cid 与 `conv()` 不一致时告警而不是静默用旧的 |
| **N9** | **暂停/静音项判据多处不一致 → 已暂停项目被反复误报为"卡住"**：① `wflib/asks.py:89` 生成 `stuck:<state>:<since>` 项时**只看停留时长**（`machine.py:110`：`stuck = bool(limit and dwell > limit)`），**不排除 `paused`**；而 `wflib/watchdog.py:34-35` 已正确排除（`if st.get("paused"): return out  # 暂停期间不报停滞`）。后果：`amy-local` 自 2026-10-11T17:01:24 起被持续列为"停留超过上限"并反复唤醒投递（>9 小时）。② `wf schedule status` 对 `auto:false` 项目报"超过 3 个周期，定时器可能没有运行"——**误报**：定时器正常（`collab-wf-dev` 0 分钟前 exit=30），只是被跳过的项目不再更新"最近运行"记录，把旧记录当成停了。 | 待修：三处判据统一——`asks` 也跳过 `paused`；`auto:false` 的项目既不被判 stuck、也不被判"定时器没运行"（或明确标为"已静音"）。**与 N5 同源**——`paused` 解不开才让这个误报永久化 |


════════════════════════════════════════
附三：乙 —— 多项目并行与全链路隔离 · 设计与实现任务 全文
════════════════════════════════════════

【多项目并行与全链路隔离 · 设计与实现任务】
档位：high
当前工具版本：collab-wf 1.6.8；项目 collab-wf-dev v2.6（指纹 b848fba7f3776a69）

用户最新要求（原话）：
「我希望我是可以多项目同步运行的，每一个项目都是独立的，隔离的，全流程都是独立的，包括会话窗口，所有参与环节。你现在都混乱到一起去了。帮我重新梳理需求。重新设计完善。」

请把本轮当作**架构级设计任务**，不要只给局部补丁。下面是执行端整理的需求、本机实测事实与验收判据。

────────────────────────────────
一、需求（R1–R5）
────────────────────────────────
R1 并行：N 个项目同时活动，各按自己节奏推进；一个等待/卡住/报错不拖慢其他。
R2 隔离：任一项目的会话、消息、通知、待办、审查、锁、调度、产物、同步目标都与其它项目无关；A 的任何动作不得改变 B 的状态或通道。
R3 会话独立：每项目固定绑定自己的对话，永不交叉；某项目轮换对话只影响自己。
R4 全链路独立：COMPOSE→VERIFY 每一步，以及唤醒、看门狗、审查、同步、看板，全部按项目隔离。
R5 共用要仲裁不许串线：账号、额度、机器是物理共用资源，必须显式排队/限流并留痕，不能靠"发进同一个对话"隐式共用。

────────────────────────────────
二、本机实测事实（2026-10-12 01:30 CST，均可复现）
────────────────────────────────
1. 会话是全局唯一的：adapter/bridge.json 只有 {"default_conversation": "ad11a27a-e41d-4e20-aa01-3f3c12b87e8e", "wf_project": ".../collab-wf-dev"}；claude_bridge.py 的 conv()（:211-215）只读该单一字段。
2. 工具确实把项目名传给了适配器：driver.py 的 send_args / pargs 都含 --project <name>；但 aimonkey.py:513 注释写明 --project「只用于日志」，不参与选会话。**按项目映射会话这一层根本不存在**（全仓 grep 无任何 per-project conversation 映射）。
3. conv_new 会改写全局默认对话：aimonkey.py conv_new（:659-667）把 cfg["default_conversation"] 换成新对话并写回 bridge.json。→ 任何项目轮换会话都会影响其他项目。
4. transport.json 单份（模型/超时/限速/通道全项目共用）。
5. 发送限速是全局的：local/send_log.jsonl + transport.rate，driver.h_send 读该文件计数。
6. 收件箱单文件：local/inbox.json 同时装 collab-wf-dev 与 amy-local 的项。
7. 信号目录全局：local/signals/，靠文件名 <project>_<slug>-<hash> 区分。
8. 审查请求键 = 发布包文件名：local/reviews/ 下 block-v2.zip.*、collab-wf-1.6.7.zip.* 混放，同名包会互相覆盖。
9. 调度单实例串行：launchd local.collab-wf.tick，StartInterval=60，wf.py tick --max-seconds 540，一次推进全部项目。
10. 唤醒单实例单目标：launchd local.collab-wf.wake，投递到单个执行端会话。
11. 看门狗单实例、判定全局：local.collab-wf.watchdog，告警进同一个全局 inbox。
12. 审查适配器硬编码：executor/review_adapter.py 固定 PROJECT/LOCAL（:24-31、:77-84），会写生产 local/reviews 并跑全局 wf watchdog --write。
13. 注册表单文件：local/registry.json（现含 collab-wf-dev、amy-local）——本身合理，保留。
14. 看板单实例：local/serve.json，端口 8765。
15. 桥接凭据单份：adapter/.secrets/.cookies.txt（一个账号）——物理共用，见第四节。
16. **第87轮新增实测（2026-10-12 07:19 CST，再次坐实第 3 条）**：执行端为 collab-wf-dev 开新对话（W48 处置）时，适配器 `conv_new` 把 `bridge.json` 的 `default_conversation` 从 `ad11a27a…` **整体改写**为 `021f6fff-de4b-42ac-878d-8f4838ba4143`（备份 `bridge.json.bak-before-conv-20261012-071937`）。即：**任何一个项目轮换对话，都会把全局默认对话抢走**——这正是"混乱到一起"的直接机制。同轮还暴露一个相邻缺陷 N10（手工 `wf conv new` 不清 `state.send_ref`，新对话首轮轮询会去查旧会话，最终 BLOCKED 且 `wf resend` 会重复发送），详见甲清单 P1-D。

**当前"多项目"的真实代价（证据在项目自己的配置里）**：
amy-local/workflow/wf.json 原文写着：
「auto: false = 驱动器不自动推进本项目。原因：collab-wf 的收发适配器只有一份全局会话（bridge.json 的 default_conversation），第二个项目若自动发送会与 collab-wf-dev 在同一对话里串线。」
→ 现在是"一个跑、一个停"，不是并行。

────────────────────────────────
三、验收判据（设计必须让下列每一条都能被实测）
────────────────────────────────
I1 A 轮换对话后，B 下一轮仍进 B 自己的对话。
I2 A 的通知不出现在 B 的待决列表（分别 wf ask 核对 project 字段）。
I3 A 的长等待不占用 B 的调度预算（B 仍按自己节奏推进）。
I4 两项目同名发布包不互相覆盖（两份审查请求与结论都完整留存）。
I5 A 处于 BLOCKED 期间，B 继续正常轮转。
I6 每项目唤醒只投递到该项目自己的会话。
I7 每项目审查结论写进自己的证据目录（review_adapter.json 的 project/turn 绑定正确）。
I8 共用资源命中排队时，记录能说明"被谁占用、轮到谁"。

────────────────────────────────
四、必须共用、只能仲裁的资源（不能隔离掉的）
────────────────────────────────
① 一个 aimonkey 账号与 cookies；② 一个 Claude 账号的额度与速率（5 小时/7 天窗口）；③ 这台机器（launchd/磁盘/网络）。
要求：显式排队 + 留痕，例如"谁在何时占用额度窗口、下一个轮到谁"。
是否需要多账号属于用户本人的凭据决策，**不要把它作为本方案的前提**，执行端不代办。

────────────────────────────────
五、非目标
────────────────────────────────
- 不拆物理共用资源（那要用户新凭据，不在本次范围）。
- 不改四道底线（wflib/floor.py），不放宽任何权限。
- 不做"多份工具副本"，要的是"一套工具 + 显式项目上下文"。
- 不修改用户真实使用的产品名/通道名/环境，不做去个人化。
- 本轮不要顺带重写无关模块。

────────────────────────────────
六、安全与交付约束
────────────────────────────────
- 不改 floor.py；不涉及真钱、凭据处置、签名、实盘。
- 只下载附件、不上传；回报正文不出现平台内部机制细节。
- 真实环境：macOS + launchd + WorkBuddy AI 国际版；通道 c.aimonkey.plus 网页端；固定模型 Opus 5.5。
- 每个交付文件列字节数与 sha256。

────────────────────────────────
七、请交付
────────────────────────────────
1. 隔离模型表：第 2 节 15 项逐一给出"由谁持有、存在哪里、如何按项目寻址"。
2. 会话绑定方案：bridge.json 从"一个默认对话"演进为"按项目映射"的具体结构与迁移步骤；轮换对话时只改自己那一项的机制。
3. 目录布局：local/ 从全局单份演进为按项目分区的具体路径与兼容策略（含 WF_LOCAL_DIR 是否仍需要）。
4. 共用资源仲裁：额度与限速的排队规则与留痕格式。
5. 调度与唤醒：每项目独立节奏的实现；唤醒目标按项目解析的方法。
6. 迁移与回滚：collab-wf-dev 与 amy-local 两个现有项目如何迁移、如何回滚。
7. 可直接落地的代码补丁 + 覆盖 I1–I8 的可执行测试。
8. 分阶段顺序：若一次做完风险大，给出可分阶段落地、每阶段可独立验收的顺序。

────────────────────────────────
八、本轮已知的、与本设计相邻但不要求你一并解决的事（供你判断依赖关系）
────────────────────────────────
- 第83轮交付的 v2.7 zip 实为 pt5_regress.js 源码（18884B / f0d3cec4，非 zip），已隔离到 evidence/turn-083/delivered/rejected/ 并保留证据；有效 v2.7 仍待重新交付。
- 同步曾因该坏包暂停；已隔离后恢复推送。另发现两处工具缺陷待修：① sync.notices() 对所有 blocked 原因都写死标题「发现疑似密钥」，实际命中是"不是有效的 zip"，属误报；② sync_blocked 的 key 用 rec["blocked"]["ts"]，每次 tick 刷新 → 同一原因每分钟产生新键，唤醒反复投递同一问题。建议一并给修法（可归入下一小版本）。

────────────────────────────────
九、执行端本轮不做的
────────────────────────────────
不自行设计、不自行改主工具源码。等你的方案与代码，落地后按第三节 I1–I8 逐条实测并回报原始输出。

【下一轮建议档位】high
