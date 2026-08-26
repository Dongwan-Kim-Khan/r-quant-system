# Domain 4 Audit Report: Broker & SSOT Data Synchronization Audit
**Target System**: Al-Sangmoo Institutional Quant Platform (d:\코딩\R)  
**Auditor**: Explorer 4 (Data Synchronization & Concurrency Auditor)  
**Audit Date**: 2026-08-25  
**Integrity Mode**: Development / Strict Read-Only Audit  

---

## 1. Executive Summary

A comprehensive, read-only architectural and concurrency audit of **Domain 4 (Broker & SSOT Data Synchronization)** was conducted across persistence layers, API routes, KIS broker integrations, WebSocket broadcast state, background daemons, and frontend polling mechanisms.

While the platform incorporates several high-level synchronization concepts (such as `ManagedConnection` with WAL mode pragmas, `asyncio.Lock` for background scans, and `atomic_save_json` for file I/O), our forensic inspection revealed **critical state divergence, unhandled database exceptions, transaction rollback risks, race conditions during order execution, and rogue background polling loops**.

### Key Findings Summary:
1. **Critical Schema & API Incompatibilities (SYNC-01)**: The Portfolio Guardian attempts to insert partial profit-taking records into a non-existent `trade_history` table (`sqlite3.OperationalError: no such table: trade_history`), and `GET /api/portfolio/history` invokes a non-existent facade method `db_manager.get_trade_history_records()`, resulting in unhandled `AttributeError` (HTTP 500).
2. **Sold Holding Overwrite Race Condition (SYNC-02)**: The background price synchronization worker (`sync_portfolio_prices`) executes long synchronous I/O fetches and blindly updates `my_portfolio` with `WHERE id = ?` without verifying `AND status = 'HOLDING'`. Positions sold during the network fetch have their `exit_advice` and PnL metrics overwritten with holding state data.
3. **CQRS Violation & Event-Loop Starvation (SYNC-03)**: `GET /api/dashboard` synchronously executes `db_manager.sync_portfolio_prices()` during HTTP request processing. Coupled with a rogue 15-second frontend `setInterval` fallback in `websocket.js` that never terminates even when WebSockets are connected, the server is bombarded with blocking price sync and SQLite write transactions.
4. **Reconciliation Ghost Positions & Missing Liquidation Metrics (SYNC-04)**: `check_sync()` in `reconciliation.py` contains a guard that prevents closing local SQLite positions if the broker returns 0 holdings. When an account is completely liquidated on the broker, local positions remain permanently open in SQLite. Furthermore, auto-calibrating closed positions fails to record `sell_price`, `pnl_pct`, and `pnl_amount`.
5. **WebSocket Desynchronization on Broker Routes (SYNC-05)**: `POST /api/broker/order` modifies SQLite state upon order execution but completely omits the WebSocket broadcast (`hub.broadcast("portfolio_update", p_data)`), causing frontend state drift. It also implements an ambiguous ticker search that blindly liquidates the first matching holding on sell orders.
6. **Concurrent Order Double-Spend & Slot Breach Race (SYNC-06)**: Absence of concurrency locks on trade execution endpoints allows overlapping buy requests to pass pre-trade guardrails simultaneously with the same cash balance, leading to cash double-spending and slot limit violations.
7. **Stale Chart Cache & Thread-Unsafe Eviction (SYNC-07)**: In-memory `CHART_CACHE` lacks TTL expiration, is never invalidated when market scans complete, and performs unsynchronized dictionary key eviction across async worker threads.

---

## 2. Severity Matrix

