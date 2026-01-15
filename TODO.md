# Project Roadmap & Tech Debt

## Completed Milestones (Recent)
- [x] **Session Context**: Passed `today_high` and `today_low` (Session Extremes) to the Gemini Analyst to prevent selling bottoms/buying tops.
- [x] **Capital Preservation (Risk Floor)**: Implemented `MIN_ACCOUNT_BALANCE` check with dynamic position sizing step-down (Standard -> Min Size -> Abort).
- [x] **API Resilience**: Added retry logic (with exponential backoff) for Gemini 503/500 errors to prevent strategy aborts on transient AI outages.
- [x] **Hardened Risk Technicals**: Enforced strict **1.5x - 2.0x ATR** minimum stop loss distance in the Analyst prompt to prevent volatility stop-outs.
- [x] **Granular Price History**: Included last 2 hours of **5-minute candles** in the AI prompt context to help AI see specific "wick rejections" that 15-minute candles hide.

## Trading Strategy Enhancements
- [x] **Active Re-evaluation (Smart WAIT)**:
    - Update `TradingSignal` schema to include `validity_time_minutes`.
    - If validity expires: Re-trigger `_run_analysis()` automatically.
    - Uniform handling for both Trade TTL and WAIT Cooldowns.
- [ ] **Dynamic Breakeven Trigger**:
    - Allow Analyst to specify `breakeven_trigger_r` (e.g., 1.0, 1.5, 2.0, or 0 to disable) in the `TradingSignal`.
    - Goal: Optimize risk management for different regimes (e.g., tight BE for breakouts, wide BE for range trading).
## Architecture & Infrastructure
- [ ] **Single Stream Architecture**: 
    - Decouple "Data Recording" from "Trade Execution".
    - `StreamManager` should ideally be a singleton service that writes to DB.
    - `StrategyEngine` should poll DB or subscribe to internal events, rather than opening a second stream to IG.
- [ ] **Trigger Price Persistence**:
    - Add `trigger_price` column to `trade_log` table.
    - Log the *planned* entry price separately from the *execution* price (which includes slippage).
    - Update `main.py` reporting to show "Entry vs Trigger".
- [ ] **Structured Logging**:
    - Implement `logging.LoggerAdapter` in `StrategyEngine`.
    - Prefix all trade-specific logs with `[DB:<id>]` for better traceability during concurrent strategy execution.

## UI Improvements
- [ ] **Trade Detail Chart**: Add "Trigger Price" line if available.