# Domain 1: Web & API Security Vulnerability Assessment Report

**Audit Target**: Al-Sangmoo Quant Terminal (Full Platform)  
**Auditor**: Security Vulnerability Auditor (Explorer 1)  
**Audit Mode**: Strictly Read-Only Static Code Analysis & Verification  
**Date**: 2026-08-25  
**Assessment Standard**: OWASP Top 10 (2021/2026), CWE/SANS Top 25, NIST SP 800-53  

---

## Executive Summary

A comprehensive, code-level static security audit was performed across all backend modules (`server.py`, `al_sangmoo/api/*`, `al_sangmoo/interfaces/api/routers/*`, `al_sangmoo/infrastructure/*`, `al_sangmoo/domain/risk/*`, `youtube_stream_scanner.py`, `al_sangmoo_daily_bot.py`, `generate_dashboard_feed.py`) and frontend assets (`frontend/index.html`, `frontend/js/*`, `al_sangmoo_dashboard.html`, `al_sangmoo_chart_system.html`).

While significant hardening was implemented during previous phases (such as parameterized SQLite queries, non-blocking background scanning, and Pydantic input models on core portfolio endpoints), our static audit identified **10 concrete security vulnerabilities and design weaknesses** categorized across Critical, High, Medium, and Low severity tiers.

### Vulnerability Severity Matrix

| Vulnerability ID | Title | Severity | CWE | Primary File & Lines |
| :--- | :--- | :---: | :--- | :--- |
| **SEC-01** | Insecure WebSocket Origin Validation & Lack of Handshake Authentication (CSWSH) | 🔴 **HIGH** | CWE-1385, CWE-306 | `server.py:218-221`, `al_sangmoo/api/hub.py:17-35` |
| **SEC-02** | Exposure of Plaintext KIS OAuth 2.0 Access Tokens via Missing `.gitignore` Rule | 🔴 **HIGH** | CWE-522, CWE-312 | `kis_broker.py:66, 117-138`, `.gitignore:1-25` |
| **SEC-03** | Information Disclosure: Unmasked Broker Account Number (CANO) in Balance API | 🟡 **MEDIUM** | CWE-200, CWE-359 | `kis_broker.py:345`, `broker.py:28-30` |
| **SEC-04** | DOM XSS in Frontend Search Dropdown via Unescaped `innerHTML` Interpolation | 🔴 **HIGH** | CWE-79 | `frontend/js/websocket.js:420-428` |
| **SEC-05** | Missing Input & Pre-Trade Guardrail Validation in `BrokerOrderRequest` | 🔴 **HIGH** | CWE-20, CWE-840 | `broker.py:11-20, 42-68` |
| **SEC-06** | Path Traversal Flaw in Chart Route via Incomplete Prefix Matching | 🟡 **MEDIUM** | CWE-22 | `charts.py:24-29` |
| **SEC-07** | Missing Content-Security-Policy (CSP) & Incomplete Security Response Headers | 🟡 **MEDIUM** | CWE-693, CWE-1021 | `server.py:149-160` |
| **SEC-08** | Schema Disconnect & Missing Endpoint Method (`trade_history` & `get_trade_history_records`) | 🟡 **MEDIUM** | CWE-754, CWE-404 | `portfolio_guardian.py:241-251`, `portfolio.py:284` |
| **SEC-09** | Hardcoded Personal Email Address in Source Code (PII Leakage) | 🟢 **LOW** | CWE-798, CWE-200 | `al_sangmoo_daily_bot.py:42` |
| **SEC-10** | Fragile Inline `onclick` String Interpolations in UI Rendering | 🟢 **LOW** | CWE-79, CWE-116 | `frontend/js/ui.js:153, 216, 236, 294` |

---

## Detailed Vulnerability Analysis

---

### SEC-01: Insecure WebSocket Origin Validation & Lack of Handshake Authentication (CSWSH)

