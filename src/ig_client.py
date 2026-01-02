import logging
import pandas as pd
from typing import Optional
from dotenv import dotenv_values
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from trading_ig import IGService
from trading_ig.rest import IGException
from config import IG_API_KEY, IG_USERNAME, IG_PASSWORD, IG_ACC_ID, IS_LIVE, ROOT_DIR

# Configure logging
logger = logging.getLogger(__name__)


class IGClient:
    def __init__(self):
        """
        Initializes the IG Client.
        Uses config.IS_LIVE to toggle between DEMO and LIVE environments for TRADING.

        Advanced Feature:
        If running in DEMO mode (IS_LIVE=False), it checks for a .env.live file.
        If found, it initializes a SECONDARY 'data_service' using those LIVE credentials
        strictly for fetching historical data (bypassing Demo limits), while keeping
        trading on the DEMO account.
        """
        # 1. Primary Service (Trading)
        self.service = IGService(
            IG_USERNAME,
            IG_PASSWORD,
            IG_API_KEY,
            "LIVE" if IS_LIVE else "DEMO",
            acc_number=IG_ACC_ID,
        )
        self._apply_timeout_patch(self.service)

        # 2. Secondary Service (Data) - Default to primary
        self.data_service = self.service
        self.live_data_config = None

        # Check for Live Data Override if we are in Demo mode
        if not IS_LIVE:
            env_live_path = ROOT_DIR / ".env.live"
            if env_live_path.exists():
                logger.info(
                    f"Detected .env.live at {env_live_path} - Attempting to configure Live Data Feed for Demo Bot..."
                )
                config_live = dotenv_values(env_live_path)

                # Check if it's actually enabled/live
                if config_live.get("IS_LIVE", "false").lower() == "true":
                    try:
                        self.data_service = IGService(
                            config_live.get("IG_USERNAME"),
                            config_live.get("IG_PASSWORD"),
                            config_live.get("IG_API_KEY"),
                            "LIVE",
                            acc_number=config_live.get("IG_ACC_ID"),
                        )
                        self._apply_timeout_patch(self.data_service)
                        self.live_data_config = config_live
                        logger.info(
                            "Hybrid Mode Enabled: TRADING on DEMO, DATA from LIVE."
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to initialize Live Data service: {e}. Reverting to Demo data."
                        )
                        self.data_service = self.service

        self.authenticated = False

    def _apply_timeout_patch(self, service_obj):
        """Enforces default timeout on the session."""
        original_request = service_obj.session.request

        def timeout_request(*args, **kwargs):
            if "timeout" not in kwargs:
                kwargs["timeout"] = 10
            return original_request(*args, **kwargs)

        service_obj.session.request = timeout_request

    def _authenticate_service(
        self, service_obj, username, password, acc_id_target, env_label
    ):
        """
        Helper to authenticate a specific service instance and set the account.
        """
        try:
            # 1. Create Session
            service_obj.create_session()

            # 2. Fetch Accounts
            accounts_df = service_obj.fetch_accounts()

            if accounts_df.empty:
                raise Exception(f"No trading accounts found for {env_label}.")

            target_account = None
            if acc_id_target:
                # Filter by Account ID
                filtered_accounts = accounts_df[
                    accounts_df["accountId"] == acc_id_target
                ]
                if not filtered_accounts.empty:
                    target_account = filtered_accounts.iloc[0]
                else:
                    raise Exception(
                        f"Configured Account ID ({acc_id_target}) not found in {env_label} accounts."
                    )
            else:
                # Preference logic
                preferred_accounts = accounts_df[accounts_df["preferred"]]
                if not preferred_accounts.empty:
                    target_account = preferred_accounts.iloc[0]
                elif not accounts_df.empty:
                    target_account = accounts_df.iloc[0]
                else:
                    raise Exception(f"No preferred account found for {env_label}.")

            # 3. Set Context
            service_obj.account_id = target_account["accountId"]
            service_obj.account_type = target_account["accountType"]

            logger.info(
                f"Authenticated {env_label} Service: {service_obj.account_id} ({service_obj.account_type})"
            )

        except Exception as e:
            logger.error(f"Authentication failed for {env_label}: {e}")
            raise

    def authenticate(self):
        """
        Authenticates the trading service (and data service if separate).
        """
        try:
            # 1. Authenticate Trading Service
            self._authenticate_service(
                self.service,
                IG_USERNAME,
                IG_PASSWORD,
                IG_ACC_ID,
                "LIVE TRADING" if IS_LIVE else "DEMO TRADING",
            )

            # 2. Authenticate Data Service (if separate)
            if self.data_service != self.service and self.live_data_config:
                self._authenticate_service(
                    self.data_service,
                    self.live_data_config.get("IG_USERNAME"),
                    self.live_data_config.get("IG_PASSWORD"),
                    self.live_data_config.get("IG_ACC_ID"),
                    "LIVE DATA",
                )

            self.authenticated = True
        except Exception as e:
            self.authenticated = False
            raise e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((IGException, ConnectionError)),
    )
    def fetch_historical_data(
        self, epic: str, resolution: str, num_points: int
    ) -> pd.DataFrame:
        """
        Fetches historical OHLC data. Uses data_service (Live or Demo).
        """
        if not self.authenticated:
            self.authenticate()

        try:
            # Use data_service here
            response = self.data_service.fetch_historical_prices_by_epic_and_num_points(
                epic, resolution, num_points
            )
            df = response["prices"]
            return self._process_historical_df(df)
        except Exception as e:
            logger.error(f"Error fetching data for {epic}: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((IGException, ConnectionError)),
    )
    def fetch_historical_data_by_range(
        self, epic: str, resolution: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """
        Fetches historical OHLC data by range. Uses data_service (Live or Demo).
        """
        if not self.authenticated:
            self.authenticate()

        try:
            # Use data_service here
            response = self.data_service.fetch_historical_prices_by_epic_and_date_range(
                epic, resolution, start_date, end_date
            )
            df = response["prices"]
            return self._process_historical_df(df)
        except Exception as e:
            logger.error(f"Error fetching historical range for {epic}: {e}")
            raise

    def _process_historical_df(self, df: pd.DataFrame) -> pd.DataFrame:
        if isinstance(df.columns, pd.MultiIndex):
            # Extract volume if it exists at the top level
            volume_col = None
            if "Volume" in df.columns.get_level_values(0):
                volume_col = df["Volume"]

            if "bid" in df.columns.get_level_values(0):
                df = df["bid"]
            elif "last" in df.columns.get_level_values(0):
                df = df["last"]
            elif "ask" in df.columns.get_level_values(0):
                df = df["ask"]

            # Re-attach volume if we extracted it
            if volume_col is not None:
                # If volume_col is a DataFrame (e.g. from multi-index), take its first column
                if isinstance(volume_col, pd.DataFrame):
                    df["volume"] = volume_col.iloc[:, 0]
                else:
                    df["volume"] = volume_col

        df.rename(
            columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            },
            inplace=True,
        )
        return df

    def place_spread_bet_order(
        self,
        epic: str,
        direction: str,
        size: float,
        stop_level: float,
        level: float = None,
        limit_level: float = None,
    ):
        """
        Places a SPREAD BET order using self.service (TRADING service).
        Refactored to use the trading_ig library's create_open_position method.
        """
        if not self.authenticated:
            self.authenticate()

        if size <= 0:
            raise ValueError("Size must be positive.")

        currency_code = "GBP"

        try:
            logger.info(
                f"Placing Spread Bet: Epic={epic}, Dir={direction}, Size={size}, Stop={stop_level}, Limit={limit_level}"
            )

            # Use self.service.create_open_position
            response = self.service.create_open_position(
                currency_code=currency_code,
                direction=direction,
                epic=epic,
                expiry="DFB",  # DFB for Daily Funded Bet (Spread Bet)
                force_open=True,
                guaranteed_stop=False,
                level=None,  # MARKET orders execute at current price, level must be None
                limit_level=limit_level,
                limit_distance=None,
                order_type="MARKET",
                quote_id=None,
                size=size,
                stop_distance=None,
                stop_level=stop_level,
                trailing_stop=False,  # Trailing stop is managed manually in TradeMonitorDB
                trailing_stop_increment=None,
            )

            if "dealReference" in response:
                deal_ref = response["dealReference"]
                logger.info(f"Order Submitted. Deal Ref: {deal_ref}")

                confirmation = self.service.fetch_deal_by_deal_reference(deal_ref)

                if confirmation["dealStatus"] == "ACCEPTED":
                    logger.info(f"Market Order ACCEPTED: {deal_ref}")
                    return confirmation
                else:
                    logger.error(f"Market Order REJECTED Full Details: {confirmation}")
                    reason = confirmation.get("reason", "Unknown")
                    raise Exception(f"Order rejected: {reason}")
            else:
                # Should not happen with successful library call, but handling just in case
                logger.error(
                    f"Unexpected response format from create_open_position: {response}"
                )
                raise Exception(f"API Error: Unexpected response {response}")

        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            raise

    def update_open_position(
        self, deal_id: str, stop_level: float = None, limit_level: float = None
    ):
        if not self.authenticated:
            self.authenticate()

        try:
            # Use self.service
            response = self.service.update_open_position(
                deal_id=deal_id, stop_level=stop_level, limit_level=limit_level
            )
            logger.info(
                f"Updated position {deal_id}: Stop={stop_level}, Limit={limit_level}. Response: {response}"
            )
            return response

        except Exception as e:
            logger.error(f"Failed to update position {deal_id}: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((IGException, ConnectionError)),
    )
    def fetch_open_position_by_deal_id(self, deal_id: str):
        if not self.authenticated:
            self.authenticate()

        try:
            # Use self.service
            positions = self.service.fetch_open_positions()

            if isinstance(positions, pd.DataFrame):
                if positions.empty:
                    return None
                if "dealId" in positions.columns:
                    row = positions[positions["dealId"] == deal_id]
                    if not row.empty:
                        return row.iloc[0].to_dict()

            elif isinstance(positions, dict):
                if "positions" in positions:
                    for pos in positions["positions"]:
                        if pos.get("position", {}).get("dealId") == deal_id:
                            return pos
            return None
        except Exception as e:
            logger.error(f"Error fetching position {deal_id}: {e}")
            return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((IGException, ConnectionError)),
    )
    def get_market_info(self, epic: str):
        """
        Fetches details about a market (min stop distance, etc).
        Using trading service to ensure consistency with trading rules.
        """
        if not self.authenticated:
            self.authenticate()

        return self.service.fetch_market_by_epic(epic)

    def get_account_info(self):
        """
        Fetches account details for the TRADING account.
        """
        if not self.authenticated:
            self.authenticate()

        return self.service.fetch_accounts()

    def close_open_position(
        self,
        deal_id: Optional[str],
        direction: str,
        size: float,
        epic: Optional[str] = None,
        expiry: Optional[str] = "DFB",
    ):
        """
        Closes an open position by placing an opposing market order.
        In IG API, deal_id and (epic, expiry) are often mutually exclusive for closure.
        """
        if not self.authenticated:
            self.authenticate()

        if not deal_id and not epic:
            raise ValueError(
                "Either deal_id or epic must be provided to close a position."
            )

        try:
            logger.info(
                f"Attempting to CLOSE position: DealID={deal_id}, Epic={epic}, Dir={direction}, Size={size}"
            )
            response = self.service.close_open_position(
                deal_id=deal_id,
                direction=direction,
                epic=epic,
                expiry=expiry,
                level=None,
                order_type="MARKET",
                quote_id=None,
                size=size,
            )
            logger.info(f"Close Position Response: {response}")
            return response
        except Exception as e:
            logger.error(f"Failed to close position: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((IGException, ConnectionError)),
    )
    def fetch_transaction_history_by_deal_id(self, deal_id: str):
        """
        Fetches transaction history for the TRADING account.
        """
        if not self.authenticated:
            self.authenticate()

        try:
            return self.service.fetch_transaction_history()
        except Exception as e:
            logger.error(f"Error fetching transaction history: {e}")
            return None
