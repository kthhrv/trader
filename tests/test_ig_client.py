import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
from trading_ig.rest import IGException
from src.ig_client import IGClient

@pytest.fixture
def mock_ig_service():
    with patch("src.ig_client.IGService") as mock:
        yield mock

def test_authenticate_success(mock_ig_service):
    # Setup
    mock_instance = MagicMock()
    mock_ig_service.return_value = mock_instance
    
    client = IGClient()
    client.authenticate()
    
    mock_instance.create_session.assert_called_once()
    assert client.authenticated is True

def test_fetch_historical_data_success(mock_ig_service):
    mock_instance = MagicMock()
    mock_ig_service.return_value = mock_instance
    
    # Mock DataFrame return
    mock_df = pd.DataFrame({'bid': [1, 2], 'ask': [1.1, 2.1]})
    mock_instance.fetch_historical_prices_by_epic_and_num_points.return_value = {'prices': mock_df}
    
    client = IGClient()
    # Pre-auth to skip that step in this test
    client.authenticated = True
    
    df = client.fetch_historical_data("CS.D.GBPUSD.TODAY.IP", "M15", 10)
    
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    mock_instance.fetch_historical_prices_by_epic_and_num_points.assert_called_with(
        "CS.D.GBPUSD.TODAY.IP", "M15", 10
    )

def test_place_spread_bet_order_success(mock_ig_service):
    mock_instance = MagicMock()
    mock_ig_service.return_value = mock_instance
    mock_instance.create_open_position.return_value = {'dealReference': 'REF123'}
    
    client = IGClient()
    client.authenticated = True
    
    response = client.place_spread_bet_order(
        epic="CS.D.FTSE.TODAY.IP",
        direction="BUY",
        size=1,
        stop_level=7450
    )
    
    assert response['dealReference'] == 'REF123'
    mock_instance.create_open_position.assert_called_once()
    call_args = mock_instance.create_open_position.call_args[1]
    assert call_args['currency_code'] == 'GBP'
    assert call_args['expiry'] == 'DFB'
    assert call_args['stop_level'] == 7450
