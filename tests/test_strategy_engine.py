import pytest
from unittest.mock import MagicMock, patch, ANY
import pandas as pd
import logging
from src.strategy_engine import StrategyEngine, Action, TradingSignal, EntryType


@pytest.fixture
def mock_components():
    with (
        patch("src.strategy_engine.IGClient") as mock_client_cls,
        patch("src.strategy_engine.GeminiAnalyst") as mock_analyst_cls,
        patch("src.strategy_engine.NewsFetcher") as mock_news_cls,
        patch("src.strategy_engine.TradeLoggerDB") as mock_trade_logger_cls,
        patch("src.strategy_engine.TradeMonitorDB") as mock_trade_monitor_cls,
        patch("src.strategy_engine.MarketStatus") as mock_market_status_cls,
        patch("src.strategy_engine.StreamManager") as mock_stream_manager_cls,
        patch("src.strategy_engine.MarketDataProvider") as mock_provider_cls,
    ):
        mock_client = mock_client_cls.return_value
        mock_analyst = mock_analyst_cls.return_value
        mock_trade_logger = mock_trade_logger_cls.return_value
        mock_trade_monitor = mock_trade_monitor_cls.return_value
        mock_market_status = mock_market_status_cls.return_value
        mock_stream_manager = mock_stream_manager_cls.return_value
        mock_provider = mock_provider_cls.return_value

        mock_market_status.is_holiday.return_value = False

        # Mock account info for dynamic sizing
        mock_client.get_account_info.return_value = pd.DataFrame(
            {
                "accountId": ["TEST_ACC_ID"],
                "accountType": ["SPREADBET"],
                "available": [10000.0],
                "balance": [10000.0],
            }
        )
        mock_client.service = MagicMock()
        mock_client.service.session = MagicMock()
        mock_client.service.session.headers = {
            "CST": "TEST",
            "X-SECURITY-TOKEN": "TEST",
        }
        mock_client.service.account_id = "TEST_ACC_ID"

        mock_stream_manager.connect_and_subscribe.return_value = None

        mock_engine = MagicMock(spec=StrategyEngine)
        mock_engine._calculate_size.return_value = 1.0
        mock_engine.client = mock_client
        mock_engine.analyst = mock_analyst
        mock_engine.news_fetcher = mock_news_cls.return_value
        mock_engine.market_status = mock_market_status
        mock_engine.trade_logger = mock_trade_logger
        mock_engine.trade_monitor = mock_trade_monitor
        mock_engine.stream_manager = mock_stream_manager
        mock_engine.data_provider = mock_provider

        yield (
            mock_client,
            mock_analyst,
            mock_trade_logger,
            mock_trade_monitor,
            mock_market_status,
            mock_stream_manager,
            mock_provider,
            mock_engine,
        )


def test_generate_plan_success(mock_components):
    (
        mock_client,
        mock_analyst,
        mock_trade_logger,
        mock_trade_monitor,
        mock_market_status,
        mock_stream_manager,
        mock_provider,
        mock_engine,
    ) = mock_components

    # Mock provider return
    mock_provider.get_market_context.return_value = "Mock Context"

    # Mock analysis result
    mock_signal = TradingSignal(
        ticker="FTSE",
        action=Action.BUY,
        entry=7500,
        stop_loss=7450,
        take_profit=7600,
        confidence="high",
        reasoning="Test",
        size=1,
        atr=15.0,
        entry_type=EntryType.INSTANT,
        use_trailing_stop=True,
    )
    mock_analyst.analyze_market.return_value = mock_signal

    engine = StrategyEngine("EPIC")
    # Manually inject the mock provider since __init__ creates a real one
    engine.data_provider = mock_provider
    engine.client = mock_client
    engine.analyst = mock_analyst
    engine.trade_logger = mock_trade_logger  # Ensure logger is mocked

    engine.generate_plan()

    assert engine.active_plan == mock_signal
    mock_provider.get_market_context.assert_called_once()
    mock_analyst.analyze_market.assert_called_once_with(
        "Mock Context", strategy_name="Market Open"
    )


