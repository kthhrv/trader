# AI-Driven Market Open Trader (IG Markets)

A Python-based automated trading bot that leverages **Google Gemini** to analyze market data and **IG Markets** to execute trades during key market opens (London & New York).

## 🚀 Features

*   **AI-Powered Analysis:** Uses Google's Gemini models (via `google-generativeai`) to generate trading plans based on OHLC data and technical indicators.
*   **Automated Scheduling:** Runs automatically for London (08:00 GMT) and New York (14:30 GMT) market opens.
*   **On-Demand Mode:** Manually trigger analysis and execution for any instrument via CLI.
*   **Risk Management:** Enforces stop losses, limits, and daily loss caps (configurable).
*   **Real-Time Execution:** Uses Lightstreamer for tick-by-tick price monitoring to ensure precise entry.
*   **Spread Betting:** Optimized for UK Spread Betting accounts (Tax-Free).

## 🛠️ Prerequisites

*   **Python 3.12+**
*   **uv** (for fast Python package management)
*   **IG Markets Account** (Demo or Live)
*   **Google Gemini API Key**

## 📦 Installation

1.  **Clone the repository:**
    ```bash
    git clone <repository_url>
    cd trader
    ```

2.  **Initialize the environment:**
    ```bash
    uv sync
    ```

3.  **Configure Environment Variables:**
    Copy the example file and fill in your credentials.
    ```bash
    cp .env.example .env
    ```
    
    Edit `.env`:
    ```ini
    # IG Markets Credentials
    IG_API_KEY=your_ig_api_key
    IG_USERNAME=your_username
    IG_PASSWORD=your_password
    IG_ACC_ID=your_account_id

    # Google Gemini AI
    GEMINI_API_KEY=your_gemini_api_key

    # Settings
    IS_LIVE=false  # Set to 'true' for REAL MONEY trading
    MAX_DAILY_LOSS=50.0
    ```

## 🖥️ Usage

### 1. Automated Scheduler (Default)
Run the bot to listen for scheduled market opens:
```bash
uv run main.py
```
*   **London Open:** Checks at 07:45 GMT.
*   **NY Open:** Checks at 14:15 GMT.

### 2. On-Demand Execution (Manual)
Run the strategy immediately for a specific instrument:
```bash
uv run main.py --now --epic "CS.D.FTSE600.TODAY.IP"
```

*   `--now`: Bypasses the scheduler.
*   `--epic`: (Optional) Specify the instrument epic. Defaults to `CS.D.FTSE600.TODAY.IP`.

## 🧪 Testing

Run the test suite to verify logic without connecting to live APIs:
```bash
uv run pytest
```

## 📂 Project Structure

```
/
├── main.py                 # Entry point (CLI & Scheduler)
├── config.py               # Configuration & Safety Flags
├── .env                    # Secrets (Excluded from Git)
├── pyproject.toml          # Dependencies (uv)
├── src/
│   ├── gemini_analyst.py   # AI Analysis Module
│   ├── ig_client.py        # Broker Interaction (REST)
│   ├── stream_manager.py   # Live Price Streaming
│   └── strategy_engine.py  # Core Trading Logic
└── tests/                  # Unit Tests
```

## ⚠️ Risk Warning

**Trading financial instruments carries a high level of risk and may not be suitable for all investors.** 
*   This software is for educational purposes only.
*   Always test thoroughly in a **DEMO** environment before using real funds.
*   The authors are not responsible for any financial losses incurred.
