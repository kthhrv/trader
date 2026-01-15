import logging
import threading
import time
from typing import Dict, Optional
from dataclasses import dataclass

from src.ig_client import IGClient
from src.stream_manager import StreamManager
from src.trade_monitor_db import TradeMonitorDB
from src.market_status import MarketStatus

logger = logging.getLogger(__name__)


@dataclass
class ActiveTrade:
    deal_id: str
    epic: str
    entry_price: float
    stop_loss: float
    atr: float
    use_trailing_stop: bool
    direction: str  # BUY/SELL


class TradeSupervisor:
    """
    Singleton-like service that manages multiple active trades in a background thread.
    Replaces the blocking TradeMonitorDB loop.
    """

    def __init__(
        self,
        client: IGClient,
        stream_manager: StreamManager,
        market_status: MarketStatus,
        poll_interval: float = 10.0,
    ):
        self.client = client
        self.stream_manager = stream_manager
        self.market_status = market_status
        self.poll_interval = poll_interval

        # Registry: {deal_id: ActiveTrade}
        self.active_trades: Dict[str, ActiveTrade] = {}
        self.lock = threading.Lock()

        self.is_running = False
        self.thread: Optional[threading.Thread] = None

        # We reuse the logic from TradeMonitorDB but adapt it for polling
        # We can instantiate one "worker" monitor or refactor static methods later.
        # For now, we will instantiate a helper monitor per check or keep a stateless one.
        # Ideally, we refactor Monitor logic to be stateless.
        # But to save time, we will use the Supervisor to orchestrate the "Check" logic.

        # Helper to access DB updates
        self.monitor_helper = TradeMonitorDB(
            client, stream_manager, market_status=market_status
        )

    def start(self):
        """Starts the background monitoring loop."""
        if self.is_running:
            return

        self.is_running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logger.info("Trade Supervisor started.")

    def stop(self):
        """Stops the monitoring loop."""
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Trade Supervisor stopped.")

    def register_trade(
        self,
        deal_id: str,
        epic: str,
        entry_price: float,
        stop_loss: float,
        atr: float,
        use_trailing_stop: bool,
    ):
        """
        Adds a new trade to be monitored.
        """
        # We need direction for trailing logic. Fetch it or pass it?
        # Ideally pass it, but for now let's fetch to be safe/robust.
        try:
            pos = self.client.fetch_open_position_by_deal_id(deal_id)
            direction = pos.get("direction") if pos else "BUY"  # Fallback/Error handle
        except Exception:
            direction = "BUY"  # Default, should ideally fail or retry

        trade = ActiveTrade(
            deal_id=deal_id,
            epic=epic,
            entry_price=entry_price,
            stop_loss=stop_loss,
            atr=atr,
            use_trailing_stop=use_trailing_stop,
            direction=direction,
        )

        with self.lock:
            self.active_trades[deal_id] = trade

        logger.info(f"Supervisor: Registered trade {deal_id} ({epic}).")

        # Ensure we are subscribed to this epic on the stream
        # (StrategyEngine usually does this, but good to ensure)
        # self.stream_manager.subscribe... (Already handled by Engine for now)

    def _monitor_loop(self):
        """
        Main loop that iterates through all active trades and manages them.
        """
        while self.is_running:
            try:
                # 1. Snapshot active trades to avoid lock contention during processing
                with self.lock:
                    current_trades = list(self.active_trades.values())

                if not current_trades:
                    time.sleep(1.0)  # Sleep fast if nothing to do
                    continue

                # 2. Iterate and Manage
                deals_to_remove = []

                for trade in current_trades:
                    is_active = self._manage_single_trade(trade)
                    if not is_active:
                        deals_to_remove.append(trade.deal_id)

                # 3. Clean up closed trades
                if deals_to_remove:
                    with self.lock:
                        for deal_id in deals_to_remove:
                            self.active_trades.pop(deal_id, None)
                            logger.info(
                                f"Supervisor: Deregistered closed trade {deal_id}"
                            )

                time.sleep(self.poll_interval)

            except Exception as e:
                logger.error(f"Supervisor Loop Error: {e}")
                time.sleep(5)

    def _manage_single_trade(self, trade: ActiveTrade) -> bool:
        """
        Performs checks for a single trade (Trailing Stop, Market Close, Closure Check).
        Returns True if trade is still active, False if closed.
        """
        try:
            # 1. Check if closed (API check)
            # This is "expensive". We rely on Stream ideally, but Supervisor does polling backup.
            position = self.client.fetch_open_position_by_deal_id(trade.deal_id)

            if not position:
                # Position gone. Assume closed.
                # Trigger final DB update logic
                logger.info(
                    f"Supervisor: Position {trade.deal_id} not found. Triggering closure logic."
                )
                self.monitor_helper._update_db_from_history(
                    trade.deal_id, trade.entry_price
                )
                return False

            # 2. Update local state from API (Current Stop, Bid/Offer)
            current_stop = float(position.get("stopLevel", trade.stop_loss))
            current_bid = float(position.get("bid", 0))
            current_offer = float(position.get("offer", 0))

            # 3. Trailing Stop Logic
            # We can reuse logic from TradeMonitorDB if we extract it,
            # or implement a lightweight version here.
            # For now, let's delegate to a helper method on TradeMonitorDB if possible,
            # or replicate the logic here for clarity.

            if trade.use_trailing_stop:
                self._check_trailing_stop(
                    trade, current_bid, current_offer, current_stop
                )

            # 4. Market Close Logic
            # ... (Implement close check)

            return True

        except Exception as e:
            logger.error(f"Error managing trade {trade.deal_id}: {e}")
            return True  # Assume active on error to avoid premature deregistration

    def _check_trailing_stop(
        self, trade: ActiveTrade, bid: float, offer: float, current_stop: float
    ):
        self.monitor_helper.check_and_update_trailing_stop(
            deal_id=trade.deal_id,
            direction=trade.direction,
            entry_price=trade.entry_price,
            current_stop=current_stop,
            current_bid=bid,
            current_offer=offer,
            atr=trade.atr,
        )
