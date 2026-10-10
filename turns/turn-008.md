【回报 · 第 8 轮 · v0.1】档位：high

① 环境障碍与偏离
- [EXEC_FAIL] 命令执行失败。回报给 Claude。 第 11 条命令「grep -n kick /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py」退出码 1
- [EXEC_FAIL] 命令执行失败。回报给 Claude。 第 16 条命令「grep -n carry /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py」退出码 1
- 命令 c11 退出码 1：`grep -n kick /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py`
- 命令 c16 退出码 1：`grep -n carry /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py`

② 关键数字与终端原文

1) driver.py 第 773–826 行（h_execute）。 —— 通过
```
$ sed -n 773,826p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/driver.py
def h_execute(ctx):
    inst = ctx.inst
    st = inst.state
    it = read_json(inst.turn_dir() / "intake.json", {}) or {}
    d = it.get("directives") or {}
    cmds = it.get("commands", [])
    inflight = st.get("exec_inflight")
    if inflight:
        st.pop("exec_inflight", None)
        st["exec_next"] = inflight["i"] + 1
        inst.save()
        done = fidelity.exec_completed(ops.list_execs(inst), inflight["i"] + 1)
        if done is not None:
            # 1.5.0：命令其实已执行完（证据里有退出码），驱动器是在命令结束之后被打断的，不算命令中断（第 31 轮误报）
            inst.event("exec_resumed", cid=done.get("id"), exit_code=done.get("exit_code"),
                       note=f"第 {inflight['i'] + 1} 条命令已执行完毕，中断发生在命令结束之后", by="driver")
            if done.get("exit_code") not in (0, None):
                _anomaly(ctx, {"source": "exec", "code": done["exit_code"]},
                         f"第 {inflight['i'] + 1} 条命令「{inflight['cmd']}」退出码 {done['exit_code']}")
        else:
            _anomaly(ctx, {"source": "driver", "kind": "exec_interrupted"},
                     f"第 {inflight['i'] + 1} 条命令「{inflight['cmd']}」执行时驱动器被中断，未重跑")
        st = inst.state
    i = st.get("exec_next", 0)
    failed = False
    timeout = d.get("timeout") or ctx.pol.get("command_timeout_s", 900)
    while i < len(cmds):
        if ctx.time_left() <= 0:
            return "progress", f"已执行到第 {i} 条，本次时间用完，下次继续"
        if policy.classify(cmds[i], ctx.pol).get("manual"):
            ops.add_note(inst, "info", f"第 {i + 1} 条「{cmds[i]}」是手动步骤，驱动器未执行（F21）", by="driver")
            decide(inst, "exec", f"跳过第 {i + 1} 条：{cmds[i]}", "会改变工作流状态的 wf 命令只能在驱动器之外手动执行", "", "已跳过")
            i += 1
            st = inst.state
            st["exec_next"] = i
            inst.save()
            continue
        st["exec_inflight"] = {"i": i, "cmd": cmds[i], "ts": now_iso()}
        inst.save()
        rc = ops.run_exec(inst, cmds[i], label=f"#{i + 1}", timeout=timeout, by="driver")
        st = inst.state
        st.pop("exec_inflight", None)
        i += 1
        st["exec_next"] = i
        inst.save()
        if rc != 0:
            _anomaly(ctx, {"source": "exec", "code": rc}, f"第 {i} 条命令「{cmds[i - 1]}」退出码 {rc}")
            failed = True
            if not d.get("continue_on_error"):
                break
    machine.go(inst, "VERIFY", by="driver", reason="执行结束" + ("（有失败）" if failed else ""))
    return "progress", f"执行结束：{i} / {len(cmds)} 条" + ("，有失败" if failed else "")
exit=0  耗时 0.0s
```

