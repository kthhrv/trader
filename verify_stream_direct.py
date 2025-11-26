import logging
import sys
import time
from src.ig_client import IGClient
from lightstreamer.client import LightstreamerClient, ConsoleLoggerProvider, ConsoleLogLevel, ConnectionDetails, Subscription, SubscriptionListener, ItemUpdate, ClientListener

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("verify_stream_direct")

class MyClientListener(ClientListener):
    def on_status_change(self, status):
        logger.info(f"[LS] Status Change: {status}")

    def on_server_error(self, code, message):
        logger.error(f"[LS] Server Error: {code} - {message}")

class MySubscriptionListener(SubscriptionListener):
    def on_item_update(self, item_update: ItemUpdate):
        print(f"\n>>> STREAM UPDATE: {item_update.get_item_name()}")
        print(f"    BID: {item_update.get_value('BID')}")
        print(f"    OFFER: {item_update.get_value('OFFER')}")
        print(f"    TIME: {item_update.get_value('UPDATE_TIME')}")
        
        global update_count, stop_flag
        update_count += 1
        if update_count >= 3:
            logger.info("Received 3 updates. Signal stop.")
            stop_flag = True

update_count = 0
stop_flag = False

def main():
    epic = "IX.D.SPTRD.DAILY.IP" # US500
    
    # 1. Authenticate REST to get tokens
    logger.info("Authenticating REST Client...")
    client = IGClient()
    client.authenticate()
    
    # Extract required details
    cst = client.service.session.headers.get('CST')
    xst = client.service.session.headers.get('X-SECURITY-TOKEN')
    account_id = client.service.account_id
    # Default IG Demo Lightstreamer Endpoint
    ls_endpoint = "https://demo-apd.marketdatasystems.com" 
    
    logger.info(f"CST: {cst}")
    logger.info(f"XST: {xst}")
    logger.info(f"Account: {account_id}")
    
    # 2. Configure Lightstreamer Client Directly
    logger.info("Configuring Lightstreamer Client...")
    
    # Force Lightstreamer internal logging
    logger_provider = ConsoleLoggerProvider(ConsoleLogLevel.DEBUG)
    LightstreamerClient.setLoggerProvider(logger_provider)
    
    # Password is concatenation of CST and XST
    password = f"CST-{cst}|XST-{xst}"
    
    ls_client = LightstreamerClient(ls_endpoint, "DEFAULT")
    
    ls_client.connectionDetails.setUser(account_id)
    ls_client.connectionDetails.setPassword(password)
    ls_client.addListener(MyClientListener())
    
    # 3. Connect
    logger.info("Connecting...")
    ls_client.connect()
    
    # Wait for connection (simple sleep for test)
    time.sleep(5)
    
    # 4. Subscribe
    logger.info(f"Subscribing to multiple formats for {epic}...")
    
    # 1. L1
    sub1 = Subscription("MERGE", [f"L1:{epic}"], ["BID", "OFFER", "UPDATE_TIME"])
    sub1.addListener(MySubscriptionListener())
    ls_client.subscribe(sub1)
    
    # 2. MARKET
    sub2 = Subscription("MERGE", [f"MARKET:{epic}"], ["BID", "OFFER", "UPDATE_TIME"])
    sub2.addListener(MySubscriptionListener())
    ls_client.subscribe(sub2)
    
    # 3. CHART
    sub3 = Subscription("DISTINCT", [f"CHART:{epic}:TICK"], ["BID", "OFFER", "LTP"])
    sub3.addListener(MySubscriptionListener())
    ls_client.subscribe(sub3)
    
    # 5. Loop
    start_time = time.time()
    while not stop_flag:
        if time.time() - start_time > 30:
            logger.warning("Timeout reached.")
            break
        time.sleep(1)
        
    logger.info("Disconnecting...")
    ls_client.unsubscribe(sub)
    ls_client.disconnect()

if __name__ == "__main__":
    main()
