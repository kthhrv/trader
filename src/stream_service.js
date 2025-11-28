const ls = require('lightstreamer-client');

// Basic logging to stderr for Python to capture
function log_error(message) { console.error("[NODE_STREAM_ERROR] " + message); }
function log_info(message) { console.log("[NODE_STREAM_INFO] " + message); }
function log_data(data) { console.log(JSON.stringify(data)); } // JSON to stdout

log_info("Node.js Stream Service starting...");

// --- Configuration from CLI arguments ---
const args = process.argv.slice(2); // Remove 'node' and 'script_name.js'

const cst = args[0];
const xst = args[1];
const account_id = args[2];
const epic = args[3];
const ls_endpoint = args[4] || "https://demo-apd.marketdatasystems.com";

if (!cst || !xst || !account_id || !epic) {
    log_error("Missing required arguments: CST, XST, Account ID, and Epic.");
    process.exit(1);
}

log_info(`Connecting to LS: ${ls_endpoint}, Account: ${account_id}, Epic: ${epic}`);

// --- Lightstreamer Client Setup ---
const client = new ls.LightstreamerClient(ls_endpoint, "DEFAULT");

client.connectionDetails.setUser(account_id);
client.connectionDetails.setPassword(`CST-${cst}|XST-${xst}`);

client.addListener({
  onStatusChange: function(newStatus) {
    log_info("[LS Status]: " + newStatus);
    if (newStatus === "DISCONNECTED") {
        log_error("Lightstreamer disconnected. Exiting.");
        process.exit(1); // Exit so Python can restart us
    }
  },
  onServerError: function(code, msg) {
    log_error(`[LS Server Error]: ${code} - ${msg}`);
  },
  onLoadError: function(url) {
    log_error(`[LS Load Error]: ${url}`);
  }
});

client.connect();

// --- Subscription 1: Market Data (MERGE) ---
const priceSub = new ls.Subscription("MERGE", [`L1:${epic}`], ["BID", "OFFER", "UPDATE_TIME", "MARKET_STATE"]);

priceSub.addListener({
  onItemUpdate: function(update) {
    const bid = update.getValue("BID");
    const offer = update.getValue("OFFER");
    const update_time = update.getValue("UPDATE_TIME");
    const market_state = update.getValue("MARKET_STATE");

    if (bid && offer) { // Only log if we have valid price data
        log_data({
            type: "price_update",
            epic: epic,
            bid: parseFloat(bid),
            offer: parseFloat(offer),
            time: update_time,
            market_state: market_state
        });
    }
  },
  onSubscription: function() {
    log_info(`Subscribed to Price: ${epic}`);
  },
  onSubscriptionError: function(code, msg) {
    log_error(`[Price Subscription Error]: ${code} - ${msg}`);
  }
});

client.subscribe(priceSub);

// --- Subscription 2: Trade Updates (DISTINCT) ---
// Subscribing to TRADE:{account_id} to receive CONFIRMS and OPU (Open Position Updates)
const tradeSub = new ls.Subscription("DISTINCT", [`TRADE:${account_id}`], ["CONFIRMS", "OPU"]);
tradeSub.setRequestedSnapshot("yes");

tradeSub.addListener({
  onItemUpdate: function(update) {
    const confirms = update.getValue("CONFIRMS");
    const opu = update.getValue("OPU");
    
    // We log anything significant.
    // 'CONFIRMS' usually contains JSON string about recent trade success/fail.
    // 'OPU' contains JSON string about position status changes.
    
    if (confirms) {
        log_data({
            type: "trade_update",
            subtype: "confirms",
            payload: confirms
        });
    }
    if (opu) {
         log_data({
            type: "trade_update",
            subtype: "opu",
            payload: opu
        });
    }
  },
  onSubscription: function() {
     log_info(`Subscribed to Trades: TRADE:${account_id}`);
  },
  onSubscriptionError: function(code, msg) {
    log_error(`[Trade Subscription Error]: ${code} - ${msg}`);
  }
});

client.subscribe(tradeSub);

// Keep process alive indefinitely