def test_generate_plan_wait(mock_components, caplog):
    (
        mock_client,
        mock_analyst,
        mock_trade_logger,
        mock_trade_monitor,
        mock_market_status,
        mock_stream_manager,
        mock_provider,
        mock_engine,
    ) = mock_components

    mock_provider.get_market_context.return_value = "Mock Context for WAIT"

    # Mock analysis result to return Action.WAIT
    mock_signal = TradingSignal(
        ticker="FTSE",
        action=Action.WAIT,
        entry=0,
        stop_loss=0,
        take_profit=0,
        confidence="low",
        reasoning="Market is uncertain",
        size=0,
        atr=0.0,
        entry_type=EntryType.INSTANT,
        use_trailing_stop=True,
    )
    mock_analyst.analyze_market.return_value = mock_signal

    engine = StrategyEngine("EPIC")
    engine.data_provider = mock_provider
    engine.client = mock_client
    engine.analyst = mock_analyst
    engine.trade_logger = mock_trade_logger

    with caplog.at_level(logging.INFO):
        engine.generate_plan()

    assert engine.active_plan == mock_signal
    mock_provider.get_market_context.assert_called_once()
    mock_analyst.analyze_market.assert_called_once()

    assert "PLAN RESULT: Gemini advised WAIT" in caplog.text


def test_generate_plan_holiday(mock_components, caplog):
    (
        mock_client,
        mock_analyst,
        _,
        _,
        mock_market_status,
        mock_stream_manager,
        mock_provider,
        mock_engine,
    ) = mock_components

    # Simulate a holiday
    mock_market_status.is_holiday.return_value = True

    engine = StrategyEngine("EPIC")

    with caplog.at_level(logging.WARNING):
        engine.generate_plan()

    # Verify holiday check
    mock_market_status.is_holiday.assert_called_once_with("EPIC")

    # Verify execution aborted
    assert "Holiday detected for EPIC. Strategy execution aborted." in caplog.text
    # fetch_historical_data is no longer called directly on client in Engine
    # Instead, provider.get_market_context would be called, but holiday check is before that.
    # So we can assert provider was NOT called.
    # But for now, let's just ensure analyst wasn't called.
    mock_analyst.analyze_market.assert_not_called()


def test_poll_market_triggers_buy(mock_components):
    (
        mock_client,
        mock_analyst,
        mock_trade_logger,
        mock_trade_monitor,
        mock_market_status,
        mock_stream_manager,
        mock_provider,
        _,
    ) = mock_components
    engine = StrategyEngine(
        "EPIC",
        ig_client=mock_client,
        analyst=mock_analyst,
        news_fetcher=MagicMock(),
        trade_logger=mock_trade_logger,
        trade_monitor=mock_trade_monitor,
        market_status=mock_market_status,
        stream_manager=mock_stream_manager,
    )
    engine.data_provider = mock_provider

    plan = TradingSignal(
        ticker="FTSE",
        action=Action.BUY,
        entry=7500,
        stop_loss=7450,
        take_profit=7600,
        confidence="high",
        reasoning="Test",
        size=1,
        atr=15.0,
        entry_type=EntryType.INSTANT,
        use_trailing_stop=True,
    )
    engine.active_plan = plan
    engine.active_plan_id = 123

    mock_client.place_spread_bet_order.return_value = {
        "dealId": "TEST_DEAL_ID",
        "dealStatus": "ACCEPTED",
        "level": 7500.0,
    }

    current_time = [100.0]

    def mock_time():
        current_time[0] += 0.01
        return current_time[0]

    def trigger_update(seconds):
        engine._stream_price_update_handler(
            {
                "epic": "EPIC",
                "bid": 7499.0,
                "offer": 7500.0,
            }
        )

    with (
        patch.object(StrategyEngine, "_calculate_size", return_value=1.0),
        patch("time.sleep", side_effect=trigger_update),
        patch("time.time", side_effect=mock_time),
    ):
        engine.execute_strategy(timeout_seconds=5.0, collection_seconds=10.0)

    assert engine.position_open is True
    mock_client.place_spread_bet_order.assert_called_once()

    mock_trade_logger.update_trade_status.assert_any_call(
        row_id=123,
        outcome="LIVE_PLACED",
        deal_id="TEST_DEAL_ID",
        size=ANY,
        entry=7500.0,
        stop_loss=7449.0,  # Adjusted for 1.0 spread
    )


