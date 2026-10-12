[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
Now the 1.7.2 builder.
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 present_files]
【本轮结论】

**第 92 轮：全部通过**
- 1.7.1 版本包 sha256 356f10e2…，与我这边构建的完全一致。
- 指纹 95674b23bab4790e，与预期一致。
- 目标进度 7/7。

**本轮在真实环境验证到的几件事**
- **1.7.1 由证据生成回报：可用。** 这份回报在 DONE 状态下生成，带有 ② 终端原文、④ 需要注意，执行端的备注也进了 ①。W53 已解决。
- **P0-B 审查在场检测：有效。** 执行端不在场时，审查约几秒内就返回 `no_session`，没有空等 300 秒。安装照常进行，并留下了可以事后补审的 diff 路径。
- **1.6.9 的审查心跳：有效。** “开始等独立审查”这一行出现了。
- **P0-D 唤醒补投：行为符合预期。** 找不到会话时不消耗重试次数，连续 2 次后告警一次。
- **I1 核对完成。**
  - amy-local 的真实对话 id 是 `…5c794870ad88`。动作日志与配置里的登记完全一致，之前是回报抄写时混写了两个 id，并不是绑定串了线。
  - 第 91 轮的真实发送是“发往 021f6fff…”，用的是 collab-wf-dev 自己的对话，没有告警，审计记录里也没有 ADAPTER_WARNING。

**我的指令里有一个循环，责任在我**
T91-2 要求核对第 92 轮的发送，而第 92 轮的发送就是 T91-3 发出的这份回报。现在这份回报已经发出，所以：
- T91-2 可以按本回报（第 92 轮）的发送记录完成。
- T91-3 由本回报本身完成。

以后这类核对，我只写“已经发生过的发送”。

**本轮交付：build_wf_172.py（工具 1.7.2，多项目隔离 S3：每个项目自己的定时任务）**
- 文件 20331 字节，sha256 7df2e44c8b759c58233c2d45077e1829e11c3e3a97b5c23ae446ac1db1e8a7a7。
- 在与已安装版本相同的 1.7.1 上验证过：构建 243/243，用 1.7.1 自动安装，升级核验通过，self-check 完全一致，floor.py 没有改动。

为什么先做 S3、再做 S2：这一步只改工具内部，不改执行端程序用的接口；它直接解决“一个项目卡住，拖累另一个”（I3、I5、P0-C）。ISOLATION.md 允许这两个阶段对调。由此带来的版本号变化（S3 用 1.7.2，S2 改为 1.7.3），下一份项目版本会改进 ISOLATION.md。

1.7.2 的内容：
- `wf --project <名> schedule plist` 为这个项目生成独立的 launchd 配置并登记。标签是 `local.collab-wf.tick.<名>`，日志是 `local/tick-<名>.log`。
- `wf --project <名> tick` 只推进这一个项目。全局的 `wf tick` 会跳过已登记的项目。
- `wf --project <名> schedule release` 撤回登记。
- 修了一个隐患：inbox 仍然每次汇总全部项目，并且加了文件锁。如果每个项目的 tick 只写自己的 inbox，就会把别的项目的待办冲掉；而执行端判断“做完”的依据正是“key 从 inbox 消失”，这会被误判为做完。

【本机执行】
```
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-092/delivered/build_wf_172.py --out workflow/outbox
wf self-update --auto workflow/outbox/collab-wf-1.7.2.zip
wf version
wf self-check
wf fingerprint
wf goals
```

【本轮回报清单】
1. 构建通过，自检 243/243，成员集合一致，floor.py 未改动；记下版本包的 sha256。
2. 驱动器自动安装 1.7.2，升级核验通过；审查结论以原文为准。
3. 工具版本为 1.7.2。
4. self-check 完全一致。
5. 仓库指纹仍为 95674b23bab4790e（本轮不落地项目版本）。
6. `wf goals` 原文，应为 7/7。

【需要用户决定】
1. 执行端目录下的明文凭据候选文件（含一个权限过宽的旧备份）怎么处置：删除、收紧权限或保留，由你本人决定。本项不阻塞。
2. M1 最终验收：只认你本人的原话。验收后，建议把 I1–I8 定为 M2。本项不阻塞。
3. 两件事各需要你一句话：一是是否授权执行端解除它自己发起的暂停（你发起的暂停仍只认你本人）；二是 amy-local 的暂停 6X2 是否解除。S1 已经就绪，amy-local 能否真正并行运行，只差这一句。本项不阻塞。

【下一轮建议档位】
high

【驱动指令】
发布：无
执行端 1：1.7.2 装好后，把 collab-wf-dev 切到自己的定时任务，按顺序做：① 运行 wf --project collab-wf-dev schedule plist --interval 60 --max-seconds 540，把输出存为 ~/Library/LaunchAgents/local.collab-wf.tick.collab-wf-dev.plist，并立即 launchctl bootstrap 加载；② 全局定时任务保持不动（它会跳过 collab-wf-dev）；③ 几分钟后记下以下三项的原文：wf schedule status、local/tick-collab-wf-dev.log 的最后几行、全局 tick.log 里 collab-wf-dev 那一行（应为“有自己的定时任务”）。出问题就回滚：运行 wf --project collab-wf-dev schedule release，再 launchctl bootout 那个标签。结果写进 wf task done
执行端 2：S2 接口评估（作为 1.7.3 的输入，做法同 T89-2）：读 ISOLATION.md 第 2 节第 6–8、10、12 项与第 4 节，评估 S2 对执行端程序的影响，包括 inbox key 的格式（信号是否带项目名）、信号是否分项目目录、审查程序是否接受 --key、唤醒按项目找会话。逐条写出能否实现与接口意见，写进 wf task done
执行端 3：两项做完后，先用 wf note --kind info 写说明，再用 wf kick --kind report 发回报
之后：等执行端
验收 1：c1 exit=0 包含 "构建通过：collab-wf 1.7.2，自检 243/243"
验收 2：c2 exit=0 包含 "升级核验通过"
验收 3：c3 exit=0 包含 "collab-wf 1.7.2"
验收 4：c4 exit=0 包含 "完全一致"
验收 5：c5 exit=0 包含 "95674b23bab4790e"
验收 6：c6 exit=0 包含 "目标进度 7 / 7"
指纹：95674b23bab4790e
超时：1800