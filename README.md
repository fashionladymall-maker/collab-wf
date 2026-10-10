# collab-wf-dev · 项目上下文（collab-wf 自动同步）

本仓库由 collab-wf 在每次内容变化后自动同步，是本项目的上下文载体：读这里就能知道最新的版本、进度与工具源码。

## 怎么读
1. `STATUS.md`：版本、轮次、最近一轮的结果、目标进度、待办，一屏看完。
2. `project/`：项目的已提交文件（PROJECT.md、CHANGELOG.md、workflow/ 下的白名单与目标等），与仓库指纹同口径。
3. `tool/`：当前安装的 collab-wf 工具源码（版本清单内的全部文件），可直接按路径读取。
4. `turns/turn-NNN.md`：执行端每轮发出的消息原文；`replies/reply-NNN.md`：Claude 每轮的回复原文。
5. `artifacts/`：最新的工具版本包与项目版本包。

## 边界
- 内容由工具按白名单挑选，推送前全部经过发送前用的密钥扫描（zip 内的文本也扫），命中就整次不同步。
- 不含：local/（通道配置、会话凭据、页面快照）、workflow/evidence/（命令原文）、approvals.json、状态与事件文件。
- 本仓库是只读镜像，源头在执行端机器上；请不要在这里改文件。
