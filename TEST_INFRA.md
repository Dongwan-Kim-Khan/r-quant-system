# E2E Test Infra: Phase 5.3 Quantitative Consolidation & SSOT Architecture

## Test Philosophy
- Opaque-box, requirement-driven, and invariant-verifying.
- Verification Target: `tools_and_tests/test_phase5_3_ssot_quant.py`.
- Methodology: Category-Partition + BVA + Pairwise + Workload + AST Static Analysis + Full Regression.

## Feature Inventory & Test Mapping
| # | Feature | Requirement Source | Tier 1 (Math) | Tier 2 (Scoring) | Tier 3 (MSI) | Tier 4 (CQRS) | Tier 5 (AST) | Tier 6 (Regression) |
|---|---------|-------------------|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | SSOT Indicator Math Engine | ORIGINAL_REQUEST §R1 | ✓ | | | | ✓ | ✓ |
| 2 | Pure Chart Series Builder | Survey 1 | ✓ | | | | | ✓ |
| 3 | Canonical Scoring Matrix | ORIGINAL_REQUEST §R2 | | ✓ | | | ✓ | ✓ |
| 4 | Canonical 3-Tier Classification | ORIGINAL_REQUEST §R2 | | ✓ | | | | ✓ |
| 5 | Hard Stop & Target Rules | Survey 2 | | ✓ | | | | ✓ |
| 6 | MSI 2.0 Parameter Unification | ORIGINAL_REQUEST §R3 | | | ✓ | | ✓ | ✓ |
| 7 | Pure Macro Stance Evaluation | ORIGINAL_REQUEST §R3 | | | ✓ | | | ✓ |
| 8 | Side-Effect Free Feed Pipeline | ORIGINAL_REQUEST §R4 | | | | ✓ | ✓ | ✓ |
| 9 | Application Deduplication | ORIGINAL_REQUEST §R1, R3 | | | | | ✓ | ✓ |
| 10 | Regression Safety | ORIGINAL_REQUEST §Acceptance | | | | | | ✓ |

## Test Architecture
- **Test Runner**: `python tools_and_tests/test_phase5_3_ssot_quant.py`
- **Pass Semantics**: All assertions pass, 0 errors, exit code 0.
- **Tiers Breakdown**:
  - **Tier 1 (Indicator Math Parity)**: Synthesize known OHLCV series. Assert identical Tenkan, Kijun, SpanA, SpanB, Chikou, SMA20/50/60/200, Vol_Ratio. Check division by zero and NaN edge cases.
  - **Tier 2 (Scoring & Classification Determinism)**: Test Bull, Sniper, Bear scores with known price gaps. Verify Tier 1, Tier 2, Tier 3 predicates (Macro Tailwind, Stealth Accum, Kijun +/-3%, Trampoline bounce, -4% stop price).
  - **Tier 3 (MSI 2.0 Unification)**: Test boundary conditions for US 10Y (3.89%, 3.90%, 4.10%, 4.30%, 4.50%), VIX (15.9, 16.0, 20.0, 25.0), WTI, DXY. Assert `evaluate_macro_stance` and `youtube_stream_scanner.py` yield 100% identical points and stances.
  - **Tier 4 (CQRS & Side-Effect Free Pipeline)**: Mock SQLite connection. Execute `build_dashboard_data()`. Assert zero DML writes (`save_recommendation_matrix_record`, `archive_daily_recommendations`) occur.
  - **Tier 5 (AST / Regex Static Deduplication)**: Parse `generate_dashboard_feed.py` and `al_sangmoo_daily_bot.py` using `ast`. Assert 0 duplicate indicator definitions or inline rolling math.
  - **Tier 6 (Platform Regression Suite)**: Run `test_phase1_hardening.py`, `test_phase2_modular.py`, `test_phase4_execution.py`, `test_phase5_1_security.py`, `test_phase5_2_concurrency.py`, `test_global60_dual_strategy.py`.
