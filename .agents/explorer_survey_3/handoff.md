# Forensic Survey & Investigation Report: Phase 5.3 Quantitative Consolidation, MSI 2.0 Parameter Unification & Side-Effect Elimination

**Author**: Explorer 3 (Survey Agent)  
**Target Milestone**: Phase 5.3 Quantitative Consolidation & Clean Architecture Refactoring  
**Working Directory**: `d:\코딩\Playground\al_sangmoo_project\.agents\explorer_survey_3`  
**Date / Timestamp**: 2026-08-23T01:54:00+09:00  

---

## 1. Observation

### 1.1 MSI 2.0 Parameter & Logic Discrepancies
Direct code comparison between domain module `al_sangmoo/domain/quant/macro.py` and application script `youtube_stream_scanner.py`:

#### 1.1.1 US 10-Year Treasury Yield (`^TNX`, Max 25.0 pt)
- **`al_sangmoo/domain/quant/macro.py:15-24`**:
  ```python
  us10y_val = gauges.get("us10y", {}).get("val", 4.0)
  if us10y_val >= 4.50:
      us10y_pts = 25.0
  elif us10y_val >= 4.30:
      us10y_pts = 15.0
  elif us10y_val >= 4.10:
      us10y_pts = 8.0
  else:
      us10y_pts = 0.0
  ```
- **`youtube_stream_scanner.py:275-279`**:
  ```python
  if us10y_val >= 4.50: us10y_pts = 25.0
  elif us10y_val >= 4.30: us10y_pts = 18.0   # CONFLICT: 18.0 vs 15.0
  elif us10y_val >= 4.10: us10y_pts = 10.0   # CONFLICT: 10.0 vs 8.0
  elif us10y_val >= 3.90: us10y_pts = 4.0    # CONFLICT: 4.0 vs 0.0
  else: us10y_pts = 0.0
  ```

#### 1.1.2 VIX Fear & Volatility Index (`^VIX`, Max 15.0 pt)
- **`al_sangmoo/domain/quant/macro.py:37-44`**:
  ```python
  vix_val = gauges.get("vix", {}).get("val", 15.0)
  if vix_val >= 25.0:
      vix_pts = 15.0
  elif vix_val >= 20.0:
      vix_pts = 8.0
  else:
      vix_pts = 0.0
  ```
- **`youtube_stream_scanner.py:282-285`**:
  ```python
  if vix_val >= 25.0: vix_pts = 15.0
  elif vix_val >= 20.0: vix_pts = 10.0        # CONFLICT: 10.0 vs 8.0
  elif vix_val >= 16.0: vix_pts = 5.0         # CONFLICT: 5.0 vs 0.0
  else: vix_pts = 0.0
  ```

#### 1.1.3 WTI Crude Oil (`CL=F`, Max 10.0 pt)
- **`al_sangmoo/domain/quant/macro.py:47-54`**:
  ```python
  wti_val = gauges.get("wti", {}).get("val", 75.0)
  if wti_val >= 85.0:
      wti_pts = 10.0
  elif wti_val >= 80.0:
      wti_pts = 5.0
  else:
      wti_pts = 0.0
  ```
- **`youtube_stream_scanner.py:288-291`**:
  ```python
  if wti_val >= 85.0: wti_pts = 10.0
  elif wti_val >= 80.0: wti_pts = 6.0         # CONFLICT: 6.0 vs 5.0
  elif wti_val >= 75.0: wti_pts = 3.0         # CONFLICT: 3.0 vs 0.0
  else: wti_pts = 0.0
  ```

#### 1.1.4 Dollar Index (`DX-Y.NYB`, Max 10.0 pt)
- **`al_sangmoo/domain/quant/macro.py:27-34`**:
  ```python
  dxy_val = gauges.get("dxy", {}).get("val", 100.0)
  if dxy_val >= 106.0:
      dxy_pts = 10.0
  elif dxy_val >= 104.0:
      dxy_pts = 5.0
  else:
      dxy_pts = 0.0
  ```
- **`youtube_stream_scanner.py:294-297`**:
  ```python
  if dxy_val >= 105.0: dxy_pts = 10.0         # CONFLICT: 105.0 vs 106.0
  elif dxy_val >= 103.0: dxy_pts = 6.0        # CONFLICT: 103.0 vs 104.0, 6.0 vs 5.0
  elif dxy_val >= 100.0: dxy_pts = 2.0        # CONFLICT: 100.0 vs 0.0
  else: dxy_pts = 0.0
  ```

#### 1.1.5 NLP Sentiment & External Shock Formulation
- **`al_sangmoo/domain/quant/macro.py:56-66`**:
  - Requires pre-computed `defense_count: int`, `buy_count: int`, and `matched_shocks: list`.
  - `m_nlp = min(25.0, round(def_ratio * 25.0, 1))` (default 12.5 if 0 tokens).
  - `m_shock = min(15.0, len(matched_shocks) * 5.0)`.
