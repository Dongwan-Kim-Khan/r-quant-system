# Al-Sangmoo Quant Terminal: Domain 5 Audit Report
## API Calling Robustness, Network Resilience & Error Handling Deep-Dive

- **Auditor**: API Robustness & Resilience Auditor (Explorer 5)
- **Target System**: Al-Sangmoo Quant Terminal (Python Backend / KIS Broker / yfinance / YouTube)
- **Workspace Root**: `d:\코딩\R`
- **Audit Date**: 2026-08-25
- **Mode**: STRICTLY READ-ONLY AUDIT

---

## 1. Executive Summary & Domain Verdict

| Metric | Assessment |
|---|---|
| **Domain 5 Health Score** | **68 / 100** (Needs Structural Hardening) |
| **Critical Severity Issues** | 2 |
| **High Severity Issues** | 3 |
| **Medium Severity Issues** | 3 |
| **Low Severity Issues** | 1 |
| **Primary Risk Vectors** | KIS TPS limit violations under load, non-atomic broker/DB dual-write hazard, concurrent OAuth token renewal collisions (`EGW00133`), unthrottled Yahoo Finance multi-thread bursts, and lack of trading calendar / holiday awareness in 24/7 background daemons. |

### Domain Verdict
The Al-Sangmoo Quant Terminal integrates sophisticated quantitative models (Ichimoku 3-Tier engine, MSI 2.0 macro climate index) and real broker interfaces (Korea Investment & Securities REST OpenAPI, SQLite SSOT). However, the external API calling architecture lacks resilient distributed systems patterns such as **Client-Side Token Bucket Rate Limiting**, **Exponential Backoff with Full Jitter**, **Circuit Breakers**, **Two-Phase Idempotency Tokens**, and **Market Calendar Filtering**. 

Under high-volatility live trading conditions, the current implementation risks:
1. **API Lockout**: Triggering KIS OpenAPI rate limits (`EGW00201`) or duplicate token errors (`EGW00133`), resulting in dropped stop-loss orders.
2. **Financial Desync**: Placing real-money broker orders that succeed on KIS but crash on local SQLite commits, inducing double-buy attempts.
3. **Data Starvation**: Getting throttled by Yahoo Finance (HTTP 429) during 60-ticker parallel batch scans, causing incomplete quant matrices.

---

## 2. Comprehensive Issue Matrix

| Issue ID | Severity | Category | Target File(s) & Lines | Title |
|---|---|---|---|---|
| **API-01** | **CRITICAL** | Rate Limiting / TR Handling | `al_sangmoo/infrastructure/brokers/kis_broker.py:272-340, 354-385, 472-543` | Missing Client-Side Rate Limiter & Token Throttling in KIS OpenAPI Gateway |
| **API-02** | **CRITICAL** | Transaction Atomicity | `al_sangmoo/interfaces/api/routers/portfolio.py:101-171, 173-237` | Non-Atomic Dual-Write Race Hazard Between KIS Broker Execution & SQLite Persistence |
| **API-03** | **HIGH** | OAuth Token Lifecycle | `al_sangmoo/infrastructure/brokers/kis_broker.py:140-177` | Concurrency Race Hazard on KIS OAuth Token Renewal (`EGW00133` Lockout) & Missing 401 Re-auth Hook |
| **API-04** | **HIGH** | External API Resilience | `generate_dashboard_feed.py:77-103, 272-278`, `al_sangmoo_daily_bot.py:138-144` | Uncontrolled Parallel `yfinance` Thread Bursts Causing HTTP 429 Rate Limiting & MultiIndex Desync |
| **API-05** | **HIGH** | Market Calendar & Failover | `al_sangmoo/domain/risk/portfolio_guardian.py:74-98`, `al_sangmoo/domain/risk/autopilot_trader.py:82-92` | 24/7 Polling Daemon Lacking Trading Calendar & Holiday Awareness |
| **API-06** | **MEDIUM** | Timeout Protections | `al_sangmoo_distill/batch_download_50_lives.py:84`, `tools_and_tests/*.py`, `yfinance` calls | Indefinite Socket Hang Risk Due to Missing Connection/Read Timeouts |
| **API-07** | **MEDIUM** | Error Schemas | `server.py:130-147`, `al_sangmoo/interfaces/api/routers/*.py` | Fragmented API Error Response Schemas & Missing Structured Error Contracts |
| **API-08** | **MEDIUM** | Outage Masking | `al_sangmoo/infrastructure/brokers/kis_broker.py:217-230`, `al_sangmoo/domain/reconciliation.py:36-50` | Simulated Fallback Masking Production Outages in `get_account_balance` |
| **API-09** | **LOW** | Logging / Traceability | `al_sangmoo/infrastructure/brokers/kis_broker.py:382-384`, `persistence.py:355-356` | Silent Error Swallowing in Auxiliary Price Inquiries |