| Issue ID | Title | Severity | Impact Area | Primary Files |
|---|---|---|---|---|
| **SYNC-01** | Missing `trade_history` DDL Schema & Broken History API Facade | 🔴 **CRITICAL** | Database Schema / API Availability | `portfolio_guardian.py`, `persistence.py`, `portfolio.py`, `db_manager.py` |
| **SYNC-02** | Stale Price Sync Overwrite Race Condition on Sold Positions | 🔴 **CRITICAL** | Data Integrity / Historical Audit | `persistence.py`, `dashboard.py` |
| **SYNC-03** | CQRS Violation & Event-Loop Blocking via Sync Price Refresh in `/api/dashboard` | 🟠 **HIGH** | Latency / Concurrency | `dashboard.py`, `websocket.js`, `autopilot_trader.py` |
| **SYNC-04** | Reconciliation Ghost Positions on Zero Broker Balance & Incomplete Sell Record | 🟠 **HIGH** | SSOT Consistency / Account Balances | `reconciliation.py`, `broker.py` |
| **SYNC-05** | WebSocket Desync on Broker Routes & Ambiguous Ticker Matching on Sell | 🟠 **HIGH** | UI Synchronization / Broker Gateway | `broker.py` |
| **SYNC-06** | Unsynchronized Order Placement Race Condition (Double-Spend / Slot Limit Breach) | 🟠 **HIGH** | Risk Management / Broker Execution | `portfolio.py`, `broker.py`, `autopilot_trader.py` |
| **SYNC-07** | Perpetual `CHART_CACHE` with Missing TTL, Zero Scan Invalidation & Unsafe Eviction | 🟡 **MEDIUM** | Cache Invalidation / Thread Safety | `charts.py`, `scanner.py` |
| **SYNC-08** | Unmanaged SQLite Handle & Un-Atomic CSV Writes in Daily Scanner Bot | 🟡 **MEDIUM** | Connection Leak / File I/O Safety | `al_sangmoo_daily_bot.py` |
| **SYNC-09** | Account Capital Sizing Inconsistency ($100k vs $7.5k Sizing Divergence) | 🔵 **LOW** | Architectural Consistency | `paper_broker.py`, `persistence.py`, `kis_broker.py` |

---

## 3. Detailed Audit Findings

---

### SYNC-01: Missing `trade_history` DDL Schema & Broken `get_trade_history_records` API Facade

- **Severity**: 🔴 **CRITICAL**
- **Affected Files & Lines**:
  - `al_sangmoo/domain/risk/portfolio_guardian.py:241-251`
  - `al_sangmoo/infrastructure/persistence.py:43-132`
  - `al_sangmoo/interfaces/api/routers/portfolio.py:281-285`
  - `db_manager.py:5-24`

#### Problematic Code Snippet:

```python
# al_sangmoo/domain/risk/portfolio_guardian.py:241-251
# Partial close: reduce SQLite holding quantity
with db_manager.get_connection() as conn:
    cursor = conn.cursor()
    ...
    cursor.execute("""
    UPDATE my_portfolio SET quantity = ?, ... WHERE id = ?
    """, (...))
    
    # Insert partial trade record to history
    cursor.execute("""
    INSERT INTO trade_history (
        holding_id, ticker, buy_date, sell_date, buy_price,
        sell_price, quantity, pnl_pct, pnl_amount, reason, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        holding_id, ticker, h.get("buy_date", now_str[:10]), now_str[:10],
        buy_price, cur_price, sell_qty, pnl_pct, (cur_price - buy_price) * sell_qty,
        f"AUTO_PARTIAL_TP_50 ({pnl_pct:+.2f}%)", now_str
    ))
    conn.commit()
```

```python
# al_sangmoo/interfaces/api/routers/portfolio.py:281-285
@router.get("/history")
def get_portfolio_history():
    """Returns all past closed trades and realized returns."""
    return db_manager.get_trade_history_records()
```

