import logging
import time
from datetime import datetime
from src.ig_client import IGClient
from src.database import update_trade_outcome

logger = logging.getLogger(__name__)

class TradeMonitorDB:
    def __init__(self, client: IGClient, db_path=None, polling_interval: int = 5):
        self.client = client
        self.db_path = db_path
        self.polling_interval = polling_interval

    def monitor_trade(self, deal_id: str, epic: str, entry_price: float = None, stop_loss: float = None, atr: float = None, polling_interval: int = None, max_duration: int = 14400, use_trailing_stop: bool = True):
        """
        Monitors an active trade, manages stops (Breakeven/Trailing), and logs outcome.
        """
        if polling_interval is None:
            polling_interval = self.polling_interval

        logger.info(f"Starting Monitor & Manage for Deal {deal_id}. Risk: {entry_price}->{stop_loss}, ATR: {atr}, Trailing: {use_trailing_stop}")
        
        start_time = time.time()
        active = True
        
        # State for management
        risk_distance = abs(entry_price - stop_loss) if (entry_price and stop_loss) else 0
        moved_to_breakeven = False
        
        while active:
            if time.time() - start_time > max_duration:
                logger.warning(f"Monitoring timed out for Deal ID: {deal_id} after {max_duration} seconds.")
                break

            try:
                # 1. Check Position Status
                position = self.client.fetch_open_position_by_deal_id(deal_id)
                
                status = "OPEN"
                pnl = 0.0
                exit_price = 0.0
                
                if position:
                    # Parse position data
                    direction = position.get('direction') # 'BUY' or 'SELL'
                    current_bid = float(position.get('bid', 0.0))
                    current_offer = float(position.get('offer', 0.0))
                    current_stop = float(position.get('stopLevel', 0.0)) # May be None if guaranteedStop used? No, standard stop.
                    
                    # --- Active Trade Management ---
                    if use_trailing_stop and risk_distance > 0 and direction:
                        current_price = current_bid if direction == 'BUY' else current_offer # Conservative price
                        profit_dist = (current_price - entry_price) if direction == 'BUY' else (entry_price - current_price)
                        
                        # Rule 1: Breakeven at 1.5R (Avoid 1R trap)
                        if not moved_to_breakeven and profit_dist >= (1.5 * risk_distance):
                            new_stop = entry_price 
                            if (direction == 'BUY' and new_stop > current_stop) or (direction == 'SELL' and new_stop < current_stop):
                                logger.info(f"Moving Stop to BREAKEVEN for {deal_id}")
                                self.client.update_open_position(deal_id, stop_level=new_stop)
                                moved_to_breakeven = True
                                current_stop = new_stop # Update local tracker

                        # Rule 2: Dynamic Trailing (ATR based)
                        # Start trailing once we are deep in profit (>= 1.5R)
                        if profit_dist >= (1.5 * risk_distance):
                            # Calculate Trail Distance: Prefer 2.0 * ATR, fallback to 1.0 * Risk
                            if atr and atr > 0:
                                trail_dist = 2.0 * atr
                            else:
                                trail_dist = 1.0 * risk_distance
                            
                            new_stop = (current_price - trail_dist) if direction == 'BUY' else (current_price + trail_dist)
                            
                            # Only move if it reduces risk (higher for BUY, lower for SELL)
                            if (direction == 'BUY' and new_stop > current_stop) or (direction == 'SELL' and new_stop < current_stop):
                                logger.info(f"Trailing Stop for {deal_id} to {new_stop}")
                                self.client.update_open_position(deal_id, stop_level=new_stop)
                                moved_to_breakeven = True # Implicitly
                    # -------------------------------
                    
                else:
                    # Position not found -> Closed
                    status = "CLOSED"
                    active = False
                    
                    # Attempt to fetch realized PnL and exit details from history
                    try:
                        history_df = self.client.fetch_transaction_history_by_deal_id(deal_id)
                        if history_df is not None and not history_df.empty:
                            if 'profitAndLoss' in history_df.columns:
                                val_str = str(history_df.iloc[0]['profitAndLoss']).replace('£', '').replace(',', '')
                                pnl = float(val_str)
                                logger.info(f"Fetched realized PnL from history: {pnl}")
                            
                            if 'closeLevel' in history_df.columns:
                                exit_price = float(history_df.iloc[0]['closeLevel'])
                    except Exception as e:
                        logger.warning(f"Could not fetch realized PnL from history: {e}")

                    # Update the main trade log with the outcome
                    self._update_db(deal_id, exit_price, pnl, datetime.now().isoformat(), status)
                
                if active:
                    time.sleep(polling_interval)
                    
            except Exception as e:
                logger.error(f"Error during trade monitoring: {e}")
                time.sleep(polling_interval) # Wait before retry

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