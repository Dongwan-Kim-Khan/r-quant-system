## 2026-08-23T02:11:36+09:00

You are the Remediation Worker for Phase 5.3 Quantitative Consolidation & Clean Architecture Refactoring.

Your working directory is: d:\코딩\Playground\al_sangmoo_project\.agents\worker_remediation_m4
Codebase root: d:\코딩\Playground\al_sangmoo_project
Original Request: d:\코딩\Playground\al_sangmoo_project\.agents\ORIGINAL_REQUEST.md
Project Spec: d:\코딩\Playground\al_sangmoo_project\PROJECT.md
Auditor Report: d:\코딩\Playground\al_sangmoo_project\.agents\auditor_phase5_3\handoff.md
Feed Fix Plan: d:\코딩\Playground\al_sangmoo_project\.agents\explorer_fix_feed_1\handoff.md
Bot & Macro Fix Plan: d:\코딩\Playground\al_sangmoo_project\.agents\explorer_fix_bot_2\handoff.md
Test Hardening Plan: d:\코딩\Playground\al_sangmoo_project\.agents\explorer_fix_test_3\handoff.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

TASK:
Implement Milestone M4 (Consumer Caller Deduplication & CQRS Side-Effect Elimination) and Test Hardening:

1. **`generate_dashboard_feed.py`**:
   - Eliminate `build_ichimoku_series` completely; delegate to `al_sangmoo.domain.quant.ichimoku.build_ichimoku_series_payload`.
   - Refactor `compute_all_indicators` to delegate daily/weekly calculations, trampoline bounce detection, institutional flow metrics, and 17-year quant scoring to `calculate_ichimoku_indicators`, `detect_cloud_trampoline_bounce`, `compute_institutional_flow_indicators`, `evaluate_quant_score` from `al_sangmoo.domain.quant`.
   - Refactor candidate classification loop in `build_dashboard_data()` to delegate to `classify_3tier_candidates`.
   - Completely remove lines 678-693 (`db_manager.save_recommendation_matrix_record` and `db_manager.archive_daily_recommendations`) from `build_dashboard_data()` so that view model generation is 100% read-only and side-effect free.
   - Maintain 100% backward compatibility of `dashboard_data.json` keys and chart payloads.

2. **`al_sangmoo_daily_bot.py`**:
   - Remove duplicate `calculate_indicators` function.
   - Refactor `scan_and_select_2x2x2` to import and delegate to `calculate_ichimoku_indicators`, `evaluate_quant_score`, and `classify_3tier_candidates` from `al_sangmoo.domain.quant`.
   - In `main()`, retain explicit SQLite persistence as the single command entry point.

3. **`youtube_stream_scanner.py`**:
   - Refactor `analyze_macro_regime_and_climate` to delegate directly to `al_sangmoo.domain.quant.macro.evaluate_macro_stance`.

4. **`tools_and_tests/test_phase5_3_ssot_quant.py`**:
   - Harden Tier 4: Add strict mock assertions (`mock_save_matrix.assert_not_called()`, `mock_archive.assert_not_called()`) and unmocked real DB zero-mutation assertion during `build_dashboard_data()`.
   - Harden Tier 5: Add strict AST analysis checking that duplicate functions (`build_ichimoku_series`, `compute_all_indicators`, `calculate_indicators`, `analyze_macro_regime_and_climate`) do NOT exist in caller scripts and that caller scripts genuinely import and call `al_sangmoo.domain.quant`.

5. **Run all tests and regression suites**:
   - `python tools_and_tests/test_phase5_3_ssot_quant.py`
   - `python tools_and_tests/test_phase1_hardening.py`
   - `python tools_and_tests/test_phase2_modular.py`
   - `python tools_and_tests/test_phase4_execution.py`
   - `python tools_and_tests/test_phase5_1_security.py`
   - `python tools_and_tests/test_phase5_2_concurrency.py`
   - `python tools_and_tests/test_global60_dual_strategy.py`
