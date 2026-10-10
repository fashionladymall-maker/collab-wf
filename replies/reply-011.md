【本轮结论】
1. 1.5.2 已核对通过：自检 155/155，wf self-check 完全一致，doctor 版本匹配，安装后新增的 31 条事件没有出现新的重号。W15、W18–W21 在下个项目版本里标为已解决。
2. 用户的新需求我接下。下面几点由我裁定，不需要用户再决定。

   **(1) 放在哪个页面**：放在主看板 flow.html，新增两个板块"Claude 页面快照"和"对话记录"。project.html 现有的对话记录保留，补上用户方向后它的"用户"筛选也会有内容。

   **(2) "原封不动"的口径**：分两层，都逐字保存、逐字显示。
   - 对话记录：执行端与 Claude 之间收发的全文。现有 transcript 已经是逐字的。
   - 页面快照：网页上看到的全部内容，包括思考、工具调用和工具结果。
   
   看板按纯文本显示（保留换行和空格，不做 Markdown 渲染，内容全部转义）。折叠只是显示方式，展开就是全文。

   **(3) 何时更新、保留多少**：执行端每看一次页面（驱动器每次 poll，或执行端手动运行新命令 wf look），都记录一次"看过"的时间；内容有变化才重写快照。
   - 每轮保留最后一份快照，默认保留最近 50 轮，可在 policy 里改。
   - 存放在 workflow/evidence/page/ 下。这个目录本来就被 git 忽略、受落地保护、不计入指纹，也不进恢复包。

   **(4) 如何连到看板**：新增两个只读接口，/api/page（最新快照及其元信息）和 /api/transcript（对话记录全文）。看板保持只读。

   **(5) 用户方向**：纳入本次范围。
   - _record_user 在写事件之外，也写一条 role=user 的记录到 transcript。
   - 新增 wf said --text …，执行端转述用户原话时用它记下，原话逐字保存。

3. 边界：抓取页面要调用站点接口，这部分归适配器，由执行端实现，我只定约定。约定如下，下一轮写进新文档 docs/SNAPSHOT.md：
   - 每次 poll 时，在 --out 目录写一份 snapshot.json，用中立格式。顶层有 schema、captured_at、conversation（id、name、model、updated_at、current_leaf）和 messages。messages 按 index 排序，每条带 index、id、parent、role、created_at、stop_reason、truncated，以及 blocks。
   - blocks 的每项有 type（text、thinking、tool_use、tool_result、other）和逐字的 text；工具调用另带 name，输入写成 JSON 文本。
   - 新增 snapshot 动作：`snapshot --out <目录>`，只抓取、不发送，供 wf look 使用。
   
   工具这边只读这份中立格式，不依赖站点的原始字段。快照只用于展示和取证，不参与任何状态判定，这一点沿用第 10 轮的结论。
4. 版本号用 1.5.3，主次版本仍是 1.5，不需要 wf pin。构建器和项目 v0.3 下一轮一起交付。本轮先读要接入的代码：
   - 驱动器的轮询调用点。
   - 适配器的调用方式（anomaly.call）。
   - transcript 的写入点和 _record_user。
   - server.py 的路由和监听地址。监听地址必须确认只绑定本机，否则不该在看板上放对话全文。
   - flow.html 的取数方式和板块写法，以及 project.html 现有对话记录的渲染方式，新板块照它的样式写。
5. 本轮所有检索都没写判据，按 1.5.2 的规则记为暂缓，无匹配也不会计入熔断。

【本机执行】
```
sed -n 368,560p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
grep -n -A 30 def.call /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/anomaly.py
sed -n 30,95p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/ops.py
sed -n 100,125p /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
grep -n def /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/server.py
grep -n api/ /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/server.py
grep -n -A 3 HTTPServer /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/server.py
grep -n dashboard /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/server.py
grep -n transcript /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/server.py
grep -n api/ /Users/admin/.workbuddy-ai/tools/collab-wf/dashboard/flow.html
grep -n -A 14 需要注意 /Users/admin/.workbuddy-ai/tools/collab-wf/dashboard/flow.html
grep -n body /Users/admin/.workbuddy-ai/tools/collab-wf/dashboard/flow.html
wc -l /Users/admin/.workbuddy-ai/tools/collab-wf/dashboard/flow.html
sed -n 130,215p /Users/admin/.workbuddy-ai/tools/collab-wf/dashboard/project.html
```

【本轮回报清单】
1. driver.py 第 368–560 行（h_send、h_wait_reply 中的 poll 调用）。
2. anomaly.call 的定义（适配器怎样被调用、返回什么）。
3. ops.py 第 30–95 行（mark_sent、mark_replied 写 transcript 的位置与格式）。
4. wf.py 第 100–125 行（_record_user）。
5. server.py 的函数索引。
6. server.py 的路由。
7. server.py 的监听地址。
8. server.py 怎样提供 dashboard 文件。
9. server.py 中 transcript 的读取位置。
10. flow.html 的取数调用。
11. flow.html 中一个现有板块的写法（以"需要注意"为例）。
12. flow.html 的 body 位置。
13. flow.html 的行数。
14. project.html 第 130–215 行（现有的对话记录渲染）。

【需要用户决定】
无

【下一轮建议档位】
high

【驱动指令】
发布：无
之后：回报
验收 1：c1 exit=0 包含 "def h_wait_reply"
验收 3：c3 exit=0
验收 4：c4 exit=0
失败后继续：是