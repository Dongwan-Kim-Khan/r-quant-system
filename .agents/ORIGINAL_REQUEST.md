# Original User Request

## 2026-08-22T04:55:03Z

Implement and verify Phase 5.1 Security Hardening across the Al-Sangmoo Quant Trading Platform to resolve all Critical and High security vulnerabilities identified in the master audit report (`MASTER-AUDIT-2026-v2.6-FINAL`).

Working directory: d:\코딩\Playground\al_sangmoo_project
Integrity mode: development

## Requirements

### R1. Stored & DOM XSS Remediation (SEC-V01)
- Backend: Implement strict regex format validation and maximum length constraints (max 100 chars) on `SellOrder.reason` in `server.py` and `al_sangmoo/infrastructure/persistence.py`.
- Frontend: Implement a robust `escapeHtml()` sanitization utility in `al_sangmoo_dashboard.html` (and all HTML dashboard mirrors). Sanitize all dynamic interpolations (`h.exit_advice`, `item.name`, `item.sector`, `item.ticker`, search dropdown labels, and live stream titles) before rendering to the DOM.
- Refactor inline `onclick` handler string interpolations (`selectStock('${item.ticker}', ...)`) to use safe `data-*` attributes or explicit event listeners.

### R2. CORS Whitelisting & Origin Validation (SEC-V02, SEC-V04)
- Remove wildcard `"*"` origin from FastAPI `CORSMiddleware` in `server.py`.
- Restrict `allow_origins` strictly to local origins: `["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:3000", "http://127.0.0.1:3000"]`.
- Enforce WebSocket handshake origin verification in `/ws/live_feed` to prevent Cross-Site WebSocket Hijacking (CSWSH).
- Enforce a maximum active connection limit (`MAX_CONNECTIONS = 50`) in `al_sangmoo/api/hub.py` to prevent resource exhaustion DoS.

### R3. Path Traversal & Subprocess Argument Hardening (SEC-V05)
- In `youtube_stream_scanner.py` and static file endpoints, enforce strict regex validation (`^[a-zA-Z0-9_-]{11}$`) on all YouTube video IDs before filesystem path construction or `yt-dlp` subprocess execution.
- Validate that all resolved file paths strictly reside within `BASE_DIR` using `os.path.abspath` prefix checking.

### R4. Pydantic API Input Validation & Global Error Sanitization (SEC-V07, SEC-V08)
- Enhance Pydantic request models (`BuyOrder`, `SellOrder`) with regex validators for date formats (`^\d{4}-\d{2}-\d{2}$`) and price/quantity boundaries.
- Enforce `TICKER_REGEX` on all broker execution endpoints (`execute_broker_order`, `get_ticker_chart`).
- Add a global exception handler in `server.py` for unhandled 500 errors to prevent internal stack trace leakage to API clients.

### R5. OWASP Security Response Headers (SEC-V10)
- Attach standard security headers middleware to FastAPI in `server.py`:
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `X-XSS-Protection: 1; mode=block`

## Acceptance Criteria

### Security Hardening Verification
- [ ] `POST /api/portfolio/sell/{id}` with XSS payload `{"reason": "<script>alert(1)</script>"}` is rejected with HTTP 400 or fully sanitized in database and DOM.
- [ ] Cross-origin requests from untrusted origins (e.g. `http://evil.com`) are blocked with HTTP 403 / CORS rejection.
- [ ] WebSocket connection attempts from unauthorized external origins are rejected during handshake.
- [ ] Path traversal attempts (e.g. `v_id = "../../etc/passwd"`) in `youtube_stream_scanner.py` raise validation errors immediately.
- [ ] Internal 500 errors return sanitized JSON `{"status": "error", "message": "An internal server error occurred"}` without raw Python traceback leakage.
- [ ] Automated security verification test suite (`tools_and_tests/test_phase5_1_security.py`) passes 100% Green.
- [ ] Existing core regression test suites (`test_phase1`, `test_phase2`, `test_phase4`, `test_global60`) continue to pass 100% Green.

