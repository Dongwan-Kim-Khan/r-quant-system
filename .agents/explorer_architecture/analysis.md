# Comprehensive Static Analysis & Architectural Audit Report
**Domain 2: Code Architecture & Spaghetti Code Audit**
**Target System**: Al-Sangmoo Quant Trading Platform (d:\코딩\R)
**Auditor**: Software Architecture Auditor (Explorer 2)
**Date**: 2026-08-25
**Mode**: STRICTLY READ-ONLY AUDIT

---

## Executive Summary

A comprehensive, multi-layer static analysis of the Al-Sangmoo Quant Terminal codebase was conducted across domain modules, infrastructure adapters, interface routers, background daemons, root automation scripts, and the frontend architecture.

While previous hardening iterations (Phases 5.1–5.3) established foundational modular packages (`al_sangmoo/domain/quant`, `al_sangmoo/infrastructure/persistence`, `al_sangmoo/interfaces/api/routers`), the audit uncovered **11 significant architectural defects** spanning layer bleeding, broken abstraction boundaries, CQRS violations, silent exception masking, macro regime calculation bugs, and file sprawl.

### Architectural Health Scorecard

| Architectural Dimension | Rating | Key Finding |
|---|---|---|
| **Layering & Domain Purity** | ⚠️ Needs Remediation | Domain modules (`risk/autopilot_trader.py`, `risk/portfolio_guardian.py`, `reconciliation.py`) directly import concrete infrastructure, issue raw SQL queries, and manage async daemon loops. |
| **Coupling & Dependency Inversion** | ⚠️ Needs Remediation | `IExecutionGateway` abstraction is bypassed; consumers directly hardcode concrete `default_kis_broker`. `scanner.py` uses runtime monkey-patch reflection via `sys.modules["server"]`. |
| **Quantitative SSOT & Math Consistency** | ❌ High Risk / Defect | Latent regime bug in `generate_dashboard_feed.py` (`"msi"` vs `"msi_score"`) causes permanent Bull regime misclassification. Stop-loss parameters diverge between -3% and -4% across 6 files. |
| **CQRS & Read/Write Separation** | ❌ High Risk / Defect | `GET /api/dashboard` triggers synchronous external network calls (KIS/Yahoo) and SQLite `UPDATE` transactions every 15s via frontend polling. |
| **Modularity & Cyclomatic Complexity** | ⚠️ Needs Remediation | Monolithic scripts (`al_sangmoo_daily_bot.py` 826 lines, `youtube_stream_scanner.py` 504 lines) combine scraping, calculations, HTML generation, and SMTP. |
| **Artifacts & Asset Governance** | ⚠️ Needs Remediation | 4 identical byte-for-byte HTML copies; 123 unbounded SQLite backup files; leftover test DBs and raw subtitle files in root. |

---

## Severity Classification Matrix

| Issue ID | Severity | Title | Affected File(s) |
|---|---|---|---|
| **ARCH-01** | **High** | Domain Layer Bleeding & Inversion of Control Violations | `al_sangmoo/domain/risk/portfolio_guardian.py`<br>`al_sangmoo/domain/risk/autopilot_trader.py`<br>`al_sangmoo/domain/reconciliation.py`<br>`al_sangmoo/domain/quant/multi_timeframe.py` |
| **ARCH-02** | **Medium** | Broken Dependency Inversion in Broker Gateway (`IExecutionGateway`) | `al_sangmoo/domain/interfaces/execution_gateway.py`<br>`al_sangmoo/infrastructure/brokers/kis_broker.py`<br>`al_sangmoo/interfaces/api/routers/broker.py`<br>`al_sangmoo/interfaces/api/routers/portfolio.py` |
| **ARCH-03** | **Medium** | Presentation Layer Bleeding & Silent Exception Masking in `server.py` | `server.py` (lines 78–100) |
| **ARCH-04** | **High** | Runtime Module Reflection & Monkey-Patch State Coupling | `al_sangmoo/interfaces/api/routers/scanner.py` (lines 15–29, 44–47) |
| **ARCH-05** | **Medium** | Root-Level Module Coupling via Legacy Facade `db_manager.py` | `db_manager.py`<br>All routers and domain risk modules |
| **ARCH-06** | **High** | Latent Regime Inversion Bug & MSI Key Mismatch in Feed Generator | `generate_dashboard_feed.py` (line 462) |
| **ARCH-07** | **High** | Universe Discrepancy & Double Computation Pipeline | `al_sangmoo_daily_bot.py` (lines 76–80, 136–229, 784)<br>`al_sangmoo/core/constants.py` |
| **ARCH-08** | **Medium** | Parameter Fragmentation Across Risk Rules & Utility Duplication | `persistence.py`, `portfolio_guardian.py`, `position_sizer.py`, `atomic_io.py`, `youtube_stream_scanner.py` |
| **ARCH-09** | **High** | Monolithic God-Scripts with Excessive Mixed Responsibilities | `al_sangmoo_daily_bot.py` (826 lines)<br>`youtube_stream_scanner.py` (504 lines) |
| **ARCH-10** | **High** | CQRS Read Query Side-Effects & Continuous Write Contention via GET | `al_sangmoo/interfaces/api/routers/dashboard.py` (lines 42–46)<br>`al_sangmoo/infrastructure/persistence.py` (lines 328–429)<br>`frontend/js/websocket.js` (lines 209–215) |
| **ARCH-11** | **Medium** | Redundant Dashboard Mirrors, Backup Accumulation & Spec Drift | `al_sangmoo_dashboard.html`, `HTML_대시보드_모음/`, `html_dashboards/`, `backups/`, `portfolio_guardian.py` |

