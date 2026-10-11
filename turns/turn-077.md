【新材料到达：Papertrade 原帖已定位，重写商业研究报告】
当前版本：collab-wf 1.6.6；项目 v2.6；指纹 b848fba7f3776a69。
档位：high

角色：你是Claude Opus 5.5，负责核心方案和报告；WorkBuddy AI负责原材料读取、公开事实核对、本机验收和交付。请从头完整读取。用户原需求是“听说一个 piper 交易所非常激进，一天交易了1200亿美元，请认真研究，给完整商业方案、商业逻辑、闭环逻辑、第一性原理和我能达成什么”。用户现在提供了保存的原帖HTML，消除了此前没有来源的问题。第76轮报告以Hyperliquid为候选，只能保留为历史候选分析，不再代表本次正确对象。必须另交完整新HTML，不仅补充说明、不只换标题。此任务与M1用户最终验收无关，不修改工具、目标、护栏、凭据、交易配置，不恢复BTC项目。

一、原材料身份与时点
原帖 https://x.com/oragnes/status/2108943557310767606 ，作者比特币橙子Trader @oragnes。HTML明确datetime=2026-10-10T15:31:40.000Z，即北京时间2026-10-10 23:31:40；本机保存时间2026-10-11 11:23:30 CST。用户提供的是自己的保存文件，执行端只读提取，不修改文件、不上传含账户导航信息的整个HTML。以下是完整业务正文（不含账号导航及回复）：

今晚币圈最疯狂的赌场开业了：MIT量化+链上巨鲸操刀，1000倍杠杆、亏钱挖矿，1小时交易量突破1000亿美元！
Papertrade上线约1小时，成交量突破1020亿美元，未平仓合约93.5亿美元，TVL约1.2亿美元。
平台根本就不需要任何初始做市资金，所有用户都跟协议对赌，用户亏得越多，协议的流动性池反而越大，亏损者还能拿到PAPER，分享未来交易平台的收益。
创始人Jez（@izebel_eth），2015年毕业于MIT，学的是计算机与管理科学，后来在摩根士丹利工作了4年，还曾担任加密风投Standard Crypto的Venture Partner，个人投资项目包括FOMO、neutrl等。
他还是一名扑克高手，2024年参加WPT Prime Championship，最终第7名，奖金18.6万美元。量化交易、VC投资、职业级扑克竞技经验，基本把金融市场、风险定价和博弈论凑齐了。
另一位创始人Blurr（@blurr）2017年前后进入Crypto，经历过CryptoKitties、Compound、Curve等早期链上机会，还曾部署Refund（RFD）。
Papertrade部署在HyperEVM，利用预编译合约读取Hyperliquid订单簿买一卖一的中间价，以此合成BTC、ETH永续仓位，不去Hyperliquid开对冲仓，因此没有传统资金费率、盘口滑点和点差。
盈利平仓受到非对称收益折减，极小价格波动的套利收益抽得更重。这套逻辑吸收Rollbit的高杠杆赌场模型，把定价、仓位和盈亏结算搬到链上。
Martingaler流动性自举机制：LP从0美元启动，输家亏损补池。如果庄家只有500万美元、赢家需领取600万美元盈利，可支付500万美元，剩余100万美元进FIFO债务队列，等待未来亏损偿还。用未来亏损现金流解决初始不足，但债务不消失，赢家可能长期拿不到全部利润。
PAPER没有预挖、团队和VC分配、空投，只有真实交易亏损或清算可铸造。LP追踪余额低于200万美元时，每亏损1美元最多按基础规则铸100枚；超过门槛按衰减曲线降速。质押者可获USDC收益，LP超过500万美元目标余额后，超出部分新增收益分配。PAPER已发行约14.9亿枚，其中13.7亿被质押，累计奖励约24.8万美元。
它把做市商收益、赌场庄家优势、DeFi代币激励揉进系统，把交易者亏损变为协议融资。

