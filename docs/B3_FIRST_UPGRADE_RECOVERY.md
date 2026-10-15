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
   - **这项检查只证明旧源自洽，不证明它是官方或原生的旧树。** 冻结后由操作人另行保存的 manifest sha256
     绑定的是"被冻结的这些字节"，不是它们的来源。L4 调用方仍需在 runner 之外绑定来源身份，例如核对冻结的
     HEAD tree 是否等于原生树 `fafef7e6…`。本 runner 不扩大这项保证。
   - 快照路径与全部保护根做双向边界检查（见下文"保护状态边界"），在创建快照目录之前完成。
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
   - 在读取 journal、任何清理或写入之前，核对快照与全部保护根的双向边界。apply 以 plan 开头，因此同样受此约束。
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

## 保护状态边界

**运行状态根**包括：
- 安装根下的 `local/`；
- WF_LOCAL_DIR，必须是非空的绝对路径，否则拒绝（`wf_local_not_absolute`），不借进程 cwd 推导；
- registry 中每个已登记项目的 `workflow/`。

每个运行状态根都按配置原样保留词法形式（normpath 与 abspath），比较时再生成规范形式（realpath）。两种形式都参与下面每一项检查，所以别名不会在比较之前被抹掉。

在 freeze、plan、apply 中，以下检查都发生在任何 mkdir、清理、journal、代码写删、暂存和提交之前。apply 一开始就会先执行 plan。

1. **运行状态根不得与安装代码冲突（`protected_state_overlaps_install_code`）。** 唯一的例外是词法上的 `安装根/local/`。除此之外，任何运行状态根的任一形式只要位于安装根之内、等于安装根、或包含安装根，都在前置阶段拒绝。
   - 因此，freeze 也不会把已识别的运行状态当作普通的被跟踪代码去认证和冻结。
   - 冲突一律拒绝，runner 不会移动或删除任何状态，也不会在写入之后再把结论改成"已变化"。
   - 安装根本身只参与快照的不交叠规则；它不意味着禁止一切代码写入。
2. **恢复写集不得触及运行状态根（`restore_writes_protected_state`）。** 从可信来源推导出 writes、deletes、登记的临时文件，以及需要新建的父目录，逐一与全部运行状态根做词法和规范两种形式的比对。在第 1 项成立时，这一项属于纵深防御；测试用真实推导出的写集直接验证了它。
3. **快照不得与安装根或任一运行状态根互相包含（`snapshot_overlaps_protected_state`），两个方向都检查。** 快照包括目录本身、blobs 和 journal。

**registry 协议**：顶层必须是对象，并且必须带有 `projects` 列表；每个条目都必须有非空的绝对路径 `root`。
- 以下情况都拒绝（`registry_unreadable`）：`{}`、缺少 `projects`、`projects` 不是列表、root 为空、root 是相对路径、root 不是字符串、JSON 格式错误、文件不可读。
- registry 不是普通文件时拒绝（`registry_not_regular`）。
- `name`、`added` 等其他字段不作要求，因此 `core.register` 写出的正常 registry 保持兼容。

**此前的真实反例**（真实旧安装器演练，候选树为替身 `3e72a969…`）：
- **WF_LOCAL_DIR 指向安装根下的 `docs/`**：plan 和 apply 都 exit 0，结果为 restored，docs 下 5 个文件被改写或删除（BATCH2_CONTRACT.json、C5_HISTORY.md、DEVELOPMENT.md、DRILL.md、DRIVER_REVIEW.md），却仍报告 `never_written_by_runner`。
- **registry 为 `{}`、root 为相对路径、root 为空串**：freeze、plan、apply 都 exit 0，其中 `{}` 实际完成了恢复。
- **WF_LOCAL_DIR 配置为快照内部的一个别名**，它的规范目标是真实的外部 LOCAL：plan 和 apply 都 exit 0，并往快照里写入了 journal。在这个例子中，外部 LOCAL 本身并没有被 runner 写入。
- **更早的反例**：快照指向合成 WF_LOCAL_DIR 的子目录时，freeze 写入了 69 个文件；合法快照被移入 WF_LOCAL_DIR 后，apply 在其中写入了 journal。

## 字节码缓存（不认证）

`__pycache__` 下的 `*.pyc` 无法与冻结源码绑定：
- unchecked-hash 格式的 pyc（flags=1）在导入时根本不读源码；
- timestamp 格式的 pyc 只比较头部记录的 mtime 和 size，这两个字段可以伪造；
- checked-hash 格式只校验源码哈希，不校验其中的字节码是否就是这份源码编译出来的；
- `-B` 和 `PYTHONDONTWRITEBYTECODE` 只是不再写新缓存，仍会读取已有缓存，不能当作修复。

因此 runner 对缓存的处理是：
- 不认证：`code_recovery` 只覆盖源码文件的集合、字节和可执行位；
- 逐个报告：`inventory.bytecode_cache_unverified` 列出每个缓存文件的路径与 sha256；
- 不删除：未知字节一律保留，由操作人决定；
- 只要存在任何缓存，`restart` 结论就是 `not_established`，阻断项为 `unbound_bytecode_cache:N`。