---

## 3. Deep-Dive Audit Findings & Remediation

---

### [API-01] Missing Client-Side Rate Limiter & Token Throttling in KIS OpenAPI Gateway
- **Severity**: **CRITICAL**
- **Impact Area**: Core Order Execution & Live Balance Inquiries
- **File**: `al_sangmoo/infrastructure/brokers/kis_broker.py`
- **Lines**: 272–340 (`get_overseas_balance`), 354–385 (`get_live_price`), 472–543 (`_place_overseas_order`)

#### Problematic Code Snippet
```python
# kis_broker.py: lines 272-280
for ex in target_exchanges: # Loops through ["NASD", "NYSE", "AMEX"]
    for attempt in range(2):
        try:
            res = requests.get(url, headers=headers, params=params, timeout=12)
            if res.status_code == 200:
                data = res.json()
                if data.get("msg_cd") == "EGW00201" and attempt == 0:
                    time.sleep(0.35)
                    continue
                # ...
```
```python
# kis_broker.py: lines 367-376
for excd in ["NYS", "NAS", "AMS"]:
    params = {"AUTH": "", "EXCD": excd, "SYMB": ticker.upper()}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=5)
        # No delay, no retry, no rate limit
```
```python
# kis_broker.py: lines 515-539
res = requests.post(url, json=body, headers=headers, timeout=12)
data = res.json()
if res.status_code == 200 and data.get("rt_cd") == "0":
    # Success
else:
    # Immediately fails without backoff on EGW00201
    msg = data.get("msg1", res.text)
    code = data.get("msg_cd", str(res.status_code))
    return {"status": "rejected", "code": code, "reason": msg}
```

#### Failure Scenario & System Resilience Risk
1. **TPS Limit Exceeded (`EGW00201`)**: Korea Investment & Securities (KIS) enforces a strict rate limit of **2 to 5 TPS (Transactions Per Second)** for Overseas Trading / Virtual Sandbox (VPS) and 20 TPS for Domestic Production.
2. In `get_overseas_balance()`, querying 3 exchanges in rapid succession creates a 3-TR burst. When `PortfolioGuardian` polls live prices across 5 portfolio positions via `get_live_price()`, it fires 15 TR requests within ~100ms.
3. KIS immediately responds with `msg_cd: "EGW00201"` (초당 처리건수를 초과하였습니다).
4. In `_place_overseas_order()`, there is **zero** retry logic. If a stop-loss sell order is fired while background price polling is occurring, the order hits `EGW00201` and is immediately marked `"rejected"`. The stop-loss is dropped, exposing the trader to unbounded downside risk.

#### Concrete Remediation Architecture
1. **Token Bucket / Leaky Bucket Rate Limiter**: Implement a thread-safe `TokenBucketLimiter` set to max 4.0 TPS with a 0.25s minimum inter-request spacing for overseas KIS endpoints.
2. **Exponential Backoff with Full Jitter**: Wrap all KIS HTTP requests in a decorator using randomized backoff:
   $$\text{sleep} = \min(\text{max\_backoff}, \text{base} \times 2^{\text{attempt}}) \times \text{Uniform}(0.5, 1.5)$$
3. **Dedicated Order Channel**: Separate market data inquiry TRs from trade execution TRs to ensure order placement never gets starved by background price polling.

