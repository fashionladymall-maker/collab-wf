[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 present_files]
【本轮结论】
1. 上一轮的结果已核对：
   - 1.5.4 自检 173/173。新增是 8 项，我当时写成 7 项，是我数错了，1.5.5 的 CHANGELOG 里已更正。
   - 适配器 1.5.0 符合 SNAPSHOT.md 的约定，三条"不打扰用户"的要求都逐条实测通过。
   - 执行端踩到的三个坑留在适配器里，属于实现细节；SNAPSHOT.md 只补一条可观察的要求：截到的图里要有会话正文，不能是骨架屏。
2. 关于 pstack（用户的第 3 条留言）：
   - 结论："常驻协调"和"定时唤醒"这两样，工具里早就有了，缺的是部署。驱动器从来不写代码，本身就是协调者；`wf tick` 加 `wf schedule plist` 就是心跳。用户当初也定过"靠系统定时唤醒，不要常驻进程"。
   - "卡死"的真正根源是执行端一直用手写的 for 循环唤醒驱动器，跑满次数就静默退出，状态停在原地不动。改用系统定时器，这个问题就消失（手动步骤见第 7 条）。
   - 1.5.6 再补两处，让驱动器停了能被看见：wf doctor 报驱动器心跳，wf ask 主动呈报"驱动器停了"。
   - "按角色分配模型"在我们这里的等价物，是同一个模型按角色分档位。
   - "多个模型互审"的等价物分三层：机器判据、真实产物验证、另一个模型系列审查。第三层要不要开，由用户决定，见下文。
   - 完整流程写成 docs/PROCESS.md 随工具发布，以后开发任何新软件都照它走。每一条写明做什么、为什么、谁做、怎样算做到了，另外新增两条约定："同一判据两轮不过就必须换思路"，"台账里的每一条都要落到自检或规则上"。
3. 关于上下文，完整设计写成 docs/CONTEXT.md：
   - 记忆放在本机文件里，不放在对话里。
   - 满足下面任一条就开新对话：体量到上限、回复对不上已有结论、对话被降级、同一问题连续两轮在 high 档没解决。
   - 恢复包只发说明消息，文件由新的 Claude 按清单点名读取。
   - 本项目的 policy 现在是满 20 轮就开新对话；目前已发出 13 轮，到 20 轮时驱动器会自动停下并生成恢复包。
4. 给用户的说明（可以直接转述）：
   - Claude 在一个对话里能看到的内容有上限，所以记忆不放在对话里，而是放在你电脑上的文件里：PROJECT.md、每个版本的代码、三方逐字的对话记录与证据、工作规则。看板上都能看到。
   - 页面上没有 Projects，是因为 Projects 要上传文件，而我们的通道只能下载；而且我们的状态每一轮都在变。我们的做法相当于一个自动维护的 Project：开新对话时，把这些记忆作为第一条消息发给新的 Claude，一两轮就能接上。这个项目开局就是这样接上的，并且逐字节核对过。
   - 什么时候开新对话、怎么开，都由执行端完成，你不用做任何事。
5. OUTGOING_STRUCTURE 的提示语改为中性：去掉"（工具缺陷）"，改成先核对标题行和 ①②③ 三段。这一处标为可选，原文命中才改。
6. websocket-client 两边都做：
   - SNAPSHOT.md 写明它是截图的前置条件。
   - 适配器缺这个模块时按"降级"处理：screenshot.status 写 unavailable，reason 写明缺什么、用什么命令安装。这段代码由执行端写。
7. 执行端的手动步骤：
   - 在项目根目录运行 `python3 build_wf_155.py --out workflow/outbox`。最后一行是"构建通过"，再运行 `wf self-update workflow/outbox/collab-wf-1.5.5.zip`。预计自检 175 项。
   - 安装系统定时器：
     - `wf schedule plist > ~/Library/LaunchAgents/local.collab-wf.tick.plist`
     - `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.collab-wf.tick.plist`
     - 然后停掉手写的 for 循环。两者同时跑也不会冲突（驱动器有项目锁），但循环已经没用了。
     - 最后用 `wf schedule status` 核对：最近一次运行应在 5 分钟内。
   - 把第 4 条转述给用户。
8. v0.5 记入了以上结论，以及一项待用户决策。落地后的指纹应为 3345de3ba3bf580a。本轮同时读心跳和主动呈报相关的代码，供 1.5.6 使用。

【本机执行】
```
wf fingerprint
git log --oneline --decorate -n 3
wf doctor
grep -n ^def /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/admin.py
grep -n ^def /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/asks.py
grep -n schedule /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
sed -n 136,202p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/server.py
```

【本轮回报清单】
1. 落地 v0.5 后，指纹为 3345de3ba3bf580a。
2. HEAD 带 v0.5 标签。
3. doctor 显示 PROJECT.md 为 v0.5，且校验通过。
4. admin.py 的函数索引（找 doctor 和 schedule 的实现位置）。
5. asks.py 的函数索引（找主动呈报的实现位置）。
6. wf.py 中 schedule 子命令的位置。
7. server.py 第 136–202 行（看板"驱动器"卡片判定"停了"的逻辑）。

【需要用户决定】
1. 是否开启"另一个模型系列审查"：由执行端用它自己的模型（WorkBuddy 所用的模型），审 Claude 交付的每个版本包。它只能阻止或提醒，不能放行。执行端本来就能看到全部代码，开启后不会多暴露什么。不开也不影响现在的工作。

【下一轮建议档位】
high

【驱动指令】
发布：collab-wf-dev-v0.5.zip
之后：回报
验收 1：c1 exit=0 包含 "3345de3ba3bf580a"
验收 2：c2 exit=0 包含 "tag: v0.5"
验收 3：c3 exit=0 包含 "PROJECT.md：v0.5"
验收 4：c4 exit=0
验收 5：c5 exit=0
验收 6：c6 exit=0 包含 "schedule"
验收 7：c7 exit=0
指纹：3345de3ba3bf580a
失败后继续：是