#### Detailed Flaw Analysis:
1. When `PortfolioGuardian` triggers a 50% partial take-profit (`AUTO_PARTIAL_TP_50`), it executes an `INSERT INTO trade_history` within the same database transaction that updates `my_portfolio`.
2. However, inspection of `init_database()` in `persistence.py` reveals that only `my_portfolio`, `recommendation_matrix`, `trades`, and `macro_history` tables are created. The `trade_history` table **does not exist in the DDL schema**.
3. Consequently, whenever the guardian attempts to take partial profits, SQLite raises `sqlite3.OperationalError: no such table: trade_history`. Because of the `ManagedConnection` context manager, this error triggers an immediate transaction rollback, failing both the trade record logging and the quantity reduction in `my_portfolio`. The position remains unreduced, causing the guardian to continually re-trigger and repeatedly fail on every cycle.
4. Furthermore, `GET /api/portfolio/history` invokes `db_manager.get_trade_history_records()`. Neither `db_manager.py` nor `persistence.py` implements this function, causing an unhandled `AttributeError` returning HTTP 500.

#### Concrete Remediation:
1. Add `trade_history` table creation to `init_database()` in `persistence.py`:
```sql
CREATE TABLE IF NOT EXISTS trade_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    holding_id INTEGER,
    ticker TEXT NOT NULL,
    buy_date TEXT NOT NULL,
    sell_date TEXT NOT NULL,
    buy_price REAL NOT NULL,
    sell_price REAL NOT NULL,
    quantity REAL NOT NULL,
    pnl_pct REAL DEFAULT 0,
    pnl_amount REAL DEFAULT 0,
    reason TEXT,
    created_at TEXT
);
```
2. Implement `get_trade_history_records()` in `persistence.py` and expose it in `db_manager.py`:
```python
def get_trade_history_records() -> list:
    init_database()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trade_history ORDER BY sell_date DESC, id DESC")
        return [dict(r) for r in cursor.fetchall()]
```

---

### SYNC-02: Stale Price Sync Overwrite Race Condition on Sold Positions

- **Severity**: 🔴 **CRITICAL**
- **Affected Files & Lines**:
  - `al_sangmoo/infrastructure/persistence.py:328-429` (specifically lines 422-426)
  - `al_sangmoo/interfaces/api/routers/dashboard.py:42-45`

#### Problematic Code Snippet:

```python
# al_sangmoo/infrastructure/persistence.py:335-427
def sync_portfolio_prices() -> dict:
    init_database()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM my_portfolio WHERE status = 'HOLDING' ORDER BY buy_date DESC, id DESC")
        holdings = [dict(r) for r in cursor.fetchall()]
        
    ... # [1-5 seconds of synchronous network I/O: KIS quotes, JSON file parsing, yf.download threadpool]
    
    update_rows = []
    for h in holdings:
        ...
        update_rows.append((cur_price, cur_val, pnl_pct, pnl_amt, advice, h['id']))
        
    if update_rows:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany("""
            UPDATE my_portfolio
            SET current_price = ?, current_value = ?, pnl_pct = ?, pnl_amount = ?, exit_advice = ?
            WHERE id = ?
            """, update_rows)
            conn.commit()
```

#### Detailed Flaw Analysis:
1. `sync_portfolio_prices()` opens a connection, selects all rows with `status = 'HOLDING'`, and releases the connection.
2. It then performs external network requests (KIS REST API / Yahoo Finance) across thread pools. This process typically takes between 500ms and 5000ms.
3. If a user or background daemon sells a position during this window (`record_portfolio_sell` via `POST /api/portfolio/sell/{id}` or Guardian stop-loss), the database record is updated to `status = 'SOLD'` with a specific realization message (e.g. `"청산 완료 (+14.50%) - MANUAL_SELL"`).
4. When `sync_portfolio_prices()` finishes fetching prices, it executes `executemany` with `WHERE id = ?`. Because the WHERE clause **lacks `AND status = 'HOLDING'`**, it unconditionally updates the sold row, overwriting `exit_advice` back to `"보유 지속 (손절선 $...)"` or `"전량 익절 매도 권고"`, and recalculating active holding values on a liquidated position.
5. This corrupts historical trade records and causes discrepancies in realized trade logs.

