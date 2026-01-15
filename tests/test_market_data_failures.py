import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
from src.market_data_provider import MarketDataProvider, MarketDataError
from src.strategy_engine import StrategyEngine


@pytest.fixture
def mock_deps():
    mock_client = MagicMock()
    mock_news = MagicMock()
    return mock_client, mock_news


def test_daily_data_failure_raises_error(mock_deps):
    mock_client, mock_news = mock_deps
    provider = MarketDataProvider(mock_client, mock_news)

    # Simulate daily data failure (empty dataframe)
    mock_client.fetch_historical_data.side_effect = [pd.DataFrame()]

    with pytest.raises(MarketDataError, match="Critical: Failed to fetch daily data"):
        provider.get_market_context("EPIC")


def test_15m_data_failure_raises_error(mock_deps):
    mock_client, mock_news = mock_deps
    provider = MarketDataProvider(mock_client, mock_news)

    # Simulate daily success, but 15m failure
    df_daily = pd.DataFrame({"close": [100] * 10})
    mock_client.fetch_historical_data.side_effect = [
        df_daily,
        pd.DataFrame(),  # 15m empty
    ]

    with pytest.raises(MarketDataError, match="Critical: Failed to fetch 15m data"):
        provider.get_market_context("EPIC")


def test_news_failure_raises_error(mock_deps):
    mock_client, mock_news = mock_deps
    provider = MarketDataProvider(mock_client, mock_news)

    # Simulate all price data success
    df_daily = pd.DataFrame({"close": [100] * 10})
    df_15m = pd.DataFrame({"close": [100] * 50})
    df_5m = pd.DataFrame({"close": [100] * 24})
    df_1m = pd.DataFrame({"close": [100] * 15})

    mock_client.fetch_historical_data.side_effect = [df_daily, df_15m, df_5m, df_1m]

    # Simulate news failure (None or Error string)
    mock_news.fetch_news.return_value = None

    with pytest.raises(MarketDataError, match="Critical: Failed to fetch news"):
        provider.get_market_context("EPIC")


def test_strategy_engine_aborts_on_market_data_error():
    # Setup
    with (
        patch("src.strategy_engine.MarketDataProvider") as MockProvider,
        patch("src.strategy_engine.IGClient") as MockIG,
        patch("src.strategy_engine.GeminiAnalyst") as MockAnalyst,
    ):
        mock_provider_instance = MockProvider.return_value
        mock_provider_instance.get_market_context.side_effect = MarketDataError(
            "Simulated Failure"
        )

        mock_analyst_instance = MockAnalyst.return_value

        # We must inject the provider or mock the class used inside
        # Since StrategyEngine instantiates MarketDataProvider inside __init__ if not provided,
        # but the test uses dependency injection via mocks if feasible.
        # However, StrategyEngine takes `data_provider` NOT as an init arg, but creates it.
        # Wait, looking at StrategyEngine __init__:
        # self.data_provider = MarketDataProvider(self.client, self.news_fetcher)
        # So we must patch the class `src.strategy_engine.MarketDataProvider` BEFORE init.

        engine = StrategyEngine(
            epic="EPIC", ig_client=MockIG(), analyst=mock_analyst_instance
        )
        # Verify our mock was used
        assert engine.data_provider == mock_provider_instance

        # Action
        engine._run_analysis()

        # Verification
        # 1. Analyst should NOT be called (Fail Fast)
        mock_analyst_instance.analyze_market.assert_not_called()

        # 2. Active plan should be None
        assert engine.active_plan is None
