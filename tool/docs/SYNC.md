# SYNC.md —— 项目上下文同步（1.6.0）

用户第 29–31 轮的要求：项目自动同步到一个公开仓库，作为本项目的上下文载体，Claude 按链接就能读到最新的版本、进度与工具源码，不需要人贴文件。

## 1. 分工

- 工具决定“放什么、能不能放”：按白名单收集内容、生成 STATUS.md 与 README.md、推送前做密钥扫描、内容没变就不推送。
- 执行端的同步程序只负责推送（例如经 GitHub REST API）。凭据只在同步程序那边，工具不接触。

## 2. 配置（local/transport.json，执行端维护）

```json
"sync": "/Users/…/executor/gh_sync.py",
"sync_projects": ["collab-wf-dev"],
"sync_timeout_s": 300
```

1.7.6：按项目分仓库时再加 `"sync_repos": {"collab-wf-dev": "owner/collab-wf", "amy-local": "owner/amy-local"}`。有这一项时，每个项目只推到自己的仓库，同步程序多收到一个 `--repo owner/仓库` 参数；不在其中的项目不同步。仓库的创建与公开或私有由用户决定。

sync 写程序路径时，无论通道是不是模块方式，都直接运行该程序（1.6.2 起；此前模块方式会把 sync 交给通道适配器，报“未知动作：sync”）；写 true 时由模块方式的适配器按子命令 sync 接收。只有列在 sync_projects 里的项目会同步。

## 3. 何时同步

wf tick 每次推进完、写 inbox 之前，对开启了同步的项目各做一次：收集 → 扫描 → 与上次成功推送的内容摘要比较 → 有变化才调用同步程序。也可以手动：`wf sync`（`--dry-run` 只收集与扫描、不推送；`--force` 内容没变也推送）。

## 4. 放什么

| 仓库内路径 | 内容 |
|---|---|
| STATUS.md | 工具生成：版本、轮次与状态、最近一轮的结果、目标进度、待办、最新产物 |
| README.md | 工具生成：怎么读、边界 |
| project/ | 项目的已提交文件，与仓库指纹同口径（不含 workflow/wf.json、evidence、outbox、*.jsonl、approvals.json 等） |
| tool/ | 当前安装的工具源码（版本清单内的全部文件） |
| turns/turn-NNN.md | 已发出的本方消息（turns.jsonl 记为 sent 的轮次）；没发出的草稿不放 |
| replies/reply-NNN.md | Claude 的回复 |
| artifacts/ | 最新的工具版本包（outbox）与最新的项目版本包（Claude 交付的） |

不放：local/（通道配置、会话凭据、页面快照）、workflow/evidence/ 的命令原文与证据、approvals.json、状态与事件文件。

## 5. 密钥扫描

推送前对全部内容跑发送前检查用的密钥扫描（ops.SECRET_PATTERNS），zip 内的每个文本成员也扫。命中任何一处：整次不推送，记在 local/sync/<项目>.json，wf ask 与 inbox 出现“项目同步已暂停：发现疑似密钥”（交给执行端；涉及密钥的事执行端先问用户）。查明来源、移除后，下一次 wf tick 或 wf sync 会自动恢复。

## 6. 同步程序的接口

```
sync --project <名称> --turn <轮次> --dir <暂存目录>
```

- 把仓库更新成与暂存目录一致：新增、修改、删除（暂存目录之外的文件，同步程序可以保留它自己的源码，例如 scripts/，其余应删除）。
- 成功时退出码 0，标准输出最后一行可以是 JSON：`{"commit": "<提交号>"}`。
- 失败时退出码非 0；工具记下失败次数，连续 3 次在 wf ask 与 inbox 中提示执行端。
- 环境变量 WF_ADAPTER_TIMEOUT_S 给出时间上限。

## 7. 开新对话的短消息（1.6.3）

transport.json 再写 "sync_repo": "owner/仓库"（可选 "sync_branch"，默认 main）时，wf bootstrap 与对话体量达到上限时生成恢复包的同时，先同步一次，再在 outbox 里生成 bootstrap-<时间>-short.md：一条短消息，列出 STATUS、PROJECT.md、VISION.md、WORKFLOW.md、DRIVER.md、最近一轮双方消息与工具源码的 raw 链接，以及当前版本、指纹与最近一轮的验收结论。开新对话时只发这一条即可；长消息与 zip 照旧生成，作为后备。

## 8. 边界

- 公开仓库的历史会保留已推送的内容。扫描是最后一道闸，不是唯一的一道：密钥本来就不应出现在消息与已提交文件里（WORKFLOW.md 第 12 节）。
- 同步失败或暂停不影响工作流推进。
