import pytest
from unittest.mock import MagicMock, patch
from src.stream_manager import StreamManager

@pytest.fixture
def mock_ig_services():
    with patch("src.stream_manager.IGStreamService") as mock_stream, \
         patch("src.stream_manager.Subscription") as mock_sub:
        yield mock_stream, mock_sub

def test_connect_success(mock_ig_services):
    mock_stream_cls, _ = mock_ig_services
    mock_stream_instance = MagicMock()
    mock_stream_cls.return_value = mock_stream_instance
    
    # Mock authenticated REST service passed in
    mock_rest_service = MagicMock()
    
    manager = StreamManager(mock_rest_service)
    manager.connect()
    
    mock_stream_instance.create_session.assert_called_once()

def test_start_tick_subscription(mock_ig_services):
    mock_stream_cls, mock_sub_cls = mock_ig_services
    mock_stream_instance = MagicMock()
    mock_stream_cls.return_value = mock_stream_instance
    
    mock_sub_instance = MagicMock()
    mock_sub_cls.return_value = mock_sub_instance
    
    manager = StreamManager(MagicMock())
    
    manager.start_tick_subscription("EPIC123", manager._on_price_update)
    
    # Verify subscribe_to_market_ticks was called
    mock_stream_instance.subscribe_to_market_ticks.assert_called_once_with(
        "EPIC123", manager._on_price_update
    )
    # The subscription object is now returned directly by the method
    assert manager.subscription is not None

def test_on_price_update_callback():
    # Setup without full mocks since we just test the callback logic
    manager = StreamManager(MagicMock())
    
    # Mock user callback
    user_callback = MagicMock()
    manager.price_callback = user_callback
    
    # Create a mock Lightstreamer update object
    mock_update = MagicMock()
    mock_update.name = "MARKET:EPIC123"
    mock_update.values = {"BID": 100.0, "OFFER": 101.0}
    
    # Trigger internal listener
    manager._on_price_update(mock_update)
    
    # Verify user callback received processed data
    user_callback.assert_called_once()
    data = user_callback.call_args[0][0]
    assert data['epic'] == "EPIC123"
    assert data['bid'] == 100.0
