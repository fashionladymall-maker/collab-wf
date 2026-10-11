【回报 · 第 86 轮 · v2.6】档位：high

① 环境障碍与偏离
- 第84轮交付物下载通道已实测坐实为永久失效（非偶发）：对该对话的 download-file 端点，请求任意 path（含 /mnt/user-data/outputs/NO_SUCH_FILE_zzz.bin 这种不存在的路径）都返回同一份 18884 字节内容（sha256 f0d3cec4…，内容是同轮 pt5_regress.js 的源码文本）。换对话返回 404。所以这不是"下一轮只提交压缩包"能解决的——该对话的该端点已被冻结在某个固定产物上，任何文件都取不回。执行端按作者本轮文档给出的确定性重建路径在本机重建了全部 5 个交付物，并逐个与作者在回复里声明的大小/sha256 逐字节比对通过：collab-wf-dev-v2.7.zip 115768/2d7cd24d…、build_wf_168.py 44052/ca141fff…、pack_v27.py 5277/25878c42…、v27-PROJECT.md 65659/c2125949…、v27-CHANGELOG.md 10533/24da66f2…（重建方式为逐字转录作者自己写下的构建器与源文件，不是执行端自创）。通道下来的 4 个错内容文件已移到 workflow/evidence/turn-084/delivered/rejected/ 保留字节与哈希；核验通过的 5 个产物已就位在同目录。
- [RELEASE_MISSING] 指令要求落地的版本包不在本轮交付的文件中。不落地，回报给 Claude。 指令要求落地 collab-wf-dev-v2.7.zip，但本轮交付文件中没有

说明（备注）：
- 巡检（兜底自动化 01:41）· amy-local 阻塞项只读复核，两条新事实：① 420MB 源包已完整落在 mini2（/Users/admin/Downloads/Amy-Voice-v3.4.0-mac-Intel.zip = 420228482B，与声明字节数一致）；② /Applications/Amy Voice.app 于 01:40:30 安装完成（704M / v3.4.0），01:41:33 已启动运行，uvicorn:3900 与 shim:3901 均在跑、health=ok（9 请求 / 31.7s 音频 / uptime 3863s）。因此 handoff-blocked 信号里「安装包正从旧机拉取、已过半」的前提已过时（/tmp/amy_upload/amy.zip 仍是 348050976B 的半包，未被使用）；仍未解决的是「新 WorkBuddy 独立任务未建立」与「多项目通道隔离」。另：amy-local 的 wf pause 属用户专属护栏（unpause 以 by=user 记录），执行端不代执行，6X2 保留待用户表态；auto:false 已独立阻止自动发送，不会串线。本轮执行端只读，未改 amy-local 任何状态。
- 1.6.8 构建器本机独立验证通过（执行端做的，不是容器自述）：python3 workflow/evidence/turn-084/delivered/build_wf_168.py --out <临时目录> → 自检 231/231（新增 15 项）、63 个文件 + MANIFEST.json、成员集合核对一致、无 local/.git/__pycache__/*.pyc、VERSION=1.6.8、floor.py 未改动（sha256 仍为 9bbef54408123fa03cf567d081c68c0fbe48d412af93ad469f24024b66cc67a2，与 1.6.7 相同）。版本包 64 个成员、testzip=None、sha256 c5733bc3cd28f0ff91e8ac3a3cc68521805b6e6541fec6c7580194ea43cfa4cc，与作者容器里的 c5733bc3… 完全一致（作者预计可能因压缩库不同而不一致，实测一致，说明可复现）。构建器本身也是逐字重建的，其 sha256 ca141fff… 与作者声明一致。下一步只差由驱动器按白名单路径正式构建并 wf self-update --auto 安装。

② 关键数字与终端原文

1) 构建通过，自检 233/233，成员集合核对一致。 —— 未验收

2) 驱动器自动安装 1.6.8，升级核验通过。 —— 未验收

3) 工具版本为 1.6.8。 —— 未验收

4) self-check 完全一致。 —— 未验收

5) v2.7 落地后，仓库指纹为 56c0dd71ea3e8bd7（10 个文件）。 —— 未验收

6) `wf goals` 原文，应为 7/7。 —— 未验收

③ 需要决策的点
1. 工具缺陷（请作者修，很小）：wf unpause 没有 _by_args，无法由执行端代决，只能 --code + --said（用户原话）。后果：执行端自己 wf pause 造成的暂停，无法由执行端自己解除，会一直以 for=user 的待办挂在 inbox 里，直接违反用户契约 §8（除密钥/凭据外一律执行端决策，不把动作退回用户）。实例：amy-local|6X2「自动化已暂停」。修法建议：给 unpause 加 _by_args，走 _decide_as 而不是 _record_user（否则会把执行端代决记成用户原话，污染审计）。执行端本轮不手改 state.json、也不伪造用户原话，保持现状等作者修；amy-local 已用 wf.json 的 auto:false 静默，不影响其他项目，也不影响用户。另：wf pause 产生的待办一律 for=user，建议改成按发起方决定 for（执行端发起的就 for=executor）。

④ 需要注意（Attention）
- 自动处置 NET → retry：响应头: {'Access-Control-Expose-Headers': 'X-Bard-Mode', 'Alt-Svc': 'h3=":443"; ma=2592000', 'Cache-Control': 'no-cache, no-transform', 'Cf-Cache-Status': 'DYNAMIC', 'Cf-Ray': 'a48fa9647d4ff7bf-LAX', 'Content-Language': '', 'Content-Type': 'text/event-stream', 'Date': 'Sun, 11 Oct 2026 17:42:30 GMT', '
- 自动处置 SEND_UNKNOWN_WAIT → retry：发送意图 2026-10-11T17:42:26+00:00 结果不明；轮询返回 none
- 自动处置 RELEASE_MISSING → report：指令要求落地 collab-wf-dev-v2.7.zip，但本轮交付文件中没有
- 当前对话已累计约 45886 字符、5 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

（清单第 1, 2, 3, 4, 5, 6 项本轮没有执行到验收，原因见 ①）

附：仓库指纹 b848fba7f3776a69（v2.6，7 个文件）
