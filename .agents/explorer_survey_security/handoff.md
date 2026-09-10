# Security & Broker Gateway Resilience Survey Report (Phase 5 Deep Dive)

- **Agent**: `explorer_survey_security`
- **Milestone**: Phase 5 End-to-End Audit & Remediation Survey
- **Scope**: Requirement 1 (Security & Credential Protection) & Requirement 2 (API Calling & Broker Gateway Resilience)
- **Target Files**:
  - `server.py`
  - `al_sangmoo/core/config.py`
  - `al_sangmoo/core/constants.py`
  - `al_sangmoo/interfaces/api/routers/*.py` (`broker.py`, `portfolio.py`, `autopilot.py`, `charts.py`, `dashboard.py`, `guardian.py`, `scanner.py`)
  - `al_sangmoo/infrastructure/brokers/*.py` (`kis_broker.py`, `paper_broker.py`)
  - `al_sangmoo/domain/risk/*.py` (`order_guardrail.py`, `portfolio_guardian.py`, `autopilot_trader.py`)
  - `al_sangmoo/domain/reconciliation.py`
  - `al_sangmoo/api/hub.py`
  - `tools_and_tests/test_phase5_1_security.py`
  - `tools_and_tests/test_phase5_4_kis_modular.py`

---

## 1. Observation

Direct code observations with exact file paths, line numbers, and behavior:

### A. Security & Access Control (R1)
1. **Unauthenticated Critical Mutating Endpoints (CWE-306)**
   - `al_sangmoo/interfaces/api/routers/broker.py:64-133` (`POST /api/broker/order`)
   - `al_sangmoo/interfaces/api/routers/broker.py:56-62` (`POST /api/broker/reconcile`)
   - `al_sangmoo/interfaces/api/routers/portfolio.py:104-180` (`POST /api/portfolio/buy`)
   - `al_sangmoo/interfaces/api/routers/portfolio.py:182-254` (`POST /api/portfolio/sell/{position_id}`)
   - `al_sangmoo/interfaces/api/routers/portfolio.py:256-287` (`POST /api/portfolio/buy_top_pick`)
   - `al_sangmoo/interfaces/api/routers/portfolio.py:290-296` (`POST /api/portfolio/reset`)
   - `al_sangmoo/interfaces/api/routers/autopilot.py:23-31` (`POST /api/autopilot/toggle`)
   - `al_sangmoo/interfaces/api/routers/autopilot.py:34-38` (`POST /api/autopilot/trigger_now`)
   - `al_sangmoo/interfaces/api/routers/guardian.py:19-23` (`POST /api/guardian/toggle`)
   - `al_sangmoo/interfaces/api/routers/guardian.py:25-28` (`POST /api/guardian/check_now`)
   - `al_sangmoo/interfaces/api/routers/scanner.py:69-86` (`POST /api/scan_now`)
   - *Observation*: Zero authentication (no `HTTPBearer`, `APIKeyHeader`, or session validation) is enforced on any of these mutating endpoints. Any local or network client reaching port 8000 can place real-money orders, reset database holdings, or trigger autonomous trading loops.

2. **Missing `json` Module in `server.py` WebSocket Handler (CWE-755)**
   - `server.py:258`: `data = json.loads(msg)` inside `websocket_live_hub`.
   - *Observation*: `json` is not imported at module level in `server.py`. When a client sends a JSON ping frame (e.g. `{"type": "ping"}` or `{"event": "ping"}`), line 258 raises `NameError: name 'json' is not defined`. Because of `except Exception: pass` on line 261, the server silently suppresses the error and fails to send the `"pong"` reply.

3. **WebSocket CSWSH Origin Check Missing Fallback Protection**
   - `server.py:241-244`:
     ```python
     origin = websocket.headers.get("origin")
     if origin and origin not in ALLOWED_ORIGINS:
         await websocket.close(code=1008, reason="Forbidden Origin")
         return
     ```
   - *Observation*: If the client is a script, bot, or proxy without an `Origin` header (`origin is None`), the check is completely skipped and the connection is accepted.

4. **Credential Handling & OAuth Token Persistence**
   - `.env:16-21`: Contains plaintext `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_CANO`, `KIS_ACNT_PRDT_CD`.
   - `.gitignore:1-25`: Properly excludes `.env`, `*.env`, `.kis_token_*.json`, `data/.kis_token_*.json`.
   - `al_sangmoo/infrastructure/brokers/kis_broker.py:149-170`: Saves OAuth tokens to `data/.kis_token_{mode}.json` with non-atomic write (`with open(...) as f: json.dump(...)`). A sudden process termination during dump can corrupt the file.
   - `al_sangmoo/infrastructure/brokers/kis_broker.py:206`: `logger.error(f"[KIS Auth Error] {res.status_code}: {res.text}")` logs verbatim error text which may include sensitive broker headers or request payloads.

