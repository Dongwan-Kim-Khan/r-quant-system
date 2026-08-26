# Al-Sangmoo Quant Terminal: Comprehensive Multi-Agent System Audit Report

**Target Codebase**: `d:\코딩\R` (Al-Sangmoo Institutional Quant Platform)  
**Audit Type**: Full System Static Code Analysis, Architecture Inspection & UX Assessment  
**Audit Mode**: Strictly Read-Only (Zero Source Code Modifications)  
**Date**: 2026-08-25  
**Orchestrator**: Multi-Agent Dispatch Orchestrator (`orchestrator_system_audit`)  
**Specialist Auditors**:
- Domain 1 (Web & API Security): Explorer 1 (`explorer_security`)
- Domain 2 (Code Architecture & Spaghetti Code): Explorer 2 (`explorer_architecture`)
- Domain 3 (Performance & Computational Optimization): Explorer 3 (`explorer_performance`)
- Domain 4 (Broker & SSOT Data Synchronization): Explorer 4 (`explorer_sync`)
- Domain 5 (API Calling Robustness & Error Handling): Explorer 5 (`explorer_api_robustness`)
- Domain 6 (Dashboard Usability & Real-Time UX): Explorer 6 (`explorer_ux`)

---

## 1. Executive Summary

A comprehensive multi-agent technical audit was conducted across the entire Al-Sangmoo Quant Terminal platform (`d:\코딩\R`). The audit evaluated the Python backend (`FastAPI`, `SQLite`, `asyncio`, `yfinance`, `KIS OpenAPI`), automated trading and risk daemons (`PortfolioGuardian`, `AutoPilotTrader`, `Reconciliation`), and the frontend trading terminal (`HTML5`, `Lightweight Charts`, `Vanilla JS ES6 Modules`, `CSS3`).

### Overall System Health Scorecard

```
+------------------------------------------------------------------------------------------------+
| DOMAIN                                   | SCORE   | GRADE | VERDICT                           |
+------------------------------------------------------------------------------------------------+
| 1. Web & API Security                    | 74/100  | B-    | Hardened Core, Peripheral Gaps    |
| 2. Code Architecture & Modularity        | 64/100  | C     | Layer Bleeding, Monolithic Scripts|
| 3. Performance & Computational Latency   | 62/100  | C-    | Event-Loop Blocking, Scan Latency |
| 4. Broker & SSOT Data Synchronization    | 58/100  | D+    | Race Hazards, Schema Inconsistency|
| 5. API Robustness & Resilience           | 68/100  | C+    | Missing Rate-Limiting & Mutexes   |
| 6. Dashboard Usability & Real-Time UX    | 68/100  | C+    | Legacy Desync, Blocking Modals    |
+------------------------------------------------------------------------------------------------+
| COMPOSITE SYSTEM HEALTH SCORE            | 65.7/100| C     | ACTION REQUIRED PRIOR TO PROD     |
+------------------------------------------------------------------------------------------------+
```

### Key Audit Conclusions
1. **Security & Financial Risk**: Parameterized SQL queries successfully eliminate SQL injection risks across persistence. However, high-risk vectors remain in WebSocket origin validation (CSWSH), unmasked brokerage account numbers in API balance payloads, plaintext KIS token caching exposed by incomplete `.gitignore` rules, and lack of pre-trade validation on broker execution routes.
2. **Event-Loop Starvation & Concurrency**: The primary dashboard read endpoint (`GET /api/dashboard`) synchronously executes external network requests and SQLite write transactions on FastAPI's main asyncio event loop. When combined with a rogue 15-second frontend HTTP polling timer, this freezes the server for 1.5s–5.0s on concurrent reads.
3. **Database Schema Disconnect**: `PortfolioGuardian` attempts to insert partial profit-taking records into a non-existent `trade_history` SQLite table, causing transaction rollbacks on partial exits. Concurrently, `GET /api/portfolio/history` calls a non-existent facade method, returning HTTP 500.
4. **Race Hazards in Order Placement**: Order placement routes execute network TR calls to KIS *before* local SQLite insertion without concurrency locks, allowing concurrent requests to double-spend cash balances, exceed 3-slot allocation limits, and create untracked real-money positions if local commits fail.
5. **Quantitative Regime Bug**: In `generate_dashboard_feed.py:462`, MSI macro score extraction queries `"msi"` instead of `"msi_score"`, permanently defaulting to `50.0` (`is_bull_regime = True`). This blinds the conviction ranker to actual Bear/Defense regimes (e.g. MSI 72.8).
6. **Frontend Usability & Trading Safety**: The console quick-buy feature executes live trades with zero confirmation prompts or debounce protection, while other actions trigger native browser `alert()`/`confirm()` dialogs that freeze the JavaScript event loop and halt WebSocket heartbeats.

---

## 2. Master Severity Classification Matrix

Across the 6 audit domains, **59 discrete findings** were identified and cataloged:
- 🔴 **Critical Severity**: 6 findings
- 🟠 **High Severity**: 18 findings
- 🟡 **Medium Severity**: 26 findings
- 🔵 **Low Severity**: 9 findings

