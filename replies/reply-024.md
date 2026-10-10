[工具 bash_tool]
[工具 create_file]
[工具 bash_tool]
[工具 create_file]
[工具 bash_tool]
[工具 bash_tool]
[工具 create_file]
[工具 bash_tool]
[工具 create_file]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 present_files]
【本轮结论】

第 24 轮 10 项的判定：c1–c6、c10 通过。c7 只有一处命中，在 selftest_v15.py 第 657 行，就是 1.5.8 的护栏断言。c8、c9 的 exit=1 是预期中的无匹配：说明除了这一处，没有别的自检依赖护栏文件。这两条被报成 EXEC_FAIL，原因是我没有写 exit=1 判据，这是我的疏漏；以后可能无匹配的检索一律写 exit=1。c5 和 c10 还在续传队列里，本版没有用到它们。

本轮交付构建器 build_wf_159.py（29867 字节，sha256 `eee47790f61d`），已确认在交付目录里。改动如下：

1. **新增 `wflib/floor.py`。** 底线、版本包内容检查、护栏命令、命令拼接的定义都集中到这里，policy.py 改为从它导入，原有名字保持可用。
2. **自动安装的护栏文件缩为 floor.py 一个。** 其余文件的改动，在安装前由已安装的 `floor.probe()` 在隔离的子进程里检查候选版本，共四项：
   - 8 条底线用例全部停下；
   - 改动 floor.py 仍判为要用户确认；
   - self_update 仍调用这两项检查；
   - 执行前评审仍调用 classify。

   这补上了 1.5.8 只改 driver.py 就能绕开底线的缺口。
3. **执行端待办信号 `local/inbox.json`**，格式与上一轮定的一致。它不受通知开关影响；自检时不写真实的 inbox 文件。
4. **文档和自检。** DRIVER.md 更正第 18 节，新增第 19 节。自检新增 5 项，预计共 189 项。改动 1 项旧断言：policy.py、anomalies.json 不再算护栏文件，floor.py 算。CHANGELOG 已写明。

本地核对过三件事：
- 重建的 policy.py、notify.py 与 1.5.8 清单逐字节一致（`9417d879d1f9`、`2c51155771e1`）。
- 13 处锚点在桩工具树上都只命中一次，编译通过，6 项新改的检查全部通过。
- 另外试了三种削弱方式：去掉 probe 调用、评审不再调用 classify、清空 GUARD_FILES。行为检查都能查出来。

release.py、selftest_v15.py、DRIVER.md、CHANGELOG.md 我只读到了片段，所以这几处锚点依靠构建器的哈希核对和完整自检来兜底：任何一处不符，构建都会整体中止。

**构建通过后，执行端手动安装一次。** 这是最后一次手动安装：1.5.9 改了 1.5.8 的护栏文件 policy.py、release.py，按设计不能自动安装。授权依据是用户已经记录的原话「用户希望连护栏改动也全自动」。安装命令：

`wf self-update workflow/outbox/collab-wf-1.5.9.zip`

装完运行 `wf version`、`wf self-check`、`wf doctor`，然后用 `wf kick --kind report` 回报。之后只有改动 floor.py 的版本才会停下等用户。

【本机执行】
```
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-024/delivered/build_wf_159.py --out workflow/outbox
```

【本轮回报清单】
1. 构建器最后一行是「构建通过」，自检预计 189 项全部通过。如果中止了，请给出中止的那一行和未通过的检查。

【需要用户决定】
无。

【下一轮建议档位】
high

【驱动指令】
发布：无
之后：空闲
验收 1：c1 exit=0 包含 "构建通过"
超时：1800