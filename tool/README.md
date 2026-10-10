# collab-wf

Claude × 执行端 协作工作流工具：状态机、自动记账与取证、版本落地与回滚、工作流看板。

- 写给用户本人：docs/USER.md
- 给用户：docs/USER.md（在 WorkBuddy 聊天窗口里只需要会说的六句话）
- 聊天窗口约定：docs/CHAT.md 与 intents.json
- 调用手册：docs/USAGE.md
- 协作规则：docs/WORKFLOW.md
- 能力边界与失败模式：docs/CAPABILITIES.md
- 执行端代码审计与收编：docs/AUDIT.md
- 适配器：adapters/README.md
- 每一步的完整定义：workflow.json（`wf step <状态>`）
- 自检：`python3 wf.py selftest`