```
+----------------------------------------------------------------------------------------------------------------------------------------------------+
| ID       | SEVERITY | DOMAIN         | PRIMARY FILE(S) & LINES                                        | TITLE / SUMMARY                                    |
+----------------------------------------------------------------------------------------------------------------------------------------------------+
| PERF-01  | CRITICAL | Performance    | routers/dashboard.py:44, persistence.py:328-430               | Sync Network I/O & SQLite Writes on Async EventLoop|
| SYNC-01  | CRITICAL | Data Sync      | portfolio_guardian.py:241-251, persistence.py:48-131           | Missing trade_history DDL Schema & Broken API Facade|
| SYNC-02  | CRITICAL | Data Sync      | persistence.py:422-426, routers/dashboard.py:42-45             | Stale Price Sync Overwrite Race on Sold Positions  |
| API-01   | CRITICAL | API Robustness | kis_broker.py:272-340, 354-385, 472-543                       | Missing Client Rate Limiter & Token Throttling (KIS)|
| API-02   | CRITICAL | API Robustness | routers/portfolio.py:101-171, 173-237                          | Non-Atomic Dual-Write Race: KIS Broker vs SQLite   |
| UX-01    | CRITICAL | Dashboard UX   | al_sangmoo_dashboard.html:539-540, frontend/js/ui.js:25-42     | Runtime TypeError & Total DOM Desync in Legacy HTML|
+----------------------------------------------------------------------------------------------------------------------------------------------------+
| SEC-01   | HIGH     | Security       | server.py:218-221, hub.py:17-35                               | Insecure WebSocket Origin Validation (CSWSH)       |
| SEC-02   | HIGH     | Security       | kis_broker.py:66, 127-136, .gitignore:1-25                     | Plaintext KIS OAuth Token Cache Exposure           |
| SEC-04   | HIGH     | Security       | frontend/js/websocket.js:420-428                              | DOM XSS in Search Dropdown via Unescaped innerHTML |
| SEC-05   | HIGH     | Security       | routers/broker.py:11-20, 66-68                                | Missing Input & Guardrail Validation in BrokerOrder|
| ARCH-01  | HIGH     | Architecture   | domain/risk/portfolio_guardian.py:15-18, reconciliation.py:11 | Domain Layer Bleeding & Inversion of Control Breaks|
| ARCH-04  | HIGH     | Architecture   | routers/scanner.py:15-29, 44-47                               | Runtime Monkey-Patch Reflection on sys.modules     |
| ARCH-06  | HIGH     | Architecture   | generate_dashboard_feed.py:462                                | Latent Regime Inversion Bug (MSI Key Mismatch)     |
| ARCH-07  | HIGH     | Architecture   | al_sangmoo_daily_bot.py:76-80, 136-229, constants.py:5-22     | Universe Discrepancy (23 vs 60) & Double Scan Work |
| ARCH-09  | HIGH     | Architecture   | al_sangmoo_daily_bot.py (826 lines), youtube_scanner.py (504)  | Monolithic God-Scripts with Excessive Complexity   |
| ARCH-10  | HIGH     | Architecture   | routers/dashboard.py:42-46, frontend/js/websocket.js:209-215  | CQRS Violation: Read Endpoint Generates Write Locks|
| PERF-02  | HIGH     | Performance    | generate_dashboard_feed.py:92-104, al_sangmoo_daily_bot.py:136| Redundant Dual-Scan & Uncached 60-Stock Downloads   |
| PERF-03  | HIGH     | Performance    | generate_dashboard_feed.py:18-50, 430-456                     | Sequential Windows NTFS os.fsync Disk Flushes       |
| PERF-04  | HIGH     | Performance    | routers/charts.py:46, 60                                       | On-Demand Chart Fallback Freezes Async Event Loop  |
| PERF-05  | HIGH     | Performance    | portfolio_guardian.py:74-135, autopilot_trader.py:208, 221    | Periodic Event Loop Starvation in Risk Daemons     |
| SYNC-03  | HIGH     | Data Sync      | routers/dashboard.py:42-45, frontend/js/websocket.js:203-215  | Permanent Rogue 15s Polling Bombarding SQLite Writes|
| SYNC-04  | HIGH     | Data Sync      | reconciliation.py:202-230, routers/broker.py:33-39            | Reconciliation Ghost Positions on 0 Broker Balance  |
| SYNC-05  | HIGH     | Data Sync      | routers/broker.py:80-99                                        | WebSocket Desync & Ambiguous Ticker Match on Sell  |
| SYNC-06  | HIGH     | Data Sync      | routers/portfolio.py:116-155, routers/broker.py:41-100         | Unsynchronized Order Placement Race (Double-Spend)  |
| API-03   | HIGH     | API Robustness | kis_broker.py:140-177                                          | Concurrency Race on KIS Token Renewal (EGW00133)   |
| API-04   | HIGH     | API Robustness | generate_dashboard_feed.py:272-278, al_sangmoo_daily_bot.py:138| 12-Thread yfinance Bursts Causing HTTP 429 Blocks   |
| API-05   | HIGH     | API Robustness | portfolio_guardian.py:74-98, autopilot_trader.py:82-92        | 24/7 Daemons Lacking Trading Calendar / DST Awareness|
| UX-02    | HIGH     | Dashboard UX   | frontend/js/websocket.js:193-215                              | Inverted Polling Lifecycle Defeating WebSocket Push |
| UX-03    | HIGH     | Dashboard UX   | frontend/js/websocket.js:98-110, index.html:50-53              | Misleading Green Indicator on WebSocket Disconnect  |
| UX-04    | HIGH     | Dashboard UX   | frontend/js/websocket.js:338-354, frontend/js/ui.js:250-262   | Unsafe 1-Click Buy & Blocking alert()/confirm() UX  |
| UX-05    | HIGH     | Dashboard UX   | frontend/js/chart.js:42-96, index.html:231-241                 | Desynced Volume Crosshairs & Missing Intraday TFs   |
+----------------------------------------------------------------------------------------------------------------------------------------------------+
| SEC-03   | MEDIUM   | Security       | kis_broker.py:345, routers/broker.py:28-30                     | Information Disclosure: Unmasked Broker CANO       |
| SEC-06   | MEDIUM   | Security       | routers/charts.py:24-29                                        | Path Traversal Flaw via Incomplete startswith Check|
| SEC-07   | MEDIUM   | Security       | server.py:149-160                                              | Missing Content-Security-Policy & Security Headers  |
| SEC-08   | MEDIUM   | Security       | portfolio_guardian.py:241-251, routers/portfolio.py:284       | Schema Disconnect on trade_history Table           |
| ARCH-02  | MEDIUM   | Architecture   | domain/interfaces/execution_gateway.py:7-28, kis_broker.py:27  | Broken Dependency Inversion in Broker Gateway       |
| ARCH-03  | MEDIUM   | Architecture   | server.py:78-100                                               | Position Sizer NameError: 'pd' Swallowed by Except  |
| ARCH-05  | MEDIUM   | Architecture   | db_manager.py:1-25, routers/portfolio.py:11                   | Root-Level Module Coupling via db_manager Facade   |
| ARCH-08  | MEDIUM   | Architecture   | portfolio_guardian.py:165, persistence.py:415, constants.py   | Stop-Loss Divergence (-4% vs -3%) & Code Clones    |
| ARCH-11  | MEDIUM   | Architecture   | al_sangmoo_dashboard.html, backups/ (123 DB files)             | 4 Identical HTML Mirrors & Unbounded Backup Sprawl  |
| PERF-06  | MEDIUM   | Performance    | frontend/js/websocket.js:147-175, 208-215                      | Post-WebSocket Message HTTP Stampedes in Frontend  |
| PERF-07  | MEDIUM   | Performance    | hub.py:49-84                                                   | Repeated JSON Serialization & Slow-Client HoL Stalls|
| PERF-08  | MEDIUM   | Performance    | domain/quant/ichimoku.py:243-274                               | Inefficient df.iterrows() Creating 39k Series Objs |
| PERF-09  | MEDIUM   | Performance    | frontend/js/ui.js:188, 244, portfolio_guardian.py:37, 269     | DOM Layout Thrashing & Unbounded Memory List Leaks  |
| SYNC-07  | MEDIUM   | Data Sync      | routers/charts.py:12-70, routers/scanner.py:30-68              | Perpetual CHART_CACHE with No TTL & Stale Inval     |
| SYNC-08  | MEDIUM   | Data Sync      | al_sangmoo_daily_bot.py:354-390                                | Unmanaged SQLite Handle & Un-Atomic CSV Writes      |
| API-06   | MEDIUM   | API Robustness | batch_download_50_lives.py:84, urllib calls in tests           | Indefinite Socket Hang Risk via Missing Timeouts    |
| API-07   | MEDIUM   | API Robustness | server.py:130-147, routers/portfolio.py:67                     | Fragmented API Error Response Schemas Across Routes |
| API-08   | MEDIUM   | API Robustness | kis_broker.py:217-230, reconciliation.py:36-50                 | Simulated Fallback Masking Production Outages       |
| UX-06    | MEDIUM   | Dashboard UX   | frontend/css/terminal.css:11-16, frontend/js/chart.js:59-62   | Hardcoded US Colors Conflicting with KRX Psychology |
| UX-07    | MEDIUM   | Dashboard UX   | frontend/css/terminal.css:184-188, frontend/js/ui.js:292-307   | Left-Aligned Tabular Numerals (Ragged Decimals)     |
| UX-08    | MEDIUM   | Dashboard UX   | frontend/js/ui.js:126-189, frontend/css/terminal.css:315-330   | Hardcoded $2,500 Slot Capital & CLS Layout Shifts   |
| UX-09    | MEDIUM   | Dashboard UX   | frontend/index.html:19-57, frontend/css/terminal.css:34-43     | Header Element Wrapping on < 1580px Viewports       |
+----------------------------------------------------------------------------------------------------------------------------------------------------+
| SEC-09   | LOW      | Security       | al_sangmoo_daily_bot.py:42                                     | Hardcoded Developer Email Address (PII Leakage)    |
| SEC-10   | LOW      | Security       | frontend/js/ui.js:153, 216, 236, 294                           | Inline onclick String Interpolations in UI          |
| PERF-10  | LOW      | Performance    | al_sangmoo_dashboard.html:539-540                              | Missing Legacy Init Functions (TypeError)          |
| SYNC-09  | LOW      | Data Sync      | paper_broker.py:10-77, persistence.py:303-311                  | Account Sizing Divergence ($100k vs $7.5k)         |
| API-09   | LOW      | API Robustness | kis_broker.py:382-384, persistence.py:355-356                  | Silent Error Swallowing in Auxiliary Price Inquiries|
| UX-10    | LOW      | Dashboard UX   | frontend/js/websocket.js:40-46, frontend/js/chart.js:99-130   | Stale Chart Series Retained on Browser Wake-Up     |
+----------------------------------------------------------------------------------------------------------------------------------------------------+
```

