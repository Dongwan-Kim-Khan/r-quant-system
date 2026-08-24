# Remediation Strategy & Architectural Handoff Report (Iteration 2)

**Target Milestone**: Phase 5.3 Quantitative Consolidation & Clean Architecture Refactoring  
**Author**: Explorer Fix Bot 2 (`explorer_fix_bot_2`)  
**Codebase Root**: `d:\코딩\Playground\al_sangmoo_project`  
**Working Directory**: `d:\코딩\Playground\al_sangmoo_project\.agents\explorer_fix_bot_2`  
**Reference Reports**:
- Auditor Report: `.agents/auditor_phase5_3/handoff.md`
- Reviewer 1 Report: `.agents/reviewer_phase5_3_1/handoff.md`
- Reviewer 2 Report: `.agents/reviewer_phase5_3_2/handoff.md`

---

## 1. Observation

Direct forensic inspection of the codebase confirmed the following architectural integrity violations and duplication defects:

### 1.1 `al_sangmoo_daily_bot.py` Duplicate Indicators & Inline Heuristics
- **File**: `al_sangmoo_daily_bot.py`
  - **Lines 82-101**: `def calculate_indicators(df):` re-implements pandas rolling Tenkan (9), Kijun (26), SpanA (shift 26), SpanB (shift 26), SMA20, SMA60, Vol_SMA20, and Vol_Ratio inline.
  - **Lines 103-240**: `def scan_and_select_2x2x2(stream_sentiment_list=None):` downloads OHLCV data and computes inline scores with unaligned obsolete 65pt thresholds (`bull_score >= 65`, line 202) and crude volume dry-up ratios (`vol_ratio <= 0.75`) instead of delegating to `al_sangmoo.domain.quant.scoring`.
  - **Line 312**: In `evaluate_active_positions_and_update`, it calls `df = calculate_indicators(df)`.
  - **Domain Imports**: `al_sangmoo.domain.quant` is **not imported** anywhere in `al_sangmoo_daily_bot.py`.

### 1.2 `youtube_stream_scanner.py` Inline Macro Stance Duplication
- **File**: `youtube_stream_scanner.py`
  - **Lines 260-386**: `def analyze_macro_regime_and_climate(title, full_transcript, gauges):` contains 126 lines of duplicate calculation for financial hard gauges (US10Y, VIX, WTI, DXY weights), host spoken NLP sentiment scoring, geopolitical/economic shock factoring, and MSI regime threshold branching.
  - **Domain Imports**: `al_sangmoo.domain.quant.macro.evaluate_macro_stance` is **not imported** or used.

### 1.3 `generate_dashboard_feed.py` CQRS Mutation & Duplicate Math
- **File**: `generate_dashboard_feed.py`
  - **Lines 81-156**: `build_ichimoku_series()` duplicates forward cloud projection and chart formatting inline instead of calling `al_sangmoo.domain.quant.ichimoku.build_ichimoku_series_payload`.
  - **Lines 170-196**: `compute_all_indicators()` computes inline daily and weekly rolling math (`df['Tenkan'] = ...`, `df['Kijun'] = ...`, `df['SpanA'] = ...`, `df['SpanB'] = ...`).
  - **Lines 239-300 & 488-573**: Calculates inline bull/sniper/bear scores and 3-tier candidate categorization loop instead of delegating to `al_sangmoo.domain.quant.scoring`.
  - **Lines 678-693**: In `build_dashboard_data()`, SQLite database write mutations are executed during read-only view model generation:
    ```python
    db_manager.save_recommendation_matrix_record(today_str, b_picks, n_picks, s_picks)
    db_manager.archive_daily_recommendations(today_str, dual_consensus_picks, strat1_exclusive, strat2_exclusive)
    ```

