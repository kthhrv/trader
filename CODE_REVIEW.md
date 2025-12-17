# Code Review Findings - December 17, 2025

This document outlines critical issues and improvement areas identified in the `trader` codebase.

## 1. Streaming Service Restarts (Efficiency vs. Isolation)
- **File:** `src/stream_manager.py` (Lines 100-111)
- **Issue:** The `StreamManager` terminates and restarts the Node.js subprocess for new subscriptions.
- **Investigation Update (2025-12-17):** While inefficient for a single strategy watching multiple epics, this behavior provides **Isolation Safety** for concurrent sessions (e.g., London and NY). Each session gets a dedicated Node.js process, preventing a crash in one from affecting others.
- **Verification:** Verified via `tests/test_concurrency.py`, which confirms overlapping trades operate independently without data loss.
- **Status:** **ACKNOWLEDGED** (Isolation prioritized over efficiency for now).

## 2. Rate Limit Risk: Redundant REST Polling
- **File:** `src/trade_monitor_db.py` (Lines 137-172)
- **Issue:** The `monitor_trade` loop polls the IG REST API (`fetch_open_position_by_deal_id`) every 5 seconds to calculate trailing stops.
- **Impact:** High consumption of API rate limits.
- **Recommendation:** Modify `TradeMonitorDB` to listen for price updates from the `StreamManager` and update stop-loss levels reactively based on streamed data.
- **Status:** **OPEN**

## 3. Maintainability: Manual API Request Construction
- **File:** `src/ig_client.py` (Lines 188-209)
- **Issue:** `place_spread_bet_order` manually constructs HTTP headers and URLs for the IG API instead of utilizing the `trading_ig` library's abstraction layer.
- **Impact:** 
    - Brittle code that is harder to maintain.
    - Bypasses any safety or convenience features provided by the library.
- **Recommendation:** Refactor to use `self.service.create_open_position()` or appropriate library methods.
- **Status:** **RESOLVED** (Refactored to use `create_open_position` on 2025-12-17).

## 4. Concurrency: Implicit State Sharing
- **File:** `src/strategy_engine.py`
- **Issue:** Shared state (e.g., `current_bid`, `current_offer`) is updated in background threads and read in the main strategy loop without explicit locks or thread-safe primitives.
- **Impact:** Potential for race conditions, especially if logic becomes more complex.
- **Recommendation:** Implement `threading.Lock` for shared state access or use thread-safe `Queue` objects for data transfer.

---
**Status:** Open
**Date Identified:** 2025-12-17
