# Handoff Report: Domain 2 Code Architecture & Spaghetti Code Audit

## 1. Observation
- **Clean Architecture Bleeding**:
  - `al_sangmoo/domain/risk/portfolio_guardian.py:15-18, 148-154`: Imports `db_manager`, `default_kis_broker`, `hub`, `yf`, and directly executes SQL `UPDATE my_portfolio` statements.
  - `al_sangmoo/domain/risk/autopilot_trader.py:16-18, 116, 242, 269`: Imports `db_manager`, `default_kis_broker`, `hub`, imports `from generate_dashboard_feed import build_dashboard_data`, executes broker orders, manages asyncio background daemon scheduler loops, and reads `dashboard_data.json` directly from filesystem.
  - `al_sangmoo/domain/reconciliation.py:11-16, 57-100`: Directly imports concrete `default_kis_broker` and `al_sangmoo.infrastructure.persistence`, executing raw SQL mutations.
  - `al_sangmoo/domain/quant/multi_timeframe.py:5`: Directly imports `yfinance as yf` to download market data over network inside the quant domain.
- **Broken Dependency Inversion**:
  - `al_sangmoo/domain/interfaces/execution_gateway.py:7`: Abstract interface `IExecutionGateway` is defined with `submit_buy_order`, `submit_sell_order`, `get_positions`, `get_account_balance`.
  - `al_sangmoo/infrastructure/brokers/kis_broker.py:27`: `KISBrokerAdapter` does NOT inherit from `IExecutionGateway` and implements different methods (`place_order`, `get_overseas_balance`, etc.).
  - All consumers (`server.py`, `portfolio.py`, `broker.py`, `autopilot_trader.py`, `portfolio_guardian.py`, `reconciliation.py`) hardcode concrete `default_kis_broker`.
- **Presentation Layer Bleeding & Silent Exception Masking**:
  - `server.py:78-100`: Standalone quant function `get_recommended_position_size(...)` executes `yf.download` and position sizing directly in `server.py`.
  - `server.py:83`: Evaluates `if isinstance(df.columns, pd.MultiIndex):`, but `pandas as pd` is NOT imported in `server.py`. This throws `NameError: name 'pd' is not defined`, which is swallowed by `except Exception:` on line 90, forcing dummy values (`cur_price = 100.0, atr = 2.0`) on every single call.
- **Runtime Module Reflection in Routers**:
  - `al_sangmoo/interfaces/api/routers/scanner.py:17-29, 44-47`: Inspects `sys.modules["server"]` at runtime, reading and modifying `_is_scanning` on the `server` module object, and dynamically retrieves `build_dashboard_data` via `getattr(server_mod, "build_dashboard_data", generate_dashboard_feed.build_dashboard_data)`.
- **Root-Level Module Coupling via db_manager.py**:
  - `db_manager.py:1-25` sits at repo root as a legacy alias facade. Deep internal modules (`domain/risk/*`, `interfaces/api/routers/*`, `generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`) import `db_manager` from root instead of `al_sangmoo.infrastructure.persistence`.
- **Latent Regime Inversion Bug in Feed Generator**:
  - `generate_dashboard_feed.py:462`: Reads `msi_val = float(macro_info.get("msi", 50.0))`.
  - In `wepoll_latest_stream.json` (`macro_info`), MSI is stored under `macro_info["macro_climate"]["msi_score"]` (e.g. 72.8).
  - Because `"msi"` does not exist at root of `macro_info`, `msi_val` ALWAYS evaluates to default `50.0`.
  - Then `is_bull_regime = (msi_val < 65.0)` ALWAYS evaluates to `True` (BULL REGIME), completely blinding the conviction ranker to actual macro bear/defense regimes.
- **Universe Discrepancy & Double Computation Pipeline**:
  - `al_sangmoo_daily_bot.py:76-80`: Hardcodes 23-ticker `UNIVERSE` list.
  - In `al_sangmoo_daily_bot.py:136-229`, `scan_and_select_2x2x2` downloads market data and computes indicators for 23 tickers. Then in `al_sangmoo_daily_bot.py:784`, `main()` ALSO calls `generate_dashboard_feed.build_dashboard_data()`, which downloads and computes all 60 tickers.
- **Parameter Fragmentation & Utility Triplication**:
  - Stop loss thresholds diverge: `portfolio_guardian.py` (-4% / `* 0.96`), `autopilot_trader.py` (-4% / `* 0.96`), `conviction_engine.py` (-4% / `* 0.96`) vs `persistence.py` (-3% / `* 0.97`), `al_sangmoo_daily_bot.py` (-3% / `* 0.97`), `reconciliation.py` (-3% / `* 0.97`).
  - Macro multipliers in `position_sizer.py`: `calculate_dynamic_position_size` uses `0.35` and `0.75`, whereas `calculate_slot_position_size` uses `0.50` and `0.85`.
  - `atomic_save_json` / `atomic_read_json` copy-pasted in `atomic_io.py`, `generate_dashboard_feed.py`, and `youtube_stream_scanner.py`.
  - `STOCK_DICT` duplicated between `constants.py` (60 tickers) and `youtube_stream_scanner.py` (23 tickers).
- **Monolithic God-Scripts**:
  - `al_sangmoo_daily_bot.py`: 826 lines combining scanning, database persistence, 280+ lines of HTML string templating, Markdown formatting, and SMTP Gmail transmission.
  - `youtube_stream_scanner.py`: 504 lines combining yt-dlp subprocess execution, regex parsing, VTT text extraction, macro gauge downloads, NLP sentiment scoring, and JSON caching.
