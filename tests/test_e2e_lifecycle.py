import pytest
import sqlite3
import os
import pandas as pd
from unittest.mock import MagicMock, patch
from src.strategy_engine import StrategyEngine, Action, TradingSignal, EntryType
from src.trade_logger_db import TradeLoggerDB
from src.trade_monitor_db import TradeMonitorDB
from src.database import init_db

# Use a separate temp DB for this test to avoid clashing with other tests or dev data
TEST_DB_PATH = "tests/test_lifecycle.db"


@pytest.fixture
def lifecycle_db():
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    init_db(TEST_DB_PATH)
    yield TEST_DB_PATH
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)


def get_row_by_id(db_path, row_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trade_log WHERE id = ?", (row_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


@pytest.mark.integration
def test_trade_lifecycle_flow(lifecycle_db):
    """
    Verifies the full DB lifecycle: PENDING -> LIVE_PLACED -> CLOSED
    """
    # 1. Setup Components
    # We use real DB loggers but mock the external clients (IG, Gemini)
    mock_client = MagicMock()
    mock_analyst = MagicMock()
    mock_news = MagicMock()
    mock_stream = MagicMock()
    mock_market_status = MagicMock()
    mock_market_status.is_holiday.return_value = False

    logger_db = TradeLoggerDB(db_path=lifecycle_db)
    monitor_db = TradeMonitorDB(
        mock_client, mock_stream, db_path=lifecycle_db, market_status=mock_market_status
    )

    engine = StrategyEngine(
        "TEST.EPIC",
        ig_client=mock_client,
        analyst=mock_analyst,
        news_fetcher=mock_news,
        trade_logger=logger_db,
        trade_monitor=monitor_db,
        market_status=mock_market_status,
        stream_manager=mock_stream,
    )
    # Safety patch for AttributeError
    engine.active_plan_id = None

    # --- PHASE 1: GENERATE PLAN (PENDING) ---

    # Mock Data & Analysis using real DataFrame to support .iloc and pandas-ta
    data = {
        "open": [100.0] * 50,
        "high": [105.0] * 50,
        "low": [95.0] * 50,
        "close": [102.0] * 50,
        "volume": [1000] * 50,
    }
    mock_df = pd.DataFrame(data)
    # Important: Set DatetimeIndex for session context filtering
    mock_df.index = pd.to_datetime(
        [pd.Timestamp.now() - pd.Timedelta(minutes=15 * i) for i in range(50)][::-1]
    )
    mock_client.fetch_historical_data.return_value = mock_df

    mock_analyst.analyze_market.return_value = TradingSignal(
        ticker="TEST.EPIC",
        action=Action.BUY,
        entry=100,
        stop_loss=90,
        take_profit=120,
        confidence="high",
        reasoning="Lifecycle Test",
        size=1,
        atr=5.0,
        entry_type=EntryType.INSTANT,
        use_trailing_stop=True,
    )

    # Run Generation
    engine.generate_plan()

    # Verification 1: Check DB for PENDING state
    assert engine.active_plan_id is not None, "active_plan_id should be set"
    row_1 = get_row_by_id(lifecycle_db, engine.active_plan_id)
    assert row_1["outcome"] == "PENDING"
    assert row_1["deal_id"] is None
    assert row_1["epic"] == "TEST.EPIC"

    # --- PHASE 2: EXECUTE (LIVE_PLACED) ---

    # Mock successful execution
    mock_client.place_spread_bet_order.return_value = {
        "dealId": "REAL_DEAL_123",
        "dealStatus": "ACCEPTED",
    }
    mock_client.get_market_info.return_value = {
        "snapshot": {"bid": 99, "offer": 100}
    }  # Spread ok
    mock_client.service.fetch_market_by_epic.return_value = {
        "snapshot": {"bid": 99, "offer": 100}
    }  # Spread ok

    # Patch _calculate_size on TradeExecutor to avoid account calls
    with patch("src.trade_executor.TradeExecutor._calculate_size", return_value=1.0):
        # Mock stream update to trigger immediately
        def mock_connect_and_subscribe(epic, callback):
            callback({"epic": epic, "bid": 99, "offer": 100})

        mock_stream.connect_and_subscribe.side_effect = mock_connect_and_subscribe

        # Here we just want to verify the Transition to PLACED.
        monitor_db.monitor_trade = MagicMock()

        engine.execute_strategy(timeout_seconds=1, collection_seconds=2)

    # Verification 2: Check DB for LIVE_PLACED state
    row_2 = get_row_by_id(lifecycle_db, engine.active_plan_id)
    assert row_2["outcome"] == "LIVE_PLACED"
    assert row_2["deal_id"] == "REAL_DEAL_123"
    assert row_2["id"] == engine.active_plan_id  # ID should not change

    # --- PHASE 3: MONITOR & CLOSE (CLOSED) ---

    # Now explicitly test the Monitor DB updating the SAME row
    # We simulate what monitor_trade would do internally upon closure
    monitor_db._update_db(
        deal_id="REAL_DEAL_123",
        exit_price=110.0,
        pnl=100.0,
        exit_time="2025-01-01T12:00:00",
        status="CLOSED",
    )

    # Verification 3: Check DB for CLOSED state
    row_3 = get_row_by_id(lifecycle_db, engine.active_plan_id)
    assert row_3["outcome"] == "CLOSED"
    assert row_3["pnl"] == 100.0
    assert row_3["exit_price"] == 110.0
    assert row_3["deal_id"] == "REAL_DEAL_123"


def test_trade_lifecycle_timeout(lifecycle_db):
    """
    Verifies the Timeout lifecycle: PENDING -> TIMED_OUT
    """
    mock_client = MagicMock()
    mock_analyst = MagicMock()
    mock_stream = MagicMock()
    mock_market_status = MagicMock()
    mock_market_status.is_holiday.return_value = False  # Explicitly False
    logger_db = TradeLoggerDB(db_path=lifecycle_db)

    engine = StrategyEngine(
        "TIMEOUT.EPIC",
        ig_client=mock_client,
        analyst=mock_analyst,
        news_fetcher=MagicMock(),
        trade_logger=logger_db,
        trade_monitor=MagicMock(),
        market_status=mock_market_status,
        stream_manager=mock_stream,
    )
    engine.active_plan_id = None

    # 1. PENDING
    mock_analyst.analyze_market.return_value = TradingSignal(
        ticker="TIMEOUT.EPIC",
        action=Action.BUY,
        entry=100,
        stop_loss=90,
        take_profit=120,
        confidence="high",
        reasoning="Timeout Test",
        size=1,
        atr=5.0,
        entry_type=EntryType.INSTANT,
        use_trailing_stop=True,
    )

    # Dummy data for generate_plan
    # Use real DataFrame to avoid comparison errors
    data = {
        "open": [100.0] * 50,
        "high": [105.0] * 50,
        "low": [95.0] * 50,
        "close": [102.0] * 50,
        "volume": [1000] * 50,
    }
    mock_df = pd.DataFrame(data)
    # Important: Set DatetimeIndex for session context filtering
    mock_df.index = pd.to_datetime(
        [pd.Timestamp.now() - pd.Timedelta(minutes=15 * i) for i in range(50)][::-1]
    )
    mock_client.fetch_historical_data.return_value = mock_df

    engine.generate_plan()

    row_id = engine.active_plan_id
    assert row_id is not None

    # 2. TIMEOUT
    # Simulate execution loop expiring
    # We assume stream does not trigger anything
    mock_stream.connect_and_subscribe.return_value = None

    # Execute with very short timeout
    engine.execute_strategy(timeout_seconds=0.1, collection_seconds=0.2)

    # Verify DB update
    row_final = get_row_by_id(lifecycle_db, row_id)
    assert row_final["outcome"] == "TIMED_OUT"
    assert row_final["deal_id"] is None  # As per recent refactor
