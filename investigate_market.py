import logging
import sys
from src.ig_client import IGClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("investigator")

def check_market(epic):
    client = IGClient()
    try:
        client.authenticate()
        details = client.service.fetch_market_by_epic(epic)
        
        print("\n--- Market Details for {} ---".format(epic))
        
        # Safe access to nested keys
        dealing_rules = details.get('dealingRules', {})
        snapshot = details.get('snapshot', {})
        instrument = details.get('instrument', {})
        
        min_deal_size = dealing_rules.get('minDealSize', {})
        min_stop = dealing_rules.get('minStepDistance', {}) # Sometimes it's minStepDistance or minNormalStopOrLimitDistance
        
        print(f"Min Deal Size: {min_deal_size.get('value')} {min_deal_size.get('unit')}")
        print(f"Min Stop Distance: {dealing_rules.get('minNormalStopOrLimitDistance', {}).get('value')}")
        print(f"Market Status: {snapshot.get('marketStatus')}")
        print(f"Price: Bid={snapshot.get('bid')} Offer={snapshot.get('offer')}")
        print(f"Currencies: {instrument.get('currencies')}")
        print(f"Lot Size: {instrument.get('lotSize')}")
        print(f"Unit: {instrument.get('unit')}")
        
    except Exception as e:
        logger.error(f"Failed: {e}")

if __name__ == "__main__":
    check_market("IX.D.FTSE.DAILY.IP")
