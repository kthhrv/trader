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
            use_trailing_stop BOOLEAN,
            initial_stop_loss REAL
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

    if "initial_stop_loss" not in columns:
        logger.info(
            "Migrating database: Adding 'initial_stop_loss' column to 'trade_log'..."
        )
        try:
            cursor.execute("ALTER TABLE trade_log ADD COLUMN initial_stop_loss REAL")
            logger.info("Migration successful.")
        except Exception as e:
            logger.error(f"Migration failed: {e}")

    # Market Candles (1-Minute Aggregation)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_candles_1m (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            epic TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_candles_1m_epic_timestamp ON market_candles_1m (epic, timestamp)"
    )

    # Market Data Table (Ticks)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            epic TEXT,
            bid REAL,
            offer REAL,
            volume INTEGER
        )
    """)
    # Create index for fast time-range queries
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_data_epic_timestamp ON market_data (epic, timestamp)"
    )

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


def fetch_trades_in_range(start_date: str, end_date: str, db_path=None):
    """
    Fetches trades where the timestamp falls within the start_date and end_date (inclusive).
    Expects ISO format strings (YYYY-MM-DD...).
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT * FROM trade_log WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp DESC",
            (start_date, end_date),
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to fetch trades in range: {e}")
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


def update_trade_stop_loss(deal_id: str, new_stop_loss: float, db_path=None):
    """
    Updates the current stop_loss for a deal (trailing stop update).
    Does NOT affect initial_stop_loss.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE trade_log SET stop_loss = ? WHERE deal_id = ?",
            (new_stop_loss, deal_id),
        )
        conn.commit()
        logger.info(f"Updated DB stop_loss for {deal_id} to {new_stop_loss}")
    except Exception as e:
        logger.error(f"Failed to update stop_loss in DB: {e}")
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
                    timestamp, epic, action, entry_type, entry, stop_loss, initial_stop_loss, take_profit,
                    size, outcome, reasoning, confidence, spread_at_entry,
                    atr, is_dry_run, deal_id, use_trailing_stop
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    epic,
                    direction,  # 'BUY' or 'SELL'
                    "MANUAL_MONITOR",
                    entry,
                    stop_loss,
                    stop_loss,  # Set initial_stop_loss same as current for manual resume
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


def save_candle(
    epic: str,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: int,
    timestamp: str,
    db_path=None,
):
    """
    Logs a 1-minute candle to the database.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO market_candles_1m (timestamp, epic, open, high, low, close, volume) 
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (timestamp, epic, open_price, high, low, close, volume),
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to save candle: {e}")
    finally:
        conn.close()


def save_market_tick(
    epic: str,
    bid: float,
    offer: float,
    volume: int = 0,
    timestamp: str = None,
    db_path=None,
):
    """
    Logs a single market tick (price update) to the database.
    Timestamp defaults to datetime.now().isoformat() if not provided.
    """
    if not timestamp:
        from datetime import datetime

        timestamp = datetime.now().isoformat()

    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO market_data (timestamp, epic, bid, offer, volume) VALUES (?, ?, ?, ?, ?)",
            (timestamp, epic, bid, offer, volume),
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to save market tick: {e}")
    finally:
        conn.close()


def fetch_market_data_range(epic: str, start_time: str, end_time: str, db_path=None):
    """
    Fetches raw market ticks for a given epic and time range.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT * FROM market_data 
            WHERE epic = ? AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp ASC
        """,
            (epic, start_time, end_time),
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to fetch market data range: {e}")
        return []
    finally:
        conn.close()


def fetch_candles_range(epic: str, start_time: str, end_time: str, db_path=None):
    """
    Fetches 1-minute candles for a given epic and time range.
    Returns a list of dictionaries with keys: timestamp, open, high, low, close, volume.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT timestamp, open, high, low, close, volume 
            FROM market_candles_1m 
            WHERE epic = ? AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp ASC
        """,
            (epic, start_time, end_time),
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to fetch candles range: {e}")
        return []
    finally:
        conn.close()


def delete_trade_log(identifier: str, is_db_id: bool = False, db_path=None):
    """
    Deletes a trade log entry by deal_id or primary key id.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    try:
        if is_db_id:
            cursor.execute("DELETE FROM trade_log WHERE id = ?", (identifier,))
            logger.info(f"Deleted trade log with DB ID: {identifier}")
        else:
            cursor.execute("DELETE FROM trade_log WHERE deal_id = ?", (identifier,))
            logger.info(f"Deleted trade log with Deal ID: {identifier}")

        if cursor.rowcount == 0:
            logger.warning(f"No trade found to delete for identifier: {identifier}")
            return False

        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to delete trade log: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    # Configure logging if run directly
    logging.basicConfig(level=logging.INFO)
    init_db()
