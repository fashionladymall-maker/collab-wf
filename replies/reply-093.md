[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 present_files]
【本轮结论】

**第 93 轮：全部通过**
- 1.7.2 的版本包 sha256 为 e8f80f90…，与我这边构建的一致。
- 仓库指纹不变，目标仍是 7/7。

**S3（每个项目自己的定时任务）在真实环境里切换成功**
- collab-wf-dev 现在由它自己的定时任务推进，周期 60 秒。
- 全局 tick 日志里，collab-wf-dev 这一行显示为“有自己的定时任务”。
- amy-local 仍然静音，没有动。

**I1 闭合**
第 91、92 轮两次真实发送都发往 collab-wf-dev 自己登记的对话（021f6fff…），没有告警，ADAPTER_WARNING 计数为 0。

**需要注意：工具版本连续两版没有经过独立审查**
- 1.7.1：执行端不在场，审查返回 `no_session`。
- 1.7.2：执行端在场，但 270 秒内没写出结论，超时。这一轮心跳每 30 秒一行，等待过程看得见；c02 一共用了 452 秒。

“未审查照常安装”是设计上允许的，但独立审查是底线之外的一道防线（结论 17），不能长期空着。本轮请执行端事后补审，见执行端 1。

**执行端的 S2 接口评估，逐条采纳或答复**
- **#6 收件箱分项目视图**：视图只由工具生成、只读，执行端仍读全局 inbox。路径改成 `local/views/<名>/inbox.json`，不用 `local/projects/`，避免和项目工作树混淆，这是采纳你的提醒。按项目、机器可读的待办，现成命令就有：`wf --project <名> --json ask`。
- **#7 信号分项目目录**：写路径完全由工具决定，`wf --project <名> signal add` 自动写到 `signals/<名>/`。旧位置里带项目的信号，由工具在读取时一次性搬过去。inbox 里信号的 key 保持 `signal|<键>` 不变，执行端程序不用改。
- **#8 审查键**：由工具显式传 `--key`。项目版本的键是 `<项目>.t<轮次>.<包名>`，工具版本的键是 `collab-wf.tool.<包名>`。同一轮里同一个包重审时，键是相同的。这个参数需要在 transport.json 里声明 `review_key` 才会传，所以执行端的审查程序先改好，再打开开关。
- **#10 唤醒按项目**：仍由执行端实现。`wf wake status` 我不做：唤醒状态在执行端程序里，工具拿不到真实数据。建议改成把投递失败写成带项目的信号，例如 `wake:<项目>:<事项>`，看门狗和 inbox 自然就能看见，一个项目没开窗口也不会影响另一个项目的告警去重。“窗口要开在项目目录下”这条约束已写进 USER.md。
- **#12 审查程序参数化**：与 #8 是同一件事。

**本轮交付：build_wf_173.py（工具 1.7.3，S2 的工具侧）**
- 文件 23816 字节，sha256 fa75aa5030521eeec37b6274710329ad35ae59825bbad4edebf86f532305c143。
- 我在与已安装版本相同的 1.7.2 上验证过：构建 245/245；用 1.7.2 自动安装，升级核验通过，self-check 完全一致；floor.py 没有改动。原有的信号与看门狗自检都照常通过。
- 内容有四项：
  1. 信号按项目分目录，并迁移旧位置的信号。
  2. 分项目只读视图。
  3. 审查 `--key`。
  4. 项目版本审查时也输出周期心跳，1.6.9 只覆盖了工具版本。

ISOLATION.md 的阶段版本号（S3 = 1.7.2，S2 工具侧 = 1.7.3）和第 92–93 轮的进展，下一份项目版本一并补记。

【本机执行】
```
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-093/delivered/build_wf_173.py --out workflow/outbox
wf self-update --auto workflow/outbox/collab-wf-1.7.3.zip
wf version
wf self-check
wf fingerprint
wf goals
```

【本轮回报清单】
1. 构建通过，自检 245/245，成员集合一致，floor.py 未改动；记下版本包的 sha256。
2. 驱动器自动安装 1.7.3，升级核验通过；审查结论以原文为准。
3. 工具版本为 1.7.3。
4. self-check 完全一致。
5. 仓库指纹仍为 95674b23bab4790e（本轮不落地项目版本）。
6. `wf goals` 原文，应为 7/7。

【需要用户决定】
1. 执行端目录下明文凭据候选文件（含一个权限过宽的旧备份）的处置：删除、收紧权限或保留。由你本人决定。本项不阻塞。
2. M1 最终验收：只认你本人的原话。验收之后，建议把 I1–I8 定为 M2。本项不阻塞。
3. 两件事各需要你一句话：一是是否授权执行端解除它自己发起的暂停（你发起的暂停仍只认你本人）；二是 amy-local 的暂停 6X2 是否解除。会话绑定（S1）和独立定时（S3）都已就绪，amy-local 能否并行跑起来只差这一句。本项不阻塞。

【下一轮建议档位】
high

【驱动指令】
发布：无
执行端 1：事后补审：按 REVIEW.md 第 3 节审 local/reviews/collab-wf-1.7.1.diff、collab-wf-1.7.2.diff（如 1.7.3 本轮也未审查，连同 collab-wf-1.7.3.diff），每个版本给出 pass / concerns / block 与要点，写进 wf task done；任何一个是 block 就写明理由，回滚由用户决定，执行端不自行回滚
执行端 2：1.7.3 装好后：① 审查程序接受 --key，请求、心跳、未审查标记、信号全部用这个键命名，不再从版本包文件名反推（verdict 侧 --request 与 --request-id 不变）；② transport.json 加 "review_key": true；③ 用一次 wf --project collab-wf-dev signal add 加 clear 的往返，确认信号落在 local/signals/collab-wf-dev/ 下，并记下 local/views/collab-wf-dev/inbox.json 的存在与条目数。结果写进 wf task done
执行端 3：两项做完后，先用 wf note --kind info 写说明，再用 wf kick --kind report 发回报
之后：等执行端
验收 1：c1 exit=0 包含 "构建通过：collab-wf 1.7.3，自检 245/245"
验收 2：c2 exit=0 包含 "升级核验通过"
验收 3：c3 exit=0 包含 "collab-wf 1.7.3"
验收 4：c4 exit=0 包含 "完全一致"
验收 5：c5 exit=0 包含 "95674b23bab4790e"
验收 6：c6 exit=0 包含 "目标进度 7 / 7"
指纹：95674b23bab4790e
超时：1800