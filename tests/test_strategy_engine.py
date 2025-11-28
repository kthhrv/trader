import pytest
from unittest.mock import MagicMock, patch, call
import pandas as pd
import logging
from src.strategy_engine import StrategyEngine, Action, TradingSignal

@pytest.fixture
def mock_components():
    with patch("src.strategy_engine.IGClient") as mock_client_cls, \
         patch("src.strategy_engine.GeminiAnalyst") as mock_analyst_cls, \
         patch("src.strategy_engine.NewsFetcher") as mock_news_cls, \
         patch("src.strategy_engine.TradeLoggerDB") as mock_trade_logger_cls, \
         patch("src.strategy_engine.TradeMonitorDB") as mock_trade_monitor_cls, \
         patch("src.strategy_engine.MarketStatus") as mock_market_status_cls, \
         patch("src.strategy_engine.StreamManager") as mock_stream_manager_cls:
        
        mock_client = mock_client_cls.return_value
        mock_analyst = mock_analyst_cls.return_value
        mock_trade_logger = mock_trade_logger_cls.return_value
        mock_trade_monitor = mock_trade_monitor_cls.return_value
        mock_market_status = mock_market_status_cls.return_value
        mock_stream_manager = mock_stream_manager_cls.return_value # Mock the stream manager
        
        mock_market_status.is_holiday.return_value = False # Default to no holiday
        
        # Mock account info for dynamic sizing
        mock_client.get_account_info.return_value = pd.DataFrame({
            'accountId': ['TEST_ACC_ID'],
            'accountType': ['SPREADBET'],
            'available': [10000.0],
            'balance': [10000.0]
        })
        # IMPORTANT: Mock the 'service' attribute itself first
        mock_client.service = MagicMock()
        # Mock the client.service attribute and its account_id
        mock_client.service.session = MagicMock()
        mock_client.service.session.headers = {
            'CST': 'TEST_CST',
            'X-SECURITY-TOKEN': 'TEST_XST'
        }
        mock_client.service.account_id = 'TEST_ACC_ID'
        mock_client.service.account_type = 'SPREADBET'
        
        # Mock connect_and_subscribe for the StreamManager
        mock_stream_manager.connect_and_subscribe.return_value = None

        # Create a mock StrategyEngine instance for tests to use
        mock_engine = MagicMock(spec=StrategyEngine) # Create a mock instance with spec
        # Define the _calculate_size method directly on the mock instance
        mock_engine._calculate_size.return_value = 1.0 
        # Copy over other mocks needed by the engine
        mock_engine.client = mock_client
        mock_engine.analyst = mock_analyst
        mock_engine.news_fetcher = mock_news_cls.return_value
        mock_engine.market_status = mock_market_status
        mock_engine.trade_logger = mock_trade_logger
        mock_engine.trade_monitor = mock_trade_monitor
        mock_engine.stream_manager = mock_stream_manager

        yield mock_client, mock_analyst, mock_trade_logger, mock_trade_monitor, mock_market_status, mock_stream_manager, mock_engine

def test_generate_plan_success(mock_components):
    mock_client, mock_analyst, mock_trade_logger, mock_trade_monitor, mock_market_status, mock_stream_manager, mock_engine = mock_components
    
    # Mock data fetch with real DataFrame to support pandas-ta
    data = {
        'open': [100.0] * 50,
        'high': [105.0] * 50,
        'low': [95.0] * 50,
        'close': [102.0] * 50
    }
    mock_df = pd.DataFrame(data)
    mock_client.fetch_historical_data.return_value = mock_df
    
    # Mock analysis result
    mock_signal = TradingSignal(
        ticker="FTSE", action=Action.BUY, entry=7500, stop_loss=7450, 
        take_profit=7600, confidence="high", reasoning="Test", size=1, atr=15.0,
        entry_type="INSTANT", use_trailing_stop=True
    )
    mock_analyst.analyze_market.return_value = mock_signal
    
    engine = StrategyEngine("EPIC")
    engine.generate_plan()
    
    assert engine.active_plan == mock_signal
    mock_client.fetch_historical_data.assert_called_once()
    mock_analyst.analyze_market.assert_called_once()

