import unittest
from unittest.mock import patch, MagicMock
import threading
import time
import json
from datetime import datetime, timedelta, timezone
from src.trade_monitor_db import TradeMonitorDB
from src.market_status import MarketStatus


class TestTradeMonitorDB(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_stream_manager = MagicMock()  # Add mock stream_manager
        self.mock_market_status = MagicMock(spec=MarketStatus)
        self.monitor = TradeMonitorDB(
            self.mock_client,
            self.mock_stream_manager,
            market_status=self.mock_market_status,
        )

    @patch("src.trade_monitor_db.time.sleep")  # Patch sleep to skip retry loop delays
    @patch("src.trade_monitor_db.update_trade_outcome")
    def test_monitor_trade_flow(self, mock_update_db, mock_sleep):
        deal_id = "DEAL123"

        # Disable market close check for this test
        self.mock_market_status.get_market_close_datetime.return_value = None

        # Setup IG Client mock for PnL fetch
        mock_history_df = MagicMock()
        mock_history_df.empty = False
        mock_history_df.columns = ["profitAndLoss", "closeLevel"]
        mock_history_df.iloc.__getitem__.return_value = {
            "profitAndLoss": "£50.5",
            "closeLevel": 105.0,
        }
        self.mock_client.fetch_transaction_history_by_deal_id.return_value = (
            mock_history_df
        )

        # We need to run monitor_trade in a thread because it blocks waiting for event
        monitor_thread = threading.Thread(
            target=self.monitor.monitor_trade,
            args=(deal_id, "EPIC"),
            kwargs={"polling_interval": 0.1},
        )
        monitor_thread.start()

        time.sleep(0.1)  # Give it time to start and register callback

        # Verify it subscribed
        self.mock_stream_manager.subscribe_trade_updates.assert_called_once()

        # Trigger the closure via callback
        close_payload = json.dumps(
            {
                "dealId": deal_id,
                "status": "CLOSED",
                "level": 105.0,
                "profitAndLoss": 50.5,
            }
        )
        self.monitor._handle_trade_update(
            {"type": "trade_update", "payload": close_payload}
        )

        # Join thread (should finish now that event is set)
        monitor_thread.join(timeout=2.0)
        self.assertFalse(monitor_thread.is_alive(), "Monitor thread failed to exit")

        # Check DB update
        mock_update_db.assert_called_once()
        args = mock_update_db.call_args[0]
        # deal_id, exit_price, pnl, exit_time, outcome, db_path
        self.assertEqual(args[0], deal_id)
        self.assertEqual(args[1], 105.0)
        self.assertEqual(args[2], 50.5)
        # outcome is passed as a keyword argument in the actual code call,
        # but let's check how it's captured.
        # The code is: update_trade_outcome(..., outcome=status, ...)
        # args might only contain positional args if outcome was passed as kwarg
        # Let's check call_args kwargs
        kwargs = mock_update_db.call_args[1]
        self.assertEqual(kwargs["outcome"], "CLOSED")

    @patch("src.trade_monitor_db.update_trade_outcome")
    def test_update_db_exception(self, mock_update_db):
        mock_update_db.side_effect = Exception("DB Fail")
        # Should catch exception and log error, not crash
        self.monitor._update_db("DEAL", 100, 50, "time", "CLOSED")
        mock_update_db.assert_called_once()

    @patch("src.trade_monitor_db.logger")
    def test_handle_trade_update_logs_and_continues(self, mock_logger):
        deal_id = "DEAL123"
        self.monitor._active_monitors[deal_id] = threading.Event()

        # Simulate UPDATED event
        update_payload = json.dumps(
            {
                "dealId": deal_id,
                "status": "UPDATED",
                "level": 105.0,
                "profitAndLoss": 50.5,
            }
        )

        self.monitor._handle_trade_update(
            {"type": "trade_update", "payload": update_payload}
        )

        # Verify it logged the update
        mock_logger.info.assert_any_call(
            f"STREAM: Trade {deal_id} detected as UPDATED via streaming update."
        )

        # Verify the event is NOT set (monitoring continues)
        self.assertFalse(
            self.monitor._active_monitors[deal_id].is_set(),
            "Monitor event should not be set on UPDATED status",
        )

    def test_handle_trade_close_terminates(self):
        deal_id = "DEAL123"
        self.monitor._active_monitors[deal_id] = threading.Event()

        # Simulate CLOSED event
        close_payload = json.dumps(
            {
                "dealId": deal_id,
                "status": "CLOSED",
                "level": 105.0,
                "profitAndLoss": 50.5,
            }
        )

        self.monitor._handle_trade_update(
            {"type": "trade_update", "payload": close_payload}
        )

        # Verify the event IS set (monitoring stops)
        self.assertTrue(
            self.monitor._active_monitors[deal_id].is_set(),
            "Monitor event should be set on CLOSED status",
        )

    @patch("src.trade_monitor_db.time.sleep")
    @patch("src.trade_monitor_db.update_trade_outcome")
    def test_monitor_trade_forces_exit_near_market_close(
        self, mock_update_db, mock_sleep
    ):
        deal_id = "DEAL_CLOSE"
        epic = "IX.D.FTSE.DAILY.IP"

        # Mock Market Status to return a time 2 minutes from "now"
        # We need to ensure timezone awareness matches
        tz = timezone.utc
        now = datetime.now(tz)
        close_time = now + timedelta(minutes=2)

        self.mock_market_status.get_market_close_datetime.return_value = close_time

        # Mock Open Position to return valid data for closing
        self.mock_client.fetch_open_position_by_deal_id.return_value = {
            "direction": "BUY",
            "size": 2.5,
            "dealId": deal_id,
        }

        # Run monitor in a thread
        monitor_thread = threading.Thread(
            target=self.monitor.monitor_trade,
            args=(deal_id, epic),
            kwargs={"polling_interval": 0.1},
        )
        monitor_thread.start()

        # Wait enough time for the loop to run at least once and check time
        # Since we patched sleep, it should cycle fast.
        # We need to stop the thread after the check.
        # The logic calls close_open_position then sleeps 2s then checks event.
        # To terminate the test cleanly, we can set the event manually after a short delay
        # OR we can verify the call happened and then set the event.

        start_wait = time.time()
        call_found = False
        while time.time() - start_wait < 2.0:
            if self.mock_client.close_open_position.called:
                call_found = True
                break
            time.sleep(0.1)

        # Signal the thread to exit
        if deal_id in self.monitor._active_monitors:
            self.monitor._active_monitors[deal_id].set()

        monitor_thread.join(timeout=1.0)

        self.assertTrue(call_found, "close_open_position was not called")

        # Verify arguments - epic and expiry should be None
        self.mock_client.close_open_position.assert_called_with(
            deal_id, "SELL", 2.5, epic=None, expiry=None
        )


if __name__ == "__main__":
    unittest.main()
