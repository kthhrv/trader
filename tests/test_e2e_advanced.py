import pytest
from unittest.mock import MagicMock
import os
import tempfile
import logging
import time
from threading import Thread
import pandas as pd  # Added

from src.strategy_engine import StrategyEngine
from src.gemini_analyst import Action, TradingSignal, EntryType
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


@pytest.fixture
def advanced_mocks(temp_db_path):
    mock_ig_client = MockIGClient()
    mock_gemini_analyst = MockGeminiAnalyst()
    mock_stream_manager = MockStreamManager()
    mock_market_status = MockMarketStatus()
    mock_news_fetcher = MagicMock()

    mock_trade_logger = TradeLoggerDB(db_path=temp_db_path)
    mock_trade_logger.log_trade = MagicMock(side_effect=mock_trade_logger.log_trade)

    # Mock account info for dynamic sizing (Required for TradeExecutor)
    mock_ig_client.get_account_info.return_value = pd.DataFrame(
        {"accountId": ["TEST_ACC_ID"], "balance": [10000.0], "available": [10000.0]}
    )
    mock_ig_client.service.account_id = "TEST_ACC_ID"

    # Fast polling for test speed
    mock_trade_monitor = TradeMonitorDB(
        client=mock_ig_client,
        stream_manager=mock_stream_manager,
        db_path=temp_db_path,
        polling_interval=0.1,
    )
    mock_trade_monitor.monitor_trade = MagicMock(
        side_effect=mock_trade_monitor.monitor_trade
    )

    yield (
        mock_ig_client,
        mock_gemini_analyst,
        mock_stream_manager,
        mock_market_status,
        mock_news_fetcher,
        mock_trade_logger,
        mock_trade_monitor,
        temp_db_path,
    )


def test_e2e_trailing_stop(advanced_mocks, caplog):
    caplog.set_level(logging.DEBUG)
    (
        mock_ig_client,
        mock_gemini_analyst,
        mock_stream_manager,
        mock_market_status,
        mock_news_fetcher,
        mock_trade_logger,
        mock_trade_monitor,
        db_path,
    ) = advanced_mocks

    epic = "IX.D.FTSE.DAILY.IP"
    entry_price = 7500.0
    stop_loss = 7450.0

    # 1. Setup: INSTANT Entry
    mock_gemini_analyst.analyze_market.return_value = TradingSignal(
        ticker=epic,
        action=Action.BUY,
        entry=entry_price,
        stop_loss=stop_loss,
        take_profit=7600.0,
        confidence="high",
        reasoning="Test Trailing",
        size=1.0,
        atr=10.0,
        entry_type=EntryType.INSTANT,
        use_trailing_stop=True,
    )

    engine = StrategyEngine(
        epic,
        strategy_name="TEST_TRAIL",
        ig_client=mock_ig_client,
        analyst=mock_gemini_analyst,
        news_fetcher=mock_news_fetcher,
        trade_logger=mock_trade_logger,
        trade_monitor=mock_trade_monitor,
        market_status=mock_market_status,
        stream_manager=mock_stream_manager,
        dry_run=False,
    )
    logging.getLogger("src.strategy_engine").setLevel(logging.DEBUG)
    engine.generate_plan()
    trade_execution_thread = Thread(
        target=engine.execute_strategy,
        kwargs={"timeout_seconds": 10.0, "collection_seconds": 15},
        daemon=True,
    )
    trade_execution_thread.start()
    time.sleep(0.1)

    # Trigger Trade
    mock_stream_manager.simulate_price_tick(epic, entry_price, entry_price + 1)
    time.sleep(0.2)

    # Verify Trade Placed
    mock_ig_client.place_spread_bet_order.assert_called_once()
    # Original Stop: 7450.0 (No adjustment)
    mock_trade_monitor.monitor_trade.assert_called_once_with(
        "MOCK_DEAL_ID",
        epic,
        entry_price=7501.0,
        stop_loss=7450.0,
        atr=10.0,
        use_trailing_stop=True,
    )

    # 3. Simulate Price Movement for Trailing
    # Monitor is running in background (polling for trailing stop).

    # Initial State
    initial_pos = {
        "dealId": "MOCK_DEAL_ID",
        "direction": "BUY",
        "bid": 7500,
        "offer": 7501,
        "stopLevel": 7450,  # Original SL
    }

    # Move 1: Profit < 1.5R.
    move_1_pos = {
        "dealId": "MOCK_DEAL_ID",
        "direction": "BUY",
        "bid": 7550,
        "offer": 7551,
        "stopLevel": 7450,
    }

    # Move 2: Profit > 1.5R (7501 + 1.5*50 = 7576). Price = 7590.
    move_2_pos = {
        "dealId": "MOCK_DEAL_ID",
        "direction": "BUY",
        "bid": 7590,
        "offer": 7591,
        "stopLevel": 7450,
    }

    # Dynamic mock for fetch_open_position that reflects updated stop levels
    current_pos_state = {"stopLevel": 7450.0}  # Initial SL

    def fetch_pos_side_effect(deal_id):
        # Determine base price based on time/call count logic or just iterate through a sequence?
        # We can use the original sequence logic but override stopLevel.
        # But wait, the original sequence had 20+20+100 items.
        # Let's just map time to price or simply iterate an iterator and update the stopLevel.

        # Simple iterator wrapper
        try:
            pos = next(pos_iterator)
            pos["stopLevel"] = current_pos_state["stopLevel"]
            return pos
        except StopIteration:
            return None

    # Update stop level when update_open_position is called
    def update_pos_side_effect(deal_id, stop_level=None, limit_level=None):
        if stop_level:
            current_pos_state["stopLevel"] = stop_level
        return {"dealId": deal_id, "status": "ACCEPTED"}

    mock_ig_client.update_open_position.side_effect = update_pos_side_effect

    # Define the sequence of market moves
    pos_sequence = [initial_pos] * 20 + [move_1_pos] * 20 + [move_2_pos] * 100
    pos_iterator = iter(pos_sequence)

    mock_ig_client.fetch_open_position_by_deal_id.side_effect = fetch_pos_side_effect

    # Give time for trailing logic to run (polling interval is 0.1s, we need a few polls)
    # We poll for the expected call count with a timeout
    start_wait = time.time()
    while (
        mock_ig_client.update_open_position.call_count < 2
        and (time.time() - start_wait) < 5.0
    ):
        time.sleep(0.1)

    # 4. Trigger Closure via Stream
    mock_stream_manager.simulate_trade_update(
        {
            "dealId": "MOCK_DEAL_ID",
            "status": "CLOSED",
            "level": 7555.0,
            "profitAndLoss": 55.0,
        }
    )

    # Wait for monitor to finish
    trade_execution_thread.join(timeout=2.0)

    # 5. Verify Updates
    # Check calls to update_open_position
    assert mock_ig_client.update_open_position.call_count >= 2

    # First update: Breakeven
    args1 = mock_ig_client.update_open_position.call_args_list[0]
    assert args1[1]["stop_level"] == 7501.0  # Entry Price

    # Second update: Trailing (ATR 10 * 3 = 30. Price 7590 - 30 = 7560)
    args2 = mock_ig_client.update_open_position.call_args_list[1]
    assert args2[1]["stop_level"] == 7560.0  # 7590 - 30 (Wider trail)


