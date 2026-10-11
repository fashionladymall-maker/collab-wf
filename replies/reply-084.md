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
[工具 bash_tool]
[工具 bash_tool]
[工具 bash_tool]
[工具 create_file]
[工具 bash_tool]
[工具 create_file]
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

**作者实际交付（作者容器里逐个核对过）**

我先核对了文件头（magic bytes）、`zipfile.testzip()`，并用打包脚本重建了两次。下面的摘要都是交付目录里实际文件的值。

| 文件 | 字节 | sha256 | 文件头 |
|---|---|---|---|
| collab-wf-dev-v2.7.zip | 115768 | 2d7cd24de07861b724d30638dd32d51759a1d1a293446f30324b60a3e21aafd0 | 50 4b 03 04（PK） |
| build_wf_168.py | 44052 | ca141fff51d9e96bfcc36701bd21db88144f0608eed6b4c979678b188b402679 | #!/u |
| pack_v27.py | 5277 | 25878c42d0d09ae4c30a08f880ddc307d5c30deab7512fe631d8cce9fc100f0b | #!/u |
| v27-PROJECT.md | 65659 | c2125949bb3a9a55098a54cf74c603945d5617506200323024b945c555310a27 | # PR |
| v27-CHANGELOG.md | 10533 | 24da66f291344a3bc4858f08a8f877349a274c9c434c9fd8108dd3c813ac592f | # CH |

v2.7 包内容（不压缩、固定时间戳、testzip 为 None）：

| 成员 | 字节 | sha256 |
|---|---|---|
| collab-wf-dev-v2.7/（目录） | 0 | — |
| collab-wf-dev-v2.7/CHANGELOG.md | 10533 | 24da66f2…（同 v27-CHANGELOG.md） |
| collab-wf-dev-v2.7/PROJECT.md | 65659 | c2125949…（同 v27-PROJECT.md） |
| collab-wf-dev-v2.7/papertrade/make_r5.py | 19964 | 708e7dd49640cd018f709a33921639e48738308a5c757b9c93d808ed62566874 |
| collab-wf-dev-v2.7/papertrade/pt5_regress.js | 18884 | f0d3cec46bc7f4a9cf75c1a316b84384c52d2c1dad421095dbf6537583d239ca |

- 包里没有 .git、local/、locks、`__pycache__`、*.pyc。
- 用工具的同步密钥扫描规则扫过，0 命中。
- 在副本项目上，评审规则没有发现需要用户批准的项，`apply_zip` 落地成功；落地后指纹应为 **763bef1a04e059f1**（9 个文件）。

**第 83 轮坏文件**

我这边当时的同名文件是有效 zip（31908 字节、d855c5d8…、PK 文件头、testzip 通过）。你那边拿到的是同一轮 pt5_regress.js 的字节，所以文件是在附件到本机下载之间被换掉的。具体哪个环节出的问题，我看不到，也不下结论。已记为 W48，d855c5d8… 这份声明作废。v2.7 从未落地，按你的要求沿用这个版本号。

本轮的保护措施：

- 每个交付文件附大小、sha256 和文件头。
- pack_v27.py 可以只读核对 zip，也可以从四个源文件确定性重建 zip，重建结果与交付包逐字节相同（我在容器里重建了两次，一致）。
- 你本机那份“坏 zip”的字节恰好就是 pt5_regress.js，可以直接当作重建的源文件使用。

**未完成与无法验证的项**

- **P1-C**：你本机没有 r4 原件，生成器和 24 项回归都没在本机运行过。作者容器的 24/24 只是在替身页面上的结果，不算本机通过。r4 原件的 sha256 应为 1d580864…，72438 字节；第 79 轮如果是驱动器接的回复，它可能在 `workflow/evidence/turn-079/delivered/` 下，见执行端 2。
- **P1-B**：v2.7 尚未落地。
- **P0-A**：DONE 下的真实闭环仍未检验。本轮内存里是 1.6.7，已有 P0-A 的修复，所以本轮可以检验，见执行端 3。
- **1.6.8**：只是交付了构建器，本机还没构建、安装或回归。
- **P1-D**：演练的实景运行要在 1.6.8 装上之后。
- **P2-F**：仍未开始。
- **审查者的独立性**：独立审查由执行端完成，相对执行端不是独立的。

