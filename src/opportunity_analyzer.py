import logging
from datetime import datetime, timedelta
import pandas as pd
import pandas_ta as ta

try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Fallback for older python or if backports is installed
    try:
        from backports.zoneinfo import ZoneInfo
    except ImportError:
        import pytz

        # Adapt pytz to behave like ZoneInfo for simple lookups
        def ZoneInfo(key):
            return pytz.timezone(key)


from src.ig_client import IGClient
from src.database import get_db_connection, fetch_candles_range

logger = logging.getLogger(__name__)


class OpportunityAnalyzer:
    def __init__(self, client: IGClient = None):
        self.client = client if client else IGClient()

    def analyze_session(
        self, market_config: dict, date_str: str = None, force_api_fetch: bool = False
    ) -> dict:
        """
        Analyzes a specific market session to detect "Power Law" moves.
        Prioritizes local DB data. API fetch for 1-min data is disabled unless forced.
        """
        epic = market_config["epic"]
        schedule = market_config["schedule"]
        strategy_name = market_config["strategy_name"]
        tz_name = schedule.get("timezone", "UTC")

        # Determine Session Start Time (Target Date)
        if date_str:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        else:
            target_date = datetime.now().date()

        # 1. Construct Aware Datetime in Market's Timezone
        try:
            market_tz = ZoneInfo(tz_name)
            # Create a naive time then attach tz
            session_start_market = datetime.combine(
                target_date,
                datetime.min.time().replace(
                    hour=schedule["hour"], minute=schedule["minute"]
                ),
            ).replace(tzinfo=market_tz)

            # 2. Convert to System Local Time (Naive)
            session_start = session_start_market.astimezone(None).replace(tzinfo=None)

        except Exception as e:
            logger.error(
                f"Timezone conversion failed: {e}. Falling back to naive schedule time."
            )
            session_start = datetime.combine(
                target_date,
                datetime.min.time().replace(
                    hour=schedule["hour"], minute=schedule["minute"]
                ),
            )

        # 1. Pre-market context (Wait for ATR warmup - fetch more data)
        # 2. Session first 60 minutes for "Power Law" move

        fetch_start = session_start - timedelta(minutes=200)
        fetch_end = session_start + timedelta(
            minutes=90
        )  # 90 mins after open covers the standard timeout

        # Format for IG API / DB
        start_fmt = fetch_start.strftime("%Y-%m-%d %H:%M:%S")
        end_fmt = fetch_end.strftime("%Y-%m-%d %H:%M:%S")

        logger.info(f"Analyzing {strategy_name} ({epic}) for {target_date}...")

        try:
            # 1. Fetch Daily Data for Macro Context (ATR)
            df_daily = pd.DataFrame()
            try:
                df_daily = self.client.fetch_historical_data(epic, "D", 20)
            except Exception as e:
                logger.warning(f"Could not fetch daily data: {e}")

            daily_atr = 0.0
            if not df_daily.empty and len(df_daily) >= 14:
                df_daily["ATR"] = ta.atr(
                    df_daily["high"], df_daily["low"], df_daily["close"], length=14
                )
                daily_atr = df_daily["ATR"].iloc[-1]

            # 2. Fetch Intraday Data for Session Analysis
            # Try Local DB First
            local_data = fetch_candles_range(epic, start_fmt, end_fmt)

            df = pd.DataFrame()
            if local_data:
                logger.info(f"Using local DB data ({len(local_data)} candles).")
                df = pd.DataFrame(local_data)
                if "timestamp" in df.columns:
                    df.set_index("timestamp", inplace=True)
            elif force_api_fetch:
                logger.info("Local data missing. Fetching from IG API (Forced)...")
                df = self.client.fetch_historical_data_by_range(
                    epic, "1Min", start_fmt, end_fmt
                )
            else:
                logger.info("Local data missing and API fetch not forced. Skipping.")
                return {
                    "status": "SKIPPED",
                    "reason": "No local data. Use --force-api-fetch to download.",
                }

            if df.empty:
                return {
                    "status": "NO_DATA",
                    "reason": "No data found (Local or API) for this period.",
                }

            # Ensure proper typing
            cols = ["open", "high", "low", "close"]
            for col in cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col])

            df.index = pd.to_datetime(df.index)

            # 3. Identify "Power Law" Move in the first 60 mins of session
            session_window = df[
                (df.index >= session_start)
                & (df.index <= session_start + timedelta(minutes=60))
            ]

            if session_window.empty:
                return {
                    "status": "NO_SESSION_DATA",
                    "reason": "No data found for the session window.",
                }

            session_high = session_window["high"].max()
            session_low = session_window["low"].min()
            session_range = session_high - session_low

            # Power Factor: Session Range / Daily ATR
            power_factor = session_range / daily_atr if daily_atr > 0 else 0

            # Threshold: 0.5 (50% of daily range in 1 hour is significant)
            is_power_law = power_factor >= 0.5

            # Determine Direction
            open_price = session_window.iloc[0]["open"]
            close_price = session_window.iloc[-1]["close"]
            direction = "BULLISH" if close_price > open_price else "BEARISH"

            # 4. Check Database for Bot Activity
            conn = get_db_connection()
            cursor = conn.cursor()

            db_start = session_start.isoformat()
            db_end = fetch_end.isoformat()

            cursor.execute(
                """
                SELECT * FROM trade_log 
                WHERE epic = ? 
                AND timestamp BETWEEN ? AND ?
            """,
                (epic, db_start, db_end),
            )

            trades = [dict(row) for row in cursor.fetchall()]
            conn.close()

            bot_status = "NO_ACTION"
            trade_details = None

            if trades:
                # Prioritize checking for executed trades
                executed = [
                    t
                    for t in trades
                    if t["outcome"]
                    in ["LIVE_PLACED", "DRY_RUN_PLACED", "WIN", "LOSS", "CLOSED"]
                ]
                pending_timed_out = [t for t in trades if t["outcome"] == "TIMED_OUT"]
                waited = [t for t in trades if t["outcome"] == "WAIT"]
                error = [t for t in trades if t["outcome"] == "AI_ERROR"]

                if executed:
                    bot_status = "TRADED"
                    trade_details = executed[0]  # Take first executed
                elif pending_timed_out:
                    bot_status = (
                        "MISSED_EXECUTION"  # Signal was there, but price didn't trigger
                    )
                    trade_details = pending_timed_out[0]
                elif waited:
                    bot_status = "MISSED_AI"  # AI said wait, but market moved
                    trade_details = waited[0]
                elif error:
                    bot_status = "ERROR"
                    trade_details = error[0]

            # 5. Synthesize Result
            result = {
                "date": target_date.isoformat(),
                "market": strategy_name,
                "is_power_law": is_power_law,
                "power_factor": round(power_factor, 2),
                "daily_atr": round(daily_atr, 2),
                "session_range": round(session_range, 2),
                "direction": direction,
                "bot_status": bot_status,
                "trade_details": trade_details,
            }

            return result

        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            return {"status": "ERROR", "reason": str(e)}
