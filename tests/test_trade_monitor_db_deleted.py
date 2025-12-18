import unittest
from unittest.mock import patch, MagicMock
import threading
import time
import json
from src.trade_monitor_db import TradeMonitorDB


class TestTradeMonitorDBDeleted(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_stream_manager = MagicMock()
        self.monitor = TradeMonitorDB(self.mock_client, self.mock_stream_manager)

    @patch("src.trade_monitor_db.time.sleep")  # Patch sleep to skip retry delays
    @patch("src.trade_monitor_db.update_trade_outcome")
    def test_monitor_trade_deleted_status(self, mock_update_db, mock_sleep):
        """
        Test that receiving a 'DELETED' status update correctly signals trade closure.
        """
        deal_id = "DEAL_DELETED_TEST"

        # Setup IG Client mock for PnL fetch (simulating history fetch after close)
        mock_history_df = MagicMock()
        mock_history_df.empty = False
        mock_history_df.columns = ["profitAndLoss", "closeLevel"]
        mock_history_df.iloc.__getitem__.return_value = {
            "profitAndLoss": "-10.0",
            "closeLevel": 90.0,
        }
        self.mock_client.fetch_transaction_history_by_deal_id.return_value = (
            mock_history_df
        )

        # Run monitor_trade in a separate thread
        monitor_thread = threading.Thread(
            target=self.monitor.monitor_trade,
            args=(deal_id, "EPIC_TEST"),
            kwargs={"polling_interval": 0.1},
        )
        monitor_thread.start()

        time.sleep(0.1)

        # Simulate receiving a trade update with status 'DELETED'
        # This matches the log: Status: DELETED, DealStatus: ACCEPTED
        delete_payload = json.dumps(
            {"dealId": deal_id, "status": "DELETED", "dealStatus": "ACCEPTED"}
        )

        self.monitor._handle_trade_update(
            {"type": "trade_update", "payload": delete_payload}
        )

        # Join thread - if the DELETED status is handled, this should finish immediately
        monitor_thread.join(timeout=2.0)
        self.assertFalse(
            monitor_thread.is_alive(), "Monitor thread failed to exit on DELETED status"
        )

        # Check DB update was triggered
        mock_update_db.assert_called_once()
        kwargs = mock_update_db.call_args[1]
        self.assertEqual(
            kwargs["outcome"], "CLOSED"
        )  # Our code sets final status to CLOSED even if trigger was DELETED


if __name__ == "__main__":
    unittest.main()