- **Severity**: 🔴 **HIGH**
- **CWE**: CWE-1385 (Missing Origin Validation in WebSockets), CWE-306 (Missing Authentication for Critical Function)
- **Target Files & Lines**: `server.py:218-221`, `al_sangmoo/api/hub.py:17-35`

#### Vulnerable Code Snippet
```python
# File: server.py (lines 218-221)
@app.websocket("/ws")
@app.websocket("/ws/live_feed")
async def websocket_live_hub(websocket: WebSocket):
    """Real-time WebSocket connection to broadcast hub with CSWSH protection."""
    origin = websocket.headers.get("origin")
    if origin and origin not in ALLOWED_ORIGINS and not origin.startswith("http://localhost:") and not origin.startswith("http://127.0.0.1:"):
        await websocket.close(code=1008, reason="Forbidden Origin")
        return
        
    connected = await hub.connect(websocket)
    ...
```

#### Detailed Explanation & Exploit Scenario
1. **Omitted Origin Bypass**: The conditional `if origin and ...` only validates the origin if the `Origin` header is present and non-empty. If a non-browser client, manipulated proxy, or local process omits the `Origin` header, the validation is skipped entirely.
2. **Broad Wildcard Port Prefix Matching (`startswith`)**: The check `not origin.startswith("http://localhost:")` and `not origin.startswith("http://127.0.0.1:")` permits connections from *any port* on `localhost` or `127.0.0.1`. If an attacker executes a script on a compromised local development server (e.g. Jupyter notebook on 8888, webpack-dev-server on 8080, or local test servers), that webpage can open a WebSocket connection to `ws://127.0.0.1:8000/ws` without restriction.
3. **No Authentication / Session Token**: The WebSocket gateway does not require any authentication token (API token, session cookie, or handshake ticket).
4. **Data Exfiltration Risk**: Upon connection (`server.py:227-228`), the server immediately transmits real-time portfolio holdings and financial data (`{"type": "connected", "data": {"portfolio": p_data}}`). An unauthorized page can harvest live trading data, open positions, account valuations, and real-time execution alerts.

#### Actionable Remediation
1. Enforce strict exact-match origin validation against a verified whitelist:
   ```python
   ALLOWED_WS_ORIGINS = {
       "http://localhost:8000",
       "http://127.0.0.1:8000",
       "http://localhost:3000",
       "http://127.0.0.1:3000"
   }
   
   origin = websocket.headers.get("origin")
   if not origin or origin not in ALLOWED_WS_ORIGINS:
       await websocket.close(code=1008, reason="Unauthorized WebSocket Origin")
       return
   ```
2. For remote / multi-user access, require an ephemeral token query parameter (`/ws?token=<HMAC_TOKEN>`) validated during the initial handshake before calling `hub.connect(websocket)`.

---

### SEC-02: Exposure of Plaintext KIS OAuth 2.0 Access Tokens via Missing `.gitignore` Rule

- **Severity**: 🔴 **HIGH**
- **CWE**: CWE-522 (Insufficiently Protected Credentials), CWE-312 (Cleartext Storage of Sensitive Information)
- **Target Files & Lines**: `al_sangmoo/infrastructure/brokers/kis_broker.py:66, 117-138`, `.gitignore:1-25`

#### Vulnerable Code Snippet
```python
# File: al_sangmoo/infrastructure/brokers/kis_broker.py (lines 66, 127-136)
self.cache_file = TOKEN_CACHE_DIR / f".kis_token_{self.mode_str}.json"

payload = {
    "access_token": token,
    "expires_at": expired_str,
    "expires_at_timestamp": self.token_expiry,
    "mode": self.mode_str,
    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
}
with open(self.cache_file, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2)
```

```gitignore
# File: .gitignore (lines 1-25)
# Environment variables & secrets (CRITICAL: Do not commit)
.env
*.env
.env.*

# Python caches
__pycache__/
*.pyc
*.pyo

# Local Database
*.db-journal
*.db-wal
*.db-shm
test_*.db*
backups/*.db
*.vtt
.pytest_cache/
data/charts/PRN.json
data/charts/NUL.json
data/charts/CON.json
data/charts/AUX.json
```