2) goals.py 中 judge 的定义与判据语法。 —— 通过
```
$ grep -n -A 30 judge /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/goals.py
55:def judge(cond: dict, exit_code: int, out: str) -> tuple:
56-    """按条件判定命令结果。返回 (是否通过, 不通过的原因列表)。验收与目标共用。"""
57-    why = []
58-    if "exit" in cond and exit_code != cond["exit"]:
59-        why.append(f"退出码 {exit_code}，期望 {cond['exit']}")
60-    for s in cond.get("contains", []):
61-        if s not in out:
62-            why.append(f"输出不含“{s}”")
63-    for s in cond.get("not_contains", []):
64-        if s in out:
65-            why.append(f"输出含有“{s}”")
66-    for r in cond.get("regex", []):
67-        if not re.search(r, out, re.M):
68-            why.append(f"输出不匹配 /{r}/")
69-    return not why, why
70-
71-
72-def _confirmations(inst: Instance) -> dict:
73-    return (policy.approvals(inst).get("confirmations") or {})
74-
75-
76-def confirm(inst: Instance, gid: str, note: str) -> dict:
77-    g = load(inst)
78-    if not g or gid not in {x["id"] for x in g["goals"]}:
79-        raise WfError(f"没有目标 {gid}")
80-    x = next(x for x in g["goals"] if x["id"] == gid)
81-    if x["kind"] != "manual":
82-        raise WfError(f"{gid} 是可执行判据，由检查命令判定，不能人工确认")
83-    st = policy.status(inst, "goals")
84-    if not st["approved"]:
85-        raise WfError("目标尚未批准或批准后被改动，先 wf goals approve")
--
119:        ok, why = judge(x["pass"], code, out)
120-        ev = d / f"{stamp}-{x['id']}.txt"
121-        ev.write_text(f"$ {x['check']}\nexit={code}\n{out}", encoding="utf-8")
122-        gs[x["id"]] = {"ok": ok, "why": why, "ts": now_iso(), "exit": code, "evidence": str(ev.relative_to(inst.root)),
123-                       "duration_s": round(time.time() - t0, 1), "goals_sha": st["current"]}
124-        inst.event("goal_check", goal=x["id"], ok=ok, why="；".join(why) or None, by=by)
125-    write_json(inst.wf / "goals_state.json", gs)
126-    return progress(inst)
127-
128-
129-def progress(inst: Instance) -> dict:
130-    g = load(inst)
131-    st = policy.status(inst, "goals")
132-    if not g:
133-        return {"defined": False, "approved": False, "items": [], "done": 0, "total": 0, "reached": False}
134-    gs = read_json(inst.wf / "goals_state.json", {}) or {}
135-    conf = _confirmations(inst)
136-    items = []
137-    for x in g["goals"]:
138-        if x["kind"] == "exec":
139-            r = gs.get(x["id"]) or {}
140-            valid = r.get("goals_sha") == st["current"]
141-            ok = bool(r.get("ok")) and valid
142-            detail = ("通过" if ok else ("；".join(r.get("why", [])) or "未通过")) if valid and r else "尚未检查"
143-        else:
144-            c = conf.get(x["id"]) or {}
145-            ok = c.get("goals_sha") == st["current"] and st["approved"]
146-            detail = f"用户已确认：{c.get('note')}" if ok else "等待用户确认"
147-        items.append({"id": x["id"], "title": x["title"], "kind": x["kind"], "ok": ok, "detail": detail,
148-                      "means": x.get("means"), "on_fail": x.get("on_fail")})
149-    done = sum(1 for i in items if i["ok"])
exit=0  耗时 0.0s
```

3) release.py 第 21–48 行（_extract）。 —— 通过
```
$ sed -n 21,48p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/release.py
def _extract(zip_path: Path, dest: Path) -> Path:
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise WfError(f"找不到文件：{zip_path}")
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise WfError(f"不是有效的 zip 文件：{zip_path}")
    bad = zf.testzip()
    if bad:
        raise WfError(f"zip 内文件损坏：{bad}")
    tops = set()
    for n in zf.namelist():
        if n.startswith("/") or ".." in Path(n).parts:
            raise WfError(f"zip 中有越界路径，拒绝处理：{n}")
        tops.add(n.split("/")[0])
    tops.discard("__MACOSX")
    if len(tops) != 1:
        raise WfError(f"zip 应该只有一个顶层目录，实际有：{sorted(tops)}")
    zf.extractall(dest)
    # zipfile 不会恢复 Unix 权限：按 zip 中记录的权限补回（否则可执行脚本会丢失执行位）
    for info in zf.infolist():
        mode = (info.external_attr >> 16) & 0o777
        if mode and not info.is_dir():
            os.chmod(dest / info.filename, mode)
    return dest / tops.pop()
exit=0  耗时 0.0s
```

