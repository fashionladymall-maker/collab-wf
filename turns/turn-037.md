【回报 · 第 37 轮 · v1.4】档位：medium

① 环境障碍与偏离
无。

② 关键数字与终端原文

1) 构建通过，自检 199/199。 —— 通过
```
$ /Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-036/delivered/build_wf_162.py --out workflow/outbox
collab-wf 1.6.2 构建器；Python 3.13.12（/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3）
[1/6] 克隆 /Users/admin/.workbuddy-ai/tools/collab-wf
      HEAD c8547bd = wf-v1.6.1
[2/6] 核对要改的文件
      ✓ VERSION  6430a242e948
      ✓ wflib/anomaly.py  273f4c15a526
      ✓ wflib/selftest_v15.py  87ce409865e9
      ✓ docs/SYNC.md  4e624693e6f8
      ✓ docs/REVIEW.md  1f4900d88885
      ✓ CHANGELOG.md  89a17f465ac5
      ✓ wflib/floor.py  9bbef5440812
[3/6] 应用 7 处锚点改动，写入 0 个新文件
      ✓  1. wflib/anomaly.py：anomaly-call-program-first
      ✓  2. wflib/anomaly.py：anomaly-program-ops
      ✓  3. docs/SYNC.md：sync-doc-program
      ✓  4. docs/REVIEW.md：review-doc-program
      ✓  5. wflib/selftest_v15.py：selftest-v162-funcs
      ✓  6. wflib/selftest_v15.py：selftest-v162-register
      ✓  7. CHANGELOG.md：changelog
      VERSION → 1.6.2
 CHANGELOG.md          |  7 +++++++
 VERSION               |  2 +-
 docs/REVIEW.md        |  2 +-
 docs/SYNC.md          |  2 +-
 wflib/anomaly.py      |  9 +++++++++
 wflib/selftest_v15.py | 26 +++++++++++++++++++++++++-
 6 files changed, 44 insertions(+), 4 deletions(-)
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

自检结果：199 / 199 通过
      ✓ 自检通过（退出码 0）
[6/6] 打包
      /Users/admin/.workbuddy-ai/projects/collab-wf-dev/workflow/outbox/collab-wf-1.6.2.zip（58 个文件，284437 字节，sha256 e177fc83c512）
构建通过。安装：wf self-update --auto workflow/outbox/collab-wf-1.6.2.zip（驱动器执行；本版不改 floor.py，可自动安装）
exit=0  耗时 78.1s
```

2) 驱动器自动安装 1.6.2，升级核验通过。 —— 通过
```
$ wf self-update --auto workflow/outbox/collab-wf-1.6.2.zip
工具已升级：1.6.1 → 1.6.2（47e3818），自检已通过
看板已重启以加载新版本
[stderr]
升级核验通过：磁盘版本 1.6.2，58 个文件与版本包一致，看板已加载 1.6.2
exit=0  耗时 102.1s
```

3) 工具版本为 1.6.2。 —— 通过
```
$ wf version
collab-wf 1.6.2（wf-v1.6.2）  安装位置：/Users/admin/.workbuddy-ai/tools/collab-wf
可回滚的版本：wf-v1.6.2、wf-v1.6.1、wf-v1.6.0、wf-v1.5.9、wf-v1.5.8、wf-v1.5.7、wf-v1.5.6、wf-v1.5.5、wf-v1.5.4、wf-v1.5.3、wf-v1.5.2、wf-v1.5.1、wf-v1.5.0、wf-v1.4.3、wf-v1.4.2、wf-v1.4.1、wf-v1.4.0、wf-v1.3.1、wf-v1.3.0、wf-v1.2.2、wf-v1.2.1、wf-v1.1.0、wf-v1.0.3、wf-v1.0.2、wf-v1.0.1、wf-v1.0.0
当前项目 collab-wf-dev 固定版本：1.6
exit=0  耗时 0.4s
```

4) 落地 v1.4 后指纹为 44cc3767d43e4af0。 —— 通过
```
$ wf fingerprint
仓库指纹 44cc3767d43e4af0（7 个文件）
exit=0  耗时 0.6s
```

③ 需要决策的点
无。

④ 需要注意（Attention）
- 当前对话已累计约 173416 字符、16 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

附：仓库指纹 44cc3767d43e4af0（v1.4，7 个文件）
附：发送前额度 5 小时窗口 31.0%，7 天窗口 66.0%
