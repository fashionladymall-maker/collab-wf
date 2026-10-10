# TRANSPORT.md —— 传输适配器约定

wf 不访问任何通道。收发消息由执行端提供的适配器完成，wf 只负责何时调用、超时、按退出码分类、幂等与记账。适配器的实现与通道无关：可以是任何受支持的通道（例如官方 API），换通道只需换适配器。

## 适配器放在工具目录之外

具体通道的适配器（含通道客户端、凭据）**不放在 collab-wf 目录内**。原因：工具目录会被 `wf bootstrap` 打包、随新对话投递，也会被 `self-update` 覆盖；而含站点内部接口的源码一旦进入聊天通道就会被安全防护拦下（F23）。
推荐位置：`~/.workbuddy-ai/adapter/`。工具目录里的 `adapters/` 只放通道无关的接口（`base.py`）与示例（`example_echo.py`）。通道适配器放在工具目录内时，`wf transport` 与 `wf transport test` 会报警，`wf self-check` 会把它列为“多出的文件”。

## 配置

`<工具目录>/local/transport.json`（每台机器一份，不进任何版本库）。适配器有两种接法，二选一：

**模块方式（推荐）**：适配器是一个继承 `adapters.base.Adapter` 的 Python 模块，放在工具目录之外。

```json
{"module": "aimonkey", "module_path": "~/.workbuddy-ai/adapter",
 "log": "~/.workbuddy-ai/adapter/bridge.log.jsonl", "timeout_s": 120, "model": "模型名",
 "rate": {"window_s": 600, "max_sends": 8},
 "env": {"CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR": "/Users/xxx/tmp/safe-delete"}}
```

wf 以 `python -m <module> <动作> …` 运行它，把 `module_path` 与工具目录加入 PYTHONPATH（所以适配器可以 `from adapters.base import Adapter, main`），并通过环境变量 `WF_TRANSPORT_JSON` 把这份配置的路径交给它。

**可执行程序方式**：send、poll、quota 三个可执行程序。

```json
{"send": "/绝对路径/send", "poll": "/绝对路径/poll", "quota": "/绝对路径/quota",
 "review": "/绝对路径/review（可选）", "log": "…", "timeout_s": 120, "review_timeout_s": 900, "model": "模型名",
 "rate": {"window_s": 600, "max_sends": 8}, "env": {}}
```

- rate：全局限速，所有项目合计，窗口内最多发送多少条（按实测的通道限制设置，留出余量）。
- log：适配器的动作日志路径，`wf transport test` 会用它核对行为约定。

env 中的变量对驱动器自身、适配器、wf exec 执行的命令都生效（用于无人值守时必须存在的环境设置，见 F16）。检查：`wf transport`（配置）与 `wf transport test`（黑盒一致性检查，不发送任何消息）。

## send

```
send --project <项目> --turn <轮次> --message <消息文件> --effort <档位> --model <模型> [--parent <父消息引用>]
```

| 退出码 | 含义 | 标准输出最后一行（JSON） |
|---|---|---|
| 0 | 通道已接受，且已出现生成开始的正面证据 | `{"ref": "本轮引用", "accepted": true}` |
| 2 | 通道正忙（上一条回复仍在生成，或执行环境被占用） | `{}` |
| 3 | 额度用尽 | `{"resets_at": "ISO 时间"}` |
| 4 | 暂时性错误，且无法确定消息是否已被接受 | `{}` |
| 5 | 通道明确拒绝（非暂时性） | `{"reason": "…"}` |

**退出码 0 必须有正面证据。** 只收到心跳、看不到生成开始，就不是 0：返回 `{"status": "heartbeat_only"}` 配退出码 2，或 `{"accepted": false}`，让 wf 按“通道忙”退避。wf 不只信退出码（缺口 11-3）。
适配器自己不重试（重试由 wf 按异常字典进行），不中止任何进行中的生成。

**中止生成不属于适配器接口。** 适配器的代码里不应保留调用“中止生成”的函数，包括不被调用的定义：留着它，就留着一条被误用的路径（F22）。

