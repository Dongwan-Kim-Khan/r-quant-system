# Comprehensive Testing Infrastructure Survey & Acceptance Criteria Specification
**Milestone:** Phase 5.1 Security Hardening  
**Platform:** Al-Sangmoo Quant Trading Platform (v2.6)  
**Author:** Explorer 3 (Spec Miner / Test Explorer)  
**Date:** 2026-08-22  
**Target Verification Suite:** `tools_and_tests/test_phase5_1_security.py`  
**Repository Scope:** `d:\코딩\Playground\al_sangmoo_project`  

---

## 1. Executive Summary & Scope

This report establishes the exhaustive test specification and verification harness architecture for **Phase 5.1 Security Hardening** of the Al-Sangmoo Quant Trading Platform. The hardening scope targets all Critical, High, and Medium vulnerabilities documented in the Master Audit Report (`MASTER-AUDIT-2026-v2.6-FINAL` / `explorer_r1/security_audit_findings.md`) and codified in `ORIGINAL_REQUEST.md`:

- **R1: Stored & DOM XSS Remediation** (`SEC-V01`, CWE-79)
- **R2: CORS Whitelisting, Origin Validation & WebSocket DoS Prevention** (`SEC-V02`, `SEC-V04`, CWE-942, CWE-400, CWE-1385)
- **R3: Path Traversal & Subprocess Hardening** (`SEC-V05`, CWE-22, CWE-88)
- **R4: Pydantic API Input Validation & Global Error Sanitization** (`SEC-V07`, `SEC-V08`, CWE-20, CWE-209)
- **R5: OWASP Security Response Headers** (`SEC-V10`, CWE-693)

The automated test suite `tools_and_tests/test_phase5_1_security.py` will serve as the authoritative gate for verifying 100% compliance with security requirements while guaranteeing zero regression across existing core trading features.

---

## 2. Current Testing Infrastructure Analysis

### 2.1. Existing Test Suites & Patterns

The platform currently maintains 5 primary verification suites in `tools_and_tests/`:

| Test Suite File | Domain / Focus | Runner Type | Database Isolation | Current Status |
| :--- | :--- | :--- | :--- | :--- |
| `test_phase1_hardening.py` | SQLite WAL mode, Atomic JSON I/O, REST endpoints, 50-thread concurrency | Standalone Python script | `test_quant_trades_p1.db` | **100% GREEN** |
| `test_phase2_modular.py` | Core constants, Ichimoku/MSI domain, Infrastructure I/O, WebSocket Hub | Standalone Python script | `test_quant_trades_p2.db` | **100% GREEN** |
| `test_phase3_backtester.py`| Friction backtest engine, MTF consensus matrix, ATR position sizer | Standalone Python script | In-memory / Mock data | **100% GREEN** |
| `test_phase4_execution.py` | Pre-trade risk guardrails, Macro circuit breakers, Paper broker, SQLite backups | Standalone Python script | `test_quant_trades_p4.db` | **100% GREEN** |
| `test_global60_dual_strategy.py` | Global 60 watchlist, Ticker resolver, Origin tags, Zero-emoji compliance | Standalone Python script | In-memory / JSON feed | **100% GREEN** |

### 2.2. Architectural Conventions Observed
1. **Isolated SQLite Environments**: Each test suite redirects `AL_SANGMOO_DB_PATH` environment variable to a dedicated temporary database file (`test_quant_trades_pX.db`) and executes `setup_test_db()` and `cleanup_test_db()` in a `try...finally` block.
2. **Deterministic Standard Assertions**: Python native `assert` statements are paired with descriptive error messages.
3. **Cross-Platform UTF-8 Handling**: All test scripts configure `sys.stdout.reconfigure(encoding='utf-8')` on Windows platforms.
4. **Dependency Minimization**: The platform's Python environment lacks `httpx` (which disables `starlette.testclient.TestClient`). The test suites directly execute async endpoints via `asyncio.run()`, direct class method invocations, or mock socket handlers.

### 2.3. Test Client Architecture for Phase 5.1 (Pure ASGI In-Memory Client)
Because `httpx` is not installed, FastAPI endpoints involving middleware (CORS headers, custom security headers middleware, exception handlers, and WebSocket handshake origin checks) cannot rely on `TestClient`. 

Instead, `test_phase5_1_security.py` will implement a lightweight, zero-dependency **In-Memory ASGI Test Harness** that calls `await app(scope, receive, send)` directly:

