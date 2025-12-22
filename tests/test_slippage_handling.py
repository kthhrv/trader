import unittest
from unittest.mock import MagicMock, ANY
from src.strategy_engine import StrategyEngine
from src.gemini_analyst import TradingSignal, Action, EntryType
from tests.mocks import (
    MockIGClient,
    MockGeminiAnalyst,
    MockNewsFetcher,
    MockMarketStatus,
    MockStreamManager,
)


class TestSlippageHandling(unittest.TestCase):
    def setUp(self):
        # Setup mocks
        self.mock_client = MockIGClient()
        self.mock_analyst = MockGeminiAnalyst()
        self.mock_news = MockNewsFetcher()
        self.mock_market_status = MockMarketStatus()
        self.mock_trade_logger = MagicMock()
        self.mock_trade_monitor = MagicMock()
        self.mock_stream_manager = MockStreamManager()

        # Initialize StrategyEngine with injected mocks
        self.engine = StrategyEngine(
            epic="IX.D.FTSE.DAILY.IP",
            strategy_name="TEST_STRATEGY",
            ig_client=self.mock_client,
            analyst=self.mock_analyst,
            news_fetcher=self.mock_news,
            trade_logger=self.mock_trade_logger,
            trade_monitor=self.mock_trade_monitor,
            market_status=self.mock_market_status,
            stream_manager=self.mock_stream_manager,
            dry_run=False,
        )

    def test_slippage_capture(self):
        """
        Test that when the actual execution price differs from the plan (slippage),
        the actual price is used for DB update and monitoring.
        """
        # 1. Define a Plan
        planned_entry = 7500.0
        actual_fill = 7490.0  # Slippage of 10 points

        plan = TradingSignal(
            ticker="FTSE100",
            action=Action.SELL,
            entry=planned_entry,
            stop_loss=7520.0,
            take_profit=7450.0,
            confidence="high",
            reasoning="Bearish setup",
            size=1.0,
            atr=10.0,
            entry_type=EntryType.INSTANT,
            use_trailing_stop=True,
        )
        self.engine.active_plan = plan

        # Simulate PENDING log creation
        self.engine.active_plan_id = 123

        # 2. Configure Mock Client to return Actual Fill
        self.mock_client.place_spread_bet_order.return_value = {
            "dealId": "DEAL_XYZ",
            "dealStatus": "ACCEPTED",
            "level": str(
                actual_fill
            ),  # IG returns levels as strings sometimes, or we should handle it
            "reason": "SUCCESS",
        }

        # 3. Simulate Price Trigger (Instant Entry logic)
        # We need to trigger the loop in execute_strategy.
        # But for this test, we can directly call _place_market_order
        # to isolate the logic we care about (the handling of the confirmation).
        # _place_market_order is where the fix was applied.

        current_spread = 1.0
        success = self.engine._place_market_order(plan, current_spread, dry_run=False)

        self.assertTrue(success, "Market order should have been placed successfully")

        # 4. Assertions

        # A) Check that TradeLoggerDB.update_trade_status was called with the ACTUAL fill
        self.mock_trade_logger.update_trade_status.assert_called_with(
            row_id=123,
            outcome="LIVE_PLACED",
            deal_id="DEAL_XYZ",
            size=ANY,
            entry=actual_fill,  # THE CRITICAL ASSERTION
        )

        # B) Check that TradeMonitorDB.monitor_trade was called with the ACTUAL fill
        self.mock_trade_monitor.monitor_trade.assert_called_with(
            "DEAL_XYZ",
            "IX.D.FTSE.DAILY.IP",
            entry_price=actual_fill,  # THE CRITICAL ASSERTION
            stop_loss=plan.stop_loss,
            atr=plan.atr,
            use_trailing_stop=plan.use_trailing_stop,
        )

    def test_missing_fill_level_fallback(self):
        """
        Test fallback behavior when 'level' is missing from confirmation.
        Should revert to using planned entry.
        """
        planned_entry = 7500.0

        plan = TradingSignal(
            ticker="FTSE100",
            action=Action.SELL,
            entry=planned_entry,
            stop_loss=7520.0,
            take_profit=7450.0,
            confidence="high",
            reasoning="Bearish setup",
            size=1.0,
            atr=10.0,
            entry_type=EntryType.INSTANT,
            use_trailing_stop=True,
        )
        self.engine.active_plan = plan
        self.engine.active_plan_id = 124

        # Return confirmation WITHOUT 'level'
        self.mock_client.place_spread_bet_order.return_value = {
            "dealId": "DEAL_ABC",
            "dealStatus": "ACCEPTED",
            # "level": missing
        }

        success = self.engine._place_market_order(plan, 1.0, dry_run=False)
        self.assertTrue(success)

        # Assert Fallback to Planned Entry
        self.mock_trade_logger.update_trade_status.assert_called_with(
            row_id=124,
            outcome="LIVE_PLACED",
            deal_id="DEAL_ABC",
            size=ANY,
            entry=planned_entry,  # FALLBACK ASSERTION
        )

        self.mock_trade_monitor.monitor_trade.assert_called_with(
            "DEAL_ABC",
            "IX.D.FTSE.DAILY.IP",
            entry_price=planned_entry,  # FALLBACK ASSERTION
            stop_loss=plan.stop_loss,
            atr=plan.atr,
            use_trailing_stop=plan.use_trailing_stop,
        )


if __name__ == "__main__":
    unittest.main()