#### Detailed Explanation & Exploit Scenario
1. `kis_broker.py` caches the live Korea Investment & Securities (한국투자증권) OAuth 2.0 bearer token to disk at `data/.kis_token_prod.json` and `data/.kis_token_vps.json`.
2. In `.gitignore`, `data/` or `.kis_token_*.json` is **not ignored**. Only four specific Windows reserved names (`PRN.json`, `NUL.json`, etc.) under `data/charts/` are ignored.
3. If an automated script or developer runs `git add .` or `git add data/`, the cached file containing the active OAuth token (`access_token`) will be committed to the Git repository.
4. An active KIS OAuth token has a 24-hour validity window and full authorization to query account balances, view open positions, and execute real-money stock orders (`place_order`) on US/KRX exchanges via Korea Investment REST APIs.

#### Actionable Remediation
1. Add token cache files and the entire `data/` directory to `.gitignore`:
   ```gitignore
   # Broker Token Cache & Runtime Data
   .kis_token_*.json
   data/.kis_token_*.json
   data/
   !data/.gitkeep
   ```
2. Store token cache files in a secure user-level directory (`~/.al_sangmoo/` or OS-specific secure keyring) with `0600` (read/write by owner only) file permissions.

---

### SEC-03: Information Disclosure: Unmasked Broker Account Number (CANO) in Balance API Endpoint

- **Severity**: 🟡 **MEDIUM**
- **CWE**: CWE-200 (Exposure of Sensitive Information to an Unauthorized Actor), CWE-359 (Exposure of Private Personal Information)
- **Target Files & Lines**: `al_sangmoo/infrastructure/brokers/kis_broker.py:345`, `al_sangmoo/interfaces/api/routers/broker.py:28-30`

#### Vulnerable Code Snippet
```python
# File: al_sangmoo/infrastructure/brokers/kis_broker.py (lines 342-352)
return {
    "status": "success",
    "mode": "VIRTUAL_PAPER" if self.is_paper else "REAL_PRODUCTION",
    "account_no": f"{self.account_no}-{self.account_code}",
    "total_equity_usd": total_equity if total_equity > 0 else 100_000.0,
    "cash_available_usd": cash_avail,
    "stock_eval_usd": total_eval,
    "holdings_count": len(all_holdings),
    "holdings": all_holdings,
    "raw_summary": latest_summary
}
```

```python
# File: al_sangmoo/interfaces/api/routers/broker.py (lines 27-30)
@router.get("/balance")
def get_broker_balance():
    """Queries live US equity cash balance and portfolio equity from KIS."""
    return default_kis_broker.get_account_balance()
```

#### Detailed Explanation & Exploit Scenario
1. In `kis_broker.py:77-81` (`get_status()`), the account number is masked (`f"{self.account_no[:4]}****{self.account_no[-2:]}"`).
2. However, in `get_overseas_balance()` (line 345), `account_no` is returned in **full, unmasked cleartext** (`f"{self.account_no}-{self.account_code}"`).
3. Calling `GET /api/broker/balance` directly leaks the complete 8-digit real brokerage account number and 2-digit product code to any client on the network.
4. In addition, `raw_summary` contains internal broker response fields which may contain customer identification identifiers.

#### Actionable Remediation
1. Mask the account number in `get_overseas_balance()`:
   ```python
   masked_acc = f"{self.account_no[:4]}****{self.account_no[-2:]}" if len(self.account_no) >= 8 else "NOT_CONFIGURED"
   return {
       "status": "success",
       "mode": "VIRTUAL_PAPER" if self.is_paper else "REAL_PRODUCTION",
       "account_no": f"{masked_acc}-{self.account_code}",
       ...
   }
   ```
2. Sanitize or strip raw unneeded fields from `raw_summary` before returning to REST clients.

