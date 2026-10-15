【项目续接 · 新对话】档位：max

这是一个进行中的项目，上一个对话因 {reason} 结束。请按 WORKFLOW.md 第 10 节处理：

1. 附件 {zip} 是项目仓库（git 提交 {head}，版本 {version}，只含已提交文件，不含数据与密钥）。仓库指纹 {fingerprint}：请用 collab-wf 的指纹规则核对。
2. 附件 {tool_zip} 是协作工具 collab-wf {tool_version} 本身（源码、自检、文档）。请先阅读其中的 docs/DESIGN.md（总设计与边界）、docs/CAPABILITIES.md（能力边界）、docs/DRIVER.md（驱动器与【驱动指令】格式）。
3. 工作流当前位置：{state}。
4. 最近一轮的回报清单与验收结论：
{last_turn}
5. 下面是 WORKFLOW.md 与 PROJECT.md 全文。
6. 本轮请只做三件事：复述当前版本、阶段、待用户决策与工具边界；确认两个附件与 PROJECT.md、仓库指纹一致；给出下一轮指令。本轮不要交付新版本，【驱动指令】保持当前的计划节点。
7. 如果附件没能上传（有的通道只能下载）：文末附有工具与项目的文件清单（路径、大小、sha256 前 12 位）。请按需点名文件，由执行端用 cat 或 sed -n 起,止p 读取后经回报送达（回报按预算完整嵌入原文，放不下的部分自动续传）。没读到原文之前，不要推断文件内容。

===== WORKFLOW.md =====
{workflow_md}

===== PROJECT.md =====
{project_md}

===== 文件清单 =====
{tool_manifest}