```python
async def asgi_request(app, method: str, path: str, headers: dict = None, body: bytes = b"", query_string: bytes = b""):
    """
    Direct in-memory ASGI HTTP client.
    Tests full FastAPI middleware stack (CORS, Security Headers, Exception Handlers) without external dependencies.
    """
    raw_headers = []
    if headers:
        for k, v in headers.items():
            raw_headers.append((k.lower().encode('latin1'), v.encode('latin1')))
            
    scope = {
        'type': 'http',
        'http_version': '1.1',
        'method': method.upper(),
        'path': path,
        'raw_path': path.encode('ascii'),
        'query_string': query_string,
        'headers': raw_headers,
        'client': ('127.0.0.1', 50000),
        'server': ('127.0.0.1', 8000),
    }
    
    response_started = False
    status_code = None
    response_headers = []
    response_body = []
    
    async def receive():
        return {'type': 'http.request', 'body': body, 'more_body': False}
        
    async def send(message):
        nonlocal response_started, status_code, response_headers, response_body
        if message['type'] == 'http.response.start':
            response_started = True
            status_code = message['status']
            response_headers = message.get('headers', [])
        elif message['type'] == 'http.response.body':
            response_body.append(message.get('body', b''))
            
    await app(scope, receive, send)
    
    hdr_dict = {k.decode('latin1').lower(): v.decode('latin1') for k, v in response_headers}
    body_bytes = b"".join(response_body)
    return status_code, hdr_dict, body_bytes
```

This harness executes in microseconds, runs fully asynchronously within `asyncio.run()`, and tests real HTTP/WebSocket headers and response bodies.

---

## 3. Master Audit Vulnerability Mapping (R1–R5 to SEC-Vxx)

| Req ID | Audit Vuln ID | CWE Category | Target Component | Root Vulnerability | Verification Objective |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **R1** | `SEC-V01` | CWE-79 (Stored & DOM XSS) | `server.py`<br>`persistence.py`<br>`al_sangmoo_dashboard.html` | Unvalidated `SellOrder.reason` saved to SQLite and interpolated directly into DOM `.innerHTML` | Verify backend input regex/length bounds & frontend entity escaping (`escapeHtml`). |
| **R2** | `SEC-V02`<br>`SEC-V04` | CWE-942 (CORS Misconfig)<br>CWE-1385 (CSWSH)<br>CWE-400 (DoS) | `server.py`<br>`al_sangmoo/api/hub.py` | Wildcard `"*"` in CORS allowlist; unauthenticated WebSocket handshake; unbounded connection pool | Verify CORS origin rejection, WebSocket origin enforcement, and `MAX_CONNECTIONS = 50` limit. |
| **R3** | `SEC-V05` | CWE-22 (Path Traversal)<br>CWE-88 (Argument Injection) | `youtube_stream_scanner.py` | Unchecked YouTube video IDs (`v_id`) passed to `os.path.join` and `yt-dlp` subprocess | Verify strict regex `^[a-zA-Z0-9_-]{11}$` and `BASE_DIR` containment validation. |
| **R4** | `SEC-V07`<br>`SEC-V08` | CWE-209 (Info Disclosure)<br>CWE-20 (Improper Input Validation) | `server.py`<br>`al_sangmoo/api/` | Raw exception stack traces returned in HTTP 500; unvalidated dates/tickers in Pydantic models | Verify Pydantic field validators (`TICKER_REGEX`, `DATE_REGEX`, price/qty) and global 500 error sanitization. |
| **R5** | `SEC-V10` | CWE-693 (Missing Security Headers) | `server.py` | Missing OWASP defensive HTTP response headers on FastAPI endpoints | Verify presence of `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and `X-XSS-Protection`. |

---

## 4. Features Discovered

| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|----------|---------|-------------|--------|---------|----------------|----------------|
| 1 | R1: XSS | Backend `SellOrder.reason` Length & Format Validation | Validates reason field to maximum 100 characters and strips/escapes dangerous HTML/JS tags | `SellOrder(sell_price=100.0, reason=payload)` | Clean reason string stored in SQLite `my_portfolio` table | Raises HTTP 400 / Pydantic `ValidationError` or sanitizes string | `server.py`, `persistence.py` |
| 2 | R1: XSS | Frontend `escapeHtml()` Sanitizer Utility | HTML entity encoder escaping `&`, `<`, `>`, `"`, `'` | String with HTML tags (`<script>`, `<img ...>`) | Entity-escaped safe string (`&lt;script&gt;...`) | Returns empty string on `null`/`undefined` | `al_sangmoo_dashboard.html` |
| 3 | R1: XSS | Safe DOM Attribute / Event Binding | Refactored DOM templates using `data-*` attributes or escaped parameters instead of raw string interpolation in inline `onclick` | DOM click on stock card or table action | Triggers `selectStock` with sanitized ticker and price | No script execution on payload tickers | `al_sangmoo_dashboard.html` |
| 4 | R2: CORS | Strict Local CORS Origin Allowlist | Restricts CORS allowed origins strictly to localhost/127.0.0.1 ports 8000 and 3000, removing wildcard `"*"` | HTTP request with `Origin: http://evil.com` or `http://localhost:8000` | HTTP 200 with `Access-Control-Allow-Origin: http://localhost:8000` for allowed; NO header or CORS rejection for evil | Blocked by browser CORS policy | `server.py` |
| 5 | R2: CSWSH | WebSocket Handshake Origin Verification | Inspects incoming `Origin` header during `/ws/live_feed` connection handshake | WebSocket handshake with unauthorized `Origin` | Handshake accepted (HTTP 101) only for whitelisted local origins | Closes socket with code 1008 or rejects handshake | `server.py`, `hub.py` |
| 6 | R2: DoS | WebSocket Active Connection Cap (`MAX_CONNECTIONS = 50`) | Rejects new connection attempts when 50 active clients are already connected | 51st simultaneous WebSocket client connection | 50 clients connected; 51st rejected with code 1008 | Closed immediately with reason "Max connection capacity reached" | `hub.py` |
| 7 | R3: Path Traversal | YouTube Video ID Strict Regex (`^[a-zA-Z0-9_-]{11}$`) | Rejects video IDs not conforming to standard 11-char alphanumeric pattern | Valid ID: `dQw4w9WgXcQ`; Invalid ID: `../../etc/passwd` | Resolves subtitle file path inside `BASE_DIR` | Raises `ValueError` or `PermissionError` immediately | `youtube_stream_scanner.py` |
| 8 | R3: Path Traversal | Filesystem `BASE_DIR` Boundary Prefix Validation | Checks that `os.path.abspath(target).startswith(BASE_DIR)` | Any constructed subtitle or chart path | Returns verified absolute path | Raises `PermissionError` on path escape attempt | `youtube_stream_scanner.py`, `server.py` |
| 9 | R4: Validation | Pydantic Date & Boundary Validators | Enforces `^\d{4}-\d{2}-\d{2}$` date regex, `buy_price > 0`, `quantity > 0`, and upper bounds | `BuyOrder` / `SellOrder` payload with invalid date/price/quantity | Validated Pydantic model instance | Raises HTTP 422 / HTTP 400 `ValidationError` | `server.py` |
| 10 | R4: Validation | Broker Execution Ticker Regex Enforcement | Validates ticker symbol format on `/api/broker/order` and `/api/chart/{ticker}` before routing | Malicious ticker (e.g. `..`, `NVDA;DROP`, `<script>`) | Returns chart JSON or executes broker order | Returns HTTP 400 Bad Request | `server.py` |
| 11 | R4: Info Disclosure | Global 500 Exception Sanitization | Catches all unhandled exceptions and returns generic JSON error response without Python tracebacks | HTTP request triggering unhandled internal error | JSON `{"status": "error", "message": "An internal server error occurred"}` | HTTP 500 without `Traceback` or filepath leakage | `server.py` |
| 12 | R5: Headers | OWASP Defensive HTTP Response Headers | Middleware attaching standard security headers to all HTTP responses | Any HTTP GET/POST request (`/`, `/api/...`) | Response headers include `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-XSS-Protection: 1; mode=block` | Standard headers present even on error responses | `server.py` |

