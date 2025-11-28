# AI-Driven Market Open Trader

A fully automated trading bot that leverages Google's **Gemini 3 Pro Preview** (via `gemini-2.0-flash-thinking-exp` reasoning models) to analyze market opens and execute breakout strategies on the IG Markets platform.

## 🚀 Key Features

*   **Multi-Market Support:** Pre-configured strategies for **London (FTSE 100)**, **New York (S&P 500)**, and **Tokyo (Nikkei 225)**.
*   **AI Analyst (Gemini 3):** Uses **Chain-of-Thought** reasoning to synthesize technical data and news, and dynamically decides on entry type (`INSTANT` or `CONFIRMATION`) and **trailing stop (on/off)** before making a decision.
*   **Post-Mortem Analysis:** Generates detailed AI-powered reports on completed trades, analyzing execution, slippage, and plan adherence.
*   **News Integration:** Real-time sentiment analysis using top headlines from Google News (via `feedparser`).
*   **Technical Analysis:** Automatically calculates **ATR** (Volatility), **RSI** (Momentum), and **EMA** (Trend) using `pandas-ta`.
*   **Risk Management:** Enforces mandatory Stop Losses, checks Risk/Reward ratios, and calculates dynamic position sizing based on account balance.
*   **Database Logging:** Stores all trade decisions and real-time monitoring data in a local SQLite database for historical analysis.
*   **Holiday Filter:** Automatically skips trading on public holidays for the UK, US, and Japan.
*   **Timezone Aware:** Scheduler automatically handles DST shifts for London, NY, and Tokyo.

## 🛠 Tech Stack

*   **Language:** Python 3.12+
*   **AI Model:** Google Gemini (`gemini-3-pro-preview`)
*   **Broker API:** IG Markets (via `trading-ig`)
*   **Database:** SQLite (`data/trader.db`)
*   **Data Processing:** `pandas`, `pandas-ta`
*   **News Fetching:** `feedparser` (RSS)
*   **Scheduling:** `APScheduler`

## 📦 Installation

1.  Clone the repository.
2.  Install dependencies using `uv` (recommended) or `pip`:
    ```bash
    uv sync
    ```
3.  Set up your `.env` file (see `.env.example`):
    ```env
    IG_USERNAME=your_username
    IG_PASSWORD=your_password
    IG_API_KEY=your_api_key
    IG_ACC_ID=your_account_id
    GEMINI_API_KEY=your_google_ai_key
    IS_LIVE=false
    ```

## 🖥 Usage

### 1. Scheduler Mode (Default)
Run the bot to wait for scheduled market opens:
```bash
python main.py
```
*   **London:** 07:45 London Time (Mon-Fri)
*   **New York:** 09:15 NY Time (Mon-Fri)
*   **Tokyo:** 08:45 Tokyo Time (Mon-Fri)

### 2. Manual Execution (On-Demand)
Run a strategy immediately for testing:
```bash
# Run London Strategy
python main.py --now --market london

# Run NY Strategy
python main.py --now --market ny

# Run Nikkei Strategy
python main.py --now --market nikkei
```

### 3. Post-Trading Analysis
Analyze recent performance:
```bash
# View recent trades (Default 5)
python main.py --recent-trades

# View last 10 trades
python main.py --recent-trades 10

# Generate Post-Mortem Analysis for a specific Deal ID
python main.py --post-mortem <DEAL_ID>
```

### 4. News Check Only
Fetch and print the latest market news without trading:
```bash
python main.py --news-only --market london
```

## 🛡 Safety Mechanisms

*   **Paper Trading Default:** `IS_LIVE` defaults to `false`.
*   **Mandatory Stops:** The engine refuses to place orders without a defined Stop Loss.
*   **Dynamic Trailing Stops:** Profit-taking stops can be dynamically moved to **Breakeven (at 1.5R)** and then **trailed based on 2.0x ATR** to protect and maximize gains, as chosen by Gemini.
*   **Chain-of-Thought:** The AI must justify its trade with a step-by-step rationale before generating a signal.

## 📂 Project Structure

*   `src/strategy_engine.py`: Orchestrates data fetching, AI analysis, and execution.
*   `src/gemini_analyst.py`: Wraps the Gemini API with Chain-of-Thought prompting.
*   `src/ig_client.py`: Handles IG API authentication and order placement.
*   `src/news_fetcher.py`: Fetches real-time news headlines.
*   `main.py`: Entry point and scheduler configuration.

## ⚠️ Risk Warning

**Trading financial instruments carries a high level of risk and may not be suitable for all investors.** 
*   This software is for educational purposes only.
*   Always test thoroughly in a **DEMO** environment before using real funds.
*   The authors are not responsible for any financial losses incurred.
