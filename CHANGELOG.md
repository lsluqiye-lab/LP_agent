# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v4.6.2] - 2026-07-30
### Added
- **现金优先与融资感知 (Cash Priority & Financing Awareness)**: CIO 现在能实时感知账户融资状态。若处于融资状态 (Cash < 0)，系统会强制提示融资利息损耗，并显著提高买入准入门槛（仅限 Conviction: high 且 Alpha > 90 的 A+ 级机会）。
- **交易频率反馈与成本账单 (Frequency Feedback Matrix)**: 在 ReAct Prompt 中动态注入“今日交易账单”。针对止损微调 (< 0.5%) 提供显性成本警告，引导 CIO 合并决策，拒绝无效微操。
- **账单审计意识**: 强制 CIO 在推理过程中权衡交易摩擦（手续费+滑点）与潜在收益，实现“频率感知而非硬拦截”的智能调控。

### Changed
- **战略大脑升级 (Brain Upgrade v4.6.2)**: 更新 `agent/react.py` 核心 Prompt。**保留了所有既有的稳定性约束**（如超买冷却、ATR 防线、填充态拦截、以及 Risk < 30 的 LOCKDOWN 模式），在此基础上新增了对资金成本和交易频率的全局认知。

## [v4.6.1] - 2026-07-24
### Added
- **超买决策冷却期 (Overbought Decision Cooldown)**: 在大盘极度超买 (Sentiment > 80 或 RSI > 75) 环境下，对同一标的强制执行 30 分钟买入决策冷却期，严防刷票式重复下单。

### Changed
- **领涨股呼吸空间 (Tier 1 Breathing Room)**: 将 Tier 1 领涨标的的最小 ATR 追踪止损倍数从 3.5x 提升至 **4.0x**，并优化动态 ATR 范围 (最高 4.0x)，防止在 Stage 2 强势期被日内噪音误伤洗盘。
- **物理拦截精度提升 (Enhanced Duplicate Check)**: 升级 `_has_duplicate_pending_order` 物理拦截网，将 `FILLING` (成交中) 状态纳入原子化校验，彻底解决毫秒级并发下的重复建仓“幽灵单”问题。

## [v4.6.0] - 2026-07-22
### Added
- **主线热点探测器 (Main-Line Detector)**: 新增 `GetMainLineLeadersTool` 工具，实时扫描涨幅榜与主力净流入，捕捉当日“主钱”流向。
- **对冲熔断机制 (Hedge Circuit Breaker)**: 当大盘日内强力反弹 (>1.2%) 或风控评分急速修复时，系统自动触发对冲平仓指令，防止 Protective Puts 产生无谓的时间价值损耗。
- **叙事“打脸”修正协议 (Narrative Reality Check)**: 升级 `NarrativeAnalyst` 逻辑，要求其在叙事与实际价格走势背离时（如避险叙事遇上科技暴涨）主动认错并修正立场。

### Changed
- **柔性板块风控 (Soft Sector Guardrails)**: 废除硬性的“板块一票否决权”，升级为“板块资金流惩罚”。允许在弱势板块中通过减半头寸 (0.5x) 进行 A+ 级信号博弈。
- **CIO 战略意识进化**: 升级 `STRATEGIC_SYSTEM_PROMPT`，注入主线偏好 (Main-Line Bias) 与动态对冲退出意识。
- **物理层对齐**: 同步更新 `PortfolioManager` 与 `TradingEngine` 物理拦截网，确保软性约束的顺滑执行。

### Fixed
- 修复了在 0721 大反弹中因板块一票否决导致的踏空问题。
- 优化了对冲头寸的退出逻辑，减少了 Theta 损耗。

## [v4.5.6] - 2026-07-17
### Added
- **执行快照感知 (Execution Snapshot)**：在 `buy_stock` 和 `sell_stock` 执行瞬间自动抓取完整的技术指标快照并存入日志。
- **后交易轨迹审计 (Trajectory Audit)**：`ReviewAgent` 新增回溯机制，自动评估过去 7 天卖出决策的质量（识别卖早了或卖得好）。
- **指标增强可视化**：升级 `visualizer.py`，支持 RSI、ATR 子图和多面板 K 线复盘图。

