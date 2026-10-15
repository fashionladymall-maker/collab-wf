# B3 纯合成状态夹具

- 2026-10-15：MAIN-CONTRACT-B3 首版。
- 2026-10-16：B3-MAIN-FXR-20261016-1 修订，生成器升级为 `wflib.b3_fixtures/2`，清单格式为 `b3-fixture/2`。
- 2026-10-16：B3-MAIN-GUARD-20261016-1 返工，修复独立审查发现的三个反例（GUARD-R1/R2/R3）。
- 2026-10-16：B3-MAIN-SEQ-20261016-1 更正 seq 叙述，新增经真实消费者的 SEQ scaffold 测试。

生成器为 `wflib/b3_fixtures.py`，定向测试为 `wflib/selftest_b3_fixtures.py`。

夹具全部是合成的，**不是**生产证据，**不是**安装授权，也**不能**替代真实轮次。生成器不读取、不复制、不修改任何真实宿主状态：真实 local/、WorkBuddy 执行端、原 dirty 工作区和第 149 轮都不在它的输入里。

## 夹具等级与验收层级

每个场景属于下面三种等级之一。三种等级给出的证据都**最多到 L0/L1**。

| 等级 | 含义 | 场景 |
|---|---|---|
| 布局 | 只有文件形态，没有任何真实消费者去读它 | S06、S09b |
| 字段 | 真实 `core.Instance` 能读出这些字段，但没有执行 driver 的处理函数 | S02a、S02b、S03 |
| 行为 | 真实消费者函数在合成输入上给出了预期结论 | S01（仅读取层）、S04a/b、S05、S07a–e（仅安装锁扫描）、S08、S09a、R01–R03 |

**L4 是在正式候选包上做的真实演练，任何夹具等级都不能替代它。** L4 包括：版本与重建的 MANIFEST、审查 API 安装绑定、旧宿主首次升级、有状态回滚、真实默认门禁的正反例，以及 m4 上的运行。所以"行为"等级只说明某一个消费者函数的判断是对的，不说明整条链路已经通过验收。

## 两层

**第一层（S）只用标准库，不导入任何 wflib 模块。**
- 同一根目录重复生成，产物按字节一致。
- 在不同根目录生成时，把绝对路径替换为 `<ROOT>`、把哈希封装记录中派生的 sha256 替换为 `<DERIVED>` 后，归一化视图一致。
- Git 仓库使用固定身份和固定日期（合成时钟 2026-01-15T08:00:00+00:00），因此提交号稳定。

**第二层（R）在子进程中驱动 B2 的合成审查夹具 `wflib.selftest_review.Env`。**
- 子进程的 `WF_LOCAL_DIR`、`HOME`、`TMPDIR` 都位于夹具根目录内。
- 子进程内的审计钩子只约束这个 Python 进程本身：它拒绝本进程写到根目录之外（str 与 PathLike 都有测试），并且只允许启动三种进程——启动时解析出的同一个 git，以及 `host/bin/runner.py`、`host/bin/verifier.py` 两个替身脚本；`os.system`、fork、白名单之外的 exec/spawn 一律拒绝。
- **这不是 OS 沙箱。** 审计钩子不会继承到被启动的进程，白名单内的 git、runner、verifier 自身的写入不受审计；它们的工作目录被限制在根目录内，`HOME` 与 `TMPDIR` 也指向根目录内。不使用 seccomp、命名空间等平台机制，也不需要任何权限变更（反例 GUARD-R3 的修复说明见下文）。
- 这一层只保证语义可复现，不保证字节一致。

## 第二层依赖契约（FX-R）

第二层能否正确工作，取决于 B2 审查夹具和 review 接口的现有行为。只要这些依赖发生漂移，生成就必须明确失败，不允许悄悄改变语义。为此设了两层检查。

**静态层：14 项源码指纹，记录在 `TIER_R_CONTRACT` 中。**
- 覆盖范围：
  - `selftest_review.py` 中的 Env、RUNNER、VERIFIER、REVIEWER、AUTHOR_SESSION、GIT_ID、`_git`、`_zip`；
  - `review.py` 中 prepare、run、status、`_now`、`_tool_home` 的签名，以及 REUSE_DAYS。
