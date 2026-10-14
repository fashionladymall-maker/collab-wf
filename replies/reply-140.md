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
[工具 present_files]
【本轮结论】

1.9.5 作废，本轮交付修正版 1.9.6，构建器为 build_wf_196.py（31549 字节，sha256 10e6785b…）。

1.9.5 没有装上。c2 等满 600 秒，审查没有到场，review_required 按设计拒绝了安装。之后执行端审读给出 block，指出的问题成立：完整性判据在 ops.intake 已经把状态持久转入 REVIEW 之后才判断，所以中途退出再重启、或者用命令行 wf intake，都能绕过这道前门。按版本号不复用的规则（W29），1.9.5 作废，同一个包不再安装。

1.9.6 在已安装的 1.9.4 上构建，包含 1.9.5 的全部内容，逐条处理审查意见。共改 12 个文件、29 处锚点，不改 floor.py：
- 完整性判据前移：在 ops.intake 转入 REVIEW 之前判断，与转入是同一步。评审、落地、执行三处共用一个守卫 ops.truncated_reason，在这三个状态里重启也会被拦下并转为续接，一条命令都不执行。wf review 对未完成的回复直接报错；wf intake 会写明“没有完成：不进入评审”。
- 轮询返回 read_error（适配器读不了通道）单独处置，规则名 POLL_READ_ERROR：
  - 不计入“没落地”，不清 ref，不重发，不换对话，也不占用“结果不明”的等待窗口；
  - 退避重试，12 次后交给执行端。
  - 适配器约定写进 TRANSPORT 附录 1.9.6：读不了通道时返回 read_error，不要返回 none。

  这一条对应第 139 轮的实情：适配器把 HTML 响应吞成了 none。
- wf tell 原样保留首尾空白，每段的段头写完整的 sha256。自检会按段头、段尾把各段拆回，逐字核对与原文一致。
- wf watchdog 重写 inbox 时，这次读不出状态的项目在 inbox 里原有的事项原样保留。

新增和改动的检查：
- 新增“完整性前门原子”：覆盖命令行 intake，以及 REVIEW、APPLY、EXECUTE 三个状态里重启的情形。
- 新增“读不了通道”：覆盖发送待确认与等待回复两条路径。
- “补充说明无损”与“看门狗按项目”两条扩展了断言。
- 没有删除任何检查。

我在容器里的结果：
- 在模拟的已安装 1.9.4 上构建，A、B、D 三种条件都是 290/290，包的 sha256 为 20270d70…。
- 用 1.9.4 自己的 self-update 安装 1.9.6：升级核验通过，self-check 完全一致，探针 41/41。

本轮仍是构建加一次安装尝试：c2 会发出审查请求，请执行端在 600 秒内对这个新包给出结论。来不及的话，c2 会被 review_required 拒绝、以退出码 1 结束，审查完成后下一轮重跑。c2 不写机器判据，由我看原文判断。

执行端在 wf tell 里提的“项目专属执行端绑定、禁止跨项目回退、迁移审计”，属于适配器和执行端的落地范围，工具侧的约定放在 1.9.6 之后设计，本轮不做。第 139 轮的处置按执行端 08:39 的更正：原回复已经送达，只沿原链路正式接收，不重发、不换对话、不换账号。

【本机执行】
```
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-140/delivered/build_wf_196.py --out workflow/outbox
wf self-update --auto workflow/outbox/collab-wf-1.9.6.zip
```

【本轮回报清单】
1. c1：构建器的原文。包括：HEAD 与 1.9.4 原文的核对、12 个文件 29 处锚点是否全部命中、打包的 sha256（应为 20270d70…）、A/B/D 三种条件的自检结果，以及“构建完成”那一行。
2. c2：安装命令的原文。审查结论到场就会装上，应有“1.9.4 → 1.9.6”与“自检已通过”；审查没到场则按 review_required 拒绝、退出码 1。由我看原文判断。

【需要用户决定】
无

【下一轮建议档位】
high

【驱动指令】
发布：无
回执：3bb1ff38
交付 1：build_wf_196.py 10e6785b2417b1483f0b07e11dd79d8d218fbfd39c7602fccb29219999a1a73d
执行端 1：c2 发出的安装前审查（包 collab-wf-1.9.6.zip，sha256 20270d702bbfaba4f7f0de0a09845d8ad7e0e1e2564a7ca855d16dd92bf99824，diff 为 local/reviews/collab-wf-1.9.6.diff）请尽量在 600 秒内正式提交结论；重点核对 1.9.5 的 block 意见（完整性前门原子、REVIEW/APPLY/EXECUTE 重启与命令行 intake 的守卫）是否解决；来不及时审完再提交，确认同一个包再次请求时会立即返回，用 wf task done 写回结论、意见原文、diff 的 sha256 与提交时刻；不要手工运行 self-update，也不要安装 1.9.5。
之后：回报
验收 1：c1 exit=0 包含 "构建完成" 包含 "20270d702bbfaba4f7f0de0a09845d8ad7e0e1e2564a7ca855d16dd92bf99824"
指纹：e1d316e281551ca5
超时：1500
失败后继续：否