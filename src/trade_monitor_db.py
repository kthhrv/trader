import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional
from src.ig_client import IGClient
from src.database import (
    update_trade_outcome,
    update_trade_stop_loss,
    fetch_last_n_closed_trades,
)
from src.stream_manager import StreamManager
from src.market_status import MarketStatus
from src.notification_service import HomeAssistantNotifier
from config import CONSECUTIVE_LOSS_LIMIT, BREAKEVEN_TRIGGER_R
import threading
import json

logger = logging.getLogger(__name__)


class TradeMonitorDB:
    def __init__(
        self,
        client: IGClient,
        stream_manager: StreamManager,
        db_path=None,
        polling_interval: int = 10,
        market_status: Optional[MarketStatus] = None,
    ):
        self.client = client
        self.stream_manager = stream_manager
        self.db_path = db_path
        self.polling_interval = polling_interval
        self.market_status = market_status if market_status else MarketStatus()
        self.notifier = HomeAssistantNotifier()
        self._active_monitors: Dict[str, threading.Event] = {}
        self._is_subscribed_to_trade_updates = False

    def _handle_trade_update(self, data: dict):
        if data.get("type") != "trade_update":
            return
        payload_str = data.get("payload")
        if not payload_str:
            return
        try:
            payload = json.loads(payload_str)
        except json.JSONDecodeError:
            return
        deal_id = payload.get("dealId")
        affected_deal_id = payload.get("affectedDealId")
        trade_status = payload.get("status")
        deal_status = payload.get("dealStatus")
        monitored_id = None
        if deal_id and deal_id in self._active_monitors:
            monitored_id = deal_id
        elif affected_deal_id and affected_deal_id in self._active_monitors:
            monitored_id = affected_deal_id
        if not monitored_id:
            return
        if trade_status == "CLOSED" or trade_status == "DELETED":
            logger.info(f"STREAM: Trade {monitored_id} detected as {trade_status}.")
            self._active_monitors[monitored_id].set()
        elif deal_status == "ACCEPTED" and trade_status == "CLOSED":
            logger.info(
                f"STREAM: Trade {monitored_id} detected as CLOSED via CONFIRMS."
            )
            self._active_monitors[monitored_id].set()
        elif trade_status == "UPDATED":
            logger.info(
                f"STREAM: Trade {deal_id} detected as UPDATED via streaming update."
            )

    def monitor_trade(
        self,
        deal_id: str,
        epic: str,
        entry_price: float = None,
        stop_loss: float = None,
        atr: float = None,
        polling_interval: int = None,
        use_trailing_stop: bool = True,
    ):
        if polling_interval is None:
            polling_interval = self.polling_interval
        logger.info(f"Starting Monitor & Manage for Deal {deal_id}.")
        if not self._is_subscribed_to_trade_updates:
            self.stream_manager.subscribe_trade_updates(self._handle_trade_update)
            self._is_subscribed_to_trade_updates = True
        trade_closure_event = threading.Event()
        self._active_monitors[deal_id] = trade_closure_event
        risk_distance = (
            abs(entry_price - stop_loss) if (entry_price and stop_loss) else 0
        )
        try:
            market_close_dt = self.market_status.get_market_close_datetime(epic)
        except Exception:
            market_close_dt = None
        try:
            while not trade_closure_event.is_set():
                if market_close_dt:
                    now_tz = datetime.now(market_close_dt.tzinfo)
                    if (
                        timedelta(seconds=0)
                        < (market_close_dt - now_tz)
                        < timedelta(minutes=15)
                    ):
                        logger.warning(f"Market closing. Forcing exit for {deal_id}.")
                        pos = self.client.fetch_open_position_by_deal_id(deal_id)
                        if pos:
                            close_direction = (
                                "SELL" if pos.get("direction") == "BUY" else "BUY"
                            )
                            self.client.close_open_position(
                                deal_id=deal_id,
                                direction=close_direction,
                                size=float(pos.get("size", 0)),
                                epic=None,
                                expiry=None,
                            )
                            time.sleep(2)
                            if trade_closure_event.is_set():
                                break
                        else:
                            break
                if use_trailing_stop and risk_distance > 0:
                    position = self.client.fetch_open_position_by_deal_id(deal_id)
                    if position:
                        self.check_and_update_trailing_stop(
                            deal_id,
                            position.get("direction"),
                            entry_price,
                            float(position.get("stopLevel", 0.0)),
                            float(position.get("bid", 0.0)),
                            float(position.get("offer", 0.0)),
                            atr,
                            risk_distance,
                        )
                trade_closure_event.wait(polling_interval)
            self.handle_closure(deal_id, entry_price)
        except Exception as e:
            logger.error(f"Error during monitoring: {e}")
        finally:
            if deal_id in self._active_monitors:
                del self._active_monitors[deal_id]
            logger.info(f"Trade {deal_id} CLOSED. Monitoring finished.")

    def check_and_update_trailing_stop(
        self,
        deal_id: str,
        direction: str,
        entry_price: float,
        current_stop: float,
        current_bid: float,
        current_offer: float,
        atr: float = None,
        risk_distance: float = None,
    ) -> bool:
        try:
            current_price = current_bid if direction == "BUY" else current_offer
            profit_dist = (
                (current_price - entry_price)
                if direction == "BUY"
                else (entry_price - current_price)
            )
            trigger_level = (
                (risk_distance * BREAKEVEN_TRIGGER_R)
                if risk_distance
                else (atr * 1.5 if atr else 0)
            )
            new_stop = None
            if trigger_level > 0 and profit_dist >= trigger_level:
                if direction == "BUY" and current_stop < entry_price:
                    new_stop = entry_price
                elif direction == "SELL" and current_stop > entry_price:
                    new_stop = entry_price
            # ATR Trailing
            # Restore logic: Only trail if we have reached Breakeven (current_stop >= entry)
            # OR if the new proposed trail puts us at/above Breakeven?
            # Original code was strictly: if moved_to_breakeven.

            allow_trailing = False
            if direction == "BUY" and current_stop >= entry_price:
                allow_trailing = True
            elif direction == "SELL" and current_stop <= entry_price:
                allow_trailing = True

            if atr and atr > 0 and allow_trailing:
                trail_dist = 3.0 * atr
                potential_trail = (
                    (current_price - trail_dist)
                    if direction == "BUY"
                    else (current_price + trail_dist)
                )
                if direction == "BUY":
                    if potential_trail > (new_stop or current_stop):
                        new_stop = potential_trail
                elif direction == "SELL":
                    if potential_trail < (new_stop or current_stop):
                        new_stop = potential_trail
            if new_stop is not None:
                min_step = (0.1 * atr) if (atr and atr > 0) else 1.0
                if abs(new_stop - current_stop) >= min_step:
                    logger.info(f"Updating stop for {deal_id} to {new_stop}")
                    self.client.update_open_position(deal_id, stop_level=new_stop)
                    update_trade_stop_loss(deal_id, new_stop, self.db_path)
                    return True
            return False
        except Exception as e:
            logger.error(f"Error checking stop for {deal_id}: {e}")
            return False

    def handle_closure(self, deal_id: str, entry_price: float):
        self._update_db_from_history(deal_id, entry_price)

    def _update_db_from_history(self, deal_id: str, entry_price: float):
        final_pnl, final_exit = 0.0, 0.0
        for _ in range(10):
            time.sleep(5)
            try:
                history_df = self.client.fetch_transaction_history_by_deal_id(deal_id)
                if history_df is not None and not history_df.empty:
                    latest = history_df.iloc[0]
                    pnl_raw = (
                        str(latest.get("profitAndLoss", "0"))
                        .replace("£", "")
                        .replace(",", "")
                    )
                    final_pnl = float(pnl_raw)
                    final_exit = float(
                        latest.get("closeLevel") or latest.get("level", 0)
                    )
                    if (
                        entry_price
                        and abs(float(latest.get("openLevel", 0.0)) - entry_price) > 5.0
                    ):
                        continue
                    break
            except Exception:
                pass
        self._update_db(
            deal_id=deal_id,
            exit_price=final_exit,
            pnl=final_pnl,
            exit_time=datetime.now().isoformat(),
            outcome="CLOSED",
        )

    def _update_db(self, deal_id, exit_price, pnl, exit_time, outcome):
        try:
            update_trade_outcome(
                deal_id, exit_price, pnl, exit_time, outcome, self.db_path
            )
            last_trades = fetch_last_n_closed_trades(
                limit=CONSECUTIVE_LOSS_LIMIT, db_path=self.db_path
            )
            if len(last_trades) >= CONSECUTIVE_LOSS_LIMIT:
                losses = [t for t in last_trades if t.get("pnl", 0) < 0]
                if len(losses) == CONSECUTIVE_LOSS_LIMIT:
                    self.notifier.send_notification(
                        title="Consecutive Losses Alert",
                        message="Bot recorded consecutive losses.",
                        priority="high",
                    )
        except Exception as e:
            logger.error(f"Failed to update trade outcome: {e}")
