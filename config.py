import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Project Paths ---
ROOT_DIR = Path(__file__).parent
LOGS_DIR = ROOT_DIR / "logs"
TRADING_PLAN_PATH = ROOT_DIR / "trading_plan.json"

# --- Safety & Risk Management ---
# Default to FALSE (Paper Trading) if not explicitly set to "true"
IS_LIVE = os.getenv("IS_LIVE", "false").lower() == "true"
RISK_PER_TRADE_PERCENT = float(
    os.getenv("RISK_PER_TRADE_PERCENT", 0.01)
)  # Risk 1% of account balance per trade
CONSECUTIVE_LOSS_LIMIT = int(os.getenv("CONSECUTIVE_LOSS_LIMIT", 4))

# --- API Keys ---
IG_API_KEY = os.getenv("IG_API_KEY")
IG_USERNAME = os.getenv("IG_USERNAME")
IG_PASSWORD = os.getenv("IG_PASSWORD")
IG_ACC_ID = os.getenv("IG_ACC_ID")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- Home Assistant ---
HA_API_URL = os.getenv("HA_API_URL", "http://192.168.0.207:8123")
HA_ACCESS_TOKEN = os.getenv("HA_ACCESS_TOKEN")
HA_NOTIFY_ENTITY = os.getenv("HA_NOTIFY_ENTITY", "notify.mobile_app_pixel_8")

# --- Configuration Checks ---
if not GEMINI_API_KEY:
    print("WARNING: GEMINI_API_KEY is not set.")

if IS_LIVE:
    print("WARNING: RUNNING IN LIVE TRADING MODE.")
else:
    print("INFO: Running in PAPER TRADING mode.")
