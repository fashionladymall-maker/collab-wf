[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
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

**作者实际交付（本轮只有这一个文件）**

collab-wf-dev-v2.7.zip：167832 字节，sha256 `04e9528945dcd1d412df3688e942c38a43f466aaa3dae26bacccc8e638aeeb12`。我在作者容器里核对过：文件头 `50 4b 03 04`，`is_zipfile=True`，`testzip()=None`。包是确定性打包的：不压缩，时间戳固定。

| 成员 | 字节 | sha256 |
|---|---|---|
| collab-wf-dev-v2.7/（目录） | 0 | — |
| CHANGELOG.md | 10837 | 9a2e48984df4ca02f0c4c2071818f6cad96c08ddac18ecfa21637be40d3de62e |
| PROJECT.md | 67183 | be9af088e71f48b9c5afc972fa606fbfd42880a0c740c969efa056fd50da0642 |
| build_wf_168.py | 50092 | d096c798f9e308355778c594c292e8d1be6d7ef569a59f63291ecb76adde2ad7 |
| papertrade/make_r5.py | 19964 | 708e7dd49640cd018f709a33921639e48738308a5c757b9c93d808ed62566874 |
| papertrade/pt5_regress.js | 18884 | f0d3cec46bc7f4a9cf75c1a316b84384c52d2c1dad421095dbf6537583d239ca |

- 包里没有 .git、local/、locks、`__pycache__`、*.pyc；用同步密钥扫描规则扫过，0 命中。
- 按你的实测（每个对话只暴露一个产物），1.6.8 构建器和 papertrade 两个脚本都放进了这一个项目版本包。落地后构建器在项目根，`python3 build_wf_168.py --out workflow/outbox` 符合现有白名单，我已逐条核对过本轮 6 条命令都在白名单内。
- 我在副本项目上演练过：评审规则没有发现需要用户批准的项；`apply_zip` 落地成功；落地后指纹应为 **56c0dd71ea3e8bd7**（10 个文件）。在项目根运行构建器，得到“构建通过：1.6.8，自检 233/233”。
- 第 84 轮声明的 ca141fff…（构建器）、2d7cd24d…（v2.7）、25878c42…（pack_v27.py）都没有到达本机，全部作废。pack_v27.py 本轮不再单独交付，核对改用下面的只读命令。

**未完成与无法验证的项**

- **1.6.8**：只在作者容器里构建并模拟了自动安装（233/233，63 个文件一致，self-check 完全一致），本机还没跑。
- **P1-C**：仍缺 r4 原件，本机没有运行过生成器和回归。
- **P0-A**：DONE 下的真实闭环仍未检验。
- **P1-D**：实景演练要在 1.6.8 装上之后做。
- **P2-F 与多项目隔离**：都还没有设计。
- **单文件通道是否可靠**：如果这一个文件仍然被换成旧的产物，驱动器会在解包时停下，不会落地。是否开新对话由执行端判断，属于适配器与通道的事，我不审读。

**本轮代码与文档**

1.6.8 构建器在 1.6.7 基线上：

- **验收失败不再被吞进 DONE**：目标达成但本轮有失败时，照常回报，不进入 DONE。
- **P2-E 墙钟计时**：新增 `landed_timeout_s` 和 `landed_min_polls`；设了就按距发送的实际时间判定 SEND_NOT_LANDED，不设则保留旧的次数判定。仍不自动重发。
- **bridge-check R3 飞行宽限**：发送后还没结果时，在宽限内（默认 900 秒，可用 `--grace-s` 调）不报；超过宽限照常报；结果出现前又有新的发送一律报。附时钟、终态、新发送和过滤窗口的边界测试。
- **构建器**：自检在另一个副本上跑；打包时过滤 local/、.git、`__pycache__`、*.pyc，并核对成员集合。
- **P1-D**：新增 `wf drill review`，配 docs/DRILL.md。审查用真实程序，替身只替代 Claude 通道。
- **本轮新增：同步暂停提示**：标题按原因区分，“版本包不可读（不是凭据问题）”和“发现疑似密钥”分开写；key 改成绑定命中原因的指纹，同一个原因不再每次 tick 生成新 key。
- **本轮新增：SEND 停留（即你提的决策点）**：发送意图仍在途、结果不明时，SEND 的停留上限放宽到 SEND_UNKNOWN_WAIT 的退避之和加 5 分钟（约 62 分钟）；没有在途意图时仍按 15 分钟报。看门狗原有的 SEND 60 分钟阈值不变。
- **作者容器自测**：自检 233/233，新增 17 项。新用例在 1.6.7 上会失败，说明测到了改动。本版不改 floor.py。

v2.7 文档在原有内容基础上补记了第 84、85 轮：

- W48 更新为你的实测结论：通道每个对话只暴露一个产物。
- 新增 W49：第 84 轮消息迟到可见被误判为未落地、同步提示误报、SEND 停留误报。
- 多项目并行与全链路隔离作为新需求写进“下一步”，等我出方案。

**执行端只读核验命令（REVIEW 阶段、落地之前，由作为审查者的执行端运行）**

```
shasum -a 256 workflow/evidence/turn-085/delivered/collab-wf-dev-v2.7.zip
head -c 4 workflow/evidence/turn-085/delivered/collab-wf-dev-v2.7.zip | xxd
python3 -c "import zipfile,hashlib,sys;p=sys.argv[1];z=zipfile.ZipFile(p);print(zipfile.is_zipfile(p),z.testzip());[print(i.filename,i.file_size,hashlib.sha256(z.read(i)).hexdigest()) for i in z.infolist()]" workflow/evidence/turn-085/delivered/collab-wf-dev-v2.7.zip
```

**之后由执行端执行的验收顺序**

1. 驱动器取回这个文件后进入 REVIEW。执行端作为审查者，先运行上面三条命令，与上表逐项比对：大小、sha256、文件头、`is_zipfile`、`testzip`、6 个成员和各自的 sha256。任何一项不符就判 block：驱动器在落地前停下，坏文件保留，不重建、不覆盖、不删除。
2. 全部一致后，审查给出结论，驱动器落地 v2.7，再执行【本机执行】里的 6 条命令。
3. 验收由内存里的 1.6.7 完成。1.6.7 仍会把失败吞进 DONE，所以执行端 1 要单独核对 verify.json。
4. 进入 DONE 后，按执行端 1、2、3 的顺序完成待办，最后用 `wf kick` 发出下一条消息。可以把你准备好的多项目隔离设计包和三项结果合在同一条消息里。
5. 下一轮我先读多项目隔离的需求梳理，给出设计方案，暂不写代码；同时安排 P1-D 实景演练，并发布 policy 里加 `landed_timeout_s` 的项目版本。

【本机执行】
```
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 build_wf_168.py --out workflow/outbox
wf self-update --auto workflow/outbox/collab-wf-1.6.8.zip
wf version
wf self-check
wf fingerprint
wf goals
```

【本轮回报清单】
1. 构建通过，自检 233/233，成员集合核对一致。
2. 驱动器自动安装 1.6.8，升级核验通过。
3. 工具版本为 1.6.8。
4. self-check 完全一致。
5. v2.7 落地后，仓库指纹为 56c0dd71ea3e8bd7（10 个文件）。
6. `wf goals` 原文，应为 7/7。

【需要用户决定】
1. 执行端目录下明文凭据候选文件（含一个权限过宽的旧备份）的处置：删除、收紧权限或保留。由你本人决定，执行端不代办。本项不阻塞本轮。
2. M1 最终验收：只认你本人的原话。本项不阻塞本轮。

【下一轮建议档位】
high

【驱动指令】
发布：collab-wf-dev-v2.7.zip
执行端 1：核对本轮 workflow/evidence/turn-085/verify.json 六项是否全部 pass（内存里的 1.6.7 会把失败吞进 DONE），记下 c01、c02 的末行与本机 1.6.8 版本包的 sha256，以及 REVIEW 阶段只读核验的结论，用 wf task done T85-1 --note 写下
执行端 2：P1-C：查找 r4 原件（sha256 1d580864a913f907d6f6c1aacd78d61a13db11976af47c13eb8f3fc995f8794f，72438 字节），先看 workflow/evidence/turn-079/delivered/；找到就运行 python3 papertrade/make_r5.py --in <r4> --out <同目录>/papertrade-business-research-20261011-r5.html，再运行 node papertrade/pt5_regress.js <r5> <r4>，并做真实浏览器桌面与 390 宽度检查；找不到就如实写“未找到”。结果用 wf task done T85-2 --note 写下
执行端 3：P0-A 实景（最后做）：先在 T85-3 未完成时记下 wf status（应为第 85 轮 DONE）、wf task list、local/inbox.json 里的 task:T85-3（应带 action）、wf explain（应写“需要：执行端”）；然后 wf task done T85-3 --note "P0-A 实景"，再记下 done:85 的 why 是否附上三项结果、状态是否仍为 DONE；最后用 wf kick 把三项结果与多项目隔离设计包合成一条任务消息发出
之后：等执行端
验收 1：c1 exit=0 包含 "构建通过"
验收 2：c2 exit=0 包含 "升级核验通过"
验收 3：c3 exit=0 包含 "collab-wf 1.6.8"
验收 4：c4 exit=0 包含 "完全一致"
验收 5：c5 exit=0 包含 "56c0dd71ea3e8bd7"
验收 6：c6 exit=0 包含 "目标进度 7 / 7"
指纹：56c0dd71ea3e8bd7
超时：1800