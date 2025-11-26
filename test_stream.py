import time
import logging
import sys
from trading_ig import IGService, IGStreamService
# from trading_ig.lightstreamer import Subscription # Deprecated
from lightstreamer.client import Subscription, SubscriptionListener
from config import IG_USERNAME, IG_PASSWORD, IG_API_KEY, IG_ACC_ID, IS_LIVE

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Define a custom listener class for price updates
class PriceListener(SubscriptionListener):
    def onItemUpdate(self, update):
        """
        Callback when a new price tick is received.
        """
        try:
            # For official client, update is an ItemUpdate object
            # epic = update.getItemName() # Might not be available directly like this in all versions
            # values = update.getFields() # This might be named differently, e.g. getValue
            
            # Let's inspect the object if needed, but standard LS API is:
            # update.getValue("BID"), update.getValue("OFFER")
            
            bid = update.getValue("BID")
            offer = update.getValue("OFFER")
            update_time = update.getValue("UPDATE_TIME")
            item_name = update.getItemName()
            
            logger.info(f"⚡ LIVE TICK [{item_name}]: Bid={bid}, Offer={offer} @ {update_time}")
        except Exception as e:
            logger.error(f"Error processing update: {e}")

def main():
    logger.info(f"Initializing IG Stream Test (Mode: {'LIVE' if IS_LIVE else 'DEMO'})...")
    
    # 1. Authenticate via REST API to establish session and get tokens
    rest_service = IGService(
        IG_USERNAME,
        IG_PASSWORD,
        IG_API_KEY,
        "LIVE" if IS_LIVE else "DEMO",
        acc_number=IG_ACC_ID
    )
    
    try:
        rest_service.create_session()
        logger.info("REST Authentication successful.")
    except Exception as e:
        logger.error(f"REST Authentication failed: {e}")
        return

    # 2. Initialize Streaming Service
    stream_service = IGStreamService(rest_service)
    
    try:
        # 3. Connect to Lightstreamer
        stream_service.create_session()
        logger.info("Streaming Session created. Connected to Lightstreamer.")
        
        # 4. Define Subscription
        epic = "CS.D.FTSE.TODAY.IP" 
        subscription_item = f"L1:{epic}"
        
        logger.info(f"Subscribing to {subscription_item}...")
        
        # Official Client Subscription
        subscription = Subscription(
            mode="MERGE",
            items=[subscription_item],
            fields=["BID", "OFFER", "UPDATE_TIME"]
        )
        # Adapter is often set at client level or defaults, but let's try without explicit adapter first 
        # or set it via setter if needed. IG usually requires "QUOTE_ADAPTER".
        subscription.setDataAdapter("QUOTE_ADAPTER")
        
        # 5. Attach Listener
        subscription.addListener(PriceListener())
        
        # 6. Subscribe
        # Access the underlying LightstreamerClient
        stream_service.ls_client.subscribe(subscription)
        
        logger.info("Subscription sent. Waiting for ticks (Ctrl+C to stop or timeout)...")
        
        # Keep thread alive with a timeout
        TIMEOUT_SECONDS = 120 # For testing, 120 seconds should be enough to see some ticks
        start_time = time.time()
        while True:
            if (time.time() - start_time) > TIMEOUT_SECONDS:
                logger.info(f"Stream test timed out after {TIMEOUT_SECONDS} seconds.")
                break
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Stopping stream (KeyboardInterrupt)...")
    except Exception as e:
        logger.error(f"Streaming Error: {e}")
    finally:
        # Ensure disconnect always happens
        if 'stream_service' in locals() and stream_service:
            stream_service.disconnect()
            logger.info("Stream disconnected.")

if __name__ == "__main__":
    main()
