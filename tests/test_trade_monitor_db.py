import unittest
from unittest.mock import patch, MagicMock
from src.trade_monitor_db import TradeMonitorDB

class TestTradeMonitorDB(unittest.TestCase):
    
    def setUp(self):
        self.mock_client = MagicMock()
        self.monitor = TradeMonitorDB(self.mock_client)

    @patch('src.trade_monitor_db.get_db_connection')
    @patch('time.sleep', return_value=None)
    def test_monitor_trade_flow(self, mock_sleep, mock_get_conn):
        # Setup DB mock
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        # Setup IG Client mock behavior
        # 1. Open Position
        # 2. Closed (None)
        self.mock_client.fetch_open_position_by_deal_id.side_effect = [
            {'profitAndLoss': 10, 'bid': 100, 'offer': 101},
            None
        ]
        self.mock_client.get_market_info.return_value = {'snapshot': {'bid': 102, 'offer': 103}}
        self.mock_client.fetch_transaction_history_by_deal_id.return_value = None # Mock history fetch
        
        self.monitor.monitor_trade("DEAL123", "EPIC", polling_interval=0.1)
        
        # Should be called twice: once for open state, once for closed state
        self.assertEqual(mock_cursor.execute.call_count, 2)
        
        # Check first insert (Open)
        args1 = mock_cursor.execute.call_args_list[0][0]
        self.assertIn("INSERT INTO trade_monitor", args1[0])
        self.assertEqual(args1[1][0], "DEAL123")
        self.assertEqual(args1[1][2], 100) # bid
        self.assertEqual(args1[1][5], "OPEN")
        
        # Check second insert (Closed)
        args2 = mock_cursor.execute.call_args_list[1][0]
        self.assertEqual(args2[1][2], 102) # final bid from snapshot
        self.assertEqual(args2[1][5], "CLOSED")

    @patch('src.trade_monitor_db.get_db_connection')
    def test_log_to_db_exception(self, mock_get_conn):
        mock_get_conn.side_effect = Exception("DB Fail")
        # Should catch exception and log error
        self.monitor._log_to_db("DEAL", 1, 2, 3, "OPEN")

if __name__ == '__main__':
    unittest.main()
