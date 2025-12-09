import pytest
from unittest.mock import MagicMock, patch, ANY
import json
from src.gemini_analyst import GeminiAnalyst, TradingSignal, Action, EntryType
from google.genai import types

# Mock response class to simulate Gemini's return object
class MockGeminiResponse:
    def __init__(self, text_content):
        self.text = text_content

@pytest.fixture
def mock_genai():
    with patch("src.gemini_analyst.genai") as mock:
        yield mock

def test_analyze_market_success(mock_genai):
    # Setup
    mock_client = MagicMock()
    mock_genai.Client.return_value = mock_client
    
    # Define expected JSON response from Gemini
    expected_response = {
        "ticker": "FTSE100",
        "action": "BUY",
        "entry": 7510.0,
        "entry_type": "INSTANT",
        "stop_loss": 7490.0,
        "take_profit": 7550.0,
        "size": 1.0, 
        "atr": 15.0,
        "confidence": "high",
        "reasoning": "Breakout above resistance with strong volume."
    }
    
    mock_client.models.generate_content.return_value = MockGeminiResponse(json.dumps(expected_response))    
    # Execute
    analyst = GeminiAnalyst()
    result = analyst.analyze_market("Some market context", strategy_name="Test Strategy")
    
    # Verify
    assert isinstance(result, TradingSignal)
    assert result.ticker == "FTSE100"
    assert result.action == Action.BUY
    assert result.entry == 7510.0
    assert result.entry_type == EntryType.INSTANT
    assert result.confidence == "high"
    assert result.size == 1.0
    
    # Verify call arguments
    mock_client.models.generate_content.assert_called_once()
    call_kwargs = mock_client.models.generate_content.call_args.kwargs
    
    assert call_kwargs['model'] == "gemini-3-pro-preview"
    assert "Some market context" in call_kwargs['contents']
    assert "Test Strategy" in call_kwargs['contents']
    
    # Check config
    assert 'config' in call_kwargs
    config = call_kwargs['config']
    assert isinstance(config, types.GenerateContentConfig)
    assert config.response_mime_type == "application/json"
    assert config.response_schema is not None

def test_analyze_market_wait_action(mock_genai):
    # Setup
    mock_client = MagicMock()
    mock_genai.Client.return_value = mock_client
    
    # Define expected JSON response from Gemini for a WAIT action
    expected_response = {
        "ticker": "NONE",
        "action": "WAIT",
        "entry": 0.0,
        "entry_type": "INSTANT",
        "stop_loss": 0.0,
        "take_profit": 0.0,
        "size": 0.0,
        "atr": 0.0,
        "confidence": "low",
        "reasoning": "Market conditions are currently unfavorable; awaiting clearer signals."
    }
    
    mock_client.models.generate_content.return_value = MockGeminiResponse(json.dumps(expected_response))
    
    # Execute
    analyst = GeminiAnalyst()
    result = analyst.analyze_market("Some market context for WAIT", strategy_name="Wait Strategy")
    
    # Verify
    assert isinstance(result, TradingSignal)
    assert result.action == Action.WAIT
    assert result.ticker == "NONE"
    assert result.confidence == "low"
    
    # Verify call arguments
    mock_client.models.generate_content.assert_called_once()
    call_kwargs = mock_client.models.generate_content.call_args.kwargs
    assert "Some market context for WAIT" in call_kwargs['contents']
    assert "Wait Strategy" in call_kwargs['contents']

def test_analyze_market_failure_handles_exception(mock_genai):
    # Setup
    mock_client = MagicMock()
    mock_genai.Client.return_value = mock_client
    
    # Simulate an API error
    mock_client.models.generate_content.side_effect = Exception("API Error")
    
    # Execute
    analyst = GeminiAnalyst()
    result = analyst.analyze_market("Context", strategy_name="Test Strategy")
    
    # Verify
    assert result is None

def test_analyze_market_optional_tp(mock_genai):
    # Setup
    mock_client = MagicMock()
    mock_genai.Client.return_value = mock_client
    
    # Define expected JSON response from Gemini with null take_profit
    expected_response = {
        "ticker": "FTSE100",
        "action": "BUY",
        "entry": 7510.0,
        "entry_type": "INSTANT",
        "stop_loss": 7490.0,
        "take_profit": None, # Key check
        "size": 1.0, 
        "atr": 15.0,
        "confidence": "high",
        "use_trailing_stop": True,
        "reasoning": "Breakout above resistance with strong volume, using trailing stop."
    }
    
    mock_client.models.generate_content.return_value = MockGeminiResponse(json.dumps(expected_response))    
    # Execute
    analyst = GeminiAnalyst()
    result = analyst.analyze_market("Some market context", strategy_name="Test Strategy")
    
    # Verify
    assert isinstance(result, TradingSignal)
    assert result.action == Action.BUY
    assert result.take_profit is None
    assert result.use_trailing_stop is True