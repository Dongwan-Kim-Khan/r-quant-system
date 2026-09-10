# Handoff Report: Domain 4 Broker & SSOT Data Synchronization Audit

**Agent**: Explorer 4 (Data Synchronization & Concurrency Auditor)  
**Working Directory**: `d:\코딩\R\.agents\explorer_sync`  
**Handoff Type**: Hard (Task Complete)  
**Date**: 2026-08-25  

---

## 1. Observation

Direct forensic observations across the codebase:

1. **Missing DDL Schema & Broken API Facade**:
   - `al_sangmoo/domain/risk/portfolio_guardian.py:241-251`: Executes `INSERT INTO trade_history (holding_id, ticker, buy_date, sell_date, buy_price, sell_price, quantity, pnl_pct, pnl_amount, reason, created_at) VALUES (...)`.
   - `al_sangmoo/infrastructure/persistence.py:43-132`: `init_database()` creates `my_portfolio`, `recommendation_matrix`, `trades`, and `macro_history`. `trade_history` table is completely missing.
   - `al_sangmoo/interfaces/api/routers/portfolio.py:284`: `get_portfolio_history()` executes `return db_manager.get_trade_history_records()`.
   - `db_manager.py:1-25` and `al_sangmoo/infrastructure/persistence.py`: Neither defines or exports `get_trade_history_records()`.

2. **Sold Holding Overwrite Race Condition in Price Sync**:
   - `al_sangmoo/infrastructure/persistence.py:335-338`: `sync_portfolio_prices()` executes `SELECT * FROM my_portfolio WHERE status = 'HOLDING'`.
   - `al_sangmoo/infrastructure/persistence.py:348-394`: Synchronous network quote fetches (KIS REST API / Yahoo Finance ThreadPoolExecutor) take 1-5 seconds.
   - `al_sangmoo/infrastructure/persistence.py:422-426`:
     ```sql
     UPDATE my_portfolio
     SET current_price = ?, current_value = ?, pnl_pct = ?, pnl_amount = ?, exit_advice = ?
     WHERE id = ?
     ```
     Lacks `AND status = 'HOLDING'`. If a holding is sold during the network fetch, this query overwrites the sold status advice with holding advice.

3. **CQRS Violation & Event-Loop Blocking in `/api/dashboard`**:
   - `al_sangmoo/interfaces/api/routers/dashboard.py:44`: In `async def get_dashboard_data()`, calls `live_portfolio = db_manager.sync_portfolio_prices()`, executing synchronous HTTP requests and SQLite writes directly on the asyncio event loop.
   - `frontend/js/websocket.js:203-215`:
     ```javascript
     _stopHttpPolling() {
         if (this.pollingInterval) {
             clearInterval(this.pollingInterval);
             this.pollingInterval = null;
         }
         // Background safety fallback
         this.pollingInterval = setInterval(async () => {
             try {
                 const fresh = await ApiClient.getDashboardData();
                 if (fresh) UI.renderDashboard(fresh);
             } catch (e) {}
         }, 15000);
     }
     ```
     Sets a permanent 15-second HTTP polling loop even when WebSocket is connected.

4. **Reconciliation Ghost Positions & Missing Liquidation Metrics**:
   - `al_sangmoo/domain/reconciliation.py:214-229`:
     ```python
     if auto_calibrate and len(broker_holdings) > 0:
         cursor.execute("""
         UPDATE my_portfolio
         SET status = 'SOLD', sell_date = ?, exit_advice = ?
         WHERE id = ?
         """, (datetime.now().strftime("%Y-%m-%d"), f"실계좌 청산 감지 보정 ({now_str})", l_item["id"]))
     ```
     If broker holdings count is 0 (all positions closed externally), the condition `len(broker_holdings) > 0` prevents local position closure.
     Furthermore, the UPDATE query sets `status = 'SOLD'` without recording `sell_price`, `current_price`, `current_value`, `pnl_pct`, or `pnl_amount`.

5. **WebSocket Desync & Ambiguous Ticker Matching on Broker Order Route**:
   - `al_sangmoo/interfaces/api/routers/broker.py:80-100`: `execute_broker_order` modifies SQLite on BUY and SELL, but does NOT broadcast `hub.broadcast("portfolio_update", p_data)`.
   - `al_sangmoo/interfaces/api/routers/broker.py:88-98`: On SELL, searches by ticker string and liquidates the first matching holding without lot ID or quantity validation.

6. **Unsynchronized Order Placement Race Condition**:
   - `al_sangmoo/interfaces/api/routers/portfolio.py:101-171` & `al_sangmoo/interfaces/api/routers/broker.py:41-100`: No lock or mutex wraps pre-trade balance validation and order submission, allowing concurrent requests to double-spend cash balances and exceed the 3-slot limit.

