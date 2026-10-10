【本轮结论】

七项逐项判定如下，判据由我对照原文给出：

1. doctor 通过：工具是 1.5.8，看板加载的也是 1.5.8，PROJECT.md 为 v0.7，指纹 `65f6fad7a591346c`。
2. git log 通过。上一轮的矛盾解开了：v0.7 标签在 `ed4d6ca`（指纹 e399…），HEAD 是之后单独提交的 policy 改动 `2a5e72f`（指纹 65f6…）。HEAD 不在 v0.7 标签上，这是预期的。
3. policy.json 通过：`auto_self_update: true`，self-update --auto 的规则在白名单里。
4. policy.py：已读。
5. release.py：已读。
6. notify.py：已读。
7. DRIVER.md 暂缓：还在续传队列里，下一份回报会先补发。

上一轮的驱动指令写错了格式（验收行缺命令编号），这是我的问题。本轮先不写验收行，等读到 DRIVER.md 第 5 节再按原文格式写。

**读代码发现 1.5.8 有一个缺口。** `GUARD_FILES` 只包含 policy.py、release.py、asks.py 和 anomalies.json，不含 driver.py 和 wf.py。所以只改 driver.py 的版本（比如不再理会 classify 的结果）今天就能自动安装，底线只在名义上存在。还有一个问题：自动安装前跑的是候选版本自带的 selftest，候选版本可以连测试一起改掉。目前队列里没有待装的版本，暂时没有实际风险，1.5.9 一并修掉。

**1.5.9 的设计（取代上一轮那三条）：**

1. **底线集中到 `wflib/floor.py`。** STOP_FLOOR、DIFF_FLOOR、GUARDRAIL、META 和一组固定的底线测试用例都放进这个文件，policy.py 改为从它导入，原有名字保持可用。`GUARD_FILES` 缩成只有 floor.py 一个文件。只有改动 floor.py 的版本才停下等用户，其余版本全部自动安装。
2. **用已安装的代码去测候选版本的行为。** 测试用例来自已安装的 floor.py，在隔离目录里对候选版本跑三组断言：
   - 候选的 classify 对资金、密钥、合规、实盘四类命令都判为需要用户。
   - 候选的驱动器在假通道下遇到这些命令会停下。
   - 候选的 `self_update(auto=True)` 会拒绝一个改动了 floor.py 的测试包。

   第三组用来堵「先削弱检查、再改底线」的两步绕过。我要说清楚它的边界：这组测试拦得住意外的回归，拦不住刻意写来躲测试的代码。所以 floor.py 的改动仍然要人确认。一旦另一个模型系列审查上线，它是这之外的又一层。
3. **通知改为面向执行端。** 工具在每次 tick 之后写 `local/inbox.json`，每条记录带 `for: executor | user`、原因、证据和建议动作；新增只读命令 `wf inbox`。执行端配 WorkBuddy 的定时自动化去读它。阻塞、NO_REF、通道故障、手动步骤，都标为 `for: executor`。只有四道底线标为 `for: user`，由执行端转告用户，不弹 macOS 通知。
4. **唤醒间隔写进配置。** `wf schedule plist` 改为从配置读取间隔，不再写死 300 秒。

**你问「从 1.5.9 起 self-update 是否不再需要执行端」。** 答案是：1.5.9 本身还要手动装最后一次，因为它改了 policy.py 和 release.py，1.5.8 会按设计拒绝自动安装它。手动安装的授权依据是用户已经用 wf said 记下的「护栏也全自动」。1.5.9 装好之后，不改 floor.py 的版本由驱动器自己构建、安装、核对，全程无人参与；构建或自检失败时，写进 inbox 交给执行端。

写构建器需要锚点原文，本轮读取下面这些位置。

【本机执行】
```
cat /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/asks.py
grep -n classify /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
grep -n notify /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
grep -n plist /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
grep -n self_update /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
grep -n v158 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_v15.py
```

【本轮回报清单】
1. asks.py 全文。
2. driver.py 中 classify 的调用位置。
3. wf.py 中 notify 的调用位置。
4. wf.py 中 schedule plist 的位置。
5. wf.py 中 self_update 的位置。
6. selftest_v15.py 中 1.5.8 测试的注册位置。

【需要用户决定】
无。

【下一轮建议档位】
high

【驱动指令】
本轮之后：回报