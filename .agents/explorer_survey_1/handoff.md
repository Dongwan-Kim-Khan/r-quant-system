# Phase 5.3 Quantitative Consolidation & Clean Architecture Survey Report

**Explorer 1 Survey & Investigation Handoff**
**Timestamp**: 2026-08-22T16:54:10Z
**Working Directory**: `d:\코딩\Playground\al_sangmoo_project\.agents\explorer_survey_1`
**Target Milestone**: Phase 5.3 Quantitative Consolidation & Clean Architecture Refactoring

---

## 1. Observation

Direct code inspections across `al_sangmoo/domain/quant/`, `generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, `youtube_stream_scanner.py`, `server.py`, and `research_and_backtests/` revealed significant indicator duplication, conflicting scoring formulas, missing indicators, and database side-effect leakage.

### 1.1 Duplication of Rolling Indicator Math
- **`al_sangmoo/domain/quant/ichimoku.py` (lines 7-35)**:
  ```python
  def calculate_ichimoku_indicators(df: pd.DataFrame) -> pd.DataFrame:
      df = df.copy()
      high_9 = df['High'].rolling(window=9, min_periods=5).max()
      low_9 = df['Low'].rolling(window=9, min_periods=5).min()
      df['Tenkan'] = (high_9 + low_9) / 2

      high_26 = df['High'].rolling(window=26, min_periods=10).max()
      low_26 = df['Low'].rolling(window=26, min_periods=10).min()
      df['Kijun'] = (high_26 + low_26) / 2

      high_52 = df['High'].rolling(window=52, min_periods=20).max()
      low_52 = df['Low'].rolling(window=52, min_periods=20).min()

      df['RawSpanA'] = (df['Tenkan'] + df['Kijun']) / 2
      df['RawSpanB'] = (high_52 + low_52) / 2

      df['SpanA'] = df['RawSpanA'].shift(26)
      df['SpanB'] = df['RawSpanB'].shift(26)

      df['SMA20'] = df['Close'].rolling(window=20, min_periods=10).mean()
      df['SMA60'] = df['Close'].rolling(window=60, min_periods=20).mean()
      df['Vol_SMA20'] = df['Volume'].rolling(window=20, min_periods=5).mean()
      df['Vol_Ratio'] = df['Volume'] / df['Vol_SMA20']
      return df
  ```
- **`generate_dashboard_feed.py` (lines 171-196)**:
  Repeats identical rolling calculations for daily indicators (`df['Tenkan']`, `df['Kijun']`, `df['RawSpanA']`, `df['RawSpanB']`, `df['SpanA']`, `df['SpanB']`, `df['SMA20']`, `df['SMA60']`, `df['Vol_SMA20']`, `df['Vol_Ratio']`) and weekly resampled indicators (`df_w['Tenkan']`, `df_w['Kijun']`, etc.) without using `min_periods`.
- **`al_sangmoo_daily_bot.py` (lines 82-101)**:
  `calculate_indicators(df)` repeats identical rolling calculations for `Tenkan`, `Kijun`, `SpanA`, `SpanB`, `SMA20`, `SMA60`, `Vol_SMA20`, `Vol_Ratio`, omitting `RawSpanA` and `RawSpanB`.
- **`research_and_backtests/` (5 files)**:
  `al_sangmoo_2026_3month_swing_case_study.py` (lines 33-50), `al_sangmoo_pre_trigger_scanner.py` (lines 35-52), `daily_nasdaq_screener.py` (lines 38-55), `nasdaq_al_sangmoo_2026_ytd_backtester.py`, and `nasdaq_al_sangmoo_backtester.py` all define duplicate inline rolling functions.

### 1.2 Missing Canonical Quantitative Indicators
- **Chikou Span (Lagging Span)**: Defined in Ichimoku theory as `df['Close'].shift(-26)` (or today's Close vs Close 26 bars ago). Completely absent across all modules in `al_sangmoo/domain/quant/`.
- **50-Day and 200-Day SMA**: Required for institutional trend classification (`SMA50`, `SMA200`). Entirely absent from `ichimoku.py`, `generate_dashboard_feed.py`, and `al_sangmoo_daily_bot.py` (which only compute `SMA20` and `SMA60`).

### 1.3 Conflicting Scoring Formulas and Thresholds
- **Binary vs Tiered Scoring**:
  - `ichimoku.py` (lines 76-80) and `al_sangmoo_daily_bot.py` (lines 157-162):
    - Cloud top: `close >= cloud_top` (+35 pt)
    - Kijun gap: `-0.5 <= kijun_gap <= 4.0` (+35 pt)
    - Volume ratio: `vol_ratio <= 0.75` (+20 pt)
    - Tenkan alignment: `tenkan >= kijun` (+10 pt)
    - Thresholds: `bull_score >= 70` (in `ichimoku.py`), `bull_score >= 65` (in `al_sangmoo_daily_bot.py`).
  - `generate_dashboard_feed.py` (lines 239-265):
    - Cloud top: `>= cloud_top` (+35), `>= cloud_top * 0.97` (+25), `>= cloud_bottom` (+15).
    - Kijun gap: `-0.5 ~ 3.5` (+35), `-0.8 ~ 4.8` (+25), `-1.5 ~ 7.0` (+15).
    - Volume ratio: `<= 0.60` (+20), `<= 0.85` (+15), `<= 1.10` (+10).
    - Tenkan alignment: `tenkan >= kijun` (+10), `close >= tenkan` (+5).
    - Threshold: Strategy 1 requires `is_weekly_bull and (bull_score >= 80) and (-0.8 <= kijun_gap <= 4.8)`.
- **Strategy 2 (Cloud Trampoline Bounce Sniper)**:
  - Implemented inline ONLY in `generate_dashboard_feed.py` (lines 271-300):
    - 14-bar lookback for `(-0.035 <= (h_low - h_cloud_top)/h_cloud_top <= 0.060) and ((h_close - h_cloud_top)/h_cloud_top >= -0.015)`.
    - Sniper score: Trampoline (+40), Cloud (+30/+20), Kijun (+15/+10), Tenkan (+15/+10).
    - Threshold: `is_weekly_bull and trampoline_detected and (sniper_score >= 80) and (close >= cloud_top * 0.97)`.
  - Completely absent from `al_sangmoo/domain/quant/ichimoku.py` and `al_sangmoo_daily_bot.py`.

### 1.4 Macro Stance Index 2.0 (MSI 2.0) Discrepancies
- **`al_sangmoo/domain/quant/macro.py` (lines 15-54)**:
  - US 10Y Yield: `>=4.50: 25.0`, `>=4.30: 15.0`, `>=4.10: 8.0`, else `0.0`.
  - VIX: `>=25.0: 15.0`, `>=20.0: 8.0`, else `0.0`.
  - WTI Oil: `>=85.0: 10.0`, `>=80.0: 5.0`, else `0.0`.
  - DXY: `>=106.0: 10.0`, `>=104.0: 5.0`, else `0.0`.
  - Shock weight: `min(15.0, len(matched_shocks) * 5.0)`.
- **`youtube_stream_scanner.py` (lines 274-326)**:
  - US 10Y Yield: `>=4.50: 25.0`, `>=4.30: 18.0`, `>=4.10: 10.0`, `>=3.90: 4.0`, else `0.0`.
  - VIX: `>=25.0: 15.0`, `>=20.0: 10.0`, `>=16.0: 5.0`, else `0.0`.
  - WTI Oil: `>=85.0: 10.0`, `>=80.0: 6.0`, `>=75.0: 3.0`, else `0.0`.
  - DXY: `>=105.0: 10.0`, `>=103.0: 6.0`, `>=100.0: 2.0`, else `0.0`.
  - Shock weight: War (+6.0), Trade (+4.0), Interest Rate (+5.0).

### 1.5 Database Mutation Side Effects in Read-Only Feed Generation
- In `generate_dashboard_feed.py` (lines 688-693):
  ```python
  db_manager.save_recommendation_matrix_record(today_str, b_picks, n_picks, s_picks)
  db_manager.archive_daily_recommendations(today_str, dual_consensus_picks, strat1_exclusive, strat2_exclusive)
  ```
  `build_dashboard_data()` mutates the database on every read invocation.
- In `server.py` (lines 378-382), `al_sangmoo_daily_bot.scan_and_select_2x2x2()` saves one recommendation matrix, and then `build_dashboard_data()` immediately overwrites it with a different matrix.

---

## 2. Logic Chain

1. **Indicator Duplication & Maintenance Burden**:
   - Because `calculate_ichimoku_indicators` in `ichimoku.py` was not utilized by `generate_dashboard_feed.py` and `al_sangmoo_daily_bot.py`, each file maintained a separate copy. When modifications were made (such as adding weekly resampling or tiered scoring), they diverged.
2. **Deterministic Output Failure**:
   - Because `al_sangmoo_daily_bot.py` applies a binary score with a 65pt threshold on daily data only, while `generate_dashboard_feed.py` applies weekly bull filtering + tiered scoring (80pt threshold) + trampoline sniper detection, the two scripts produce completely divergent recommendation sets for the exact same stock universe on the same day.
3. **Macro Stance Desynchronization**:
   - `youtube_stream_scanner.py` computes MSI 2.0 with finer-grained gauge intervals (e.g. 18pt for US 10Y at 4.30%, 10pt for VIX at 20.0), while `macro.py` computes lower point totals (15pt and 8pt). As a result, a YouTube scan cache might classify the macro stance as `DEFENSE_HOLD` (52 pt), whereas a direct call to `macro.py` with identical gauge data outputs `SELECTIVE_BUY` (45 pt).
4. **Clean Architecture Violation & Side Effects**:
   - Calling `build_dashboard_data()` should be a pure, idempotent projection from market data + persistence into a view model payload. Executing database write side-effects inside `build_dashboard_data()` causes double-writes and race conditions when invoked from API background workers.

---

## 3. Caveats

- **Existing Tests**: Regression tests (`test_global60_dual_strategy.py`, `test_phase5_1_security.py`, `test_phase5_2_concurrency.py`) verify the presence of keys in `dashboard_data.json` (such as `primary_accumulation`, `sniper_radar`, `signal_tracker`) and ensure zero-emoji compliance. The refactored SSOT output must maintain 100% schema backward compatibility.
- **Chart Output Compatibility**: The lightweight dashboard charts consume `candles`, `kijun_line`, `tenkan_line`, `span_a_line`, `span_b_line`, `sma20`, `sma60`, and `volume`. Adding `sma50` and `sma200` to the series payload must be done additively without breaking existing keys.

---

## 4. Conclusion & Proposed SSOT Architecture

All quantitative mathematics, scoring matrices, macro evaluation, and tier classification must be centralized into `al_sangmoo.domain.quant`:

### 4.1 Canonical Module Layout in `al_sangmoo/domain/quant/`

```
al_sangmoo/domain/quant/
├── __init__.py
├── ichimoku.py          # Pure indicator calculation, rolling series, future cloud, bounce detection, OBV/Flow
├── scoring.py           # Pure 17-year quant scoring matrix (Bull, Sniper, Bear) & 3-Tier classification
├── macro.py             # Pure MSI 2.0 macro regime evaluation & tailwind sector resolution
├── multi_timeframe.py   # MTF consensus matrix (Weekly + Daily + Hourly)
└── ticker_resolver.py   # Multi-language ticker lookup & validation
```

### 4.2 Proposed SSOT Domain Interfaces

#### A. `al_sangmoo/domain/quant/ichimoku.py`
```python
def calculate_ichimoku_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    SSOT for all rolling technical indicators:
    - 9-day Tenkan-sen, 26-day Kijun-sen, 52-day Senkou Span B
    - RawSpanA ((Tenkan+Kijun)/2), RawSpanB, SpanA (shift +26), SpanB (shift +26)
    - Chikou Span (shift -26)
    - SMA20, SMA50, SMA60, SMA200
    - Vol_SMA20, Vol_Ratio (safe division against 0)
    """

def project_future_cloud(df_clean: pd.DataFrame, periods: int = 26, is_weekly: bool = False) -> tuple:
    """Projects forward 26 trading periods for future cloud boundary visualization."""

def detect_cloud_trampoline_bounce(df_clean: pd.DataFrame, max_lookback: int = 14) -> tuple[bool, int]:
    """Detects historical cloud top bounce within last max_lookback bars."""

def compute_institutional_flow_indicators(df: pd.DataFrame) -> dict:
    """Computes OBV, 14-day flow ratio (up/down vol), stealth accumulation divergence, and flow score."""

def build_ichimoku_series_payload(df_clean: pd.DataFrame, is_weekly: bool = False, max_bars: int = 500) -> dict:
    """Converts calculated DataFrame into chart-ready JSON series dictionary."""
```

#### B. `al_sangmoo/domain/quant/scoring.py`
```python
def evaluate_quant_score(
    close: float,
    kijun: float,
    tenkan: float,
    span_a: float,
    span_b: float,
    vol_ratio: float,
    trampoline_detected: bool = False,
    is_weekly_bull: bool = True
) -> dict:
    """
    Canonical 17-Year Scoring Matrix:
    - Bull Score (0 ~ 100 pt): Cloud (+35/+25/+15), Kijun gap (+35/+25/+15), VDU (+20/+15/+10), Tenkan (+10/+5)
    - Sniper Score (0 ~ 100 pt): Trampoline (+40), Cloud (+30/+20), Kijun (+15/+10), Tenkan (+15/+10)
    - Bear Score (0 ~ 100 pt): Kijun breakdown (+40), Cloud collapse (+35), Kijun gap < -2.0% (+15)
    """

def classify_3tier_candidates(
    chart_data: dict[str, dict],
    tailwind_sectors: list[str],
    stream_mentioned_tickers: set[str] = None
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    SSOT 3-Tier Classification Engine:
    - Tier 1: Dual Consensus (Macro Tailwind / Stealth Accum / High Flow + Bull Score >= 75 / 80)
    - Tier 2: Structural Pullback (Weekly Bull + Kijun Support -0.8%~+4.8% + VDU < 0.60)
    - Tier 3: Cloud Sniper (Weekly Bull + Trampoline Bounce + Sniper Score >= 80)
    Returns: (tier1_picks, tier2_picks, tier3_picks)
    """
