import warnings
warnings.simplefilter(action='ignore', category=FutureWarning)

import time
import logging
import signal
import sys
import argparse
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
from apscheduler.schedulers.blocking import BlockingScheduler
from src.strategy_engine import StrategyEngine
from src.news_fetcher import NewsFetcher
from src.database import fetch_trade_data, save_post_mortem, fetch_recent_trades
from src.gemini_analyst import GeminiAnalyst, TradingSignal, Action, EntryType # Added imports
from src.ig_client import IGClient
from src.trade_monitor_db import TradeMonitorDB
from src.stream_manager import StreamManager

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/trader.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Define market configurations once
MARKET_CONFIGS = {
    "london": {
        "epic": "IX.D.FTSE.DAILY.IP",
        "strategy_name": "LONDON OPEN",
        "news_query": "FTSE 100 UK Economy",
        "schedule": {"day_of_week": 'mon-fri', "hour": 7, "minute": 55, "timezone": 'Europe/London'},
        "timeout_seconds": 5400, # 90 minutes
        "max_spread": 2.0
    },
    "ny": {
        "epic": "IX.D.SPTRD.DAILY.IP",
        "strategy_name": "NY OPEN",
        "news_query": "S&P 500 US Economy",
        "schedule": {"day_of_week": 'mon-fri', "hour": 9, "minute": 25, "timezone": 'America/New_York'},
        "timeout_seconds": 5400, # 90 minutes
        "max_spread": 1.6
    },
    "nikkei": {
        "epic": "IX.D.NIKKEI.DAILY.IP",
        "strategy_name": "NIKKEI OPEN",
        "news_query": "Nikkei 225 Japan Economy",
        "schedule": {"day_of_week": 'mon-fri', "hour": 8, "minute": 55, "timezone": 'Asia/Tokyo'},
        "timeout_seconds": 5400, # 90 minutes
        "max_spread": 8.0
    }
}

def run_test_trade(epic: str, dry_run: bool = False, trade_action: str = "BUY"):
    """
    Executes an immediate TEST trade with minimal size and tight stops.
    Bypasses Gemini analysis.
    """
    logger.info(f"--- STARTING TEST TRADE for {epic}, Action: {trade_action} (Dry Run: {dry_run}) ---")
    
    try:
        client = IGClient()
        market_info = client.get_market_info(epic)
        if not market_info or 'snapshot' not in market_info:
            logger.error("Could not fetch market info for test trade.")
            return

        current_offer = float(market_info['snapshot']['offer'])
        current_bid = float(market_info['snapshot']['bid'])
        
        # Determine entry, stop, and profit based on action
        action_enum = Action[trade_action] # Convert string to enum
        entry_price = 0.0
        stop_loss = 0.0
        take_profit = 0.0

        if action_enum == Action.BUY:
            entry_price = current_offer # Buy at the offer price
            stop_loss = entry_price - 10.0 # 10 points below entry
            take_profit = entry_price + 20.0 # 20 points above entry
            logger.info(f"Test BUY: Entry at {entry_price}, SL {stop_loss}, TP {take_profit}")
        elif action_enum == Action.SELL:
            entry_price = current_bid # Sell at the bid price
            stop_loss = entry_price + 10.0 # 10 points above entry
            take_profit = entry_price - 20.0 # 20 points below entry
            logger.info(f"Test SELL: Entry at {entry_price}, SL {stop_loss}, TP {take_profit}")
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
            size=0.5, # Minimum size
            atr=5.0, # Dummy ATR
            entry_type=EntryType.INSTANT,
            use_trailing_stop=True
        )
        
        engine = StrategyEngine(epic, strategy_name="TEST_TRADE", dry_run=dry_run, verbose=True, max_spread=5.0) # High max spread to ensure execution
        engine.active_plan = test_plan
        
        logger.info(f"Injecting Test Plan: {trade_action} at {entry_price}, SL {stop_loss}, TP {take_profit}")
        
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
        
        print(f"\n{'='*60}")
        print(f"VOLATILITY REPORT: {epic}")
        print(f"{'='*60}")
        
        # Analyze 1-Minute Candle
        if not df_1m.empty and len(df_1m) >= 2:
            latest_completed_1m = df_1m.iloc[-2]
            high_1m = float(latest_completed_1m['high'])
            low_1m = float(latest_completed_1m['low'])
            range_1m = round(high_1m - low_1m, 2)
            close_1m = float(latest_completed_1m['close'])
            print(f"Latest Completed 1-Min Candle ({latest_completed_1m.name}):")
            print(f"  Close: {close_1m}")
            print(f"  High:  {high_1m}")
            print(f"  Low:   {low_1m}")
            print(f"  Range: {range_1m} points")
        else:
            print("  Error: Could not fetch latest completed 1-minute candle.")

        # Analyze ATR (15-Min)
        if not df_15m.empty and len(df_15m) >= 14:
            df_15m['ATR'] = ta.atr(df_15m['high'], df_15m['low'], df_15m['close'], length=14)
            if 'ATR' in df_15m.columns and not df_15m['ATR'].isnull().all():
                latest_atr = round(float(df_15m['ATR'].iloc[-1]), 2)
                
                # Calculate relative volatility
                avg_atr = df_15m['ATR'].mean()
                vol_ratio = latest_atr / avg_atr if avg_atr > 0 else 1.0
                
                vol_state = "MEDIUM"
                if vol_ratio < 0.8:
                    vol_state = "LOW"
                elif vol_ratio > 1.2:
                    vol_state = "HIGH"
                
                print(f"\nVolatility Context (15-Min Resolution):")
                print(f"  ATR (14): {latest_atr} points")
                print(f"  Avg ATR:  {round(avg_atr, 2)} points (Last 50 periods)")
                print(f"  Level:    {vol_state} (Current vs Avg)")
            else:
                print("\n  Error: ATR calculation failed (NaN values).")
        else:
            print("\n  Error: Insufficient data for ATR calculation.")
            
        print(f"{'='*60}\n")
        
    except Exception as e:
        logger.error(f"Failed to check volatility: {e}")