---
*Verification Target: `tools_and_tests/test_phase5_1_security.py`*

## 2026-08-22T16:24:19Z

Implement and verify Phase 5.2 Concurrency & Real-Time Synchronization Hardening across the Al-Sangmoo Quant Trading Platform to resolve all event-loop starvation, database locking, and WebSocket broadcast bottlenecks identified in the master audit report (`MASTER-AUDIT-2026-v2.6-FINAL`).

Working directory: d:\코딩\Playground\al_sangmoo_project
Integrity mode: development

## Requirements

### R1. Non-Blocking Background Scanning & Event-Loop Protection (CONC-01, SEC-V09)
- Refactor `/api/scan_now` in `server.py` to offload the heavy scanning pipeline (`al_sangmoo_daily_bot.scan_and_select_2x2x2` / `build_dashboard_data`) to `FastAPI.BackgroundTasks` or `asyncio.to_thread`.
- Return an immediate non-blocking response `{"status": "scanning_started", "message": "Background scan in progress"}` with an atomic `is_scanning` state lock to prevent overlapping scan storms.
- Broadcast scan progress and completion events to all connected clients via WebSocket (`hub.broadcast`).

### R2. Parallelized WebSocket Broadcasting & Slow-Client Shielding (CONC-02, SEC-V03)
- In `al_sangmoo/api/hub.py`, refactor `WebSocketBroadcastHub.broadcast()`:
  1. Release the internal lock before initiating network socket I/O.
  2. Use `asyncio.gather(*[...], return_exceptions=True)` to broadcast to all clients concurrently.
  3. Wrap each client send in `asyncio.wait_for(ws.send_json(message), timeout=2.0)` to shield the server from slow-client Head-of-Line blocking.
  4. Automatically prune dead or timed-out connections.

### R3. SQLite Connection Leak & Transaction Cleanup (CONC-03)
- In `al_sangmoo/infrastructure/persistence.py`, fix `archive_daily_recommendations()`:
  - Enforce context management (`with get_connection() as conn:`) with explicit `conn.commit()` and `conn.close()`.
  - Ensure zero leaked database handles or uncommitted SQLite transaction locks across all persistence methods.

### R4. CQRS Separation: Read Query Independence from Network I/O (CONC-04)
- Separate read-only portfolio queries (`get_live_portfolio`) from network updates:
  - Pure read operations (`get_live_portfolio`) must return current database records without executing blocking `yf.download` or issuing SQL `UPDATE` statements.
  - Create a dedicated asynchronous/background sync worker (`sync_portfolio_prices`) for market price refresh and database price updates.

### R5. Frontend WebSocket Reconnection & Desync Resiliency (CONC-05)
- In `al_sangmoo_dashboard.html` (and all HTML dashboard mirrors), harden `connectWebSocket()`:
  - Implement robust exponential backoff reconnect with jitter (1s, 2s, 4s, max 16s).
  - Clear existing heartbeat/polling intervals upon reconnect to prevent duplicate timer proliferation.
  - Seamlessly fall back to 30-second HTTP polling when WebSocket connection is unavailable.

## Acceptance Criteria

### Concurrency & Performance Verification
- [ ] Calling `POST /api/scan_now` returns HTTP 200 within < 200ms while scanning executes in background without blocking concurrent HTTP requests or WebSocket heartbeats.
- [ ] A stalled or slow WebSocket client does not degrade broadcast latency to other active clients (concurrent broadcast completes within < 2.5s regardless of slow clients).
- [ ] `archive_daily_recommendations()` commits all rows and leaves zero dangling SQLite connection locks.
- [ ] `get_live_portfolio` executes purely in memory/SQLite without triggering blocking network requests or acquiring write locks.
- [ ] Automated concurrency verification test suite (`tools_and_tests/test_phase5_2_concurrency.py`) passes 100% Green.
- [ ] All previous test suites (`test_phase1`, `test_phase2`, `test_phase4`, `test_phase5_1_security`) pass 100% Green.

