# Quantitative Scoring & 3-Tier Classification Architectural Survey Report

## 1. Observation

### 1.1 Codebase Indicator Calculation Duplications

| Component / File | Line Numbers | Method / Function | Implementation Details |
|---|---|---|---|
| `al_sangmoo/domain/quant/ichimoku.py` | 7–35 | `calculate_ichimoku_indicators(df)` | Domain SSOT: High/Low 9-day rolling max/min for Tenkan, 26-day for Kijun, RawSpanA, RawSpanB, SpanA (shift 26), SpanB (shift 26), SMA20, SMA60, Vol_SMA20, Vol_Ratio (`min_periods` specified). |
| `generate_dashboard_feed.py` | 158–196 | `compute_all_indicators(ticker)` | Inline duplicate calculation for both daily and weekly resampled DataFrames (`min_periods` omitted). |
| `al_sangmoo_daily_bot.py` | 82–101 | `calculate_indicators(df)` | Inline duplicate calculation for daily DataFrame without weekly support. |
| `research_and_backtests/al_sangmoo_pre_trigger_scanner.py` | 35–53 | `evaluate_pre_trigger(ticker)` | Inline duplicate calculation of Tenkan, Kijun, SpanA, SpanB, Vol_Ratio. |
| `research_and_backtests/daily_nasdaq_screener.py` | 37–55 | `scan_tickers()` | Inline duplicate calculation of Tenkan, Kijun, SpanA, SpanB, Vol_Ratio. |

Verbatim quotes:
- `al_sangmoo/domain/quant/ichimoku.py:12-29`:
```python
high_9 = df['High'].rolling(window=9, min_periods=5).max()
low_9 = df['Low'].rolling(window=9, min_periods=5).min()
df['Tenkan'] = (high_9 + low_9) / 2
high_26 = df['High'].rolling(window=26, min_periods=10).max()
low_26 = df['Low'].rolling(window=26, min_periods=10).min()
df['Kijun'] = (high_26 + low_26) / 2
df['RawSpanA'] = (df['Tenkan'] + df['Kijun']) / 2
df['RawSpanB'] = (high_52 + low_52) / 2
df['SpanA'] = df['RawSpanA'].shift(26)
df['SpanB'] = df['RawSpanB'].shift(26)
```

- `generate_dashboard_feed.py:171-180`:
```python
df['Tenkan'] = (df['High'].rolling(9).max() + df['Low'].rolling(9).min()) / 2
df['Kijun'] = (df['High'].rolling(26).max() + df['Low'].rolling(26).min()) / 2
df['RawSpanA'] = (df['Tenkan'] + df['Kijun']) / 2
df['RawSpanB'] = (df['High'].rolling(52).max() + df['Low'].rolling(52).min()) / 2
df['SpanA'] = df['RawSpanA'].shift(26)
df['SpanB'] = df['RawSpanB'].shift(26)
df['SMA20'] = df['Close'].rolling(20).mean()
df['SMA60'] = df['Close'].rolling(60).mean()
df['Vol_SMA20'] = df['Volume'].rolling(20).mean()
df['Vol_Ratio'] = df['Volume'] / df['Vol_SMA20']
```

- `al_sangmoo_daily_bot.py:84-100`:
```python
high_9 = df['High'].rolling(window=9).max()
low_9 = df['Low'].rolling(window=9).min()
df['Tenkan'] = (high_9 + low_9) / 2
high_26 = df['High'].rolling(window=26).max()
low_26 = df['Low'].rolling(window=26).min()
df['Kijun'] = (high_26 + low_26) / 2
df['SpanA'] = ((df['Tenkan'] + df['Kijun']) / 2).shift(26)
df['SpanB'] = ((high_52 + low_52) / 2).shift(26)
```

---

### 1.2 Divergent Quant Scoring Formulations & Cutoffs

