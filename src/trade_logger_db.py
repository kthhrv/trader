import logging
from datetime import datetime
from src.gemini_analyst import TradingSignal, Action
from src.database import get_db_connection, init_db

logger = logging.getLogger(__name__)


class TradeLoggerDB:
    def __init__(self, db_path=None):
        self.db_path = db_path
        init_db(self.db_path)  # Ensure DB exists

    def log_trade(
        self,
        epic: str,
        plan: TradingSignal,
        outcome: str,
        spread_at_entry: float,
        is_dry_run: bool,
        deal_id: str = None,
        entry_type: str = "UNKNOWN",
    ):
        """
        Logs the details of a trade to the SQLite database.
        """
        timestamp = datetime.now().isoformat()

        try:
            conn = get_db_connection(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO trade_log (
                    timestamp, epic, action, entry_type, entry, stop_loss, initial_stop_loss, take_profit,
                    size, outcome, reasoning, confidence, spread_at_entry,
                    atr, is_dry_run, deal_id, use_trailing_stop
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    timestamp,
                    epic,
                    plan.action.value
                    if isinstance(plan.action, Action)
                    else str(plan.action),
                    entry_type,
                    plan.entry,
                    plan.stop_loss,
                    plan.stop_loss,  # initial_stop_loss
                    plan.take_profit,
                    plan.size,
                    outcome,
                    plan.reasoning,
                    plan.confidence,
                    spread_at_entry,
                    plan.atr,
                    is_dry_run,
                    deal_id,
                    plan.use_trailing_stop,
                ),
            )

            conn.commit()
            row_id = cursor.lastrowid  # Get the ID of the inserted row
            conn.close()
            logger.info(
                f"Logged trade for {epic} with outcome: {outcome} (Deal ID: {deal_id}, Row ID: {row_id})"
            )
            return row_id

        except Exception as e:
            logger.error(f"Failed to log trade to DB for {epic}: {e}")
            return None

    def update_trade_status(
        self,
        row_id: int,
        outcome: str,
        deal_id: str = None,
        size: float = None,
        entry: float = None,
    ):
        """
        Updates the status/outcome of an existing trade log entry.
        Used to transition a trade from 'PENDING' to 'LIVE_PLACED' or 'TIMED_OUT'.
        """
        try:
            conn = get_db_connection(self.db_path)
            cursor = conn.cursor()

            updates = ["outcome = ?"]
            params = [outcome]

            if deal_id:
                updates.append("deal_id = ?")
                params.append(deal_id)

            if size is not None:
                updates.append("size = ?")
                params.append(size)

            if entry is not None:
                updates.append("entry = ?")
                params.append(entry)

            params.append(row_id)
            query = f"UPDATE trade_log SET {', '.join(updates)} WHERE id = ?"

            cursor.execute(query, params)

            conn.commit()
            conn.close()
            logger.info(
                f"Updated trade outcome for Row ID {row_id} to: {outcome} (Deal ID: {deal_id}, Size: {size}, Entry: {entry})"
            )

        except Exception as e:
            logger.error(f"Failed to update trade status for Row ID {row_id}: {e}")
