import pytest
from unittest.mock import patch, MagicMock, call
import pandas as pd
import os
import tempfile
import logging
from datetime import datetime, timedelta
import time

from src.strategy_engine import StrategyEngine, Action, TradingSignal, EntryType
from src.database import get_db_connection, init_db, fetch_trade_data
from src.trade_logger_db import TradeLoggerDB
from src.trade_monitor_db import TradeMonitorDB
from tests.mocks import MockIGClient, MockGeminiAnalyst, MockStreamManager, MockMarketStatus, MockNewsFetcher

logger = logging.getLogger(__name__)

@pytest.fixture
def temp_db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temp_db:
        db_path = temp_db.name
    init_db(db_path)
    yield db_path
    os.remove(db_path)

@pytest.fixture
def advanced_mocks(temp_db_path):
    mock_ig_client = MockIGClient()
    mock_gemini_analyst = MockGeminiAnalyst()
    mock_stream_manager = MockStreamManager()
    mock_market_status = MockMarketStatus()
    mock_news_fetcher = MagicMock()
    
    mock_trade_logger = TradeLoggerDB(db_path=temp_db_path)
    mock_trade_logger.log_trade = MagicMock(side_effect=mock_trade_logger.log_trade)
    
    # Fast polling for test speed
    mock_trade_monitor = TradeMonitorDB(client=mock_ig_client, db_path=temp_db_path, polling_interval=0.1)
    mock_trade_monitor.monitor_trade = MagicMock(side_effect=mock_trade_monitor.monitor_trade)
    
    yield mock_ig_client, mock_gemini_analyst, mock_stream_manager, mock_market_status, mock_news_fetcher, mock_trade_logger, mock_trade_monitor, temp_db_path

def test_e2e_confirmation_entry(advanced_mocks, caplog):
    caplog.set_level(logging.DEBUG)
    mock_ig_client, mock_gemini_analyst, mock_stream_manager, mock_market_status, mock_news_fetcher, mock_trade_logger, mock_trade_monitor, db_path = advanced_mocks
    
    epic = "IX.D.FTSE.DAILY.IP"
    entry_price = 7500.0
    
    # 1. Setup: CONFIRMATION Entry
    mock_gemini_analyst.analyze_market.return_value = TradingSignal(
        ticker=epic, action=Action.BUY, entry=entry_price, stop_loss=7450.0, 
        take_profit=7600.0, confidence="high", reasoning="Test Confirmation", size=1.0, atr=10.0,
        entry_type=EntryType.CONFIRMATION
    )
    
    engine = StrategyEngine(
        epic, strategy_name="TEST_CONFIRM", ig_client=mock_ig_client, analyst=mock_gemini_analyst,
        news_fetcher=mock_news_fetcher, trade_logger=mock_trade_logger, trade_monitor=mock_trade_monitor,
        market_status=mock_market_status, stream_manager=mock_stream_manager, dry_run=False
    )
    # Ensure logger debug
    logging.getLogger("src.strategy_engine").setLevel(logging.DEBUG)
    
    engine.generate_plan()
    assert engine.active_plan is not None, "Plan generation failed!"
    
    from threading import Thread
    trade_execution_thread = Thread(target=engine.execute_strategy, kwargs={'timeout_seconds': 4.0}, daemon=True)
    trade_execution_thread.start()
    time.sleep(0.1)
    
    # Initialize prices (so loop doesn't skip)
    engine.current_bid = 7490.0
    engine.current_offer = 7491.0
    
    # 3. Simulate Price Ticks crossing entry (Should be IGNORED for Confirmation)
    mock_stream_manager.simulate_price_tick(epic, entry_price + 5, entry_price + 6)
    time.sleep(0.5)
    
    # Verify NO trade placed yet
    mock_ig_client.place_spread_bet_order.assert_not_called()
    
    # Stop the first thread to avoid double triggers
    engine.position_open = True
    trade_execution_thread.join(timeout=1.0)
    engine.position_open = False # Reset for next run
    
    # 4. Simulate 1-Min Candle Close > Entry
    # We patch datetime to control the "current time" and simulate a minute change.
    with patch("src.strategy_engine.datetime") as mock_datetime:
        # Start at minute 0
        mock_datetime.now.return_value = datetime(2025, 1, 1, 8, 0, 0)
        
        # Start Execution (restart thread with patched datetime)
        trade_execution_thread = Thread(target=engine.execute_strategy, kwargs={'timeout_seconds': 4.0}, daemon=True)
        trade_execution_thread.start()
        time.sleep(0.1)
        
        # Initialize prices
        engine.current_bid = 7490.0
        engine.current_offer = 7491.0
        
        # Ticks cross entry (7500) -> Should be ignored
        mock_stream_manager.simulate_price_tick(epic, entry_price + 5, entry_price + 6)
        time.sleep(0.2)
        mock_ig_client.place_spread_bet_order.assert_not_called()
        
        # Mock "Closed Candle" data > Entry
        closed_candle_df = pd.DataFrame({
            'open': [entry_price, entry_price],
            'high': [entry_price + 10, entry_price + 10],
            'low': [entry_price - 2, entry_price - 2],
            'close': [entry_price + 5, entry_price + 5] # Close > Entry
        })
        mock_ig_client.fetch_historical_data.return_value = closed_candle_df
        
        # Advance time to minute 1 -> Triggers check
        mock_datetime.now.return_value = datetime(2025, 1, 1, 8, 1, 5)
        time.sleep(0.5) # Allow loop to cycle and check
        
        # Verify Trade Placed
        mock_ig_client.place_spread_bet_order.assert_called_once()
        
        # Cleanup thread
        engine.position_open = True # Force exit loop if not already done
        trade_execution_thread.join(timeout=1.0)

