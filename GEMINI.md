# **GEMINI.md \- Automated IG Trading Bot Context**

## **1\. Project Overview**

Name: AI-Driven Market Open Trader (IG Markets)  
Goal: Automate trading strategies for UK (08:00 GMT) and US (14:30 GMT) market opens.  
Workflow:

1. **Scheduler:** Wake up 15 mins before market open.  
2. **Data Fetch:** Retrieve OHLC data and recent news via IG API / External Sources.  
3. **Strategy (AI):** Send data to Gemini \-\> Receive JSON triggers (Entry, SL, TP).  
4. **Execution (Bot):** Monitor live prices via IG Streaming API. Execute orders if triggers are met.

## **2\. Tech Stack & Libraries**

* **Language:** Python 3.12+  
* **Python Packaging** Use uv native, ie pyproject.toml and uv.lock
* **Testing** Create tests as we go, mock all api and http calls
* **Broker API:** IG Markets (IG.com) via trading-ig library (Community wrapper).  
  * *Crucial:* Use IGService for REST and IGStreamService for Lightstreamer.  
* **AI Model:** Google Gemini via google-generativeai SDK.  
* **Scheduling:** APScheduler or schedule library (Persistent process).  
* **Data:** pandas for DataFrames, ta-lib (optional) for technical indicators.  
* **Resilience:** tenacity for API retry logic.

## **3\. Architecture & Directory Structure**

/  
├── main.py                 \# Entry point & Scheduler loops  
├── config.py               \# Envs (API\_KEY, ACC\_ID) \- NO SECRETS IN CODE  
├── trading\_plan.json       \# Daily generated plan (Output from Gemini)  
├── logs/                   \# Execution logs  
└── src/  
    ├── ig\_client.py        \# IG Auth, Market Data, Order Placement  
    ├── gemini\_analyst.py   \# Prompts Gemini, Validates JSON response  
    ├── stream\_manager.py   \# Handles Lightstreamer price updates  
    └── strategy\_engine.py  \# Checks live price vs. JSON triggers

## **4\. Coding Guidelines (STRICT)**

### **A. Safety & Risk Management**

1. **Paper Trading First:** All code must check a config.IS\_LIVE flag. Default to False (Demo Account).  
2. **Stop Losses:** NEVER submit an order without a stopLevel or guaranteedStop attached.  
3. **Hard Limits:** Implement a max\_daily\_loss check before any new trade execution.

### **B. Gemini Interaction (The "Analyst")**

1. **Output Format:** Gemini must ALWAYS return data in valid **JSON**.  
   * *Bad:* "I think you should buy FTSE at 7500."  
   * *Good:* {"ticker": "FTSE100", "action": "BUY", "entry": 7500, "stop": 7450, "confidence": "high"}  
2. **Context Injection:** When prompting Gemini, always include:  
   * Current volatility (ATR).  
   * Key support/resistance levels calculated via Python.  
   * Recent trend data (last 5-10 candles).

### **C. IG API Specifics**

1. **Trade Type** use "spread betting" as its tax free in the UK
2. **Rate Limiting:** IG has strict limits (approx 60 requests/minute). Use time.sleep() or tenacity decorators on REST calls.  
3. **Session Handling:** IG V3 tokens expire. The ig\_client.py must handle re-authentication automatically.  
4. **Streaming:** Use IGStreamService to listen for L1 prices (bids/offers). Do not poll the REST API for live prices (too slow/expensive).

## **5\. Typical Workflow Implementation**

When asked to write the "Strategy Generation" code, follow this pattern:

1. **Python:** Fetches last 4 hours of 15-min candles for FTSE100.  
2. **Python:** Formats data into a CSV string.  
3. **Gemini Prompt:** "Analyze this price action. Identify a breakout trigger for the London Open. Return JSON with 'trigger\_price' and 'direction'."  
4. **Python:** Parses JSON.  
5. **Python:** Sets a "Watch" on the stream. If price \> trigger\_price, execute BUY order.

## **6\. Anti-Patterns (Do Not Do This)**

* Do not ask Gemini to "execute the trade" (it cannot accessing the API).  
* Do not hardcode API keys or passwords. Use os.getenv().  
* Do not use while True loops without time.sleep().
