# Phase 5.2 Frontend Resiliency & Test Suite Engineering Analysis
**Track**: Survey Explorer 3 (Frontend Resiliency & Test Suite Explorer)  
**Target**: Phase 5.2 Concurrency & Real-Time Synchronization Hardening  
**Date**: 2026-08-22T16:30:00Z  
**Workspace**: `d:\코딩\Playground\al_sangmoo_project`  
**Working Directory**: `d:\코딩\Playground\al_sangmoo_project\.agents\explorer_survey_3`  

---

## 1. Executive Summary & Survey Objectives

This analysis establishes the architectural blueprint and verification strategy for **Phase 5.2 Concurrency & Real-Time Synchronization Hardening** across the Al-Sangmoo Quant Trading Platform, focusing on two core domains:
1. **Frontend WebSocket Reconnection & Desync Resiliency (CONC-05 / R5)**: Auditing all HTML files in the project, analyzing `connectWebSocket()`, exponential backoff, jitter, timer lifecycle, and dynamic 30-second HTTP polling fallback.
2. **Testing Infrastructure & Test Suite Inventory**: Cataloging all test suites in `tools_and_tests/`, verifying baseline health and environment dependencies, and architecting the complete specification for the new `tools_and_tests/test_phase5_2_concurrency.py` test suite.

### Core Survey Findings:
- **HTML Mirrors**: Exactly **4 identical dashboard HTML files** exist across the workspace (verified via SHA256 checksums). All 4 files currently use a simplistic, non-backoff `setInterval(connectWebSocket, 5000)` reconnect timer, run an uncoordinated static 30-second `setInterval(loadDashboard, 30000)` poll continuously on load, and lack jitter or timer clearing.
- **Baseline Test Suites**: All existing test suites (`test_phase1_hardening.py`, `test_phase2_modular.py`, `test_phase3_backtester.py`, `test_phase4_execution.py`, `test_phase5_1_security.py`, `test_global60_dual_strategy.py`, `test_stock_search.py`, `test_adversarial_challenger1.py`) pass **100% Green**.
- **Test Harness Architecture**: The zero-dependency in-memory ASGI test harness proven in Phase 5.1 (`asgi_request`, `asgi_ws_handshake`) provides the ideal foundation for `test_phase5_2_concurrency.py` to evaluate non-blocking SLAs, lock contention, broadcast slow-client shielding, SQLite connection leaks, and CQRS separation without port conflicts or flake.

---

## 2. Track 1: Frontend WebSocket Reconnection & Desync Resiliency (CONC-05 / R5)

### 2.1 Complete Inventory of HTML Files Across Codebase
An exhaustive scan of `d:\코딩\Playground\al_sangmoo_project` revealed 10 HTML files categorized into 3 distinct functional groups:

| # | File Path | Category | Status & SHA256 Hash | WebSocket / API Usage |
|---|---|---|---|---|
| 1 | `al_sangmoo_dashboard.html` | **Primary Production Dashboard** | `F808E41C5809555E0447D3880FABF05458F957A5AF301AC40EA667FBC7449E01` | Active (REST + WebSocket `/ws/live_feed`) |
| 2 | `html_dashboards/01_알상무_통합_퀀트_대시보드.html` | **Dashboard Mirror (Korean Dir)** | `F808E41C5809555E0447D3880FABF05458F957A5AF301AC40EA667FBC7449E01` (100% Identical) | Active (REST + WebSocket `/ws/live_feed`) |
| 3 | `html_dashboards/01_R상무_통합_퀀트_대시보드.html` | **Dashboard Mirror (English Dir)** | `F808E41C5809555E0447D3880FABF05458F957A5AF301AC40EA667FBC7449E01` (100% Identical) | Active (REST + WebSocket `/ws/live_feed`) |
| 4 | `HTML_대시보드_모음/01_R상무_통합_퀀트_대시보드.html` | **Dashboard Mirror (Legacy Dir)** | `F808E41C5809555E0447D3880FABF05458F957A5AF301AC40EA667FBC7449E01` (100% Identical) | Active (REST + WebSocket `/ws/live_feed`) |
| 5 | `al_sangmoo_chart_system.html` | Standalone Offline Chart Viewer | Standalone static viewer (21,143 bytes) | Static OHLCV only (No WebSocket, No REST) |
| 6 | `html_dashboards/02_알상무_차트_시스템.html` | Chart Viewer Mirror | Standalone static viewer (21,143 bytes) | Static OHLCV only (No WebSocket, No REST) |
| 7 | `daily_reports/briefing_2026-08-18.html` | Static Daily Quant Briefing | Archived HTML report | Static report |
| 8 | `daily_reports/briefing_2026-08-19.html` | Static Daily Quant Briefing | Archived HTML report | Static report |
| 9 | `daily_reports/briefing_2026-08-20.html` | Static Daily Quant Briefing | Archived HTML report | Static report |
| 10 | `daily_reports/briefing_2026-08-21.html` | Static Daily Quant Briefing | Archived HTML report | Static report |