- **`youtube_stream_scanner.py:301-326`**:
  - Implements inline regex keyword token counting for `MACRO_DEFENSE_KEYWORDS` and buy keywords.
  - Implements subtitle length fallback heuristic (`m_nlp = 16.0 if title_defense else 8.0` if `< 500` chars).
  - Implements category-specific weights for shocks (War: 6pt, Tariffs: 4pt, Fed/Rates: 5pt).

---

### 1.2 Database Write Side-Effects & CQRS Violation
Investigation of the data pipeline identified hidden persistence side-effects during read-only view model generation:

1. **`generate_dashboard_feed.py:688-693` (`build_dashboard_data`)**:
   ```python
   try:
       db_manager.save_recommendation_matrix_record(today_str, b_picks, n_picks, s_picks)
       db_manager.archive_daily_recommendations(today_str, dual_consensus_picks, strat1_exclusive, strat2_exclusive)
   except Exception as e:
       print(f"[DB Archiving Warning] {e}")
   ```
   - **Side-Effect**: `build_dashboard_data()` modifies permanent trade history and recommendation records in SQLite as a side-effect of generating `dashboard_data.json` and chart files.
   - When invoked from `server.py` (`POST /api/scan_now`) or any dashboard view refresher, this silently mutates the database.

2. **`al_sangmoo_daily_bot.py:795-820` (`main`)**:
   ```python
   # Line 795: Calls build_dashboard_data(), which triggers the DB writes above
   feed_data = generate_dashboard_feed.build_dashboard_data(target_tickers=UNIVERSE, force_stream_fetch=False)

   # Lines 809-817: Immediately performs redundant duplicate writes to SQLite
   bull_picks = (dual_consensus + strat1_exclusive)[:2]
   neutral_picks = (strat1_exclusive + dual_consensus)[2:4]
   bear_picks = strat2_exclusive[:2]
   db_manager.save_macro_history_record(today_str, macro_climate, macro_gauges)
   db_manager.save_recommendation_matrix_record(today_str, bull_picks, neutral_picks, bear_picks)
   ```
   - **Double-Write Issue**: In a single execution run of `al_sangmoo_daily_bot.py`, `save_recommendation_matrix_record` is invoked twice. Slicing in `build_dashboard_data()` (`top_neutrals = (strat1_exclusive + dual_consensus)[2:4]`) and `al_sangmoo_daily_bot.py` creates redundant transactions and potential data race discrepancies.

---

### 1.3 Quantitative Indicator Math Duplication
1. **`al_sangmoo_daily_bot.py:82-101` (`calculate_indicators`)**:
   - Duplicates Tenkan (9), Kijun (26), SpanA (26 shifted), SpanB (52 shifted), SMA20, SMA60, Vol_SMA20, Vol_Ratio.
2. **`generate_dashboard_feed.py:171-196` (`compute_all_indicators`)**:
   - Duplicates Tenkan, Kijun, SpanA, SpanB, SMA20, SMA60, Vol_SMA20, Vol_Ratio on both Daily and Weekly resampled DataFrames instead of importing from `al_sangmoo.domain.quant.ichimoku`.
3. **Scoring Inconsistencies**:
   - `al_sangmoo_daily_bot.py:202`: Threshold filter `bull_score >= 65`.
   - `al_sangmoo/domain/quant/ichimoku.py:87`: Threshold filter `bull_score >= 70`.
   - `generate_dashboard_feed.py:239-250`: Graduated scoring (+35/+25/+15) vs binary scoring (+35/0).

---

### 1.4 Test Infrastructure Status
1. **Existing Baseline Suites Verified Green**:
   - `tools_and_tests/test_phase1_hardening.py` -> 100% Green (Exit Code 0).
   - `tools_and_tests/test_phase2_modular.py` -> 100% Green (Exit Code 0).
   - `tools_and_tests/test_phase4_execution.py` -> 100% Green (Exit Code 0).
   - `tools_and_tests/test_global60_dual_strategy.py` -> 100% Green (Exit Code 0).
   - `tools_and_tests/test_phase5_1_security.py` -> 100% Green (Exit Code 0).
   - `tools_and_tests/test_phase5_2_concurrency.py` -> 100% Green (Exit Code 0).
2. **Target Test Suite**:
   - `tools_and_tests/test_phase5_3_ssot_quant.py` does not currently exist.

---

## 2. Logic Chain

