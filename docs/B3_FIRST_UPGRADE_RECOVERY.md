# B3 首次升级的代码恢复：tools/b3_first_upgrade_recovery.py

## 缺口

1.9.7 的 `wf self-update` 把新包复制到工具目录，提交并打 `wf-v<版本>` 标签。它不写 `local/install`
事务，也不写 verified baseline。新版 `wf self-rollback` 只按已登记的 baseline 恢复，标签只是名字；
`local/install` 不存在时直接失败。实测：`install_boundary:install_store:FileNotFoundError …/local/install`。
标签可以被移动或重建，也不能作为旧代码的证据。所以从旧安装器第一次升级到新版之后，
没有一条经过核验的路可以回到旧代码。

## 旧版来源身份

- 原生提交 `818ecd959f86132c4292be9e426ae1aa2435cf4e` 不能从远端取回（`not our ref`）。
- `base/tool-818ecd9` 上的 `fd3f22d04c742094db2a6c0c38a8f98f444684b8` 写明 Native source 818ecd95、
  Native tree `fafef7e6007d544aa897890b7271eae5cf29b051`。它的实际 git tree 就是 `fafef7e6…`。
  tree id 按内容寻址，所以内容与原生树一致。
- 这棵树的 67 个文件与其中 1.9.7 自带的 MANIFEST.json 逐个 sha256 一致。
- 演练里的安装根由这棵树的 git 对象解出，再用旧版自己的 `wf.py self-init` 初始化，
  得到的 HEAD tree 必须等于 `fafef7e6…`。没有用新版安装器去模拟旧目录。

## 设计

外部 runner，只用标准库，不导入 wflib，不改安装器，也不新增任何审查绕过参数。

1. `freeze`（升级前运行）
   - 要求安装根是普通的 git 根，绝对路径，路径上没有任何符号链接或别名，
     工作树干净（包括未跟踪文件），`local/`、`.git/` 都不被跟踪。
   - 每个跟踪文件只允许 100644 或 100755；符号链接、子模块、冲突一律拒绝。
   - 工作树字节必须与索引里的 blob 一致。
   - **旧源必须合格**：MANIFEST.json 与 VERSION 都被跟踪；清单列出的每个文件都存在且 sha256 一致；
     除 MANIFEST.json 外每个被跟踪文件都在清单里；清单 version 等于 VERSION。任一不符即
     `old_source_manifest_mismatch`，快照目录不会被创建（此前 VERSION 哈希为 64 个 0 仍被冻结）。
   - **完整磁盘清单**必须与被跟踪文件一致，不受 ignore 规则影响；被忽略的代码残留即 `unexplained_code_residue`。
   - 在安装根之外、此前不存在的快照目录里写入：
     - manifest.json（路径、mode、大小、sha256、git blob，HEAD commit/tree，root 的 dev/ino，
       运行状态清单，外部审查记录）；
     - 按 sha256 命名的只读 blob。
   - 输出 manifest 的 sha256。操作人要把它另行保存，`plan`/`apply` 必须显式传入。
     快照目录本身不能自证。
2. `plan`（默认动作，只读）
   - 核对 manifest 的 hash、schema、路径，以及每个 blob 的 sha256/大小/git blob。
   - 核对目标身份：路径与 dev/ino 都必须一致，且与快照不交叠。
   - 核对当前树干净（续做时除外，见下）。
   - 恢复计划只从可信来源推导：经 `--snapshot-sha256` 绑定的冻结 manifest，加上当前 HEAD tree 的
     git 内容寻址对象（`ls-tree` 与 `cat-file --batch`，不读索引、不读磁盘）。写入前 hash 取自这些对象。
   - 用独立的完整磁盘清单对账代码（见下文"代码对账"），不依赖 git status、索引或 ignore 规则。
   - 给出 `target_id`：安装根、dev/ino、当前 HEAD commit/tree、快照 hash 的摘要。
