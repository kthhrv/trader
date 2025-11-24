import logging
import time
from typing import Callable, Optional
from trading_ig import IGStreamService
from trading_ig.lightstreamer import Subscription
from config import IG_API_KEY, IG_USERNAME, IG_PASSWORD, IG_ACC_ID, IS_LIVE

logger = logging.getLogger(__name__)

class StreamManager:
    def __init__(self, ig_service):
        """
        Initializes the Stream Manager.
        
        Args:
            ig_service: An authenticated IGService instance (from IGClient).
        """
        self.service = ig_service
        self.stream_service = IGStreamService(self.service)
        self.session = None
        self.subscription: Optional[Subscription] = None
        self.price_callback: Optional[Callable] = None
        
    def connect(self):
        """
        Establishes the Lightstreamer connection.
        """
        try:
            self.session = self.stream_service.create_session()
            logger.info("Connected to IG Lightstreamer.")
        except Exception as e:
            logger.error(f"Failed to connect to stream: {e}")
            raise

    def start_tick_subscription(self, epic: str, callback: Callable[[dict], None]):
        """
        Subscribes to L1 prices (Bids/Offers) for a specific epic.
        
        Args:
            epic (str): The instrument epic.
            callback (Callable): Function to call on new price data. 
                                 Signature: callback(item_update)
        """
        self.price_callback = callback
        
        # Subscription for Chart ticks or L1 prices
        # 'MARKET:{epic}' is the standard item for L1 prices
        item = f"MARKET:{epic}"
        fields = ["BID", "OFFER", "HIGH", "LOW", "MARKET_STATE"]
        
        # Subscribe using the high-level method
        self.subscription = self.stream_service.subscribe_to_market_ticks(
            epic,
            self._on_price_update # The callback function
        )
        logger.info(f"Subscribed to {epic}")

    def _on_price_update(self, update):
        """
        Internal listener that forwards updates to the user callback.
        """
        # 'update' is a lightstreamer ItemUpdate object
        # We extract relevant data to a dictionary
        data = {
            "epic": update.name.replace("MARKET:", ""),
            "bid": update.values.get("BID"),
            "offer": update.values.get("OFFER"),
            "market_state": update.values.get("MARKET_STATE")
        }
        
        if self.price_callback:
            self.price_callback(data)

    def stop(self):
        """
        Disconnects the stream.
        """
        if self.subscription:
            self.stream_service.unsubscribe(self.subscription)
        if self.stream_service:
            self.stream_service.disconnect()
        logger.info("Stream stopped.")
