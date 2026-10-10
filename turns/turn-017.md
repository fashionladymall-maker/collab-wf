【回报 · 第 17 轮 · v0.5】1.5.5 与系统定时器已落地 + 三处需要你处理

档位：high

---

## ① 落地结果

### 1. 1.5.5 已构建并安装

构建（`build_wf_155.py --out workflow/outbox`）：

```
自检结果：175 / 175 通过
      ✓ 自检通过（退出码 0）
[6/6] 打包
      workflow/outbox/collab-wf-1.5.5.zip（52 个文件，250017 字节，sha256 f4abee2cd884）
构建通过。安装：wf self-update …/collab-wf-1.5.5.zip
```

安装：

```
升级核验通过：磁盘版本 1.5.5，52 个文件与版本包一致，看板已加载 1.5.5
工具已升级：1.5.4 → 1.5.5（58c4e8e），自检已通过
看板已重启以加载新版本
```

**175/175 与你的预期一致。**

### 2. 系统定时器已换成工具自己的配置

```
cd /Users/admin/.workbuddy-ai/projects/collab-wf-dev
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 \
  /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py schedule plist > /tmp/new-tick.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/local.collab-wf.tick.plist
cp /tmp/new-tick.plist ~/Library/LaunchAgents/local.collab-wf.tick.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.collab-wf.tick.plist
```

`wf schedule plist` 生成的 `ProgramArguments` 正是你要求的：

```xml
<string>/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3</string>
<string>/Users/admin/.workbuddy-ai/tools/collab-wf/wf.py</string>
<string>tick</string>
<string>--max-seconds</string>
<string>540</string>
```

核对：

```
$ launchctl list | grep collab
72344	0	local.collab-wf.tick

$ wf schedule status
  btc-quant：最近一次运行 0 分钟前，退出码 20
  collab-wf-dev：最近一次运行 0 分钟前，退出码 10
```

### 3. 手写的 for 循环已停

`kill 68509` → `ps` 确认进程已消失。

### 4. doctor

```
✓ Python：3.13.12
✓ 工作流定义：workflow.json，14 个状态
✓ 工具版本：1.5.5（wf-v1.5.5）
✓ 看板进程：运行中（PID 72271，端口 8765，加载的版本 1.5.5）
✓ 版本匹配：项目固定 1.5，工具 1.5.5
✓ PROJECT.md：v0.5，阶段：1.5.4 已安装，页面快照与截图已实测，1.5.5（流程与上下文文档）构建器已交付
✓ 当前步骤：WAIT_SCHEDULE（等待计划节点），第 16 轮
✓ 额度数据：6 分钟前更新
! 事件序号：共 632 条：重复 5 个、倒序 2 处、无法解析 0 行  → 进程曾在"写事件"与"写状态"之间被打断；1.5.0 起先写状态再追加事件，历史记录不改
```

**你第 15 轮提的解释器不一致问题已消失**：doctor 现在报 3.13.12。原因是那个手写 for 循环用 `nohup zsh -c` 启动，非交互 zsh 的 PATH 把 `python3` 解析到了 `/opt/homebrew/bin/python3`（3.12）；定时器换成绝对路径 3.13.12 之后就不会再有这个分叉。

工作区干净（构建器的临时副本已从项目根移走）。

### 5. 一处更正：我上一轮说的"驱动器停摆"是错的

我第 16 轮那会儿用 `ps` 找 `wf run` 进程，找不到就判断"驱动器没在跑"。**这个判断错了** —— 系统定时器 `local.collab-wf.tick` **一直在正常工作**，它每 300 秒起来跑一次 `wf tick`，是个短命进程，我用 `ps` 采样的时刻恰好没抓到。

真实的退出码分布（`local/tick.log`）：

```
collab-wf-dev：exit=10  等待计划节点 2026-10-10T12:30+00:00：执行端安装 1.5.5、1.5.6 与系统定时器
btc-quant：exit=20  已暂停：主角改为工具层项目 collab-wf-dev
```

所以定时器没坏，**"停摆"的真相是"空等"** —— 见 ②-1。

---

## ② 三处需要你处理

### 1. ★ 计划节点被当成了"给执行端留时间"，造成两小时空等

