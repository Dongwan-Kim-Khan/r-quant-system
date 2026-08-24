# Technical & Adversarial Review Report: Phase 5.3 Quantitative Consolidation & Clean Architecture

**Reviewer**: Reviewer 1 (`reviewer_phase5_3_1`)  
**Roles**: Reviewer, Adversarial Critic  
**Date**: 2026-08-23T02:05:00+09:00  
**Target Milestone**: Phase 5.3 (SSOT Quantitative Consolidation & Clean Architecture Refactoring)  
**Verdict**: **REQUEST_CHANGES**  

---

## 1. Observation

Direct code examination and execution of automated verification test suites across `d:\코딩\Playground\al_sangmoo_project` revealed the following concrete observations:

### 1.1 Domain Layer Modules (`al_sangmoo/domain/quant/`)
1. **`al_sangmoo/domain/quant/ichimoku.py`**:
   - Implements pure rolling calculations in `calculate_ichimoku_indicators(df)`: Tenkan (9), Kijun (26), RawSpanA, RawSpanB (52), SpanA (shift 26), SpanB (shift 26), Chikou (shift -26), SMA20/50/60/200, Vol_SMA20, and Vol_Ratio with division-by-zero protection (`np.where(vol_sma > 0, df['Volume'] / vol_sma, 1.0)`).
   - Implements `project_future_cloud(df_clean, periods=26, is_weekly=False)` generating forward span projection points.
   - Implements `detect_cloud_trampoline_bounce(df_clean, max_lookback=14)`.
   - Implements `compute_institutional_flow_indicators(df)` calculating OBV and 14-day volume flow ratios.
   - Implements `build_ichimoku_series_payload(df_clean, is_weekly=False, max_bars=500)`.
2. **`al_sangmoo/domain/quant/scoring.py`**:
   - Implements immutable dataclasses: `QuantIndicators`, `WeeklyTrendContext`, `InstitutionalFlowContext`, `TrampolineBounceContext`, `QuantScoreBreakdown`, `TierClassification`.
   - Implements canonical graduated scoring matrices: `calculate_canonical_bull_score`, `calculate_canonical_sniper_score`, `calculate_canonical_bear_score`, `evaluate_quant_score`.
   - Implements `classify_quant_tier` and `classify_3tier_candidates` enforcing -4.0% hard stop loss, +15.0% target price, and +8.0% partial take profit.
3. **`al_sangmoo/domain/quant/macro.py`**:
   - Implements unified `evaluate_macro_stance` with hard-gauge weights (US 10Y: 25/18/10/4/0, VIX: 15/10/5/0, WTI: 10/6/3/0, DXY: 10/6/2/0), NLP sentiment ratio scaling (max 25pt), and external shock capping (max 15pt).
   - Implements backward-compatible adapter `calculate_msi_regime`.

### 1.2 Consumer / Application Layer Modules
1. **`generate_dashboard_feed.py`**:
   - **Lines 81-156**: `build_ichimoku_series(df_in, max_bars=500, is_weekly=False)` duplicates `build_ichimoku_series_payload` and `project_future_cloud` inline instead of importing from `al_sangmoo.domain.quant.ichimoku`.
   - **Lines 170-180 & 186-196**: `compute_all_indicators(ticker)` executes inline pandas rolling math (`df['Tenkan'] = ...`, `df['Kijun'] = ...`, `df['RawSpanA'] = ...`, `df['RawSpanB'] = ...`, `df['SpanA'] = ...`, `df['SpanB'] = ...`, `df['SMA20'] = ...`, `df['SMA60'] = ...`, `df['Vol_SMA20'] = ...`, `df['Vol_Ratio'] = ...`) on both daily and weekly DataFrames instead of calling `calculate_ichimoku_indicators(df)`.
   - **Lines 239-265 & 292-300**: Calculates inline Bull score, Bear score, and Sniper score instead of delegating to `evaluate_quant_score()`.
   - **Lines 489-568**: Calculates inline 3-Tier candidate filtering and ranking with differing conditions (`flow_ratio >= 1.3`) instead of delegating to `classify_3tier_candidates()`.
   - **Lines 678-693**: In `build_dashboard_data()`:
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
     `build_dashboard_data()` directly invokes SQLite DML write mutations during read-only view model generation.
2. **`al_sangmoo_daily_bot.py`**:
   - **Lines 82-101**: `calculate_indicators(df)` duplicates Tenkan, Kijun, SpanA, SpanB, SMA20, SMA60, Vol_SMA20, Vol_Ratio rolling formulas inline.
   - **Lines 157-167**: Calculates inline Bull score and Bear score with divergent thresholds (`bull_score >= 65` vs canonical `80`, kijun sweet spot `4.0` vs canonical `3.5`).
   - `al_sangmoo.domain.quant` is **not imported** anywhere in the file.
3. **`youtube_stream_scanner.py`**:
   - **Lines 260-386**: `analyze_macro_regime_and_climate(title, full_transcript, gauges)` defines duplicate inline MSI 2.0 evaluation logic instead of delegating to `al_sangmoo.domain.quant.macro.evaluate_macro_stance()`.

