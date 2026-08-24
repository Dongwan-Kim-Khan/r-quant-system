# Phase 5.3 Technical Review & Adversarial Critic Report

## Review Summary

- **Verdict**: **REQUEST_CHANGES**
- **Reviewed Scope**:
  - `al_sangmoo/domain/quant/ichimoku.py`
  - `al_sangmoo/domain/quant/scoring.py`
  - `al_sangmoo/domain/quant/macro.py`
  - `al_sangmoo/domain/quant/__init__.py`
  - `generate_dashboard_feed.py`
  - `al_sangmoo_daily_bot.py`
  - `youtube_stream_scanner.py`
  - `tools_and_tests/test_phase5_3_ssot_quant.py`
- **Primary Finding**: **INTEGRITY VIOLATION / INCOMPLETE SSOT MIGRATION & CQRS WRITE LEAKAGE**.
  While the domain math modules in `al_sangmoo/domain/quant/` were written with excellent mathematical rigor and pass unit tests, the operational scripts (`generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, `youtube_stream_scanner.py`) were never actually refactored to consume them. They retain 100% of their legacy inline duplicate indicator calculations, conflicting scoring thresholds, and CQRS database write side-effects.

---

## 1. Observation

### Observation 1.1: Inline Math Duplication in `generate_dashboard_feed.py`
- **File**: `generate_dashboard_feed.py`
- **Lines 81-156**: Defines inline `build_ichimoku_series(df_in, max_bars=500, is_weekly=False)` instead of importing `build_ichimoku_series_payload` from `al_sangmoo.domain.quant.ichimoku`.
- **Lines 171-195**: Re-computes Tenkan, Kijun, RawSpanA, RawSpanB, SpanA, SpanB, SMA20, SMA60, Vol_SMA20, Vol_Ratio inline inside `compute_all_indicators()`:
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
- **Lines 239-300**: Calculates `bull_score`, `bear_score`, `trampoline_detected`, and `sniper_score` using inline branch conditions instead of `evaluate_quant_score()` or `calculate_canonical_bull_score()` from `al_sangmoo.domain.quant.scoring`.
- **Lines 488-558**: Re-implements candidate categorization loop inline instead of delegating to `classify_3tier_candidates()`.

### Observation 1.2: Inline Math Duplication in `al_sangmoo_daily_bot.py`
- **File**: `al_sangmoo_daily_bot.py`
- **Lines 82-101**: Implements `calculate_indicators(df)` inline:
  ```python
  def calculate_indicators(df):
      df = df.dropna(subset=['Close', 'High', 'Low', 'Volume']).copy()
      high_9 = df['High'].rolling(window=9).max()
      low_9 = df['Low'].rolling(window=9).min()
      df['Tenkan'] = (high_9 + low_9) / 2
      ...
  ```
- **Lines 157-168**: Implements inline `bull_score`, `bear_score`, and `neutral_score`.
- **Line 202**: Uses unaligned `bull_score >= 65` threshold rather than the canonical 80pt / 70pt scoring engine.

### Observation 1.3: Inline Macro Calculation in `youtube_stream_scanner.py`
- **File**: `youtube_stream_scanner.py`
- **Lines 260-386**: Implements full 126-line inline `analyze_macro_regime_and_climate(title, full_transcript, gauges)` containing duplicate hard gauge weights and NLP logic, completely ignoring `al_sangmoo.domain.quant.macro.evaluate_macro_stance`.

### Observation 1.4: CQRS Database Write Violation in `generate_dashboard_feed.py`
- **File**: `generate_dashboard_feed.py`
- **Lines 678-693**: In `build_dashboard_data()`, the following DML writes are executed:
  ```python
  if dual_consensus_picks or strat1_exclusive or strat2_exclusive:
      ...
      try:
          db_manager.save_recommendation_matrix_record(today_str, b_picks, n_picks, s_picks)
          db_manager.archive_daily_recommendations(today_str, dual_consensus_picks, strat1_exclusive, strat2_exclusive)
      except Exception as e:
          print(f"[DB Archiving Warning] {e}")
  ```

### Observation 1.5: Test Masking in `tools_and_tests/test_phase5_3_ssot_quant.py`
- **File**: `tools_and_tests/test_phase5_3_ssot_quant.py`
- **Lines 673-678**:
  ```python
  with patch.object(db_manager, "save_recommendation_matrix_record") as mock_save_matrix, \
       patch.object(db_manager, "archive_daily_recommendations") as mock_archive:
      payload = generate_dashboard_feed.build_dashboard_data()
  ```
  `mock_save_matrix.assert_not_called()` and `mock_archive.assert_not_called()` were omitted. If asserted, the test would have failed because `build_dashboard_data()` invoked both.
- **Lines 727-736**:
  AST test only checked if `generate_dashboard_feed.py` contained *any* import from `al_sangmoo.domain.quant` (satisfied by a single helper import), ignoring all inline duplicate functions.

---

## 2. Logic Chain

1. **Premise 1 (Requirements R1-R4 in ORIGINAL_REQUEST.md & PROJECT.md)**:
   - R1 mandates Single Source of Truth indicator engine consolidation into `al_sangmoo/domain/quant/ichimoku.py` and complete elimination of duplicate indicator calculation functions in `generate_dashboard_feed.py` and `al_sangmoo_daily_bot.py`.
   - R2 mandates consolidation of all scoring formulas into `al_sangmoo/domain/quant/scoring.py` and elimination of conflicting scoring thresholds (e.g. 65pt vs 80pt).
   - R3 mandates MSI 2.0 logic unification in `al_sangmoo/domain/quant/macro.py` with `youtube_stream_scanner.py` delegating to `evaluate_macro_stance()`.
   - R4 mandates that `generate_dashboard_feed.build_dashboard_data()` operates as a pure data transformation pipeline without unintended database write side-effects.

2. **Step 1 (Domain Modules Quality Assessment)**:
   - Directly verified that `al_sangmoo/domain/quant/ichimoku.py`, `scoring.py`, and `macro.py` are robust, well-typed, and mathematically sound.

3. **Step 2 (Caller Script Verification)**:
   - Verified via file inspection (Observations 1.1, 1.2, 1.3) that `generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, and `youtube_stream_scanner.py` still contain extensive inline implementations of the exact formulas they were supposed to delegate to `al_sangmoo.domain.quant`.

4. **Step 3 (CQRS Purity Verification)**:
   - Verified via file inspection (Observation 1.4) that `build_dashboard_data()` executes `db_manager.save_recommendation_matrix_record` and `db_manager.archive_daily_recommendations`.

5. **Step 4 (Test Verification & Integrity Assessment)**:
   - `python tools_and_tests/test_phase5_3_ssot_quant.py` passes 14/14 tests, but inspection of the test code (Observation 1.5) reveals that Tier 4 and Tier 5 were crafted to bypass verifying the callers' migration and CQRS zero-write assertions.

6. **Conclusion**:
   - The implementation is incomplete and represents an architectural facade where the domain layer was built but the consumer codebase was not refactored. The verdict must be **REQUEST_CHANGES**.

---

## 3. Findings

### [Critical] Finding 1: Incomplete SSOT Migration & Triple Math Duplication
- **What**: `generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, and `youtube_stream_scanner.py` retain redundant inline math implementations instead of delegating to `al_sangmoo.domain.quant`.
- **Where**:
  - `generate_dashboard_feed.py` (lines 81-156, lines 171-195, lines 239-300, lines 488-558)
  - `al_sangmoo_daily_bot.py` (lines 82-101, lines 157-168, lines 202-239)
  - `youtube_stream_scanner.py` (lines 260-386)
- **Why**: Violates R1, R2, R3. Creates divergence risks where scanners, bots, and feeds calculate conflicting values and thresholds (e.g. 65pt in bot vs 80pt in feed).
- **Suggestion**:
  - In `generate_dashboard_feed.py`: Use `build_ichimoku_series_payload`, `calculate_ichimoku_indicators`, `evaluate_quant_score`, and `classify_3tier_candidates`.
  - In `al_sangmoo_daily_bot.py`: Remove `calculate_indicators` and import `calculate_ichimoku_indicators`; use `evaluate_quant_score` or `classify_3tier_candidates`.
  - In `youtube_stream_scanner.py`: Replace `analyze_macro_regime_and_climate` body with a direct delegation to `evaluate_macro_stance`.

### [Critical] Finding 2: CQRS Architectural Violation in `build_dashboard_data()`
- **What**: View model generation in `generate_dashboard_feed.py` executes database mutations (`save_recommendation_matrix_record`, `archive_daily_recommendations`).
- **Where**: `generate_dashboard_feed.py` (lines 688-693).
- **Why**: Violates R4. A read query / view model builder must be pure and free of write side effects to avoid race conditions and transaction locks during read operations.
- **Suggestion**: Remove lines 688-693 from `generate_dashboard_feed.py`. Explicitly ensure database persistence occurs only in command workflows (e.g. `al_sangmoo_daily_bot.py` or dedicated scan commands).

### [Major] Finding 3: Inadequate Test Assertions in Tier 4 and Tier 5
- **What**: `test_phase5_3_ssot_quant.py` did not assert `assert_not_called()` on DML mocks in Tier 4, and Tier 5 AST analysis only checked for the existence of an import without asserting the removal of duplicate indicator functions.
- **Where**: `tools_and_tests/test_phase5_3_ssot_quant.py` (lines 673-678, lines 727-744).
- **Why**: Allowed regression and incomplete implementation to pass CI silently.
- **Suggestion**: Add `mock_save_matrix.assert_not_called()` and `mock_archive.assert_not_called()` in Tier 4; enhance AST checks to assert that `calculate_indicators` and `build_ichimoku_series` do not exist in caller scripts.

---

## 4. Adversarial Challenges

### Challenge 1: Scoring Divergence Across Bot and Dashboard
- **Assumption Challenged**: That the platform provides a unified recommendation engine.
- **Attack Scenario**: When running on volatile market data, `al_sangmoo_daily_bot.py` picks stocks with `bull_score >= 65` and ranks them via custom distance to Kijun-sen, while `generate_dashboard_feed.py` evaluates with `bull_score >= 80` and `flow_ratio >= 1.3`, and `scoring.py` evaluates with `flow_ratio >= 1.2`.
- **Blast Radius**: Daily email reports will recommend different tickers than the dashboard live feed on the same day.
- **Mitigation**: Standardize all recommendation pipelines to call `al_sangmoo.domain.quant.scoring.classify_3tier_candidates()`.

### Challenge 2: SQLite Write Lock Contention on Read Feeds
- **Assumption Challenged**: That `/api/scan_now` or feed generation does not lock SQLite.
- **Attack Scenario**: Concurrent API requests triggering `build_dashboard_data()` invoke `archive_daily_recommendations()` while portfolio updates or order executions occur, creating database write lock contention.
- **Blast Radius**: API latency spikes and potential `sqlite3.OperationalError: database is locked`.
- **Mitigation**: Enforce 100% CQRS purity by removing write calls from `build_dashboard_data()`.

---

## 5. Caveats
- No caveats. All domain modules, caller scripts, test suites, and regression runners were directly inspected and verified.

---

## 6. Conclusion
- **Verdict**: **REQUEST_CHANGES**
- The domain modules (`ichimoku.py`, `scoring.py`, `macro.py`) are mathematically sound and properly designed.
- However, Phase 5.3 cannot be approved until:
  1. `generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, and `youtube_stream_scanner.py` are refactored to eliminate duplicate inline math and delegate 100% to `al_sangmoo.domain.quant`.
  2. `build_dashboard_data()` is made strictly pure (database write calls removed).
  3. `tools_and_tests/test_phase5_3_ssot_quant.py` is updated with strict assertions for zero-writes and AST deduplication.

---

## 7. Verification Method

To independently verify these findings:
1. Check inline math in callers:
   - `grep -n "def calculate_indicators" al_sangmoo_daily_bot.py` -> line 82
   - `grep -n "def build_ichimoku_series" generate_dashboard_feed.py` -> line 81
   - `grep -n "def analyze_macro_regime_and_climate" youtube_stream_scanner.py` -> line 260
2. Check CQRS write calls inside `build_dashboard_data()`:
   - `grep -n "save_recommendation_matrix_record" generate_dashboard_feed.py` -> line 689
3. Invalidate / Fix Condition:
   - All caller scripts import and delegate to `al_sangmoo.domain.quant`.
   - `build_dashboard_data()` contains 0 database write calls.
   - `python tools_and_tests/test_phase5_3_ssot_quant.py` passes with strict non-mocked CQRS and AST deduplication assertions.
