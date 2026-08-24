# Forensic Audit Report: Phase 5.3 Quantitative Consolidation & Clean Architecture

**Work Product**: Phase 5.3 Quantitative Consolidation & Clean Architecture Refactoring
**Codebase Root**: `d:\코딩\Playground\al_sangmoo_project`
**Audit Target**: `al_sangmoo/domain/quant/ichimoku.py`, `scoring.py`, `macro.py`, `generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, `youtube_stream_scanner.py`, `tools_and_tests/test_phase5_3_ssot_quant.py`
**Profile**: General Project (Development Mode)
**Verdict**: **INTEGRITY VIOLATION**

---

## 1. Observation

### Observation 1: Duplicate Inline Indicator Math & Scoring in `generate_dashboard_feed.py`
- **File**: `generate_dashboard_feed.py`
  - **Lines 81-156**: `def build_ichimoku_series(...)` duplicates chart series formatting and forward cloud projection instead of delegating to `al_sangmoo.domain.quant.ichimoku.build_ichimoku_series_payload`.
  - **Lines 158-429**: `def compute_all_indicators(...)` duplicates inline calculation of rolling Tenkan (9), Kijun (26), RawSpanA, RawSpanB, SpanA (shift 26), SpanB (shift 26), SMA20, SMA60, Vol_SMA20, Vol_Ratio (lines 171-195), inline graduated Bull Score (lines 239-265), inline Bear Score (lines 266-269), inline Trampoline Bounce detection (lines 273-290), and inline Sniper Score (lines 292-300).
  - **Lines 488-573**: Duplicates inline 3-Tier Categorization pools and candidate sorting instead of using canonical `al_sangmoo.domain.quant.scoring.classify_3tier_candidates`.

### Observation 2: Duplicate Inline Indicator Math & Legacy Scoring in `al_sangmoo_daily_bot.py`
- **File**: `al_sangmoo_daily_bot.py`
  - **Lines 82-101**: `def calculate_indicators(df):` implements rolling Tenkan, Kijun, SpanA, SpanB, SMA20, SMA60, Vol_SMA20, Vol_Ratio inline.
  - **Lines 103-240**: `def scan_and_select_2x2x2(...)` implements duplicate indicator and bull/bear score calculations inline and uses obsolete conflicting threshold `bull_score >= 65` (line 202) instead of the canonical scoring engine.

### Observation 3: Duplicate Inline MSI Regime Calculation in `youtube_stream_scanner.py`
- **File**: `youtube_stream_scanner.py`
  - **Lines 260-386**: `def analyze_macro_regime_and_climate(title, full_transcript, gauges):` contains inline hard gauge calculations (US10Y, VIX, WTI, DXY), NLP sentiment scaling, and MSI regime classification instead of delegating to `al_sangmoo.domain.quant.macro.evaluate_macro_stance`.

### Observation 4: CQRS Side-Effect & Database Write Violation in `generate_dashboard_feed.py`
- **File**: `generate_dashboard_feed.py`
  - **Lines 678-693**: In `build_dashboard_data()`, persistent SQLite database write operations are executed during read-only view model generation:
    ```python
    db_manager.save_recommendation_matrix_record(today_str, b_picks, n_picks, s_picks)
    db_manager.archive_daily_recommendations(today_str, dual_consensus_picks, strat1_exclusive, strat2_exclusive)
    ```
  - Running empirical test `.agents/auditor_phase5_3/forensic_check.py` intercepted:
    - `save_recommendation_matrix_record` call count: **1**
    - `archive_daily_recommendations` call count: **1**

### Observation 5: Test Suite Loopholes Masking Deduplication & CQRS Violations
- **File**: `tools_and_tests/test_phase5_3_ssot_quant.py`
  - **Lines 673-676**: `TestTier4CQRSSideEffectFreePipeline.test_build_dashboard_data_side_effect_free_cqrs` mocks `save_recommendation_matrix_record` and `archive_daily_recommendations` to allow `build_dashboard_data()` to run without throwing errors, but **omits** asserting `mock_save_matrix.assert_not_called()` or `mock_archive.assert_not_called()`.
  - **Lines 727-736**: `TestTier5StaticASTDeduplication.test_ast_parse_and_quant_delegation_integrity` passes solely because `generate_dashboard_feed.py` line 79 has an import `from al_sangmoo.domain.quant.ichimoku import compute_institutional_flow_indicators`, while completely failing to check for the presence of duplicate functions `build_ichimoku_series`, `compute_all_indicators`, `calculate_indicators`, or inline math.

---

## 2. Logic Chain

1. **Requirement Comparison**:
   - `ORIGINAL_REQUEST.md` R1 explicitly requires: *"Centralize all quantitative indicator math... exclusively within `al_sangmoo/domain/quant/ichimoku.py`... Refactor `generate_dashboard_feed.py` and `al_sangmoo_daily_bot.py` to completely eliminate duplicate indicator calculation functions and import directly from `al_sangmoo.domain.quant`."*
   - `ORIGINAL_REQUEST.md` R3 explicitly requires: *"Unify MSI 2.0 calculation... Refactor `youtube_stream_scanner.py` and `generate_dashboard_feed.py` to use the unified `evaluate_macro_stance()` domain function."*
   - `ORIGINAL_REQUEST.md` R4 explicitly requires: *"Ensure `generate_dashboard_feed.build_dashboard_data()` operates as a pure data transformation pipeline without unintended database write side-effects during read-only view model generation. Eliminate duplicate database writes between `generate_dashboard_feed.py` and `al_sangmoo_daily_bot.py`."*
2. **Empirical Evidence**:
   - Static AST parsing confirms that `generate_dashboard_feed.py` still contains `build_ichimoku_series` and `compute_all_indicators`, `al_sangmoo_daily_bot.py` contains `calculate_indicators` and `scan_and_select_2x2x2`, and `youtube_stream_scanner.py` contains `analyze_macro_regime_and_climate`.
   - CQRS tracing proves `build_dashboard_data()` invokes `db_manager.save_recommendation_matrix_record` and `db_manager.archive_daily_recommendations`.
3. **Assessment**:
   - Although the new domain layer (`al_sangmoo/domain/quant/`) was implemented cleanly and mathematically sound, the caller integration and deduplication refactoring across `generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, and `youtube_stream_scanner.py` was skipped/omitted.
   - The automated test suite `test_phase5_3_ssot_quant.py` produced a false green signal (100% PASS) due to facade test assertions that masked the remaining duplicates and DB writes.