---

### SEC-04: DOM XSS in Frontend Search Dropdown via Unescaped `innerHTML` Interpolation

- **Severity**: 🔴 **HIGH**
- **CWE**: CWE-79 (Improper Neutralization of Input During Web Page Generation - DOM XSS)
- **Target Files & Lines**: `frontend/js/websocket.js:420-428`

#### Vulnerable Code Snippet
```javascript
// File: frontend/js/websocket.js (lines 417-429)
const results = await ApiClient.searchStocks(query);
if (spinner) spinner.style.display = "none";
if (results && results.length > 0) {
    dropdown.innerHTML = results.map(r => `
        <div class="search-item" style="padding:8px 12px; border-bottom:1px solid #1e293b; cursor:pointer; display:flex; justify-content:space-between; align-items:center;" data-ticker="${r.ticker}" data-price="${r.price || 0}">
            <div>
                <strong style="color:#f8fafc; font-family:'JetBrains Mono';">${r.ticker}</strong>
                <span style="color:#94a3b8; font-size:11px; margin-left:6px;">${r.name || ''}</span>
            </div>
            <span style="color:#38bdf8; font-weight:700; font-family:'JetBrains Mono';">$${Number(r.price || 0).toFixed(2)}</span>
        </div>
    `).join('');
    dropdown.style.display = "block";
```

#### Detailed Explanation & Exploit Scenario
1. In `websocket.js:420-428`, search results returned from `ApiClient.searchStocks(query)` (`GET /api/search?q=...`) are directly interpolated into `dropdown.innerHTML` template literals without passing through `UI.escapeHtml()`.
2. While `UI.renderPortfolio()` and `UI.renderTopPicks()` in `frontend/js/ui.js` sanitize fields with `this.escapeHtml()`, `websocket.js` omits sanitization for `r.ticker` and `r.name`.
3. In `ticker_resolver.py:172-179`, if a search query is resolved to a custom fallback or if an upstream stock directory / API contains unescaped special characters, entering crafted characters or retrieving poisoned data executes arbitrary JavaScript in the context of the user's trading terminal session.
4. An attacker could exploit this DOM XSS to hijack session cookies, dispatch unauthorized stock purchase/sell orders (`ApiClient.buyStock()`, `ApiClient.sellStock()`), or exfiltrate portfolio state.

#### Actionable Remediation
1. Escape all dynamic properties before HTML template concatenation:
   ```javascript
   import { UI } from './ui.js?v=2.0.4';
   
   dropdown.innerHTML = results.map(r => {
       const safeTicker = UI.escapeHtml(r.ticker || '');
       const safeName = UI.escapeHtml(r.name || r.name_kr || r.name_en || '');
       const safePrice = Number(r.price || r.close || 0).toFixed(2);
       return `
           <div class="search-item" style="padding:8px 12px; border-bottom:1px solid #1e293b; cursor:pointer; display:flex; justify-content:space-between; align-items:center;" data-ticker="${safeTicker}" data-price="${safePrice}">
               <div>
                   <strong style="color:#f8fafc; font-family:'JetBrains Mono';">${safeTicker}</strong>
                   <span style="color:#94a3b8; font-size:11px; margin-left:6px;">${safeName}</span>
               </div>
               <span style="color:#38bdf8; font-weight:700; font-family:'JetBrains Mono';">$${safePrice}</span>
           </div>
       `;
   }).join('');
   ```

---

### SEC-05: Missing Input & Pre-Trade Guardrail Validation in `BrokerOrderRequest`

- **Severity**: 🔴 **HIGH**
- **CWE**: CWE-20 (Improper Input Validation), CWE-840 (Business Logic Errors)
- **Target Files & Lines**: `al_sangmoo/interfaces/api/routers/broker.py:11-20, 48-68`

