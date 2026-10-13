【回报 · 第 117 轮 · v3.7】档位：high

① 环境障碍与偏离
- 唤醒投递找不到可用会话：任务码 signal|review:collab-wf.tool.collab-wf-1.8.7 已等待，用户可能没开窗口；按 tick 节奏继续重试（不消耗重试次数，会话出现即补投）

说明（备注）：
- 执行端待办 T115-1 已完成（把本轮结论里那段写给用户的话原样转给用户；转发的原话写进 wf task done）：已把本轮结论里写给用户的话原样转给用户。转发的原话（Claude第115轮原文）：『项目在正常推进，M2 的 11 项已经完成 7 项。之前几次停下，都是安全检查发现『回复和文件对不上』，为了不装错东西主动停的；这一轮交付的新版本会从源头把回复和文件绑在一起。你现在不需要做任何事，全部完成后才会请你最终验收。』已在会话中告知用户。同时按Claude要求附带纠正：此前T113-3转的『这一轮已经修复，你什么都不用做』不是Claude写的且说早了——回复和文件错配要等1.8.6装好才算修复。
- 执行端待办 T115-2 已完成（只读核对第 112 轮驱动器在 01:17:15 的重发，不改状态：列出 01:15–01:18 之间 wf audit 的事件原文（重点看 LEAF_REFU）：第112轮驱动器01:17:15重发核对（只读，未改状态）。【重要纠正】T114-2中我写的『第3次发送：01:24:41 executor手工发送turn-112.md（2345字节）』不准确，现予纠正：bridge.log中2026-10-13T01:20–01:29之间只有download/poll、没有任何send记录；112轮实际只有2次send，均为1381字符、均由驱动器发出。我在T114-2中据此刻画的三次发送（含Claude v3.7结论70采信的『执行端手工01:24』）有误，特此纠正。①01:15–01:18事件原文：01:13:15 auto SEND_UNKNOWN_WAIT retry attempt=1 detail『发送意图 2026-10-13T01:05:26+00:00 结果不明；轮询返回 none』facts含send_one；01:13:15 tick note『[SEND_UNKNOWN_WAIT] 发送结果不明…保留发送意图，约一小时内反复轮询确认，绝不自动重发；仍不明再阻塞，由人用 wf resend 决定。120 秒后重试（第 1 次）』；01:13:50 executor transition SEND→WAIT_REPLY；01:13:58 executor WAIT_REPLY→INTAKE；01:14:09 wf INTAKE→REVIEW reason=固定块齐全；01:14:25 wf REVIEW→COMPOSE reason=生成回报草稿；01:14:25 driver report_draft file=workflow/outbox/turn-112.md effort=high；01:14:25 driver COMPOSE→SEND reason=消息已通过发送前检查；01:16:02 driver SEND→WAIT_REPLY。②适配器poll返回status：bridge.log 01:18:24 poll『写出页面快照：22 条消息』→reply index=21、交付2个文件、另有1套交付；01:21:28/01:21:34 download turn=112（v3.5.zip、build_wf_184.py）done；01:22:06 poll 22条；01:22:11/01:22
- 执行端待办 T115-3 已完成（1.8.6 装好、驱动器跑完目标检查后，运行 wf goals（预期 7/11）、wf self-check --probes（预期 20/20），并写下独立审）：T115-3的前提已不成立，如实记录后关闭：1.8.6版本号已被Claude作废（第116轮回复原文：『1.8.6这个版本号也被两条回复各用了一次，内容不同，两个都没有安装，这个号作废』）。原因是我方第115轮取到的回复是分叉的另一条（交付的build_wf_186.py 25348字节/sha256 ed9ae8bd做回执码，而Claude实际交付的是build_wf_186.py 23854字节/sha256 67fda11f做台账检查器，且那一轮没有项目版本）。因此115轮6条命令未执行、6项验收全fail、1.8.6从未安装。现在Claude已交付1.8.7（自检271/271、探针22/22、floor.py未改、版本包sha256 67e58c4369ab5d07e25913117e2c03e33ddc6bc0feb12f301caedbb696d26b61，基于已安装的1.8.5）。对应1.8.7的目标检查与汇报将在T116-3执行（Claude预期goals 8/11、M2-D1通过；探针22/22；新增wf ledger）。本条不伪造『1.8.6已装好』的执行结果。附带：确认了『下一条消息末尾带回执码』这一需求由1.8.7实现（Claude原文：驱动器发出的每条消息末尾附一个随机码，同一轮重发时沿用同一个；回复里要写一行『回执：<码>』），1.8.5会忽略驱动指令里的『交付』行，装上1.8.7之后才开始生效。
- 执行端待办 T116-1 已完成（只读核对当前的项目基线，不改状态：wf fingerprint --files 的原文；项目 git log -4 --stat 的原文；v3.7 是由谁、在什）：项目基线只读核对（未改状态）：①wf fingerprint --files原文：『仓库指纹 47b9f533d91ecbff（10 个文件）』+10个文件哈希（.gitignore 61735e84、CHANGELOG.md 8a7c69cb、ISOLATION.md c43a9ed1、PROJECT.md 09677d2e、VISION.md ec4636b5、papertrade/make_r5.py 708e7dd4、papertrade/pt5_regress.js f0d3cec4、workflow/goals.json a838a75f、workflow/m1_check.py f9b36ffb、workflow/policy.json f40bf52e）。②git log -4 --stat原文：a41ae6c release v3.7（CHANGELOG.md +4、PROJECT.md +19/-7）；0f4fd8f release v3.6（CHANGELOG.md +4、PROJECT.md +20/-8）；9f99a87 release v3.5（CHANGELOG.md +4、PROJECT.md +24/-10）；21684ee release v3.4（CHANGELOG.md +5、PROJECT.md +38/-14、workflow/goals.json +5/-1）。③v3.7由谁/何时/什么命令落地：wf audit原文『10-13 02:48 第115轮 [executor] 落地：version=v3.7 commit=a41ae6c changed=2』，同期有『[driver] 驱动：exit=0：评审通过，落地版本』；即由执行端（本会话）在驱动器之外用 wf apply workflow/evidence/turn-115/delivered/collab-wf-dev-v3.7.zip 落地，commit a41ae6c。④本机除v3.7那两个文件外有无别的改动：git status --short 无输出（干净），仅a41ae6c改了CHANGELOG.md与PROJECT.md两个文件，无其他改动。⑤指纹差异说明：Claude在镜像里用『v3.6的项目文件+v3.7的两个文件』重算得2b3c193059e61
- 执行端待办 T116-3 已完成（按决策顺位决定：1.8.8 是否提供并开启“审查不在场就不安装”的开关（我建议开启）。1.8.7 装好、驱动器跑完目标检查后，运行 wf goals（预期 8/）：1.8.7装好后的三项原文：①wf goals原文：『当前阶段 M2 专业基线（第一批）：交接、动态目标与防循环（取代 M1）』『目标进度 8 / 11』，其中M2-D1『经验闭环：台账缺项不得标已解决』**已通过**（Claude预期8/11达成）；仍缺M2-C1（默认工程门禁基线与角色准入退出：输出不含『探针 门禁基线：通过』）、M2-G1（共享资源仲裁S4：输出不含『探针 共享仲裁：通过』）；M2-U1前置未满足：M2-C1、M2-G1（未到确认的时候，不通知）。②wf self-check --probes原文：『探针结果：22 / 22 通过』，新增4项全部通过——经验闭环（M2-D1）、回复与交付同源、回执核对、交付核对（符合Claude预期22/22）。③wf ledger原文（M2-D1的判据命令，只读）：『失败模式台账 84 条：修复已交付 12、已回归 1、已缓解 10、已规避 19、已解决 36、待处理 4、待开发 1、待执行端 1』『不符合口径的 30 条』，逐条列出如W01『标了『已解决』，但缺少：回归证据（自检、探针或回归测试）』（W01–W10及后续多条同类，均缺回归证据）。④独立审查结论：1.8.7装好了，安装时审查情况待补——self-update输出需从c02.out取证；待取证后按『安装前/超时未审查/迟到』如实注明，结论据diff给出。注：本条执行时116轮仍在VERIFY，驱动器跑完目标检查后M2-D1已标记通过（见①）。
- 执行端待办 T116-2 已完成（只读核对对话分叉（W83、W84），不改状态：从第 112 轮起，每一条发出的消息分别用了哪个父消息，每条消息下面各有几条助手回复（只写数量和 uuid），驱动）：对话分叉（W83/W84）只读核对完成，未改状态。【发送与取回链——自第112轮起】①112轮：2次send（01:15:29、01:17:15），均由驱动器发出，均未显式指定父消息（适配器send()调用cb.send_message时parent=a.get('parent') or None，即None→由服务端接到当时会话叶子）。两次send的间隔106秒，第一次的accepted在01:15:59、第二次在01:17:30。对应产生的助手回复有2条以上：poll在01:18:34/01:21:34/01:22:15三次都取到reply index=21（同一条）且都标注『另有1套交付』——即同一轮下并存至少2条回复，交付内容却不同（我方intake取到的那条交付v3.5+build_wf_184.py，Claude实际写的那条交付的是别的，见116轮回复）。②113轮：2次send（01:56:23、01:56:25，相隔仅2秒，均为6492字符，CLI连续重试造成），同样未指定父消息。③114轮及之后：每轮1次send。④驱动器每轮取的是哪一条：由poll的reply_parent与send_ref逐字符比对决定——适配器用本轮ref里的parent_uuid定位『我们的消息』，再看最后一条回复的parent_message_uuid回溯；比对不上就『不收下、继续轮询』（TRANSPORT.md 1.5.1、第30轮真实发生过）。分叉时这个比对能发现『取到旧回复』，但无法分辨同一轮下并存的**两条新回复**中哪条才是本轮消息的子回复——这正是113/115轮取错的机制。【适配器机制】发送时怎么选父消息：send()的parent参数默认None，不显式选择，由服务端接到当前会话叶子；取回复时怎么选：按ref的parent_uuid回溯+index规则兜底，取『最后一条』回复，并用reply_parent与send_ref比对校验。【为什么会在同一轮下分叉】send的parent=None时，第二轮发送会被服务端接在第一轮产生的回复之后（成为其子消息），于是同一轮次下出现并列的两条回复链；各自的子回复都『像』是本轮的回复。【改造计划（W84，Claude已指明，执行端范围）】①每条消息只发一次：发送前先确认没有在途发送（避免120秒定时重试与正常发送叠加，

