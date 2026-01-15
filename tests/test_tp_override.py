import pytest
from unittest.mock import MagicMock
import pandas as pd
from src.trade_executor import TradeExecutor
from src.gemini_analyst import TradingSignal, Action, EntryType


@pytest.fixture
def mock_deps():
    mock_client = MagicMock()
    mock_logger = MagicMock()
    mock_monitor = MagicMock()

    # Mock account info
    mock_client.get_account_info.return_value = pd.DataFrame(
        {"accountId": ["ACC1"], "available": [10000.0]}
    )
    mock_client.service.account_id = "ACC1"

    # Mock successful placement
    mock_client.place_spread_bet_order.return_value = {
        "dealId": "DEAL123",
        "level": 100.0,
    }

    return mock_client, mock_logger, mock_monitor


def test_tp_overridden_when_trailing_stop(mock_deps):
    """
    Verifies that take_profit is overridden to None (no limit level)
    when use_trailing_stop is True.
    """
    mock_client, mock_logger, mock_monitor = mock_deps
    executor = TradeExecutor(mock_client, mock_logger, mock_monitor)

    # Create a plan with use_trailing_stop=True and a finite TP
    plan = TradingSignal(
        ticker="FTSE100",
        action=Action.BUY,
        entry=7500.0,
        stop_loss=7450.0,
        take_profit=7600.0,  # Should be ignored
        confidence="high",
        reasoning="Test Trailing",
        size=1.0,
        atr=10.0,
        use_trailing_stop=True,  # Key flag
        entry_type=EntryType.INSTANT,
    )

    # Execute
    executor.execute_trade(
        plan, trigger_price=plan.entry, current_spread=1.0, row_id=1, dry_run=False
    )

    # Verify limit_level was None
    mock_client.place_spread_bet_order.assert_called_once()
    assert mock_client.place_spread_bet_order.call_args[1]["limit_level"] is None


def test_tp_preserved_when_no_trailing_stop(mock_deps):
    """
    Verifies that take_profit is passed as limit_level
    when use_trailing_stop is False.
    """
    mock_client, mock_logger, mock_monitor = mock_deps
    executor = TradeExecutor(mock_client, mock_logger, mock_monitor)

    # Create a plan with use_trailing_stop=False and a finite TP
    plan = TradingSignal(
        ticker="FTSE100",
        action=Action.BUY,
        entry=7500.0,
        stop_loss=7450.0,
        take_profit=7600.0,  # Should be preserved
        confidence="high",
        reasoning="Test Fixed TP",
        size=1.0,
        atr=10.0,
        use_trailing_stop=False,  # Key flag
        entry_type=EntryType.INSTANT,
    )

    # Execute
    executor.execute_trade(
        plan, trigger_price=plan.entry, current_spread=1.0, row_id=1, dry_run=False
    )

    # Verify limit_level was 7600.0
    mock_client.place_spread_bet_order.assert_called_once()
    assert mock_client.place_spread_bet_order.call_args[1]["limit_level"] == 7600.0
