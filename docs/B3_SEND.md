# A-B3-SEND：accepted 发送意图崩溃保护与 WAIT_REPLY 缺 ref 的 NO_REF

基线 PR33 head `d01192d`。仓库里没有 S02a/S02b/S03 的条目定义，本文按 DEVELOPMENT.md 批次 3 的两项
（accepted 发送意图崩溃保护、WAIT_REPLY 双缺字段的 NO_REF 调整）编号：S02a = accepted 残留，
S02b = pending/结果不明，S03 = WAIT_REPLY 没有 ref。改动只在 `wflib/driver.py`。

## 源码里的状态与字段

- `send_intent`：外发前在锁内落盘的意图 `{turn, status, ts, file, message}`。`status` 只有
  `pending`（已落盘、适配器结果未知）与 `accepted`（适配器确认接受）。`message` 绑定项目根、轮次、
  attempt_id 与正文副本 `turn-NNN/send-<attempt>.md` 的 sha256。
- `_finalize_send` 的顺序：写发送日志 → `send_ref` → 关闭恢复期 → 当日轮数 → **保存（accepted 已落盘、仍在 SEND）**
  → `ops.mark_sent`（SEND → WAIT_REPLY）→ `last_sent`、清 `send_intent` → 保存。
- **双缺字段**：状态 WAIT_REPLY，同时没有 `send_ref` 也没有 `send_intent`。`h_wait_reply` 原来只看
  `send_ref`，一缺就判 NO_REF（“手动发出”）。驱动器自己发出、适配器没返回 ref 时，`_finalize_send`
  照常清掉意图，就落入这个状态。

## 复现（未修代码 d01192d，真实 fake_transport，按适配器调用记录计数）

| 场景 | 未修 | 修后 |
|---|---|---|
| accepted 残留（子进程在 `mark_sent` 处真实 SIGKILL），仲裁关 | 再次调用 send，共 2 次 | 1 次，轮询确认 |
| 同上，仲裁开、本地 token 一致 | 共 2 次 | 1 次，租约不变 |
| 同上，原租约已被回收 | 共 2 次，并持有新租约 | 1 次，新授予立即按 token 退回 |
| 本轮意图无 status（旧协议） | 当作未发送，send 1 次 | 停下，0 次 |
| 驱动器发出、无 ref、通道支持 poll_since | NO_REF 等人 | 按时刻与正文定位轮询 |

根因：`h_send` 只把 `pending` 当在途；`accepted` 与旧协议意图落到新发送分支，`_prepare_send` 覆盖原意图再发。

## 修复语义

- **S02a**：本轮 `accepted` 残留绝不进入新发送。先用 `_check_attempt` 核对正文绑定（项目根、轮次、副本 sha），
  不符即停（`[SEND_UNKNOWN]`）；通过则降为待核对（保留 ref，`origin=accepted_unfinished`，事件
  `SEND_ACCEPTED_UNFINISHED`），只轮询确认后补完登记。仲裁关时不触碰仲裁；仲裁开时仍走原
  acquire/`_slot_bind`（token 缺失或不符照旧降为结果不明），只在“本地 token 一致”或“刚得到新授予”时拦截，
  新授予不做外发前标记、不写进 `send_slot`，立即按 token 释放。
- **意图身份**：只有字段不存在（None）才算没有意图。字段存在但不是 dict（`[]`、`0`）、是空 dict、状态不是
  pending/accepted、缺 status 或轮次不符，一律保留原值停下（BLOCKED），在限速、仲裁和发送之前返回：
  不调用 send、不 acquire。（A-B3-SEND-FIX 必修 1：原判断写成 `if intent and …`，`{}`、`[]`、`0` 会跳过检查并真实发送。）
- **幂等登记**：`_finalize_send` 在意图上记 `logged`，中断后补完登记不重复写发送日志、不重复计当日轮数。
- **S02b**：pending/结果不明仍走原核对分支（抽成 `_verify_pending`，代码不变）：只轮询，不 acquire、不借用、
  不重发；`SEND_UNKNOWN_WAIT` 的恢复期 id/since/deadline 跨重启、真实 kill 不重置，同一意图只计一次。
- **S03**：WAIT_REPLY 没有 `send_ref` 时，只有在本轮确由驱动器外发且有持久证据、并且 `transport.json`
  声明 `poll_since` 时才定位轮询；证据是本轮意图，或意图已清（双缺字段）时的 `last_sent`。`_finalize_send`
  在 `last_sent` 里保存完整的 `message`（driver-message/1：项目根、轮次、attempt_id、副本路径、正文 sha256、
  源文件与其 sha256）、副本的 `snapshot_stat`（dev、ino、mode、size）和 `sent_ts`（外发前的意图时刻，不用
  mark_sent 之后的 `sent_at`）。定位前由 `_sent_message` 重新核对：`_check_attempt` 的根、轮次、attempt、副本
  路径与正文 hash；源文件仍是当时外发的内容（`_outgoing_check`）；副本仍是同一个文件，mode 与大小未变。
  任一项不符（正文、同名替换、mode、源文件、轮次、路径）都不定位，按 NO_REF 停下，`last_sent`、发送槽与
  外发标记原样保留（A-B3-SEND-FIX 必修 2）。轮询带 `--since/--message`，与结果不明核对同一协议。其余（真正
  手动发出、缺这些字段的旧状态、通道不支持 poll_since）仍是原 NO_REF：等人，同一轮第二次转为阻塞。定位
  轮询返回 `no_ref` 也按 NO_REF 处理。有界：none 走原落地核对上限（`landed_confirm_polls` 或
  `landed_timeout_s`）后 SEND_NOT_LANDED 阻塞；不发送、不 acquire/释放发送槽、不清外发标记。

## 未改变的约束

generation、lease、项目身份核对、锁内持久化与外发前标记、`_slot_bind` 收养与不借用规则、恢复期预算、
NO_REF 防循环（max 1 → block）与 `wf approve` 拒绝均不变。

## 测试

`python3 -m unittest -v wflib.selftest_b3_send`（9 项，临时 LOCAL、真实 fake_transport、真实子进程 SIGKILL；其中两项是 A-B3-SEND-FIX 的专属回归）；
`run_unittests(_ok)` 供聚合器注册，本卡不改聚合器。

## 边界

- 原租约已被回收时，仲裁开启的拦截仍会先得到一次新授予再立即释放（不外发、不写 `send_slot`）。完全不取得需要
  仲裁提供只重入、不新授的查询，建议接口：`arbiter.acquire(inst, mx, lease_s, reentry_only=True)`，
  无持有时返回 `granted=False, reason="no_hold"`。
- `_finalize_send` 第一次保存之前被杀时，发送日志可能多记一行（只会让限速更严）。
- 有完整 `last_sent.message`、`snapshot_stat` 与 `sent_ts` 的状态才能做 S03 定位；本修复之前（含 831a1de）
  留下的双缺状态没有这些字段，仍是 NO_REF。
- 双缺定位依赖适配器按 `--since/--message` 找到消息；真实通道的这一能力未在本卡验证。