#### Vulnerable Code Snippet
```python
# File: al_sangmoo/interfaces/api/routers/broker.py (lines 11-20, 66-68)
class BrokerOrderRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=15)
    side: Optional[str] = Field(default="BUY", description="'BUY' or 'SELL'")
    qty: Optional[int] = Field(default=None, gt=0, le=100_000)
    quantity: Optional[float] = Field(default=None, gt=0, le=100_000.0)
    price: Optional[float] = Field(default=0.0, ge=0.0)
    buy_price: Optional[float] = Field(default=None, ge=0.0)
    buy_date: Optional[str] = Field(default=None)
    order_type: str = Field(default="00", description="'00': Limit (지정가), '01': Market")
    exchange: Optional[str] = Field(default="NASD", description="'NASD', 'NYSE', 'AMEX'")

...
    eval_res = validate_pre_trade_guardrail(...)
    if not eval_res["allowed"] and final_side == "BUY":
        raise HTTPException(status_code=400, detail=f"Pre-Trade Risk Violation: {eval_res['reason']}")
```

#### Detailed Explanation & Exploit Scenario
1. **Missing Ticker Regex Validation**: `portfolio.py` enforces `@field_validator("ticker")` using `TICKER_REGEX = re.compile(r'^[A-Za-z0-9.\^=-]{1,15}$')`. In contrast, `broker.py:11-20` only specifies `min_length=1, max_length=15`. Characters such as newlines, spaces, or injection payloads can pass into the broker adapter.
2. **Missing `side` Validation & Guardrail Bypass**:
   - `side` is defined as `Optional[str]` without enum constraints (`Literal["BUY", "SELL"]`).
   - Look at line 66: `if not eval_res["allowed"] and final_side == "BUY": raise HTTPException(...)`.
   - If an attacker passes `side = "buy "` (with whitespace) or an unrecognized value, `validate_pre_trade_guardrail` runs, fails, but line 66 evaluates `final_side == "BUY"` to False if case or normalization is bypassed, or fails to reject illegal actions.
3. **Missing `buy_date` Regex**: `buy_date` is unvalidated; in `portfolio.py`, `DATE_REGEX` (`^\d{4}-\d{2}-\d{2}$`) is strictly enforced.
4. **Missing `exchange` and `order_type` Whitelists**: Arbitrary strings can be submitted to KIS TR payload parameters.

#### Actionable Remediation
1. Update `BrokerOrderRequest` with Pydantic field validators and `Literal` types:
   ```python
   from typing import Literal
   
   class BrokerOrderRequest(BaseModel):
       ticker: str = Field(..., min_length=1, max_length=15)
       side: Literal["BUY", "SELL"] = "BUY"
       qty: Optional[int] = Field(default=None, gt=0, le=100_000)
       quantity: Optional[float] = Field(default=None, gt=0, le=100_000.0)
       price: float = Field(default=0.0, ge=0.0, le=10_000_000.0)
       buy_price: Optional[float] = Field(default=None, ge=0.0, le=10_000_000.0)
       buy_date: Optional[str] = Field(default=None)
       order_type: Literal["00", "01"] = "00"
       exchange: Literal["NASD", "NYSE", "AMEX"] = "NASD"

       @field_validator("ticker")
       @classmethod
       def validate_ticker(cls, v: str) -> str:
           clean = v.strip().upper()
           if not TICKER_REGEX.match(clean):
               raise ValueError(f"Invalid ticker format: '{v}'")
           return clean

       @field_validator("buy_date")
       @classmethod
       def validate_buy_date(cls, v: Optional[str]) -> Optional[str]:
           if v is not None and v.strip():
               clean = v.strip()
               if not DATE_REGEX.match(clean):
                   raise ValueError("Date format must be YYYY-MM-DD")
               return clean
           return None
   ```

---

### SEC-06: Path Traversal Flaw in Chart Route via Incomplete Prefix Matching

- **Severity**: 🟡 **MEDIUM**
- **CWE**: CWE-22 (Improper Limitation of a Pathname to a Restricted Directory - Path Traversal)
- **Target Files & Lines**: `al_sangmoo/interfaces/api/routers/charts.py:24-29`