② 关键数字与终端原文

1) 构建通过，自检 271/271，floor.py 未改动；版本包 sha256 与我这边一致。 —— 通过
```
$ /Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-116/delivered/build_wf_187.py --out workflow/outbox
基线核对：/Users/admin/.workbuddy-ai/tools/collab-wf 为 1.8.5，64 个文件与公开版本包逐字节一致
改动：19 处、新文件 1 个，编译通过；floor.py 未改动
正在运行完整自检……
自检：271 / 271 通过
成员集合：65 个文件 + MANIFEST.json，与清单一致
构建通过：collab-wf 1.8.7，自检 271/271
版本包：workflow/outbox/collab-wf-1.8.7.zip，1022055 字节，sha256 67e58c4369ab5d07e25913117e2c03e33ddc6bc0feb12f301caedbb696d26b61
exit=0  耗时 154.9s
```

2) 1.8.7 自动安装，升级核验通过；附上独立审查结论原文，写明它是不是在安装前给出的。 —— 通过
```
$ wf self-update --auto workflow/outbox/collab-wf-1.8.7.zip
工具已升级：1.8.5 → 1.8.7（af643f1），自检已通过
看板已重启以加载新版本
[stderr]
开始等独立审查（上限 300 秒；到时没有结论按“未审查”继续安装）
仍在等独立审查：已 30 秒 / 上限 300 秒
仍在等独立审查：已 60 秒 / 上限 300 秒
仍在等独立审查：已 90 秒 / 上限 300 秒
仍在等独立审查：已 120 秒 / 上限 300 秒
仍在等独立审查：已 150 秒 / 上限 300 秒
仍在等独立审查：已 180 秒 / 上限 300 秒
仍在等独立审查：已 210 秒 / 上限 300 秒
仍在等独立审查：已 241 秒 / 上限 300 秒
本次未审查：{"status": "unreviewed", "reason": "review deadline elapsed"}；照常安装，可事后按 /Users/admin/.workbuddy-ai/tools/collab-wf/local/reviews/collab-wf-1.8.7.diff 补审
正在运行新版本 1.8.7 的完整自检
升级核验通过：磁盘版本 1.8.7，65 个文件与版本包一致，看板已加载 1.8.7
exit=0  耗时 456.8s
```

