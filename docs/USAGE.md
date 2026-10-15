# USAGE.md —— collab-wf 调用手册

## 1. 安装（每台机器一次）

```bash
cd ~/.workbuddy-ai/tools                 # 或任何你想放工具的目录
unzip -q /路径/collab-wf-1.0.0.zip       # 得到 collab-wf/
python3 collab-wf/wf.py selftest         # 端到端自检，应全部通过
python3 collab-wf/wf.py self-init        # 初始化工具自己的版本库，打标签 wf-v1.0.0
echo 'export PATH="$HOME/.workbuddy-ai/tools/collab-wf/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
wf version
```

用 PATH 而不是 alias。wf exec 运行命令时会自动把工具的 bin 目录放到 PATH 最前面，所以指令里的 wf 命令在 wf exec 中可以原样执行，不依赖 shell 配置文件。不要把 bin/wf 做成符号链接。
工具只用 Python 标准库（3.7 及以上即可），不依赖项目的虚拟环境，也不访问网络。
机器本地数据（已接入项目的注册表、看板进程信息）放在 collab-wf/local/，不进工具的版本库，回滚工具不会影响它。

## 2. 起一个项目

新项目：
```bash
wf init --name my-project --root ~/work/my-project
```
会生成 PROJECT.md（固定九章模板）、CHANGELOG.md、workflow/wf.json，并登记到注册表。

接入已有项目（例如 btc-quant）：
```bash
cd ~/work/btc-quant
wf init --adopt --turn 12 --state EXECUTE      # 指明接入时的轮次与所在步骤
```
接入不会回填历史事件（只记录实测），事件流从接入那一刻开始。

项目配置在 workflow/wf.json：`protect` 追加落地时不许覆盖的路径；`processes` 声明需要长期运行的进程，wf doctor、看板与 wf apply 会用到：
```json
{"name": "模拟盘", "pidfile": "state/paper.pid", "match": "run_live.py",
 "code_paths": ["run_live.py", "config.yaml", "core/", "strategies/"],
 "start": "bash tools/start_paper.sh", "restart": "bash tools/stop_paper.sh && bash tools/start_paper.sh"}
```

## 3. 自动驱动（推荐）

完整说明见 DRIVER.md，适配器约定见 TRANSPORT.md。启用步骤：

```bash
wf transport && wf transport test              # 检查 local/transport.json；黑盒一致性检查
wf policy                                      # 查看待批准的白名单
wf policy approve --note "已审阅"               # 用户本人执行
wf goals approve --note "已审阅"                # 用户本人执行
wf schedule plist > ~/Library/LaunchAgents/local.collab-wf.tick.plist   # 系统定时器（见 DRIVER.md 第 7 节）
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.collab-wf.tick.plist
wf kick --message 任务文件                      # 发起（或等计划节点到期）
wf explain                                     # 随时：卡在哪、为什么、需要谁做什么
wf decisions --tsv                             # 决策日志
wf effort                                      # 档位规则与复盘
wf conv                                        # 当前对话体量
```

总设计与每条需求的落实情况见 DESIGN.md。

下面第 4 节是手动做法：驱动器暂停（wf pause）或未启用时使用。

## 4. 一轮怎么走（手动）

在项目目录里运行（或在任意目录加 `--project 名称`，放在子命令之前：`wf --project btc-quant status`）。

```bash
wf status                                   # 随时：在哪一步、该做什么、怎样算完成
wf report                                   # 上一轮执行完 → 生成回报草稿
wf check-outgoing workflow/outbox/turn-013.md --kind report
#（桥接工具发送成功后）
wf mark sent --message-file workflow/outbox/turn-013.md --kind report --effort high --model claude-opus-5-5
#（桥接工具等到回复结束，把回复全文存成文件后）
wf mark replied --reply-file /tmp/reply-013.md
wf intake /tmp/reply-013.md
wf review
wf review --accept
wf apply ~/Downloads/btc-quant-v1.0.zip     # 本轮有交付版本时
wf exec -- 'bash tools/status.sh | head -1' # 含管道、重定向、&& 的命令整体用单引号括起来
wf verify list
wf verify set 1 pass --evidence c01
wf verify set 2 fail --evidence c02 --note "退出码 1"
wf note --kind decision "是否……"
wf report                                   # 回到第一步
```

计划节点的顺序：本轮需要回报时，先发回报，等 Claude 回复后在 REVIEW 之后执行 wf wait；本轮不需要回报时（全部通过），验收后直接 wf wait。消息发出之后、回复到达之前不能设置计划节点，因为下一步取决于回复。

wf exec 在项目根目录运行命令，并自动启用项目的 .venv（等同于 source .venv/bin/activate），所以指令中的 `cd 项目目录` 与 `source .venv/bin/activate` 不需要单独执行。每条命令是一个独立的 shell，cd 不会延续到下一条。

## 5. 桥接工具的接入点与行为约定

通道操作（发送、等待、读取、下载）由执行端自己的桥接工具完成，wf 不访问通道。桥接工具只需在下面几处调用 wf，记账就全部自动完成：