| Component | Line Numbers | Scoring Model | Factor Breakdown & Weights | Verdict / Qualification Cutoffs |
|---|---|---|---|---|
| `al_sangmoo/domain/quant/ichimoku.py` | 76–102 | Binary Classic Matrix | • Cloud Top (`close >= cloud_top`): +35 pt<br>• Kijun Gap (`-0.5 <= kijun_gap <= 4.0`): +35 pt<br>• Vol Ratio (`vol_ratio <= 0.75`): +20 pt<br>• Tenkan Momentum (`tenkan >= kijun`): +10 pt | • `bull_score >= 70`: `BULL`<br>• `bear_score >= 50`: `BEAR`<br>• Else: `NEUTRAL` |
| `al_sangmoo_daily_bot.py` | 157–216 | Binary Classic Matrix | Identical to `ichimoku.py` (35 / 35 / 20 / 10) | • `bull_score >= 65`: Included in `bull_pool`<br>• `bear_score >= 50`: Included in `bear_pool` |
| `generate_dashboard_feed.py` | 238–265 | Graduated Continuous Matrix | • Cloud: `>= cloud_top` (+35), `>= cloud_top*0.97` (+25), `>= cloud_bottom` (+15)<br>• Kijun Gap: `[-0.5, 3.5]` (+35), `[-0.8, 4.8]` (+25), `[-1.5, 7.0]` (+15)<br>• Vol Ratio: `<= 0.60` (+20), `<= 0.85` (+15), `<= 1.10` (+10)<br>• Tenkan: `>= kijun` (+10), `close >= tenkan` (+5) | • `bull_score >= 80` & `is_weekly_bull`: `is_strat1_active`<br>• `item_score = 100` if dual active |
| `generate_dashboard_feed.py` | 271–300 | Strategy 2 (Cloud Sniper) Matrix | • Trampoline Detected (14-bar lookback): +40 pt<br>• Cloud Top: `>= cloud_top` (+30), `>= cloud_top*0.98` (+20)<br>• Kijun Gap: `>= 0` (+15), `>= -1.0` (+10)<br>• Tenkan: `>= kijun` (+15), `close >= tenkan` (+10) | • `sniper_score >= 80` & `is_weekly_bull` & `trampoline_detected` & `close >= cloud_top*0.97`: `is_sniper_active` |
| `al_sangmoo_dashboard.html` | 1013 | Client Javascript Decoder | Client reads `info.bull_score` and `info.is_sniper` | `if (info.is_sniper && info.bull_score >= 90) tierType = 'TIER_1'` |

---

### 1.3 3-Tier Classification & Selection Rules Discrepancies

| Tier Name / Target | Phase 5.3 SSOT Requirement (`ORIGINAL_REQUEST.md:116-122`) | Current Implementation in `generate_dashboard_feed.py` (Lines 514–568) | Current Implementation in `al_sangmoo_daily_bot.py` (Lines 200–240, 809–811) |
|---|---|---|---|
| **Tier 1: Sniper Radar / Macro Tailwind + Smart Money** | 1. Gate-0 Macro Tailwind (`sector in tailwind_sectors`)<br>2. OBV Stealth Accumulation (`is_stealth_accum` or `obv_status == 'STEALTH_ACCUM'`)<br>3. 14-Day Volume Flow Ratio >= 120% (`flow_ratio >= 1.20`)<br>4. Bull Score >= 75<br>5. Weekly Bull Alignment | `is_weekly_bull and (is_strat1 or is_strat2) and is_safe_entry and (is_macro_tailwind or is_stealth_accum or flow_ratio >= 1.3 or item_score == 100)`<br>*(Uses loose `OR` conditions, flow ratio >= 1.3, safe entry [-0.8%~+3.5%])* | Not implemented in `scan_and_select_2x2x2`. Reads `dual_consensus` from feed generator, then treats `dual_consensus[:2]` as `bull_picks`. |
| **Tier 2: Structural Pullback** | 1. 26-Day Kijun-sen support (Price within +/-3% of Kijun: `-3.0 <= kijun_gap <= 3.0` or sweet spot `-0.5% ~ +3.0%`)<br>2. Tenkan >= Kijun alignment (`tenkan >= kijun`)<br>3. 20-Day Volume Dry-Up (`vol_ratio < 0.60` or `<= 0.60`)<br>4. Weekly Bull Trend (`is_weekly_bull`) | `is_weekly_bull and is_strat1 and is_safe_entry`<br>*(Where `is_strat1` required `b_score >= 80` which allowed `vol_ratio <= 1.10`)* | Uses legacy `bull_pool` with `bull_score >= 65`, sorts by `(bull_score, -abs(kijun_gap - 1.0), -vol_ratio)`. |
| **Tier 3: Cloud Sniper** | 1. Forward +26D Ichimoku Cloud Trampoline bounce (Price above Span A/B, 14-bar lookback touch -3.5%~+6.0% and close >= -1.5%)<br>2. Stage 2 Breakout Momentum (`close >= cloud_top` & Tenkan >= Kijun)<br>3. -4% Hard Stop Rule | `is_weekly_bull and is_strat2`<br>*(Where `is_strat2` required `sniper_score >= 80`)* | Completely absent in `scan_and_select_2x2x2`. Piggybacks on `strat2_exclusive` from feed generator, but maps it to `bear_picks` for database storage (Line 811: `bear_picks = strat2_exclusive[:2]`). |

