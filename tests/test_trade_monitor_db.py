import unittest
from unittest.mock import patch, MagicMock
from src.trade_monitor_db import TradeMonitorDB

class TestTradeMonitorDB(unittest.TestCase):
    
    def setUp(self):
        self.mock_client = MagicMock()
        self.monitor = TradeMonitorDB(self.mock_client)

    @patch('src.trade_monitor_db.update_trade_outcome')
    @patch('time.sleep', return_value=None)
    def test_monitor_trade_flow(self, mock_sleep, mock_update_db):
        # Setup IG Client mock behavior
        # 1. Open Position (loop continues)
        # 2. Closed (None) -> loop breaks, update db
        self.mock_client.fetch_open_position_by_deal_id.side_effect = [
            {'profitAndLoss': 10, 'bid': 100, 'offer': 101},
            None
        ]
        self.mock_client.get_market_info.return_value = {'snapshot': {'bid': 102, 'offer': 103}}
        
        # Mock transaction history return
        mock_history_df = MagicMock()
        mock_history_df.empty = False
        mock_history_df.columns = ['profitAndLoss', 'closeLevel']
        mock_history_df.iloc.__getitem__.return_value = {'profitAndLoss': '£50.5', 'closeLevel': 105.0}
        self.mock_client.fetch_transaction_history_by_deal_id.return_value = mock_history_df
        
        self.monitor.monitor_trade("DEAL123", "EPIC", polling_interval=0.1)
        
        # Should be called ONCE: only when status is CLOSED
        mock_update_db.assert_called_once()
        
        # Check args
        args = mock_update_db.call_args[0]
        # deal_id, exit_price, pnl, exit_time, outcome, db_path
        self.assertEqual(args[0], "DEAL123")
        self.assertEqual(args[1], 105.0) # exit_price from history
        self.assertEqual(args[2], 50.5)  # pnl from history
        # outcome is passed as a keyword argument
        kwargs = mock_update_db.call_args[1]
        self.assertEqual(kwargs['outcome'], "CLOSED")

    @patch('src.trade_monitor_db.update_trade_outcome')
    def test_update_db_exception(self, mock_update_db):
        mock_update_db.side_effect = Exception("DB Fail")
        # Should catch exception and log error, not crash
        self.monitor._update_db("DEAL", 100, 50, "time", "CLOSED")
        mock_update_db.assert_called_once()

if __name__ == '__main__':
    unittest.main()