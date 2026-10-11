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
【本轮结论】

新对话第一轮，按约定只做核对，不交付任何版本。

**当前版本与阶段**

- 工具是 collab-wf 1.6.6。它在第 59 轮由驱动器自己构建（自检 212/212）并自动安装。
- 项目是 collab-wf-dev v2.6，提交 e213fe3，标签 v2.6。
- 第 80 轮验收通过，`wf goals` 为 7/7，M1 的全部判据都达成了。
- 第 80 轮的结论是“有条件通过（CONDITIONAL）”，遗留项就是你附上的改进批次一（P0-A 到 P2-F）。

**与事实不符的地方**

PROJECT.md 的“当前版本”一行还停在“G3 计数中”，“下一步”里 M1 也没有勾上，和 7/7 的事实不符。目前以 STATUS 与 `wf goals` 为准，这些会在 P1-B 的补记里一并修正。

**待用户决策（只认你本人的原话，执行端不代办）**

1. 执行端目录下有明文凭据候选文件，其中一个是权限过宽的旧备份。要删除、收紧权限还是保留，由你决定。
2. M1 的最终验收。

**工具边界（照旧）**

- 四道底线（资金、密钥/凭据、合规、实盘）只由用户决定。
- 改动 `wflib/floor.py` 的版本必须停下等用户确认，再手动安装；其余工具版本由驱动器 `--auto` 自动安装（auto_apply 与 auto_self_update 均为 true）。
- 驱动器只执行 policy 白名单内的命令。
- 发送结果不明时不重发，需要重发只用 `wf resend`。
- Claude 不审读通道适配器的站点接口代码。
- 公开仓库里不放 local/、evidence/ 和任何密钥。
- 作者在容器里的自测不等于本机通过。

**仓库指纹核对（作者容器内）**

我从公开仓库下载了 main 的完整快照，按 `wflib/ops.py` 的 `fingerprint()` 算法对 project/ 下的 7 个文件复算：

- 文件是 .gitignore、CHANGELOG.md、PROJECT.md、VISION.md，以及 workflow/ 下的 goals.json、m1_check.py、policy.json。
- 复算得到 **b848fba7f3776a69（7 个文件）**，和你给的一致。
- `artifacts/collab-wf-dev-v2.6.zip` 里的 PROJECT.md 和 CHANGELOG.md 与仓库现有文件逐字节相同。
- 工具 VERSION 文件是 1.6.6。

**关于改进批次一（本轮不做，先给计划）**

我读源码时顺带确认了 P0-A 的根因：

- `driver.py` 第 1110–1113 行在目标达成时直接进入 DONE 并 return。
- 登记执行端待办的 `_tasks.add(...)` 在第 1128 行，所以永远执行不到。
- 修复只需在 driver.py 里调整顺序，不碰 `floor.py`，可以走 `--auto` 自动安装。

分批计划如下：

| 轮次 | 交付内容 |
|---|---|
| 第 82 轮 | P0-A，工具 1.6.7，附自检用例 ①–④；P1-C，给出 r4 的计量单位方案与数值回归用例，作为附件交付，由执行端在本机验收 |
| 第 83 轮 | P1-B，项目 v2.7，只改文档，会把 P0-A 也补记进去；P1-D，驱动器侧 block 与超时的演练清单，写成“执行端 N” |
| 第 84 轮 | P2-E，工具 1.6.8，新增 landed_timeout_s，保持“结果不明不重发”；P2-F，两项演练设计，或给出“不可演练”的论证 |

**本轮“执行端 1”预计不会被登记**

本轮验收后，驱动器会照常因为 7/7 进入 DONE，所以下面的“执行端 1”预计**不会**被登记，也不会进 inbox。这正好作为 P0-A 修复前的一次真实复现基线。修复后，同样的写法应该登记为 T&lt;轮次&gt;-1 并进 inbox，DONE 状态保持不变。

【本机执行】
```
wf version
wf goals
wf fingerprint
```

【本轮回报清单】
1. `wf version` 原文，应为 collab-wf 1.6.6。
2. `wf goals` 原文，应为 7/7。
3. `wf fingerprint` 原文，应为 b848fba7f3776a69（7 个文件）。

【需要用户决定】
1. 执行端目录下明文凭据候选文件（含一个权限过宽的旧备份）的处置：删除、收紧权限或保留。由你本人决定，执行端不代办。本项不阻塞本轮。
2. M1 最终验收：驱动器停在“目标达成”，确认只认你本人的原话。本项不阻塞本轮。

【下一轮建议档位】
high

【驱动指令】
发布：无
之后：空闲
执行端 1：本轮进入 DONE 后，在改进批次一委派包（即本轮附带的原消息）§1 末尾补一行“P0-A 修复前基线”，写明 `wf status` 的状态，以及 `wf task list` 里有没有 T81-1、inbox 里有没有 task:T81-1；然后用 wf kick --message <该委派包文件> --kind task 发出，档位 high
验收 1：c1 exit=0 包含 "collab-wf 1.6.6"
验收 2：c2 exit=0 包含 "目标进度 7 / 7"
验收 3：c3 exit=0 包含 "b848fba7f3776a69"
指纹：b848fba7f3776a69