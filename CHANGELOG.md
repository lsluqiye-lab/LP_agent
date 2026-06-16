# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
