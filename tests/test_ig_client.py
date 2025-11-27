import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
from trading_ig.rest import IGException
from src.ig_client import IGClient
import config # Import config to patch IG_ACC_ID

@pytest.fixture
def mock_ig_service():
    with patch("src.ig_client.IGService") as mock:
        yield mock

def test_authenticate_success(mock_ig_service):
    # Setup
    mock_instance = MagicMock()
    mock_ig_service.return_value = mock_instance
    
    # Mock fetch_accounts to return a DataFrame with an account matching IG_ACC_ID
    mock_accounts_data = [
        {'accountId': 'Z65PO8', 'accountType': 'CFD', 'preferred': False, 'available': 10000.0},
        {'accountId': 'Z65PO9', 'accountType': 'SPREADBET', 'preferred': True, 'available': 9761.0}
    ]
    mock_accounts_df = pd.DataFrame(mock_accounts_data)
    mock_instance.fetch_accounts.return_value = mock_accounts_df
    mock_instance.create_session.return_value = None # create_session doesn't return anything specific
    
    with patch.object(config, 'IG_ACC_ID', 'Z65PO9'): # Patch IG_ACC_ID for this test
        client = IGClient()
        client.authenticate()
        
        mock_instance.create_session.assert_called_once()
        assert client.authenticated is True
        assert client.service.account_id == 'Z65PO9'
        assert client.service.account_type == 'SPREADBET'

def test_authenticate_success_no_ig_acc_id(mock_ig_service):
    # Test case when IG_ACC_ID is not set in config, should pick preferred
    mock_instance = MagicMock()
    mock_ig_service.return_value = mock_instance
    
    mock_accounts_data = [
        {'accountId': 'Z65PO8', 'accountType': 'CFD', 'preferred': False, 'available': 10000.0},
        {'accountId': 'Z65PO9', 'accountType': 'SPREADBET', 'preferred': True, 'available': 9761.0}
    ]
    mock_accounts_df = pd.DataFrame(mock_accounts_data)
    mock_instance.fetch_accounts.return_value = mock_accounts_df
    mock_instance.create_session.return_value = None
    
    with patch.object(config, 'IG_ACC_ID', None): # Simulate IG_ACC_ID not set
        client = IGClient()
        client.authenticate()
        
        assert client.authenticated is True
        assert client.service.account_id == 'Z65PO9' # Should pick preferred
        assert client.service.account_type == 'SPREADBET'

def test_authenticate_fail_account_not_found(mock_ig_service):
    # Test case when IG_ACC_ID is set but not found
    mock_instance = MagicMock()
    mock_ig_service.return_value = mock_instance
    
    mock_accounts_data = [
        {'accountId': 'Z65PO8', 'accountType': 'CFD', 'preferred': False, 'available': 10000.0}
    ]
    mock_accounts_df = pd.DataFrame(mock_accounts_data)
    mock_instance.fetch_accounts.return_value = mock_accounts_df
    mock_instance.create_session.return_value = None
    
    with patch.object(config, 'IG_ACC_ID', 'NON_EXISTENT_ACC_ID'):
        client = IGClient()
        with pytest.raises(Exception, match="Configured IG_ACC_ID .* not found"):
            client.authenticate()

def test_authenticate_fail_no_accounts(mock_ig_service):
    # Test case when no accounts are returned
    mock_instance = MagicMock()
    mock_ig_service.return_value = mock_instance
    
    mock_accounts_df = pd.DataFrame()
    mock_instance.fetch_accounts.return_value = mock_accounts_df
    mock_instance.create_session.return_value = None
    
    with patch.object(config, 'IG_ACC_ID', 'Z65PO9'):
        client = IGClient()
        with pytest.raises(Exception, match="No trading accounts found"):
            client.authenticate()

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
    
    # Mock the session.post response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {'dealReference': 'REF123'}
    mock_instance.session.post.return_value = mock_response
    
    # Mock confirmation
    mock_instance.fetch_deal_by_deal_reference.return_value = {
        'dealStatus': 'ACCEPTED', 
        'dealReference': 'REF123',
        'dealId': 'DEAL456'
    }

    client = IGClient()
    client.authenticated = True
    
    confirmation = client.place_spread_bet_order(
        epic="CS.D.FTSE.TODAY.IP",
        direction="BUY",
        size=1,
        stop_level=7450
    )
    
    assert confirmation['dealStatus'] == 'ACCEPTED'
    assert confirmation['dealReference'] == 'REF123'
    
    # Check that session.post was called instead of create_open_position
    mock_instance.session.post.assert_called_once()
    
    # Verify payload
    call_args = mock_instance.session.post.call_args
    payload = call_args[1]['json']
    
    assert payload['epic'] == "CS.D.FTSE.TODAY.IP"
    assert payload['direction'] == "BUY"
    assert payload['size'] == 1
    assert payload['stopLevel'] == 7450
    assert payload['currencyCode'] == 'GBP'
    assert payload['expiry'] == 'DFB'