### 1.4 `tools_and_tests/test_phase5_3_ssot_quant.py` Facade Test Assertions
- **File**: `tools_and_tests/test_phase5_3_ssot_quant.py`
  - **Lines 673-678**: `TestTier4CQRSSideEffectFreePipeline` mocked `save_recommendation_matrix_record` and `archive_daily_recommendations` to allow `build_dashboard_data()` to run without throwing errors, but **omitted** asserting `mock_save_matrix.assert_not_called()` or `mock_archive.assert_not_called()`.
  - **Lines 727-744**: `TestTier5StaticASTDeduplication` passed solely because a single helper import existed at line 79 of `generate_dashboard_feed.py`, without asserting the absence of `calculate_indicators`, `build_ichimoku_series`, `compute_all_indicators`, or `analyze_macro_regime_and_climate`.

---

## 2. Logic Chain

1. **SSOT Quantitative Consolidation (ORIGINAL_REQUEST R1, R2, R3)**:
   - Having multiple scripts compute rolling indicators (`rolling(9)`, `rolling(26)`, `rolling(52)`, `rolling(20)`) and scoring formulas independently violates Single Source of Truth (SSOT).
   - In `al_sangmoo_daily_bot.py`, `bull_score >= 65` and `vol_ratio <= 0.75` conflict with `al_sangmoo.domain.quant.scoring`, which specifies graduated bull scoring (35/25/15 pt cloud, 35/25/15 pt Kijun support, 20/15/10 pt VDU) with canonical 80pt/70pt tier qualification.
   - In `youtube_stream_scanner.py`, computing MSI 2.0 inline risks gauge weight divergence (e.g. US10Y 25/18/10/4/0 vs 15pt).
   - Replacing inline logic in both scripts with direct calls to `al_sangmoo.domain.quant` guarantees 100% deterministic outputs across batch scanners, dashboards, emails, and API endpoints.

2. **CQRS Separation & Zero Double-Writing (ORIGINAL_REQUEST R4)**:
   - `generate_dashboard_feed.build_dashboard_data()` is a **Query / View Model Builder**. Executing DML `INSERT`/`UPDATE` operations (`save_recommendation_matrix_record`, `archive_daily_recommendations`) inside a query causes race conditions, SQLite lock contention on concurrent `/api/scan_now` or feed generation requests, and duplicate writes with `al_sangmoo_daily_bot.py`.
   - `al_sangmoo_daily_bot.py:main` is the **Command Entry Point** (scheduled daily reporting / CLI job).
   - Stripping write operations from `build_dashboard_data()` ensures 100% side-effect free queries.
   - Having `al_sangmoo_daily_bot.py:main` execute explicit SQLite persistence (`save_macro_history_record`, `save_recommendation_matrix_record`, `archive_daily_recommendations`) ensures all persistence happens exactly once per execution cycle with zero double-writing.

3. **Test Hardening & Forensic Integrity**:
   - Asserting `mock_save_matrix.assert_not_called()` and `mock_archive.assert_not_called()` in Tier 4 prevents future CQRS write regressions.
   - Adding AST assertions in Tier 5 to check for the absence of duplicate functions (`calculate_indicators`, `build_ichimoku_series`, inline macro calculators) guarantees that facade implementations cannot pass CI.

---

## 3. Caveats

- **Signature Compatibility for `scan_and_select_2x2x2`**:
  - `server.py:379` calls:
    `bull_picks, neutral_picks, bear_picks, macro_climate = al_sangmoo_daily_bot.scan_and_select_2x2x2()`
  - Existing regression tests in `test_phase5_2_concurrency.py` mock `scan_and_select_2x2x2` returning 4 items: `(bull_picks, neutral_picks, bear_picks, macro_climate)`.
  - The refactored `scan_and_select_2x2x2` must continue returning a 4-tuple: `(bull_picks, neutral_picks, bear_picks, macro_climate)` where `macro_climate` is a dictionary containing `macro_stance` and `msi_score`.
- **Signature Compatibility for `analyze_macro_regime_and_climate`**:
  - `youtube_stream_scanner.py:528` calls `analyze_macro_regime_and_climate(title, full_transcript, gauges)`.
  - `evaluate_macro_stance(gauges=gauges, transcript=full_transcript, title=title)` produces a dictionary with all required keys (`msi_score`, `msi_breakdown`, `macro_stance`, `macro_stance_kr`, `macro_headline`, `macro_action_directive`, `external_shocks`, `defense_keyword_count`).

