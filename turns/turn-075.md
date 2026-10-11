【独立看门狗告警处置：完成通知不应当报待办积压】

你是Claude方案与代码作者，WorkBuddy执行端只负责本机核验、安装与实测。本轮是用户要求处理WATCHDOG异常的正式新任务，不是重启M1累计实验，不是代用户最终验收，不改goals/policy/floor/项目版本或教程。

已核实本机事实：工具1.6.6、collab-wf-dev v2.6、指纹b848fba7f3776a69。第74轮09:06:59 CST到DONE，当前最新tick仍exit30/done，无inflight，连续失败0。wf --json ask返回items=[]、notices=[{key:done:74,title:目标已达成,why:全部目标判据已通过，自动化停在这里}]。所有已登记执行端任务done。inbox只有collab-wf-dev|done:74，for=user、action/code均为空、since=2026-10-11T01:07:07+00:00。工具内watchdog findings=[]；执行端补充检查在09:37将此项报high inbox-backlog。

根因：执行端watchdog.py的check_project无差别把所有inbox项计入积压，且每项目检查都会读取全局inbox，多个项目时重复计算。03:22对done:71已诊断但未修，本次done:74重现。asks的mark-shown只影响通知呈现，不会从inbox移除notice，因此不能靠清inbox/标已读/伪造确认掩盖。

请提供针对执行端wrapper的最小可应用补丁或替换函数（下载文件），以及标准库回归测试。不要改任何网络/浏览器会话/通知投递实现，也无需审读这些实现。本轮只定义和修复本机待办分类及去重。完整相关源码会附在本文后。

要求：
1. 精确排除已知正常完成notice（形如项目|done:<轮次>，无action/code）。不能简单排除所有for=user、所有无action/code项（外部严重信号也可能无action/code），不能按中文title匹配。
2. quota普通等待需独立判断其正常通知性质，不把所有quota相关故障一起隐藏；若本轮不处理quota请明确范围。
3. 真实批准项、执行端task、外部signal、底线、BLOCKED都继续保留，未知结构fail-visible。
4. 按project准确筛选，避免全局inbox被每个项目重复计入；无project信号必须有明确只检查一次的策略。若分开全局check_inbox与check_project，给准确完整替换及collect调整，不削弱其他现有检查。
5. 不手改inbox/state，不禁用watchdog，不清seen作为修复，不伪造最终用户验收。
6. 测试覆盖done通知超过30分钟不报；真实user批准超过30分钟报；executor task报；signal无action/code报；多项目不重复；无project signal仍可见；完成notice带action/code的异常结构不能被忽略；已有jobs/tick/native检查保持。
7. 补丁需基于本文给出的实际源码，给出原文件SHA绑定、修改说明、测试原文；不要执行整个watchdog投递真实告警，测试mock读文件/时间/外部调用。
8. 不修改其他业务；不将M1 7/7误当教程r3验收。第74轮r3已生成但执行端尚未本机验收。

交付正文：本轮结论、实际补丁与测试文件路径和SHA、仅容器测试/待本机边界、本机执行（只读wf goals/wf fingerprint）。驱动指令发布无、之后空闲；本轮不要列执行端待办（现有DONE优先会漏登记），执行端本会话接收后立即落实。


