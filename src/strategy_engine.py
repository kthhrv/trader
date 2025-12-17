import logging
import time
import uuid
import threading
from typing import Optional
from datetime import datetime
import pandas as pd
import pandas_ta as ta
from config import IS_LIVE, RISK_PER_TRADE_PERCENT
from src.ig_client import IGClient
from src.gemini_analyst import GeminiAnalyst, TradingSignal, Action, EntryType
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
        self.stream_manager = stream_manager if stream_manager else StreamManager(self.client) # Initialize first
        self.trade_monitor = trade_monitor if trade_monitor else TradeMonitorDB(self.client, self.stream_manager, market_status=self.market_status)
        
        self.vix_epic = "CC.D.VIX.USS.IP"
        
        self.active_plan: Optional[TradingSignal] = None
        self.position_open = False
        self.current_bid: float = 0.0
        self.current_offer: float = 0.0
        self.price_lock = threading.Lock() # Lock for synchronizing price updates
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
            
            # Fetch Daily Data for macro trend context
            df_daily = self.client.fetch_historical_data(self.epic, "D", 10)
            if df_daily.empty:
                logger.warning("No daily data received from IG. Proceeding with 15Min data only.")
            
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
            
            # --- Volatility Context (New) ---
            # Calculate average ATR of the loaded period (approx 50 candles)
            avg_atr = df['ATR'].mean()
            current_atr = latest['ATR']
            vol_ratio = current_atr / avg_atr if avg_atr > 0 else 1.0
            
            vol_state = "MEDIUM"
            if vol_ratio < 0.8:
                vol_state = "LOW (Caution: Market may be choppy/ranging)"
            elif vol_ratio > 1.2:
                vol_state = "HIGH (Caution: Expect wider swings)"
            
            # Calculate Gap (Current Price vs Yesterday's Close)
            yesterday_close = prev_close # Default fallback to previous 15m close
            if not df_daily.empty and len(df_daily) >= 2:
                 # Assuming last row is 'Today' (forming) and second to last is 'Yesterday' (Confirmed)
                 yesterday_close = df_daily.iloc[-2]['close']

            gap_percent = ((latest['close'] - yesterday_close) / yesterday_close) * 100
            gap_str = f"{gap_percent:+.2f}%"

            # 3. Format Data for Gemini
            market_context = f"Instrument: {self.epic}\n"
            
            if not df_daily.empty:
                market_context += "\n--- Daily OHLC Data (Last 10 Days) ---\n"
                market_context += df_daily.to_string()
                market_context += "\n"

            market_context += "Recent OHLC Data (Last 12 Hours, 15m intervals):\n"
            market_context += df.to_string()
            
            market_context += "\n\n--- Technical Indicators (Latest Candle) ---\n"
            market_context += f"RSI (14): {latest['RSI']:.2f}\n"
            market_context += f"ATR (14): {current_atr:.2f}\n"
            market_context += f"Avg ATR (Last 50): {avg_atr:.2f}\n"
            market_context += f"Volatility Regime: {vol_state} (Current/Avg Ratio: {vol_ratio:.2f})\n"
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
                
                # --- Hardcoded Safety Checks ---
                if signal.action != Action.WAIT:
                    if not self._validate_plan(signal):
                        logger.warning("PLAN RESULT: Gemini plan failed safety validation. Treating as WAIT.")
                        self.active_plan = None
                        return

                if signal.action != Action.WAIT:
                    self.active_plan = signal
                    logger.info(f"PLAN GENERATED: {signal.action} {signal.size} at {signal.entry} (Stop: {signal.stop_loss}, TP: {signal.take_profit}) Conf: {signal.confidence} Type: {signal.entry_type.value}")
                else:
                    logger.info("PLAN RESULT: Gemini advised WAIT.")
                    self.active_plan = None
                logger.info(f"reasoning: {signal.reasoning}")
            else:
                logger.warning("PLAN RESULT: Gemini signal generation failed.")
                self.active_plan = None
                
        except Exception as e:
            logger.error(f"Error generating plan: {e}")

    def _validate_plan(self, plan: TradingSignal) -> bool:
        """
        Performs hardcoded sanity checks on the generated plan.
        Returns True if valid, False if rejected.
        """
        try:
            # 1. Entry vs Stop Logic
            if plan.action == Action.BUY:
                if plan.entry <= plan.stop_loss:
                    logger.warning(f"Validation Failed: BUY Entry ({plan.entry}) must be > Stop Loss ({plan.stop_loss}).")
                    return False
            elif plan.action == Action.SELL:
                if plan.entry >= plan.stop_loss:
                    logger.warning(f"Validation Failed: SELL Entry ({plan.entry}) must be < Stop Loss ({plan.stop_loss}).")
                    return False

            risk_dist = abs(plan.entry - plan.stop_loss)

            # 2. Risk/Reward Ratio (only if TP is set)
            if plan.take_profit:
                reward_dist = abs(plan.take_profit - plan.entry)
                if risk_dist > 0:
                    rr_ratio = reward_dist / risk_dist
                    if rr_ratio < 1.0:
                        logger.warning(f"Validation Failed: Risk/Reward Ratio {rr_ratio:.2f} is < 1.0 (Risk: {risk_dist:.2f}, Reward: {reward_dist:.2f}).")
                        return False
            
            # 3. Stop Tightness vs ATR
            if plan.atr and plan.atr > 0:
                # Min Stop Distance (0.5 * ATR)
                min_stop = 0.5 * plan.atr
                if risk_dist < min_stop:
                    logger.warning(f"Validation Failed: Stop Distance {risk_dist:.2f} is too tight (< 0.5 * ATR: {min_stop:.2f}).")
                    return False
                
                # Max Stop Distance (5.0 * ATR) - Prevent "wide" stops
                max_stop = 5.0 * plan.atr
                if risk_dist > max_stop:
                    logger.warning(f"Validation Failed: Stop Distance {risk_dist:.2f} is too wide (> 5.0 * ATR: {max_stop:.2f}).")
                    return False
            
            logger.info("Validation Successful: Gemini plan accepted.")
            return True
        except Exception as e:
            logger.error(f"Error during plan validation: {e}")
            return False

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
        last_checked_minute = -1
        plan = self.active_plan
        
        try:
            # 1. Connect to stream and subscribe to epic
            # The StreamManager handles its own connection/reconnection
            self.stream_manager.connect_and_subscribe(self.epic, self._stream_price_update_handler)

            # Keep main thread alive until trade execution, interrupt, or timeout
            while not self.position_open:
                if (time.time() - start_time) > timeout_seconds:
                    logger.info(f"Strategy for {self.epic} timed out after {timeout_seconds}s. No trade executed.")
                    
                    # Generate a synthetic deal_id for post-mortem analysis
                    synthetic_deal_id = f"TIMEOUT_{uuid.uuid4().hex[:8]}"
                    
                    # Log the timeout event to DB
                    self.trade_logger.log_trade(
                        epic=self.epic,
                        plan=self.active_plan,
                        outcome="TIMED_OUT",
                        spread_at_entry=0.0,
                        is_dry_run=self.dry_run,
                        deal_id=synthetic_deal_id,
                        entry_type=self.active_plan.entry_type.value if self.active_plan.entry_type else "UNKNOWN"
                    )
                    
                    break # Exit loop on timeout
                
                # Sleep for a short duration to prevent busy-spinning and reduce CPU usage.
                # The stream handler will update prices in the background.
                time.sleep(0.1)

                # Thread-safe read of prices
                with self.price_lock:
                    bid_snapshot = self.current_bid
                    offer_snapshot = self.current_offer

                if bid_snapshot == 0 or offer_snapshot == 0:
                    continue

                # Log status every 10 seconds to show it's alive
                current_time = time.time()
                if current_time - last_log_time > 10:
                    wait_msg = ""
                    if plan.entry_type == EntryType.INSTANT:
                        if plan.action == Action.BUY:
                            wait_msg = f"Waiting for BUY trigger (INSTANT): Offer {offer_snapshot} >= {plan.entry}"
                        elif plan.action == Action.SELL:
                            wait_msg = f"Waiting for SELL trigger (INSTANT): Bid {bid_snapshot} <= {plan.entry}"
                    elif plan.entry_type == EntryType.CONFIRMATION:
                        if plan.action == Action.BUY:
                            wait_msg = f"Waiting for BUY trigger (CONFIRMATION): Candle Close > {plan.entry}"
                        elif plan.action == Action.SELL:
                            wait_msg = f"Waiting for SELL trigger (CONFIRMATION): Candle Close < {plan.entry}"
                    
                    logger.info(f"MONITORING ({self.epic}): {wait_msg} | Current Bid/Offer: {bid_snapshot}/{offer_snapshot}")
                    last_log_time = current_time
                
                # --- Spread and Trigger Logic ---
                current_spread = round(abs(offer_snapshot - bid_snapshot), 2)
                triggered = False

                if current_spread > self.max_spread:
                    current_time = time.time()
                    if current_time - self.last_skipped_log_time > 5: # Log at most every 5 seconds
                        logger.info(f"SKIPPED: Spread ({current_spread}) is wider than max allowed ({self.max_spread}). Holding off trigger for {self.epic}.")
                        self.last_skipped_log_time = current_time
                    continue # Skip to next iteration, don't check price trigger
                
                if plan.entry_type == EntryType.INSTANT:
                    # Logic 1: INSTANT (Touch Entry)
                    if plan.action == Action.BUY:
                        if offer_snapshot >= plan.entry:
                            triggered = True
                            logger.info(f"BUY TRIGGERED (INSTANT): Offer {offer_snapshot} >= Entry {plan.entry} (Spread: {current_spread})")
                            
                    elif plan.action == Action.SELL:
                        if bid_snapshot <= plan.entry:
                            triggered = True
                            logger.info(f"SELL TRIGGERED (INSTANT): Bid {bid_snapshot} <= Entry {plan.entry} (Spread: {current_spread})")
                
                elif plan.entry_type == EntryType.CONFIRMATION:
                    # Logic 2: CONFIRMATION (Candle Close)
                    current_dt = datetime.now()
                    if current_dt.minute != last_checked_minute:
                        # Only check once per minute to avoid API spam, slightly after the minute mark ideally
                        # Fetch last 1-minute candle
                        try:
                            # Fetch 2 points to ensure we get the latest completed one
                            df_1m = self.client.fetch_historical_data(self.epic, "1Min", 2)
                            if not df_1m.empty:
                                # IG often returns the *current open* candle as the last row.
                                # The second to last row is the fully closed candle.
                                # Or check timestamps.
                                # For safety, let's look at the latest candle and see if its close breaches.
                                # If it's a "closed" candle, we use it. If it's live, we use it (proxy for "closing").
                                # Standard approach: Wait for candle to *complete*.
                                # Let's assume the last row is the current forming candle. 
                                # So we look at df_1m.iloc[-2] if len >= 2
                                if len(df_1m) >= 2:
                                    last_closed_candle = df_1m.iloc[-2]
                                    close_price = last_closed_candle['close']
                                    
                                    if plan.action == Action.BUY and close_price > plan.entry:
                                        triggered = True
                                        logger.info(f"BUY TRIGGERED (CONFIRMATION): 1m Close {close_price} > Entry {plan.entry}")
                                    elif plan.action == Action.SELL and close_price < plan.entry:
                                        triggered = True
                                        logger.info(f"SELL TRIGGERED (CONFIRMATION): 1m Close {close_price} < Entry {plan.entry}")
                                    else:
                                        logger.info(f"Checked CONFIRMATION ({current_dt.strftime('%H:%M')}): Close {close_price} did not trigger {plan.action} (Target: {plan.entry})")
                                        
                                    last_checked_minute = current_dt.minute
                        except Exception as e:
                            logger.error(f"Error fetching 1m data for confirmation: {e}")

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
            with self.price_lock:
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

            # --- Sizing Logic ---
            if self.strategy_name == "TEST_TRADE":
                # For manual test trades, use the fixed size from the plan (e.g. 0.5)
                # This ensures consistency and ignores account balance fluctuations.
                size = plan.size
                logger.info(f"TEST_TRADE: Using fixed size from plan: {size}")
            else:
                # Dynamic Sizing for Strategy Execution
                size = self._calculate_size(plan.entry, plan.stop_loss)
                # Ensure min size (safeguard, though IG API will reject if too small, usually 0.5 or 0.04)
                if size < 0.04:
                     logger.warning(f"Calculated size {size} is below minimum (0.04). Setting to 0.04.")
                     size = 0.04
                # Log the final calculated size
                logger.info(f"Final calculated trade size for {self.epic}: {size}")
            # --- End Sizing Logic ---

            # --- Take Profit Override for Trailing Stop ---
            take_profit_level = plan.take_profit
            if plan.use_trailing_stop:
                logger.info("Using Trailing Stop strategy. Overriding Take Profit to None for uncapped upside.")
                take_profit_level = None

            if dry_run:
                logger.info(f"DRY RUN: Order would have been PLACED for {direction} {size} {self.epic} at entry {plan.entry} (Stop: {plan.stop_loss}, TP: {take_profit_level}). Spread: {current_spread}.")
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
                    limit_level=take_profit_level
                )
                logger.info("LIVE Market Order successfully placed.")
                outcome = "LIVE_PLACED"
                
                if confirmation and 'dealId' in confirmation:
                    deal_id = confirmation['dealId']
                else:
                    logger.warning("Could not extract dealId from confirmation.")

            self.position_open = True # Set to True even in dry run to stop polling

            # Log the placed trade FIRST (Insert)
            self.trade_logger.log_trade(
                epic=self.epic,
                plan=plan,
                outcome=outcome,
                spread_at_entry=current_spread,
                is_dry_run=dry_run,
                deal_id=deal_id,
                entry_type=plan.entry_type.value if plan.entry_type else "UNKNOWN"
            )

            # Start Monitoring (Update upon close) - Blocking call
            if not dry_run and deal_id:
                logger.info(f"Starting to monitor trade {deal_id}...")
                self.trade_monitor.monitor_trade(
                    deal_id, 
                    self.epic, 
                    entry_price=plan.entry, 
                    stop_loss=plan.stop_loss, 
                    atr=plan.atr,
                    use_trailing_stop=plan.use_trailing_stop
                )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to execute trade: {e}")
            return False