---

## 3. Domain Deep Dives

---

### Domain 1: Web & API Security Vulnerability Assessment

#### SEC-01: Insecure WebSocket Origin Validation & Lack of Handshake Authentication (CSWSH)
- **Severity**: 🔴 **HIGH** | **CWE**: CWE-1385, CWE-306
- **Files & Lines**: `server.py:218-221`, `al_sangmoo/api/hub.py:17-35`
- **Vulnerable Code**:
  ```python
  # server.py (lines 218-221)
  origin = websocket.headers.get("origin")
  if origin and origin not in ALLOWED_ORIGINS and not origin.startswith("http://localhost:") and not origin.startswith("http://127.0.0.1:"):
      await websocket.close(code=1008, reason="Forbidden Origin")
      return
  ```
- **Root Cause & Risk**:
  1. If the `Origin` header is absent (e.g. non-browser client or modified proxy), `if origin and ...` evaluates to False and skips origin enforcement entirely.
  2. `origin.startswith("http://localhost:")` permits connections from *any port* on localhost. A malicious local service (e.g. malicious script on port 8080 or compromised Jupyter notebook on 8888) can connect to the trading WebSocket without hindrance.
  3. Upon connection (`server.py:227`), the server immediately transmits the full live portfolio (`{"type": "connected", "data": {"portfolio": p_data}}`), allowing unauthorized local sites to exfiltrate active holdings, purchase prices, and trading alerts.
- **Remediation**:
  Enforce exact set membership matching:
  ```python
  ALLOWED_WS_ORIGINS = {
      "http://localhost:8000", "http://127.0.0.1:8000",
      "http://localhost:3000", "http://127.0.0.1:3000"
  }
  origin = websocket.headers.get("origin")
  if not origin or origin not in ALLOWED_WS_ORIGINS:
      await websocket.close(code=1008, reason="Unauthorized WebSocket Origin")
      return
  ```

#### SEC-02: Exposure of Plaintext KIS OAuth 2.0 Tokens via Incomplete `.gitignore`
- **Severity**: 🔴 **HIGH** | **CWE**: CWE-522, CWE-312
- **Files & Lines**: `al_sangmoo/infrastructure/brokers/kis_broker.py:66, 127-136`, `.gitignore:1-25`
- **Vulnerable Code**:
  ```python
  # kis_broker.py: lines 66, 127-136
  self.cache_file = TOKEN_CACHE_DIR / f".kis_token_{self.mode_str}.json"
  # Writes bearer token to data/.kis_token_prod.json
  ```
  ```gitignore
  # .gitignore: lines 127-130
  data/charts/PRN.json
  data/charts/NUL.json
  # data/ or .kis_token_*.json is NOT ignored!
  ```
- **Root Cause & Risk**: The bearer token cache file generated by `kis_broker.py` contains active 24-hour KIS OAuth 2.0 access tokens capable of executing live orders. Because `.gitignore` only ignores specific chart filenames, executing `git add .` commits plaintext broker credentials to version control.
- **Remediation**: Add `.kis_token_*.json`, `data/.kis_token_*.json`, and `data/` to `.gitignore`.

