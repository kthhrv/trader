from src.strategy_engine import StrategyEngine
from src.gemini_analyst import TradingSignal, Action, EntryType
from tests.mocks import (
    MockIGClient,
    MockGeminiAnalyst,
    MockNewsFetcher,
    MockTradeLoggerDB,
    MockTradeMonitorDB,
    MockStreamManager,
)


def test_tp_overridden_when_trailing_stop():
    """
    Verifies that take_profit is overridden to None (no limit level)
    when use_trailing_stop is True.
    """
    # Setup dependencies
    mock_client = MockIGClient()
    mock_analyst = MockGeminiAnalyst()

    # Setup strategy
    engine = StrategyEngine(
        epic="CS.D.FTSE.TODAY.IP",
        ig_client=mock_client,
        analyst=mock_analyst,
        news_fetcher=MockNewsFetcher(),
        trade_logger=MockTradeLoggerDB(),
        trade_monitor=MockTradeMonitorDB(),
        stream_manager=MockStreamManager(),
        dry_run=False,  # Ensure we test the live order placement path logic (even if mocked)
    )

    # Create a plan with use_trailing_stop=True and a finite TP
    plan = TradingSignal(
        ticker="FTSE100",
        action=Action.BUY,
        entry=7500.0,
        stop_loss=7450.0,
        take_profit=7600.0,  # Should be ignored
        confidence="high",
        reasoning="Test Trailing",
        size=1.0,
        atr=10.0,
        use_trailing_stop=True,  # Key flag
        entry_type=EntryType.INSTANT,
    )

    # Execute internal order placement logic
    engine._place_market_order(plan, current_spread=1.0, dry_run=False)

    # Assertions
    mock_client.place_spread_bet_order.assert_called_once()
    call_args = mock_client.place_spread_bet_order.call_args[1]

    assert call_args["limit_level"] is None, (
        "limit_level should be None when use_trailing_stop is True"
    )
    assert call_args["level"] == 7500.0
    assert call_args["stop_level"] == 7450.0


def test_tp_preserved_when_no_trailing_stop():
    """
    Verifies that take_profit is passed as limit_level
    when use_trailing_stop is False.
    """
    # Setup dependencies
    mock_client = MockIGClient()
    mock_analyst = MockGeminiAnalyst()

    # Setup strategy
    engine = StrategyEngine(
        epic="CS.D.FTSE.TODAY.IP",
        ig_client=mock_client,
        analyst=mock_analyst,
        news_fetcher=MockNewsFetcher(),
        trade_logger=MockTradeLoggerDB(),
        trade_monitor=MockTradeMonitorDB(),
        stream_manager=MockStreamManager(),
        dry_run=False,
    )

    # Create a plan with use_trailing_stop=False and a finite TP
    plan = TradingSignal(
        ticker="FTSE100",
        action=Action.BUY,
        entry=7500.0,
        stop_loss=7450.0,
        take_profit=7600.0,  # Should be preserved
        confidence="high",
        reasoning="Test Fixed TP",
        size=1.0,
        atr=10.0,
        use_trailing_stop=False,  # Key flag
        entry_type=EntryType.INSTANT,
    )

    # Execute internal order placement logic
    engine._place_market_order(plan, current_spread=1.0, dry_run=False)

    # Assertions
    mock_client.place_spread_bet_order.assert_called_once()
    call_args = mock_client.place_spread_bet_order.call_args[1]

    assert call_args["limit_level"] == 7600.0, (
        "limit_level should match take_profit when use_trailing_stop is False"
    )
