import pytest
import os
import shutil
from unittest.mock import MagicMock
import pandas as pd
from src.market_data_provider import MarketDataProvider


@pytest.fixture
def clean_cache():
    if os.path.exists(".cache"):
        shutil.rmtree(".cache")
    yield
    if os.path.exists(".cache"):
        shutil.rmtree(".cache")


def test_caching_enabled(clean_cache):
    mock_client = MagicMock()
    mock_news = MagicMock()

    # Setup data
    df_data = pd.DataFrame({"close": [100] * 10})
    mock_client.fetch_historical_data.return_value = df_data

    # Initialize with cache enabled
    provider = MarketDataProvider(mock_client, mock_news, use_cache=True)

    # 1. First Call - Should hit API
    data1 = provider._fetch_daily_data("TEST.EPIC")
    assert mock_client.fetch_historical_data.call_count == 1

    # 2. Second Call - Should hit Cache (API call count stays 1)
    data2 = provider._fetch_daily_data("TEST.EPIC")
    assert mock_client.fetch_historical_data.call_count == 1

    # Data should match
    pd.testing.assert_frame_equal(data1, data2)


def test_caching_disabled(clean_cache):
    mock_client = MagicMock()
    mock_news = MagicMock()

    df_data = pd.DataFrame({"close": [100] * 10})
    mock_client.fetch_historical_data.return_value = df_data

    # Initialize with cache DISABLED
    provider = MarketDataProvider(mock_client, mock_news, use_cache=False)

    # 1. First Call
    provider._fetch_daily_data("TEST.EPIC")
    assert mock_client.fetch_historical_data.call_count == 1

    # 2. Second Call - Should hit API again
    provider._fetch_daily_data("TEST.EPIC")
    assert mock_client.fetch_historical_data.call_count == 2


def test_cache_ttl_expiry(clean_cache):
    mock_client = MagicMock()
    mock_news = MagicMock()

    df_data = pd.DataFrame({"close": [100] * 10})
    mock_client.fetch_historical_data.return_value = df_data

    # Short TTL
    provider = MarketDataProvider(mock_client, mock_news, use_cache=True, cache_ttl=0.1)

    provider._fetch_daily_data("TEST.EPIC")
    assert mock_client.fetch_historical_data.call_count == 1

    # Wait for expiry
    import time

    time.sleep(0.2)

    # Should hit API again
    provider._fetch_daily_data("TEST.EPIC")
    assert mock_client.fetch_historical_data.call_count == 2