真实反例：恢复完成后，放入用真实 `py_compile` 生成的 UNCHECKED_HASH 格式 driver 缓存，其中带有一个合成标记。
此时源码与快照完全一致，旧 self-check 也返回 0，但新进程 import driver 会输出 `executed-unbound-bytecode`，
加 `-B` 同样输出。现在 runner 报出这个缓存并阻断重启；只有在操作人删除缓存之后，标记才无法再执行，阻断项才会消失。

## 四项结论（互相独立）

| 结论 | 含义 |
|---|---|
| `code_recovery` | `restorable` / `resume_interrupted_apply` / `already_restored`；其余情况都是拒绝 |
| `state_preservation` | `never_written_by_runner`：runner 不写 `local/`、WF_LOCAL_DIR、已登记项目的 workflow；演练中逐字节核对恢复前后相同。这个结论以"保护状态边界"中的前置检查为前提：一旦运行状态根与安装代码冲突、恢复写集触及运行状态根，或快照与保护根交叠，plan 和 apply 都会在写入之前拒绝，根本不会给出这个结论 |
| `schema_compat` | 比较冻结时与现在的运行状态清单。有新增或改动时为 `unverified`，并列出每个文件；不宣称旧代码能读新状态 |
| `restart` | `auto_start=false`，runner 从不启动旧执行体。只有在 `blockers` 为空时才是 `operator_may_restart_old_code`，否则为 `not_established`。阻断项包括：代码未恢复、运行状态自冻结后有变化、安装根下存在任何未认证的字节码缓存（`unbound_bytecode_cache:N`） |

演练中，旧安装器自己就会新建 `local/locks/self-update.lock`，所以 `schema_compat` 如实报 `unverified`。

## 拒绝条件（返回码 2，拒绝的那一步不写任何东西）

- 路径不是绝对路径，或有别名、符号链接
- 快照与安装根交叠，或快照目录已存在
- 快照与任一保护根双向交叠（`snapshot_overlaps_protected_state`）
- 运行状态根与安装代码冲突（`protected_state_overlaps_install_code`），或恢复写集触及运行状态根（`restore_writes_protected_state`）
- WF_LOCAL_DIR 不是非空绝对路径（`wf_local_not_absolute`）
- registry 不是普通文件（`registry_not_regular`），或违反协议、无法读取（`registry_unreadable`）
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
  - 对全部文件核对（内容，git mode）、类型与集合，只接受计划能够证明的状态：
    - 计划内的写入：只能是（写入前 hash，基线 mode）或（目标 hash，目标 mode）；
    - 计划内的删除：只能是（写入前 hash，基线 mode）或已不存在；
    - 其余基线文件：必须与基线完全相同。

    任何不符都在清理、写入、删除、暂存和提交之前拒绝。此前的反例是：只把不在 writes 中的 VERSION 改为 0755，
    plan 仍判为可续做，apply 先把恢复提交写到了旧 tree，最后才因 `M VERSION` 失败。
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
- `tests`：`python3 -m unittest -v wflib.selftest_b3_first_upgrade_recovery`，共 16 项：
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
  - freeze 只接受合格旧源（VERSION 哈希、未列出文件、版本不符、冻结时的被忽略残留都拒绝，且不创建快照）；
  - 快照与保护状态的双向边界：freeze 到 WF_LOCAL 或项目 workflow 中、WF_LOCAL 位于快照之内、合法快照移入
    WF_LOCAL 或项目 workflow，在 freeze、plan、apply 三处都受控拒绝，代码、HEAD、journal 与保护状态完全不变；
  - 未认证的字节码缓存：用当前解释器真实编译 UNCHECKED_HASH 缓存，并真实 import（加与不加 `-B` 都会执行标记）；
    runner 报告并保留该缓存、不写入任何东西、阻断重启；删除缓存后标记不再执行、阻断项消失；
  - 续做预检的 mode 检查：VERSION 仅改为 0755，或计划内文件内容已是目标但可执行位不对，plan 与 apply 都拒绝，
    完整字节、mode、HEAD 与 journal 均不变；复原后续做完成；
  - 运行状态根与安装代码冲突：WF_LOCAL_DIR 等于代码目录 `docs/`、等于安装根、包含安装根、或是规范目标落入
    `wflib/` 的外部别名，以及经真实旧版 `wf init` 登记的项目其 `workflow` 指向 `docs/`；这些情况在 freeze、
    plan、apply 中都前置拒绝，完整清单不变。另外用真实推导写集直接验证写集检查；正例中 WF_LOCAL_DIR 等于
    `安装根/local` 与正常的外部 LOCAL 都能照常恢复；
  - registry 协议：11 种坏形态（含 chmod 000 与符号链接）在 freeze、plan、apply 中都零写入拒绝；
    由真实旧版 `wf init` 写出的正常 registry 能照常恢复；
  - 词法别名：WF_LOCAL_DIR 或已登记项目 root 是快照内部的别名时，plan 与 apply 都拒绝，不写入 journal，
    真实 LOCAL 也不被改动。
- 未验证：
  - 正式发布包（尚未产生）；
  - 带 `--auto` 的旧审查路径；
  - 真实宿主上正在运行的新版看板或驱动器进程（runner 不检查进程）；
  - macOS（m4）：本卡没有在 macOS 上运行任何测试。路径别名，例如 `/var` 与 `/private/var`，runner 一律拒绝，请传入规范路径；
  - 旧代码是否兼容新版写下的状态（只报告，不判定）。
