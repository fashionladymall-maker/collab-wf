# B3 纯合成状态夹具

2026-10-15，MAIN-CONTRACT-B3。生成器为 `wflib/b3_fixtures.py`，定向测试为 `wflib/selftest_b3_fixtures.py`。

夹具全部是合成的。它们用来证明逻辑和反例，**不是**生产证据，**不是**安装授权，也**不能**替代真实轮次。生成器不读取、不复制、不修改任何真实宿主状态：真实 local/、WorkBuddy 执行端、原 dirty 工作区和第 149 轮都不在它的输入里。

## 两层

**第一层（S，结构夹具）只用 Python 标准库，不导入任何 wflib 模块。**
- 同一根目录重复生成，产物按字节一致。
- 在不同根目录生成时，先把绝对路径替换为 `<ROOT>`，并把哈希封装记录中派生的 sha256 替换为 `<DERIVED>`，再比较归一化视图，结果一致。
- Git 仓库使用固定身份和固定日期（合成时钟 2026-01-15T08:00:00+00:00），因此提交号稳定。

**第二层（R，审查库状态）在子进程中运行 B2 现有的合成审查夹具 `wflib.selftest_review.Env`。**
- 其中的替身 runner 和 verifier 都自报 synthetic=true。
- 子进程的 `WF_LOCAL_DIR`、`HOME`、`TMPDIR` 都位于夹具根目录内。
- 子进程装有审计钩子，任何写到根目录之外的操作都直接拒绝。
- 这一层只保证语义可复现，不保证字节一致，因为里面有真实的 nonce 和真实时钟。

## 安全护栏

生成器在写入任何内容之前先检查根目录，遇到以下情况直接拒绝，并且不创建任何东西：

- 相对路径；
- 未完全解析的路径（含符号链接组件或 `..`；macOS 上需要传入 `Path(...).resolve()` 之后的 `/private/var/...` 形式）；
- 不在系统临时目录之内；
- 路径中任一级目录名为 `.workbuddy-ai`；
- 位于工具目录之内；
- 已存在且不为空。

此外：

- 所有写入都经过统一的写入器：只接受相对路径，使用 `O_EXCL` 或 `O_NOFOLLOW`，拒绝非普通文件。
- 临时目录用只读方式确定，因为 `tempfile.gettempdir()` 首次调用时会在临时目录里写一个探测文件，定向测试已经实际抓到过这个副作用。
- 密钥形态的值在运行时拼接，源码和文档里都不出现字面值（与 PR28 的做法一致）。

## 场景矩阵

"真实消费者"一列表示定向测试会调用该列中的真实代码去读取夹具。"无"表示目前只有布局，这是明确登记的缺口，不算通过。

| 场景 | 模拟的状态 | 预期语义 | 真实消费者 |
|---|---|---|---|
| S01-legacy-host-1.9.7 | 1.9.7 旧宿主；PR31 之前的 exec 事件没有 install_exec；`bin/wf self-update`、`python3 "…/wf.py" self-update`、`wf apply` 三种写法各一条，外加一条旧式 apply | 首次升级路径的输入；现代 C5 应把每一种旧安装写法都报为 unknown，不能丢弃 | `core.Instance`、`live._strict_log`；同时确认第二种写法不含子串 `wf self-update`（GPT-CT5 的形态） |
| S02a-wait-reply-no-ref-slot-present | WAIT_REPLY，缺 send_ref，send_slot 存在 | driver 进入 NO_REF 分支；B3 的 NO_REF 调整尚待 A 线 | 仅核对状态字段 |
| S02b-wait-reply-no-ref-slot-missing | WAIT_REPLY，send_ref 与 send_slot 都缺 | 同上，并留下缺 token 的证据；不得借用租约或重发 | 仅核对状态字段 |
| S03-send-accepted-intent-crash | SEND，send_intent.status=accepted 且带 ref，在写入 send_ref 之前崩溃 | 只能轮询或核对，不得当作未发送；B3 崩溃保护尚待 A 线 | 仅核对状态字段 |
| S04a-quota-blocked-valid-episode | BLOCKED，带合法的 quota recovery/1 恢复期；轮次为 49（不是 149） | 在合成时钟下恢复期合法；夹具不会恢复任何东西 | `recovery._check` 返回空 |
| S04b-quota-blocked-corrupt-episode | 同上，但 deadline 超出 8 天窗口 | 恢复期被拒绝 | `recovery._check` 返回非空 |
| S05-dirty-variants | 已跟踪文件被修改、被删除；未跟踪文件；未跟踪且含密钥形态内容；受保护路径 data/；被忽略的文件 | 只列出非受保护的改动；密钥形态文件被标出；被忽略文件不出现 | `release._dirty`、`sync._scan_text` |
| S06-legacy-live-collection-v2 | goals_state 中有 live-collection/2 记录，以及 B1 式两键绑定 | 必须按 live-collection/3 重新采集，不得授予 | 无（缺口） |
| S07a-install-tx-prepared-only | 安装库中只有 prepared | 安装锁扫描拒绝：未闭合 | `release._InstallLock._check_incomplete`、`release._read_record` |
| S07b-install-tx-failed-closed | prepared + failed（即 CL-CT5 的形态） | 锁扫描视为已闭合；C5 仍须在窗口中暴露它（未修缺陷 CL-CT5） | 同上 |
| S07c-install-tx-partial | prepared + partial | 锁扫描拒绝：不完整 | 同上 |
| S07d-install-tx-identity-mismatch | prepared.tx_id 与所在目录不符 | 锁扫描拒绝：身份损坏 | 同上 |
| S07e-install-tx-hash-tampered | failed 记录内容与 body_sha256 不符 | 锁扫描拒绝：hash 不符 | 同上 |
| S08-rollback-target-missing | Git 项目有 v0.1 标签，安装库存在但没有可恢复的基线 | `release.rollback` 拒绝（rollback_target_unverified），HEAD 与工作区不变 | `release.rollback` |
| S09a-events-truncated | events.jsonl 最后一条被截断 | 严格解析返回 corrupt | `live._strict_log` |
| S09b-events-duplicate-seq | 两条记录的 seq 都是 2 | 目前没有任何消费者拒绝重复 seq | 无（缺口；测试只做现状记录） |
| R01-review-expired | B2 合成审查完成于 REUSE_DAYS 天之前 | 当前不能用于安装 | `review.status`：reasons 含 expired |
| R02-review-later-block | B2 合成审查通过后，同一内容又登记了 block | 之前的通过失效 | `review.status`：reasons 含 blocked |
| R03-verifier-pin-changed | B2 合成审查通过后，宿主 verifier 的钉住值被改 | 失效：verifier 已变更 | `review.status`：reasons 以 verifier_ 开头 |