**第 84 轮交付：build_wf_168.py（1.6.7 → 1.6.8，不改 floor.py）**

1. **验收失败不再被 DONE 吞掉**：目标判据通过、但本轮有验收未通过或命令失败时，不进入 DONE，照常回报，决策日志写“先回报，不进入目标达成”。下一轮干净、目标仍达成，照常进入 DONE。执行端待办照样先登记，熔断照常计数。
2. **P2-E 墙钟计时**：新增 `landed_timeout_s` 和 `landed_min_polls`（默认 3），都是时间参数，不算扩大权限。设了 `landed_timeout_s` 后，必须距发送（sent_at）满这个秒数、并且至少核对 min_polls 次，才判 SEND_NOT_LANDED，此时 `landed_confirm_polls` 不再起作用。不设就保留旧的次数判定。SEND_NOT_LANDED 仍不自动重发。迁移方式是在 policy 里加 `"landed_timeout_s": 2700`，等 1.6.8 装好后用单独的项目版本做。
3. **bridge-check R3 飞行宽限**：
   - 发送后还没有结果、也还没有新的发送时，在宽限内（`--grace-s`，默认 900 秒）不报，并写明“在等待”；超过宽限照常报。
   - 结果出现之前又有新的发送，一律报。
   - 作为日志最后一条的旧发送，以前从不报，现在也会报。
   - `--hours` 只过滤日志窗口，不当作宽限；判定时刻可以固定，便于自检。
4. **构建器**：完整自检放在另一个副本上跑；打包前过滤 local/、.git、`__pycache__`、*.pyc；核对版本包成员集合恰好等于“文件清单 + MANIFEST.json”，并做 testzip。
5. **P1-D 演练**：新增 `wf drill review --kind block|timeout --dir <空目录> --review <真实审查程序>`（wflib/drill.py、docs/DRILL.md）。
   - **隔离方式**：用 WF_LOCAL_DIR 把注册表、inbox、transport 和状态锁都放进演练目录，所以生产的定时 tick 看不到演练项目；它也不在 sync_projects 里，不会同步到公开仓库。
   - **替身只替代 Claude 通道**：驱动器和审查程序都是真实的。
   - **block 演练**：用一个削弱四道底线的版本包，必须停在 AWAITING_USER，不落地。
   - **超时演练**：用良性版本包，应记“未审查”并照常落地继续。
   - **收尾**：结束后暂停演练项目，目录保留作为证据，不删除任何东西。
   - **已知副作用**：真实审查程序有写死的目录，所以仍会在生产 local/ 里写审查信号、唤醒执行端。这正是要演练的真实链路，DRILL.md 里如实写明。
6. **作者容器自测**：
   - 在 1.6.7 基线上构建，自检 231/231（新增 15 项）。
   - 反向验证：只加新用例、不改代码时，新用例在 1.6.7 上失败 7 项，说明用例测到了改动。
   - 模拟 `self-update --auto`：升级核验通过，63 个文件一致，self-check 完全一致。
   - 版本包 64 个成员，没有运行文件。容器里的 zip sha 是 c5733bc3…，本机重建可能因为压缩库不同而不一样，以构建器自己的成员核对为准。
   - 以上都是作者自测，以本机结果为准。

**执行端只读核验命令（在任何 apply 之前做，不需要驱动器）**

```
python3 workflow/evidence/turn-084/delivered/pack_v27.py --check workflow/evidence/turn-084/delivered/collab-wf-dev-v2.7.zip
shasum -a 256 workflow/evidence/turn-084/delivered/*
head -c 4 workflow/evidence/turn-084/delivered/collab-wf-dev-v2.7.zip | xxd
```

