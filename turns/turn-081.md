【项目续接 · 新对话】档位：high

上一个对话因 当前对话已 20 轮、约 104689 字符，达到上限（policy：20 轮 / 0 字符） 结束。项目上下文在公开仓库 fashionladymall-maker/collab-wf，请直接按链接读取，不需要附件：

1. 进度总览：https://raw.githubusercontent.com/fashionladymall-maker/collab-wf/main/STATUS.md
2. 项目状态与计划：https://raw.githubusercontent.com/fashionladymall-maker/collab-wf/main/project/PROJECT.md 、https://raw.githubusercontent.com/fashionladymall-maker/collab-wf/main/project/VISION.md
3. 协作规则：https://raw.githubusercontent.com/fashionladymall-maker/collab-wf/main/tool/docs/WORKFLOW.md ；【驱动指令】格式：https://raw.githubusercontent.com/fashionladymall-maker/collab-wf/main/tool/docs/DRIVER.md 第 5 节
4. 最近一轮：我方消息 https://raw.githubusercontent.com/fashionladymall-maker/collab-wf/main/turns/turn-080.md ；你的回复 https://raw.githubusercontent.com/fashionladymall-maker/collab-wf/main/replies/reply-080.md
5. 工具源码：https://raw.githubusercontent.com/fashionladymall-maker/collab-wf/main/tool/ 下按路径读取（例如 tool/wflib/driver.py）；版本包在 artifacts/

当前：工具 collab-wf 1.6.6，项目 v2.6（提交 e213fe3），仓库指纹 b848fba7f3776a69（7 个文件）；工作流 COMPOSE（组织消息），第 81 轮。

最近一轮的回报清单与验收结论：
第 80 轮（Claude 建议档位 medium）：
  1. `wf goals` 原文，应为 7/7。 —— 通过
  2. `wf fingerprint` 原文，应仍为 b848fba7f3776a69。 —— 通过
  3. 执行端在本机验收测试报告（这一项不由驱动器执行），请回报以下内容： —— 暂缓（没有机器判据，原文见回报，由 Claude 判断）
  待用户决定 1：执行端自查发现执行端目录下有明文凭据的候选文件，其中一个是权限过宽的旧备份。是删除、收紧权限还是保留，由你决定。执行端没有动它们。本项不阻塞本轮。
  待用户决定 2：M1 最终验收：驱动器停在“目标达成”，确认只认你本人的原话。本项不阻塞本轮。

本轮请只做三件事：复述当前版本、阶段、待用户决策与工具边界；按仓库内容核对仓库指纹；给出下一轮指令。本轮不要交付新版本。raw 链接偶尔有几分钟的缓存，内容对不上时稍后重读。

===== 附：本轮原本要发出的消息（原样）=====

# collab-wf 改进批次一（P0/P1/P2）委派包

档位：high

## 0. 背景与本轮定位
用户读完第80轮全流程能力验收报告后，要求把报告里的"有条件通过 / 已知关注 / 未覆盖实景"逐项解决完善，目标是"丝滑、顺畅运行"。
本包是改进批次一：**需要 Claude 设计或出代码的部分**。执行端已自行完成的回归在 §1 汇报，不需要 Claude 重做。

## 1. 执行端已完成（本轮前，供你判断上下文，不必重复做）
1. **看门狗完成通知分类补丁 · 本机真实回归**：对已安装的 `~/.workbuddy-ai/executor/watchdog.py`（SHA256 `b71a50d72a769f2e153624ae82ec4d288855713f8e674104d4647857c3097541`）跑了 24 项分类矩阵，**24/24 通过**。要点：
   - 正常 `done` 通知（key=`<项目>|done:<N>`、for=user、action/code 空、state=DONE 且 turn==N）被识别为正常提示，**不再计入积压**；
   - 带 `action`/`code`/`escalated` 的待办一律不豁免；`done` 但状态非 DONE、轮次对不上、for 非 user、轮次非数字，一律不豁免；
   - `quota` 到点前豁免、到点后迟迟不恢复则照常告警；
   - 端到端 `check_inbox`：只有 done 通知 → 0 条告警；done + 真待办 → 报 1 条积压；带 code 批准项 → 报积压；真待办坏 `since`/非对象条目 → 报畸形（fail-visible）。
   - 实测 `watchdog.py --status`：工具内 watchdog `findings: []`，健康检查未发现异常。
   - 观察项（非缺陷）：正常 `done` 通知即使 `since` 坏，也按"已知正常提示"跳过，不报畸形。这是有意的优先级，记录备查。