实际watchdog.py SHA256: f3eabf7785dedf1e62d324a109987d73de7eac56bfeb35f352760e184c21c13c
已存在依赖：json/os/time/datetime；LOCAL/EXPECTED_JOBS等常量与run_tool_watchdog/suppress_native_duplicates保留。main的--status与正常巡检均调用collect()，正常投递前复检也调用collect()。下列实际源码仅本机检查，不含通道实现：
```python
def parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def age_of(s):
    t = parse_ts(s)
    return None if t is None else now() - t


def mins(sec):
    return int(sec // 60)


def fmt_age(sec):
    if sec is None:
        return "未知"
    if sec < 60:
        return "%d 秒" % int(sec)
    if sec < 3600:
        return "%d 分钟" % mins(sec)
    return "%.1f 小时" % (sec / 3600.0)


# ── 各项检查：每个返回 (key, 严重度, 人话描述) ────────────────────────────────

def check_jobs():
    out = []
    try:
        raw = subprocess.run(["launchctl", "list"], capture_output=True,
                             text=True, timeout=20).stdout
    except Exception as e:
        return [("launchctl", "high", "读不到 launchctl 列表：%s" % e)]
    loaded = set()
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2].startswith("local.collab-wf"):
            loaded.add(parts[2])
    for j in EXPECTED_JOBS:
        if j not in loaded:
            out.append(("job-missing:" + j, "high",
                        "环节掉了：launchd 代理 %s 未加载（launchctl list 里没有）" % j))
    return out


def check_tick_log():
    p = os.path.join(LOCAL, "tick.log")
    try:
        a = now() - os.path.getmtime(p)
    except Exception:
        return [("ticklog-missing", "high", "找不到驱动器日志 %s" % p)]
    if a > TICK_LOG_STALE_S:
        return [("ticklog-stale", "high",
                 "驱动器已 %s 没有任何动作（tick.log 最后写入于 %s 前）"
                 % (fmt_age(a), fmt_age(a)))]
    return []


def check_project(name, root):
    out = []
    sp = os.path.join(root, "workflow", "state.json")
    st = read_json(sp, None)
    if not isinstance(st, dict):
        out.append(("state-unreadable:" + name, "high",
                    "读不出 %s 的状态文件（%s）—— 驱动器可能根本没起来过" % (name, sp)))
        return out

    turn, state = st.get("turn"), st.get("state")

    infl = ((st.get("driver") or {}).get("inflight") or {})
    if infl.get("started"):
        a = age_of(infl["started"])
        if a is not None and a > INFLIGHT_STALE_S:
            out.append(("inflight:" + name, "high",
                        "驱动器有个子进程卡住 %s（pid=%s，第 %s 轮 %s）"
                        % (fmt_age(a), infl.get("pid"), turn, state)))

    pa = st.get("poll_after")
    if pa:
        a = age_of(pa)
        if a is not None and a > POLL_LATE_S:
            out.append(("poll-late:" + name, "high",
                        "该轮询的时间已过 %s 仍未轮询（第 %s 轮 %s）—— 驱动器可能停摆了"
                        % (fmt_age(a), turn, state)))

    # 阈值同样对齐工具自己的 policy（教训 ㊻）：工具在 landed_confirm_polls 次核对失败后
    # 才判 SEND_NOT_LANDED 并停下，所以这里按 75% 提前预警，不要自己拍绝对值。
    pol = read_json(os.path.join(root, "workflow", "policy.json"), {}) or {}
    try:
        landed_polls = int(pol.get("landed_confirm_polls") or 5)
    except Exception:
        landed_polls = 5
    su_warn = max(2, int(landed_polls * 0.75 + 0.5))     # 5 → 4
    su = st.get("send_unconfirmed") or 0
    if su >= su_warn:
        out.append(("send-unknown:" + name, "high",
                    "「发送结果不明」已连续 %s 次核对未确认（第 %s 轮）—— 消息可能没送达；"
                    "工具的判定线是 %s 次" % (su, turn, landed_polls)))

    if (st.get("consecutive_failed") or 0) >= 2:
        out.append(("fail-streak:" + name, "high",
                    "驱动器连续 %s 轮失败（第 %s 轮 %s）"
                    % (st.get("consecutive_failed"), turn, state)))

    # 这个计数是工具自己算的：只有「本轮通过的目标数超过历史最好值」才清零，否则 +1。
    # 阈值必须对齐工具自己的熔断线（policy.max_rounds_without_goal_progress），
    # 否则会在熔断前好几轮就误报。特别注意：当剩余目标是「连续 N 轮无人工动作」这类
    # **累积型判据**时，这个计数上涨恰恰是正常稳态，不是故障（2026-10-10 23:45 实测）。
    pol = read_json(os.path.join(root, "workflow", "policy.json"), {}) or {}
    try:
        np_limit = int(pol.get("max_rounds_without_goal_progress") or 8)
    except Exception:
        np_limit = 8
    np_warn = max(3, int(np_limit * 0.75 + 0.5))     # 12 → 9，留出处置余量
    npr = st.get("rounds_no_progress") or 0
    if npr >= np_warn:
        out.append(("no-progress:" + name, "mid",
                    "已连续 %s 轮目标没有进展（第 %s 轮 %s）—— 工具的熔断线是 %s 轮，还差 %s 轮"
                    % (npr, turn, state, np_limit, np_limit - npr)))

    if state in ("BLOCKED", "AWAITING_USER") and (age_of(st.get("since")) or 0) > INBOX_BACKLOG_S:
        out.append(("stuck-needs-human:" + name, "mid",
                    "第 %s 轮停在 %s 已 %s，没人处理（需要人/执行端）"
                    % (turn, state, fmt_age(age_of(st.get("since"))))))

    inbox = read_json(os.path.join(LOCAL, "inbox.json"), {})
    items = [x for x in (inbox.get("items") or []) if isinstance(x, dict)]
    if items:
        ages = [age_of(x.get("since")) for x in items]
        ages = [a for a in ages if a is not None]
        if ages and max(ages) > INBOX_BACKLOG_S:
            who = {}
            for x in items:
                who[x.get("for") or "?"] = who.get(x.get("for") or "?", 0) + 1
            out.append(("inbox-backlog", "high",
                        "收件箱有 %d 件待办积压最久已 %s 没人处理（%s）"
                        % (len(items), fmt_age(max(ages)),
                           "、".join("%s %d 件" % (k, v) for k, v in sorted(who.items())))))
    return out


def check_locks(registered):
    out = []
    d = os.path.join(LOCAL, "locks")
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        # `*.state.lock` 只在创建时写一次、之后靠 flock 持有，mtime 不更新
        # → 按"没被动过"判卡死会稳定误报，直接不看它（真卡死由 tick.log / poll / inflight 覆盖）
        if name.endswith(".state.lock"):
            continue
        p = os.path.join(d, name)
        try:
            a = now() - os.path.getmtime(p)
        except Exception:
            continue
        if a <= LOCK_STALE_S:
            continue
        proj = name.split(".lock")[0]
        if proj in registered:
            out.append(("lock-stale:" + name, "mid",
                        "锁文件 %s 已 %s 没被动过 —— 驱动器可能挂在持有它的那一步"
                        % (name, fmt_age(a))))
        else:
            out.append(("lock-leftover:" + name, "low",
                        "遗留锁文件 %s（所属项目 %s 已不在注册表里），已 %s —— 建议清理"
                        % (name, proj, fmt_age(a))))
    return out


def collect():
    reg = read_json(os.path.join(LOCAL, "registry.json"), {})
    projects = [p for p in (reg.get("projects") or []) if isinstance(p, dict)]
    registered = {p.get("name") for p in projects}
    findings = []
    findings += check_jobs()
    findings += check_tick_log()
    for p in projects:
        findings += check_project(p.get("name") or "?", p.get("root") or "")
    findings += check_locks(registered)
    return findings
```
