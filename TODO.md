# Project Roadmap & Tech Debt

## Trading Strategy Enhancements
- [ ] **Refine Entry Types**: Currently, `EntryType.INSTANT` acts as a "Market if Touched" logic, but logic assumes Breakout. 
    - Split into:
        - `BREAKOUT` (Stop Entry): Buy if Price >= Level.
        - `PULLBACK` (Limit Entry): Buy if Price <= Level.
    - Fix logic in `StrategyEngine` to handle the conditional trigger direction correctly for Pullbacks.
- [x] **Session Context**: Pass `today_high` and `today_low` (Session Extremes) to the Gemini Analyst.
    - Goal: Prevent "Selling the Bottom" or "Buying the Top" traps.
- [ ] **Granular Price History**: Include last 2 hours of **5-minute candles** in the AI prompt context.
    - Goal: Help AI see specific "wick rejections" that 15-minute candles hide.

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
