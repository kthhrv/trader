import unittest
from src.strategy_engine import StrategyEngine
from tests.mocks import (
    MockIGClient,
    MockGeminiAnalyst,
    MockNewsFetcher,
    MockMarketStatus,
    MockTradeLoggerDB,
    MockTradeMonitorDB,
    MockStreamManager,
)


class TestHolidayOverride(unittest.TestCase):
    def setUp(self):
        self.mock_client = MockIGClient()
        self.mock_analyst = MockGeminiAnalyst()
        self.mock_news = MockNewsFetcher()
        self.mock_market_status = MockMarketStatus()
        self.mock_trade_logger = MockTradeLoggerDB()
        self.mock_trade_monitor = MockTradeMonitorDB()
        self.mock_stream_manager = MockStreamManager()

    def test_holiday_block_default(self):
        """
        Test that strategy execution IS aborted when it IS a holiday and override is False (default).
        """
        # Force is_holiday to return True
        self.mock_market_status.is_holiday.return_value = True

        engine = StrategyEngine(
            epic="IX.D.FTSE.DAILY.IP",
            ig_client=self.mock_client,
            market_status=self.mock_market_status,
            analyst=self.mock_analyst,
            news_fetcher=self.mock_news,
            trade_logger=self.mock_trade_logger,
            trade_monitor=self.mock_trade_monitor,
            stream_manager=self.mock_stream_manager,
            ignore_holidays=False,  # Default behavior
        )

        engine.generate_plan()

        # Should have checked holiday status
        self.mock_market_status.is_holiday.assert_called()

        # Should NOT have proceeded to fetch data (which happens inside generate_plan if not blocked)
        # We can check if client.fetch_historical_data was called.
        self.mock_client.fetch_historical_data.assert_not_called()

    def test_holiday_override_enabled(self):
        """
        Test that strategy execution PROCEEDS even when it IS a holiday, if override is True.
        """
        # Force is_holiday to return True
        self.mock_market_status.is_holiday.return_value = True

        engine = StrategyEngine(
            epic="IX.D.FTSE.DAILY.IP",
            ig_client=self.mock_client,
            market_status=self.mock_market_status,
            analyst=self.mock_analyst,
            news_fetcher=self.mock_news,
            trade_logger=self.mock_trade_logger,
            trade_monitor=self.mock_trade_monitor,
            stream_manager=self.mock_stream_manager,
            ignore_holidays=True,  # OVERRIDE ENABLED
        )

        engine.generate_plan()

        # Should NOT have even checked is_holiday (because short-circuit evaluation),
        # OR it checked but proceeded.
        # My code was: if not self.ignore_holidays and self.market_status.is_holiday(self.epic):
        # So if ignore_holidays is True, 'not self.ignore_holidays' is False, and is_holiday is NOT called.
        self.mock_market_status.is_holiday.assert_not_called()

        # Should HAVE proceeded to fetch data
        self.mock_client.fetch_historical_data.assert_called()


if __name__ == "__main__":
    unittest.main()
