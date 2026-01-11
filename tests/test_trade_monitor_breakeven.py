import unittest
from unittest.mock import MagicMock, patch
from src.trade_monitor_db import TradeMonitorDB
from src.market_status import MarketStatus


class TestTradeMonitorBreakeven(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_stream_manager = MagicMock()
        self.mock_market_status = MagicMock(spec=MarketStatus)
        self.monitor = TradeMonitorDB(
            self.mock_client,
            self.mock_stream_manager,
            market_status=self.mock_market_status,
        )

    @patch("src.trade_monitor_db.BREAKEVEN_TRIGGER_R", 1.5)
    @patch("src.trade_monitor_db.update_trade_stop_loss")
    @patch("src.trade_monitor_db.update_trade_outcome")
    def test_monitor_trade_moves_to_breakeven(
        self, mock_update_outcome, mock_update_sl
    ):
        deal_id = "DEAL123"
        entry_price = 100.0
        stop_loss = 90.0  # Risk = 10.0

        # 1.5R profit = 100 + 15 = 115.0

        # Ensure market status returns None to skip time logic
        self.mock_market_status.get_market_close_datetime.return_value = None

        # Mock position data showing profit at 1.5R
        self.mock_client.fetch_open_position_by_deal_id.return_value = {
            "direction": "BUY",
            "bid": 115.0,
            "offer": 115.5,
            "stopLevel": 90.0,
            "dealId": deal_id,
        }

        # Mock history for final closure (to let loop exit)
        mock_history = MagicMock()
        mock_history.empty = True
        self.mock_client.fetch_transaction_history_by_deal_id.return_value = (
            mock_history
        )

        # Setup event to signal trade closure (so monitor loop exits after 1 run)
        # However, the loop check is at the top. We need to run it once.
        # We can mock time.sleep to raise an exception after the first call.

        call_count = [0]

        def sleep_effect(seconds):
            call_count[0] += 1
            if call_count[0] >= 1:
                # Signal closure to stop the loop
                self.monitor._active_monitors[deal_id].set()

        with patch("time.sleep", side_effect=sleep_effect):
            self.monitor.monitor_trade(
                deal_id=deal_id,
                epic="TEST",
                entry_price=entry_price,
                stop_loss=stop_loss,
                use_trailing_stop=True,
                polling_interval=0.01,
            )

        # Verify update_open_position was called with breakeven price (100.0)
        self.mock_client.update_open_position.assert_called_with(
            deal_id, stop_level=100.0
        )
        mock_update_sl.assert_called_with(deal_id, 100.0, None)


if __name__ == "__main__":
    unittest.main()