```python
# Remediation Blueprint: TokenBucket & Resilient Decorator
import time
import threading
import random
from functools import wraps

class KISTokenBucket:
    def __init__(self, rate: float = 4.0, capacity: float = 4.0):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_time = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_time
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_time = now
            if self.tokens < 1.0:
                wait_time = (1.0 - self.tokens) / self.rate
                time.sleep(wait_time)
                self.tokens = 0.0
                self.last_time = time.monotonic()
            else:
                self.tokens -= 1.0

kis_limiter = KISTokenBucket(rate=3.5, capacity=3.5)

def kis_retry(max_attempts=3, base_delay=0.4, max_delay=3.0):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                kis_limiter.acquire()
                try:
                    res = func(*args, **kwargs)
                    # Check KIS TPS error in response
                    if isinstance(res, dict) and res.get("code") == "EGW00201":
                        raise requests.exceptions.RequestException("KIS TPS Exceeded (EGW00201)")
                    return res
                except Exception as exc:
                    if attempt == max_attempts - 1:
                        raise
                    jittered_delay = min(max_delay, base_delay * (2 ** attempt)) * random.uniform(0.8, 1.2)
                    time.sleep(jittered_delay)
            return func(*args, **kwargs)
        return wrapper
    return decorator
```

---

### [API-02] Non-Atomic Dual-Write Race Hazard Between KIS Broker Execution & SQLite Persistence
- **Severity**: **CRITICAL**
- **Impact Area**: Portfolio Balance Integrity & Financial Execution
- **File**: `al_sangmoo/interfaces/api/routers/portfolio.py`
- **Lines**: 101–171 (`buy_stock`), 173–237 (`sell_stock`)

#### Problematic Code Snippet
```python
# portfolio.py: lines 134-156
# 1. External Broker Order is submitted FIRST over the wire
broker_res = default_kis_broker.place_order(
    ticker=ticker_clean,
    side="BUY",
    qty=qty,
    price=price,
    order_type="00",
    exchange=order.exchange or "NASD"
)
if broker_res.get("status") not in ("submitted", "filled"):
    reason = broker_res.get("reason", "증권사 주문 전송 실패")
    raise HTTPException(status_code=400, detail=f"증권사 주문 거부: {reason}")

order_id = broker_res.get("order_id")
broker_msg = broker_res.get("message")

# 2. Local SQLite DB is updated SECOND
# If SQLite is locked by Guardian or disk I/O throws OperationalError,
# an unhandled 500 exception is raised here!
inserted_id = db_manager.add_portfolio_buy(
    ticker=ticker_clean,
    buy_price=price,
    quantity=float(qty),
    buy_date=order.buy_date
)
```

#### Failure Scenario & System Resilience Risk
1. **Partial Failure Scenario**:
   - User or AutoPilot submits a Buy order for 50 shares of NVDA ($6,000).
   - Step 1 executes: KIS OpenAPI receives and FILLS the buy order on the Nasdaq exchange.
   - Step 2 executes: A concurrent background process (e.g., Guardian daemon or scanner) holds an active SQLite write lock, causing `db_manager.add_portfolio_buy()` to time out (`sqlite3.OperationalError: database is locked`).
   - The FastAPI endpoint aborts and returns HTTP 500 ("An internal server error occurred").
2. **Disaster Cascade**:
   - The client/user perceives the order as failed and clicks "Buy" again.
   - A second order is executed on KIS, doubling the capital commitment and violating position sizing rules.
   - Meanwhile, the first executed trade was never recorded in `my_portfolio`, so `PortfolioGuardian` will NEVER monitor it for stop-loss or take-profit protection!

#### Concrete Remediation Architecture
1. **Outbox / Pre-Allocation Pattern**:
   - Stage 1: Insert an intent record into SQLite with `status = 'PENDING_BROKER_SUBMISSION'`.
   - Stage 2: Submit order to KIS broker with idempotent client order ID (`clOrdId` / `MGCO_APTM_ODNO`).
   - Stage 3: Upon broker confirmation, transition record to `status = 'HOLDING'`.
   - If Stage 2 or 3 fails, the system logs the exact broker order ID and leaves the pending record for the reconciliation engine to resolve, preventing ghost executions.

---

### [API-03] Concurrency Race Hazard on KIS OAuth Token Renewal (`EGW00133` Lockout) & Missing 401 Re-auth Hook
- **Severity**: **HIGH**
- **Impact Area**: Authentication Gateway & All KIS API Integrations
- **File**: `al_sangmoo/infrastructure/brokers/kis_broker.py`
- **Lines**: 100–177 (`_load_cached_token`, `authenticate`)