def run_post_mortem(deal_id: str):
    """
    Runs a post-mortem analysis for a specific deal ID.
    """
    logger.info(f"Starting Post-Mortem for Deal ID: {deal_id}")
    
    # 1. Fetch Data
    trade_data = fetch_trade_data(deal_id)
    if not trade_data:
        logger.error(f"No data found for deal ID: {deal_id}")
        return

    # 2. Fetch Historical Price Context
    price_history_df = None
    try:
        log_entry = trade_data.get('log', {})
        epic = log_entry.get('epic')
        entry_time_str = log_entry.get('timestamp')
        
        if epic and entry_time_str:
            entry_time = datetime.fromisoformat(entry_time_str)
            start_time = (entry_time - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
            end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            client = IGClient()
            logger.info(f"Fetching historical data for {epic} from {start_time} to {end_time}...")
            price_history_df = client.fetch_historical_data_by_range(
                epic=epic,
                resolution='1Min',
                start_date=start_time,
                end_date=end_time
            )
    except Exception as e:
        logger.warning(f"Failed to fetch historical price context for post-mortem: {e}")

    # 3. Analyze with Gemini
    analyst = GeminiAnalyst()
    report = analyst.generate_post_mortem(trade_data, price_history_df=price_history_df)
    
    # 4. Save and Print
    save_post_mortem(deal_id, report)
    
    print("\n" + "="*40)
    print(f"POST-MORTEM ANALYSIS: {deal_id}")
    print("="*40)
    print(report)
    print("="*40 + "\n")

def run_recent_trades(limit: int):
    """
    Fetches and prints recent trade logs.
    """
    logger.info(f"Fetching {limit} most recent trades...")
    trades = fetch_recent_trades(limit)
    
    if not trades:
        print("No recent trades found.")
        return
    
    print(f"\n{'='*80}")
    title = f"RECENT TRADES (Last {limit})"
    print(f"{title:^80}")
    print(f"{'='*80}")
    
    for trade in trades:
        # Parse timestamps to calculate duration
        entry_time_str = trade.get('timestamp')
        exit_time_str = trade.get('exit_time')
        duration_str = "Active"
        
        if entry_time_str and exit_time_str:
            try:
                start = datetime.fromisoformat(entry_time_str)
                end = datetime.fromisoformat(exit_time_str)
                duration = end - start
                duration_str = str(duration).split('.')[0] # Remove microseconds
            except ValueError:
                duration_str = "Error"

        pnl = trade.get('pnl')
        pnl_str = f"£{pnl:.2f}" if pnl is not None else "N/A"
        
        # Determine Plan Type
        is_uncapped = trade.get('use_trailing_stop') or (trade.get('take_profit') is None)
        plan_type = "Uncapped Trailing" if is_uncapped else f"Target: {trade.get('take_profit')}"

        # Simple color for PnL (Green/Red/Reset)
        color = ""
        reset = ""
        if sys.stdout.isatty():
            if pnl is not None and pnl > 0:
                color = "\033[92m" # Green
            elif pnl is not None and pnl < 0:
                color = "\033[91m" # Red
            reset = "\033[0m"

        print(f"Deal ID:    {trade['deal_id']}")
        print(f"Time:       {entry_time_str} -> {exit_time_str or 'Active'}")
        print(f"Epic:       {trade['epic']} ({trade['action']})")
        print(f"Entry Type: {trade['entry_type']}")
        print(f"TP:         {plan_type}")
        print(f"Entry:      {trade['entry']} | Exit: {trade.get('exit_price') or 'N/A'}")
        print(f"Result:     {color}{pnl_str}{reset} ({trade['outcome']})")
        print(f"Duration:   {duration_str}")
        print(f"Reasoning:  {trade['reasoning'][:100]}...") 
        print(f"{'-'*80}")
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
        logger.error(f"Could not find open position for Deal ID: {deal_id}. It might be closed.")
        return

    # Extract details (assuming flat dict from IGClient)
    epic = position.get('epic')
    direction = position.get('direction')
    entry_price = position.get('level')
    if entry_price is not None:
        entry_price = float(entry_price)
    
    stop_level = position.get('stopLevel')
    if stop_level is not None:
        stop_level = float(stop_level)
    else:
        logger.warning(f"Deal {deal_id} has no Stop Level. Monitoring might be limited.")
        stop_level = 0.0

    logger.info(f"Found Position: {epic} ({direction}) @ {entry_price}, Stop: {stop_level}")

    # 2. Calculate ATR for dynamic trailing
    atr = None
    try:
        # Fetch 15-minute data for ATR calculation
        logger.info(f"Calculating ATR for {epic}...")
        df_15m = client.fetch_historical_data(epic, "15Min", 50)
        if not df_15m.empty and len(df_15m) >= 14:
            df_15m['ATR'] = ta.atr(df_15m['high'], df_15m['low'], df_15m['close'], length=14)
            if 'ATR' in df_15m.columns and not df_15m['ATR'].isnull().all():
                atr = float(df_15m['ATR'].iloc[-1])
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
            use_trailing_stop=True 
        )
    except KeyboardInterrupt:
        logger.info("Monitoring interrupted by user.")
    except Exception as e:
        logger.error(f"Monitoring failed: {e}")
    finally:
        stream_manager.stop()


