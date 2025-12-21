import pandas as pd
from unittest.mock import MagicMock
from datetime import datetime, timedelta

from src.gemini_analyst import TradingSignal, Action


class MockIGClient:
    def __init__(self):
        self.authenticated = True
        self.service = MagicMock()
        self.service.account_id = "TEST_ACC_ID"
        self.service.account_type = "SPREADBET"

        # Default mock for fetch_historical_data (used by generate_plan)
        self.historical_data_df = self._create_mock_historical_data()
        self.fetch_historical_data = MagicMock(return_value=self.historical_data_df)
        self.fetch_historical_data_by_range = MagicMock(
            return_value=self.historical_data_df
        )

        # Default mock for place_spread_bet_order
        self.place_spread_bet_order = MagicMock(
            return_value={"dealId": "MOCK_DEAL_ID", "dealStatus": "ACCEPTED"}
        )

        # Default mock for get_account_info
        self.get_account_info = MagicMock(
            return_value=pd.DataFrame(
                {
                    "accountId": ["TEST_ACC_ID"],
                    "accountType": ["SPREADBET"],
                    "available": [10000.0],
                    "balance": [10000.0],
                }
            )
        )

        # Mock for fetch_open_position_by_deal_id (initially open, then closed)
        self._open_position_status = True
        self.fetch_open_position_by_deal_id = MagicMock(
            side_effect=self._mock_fetch_open_position
        )

        # Mock for transaction history for P&L
        self.fetch_transaction_history_by_deal_id = MagicMock(
            return_value=self._create_mock_transaction_history()
        )

        # Mock for market info (for spread check)
        self.get_market_info = MagicMock(
            return_value={
                "snapshot": {"bid": 7499.0, "offer": 7500.0}  # Default spread of 1.0
            }
        )

        # Mock for update_open_position
        self.update_open_position = MagicMock(
            return_value={"dealReference": "MOCK_UPDATE_REF"}
        )

    def _create_mock_historical_data(self, num_points=50):
        data = {
            "open": [100.0 + i for i in range(num_points)],
            "high": [105.0 + i for i in range(num_points)],
            "low": [95.0 + i for i in range(num_points)],
            "close": [102.0 + i for i in range(num_points)],
            "volume": [1000 for _ in range(num_points)],
        }
        df = pd.DataFrame(data)
        df.index = pd.to_datetime(
            [
                datetime.now() - timedelta(minutes=(num_points - 1 - i) * 15)
                for i in range(num_points)
            ]
        )
        return df

    def _create_mock_transaction_history(self, deal_id="MOCK_DEAL_ID", pnl=50.0):
        data = {
            "date": [datetime.now().isoformat()],
            "instrumentName": ["FTSE 100"],
            "profitAndLoss": [f"£{pnl}"],
            "transactionType": ["DEAL"],
            "reference": ["closing_ref_for_MOCK_DEAL_ID"],
        }
        return pd.DataFrame(data)

    def _mock_fetch_open_position(self, deal_id: str):
        if self._open_position_status:
            # Return an open position with some PnL
            return {
                "dealId": deal_id,
                "profitAndLoss": 25.0,  # Example PnL
                "bid": 7505.0,
                "offer": 7506.0,
                "size": 1.0,
                "direction": "BUY",
                "openLevel": 7500.0,
            }
        return None  # Simulate position being closed

    def simulate_position_close(self, pnl: float = 50.0):
        self._open_position_status = False
        self.fetch_transaction_history_by_deal_id.return_value = (
            self._create_mock_transaction_history(pnl=pnl)
        )


class MockGeminiAnalyst:
    def __init__(self):
        # Default mock for analyze_market - returns a simple BUY signal
        self.analyze_market = MagicMock(
            return_value=TradingSignal(
                ticker="FTSE100",
                action=Action.BUY,
                entry=7500,
                stop_loss=7450,
                take_profit=7600,
                confidence="high",
                reasoning="Mock Buy",
                size=1,
                atr=15.0,
                entry_type="INSTANT",
                use_trailing_stop=True,
            )
        )
        self.generate_post_mortem = MagicMock(return_value="Mock Post-Mortem Report")


class MockStreamManager:
    def __init__(self):
        self.callbacks = {}
        self._trade_callback = None
        self.connect_and_subscribe = MagicMock(
            side_effect=self._connect_and_subscribe_impl
        )
        self.stop = MagicMock()

    def simulate_price_tick(self, epic: str, bid: float, offer: float):
        # Directly call the registered callback with simulated price data
        if epic in self.callbacks:
            self.callbacks[epic](
                {
                    "epic": epic,
                    "bid": bid,
                    "offer": offer,
                    "time": datetime.now().isoformat(),
                    "market_state": "OPEN",
                }
            )

    def subscribe_trade_updates(self, callback):
        self._trade_callback = callback

    def simulate_trade_update(self, payload: dict):
        if self._trade_callback:
            # Payload is expected to be a JSON string inside the stream message
            import json

            self._trade_callback(
                {"type": "trade_update", "payload": json.dumps(payload)}
            )

    def _connect_and_subscribe_impl(self, epic: str, callback):
        self.callbacks[epic] = callback
        # The MagicMock assigned in __init__ will record the calls made to it.


# Mock for MarketStatus
class MockMarketStatus:
    def __init__(self):
        self.is_holiday = MagicMock(return_value=False)


# Mock for TradeLoggerDB
class MockTradeLoggerDB:
    def __init__(self):
        self.log_trade = MagicMock()


# Mock for TradeMonitorDB
class MockTradeMonitorDB:
    def __init__(self, client=None, db_path=None):
        self.client = client
        self.db_path = db_path
        self.monitor_trade = MagicMock()


class MockNewsFetcher:
    def __init__(self):
        self.fetch_news = MagicMock(
            return_value="Mock news context: Market looking stable."
        )