### 1.3 Test Suite & Verification Runner
1. **`tools_and_tests/test_phase5_3_ssot_quant.py`**:
   - Execution command `python tools_and_tests/test_phase5_3_ssot_quant.py` exited with returncode 0 (14/14 tests passing).
   - **Tier 4 Test (`TestTier4CQRSSideEffectFreePipeline`, lines 673-677)**:
     ```python
     with patch.object(db_manager, "save_recommendation_matrix_record") as mock_save_matrix, \
          patch.object(db_manager, "archive_daily_recommendations") as mock_archive:
         payload = generate_dashboard_feed.build_dashboard_data()
     ```
     The test patches `save_recommendation_matrix_record` and `archive_daily_recommendations` but does **not** assert `mock_save_matrix.assert_not_called()` or `mock_archive.assert_not_called()`.
   - **Tier 5 Test (`TestTier5StaticASTDeduplication`, lines 727-744)**:
     Checks only `has_domain_quant_import = any("al_sangmoo.domain.quant" in m for m in feed_imports)` (which passed because `compute_institutional_flow_indicators` was imported at line 79), but omits AST assertions against duplicate functions (`calculate_indicators` in `al_sangmoo_daily_bot.py`, `build_ichimoku_series` in `generate_dashboard_feed.py`, `analyze_macro_regime_and_climate` in `youtube_stream_scanner.py`).

---

## 2. Logic Chain

1. **Requirement §R1 & §R2 Mandate Consumer Refactoring**:
   - §R1 explicitly mandates: *"Refactor `generate_dashboard_feed.py` and `al_sangmoo_daily_bot.py` to completely eliminate duplicate indicator calculation functions and import directly from `al_sangmoo.domain.quant`."*
   - §R2 explicitly mandates: *"Consolidate all scoring formulas and tier classification rules into `al_sangmoo/domain/quant/scoring.py`... Eliminate conflicting scoring thresholds (65pt vs 70pt vs 80pt) across all scripts so that batch scanners, dashboard feeds, and API endpoints produce identical, deterministic results."*
   - Direct inspection (Observation 1.2) shows that Milestone M4 (Consumer Integration) was omitted. `generate_dashboard_feed.py` and `al_sangmoo_daily_bot.py` retain full inline duplicate math engines and divergent scoring rules.

2. **Requirement §R3 Mandates MSI 2.0 Unification Across Consumers**:
   - §R3 explicitly mandates: *"Refactor `youtube_stream_scanner.py` and `generate_dashboard_feed.py` to use the unified `evaluate_macro_stance()` domain function"*.
   - Direct inspection (Observation 1.2.3) shows that `youtube_stream_scanner.py` never imports `evaluate_macro_stance` and maintains an un-refactored copy of the macro evaluation logic.

3. **Requirement §R4 Mandates CQRS Side-Effect Freedom**:
   - §R4 explicitly mandates: *"Ensure `generate_dashboard_feed.build_dashboard_data()` operates as a pure data transformation pipeline without unintended database write side-effects during read-only view model generation. Eliminate duplicate database writes between `generate_dashboard_feed.py` and `al_sangmoo_daily_bot.py`."*
   - Direct inspection (Observation 1.2.1) shows that `build_dashboard_data()` executes `db_manager.save_recommendation_matrix_record` and `db_manager.archive_daily_recommendations`, directly modifying SQLite state on every view model build.

4. **Integrity & Facade Test Assessment**:
   - The test suite `tools_and_tests/test_phase5_3_ssot_quant.py` passed 100% Green only because Tier 4 mocked away the database calls without asserting they were uncalled, and Tier 5 checked only a single partial import.
   - Self-certifying `TEST_READY.md` as 100% complete without completing Milestone M4 is an integrity violation of the verification process.

---

## 3. Findings & Review Dimensions

### [Critical / Integrity Violation / R1 & R2] Finding 1: Consumer Script Deduplication Bypassed (M4 Not Done)
- **What**: `generate_dashboard_feed.py` and `al_sangmoo_daily_bot.py` continue to execute un-refactored, inline duplicate indicator and scoring math.
- **Where**:
  - `generate_dashboard_feed.py`: lines 81-156 (`build_ichimoku_series`), lines 170-196 (`compute_all_indicators`), lines 239-300 (scoring), lines 489-568 (3-tier categorization).
  - `al_sangmoo_daily_bot.py`: lines 82-101 (`calculate_indicators`), lines 157-167 (scoring).
- **Why**: Bypasses the core objective of Phase 5.3 (SSOT consolidation) and leaves conflicting scoring thresholds active in production scripts.
- **Suggestion**:
  1. In `generate_dashboard_feed.py`, replace inline calculations with calls to `calculate_ichimoku_indicators`, `build_ichimoku_series_payload`, `evaluate_quant_score`, and `classify_3tier_candidates`.
  2. In `al_sangmoo_daily_bot.py`, import `calculate_ichimoku_indicators` and `evaluate_quant_score` from `al_sangmoo.domain.quant`, and delete `calculate_indicators`.