---

## 4. Conclusion & Concrete Step-by-Step Remediation Plan

### Step 1: Refactor `al_sangmoo_daily_bot.py`
1. **Add Domain Imports**:
   ```python
   from al_sangmoo.domain.quant.ichimoku import (
       calculate_ichimoku_indicators,
       detect_cloud_trampoline_bounce,
       compute_institutional_flow_indicators
   )
   from al_sangmoo.domain.quant.scoring import (
       QuantIndicators,
       WeeklyTrendContext,
       InstitutionalFlowContext,
       TrampolineBounceContext,
       calculate_canonical_bull_score,
       calculate_canonical_sniper_score,
       calculate_canonical_bear_score,
       evaluate_quant_score,
       classify_quant_tier,
       classify_3tier_candidates
   )
   from al_sangmoo.domain.quant.macro import (
       evaluate_macro_stance,
       calculate_msi_regime
   )
   from al_sangmoo.core.constants import WATCHLIST, STOCK_DICT, TICKER_SECTORS, get_macro_tailwind_sectors
   ```

2. **Delete Duplicate `calculate_indicators` Function**:
   - Completely delete `def calculate_indicators(df):` (lines 82-101).
   - In `evaluate_active_positions_and_update` (line 312), replace `df = calculate_indicators(df)` with `df = calculate_ichimoku_indicators(df)`.
   - Update stop loss / target rules to align with Canonical -4.0% hard stop / +15.0% target / +8.0% partial TP.