### Fixed
- **过度交易与左右挨打 (Whipsaw & Over-trading) 深度修复**:
  - **日内 4 小时物理冷静期**: 在 `BuyStockTool` 中引入了绝对离场观察期。一旦触发 MO/LO 卖出离场，物理层将死锁 4 小时禁买，严禁在同一交易时段反复进出。
  - **止损逻辑纠偏**: 修复了 `PortfolioManager` 将防御性挂单（TSMPCT/LIT）误判为硬止损的 Bug。现在仅 execution-based 卖出才会触发冷静期。
  - **动态迟滞缓冲区 (Dynamic Hysteresis)**: 止损微调阈值升级为 `max(1.5%, 0.5 * ATR)`，减少高波动环境下的无效订单微调。
  - **交易摩擦税认知注入**: 在 CIO 决策大脑中注入 0.5% 摩擦成本评估模型，强制要求每一笔交易必须具备远超损耗的确定性理由。
  - **缺失依赖修复**: 补齐了 `PortfolioManager` 中缺失的 `pytz` 导入，确保时区处理稳定性。

## [v4.5.5] - 2026-07-09
### Added
- **板块资金流向一票否决 (Sector Flow Veto)**: 在 `PortfolioManager` 中集成 `SectorAnalyst` 研报。若板块资金流出，物理层强行拦截买入指令。
- **Tier 1 领涨股呼吸空间 (Breathing Space)**: 强制 Tier 1 标的追踪止损不小于 3.5x ATR，防止牛股被随机波动洗出。

### Fixed
- **过度交易迟滞缓冲区 (Hysteresis Upgrade)**: 追踪止损调价缓冲区从 0.8% 提高至 1.0%，防止无效微调损耗。
- **Whipsaw 物理拦截**: 将 Hard Stop 后的禁买冷却期调整为物理锁死 3 天，禁止报复性买入。

## [v4.5.4] - 2026-07-08

## [v4.5.3] - 2026-07-07
### Added
- **对冲动态退出协议 (Dynamic Hedge Unwinding)**: 在 `STRATEGIC_SYSTEM_PROMPT` 中注入 Theta 损耗敏感度。强制要求 CIO 在风险评分从 <70 修复至 >80 时，优先平仓 Protective Puts。
- **非对称对冲原则**: 明确在评分 >75 时禁用昂贵的期权对冲，转而使用 `TSMPCT` (追踪止损) 作为零成本防御手段。

## [v4.5.1] - 2026-07-06
### Added
- **深度思考树协议 (Tree-of-Thought, ToT)**: 为 CIO 引入多路径博弈推演机制。针对高信心或高冲突标的，强制执行 [BULL] / [BEAR] / [TAIL_RISK] 三路并行分析与 [SYNTHESIS] 合成决策，过滤诱多陷阱。
- **ToT 触发逻辑**: 优化 Token 使用，仅在专家研报冲突或 Tier 1 标的时激活深度推演模式。

## [v4.5.0] - 2026-07-06
### Added
- **叙事量化引擎 (Narrative Momentum Engine, NME)**: 引入 `NarrativeAnalyst` 专家，支持全网叙事密度、主题动能及情绪偏移的量化探测。
- **CIO 叙事智能注入**: 升级 `STRATEGIC_SYSTEM_PROMPT` 至 v4.5.0，使 CIO 具备基于叙事共振（加仓）和叙事偏移（避险）的“直觉”决策能力。
- **全链路集成**: 在 Phase 3 统筹中心正式集成 NME，并将叙事简报作为核心决策权重转发至 CIO。

## [v4.4.2] - 2026-07-06
### Added
- **Orchestrator 认知增强**: 在决策前注入 `recently_weeded_out` 冷静期冲突提示，直接在专家研报环节拦截无效的 Whipsaw 回补尝试。
- **高频交易预警**: 在 Orchestrator 中根据交易历史自动识别并标记“高频反复交易”标的，警示 CIO 避免陷入过度交易陷阱。

### Fixed
- **Hysteresis 拦截逻辑闭环**: 将 `SellStockTool` 和 `BuyStockTool` 中的迟滞缓冲区拦截返回值由 `success: True` 改为 `success: False`。配合 Prompt 更新，强制让 CIO 意识到微小调价已被物理层拒绝，杜绝“空转”决策。
- **V-Recovery 严格约束**: 在 System Prompt 中量化了 V-Recovery 的判定标准（收复 50% 跌幅 + 2x 成交量），严防 Bull Trap。

## [v4.4.1] - 2026-06-29

### Optimized
- **V-Recovery vs Bull Trap Boundaries**: Refined re-entry criteria for stopped-out positions. Requires 50% price recovery, `vol_ratio > 2.0x`, and confirmation within 48h. Added "Anti Bull Trap" checks to prevent re-entering on low volume or at major resistance levels.
- **Tier 1 Leader Protection**: Increased ATR trailing stop multiplier from 3.0x to **3.5x - 4.0x** for high-conviction stocks, providing more room for primary bull runs.

