[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 present_files]
【本轮结论】

**第 95 轮核对**
- v3.0 已落地，仓库指纹 e5ce640d3914fd82，与预期一致。工具版本 1.7.3，目标 7/7。
- 三版工具（1.7.1、1.7.2、1.7.3）的事后补审结论都是 pass，没有 block，不需要回滚。W55 视为已缓解。
- `review_key` 开关原先没打开，所以工具没有传 `--key`，审查程序里按键命名的那条路径实际一直没有走到。执行端这一轮补上了，处理正确。

**两点裁定（执行端请我定的）**

1. **在场判定改成两档，同意。**
   - T88-2 里的“10 秒内退出”，针对的是“确实没有执行端”这一种情况。执行端的修法保留了这一档：确实没有会话进程时，3 秒左右返回 no_session。
   - “有会话进程、但暂时连不上”是 N15 揭出来的另一种情况，不应该当作“没人”。对这种情况最多再等 90 秒，仍在外层 review_timeout_s（300 秒）之内，与第 86 轮空等 716 秒是两回事。
   - 这条已写进 DRIVER.md 第 31 节。
2. **迟到的结论（请求已按“未审查”关闭之后才写出来的），不需要猜路径。**
   - `local/reviews/<键>.late.*.json` 这份留档保持不变。
   - 项目版本的迟到结论：用 `wf --project <项目> note --kind info "迟到的审查结论：…"`。由工具解析当前轮次目录，写进该项目当前这一轮，下一份回报自然会带上。
   - 工具版本的迟到结论：用 `wf signal add --key review-late:<键>` 写进 inbox，再由执行端在下一份回报里写明。
   - 迟到的结论一律不算“已审查”，也不改变已经做出的处置。

**本轮交付：build_wf_174.py（工具 1.7.4）**

处理补审里的三条质量意见：
- `review_key_args` 改为显式的 `kind` 参数。键的格式不变，执行端审查程序不用改。
- plist 里的项目名按 XML 转义。项目名里含 `&` 或 `<` 时，生成的配置仍然合法。
- `schedule status` 只读一次 schedule.json。
- 另外把上面的两点裁定写进了 DRIVER.md 第 31 节。

“帮助文本与自检依赖中文提示串”这一条不改：自检核对提示文字是有意为之，目的就是让文案改动时能被发现。

文件 12443 字节，sha256 c43c5316f23728a29456f2734b34ebdc4072f50446259addce05e561e4f0bd2b。我在与已装版本相同的 1.7.3 上验证过：构建 246/246，自动安装后升级核验通过，self-check 完全一致，floor.py 没有改动。

这次安装也是两项改动的第一次实景检验：一是 `--key collab-wf.tool.collab-wf-1.7.4` 的审查路径，二是 N15 的修法。

**唤醒通道持续返回 429**
这是执行端唤醒通道的限流，属于执行端的范围。这一轮靠兜底巡检接住了，五项待办全部完成。

【本机执行】
```
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-095/delivered/build_wf_174.py --out workflow/outbox
wf self-update --auto workflow/outbox/collab-wf-1.7.4.zip
wf version
wf self-check
wf fingerprint
wf goals
```

【本轮回报清单】
1. 构建通过，自检 246/246，成员集合一致，floor.py 未改动；记下版本包的 sha256。
2. 驱动器自动安装 1.7.4，升级核验通过；审查结论或“未审查”的原因以原文为准。
3. 工具版本为 1.7.4。
4. self-check 完全一致。
5. 仓库指纹仍为 e5ce640d3914fd82（本轮不落地项目版本）。
6. `wf goals` 原文，应为 7/7。

【需要用户决定】
1. 执行端目录下的明文凭据候选文件（其中有一个权限过宽的旧备份），删除、收紧权限还是保留，由你本人决定。本项不阻塞。
2. M1 最终验收，只认你本人的原话。验收之后，建议把 I1–I8 定为 M2。本项不阻塞。
3. 两件事都需要你一句话：一是是否授权执行端解除它自己发起的暂停（你发起的暂停仍只认你本人）；二是 amy-local 的暂停 6X2 是否解除。本项不阻塞。

【下一轮建议档位】
high

【驱动指令】
发布：无
执行端 1：1.7.4 安装时的审查实景：从 local/reviews/ 里找出这次请求所用的键（应为 collab-wf.tool.collab-wf-1.7.4）、请求状态与原因（pass、concerns、block，或 no_session、no_session_unreachable、timeout），逐字写进 wf task done；如果按“未审查”放行了，按 REVIEW.md 第 3 节事后补审 collab-wf-1.7.4.diff，结论一并写进去
执行端 2：做完后先用 wf note --kind info 写说明，再用 wf kick --kind report 发回报
之后：等执行端
验收 1：c1 exit=0 包含 "构建通过：collab-wf 1.7.4，自检 246/246"
验收 2：c2 exit=0 包含 "升级核验通过"
验收 3：c3 exit=0 包含 "collab-wf 1.7.4"
验收 4：c4 exit=0 包含 "完全一致"
验收 5：c5 exit=0 包含 "e5ce640d3914fd82"
验收 6：c6 exit=0 包含 "目标进度 7 / 7"
指纹：e5ce640d3914fd82
超时：1800