#!/bin/bash

# This script provides the curl equivalent for placing a market order with IG.
# YOU MUST REPLACE THE PLACEHOLDER VALUES WITH YOUR ACTUAL IG SESSION TOKENS AND API KEY.
# For debugging purposes, you can temporarily modify src/ig_client.py to log session headers
# after authentication to retrieve CST and X-SECURITY-TOKEN.
# Remember to remove logging sensitive information in production.

# --- Configuration ---

# Set your environment to DEMO or LIVE based on your config.py
# For DEMO (IS_LIVE = False):
BASE_URL="https://demo-api.ig.com/gateway/deal"
# For LIVE (IS_LIVE = True):
# BASE_URL="https://api.ig.com/gateway/deal"

# IMPORTANT: Replace these with your actual tokens and API key from an active session
CST_TOKEN="5794d823a4003fc49ebee708da2b65124d66a18d3418d7e1c56899ddbcf405CC01113"
X_SECURITY_TOKEN="6b0a6cb36f612714fae53f3fafa66108b2b11563a815ac3f6907fa61086065CD01112"
API_KEY="26610cc38673ae62ff7bdbe77bd0183fa2eccfa5"

# --- Trading Parameters (replace with your plan's values) ---
EPIC="IX.D.FTSE.DAILY.IP"
DIRECTION="BUY" # Or "SELL" (e.g., from Gemini's TradingSignal.action)
SIZE=0.5 # Based on API response: Min Deal Size for FTSE DFB
STOP_LEVEL=9500.0 # Example stop loss price (from TradingSignal.stop_loss)
LIMIT_LEVEL=9600.0 # Example take profit price (from TradingSignal.take_profit)

# --- Check for jq ---
if ! command -v jq &> /dev/null
then
    echo "jq is not installed. Please install it to parse JSON responses: sudo apt-get install jq"
    exit 1
fi

# --- Construct JSON Payload ---
PAYLOAD=$(cat <<EOF
{
    "epic": "${EPIC}",
    "direction": "${DIRECTION}",
    "size": ${SIZE},
    "expiry": "DFB",
    "orderType": "MARKET",
    "currencyCode": "GBP",
    "forceOpen": true,
    "guaranteedStop": false,
    "stopLevel": ${STOP_LEVEL},
    "limitLevel": ${LIMIT_LEVEL}
}
EOF
)

# --- Execute Curl Command to Place Order ---
echo "Sending order for ${EPIC}, Direction: ${DIRECTION}, Size: ${SIZE}..."
echo "Payload: ${PAYLOAD}"

ORDER_RESPONSE=$(curl -s -X POST "${BASE_URL}/positions/otc" \
     -H "Content-Type: application/json" \
     -H "Accept: application/json" \
     -H "X-IG-API-KEY: ${API_KEY}" \
     -H "CST: ${CST_TOKEN}" \
     -H "X-SECURITY-TOKEN: ${X_SECURITY_TOKEN}" \
     -H "Version: 2" \
     -d "${PAYLOAD}")

echo "\nOrder Placement Response:"
echo "${ORDER_RESPONSE}" | jq .

DEAL_REFERENCE=$(echo "${ORDER_RESPONSE}" | jq -r '.dealReference')

if [ "${DEAL_REFERENCE}" == "null" ] || [ -z "${DEAL_REFERENCE}" ]; then
    echo "Failed to get dealReference from order response. Exiting."
    exit 1
fi

echo "\nDeal Reference: ${DEAL_REFERENCE}"

# --- Fetch Deal Confirmation ---
echo "\nFetching deal confirmation for ${DEAL_REFERENCE}..."

CONFIRM_RESPONSE=$(curl -s -X GET "${BASE_URL}/confirms/${DEAL_REFERENCE}" \
     -H "Accept: application/json" \
     -H "X-IG-API-KEY: ${API_KEY}" \
     -H "CST: ${CST_TOKEN}" \
     -H "X-SECURITY-TOKEN: ${X_SECURITY_TOKEN}" \
     -H "Version: 2")

echo "\nDeal Confirmation Response:"
echo "${CONFIRM_RESPONSE}" | jq .

DEAL_STATUS=$(echo "${CONFIRM_RESPONSE}" | jq -r '.dealStatus')
REASON=$(echo "${CONFIRM_RESPONSE}" | jq -r '.reason')

echo "\nFinal Deal Status: ${DEAL_STATUS}"
if [ "${REASON}" != "null" ]; then
    echo "Reason: ${REASON}"
fi
