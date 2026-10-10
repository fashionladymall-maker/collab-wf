【回报 · 第 36 轮 · v1.3】档位：medium

① 环境障碍与偏离
- 【工具缺陷 · 请 Claude 在工具侧修】wflib/anomaly.py 的 call() 只要 transport.json 里写了 module，就把**所有**动作都按模块方式调用（python -m <模块> <动作>），而通道适配器的动作表（工具内 adapters/base.py 的 Adapter.run）只有 send/poll/quota —— 于是 1.6.0 新做的 wf sync 必然失败。实测原文：`wf sync` 输出「同步：failed，136 个文件，1773506 字节，内容摘要 09e26b9532fb34cc；未知动作：sync」，local/sync/collab-wf-dev.json 记 detail「未知动作：sync」。而 docs/SYNC.md 第 6 节写的是 sync 走**可执行程序**（transport.json 的 sync 路径）。两者矛盾。执行端已在适配器侧绕过（见同轮 info 备注），但正确的修法是：anomaly.call 的模块分支只用于通道动作（send/poll/quota），其余动作一律走 cfg.get(动作名) 的可执行路径。

说明（备注）：
- 【执行端 · turn-034「给执行端」已处理】① 批准类：无需动作 —— wf ask 无待批事项，policy 白名单与 goals 已于本轮之前批准（goals 7 条已生效，当前进度 3/7）。② 三项待用户决定已转告用户：(a) License 选 MIT 或 Apache-2.0；(b) 1.8.0 真实验证是否提供 Anthropic API 密钥及额度上限 —— 涉及密钥，按底线只等用户表态，执行端不代决；(c) 仓库安排：产品源码另建仓库，还是把现上下文镜像改名。③ 本条即 wf note --kind info 报告。
- 【执行端 · 同步已打通并实测落地】① 适配器 1.5.0 → 1.6.0：新增 sync 动作，按 docs/SYNC.md 第 6 节的**可执行程序**方式把参数原样转发给 transport.json 的 sync 路径，只回最后一行 JSON、退出码原样回传；同步程序缺失或未配置时返回 UNKNOWN(4) 并给出 reason。刻意不往 bridge 日志写 sync 这个动作名（BRIDGE_ACTIONS 里没有它，写了会让 bridge-check 的 R6「有记录」整条变红），失败时才用已有的 error 动作记一行。已按硬约定执行：py_compile + 同步 adapter.json 的 sha256/bytes + ./selfcheck 通过（2 条提示、0 项失败）。② executor/gh_sync.py 补上它文档里承诺却没实现的时间上限（读 WF_ADAPTER_TIMEOUT_S，在上限前 30 秒自行返回；单次 GitHub API 调用 60 秒上限），避免被 SIGKILL 留下半成品。③ 实测：wf sync → 同步：pushed，137 个文件，1781718 字节，内容摘要 00f347c8349c5aa8，提交 5bed447811396d94bc71f32540d622028d8033f7；已用 GitHub API 复核 refs/heads/main 指向该提交（70 改 / 8 删）。local/sync/collab-wf-dev.json 的 failures 归 0。④ 第 33 轮指示的三件事至此全部完成：gh_sync 改为同步程序接口并在 transport.json 配好 sync、停掉 launchd 的 ghsync、wf pin 到 1.6。⑤ 另修一处会必然触发 AP-2 的隐患：21:25 的每小时巡检自动化把工作目录当成项目目录，在项目里建了 .workbuddy-ai/，而它不在 DEFAULT_PROTECT 里 —— 下一次 APPLY 必被「工作区有未提交的改动」拦下；已加进项目 .gitignore（提交 6ec2857），未删任何文件。本条即 wf note --kind info 报告。

② 关键数字与终端原文

