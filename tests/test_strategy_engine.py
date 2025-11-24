import pytest
from unittest.mock import MagicMock, patch, call
from src.strategy_engine import StrategyEngine, Action, TradingSignal

@pytest.fixture
def mock_components():
    with patch("src.strategy_engine.IGClient") as mock_client_cls, \
         patch("src.strategy_engine.GeminiAnalyst") as mock_analyst_cls, \
         patch("src.strategy_engine.StreamManager") as mock_stream_cls:
        
        mock_client = mock_client_cls.return_value
        mock_analyst = mock_analyst_cls.return_value
        mock_stream = mock_stream_cls.return_value
        
        yield mock_client, mock_analyst, mock_stream

def test_generate_plan_success(mock_components):
    mock_client, mock_analyst, _ = mock_components
    
    # Mock data fetch
    mock_df = MagicMock()
    mock_df.empty = False
    mock_df.tail.return_value.to_string.return_value = "Mock Data String"
    mock_client.fetch_historical_data.return_value = mock_df
    
    # Mock analysis result
    mock_signal = TradingSignal(
        ticker="FTSE", action=Action.BUY, entry=7500, stop_loss=7450, 
        take_profit=7600, confidence="high", reasoning="Test"
    )
    mock_analyst.analyze_market.return_value = mock_signal
    
    engine = StrategyEngine("EPIC")
    engine.generate_plan()
    
    assert engine.active_plan == mock_signal
    mock_client.fetch_historical_data.assert_called_once()
    mock_analyst.analyze_market.assert_called_once()
    assert "Mock Data String" in mock_analyst.analyze_market.call_args[0][0]

def test_on_tick_triggers_buy(mock_components):
    mock_client, _, _ = mock_components
    engine = StrategyEngine("EPIC")
    
    # Setup active plan
    engine.active_plan = TradingSignal(
        ticker="FTSE", action=Action.BUY, entry=7500, stop_loss=7450, 
        take_profit=7600, confidence="high", reasoning="Test"
    )
    
    # Simulate tick that triggers BUY (Offer >= Entry)
    tick_data = {'bid': 7498, 'offer': 7501} # 7501 > 7500
    
    engine._on_tick(tick_data)
    
    assert engine.position_open is True
    mock_client.place_spread_bet_order.assert_called_once()
    call_kwargs = mock_client.place_spread_bet_order.call_args[1]
    assert call_kwargs['direction'] == 'BUY'
    assert call_kwargs['stop_level'] == 7450

def test_on_tick_no_trigger(mock_components):
    mock_client, _, _ = mock_components
    engine = StrategyEngine("EPIC")
    
    engine.active_plan = TradingSignal(
        ticker="FTSE", action=Action.BUY, entry=7500, stop_loss=7450, 
        take_profit=7600, confidence="high", reasoning="Test"
    )
    
    # Simulate tick below entry
    tick_data = {'bid': 7490, 'offer': 7495} 
    
    engine._on_tick(tick_data)
    
    assert engine.position_open is False
    mock_client.place_spread_bet_order.assert_not_called()