7. **Stale `CHART_CACHE` & Thread-Unsafe Eviction**:
   - `al_sangmoo/interfaces/api/routers/charts.py:12-70`: Global `CHART_CACHE` has no TTL expiration, is not invalidated when background scans complete in `scanner.py`, and evicts keys via `del CHART_CACHE[first_key]` without locks across worker threads.

8. **Unmanaged SQLite Connection in Daily Bot**:
   - `al_sangmoo_daily_bot.py:359`: Calls `conn = db_manager.get_db()` without a `with` context manager. Any error during batch iteration prevents `conn.close()`.

---

## 2. Logic Chain

1. **From Observation 1**: When `PortfolioGuardian` detects a partial take-profit (+15%), it executes `INSERT INTO trade_history`. Because `trade_history` was never created in `init_database()`, SQLite throws `sqlite3.OperationalError: no such table: trade_history`. Because of the `ManagedConnection` context manager, this rolls back the transaction, leaving the position unreduced. On every subsequent check cycle (every 10s), the guardian re-triggers and fails again. Concurrently, calling `GET /api/portfolio/history` throws `AttributeError: module 'db_manager' has no attribute 'get_trade_history_records'`, breaking the trade history view.
2. **From Observation 2**: When `sync_portfolio_prices()` executes, it reads `status = 'HOLDING'` rows and begins fetching external prices over network (1-5s). If a position is sold in the interim (e.g. by manual sell or stop-loss), the row status is set to `'SOLD'`. When the network fetch completes, `executemany` runs `UPDATE my_portfolio ... WHERE id = ?`. Because it lacks `AND status = 'HOLDING'`, it overwrites the sold row's `exit_advice` and PnL fields, corrupting historical records.
3. **From Observation 3**: `GET /api/dashboard` is invoked on page load and every 15s by `websocket.js` due to an uncleared `setInterval`. Because `dashboard.py` calls `sync_portfolio_prices()` synchronously on the async event loop, every request freezes all async tasks (including WebSocket broadcasts and heartbeats) during external network I/O.
4. **From Observation 4**: In `reconciliation.py`, the defensive check `len(broker_holdings) > 0` was intended to prevent accidental wipes during network drops, but it inadvertently prevents clearing local holdings when an account is legitimately 100% in cash. When auto-calibrating, omitting `sell_price` leaves null/stale metrics in `my_portfolio`.
5. **From Observation 5**: Because `broker.py` omits `hub.broadcast("portfolio_update", ...)`, clients placing trades via broker routes experience UI state desync until a manual reload or poll occurs.

---

## 3. Caveats

- **No Source Code Modifications**: This investigation operated under strict read-only constraints. None of the identified issues have been patched in the active codebase.
- **External Network Conditions**: Network latency during `yf.download` or KIS REST calls varies between 200ms and 5000ms, which dictates the severity of the event loop stall and the window of vulnerability for the price sync overwrite race condition.
- **KIS Live Credentials**: KIS API credentials in `.env` operate in simulated VPS or live production mode; both code paths share the identical SQLite persistence and WebSocket broadcast layers.

---

## 4. Conclusion

Domain 4 exhibits significant synchronization and concurrency vulnerabilities that threaten data integrity, API uptime, and real-time frontend coherence:
1. **2 Critical Defects**: Missing `trade_history` table (causing partial take-profit transaction rollback crashes) and stale price sync overwrites on sold holdings.
2. **4 High-Severity Defects**: CQRS violation in `/api/dashboard` causing event-loop stalls, rogue 15-second HTTP polling proliferation, reconciliation ghost holdings on zero balance, and missing WebSocket broadcasts on broker routes.
3. **2 Medium-Severity Defects**: Perpetual stale chart cache with thread-unsafe eviction, and unmanaged SQLite handles in daily bot scripts.

All flaws have precise file paths, line citations, and concrete remediation patterns documented in `d:\코딩\R\.agents\explorer_sync\analysis.md`.

---

## 5. Verification Method

To independently verify these findings:

1. **Verify Missing `trade_history` Table & History Endpoint**:
   ```bash
   python -c "import db_manager; db_manager.init_database(); conn = db_manager.get_connection(); print([r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()])"
   # Output confirms 'trade_history' is missing from tables list.
   ```
   ```bash
   python -c "import db_manager; print(hasattr(db_manager, 'get_trade_history_records'))"
   # Output: False (AttributeError confirmed)
   ```

2. **Verify CQRS Violation in Dashboard Endpoint**:
   Inspect `al_sangmoo/interfaces/api/routers/dashboard.py:44`. Notice synchronous call to `db_manager.sync_portfolio_prices()`.

3. **Verify Stale Price Overwrite Query**:
   Inspect `al_sangmoo/infrastructure/persistence.py:423-426`. Notice `WHERE id = ?` lacks `AND status = 'HOLDING'`.

4. **Verify Rogue Polling in Frontend**:
   Inspect `frontend/js/websocket.js:203-215`. Notice `_stopHttpPolling()` creates a permanent 15s interval.