---

### 1.4 Macro Stance Index 2.0 (MSI 2.0) Point Weight Inconsistencies

| Metric / Threshold | `al_sangmoo/domain/quant/macro.py` (Lines 15–66) | `youtube_stream_scanner.py` (Lines 268–325) | Conflict Status |
|---|---|---|---|
| **US 10Y >= 4.50%** | 25.0 pt | 25.0 pt | Identical |
| **US 10Y >= 4.30%** | **15.0 pt** | **18.0 pt** | **DIVERGENT (3.0 pt gap)** |
| **US 10Y >= 4.10%** | **8.0 pt** | **10.0 pt** | **DIVERGENT (2.0 pt gap)** |
| **US 10Y >= 3.90%** | 0.0 pt | 4.0 pt | **DIVERGENT (4.0 pt gap)** |
| **VIX >= 25.0** | 15.0 pt | 15.0 pt | Identical |
| **VIX >= 20.0** | **8.0 pt** | **10.0 pt** | **DIVERGENT (2.0 pt gap)** |
| **VIX >= 16.0** | 0.0 pt | 5.0 pt | **DIVERGENT (5.0 pt gap)** |
| **WTI Oil >= 85.0** | 10.0 pt | 10.0 pt | Identical |
| **WTI Oil >= 80.0** | **5.0 pt** | **6.0 pt** | **DIVERGENT (1.0 pt gap)** |
| **WTI Oil >= 75.0** | 0.0 pt | 3.0 pt | **DIVERGENT (3.0 pt gap)** |
| **DXY >= 106.0 / 105.0** | 10.0 pt (>= 106.0) | 10.0 pt (>= 105.0) | **DIVERGENT Threshold** |
| **DXY >= 104.0 / 103.0** | 5.0 pt (>= 104.0) | 6.0 pt (>= 103.0) | **DIVERGENT Points & Threshold** |
| **DXY >= 100.0** | 0.0 pt | 2.0 pt | **DIVERGENT** |
| **External Shock ($M_{shock}$)** | `len(matched_shocks) * 5.0` (Max 15 pt) | War 6.0 pt, Fed 5.0 pt, Trade 4.0 pt (Max 15 pt) | **DIVERGENT Calculation** |

---

### 1.5 Stop-Loss & Target Multiplier Discrepancies

- `al_sangmoo/infrastructure/persistence.py:178`: `stop_loss_price = round(buy_price * 0.97, 2)` (-3.0% stop).
- `al_sangmoo/infrastructure/persistence.py:436`: `stop_p = float(item.get("stop_price", round(price * 0.96, 2)))` (-4.0% stop).
- `generate_dashboard_feed.py:548`: `stop_price = round(c["latest_close"] * 0.96, 2)` (-4.0% stop).
- `al_sangmoo_daily_bot.py:257`: `is_stop_loss = cur_price <= stop_p or pnl_pct <= -3.0` (-3.0% stop).
- `al_sangmoo_daily_bot.py:748`: Markdown report prints `손절가(-4%): ${p['stop_loss_price']}` while code evaluated against -3.0%.
- `al_sangmoo/backtest/engine.py:20`: `stop_loss_pct = -0.03` (-3.0% stop).
- `al_sangmoo_dashboard.html:923`: `bp * 0.97` vs `al_sangmoo_dashboard.html:1592`: `item.price * 0.96`.

