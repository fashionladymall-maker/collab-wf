# collab-wf-dev · 项目状态（collab-wf 自动同步）

第 107 轮 · 验收（VERIFY）

## 版本
- 工具：collab-wf 1.7.6
- 项目：v3.3；55c1e75 (HEAD -> main, tag: v3.3) release v3.3
- 仓库指纹：cbad75d7e6df496b（10 个文件）

## 最近一轮
- 第 106 轮验收未通过（0 项）（2026-10-12T23:25:32+00:00）
- 本方最新消息：`turns/turn-107.md`
- Claude 最新回复：`replies/reply-107.md`

## 目标
- 进度 0 / 11
- ○ M2-B1 阶段目标：看板与 STATUS 先显示当前阶段，旧阶段只作历史：尚未检查
- ○ M2-B2 待办可取消、可被取代，完成必须写结果：尚未检查
- ○ M2-E1 NO_REF 不再循环：尚未检查
- ○ M2-F1 回复完整性：识别工具上限并有界续接：尚未检查
- ○ M2-H1 同步凭证：没有提交号不算推送：尚未检查
- ○ M2-A1 交接短消息钉在提交上、只链已推送的文件：尚未检查
- ○ M2-F2 回复发现延迟有界：尚未检查
- ○ M2-C1 默认工程门禁基线与角色准入退出：尚未检查
- ○ M2-D1 经验闭环：台账缺项不得标已解决：尚未检查
- ○ M2-G1 共享资源仲裁 S4：尚未检查
- ○ M2-U1 用户验收 M2：等待用户确认

## 待办
- [executor] 确认目标 M2-U1：用户验收 M2
- [executor] 自动化已暂停（执行端发起）：1.8.1安全整改候选已到，安装前立即审查symlink和逐cmd暂停负向路径；保持当前构建可结束，不允许未审查超时自动安装
- [executor] 执行端待办 T106-1：本轮验收通过后，迁移旧待办：wf task supersede T100-2 --reason "用户边界：Amy 在 collab-wf 完备前不恢复（第 104 轮）"；wf task supersede T100-1 --reason "由第 106 轮执行端 2 取代" --by-task T106-2。把两条命令的输出原文写进 wf task done
- [executor] 执行端待办 T106-2：同步程序（P0-H 执行端侧）：接受 --repo；拿不到锁或跳过时返回非 0，且不输出提交号；缓存、锁、日志按项目、目标仓库、分支分开。改好后先做一次 wf sync --dry-run，再做一次真实推送，确认 local/sync/collab-wf-dev.json 里出现 pushed_commit。结果写进 wf task done
- [executor] 执行端待办 T106-3：先运行 wf goals，核对 M2 目标是否已被自动批准。如果没有，按决策顺位审阅 goals.json 后，用 wf goals approve --by executor --reason 代批。再运行 wf self-check --probes，把它和 wf goals 的原文都写进 wf task done

## 最新产物
- `artifacts/collab-wf-1.8.1.zip`
- `artifacts/collab-wf-1.8.1.zip`
