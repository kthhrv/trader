import pytest
from unittest.mock import MagicMock
import os
import tempfile
from src.strategy_engine import StrategyEngine
from src.gemini_analyst import TradingSignal, Action, EntryType
from src.database import init_db, get_db_connection
from src.trade_logger_db import TradeLoggerDB


@pytest.fixture
def test_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temp_db:
        db_path = temp_db.name
    init_db(db_path)
    yield db_path
    os.remove(db_path)


def test_timeout_logging(test_db):
    mock_ig_client = MagicMock()
    mock_analyst = MagicMock()
    trade_logger = TradeLoggerDB(db_path=test_db)
    mock_stream_manager = MagicMock()

    engine = StrategyEngine(
        epic="CS.D.GBPUSD.TODAY.IP",
        ig_client=mock_ig_client,
        analyst=mock_analyst,
        trade_logger=trade_logger,
        stream_manager=mock_stream_manager,
    )

    engine.active_plan = TradingSignal(
        ticker="GBPUSD",
        action=Action.BUY,
        entry=1.3000,
        stop_loss=1.2900,
        take_profit=1.3200,
        size=0.5,
        atr=0.0010,
        use_trailing_stop=False,
        confidence="high",
        reasoning="Test",
        entry_type=EntryType.INSTANT,
    )
    engine.active_plan_id = None

    # Run execution with a very short timeout
    engine.execute_strategy(timeout_seconds=0.01)

    # Verify trade was logged as TIMED_OUT
    conn = get_db_connection(test_db)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trade_log WHERE outcome = ?", ("TIMED_OUT",))
    timed_out_trade = cursor.fetchone()
    conn.close()

    assert timed_out_trade is not None
    assert timed_out_trade["outcome"] == "TIMED_OUT"
    assert timed_out_trade["deal_id"] is None
