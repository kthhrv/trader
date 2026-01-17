import logging
import pandas as pd
from typing import Optional

from config import RISK_PER_TRADE_PERCENT, MIN_ACCOUNT_BALANCE
from src.ig_client import IGClient
from src.gemini_analyst import TradingSignal, Action
from src.trade_logger_db import TradeLoggerDB
from src.trade_monitor_db import TradeMonitorDB

logger = logging.getLogger(__name__)


class TradeExecutor:
    def __init__(
        self,
        client: IGClient,
        logger_db: TradeLoggerDB,
        monitor: TradeMonitorDB,
        risk_scale: float = 1.0,
        min_size: float = 0.01,
    ):
        self.client = client
        self.logger_db = logger_db
        self.monitor = monitor
        self.risk_scale = risk_scale
        self.min_size = min_size

    def execute_trade(
        self,
        plan: TradingSignal,
        trigger_price: float,
        current_spread: float,
        row_id: Optional[int] = None,
        dry_run: bool = False,
    ) -> bool:
        """
        Executes a trade based on the plan and current market conditions.
        Handles sizing, stop adjustment, order placement, and logging.
        Returns True if trade was successfully placed (or simulated).
        """
        try:
            logger.info("Placing MARKET order...")
            direction = "BUY" if plan.action == Action.BUY else "SELL"
            deal_id = None
            execution_price = trigger_price

            if plan.stop_loss is None:
                logger.error("Trade aborted: No Stop Loss provided.")
                return False

            # Adjust Stop Loss by widening it by the current spread
            original_sl = plan.stop_loss
            adjusted_sl = original_sl
            if plan.action == Action.BUY:
                adjusted_sl = original_sl - current_spread
            elif plan.action == Action.SELL:
                adjusted_sl = original_sl + current_spread

            logger.info(
                f"Adjusting Stop Loss for spread ({current_spread}): {original_sl} -> {adjusted_sl}"
            )

            # Calculate Position Size
            # Use plan.size if explicitly set (e.g. for testing), otherwise calculate dynamically
            # Note: StrategyEngine logic implies plan.size is usually a suggestion or placeholder
            # and real sizing happens here.
            # However, if plan.size > 0 from Analyst and we trust it?
            # Actually, Analyst gives 'size' usually as '1.0' placeholder.
            # We stick to dynamic sizing for safety unless dry run?
            # Existing logic calculated size based on trigger price.

            # Special case for "TEST_TRADE" strategy logic which might pass a fixed size?
            # We'll assume dynamic sizing is preferred for production safety.
            size = self._calculate_size(trigger_price, adjusted_sl)

            # Check for abort condition (Size too small)
            if size <= 0:
                logger.warning("Trade Aborted: Size is 0 (Risk/Floor limits reached).")
                if row_id:
                    self.logger_db.update_trade_status(
                        row_id=row_id,
                        outcome="ABORTED_RISK",
                        deal_id=None,
                    )
                return False

            take_profit_level = plan.take_profit
            if plan.use_trailing_stop:
                take_profit_level = None

            if dry_run:
                logger.info(
                    f"DRY RUN: {direction} {size} {plan.ticker} at {trigger_price} (Stop: {adjusted_sl})"
                )
                outcome = "DRY_RUN_PLACED"
            else:
                confirmation = self.client.place_spread_bet_order(
                    epic=plan.ticker,
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

            if row_id:
                self.logger_db.update_trade_status(
                    row_id=row_id,
                    outcome=outcome,
                    deal_id=deal_id,
                    size=size,
                    entry=execution_price,
                    stop_loss=adjusted_sl,
                )

            if not dry_run and deal_id:
                self.monitor.monitor_trade(
                    deal_id,
                    plan.ticker,
                    entry_price=execution_price,
                    stop_loss=adjusted_sl,
                    atr=plan.atr,
                    use_trailing_stop=plan.use_trailing_stop,
                )

            return True

        except Exception as e:
            logger.error(f"Failed to execute trade: {e}")
            return False

    def _calculate_size(self, entry: float, stop_loss: float) -> float:
        try:
            all_accounts = self.client.get_account_info()
            balance = 0.0
            available = 0.0

            if self.client.service.account_id and isinstance(
                all_accounts, pd.DataFrame
            ):
                target_account_df = all_accounts[
                    all_accounts["accountId"] == self.client.service.account_id
                ]
                if not target_account_df.empty:
                    # 'balance' is the cash value. 'available' is equity minus margin.
                    balance = float(target_account_df.iloc[0].get("balance", 0))
                    available = float(target_account_df.iloc[0].get("available", 0))
                else:
                    logger.error(
                        "Could not find target account in account list. Aborting."
                    )
                    return 0.0
            elif isinstance(all_accounts, dict) and "accounts" in all_accounts:
                for acc in all_accounts["accounts"]:
                    if acc.get("accountId") == self.client.service.account_id:
                        balance = float(
                            acc.get("balance", {}).get("available", 0)
                            if isinstance(acc.get("balance"), dict)
                            else acc.get("balance", 0)
                        )
                        available = float(acc.get("available", 0))
                        break

            # 1. Broker Liquidity Check
            if available <= 0:
                logger.warning(
                    f"Aborting: No available funds ({available}) for margin."
                )
                return 0.0

            if balance <= 0:
                return 0.0

            stop_distance = abs(entry - stop_loss)
            if stop_distance <= 0:
                return 0.0

            # 2. Calculate Standard Risk Size (Unclamped)
            target_risk_amount = balance * RISK_PER_TRADE_PERCENT * self.risk_scale
            standard_size = round(target_risk_amount / stop_distance, 2)

            # 3. Check if Standard Trade is safe for the Floor (Using Cash Balance)
            if (
                MIN_ACCOUNT_BALANCE <= 0
                or (balance - target_risk_amount) >= MIN_ACCOUNT_BALANCE
            ):
                # Standard is safe. But we must trade at least broker min.
                effective_size = max(standard_size, self.min_size)

                # RE-CHECK safety of the broker min against Cash Balance
                if (balance - (effective_size * stop_distance)) >= MIN_ACCOUNT_BALANCE:
                    logger.info(
                        f"Dynamic Sizing: Balance={balance}, Available={available}, Size={effective_size} (Standard/Min)"
                    )
                    return effective_size

            # 4. Standard Trade Breaches Floor. Try Min.
            logger.warning(
                f"Standard Risk would breach Floor (£{MIN_ACCOUNT_BALANCE}) based on Cash Balance. "
                "Attempting step-down to Broker Minimum."
            )

            min_risk = self.min_size * stop_distance
            if (balance - min_risk) >= MIN_ACCOUNT_BALANCE:
                logger.info(
                    f"Dynamic Sizing: Balance={balance}, Size={self.min_size} (Step-Down to Min)"
                )
                return self.min_size

            # 5. Even Broker Minimum breaches the floor.
            logger.warning(
                f"Even Broker Minimum Risk (£{min_risk:.2f}) would breach Floor (£{MIN_ACCOUNT_BALANCE}). "
                "Aborting trade."
            )
            return 0.0

        except Exception as e:
            logger.error(f"Error calculating size: {e}. Defaulting to 0.0")
            return 0.0
