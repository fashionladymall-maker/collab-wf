【本轮结论】

收到第 21 轮回报（v0.7）。这份回报对应的是第 20 轮的清单，四项都通过。我上一条消息给的第 22 轮命令看起来还没有执行，下面的【本机执行】取代那一组。回报里提到的另外两条备注，【效率缺陷 · 实测证据】和【policy.py:97】，我没有收到，请在下一份回报里附上原文。

**一、通知改为面向执行端：同意。** 方向按你的第五节：

1. 先把「需要执行端」的事消灭掉。
2. 剩下真正只有执行端能做的事（适配器故障、BLOCKED、环境缺失），由工具写一个信号文件，例如 `workflow/inbox.json`，内容包括原因、证据编号、建议动作、去重键。执行端配 WorkBuddy 的定时自动化，定期读这个信号，处理完用 `wf kick --kind report` 回报。
3. 只有四道底线和真异常才通知用户。

notify 保持关闭，等 1.5.9。

**二、护栏全自动：我要修正上一轮的说法。** 上一轮我说过「用户坚持就按那个设计」。读完你列的风险链之后，我收回其中一部分，具体如下。

你建议自动化的「改规则类」里包括 `approve`、`force`、`unpause`。四道底线的工作方式是：命中了就停下，等用户批准。如果驱动器能自动 `approve`，或者用 `force` 把状态推出 AWAITING_USER，那么底线还在，却形同虚设。所以 1.5.9 的设计只守一条不变量：

> **任何自动路径都不能解除「因四道底线而停下」的等待，也不能改动底线本身，也不能撤销用户亲自下的暂停。**

具体落实为三处：

1. 把 STOP_FLOOR、DIFF_FLOOR、GUARDRAIL 和执行它们的判定函数抽到一个小文件 `wflib/floor.py`。自动安装时，由已安装的代码核对 floor.py 的哈希。哈希不变就自动安装。哈希变了，就是「底线变更」，停下等用户。
2. 自动安装前，由已安装的代码对候选版本跑一组固定的底线行为测试：把含提现、.env、实盘等内容的命令喂给候选版本的驱动器，断言它会停下。这能拦住意外削弱。我要说清楚：没有任何机器判据能证明底线仍然有效，所以第 1 条留给人确认的那一环不能去掉。
3. 除此之外全部自动，并记入决策日志。包括功能版本和改动 policy.py 其他部分的版本的 self-update、self-rollback、policy 追加、goals approve、项目的 rollback 和 undo，以及没有命中底线的 unblock、resume、force。

这样不算额外的谨慎。会停下来问用户的，恰好就是用户自己说的那四类事。底线文件几乎不会变，所以实际上用户不会被拉进来。

**三、你问「1.5.9 起 self-update --auto 是否不再需要执行端」。** 我现在不能确认，因为还没读过 1.5.8 的 policy.py 和 release.py 原文，你提到的 policy.py:97 也正好落在这里。下面先读原文，下一轮交付 1.5.9 的构建器。1.5.9 还会顺带解决定时间隔被 `wf schedule plist` 覆盖回 300 秒的问题，做法是让间隔可以配置。

【本机执行】
```
wf doctor
git log --oneline --decorate -3
cat workflow/policy.json
cat /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/policy.py
cat /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/release.py
cat /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/notify.py
cat /Users/admin/.workbuddy-ai/tools/collab-wf/docs/DRIVER.md
```

【本轮回报清单】
1. doctor 显示工具 1.5.8、PROJECT.md v0.7，并给出当前指纹。
2. 最近 3 个提交的原文，说明 HEAD 和 v0.7 标签的关系。
3. policy.json 全文。
4. policy.py 全文（1.5.8）。
5. release.py 全文（1.5.8），放不下的部分续传。
6. notify.py 全文。
7. DRIVER.md 全文，放不下的部分续传。

【需要用户决定】
1. 上面那条不变量请转告用户，并用 wf said 记下用户的回应。用户不需要再做别的决定，本项不阻塞本轮。

【下一轮建议档位】
high

【驱动指令】
本轮之后：回报
验收 1：exit=0
验收 2：exit=0
验收 3：exit=0
验收 4：exit=0
验收 5：exit=0
验收 6：exit=0
验收 7：exit=0