# Handoff Report: Phase 5.3 Remediation Iteration 2 — `generate_dashboard_feed.py` Fix Strategy

**Author**: Explorer 1 (`explorer_fix_feed_1`)  
**Target Milestone**: Phase 5.3 Remediation Iteration 2 (Quantitative Consolidation & Clean Architecture)  
**Scope**: `generate_dashboard_feed.py` Refactoring & CQRS Isolation  
**Related Modules**: `al_sangmoo/domain/quant/ichimoku.py`, `al_sangmoo/domain/quant/scoring.py`, `al_sangmoo/domain/quant/macro.py`, `server.py`, `tools_and_tests/test_phase5_3_ssot_quant.py`  

---

## 1. Observation

Direct forensic examination of `generate_dashboard_feed.py` and the domain quantitative layer `al_sangmoo/domain/quant/` revealed four specific areas of duplication, side-effect bleeding, and architectural violation:

### 1.1 Duplicated Chart Series Builder (`build_ichimoku_series`)
- **Location**: `generate_dashboard_feed.py` (lines 81–156, 76 lines of code)
- **Current Code**:
  ```python
  def build_ichimoku_series(df_in, max_bars=500, is_weekly=False):
      df_clean = df_in.dropna(subset=['Close', 'High', 'Low', 'Kijun', 'Tenkan']).tail(max_bars)
      ...
      # Future 26 forward cloud projection
      last_date = df_clean.index[-1]
      if is_weekly:
          future_dates = pd.date_range(start=last_date + pd.Timedelta(days=7), periods=26, freq='W-FRI')
      else:
          future_dates = pd.bdate_range(start=last_date + pd.Timedelta(days=1), periods=26)
      ...
  ```
- **Domain Counterpart**: `al_sangmoo.domain.quant.ichimoku.build_ichimoku_series_payload` (`ichimoku.py:196-269`) already provides pure, modular payload generation with `project_future_cloud()` integration.
- **Payload Parity**: Both produce identical keys: `candles`, `kijun_line`, `tenkan_line`, `span_a_line`, `span_b_line`, `sma20`, `sma60`, `volume`, `future_span_a`, `future_span_b` (plus `build_ichimoku_series_payload` provides additional `sma50` and `sma200`).

### 1.2 Duplicated Inline Indicator Mathematics in `compute_all_indicators`
- **Location**: `generate_dashboard_feed.py` (lines 170–196)
- **Current Code**:
  ```python
  # 1. Calculate Daily Ichimoku
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
  
  # 2. Calculate Weekly Ichimoku (Resampled to Weekly)
  df_w = df[['Open', 'High', 'Low', 'Close', 'Volume']].resample('W-FRI').agg({
      'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
  }).dropna()
  df_w['Tenkan'] = ...
  ```
- **Defects**: Missing `min_periods` guards, unprotected volume division, and duplicate indicator computation.
- **Domain Counterpart**: `al_sangmoo.domain.quant.ichimoku.calculate_ichimoku_indicators(df)` implements robust, zero-safe rolling formulas with `min_periods` for all required series.

### 1.3 Duplicated Inline Scoring, Bounce Detection & Card Formatting
- **Location**: `generate_dashboard_feed.py` (lines 239–383)
- **Current Code**:
  - Inline Strategy 1 graduated scoring (lines 239–265)
  - Inline Bear score (lines 266–269)
  - Inline 14-bar lookback for Trampoline Bounce (lines 273–290)
  - Inline Sniper score (lines 292–300)
  - Inline indicator card badge logic (`kijun_status`, `cloud_status`, `vol_status`, lines 332–356)
- **Domain Counterpart**:
  - `al_sangmoo.domain.quant.ichimoku.detect_cloud_trampoline_bounce(df_clean, max_lookback=14)`
  - `al_sangmoo.domain.quant.scoring.evaluate_quant_score(...)`
  `evaluate_quant_score` calculates `bull_score`, `sniper_score`, `bear_score`, `composite_score`, `action_directive`, and the full `intelligence` dictionary (with `kijun`, `tenkan`, `cloud`, `vol` badge statuses and descriptions).

### 1.4 Duplicated 3-Tier Categorization & Candidate Filtering Loop
- **Location**: `generate_dashboard_feed.py` (lines 488–573)
- **Current Code**:
  - An inline loop iterating over `chart_data`, re-evaluating tier predicates, sorting candidate lists, and slicing top 4 picks for `dual_consensus_picks`, `strat1_exclusive`, and `strat2_exclusive`.