#### Vulnerable Code Snippet
```python
# File: al_sangmoo/interfaces/api/routers/charts.py (lines 23-30)
@router.get("/api/chart/{ticker}")
async def get_chart_data(ticker: str):
    global CHART_CACHE
    ticker_resolved = resolve_ticker(ticker)
    chart_file = os.path.abspath(os.path.join(CHARTS_DIR, f"{ticker_resolved}.json"))
    charts_dir_abs = os.path.abspath(CHARTS_DIR)

    # Path traversal protection
    if not chart_file.startswith(charts_dir_abs):
        raise HTTPException(status_code=400, detail="유효하지 않은 차트 파일 경로입니다.")
```

#### Detailed Explanation & Exploit Scenario
1. In `charts.py`, `chart_file.startswith(charts_dir_abs)` checks if the resolved path string begins with `CHARTS_DIR`.
2. Classic `startswith` prefix flaw: If `CHARTS_DIR` is `/app/data/charts`, a path like `/app/data/charts_backup/secret.json` or `/app/data/charts_temp/data.json` will satisfy `chart_file.startswith("/app/data/charts")` because `/app/data/charts` is a substring prefix.
3. `ticker: str` in the endpoint parameter signature has no `TICKER_REGEX` validator, relying solely on this path prefix check.

#### Actionable Remediation
1. Validate `ticker` with `TICKER_REGEX` and ensure prefix matching includes the directory separator or use `os.path.commonpath`:
   ```python
   # 1. Enforce strict ticker regex
   if not TICKER_REGEX.match(ticker.strip().upper()):
       raise HTTPException(status_code=400, detail="유효하지 않은 티커 형식입니다.")
       
   # 2. Strict directory containment
   charts_dir_abs = os.path.abspath(CHARTS_DIR)
   chart_file = os.path.abspath(os.path.join(charts_dir_abs, f"{ticker_resolved}.json"))
   if os.path.commonpath([chart_file, charts_dir_abs]) != charts_dir_abs:
       raise HTTPException(status_code=400, detail="유효하지 않은 차트 파일 경로입니다.")
   ```

---

### SEC-07: Missing Content-Security-Policy (CSP) & Incomplete Security Response Headers

- **Severity**: 🟡 **MEDIUM**
- **CWE**: CWE-693 (Protection Mechanism Failure), CWE-1021 (Improper Restriction of Rendered UI Layers)
- **Target Files & Lines**: `server.py:149-160`

#### Vulnerable Code Snippet
```python
# File: server.py (lines 149-160)
@app.middleware("http")
async def add_security_headers_middleware(request: Request, call_next):
    try:
        response = await call_next(request)
    except Exception as exc:
        response = await global_exception_handler(request, exc)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response
```

#### Detailed Explanation & Exploit Scenario
1. **Missing Content-Security-Policy (CSP)**: Modern browsers rely primarily on CSP to prevent XSS, restrict script loading to trusted origins (such as `https://unpkg.com`), and prevent unauthorized data exfiltration via unauthorized `connect-src`.
2. **Missing `Strict-Transport-Security` (HSTS)**: For HTTPS production deployments, HSTS is absent, permitting SSL-stripping attacks.
3. **Missing `Permissions-Policy`**: No policy exists to disable unused browser capabilities (e.g. camera, microphone, geolocation).
4. **WebSocket Handshakes Not Covered**: `@app.middleware("http")` in Starlette/FastAPI does not attach headers to WebSocket handshake upgrade responses.

