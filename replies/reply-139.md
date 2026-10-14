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
Now update the old test and add the new ones.
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

v3.11 已经落地，落地后的仓库指纹是 e1d316e281551ca5，与预期一致。项目版本的独立审查这次没有到场，驱动器按规则继续。落地前后的工具门禁都跑过；需要的话，执行端可以事后按 turn-138/release.diff 补审。

本轮交付 1.9.5 的构建器 build_wf_195.py（23553 字节，sha256 ce4345c5…）。1.9.5 在 1.9.4 上构建，改 10 个文件、18 处锚点，不改 floor.py：
- 回复完整性前门（用户要求“没完成不能算完成”）：回复因工具调用或长度上限结束时，即使固定块齐全，也不落地、不执行，按新规则 REPLY_TRUNCATED 续接。续接消息写明结束原因，要求把命令和驱动指令完整重写；连续续接 3 轮仍不完整就停下等人看。1.8.2 的旧做法是“照常执行、下一条再补齐”，从此不再使用。
- 等执行端待办时（W101），explain 和看板写明“需要：执行端”和待办编号，并说明定时器只会超时回报，不会替执行端完成。
- wf tell 不再静默截断（W98）：超过 4000 字分段排队，每段写明第几段、全文长度和 sha256；单次超过 40000 字直接拒绝，并说明原因。
- wf watchdog --project 只检查指定的项目；重写 inbox 时仍带上全部项目，不会抹掉别的项目的事项。schedule status 按项目区分这一项没有做，留到后面。
- 开启 review_required 时，审查开始行改成“到时没有结论按 review_required 不安装”。

受影响的旧断言（按约定列出，没有删除任何检查）：
- “回复完整性”一条改为新行为：截断后续接、不执行。旧的“下一条补齐”附加逻辑保留给升级前留下的状态，仍照旧检查。
- 新增 3 条：等执行端的说明、补充说明无损、看门狗按项目。

我在容器里的结果：
- 在模拟的已安装 1.9.4 上构建，A、B、D 三种条件都是 288/288，包的 sha256 为 08fbb6ff…。
- 用 1.9.4 自己的 self-update 安装 1.9.5：升级核验通过，self-check 完全一致，探针 39/39。

本轮直接构建并安装：
- c1 构建，有机器判据：要求完成，且 sha256 与我这边一致。
- c2 用 wf self-update --auto 安装，它会发出审查请求。

请执行端在 c2 等待审查的 600 秒内给出正式结论。来不及也没关系：review_required 会拒绝安装，c2 以退出码 1 结束，审查完成后下一轮重跑同一条命令即可，和 1.9.4 的流程相同。所以 c2 不写机器判据，由我看原文判断，失败不计入熔断（W99 的教训）。装上之后的版本、self-check 和探针核验放到下一轮，免得 c2 没装上时它们被记成未执行的失败。

【本机执行】
```
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-139/delivered/build_wf_195.py --out workflow/outbox
wf self-update --auto workflow/outbox/collab-wf-1.9.5.zip
```

【本轮回报清单】
1. c1：构建器的原文。包括：HEAD 与 1.9.4 原文的核对、10 个文件 18 处锚点全部命中、打包的 sha256（应为 08fbb6ff…）、A/B/D 三种条件的自检结果，以及“构建完成”那一行。
2. c2：安装命令的原文。审查结论到场就装上，应有“1.9.4 → 1.9.5”与“自检已通过”；审查没到场，则是按 review_required 拒绝、退出码 1。两种情况由我看原文判断。

【需要用户决定】
无

【下一轮建议档位】
high

【驱动指令】
发布：无
回执：73f55fa1
交付 1：build_wf_195.py ce4345c5ad11b8a7cc28980231d4c6d5ca5fa70c39e3ffa491c0d9d60315dfa1
执行端 1：c2 发出的安装前审查（包 collab-wf-1.9.5.zip，sha256 08fbb6ff5857d1d7da6b43e5bf70672880353db1e54bf1f1bd9cc50bdf955202，diff 为 local/reviews/collab-wf-1.9.5.diff）请尽量在 600 秒内正式提交结论；来不及时审完再提交，确认同一个包再次请求时会立即返回，用 wf task done 写回结论、意见原文、diff 的 sha256 与提交时刻；不要手工运行 self-update。
之后：回报
验收 1：c1 exit=0 包含 "构建完成" 包含 "08fbb6ff5857d1d7da6b43e5bf70672880353db1e54bf1f1bd9cc50bdf955202"
指纹：e1d316e281551ca5
超时：1500
失败后继续：否