---

### 1.6 Architectural Bleeding & Pipeline Side-Effects

1. **Dual DB Writes**:
   - `generate_dashboard_feed.py:689-690`: `build_dashboard_data()` calls `db_manager.save_recommendation_matrix_record()` and `db_manager.archive_daily_recommendations()`.
   - `al_sangmoo_daily_bot.py:816-817`: `main()` calls `build_dashboard_data()`, and subsequently calls `db_manager.save_macro_history_record()` and `db_manager.save_recommendation_matrix_record()` again.
2. **Conceptual Collision in Bot DB Archiving**:
   - `al_sangmoo_daily_bot.py:811`: `bear_picks = strat2_exclusive[:2]`. The bot feeds `strat2_exclusive` (which is Tier 3 Cloud Sniper bullish momentum) into the database column `bear_1` and `bear_2`!
3. **Monolithic Feed Generator**:
   - `generate_dashboard_feed.py` contains 766 lines performing data downloading, indicator computation, scoring, classification, database write side-effects, HTML card generation, and JSON caching all in one script.

---

## 2. Logic Chain

1. **Root Cause of Math Divergence**: Because `al_sangmoo/domain/quant/scoring.py` was never established as the canonical scoring service, each script (`al_sangmoo_daily_bot.py`, `generate_dashboard_feed.py`, `server.py`, `youtube_stream_scanner.py`) wrote its own scoring logic or copied an older revision of the algorithm.
2. **Threshold Fragmentation**:
   - `ichimoku.py` implemented the original binary scoring (35/35/20/10) with a 70pt cutoff.
   - `al_sangmoo_daily_bot.py` adopted the binary scoring but dropped the cutoff to 65pt to prevent empty candidate lists in small 23-ticker runs.
   - `generate_dashboard_feed.py` expanded to a graduated scoring system (allowing partial points like 25, 15, 10, 5) and raised the cutoff to 80pt for its 60-ticker universe.
   - As a consequence, a stock with `kijun_gap = 4.2%` scores 25pt in `generate_dashboard_feed.py` but 0pt in `ichimoku.py` and `al_sangmoo_daily_bot.py`. A stock with `vol_ratio = 0.80` scores 15pt in `generate_dashboard_feed.py` but 0pt in `ichimoku.py`.
3. **Macro Weight Drift**:
   - `macro.py` was created during Phase 2 with a simplified hard-gauge matrix.
   - `youtube_stream_scanner.py` was updated separately with higher sensitivity intermediate steps (e.g. 3.90% yield step, 16.0 VIX step).
   - This causes MSI 2.0 calculated by `macro.py` to disagree with `youtube_stream_scanner.py` by 3 to 10 points for identical macroeconomic inputs.
4. **Classification Collision**:
   - The platform evolved from a 2+2+2 (Bull, Neutral, Bear) prototype to a 3-Tier Institutional Portfolio (Tier 1 Macro Leader, Tier 2 Structural Pullback, Tier 3 Cloud Sniper).
   - `al_sangmoo_daily_bot.py` and the SQLite `recommendation_matrix` table still have column schemas `bull_1, bull_2, neutral_1, neutral_2, bear_1, bear_2`.
   - To fit the 3-Tier model into the old 2+2+2 schema, `al_sangmoo_daily_bot.py` mapped `strat2_exclusive` (Tier 3 Cloud Sniper) into `bear_1/bear_2`, causing high-momentum buy candidates to be saved under "Bear" classifications in SQLite.
5. **Architectural Purity**:
   - View-model data generation (`build_dashboard_data`) must be completely pure and free of database write side effects.
   - All indicator calculations, pattern detections (Cloud Trampoline), scoring algorithms, and 3-Tier classifications must be centralized in `al_sangmoo.domain.quant.scoring` and `al_sangmoo.domain.quant.ichimoku`.

---

## 3. Caveats