5. **Defensive Sanitization & Response Headers (Verified Solid)**
   - `al_sangmoo/interfaces/api/routers/charts.py:26-44`: Enforces regex `^[A-Za-z0-9가-힣.\^=-]{1,15}$` and path containment check `chart_file.startswith(charts_dir_abs + os.sep)`.
   - `al_sangmoo/interfaces/api/routers/portfolio.py:23-75`: Pydantic field validators enforce uppercase ticker format, `YYYY-MM-DD` date regex, and alphanumeric Korean reason regex.
   - `server.py:186-195`: Injects standard OWASP headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-XSS-Protection: 1; mode=block`).
   - `server.py:166-183`: Global exception handler catches all unhandled exceptions and masks internal tracebacks.

---

### B. Broker Gateway & API Resilience (R2)
1. **Broken Mutex Indentation in `/api/broker/order` (Race Condition & Concurrency Flaw)**
   - `al_sangmoo/interfaces/api/routers/broker.py:72-76`:
     ```python
     async with ORDER_MUTEX:
         final_qty = int(order.qty if order.qty is not None else (order.quantity or 1))
         final_price = float(order.price if order.price > 0 else (order.buy_price or 0.0))
         final_side = (order.side or "BUY").upper()

     # 1. Pre-Trade Guardrail Validation (OUTSIDE MUTEX!)
     balance_info = default_kis_broker.get_overseas_balance()
     ...
     result = default_kis_broker.place_order(...)
     ...
     add_portfolio_buy(...)
     ```
   - *Observation*: The `ORDER_MUTEX` context manager exits at line 76 after merely extracting 3 local variables. Pre-trade guardrail checks, KIS order placement, and SQLite database mutations execute concurrently outside any lock.
   - Furthermore, `broker.py` defines its own `ORDER_MUTEX` and `portfolio.py` defines a separate `ORDER_MUTEX`, so concurrent requests to `/api/broker/order` and `/api/portfolio/buy` do not block each other.

2. **Synchronous Blocking HTTP Calls on FastAPI Async Event Loop**
   - `al_sangmoo/infrastructure/brokers/kis_broker.py:40-52`: `TokenBucketLimiter.acquire()` calls `time.sleep()`.
   - `al_sangmoo/infrastructure/brokers/kis_broker.py:197, 315, 450, 531, 642, 721`: Synchronous `requests.get()` and `requests.post()` with timeouts up to 12 seconds.
   - `al_sangmoo/interfaces/api/routers/portfolio.py:123, 139, 212` and `al_sangmoo/interfaces/api/routers/broker.py:78, 95`: Called directly from `async def` route handlers without `asyncio.to_thread()`.
   - *Observation*: Under API throttling (EGW00201 backoff) or broker network latency (1-3s), the single-threaded asyncio event loop is blocked, freezing WebSocket broadcasts, UI chart polling, and server health checks.

3. **Multi-Worker OAuth Token Refresh Race Condition**
   - `al_sangmoo/infrastructure/brokers/kis_broker.py:94, 177`: `self._auth_lock = threading.RLock()`.
   - *Observation*: In a multi-worker deployment (`uvicorn --workers 4`), `threading.RLock()` only synchronizes threads within a single OS process. When the token expires, all worker processes concurrently fire `POST /oauth2/tokenP` to KIS, triggering `EGW00133` (Token generation frequency limit: max 1 request/min).

4. **Network Timeout vs Duplicate Fill Blindness**
   - `al_sangmoo/infrastructure/brokers/kis_broker.py:640-693`:
     When `requests.post(url, timeout=12)` raises `requests.exceptions.Timeout` on attempt 0, it sleeps 0.5s and blindly resubmits the order on attempt 1.
   - *Observation*: If KIS received and executed the order before the network dropped, the client-side retry results in a duplicate order fill (double buy/sell) on the live exchange.

5. **Failure Handshake (Broker Filled vs SQLite Persistence Failed)**
   - `al_sangmoo/interfaces/api/routers/portfolio.py:139-164`: `default_kis_broker.place_order()` is executed first; if filled, `db_manager.add_portfolio_buy()` is executed. If SQLite fails (e.g. database locked or constraint violation), the order is live at KIS, but missing from local portfolio tracking.

6. **Market Data Fallbacks & Pre/Post-Market Awareness (Verified Functional)**
   - `al_sangmoo/infrastructure/brokers/kis_broker.py:424-503`: Queries KIS TR `HHDFS00000300` in production; in VPS/mock mode queries 1-minute pre/post-market candles (`interval="1m", prepost=True`), then `fast_info`, then 5d daily close.
   - `portfolio_guardian.py:163-171`: Includes defensive `getattr(ticker_obj, "fast_info", None)` guarding against yfinance `KeyError: 'currentTradingPeriod'`.

---

## 2. Logic Chain

```
[Observation: Unauthenticated Mutating Routes]
       ↓
[Risk: Anyone on network can issue POST /api/broker/order or POST /api/portfolio/reset]
       ↓
[Remediation Requirement: Introduce API Key / Bearer Auth Header Dependency for all mutating endpoints]

[Observation: Broken Mutex Scope in broker.py & Disjoint Locks across broker.py / portfolio.py]
       ↓
[Risk: Concurrent order requests bypass guardrails, cause double fills, race on SQLite writes]
       ↓