---

## Detailed Audit Findings

```
================================================================================
FINDING ARCH-01: Domain Layer Bleeding & Inversion of Control Violations
================================================================================
```
- **Severity**: High
- **Exact File Paths & Lines**:
  - `al_sangmoo/domain/risk/portfolio_guardian.py`: lines 15–18, 148–154
  - `al_sangmoo/domain/risk/autopilot_trader.py`: lines 16–18, 116–117, 242, 269
  - `al_sangmoo/domain/reconciliation.py`: lines 11–16, 57–100
  - `al_sangmoo/domain/quant/multi_timeframe.py`: line 5
- **Problematic Code Snippets**:
  ```python
  # al_sangmoo/domain/risk/portfolio_guardian.py
  import db_manager
  from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
  from al_sangmoo.api.hub import hub
  ...
  with db_manager.get_connection() as conn:
      conn.cursor().execute("""
      UPDATE my_portfolio
      SET current_price = ?, current_value = ?, pnl_pct = ?, pnl_amount = ?
      WHERE id = ?
      """, (cur_price, cur_val, pnl_pct, pnl_amt, holding_id))
      conn.commit()
  ```
  ```python
  # al_sangmoo/domain/risk/autopilot_trader.py
  import db_manager
  from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
  from al_sangmoo.api.hub import hub
  ...
  from generate_dashboard_feed import build_dashboard_data
  await asyncio.to_thread(build_dashboard_data)
  ...
  with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
      feed = json.load(f)
  ```
- **Architectural Defect & Risk**:
  In Clean Architecture and Hexagonal Architecture, the `domain/` layer represents the enterprise business rules and must remain 100% pure—independent of databases, external brokers, network clients (`yfinance`), presentation/WebSockets (`hub`), or filesystem I/O.
  Here, `portfolio_guardian.py`, `autopilot_trader.py`, and `reconciliation.py` reside inside `al_sangmoo/domain/`, yet act as full application daemons. They directly execute raw SQL `UPDATE` statements, manage `asyncio` task schedulers, perform file I/O on `dashboard_data.json`, and invoke WebSocket broadcasts. This causes high coupling, prevents unit testing domain logic without mocking I/O, and violates the Dependency Rule.
- **Remediation Strategy**:
  1. Extract pure domain risk calculation rules (e.g. `evaluate_exit_conditions`, `evaluate_reconciliation_diffs`) into pure functions inside `al_sangmoo/domain/risk/`.
  2. Move background daemons, schedulers, and coordination logic to a new application/service layer: `al_sangmoo/services/guardian_service.py`, `al_sangmoo/services/autopilot_service.py`, and `al_sangmoo/services/reconciliation_service.py`.
  3. Inject repositories (`IPortfolioRepository`) and broker gateways (`IExecutionGateway`) via constructor dependency injection instead of global concrete imports.

---

