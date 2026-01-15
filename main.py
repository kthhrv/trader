import logging
import signal
import sys
import argparse
import warnings
import subprocess
import os  # Added os import
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
from apscheduler.schedulers.blocking import BlockingScheduler

from src.strategy_engine import StrategyEngine
from src.news_fetcher import NewsFetcher
from src.database import (
    fetch_trade_data,
    save_post_mortem,
    fetch_recent_trades,
    fetch_active_trades,
    sync_active_trade,
    update_trade_outcome,
    fetch_trades_in_range,
    delete_trade_log,
    save_candles_batch,
)
from src.gemini_analyst import (
    GeminiAnalyst,
    TradingSignal,
    Action,
    EntryType,
)  # Added imports
from src.ig_client import IGClient
from src.trade_monitor_db import TradeMonitorDB
from src.stream_manager import StreamManager
from src.scorecard import generate_scorecard
from src.opportunity_analyzer import OpportunityAnalyzer
from src.notification_service import HomeAssistantNotifier, HANotificationHandler


warnings.simplefilter(action="ignore", category=FutureWarning)

# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)

# Initialize Notification Service
notifier = HomeAssistantNotifier()

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/trader.log"),
        logging.StreamHandler(sys.stdout),
        HANotificationHandler(notifier),
    ],
)
logger = logging.getLogger(__name__)


def get_version_info() -> str:
    """
    Returns the current code version.
    Prioritizes GIT_COMMIT_SHA env var (for Docker), falls back to local git command.
    """
    # 1. Check Environment Variable (Production/Docker)
    env_sha = os.getenv("GIT_COMMIT_SHA")
    if env_sha:
        return f"{env_sha[:7]} ({env_sha})"

    # 2. Check Local Git (Development)
    try:
        full_hash = (
            subprocess.check_output(["git", "rev-parse", "HEAD"])
            .decode("ascii")
            .strip()
        )
        short_hash = (
            subprocess.check_output(["git", "rev-parse", "--short", "HEAD"])
            .decode("ascii")
            .strip()
        )
        return f"{short_hash} ({full_hash})"
    except Exception:
        return "Unknown Version"


# Define market configurations once
MARKET_CONFIGS = {
    "london": {
        "epic": "IX.D.FTSE.DAILY.IP",
        "strategy_name": "LONDON OPEN",
        "news_query": "FTSE 100 UK Economy",
        "schedule": {
            "day_of_week": "mon-fri",
            "hour": 7,
            "minute": 55,
            "timezone": "Europe/London",
        },
        "timeout_seconds": 5400,  # 90 minutes
        "max_spread": 2.0,
        "min_size": 0.01,
    },
    "ny": {
        "epic": "IX.D.SPTRD.DAILY.IP",
        "strategy_name": "NY OPEN",
        "news_query": "S&P 500 US Economy",
        "schedule": {
            "day_of_week": "mon-fri",
            "hour": 9,
            "minute": 25,
            "timezone": "America/New_York",
        },
        "timeout_seconds": 5400,  # 90 minutes
        "max_spread": 1.6,
        "min_size": 0.01,
    },
    "nikkei": {
        "epic": "IX.D.NIKKEI.DAILY.IP",
        "strategy_name": "NIKKEI OPEN",
        "news_query": "Nikkei 225 Japan Economy",
        "schedule": {
            "day_of_week": "mon-fri",
            "hour": 8,
            "minute": 55,
            "timezone": "Asia/Tokyo",
        },
        "timeout_seconds": 5400,  # 90 minutes
        "max_spread": 8.0,
        "min_size": 0.01,
    },
    "germany": {
        "epic": "IX.D.DAX.DAILY.IP",
        "strategy_name": "DAX OPEN",
        "news_query": "DAX 40 Germany Economy",
        "schedule": {
            "day_of_week": "mon-fri",
            "hour": 7,
            "minute": 55,
            "timezone": "Europe/London",
        },
        "timeout_seconds": 5400,
        "max_spread": 2.5,
        "min_size": 0.01,
    },
    "australia": {
        "epic": "IX.D.ASX.MONTH1.IP",
        "strategy_name": "ASX OPEN",
        "news_query": "ASX 200 Australia Economy",
        "schedule": {
            "day_of_week": "mon-fri",
            "hour": 9,
            "minute": 55,
            "timezone": "Australia/Sydney",
        },
        "timeout_seconds": 5400,
        "max_spread": 3.0,
        "risk_scale": 1.0,
        "min_size": 0.01,
    },
    "us_tech": {
        "epic": "IX.D.NASDAQ.CASH.IP",
        "strategy_name": "NASDAQ OPEN",
        "news_query": "Nasdaq 100 US Tech Sector",
        "schedule": {
            "day_of_week": "mon-fri",
            "hour": 9,
            "minute": 25,
            "timezone": "America/New_York",
        },
        "timeout_seconds": 5400,
        "max_spread": 2.0,
        "min_size": 0.01,
    },
}


def run_opportunity_check(market_key: str, force_api_fetch: bool = False):
    """
    Checks for missed 'Power Law' moments for the given market today.
    """
    if market_key not in MARKET_CONFIGS:
        logger.error(f"Unknown market key: {market_key}")
        return

    config = MARKET_CONFIGS[market_key]
    analyzer = OpportunityAnalyzer()

    print(
        f"\nAnalyzing {config['strategy_name']} for potential missed opportunities..."
    )
    result = analyzer.analyze_session(config, force_api_fetch=force_api_fetch)

    print(f"{'=' * 60}")
    print(f"OPPORTUNITY REPORT: {result['date']} ({result['market']})")
    print(f"{'=' * 60}")

    if result.get("status") in ["NO_DATA", "ERROR", "NO_SESSION_DATA", "SKIPPED"]:
        print(f"Analysis Status: {result.get('status')}")
        print(f"Reason: {result.get('reason')}")
    else:
        print(f"Session Range:  {result['session_range']} points")
        print(f"Daily ATR:      {result['daily_atr']}")
        print(f"Power Factor:   {result['power_factor']} (Threshold: 0.5)")
        print(f"Direction:      {result['direction']}")
        print(f"{'-' * 60}")
        print(f"Was Power Law?  {'YES' if result['is_power_law'] else 'NO'}")
        print(f"Bot Status:     {result['bot_status']}")

        if result["is_power_law"]:
            if result["bot_status"] == "TRADED":
                outcome = result["trade_details"]["outcome"]
                pnl = result["trade_details"].get("pnl", "N/A")
                print(f"SUCCESS: Bot caught the move. Outcome: {outcome} (PnL: {pnl})")
            elif result["bot_status"] == "MISSED_AI":
                print("MISSED (AI): Market moved, but AI advised WAIT.")
                if result["trade_details"]:
                    print(
                        f"AI Reasoning: {result['trade_details']['reasoning'][:100]}..."
                    )
            elif result["bot_status"] == "MISSED_EXECUTION":
                print(
                    "MISSED (EXECUTION): Signal generated but trade TIMED_OUT (Entry not hit)."
                )
            elif result["bot_status"] == "NO_ACTION":
                print("MISSED (OFFLINE): No logs found. Bot may not have run.")
        else:
            print("Market condition was normal (not a Power Law event).")

    print(f"{'=' * 60}\n")


