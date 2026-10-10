[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 present_files]
【本轮结论】

第 36 轮四项都通过：1.6.1 由驱动器自己构建、自动安装，自检 198/198，self-check 完全一致。gh_sync 切换、`wf pin` 到 1.6、`.workbuddy-ai/` 加进 .gitignore，这几件都收到了。

**用户的裁定：我接受，第 34 轮的方案是我错了（记为 W36）。** 执行端的理解和用户本意一致，我没有要纠正的地方。我犯了三个错：
1. 没有先问清「公开」是为了什么，就把它当成了产品化；
2. 拿我自己容器里 Linux 上跑通自检这件事，当成了对用户环境的判断；
3. 用一份面向陌生人的路线图，盖过了 M1 这个当前计划。

还有一处：不内置网页通道适配器，理由只能是「它是自用通道」，不该写成「为了公开版」。执行端在第〇节里的这个更正是对的。

**本轮交付两件。**

1. **collab-wf-dev-v1.4.zip。** 第〇节「用户裁定」原文保留，其余按裁定重写：
   - **不改的部分**：执行端是两半之一；aimonkey 通道和它在工具目录之外的适配器；macOS 与 launchd、每 60 秒一次 tick；一个公开仓库就是项目上下文；执行端、WorkBuddy、aimonkey 这些词照常使用。
   - **当前计划**，按实际进度排：M1 第一位；其次是及时性，从 M1 计数里实际出现的中断入手，逐个消除原因；已知缺陷先修。
   - **可以加的**，只加不改，优先级排在后面：`wf demo`、pyproject.toml、`wf run --loop` 与 systemd、cron 配置。
   - **暂缓**：License、面向陌生人的文档、PyPI。

   PROJECT.md 同步改写：已确认结论 29 改写；「下一步」去掉 1.7.0–2.0.0；待用户决策清空，我上一轮提的 API 密钥、仓库安排两项也撤回；新增 W36、W37。落地后共 7 个文件，指纹应为 `44cc3767d43e4af0`。这个值是我从公开仓库取到你改过的 VISION.md 和 .gitignore，先重算出现在的 `2d407489ebf88c00` 对上之后，再算出来的。

2. **build_wf_162.py（sha256 `a17777277942`），修正 W37。** 你的诊断和修法都对：transport.json 里 `sync`、`review` 写了程序路径时，即使通道是模块方式，也直接运行这个程序；写 `true` 时仍交给模块适配器的子命令；send、poll、quota 不变。在 1.6.1 的完整源码上端到端构建，自检 199/199。用 1.6.1 的 `floor.probe` 和 `guard_changes` 预检，结果都为空，所以由驱动器自动安装。你在适配器里加的 sync 转发继续可用，可以留着。

给执行端：本轮不需要做任何事。

【本机执行】
```
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-036/delivered/build_wf_162.py --out workflow/outbox
wf self-update --auto workflow/outbox/collab-wf-1.6.2.zip
wf version
wf fingerprint
```

【本轮回报清单】
1. 构建通过，自检 199/199。
2. 驱动器自动安装 1.6.2，升级核验通过。
3. 工具版本为 1.6.2。
4. 落地 v1.4 后指纹为 44cc3767d43e4af0。

【需要用户决定】
无。

【下一轮建议档位】
medium

【驱动指令】
发布：collab-wf-dev-v1.4.zip
之后：回报
验收 1：c1 exit=0 包含 "构建通过"
验收 2：c2 exit=0 包含 "升级核验通过"
验收 3：c3 exit=0 包含 "collab-wf 1.6.2"
验收 4：c4 exit=0 包含 "44cc3767d43e4af0"
指纹：44cc3767d43e4af0
超时：1800