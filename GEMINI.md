# Gemini Context & Conventions

## Project Overview
**Name**: Trader
**Type**: Automated Trading Bot & Dashboard
**Goal**: Analyze market data (IG Markets), generate trading signals using Google Gemini AI, execute trades, and visualize performance via a Web UI.

## Tech Stack

### Core
- **Language**: Python 3.12+
- **Dependency Manager**: `uv` (primary), `pip` (compatible)
- **Configuration**: `pyproject.toml`, `.env`

### Key Libraries (Python)
- **AI/ML**: `google-generativeai` (Gemini 2.0 Flash Thinking/Pro)
- **Broker API**: `trading-ig` (IG Markets REST API)
- **Data Analysis**: `pandas`, `pandas-ta` (Technical Analysis)
- **Web Framework**: `reflex` (Full-stack Python UI)
- **Database**: `sqlite3` (Standard library)
- **Scheduler**: `apscheduler`
- **Testing**: `pytest`, `pytest-cov`

### Key Libraries (Node.js)
- **Streaming**: `lightstreamer-client` (Used for real-time market data streaming service)

## Folder Structure

```text
/
├── main.py                 # Entry point for the trading bot
├── config.py               # Configuration loader
├── pyproject.toml          # Python dependencies & project metadata
├── package.json            # Node.js dependencies (for stream service)
├── run_ui.sh               # Helper script to launch the Web UI
├── src/                    # Core Application Logic
│   ├── database.py         # SQLite connection & schema management
│   ├── gemini_analyst.py   # AI Signal generation (Gemini API wrapper)
│   ├── ig_client.py        # Wrapper for IG Markets API
│   ├── strategy_engine.py  # specific trading strategies
│   ├── stream_service.js   # Node.js service for Lightstreamer (IG Realtime)
│   └── ...
├── web_ui/                 # Reflex Web Interface
│   ├── rxconfig.py         # Reflex configuration
│   └── web_ui/             # UI source code
├── tests/                  # Test Suite
│   ├── mocks.py            # Mock objects for API testing
│   └── test_*.py           # Unit and E2E tests
├── data/                   # Persistent storage (SQLite DB)
└── logs/                   # Application logs
```

## Key Conventions

### Database (SQLite)
- **Location**: `data/trader.db`
- **Access**: Via `src/database.py`.
- **Schema**:
    - `trade_log`: Stores all trade executions, outcomes, and AI reasoning.
- **Pattern**: `get_db_connection()` returns `sqlite3.Row` for dict-like access.

### AI Integration (Gemini)
- **Module**: `src/gemini_analyst.py`
- **Model**: Uses structured output (Pydantic models) to enforce strict JSON schemas for trading signals (`Action`, `EntryType`, `StopLoss`, etc.).
- **Usage**: Analyzes market context and technical indicators to return actionable signals.

### Trading Logic
- **Execution**: `main.py` orchestrates the loop (Fetch Data -> Analyze -> Execute).
- **IG Integration**: `src/ig_client.py` handles REST calls; `src/stream_service.js` handles real-time price updates (likely via inter-process communication or file/socket).

### Testing
- **Framework**: `pytest`
- **Location**: `tests/`
- **Pattern**: Extensive use of mocks (`tests/mocks.py`) to simulate IG Markets and Gemini API responses.
- **Coverage**: Includes unit tests for logic and E2E tests for flows (`test_e2e_flow.py`).

### Web UI
- **Framework**: Reflex
- **Entry**: `web_ui/web_ui.py`
- **Config**: `web_ui/rxconfig.py`
- **Run**: Typically via `reflex run` (or `run_ui.sh`).

### Outstanding Issues
- **Liveness Monitoring**: we should have a 3rd party ping the system and alert me if non-reachable, non-healthy
- **Error Alerting**: system should notify me of errors, not just crashes but Errors in the logs
