import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional
from src.ig_client import IGClient
from src.database import update_trade_outcome
from src.stream_manager import StreamManager # New import
from src.market_status import MarketStatus
import threading # New import
import json # New import

logger = logging.getLogger(__name__)

class TradeMonitorDB:
    def __init__(self, client: IGClient, stream_manager: StreamManager, db_path=None, polling_interval: int = 5, market_status: Optional[MarketStatus] = None):
        self.client = client
        self.stream_manager = stream_manager
        self.db_path = db_path
        self.polling_interval = polling_interval # Polling interval for trailing stops if needed
        self.market_status = market_status if market_status else MarketStatus()
        self._active_monitors: Dict[str, threading.Event] = {} # {deal_id: threading.Event}
        self._is_subscribed_to_trade_updates = False


    def _handle_trade_update(self, data: dict):
        """
        Callback method to process incoming trade updates from the StreamManager.
        This method will look for closure events for trades it is actively monitoring.
        """
        if data.get('type') != "trade_update":
            return # Not a trade update

        # Parse the payload. It can be CONFIRMS or OPU. Both are JSON strings.
        payload_str = data.get('payload')
        if not payload_str:
            logger.warning(f"Trade update received without payload: {data}")
            return
        
        try:
            payload = json.loads(payload_str)
        except json.JSONDecodeError:
            logger.warning(f"Failed to decode trade update payload JSON: {payload_str}")
            return

        # Check for OPU (Open Position Update)
        # OPU can indicate closure by 'status' or lack of position.
        # CONFIRMS contains 'dealStatus' (ACCEPTED/REJECTED/DEALT) and 'status' (CLOSED/OPEN).
        
        # Prioritize 'CONFIRMS' for final closure status if available
        # CONFIRMS example: {"dealId": "...", "dealStatus": "DEALT", "status": "CLOSED", ...}
        # OPU example: {"dealId": "...", "status": "CLOSED", ...}
        
        deal_id = payload.get('dealId')
        affected_deal_id = payload.get('affectedDealId')
        trade_status = payload.get('status') # CLOSED, OPEN etc.
        deal_status = payload.get('dealStatus') # ACCEPTED, REJECTED etc (from CONFIRMS)

        # Determine which ID matches our monitored list
        monitored_id = None
        if deal_id and deal_id in self._active_monitors:
            monitored_id = deal_id
        elif affected_deal_id and affected_deal_id in self._active_monitors:
            monitored_id = affected_deal_id

        logger.info(f"DEBUG: Trade Update. Deal: {deal_id}, Affected: {affected_deal_id}, Status: {trade_status}, DealStatus: {deal_status}, MonitoredID: {monitored_id}")

        if not monitored_id:
            logger.info(f"Received trade update for unmonitored deal ID {deal_id} or no deal ID.")
            return

        # Handle closure event
        # 1. Standard OPU with status="CLOSED" or "DELETED" (Position removed)
        if trade_status == "CLOSED" or trade_status == "DELETED":
            logger.info(f"STREAM: Trade {monitored_id} detected as {trade_status} via streaming update (OPU/CONFIRMS).")
            self._active_monitors[monitored_id].set()

        # 2. CONFIRMS for a closing deal (dealStatus=ACCEPTED, status=CLOSED usually, but let's check direction/size implies close?)
        # Actually, if we matched via affected_deal_id, it implies this new deal is acting on our monitored deal.
        # If it's a closing order that was ACCEPTED, does that mean it's closed?
        # A closing order might be partial.
        # But usually 'status' in CONFIRMS will say "CLOSED" if it fully closed the position?
        # Let's rely on 'status' == 'CLOSED' which appears in both OPU (Position Closed) and CONFIRMS (Trade Closed).
        
        elif deal_status == "ACCEPTED" and trade_status == "CLOSED":
             logger.info(f"STREAM: Trade {monitored_id} detected as CLOSED via CONFIRMS (AffectedDeal).")
             self._active_monitors[monitored_id].set()


        elif trade_status == "UPDATED":
            logger.info(f"STREAM: Trade {deal_id} detected as UPDATED via streaming update.")
            # This could be a stop/limit update. Trailing stop logic handles this.
            # No need to set the event, trade is still active.

        else:
            logger.debug(f"STREAM: Unhandled trade status for {deal_id}: {trade_status} - {payload}")



    def monitor_trade(self, deal_id: str, epic: str, entry_price: float = None, stop_loss: float = None, atr: float = None, polling_interval: int = None, use_trailing_stop: bool = True):
        """
        Monitors an active trade, manages stops (Breakeven/Trailing), and logs outcome.
        This version uses streaming updates for trade closure and polling for trailing stops.
        """
        if polling_interval is None:
            polling_interval = self.polling_interval

        logger.info(f"Starting Monitor & Manage for Deal {deal_id}. Risk: {entry_price}->{stop_loss}, ATR: {atr}, Trailing: {use_trailing_stop}")
        
        # Ensure stream manager is subscribed to trade updates
        if not self._is_subscribed_to_trade_updates:
            self.stream_manager.subscribe_trade_updates(self._handle_trade_update)
            self._is_subscribed_to_trade_updates = True

        # Setup event to signal trade closure
        trade_closure_event = threading.Event()
        self._active_monitors[deal_id] = trade_closure_event
        
        moved_to_breakeven = False
        risk_distance = abs(entry_price - stop_loss) if (entry_price and stop_loss) else 0

        # Market Close Check Setup
        try:
            market_close_dt = self.market_status.get_market_close_datetime(epic)
            logger.info(f"Market Close time for {epic}: {market_close_dt}")
        except Exception as e:
            logger.warning(f"Could not determine market close time for {epic}: {e}")
            market_close_dt = None

        try:
            while not trade_closure_event.is_set():
                # --- Market Close Time Check ---
                if market_close_dt:
                    now_tz = datetime.now(market_close_dt.tzinfo)
                    time_to_close = market_close_dt - now_tz
                    
                    # If within 15 minutes (900 seconds) of close
                    if timedelta(seconds=0) < time_to_close < timedelta(minutes=15):
                        logger.warning(f"Market for {epic} closing in {time_to_close}. Forcing exit for {deal_id}.")
                        try:
                            # 1. Fetch current position to get size and direction
                            pos = self.client.fetch_open_position_by_deal_id(deal_id)
                            if pos:
                                direction = pos.get('direction')
                                size = float(pos.get('size', 0))
                                expiry = pos.get('expiry', 'DFB')
                                
                                # Invert direction for closing
                                close_direction = "SELL" if direction == "BUY" else "BUY"
                                
                                # 2. Execute Market Close
                                # Use epic=None and expiry=None to avoid mutual-exclusive error in IG API
                                self.client.close_open_position(deal_id, close_direction, size, epic=None, expiry=None)
                                
                                # Wait a moment for the stream to pick up the close event
                                time.sleep(2)
                                if trade_closure_event.is_set():
                                    break
                            else:
                                logger.warning(f"Could not fetch position {deal_id} for forced exit. It may be already closed.")
                                break # Assume closed if not found
                        except Exception as e:
                            logger.error(f"Failed to force close trade {deal_id} near market close: {e}")
                # --- End Market Close Check ---

                # --- Polling for Trailing Stop Logic (Still active in this loop) ---
                if use_trailing_stop and risk_distance > 0:
                    try:
                        position = self.client.fetch_open_position_by_deal_id(deal_id)
                        if position:
                            direction = position.get('direction')
                            current_bid = float(position.get('bid', 0.0))
                            current_offer = float(position.get('offer', 0.0))
                            current_stop = float(position.get('stopLevel', 0.0))
                            
                            current_price = current_bid if direction == 'BUY' else current_offer
                            profit_dist = (current_price - entry_price) if direction == 'BUY' else (entry_price - current_price)
                            
                            # Rule 1: Breakeven at 1.5R
                            if not moved_to_breakeven and profit_dist >= (1.5 * risk_distance):
                                new_stop = entry_price 
                                if (direction == 'BUY' and new_stop > current_stop) or (direction == 'SELL' and new_stop < current_stop):
                                    logger.info(f"Moving Stop to BREAKEVEN for {deal_id}")
                                    self.client.update_open_position(deal_id, stop_level=new_stop)
                                    moved_to_breakeven = True
                                    current_stop = new_stop # Update local tracker

                            # Rule 2: Dynamic Trailing (ATR based)
                            if profit_dist >= (1.5 * risk_distance): # Only trail if already in profit beyond breakeven
                                if atr and atr > 0:
                                    trail_dist = 2.0 * atr
                                else:
                                    trail_dist = 1.0 * risk_distance
                                
                                new_stop = (current_price - trail_dist) if direction == 'BUY' else (current_price + trail_dist)
                                
                                if (direction == 'BUY' and new_stop > current_stop) or (direction == 'SELL' and new_stop < current_stop):
                                    logger.info(f"Trailing Stop for {deal_id} to {new_stop}")
                                    self.client.update_open_position(deal_id, stop_level=new_stop)
                                    # moved_to_breakeven is implicitly true here
                        # else: Position not found via polling. This might mean it closed and stream update is delayed.

                    except Exception as e:
                        logger.error(f"Error during trailing stop management: {e}")
                # --- End Trailing Stop Logic ---

                # Wait for next check or until event is set
                trade_closure_event.wait(polling_interval)
            
            # --- Trade has closed ---
            final_status = "CLOSED"
            final_pnl = 0.0
            final_exit_price = 0.0
            
            # Attempt to fetch final details from history with retries.
            for attempt in range(3):
                # Give the IG backend a moment to index the transaction
                time.sleep(2)
                
                try:
                    history_df = self.client.fetch_transaction_history_by_deal_id(deal_id)
                    if history_df is not None and not history_df.empty:
                        # We assume the most recent transaction (index 0) is the close.
                        latest_tx = history_df.iloc[0]
                        
                        # Extract PnL
                        pnl_raw = latest_tx.get('profitAndLoss', '0')
                        val_str = str(pnl_raw).replace('£', '').replace(',', '')
                        try:
                            current_pnl = float(val_str)
                        except ValueError:
                            current_pnl = 0.0
                        
                        final_pnl = current_pnl
                        
                        if 'closeLevel' in latest_tx:
                            final_exit_price = float(latest_tx['closeLevel'])
                        elif 'level' in latest_tx: # Fallback
                                final_exit_price = float(latest_tx['level'])

                        logger.info(f"History Fetch Attempt {attempt+1}: Found PnL={final_pnl}, Exit={final_exit_price}")

                        if final_pnl != 0.0:
                            break
                except Exception as e:
                    logger.warning(f"History fetch attempt {attempt+1} failed for {deal_id}: {e}")

            # Update the main trade log with the outcome
            self._update_db(deal_id, final_exit_price, final_pnl, datetime.now().isoformat(), final_status)

        except Exception as e:
            logger.error(f"Error during trade monitoring: {e}")
        finally:
            # Clean up active monitor
            if deal_id in self._active_monitors:
                del self._active_monitors[deal_id]
            logger.info(f"Trade {deal_id} CLOSED. Monitoring finished.")

    def _update_db(self, deal_id, exit_price, pnl, exit_time, status):
        """
        Updates the trade_log with closure details.
        """
        try:
            update_trade_outcome(
                deal_id, 
                exit_price, 
                pnl, 
                exit_time, 
                outcome=status, 
                db_path=self.db_path
            )
        except Exception as e:
            logger.error(f"Failed to update trade outcome in DB: {e}")