3. `apply`
   - 必须带 `--snapshot-sha256` 和 `--confirm-target`，后者是 plan 对当前树给出的 `target_id`。
   - 先以 O_EXCL|O_NOFOLLOW 在真实目录 journal/ 中写下 apply.json，记录推导出的计划及其 `plan_sha256`。
   - 逐个文件：写入登记过的临时文件（名称由快照 hash、基线 tree 与路径确定性推导）、fsync、rename、恢复 mode。
     删除只针对写入前 hash 与推导计划一致的文件。
   - 只暂存记录过的路径，`write-tree` 必须等于冻结的 tree，再提交一个恢复提交（父提交是升级后的 HEAD），
     随后核对 HEAD tree、工作树干净，并用完整磁盘清单逐字节、逐 mode 对账冻结代码，全部通过才写 completed.json。
   - 所有验证都在第一次写、删、清理之前完成；验证阶段的拒绝不写任何东西（包括 journal）。
     写入阶段出现异常时，先写 `failed-N.json`，再原样抛出，不吞掉错误。

## journal 的信任模型

journal 是可变、不可信的恢复计划副本，不能凭它自己的 JSON 当授权。续做前逐项核对：

- journal/ 必须是真实目录，其中每个条目都是普通文件（符号链接、未知条目都拒绝，`journal_not_directory`/`journal_corrupt`）；
  快照的 blobs/ 也必须是真实目录；
- apply.json 的键集合、schema、state、类型精确匹配；坏 JSON 受控拒绝（`journal_corrupt`），零写；
- 每个路径都过同一条规则：拒绝绝对路径、`..`、空段、反斜杠、NUL 以及以 `local/`、`.git/` 开头的保护路径
  （`journal_unsafe_path`）；重复路径或同一路径既写又删（`journal_duplicate_path`）；
- `snapshot_sha256` 与传入值一致；`base_head` 等于当前 HEAD；`target_id` 按安装根、dev/ino、HEAD、快照重新计算后一致；
- writes/deletes 必须与上文推导出的计划逐字段相等，`plan_sha256` 也必须相等（`journal_plan_mismatch`）。
  因此只改 journal 不能增加、删除或改写任何一项写入或删除。

## 代码对账（独立于 git）

- 对安装根做完整遍历，跳过且只跳过顶层 `.git/` 与 `local/`（受保护的运行状态）。
- 允许的非代码只有一种：位于 `__pycache__` 目录中的 `*.pyc` 解释器缓存（没有源文件时不能被导入，旧 self-check 也忽略它）。
- 其余每个普通文件都必须是对应树中的代码文件；符号链接与其他非普通文件一律算残留。
- `already_restored` 必须同时满足：HEAD tree 等于冻结 tree，且磁盘上的代码文件集合、每个文件的 sha256 与可执行位
  都与冻结清单完全一致。只看 HEAD 或索引不够：恢复后修改被跟踪的 `driver.py`、或被 `.git/info/exclude` 隐藏的
  `stray-code.py`，都会得到 `restored_disk_drift`，而不是 `already_restored`。
- 残留与漂移只报告、拒绝，从不自动删除；由操作人决定。

## 四项结论（互相独立）

| 结论 | 含义 |
|---|---|
| `code_recovery` | `restorable` / `resume_interrupted_apply` / `already_restored`；其余情况都是拒绝 |
| `state_preservation` | `never_written_by_runner`：runner 不写 `local/`、WF_LOCAL_DIR、已登记项目的 workflow；演练中逐字节核对恢复前后相同 |
| `schema_compat` | 比较冻结时与现在的运行状态清单。有新增或改动时为 `unverified`，并列出每个文件；不宣称旧代码能读新状态 |
| `restart` | `auto_start=false`，runner 从不启动旧执行体。只有代码已恢复、且运行状态自冻结后完全没变时，才是 `operator_may_restart_old_code`；否则为 `not_established` |

演练中，旧安装器自己就会新建 `local/locks/self-update.lock`，所以 `schema_compat` 如实报 `unverified`。

## 拒绝条件（返回码 2，拒绝的那一步不写任何东西）

- 路径不是绝对路径，或有别名、符号链接
- 快照与安装根交叠，或快照目录已存在
- 不是普通 git 根，或有 shallow/alternates/commondir
- 工作树有未知改动或未跟踪文件
- 保护状态被跟踪
- 不支持的 mode
- 快照 manifest 的 hash、schema、路径不对，或 blob 缺失、损坏
- 安装根身份不符
- `--confirm-target` 不符
- 续做时 HEAD 已移动、出现无法解释的改动（`blocked_unexplained_change`），或某个计划内文件既不是写入前的 hash
  也不是目标 hash（`blocked_concurrent_change`）