```

#### C. `al_sangmoo/domain/quant/macro.py`
```python
def evaluate_macro_stance(
    gauges: dict,
    defense_count: int = 0,
    buy_count: int = 0,
    matched_shocks: list = None
) -> dict:
    """
    Unified MSI 2.0 SSOT Calculation:
    - US 10Y Yield: >=4.50: 25.0, >=4.30: 18.0, >=4.10: 10.0, >=3.90: 4.0, else 0.0 (Max 25 pt)
    - VIX Index: >=25.0: 15.0, >=20.0: 10.0, >=16.0: 5.0, else 0.0 (Max 15 pt)
    - WTI Oil: >=85.0: 10.0, >=80.0: 6.0, >=75.0: 3.0, else 0.0 (Max 10 pt)
    - Dollar Index: >=105.0: 10.0, >=103.0: 6.0, >=100.0: 2.0, else 0.0 (Max 10 pt)
    - NLP Sentiment (def_count / total): Max 25 pt
    - External Shocks (war: 6.0, trade: 4.0, rate: 5.0): Max 15 pt
    Total MSI: 0.0 ~ 100.0 pt -> CASH_EXIT (>=75), DEFENSE_HOLD (>=50), SELECTIVE_BUY (>=30), ACTIVE_BUY (<30).
    """
