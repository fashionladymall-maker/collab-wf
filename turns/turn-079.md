【新任务：Papertrade动态图解学习版 + 研究报告必要勘误】
当前：collab-wf 1.6.6，项目v2.6，指纹b848fba7f3776a69，第78轮正式DONE，对话18轮。档位high；固定Claude Opus 5.5负责教学内容、方案和代码，执行端负责事实核对、本机独立复算与真实浏览器验收。不修改工具版本、目标、安全护栏，不恢复BTC项目，不代用户最终验收。

一、用户的新需求（核心不是再写长报告）
用户原话：“paper trade你也帮我推理一下各种图，帮我呈现，学习一下。”
请创作完整可离线学习的中文动态图解：用多种图把Papertrade推理讲透，有逐步推演、场景切换、节点释义和理解检查。要让用户看到机制而非只读文字；图种为理解问题服务，不机械堆图。已有collab-wf教程r3的学习交互风格可借鉴，但不要把它的状态机套成Papertrade规则。

二、当前事实与报告基准
第78轮r3已在本机接收，68635字节，SHA256 04ebaa03f7db599f0b8c49c5baf0ab8e41b3a28feaf6bfdec350224adf4cff00；执行端正在独立验收，作者60/60容器自测不等于本机pass。r2两个数学阻断已在r3源码显示修正，但本文与图示仍有下列待勘误。r3未作为合格成品最终交付。
原帖 https://x.com/oragnes/status/2108943557310767606 发布2026-10-10 23:31:40 CST；用户保存2026-10-11 11:23:30。实际对象Papertrade，原帖约一小时1020亿美元名义成交（$102 billion），93.5亿OI、1.2亿TVL、PAPER14.9亿发行/13.7亿质押/24.8万美元奖励均为原帖主张，未链上复算，不是一日1200亿。Hyperliquid是参考价格与基础设施来源，不是同一交易产品。不要沿用旧Hyperliquid候选商业报告。最高1000倍不能反推实际入金，成交额不是收入/利润/现金。公开合约地址未定位，文档TBA只表示该页没公布，不证明运行地址不存在。
官方首页说明用户与自有LP synthetic swaps、LP从0起、无订单簿撮合、读取HL BBO中间价、不在HL成交/对冲、无资金费。价格信息不是资金。仅中立研究，无真钱、不连钱包、不签名、不交易、不开户导流、不包含价格操纵或亏损挖矿方法。

三、最新官方原文复核与r3必要勘误（请完整读，不能再次单向选取）
2026-10-11约13:2x CST重取 https://docs.papertrade.xyz/#/how/mint-curve，并直接核公开资源 https://docs.papertrade.xyz/assets/index-CdRrkZnQ.js。
同一官方页面两段原文：
A The shape："Flat region (when trackedLpUsd < lpFlatThresholdUsd, including negative): mint rate is the constant flatRate = 100 PAPER per $1 of mint basis."；"Ratchet region (when trackedLpUsd ≥ lpFlatThresholdUsd): rate decays as the high-water-mark ... tailProgressUsd grows." 配图也说负数/flat均100。
B One-way ratchet(v2)："tailProgressUsd is a high-water-mark that never decreases: staker drains debit trackedLpUsd but do not reduce tailProgressUsd. Once the cumulative tail-region gain has advanced, the rate is permanently decayed by that amount ... HWM stays at $100M ... rate ... ≈30 PAPER/$ regardless of how often the LP got drained to stakers in between."
所以r3第6.1第4条“池子跌回小额时也不会自动恢复每美元100枚”过强：HWM不下降≠已经确定所有flat区域永远不再应用100。文档同时存在当前trackedLP条件与不可逆描述的解释张力，运行代码未核不能裁定。请改为“高水位本身不回退；当前追踪账低于门槛时是否按flatRate、以及与永久衰减的衔接，文档两段需澄清，待运行合约核实”。图8若画尾部，横轴明确H而不是当前现金或简单当前trackedLP；只在尾部条件有效时演示100*(120M/(120M+H))^2；不要演示带确定答案的跌回情景。查出明确官方代码之前不能把二手文章的无限增发恢复说法或r3不恢复断言当结论。
C r3第8章图内“费用只在队列为空时才收取”、表格“队列变长，费用免收”均混淆计算/扣留/分发；r3正文第4章和第6章已经区分。统一为“队列活跃时暂停费用发放、优先偿付；LP侧亏损费不足时免收；赢利费缺款时处理待代码核实”，不要笼统所有费用免收/停止计算。
D 五本账中债权被用于新仓而亏损：可销毁队列债权并形成铸造基数，但没有新增USDC。现金funded亏损也有fee carve基数，不全都rawLoss×100。
E 综合检索今日再次给出首24h数据/美元短缺转为增发/时间锁7日等。首个24h按10日22点上线至11日22点才结束，当前13点多尚未结束，不采用其“首24h完整结果”；报道不同采样时刻OI互相冲突，均不改为核实值。发行代币不偿付美元缺口，媒体“7日时间锁”不等于已核实际配置。图解不需要最新运营数字，宁可只用显式教学数据。
F 如要保留监管章，沿用执行端上一轮已核证监会 https://www.csrc.gov.cn/csrc/c100028/c7614318/content.shtml 银发〔2026〕42号（2026-02-06）；只标此前核对，不声称本轮新增法律审查。不提供境内虚拟货币产品商业导流、经营平台、代客交易或规避法域方案；方法迁移也不自动获得合规许可。

