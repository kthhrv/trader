import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
import os
import tempfile
import logging
from datetime import datetime, timedelta
import json
import time

from src.strategy_engine import StrategyEngine, Action, TradingSignal
from src.database import get_db_connection, init_db, fetch_trade_data
from src.trade_logger_db import TradeLoggerDB
from src.trade_monitor_db import TradeMonitorDB
from tests.mocks import (
    MockIGClient,
    MockGeminiAnalyst,
    MockStreamManager,
    MockMarketStatus,
)

# Capture real sleep for testing delays
real_sleep = time.sleep

logger = logging.getLogger(__name__)


@pytest.fixture
def temp_db_path():
    # Create a temporary file for the database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temp_db:
        db_path = temp_db.name

    # Initialize the database schema for the temporary DB
    init_db(db_path)

    yield db_path

    # Clean up the temporary database file after the test
    os.remove(db_path)


@pytest.fixture
def e2e_mocks(temp_db_path):
    # Instantiate mocks with the temporary database path
    mock_ig_client = MockIGClient()
    mock_gemini_analyst = MockGeminiAnalyst()
    mock_stream_manager = MockStreamManager()
    mock_market_status = MockMarketStatus()
    mock_news_fetcher = (
        MagicMock()
    )  # NewsFetcher doesn't interact with DB directly, so MagicMock is fine
    mock_trade_logger = TradeLoggerDB(db_path=temp_db_path)
    # Wrap log_trade with a mock to allow assertions while keeping functionality
    mock_trade_logger.log_trade = MagicMock(side_effect=mock_trade_logger.log_trade)
    mock_trade_logger.update_trade_status = MagicMock(
        side_effect=mock_trade_logger.update_trade_status
    )

    mock_trade_monitor = TradeMonitorDB(
        client=mock_ig_client,
        stream_manager=mock_stream_manager,
        db_path=temp_db_path,
        polling_interval=0.1,
    )
    # Wrap monitor_trade with a mock to allow assertions while keeping functionality
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


