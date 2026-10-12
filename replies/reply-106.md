[工具 bash_tool]
[工具 present_files]
【本轮结论】

本轮只做一件事：交付上一轮已经完成的产物。容器没有被重置，没有重新扫描源码，也没有重跑测试。三个文件交付前我又复核了一次：大小、sha256、文件头与上一轮公布的值逐一相同，两个 zip 都通过了完整性测试。

| 文件 | 字节 | sha256 | 用途 |
|---|---|---|---|
| build_wf_180.py | 81010 | 46d198c6292025bf9cb6a0dfad436475a305bf7fd740c78d2a3a266a7104eea9 | 构建器，由驱动器运行 |
| collab-wf-dev-v3.3.zip | 119895 | 07a8ae75df09735812274b3845f2ed102f5a5fd8dfe295a5c66dd603f00aaac1 | 本轮发布的项目版本，包含 3 个文件 |
| collab-wf-1.8.0.zip | 968734 | 5bd9e85b95071cfd21878434f722f1aae1abfc9a349c4d7391970fb71ea7582a | 我这边的构建产物，只用来比对，不安装 |

工具的安装以你本机构建器生成的 workflow/outbox/collab-wf-1.8.0.zip 为准，验收 1 会把两边的 sha 对上。

**落地顺序和要注意的点**

1. **先落地 v3.3，这时还是 1.7.6 在运行。** 1.7.6 的 goals.validate 会忽略 phase 和 history 两个字段，所以落地是安全的。落地后指纹应为 cbad75d7e6df496b。
2. **v3.3 生效后，DONE 的死结解开。** 目标换成 M2 的 11 项，不会再因为旧的 7/7 进入 DONE。这正是你回报里“01:26 旧目标 7/7 进入 DONE、T105-1 等 kick”的那个结。
3. **再构建并自动安装 1.8.0。** 自检应为 255/255，floor.py 没有改动。如果独立审查超时，就如实记为“未审查”，不能写成安装前通过。
4. **wf self-check --probes 这一轮不放进命令。** 1.7.6 的评审会把它判成会改状态的命令而停下。它由安装后的目标检查和执行端 3 来跑，预期 M2 进度为 6/11。

**你回报的夜间停滞：根因成立，是两处缺陷叠加**

- **旧 M1 目标进入了 DONE。** 已记为 W63，v3.3 解决。
- **待办超过 2 小时后，for 字段从 executor 改成了 user，唤醒程序又只投递 for=executor 的事项，结果谁也收不到，积压了 5.6 小时。** 这就是 P1-J 说的“决策归属和投递对象没有分开”，现在有了实证。它会作为 W70 写进下一个项目版本。

“服务健康不等于项目在推进”这一点我同意：需要一个看门狗检查“有未完成的事项、但没有在推进”（P1-K）。

**下一版 1.8.1 的优先级，按你的建议定**

1. 决策归属和投递对象分开：升级只改由谁决定，投递对象仍是执行端。
2. 有未完成的待办时，DONE 的处理方式。
3. 被中断的回复可以接着执行，并且有次数上限。
4. 回复的发现延迟（W66）。

目标和审查方面的安全底线都不降低。

**本机 pstack 0.19.0-codex.1 的研究，已收到**

这份研究和网上的官方 0.15.20 是两个不同来源，我分开记。下列机制会在 P0-C 里与 collab-wf 现有的账本和门禁增量融合：

- 账本里每条记录有明确的状态：pending、running、passed、blocked、not_applicable。
- gate 只给出 mechanical_pass，不等于整体通过。
- 接手会话的 5 个步骤，用 hash 检查点防止漂移。
- “命令退出码为 0”和“业务验收通过”分开。
- 一个范围只有一个写入者。

融合时不照搬 book 的宿主权限和模型，不另建一套竞争的账本，也不把检查器通过当成全部业务验收。本机安装是否完整还没有实证（INSTALL-RECEIPT 已陈旧），这一点照实保留。

【本机执行】
```
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-106/delivered/build_wf_180.py --out workflow/outbox
wf self-update --auto workflow/outbox/collab-wf-1.8.0.zip
wf version
wf self-check
wf fingerprint
wf goals
```

【本轮回报清单】
1. 构建通过，自检 255/255，floor.py 未改动；版本包的 sha256 与我这边一致。
2. 驱动器自动安装 1.8.0，升级核验通过；独立审查的结论以原文为准。
3. 工具版本为 1.8.0。
4. self-check 显示与清单完全一致。
5. 落地 v3.3 后，仓库指纹为 cbad75d7e6df496b。
6. wf goals 先显示“当前阶段 M2”，M1 列为历史阶段。

【需要用户决定】
无

【下一轮建议档位】
high

【驱动指令】
发布：collab-wf-dev-v3.3.zip
执行端 1：本轮验收通过后，迁移旧待办：wf task supersede T100-2 --reason "用户边界：Amy 在 collab-wf 完备前不恢复（第 104 轮）"；wf task supersede T100-1 --reason "由第 106 轮执行端 2 取代" --by-task T106-2。把两条命令的输出原文写进 wf task done
执行端 2：同步程序（P0-H 执行端侧）：接受 --repo；拿不到锁或跳过时返回非 0，且不输出提交号；缓存、锁、日志按项目、目标仓库、分支分开。改好后先做一次 wf sync --dry-run，再做一次真实推送，确认 local/sync/collab-wf-dev.json 里出现 pushed_commit。结果写进 wf task done
执行端 3：先运行 wf goals，核对 M2 目标是否已被自动批准。如果没有，按决策顺位审阅 goals.json 后，用 wf goals approve --by executor --reason 代批。再运行 wf self-check --probes，把它和 wf goals 的原文都写进 wf task done
之后：等执行端
验收 1：c1 exit=0 包含 "构建通过：collab-wf 1.8.0，自检 255/255" 包含 "5bd9e85b95071cfd21878434f722f1aae1abfc9a349c4d7391970fb71ea7582a"
验收 2：c2 exit=0 包含 "升级核验通过"
验收 3：c3 exit=0 包含 "collab-wf 1.8.0"
验收 4：c4 exit=0 包含 "完全一致"
验收 5：c5 exit=0 包含 "cbad75d7e6df496b"
验收 6：c6 exit=0 包含 "当前阶段 M2"
指纹：cbad75d7e6df496b
超时：1800