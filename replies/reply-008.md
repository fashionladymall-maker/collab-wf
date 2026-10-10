【本轮结论】
1. 本轮两条退出码 1 都是"无匹配"，结论也是无匹配：selftest.py 里没有 kick 和续传相关的断言。连续失败计数现在是 1；下一轮只放 sed 和 cat，会归零。
2. W21 的方案最终确定：
   - 新增 `_exec_ok`：有判据的命令按 goals.judge 判定（judge 支持 exit=N，所以"预期无匹配"可以写成 exit=1）；没有判据的命令按退出码判定。
   - h_execute 用它决定要不要记 EXEC_FAIL、要不要停止执行。
   - h_verify 和 _to_report 判断整轮成败时，只把"没关联到任何清单项、且退出码非 0"的命令算失败；清单项本身按判据判定，暂缓项不否决整轮。
   
   要确认的一点：selftest_driver.py:124 断言了一轮失败后 consecutive_failed == 1。如果那个场景用的是"没写判据、靠退出码判失败"，新规则会改变它的结果。本轮先读原文，再决定是保留这条断言还是在构建器里一并改写，并在摘要里写明。
3. 构建器的打包方式已从 release.py 原文确定：
   - zip 只能有一个顶层目录，并带 VERSION。
   - self-update 会先用新版本跑 wf selftest，安装后再按 tree_hashes 逐个核对文件，所以版本包不放 MANIFEST.json、local/、.git 和 __pycache__。
   
   构建器的步骤：
   - 克隆工具仓库，核对克隆在 wf-v1.5.1。
   - 逐个核对要改的文件，sha256 必须与清单一致。
   - 应用锚点：每处恰好命中一次，否则整体中止。
   - 写 VERSION 和 CHANGELOG，编译全部 .py。
   - 跑 wf selftest，跑时用临时的 WF_LOCAL_DIR 隔离本机数据，不碰本机注册表。
   - 全部通过才打包。
4. 项目版本包的格式也确定了：单个顶层目录，里面只放改动的文件。v0.2 只含 PROJECT.md 和 CHANGELOG.md。落地前 apply_zip 会用 projectmd.validate 校验，所以本轮也读 projectmd.py，确保 v0.2 一次落地成功。
5. 自检里直接写 state.json 的地方都是测试进程在 wf 子进程之外写文件，与 W18 的合并语义不冲突。新用例写进 selftest_v15.py 的 run_v15（selftest.py:87 调起它），计入总数，原有检查不动。
6. 下一轮交付两样东西：1.5.2 构建器（W18、W19、W20、W21、W15、seq_check、conv_new、文档措辞），以及 v0.2 项目版本包。

【本机执行】
```
sed -n 1,135p /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_driver.py
cat /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/selftest_v15.py
cat /Users/admin/.workbuddy-ai/tools/collab-wf/wflib/projectmd.py
```

【本轮回报清单】
1. selftest_driver.py 第 1–135 行（测试脚手架与 consecutive_failed 那一例）。
2. selftest_v15.py 全文。
3. projectmd.py 全文。

【需要用户决定】
无

【下一轮建议档位】
high

【驱动指令】
发布：无
之后：回报
验收 1：c1 exit=0 包含 "consecutive_failed"
验收 2：c2 exit=0 包含 "def run_v15"
验收 3：c3 exit=0 包含 "def validate"
失败后继续：是