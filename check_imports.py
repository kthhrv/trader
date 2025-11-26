try:
    from lightstreamer.client import Subscription
    print("Import successful: lightstreamer.client.Subscription")
except ImportError:
    print("Import failed: lightstreamer.client.Subscription")

try:
    from trading_ig.lightstreamer import Subscription as IGSubscription
    print("Import successful: trading_ig.lightstreamer.Subscription")
except ImportError:
    print("Import failed: trading_ig.lightstreamer.Subscription")