3) 工具版本为 1.8.7。 —— 通过
```
$ wf version
collab-wf 1.8.7（wf-v1.8.7）  安装位置：/Users/admin/.workbuddy-ai/tools/collab-wf
可回滚的版本：wf-v1.8.7、wf-v1.8.5、wf-v1.8.4、wf-v1.8.3、wf-v1.8.2、wf-v1.7.6、wf-v1.7.5、wf-v1.7.4、wf-v1.7.3、wf-v1.7.2、wf-v1.7.1、wf-v1.7.0、wf-v1.6.9、wf-v1.6.8、wf-v1.6.7、wf-v1.6.6、wf-v1.6.5、wf-v1.6.4、wf-v1.6.3、wf-v1.6.2、wf-v1.6.1、wf-v1.6.0、wf-v1.5.9、wf-v1.5.8、wf-v1.5.7、wf-v1.5.6、wf-v1.5.5、wf-v1.5.4、wf-v1.5.3、wf-v1.5.2、wf-v1.5.1、wf-v1.5.0、wf-v1.4.3、wf-v1.4.2、wf-v1.4.1、wf-v1.4.0、wf-v1.3.1、wf-v1.3.0、wf-v1.2.2、wf-v1.2.1、wf-v1.1.0、wf-v1.0.3、wf-v1.0.2、wf-v1.0.1、wf-v1.0.0
当前项目 collab-wf-dev 固定版本：1.7
exit=0  耗时 0.6s
```

