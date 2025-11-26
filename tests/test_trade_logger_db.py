import unittest
from unittest.mock import patch, MagicMock
import sqlite3
from src.trade_logger_db import TradeLoggerDB
from src.gemini_analyst import TradingSignal, Action

class TestTradeLoggerDB(unittest.TestCase):
    
    @patch('src.trade_logger_db.init_db')
    @patch('src.trade_logger_db.get_db_connection')
    def test_log_trade(self, mock_get_conn, mock_init_db):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_conn.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        logger = TradeLoggerDB()
        mock_init_db.assert_called_once()
        
        plan = TradingSignal(
            ticker="TEST",
            action=Action.BUY,
            entry=100,
            stop_loss=90,
            take_profit=110,
            size=1,
            confidence="high",
            reasoning="test",
            atr=5.0
        )
        
        logger.log_trade("TEST", plan, "SUCCESS", 1.5, False, "DEAL123")
        
        mock_cursor.execute.assert_called_once()
        args = mock_cursor.execute.call_args[0]
        self.assertIn("INSERT INTO trade_log", args[0])
        params = args[1]
        self.assertEqual(params[1], "TEST") # epic
        self.assertEqual(params[12], "DEAL123") # deal_id
        
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch('src.trade_logger_db.init_db')
    @patch('src.trade_logger_db.get_db_connection')
    def test_log_trade_exception(self, mock_get_conn, mock_init_db):
        mock_get_conn.side_effect = Exception("DB Error")
        
        logger = TradeLoggerDB()
        # Should not raise exception, just log error
        logger.log_trade("TEST", MagicMock(), "FAIL", 0, True) 

if __name__ == '__main__':
    unittest.main()
