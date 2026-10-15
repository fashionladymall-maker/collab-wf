# 独立审查 block 与超时演练

`wf drill review` 在空目录中创建一次性项目，使用真实驱动器和调用方指定的审查程序。block 和超时都必须停在 BLOCKED，不得安装版本包或启动安装事务。超时不能作为通过。

```sh
wf drill review --kind block --dir ~/wf-drills/block-<日期> --review /absolute/path/to/reviewer
wf drill review --kind timeout --dir ~/wf-drills/timeout-<日期> --review /absolute/path/to/reviewer --review-timeout-s 120
```

审查程序必须支持当前 REVIEW 协议。block 演练要求程序明确返回 block；timeout 演练要求程序运行超过所设期限。调用方宿主 transport.json 的 review_verifier 配置原样复制到隔离环境，包括固定哈希和 synthetic 标记；不会创建可信核验器或伪造外部审查来源。缺少有效核验器导致的拒绝不能算作 block/timeout 演练通过。

演练 local 中单独登记作者车道及 driver-review/1 作者绑定。注册表、请求、尝试、inbox 和状态锁均在演练目录内。Claude 发送与轮询使用 fake_transport，审查仍使用 --review 指定的程序。选择会遵守隔离环境的审查程序；外部程序写死的路径不受此工具隔离机制控制，执行前应核对其行为。

结果写入 `<演练目录>/drill-result.json`，包含实际尝试、安装审查决定、状态、版本及事务检查。权威证据为 `local/reviews/attempts/*/attempt.json`、项目 `workflow/evidence/install/driver-review/*-decision.json` 及项目 Git 标签。两种演练均要求恰好一次实际尝试、拒绝安装、保持 v0.1 且无 v0.2 标签或 started 安装记录；block 要有明确 block，timeout 要有 timed_out 尝试。

自检使用 synthetic runner 和固定哈希核验器，只证明协议及门禁机制，不代表真实外部模型审查，也不构成生产验收。使用真实审查程序时，来源核验必须由已有宿主配置提供。

目录必须不存在或为空，不能复用已有证据目录。演练结束自动暂停项目，保留全部证据，不自动删除。演练通过只证明对应拒绝路径，不证明整体集成或生产安装合格。
