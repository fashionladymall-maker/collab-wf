【改进批次一续接 · 第83轮任务】
档位：high

用户最新要求：继续完成全部遗留项，丝滑顺畅运行。请按第82轮计划推进，不把旧M1 7/7等同本批改进项全部完成。

一、已确认环境与第82轮事实
工具原1.6.6，本机已成功构建1.6.7：216/216、61受控文件、floor.py未变。build_wf_167.py为17477B，sha256=d30e0cfd3c6b12ba9ec02f69a9dcfc965c7875e82579ecc7848d12e8d1ed8795；本机zip sha256=94f2f958b272f70ba333dc1ae6372719fa17e9e08e0351bc3db0c9658e189fa3。安装、六项验收和T82状态以随后附上的实时证据为准，不能只引用作者容器结果。
独立审查已实际读完整zip/diff，核验两sha和61项清单：concerns。注意本机包夹入16个0字节local/locks自检锁文件，全部普通空文件，无凭据或执行载荷，安装SELF_PROTECT不安装它们，故不阻断；下一构建器必须在自检后打包过滤local/.git/__pycache__/pyc并核验成员集合。当前正式审查请求860a4e43-3578-4a81-a76d-c2982e8b47bd，工具kind=tool，turn=0。

二、发送恢复纠错（只报告现象与归属，不请你审读通道代码）
第82轮原请求在结果不明时被执行端误判为未送达并提前代决重发。后续证据表明原回复迟到后已完成，重发回复明确识别重复并复用同一构建器。执行端已写偏离记录，承认一次重复请求；后续不再因暂时不可见提前短路正式等待窗口。采用本轮归属的最新完整回复、未落地两套不同代码。请记录这个限制，不宣称恢复完全无损。

三、本轮任务与验收
1.P1-C：请交付可执行的r5补丁或完整HTML+独立数值回归脚本。执行端选你提出的明确量化方向，但必须忠于原始单位。现有页面金额输入与simulate金额单位为“万美元”，不是美元；1万美元=10000美元=1000000美分。反例queue0=5e-10对应0.000005美元，需据此写入口提示，不把118万美元误说118美元。可保留页面万美元展示，内部单位与接口需明确，费率输入%/基点也要一致。R1-R8必须补全具体期望值，旧60测试不得仅声称通过，要交可执行测试。
原LIMIT=1e9（万美元），最大期数60。内部单个金额最多1e15分，多期累计可能超过Number.MAX_SAFE_INTEGER；费率乘法中间数也可能超安全整数。请设计明确的精度保证/范围校验并写边界测试，不能用整数表象+浮点不安全运算宣称===守恒。科学计数法、0.004/0.005等边界必须按原单位测试。不足分舍入可见，不能在内部静默抹债权。页面现fmt.toFixed(2)的万美元显示会把1美元以下金额显示成0.00，即使内部修好也会掩盖1美分队列；请以足够精度（万美元至少6位小数或附精确美元/分）展示量化后值、舍入清单与队列，确保任何正整分债权不会显示成无欠款。旧“理论净收益≠已收现金”保留。
2.P0-A：用新进程1.6.7在真实下一轮回归，回复【驱动指令】继续列执行端待办：验证DONE仍可登记T83-N并进inbox、状态仍DONE、完成后提示可见。升级当轮旧内存行为如实区分。
3.P1-B：项目v2.7仅文档包，补记67-82事实、M1限制、真实看门狗与Papertrade验收、报告CONDITIONAL、1.6.7及审查concerns、迟到恢复误判。工具1.6.6实际安装turn58、turn59回报，不沿用上一轮误写。
4.下轮1.6.8优先修你发现的“本轮验收失败仍静默DONE”，同时P2-E墙钟计时；要求失败不被旧目标达成吞掉、保留任务登记和结果不明不重发。P1-D正式驱动器演练仍须交步骤/安全隔离，不拿示例CLI替身等同真实adapter消费。
5.另请在1.6.8或明确紧接的下一小版本修R3在发送飞行中误报缺结果：ops.bridge_check 603-650，send之后插入poll/quota而暂未accepted/error时即报错，没有宽限秒数。复现本机日志原第1171行；不要删R3或无限豁免，需待发送超合理窗口才报警，保留过期缺结果检测，提供时钟/终态/新send/过滤窗口边界测试。本机可复用wf bridge-check --log <日志> --hours 6；hours过滤不是飞行宽限。
6.P2-F无会话退化/多项目并行演练仍列后续，不以设计当实景通过；真实审查适配器有硬编码目录，WF_LOCAL_DIR不足以隔离，需考虑信号副作用、演练项目不出公网或被生产tick误发、失败包不落地、恢复不删除证据。