你第 16 轮的驱动指令写的是：

```
之后：等待 2026-10-10T12:30:00+00:00 执行端安装 1.5.5、1.5.6 与系统定时器
```

驱动器忠实执行：进入 WAIT_SCHEDULE，每 5 分钟醒来一次，看一眼表，发现没到 12:30Z（= 20:30 北京时间），打印 `exit=10`，继续睡。**从 18:27 到 20:30，整个项目不会做任何事。**

但实际上这些活 **18:30 就全部做完了** —— 你第 16 轮自己也写了「执行端提前做完也可以直接回报」。

**我建议：计划节点只用于"真的要等外部条件"**（等到某个日期、等用户答复、等第三方）。**"给执行端留时间"不该用计划节点** —— 执行端是事件驱动的，活干完自然会回报，不需要预留时间。用户这一轮的反馈就是直接证据：他看见消息到了但看板不动，问我"为啥死在这里了"。

（这一条也说明：`wf schedule` 的语义边界需要在 PROCESS.md 里写清楚，否则以后还会踩。）

### 2. ★ `build_wf_156.py` 没有送达

你第 16 轮说"本轮交付 1.5.6 构建器 `build_wf_156.py`"，但：

```
workflow/evidence/turn-016/delivered/  →  只有 reply.md（2668 字节），没有 .py
驱动器日志                             →  [WAIT_REPLY] 收到回复（交付文件 0 个）
全盘 find ~ -name "build_wf_156*.py"   →  无结果
```

**第 16 轮的交付文件一个都没下来。** 请重新交付 `build_wf_156.py`。

顺带一个观察：第 14 轮交付了 2 个文件（`build_wf_155.py` + `collab-wf-dev-v0.5.zip`）都下来了，第 15、16 轮都是 0 个。第 15 轮本来就无交付，所以真正的问题是第 16 轮 —— 可能与你那次回复里 `present_files` 的调用方式有关，供你参考。

### 3. ★ 通知能力被官方 plist 取代后丢了，请折进工具

换 plist 之前，定时器跑的是我自己写的胶水 `local/tick_notify.py`，它的作用是：**跑完 `wf tick` 之后，如果工作流停在需要用户的状态，就弹一条 macOS 通知**（`AWAITING_USER` → "需要你决定"、`BLOCKED` → "流程阻塞"、`DONE` → "目标已达成"）。

那个脚本自己的文件头就写着：

```
背景：wf tick 有推进能力，但没有通知能力。用户不主动来问，就永远不知道流程停了。
这是执行端的本地胶水（放在 local/ 下，不进工具仓库，不参与指纹）。
正式做法应由 wf tick 内置通知钩子（建议在 1.5.0 吸收）。
```

现在换成官方 plist（跑 `wf.py tick`）之后，**通知没有了**。旧配置备份在 `~/Library/LaunchAgents/local.collab-wf.tick.plist.bak-20261010-183*`。

**请把通知折进 `wf tick`** —— 这正是"自建胶水要交你审计并融入大工具"的实例。用户明确要求过"要看得到进度、不接受默默执行"，通知是这个诉求的一部分。

---

## ③ 现状

- 工具 **1.5.5**（`58c4e8e`，标签 `wf-v1.5.5`），19 个可回滚版本；看板 PID 72271 加载 1.5.5
- 项目 **v0.5**，指纹 `3345de3ba3bf580a`，工作区干净
- 定时器：官方配置已加载（PID 72344），每 300s 一次，`wf schedule status` 显示 0 分钟前
- 手写 for 循环已停
- 状态：第 16 轮 · WAIT_SCHEDULE（我提前做完了，本轮直接回报）
- 待办：**等 `build_wf_156.py`**（到了就构建安装，预期自检 177 项），然后核对 doctor 的"驱动器心跳"和 `wf ask` 的主动呈报

【本机执行】
```
（等 1.5.6 构建器）
```

【需要用户决定】
1. 是否开启"另一个模型系列审查"（你连续三轮提过，用户尚未表态，继续挂起）。
2. btc-quant 模拟盘进程 PID 70099 是否继续运行（一直挂起，保持现状）。

【下一轮建议档位】
high

【驱动指令】
发布：无
之后：回报
