# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
