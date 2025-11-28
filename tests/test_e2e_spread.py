import pytest
from unittest.mock import MagicMock
import logging
import time
from threading import Thread

from src.strategy_engine import StrategyEngine, Action, TradingSignal, EntryType
from src.trade_logger_db import TradeLoggerDB
from src.trade_monitor_db import TradeMonitorDB
from tests.mocks import MockIGClient, MockGeminiAnalyst, MockStreamManager, MockMarketStatus

logger = logging.getLogger(__name__)

@pytest.fixture
def spread_mocks():
    mock_ig_client = MockIGClient()
    mock_gemini_analyst = MockGeminiAnalyst()
    mock_stream_manager = MockStreamManager()
    mock_market_status = MockMarketStatus()
    mock_news_fetcher = MagicMock()
    mock_trade_logger = MagicMock()
    mock_trade_monitor = MagicMock()
    
    return mock_ig_client, mock_gemini_analyst, mock_stream_manager, mock_market_status, mock_news_fetcher, mock_trade_logger, mock_trade_monitor

def test_e2e_spread_filter(spread_mocks, caplog):
    """
    Verifies that the bot does NOT enter a trade if the spread is wider than max_spread.
    """
    caplog.set_level(logging.DEBUG)
    mock_ig_client, mock_gemini_analyst, mock_stream_manager, mock_market_status, mock_news_fetcher, mock_trade_logger, mock_trade_monitor = spread_mocks
    
    epic = "IX.D.FTSE.DAILY.IP"
    entry_price = 7500.0
    max_spread = 2.0
    
    # Setup: INSTANT Entry
    mock_gemini_analyst.analyze_market.return_value = TradingSignal(
        ticker=epic, action=Action.BUY, entry=entry_price, stop_loss=7450.0, 
        take_profit=7600.0, confidence="high", reasoning="Test Spread", size=1.0, atr=10.0,
        entry_type=EntryType.INSTANT, use_trailing_stop=True
    )
    
    engine = StrategyEngine(
        epic, strategy_name="TEST_SPREAD", ig_client=mock_ig_client, analyst=mock_gemini_analyst,
        news_fetcher=mock_news_fetcher, trade_logger=mock_trade_logger, trade_monitor=mock_trade_monitor,
        market_status=mock_market_status, stream_manager=mock_stream_manager, 
        dry_run=False, max_spread=max_spread
    )
    # Silence validation logs for clarity
    logging.getLogger("src.strategy_engine").setLevel(logging.INFO)
    
    engine.generate_plan()
    
    # Start Execution
    trade_execution_thread = Thread(target=engine.execute_strategy, kwargs={'timeout_seconds': 3.0}, daemon=True)
    trade_execution_thread.start()
    time.sleep(0.1)
    
    # 1. Simulate High Spread (Bid 7500, Offer 7505 -> Spread 5.0 > 2.0)
    # Even though Offer (7505) > Entry (7500), it should SKIP.
    mock_stream_manager.simulate_price_tick(epic, 7500, 7505)
    time.sleep(0.5)
    
    # Verify NO trade placed
    mock_ig_client.place_spread_bet_order.assert_not_called()
    assert "SKIPPED: Spread (5) is wider than max allowed" in caplog.text
    
    # 2. Simulate Normal Spread (Bid 7501, Offer 7502 -> Spread 1.0 < 2.0)
    # Offer (7502) > Entry (7500) -> Should TRIGGER
    mock_stream_manager.simulate_price_tick(epic, 7501, 7502)
    time.sleep(0.5)
    
    # Verify Trade Placed
    mock_ig_client.place_spread_bet_order.assert_called_once()
    
    trade_execution_thread.join(timeout=1.0)
