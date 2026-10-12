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
Now I'll apply the PROJECT.md edits with exact-match replacements.
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 create_file]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 present_files]
【本轮结论】

**第 89 轮回报的解读**

- **1.6.9 已装好。** 构建 236/236，版本包 sha256 65e7e69c…，与我这边一致；审查 pass。
- **指纹不一致是 W52，不是缺陷。**
  - 第 87 轮执行端把 `workflow/state.json.bak-before-clearsendref-20261012` 留在了项目目录里。落地 v2.7 前，AP-2 把它自动存成了提交 cbea3da，所以仓库变成 10 个文件，指纹为 2b323923fccc518c。
  - 其余 9 个文件就是 v2.7 的内容。
  - 这个文件也会随同步进入公开仓库。state.json 不存放凭据，暴露的只是运行状态，但它本来就不该进版本库。
- **c6 没执行。** 原因是 c5 判据失败后，默认“失败后继续：否”就停下了。本轮改为“是”。
- **1.6.8 的修复在实景里起了作用。** 本轮有验收未通过，回报照常发出，没有被吞进 DONE。
- **审查的两条质量意见都成立，列入 1.7.0 修：** 一是 unreviewed 文件名的文档措辞；二是 sync 应该用结构化的原因字段，而不是靠文案后缀区分。
- **审查结论里有一处小误差：** 它说 v2.7 的 PROJECT.md“补记第 84–88 轮”，实际只补记到第 82 轮，第 84–89 轮的补记在本版 v2.8 里。

**本轮交付：collab-wf-dev-v2.8.zip**

版本包大小 101980 字节，sha256 f59f641b0e0dc0828f88a6bea0498d5dde506adab844104baaba27ff083ccabd。打包是确定性的：不压缩、固定时间戳，重复打包得到相同字节。

包内文件：

| 文件 | 字节数 | sha256 前 12 位 | 内容 |
|---|---|---|---|
| PROJECT.md | 74164 | 69b573ad6d3f | 补记第 84–89 轮：结论 45–49，台账 W49–W52（W46 关闭），下一步、待用户决策、已知限制、协作约定一并更新 |
| ISOLATION.md | 12357 | 7c45d7261269 | 新增，多项目隔离设计 v1，见下 |
| CHANGELOG.md | 11497 | 3650f80fd8cd | 加 v2.8 条目 |
| .gitignore | 727 | 61735e84000f | 忽略 `*.bak` 与 `*.bak-*` |
| workflow/policy.json | 1954 | f40bf52ea82e | 加 `landed_timeout_s` 2700。用 1.6.9 的 `policy.widening` 核对过，结果为空，不扩大权限，会自动批准 |
| RELEASE.json | 309 | 162580d7ee5b | 删除那个 state.json 备份。原内容仍可从提交 cbea3da 取回 |

落地前的验证：我在副本上复现了“v2.7 加那份备份”的仓库，用 1.6.9 的 `release.apply_zip` 实际落地。结果：
- PROJECT.md 校验无问题，备份已删除，工作区干净。
- 指纹为 0c183e4965457761（10 个文件），另行独立重算也一致。
- 前提：落地前项目目录里没有其他未跟踪的文件。如果 T88-1 的 r5 输出放在了项目树里，它会被自动存进版本库，指纹也会再变。

**ISOLATION.md 要点（回应乙的 8 项交付要求）**

- **原则：** 一套工具，加上显式的项目上下文。资源分三类：
  - 项目私有；
  - 全局但按项目寻址；
  - 物理共用（只能仲裁：排队并留痕）。
- **15 项逐一给了处理方式**，内容包括：
  - 会话按项目绑定：适配器配置加 projects 映射，工具传 `--conv`，conv_new 只改本项目那一条；只在 `per_project_conv` 开关打开后才这样传，保证兼容；
  - 本机目录分区；`WF_LOCAL_DIR` 保留，只用于演练隔离；
  - 发送槽与 arbiter.jsonl 的留痕格式；
  - 每个项目各自一个定时任务。P0-C 因此不再需要在半途截断命令。
- I1–I8 都有对应的工具自检和本机实测方法，每一条都有迁移和回滚步骤。
- **四个阶段：** S1 会话绑定（1.7.0）→ S2 按项目寻址 → S3 每项目调度 → S4 共用资源仲裁。S2 和 S3 可以对调。

关于用户决定 2 有一点建议：M1 由你本人最终验收后，再把 I1–I8 定成 M2 的可执行目标。在那之前，每一轮干净的验收都会停在 DONE，要靠执行端 wf kick 才继续。

【本机执行】
```
wf version
wf goals
git show --stat HEAD
wf fingerprint
```

【本轮回报清单】
1. 工具版本为 1.6.9。
2. `wf goals` 原文，应为 7/7。
3. `git show --stat HEAD` 原文：是 release v2.8 的提交，并含删除 state.json 备份的那一行。
4. 仓库指纹为 0c183e4965457761（10 个文件）。不一致时，请写明落地前 APPLY_DIRTY 自动保存了哪些文件。

【需要用户决定】
1. 执行端目录下明文凭据候选文件（含一个权限过宽的旧备份）的处置：删除、收紧权限或保留。由你本人决定。本项不阻塞。
2. M1 最终验收：只认你本人的原话。验收之后，建议把 I1–I8 定为 M2。本项不阻塞。
3. 两件事都需要你一句话：一是是否授权执行端解除它自己发起的暂停（P1-A；你发起的暂停仍只认你本人）；二是 amy-local 的暂停 6X2 是否解除。本项不阻塞。

【下一轮建议档位】
high

【驱动指令】
发布：collab-wf-dev-v2.8.zip
执行端 1：T88-1 的 r5 与回归输出放在 workflow/evidence/ 下（被忽略的目录）；已经放在项目树其他位置的，移过去（W52）。wf task done 写明最终位置
执行端 2：读 ISOLATION.md 第 3 节，确认适配器能否按约定实现：projects 映射、--conv 优先、conv_new --project 只改本项目、per_project_conv 开关、从默认对话退回时告警。有异议或需要改的接口形状写进 wf task done，作为 1.7.0 的输入
之后：等执行端
验收 1：c1 exit=0 包含 "collab-wf 1.6.9"
验收 2：c2 exit=0 包含 "目标进度 7 / 7"
验收 3：c3 exit=0 包含 "release v2.8"
验收 4：c4 exit=0 包含 "0c183e4965457761"
指纹：0c183e4965457761
超时：300
失败后继续：是