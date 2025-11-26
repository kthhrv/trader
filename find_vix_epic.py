from src.ig_client import IGClient
import logging

logging.basicConfig(level=logging.ERROR)

def search_vix():
    client = IGClient()
    try:
        client.authenticate()
        # Search for "Volatility"
        print("Searching for 'Volatility'...")
        markets = client.service.search_markets("Volatility")
        print(f"Raw response type: {type(markets)}")
        if isinstance(markets, list) and markets:
             print(f"First item type: {type(markets[0])}")
             print(f"First item: {markets[0]}")
        
        for market in markets:
            # print(f"Name: {market.instrumentName}, Epic: {market.epic}")
            pass
            
        print("\nSearching for 'VIX'...")
        markets = client.service.search_markets("VIX")
        for market in markets:
            print(f"Name: {market.instrumentName}, Epic: {market.epic}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    search_vix()