#### SEC-04: DOM XSS in Frontend Search Dropdown via Unescaped `innerHTML`
- **Severity**: 🔴 **HIGH** | **CWE**: CWE-79
- **Files & Lines**: `frontend/js/websocket.js:420-428`
- **Vulnerable Code**:
  ```javascript
  // frontend/js/websocket.js:420-424
  dropdown.innerHTML = results.map(r => `
      <div class="search-item" data-ticker="${r.ticker}" data-price="${r.price || 0}">
          <div>
              <strong style="color:#f8fafc;">${r.ticker}</strong>
              <span style="color:#94a3b8;">${r.name || ''}</span>
          </div>
      </div>
  `).join('');
  ```
- **Root Cause & Risk**: While `ui.js` sanitizes portfolio tables with `escapeHtml()`, `websocket.js:420` directly concatenates stock search results into `dropdown.innerHTML`. If custom ticker resolvers or upstream stock name directories contain crafted script tags, arbitrary JavaScript executes in the user's trading terminal session.
- **Remediation**: Pass `r.ticker` and `r.name` through `UI.escapeHtml()` prior to interpolation.

#### SEC-05: Missing Input & Pre-Trade Guardrail Validation in `BrokerOrderRequest`
- **Severity**: 🔴 **HIGH** | **CWE**: CWE-20, CWE-840
- **Files & Lines**: `al_sangmoo/interfaces/api/routers/broker.py:11-20, 66-68`
- **Vulnerable Code**:
  ```python
  class BrokerOrderRequest(BaseModel):
      ticker: str = Field(..., min_length=1, max_length=15) # Missing TICKER_REGEX
      side: Optional[str] = Field(default="BUY")             # Missing Literal["BUY", "SELL"]
      price: Optional[float] = Field(default=0.0, ge=0.0)    # Missing upper bound
  ```
- **Root Cause & Risk**: `BrokerOrderRequest` lacks strict regex validation (`TICKER_REGEX`, `DATE_REGEX`) and enum typing (`Literal["BUY", "SELL"]`). If `side` is passed as lowercase or padded with whitespace, the pre-trade check `if not eval_res["allowed"] and final_side == "BUY"` in `broker.py:66` can be bypassed.
- **Remediation**: Apply `field_validator` with `TICKER_REGEX` and `Literal` constraints to `BrokerOrderRequest`.

#### SEC-06: Path Traversal Flaw via Incomplete `startswith` Check in Chart Router
- **Severity**: 🟡 **MEDIUM** | **CWE**: CWE-22
- **Files & Lines**: `al_sangmoo/interfaces/api/routers/charts.py:24-29`
- **Vulnerable Code**:
  ```python
  chart_file = os.path.abspath(os.path.join(CHARTS_DIR, f"{ticker_resolved}.json"))
  charts_dir_abs = os.path.abspath(CHARTS_DIR)
  if not chart_file.startswith(charts_dir_abs): # Sibling directory traversal flaw
      raise HTTPException(status_code=400, detail="유효하지 않은 차트 파일 경로입니다.")
  ```
- **Root Cause & Risk**: String prefix checking (`chart_file.startswith("/app/data/charts")`) allows sibling directory paths such as `/app/data/charts_backup/secret.json` to pass the check.
- **Remediation**: Use `os.path.commonpath([chart_file, charts_dir_abs]) != charts_dir_abs` and enforce `TICKER_REGEX`.

---

### Domain 2: Code Architecture & Spaghetti Code Audit

#### ARCH-01: Domain Layer Bleeding & Inversion of Control Violations
- **Severity**: 🟠 **HIGH** | **Files & Lines**: `al_sangmoo/domain/risk/portfolio_guardian.py:15-18`, `al_sangmoo/domain/risk/autopilot_trader.py:16-18`, `al_sangmoo/domain/reconciliation.py:11-16`
- **Vulnerable Code**:
  ```python
  # al_sangmoo/domain/risk/portfolio_guardian.py:15-18
  import db_manager
  from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
  from al_sangmoo.api.hub import hub
  # Executes SQL UPDATEs, manages asyncio task loops, reads JSON files
  ```
- **Root Cause & Risk**: Modules inside `al_sangmoo/domain/` act as active background daemons. They directly import concrete database managers, concrete broker singletons, and WebSocket hubs, executing raw SQL and network I/O. This breaks Clean Architecture and prevents automated unit testing without spinning up full databases and broker connections.
- **Remediation**: Extract pure mathematical risk evaluation functions into `domain/risk/` and move daemon schedulers to `al_sangmoo/services/` (e.g. `guardian_service.py`, `autopilot_service.py`), injecting repositories and gateways via interfaces.

#### ARCH-03: Presentation Layer Bleeding & Swallowed `NameError` in `server.py`
- **Severity**: 🟡 **MEDIUM** | **Files & Lines**: `server.py:78-100`
- **Vulnerable Code**:
  ```python
  # server.py (lines 83, 146-155)
  if isinstance(df.columns, pd.MultiIndex): # BUG: 'pd' is NEVER imported in server.py!
      df.columns = df.columns.get_level_values(0)
  ...
  except Exception:
      cur_price = 100.0
      atr = 2.0
  ```
- **Root Cause & Risk**: `server.py` embeds quantitative position sizing logic. Line 83 evaluates `isinstance(df.columns, pd.MultiIndex)`, but `pandas as pd` was never imported. This raises `NameError: name 'pd' is not defined`, which is immediately swallowed by broad `except Exception:`, silently returning fake dummy numbers (`cur_price = 100.0, atr = 2.0`) on every execution.
- **Remediation**: Remove `get_recommended_position_size` from `server.py` and delegate position sizing to `al_sangmoo/domain/risk/position_sizer.py`.

#### ARCH-04: Runtime Monkey-Patch State Coupling in `scanner.py`
- **Severity**: 🟠 **HIGH** | **Files & Lines**: `al_sangmoo/interfaces/api/routers/scanner.py:15-29, 44-47`
- **Vulnerable Code**:
  ```python
  if "server" in sys.modules:
      s = sys.modules["server"]
      if hasattr(s, "_is_scanning"):
          return getattr(s, "_is_scanning")
  ```
- **Root Cause & Risk**: `scanner.py` dynamically inspects and mutates attributes on `sys.modules["server"]` at runtime to check and toggle scan state. This runtime monkey-patching anti-pattern introduces fragile hidden coupling and race conditions.
- **Remediation**: Encapsulate scan state into an explicit `ScanStateManager` singleton or FastAPI dependency provider (`Depends`).

