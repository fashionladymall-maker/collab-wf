【回报 · 第 25 轮 · v0.7】档位：high

① 环境障碍与偏离

1. **`github.com` 被阻断，`git push` 不可用 → 改用 REST API 完成提交。**
   - 现象：`git push` 报 `Recv failure: Operation timed out`；随后 `Failed to connect to github.com port 443 after 75003 ms: Couldn't connect to server`。
   - 实测（curl 逐个域名）：**只有 `github.com` 一个域名被阻断**（TCP 0.10s 能连上、20 秒收不到任何字节）；其余全部畅通。
   - 处置：绕过 `github.com`，改用 REST API 提交（blobs → tree → commit → ref；空仓库先用 Contents API 播种一个初始提交）；Release 用 `gh release create`（走 api.github.com + uploads.github.com）。
   - 这是**本机链路**的干扰；Claude 侧从自己的出口取，不受影响。已实测匿名下载并核对 sha256 通过。

2. **手动安装 1.5.9**（按第 24 轮指令）。本版改动了 1.5.8 的护栏文件（policy.py、release.py），按设计不能自动安装；授权依据是用户已记录的原话「用户希望连护栏改动也全自动」。**这是最后一次手动安装**，装后只有改动 `floor.py` 的版本才会停下等用户。

② 关键数字与终端原文

1) 第 24 轮交付物核对 —— 与回复里给的 sha256 前 12 位 `eee47790f61d` 一致
```
eee47790f61de05e9ed5f8716696d0d0b00d4310db2227dcaa2fbf99cce62809  workflow/evidence/turn-024/delivered/build_wf_159.py（29867B）
```

2) 构建（构建器由驱动器跑完，日志 `workflow/outbox/build-wf-1.5.9.log`）
```
[6/6] 打包
      /Users/admin/.workbuddy-ai/projects/collab-wf-dev/workflow/outbox/collab-wf-1.5.9.zip（56 个文件，268198 字节，sha256 3ba9bbe0c09b）
构建通过。本版需要手动安装一次：wf self-update …/collab-wf-1.5.9.zip
```
```
自检结果：189 / 189 通过
```

3) 安装
```
$ wf self-update workflow/outbox/collab-wf-1.5.9.zip
升级核验通过：磁盘版本 1.5.9，56 个文件与版本包一致，看板已加载 1.5.9
工具已升级：1.5.8 → 1.5.9（ec2455e），自检已通过
看板已重启以加载新版本
```

4) 三项验证
```
$ wf version
collab-wf 1.5.9（wf-v1.5.9）  安装位置：/Users/admin/.workbuddy-ai/tools/collab-wf
可回滚的版本：wf-v1.5.9、wf-v1.5.8、wf-v1.5.7、…
当前项目 collab-wf-dev 固定版本：1.5

$ wf self-check
对照 1.5.9 的文件清单：完全一致

$ wf doctor
✓ Python：3.13.12
✓ 工作流定义：workflow.json，14 个状态
✓ 工具版本：1.5.9（wf-v1.5.9）
✓ 看板进程：运行中（PID 86694，端口 8765，加载的版本 1.5.9）
✓ 版本匹配：项目固定 1.5，工具 1.5.9
✓ PROJECT.md：v0.7，阶段：1.5.7 已安装，1.5.8（自动安装工具版本、tick 内等回复）构建器已交付
✓ 项目工作区：干净
✓ 当前步骤：IDLE（空闲），第 24 轮
✓ 驱动器心跳：最近一次运行在 0 分钟前（退出码 0）
! 额度数据：18 分钟前更新  → 超过 10 分钟未更新：轮次记录中的额度会是空值（只记实测值）
! 事件序号：共 916 条：重复 5 个、倒序 2 处、无法解析 0 行
! 续传队列：2 片原文待发  → 下一份回报会自动先发送
```

5) 域名可达性实测（本机，2026-10-10 20:12）
```
api.github.com                            200  20.205.243.168  连接 0.090s  总计 0.262s
github.com                                curl: (28) Connection timed out after 12006 milliseconds
codeload.github.com                       301  20.205.243.165  连接 0.105s  总计 1.492s
raw.githubusercontent.com                 301  185.199.108.133 连接 0.072s  总计 0.204s
objects.githubusercontent.com             404  185.199.111.133 连接 0.065s  总计 0.330s
uploads.github.com                        302  20.205.243.161  连接 0.095s  总计 0.792s
release-assets.githubusercontent.com      404  185.199.110.133 连接 0.073s  总计 0.288s
gist.githubusercontent.com                301  185.199.109.133 连接 0.073s  总计 0.665s
```

6) 交接包已发布到 GitHub（用户要求走 GitHub 交付，供你按链接读取）
```
仓库（公开）：https://github.com/fashionladymall-maker/collab-wf-handoff
提交：3e46a14fe3405fd9ebd2af6604ed2e017e2d6d0a（main，9 个文件）
Release：https://github.com/fashionladymall-maker/collab-wf-handoff/releases/tag/handoff-2026-10-10
```
文本（raw 直链，可直接读）：
```
https://raw.githubusercontent.com/fashionladymall-maker/collab-wf-handoff/main/README.md
https://raw.githubusercontent.com/fashionladymall-maker/collab-wf-handoff/main/handoff/INDEX.md
https://raw.githubusercontent.com/fashionladymall-maker/collab-wf-handoff/main/handoff/bootstrap-20261010-190418.md
https://raw.githubusercontent.com/fashionladymall-maker/collab-wf-handoff/main/handoff/PROJECT.md
https://raw.githubusercontent.com/fashionladymall-maker/collab-wf-handoff/main/handoff/CHANGELOG.md
https://raw.githubusercontent.com/fashionladymall-maker/collab-wf-handoff/main/handoff/reply-022.md
```
源码包（Release 附件，匿名可下载）：
```
https://github.com/fashionladymall-maker/collab-wf-handoff/releases/download/handoff-2026-10-10/collab-wf-1.5.8-ceedce4.zip
https://github.com/fashionladymall-maker/collab-wf-handoff/releases/download/handoff-2026-10-10/collab-wf-dev-v0.7-ed4d6ca.zip
https://github.com/fashionladymall-maker/collab-wf-handoff/releases/download/handoff-2026-10-10/collab-wf-dev-v0.7-2a5e72f.zip
```
匿名核对（未带任何令牌）：下载 `collab-wf-dev-v0.7-ed4d6ca.zip` → 13939 字节，sha256 `567ad1c0df6b618f692223f50e240f7ade9759c4e307775dcb77d467ac6414d4`，与 `handoff/INDEX.md` 一致。

③ 需要决策的点
无。

④ 需要注意（Attention）
- `github.com` 在本机被**间歇性**阻断（早先尚可，20:10 起持续超时）；`git push` 不可用，本次改用 REST API 提交。后续再推 GitHub 仍需走 API。
- 交接包仓库是**公开**的：用户明确决定「就用 github」并选择「公开·连源码一起」，**允许工具源码公开到公网**。这与更早「源码不该留公网」（交接站因此下线）的定调相反，已用 `wf said` 逐字记录。
- 凭证由用户本人在终端 `gh auth login` 提供（账号 `fashionladymall-maker`，令牌存 macOS 钥匙串）；执行端全程未接触令牌。
- `wf doctor` 的 3 条 `!` 均为已知项：额度数据 18 分钟未更新（建议桥接每次查询后写 usage.json）、事件序号历史重复/倒序（1.5.0 起已改先写状态再追加事件，历史不改）、续传队列 2 片待发（下一份回报会自动先发）。

附：仓库指纹 65f6fad7a591346c（v0.7，4 个文件）