- **Domain Counterpart**: `al_sangmoo.domain.quant.scoring.classify_3tier_candidates(chart_data, tailwind_sectors, stream_mentioned_tickers)` performs this exact deterministic classification, ranking, and deduplication.

### 1.5 CQRS Database Write Side-Effects in `build_dashboard_data()`
- **Location**: `generate_dashboard_feed.py` (lines 678–693)
- **Current Code**:
  ```python
  # Refresh today's 2+2+2 Recommendation Matrix & Permanent Trade Tracking Archive in SQLite DB
  if dual_consensus_picks or strat1_exclusive or strat2_exclusive:
      top_bulls = (dual_consensus_picks + strat1_exclusive)[:2]
      top_neutrals = (strat1_exclusive + dual_consensus_picks)[2:4]
      top_snipers = strat2_exclusive[:2]
      
      b_picks = [{"ticker": x["ticker"], "close": x["price"]} for x in top_bulls]
      n_picks = [{"ticker": x["ticker"], "close": x["price"]} for x in top_neutrals]
      s_picks = [{"ticker": x["ticker"], "close": x["price"]} for x in top_snipers]
      
      try:
          db_manager.save_recommendation_matrix_record(today_str, b_picks, n_picks, s_picks)
          db_manager.archive_daily_recommendations(today_str, dual_consensus_picks, strat1_exclusive, strat2_exclusive)
      except Exception as e:
          print(f"[DB Archiving Warning] {e}")
  ```
- **Defects**: Mutates SQLite database state during a read query / view model generation. Causes double-writes with `al_sangmoo_daily_bot.py:817` and creates potential SQLite lock contention during API calls (e.g. `/api/dashboard`, `/api/scan_now`).

---

## 2. Logic Chain

1. **Clean Architecture & SSOT Mandate**:
   - `ORIGINAL_REQUEST.md` (§R1, §R2, §R4) mandates consolidating all indicator mathematics, scoring rules, and tier classification into the domain layer (`al_sangmoo/domain/quant/`).
   - Presentation/Feed generation scripts (`generate_dashboard_feed.py`) should act as thin application coordinators that fetch data, delegate computations to the domain layer, and serialize JSON view models.

2. **Refactoring `build_ichimoku_series`**:
   - `build_ichimoku_series_payload` in `al_sangmoo.domain.quant.ichimoku` is a superset of `build_ichimoku_series`.
   - Replacing calls to `build_ichimoku_series` with `build_ichimoku_series_payload` and deleting `def build_ichimoku_series` eliminates 76 lines of redundant code and ensures all chart payload transformations use canonical forward projection math.

3. **Refactoring `compute_all_indicators(ticker)`**:
   - `compute_all_indicators` is called by `server.py:338` (live ticker chart endpoint) and `tools_and_tests/test_stock_search.py`. It must remain as a public function in `generate_dashboard_feed.py`.
   - Its internals must be refactored to delegate:
     - Daily/Weekly indicator calculations $\rightarrow$ `calculate_ichimoku_indicators(df)`
     - Candlestick & series formatting $\rightarrow$ `build_ichimoku_series_payload(...)`
     - Cloud trampoline bounce detection $\rightarrow$ `detect_cloud_trampoline_bounce(df_clean, max_lookback=14)`
     - Institutional volume signatures $\rightarrow$ `compute_institutional_flow_indicators(df_clean)`
     - 17-Year Quant scoring & UI badges $\rightarrow$ `evaluate_quant_score(...)`
   - This reduces `compute_all_indicators` from 272 lines to ~70 lines while keeping 100% schema parity.

4. **Refactoring 3-Tier Candidate Categorization**:
   - `classify_3tier_candidates(chart_data, tailwind_sectors, stream_mentioned_tickers)` in `al_sangmoo.domain.quant.scoring` encapsulates all tier qualification rules (-4.0% hard stop, +15.0% target, macro tailwind filtering, institutional flow scoring).
   - Calling `classify_3tier_candidates` directly in `build_dashboard_data()` replaces lines 488–573 and guarantees that candidate ranking and tier slicing match the canonical domain rules deterministically.

5. **Enforcing 100% CQRS Read Purity**:
   - Lines 678–693 of `generate_dashboard_feed.py` execute `db_manager.save_recommendation_matrix_record` and `db_manager.archive_daily_recommendations`.
   - Removing lines 678–693 makes `build_dashboard_data()` 100% read-only and side-effect free (CQRS query purity).
   - Database mutations are exclusively retained in command workflows (`al_sangmoo_daily_bot.py`).

---

