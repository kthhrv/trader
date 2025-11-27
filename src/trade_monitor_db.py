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

    def monitor_trade(self, deal_id: str, epic: str, polling_interval: int = None, max_duration: int = 14400):
        """
        Monitors an active trade by polling position status.
        Updates the trade_log table when the trade is closed or max_duration expires.
        max_duration default: 4 hours (14400 seconds).
        """
        if polling_interval is None:
            polling_interval = self.polling_interval

        logger.info(f"Starting DB monitoring for Deal ID: {deal_id} (Poll: {polling_interval}s)")
        
        start_time = time.time()
        active = True
        
        while active:
            if time.time() - start_time > max_duration:
                logger.warning(f"Monitoring timed out for Deal ID: {deal_id} after {max_duration} seconds.")
                break

            try:
                # 1. Check Position Status
                position = self.client.fetch_open_position_by_deal_id(deal_id)
                
                # Debug log to investigate potential hanging
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Fetch position result for {deal_id}: {position}")
                
                status = "OPEN"
                pnl = 0.0
                exit_price = 0.0
                
                if position:
                    # Still open, just continue polling
                    pass
                else:
                    # Position not found -> Closed
                    status = "CLOSED"
                    active = False
                    
                    # Attempt to fetch realized PnL and exit details from history
                    try:
                        # Fetch recent history
                        history_df = self.client.fetch_transaction_history_by_deal_id(deal_id)
                        if history_df is not None and not history_df.empty:
                            if 'profitAndLoss' in history_df.columns:
                                val_str = str(history_df.iloc[0]['profitAndLoss']).replace('£', '').replace(',', '')
                                pnl = float(val_str)
                                logger.info(f"Fetched realized PnL from history: {pnl}")
                            
                            # Try to get exit price if available in history columns
                            # For now, we might not have it easily, so we can default to 0 or try to parse
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