**清理“孤儿锁”不属于适配器接口。** 通道忙（包括疑似孤儿锁）一律返回 2；wf 按异常字典退避重试，达到上限进入“阻塞”，由人判断。第 40 轮实测：通道报忙时，最可能的情况是自己的上一条消息正在生成；会话里看不到新消息、updated_at 不变、is_pending 为 false，都不能作为“没有在生成”的证据，不要据此清理或中止任何生成。判断依据（供人参考，适配器不据此行动）：连续被拒超过 10 分钟；轮询没有任何正在生成的可靠迹象；会话里最后一条回复已经完整结束（正常结束或被拒）。所以适配器不需要、也不应该判断“没有在生成”：读不到页面、页面没有刷新、状态接口不准，都可能把正在进行的生成误判为没有（F22）。
**只有拿到正面的接受证据才返回 0。** 发送结果必须如实反映到退出码；不能在结果不明时记为已发送。

`--parent` 可选：从指定的父消息另开分支发送（叶子被拒后 wf 会带上 poll 返回的 parent_ref）。

## poll

```
poll --project <项目> --turn <轮次> --ref <send 返回的引用> --out <目录>
```

退出码 0，标准输出最后一行：

```json
{"status": "generating" | "done" | "none" | "refused",
 "reply_file": "<out>/reply.md",
 "files": ["<out>/xxx.zip"],
 "tools": [{"name": "…", "summary": "…"}],
 "parent_ref": "最近一条健康消息的引用（status=refused 时给出）"}
```

- generating：有可靠的“正在生成”迹象。
- done：回复已结束；回复全文已写入 reply_file，本轮交付的文件已下载到 out 目录。
- none：没有任何迹象（看不到消息也看不到生成）。wf 会退避后再查，不会据此重发。
- refused：这条消息被通道的安全防护拒绝（例如 stop_reason=refusal、空正文），无法从它继续。给出 parent_ref（上一条健康消息），wf 会回退到那里、重新组织并重发本轮消息（§“叶子被拒”）。
- landed（可选布尔）：这条消息是否已经出现在会话中。wf 用它做独立落地核对；不给时，wf 以 generating/done 视为已落地，连续多次 none 视为未落地并停下（不重发）。
- **同一轮的多次交付**：一轮里可能有多次生成（例如一次在途中被安全防护拦下、由其他模型续写，另一次正常完成），它们共用同一个代码容器，可能各自交付文件。poll 在 done 时只返回**最后一条完整回复**（stop_reason 为正常结束）交付的文件，并用 `other_deliveries` 报告同一轮里其他回复交付过几套文件。wf 会把这一情况记入决策日志与回报（§7、MULTI_DELIVERY）。
- 必须幂等：重复调用不产生副作用。
- 额度用尽或暂时性错误时，可以用退出码 3 或 4（同 send）。

## review（可选）

```
review --project <项目> --turn <轮次> --release <版本包> --diff <改动的 diff 文件>
```

退出码 0，标准输出最后一行：`{"verdict": "pass" | "concerns" | "block", "model": "审查所用模型", "notes": ["…"]}`。
用来接入另一个模型系列做独立审查。它只能阻止（block）或提出关注点（concerns），不能放行任何东西；不可用时驱动器记录“未审查”并照常按其他规则继续。

## quota（可选）

```
quota --project <项目> --out <项目>/workflow/usage.json
```

写入通道返回的原值（只记实测值），格式见 USAGE.md 第 5 节。失败不影响流程。

## 必须遵守

1. 无界面：不操作用户的鼠标键盘、不打开可见窗口、不截屏。
2. 凭据只存在于本机的适配器配置中，不写进任何消息、日志、项目仓库。
3. 每个动作写入本地 JSONL 日志,**action 只用工具认可的名字**（`wf bridge-check --list-actions` 列出;poll 这类轮询动作可以写日志（1.5.0 起为合法动作，便于事后判断消息到底送没送达）,refused 由驱动器侧的 poll 返回状态处理,不进动作日志）（USAGE.md 第 5 节），每轮可由 `wf bridge-check` 核对。
4. 判断“正在生成”只用已验证可靠的判据；“看不到”只能返回 none，不能当作失败。