#### Problematic Code Snippet
```python
# kis_broker.py: lines 140-176
def authenticate(self, force_refresh: bool = False) -> bool:
    if not self.is_configured():
        self.token = "MOCK_KIS_SESSION_TOKEN"
        self.token_expiry = time.time() + 86400
        return True

    # No mutex or thread-locking protecting token refresh!
    if not force_refresh and self.token and (time.time() < self.token_expiry - 300):
        return True

    url = f"{self.base_url}/oauth2/tokenP"
    payload = {
        "grant_type": "client_credentials",
        "appkey": self.app_key,
        "appsecret": self.app_secret
    }
    headers = {"Content-Type": "application/json; charset=UTF-8"}

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            token = data.get("access_token")
            expires_in = int(data.get("expires_in", 86400))
            expired_str = data.get("access_token_token_expired")
            self._save_cached_token(token, expires_in, expired_str)
            return True
        else:
            logger.error(f"[KIS Auth Error] {res.status_code}: {res.text}")
            return False
    except Exception as exc:
        logger.error(f"[KIS Auth Exception] {exc}")
        return False
```

#### Failure Scenario & System Resilience Risk
1. **Concurrent Token Request Collision (`EGW00133`)**:
   - KIS restricts OAuth token requests to a maximum of 1 issuance per minute per account. Issuing another token within 60 seconds returns `EGW00133` ("동일한 계정의 유효한 토큰이 이미 발급되었습니다").
   - When a token expires after 24 hours during an active trading session, multiple concurrent coroutines (`PortfolioGuardian`, `AutoPilotTrader`, and incoming HTTP requests to `/api/dashboard`) simultaneously enter `authenticate()`.
   - Without an async/threading lock, all tasks send POST requests to `/oauth2/tokenP`. KIS rejects all subsequent requests with `EGW00133`, causing authentication to fail across the entire system.
2. **Silent Expiry & Lack of 401 Interception**:
   - If KIS invalidates a token prematurely (e.g. server reset or clock drift), `kis_broker.py` will not know until `self.token_expiry` lapses.
   - TR requests return HTTP 401 or `EGW00123`. None of the TR methods (`get_overseas_balance`, `place_order`, `get_live_price`) catch 401 to trigger `authenticate(force_refresh=True)` and retry. All TRs fail until the 24-hour timer expires!

#### Concrete Remediation Architecture
1. **Re-Entrant Mutex on Token Refresh**: Protect `authenticate()` with `threading.RLock()` and double-checked locking (check cache again after acquiring lock before hitting network).
2. **Session-Level 401 / Auth Error Interceptor**: Create a custom `requests.Session` hook or wrapper that intercepts 401 responses or KIS auth error codes, automatically forces a token refresh, and retries the original request once.

---

### [API-04] Uncontrolled Parallel `yfinance` Thread Bursts Causing HTTP 429 Rate Limiting & MultiIndex Desync
- **Severity**: **HIGH**
- **Impact Area**: Market Data Ingestion & Quantitative Scanners
- **File**: `generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, `al_sangmoo/infrastructure/persistence.py`
- **Lines**: `generate_dashboard_feed.py:77-103, 272-278`, `al_sangmoo_daily_bot.py:138-144`, `persistence.py:381-385`

#### Problematic Code Snippet
```python
# generate_dashboard_feed.py: lines 272-274
# Fast Parallel Batch Computation for 60 tickers
with ThreadPoolExecutor(max_workers=12) as executor:
    results = list(executor.map(compute_all_indicators, WATCHLIST))