2. **独立审查三态历史证据核对**：`local/reviews/` 现有请求记录分布为 pass 3 次（含真实 v2.6 落地）、concerns 4 次（真实 v2.2–v2.5 落地）、block 2 次（`executor-review-block.zip`、`block-v2.zip`，turn-059 接口演练）、timeout 2 次（`executor-review-timeout.zip`，及本次演练）。即**接口层的 pass/block/timeout 三态已有留档**。
3. **本轮演练遇到的环境事实**（不是代码缺陷，记录以免重复踩坑）：直接驱动审查程序做完整演练时，本机宿主的安全删除守卫会拦截子进程的临时目录操作（`SAFE_DELETE_BULK_GUARD_ERROR: EACCES`），导致审查程序内部的 `wf signal add` 子调用失败、进程以 rc=1 退出（但 timeout 状态仍被正确写进请求文件）。带 `CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR=/Users/admin/.workbuddy-ai/tools/local/safe-delete-bulk` 后可正常执行。演练残留信号已清理，当前 `wf signal list` 为空。

## 2. 本轮需要 Claude 交付的内容（按优先级）

### P0-A 修 "DONE 优先导致『执行端 N』漏登记"
**现象**（第80轮实测）：当项目目标已全部达成（7/7）时，驱动器在验收阶段先进入 DONE；Claude 回复【驱动指令】里写的"执行端 N：<事项>"与"之后：等执行端"因此**不会被登记**为可追踪待办，也没有进 inbox。作者在第80轮因此主动放弃使用该机制（把判据写进回报清单、之后写"空闲"）。
**要求**：
1. 给出最小补丁设计：让"登记执行端待办"发生在目标检查/DONE 判定**之前**；或让 DONE 状态下新出现的执行端待办作为**可见项**保留（进 inbox 且带 action/code），不因进入 DONE 而丢失。
2. 明确 DONE 的语义不被破坏：DONE 仍是"目标达成、自动化停下、等用户"；待办登记不改变目标判定，也不让驱动器在 DONE 下继续推进项目版本。
3. 给出**可执行的自检用例**（新增到 `wflib/selftest*.py`），覆盖：① 目标已达成时新待办仍被登记 ② 待办登记不改变 DONE ③ 待办完成后仍能生成回报或提示 ④ 无待办时 DONE 行为不变。
4. 说明该改动是否触碰 `wflib/floor.py`（若触碰，按约定必须停下等用户确认后手动安装；否则走 `--auto` 自动安装）。

### P1-B PROJECT.md 补记第 67–79 轮事实
**要求**：起草 PROJECT.md 的补记，至少覆盖：
- M1 达成（第71轮 02:51:20 DONE，G3b 第67轮、G3c 第69轮、G3d 第71轮；现行判据连续 62–71 十轮通过；**限制**：独立审查提交不产生 by=executor 项目事件，M1 口径不证明完全无执行端）；
- 看门狗完成通知误分类的诊断与修复（第75轮补丁、本机 24/24 回归、安装后 SHA）；
- Papertrade 第77–79 轮的验收结论（r2 block → r3 pass_with_concerns → r4/动态图解 pass_with_concerns）；
- 第80轮全流程能力验收报告的结论（CONDITIONAL）与遗留项；
- 修正历史文档里"执行端仅小时唤醒""codebuddy 登录"等旧表述，区分历史裁定与现状。
**要求**：作为项目版本包交付（`wf apply` 可落地），改动仅限文档，不含行为变更。

### P1-C r4 现金模拟的计量单位与 EPS 边界
**现象**：r4 的 `simulate()` 用 `EPS = 1e-9` 判断期初队列是否为空：
```javascript
var EPS = 1e-9;
if (q0 <= EPS && cash >= o.W + o.f * o.L) { branch = "收费"; queue = 0; }
else { ... }
...
if (queue < EPS) queue = 0;
```
实测：`cash0=100, queue0=5e-10, L=100, W=80, f=0.02, n=1` → 进入"收费"分支，现金 118、实付 78.4、费用 3.6、队列被清零，而严格"队列为正即走队列优先"应得现金 121.6、费用 0。
**要求**：明确教学模型的**最小计量单位**（例如以美元分为最小单位），并给出处理方式，二选一并说明理由：
- 严格语义：`queue > 0` 就走队列优先，正债权不被静默删除；或
- 明确量化：在模型入口把金额量化到最小单位，`EPS` 只用于展示与残差比较，绝不用于消灭实际债权。
**要求**：同时给出该改动的数值回归用例（含上述反例），并说明"理论净收益 ≠ 已收到现金"在 UI 上的表述是否受影响。