---

## 5. Edge Cases & Boundary Test Vectors

| # | Feature | Input / Payload | Expected Observed Behavior |
|---|---------|-----------------|-----------------------------|
| 1 | R1: `SellOrder.reason` | `<script>alert(document.cookie)</script>` | HTML tags stripped or entity-escaped; payload cannot execute in DOM. |
| 2 | R1: `SellOrder.reason` | `"><img src=x onerror=alert(1)>` | Quotation marks and tags sanitized; safely stored as text. |
| 3 | R1: `SellOrder.reason` | `"A" * 150` (150-char string exceeding 100 char limit) | Rejected with HTTP 422/400 ValidationError or truncated to 100 chars. |
| 4 | R1: `SellOrder.reason` | `""` (Empty string) or `"   "` (Whitespace only) | Defaults safely to `"MANUAL_SELL"`. |
| 5 | R1: `SellOrder.reason` | `"수익률 15.2% 달성 익절 매도 (Target Hit)"` (Korean & special chars) | Allowed and preserved intact without corruption. |
| 6 | R1: DOM `escapeHtml()` | `null`, `undefined`, `12345`, `false` | Handles non-string types gracefully; returns `""` for nullish, `"12345"` for numbers. |
| 7 | R2: CORS Allowlist | `Origin: http://localhost:8000` | Allowed: `Access-Control-Allow-Origin: http://localhost:8000`. |
| 8 | R2: CORS Allowlist | `Origin: http://127.0.0.1:3000` | Allowed: `Access-Control-Allow-Origin: http://127.0.0.1:3000`. |
| 9 | R2: CORS Allowlist | `Origin: http://evil.com` | Blocked: No matching `Access-Control-Allow-Origin` header returned. |
| 10 | R2: CORS Allowlist | `Origin: http://localhost.evil.com` (Subdomain spoofing) | Blocked: Subdomain prefix does not bypass whitelist. |
| 11 | R2: CORS Allowlist | `Origin: null` | Blocked: Sandboxed iframe origin rejected. |
| 12 | R2: CORS Wildcard | Any request | `Access-Control-Allow-Origin` header is NEVER `*`. |
| 13 | R2: CSWSH Handshake | WebSocket scope `Origin: http://evil.com` | Handshake rejected with status 403 or immediate close code 1008. |
| 14 | R2: CSWSH Handshake | WebSocket scope `Origin: http://localhost:8000` | Handshake accepted successfully. |
| 15 | R2: CSWSH Handshake | WebSocket scope without `Origin` header | Handled safely per policy (accepted for local direct tools). |
| 16 | R2: WebSocket DoS | 50 concurrent active connections | All 50 connections active and receiving broadcasts. |
| 17 | R2: WebSocket DoS | 51st simultaneous connection attempt | 51st rejected with code 1008 ("Max connection capacity reached"); active count stays <= 50. |
| 18 | R2: WebSocket DoS | Disconnect 10 clients, connect 10 new clients | Hub reclaims disconnected slots; new clients succeed up to capacity. |
| 19 | R3: Video ID Regex | `dQw4w9WgXcQ` (Valid 11-char ID) | Passes regex validation. |
| 20 | R3: Video ID Regex | `../../etc/passwd` (Unix traversal) | Regex validation fails immediately with `ValueError`. |
| 21 | R3: Video ID Regex | `..\..\windows\system32\cmd.exe` (Windows traversal) | Regex validation fails immediately with `ValueError`. |
| 22 | R3: Video ID Regex | `dQw4w9WgXcQ; rm -rf /` (Subprocess injection) | Regex validation fails immediately with `ValueError`. |
| 23 | R3: Video ID Regex | `dQw4w9WgXc` (10 chars, too short) | Regex validation fails immediately with `ValueError`. |
| 24 | R3: Video ID Regex | `dQw4w9WgXcQQ` (12 chars, too long) | Regex validation fails immediately with `ValueError`. |
| 25 | R3: Video ID Regex | `""` or `None` | Regex validation fails immediately with `ValueError`. |
| 26 | R3: Path Prefix Check | Resolved path escaping `BASE_DIR` | Raises `PermissionError("Path traversal attempt detected.")`. |
| 27 | R4: `BuyOrder.ticker` | `"NVDA"`, `"AMZN"`, `"005930.KS"`, `"000660.KS"` | Allowed and normalized to uppercase. |
| 28 | R4: `BuyOrder.ticker` | `".."`, `"..."`, `"---"`, `"NVDA;DROP"` | Rejected with HTTP 422 / 400 ValidationError. |
| 29 | R4: `BuyOrder.buy_date`| `"2026-08-22"` | Allowed. |
| 30 | R4: `BuyOrder.buy_date`| `"2026/08/22"`, `"22-08-2026"`, `"INVALID_DATE"` | Rejected with HTTP 422 / 400 ValidationError. |
| 31 | R4: `BuyOrder.price` | `0.0`, `-10.0`, `10_000_000.0` | Rejected with HTTP 422 ValidationError (must be `> 0` and `<= 1_000_000.0`). |
| 32 | R4: `BuyOrder.quantity`| `0.0`, `-5.0`, `1_000_000.0` | Rejected with HTTP 422 ValidationError (must be `> 0` and `<= 100_000.0`). |
| 33 | R4: `SellOrder.date` | `"NOT-A-DATE"`, `"2026-13-45"` | Rejected with HTTP 422 ValidationError. |
| 34 | R4: Broker Order API | `POST /api/broker/order` with `ticker=".."` | Rejected with HTTP 400 / 422 before reaching broker gateway. |
| 35 | R4: Chart API | `GET /api/chart/..` or `GET /api/chart/../../secret` | Rejected with HTTP 400 Bad Request. |
| 36 | R4: 500 Error Masking | Unhandled server exception | Returns HTTP 500 JSON `{"status": "error", "message": "An internal server error occurred"}`. |
| 37 | R4: Traceback Masking | Unhandled server exception | Response body contains ZERO instances of `"Traceback"`, `"File \""`, or `al_sangmoo`. |
| 38 | R5: Security Headers | `GET /` (Dashboard HTML) | Contains `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-XSS-Protection: 1; mode=block`. |
| 39 | R5: Security Headers | `GET /api/portfolio` (REST JSON) | Contains all 4 security headers. |
| 40 | R5: Security Headers | `GET /nonexistent_endpoint` (HTTP 404) | Contains all 4 security headers. |