#### ARCH-06: Latent Regime Inversion Bug (MSI Key Mismatch) in Feed Generator
- **Severity**: 🟠 **HIGH** | **Files & Lines**: `generate_dashboard_feed.py:462`
- **Vulnerable Code**:
  ```python
  # generate_dashboard_feed.py:462
  msi_val = float(macro_info.get("msi", 50.0)) # BUG: key in wepoll JSON is "msi_score" inside "macro_climate"!
  is_bull_regime = (msi_val < 65.0)            # 50.0 < 65.0 -> ALWAYS True!
  ```
- **Root Cause & Risk**: In `wepoll_latest_stream.json`, MSI is stored as `macro_info["macro_climate"]["msi_score"]` (e.g. `72.8`). In line 462, `macro_info.get("msi", 50.0)` is queried. Because `"msi"` does not exist at top level, `msi_val` permanently defaults to `50.0`, forcing `is_bull_regime = True` unconditionally. Even during severe macro defense regimes (MSI 72.8), the conviction ranker allocates 3 full aggressive slots instead of activating the 50% cash preservation defense.
- **Remediation**: Update key extraction to `msi_val = float(macro_climate.get("msi_score", 50.0))`.

#### ARCH-07: Universe Discrepancy & Double Computation Pipeline
- **Severity**: 🟠 **HIGH** | **Files & Lines**: `al_sangmoo_daily_bot.py:76-80, 136-229`, `al_sangmoo/core/constants.py:5-22`
- **Root Cause & Risk**: `al_sangmoo_daily_bot.py` scans a legacy hardcoded 23-ticker list in `scan_and_select_2x2x2()`. Then in `main()`, it calls `generate_dashboard_feed.build_dashboard_data()`, which downloads and scores all 60 tickers from `constants.py` again. This causes double execution, 83+ un-pooled HTTP requests, and conflicting recommendation outputs.
- **Remediation**: Deprecate the 23-ticker scanning loop and delegate batch calculations to the canonical 60-ticker feed engine.

---

### Domain 3: Performance & Computational Optimization

#### PERF-01: Synchronous Network I/O & SQLite Writes on Async Event Loop in `GET /api/dashboard`
- **Severity**: 🔴 **CRITICAL** | **Files & Lines**: `al_sangmoo/interfaces/api/routers/dashboard.py:44`, `al_sangmoo/infrastructure/persistence.py:328-430`
- **Vulnerable Code**:
  ```python
  # dashboard.py: lines 41-45
  @router.get("/api/dashboard")
  async def get_dashboard_data():
      ...
      import db_manager
      live_portfolio = db_manager.sync_portfolio_prices() # Synchronous KIS/Yahoo calls + SQL UPDATEs!
      feed_out["portfolio"] = live_portfolio
  ```
- **Root Cause & Risk**: The primary dashboard read endpoint (`GET /api/dashboard`) is an `async def` route running on FastAPI's main asyncio event loop. Calling `sync_portfolio_prices()` executes synchronous KIS REST calls, yfinance downloads, and SQLite batch updates directly on the event-loop thread. Under multi-client traffic, the event loop freezes for **1.5s–5.0s**, dropping WebSocket heartbeats and stalling concurrent API requests.
- **Remediation**: Refactor `get_dashboard_data()` to call `db_manager.get_live_portfolio()`, a pure, non-blocking in-memory/SQLite read query (< 5ms). Offload price synchronization strictly to background worker tasks.

#### PERF-02: Redundant Dual-Scan & Uncached 60-Universe yfinance Downloads
- **Severity**: 🟠 **HIGH** | **Files & Lines**: `generate_dashboard_feed.py:92-104`, `al_sangmoo_daily_bot.py:136-218`, `al_sangmoo/interfaces/api/routers/scanner.py:38-50`
- **Root Cause & Risk**: Background scans execute 23 sequential yfinance downloads in `scan_and_select_2x2x2`, followed by 60 parallel downloads in `build_dashboard_data`, followed by position evaluation downloads. This issues **83+ un-pooled HTTP requests** taking 25s–45s per scan and risking Yahoo Finance HTTP 429 IP bans.
- **Remediation**: Unify scanning into a single 60-ticker pass and implement a local 15-minute OHLCV cache.

#### PERF-03: Sequential Windows NTFS `os.fsync()` Disk Flushes in Chart Writer
- **Severity**: 🟠 **HIGH** | **Files & Lines**: `generate_dashboard_feed.py:18-50, 430-456`
- **Vulnerable Code**:
  ```python
  for ticker, c_obj in chart_data.items():
      atomic_save_json(ticker_chart_path, c_obj) # Invokes os.fsync(tf.fileno()) 60 times sequentially!
  ```
- **Root Cause & Risk**: On Windows NTFS filesystems, `os.fsync()` blocks until physical storage acknowledges disk persistence (10ms–30ms per file). Executing 60 sequential `fsync` calls adds **600ms–1800ms of blocking I/O** to every feed generation cycle.
- **Remediation**: Save chart files concurrently using `ThreadPoolExecutor(max_workers=8)` and compact JSON (`indent=None`).

#### PERF-04: On-Demand Chart Fallback Freezing Async Event Loop
- **Severity**: 🟠 **HIGH** | **Files & Lines**: `al_sangmoo/interfaces/api/routers/charts.py:46, 60`
- **Root Cause & Risk**: When a user queries a ticker whose chart is not cached on disk, `GET /api/chart/{ticker}` synchronously runs `compute_all_indicators()` (3-year yfinance download + indicator math + `atomic_save_json`) on the event loop without `asyncio.to_thread`, freezing the entire backend for 2.0s–4.5s.
- **Remediation**: Wrap `compute_all_indicators` and `atomic_save_json` in `await asyncio.to_thread(...)`.

#### PERF-08: Inefficient `df.iterrows()` Creating 39,000 Series Objects
- **Severity**: 🟡 **MEDIUM** | **Files & Lines**: `al_sangmoo/domain/quant/ichimoku.py:243-274`
- **Root Cause & Risk**: `build_ichimoku_series_payload` iterates over historical bars using `df.iterrows()`, instantiating ~39,000 temporary Series objects across 60 tickers and consuming ~1.1s of unnecessary CPU overhead.
- **Remediation**: Replace `df.iterrows()` with `df.itertuples()` or numpy dictionary conversions (25x speedup).

---

### Domain 4: Broker & SSOT Data Synchronization Audit

