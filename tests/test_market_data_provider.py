import pytest
from unittest.mock import MagicMock
import pandas as pd
from src.market_data_provider import MarketDataProvider


@pytest.fixture
def mock_deps():
    mock_client = MagicMock()
    mock_news = MagicMock()

    # Mock DataFrames
    df_daily = pd.DataFrame({"close": [100] * 10})
    df_15m = pd.DataFrame(
        {
            "open": [100] * 50,
            "high": [105] * 50,
            "low": [95] * 50,
            "close": [102] * 50,
            "volume": [1000] * 50,
        }
    )
    # Set time index for 15m
    df_15m.index = pd.to_datetime(
        [pd.Timestamp.now() - pd.Timedelta(minutes=15 * i) for i in range(50)][::-1]
    )

    df_5m = pd.DataFrame({"close": [102] * 24})
    df_1m = pd.DataFrame({"close": [102] * 15})

    # Setup returns
    mock_client.fetch_historical_data.side_effect = [
        df_daily,  # D
        df_15m,  # 15Min
        df_5m,  # 5Min
        df_1m,  # 1Min
    ]

    # Mock VIX and Sentiment
    mock_client.service.fetch_market_by_epic.return_value = {"snapshot": {"bid": 20.0}}
    mock_client.data_service.fetch_market_by_epic.return_value = {
        "instrument": {"marketId": "123"}
    }
    mock_client.data_service.fetch_client_sentiment_by_instrument.return_value = {
        "longPositionPercentage": 60,
        "shortPositionPercentage": 40,
    }

    mock_news.fetch_news.return_value = "Mock News"

    return mock_client, mock_news


def test_get_market_context(mock_deps):
    mock_client, mock_news = mock_deps
    provider = MarketDataProvider(mock_client, mock_news)

    context = provider.get_market_context("EPIC")

    # Verify Calls
    assert mock_client.fetch_historical_data.call_count == 4

    # Verify Content
    assert "Instrument: EPIC" in context
    assert "Daily OHLC Data" in context
    assert "Recent OHLC Data" in context
    assert "Granular OHLC Data" in context
    assert "Timing OHLC Data" in context
    assert "Mock News" in context
    assert "VIX Level: 20.0" in context
    assert "Long: 60" in context

    # Verify Indicators were calculated
    assert "ATR (14):" in context
    assert "RSI (14):" in context