4) release.py 第 125–171 行（apply_zip）。 —— 通过
```
$ sed -n 125,171p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/release.py
def apply_zip(inst: Instance, zip_path, by="executor") -> dict:
    root = inst.root
    protect = inst.protect()
    if (root / ".git").exists():
        dirty = _dirty(root, protect)
        if dirty:
            raise WfError("工作区有未提交的改动，已停止（AP-2）：\n  " + "\n  ".join(dirty[:20]))
    with tempfile.TemporaryDirectory() as td:
        src = _extract(Path(zip_path), Path(td))
        if not (src / "PROJECT.md").exists():
            raise WfError("zip 中没有 PROJECT.md（AP-4）")
        p = parse_project_md((src / "PROJECT.md").read_text(encoding="utf-8"))
        cl = src / "CHANGELOG.md"
        issues = validate(p, cl.read_text(encoding="utf-8") if cl.exists() else None)
        if issues:
            raise WfError("PROJECT.md 校验未通过，未改动仓库（AP-1）：\n  - " + "\n  - ".join(issues))
        manifest = read_json(src / "RELEASE.json", {}) or {}
        tracked = set(git(["ls-files"], root).stdout.splitlines()) if (root / ".git").exists() else None
        has_head = tracked is not None and git(["rev-parse", "-q", "--verify", "HEAD"], root, check=False).returncode == 0
        cfg_before = json.loads(json.dumps(inst.cfg))
        written, deleted, cfg_keys = [], [], []
        rel_json = f"workflow/releases/{p['version']}.json"
        try:
            _copy_tree(src, root, protect, written=written)
            _delete(root, manifest.get("delete"), protect, done=deleted)
            if manifest.get("wf_config"):
                # 项目级工具配置随版本交付（wf.json 本身受保护，不会被 zip 覆盖），只合并允许的键
                for k, v in manifest["wf_config"].items():
                    if k in ("processes", "protect", "hook"):
                        inst.cfg[k] = v
                        cfg_keys.append(k)
                if cfg_keys:
                    write_json(inst.cfg_path, inst.cfg)
            if manifest:
                (root / "workflow" / "releases").mkdir(parents=True, exist_ok=True)
                shutil.copy2(src / "RELEASE.json", root / rel_json)
            head, changed = _commit(root, f"release {p['version']}", tag=p["version"])
        except Exception as e:
            note = _restore_after_failure(root, tracked, has_head, written, manifest.get("delete") or [],
                                          ["workflow/wf.json", rel_json], inst, cfg_before)
            raise WfError(f"落地失败：{e}\n{note}")
    res = {"version": p["version"], "commit": head, "changed": changed, "deleted": deleted, "config": cfg_keys,
           "hints": _restart_hints(inst, changed), "notes": manifest.get("notes")}
    inst.event("apply", version=p["version"], commit=head, changed=len(changed), deleted=deleted or None, by=by)
    return res
exit=0  耗时 0.0s
```

