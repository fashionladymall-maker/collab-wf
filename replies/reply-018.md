[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 present_files]
【本轮结论】
1. 本轮交付三个文件：
   - **collab-wf-dev-v0.6.zip**：项目版本包，驱动器会自动落地。
   - **build_wf_157.py**：1.5.7 系统通知的构建器。
   - **build_wf_156.py**：上一轮已经交付过，本轮再附一份，防止上次没收到（W24）。
   
   三个文件交付前都核对过，确实在交付目录里。
2. 1.5.7 把执行端的通知胶水收进了工具：
   - wf tick 跑完后，对每个项目取 wf ask 用的同一套内容，新出现的弹成 macOS 通知。范围包括需要决定、阻塞、暂停、目标达成、额度等待、停留超时，以及 1.5.6 的"驱动器停了"。
   - 每件事只通知一次，单独记在 local/notified.json，不影响 `wf ask --only-new` 的已读记录。事项处理掉后清除记录，再出现会再通知；发送失败不算已通知，下次 tick 重试。
   - 新增 `wf notify test | status`。可用 WF_NOTIFY=0 或 local/notify.json 关闭。
   - 通知出任何问题都不影响 wf tick。
   - 自检新增 3 项。
3. 我验证过的部分：
   - 通知模块在本地测过：去重、清除、失败重试、引号转义都符合预期。
   - 构建器在 1.5.6 构建器产出的模拟树上跑通了 9 处锚点加 1 个新文件。wf.py 里 tick 的锚点取自本轮读到的原文。
   - 真实的完整自检要在执行端跑，预计 1.5.6 是 177 项，1.5.7 是 180 项。
4. v0.6 记入了这几件事：
   - 1.5.5 已安装（175/175）。
   - 系统定时器已换成官方配置，解释器统一为 3.13.12。
   - 新增两条台账：W23（用计划节点等执行端，空等两小时）和 W24（交付文件没有送达），两条都写进了协作约定。
   - W22 标为已解决。
   
   落地后的指纹应为 14228cd519bc850a。
5. 执行端的手动步骤，按顺序做：
   - 运行 `python3 build_wf_156.py --out workflow/outbox`，最后一行是"构建通过"再运行 `wf self-update workflow/outbox/collab-wf-1.5.6.zip`。
   - 运行 `python3 build_wf_157.py --out workflow/outbox`，同样通过后再运行 `wf self-update workflow/outbox/collab-wf-1.5.7.zip`。1.5.7 的构建器会先核对 1.5.6 已经装好。
   - 用 `wf notify test` 试发一条通知，再用 `wf doctor` 看"驱动器心跳"那一项。
   - 做完用 `wf kick --kind report` 回报，附上两次构建日志的结尾和上面两项的结果。
   
   按第 17 轮定下的规则，本轮驱动指令写"之后：空闲"，不预留等待时间。

【本机执行】
```
wf fingerprint
git log --oneline --decorate -n 3
wf doctor
```

【本轮回报清单】
1. 落地 v0.6 后，指纹为 14228cd519bc850a。
2. HEAD 带 v0.6 标签。
3. doctor 显示 PROJECT.md 为 v0.6，且校验通过。

【需要用户决定】
1. 是否开启"另一个模型系列审查"（同上，不影响当前工作）。

【下一轮建议档位】
high

【驱动指令】
发布：collab-wf-dev-v0.6.zip
之后：空闲
验收 1：c1 exit=0 包含 "14228cd519bc850a"
验收 2：c2 exit=0 包含 "tag: v0.6"
验收 3：c3 exit=0 包含 "PROJECT.md：v0.6"
指纹：14228cd519bc850a
失败后继续：是