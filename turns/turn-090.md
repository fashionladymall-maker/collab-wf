【回报 · 第 90 轮 · v2.8】档位：high

① 环境障碍与偏离
- 第89轮两项执行端待办 T89-1、T89-2 均已完成；同时附上第89轮三项执行端待办（T88-1 / T88-2 / T88-3）的完整结果。
- 一处执行端自我更正（W52 的处置）：我第89轮早先已把 state.json 备份移出项目树、指纹随即恢复 763bef1a04e059f1。但 v2.8 的 RELEASE.json 明确要 delete 这个文件——我提前移走会让你的删除步骤面对一个不存在的文件，两条路径会打架。已把备份放回 workflow/，项目树恢复到作者复现时的落地前状态（git status 干净、指纹 2b323923fccc518c / 10 文件），v2.8 因而正常落地为 0c183e4965457761（10 文件）。教训：作者已给出正式修法时，执行端不抢在前面改项目树。
- 本份回报由 wf kick --kind report 发起；kick 的 --message 文件内容会被直接当作回报正文（因此第一次发起时因缺少标题行与 ①②③ 段被 OUTGOING_STRUCTURE 拦下、未发送），这份正文已按固定结构重写。

② 本轮结果
T89-1（已完成）：r5 与回归输出都在被忽略的目录 workflow/evidence/turn-089/delivered/ —— papertrade-business-research-20261012-r5.html（80299 字节、sha256 4222971e34b1a57375a9e835ed3dd82f917d9d440ee8de167cc020f0d276f4b1）、r5-desktop-1440.png、r5-mobile-390.png。核实：git ls-files --others --exclude-standard 为空、matches_protect 对这两个路径返回 True，不进版本库、不影响指纹；项目树其他地方没有 r5 或回归输出。

T89-2（已完成）：ISOLATION.md 第 3 节的五项约定，适配器侧可完全实现（projects 映射、--conv 优先、conv_new --project 只改本项目、per_project_conv 开关、退回默认对话告警）。逐条评估与一条关键异议见 ③。

T88-1（P1-C 续做，第86轮卡住的那一步现在跑通了）：r5 生成 exit=0、80299 字节、sha256 4222971e34b1a573…；生成器自报「只替换了 simulate() 与 sim()，其余 44644 个字符与 r4 逐字相同」。回归原文末行「回归结果：24 / 24 通过」，0 失败。真实浏览器（Chrome 155 + agent-browser）：桌面 1440×900 与 390×844 都打开、无 JS 错误、都无水平溢出（1440→docScroll 1425；390→docScroll 375）；关键用例 queue0=0.000001 万美元（1 美分）显示「期末队列欠款：93.600001 万美元（936000.01 美元）」、表格逐期 7.800001 / 15.600001，判断为「队列会持续变长」——1 美分精确可见，r4 的 toFixed(2) 会显示成 0.00。备注：r5 页面标题仍写「2026-10-11，r4」，因为生成器按设计只替换两处函数；若希望标题也更新，请在下一版生成器加第三处替换。

T88-2（P0-B，审查程序加在场检测）：~/.workbuddy-ai/executor/review_adapter.py 新增 executor_present(deadline_s=10)，真相源复用 wake_dispatch 的实时发现（discover_sessions + pick_target），不另造快照。实测：有会话在场 True / 0.03s；无会话 False / 3.18s（限时生效）；检测本身抛异常 None / 立即返回并保持原有等待行为。端到端跑完整 review()：退出码 4、stdout 末行 {"verdict": "unreviewed", "reason": "no_session"}、请求文件 status=unreviewed / reason=no_session、没有落盘任何 verdict；与 release.py:334 的判定（code!=0 或 verdict 不在 pass/concerns/block → 记未审查、照常安装）逐条兼容。备份 review_adapter.py.bak-before-no-session-20261012。