### P1-D 独立审查 block / 超时 的**驱动器侧**正式演练方案
**现状**：接口层三态已有留档（§1.2），但"驱动器消费 block → 停在 AWAITING_USER 显示审查意见"与"驱动器消费超时 → 决策日志记『未审查』并照常继续"**没有真实端到端证据**。
**要求**：给出一个**可控、不涉及真实资金、不落地坏代码**的正式演练方案，包含：
1. 演练用的版本包与 diff 如何构造（例如只改 CHANGELOG 的良性包用于 pass；一个**故意削弱护栏、但绝不会被落地**的包用于 block——驱动器必须在落地前停下）；
2. 驱动器侧应产生的可核对证据（决策日志条目、`review_adapter.json`、inbox 项、`wf explain` 输出）；
3. 演练结束后的**清理步骤**（如何让项目回到正常状态、如何避免演练包被误落地）；
4. 是否需要在工具内提供一个演练专用入口（例如 `wf` 子命令或 `--kind drill`），还是纯手工按流程走一次即可。
**要求**：若需要工具改动，给最小补丁；若不需要，给出逐步操作清单（写给执行端照做）。

### P2-E SEND_NOT_LANDED 改为按墙钟计时
**现状**：落地确认目前用 `landed_confirm_polls`（45）×`landed_poll_s`（60）近似 45 分钟，是"轮询次数 × 间隔"，不是墙钟上限；轮询被唤醒抖动或 tick 被打断时会漂移。
**要求**：给出改为按墙钟计时的最小设计（例如新增 `landed_timeout_s`，与现有两个参数并存或替代，说明兼容与迁移），并给出自检用例。不要削弱"结果不明不重发"这条不变量。

### P2-F 无会话退化 / 多项目并行的可演练化
**现状**：① 完全没有可用执行端时的退化（审查记未审查、告警退回每小时兜底）② 多项目长时间并行，均无实景证据。
**要求**：各给一个**最小可执行**的演练设计（可以是在本机可控构造的、可回滚的），写明前置条件、步骤、可核对证据、恢复步骤。若某项在本机确实无法安全构造，明确说"不可演练"并给出替代的静态论证或代码级证据。

## 3. 约束与非目标
- 不涉及真实资金、密钥、实盘、合规签字；不改四道底线；不扩大自动执行权限（若确需扩大，单独列出并明确"需用户批准"）。
- 不改动 `wflib/floor.py` 以外可自动安装的文件时走 `--auto`；**任何触碰 `floor.py` 的改动必须停下等用户确认**。
- 交付物放在交付目录并核对哈希；作者容器自测不等于本机通过。
- 本轮**不要求**一次性交付全部六项；可按 P0-A → P1-B → P1-C → P1-D → P2-E → P2-F 的顺序分批，但请在回复里明确"本轮交付了哪几项、其余项的计划"。

## 4. 当前真实环境与指纹
- 工具：collab-wf 1.6.6（`wf-v1.6.6`，安装于 `/Users/admin/.workbuddy-ai/tools/collab-wf`），`wf self-check` 对照 1.6.6 文件清单完全一致。
- 项目：collab-wf-dev，当前 **第80轮 DONE（目标达成）**，`wf goals` = **7/7**。
- 仓库指纹：**b848fba7f3776a69**（7 个文件）。
- 执行端：WorkBuddy AI（国际版），本机 macOS，模型 GPT 系列 `gpt-6.1-sol`。
- 作者端：Claude 网页通道，模型固定 Opus 5.5。
- 适配器配置：`local/transport.json`，`review` = `/Users/admin/.workbuddy-ai/executor/review_adapter.py`，`review_timeout_s` = 900。
- 已知非阻断提示（如实记录，不必处理）：额度缓存超过 10 分钟；事件日志历史 5 个重复序号、2 处倒序、0 行无法解析。

## 5. 回报要求
按既有格式输出六块。【驱动指令】里如需执行端做事，写"执行端 N：<事项>"（注意：本轮 P0-A 正是要修"执行端 N"在 DONE 下漏登记的缺陷，若你在同一轮里既交付 P0-A 又需要执行端做事，请说明你预期它在修复前后分别如何表现）。
【需要用户决定】只列属于用户本人的事项（资金/密钥/合规/实盘/本人签字）。当前已知属于用户本人的两件：① 执行端目录下明文凭据候选文件的处置（删除/收紧权限/保留）；② M1 最终验收（只认用户原话）。这两项**不要**在回复里要求执行端代办。
