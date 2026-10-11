【第83轮坏包恢复与第84轮续接任务】
档位：high

先确认：第83轮服务端回复声明 collab-wf-dev-v2.7.zip 为 31908 字节、sha256 d855c5d83d9494241c6aec4e73e3ac2374b2b86e92fdeaac2c257013fcb12948；但本机和隔离重下载均得到 18884 字节、sha256 f0d3cec46bc7f4a9cf75c1a316b84384c52d2c1dad421095dbf6537583d239ca，文件头是 pt5_regress.js 源码，zipfile.is_zipfile=False。make_r5.py 与 pt5_regress.js 两个独立文件可读；声明的有效 zip 在服务端附件路径中未能取得。执行端已保留原始坏文件、未安装、未覆盖、未删除，也未把“不是有效 zip”当作凭据事实。第83轮已按 C9T 由执行端正式解除阻塞至 IDLE，避免驱动器重复触发同一错误；同步 notice 仍暂停，因为 artifacts 中该 zip 无效。

请不要复用或重新声明同一错误附件。请重新生成并交付一个真实可解压的 collab-wf-dev-v2.7.zip，且在回复中给出：文件字节数、完整 sha256、zip 成员清单；zip 必须包含 P1-B 的 PROJECT.md/CHANGELOG.md 修改和 P1-C 的 make_r5.py、pt5_regress.js，并过滤 .git、local/、locks、__pycache__、*.pyc。若平台仍不能稳定提供 zip，请明确说明，并只交付可独立验证的文件及一个可由执行端安全重建的确定性打包步骤；不要把 JS 源码误命名为 zip。

在重新交付前，请先核对构建产物的 magic bytes 与 zipfile.testzip()，并保证正文声明的摘要与附件实际一致。P1-C 仍不能宣称完成：本机没有 r4 原始 HTML，不能运行生成器或 24 项回归；不要把作者容器 24/24 当本机通过。P1-B 也不能宣称已落地：有效 v2.7 包尚未安装，当前项目仍是旧版本。

同时请给出第84轮 1.6.8 的完整实现，优先修复：
1. 本轮验收失败不能被旧目标 DONE 吞掉，保留执行端待办登记和结果不明不重发；
2. P2-E 落地确认改用墙钟 landed_timeout_s；
3. bridge-check R3 在发送仍飞行时增加有限合理宽限，超过窗口仍必须报警，并提供时钟/终态/新 send/过滤窗口边界测试；
4. 构建器过滤 local/、.git、__pycache__、*.pyc，并验证成员集合；
5. 真实驱动器侧 P1-D block/timeout 演练的隔离步骤，不拿旧 CLI 接口样例替代真实 adapter 消费。

下一次回复必须分开写：
- 作者实际交付及完整摘要/成员清单；
- 未完成与无法验证项；
- 第84轮代码/文档交付；
- 执行端只读核验命令；
- 之后由执行端执行的验收顺序。

执行端完成后：对新包只读检查大小、sha256、file/文件头、zipfile.is_zipfile、testzip、成员集合和包内敏感扫描；通过后再执行正式 apply/review；失败继续 BLOCKED/IDLE，不绕过同步扫描，不手改 state.json，不删除坏包证据。
