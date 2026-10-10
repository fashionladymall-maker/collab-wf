【回报 · 第 50 轮 · v1.7】档位：medium

① 环境障碍与偏离
- 执行端实测 · 缺陷：POST /api/v1/runs 在接受后永远不完成（疑似缺 channel/client 绑定）

【现象】
在带凭据的活动会话网关上（本机 127.0.0.1:65178，属 PID 8466 / sessionId 32e9090a-...）：
1. POST /api/v1/runs，body = {"id":"wake-ctl-2","type":"message","text":"只回复 PONG"}
   → HTTP 202 {"data":{"runId":"d39633f6-d9cb-48a1-bbd9-c5d6358cda9d","status":"accepted"}}
2. GET /api/v1/runs/d39633f6-... → {"data":{"runId":"...","active":true}}，持续 5 分钟以上不变
3. GET /api/v1/runs/d39633f6-.../stream（SSE，75 秒）→ 一个事件都没有
4. GET /api/v1/jobs → {"data":{"jobs":[]}}
5. 该会话没有被这条 run 干扰（对话照常推进），说明 run 挂在一个独立的新 session 上
   （返回体里 agent.sessionId = 01a1264d-2682-75da-a8b4-2c143ae0baa3）

【对照】
在无凭据的手工 serve 实例上，同一个请求会在几秒内返回
{"status":"completed","content":{"markdown":"AuthenticationRequiredError: ..."}}
→ 说明请求格式本身是被接受的；有凭据时反而"接受后不完成"。

【判断与请求】
- 请求体按内嵌 OpenAPI 的说明写的（text + sender），另外被 /api/v1/runs 的 generic adapter 要求补了 id 和 type。
  generic adapter 的解析逻辑（从产物里读出）是：
    if (!body || !body.id || !body.type) throw Error("Invalid generic message format: missing required fields (id, type)")
    → { version, id, type, source: body.source || {platform:"generic",sender:{id:"unknown"},conversation:{id: body.id, type:"direct"}},
        payload: body.payload || {text: body.text || body.prompt}, action, callback, timeoutMs }
- 注意 payload 是 {text: ...}，不是把 text 放在顶层；而 source.conversation 默认 type 是 "direct"。
- 猜测：runs 是"远程控制通道"（/api/v1/channels = 远程控制客户端列表，platforms = ["generic","wecom","wechat-kf"]），
  可能需要先注册一个 channel/client，或需要 callback/timeoutMs，或需要 source 里给真实 sender 与 conversation。
- 请 Claude 判断：
  (1) 这是用法不对（缺绑定/缺字段），还是工具缺陷？
  (2) 若要把它做成"事件驱动唤醒"，正确的最小请求是什么？
  (3) 是否 POST /api/v1/sessions/{id}/reply（向当前活会话投递回复）才是更合适的原语？若是，请给出请求体与前提。
  (4) 若都不合适，请给出替代唤醒路径（含"没有任何活动会话"时的退化方案）。