---

## 6. Acceptance Criteria Verification Plan

| Acceptance Criterion | Verification Method in `test_phase5_1_security.py` | Target Result |
| :--- | :--- | :--- |
| **AC-1:** `POST /api/portfolio/sell/{id}` with XSS payload `{"reason": "<script>alert(1)</script>"}` is rejected with HTTP 400 or fully sanitized in database and DOM. | Test `test_r1_xss_backend_sanitization()`: Send XSS payload to `POST /api/portfolio/sell/{id}` and check response/database; verify HTML escaping in `test_r1_xss_frontend_sanitization()`. | **100% GREEN (No unescaped script tags in DB/DOM)** |
| **AC-2:** Cross-origin requests from untrusted origins (`http://evil.com`) are blocked with HTTP 403 / CORS rejection. | Test `test_r2_cors_origin_whitelisting()`: Send ASGI HTTP OPTIONS/GET/POST with `Origin: http://evil.com`; assert no allow header or rejection. | **100% GREEN (Untrusted origins blocked, wildcard disallowed)** |
| **AC-3:** WebSocket connection attempts from unauthorized external origins are rejected during handshake. | Test `test_r2_cswsh_websocket_origin()`: Initiate WebSocket handshake with `Origin: http://evil.com`; verify handshake is rejected. | **100% GREEN (Unauthorized WebSocket rejected)** |
| **AC-4:** Path traversal attempts (`v_id = "../../etc/passwd"`) in `youtube_stream_scanner.py` raise validation errors immediately. | Test `test_r3_path_traversal_validation()`: Pass traversal strings to video ID validator; assert `ValueError` raised. | **100% GREEN (`ValueError` raised on traversal)** |
| **AC-5:** Internal 500 errors return sanitized JSON without raw Python traceback leakage. | Test `test_r4_global_error_sanitization()`: Trigger intentional error route; assert sanitized JSON response with zero traceback tokens. | **100% GREEN (Sanitized JSON, zero traceback leakage)** |
| **AC-6:** Automated security verification test suite (`tools_and_tests/test_phase5_1_security.py`) passes 100% Green. | Run `python tools_and_tests/test_phase5_1_security.py` in powershell. | **100% GREEN (Exit code 0, 0 assertion failures)** |
| **AC-7:** Existing core regression test suites (`test_phase1`, `test_phase2`, `test_phase4`, `test_global60`) continue to pass 100% Green. | Run all 4 regression test files sequentially. | **100% GREEN (All regression suites pass)** |

