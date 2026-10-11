【Papertrade r2本机独立验收：阻断性数学与事实问题，请交完整r3】
当前版本：collab-wf 1.6.6；项目v2.6；指纹b848fba7f3776a69；第77轮已由驱动正式消费、执行两条只读命令、进入DONE。工具目标7/7与报告数学验收是不同的事，不能据此宣布报告正确或代用户最终验收。
档位：high
角色不变：你为Claude Opus 5.5，负责业务报告、公式和代码；执行端只读核对、独立复算、本机验收及交付。请完整读取本反馈，交全量自包含离线HTML r3，不仅文字道歉、不只补丁、不自行改工具/目标/护栏/交易配置。

一、交付身份与原始需求（保持完整范围）
已实际下载并独立核SHA：papertrade-business-research-20261011-r2.html，56211字节，SHA256 157b0fc6c28b4f711e56474efa48fd7710dc0d2fa4bae376074ea339a19f8ece。原帖https://x.com/oragnes/status/2108943557310767606，2026-10-10 23:31:40 CST，用户保存2026-10-11 11:23:30；真实对象Papertrade，原帖主张约一小时1020亿美元名义成交（$102 billion），93.5亿OI、1.2亿TVL，不是一日1200亿。精确成交/口径/OI/TVL/PAPER14.9亿发行13.7亿质押24.8万美元奖励及履历仍未独立复算。不能将Hyperliquid候选报告套用为本对象。用户要完整商业方案、商业逻辑、闭环、第一性原理和可达成目标；保留14章及原有边界，不因修计算器而削成简报。

二、阻断A：hc()只减move的deadband，未减原始收益
源码495–506行：m=movePct/100-dead，但scaled=gain*scale，gain标为原始赢利。
2026-10-11 11:41–11:43 CST直接重取官方https://docs.papertrade.xyz/#/how/asymmetric-impact，Last updated 2026-10-06：
“A winning close goes through three steps, in order: 1. Deadband. The gain is measured as if the exit were 0.2 bps (entryPrice / 50000) less favourable. A win smaller than that becomes zero. 2. Impact. The remaining gain is scaled by the formula below. 3. Win fee. 2% of the scaled gain comes off. The trader receives the other 98%.”
“QueueLib.applyMarketImpact; move there uses the exit price after the deadband.”
因此线性PnL的规则演示需先扣收益基数，再算scale：rawMove=movePct/100，effectiveMove=max(rawMove-dead,0)，gainAfterDeadband=rawGain*effectiveMove/rawMove（rawMove<=0单独处理）；scaled=gainAfterDeadband*scale；2%示例费用在scaled上扣。请自行审核并给正确实现，而非直接信任反馈。若未定位实际合约，只称官方规则演示，不称链上审计结论。
执行端独立Python计算、rawGain=$1000：
BTC pm814.598，1%：deadband后gain998，scale0.8832356384627474，scaled881.4691671858219，98%后863.8397838421055；r2错误scaled883.2356384627475、user865.5709256934925。
BTC 0.1%：deadband后980，scale0.754215781013626，scaled739.1314653933534，user724.3488360854864；r2错误scaled754.2157810136259、user739.1314653933534。
BTC 0.003%：deadband后333.3333333333333，scaled15.043128295501486，user14.742265729591455；r2 scaled45.12938488650447。
ETH pm483.979，0.1%：scaled689.6743210339434，user675.8808346132646。
请同时修正文公式rawPnl定义、输入标签、输出中的scale与相对原始收益保留率区分，去掉“75% vs74%可能来自取整”的错误解释。测试必须以官方顺序和独立预期值为准，不能复刻同一源码再说手算通过。

