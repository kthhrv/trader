import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
import os
import tempfile
import logging
from datetime import datetime
import time
from threading import Thread

from src.strategy_engine import StrategyEngine, Action, TradingSignal, EntryType
from src.database import init_db
from src.trade_logger_db import TradeLoggerDB
from src.trade_monitor_db import TradeMonitorDB
from tests.mocks import (
    MockIGClient,
    MockGeminiAnalyst,
    MockStreamManager,
    MockMarketStatus,
)

logger = logging.getLogger(__name__)


@pytest.fixture
def temp_db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temp_db:
        db_path = temp_db.name
    init_db(db_path)
    yield db_path
    os.remove(db_path)


def create_engine(epic, strategy_name, db_path, deal_id):
    mock_ig_client = MockIGClient()
    mock_gemini_analyst = MockGeminiAnalyst()
    mock_stream_manager = MockStreamManager()
    mock_market_status = MockMarketStatus()
    mock_news_fetcher = MagicMock()
    mock_trade_logger = TradeLoggerDB(db_path=db_path)

    # Fast polling for test speed
    mock_trade_monitor = TradeMonitorDB(
        client=mock_ig_client,
        stream_manager=mock_stream_manager,
        db_path=db_path,
        polling_interval=0.1,
        market_status=mock_market_status,
    )

    # Setup typical plan
    mock_gemini_analyst.analyze_market.return_value = TradingSignal(
        ticker=epic,
        action=Action.BUY,
        entry=100.0,
        stop_loss=90.0,
        take_profit=120.0,
        confidence="high",
        reasoning="Test",
        size=1.0,
        atr=5.0,
        entry_type=EntryType.INSTANT,
        use_trailing_stop=True,
    )

    # Mock account info for sizing
    mock_ig_client.get_account_info.return_value = pd.DataFrame(
        [
            {
                "accountId": "MOCK_ACC",
                "accountType": "SPREADBET",
                "preferred": True,
                "available": 10000.0,
            }
        ]
    )
    mock_ig_client.service.account_id = "MOCK_ACC"

    # Set unique deal ID
    mock_ig_client.place_spread_bet_order.return_value = {
        "dealId": deal_id,
        "dealStatus": "ACCEPTED",
    }

    # Mock history to return non-zero exit level
    mock_history_df = pd.DataFrame(
        [
            {
                "profitAndLoss": "£50.0",
                "openLevel": 100.0,
                "closeLevel": 110.0,
                "date": datetime.now().isoformat(),
            }
        ]
    )
    mock_ig_client.fetch_transaction_history_by_deal_id.return_value = mock_history_df

    # Mock historical data for MarketDataProvider
    mock_candles = pd.DataFrame(
        {
            "open": [100.0] * 50,
            "high": [105.0] * 50,
            "low": [95.0] * 50,
            "close": [100.0] * 50,
            "volume": [100] * 50,
        }
    )
    # Needed for pandas_ta
    mock_candles.index = pd.to_datetime([datetime.now()] * 50)
    mock_ig_client.fetch_historical_data.return_value = mock_candles

    engine = StrategyEngine(
        epic,
        strategy_name=strategy_name,
        ig_client=mock_ig_client,
        analyst=mock_gemini_analyst,
        news_fetcher=mock_news_fetcher,
        trade_logger=mock_trade_logger,
        trade_monitor=mock_trade_monitor,
        market_status=mock_market_status,
        stream_manager=mock_stream_manager,
        dry_run=False,
    )
    return engine, mock_ig_client, mock_stream_manager


# Capture original sleep before patching
_real_sleep = time.sleep


def fast_sleep_side_effect(seconds):
    if seconds >= 2.0:
        return  # Skip long retry waits
    _real_sleep(seconds)


@patch("src.strategy_engine.time.sleep", side_effect=fast_sleep_side_effect)
@patch("src.trade_monitor_db.time.sleep", side_effect=fast_sleep_side_effect)
def test_concurrent_trades(mock_sleep_db, mock_sleep_engine, temp_db_path, caplog):
    """
    Simulates two overlapping trades on different instruments to ensure isolation.
    """
    caplog.set_level(logging.DEBUG)

    # 1. Setup London Engine (FTSE)
    london_epic = "IX.D.FTSE.DAILY.IP"
    london_deal_id = "DEAL_LONDON_123"
    london_engine, london_ig, london_stream = create_engine(
        london_epic, "LONDON", temp_db_path, london_deal_id
    )

    # 2. Setup NY Engine (SPX)
    ny_epic = "IX.D.SPTRD.DAILY.IP"
    ny_deal_id = "DEAL_NY_456"
    ny_engine, ny_ig, ny_stream = create_engine(ny_epic, "NY", temp_db_path, ny_deal_id)

    # 3. Start London Strategy
    logger.info("--- Starting London Strategy ---")
    london_engine.generate_plan()
    london_thread = Thread(
        target=london_engine.execute_strategy,
        kwargs={
            "timeout_seconds": 5,
            "collection_seconds": 15,
        },  # Short collection for test
        daemon=True,
    )
    london_thread.start()
    time.sleep(0.2)

    # Trigger London Entry
    london_stream.simulate_price_tick(london_epic, 100.0, 100.5)
    time.sleep(1.0)

    assert london_engine.position_open is True
    london_ig.place_spread_bet_order.assert_called_once()

    # 4. Start NY Strategy WHILE London is still open
    logger.info("--- Starting NY Strategy ---")
    ny_engine.generate_plan()
    ny_thread = Thread(
        target=ny_engine.execute_strategy,
        kwargs={"timeout_seconds": 5, "collection_seconds": 15},
        daemon=True,
    )
    ny_thread.start()
    time.sleep(0.2)

    # Trigger NY Entry
    ny_stream.simulate_price_tick(ny_epic, 100.0, 100.5)
    time.sleep(1.0)

    assert ny_engine.position_open is True
    ny_ig.place_spread_bet_order.assert_called_once()

    # 5. Verify Isolation: Close London, ensure NY remains open
    logger.info(f"--- Closing London Trade {london_deal_id} ---")
    london_stream.simulate_trade_update({"dealId": london_deal_id, "status": "CLOSED"})

    # Wait for monitor to detect closure (History fetch takes up to 50s with retries)
    for _ in range(
        100
    ):  # 10 seconds (still might be tight if full retry hits, but monitor sees closure via stream first)
        if not london_thread.is_alive():
            break
        time.sleep(0.5)  # Increase sleep check interval

    assert not london_thread.is_alive(), (
        "London thread should have finished after closure."
    )
    assert ny_thread.is_alive(), "NY thread should still be alive."

    # 6. Verify NY can still be closed independently
    logger.info(f"--- Closing NY Trade {ny_deal_id} ---")
    ny_stream.simulate_trade_update({"dealId": ny_deal_id, "status": "CLOSED"})

    for _ in range(100):  # Wait longer for potential retries
        if not ny_thread.is_alive():
            break
        time.sleep(0.5)

    assert not ny_thread.is_alive(), "NY thread should have finished after closure."

    # Final check on DB logs
    from src.database import fetch_recent_trades

    trades = fetch_recent_trades(5, db_path=temp_db_path)
    assert len(trades) == 2
    epics = [t["epic"] for t in trades]
    assert london_epic in epics
    assert ny_epic in epics
