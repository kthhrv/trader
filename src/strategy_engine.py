import logging
import time
from typing import Optional
import pandas as pd
import pandas_ta as ta
from config import IS_LIVE, RISK_PER_TRADE_PERCENT
from src.ig_client import IGClient
from src.gemini_analyst import GeminiAnalyst, TradingSignal, Action
from src.news_fetcher import NewsFetcher
from src.trade_logger_db import TradeLoggerDB
from src.trade_monitor_db import TradeMonitorDB
from src.market_status import MarketStatus
from src.stream_manager import StreamManager # Import the new StreamManager

logger = logging.getLogger(__name__)

class StrategyEngine:
    def __init__(self, epic: str, strategy_name: str = "Market Open", news_query: str = None, dry_run: bool = False, verbose: bool = False, max_spread: float = 2.0,
                 ig_client: Optional[IGClient] = None,
                 analyst: Optional[GeminiAnalyst] = None,
                 news_fetcher: Optional[NewsFetcher] = None,
                 trade_logger: Optional[TradeLoggerDB] = None,
                 trade_monitor: Optional[TradeMonitorDB] = None,
                 market_status: Optional[MarketStatus] = None,
                 stream_manager: Optional[StreamManager] = None):
        """
        Orchestrates the trading workflow for a single instrument.
        Supports Dependency Injection for testing.
        """
        self.epic = epic
        self.strategy_name = strategy_name
        self.news_query = news_query
        self.dry_run = dry_run
        self.verbose = verbose
        self.max_spread = max_spread
        
        self.client = ig_client if ig_client else IGClient()
        self.analyst = analyst if analyst else GeminiAnalyst()
        self.news_fetcher = news_fetcher if news_fetcher else NewsFetcher()
        self.market_status = market_status if market_status else MarketStatus()
        self.trade_logger = trade_logger if trade_logger else TradeLoggerDB()
        self.trade_monitor = trade_monitor if trade_monitor else TradeMonitorDB(self.client)
        self.stream_manager = stream_manager if stream_manager else StreamManager(self.client)
        
        self.vix_epic = "CC.D.VIX.USS.IP"
        
        self.active_plan: Optional[TradingSignal] = None
        self.position_open = False
        self.current_bid: float = 0.0
        self.current_offer: float = 0.0
        self.last_skipped_log_time: float = 0.0 # For rate-limiting skipped logs
        
    def generate_plan(self):
        """
        Step 1: Fetches data, asks Gemini, and stores the trading plan.
        """
        # Check for holidays
        if self.market_status.is_holiday(self.epic):
            logger.warning(f"Holiday detected for {self.epic}. Strategy execution aborted.")
            return

        logger.info(f"Generating plan for {self.epic} ({self.strategy_name})...")
        
        try:
            # 1. Fetch Historical Data (Fetch 50 points to allow for indicator calculation)
            df = self.client.fetch_historical_data(self.epic, "15Min", 50)
            
            if df.empty:
                logger.error("No data received from IG.")
                return

            # 2. Calculate Technical Indicators
            # Ensure columns are numeric
            cols = ['open', 'high', 'low', 'close']
            df[cols] = df[cols].apply(pd.to_numeric, errors='coerce')
            
            # Calculate Indicators
            # ATR(14) - Volatility
            df['ATR'] = ta.atr(df['high'], df['low'], df['close'], length=14)
            # RSI(14) - Momentum
            df['RSI'] = ta.rsi(df['close'], length=14)
            # EMA(20) - Trend
            df['EMA_20'] = ta.ema(df['close'], length=20)

            # Get the latest complete candle
            latest = df.iloc[-1]
            prev_close = df.iloc[-2]['close']
            
            # Calculate Gap (Current Open vs Previous Close)
            gap_percent = ((latest['open'] - prev_close) / prev_close) * 100
            gap_str = f"{gap_percent:+.2f}%"

            # 3. Format Data for Gemini
            market_context = f"Instrument: {self.epic}\n"
            market_context += "Recent OHLC Data (Last 4 Hours, 15m intervals):\n"
            market_context += df.tail(16).to_string()
            
            market_context += "\n\n--- Technical Indicators (Latest Candle) ---\n"
            market_context += f"RSI (14): {latest['RSI']:.2f}\n"
            market_context += f"ATR (14): {latest['ATR']:.2f}\n"
            market_context += f"EMA (20): {latest['EMA_20']:.2f}\n"
            market_context += f"Current Close: {latest['close']}\n"
            market_context += f"Gap (Open vs Prev Close): {gap_str}\n"
            market_context += f"Trend Context: {'Price > EMA20 (Bullish)' if latest['close'] > latest['EMA_20'] else 'Price < EMA20 (Bearish)'}\n"
            
            # --- VIX Check ---
            try:
                vix_data = self.client.service.fetch_market_by_epic(self.vix_epic)
                if vix_data and 'snapshot' in vix_data:
                    vix_bid = vix_data['snapshot'].get('bid')
                    if vix_bid:
                        market_context += f"VIX Level: {vix_bid} (Market Fear Index)\n"
            except Exception as e:
                logger.warning(f"Failed to fetch VIX data: {e}")
            
            # 4. Fetch and Append News
            query = self.news_query if self.news_query else self._get_news_query(self.epic)
            news_context = self.news_fetcher.fetch_news(query)
            market_context += f"\n\n{news_context}"

            # 5. Get Analysis
            signal = self.analyst.analyze_market(market_context, strategy_name=self.strategy_name)
            
            if signal:
                # Ensure the ATR from analysis is correctly populated in the signal for later checks
                if signal.atr is None: # If Gemini doesn't provide it, use our calculated one
                    signal.atr = latest['ATR']
                
                if signal.action != Action.WAIT:
                    self.active_plan = signal
                    logger.info(f"PLAN GENERATED: {signal.action} {signal.size} at {signal.entry} (Stop: {signal.stop_loss}, TP: {signal.take_profit}) Conf: {signal.confidence}")
                else:
                    logger.info("PLAN RESULT: Gemini advised WAIT.")
                    self.active_plan = None
                logger.info(f"reasoning: {signal.reasoning}")
            else:
                logger.warning("PLAN RESULT: Gemini signal generation failed.")
                self.active_plan = None
                
        except Exception as e:
            logger.error(f"Error generating plan: {e}")

    def _get_news_query(self, epic: str) -> str:
        """
        Maps an IG epic to a search query for Google News.
        """
        if "FTSE" in epic:
            return "FTSE 100 UK Economy"
        elif "SPX" in epic or "US500" in epic:
            return "S&P 500 US Economy"
        elif "GBP" in epic:
            return "GBP USD Forex"
        elif "EUR" in epic:
            return "EUR USD Forex"
        elif "DAX" in epic or "DE30" in epic:
            return "DAX 40 Germany Economy"
        else:
            # Fallback: try to make a generic query from the epic parts
            parts = epic.split('.')
            if len(parts) > 2:
                return f"{parts[2]} Market News"
            return "Global Financial Markets"

    def execute_strategy(self, timeout_seconds: int = 5400): # Default to 90 minutes
        """
        Step 2: Uses the streaming API to monitor prices and triggers Market Order when level is hit.
        """
        if not self.active_plan:
            logger.info("SKIPPED: No active plan to execute.")
            return

        logger.info(f"Starting execution monitor (Streaming price updates). Timeout in {timeout_seconds}s...")
        
        start_time = time.time()
        last_log_time = start_time
        plan = self.active_plan
        
        try:
            # 1. Connect to stream and subscribe to epic
            # The StreamManager handles its own connection/reconnection
            self.stream_manager.connect_and_subscribe(self.epic, self._stream_price_update_handler)

            # Keep main thread alive until trade execution, interrupt, or timeout
            while not self.position_open:
                if (time.time() - start_time) > timeout_seconds:
                    logger.info(f"Strategy for {self.epic} timed out after {timeout_seconds}s. No trade executed.")
                    break # Exit loop on timeout
                
                # Sleep for a short duration to prevent busy-spinning and reduce CPU usage.
                # The stream handler will update prices in the background.
                time.sleep(0.1)

                if self.current_bid == 0 or self.current_offer == 0:
                    continue

                # Log status every 10 seconds to show it's alive
                current_time = time.time()
                if current_time - last_log_time > 10:
                    logger.info(f"MONITORING ({self.epic}): Target Entry={plan.entry}, Current Offer={self.current_offer}, Current Bid={self.current_bid} (Loop Status)")
                    last_log_time = current_time
                
                # DEBUG: Log current prices and entry on every iteration
                logger.debug(f"DEBUG-EXEC: Epic={self.epic}, Current Bid={self.current_bid}, Current Offer={self.current_offer}, Target Entry={plan.entry}, Action={plan.action.value}")

                # --- Spread and Trigger Logic ---
                current_spread = round(abs(self.current_offer - self.current_bid), 2)
                triggered = False

                if current_spread > self.max_spread:
                    current_time = time.time()
                    if current_time - self.last_skipped_log_time > 5: # Log at most every 5 seconds
                        logger.info(f"SKIPPED: Spread ({current_spread}) is wider than max allowed ({self.max_spread}). Holding off trigger for {self.epic}.")
                        self.last_skipped_log_time = current_time
                    continue # Skip to next iteration, don't check price trigger
                
                if plan.action == Action.BUY:
                    # Buy if Offer price moves UP to our entry level
                    if self.current_offer >= plan.entry:
                        triggered = True
                        logger.info(f"BUY TRIGGERED: Offer {self.current_offer} >= Entry {plan.entry} (Spread: {current_spread})")
                        
                elif plan.action == Action.SELL:
                    # Sell if Bid price moves DOWN to our entry level
                    if self.current_bid <= plan.entry:
                        triggered = True
                        logger.info(f"SELL TRIGGERED: Bid {self.current_bid} <= Entry {plan.entry} (Spread: {current_spread})")
                
                if triggered:
                    success = self._place_market_order(plan, current_spread, dry_run=self.dry_run)
                    if not success:
                        logger.info("Trade attempt failed or was skipped. Halting further attempts for this plan.")
                    # Whether successful or not, we break after one attempt.
                    break # Exit the monitoring loop
                    
        except KeyboardInterrupt:
            logger.info("Execution stopped by user.")
        except Exception as e:
            logger.error(f"Execution error: {e}")
        finally:
            self.stream_manager.stop() # Ensure stream is stopped
            logger.info("Execution monitor stopped.")

    def _stream_price_update_handler(self, data: dict):
        """
        Callback function to receive price updates from the stream.
        """
        epic = data.get('epic')
        bid = data.get('bid', 0.0)
        offer = data.get('offer', 0.0)
        market_state = data.get('market_state', 'UNKNOWN')

        if epic == self.epic:
            self.current_bid = bid
            self.current_offer = offer

    def _calculate_size(self, entry: float, stop_loss: float) -> float:
        """
        Calculates position size based on account risk.
        Formula: Size = (Account Balance * Risk %) / Stop Distance
        """
        try:
            all_accounts = self.client.get_account_info()
            balance = 0.0

            # Ensure we're looking at the correctly authenticated account
            if self.client.service.account_id and isinstance(all_accounts, pd.DataFrame):
                target_account_df = all_accounts[all_accounts['accountId'] == self.client.service.account_id]
                if not target_account_df.empty:
                    # Prioritize 'available' funds for trading
                    if 'available' in target_account_df.columns:
                        balance = float(target_account_df.iloc[0]['available'])
                    elif 'balance' in target_account_df.columns:
                        # Fallback to general balance if 'available' isn't there or if it's nested (though inspect showed it's top-level)
                        val = target_account_df.iloc[0]['balance']
                        if isinstance(val, dict):
                            balance = float(val.get('available', 0))
                        else:
                            balance = float(val)
                else:
                    logger.warning(f"Authenticated account ID {self.client.service.account_id} not found in fetched accounts. Defaulting to minimum size.")
                    return 0.5
            elif isinstance(all_accounts, dict) and 'accounts' in all_accounts:
                 # Handle raw dictionary response if it's not converted to DataFrame by client
                 for acc in all_accounts['accounts']:
                     if acc.get('accountId') == self.client.service.account_id:
                         if 'available' in acc:
                             balance = float(acc['available'])
                         elif 'balance' in acc and isinstance(acc['balance'], dict):
                             balance = float(acc['balance'].get('available', 0))
                         else:
                             balance = float(acc.get('balance', 0))
                         break
                 if balance == 0:
                     logger.warning(f"Authenticated account ID {self.client.service.account_id} not found in fetched accounts dict. Defaulting to minimum size.")
                     return 0.5
            else:
                logger.warning(f"Unknown accounts response type or no account ID set in client. Defaulting to minimum size. Type: {type(all_accounts)}")
                return 0.5
            
            if balance <= 0: # Sanity check for negative or zero balance
                logger.warning(f"Calculated balance is {balance}. Cannot calculate trade size. Defaulting to minimum size.")
                return 0.5
            
            risk_amount = balance * RISK_PER_TRADE_PERCENT
            stop_distance = abs(entry - stop_loss)
            
            if stop_distance <= 0:
                logger.error("Invalid stop distance (0 or negative).")
                return 0.5

            calculated_size = risk_amount / stop_distance
            
            # Round to 2 decimal places
            calculated_size = round(calculated_size, 2)
            
            logger.info(f"Dynamic Sizing: Balance={balance}, Risk={RISK_PER_TRADE_PERCENT*100}%, Risk Amount={risk_amount}, Stop Dist={stop_distance}, Size={calculated_size}")
            
            return calculated_size
            
        except Exception as e:
            logger.error(f"Error calculating size: {e}. Defaulting to 0.5")
            return 0.5

    def _place_market_order(self, plan: TradingSignal, current_spread: float, dry_run: bool) -> bool:
        """
        Executes the trade via IG Client as a Market Order (Spread Bet),
        or simulates it if dry_run is True.
        Returns True if order placed/simulated, False if skipped/failed.
        """
        try:
            logger.info("Placing MARKET order...")
            direction = "BUY" if plan.action == Action.BUY else "SELL"
            deal_id = None # Initialize deal_id
            
            if plan.stop_loss is None:
                logger.warning("Mandatory stop_loss is missing from the plan. Cannot place order.")
                return False

            # --- Stop vs. ATR Check ---
            if plan.atr and plan.atr > 0:
                stop_distance = abs(plan.entry - plan.stop_loss)
                stop_to_atr_ratio = round(stop_distance / plan.atr, 2)
                
                if stop_to_atr_ratio < 1.0:
                    logger.warning(f"Stop Loss for {self.epic} is tight ({stop_to_atr_ratio}x ATR). Consider wider stop (Entry: {plan.entry}, Stop: {plan.stop_loss}, ATR: {plan.atr}).")
            else:
                logger.warning("ATR not available in plan for stop tightness check.")
            # --- End Stop vs. ATR Check ---

            # --- Dynamic Sizing ---
            size = self._calculate_size(plan.entry, plan.stop_loss)
            # Ensure min size (safeguard, though IG API will reject if too small, usually 0.5 or 0.04)
            if size < 0.04:
                 logger.warning(f"Calculated size {size} is below minimum (0.04). Setting to 0.04.")
                 size = 0.04
            # Log the final calculated size
            logger.info(f"Final calculated trade size for {self.epic}: {size}")
            # --- End Dynamic Sizing ---

            if dry_run:
                logger.info(f"DRY RUN: Order would have been PLACED for {direction} {size} {self.epic} at entry {plan.entry} (Stop: {plan.stop_loss}, TP: {plan.take_profit}). Spread: {current_spread}.")
                outcome = "DRY_RUN_PLACED"
            else:
                logger.info("Placing LIVE MARKET order...")
                # confirmation is now returned (dict with dealId, dealStatus, etc.)
                confirmation = self.client.place_spread_bet_order(
                    epic=self.epic,
                    direction=direction,
                    size=size,
                    level=plan.entry, 
                    stop_level=plan.stop_loss,
                    limit_level=plan.take_profit
                )
                logger.info("LIVE Market Order successfully placed.")
                outcome = "LIVE_PLACED"
                
                # Start Monitoring
                if confirmation and 'dealId' in confirmation:
                    deal_id = confirmation['dealId']
                    logger.info(f"Starting to monitor trade {deal_id}...")
                    self.trade_monitor.monitor_trade(deal_id, self.epic)
                else:
                    logger.warning("Could not extract dealId from confirmation. Monitoring skipped.")
            
            self.position_open = True # Set to True even in dry run to stop polling

            # Log the placed trade
            self.trade_logger.log_trade(
                epic=self.epic,
                plan=plan,
                outcome=outcome,
                spread_at_entry=current_spread,
                is_dry_run=dry_run,
                deal_id=deal_id
            )
            return True
            
        except Exception as e:
            logger.error(f"Failed to execute trade: {e}")
            return False