- 指纹对源码文本求哈希（只去掉行尾空白），在 Python 3.9 和 3.12 上结果相同。
- 检查发生在写入任何夹具之前。发现漂移时抛出 `FixtureRefused`，并逐项列出 changed、missing、unpinned。
- 这 14 项指纹在 batch2 b523be4 和 PR33 d01192d 上一致，因为这两个文件在两者之间没有改动。

**行为层：子进程在运行时逐项检查。**
- Env 写入的 verifier 配置标记了 synthetic，且钉住值与替身 verifier 的字节一致；
- 审查决定为 allowed，provenance.synthetic 为 True，level 为 platform_verified，审查者身份的平台为 synthetic；
- `behave(verdict="block")` 确实产生 block；
- 改钉之后，配置仍然标记 synthetic。

任何一项不满足，都会以"tier R behavioural contract failed"失败。所以即使有人只刷新了指纹而没有复审，替身一旦不再自报 synthetic（也就是 CT4 的形态），这一层仍然会拦下。

**更新指纹的流程：**
1. 先复审第二层与新版夹具之间的差异；
2. 再用 `b3_fixtures.tier_r_contract()` 生成新的指纹；
3. 在同一个提交里同时更新 `TIER_R_CONTRACT`，并写明是谁复审的。

## 安全护栏

根目录必须满足以下全部条件：
- 是绝对路径，并且已经完全解析（没有符号链接组件或 `..`；macOS 上需要传入 `Path(...).resolve()` 之后的 `/private/var/...` 形式）；
- 严格位于临时目录之内；
- 路径中任何一级都不是 `.workbuddy-ai`；
- 不在工具目录之内；
- 不是当前配置的 LOCAL（`WF_LOCAL_DIR`，未设置时为工具目录下的 `local/`）、不在它之内、也不是它的祖先目录。比较时同时使用词法形式与规范形式，因此符号链接别名同样被识别（反例 GUARD-R1）。生成器只解析这个路径，不读取其中的内容；
- 要么是空目录，要么尚不存在。

**临时目录本身也必须是沙箱**，这是本次修订新增的规则，起因是反例 CX2。
- 它必须是共享的粘滞位临时目录（例如 /tmp），或者是本人所有、组和其他人不可写的私有目录（例如 macOS 的 /var/folders/…/T）。
- 不能是 `/`、HOME 或 HOME 的上级目录、包含工具目录的目录，也不能位于 `.workbuddy-ai` 之下。
- 判断临时目录时只做只读检查，不调用 `tempfile.gettempdir()`，因为它首次调用时会在临时目录里写探测文件。

此外：
- 所有写入都经过统一的写入器。`path`、`mkdir`、`write`、`remove` 共用同一条路径规则：拒绝绝对路径、`..`、空路径、`.` 和 NUL（反例 GUARD-R2：此前 `mkdir("../outside")` 能在根目录之外建目录）；写入使用 `O_EXCL` 或 `O_NOFOLLOW`，拒绝非普通文件；
- 密钥形态的值在运行时拼接，源码和文档中都不出现字面值。

**仍然允许的情况：** 本人所有、权限为 0700 的其他目录（例如 `~/Documents`）如果被设为 TMPDIR，仍会被视为私有临时目录。生成器只会在其中新建空目录，不会覆盖已有文件，但这一点需要调用方知情。

## 清单与 verify()

每个场景目录下的 `FIXTURE.json` 记录以下内容：
- synthetic、not_production_evidence、`authorization: none`；
- 夹具等级对应的消费者（`validated_by`，为 None 时表示只有布局）；
- 每个文件的原始和归一化 sha256、权限、归一化方式；
- **每个目录及其权限**，包括空目录（本次修订新增，起因是反例 CX3：S08 依赖一个 0700 的空安装库，删除后旧清单察觉不到）；
- 每个仓库的 Git 提交号。

第二层场景还额外记录 `dependency_contract`。

`b3_fixtures.verify(root)` 会重新计算所有场景，返回 `{场景: [差异]}`，差异项的形式为 `files:…`、`dirs:…` 或 `git_heads:…`；没有差异时返回空字典。下游卡片在使用夹具之前，应先调用它确认夹具没有被改动。

## 场景矩阵