#### SYNC-01: Missing `trade_history` DDL Schema & Broken History API Facade
- **Severity**: 🔴 **CRITICAL** | **Files & Lines**: `al_sangmoo/domain/risk/portfolio_guardian.py:241-251`, `al_sangmoo/infrastructure/persistence.py:43-132`, `al_sangmoo/interfaces/api/routers/portfolio.py:281-285`, `db_manager.py:5-24`
- **Vulnerable Code**:
  ```python
  # portfolio_guardian.py: lines 241-248
  cursor.execute("""
  INSERT INTO trade_history (
      holding_id, ticker, buy_date, sell_date, buy_price,
      sell_price, quantity, pnl_pct, pnl_amount, reason, created_at
  ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  """, (...))
  ```
  ```python
  # portfolio.py: line 284
  @router.get("/history")
  def get_portfolio_history():
      return db_manager.get_trade_history_records() # AttributeError: module has no attribute!
  ```
- **Root Cause & Risk**:
  1. `persistence.py:init_database()` creates tables for `my_portfolio`, `recommendation_matrix`, `trades`, and `macro_history`, but **never creates `trade_history`**.
  2. When `PortfolioGuardian` executes a 50% partial take-profit, SQLite raises `sqlite3.OperationalError: no such table: trade_history`, rolling back the entire transaction. The position remains unreduced, causing the guardian to retry and fail on every cycle.
  3. `GET /api/portfolio/history` invokes `db_manager.get_trade_history_records()`, which does not exist in `db_manager.py` or `persistence.py`, raising an unhandled `AttributeError` (HTTP 500).
- **Remediation**: Add `CREATE TABLE IF NOT EXISTS trade_history (...)` to `init_database()` and implement `get_trade_history_records()` in `persistence.py`.

#### SYNC-02: Stale Price Sync Overwrite Race Condition on Sold Positions
- **Severity**: 🔴 **CRITICAL** | **Files & Lines**: `al_sangmoo/infrastructure/persistence.py:422-426`, `al_sangmoo/interfaces/api/routers/dashboard.py:42-45`
- **Vulnerable Code**:
  ```python
  # persistence.py: lines 422-426
  cursor.executemany("""
  UPDATE my_portfolio
  SET current_price = ?, current_value = ?, pnl_pct = ?, pnl_amount = ?, exit_advice = ?
  WHERE id = ?
  """, update_rows) # MISSING: "AND status = 'HOLDING'"
  ```
- **Root Cause & Risk**: `sync_portfolio_prices()` fetches open holdings, releases the database connection, performs 1-5 seconds of network I/O, and then updates rows by ID. If a holding is sold (`status = 'SOLD'`) during the network fetch, the batch update executes without checking status, overwriting `exit_advice` back to active holding advice and corrupting closed trade records.
- **Remediation**: Append `AND status = 'HOLDING'` to the `WHERE` clause.

#### SYNC-04: Reconciliation Ghost Positions on Zero Broker Balance
- **Severity**: 🟠 **HIGH** | **Files & Lines**: `al_sangmoo/domain/reconciliation.py:212-229`
- **Vulnerable Code**:
  ```python
  if auto_calibrate and len(broker_holdings) > 0:
      cursor.execute("UPDATE my_portfolio SET status = 'SOLD' ... WHERE id = ?", ...)
  else:
      logger.warning(f"Preserved local position for {ticker} (Broker returned 0 total holdings)")
  ```
- **Root Cause & Risk**: When an account is completely liquidated on the broker (`len(broker_holdings) == 0`), the guard `len(broker_holdings) > 0` prevents local position closure. The liquidated positions remain as `HOLDING` in local SQLite indefinitely. Furthermore, the calibration SQL query fails to set `sell_price`, `pnl_pct`, and `pnl_amount`.
- **Remediation**: Distinguish legitimate 0-holding balances from API errors and persist complete liquidation metrics upon auto-calibration.

#### SYNC-06: Unsynchronized Order Placement Race Condition (Double-Spend / Slot Breach)
- **Severity**: 🟠 **HIGH** | **Files & Lines**: `al_sangmoo/interfaces/api/routers/portfolio.py:116-155`, `al_sangmoo/interfaces/api/routers/broker.py:41-100`
- **Root Cause & Risk**: Concurrent buy order requests (e.g. rapid UI double-clicks or simultaneous manual and Autopilot orders) validate pre-trade guardrails simultaneously against the same cash balance. Both submit orders to KIS and insert records into `my_portfolio`, resulting in cash double-spending and opening 4+ holdings on a 3-slot system.
- **Remediation**: Wrap order validation and execution pipelines in an `asyncio.Lock()`.

---

### Domain 5: API Calling Robustness & Error Handling

#### API-01: Missing Client-Side Rate Limiter & Token Throttling in KIS Broker Gateway
- **Severity**: 🔴 **CRITICAL** | **Files & Lines**: `al_sangmoo/infrastructure/brokers/kis_broker.py:272-340, 354-385, 472-543`
- **Vulnerable Code**:
  ```python
  # kis_broker.py: lines 515-539 (_place_overseas_order)
  res = requests.post(url, json=body, headers=headers, timeout=12)
  data = res.json()
  if res.status_code == 200 and data.get("rt_cd") == "0":
      return {"status": "submitted", ...}
  else:
      # Fails immediately without retry or backoff on EGW00201!
      return {"status": "rejected", "code": data.get("msg_cd"), "reason": data.get("msg1")}
  ```
- **Root Cause & Risk**: KIS enforces a strict rate limit of **2 to 5 TPS** for Overseas Trading / VPS. Querying multi-exchange balances or rapid price polling generates bursts that exceed this limit. When `place_order` encounters `EGW00201` (초당 처리건수 초과), it immediately rejects the order without backoff or retries, dropping stop-loss sell orders during market volatility.
- **Remediation**: Implement a thread-safe `TokenBucketLimiter` capped at 3.5 TPS with exponential backoff and full jitter on TR retryable errors.

#### API-02: Non-Atomic Dual-Write Race Hazard: KIS Broker Execution vs SQLite Persistence
- **Severity**: 🔴 **CRITICAL** | **Files & Lines**: `al_sangmoo/interfaces/api/routers/portfolio.py:134-195`
- **Vulnerable Code**:
  ```python
  # Step 1: External broker order submitted over the wire FIRST
  broker_res = default_kis_broker.place_order(ticker=ticker_clean, side="BUY", qty=qty, price=price)
  if broker_res.get("status") not in ("submitted", "filled"):
      raise HTTPException(status_code=400, detail="증권사 주문 거부")

  # Step 2: Local SQLite DB updated SECOND
  # If SQLite is locked or throws OperationalError, endpoint crashes with 500!
  inserted_id = db_manager.add_portfolio_buy(ticker=ticker_clean, buy_price=price, quantity=float(qty))
  ```