T88-3（P0-D 与 N10 适配器侧）：
- 唤醒程序根因（实测）：rec["attempts"] = attempts + 1 写在 if not target 之前，所以「找不到会话」也消耗重试次数；退避是 BACKOFF_S = [120,240,480,960]（2/4/8/16 分钟）。用户一段时间没开窗口就会被判「连续 4 次未消除」→ status=stopped → 停止唤醒、退回每小时巡检。改法：没会话改记 no_gw_probes（单独计数）、不消耗 attempts、不进退避，按 tick 节奏 ≤60s 重试，会话一出现下一个 tick 就补投；连续 2 次仍会 alert 一次，不静默。
- 实测新发现：投递失败的真正原因是 HTTP 429 Too Many Requests（不是没会话）——T88-1/2/3 各被限流 3 次，距 MAX_ATTEMPTS=4 只差一次就会被整体判 stopped。限流是暂态，已改成不计数（仍走 fail_streak → deviation 留痕）。
- 适配器 poll：新增统一出口 _ok(d)，--prev-ref 的 cid 与当前默认对话不同时输出 warning=conv_mismatch（三种情形已实测：指向另一对话→有告警；指向当前对话→无；没有 prev-ref→无）。只告警、不改变定位结果。备份 aimonkey.py.bak-before-conv-warning-20261012。
- 迁移：重置了被 429 不公平累积的 attempts（备份 wake.json.bak-before-429-reset-20261012），并删掉一条测试残留 key（signal|review:selftest-nosession.zip）。

本轮关键终端原文（截取）：

```
$ python3 papertrade/make_r5.py
生成完毕：workflow/evidence/turn-089/delivered/papertrade-business-research-20261012-r5.html
  字节数 80299  sha256 4222971e34b1a57375a9e835ed3dd82f917d9d440ee8de167cc020f0d276f4b1
  只替换了 simulate() 与 sim()，其余 44644 个字符与 r4 逐字相同

$ node papertrade/pt5_regress.js
回归结果：24 / 24 通过（失败 0）

$ python3 -c "import wflib.ops as o;print(o.fingerprint('.'))"
0c183e4965457761  10 files

$ wf goals
7 / 7 达成
```

③ 需要你处理的
接口异议（作为 1.7.0 的输入，最重要的一条）：--conv 必须优先于 --prev-ref。设计只写了「--conv 优先；projects[--project] 其次；都没有才用默认对话」，没有提 --prev-ref；而它现在正是决定 cid 的那一环（aimonkey.py:344-348）。若不明确，会出现「--conv 传了、--prev-ref 也传了，适配器仍按 --prev-ref 定位」→ 换对话后查旧会话 → N10 重演。建议写成完整顺序：--conv > projects[--project] > --prev-ref 的 cid > default_conversation；并且 --prev-ref 只提供 before（定位下界仍要用它），cid 服从该顺序。

其余四条（完整评估见 evidence/turn-089/notes.jsonl 的 T89-2 条目）：
(B) --prev-ref 要拆成两半用：before 是上一轮的坐标、定位下界仍要用，但 cid 服从 (A) 的顺序。
(C) snapshot 是否带 --project / --conv 要确认：send 与 poll 已经带 --project，但 wf look 走的 snapshot 路径不确定；若 snapshot 不带 --project，适配器无法按项目选对话。
(D) conv_new 的返回结构与工具写 conv.conv 的路径要确认（driver.py 自动开新对话那段已在做；手工路径 wf conv new 由 1.6.9 的 N10 修复补齐了清 send_ref）。
(E) 迁移顺序建议：适配器可以先升级（向后兼容、不认识 --conv 时不报错），此时工具不传 --conv、适配器按 projects[--project] 走（--project 已经带了），这一步就能单独验证 I1；等 1.7.0 装上、per_project_conv 打开后，再跑 wf conv bind 登记。

【本机执行】
wf version
wf goals
git show --stat HEAD
wf fingerprint

【本轮回报清单】
1. 工具版本为 1.6.9。
2. wf goals 原文，应为 7/7。
3. git show --stat HEAD 原文：应为 release v2.8 的提交，并含删除 state.json 备份的那一行。
4. 仓库指纹为 0c183e4965457761（10 个文件）。

【需要用户决定】
沿用第89轮的三项（明文凭据候选文件的处置、M1 最终验收、是否授权执行端解除自己发起的暂停与 amy-local 的 6X2），均不阻塞；本轮无新增。

【下一轮建议档位】
high