四、数学基准与口径
官方impact页 https://docs.papertrade.xyz/#/how/asymmetric-impact Last updated2026-10-06，本轮13:2x再次核原文：赢利先deadband0.2bps，再对剩余赢利impact，再2%win fee；move用deadband后的exit。Move-sized,not position-sized；Rollbit为官方自述来源。r3 PTModel可保留正确模型但请自行检查全部数字。公式：rawMove=rawMovePct/100，effective=max(rawMove-1/50000,0)，gainAfterDeadband=rawGain*effective/rawMove，scale=.9/(1+1/(effective*15000)+100000/(1e6*effective*pm))，scaled=gad*scale，fee=.02scaled，net=.98scaled；pm BTC814.598/ETH483.979。
独立基准BTC0.1%、rawGain1000：gad980，scale .754215781013626，scaled739.1314653933534，fee14.782629307867，net724.3488360854864。BTC1% gad998/scaled881.4691671858219/net863.8397838421055；ETH0.1% scaled689.6743210339434/net675.8808346132646。0.002%为死区边界。曲线必须区分scale、含死区保留率、含2%费保留率；净收益是理论应得，不是实收。固定rawGain改变move只是独立参数演示，若图按固定notional推导必须让rawGain随move同步变动。
现金模型明确W=impact之后win fee之前。教学模拟正常收费实付(1-f)W，fee=fW+fL；现金输入C0+L=现金末+实付+fee。基准C0=0 Q0=0 L100 W80 f.02 n1 → C18 paid78.4 fee3.6；C0100/L100/W50 → C148 paid49 fee3。缺款/已有队列情景不发放费用且新增债权(1-f)W为教学假设，不能当真实协议。默认0/0/100/110/.02/12 → 队列93.6、实付1200。逐期现金与债权两守恒，界面必须提供精确表格复核，浮点容差注明。追踪账与cash独立，不把5M阈值画成现金硬封顶。

五、教学图任务范围（请你据理解设计，不是我替你设计）
建议围绕以下九个理解问题组织一份学习路径，可调整组合但不能漏掉逻辑：
1 谁定价、谁承担风险：系统结构/职责图，明确HL只传价格无对应交易现金。
2 五本账：cash/trackedLP/queue/OI/PAPER，再旁列用户保证金不是LP可支付资产。
3 一笔仓位如何走完：有cash保证金的开仓→价格变动→平仓→本金与收益区分，赢/亏/清算/欠款各分支；保证金退回与收益进队列要分开，不将queued-funded保证金当现金。清算阈值仅定性或粗算，不伪造确切算法。
4 盈利缩水：逐步瀑布图+BTC/ETH曲线，scale与保留率分清；明示发布参数非永恒参数。
5 FIFO欠款如何偿还：空池赢家利润变债权、后续亏损40/60等简单事件，至少两位赢家验证先到先付；排队可能无限延后不是保证兑付。可用无费净债权教学故事，别伪称实际gross100必欠100。
6 现金压力情景：正常收费/已有队列/赢家集中/新增亏损减少/队列清完，展示cash与queue曲线或柱状+精确表，两守恒可见。合约缺款费用语义仍待核。
7 PAPER及费用路线：合格亏损→铸币→质押权益，不是USDC；2%费一半staker一半dev，分发条件、overflow trackedLP5M；用债权亏损的无新现金路径。
8 铸造尾部曲线与稀释：H30M=64/$、H120M=25/$、H360M=6.25/$（尾部有效条件），分母新增稀释原份额不代表现金新增/币价预测；冲突处可折叠原文，不画确定跌回速率。
9 正向飞轮/反向断环及证据缺口：活动不自动净亏损，赢家相关性、单点报价、现金补充不足、队列变长与分发下降；给理解测验，检验“名义成交额能不能付欠款/代币增发能不能补USDC/无滑点是否无成本”等。
每图至少明确：要解决的问题、阅读方式、场景/步骤、节点点击释义、当前步解释、证据类型/来源采集时点、不能推出的结论。至少9图且合计约18–30有实质差别的场景即可，不扩成几百步耗资源。动画用于信息流/现金/债权区别，不做装饰闪动；播放/暂停/上一步/下一步/重置、速度选择、终点停止、离屏暂停、reduced-motion及键盘可达。图内线型与图例一致：价格、USDC现金、队列债权、PAPER至少四类，所有箭头有语义。应保留静态完整图视图与精确数据表，动画关掉仍可学。每图有稳定ID、可读export测试接口（图场景清单/步数/当前状态/模型输出），方便执行端自动遍历但不把DOM测完说逐帧视觉穷举。

六、交付要求
主成品 outputs/papertrade-visual-learning-20261011.html：完整中文浅色研报/课堂风，首屏学什么及怎么用，390px无全页横溢，所有图离线可用、无外部资源/CDN/钱包/签名/交易/联网接口，HTML+SVG/Canvas实现数值图可免外库。不要新建后台/数据库/安装依赖，纯本地文件。离线自包含约束高于ECharts偏好。页面包含术语、来源和免责声明，不能把分析箭头当已实证因果。
第二成品 outputs/papertrade-business-research-20261011-r4.html：完整14章，仅按以上发现统一必要勘误，沿用正确r3数学及明确假设，不能缩成补丁/简报，也不把未验收r3宣称pass。
提供两文件bytes/SHA、内容覆盖、场景数/步数、JS语法、公式数值及守恒测试、作者容器桌面/手机自测范围、未核项目清单，区分作者自测和本机独立验收。用固定回复块；本机执行仅 wf goals、wf fingerprint；发布无，之后空闲。用户不需再次批准无资金学习研究。你负责编写并交付实际文件，不仅提纲。执行端会下载、核SHA、复验并呈现。