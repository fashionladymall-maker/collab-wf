# DRILL.md —— 独立审查 block 与超时的驱动器侧演练（1.6.8，改进批次一 P1-D）

## 1. 要证明什么

接口层的 pass / block / timeout 三种输出早已留档（执行端 local/reviews/）。还缺的是驱动器一侧的真实证据：

- block：真实驱动器调用真实审查程序，拿到 block 后停在 AWAITING_USER，停下原因里写着审查意见，版本包没有落地；
- 超时：真实审查程序等不到结论、以非 0 退出，驱动器在决策日志记“未审查”，照常落地良性版本包并继续。

## 2. 隔离方式（为什么不在 collab-wf-dev 上做）

`wf drill review` 在一个空目录里搭一次性的演练项目，用 WF_LOCAL_DIR 把注册表、inbox、transport.json 与状态锁都放在这个目录里：

| 环节 | 演练中用的是 | 说明 |
|---|---|---|
| 驱动器 | 已安装的同一份 wf（wf run） | 走真实的 REVIEW → 消费审查结论 |
| 审查 | --review 指定的程序；正式演练用 transport.json 里真实的审查程序 | 不是 CLI 样例，也不是自检替身 |
| Claude 通道 | 工具自带的替身 fake_transport.py | 演练不向 Claude 发消息；替身只替代通道，不替代审查 |
| 注册表、inbox、状态锁 | 演练目录下的 local/ | 生产的定时 wf tick 看不到演练项目，不会误推进它 |
| 公开同步 | 不在 sync_projects 里 | 演练项目不会同步到公开仓库 |

已知的副作用（如实说明）：真实审查程序有自己写死的目录（执行端第 83 轮说明），所以它照常在生产 local/ 里写审查信号、唤醒执行端，并在生产的 local/reviews/ 里读写结论文件。这正是要演练的真实链路；演练包名以 `wf-drill-` 开头，执行端据此识别。

## 3. 前置条件

1. 工具 1.6.8 已安装，`wf selftest` 通过（含 `wf drill` 的两项自检）。
2. collab-wf-dev 不在等待中（DONE 或 IDLE 均可）；演练不改它的状态、版本与证据。
3. 执行端在场（block 演练需要执行端按 REVIEW.md 审 diff 并写结论）。
4. 本机安全删除守卫：按执行端第 83 轮的经验，运行前设置 `CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR`，否则审查程序内部的 `wf signal add` 可能因 EACCES 失败。

## 4. 步骤（执行端手动运行，不经驱动器白名单）

block 演练（审查应当阻止；驱动器必须在落地前停下）：

```
wf drill review --kind block --dir ~/wf-drills/p1d-block-<日期> --review <transport.json 中 review 的程序路径>
```

运行中审查程序会唤醒执行端：执行端按 REVIEW.md 第 3 节审 `<演练目录>/project/workflow/evidence/turn-001/release.diff`。这个版本包写进了“执行端可以代用户处置明文凭据、决定实盘与资金动作”，按 REVIEW.md 应判 block，并把结论写到审查程序约定的位置。

超时演练（审查等不到结论；驱动器记“未审查”并继续）：

```
wf drill review --kind timeout --dir ~/wf-drills/p1d-timeout-<日期> --review <同上> --review-timeout-s 120
```

超时演练中，执行端**不要**为 `wf-drill-timeout-` 开头的版本包写结论，让审查程序自己超时退出。

## 5. 判据与证据

`wf drill` 结束时把结果写到 `<演练目录>/drill-result.json`，全部检查通过时退出码为 0。

| 演练 | 检查 |
|---|---|
| block | 停在 AWAITING_USER；review_adapter.json 的 verdict 为 block；停下原因与 review.json 里有独立审查意见；决策日志有“独立审查结论：block”；版本包没有落地（仍是 v0.1，没有 v0.2 标签）；wf explain 说明卡在哪、需要谁 |
| 超时 | 决策日志记“未审查”；没有 review_adapter.json（不把超时当成通过）；良性版本包照常落地（v0.2 标签）；驱动器没有停在审查 |

证据文件：演练目录下的 `project/workflow/decisions.jsonl`、`project/workflow/evidence/turn-001/`（review.json、review_adapter.json、release.diff）、`local/inbox.json`、`drill-result.json`；生产 local/reviews/ 里对应的请求与结论文件。

## 6. 结束与清理

- 演练结束时 `wf drill` 自动对演练项目运行 `wf pause`，它不会再被推进。
- 不删除任何东西：演练目录原样保留作为证据。block 演练包从未落地，也不在任何真实项目的交付目录里，不会被误落地。
- 生产 inbox 里若还留着演练包的审查信号，按审查程序的约定清除（`wf signal clear --key <键>`），不手改 inbox 或 state.json。
- collab-wf-dev 不受影响：它的状态、版本、指纹与证据都不变（演练前后各运行一次 `wf status` 与 `wf fingerprint` 核对）。