def test_e2e_no_trailing_stop(advanced_mocks, caplog):
    caplog.set_level(logging.DEBUG)
    (
        mock_ig_client,
        mock_gemini_analyst,
        mock_stream_manager,
        mock_market_status,
        mock_news_fetcher,
        mock_trade_logger,
        mock_trade_monitor,
        db_path,
    ) = advanced_mocks

    epic = "IX.D.FTSE.DAILY.IP"
    entry_price = 7500.0
    stop_loss = 7450.0

    # 1. Setup: INSTANT Entry with use_trailing_stop=False
    mock_gemini_analyst.analyze_market.return_value = TradingSignal(
        ticker=epic,
        action=Action.BUY,
        entry=entry_price,
        stop_loss=stop_loss,
        take_profit=7600.0,
        confidence="high",
        reasoning="Test No Trailing",
        size=1.0,
        atr=10.0,
        entry_type=EntryType.INSTANT,
        use_trailing_stop=False,
    )

    engine = StrategyEngine(
        epic,
        strategy_name="TEST_NO_TRAIL",
        ig_client=mock_ig_client,
        analyst=mock_gemini_analyst,
        news_fetcher=mock_news_fetcher,
        trade_logger=mock_trade_logger,
        trade_monitor=mock_trade_monitor,
        market_status=mock_market_status,
        stream_manager=mock_stream_manager,
        dry_run=False,
    )
    logging.getLogger("src.strategy_engine").setLevel(logging.DEBUG)
    engine.generate_plan()

    # 2. Start Execution
    trade_execution_thread = Thread(
        target=engine.execute_strategy,
        kwargs={"timeout_seconds": 10.0, "collection_seconds": 15},
        daemon=True,
    )
    trade_execution_thread.start()
    time.sleep(0.1)

    # Trigger Trade
    mock_stream_manager.simulate_price_tick(epic, entry_price, entry_price + 1)
    time.sleep(0.2)

    # 3. Simulate Price Movement that WOULD normally trigger trailing
    # But because trailing is disabled, nothing should happen.

    initial_pos = {
        "dealId": "MOCK_DEAL_ID_2",
        "direction": "BUY",
        "bid": 7500,
        "offer": 7501,
        "stopLevel": 7450,
    }
    move_pos = {
        "dealId": "MOCK_DEAL_ID_2",
        "direction": "BUY",
        "bid": 7575,
        "offer": 7576,
        "stopLevel": 7450,
    }

    mock_ig_client.fetch_open_position_by_deal_id.side_effect = [initial_pos] * 5 + [
        move_pos
    ] * 100

    # Mock order placement return to match deal ID (needed for monitor)
    mock_ig_client.place_spread_bet_order.return_value = {
        "dealId": "MOCK_DEAL_ID_2",
        "dealStatus": "ACCEPTED",
    }

    # Give time for monitor to poll
    time.sleep(1.0)

    # Trigger Closure via Stream
    mock_stream_manager.simulate_trade_update(
        {
            "dealId": "MOCK_DEAL_ID_2",
            "status": "CLOSED",
            "level": 7575.0,
            "profitAndLoss": 75.0,
        }
    )

    # Wait for monitor to finish
    trade_execution_thread.join(timeout=2.0)

    # 4. Verify Updates
    # update_open_position should NEVER be called because trailing is disabled
    mock_ig_client.update_open_position.assert_not_called()
