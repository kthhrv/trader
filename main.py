import time
import logging
import signal
import sys
import argparse
from apscheduler.schedulers.blocking import BlockingScheduler
from src.strategy_engine import StrategyEngine

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

def run_strategy(epic: str, strategy_name: str):
    """
    Generic driver for a trading strategy on a specific epic.
    """
    logger.info(f"--- STARTING {strategy_name} STRATEGY for {epic} ---")
    
    engine = StrategyEngine(epic)
    
    # 1. Generate Plan
    engine.generate_plan()
    
    # 2. Execute if plan exists
    if engine.active_plan:
        engine.execute_strategy()
    else:
        logger.info("No actionable plan generated. Execution finished.")
    
    logger.info(f"--- {strategy_name} STRATEGY COMPLETED ---")

def run_london_strategy():
    """
    Job to be scheduled for the London Open strategy.
    """
    # FTSE 100 Epic (CFD)
    run_strategy("IX.D.FTSE.CFD.IP", "LONDON OPEN")

def run_ny_strategy():
    """
    Job to be scheduled for the NY Open strategy.
    """
    # S&P 500 Epic (CFD)
    run_strategy("IX.D.SPX500.CFD.IP", "NY OPEN")

def graceful_shutdown(signum, frame):
    logger.info("Shutdown signal received. Exiting...")
    sys.exit(0)

def main():
    parser = argparse.ArgumentParser(description="AI Market Open Trader")
    parser.add_argument("--now", action="store_true", help="Run the strategy immediately and exit")
    parser.add_argument("--epic", type=str, default="IX.D.FTSE.CFD.IP", help="The epic to trade (used with --now)")
    
    args = parser.parse_args()

    # Register signal handlers
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    if args.now:
        logger.info(f"Executing ON-DEMAND strategy for {args.epic}...")
        run_strategy(args.epic, "ON-DEMAND")
        return

    logger.info("Initializing AI Market Open Trader (Scheduler Mode)...")
    
    scheduler = BlockingScheduler()
    
    # Schedule London Open (08:00 GMT) - Start 15 mins prior
    scheduler.add_job(
        run_london_strategy, 
        'cron', 
        day_of_week='mon-fri', 
        hour=7, 
        minute=45,
        timezone='Europe/London'
    )
    
    # Schedule NY Open (14:30 GMT) - Start 15 mins prior
    scheduler.add_job(
        run_ny_strategy, 
        'cron', 
        day_of_week='mon-fri', 
        hour=14, 
        minute=15,
        timezone='Europe/London'
    )
    
    logger.info("Scheduler started. Waiting for market opens...")
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass

if __name__ == "__main__":
    main()