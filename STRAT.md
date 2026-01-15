# Trading Strategy & Rules "The Constitution"

## Core Philosophy
The bot acts as an **Intraday Breakout & Momentum System**, targeting the opening volatility ("Open Drive") of major global indices. It leverages AI (Gemini) to synthesize technical structure with fundamental news sentiment, applying institutional-grade risk management to survive the noise.

## 1. Market Selection
We trade high-liquidity indices during their specific "Open" windows (first 90 minutes).
*   **Tier A (Aggr):** Nasdaq 100, DAX 40.
*   **Tier B (Std):** FTSE 100, S&P 500, Nikkei 225, ASX 200.

## 2. Risk Management (The "Brakes")
*   **Base Risk:** 1.0% of Account Balance per trade.
*   **Risk Scaling:**
    *   **1.25x**: High-conviction markets (Nasdaq).
    *   **0.5x**: Low-liquidity/High-spread markets (ASX).
*   **Stop Loss:**
    *   **Structural:** Must be placed beyond key technical levels (Swing High/Low, EMA).
    *   **Volatility Based:** Minimum distance of **1.5x to 2.0x ATR**.
    *   **Pre-Open Safety:** For entries near the open (e.g., HH:55), the Stop **MUST** clear the *entire* session high/low volatility structure to survive the opening flush.
    *   **Spread Adjusted:** The calculated Stop Loss is **widened** by the current market spread at the moment of execution to prevent "bid/ask" stop-outs.
*   **Dynamic Sizing:** Position size is calculated *after* the Stop Loss is determined to ensure the monetary risk (£) remains constant regardless of stop distance.

## 3. Entry Logic (The "Gas")
*   **Trigger Type:** **INSTANT (Touch)**.
    *   *Logic:* We place "Market if Touched" orders. We do not wait for candle closes, prioritizing entry speed to catch momentum.
*   **Setup:** Breakout of Pre-Market Range or Opening Range.
*   **Extension Rule (No Chasing):** Do NOT enter a trade if the entry price is more than **1.5x ATR** away from the 20-period EMA. Wait for a pullback or skip.
*   **Context Awareness (AI):**
    *   **Session Extremes:** The AI is given `Today's High` and `Today's Low`.
    *   **Rule:** "Do NOT Buy the High / Sell the Low" unless volatility is high (Breakout).
    *   **Volatility Regime:**
        *   **High Vol:** Breakout Strategy preferred.
        *   **Low Vol:** Mean Reversion (Fade) Strategy preferred.

## 4. Exit Logic
*   **Hard Stop:** Fixed price level (Initial SL).
*   **Trailing Stop:** Active for trend-following trades.
    *   **Trigger:** When price moves **1.5x ATR** in favor.
    *   **Step:** Trail at **2.0x ATR** behind price.
*   **Time-Based:** Auto-close at end of session (if configured) or timeout after 90 mins if entry not hit.

## 5. "The Vibe" (Qualitative)
*   **News:** We filter for high-impact headlines (War, Inflation, Earnings). Bearish news overrides bullish technicals (and vice-versa).
*   **Friday Effect:** Be cautious of afternoon fades.
*   **Gap Logic:** Be skeptical of massive gaps (>3x ATR) unless confirmed by strong continuation (Open Drive).

## Current "Tech Debt" / Watchlist
*   **Entry Types:** Considering re-introducing `PULLBACK` (Limit) entries to improve R:R on mean reversion trades.
*   **Trigger Persistence:** Need to store the original "Plan Entry" vs "Execution Entry" to measure slippage accurately.
