import pytest
import pandas as pd
from unittest.mock import MagicMock
from datetime import datetime, timedelta

from tests.mocks import MockIGClient, MockGeminiAnalyst, MockStreamManager, MockMarketStatus, MockNewsFetcher, MockTradeLoggerDB, MockTradeMonitorDB
from src.gemini_analyst import TradingSignal, Action

# --- Fixtures for reusable mock instances ---
@pytest.fixture
def mock_ig_client_instance():
    return MockIGClient()

@pytest.fixture
def mock_gemini_analyst_instance():
    return MockGeminiAnalyst()

@pytest.fixture
def mock_stream_manager_instance():
    return MockStreamManager()

@pytest.fixture
def mock_market_status_instance():
    return MockMarketStatus()

@pytest.fixture
def mock_trade_logger_instance():
    return MockTradeLoggerDB()

@pytest.fixture
def mock_trade_monitor_instance(mock_ig_client_instance):
    return MockTradeMonitorDB(client=mock_ig_client_instance)

@pytest.fixture
def mock_news_fetcher_instance():
    return MockNewsFetcher()

# --- Tests for MockIGClient ---
def test_mock_ig_client_fetch_historical_data(mock_ig_client_instance):
    epic = "TEST_EPIC"
    resolution = "15Min"
    num_points = 50
    df = mock_ig_client_instance.fetch_historical_data(epic, resolution, num_points)
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    mock_ig_client_instance.fetch_historical_data.assert_called_once_with(epic, resolution, num_points)

def test_mock_ig_client_place_spread_bet_order(mock_ig_client_instance):
    epic = "TEST_EPIC"
    direction = "BUY"
    size = 1.0
    stop_level = 100.0
    limit_level = 150.0
    
    response = mock_ig_client_instance.place_spread_bet_order(epic, direction, size, stop_level, None, limit_level)
    
    assert response == {'dealId': 'MOCK_DEAL_ID', 'dealStatus': 'ACCEPTED'}
    mock_ig_client_instance.place_spread_bet_order.assert_called_once_with(epic, direction, size, stop_level, None, limit_level)

def test_mock_ig_client_get_account_info(mock_ig_client_instance):
    df = mock_ig_client_instance.get_account_info()
    assert isinstance(df, pd.DataFrame)
    assert 'accountId' in df.columns
    assert not df.empty
    mock_ig_client_instance.get_account_info.assert_called_once()

def test_mock_ig_client_fetch_open_position_lifecycle(mock_ig_client_instance):
    deal_id = "TEST_DEAL_ID_OPEN"
    
    # Initially, position is open
    position = mock_ig_client_instance.fetch_open_position_by_deal_id(deal_id)
    assert position is not None
    assert position['dealId'] == deal_id
    mock_ig_client_instance.fetch_open_position_by_deal_id.assert_called_once() # First call
    
    # Simulate position closure
    mock_ig_client_instance.simulate_position_close(pnl=75.0)
    
    # Now, position should be closed
    mock_ig_client_instance.fetch_open_position_by_deal_id.reset_mock() # Reset mock call count
    closed_position = mock_ig_client_instance.fetch_open_position_by_deal_id(deal_id)
    assert closed_position is None
    mock_ig_client_instance.fetch_open_position_by_deal_id.assert_called_once() # Second call

def test_mock_ig_client_fetch_transaction_history_by_deal_id(mock_ig_client_instance):
    deal_id = "TEST_DEAL_ID_HISTORY"
    pnl_value = 123.45
    mock_ig_client_instance.simulate_position_close(pnl=pnl_value) # Ensure history mock is updated

    history_df = mock_ig_client_instance.fetch_transaction_history_by_deal_id(deal_id)
    assert isinstance(history_df, pd.DataFrame)
    assert 'profitAndLoss' in history_df.columns
    assert not history_df.empty
    assert f'£{pnl_value}' == history_df.iloc[0]['profitAndLoss']
    mock_ig_client_instance.fetch_transaction_history_by_deal_id.assert_called_once()

def test_mock_ig_client_get_market_info(mock_ig_client_instance):
    epic = "TEST_EPIC"
    market_info = mock_ig_client_instance.get_market_info(epic)
    assert isinstance(market_info, dict)
    assert 'snapshot' in market_info
    assert 'bid' in market_info['snapshot']
    mock_ig_client_instance.get_market_info.assert_called_once_with(epic)

