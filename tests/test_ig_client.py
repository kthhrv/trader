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
        with pytest.raises(Exception, match="Configured Account ID .* not found in DEMO TRADING accounts."):
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
    
    # Mock create_open_position response
    mock_instance.create_open_position.return_value = {'dealReference': 'REF123'}
    
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
    
    # Check that create_open_position was called
    mock_instance.create_open_position.assert_called_once_with(
        currency_code='GBP',
        direction='BUY',
        epic='CS.D.FTSE.TODAY.IP',
        expiry='DFB',
        force_open=True,
        guaranteed_stop=False,
        level=None,
        limit_level=None,
        limit_distance=None,
        order_type='MARKET',
        quote_id=None,
        size=1,
        stop_distance=None,
        stop_level=7450,
        trailing_stop=False,
        trailing_stop_increment=None
    )

def test_place_spread_bet_order_ignores_level_for_market_orders(mock_ig_service):
    """
    Regression Test: Ensure that even if a 'level' is passed (e.g. by StrategyEngine),
    it is forced to None for MARKET orders to avoid IG API validation errors.
    """
    mock_instance = MagicMock()
    mock_ig_service.return_value = mock_instance
    
    mock_instance.create_open_position.return_value = {'dealReference': 'REF123'}
    mock_instance.fetch_deal_by_deal_reference.return_value = {
        'dealStatus': 'ACCEPTED', 
        'dealReference': 'REF123',
        'dealId': 'DEAL456'
    }

    client = IGClient()
    client.authenticated = True
    
    # Act: Call with an explicit level (e.g. 7500)
    client.place_spread_bet_order(
        epic="CS.D.FTSE.TODAY.IP",
        direction="BUY",
        size=1,
        stop_level=7450,
        level=7500 # Explicit level provided
    )
    
    # Assert: Verify that create_open_position received level=None
    mock_instance.create_open_position.assert_called_once()
    call_args = mock_instance.create_open_position.call_args[1]
    assert call_args['order_type'] == 'MARKET'
    assert call_args['level'] is None # Crucial check

def test_close_open_position_success(mock_ig_service):
    mock_instance = MagicMock()
    mock_ig_service.return_value = mock_instance
    
    mock_instance.close_open_position.return_value = {'dealReference': 'CLOSE_REF'}
    
    client = IGClient()
    client.authenticated = True
    
    response = client.close_open_position(
        deal_id="DEAL123",
        direction="SELL",
        size=1,
        epic="CS.D.FTSE.TODAY.IP"
    )
    
    assert response['dealReference'] == 'CLOSE_REF'
    mock_instance.close_open_position.assert_called_once_with(
        deal_id="DEAL123",
        direction="SELL",
        epic="CS.D.FTSE.TODAY.IP",
        expiry="DFB",
        level=None,
        order_type="MARKET",
        quote_id=None,
        size=1
    )

def test_close_open_position_fail(mock_ig_service):
    mock_instance = MagicMock()
    mock_ig_service.return_value = mock_instance
    
    mock_instance.close_open_position.side_effect = Exception("API Error")
    
    client = IGClient()
    client.authenticated = True
    
    with pytest.raises(Exception, match="API Error"):
        client.close_open_position(
            deal_id="DEAL123",
            direction="SELL",
            size=1,
            epic="CS.D.FTSE.TODAY.IP"
        )
