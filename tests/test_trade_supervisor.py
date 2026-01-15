import pytest
from unittest.mock import MagicMock, patch
from src.trade_supervisor import TradeSupervisor, ActiveTrade


@pytest.fixture
def mock_deps():
    mock_client = MagicMock()
    mock_stream = MagicMock()
    mock_status = MagicMock()
    # Mock the helper inside supervisor
    mock_monitor_helper = MagicMock()

    return mock_client, mock_stream, mock_status, mock_monitor_helper


def test_register_trade(mock_deps):
    mock_client, mock_stream, mock_status, _ = mock_deps
    supervisor = TradeSupervisor(mock_client, mock_stream, mock_status)

    # Mock fetching direction
    mock_client.fetch_open_position_by_deal_id.return_value = {"direction": "SELL"}

    supervisor.register_trade(
        deal_id="DEAL1",
        epic="EPIC",
        entry_price=100.0,
        stop_loss=110.0,
        atr=5.0,
        use_trailing_stop=True,
    )

    assert "DEAL1" in supervisor.active_trades
    trade = supervisor.active_trades["DEAL1"]
    assert trade.direction == "SELL"
    assert trade.entry_price == 100.0

    mock_client.fetch_open_position_by_deal_id.assert_called_once_with("DEAL1")


def test_monitor_loop_manages_trade(mock_deps):
    mock_client, mock_stream, mock_status, _ = mock_deps
    supervisor = TradeSupervisor(
        mock_client, mock_stream, mock_status, poll_interval=0.1
    )

    # Inject a trade directly
    trade = ActiveTrade("DEAL1", "EPIC", 100.0, 90.0, 5.0, True, "BUY")
    supervisor.active_trades["DEAL1"] = trade

    # Mock position fetch (Active)
    mock_client.fetch_open_position_by_deal_id.return_value = {
        "stopLevel": 91.0,
        "bid": 105.0,
        "offer": 106.0,
    }

    # Mock helper method via patching because it's created in __init__
    with patch.object(supervisor, "monitor_helper") as mock_helper:
        # Run one iteration of logic manually (to avoid thread timing issues in test)
        is_active = supervisor._manage_single_trade(trade)

        assert is_active is True
        mock_client.fetch_open_position_by_deal_id.assert_called_with("DEAL1")

        # Verify trailing check called
        mock_helper.check_and_update_trailing_stop.assert_called_once_with(
            deal_id="DEAL1",
            direction="BUY",
            entry_price=100.0,
            current_stop=91.0,
            current_bid=105.0,
            current_offer=106.0,
            atr=5.0,
        )


def test_monitor_loop_detects_closure(mock_deps):
    mock_client, mock_stream, mock_status, _ = mock_deps
    supervisor = TradeSupervisor(mock_client, mock_stream, mock_status)

    trade = ActiveTrade("DEAL1", "EPIC", 100.0, 90.0, 5.0, True, "BUY")
    supervisor.active_trades["DEAL1"] = trade

    # Mock position fetch (Closed/None)
    mock_client.fetch_open_position_by_deal_id.return_value = None

    with patch.object(supervisor, "monitor_helper") as mock_helper:
        is_active = supervisor._manage_single_trade(trade)

        assert is_active is False
        # Verify closure logic triggered
        mock_helper._update_db_from_history.assert_called_once_with("DEAL1", 100.0)


def test_start_stop_thread(mock_deps):
    mock_client, mock_stream, mock_status, _ = mock_deps
    supervisor = TradeSupervisor(
        mock_client, mock_stream, mock_status, poll_interval=0.01
    )

    assert not supervisor.is_running
    supervisor.start()
    assert supervisor.is_running
    assert supervisor.thread.is_alive()

    supervisor.stop()
    assert not supervisor.is_running
    assert not supervisor.thread.is_alive()