def run_weekly_powerlaw_check(force_api_fetch: bool = False):
    """
    Iterates through the current week (Monday to Today) and checks all markets
    for Power Law opportunities.
    """
    logger.info("Starting Weekly Power Law Event Scan...")

    # Initialize Client once to reuse session and avoid rate limits
    client = IGClient()
    analyzer = OpportunityAnalyzer(client=client)

    today = datetime.now().date()

    # Calculate Monday of the current week
    start_of_week = today - timedelta(days=today.weekday())

    print(f"\n{'=' * 100}")
    print(f"{'WEEKLY POWER LAW REPORT':^100}")
    print(f"Period: {start_of_week} to {today}")
    print(f"{'=' * 100}")
    print(
        f"{'Date':<12} | {'Market':<15} | {'Range':<8} | {'DailyATR':<8} | {'Factor':<6} | {'Status':<15} | {'Outcome'}"
    )
    print("-" * 100)

    power_law_count = 0
    missed_count = 0
    caught_count = 0

    current_date = start_of_week
    while current_date <= today:
        date_str = current_date.isoformat()

        # Skip weekends if desired, but let's just check configured markets
        # Some might run on weekends? Unlikely for indices.
        if current_date.weekday() >= 5:  # Sat/Sun
            current_date += timedelta(days=1)
            continue

        for market_key, config in MARKET_CONFIGS.items():
            # Run analysis
            # Suppress logging noise during batch run if possible, or just accept it
            try:
                result = analyzer.analyze_session(
                    config, date_str=date_str, force_api_fetch=force_api_fetch
                )

                if result.get("status") in [
                    "NO_DATA",
                    "ERROR",
                    "NO_SESSION_DATA",
                    "SKIPPED",
                ]:
                    # Don't clutter with errors for missing data (e.g. holidays) or skipped fetches
                    continue

                # Format Output
                market_name = config["strategy_name"].replace(" OPEN", "")
                rnge = str(result.get("session_range", 0))
                atr = str(result.get("daily_atr", 0))
                factor = str(result.get("power_factor", 0))
                status = result.get("bot_status", "N/A")
                is_pl = result.get("is_power_law", False)

                outcome_str = ""
                if status == "TRADED":
                    outcome_str = f"{result['trade_details']['outcome']} (PnL: {result['trade_details'].get('pnl', 'N/A')})"

                # Highlight Power Law Events
                if is_pl:
                    power_law_count += 1
                    factor_display = f"*{factor}*"
                    if status == "TRADED":
                        caught_count += 1
                    else:
                        missed_count += 1
                else:
                    factor_display = factor

                # Only print if it's interesting (Power Law OR Traded)
                # Or print all for completeness? Let's print all valid sessions.
                print(
                    f"{date_str:<12} | {market_name:<15} | {rnge:<8} | {atr:<8} | {factor_display:<6} | {status:<15} | {outcome_str}"
                )

            except Exception as e:
                logger.error(f"Error checking {market_key} on {date_str}: {e}")

        current_date += timedelta(days=1)

    print("-" * 100)
    print(f"Summary: {power_law_count} Power Law Events detected.")
    print(f"Caught: {caught_count} | Missed: {missed_count}")
    print(f"{'=' * 100}\n")


def run_test_trade(epic: str, dry_run: bool = False, trade_action: str = "BUY"):
    """
    Executes an immediate TEST trade with minimal size and tight stops.
    Bypasses Gemini analysis.
    """
    logger.info(
        f"--- STARTING TEST TRADE for {epic}, Action: {trade_action} (Dry Run: {dry_run}) ---"
    )

    try:
        client = IGClient()
        market_info = client.get_market_info(epic)
        if not market_info or "snapshot" not in market_info:
            logger.error("Could not fetch market info for test trade.")
            return

        current_offer = float(market_info["snapshot"]["offer"])
        current_bid = float(market_info["snapshot"]["bid"])

        # Determine entry, stop, and profit based on action
        action_enum = Action[trade_action]  # Convert string to enum
        entry_price = 0.0
        stop_loss = 0.0
        take_profit = 0.0

        if action_enum == Action.BUY:
            entry_price = current_offer  # Buy at the offer price
            stop_loss = entry_price - 10.0  # 10 points below entry
            take_profit = entry_price + 20.0  # 20 points above entry
            logger.info(
                f"Test BUY: Entry at {entry_price}, SL {stop_loss}, TP {take_profit}"
            )
        elif action_enum == Action.SELL:
            entry_price = current_bid  # Sell at the bid price
            stop_loss = entry_price + 10.0  # 10 points above entry
            take_profit = entry_price - 20.0  # 20 points below entry
            logger.info(
                f"Test SELL: Entry at {entry_price}, SL {stop_loss}, TP {take_profit}"
            )
        else:
            logger.error(f"Invalid trade action for test trade: {trade_action}")
            return

        test_plan = TradingSignal(
            ticker=epic,
            action=action_enum,
            entry=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence="high",
            reasoning="Manual Test Trade via CLI",
            size=0.5,  # Minimum size
            atr=5.0,  # Dummy ATR
            entry_type=EntryType.INSTANT,
            use_trailing_stop=True,
        )

        engine = StrategyEngine(
            epic,
            strategy_name="TEST_TRADE",
            dry_run=dry_run,
            verbose=True,
            max_spread=5.0,
        )  # High max spread to ensure execution
        engine.active_plan = test_plan

        logger.info(
            f"Injecting Test Plan: {trade_action} at {entry_price}, SL {stop_loss}, TP {take_profit}"
        )

        # Execute (timeout after 60s)
        engine.execute_strategy(timeout_seconds=60)

    except Exception as e:
        logger.error(f"Test trade failed: {e}")