**之后由执行端执行的验收顺序**

1. 对 5 个文件逐个核对大小、sha256 和文件头，与上表比对。对 zip 运行 `pack_v27.py --check`，确认 is_zipfile、testzip、成员集合和每个成员的内容。用 `sync.scan` 做包内敏感扫描。
2. 第 1 步任何一项不通过：不 apply，保持 BLOCKED/IDLE，保留坏文件证据。可以从 v27-PROJECT.md、v27-CHANGELOG.md 加上第 83 轮已有的 make_r5.py、pt5_regress.js，用 `pack_v27.py --src <目录> --out <新路径>` 重建，再用 `--check` 核对。
3. 第 1 步通过后，由驱动器正式走 REVIEW 和 APPLY。执行端作为审查者，先对驱动器收到的那份 zip 运行 `pack_v27.py --check`，不通过就判 block。
4. 落地后，驱动器执行【本机执行】里的三条命令：指纹应为 763bef1a04e059f1，`wf goals` 应为 7/7。然后进入 DONE，并登记 T84-1 到 T84-3。
5. 按执行端 1、2、3 的顺序完成待办，最后用 `wf kick` 发下一条消息。
6. 下一轮（第 85 轮）由我写驱动指令：运行 `workflow/evidence/turn-084/delivered/build_wf_168.py`（这个路径符合白名单），自动安装 1.6.8；再下一轮做 P1-D 实景演练，并发布 policy 里加 `landed_timeout_s` 的项目版本。

【本机执行】
```
wf version
wf fingerprint
wf goals
```

【本轮回报清单】
1. 工具版本仍为 1.6.7（本轮不升级工具）。
2. v2.7 落地后，仓库指纹为 763bef1a04e059f1（9 个文件）。
3. `wf goals` 原文，应为 7/7。

【需要用户决定】
1. 执行端目录下明文凭据候选文件（含一个权限过宽的旧备份）的处置：删除、收紧权限或保留。由你本人决定，执行端不代办。本项不阻塞本轮。
2. M1 最终验收：只认你本人的原话。本项不阻塞本轮。

【下一轮建议档位】
high

【驱动指令】
发布：collab-wf-dev-v2.7.zip
执行端 1：只读核验本轮全部 5 个交付文件：逐个核对大小、sha256、文件头；对 zip 运行 pack_v27.py --check，并用 sync.scan 做包内扫描；把结果用 wf task done T84-1 --note 记下（任何一项不符时写明，不重建、不覆盖、不删除）
执行端 2：P1-C 前提核对：查找 r4 原件（sha256 1d580864a913f907d6f6c1aacd78d61a13db11976af47c13eb8f3fc995f8794f，72438 字节），先看 workflow/evidence/turn-079/delivered/；找到就运行 papertrade/make_r5.py --in <r4> --out <同目录>/papertrade-business-research-20261011-r5.html，再运行 node papertrade/pt5_regress.js <r5> <r4>，并做真实浏览器桌面与 390 宽度检查；找不到就如实写“未找到”。结果用 wf task done T84-2 --note 记下
执行端 3：P0-A 实景（最后做）：先在 T84-3 未完成时记下 wf status（应为第 84 轮 DONE）、wf task list、local/inbox.json 里的 task:T84-3（应带 action）、wf explain（应写“需要：执行端”）；然后 wf task done T84-3 --note "P0-A 实景"，再记下 inbox 中 done:84 的 why 是否附上 T84-1 到 T84-3 的结果、状态是否仍为 DONE；最后用 wf kick 把三项结果和本轮三项验收结论作为下一条任务消息发出
之后：等执行端
验收 1：c1 exit=0 包含 "collab-wf 1.6.7"
验收 2：c2 exit=0 包含 "763bef1a04e059f1"
验收 3：c3 exit=0 包含 "目标进度 7 / 7"
指纹：763bef1a04e059f1