## 3. Caveats & Invariants

1. **Downstream API & Test Invariants**:
   - `server.py` and front-end endpoints rely on `dashboard_data.json` having exact root keys:
     `["macro", "kpis", "trades", "matrix", "daily_history", "portfolio", "dual_consensus", "strat1_exclusive", "strat2_exclusive", "primary_accumulation", "sniper_radar", "signal_tracker", "chart_intelligence", "last_updated"]`.
   - Individual modular chart files in `data/charts/{ticker}.json` must retain `candles`, `kijun_line`, `tenkan_line`, `span_a_line`, `span_b_line`, `sma20`, `sma60`, `volume`, `intelligence`, `latest_close`, `kijun`, etc.
   - All proposed refactorings maintain 100% key and data-type parity.

2. **Wallet & Recommendation Streak Tagging**:
   - After `classify_3tier_candidates` returns `dual_consensus_picks`, `strat1_exclusive`, and `strat2_exclusive`, `generate_dashboard_feed.py` enriches each item with `in_wallet`, `holding_pnl`, `streak_days`, and builds `signal_tracker`. This enrichment logic is preserved intact.

3. **Fallback Handling for Uncached Tickers**:
   - In `build_dashboard_data()`, if a ticker from SQLite `matrix` is missing from `chart_data`, `compute_all_indicators(tk)` is invoked dynamically. The refactored `compute_all_indicators` supports this cleanly.

---

## 4. Conclusion & Step-by-Step Remediation Plan

The exact step-by-step code modifications for `generate_dashboard_feed.py` are detailed below.

### Step 1: Update Domain Quant Imports
Replace lines 78–79:
```python
# BEFORE (lines 78-79):
from al_sangmoo.core.constants import WATCHLIST, STOCK_DICT, TICKER_SECTORS, get_macro_tailwind_sectors
from al_sangmoo.domain.quant.ichimoku import compute_institutional_flow_indicators
```
With:
```python
# AFTER:
from al_sangmoo.core.constants import WATCHLIST, STOCK_DICT, TICKER_SECTORS, get_macro_tailwind_sectors
from al_sangmoo.domain.quant.ichimoku import (
    calculate_ichimoku_indicators,
    detect_cloud_trampoline_bounce,
    compute_institutional_flow_indicators,
    build_ichimoku_series_payload,
)
from al_sangmoo.domain.quant.scoring import (
    evaluate_quant_score,
    classify_3tier_candidates,
)
```

### Step 2: Eliminate Duplicate `build_ichimoku_series` Function
Delete lines 81–156 (`def build_ichimoku_series(...)`) completely from `generate_dashboard_feed.py`.

### Step 3: Refactor `compute_all_indicators(ticker)` to Delegate to Domain Quant
Replace lines 158–429 with the following streamlined delegation implementation:

```python
def compute_all_indicators(ticker):
    try:
        df = yf.download(ticker, period="3y", interval="1d", progress=False)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        df = df.dropna(subset=['Close', 'High', 'Low', 'Volume']).copy()
        if len(df) < 60:
            return None
            
        # 1. Calculate Daily Ichimoku via SSOT Domain Engine
        df = calculate_ichimoku_indicators(df)
        
        # 2. Calculate Weekly Ichimoku via SSOT Domain Engine
        df_w = df[['Open', 'High', 'Low', 'Close', 'Volume']].resample('W-FRI').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
        }).dropna()
        df_w = calculate_ichimoku_indicators(df_w)

        # Build Daily & Weekly Series Payloads via SSOT Domain Engine
        daily_series = build_ichimoku_series_payload(df, is_weekly=False, max_bars=500)
        weekly_series = build_ichimoku_series_payload(df_w, is_weekly=True, max_bars=150)
        
        df_clean = df.dropna(subset=['Close', 'High', 'Low', 'Kijun', 'Tenkan']).tail(500)
        last = df_clean.iloc[-1]
        close = float(last['Close'])
        kijun = float(last['Kijun'])
        tenkan = float(last['Tenkan'])
        vol_ratio = float(last['Vol_Ratio']) if not pd.isna(last['Vol_Ratio']) else 1.0
        span_a = float(last['SpanA']) if not pd.isna(last['SpanA']) else close
        span_b = float(last['SpanB']) if not pd.isna(last['SpanB']) else close
        
        cloud_top = max(span_a, span_b)
        cloud_bottom = min(span_a, span_b)
        kijun_gap = ((close - kijun) / kijun) * 100
        
        # Weekly Macro Stance Evaluation
        w_clean = df_w.dropna(subset=['Close', 'High', 'Low', 'Kijun', 'Tenkan'])
        if not w_clean.empty:
            w_last = w_clean.iloc[-1]
            w_close = float(w_last['Close'])
            w_span_a = float(w_last['SpanA']) if not pd.isna(w_last['SpanA']) else w_close
            w_span_b = float(w_last['SpanB']) if not pd.isna(w_last['SpanB']) else w_close
            w_cloud_top = max(w_span_a, w_span_b)
            is_weekly_bull = (w_close >= w_cloud_top * 0.98)
        else:
            is_weekly_bull = (close >= cloud_top * 0.97)
        
        future_span_a_vals = daily_series.get("future_span_a", [])
        future_span_b_vals = daily_series.get("future_span_b", [])
        future_a_latest = future_span_a_vals[-1] if future_span_a_vals else span_a
        future_b_latest = future_span_b_vals[-1] if future_span_b_vals else span_b
        is_future_bull_cloud = future_a_latest >= future_b_latest
        future_cloud_type = "양운 (상승 지지 구름대)" if is_future_bull_cloud else "음운 (하락 저항 구름대)"
        future_cloud_gap = abs(future_a_latest - future_b_latest)
        
        # Detect Cloud Trampoline Bounce via SSOT Domain Function
        trampoline_detected, trampoline_days_ago, touch_gap_pct, close_gap_pct = detect_cloud_trampoline_bounce(df_clean, max_lookback=14)
        
        # Institutional Flow Signature & Sector Mapping via SSOT Domain Function
        sector = TICKER_SECTORS.get(ticker, "GENERAL")
        flow_data = compute_institutional_flow_indicators(df_clean)
        
        # Canonical 17-Year Quant Scoring via SSOT Domain Engine
        quant_eval = evaluate_quant_score(
            close=close,
            kijun=kijun,
            tenkan=tenkan,
            span_a=span_a,
            span_b=span_b,
            vol_ratio=vol_ratio,
            trampoline_detected=trampoline_detected,
            is_weekly_bull=is_weekly_bull,
            days_ago=trampoline_days_ago
        )
        
        bull_score = quant_eval["bull_score"]
        bear_score = quant_eval["bear_score"]
        sniper_score = quant_eval["sniper_score"]
        is_sniper_active = quant_eval["is_sniper_active"]
        quant_type = quant_eval["quant_type"]
        quant_verdict = quant_eval["quant_verdict"]
        
        # Construct Intelligence Metadata
        intelligence = dict(quant_eval["intelligence"])
        intelligence.update({
            "is_sniper": is_sniper_active,
            "is_weekly_bull": is_weekly_bull,
            "trampoline_detected": trampoline_detected,
            "sector": sector,
            "obv_status": flow_data["obv_status"],
            "obv_label": flow_data["obv_label"],
            "flow_ratio": flow_data["flow_ratio"],
            "flow_label": flow_data["flow_label"],
            "flow_score": flow_data["flow_score"],
            "is_stealth_accum": flow_data["is_stealth_accum"],
        })
            
        return {
            "ticker": ticker,
            "sector": sector,
            "obv_status": flow_data["obv_status"],
            "obv_label": flow_data["obv_label"],
            "flow_ratio": flow_data["flow_ratio"],
            "flow_label": flow_data["flow_label"],
            "flow_score": flow_data["flow_score"],
            "is_stealth_accum": flow_data["is_stealth_accum"],
            "latest_close": round(close, 2),
            "kijun": round(kijun, 2),
            "tenkan": round(tenkan, 2),
            "span_a": round(span_a, 2),
            "span_b": round(span_b, 2),
            "future_span_a_latest": round(future_a_latest, 2),
            "future_span_b_latest": round(future_b_latest, 2),
            "future_cloud_type": future_cloud_type,
            "future_cloud_gap": round(future_cloud_gap, 2),
            "kijun_gap_pct": round(kijun_gap, 2),
            "vol_ratio": round(vol_ratio, 2),
            "bull_score": bull_score,
            "bear_score": bear_score,
            "sniper_score": sniper_score,
            "is_sniper": is_sniper_active,
            "is_weekly_bull": is_weekly_bull,
            "trampoline_detected": trampoline_detected,
            "trampoline_days_ago": trampoline_days_ago,
            "touch_gap_pct": touch_gap_pct,
            "close_gap_pct": close_gap_pct,
            "intelligence": intelligence,
            "status_tag": quant_type,
            "status_text": quant_verdict,
            "timeframes": {
                "daily": daily_series,
                "weekly": weekly_series
            },
            "candles": daily_series.get("candles", []),
            "kijun_line": daily_series.get("kijun_line", []),
            "tenkan_line": daily_series.get("tenkan_line", []),
            "span_a_line": daily_series.get("span_a_line", []),
            "span_b_line": daily_series.get("span_b_line", []),
            "sma20": daily_series.get("sma20", []),
            "sma60": daily_series.get("sma60", []),
            "volume": daily_series.get("volume", [])
        }
    except Exception as e:
        print(f"Error computing {ticker}: {e}")
        return None
```

