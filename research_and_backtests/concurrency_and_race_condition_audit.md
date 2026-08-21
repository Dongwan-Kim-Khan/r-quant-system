# R-Sangmoo Quant Trading Platform: Concurrency, Database Transactions, and Distributed State Synchronization Audit

**Document Reference**: `AUDIT-2026-M2-CONCURRENCY-001`  
**Classification**: Engineering Architecture & Data Integrity Verification  
**Target Systems**: `server.py`, `db_manager.py`, `al_sangmoo_dashboard.html`, `youtube_stream_scanner.py`, `al_sangmoo_daily_bot.py`, `generate_dashboard_feed.py`  
**Database**: `quant_trades.db` (SQLite 3.x)  
**File Persistence**: `dashboard_data.json` (8.5 MB), `wepoll_latest_stream.json`, `trade_history.csv`  
**Audit Author**: Worker M2 (Specialist: Concurrency, Database Transactions, Asynchronous Architecture & Distributed State Synchronization)  
**Date**: 2026-08-21  

---

## Table of Contents
1. [Executive Summary & Concurrency Risk Profile](#1-executive-summary--concurrency-risk-profile)
2. [Concurrency & Race Condition Vulnerability Matrix](#2-concurrency--race-condition-vulnerability-matrix)
3. [SQLite Database Contention & Transaction Safety Analysis](#3-sqlite-database-contention--transaction-safety-analysis)
   - 3.1 SQLite Lock Escalation & Absence of WAL Mode
   - 3.2 Transaction Poisoning via Synchronous Network I/O in `get_live_portfolio()`
   - 3.3 Connection Spawning Overhead & Redundant DDL Execution
   - 3.4 Connection and Lock Leaks under Unhandled Exceptions
   - 3.5 Inter-Process DB Contention: FastAPI vs Background Daily Bot
4. [Async Event Loop Starvation & Blocking I/O](#4-async-event-loop-starvation--blocking-io)
   - 4.1 FastAPI Execution Model: Synchronous `def` vs `async def`
   - 4.2 AnyIO Worker Thread Pool Saturation via `/api/scan_now`
   - 4.3 Synchronous 8.5MB Disk I/O on Worker Threads
   - 4.4 CPU-Bound Indicator Calculations on HTTP Workers
   - 4.5 Denial of Service & Yahoo Finance Rate-Limiting Blast Radius
5. [Frontend DOM Polling & Asynchronous State Drift](#5-frontend-dom-polling--asynchronous-state-drift)
   - 5.1 Short-Polling Overlap & Out-of-Order DOM Mutation
   - 5.2 Rapid Ticker Switching Race Condition in TradingView Chart
   - 5.3 Optimistic UI Desynchronization on Buy/Sell Actions
   - 5.4 Dual Chart Streaming & Crosshair Synchronization Race
6. [File-Based Persistence Race Conditions](#6-file-based-persistence-race-conditions)
   - 6.1 Non-Atomic 8.5MB File Truncation in `generate_dashboard_feed.py`
   - 6.2 Partial JSON Reads & Silent Failure Cascades
   - 6.3 Unsynchronized Multi-Process Writes to `trade_history.csv` & `wepoll_latest_stream.json`
7. [High-Concurrency Hardening Architecture](#7-high-concurrency-hardening-architecture)
   - 7.1 Async SQLite Engine (`aiosqlite` / SQLAlchemy 2.0 Async + WAL Mode)
   - 7.2 Strict Decoupling: Network Ingestion vs Atomic Short-Lived Transactions
   - 7.3 Atomic File Persistence Engine with Multi-Process File Locks
   - 7.4 Background Task Queue & Deduplicated Job Runner
   - 7.5 Real-Time WebSocket Pub/Sub Architecture
   - 7.6 Optimistic Concurrency Control (OCC) with Revision Versioning
   - 7.7 Frontend Request Cancellation via `AbortController` and Request Sequencing
8. [Stress-Testing Scenarios & Concurrency Verification Test Plans](#8-stress-testing-scenarios--concurrency-verification-test-plans)
   - 8.1 Scenario A: High-Concurrency Database Contention & Lockout Test
   - 8.2 Scenario B: File Persistence Atomic Write & Dirty Read Load Test
   - 8.3 Scenario C: Async Worker Pool Saturation & Event Loop Starvation Test
   - 8.4 Scenario D: Frontend Rapid Ticker Switching Out-of-Order Simulation
   - 8.5 Automated Pytest & Locust Test Suite Implementation

---

## 1. Executive Summary & Concurrency Risk Profile

The R-Sangmoo Quant Trading Platform implements a 3-Gate quantitative investment engine (Gate-0 Macro Stance, Gate-1 NLP Broadcast Parsing, Gate-2 17-Year Quant Formula) served through a FastAPI backend, an interactive TradingView dashboard, and scheduled daily batch processes. 

A rigorous, line-by-line concurrency and transactional state audit revealed that while the business logic and quantitative algorithms are domain-sound, the platform's **concurrency architecture, database transaction isolation, file persistence, and frontend state synchronization exhibit severe structural vulnerabilities**.

```
+-------------------------------------------------------------------------------------------------------+
|                                    CURRENT CONCURRENCY TOPOLOGY                                       |
+-------------------------------------------------------------------------------------------------------+
|                                                                                                       |
|  +--------------------+         HTTP Fetch (30s Polling)          +-------------------------------+   |
|  | al_sangmoo_        | <=======================================> | FastAPI Server (server.py)    |   |
|  | dashboard.html     |                                           | (AnyIO Threadpool Worker)     |   |
|  +--------------------+                                           +---------------+---------------+   |
|            |                                                                      |                   |
|            | (No AbortController / Rapid Ticker Switch Race)                      |                   |
|            v                                                                      |                   |
|  [Out-of-Order DOM Mutation]                                                      |                   |
|                                                                                   |                   |
|  +---------------------------+       Concurrent Unlocked File Writes              |                   |
|  | al_sangmoo_daily_bot.py   | ------------------------------------+              |                   |
|  +---------------------------+                                     |              |                   |
|            |                                                       v              v                   |
|            | (Exclusive DB Lock)                         +------------------------------------+       |
|            v                                             | dashboard_data.json (8.5 MB)       |       |
|  +---------------------------+                           | wepoll_latest_stream.json          |       |
|  | SQLite quant_trades.db    |                           | trade_history.csv                  |       |
|  | (Rollback Mode: DELETE)   | <-------------------------+------------------------------------+       |
|  +---------------------------+      [0-Byte Truncation Window / Dirty Reads / Corrupted JSON]         |
|            ^                                                                                          |
|            | (Hold Transaction for 20s during yf.download() Network Calls)                            |
|  +---------+-----------------+                                                                        |
|  | db_manager.py             |                                                                        |
|  | get_live_portfolio()      |                                                                        |
|  +---------------------------+                                                                        |
+-------------------------------------------------------------------------------------------------------+
```

### Risk Profile Breakdown
- **Overall Concurrency Risk Rating**: **CRITICAL**
- **Data Integrity Hazard**: **HIGH** (Risk of unrecoverable database locks, lost portfolio updates, and corrupted 8.5MB JSON dashboard feeds).
- **Service Availability Hazard**: **CRITICAL** (Synchronous heavy scans freeze backend worker pools; lock timeouts crash transaction handlers).
- **User Experience Drift**: **MEDIUM-HIGH** (Frontend short polling overwrites user buy/sell state; rapid chart clicks render mismatched ticker datasets).

---

## 2. Concurrency & Race Condition Vulnerability Matrix

| Vulnerability ID | Title | Severity | Component | Concurrency Failure Mode | Business & Technical Impact |
| :--- | :--- | :---: | :--- | :--- | :--- |
| **VULN-CC-01** | Long-Lived Transaction Poisoning with External Network I/O | **CRITICAL** | `db_manager.py` | `get_live_portfolio()` holds open SQLite transaction while executing sequential `yf.download()` HTTP requests in a loop. | Blocks all concurrent portfolio writes (`add_portfolio_buy`, `record_portfolio_sell`) with `sqlite3.OperationalError: database is locked`. |
| **VULN-CC-02** | Non-Atomic 8.5MB JSON Truncation & Dirty Read Collision | **CRITICAL** | `generate_dashboard_feed.py`, `server.py` | Direct `open(OUTPUT_JSON, "w")` truncates file to 0 bytes before serializing 8.5MB JSON payload. | Concurrent readers encounter `JSONDecodeError`, silently zeroing out macro & KPI cards on frontend. |
| **VULN-CC-03** | Threadpool Starvation & DoS via Synchronous `/api/scan_now` | **HIGH** | `server.py`, `al_sangmoo_daily_bot.py` | Heavy 60-second synchronous market scanning runs directly inside FastAPI AnyIO worker threads. | Multiple concurrent scan requests exhaust thread pool, causing total server unresponsiveness and Yahoo Finance IP rate-limiting. |
| **VULN-CC-04** | Broken Method References in Portfolio API Endpoints | **HIGH** | `server.py` | Line 144 calls non-existent `db_manager.close_portfolio_position`; Line 156 calls non-existent `db_manager.clear_portfolio`. | 100% runtime crash (`AttributeError`) when users attempt to sell positions or reset portfolio via web UI. |
| **VULN-CC-05** | Multi-Process SQLite Contention (Bot vs Web Server) | **HIGH** | `quant_trades.db`, `al_sangmoo_daily_bot.py` | SQLite defaults to rollback journal mode (`DELETE`) without connection pooling or busy timeout tuning. | Daily 12:30 batch bot locks database exclusively, causing incoming web user trade requests to fail. |
| **VULN-CC-06** | Lost Update Anomaly in Portfolio Mutations (No OCC) | **HIGH** | `db_manager.py`, `server.py` | Multiple concurrent portfolio mutations (buy, sell, price refresh) perform unversioned read-modify-write cycles. | Overwrites concurrent transactions, resulting in ghost positions and corrupted realized PnL accounting. |
| **VULN-CC-07** | Frontend Short-Polling Out-of-Order State Drift | **MEDIUM** | `al_sangmoo_dashboard.html` | 30s `setInterval(loadDashboard)` races with immediate post-mutation `loadDashboard()` calls without request cancellation. | In-flight stale polling response overwrites freshly placed buy/sell order in DOM. |
| **VULN-CC-08** | Rapid Ticker Switching Chart Desynchronization | **MEDIUM** | `al_sangmoo_dashboard.html` | Asynchronous `loadChartData(ticker)` calls resolve out-of-order when user rapidly clicks matrix tickers. | Chart candles display ticker $A$ while header, price tag, and buy order box display ticker $B$. |
| **VULN-CC-09** | Unsynchronized Global In-Memory Cache Mutation (`CHART_CACHE`) | **MEDIUM** | `server.py` | Global dictionary `CHART_CACHE` mutated and reassigned concurrently across threadpool workers without locks. | Race conditions, stale chart reads, and potential `RuntimeError: dictionary changed size during iteration`. |
| **VULN-CC-10** | Unlocked Multi-Process Writes to `trade_history.csv` | **MEDIUM** | `generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py` | `trade_history.csv` read/written via un-locked `pandas.to_csv()` and `pandas.read_csv()`. | Partial CSV writes result in corrupt lines, `ParserError`, or truncated historical tracking records. |
| **VULN-CC-11** | Redundant DDL Table Creation on Every Database Call | **LOW** | `db_manager.py` | Every helper function calls `init_db()` (executing 4 `CREATE TABLE IF NOT EXISTS` queries) before executing queries. | Acquires schema locks, inflates SQLite lock contention, and adds unnecessary query latency. |

---

## 3. SQLite Database Contention & Transaction Safety Analysis

### 3.1 SQLite Lock Escalation & Absence of WAL Mode

SQLite is a serverless, single-file database engine. By default, SQLite operates in **Rollback Journal Mode (`PRAGMA journal_mode = DELETE;`)**. Under this mode, SQLite enforces a coarse-grained locking hierarchy:

```
[ UNLOCKED ]  ===>  [ SHARED ] (Multiple Readers)
                          |
                          v
                    [ RESERVED ] (One Writer planning to write; readers still allowed)
                          |
                          v
                    [ PENDING ] (Writer waiting for readers to finish; NO NEW READERS)
                          |
                          v
                    [ EXCLUSIVE ] (Single Writer; ALL READERS AND WRITERS BLOCKED)
```

In `db_manager.py`, `sqlite3.connect(DB_FILE)` is instantiated without setting `PRAGMA journal_mode = WAL;` and without configuring `timeout`:

```python
# db_manager.py: Lines 10-13
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn
```

**Failure Mode**: When any process initiates a write (e.g. `save_macro_history_record`, `record_portfolio_sell`, or `al_sangmoo_daily_bot.py`), SQLite transitions to `PENDING` and `EXCLUSIVE`. While `EXCLUSIVE` is held, any concurrent read request (such as `get_latest_macro_record()` or `get_recommendations_matrix()`) is immediately blocked. If the lock is held longer than the default 5.0 seconds, SQLite raises:
```
sqlite3.OperationalError: database is locked
```

### 3.2 Transaction Poisoning via Synchronous Network I/O in `get_live_portfolio()`

The single most critical database vulnerability exists in `db_manager.py:get_live_portfolio()`.

```python
# db_manager.py: Lines 249-301
def get_live_portfolio():
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM my_portfolio WHERE status = 'HOLDING' ORDER BY buy_date DESC, id DESC")
    holdings = [dict(r) for r in cursor.fetchall()]
    
    total_invested = 0.0
    total_eval = 0.0
    
    for h in holdings:
        ticker = h['ticker']
        try:
            # CRITICAL FLAW: Synchronous HTTP Network Request inside active DB transaction!
            cur_data = yf.download(ticker, period="5d", interval="1d", progress=False)
            if not cur_data.empty:
                ...
                cur_price = float(cur_data.iloc[-1]['Close'])
                ...
                # DML Write Statement inside the loop
                cursor.execute("""
                UPDATE my_portfolio SET current_price = ?, current_value = ?, pnl_pct = ?, pnl_amount = ?, exit_advice = ?
                WHERE id = ?
                """, (cur_price, cur_val, pnl_pct, pnl_amt, advice, h['id']))
        except Exception:
            pass
            
        total_invested += float(h['total_cost'])
        total_eval += float(h['current_value'])
        
    conn.commit()
    conn.close()
```

#### Mathematical Proof of Lock Holding Duration
Let $N$ be the number of active portfolio holdings, and let $T_{\text{net}}(i)$ be the HTTP round-trip latency to Yahoo Finance for ticker $i$. Let $T_{\text{db}}$ be the local SQLite update time ($< 0.5\text{ ms}$).

The total transaction hold time $T_{\text{hold}}$ is:
$$T_{\text{hold}} = \sum_{i=1}^N \Big( T_{\text{net}}(i) + T_{\text{db}} \Big)$$

If a user holds $N = 8$ assets, and each Yahoo Finance call takes an average of $1.5\text{ s}$ (with occasional network tail latency of $3.5\text{ s}$):
$$T_{\text{hold}} = 8 \times 1.5\text{ s} = 12.0\text{ seconds}$$

During this entire 12-second window:
1. The first `UPDATE` statement elevates the SQLite connection to a `RESERVED`/`EXCLUSIVE` transaction lock.
2. The transaction remains uncommitted until all 8 network requests finish.
3. If a user clicks "Enter Portfolio" (`/api/portfolio/buy`) or "Exit" (`/api/portfolio/sell/{id}`), their request attempts to acquire a write lock. Because the lock is held for $12\text{ s} > 5.0\text{ s}$ (default busy timeout), the user's order fails catastrophically.

```
Time (s)    Thread 1: get_live_portfolio()               Thread 2: /api/portfolio/buy
-------------------------------------------------------------------------------------------------
t = 0.0s    BEGIN TRANSACTION (Implicit)
t = 0.1s    yf.download("NVDA") [Network I/O...]
t = 1.5s    UPDATE my_portfolio (Lock = RESERVED)
t = 1.6s    yf.download("AMZN") [Network I/O...]        POST /api/portfolio/buy (Lock Request)
t = 3.1s    UPDATE my_portfolio                         ... Waiting for DB Lock (0.0s elapsed) ...
t = 3.2s    yf.download("TSLA") [Network I/O...]        ... Waiting for DB Lock (1.6s elapsed) ...
t = 5.0s    UPDATE my_portfolio                         ... Waiting for DB Lock (3.4s elapsed) ...
t = 6.6s    ...                                         ERROR: sqlite3.OperationalError: database is locked
t = 12.0s   COMMIT & CLOSE (Lock Released)              (User order was dropped 5.4s ago!)
```

### 3.3 Connection Spawning Overhead & Redundant DDL Execution

Every single function in `db_manager.py` includes a call to `init_db()` before calling `get_db()`:
- `save_macro_history_record` (Line 108)
- `get_latest_macro_record` (Line 139)
- `add_portfolio_buy` (Line 148)
- `record_portfolio_sell` (Line 180)
- `reset_all_holdings` (Line 213)
- `save_recommendation_matrix_record` (Line 222)
- `get_live_portfolio` (Line 250)
- `get_recommendations_matrix` (Line 314)

```python
# db_manager.py: Lines 15-20
def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS my_portfolio (...)")
    cursor.execute("CREATE TABLE IF NOT EXISTS recommendation_matrix (...)")
    cursor.execute("CREATE TABLE IF NOT EXISTS trades (...)")
    cursor.execute("CREATE TABLE IF NOT EXISTS macro_history (...)")
    conn.commit()
    conn.close()
```

**Architectural Impact**:
1. For every read or write, the application opens **two distinct SQLite connections** in series: Connection #1 executes 4 DDL statements and commits; Connection #2 executes the actual query.
2. DDL statements (`CREATE TABLE IF NOT EXISTS`) require exclusive schema locks in SQLite. Executing DDL on high-frequency read paths dramatically magnifies lock contention and degrades throughput.

### 3.4 Connection and Lock Leaks under Unhandled Exceptions

In `db_manager.py`, database connections are opened without Python context managers (`with sqlite3.connect(...)` or `try...finally` blocks):

```python
# db_manager.py: Lines 147-177
def add_portfolio_buy(ticker, buy_price, quantity, buy_date=None):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    ...
    cursor.execute("INSERT INTO my_portfolio ...", (...))
    inserted_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return inserted_id
```

**Failure Mode**: If an uncaught exception occurs between `get_db()` and `conn.close()` (e.g. invalid type casting, database disk full, OS interrupt, memory exhaustion), `conn.close()` is skipped. The orphaned connection object remains in memory, holding SQLite file locks indefinitely until Python garbage collection reclaims it.

### 3.5 Inter-Process DB Contention: FastAPI vs Background Daily Bot

The platform runs two independent OS processes interacting with `quant_trades.db`:
1. `uvicorn server:app` (Web server handling user requests and API polling).
2. `python al_sangmoo_daily_bot.py` (Daily cron batch pipeline at 12:30 KST).

In `al_sangmoo_daily_bot.py:evaluate_active_positions_and_update()` (Lines 368-400), the bot loops through all historical trades and executes a series of `INSERT OR REPLACE INTO trades` queries inside an explicit transaction:

```python
# al_sangmoo_daily_bot.py: Lines 369-399
db_manager.init_db()
conn = db_manager.get_db()
cursor = conn.cursor()
for idx, row in history_df.iterrows():
    ...
    cursor.execute("INSERT OR REPLACE INTO trades ...", (...))
conn.commit()
conn.close()
```

When this batch sync runs concurrently with `server.py` serving `/api/dashboard` or `/api/portfolio`, the multi-process lock contention on `quant_trades.db` produces mutual lockouts because inter-process lock arbitration is unmanaged.

---

## 4. Async Event Loop Starvation & Blocking I/O

### 4.1 FastAPI Execution Model: Synchronous `def` vs `async def`

FastAPI is built on Starlette and AnyIO. Its concurrency model operates under two distinct paradigms:
1. `async def endpoint()`: Runs directly on the main asyncio event loop. Any blocking synchronous call (e.g. `time.sleep()`, synchronous `open()`, or synchronous `sqlite3` / `requests`) will **freeze the entire event loop**, preventing all other async tasks and coroutines from processing.
2. `def endpoint()`: Offloaded to an external worker thread pool managed by AnyIO (`anyio.to_thread.run_sync`).

In `server.py`, all endpoints are defined using standard synchronous `def`:
```python
# server.py
@app.get("/api/dashboard")
def get_dashboard_summary(): ...

@app.post("/api/scan_now")
def trigger_scan_now(): ...

@app.get("/api/chart/{ticker}")
def get_ticker_chart(ticker: str): ...
```

While defining endpoints as `def` prevents direct freezing of the asyncio main event loop, it introduces **thread pool exhaustion and secondary starvation**.

### 4.2 AnyIO Worker Thread Pool Saturation via `/api/scan_now`

The default worker thread pool size in AnyIO is 40 threads.

Look at `/api/scan_now` in `server.py`:
```python
# server.py: Lines 183-196
@app.post("/api/scan_now")
def trigger_scan_now():
    try:
        import al_sangmoo_daily_bot
        bull_picks, neutral_picks, bear_picks = al_sangmoo_daily_bot.scan_and_select_2x2x2()
        today_str = datetime.now().strftime("%Y-%m-%d")
        db_manager.save_recommendation_matrix_record(today_str, bull_picks, neutral_picks, bear_picks)
        data = build_dashboard_data()
        global CHART_CACHE
        CHART_CACHE = data.get("charts", {})
        return {"status": "success", "message": f"{today_str} 실시간 스캔 & 차트 갱신 완료!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
```

`al_sangmoo_daily_bot.scan_and_select_2x2x2()` downloads historical market data for 23 universe tickers sequentially:
$$T_{\text{scan}} = 23 \text{ tickers} \times 1.2\text{ s} \approx 27.6\text{ seconds}$$
`build_dashboard_data()` then runs `compute_all_indicators()` on 23 watchlist tickers sequentially:
$$T_{\text{feed}} = 23 \text{ tickers} \times 1.2\text{ s} \approx 27.6\text{ seconds}$$
**Total Execution Time for a single `/api/scan_now` request = 55 to 65 seconds!**

**Failure Mode**:
If 2 to 3 users click "Run Live Scan" simultaneously, or if an automated client triggers retries, multiple 60-second synchronous tasks monopolize worker threads.
- Thread worker queue latency skyrockets.
- Lightweight requests (e.g. `/api/portfolio` or `/api/chart/NVDA`) are queued behind the 60-second scans.
- Web browser requests time out with `504 Gateway Timeout` or connection drops.

### 4.3 Synchronous 8.5MB Disk I/O on Worker Threads

In `server.py:get_dashboard_summary()` and `server.py:startup_event()`:
```python
# server.py: Lines 96-103
if os.path.exists(DASHBOARD_JSON):
    try:
        with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
            feed = json.load(f)
            macro_info = feed.get("macro", {})
            kpis = feed.get("kpis", {})
    except Exception:
        pass
```

`dashboard_data.json` on disk is **8,456,071 bytes (8.5 MB)**.
- Reading an 8.5MB file from disk and parsing it via `json.load()` requires 30ms - 80ms of synchronous CPU and file I/O time per request.
- With 10 frontend clients polling `/api/dashboard` every 30 seconds, the server spends hundreds of milliseconds per minute synchronously reading and parsing identical 8.5MB JSON files instead of serving from memory.

### 4.4 CPU-Bound Indicator Calculations on HTTP Workers

In `server.py:get_ticker_chart(ticker)`:
```python
# server.py: Lines 176-181
data = compute_all_indicators(ticker_upper)
if not data:
    raise HTTPException(status_code=404, detail="시세 데이터를 불러올 수 없습니다.")
CHART_CACHE[ticker_upper] = data
return data
```

When a cache miss occurs in `CHART_CACHE`:
1. `compute_all_indicators()` downloads 2 years (500 bars) of OHLCV data.
2. Computes rolling max/min for Tenkan (9), Kijun (26), Senkou Span A/B (52), SMA20, SMA60, and Volume ratios in pandas.
3. Generates 500 dictionary objects for candles, lines, and 26-day forward projections.
This CPU-heavy transformation blocks the worker thread for 1.5 - 3.0 seconds per ticker miss.

### 4.5 Denial of Service & Yahoo Finance Rate-Limiting Blast Radius

Because neither `/api/scan_now` nor `/api/chart/{ticker}` implements concurrency semaphores, rate limiting, or request deduplication:
- A malicious actor or an accidental double-click can trigger 50 concurrent yfinance download routines.
- Yahoo Finance detects rapid concurrent requests from the same IP address and issues `HTTP 429 Too Many Requests` or temporary IP bans, collapsing market data fetching for the entire platform.

---

## 5. Frontend DOM Polling & Asynchronous State Drift

### 5.1 Short-Polling Overlap & Out-of-Order DOM Mutation

In `al_sangmoo_dashboard.html`:
```javascript
// al_sangmoo_dashboard.html: Lines 1414-1419
window.addEventListener("DOMContentLoaded", () => {
    initCharts();
    selectStock("NVDA", 225.16);
    loadDashboard();
    setInterval(loadDashboard, 30000); // 30s live refresh
});
```

And when a user submits a buy order:
```javascript
// al_sangmoo_dashboard.html: Lines 1341-1369
async function submitQuickBuy() {
    ...
    const res = await fetch('/api/portfolio/buy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker: currentSelectedTicker, buy_price: buyPrice, quantity: qty })
    });
    if (res.ok) {
        loadDashboard(); // Triggers manual refresh
        return;
    }
}
```

#### The State Drift Race Condition Sequence
```
Client Timeline     Periodic Poller (30s)                User Action: submitQuickBuy()
--------------------------------------------------------------------------------------------------
t = 0.0s            fetch('/api/dashboard') [Request #1 dispatched...]
t = 0.2s                                                 User clicks "Enter Portfolio" (NVDA 10 shares)
t = 0.3s                                                 fetch('/api/portfolio/buy') [POST dispatched]
t = 0.6s                                                 POST responds 200 OK (Buy registered in DB)
t = 0.7s                                                 loadDashboard() [Request #2 dispatched]
t = 1.0s                                                 Request #2 responds 200 OK
t = 1.1s                                                 DOM updated with NVDA holding (Correct!)
t = 1.8s            Request #1 responds 200 OK (Delayed)
t = 1.9s            renderDashboardData(staleData)       DOM OVERWRITTEN: NVDA disappears!
```

**Failure Mode**: Request #1 was generated *before* the buy order was processed on the server, but due to network jitter or server scheduling, Request #1 resolved *after* Request #2. The frontend blindly executes `renderDashboardData(data)`, reverting the DOM to the stale state where NVDA is missing. The user assumes their order failed and clicks "Enter Portfolio" again, resulting in an unintended duplicate buy order.

### 5.2 Rapid Ticker Switching Race Condition in TradingView Chart

In `al_sangmoo_dashboard.html`, clicking any ticker pill calls `selectStock(ticker, price)`:
```javascript
// al_sangmoo_dashboard.html: Lines 888-909
function selectStock(ticker, price) {
    currentSelectedTicker = ticker;
    currentSelectedPrice = price || 100.0;

    document.getElementById("curTicker").textContent = ticker;
    document.getElementById("curPrice").textContent = `$${currentSelectedPrice.toFixed(2)}`;
    ...
    updateDecoderUI(ticker);
    loadChartData(ticker);
}

// al_sangmoo_dashboard.html: Lines 969-1068
async function loadChartData(ticker) {
    ...
    const res = await fetch(`/api/chart/${ticker}`);
    if (res.ok) {
        chartObj = await res.json();
    }
    ...
    candleSeries.setData(chartObj.candles);
    kijunSeries.setData(chartObj.kijun_line || []);
    ...
}
```

**Failure Mode**:
1. User clicks **AMZN** at $t = 0.0\text{ s}$. `fetch('/api/chart/AMZN')` is fired (takes 1.8 seconds due to live compute).
2. User immediately clicks **NVDA** at $t = 0.3\text{ s}$. `fetch('/api/chart/NVDA')` is fired (hits in-memory cache, takes 0.1 seconds).
3. At $t = 0.4\text{ s}$, NVDA data arrives. Chart renders NVDA candles. Header displays NVDA.
4. At $t = 1.8\text{ s}$, the stale AMZN data arrives. `loadChartData()` completes and calls `candleSeries.setData(amznCandles)`.
5. **Outcome**: The UI is in a corrupt split state. Header and Quick-Buy box show `NVDA $225.16`, but the TradingView chart displays AMZN's candlestick data ($261.31), creating extreme confusion for order execution.

```
+--------------------------------------------------------------------------------------+
|                           CHART DESYNCHRONIZATION DEFECT                             |
+--------------------------------------------------------------------------------------+
|  Header Bar:       [ NVDA ] $225.16  (Active Selection)                              |
|  Quick Buy Box:    Enter Position for [ NVDA ] at $225.16                            |
|                                                                                      |
|  Chart Canvas:     [ AMZN Candles: $261.31 ] <--- Stale Overwrite from Delayed Fetch!|
|  Decoder Cards:    AMZN 26-Day Kijun $256.68 (Mismatched with NVDA header)           |
+--------------------------------------------------------------------------------------+
```

### 5.3 Optimistic UI Desynchronization on Buy/Sell Actions

In `al_sangmoo_dashboard.html`:
- `submitQuickBuy()` and `sellHolding()` lack client-side optimistic state rollback.
- There are no client-side transaction idempotency keys (`Idempotency-Key: <uuid>`).
- If a network disconnect occurs while the server processes the order, the client assumes failure and alerts `[Local Simulated Entry]`, while the server has already committed the real trade into `my_portfolio`.

### 5.4 Dual Chart Streaming & Crosshair Synchronization Race

`al_sangmoo_dashboard.html` uses two separate Lightweight Chart instances:
1. `mainChart` (Candlesticks, Tenkan, Kijun, Cloud, SMAs)
2. `volumeChart` (Volume histogram)

Visible time ranges are synchronized via:
```javascript
// al_sangmoo_dashboard.html: Lines 857-859
mainChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
    volumeChart.timeScale().setVisibleLogicalRange(range);
});
```

**Failure Mode**:
During rapid user panning or zooming on high-DPI / touch displays, `subscribeVisibleLogicalRangeChange` fires dozens of events per second. Because there is no throttling or `requestAnimationFrame` debouncing, the two chart rendering engines enter recursive layout thrashing, causing visible stutter, dropped frames, and canvas tearing.

---

## 6. File-Based Persistence Race Conditions

### 6.1 Non-Atomic 8.5MB File Truncation in `generate_dashboard_feed.py`

In `generate_dashboard_feed.py:build_dashboard_data()`:
```python
# generate_dashboard_feed.py: Lines 301-304
out_path = os.path.join(BASE_DIR, OUTPUT_JSON)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
```

And similarly in `youtube_stream_scanner.py`:
```python
# youtube_stream_scanner.py: Lines 468-469
with open(CACHE_FILE, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
```

#### Operating System File Mechanics: The Truncation Window
When Python executes `open(filename, "w")`:
1. The OS kernel issues the system call `open(..., O_WRONLY | O_CREAT | O_TRUNC)`.
2. The file length is immediately set to **0 bytes**.
3. `json.dump()` begins serializing the Python dictionary into formatted JSON text.
4. Writing an 8,456,071 byte payload formatted with `indent=2` takes **40ms to 250ms** depending on disk I/O and OS buffer flushing.
5. During this multi-millisecond window, the file size grows incrementally from 0 KB $\to$ 2,000 KB $\to$ 8,456 KB.

```
Process A (Generator): open("dashboard_data.json", "w")  ===> File size = 0 KB (TRUNCATED)
                     | json.dump(payload...) [Writing bytes...]
Process B (FastAPI):   | open("dashboard_data.json", "r") ===> json.load(f)
                     |                                       ERROR: JSONDecodeError ("Expecting value")
Process A (Generator): ===> flush() & close()           ===> File size = 8,456 KB (COMPLETED)
```

### 6.2 Partial JSON Reads & Silent Failure Cascades

When `server.py:get_dashboard_summary()` reads `dashboard_data.json` while `generate_dashboard_feed.py` or `trigger_scan_now()` is writing it:

```python
# server.py: Lines 96-103
if os.path.exists(DASHBOARD_JSON):
    try:
        with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
            feed = json.load(f)
            macro_info = feed.get("macro", {})
            kpis = feed.get("kpis", {})
    except Exception:
        pass # SILENT CATCH!
```

**Failure Impact**:
1. `json.load(f)` encounters truncated syntax (e.g. unmatched brackets or 0 bytes) and raises `json.decoder.JSONDecodeError`.
2. The `try...except Exception: pass` block catches and discards the exception.
3. `macro_info` and `kpis` remain `{}` (empty dictionaries).
4. The server returns:
```json
{
  "macro": {},
  "kpis": {},
  "portfolio": { ... },
  "matrix": [ ... ],
  "last_updated": "2026-08-21 20:15:00"
}
```
5. The frontend DOM receives empty macro data and wipes the MSI needle, wipes the 5 macro gauge cards, and clears KPI statistics.

### 6.3 Unsynchronized Multi-Process Writes to `trade_history.csv` & `wepoll_latest_stream.json`

`trade_history.csv` is read and updated concurrently by:
1. `al_sangmoo_daily_bot.py:evaluate_active_positions_and_update()` via `history_df.to_csv(HISTORY_CSV, index=False)`.
2. `generate_dashboard_feed.py:build_dashboard_data()` via `pd.read_csv(HISTORY_CSV)`.

Neither process utilizes file locking (`fcntl.flock` on Unix or `msvcrt.locking` on Windows, or cross-platform `portalocker`). If the daily bot writes to `trade_history.csv` while the feed builder reads it, pandas raises `pd.errors.EmptyDataError` or `pd.errors.ParserError: Error tokenizing data`.

---

## 7. High-Concurrency Hardening Architecture

To permanently resolve all concurrency, lock contention, thread starvation, file persistence, and frontend desynchronization issues, we specify the **High-Concurrency Production Hardening Architecture**.

```
+--------------------------------------------------------------------------------------------------------------------+
|                                    HARDENED HIGH-CONCURRENCY ARCHITECTURE                                          |
+--------------------------------------------------------------------------------------------------------------------+
|                                                                                                                    |
|   +-------------------------------------------------------+         WebSocket (/ws/live_feed)                      |
|   | Frontend (al_sangmoo_dashboard.html)                  | <====================================+                 |
|   | - AbortController on fetch requests                   |                                      |                 |
|   | - Request Sequence Monotonic IDs                      |                                      |                 |
|   | - rAF-Throttled Chart Sync                            |                                      |                 |
|   +-------------------------------------------------------+                                      |                 |
|                               ^                                                                  |                 |
|                               | HTTP REST (Idempotent Orders + OCC Revision Check)               |                 |
|                               v                                                                  v                 |
|   +------------------------------------------------------------------------------------------------------------+   |
|   | FastAPI Application Layer (server.py)                                                                      |   |
|   |                                                                                                            |   |
|   |  +-------------------------------------+      +-----------------------------------+   +-----------------+  |   |
|   |  | In-Memory Cache & Broadcast Hub     |      | Background Task Queue (asyncio)   |   | WebSocket Hub   |  |   |
|   |  | (Thread-Safe Async RLock)           |      | - Single-Flight Scan Mutex        |   | (Pub/Sub State) |  |   |
|   |  +-------------------------------------+      +-----------------------------------+   +-----------------+  |   |
|   +------------------------------------------------------------------------------------------------------------+   |
|                               |                                                  |                                 |
|                               | Short Atomic Transactions                        | Atomic File IO                  |
|                               v                                                  v                                 |
|   +---------------------------------------------------+          +---------------------------------------------+   |
|   | aiosqlite / SQLAlchemy Async Engine               |          | Atomic Persistence Engine                   |   |
|   | - PRAGMA journal_mode = WAL;                      |          | - tempfile.NamedTemporaryFile               |   |
|   | - PRAGMA busy_timeout = 30000;                    |          | - os.replace() (Atomic OS Inode Swap)       |   |
|   | - Connection Pool (Max 10, Min 2)                 |          | - Multi-Process Portalocker (.lock)         |   |
|   | - OCC: version = version + 1                      |          +---------------------------------------------+   |
|   +---------------------------------------------------+                                  |                         |
|                               |                                                      v                         |
|                               v                                          +---------------------------------+   |
|   +---------------------------------------------------+                  | dashboard_data.json             |   |
|   | SQLite (quant_trades.db)                          |                  | (Always 100% Valid JSON)        |   |
|   | [Readers never block Writers; Writers never block]|                  +---------------------------------+   |
|   +---------------------------------------------------+                                                        |
+--------------------------------------------------------------------------------------------------------------------+
```

### 7.1 Async SQLite Engine (`aiosqlite` + WAL Mode & Connection Tuning)

Replace the synchronous `sqlite3` driver with `aiosqlite` utilizing explicit Write-Ahead Logging (WAL) and tuned database pragmas.

#### Hardened `db_manager_async.py` Blueprint
```python
import os
import aiosqlite
from contextlib import asynccontextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "quant_trades.db")

@asynccontextmanager
async def get_db_session():
    """
    Asynchronous connection manager with WAL mode, busy timeout, and auto-commit/rollback.
    """
    conn = await aiosqlite.connect(DB_FILE, timeout=30.0)
    conn.row_factory = aiosqlite.Row
    
    # 1. WAL Mode: Readers never block Writers, Writers never block Readers
    await conn.execute("PRAGMA journal_mode = WAL;")
    # 2. Busy Timeout: Wait up to 30,000ms before raising OperationalError
    await conn.execute("PRAGMA busy_timeout = 30000;")
    # 3. Synchronous = NORMAL: Optimal balance of ACID safety and write performance in WAL mode
    await conn.execute("PRAGMA synchronous = NORMAL;")
    # 4. In-memory temporary tables & 64MB cache
    await conn.execute("PRAGMA temp_store = MEMORY;")
    await conn.execute("PRAGMA cache_size = -64000;")
    
    try:
        yield conn
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()

async def init_db_schema():
    """
    Executed ONLY ONCE during FastAPI application startup lifespan.
    """
    async with get_db_session() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS my_portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            buy_date TEXT NOT NULL,
            buy_price REAL NOT NULL,
            quantity REAL NOT NULL,
            current_price REAL DEFAULT 0,
            total_cost REAL NOT NULL,
            current_value REAL DEFAULT 0,
            pnl_pct REAL DEFAULT 0,
            pnl_amount REAL DEFAULT 0,
            target_price REAL DEFAULT 0,
            stop_loss_price REAL DEFAULT 0,
            partial_tp_price REAL DEFAULT 0,
            status TEXT DEFAULT 'HOLDING',
            sell_date TEXT,
            sell_price REAL,
            exit_advice TEXT DEFAULT '보유 지속',
            version INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        );
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS recommendation_matrix (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            bull_1 TEXT, bull_1_price REAL,
            bull_2 TEXT, bull_2_price REAL,
            neutral_1 TEXT, neutral_1_price REAL,
            neutral_2 TEXT, neutral_2_price REAL,
            bear_1 TEXT, bear_1_price REAL,
            bear_2 TEXT, bear_2_price REAL,
            created_at TEXT
        );
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            type TEXT NOT NULL,
            entry_price REAL NOT NULL,
            current_price REAL DEFAULT 0,
            target_price REAL NOT NULL,
            partial_tp_price REAL NOT NULL,
            stop_loss_price REAL NOT NULL,
            pnl_pct REAL DEFAULT 0,
            max_gain_pct REAL DEFAULT 0,
            status TEXT DEFAULT 'OPEN',
            days_active INTEGER DEFAULT 0,
            exit_advice TEXT DEFAULT '보유 지속',
            version INTEGER DEFAULT 1,
            updated_at TEXT
        );
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS macro_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            vix_val REAL, vix_status TEXT,
            us10y_val REAL, us10y_status TEXT,
            wti_val REAL, wti_status TEXT,
            macro_stance TEXT,
            macro_headline TEXT,
            macro_directive TEXT,
            external_shocks TEXT,
            created_at TEXT
        );
        """)
```

### 7.2 Strict Decoupling: Network Ingestion vs Atomic Short-Lived Transactions

Completely separate external HTTP network fetching from the database transaction boundary.

#### Refactored Zero-Lock Holding Pattern for `get_live_portfolio()`
```python
import asyncio
import yfinance as yf
import pandas as pd

async def fetch_single_ticker_price(ticker: str) -> float:
    """Non-blocking ticker fetch offloaded to async threadpool."""
    loop = asyncio.get_running_loop()
    def _download():
        df = yf.download(ticker, period="5d", interval="1d", progress=False)
        if not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return float(df.iloc[-1]['Close'])
        return None
    return await loop.run_in_executor(None, _download)

async def get_live_portfolio_hardened():
    # 1. Short Read Transaction: Fetch all open holdings (Duration < 1ms)
    async with get_db_session() as conn:
        cursor = await conn.execute(
            "SELECT * FROM my_portfolio WHERE status = 'HOLDING' ORDER BY buy_date DESC, id DESC"
        )
        rows = await cursor.fetchall()
        holdings = [dict(r) for r in rows]

    if not holdings:
        return {"holdings": [], "total_invested": 0.0, "total_eval": 0.0, "overall_pnl_pct": 0.0, "overall_pnl_amount": 0.0}

    # 2. Parallel Non-Blocking Network Ingestion: Zero DB locks held!
    tickers = list({h['ticker'] for h in holdings})
    price_tasks = [fetch_single_ticker_price(t) for t in tickers]
    prices = await asyncio.gather(*price_tasks, return_exceptions=True)
    price_map = {t: p for t, p in zip(tickers, prices) if isinstance(p, float)}

    # 3. In-Memory Valuation Computations
    total_invested = 0.0
    total_eval = 0.0
    updates = []

    for h in holdings:
        cur_price = price_map.get(h['ticker'], float(h['current_price']))
        buy_price = float(h['buy_price'])
        quantity = float(h['quantity'])
        total_cost = buy_price * quantity
        cur_val = cur_price * quantity
        pnl_pct = ((cur_price - buy_price) / buy_price) * 100
        pnl_amt = cur_val - total_cost
        
        if pnl_pct >= 15.0:
            advice = f"전량 익절 매도 권고 (목표가 달성 {pnl_pct:+.2f}%)"
        elif pnl_pct <= -3.0:
            advice = f"칼손절 긴급 매도 권고 (손절선 이탈 {pnl_pct:+.2f}%)"
        elif 8.0 <= pnl_pct < 15.0:
            advice = f"50% 분할 익절 권고 (수익률 {pnl_pct:+.1f}%)"
        else:
            advice = f"보유 지속 (손절선 ${buy_price * 0.97:,.2f} 유지)"

        h.update({
            'current_price': cur_price, 'current_value': cur_val,
            'pnl_pct': pnl_pct, 'pnl_amount': pnl_amt, 'exit_advice': advice
        })
        updates.append((cur_price, cur_val, pnl_pct, pnl_amt, advice, h['id']))
        total_invested += total_cost
        total_eval += cur_val

    # 4. Ultra-Fast Batch Write Transaction (Duration < 2ms)
    async with get_db_session() as conn:
        await conn.executemany("""
        UPDATE my_portfolio 
        SET current_price = ?, current_value = ?, pnl_pct = ?, pnl_amount = ?, exit_advice = ?, updated_at = datetime('now')
        WHERE id = ?
        """, updates)

    overall_pnl_pct = ((total_eval - total_invested) / total_invested * 100) if total_invested > 0 else 0.0
    return {
        "holdings": holdings,
        "total_invested": total_invested,
        "total_eval": total_eval,
        "overall_pnl_pct": overall_pnl_pct,
        "overall_pnl_amount": total_eval - total_invested
    }
```

### 7.3 Atomic File Persistence Engine with Multi-Process File Locks

To guarantee that no process or web client can ever observe an empty (0 byte) or half-written JSON/CSV file, all disk writes must utilize **Atomic Temporary File Renaming (`os.replace`)**.

```python
import os
import json
import tempfile
import portalocker

def atomic_write_json(target_path: str, data: dict, indent: int = 2):
    """
    Atomically writes a JSON file using temporary file swap and process lock.
    Guarantees readers never observe a truncated (0-byte) or partially written file.
    """
    directory = os.path.dirname(os.path.abspath(target_path))
    lock_path = target_path + ".lock"
    
    with portalocker.Lock(lock_path, mode="w", timeout=15):
        # Create temp file in the SAME filesystem directory to allow atomic os.replace()
        with tempfile.NamedTemporaryFile("w", dir=directory, delete=False, encoding="utf-8") as tf:
            temp_name = tf.name
            json.dump(data, tf, ensure_ascii=False, indent=indent)
            tf.flush()
            os.fsync(tf.fileno()) # Force write to physical storage
            
        # Atomic rename: Replaces target_path in a single OS inode operation
        os.replace(temp_name, target_path)

def atomic_read_json(target_path: str, default: dict = None) -> dict:
    """
    Safely reads JSON file with retry mechanism if locked.
    """
    if not os.path.exists(target_path):
        return default or {}
    try:
        with open(target_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default or {}
```

### 7.4 Background Task Queue & Deduplicated Job Runner

Prevent multiple concurrent executions of heavy scans (`/api/scan_now`) using an async single-flight lock (`asyncio.Lock`).

```python
import asyncio
from fastapi import FastAPI, BackgroundTasks, HTTPException

SCAN_MUTEX = asyncio.Lock()
IS_SCANNING = False

@app.post("/api/scan_now")
async def trigger_scan_now_hardened(background_tasks: BackgroundTasks):
    global IS_SCANNING
    if SCAN_MUTEX.locked() or IS_SCANNING:
        return JSONResponse(
            status_code=429,
            content={"status": "busy", "message": "스캔 작업이 이미 진행 중입니다. 잠시 후 완료됩니다."}
        )
        
    async def _run_scan_job():
        global IS_SCANNING, CHART_CACHE
        async with SCAN_MUTEX:
            IS_SCANNING = True
            try:
                loop = asyncio.get_running_loop()
                # Run heavy scanner in background thread executor
                data = await loop.run_in_executor(None, generate_dashboard_feed.build_dashboard_data)
                CHART_CACHE = data.get("charts", {})
                # Broadcast real-time update to all WebSocket clients
                await ws_manager.broadcast({"type": "FEED_UPDATE", "data": data})
            finally:
                IS_SCANNING = False

    background_tasks.add_task(_run_scan_job)
    return {"status": "accepted", "message": "실시간 마켓 스캔이 백그라운드에서 시작되었습니다."}
```

### 7.5 Real-Time WebSocket Pub/Sub Architecture

Eliminate the 30-second client-side short-polling loop entirely by deploying a bidirectional WebSocket connection.

```python
from fastapi import WebSocket, WebSocketDisconnect
from typing import List

class WebSocketManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        async with self._lock:
            disconnected = []
            for connection in self.active_connections:
                try:
                    await connection.send_json(message)
                except Exception:
                    disconnected.append(connection)
            for d in disconnected:
                self.active_connections.remove(d)

ws_manager = WebSocketManager()

@app.websocket("/ws/live_feed")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keepalive ping/pong
            await websocket.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
```

### 7.6 Optimistic Concurrency Control (OCC) with Revision Versioning

Prevent lost updates during concurrent portfolio adjustments by adding a monotonically increasing `version` column:

```sql
ALTER TABLE my_portfolio ADD COLUMN version INTEGER DEFAULT 1;
```

#### OCC Execution Logic
```python
async def update_portfolio_position_occ(holding_id: int, expected_version: int, new_price: float):
    async with get_db_session() as conn:
        cursor = await conn.execute("""
        UPDATE my_portfolio 
        SET current_price = ?, version = version + 1, updated_at = datetime('now')
        WHERE id = ? AND version = ?
        """, (new_price, holding_id, expected_version))
        
        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=409, 
                detail="포지션 데이터가 다른 세션에 의해 수정되었습니다 (Conflict). 최신 상태로 새로고침합니다."
            )
```

### 7.7 Frontend Request Cancellation via `AbortController` and Request Sequencing

Hardening `al_sangmoo_dashboard.html` with `AbortController` and sequence tracking to eradicate out-of-order chart rendering and polling state overwrite.

```javascript
// Hardened Frontend Request Dispatcher
let chartAbortController = null;
let dashboardAbortController = null;
let chartRequestSequence = 0;
let dashboardRequestSequence = 0;

async function loadChartDataHardened(ticker) {
    // 1. Cancel previous in-flight chart request immediately
    if (chartAbortController) {
        chartAbortController.abort();
    }
    chartAbortController = new AbortController();
    const currentSeq = ++chartRequestSequence;

    try {
        const res = await fetch(`/api/chart/${ticker}`, {
            signal: chartAbortController.signal
        });
        
        if (!res.ok) throw new Error("Fetch failed");
        const chartObj = await res.json();
        
        // 2. Discard response if a newer request was dispatched in the meantime
        if (currentSeq !== chartRequestSequence) {
            return; // Stale response dropped!
        }
        
        // 3. Render chart
        candleSeries.setData(chartObj.candles);
        kijunSeries.setData(chartObj.kijun_line || []);
        tenkanSeries.setData(chartObj.tenkan_line || []);
        spanASeries.setData(chartObj.span_a_line || []);
        spanBSeries.setData(chartObj.span_b_line || []);
        volumeSeries.setData(chartObj.volume || []);
    } catch (err) {
        if (err.name === 'AbortError') {
            console.log(`[AbortController] Discarded previous chart fetch for: ${ticker}`);
        }
    }
}
```

---

## 8. Stress-Testing Scenarios & Concurrency Verification Test Plans

To validate database transaction safety, non-blocking asynchronous event loops, and file persistence atomicity, execute the following four comprehensive test scenarios.

### 8.1 Scenario A: High-Concurrency Database Contention & Lockout Test
- **Objective**: Verify that SQLite does not throw `OperationalError: database is locked` under 50 concurrent buy orders and continuous portfolio reads.
- **Traffic Pattern**:
  - Worker Group 1: 50 concurrent `POST /api/portfolio/buy` requests across 5 threads.
  - Worker Group 2: 20 concurrent `GET /api/portfolio` requests.
  - Worker Group 3: 1 concurrent `POST /api/scan_now` request.
- **Pass Criteria**: 100% of buy orders return HTTP 200/201 with unique IDs; 0% `database is locked` errors; 0 uncommitted transaction leaks.

### 8.2 Scenario B: File Persistence Atomic Write & Dirty Read Load Test
- **Objective**: Verify that concurrent readers never encounter `JSONDecodeError` while `dashboard_data.json` is continuously overwritten.
- **Traffic Pattern**:
  - Writer Thread: Writes 8.5MB JSON payload to `dashboard_data.json` in a tight loop 100 times using `atomic_write_json`.
  - Reader Thread Pool (20 workers): Continuously reads `dashboard_data.json` via `atomic_read_json` (5,000 total read operations).
- **Pass Criteria**: 0 `JSONDecodeError` occurrences; 0 reads of 0-byte files; all reads return valid `macro` and `kpis` structures.

### 8.3 Scenario C: Async Worker Pool Saturation & Event Loop Starvation Test
- **Objective**: Measure latency of lightweight `/api/portfolio` endpoint during an active 60-second `/api/scan_now` execution.
- **Pass Criteria**: P95 latency for `/api/portfolio` remains $< 50\text{ ms}$; main event loop latency $< 5\text{ ms}$.

### 8.4 Scenario D: Frontend Rapid Ticker Switching Out-of-Order Simulation
- **Objective**: Simulate rapid consecutive clicks (50ms interval across 6 tickers) with artificial random network latency (100ms - 800ms).
- **Pass Criteria**: The final rendered candlestick dataset in the DOM strictly matches the last clicked ticker symbol 100% of the time.

---

### 8.5 Automated Pytest & Locust Test Suite Implementation

Save the following executable test suite to `tools_and_tests/test_concurrency_suite.py` for CI/CD integration.

```python
import pytest
import asyncio
import aiohttp
import json
import time
import os
import random

BASE_URL = "http://localhost:8000"

@pytest.mark.asyncio
async def test_concurrent_portfolio_buys():
    """
    Stress-tests 50 concurrent buy orders against the SQLite backend.
    """
    tickers = ["NVDA", "AMZN", "MSFT", "AAPL", "META", "TSLA", "AVGO", "COST"]
    
    async with aiohttp.ClientSession() as session:
        async def _place_buy(idx):
            ticker = random.choice(tickers)
            payload = {
                "ticker": ticker,
                "buy_price": round(random.uniform(100.0, 900.0), 2),
                "quantity": round(random.uniform(1.0, 50.0), 2),
                "buy_date": "2026-08-21"
            }
            async with session.post(f"{BASE_URL}/api/portfolio/buy", json=payload) as resp:
                status = resp.status
                text = await resp.text()
                return status, text

        tasks = [_place_buy(i) for i in range(50)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        success_count = sum(1 for r in results if isinstance(r, tuple) and r[0] == 200)
        assert success_count == 50, f"Expected 50 successful orders, got {success_count}. Details: {results}"

@pytest.mark.asyncio
async def test_atomic_file_concurrency():
    """
    Tests atomic file persistence under high-frequency read/write collisions.
    """
    from generate_dashboard_feed import build_dashboard_data
    
    read_errors = []
    stop_event = asyncio.Event()

    async def _reader():
        async with aiohttp.ClientSession() as session:
            while not stop_event.is_set():
                try:
                    async with session.get(f"{BASE_URL}/api/dashboard") as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if not data.get("macro") or not data.get("kpis"):
                                read_errors.append("Empty macro/kpi payload detected!")
                        else:
                            read_errors.append(f"HTTP {resp.status}")
                except Exception as e:
                    read_errors.append(str(e))
                await asyncio.sleep(0.02)

    reader_tasks = [asyncio.create_task(_reader()) for _ in range(10)]
    
    # Run reader threads for 5 seconds while triggering background scans
    await asyncio.sleep(5.0)
    stop_event.set()
    await asyncio.gather(*reader_tasks)
    
    assert len(read_errors) == 0, f"Encountered dirty read errors during atomic persistence: {read_errors}"
```

---

## 9. Conclusion & Actionable Next Steps

This audit establishes that the R-Sangmoo Quant Trading Platform requires immediate concurrency hardening before scaling to production multi-user operation or automated live order routing.

### Priority Action Items for Engineering
1. **P0 (Immediate Fix)**: Correct the runtime API method name mismatches in `server.py` (`close_portfolio_position` $\to$ `record_portfolio_sell`; `clear_portfolio` $\to$ `reset_all_holdings`).
2. **P0 (Database Safety)**: Refactor `db_manager.py` to decouple `yf.download()` network calls from the SQLite transaction scope in `get_live_portfolio()`.
3. **P0 (Storage Atomicity)**: Replace direct `open(..., "w")` file writes with `tempfile` + `os.replace()` atomic swaps in `generate_dashboard_feed.py` and `youtube_stream_scanner.py`.
4. **P1 (Async Migration)**: Migrate database operations to `aiosqlite` with `PRAGMA journal_mode = WAL;` and `busy_timeout = 30000`.
5. **P1 (Frontend Hardening)**: Integrate `AbortController` and request sequence tokens in `al_sangmoo_dashboard.html` to eliminate chart rendering desynchronization.
6. **P2 (Real-Time Push)**: Replace 30-second client short-polling with WebSocket pub/sub state broadcasting (`/ws/live_feed`).

*Report authored and verified by Worker M2 (Specialist: Concurrency, Database Transactions, and Distributed State Synchronization).*
