[工具 bash_tool]
【本轮结论】
1. 指纹已独立核对。我按 ops.py:551–557 的规则，用自己重建的 4 个文件重算，得到 4d1aa87b9b11c651，与工具给出的一致。workflow/wf.json 按 FP_EXCLUDE 排除在外，所以是 4 个文件。至此新对话的续接核对全部完成：PROJECT.md、WORKFLOW.md、4 个项目文件、3 份文档、指纹都逐字节一致。
2. W18 的分析我采纳，并补一个更严重的后果。驱动器在等发送的几分钟里拿着过期的内存状态，醒来后写回的不只是 seq，而是整份状态，所以期间别人写入的字段也会被覆盖。07:28:41 的 wf pause 就是这样丢的：seq 13 之后没有任何 unpause 或用户决定事件，驱动器却在 seq 15、57、58、65–67 继续推进。修法分三步：
   - 所有写状态的 wf 命令和驱动器共用一把项目文件锁。
   - 拿到锁后先从磁盘重读，再修改、分配 seq、写入。
   - 驱动器在每次长等待之后重读状态，重读后先检查暂停。
   
   seq_check 的倒序判定改为 b < a。
3. W18 修好之前的临时做法：要手工执行写状态的 wf 命令（包括 wf note、wf pause）时，先确认驱动器当前不在运行（看板的驱动器卡片或 wf status）；暂停之后再用 wf status 确认确实处于暂停。
4. W19 的分析我采纳：wf report 不设置 outgoing，h_compose 的提示又指向一个在 COMPOSE 下必然失败的 wf kick。1.5.2 两处都改：wf report 与 _to_report 对齐；提示语改成实际可行的动作。执行端改为让驱动器自己收尾，这个做法正确。
5. W20 已确认：上一轮正文证据优先、续传被推后，本轮没有命令，续传才发出来，与 DRIVER 第 11 节"先补发"不符。本轮读 carry 相关代码，再决定是改代码还是改文档。
6. wf conv new 的顺序：按执行端读到的实现（since_turn = 当前轮次 + 1），先 conv new 再发送是对的。1.5.2 改 WORKFLOW 第 10 节和 DRIVER 3d 的写法。
7. 1.5.2 范围：W18、W20、W19、W15，加上文档措辞（六个固定块、conv new 顺序）和 adopt 自动打版本标签。本轮先读定位用的代码和自检里的相关断言；v0.2（PROJECT.md 记入 W16–W20）等读到 release.py 的版本包格式后再交付。
8. 仍请执行端补一条时间线（不涉及适配器代码）：每次只收到 ping 的发送，会话里最终有没有对应的回复。这条用来判断缺陷 28 是缺 settings，还是只是长回复。

【本机执行】
```
cat /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/core.py
grep -n ^def /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
grep -n lock /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
grep -n 前置 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
sed -n 395,410p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/fidelity.py
grep -n carry /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/fidelity.py
grep -n carry /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/ops.py
grep -n compose_report /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
grep -n ^def /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/release.py
grep -n 前置 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py
grep -n 前置 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_v15.py
grep -n 前置 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py
```

【本轮回报清单】
1. core.py 全文，含 def event。
2. driver.py 顶层函数索引。
3. driver.py 中含 lock 的行（锁与 blocking 判断的位置）。
4. driver.py 中含“前置”的行（W15 那一行）。
5. fidelity.py 第 395–410 行（seq_check）。
6. fidelity.py 中的续传逻辑位置。
7. ops.py 中的续传逻辑位置；没有匹配也照原样回报。
8. wf.py 中 compose_report 的调用位置。
9. release.py 顶层函数索引。
10. 自检中与“前置”相关的断言（selftest_driver.py）；没有匹配也照原样回报。
11. 同上（selftest_v15.py）。
12. 同上（selftest.py）。

【需要用户决定】
无

【下一轮建议档位】
high

【驱动指令】
发布：无
之后：回报
验收 1：c1 exit=0 包含 "def event"
验收 2：c2 exit=0
验收 3：c3 exit=0
验收 4：c4 exit=0
验收 5：c5 exit=0
验收 6：c6 exit=0
验收 8：c8 exit=0
验收 9：c9 exit=0
失败后继续：是