5) release.py 第 254–317 行（self_init、self_update）。 —— 通过
```
$ sed -n 254,317p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/release.py
def self_init() -> str:
    if not (WF_HOME / ".git").exists():
        git(["init", "-q"], WF_HOME)
    gi = WF_HOME / ".gitignore"
    if not gi.exists():
        gi.write_text("local/\n__pycache__/\n*.pyc\n", encoding="utf-8")
    (WF_HOME / "local").mkdir(exist_ok=True)
    v = tool_version()
    head, _ = _commit(WF_HOME, f"wf {v}", tag=f"wf-v{v}")
    return head or git(["rev-parse", "--short", "HEAD"], WF_HOME).stdout.strip()


def _vtuple(v: str):
    return tuple(int(x) for x in v.strip().split("."))


def self_update(zip_path, skip_test=False, allow_same=False) -> dict:
    if not (WF_HOME / ".git").exists():
        raise WfError("工具尚未初始化版本库，先运行 wf self-init")
    if _dirty(WF_HOME, SELF_PROTECT):
        raise WfError("工具目录有未提交的改动，已停止。用 git -C 工具目录 status 查看。")
    cur = tool_version()
    with tempfile.TemporaryDirectory() as td:
        src = _extract(Path(zip_path), Path(td))
        src_files = fidelity.tree_hashes(src)   # 1.5.0：落地后逐个核对，确认装上的就是这个版本包
        new = (src / "VERSION").read_text(encoding="utf-8").strip()
        if _vtuple(new) < _vtuple(cur) or (_vtuple(new) == _vtuple(cur) and not allow_same):
            raise WfError(f"新版本 {new} 不高于当前版本 {cur}。回到旧版本请用 wf self-rollback。")
        if not skip_test:
            env = dict(os.environ)
            env.pop("WF_LOCAL_DIR", None)
            r = subprocess.run([sys.executable, str(src / "wf.py"), "selftest"], capture_output=True, text=True,
                               env=env, timeout=600)
            if r.returncode != 0:
                raise WfError(f"新版本 {new} 的自检未通过，未安装：\n{r.stdout[-3000:]}\n{r.stderr[-2000:]}")
        manifest = read_json(src / "RELEASE.json", {}) or {}
        _copy_tree(src, WF_HOME, SELF_PROTECT)
        _delete(WF_HOME, manifest.get("delete"), SELF_PROTECT)
    # 落地后按实际安装的树重算清单，使它一定等于新版本——打包时忘了重算也不会让 self-check 失准（③-1）
    write_manifest()
    head, changed = _commit(WF_HOME, f"wf {new}", tag=f"wf-v{new}")
    from . import server
    board_was_running = bool((fidelity.safe_call(server.info, {}) or {}).get("pid"))
    restarted = _restart_board()
    # 1.5.0：升级本身要可验证（F25）——磁盘版本、文件与版本包逐个一致、看板以新版本运行；不通过就自动回滚
    receipt = fidelity.post_install_receipt(new, src_files, _tool_files(), board_was_running,
                                            lambda: fidelity.safe_call(server.info, {}), WF_HOME)
    if not receipt["ok"]:
        failed_tag = f"wf-v{new}-failed-{fidelity.stamp()}"
        git(["tag", failed_tag, f"wf-v{new}"], WF_HOME, check=False)
        git(["tag", "-d", f"wf-v{new}"], WF_HOME, check=False)
        try:
            self_rollback(f"wf-v{cur}")
            rb = f"已自动回滚到 {cur}；失败版本的标签改名为 {failed_tag}"
        except WfError as e:
            rb = f"自动回滚失败：{e}。请手动运行 wf self-rollback {cur}"
        raise WfError(f"升级到 {new} 后核验未通过：" + "；".join(receipt["problems"]) + "。" + rb)
    print(f"升级核验通过：磁盘版本 {receipt['version']}，{receipt['files_checked']} 个文件与版本包一致，"
          + (f"看板已加载 {new}" if board_was_running else "看板未在运行")
          + ("；" + "；".join(receipt["notes"]) if receipt["notes"] else ""), file=sys.stderr, flush=True)
    return {"from": cur, "to": new, "commit": head, "changed": changed, "tested": not skip_test,
            "board_restarted": restarted, "receipt": receipt}
exit=0  耗时 0.0s
```

6) release.py 第 349–430 行（工具文件清单、manifest、self_check）。 —— 通过
```
$ sed -n 349,430p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/release.py
def self_versions() -> list:
    if not (WF_HOME / ".git").exists():
        return []
    r = git(["for-each-ref", "--sort=-creatordate", "--format=%(refname:short)\t%(creatordate:iso8601)", "refs/tags/wf-v*"],
            WF_HOME, check=False)
    return [dict(zip(("tag", "date"), l.split("\t"))) for l in r.stdout.splitlines()]


# ---------------- 工具文件清单与自检（§7：两套交付的收尾）----------------

def _tool_files() -> dict:
    import hashlib
    out = {}
    for f in sorted(WF_HOME.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(WF_HOME)
        parts = set(rel.parts)
        if parts & {".git", "local", "__pycache__"} or f.suffix == ".pyc" or str(rel) == "MANIFEST.json":
            continue
        out[str(rel).replace(os.sep, "/")] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def write_manifest() -> Path:
    p = WF_HOME / "MANIFEST.json"
    write_json(p, {"version": tool_version(), "files": _tool_files()})
    return p


def self_check() -> dict:
    """对照随版本发布的 MANIFEST.json：多出的文件（可能是旧交付的残留，或放错位置的通道适配器）、缺失的、被改动的。只列出，不删除。"""
    m = read_json(WF_HOME / "MANIFEST.json")
    if not m:
        return {"ok": False, "error": "没有 MANIFEST.json（1.4.0 之前的版本不带清单）"}
    now_files = _tool_files()
    want = m.get("files", {})
    extra = sorted(set(now_files) - set(want))
    missing = sorted(set(want) - set(now_files))
    changed = sorted(k for k in set(want) & set(now_files) if want[k] != now_files[k])
    return {"ok": not (extra or missing or changed), "version": m.get("version"), "extra": extra,
            "missing": missing, "changed": changed}
exit=0  耗时 0.0s
```

