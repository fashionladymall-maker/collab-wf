【回报 · 第 107 轮 · v3.3】档位：high

① 环境障碍与偏离
- 夜间停滞执行端侧已修：wake仅for=executor过滤导致升级task/user丢递送；新增delivery_owner兼容/旧escalated有action task与signal继续递送，user事项不自动批准；空code按完整project/key/task.done核验。memory_hint原指最新另窗口，与实际选中窗口不一致，现按真实target.cwd记忆；project忽略global sid pin且fallback限本项目/已授权本collab工作区，缺目标fail-closed不串投。负向/隔离回归14/14、py_compile通过，独立先BLOCK（错误记忆/globalpin）整改后PASS；真实选中当前窗口pid92293与本工作区记忆一致。03:01那次投递为何没完成仍需运行端证据，不能把runId当已执行。第106轮已正式接受且收到3文件/sha匹配，回复end_turn；book本机研究正文已真实送达且Claude确认收到。主工具1.8.0/项目3.3安装前审查正在进行，暂停是明确的质量边界，不是无人知道的旧DONE停止。
- 唤醒投递找不到可用会话：任务码 signal|review:collab-wf.tool.collab-wf-1.8.0 已等待，用户可能没开窗口；按 tick 节奏继续重试（不消耗重试次数，会话出现即补投）
- 独立审查边界实证纠正：第106轮项目v3.3的review request 6e01c3a9...07:09:07创建，07:13:37 timeout，旧驱动器按既有unreviewed路径落地v3.3，不能说安装前项目审查pass。执行端07:15:30发起暂停时构建c01已开始，现构建255/255且hash与作者一致，工具仍1.7.6；正在进行工具1.8.0安装前独立包/diff审查，项目v3.3只能标事后补审。暂停不截断已经在跑的原子构建，下一条self-update前应保持暂停。禁止事后把未审查史改成pass。
- [EXEC_FAIL] 命令执行失败。回报给 Claude。 第 2 条命令「wf self-update --auto workflow/outbox/collab-wf-1.8.0.zip」退出码 -15
- 【安装BLOCK，不是临时卡死】候选1.8.0 sync._stage_tree安全边界缺失已隔离复现：file symlink safe.txt指向stage外，write_bytes实际覆盖outside；dir symlink linked-dir指向stage外，实际创建outside/new.txt；两个repro=True。255/255只测既有用例，不证明symlink安全。工具仍1.7.6，项目v3.3已落地；暂停期间旧h_execute单handler未重读pause且继续到self_update审查，发现额外暂停边界漏洞。工具审查block递交时request已timeout，正式保留late block而非伪pass；已精确SIGTERM仅候选更新进程94591并收据c02=-15，旧driver已退出/无exec_inflight、状态VERIFY。请Claude发布新1.8.1（不复用1.8.0版本字节）修_stage_tree路径规范、stage根/父链/目标符号链接拒绝或安全替换，不写穿，负向测试root/file/dir link与路径穿越；修暂停在每条cmd之间重读。不得重新运行同一不安全安装包，不降低floor或skip hooks。
- 命令 c02 退出码 -15：`wf self-update --auto workflow/outbox/collab-wf-1.8.0.zip`
- 清单第 2 项未通过：驱动器自动安装 1.8.0，升级核验通过；独立审查的结论以原文为准。（独立静态检查_stage_tree符号链接写穿，file/dir两负向repro成立；review timeout后late block不追认pass，终止候选安装exit -15，1.8.0未安装）
- 清单第 3 项未通过：工具版本为 1.8.0。（仍1.7.6，1.8.0安全审查未通过，不能安装）

② 关键数字与终端原文

1) 构建通过，自检 255/255，floor.py 未改动；版本包的 sha256 与我这边一致。 —— 通过：本机构建255/255、hash一致、floor未改；不等于安全审查通过
```
$ /Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-106/delivered/build_wf_180.py --out workflow/outbox
基线核对：/Users/admin/.workbuddy-ai/tools/collab-wf 为 1.7.6，63 个文件与公开版本包逐字节一致
改动：51 处、新文件 1 个，编译通过；floor.py 未改动
正在运行完整自检……
自检：255 / 255 通过
成员集合：64 个文件 + MANIFEST.json，与清单一致
构建通过：collab-wf 1.8.0，自检 255/255
版本包：workflow/outbox/collab-wf-1.8.0.zip，968734 字节，sha256 5bd9e85b95071cfd21878434f722f1aae1abfc9a349c4d7391970fb71ea7582a
exit=0  耗时 151.6s
```

2) 驱动器自动安装 1.8.0，升级核验通过；独立审查的结论以原文为准。 —— 未通过：独立静态检查_stage_tree符号链接写穿，file/dir两负向repro成立；review timeout后late block不追认pass，终止候选安装exit -15，1.8.0未安装
```
$ wf self-update --auto workflow/outbox/collab-wf-1.8.0.zip
[stderr]
开始等独立审查（上限 300 秒；到时没有结论按“未审查”继续安装）
仍在等独立审查：已 30 秒 / 上限 300 秒
仍在等独立审查：已 60 秒 / 上限 300 秒
仍在等独立审查：已 90 秒 / 上限 300 秒
仍在等独立审查：已 120 秒 / 上限 300 秒
仍在等独立审查：已 150 秒 / 上限 300 秒
仍在等独立审查：已 180 秒 / 上限 300 秒
仍在等独立审查：已 210 秒 / 上限 300 秒
仍在等独立审查：已 240 秒 / 上限 300 秒
本次未审查：{"status": "unreviewed", "reason": "review deadline elapsed"}；照常安装，可事后按 /Users/admin/.workbuddy-ai/tools/collab-wf/local/reviews/collab-wf-1.8.0.diff 补审
正在运行新版本 1.8.0 的完整自检
exit=-15  耗时 304.7s
```

3) 工具版本为 1.8.0。 —— 未通过：仍1.7.6，1.8.0安全审查未通过，不能安装

4) self-check 显示与清单完全一致。 —— 暂缓：候选未安装，不能验证1.8.0清单，保留原工具

5) 落地 v3.3 后，仓库指纹为 cbad75d7e6df496b。 —— 通过：项目v3.3已经由旧driver落地，fingerprint实测cbad75d7e6df496b；项目review超时只能事后补审

6) wf goals 先显示“当前阶段 M2”，M1 列为历史阶段。 —— 暂缓：工具仍1.7.6不会显示新phase；目标新定义尚待批准/候选安全修复；不伪报M2通过

③ 需要决策的点
1. 执行端依用户授权自动批准 D4V（非密钥类），不再惊动用户。

④ 需要注意（Attention）
- 自动处置 EXEC_FAIL → report：第 2 条命令「wf self-update --auto workflow/outbox/collab-wf-1.8.0.zip」退出码 -15
- 决策（review）：独立审查不可用，按规则继续。理由：适配器返回 4：{"status": "unreviewed", "reason": "review deadline elapsed"}。证据：/Users/admin/.workbuddy-ai/projects/collab-wf-dev/workflow/evidence/turn-106/release.diff
- 清单第 4, 6 项没有机器判据，结论需要 Claude 根据原文判断
- goals.json 在批准后被改动，未生效
- 当前对话已累计约 43423 字符、4 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

附：仓库指纹 cbad75d7e6df496b（v3.3，10 个文件）