每个场景目录里都有一个 `FIXTURE.json`。它写明 synthetic、not_production_evidence、`authorization: none`，列出该场景模拟的状态、预期语义和对应的真实消费者，并为每个文件记录原始与归一化的 sha256、权限、归一化方式，以及每个仓库的 Git 提交号。根目录下的 `B3_FIXTURES_INDEX.json` 汇总全部场景的摘要。

## 用法

```
python3 -m wflib.b3_fixtures "$(python3 -c 'import tempfile,pathlib;print(pathlib.Path(tempfile.mkdtemp()).resolve())')/fx" --tier-r
python3 -m wflib.selftest_b3_fixtures
```

定向测试由父进程建立临时 `WF_LOCAL_DIR`，在子进程中运行（与各车道自检的方式相同）。它**没有**登记到 `wf selftest` 总聚合里；聚合器归 C 线所有，而 GPT 的聚合器登记分支尚未合入。

## 已核实的结果（本卡）

在 Linux 上，以非 root 专用用户运行，使用私有 CPython 3.9.25（无启动 `.pth`），独立会话，结果为 36/36。测试覆盖：护栏 7 项，结构与确定性 6 项，源码无密钥字面值 1 项，审计隔离 4 项，第一层真实消费者 14 项，第二层 3 项，以及第二层审计钩子 1 项。另外有 4 条信息行（ℹ）不计入通过数，记录的是缺口和现状。

上一次运行时，这套测试曾抓到三个真实问题，修正后才转为全绿：

- `tempfile` 的探测文件写到了根目录之外；
- 绑定根目录的记录在跨根目录比较时归一化不正确；
- R03 来源的 synthetic 默认值问题（见下文）。

**未覆盖：** macOS（m4）、目标机上的 Python 3.9、Python 3.12、总聚合自检。

## 已知边界与缺口

- **S07 只是结构夹具。** prepared 记录不带 baseline_sha256，只用于检验终态与身份扫描；它不是完整的安装链。完整的合成安装链请使用 B2 的 `selftest_install_history` 夹具。
- **S03 的 `send_intent.message` 只是占位**，不是 `_prepare_send` 的真实输出结构。A 线在 B3 中需要补齐。
- **S02 中"双缺字段"的含义是我的解读。** DEVELOPMENT.md 没有写明是哪两个字段，本卡按代码解读为 send_ref 与 send_slot，需要 A 线确认。S02、S03 目前只核对状态字段，没有运行 driver 的处理函数。
- **S06 只有布局。** 现有的 goals 消费者需要完整的批准、闭包和证据才能评估这类记录，本卡没有构造这些内容。
- **S09b 是现状记录：** `live._strict_log` 接受重复 seq，目前没有消费者拒绝它。
- **R03 的现状：** verifier 钉住值改变后，`review.status` 的来源退回 `unavailable`，其 synthetic 字段显示为默认值 False。这个 False **不代表非合成**。这与 P1-1 属于同一类问题（"非合成"由默认值或缺省得出），交 G1 的正向信任工作参考。本卡不修改 G1 的文件。
- **第二层依赖 B2 测试夹具 `selftest_review.Env` 的现有行为。** 夹具一旦改变，第二层的结果会随之改变。
- **macOS 上的 git 是 xcrun shim。** 第一层把 HOME 指向根目录内一个不存在的目录，这在 m4 上没有验证过。
