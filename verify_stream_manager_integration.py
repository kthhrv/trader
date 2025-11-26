import logging
import sys
import time
from src.ig_client import IGClient
from src.stream_manager import StreamManager

# Configure logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("verify_integration")

def price_update_callback(data):
    print(f"\n>>> PYTHON CALLBACK RECEIVED: {data}")

def main():
    epic = "IX.D.SPTRD.DAILY.IP" # US500
    
    # 1. Authenticate IG Client
    logger.info("Authenticating IG Client...")
    ig_client = IGClient()
    ig_client.authenticate()
    
    # 2. Initialize Stream Manager
    logger.info("Initializing Stream Manager...")
    stream_manager = StreamManager(ig_client)
    
    # 3. Connect and Subscribe
    logger.info(f"Connecting and Subscribing to {epic}...")
    try:
        # This should spawn Node.js, connect, and start streaming
        stream_manager.connect_and_subscribe(epic, price_update_callback)
        
        logger.info("Stream started. Waiting for updates (Ctrl+C to stop)...")
        
        # Keep alive for 30 seconds to observe updates
        for i in range(30):
            time.sleep(1)
            if i % 5 == 0:
                logger.info(f"Main thread heartbeat {i}s...")
                
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        logger.info("Stopping Stream Manager...")
        stream_manager.stop()
        logger.info("Done.")

if __name__ == "__main__":
    main()
