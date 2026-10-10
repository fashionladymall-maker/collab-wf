[工具 bash_tool]
【本轮结论】
1. 4 条退出码 1 都是"没有匹配"，属于正常结果，不是故障：
   - ops.py 里没有续传逻辑，续传全部在 fidelity.py 里。
   - 三个自检文件都不含"前置"，所以 W15 改判断条件不会碰到任何现有断言。还要再查一遍"阻塞"（第 11 条）。
2. core.py 我按原文重建后得到 8156 字节、71dd368941dd，与工具清单一致，可以直接在它上面打锚点。
3. W15 已定位到 driver.py:617，条件是 `"前置" in x or "阻塞" in x`。改为只认显式标记"（前置）"，全角和半角括号都认。
4. seq_check 的倒序判定在 fidelity.py:403，`b <= a` 改为 `b < a`。改之前先查自检里有没有相关断言（第 13 条）。
5. W18 的方案（等读完 machine.py 和驱动器主循环再定稿）：
   - Instance 增加跨进程状态锁：放在 LOCAL/locks/<项目>.state.lock，用 flock，进程内可重入。
   - save() 在锁内做三方合并：只把本进程从读入以来改过的键写回磁盘，其余键以磁盘为准，合并结果同时回写内存。
   - event() 在锁内取磁盘 seq 与内存 seq 的较大值再加 1。
   - 驱动器每一步开始前重新检查是否暂停。
   
   效果：并发时不再重号，手工写入的字段（包括暂停）也不会被驱动器覆盖。双方同时改同一个键时，仍是后写者生效，所以暂停要单独检查。
6. W20 先读 fidelity.py:209–295 的续传实现，再决定是改成"续传优先"，还是把文档改成与现行为一致。
7. 本轮只读不改。读完后下一轮开始写 1.5.2 构建器。v0.2（PROJECT.md 记入 W16–W20）随后交付。

【本机执行】
```
cat /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/machine.py
sed -n 47,118p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
sed -n 209,295p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/fidelity.py
grep -n paused /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
grep -n paused /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
grep -n state_path /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
grep -n state_path /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/ops.py
grep -n state_path /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/admin.py
grep -n state_path /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
grep -n state_path /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/server.py
grep -n 阻塞 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py
grep -n user_decisions /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py
grep -n backwards /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_v15.py
grep -n 需要内容 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py
```

【本轮回报清单】
1. machine.py 全文。
2. driver.py 第 47–118 行（run 与主循环）。
3. fidelity.py 第 209–295 行（续传）。
4. driver.py 中暂停的判断位置。
5. wf.py 中暂停的写入位置。
6. driver.py 中直接读写 state_path 的位置；没有匹配也照原样回报。
7. 同上（ops.py）。
8. 同上（admin.py）。
9. 同上（wf.py）。
10. 同上（server.py）。
11. 自检中含"阻塞"的断言；没有匹配也照原样回报。
12. 自检中涉及 user_decisions 的断言；同上。
13. 自检中涉及 backwards 的断言；同上。
14. 自检中涉及"需要内容"提示语的断言；同上。

【需要用户决定】
无

【下一轮建议档位】
high

【驱动指令】
发布：无
之后：回报
验收 1：c1 exit=0 包含 "def go"
验收 2：c2 exit=0 包含 "def _run_locked"
验收 3：c3 exit=0 包含 "def report_evidence"
失败后继续：是