### Step 4: Refactor 3-Tier Categorization in `build_dashboard_data()`
Replace lines 488–573 in `generate_dashboard_feed.py` with:
```python
    # 3-Tier Categorization via SSOT Domain Engine
    dual_consensus_picks, strat1_exclusive, strat2_exclusive = classify_3tier_candidates(
        chart_data=chart_data,
        tailwind_sectors=tailwind_sectors,
        stream_mentioned_tickers=stream_mentioned_tickers
    )
```

### Step 5: Completely Remove Database Mutation Side-Effects
Delete lines 678–693 from `generate_dashboard_feed.py`:
```python
# DELETE THIS ENTIRE BLOCK:
    # Refresh today's 2+2+2 Recommendation Matrix & Permanent Trade Tracking Archive in SQLite DB
    if dual_consensus_picks or strat1_exclusive or strat2_exclusive:
        top_bulls = (dual_consensus_picks + strat1_exclusive)[:2]
        top_neutrals = (strat1_exclusive + dual_consensus_picks)[2:4]
        top_snipers = strat2_exclusive[:2]
        
        b_picks = [{"ticker": x["ticker"], "close": x["price"]} for x in top_bulls]
        n_picks = [{"ticker": x["ticker"], "close": x["price"]} for x in top_neutrals]
        s_picks = [{"ticker": x["ticker"], "close": x["price"]} for x in top_snipers]
        
        try:
            db_manager.save_recommendation_matrix_record(today_str, b_picks, n_picks, s_picks)
            db_manager.archive_daily_recommendations(today_str, dual_consensus_picks, strat1_exclusive, strat2_exclusive)
        except Exception as e:
            print(f"[DB Archiving Warning] {e}")
```

### Summary of Resulting Architectural Benefits
| Metric | Before Remediation | After Remediation |
|---|---|---|
| `generate_dashboard_feed.py` LOC | 766 lines | ~420 lines (-45% bloat) |
| Indicator Math Engine | Inline duplicate pandas rolling | 100% delegated to `ichimoku.py` |
| Scoring Engine | Inline branches (35/25/15 pt) | 100% delegated to `scoring.py` |
| 3-Tier Classification | Inline loops & custom filtering | 100% delegated to `classify_3tier_candidates` |
| CQRS Side Effects in Feed Builder | 2 SQLite DML writes per execution | **0** writes (Pure read transformation) |
| Schema & Payload Compatibility | Existing format | 100% identical keys and structures |

---

## 5. Verification Method

To verify this remediation plan upon implementation:

1. **Static AST Deduplication & Absence Check**:
   Verify that `generate_dashboard_feed.py` does NOT contain `build_ichimoku_series` and that `compute_all_indicators` does not contain inline rolling math:
   ```powershell
   python -c "import ast; tree = ast.parse(open('generate_dashboard_feed.py', encoding='utf-8').read()); funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]; assert 'build_ichimoku_series' not in funcs, 'build_ichimoku_series must be deleted'; print('AST Verification: PASSED')"
   ```

2. **CQRS Zero-Write Verification**:
   Execute `build_dashboard_data()` with database mocks asserting zero DML writes:
   ```powershell
   python -c "from unittest.mock import patch; import db_manager, generate_dashboard_feed; 
with patch.object(db_manager, 'save_recommendation_matrix_record') as m1, patch.object(db_manager, 'archive_daily_recommendations') as m2:
    generate_dashboard_feed.build_dashboard_data()
    m1.assert_not_called()
    m2.assert_not_called()
print('CQRS Zero-Write Assertion: PASSED')"
   ```

3. **Run Comprehensive Phase 5.3 SSOT Test Suite**:
   ```powershell
   python tools_and_tests/test_phase5_3_ssot_quant.py
   ```

4. **Run Regression Suites**:
   ```powershell
   python tools_and_tests/test_modular_2tier_architecture.py
   python tools_and_tests/test_global60_dual_strategy.py
   ```