[Remediation Requirement: Unify into a single centralized ExecutionMutex in al_sangmoo/core/ or domain/risk/ and enclose entire order lifecycle]

[Observation: Synchronous requests.post() & time.sleep() inside async route handlers]
       ↓
[Risk: Event loop freezes for 1.2s ~ 12s on KIS rate-limiting or network latency]
       ↓
[Remediation Requirement: Wrap all KIS adapter I/O calls in asyncio.to_thread() or migrate to httpx.AsyncClient]

[Observation: Blind retry on requests timeout during order execution]
       ↓
[Risk: Duplicate live order execution on KIS exchange]
       ↓
[Remediation Requirement: Query execution status TR (TTTS3035R/VTTS3035R) before retrying timeout orders]

[Observation: Missing 'import json' in server.py WebSocket handler]
       ↓
[Risk: NameError on JSON ping frame, silently dropping keepalive pong]
       ↓
[Remediation Requirement: Add 'import json' to top of server.py]
```

---

## 3. Caveats

1. **Broker Credentials in Local Environment**: The `.env` file in the workspace currently contains valid VPS sandbox credentials. In production deployments, secrets should be injected via environment variables or secret managers (e.g. Docker secrets, Vault, AWS Secrets Manager) rather than committed files.
2. **KIS OpenAPI Sandbox Limitations**: KIS VPS (모의투자) environment does not support real-time tick streaming TRs (HHDFS76200200) reliably and enforces a strict 1-minute token issuance rate limit.
3. **Single-Worker vs Multi-Worker**: The current server is run with `uvicorn server:app --workers 1` where in-memory locks work for intra-process concurrency. Multi-worker scaling requires inter-process file locks or Redis.

---

## 4. Conclusion & Proposed Remediations

| ID | Component | Vulnerability / Flaw | Severity | Remediation Strategy |
|---|---|---|---|---|
| **SEC-01** | `server.py`, `routers/*.py` | Missing Authentication on Mutating Endpoints | **HIGH** | Add optional/enforceable `API_KEY` header verification dependency (`X-API-KEY` or Bearer Token) on all mutating `POST`/`PUT`/`DELETE` routes. |
| **SEC-02** | `server.py:258` | Missing `import json` in WebSocket ping handler | **MEDIUM** | Add `import json` to top of `server.py`. |
| **SEC-03** | `server.py:241` | WebSocket CSWSH Bypass when Origin header is absent | **LOW** | Reject WebSocket connections with missing or invalid Origin if Origin verification is enabled. |
| **SEC-04** | `kis_broker.py:166` | Non-atomic OAuth token cache JSON save | **LOW** | Use `atomic_save_json` from `atomic_io.py` to prevent 0-byte corrupt token cache files. |
| **BRK-01** | `broker.py:72-76` | Broken Mutex Scope & Disjoint `ORDER_MUTEX` instances | **HIGH** | Move `ORDER_MUTEX` to a shared singleton in `al_sangmoo/domain/risk/order_guardrail.py` and enclose the entire function body (guardrails, broker execution, SQLite write) in `async with ORDER_MUTEX:`. |
| **BRK-02** | `broker.py`, `portfolio.py` | Synchronous HTTP calls blocking Async Event Loop | **MEDIUM** | Wrap all `default_kis_broker` method invocations in `await asyncio.to_thread(...)` inside async FastAPI routes. |
| **BRK-03** | `kis_broker.py:640-693` | Duplicate order fill risk on network timeout retry | **MEDIUM** | Prior to retrying an order that timed out, query the order book/execution TR to verify whether the order was received by the exchange. |
| **BRK-04** | `kis_broker.py:177` | Multi-worker process token race condition | **LOW** | Add atomic file locking (e.g. `portalocker` or `.lock` file) around token generation. |
| **BRK-05** | `paper_broker.py`, `kis_broker.py` | `KISBrokerAdapter` does not implement `IExecutionGateway` | **LOW** | Align `KISBrokerAdapter` and `PaperTradingBroker` under a unified `IExecutionGateway` interface. |

---

## 5. Verification Method

To independently verify all findings and test suite behavior:

1. **Security Test Suite Verification**:
   ```powershell
   pytest -s tools_and_tests/test_phase5_1_security.py
   ```
   *Expected*: All 10 tests across Tier 1 through Tier 4 pass 100%.

2. **KIS Broker & Modular Gateway Test Suite Verification**:
   ```powershell
   pytest -s tools_and_tests/test_phase5_4_kis_modular.py
   ```
   *Expected*: All 11 unit tests pass 100%.

3. **Verify WebSocket `json` Import Defect**:
   - Inspect `server.py` line 6-15 and line 258.
   - Run Python check:
     ```python
     python -c "import server; print(hasattr(server, 'json'))"
     ```
     *Output*: `False` (confirming `json` is undefined in `server.py` scope).

4. **Verify Mutex Indentation Defect in `broker.py`**:
   - View `al_sangmoo/interfaces/api/routers/broker.py:72-78`.
   - Confirm lines 73-75 are inside `async with ORDER_MUTEX:`, while lines 78-132 are indented outside the lock.
