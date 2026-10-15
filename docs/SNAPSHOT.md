# SNAPSHOT.md —— Claude 页面快照的适配器约定（1.5.3；截图 1.5.4）

工作流看板的“Claude 页面快照”显示执行端最近一次看到的 Claude 会话页面，有文本与截图两种形态（第 5 节）：每看一次就记一次查看时间，内容变了就更新。抓取页面要调用通道站点的接口，这部分由执行端在传输适配器里实现；本文件只规定接口与格式（DESIGN.md 第 2 节的分工）。工具只读这里规定的中立格式，不依赖站点的原始字段。

## 1. 何时写快照

- poll：wf 调用适配器的 poll 时，在环境变量 WF_PAGE_SNAPSHOT 里给出一个文件路径。适配器在这次 poll 中拉取过会话，就把快照写到这个路径；没有拉取就不写。
- snapshot（新动作，供 wf look 使用）：`snapshot --project <名称> --ref <ref> --out <目录>`，环境变量同上。只拉取会话并写快照：不发送、不下载交付文件、不改变任何状态。成功时退出码 0，标准输出最后一行是 JSON，例如 {"status": "ok", "messages": 20}；失败时退出码非 0，JSON 里写明原因。动作日志的动作名用 snapshot（已加入 BRIDGE_ACTIONS）。模块方式的适配器按子命令 snapshot 接收；程序方式的适配器在 transport.json 里配置 "snapshot" 程序。
- 写法：先写同目录的临时文件再改名，保证 wf 读到的是完整文件。环境变量不存在时不写（旧版 wf）。
- wf 在调用前删除旧文件、调用后收下并删除，所以文件存在就表示这次调用写出了新快照。

## 2. 格式（collab-wf.snapshot/1）

UTF-8 编码的 JSON 对象：

    {"schema": "collab-wf.snapshot/1", "captured_at": "<抓取时刻，ISO 8601>",
     "conversation": {"id": "…", "name": "…", "model": "…", "updated_at": "…", "current_leaf": "<当前叶子消息的 id>"},
     "messages": [ … ]}

messages 是页面上的全部消息，按 index 升序，每条：

- index：站点给的序号（可能跳号，原样保留）；id；parent：父消息的 id 或 null
- role：human 或 assistant
- created_at、stop_reason、truncated
- blocks：消息内容按页面顺序排列，每块 {"type": …, "text": …}
  - text：正文
  - thinking：思考
  - tool_use：工具调用；另带 name，text 为输入（JSON 文本）
  - tool_result：工具结果；另带 name（取得到时）、is_error；text 为结果的全部文字
  - other：其他类型；另带 kind 写原始类型名，text 为能取到的文字
- files：附件与交付文件的名称列表（可选）

原封不动：所有 text 逐字保留，不截断、不改写、不转义；页面上有但取不到文字的内容，用 other 块写明。

## 3. wf 怎样处理

- 收下后逐字节原样保存为 workflow/evidence/page/latest.json 与本轮的 turn-NNN.json；内容（消息与当前叶子）没变时不重写，只记录这次查看。每次查看在 checks.jsonl 记一行，meta.json 记最近查看与最近变化的时间。
- 按轮保留最近 50 份（policy.json 的 page_snapshot_keep 可改；改 policy 需要重新批准）。
- 格式不符时不覆盖已有快照，问题写进 checks.jsonl。
- 快照只用于展示与取证，不参与任何状态判定：会话的消息列表在生成结束前不会出现新消息（collab-wf-dev 第 10 轮），不能用它判断消息是否送达。
- 看板通过只读接口 /api/page 读取（?meta=1 只取元信息；?turn=N 取第 N 轮的快照）；对话记录通过 /api/transcript 读取。看板只监听 127.0.0.1。

## 4. 适配器自检建议

- 拿一次真实会话写出快照，载入后核对：schema 正确；messages 条数与页面一致；每个 text 块与页面逐字相同；thinking、tool_use、tool_result 都在。
- 运行 wf look：看板“Claude 页面快照”应在 20 秒内出现，“最近查看”的时间随之更新。

## 5. 页面截图（1.5.4）

用户第 13 轮明确要求快照要“文本 + 截图”两种形态。此前用户否定的是用截屏与坐标点击去操作页面；这里的截图只用于留档，不用于操作。适配器里写着“不截屏”的旧注释，请按新约定一并更正。

- 位置：wf 同时给出环境变量 WF_PAGE_SNAPSHOT（文本快照）与 WF_PAGE_SCREENSHOT（一个 .png 路径）。适配器截到图，就把 PNG 写到这个路径（先写临时文件再改名）；没截就不写。没有 WF_PAGE_SCREENSHOT 时不截图（policy.json 的 page_screenshots 为 false，或旧版 wf）。
- 说明：文本快照增加可选字段 screenshot：{"status": "ok" | "unchanged" | "unavailable" | "error", "reason": "…", "mode": "viewport" | "full", "captured_at": "…"}。没截图时用 status 与 reason 写明原因。
- 截什么：Claude 会话页面本身。默认截滚到最新消息处的一屏（mode 为 viewport）；WF_PAGE_SCREENSHOT_MODE 为 full 时（wf look --full）截整页，过长时只截最后一部分，高度不超过 16000 像素。等页面渲染稳定再截：会话的消息都已显示，没有加载中的占位。与文本快照在同一次调用里先后取得，二者成对。
- 不打扰用户（硬性要求）：不切换、不滚动、不改变用户当前看到的标签页与窗口；不抢鼠标、键盘与前台焦点；不弹出可见的新窗口。可以用后台标签页或无头方式打开同一个会话来截，截完关闭。做不到这几条时就不截图，status 写 unavailable 并写明原因。
- 降级：浏览器没开、调试端口连不上、页面打不开或超时，都只写文本快照，screenshot.status 写 unavailable 或 error；poll 照常返回，不报错，截图部分总耗时不超过 20 秒，超时就放弃。
- 前置条件：截图需要浏览器开着调试端口，适配器所需的 Python 模块（例如 websocket-client）已经安装。缺少时按降级处理：screenshot.status 写 unavailable，reason 写明缺什么、怎样安装（例如 python3 -m pip install websocket-client）。
- 频率：页面与上次截图时相比没有变化（例如 updated_at 与消息数都相同）时可以不截，status 写 unchanged；wf look 总是截一张。
- wf 的处理：PNG 原样保存为 workflow/evidence/page/latest.png 与本轮的 turn-NNN.png，与同一轮的文本快照成对；按轮保留最近 20 张（policy.json 的 page_screenshot_keep）；单张超过 12 MB 不收。看板在快照板块顶部显示最近一张截图，点击看原图；本次没有截图时写明原因，并保留上一张。截图通过只读接口 /api/page-image 读取。
- 自检：截图前后，用户当前标签页的地址与滚动位置、前台应用都不变；关掉浏览器的调试端口再运行 wf look，应当只更新文本快照，看板写明“本次查看没有新截图”。截到的图里应当有会话正文，而不是加载中的骨架屏。
