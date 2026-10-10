【本轮结论】
1. 本轮全部通过，连续失败计数应已归零。下一轮的 grep 里有几条可能无匹配（退出码 1），在旧规则下最多让计数变成 1，不会熔断。
2. W19 定为由驱动器接手：h_compose 拿不到正文时，如果本轮已有 wf report 生成的草稿（outbox/turn-NNN.md），就按草稿首行的档位登记 outgoing，接着发送。wf report 本身行为不变，手工流程不受影响。同时把那条必然失败的 wf kick 提示改成"用 wf report 生成本轮回报草稿，驱动器会接着发送"。
3. W21 定为：关联到清单项的命令不再用退出码否决整轮，熔断回到文档写的"连续 3 轮验收未通过"。具体做法：
   - 抽出一个公共判断，供 h_verify 和 _to_report 共用。_to_report 用它决定回报档位，所以这次 grep 无匹配不会再把回报抬到 max。
   - h_execute 里，命令有判据且判据通过（例如"exit=1"）时，不算失败、不停止执行。
   
   这一步要先读 h_execute 和 goals.judge，确认判据支持 exit=1。
4. W15 定为：driver.py:617 改成只认"（前置）"或"(前置)"。driver.py 已经导入 re，不用加导入。
5. W18 方案不变，见上轮。wf pause 先写 paused、再 event()，所以只要 event() 里的合并生效，暂停就会落盘；驱动器主循环每一步开始前合并一次并检查暂停。
6. 本轮读四样东西：
   - h_execute 和 judge。
   - release.py 的版本包解压、落地和自升级部分，用来确定 v0.2 的 zip 格式和构建器的打包方式。
   - 工具 CHANGELOG 的开头格式。
   - 自检里与这些改动相关的断言（kick、state.json、consecutive_failed、需要用户决定、carry），以及 selftest_v15 是怎么被调起的。
   
   下一轮读 selftest_v15.py 全文，然后交付构建器。

【本机执行】
```
sed -n 773,826p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
grep -n -A 30 judge /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/goals.py
sed -n 21,48p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/release.py
sed -n 125,171p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/release.py
sed -n 254,317p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/release.py
sed -n 349,430p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/release.py
sed -n 1,30p /Users/admin/.workbuddy-ai/tools/collab-wf/CHANGELOG.md
grep -n selftest /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
grep -n selftest_v15 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py
grep -n kick /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py
grep -n kick /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py
grep -n consecutive_failed /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py
grep -n state.json /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py
grep -n state.json /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py
grep -n 需要用户决定 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py
grep -n carry /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py
```

【本轮回报清单】
1. driver.py 第 773–826 行（h_execute）。
2. goals.py 中 judge 的定义与判据语法。
3. release.py 第 21–48 行（_extract）。
4. release.py 第 125–171 行（apply_zip）。
5. release.py 第 254–317 行（self_init、self_update）。
6. release.py 第 349–430 行（工具文件清单、manifest、self_check）。
7. 工具 CHANGELOG.md 的开头 30 行。
8. wf.py 中 selftest 的调用位置。
9. selftest.py 中调起 selftest_v15 的位置；没有匹配也照原样回报。
10. 自检中涉及 kick 的断言（selftest_driver.py）；同上。
11. 同上（selftest.py）。
12. 自检中涉及 consecutive_failed 的断言；同上。
13. 自检中直接读写 state.json 的位置（selftest_driver.py）；同上。
14. 同上（selftest.py）。
15. 自检里回复样本中"需要用户决定"块的写法；同上。
16. selftest.py 中涉及续传的断言；同上。

【需要用户决定】
无

【下一轮建议档位】
high

【驱动指令】
发布：无
之后：回报
验收 1：c1 exit=0 包含 "def h_execute"
验收 2：c2 exit=0 包含 "def judge"
验收 3：c3 exit=0 包含 "def _extract"
验收 4：c4 exit=0 包含 "def apply_zip"
验收 5：c5 exit=0 包含 "def self_update"
验收 6：c6 exit=0 包含 "def self_check"
验收 7：c7 exit=0
验收 8：c8 exit=0 包含 "selftest"
失败后继续：是