【本轮结论】

六项都读到了，DRIVER.md 也随续传送达，逐项判定如下：

1. asks.py：已读。
2. driver.py 里有 4 处调用 classify（242、622、652、834 行）。
3. 通知在 wf.py 第 664–665 行，tick 之后调用 `notify.after_tick(insts)`。所以 1.5.9 只改 notify.py 的内部，wf.py 不用动。
4. plist 在 wf.py 第 204–235 行。
5. 自动安装从 wf.py 第 916 行进入 `release.self_update(auto=…)`。
6. 1.5.8 的测试注册在 selftest_v15.py 第 673 行。

DRIVER.md 第 5 节的格式我现在拿到原文了，上一轮写的「验收 N：exit=0」少了 `cN`，从本轮起按原文格式写。

**inbox 的格式先定下来，执行端可以同时准备定时自动化。** 工具在每次 tick 之后覆盖写入 `~/.workbuddy-ai/tools/collab-wf/local/inbox.json`，结构如下：

```
{"updated": "…", "items": [{"key", "project", "for": "executor"|"user", "title", "why", "action", "since"}]}
```

`for` 的划分：

- 写 `user` 的情况：四道底线命中（包括版本包新增的实盘、密钥、资金内容，以及改动 floor.py 的工具版本）、撤销用户的暂停、目标达成。执行端看到后转告用户，不替用户做决定。
- 其余一律写 `executor`：阻塞、普通的等待批准、手动步骤、驱动器停了、卡住、额度。

事项处理掉之后，它会自动从文件里消失。执行端的定时自动化只需要读这个文件，处理新出现的 key，然后用 `wf kick --kind report` 回报。macOS 通知只对 `user` 类事项生效，并且仍受 notify.json 开关控制，现在保持关闭。

**为什么还要再读一轮。** 1.5.9 把 policy.py 移出护栏集，`_t_v158_auto_update` 里很可能有断言依赖「改 policy.py 就拒绝」。按约定（W12），我必须先读到这些断言原文，再一并改掉。另外：

- 构建器骨架沿用第 20 轮那个跑通的 build_wf_158.py，所以要读它的原文；
- 驱动器写 needs_text 的方式决定 inbox 怎么识别底线命中，所以要读 driver.py 622 行附近；
- plist 的间隔要做成可配置，所以要读 wf.py 第 195–240 行。

读完之后，下一轮交付 build_wf_159.py。

【本机执行】
```
cat workflow/evidence/turn-020/delivered/build_wf_158.py
sed -n 600,680p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_v15.py
sed -n 1,40p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_v15.py
sed -n 605,660p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
sed -n 195,240p /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
sed -n 1,20p /Users/admin/.workbuddy-ai/tools/collab-wf/CHANGELOG.md
grep -n 护栏 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_v15.py
grep -n 护栏 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py
grep -n 护栏 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py
grep -n LOCAL /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/core.py
```

【本轮回报清单】
1. build_wf_158.py 全文，放不下的部分续传。
2. selftest_v15.py 第 600–680 行（1.5.8 测试与注册表）。
3. selftest_v15.py 第 1–40 行（导入与辅助函数）。
4. driver.py 第 605–660 行（评审时如何记录停下的原因）。
5. wf.py 第 195–240 行（schedule plist）。
6. CHANGELOG.md 第 1–20 行。
7. selftest_v15.py 中提到「护栏」的位置，可能没有匹配。
8. selftest_driver.py 中提到「护栏」的位置，可能没有匹配。
9. selftest.py 中提到「护栏」的位置，可能没有匹配。
10. core.py 中 LOCAL 的定义。

【需要用户决定】
无。

【下一轮建议档位】
high

【驱动指令】
发布：无
之后：回报
验收 1：c1 exit=0
验收 2：c2 exit=0
验收 3：c3 exit=0
验收 4：c4 exit=0
验收 5：c5 exit=0
验收 6：c6 exit=0
验收 10：c10 exit=0
失败后继续：是