import logging
from datetime import datetime
from src.gemini_analyst import TradingSignal, Action
from src.database import get_db_connection, init_db

logger = logging.getLogger(__name__)

class TradeLoggerDB:
    def __init__(self, db_path=None):
        self.db_path = db_path
        init_db(self.db_path) # Ensure DB exists

    def log_trade(self,
                   epic: str,
                   plan: TradingSignal,
                   outcome: str,
                   spread_at_entry: float,
                   is_dry_run: bool,
                   deal_id: str = None):
        """
        Logs the details of a trade to the SQLite database.
        """
        timestamp = datetime.now().isoformat()
        
        try:
            conn = get_db_connection(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO trade_log (
                    timestamp, epic, action, entry, stop_loss, take_profit,
                    size, outcome, reasoning, confidence, spread_at_entry,
                    atr, is_dry_run, deal_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                timestamp,
                epic,
                plan.action.value if isinstance(plan.action, Action) else str(plan.action),
                plan.entry,
                plan.stop_loss,
                plan.take_profit,
                plan.size,
                outcome,
                plan.reasoning,
                plan.confidence,
                spread_at_entry,
                plan.atr,
                is_dry_run,
                deal_id
            ))
            
            conn.commit()
            conn.close()
            logger.info(f"Logged trade for {epic} with outcome: {outcome} (Deal ID: {deal_id})")
            
        except Exception as e:
            logger.error(f"Failed to log trade to DB for {epic}: {e}")
