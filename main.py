import time
import logging
import signal
import sys
import argparse
from apscheduler.schedulers.blocking import BlockingScheduler
from src.strategy_engine import StrategyEngine
from src.news_fetcher import NewsFetcher
from src.database import fetch_trade_data, save_post_mortem, fetch_recent_trades
from src.gemini_analyst import GeminiAnalyst

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
        "schedule": {"day_of_week": 'mon-fri', "hour": 7, "minute": 45, "timezone": 'Europe/London'},
        "timeout_seconds": 5400, # 90 minutes
        "max_spread": 2.0
    },
    "ny": {
        "epic": "IX.D.SPTRD.DAILY.IP",
        "strategy_name": "NY OPEN",
        "news_query": "S&P 500 US Economy",
        "schedule": {"day_of_week": 'mon-fri', "hour": 9, "minute": 15, "timezone": 'America/New_York'},
        "timeout_seconds": 5400, # 90 minutes
        "max_spread": 1.0
    },
    "nikkei": {
        "epic": "IX.D.NIKKEI.DAILY.IP",
        "strategy_name": "NIKKEI OPEN",
        "news_query": "Nikkei 225 Japan Economy",
        "schedule": {"day_of_week": 'mon-fri', "hour": 8, "minute": 45, "timezone": 'Asia/Tokyo'},
        "timeout_seconds": 5400, # 90 minutes
        "max_spread": 8.0
    }
}

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

    # 2. Analyze with Gemini
    analyst = GeminiAnalyst()
    report = analyst.generate_post_mortem(trade_data)
    
    # 3. Save and Print
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
    
    print("\n" + "="*60)
    print(f"RECENT TRADES (Last {limit})")
    print("="*60)
    for trade in trades:
        print(f"Timestamp: {trade['timestamp']}")
        print(f"Deal ID: {trade['deal_id']}")
        print(f"Epic: {trade['epic']}")
        print(f"Action: {trade['action']} @ {trade['entry']} (SL: {trade['stop_loss']}, TP: {trade['take_profit']})")
        print(f"Outcome: {trade['outcome']} (Dry Run: {trade['is_dry_run']})")
        print(f"Reasoning: {trade['reasoning'][:70]}...") # Truncate for display
        print("-"*60)
    print("="*60 + "\n")

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
    parser.add_argument("--recent-trades", type=int, nargs="?", const=5, help="Print N recent trades. Defaults to 5 if no number provided.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print live prices to console")
    
    market_group = parser.add_mutually_exclusive_group()
    market_group.add_argument("--epic", type=str, help="The epic to trade (used with --now). Cannot be used with --market.")
    market_group.add_argument("--market", type=str, choices=list(MARKET_CONFIGS.keys()), help="Run predefined strategy for specific market open (used with --now). Cannot be used with --epic.")
    parser.add_argument("--news-query", type=str, default=None, help="News search terms (used with --now)")
    
    args = parser.parse_args()

    # Register signal handlers
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    if args.post_mortem:
        run_post_mortem(args.post_mortem)
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
