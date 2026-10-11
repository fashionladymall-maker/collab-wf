【本轮结论】

**本轮不交付任何文件。** 你已经实测到：这个对话的下载端点对任何路径都返回同一份 18884 字节内容，所以从这里再交付什么都取不回。本轮只用本机已经核验过的产物推进，下一轮换新对话恢复交付。

**已核验的事实（本机，执行端完成）**

第 84 轮的 5 个交付物已在本机重建，大小和 sha256 与我的声明逐字节一致：

- collab-wf-dev-v2.7.zip：2d7cd24d…
- build_wf_168.py：ca141fff…
- pack_v27.py：25878c42…
- v27-PROJECT.md：c2125949…
- v27-CHANGELOG.md：24da66f2…

本机独立运行 build_wf_168.py：自检 231/231，63 个文件加 MANIFEST.json，成员集合一致，没有运行文件，floor.py 未改动。版本包 sha256 为 c5733bc3…，与作者容器里的结果相同，说明构建可以复现。错内容的文件已移到 rejected/，原字节保留。

关于重建方式：你是逐字转录我写下的源文件，再用 sha256 逐个比对。内容的完整性由哈希保证，所以我采信这批产物。这条路径只作为兜底，不作为常规交付方式。

**版本号的处理（按 W29，已交付的版本号不复用到不同内容）**

- **1.6.8 以 ca141fff 这一版为准**（231 项自检）。第 85 轮那份带同步提示和 SEND 停留修正的 d096c798 从未到达本机，作废；它多出的修正改入 1.6.9。
- 第 85 轮的版本包 04e95289… 也作废。本机已核验的 v2.7（2d7cd24d）内容有效，但驱动器只会落地本轮下载到的文件，所以在这个对话里没法落地。下一轮在新对话里重新交付。

**本轮做什么**

用白名单内的路径，直接运行本机已核验的 `workflow/evidence/turn-084/delivered/build_wf_168.py`，再自动安装 1.6.8。不发布项目版本，指纹应保持 b848fba7f3776a69。

验收仍由内存里的 1.6.7 完成，所以：

- 本轮会进入 DONE，同时把下面三件待办登记为 T86-1 到 T86-3。这正好是 P0-A 在 DONE 下的第一次真实检验。
- 1.6.7 仍会把失败吞进 DONE（1.6.8 才修好），所以执行端 1 要单独核对 verify.json。

**你提的 unpause 问题（决策点 1）**

问题属实：`wf pause` 一律记 by=user，`wf unpause` 也只认用户原话，所以执行端自己发起的暂停只能由用户解除。

我的修法放进 1.6.9：

- `wf pause` 支持 `--by executor --reason`，并在状态里记下发起方。
- 执行端发起的暂停，inbox 交给执行端处理，可以用 `wf unpause --by executor --reason` 解除，审计里如实记 by=executor。
- 用户发起的暂停仍只认用户原话。

这条线来自已确认结论 17（用户第 21–24 轮定下的护栏）。解除暂停不属于四道底线，但改动的是用户定过的护栏，所以我把“是否授权执行端解除它自己发起的暂停”列为用户决定，不阻塞本轮。

amy-local 现有的暂停 6X2 是旧版记录的 by=user，事后分不清是谁发起的，所以即使 1.6.9 装上，它仍需要你一句话。它已经用 auto:false 静音，不影响其他项目。

**下一步**

1. 执行端开新对话（按 WORKFLOW 第 10 节）。
2. 新对话第一轮：我只交付**一个**版本包，即项目版本 v2.7。包里有 PROJECT.md 和 CHANGELOG.md（补记第 84–86 轮，包括 W48 的确定结论）、papertrade 的两个脚本，以及项目根下的 1.6.9 构建器。1.6.9 的内容是：同步暂停提示按原因区分、发送结果不明期间 SEND 不算停留过久、unpause 由发起方决定。
3. 随后：读多项目隔离的需求梳理，先出设计方案；做 P1-D 实景演练；发布 policy 里加 `landed_timeout_s` 的项目版本。
4. amy-local 的新事实（安装包已到位、Amy Voice 3.4.0 在运行、health=ok）我已知悉。它真正卡住的是“独立任务未建立”和“多项目通道隔离”，并入多项目方案处理。

【本机执行】
```
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-084/delivered/build_wf_168.py --out workflow/outbox
wf self-update --auto workflow/outbox/collab-wf-1.6.8.zip
wf version
wf self-check
wf fingerprint
wf goals
```

【本轮回报清单】
1. 构建通过，自检 231/231，成员集合核对一致。
2. 驱动器自动安装 1.6.8，升级核验通过。
3. 工具版本为 1.6.8。
4. self-check 完全一致。
5. 仓库指纹仍为 b848fba7f3776a69（本轮不落地项目版本）。
6. `wf goals` 原文，应为 7/7。

【需要用户决定】
1. 执行端目录下明文凭据候选文件（含一个权限过宽的旧备份）的处置：删除、收紧权限或保留。由你本人决定，执行端不代办。本项不阻塞本轮。
2. M1 最终验收：只认你本人的原话。本项不阻塞本轮。
3. 是否授权执行端解除它自己发起的暂停（1.6.9 实现，用户发起的暂停仍只认你本人）；另外，amy-local 现有的暂停（6X2）是否解除，需要你一句话。本项不阻塞本轮。

【下一轮建议档位】
high

【驱动指令】
发布：无
执行端 1：核对本轮 verify.json 六项是否全部 pass（内存里的 1.6.7 会把失败吞进 DONE），记下 c01、c02 的末行与本机 1.6.8 版本包的 sha256（应为 c5733bc3…），用 wf task done T86-1 --note 写下
执行端 2：P1-C：查找 r4 原件（sha256 1d580864a913f907d6f6c1aacd78d61a13db11976af47c13eb8f3fc995f8794f，72438 字节），先看 workflow/evidence/turn-079/delivered/。脚本用本机已核验的 make_r5.py（708e7dd4…）和 pt5_regress.js（f0d3cec4…，与 rejected/ 里那份坏 zip 的字节相同）。找到 r4 就生成 r5，跑 24 项回归，并做真实浏览器桌面与 390 宽度检查；找不到就如实写“未找到”。用 wf task done T86-2 --note 写下
执行端 3：P0-A 实景（最后做）：先在 T86-3 未完成时记下 wf status（应为第 86 轮 DONE）、wf task list、local/inbox.json 里的 task:T86-3（应带 action）、wf explain（应写“需要：执行端”）；然后 wf task done T86-3 --note "P0-A 实景"，再记下 done:86 的 why 是否附上三项结果、状态是否仍为 DONE。最后按 WORKFLOW 第 10 节开新对话（wf bootstrap --reason "下载端点冻结，W48"、wf conv new），在说明消息后附上三项结果与多项目隔离设计包
之后：等执行端
验收 1：c1 exit=0 包含 "构建通过"
验收 2：c2 exit=0 包含 "升级核验通过"
验收 3：c3 exit=0 包含 "collab-wf 1.6.8"
验收 4：c4 exit=0 包含 "完全一致"
验收 5：c5 exit=0 包含 "b848fba7f3776a69"
验收 6：c6 exit=0 包含 "目标进度 7 / 7"
指纹：b848fba7f3776a69
超时：1800