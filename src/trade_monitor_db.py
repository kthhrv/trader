import logging
import time
from datetime import datetime
from src.ig_client import IGClient
from src.database import update_trade_outcome
from src.stream_manager import StreamManager # New import
import threading # New import
import json # New import

logger = logging.getLogger(__name__)

class TradeMonitorDB:
    def __init__(self, client: IGClient, stream_manager: StreamManager, db_path=None, polling_interval: int = 5):
        self.client = client
        self.stream_manager = stream_manager
        self.db_path = db_path
        self.polling_interval = polling_interval # Polling interval for trailing stops if needed
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
        trade_status = payload.get('status') # CLOSED, OPEN etc.

        if not deal_id or deal_id not in self._active_monitors:
            logger.debug(f"Received trade update for unmonitored deal ID {deal_id} or no deal ID: {payload}")
            return

        # Handle closure event
        if trade_status == "CLOSED":
            logger.info(f"STREAM: Trade {deal_id} detected as CLOSED via streaming update.")
            
            # Extract PnL and exit price from payload if available
            pnl = float(payload.get('profitAndLoss', 0.0)) # Example, adjust key as per actual payload
            exit_price = float(payload.get('level', 0.0)) # 'level' or 'closeLevel' in confirms

            # Signal that monitoring can stop for this trade
            self._active_monitors[deal_id].set()

            # The main monitor_trade loop will pick up from here, log to DB and clean up.
            # We don't update DB directly here, but let the blocking monitor_trade do it
            # to ensure proper flow and avoid race conditions with its state.

        elif trade_status == "UPDATED":
            logger.info(f"STREAM: Trade {deal_id} detected as UPDATED via streaming update.")
            # This could be a stop/limit update. Trailing stop logic handles this.
            # No need to set the event, trade is still active.
        else:
            logger.debug(f"STREAM: Unhandled trade status for {deal_id}: {trade_status} - {payload}")


    def monitor_trade(self, deal_id: str, epic: str, entry_price: float = None, stop_loss: float = None, atr: float = None, polling_interval: int = None, max_duration: int = 14400, use_trailing_stop: bool = True):
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
        
        start_time = time.time()
        moved_to_breakeven = False
        risk_distance = abs(entry_price - stop_loss) if (entry_price and stop_loss) else 0

        try:
            while not trade_closure_event.is_set():
                if time.time() - start_time > max_duration:
                    logger.warning(f"Monitoring timed out for Deal ID: {deal_id} after {max_duration} seconds.")
                    break # Exit loop on timeout

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
            
            # --- Trade has closed (either by stream event or timeout) ---
            final_status = "CLOSED"
            final_pnl = 0.0
            final_exit_price = 0.0
            
            # Attempt to fetch final details from history if needed, or from the streamed payload
            # For now, we fetch from history as stream payload might not contain full details.
            try:
                history_df = self.client.fetch_transaction_history_by_deal_id(deal_id)
                if history_df is not None and not history_df.empty:
                    if 'profitAndLoss' in history_df.columns:
                        val_str = str(history_df.iloc[0]['profitAndLoss']).replace('£', '').replace(',', '')
                        final_pnl = float(val_str)
                        logger.info(f"Fetched realized PnL from history: {final_pnl}")
                    
                    if 'closeLevel' in history_df.columns:
                        final_exit_price = float(history_df.iloc[0]['closeLevel'])
            except Exception as e:
                logger.warning(f"Could not fetch realized PnL from history for {deal_id}: {e}")

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