def run_volatility_check(epic: str):
    """
    Fetches and prints current market volatility metrics (Candle Range & ATR).
    """
    logger.info(f"Checking volatility for {epic}...")
    client = IGClient()

    try:
        # 1. Fetch latest 1-minute *completed* candle (fetch 2 to get the last complete one)
        df_1m = client.fetch_historical_data(epic, "1Min", 2)

        # 2. Fetch 15-minute data for ATR calculation (50 points for warmup)
        df_15m = client.fetch_historical_data(epic, "15Min", 50)

        print(f"\n{'=' * 60}")
        print(f"VOLATILITY REPORT: {epic}")
        print(f"{'=' * 60}")

        # Analyze 1-Minute Candle
        if not df_1m.empty and len(df_1m) >= 2:
            latest_completed_1m = df_1m.iloc[-2]
            high_1m = float(latest_completed_1m["high"])
            low_1m = float(latest_completed_1m["low"])
            range_1m = round(high_1m - low_1m, 2)
            close_1m = float(latest_completed_1m["close"])
            print(f"Latest Completed 1-Min Candle ({latest_completed_1m.name}):")
            print(f"  Close: {close_1m}")
            print(f"  High:  {high_1m}")
            print(f"  Low:   {low_1m}")
            print(f"  Range: {range_1m} points")
        else:
            print("  Error: Could not fetch latest completed 1-minute candle.")

        # Analyze ATR (15-Min)
        if not df_15m.empty and len(df_15m) >= 14:
            df_15m["ATR"] = ta.atr(
                df_15m["high"], df_15m["low"], df_15m["close"], length=14
            )
            if "ATR" in df_15m.columns and not df_15m["ATR"].isnull().all():
                latest_atr = round(float(df_15m["ATR"].iloc[-1]), 2)

                # Calculate relative volatility
                avg_atr = df_15m["ATR"].mean()
                vol_ratio = latest_atr / avg_atr if avg_atr > 0 else 1.0

                vol_state = "MEDIUM"
                if vol_ratio < 0.8:
                    vol_state = "LOW"
                elif vol_ratio > 1.2:
                    vol_state = "HIGH"

                print("\nVolatility Context (15-Min Resolution):")
                print(f"  ATR (14): {latest_atr} points")
                print(f"  Avg ATR:  {round(avg_atr, 2)} points (Last 50 periods)")
                print(f"  Level:    {vol_state} (Current vs Avg)")
            else:
                print("\n  Error: ATR calculation failed (NaN values).")
        else:
            print("\n  Error: Insufficient data for ATR calculation.")

        print(f"{'=' * 60}\n")

    except Exception as e:
        logger.error(f"Failed to check volatility: {e}")


def run_list_open_positions():
    """
    Fetches and prints all currently open positions from IG.
    Useful for finding Deal IDs for manual monitoring.
    """
    logger.info("Fetching open positions from IG...")
    try:
        client = IGClient()
        client.authenticate()
        # fetch_open_positions returns a DataFrame or dict
        positions = client.service.fetch_open_positions()

        if isinstance(positions, pd.DataFrame):
            if positions.empty:
                print("\nNo open positions found.")
                return

            print(f"\n{'=' * 90}")
            print(f"{'OPEN POSITIONS':^90}")
            print(f"{'=' * 90}")

            # Select relevant columns if they exist
            cols_to_show = [
                "epic",
                "dealId",
                "direction",
                "size",
                "level",
                "stopLevel",
                "limitLevel",
                "profit",
            ]
            available_cols = [c for c in cols_to_show if c in positions.columns]

            df_view = positions[available_cols].copy()
            print(df_view.to_string(index=False))
            print(f"{'=' * 90}\n")

        elif isinstance(positions, dict):
            # Handle dictionary response (raw API response)
            pos_list = positions.get("positions", [])
            if not pos_list:
                print("\nNo open positions found.")
                return

            print(f"\n{'=' * 80}")
            print(f"{'OPEN POSITIONS':^80}")
            print(f"{'=' * 80}")

            for item in pos_list:
                market = item.get("market", {})
                pos = item.get("position", {})

                print(f"Epic:      {market.get('epic')}")
                print(f"Deal ID:   {pos.get('dealId')}")
                print(f"Direction: {pos.get('direction')}")
                print(f"Size:      {pos.get('size')}")
                print(f"Level:     {pos.get('level')}")
                print(
                    f"PnL:       {market.get('profitCurrency')} {market.get('netChange')}"
                )
                print("-" * 40)
            print(f"{'=' * 80}\n")
        else:
            print(f"Unexpected response format: {type(positions)}")
            print(positions)

    except Exception as e:
        logger.error(f"Failed to list positions: {e}")


def run_list_active_trades():
    """
    Fetches and prints trades that are currently active (PENDING or PLACED) from the DB.
    """
    logger.info("Fetching active trades from Database...")
    trades = fetch_active_trades()

    if not trades:
        print("No active trades found in Database.")
        return

    print(f"\n{'=' * 100}")
    print(f"{'ACTIVE TRADES (Bot Perspective)':^100}")
    print(f"{'=' * 100}")
    print(
        f"{'Time':<20} | {'Status':<15} | {'Market':<20} | {'Action':<5} | {'Entry':<10} | {'Plan Type':<15}"
    )
    print("-" * 100)

    for t in trades:
        ts = (
            t["timestamp"].split("T")[1].split(".")[0]
            if "T" in t["timestamp"]
            else t["timestamp"]
        )
        status = t["outcome"]
        market = t["epic"]
        action = t["action"]
        entry = str(t["entry"])
        plan_type = t.get("entry_type", "N/A")

        # Color coding if supported
        status_str = status
        if sys.stdout.isatty():
            if status == "PENDING":
                status_str = f"\033[93m{status}\033[0m"  # Yellow
            elif "PLACED" in status:
                status_str = f"\033[92m{status}\033[0m"  # Green

        print(
            f"{ts:<20} | {status_str:<15} | {market:<20} | {action:<5} | {entry:<10} | {plan_type:<15}"
        )
    print(f"{'=' * 100}\n")