#### Actionable Remediation
1. Add `Content-Security-Policy`, `Strict-Transport-Security`, and `Permissions-Policy` headers to the middleware:
   ```python
   response.headers["X-Content-Type-Options"] = "nosniff"
   response.headers["X-Frame-Options"] = "DENY"
   response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
   response.headers["X-XSS-Protection"] = "1; mode=block"
   response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
   response.headers["Content-Security-Policy"] = (
       "default-src 'self'; "
       "script-src 'self' 'unsafe-inline' https://unpkg.com; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; "
       "connect-src 'self' ws: wss: http://localhost:8000 http://127.0.0.1:8000; "
       "img-src 'self' data:; "
       "frame-ancestors 'none';"
   )
   ```

---

### SEC-08: Database Schema Disconnect & Missing Endpoint Method (`trade_history` & `get_trade_history_records`)

- **Severity**: 🟡 **MEDIUM**
- **CWE**: CWE-754 (Improper Check for Unusual or Exceptional Conditions), CWE-404 (Improper Resource Shutdown or Release)
- **Target Files & Lines**: `al_sangmoo/domain/risk/portfolio_guardian.py:241-251`, `al_sangmoo/interfaces/api/routers/portfolio.py:284`, `al_sangmoo/infrastructure/persistence.py:48-131`

#### Vulnerable Code Snippet
```python
# File: al_sangmoo/domain/risk/portfolio_guardian.py (lines 241-251)
# Insert partial trade record to history
cursor.execute("""
INSERT INTO trade_history (
    holding_id, ticker, buy_date, sell_date, buy_price,
    sell_price, quantity, pnl_pct, pnl_amount, reason, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", ...)
```

```python
# File: al_sangmoo/interfaces/api/routers/portfolio.py (lines 281-285)
@router.get("/history")
def get_portfolio_history():
    """Returns all past closed trades and realized returns."""
    return db_manager.get_trade_history_records()
```

#### Detailed Explanation & Exploit Scenario
1. In `portfolio_guardian.py:241`, when a partial take-profit (+15%) occurs, the daemon attempts to insert a record into the `trade_history` SQLite table.
2. In `persistence.py:init_database()`, the tables created are `my_portfolio`, `recommendation_matrix`, `trades`, and `macro_history`. Table `trade_history` is **never created**.
3. When `PortfolioGuardian` triggers a partial TP, SQLite raises `sqlite3.OperationalError: no such table: trade_history`, aborting the database transaction.
4. In `portfolio.py:284`, `GET /api/portfolio/history` invokes `db_manager.get_trade_history_records()`. This function does not exist in `db_manager.py` or `persistence.py`, raising an unhandled `AttributeError` and returning an HTTP 500 error.

#### Actionable Remediation
1. Add `trade_history` table creation to `persistence.py:init_database()`:
   ```sql
   CREATE TABLE IF NOT EXISTS trade_history (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       holding_id INTEGER,
       ticker TEXT NOT NULL,
       buy_date TEXT,
       sell_date TEXT,
       buy_price REAL,
       sell_price REAL,
       quantity REAL,
       pnl_pct REAL,
       pnl_amount REAL,
       reason TEXT,
       created_at TEXT
   );
   ```
2. Implement `get_trade_history_records()` in `persistence.py` and export it in `db_manager.py`:
   ```python
   def get_trade_history_records() -> list:
       init_database()
       with get_connection() as conn:
           cursor = conn.cursor()
           cursor.execute("SELECT * FROM my_portfolio WHERE status = 'SOLD' ORDER BY sell_date DESC, id DESC")
           return [dict(r) for r in cursor.fetchall()]
   ```

---

### SEC-09: Hardcoded Personal Email Address in Source Code (Information Disclosure / PII)

- **Severity**: 🟢 **LOW**
- **CWE**: CWE-798 (Use of Hard-coded Credentials), CWE-200 (Exposure of Sensitive Information)
- **Target Files & Lines**: `al_sangmoo_daily_bot.py:42`

#### Vulnerable Code Snippet
```python
# File: al_sangmoo_daily_bot.py (line 42)
DEFAULT_EMAIL_RECEIVER = os.environ.get("ALERT_EMAIL_RECEIVER") or os.environ.get("EMAIL_RECEIVER") or "kdw58170425@gmail.com"
```