二、已直接核官方文档（执行端采集2026-10-11约11:2x–11:4x CST；首页和部分页面显示Last updated 2026-10-06；说明是规则文本，不是合约独立审计/实盘证明）
1. 官网 https://papertrade.xyz/ 实际可见1000x leverage、0 market impact、100% user owned、fully onchain。营销语不等于安全保证。
2. 文档首页 https://docs.papertrade.xyz/ ：fully on-chain perpetuals，1000倍、无订单簿/撮合；合成swap在user和LP之间；HyperEVM预编译读取HyperCore BBO；没有相应合约在Hyperliquid成交。LP从零累积用户损失，缺款形成支付队列由以后损失偿还。不是传统外部流动性网络效应；Hyperliquid成交不能当Papertrade成交。PAPER初始零，无预挖/team/VC/airdrop/vesting，启动不可跨钱包转移，只能stake/unstake。
3. https://docs.papertrade.xyz/#/how/asymmetric-impact ：收益依次经过0.2bps deadband、scale haircut、2% win fee，用户得缩放收益98%。只有赢仓缩放，亏仓不额外从用户收取；solvent时LP侧loss fee从LP收入切走，insolvent时waived。公式adjustedPnl=rawPnl*scale；scale=(1-baseRate)/(1+term1+term2)；term1=1/(move*rateMultiplier)；term2=referenceNotional/(1e6*move*positionMultiplier)；move为deadband后exit的相对变化。官方launch params baseRate10%、rateMultiplier15000、referenceNotional100000美元、positionMultiplier BTC814.598/ETH483.979，且owner可改。BTC1%变化约保留88% raw gain、0.1%约74%，均在2% win fee之前；不是固定2%手续费，也不是按名义量收费。该曲线不证明价格操纵无利可图。
4. https://docs.papertrade.xyz/#/how/pricing-bbo ：价格=(bid+ask)/2，来自HyperCore共识状态的BBO预编译。只拒zero或crossed book，没有max deviation、TWAP、lookback breaker。非零、不交叉但被操纵的BBO可接受。Guardian可永久退市并冻结当刻BBO为此instrument后续close/liquidation价格；global pause仅挡新开且不冻结，owner可解除。不提供操纵实施步骤或交易代码。
5. https://docs.papertrade.xyz/#/how/risk ：官方承认BBO操纵剩余风险，经济摩擦和OI caps不构成损失上限/不盈利证明。launch keeper每side目标=current OI加约10亿美元headroom，10%迟滞；不是每side总OI上限10亿美元，也不是平台总OI上限。owner可调费用/影响参数、dev fee recipient、relayer/keeper、新市场、升级proxy；本页未核独立审计及实际多签/时间锁，不把媒体2/3多签7天当已实验证实。用户余额不自动成为支付赢家的LP资金。
6. https://docs.papertrade.xyz/#/paper/overview ：staker收入来自每笔2% fee的一半（win branch及LP-side loss branch）和超过500万美元staker reward cap的LP excess；另外一半给dev fee recipient。费用只在队列空且LP可覆盖时支付。必须单列开发者收入，不将100% user owned理解成开发者无收益。
7. https://docs.papertrade.xyz/#/how/mint-curve ：loss mint basis=eligible realised loss，不必等于真实新增现金；trackedLpUsd<200万美元（包括负）flat100；超过门槛按one-way HWM tailProgressUsd衰减，默认S=120M，marginal rate100*(S/(S+H))^2。staker drains不降低HWM；不能说池子跌回小额便一律恢复100。USD-funded loss与debt-funded loss不同，后者摧毁queue claim也可铸币，债权销毁不等于新USDC入池。清算以full lost margin铸币，tracked LP可能扣fee。报告区分现金/追踪账/债权/名义敞口/代币五本账，不用单一TVL代替偿付资产。
8. https://docs.papertrade.xyz/#/dev/contract-addresses 当前实际抓到Exchange等地址仍全TBA，不能用文档规则当已核实运行合约；本次未完成链上地址定位、历史区块量/余额重算、代码审计。