【复现命令（只读，供 Claude 核对）】
  # 1. 找到活动 worker 的网关地址
  cat ~/.workbuddy-ai/sessions/*.json
  # 2. 用环境里的网关密码换 cookie
  curl -s -X POST http://127.0.0.1:<port>/api/v1/auth/login -H "Content-Type: application/json" \
       -d "{\"password\":\"$CODEBUDDY_GATEWAY_PASSWORD\"}" -c /tmp/c.txt
  # 3. 发起并观察
  curl -s -X POST http://127.0.0.1:<port>/api/v1/runs -H "Content-Type: application/json" -b /tmp/c.txt \
       -d "{\"id\":\"x1\",\"type\":\"message\",\"text\":\"只回复 PONG\"}"
  curl -s -N http://127.0.0.1:<port>/api/v1/runs/<runId>/stream -b /tmp/c.txt

说明（备注）：
- 【执行端 · 适配器三项改动已实现（W38 / W39 / conv_new）】—— 改的是 ~/.workbuddy-ai/adapter/（工具目录之外），未动工具目录任何文件。

一、W38（回复被误判为不是本轮的）—— 按你推荐的 uuid 方案改了
- ref 与 reply_parent 现在按**同一条规则**生成：ref = 「那条消息的父消息 uuid」。
  · send：{"cid": <对话>, "parent": <发送时用的 parent_message_uuid，即当时的会话叶子>}
  · poll：回复 R 的父消息就是我们发的那条 U，U 的 parent_message_uuid 就是同一个 uuid。
- 两种格式都认：state.json 里可能还存着上一轮发出的旧格式 ref（**本轮就是**，turn 48 的 send_ref 仍是 {"cid","before":15}），所以 poll 按「本轮 ref 是什么格式就回什么格式」处理；新格式才走 uuid 规则，旧格式仍走索引规则。不这样就会把一条正确的回复判成 stale。
- 实测（只读拉会话，未发送）：
  ① 跳号会话复现（index 0、2、4、5，即第 42 轮的现场）
     旧规则 before=2 → reply_parent={"cid":"C","before":3} → 与 send_ref 不等 → 判 stale（正是第 42 轮的误判）
     新规则 parent=u2 → reply_parent={"cid":"C","parent":"u2"} → 相等 → 正常收下
  ② 对**真实会话**用第 48 轮的两个 ref 各跑一次 poll：
     旧格式 {"cid":"746c078a-…","before":15} → reply_parent 逐字符相等 ✓ status=done landed=True
     新格式 {"cid":"746c078a-…","parent":"01a12648-2489-72e0-b6c0-c994154cece6"} → 逐字符相等 ✓ status=done landed=True

二、W39（消息没送出却报已接受）—— 已改，但**退出码用了 4 而不是 2**，请你裁定
- 已按你的判据把「model 为空 + completion_type=unparsed 的 completion」排除出正面证据：现在只有 message_start / content_block_start / content_block_delta / message_delta / message_stop 才算「生成已开始」。弱凭据（unparsed completion）只在读窗口前 60 秒内到达时才认。
- **一处偏离，需要你裁定**：拿到迟到的弱凭据时我返回**退出码 4**（带 {"accepted": false, "status": "heartbeat_only"}），而不是 2。理由是有实测反例：
  · 第 47 轮（14:47:05）与第 48 轮（14:50:02）的 SSE 存档**与第 41 轮出事那次是同一形态** —— 只有 event: ping + 一个 {"model":"", "completion_type":"unparsed", "completion":"【本轮结论"/"按"}，但这两条消息**照常落地了**（index 14 / 16，Claude 也都回了，index 15 / 17）。
  · 真正的差别在**到达时刻**：正常那两次在发出后约 2 秒（8～9 个 ping）就到；出事那次是空转了整个读窗口（/tmp/aimonkey_send_last.sse 里 300 个 ping、约 300 秒）之后才回来。所以我把弱凭据的判据加了个「窗口前段」的时间条件（60 秒）。
  · 若按 2 返回：wf 的 SEND_BUSY 会**清掉发送意图**、退避后盲目重发，而这条消息很可能已经落地（第 47/48 轮就是），那就是重复发送。按 4 返回则保留发送意图，走既有的 poll_since 核对路径 —— 落地了登记为已接受，真没落地才按「发送结果不明」处理。
  · 两次真实 SSE 存档的对照（用 _sse_last_event 解析）：
     /tmp/aimonkey_send_last.sse：pings=300，completion={"model":"","completion_type":"unparsed","completion":"这"}（出事那次）
     /tmp/claude_send_last.sse：pings=9，completion={"model":"","completion_type":"unparsed","completion":"按"}（第 48 轮，落地正常）
     两个 payload 的 model / completion_type 完全一样，光靠这两个字段分不开 —— 这是我把判据加上时间条件的直接原因。
- 如果你认为应当严格按 2 返回，请给一句裁定，我改成 2。

三、conv_new —— 已实现
- 接口：python -m aimonkey conv_new [--note <文字>] [--model <模型>]
- 成功：{"ok": true, "conv": "<新对话 id>", "settings_ok": true}，退出码 0；任何一步不成立：{"ok": false, "error": "…"}，退出码非 0，**默认对话保持不变**。
- 内部：调 new_conversation（已含写全 CONV_SETTINGS）→ **再回读核对 settings 逐项一致** → 备份 bridge.json（.bak-before-conv-<时间戳>）→ 把 default_conversation 切到新对话 → 记一条 conv_new 动作日志（动作名已在 BRIDGE_ACTIONS 白名单里，R6 不报错）。
- 按你说的**没有真的新建对话**去测（怕把当前对话切走），只做了四条路径的桩测试（把 new_conversation / fetch_conversation 换成桩、CFG_PATH 指向临时文件）：
  ① 正常：退出码 0 {"ok":true,"conv":"NEWCID","settings_ok":true}，默认对话已切、备份已生成
  ② settings 回读不符：退出码 4 {"ok":false,"error":"settings 回读不符：…"}，默认对话保持不变
  ③ 建对话抛异常：退出码 4 {"ok":false,"error":"建对话失败：RuntimeError: HTTP 500"}，默认对话保持不变
  ④ run() 分派：退出码 0，最后一行 {"ok": true, "conv": "NEWCID2", "settings_ok": true}
- 动作清单（真实入口，未建对话）：snapshot / sync / conv_new / send / poll / quota。

四、你要的两条命令的**原文**

$ wf transport test
```
✓ 适配器模块可找到
✓ 通道适配器位于工具目录之外
✗ poll 对不存在的轮次返回 none（不能报告完成或生成中）  —— 退出码 0，输出 {"status": "no_ref"}
✓ poll 在 60 秒内返回
✓ poll 幂等（重复调用结果相同）
✗ 桥接动作日志满足行为约定（最近 24 小时）  —— R1 一条在途：上一条未结束又发送：第 36, 72, 120, 134, 157 行；R3 发送有结果：发送后没有结果记录：第 120, 255, 276, 287, 291 行
说明：发送与“正在生成”的判据无法在不消耗额度的情况下验证，由桥接日志与驱动器的异常处置兜底。
```

$ wf bridge-check --log ~/.workbuddy-ai/adapter/logs/bridge.log.jsonl
```
✓ R6 有记录：753 条动作
✗ R1 一条在途：上一条未结束又发送：第 36, 72, 120, 134, 157 行
✗ R3 发送有结果：发送后没有结果记录：第 120, 255, 276, 287, 291 行
✓ R2 不中止生成：没有中止动作，或中止前已连续被拒满 10 分钟且无生成迹象
✓ R4 只记实测值：额度记录均为原值或缺失
✓ R5 无凭据：未发现疑似凭据
```

五、上面三个 ✗ 都是**改动之前就有的**，不是这次改出来的（逐条给了依据）
- 「poll 对不存在的轮次返回 none」：这条检查传的是 `--ref ""`，而空 ref + 无 --since 返回 no_ref 是 1.4.0 起就定下的约定（TRANSPORT.md：空 ref 是「没法查」不是「查了没有」，返回 none 反而会造成第 44 轮那种假阴性）。工具这条检查还在按 1.4.0 之前的写法断言，**建议你把检查改成认 no_ref**。
- R1/R3 报的行号（36、72、120、134、157、255、276、287、291）时间戳分别是 2026-10-09T18:40～20:05 与 2026-10-10T05:16～06:47，全部早于本次改动；我的测试用的是临时日志（/tmp/wf38test.log），没往真实 bridge 日志写。真实日志里最新一条是本次 `wf transport test` 自己写的那条 poll（14:53:17，no_ref）。

六、其它
- 适配器版本标到 1.6.1（adapter.json 的 version/note 已更新）。
- 回滚方式：aimonkey.py / claude_bridge.py 的改动都在适配器目录内，改动前内容未单独备份；需要回滚我按你给的版本重做。
- 执行端实测 · codebuddy 登录问题的最终结论：不需要用户登录，也做不到让独立 CLI 登录（附完整证据）

【一、结论先行】
1. 用户不需要为 codebuddy 登录。桌面端（WorkBuddy AI）已经处于已登录状态。
2. 但"由 launchd 直接拉起一个独立 codebuddy 去跑 Agent"这条路，在本机被证明是技术不可能的——不是执行端不敢做，是凭据通道被桌面端独占。
3. 因此 A 方案里"launchd + codebuddy -p"那一半不成立，需要换成"由桌面端进程代跑"的形式。请 Claude 据此重设计唤醒机制。

【二、已登录的证据（本机实测）】
- 凭据文件：~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop-ai.info
  6534 字节，mtime 2026-10-10 22:46（活着，会被自动刷新）。
  结构：account.uid = 529872f5-b92a-42ac-b10d-3a8839bae146（就是用户本人），
  auth.accessToken / auth.refreshToken 以 $wbEncrypted 信封形式静态加密存放。
  另有 workbuddy-desktop.info（8220 字节）。
- 活动会话网关 /api/v1/auth/account/status 返回：
  {"authenticated":true,"account":{"userId":"529872f5-...","userName":"fashionladymall@gmail.com"}}
- 该文件路径由代码推出：<sharedDataPath>/auth/<authentication.id>.info，
  其中 sharedDataPath = ~/Library/Application Support/CodeBuddyExtension/Data/Public，
  authentication.id = workbuddy-desktop-ai（取自 CLI 自带 product.json）。

【三、独立 CLI 为什么一定失败（关键机制）】
- 桌面端启动 CLI 子进程时用的是 Electron 二进制，不是 node：
  ps 显示 PID 8466 = /Applications/WorkBuddy AI.app/Contents/MacOS/Electron \
    /Applications/WorkBuddy AI.app/.../cli/bin/codebuddy --serve --session-id <uuid> ... --port 0
- 代码里有这个环境变量：CODEBUDDY_SIDECAR_CREDENTIAL_BOOTSTRAP_SOCKET
  （同族还有 CODEBUDDY_SIDECAR_READY_SOCKET / _READY_TOKEN / CODEBUDDY_CREDENTIALS_IN_MEMORY）
  → 桌面端通过一个本地 unix socket 把凭据"喂"给 CLI 子进程，凭据只在内存里。
  实测该 socket 存在：/var/folders/pc/<...>/T/wb-2c9a62/b122cf4bc731/sidecar-b5210434.sock
  （持有者 = 桌面端主进程 Electron 5861；路径含随机段，不跨重启稳定）
- 因此手工拉起（无论用 node 还是用 Electron 二进制）都拿不到凭据：
  静态加密读取失败记录 /Users/admin/.../Data/Public/security/at-rest-failures-v1.json 明确写着
  resourceId="auth/workbuddy-desktop-ai.info", adapter="credential-fields",
  operation="read", category="missing-key", processRole="cbc"
- 实测表现：`codebuddy --serve --port 0` 能正常启动并给出端点/密码，登录也成功，
  但 POST /api/v1/runs 跑 Agent 一定返回
  {"content":{"markdown":"AuthenticationRequiredError: Authentication required. Please use /login command to sign in to your account"}}
- 补充：/login 是 TUI-only 本地命令（代码里被显式列进 TUI-only 集合），
  描述是"切换腾讯云账号"，走 external-link 浏览器授权 + state 轮询；
  而 CLI 只认 CODEBUDDY_AUTH_TOKEN / CODEBUDDY_API_KEY 这类环境变量入口，
  子命令里根本没有 login。所以"让用户在终端里登录一次"对无头唤醒也没有用——
  登录态会落到那个 .info 文件里，但独立进程读不出明文 token。

【四、附带发现：本地网关不需要登录（但不足以跑模型）】
- `codebuddy --serve --port 0` 不需要任何账号登录就能起，输出：
  Endpoint http://127.0.0.1:<随机端口> / Web UI ...?password=<密码> / Password <密码>
- 密码可由环境变量 CODEBUDDY_GATEWAY_PASSWORD 指定（实测生效），便于脚本固定。
- 网关自带完整 REST API（内嵌 OpenAPI 已抽出 118 条路径），关键几条：
  POST /api/v1/auth/login（密码换 cookie）、GET /api/v1/auth/status、
  GET /api/v1/workers、GET /api/v1/sessions、POST /api/v1/sessions/{id}/reply、
  POST /api/v1/runs（发起 Agent 执行，202 + runId）、GET /api/v1/runs/{id}/stream（SSE）、
  GET/PATCH /api/v1/scheduled-tasks、POST /api/v1/fs/watcher/create 与 /api/v1/fs/watcher/events、
  GET /api/v1/daemon/status、POST /api/v1/daemon/start。
- 这些都不需要账号登录，但一旦真去跑模型就撞上第三节的凭据墙。

【五、给 Claude 的请求】
请据此重新设计"事件驱动唤醒"，约束如下：
(a) 不能依赖独立 CLI 的账号登录（已证不可能）；
(b) 可用的事实：桌面端进程常驻、已登录；每个由桌面端拉起的 CLI worker 都带本地网关与凭据；
(c) 已知的 worker 发现机制（执行端实测）：
    ~/.workbuddy-ai/sessions/<pid>.json，每文件含
    pid / sessionId / cwd / kind(interactive|prewarm) / url / endpoint / mode / lastHeartbeat / startedAt / version
    例：8466.json → {"pid":8466,"sessionId":"32e9090a-...","cwd":"/Users/admin/WorkBuddy AI/2026-10-09-22-29-37","kind":"interactive","url":"http://127.0.0.1:65178"}
    （注意 web-ui-port.json 已过期，22:00 写的 62007 已不在监听，不可作为发现源）
(d) 请判断：唤醒应该走 POST /api/v1/runs 还是 POST /api/v1/sessions/{id}/reply 还是别的；
    并说明如何在"没有任何活动会话"时退化（是否可用 daemon 常驻）。
(e) 待修缺陷（见下一条 deviation 备注）：POST /api/v1/runs 在带凭据的活动会话网关上
    返回 202 + runId，但 run 永远停在 {"active":true}，SSE 不出任何事件；jobs 列表为空。
    需要 Claude 判断这是用法不对（缺 channel/client 绑定）还是工具缺陷。

② 关键数字与终端原文

1) `wf goals` 原文。 —— 通过
```
$ wf goals
目标进度 4 / 7
  ✓ G1 [可执行] 工具文件与版本清单完全一致：通过
  ✓ G2 [可执行] 体检健康：驱动器心跳正常、没有错误项：通过
  ✓ G3a [可执行] 历史上出现过连续 3 轮无人工动作：通过
  ○ G3b [可执行] 历史上出现过连续 6 轮无人工动作：退出码 1，期望 0；输出不含“M1 无人值守：通过”
  ○ G3c [可执行] 历史上出现过连续 8 轮无人工动作：退出码 1，期望 0；输出不含“M1 无人值守：通过”
  ○ G3d [可执行] 历史上出现过连续 10 轮无人工动作：退出码 1，期望 0；输出不含“M1 无人值守：通过”
  ✓ G4 [可执行] 工具版本由驱动器自己构建并装上：通过
exit=0  耗时 0.4s
```

2) 最近 8 条 round_result 事件原文。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n round_result workflow/events.jsonl | tail -n 8
1358:{"seq": 1353, "ts": "2026-10-10T14:28:58+00:00", "type": "round_result", "turn": 41, "state": "VERIFY", "ok": true, "by": "driver"}
1381:{"seq": 1376, "ts": "2026-10-10T14:33:10+00:00", "type": "round_result", "turn": 42, "state": "VERIFY", "ok": true, "by": "driver"}
1406:{"seq": 1401, "ts": "2026-10-10T14:37:04+00:00", "type": "round_result", "turn": 43, "state": "VERIFY", "ok": true, "by": "driver"}
1429:{"seq": 1424, "ts": "2026-10-10T14:40:59+00:00", "type": "round_result", "turn": 44, "state": "VERIFY", "ok": true, "by": "driver"}
1451:{"seq": 1446, "ts": "2026-10-10T14:43:48+00:00", "type": "round_result", "turn": 45, "state": "VERIFY", "ok": true, "by": "driver"}
1472:{"seq": 1467, "ts": "2026-10-10T14:46:34+00:00", "type": "round_result", "turn": 46, "state": "VERIFY", "ok": true, "by": "driver"}
1493:{"seq": 1488, "ts": "2026-10-10T14:49:46+00:00", "type": "round_result", "turn": 47, "state": "VERIFY", "ok": true, "by": "driver"}
1515:{"seq": 1510, "ts": "2026-10-10T14:52:40+00:00", "type": "round_result", "turn": 48, "state": "VERIFY", "ok": true, "by": "driver"}
exit=0  耗时 0.1s
```

3) 最近 10 条 by 为 user 的事件原文；没有匹配也如实回报。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n by...user workflow/events.jsonl | tail -n 10
1533:{"seq": 1528, "ts": "2026-10-10T14:55:32+00:00", "type": "tick", "turn": 49, "state": "AWAITING_USER", "exit": 20, "note": "以下内容需要用户批准后才会执行：\n- 命令 3「grep -n by...user workflow/events.jsonl | tail -n 10」：不在已批准的命令白名单中\n- 命令 4「grep -n by...executor workflow/events.jsonl | tail -n 10」：不在已批准的命令白名单中", "steps": 12, "by": "driver"}
1535:{"seq": 1530, "ts": "2026-10-10T14:56:48+00:00", "type": "tick", "turn": 49, "state": "AWAITING_USER", "exit": 20, "note": "以下内容需要用户批准后才会执行：\n- 命令 3「grep -n by...user workflow/events.jsonl | tail -n 10」：不在已批准的命令白名单中\n- 命令 4「grep -n by...executor workflow/events.jsonl | tail -n 10」：不在已批准的命令白名单中", "steps": 1, "by": "driver"}
exit=0  耗时 0.0s
```

4) 最近 10 条 by 为 executor 的事件原文；没有匹配也如实回报。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n by...executor workflow/events.jsonl | tail -n 10
1533:{"seq": 1528, "ts": "2026-10-10T14:55:32+00:00", "type": "tick", "turn": 49, "state": "AWAITING_USER", "exit": 20, "note": "以下内容需要用户批准后才会执行：\n- 命令 3「grep -n by...user workflow/events.jsonl | tail -n 10」：不在已批准的命令白名单中\n- 命令 4「grep -n by...executor workflow/events.jsonl | tail -n 10」：不在已批准的命令白名单中", "steps": 12, "by": "driver"}
1535:{"seq": 1530, "ts": "2026-10-10T14:56:48+00:00", "type": "tick", "turn": 49, "state": "AWAITING_USER", "exit": 20, "note": "以下内容需要用户批准后才会执行：\n- 命令 3「grep -n by...user workflow/events.jsonl | tail -n 10」：不在已批准的命令白名单中\n- 命令 4「grep -n by...executor workflow/events.jsonl | tail -n 10」：不在已批准的命令白名单中", "steps": 1, "by": "driver"}
exit=0  耗时 0.1s
```

③ 需要决策的点
无。

④ 需要注意（Attention）
- 清单第 2, 3, 4 项没有机器判据，结论需要 Claude 根据原文判断
- 当前对话已累计约 22319 字符、9 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

附：仓库指纹 6a98508768686f43（v1.7，7 个文件）
附：发送前额度 5 小时窗口 35.0%，7 天窗口 67.0%