```
================================================================================
FINDING ARCH-02: Broken Dependency Inversion in Broker Gateway (IExecutionGateway)
================================================================================
```
- **Severity**: Medium
- **Exact File Paths & Lines**:
  - `al_sangmoo/domain/interfaces/execution_gateway.py`: lines 7–28
  - `al_sangmoo/infrastructure/brokers/kis_broker.py`: line 27
  - `al_sangmoo/interfaces/api/routers/broker.py`: line 4
  - `al_sangmoo/interfaces/api/routers/portfolio.py`: line 13
- **Problematic Code Snippets**:
  ```python
  # al_sangmoo/domain/interfaces/execution_gateway.py
  class IExecutionGateway(ABC):
      @abstractmethod
      def submit_buy_order(self, ticker: str, price: float, quantity: float, stop_loss: float = None, target_price: float = None) -> Dict[str, Any]: ...
      @abstractmethod
      def submit_sell_order(self, position_id: int, price: float, reason: str = "MANUAL_SELL") -> Dict[str, Any]: ...

  # al_sangmoo/infrastructure/brokers/kis_broker.py
  class KISBrokerAdapter: # DOES NOT INHERIT FROM IExecutionGateway
      def place_order(self, ticker: str, side: str, qty: int, price: float, order_type: str = "00", exchange: str = "NASD") -> Dict[str, Any]: ...
  ```
- **Architectural Defect & Risk**:
  `IExecutionGateway` was introduced as an abstract interface, but `KISBrokerAdapter` (the actual production broker implementation) does not implement it. Furthermore, all callers (`server.py`, `portfolio.py`, `broker.py`, `autopilot_trader.py`, `portfolio_guardian.py`, `reconciliation.py`) directly import the concrete singleton `default_kis_broker`. This makes it impossible to substitute broker implementations (e.g. swapping KIS for Interactive Brokers, Alpaca, or `PaperTradingBroker`) without editing dozens of application files.
- **Remediation Strategy**:
  1. Unify the broker interface signatures in `IExecutionGateway` (`place_order`, `get_account_balance`, `get_live_price`, `get_positions`).
  2. Have `KISBrokerAdapter` and `PaperTradingBroker` both inherit from `IExecutionGateway`.
  3. Introduce a factory / dependency provider `get_execution_gateway()` that returns the configured broker adapter based on environment configuration.

---

```
================================================================================
FINDING ARCH-03: Presentation Layer Bleeding & Silent Exception Masking in server.py
================================================================================
```
- **Severity**: Medium
- **Exact File Paths & Lines**:
  - `server.py`: lines 78–100
- **Problematic Code Snippets**:
  ```python
  # server.py lines 78-100
  def get_recommended_position_size(ticker: str = "SPY", equity: float = 100000.0, msi: float = 50.0, **kwargs):
      from al_sangmoo.domain.risk.position_sizer import calculate_dynamic_position_size, calculate_atr
      import yfinance as yf
      try:
          df = yf.download(ticker, period="1mo", interval="1d", progress=False)
          if isinstance(df.columns, pd.MultiIndex):  # BUG: 'pd' is NEVER imported in server.py!
              if 'Close' in df.columns.get_level_values(0):
                  df.columns = df.columns.get_level_values(0)
                  ...
          cur_price = float(df['Close'].iloc[-1]) if not df.empty else 100.0
          atr = calculate_atr(df) if not df.empty else (cur_price * 0.02)
      except Exception:
          cur_price = 100.0
          atr = 2.0
      res = calculate_dynamic_position_size(
          portfolio_equity=equity,
          current_price=cur_price,
          atr_14=atr,
          msi_score=msi
      )
      res["ticker"] = ticker
      return res
  ```
- **Architectural Defect & Risk**:
  1. `server.py` should only configure FastAPI, mount routers, configure middleware, and manage lifecycle. Placing quant data fetching and calculation routines directly in `server.py` violates single-responsibility and separation of concerns.
  2. **Active Bug**: `pandas as pd` is never imported at module level or inside `get_recommended_position_size()`. When `isinstance(df.columns, pd.MultiIndex)` executes, it raises `NameError: name 'pd' is not defined`. The broad `except Exception:` immediately swallows the error and forces dummy values (`cur_price = 100.0, atr = 2.0`) on every single execution.
- **Remediation Strategy**:
  1. Remove `get_recommended_position_size()` from `server.py`.
  2. If a position sizing API endpoint is needed, place it inside `al_sangmoo/interfaces/api/routers/portfolio.py` and delegate to domain position sizing services.