def test_generate_plan_wait(mock_components, caplog):
    mock_client, mock_analyst, mock_trade_logger, mock_trade_monitor, mock_market_status, mock_stream_manager, mock_engine = mock_components

    # Mock data fetch with real DataFrame (required for pandas-ta)
    data = {
        'open': [100.0] * 50,
        'high': [105.0] * 50,
        'low': [95.0] * 50,
        'close': [102.0] * 50
    }
    mock_df = pd.DataFrame(data)
    mock_client.fetch_historical_data.return_value = mock_df

    # Mock analysis result to return Action.WAIT
    mock_signal = TradingSignal(
        ticker="FTSE", action=Action.WAIT, entry=0, stop_loss=0, 
        take_profit=0, confidence="low", reasoning="Market is uncertain", size=0, atr=0.0,
        entry_type="INSTANT", use_trailing_stop=True
    )
    mock_analyst.analyze_market.return_value = mock_signal

    engine = StrategyEngine("EPIC")
    
    with caplog.at_level(logging.INFO):
        engine.generate_plan()

    # Verify that no active plan is set
    assert engine.active_plan is None
    mock_client.fetch_historical_data.assert_called_once()
    mock_analyst.analyze_market.assert_called_once()
    
    # Verify that the correct message was logged
    assert "PLAN RESULT: Gemini advised WAIT." in caplog.text
    
    # Ensure no trade was attempted
    mock_client.place_spread_bet_order.assert_not_called()
    mock_trade_logger.log_trade.assert_not_called()
    mock_trade_monitor.monitor_trade.assert_not_called()

def test_generate_plan_holiday(mock_components, caplog):
    mock_client, mock_analyst, _, _, mock_market_status, mock_stream_manager, mock_engine = mock_components
    
    # Simulate a holiday
    mock_market_status.is_holiday.return_value = True
    
    engine = StrategyEngine("EPIC")
    
    with caplog.at_level(logging.WARNING):
        engine.generate_plan()
        
    # Verify holiday check
    mock_market_status.is_holiday.assert_called_once_with("EPIC")
    
    # Verify execution aborted
    assert "Holiday detected for EPIC. Strategy execution aborted." in caplog.text
    mock_client.fetch_historical_data.assert_not_called()
    mock_analyst.analyze_market.assert_not_called()

def test_poll_market_triggers_buy(mock_components):
    mock_client, mock_analyst, mock_trade_logger, mock_trade_monitor, mock_market_status, mock_stream_manager, _ = mock_components # Unpack all components
    engine = StrategyEngine("EPIC", ig_client=mock_client, analyst=mock_analyst, news_fetcher=MagicMock(), trade_logger=mock_trade_logger, trade_monitor=mock_trade_monitor, market_status=mock_market_status, stream_manager=mock_stream_manager)
    
    # Setup active plan
    plan_to_trigger = TradingSignal(
        ticker="FTSE", action=Action.BUY, entry=7500, stop_loss=7450,
        take_profit=7600, confidence="high", reasoning="Test", size=1, atr=15.0,
        entry_type="INSTANT", use_trailing_stop=True
    )
    engine.active_plan = plan_to_trigger
    
    # Mock successful trade placement return with dealId to trigger monitoring
    mock_client.place_spread_bet_order.return_value = {'dealId': 'TEST_DEAL_ID', 'dealStatus': 'ACCEPTED'}

    # Mock fetch_market_by_epic for spread check in _place_market_order
    mock_client.service.fetch_market_by_epic.return_value = {
        'snapshot': {'bid': 7499.0, 'offer': 7500.0} # Spread is 1.0
    }

    # Patch _calculate_size at the class level
    # Patch _calculate_size at the class level
    with patch.object(StrategyEngine, '_calculate_size', return_value=1.0, create=True) as mock_calculate_size:
        # Mock the stream manager's connect_and_subscribe to immediately update current prices
        def mock_connect_and_subscribe(epic, callback):
            # Simulate an immediate price update from the stream that triggers a BUY
            callback({
                "epic": epic,
                "bid": 7499.0,
                "offer": 7500.0, # Offer matches entry
                "time": "",
                "market_state": "OPEN"
            })
        mock_stream_manager.connect_and_subscribe.side_effect = mock_connect_and_subscribe
        
        # Call execute_strategy directly. The stream manager mock will handle initial price setting.
        engine.execute_strategy(timeout_seconds=0.1) # Very short timeout

        # Verify stream manager was called to connect and subscribe
        mock_stream_manager.connect_and_subscribe.assert_called_once_with(
            engine.epic, engine._stream_price_update_handler
        )
        
        # Verify trade placed
        assert engine.position_open is True
        mock_client.place_spread_bet_order.assert_called_once()
        mock_calculate_size.assert_called_once()
        call_kwargs = mock_client.place_spread_bet_order.call_args[1]
        assert call_kwargs['direction'] == 'BUY'
        assert call_kwargs['stop_level'] == 7450

    # Verify TradeLogger and TradeMonitor calls
    mock_trade_logger.log_trade.assert_called_once()
    log_trade_args = mock_trade_logger.log_trade.call_args[1]
    assert log_trade_args['epic'] == engine.epic
    assert log_trade_args['outcome'] == "LIVE_PLACED"
    assert log_trade_args['is_dry_run'] == False
    
    mock_trade_monitor.monitor_trade.assert_called_once()
    monitor_trade_args = mock_trade_monitor.monitor_trade.call_args[0]
    assert monitor_trade_args[0] == 'TEST_DEAL_ID'
    assert monitor_trade_args[1] == engine.epic

    # Ensure stream manager was stopped
    mock_stream_manager.stop.assert_called_once()