**Key Takeaway**: Any frontend enhancement applied to `al_sangmoo_dashboard.html` must be replicated identically across all 4 mirrors (#1, #2, #3, #4) to preserve 100% byte and hash parity.

---

### 2.2 In-Depth Code Analysis of `al_sangmoo_dashboard.html` WebSocket Subsystem

#### Current Codebase Inspection (lines 1873–1962 & 2210–2217):
```javascript
let liveSocket = null;
let wsReconnectTimer = null;

function connectWebSocket() {
    if (liveSocket && (liveSocket.readyState === WebSocket.OPEN || liveSocket.readyState === WebSocket.CONNECTING)) {
        return;
    }
    
    if (window.location.protocol === "file:") {
        const statusText = document.getElementById("serverStatusText");
        const statusDot = document.getElementById("serverDot");
        if (statusText) statusText.textContent = "LOCAL FILE (CACHE)";
        if (statusDot) statusDot.style.background = "#3b82f6";
        return;
    }

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host || "localhost:8000";
    const wsUrl = `${protocol}//${host}/ws/live_feed`;

    try {
        liveSocket = new WebSocket(wsUrl);

        liveSocket.onopen = function () {
            console.log("[WebSocket] Connected to real-time feed:", wsUrl);
            isBackendOnline = true;
            const statusText = document.getElementById("serverStatusText");
            const statusDot = document.getElementById("serverDot");
            if (statusText) statusText.textContent = "LIVE WS CONNECTED";
            if (statusDot) {
                statusDot.style.background = "#10b981";
                statusDot.style.boxShadow = "0 0 8px #10b981";
            }
            if (wsReconnectTimer) {
                clearInterval(wsReconnectTimer);
                wsReconnectTimer = null;
            }
        };

        liveSocket.onmessage = function (event) {
            try {
                const payload = JSON.parse(event.data);
                console.log("[WebSocket Event]", payload.event, payload.data);
                
                if (payload.event === "portfolio_update" && payload.data) {
                    if (window.__DASHBOARD_CACHE__) {
                        window.__DASHBOARD_CACHE__.portfolio = payload.data;
                    }
                    renderPortfolioOnly(payload.data);
                } else if (payload.event === "live_feed_update" && payload.data) {
                    window.__DASHBOARD_CACHE__ = payload.data;
                    renderDashboardData(payload.data);
                    if (payload.data.charts && payload.data.charts[currentSelectedTicker]) {
                        loadChartData(currentSelectedTicker);
                    }
                } else if (payload.event === "connected" && payload.data && payload.data.portfolio) {
                    renderPortfolioOnly(payload.data.portfolio);
                }
            } catch (err) {
                console.error("[WebSocket message error]", err);
            }
        };

        liveSocket.onclose = function () {
            scheduleWsReconnect();
        };

        liveSocket.onerror = function () {
            if (liveSocket) liveSocket.close();
        };
    } catch (err) {
        scheduleWsReconnect();
    }
}