---

```
================================================================================
FINDING ARCH-04: Runtime Module Reflection & Monkey-Patch State Coupling
================================================================================
```
- **Severity**: High
- **Exact File Paths & Lines**:
  - `al_sangmoo/interfaces/api/routers/scanner.py`: lines 15–29, 44–47
- **Problematic Code Snippets**:
  ```python
  # al_sangmoo/interfaces/api/routers/scanner.py
  def get_is_scanning() -> bool:
      global _is_scanning
      if "server" in sys.modules:
          s = sys.modules["server"]
          if hasattr(s, "_is_scanning"):
              return getattr(s, "_is_scanning")
      return _is_scanning

  def set_is_scanning(val: bool):
      global _is_scanning
      _is_scanning = val
      if "server" in sys.modules:
          s = sys.modules["server"]
          setattr(s, "_is_scanning", val)
  ...
  # Inside _sync_worker():
  server_mod = sys.modules.get("server")
  build_fn = getattr(server_mod, "build_dashboard_data", generate_dashboard_feed.build_dashboard_data)
  data = build_fn()
  ```
- **Architectural Defect & Risk**:
  `scanner.py` queries `sys.modules["server"]` dynamically at runtime to inspect and set attributes on the parent module. This runtime monkey-patching anti-pattern introduces hidden coupling, makes control flow unpredictable, and creates race conditions when multiple workers or test runners import the modules in different orders.
- **Remediation Strategy**:
  1. Encapsulate scan state into an explicit `ScanStateManager` singleton or inject it via FastAPI dependency injection (`Depends()`).
  2. Directly import canonical feed generator or inject the scanner pipeline service.

---

```
================================================================================
FINDING ARCH-05: Root-Level Module Coupling via Legacy Facade db_manager.py
================================================================================
```
- **Severity**: Medium
- **Exact File Paths & Lines**:
  - `db_manager.py`: lines 1–25
  - `al_sangmoo/domain/risk/autopilot_trader.py`: line 16
  - `al_sangmoo/domain/risk/portfolio_guardian.py`: line 15
  - `al_sangmoo/interfaces/api/routers/portfolio.py`: line 11
  - `al_sangmoo/interfaces/api/routers/dashboard.py`: line 43
  - `al_sangmoo/interfaces/api/routers/scanner.py`: line 5
- **Problematic Code Snippets**:
  ```python
  # db_manager.py
  from al_sangmoo.infrastructure.persistence import (
      get_connection, init_database, get_db, init_db, ...
  )
  ```
- **Architectural Defect & Risk**:
  `db_manager.py` is a top-level legacy facade that exists solely to re-export persistence functions. Core domain modules and API routers import `db_manager` from the root namespace instead of using `al_sangmoo.infrastructure.persistence`. This creates an implicit dependency on the workspace root directory being in `sys.path`, preventing clean packaging and distribution of the `al_sangmoo` library.
- **Remediation Strategy**:
  1. Refactor all imports across `domain/`, `interfaces/`, and scripts to import directly from `al_sangmoo.infrastructure.persistence`.
  2. Mark `db_manager.py` as deprecated and eliminate internal usage.

---

```
================================================================================
FINDING ARCH-06: Latent Regime Inversion Bug & MSI Key Mismatch in Feed Generator
================================================================================
```
- **Severity**: High
- **Exact File Paths & Lines**:
  - `generate_dashboard_feed.py`: line 462
- **Problematic Code Snippets**:
  ```python
  # generate_dashboard_feed.py lines 295 vs 462
  # Line 295 (Correct extraction):
  macro_climate = macro_info.get("macro_climate", {}) if isinstance(macro_info, dict) else {}
  msi_score = float(macro_climate.get("msi_score", 65.0))

  # Line 462 (DEFECTIVE extraction):
  msi_val = float(macro_info.get("msi", 50.0))  # BUG: macro_info has no "msi" key! Always falls back to 50.0!
  is_bull_regime = (msi_val < 65.0)             # (50.0 < 65.0) -> ALWAYS True (BULL REGIME)!
  
  conviction_res = rank_and_select_top_picks(
      candidates=all_candidates,
      portfolio_equity_usd=7500.0,
      msi_score=msi_val,
      is_bull_regime=is_bull_regime
  )
  ```
