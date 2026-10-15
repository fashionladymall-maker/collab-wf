"use strict";
const OLD_SIMULATE = "  function simulate(o){\n    var keys = [\"cash0\", \"queue0\", \"L\", \"W\", \"f\", \"n\"];\n    for (var i = 0; i < keys.length; i++){ if (!fin(o[keys[i]])) return {ok: false, reason: \"所有输入都必须是有限数字，不能为空\"}; }\n    if (o.cash0 < 0 || o.queue0 < 0 || o.L < 0 || o.W < 0) return {ok: false, reason: \"金额不能为负\"};\n    if (o.cash0 > LIMIT || o.queue0 > LIMIT || o.L > LIMIT || o.W > LIMIT) return {ok: false, reason: \"金额超出演示上限（每项 ≤ 10 亿万美元）\"};\n    if (o.f < 0 || o.f > 0.5) return {ok: false, reason: \"费率必须在 0%–50% 之间\"};\n    if (o.n !== Math.floor(o.n) || o.n < 1 || o.n > 60) return {ok: false, reason: \"期数必须是 1–60 的整数\"};\n    var cash = o.cash0, queue = o.queue0, rows = [], maxRes = 0, maxQRes = 0, tot = {paid: 0, fee: 0, claims: 0};\n    var EPS = 1e-9;\n    for (var t = 1; t <= o.n; t++){\n      var c0 = cash, q0 = queue, paid, fee, newClaim, branch;\n      cash = cash + o.L;\n      if (q0 <= EPS && cash >= o.W + o.f * o.L){\n        branch = \"收费\"; queue = 0;\n        newClaim = (1 - o.f) * o.W; paid = newClaim; fee = o.f * o.W + o.f * o.L;\n        cash = cash - paid - fee;\n      } else {\n        branch = \"队列优先\";\n        newClaim = (1 - o.f) * o.W; fee = 0;\n        var owed = queue + newClaim; paid = Math.min(cash, owed);\n        cash = cash - paid; queue = owed - paid;\n        if (queue < EPS) queue = 0;\n      }\n      if (cash < 0 && cash > -EPS) cash = 0;\n      var overflow = 0;\n      var res = (c0 + o.L) - (cash + paid + fee + overflow);\n      var qres = (q0 + newClaim) - (queue + paid);\n      if (!fin(cash) || !fin(queue) || !fin(res)) return {ok: false, reason: \"计算出现非有限数值，已停止\"};\n      var scaleRef = Math.max(1, c0 + o.L, q0 + newClaim);\n      maxRes = Math.max(maxRes, Math.abs(res) / scaleRef); maxQRes = Math.max(maxQRes, Math.abs(qres) / scaleRef);\n      tot.paid += paid; tot.fee += fee; tot.claims += newClaim;\n      rows.push({t: t, branch: branch, cashStart: c0, inflow: o.L, paid: paid, fee: fee, cashEnd: cash, queueEnd: queue, residual: res, qResidual: qres});\n    }\n    return {ok: true, rows: rows, cash: cash, queue: queue, totals: tot, maxRelResidual: maxRes, maxRelQResidual: maxQRes};\n  }\n";   // 第 83 轮执行端逐字抽取的 r4 simulate()
/* r5 数值回归（collab-wf-dev 第 83 轮，P1-C）。用法：node pt5_regress.js <r5.html> [<r4.html>]
   模型与展示代码从 r5 页面里按标记抽出来测，测的就是页面本身；给了 r4 时，旧算法也从 r4 页面抽出、并核对与第 83 轮逐字抽取一致。 */