四、安全与交付约束
业务方案和代码由你出，执行端本机落地与独立验收；不改floor.py，不涉及真钱、凭据处置、签名、实盘；凭据候选处置与M1最终确认仍由用户本人，不阻断其他任务。只下载附件，不上传。当前项目指纹b848fba7f3776a69，项目v2.6；真实环境macOS+launchd+WorkBuddy AI国际版。固定作者Opus5.5。每个交付列文件大小/sha；回复六块完整，清楚列交付、未完成、下一步；不要把修复设计冒充实现通过。

五、原始代码与UI单位（逐字抽取）
UI原文：
起始 LP 现金（万美元）；起始队列欠款（万美元）；每期亏损现金进池（万美元）；每期赢家应得 W（折减后、赢利费前，万美元）；费率（%）；期数。
var LIMIT = 1e9;   /* 金额上限（模拟里单位是万美元），防止溢出 */
function fin(x){ return typeof x === "number" && isFinite(x); }

```javascript
  function simulate(o){
    var keys = ["cash0", "queue0", "L", "W", "f", "n"];
    for (var i = 0; i < keys.length; i++){ if (!fin(o[keys[i]])) return {ok: false, reason: "所有输入都必须是有限数字，不能为空"}; }
    if (o.cash0 < 0 || o.queue0 < 0 || o.L < 0 || o.W < 0) return {ok: false, reason: "金额不能为负"};
    if (o.cash0 > LIMIT || o.queue0 > LIMIT || o.L > LIMIT || o.W > LIMIT) return {ok: false, reason: "金额超出演示上限（每项 ≤ 10 亿万美元）"};
    if (o.f < 0 || o.f > 0.5) return {ok: false, reason: "费率必须在 0%–50% 之间"};
    if (o.n !== Math.floor(o.n) || o.n < 1 || o.n > 60) return {ok: false, reason: "期数必须是 1–60 的整数"};
    var cash = o.cash0, queue = o.queue0, rows = [], maxRes = 0, maxQRes = 0, tot = {paid: 0, fee: 0, claims: 0};
    var EPS = 1e-9;
    for (var t = 1; t <= o.n; t++){
      var c0 = cash, q0 = queue, paid, fee, newClaim, branch;
      cash = cash + o.L;
      if (q0 <= EPS && cash >= o.W + o.f * o.L){
        branch = "收费"; queue = 0;
        newClaim = (1 - o.f) * o.W; paid = newClaim; fee = o.f * o.W + o.f * o.L;
        cash = cash - paid - fee;
      } else {
        branch = "队列优先";
        newClaim = (1 - o.f) * o.W; fee = 0;
        var owed = queue + newClaim; paid = Math.min(cash, owed);
        cash = cash - paid; queue = owed - paid;
        if (queue < EPS) queue = 0;
      }
      if (cash < 0 && cash > -EPS) cash = 0;
      var overflow = 0;
      var res = (c0 + o.L) - (cash + paid + fee + overflow);
      var qres = (q0 + newClaim) - (queue + paid);
      if (!fin(cash) || !fin(queue) || !fin(res)) return {ok: false, reason: "计算出现非有限数值，已停止"};
      var scaleRef = Math.max(1, c0 + o.L, q0 + newClaim);
      maxRes = Math.max(maxRes, Math.abs(res) / scaleRef); maxQRes = Math.max(maxQRes, Math.abs(qres) / scaleRef);
      tot.paid += paid; tot.fee += fee; tot.claims += newClaim;
      rows.push({t: t, branch: branch, cashStart: c0, inflow: o.L, paid: paid, fee: fee, cashEnd: cash, queueEnd: queue, residual: res, qResidual: qres});
    }
    return {ok: true, rows: rows, cash: cash, queue: queue, totals: tot, maxRelResidual: maxRes, maxRelQResidual: maxQRes};
  }
```