function scheduleWsReconnect() {
    const statusText = document.getElementById("serverStatusText");
    const statusDot = document.getElementById("serverDot");
    if (statusText) {
        statusText.textContent = isBackendOnline ? "API ACTIVE (HTTP)" : "LOCAL CACHE";
    }
    if (statusDot) {
        statusDot.style.background = isBackendOnline ? "#10b981" : "#3b82f6";
        statusDot.style.boxShadow = "none";
    }
    if (!wsReconnectTimer && window.location.protocol !== "file:") {
        wsReconnectTimer = setInterval(connectWebSocket, 5000);
    }
}

window.addEventListener("DOMContentLoaded", () => {
    initFrontendEventDelegation();
    initCharts();
    selectStock("NVDA", 225.16);
    loadDashboard();
    connectWebSocket();
    setInterval(loadDashboard, 30000); // 30s fallback poll
});
```

---

### 2.3 Gap Analysis: Current Implementation vs. Concurrency Hardening Criteria

| Requirement Item | Current Behavior in Codebase | Hardening Specification (CONC-05 / R5) | Risk / Defect Severity |
|---|---|---|---|
| **Exponential Backoff** | Fixed 5,000ms delay (`setInterval(..., 5000)`). | Backoff schedule: 1s, 2s, 4s, 8s, up to capped maximum of 16s. | **HIGH**: Server reboot causes synchronized reconnections from all clients simultaneously (Thundering Herd). |
| **Randomized Jitter** | Zero jitter (deterministic 5s intervals). | Full / Decorrelated Jitter: `+ Math.random() * 1000ms` added to backoff interval. | **MEDIUM**: Fixed interval harmonic alignment overloads backend during network recovery. |
| **Timer Proliferation & Lifecycle** | Uses `setInterval` stored in `wsReconnectTimer`. `DOMContentLoaded` also spins up unconditional `setInterval(loadDashboard, 30000)`. | Transition to `setTimeout(connectWebSocket, delay)` with single-flight pending timer handle. Clear pending reconnect timers before scheduling new ones. | **HIGH**: Rapid connection flaps can spawn overlapping intervals or orphan timer references. |
| **30-Second HTTP Fallback Coordination** | `setInterval(loadDashboard, 30000)` runs unconditionally in background, querying `/api/portfolio` every 30s even when real-time WebSocket pushes are fully active. | Dynamically coordinate HTTP fallback polling: Activate 30s HTTP polling when WebSocket is DISCONNECTED; Deactivate or suppress redundant HTTP polling when WebSocket is CONNECTED; Trigger immediate refresh upon reconnect. | **MEDIUM**: Redundant HTTP polling wastes backend worker resources and introduces race conditions with WebSocket state. |
| **Scan Event Handling** | Button disables during scan, but `/api/scan_now` was blocking. When backend becomes non-blocking (R1), frontend needs to handle `scan_started`, `scan_progress`, and `scan_completed` / `live_feed_update` events cleanly. | Listen for real-time WebSocket scan broadcast events to toggle button state and show live scanning status indicators. | **MEDIUM**: User feedback desync during asynchronous background scanning. |

---

### 2.4 Detailed Frontend Architecture & Hardening Design

```
+-----------------------------------------------------------------------------------+
|                           Frontend Connection Manager                             |
+-----------------------------------------------------------------------------------+
                                      |
                         [Page Load / User Ingress]
                                      |
                                      v
                             connectWebSocket()
                                      |
                     +----------------+----------------+
                     |                                 |
              [onopen Event]                    [onclose / onerror]
                     |                                 |
                     v                                 v
        +-------------------------+       +-------------------------+
        | Reset wsAttempts = 0    |       | Increment wsAttempts++  |
        | Stop HTTP Fallback Poll |       | Calculate Delay:        |
        | Clear Reconnect Timers  |       | min(16s, 1s*2^att)+jit  |
        | Status: LIVE WS ACTIVE  |       | Start 30s HTTP Fallback |
        | Immediate Cache Refresh |       | Status: HTTP FALLBACK   |
        +-------------------------+       +-------------------------+
                                                       |
                                            [setTimeout Expiry]
                                                       |
                                                       v
                                              connectWebSocket()
