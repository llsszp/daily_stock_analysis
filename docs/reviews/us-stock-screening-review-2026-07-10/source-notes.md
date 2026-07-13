# 美股短线选股功能审查 - 来源与复核说明

## 报告任务

- 决策问题：当前“选股 > 美股 > 美股短线回踩修复”是否足以作为用户的短线候选排序依据，以及应按什么顺序优化。
- 主要读者：产品使用者，股票知识有限，需要看懂候选为何入选及何时不应行动。
- 当前假设：短线波段默认持有三至十个交易日；扩展时段主要用于观察和条件触发，而不是无条件成交。
- 结论口径：代码审查和单次实时运行可确认逻辑缺陷与数据风险，不能证明未来收益。

## 报告结构映射

- Title：美股短线选股功能审查。
- Executive Summary：总体判断、策略偏差、首要改造方向。
- Key Findings with Visual Evidence：真实候选质量检查图和优先级审查表。
- Recommended Next Steps：可信度、策略对齐、向前验证三个阶段。
- Further Questions：默认持有窗口、观察名单语义、候选失效条件。
- Caveats and Assumptions：单次运行限制、扩展时段风险、Longbridge能力边界。

## 证据清单

- 当前代码：`src/services/alphasift_service.py`。
  - 动态股票池与四篮子预筛：`_build_dsa_us_dynamic_universe`、`_select_dsa_us_dynamic_universe_symbols`。
  - 实时层与深评上限：`_build_dsa_us_candidates`。
  - 实时和短线评分：`_score_dsa_us_factors`、`_score_dsa_us_short_swing_setup`。
  - 风险等级：`_dsa_us_risk_level`、`_dsa_us_risk_flags`。
  - 排名后新闻与基本面补充：`_enrich_candidates_with_dsa`。
- 当前行情实现：`data_provider/longbridge_fetcher.py`、`data_provider/base.py`。
  - 盘前、盘中、盘后和夜盘最新报价选择。
  - 最近二十四小时五分钟K线摘要。
  - 逐股行情与跨数据源补充链路。
- 当前页面：`apps/dsa-web/src/pages/StockScreeningPage.tsx`。
  - 评分、风险标签、主要因子和DSA增强呈现。
- Nasdaq缓存：`data/alphasift/us_universe_nasdaq.json`，缓存时间 `2026-07-10T03:35:09Z`，共七千一百四十条。
- 本地实跑任务：`3fd7e5bc59444ebb8f552f10c8fc0a6d`。
  - 运行时间：中国标准时间约 `12:42:50` 至 `12:44:28`。
  - 快照七千一百四十，可选两千二百九十五，动态上限二百二十，实际预筛二百二十二，实时层四十，深评三十二，返回十。
  - 返回十只中：固定默认列表七只、半导体五只、ETF两只、日线不足一只、符合程序偏好五日回踩区间零只、风险等级全部为低。

## 复算定义

- “固定默认列表”匹配 `DSA_US_DEFAULT_UNIVERSE` 中的代码。
- “近期回踩区间”使用当前短线评分的正向区间：`change_5d_pct` 在负八至负一之间。
- “半导体候选”按公司业务识别为 TSM、AMD、NVDA、AVGO、MU。
- “日线不足”指 `daily_calibration.available != true`；本次为 SPCX，只有十八根日线。
- “ETF”按代码和名称识别为 SPY、QQQ。

## 图表契约

- 分析问题：同一次返回候选中，有多少结果暴露出固定名单偏置、行业集中、ETF混入、日线不足、回踩不匹配和风险低估。
- 一句话结论：候选列表与“稳健回踩修复”的目标没有稳定对齐，风险表达也缺少区分度。
- 图表家族：类别比较。
- 具体形式：横向单序列条形图。
- 数据充分性：六个互不要求相加的质量检查项，统一分母为十个返回候选。
- 配色：单蓝色根加中性色网格；类别由直接标签区分，不依赖颜色。
- 输出：`report.html` 中的 Recharts 图和同数据静态 SVG 回退。
- QA：检查桌面、窄屏、脚本开启和静态回退；核对图中数值与任务结果。

## 省略与限制

- 未绘制历史收益趋势：当前选股任务不持久化专用候选历史，无法形成不带回看偏差的长期序列。
- 未计算胜率或预期收益：单次候选不能支持此类结论。
- 未把建议权重描述为已验证：任何初始权重都必须通过保存后的向前结果校准。
- 没有显示独立来源附录：来源通过报告内数值和图表的可访问工具提示提供；本文件保留完整复核路径。

## 外部权威资料

- SEC Investor.gov, Extended-Hours Trading: Investor Bulletin: https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-42
- FINRA Rule 2265, Extended Hours Trading Risk Disclosure: https://www.finra.org/rules-guidance/rulebooks/finra-rules/2265
- Longbridge Developers, Candlesticks: https://open.longbridge.com/docs/quote/pull/candlestick
