import logging
import time
from typing import Optional
from config import IS_LIVE
from src.ig_client import IGClient
from src.gemini_analyst import GeminiAnalyst, TradingSignal, Action
from src.stream_manager import StreamManager

logger = logging.getLogger(__name__)

class StrategyEngine:
    def __init__(self, epic: str):
        """
        Orchestrates the trading workflow for a single instrument.
        
        Args:
            epic (str): The IG epic to trade (e.g., 'CS.D.FTSE.TODAY.IP').
        """
        self.epic = epic
        self.client = IGClient()
        self.analyst = GeminiAnalyst()
        self.stream_manager = StreamManager(self.client.service)
        
        self.active_plan: Optional[TradingSignal] = None
        self.position_open = False
        
    def generate_plan(self):
        """
        Step 1: Fetches data, asks Gemini, and stores the trading plan.
        """
        logger.info(f"Generating plan for {self.epic}...")
        
        try:
            # 1. Fetch Historical Data (4 hours of 15-min candles)
            df = self.client.fetch_historical_data(self.epic, "M15", 16)
            
            if df.empty:
                logger.error("No data received from IG.")
                return

            # 2. Format Data for Gemini
            # Convert dataframe to a readable string representation
            market_context = f"Instrument: {self.epic}\n"
            market_context += "Recent OHLC Data (Last 4 Hours, 15m intervals):\n"
            market_context += df.tail(16).to_string()
            
            # 3. Get Analysis
            signal = self.analyst.analyze_market(market_context)
            
            if signal and signal.action != Action.WAIT:
                self.active_plan = signal
                logger.info(f"PLAN GENERATED: {signal.action} at {signal.entry} (Stop: {signal.stop_loss})")
            else:
                logger.info("Plan is WAIT or generation failed.")
                self.active_plan = None
                
        except Exception as e:
            logger.error(f"Error generating plan: {e}")

    def execute_strategy(self):
        """
        Step 2: Starts the stream and monitors for triggers.
        Blocking call - runs until a trade is executed or manually stopped.
        """
        if not self.active_plan:
            logger.warning("No active plan to execute.")
            return

        logger.info("Starting execution monitor...")
        
        # Connect to stream
        try:
            self.client.authenticate() # Ensure REST session is active
            self.stream_manager.connect()
            self.stream_manager.start_tick_subscription(self.epic, self._on_tick)
            
            # Keep main thread alive while stream runs in background thread
            # In a real app, this might be managed by the Scheduler
            while not self.position_open:
                time.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("Execution stopped by user.")
        finally:
            self.stream_manager.stop()

    def _on_tick(self, data: dict):
        """
        Callback for live price updates. Checks triggers.
        """
        if self.position_open or not self.active_plan:
            return

        bid = float(data.get('bid') or 0)
        offer = float(data.get('offer') or 0)
        
        if bid == 0 or offer == 0:
            return

        # Simple Trigger Logic
        # BUY: If Offer Price > Entry Level
        # SELL: If Bid Price < Entry Level
        
        plan = self.active_plan
        triggered = False
        
        if plan.action == Action.BUY:
            if offer >= plan.entry:
                triggered = True
                logger.info(f"BUY TRIGGERED: Offer {offer} >= Entry {plan.entry}")
                
        elif plan.action == Action.SELL:
            if bid <= plan.entry:
                triggered = True
                logger.info(f"SELL TRIGGERED: Bid {bid} <= Entry {plan.entry}")
        
        if triggered:
            self._place_order(plan)

    def _place_order(self, plan: TradingSignal):
        """
        Executes the trade via IG Client.
        """
        try:
            logger.info("Placing order...")
            direction = "BUY" if plan.action == Action.BUY else "SELL"
            
            # Size is hardcoded for safety in this MVP, but should be dynamic
            size = 0.5 
            
            self.client.place_spread_bet_order(
                epic=self.epic,
                direction=direction,
                size=size,
                stop_level=plan.stop_loss,
                limit_level=plan.take_profit
            )
            
            self.position_open = True
            logger.info("Trade executed successfully.")
            
        except Exception as e:
            logger.error(f"Failed to execute trade: {e}")