3. **Refactor `scan_and_select_2x2x2` to Delegate to Domain Modules**:
   Replace lines 103-240 with:
   ```python
   def scan_and_select_2x2x2(stream_sentiment_list=None):
       """
       3-Gate Quantitative Filtering & 3-Tier Recommendation Engine.
       SSOT Integration: Delegates indicator math to al_sangmoo.domain.quant.ichimoku
       and scoring to al_sangmoo.domain.quant.scoring.
       """
       if stream_sentiment_list is None:
           stream_sentiment_list = []
           
       stream_tickers = {item["ticker"] for item in stream_sentiment_list if isinstance(item, dict) and "ticker" in item}
       priority_tickers = [item["ticker"] for item in stream_sentiment_list if isinstance(item, dict) and "ticker" in item]
       
       scan_list = []
       for t in priority_tickers:
           if t not in scan_list:
               scan_list.append(t)
       for t in UNIVERSE:
           if t not in scan_list:
               scan_list.append(t)
               
       # 1. Macro Climate & Sector Tailwind Gate
       try:
           stream_info = youtube_stream_scanner.fetch_latest_wepoll_stream()
           macro_climate = stream_info.get("macro_climate", {})
       except Exception:
           macro_climate = evaluate_macro_stance()
           
       macro_stance = macro_climate.get("macro_stance", "DEFENSE_HOLD")
       tailwind_sectors = get_macro_tailwind_sectors(macro_stance)
       
       chart_data = {}
       for ticker in scan_list:
           try:
               df = yf.download(ticker, period="6mo", interval="1d", progress=False)
               if df.empty or len(df) < 55:
                   continue
               if isinstance(df.columns, pd.MultiIndex):
                   df.columns = df.columns.get_level_values(0)
                   
               df = calculate_ichimoku_indicators(df)
               df_clean = df.dropna(subset=['Close', 'Kijun', 'Tenkan', 'SMA20', 'Vol_Ratio'])
               if df_clean.empty:
                   continue
                   
               last = df_clean.iloc[-1]
               close = float(last['Close'])
               kijun = float(last['Kijun'])
               tenkan = float(last['Tenkan'])
               vol_ratio = float(last['Vol_Ratio'])
               span_a = float(last['SpanA']) if not pd.isna(last['SpanA']) else close
               span_b = float(last['SpanB']) if not pd.isna(last['SpanB']) else close
               
               # Resample weekly for weekly trend alignment
               df_w = df[['Open', 'High', 'Low', 'Close', 'Volume']].resample('W-FRI').agg({
                   'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
               }).dropna()
               if len(df_w) >= 26:
                   df_w = calculate_ichimoku_indicators(df_w)
                   w_clean = df_w.dropna(subset=['Close', 'Kijun', 'Tenkan'])
                   if not w_clean.empty:
                       w_last = w_clean.iloc[-1]
                       w_close = float(w_last['Close'])
                       w_sp_a = float(w_last['SpanA']) if not pd.isna(w_last['SpanA']) else w_close
                       w_sp_b = float(w_last['SpanB']) if not pd.isna(w_last['SpanB']) else w_close
                       is_weekly_bull = (w_close >= max(w_sp_a, w_sp_b) * 0.98)
                   else:
                       is_weekly_bull = True
               else:
                   is_weekly_bull = (close >= max(span_a, span_b) * 0.97)
                   
               tramp_detected, tramp_days, touch_gap, close_gap = detect_cloud_trampoline_bounce(df_clean)
               flow_data = compute_institutional_flow_indicators(df)
               
               q_eval = evaluate_quant_score(
                   close=close,
                   kijun=kijun,
                   tenkan=tenkan,
                   span_a=span_a,
                   span_b=span_b,
                   vol_ratio=vol_ratio,
                   trampoline_detected=tramp_detected,
                   is_weekly_bull=is_weekly_bull,
                   days_ago=tramp_days
               )
               
               chart_data[ticker] = {
                   "ticker": ticker,
                   "price": close,
                   "close": close,
                   "latest_close": close,
                   "kijun": kijun,
                   "latest_kijun": kijun,
                   "tenkan": tenkan,
                   "latest_tenkan": tenkan,
                   "span_a": span_a,
                   "span_b": span_b,
                   "vol_ratio": vol_ratio,
                   "latest_vol_ratio": vol_ratio,
                   "is_weekly_bull": is_weekly_bull,
                   "trampoline_detected": tramp_detected,
                   "trampoline_days_ago": tramp_days,
                   "touch_gap_pct": touch_gap,
                   "close_gap_pct": close_gap,
                   "obv_status": flow_data["obv_status"],
                   "obv_label": flow_data["obv_label"],
                   "flow_ratio": flow_data["flow_ratio"],
                   "flow_label": flow_data["flow_label"],
                   "flow_score": flow_data["flow_score"],
                   "is_stealth_accum": flow_data["is_stealth_accum"],
                   "action": q_eval["action_directive"]
               }
           except Exception:
               continue
               
       tier1_picks, tier2_picks, tier3_picks = classify_3tier_candidates(
           chart_data=chart_data,
           tailwind_sectors=tailwind_sectors,
           stream_mentioned_tickers=stream_tickers
       )
       
       bull_picks = (tier1_picks + tier2_picks)[:2]
       neutral_picks = (tier2_picks + tier1_picks)[2:4]
       bear_picks = tier3_picks[:2]
       
       return bull_picks, neutral_picks, bear_picks, macro_climate
   ```

4. **Refactor `al_sangmoo_daily_bot.py:main()` for Explicit, Non-Duplicating Persistence**:
   - `build_dashboard_data()` generates view-model payload with 0 database writes.
   - `main()` explicitly saves macro snapshot, recommendation matrix, and archives daily recommendations into SQLite via `db_manager.save_macro_history_record`, `db_manager.save_recommendation_matrix_record`, and `db_manager.archive_daily_recommendations`.

---

### Step 2: Refactor `youtube_stream_scanner.py`
1. **Import `evaluate_macro_stance` from Domain Layer**:
   ```python
   from al_sangmoo.domain.quant.macro import evaluate_macro_stance
   ```
