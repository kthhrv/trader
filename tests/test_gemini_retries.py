import pytest
from unittest.mock import MagicMock, patch
from src.gemini_analyst import GeminiAnalyst
from google.genai import errors


@pytest.fixture
def mock_genai_retry():
    with patch("src.gemini_analyst.genai") as mock:
        yield mock


def test_analyze_market_retries_on_503(mock_genai_retry):
    """
    Verifies that analyze_market retries once when a 503 ServerError occurs.
    1st Attempt: 503 Error
    2nd Attempt: Success
    """
    mock_client = MagicMock()
    mock_genai_retry.Client.return_value = mock_client

    # Define a success response for the 2nd call
    import json

    success_json = json.dumps(
        {
            "ticker": "FTSE100",
            "action": "BUY",
            "entry": 7510.0,
            "entry_type": "INSTANT",
            "stop_loss": 7490.0,
            "take_profit": 7550.0,
            "size": 1.0,
            "atr": 15.0,
            "confidence": "high",
            "reasoning": "Success after retry",
            "use_trailing_stop": True,
        }
    )

    # Mock part structure for the success response
    mock_part = MagicMock()
    mock_part.thought = False
    mock_part.text = success_json
    mock_content = MagicMock()
    mock_content.parts = [mock_part]
    mock_candidate = MagicMock()
    mock_candidate.content = mock_content
    mock_response = MagicMock()
    mock_response.text = success_json
    mock_response.candidates = [mock_candidate]

    # Side effect: 1st call fails with 503, 2nd call succeeds
    mock_client.models.generate_content.side_effect = [
        errors.ServerError("503 Service Unavailable", response_json={}),
        mock_response,
    ]

    analyst = GeminiAnalyst()

    # We need to monkeypatch the wait in tenacity to make the test fast
    with patch("tenacity.nap.time.sleep", return_value=None):
        result = analyst.analyze_market("Context")

    assert result is not None
    assert result.reasoning == "Success after retry"
    assert mock_client.models.generate_content.call_count == 2


def test_analyze_market_fails_after_all_retries(mock_genai_retry):
    """
    Verifies that analyze_market raises ServerError if all retries are exhausted.
    """
    mock_client = MagicMock()
    mock_genai_retry.Client.return_value = mock_client

    # Side effect: Always fails with 503
    mock_client.models.generate_content.side_effect = errors.ServerError(
        "503 Service Unavailable", response_json={}
    )

    analyst = GeminiAnalyst()

    # We need to monkeypatch the wait in tenacity to make the test fast
    with patch("tenacity.nap.time.sleep", return_value=None):
        with pytest.raises(errors.ServerError):
            analyst.analyze_market("Context")

    # tenacity tries twice (stop_after_attempt(2)), so 2 calls total
    assert mock_client.models.generate_content.call_count == 2