- **Root Cause & Risk**: If Step 1 succeeds on KIS but Step 2 fails due to a locked SQLite database, FastAPI throws HTTP 500. The user assumes the order failed and clicks "Buy" again, doubling their position. Meanwhile, the first trade was never recorded in SQLite and will never be tracked by `PortfolioGuardian` for stop-loss protection.
- **Remediation**: Implement an Outbox / Pre-Allocation Pattern (`status = 'PENDING_BROKER_SUBMISSION'`) prior to submitting the broker order.

#### API-03: Concurrency Race Hazard on KIS OAuth Token Renewal (`EGW00133` Lockout)
- **Severity**: 🟠 **HIGH** | **Files & Lines**: `al_sangmoo/infrastructure/brokers/kis_broker.py:140-177`
- **Root Cause & Risk**: KIS restricts token issuance to 1 per minute per account. When a token expires after 24 hours, multiple concurrent tasks (`Guardian`, `AutoPilot`, incoming API requests) simultaneously enter `authenticate()`. Without a mutex, multiple requests hit `/oauth2/tokenP`, triggering `EGW00133` lockout. Furthermore, missing HTTP 401 hooks prevent automatic re-authentication upon unexpected token revocation.
- **Remediation**: Protect `authenticate()` with a `threading.RLock()` / `asyncio.Lock()` and add a 401 response interceptor.

#### API-05: 24/7 Polling Daemons Lacking Trading Calendar & DST Awareness
- **Severity**: 🟠 **HIGH** | **Files & Lines**: `al_sangmoo/domain/risk/portfolio_guardian.py:74-98`, `al_sangmoo/domain/risk/autopilot_trader.py:82-92`
- **Root Cause & Risk**: `PortfolioGuardian` polls KIS live price TRs every 10 seconds 24/7/365, spamming error logs on weekends and holidays when KIS servers are down (`EGW00205`). Additionally, hardcoding `22:30 ~ 06:00 KST` ignores Daylight Saving Time (EDT opens at `21:30 KST`), missing the first hour of trading from March to November.
- **Remediation**: Implement an institutional market calendar provider with DST detection and adaptive polling intervals (10s in market, 300s off-market).

---

### Domain 6: Dashboard Usability & Real-Time UX Inspection

#### UX-01: Total UI/DOM Desync & Runtime Exceptions in Legacy Dashboard (`al_sangmoo_dashboard.html`)
- **Severity**: 🔴 **CRITICAL** | **Files & Lines**: `al_sangmoo_dashboard.html:199-250, 539-540`, `frontend/js/ui.js:25-42`
- **Vulnerable Code**:
  ```javascript
  // al_sangmoo_dashboard.html:539-540
  UI.initDelegation(); // CRASH: TypeError: UI.initDelegation is not a function!
  UI.initSearch();     // CRASH: TypeError: UI.initSearch is not a function!
  ```
- **Root Cause & Risk**: Navigating to `/legacy` or opening `al_sangmoo_dashboard.html` immediately throws an unhandled `TypeError` because `ui.js` no longer exports `initDelegation`/`initSearch`. Furthermore, `ui.js:renderDashboard()` attempts to update DOM element IDs (`#slotVisualizerGrid`, `#topPicksContainer`) that do not exist in the legacy HTML, leaving all quantitative data permanently frozen on "Loading...".
- **Remediation**: Redirect `/legacy` permanently (`HTTP 301 / 307`) to `/` (`frontend/index.html`).

#### UX-02: Inverted HTTP Polling Lifecycle Defeating WebSocket Push Architecture
- **Severity**: 🟠 **HIGH** | **Files & Lines**: `frontend/js/websocket.js:193-215`
- **Vulnerable Code**:
  ```javascript
  _stopHttpPolling() {
      if (this.pollingInterval) clearInterval(this.pollingInterval);
      // BUG: Immediately re-creates a 15-second polling interval!
      this.pollingInterval = setInterval(async () => {
          const fresh = await ApiClient.getDashboardData();
          if (fresh) UI.renderDashboard(fresh);
      }, 15000);
  }
  ```
- **Root Cause & Risk**: When WebSocket connects, `_stopHttpPolling()` immediately spawns a new 15-second polling loop. HTTP polling is never deactivated, generating continuous redundant network traffic and blocking rapid 10-second reconnect fallback upon socket disconnection.
- **Remediation**: Refactor `_stopHttpPolling()` to purely clear and nullify `this.pollingInterval`.

#### UX-03: Misleading Glowing Green Status Pill During WebSocket Disconnection
- **Severity**: 🟠 **HIGH** | **Files & Lines**: `frontend/js/websocket.js:98-110, 217-225`, `frontend/index.html:50-53`
- **Root Cause & Risk**: When WebSocket drops or errors, `websocket.js:98` sets the status pill to `"API ACTIVE (HTTP)"` with a glowing green color (`#10b981`). The trader assumes sub-second tick streaming is active when data is actually delayed by 15-second polling.
- **Remediation**: Implement a 4-state indicator: `LIVE WS` (Green `#10b981`), `RECONNECTING` (Pulsing Orange `#f59e0b`), `POLLING (HTTP)` (Amber `#d97706`), and `OFFLINE` (Red `#ef4444`).

#### UX-04: Unsafe 1-Click Console Buy & Event-Loop-Freezing Native Browser Modals
- **Severity**: 🟠 **HIGH** | **Files & Lines**: `frontend/js/websocket.js:338-354`, `frontend/js/ui.js:250-262, 311-327`
- **Root Cause & Risk**:
  1. Clicking "BUY SLOT" immediately places a live trade with zero confirmation prompt or debounce protection, risking accidental double-buys.
  2. Order completions and liquidations trigger native `alert()` and `confirm()` dialogs. While open, these dialogs freeze the JavaScript event loop, halting WebSocket heartbeats, live tick updates, and chart redraws.
- **Remediation**: Replace native dialogs with non-blocking UI Modals and Toast notifications; add a 2-second debounce and confirmation modal to quick-buy actions.