| 场景 | 等级 | 模拟的状态 | 预期 | 真实消费者 |
|---|---|---|---|---|
| S01-legacy-host-1.9.7 | 行为（读取） | 1.9.7 旧宿主；没有 install_exec 的 exec 事件；三种安装写法；旧式 apply | 现代 C5 应把每一种旧写法都报为 unknown | `core.Instance`、`live._strict_log` |
| S02a-wait-reply-no-ref-slot-present | 字段 | WAIT_REPLY，缺 send_ref | NO_REF 分支；调整工作在 A 线 | 状态字段 |
| S02b-wait-reply-no-ref-slot-missing | 字段 | WAIT_REPLY，send_ref 与 send_slot 都缺（"双缺字段"是本卡的解读，待 A 线确认） | 同上，并且不得借用租约或重发 | 状态字段 |
| S03-send-accepted-intent-crash | 字段 | SEND，accepted 且带 ref，但缺 send_ref；message 字段只是占位 | 只能轮询或核对 | 状态字段 |
| S04a-quota-blocked-valid-episode | 行为 | BLOCKED，带合法的 quota 恢复期（轮次 49） | 恢复期合法 | `recovery._check` |
| S04b-quota-blocked-corrupt-episode | 行为 | deadline 超出 8 天窗口 | 恢复期被拒 | `recovery._check` |
| S05-dirty-variants | 行为 | 修改、删除、未跟踪、密钥形态、受保护、被忽略 | 只列出非受保护的改动；密钥形态被标出 | `release._dirty`、`sync._scan_text` |
| S06-legacy-live-collection-v2 | 布局 | live-collection/2 记录与 B1 两键绑定 | 必须重新采集、不得授予 | 无（下一卡 FX-S06） |
| S07a–e-install-tx-* | 行为（锁扫描） | prepared-only、failed、partial、身份不符、hash 篡改；结构夹具，不带 baseline | 锁扫描依次拒绝、放行、拒绝、拒绝、拒绝 | `release._InstallLock._check_incomplete`、`_read_record` |
| S08-rollback-target-missing | 行为 | 有 v0.1 标签，安装库为空（0700） | `rollback_target_unverified`，HEAD 不变 | `release.rollback` |
| S09a-events-truncated | 行为 | 最后一条事件被截断 | corrupt | `live._strict_log` |
| S09b-events-duplicate-seq | 布局 | seq 重复；只有布局，缺 transport、适配器日志、sync 历史与 turns，不能单独驱动完整消费者 | 语法层 `live._strict_log` 单独接受；完整消费者 `live._capture_inputs` 与 `_revalidate_inputs` 判为 corrupt（见下文 SEQ 节） | 无（布局） |
| R01-review-expired | 行为 | 合成审查完成于 REUSE_DAYS 天之前 | 被拒：expired | `review.status` |
| R02-review-later-block | 行为 | 通过之后又登记了 block | 被拒：blocked | `review.status` |
| R03-verifier-pin-changed | 行为 | 宿主 verifier 被改钉 | 被拒：verifier_ 开头的原因；provenance 退回 unavailable，此时 synthetic 显示为默认值 False，这个 False 不代表非合成 | `review.status` |

## 事件 seq 一致性（SEQ 卡）

**更正：** 此前文档和测试中"目前没有消费者拒绝重复 seq"的说法是错的。只有语法层解析器 `live._strict_log` 单独接受重复 seq；完整消费者 `live._capture_inputs`（以及会重新采集的 `_revalidate_inputs`）会拒绝。batch2 与 PR33 的 live.py 中，这段规则完全相同。

S09 系列布局夹具达不到完整消费者，因此另建了一个最小的隔离真实 scaffold，位于 `wflib/selftest_b3_fixtures.py` 的 `_SEQ_CHILD`。它的构造方式如下：
- 一个 Git 项目，空的 turns，空的适配器日志（transport.json 中给出绝对路径），空的 sync 历史；
- 事件全部由真实的 `core.event` 写入；
- 每个 case 在独立子进程中运行，并使用独立的 `WF_LOCAL_DIR`；
- 事故类 case 只在真实的 `save()` 与真实的事件追加之间发送 SIGKILL，不做其他注入；
- 重复与倒序 case 是对真实 `core.event` 输出做字节编辑得到的，`core.event` 本身不会产生这两种形态。