#### Concrete Remediation:
Enforce status guarding in the batch UPDATE query:
```python
cursor.executemany("""
UPDATE my_portfolio
SET current_price = ?, current_value = ?, pnl_pct = ?, pnl_amount = ?, exit_advice = ?
WHERE id = ? AND status = 'HOLDING'
""", update_rows)
```

---

### SYNC-03: CQRS Violation & Event-Loop Blocking via Sync Price Refresh in `/api/dashboard`

- **Severity**: 🟠 **HIGH**
- **Affected Files & Lines**:
  - `al_sangmoo/interfaces/api/routers/dashboard.py:42-45`
  - `frontend/js/websocket.js:203-215`
  - `al_sangmoo/domain/risk/autopilot_trader.py:269`

#### Problematic Code Snippet:

```python
# al_sangmoo/interfaces/api/routers/dashboard.py:41-46
# Inject real-time live portfolio from SQLite SSOT (with live price sync)
try:
    import db_manager
    live_portfolio = db_manager.sync_portfolio_prices() # Synchronous blocking network & DB write!
    feed_out["portfolio"] = live_portfolio
```

```javascript
// frontend/js/websocket.js:203-215
_stopHttpPolling() {
    if (this.pollingInterval) {
        clearInterval(this.pollingInterval);
        this.pollingInterval = null;
    }
    // Background safety fallback: NEVER stops polling!
    this.pollingInterval = setInterval(async () => {
        try {
            const fresh = await ApiClient.getDashboardData();
            if (fresh) UI.renderDashboard(fresh);
        } catch (e) {}
    }, 15000);
},
```

#### Detailed Flaw Analysis:
1. Phase 5.2 Requirement R4 (CONC-04) explicitly required CQRS read-write separation: pure read queries (`get_live_portfolio`) must return database records without executing blocking network calls (`yf.download`) or issuing SQL `UPDATE` statements.
2. In `dashboard.py:44`, `get_dashboard_data()` directly invokes `sync_portfolio_prices()`. Because `get_dashboard_data` is an `async` route executed on FastAPI's main event loop, calling a synchronous function that performs thread-pool I/O and synchronous database updates blocks the entire Python event loop. All incoming WebSocket pings, API requests, and router dispatches freeze while `/api/dashboard` is running.
3. In `frontend/js/websocket.js:203-215`, `_stopHttpPolling()` claims to stop HTTP polling when WebSocket connects, but immediately re-creates a 15-second polling timer (`setInterval(..., 15000)`).
4. Every connected browser tab bombards the server every 15 seconds with `/api/dashboard` calls, repeatedly locking SQLite and stalling the event loop.

#### Concrete Remediation:
1. In `dashboard.py`, replace `sync_portfolio_prices()` with pure, non-blocking `get_live_portfolio()`:
```python
# Pure CQRS read query without network or write side-effects
live_portfolio = db_manager.get_live_portfolio()
feed_out["portfolio"] = live_portfolio
```
2. In `websocket.js`, ensure `_stopHttpPolling()` genuinely clears polling intervals:
```javascript
_stopHttpPolling() {
    if (this.pollingInterval) {
        clearInterval(this.pollingInterval);
        this.pollingInterval = null;
    }
}
```
3. Offload price synchronization strictly to background daemons (Guardian / Dedicated price sync task) and broadcast updates via `hub.broadcast("portfolio_update", ...)`.

---

### SYNC-04: Reconciliation Ghost Positions on Zero Broker Balance & Incomplete Sell Record

- **Severity**: 🟠 **HIGH**
- **Affected Files & Lines**:
  - `al_sangmoo/domain/reconciliation.py:202-230` (specifically lines 212-229)
  - `al_sangmoo/interfaces/api/routers/broker.py:33-39`

#### Problematic Code Snippet:

```python
# al_sangmoo/domain/reconciliation.py:212-229
# Defensive safety: Only auto-close if broker returned at least 1 other valid holding,
# preventing wiping out local DB during network/broker query glitches.
if auto_calibrate and len(broker_holdings) > 0:
    cursor.execute("""
    UPDATE my_portfolio
    SET status = 'SOLD', sell_date = ?, exit_advice = ?
    WHERE id = ?
    """, (
        datetime.now().strftime("%Y-%m-%d"),
        f"실계좌 청산 감지 보정 ({now_str})", l_item["id"]
    ))
    calibrations.append({
        "ticker": ticker,
        "action": "CLOSE_LOCAL_POSITION",
        "id": l_item["id"]
    })
else:
    logger.warning(f"[Reconciliation Guard] Preserved local position for {ticker} (Broker returned 0 total holdings)")
```

#### Detailed Flaw Analysis:
1. When a user liquidates all active positions on their broker MTS/HTS, `broker_holdings` becomes empty (`len(broker_holdings) == 0`).
2. If `check_sync(auto_calibrate=True)` runs (via `/api/broker/reconcile` or `/api/portfolio/sync`), the guard `len(broker_holdings) > 0` evaluates to `False`. The engine refuses to calibrate, logging a warning and leaving the closed position as `HOLDING` in local SQLite indefinitely.
3. When closing positions (lines 216-219), the SQL query sets `status = 'SOLD'`, but **does not update `sell_price`, `current_price`, `current_value`, `pnl_pct`, or `pnl_amount`**. `sell_price` remains `NULL` and `pnl_amount` reflects stale evaluations rather than the actual liquidation return, corrupting historical performance statistics and portfolio equity summaries.

#### Concrete Remediation:
1. Distinguish between broker API errors vs legitimately empty accounts (verify `broker_balance.get("status") == "success"` and `cash_available_usd > 0`).
2. When closing local positions, calculate and persist complete liquidation metrics:
```python
if auto_calibrate:
    cur_p = float(l_item.get("current_price", l_item.get("buy_price", 0.0)))
    buy_p = float(l_item.get("buy_price", 0.0))
    qty = float(l_item.get("quantity", 0.0))
    pnl_pct = ((cur_p - buy_p) / buy_p * 100) if buy_p > 0 else 0.0
    pnl_amt = (cur_p - buy_p) * qty
    
    cursor.execute("""
    UPDATE my_portfolio
    SET status = 'SOLD', sell_date = ?, sell_price = ?, current_price = ?,
        current_value = ?, pnl_pct = ?, pnl_amount = ?, exit_advice = ?
    WHERE id = ?
    """, (
        datetime.now().strftime("%Y-%m-%d"), cur_p, cur_p,
        cur_p * qty, pnl_pct, pnl_amt,
        f"실계좌 청산 감지 보정 ({now_str})", l_item["id"]
    ))
```

---

### SYNC-05: WebSocket Desync on Broker Routes & Ambiguous Ticker Matching on Sell

- **Severity**: 🟠 **HIGH**
- **Affected Files & Lines**:
  - `al_sangmoo/interfaces/api/routers/broker.py:41-100` (specifically lines 80-99)

#### Problematic Code Snippet:

```python
# al_sangmoo/interfaces/api/routers/broker.py:80-99
if result.get("status") in ("submitted", "filled"):
    if final_side == "BUY":
        add_portfolio_buy(
            ticker=order.ticker.upper(),
            buy_price=final_price if final_price > 0 else 100.0,
            quantity=float(final_qty),
            buy_date=order.buy_date
        )
    elif final_side == "SELL":
        # Find active holding ID in SQLite
        live_port = get_live_portfolio()
        for h in live_port.get("holdings", []):
            if h["ticker"].upper() == order.ticker.upper():
                record_portfolio_sell(
                    holding_id=h["id"],
                    sell_price=final_price if final_price > 0 else h["current_price"],
                    reason="BROKER_LIVE_SELL"
                )
                break

    # Missing WebSocket broadcast!
    return result
```

