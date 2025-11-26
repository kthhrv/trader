from src.ig_client import IGClient
import logging

logging.basicConfig(level=logging.ERROR)

def inspect_ig_service():
    client = IGClient()
    # We don't strictly need to authenticate to inspect methods, but some might be dynamic.
    # Let's inspect the static class/object attributes.
    
    methods = [m for m in dir(client.service) if 'calendar' in m.lower() or 'event' in m.lower()]
    print("Possible Calendar Methods:", methods)

if __name__ == "__main__":
    inspect_ig_service()