- **CQRS Violations on Read Endpoints**:
  - `al_sangmoo/interfaces/api/routers/dashboard.py:44`: On every `GET /api/dashboard`, it synchronously invokes `db_manager.sync_portfolio_prices()`, executing external network calls (KIS TR HHDFS00000300 or Yahoo Finance) and issuing SQLite `UPDATE my_portfolio` transactions.
  - `frontend/js/websocket.js:209-215`: Frontend polling loop invokes `GET /api/dashboard` every 15s indefinitely even when WebSocket is connected.
- **File Sprawl & Artifact Accumulation**:
  - 4 identical byte-for-byte SHA256 copies of 38.8KB dashboard HTML: `al_sangmoo_dashboard.html`, `HTML_대시보드_모음/01_R상무_통합_퀀트_대시보드.html`, `html_dashboards/01_R상무_통합_퀀트_대시보드.html`, `html_dashboards/01_알상무_통합_퀀트_대시보드.html`.
  - `backups/` directory contains 123 SQLite `.db` snapshot files without retention limit or rotation cleanup policy.
  - Leftover test artifact `test_quant_trades_p5_2.db` and raw subtitle files in root.
  - In `portfolio_guardian.py`, claimed 50% partial take-profit is not implemented (exits 100% full position).

## 2. Logic Chain
1. *Clean Architecture Inversion*: In a layered architecture, the domain core contains pure business logic with zero external I/O dependencies. Because `portfolio_guardian.py`, `autopilot_trader.py`, and `reconciliation.py` were placed under `al_sangmoo/domain/` but implemented as daemons performing network I/O, database writes, and WebSocket broadcasts, the domain layer has bled into infrastructure and presentation.
2. *Polymorphism Breakdown*: Because `KISBrokerAdapter` did not inherit from `IExecutionGateway` and callers directly imported concrete singletons, the abstract port cannot be substituted or tested in isolation.
3. *Regime Calculation Latency*: Because `generate_dashboard_feed.py:462` accessed `macro_info.get("msi", 50.0)` instead of `macro_info["macro_climate"]["msi_score"]`, the market regime classification was permanently locked into Bull mode (`50.0 < 65.0`), blinding downstream position sizers and conviction engines during Bear/Defense regimes.
4. *CQRS Breakdown*: By triggering `sync_portfolio_prices()` inside `GET /api/dashboard`, every HTTP read request becomes a blocking write operation. Continuous 15s polling from frontend clients creates SQLite lock contention against background daemons.
5. *Code Duplication & Math Divergence*: The presence of legacy scripts (`al_sangmoo_daily_bot.py` with 23 tickers vs `generate_dashboard_feed.py` with 60 tickers) causes redundant data fetching and contradictory recommendations. Divergent stop-loss thresholds (-3% vs -4%) cause UI text to contradict daemon execution.

## 3. Caveats
- This audit was conducted as a **strictly read-only static analysis**. No source files or runtime environments were modified.
- Network calls to live KIS broker endpoints were not executed during this audit; analysis was performed by tracing static method flows and TR payload handling in `kis_broker.py`.
- Dynamic WebSocket message flow was verified via static client/server code tracing.

## 4. Conclusion
The Al-Sangmoo Quant Terminal possesses strong quantitative foundations (Ichimoku indicator engine, GS-style conviction ranking, MSI 2.0 framework), but suffers from **architectural layering inversion, CQRS violations, silent exception masking, macro regime key mismatches, and monolithic file sprawl**.

**Actionable Remediation Priority**:
1. **P0**: Fix latent MSI regime key bug in `generate_dashboard_feed.py:462` and `NameError: pd` bug in `server.py:83`.
2. **P0**: Fix CQRS violation in `GET /api/dashboard` by decoupling price synchronization into a background worker and making `GET /api/dashboard` a pure read operation.
3. **P1**: Decouple domain layer from infrastructure daemons by moving `PortfolioGuardian`, `AutoPilotTrader`, and `check_sync` to `al_sangmoo/services/`.
4. **P1**: Unify `IExecutionGateway` broker interface and implement polymorphic adapter binding.
5. **P2**: Decompose monolithic scripts (`al_sangmoo_daily_bot.py`, `youtube_stream_scanner.py`) and eliminate redundant HTML mirrors / unconstrained database backups.

## 5. Verification Method
- **Verify Domain Layer Purity**:
  ```powershell
  grep -rn "import db_manager" d:\코딩\R\al_sangmoo\domain\
  grep -rn "import yfinance" d:\코딩\R\al_sangmoo\domain\
  ```
- **Verify server.py Position Sizing**:
  ```powershell
  python -c "import server; print(server.get_recommended_position_size('NVDA'))"
  ```
- **Verify MSI Regime Extraction**:
  ```powershell
  python -c "import json; d=json.load(open('d:/코딩/R/wepoll_latest_stream.json', encoding='utf-8')); print('macro_climate msi_score:', d.get('macro_climate', {}).get('msi_score')); print('root msi:', d.get('msi'))"
  ```
- **Verify CQRS GET Route Performance**:
  Inspect `al_sangmoo/interfaces/api/routers/dashboard.py` to confirm `GET /api/dashboard` calls `get_live_portfolio()` and contains zero SQL write statements or external HTTP calls.
- **Verify Test Suites**:
  ```powershell
  pytest d:\코딩\R\tools_and_tests\test_phase5_1_security.py
  pytest d:\코딩\R\tools_and_tests\test_phase5_2_concurrency.py
  pytest d:\코딩\R\tools_and_tests\test_phase5_3_ssot_quant.py
  ```