```
```python
# generate_dashboard_feed.py: lines 94-103
def compute_all_indicators(ticker):
    try:
        df = yf.download(ticker, period="3y", interval="1d", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            if 'Close' in df.columns.get_level_values(0):
                df.columns = df.columns.get_level_values(0)
            elif 'Close' in df.columns.get_level_values(1):
                df.columns = df.columns.get_level_values(1)
        # ...
        if len(df) < 60:
            return None
    except Exception as e:
        print(f"Error computing {ticker}: {e}")
        return None
```
```python
# al_sangmoo_daily_bot.py: lines 141-143 (Fragile MultiIndex extraction)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)  # Bug if Price is in level 1
```

#### Failure Scenario & System Resilience Risk
1. **Yahoo Finance IP Throttling (HTTP 429)**:
   - Blasting Yahoo Finance with 12 simultaneous threads downloading 3 years of daily OHLCV data for 60 symbols triggers Yahoo's anti-scraping / rate-limit threshold.
   - Yahoo Finance drops connections or returns empty responses (`df.empty`).
   - In `compute_all_indicators()`, any failure immediately returns `None`. Tickers silently disappear from the dashboard feed and 3-Tier ranking without logging the underlying HTTP code.
2. **MultiIndex Column Parsing Inconsistency**:
   - `yfinance` >= 0.2.35 changed DataFrame column index schemas depending on single vs multi-ticker downloads.
   - `al_sangmoo_daily_bot.py:141` blindly executes `df.columns = df.columns.get_level_values(0)`. If `yfinance` formats columns as `('NVDA', 'Close')` (ticker at level 0, metric at level 1), `df.columns` becomes `['NVDA', 'NVDA', ...]`.
   - The subsequent `df['Close']` raises `KeyError: 'Close'`, causing the entire scanner iteration to abort for that ticker.

#### Concrete Remediation Architecture
1. **Bounded Concurrency with Staggered Jitter**: Reduce `max_workers` from 12 to 4 and add a 50ms–150ms randomized inter-request delay.
2. **Canonical MultiIndex Normalizer**: Centralize a robust DataFrame column unwrapper in `al_sangmoo.domain.quant.ichimoku` that dynamically checks both level 0 and level 1 for standard price column names (`Close`, `Open`, `High`, `Low`, `Volume`).
3. **Local Stale Cache Fallback**: If `yf.download` fails or returns HTTP 429, fall back to `data/charts/{ticker}.json` cached history instead of returning `None`.

---

### [API-05] 24/7 Polling Daemon Lacking Trading Calendar & Holiday Awareness
- **Severity**: **HIGH**
- **Impact Area**: Portfolio Guardian & AutoPilot Schedulers
- **File**: `al_sangmoo/domain/risk/portfolio_guardian.py:74-98`, `al_sangmoo/domain/risk/autopilot_trader.py:82-92`
- **Lines**: `portfolio_guardian.py:31, 74-98`, `autopilot_trader.py:82-92`

#### Problematic Code Snippet
```python
# portfolio_guardian.py: lines 74-85
async def _monitor_loop(self):
    """Main async daemon polling loop."""
    while self.is_running:
        try:
            await self.check_and_execute_guardian_rules()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[Portfolio Guardian Error] {e}", exc_info=True)

        await asyncio.sleep(self.interval)  # Runs every 10 seconds 24/7/365!
```
```python
# autopilot_trader.py: lines 82-88
now = datetime.now()
# Run scheduled check during US market hours (22:30 ~ 06:00 KST)
is_market_hours = (now.hour >= 22 or now.hour < 6)
# Does NOT check weekday (now.weekday() < 5) or US Market Holidays!
```

#### Failure Scenario & System Resilience Risk
1. **Weekend & Holiday Spurious Orders**:
   - `PortfolioGuardian` polls KIS OpenAPI TR `HHDFS00000300` every 10 seconds on Saturdays, Sundays, and market holidays (e.g., Labor Day, Thanksgiving, Christmas).
   - During weekends, KIS servers are down for maintenance or return `EGW00205` ("장운영시간이 아닙니다").
   - Live price fetch fails, falling back to stale DB prices or yfinance. If a position was left near stop-loss from Friday's close, the guardian attempts to place real sell orders into a closed exchange, accumulating hundreds of failed order rejection logs and spamming WebSocket clients with error broadcasts.
2. **Daylight Saving Time (DST) Desync**:
   - Hardcoded `22:30 ~ 06:00 KST` is only valid for Standard Time (EST). During Eastern Daylight Time (EDT - March to November), US markets open at `21:30 KST`. The bot misses the first full hour of market open action!

#### Concrete Remediation Architecture
1. **Market Hours & Holiday Calendar Provider**: Implement an institutional market session validator (`is_us_market_open()`) with DST calculation and NYSE/Nasdaq holiday calendar detection.
2. **Adaptive Polling Rates**: Dynamically adjust `check_interval_seconds`:
   - During Active Market Hours: **10s**
   - Pre/Post Market: **60s**
   - Market Closed / Weekends: **300s** (Low-frequency standby, zero broker TR calls).

---

### [API-06] Indefinite Socket Hang Risk Due to Missing Connection/Read Timeouts
- **Severity**: **MEDIUM**
- **Impact Area**: Subprocess Scrapers, Standard HTTP Requests
- **File**: `al_sangmoo_distill/batch_download_50_lives.py:84`, `tools_and_tests/*.py`, `yfinance` requests
- **Lines**: `batch_download_50_lives.py:84`, `tools_and_tests/scrape_rsangmoo_lives.py:20`

#### Problematic Code Snippet
```python
# batch_download_50_lives.py: line 84
res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
# Missing timeout parameter!
```
```python
# tools_and_tests/check_runs.py: lines 12-13
with urllib.request.urlopen(req) as res:
    # Missing timeout parameter! Default socket timeout is None (infinite).
```

#### Failure Scenario & System Resilience Risk
- If an external process or network stream hangs (e.g. YouTube stream throttling or half-open TCP socket), `subprocess.run()` without `timeout=` or `urllib.request.urlopen()` without `timeout=` will block indefinitely.
- The thread becomes permanently deadlocked, consuming memory and thread handles.

#### Concrete Remediation Architecture
- Enforce explicit two-tuple timeouts `timeout=(connect_timeout, read_timeout)` (e.g. `timeout=(3.05, 15.0)`) on all HTTP requests and `timeout=30` on all `subprocess.run` invocations.

---

### [API-07] Fragmented API Error Response Schemas & Missing Structured Error Contracts
- **Severity**: **MEDIUM**
- **Impact Area**: Frontend-Backend Contract & Error Diagnostics
- **File**: `server.py:130-147`, `al_sangmoo/interfaces/api/routers/*.py`
- **Lines**: `server.py:130-147`, `portfolio.py:67, 131, 144, 210`

#### Problematic Code Snippet
```python
# server.py: lines 130-147
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    if isinstance(exc, RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": exc.errors()})
    return JSONResponse(status_code=500, content={"status": "error", "message": "An internal server error occurred"})
```
```python
# In various routers:
# portfolio.py raises HTTPException with string detail:
raise HTTPException(status_code=400, detail=f"사전 리스크 한도 초과: {eval_res['reason']}")

# broker.py returns dictionary:
return {"status": "rejected", "code": code, "reason": msg}

# kis_broker.py returns:
return {"status": "error", "message": str(exc)}
```

#### Failure Scenario & System Resilience Risk
- Frontend client JS (`terminal.js`, `al_sangmoo_dashboard.html`) must write fragmented conditional branches to extract error messages: `const msg = res.message || res.detail || res.reason || res.error || 'Unknown error'`.
- If an endpoint returns `detail` as a list of dictionaries (from `RequestValidationError`), standard toast notification crashes with `[object Object]`.

#### Concrete Remediation Architecture
- Standardize all API error responses to a canonical JSON schema:
```json
{
  "success": false,
  "error": {
    "code": "RISK_GUARDRAIL_VIOLATION",
    "message": "Maximum single asset concentration limit (25%) exceeded.",
    "details": {},
    "timestamp": "2026-08-25T17:58:00Z"
  }
}
```

---

### [API-08] Simulated Fallback Masking Production Outages in `get_account_balance`
- **Severity**: **MEDIUM**
- **Impact Area**: Health Monitoring & Disaster Detection
- **File**: `al_sangmoo/infrastructure/brokers/kis_broker.py:217-230`, `al_sangmoo/domain/reconciliation.py:36-50`
- **Lines**: `kis_broker.py:217-230`

#### Problematic Code Snippet
```python
# kis_broker.py: lines 217-230
# Resilient fallback if broker returns temporary gateway error
return {
    "status": "success",
    "mode": "SIMULATED",
    "total_equity": 100_000.0,
    "total_equity_usd": 100_000.0,
    "cash_available": 100_000.0,
    "cash_available_usd": 100_000.0,
    "stock_evaluation": 0.0,
    "stock_eval_usd": 0.0,
    "holdings": [],
    "raw_output": {},
    "broker_error": res.get("message")
}
```

#### Failure Scenario & System Resilience Risk
- When KIS OpenAPI fails in Real Production mode (`mode="prod"`), `get_account_balance()` returns `status: "success"` with a fake $100,000 equity and empty holdings list.
- A health check or monitoring daemon checking `res.get("status") == "success"` will falsely report the system as 100% operational when in fact real live account balance access is completely severed!
- In `reconciliation.py`, if not carefully guarded, comparing 0 holdings to local holdings could trigger erroneous reconciliation states.

#### Concrete Remediation Architecture
- Separate fallback states explicitly: Return `status: "degraded"` or `status: "error"` with `is_fallback: True`. Never report an outage as `status: "success"` in production environments.

---

## 4. Synthesis Across All 6 Focus Areas

### Focus Area 1: KIS OpenAPI TR Handling & Rate Limiting
- **Status**: Vulnerable (No TPS rate limiter, single-attempt fixed sleep).
- **Key Finding**: KIS 2-5 TPS limit can be breached easily during simultaneous quote checks and order placements.
- **Action**: Introduce `TokenBucketLimiter` (3.5 TPS cap) and exponential backoff with full jitter on `EGW00201`.

### Focus Area 2: KIS OAuth Token Lifecycle
- **Status**: Vulnerable to race condition (`EGW00133`).
- **Key Finding**: Unprotected multi-thread calls to `authenticate()` can trigger KIS 1-minute duplicate token block.
- **Action**: Add `threading.RLock()` / `asyncio.Lock()` and double-checked token validation. Implement 401 interception.

### Focus Area 3: External API Resilience (yfinance, YouTube)
- **Status**: Partially Resilient (fallback cache exists for YouTube, but yfinance bursts cause 429s).
- **Key Finding**: 12 parallel threads for `yf.download` cause Yahoo Finance IP blocks. MultiIndex column structure can desync.
- **Action**: Lower concurrency to 4 workers with jittered delays; implement canonical column normalizer.

### Focus Area 4: Timeout Protections
- **Status**: Mostly Compliant, Minor Gaps.
- **Key Finding**: Core broker calls have timeouts (5s–12s), but helper scripts and standard library `urllib` calls omit timeouts.
- **Action**: Enforce `(connect_timeout, read_timeout)` tuples across all network I/O.

### Focus Area 5: Failover & Market Closure Behavior
- **Status**: Deficient (Daemons unaware of market hours/holidays).
- **Key Finding**: Guardian polls KIS live price TR 24/7/365, spamming error logs on weekends and holidays.
- **Action**: Implement NYSE/Nasdaq calendar provider with adaptive polling intervals (10s in market, 300s off-market).

### Focus Area 6: Global Exception Handling & Error Schemas
- **Status**: Good Security Headers, Fragmented Error Schemas.
- **Key Finding**: Global 500 handler prevents raw traceback leaks, but error JSON schemas across routes are inconsistent.
- **Action**: Standardize on a unified error response envelope (`{"success": false, "error": {...}}`).

---

## 5. Prioritized Remediation Roadmap

```
Phase 1 (Immediate / Critical):
├── [API-01] Implement TokenBucket Rate Limiter (3.5 TPS) in kis_broker.py
├── [API-02] Refactor portfolio order execution to Pre-Allocation / Outbox pattern
└── [API-03] Add Re-entrant Lock to kis_broker.authenticate() to prevent EGW00133 collisions

Phase 2 (High Priority / Resilience):
├── [API-04] Throttle yfinance ThreadPool to 4 workers and harden MultiIndex normalization
├── [API-05] Add US Market Calendar & Holiday Validator to Portfolio Guardian and AutoPilot
└── [API-08] Refactor get_account_balance fallback to return status="degraded" rather than "success"

Phase 3 (Medium Priority / Polish):
├── [API-06] Add explicit timeout tuples to all network and subprocess invocations
└── [API-07] Standardize API error response schemas across all FastAPI routers
```
