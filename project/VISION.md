# VISION.md —— collab-wf 公开化需求

> 本文件由执行端按用户原话整理，作为需求提给 Claude。**Claude 出方案后，本文件由 Claude 接管维护。**
> 提出时间：2026-10-10 21:07 CST

## 一、用户原话

> 「我希望将我们的 collab-wf 项目真正建立起来，逐步版本迭代，最终公开给所有人使用」

## 二、执行端理解的需求

把 collab-wf 从「本机自用工具」推进为「任何人都能安装、使用、参与的公开项目」：

1. **真正建立起来** —— 有产品定位、有清晰的对外形象、有安装与上手路径
2. **逐步版本迭代** —— 有对外可见的路线图与版本节奏，不是被动响应式修补
3. **公开给所有人使用** —— 开源发布，陌生人能独立跑起来

## 三、执行端的现状扫描（环境事实，实测于 1.5.9）

### 对公开化有利的

- **无绝对路径硬编码**：全仓库 Grep `/Users/admin` **零命中**，工具是路径无关的
- **通道层已抽象**：`adapters/base.py`（通道无关基类，含参数解析、动作日志、退出码、约定输出格式）+ `adapters/example_echo.py`（本地文件模拟通道，供自检）。具体通道适配器**放在工具目录之外**，由 `local/transport.json` 的 `module` / `module_path` 指向，`wf` 以 `python -m <module> <动作>` 运行，并把 `module_path` 与工具目录加入 `PYTHONPATH`，外部适配器可 `from adapters.base import Adapter, main`
- 规模适中：**29 个 Python 文件、8510 行**
- 有自检与体检：`wf self-check`（1.5.9 为 189 项）、`wf doctor`
- 有版本线与回滚：git 标签 `wf-v1.0.0` ～ `wf-v1.6.0`

### 公开化的阻碍（7 项，逐条给实测依据）

| # | 阻碍 | 实测依据 |
|---|---|---|
| 1 | **README 不是给外人看的** | 全文仅 15 行，全是内部文档指针；第 6 行还重复了一行 |
| 2 | **无依赖声明** | 顶层无 `requirements.txt` / `pyproject.toml` / `setup.py` |
| 3 | **调度绑死 macOS** | `launchd` 出现在 `wf.py`、`wflib/ops.py`、`wflib/notify.py`；定时器用 `launchctl` + `~/Library/LaunchAgents/*.plist` |
| 4 | **概念绑死 WorkBuddy + aimonkey** | `README.md`、`intents.json`、`docs/{CHAT,USER,TRANSPORT,DESIGN,AUDIT,USAGE,CAPABILITIES,DRIVER}.md`、`dashboard/flow.html`、`wflib/asks.py` |
| 5 | **「大脑」通道是私有的** | 当前通道 `c.aimonkey.plus` 网页端（`local/transport.json` 的 `module: aimonkey`），适配器在 `~/.workbuddy-ai/adapter/`、**不在工具内**；陌生人无法具备 |
| 6 | **无 License** | 仓库根无 `LICENSE` |
| 7 | **无安装 / 上手路径** | 无安装脚本、无快速开始、无演示 |

## 四、需要 Claude 定的关键问题

1. **定位**：公开的是「协作工作流引擎」，还是「Claude × 执行端 这套具体协作法」？「执行端」这个概念依赖 WorkBuddy 聊天窗口，对外如何表述？
2. **「大脑」怎么办（最关键）**：陌生人不具备我们的私有通道。候选方向：
   - (a) 只公开执行端框架，通道由使用者自备（适配器接口公开，各自实现）
   - (b) 官方提供基于 API 的适配器（Anthropic / OpenAI / 任意兼容 OpenAI 的端点）
   - (c) 两者都做：内置 API 适配器 + 允许自定义
3. **跨平台**：`launchd` 是否抽象成「调度器后端」（launchd / systemd / cron / Windows 任务计划）？还是先只支持 macOS？
4. **去个人化**：`WorkBuddy` / `aimonkey` / `c.aimonkey.plus` 这些词，哪些改成通用词、哪些作为「内置适配器」保留？
5. **发布形态**：公开仓库（已有 `fashionladymall-maker/collab-wf`）够不够？要不要上 PyPI？License 选哪个？
6. **路线图**：从当前 1.5.9 到「陌生人能独立跑起来」，分几个版本、每版做什么、每版验收判据是什么？
7. **与现有目标的关系**：当前 `goals.json` 的六条 M1 判据（自检一致 / 体检无 ✗ / 连续 3-6-10 轮无人值守 / 自动安装工具版本）是「自用跑顺」的判据。公开化目标如何并入 —— 替换、追加，还是排在 M1 之后作为 M2？

## 五、执行端的建议（仅供参考，方案以 Claude 为准）

- 硬门槛优先级最高的是 **#2 依赖声明** 与 **#7 安装路径** —— 这两项决定「别人能不能跑起来」
- `adapters/base.py` 已抽象好，**#5「大脑」可能不需要大改架构**，只需提供 1～2 个内置适配器
- 建议**先出「公开化路线图」再动代码**，避免边改边定、返工