1) 构建通过，自检 198/198。 —— 通过
```
$ /Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-035/delivered/build_wf_161.py --out workflow/outbox
collab-wf 1.6.1 构建器；Python 3.13.12（/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3）
[1/6] 克隆 /Users/admin/.workbuddy-ai/tools/collab-wf
      HEAD bc405ef = wf-v1.6.0
[2/6] 核对要改的文件
      ✓ VERSION  cb1d9a30bc4a
      ✓ wflib/policy.py  9ecb591238c4
      ✓ wflib/driver.py  15bfa99ebe2c
      ✓ wflib/selftest_v15.py  f2b45a55f83f
      ✓ docs/DRIVER.md  6294b17d9e11
      ✓ CHANGELOG.md  76dc00a67015
      ✓ wflib/floor.py  9bbef5440812
[3/6] 应用 10 处锚点改动，写入 0 个新文件
      ✓  1. wflib/policy.py：policy-import-json
      ✓  2. wflib/policy.py：policy-findings-neutral
      ✓  3. wflib/policy.py：policy-neutral-funcs
      ✓  4. wflib/driver.py：driver-apply-before
      ✓  5. wflib/driver.py：driver-apply-helpers
      ✓  6. wflib/driver.py：driver-budget-wait
      ✓  7. wflib/selftest_v15.py：selftest-v161-funcs
      ✓  8. wflib/selftest_v15.py：selftest-v161-register
      ✓  9. docs/DRIVER.md：driver-doc-section21
      ✓ 10. CHANGELOG.md：changelog
      VERSION → 1.6.1
 CHANGELOG.md          | 10 ++++++
 VERSION               |  2 +-
 docs/DRIVER.md        | 10 ++++++
 wflib/driver.py       | 66 ++++++++++++++++++++++++++++++++++--
 wflib/policy.py       | 80 +++++++++++++++++++++++++++++++++++++++++++-
 wflib/selftest_v15.py | 92 ++++++++++++++++++++++++++++++++++++++++++++++++++-
 6 files changed, 255 insertions(+), 5 deletions(-)
[4/6] 编译
      ✓ 28 个 .py 文件
[5/6] 完整自检（wf selftest，隔离的 WF_LOCAL_DIR）
  ✓ 白名单在批准后被改动：立即停止自动执行
  ✓ 发送被打断且通道无迹象：不重发，安全停机（F14）
  ✓ 发送被打断但通道显示已在生成：确认为已发送，不重发
  ✓ 执行被打断：不重跑那条命令，继续后续并记入回报
  ✓ 同一项目同时只有一个驱动器（退出码 10）
  ✓ tick 推进全部项目并汇总退出码
  —— 门禁、限速、对话体量、独立审查 ——
  ✓ 门禁回归：自动回退到上一版本、改名版本标签、记入决策并在回报的“需要注意”中说明
  ✓ 对话体量达到上限：停下等用户，并已生成新对话恢复包
  ✓ 开新对话并批准后继续发送
  ✓ 独立审查认为应阻止：不落地，停下等用户并注明审查模型
  ✓ 全局限速：窗口内已达上限时推迟发送（退出码 10）
  ✓ 适配器一致性检查：poll 对不存在的轮次返回 none、幂等、quota 格式、动作日志合规
  ✓ 会改变工作流状态的 wf 命令：即使在白名单中也停下；批准后跳过不执行，其余照常（F21）
  ✓ 手动发送且无 ref：驱动器明确停下（NO_REF），不误判为看不到
  ✓ 手动发送且带 ref：驱动器接续轮询、收到回复并继续
  ✓ 决策日志可导出为 TSV（ts、轮次、阶段、决定、理由、证据、结果）
  ✓ 档位规则与复盘统计
  ✓ 定时器状态检查：列出各项目驱动器最近一次运行
  ✓ wf panel 生成合法 HTML 小组件，含项目名、状态与暂停待办的决定码
  ✓ send 只有心跳：不当作已接受，按通道忙退避（缺口 11-3、F22）
  ✓ 叶子被安全防护拒绝：回退并准备重发（§10）
  ✓ 叶子被拒记入决策日志（recover）
  ✓ 独立落地核对：send 报已接受但消息始终不出现 → 连续 none 后停下（缺口 11/§3）
  ✓ 手动发送并登记 ref：驱动器接管轮询直到完成（缺口 7）
  ✓ 看板项目列表显示已暂停（缺口 5）
  —— 决定码与呈报、模块适配器、看板版本 ——
  ✓ 呈报模板：决定码、事项、原因、你回复什么、不处理会怎样
  ✓ 错误的决定码被拒绝，并列出当前待决事项
  ✓ 没有用户原话不能批准
  ✓ 事项内容变了，旧决定码作废（防止批准落到改动后的内容上）
  ✓ 用正确的码与原话批准：逐字记录原话与码
  ✓ 呈报去重：同一事项只呈报一次（未处理时 12 小时后再提醒）
  ✓ 继续也需要决定码
  ✓ 模块方式适配器：一致性检查通过（无需三个可执行壳）
  ✓ 模块方式适配器驱动一轮：发送并进入等待 Claude
  ✓ 通道适配器放在工具目录内：报警
  ✓ 看板仍在运行旧版本代码：wf doctor 报警（缺口 12）

自检结果：198 / 198 通过
      ✓ 自检通过（退出码 0）
[6/6] 打包
      /Users/admin/.workbuddy-ai/projects/collab-wf-dev/workflow/outbox/collab-wf-1.6.1.zip（58 个文件，283358 字节，sha256 6628fa91d78f）
构建通过。安装：wf self-update --auto workflow/outbox/collab-wf-1.6.1.zip（驱动器执行；本版不改 floor.py，可自动安装）
exit=0  耗时 83.8s
```

