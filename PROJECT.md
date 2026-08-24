# Project: Al-Sangmoo Quant Trading Platform - Phase 5.3 SSOT Quantitative Consolidation & Clean Architecture

## Architecture
- **Domain Layer (`al_sangmoo.domain.quant`)**:
  - `ichimoku.py`: SSOT calculation for all rolling technical indicators (Tenkan 9, Kijun 26, SpanA shift 26, SpanB 52 shift 26, Chikou shift -26, SMA20/50/60/200, Vol_SMA20, Vol_Ratio, future cloud projection, cloud trampoline bounce detection, OBV & 14-day inflow flow indicators).
  - `scoring.py`: SSOT calculation for 17-Year Quant Scoring matrices (Bull Score 0-100pt, Sniper Score 0-100pt, Bear Score 0-100pt) and deterministic 3-Tier Quant Classification (Tier 1 Macro Leader, Tier 2 Structural Pullback, Tier 3 Cloud Sniper).
  - `macro.py`: SSOT calculation for Macro Stance Index 2.0 (MSI 2.0) with unified hard-gauge weights (US10Y, VIX, WTI, DXY), NLP sentiment, and geopolitical/economic shocks.
  - `multi_timeframe.py`: Multi-timeframe consensus matrix (Weekly + Daily + Hourly).
  - `ticker_resolver.py`: Multi-language symbol & alias resolution.
- **Application & Presentation Layer**:
  - `generate_dashboard_feed.py`: Pure, side-effect free view-model builder. Generates `dashboard_data.json` and chart payload caches from domain quant engines. Zero database writes.
  - `al_sangmoo_daily_bot.py`: Command-line orchestrator and daily reporter. Executes scanning, evaluates portfolio, generates reports, and explicitly persists to SQLite.
  - `youtube_stream_scanner.py`: YouTube stream analyzer. Delegates MSI 2.0 calculations 100% to `al_sangmoo.domain.quant.macro`.
  - `server.py`: FastAPI server exposing read-only and background task scanning endpoints.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | SSOT Indicator Math Engine | Consolidate Tenkan, Kijun, SpanA, SpanB, Chikou, SMA20/50/60/200, Vol_Ratio, OBV, Inflow into `ichimoku.py` | M1 | ORIGINAL_REQUEST §R1 |
| 2 | Pure Chart Series Builder | Extract pure `build_ichimoku_series_payload()` and `project_future_cloud()` into `ichimoku.py` | M1 | Survey Report 1 |
| 3 | Canonical Scoring Matrix | Consolidate 17-Year graduated Bull (35/25/15), Sniper (40/30/15), Bear scoring into `scoring.py` | M2 | ORIGINAL_REQUEST §R2 |
| 4 | Canonical 3-Tier Classification | Implement Tier 1 (Sniper Radar), Tier 2 (Structural Pullback), Tier 3 (Cloud Sniper) classification in `scoring.py` | M2 | ORIGINAL_REQUEST §R2 |
| 5 | Hard Stop & Target Rules | Enforce -4.0% hard stop, +15.0% target, +8.0% partial take-profit across value objects | M2 | Survey Report 2 |
| 6 | MSI 2.0 Parameter Unification | Unify US10Y (25/18/10/4/0), VIX (15/10/5/0), WTI (10/6/3/0), DXY (10/6/2/0) gauge weights in `macro.py` | M3 | ORIGINAL_REQUEST §R3 |
| 7 | Pure Macro Stance Evaluation | Refactor `evaluate_macro_stance()` and legacy `calculate_msi_regime()` in `macro.py` | M3 | ORIGINAL_REQUEST §R3 |
| 8 | Side-Effect Free Feed Pipeline | Strip all SQLite DML writes from `generate_dashboard_feed.build_dashboard_data()` | M4 | ORIGINAL_REQUEST §R4 |
| 9 | Application Deduplication | Refactor `generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, `youtube_stream_scanner.py` to import domain SSOT | M4 | ORIGINAL_REQUEST §R1, R3 |
| 10 | Comprehensive SSOT Test Suite | Create `tools_and_tests/test_phase5_3_ssot_quant.py` with 6-tier verification assertions | M5 | ORIGINAL_REQUEST §Acceptance |
| 11 | Full Platform Regression Pass | Verify 100% Green on all regression suites (`test_phase1`, `test_phase2`, `test_phase4`, `test_phase5_1`, `test_phase5_2`, `test_global60`) | M5 | ORIGINAL_REQUEST §Acceptance |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | SSOT Indicator Math Engine | `al_sangmoo/domain/quant/ichimoku.py` | None | PLANNED |
| M2 | Canonical 3-Tier Quant Scoring Engine | `al_sangmoo/domain/quant/scoring.py` | M1 | PLANNED |
| M3 | MSI 2.0 Parameter & Logic Unification | `al_sangmoo/domain/quant/macro.py` | None | PLANNED |
| M4 | Side-Effect Free Pipeline & Feed Integration | `generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, `youtube_stream_scanner.py` | M1, M2, M3 | PLANNED |
| M5 | E2E Verification & Test Suite Hardening | `tools_and_tests/test_phase5_3_ssot_quant.py` & all regression test suites | M4 | PLANNED |

