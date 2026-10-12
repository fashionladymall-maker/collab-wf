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
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 present_files]
【本轮结论】

**第 91 轮核对**

已与公开同步仓库（提交 6236bab）对过：
- 1.7.0 已装上，安装后的工具树与我这边构建的逐字节一致。
- 第 90 轮验收通过。
- 指纹仍是 0c183e4965457761。
- 执行端已完成 `wf pin` 和 S1 迁移，按约定的顺序做的。

**有两处没闭合，本轮交给执行端核对：**

1. **amy-local 的对话 id 前后不一致。**
   - conv_new 输出里是 `d14def32-…-5c4838ba4143`。
   - 适配器配置里登记的是 `d14def32-…-5c794870ad88`。
   - 前者的末 10 位 `4838ba4143` 正好是 collab-wf-dev 对话 id 的末 10 位，像是抄写时混进去的。但到底哪个是真的，要看原文。
2. **I1 的“发送用本项目对话”只做了不外发的演练。** 本条回报本身就是打开 `per_project_conv` 后的第一次真实发送，适配器动作日志里应该能看到它用的是哪个对话。

**问题的来源**

第 89–91 轮的回报都是 DONE 状态下手写，再经 `wf kick --message` 原样发出的。所以这几份回报没有终端原文，也没有“需要注意”。上面的 id 不一致，以及第 90 轮那段格式对不上的“原文”，都出在这里。根因在工具：DONE 下 `wf report` 用不了，而 `wf kick` 又必须带 `--message`。这记为 W53，由本轮的 1.7.1 修复。

**本轮交付两个文件**

1. **collab-wf-dev-v2.9.zip**（项目版本，落地）
   - 103790 字节，sha256 9846664ad4abb9aa28246261ed06ea83efb9ac4f55c4b3aeed8a63b6b185345f。打包是确定性的，包里有 3 个文件。
   - PROJECT.md 补记第 90–91 轮：结论 50–52 与 W53；P1-C、T88-2、T88-3 标为完成；amy-local 的 id 不一致写成待核对。
   - ISOLATION.md 写入选对话的完整顺序和第 91 轮的迁移结果。阶段版本号顺延：S2 为 1.7.2，S3 为 1.7.3，S4 为 1.7.4。
   - 我在 v2.8 的复现仓库上，用 1.7.1 的 `apply_zip` 实际落地了一遍：校验无问题，工作区干净。落地后指纹为 95674b23bab4790e，共 10 个文件。

2. **build_wf_171.py**（工具 1.7.1）
   - 13413 字节，sha256 4bbe1f385435f03b30cd9e9d12acb74a34e09e8b218576e0e8798db04f81b587。
   - 我在与已装版本逐字节相同的 1.7.0 上验证过：构建 242/242，用 1.7.0 自动安装，底线检查和升级核验都通过。floor.py 没有改动。
   - 改动：DONE 下 `wf report` 可以用了。`wf kick --kind report` 不带 `--message` 时，由本轮证据生成标准回报，与驱动器自动回报用的是同一个函数（含 ② 原文、④ 需要注意、执行端备注和待办结果）。任务类的 `kick` 仍然必须带 `--message`。DONE 状态和待办完成时的提示，也改为推荐这条命令。

构建时有一次自检失败：新增测试在 `kick` 已经发出之后才去检查“任务类 kick 必须带 `--message`”，这是测试本身写错了顺序，调整后通过。

【本机执行】
```
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-091/delivered/build_wf_171.py --out workflow/outbox
wf self-update --auto workflow/outbox/collab-wf-1.7.1.zip
wf version
wf self-check
wf fingerprint
wf goals
```

【本轮回报清单】
1. 构建通过，自检 242/242，成员集合一致，floor.py 未改动；记下版本包的 sha256。
2. 驱动器自动安装 1.7.1，升级核验通过；审查结论以原文为准。
3. 工具版本为 1.7.1。
4. self-check 完全一致。
5. 落地 v2.9 后仓库指纹为 95674b23bab4790e（10 个文件）。
6. `wf goals` 原文，应为 7/7。

【需要用户决定】
1. 执行端目录下的明文凭据候选文件（其中一个是权限过宽的旧备份）要删除、收紧权限还是保留，由你本人决定。本项不阻塞。
2. M1 最终验收只认你本人的原话。验收之后，建议把 I1–I8 定为 M2。本项不阻塞。
3. 两件事都需要你一句话：一是是否授权执行端解除它自己发起的暂停（你发起的暂停仍只认你本人）；二是 amy-local 的暂停 6X2 是否解除。本项不阻塞。

【下一轮建议档位】
high

【驱动指令】
发布：collab-wf-dev-v2.9.zip
执行端 1：核对 amy-local 的对话 id：conv_new 的输出（…5c4838ba4143）与适配器配置登记的（…5c794870ad88）不一致。从适配器动作日志里 conv_new --project amy-local 那一条，以及适配器配置里 projects.amy-local 那一项，逐字复制原文（只含对话 id 与时间，不含凭据）。如果两者不同，以通道里真实存在的对话为准修正配置，并写明原因
执行端 2：I1 真实发送核对：在适配器动作日志里找到第 91 轮回报与本轮之后第一次发送的记录，逐字复制它们所用的对话（应为 collab-wf-dev 登记的对话）与告警字段（应无 conv_from_default）；再用 wf audit 查第 91、92 轮有没有 ADAPTER_WARNING
执行端 3：以上两项做完、1.7.1 装好后，用 wf kick --kind report（不带 --message）发回报，说明先用 wf note --kind info 写。这一项本身就是 1.7.1 的实景验收：回报应带 ② 终端原文与 ④ 需要注意
之后：等执行端
验收 1：c1 exit=0 包含 "构建通过：collab-wf 1.7.1，自检 242/242"
验收 2：c2 exit=0 包含 "升级核验通过"
验收 3：c3 exit=0 包含 "collab-wf 1.7.1"
验收 4：c4 exit=0 包含 "完全一致"
验收 5：c5 exit=0 包含 "95674b23bab4790e"
验收 6：c6 exit=0 包含 "目标进度 7 / 7"
指纹：95674b23bab4790e
超时：1800