def run_strategy(epic: str, strategy_name: str, news_query: str = None, dry_run: bool = False, verbose: bool = False, timeout_seconds: int = 5400, max_spread: float = 2.0):
    """
    Generic driver for a trading strategy on a specific epic.
    """
    logger.info(f"--- STARTING {strategy_name} STRATEGY for {epic} (Dry Run: {dry_run}, Timeout: {timeout_seconds}s, Max Spread: {max_spread}) ---")
    
    engine = StrategyEngine(epic, strategy_name=strategy_name, news_query=news_query, dry_run=dry_run, verbose=verbose, max_spread=max_spread)
    
    # 1. Generate Plan
    engine.generate_plan()
    
    # 2. Execute if plan exists
    if engine.active_plan:
        engine.execute_strategy(timeout_seconds=timeout_seconds)
    else:
        logger.info("No actionable plan generated. Execution finished.")
    
    logger.info(f"--- {strategy_name} STRATEGY COMPLETED ---")

def run_london_strategy(dry_run: bool = False):
    config = MARKET_CONFIGS["london"]
    run_strategy(config["epic"], config["strategy_name"], news_query=config["news_query"], dry_run=dry_run, timeout_seconds=config["timeout_seconds"], max_spread=config["max_spread"])

def run_ny_strategy(dry_run: bool = False):
    config = MARKET_CONFIGS["ny"]
    run_strategy(config["epic"], config["strategy_name"], news_query=config["news_query"], dry_run=dry_run, timeout_seconds=config["timeout_seconds"], max_spread=config["max_spread"])

def run_nikkei_strategy(dry_run: bool = False):
    config = MARKET_CONFIGS["nikkei"]
    run_strategy(config["epic"], config["strategy_name"], news_query=config["news_query"], dry_run=dry_run, timeout_seconds=config["timeout_seconds"], max_spread=config["max_spread"])

def graceful_shutdown(signum, frame):
    logger.info("Shutdown signal received. Exiting...")
    sys.exit(0)