#### Detailed Flaw Analysis:
1. `POST /api/broker/order` modifies SQLite portfolio records upon trade execution, but **never broadcasts the updated portfolio state via WebSocket (`hub.broadcast`)**. In contrast, `POST /api/portfolio/buy` and `POST /api/portfolio/sell` broadcast `portfolio_update` immediately. Connected frontend clients remain unaware of broker-executed trades until the page is refreshed.
2. In `execute_broker_order` (lines 88-98), sell orders search by ticker string only and close the first encountered holding row (`break`). If the user holds multiple tranches of the same stock purchased at different prices/dates, the wrong lot is closed, and partial quantity sells are not supported.

#### Concrete Remediation:
1. Broadcast `portfolio_update` over WebSocket immediately following database mutation.
2. Accept optional `position_id` in `BrokerOrderRequest` to ensure precise lot targeting.

---

### SYNC-06: Unsynchronized Order Placement Race Condition (Double-Spend / Slot Limit Breach)

- **Severity**: 🟠 **HIGH**
- **Affected Files & Lines**:
  - `al_sangmoo/interfaces/api/routers/portfolio.py:101-171`
  - `al_sangmoo/interfaces/api/routers/broker.py:41-100`
  - `al_sangmoo/domain/risk/autopilot_trader.py:155-251`

#### Problematic Code Snippet:

```python
# al_sangmoo/interfaces/api/routers/portfolio.py:116-155
if default_kis_broker.is_configured():
    balance_info = default_kis_broker.get_overseas_balance()
    total_equity = float(balance_info.get("total_equity_usd", 100_000.0))
    active_holdings = balance_info.get("holdings", [])

    eval_res = validate_pre_trade_guardrail(...) # No lock held!
    if not eval_res["allowed"]:
        raise HTTPException(status_code=400, detail=...)

    broker_res = default_kis_broker.place_order(...) # Network delay (1-2s)

inserted_id = db_manager.add_portfolio_buy(...) # DB record added after broker order
```

#### Detailed Flaw Analysis:
1. When two order requests arrive concurrently (e.g. user rapid double-click on "BUY", or simultaneous Autopilot buy and manual buy):
   - Both requests query broker balance and active holdings in parallel.
   - Both requests see sufficient cash and empty slots (e.g. 1 active holding, 2 available slots).
   - Both requests pass pre-trade guardrails simultaneously.
   - Both submit buy orders to KIS and insert records into `my_portfolio`.
2. This creates an uncoordinated state where 4 holdings can be opened on a 3-slot maximum system, over-allocating cash reserves and violating risk limits.

#### Concrete Remediation:
Implement an `asyncio.Lock` or transaction mutex around the entire validation-to-execution pipeline:
```python
_order_execution_lock = asyncio.Lock()

@router.post("/buy")
async def buy_stock(order: BuyOrder):
    async with _order_execution_lock:
        # 1. Check current DB & Broker slot count atomically
        # 2. Validate pre-trade risk
        # 3. Place order & record to SQLite
        # 4. Broadcast portfolio update
```

---

### SYNC-07: Perpetual `CHART_CACHE` with Missing TTL, Zero Scan Invalidation & Unsafe Eviction

- **Severity**: 🟡 **MEDIUM**
- **Affected Files & Lines**:
  - `al_sangmoo/interfaces/api/routers/charts.py:12-70`
  - `al_sangmoo/interfaces/api/routers/scanner.py:30-68`

#### Problematic Code Snippet:

```python
# al_sangmoo/interfaces/api/routers/charts.py:12-69
CHART_CACHE = {}

@router.get("/api/chart/{ticker}")
async def get_chart_data(ticker: str):
    global CHART_CACHE
    ...
    if ticker_resolved in CHART_CACHE:
        return CHART_CACHE[ticker_resolved] # Stale forever!

    # LRU eviction across concurrent async workers without locking
    if len(CHART_CACHE) > 150:
        first_key = next(iter(CHART_CACHE))
        del CHART_CACHE[first_key]

    CHART_CACHE[ticker_resolved] = data
    return data
```