def test_poll_market_no_trigger(mock_components):
    (
        mock_client,
        mock_analyst,
        mock_trade_logger,
        mock_trade_monitor,
        mock_market_status,
        mock_stream_manager,
        mock_provider,
        _,
    ) = mock_components
    engine = StrategyEngine(
        "EPIC",
        ig_client=mock_client,
        analyst=mock_analyst,
        news_fetcher=MagicMock(),
        trade_logger=mock_trade_logger,
        trade_monitor=mock_trade_monitor,
        market_status=mock_market_status,
        stream_manager=mock_stream_manager,
    )
    engine.data_provider = mock_provider

    engine.active_plan = TradingSignal(
        ticker="FTSE",
        action=Action.BUY,
        entry=7500,
        stop_loss=7450,
        take_profit=7600,
        confidence="high",
        reasoning="Test",
        size=1,
        atr=15.0,
        entry_type=EntryType.INSTANT,
        use_trailing_stop=True,
    )
    engine.active_plan_id = 123

    current_time = [100.0]

    def mock_time():
        current_time[0] += 1.0
        return current_time[0]

    with (
        patch("time.time", side_effect=mock_time),
        patch("time.sleep", return_value=None),
    ):
        engine.execute_strategy(timeout_seconds=0.1, collection_seconds=2.0)

    assert engine.position_open is False
    mock_client.place_spread_bet_order.assert_not_called()

    mock_trade_logger.update_trade_status.assert_called_with(
        row_id=123, outcome="TIMED_OUT", deal_id=None
    )


def test_place_market_order_spread_too_wide(mock_components, caplog):
    (
        mock_client,
        mock_analyst,
        mock_trade_logger,
        mock_trade_monitor,
        mock_market_status,
        mock_stream_manager,
        mock_provider,
        _,
    ) = mock_components
    engine = StrategyEngine(
        "EPIC",
        max_spread=1.0,
        ig_client=mock_client,
        analyst=mock_analyst,
        news_fetcher=MagicMock(),
        trade_logger=mock_trade_logger,
        trade_monitor=mock_trade_monitor,
        market_status=mock_market_status,
        stream_manager=mock_stream_manager,
    )
    engine.data_provider = mock_provider

    engine.active_plan = TradingSignal(
        ticker="FTSE",
        action=Action.BUY,
        entry=7500,
        stop_loss=7450,
        take_profit=7600,
        confidence="high",
        reasoning="Test",
        size=1,
        atr=15.0,
        entry_type=EntryType.INSTANT,
        use_trailing_stop=True,
    )
    engine.active_plan_id = 123

    current_time = [100.0]

    def mock_time():
        current_time[0] += 0.001
        return current_time[0]

    def trigger_update(seconds):
        engine._stream_price_update_handler(
            {
                "epic": "EPIC",
                "bid": 7495.0,
                "offer": 7505.0,
            }
        )

    with (
        patch("time.sleep", side_effect=trigger_update),
        patch("time.time", side_effect=mock_time),
        caplog.at_level(logging.INFO),
    ):
        engine.execute_strategy(timeout_seconds=5.0, collection_seconds=10.0)

    mock_client.place_spread_bet_order.assert_not_called()
    assert "SKIPPED: Spread (10.0) is wider than max allowed (1.0)" in caplog.text

    mock_trade_logger.update_trade_status.assert_any_call(
        row_id=123, outcome="TIMED_OUT", deal_id=None
    )


def test_place_market_order_stop_too_tight(mock_components, caplog):
    (
        mock_client,
        mock_analyst,
        mock_trade_logger,
        mock_trade_monitor,
        mock_market_status,
        mock_stream_manager,
        mock_provider,
        _,
    ) = mock_components
    engine = StrategyEngine(
        "EPIC",
        ig_client=mock_client,
        analyst=mock_analyst,
        news_fetcher=MagicMock(),
        trade_logger=mock_trade_logger,
        trade_monitor=mock_trade_monitor,
        market_status=mock_market_status,
        stream_manager=mock_stream_manager,
    )
    engine.data_provider = mock_provider

    plan = TradingSignal(
        ticker="FTSE",
        action=Action.BUY,
        entry=7500,
        stop_loss=7490,
        take_profit=7600,
        confidence="high",
        reasoning="Test",
        size=1,
        atr=15.0,
        entry_type=EntryType.INSTANT,
        use_trailing_stop=True,
    )
    engine.active_plan = plan
    engine.active_plan_id = 123

    current_time = [100.0]

    def mock_time():
        current_time[0] += 0.01
        return current_time[0]

    def trigger_update(seconds):
        engine._stream_price_update_handler(
            {
                "epic": "EPIC",
                "bid": 7499.0,
                "offer": 7500.0,
            }
        )

    mock_client.place_spread_bet_order.return_value = {"dealId": "OK"}
    with (
        patch.object(StrategyEngine, "_calculate_size", return_value=1.0),
        patch("time.sleep", side_effect=trigger_update),
        patch("time.time", side_effect=mock_time),
    ):
        engine.execute_strategy(timeout_seconds=0.5, collection_seconds=2.0)

    mock_client.place_spread_bet_order.assert_called_once()