# --- Tests for MockGeminiAnalyst ---
def test_mock_gemini_analyst_analyze_market(mock_gemini_analyst_instance):
    market_context = "some market data"
    strategy_name = "test strategy"
    signal = mock_gemini_analyst_instance.analyze_market(market_context, strategy_name=strategy_name)
    assert isinstance(signal, TradingSignal)
    assert signal.action == Action.BUY
    assert signal.entry_type == "INSTANT" # Ensure default is set or returned
    assert signal.use_trailing_stop is True # Ensure default is set or returned
    mock_gemini_analyst_instance.analyze_market.assert_called_once_with(market_context, strategy_name=strategy_name)

def test_mock_gemini_analyst_generate_post_mortem(mock_gemini_analyst_instance):
    trade_data = {"log": {}, "monitor": []}
    report = mock_gemini_analyst_instance.generate_post_mortem(trade_data)
    assert isinstance(report, str)
    assert report == "Mock Post-Mortem Report"
    mock_gemini_analyst_instance.generate_post_mortem.assert_called_once_with(trade_data)

# --- Tests for MockStreamManager ---
def test_mock_stream_manager_connect_and_subscribe(mock_stream_manager_instance):
    epic = "TEST_STREAM_EPIC"
    mock_callback = MagicMock()
    
    mock_stream_manager_instance.connect_and_subscribe(epic, mock_callback)
    
    assert epic in mock_stream_manager_instance.callbacks
    assert mock_stream_manager_instance.callbacks[epic] == mock_callback
    # Verify that the underlying MagicMock was called
    mock_stream_manager_instance.connect_and_subscribe.assert_called_once_with(epic, mock_callback)

def test_mock_stream_manager_simulate_price_tick(mock_stream_manager_instance):
    epic = "TEST_STREAM_EPIC"
    bid_price = 123.45
    offer_price = 123.50
    mock_callback = MagicMock()
    
    # First, register the callback
    mock_stream_manager_instance.connect_and_subscribe(epic, mock_callback)
    mock_stream_manager_instance.connect_and_subscribe.reset_mock() # Reset call count for this mock
    
    # Now, simulate a price tick
    mock_stream_manager_instance.simulate_price_tick(epic, bid_price, offer_price)
    
    # Verify the callback was called with the correct data
    mock_callback.assert_called_once()
    called_args, called_kwargs = mock_callback.call_args
    assert called_args[0]['epic'] == epic
    assert called_args[0]['bid'] == bid_price
    assert called_args[0]['offer'] == offer_price

def test_mock_stream_manager_stop(mock_stream_manager_instance):
    mock_stream_manager_instance.stop()
    mock_stream_manager_instance.stop.assert_called_once()

# --- Tests for MockMarketStatus ---
def test_mock_market_status_is_holiday(mock_market_status_instance):
    epic = "TEST_EPIC"
    is_holiday = mock_market_status_instance.is_holiday(epic)
    assert is_holiday is False # Default return value
    mock_market_status_instance.is_holiday.assert_called_once_with(epic)
    
    # Test with custom return value
    mock_market_status_instance.is_holiday.return_value = True
    assert mock_market_status_instance.is_holiday(epic) is True

# --- Tests for MockNewsFetcher ---
def test_mock_news_fetcher_fetch_news(mock_news_fetcher_instance):
    query = "some news query"
    news = mock_news_fetcher_instance.fetch_news(query)
    assert isinstance(news, str)
    assert news == "Mock news context: Market looking stable."
    mock_news_fetcher_instance.fetch_news.assert_called_once_with(query)

# --- Tests for MockTradeLoggerDB ---
def test_mock_trade_logger_db_log_trade(mock_trade_logger_instance):
    epic = "TEST_EPIC"
    plan = TradingSignal(
        ticker="TEST", action=Action.BUY, entry=100, stop_loss=90, take_profit=110,
        confidence="low", reasoning="test", size=1, atr=5,
        entry_type="INSTANT", use_trailing_stop=True
    )
    outcome = "LIVE_PLACED"
    spread_at_entry = 1.5
    is_dry_run = False
    deal_id = "TEST_DEAL_ID"
    
    mock_trade_logger_instance.log_trade(epic, plan, outcome, spread_at_entry, is_dry_run, deal_id)
    mock_trade_logger_instance.log_trade.assert_called_once_with(epic, plan, outcome, spread_at_entry, is_dry_run, deal_id)

# --- Tests for MockTradeMonitorDB ---
def test_mock_trade_monitor_db_monitor_trade(mock_trade_monitor_instance):
    deal_id = "TEST_DEAL_ID_MONITOR"
    epic = "TEST_EPIC"
    
    mock_trade_monitor_instance.monitor_trade(deal_id, epic)
    mock_trade_monitor_instance.monitor_trade.assert_called_once_with(deal_id, epic)