def main():
    parser = argparse.ArgumentParser(description="AI Market Open Trader")
    parser.add_argument("--now", action="store_true", help="Run the strategy immediately and exit")
    parser.add_argument("--dry-run", action="store_true", help="Execute strategy without placing actual orders (used with --now)")
    parser.add_argument("--news-only", action="store_true", help="Only fetch and print news for the selected market/query then exit")
    parser.add_argument("--post-mortem", type=str, help="Run post-mortem analysis on a specific deal ID.")
    parser.add_argument("--monitor-trade", type=str, help="Start 'Monitor & Manage' process for a specific active Deal ID.")
    parser.add_argument("--recent-trades", type=int, nargs="?", const=5, help="Print N recent trades. Defaults to 5 if no number provided.")
    parser.add_argument("--volatility-check", action="store_true", help="Check current market volatility (Range/ATR) for the selected market/epic.")
    parser.add_argument("--test-trade", action="store_true", help="Execute an immediate test trade (BUY/SELL) with minimal size/stops. Requires --epic or --market.")
    parser.add_argument("--test-trade-action", type=str, choices=["BUY", "SELL"], default="BUY", help="Action for --test-trade: 'BUY' or 'SELL'. Defaults to BUY.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print live prices to console")
    
    market_group = parser.add_mutually_exclusive_group()
    market_group.add_argument("--epic", type=str, help="The epic to trade (used with --now). Cannot be used with --market.")
    market_group.add_argument("--market", type=str, choices=list(MARKET_CONFIGS.keys()), help="Run predefined strategy for specific market open (used with --now). Cannot be used with --epic.")
    parser.add_argument("--news-query", type=str, default=None, help="News search terms (used with --now)")
    
    args = parser.parse_args()

    # Register signal handlers
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    if args.test_trade:
        epic_to_trade = None
        if args.market:
            epic_to_trade = MARKET_CONFIGS[args.market]["epic"]
        elif args.epic:
            epic_to_trade = args.epic
        
        if epic_to_trade:
            run_test_trade(epic_to_trade, dry_run=args.dry_run, trade_action=args.test_trade_action)
        else:
            logger.error("When --test-trade is used, either --epic or --market must be specified.")
        return

    if args.volatility_check:
        if args.market:
            run_volatility_check(MARKET_CONFIGS[args.market]["epic"])
        elif args.epic:
            run_volatility_check(args.epic)
        else:
            logger.error("When --volatility-check is used, either --epic or --market must be specified.")
        return

    if args.post_mortem:
        run_post_mortem(args.post_mortem)
        return

    if args.monitor_trade:
        run_monitor_trade(args.monitor_trade)
        return

    if args.recent_trades is not None:
        run_recent_trades(args.recent_trades)
        return

    if args.news_only:
        fetcher = NewsFetcher()
        query = None
        
        if args.news_query:
            query = args.news_query
        elif args.market:
            query = MARKET_CONFIGS[args.market]["news_query"]
        elif args.epic:
            query = args.epic
            
        if query:
            print(fetcher.fetch_news(query))
        else:
            logger.error("No query provided. Use --market, --news-query, or --epic.")
        return

    if args.now:
        logger.info("Executing ON-DEMAND strategy...")
        if args.market:
            market_config = MARKET_CONFIGS[args.market]
            news_q = args.news_query if args.news_query else market_config["news_query"]
            run_strategy(market_config["epic"], market_config["strategy_name"], news_query=news_q, dry_run=args.dry_run, verbose=args.verbose, timeout_seconds=market_config["timeout_seconds"], max_spread=market_config["max_spread"])
        elif args.epic:
            # Default timeout for custom epic if not specified
            run_strategy(args.epic, "ON-DEMAND", news_query=args.news_query, dry_run=args.dry_run, verbose=args.verbose, timeout_seconds=5400, max_spread=2.0)
        else:
            logger.error("When --now is used, either --epic or --market must be specified.")
        return

    logger.info("Initializing AI Market Open Trader (Scheduler Mode)....")
    
    scheduler = BlockingScheduler()
    
    # Schedule jobs based on MARKET_CONFIGS
    for market_key, config in MARKET_CONFIGS.items():
        scheduler.add_job(
            globals()[f"run_{market_key}_strategy"], 
            'cron', 
            kwargs={'dry_run': args.dry_run}, # Pass dry_run as a keyword argument
            **config["schedule"]
        )
    
    logger.info("Scheduler started. Waiting for market opens...")
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass

if __name__ == "__main__":
    main()
