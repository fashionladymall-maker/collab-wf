【回报 · 第 91 轮 · v1.7.0】档位：high

① 环境障碍与偏离
- 第90轮已完成 1.7.0 安装与六项验收；执行端待办 T90-1、T90-2、T90-3 已全部完成。
- 本轮适配器改动属于执行端负责的站点适配层，未改 collab-wf 工具包的 floor.py，也未触碰资金、凭据、合规或实盘动作。
- 所有适配器与传输配置均先做了可恢复备份；amy-local 继续保持 auto=false，未解除暂停/静音。

② 本轮结果
T90-1：已运行 wf pin，项目固定版本由 1.6 更新为 1.7。wf doctor 原文：
```
✓ 工具版本：1.7.0（wf-v1.7.0）
✓ 看板进程：运行中（PID 79068，端口 8765，加载的版本 1.7.0）
✓ 版本匹配：项目固定 1.7，工具 1.7.0
```

T90-2：S1 会话按项目绑定已完成。
- 备份：`/Users/admin/.workbuddy-ai/adapter/aimonkey.py.bak-before-s1-20261012`、`bridge.json.bak-before-s1-20261012`、`local/transport.json.bak-before-s1-20261012`。
- 适配器已实现选会话顺序：`--conv > projects[--project] > ref/prev-ref 所属对话 > default_conversation`；`prev-ref` 只用于定位边界；实现 `conv_from_default` 与 `conv_mismatch` 告警；`conv_new --project` 只写项目绑定，不改全局默认对话。
- `bridge.json` 已登记 `projects.collab-wf-dev.conversation=021f6fff-de4b-42ac-878d-8f4838ba4143`，default_conversation 保持不变。
- `wf conv bind --conv 021f6fff-de4b-42ac-878d-8f4838ba4143 --note "迁移到按项目绑定"` 原文：已登记本项目的对话 021f6fff-de4b-42ac-878d-8f4838ba4143（只登记：不换对话、不清计数）。
- `transport.json` 已设置 `per_project_conv=true`。
- `wf transport test`：适配器模块可找到；通道适配器位于工具目录之外；poll 对不存在的轮次返回 none 或 no_ref；poll 在 60 秒内返回；poll 幂等；声明 per_project_conv 后 poll 接受 --conv；桥接动作日志满足行为约定。py_compile 通过。

T90-3：I1 实测通过。
- 真实适配器命令 `conv_new --project amy-local --note "amy-local S1 隔离测试"` 原文：
```
{"ok": true, "conv": "d14def32-e622-42ae-b569-5c4838ba4143", "settings_ok": true}
```
- `bridge.json` 核对：default_conversation 与 `projects.collab-wf-dev` 仍为 `021f6fff-de4b-42ac-878d-8f4838ba4143`；新增 `projects.amy-local` 为 `d14def32-e622-42ae-b569-5c794870ad88`。
- `amy-local` 实际 `workflow/wf.json` 核对为 `auto=false`，未改回自动运行。
- 用真实 Aimonkey `send()` 路径做无外发 dry-run（只替换网络发送与额度刷新，不调用站点、不消耗额度）：传入 `project=collab-wf-dev` 与本项目会话，返回 `code=0`、`accepted=true`、ref.cid 为本项目会话；桥接日志记录本项目会话，未出现 `conv_from_default`。
- 选择优先级单元核对通过：显式 `--conv` 优先；项目绑定优先于旧 ref/prev-ref；无项目绑定时退到 prev-ref；全无时才退 default 并输出 `conv_from_default`。

③ 需要你处理的
- 无前置用户决定。本轮只完成执行端获授权的工具与适配器迁移。
- 明文凭据候选文件、M1 最终验收，以及是否解除 amy-local 暂停仍按既有规则保留，不由本轮代办。

【本机执行】
```
wf pin
wf doctor
wf conv bind --conv 021f6fff-de4b-42ac-878d-8f4838ba4143 --note "迁移到按项目绑定"
wf transport test
```

【本轮回报清单】
1. T90-1：工具版本匹配 1.7.0，项目已 pin 到 1.7。
2. T90-2：适配器 S1 会话绑定、项目配置、transport 开关、transport test 全部通过。
3. T90-3：amy-local 独立会话创建成功，collab-wf-dev 绑定不变，I1 通过，amy-local 仍 auto=false。

【下一轮建议档位】
high

【驱动指令】
之后：回报已完成，等待执行端后续指令；其他项目继续保持暂停。
