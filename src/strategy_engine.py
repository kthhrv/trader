import logging
import time
import threading
from datetime import datetime, timezone  # Added timezone
from typing import Optional
import pandas as pd
import pandas_ta as ta
from config import RISK_PER_TRADE_PERCENT, MIN_ACCOUNT_BALANCE
from src.ig_client import IGClient
from src.gemini_analyst import GeminiAnalyst, TradingSignal, Action, EntryType
from src.news_fetcher import NewsFetcher
from src.trade_logger_db import TradeLoggerDB
from src.trade_monitor_db import TradeMonitorDB
from src.market_status import MarketStatus
from src.stream_manager import StreamManager  # Import the new StreamManager

logger = logging.getLogger(__name__)


class StrategyEngine:
    def __init__(
        self,
        epic: str,
        strategy_name: str = "Market Open",
        news_query: str = None,
        dry_run: bool = False,
        verbose: bool = False,
        max_spread: float = 2.0,
        ignore_holidays: bool = False,
        ig_client: Optional[IGClient] = None,
        analyst: Optional[GeminiAnalyst] = None,
        news_fetcher: Optional[NewsFetcher] = None,
        trade_logger: Optional[TradeLoggerDB] = None,
        trade_monitor: Optional[TradeMonitorDB] = None,
        market_status: Optional[MarketStatus] = None,
        stream_manager: Optional[StreamManager] = None,
        risk_scale: float = 1.0,
        min_size: float = 0.5,
    ):
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
        self.ignore_holidays = ignore_holidays
        self.risk_scale = risk_scale
        self.min_size = min_size

        self.client = ig_client if ig_client else IGClient()
        self.analyst = analyst if analyst else GeminiAnalyst()
        self.news_fetcher = news_fetcher if news_fetcher else NewsFetcher()
        self.market_status = market_status if market_status else MarketStatus()
        self.trade_logger = trade_logger if trade_logger else TradeLoggerDB()
        self.stream_manager = (
            stream_manager if stream_manager else StreamManager(self.client)
        )
        self.trade_monitor = (
            trade_monitor
            if trade_monitor
            else TradeMonitorDB(
                self.client, self.stream_manager, market_status=self.market_status
            )
        )

        self.vix_epic = "CC.D.VIX.USS.IP"

        self.active_plan: Optional[TradingSignal] = None
        self.active_plan_id: Optional[int] = None
        self.position_open = False
        self.current_bid: float = 0.0
        self.current_offer: float = 0.0
        self.price_lock = threading.Lock()  # Lock for synchronizing price updates
        self.last_skipped_log_time: float = 0.0  # For rate-limiting skipped logs

    def generate_plan(self):
        """
        Step 1: Fetches data, asks Gemini, and stores the trading plan.
        """
        # Check for holidays
        if not self.ignore_holidays and self.market_status.is_holiday(self.epic):
            logger.warning(
                f"Holiday detected for {self.epic}. Strategy execution aborted."
            )
            return

        logger.info(f"Generating plan for {self.epic} ({self.strategy_name})...")

        try:
            # 1. Fetch Historical Data (Fetch 50 points to allow for indicator calculation)
            df = self.client.fetch_historical_data(self.epic, "15Min", 50)

            # Fetch Daily Data for macro trend context
            df_daily = self.client.fetch_historical_data(self.epic, "D", 10)
            if df_daily.empty:
                logger.warning(
                    "No daily data received from IG. Proceeding with 15Min data only."
                )

            if df.empty:
                logger.error("No data received from IG.")
                return

            # 2. Calculate Technical Indicators
            cols = ["open", "high", "low", "close", "volume"]
            existing_cols = [c for c in cols if c in df.columns]
            df[existing_cols] = df[existing_cols].apply(pd.to_numeric, errors="coerce")

            # Calculate Indicators
            df["ATR"] = ta.atr(df["high"], df["low"], df["close"], length=14)
            df["RSI"] = ta.rsi(df["close"], length=14)
            df["EMA_20"] = ta.ema(df["close"], length=20)

            latest = df.iloc[-1]
            prev_close = df.iloc[-2]["close"]

            # --- Volatility Context ---
            avg_atr = df["ATR"].mean()
            current_atr = latest["ATR"]
            vol_ratio = current_atr / avg_atr if avg_atr > 0 else 1.0

            vol_state = "MEDIUM"
            if vol_ratio < 0.8:
                vol_state = "LOW (Caution: Market may be choppy/ranging)"
            elif vol_ratio > 1.2:
                vol_state = "HIGH (Caution: Expect wider swings)"

            # --- Session Extremes (Intraday) ---
            # Extract candles for the current calendar day
            today_str = datetime.now().date().isoformat()

            session_high = None
            session_low = None

            try:
                # Ensure index is DatetimeIndex before filtering
                if not isinstance(df.index, pd.DatetimeIndex):
                    # Try to convert if possible, otherwise assume we can't filter
                    temp_index = pd.to_datetime(df.index, errors="coerce")
                    if not temp_index.isna().all():
                        df_today = df[temp_index.strftime("%Y-%m-%d") == today_str]
                    else:
                        df_today = pd.DataFrame()  # Empty
                else:
                    df_today = df[df.index.strftime("%Y-%m-%d") == today_str]

                if not df_today.empty:
                    session_high = df_today["high"].max()
                    session_low = df_today["low"].min()
            except Exception as e:
                logger.warning(f"Could not calculate session extremes: {e}")

            yesterday_close = prev_close
            if not df_daily.empty and len(df_daily) >= 2:
                yesterday_close = df_daily.iloc[-2]["close"]

            gap_percent = ((latest["close"] - yesterday_close) / yesterday_close) * 100
            gap_str = f"{gap_percent:+.2f}%"

            # 3. Format Data for Gemini
            market_context = f"Current Time (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n"
            market_context += f"Instrument: {self.epic}\n"

            if not df_daily.empty:
                market_context += "\n--- Daily OHLC Data (Last 10 Days) ---\n"
                market_context += df_daily.to_string()
                market_context += "\n"

            market_context += "Recent OHLC Data (Last 12 Hours, 15m intervals):\n"
            market_context += df.to_string()

            market_context += "\n\n--- Session Context (Today so far) ---\n"
            if session_high is not None and session_low is not None:
                market_context += f"Today's High: {session_high}\n"
                market_context += f"Today's Low:  {session_low}\n"
                position_in_range = 0
                if session_high != session_low:
                    position_in_range = int(
                        ((latest["close"] - session_low) / (session_high - session_low))
                        * 100
                    )
                market_context += f"Current Position in Range: {position_in_range}% (0%=Low, 100%=High)\n"
            else:
                market_context += (
                    "Today's intraday high/low data not yet established.\n"
                )

            market_context += "\n--- Technical Indicators (Latest Candle) ---\n"
            market_context += f"RSI (14): {latest['RSI']:.2f}\n"
            market_context += f"ATR (14): {current_atr:.2f}\n"
            market_context += f"Avg ATR (Last 50): {avg_atr:.2f}\n"
            market_context += (
                f"Volatility Regime: {vol_state} (Current/Avg Ratio: {vol_ratio:.2f})\n"
            )
            market_context += f"EMA (20): {latest['EMA_20']:.2f}\n"
            market_context += f"Current Close: {latest['close']}\n"
            market_context += f"Gap (Open vs Prev Close): {gap_str}\n"
            market_context += f"Trend Context: {'Price > EMA20 (Bullish)' if latest['close'] > latest['EMA_20'] else 'Price < EMA20 (Bearish)'}\n"

            # --- VIX Check ---
            try:
                vix_data = self.client.service.fetch_market_by_epic(self.vix_epic)
                if vix_data and "snapshot" in vix_data:
                    vix_bid = vix_data["snapshot"].get("bid")
                    if vix_bid:
                        market_context += f"VIX Level: {vix_bid} (Market Fear Index)\n"
            except Exception as e:
                logger.warning(f"Failed to fetch VIX data: {e}")

            # --- Client Sentiment (Contrarian Indicator) ---
            try:
                # 1. Get Market ID (Required for sentiment lookup)
                market_details = self.client.data_service.fetch_market_by_epic(
                    self.epic
                )
                if market_details and "instrument" in market_details:
                    market_id = market_details["instrument"]["marketId"]

                    # 2. Fetch Sentiment from LIVE Data Service (to avoid Demo inversions)
                    sentiment = (
                        self.client.data_service.fetch_client_sentiment_by_instrument(
                            market_id
                        )
                    )

                    if sentiment:
                        long_pct = float(sentiment.get("longPositionPercentage", 0))
                        short_pct = float(sentiment.get("shortPositionPercentage", 0))

                        signal_hint = "NEUTRAL"
                        if long_pct > 70:
                            signal_hint = "BEARISH CONTRA (Retail is Crowded Long)"
                        elif short_pct > 70:
                            signal_hint = "BULLISH CONTRA (Retail is Crowded Short)"

                        market_context += (
                            "\n--- Client Sentiment (IG Markets - % of Accounts) ---\n"
                        )
                        market_context += f"Long: {long_pct}%\n"
                        market_context += f"Short: {short_pct}%\n"
                        market_context += f"Signal Implication: {signal_hint}\n"
            except Exception as e:
                logger.warning(f"Failed to fetch Client Sentiment: {e}")

            # 4. Fetch News
            query = (
                self.news_query if self.news_query else self._get_news_query(self.epic)
            )
            news_context = self.news_fetcher.fetch_news(query)
            market_context += f"\n\n{news_context}"

            # 5. Get Analysis
            signal = self.analyst.analyze_market(
                market_context, strategy_name=self.strategy_name
            )

            current_spread = 0.0
            try:
                market_info = self.client.get_market_info(self.epic)
                if market_info and "snapshot" in market_info:
                    current_spread = round(
                        abs(
                            float(market_info["snapshot"]["offer"])
                            - float(market_info["snapshot"]["bid"])
                        ),
                        2,
                    )
            except Exception:
                pass

            if signal:
                if signal.atr is None:
                    signal.atr = latest["ATR"]

                if signal.action != Action.WAIT:
                    if not self._validate_plan(signal):
                        logger.warning(
                            "PLAN RESULT: Gemini plan failed safety validation. Treating as WAIT."
                        )
                        self.trade_logger.log_trade(
                            epic=self.epic,
                            plan=signal,
                            outcome="REJECTED_SAFETY",
                            spread_at_entry=current_spread,
                            is_dry_run=self.dry_run,
                            entry_type=signal.entry_type.value
                            if signal.entry_type
                            else "UNKNOWN",
                        )
                        self.active_plan = None
                        return

                if signal.action != Action.WAIT:
                    self.active_plan = signal
                    logger.info(
                        f"PLAN GENERATED: {signal.action} {signal.size} at {signal.entry} (Stop: {signal.stop_loss}, TP: {signal.take_profit}) Conf: {signal.confidence} Type: {signal.entry_type.value}"
                    )

                    self.active_plan_id = self.trade_logger.log_trade(
                        epic=self.epic,
                        plan=signal,
                        outcome="PENDING",
                        spread_at_entry=current_spread,
                        is_dry_run=self.dry_run,
                        entry_type=signal.entry_type.value
                        if signal.entry_type
                        else "UNKNOWN",
                    )
                else:
                    logger.info(
                        "PLAN RESULT: Gemini advised WAIT. Proceeding to monitor mode for data collection."
                    )
                    self.active_plan = signal
                    self.active_plan_id = self.trade_logger.log_trade(
                        epic=self.epic,
                        plan=signal,
                        outcome="WAIT",
                        spread_at_entry=current_spread,
                        is_dry_run=self.dry_run,
                        entry_type=signal.entry_type.value
                        if signal.entry_type
                        else "UNKNOWN",
                    )
                logger.info(f"reasoning: {signal.reasoning}")
            else:
                logger.error("PLAN RESULT: Gemini signal generation failed.")
                error_signal = TradingSignal(
                    ticker=self.epic,
                    action=Action.ERROR,
                    entry=0.0,
                    stop_loss=0.0,
                    take_profit=0.0,
                    confidence="none",
                    reasoning="AI Analysis failed to generate a response.",
                    size=0.0,
                    atr=0.0,
                    entry_type=EntryType.INSTANT,
                    use_trailing_stop=False,
                )
                self.trade_logger.log_trade(
                    epic=self.epic,
                    plan=error_signal,
                    outcome="AI_ERROR",
                    spread_at_entry=current_spread,
                    is_dry_run=self.dry_run,
                )
                self.active_plan = None

        except Exception as e:
            logger.error(f"Error generating plan: {e}")

    def _validate_plan(self, plan: TradingSignal) -> bool:
        """
        Performs hardcoded sanity checks on the generated plan.
        """
        try:
            if plan.action == Action.BUY:
                if plan.entry <= plan.stop_loss:
                    logger.warning(
                        f"Validation Failed: BUY Entry ({plan.entry}) must be > Stop Loss ({plan.stop_loss})."
                    )
                    return False
            elif plan.action == Action.SELL:
                if plan.entry >= plan.stop_loss:
                    logger.warning(
                        f"Validation Failed: SELL Entry ({plan.entry}) must be < Stop Loss ({plan.stop_loss})."
                    )
                    return False

            risk_dist = abs(plan.entry - plan.stop_loss)

            if plan.take_profit:
                reward_dist = abs(plan.take_profit - plan.entry)
                if risk_dist > 0:
                    rr_ratio = reward_dist / risk_dist
                    if rr_ratio < 1.0:
                        logger.warning(
                            f"Validation Failed: Risk/Reward Ratio {rr_ratio:.2f} is < 1.0 (Risk: {risk_dist:.2f}, Reward: {reward_dist:.2f})."
                        )
                        return False

            if plan.atr and plan.atr > 0:
                min_stop = 0.5 * plan.atr
                if risk_dist < min_stop:
                    logger.warning(
                        f"Validation Failed: Stop Distance {risk_dist:.2f} is too tight (< 0.5 * ATR: {min_stop:.2f})."
                    )
                    return False

                max_stop = 5.0 * plan.atr
                if risk_dist > max_stop:
                    logger.warning(
                        f"Validation Failed: Stop Distance {risk_dist:.2f} is too wide (> 5.0 * ATR: {max_stop:.2f})."
                    )
                    return False

            logger.info("Validation Successful: Gemini plan accepted.")
            return True
        except Exception as e:
            logger.error(f"Error during plan validation: {e}")
            return False

    def _get_news_query(self, epic: str) -> str:
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
            parts = epic.split(".")
            if len(parts) > 2:
                return f"{parts[2]} Market News"
            return "Global Financial Markets"

    def execute_strategy(
        self, timeout_seconds: int = 5400, collection_seconds: int = 14400
    ):
        """
        Step 2: Monitoring loop. Triggers immediate entry on touch.
        Adjusts Stop Loss dynamically to maintain planned risk distance if slippage occurs.
        Stops TRADING after `timeout_seconds`, but continues RECORDING DATA until `collection_seconds`.
        """
        if not self.active_plan:
            logger.info("SKIPPED: No active plan to execute.")
            return

        logger.info(
            f"Starting execution monitor (Streaming price updates). Trade Timeout: {timeout_seconds}s, Collection: {collection_seconds}s..."
        )

        start_time = time.time()
        last_log_time = start_time
        plan = self.active_plan
        decision_made = False  # Track if we've attempted a trade or rejected one
        trading_active = True

        try:
            self.stream_manager.connect_and_subscribe(
                self.epic, self._stream_price_update_handler
            )

            # Main Loop: Runs until collection time expires or user interrupt
            while (time.time() - start_time) < collection_seconds:
                elapsed = time.time() - start_time

                # --- Trading Logic (Only if within timeout and no decision yet) ---
                if trading_active and not self.position_open and not decision_made:
                    # Check for Trading Timeout
                    if elapsed > timeout_seconds:
                        logger.info(
                            f"Strategy for {self.epic} TRADING timed out after {timeout_seconds}s. No trade executed. continuing data collection..."
                        )
                        if self.active_plan_id:
                            self.trade_logger.update_trade_status(
                                row_id=self.active_plan_id,
                                outcome="TIMED_OUT",
                                deal_id=None,
                            )
                        elif not self.active_plan_id:
                            self.trade_logger.log_trade(
                                epic=self.epic,
                                plan=self.active_plan,
                                outcome="TIMED_OUT",
                                spread_at_entry=0.0,
                                is_dry_run=self.dry_run,
                                deal_id=None,
                                entry_type=self.active_plan.entry_type.value
                                if self.active_plan.entry_type
                                else "UNKNOWN",
                            )
                        trading_active = False  # Stop looking for entry

                    else:
                        # --- Normal Trading Checks ---
                        with self.price_lock:
                            bid_snapshot = self.current_bid
                            offer_snapshot = self.current_offer

                        if bid_snapshot == 0 or offer_snapshot == 0:
                            time.sleep(0.1)
                            continue

                        # Periodic Logging (Trading Phase)
                        if time.time() - last_log_time > 10:
                            wait_msg = ""
                            if plan.action == Action.WAIT:
                                wait_msg = "Monitoring Mode (WAIT)"
                            else:
                                if plan.action == Action.BUY:
                                    wait_msg = f"Waiting BUY: Offer {offer_snapshot} >= {plan.entry}"
                                elif plan.action == Action.SELL:
                                    wait_msg = f"Waiting SELL: Bid {bid_snapshot} <= {plan.entry}"

                            logger.info(
                                f"MONITORING ({self.epic}): {wait_msg} | Current Bid/Offer: {bid_snapshot}/{offer_snapshot}"
                            )
                            last_log_time = time.time()

                        # Spread Check & Trigger Logic (Same as before)
                        current_spread = round(abs(offer_snapshot - bid_snapshot), 2)
                        if plan.action != Action.WAIT:
                            if current_spread > self.max_spread:
                                if time.time() - self.last_skipped_log_time > 5:
                                    # limit log spam
                                    logger.info(
                                        f"SKIPPED: Spread ({current_spread}) is wider than max allowed ({self.max_spread}). Holding off trigger."
                                    )
                                    self.last_skipped_log_time = time.time()
                            else:
                                triggered = False
                                trigger_price = 0.0

                                if plan.action == Action.BUY:
                                    if offer_snapshot >= plan.entry:
                                        triggered = True
                                        trigger_price = offer_snapshot
                                elif plan.action == Action.SELL:
                                    if bid_snapshot <= plan.entry:
                                        triggered = True
                                        trigger_price = bid_snapshot

                                if triggered:
                                    self._place_market_order(
                                        plan,
                                        current_spread,
                                        trigger_price,
                                        dry_run=self.dry_run,
                                    )
                                    decision_made = True
                                    trading_active = False  # Stop looking once executed

                # --- End of Trading Logic ---

                # Sleep briefly to spare CPU in the loop
                time.sleep(0.1)

            logger.info("Data collection timeout reached.")

        except KeyboardInterrupt:
            logger.info("Execution stopped by user.")
        except Exception as e:
            logger.error(f"Execution error: {e}")
        finally:
            self.stream_manager.stop()
            logger.info("Execution monitor stopped.")

    def _stream_price_update_handler(self, data: dict):
        epic = data.get("epic")
        bid = data.get("bid", 0.0)
        offer = data.get("offer", 0.0)

        if epic == self.epic:
            with self.price_lock:
                self.current_bid = bid
                self.current_offer = offer

    def _calculate_size(self, entry: float, stop_loss: float) -> float:
        try:
            all_accounts = self.client.get_account_info()
            balance = 0.0

            if self.client.service.account_id and isinstance(
                all_accounts, pd.DataFrame
            ):
                target_account_df = all_accounts[
                    all_accounts["accountId"] == self.client.service.account_id
                ]
                if not target_account_df.empty:
                    if "available" in target_account_df.columns:
                        balance = float(target_account_df.iloc[0]["available"])
                    elif "balance" in target_account_df.columns:
                        val = target_account_df.iloc[0]["balance"]
                        balance = (
                            float(val.get("available", 0))
                            if isinstance(val, dict)
                            else float(val)
                        )
                else:
                    logger.error(
                        "Could not find target account in account list. Aborting."
                    )
                    return 0.0
            elif isinstance(all_accounts, dict) and "accounts" in all_accounts:
                for acc in all_accounts["accounts"]:
                    if acc.get("accountId") == self.client.service.account_id:
                        balance = float(
                            acc.get("available")
                            or acc.get("balance", {}).get("available", 0)
                        )
                        break

            if balance <= 0:
                return 0.0

            stop_distance = abs(entry - stop_loss)
            if stop_distance <= 0:
                return 0.0

            # 1. Calculate Standard Risk Size (Unclamped)
            target_risk_amount = balance * RISK_PER_TRADE_PERCENT * self.risk_scale
            standard_size = round(target_risk_amount / stop_distance, 2)

            # 2. Check if Standard Trade is safe for the Floor
            # We check the actual risk of the standard 1% size
            if (
                MIN_ACCOUNT_BALANCE <= 0
                or (balance - target_risk_amount) >= MIN_ACCOUNT_BALANCE
            ):
                # Standard is safe. But we must trade at least broker min.
                effective_size = max(standard_size, self.min_size)

                # RE-CHECK safety of the broker min if standard_size was smaller than it
                if (balance - (effective_size * stop_distance)) >= MIN_ACCOUNT_BALANCE:
                    logger.info(
                        f"Dynamic Sizing: Balance={balance}, Size={effective_size} (Standard/Min)"
                    )
                    return effective_size

            # 3. Standard Trade (or min required for standard) Breaches Floor.
            # Drop immediately to Broker Minimum (if it wasn't already tried above).
            logger.warning(
                f"Standard Risk would breach Floor (£{MIN_ACCOUNT_BALANCE}). "
                "Attempting step-down to Broker Minimum."
            )

            min_risk = self.min_size * stop_distance
            if (balance - min_risk) >= MIN_ACCOUNT_BALANCE:
                logger.info(
                    f"Dynamic Sizing: Balance={balance}, Size={self.min_size} (Step-Down to Min)"
                )
                return self.min_size

            # 4. Even Broker Minimum breaches the floor.
            logger.warning(
                f"Even Broker Minimum Risk (£{min_risk:.2f}) would breach Floor (£{MIN_ACCOUNT_BALANCE}). "
                "Aborting trade."
            )
            return 0.0

        except Exception as e:
            logger.error(f"Error calculating size: {e}. Defaulting to 0.0")
            return 0.0

    def _place_market_order(
        self,
        plan: TradingSignal,
        current_spread: float,
        trigger_price: float,
        dry_run: bool,
    ) -> bool:
        try:
            logger.info("Placing MARKET order...")
            direction = "BUY" if plan.action == Action.BUY else "SELL"
            deal_id = None
            execution_price = trigger_price

            if plan.stop_loss is None:
                return False

            # Adjust Stop Loss by widening it by the current spread
            # This protects the trade from spread-induced stop-outs on high-spread markets.
            original_sl = plan.stop_loss
            adjusted_sl = original_sl
            if plan.action == Action.BUY:
                adjusted_sl = original_sl - current_spread
            elif plan.action == Action.SELL:
                adjusted_sl = original_sl + current_spread

            logger.info(
                f"Adjusting Stop Loss for spread ({current_spread}): {original_sl} -> {adjusted_sl}"
            )

            if self.strategy_name == "TEST_TRADE":
                size = plan.size
            else:
                # Calculate size based on the ACTUAL entry (trigger_price) and the ADJUSTED stop loss
                # This ensures total risk amount is constant.
                size = self._calculate_size(trigger_price, adjusted_sl)

                # Check for abort condition (Size too small)
                if size <= 0:
                    logger.warning(
                        "Trade Aborted: Size is 0 (Risk/Floor limits reached)."
                    )

                    # Update DB to show we tried but aborted
                    if self.active_plan_id:
                        self.trade_logger.update_trade_status(
                            row_id=self.active_plan_id,
                            outcome="ABORTED_RISK",
                            deal_id=None,
                        )
                    return False

            take_profit_level = plan.take_profit
            if plan.use_trailing_stop:
                take_profit_level = None

            if dry_run:
                logger.info(
                    f"DRY RUN: {direction} {size} {self.epic} at {trigger_price} (Stop: {adjusted_sl})"
                )
                outcome = "DRY_RUN_PLACED"
            else:
                confirmation = self.client.place_spread_bet_order(
                    epic=self.epic,
                    direction=direction,
                    size=size,
                    level=trigger_price,
                    stop_level=adjusted_sl,
                    limit_level=take_profit_level,
                )
                outcome = "LIVE_PLACED"
                if confirmation and "dealId" in confirmation:
                    deal_id = confirmation["dealId"]
                    if confirmation.get("level"):
                        execution_price = float(confirmation["level"])
                        logger.info(f"Order filled at {execution_price}")

            self.position_open = True

            if self.active_plan_id:
                self.trade_logger.update_trade_status(
                    row_id=self.active_plan_id,
                    outcome=outcome,
                    deal_id=deal_id,
                    size=size,
                    entry=execution_price,
                    stop_loss=adjusted_sl,  # Update the DB with the adjusted SL
                )

            if not dry_run and deal_id:
                self.trade_monitor.monitor_trade(
                    deal_id,
                    self.epic,
                    entry_price=execution_price,
                    stop_loss=adjusted_sl,
                    atr=plan.atr,
                    use_trailing_stop=plan.use_trailing_stop,
                )

            return True

        except Exception as e:
            logger.error(f"Failed to execute trade: {e}")
            return False
