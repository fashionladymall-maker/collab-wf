[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 present_files]
【本轮结论】

v3.12 已经落地，仓库指纹 b66b4395f4223063，与预期一致。这次项目版本的独立审查没有到场，驱动器按规则继续，没有阻挡落地。

本轮交付三会话 180 秒巡查的演练版 patrol-0.1.zip（14757 字节，sha256 95926568…）。三条前提都已满足：执行端第 143 轮补齐了 hook 结构、输入方式和退出码；一次性会话上的并发实验结论是 UNKNOWN；执行端也建议先交演练版。

需要明确的一点是：**这是演练版，不是完整的唤醒闭环**。真正的续用投递（官方 CLI `-p -r`）要同时满足两个条件才会放行：
1. patrol.json 里把 resume_enabled 打开；
2. 有一份在一次性会话上做的、结论为 safe 的并发实验记录。

现在两个条件都不满足，所以续接验证照实记为“阻塞”。

包里有六个文件，全部装在工具目录之外的 adapter/patrol/：

- patrol.py：巡查主体，提供 run、check、status、selftest 四个命令，只用标准库。
  - 每轮先取非阻塞文件锁，锁被占用就记录后跳过，保证互斥。
  - 再看暂停门：patrol.pause 或项目暂停。
  - 然后逐个会话判定 idle、busy、unknown，并写明依据。
    - idle：最近一次 Stop 晚于最近一次输入，且 Stop 和 transcript 都已静默满 180 秒。这是自定判据，不说成是官方的空闲判定。
    - unknown 一律不投递。
  - 事项状态只能往前走：排队 → 送达（CLI 成功，且返回的会话 id 一致）→ 执行（该会话收到带本事项标记的输入）→ 完成（之后出现 Stop）→ 验收（只读判据成立）。
  - main 的事项必须等它依赖的 repair 事项验收之后，才会被投递。
- patrol_hook.py：只记录事件的 hook。
  - 只记三个绑定会话的 UserPromptSubmit、Stop、SessionEnd。
  - 输入正文不落盘，只记标记和 sha256。
  - 任何情况下都以退出码 0 结束，不会阻断宿主（退出码 2 才会阻断）。
- patrol.json：三个会话 id、180 秒周期，resume_enabled 为 false。
- hooks-snippet.json：要合并进宿主 hook 配置的条目，格式按宿主源码 2460 行。
- 独立的 launchd 任务：间隔 180 秒，不改任何已有任务的间隔。
- README：写明安装、九项验收谓词、回滚步骤和未完成项。

我这边自检 16/16 通过，覆盖这些场景：
- 源码里没有读取环境变量键、cookie、钥匙串或密码；
- 会话状态的三种判定，以及“transcript 刚有修改就判 busy”；
- 演练模式下不调用 CLI；只打开开关、没有并发实验记录时仍然阻塞；
- 闸门打开后，只投递给空闲的 repair，main 等前置事项验收；
- 去重、按证据逐级推进到验收、暂停门、锁互斥；
- 检查器在正常日志上九项全过，在“闸门关闭却有投递”的反例上判 P8 不通过；
- hook 只记绑定会话、不落原文，遇到坏输入也以 0 退出。

还没做完、照实写为阻塞的几项：
- 同一会话的并发 resume，以及图形界面同时运行时的行为，都没有证实；
- 非工具事件的 matcher 写空串是否会匹配全部事件；
- 三个会话实际读取的是哪一份 hook 配置；
- 事项还没有接入 collab-wf 的 inbox，演练期间由执行端手工往 queue 里放测试事项。

另外记下执行端第 143 轮的实景测量结果：wf live-check 第 140–142 轮连续 0/3。原因是适配器的发送日志、发送消息的 uuid、回复的 parent 和 ended_at 都缺失。这几项需要执行端在适配器里接上，M2-R1 才有可能通过；工具这边不能代替适配器填这些字段。

【本机执行】
无

【本轮回报清单】
无

【需要用户决定】
无

【下一轮建议档位】
high

【驱动指令】
发布：无
回执：3f33b2fd
交付 1：patrol-0.1.zip 95926568f4ce7748de13be3bf8d88b47eeaa8da1136f42635386db4ce82b8de8
执行端 1：按 patrol-0.1.zip 里 README 的“安装”在演练模式下落地并验收（resume_enabled 保持 false，不 resume 任何会话、不运行 wf tick、不改已有 launchd 任务）：① 解压到 /Users/admin/.workbuddy-ai/adapter/patrol/，核对包的 sha256 后运行 patrol.py selftest，应为 16/16；② status 的 config_problems 为空、resume_gate 为“演练模式”；③ 先核实 main、repair、live 三个会话实际读取的 hook 配置文件，再合并 hooks-snippet.json 的三个事件（保留原有条目）；核实不了就停在这一步、写明原因，不加载 launchd；④ 加载独立的 launchd 任务，至少运行 3 轮后执行 patrol.py check --cycles 10，并检查 events.jsonl 里三个会话是否都出现过真实的 hook 事件。各步原文（selftest、status、check 的输出，events.jsonl 末尾 10 行，加载 launchd 的命令与结果）写进 wf task done；任何一步不通过就停下，用 touch patrol.pause 停止投递，写明原因。
执行端 2：（不阻塞）适配器接上实景测量需要的字段：发送日志按项目与轮次记录、发送时给出用户消息的 uuid、poll 完成时给出回复的 parent 与可信的 ended_at；改完用一轮真实收发核对 reply_meta.json 与 send_meta.json 的原文，写进 wf task done。
之后：等执行端