## Interface Contracts

### `al_sangmoo.domain.quant.ichimoku`
- `calculate_ichimoku_indicators(df: pd.DataFrame) -> pd.DataFrame`
  - Adds: `Tenkan`, `Kijun`, `RawSpanA`, `RawSpanB`, `SpanA`, `SpanB`, `Chikou`, `SMA20`, `SMA50`, `SMA60`, `SMA200`, `Vol_SMA20`, `Vol_Ratio`.
  - Handles `min_periods` safely and prevents division-by-zero on `Volume / Vol_SMA20`.
- `project_future_cloud(df_clean: pd.DataFrame, periods: int = 26, is_weekly: bool = False) -> tuple`
- `detect_cloud_trampoline_bounce(df_clean: pd.DataFrame, max_lookback: int = 14) -> tuple[bool, int, float, float]`
- `compute_institutional_flow_indicators(df: pd.DataFrame) -> dict`
- `build_ichimoku_series_payload(df_clean: pd.DataFrame, is_weekly: bool = False, max_bars: int = 500) -> dict`

### `al_sangmoo.domain.quant.scoring`
- `evaluate_quant_score(close, kijun, tenkan, span_a, span_b, vol_ratio, trampoline_detected=False, is_weekly_bull=True) -> dict`
  - Returns dict with keys: `bull_score`, `sniper_score`, `bear_score`, `composite_score`, `cloud_pts`, `kijun_pts`, `vdu_pts`, `tenkan_pts`, `verdict`.
- `classify_quant_tier(ticker, ind, weekly, flow, trampoline, macro_tailwind_sectors, sector) -> TierClassification`
- `classify_3tier_candidates(chart_data: dict, tailwind_sectors: list[str], stream_mentioned_tickers: set[str] = None) -> tuple[list[dict], list[dict], list[dict]]`
  - Returns `(tier1_picks, tier2_picks, tier3_picks)` deterministically.

### `al_sangmoo.domain.quant.macro`
- `evaluate_macro_stance(gauges=None, defense_count=0, buy_count=0, matched_shocks=None, transcript="", title="") -> dict`
  - Returns: `msi_score` (0.0~100.0), `macro_stance` (`ACTIVE_BUY`, `SELECTIVE_BUY`, `DEFENSE_HOLD`, `CASH_EXIT`), `m_hard`, `m_nlp`, `m_shock`, `us10y_pts`, `vix_pts`, `wti_pts`, `dxy_pts`, `gauge_breakdown`.
- `calculate_msi_regime(gauges, defense_count, buy_count, matched_shocks) -> tuple[str, float, dict]` (Legacy adapter).

## Code Layout
- `al_sangmoo/domain/quant/`: Pure quantitative algorithms, models, and scoring logic.
- `al_sangmoo/infrastructure/`: Persistence, database managers, and file storage.
- `al_sangmoo/api/`: FastAPI server and WebSocket broadcast hubs.
- `tools_and_tests/`: Automated unit, integration, security, concurrency, and E2E test suites.