7) 工具 CHANGELOG.md 的开头 30 行。 —— 通过
```
$ sed -n 1,30p /Users/admin/.workbuddy-ai/tools/collab-wf/CHANGELOG.md
# CHANGELOG（collab-wf）

## 1.5.1 — 2026-10-10

发送结果不明这条因果链（第 40 轮事故）与 1.5.0 安装后发现的问题：
- 适配器超时：先 SIGTERM，宽限期（transport.json 的 grace_s，默认 15 秒）后才 SIGKILL；通过 WF_ADAPTER_TIMEOUT_S 告知适配器时间上限。
- 发送结果不明：新增 SEND_UNKNOWN_WAIT（policy 的 send_unknown_wait 开启时生效），约一小时内反复轮询确认，绝不自动重发；可选 poll --since/--message 定位没有 ref 的消息（transport.json 的 poll_since）。
- 新增 wf resend：确认没有送达时，凭决定码与用户原话清除发送意图并重发，取代手改 state.json。
- SEND_BUSY、SEND_UNKNOWN 与 TRANSPORT.md 的说明写入第 40 轮教训：通道报忙最可能是自己的消息在生成；会话无变化不能证明没有在生成。
- 证据关联：驱动器执行的命令只按 label 对应，跳过的命令不再导致错配（第 44 轮）。
- wf self-check 归为只读命令；wf note 在回报生成之后写入时提示去向；说明类备注进入回报。
- wf resume 在等待命令批准时提示改用 wf approve（由驱动器推进时 resume 会原地打转）。
- TRANSPORT.md：空 ref 时 poll 返回 no_ref，不返回 none。
- 自检新增 11 项（超时宽限、确认重发、只读命令、备注提示等）。

## 1.5.0 — 2026-10-10

可靠性与可验证性（btc-quant 第 29–40 轮实测暴露的问题）：
- 回报保真：暂缓项与未关联命令也附原文；原文按约 32000 字符的预算完整嵌入，不再只取末尾 60 行；每段只出现一次；放不下的分片续传（workflow/outbox/carry.json）；原文含反引号时外层围栏自动加长。
- 解析容错：块标题须位于行首，正文中提到块名不再顶掉命令块；驱动指令容忍行首编号、“本轮之后”与加粗；指令块有内容却解析不出键、清单编号不连续时，写进“需要注意”。
- 回复归属：poll 可返回 reply_parent，与本轮 send_ref 不符时不收下、继续轮询（TRANSPORT.md 末节）。
- 执行接续：被打断的那条命令已有退出码时，不再误报 EXEC_INTERRUPTED。
- 事件与体检：先写状态再追加事件，杜绝重号；wf doctor 检查事件序号、额度空值连续数、回复归属与续传队列；额度有效期统一为 10 分钟。
- 升级核验：wf self-update 安装后核对版本、文件与看板，不通过就自动回滚并改名失败标签（从 1.5.0 升级到之后的版本时生效）。
- 桥接自检：poll 为合法动作；被拒后重发不算重叠发送。
- 叶子被拒：重发次数可配置（policy.max_leaf_refusals，默认 2 保持原行为，建议 0）。
- 无附件续接：wf bootstrap 附工具与项目的文件清单。
- 新增 wflib/fidelity.py（纯函数）与 wflib/selftest_v15.py（新增检查，计入 wf selftest 总数）；原有检查没有删改。
- 交付方式：补丁构建器（锚点逐处核对，任何一处不符就整体中止）在工具仓库的克隆上生成版本包并先跑自检。
exit=0  耗时 0.0s
```