const fs = require("fs"), vm = require("vm");
const [, , r5Path, r4Path] = process.argv;
if (!r5Path) { console.error("用法：node pt5_regress.js <r5.html> [<r4.html>]"); process.exit(2); }
const r5 = fs.readFileSync(r5Path, "utf8");
let pass = 0, fail = 0;
function ok(name, cond, detail) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ " + name + (detail ? "  —— " + String(detail).slice(0, 600) : "")); }
}
function between(src, a, b) {
  const i = src.indexOf(a), j = src.indexOf(b);
  if (i < 0 || j < 0 || src.indexOf(a, i + 1) >= 0 || src.indexOf(b, j + 1) >= 0) throw new Error("标记缺失或不唯一：" + a);
  return src.slice(i, j + b.length);
}
function fnBlock(src, head) {   // 从 head 起按花括号配对取出函数（跳过字符串与注释）
  const i = src.indexOf(head);
  if (i < 0 || src.indexOf(head, i + 1) >= 0) throw new Error("函数头缺失或不唯一：" + head);
  let k = src.indexOf("{", i), depth = 0;
  for (; k < src.length; k++) {
    const ch = src[k], nx = src[k + 1];
    if (ch === "/" && nx === "/") { k = src.indexOf("\n", k); continue; }
    if (ch === "/" && nx === "*") { k = src.indexOf("*/", k + 2) + 1; continue; }
    if (ch === '"' || ch === "'" || ch === "`") { for (k++; src[k] !== ch; k++) if (src[k] === "\\") k++; continue; }
    if (ch === "{") depth++;
    if (ch === "}" && --depth === 0) return src.slice(i, k + 1);
  }
  throw new Error("花括号不配对：" + head);
}
const norm = (s) => s.replace(/\s+/g, " ").trim();
const B = (x) => BigInt(x);