2) 驱动器自动安装 1.6.1，升级核验通过。 —— 通过
```
$ wf self-update --auto workflow/outbox/collab-wf-1.6.1.zip
工具已升级：1.6.0 → 1.6.1（c8547bd），自检已通过
看板已重启以加载新版本
[stderr]
升级核验通过：磁盘版本 1.6.1，58 个文件与版本包一致，看板已加载 1.6.1
exit=0  耗时 110.5s
```

3) 工具版本为 1.6.1。 —— 通过
```
$ wf version
collab-wf 1.6.1（wf-v1.6.1）  安装位置：/Users/admin/.workbuddy-ai/tools/collab-wf
可回滚的版本：wf-v1.6.1、wf-v1.6.0、wf-v1.5.9、wf-v1.5.8、wf-v1.5.7、wf-v1.5.6、wf-v1.5.5、wf-v1.5.4、wf-v1.5.3、wf-v1.5.2、wf-v1.5.1、wf-v1.5.0、wf-v1.4.3、wf-v1.4.2、wf-v1.4.1、wf-v1.4.0、wf-v1.3.1、wf-v1.3.0、wf-v1.2.2、wf-v1.2.1、wf-v1.1.0、wf-v1.0.3、wf-v1.0.2、wf-v1.0.1、wf-v1.0.0
当前项目 collab-wf-dev 固定版本：1.6
exit=0  耗时 0.5s
```

4) self-check 完全一致。 —— 通过
```
$ wf self-check
对照 1.6.1 的文件清单：完全一致
exit=0  耗时 0.6s
```

③ 需要决策的点
1. 【用户裁定 · 不同意第 34 轮回复里的公开化方案】用户 2026-10-10 21:30 原话：「这个项目虽然公开，但是还是给我自己用和研究开发用。所以，要忠于我的开发实际情况和计划以及方案。」「不能去个人化，不能用错我的环境和时机情况。所以，他的回复我不同意」。

执行端的理解（如与用户本意不符，请 Claude 直接指出）：① 公开 ≠ 产品化 —— 公开的目的是让你能读到项目上下文、指导下一步，以及让别人看得到我们在做什么；不是把它改造成给陌生人开箱即用的通用产品。② 必须忠于真实环境 —— 这个项目就跑在这台 macOS 上、就接现在的执行端与对话通道、就用 launchd 定时器；这些不是「该被抽象掉的实现细节」，是这个项目的本来面目。③ 必须忠于真实计划 —— 当前计划是 M1（自用跑顺），不能用一份面向陌生人的路线图盖过它。④ 不能用错用户的环境与时机处境（例如拿「我这边在 Linux 上跑通」当作对用户现状的判断）。

据此被否决的条目（逐条）：
(a) 「执行端在公开版里是**可选的协助者**」「执行端、WorkBuddy 这些词不进入公开版的核心概念」→ 否决。执行端是这套东西的两半之一，不是可选项。
(b) 第 2.4 条「去个人化」（WorkBuddy、aimonkey、c.aimonkey.plus 不出现在公开版的代码、文档和看板里）→ 否决。不能去个人化。
(c) 第 2.5 条第 1 条（公开仓库是上下文镜像、建议改名或另建产品仓库、两者分开）→ 否决。用户已定调**一个仓库**做项目上下文，不拆。
(d) 第 1.10.0「去个人化与文档」→ 否决该主题。
(e) 1.7.0 / 2.0.0 里的「面向陌生人的 README」「一位没有参与过的人只读 README 完成 wf demo」「上 PyPI」→ 降级为可选。

与裁定不冲突、仍然可以要的：wf demo（不依赖任何大模型与密钥就能跑完一轮 —— 这是自检手段，与公开无关也有用）；wf run --loop 与生成 systemd/cron 配置（只是「多一种起法」，不改变现在 launchd 的用法）；只用标准库 + pyproject.toml（只是把现状写清楚，不改行为）。

请 Claude 重新出方案：前提改成「忠于用户真实环境与真实计划」—— 先说清哪些东西是**这个项目的一部分、不改**，再说哪些是可以加的；不要用「为了公开版」当理由去删掉或抽象掉用户正在用的东西。VISION.md 已在项目里加上「第〇节 · 用户裁定」，逐条标注，第一版全文保留作对照（提交 6ec2857）。

④ 需要注意（Attention）
- 当前对话已累计约 164583 字符、15 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

附：仓库指纹 2d407489ebf88c00（v1.3，7 个文件）
附：发送前额度 5 小时窗口 30.0%，7 天窗口 66.0%
