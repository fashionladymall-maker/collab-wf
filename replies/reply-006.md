【本轮结论】
1. 执行端提的 exit=1 问题成立，记为 W21（W20 已经给了续传先后顺序）。代码现在用"任何命令退出码非 0"来否决整轮，而 DESIGN 和 DRIVER 写的熔断条件是"连续 3 轮验收未通过"，两者不一致。按执行端的方案 (a) 修：
   - 有清单项的命令，成败以清单项为准；清单项没写判据（暂缓）时，退出码不否决整轮。
   - 只有没关联到任何清单项的命令，才按退出码算失败。
   - 我今后用"exit=1"把"预期无匹配"写成判据。
   
   不要加 `|| true`，它会掩盖真失败，白名单也会拦下 `||`。
2. 风险提醒：连续失败计数现在是 2。本轮只放 sed 读取，退出码一定是 0。1.5.2 装好之前，我不再给可能无匹配的 grep。万一还是熔断了，执行端按用户的整体授权 wf approve 继续即可。
3. W18：执行端的第 (1) 条（锁内重读 seq）只能治重号，治不了丢暂停，因为驱动器写回的是整份过期状态。原文显示所有写入都经过 save() 或 event()，五个文件里没有直接写 state_path 的地方，所以修在 core.py 一处就能覆盖：
   - 新增进程内可重入、跨进程互斥的状态锁。
   - save() 在锁内做三方合并：只写回本进程改过的键，其余以磁盘为准。合并结果原地更新内存字典，让 ctx.st 等已有引用继续有效。
   - event() 取磁盘 seq 与内存 seq 的较大值再加 1，事件追加也放在锁内，所以文件顺序和 seq 顺序一致。
   - 驱动器主循环每一步开始前先合并一次、检查暂停。
   - seq_check 的倒序判定改为 b < a。
4. W20 已看清：续传排在本轮证据之后，并且只要有一片放不下，后面就全部推迟。改为续传先嵌入；一片都没放下时，不再输出空标题。
5. 采纳执行端的请求：在 BRIDGE_ACTIONS 里加入 conv_new。适配器七项全部落地，PROJECT.md 里对应的待办在 v0.2 一并勾掉。
6. 节奏：本轮读驱动器和 wf.py 的相关段落；下一轮读 selftest_v15.py 全文（照它的写法补新用例）和 release.py 的版本包格式；再下一轮交付 1.5.2 构建器和 v0.2。

【本机执行】
```
sed -n 1,46p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
sed -n 316,367p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
sed -n 580,700p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
sed -n 827,901p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
sed -n 520,545p /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
sed -n 700,730p /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
```

【本轮回报清单】
1. driver.py 第 1–46 行（导入、退出码、Ctx）。
2. driver.py 第 316–367 行（h_compose）。
3. driver.py 第 580–700 行（h_review、_to_report、_clear_turn）。
4. driver.py 第 827–901 行（h_verify，整轮成败的判定）。
5. wf.py 第 520–545 行（wf report）。
6. wf.py 第 700–730 行（pause、unpause、undo）。

【需要用户决定】
无

【下一轮建议档位】
high

【驱动指令】
发布：无
之后：回报
验收 1：c1 exit=0 包含 "import"
验收 2：c2 exit=0 包含 "def h_compose"
验收 3：c3 exit=0 包含 "def h_review"
验收 4：c4 exit=0 包含 "def h_verify"
验收 5：c5 exit=0 包含 "compose_report"
验收 6：c6 exit=0 包含 "paused"
失败后继续：是