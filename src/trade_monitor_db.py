import logging
import time
from datetime import datetime
from src.ig_client import IGClient
from src.database import get_db_connection

logger = logging.getLogger(__name__)

class TradeMonitorDB:
    def __init__(self, client: IGClient, db_path=None, polling_interval: int = 5):
        self.client = client
        self.db_path = db_path
        self.polling_interval = polling_interval

    def monitor_trade(self, deal_id: str, epic: str, polling_interval: int = None, max_duration: int = 14400):
        """
        Monitors an active trade by polling position status and market price.
        Logs data to the SQLite database until the trade is closed or max_duration expires.
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
                current_bid = 0.0
                current_offer = 0.0
                
                if position:
                    # Parse position data
                    pnl = float(position.get('profitAndLoss', 0.0))
                    current_bid = float(position.get('bid', 0.0))
                    current_offer = float(position.get('offer', 0.0))
                    
                    if current_bid == 0 and current_offer == 0:
                        # Fallback to market snapshot
                        market = self.client.get_market_info(epic)
                        if market and 'snapshot' in market:
                            current_bid = float(market['snapshot'].get('bid', 0.0))
                            current_offer = float(market['snapshot'].get('offer', 0.0))
                else:
                    # Position not found -> Closed
                    status = "CLOSED"
                    active = False
                    
                    # Attempt to fetch realized PnL from history
                    try:
                        # Fetch recent history
                        history_df = self.client.fetch_transaction_history_by_deal_id(deal_id)
                        if history_df is not None and not history_df.empty:
                            # Filter for 'DEAL' type and matching instrument/date if possible
                            # For now, we assume the most recent 'DEAL' closing transaction for this epic is ours
                            # This is a heuristic; perfect matching requires parsing the 'reference' field deeper or more API calls
                            # Sort by date descending
                            if 'date' in history_df.columns:
                                history_df = history_df.sort_values('date', ascending=False)
                            
                            # Look for the first record with a non-zero PnL for this epic (if column available)
                            # Columns usually: date, instrumentName, period, profitAndLoss, transactionType, reference, openDateUtc...
                            
                            # Check if we can filter by epic/instrumentName
                            # history_df['instrumentName'] might contain the epic or name
                            
                            # Take the first record's PnL as the "realized" PnL for now
                            # This assumes the bot is monitoring one active trade per instrument mostly
                            if 'profitAndLoss' in history_df.columns:
                                potential_pnl = float(history_df.iloc[0]['profitAndLoss'].replace('£', '').replace(',', '')) # Clean string currency
                                pnl = potential_pnl
                                logger.info(f"Fetched realized PnL from history: {pnl}")
                    except Exception as e:
                        logger.warning(f"Could not fetch realized PnL from history: {e}")

                    # Fetch one last price for closure record
                    market = self.client.get_market_info(epic)
                    if market and 'snapshot' in market:
                        current_bid = float(market['snapshot'].get('bid', 0.0))
                        current_offer = float(market['snapshot'].get('offer', 0.0))
                
                # 2. Log Data to DB
                self._log_to_db(deal_id, current_bid, current_offer, pnl, status)
                
                if active:
                    time.sleep(polling_interval)
                    
            except Exception as e:
                logger.error(f"Error during trade monitoring: {e}")
                time.sleep(polling_interval) # Wait before retry

        logger.info(f"Trade {deal_id} CLOSED. Monitoring finished.")

    def _log_to_db(self, deal_id, bid, offer, pnl, status):
        try:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Logging to DB: {self.db_path} | Deal: {deal_id} Status: {status}")
                
            conn = get_db_connection(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO trade_monitor (deal_id, timestamp, bid, offer, pnl, status)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                deal_id,
                datetime.now().isoformat(),
                bid,
                offer,
                pnl,
                status
            ))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to log monitor data to DB: {e}")