def test_poll_market_no_trigger(mock_components):
    mock_client, mock_analyst, mock_trade_logger, mock_trade_monitor, mock_market_status, mock_stream_manager, _ = mock_components
    engine = StrategyEngine("EPIC", ig_client=mock_client, analyst=mock_analyst, news_fetcher=MagicMock(), trade_logger=mock_trade_logger, trade_monitor=mock_trade_monitor, market_status=mock_market_status, stream_manager=mock_stream_manager)
    
    engine.active_plan = TradingSignal(
        ticker="FTSE", action=Action.BUY, entry=7500, stop_loss=7450, 
        take_profit=7600, confidence="high", reasoning="Test", size=1, atr=15.0,
        entry_type="INSTANT", use_trailing_stop=True
    )
    
    # Set initial current prices
    engine.current_bid = 7490.0
    engine.current_offer = 7495.0

    engine.execute_strategy(timeout_seconds=0.5) # Short timeout

    # Simulate a price update that DOES NOT trigger the BUY
    no_trigger_data = {
        "epic": "EPIC",
        "bid": 7490.0,
        "offer": 7495.0, # Offer is less than entry
        "time": "",
        "market_state": "OPEN"
    }
    engine._stream_price_update_handler(no_trigger_data)

    mock_stream_manager.connect_and_subscribe.assert_called_once_with(
        engine.epic, engine._stream_price_update_handler
    )
    assert engine.position_open is False
    mock_client.place_spread_bet_order.assert_not_called()
    mock_trade_logger.log_trade.assert_not_called()
    mock_trade_monitor.monitor_trade.assert_not_called()
    mock_stream_manager.stop.assert_called_once()

