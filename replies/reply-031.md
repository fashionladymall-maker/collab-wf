[工具 bash_tool]
[工具 view]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 create_file]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
Now the docs and the edit list, applied to a dev copy to run the full selftest.
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

第 31 轮两项都通过：v1.0 已落地，指纹 `3551230e49517eba`。goals.json 还没批准，执行端下次巡检时运行 `wf goals approve` 即可。

**公开仓库带来了一个比「少贴文件」更大的变化。** 我从仓库下载了 `artifacts/collab-wf-1.5.9.zip`，sha256 `3ba9bbe0c09b`，和执行端构建的包一致。在我这边跑它的完整自检，189/189 通过。也就是说，我现在能拿到真实的完整源码，在上面直接开发和测试，不用再靠你 cat 出来的片段去猜锚点了。本轮的 1.6.0 就是这样做出来的。

**审计 gh_sync.py，发现两个实质问题：**
1. README 写着「同步内容经过发送前密钥扫描」，但脚本里实际上没有任何扫描。
2. 它会上传 outbox 里所有的 `turn-*.md`，包括被发送前检查拦下、从没发出去的草稿。而一份草稿被拦下，最常见的原因恰恰是里面疑似有密钥。

其余没问题：用 flock 加锁、增量提交、成功后才写缓存、更新 ref 不强推，这些都对。

**你的三个问题：**

(a) **可以。** 开新对话时只需一条短消息加仓库链接，我按链接读 STATUS、PROJECT 和工具源码。有一个限制：匿名访问 GitHub API 每小时只有 60 次，我已经撞到过上限，所以我读文件主要靠 raw 链接，这就要求仓库里的路径是固定、可预知的。另外，对话寿命现在按轮数计（policy 上限 20 轮），不按字符计。消息变短本身不会延长寿命，这个以后再看要不要放宽。

(b) **不需要拆 PROJECT.md，也不需要放逐轮证据。** 回报里已经嵌了终端原文，而且经过了扫描。缺的只有两样：完整的工具源码（按路径可读），以及包含目标进度和待办的 STATUS。1.6.0 都补上了。

(c) **做进工具，就是本轮交付的 1.6.0（build_wf_160.py，sha256 `3d6f6a95b242`）：**
- 新增 `wflib/sync.py` 和 `docs/SYNC.md`。wf tick 之后，工具按白名单收集内容：STATUS.md、README.md、项目已提交文件、`tool/` 下的完整源码、只含已发出的本方消息、回复、最新版本包。推送前对全部内容做密钥扫描，zip 里的文本也扫。命中就整次不推送，并写进 inbox。内容没变就不调用同步程序。
- 新增 `wf sync [--dry-run] [--force]`。推送本身仍由执行端的程序完成，凭据不进工具。
- 自检新增 4 项。其中一项当场抓到了我自己写在测试里的假密钥：它会被同步到 `tool/` 下，扫描就拦住了整次推送。已改成拆开写。

本地验证：在 1.5.9 的完整源码上把构建器端到端跑通，自检 193/193。用 1.5.9 的 `floor.probe` 检查 1.6.0 候选，结果为空；`guard_changes` 结果也为空。所以本版由驱动器自己构建、自己安装，这也会是 G4 的第一次达成。

给执行端：1.6.0 装上之后，请做三件事：
① 把 gh_sync.py 改成同步程序的接口：`sync --project <名> --turn <N> --dir <暂存目录>`。效果是让仓库与暂存目录一致，可以保留 `scripts/`；成功时退出码 0，最后一行输出 `{"commit": "<sha>"}`。
② 在 local/transport.json 加上 `"sync": "<程序路径>"`（模块方式的适配器就写 `true` 并实现 sync 子命令）和 `"sync_projects": ["collab-wf-dev"]`，然后停掉 launchd 的 ghsync 定时器。
③ 先运行一次 `wf sync --dry-run`，确认没有疑似密钥，再运行 `wf sync`，最后运行 `wf goals approve`（引用用户已记录的原话）。
做完用 `wf note` 报告。

【本机执行】
```
/Users/admin/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 workflow/evidence/turn-031/delivered/build_wf_160.py --out workflow/outbox
wf self-update --auto workflow/outbox/collab-wf-1.6.0.zip
wf version
wf self-check
```

【本轮回报清单】
1. 构建通过，自检 193/193。
2. 驱动器自动安装 1.6.0，升级核验通过。
3. 工具版本为 1.6.0。
4. self-check 完全一致。

【需要用户决定】
无。

【下一轮建议档位】
high

【驱动指令】
发布：无
之后：回报
验收 1：c1 exit=0 包含 "构建通过"
验收 2：c2 exit=0 包含 "升级核验通过"
验收 3：c3 exit=0 包含 "collab-wf 1.6.0"
验收 4：c4 exit=0 包含 "完全一致"
超时：1800