1. **Backwards Compatibility**: Existing test suites (`test_phase2_modular.py`, `test_modular_2tier_architecture.py`, `test_global60_dual_strategy.py`) assert specific keys in `dashboard_data.json` (such as `primary_accumulation`, `sniper_radar`, `signal_tracker`, `chart_intelligence`). The canonical consolidation must preserve these exact payload structures.
2. **Database Schema**: The `recommendation_matrix` table schema contains legacy columns `bull_1, bull_2, neutral_1, neutral_2, bear_1, bear_2`. The `trades` and `daily_recommendation_history` tables already support `DUAL_5_STAR`, `STRAT1_PULLBACK`, and `STRAT2_SNIPER`. Database archiving should be unified through `al_sangmoo.infrastructure.persistence.archive_daily_recommendations`.
3. **External Network Dependencies**: Yahoo Finance (`yf.download`) is used for market data. All domain scoring and classification functions in `al_sangmoo/domain/quant/` must remain 100% pure (accepting in-memory DataFrames / dicts / Pydantic models) so they can be unit-tested without network I/O.

---

## 4. Conclusion & Proposed Canonical Architecture

### 4.1 Canonical Module Structure in `al_sangmoo/domain/quant/`

```
al_sangmoo/domain/quant/
├── __init__.py               # Exports all public quant classes and functions
├── ichimoku.py               # Pure indicator calculations & Ichimoku series builders
├── scoring.py                # CANONICAL SSOT: Pure Quant Scoring & 3-Tier Classifier
├── macro.py                  # CANONICAL SSOT: MSI 2.0 Regime & Hard Gauges
├── multi_timeframe.py        # MTF Consensus Engine (Weekly + Daily + Hourly)
└── ticker_resolver.py        # Multi-language symbol & alias resolver
```

---

### 4.2 Proposed `al_sangmoo/domain/quant/scoring.py` Specification

#### Data Structures (Value Objects & Models)

```python
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Literal
import pandas as pd
import numpy as np

@dataclass(frozen=True)
class QuantIndicators:
    """Immutable snapshot of computed indicators for a single bar."""
    close: float
    open: float
    high: float
    low: float
    volume: float
    tenkan: float
    kijun: float
    span_a: float
    span_b: float
    raw_span_a: float
    raw_span_b: float
    sma20: float
    sma60: float
    vol_sma20: float
    vol_ratio: float
    kijun_gap_pct: float
    cloud_top: float
    cloud_bottom: float

@dataclass(frozen=True)
class WeeklyTrendContext:
    """Weekly timeframe trend alignment context."""
    is_weekly_bull: bool
    weekly_close: float
    weekly_cloud_top: float
    weekly_kijun: float
    weekly_tenkan: float
    trend_regime: Literal["BULLISH_TREND", "CONSOLIDATING", "BEARISH_TREND"]

@dataclass(frozen=True)
class InstitutionalFlowContext:
    """OBV and 14-day volume flow accumulation signatures."""
    obv_status: Literal["STEALTH_ACCUM", "BULL_FLOW", "NEUTRAL"]
    obv_label: str
    flow_ratio: float
    flow_label: str
    flow_score: int
    is_stealth_accum: bool

@dataclass(frozen=True)
class TrampolineBounceContext:
    """14-day Ichimoku cloud trampoline bounce detection."""
    detected: bool
    days_ago: int
    touch_gap_pct: float
    close_gap_pct: float

@dataclass(frozen=True)
class QuantScoreBreakdown:
    """Detailed point breakdown for auditability."""
    cloud_pts: int
    kijun_pts: int
    vdu_pts: int
    tenkan_pts: int
    total_bull_score: int
    bear_score: int
    sniper_score: int
    composite_score: float

@dataclass(frozen=True)
class TierClassification:
    """3-Tier Quant Classification output."""
    tier: Literal["TIER_1", "TIER_2", "TIER_3", "UNCLASSIFIED"]
    tier_name_kr: str
    strategy_code: str
    score: int
    composite_score: float
    entry_price: float
    target_price: float         # +15.0%
    stop_price: float           # -4.0% Hard Stop
    partial_tp_price: float     # +8.0% (50% Take Profit)
    is_tier1_qualified: bool
    is_tier2_qualified: bool
    is_tier3_qualified: bool
    rationale: str
```

