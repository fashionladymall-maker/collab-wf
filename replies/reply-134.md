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
[工具 present_files]
【本轮结论】

输入齐了，交付 1.9.4 构建器 build_wf_194.py（39110 字节，sha256 290938ff…）。

输入核对：
- 1.9.3 候选包：从钉住的提交 7f9ad5a… 下载，sha256 ad541aea… 一致。
- 审查结论原文和校正复现汇总都完整收到。
- 第三份（A 组 failure-context）在“sha256：54f5”处被截断了。这份不影响开发，因为失败行在复现汇总里已经有原文。但 wf tell 附带的内容被截断却没有任何提示，这本身是个保真问题，交给执行端 2 查。

1.9.4 的内容。它在 1.9.1 上构建，包含 1.9.2、1.9.3 的全部内容，不改 floor.py：
- 自检隔离改在进程边界上。wf selftest 先起一个子进程跑全部检查，子进程从第一条检查起就是 WF_NOTIFY=0，WF_LOCAL_DIR 指向临时目录。父进程的环境一项都不改，所以不存在“漏恢复”。构建器不再预设 WF_NOTIFY。
- “自检不发通知”这条检查改走真实路径 notify.after_tick：
  - 对照组：WF_NOTIFY=1，用替身 osascript（只记录、不执行），确认这条路径确实会发通知。
  - 实验组：WF_NOTIFY=0，确认一条都不发。
  - 不再吞异常。
- 自检失败时，完整输出写进工具目录的 local/selftest-logs/，提示里列出全部 ✗ 行和文件路径。
- ad54 审查的 concerns 逐条处理：
  - ACTOR：在 driver.run 结束或出错后恢复成进入前的值。
  - send_meta：发送结果不明、之后经轮询确认送达时也记，注明 confirmed_by=poll。适配器没给 uuid 就不写，不编造。
  - 审查时刻：按开始安装的时刻核对。结论早于这次安装命令、没有时刻、晚于开始安装，都判为不通过。
  - 同步 HEAD：记的 HEAD 必须等于项目在同步时刻的 HEAD。
  - 目标检查命令：改为按允许清单证明只读（policy.readonly_check）。
- sync.py 那一行修正正式收进来，并补了一条带 .git 的回归测试。用 1.9.1 的 sync.py 跑这条测试，报的正是 `TypeError: 'PosixPath' object is not iterable`；换成新版就通过。

受影响的旧断言（按协作约定列出，没有删除或跳过任何检查）：
- 1.9.3 的 _t_no_desktop_notify 整条重写。
- _t_live_check 改了三处：
  - 测试项目改为带版本库；
  - 安装事件补上 duration_s；
  - 加了审查时刻和 HEAD 的反例。

我在容器里的结果（Linux、Python 3.12.3，不是你的 macOS 3.13.12，所以本机运行才是最终证据）：
- 构建器在 A（没有 WF_NOTIFY）、B（WF_NOTIFY=0）、D（WF_NOTIFY=1）三种条件下，自检都是 285/285。
- 两次构建得到同一个包：68 个文件，sha256 3386fb1fa31c794ecfd20ec56dd26c5e2ede460923b689f25288cdf3f70b6b65。
- 模拟安装：在模拟的 1.9.1 工具目录里，sync.py 有未提交改动时，self-update 拒绝安装；恢复之后，用 1.9.1 自己的 self-update 装上 1.9.4，升级核验通过，self-check 完全一致，探针 36/36。

安装为什么分两轮：self-update 要求工具目录没有未提交的改动。所以本轮只构建，构建完成后由执行端把 sync.py 恢复到 HEAD；下一轮再由驱动器运行 `wf self-update --auto`。构建器只读工具目录 HEAD 里的内容，不受工作树改动影响。从恢复到安装之间，同步历史会重新出错，但 sync.after_tick 会接住异常，推送本身照常，只是少记几条历史，影响可以接受。

按现行设计，安装时的审查结论如果是 concerns，不会阻止安装（只有 block 才会）。下一轮照此执行。

【本机执行】
```
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-134/delivered/build_wf_194.py --out workflow/outbox
```

【本轮回报清单】
1. c1：构建器的原文。包括：工具目录 HEAD 与 1.9.1 原文的核对、14 个文件 26 处锚点是否全部命中、打包的 sha256（应为 3386fb1f…）、A/B/D 三种条件各自的自检结果，以及“构建完成”那一行。

【需要用户决定】
无

【下一轮建议档位】
high

【驱动指令】
发布：无
回执：45ee7429
交付 1：build_wf_194.py 290938ff659700bad9392f1b723110c4dc815f01c6f2124861a8bc6b1c6320e1
执行端 1：先核对 c1 通过（输出有“构建完成”且 sha256 为 3386fb1fa31c794ecfd20ec56dd26c5e2ede460923b689f25288cdf3f70b6b65）；通过的话运行 git -C /Users/admin/.workbuddy-ai/tools/collab-wf checkout -- wflib/sync.py，再运行 wf version（应为 wf-v1.9.1，不带 -dirty）与 wf self-check（应为完全一致），原文写进 wf task done；c1 未通过就不恢复，写明原因；不要运行 self-update。
执行端 2：（不阻塞）查明第 134 轮回报里 wf tell 附带的第三份原文为什么在“sha256：54f5”处中断，是 wf tell 排队、回报预算还是发送环节截断的，截断时有没有任何提示；只报告，不重发那份原文。
之后：等执行端
验收 1：c1 exit=0 包含 "构建完成" 包含 "3386fb1fa31c794ecfd20ec56dd26c5e2ede460923b689f25288cdf3f70b6b65"
指纹：29a0181934f5d903
超时：1500
失败后继续：否