```
[Observation 1.1: Divergent MSI Weights & Logic]
      │
      ├─> `macro.py` has 4-tier US10Y (0,8,15,25) while `youtube_stream_scanner.py` has 5-tier (0,4,10,18,25)
      ├─> VIX, WTI, and DXY point tables have conflicting thresholds across files
      └─> Directives and textual outputs are duplicated across 3 separate files
      │
      ▼
[Conclusion 1: Unify in al_sangmoo.domain.quant.macro as SSOT]
      │
      ├─> Establish canonical `evaluate_macro_stance(gauges, transcript, title, ...)` in `macro.py`
      ├─> Standardize on the refined institutional gauge scale (e.g. 5-tier graduated weights)
      ├─> Provide backward-compatible adapter `calculate_msi_regime()`
      └─> Refactor `youtube_stream_scanner.py` and `generate_dashboard_feed.py` to delegate 100% to `macro.py`

[Observation 1.2: DB Writes inside build_dashboard_data]
      │
      ├─> `build_dashboard_data()` in `generate_dashboard_feed.py:688-693` writes to DB
      ├─> `al_sangmoo_daily_bot.py:816-817` writes to DB immediately after
      └─> View model generation mutates persistence state (violates CQRS)
      │
      ▼
[Conclusion 2: Pure Pipeline Refactoring & Explicit Persistence]
      │
      ├─> Strip all DML (`save_recommendation_matrix_record`, `archive_daily_recommendations`) from `build_dashboard_data()`
      ├─> Make `build_dashboard_data()` a pure read-and-transform function returning JSON payload & chart caches
      └─> Keep database persistence exclusively in command entry points (`al_sangmoo_daily_bot.py:main`, explicit sync endpoints)

[Observation 1.3 & 1.4: Math Duplication & Missing Phase 5.3 Test]
      │
      ├─> `calculate_indicators` in `al_sangmoo_daily_bot.py` and inline formulas in `generate_dashboard_feed.py`
      └─> Need an automated regression test suite to enforce SSOT invariants
      │
      ▼
[Conclusion 3: Domain Math Consolidation & Phase 5.3 Test Suite]
      │
      ├─> Import all indicators and 3-Tier scoring exclusively from `al_sangmoo.domain.quant`
      └─> Create comprehensive 6-Tier `tools_and_tests/test_phase5_3_ssot_quant.py`
```

---

## 3. Caveats

1. **YouTube Subtitle Fetching**: Live YouTube stream extraction depends on network/API availability. In offline or cached environments, `youtube_stream_scanner.load_fallback_cache()` is used. The unified `evaluate_macro_stance()` function must handle both live raw text and fallback cache dictionary inputs deterministically without crashing.
2. **Dashboard Mirror Synchronization**: There are 4 identical dashboard HTML mirrors in the project (`al_sangmoo_dashboard.html`, `html_dashboards/01_R상무_통합_퀀트_대시보드.html`, etc.). All mirror files must maintain 100% byte-for-byte SHA256 parity if any frontend contract fields are updated.
3. **Database Schema Stability**: The SQLite schema in `al_sangmoo/infrastructure/persistence.py` contains `macro_history` and `trades` tables. Refactoring must preserve exact column compatibility (`us10y_val`, `us10y_status`, `vix_val`, `vix_status`, `wti_val`, `wti_status`, `macro_stance`, `msi_score`).

---

## 4. Conclusion & Proposed Refactoring Plan

### 4.1 MSI 2.0 Canonical Architecture in `al_sangmoo/domain/quant/macro.py`
Consolidate the canonical MSI 2.0 evaluation into `al_sangmoo/domain/quant/macro.py`:

```python
def evaluate_macro_stance(
    gauges: Optional[Dict[str, Any]] = None,
    defense_count: int = 0,
    buy_count: int = 0,
    matched_shocks: Optional[List[str]] = None,
    transcript: str = "",
    title: str = ""
) -> Dict[str, Any]:
    """
    Single Authoritative Source of Truth for Macro Stance Index 2.0 (MSI 2.0).
    - M_hard (max 60 pt): US 10Y Yield (25pt), VIX (15pt), WTI Oil (10pt), DXY (10pt)
    - M_nlp (max 25 pt): Spoken/Textual Sentiment Analysis & Fallback Heuristics
    - M_shock (max 15 pt): Geopolitical, Inflation, and Monetary Policy Shocks
    """
```
- Standardize gauge weight definitions:
  - **US 10Y Yield (Max 25pt)**: `>= 4.50`: 25.0pt, `>= 4.30`: 18.0pt, `>= 4.10`: 10.0pt, `>= 3.90`: 4.0pt, `< 3.90`: 0.0pt.
  - **VIX Fear (Max 15pt)**: `>= 25.0`: 15.0pt, `>= 20.0`: 10.0pt, `>= 16.0`: 5.0pt, `< 16.0`: 0.0pt.
  - **WTI Oil (Max 10pt)**: `>= 85.0`: 10.0pt, `>= 80.0`: 6.0pt, `>= 75.0`: 3.0pt, `< 75.0`: 0.0pt.
  - **DXY Dollar Index (Max 10pt)**: `>= 105.0`: 10.0pt, `>= 103.0`: 6.0pt, `>= 100.0`: 2.0pt, `< 100.0`: 0.0pt.
