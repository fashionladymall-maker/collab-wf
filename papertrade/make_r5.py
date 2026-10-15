#!/usr/bin/env python3
"""Papertrade 研究报告 r4 → r5 生成器（collab-wf-dev 第 83 轮，P1-C：现金模拟的计量单位与 EPS 边界）。

只替换两处：模型里的 simulate()（换成美分整数、精确舍入可见的版本）与展示里的 sim()（万美元 6 位小数、舍入清单）。
其余内容逐字不变；原来的 fmt()、LIMIT、fin() 原样保留给页面其它部分。
每处替换前，先按花括号配对取出原函数，与第 83 轮执行端逐字抽取的原文比对（空白归一后必须一致），不一致就停止。

用法：python3 make_r5.py --in <r4.html> --out <r5.html>
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

R4_SHA = "1d580864a913f907d6f6c1aacd78d61a13db11976af47c13eb8f3fc995f8794f"   # 第 79 轮交付的 r4（72438 字节）

OLD_SIMULATE = "  function simulate(o){\n    var keys = [\"cash0\", \"queue0\", \"L\", \"W\", \"f\", \"n\"];\n    for (var i = 0; i < keys.length; i++){ if (!fin(o[keys[i]])) return {ok: false, reason: \"所有输入都必须是有限数字，不能为空\"}; }\n    if (o.cash0 < 0 || o.queue0 < 0 || o.L < 0 || o.W < 0) return {ok: false, reason: \"金额不能为负\"};\n    if (o.cash0 > LIMIT || o.queue0 > LIMIT || o.L > LIMIT || o.W > LIMIT) return {ok: false, reason: \"金额超出演示上限（每项 ≤ 10 亿万美元）\"};\n    if (o.f < 0 || o.f > 0.5) return {ok: false, reason: \"费率必须在 0%–50% 之间\"};\n    if (o.n !== Math.floor(o.n) || o.n < 1 || o.n > 60) return {ok: false, reason: \"期数必须是 1–60 的整数\"};\n    var cash = o.cash0, queue = o.queue0, rows = [], maxRes = 0, maxQRes = 0, tot = {paid: 0, fee: 0, claims: 0};\n    var EPS = 1e-9;\n    for (var t = 1; t <= o.n; t++){\n      var c0 = cash, q0 = queue, paid, fee, newClaim, branch;\n      cash = cash + o.L;\n      if (q0 <= EPS && cash >= o.W + o.f * o.L){\n        branch = \"收费\"; queue = 0;\n        newClaim = (1 - o.f) * o.W; paid = newClaim; fee = o.f * o.W + o.f * o.L;\n        cash = cash - paid - fee;\n      } else {\n        branch = \"队列优先\";\n        newClaim = (1 - o.f) * o.W; fee = 0;\n        var owed = queue + newClaim; paid = Math.min(cash, owed);\n        cash = cash - paid; queue = owed - paid;\n        if (queue < EPS) queue = 0;\n      }\n      if (cash < 0 && cash > -EPS) cash = 0;\n      var overflow = 0;\n      var res = (c0 + o.L) - (cash + paid + fee + overflow);\n      var qres = (q0 + newClaim) - (queue + paid);\n      if (!fin(cash) || !fin(queue) || !fin(res)) return {ok: false, reason: \"计算出现非有限数值，已停止\"};\n      var scaleRef = Math.max(1, c0 + o.L, q0 + newClaim);\n      maxRes = Math.max(maxRes, Math.abs(res) / scaleRef); maxQRes = Math.max(maxQRes, Math.abs(qres) / scaleRef);\n      tot.paid += paid; tot.fee += fee; tot.claims += newClaim;\n      rows.push({t: t, branch: branch, cashStart: c0, inflow: o.L, paid: paid, fee: fee, cashEnd: cash, queueEnd: queue, residual: res, qResidual: qres});\n    }\n    return {ok: true, rows: rows, cash: cash, queue: queue, totals: tot, maxRelResidual: maxRes, maxRelQResidual: maxQRes};\n  }\n"
OLD_SIM = "  function sim(){\n    var out = document.getElementById(\"sim_out\"), tab = document.getElementById(\"sim_tab\");\n    while (tab.rows.length > 1) tab.deleteRow(1);\n    var fp = num(\"s_f\");\n    var r = PTModel.simulate({cash0: num(\"s_c\"), queue0: num(\"s_q\"), L: num(\"s_l\"), W: num(\"s_w\"), f: fp / 100, n: num(\"s_n\")});\n    if (!r.ok){ put(out, [[\"无法模拟：\", r.reason]], \"请修改输入。本模拟不会把空白或非法输入当作 0。\", true); return; }\n    r.rows.forEach(function(x){\n      var row = tab.insertRow(-1);\n      [String(x.t), x.branch, fmt(x.cashStart), fmt(x.inflow), fmt(x.paid), fmt(x.fee), fmt(x.cashEnd), fmt(x.queueEnd), x.residual.toExponential(1)].forEach(function(v){ row.insertCell(-1).textContent = v; });\n    });\n    var W = num(\"s_w\"), L = num(\"s_l\"), f = fp / 100;\n    put(out, [\n      [\"期末 LP 现金：\", fmt(r.cash) + \" 万美元\"],\n      [\"期末队列欠款：\", fmt(r.queue) + \" 万美元\"],\n      [\"累计赢家实收：\", fmt(r.totals.paid) + \" 万美元；累计费用流出：\" + fmt(r.totals.fee) + \" 万美元\"],\n      [\"现金守恒最大相对残差：\", r.maxRelResidual.toExponential(2) + \"；债权守恒：\" + r.maxRelQResidual.toExponential(2)],\n      [\"判断：\", r.queue > 0 ? ((1 - f) * W > L ? \"每期新增赢家债权大于亏损进池，队列会持续变长\" : \"队列还在，正在用后续亏损偿还\") : \"没有欠款排队\"]\n    ], \"教学模拟：高度简化，不是该协议的真实算法或表现；铸造 PAPER 不改变这里的任何现金数字。\");\n  }\n"
NEW_MODEL = "/* PT5-MODEL-BEGIN */\n  /* r5（collab-wf-dev 第 83 轮）：页面输入与展示的单位是“万美元”，模型内部的最小计量单位是 1 美分，用精确整数（BigInt）计算。\n     1 万美元 = 1,000,000 美分 = 0.000001 万美元的 1,000,000 倍。输入按十进制文本精确换算成美分，不足 1 美分的部分\n     四舍五入（0.5 美分进 1），并逐项列在结果里，不在计算内部静默删除。费率按百分数的十进制文本精确处理，\n     每期费用按美分四舍五入，同样列出。没有 EPS：队列只要还有 1 美分就走“队列优先”。守恒逐期按整数严格相等检查。 */\n  var PT5Money = (function(){\n    var OK = typeof BigInt === \"function\";\n    var Z = OK ? BigInt(0) : 0, ONE = OK ? BigInt(1) : 1, TWO = OK ? BigInt(2) : 2;\n    var LIMIT_CENTS = OK ? BigInt(\"1000000000000000\") : 0;   /* 10 亿万美元 = 1e15 美分（与 r4 的 LIMIT = 1e9 万美元一致） */\n    var MAX_LEN = 64, MAX_EXP = 400;\n    function p10(k){ return BigInt(\"1\" + new Array(k + 1).join(\"0\")); }\n    /* 十进制文本 → {neg, m, e}，值 = m × 10^e（m ≥ 0 的整数）。只接受十进制（可带科学计数法），不接受 0x、Infinity、空白 */\n    function parseDec(v){\n      var s;\n      if (typeof v === \"number\"){ if (!isFinite(v)) return null; s = String(v); }\n      else if (typeof v === \"string\"){ s = v.trim(); }\n      else return null;\n      if (s === \"\" || s.length > MAX_LEN) return null;\n      var mt = /^([+-]?)(\\d*)(?:\\.(\\d*))?(?:[eE]([+-]?\\d+))?$/.exec(s);\n      if (!mt || (mt[2] === \"\" && (mt[3] || \"\") === \"\")) return null;\n      var ex = mt[4] ? parseInt(mt[4], 10) : 0;\n      if (!isFinite(ex) || Math.abs(ex) > MAX_EXP) return {neg: mt[1] === \"-\", m: BigInt((mt[2] || \"\") + (mt[3] || \"\") || \"0\"), e: ex, wild: true, text: s};\n      var frac = mt[3] || \"\";\n      return {neg: mt[1] === \"-\", m: BigInt(((mt[2] || \"\") + frac) || \"0\"), e: ex - frac.length, text: s};\n    }\n    /* m × 10^k 的十进制文本（去掉多余的 0） */\n    function decStr(m, k){\n      var s = m.toString();\n      if (k >= 0) return m === Z ? \"0\" : s + new Array(k + 1).join(\"0\");\n      var neg = -k;\n      while (s.length <= neg) s = \"0\" + s;\n      var a = s.slice(0, s.length - neg), b = s.slice(s.length - neg).replace(/0+$/, \"\");\n      return b ? a + \".\" + b : a;\n    }\n    /* 非负有理数 num/den 四舍五入到整数（0.5 进 1） */\n    function roundHalfUp(num, den){\n      var q = num / den, r = num % den;\n      return {v: (r * TWO >= den) ? q + ONE : q, rounded: r !== Z};\n    }\n    /* 金额（万美元）→ 美分：值 × 10^6 */\n    function toCents(d){\n      if (d.wild) return d.m === Z ? {v: Z, rounded: false} : (d.e > 0 ? {v: LIMIT_CENTS + ONE, rounded: false} : {v: Z, rounded: true, tiny: true});\n      var k = d.e + 6;\n      if (k >= 0) return {v: d.m * p10(k), rounded: false};\n      return roundHalfUp(d.m, p10(-k));\n    }\n    function wan6(c){   /* 美分 → 万美元，6 位小数，精确（1 美分 = 0.000001 万美元） */\n      var s = c.toString(); while (s.length < 7) s = \"0\" + s;\n      return s.slice(0, s.length - 6) + \".\" + s.slice(s.length - 6);\n    }\n    function usd(c){    /* 美分 → 美元，2 位小数，精确 */\n      var s = c.toString(); while (s.length < 3) s = \"0\" + s;\n      return s.slice(0, s.length - 2) + \".\" + s.slice(s.length - 2);\n    }\n    function toWanNumber(c){ return Number(c) / 1e6; }   /* 仅为兼容旧接口的近似数，不用于计算与判断 */\n    return {OK: OK, Z: Z, LIMIT_CENTS: LIMIT_CENTS, parseDec: parseDec, decStr: decStr, roundHalfUp: roundHalfUp,\n            toCents: toCents, wan6: wan6, usd: usd, toWanNumber: toWanNumber, p10: p10};\n  })();\n\n  function simulate(o){\n    var M = PT5Money;\n    var E_FIN = \"所有输入都必须是有限数字，不能为空\";\n    if (!M.OK) return {ok: false, reason: \"当前浏览器不支持精确整数（BigInt），无法做精确模拟，请换用较新的浏览器\"};\n    if (!o || typeof o !== \"object\") return {ok: false, reason: E_FIN};\n    var rateIsPct = o.fPct !== undefined, rateRaw = rateIsPct ? o.fPct : o.f;\n    var amtKeys = [\"cash0\", \"queue0\", \"L\", \"W\"];\n    var label = {cash0: \"起始 LP 现金\", queue0: \"起始队列欠款\", L: \"每期亏损现金进池\", W: \"每期赢家应得 W\"};\n    var d = {}, i;\n    for (i = 0; i < amtKeys.length; i++){ d[amtKeys[i]] = M.parseDec(o[amtKeys[i]]); if (!d[amtKeys[i]]) return {ok: false, reason: E_FIN}; }\n    var dr = M.parseDec(rateRaw), dn = M.parseDec(o.n);\n    if (!dr || !dn) return {ok: false, reason: E_FIN};\n    for (i = 0; i < amtKeys.length; i++){ var x = d[amtKeys[i]]; if (x.neg && x.m !== M.Z) return {ok: false, reason: \"金额不能为负\"}; }\n    var c = {}, rounding = [];\n    for (i = 0; i < amtKeys.length; i++){\n      var k = amtKeys[i], q = M.toCents(d[k]);\n      if (q.v > M.LIMIT_CENTS) return {ok: false, reason: \"金额超出演示上限（每项 ≤ 10 亿万美元）\"};\n      c[k] = q.v;\n      if (q.rounded){\n        rounding.push({field: k, input: d[k].text, cents: q.v.toString(),\n          text: label[k] + \"：输入 \" + d[k].text + \" 万美元\" + (q.tiny ? \"（极小值）\" : \"（= \" + M.decStr(d[k].m, d[k].e + 4) + \" 美元）\")\n                + \"，不足整美分，按 \" + M.wan6(q.v) + \" 万美元（\" + M.usd(q.v) + \" 美元）计\"});\n      }\n    }\n    /* 费率：百分数 = m × 10^e（若给的是旧接口的小数 f，则百分数 = f × 100，指数加 2，仍然精确） */\n    if (dr.wild) return {ok: false, reason: \"费率必须在 0%–50% 之间\"};\n    var pe = dr.e + (rateIsPct ? 0 : 2);\n    var rNum = dr.m * (pe >= 0 ? M.p10(pe) : BigInt(1)), rDen = pe >= 0 ? BigInt(1) : M.p10(-pe);   /* 百分数 = rNum / rDen */\n    if ((dr.neg && dr.m !== M.Z) || rNum > BigInt(50) * rDen) return {ok: false, reason: \"费率必须在 0%–50% 之间\"};\n    if (dn.wild || dn.neg && dn.m !== M.Z) return {ok: false, reason: \"期数必须是 1–60 的整数\"};\n    var nNum = dn.m * (dn.e >= 0 ? M.p10(dn.e) : BigInt(1)), nDen = dn.e >= 0 ? BigInt(1) : M.p10(-dn.e);\n    if (nNum % nDen !== M.Z) return {ok: false, reason: \"期数必须是 1–60 的整数\"};\n    var nB = nNum / nDen;\n    if (nB < BigInt(1) || nB > BigInt(60)) return {ok: false, reason: \"期数必须是 1–60 的整数\"};\n    var n = Number(nB);\n    /* 每期费用（按美分四舍五入）：赢家侧 W×f，LP 侧 L×f；赢家每期新增债权 = W − 赢家侧费用（两者之和恰为 W） */\n    var feeW = M.roundHalfUp(c.W * rNum, BigInt(100) * rDen), feeL = M.roundHalfUp(c.L * rNum, BigInt(100) * rDen);\n    function feeNote(name, base, fr){\n      var ex = M.decStr(base * rNum * BigInt(10000) / (BigInt(100) * rDen), -4);\n      return name + \"：精确值约 \" + ex + \" 美分，按 \" + fr.v.toString() + \" 美分计（每期，四舍五入到整美分）\";\n    }\n    if (feeW.rounded) rounding.push({field: \"feeW\", cents: feeW.v.toString(), text: feeNote(\"每期赢家侧费用 W×f\", c.W, feeW)\n                                     + \"；赢家每期新增债权相应为 \" + M.wan6(c.W - feeW.v) + \" 万美元\"});\n    if (feeL.rounded) rounding.push({field: \"feeL\", cents: feeL.v.toString(), text: feeNote(\"每期 LP 侧费用 L×f\", c.L, feeL)});\n    var claim = c.W - feeW.v, feeAll = feeW.v + feeL.v;\n    var cash = c.cash0, queue = c.queue0, rows = [], tp = M.Z, tf = M.Z, tc = M.Z;\n    for (var t = 1; t <= n; t++){\n      var c0 = cash, q0 = queue, paid, fee, branch;\n      cash = cash + c.L;\n      if (q0 === M.Z && cash >= claim + feeAll){\n        branch = \"收费\"; paid = claim; fee = feeAll; cash = cash - paid - fee;\n      } else {\n        branch = \"队列优先\"; fee = M.Z;\n        var owed = queue + claim; paid = cash < owed ? cash : owed;\n        cash = cash - paid; queue = owed - paid;\n      }\n      var res = (c0 + c.L) - (cash + paid + fee), qres = (q0 + claim) - (queue + paid);\n      if (res !== M.Z || qres !== M.Z || cash < M.Z || queue < M.Z) return {ok: false, reason: \"内部守恒检查未通过，已停止（这不应发生）\"};\n      tp += paid; tf += fee; tc += claim;\n      rows.push({t: t, branch: branch,\n        cashStart: M.toWanNumber(c0), inflow: M.toWanNumber(c.L), paid: M.toWanNumber(paid), fee: M.toWanNumber(fee),\n        cashEnd: M.toWanNumber(cash), queueEnd: M.toWanNumber(queue), residual: 0, qResidual: 0,\n        exact: {cashStart: c0, inflow: c.L, paid: paid, fee: fee, cashEnd: cash, queueStart: q0, queueEnd: queue, claim: claim}});\n    }\n    return {ok: true, unit: \"美分（整数，精确）\", rows: rows,\n      cash: M.toWanNumber(cash), queue: M.toWanNumber(queue),\n      totals: {paid: M.toWanNumber(tp), fee: M.toWanNumber(tf), claims: M.toWanNumber(tc)},\n      maxRelResidual: 0, maxRelQResidual: 0,\n      exact: {cash: cash, queue: queue, totals: {paid: tp, fee: tf, claims: tc}, claimPerPeriod: claim, L: c.L, W: c.W,\n              feeW: feeW.v, feeL: feeL.v, inputs: c},\n      rounding: rounding, fmt: {wan6: M.wan6, usd: M.usd}};\n  }\n  /* PT5-MODEL-END */"
NEW_VIEW = "/* PT5-VIEW-BEGIN */\n  /* r5：展示用模型给出的精确值。万美元显示 6 位小数（0.000001 万美元 = 1 美分），任何正的整美分队列都不会显示成 0；\n     期末现金与队列同时附精确美元。原来的 fmt()（toFixed(2)）保留给页面其它部分，这里不再使用。 */\n  function pt5Cells(r, x){\n    var w = r.fmt.wan6, e = x.exact;\n    return [String(x.t), x.branch, w(e.cashStart), w(e.inflow), w(e.paid), w(e.fee), w(e.cashEnd), w(e.queueEnd), \"0（精确）\"];\n  }\n  function pt5Summary(r){\n    var w = r.fmt.wan6, u = r.fmt.usd, e = r.exact, zero = e.queue - e.queue;\n    var judge = e.queue > zero\n      ? (e.claimPerPeriod > e.L ? \"每期新增赢家债权大于亏损进池，队列会持续变长\" : \"队列还在，正在用后续亏损偿还\")\n      : \"没有欠款排队\";\n    var rows = [\n      [\"期末 LP 现金：\", w(e.cash) + \" 万美元（\" + u(e.cash) + \" 美元）\"],\n      [\"期末队列欠款：\", w(e.queue) + \" 万美元（\" + u(e.queue) + \" 美元）\"],\n      [\"累计赢家实收：\", w(e.totals.paid) + \" 万美元；累计费用流出：\" + w(e.totals.fee) + \" 万美元\"],\n      [\"现金守恒：\", \"逐期精确成立（整数美分，残差为 0）；债权守恒：逐期精确成立\"],\n      [\"判断：\", judge]\n    ];\n    if (r.rounding.length) r.rounding.forEach(function(x){ rows.push([\"舍入：\", x.text]); });\n    else rows.push([\"舍入：\", \"无（输入都是整美分，费用也没有不足 1 美分的部分）\"]);\n    return {rows: rows, note: \"教学模拟：高度简化，不是该协议的真实算法或表现；铸造 PAPER 不改变这里的任何现金数字。\"\n      + \"金额以 1 美分为最小计量单位，内部用精确整数计算；万美元显示到 6 位小数（0.000001 万美元 = 1 美分）。\"};\n  }\n  function sim(){\n    var out = document.getElementById(\"sim_out\"), tab = document.getElementById(\"sim_tab\");\n    while (tab.rows.length > 1) tab.deleteRow(1);\n    function raw(id){ return String(document.getElementById(id).value).trim(); }\n    var r = PTModel.simulate({cash0: raw(\"s_c\"), queue0: raw(\"s_q\"), L: raw(\"s_l\"), W: raw(\"s_w\"), fPct: raw(\"s_f\"), n: raw(\"s_n\")});\n    if (!r.ok){ put(out, [[\"无法模拟：\", r.reason]], \"请修改输入。本模拟不会把空白或非法输入当作 0。\", true); return; }\n    r.rows.forEach(function(x){\n      var row = tab.insertRow(-1);\n      pt5Cells(r, x).forEach(function(v){ row.insertCell(-1).textContent = v; });\n    });\n    var s = pt5Summary(r);\n    put(out, s.rows, s.note, false);\n  }\n  /* PT5-VIEW-END */"


def fail(msg):
    print(f"生成失败：{msg}", flush=True)
    sys.exit(1)


def fn_block(src, head):
    """从函数头起按花括号配对取出整个函数，跳过字符串与注释。返回 (起点, 终点)。"""
    i = src.find(head)
    if i < 0 or src.find(head, i + 1) >= 0:
        fail(f"函数头缺失或不唯一：{head}")
    k, depth = src.index("{", i), 0
    while k < len(src):
        ch, nx = src[k], src[k + 1:k + 2]
        if ch == "/" and nx == "/":
            k = src.index("\n", k)
            continue
        if ch == "/" and nx == "*":
            k = src.index("*/", k + 2) + 2
            continue
        if ch in "\"'`":
            k += 1
            while src[k] != ch:
                k += 2 if src[k] == "\\" else 1
            k += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i, k + 1
        k += 1
    fail(f"花括号不配对：{head}")


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--expect-sha", default=R4_SHA, help="只供作者在替身页面上自测时改用")
    a = ap.parse_args()
    raw = Path(a.src).read_bytes()
    h = hashlib.sha256(raw).hexdigest()
    if h != a.expect_sha:
        fail(f"输入不是第 79 轮交付的 r4（sha256 {h}，应为 {a.expect_sha}）")
    src = raw.decode("utf-8")
    for name in ("PT5Money", "pt5Cells", "pt5Summary", "PT5-MODEL", "PT5-VIEW"):
        if name in src:
            fail(f"输入里已有 {name}：可能已经打过补丁")
    s1, e1 = fn_block(src, "function simulate(o){")
    if norm(src[s1:e1]) != norm(OLD_SIMULATE):
        fail("simulate() 与第 83 轮逐字抽取的原文不一致，未改动任何内容")
    s2, e2 = fn_block(src, "function sim(){")
    if norm(src[s2:e2]) != norm(OLD_SIM):
        fail("sim() 与第 83 轮逐字抽取的原文不一致，未改动任何内容")
    if not (e1 <= s2 or e2 <= s1):
        fail("两处函数重叠")
    parts = sorted([(s1, e1, NEW_MODEL), (s2, e2, NEW_VIEW)])
    out, pos = [], 0
    for s, e, new in parts:
        out += [src[pos:s], new]
        pos = e
    out.append(src[pos:])
    res = "".join(out)
    chk_old = src.replace(src[s1:e1], "").replace(src[s2:e2], "")
    chk_new = res.replace(NEW_MODEL, "").replace(NEW_VIEW, "")
    if chk_old != chk_new:
        fail("替换之外的内容有变化（不应发生）")
    for must in ("function fmt(x)", "PTModel.simulate", "var LIMIT = 1e9", "function fin(x)"):
        if must not in res:
            fail(f"生成结果缺少 {must}")
    p = Path(a.out)
    p.write_text(res, encoding="utf-8")
    b = p.read_bytes()
    print(f"r5 生成通过：{p}，{len(b)} 字节，sha256 {hashlib.sha256(b).hexdigest()}；"
          f"只替换了 simulate() 与 sim()，其余 {len(chk_new)} 个字符与 r4 逐字相同", flush=True)


if __name__ == "__main__":
    main()