def test_place_market_order_spread_too_wide(mock_components, caplog):
    mock_client, mock_analyst, mock_trade_logger, mock_trade_monitor, mock_market_status, mock_stream_manager, mock_engine_unused = mock_components
    engine = StrategyEngine("EPIC", max_spread=1.0, ig_client=mock_client, analyst=mock_analyst, news_fetcher=MagicMock(), trade_logger=mock_trade_logger, trade_monitor=mock_trade_monitor, market_status=mock_market_status, stream_manager=mock_stream_manager) # Set max_spread
    
    # Setup active plan
    plan_to_trigger = TradingSignal(
        ticker="FTSE", action=Action.BUY, entry=7500, stop_loss=7450,
        take_profit=7600, confidence="high", reasoning="Test", size=1, atr=15.0,
        entry_type="INSTANT", use_trailing_stop=True
    )
    engine.active_plan = plan_to_trigger

    # Mock fetch_market_by_epic is still needed for _place_market_order's spread check (even if skipped by policy)
    # It's also used in TradeMonitorDB if position not found
    mock_client.service.fetch_market_by_epic.return_value = {
        'snapshot': {'bid': 7495.0, 'offer': 7505.0} # Spread is 10.0
    }
    
    # Patch _calculate_size at the class level
    # Patch _calculate_size at the class level
    with patch.object(StrategyEngine, '_calculate_size', return_value=1.0, create=True) as mock_calculate_size:
        # Mock the stream manager's connect_and_subscribe to immediately update current prices
        def mock_connect_and_subscribe(epic, callback):
            # Simulate an immediate price update that triggers the BUY with a wide spread
            callback({
                "epic": epic,
                "bid": 7495.0,
                "offer": 7505.0, # Offer is > entry (7500), but spread is 10
                "time": "",
                "market_state": "OPEN"
            })
        mock_stream_manager.connect_and_subscribe.side_effect = mock_connect_and_subscribe

        with caplog.at_level(logging.INFO):
            # Execute strategy, which will start the stream and wait for updates
            engine.execute_strategy(timeout_seconds=0.1) # Short timeout
        
            # Verify stream manager was called to connect and subscribe
            mock_stream_manager.connect_and_subscribe.assert_called_once_with(
                engine.epic, engine._stream_price_update_handler
            )
        
            # Verify that no trade was placed
            mock_client.place_spread_bet_order.assert_not_called()
            mock_calculate_size.assert_not_called()
            assert engine.position_open is False

                

        # Verify a warning was logged
        assert "SKIPPED: Spread (10.0) is wider than max allowed (1.0)" in caplog.text
        
        mock_trade_logger.log_trade.assert_not_called()
        mock_trade_monitor.monitor_trade.assert_not_called()
        mock_stream_manager.stop.assert_called_once()
def test_place_market_order_stop_too_tight(mock_components, caplog):
    mock_client, mock_analyst, mock_trade_logger, mock_trade_monitor, mock_market_status, mock_stream_manager, mock_engine_unused = mock_components
    engine = StrategyEngine("EPIC", ig_client=mock_client, analyst=mock_analyst, news_fetcher=MagicMock(), trade_logger=mock_trade_logger, trade_monitor=mock_trade_monitor, market_status=mock_market_status, stream_manager=mock_stream_manager)
    
    # Setup active plan with a tight stop relative to ATR
    # ATR = 15.0, Entry = 7500, Stop = 7490. Stop distance = 10. Ratio = 10/15 = 0.66 < 1.0
    plan_to_trigger = TradingSignal(
        ticker="FTSE", action=Action.BUY, entry=7500, stop_loss=7490,
        take_profit=7600, confidence="high", reasoning="Test", size=1, atr=15.0,
        entry_type="INSTANT", use_trailing_stop=True
    )
    engine.active_plan = plan_to_trigger

    # Mock fetch_market_by_epic to allow trade placement (acceptable spread)
    mock_client.service.fetch_market_by_epic.return_value = {
        'snapshot': {'bid': 7499.0, 'offer': 7500.0} # Spread is 1.0
    }
    
    # Mock successful trade placement return
    mock_client.place_spread_bet_order.return_value = {'dealId': 'TEST_DEAL_ID_TIGHT', 'dealStatus': 'ACCEPTED'}

    # Patch _calculate_size
    # Patch _calculate_size at the class level
    with patch.object(StrategyEngine, '_calculate_size', return_value=1.0, create=True) as mock_calculate_size:
        # Mock the stream manager's connect_and_subscribe to immediately update current prices
        def mock_connect_and_subscribe(epic, callback):
            # Simulate an immediate price update that triggers the BUY
            callback({
                "epic": epic,
                "bid": 7499.0,
                "offer": 7500.0, # Offer matches entry
                "time": "",
                "market_state": "OPEN"
            })
        mock_stream_manager.connect_and_subscribe.side_effect = mock_connect_and_subscribe

        with caplog.at_level(logging.WARNING):
            # Execute strategy
            engine.execute_strategy(timeout_seconds=0.1) # Short timeout

            # Verify stream manager was called to connect and subscribe
            mock_stream_manager.connect_and_subscribe.assert_called_once_with(
                engine.epic, engine._stream_price_update_handler
            )

            # Verify trade was placed (since spread is fine)
            mock_client.place_spread_bet_order.assert_called_once()
            mock_calculate_size.assert_called_once()
            assert engine.position_open is True
        
        # Verify a warning about tight stop was logged
        assert "Stop Loss for EPIC is tight (0.67x ATR)." in caplog.text

        mock_trade_logger.log_trade.assert_called_once()
        mock_trade_monitor.monitor_trade.assert_called_once()
        monitor_trade_args = mock_trade_monitor.monitor_trade.call_args[0]
        assert monitor_trade_args[0] == 'TEST_DEAL_ID_TIGHT'
        mock_stream_manager.stop.assert_called_once()