4) self-check 与清单完全一致。 —— 通过
```
$ wf self-check
对照 1.8.7 的文件清单：完全一致
exit=0  耗时 0.5s
```

5) 仓库指纹为 47b9f533d91ecbff。 —— 通过
```
$ wf fingerprint
仓库指纹 47b9f533d91ecbff（10 个文件）
exit=0  耗时 0.4s
```

6) wf goals 显示“当前阶段 M2”。 —— 通过
```
$ wf goals
当前阶段 M2 专业基线（第一批）：交接、动态目标与防循环（取代 M1）
目标进度 7 / 11
  ✓ M2-B1 [可执行] 阶段目标：看板与 STATUS 先显示当前阶段，旧阶段只作历史：通过
  ✓ M2-B2 [可执行] 待办可取消、可被取代，完成必须写结果：通过
  ✓ M2-E1 [可执行] NO_REF 不再循环：通过
  ✓ M2-F1 [可执行] 回复完整性：识别工具上限并有界续接：通过
  ✓ M2-H1 [可执行] 同步凭证：没有提交号不算推送：通过
  ✓ M2-A1 [可执行] 交接短消息钉在提交上、只链已推送的文件：通过
  ✓ M2-F2 [可执行] 回复发现延迟有界：通过
  ○ M2-C1 [可执行] 默认工程门禁基线与角色准入退出：输出不含“探针 门禁基线：通过”
  ○ M2-D1 [可执行] 经验闭环：台账缺项不得标已解决：输出不含“探针 经验闭环：通过”
  ○ M2-G1 [可执行] 共享资源仲裁 S4：输出不含“探针 共享仲裁：通过”
  ○ M2-U1 [人工] 用户验收 M2：前置未满足：M2-C1、M2-D1、M2-G1（未到确认的时候，不通知）
  历史阶段 M1 无人值守稳定运行：已完成（2026-10-12）；7/7：G1 工具文件与清单一致、G2 体检健康、G3a–d 历史最长连续 3/6/8/10 轮无人工动作、G4 驱动器自建自装；第 71 轮达成，第 98 轮由用户授权执行端代为验收；原判据见 v3.2 的 goals.json（git 历史）
exit=0  耗时 0.4s
```

③ 需要决策的点
无。

④ 需要注意（Attention）
- 自动处置 NET → retry：响应头: {'Access-Control-Expose-Headers': 'X-Bard-Mode', 'Alt-Svc': 'h3=":443"; ma=2592000', 'Cache-Control': 'no-cache, no-transform', 'Cf-Cache-Status': 'DYNAMIC', 'Cf-Ray': 'a49b0ad878e680ca-MIA', 'Content-Language': '', 'Content-Type': 'text/event-stream', 'Date': 'Tue, 13 Oct 2026 02:51:25 GMT', '
- 当前对话已累计约 111397 字符、13 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

附：仓库指纹 47b9f533d91ecbff（v3.7，10 个文件）

回执码：9c7492f3（请在驱动指令里写一行“回执：9c7492f3”，驱动器据此确认回复对应本条消息）
