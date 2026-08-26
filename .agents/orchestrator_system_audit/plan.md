# Master Plan: Al-Sangmoo Quant Terminal Full System Audit

## Objective
Execute an exhaustive, rigorous, read-only multi-agent audit of the Al-Sangmoo Quant Terminal codebase (`d:\코딩\R`) across 6 core technical domains and consolidate findings into a high-impact, professional audit report at `d:\코딩\R\system_audit_report.md`.

## Domain Partitioning & Scope

### Domain 1: Web & API Security Vulnerability Assessment
- **Key Modules**: `server.py`, `al_sangmoo/api/*`, `al_sangmoo/infrastructure/persistence.py`, `youtube_stream_scanner.py`, `al_sangmoo_dashboard.html`, `dashboard_terminal.html`
- **Scope**:
  - CSWSH (Cross-Site WebSocket Hijacking) in `/ws/live_feed` & connection limit DoS protections.
  - CORS policy & origins configuration (`CORSMiddleware`).
  - Secret & Credential management (KIS `app_key`, `app_secret`, account number, `.env` leakage).
  - SQL injection risks (raw string formatting / parameterization in SQLite queries).
  - Input validation, Pydantic constraints, ticker regex validation, path traversal vectors (`yt-dlp`, file endpoints).
  - OWASP compliance & security response headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `X-XSS-Protection`).
  - XSS in dashboard DOM manipulation (`innerHTML`, unescaped inputs).

### Domain 2: Code Architecture & Spaghetti Code Audit
- **Key Modules**: Entire repository structure across `al_sangmoo/domain/`, `al_sangmoo/infrastructure/`, `al_sangmoo/interfaces/`, `al_sangmoo/api/`, root scripts (`server.py`, `generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, `youtube_stream_scanner.py`), `frontend/` & HTML files.
- **Scope**:
  - Layer bleeding & coupling (domain depending on infrastructure, UI mixing with calculations).
  - Circular dependencies & monolithic anti-patterns.
  - Redundant / duplicate logic (e.g. Ichimoku math, scoring formulas, MSI 2.0 definitions).
  - Dead code, unused functions, obsolete legacy modules.
  - CQRS adherence: separation between read queries and write side-effects.

### Domain 3: Performance & Computational Optimization
- **Key Modules**: `server.py`, `generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, `al_sangmoo/api/hub.py`, `al_sangmoo_dashboard.html`, `dashboard_terminal.html`, `js/`
- **Scope**:
  - Backend feed generation performance (60-stock universe scan latency, yfinance calls, caching strategies).
  - Concurrency bottlenecks: event-loop blocking, thread offloading (`asyncio.to_thread`, `BackgroundTasks`).
  - WebSocket broadcast performance: async gathering, slow-client shielding, backpressure.
  - Frontend rendering latency: Lightweight Charts instantiation, high-frequency DOM updates, layout thrashing, memory leaks in JS long-polling/WS listeners.

### Domain 4: Broker & SSOT Data Synchronization Audit
- **Key Modules**: `al_sangmoo/infrastructure/persistence.py`, `al_sangmoo/infrastructure/kis_broker.py`, `server.py`, `al_sangmoo/api/hub.py`, SQLite databases (`my_portfolio.db`, `portfolio.db`).
- **Scope**:
  - SQLite lock contention (`database is locked`) between background scans and manual order writes.
  - State consistency across SQLite database, live WebSocket stream, in-memory portfolio state, and real KIS Broker account balance.
  - Race conditions during concurrent trade execution and price sync.
  - Local chart JSON caching staleness and invalidation.

### Domain 5: API Calling Robustness & Error Handling
- **Key Modules**: `al_sangmoo/infrastructure/kis_broker.py`, `al_sangmoo/infrastructure/market_data.py`, `youtube_stream_scanner.py`, `server.py`
- **Scope**:
  - KIS OpenAPI TR transaction handling: token renewal, rate-limiting (TPS limits), exponential backoff with jitter.
  - External API failure handling: yfinance rate limits/blocks, YouTube feed failures, network disconnects, market closure behavior.
  - Timeout protections, global exception handling, error masking vs traceback leakage.

### Domain 6: Dashboard Usability & Real-Time UX Inspection
- **Key Modules**: `al_sangmoo_dashboard.html`, `dashboard_terminal.html`, `frontend/`, `static/`, `js/`
- **Scope**:
  - Bloomberg Dark design system execution: contrast, alignment, typography, visual hierarchy.
  - 3-Slot Visualizer (Radar, Pullback, Cloud Trampoline) usability and responsive responsiveness.
  - Timeframe switching (Daily, 60m, 15m) fluidity and chart synchronization.
  - 1-Click order execution flow: confirmation modals, safety guardrails, slippage display, feedback latency.
  - Long-session resilience: WebSocket reconnection lifecycle, reconnection backoff, heartbeat keep-alive, desync detection.

## Execution Schedule
1. **Phase 1: Dispatch 6 Explorers** (Parallel static analysis).
2. **Phase 2: Monitor & Collect Reports** (Review each explorer report for completeness and line-level citations).
3. **Phase 3: Synthesis & Consolidated Report Generation** (Compile `system_audit_report.md` with Executive Summary, Severity Matrix, 6 Domain Deep Dives, and Prioritized Remediation Roadmap).
4. **Phase 4: Verification & Handoff** (Ensure 100% read-only integrity, verify all acceptance criteria, and send completion message).