def test_place_market_order_dry_run(mock_components, caplog):
    mock_client, mock_analyst, mock_trade_logger, mock_trade_monitor, mock_market_status, mock_stream_manager, mock_engine_unused = mock_components
    engine = StrategyEngine("EPIC", dry_run=True, ig_client=mock_client, analyst=mock_analyst, news_fetcher=MagicMock(), trade_logger=mock_trade_logger, trade_monitor=mock_trade_monitor, market_status=mock_market_status, stream_manager=mock_stream_manager) # Set dry_run to True

    # Setup active plan
    plan_to_trigger = TradingSignal(
        ticker="FTSE", action=Action.BUY, entry=7500, stop_loss=7450,
        take_profit=7600, confidence="high", reasoning="Test", size=1, atr=15.0,
        entry_type="INSTANT", use_trailing_stop=True
    )
    engine.active_plan = plan_to_trigger

    # Mock fetch_market_by_epic to allow internal _place_market_order calls (e.g. spread check)
    mock_client.service.fetch_market_by_epic.return_value = {
        'snapshot': {'bid': 7499.0, 'offer': 7500.0} # Spread is 1.0
    }
    mock_client.place_spread_bet_order.return_value = {'dealId': 'mockDryRunDealId', 'dealStatus': 'ACCEPTED'}

    # Patch _calculate_size
    # Patch _calculate_size at the class level
    with patch.object(StrategyEngine, '_calculate_size', return_value=1.0, create=True) as mock_calculate_size:
        # Mock the stream manager's connect_and_subscribe to immediately update current prices
        def mock_connect_and_subscribe(epic, callback):
            # Simulate an immediate price update that triggers the BUY
            callback({
                "epic": epic,
                "bid": 7499.0,
                "offer": 7500.0,
                "time": "",
                "market_state": "OPEN"
            })
        mock_stream_manager.connect_and_subscribe.side_effect = mock_connect_and_subscribe

        with caplog.at_level(logging.INFO):
            # Execute strategy
            engine.execute_strategy(timeout_seconds=0.1) # Short timeout

            # Verify stream manager was called to connect and subscribe
            mock_stream_manager.connect_and_subscribe.assert_called_once_with(
                engine.epic, engine._stream_price_update_handler
            )

            # Verify no actual trade was placed
            mock_client.place_spread_bet_order.assert_not_called()
            mock_calculate_size.assert_called_once()
            assert engine.position_open is True # Position open becomes True to stop polling
        assert "DRY RUN: Order would have been PLACED" in caplog.text

        mock_trade_logger.log_trade.assert_called_once()
        log_trade_args = mock_trade_logger.log_trade.call_args[1]
        assert log_trade_args['epic'] == engine.epic
        assert log_trade_args['outcome'] == "DRY_RUN_PLACED"
        assert log_trade_args['is_dry_run'] == True

        mock_trade_monitor.monitor_trade.assert_not_called() # Should not monitor in dry run
        mock_stream_manager.stop.assert_called_once()