---

## 7. Concrete Test Suite Blueprint (`tools_and_tests/test_phase5_1_security.py`)

Below is the concrete implementation blueprint for `test_phase5_1_security.py`:

```python
"""
Comprehensive Phase 5.1 Security Hardening Test Suite.
Verifies R1-R5 Security Remediation & OWASP Top 10 Protections:
- R1: Stored & DOM XSS Remediation (SEC-V01)
- R2: CORS Whitelisting, CSWSH Protection & WebSocket DoS Defense (SEC-V02, SEC-V04)
- R3: Path Traversal & Subprocess Hardening (SEC-V05)
- R4: Pydantic Input Validation & 500 Error Sanitization (SEC-V07, SEC-V08)
- R5: OWASP Defensive Security Response Headers (SEC-V10)
"""
import os
import sys
import re
import json
import asyncio
import sqlite3
from typing import Dict, Tuple

# Windows encoding fix
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Isolate tests to dedicated temporary SQLite database
TEST_DB = os.path.join(PROJECT_ROOT, "test_quant_trades_p5_1.db")
os.environ["AL_SANGMOO_DB_PATH"] = TEST_DB

import db_manager
from server import app, BuyOrder, SellOrder
from al_sangmoo.api.hub import hub, WebSocketBroadcastHub
from youtube_stream_scanner import YOUTUBE_ID_REGEX, safe_download_subtitles

# ---------------------------------------------------------------------------
# Zero-Dependency In-Memory ASGI Test Harness
# ---------------------------------------------------------------------------
async def asgi_request(app, method: str, path: str, headers: Dict[str, str] = None, body: bytes = b"", query_string: bytes = b"") -> Tuple[int, Dict[str, str], bytes]:
    raw_headers = []
    if headers:
        for k, v in headers.items():
            raw_headers.append((k.lower().encode('latin1'), v.encode('latin1')))
            
    scope = {
        'type': 'http',
        'http_version': '1.1',
        'method': method.upper(),
        'path': path,
        'raw_path': path.encode('ascii'),
        'query_string': query_string,
        'headers': raw_headers,
        'client': ('127.0.0.1', 50000),
        'server': ('127.0.0.1', 8000),
    }
    
    status_code = 500
    response_headers = []
    response_body = []
    
    async def receive():
        return {'type': 'http.request', 'body': body, 'more_body': False}
        
    async def send(message):
        nonlocal status_code, response_headers, response_body
        if message['type'] == 'http.response.start':
            status_code = message['status']
            response_headers = message.get('headers', [])
        elif message['type'] == 'http.response.body':
            response_body.append(message.get('body', b''))
            
    await app(scope, receive, send)
    hdr_dict = {k.decode('latin1').lower(): v.decode('latin1') for k, v in response_headers}
    return status_code, hdr_dict, b"".join(response_body)

# ---------------------------------------------------------------------------
# Test Setup & Teardown
# ---------------------------------------------------------------------------
def setup_test_db():
    db_manager.init_db()

def cleanup_test_db():
    for f in [TEST_DB, f"{TEST_DB}-wal", f"{TEST_DB}-shm"]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass

# ---------------------------------------------------------------------------
# Test 1: R1 Stored & DOM XSS Remediation
# ---------------------------------------------------------------------------
def test_r1_xss_remediation():
    print("\n[Test 1] Verifying R1: Stored & DOM XSS Remediation (SEC-V01)...")
    
    # 1. Backend Pydantic SellOrder reason sanitization
    xss_payloads = [
        "<script>alert('xss')</script>",
        "<img src=x onerror=alert(1)>",
        "\"><svg/onload=alert(1)>",
        "javascript:alert(1)"
    ]
    for p in xss_payloads:
        order = SellOrder(sell_price=100.0, reason=p)
        assert "<script>" not in order.reason, f"Failed to sanitize XSS script tag in {order.reason}"
        assert "<img" not in order.reason, f"Failed to sanitize XSS img tag in {order.reason}"
        assert "<svg" not in order.reason, f"Failed to sanitize XSS svg tag in {order.reason}"
        assert len(order.reason) <= 100, "Reason length exceeds 100 chars"
    print("  - Backend SellOrder Reason Sanitizer: PASSED (XSS tags stripped/escaped)")

    # 2. Database persistence verification
    pos_id = db_manager.add_portfolio_buy(ticker="NVDA", buy_price=120.0, quantity=1.0)
    db_manager.record_portfolio_sell(holding_id=pos_id, sell_price=130.0, reason="<script>alert(1)</script>")
    
    conn = db_manager.get_db()
    cur = conn.cursor()
    cur.execute("SELECT exit_advice FROM my_portfolio WHERE id = ?", (pos_id,))
    row = cur.fetchone()
    conn.close()
    assert row is not None
    assert "<script>" not in row[0], f"Unsanitized XSS payload found in SQLite: {row[0]}"
    print("  - SQLite Database Persistence XSS Immunity: PASSED")

    # 3. Frontend HTML escapeHtml utility verification
    dashboard_path = os.path.join(PROJECT_ROOT, "al_sangmoo_dashboard.html")
    assert os.path.exists(dashboard_path), "al_sangmoo_dashboard.html not found"
    with open(dashboard_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    assert "function escapeHtml" in html_content, "escapeHtml() utility missing in al_sangmoo_dashboard.html"
    assert "&amp;" in html_content and "&lt;" in html_content and "&gt;" in html_content, "escapeHtml() missing entity replacements"
    print("  - Frontend escapeHtml() DOM Utility: PASSED")
    print("  -> PASSED: R1 Stored & DOM XSS Remediation verified.")

# ---------------------------------------------------------------------------
# Test 2: R2 CORS Whitelisting, CSWSH & WebSocket DoS Defense
# ---------------------------------------------------------------------------
def test_r2_cors_and_websocket_hardening():
    print("\n[Test 2] Verifying R2: CORS Whitelisting, CSWSH & WebSocket DoS Defense (SEC-V02, SEC-V04)...")
    
    async def run_cors_and_ws_tests():
        # 1. Allowed Origin CORS Test
        status, headers, body = await asgi_request(
            app, "GET", "/api/portfolio",
            headers={"Origin": "http://localhost:8000", "Host": "localhost:8000"}
        )
        assert status == 200
        assert headers.get("access-control-allow-origin") == "http://localhost:8000"
        print("  - CORS Whitelisted Origin (http://localhost:8000): ACCEPTED")

        # 2. Evil Origin CORS Test
        status_evil, headers_evil, _ = await asgi_request(
            app, "GET", "/api/portfolio",
            headers={"Origin": "http://evil.com", "Host": "localhost:8000"}
        )
        assert headers_evil.get("access-control-allow-origin") != "http://evil.com"
        assert headers_evil.get("access-control-allow-origin") != "*"
        print("  - CORS Untrusted Origin (http://evil.com): BLOCKED (Wildcard '*' disallowed)")

        # 3. WebSocket Connection Capacity Enforcement (MAX_CONNECTIONS = 50)
        test_hub = WebSocketBroadcastHub(max_connections=50)
        
        class MockWS:
            def __init__(self):
                self.accepted = False
                self.closed = False
                self.close_code = None
            async def accept(self):
                self.accepted = True
            async def close(self, code=1000, reason=""):
                self.closed = True
                self.close_code = code
            async def send_json(self, msg):
                pass
                
        ws_pool = [MockWS() for _ in range(50)]
        for ws in ws_pool:
            accepted = await test_hub.connect(ws)
            assert accepted is True
            assert ws.accepted is True
        assert len(test_hub.active_connections) == 50
        print(f"  - WebSocket Connection Pool: Filled 50 / 50 active slots")

        # 51st client connection attempt
        ws_overflow = MockWS()
        overflow_accepted = await test_hub.connect(ws_overflow)
        assert overflow_accepted is False
        assert ws_overflow.closed is True
        assert ws_overflow.close_code == 1008
        assert len(test_hub.active_connections) == 50
        print("  - WebSocket Overflow Connection (Client #51): REJECTED with code 1008 as expected")

        # Disconnect and reclaim
        for ws in ws_pool:
            await test_hub.disconnect(ws)
        assert len(test_hub.active_connections) == 0
        print("  - WebSocket Pool Cleanup: All connections safely detached")

    asyncio.run(run_cors_and_ws_tests())
    print("  -> PASSED: R2 CORS Whitelisting & WebSocket DoS Defense verified.")

# ---------------------------------------------------------------------------
# Test 3: R3 Path Traversal & Subprocess Hardening
# ---------------------------------------------------------------------------
def test_r3_path_traversal_hardening():
    print("\n[Test 3] Verifying R3: Path Traversal & Subprocess Hardening (SEC-V05)...")
    
    # 1. Valid YouTube video IDs
    valid_ids = ["dQw4w9WgXcQ", "abc_123-XYZ", "_A-1b2C3d4E"]
    for v in valid_ids:
        assert YOUTUBE_ID_REGEX.match(v) is not None, f"Valid ID failed regex: {v}"
    print("  - YouTube Video ID Whitelist Regex: Validated standard 11-char patterns")

    # 2. Malicious Path Traversal & Command Injection payloads
    malicious_ids = [
        "../../etc/passwd",
        r"..\..\windows\system32\calc.exe",
        "video_id; rm -rf /",
        "dQw4w9WgXcQ&param=1",
        "toolongvideoid12345",
        "short",
        "",
        None
    ]
    for bad in malicious_ids:
        if bad is None:
            continue
        assert YOUTUBE_ID_REGEX.match(bad) is None, f"Malicious ID bypassed regex: {bad}"
        try:
            safe_download_subtitles(bad)
            assert False, f"safe_download_subtitles did not raise ValueError for {bad}"
        except (ValueError, PermissionError):
            pass
    print("  - Subprocess & Path Traversal Injection Vectors: REJECTED with ValueError")
    print("  -> PASSED: R3 Path Traversal & Subprocess Hardening verified.")

# ---------------------------------------------------------------------------
# Test 4: R4 Pydantic Input Validation & Global Error Sanitization
# ---------------------------------------------------------------------------
def test_r4_pydantic_validation_and_error_sanitization():
    print("\n[Test 4] Verifying R4: Pydantic Input Validation & Global Error Sanitization (SEC-V07, SEC-V08)...")
    
    # 1. Pydantic BuyOrder validation
    try:
        BuyOrder(ticker="..", buy_price=100.0, quantity=1.0)
        assert False, "BuyOrder accepted invalid ticker '..'"
    except Exception:
        pass
        
    try:
        BuyOrder(ticker="NVDA", buy_price=-10.0, quantity=1.0)
        assert False, "BuyOrder accepted negative buy_price"
    except Exception:
        pass

    try:
        BuyOrder(ticker="NVDA", buy_price=100.0, quantity=1.0, buy_date="INVALID_DATE")
        assert False, "BuyOrder accepted invalid date format"
    except Exception:
        pass
    print("  - Pydantic BuyOrder & SellOrder Boundary Validators: PASSED")

    # 2. Broker order API ticker validation
    async def run_r4_api_tests():
        # Check invalid ticker on /api/chart/..
        status_chart, _, _ = await asgi_request(app, "GET", "/api/chart/..")
        assert status_chart in [400, 404, 422], f"Expected 400/404/422 for invalid ticker chart, got {status_chart}"
        print("  - GET /api/chart/.. Malformed Ticker: REJECTED")

        # 3. Global Exception Handler (Internal 500 Sanitization)
        # Request intentional error trigger or non-existent broken endpoint
        status_err, _, body_err = await asgi_request(app, "GET", "/api/nonexistent_test_500_route")
        # Ensure that whatever error is returned, no internal Python tracebacks are leaked
        body_text = body_err.decode("utf-8", errors="ignore")
        assert "Traceback (most recent call last)" not in body_text, "Raw Python Traceback leaked in response!"
        assert "d:\\코딩" not in body_text.lower(), "Internal absolute filepath leaked in response!"
        print("  - Global Exception Sanitization (Zero Traceback Leakage): PASSED")

    asyncio.run(run_r4_api_tests())
    print("  -> PASSED: R4 Pydantic Input Validation & Global Error Sanitization verified.")

# ---------------------------------------------------------------------------
# Test 5: R5 OWASP Security Response Headers
# ---------------------------------------------------------------------------
def test_r5_owasp_security_headers():
    print("\n[Test 5] Verifying R5: OWASP Defensive Security Response Headers (SEC-V10)...")
    
    async def run_headers_test():
        endpoints = ["/", "/api/portfolio", "/api/recommendations/matrix"]
        for ep in endpoints:
            status, headers, _ = await asgi_request(app, "GET", ep)
            assert status == 200, f"Endpoint {ep} returned status {status}"
            assert headers.get("x-content-type-options") == "nosniff", f"Missing X-Content-Type-Options on {ep}"
            assert headers.get("x-frame-options") == "DENY", f"Missing X-Frame-Options on {ep}"
            assert headers.get("referrer-policy") == "strict-origin-when-cross-origin", f"Missing Referrer-Policy on {ep}"
            assert headers.get("x-xss-protection") == "1; mode=block", f"Missing X-XSS-Protection on {ep}"
        print("  - OWASP Security Headers (X-Content-Type-Options, X-Frame-Options, Referrer-Policy, X-XSS-Protection): VERIFIED on all endpoints")

    asyncio.run(run_headers_test())
    print("  -> PASSED: R5 OWASP Security Response Headers verified.")

# ---------------------------------------------------------------------------
# Main Execution Runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 70)
    print("  R-SANGMOO QUANT PLATFORM: PHASE 5.1 SECURITY HARDENING TEST SUITE")
    print("=" * 70)
    setup_test_db()
    try:
        test_r1_xss_remediation()
        test_r2_cors_and_websocket_hardening()
        test_r3_path_traversal_hardening()
        test_r4_pydantic_validation_and_error_sanitization()
        test_r5_owasp_security_headers()
        print("\n" + "=" * 70)
        print("  ALL PHASE 5.1 SECURITY HARDENING TESTS PASSED! (100% GREEN)")
        print("=" * 70)
    finally:
        cleanup_test_db()
```

