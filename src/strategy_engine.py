import logging
import time
import threading
from typing import Optional
import pandas as pd
import pandas_ta as ta
from config import RISK_PER_TRADE_PERCENT
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

            yesterday_close = prev_close
            if not df_daily.empty and len(df_daily) >= 2:
                yesterday_close = df_daily.iloc[-2]["close"]

            gap_percent = ((latest["close"] - yesterday_close) / yesterday_close) * 100
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
                logger.warning("PLAN RESULT: Gemini signal generation failed.")
                error_signal = TradingSignal(
                    ticker=self.epic,
                    action=Action.WAIT,
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

    def execute_strategy(self, timeout_seconds: int = 5400):
        """
        Step 2: Monitoring loop. Triggers immediate entry on touch.
        Adjusts Stop Loss dynamically to maintain planned risk distance if slippage occurs.
        """
        if not self.active_plan:
            logger.info("SKIPPED: No active plan to execute.")
            return

        logger.info(
            f"Starting execution monitor (Streaming price updates). Timeout in {timeout_seconds}s..."
        )

        start_time = time.time()
        last_log_time = start_time
        plan = self.active_plan
        decision_made = False  # Track if we've attempted a trade or rejected one

        try:
            self.stream_manager.connect_and_subscribe(
                self.epic, self._stream_price_update_handler
            )

            while not self.position_open and not decision_made:
                if (time.time() - start_time) > timeout_seconds:
                    logger.info(
                        f"Strategy for {self.epic} timed out after {timeout_seconds}s. No trade executed."
                    )
                    if self.active_plan_id and not self.position_open:
                        # Only update to TIMED_OUT if we haven't already made a decision (like REJECTED_CHASE)
                        self.trade_logger.update_trade_status(
                            row_id=self.active_plan_id,
                            outcome="TIMED_OUT",
                            deal_id=None,
                        )
                    elif not self.active_plan_id:
                        # Fallback: Log the timeout event to DB if no initial PENDING log exists
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
                    decision_made = True
                    break

                time.sleep(0.1)

                with self.price_lock:
                    bid_snapshot = self.current_bid
                    offer_snapshot = self.current_offer

                if bid_snapshot == 0 or offer_snapshot == 0:
                    continue

                # Periodic Logging
                current_time = time.time()
                if current_time - last_log_time > 10:
                    wait_msg = ""
                    if plan.action == Action.WAIT:
                        wait_msg = "Monitoring Mode (WAIT): Recording data only."
                    else:
                        if plan.action == Action.BUY:
                            wait_msg = f"Waiting for BUY trigger: Offer {offer_snapshot} >= {plan.entry}"
                        elif plan.action == Action.SELL:
                            wait_msg = f"Waiting for SELL trigger: Bid {bid_snapshot} <= {plan.entry}"

                    logger.info(
                        f"MONITORING ({self.epic}): {wait_msg} | Current Bid/Offer: {bid_snapshot}/{offer_snapshot}"
                    )
                    last_log_time = current_time

                # Spread Check
                current_spread = round(abs(offer_snapshot - bid_snapshot), 2)
                if plan.action == Action.WAIT:
                    continue

                if current_spread > self.max_spread:
                    if current_time - self.last_skipped_log_time > 5:
                        logger.info(
                            f"SKIPPED: Spread ({current_spread}) is wider than max allowed ({self.max_spread}). Holding off trigger."
                        )
                        self.last_skipped_log_time = current_time
                    continue

                # Trigger Logic (Touch Entry)
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
                    # --- Maintain Structural Risk Distance ---
                    # Calculate original planned risk distance (points)
                    original_risk = abs(plan.entry - plan.stop_loss)

                    if abs(trigger_price - plan.entry) > 0.1:  # Significant deviation
                        logger.info(
                            f"Trigger Price ({trigger_price}) deviates from Target ({plan.entry}). "
                            f"Adjusting Stop Loss to maintain {original_risk:.2f} risk distance."
                        )

                        # Update plan with the price reality at the moment of trigger
                        plan.entry = trigger_price
                        if plan.action == Action.BUY:
                            plan.stop_loss = trigger_price - original_risk
                        else:
                            plan.stop_loss = trigger_price + original_risk

                    # Proceed to execution
                    success = self._place_market_order(
                        plan, current_spread, dry_run=self.dry_run
                    )
                    if not success:
                        logger.warning(
                            "Trade execution failed or was rejected internally."
                        )
                    decision_made = True
                    break

            # --- Post-Decision Data Recording ---
            elapsed = time.time() - start_time
            remaining = timeout_seconds - elapsed

            if remaining > 0:
                logger.info(
                    f"Session active. Continuing to record market data for {remaining:.0f}s until timeout..."
                )
                while time.time() - start_time < timeout_seconds:
                    time.sleep(5)
                logger.info("Session timeout reached.")

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
                    return 0.5
            elif isinstance(all_accounts, dict) and "accounts" in all_accounts:
                for acc in all_accounts["accounts"]:
                    if acc.get("accountId") == self.client.service.account_id:
                        balance = float(
                            acc.get("available")
                            or acc.get("balance", {}).get("available", 0)
                        )
                        break

            if balance <= 0:
                return 0.5

            risk_amount = balance * RISK_PER_TRADE_PERCENT
            stop_distance = abs(entry - stop_loss)

            if stop_distance <= 0:
                return 0.5

            calculated_size = round(risk_amount / stop_distance, 2)
            logger.info(f"Dynamic Sizing: Balance={balance}, Size={calculated_size}")
            return calculated_size

        except Exception as e:
            logger.error(f"Error calculating size: {e}. Defaulting to 0.5")
            return 0.5

    def _place_market_order(
        self,
        plan: TradingSignal,
        current_spread: float,
        dry_run: bool,
    ) -> bool:
        try:
            logger.info("Placing MARKET order...")
            direction = "BUY" if plan.action == Action.BUY else "SELL"
            deal_id = None
            execution_price = plan.entry

            if plan.stop_loss is None:
                return False

            if self.strategy_name == "TEST_TRADE":
                size = plan.size
            else:
                size = self._calculate_size(plan.entry, plan.stop_loss)
                size = max(size, 0.04)

            take_profit_level = plan.take_profit
            if plan.use_trailing_stop:
                take_profit_level = None

            if dry_run:
                logger.info(f"DRY RUN: {direction} {size} {self.epic} at {plan.entry}")
                outcome = "DRY_RUN_PLACED"
            else:
                confirmation = self.client.place_spread_bet_order(
                    epic=self.epic,
                    direction=direction,
                    size=size,
                    level=plan.entry,
                    stop_level=plan.stop_loss,
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
                )

            if not dry_run and deal_id:
                self.trade_monitor.monitor_trade(
                    deal_id,
                    self.epic,
                    entry_price=execution_price,
                    stop_loss=plan.stop_loss,
                    atr=plan.atr,
                    use_trailing_stop=plan.use_trailing_stop,
                )

            return True

        except Exception as e:
            logger.error(f"Failed to execute trade: {e}")
            return False
