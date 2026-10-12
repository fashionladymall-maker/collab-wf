[工具 bash_tool]
[工具 present_files]
【本轮结论】

**这是第 90 轮回报的重复送达。** 正文与上一条相同，只在 ② 末尾多了一段“本轮关键终端原文（截取）”。我上一条回复已经答复过第 90 轮，并交付了 build_wf_170.py。

按 W47 的做法，这条回复与上一条完全一致：同一个构建器（字节未变），同样的命令、清单和驱动指令。两条回复都落地时，用哪一条结果都一样，不会出现两套不同的代码。

- 构建器：build_wf_170.py，32698 字节，sha256 d5c7f7e0fa51df246197dff99f778a87038e35830c2b9ce4e4336a29f156ca71。
- 内容：工具 1.7.0，多项目隔离第一阶段 S1。我这边自检 241/241；用 1.6.9 自动安装，底线行为检查通过；floor.py 没有改动。
- 第 90 轮的五条接口意见（A）–（E）全部采纳，具体见上一条回复与构建器里的 TRANSPORT.md 附录、DRIVER.md 第 27 节。

**关于新增的那段“终端原文”**

它不是逐字的终端输出，更像是整理后的转述：
- make_r5.py 必须带 `--in` 和 `--out` 两个参数，不带参数运行会直接报错。
- `wf goals` 的实际格式是“目标进度 7 / 7”，不是“7 / 7 达成”。
- `ops.fingerprint()` 返回的是一个字典，打印出来不会是“0c183e4965457761  10 files”。

所说的事实本身我已独立核对过：公开同步仓库里 v2.8 已落地，指纹为 0c183e4965457761。所以这段不影响结论。

按决策原则第 3 条（回报给原文，不只给转述），以后请：
- 手写回报时，用 `wf exec` 跑命令，引用 evidence 里的原文；
- 或者把内容明确标成“转述”。

【本机执行】
```
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-090/delivered/build_wf_170.py --out workflow/outbox
wf self-update --auto workflow/outbox/collab-wf-1.7.0.zip
wf version
wf self-check
wf fingerprint
wf goals
```

【本轮回报清单】
1. 构建通过，自检 241/241，成员集合一致，floor.py 未改动；记下版本包的 sha256。
2. 驱动器自动安装 1.7.0，升级核验通过；审查结论以原文为准（应能看到 1.6.9 新增的“开始等独立审查”与心跳行）。
3. 工具版本为 1.7.0。
4. self-check 完全一致。
5. 仓库指纹仍为 0c183e4965457761（本轮不落地项目版本）。
6. `wf goals` 原文，应为 7/7。

【需要用户决定】
1. 执行端目录下明文凭据候选文件（含一个权限过宽的旧备份）的处置：删除、收紧权限或保留。由你本人决定。本项不阻塞。
2. M1 最终验收：只认你本人的原话。验收之后，建议把 I1–I8 定为 M2。本项不阻塞。
3. 两件事都需要你一句话：一是是否授权执行端解除它自己发起的暂停（你发起的暂停仍只认你本人）；二是 amy-local 的暂停 6X2 是否解除。本项不阻塞。

【下一轮建议档位】
high

【驱动指令】
发布：无
执行端 1：1.7.0 装好后运行 wf pin（项目固定版本 1.6 → 1.7，WORKFLOW 第 11 节），再记下 wf doctor 里“版本匹配”那一行，用 wf task done 写下
执行端 2：S1 迁移，按以下顺序做：① 适配器按 TRANSPORT.md 附录“1.7.0”实现（选对话顺序 --conv > projects[--project] > --prev-ref 所属对话 > 默认对话，--prev-ref 只给定位下界；conv_new --project 只改该项目、不改默认对话；两种告警），不认识的参数不报错；② 适配器配置写入 projects.collab-wf-dev = 当前对话；③ 运行 wf conv bind --conv <当前对话> --note "迁移到按项目绑定"；④ transport.json 加 "per_project_conv": true；⑤ 运行 wf transport test。各步结果与第 ⑤ 步原文写进 wf task done
执行端 3：I1 实测（在执行端 2 之后做；amy-local 的暂停与 auto 都不动）：用适配器 conv_new --project amy-local 为 amy-local 建一个对话，核对适配器配置里 collab-wf-dev 的条目不变，并在 collab-wf-dev 下一次发送的动作日志里确认用的是它自己的对话、没有 conv_from_default 告警。结果写进 wf task done
之后：等执行端
验收 1：c1 exit=0 包含 "构建通过：collab-wf 1.7.0，自检 241/241"
验收 2：c2 exit=0 包含 "升级核验通过"
验收 3：c3 exit=0 包含 "collab-wf 1.7.0"
验收 4：c4 exit=0 包含 "完全一致"
验收 5：c5 exit=0 包含 "0c183e4965457761"
验收 6：c6 exit=0 包含 "目标进度 7 / 7"
指纹：0c183e4965457761
超时：1800