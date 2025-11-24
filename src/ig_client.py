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
        self.authenticated = False
        
    def authenticate(self):
        """
        Creates a session with IG.
        """
        try:
            self.service.create_session()
            self.authenticated = True
            logger.info(f"Successfully authenticated to IG ({'LIVE' if IS_LIVE else 'DEMO'}).")
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
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
            pd.DataFrame: A DataFrame with OHLCV data.
        """
        if not self.authenticated:
            self.authenticate()

        try:
            response = self.service.fetch_historical_prices_by_epic_and_num_points(
                epic, "15Min", num_points # Corrected resolution format based on utils.py
            )
            # The library returns a DataFrame directly in 'prices' key often, but let's inspect structure
            # Standard trading-ig returns a dataframe directly from this call in recent versions
            df = response['prices']
            return df
        except Exception as e:
            logger.error(f"Error fetching data for {epic}: {e}")
            raise

    def place_spread_bet_order(self, epic: str, direction: str, size: float, stop_level: float, limit_level: float = None):
        """
        Places a SPREAD BET order.
        Strictly requires a stop_level for risk management.
        
        Args:
            epic (str): Instrument epic.
            direction (str): 'BUY' or 'SELL'.
            size (float): Size per point.
            stop_level (float): Absolute price for stop loss.
            limit_level (float, optional): Absolute price for take profit.
        """
        if not self.authenticated:
            self.authenticate()

        # Basic validation
        if size <= 0:
            raise ValueError("Size must be positive.")
        
        # Determine currency code (assuming GBP based on spread betting context, but ideally fetched from instrument)
        # For simplicity in this method, we rely on the API defaults or simple lookup if needed.
        # However, create_open_position requires currency_code. 
        # We will assume 'GBP' for UK Spread Betting accounts unless specified.
        currency_code = 'GBP' 
        
        try:
            response = self.service.create_open_position(
                currency_code=currency_code,
                direction=direction,
                epic=epic,
                expiry='DFB', # Daily Funded Bet
                force_open=True,
                guaranteed_stop=False, # Can be toggleable
                level=None, # Market order
                limit_level=limit_level,
                order_type='MARKET',
                quote_id=None,
                size=size,
                stop_level=stop_level
            )
            logger.info(f"Order placed: {response['dealReference']}")
            return response
        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            raise

    def get_market_info(self, epic: str):
        """
        Fetches details about a market (min stop distance, etc).
        """
        if not self.authenticated:
            self.authenticate()
            
        return self.service.fetch_market_by_epic(epic)