三、来源质量与冲突（禁止照搬综合检索的强断言）
- 原帖精确1020亿美元、93.5亿OI、1.2亿TVL、14.9亿PAPER/13.7亿stake/24.8万美元奖励，只是该帖发布时主张，尚未独立复算，不标属实。名义notional的定义由媒体和产品杠杆结构支持，具体累计算法（开/平双计、window、去重、实际杠杆分布）仍未知。不得1020亿/1000直接推真实入金，也不将最高杠杆当平均杠杆。
- 直接读CryptoBriefing https://cryptobriefing.com/?p=391594 ：报道10月10日10AM ET=14UTC=北京22点上线，不足10分钟14.4B notional，BTC3B OI/ETH2.66B、预存85–138M。该时间线与23:31发帖大致同一launch stretch，不说明1020B? 注意1020亿美元是102B，非1020B。只能媒体交叉参考，非独立链上核验。
- https://cryptobriefing.com/?p=391667 页面相对10hours ago却声称first24hours volume>300B和staker12.57M，开业从10月10日22点至用户提供原帖的10月11日11:24:08仅13:24:08。首完整24小时尚未到。不可把此媒体first24h当已闭合24h统计，需标口径/时间冲突，暂不作为确定数据。该报道又把所有loss都100PAPER，违背官方eligible basis和衰减，舍弃简化。
- 综合检索说1020亿“大体属实”，但自己承认没有精确来源，结论不成立；同量级不构成验证。还把Jez和@izebel_eth当不同人，原帖明确同一人，纠正。综合给示例双仓刷币、预言机操纵细节不用：可以研究激励漏洞与稀释风险，不给可执行滥用策略。
- 深潮 https://www.techflowpost.com/article/34469 实际可读标题“明天上线”、页面日期2026.10.10，但正文又说“明天10月10日”，出版相对时间存在不一致；作者David Christopher、编译TechFlow。机制与官方有部分一致，但“收益持有人”不能当股权、保本或兑付承诺。无独立创始人履历证明，MIT、摩根士丹利、VC、WPT第7/18.6万仍原帖待核说法，履历不是偿付保证。

四、报告必须重新推演（不是把旧版结论硬套）
1 首屏明确来源已定位Papertrade、旧报告仅候选，原数字改一小时1020亿而非一天1200亿；最新度/发布点/统计窗口分开。
2 做逐条主张证据矩阵：原帖说什么、官方规则支持什么、已实证什么、缺什么。不伪造上线数据。
3 第一性原理与商业逻辑：外部价格发现+内部风险承接与结算；无对冲不意味着无成本；利润折减、开发者fee、loss redistribution与token fee claim、负债排队，不能套HL maker/orderbook network。
4 价值流/飞轮/断环：用户亏损→池/费用→赢家支付&stake分配→早期代币权益→新活动；新增亏损供给不足/赢家集中/价格异常/排队→兑付期拉长/信心下降；发行代币不是填现金缺口。不自动称Ponzi、不自动称良性，评机制与实际流量。
5 用五本账和现金偿付恒等式解释TVL与真实available cash、debt、OI、fees；演示全部明确假设，不当真实报表。交易者盈亏不能直接当公司净利润。说明1000倍价格不利变动约0.1%是忽略维持保证金/参数的粗略上界，实际可更早。
6 公平启动与早期铸币高率、不可转账、dev fee和升级权限区分。零LP启动将损失吸收/等待转到参与者，不消灭资本需求；不证明盈利者一定少或LLN必然盈利。
7 商业方案保留完整，但不强行把用户目标变成“你只能卖报告”；先区分学习该系统、做无真钱非交易现金流模拟/研究原型、能复制的方法和不能擅自开展的交易/博彩经营。单位经济如有只为合规通用业务、清楚获客工时/法务/数据成本假设；不要声称已启动、收益、牌照或客户确定。
8 现行监管沿用第76轮已核官方银发2026 42号及 https://www.csrc.gov.cn/csrc/c100028/c7614318/content.shtml ，仅中立研究不做境内交易所、营销导流、亏损挖矿操作、入金开户、套利/操纵脚本、代币推荐或规避法域；也不给直接仿造高杠杆赌场方案。可提下一步研究证据和合规的非交易演示。用户未授权资金操作、实盘、账户签名。
9 回答“我能达成什么”：定位原帖、建立账目模型和机制理解、离线无资金压力测试、合规商业验证；明确原始数据和代码独立审计仍缺。

五、实际交付
新文件 outputs/papertrade-business-research-20261011-r2.html ，中文浅色、自包含离线、手机390不横溢、可点来源。可用分析示意图和无真实交易计算器，不联网/外部脚本/钱包/签名。如有JS必须node --check及真实容器桌面/390px测试；只称Claude自测。报告14章上下完整，不提供赌博宣传或赚钱承诺。作者不可声称本机验收/资料库已完成。
回报第一段文件bytes/SHA、关键纠正、未实证项、容器检查范围；按固定块。仅本机执行wf goals、wf fingerprint，发布无、之后空闲；与M1最终用户验收/原凭据发现分开，不强加无关审批阻塞报告。执行端验收结果由执行端完成。本轮不要提出目标或项目版本升级。