#### Detailed Flaw Analysis:
1. `CHART_CACHE` stores computed chart JSON payloads in memory with no TTL expiration. Once loaded, cached data is never refreshed unless the server process is restarted or the cache exceeds 150 entries.
2. When a background scan completes (`_run_background_scan_pipeline` in `scanner.py`), fresh chart data is written to disk in `data/charts/{ticker}.json`. However, `CHART_CACHE` in `charts.py` is **never cleared or updated**. Clients fetching charts continue to receive outdated pre-scan data from memory.
3. The cache eviction code (`del CHART_CACHE[first_key]`) mutates a shared dictionary across Starlette async workers without synchronization locks, which can raise `RuntimeError: dictionary changed size during iteration`.

#### Concrete Remediation:
1. Replace raw `CHART_CACHE` with a thread-safe TTL cache (e.g. 5-minute TTL).
2. Clear `CHART_CACHE.clear()` when `_run_background_scan_pipeline` finishes.
3. In disk fallback, check `os.path.getmtime(chart_file)` against market open/close times to avoid serving stale files.

---

### SYNC-08: Unmanaged SQLite Handle & Un-Atomic CSV Writes in Daily Scanner Bot

- **Severity**: 🟡 **MEDIUM**
- **Affected Files & Lines**:
  - `al_sangmoo_daily_bot.py:354-390`

#### Problematic Code Snippet:

```python
# al_sangmoo_daily_bot.py:354-389
history_df.to_csv(HISTORY_CSV, index=False) # Unlocked raw write!

# SQLite sync
try:
    db_manager.init_db()
    conn = db_manager.get_db() # Missing "with get_db() as conn:"!
    cursor = conn.cursor()
    for idx, row in history_df.iterrows():
        ...
        cursor.execute(...)
    conn.commit()
    conn.close()
except Exception as e:
    print(f"[SQLite Sync Warning] {e}") # Leaks handle if loop throws
```

#### Detailed Flaw Analysis:
1. `al_sangmoo_daily_bot.py:359` calls `db_manager.get_db()` without using a `with` statement. If an exception occurs during the batch row iteration, `conn.close()` is never reached, leaking the database connection handle.
2. `history_df.to_csv(HISTORY_CSV, index=False)` performs an un-atomic write to `trade_history.csv`. If `generate_dashboard_feed.py` reads the file simultaneously, pandas encounters an incomplete file, raising `pd.errors.EmptyDataError` or `ParserError`.

#### Concrete Remediation:
1. Use `with db_manager.get_db() as conn:` context management.
2. Use atomic file replacement for CSV writes (write to `.tmp` file and `os.replace`).

---

### SYNC-09: Account Capital Sizing Inconsistency ($100k vs $7.5k Sizing Divergence)

- **Severity**: 🔵 **LOW**
- **Affected Files & Lines**:
  - `al_sangmoo/infrastructure/brokers/paper_broker.py:10-77`
  - `al_sangmoo/infrastructure/persistence.py:303-311`
  - `al_sangmoo/infrastructure/brokers/kis_broker.py:201-225`

#### Detailed Flaw Analysis:
- `persistence.py:get_live_portfolio()` bases financial cash and equity accounting on a $7,500 base account (10,000,000 KRW 3-slot quant framework).
- `paper_broker.py` initializes `initial_cash = 100000.0` ($100k account).
- `kis_broker.py` simulated fallback balance defaults to $100,000.0.
- When paper broker balance is queried via `/api/broker/balance` vs `/api/portfolio`, the available cash and equity figures diverge ($100k vs $7.5k), leading to confusion on the frontend dashboard.

#### Concrete Remediation:
Standardize default simulation account capital across all adapters using `BASE_PORTFOLIO_EQUITY_USD = 7500.0` defined in `al_sangmoo/core/constants.py`.

---

## 4. Domain 4 Synthesis Across Focus Areas