def test_place_market_order_dry_run(mock_components, caplog):
    (
        mock_client,
        mock_analyst,
        mock_trade_logger,
        mock_trade_monitor,
        mock_market_status,
        mock_stream_manager,
        mock_provider,
        _,
    ) = mock_components
    engine = StrategyEngine(
        "EPIC",
        dry_run=True,
        ig_client=mock_client,
        analyst=mock_analyst,
        news_fetcher=MagicMock(),
        trade_logger=mock_trade_logger,
        trade_monitor=mock_trade_monitor,
        market_status=mock_market_status,
        stream_manager=mock_stream_manager,
    )
    engine.data_provider = mock_provider

    plan = TradingSignal(
        ticker="FTSE",
        action=Action.BUY,
        entry=7500,
        stop_loss=7450,
        take_profit=7600,
        confidence="high",
        reasoning="Test",
        size=1,
        atr=15.0,
        entry_type=EntryType.INSTANT,
        use_trailing_stop=True,
    )
    engine.active_plan = plan
    engine.active_plan_id = 123

    current_time = [100.0]

    def mock_time():
        current_time[0] += 0.01
        return current_time[0]

    def trigger_update(seconds):
        engine._stream_price_update_handler(
            {
                "epic": "EPIC",
                "bid": 7499.0,
                "offer": 7500.0,
            }
        )

    with (
        patch.object(StrategyEngine, "_calculate_size", return_value=1.0),
        patch("time.sleep", side_effect=trigger_update),
        patch("time.time", side_effect=mock_time),
        caplog.at_level(logging.INFO),
    ):
        engine.execute_strategy(timeout_seconds=0.5, collection_seconds=2.0)

    mock_client.place_spread_bet_order.assert_not_called()
    assert "DRY RUN: BUY 1.0 EPIC at 7500.0 (Stop: 7449.0)" in caplog.text
    mock_trade_logger.update_trade_status.assert_called_with(
        row_id=123,
        outcome="DRY_RUN_PLACED",
        deal_id=None,
        size=1.0,
        entry=7500.0,
        stop_loss=7449.0,
    )


def test_generate_plan_session_context(mock_components):
    """
    Verifies that the session context (Today's High/Low) uses only today's data.
    """
    (
        mock_client,
        mock_analyst,
        _,
        _,
        mock_market_status,
        _,
        mock_provider,
        mock_engine,
    ) = mock_components

    # Note: Session context logic has moved to MarketDataProvider.
    # This test previously relied on mocking client.fetch_historical_data
    # and checking the formatted string passed to analyst.
    # Now StrategyEngine just calls provider.get_market_context.
    # So this test effectively tests that StrategyEngine passes the result
    # of provider to analyst. The logic of formatting is tested in
    # test_market_data_provider.py (which needs to be robust).

    # We can simulate the provider returning the specific string
    mock_provider.get_market_context.return_value = "Today's High: 7500"

    mock_analyst.analyze_market.return_value = TradingSignal(
        ticker="TEST",
        action=Action.WAIT,
        entry=0,
        stop_loss=0,
        take_profit=0,
        confidence="low",
        reasoning="Context Test",
        size=0,
        atr=0,
        entry_type=EntryType.INSTANT,
        use_trailing_stop=False,
    )

    engine = StrategyEngine("EPIC")
    engine.data_provider = mock_provider
    engine.client = mock_client
    engine.analyst = mock_analyst

    engine.generate_plan()

    # Verify the prompt sent to Gemini
    assert mock_analyst.analyze_market.called
    args, _ = mock_analyst.analyze_market.call_args
    context_str = args[0]

    assert "Today's High: 7500" in context_str


def test_risk_scaling_logic(mock_components):
    """
    Verifies that the risk_scale parameter correctly influences position sizing.
    """
    mock_client, _, _, _, _, _, _, _ = mock_components

    # Setup: Balance 10,000. Risk 1% = £100.
    mock_client.get_account_info.return_value = pd.DataFrame(
        {"accountId": ["TEST_ACC_ID"], "available": [10000.0]}
    )

    # 1. Scale 1.25 (Nasdaq case)
    # Stop distance 100 pts.
    # Expected size: (10000 * 0.01 * 1.25) / 100 = 125 / 100 = 1.25
    engine_high = StrategyEngine("EPIC", risk_scale=1.25, ig_client=mock_client)
    size_high = engine_high._calculate_size(7500, 7400)
    assert size_high == 1.25

    # 2. Scale 0.5 (Australia case)
    # Expected size: (10000 * 0.01 * 0.5) / 100 = 50 / 100 = 0.5
    engine_low = StrategyEngine("EPIC", risk_scale=0.5, ig_client=mock_client)
    size_low = engine_low._calculate_size(7500, 7400)
    assert size_low == 0.5
