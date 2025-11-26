import sqlite3
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = "data/trader.db"

def get_db_connection():
    """
    Establishes and returns a connection to the SQLite database.
    """
    # Ensure the data directory exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row # Return rows as dictionary-like objects
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        raise

def init_db():
    """
    Initializes the database schema.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Table for Trade Logs (formerly trade_log.csv)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trade_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            epic TEXT,
            action TEXT,
            entry REAL,
            stop_loss REAL,
            take_profit REAL,
            size REAL,
            outcome TEXT,
            reasoning TEXT,
            confidence TEXT,
            spread_at_entry REAL,
            is_dry_run BOOLEAN,
            deal_id TEXT
        )
    ''')
    
    # Table for Trade Monitoring (formerly logs/trades/trade_....csv)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trade_monitor (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id TEXT,
            timestamp TEXT,
            bid REAL,
            offer REAL,
            pnl REAL,
            status TEXT,
            FOREIGN KEY(deal_id) REFERENCES trade_log(deal_id)
        )
    ''')

    # Check and add post_mortem column if missing (migration)
    cursor.execute("PRAGMA table_info(trade_log)")
    columns = [column['name'] for column in cursor.fetchall()]
    if 'post_mortem' not in columns:
        cursor.execute("ALTER TABLE trade_log ADD COLUMN post_mortem TEXT")
        logger.info("Added 'post_mortem' column to 'trade_log' table.")
    
    conn.commit()
    conn.close()
    logger.info(f"Database initialized at {DB_PATH}")

def fetch_trade_data(deal_id: str):
    """
    Fetches complete data for a trade (log + monitoring) by deal_id.
    Returns a dictionary with 'log' and 'monitor' keys.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Fetch from trade_log
        cursor.execute("SELECT * FROM trade_log WHERE deal_id = ?", (deal_id,))
        trade_log = cursor.fetchone()
        
        if not trade_log:
            return None
            
        # Fetch from trade_monitor
        cursor.execute("SELECT * FROM trade_monitor WHERE deal_id = ? ORDER BY timestamp ASC", (deal_id,))
        trade_monitor = cursor.fetchall()
        
        return {
            "log": dict(trade_log),
            "monitor": [dict(row) for row in trade_monitor]
        }
    finally:
        conn.close()

def save_post_mortem(deal_id: str, analysis: str):
    """
    Saves the post-mortem analysis to the trade_log table.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE trade_log SET post_mortem = ? WHERE deal_id = ?", (analysis, deal_id))
        conn.commit()
        logger.info(f"Saved post-mortem for deal {deal_id}")
    except Exception as e:
        logger.error(f"Failed to save post-mortem: {e}")
    finally:
        conn.close()

def fetch_recent_trades(limit: int = 5):
    """
    Fetches the N most recent trades from the trade_log.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM trade_log ORDER BY timestamp DESC LIMIT ?", (limit,))
        recent_trades = cursor.fetchall()
        return [dict(row) for row in recent_trades]
    except Exception as e:
        logger.error(f"Failed to fetch recent trades: {e}")
        return []
    finally:
        conn.close()

if __name__ == "__main__":
    # Configure logging if run directly
    logging.basicConfig(level=logging.INFO)
    init_db()