2. **Replace Duplicate `analyze_macro_regime_and_climate` Body**:
   Replace lines 260-386 with direct delegation:
   ```python
   def analyze_macro_regime_and_climate(title, full_transcript, gauges):
       """
       Evaluates Gate-0 Macro Climate & Computes Macro Stance Index 2.0 (MSI: 0~100점).
       Delegates exclusively to SSOT domain module al_sangmoo.domain.quant.macro.evaluate_macro_stance.
       """
       return evaluate_macro_stance(
           gauges=gauges,
           transcript=full_transcript,
           title=title
       )
   ```

---

### Step 3: Refactor `generate_dashboard_feed.py`
1. **Remove Database Writes from `build_dashboard_data()`**:
   - Delete lines 678-693 (`db_manager.save_recommendation_matrix_record` and `db_manager.archive_daily_recommendations`).
2. **Remove Duplicate `build_ichimoku_series`**:
   - Replace `build_ichimoku_series` (lines 81-156) with import and call to `build_ichimoku_series_payload` from `al_sangmoo.domain.quant.ichimoku`.
3. **Refactor `compute_all_indicators` & Categorization**:
   - Delegate rolling indicator math to `calculate_ichimoku_indicators`.
   - Delegate scoring to `evaluate_quant_score`.
   - Delegate 3-tier categorization to `classify_3tier_candidates`.

---

### Step 4: Harden `tools_and_tests/test_phase5_3_ssot_quant.py`
1. **Tier 4 CQRS Isolation Assertions**:
   ```python
   with patch.object(db_manager, "save_recommendation_matrix_record") as mock_save_matrix, \
        patch.object(db_manager, "archive_daily_recommendations") as mock_archive:
       payload = generate_dashboard_feed.build_dashboard_data()
       mock_save_matrix.assert_not_called()
       mock_archive.assert_not_called()
   ```
2. **Tier 5 AST Deduplication Assertions**:
   ```python
   bot_funcs = [n.name for n in ast.walk(bot_tree) if isinstance(n, ast.FunctionDef)]
   feed_funcs = [n.name for n in ast.walk(feed_tree) if isinstance(n, ast.FunctionDef)]
   
   self.assertNotIn("calculate_indicators", bot_funcs, "al_sangmoo_daily_bot.py must NOT define duplicate calculate_indicators")
   self.assertNotIn("build_ichimoku_series", feed_funcs, "generate_dashboard_feed.py must NOT define duplicate build_ichimoku_series")
   
   # Check domain quant imports
   for name, tree in [("feed", feed_tree), ("bot", bot_tree), ("yt", yt_tree)]:
       imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
       self.assertTrue(
           any("al_sangmoo.domain.quant" in m for m in imports),
           f"{name} must import from al_sangmoo.domain.quant"
       )
   ```

---

## 5. Verification Method

To independently verify the implementation after code application:

1. **Static AST & CQRS Deduplication Test**:
   ```powershell
   python -m unittest tools_and_tests/test_phase5_3_ssot_quant.py -v -k TestTier4CQRSSideEffectFreePipeline
   python -m unittest tools_and_tests/test_phase5_3_ssot_quant.py -v -k TestTier5StaticASTDeduplication
   ```
2. **Full Phase 5.3 SSOT Test Suite**:
   ```powershell
   python tools_and_tests/test_phase5_3_ssot_quant.py
   ```
3. **Forensic AST Check**:
   ```powershell
   python .agents/auditor_phase5_3/forensic_check.py
   ```
4. **Full Platform Regression Pass**:
   ```powershell
   python tools_and_tests/test_phase1_hardening.py
   python tools_and_tests/test_phase2_modular.py
   python tools_and_tests/test_phase4_execution.py
   python tools_and_tests/test_phase5_1_security.py
   python tools_and_tests/test_phase5_2_concurrency.py
   python tools_and_tests/test_global60_dual_strategy.py
   ```

**Invalidation Conditions**:
- If `calculate_indicators` exists in `al_sangmoo_daily_bot.py`.
- If `build_dashboard_data()` invokes any `save_recommendation_matrix_record` or `archive_daily_recommendations`.
- If `analyze_macro_regime_and_climate` in `youtube_stream_scanner.py` computes hard gauges inline rather than delegating to `evaluate_macro_stance`.
