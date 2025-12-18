import sqlite3
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Make DB_PATH absolute and relative to the project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = str(PROJECT_ROOT / "data" / "trader.db")


def get_db_connection(db_path=None):
    """
    Establishes and returns a connection to the SQLite database.
    """
    path = db_path if db_path else DB_PATH
    # Ensure the data directory exists
    os.makedirs(os.path.dirname(path), exist_ok=True)

    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row  # Return rows as dictionary-like objects
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        raise


def init_db(db_path=None):
    """
    Initializes the database schema.
    """
    logger.info(f"Initializing database at: {db_path if db_path else DB_PATH}")
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    # Updated Table for Trade Logs (Consolidated)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trade_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            epic TEXT,
            action TEXT,
            entry_type TEXT,
            entry REAL,
            stop_loss REAL,
            take_profit REAL,
            size REAL,
            outcome TEXT,
            reasoning TEXT,
            confidence TEXT,
            spread_at_entry REAL,
            atr REAL,
            is_dry_run BOOLEAN,
            deal_id TEXT,
            exit_price REAL,
            pnl REAL,
            exit_time TEXT,
            post_mortem TEXT,
            use_trailing_stop BOOLEAN
        )
    """)

    # Check if 'entry_type' column exists (for migration)
    cursor.execute("PRAGMA table_info(trade_log)")
    columns = [info[1] for info in cursor.fetchall()]

    if "entry_type" not in columns:
        logger.info("Migrating database: Adding 'entry_type' column to 'trade_log'...")
        try:
            cursor.execute("ALTER TABLE trade_log ADD COLUMN entry_type TEXT")
            logger.info("Migration successful.")
        except Exception as e:
            logger.error(f"Migration failed: {e}")

    if "use_trailing_stop" not in columns:
        logger.info(
            "Migrating database: Adding 'use_trailing_stop' column to 'trade_log'..."
        )
        try:
            cursor.execute("ALTER TABLE trade_log ADD COLUMN use_trailing_stop BOOLEAN")
            logger.info("Migration successful.")
        except Exception as e:
            logger.error(f"Migration failed: {e}")

    conn.commit()
    conn.close()
    logger.info(f"Database initialized at {db_path if db_path else DB_PATH}")


def fetch_trade_data(deal_id: str, db_path=None):
    """
    Fetches complete data for a trade from trade_log by deal_id.
    Returns a dictionary with 'log' key containing the row data.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    try:
        # Fetch from trade_log
        cursor.execute("SELECT * FROM trade_log WHERE deal_id = ?", (deal_id,))
        trade_log = cursor.fetchone()

        if not trade_log:
            return None

        return {"log": dict(trade_log)}
    finally:
        conn.close()


def update_trade_outcome(
    deal_id: str,
    exit_price: float,
    pnl: float,
    exit_time: str,
    outcome: str,
    db_path=None,
):
    """
    Updates an existing trade log with exit details.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE trade_log 
            SET exit_price = ?, pnl = ?, exit_time = ?, outcome = ?
            WHERE deal_id = ?
        """,
            (exit_price, pnl, exit_time, outcome, deal_id),
        )
        conn.commit()
        logger.info(
            f"Updated trade outcome for {deal_id}: PnL={pnl}, Outcome={outcome}"
        )
    except Exception as e:
        logger.error(f"Failed to update trade outcome: {e}")
    finally:
        conn.close()


def save_post_mortem(deal_id: str, analysis: str, db_path=None):
    """
    Saves the post-mortem analysis to the trade_log table.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE trade_log SET post_mortem = ? WHERE deal_id = ?",
            (analysis, deal_id),
        )
        conn.commit()
        logger.info(f"Saved post-mortem for deal {deal_id}")
    except Exception as e:
        logger.error(f"Failed to save post-mortem: {e}")
    finally:
        conn.close()


def fetch_recent_trades(limit: int = 5, db_path=None):
    """
    Fetches the N most recent trades from the trade_log.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT * FROM trade_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        recent_trades = cursor.fetchall()
        return [dict(row) for row in recent_trades]
    except Exception as e:
        logger.error(f"Failed to fetch recent trades: {e}")
        return []
    finally:
        conn.close()


def fetch_all_trade_logs(db_path=None):
    """
    Fetches ALL trade log entries for scorecard analysis.
    Returns a list of dictionaries.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM trade_log ORDER BY timestamp ASC")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to fetch all trade logs: {e}")
        return []
    finally:
        conn.close()


def fetch_active_trades(db_path=None):
    """
    Fetches trades that are currently PENDING (waiting for trigger) or LIVE_PLACED (open).
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT * FROM trade_log 
            WHERE outcome IN ('PENDING', 'LIVE_PLACED', 'DRY_RUN_PLACED') 
            ORDER BY timestamp DESC
        """)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to fetch active trades: {e}")
        return []
    finally:
        conn.close()


def sync_active_trade(
    deal_id: str,
    epic: str,
    direction: str,
    size: float,
    entry: float,
    stop_loss: float,
    take_profit: float,
    db_path=None,
):
    """
    Syncs the trade status in the DB with the live position.
    If the trade exists, updates mutable fields.
    If not, inserts a new record representing this active trade.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    try:
        # Check if exists
        cursor.execute("SELECT id FROM trade_log WHERE deal_id = ?", (deal_id,))
        row = cursor.fetchone()

        if row:
            # Update existing
            cursor.execute(
                """
                UPDATE trade_log
                SET size = ?, entry = ?, stop_loss = ?, take_profit = ?, outcome = 'LIVE_PLACED'
                WHERE deal_id = ?
                """,
                (size, entry, stop_loss, take_profit, deal_id),
            )
            logger.info(f"Updated existing DB record for Deal {deal_id}")
        else:
            # Insert new
            from datetime import datetime

            timestamp = datetime.now().isoformat()
            cursor.execute(
                """
                INSERT INTO trade_log (
                    timestamp, epic, action, entry_type, entry, stop_loss, take_profit,
                    size, outcome, reasoning, confidence, spread_at_entry,
                    atr, is_dry_run, deal_id, use_trailing_stop
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    epic,
                    direction,  # 'BUY' or 'SELL'
                    "MANUAL_MONITOR",
                    entry,
                    stop_loss,
                    take_profit,
                    size,
                    "LIVE_PLACED",
                    "Resumed/Manual Monitor",
                    "N/A",
                    0.0,
                    0.0,  # ATR unknown at this point
                    False,  # Not dry run if we have a deal ID
                    deal_id,
                    True,  # Default to True for monitored trades
                ),
            )
            logger.info(f"Inserted new DB record for Deal {deal_id}")

        conn.commit()
    except Exception as e:
        logger.error(f"Failed to sync trade to DB: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    # Configure logging if run directly
    logging.basicConfig(level=logging.INFO)
    init_db()