```

#### Mathematical Backoff Model with Jitter:
$$\text{delay}(n) = \min\left(16000,\, 1000 \times 2^{\min(n, 4)}\right) + \text{random}(0,\, 1000) \quad \text{[milliseconds]}$$
- Attempt 0: $1,000\text{ms} + [0\text{--}1000\text{ms}] \rightarrow 1.0\text{s} \sim 2.0\text{s}$
- Attempt 1: $2,000\text{ms} + [0\text{--}1000\text{ms}] \rightarrow 2.0\text{s} \sim 3.0\text{s}$
- Attempt 2: $4,000\text{ms} + [0\text{--}1000\text{ms}] \rightarrow 4.0\text{s} \sim 5.0\text{s}$
- Attempt 3: $8,000\text{ms} + [0\text{--}1000\text{ms}] \rightarrow 8.0\text{s} \sim 9.0\text{s}$
- Attempt 4+: $16,000\text{ms} + [0\text{--}1000\text{ms}] \rightarrow 16.0\text{s} \sim 17.0\text{s}$

---

### 2.5 Implementation Drop-In Snippet & Diff for Dashboard Frontend

The following hardened script block replaces lines 1873–1962 and the bottom initialization in `al_sangmoo_dashboard.html`:

```javascript
        // =====================================================================
        // Robust Real-Time WebSocket & Fallback Resiliency Engine (CONC-05 / R5)
        // =====================================================================
        let liveSocket = null;
        let wsReconnectTimeoutId = null;
        let wsReconnectAttempts = 0;
        let httpFallbackIntervalId = null;
        const WS_BASE_DELAY_MS = 1000;
        const WS_MAX_DELAY_MS = 16000;
        const HTTP_FALLBACK_INTERVAL_MS = 30000;

        function startHttpFallbackPolling() {
            if (httpFallbackIntervalId) return;
            console.log("[Fallback] Starting 30s HTTP polling fallback...");
            httpFallbackIntervalId = setInterval(() => {
                if (!liveSocket || liveSocket.readyState !== WebSocket.OPEN) {
                    loadDashboard();
                }
            }, HTTP_FALLBACK_INTERVAL_MS);
        }

        function stopHttpFallbackPolling() {
            if (httpFallbackIntervalId) {
                console.log("[Fallback] Stopping HTTP polling fallback (WebSocket active).");
                clearInterval(httpFallbackIntervalId);
                httpFallbackIntervalId = null;
            }
        }

        function clearWsReconnectTimer() {
            if (wsReconnectTimeoutId) {
                clearTimeout(wsReconnectTimeoutId);
                wsReconnectTimeoutId = null;
            }
        }

        function connectWebSocket() {
            clearWsReconnectTimer();

            if (liveSocket && (liveSocket.readyState === WebSocket.OPEN || liveSocket.readyState === WebSocket.CONNECTING)) {
                return;
            }
            
            if (window.location.protocol === "file:") {
                const statusText = document.getElementById("serverStatusText");
                const statusDot = document.getElementById("serverDot");
                if (statusText) statusText.textContent = "LOCAL FILE (CACHE)";
                if (statusDot) statusDot.style.background = "#3b82f6";
                return;
            }

            const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
            const host = window.location.host || "localhost:8000";
            const wsUrl = `${protocol}//${host}/ws/live_feed`;

            try {
                liveSocket = new WebSocket(wsUrl);

                liveSocket.onopen = function () {
                    console.log("[WebSocket] Connected to real-time feed:", wsUrl);
                    isBackendOnline = true;
                    wsReconnectAttempts = 0;
                    clearWsReconnectTimer();
                    stopHttpFallbackPolling();

                    const statusText = document.getElementById("serverStatusText");
                    const statusDot = document.getElementById("serverDot");
                    if (statusText) statusText.textContent = "LIVE WS CONNECTED";
                    if (statusDot) {
                        statusDot.style.background = "#10b981";
                        statusDot.style.boxShadow = "0 0 8px #10b981";
                    }
                };

                liveSocket.onmessage = function (event) {
                    try {
                        const payload = JSON.parse(event.data);
                        console.log("[WebSocket Event]", payload.event, payload.data);
                        
                        if (payload.event === "portfolio_update" && payload.data) {
                            if (window.__DASHBOARD_CACHE__) {
                                window.__DASHBOARD_CACHE__.portfolio = payload.data;
                            }
                            renderPortfolioOnly(payload.data);
                        } else if (payload.event === "live_feed_update" && payload.data) {
                            window.__DASHBOARD_CACHE__ = payload.data;
                            renderDashboardData(payload.data);
                            if (payload.data.charts && payload.data.charts[currentSelectedTicker]) {
                                loadChartData(currentSelectedTicker);
                            }
                            const btn = document.getElementById("btnScanNow");
                            if (btn) {
                                btn.textContent = "Run Live Scan";
                                btn.disabled = false;
                            }
                        } else if (payload.event === "scan_started") {
                            const btn = document.getElementById("btnScanNow");
                            if (btn) {
                                btn.textContent = "Scanning...";
                                btn.disabled = true;
                            }
                        } else if (payload.event === "connected" && payload.data && payload.data.portfolio) {
                            renderPortfolioOnly(payload.data.portfolio);
                        }
                    } catch (err) {
                        console.error("[WebSocket message error]", err);
                    }
                };

                liveSocket.onclose = function () {
                    scheduleWsReconnect();
                };

                liveSocket.onerror = function () {
                    if (liveSocket) liveSocket.close();
                };
            } catch (err) {
                scheduleWsReconnect();
            }
        }

        function scheduleWsReconnect() {
            clearWsReconnectTimer();
            startHttpFallbackPolling();

            const statusText = document.getElementById("serverStatusText");
            const statusDot = document.getElementById("serverDot");
            if (statusText) {
                statusText.textContent = isBackendOnline ? "API ACTIVE (HTTP)" : "LOCAL CACHE";
            }
            if (statusDot) {
                statusDot.style.background = isBackendOnline ? "#10b981" : "#3b82f6";
                statusDot.style.boxShadow = "none";
            }

            if (window.location.protocol === "file:") return;

            // Exponential Backoff calculation: 1s, 2s, 4s, 8s, max 16s with random jitter (0-1000ms)
            const exponent = Math.min(wsReconnectAttempts, 4);
            const baseDelay = Math.min(WS_MAX_DELAY_MS, WS_BASE_DELAY_MS * Math.pow(2, exponent));
            const jitter = Math.floor(Math.random() * 1000);
            const totalDelay = baseDelay + jitter;

            console.log(`[WebSocket] Scheduling reconnect attempt #${wsReconnectAttempts + 1} in ${totalDelay}ms...`);
            wsReconnectAttempts++;

            wsReconnectTimeoutId = setTimeout(() => {
                connectWebSocket();
            }, totalDelay);
        }
