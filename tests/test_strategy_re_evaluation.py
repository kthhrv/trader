import pytest
from unittest.mock import MagicMock, patch
from src.strategy_engine import StrategyEngine
from src.gemini_analyst import TradingSignal, Action, EntryType


@pytest.fixture
def mock_engine_deps():
    mock_client = MagicMock()
    mock_analyst = MagicMock()
    mock_logger = MagicMock()
    mock_provider = MagicMock()
    mock_stream = MagicMock()
    mock_status = MagicMock()

    # Setup some mock defaults
    mock_status.is_holiday.return_value = False

    return {
        "client": mock_client,
        "analyst": mock_analyst,
        "logger": mock_logger,
        "provider": mock_provider,
        "stream": mock_stream,
        "status": mock_status,
    }


def test_re_evaluation_on_wait_cooldown(mock_engine_deps):
    """
    Verifies that a WAIT plan re-analyzes after its validity period.
    """
    deps = mock_engine_deps
    engine = StrategyEngine(
        "TEST.EPIC",
        ig_client=deps["client"],
        analyst=deps["analyst"],
        trade_logger=deps["logger"],
        market_status=deps["status"],
        stream_manager=deps["stream"],
    )
    engine.data_provider = deps["provider"]

    # 1. First analysis returns WAIT (valid for 1 min)
    wait_signal = TradingSignal(
        ticker="TEST.EPIC",
        action=Action.WAIT,
        entry=0,
        stop_loss=0,
        take_profit=0,
        size=0,
        atr=10,
        validity_time_minutes=1,
        confidence="low",
        reasoning="Wait Test",
        entry_type=EntryType.INSTANT,
        use_trailing_stop=False,
    )

    # 2. Second analysis returns BUY
    buy_signal = TradingSignal(
        ticker="TEST.EPIC",
        action=Action.BUY,
        entry=100,
        stop_loss=90,
        take_profit=110,
        size=1,
        atr=10,
        validity_time_minutes=30,
        confidence="high",
        reasoning="Buy after wait",
        entry_type=EntryType.INSTANT,
        use_trailing_stop=True,
    )

    deps["analyst"].analyze_market.side_effect = [wait_signal, buy_signal]
    deps["provider"].get_market_context.return_value = "Market Data"

    # Initial plan generation
    engine.generate_plan()
    assert engine.active_plan.action == Action.WAIT

    # Execute strategy
    # We patch time to simulate 65 seconds passing
    start_mock_time = 1000.0
    engine.last_analysis_time = start_mock_time
    current_mock_time = [start_mock_time]

    def mock_time():
        return current_mock_time[0]

    def trigger_reeval(seconds):
        # On first sleep, jump 65 seconds ahead (> 1 min)
        if current_mock_time[0] == start_mock_time:
            current_mock_time[0] += 65.0
        else:
            # On subsequent sleeps, jump enough to exit the loop (collection_seconds=0.2)
            current_mock_time[0] += 1.0

    with (
        patch("time.time", side_effect=mock_time),
        patch("time.sleep", side_effect=trigger_reeval),
    ):
        # collection_seconds=100 means it will run as long as our mock time allows
        # we will stop it by returning from trigger_reeval once done
        engine.execute_strategy(timeout_seconds=10, collection_seconds=100)

    # Verify analyst was called twice (once in generate_plan, once in loop)
    assert deps["analyst"].analyze_market.call_count == 2
    # Verify we transitioned to BUY
    assert engine.active_plan.action == Action.BUY


def test_re_evaluation_on_trade_expiration(mock_engine_deps):
    """
    Verifies that a BUY plan expires and re-analyzes if entry not hit.
    """
    deps = mock_engine_deps
    engine = StrategyEngine(
        "TEST.EPIC",
        ig_client=deps["client"],
        analyst=deps["analyst"],
        trade_logger=deps["logger"],
        market_status=deps["status"],
        stream_manager=deps["stream"],
    )
    engine.data_provider = deps["provider"]

    # 1. First analysis returns BUY (valid for 1 min)
    expired_buy = TradingSignal(
        ticker="TEST.EPIC",
        action=Action.BUY,
        entry=150,
        stop_loss=140,
        take_profit=160,
        size=1,
        atr=10,
        validity_time_minutes=1,
        confidence="high",
        reasoning="TTL Test",
        entry_type=EntryType.INSTANT,
        use_trailing_stop=True,
    )

    # 2. Second analysis returns WAIT
    new_wait = TradingSignal(
        ticker="TEST.EPIC",
        action=Action.WAIT,
        entry=0,
        stop_loss=0,
        take_profit=0,
        size=0,
        atr=10,
        validity_time_minutes=30,
        confidence="low",
        reasoning="Wait after expire",
        entry_type=EntryType.INSTANT,
        use_trailing_stop=False,
    )

    deps["analyst"].analyze_market.side_effect = [expired_buy, new_wait]
    deps["provider"].get_market_context.return_value = "Market Data"

    engine.generate_plan()
    engine.active_plan_id = 999

    # Mock prices that don't trigger BUY (Price stays at 100, Entry is 150)
    def mock_stream_prices(epic, callback):
        callback({"epic": epic, "bid": 100, "offer": 101})

    deps["stream"].connect_and_subscribe.side_effect = mock_stream_prices

    start_mock_time = 1000.0
    engine.last_analysis_time = start_mock_time
    current_mock_time = [start_mock_time]

    def mock_time():
        return current_mock_time[0]

    def trigger_expire(seconds):
        if current_mock_time[0] == start_mock_time:
            current_mock_time[0] += 65.0  # Jump past 1 min TTL
        else:
            # End the loop
            current_mock_time[0] += 1000.0

    with (
        patch("time.time", side_effect=mock_time),
        patch("time.sleep", side_effect=trigger_expire),
    ):
        engine.execute_strategy(timeout_seconds=10, collection_seconds=100)

    # Verify expiration was logged
    deps["logger"].update_trade_status.assert_any_call(
        row_id=999, outcome="EXPIRED", deal_id=None
    )

    # Verify analyst called again
    assert deps["analyst"].analyze_market.call_count == 2
    assert engine.active_plan.action == Action.WAIT