- **Architectural Defect & Risk**:
  `wepoll_latest_stream.json` (`macro_info`) stores MSI under `macro_info["macro_climate"]["msi_score"]` (e.g. 72.8).
  In `generate_dashboard_feed.py:462`, `macro_info.get("msi", 50.0)` is queried instead of `macro_climate.get("msi_score")`. As a result, `msi_val` ALWAYS resolves to default `50.0`, and `is_bull_regime` ALWAYS evaluates to `True`.
  Even when the actual macro climate is a severe defense/bear shock (e.g. MSI 72.8, Defense Hold), the Goldman Sachs conviction ranker is told the market is in a healthy Bull regime (`is_bull_regime=True`), causing it to allocate 3 slots aggressively instead of triggering 50% cash preservation.
- **Remediation Strategy**:
  1. Fix key access on line 462 to `msi_val = float(macro_climate.get("msi_score", 50.0))`.
  2. Use a strongly-typed Pydantic or dataclass model for `MacroClimatePayload` to prevent dict key mismatch bugs.

---

```
================================================================================
FINDING ARCH-07: Universe Discrepancy & Double Computation Pipeline
================================================================================
```
- **Severity**: High
- **Exact File Paths & Lines**:
  - `al_sangmoo_daily_bot.py`: lines 76–80, 105–230, 784
  - `al_sangmoo/core/constants.py`: lines 5–22
- **Problematic Code Snippets**:
  ```python
  # al_sangmoo_daily_bot.py
  UNIVERSE = [ # 23 tickers
      "NVDA", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AAPL", "AVGO", "COST", "LLY",
      "AMD", "QCOM", "PLTR", "SMCI", "MU", "ARM", "VST", "CEG", "GEV", "ETN",
      "005930.KS", "000660.KS", "012450.KS"
  ]
  ...
  def scan_and_select_2x2x2():
      # Loops over 23 UNIVERSE tickers, downloads data, runs Ichimoku + Scoring ...
  ...
  def main():
      stream_info = youtube_stream_scanner.fetch_latest_wepoll_stream()
      # Calls build_dashboard_data() which loops over 60 WATCHLIST tickers again!
      feed_data = generate_dashboard_feed.build_dashboard_data()
  ```
- **Architectural Defect & Risk**:
  1. `al_sangmoo_daily_bot.py` defines a legacy 23-ticker `UNIVERSE` list, ignoring the SSOT 60-ticker `WATCHLIST` in `al_sangmoo/core/constants.py`.
  2. In `scan_and_select_2x2x2()`, `al_sangmoo_daily_bot.py` independently runs yfinance downloads and technical scoring over 23 tickers. Then in `main()`, it calls `generate_dashboard_feed.build_dashboard_data()`, which downloads and computes indicators for all 60 tickers again.
  3. This causes massive redundant network I/O and results in inconsistent candidate picks between `scan_and_select_2x2x2()` and `build_dashboard_data()`.
- **Remediation Strategy**:
  1. Deprecate the inline scanning loop in `al_sangmoo_daily_bot.py`.
  2. Consolidate `scan_and_select_2x2x2` to delegate directly to `generate_dashboard_feed.build_dashboard_data()` and `al_sangmoo.domain.quant.scoring`.
  3. Ensure all components share the canonical `WATCHLIST` from `constants.py`.

---

```
================================================================================
FINDING ARCH-08: Parameter Fragmentation Across Risk Rules & Utility Duplication
================================================================================
```
- **Severity**: Medium
- **Exact File Paths & Lines**:
  - Stop Loss: `portfolio_guardian.py` (line 165), `autopilot_trader.py` (line 239), `conviction_engine.py` (line 147) vs `persistence.py` (line 415), `al_sangmoo_daily_bot.py` (line 243, 364), `reconciliation.py` (line 94), `backtest/engine.py` (line 20)
  - Macro Multiplier: `al_sangmoo/domain/risk/position_sizer.py` (lines 46–52 vs 111–118)
  - Utility Duplication: `atomic_save_json` / `atomic_read_json` copy-pasted in `atomic_io.py`, `generate_dashboard_feed.py`, `youtube_stream_scanner.py`
  - Stock Dict: `constants.py` (60 tickers) vs `youtube_stream_scanner.py` (23 tickers)