| case | 事件 seq / state.seq | `_capture_inputs` | 回放（`_evaluate_snapshot`） | `_revalidate_inputs` |
|---|---|---|---|---|
| 连续 | 1..7 / 7 | collected | collected，authorization=unavailable | 对自身：stable |
| 重复 | 1..7,7 / 7 | corrupt：duplicate/out-of-order event sequence | 无快照 | 对基线：corrupt |
| 倒序 | 1,2,3,5,4,6,7 / 7 | corrupt：同上 | 无快照 | 对基线：corrupt |
| state 领先（真实 SIGKILL，rc=-9） | 1..7 / 8 | unknown：event sequence gap/order/state mismatch | 无快照 | 对基线：unknown |
| 事故续写（SIGKILL 后正常续写） | 1..7,9 / 9 | unknown：同上 | 无快照 | 对基线：unknown |
| 正常续写 | 1..8 / 8 | collected | collected，authorization=unavailable | 对旧基线：changed；对自身：stable |

**如何防止假覆盖：**
- "连续"与各负例使用同一个 scaffold，"连续"能完整产出 collected 快照，证明所有前置条件都已齐备；
- 每个负例都断言原因文本与来源（`workflow/events.jsonl`）完全一致，因此负例是被 seq 规则拦下的，而不是因为缺少 ts、type、state 或适配器日志。

**`check()` 在两条线上不同：**
- PR33 版的 `check()` 只有在事件中含带 `install_exec` 的 exec 时，才会经由 `_capture_inputs`。此时重复 seq 得到 `input_problem` corrupt；同一 scaffold 的连续日志得到的则是"安装元数据缺失"，不是 seq 原因。
- batch2 版的 `check()` 直接读日志、不经过采集，因此**不把它计为拒绝者**，只打印信息行。

**完整性策略（失败关闭，保持不变）：**
- `core.event` 先在状态锁内保存 state（seq 加一），再追加事件行。如果进程在这两步之间被杀，state.seq 就会领先于事件；core 之后可以照常续写，结果是留下内部缺号，并且 state 等于最大值（例如事件 1..7,9、state 9）。
- 这两种情形在没有可验证的恢复证据时，LIVE 一律判为 unknown，不授予。原因是它们与"人为删除了一条操作历史"在形态上无法区分。
- **不得**放宽为"严格递增且 state=max"：那条规则会把 1..7,9/9 当作合法，而删除第 8 条人工操作记录产生的正是完全相同的形态。
- **不得**重排、重编号或修补原日志来消除缺号。如果将来要放行这类情形，必须先有一个独立、可验证的恢复证据协议；这个协议目前不存在。

## 用法

```
python3 -m wflib.b3_fixtures "$(python3 -c 'import tempfile,pathlib;print(pathlib.Path(tempfile.mkdtemp()).resolve())')/fx" --tier-r
python3 -m wflib.selftest_b3_fixtures
```

定向测试**没有**登记进 `wf selftest` 总聚合。聚合器归 C 线所有，本卡不修改它。

## 已核实的结果

所有运行条件相同：Linux，非 root 专用用户，独立会话，私有 CPython 3.9.25（无启动 `.pth`）。

- **MAIN-CONTRACT-B3**：36/36。补跑的 Python 3.12.3 也是 36/36；此前文档中写的"3.12 未覆盖"有误，此处更正。
- **B3-MAIN-FXR-20261016-1**：
  - 代码提交 60ad8ae（tree 55591455）：3.9 为 45/45，3.12.3 为 45/45；
  - PR33 d01192d 叠加原 MAIN 两个提交和本提交（tree 4fd23a92）：3.9 为 45/45；
  - 用新测试运行原版生成器：34/37、rc=1，CX2、CX3 和依赖契约三个检查组全部失败，说明新测试能检出这些问题。

- **B3-MAIN-GUARD-20261016-1**：
  - 三个反例都先在 FX-R 版（5a3daed）上实际复现：对空的 `WF_LOCAL_DIR` 本体和子目录都生成了夹具；`mkdir("../…")` 越过根目录建了目录；审计前缀下的普通 `sys.executable` 孙进程 rc=0 并在根目录外写出了文件。真实第二层生成只启动了 git、runner.py、verifier.py 三类进程，共 22 次，白名单即据此确定。
  - 代码提交 5b71d6f（tree c2b111ed）：3.9 为 51/51，3.12.3 为 51/51；PR33 叠加线（tree d7aa8293）：3.9 为 51/51。
  - 用新测试运行 FX-R 版生成器：46/51、rc=1，三个反例对应的检查全部失败。此前正常的 36/36 和 45/45 都覆盖不到这三个反例。