```

### 4.3 Consumption Refactoring in Applications

- **`generate_dashboard_feed.py`**:
  - Remove all inline rolling indicator calculations in `compute_all_indicators()` and `build_ichimoku_series()`.
  - Import `calculate_ichimoku_indicators`, `build_ichimoku_series_payload`, `detect_cloud_trampoline_bounce`, `compute_institutional_flow_indicators` from `al_sangmoo.domain.quant.ichimoku`.
  - Import `evaluate_quant_score`, `classify_3tier_candidates` from `al_sangmoo.domain.quant.scoring`.
  - Import `evaluate_macro_stance` from `al_sangmoo.domain.quant.macro`.
  - Remove database mutation calls (`save_recommendation_matrix_record`, `archive_daily_recommendations`) from `build_dashboard_data()` to ensure pure read-only payload transformation.

- **`al_sangmoo_daily_bot.py`**:
  - Remove duplicate `calculate_indicators()`.
  - Delegate `scan_and_select_2x2x2()` to use `classify_3tier_candidates()` and the SSOT domain pipeline, producing identical deterministic results as the dashboard feed.
  - Explicitly perform database persistence only when executing scanning commands or bot runs.

- **`youtube_stream_scanner.py`**:
  - Replace inline `calculate_msi_regime` / hard gauge evaluation with `al_sangmoo.domain.quant.macro.evaluate_macro_stance()`.

---

## 5. Verification Method

To independently verify the implementation during Phase 5.3:

1. **Automated SSOT Verification Test**:
   - Run `python tools_and_tests/test_phase5_3_ssot_quant.py` to verify:
     - All indicator columns (`Tenkan`, `Kijun`, `RawSpanA`, `RawSpanB`, `SpanA`, `SpanB`, `Chikou`, `SMA20`, `SMA50`, `SMA60`, `SMA200`, `Vol_SMA20`, `Vol_Ratio`) are calculated correctly and handle NaN/zero-volume edge cases.
     - `evaluate_quant_score` produces identical deterministic scores.
     - `classify_3tier_candidates` yields identical Tier 1/2/3 output for both `generate_dashboard_feed.py` and `al_sangmoo_daily_bot.py`.
     - `evaluate_macro_stance` yields 100% identical MSI scores across `macro.py` and `youtube_stream_scanner.py`.
     - `build_dashboard_data()` executes without writing to SQLite.
2. **Static AST Analysis**:
   - Inspect `generate_dashboard_feed.py` and `al_sangmoo_daily_bot.py` with `ast` parsing to assert zero direct calls to `.rolling()` or duplicate scoring dict definitions.
3. **Full Regression Test Suite**:
   - `python tools_and_tests/test_global60_dual_strategy.py`
   - `python tools_and_tests/test_phase1_hardening.py`
   - `python tools_and_tests/test_phase2_modular.py`
   - `python tools_and_tests/test_phase4_execution.py`
   - `python tools_and_tests/test_phase5_1_security.py`
   - `python tools_and_tests/test_phase5_2_concurrency.py`
