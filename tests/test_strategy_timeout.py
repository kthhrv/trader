import unittest
from unittest.mock import MagicMock
import os
import uuid

from src.strategy_engine import StrategyEngine
from src.gemini_analyst import TradingSignal, Action, EntryType
from src.database import init_db, get_db_connection
from src.trade_logger_db import TradeLoggerDB


class TestStrategyTimeout(unittest.TestCase):
    def setUp(self):
        self.test_db_path = f"/home/keith/.gemini/tmp/{uuid.uuid4().hex}.db"
        init_db(self.test_db_path)
        self.mock_ig_client = MagicMock()
        self.mock_analyst = MagicMock()
        self.trade_logger = TradeLoggerDB(db_path=self.test_db_path)
        self.mock_stream_manager = MagicMock()

        self.engine = StrategyEngine(
            epic="CS.D.GBPUSD.TODAY.IP",
            ig_client=self.mock_ig_client,
            analyst=self.mock_analyst,
            trade_logger=self.trade_logger,
            stream_manager=self.mock_stream_manager,
        )

        # Set up a dummy active plan
        self.engine.active_plan = TradingSignal(
            ticker="GBPUSD",
            action=Action.BUY,
            entry=1.3000,
            stop_loss=1.2900,
            take_profit=1.3200,
            size=0.5,
            atr=0.0010,
            use_trailing_stop=False,
            confidence="high",
            reasoning="Test",
            entry_type=EntryType.INSTANT,
        )
        self.engine.active_plan_id = None

    def tearDown(self):
        os.remove(self.test_db_path)

    def test_timeout_logging(self):
        # Run execution with a very short timeout
        self.engine.execute_strategy(timeout_seconds=0.01)

        # Verify trade was logged as TIMED_OUT
        conn = get_db_connection(self.test_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trade_log WHERE outcome = ?", ("TIMED_OUT",))
        timed_out_trade = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(timed_out_trade)
        self.assertEqual(timed_out_trade["outcome"], "TIMED_OUT")
        # Deal ID should be None/Null now
        self.assertIsNone(timed_out_trade["deal_id"])
        self.assertEqual(timed_out_trade["spread_at_entry"], 0.0)