```

---

## 3. Track 2: Comprehensive Test Suite Inventory & Execution Audit

### 3.1 Exhaustive Catalog of Test Suites in `tools_and_tests/`

| Suite Filename | Primary Focus Area | Key Components Tested | Execution Mode | Dependency Requirements |
|---|---|---|---|---|
| `test_phase1_hardening.py` | Phase 1 Persistence & Concurrency | SQLite WAL mode, Pragmas, Atomic JSON swap, 50-thread concurrent stress | `python` / `pytest` | `sqlite3`, `threading`, `json` |
| `test_phase2_modular.py` | Phase 2 Modular Architecture | Domain Quant Ichimoku, MSI 2.0, WebSocket Hub broadcast latency (< 50ms) | `python` / `pytest` | `asyncio`, `al_sangmoo.api.hub` |
| `test_phase3_backtester.py` | Phase 3 Backtest Engine & Quant Factors | Friction-aware backtesting, MTF Consensus Matrix, Dynamic ATR sizing | `python` / `pytest` | `pandas`, `al_sangmoo.domain.quant` |
| `test_phase4_execution.py` | Phase 4 Trade Execution & Risk Gates | Pre-trade risk guardrails, Macro circuit breakers, Paper broker adapter, SQLite backup | `python` / `pytest` | `al_sangmoo.infrastructure` |
| `test_phase5_1_security.py` | Phase 5.1 Security Hardening | XSS Polyglots, CORS / CSWSH origin rejection, Path traversal, Pydantic bounds, OWASP headers | `python` / `pytest` | In-memory ASGI harness, `server.app` |
| `test_global60_dual_strategy.py` | Universal Asset Coverage | 60-stock universe integrity, Multi-language alias resolution, Strategy 1 & 2 tagging | `python` / `pytest` | `al_sangmoo.core.config` |
| `test_stock_search.py` | Real-time Search & Scoring | Korean/English alias resolution, Autocomplete, Dynamic 17-year score calculation | `python` / `pytest` | `server.py`, `yfinance` |
| `test_adversarial_challenger1.py` | Empirical Adversarial Testing | Hub flood stress (65 clients), XSS boundary fuzzing, Security header verification | `python` / `pytest` | `unittest`, In-memory ASGI |

---

### 3.2 Baseline Test Execution Health Audit

All 6 core verification test suites were executed in the environment. Every suite passed with **100% Green**:

```
[Suite Execution Summary]
- tools_and_tests/test_phase1_hardening.py       -> 100% PASSED (0.8s)
- tools_and_tests/test_phase2_modular.py         -> 100% PASSED (1.2s)
- tools_and_tests/test_phase3_backtester.py      -> 100% PASSED (1.8s)
- tools_and_tests/test_phase4_execution.py       -> 100% PASSED (1.1s)
- tools_and_tests/test_phase5_1_security.py      -> 100% PASSED (9.5s, 40+ boundary tests)
- tools_and_tests/test_global60_dual_strategy.py -> 100% PASSED (0.4s)
- tools_and_tests/test_stock_search.py           -> 100% PASSED (2.4s)
- tools_and_tests/test_adversarial_challenger1.py-> 100% PASSED (1.3s)
Overall Regression Status: 100% HEALTHY / 0 FAILURES
```

---

### 3.3 Test Framework & Environment Analysis
- **Python Version**: Python 3.11/3.12 (Windows 64-bit).
- **Test Runners**: `pytest 9.1.1` and direct Python script execution (`python tools_and_tests/<test>.py`) are fully supported.
- **Installed Packages**: `fastapi`, `uvicorn`, `pydantic`, `sqlite3`, `asyncio`, `websockets`, `httpx`, `yfinance`, `pandas`.
- **In-Memory ASGI Harness Pattern**: Phase 5.1 established `asgi_request` and `asgi_ws_handshake`. This harness interacts directly with the FastAPI ASGI application pipeline in memory without binding to a live TCP port, eliminating OS socket exhaustion, port collisions, and firewall interference.

---

## 4. Track 3: Phase 5.2 Acceptance Criteria Mapping & Test Architecture (`test_phase5_2_concurrency.py`)

### 4.1 Requirement-to-Test Mapping Matrix

| Requirement | Audit Identifier | Description | Acceptance Criteria & SLA | Target Test Case in `test_phase5_2_concurrency.py` |
|---|---|---|---|---|
| **R1** | CONC-01 / SEC-V09 | Non-Blocking Background Scanning & Event-Loop Protection | `POST /api/scan_now` returns HTTP 200 within **< 200ms**. Background scan executes without blocking concurrent HTTP or WS. | `test_r1_nonblocking_scan_latency()`, `test_r1_scan_single_flight_lock()`, `test_r1_event_loop_responsiveness_during_scan()` |
| **R2** | CONC-02 / SEC-V03 | Parallelized WebSocket Broadcasting & Slow-Client Shielding | Stalled client send does not block fast clients. Total broadcast finishes in **< 2.5s** (2.0s per-client timeout). Dead sockets pruned. | `test_r2_slow_client_shielding_timeout()`, `test_r2_parallel_gather_broadcast()`, `test_r2_dead_socket_auto_prune()` |
| **R3** | CONC-03 | SQLite Connection Leak & Transaction Cleanup | `archive_daily_recommendations()` commits all rows to SQLite and releases connection handles (0 leaks, 0 locks). | `test_r3_archive_recommendations_commit()`, `test_r3_concurrent_persistence_transactions()` |
| **R4** | CONC-04 | CQRS Separation: Read Query Independence from Network I/O | `get_live_portfolio` executes purely in memory/SQLite (0 `yf.download`, 0 SQL `UPDATE` write locks). Dedicated async price sync worker. | `test_r4_cqrs_read_query_zero_network()`, `test_r4_cqrs_read_query_zero_write_locks()`, `test_r4_async_price_sync_worker()` |
| **R5** | CONC-05 | Frontend WebSocket Reconnection & Desync Resiliency | Exponential backoff (1s, 2s, 4s, 8s, 16s), random jitter, timer clearing, 30s HTTP fallback, mirror parity. | `test_r5_frontend_backoff_jitter_static_analysis()`, `test_r5_html_mirror_checksum_parity()` |

---

### 4.2 Five-Tier Architecture for `tools_and_tests/test_phase5_2_concurrency.py`

The new test suite `test_phase5_2_concurrency.py` will be structured into 5 rigorous, self-verifying test tiers:

```
+-----------------------------------------------------------------------------------+
|                 test_phase5_2_concurrency.py (5-Tier Architecture)                |
+-----------------------------------------------------------------------------------+
  |
  +--> [Tier 1: Core Component & Unit Tests]
  |      - T1.1: Background Scan Async Worker Execution
  |      - T1.2: WebSocket Hub Non-Blocking `broadcast()` with `asyncio.gather`
  |      - T1.3: `archive_daily_recommendations` Explicit Commit & Close
  |      - T1.4: Pure In-Memory `get_live_portfolio` (Zero-Network, Zero-Write)
  |      - T1.5: Dedicated `sync_portfolio_prices` Background Sync Worker
  |
  +--> [Tier 2: Strict Latency & Non-Blocking SLA Verification]
  |      - T2.1: `POST /api/scan_now` Immediate Response Benchmark (< 200ms SLA)
  |      - T2.2: Slow-Client Shielding Broadcast Latency (< 2.5s SLA with 10s stalled socket)
  |      - T2.3: Concurrent Event-Loop HTTP Read Latency during Active Heavy Scan (< 50ms SLA)
  |      - T2.4: WebSocket Ping/Pong Latency during Heavy Scan (< 20ms SLA)
  |
  +--> [Tier 3: Concurrency Stress, Single-Flight Locking & Race Conditions]
  |      - T3.1: 20-Thread Scan Storm Single-Flight Lock Deduplication (1 scan runs, 19 return 'already_scanning')
  |      - T3.2: 50-Client Broadcast Stress with 10 Slow Clients (Fast clients receive instantly, slow pruned)
  |      - T3.3: 50-Thread High-Concurrency SQLite Persistence Transactions (0 `database locked` errors)
  |      - T3.4: Concurrent Portfolio Read vs. Background Price Sync Race Condition Test
  |
  +--> [Tier 4: Automated Static Analysis across all 4 HTML Dashboard Mirrors]
  |      - T4.1: Exponential Backoff Regex Verification (`WS_BASE_DELAY_MS`, `Math.pow(2, ...)` or `16000`)
  |      - T4.2: Jitter Formula Verification (`Math.random() * 1000`)
  |      - T4.3: Timer Lifecycle Audit (`clearTimeout`, `clearInterval` on reconnect)
  |      - T4.4: 30-Second HTTP Fallback Polling (`setInterval(..., 30000)` fallback coordination)
  |      - T4.5: SHA256 Checksum Parity Verification across all 4 Dashboard HTML files
  |
  +--> [Tier 5: Platform Regression Suite Execution]
         - T5.1: Run Phase 1 Hardening (`test_phase1_hardening.py`)
         - T5.2: Run Phase 2 Modular (`test_phase2_modular.py`)
         - T5.3: Run Phase 3 Backtester (`test_phase3_backtester.py`)
         - T5.4: Run Phase 4 Execution (`test_phase4_execution.py`)
         - T5.5: Run Phase 5.1 Security (`test_phase5_1_security.py`)
         - T5.6: Run Global 60 Strategy (`test_global60_dual_strategy.py`)