- 已有 completed 记录但当前状态不是已恢复（`completed_record_but_target_differs`）
- journal 不可信（`journal_not_directory`、`journal_corrupt`、`journal_unsafe_path`、`journal_duplicate_path`、
  `journal_plan_mismatch`），或快照 blobs 不是真实目录
- 旧源不合格（`old_source_manifest_missing`、`old_source_manifest_corrupt`、`old_source_manifest_mismatch`）
- 完整磁盘清单出现代码残留或漂移（`unexplained_code_residue`、`restored_disk_drift`）

## 中断与重入

- 中断后：有 apply.json、没有 completed.json，HEAD 仍是升级后的提交。不存在“恢复成功”的假象。
- 再次执行 `apply`：
  - 必须用记录里的 `target_id`；
  - 只清理登记过的临时文件：名称必须是为某个计划内写入推导出的那一个，内容必须是该文件目标 blob 的前缀；
    其他任何文件（包括匹配旧 glob `.b3-recovery-*` 的用户文件）都算残留，拒绝且不删除；
  - 清理发生在全部验证通过之后；
  - 每个文件只接受“写入前 hash”或“目标 hash”两种状态，否则阻塞，人处理后再续。
- 恢复提交已经写入、但 completed 记录缺失时，重入只补写 completed。

## 外部门控

旧入口没有 review-id，也不强制审查，非 `--auto` 时更是如此。`freeze --external-review-id --package`
只在快照里记下审查请求和包的 sha256，并标明 `enforced_by_old_host=false`。
这不代表旧宿主强制执行了审查。

## 演练范围与未验证部分

- 演练只在临时目录里进行：私有 HOME、WF_LOCAL_DIR、安装根，以及 P1/P2 两个项目，没有凭据，没有适配器。
- 候选包是集成树 `7b3b6cef…` 的 git archive。它的 VERSION 仍是 1.9.7，所以使用旧版自带的 `--allow-same`。
  B3-MAIN-UPGRADE-FIX-20261016-1 返工时，`7b3b6cef…` 不在远端任何引用中，复跑使用
  `B3_CANDIDATE_TREE=3e72a969241e4b095be5058615538a5f494753af`（PR33/d01192d 的 tree，同样含 `wflib/install_history.py`，VERSION 1.9.7）。
  `--skip-test` 只跳过旧安装器“先跑候选版本自检”这一步。本卡不分配版本号。
- `tests`：`python3 -m unittest -v wflib.selftest_b3_first_upgrade_recovery`，共 10 项：
  - 真实首次升级 → 新 self-rollback 失败 → 冻结树恢复，恢复后旧 self-check 通过；
  - 真实 SIGKILL 中断后续做（并发修改在全量验证阶段被拒，零写）；
  - 九类拒绝；
  - 不可信 journal：穿越、绝对、保护、重复、既写又删、post/pre 篡改、删项、坏 JSON、apply.json 符号链接，
    每项 plan 与 apply 都拒绝且所有字节不变，随后真实 journal 仍能续做完成；
  - journal 目录符号链接、blobs 目录符号链接、快照目录别名；
  - 被忽略的代码残留在恢复前后都不被隐藏、不被删除；`__pycache__/*.pyc` 允许；
  - 完成后被跟踪文件内容或可执行位漂移，不再报告已恢复；
  - 只清理登记过的临时文件（用户的 `.b3-recovery-*` 文件被保留）；
  - 真实 SIGKILL 落在临时文件写完与 rename 之间：登记临时文件被识别、篡改后被拒、复原后续做完成；
  - freeze 只接受合格旧源（VERSION 哈希、未列出文件、版本不符、冻结时的被忽略残留都拒绝，且不创建快照）。
- 未验证：
  - 正式发布包（尚未产生）；
  - 带 `--auto` 的旧审查路径；
  - 真实宿主上正在运行的新版看板或驱动器进程（runner 不检查进程）；
  - macOS 路径别名，例如 `/var` 与 `/private/var`。runner 一律拒绝别名，请传入规范路径；
  - 旧代码是否兼容新版写下的状态（只报告，不判定）。
