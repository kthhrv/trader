import pytest
from unittest.mock import MagicMock, patch
import json
from src.gemini_analyst import GeminiAnalyst, TradingSignal, Action

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
    mock_model = MagicMock()
    mock_genai.GenerativeModel.return_value = mock_model
    
    # Define expected JSON response from Gemini
    expected_response = {
        "ticker": "FTSE100",
        "action": "BUY",
        "entry": 7510.0,
        "stop_loss": 7490.0,
        "take_profit": 7550.0,
        "confidence": "high",
        "reasoning": "Breakout above resistance with strong volume."
    }
    
    mock_model.generate_content.return_value = MockGeminiResponse(json.dumps(expected_response))
    
    # Execute
    analyst = GeminiAnalyst()
    result = analyst.analyze_market("Some market context")
    
    # Verify
    assert isinstance(result, TradingSignal)
    assert result.ticker == "FTSE100"
    assert result.action == Action.BUY
    assert result.entry == 7510.0
    assert result.confidence == "high"
    
    # Verify call arguments
    mock_model.generate_content.assert_called_once()
    call_args = mock_model.generate_content.call_args
    assert "Some market context" in call_args[0][0]
    
    # Check that genai.GenerationConfig was called with correct parameters
    mock_genai.GenerationConfig.assert_called_with(
        response_mime_type="application/json",
        response_schema=TradingSignal
    )

def test_analyze_market_failure_handles_exception(mock_genai):
    # Setup
    mock_model = MagicMock()
    mock_genai.GenerativeModel.return_value = mock_model
    
    # Simulate an API error
    mock_model.generate_content.side_effect = Exception("API Error")
    
    # Execute
    analyst = GeminiAnalyst()
    result = analyst.analyze_market("Context")
    
    # Verify
    assert result is None
