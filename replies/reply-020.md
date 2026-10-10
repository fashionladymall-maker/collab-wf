[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 present_files]
【本轮结论】
1. 第 1 条命令构建失败，原因在我：第 19 轮我用同一个版本号 1.5.7 交付了内容不同的构建器，而执行端已经装好了第 18 轮那份只含通知的 1.5.7，所以克隆停在 1.5.7、不是 1.5.6。构建器按设计中止，没有造成任何改动。这条记为 W29，今后已交付的版本号不再复用。第 19 轮那份的内容（tick 内等回复、判据失败不再误报、REVIEW.md）全部并入 1.5.8。
2. 本轮交付两个文件：
   - **collab-wf-dev-v0.7.zip**：项目版本包，驱动器会自动落地。
   - **build_wf_158.py**：1.5.8 构建器，本轮第 1 条命令就由驱动器自己构建它。
   
   1.5.8 的内容：
   - **自动安装工具版本。** 驱动器只能执行这一种形式：`wf self-update --auto workflow/outbox/collab-wf-X.Y.Z.zip`，而且要 policy 开启 auto_self_update、白名单也放行它才行。安装前，由当前已安装的代码做三项检查：
     - 版本改动了护栏文件（policy.py、release.py、asks.py、anomalies.json），就拒绝自动安装，停下等用户。
     - 配置了另一个模型系列审查时，先审 diff；结论是 block 也拒绝。
     - `--auto` 不允许跳过自检。
     
     安装后的逐个文件核验和失败自动回滚照旧。
   - **tick 内等回复。** 同第 19 轮的方案。有了它，唤醒间隔回到 300 秒也不影响效率；执行端改的 60 秒是安全的（launchd 不会让同一个任务重叠运行，驱动器另有项目锁），保留与否都可以。
   - **判据失败不再误报"退出码 0"。**
   - **新增 docs/REVIEW.md。** 新增工具版本的审查参数 `--kind tool`。
   - **自检新增 4 项**，预计 184 项。
   
   它在模拟的 1.5.7 树上跑通了 18 处锚点和新文件。policy.py 与 release.py 自 1.5.1 起没有改过，锚点取自本轮读到的原文。
3. 指纹不一致的原因已经确认：我按本轮读到的 policy.json 原文重建，sha256 与执行端报的 1c08f62d4195 一致，重算出的指纹正是 6067226cdfe27d0b。这条记为 W25。v0.7 落地后的指纹应为 e399837c902f6b51。
4. 1.5.8 改动了护栏文件，按它自己引入的规则，这是最后一次需要手动安装。执行端的步骤：
   - 第 1 条命令构建通过后，运行 `wf self-update workflow/outbox/collab-wf-1.5.8.zip`。
   - 在 workflow/policy.json 的 allow 里加入这一行：
     `"^wf self-update --auto workflow/outbox/collab-wf-[0-9]+\\.[0-9]+\\.[0-9]+\\.zip$",`
     再在顶层加入：
     `"auto_self_update": true,`
   - 用 `wf ask` 取决定码，按用户第 19 轮的原话批准。
   - 把构建、安装和白名单批准的结果作为说明备注，随下一份回报发出。
   
   从 1.5.9 起，我在命令里写三条：构建、`wf self-update --auto …`、核对（wf version 与 wf self-check），驱动器自己完成，不需要任何人提醒。
5. 回答执行端上一轮问的两点（命令里已经照此写）：
   - 解释器：驱动器跑的命令一律写 3.13.12 的绝对路径。
   - 工作目录：驱动器执行命令时，工作目录是项目根。
6. 用户已批准的另一个模型系列审查：约定在 docs/REVIEW.md，审查程序由执行端实现。项目版本包早已会调用它；1.5.8 起，自动安装工具版本前也会调用。

【本机执行】
```
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-020/delivered/build_wf_158.py --out workflow/outbox
wf fingerprint
git log --oneline --decorate -n 3
wf doctor
```

【本轮回报清单】
1. 驱动器自己构建 1.5.8：最后一行是"构建通过"，预计自检 184 项。
2. 落地 v0.7 后，指纹为 e399837c902f6b51。
3. HEAD 带 v0.7 标签。
4. doctor 显示 PROJECT.md 为 v0.7，且校验通过。

【需要用户决定】
1. 1.5.8 的护栏规则：功能版本自动安装，改动了护栏文件的版本停下等用户确认。我建议保留这一道确认；用户若希望连护栏改动也全自动，请明确说出来。不影响当前工作。

【下一轮建议档位】
high

【驱动指令】
发布：collab-wf-dev-v0.7.zip
之后：回报
验收 1：c1 exit=0 包含 "构建通过"
验收 2：c2 exit=0 包含 "e399837c902f6b51"
验收 3：c3 exit=0 包含 "tag: v0.7"
验收 4：c4 exit=0 包含 "PROJECT.md：v0.7"
指纹：e399837c902f6b51
失败后继续：是