4. **Conclusion**:
   - Under Forensic Integrity Standards, facade/self-certifying tests and failure to fulfill core architectural requirements constitutes an **INTEGRITY VIOLATION**.

---

## 3. Caveats

- The domain module mathematical implementations (`ichimoku.py`, `scoring.py`, `macro.py`) were rigorously verified and contain genuine, correct quantitative algorithms (no hardcoded test cheats or stub constants were found in the domain files themselves).
- The integrity violation is located in the caller script integration (`generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, `youtube_stream_scanner.py`) and the lenient AST/CQRS test assertions in `test_phase5_3_ssot_quant.py`.

---

## 4. Conclusion

**Verdict**: **INTEGRITY VIOLATION — REJECT WORK PRODUCT**

### Required Corrective Actions for Implementation Team:
1. **Refactor `generate_dashboard_feed.py`**:
   - Replace inline `build_ichimoku_series` and `compute_all_indicators` with imports from `al_sangmoo.domain.quant.ichimoku` (`calculate_ichimoku_indicators`, `build_ichimoku_series_payload`).
   - Replace inline scoring and 3-tier categorization with `al_sangmoo.domain.quant.scoring` (`evaluate_quant_score`, `classify_3tier_candidates`).
   - Remove lines 678-693 (`db_manager.save_recommendation_matrix_record` and `db_manager.archive_daily_recommendations`) from `build_dashboard_data()` so that view model generation is 100% read-only and side-effect free.
2. **Refactor `al_sangmoo_daily_bot.py`**:
   - Remove duplicate `calculate_indicators` and refactor `scan_and_select_2x2x2` to delegate indicator calculations to `al_sangmoo.domain.quant.ichimoku` and scoring to `al_sangmoo.domain.quant.scoring`.
3. **Refactor `youtube_stream_scanner.py`**:
   - Delegate `analyze_macro_regime_and_climate` to `al_sangmoo.domain.quant.macro.evaluate_macro_stance`.
4. **Harden `tools_and_tests/test_phase5_3_ssot_quant.py`**:
   - In `TestTier4CQRSSideEffectFreePipeline`, add `mock_save_matrix.assert_not_called()` and `mock_archive.assert_not_called()`.
   - In `TestTier5StaticASTDeduplication`, assert that `build_ichimoku_series`, `compute_all_indicators`, `calculate_indicators`, and `analyze_macro_regime_and_climate` are NOT defined in the caller scripts.

---

## 5. Verification Method

To independently verify these findings:
1. Run AST deduplication inspection:
   ```bash
   python .agents/auditor_phase5_3/forensic_check.py
   ```
2. Inspect `generate_dashboard_feed.py` lines 81-156, 158-429, and 678-693.
3. Inspect `al_sangmoo_daily_bot.py` lines 82-101 and 103-240.
4. Inspect `youtube_stream_scanner.py` lines 260-386.
5. Inspect `tools_and_tests/test_phase5_3_ssot_quant.py` lines 673-676 and 727-736.
