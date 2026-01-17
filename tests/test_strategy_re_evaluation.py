import pytest
from unittest.mock import MagicMock, patch
from src.strategy_engine import StrategyEngine
from src.gemini_analyst import TradingSignal, Action, EntryType


@pytest.fixture
def mock_engine_deps():
    mock_client = MagicMock()
    mock_analyst = MagicMock()
    mock_logger = MagicMock()
    mock_status = MagicMock()
    mock_stream = MagicMock()
    mock_provider = MagicMock()

    mock_status.is_holiday.return_value = False
    mock_stream.connect_and_subscribe.return_value = None

    return {
        "client": mock_client,
        "analyst": mock_analyst,
        "logger": mock_logger,
        "status": mock_status,
        "stream": mock_stream,
        "provider": mock_provider,
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

    # 1. First analysis returns WAIT (validity ignored, defaults to 5 mins retry)
    wait_signal = TradingSignal(
        ticker="TEST.EPIC",
        action=Action.WAIT,
        entry=0,
        stop_loss=0,
        take_profit=0,
        size=0,
        atr=10,
        validity_time_minutes=15,  # Should be overridden to 5 mins
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

    # Add a fallback signal to prevent StopIteration if called extra times
    deps["analyst"].analyze_market.side_effect = [wait_signal, buy_signal, buy_signal]
    deps["provider"].get_market_context.return_value = "Market Data"

    # Initial plan generation
    engine.generate_plan()
    assert engine.active_plan.action == Action.WAIT

    # Execute strategy
    # We patch time to simulate 305 seconds passing (5 mins + buffer)
    start_mock_time = 1000.0
    engine.last_analysis_time = start_mock_time
    current_mock_time = [start_mock_time]

    def mock_time():
        return current_mock_time[0]

    def trigger_reeval(seconds):
        # On first sleep, jump 305 seconds ahead (> 5 mins default WAIT retry)
        if current_mock_time[0] == start_mock_time:
            current_mock_time[0] += 305.0
        else:
            # On subsequent sleeps, jump enough to exit the loop
            current_mock_time[0] += 1.0

    with (
        patch("time.time", side_effect=mock_time),
        patch("time.sleep", side_effect=trigger_reeval),
    ):
        # collection_seconds=350 ensures loop runs long enough for the jump but stops before next check
        engine.execute_strategy(timeout_seconds=10, collection_seconds=350)

    # Verify analyst was called twice (once in generate_plan, once in loop)
    assert deps["analyst"].analyze_market.call_count >= 2

    # Verify we transitioned to BUY
    assert engine.active_plan.action == Action.BUY
    assert engine.active_plan.entry == 100


def test_re_evaluation_expiration_outcome(mock_engine_deps):
    """
    Verifies that an expired BUY plan logs 'EXPIRED' before re-evaluating.
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

    # 1. Initial BUY signal (expired)
    expired_buy = TradingSignal(
        ticker="TEST.EPIC",
        action=Action.BUY,
        entry=100,
        stop_loss=90,
        take_profit=110,
        size=1,
        atr=10,
        validity_time_minutes=1,  # Short validity
        confidence="high",
        reasoning="Buy then expire",
        entry_type=EntryType.INSTANT,
        use_trailing_stop=True,
    )

    # 2. Subsequent WAIT signal
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
        reasoning="Market cooled off",
        entry_type=EntryType.INSTANT,
        use_trailing_stop=False,
    )

    deps["analyst"].analyze_market.side_effect = [expired_buy, new_wait, new_wait]
    deps["provider"].get_market_context.return_value = "Market Data"

    # Initial plan
    engine.generate_plan()
    engine.active_plan_id = 123  # Mock DB ID

    # Execute
    start_mock_time = 1000.0
    engine.last_analysis_time = start_mock_time
    current_mock_time = [start_mock_time]

    def mock_time():
        return current_mock_time[0]

    def trigger_reeval(seconds):
        if current_mock_time[0] == start_mock_time:
            current_mock_time[0] += 65.0  # > 1 min
        else:
            current_mock_time[0] += 1.0

    with (
        patch("time.time", side_effect=mock_time),
        patch("time.sleep", side_effect=trigger_reeval),
    ):
        engine.execute_strategy(timeout_seconds=10, collection_seconds=70)

    # Verify calls
    assert deps["analyst"].analyze_market.call_count >= 2

    # Verify DB update for EXPIRED
    deps["logger"].update_trade_status.assert_called_with(
        row_id=123, outcome="EXPIRED", deal_id=None
    )

    # Verify new plan is WAIT
    assert engine.active_plan.action == Action.WAIT