---

### 4.3 Proposed Canonical Mathematical Rules

#### 1. Canonical Graduated Bull Score (0 ~ 100 pt)
```python
def calculate_canonical_bull_score(ind: QuantIndicators) -> tuple[int, dict]:
    """
    Evaluates pure 17-Year Quant Formula with graduated sweet-spots:
    - Cloud Clearance (Max 35 pt):
        close >= cloud_top: +35 pt
        close >= cloud_top * 0.97: +25 pt
        close >= cloud_bottom: +15 pt
    - Kijun-sen Sweet-Spot Support (Max 35 pt):
        -0.5% <= kijun_gap <= +3.5%: +35 pt
        -0.8% <= kijun_gap <= +4.8%: +25 pt
        -1.5% <= kijun_gap <= +7.0%: +15 pt
    - 20-Day Volume Dry-Up (Max 20 pt):
        vol_ratio <= 0.60: +20 pt
        vol_ratio <= 0.85: +15 pt
        vol_ratio <= 1.10: +10 pt
    - 9-Day Tenkan Momentum (Max 10 pt):
        tenkan >= kijun: +10 pt
        close >= tenkan: +5 pt
    """
```

#### 2. Canonical Cloud Trampoline Bounce Detector (Strategy 2 / Tier 3)
```python
def detect_cloud_trampoline_bounce(df_clean: pd.DataFrame, lookback_bars: int = 14) -> TrampolineBounceContext:
    """
    Scans prior 14 bars for a valid Cloud Top trampoline bounce:
    - Candle Low touched within -3.5% to +6.0% of Span A/B Cloud Top
    - Candle Close held >= -1.5% of Cloud Top
    """
```

#### 3. Canonical Strategy 2 Sniper Score (0 ~ 100 pt)
```python
def calculate_canonical_sniper_score(ind: QuantIndicators, trampoline: TrampolineBounceContext) -> int:
    """
    - Trampoline Bounce Detected: +40 pt
    - Price above Cloud: close >= cloud_top (+30 pt) or >= cloud_top*0.98 (+20 pt)
    - Kijun Position: kijun_gap >= 0 (+15 pt) or >= -1.0 (+10 pt)
    - Tenkan Alignment: tenkan >= kijun (+15 pt) or close >= tenkan (+10 pt)
    """
```

#### 4. Canonical 3-Tier Classification Decision Rules
```python
def classify_quant_tier(
    ticker: str,
    ind: QuantIndicators,
    weekly: WeeklyTrendContext,
    flow: InstitutionalFlowContext,
    trampoline: TrampolineBounceContext,
    macro_tailwind_sectors: List[str],
    sector: str = "GENERAL"
) -> TierClassification:
    """
    Tier 1 (Sniper Radar / Macro Tailwind + Smart Money):
      - Gate-0 Macro Tailwind: sector in macro_tailwind_sectors
      - Weekly Bull Trend: weekly.is_weekly_bull is True
      - Smart Money Accumulation: flow.is_stealth_accum is True OR flow.flow_ratio >= 1.20 OR flow.obv_status == "STEALTH_ACCUM"
      - High Quant Conviction: bull_score >= 75 OR sniper_score >= 75 OR is_dual_strategy
      - Safe Kijun Support: -0.8% <= kijun_gap <= +3.5%

    Tier 2 (Structural Pullback):
      - Weekly Bull Trend: weekly.is_weekly_bull is True
      - 26-Day Kijun Support: -0.8% <= kijun_gap <= +3.5% (or within +/-3.0%)
      - Tenkan Alignment: ind.tenkan >= ind.kijun
      - Volume Dry-Up: ind.vol_ratio <= 0.85 (Prime: <= 0.60)
      - Bull Score: bull_score >= 75

    Tier 3 (Cloud Sniper):
      - Weekly Bull Trend: weekly.is_weekly_bull is True
      - Cloud Trampoline Bounce: trampoline.detected is True
      - Breakout Momentum: ind.close >= ind.cloud_top * 0.97
      - Sniper Score: sniper_score >= 75

    All Tiers enforce:
      - 1st Target: Entry Price * 1.15 (+15.0%)
      - Hard Stop Loss: Entry Price * 0.96 (-4.0%)
      - 50% Partial Take Profit: Entry Price * 1.08 (+8.0%)
    """
```