def run_post_mortem(deal_id: str, model_name: str = "gemini-3-flash-preview"):
    """
    Runs a post-mortem analysis for a specific deal ID.
    """
    logger.info(f"Starting Post-Mortem for Deal ID: {deal_id} using {model_name}")

    # 1. Fetch Data
    trade_data = fetch_trade_data(deal_id)
    if not trade_data:
        logger.error(f"No data found for deal ID: {deal_id}")
        return

    # 2. Fetch Historical Price Context
    price_history_df = None
    try:
        log_entry = trade_data.get("log", {})
        epic = log_entry.get("epic")
        entry_time_str = log_entry.get("timestamp")

        if epic and entry_time_str:
            entry_time = datetime.fromisoformat(entry_time_str)
            start_time = (entry_time - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            client = IGClient()
            logger.info(
                f"Fetching historical data for {epic} from {start_time} to {end_time}..."
            )
            price_history_df = client.fetch_historical_data_by_range(
                epic=epic, resolution="1Min", start_date=start_time, end_date=end_time
            )

            if price_history_df is not None and not price_history_df.empty:
                logger.info("Saving fetched historical data to database...")
                save_candles_batch(epic, price_history_df)
    except Exception as e:
        logger.warning(f"Failed to fetch historical price context for post-mortem: {e}")

    # 3. Analyze with Gemini
    analyst = GeminiAnalyst(model_name=model_name)
    report = analyst.generate_post_mortem(trade_data, price_history_df=price_history_df)

    # 4. Save and Print
    save_post_mortem(deal_id, report)

    print("\n" + "=" * 40)
    print(f"POST-MORTEM ANALYSIS: {deal_id}")
    print("=" * 40)
    print(report)
    print("=" * 40 + "\n")


def run_delete_trade(identifier: str):
    """
    Deletes a trade log from the database.
    Supports "DB:<ID>" for primary keys or straight Deal IDs.
    """
    is_db_id = False
    clean_id = identifier

    if identifier.startswith("DB:"):
        is_db_id = True
        clean_id = identifier.replace("DB:", "")
        logger.info(f"Deleting trade log with DB ID: {clean_id}")
    else:
        logger.info(f"Deleting trade log with Deal ID: {identifier}")

    success = delete_trade_log(clean_id, is_db_id=is_db_id)
    if success:
        print(f"Successfully deleted trade: {identifier}")
    else:
        print(f"Failed to delete trade: {identifier} (Not found or error)")


def run_recent_trades(limit: int):
    """
    Fetches and prints recent trade logs.
    """
    logger.info(f"Fetching {limit} most recent trades...")
    trades = fetch_recent_trades(limit)

    if not trades:
        print("No recent trades found.")
        return

    print(f"\n{'=' * 80}")
    title = f"RECENT TRADES (Last {limit})"
    print(f"{title:^80}")
    print(f"{'=' * 80}")

    for trade in trades:
        # Parse timestamps to calculate duration
        entry_time_str = trade.get("timestamp")
        exit_time_str = trade.get("exit_time")
        duration_str = "Active"

        if entry_time_str and exit_time_str:
            try:
                start = datetime.fromisoformat(entry_time_str)
                end = datetime.fromisoformat(exit_time_str)
                duration = end - start
                duration_str = str(duration).split(".")[0]  # Remove microseconds
            except ValueError:
                duration_str = "Error"

        pnl = trade.get("pnl")
        pnl_str = f"£{pnl:.2f}" if pnl is not None else "N/A"

        # Determine Plan Type
        is_uncapped = trade.get("use_trailing_stop") or (
            trade.get("take_profit") is None
        )
        plan_type = (
            "Uncapped Trailing"
            if is_uncapped
            else f"Target: {trade.get('take_profit')}"
        )

        # Simple color for PnL (Green/Red/Reset)
        color = ""
        reset = ""
        if sys.stdout.isatty():
            if pnl is not None and pnl > 0:
                color = "\033[92m"  # Green
            elif pnl is not None and pnl < 0:
                color = "\033[91m"  # Red
            reset = "\033[0m"

        deal_display = trade["deal_id"] if trade.get("deal_id") else f"DB:{trade['id']}"
        print(f"ID:         {deal_display}")
        print(f"Time:       {entry_time_str} -> {exit_time_str or 'Active'}")
        print(f"Epic:       {trade['epic']} ({trade['action']})")
        print(f"Entry Type: {trade['entry_type']}")
        print(f"TP:         {plan_type}")
        print(
            f"Entry:      {trade['entry']} | Exit: {trade.get('exit_price') or 'N/A'}"
        )
        print(f"Result:     {color}{pnl_str}{reset} ({trade['outcome']})")
        print(f"Duration:   {duration_str}")
        print(f"Reasoning:  {trade['reasoning'][:100]}...")
        print(f"{'-' * 80}")
    print("\n")


def run_monitor_trade(deal_id: str):
    """
    Starts the 'Monitor & Manage' process for a specific active deal.
    """
    logger.info(f"--- STARTING MONITORING for Deal ID: {deal_id} ---")
    client = IGClient()

    # 1. Fetch Open Position
    position = client.fetch_open_position_by_deal_id(deal_id)
    if not position:
        logger.error(
            f"Could not find open position for Deal ID: {deal_id}. It might be closed."
        )
        return

    # Extract details (assuming flat dict from IGClient)
    epic = position.get("epic")
    direction = position.get("direction")
    entry_price = position.get("level")
    if entry_price is not None:
        entry_price = float(entry_price)

    stop_level = position.get("stopLevel")
    if stop_level is not None:
        stop_level = float(stop_level)
    else:
        logger.warning(
            f"Deal {deal_id} has no Stop Level. Monitoring might be limited."
        )
        stop_level = 0.0

    size = float(position.get("size", 0))
    limit_level = position.get("limitLevel")
    take_profit = float(limit_level) if limit_level is not None else None

    # Sync with DB (Ensure record exists and is up to date)
    sync_active_trade(
        deal_id=deal_id,
        epic=epic,
        direction=direction,
        size=size,
        entry=entry_price,
        stop_loss=stop_level,
        take_profit=take_profit,
    )

    logger.info(
        f"Found Position: {epic} ({direction}) @ {entry_price}, Stop: {stop_level}"
    )

    # 2. Calculate ATR for dynamic trailing
    atr = None
    try:
        # Fetch 15-minute data for ATR calculation
        logger.info(f"Calculating ATR for {epic}...")
        df_15m = client.fetch_historical_data(epic, "15Min", 50)
        if not df_15m.empty and len(df_15m) >= 14:
            df_15m["ATR"] = ta.atr(
                df_15m["high"], df_15m["low"], df_15m["close"], length=14
            )
            if "ATR" in df_15m.columns and not df_15m["ATR"].isnull().all():
                atr = float(df_15m["ATR"].iloc[-1])
                logger.info(f"Calculated ATR (14, 15m): {atr:.2f}")
    except Exception as e:
        logger.warning(f"Could not calculate ATR: {e}")

    # 3. Setup Stream & Monitor
    stream_manager = StreamManager(client)

    try:
        # Start stream (ensure connection and subscription to epic)
        # We pass a no-op callback because TradeMonitorDB handles trade updates separately
        # and doesn't strictly rely on price ticks in its loop.
        logger.info(f"Connecting to stream for {epic}...")
        stream_manager.connect_and_subscribe(epic, lambda x: None)

        monitor = TradeMonitorDB(client, stream_manager)

        # Start blocking monitor loop
        monitor.monitor_trade(
            deal_id=deal_id,
            epic=epic,
            entry_price=entry_price,
            stop_loss=stop_level,
            atr=atr,
            use_trailing_stop=True,
        )
    except KeyboardInterrupt:
        logger.info("Monitoring interrupted by user.")
    except Exception as e:
        logger.error(f"Monitoring failed: {e}")
    finally:
        stream_manager.stop()


def run_sync_trade(deal_id: str):
    """
    Manually syncs a trade's outcome from IG History to the DB.
    """
    logger.info(f"Syncing trade {deal_id} from IG API...")

    # 1. Fetch trade from DB to get context
    trade_data = fetch_trade_data(deal_id)
    if not trade_data:
        logger.error(f"Trade {deal_id} not found in database.")
        return

    log = trade_data["log"]
    epic = log["epic"]
    logger.info(f"Looking for closure of {epic} (Deal {deal_id})...")

    try:
        client = IGClient()
        client.authenticate()

        # 2. Fetch Transaction History
        # We fetch a decent history length (e.g. last few days) to find the close
        # Use max_span_seconds (e.g. 48h = 172800s)
        history = client.service.fetch_transaction_history(
            trans_type="ALL_DEAL", max_span_seconds=172800
        )

        if history is None or history.empty:
            logger.error("No transaction history returned from IG.")
            return

        # 3. Find the closing transaction
        # We look for a transaction that:
        # - Matches the instrument/epic (approximately)
        # - Has a non-zero PnL (or is a closure)
        # - Ideally references the deal_id (but IG history sometimes uses a new deal ID for the close)
        # - Is AFTER the entry timestamp

        # Filter by Epic (instrumentName might differ, check 'epic' col if available)
        # 'instrumentName' often contains the name, not the epic.
        # But 'openDate' might help.

        target_row = None

        # Sort by date descending (newest first)
        # Use dateUtc if available for precision, else date
        date_col = "dateUtc" if "dateUtc" in history.columns else "date"
        history[date_col] = pd.to_datetime(history[date_col])
        history = history.sort_values(date_col, ascending=False)

        for index, row in history.iterrows():
            # Check if this transaction relates to our deal
            # IG History Columns: date, instrumentName, period, direction, size, level, openLevel,
            # closeLevel, profitAndLoss, transactionType, reference, openDate, dealId

            # The 'reference' column in history often points to the Deal Reference of the CLOSING trade.
            # The 'dealId' column is the ID of this transaction.

            # We try to match by Epic/Instrument and Timing, or if we are lucky, some ID linkage.
            # Since strict ID matching is hard with just 'deal_id' (opening), we look for:
            # - Same Instrument
            # - Closing Action (Opposite direction of opening? Or just 'profitAndLoss' presence)
            # - Time > Entry Time

            # Check if profitAndLoss is populated
            pnl_str = str(row.get("profitAndLoss", ""))
            if not pnl_str or pnl_str == "nan":
                continue

            # Check if it looks like the right instrument
            # (Simple heuristic: matching name or epic if available)
            # Note: IG REST history doesn't always have 'epic'. It has 'instrumentName'.
            # We assume the most recent closing transaction for this instrument is likely it if we trade sequentially.

            # Clean PnL
            try:
                pnl_val = float(
                    pnl_str.replace("£", "").replace("A$", "").replace(",", "")
                )
            except ValueError:
                continue

            # If we found a valid PnL row, let's see if it's the one.
            # For now, let's log potential candidates and pick the first one that matches the epic/instrument loosely.
            logger.info(
                f"Candidate: {row['date']} | {row['instrumentName']} | PnL: {pnl_val}"
            )

            # Confirm Update
            # In a CLI tool, we might want to ask confirmation, but here we just take the first
            # plausible match if it's recent?
            # Or better: check if 'openDate' matches our trade's open date?
            # row['openDate'] usually format: "YYYY/MM/DD" or similar.

            # Map common Epic codes to Instrument Names found in History
            epic_to_name_map = {
                "SPTRD": "US 500",
                "FTSE": "FTSE 100",
                "DAX": "Germany 40",
                "ASX": "Australia 200",
                "NASDAQ": "US Tech 100",
                "NIKKEI": "Japan 225",
            }

            matched = False
            # Check direct epic match (rare in history)
            if epic in row.get("epic", ""):
                matched = True

            # Check using the mapping
            if not matched:
                core_code = epic.split(".")[2] if len(epic.split(".")) > 2 else ""
                expected_name = epic_to_name_map.get(core_code, core_code)
                if expected_name in row["instrumentName"]:
                    matched = True

            if matched:
                target_row = row
                break

        if target_row is not None:
            pnl_str = str(target_row.get("profitAndLoss", ""))
            pnl_val = float(pnl_str.replace("£", "").replace("A$", "").replace(",", ""))

            close_level = target_row.get("closeLevel") or target_row.get("level")
            close_time = target_row[date_col].isoformat()
            outcome = "WIN" if pnl_val > 0 else "LOSS"

            logger.info(
                f"Found Match! Updating Trade {deal_id}: PnL={pnl_val}, Exit={close_level}, Time={close_time}"
            )

            update_trade_outcome(
                deal_id=deal_id,
                exit_price=float(close_level),
                pnl=pnl_val,
                exit_time=close_time,
                outcome=outcome,
            )
            logger.info("Database updated successfully.")

        else:
            logger.warning(
                "No matching closing transaction found in recent history (2 days)."
            )

    except Exception as e:
        logger.error(f"Sync failed: {e}")


def run_strategy(
    epic: str,
    strategy_name: str,
    news_query: str = None,
    dry_run: bool = False,
    verbose: bool = False,
    timeout_seconds: int = 5400,
    max_spread: float = 2.0,
    ignore_holidays: bool = False,
    risk_scale: float = 1.0,
    min_size: float = 0.01,
    model_name: str = "gemini-3-flash-preview",
    live_data: bool = False,
):
    """
    Generic driver for a trading strategy on a specific epic.
    """
    logger.info(
        f"--- STARTING {strategy_name} STRATEGY for {epic} (Model: {model_name}, Dry Run: {dry_run}, Live Data: {live_data}, Timeout: {timeout_seconds}s) ---"
    )

    engine = StrategyEngine(
        epic,
        strategy_name=strategy_name,
        news_query=news_query,
        dry_run=dry_run,
        verbose=verbose,
        max_spread=max_spread,
        ignore_holidays=ignore_holidays,
        risk_scale=risk_scale,
        min_size=min_size,
        model_name=model_name,
        live_data=live_data,
    )

    # 1. Generate Plan
    engine.generate_plan()

    # 2. Execute if plan exists
    if engine.active_plan:
        engine.execute_strategy(
            timeout_seconds=timeout_seconds, collection_seconds=14400
        )
    else:
        logger.info("No actionable plan generated. Execution finished.")

    logger.info(f"--- {strategy_name} STRATEGY COMPLETED ---")


def run_london_strategy(
    dry_run: bool = False,
    ignore_holidays: bool = False,
    model_name: str = "gemini-3-flash-preview",
    live_data: bool = False,
):
    config = MARKET_CONFIGS["london"]
    run_strategy(
        config["epic"],
        config["strategy_name"],
        news_query=config["news_query"],
        dry_run=dry_run,
        timeout_seconds=config["timeout_seconds"],
        max_spread=config["max_spread"],
        ignore_holidays=ignore_holidays,
        risk_scale=config.get("risk_scale", 1.0),
        min_size=config.get("min_size", 0.01),
        model_name=model_name,
        live_data=live_data,
    )


def run_ny_strategy(
    dry_run: bool = False,
    ignore_holidays: bool = False,
    model_name: str = "gemini-3-flash-preview",
    live_data: bool = False,
):
    config = MARKET_CONFIGS["ny"]
    run_strategy(
        config["epic"],
        config["strategy_name"],
        news_query=config["news_query"],
        dry_run=dry_run,
        timeout_seconds=config["timeout_seconds"],
        max_spread=config["max_spread"],
        ignore_holidays=ignore_holidays,
        risk_scale=config.get("risk_scale", 1.0),
        min_size=config.get("min_size", 0.01),
        model_name=model_name,
        live_data=live_data,
    )


def run_nikkei_strategy(
    dry_run: bool = False,
    ignore_holidays: bool = False,
    model_name: str = "gemini-3-flash-preview",
    live_data: bool = False,
):
    config = MARKET_CONFIGS["nikkei"]
    run_strategy(
        config["epic"],
        config["strategy_name"],
        news_query=config["news_query"],
        dry_run=dry_run,
        timeout_seconds=config["timeout_seconds"],
        max_spread=config["max_spread"],
        ignore_holidays=ignore_holidays,
        risk_scale=config.get("risk_scale", 1.0),
        min_size=config.get("min_size", 0.01),
        model_name=model_name,
        live_data=live_data,
    )


def run_germany_strategy(
    dry_run: bool = False,
    ignore_holidays: bool = False,
    model_name: str = "gemini-3-flash-preview",
    live_data: bool = False,
):
    config = MARKET_CONFIGS["germany"]
    run_strategy(
        config["epic"],
        config["strategy_name"],
        news_query=config["news_query"],
        dry_run=dry_run,
        timeout_seconds=config["timeout_seconds"],
        max_spread=config["max_spread"],
        ignore_holidays=ignore_holidays,
        risk_scale=config.get("risk_scale", 1.0),
        min_size=config.get("min_size", 0.01),
        model_name=model_name,
        live_data=live_data,
    )


def run_australia_strategy(
    dry_run: bool = False,
    ignore_holidays: bool = False,
    model_name: str = "gemini-3-flash-preview",
    live_data: bool = False,
):
    config = MARKET_CONFIGS["australia"]
    run_strategy(
        config["epic"],
        config["strategy_name"],
        news_query=config["news_query"],
        dry_run=dry_run,
        timeout_seconds=config["timeout_seconds"],
        max_spread=config["max_spread"],
        ignore_holidays=ignore_holidays,
        risk_scale=config.get("risk_scale", 1.0),
        min_size=config.get("min_size", 0.01),
        model_name=model_name,
        live_data=live_data,
    )


def run_us_tech_strategy(
    dry_run: bool = False,
    ignore_holidays: bool = False,
    model_name: str = "gemini-3-flash-preview",
    live_data: bool = False,
):
    config = MARKET_CONFIGS["us_tech"]
    run_strategy(
        config["epic"],
        config["strategy_name"],
        news_query=config["news_query"],
        dry_run=dry_run,
        timeout_seconds=config["timeout_seconds"],
        max_spread=config["max_spread"],
        ignore_holidays=ignore_holidays,
        risk_scale=config.get("risk_scale", 1.0),
        min_size=config.get("min_size", 0.01),
        model_name=model_name,
        live_data=live_data,
    )


def graceful_shutdown(signum, frame):
    logger.info("Shutdown signal received. Exiting...")
    sys.exit(0)


def update_heartbeat():
    """
    Updates the heartbeat file to indicate the system is alive.
    """
    try:
        with open("data/heartbeat.txt", "w") as f:
            f.write(datetime.now().isoformat())
    except Exception as e:
        logger.error(f"Failed to update heartbeat: {e}")


def main():
    parser = argparse.ArgumentParser(description="AI Market Open Trader")
    parser.add_argument(
        "--analyst",
        action="store_true",
        help="Generate and print the trading plan for a specific market without executing (requires --market).",
    )
    parser.add_argument(
        "--now", action="store_true", help="Run the strategy immediately and exit"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Execute strategy without placing actual orders (used with --now)",
    )
    parser.add_argument(
        "--holiday-season-override",
        action="store_true",
        help="Force execution even during holiday season (Dec 20 - Jan 4) or public holidays.",
    )
    parser.add_argument(
        "--news-only",
        action="store_true",
        help="Only fetch and print news for the selected market/query then exit",
    )
    parser.add_argument(
        "--news-check",
        action="store_true",
        help="Run a health check on news fetching for all configured markets.",
    )
    parser.add_argument(
        "--with-rating",
        action="store_true",
        help="When using --news-check, ask Gemini to rate the relevance/quality of the news (consumes API tokens).",
    )
    parser.add_argument(
        "--post-mortem",
        type=str,
        help="Run post-mortem analysis on a specific deal ID.",
    )
    parser.add_argument(
        "--monitor-trade",
        type=str,
        help="Start 'Monitor & Manage' process for a specific active Deal ID.",
    )
    parser.add_argument(
        "--list-open",
        action="store_true",
        help="List all currently open positions and their Deal IDs (from IG).",
    )
    parser.add_argument(
        "--list-active",
        action="store_true",
        help="List active bot strategies (PENDING triggers and PLACED trades) from DB.",
    )
    parser.add_argument(
        "--recent-trades",
        type=int,
        nargs="?",
        const=5,
        help="Print N recent trades. Defaults to 5 if no number provided.",
    )
    parser.add_argument(
        "--scorecard",
        action="store_true",
        help="Generate a comprehensive performance scorecard from the database.",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        help="Optional start date for --scorecard (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--volatility-check",
        action="store_true",
        help="Check current market volatility (Range/ATR) for the selected market/epic.",
    )
    parser.add_argument(
        "--test-trade",
        action="store_true",
        help="Execute an immediate test trade (BUY/SELL) with minimal size/stops. Requires --epic or --market.",
    )
    parser.add_argument(
        "--test-trade-action",
        type=str,
        choices=["BUY", "SELL"],
        default="BUY",
        help="Action for --test-trade: 'BUY' or 'SELL'. Defaults to BUY.",
    )
    parser.add_argument(
        "--check-missed",
        action="store_true",
        help="Check if a 'Power Law' opportunity was missed today (requires --market).",
    )
    parser.add_argument(
        "--weekly-powerlaw-events",
        action="store_true",
        help="Scan the current week (Mon-Today) for Power Law events across all markets.",
    )
    parser.add_argument(
        "--force-api-fetch",
        action="store_true",
        help="When using --check-missed or --weekly-powerlaw-events, force fetching data from IG API if local data is missing.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Force live API data fetch during dry-runs (disables caching).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Print live prices to console"
    )
    parser.add_argument(
        "--source",
        type=str,
        choices=["google", "yahoo"],
        help="Specific news source to use (google or yahoo).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gemini-3-flash-preview",
        help="Gemini model name to use (e.g., gemini-1.5-flash).",
    )

    market_group = parser.add_mutually_exclusive_group()
    market_group.add_argument(
        "--epic",
        type=str,
        help="The epic to trade (used with --now). Cannot be used with --market.",
    )
    market_group.add_argument(
        "--market",
        type=str,
        choices=list(MARKET_CONFIGS.keys()),
        help="Run predefined strategy for specific market open (used with --now). Cannot be used with --epic.",
    )
    parser.add_argument(
        "--news-query",
        type=str,
        default=None,
        help="News search terms (used with --now)",
    )

    parser.add_argument(
        "--sync-trade",
        type=str,
        help="Manually sync a trade's outcome (PnL, Exit Price) from IG History to DB. Usage: --sync-trade <deal_id>",
    )
    parser.add_argument(
        "--delete-trade",
        type=str,
        help="Delete a trade log entry by Deal ID or DB ID (e.g., DB:123).",
    )
    parser.add_argument(
        "--test-alert",
        action="store_true",
        help="Send a test HIGH PRIORITY alert to Home Assistant and exit.",
    )

    args = parser.parse_args()

    # Register signal handlers
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    # Log Startup Info
    version = get_version_info()
    logger.info(f"Trader Starting Up. Version: {version}")

    if args.test_alert:
        logger.info("Sending test alert to Home Assistant...")
        notifier.send_notification(
            title="TRADER: Test Alert",
            message="This is a test of the High Priority notification system. If you see this, it works!",
            priority="high",
        )
        print("Alert sent. Check your device.")
        return

    if args.weekly_powerlaw_events:
        run_weekly_powerlaw_check(force_api_fetch=args.force_api_fetch)
        return

    if args.check_missed:
        if args.market:
            run_opportunity_check(args.market, force_api_fetch=args.force_api_fetch)
        else:
            logger.error(
                "Please specify a market with --market when using --check-missed."
            )
        return

    if args.sync_trade:
        run_sync_trade(args.sync_trade)
        return

    if args.delete_trade:
        run_delete_trade(args.delete_trade)
        return

    if args.test_trade:
        epic_to_trade = None
        if args.market:
            epic_to_trade = MARKET_CONFIGS[args.market]["epic"]
        elif args.epic:
            epic_to_trade = args.epic

        if epic_to_trade:
            run_test_trade(
                epic_to_trade, dry_run=args.dry_run, trade_action=args.test_trade_action
            )
        else:
            logger.error(
                "When --test-trade is used, either --epic or --market must be specified."
            )
        return

    if args.volatility_check:
        if args.market:
            run_volatility_check(MARKET_CONFIGS[args.market]["epic"])
        elif args.epic:
            run_volatility_check(args.epic)
        else:
            logger.error(
                "When --volatility-check is used, either --epic or --market must be specified."
            )
        return

    if args.post_mortem:
        run_post_mortem(args.post_mortem, model_name=args.model)
        return

    if args.monitor_trade:
        run_monitor_trade(args.monitor_trade)
        return

    if args.list_open:
        run_list_open_positions()
        return

    if args.list_active:
        run_list_active_trades()
        return

    if args.scorecard:
        if args.start_date:
            # Append time for full day coverage
            start_iso = f"{args.start_date}T00:00:00"
            end_iso = datetime.now().isoformat()
            logger.info(f"Generating scorecard from {start_iso}...")
            trades = fetch_trades_in_range(start_iso, end_iso)
            generate_scorecard(trades=trades)
        else:
            generate_scorecard()
        return

    if args.recent_trades is not None:
        run_recent_trades(args.recent_trades)
        return

    if args.news_only:
        fetcher = NewsFetcher()
        query = None
        market_key = None

        if args.news_query:
            query = args.news_query
        elif args.market:
            query = MARKET_CONFIGS[args.market]["news_query"]
            market_key = args.market
        elif args.epic:
            query = args.epic

        if query:
            print(fetcher.fetch_news(query, source=args.source, market=market_key))
        else:
            logger.error("No query provided. Use --market, --news-query, or --epic.")
        return

    if args.news_check:
        logger.info("Running News Health Check...")
        fetcher = NewsFetcher()
        analyst = GeminiAnalyst(model_name=args.model) if args.with_rating else None

        print(f"\n{'=' * 80}")
        print(f"{'NEWS HEALTH CHECK':^80}")
        if args.with_rating:
            print(f"{'(with AI Quality Audit)':^80}")
        print(f"{'=' * 80}")

        passed = 0
        failed = 0

        # Filter markets if --market is provided
        target_markets = MARKET_CONFIGS.items()
        if args.market:
            if args.market in MARKET_CONFIGS:
                target_markets = [(args.market, MARKET_CONFIGS[args.market])]
            else:
                logger.error(f"Unknown market: {args.market}")
                return

        for market, config in target_markets:
            query = config["news_query"]
            print(f"\nChecking [{market.upper()}] Query: '{query}'...")
            try:
                # Increase limit to 10 for deep audit (Strategy uses 5 by default)
                result = fetcher.fetch_news(
                    query, limit=10, source=args.source, market=market
                )
                if "No recent news found" in result:
                    print("  [WARN] No news returned.")
                    failed += 1
                else:
                    # Extract first headline for verification
                    lines = result.split("\n")
                    first_headline = next(
                        (line for line in lines if line.startswith("1. ")),
                        "No headline found",
                    )
                    print(f"  [PASS] {len(lines) - 2} items retrieved.")
                    print(f"  Sample: {first_headline[:70]}...")

                    if analyst:
                        print("  Running AI Audit...", end="", flush=True)
                        quality = analyst.assess_news_quality(result, market)
                        if quality:
                            print(
                                f"\r  [AI RATING] Score: {quality.score}/10 | Clarity: {quality.sentiment_clarity}"
                            )
                            print(f"  Reasoning: {quality.reasoning}")
                            if quality.score < 5:
                                print("  [WARN] Low quality news detected.")
                        else:
                            print("\r  [AI ERROR] Could not rate news.")

                    passed += 1
            except Exception as e:
                print(f"  [FAIL] Exception: {e}")
                failed += 1

        print(f"\nSummary: {passed} Passed, {failed} Failed.")
        return

    if args.analyst:
        if not args.market:
            logger.error("--analyst requires --market to be specified.")
            return

        logger.info(f"Running Analyst Mode for {args.market.upper()}...")
        config = MARKET_CONFIGS[args.market]

        # Initialize Engine
        engine = StrategyEngine(
            config["epic"],
            strategy_name=config["strategy_name"],
            news_query=config["news_query"],
            dry_run=True,  # Safety: Force dry run for analyst mode
            verbose=args.verbose,
            max_spread=config["max_spread"],
            ignore_holidays=args.holiday_season_override,
            risk_scale=config.get("risk_scale", 1.0),
            min_size=config.get("min_size", 0.01),
            model_name=args.model,
            live_data=args.live,
        )

        # Generate Plan
        engine.generate_plan()

        # Print Result
        print(f"\n{'=' * 60}")
        print(f"ANALYST REPORT: {config['strategy_name']}")
        print(f"{'=' * 60}")

        if engine.active_plan:
            plan = engine.active_plan
            print(f"Action:      {plan.action}")
            print(f"Entry:       {plan.entry} ({plan.entry_type})")
            print(f"Stop Loss:   {plan.stop_loss}")
            print(f"Take Profit: {plan.take_profit}")
            print(f"Size:        {plan.size}")
            print(f"Validity:    {getattr(plan, 'validity_time_minutes', 'N/A')} min")
            print(f"Confidence:  {plan.confidence}")
            print(f"{'-' * 60}")
            print(f"Reasoning:\n{plan.reasoning}")
        else:
            print("No plan generated. (See logs for details/errors)")

        print(f"{'=' * 60}\n")
        return

    if args.now:
        logger.info("Executing ON-DEMAND strategy...")
        if args.market:
            market_config = MARKET_CONFIGS[args.market]
            news_q = args.news_query if args.news_query else market_config["news_query"]
            run_strategy(
                market_config["epic"],
                market_config["strategy_name"],
                news_query=news_q,
                dry_run=args.dry_run,
                verbose=args.verbose,
                timeout_seconds=market_config["timeout_seconds"],
                max_spread=market_config["max_spread"],
                ignore_holidays=args.holiday_season_override,
                model_name=args.model,
                live_data=args.live,
            )
        elif args.epic:
            # Default timeout for custom epic if not specified
            run_strategy(
                args.epic,
                "ON-DEMAND",
                news_query=args.news_query,
                dry_run=args.dry_run,
                verbose=args.verbose,
                timeout_seconds=5400,
                max_spread=2.0,
                ignore_holidays=args.holiday_season_override,
                risk_scale=1.0,
                model_name=args.model,
                live_data=args.live,
            )
        else:
            logger.error(
                "When --now is used, either --epic or --market must be specified."
            )
        return

    logger.info("Initializing AI Market Open Trader (Scheduler Mode)....")

    scheduler = BlockingScheduler()

    # Heartbeat job
    scheduler.add_job(update_heartbeat, "interval", minutes=1)

    # Schedule jobs based on MARKET_CONFIGS
    for market_key, config in MARKET_CONFIGS.items():
        scheduler.add_job(
            globals()[f"run_{market_key}_strategy"],
            "cron",
            kwargs={
                "dry_run": args.dry_run,
                "ignore_holidays": args.holiday_season_override,
                "model_name": args.model,
                "live_data": args.live,
            },  # Pass flags as keyword arguments
            **config["schedule"],
        )

    logger.info("Scheduler started. Waiting for market opens...")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