### 4.1 SQLite Lock Contention & Connection Leaks
- **WAL Mode & Pragmas**: `persistence.py:get_connection()` correctly configures `PRAGMA journal_mode = WAL;`, `PRAGMA busy_timeout = 30000;`, and `PRAGMA synchronous = NORMAL;`.
- **Handle Lifecycle**: `ManagedConnection` properly overrides `__exit__` to guarantee `self.close()` inside a `finally:` block.
- **Vulnerabilities**: Legacy script `al_sangmoo_daily_bot.py` bypassed the context manager by calling `conn = db_manager.get_db()` directly. Missing tables (`trade_history`) cause transactional failures during partial exits.

### 4.2 SSOT Data Consistency & Split-Brain Risks
- **Persistence Multiplicity**: The system maintains 4 distinct data stores:
  1. SQLite `my_portfolio` (active holdings ledger)
  2. SQLite `trades` (daily scan recommendation records)
  3. `trade_history.csv` (used by daily bot for win-rate calculations)
  4. `dashboard_data.json` (static snapshot feed)
- **Reconciliation Engine**: `check_sync()` provides a framework for aligning SQLite with KIS broker reality, but contains logic flaws when broker balances are empty and omits realization metrics when updating closed positions.
- **WebSocket Broadcast Inconsistencies**: `portfolio.py` broadcasts real-time updates, whereas `broker.py` does not broadcast after order execution.

### 4.3 Race Conditions in Order Execution & Price Sync
- **Sold Position Overwrite**: A high-impact race condition exists in `sync_portfolio_prices()` where positions sold during quote download have their `exit_advice` overwritten by an unguarded `WHERE id = ?` UPDATE statement.
- **Double-Spend Risk**: Concurrent buy requests execute without an async mutex, allowing multiple trades to pass balance checks concurrently.

### 4.4 Local Chart Caching & Invalidation Mechanics
- **In-Memory Cache**: `CHART_CACHE` in `charts.py` has no TTL, is never invalidated upon scan completion, and has non-thread-safe eviction.
- **Timeframe Limitations**: Chart caching currently supports Daily and Weekly candles; 60m and 15m intraday candles are not cached.

### 4.5 Database Transaction Atomicity
- **Multi-Table Updates**: Partial profit-taking updates `my_portfolio` and attempts to insert into `trade_history`. Because `trade_history` does not exist, the entire transaction is rolled back.
- **Broker-DB Disconnect**: If a KIS broker order succeeds but the subsequent SQLite insert fails, the trade exists on the exchange but is omitted locally until manual reconciliation.

---

## 5. Prioritized Remediation Roadmap

1. **Step 1 (Critical Schema & Facade Repair)**:
   - Add `CREATE TABLE IF NOT EXISTS trade_history (...)` to `persistence.py:init_database()`.
   - Implement `get_trade_history_records()` in `persistence.py` and export in `db_manager.py`.
2. **Step 2 (Critical Price Sync Guarding)**:
   - Add `AND status = 'HOLDING'` to `sync_portfolio_prices()` `UPDATE my_portfolio` query.
3. **Step 3 (CQRS Decoupling & Polling Cleanup)**:
   - Refactor `dashboard.py:get_dashboard_data()` to call pure read `get_live_portfolio()`.
   - Fix `frontend/js/websocket.js` so `_stopHttpPolling()` completely cancels `setInterval` when WebSocket is live.
4. **Step 4 (Reconciliation & Broker Route Hardening)**:
   - Update `reconciliation.py` to allow liquidating local positions when broker holdings are 0 and record full PnL metrics.
   - Add `await hub.broadcast("portfolio_update", p_data)` to `broker.py:execute_broker_order`.
5. **Step 5 (Order Concurrency & Mutex)**:
   - Wrap order execution pipelines in an `asyncio.Lock()` to prevent double-spending and slot limit breaches.
6. **Step 6 (Chart Cache TTL & Invalidation)**:
   - Add 5-minute TTL to `CHART_CACHE` and clear cache on background scan completion.