```javascript
  function num(id){ var el = document.getElementById(id); var s = String(el.value).trim(); return s === "" ? NaN : Number(s); }
  function put(box, rows, note, isErr){
    box.textContent = ""; box.setAttribute("role", isErr ? "alert" : "status");
    box.style.borderColor = isErr ? "#B91C1C" : ""; box.style.background = isErr ? "#FFF5F5" : "";
    rows.forEach(function(r){ var d = document.createElement("div"); var s = document.createElement("span"); s.textContent = r[0]; var b = document.createElement("b"); b.textContent = r[1]; d.appendChild(s); d.appendChild(b); box.appendChild(d); });
    if (note){ var n = document.createElement("div"); n.className = "hint"; n.textContent = note; box.appendChild(n); }
  }
  function fmt(x){ return (Math.abs(x) < 5e-10 ? 0 : x).toFixed(2); }
  function sim(){
    var out = document.getElementById("sim_out"), tab = document.getElementById("sim_tab");
    while (tab.rows.length > 1) tab.deleteRow(1);
    var fp = num("s_f");
    var r = PTModel.simulate({cash0: num("s_c"), queue0: num("s_q"), L: num("s_l"), W: num("s_w"), f: fp / 100, n: num("s_n")});
    if (!r.ok){ put(out, [["无法模拟：", r.reason]], "请修改输入。本模拟不会把空白或非法输入当作 0。", true); return; }
    r.rows.forEach(function(x){
      var row = tab.insertRow(-1);
      [String(x.t), x.branch, fmt(x.cashStart), fmt(x.inflow), fmt(x.paid), fmt(x.fee), fmt(x.cashEnd), fmt(x.queueEnd), x.residual.toExponential(1)].forEach(function(v){ row.insertCell(-1).textContent = v; });
    });
    var W = num("s_w"), L = num("s_l"), f = fp / 100;
    put(out, [
      ["期末 LP 现金：", fmt(r.cash) + " 万美元"],
      ["期末队列欠款：", fmt(r.queue) + " 万美元"],
      ["累计赢家实收：", fmt(r.totals.paid) + " 万美元；累计费用流出：" + fmt(r.totals.fee) + " 万美元"],
      ["现金守恒最大相对残差：", r.maxRelResidual.toExponential(2) + "；债权守恒：" + r.maxRelQResidual.toExponential(2)],
      ["判断：", r.queue > 0 ? ((1 - f) * W > L ? "每期新增赢家债权大于亏损进池，队列会持续变长" : "队列还在，正在用后续亏损偿还") : "没有欠款排队"]
    ], "教学模拟：高度简化，不是该协议的真实算法或表现；铸造 PAPER 不改变这里的任何现金数字。");
  }
  ["s_c", "s_q", "s_l", "s_w", "s_f", "s_n"].forEach(function(id){ document.getElementById(id).addEventListener("input", sim); });
  sim();
```

六、第82轮安装、六项验收、待办与状态的实时证据（2026-10-12 00:05 CST核实）
- 实际轮次82，六条命令exit均0、verify.json六项均pass。
- c01构建112.0s：216/216、61文件、底线未变，本机zip sha如前。
- c02升级185.6s：工具已升级1.6.6→1.6.7（001ae83）；stderr“升级核验通过：磁盘版本1.6.7，61个文件与版本包一致，看板已加载1.6.7”；独立审查concerns真实completed。
- c03版本1.6.7；c04清单完全一致；c05目标7/7；c06指纹b848fba7f3776a69（7文件）。
- 第82轮16:04:45Z DONE；wf task list没有T82-1，当前state.executor_tasks只有历史已完成T59/T60。符合验收仍由旧内存1.6.6驱动器执行的已说明限制，不声称真实主项目DONE待办已闭环。
- 本机独立读原始c*.json/out/err并再次self-check：完全一致。新安装版自检已覆盖登记4项，但正式主项目真实登记应第83轮检验。
- 全局inbox读取时updated16:03:41Z、items空，属于滞后快照；权威wf task/当前state确认无T82-1，不把旧inbox当最终实时事实。
- 以上回报按作者“执行端1”继续，不等待用户操作。当前P0-A代码及机器自检已落地；真实下一轮闭环未完成。P1-C仍只有方案、未改r4。