#### UX-05: Disconnected Volume Crosshairs & Missing Intraday Timeframes
- **Severity**: 🟠 **HIGH** | **Files & Lines**: `frontend/js/chart.js:42-96`, `frontend/index.html:231-241`
- **Root Cause & Risk**: Moving the cursor over the candlestick chart displays a vertical crosshair, but the volume chart below remains static (`subscribeCrosshairMove` is not synced). No floating hover tooltip displays past OHLCV or indicator values. Furthermore, chart timeframes only offer 1D and 1W, lacking 60m and 15m intraday intervals required for tactical entry timing.
- **Remediation**: Implement `subscribeCrosshairMove` synchronization, a dynamic floating OHLCV legend bar, and 60m/15m timeframe data endpoints.

---

## 4. Prioritized Remediation Roadmap

```
+====================================================================================================+
| PHASE 1: IMMEDIATE CRITICAL REPAIR & TRADING SAFETY (0 - 48 Hours)                                 |
+====================================================================================================+
| 1. [PERF-01 / ARCH-10 / SYNC-03] CQRS Read/Write Decoupling                                        |
|    - In routers/dashboard.py: replace db_manager.sync_portfolio_prices() with db_manager.get_live_portfolio(). |
|    - In frontend/js/websocket.js: fix _stopHttpPolling() to truly cancel polling intervals.        |
|                                                                                                    |
| 2. [SYNC-01 / SEC-08] Schema & Facade Repair                                                       |
|    - Add CREATE TABLE IF NOT EXISTS trade_history to persistence.py:init_database().               |
|    - Implement get_trade_history_records() in persistence.py and export via db_manager.py.         |
|                                                                                                    |
| 3. [SYNC-02] Stale Price Overwrite Guard                                                           |
|    - Add AND status = 'HOLDING' to UPDATE my_portfolio in persistence.py:sync_portfolio_prices().  |
|                                                                                                    |
| 4. [API-01 / API-03] KIS OpenAPI Rate Limiting & Token Mutex                                       |
|    - Add TokenBucketLimiter (3.5 TPS) to kis_broker.py with exponential backoff on EGW00201.       |
|    - Protect kis_broker.authenticate() with threading.RLock() against EGW00133 collisions.        |
|                                                                                                    |
| 5. [API-02 / SYNC-06] Order Execution Atomicity & Mutex                                            |
|    - Wrap buy/sell order routes in an asyncio.Lock() to prevent double-spending.                   |
|    - Transition to Outbox Pattern (record PENDING before broker submission).                       |
|                                                                                                    |
| 6. [UX-01 / UX-04] Legacy Redirect & Safe Order Execution                                          |
|    - Redirect /legacy permanently to / (frontend/index.html).                                      |
|    - Replace native alert()/confirm() with non-blocking UI Modals and add 2s button debounce.      |
+====================================================================================================+
| PHASE 2: ARCHITECTURAL HARDENING & SCAN PIPELINE OPTIMIZATION (3 - 7 Days)                         |
+====================================================================================================+
| 7. [ARCH-06] Fix MSI Macro Regime Extraction Bug                                                   |
|    - In generate_dashboard_feed.py:462, read macro_climate.get("msi_score", 50.0).                |
|                                                                                                    |
| 8. [PERF-02 / ARCH-07 / API-04] Scan Pipeline Consolidation & Throttle                             |
|    - Eliminate 23-ticker scan_and_select_2x2x2; derive matrix directly from build_dashboard_data().|
|    - Lower yfinance ThreadPoolExecutor workers from 12 to 4 with jittered delays.                  |
|    - Implement 15-minute local OHLCV candle caching.                                               |
|                                                                                                    |
| 9. [PERF-03 / PERF-04] Asynchronous I/O Offloading                                                 |
|    - Parallelize 60-file chart disk writes with compact JSON formatting.                           |
|    - Wrap compute_all_indicators in asyncio.to_thread in routers/charts.py.                        |
|                                                                                                    |
| 10. [SEC-01 / SEC-02 / SEC-05] Security Perimeter Hardening                                        |
|     - Enforce exact ALLOWED_WS_ORIGINS matching in server.py:websocket_live_hub.                  |
|     - Add .kis_token_*.json and data/ to .gitignore.                                               |
|     - Enforce TICKER_REGEX and Literal validation on BrokerOrderRequest in routers/broker.py.      |
|                                                                                                    |
| 11. [ARCH-01 / ARCH-02 / ARCH-05] Domain Layer Purification                                        |
|     - Move daemons from domain/risk/ to services/ (guardian_service.py, autopilot_service.py).     |
|     - Implement IExecutionGateway on KISBrokerAdapter and eliminate root db_manager.py imports.    |
+====================================================================================================+
| PHASE 3: USER EXPERIENCE & LONG-SESSION RESILIENCE (1 - 2 Weeks)                                   |
+====================================================================================================+
| 12. [UX-03 / UX-05 / UX-10] Real-Time Chart & Connection UX                                        |
|     - Implement 4-state indicator (LIVE WS, RECONNECTING, POLLING, OFFLINE).                       |
|     - Synchronize candlestick & volume crosshairs and add floating OHLCV legend bar.               |
|     - Invalidate and reload active chart on browser tab focus/wake-up.                             |
|                                                                                                    |
| 13. [API-05] US Market Trading Calendar Provider                                                   |
|     - Add NYSE/Nasdaq holiday calendar and DST detection to PortfolioGuardian (adaptive polling).  |
|                                                                                                    |
| 14. [UX-06 / UX-07 / UX-08] Bloomberg Dark Visual Polish                                           |
|     - Add MarketColorMode toggle (KRX Red/Blue vs US Green/Red).                                   |
|     - Right-align numeric columns (font-variant-numeric: tabular-nums).                            |
|     - Dynamically calculate 3-Slot Visualizer capital allocations and flag Bear over-allocations.  |
+====================================================================================================+
```

---

## 5. Audit Compliance & Integrity Attestation

- **Integrity Mode**: Strict Read-Only Static Audit.
- **Source Code Alterations**: Exactly `0` source code files were created, modified, or deleted within the application source tree (`al_sangmoo/`, `frontend/`, `server.py`, `generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, `youtube_stream_scanner.py`).
- **Artifacts Generated**:
  - Metadata Logs: `d:\코딩\R\.agents/orchestrator_system_audit/*`
  - Subagent Evidence Reports: `d:\코딩\R\.agents/explorer_*/*`
  - Master System Audit Report: `d:\코딩\R\system_audit_report.md`
- **Verification Authority**: Al-Sangmoo Multi-Agent Orchestrator

*End of Report.*