## [4.4.0] - 2026-06-25
### Added
- **Tiered Conviction Architecture**: Introduced a differentiated defense system for "Tier 1 Leaders" (High-Conviction stocks).
  - **Conviction Parameter**: Added `conviction` parameter to `BuyStockTool` and `SellStockTool` to signal high-value positions.
  - **Relaxed Defensive Padding**: High-conviction stocks now utilize a wider 3.0x ATR trailing stop multiplier by default, preventing washout during volatile主升浪 (primary bull runs).
  - **Delayed PRR 收网**: Upgraded Profit Retention Ratio logic to only activate after 8% profit (instead of 3%) for leaders, allowing them more "breathing room" to reach 100% gains.
- **V-Recovery Exception (纠偏回补)**:
  - **Force Recovery Parameter**: Added `force_recovery` to `BuyStockTool` to bypass the 3-day `HARD_STOP` cooldown.
  - **Re-entry Logic**: Allows the CIO to immediately re-enter a position if it exhibits a high-volume (>2.0x) reversal back above the stop-out price, solving the "stopped out at the bottom" problem.
- **Enhanced CIO Strategic Brain**: Upgraded `agent/react.py` system prompt with instructions for managing the conviction tiering system and executing V-Recovery maneuvers.

## [4.3.0] - 2026-06-16
### Added
- **Strategy Quality Audit**: Implemented a mandatory audit system for trade evaluation.
- **Whipsaw Audit**: Automated back-tracking of sell decisions within 72 hours to identify "Whipsaw" (selling low, buying high) events.
- **Re-entry Friction Tracking**: Integrated real-time friction cost calculation in `react.py` when re-entering a position sold within 3 days.
- **Audit Logging**: Added `audit_logs` to daily trade logs in `trade_logger.py` for long-term strategy optimization and parameter tuning.
- **Context Enrichment**: Upgraded `review.py` to include the past 3 days of trading history in the daily review report, providing better grounding for LLM-based post-trade analysis.

## [4.2.1] - 2026-06-10
### Added
- **Execution Audit Loop**: Added regex-based intention scanning in `agent/react.py` to physically intercept and warn the CIO if a decided trading action (BUY/SELL) is not accompanied by an actual tool call, mitigating LLM "hallucination" failures during high-stress market events (like the ARM drop on 06-09).
- **Information Distillation**: Introduced `identified_certainties` to the `DecisionBriefing` schema. The `Orchestrator` now extracts hard boolean metrics (`is_volume_breakout`, `is_rsi_overbought`) from weak expert agents and displays them at the top of the briefing to prevent CIO cognitive overload.

### Changed
- **Opening Hysteria Protocol**: Refined the "Opening Hysteria" rules in `react.py`. Shifted from a rigid time-based ban to a "Volume Veto" approach. The CIO is now required to deny any breakout missing a >1.5x volume spike.
- **Dynamic Hedge Unwinding**: Upgraded the advanced options instruction in `react.py`. The CIO is now explicitly authorized to dynamically close Protective Puts when the macro risk score recovers (>70) or the Put achieves outsized defensive profits, rather than holding to expiration.

## [4.2.0] - 2026-06-08
### Added
- **MacroAnalyst Agent**: New specialized agent for "Seeing the Essence Through Phenomena". Performs structural macro analysis (e.g., jobs data quality, Fed transmission).
- **Deep Reasoning Protocol**: Upgraded `agent/react.py` (CIO Brain) to v4.2 with a dedicated Macro Reasoning Protocol to handle structural contradictions.
- **Enhanced Decision Briefing**: Phase 3 now injects deep macro narratives into the decision context.

## [4.1.1] - 2026-06-08
### Changed
- **Monitoring Window**: Expanded `is_trading_hours` from 9:30-16:00 to 8:00-17:00 ET.
- **Defensive Coverage**: Added coverage for pre-market (8:00-9:30) black-swan defense and post-market (16:00-17:00) order alignment.

### Added
- **TDD Regression Tests**: Added `tests/test_trading_hours.py` to ensure reliable trading hour detection.

## [4.0.0] - 2026-06-01
### Added
- **Market-Trading Separation**: Decoupled market data from trading logic using `BaseTradingEngine` and `LongPortTradingEngine`.
- **Option Safety Interceptor**: Physical-layer blocking for naked options and requirement of 1:100 stock-to-option ratio.
- **PRR Guard**: Profit Retention Ratio algorithm to dynamically tighten trailing stops and lock 50% of profits.
- **Risk Regime v2**: Linear interpolation for risk scores (0-100) to adjust exposure and ATR multipliers.