| 时机 | 调用 |
|---|---|
| 通道接受消息（拿到生成已开始的凭据） | `wf mark sent --message-file F --kind K --effort E --model M` |
| 回复结束、全文已存为文件 | `wf mark replied --reply-file F [--tools-json '[{"name":…,"summary":…}]']` |
| 查询额度之后 | 覆盖写入 workflow/usage.json（格式见下） |
| 发送被拒、网络错误等 | `wf fail --reason "…" --symptom SD-1`（达到上限自动阻塞） |
| 额度用尽 | `wf go QUOTA_WAIT` |

桥接工具必须遵守的行为约定（与通道实现无关）：

1. 同一时间只有一条消息在途。
2. 有一个明确的“正在生成”判据，并以它为准判断等待与结束；“读不到消息”不等于“消息丢失”，不得据此重发（F14）。
3. 不得中止进行中的生成。只有连续 10 分钟被拒、且没有任何生成迹象时，才可以按孤儿锁处理，并记 wf fail。
4. 只记录实测值：usage.json 写通道返回的原值，查不到就不写。
5. 不在任何消息、日志、项目仓库中存放凭据；桥接工具的配置放在项目之外。
6. 自己的每个动作（发送、等待、重试、中止、下载）记入本地日志，出问题时可以对照事件流复盘。

桥接工具的本地日志使用 JSONL，每个动作一行，wf 可以据此核对第 1、3、4、5、6 条约定以及“不中止生成”：
```json
{"ts": "2026-10-10T08:00:00+00:00", "action": "send", "turn": 14, "detail": "…"}
```
action 取值：send（发出）、accepted（通道接受）、rejected（被拒，例如环境被占用）、generating（观察到正在生成）、done（回复结束）、retry、abort（中止）、download、quota（可带 value，为通道原值）、error。
每轮收尾运行一次：
```bash
wf exec -- 'wf bridge-check --log ~/.workbuddy-ai/tools/bridge.log.jsonl --hours 24'
```
它只读这份本地日志，不访问通道。

usage.json 格式：
```json
{"ts": "2026-10-10T08:00:00+00:00",
 "limits": [{"name": "session", "label": "5 小时窗口", "used_pct": 33, "resets_at": "2026-10-10T12:30:00+00:00"},
            {"name": "weekly_all", "label": "7 天窗口", "used_pct": 40, "resets_at": "2026-10-13T08:00:00+00:00"}]}
```

## 6. 看板

```bash
wf serve --daemon          # 后台启动（默认端口 8765，只监听本机）
wf serve --status
wf serve --stop
```
- http://127.0.0.1:8765/ 工作流看板：运行图（当前步骤亮灯）、现在要做什么、每步定义与异常处置、轮次时间线、本轮执行、事件流、各步统计。每 3 秒刷新。
- http://127.0.0.1:8765/project.html?p=<项目> 项目视图：项目状态、下一步、项目进程、对话记录、轮次明细、失败台账、文档。

一个看板服务覆盖所有已接入的项目，顶部下拉切换。

## 7. 出了问题

```bash
wf status                 # 当前步骤；超时、失败或阻塞时会列出该步的已知异常与处置
wf step SEND              # 查看任一步的完整定义
wf doctor                 # 健康检查：版本、PROJECT.md、工作区、步骤超时、项目进程、额度数据
wf unblock --to SEND --note "已排除：……"
wf resume --note "用户决定：……"
wf force EXECUTE --reason "……"     # 状态记错时人工更正（会被标记）
```

## 8. 版本

```bash
wf version                            # 工具版本、可回滚的版本、当前项目固定的版本
wf self-update collab-wf-1.1.0.zip    # 先运行新版本的自检，通过后安装并打标签
wf self-rollback 1.0.0
wf pin                                # 读过工具 CHANGELOG 后，把当前项目固定到新版本
wf rollback --to v0.8                 # 项目回滚（生成新提交，不改写历史）
```

## 9. 开新对话

```bash
wf bootstrap --reason "对话过长，回复开始遗忘早期约定"
```
生成 workflow/outbox/bootstrap-*.md（说明消息，含 WORKFLOW.md 与 PROJECT.md 全文）和仓库 zip。在新对话中上传 zip、发送说明消息即可续上。

## 10. 让 Claude 拿到项目文件

Claude 每轮在自己的容器里保留一份交付的代码副本；通道不支持执行端上传文件，所以靠指纹确认两边一致：

- wf report 生成的回报草稿末尾自动附上“仓库指纹”（只覆盖 Claude 交付的文件，排除接入配置与运行数据）。Claude 用同样的规则计算自己的副本，一致就说明两边同步。
- 不一致时，Claude 会在回复中列出要看的文件，执行端运行 `wf snapshot 文件1,文件2 --out /tmp/pack.md` 生成有界的文件包（默认上限 40000 字符，拒绝 .env 与运行数据，含疑似凭据时拒绝生成），贴进下一条消息。
- 需要比对到单个文件时：`wf fingerprint --files`。
- 开新对话时，由用户把 wf bootstrap 生成的 zip 拖进对话框（每个新对话一次）。

## 11. 命令速查

`wf help` 列出全部命令。
