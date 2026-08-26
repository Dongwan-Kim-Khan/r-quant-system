# Project: Al-Sangmoo Institutional Quant Trading Platform End-to-End Audit & Remediation

## Architecture
The Al-Sangmoo Quant Trading Platform is an institutional-grade algorithmic swing-trading system built on Python/FastAPI with SQLite WAL mode, asynchronous WebSocket broadcast hubs, Korea Investment & Securities (KIS) OpenAPI integration with Yahoo Finance fallback, and a lightweight trading terminal frontend.

- **Presentation / API Layer**: FastAPI (`server.py`, `al_sangmoo/interfaces/api/routers/`), WebSocket Broadcast Hub (`al_sangmoo/api/hub.py`), Frontend Terminal (`frontend/`).
- **Domain Quant Layer (SSOT)**: Mathematical rolling indicators (`ichimoku.py`), 17-Year Quant Scoring (`scoring.py`), Macro Stance Index 2.0 (`macro.py`), Conviction Ranking (`conviction_engine.py`), Position Sizing & Risk Guardrails (`al_sangmoo/domain/risk/`).
- **Background Autonomous Daemons**: `PortfolioGuardian` (-4% SL, +15% Trailing TP, 3M RS exit), `AutopilotTrader` (Macro-conditioned autonomous order placement), `Scanner` (Single-flight universe analysis).
- **Infrastructure Layer**: Unified Database Persistence (`persistence.py`), Atomic File I/O (`atomic_io.py`), Broker Gateways (`kis_broker.py`, `paper_broker.py`).

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Mutating Route Authentication (SEC-01) | Add API Key validation dependency to all state-mutating endpoints | M1 | Survey |
| 2 | WebSocket Ping Frame JSON Parsing (SEC-02) | Add missing `import json` to `server.py` for ping keepalive | M1 | Survey |
| 3 | WebSocket CSWSH Origin Guard (SEC-03) | Enforce strict origin validation for WebSocket connections | M1 | Survey |
| 4 | Atomic Token Cache Persistence (SEC-04) | Use `atomic_save_json` for `.kis_token_*.json` persistence | M1 | Survey |
| 5 | Shared Global Order Mutex (BRK-01) | Unify and extend `ORDER_MUTEX` scope across `broker.py` & `portfolio.py` | M2 | Survey |
| 6 | Non-blocking Broker Async Offloading (BRK-02) | Wrap synchronous broker TR and HTTP requests in `asyncio.to_thread` | M2 | Survey |
| 7 | Broker Network Timeout Guard (BRK-03) | Protect against duplicate fills during timeout retries | M2 | Survey |
| 8 | Execution Log Audit Synchronization (SYNC-01) | Add `record_execution_log` to manual buy/sell endpoints | M3 | Survey |
| 9 | Trade History Record on Position Close (SYNC-02) | Insert closed trades into `trade_history` in `persistence.py` | M3 | Survey |
| 10 | Frontend WebSocket Event Optimization (SYNC-03) | Prevent redundant full HTTP dashboard re-fetch on WS events | M3 | Survey |
| 11 | Chart Race Condition Protection (SYNC-04) | Guard against stale ticker updates on asynchronous chart fetch | M3 | Survey |
| 12 | SQLite Concurrency & Test Isolation (DB-01) | Ensure WAL mode transaction atomicity and Windows test cleanup | M4 | Survey |
| 13 | Hard Stop-Loss SSOT Calibration (QUANT-01) | Align all -3.0% legacy stop-loss references to institutional -4.0% (`0.96`) | M5 | Survey |
| 14 | Deduplication & Dead Code Removal (CLEAN-01) | Deduplicate constants, atomic IO, chart router lines, and research test names | M5 | Survey |
| 15 | E2E Automated Test Suite & Regression (VERIF-01) | 100% pass across all Phase 5 test suites with zero regression | M6 | Survey |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Security & Credential Protection | Fix SEC-01, SEC-02, SEC-03, SEC-04 in `server.py`, `routers/`, `kis_broker.py` | None | PLANNED |
| M2 | API & Broker Gateway Resilience | Fix BRK-01, BRK-02, BRK-03 in `broker.py`, `portfolio.py`, `kis_broker.py`, `order_guardrail.py` | M1 | PLANNED |
| M3 | Backend-Frontend Synchronicity | Fix SYNC-01, SYNC-02, SYNC-03, SYNC-04 in `portfolio.py`, `persistence.py`, `websocket.js`, `chart.js` | M1, M2 | PLANNED |
| M4 | Server Stability & Concurrency | Fix DB-01, ensure lifespan and SQLite WAL concurrency safety | M2 | PLANNED |
| M5 | Quant SSOT & Code Cleanliness | Fix QUANT-01, CLEAN-01 across `reconciliation.py`, `macro_guardrail.py`, `persistence.py`, `paper_broker.py`, `position_sizer.py`, `al_sangmoo_daily_bot.py`, `charts.py`, `youtube_stream_scanner.py`, `generate_dashboard_feed.py`, research test files | M3, M4 | PLANNED |
| M6 | Final Verification & Hardening | Run all Phase 5 test suites (`test_phase5_1` to `5_4`, `test_autopilot.py`, adversarial suites), ensure 100% pass | M1, M2, M3, M4, M5 | PLANNED |

## Interface Contracts
### Mutating Endpoints Authentication
- Header: `X-API-Key: <token>` (or optional when `REQUIRE_AUTH=False`, mandatory when configured).

### Order Execution Mutex (`ORDER_MUTEX`)
- Location: `al_sangmoo.domain.risk.order_guardrail.ORDER_MUTEX`
- Usage: `async with ORDER_MUTEX:` encompasses pre-trade guardrail checks, broker gateway placement, and SQLite persistence.

### Quant SSOT Rules
- Hard Stop-Loss: -4.0% (`buy_price * 0.96` / `constants.STOP_LOSS_PCT`)
- Trailing Take-Profit: +15.0% (`buy_price * 1.15` / `constants.TAKE_PROFIT_PCT`)
- Capital Allocation: 3-slot integer shares ($7,500 / 10M KRW per slot)
- Macro Regime: MSI 2.0 (0-100) -> CASH_EXIT (<30), DEFENSE_HOLD (30-50), SELECTIVE_BUY (50-70), ACTIVE_BUY (>=70)

## Code Layout
- `server.py`: FastAPI application entry point, lifespan, middleware, WebSocket route.
- `al_sangmoo/interfaces/api/routers/`: API routers (`broker.py`, `portfolio.py`, `autopilot.py`, `charts.py`, `dashboard.py`, `guardian.py`, `scanner.py`).
- `al_sangmoo/domain/quant/`: Pure quantitative logic (`ichimoku.py`, `scoring.py`, `macro.py`, `conviction_engine.py`).
- `al_sangmoo/domain/risk/`: Risk management (`order_guardrail.py`, `portfolio_guardian.py`, `autopilot_trader.py`, `macro_guardrail.py`, `position_sizer.py`).
- `al_sangmoo/domain/reconciliation.py`: Daily broker-to-local portfolio reconciliation.
- `al_sangmoo/infrastructure/`: Persistence (`persistence.py`, `atomic_io.py`), Brokers (`kis_broker.py`, `paper_broker.py`).
- `frontend/`: UI assets (`index.html`, `js/ui.js`, `js/api.js`, `js/websocket.js`, `js/chart.js`).
- `tools_and_tests/`: Test suites (`test_phase5_1_security.py`, `test_phase5_2_concurrency.py`, `test_phase5_3_ssot_quant.py`, `test_phase5_4_kis_modular.py`).
