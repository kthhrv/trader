import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
from src.trade_executor import TradeExecutor
from src.ig_client import IGClient


class TestStrategyRiskFloor(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock(spec=IGClient)
        # Mock service.account_id to match our fake data
        self.mock_client.service = MagicMock()
        self.mock_client.service.account_id = "ACC123"

        # Helper to setup account balance
        def set_balance(amount):
            self.mock_client.get_account_info.return_value = pd.DataFrame(
                [
                    {
                        "accountId": "ACC123",
                        "available": amount,
                        "balance": amount,
                        "profitLoss": 0,
                    }
                ]
            )

        self.set_balance = set_balance

        self.executor = TradeExecutor(
            client=self.mock_client,
            logger_db=MagicMock(),
            monitor=MagicMock(),
            risk_scale=1.0,
            min_size=0.5,  # Broker minimum
        )

    @patch("src.trade_executor.RISK_PER_TRADE_PERCENT", 0.01)  # 1% Risk
    @patch("src.trade_executor.MIN_ACCOUNT_BALANCE", 1000.0)  # Floor at £1000
    def test_calculate_size_normal_operation(self):
        """
        Balance: £2000. Floor: £1000. Risk: £20 (1%).
        Projected: £1980 > £1000. No reduction.
        """
        self.set_balance(2000.0)

        # Entry 100, Stop 90 (Dist 10) -> Size = 20 / 10 = 2.0
        size = self.executor._calculate_size(entry=100, stop_loss=90)
        self.assertEqual(size, 2.0)

    @patch("src.trade_executor.RISK_PER_TRADE_PERCENT", 0.01)
    @patch("src.trade_executor.MIN_ACCOUNT_BALANCE", 1000.0)
    def test_calculate_size_approaching_floor_steps_down(self):
        """
        Balance: £1015. Floor: £1000. Stop: 40.
        Standard Risk (1%): £10.15. Size: 0.25.

        Step down to Min (0.5):
        Risk of 0.5: £20.00.
        £1015 - £20.00 = £995.00 (Breaches floor).

        Result: 0.0
        """
        self.set_balance(1015.0)
        size = self.executor._calculate_size(entry=100, stop_loss=60)  # Dist 40
        self.assertEqual(size, 0.0)

    @patch("src.trade_executor.RISK_PER_TRADE_PERCENT", 0.01)
    @patch("src.trade_executor.MIN_ACCOUNT_BALANCE", 1000.0)
    def test_calculate_size_approaching_floor_steps_down_success(self):
        """
        Balance: £1025. Floor: £1000. Stop: 40.
        Standard Risk (1%): £10.25. Size: 0.26.
        Broker Min (0.5): Risk £20.

        Check Standard (max of 0.26, 0.5):
        Risk of 0.5: £20.00.
        £1025 - £20.00 = £1005 (Safe!).

        Result: 0.5 (Standard trade was safe because it was small enough)
        """
        self.set_balance(1025.0)
        size = self.executor._calculate_size(entry=100, stop_loss=60)  # Dist 40
        self.assertEqual(size, 0.5)

    @patch("src.trade_executor.RISK_PER_TRADE_PERCENT", 0.01)
    @patch("src.trade_executor.MIN_ACCOUNT_BALANCE", 8000.0)
    def test_calculate_size_high_floor_step_down(self):
        """
        Balance: £8050. Floor: £8000. Stop: 40.
        Standard Risk (1%): £80.50. Size: 2.01.
        Actual Risk (2.01 * 40): £80.40.
        Safety Check (Standard): 8050 - 80.40 = 7969.60 (BREACH).

        Step-Down to Min (0.5):
        Actual Risk (0.5 * 40): £20.
        Safety Check (Min): 8050 - 20 = 8030 (SAFE).

        Result: 0.5
        """
        self.set_balance(8050.0)
        size = self.executor._calculate_size(entry=100, stop_loss=60)  # Dist 40
        self.assertEqual(size, 0.5)

    @patch("src.trade_executor.RISK_PER_TRADE_PERCENT", 0.01)
    @patch("src.trade_executor.MIN_ACCOUNT_BALANCE", 8000.0)
    def test_calculate_size_high_floor_abort(self):
        """
        Balance: £8015. Floor: £8000. Stop: 40.
        Min Risk (0.5 * 40): £20.
        Safety Check: 8015 - 20 = 7995 (BREACH).

        Result: 0.0
        """
        self.set_balance(8015.0)
        size = self.executor._calculate_size(entry=100, stop_loss=60)  # Dist 40
        self.assertEqual(size, 0.0)


if __name__ == "__main__":
    unittest.main()