---
*Verification Target: `tools_and_tests/test_phase5_2_concurrency.py`*

## 2026-08-22T16:50:42Z

Implement and verify Phase 5.3 Quantitative Consolidation & Clean Architecture Refactoring across the Al-Sangmoo Quant Trading Platform to eliminate all triple math duplications, conflicting scoring thresholds, and architectural layer bleeding identified in the master audit report (`MASTER-AUDIT-2026-v2.6-FINAL`).

Working directory: d:\코딩\Playground\al_sangmoo_project
Integrity mode: development

## Requirements

### R1. Single Source of Truth (SSOT) Indicator Engine Consolidation
- Centralize all quantitative indicator math (Ichimoku Tenkan/Kijun/SpanA/SpanB/Chikou, 20/50/200 MA, OBV, Volume Dry-Up ratio, 14-Day Inflow ratio) exclusively within `al_sangmoo/domain/quant/ichimoku.py` (and related domain modules).
- Refactor `generate_dashboard_feed.py` and `al_sangmoo_daily_bot.py` to completely eliminate duplicate indicator calculation functions and import directly from `al_sangmoo.domain.quant`.

### R2. Canonical 3-Tier Quant Scoring & Classification Engine
- Consolidate all scoring formulas and tier classification rules into `al_sangmoo/domain/quant/scoring.py` (or `ichimoku.py`):
  1. **Tier 1 (Sniper Radar / Macro Tailwind + Smart Money)**: Gate-0 Macro Tailwind, OBV Stealth Accumulation, 14-Day Volume Flow Ratio >= 120%, Bull Score >= 75.
  2. **Tier 2 (Structural Pullback)**: 26-Day Kijun-sen support (Price within +/-3% of Kijun), Tenkan >= Kijun alignment, 20-Day Volume Dry-Up (Volume < 60% of 20-day MA).
  3. **Tier 3 (Cloud Sniper)**: Forward +26D Ichimoku Cloud Trampoline bounce (Price above Span A/B), Stage 2 breakout momentum, -4% hard stop rule.
- Eliminate conflicting scoring thresholds (65pt vs 70pt vs 80pt) across all scripts so that batch scanners, dashboard feeds, and API endpoints produce identical, deterministic results.

### R3. Macro Stance Index 2.0 (MSI 2.0) Parameter & Logic Unification
- Unify MSI 2.0 calculation and hard gauge point weights into `al_sangmoo/domain/quant/macro.py`.
- Refactor `youtube_stream_scanner.py` and `generate_dashboard_feed.py` to use the unified `evaluate_macro_stance()` domain function, resolving conflicting weights (e.g. US 10Y yield thresholds 15pt vs 18pt).

### R4. Side-Effect Free Pipeline & Clean Data Flow
- Ensure `generate_dashboard_feed.build_dashboard_data()` operates as a pure data transformation pipeline without unintended database write side-effects during read-only view model generation.
- Eliminate duplicate database writes between `generate_dashboard_feed.py` and `al_sangmoo_daily_bot.py`.

## Acceptance Criteria

### SSOT Quantitative Architecture Verification
- [ ] `generate_dashboard_feed.py` and `al_sangmoo_daily_bot.py` have zero inline duplicate Ichimoku / indicator math and delegate 100% to `al_sangmoo.domain.quant`.
- [ ] Scoring outputs and Tier 1/2/3 recommendations produced by `al_sangmoo_daily_bot.py` and `generate_dashboard_feed.py` are mathematically identical for identical ticker datasets.
- [ ] MSI 2.0 gauge scores evaluated by `macro.py` and `youtube_stream_scanner.py` match 100% across all macro climate inputs.
- [ ] Automated SSOT verification test suite (`tools_and_tests/test_phase5_3_ssot_quant.py`) passes 100% Green.
- [ ] All previous test suites (`test_phase1`, `test_phase2`, `test_phase4`, `test_phase5_1_security`, `test_phase5_2_concurrency`) pass 100% Green.

---
*Verification Target: `tools_and_tests/test_phase5_3_ssot_quant.py`*