- Export both `evaluate_macro_stance` and legacy alias `calculate_msi_regime`.

### 4.2 Side-Effect Free Pipeline Architecture
1. **`generate_dashboard_feed.py`**:
   - Remove lines 688-693 (`db_manager.save_recommendation_matrix_record`, `db_manager.archive_daily_recommendations`).
   - `build_dashboard_data()` becomes a pure calculation & view model rendering function that writes only `dashboard_data.json` and chart files.
2. **`al_sangmoo_daily_bot.py`**:
   - Keep explicit persistence calls in `main()`:
     - `db_manager.save_macro_history_record(today_str, macro_climate, macro_gauges)`
     - `db_manager.save_recommendation_matrix_record(today_str, bull_picks, neutral_picks, bear_picks)`
     - `db_manager.archive_daily_recommendations(today_str, dual_consensus, strat1_exclusive, strat2_exclusive)`
   - Ensure clean single-flight database persistence without redundant double writes.

### 4.3 SSOT Indicator & Scoring Engine Consolidation
1. Replace `calculate_indicators` in `al_sangmoo_daily_bot.py` with `from al_sangmoo.domain.quant.ichimoku import calculate_ichimoku_indicators, compute_institutional_flow_indicators, evaluate_quant_score`.
2. Replace inline Ichimoku math in `generate_dashboard_feed.py` with domain imports.
3. Unify bull scoring threshold to standard 70pt cutoff across all modules.

---

## 5. Verification Method & Test Strategy

### 5.1 New Test Suite: `tools_and_tests/test_phase5_3_ssot_quant.py`
Construct a comprehensive 6-Tier test suite structured as follows:

| Test Tier | Focus Area | Verification Assertions |
| :--- | :--- | :--- |
| **Tier 1** | SSOT Indicator Math Parity | Verify `calculate_ichimoku_indicators()` outputs identical Tenkan, Kijun, SpanA, SpanB, Vol_Ratio across domain and callers for synthetic OHLCV data. |
| **Tier 2** | 3-Tier Quant Scoring Determinism | Verify that `al_sangmoo_daily_bot.scan_and_select_2x2x2` and `generate_dashboard_feed.build_dashboard_data` produce 100% identical scores and Tier 1/2/3 picks for identical input DataFrames. |
| **Tier 3** | MSI 2.0 Canonical Evaluation | Verify `evaluate_macro_stance()` produces identical numerical outputs, breakdowns, and stance tags when invoked with various gauge & NLP inputs across `macro.py`, `youtube_stream_scanner.py`, and `generate_dashboard_feed.py`. |
| **Tier 4** | CQRS & Pipeline Side-Effect Isolation | Mock SQLite persistence and execute `build_dashboard_data()`. Assert **0** database write methods (`save_recommendation_matrix_record`, `archive_daily_recommendations`, `add_portfolio_buy`, `record_portfolio_sell`) are invoked. |
| **Tier 5** | Static AST / Regex Deduplication Audit | Parse `generate_dashboard_feed.py` and `al_sangmoo_daily_bot.py` via `ast` and verify 0 inline duplicate definitions of `calculate_indicators`, `analyze_macro_regime_and_climate`, or custom Tenkan/Kijun math. |
| **Tier 6** | Full Platform Regression Suite | Execute `test_phase1_hardening.py`, `test_phase2_modular.py`, `test_phase4_execution.py`, `test_phase5_1_security.py`, `test_phase5_2_concurrency.py`, `test_global60_dual_strategy.py` and assert 100% Green. |

### 5.2 Independent Verification Commands
```powershell
# 1. Run all baseline test suites
python tools_and_tests/test_phase1_hardening.py
python tools_and_tests/test_phase2_modular.py
python tools_and_tests/test_phase4_execution.py
python tools_and_tests/test_phase5_1_security.py
python tools_and_tests/test_phase5_2_concurrency.py
python tools_and_tests/test_global60_dual_strategy.py

# 2. Run new Phase 5.3 SSOT Quant Verification Suite (once created)
python tools_and_tests/test_phase5_3_ssot_quant.py
```

### 5.3 Invalidation Conditions
- If `youtube_stream_scanner.py` and `macro.py` produce divergent MSI scores for identical gauge parameters.
- If calling `build_dashboard_data()` executes any SQL `INSERT` or `UPDATE` statements against SQLite.
- If `test_phase5_1_security.py` or `test_phase5_2_concurrency.py` fails after quant refactoring.
