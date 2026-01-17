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

    # Mock account info with both balance and available
    mock_client.get_account_info.return_value = pd.DataFrame(
        {"accountId": ["ACC1"], "balance": [10000.0], "available": [10000.0]}
    )
    mock_client.service.account_id = "ACC1"

    return mock_client, mock_logger, mock_monitor


def test_execute_trade_success(mock_deps):
    mock_client, mock_logger, mock_monitor = mock_deps
    executor = TradeExecutor(mock_client, mock_logger, mock_monitor)

    plan = TradingSignal(
        ticker="EPIC",
        action=Action.BUY,
        entry=100,
        stop_loss=90,
        take_profit=110,
        size=1.0,
        atr=5.0,
        entry_type=EntryType.INSTANT,
        use_trailing_stop=True,
        confidence="high",
        reasoning="Test",
    )

    mock_client.place_spread_bet_order.return_value = {
        "dealId": "DEAL123",
        "level": 100.5,
    }

    success = executor.execute_trade(
        plan=plan, trigger_price=100.0, current_spread=1.0, row_id=1
    )

    assert success is True
    mock_client.place_spread_bet_order.assert_called_once()
    # Stop adjusted for spread: 90 - 1 = 89
    assert mock_client.place_spread_bet_order.call_args[1]["stop_level"] == 89.0

    mock_monitor.monitor_trade.assert_called_once()
    mock_logger.update_trade_status.assert_called_once()


def test_execute_trade_dry_run(mock_deps):
    mock_client, mock_logger, mock_monitor = mock_deps
    executor = TradeExecutor(mock_client, mock_logger, mock_monitor)

    plan = TradingSignal(
        ticker="EPIC",
        action=Action.SELL,
        entry=100,
        stop_loss=110,
        take_profit=90,
        size=1.0,
        atr=5.0,
        entry_type=EntryType.INSTANT,
        use_trailing_stop=False,
        confidence="high",
        reasoning="Test",
    )

    success = executor.execute_trade(
        plan=plan, trigger_price=100.0, current_spread=1.0, row_id=1, dry_run=True
    )

    assert success is True
    mock_client.place_spread_bet_order.assert_not_called()
    mock_logger.update_trade_status.assert_called_once()
    assert mock_logger.update_trade_status.call_args[1]["outcome"] == "DRY_RUN_PLACED"


def test_calculate_size_risk_floor(mock_deps):
    mock_client, mock_logger, mock_monitor = mock_deps
    executor = TradeExecutor(mock_client, mock_logger, mock_monitor)

    # Balance 10000. Risk 1% = 100.
    # Stop distance 10.
    # Size = 100 / 10 = 10.0
    size = executor._calculate_size(100, 90)
    assert size == 10.0


def test_calculate_size_margin_conflict(mock_deps):
    """
    Test scenario where Balance is high but Available is low (e.g. existing trade margin).
    The Floor check should pass against Balance, but the liquidity check should fail against Available.
    """
    mock_client, mock_logger, mock_monitor = mock_deps
    executor = TradeExecutor(mock_client, mock_logger, mock_monitor)

    # 1. Available = 0 (Total lockup)
    mock_client.get_account_info.return_value = pd.DataFrame(
        {"accountId": ["ACC1"], "balance": [10000.0], "available": [0.0]}
    )

    size = executor._calculate_size(100, 90)
    assert size == 0.0  # Aborted due to liquidity

    # 2. Available > 0 but below Floor (e.g. 1000)
    # The code should now ALLOW this because balance is still 10000.
    mock_client.get_account_info.return_value = pd.DataFrame(
        {"accountId": ["ACC1"], "balance": [10000.0], "available": [1000.0]}
    )

    size = executor._calculate_size(100, 90)
    assert size == 10.0  # Allowed because balance is high
