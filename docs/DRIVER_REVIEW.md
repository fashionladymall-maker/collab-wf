# 驱动器强制安装审查：driver-review/1

自动落地在 baseline gates、工作区保存之后执行 `review.prepare`、一次
`review.run`、`review.status(..., phase="install")`。两次判定都允许时，才把同一
request_id 交给安装器。安装器仍在目标锁内独立复核和消费。没有 runner、核验器、
有效作者身份、完整结果，或结果超时、block、材料变化，都停止自动安装。
旧版可选审查适配器的结果不能构成安装授权。

作者优先从 `wf conv bind` 已绑定的对话 ID，在当前 `local/lanes.json` 的
`lanes.<作者>.sessions` 中精确唯一匹配。已有此绑定的配置无需增加字段。
缺失或歧义不会默认任选车道。

未绑定对话的宿主可以在 `local/transport.json` 显式配置：

```json
{"driver_review": {"schema": "driver-review/1", "author": "已登记的作者车道"}}
```

这是可选的版本化绑定；author 必须在当前 lanes 中有非空的 sessions。
如同时存在对话匹配，两者必须一致。诊断分别为 `author_identity_missing`、
`author_identity_ambiguous`、`author_identity_changed`。
synthetic runner/verifier 仅供隔离测试，结果不构成实景通过。

单次意图保存在目标自身的 `workflow/evidence/install/driver-review/`，
与 `WF_LOCAL_DIR` 无关。目标锁跨不同驱动器 local 串行化；记录绑定目标的真实
root、project name、created 身份、轮次、包 SHA、content_id 与基准 hash。
记录只新增、核对 hash、拒绝链接并在写后回读。每个目标身份的同一轮次只允许
一次自动尝试；改变包不会复用旧记录。

存在意图时，驱动器不会自动重复送审或安装，包括 pending、failed、installing
和 closed。损坏或不确定的记录也不会被当作从未尝试。停止原因会显示 request_id。

恢复时先核对目标安装事务及实际版本。若仍需安装，修复宿主配置后显式执行：

```bash
wf force APPLY --reason "已核对安装记录，重新送审"
wf review prepare package.zip --kind project --author 作者车道 --json
wf review run 新request_id
wf apply package.zip --review-id 新request_id
```

恢复到 APPLY 必须先于 prepare，以固定送审时的目标状态。CLI 会重新核对并消费
这个新请求；成功后进入执行阶段。也可以让作者在新轮次
重新交付。旧意图保留作证据，不需要删除它来恢复。若安装事务未闭合，先按安装器
诊断恢复该事务基线；新的审查请求不代替事务恢复。