8) wf.py 中 selftest 的调用位置。 —— 通过
```
$ grep -n selftest /Users/admin/.workbuddy-ai/tools/collab-wf/wf.py
68:  wf version | wf self-init | wf self-update <zip> | wf self-rollback <版本> | wf selftest
332:    sub.add_parser("selftest")
902:    elif c == "selftest":
903:        from wflib.selftest import run
exit=0  耗时 0.0s
```

9) selftest.py 中调起 selftest_v15 的位置；没有匹配也照原样回报。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n selftest_v15 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py
87:    from .selftest_v15 import run_v15   # 1.5.0 新增检查：纯函数与解析回归，计入总数
exit=0  耗时 0.0s
```

10) 自检中涉及 kick 的断言（selftest_driver.py）；同上。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n kick /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py
73:    _wf(env, p, "kick", "--message", str(task))
151:    _wf(env, q, "kick", "--message", str(task))
229:    _wf(env, g, "kick", "--message", str(task))
249:    _wf(env, c, "kick", "--message", str(task))
268:    _wf(env, r, "kick", "--message", str(task))
284:    _wf(env, q, "kick", "--message", str(task))
301:    _wf(env, m, "kick", "--message", str(task))
395:    _wf(env, h, "kick", "--message", str(task))
401:    _wf(env, r, "kick", "--message", str(task))
415:    _wf(env, nl, "kick", "--message", str(task))
507:    _wf(env, q, "kick", "--message", str(t))
exit=0  耗时 0.0s
```

11) 同上（selftest.py）。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n kick /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py
exit=1  耗时 0.0s
```

12) 自检中涉及 consecutive_failed 的断言；同上。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n consecutive_failed /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py
55:        "max_consecutive_failed_rounds": 3, "command_timeout_s": 60}))
124:        and _state(p).get("consecutive_failed") == 1, o)
exit=0  耗时 0.0s
```

13) 自检中直接读写 state.json 的位置（selftest_driver.py）；同上。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n state.json /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py
152:    st = json.loads((q / "workflow" / "state.json").read_text())
155:    (q / "workflow" / "state.json").write_text(json.dumps(st))
163:    st = json.loads((q / "workflow" / "state.json").read_text())
166:    (q / "workflow" / "state.json").write_text(json.dumps(st))
172:    st = json.loads((q / "workflow" / "state.json").read_text())
179:    (q / "workflow" / "state.json").write_text(json.dumps(st))
328:    st = json.loads((h2 / "workflow" / "state.json").read_text())
exit=0  耗时 0.0s
```

14) 同上（selftest.py）。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n state.json /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py
46:    return json.loads((proj / "workflow" / "state.json").read_text(encoding="utf-8"))
135:                               "workflow/state.json": "{\"evil\": true}"})
139:            and "evil" not in (proj / "workflow" / "state.json").read_text()
exit=0  耗时 0.0s
```

15) 自检里回复样本中"需要用户决定"块的写法；同上。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n 需要用户决定 /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py
30:            f"【需要用户决定】\n无\n\n【下一轮建议档位】{effort}\n\n【驱动指令】\n{directives}\n")
exit=0  耗时 0.0s
```

16) selftest.py 中涉及续传的断言；同上。 —— 暂缓：没有机器判据，原文见回报，由 Claude 判断
```
$ grep -n carry /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py
exit=1  耗时 0.0s
```

③ 需要决策的点
无。

④ 需要注意（Attention）
- 自动处置 EXEC_FAIL → report：第 11 条命令「grep -n kick /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py」退出码 1
- 自动处置 EXEC_FAIL → report：第 16 条命令「grep -n carry /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest.py」退出码 1
- 清单第 9, 10, 11, 12, 13, 14, 15, 16 项没有机器判据，结论需要 Claude 根据原文判断
- 当前对话已累计约 148188 字符、7 轮（按已发出的消息计，不含正在组织的这一条与手动发出的续接说明）

附：仓库指纹 4d1aa87b9b11c651（v0.1，4 个文件）
附：发送前额度 5 小时窗口 39.0%，7 天窗口 60.0%