## 附：1.5.0 新增的 poll 字段 reply_parent（可选，强烈建议提供）

poll 返回 status=done 时，适配器应在 JSON 中同时给出 reply_parent：这条回复所回应的那条用户消息的 ref，也就是 send 返回的 ref。wf 用它核对回复归属：

- 与本轮的 send_ref 相同：正常收下。
- 不同：说明取回的是对话里已有的旧回复（第 30 轮真实发生过：发送 21 秒后，轮询把上一条已有回复当成了本轮回复）。wf 不收下，继续轮询；连续 3 次后按 stale_reply 停下。
- 不提供：行为与 1.4.x 相同，但每轮记一条 reply_unverified 事件，wf doctor 会提示。

桥接日志中的 poll 动作从 1.5.0 起是合法动作（bridge-check 的 R6 不再报错）；R1 把 rejected 视为该次发送的终态，被拒后重发不再算重叠发送。

## 附：1.5.1 新增约定

- 时间上限：wf 调用适配器时，在环境变量 WF_ADAPTER_TIMEOUT_S 中给出本次调用的秒数上限。适配器应在上限前约 30 秒自行返回（例如把等待“生成已开始”的读流窗口设为 min(自身上限, WF_ADAPTER_TIMEOUT_S - 30)）。到时 wf 先发 SIGTERM，宽限 grace_s 秒（transport.json，默认 15）后才 SIGKILL，所以适配器的信号处理可以补写日志。
- transport.json 的 timeout_s 应大于适配器自身最长的等待时间加 60 秒。适配器最多等 300 秒时，建议设为 420。
- 发送结果不明时的定位（可选）：transport.json 设 "poll_since": true 后，wf 在发送意图没有 ref 的情况下调用 poll 时会加上 --since <发送时刻> 与 --message <本轮消息文件>。适配器据此查找“该时刻之后、内容与消息文件相同的那条用户消息”，返回它的 ref 与状态（generating 或 done）。同时传入 --prev-ref <上一轮的 ref>（wf 原样交回，不解析其格式），适配器可据此找到会话、重建定位所需的信息。不设时行为不变。
- 动作日志：每条回复结束都要写一条终态（done），只下载文件不写 done，会让 bridge-check 的 R1/R3 把下一次发送误判为重叠或无结果（第 46 轮核对日志第 30–160 行时发现）。
- poll 收到空的 --ref 且没有 --since 时，返回 {"status": "no_ref"} 并写日志，不要返回 none：none 表示“查了，没有”，空 ref 是“没法查”（第 44 轮的假阴性）。
- 结果不明之后，wf 不会自动重发。确认没有送达、需要重发时，由用户运行 wf resend（决定码加原话）。

## 附：1.6.4 新增的 conv_new 动作（可选）

policy.json 的 auto_new_conversation 为 true 时，wf 在对话达到上限时调用 `conv_new --note <说明>`（模块方式为 `python -m <模块> conv_new …`；可执行程序方式在 transport.json 写 `"conv_new": "<程序路径>"`）。

- 成功：新建对话、写全 settings 并回读核对、把适配器的默认对话切到新对话，最后一行输出 `{"ok": true, "conv": "<新对话 id>", "settings_ok": true}`，退出码 0。
- 任何一步不成立：输出 `{"ok": false, "error": "…"}`，退出码非 0，默认对话保持不变。
- 动作日志记 conv_new。新对话的首条消息怎么发（例如不带 effort）由适配器处理。
- 切换成功后，wf 不再把上一轮（旧对话）的 ref 作为 --prev-ref 交给适配器。

一致性检查（wf transport test）：对空 ref 的 poll，返回 none 或 no_ref 都算合规（1.6.4 起；此前只认 none，与上面 1.5.1 的约定矛盾）。
