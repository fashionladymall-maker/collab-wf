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
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
Now I'll write the builder.
[工具 create_file]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 present_files]
【本轮结论】

本轮交付两个文件，都已在我这边验证过。

**1. collab-wf-dev-v2.7.zip（项目版本，落地）**
- 大小 115768 字节，sha256 2d7cd24de07861b724d30638dd32d51759a1d1a293446f30324b60a3e21aafd0。
- 这是第 84 轮交付、本机已核验的同一份字节，取自公开同步仓库，没有改动（遵守 W29）。
- 包里的 papertrade/make_r5.py 正是真品（708e7dd4…，19964 字节）。T86-2 卡在“生成器缺失”，落地后就能继续。
- 落地后仓库指纹应为 763bef1a04e059f1，共 9 个文件（新增 papertrade/ 下两个）。
- v2.7 的 PROJECT.md 仍写着“1.6.7 在用”，与事实不符，因为它是第 84 轮的快照。下一份项目版本 v2.8 会补记第 84–88 轮、W48、N 系列，并写下本轮的方案裁定。

**2. build_wf_169.py（工具 1.6.9 构建器）**
- 大小 26753 字节，sha256 8d02ed3c1aff53d2378e2a82396f42e1594863ce4e054c04e1fe1d2b0675929f。
- 已在我这边的 1.6.8 源码副本上跑完整个流程：构建通过，自检 236/236（原 231 项加新增 5 项）。
- 随后用 1.6.8 的 `wf self-update --auto` 安装：底线文件检查与 floor.probe 都通过，升级核验通过，self-check 完全一致。floor.py 没有改动，所以可以自动安装。
- 构建器开始时会逐一核对 11 个待改文件与 1.6.8 是否逐字节一致，不一致就中止。

**1.6.9 的范围（按甲清单）**

本版包含：
- **P0-A（工具侧）**：等审查期间先输出开始行，之后每 30 秒输出一行心跳。没有结论时写明原因，并留档 `local/reviews/<包名>.unreviewed.json`，版本照常安装。运行新版本自检前也先输出一行。
- **N8 / P1-C**：发送结果不明期间，SEND 的停留上限改按 65 分钟计，不再在 15 分钟时误报。
- **N9**：已暂停或 auto=false 的项目，不再报停留过久、不报看门狗停滞、心跳不判“停了”；schedule status 把它们标为“已静音”。装上后，amy-local 的反复误报会停。
- **N6、N7 / P1-B**：同步暂停的标题按实际命中写；key 改为绑定命中内容，不再随时间变化。
- **N10 / P1-D**：`wf conv new` 清掉 send_ref 和 resend_parent，与 driver.py:430 的做法一致。执行端第 87 轮手工清理 send_ref，与这里的语义相同，处置正确。

本版不包含，以及原因：
- **P0-B**：两种方案中我推荐方案 2，在场检测放在执行端的审查程序里，因为只有它知道有没有可用会话。约定是：没有会话时几秒内以非 0 退出。
  - 方案 1（先安装、事后 block 再回滚）不采用。回滚是护栏命令，驱动器没有调用它的路径；而且未审查的代码会先跑起来。
  - review_timeout_s 保持 300 即可。只有在执行端在场、审查确实需要更久时，才考虑调高。
- **P0-C**：不在半途杀掉 self-update，因为复制阶段被截断会留下装了一半的版本。正确做法是让长命令在后台执行、下一次 tick 收结果。这与多项目隔离要求的 I3（调度预算不互占）是同一个改造，并入乙的第三阶段。现在有 300 秒的止血，c02 约 466 秒，已经在 540 秒的 tick 预算之内。
- **P0-D**：属于执行端的唤醒程序（结论 30a、33），我只给判据，见执行端 3。
- **P1-A**：要等用户决定 3。这一条改的是用户定过的护栏，执行端不能代为同意。

“人不在场时各道门的处理”表已写进 DRIVER.md 第 26 节。

**乙：多项目隔离**

下一轮档位用 max，专门出设计，本轮不写代码。阶段顺序先定如下：
1. **会话按项目绑定**（I1、I6）：工具把本项目的对话 id 传给适配器；适配器收到后只用它，不再读全局默认。conv_new 只改自己项目的那一项。适配器的实现由执行端负责。
2. **审查、信号、同步按项目命名空间**（I4、I7）。
3. **调度**：每个项目独立预算，长命令改为后台执行（I3、I5，含 P0-C）。
4. **共用资源的排队与留痕**（I8）。

【本机执行】
```
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-088/delivered/build_wf_169.py --out workflow/outbox
wf self-update --auto workflow/outbox/collab-wf-1.6.9.zip
wf version
wf self-check
wf fingerprint
wf goals
```

【本轮回报清单】
1. 构建通过，自检 236/236，成员集合一致，floor.py 未改动；记下版本包的 sha256。
2. 驱动器自动安装 1.6.9，升级核验通过；审查是“结论”还是“未审查”，以原文为准。
3. 工具版本为 1.6.9。
4. self-check 完全一致。
5. 落地 v2.7 后，仓库指纹为 763bef1a04e059f1。
6. `wf goals` 原文，应为 7/7。

【需要用户决定】
1. 执行端目录下的明文凭据候选文件（含一个权限过宽的旧备份）怎么处置：删除、收紧权限或保留。由你本人决定。本项不阻塞。
2. M1 最终验收：只认你本人的原话。本项不阻塞。
3. 两件事都需要你一句话：一是是否授权执行端解除它自己发起的暂停（批准后在下一版实现，你发起的暂停仍只认你本人）；二是 amy-local 现有的暂停 6X2 是否解除。本项不阻塞。

【下一轮建议档位】
max

【驱动指令】
发布：collab-wf-dev-v2.7.zip
执行端 1：P1-C 续做（T86-2）：用落地后的 papertrade/make_r5.py 与 papertrade/pt5_regress.js（先核对 sha256 前 12 位：708e7dd49640、f0d3cec46bc7），从 workflow/evidence/turn-079/delivered/ 的 r4 生成 r5，跑 24 项回归，再做真实浏览器桌面与 390 宽度检查。r5 的大小与 sha256、回归原文末行写进 wf task done
执行端 2：P0-B：在执行端的审查程序里加在场检测。没有可用会话时 10 秒内以非 0 退出，并输出 {"verdict": "unreviewed", "reason": "no_session"}；有会话时才等。用一次空闲时的调用实测耗时，写进 wf task done
执行端 3：P0-D 与 N10 的适配器侧：唤醒程序对 for=executor 的未完成项，在没有会话期间按 tick 节奏（不超过 60 秒）重试，会话出现后 60 秒内补投；适配器 poll 发现 --prev-ref 所属对话与当前默认对话不同时输出告警。实现与实测由执行端负责，结果写进 wf task done
之后：等执行端
验收 1：c1 exit=0 包含 "构建通过：collab-wf 1.6.9，自检 236/236"
验收 2：c2 exit=0 包含 "升级核验通过"
验收 3：c3 exit=0 包含 "collab-wf 1.6.9"
验收 4：c4 exit=0 包含 "完全一致"
验收 5：c5 exit=0 包含 "763bef1a04e059f1"
验收 6：c6 exit=0 包含 "目标进度 7 / 7"
指纹：763bef1a04e059f1
超时：1800