- **Problematic Code Snippets**:
  ```python
  # STOP LOSS DIVERGENCE:
  # portfolio_guardian.py (-4.0%):
  if pnl_pct <= -4.0 or cur_price <= stop_price: ... # * 0.96

  # persistence.py (-3.0%):
  elif pnl_pct <= -3.0:
      advice = f"칼손절 긴급 매도 권고 (손절선 이탈 {pnl_pct:+.2f}%)" # * 0.97
  ```
  ```python
  # MACRO MULTIPLIER DIVERGENCE in position_sizer.py:
  # calculate_dynamic_position_size:
  elif msi_score >= 50.0: macro_mult = 0.35
  elif msi_score >= 30.0: macro_mult = 0.75

  # calculate_slot_position_size:
  elif msi_score >= 50.0: macro_mult = 0.50
  elif msi_score >= 30.0: macro_mult = 0.85
  ```
- **Architectural Defect & Risk**:
  Fragmented parameters across different submodules cause UI advice to contradict automated guardian execution: a position at -3.2% loss will show "Emergency Stop-Loss Advice" in the UI table while Portfolio Guardian does not execute because its hard stop is -4.0%.
  Additionally, copy-pasting utility functions violates DRY and prevents bug fixes in `atomic_io.py` from taking effect in `youtube_stream_scanner.py`.
- **Remediation Strategy**:
  1. Centralize trading constants (`STOP_LOSS_PCT = -0.04`, `TAKE_PROFIT_PCT = 0.15`) in `al_sangmoo/core/constants.py`.
  2. Unify MSI macro scaling multipliers into a single helper function in `position_sizer.py`.
  3. Import `atomic_save_json` and `atomic_read_json` strictly from `al_sangmoo.infrastructure.atomic_io`.
  4. Import `STOCK_DICT` in `youtube_stream_scanner.py` from `al_sangmoo.core.constants`.

---

```
================================================================================
FINDING ARCH-09: Monolithic God-Scripts with Excessive Mixed Responsibilities
================================================================================
```
- **Severity**: High
- **Exact File Paths & Lines**:
  - `al_sangmoo_daily_bot.py`: 826 lines (45KB)
  - `youtube_stream_scanner.py`: 504 lines (24KB)
- **Problematic Code Snippets**:
  ```python
  # al_sangmoo_daily_bot.py mixes:
  # 1. Market scanning (lines 105-230)
  # 2. CSV/SQLite trade ledger updates (lines 277-402)
  # 3. 280 lines of inline HTML templating (lines 404-691)
  # 4. Markdown briefing formatting (lines 693-771)
  # 5. SMTP Gmail transmission (lines 44-73)
  ```
- **Architectural Defect & Risk**:
  `al_sangmoo_daily_bot.py` violates the Single Responsibility Principle (SRP). Changes to the email HTML design require editing the core bot script. Database schema changes require touching the notification script. Cyclomatic complexity is excessively high, making automated testing difficult and error-prone.
- **Remediation Strategy**:
  1. Split `al_sangmoo_daily_bot.py` into dedicated modules:
     - `al_sangmoo/interfaces/cli/daily_bot.py` (CLI runner)
     - `al_sangmoo/infrastructure/notifications/email_notifier.py` (SMTP dispatcher)
     - `al_sangmoo/templates/briefing.html` (Jinja2 or external HTML template)
     - `al_sangmoo/infrastructure/persistence.py` (all database ledger operations)

---

```
================================================================================
FINDING ARCH-10: CQRS Read Query Side-Effects & Continuous Write Contention via GET
================================================================================
```
- **Severity**: High
- **Exact File Paths & Lines**:
  - `al_sangmoo/interfaces/api/routers/dashboard.py`: lines 42–46
  - `al_sangmoo/infrastructure/persistence.py`: lines 328–429
  - `frontend/js/websocket.js`: lines 209–215