- **B3-MAIN-SEQ-20261016-1**：
  - batch2 线代码 tree 508e7b65：3.9 为 57/57（51 项加 6 个 SEQ case；`check()` 不适用，只打信息行）；
  - PR33 叠加线代码 tree bb3ec267：3.9 为 58/58（多出 `check()` 一项）；
  - 每个 case 实际调用的 API、状态、原因与授权结果，都以 ℹ 信息行写在运行原文中。

**未覆盖：** macOS（m4）、总聚合自检。

## 已知边界

- **第二层的审计钩子是协作式的进程内控制，不是 OS 沙箱。**
  - 白名单内的 git、runner、verifier 自身的写入不受审计；其中 runner 与 verifier 的模板已被 FX-R 指纹钉住，git 的调用来自 Env 和 review 的代码，而 review 的函数体并未被钉住。
  - 一段刻意对抗的代码仍可以通过 ctypes 或私有模块（例如 `_posixsubprocess`）绕过钩子。这里的威胁模型只覆盖"测试夹具和 review 代码意外越界"，不覆盖恶意代码。
- **S07 是结构夹具**，只能用来检验安装锁扫描。完整的安装链属于下一卡 FX-TX。
- **"行为"等级只针对表中列出的那一个消费者函数。** 例如 S07b 被锁扫描视为已闭合，这并不代表 C5 能看到这次失败；后者属于 C-TX1。
- **SEQ 检查没有覆盖回放伪造的冻结快照。** 回放只读取冻结字节，不会重新做 seq 检查；目前的保护依赖采集与 `_revalidate_inputs`。另外，本卡没有拿一个被放宽的 live 去验证这些测试能否检出放宽。
- **静态指纹对注释和格式也敏感。** 这是有意的保守设计：只要源码文本有任何改动，都需要复审。

## 下一张小卡

### FX-S06：旧版实景记录的行为夹具（C 线；前置：PR33 中 G1 的改动合入）

**范围：** 只把 S06 从"布局"升级为"行为"，不扩展到别的场景。

**需要的接口：**
1. 生成侧：新增 `b3_fixtures.build_live_v2(root, *, legacy_tool_root)`。由调用方提供一份已解出的 b523be4 工具树，生成器在隔离子进程中用这份旧代码执行 `goals.run_checks`，产出真实的 live-collection/2 记录和 B1 两键记录。生成器自己不拉取代码，不访问网络。
2. 消费侧：C 线提供一个只读、稳定的入口，例如 `goals.live_record_status(inst, goal_id) -> {valid, state, layer, why}`。在入口提供之前，测试只能调用私有的 `_live_binding_valid`，因此需要像 FX-R 一样钉住它的签名。
3. 读取前先调用 `verify(root)`，确认夹具没有被改动。

**验收标准：**
- /2 记录与 B1 记录的层级都不超过 mechanical_pass，状态为 unsupported，永远不会出现 verified；
- 在同一夹具内对 /3 重新采集，结果必须受 G1 的生产信任登记约束：合成来源不会被授予；
- 篡改负例被拒绝。

### FX-TX：完整安装事务链的行为夹具（B 线主导、C 线协助；前置：C-TX1 合入）

**范围：** 只覆盖安装事务链，不涉及首次升级。

**需要的接口：**
1. 生成侧：在 B 线的非测试模块中提供 `install_fixtures.chain(env, *, kind, outcome, with_receipt)`。如果坚持复用 `selftest_install_history`，就必须像 FX-R 一样加依赖契约。`outcome` 取 completed、failed、partial、recovered 之一。
2. 生成器新增第三层（T）：在隔离子进程中用真实的 release 代码生成事务链，必须包含"无回执的失败事务"（CL-CT5 的形态）。
3. 消费侧：三个消费者逐一验证：
   - `release._InstallLock._check_incomplete`（锁扫描）；
   - `review.collect_install_history`（B 线的历史验证提供者）；
   - C-TX1 合入之后的 `live._capture_inputs` 与 `_evaluate_snapshot` 窗口枚举。

**验收标准：**
- 无回执的失败事务在 C5 窗口中可见，状态为 failed 或 unknown，并且导致不具备授权资格；
- 锁扫描的行为与 S07 保持一致；
- 在 C-TX1 之前和之后的 tree 上各运行一次，如实记录差异；
- 结果与 GPT 对 CL-CT5 的独立复现对齐。
