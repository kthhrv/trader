import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
import logging
from datetime import datetime
import time
from threading import Thread

from src.strategy_engine import StrategyEngine
from src.gemini_analyst import Action, TradingSignal
from src.database import init_db
from src.trade_logger_db import TradeLoggerDB
from src.trade_monitor_db import TradeMonitorDB
from tests.mocks import (
    MockIGClient,
    MockGeminiAnalyst,
    MockStreamManager,
    MockMarketStatus,
)


@pytest.fixture
def e2e_mocks(tmp_path):
    mock_ig_client = MockIGClient()
    mock_gemini_analyst = MockGeminiAnalyst()
    mock_stream_manager = MockStreamManager()
    mock_market_status = MockMarketStatus()
    mock_news_fetcher = MagicMock()
    mock_market_provider = MagicMock()

    db_path = str(tmp_path / "test_trader.db")
    init_db(db_path)
    mock_trade_logger = TradeLoggerDB(db_path=db_path)
    mock_trade_monitor = TradeMonitorDB(
        client=mock_ig_client,
        stream_manager=mock_stream_manager,
        db_path=db_path,
        polling_interval=0.1,  # Fast polling
    )
    # Spy on monitor_trade
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
        mock_market_provider,
        db_path,
    )


# Store the original time.sleep before patching
real_sleep = time.sleep


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
        mock_market_provider,
        db_path,
    ) = e2e_mocks

    epic = "IX.D.FTSE.DAILY.IP"
    strategy_name = "TEST_E2E"
    entry_price = 7500.0
    stop_loss = 7450.0
    take_profit = 7600.0
    trade_size = 1.0

    # 1. Setup Mock Responses
    # Mock MarketDataProvider return
    mock_market_provider.get_market_context.return_value = "Mock E2E Context"

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
            "balance": [10000.0],
            "available": [10000.0],
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
    # Inject the mocked provider
    engine.data_provider = mock_market_provider

    # Ensure the StrategyEngine's logger is set to DEBUG for this test
    logging.getLogger("src.strategy_engine").setLevel(logging.DEBUG)

    # 3. Generate Plan
    engine.generate_plan()
    assert engine.active_plan is not None
    assert engine.active_plan.action == Action.BUY
    mock_market_provider.get_market_context.assert_called_once()

    # 4. Execute Strategy - this starts monitoring
    # We run in a separate thread because execute_strategy has a loop
    # and we need to simulate price ticks concurrently.

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
            kwargs={"timeout_seconds": 0.5, "collection_seconds": 1},
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
        # Original Risk: 7501 - 7450 = 51 points
        # Calculated Size: 100 / 51 = 1.9607... -> 1.96
        assert call_kwargs["size"] == 1.96
        assert call_kwargs["stop_level"] == 7450.0  # Original Stop
        assert call_kwargs["limit_level"] is None
        # Verify trade monitor started
        mock_trade_monitor.monitor_trade.assert_called_once_with(
            "MOCK_DEAL_ID",
            epic,
            entry_price=7501.0,  # Actual fill
            stop_loss=7450.0,  # Original Stop
            atr=10.0,
            use_trailing_stop=True,
        )

        # 6. Simulate Price Movement for Trailing Stop
        # Trade is open. Monitor is running.
        # We need to simulate the fetch_open_position calls made by the monitor.
        # The monitor polls fetch_open_position_by_deal_id
        # We simulate:
        # - Initial state (entry)
        # - Move to profit > 1.5R (1.5 * 50 = 75). Price > 7575.
        # - Monitor should update stop to Breakeven, then Trail.

        # Setup side effects for fetch_open_position
        # Note: Deal ID is MOCK_DEAL_ID because mock_ig_client.place_spread_bet_order returns that.
        initial_pos = {
            "dealId": "MOCK_DEAL_ID",
            "direction": "BUY",
            "bid": 7500,
            "offer": 7501,
            "stopLevel": 7450,
        }
        profit_pos = {
            "dealId": "MOCK_DEAL_ID",
            "direction": "BUY",
            "bid": 7580,  # +80 pts profit
            "offer": 7581,
            "stopLevel": 7450,  # Stop hasn't moved yet on server
        }

        # The monitor calls fetch_open_position in a loop.
        # We provide a sequence: 10x Initial, 10x Profit
        mock_ig_client.fetch_open_position_by_deal_id.side_effect = [
            initial_pos
        ] * 10 + [profit_pos] * 50

        # Wait for monitor to poll and update
        start_wait = time.time()
        while (
            not mock_ig_client.update_open_position.called
            and (time.time() - start_wait) < 5.0
        ):
            real_sleep(0.1)

        # Verify Stop Update was attempted
        assert mock_ig_client.update_open_position.called

        # 7. Simulate Trade Closure
        # Mock history to return matching PnL
        mock_history_df = pd.DataFrame(
            [
                {
                    "profitAndLoss": "£500.0",
                    "closeLevel": 7550.0,
                    "date": datetime.now().isoformat(),
                    "dealId": "MOCK_DEAL_ID",
                }
            ]
        )
        mock_ig_client.fetch_transaction_history_by_deal_id.return_value = (
            mock_history_df
        )

        # Monitor checks stream. We simulate a trade update message.
        mock_stream_manager.simulate_trade_update(
            {
                "dealId": "MOCK_DEAL_ID",
                "status": "CLOSED",
                "level": 7550.0,
                "profitAndLoss": 500.0,
            }
        )

        # Wait for thread to finish
        trade_execution_thread.join(timeout=2.0)
        assert not trade_execution_thread.is_alive()

    # 8. Final DB Verification
    # Check if trade log was updated to CLOSED
    from src.database import fetch_trade_data

    trade_data = fetch_trade_data("MOCK_DEAL_ID", db_path=db_path)
    assert trade_data is not None
    assert trade_data["log"]["outcome"] == "CLOSED"
    assert trade_data["log"]["pnl"] == 500.0