- **Problematic Code Snippets**:
  ```python
  # al_sangmoo/interfaces/api/routers/dashboard.py
  @router.get("/api/dashboard")
  async def get_dashboard_data():
      ...
      import db_manager
      live_portfolio = db_manager.sync_portfolio_prices() # SYNCHRONOUS NETWORK + DB WRITES!
      feed_out["portfolio"] = live_portfolio
      ...
  ```
  ```python
  # al_sangmoo/infrastructure/persistence.py
  def sync_portfolio_prices() -> dict:
      ...
      # 1. KIS Live Price or Yahoo Finance network downloads
      # 2. Database write transaction:
      with get_connection() as conn:
          cursor.executemany("""
          UPDATE my_portfolio
          SET current_price = ?, current_value = ?, pnl_pct = ?, pnl_amount = ?, exit_advice = ?
          WHERE id = ?
          """, update_rows)
          conn.commit()
      return get_live_portfolio()
  ```
  ```javascript
  // frontend/js/websocket.js
  _stopHttpPolling() {
      // Sets background polling every 15s even when WS is connected:
      this.pollingInterval = setInterval(async () => {
          const fresh = await ApiClient.getDashboardData(); // Calls /api/dashboard every 15s!
          if (fresh) UI.renderDashboard(fresh);
      }, 15000);
  }
  ```
- **Architectural Defect & Risk**:
  `GET /api/dashboard` is the most frequently called endpoint in the application. Calling `sync_portfolio_prices()` inside this GET route violates CQRS read/write separation and HTTP safe/idempotent semantics.
  Every 15 seconds per active browser tab, a GET request triggers external network downloads and opens SQLite write locks (`UPDATE my_portfolio`). When multiple tabs are open or during network latency spikes, this causes request timeouts, event-loop starvation, and SQLite `database is locked` concurrency errors against background daemons (Portfolio Guardian and AutoPilot).
- **Remediation Strategy**:
  1. `GET /api/dashboard` must strictly call `get_live_portfolio()` (a pure, instantaneous read query without network I/O or DB writes).
  2. Portfolio price updates must be scheduled exclusively on a dedicated background worker (e.g. `PortfolioGuardian` or a background asyncio task) that broadcasts `portfolio_update` over WebSockets when prices change.
  3. Fix `_stopHttpPolling()` in `websocket.js` so it completely halts polling when the WebSocket is actively connected.

---

```
================================================================================
FINDING ARCH-11: Redundant Dashboard Mirrors, Backup Accumulation & Spec Drift
================================================================================
```
- **Severity**: Medium
- **Exact File Paths & Lines**:
  - Duplicate HTMLs: `al_sangmoo_dashboard.html`, `HTML_대시보드_모음/01_R상무_통합_퀀트_대시보드.html`, `html_dashboards/01_R상무_통합_퀀트_대시보드.html`, `html_dashboards/01_알상무_통합_퀀트_대시보드.html` (All 4 have identical SHA256: `B6A3C7D8...`)
  - Backup Sprawl: `backups/quant_trades_*.db` (123 files, no retention limit)
  - Leftover Test Artifacts: `test_quant_trades_p5_2.db`, `live_sub_*.ko.vtt` in workspace root
  - Spec Drift: `al_sangmoo/domain/risk/portfolio_guardian.py` docstring claims 50% partial take-profit, but code exits 100% full position
- **Problematic Code Snippets**:
  ```python
  # portfolio_guardian.py lines 3 & 28 vs 173-181
  # Docstring claims:
  # "Continuous background auto-execution engine for -4% Stop-Loss and +15% Partial Take-Profit (50%)"
  # Implementation:
  elif pnl_pct >= 15.0:
      trailing_floor = max(kijun_line, buy_price * 1.10)
      if cur_price < trailing_floor:
          sell_qty = total_qty  # EXITS 100%, ZERO 50% PARTIAL TP IMPLEMENTATION
          is_full_exit = True
  ```
- **Architectural Defect & Risk**:
  1. Having 4 identical 38.8KB copies of legacy HTML files in different folders confuses developers and leads to desynchronized edits.
  2. The automated SQLite backup engine (`backup.py`) creates new files on every call without a retention limit, resulting in 123+ files and unbounded disk growth.
  3. Disconnect between docstrings and actual exit logic leads to false assumptions about trade execution.
- **Remediation Strategy**:
  1. Designate `frontend/index.html` as the SSOT frontend; remove or archive duplicate HTML files into an archive folder.
  2. Implement an automated retention policy in `backup.py` (e.g. `MAX_BACKUPS = 10` or prune snapshots older than 7 days).
  3. Move all test database files and VTT subtitles into `data/` or ignore them in `.gitignore`.
  4. Align `PortfolioGuardian` implementation with the documented 50% partial take-profit rule or update documentation.

---

## Synthesis & Architectural Target Architecture

To transition the Al-Sangmoo Quant Trading Platform to an enterprise-grade Clean / Hexagonal Architecture, the following structural reorganization is recommended:

```
al_sangmoo/
├── core/                        # Configuration & SSOT Constants
│   ├── config.py                # Environment & Path Configuration
│   └── constants.py             # SSOT Watchlist, Aliases, Risk Parameters
│
├── domain/                      # PURE DOMAIN (Zero external I/O, Zero DB, Zero WebSockets)
│   ├── quant/                   # Quantitative Indicators & Scoring Math
│   │   ├── ichimoku.py          # Daily & Weekly Ichimoku Engine
│   │   ├── scoring.py           # 3-Tier Canonical Scoring Engine
│   │   ├── macro.py             # MSI 2.0 Macro Stance Engine
│   │   └── conviction_engine.py # GS-Style Alpha Conviction Ranking
│   ├── risk/                    # Pure Risk Models & Rules
│   │   ├── position_sizer.py    # Dynamic ATR & Slot Sizer
│   │   ├── order_guardrail.py   # Pre-Trade Safety Rules
│   │   └── macro_guardrail.py   # Regime-Based Asset Caps
│   └── interfaces/              # Abstract Port Interfaces
│       ├── execution_gateway.py # IExecutionGateway interface
│       └── portfolio_repo.py    # IPortfolioRepository interface
│
├── infrastructure/              # ADAPTERS (External I/O, DB, Network)
│   ├── persistence/             # SQLite SSOT Repository Implementations
│   │   └── sqlite_repo.py
│   ├── brokers/                 # Broker Gateway Implementations
│   │   ├── kis_broker.py        # Implements IExecutionGateway
│   │   └── paper_broker.py      # Implements IExecutionGateway
│   ├── atomic_io.py             # Safe File I/O Engine
│   └── backup.py                # Snapshot Backup with Rotation (Max 10)
│
├── services/                    # APPLICATION / USE CASES / DAEMONS
│   ├── feed_service.py          # Dashboard Feed Generation Pipeline
│   ├── guardian_service.py      # Background Stop-Loss/Take-Profit Daemon
│   ├── autopilot_service.py     # Background Scheduled Scanning & Buying Daemon
│   └── reconciliation_service.py# Broker vs SQLite Sync Service
│
└── interfaces/                  # DRIVERS & PRESENTATION
    ├── api/                     # FastAPI Modular APIRouters
    │   ├── hub.py               # WebSocket Broadcast Hub
    │   └── routers/             # dashboard, portfolio, charts, scanner, broker, guardian, autopilot
    └── cli/                     # CLI Bots & Notification Dispatchers
        └── daily_bot.py
```

---

## Verification & Invalidation Conditions

| Finding | How to Verify Locally | Invalidation Condition |
|---|---|---|
| **ARCH-01** | Run `grep -rn "import db_manager" al_sangmoo/domain/` | If output is empty, domain purity is restored. |
| **ARCH-02** | Inspect `KISBrokerAdapter` class definition in `kis_broker.py` | If `issubclass(KISBrokerAdapter, IExecutionGateway)` is True. |
| **ARCH-03** | Run `python -c "import server; print(server.get_recommended_position_size('NVDA'))"` | If it returns real ATR without catching `NameError: name 'pd' is not defined`. |
| **ARCH-04** | Run `grep -rn "sys.modules\['server'\]" al_sangmoo/` | If output is empty and state is managed via dependency injection. |
| **ARCH-06** | Inspect `generate_dashboard_feed.py:462` with MSI 72.8 | If `msi_val` reads 72.8 and `is_bull_regime` is False. |
| **ARCH-07** | Run `python -c "import al_sangmoo_daily_bot; print(len(al_sangmoo_daily_bot.UNIVERSE))"` | If `UNIVERSE` is eliminated in favor of `WATCHLIST` (60). |
| **ARCH-08** | Search for `0.97` vs `0.96` stop price across `persistence.py` and `portfolio_guardian.py` | If both reference `constants.STOP_LOSS_PCT`. |
| **ARCH-10** | Monitor SQLite file locks during continuous `GET /api/dashboard` calls | If `GET /api/dashboard` executes in < 5ms without issuing SQL `UPDATE` statements. |
| **ARCH-11** | Run `Get-ChildItem d:\코딩\R\backups\*.db | Measure-Object` | If backup count <= 10. |

---
*Report compiled by Explorer 2 (Software Architecture Auditor) • Read-Only Inspection Complete.*