三、阻断B：sim()现金/实付/费用不守恒
源码518–519：fee=f*L+f*W；cash-=W+f*L；pay=W。显示的fW既未减少pay，也未扣现金。
独立反例（单位万美元）：C0=0，L=100，W=80，f=2%，1期。r2输出cash18、paid80、fee3.6，总计101.6，大于100输入，凭空多1.6。
请明确定义W税前/税后。建议采用“折减后、赢利费前的赢利”标签，或另选一致定义并说明。每期强制验证：C_start+actual_cash_loss=C_end+actual_paid_to_winners+actual_fee_cash_out+overflow（教学模拟若不含overflow则0）。债权/铸币不是现金。税前W若有赢利费，实付与费用必须同源，不能既付足W又多计fW。费用减免/队列口径请按官方支持范围声明，不冒充真实协议算法。请给无费、正常收费、不足支付、已有队列、清完队列、零输入及多期场景的守恒残差，容许浮点误差、不得只看默认免收费分支。
此外所有极大有限输入组合可能导致cash累计Infinity、NaN，不应静默显示正常结论；非法/负数/空输入不要默默变成正常0，明确校验反馈。对期数和费率的限制给可见提示，算术不溢出。

四、正文与图示事实修订
1 行178及回报说官方未核到无资金费，错误。文档首页https://docs.papertrade.xyz/明确“No funding costs”；impact页写“no spread, no slippage, no funding, no notional trading fee, and no gas charged to the user”。可称文档支持，不能称已完成合约核实。
2 行198“按价格变动幅度和仓位规模计算”及矩阵“规模折减”错误。官方明确“Move-sized, not position-sized ... A reference notional set per market controls the steepness; the size of your trade does not.” referenceNotional是每市场固定参考值，不是实际仓位规模；$100与$100K同收益百分比。统一正文、公式注释、图表。
3 行186将Rollbit来源列纯作者类比，官方impact页Inspiration明确“adapted from Rollbit's market-impact model”，且解释原Rollbit term2用actual position notional，Papertrade换成reference常数。机制来源可标官方自述，不意味着已审计Rollbit或创始人履历。
4 盈利规则页说scaled gain扣2%给98%，overview说Fees are paid out only while payout queue is empty and LP can cover them。区分“费用计算/扣留/分发”和“LP侧亏损费不足时waived”；页面如果不足以确定win fee不足时处理，显式标规则文字/执行语义待合约核实，不把所有情况简单写完全同样的98%/免收/实际拿到。hc输出建议称“该收费情景下理论净收益”，不是已付现金。
5 overflow以trackedLpUsd>5M规则为基准，不把真实LP现金达到5M与追踪账达到阈值等同。五本账定义及现金恒等式保留。
6 图价值流实线从HL到池表示价格信息，不是现金；图例“实线全部现金”不准确。用独立线型/颜色标报价信息，分清资金流、债权与代币。
7 合约地址页此次抓取TBA只证明本页未公布地址，不证明运行地址不存在。保持缺口边界。

五、核验来源与合规边界
本次又直接核https://docs.papertrade.xyz/#/paper/overview：half of every2% fee（win和LP-side loss）；another half dev recipient；费用分发有queue empty和LP cover条件；LP excess以trackedLP 5M cap为基准。保持实际代码/签名/多签/时间锁未核，不下属实或盈利必然断言。
监管官网https://www.csrc.gov.cn/csrc/c100028/c7614318/content.shtml此次复核标题/银发〔2026〕42号/2026-02-06发布施行一致。报告不是法律意见，不给经营高杠杆对赌、入金开户、钱包签名、亏损挖矿、价格操纵、境内交易中介/导流、规避法域方案。不恢复BTC。中立研究及普通企业负债分析候选并不自动取得合规资格，商业预算与客户数一律假设。

六、交付与验收契约
新文件：outputs/papertrade-business-research-20261011-r3.html，全14章、中文浅色、手机390px不全页横溢、自包含无外部脚本/钱包/联网调用。提供bytes/SHA、修订列表、独立计算预期与误差、现金逐期守恒测试、node --check及真实容器桌面/手机范围，区分作者自测和本机验收。
本轮不改项目版本、目标、工具。固定块，本机只读wf goals、wf fingerprint，发布无，之后空闲；两条工具命令通过不等于报告通过，报告本机数学/事实/浏览器验收由执行端继续。不要求用户再批准研究纠错，不声称资料库已导入或用户最终验收。
