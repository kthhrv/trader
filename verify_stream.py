import time
import logging
import sys
from src.ig_client import IGClient
from trading_ig import IGStreamService
from lightstreamer.client import Subscription, SubscriptionListener, ItemUpdate, ClientListener, ClientMessageListener

# Configure logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
# Enable Lightstreamer logger
logging.getLogger("lightstreamer.client").setLevel(logging.DEBUG)

logger = logging.getLogger("verify_stream")

class ConnectionListener(ClientListener):
    def on_status_change(self, status):
        print(f"Lightstreamer Status: {status}")

    def on_server_error(self, code, message):
        print(f"Lightstreamer Server Error: {code} - {message}")

class SimpleListener(SubscriptionListener):
    def on_listen_start(self, subscription):
        print(f"Subscription listener started for {subscription.getItems()}")

    def on_subscription(self):
        print("Subscription ACTIVE.")

    def on_unsubscription(self):
        print("Subscription INACTIVE.")

    def on_item_update(self, item_update: ItemUpdate):
        print(f"\n>>> STREAM UPDATE: {item_update.get_item_name()}")
        print(f"    BID: {item_update.get_value('BID')}")
        print(f"    OFFER: {item_update.get_value('OFFER')}")
        print(f"    TIME: {item_update.get_value('UPDATE_TIME')}")
        # Use a global flag or similar to signal main thread to stop after N updates
        global update_count
        update_count += 1
        if update_count >= 5: # Stop after 5 updates
            logger.info("Received 5 updates, stopping.")
            global stop_stream_flag
            stop_stream_flag = True

stop_stream_flag = False
update_count = 0

def main():
    epic = "IX.D.SPTRD.DAILY.IP" # US500 epic (S&P 500)
    
    # 1. Authenticate REST (Needed for tokens)
    logger.info("Authenticating REST Client...")
    client = IGClient()
    client.authenticate()
    
    # HACK: Manually set tokens as attributes because IGStreamService expects them there
    # but IGService (in this version) stores them in session.headers
    client.service.cst = client.service.session.headers.get('CST')
    client.service.x_security_token = client.service.session.headers.get('X-SECURITY-TOKEN')
    
    logger.info(f"Manually set CST: {client.service.cst}")
    logger.info(f"Manually set XST: {client.service.x_security_token}")
    
    # 2. Initialize Stream Service
    logger.info("Initializing Stream Service...")
    stream_service = IGStreamService(client.service)
    
    # 3. Connect
    try:
        logger.info("Connecting to Lightstreamer...")
        # create_session in trading_ig v0.0.22+ handles the LS client creation
        stream_service.create_session()
        
        # HACK: Ensure User is set correctly
        logger.info(f"Setting LS User to: {client.service.account_id}")
        stream_service.ls_client.connectionDetails.setUser(client.service.account_id)
        
        # IMPORTANT: Explicitly start the stream service's background thread
        logger.info("Starting IGStreamService...")
        stream_service.start()
        
        # Attach Connection Listener
        stream_service.ls_client.addListener(ConnectionListener())
        
        # Explicitly connect (should be done by start(), but for redundancy)
        # stream_service.ls_client.connect()
        
    except Exception as e:
        logger.error(f"Connection Failed: {e}")
        return

    # Wait for connection
    logger.info("Waiting for connection to establish...")
    time.sleep(2)

    # 4. Subscribe
    logger.info(f"Subscribing to {epic}...")
    subscription = Subscription(
        mode="MERGE",
        items=[f"L1:{epic}"],
        fields=["BID", "OFFER", "UPDATE_TIME"]
    )
    subscription.addListener(SimpleListener())
    
    try:
        stream_service.ls_client.subscribe(subscription)
        logger.info("Subscription request sent. Waiting for updates (Ctrl+C to stop, or 5 updates).")
        
        # Keep alive until flag is set OR timeout (30 seconds)
        start_time = time.time()
        while not stop_stream_flag:
            if time.time() - start_time > 30:
                logger.warning("Timeout reached (30s). No updates received.")
                break
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Stopping stream due to KeyboardInterrupt...")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
    finally:
        logger.info("Disconnecting stream service.")
        if stream_service.ls_client:
            stream_service.ls_client.unsubscribe(subscription) # Ensure unsubscribe
        stream_service.disconnect()

if __name__ == "__main__":
    main()