console.log("—— 页面结构 ——");
const model = between(r5, "/* PT5-MODEL-BEGIN */", "/* PT5-MODEL-END */");
const view = between(r5, "/* PT5-VIEW-BEGIN */", "/* PT5-VIEW-END */");
const scripts = [...r5.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)].map((m) => m[1]);
let synErr = "";
scripts.forEach((s, i) => { try { new vm.Script(s); } catch (e) { synErr += `#${i}: ${e.message}；`; } });
ok(`r5 全部 ${scripts.length} 段内联脚本语法正确`, scripts.length > 0 && !synErr, synErr);
ok("模型块的代码（去掉注释后）里没有 EPS；展示块里没有 toFixed(2)", !/\bEPS\b/.test(model.replace(/\/\*[\s\S]*?\*\//g, "")) && !/toFixed\(2\)/.test(view.replace(/\/\*[\s\S]*?\*\//g, "")));
const ctx = {}; vm.createContext(ctx);
vm.runInContext(model + "\n;this.simulate = simulate; this.PT5Money = PT5Money;", ctx);
vm.runInContext(view + "\n;this.pt5Cells = pt5Cells; this.pt5Summary = pt5Summary;", ctx);
const sim = ctx.simulate, cells = ctx.pt5Cells, summary = ctx.pt5Summary;

const octx = { LIMIT: 1e9, fin: (x) => typeof x === "number" && isFinite(x) }; vm.createContext(octx);
let oldSrc = OLD_SIMULATE;
if (r4Path) {
  const r4 = fs.readFileSync(r4Path, "utf8");
  const got = fnBlock(r4, "function simulate(o){");
  ok("r4 页面里的 simulate() 与第 83 轮逐字抽取的原文一致（空白归一后）", norm(got) === norm(OLD_SIMULATE), norm(got).slice(0, 200));
  oldSrc = got;
  const oldSim = fnBlock(r4, "function sim(){");
  const rest4 = r4.replace(got, "").replace(oldSim, ""), rest5 = r5.replace(model, "").replace(view, "");
  ok("除 simulate() 与 sim() 两处外，r5 与 r4 逐字相同", rest4 === rest5);
  const cnt = (s, w) => s.split(w).length - 1;
  ok("“理论净收益”等措辞保留：出现次数与 r4 相同", cnt(r4, "理论净收益") === cnt(r5, "理论净收益") && cnt(r4, "已收") === cnt(r5, "已收"),
     `${cnt(r4, "理论净收益")}/${cnt(r5, "理论净收益")}`);
}
vm.runInContext(oldSrc + "\n;this.simulate = simulate;", octx);
const old = octx.simulate;
const oldFmt = (x) => (Math.abs(x) < 5e-10 ? 0 : x).toFixed(2);

const allRows = [];
function run(o) { const r = sim(o); if (r.ok) r.rows.forEach((x) => allRows.push(x.exact)); return r; }
const base = { cash0: "100", L: "100", W: "80", fPct: "2", n: "1" };
const one = (q) => run({ ...base, queue0: q });
const E = (r) => r.exact;

console.log("—— R1–R7 指定用例（金额输入单位：万美元；内部：美分） ——");
let r = one("5e-10"), o = old({ cash0: 100, queue0: 5e-10, L: 100, W: 80, f: 0.02, n: 1 });
ok("R1 反例 queue0=5e-10 万美元（= 0.000005 美元）：入口可见取整为 0；收费；现金 118 万美元 = 118000000 美分，实付 78400000，费用 3600000，队列 0",
  r.ok && r.rows[0].branch === "收费" && E(r).cash === B("118000000") && E(r).totals.paid === B("78400000")
  && E(r).totals.fee === B("3600000") && E(r).queue === B(0)
  && r.rounding.length === 1 && r.rounding[0].field === "queue0" && r.rounding[0].text.includes("0.000005 美元")
  && r.rounding[0].text.includes("0.000000 万美元") && summary(r).rows[0][1] === "118.000000 万美元（1180000.00 美元）"
  && o.cash === 118, JSON.stringify(r.rounding));
r = one("0.000001");
ok("R2 queue0=0.000001 万美元（1 美分）：队列优先，费用 0；现金 121.599999 万美元（121599999 美分），实付 78400001，队列 0，无舍入",
  r.rows[0].branch === "队列优先" && E(r).cash === B("121599999") && E(r).totals.paid === B("78400001")
  && E(r).totals.fee === B(0) && E(r).queue === B(0) && r.rounding.length === 0 && r.fmt.wan6(E(r).cash) === "121.599999");
const r3a = one("0.0000004"), r3b = one("4.99999e-7");
ok("R3 queue0=0.0000004 / 4.99999e-7 万美元（0.004 / 0.00499999 美元，不足半美分）：取整为 0 并列出；收费；现金 118000000 美分",
  [r3a, r3b].every((x) => x.rows[0].branch === "收费" && E(x).cash === B("118000000") && x.rounding.length === 1)
  && r3a.rounding[0].text.includes("0.004 美元"), r3a.rounding[0] && r3a.rounding[0].text);
const r4a = one("0.0000005"), r4b = one("5e-7"), r4c = one("5.000001E-7");
ok("R4 queue0=0.0000005 / 5e-7 / 5.000001E-7 万美元（半美分及以上）：进 1 美分并列出；队列优先；现金 121599999 美分",
  [r4a, r4b, r4c].every((x) => x.rows[0].branch === "队列优先" && E(x).cash === B("121599999") && x.rounding.length === 1
  && x.rounding[0].cents === "1"));
const q4 = one("0.004"), q5 = one("0.005");
ok("R4b queue0=0.004 / 0.005 万美元（40 / 50 美元，整美分）：不取整；队列优先；现金 121596000 / 121595000 美分",
  q4.rounding.length === 0 && q5.rounding.length === 0 && E(q4).cash === B("121596000") && E(q5).cash === B("121595000")
  && E(q4).totals.paid === B("78404000"));
r = run({ cash0: "0", queue0: "0.3", L: "0.1", W: "0", fPct: "0", n: "4" });
ok("R5 浮点残差场景（队列 0.3 由每期 0.1 偿还）：队列 200000→100000→0→0 美分，第 4 期才转收费，期末现金 100000 美分，没有幽灵正队列",
  r.rows.map((x) => x.exact.queueEnd.toString()).join(",") === "200000,100000,0,0"
  && r.rows.map((x) => x.branch).join(",") === "队列优先,队列优先,队列优先,收费" && E(r).cash === B("100000"));
const f1 = run({ cash0: "1", queue0: "0", L: "0", W: "0.000033", fPct: "2", n: "1" });
const f2 = run({ cash0: "1", queue0: "0", L: "0", W: "0.000025", fPct: "2", n: "1" });
const f3 = run({ cash0: "1", queue0: "0", L: "0", W: "0.000024", fPct: "2", n: "1" });
ok("R6 费用舍入：W=33 美分×2%=0.66→1，债权 32；25 美分→0.5→1，债权 24；24 美分→0.48→0，债权 24；三者都列出舍入，费用+债权恰为 W",
  E(f1).feeW === B(1) && E(f1).claimPerPeriod === B(32) && E(f1).cash === B("999967")
  && E(f2).feeW === B(1) && E(f2).claimPerPeriod === B(24) && E(f3).feeW === B(0) && E(f3).claimPerPeriod === B(24)
  && [f1, f2, f3].every((x) => x.rounding.length === 1 && x.rounding[0].field === "feeW" && E(x).feeW + E(x).claimPerPeriod === E(x).W));

console.log("—— 独立参考实现与随机对拍 ——");
function ref(c0, q0, L, W, pn, pd, n) {   // 独立写的参考实现：全部 BigInt，费用 = round_half_up(base*pn/(100*pd))
  const rh = (a, d) => { const q = a / d, m = a % d; return m * 2n >= d ? q + 1n : q; };
  const fw = rh(W * pn, 100n * pd), fl = rh(L * pn, 100n * pd), cl = W - fw;
  let cash = c0, queue = q0; const out = [];
  for (let t = 0; t < n; t++) {
    const q = queue; cash += L;
    if (q === 0n && cash >= cl + fw + fl) { cash -= cl + fw + fl; out.push(["收费", cash, queue]); }
    else { const owed = queue + cl, paid = cash < owed ? cash : owed; cash -= paid; queue = owed - paid; out.push(["队列优先", cash, queue]); }
  }
  return out;
}
let seed = 20261012;
const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
function amt() {
  const k = rnd();
  if (k < 0.1) return "0";
  if (k < 0.2) return String(Math.floor(rnd() * 1e9));                       // 大额，最多 1e9 万美元
  if (k < 0.3) return (Math.floor(rnd() * 1000) + 1) + "e-" + (Math.floor(rnd() * 9) + 1);   // 科学计数法，可能不足 1 美分
  return (rnd() * 1000).toFixed(Math.floor(rnd() * 8));                   // 0–8 位小数，第 7、8 位不足 1 美分
}
let fuzzBad = "", nFuzz = 0, maxCash = 0n;
for (let i = 0; i < 3000; i++) {
  const o = { cash0: amt(), queue0: amt(), L: amt(), W: amt(), fPct: (rnd() * 50).toFixed(Math.floor(rnd() * 6)), n: String(1 + Math.floor(rnd() * 60)) };
  const res = run(o);
  if (!res.ok) { fuzzBad += `${JSON.stringify(o)} → ${res.reason}；`; continue; }
  nFuzz++;
  const pd = 10n ** BigInt((o.fPct.split(".")[1] || "").length), pn = BigInt(o.fPct.replace(".", ""));
  const i0 = res.exact.inputs;
  const exp = ref(i0.cash0, i0.queue0, i0.L, i0.W, pn, pd, Number(o.n));
  const got = res.rows.map((x) => [x.branch, x.exact.cashEnd, x.exact.queueEnd]);
  if (JSON.stringify(exp, (k, v) => typeof v === "bigint" ? v.toString() : v) !== JSON.stringify(got, (k, v) => typeof v === "bigint" ? v.toString() : v))
    fuzzBad += `不一致 ${JSON.stringify(o)}；`;
  if (res.exact.cash > maxCash) maxCash = res.exact.cash;
}
ok(`随机 3000 组（含大额、科学计数法、不足 1 美分、0–50% 费率、1–60 期）：全部可算，且与独立参考实现逐期一致（${nFuzz} 组）`,
  !fuzzBad && nFuzz === 3000, fuzzBad);
ok(`R7 守恒：全部 ${allRows.length} 期逐期精确成立（期初现金+进池 === 期末现金+实付+费用；期初队列+新债权 === 期末队列+实付），现金与队列不为负`,
  allRows.length > 3000 && allRows.every((e) => e.cashStart + e.inflow === e.cashEnd + e.paid + e.fee
    && e.queueStart + e.claim === e.queueEnd + e.paid && e.cashEnd >= 0n && e.queueEnd >= 0n));

console.log("—— R8 与 r4 原算法对拍（整美分输入，旧算法结果按美分取整后比较） ——");
let diffs = 0, edge = 0, nCase = 0, edgeList = [];
for (const c0 of [0, 1, 50, 100, 1000]) for (const q0 of [0, 0.5, 30, 200]) for (const L of [0, 10, 100])
  for (const W of [0, 80, 150]) for (const fp of [0, 2, 5, 50]) for (const n of [1, 3, 12]) {
    nCase++;
    const a = old({ cash0: c0, queue0: q0, L, W, f: fp / 100, n }), b = sim({ cash0: String(c0), queue0: String(q0), L: String(L), W: String(W), fPct: String(fp), n: String(n) });
    let same = a.ok && b.ok && a.rows.length === b.rows.length;
    for (let t = 0; same && t < a.rows.length; t++) {
      const x = a.rows[t], y = b.rows[t].exact;
      same = x.branch === b.rows[t].branch && ["cashEnd", "queueEnd", "paid", "fee"].every((k) => BigInt(Math.round(x[k] * 1e6)) === y[k]);
      if (!same) {   // 只接受一种差异：旧算法在“现金恰好等于应付+费用”的相等边界上因浮点误差走错分支
        const yc = y.cashStart + y.inflow, need = y.claim + b.exact.feeW + b.exact.feeL;
        if (y.queueStart === 0n && yc === need) { edge++; edgeList.push(JSON.stringify({ c0, q0, L, W, fp, n, t: t + 1 })); same = true; break; }
      }
    }
    if (!same) diffs++;
  }
ok(`R8 对拍 ${nCase} 组：分支序列与逐期现金、队列、实付、费用（美分）完全一致；仅相等边界上的旧版浮点误判 ${edge} 组另列`, diffs === 0,
  `不一致 ${diffs} 组`);
if (edge) console.log("    旧版在相等边界上走错分支的组（r5 按精确整数走“收费”）：" + edgeList.slice(0, 5).join("；") + (edge > 5 ? ` 等 ${edge} 组` : ""));

console.log("—— 范围与精度 ——");
r = run({ cash0: "1e9", queue0: "0", L: "1e9", W: "0", fPct: "0", n: "60" });
ok("R9a 上限累计：1e9 万美元起、每期进池 1e9 万美元、60 期 → 期末 61000000000000000 美分（超过 Number.MAX_SAFE_INTEGER），精确，显示 61000000000.000000 万美元",
  E(r).cash === B("61000000000000000") && E(r).cash > B(Number.MAX_SAFE_INTEGER) && r.fmt.wan6(E(r).cash) === "61000000000.000000");
r = run({ cash0: "1e9", queue0: "0", L: "0", W: "1e9", fPct: "33.333333333333333333", n: "1" });
ok("R9b 大额×多位小数费率：1e15 美分 × 33.333333333333333333% = 333333333333333.33… → 333333333333333 美分（浮点乘法给不出这个精确结果）",
  E(r).feeW === B("333333333333333") && E(r).claimPerPeriod === B("666666666666667"));
r = run({ cash0: "1e9", queue0: "1e9", L: "1e9", W: "1e9", fPct: "50", n: "60" });
ok("R9c 四项都取上限、50% 费率、60 期：可算，守恒精确", r.ok && r.rows.length === 60);

console.log("—— 输入校验（与 r4 的提示一致） ——");
const bad = (o) => { const x = sim({ ...base, queue0: "0", ...o }); return x.ok ? "（意外通过）" : x.reason; };
const oldBad = (o) => { const x = old({ cash0: 100, queue0: 0, L: 100, W: 80, f: 0.02, n: 1, ...o }); return x.ok ? "（意外通过）" : x.reason; };
const pairs = [[{ cash0: "" }, { cash0: NaN }], [{ L: "abc" }, { L: NaN }], [{ W: "-1" }, { W: -1 }],
  [{ cash0: "1000000000.000001" }, { cash0: 1000000001 }], [{ fPct: "60" }, { f: 0.6 }], [{ fPct: "-0.1" }, { f: -0.001 }],
  [{ n: "1.5" }, { n: 1.5 }], [{ n: "0" }, { n: 0 }], [{ n: "61" }, { n: 61 }]];
const mism = pairs.filter(([a, b]) => bad(a) !== oldBad(b)).map(([a, b]) => `${JSON.stringify(a)}：${bad(a)} ≠ ${oldBad(b)}`);
ok("非法输入的提示与 r4 相同（空白、非数字、负数、超上限、费率越界、期数非整数或越界）", mism.length === 0, mism.join("；"));
ok("只接受十进制：0x10、Infinity、1e400 不被当成数字或被拒绝；-0 与 6e1 期按合法处理",
  bad({ L: "0x10" }) === "所有输入都必须是有限数字，不能为空" && bad({ L: "Infinity" }) === "所有输入都必须是有限数字，不能为空"
  && bad({ L: "1e400" }) === "金额超出演示上限（每项 ≤ 10 亿万美元）" && sim({ ...base, queue0: "-0", n: "6e1" }).ok);
r = run({ ...base, queue0: "0", cash0: "1000000000.0000004" });
ok("上限按美分判断：1000000000.0000004 万美元取整为 1e15 美分（不超上限）并列出舍入；1e-500 万美元按极小值取整为 0 并列出",
  r.ok && r.rounding.length === 1 && E(r).inputs.cash0 === B("1000000000000000")
  && sim({ ...base, queue0: "1e-500" }).rounding[0].text.includes("极小值"));
r = sim({ cash0: 100, queue0: 5e-10, L: 100, W: 80, f: 0.02, n: 1 });
ok("旧接口（数字、费率用小数 f）仍可调用，结果与 R1 相同", r.ok && E(r).cash === B("118000000") && r.rounding.length === 1);

console.log("—— 展示 ——");
r = run({ cash0: "0", queue0: "0", L: "0", W: "0.000001", fPct: "0", n: "1" });
const s = summary(r), row = cells(r, r.rows[0]);
ok("正的 1 美分队列不会显示成无欠款：期末队列 0.000001 万美元（0.01 美元），判断不是“没有欠款排队”；r4 的 fmt 会显示 0.00",
  E(r).queue === B(1) && s.rows[1][1] === "0.000001 万美元（0.01 美元）" && s.rows[4][1] !== "没有欠款排队"
  && row[7] === "0.000001" && oldFmt(r.queue) === "0.00", JSON.stringify(s.rows));
r = one("5e-10");
ok("舍入清单出现在结果里；没有舍入时也明确写“无”；说明文字保留“教学模拟”与“铸造 PAPER 不改变”",
  summary(r).rows.some((x) => x[0] === "舍入：" && x[1].includes("起始队列欠款"))
  && summary(one("0")).rows.some((x) => x[0] === "舍入：" && x[1].startsWith("无"))
  && summary(r).note.includes("教学模拟") && summary(r).note.includes("铸造 PAPER 不改变"));

console.log(`\n回归结果：${pass} / ${pass + fail} 通过`);
process.exit(fail ? 1 : 0);