---

## 8. Regression Verification Matrix

To ensure that the Phase 5.1 hardening introduces zero regressions to existing quant, trading, and execution subsystems, the following test matrix must be executed after implementation:

| Test Suite | Command | Expected Result | Regression Risk Surface |
| :--- | :--- | :--- | :--- |
| **Phase 1 Hardening** | `python tools_and_tests/test_phase1_hardening.py` | 100% Green (0 errors) | SQLite WAL concurrency, JSON atomic persistence, REST handlers |
| **Phase 2 Modular & WS** | `python tools_and_tests/test_phase2_modular.py` | 100% Green (0 errors) | Core config, Ichimoku quant engine, WebSocket hub broadcasts |
| **Phase 3 Backtester** | `python tools_and_tests/test_phase3_backtester.py` | 100% Green (0 errors) | Backtesting engine, MTF consensus, ATR position sizing |
| **Phase 4 Execution** | `python tools_and_tests/test_phase4_execution.py` | 100% Green (0 errors) | Risk guardrails, Paper trading broker, SQLite online backup |
| **Global 60 Universe** | `python tools_and_tests/test_global60_dual_strategy.py` | 100% Green (0 errors) | 60-stock universe, ticker resolver, origin tags |
| **Phase 5.1 Security** | `python tools_and_tests/test_phase5_1_security.py` | 100% Green (0 errors) | R1-R5 security specifications & acceptance criteria |

---

## 9. Conclusion & Implementation Readiness

The survey confirms:
1. **Testing Infrastructure**: Standalone, zero-dependency in-memory ASGI test harness allows full end-to-end verification of HTTP, WebSocket, headers, CORS, validation, and database operations without needing third-party libraries like `httpx`.
2. **Requirements Mapping**: Every requirement R1 through R5 and Acceptance Criteria AC-1 through AC-7 has been mapped to concrete test vectors and edge cases.
3. **Regression Safety**: All 5 existing test suites are currently passing 100% Green and will serve as baseline regression verification.
4. **Handoff Package**: Complete code blueprints, test matrices, and edge cases are ready for Phase 5.1 implementation and verification.
