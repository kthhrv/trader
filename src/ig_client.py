import time
import logging
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from trading_ig import IGService
from trading_ig.rest import IGException
from config import IG_API_KEY, IG_USERNAME, IG_PASSWORD, IG_ACC_ID, IS_LIVE

# Configure logging
logger = logging.getLogger(__name__)

class IGClient:
    def __init__(self):
        """
        Initializes the IG Client.
        Uses config.IS_LIVE to toggle between DEMO and LIVE environments.
        """
        self.service = IGService(
            IG_USERNAME,
            IG_PASSWORD,
            IG_API_KEY,
            "LIVE" if IS_LIVE else "DEMO",
            acc_number=IG_ACC_ID
        )
        
        # Enforce default timeout on the session to prevent hangs
        # This monkey-patches the session object used by trading_ig
        original_request = self.service.session.request
        def timeout_request(*args, **kwargs):
            if 'timeout' not in kwargs:
                kwargs['timeout'] = 10 # Default 10s timeout
            return original_request(*args, **kwargs)
        self.service.session.request = timeout_request
        
        self.authenticated = False
        
    def authenticate(self):
        """
        Creates a session with IG and explicitly selects the configured account.
        """
        try:
            # 1. Create a general session first
            self.service.create_session()
            
            # 2. Fetch all available accounts
            accounts_df = self.service.fetch_accounts()
            
            if accounts_df.empty:
                raise Exception("No trading accounts found for the provided credentials.")

            target_account = None
            if IG_ACC_ID:
                # Filter by IG_ACC_ID from config
                filtered_accounts = accounts_df[accounts_df['accountId'] == IG_ACC_ID]
                if not filtered_accounts.empty:
                    target_account = filtered_accounts.iloc[0]
                else:
                    raise Exception(f"Configured IG_ACC_ID ({IG_ACC_ID}) not found among available accounts.")
            else:
                # If no specific account ID is configured, use the preferred account
                preferred_accounts = accounts_df[accounts_df['preferred'] == True]
                if not preferred_accounts.empty:
                    target_account = preferred_accounts.iloc[0]
                elif not accounts_df.empty:
                    # Fallback to the first account if no preferred and no specific ID
                    target_account = accounts_df.iloc[0]
                else:
                    raise Exception("No preferred account found and no specific IG_ACC_ID configured.")

            if target_account is None:
                raise Exception("Could not determine target trading account.")

            # 3. Explicitly set the service's account context
            self.service.account_id = target_account['accountId']
            self.service.account_type = target_account['accountType']
            
            self.authenticated = True
            logger.info(f"Successfully authenticated to IG ({'LIVE' if IS_LIVE else 'DEMO'}) with account: {self.service.account_id} ({self.service.account_type}).")
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            raise

    @retry(
        stop=stop_after_attempt(1),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((IGException, ConnectionError))
    )
    def fetch_historical_data(self, epic: str, resolution: str, num_points: int) -> pd.DataFrame:
        """
        Fetches historical OHLC data for a given epic.
        
        Args:
            epic (str): The instrument identifier (e.g., 'CS.D.GBPUSD.TODAY.IP').
            resolution (str): The chart resolution (e.g., 'M15', 'H1').
            num_points (int): Number of data points to retrieve.
            
        Returns:
            pd.DataFrame: A DataFrame with 'open', 'high', 'low', 'close', 'volume' columns.
        """
        if not self.authenticated:
            self.authenticate()

        try:
            response = self.service.fetch_historical_prices_by_epic_and_num_points(
                epic, resolution, num_points
            )
            # The library returns a DataFrame directly in 'prices' key
            df = response['prices']
            return self._process_historical_df(df)
        except Exception as e:
            logger.error(f"Error fetching data for {epic}: {e}")
            raise

    @retry(
        stop=stop_after_attempt(1),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((IGException, ConnectionError))
    )
    def fetch_historical_data_by_range(self, epic: str, resolution: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetches historical OHLC data for a given epic within a specific date range.
        
        Args:
            epic (str): Instrument epic.
            resolution (str): Resolution string (e.g., '1Min', '15Min').
            start_date (str): Start datetime (YYYY-MM-DD HH:MM:SS).
            end_date (str): End datetime (YYYY-MM-DD HH:MM:SS).
            
        Returns:
            pd.DataFrame: A DataFrame with 'open', 'high', 'low', 'close', 'volume' columns.
        """
        if not self.authenticated:
            self.authenticate()

        try:
            response = self.service.fetch_historical_prices_by_epic_and_date_range(
                epic, resolution, start_date, end_date
            )
            df = response['prices']
            return self._process_historical_df(df)
        except Exception as e:
            logger.error(f"Error fetching historical range for {epic}: {e}")
            raise

    def _process_historical_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Helper to process and standardize the historical prices DataFrame.
        """
        # Handle MultiIndex columns (trading_ig usually returns (price_type, ohlc))
        if isinstance(df.columns, pd.MultiIndex):
            # Prefer 'bid' prices, fallback to 'last' or 'ask'
            if 'bid' in df.columns.get_level_values(0):
                df = df['bid']
            elif 'last' in df.columns.get_level_values(0):
                df = df['last']
            elif 'ask' in df.columns.get_level_values(0):
                df = df['ask']
        
        # Rename columns to standard lowercase for pandas_ta
        df.rename(columns={
            'Open': 'open',
            'High': 'high', 
            'Low': 'low', 
            'Close': 'close',
            'Volume': 'volume'
        }, inplace=True)
        
        return df

    def place_spread_bet_order(self, epic: str, direction: str, size: float, stop_level: float, level: float = None, limit_level: float = None):
        """
        Places a SPREAD BET order using direct REST API call to bypass wrapper limitations.
        Strictly requires a stop_level for risk management.
        
        Args:
            epic (str): Instrument epic.
            direction (str): 'BUY' or 'SELL'.
            size (float): Size per point.
            stop_level (float): Absolute price for stop loss.
            level (float, optional): Entry level (for Market order context, usually None or current price).
            limit_level (float, optional): Absolute price for take profit.
        """
        if not self.authenticated:
            self.authenticate()

        # Basic validation
        if size <= 0:
            raise ValueError("Size must be positive.")
        
        currency_code = 'GBP' 
        
        # Construct payload manually, omitting None values
        payload = {
            "epic": epic,
            "direction": direction,
            "size": size,
            "expiry": "DFB",
            "orderType": "MARKET",
            "currencyCode": currency_code,
            "forceOpen": True,
            "guaranteedStop": False,
            "timeInForce": "FILL_OR_KILL"
        }

        # Only add optional fields if they have values
        if stop_level is not None:
            payload["stopLevel"] = stop_level
        
        if limit_level is not None:
            payload["limitLevel"] = limit_level

        try:
            # Direct POST to V2 endpoint
            # Determine base URL based on config
            from config import IS_LIVE
            base_url = "https://api.ig.com/gateway/deal" if IS_LIVE else "https://demo-api.ig.com/gateway/deal"
            endpoint = "/positions/otc"
            url = f"{base_url}{endpoint}"
            
            # Headers are managed by the session, but we ensure version 2
            headers = {"Version": "2"}
            
            logger.info(f"Sending Order Payload: {payload}")
            
            response = self.service.session.post(url, json=payload, headers=headers)
            
            if response.status_code != 200:
                logger.error(f"Order failed with status {response.status_code}: {response.text}")
                raise Exception(f"API Error: {response.text}")
            
            response_data = response.json()
            deal_ref = response_data['dealReference']
            logger.info(f"Order Submitted. Deal Ref: {deal_ref}")

            # Check confirmation
            confirmation = self.service.fetch_deal_by_deal_reference(deal_ref)
            
            if confirmation['dealStatus'] == 'ACCEPTED':
                logger.info(f"Market Order ACCEPTED: {deal_ref}")
                return confirmation
            else:
                logger.error(f"Market Order REJECTED Full Details: {confirmation}")
                reason = confirmation.get('reason', 'Unknown')
                raise Exception(f"Order rejected: {reason}")

        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            raise

    def update_open_position(self, deal_id: str, stop_level: float = None, limit_level: float = None):
        """
        Updates an existing open position (e.g. moves stop loss or take profit).
        """
        if not self.authenticated:
            self.authenticate()

        try:
            response = self.service.edit_open_position(
                deal_id=deal_id,
                stop_level=stop_level,
                limit_level=limit_level
            )
            
            # Check confirmation if available, or just return response
            logger.info(f"Updated position {deal_id}: Stop={stop_level}, Limit={limit_level}. Response: {response}")
            return response

        except Exception as e:
            logger.error(f"Failed to update position {deal_id}: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((IGException, ConnectionError))
    )
    def fetch_open_position_by_deal_id(self, deal_id: str):
        """
        Fetches a specific open position by its Deal ID.
        Returns None if not found (implied closed).
        """
        if not self.authenticated:
            self.authenticate()
            
        try:
            positions = self.service.fetch_open_positions()
            
            # Handle DataFrame response (typical for trading_ig)
            if isinstance(positions, pd.DataFrame):
                if positions.empty:
                    return None
                
                # Check for 'dealId' column
                if 'dealId' in positions.columns:
                    # Filter for the specific deal ID
                    row = positions[positions['dealId'] == deal_id]
                    if not row.empty:
                        return row.iloc[0].to_dict()
                        
            # Handle Dict response (fallback)
            elif isinstance(positions, dict):
                if 'positions' in positions:
                    for pos in positions['positions']:
                        if pos.get('position', {}).get('dealId') == deal_id:
                             return pos
            
            return None
        except Exception as e:
            logger.error(f"Error fetching position {deal_id}: {e}")
            return None

    def place_working_order(self, epic: str, direction: str, order_type: str, size: float, level: float, stop_level: float, limit_level: float = None):
        """
        Places a WORKING order (STOP or LIMIT) to be executed when price hits a level.
        
        Args:
            epic (str): Instrument epic.
            direction (str): 'BUY' or 'SELL'.
            order_type (str): 'STOP' (Breakout) or 'LIMIT' (Mean Reversion).
            size (float): Size per point.
            level (float): The price level to trigger the order.
            stop_level (float): Absolute price for stop loss.
            limit_level (float, optional): Absolute price for take profit.
        """
        if not self.authenticated:
            self.authenticate()

        if size <= 0:
            raise ValueError("Size must be positive.")
        
        currency_code = 'GBP' 
        
        try:
            response = self.service.create_working_order(
                currency_code=currency_code,
                direction=direction,
                epic=epic,
                expiry='DFB', 
                guaranteed_stop=False,
                level=level, 
                size=size,
                time_in_force='GOOD_TILL_CANCELLED', 
                order_type=order_type,
                limit_level=limit_level,
                stop_level=stop_level
            )
            
            # Check confirmation for actual status
            deal_ref = response['dealReference']
            confirmation = self.service.fetch_deal_by_deal_reference(deal_ref)
            
            if confirmation['dealStatus'] == 'ACCEPTED':
                logger.info(f"Working Order ACCEPTED: {deal_ref}")
                return response
            else:
                reason = confirmation.get('reason', 'Unknown')
                logger.error(f"Working Order REJECTED: {reason}")
                raise Exception(f"Order rejected: {reason}")

        except Exception as e:
            logger.error(f"Working Order placement failed: {e}")
            raise

        return self.service.fetch_market_by_epic(epic)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((IGException, ConnectionError))
    )
    def get_market_info(self, epic: str):
        """
        Fetches details about a market (min stop distance, etc).
        """
        if not self.authenticated:
            self.authenticate()
            
        return self.service.fetch_market_by_epic(epic)

    def get_account_info(self):
        """
        Fetches account details including available balance.
        """
        if not self.authenticated:
            self.authenticate()
            
        return self.service.fetch_accounts()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((IGException, ConnectionError))
    )
    def fetch_transaction_history_by_deal_id(self, deal_id: str):
        """
        Fetches transaction history to find the closing details for a specific opening Deal ID.
        Note: This searches recent history (e.g., last 24 hours) which is sufficient for this bot's intraday scope.
        """
        if not self.authenticated:
            self.authenticate()
            
        try:
            # Fetch last 24 hours of transaction history
            # 'milliseconds' might be needed, or just standard 'ALL' type
            history = self.service.fetch_transaction_history()
            
            if isinstance(history, pd.DataFrame):
                if history.empty:
                    return None
                
                # Depending on the dataframe columns, we look for the reference to our deal_id
                # Often the closing transaction has 'openDateUtc' or similar linking it, 
                # or a 'reference' field. 
                # However, trading_ig's fetch_transaction_history usually returns a DF.
                # Columns often include: 'date', 'instrumentName', 'period', 'profitAndLoss', 'transactionType', 'reference'
                
                # We need to find the transaction where 'reference' matches or is related.
                # Actually, for a closing trade, the 'reference' is unique to the close.
                # But usually there's a link.
                # Let's try to filter by instrument and timestamp first if we can't match deal ID directly.
                
                # A better approach might be using /confirms/{dealId} but that's for the OPENING deal.
                # To get the closing PnL, we need the deal that CLOSED it.
                
                # Let's iterate and see if we can match 'profitAndLoss' if we can't match ID.
                # But we want precision.
                
                # Wait, fetch_transaction_history args: trans_type=None, from_date=None, to_date=None, max_span_seconds=None
                # Let's fetch ALL recent transactions.
                
                # Filter for rows where we might find the PnL.
                # Unfortunately, linking an opening Deal ID to a closing transaction in the generic history 
                # can be tricky without the closing Deal ID.
                
                # Alternative: IG API has /history/transactions which might contain the link.
                
                # Let's just return the dataframe for the monitor to process, or try to find it here.
                # For now, let's return the whole recent history (last few minutes/hours) 
                # and let the monitor logic try to find the matching instrument and 'DEAL' type close.
                return history
            
            return None
        except Exception as e:
            logger.error(f"Error fetching transaction history: {e}")
            return None