---

### 4.4 MSI 2.0 Hard Gauge Unification in `al_sangmoo/domain/quant/macro.py`

Consolidate the hard gauge point matrix into a single SSOT table in `macro.py`:

```python
# 1. US 10Y Treasury Yield (Max 25 pt)
if us10y >= 4.50: us10y_pts = 25.0
elif us10y >= 4.30: us10y_pts = 18.0
elif us10y >= 4.10: us10y_pts = 10.0
elif us10y >= 3.90: us10y_pts = 4.0
else: us10y_pts = 0.0

# 2. VIX Volatility Index (Max 15 pt)
if vix >= 25.0: vix_pts = 15.0
elif vix >= 20.0: vix_pts = 10.0
elif vix >= 16.0: vix_pts = 5.0
else: vix_pts = 0.0

# 3. WTI Crude Oil (Max 10 pt)
if wti >= 85.0: wti_pts = 10.0
elif wti >= 80.0: wti_pts = 6.0
elif wti >= 75.0: wti_pts = 3.0
else: wti_pts = 0.0

# 4. Dollar Index DXY (Max 10 pt)
if dxy >= 105.0: dxy_pts = 10.0
elif dxy >= 103.0: dxy_pts = 6.0
elif dxy >= 100.0: dxy_pts = 2.0
else: dxy_pts = 0.0
```

`youtube_stream_scanner.py` and `generate_dashboard_feed.py` will import `calculate_msi_regime` directly from `al_sangmoo.domain.quant.macro`, completely removing duplicate inline gauge logic.

---

### 4.5 Clean Pipeline & Decoupled Data Flow Plan

```
[ Market Data / yfinance / Cache ]
               │
               ▼
[ al_sangmoo.domain.quant.ichimoku ] ──▶ Pure Indicator DataFrame & Cloud Projection
               │
               ▼
[ al_sangmoo.domain.quant.scoring ]  ──▶ Pure Graduated Scoring & 3-Tier Classification
               │
       ┌───────┴────────────────────────────┐
       ▼                                    ▼
[ generate_dashboard_feed.py ]     [ al_sangmoo_daily_bot.py ]
  (Pure View-Model Builder)          (Orchestrator & Reporter)
  - chart_intelligence               - Calls feed generator
  - dashboard_data.json              - Evaluates live portfolio
  - data/charts/{ticker}.json        - Sends daily email
  (ZERO DB write side-effects)       - Calls persistence to archive
```

---

## 5. Verification Method

1. **Unit Test Verification for Scoring Domain (`test_phase5_3_ssot_quant.py`)**:
   - Create synthetic test candles with known Ichimoku, OBV, and Trampoline geometries.
   - Verify that `calculate_canonical_bull_score`, `calculate_canonical_sniper_score`, and `classify_quant_tier` produce 100% deterministic outputs.
   - Assert that Tier 1, Tier 2, and Tier 3 qualification predicates match user requirements.
2. **Mathematical Equivalence Test across Callers**:
   - Pass identical ticker DataFrames through `al_sangmoo_daily_bot.py` and `generate_dashboard_feed.py` and assert 0% score discrepancy.
3. **MSI 2.0 Climate Equivalence**:
   - Test `macro.py` against `youtube_stream_scanner.py` across 10 boundary conditions (e.g. 10Y=4.32%, VIX=20.5, WTI=81.2) to ensure identical MSI scores.
4. **Regression Suite Execution**:
   - `python tools_and_tests/test_phase1_hardening.py`
   - `python tools_and_tests/test_phase2_modular.py`
   - `python tools_and_tests/test_global60_dual_strategy.py`
   - `python tools_and_tests/test_phase5_1_security.py`
   - `python tools_and_tests/test_phase5_2_concurrency.py`
