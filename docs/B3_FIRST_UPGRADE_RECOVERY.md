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
   - 计算要写入、要删除的文件，以及它们写入前的 hash。
   - 给出 `target_id`：安装根、dev/ino、当前 HEAD commit/tree、快照 hash 的摘要。
3. `apply`
   - 必须带 `--snapshot-sha256` 和 `--confirm-target`，后者是 plan 对当前树给出的 `target_id`。
   - 先以 O_EXCL 写下 journal/apply.json，记录计划。
   - 逐个文件：写临时文件、fsync、rename、恢复 mode。删除只针对写入前 hash 与记录一致的文件。
   - 只暂存记录过的路径，`write-tree` 必须等于冻结的 tree，再提交一个恢复提交（父提交是升级后的 HEAD），
     随后核对 HEAD tree 与工作树是否干净，最后写 completed.json。
   - 任何异常都先写 `failed-N.json`，再原样抛出，不吞掉错误。

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
- 续做时 HEAD 已移动、出现无法解释的改动，或某个文件既不是写入前的 hash 也不是目标 hash（`blocked_concurrent_change`）
- 已有 completed 记录但当前树不符

## 中断与重入

- 中断后：有 apply.json、没有 completed.json，HEAD 仍是升级后的提交。不存在“恢复成功”的假象。
- 再次执行 `apply`：
  - 必须用记录里的 `target_id`；
  - 只清理 runner 自己留下的 `.b3-recovery-*` 临时文件；
  - 每个文件只接受“写入前 hash”或“目标 hash”两种状态，否则阻塞，人处理后再续。
- 恢复提交已经写入、但 completed 记录缺失时，重入只补写 completed。

## 外部门控

旧入口没有 review-id，也不强制审查，非 `--auto` 时更是如此。`freeze --external-review-id --package`
只在快照里记下审查请求和包的 sha256，并标明 `enforced_by_old_host=false`。
这不代表旧宿主强制执行了审查。

## 演练范围与未验证部分

- 演练只在临时目录里进行：私有 HOME、WF_LOCAL_DIR、安装根，以及 P1/P2 两个项目，没有凭据，没有适配器。
- 候选包是集成树 `7b3b6cef…` 的 git archive。它的 VERSION 仍是 1.9.7，所以使用旧版自带的 `--allow-same`。
  `--skip-test` 只跳过旧安装器“先跑候选版本自检”这一步。本卡不分配版本号。
- `tests`：`python3 -m unittest -v wflib.selftest_b3_first_upgrade_recovery`，共 3 项：
  - 真实首次升级 → 新 self-rollback 失败 → 冻结树恢复，恢复后旧 self-check 通过；
  - 真实 SIGKILL 中断后续做；
  - 九类拒绝。
- 未验证：
  - 正式发布包（尚未产生）；
  - 带 `--auto` 的旧审查路径；
  - 真实宿主上正在运行的新版看板或驱动器进程（runner 不检查进程）；
  - macOS 路径别名，例如 `/var` 与 `/private/var`。runner 一律拒绝别名，请传入规范路径；
  - 旧代码是否兼容新版写下的状态（只报告，不判定）。
