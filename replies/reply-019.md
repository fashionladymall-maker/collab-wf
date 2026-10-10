[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 present_files]
【本轮结论】
1. 指纹不一致不是缺陷。执行端在 v0.6 落地之前改过 workflow/policy.json（提交 af6dfc0，放行构建器），而我声明的指纹是按旧白名单算的。本轮读一遍现在的 policy.json 原文，我按原文重算后，下个项目版本里一并核对。
2. 第 1 条命令报"命令执行失败，退出码 0"，是我在 1.5.2（W21）留下的误导：命令退出码是 0，只是没通过判据。1.5.7 改成：这种情况由验收报 CHECK_FAIL，不再同时报"执行失败"。
3. 效率问题，执行端的量化是对的：状态机一秒就能跑完一整轮，时间都花在等 300 秒的唤醒间隔上。1.5.7 的做法是：
   - wf tick 处在"发送"或"等待 Claude"时，如果下一次轮询的时刻落在本次唤醒的时间预算（540 秒）以内，就留在 tick 里等到那时再查。
   - 这样发现回复的延迟从约 300 秒降到轮询间隔（120 秒）以内。
   - 空闲时不轮询；`wf run` 和测试里的 0 秒间隔都不走这条路，原有自检不受影响。
   - 下一次唤醒会遇到项目锁，直接跳过，不会重复执行。
4. 构建器 build_wf_157.py 已重新交付，取代第 18 轮的同名文件，旧的那份请丢弃。内容：
   - 系统通知（同第 18 轮）。
   - tick 内等回复。
   - 判据失败的提示改正。
   - 新增 docs/REVIEW.md（见第 7 条）。
   - 自检新增 5 项，预计 182 项。
   
   它在模拟的 1.5.6 树上跑通了 15 处锚点加 2 个新文件。本轮第 1 条命令就是用用户已批准的那条白名单规则，由驱动器自己构建它。
5. 关于"驱动器自己安装工具版本"，我的设计决定如下。`wf self-update` 被写死为手动，是有意的红线：没有这条，自动化就能修改自己的护栏。用户的授权我照办，但把范围划清：
   - **只碰功能的版本，自动安装。** 白名单放行 `wf self-update --auto workflow/outbox/collab-wf-X.Y.Z.zip` 这一种形式，并在 policy 里加 "auto_self_update": true。
   - **改动了护栏文件的版本，停下等用户点头。** 护栏文件指白名单判定、批准与决定码、安装与回滚、异常处置、驱动器的评审流程（policy.py、asks.py、release.py、anomalies.json、driver.py 等）。
   - **判断由旧代码做。** "改没改护栏"由当前已安装的代码在装之前检查，新版本没法绕过；另一个模型系列审查（block）也会在装之前生效。
   - **安全性。** 安装在驱动器的子进程里进行。驱动器本次运行仍用内存里的旧代码，下一次唤醒才加载新代码；它和项目锁、看板重启都不冲突。
   
   实现放在 1.5.8。动手前要读 policy.py 的 classify 与 release_findings 原文，以及 wf.py 中 self-update 的入口，本轮一并读。到时我会给出要加进 allow 的确切那一行，以及 auto_self_update 的写法，由执行端按用户原话批准。
6. 1.5.7 是最后一次需要执行端手动安装的版本（`wf self-update workflow/outbox/collab-wf-1.5.7.zip`）。1.5.8 也只在第一次引入自动安装能力时手动装一次，因为它改的正是护栏文件；之后的功能版本由驱动器自己构建、自己安装。
7. 另一个模型系列审查（用户已批准）的完整约定写在 docs/REVIEW.md：
   - **调用时机**：每个版本包装之前调用，没有版本包的轮次不调用，所以成本只随版本数增加。
   - **审查清单**：红线、护栏、夹带、破坏、质量五条。前四条命中任何一条就判 block，只有第五条判 concerns。
   - **输出**：最后一行 JSON，`{"verdict": …, "notes": […], "model": …}`。
   - **处置**：block 停下等用户；concerns 写进"需要注意"；审查程序本身出故障时记"未审查"，不卡住流程。
   - **分工**：审查程序由执行端按这份约定实现。
8. 执行端问的两个细节：
   - 解释器：驱动器跑的命令里一律写 3.13.12 的绝对路径。裸 python3 会解析到 3.12，第 15 轮那次分叉就是这么来的。
   - 工作目录：驱动器执行命令时，工作目录是项目根。第 1 轮的相对路径命令能成功，就是证据。

【本机执行】
```
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-019/delivered/build_wf_157.py --out workflow/outbox
cat workflow/policy.json
grep -n -A 40 ^def.classify /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/policy.py
grep -n -A 30 ^def.release_findings /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/policy.py
grep -n self-update /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
grep -n -A 6 self_update /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
```

【本轮回报清单】
1. 驱动器自己构建 1.5.7：最后一行是"构建通过"，预计自检 182 项。
2. 现在的 policy.json 原文。
3. policy.py 的 classify 全文。
4. policy.py 的 release_findings 全文。
5. wf.py 中 self-update 的解析位置。
6. wf.py 中调用 self_update 的位置。

【需要用户决定】
1. 1.5.8 的护栏规则：功能版本自动安装；改动了护栏文件的版本停下等用户点头。用户若希望连护栏改动也全自动，请明确说出来；我的建议是保留这一道人工确认。不影响当前工作。

【下一轮建议档位】
high

【驱动指令】
发布：无
之后：回报
验收 1：c1 exit=0 包含 "构建通过"
验收 2：c2 exit=0
验收 3：c3 exit=0 包含 "def classify"
验收 4：c4 exit=0 包含 "def release_findings"
验收 5：c5 exit=0
验收 6：c6 exit=0
失败后继续：是