def test_e2e_trading_flow(e2e_mocks, caplog):
    caplog.set_level(logging.DEBUG)  # Capture DEBUG logs

    (
        mock_ig_client,
        mock_gemini_analyst,
        mock_stream_manager,
        mock_market_status,
        mock_news_fetcher,
        mock_trade_logger,
        mock_trade_monitor,
        db_path,
    ) = e2e_mocks

    epic = "IX.D.FTSE.DAILY.IP"
    strategy_name = "TEST_E2E"
    entry_price = 7500.0
    stop_loss = 7450.0
    take_profit = 7600.0
    trade_size = 1.0

    # 1. Setup Mock Responses
    # Mock historical data from IGClient with pre-calculated indicators
    mock_historical_df = pd.DataFrame(
        {
            "open": [entry_price - 10] * 25,
            "high": [entry_price - 5] * 25,
            "low": [entry_price - 15] * 25,
            "close": [entry_price - 8] * 24
            + [entry_price + 3],  # Make the last close different
            "volume": [1000] * 25,
        }
    )
    mock_historical_df["ATR"] = [10.0] * 25
    mock_historical_df["RSI"] = [55.0] * 25
    mock_historical_df["EMA_20"] = [entry_price] * 25
    mock_historical_df.index = pd.to_datetime(
        [datetime.now() - timedelta(minutes=(25 - 1 - i) * 15) for i in range(25)]
    )
    mock_ig_client.fetch_historical_data.return_value = mock_historical_df
    # Mock Gemini's response for a BUY signal
    mock_gemini_analyst.analyze_market.return_value = TradingSignal(
        ticker=epic,
        action=Action.BUY,
        entry=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        confidence="high",
        reasoning="E2E Test Buy",
        size=trade_size,
        atr=10.0,
        entry_type="INSTANT",
        use_trailing_stop=True,
    )

    # Mock _calculate_size directly on the StrategyEngine instance
    # The actual method will be called, so we need to set expectations for the client calls it makes
    mock_ig_client.get_account_info.return_value = pd.DataFrame(
        {
            "accountId": ["TEST_ACC_ID"],
            "accountType": ["SPREADBET"],
            "available": [10000.0],
            "balance": [10000.0],
        }
    )

    # 2. Instantiate StrategyEngine with mocks
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
        dry_run=False,  # We want to simulate a real trade for P&L
    )
    # Ensure the StrategyEngine's logger is set to DEBUG for this test
    logging.getLogger("src.strategy_engine").setLevel(logging.DEBUG)

    # 3. Generate Plan
    engine.generate_plan()
    assert engine.active_plan is not None
    assert engine.active_plan.action == Action.BUY
    assert mock_ig_client.fetch_historical_data.call_count == 4
    mock_gemini_analyst.analyze_market.assert_called_once()

    # 4. Execute Strategy - this starts monitoring
    # We run in a separate thread because execute_strategy has a loop
    # and we need to simulate price ticks concurrently.
    from threading import Thread

    # Patch sleeps to speed up test execution (Fast Sleep: 1/100th duration)
    # This prevents busy loops from hanging and allows timeouts to work naturally but fast.
    def fast_sleep(x):
        real_sleep(x / 100.0)

    with (
        patch("src.strategy_engine.time.sleep", side_effect=fast_sleep),
        patch("src.trade_monitor_db.time.sleep", side_effect=fast_sleep),
    ):
        trade_execution_thread = Thread(
            target=engine.execute_strategy,
            kwargs={"timeout_seconds": 3.0, "collection_seconds": 5},
        )
        trade_execution_thread.start()

        # Give the engine a moment to connect to the stream (mocked)
        real_sleep(0.1)

        # Verify stream manager was told to connect and subscribe
        mock_stream_manager.connect_and_subscribe.assert_called_once_with(
            epic, engine._stream_price_update_handler
        )

        # 5. Simulate Price Ticks to trigger entry
        # Price moves below entry, then hits entry
        mock_stream_manager.simulate_price_tick(epic, entry_price - 5, entry_price - 4)
        real_sleep(0.1)
        mock_stream_manager.simulate_price_tick(epic, entry_price, entry_price + 1)
        real_sleep(0.5)  # Increased sleep here to allow processing

        # Ensure trade placement was attempted
        mock_ig_client.place_spread_bet_order.assert_called_once()
        call_kwargs = mock_ig_client.place_spread_bet_order.call_args[1]
        assert call_kwargs["direction"] == "BUY"
        # Adjusted Stop: 7450 - 1.0 (spread) = 7449
        # Adjusted Risk: 7501 - 7449 = 52 points
        # Calculated Size: 100 / 52 = 1.923... -> 1.92
        assert call_kwargs["size"] == 1.92
        assert call_kwargs["stop_level"] == 7449.0  # ADJUSTED STOP LOSS
        assert call_kwargs["limit_level"] is None
        # Verify trade monitor started
        mock_trade_monitor.monitor_trade.assert_called_once_with(
            "MOCK_DEAL_ID",
            epic,
            entry_price=7501.0,  # Actual fill
            stop_loss=7449.0,  # Adjusted Stop
            atr=10.0,
            use_trailing_stop=True,
        )

        # 6. Simulate Trade Closure via Stream Update
        # Mock IG Client fetch_transaction_history_by_deal_id for PnL
        mock_history_df = pd.DataFrame(
            [
                {
                    "dealReference": "REF",
                    "profitAndLoss": "£50.00",
                    "openLevel": 7500.0,
                    "closeLevel": 7600.0,
                }
            ]
        )
        mock_ig_client.fetch_transaction_history_by_deal_id.return_value = (
            mock_history_df
        )

        # Manually trigger the closure callback on monitor
        # We need to construct the payload as the monitor expects
        close_payload = json.dumps(
            {
                "dealId": "MOCK_DEAL_ID",
                "status": "CLOSED",
                "level": 7600.0,
                "profitAndLoss": 50.0,
            }
        )

        # Wait briefly for monitor to be running and subscribed
        real_sleep(0.2)

        # Trigger callback
        mock_trade_monitor._handle_trade_update(
            {"type": "trade_update", "payload": close_payload}
        )

        # Allow monitor to poll and detect closure
        # Join should finish quickly now due to fast sleep
        trade_execution_thread.join(timeout=5.0)  # Wait for the thread to finish
        assert not trade_execution_thread.is_alive()

    # Verify trade logger called for placement (AFTER monitoring finishes)
    # 1. PENDING log (during generation)
    mock_trade_logger.log_trade.assert_called_once()
    pending_call_args = mock_trade_logger.log_trade.call_args[1]
    assert pending_call_args["outcome"] == "PENDING"

    # 2. LIVE_PLACED update (during execution)
    mock_trade_logger.update_trade_status.assert_called_once()
    update_call_args = mock_trade_logger.update_trade_status.call_args[1]
    # In python 3.8+ call_args can be accessed by index or attribute, here we use index for kwargs
    if not update_call_args:  # fallback if using positional
        update_call_kwargs = mock_trade_logger.update_trade_status.call_args.kwargs
    else:
        update_call_kwargs = update_call_args

    assert update_call_kwargs["outcome"] == "LIVE_PLACED"
    assert update_call_kwargs["deal_id"] == "MOCK_DEAL_ID"

    # Verify stream manager was stopped
    mock_stream_manager.stop.assert_called_once()

    # 7. Verify P&L and Trade Log in the temporary database
    trade_data = fetch_trade_data("MOCK_DEAL_ID", db_path=db_path)
    assert trade_data is not None
    assert trade_data["log"]["deal_id"] == "MOCK_DEAL_ID"
    assert trade_data["log"]["outcome"] == "CLOSED"  # Outcome updated after monitoring
    # The monitor will log multiple times, but the final status should reflect closure
    # We need to query the monitor table directly to get the latest status and PnL.
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT outcome, pnl FROM trade_log WHERE deal_id = ?", ("MOCK_DEAL_ID",)
    )
    latest_monitor_entry = cursor.fetchone()
    conn.close()

    assert latest_monitor_entry is not None
    assert latest_monitor_entry[0] == "CLOSED"
    assert latest_monitor_entry[1] == 50.0
    assert "Trade MOCK_DEAL_ID CLOSED. Monitoring finished." in caplog.text

    logger.info("E2E test completed successfully.")