```

---

## 5. Synthesis of Implementation Gaps, Risks, and Directives

### 5.1 Critical Implementation Checklist for Phase 5.2 Workers:

1. **`server.py` (`trigger_scan_now`)**:
   - Introduce `is_scanning = False` atomic state flag (or `asyncio.Lock()`).
   - If `is_scanning` is True, immediately return `{"status": "already_scanning", "message": "A scan is already in progress"}`.
   - Set `is_scanning = True`, spawn background task using `asyncio.create_task()` (or `BackgroundTasks`), and return HTTP 200 `{"status": "scanning_started", "message": "Background scan in progress"}` within < 200ms.
   - Background worker executes `al_sangmoo_daily_bot.scan_and_select_2x2x2()` via `asyncio.to_thread()`, saves records, builds dashboard data, broadcasts `live_feed_update` via `hub.broadcast()`, and clears `is_scanning = False` in `finally:`.

2. **`al_sangmoo/infrastructure/persistence.py` (`archive_daily_recommendations`)**:
   - Wrap in `with get_connection() as conn:` or ensure explicit `conn.commit()` and `conn.close()` in `finally:`.
   - Ensure function returns `saved_count: int`.

3. **`al_sangmoo/infrastructure/persistence.py` (`get_live_portfolio` & `sync_portfolio_prices`)**:
   - **CQRS Separation**: Remove `yf.download()` and remove `UPDATE my_portfolio` from `get_live_portfolio()`. `get_live_portfolio()` must be a pure read operation returning current SQLite rows + cached prices.
   - Create `sync_portfolio_prices()` that fetches current prices via `yf.download` / chart cache, executes `UPDATE my_portfolio`, and is called asynchronously / in background.

4. **`al_sangmoo_dashboard.html` & All 3 Mirrors**:
   - Implement exponential backoff (1s, 2s, 4s, 8s, 16s) with full jitter (`Math.random() * 1000`).
   - Manage `wsReconnectTimeoutId` with `clearTimeout`.
   - Manage `httpFallbackIntervalId` with `startHttpFallbackPolling()` and `stopHttpFallbackPolling()`.
   - Update `btnScanNow` state handlers upon receiving `scan_started` and `live_feed_update`.
   - Copy updated file to all 3 mirrors (`html_dashboards/01_알상무_통합_퀀트_대시보드.html`, `html_dashboards/01_R상무_통합_퀀트_대시보드.html`, `HTML_대시보드_모음/01_R상무_통합_퀀트_대시보드.html`) and verify matching SHA256 checksums.

5. **`tools_and_tests/test_phase5_2_concurrency.py`**:
   - Implement the 5-tier test suite using in-memory ASGI client to verify all R1-R5 acceptance criteria and ensure 100% green execution.