#### Detailed Explanation & Exploit Scenario
1. A personal developer email address (`kdw58170425@gmail.com`) is hardcoded as the default fallback recipient for daily quant briefing emails.
2. In public or shared repositories, hardcoding developer email addresses exposes Personally Identifiable Information (PII) and risks spam, targeted phishing, or credential stuffing attacks against developer accounts.

#### Actionable Remediation
1. Remove the hardcoded fallback email and require explicit environment variable configuration:
   ```python
   DEFAULT_EMAIL_RECEIVER = os.environ.get("ALERT_EMAIL_RECEIVER") or os.environ.get("EMAIL_RECEIVER")
   ```

---

### SEC-10: Fragile Inline `onclick` String Interpolations in UI Rendering

- **Severity**: 🟢 **LOW**
- **CWE**: CWE-79 (Cross-Site Scripting), CWE-116 (Improper Encoding or Escaping of Output)
- **Target Files & Lines**: `frontend/js/ui.js:153, 216, 236, 294`

#### Vulnerable Code Snippet
```javascript
// File: frontend/js/ui.js (lines 153, 216, 236, 294)
// Line 153:
onclick="window.TerminalUI.selectStock('${this.escapeHtml(h.ticker)}', ${curPrice})"

// Line 216:
onclick="window.TerminalUI.selectStock('${ticker}', ${price})"

// Line 236:
onclick="event.stopPropagation(); window.TerminalUI.executeQuickBuy('${ticker}', ${price}, ${shares}, ${allocUsd}, ${allocKrw})"

// Line 294:
onclick="window.TerminalUI.selectStock('${safeTicker}', ${safeCurrentPrice})"
```

#### Detailed Explanation & Exploit Scenario
1. Passing dynamic variables into JavaScript code within inline HTML attributes (`onclick="..."`) creates brittle escaping boundaries. If a ticker or symbol contains single quotes, backslashes, or control characters, the inline JavaScript string terminates prematurely, causing syntax errors or script injection.
2. Best practice for modern frontend architectures is to use `data-ticker="..."` and `data-price="..."` attributes and attach structured event listeners via event delegation (similar to the secure pattern implemented for `.btn-exit-holding` in `ui.js:311-326`).

#### Actionable Remediation
1. Refactor inline `onclick` handlers to use `data-*` attributes and delegated event listeners:
   ```javascript
   // Template:
   <div class="slot-card occupied" data-ticker="${safeTicker}" data-price="${curPrice}">
   
   // Delegation in UI.init():
   grid.addEventListener('click', (e) => {
       const card = e.target.closest('.slot-card.occupied');
       if (card) {
           const ticker = card.getAttribute('data-ticker');
           const price = parseFloat(card.getAttribute('data-price'));
           UI.selectStock(ticker, price);
       }
   });
   ```

---

## Verification & Independent Reproducibility

Each finding documented above was verified against the codebase:
1. **SEC-01**: Verified in `server.py:218-221` and `al_sangmoo/api/hub.py:17-35`.
2. **SEC-02**: Verified in `kis_broker.py:66, 127-136` and `.gitignore:1-25`.
3. **SEC-03**: Verified in `kis_broker.py:345` and `broker.py:28-30`.
4. **SEC-04**: Verified in `frontend/js/websocket.js:420-428`.
5. **SEC-05**: Verified in `al_sangmoo/interfaces/api/routers/broker.py:11-20, 66-68`.
6. **SEC-06**: Verified in `al_sangmoo/interfaces/api/routers/charts.py:24-29`.
7. **SEC-07**: Verified in `server.py:149-160`.
8. **SEC-08**: Verified in `portfolio_guardian.py:241-251`, `portfolio.py:284`, and `persistence.py:48-131`.
9. **SEC-09**: Verified in `al_sangmoo_daily_bot.py:42`.
10. **SEC-10**: Verified in `frontend/js/ui.js:153, 216, 236, 294`.