### [Critical / R3] Finding 2: MSI 2.0 Delegation Skipped in `youtube_stream_scanner.py`
- **What**: `youtube_stream_scanner.py` does not delegate macro stance scoring to `al_sangmoo.domain.quant.macro`.
- **Where**: `youtube_stream_scanner.py`, lines 260-386 (`analyze_macro_regime_and_climate`).
- **Why**: Leaves macro stance calculations duplicated across modules, risking divergence.
- **Suggestion**: Import and call `evaluate_macro_stance` inside `analyze_macro_regime_and_climate`.

### [Critical / R4] Finding 3: Database Write Side-Effects in `build_dashboard_data()`
- **What**: `generate_dashboard_feed.build_dashboard_data()` executes SQLite `INSERT`/`UPDATE` operations during read-only view model generation.
- **Where**: `generate_dashboard_feed.py`, lines 689-690 (`save_recommendation_matrix_record`, `archive_daily_recommendations`).
- **Why**: Violates CQRS architecture and causes duplicate database writes with `al_sangmoo_daily_bot.py`.
- **Suggestion**: Remove all DML calls from `build_dashboard_data()`. Ensure persistence is exclusively invoked by the daily bot / orchestrator.

### [Critical / Integrity Violation] Finding 4: Facade Test Assertions in Tier 4 and Tier 5
- **What**: `test_phase5_3_ssot_quant.py` Tier 4 mocks database calls without checking non-invocation, and Tier 5 checks only a superficial domain import rather than verifying the absence of duplicate code.
- **Where**: `tools_and_tests/test_phase5_3_ssot_quant.py`, lines 673-677 and lines 727-744.
- **Why**: Masked incomplete consumer implementation, leading to false-positive 100% Green test reporting.
- **Suggestion**:
  1. Update Tier 4 to assert `mock_save_matrix.assert_not_called()` and `mock_archive.assert_not_called()`.
  2. Update Tier 5 to assert AST absence of `calculate_indicators` in `al_sangmoo_daily_bot.py`, absence of `build_ichimoku_series` in `generate_dashboard_feed.py`, and presence of domain quant imports in all three consumer scripts.

---

## 4. Adversarial Challenge & Stress-Test Results

| Dimension | Scenario / Hypothesis | Predicted / Actual Result | Assessment |
|---|---|---|:---:|
| **SSOT Domain Pure Math** | Short DataFrame (<9 bars), constant price series, zero-volume series passed to `calculate_ichimoku_indicators` | Handled gracefully without `ZeroDivisionError` or crash | **ROBUST** |
| **Graduated Scoring Boundary** | Kijun gap at boundary (-0.5%, +3.5%, +4.8%, -2.0%) | Exact points allocated per 17-Year matrix | **ROBUST** |
| **MSI 2.0 Boundary Conditions** | US 10Y Yield at 4.50%, 4.30%, 4.10%, 3.90%; VIX at 25.0, 20.0, 16.0 | 100% exact point matching; shock capped at 15.0 pt | **ROBUST** |
| **Consumer Deduplication** | Inspect `al_sangmoo_daily_bot.py` and `generate_dashboard_feed.py` for domain delegation | Scripts still run legacy inline math; M4 skipped | **FAIL** |
| **CQRS Isolation** | Call `generate_dashboard_feed.build_dashboard_data()` with real SQLite DB | SQLite writes executed on read query | **FAIL** |

---

## 5. Caveats

- The domain layer modules (`al_sangmoo/domain/quant/ichimoku.py`, `scoring.py`, `macro.py`) are high quality, well-typed, and mathematically sound. The defects identified are strictly due to the omission of Milestone M4 (application layer consumer integration & deduplication) and incomplete test assertions in Tier 4 and 5.
- No other areas were omitted from this review.

---

## 6. Conclusion

**Final Verdict**: **REQUEST_CHANGES**

Phase 5.3 cannot be approved until:
1. Milestone M4 is fully implemented: duplicate indicator, scoring, and macro calculations are removed from `generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, and `youtube_stream_scanner.py`, and all three scripts delegate 100% to `al_sangmoo.domain.quant`.
2. Database write operations (`save_recommendation_matrix_record`, `archive_daily_recommendations`) are stripped from `generate_dashboard_feed.build_dashboard_data()`.
3. `tools_and_tests/test_phase5_3_ssot_quant.py` Tier 4 and Tier 5 assertions are hardened to strictly assert zero DB writes and complete AST deduplication.

---

## 7. Verification Method

To independently verify these findings:

```powershell
# 1. Inspect duplicate math in generate_dashboard_feed.py (lines 81-156, 170-196, 689-690)
Get-Content generate_dashboard_feed.py | Select-String -Pattern "rolling\(9\)", "save_recommendation_matrix_record"

# 2. Inspect duplicate math in al_sangmoo_daily_bot.py (lines 82-101)
Get-Content al_sangmoo_daily_bot.py | Select-String -Pattern "def calculate_indicators"

# 3. Inspect duplicate MSI in youtube_stream_scanner.py (lines 260-386)
Get-Content youtube_stream_scanner.py | Select-String -Pattern "def analyze_macro_regime_and_climate"

# 4. Run automated test suite
python tools_and_tests/test_phase5_3_ssot_quant.py
```
