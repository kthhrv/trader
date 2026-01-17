import pytest
import time
from unittest.mock import MagicMock, patch, ANY
import pandas as pd
from src.strategy_engine import StrategyEngine
from src.gemini_analyst import Action, TradingSignal, EntryType


@pytest.fixture
def mock_components():
    with (
        patch("src.strategy_engine.IGClient") as mock_client_cls,
        patch("src.strategy_engine.GeminiAnalyst") as mock_analyst_cls,
        patch("src.strategy_engine.NewsFetcher") as mock_news_cls,
        patch("src.strategy_engine.TradeLoggerDB") as mock_trade_logger_cls,
        patch("src.strategy_engine.TradeMonitorDB") as mock_trade_monitor_cls,
        patch("src.strategy_engine.MarketStatus") as mock_market_status_cls,
        patch("src.strategy_engine.StreamManager") as mock_stream_manager_cls,
    ):
        mock_client = mock_client_cls.return_value
        _ = mock_analyst_cls.return_value
        _ = mock_news_cls.return_value  # Unused
        mock_trade_logger = mock_trade_logger_cls.return_value
        _ = mock_trade_monitor_cls.return_value  # Unused
        mock_market_status = mock_market_status_cls.return_value
        mock_stream_manager = mock_stream_manager_cls.return_value

        mock_market_status.is_holiday.return_value = False
        mock_client.service = MagicMock()
        mock_client.service.account_id = "TEST_ACC_ID"

        yield (
            mock_client,
            mock_trade_logger,
            mock_stream_manager,
        )


def test_slippage_reduces_size_fixed_stop_loss(mock_components, caplog):
    """
    Verifies that if price has moved past target (slippage), the trade is still entered
    with the ORIGINAL Stop Loss, but the position size is REDUCED to maintain constant monetary risk.
    """
    mock_client, mock_trade_logger, mock_stream_manager = mock_components

    engine = StrategyEngine(
        "IX.D.NIKKEI.DAILY.IP",
        max_spread=20.0,
        ig_client=mock_client,
        trade_logger=mock_trade_logger,
        stream_manager=mock_stream_manager,
    )
    # Prevent premature expiration
    engine.last_analysis_time = time.time()

    # Setup plan: Entry 1000, Stop 900. Risk Distance = 100 pts.
    # Initial risk amount calculation (mocked balance 10000 * 1% = 100) -> Size = 1.0
    plan = TradingSignal(
        ticker="NIKKEI",
        action=Action.BUY,
        entry=1000.0,
        stop_loss=900.0,
        take_profit=1200.0,
        confidence="high",
        reasoning="Test Slippage",
        size=1,
        atr=50.0,
        entry_type=EntryType.INSTANT,
        use_trailing_stop=True,
    )
    engine.active_plan = plan
    engine.active_plan_id = 123

    # Mock trigger at 1050 (50 points slippage)
    # New Risk Distance = 1050 - 900 = 150 pts.
    # New Size = 100 / 150 = 0.666... -> 0.67
    def trigger_price_update(seconds):
        # Provide price update
        engine._stream_price_update_handler(
            {
                "epic": engine.epic,
                "bid": 1045.0,
                "offer": 1050.0,
            }
        )
        # Advance time significantly to exit loops
        current_time[0] += 2.0
        return None

    current_time = [100.0]

    def mock_time():
        return current_time[0]

    mock_client.place_spread_bet_order.return_value = {"dealId": "OK", "level": 1050.0}

    # Mock account info for size calculation
    # Balance 10000, 1% risk = 100 currency units.
    mock_client.get_account_info.return_value = pd.DataFrame(
        {"accountId": ["TEST_ACC_ID"], "balance": [10000.0], "available": [10000.0]}
    )
    with (
        patch("time.sleep", side_effect=trigger_price_update),
        patch("time.time", side_effect=mock_time),
    ):
        engine.execute_strategy(timeout_seconds=5.0)

    # Verify Trade was Placed
    assert engine.position_open is True

    # CRITICAL CHECKS:
    # 1. Was the stop loss KEPT at 900.0? (Fixed Structural Level)
    # 2. Was the size REDUCED to maintain risk?
    # Risk distance: 1050 - 900 = 150 pts.
    # Size: 100 / 150 = 0.666... -> 0.67
    mock_client.place_spread_bet_order.assert_called_once_with(
        epic=ANY,
        direction="BUY",
        size=0.67,
        level=1050.0,
        stop_level=900.0,  # FIXED!
        limit_level=None,
    )

    # Verify DB update reflects the actual fill and reduced size
    mock_trade_logger.update_trade_status.assert_any_call(
        row_id=123,
        outcome="LIVE_PLACED",
        deal_id="OK",
        size=0.67,
        entry=1050.0,
        stop_loss=900.0,
    )