def test_e2e_trailing_stop(advanced_mocks, caplog):
    caplog.set_level(logging.DEBUG)
    mock_ig_client, mock_gemini_analyst, mock_stream_manager, mock_market_status, mock_news_fetcher, mock_trade_logger, mock_trade_monitor, db_path = advanced_mocks
    
    epic = "IX.D.FTSE.DAILY.IP"
    entry_price = 7500.0
    stop_loss = 7450.0
    risk = 50.0
    
    # 1. Setup: INSTANT Entry
    mock_gemini_analyst.analyze_market.return_value = TradingSignal(
        ticker=epic, action=Action.BUY, entry=entry_price, stop_loss=stop_loss, 
        take_profit=7600.0, confidence="high", reasoning="Test Trailing", size=1.0, atr=10.0,
        entry_type=EntryType.INSTANT
    )
    
    engine = StrategyEngine(
        epic, strategy_name="TEST_TRAIL", ig_client=mock_ig_client, analyst=mock_gemini_analyst,
        news_fetcher=mock_news_fetcher, trade_logger=mock_trade_logger, trade_monitor=mock_trade_monitor,
        market_status=mock_market_status, stream_manager=mock_stream_manager, dry_run=False
    )
    logging.getLogger("src.strategy_engine").setLevel(logging.DEBUG)
    engine.generate_plan()
    assert engine.active_plan is not None
    
    # 2. Start Execution
    from threading import Thread
    trade_execution_thread = Thread(target=engine.execute_strategy, kwargs={'timeout_seconds': 4.0}, daemon=True)
    trade_execution_thread.start()
    time.sleep(0.1)
    
    # Trigger Trade
    mock_stream_manager.simulate_price_tick(epic, entry_price, entry_price + 1)
    time.sleep(0.2)
    
    # Verify Trade Placed
    mock_ig_client.place_spread_bet_order.assert_called_once()
    mock_trade_monitor.monitor_trade.assert_called_once()
    
    # 3. Simulate Price Movement for Trailing
    # Monitor is running in background. We need to control what `fetch_open_position` returns.
    
    # Initial State
    initial_pos = {'dealId': 'MOCK_DEAL_ID', 'direction': 'BUY', 'bid': 7500, 'offer': 7501, 'stopLevel': 7450}
    
    # Move 1: Profit = 1.0R (50 pts). Price = 7550. Stop should move to Breakeven (7500).
    move_1_pos = {'dealId': 'MOCK_DEAL_ID', 'direction': 'BUY', 'bid': 7550, 'offer': 7551, 'stopLevel': 7450}
    
    # Move 2: Profit = 1.5R (75 pts). Price = 7575. Stop should trail to Price - 1R (7575 - 50 = 7525).
    # Assuming update_open_position succeeded and updated the stopLevel on the server side.
    move_2_pos = {'dealId': 'MOCK_DEAL_ID', 'direction': 'BUY', 'bid': 7575, 'offer': 7576, 'stopLevel': 7500} # Stop was at BE
    
    # Close
    close_pos = None
    
    # We set the side_effect of fetch_open_position to iterate through these states
    # The monitor polls every 0.1s. We need to provide enough "same state" returns to ensure logic catches it, 
    # or just sequence them.
    mock_ig_client.fetch_open_position_by_deal_id.side_effect = [
        initial_pos, initial_pos, 
        move_1_pos, move_1_pos, # Trigger Breakeven
        move_2_pos, move_2_pos, # Trigger Trailing
        close_pos
    ]
    
    # Wait for monitor to finish
    trade_execution_thread.join(timeout=2.0)
    
    # 4. Verify Updates
    # Check calls to update_open_position
    assert mock_ig_client.update_open_position.call_count >= 2
    
    # First update: Breakeven
    args1 = mock_ig_client.update_open_position.call_args_list[0]
    assert args1[1]['stop_level'] == 7500.0 # Entry Price
    
    # Second update: Trailing
    args2 = mock_ig_client.update_open_position.call_args_list[1]
    assert args2[1]['stop_level'] == 7525.0 # 7575 - 50
