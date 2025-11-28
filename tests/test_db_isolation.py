import pytest
import tempfile
import os
import sqlite3
from unittest.mock import MagicMock
from src.trade_monitor_db import TradeMonitorDB
from src.trade_logger_db import TradeLoggerDB
from src.database import init_db, get_db_connection
from src.gemini_analyst import TradingSignal, Action

@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    init_db(db_path)
    yield db_path
    os.remove(db_path)

def test_trade_monitor_uses_custom_db_path(temp_db):
    mock_client = MagicMock()
    mock_stream_manager = MagicMock() # Add mock stream_manager
    monitor = TradeMonitorDB(client=mock_client, stream_manager=mock_stream_manager, db_path=temp_db)
    
    # Verify attribute is set
    assert monitor.db_path == temp_db
    
    # Prepare DB with an initial entry
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    deal_id = "TEST_MONITOR_DEAL"
    cursor.execute("INSERT INTO trade_log (deal_id, outcome) VALUES (?, ?)", (deal_id, "OPEN"))
    conn.commit()
    conn.close()

    # Perform an update operation
    monitor._update_db(deal_id, 100.0, 50.0, "2023-01-01", "CLOSED")
    
    # Verify data is updated in the temp db
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT outcome, pnl FROM trade_log WHERE deal_id = ?", (deal_id,))
    row = cursor.fetchone()
    conn.close()
    
    assert row is not None
    assert row[0] == "CLOSED"
    assert row[1] == 50.0

def test_trade_logger_uses_custom_db_path(temp_db):
    logger_db = TradeLoggerDB(db_path=temp_db)
    
    # Verify attribute is set
    assert logger_db.db_path == temp_db
    
    # Perform a log operation
    plan = TradingSignal(
        ticker="TEST", 
        action=Action.BUY, 
        entry=100, 
        stop_loss=90, 
        take_profit=110, 
        confidence="low", 
        reasoning="test", 
        size=1, 
        atr=5,
        entry_type="INSTANT",
        use_trailing_stop=True
    )
    deal_id = "TEST_LOGGER_DEAL"
    logger_db.log_trade("TEST", plan, "TEST_OUTCOME", 1.0, False, deal_id)
    
    # Verify data is in the temp db
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trade_log WHERE deal_id = ?", (deal_id,))
    row = cursor.fetchone()
    conn.close()
    
    assert row is not None
    # row is tuple, let's assume column order or check specific value
    # deal_id is the last column usually
    assert deal_id in row or row[-1] == deal_id
