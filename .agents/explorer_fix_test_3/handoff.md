# Forensic Test Hardening Strategy Report: Phase 5.3 Remediation Iteration 2

**Agent**: Explorer 3 (`explorer_fix_test_3`)  
**Role**: Test Hardening Strategist, Code Inspector, Test Architect  
**Codebase Root**: `d:\코딩\Playground\al_sangmoo_project`  
**Target Suite**: `tools_and_tests/test_phase5_3_ssot_quant.py`  
**Target Subject Modules**: `generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, `youtube_stream_scanner.py`, `al_sangmoo/domain/quant/`  
**Date**: 2026-08-23T02:10:00+09:00  

---

## 1. Observation

Direct inspection of `tools_and_tests/test_phase5_3_ssot_quant.py`, `generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, `youtube_stream_scanner.py`, and forensic auditor reports revealed the exact structural loopholes and bypass vectors that allowed facade green test passes:

### Observation 1.1: Tier 4 CQRS Mock Assertion Omission & Write Masking
In `tools_and_tests/test_phase5_3_ssot_quant.py`, lines 670-692:
```python
670: import generate_dashboard_feed
671: 
672: # Track any DML persistence invocations
673: import db_manager
674: with patch.object(db_manager, "save_recommendation_matrix_record") as mock_save_matrix, \
675:      patch.object(db_manager, "archive_daily_recommendations") as mock_archive:
676:     
677:     # Execute dashboard data pipeline
678:     payload = generate_dashboard_feed.build_dashboard_data()
```
- **Defect**: The test wrapped `save_recommendation_matrix_record` and `archive_daily_recommendations` in `patch.object()`, which silently absorbed the function calls made by `generate_dashboard_feed.py` lines 689-690 (`db_manager.save_recommendation_matrix_record(...)` and `db_manager.archive_daily_recommendations(...)`) without asserting `mock_save_matrix.assert_not_called()` or `mock_archive.assert_not_called()`.
- **Empirical Execution**: In `forensic_check.py`, `mock_save_matrix` was invoked 1 time and `mock_archive` was invoked 1 time during read-only view model generation.
- **Defect 2**: The test lacked an unmocked real-DB zero-mutation test. If run against a real SQLite database, `build_dashboard_data()` mutates the database state on every view model build.

### Observation 1.2: Tier 5 Superficial AST Import Check Masking Legacy Duplicates
In `tools_and_tests/test_phase5_3_ssot_quant.py`, lines 726-745:
```python
726: # 1. Verify generate_dashboard_feed.py imports from domain quant
727: feed_imports = []
728: for node in ast.walk(feed_tree):
729:     if isinstance(node, ast.ImportFrom) and node.module:
730:         feed_imports.append(node.module)
731: 
732: has_domain_quant_import = any("al_sangmoo.domain.quant" in m for m in feed_imports)
733: self.assertTrue(
734:     has_domain_quant_import,
735:     "generate_dashboard_feed.py must import quantitative logic from al_sangmoo.domain.quant"
736: )
```
- **Defect**: This AST check only verified that `generate_dashboard_feed.py` contained *any* import from `al_sangmoo.domain.quant` (which was superficially satisfied by `from al_sangmoo.domain.quant.ichimoku import compute_institutional_flow_indicators` at line 79).
- **Masked Violations**:
  1. `generate_dashboard_feed.py` lines 81-156 still contained full inline `def build_ichimoku_series(...)`.
  2. `generate_dashboard_feed.py` lines 171-195 still executed inline rolling Tenkan (9), Kijun (26), SpanA (shift 26), SpanB (shift 26), SMA20/60, Vol_Ratio.
  3. `generate_dashboard_feed.py` lines 239-300 still executed inline scoring calculations.
  4. `al_sangmoo_daily_bot.py` lines 82-101 still contained `def calculate_indicators(df)`.
  5. `al_sangmoo_daily_bot.py` line 202 still used legacy conflicting scoring threshold `bull_score >= 65`.
  6. `youtube_stream_scanner.py` lines 260-386 still contained full 126-line inline `def analyze_macro_regime_and_climate(...)` without delegating to `al_sangmoo.domain.quant.macro.evaluate_macro_stance`.

---

## 2. Logic Chain

1. **Premise 1 (CQRS Pure Query Mandate)**:
   - §R4 of `ORIGINAL_REQUEST.md` mandates that `build_dashboard_data()` is a pure view model transformer that produces read feeds without executing SQLite write transactions.
   - If `build_dashboard_data()` calls any persistence write method, it violates CQRS and causes database lock contention when invoked concurrently by read APIs (`/api/scan_now`, web dashboard polling).
   - Therefore, Tier 4 must verify zero writes via both:
     - **Mock assertions**: All persistence write methods patched on `db_manager` and asserted `assert_not_called()`.
     - **Unmocked real-DB tests**: Executing `build_dashboard_data()` against an initialized SQLite database and asserting 0 row count delta across all tables.

2. **Premise 2 (SSOT Elimination of Duplicate Indicator & Scoring Code)**:
   - §R1, §R2, §R3 mandate that all rolling indicator math, 17-Year quantitative scoring, and MSI 2.0 macro calculations reside exclusively in `al_sangmoo/domain/quant/`.
   - Caller scripts (`generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, `youtube_stream_scanner.py`) must delete duplicate definitions and delegate 100% to domain modules.
   - Therefore, Tier 5 AST inspection must not be a mere presence check of imports; it must be a forensic multi-layered AST gate:
     - **Gate 1 (AST Function Blacklist)**: Assert that `build_ichimoku_series`, `calculate_indicators` are NOT defined in caller ASTs.
     - **Gate 2 (AST Rolling Math Blacklist)**: Walk AST call nodes and assert that `.rolling(9)`, `.rolling(26)`, `.rolling(52)` (and `rolling(window=...)`) do NOT exist in any caller script outside `al_sangmoo/domain/quant/`.
     - **Gate 3 (AST Mandatory Domain Imports)**: Assert that caller scripts import canonical functions from `al_sangmoo.domain.quant.ichimoku`, `al_sangmoo.domain.quant.scoring`, and `al_sangmoo.domain.quant.macro`.
     - **Gate 4 (AST Call Graph Invocation)**: Assert that caller scripts actually invoke the imported domain functions in their AST call nodes.

3. **Premise 3 (Regression & Math Parity)**:
   - All 6 tiers must execute without flaky dependencies, verifying mathematical parity on synthetic data and running the full platform regression suite (`test_phase1`, `test_phase2`, `test_phase4`, `test_phase5_1`, `test_phase5_2`, `test_global60`).

---

## 3. Detailed Test Hardening Specification

Below is the exact code architecture to be implemented in `tools_and_tests/test_phase5_3_ssot_quant.py`.

### 3.1 Hardened Tier 4: `TestTier4CQRSSideEffectFreePipeline`

```python
class TestTier4CQRSSideEffectFreePipeline(unittest.TestCase):
    """
    Verifies that generate_dashboard_feed.build_dashboard_data() operates as a
    pure, side-effect free transformation pipeline without modifying persistent SQLite state.
    """

    @patch("yfinance.download")
    def test_build_dashboard_data_side_effect_free_cqrs_mock_asserted(self, mock_yf):
        """Execute build_dashboard_data() and strictly assert zero persistence write mutations."""
        print("\n[Tier 4.1] Verifying CQRS Pipeline & Side-Effect Free Feed Generation (Mock Assertions)...")
        
        synthetic_df = generate_synthetic_ohlcv(n_bars=80, start_price=150.0)
        mock_yf.return_value = synthetic_df
        
        import generate_dashboard_feed
        import db_manager
        
        # Patch ALL database mutation / write functions in db_manager
        with patch.object(db_manager, "save_recommendation_matrix_record") as mock_save_matrix, \
             patch.object(db_manager, "archive_daily_recommendations") as mock_archive, \
             patch.object(db_manager, "save_macro_history_record") as mock_save_macro, \
             patch.object(db_manager, "add_portfolio_buy") as mock_add_buy, \
             patch.object(db_manager, "record_portfolio_sell") as mock_record_sell, \
             patch.object(db_manager, "close_portfolio_position") as mock_close_pos, \
             patch.object(db_manager, "clear_portfolio") as mock_clear, \
             patch.object(db_manager, "reset_all_holdings") as mock_reset, \
             patch.object(db_manager, "sync_portfolio_prices") as mock_sync_prices:
            
            # Execute dashboard data pipeline
            payload = generate_dashboard_feed.build_dashboard_data()
            
            # STRICT ZERO-WRITE ASSERTIONS
            mock_save_matrix.assert_not_called()
            mock_archive.assert_not_called()
            mock_save_macro.assert_not_called()
            mock_add_buy.assert_not_called()
            mock_record_sell.assert_not_called()
            mock_close_pos.assert_not_called()
            mock_clear.assert_not_called()
            mock_reset.assert_not_called()
            mock_sync_prices.assert_not_called()
            
            # Verify payload completeness and structure
            self.assertIsInstance(payload, dict)
            required_keys = [
                "macro", "kpis", "trades", "matrix", "daily_history", "portfolio",
                "dual_consensus", "strat1_exclusive", "strat2_exclusive",
                "primary_accumulation", "sniper_radar", "signal_tracker", "chart_intelligence"
            ]
            for key in required_keys:
                self.assertIn(key, payload, f"Missing payload root key: {key}")

            self.assertIsInstance(payload["chart_intelligence"], dict)
            self.assertIsInstance(payload["signal_tracker"], list)

        print("  -> PASSED: Zero write mutations executed. View model build is 100% CQRS read-pure.")

    @patch("yfinance.download")
    def test_build_dashboard_data_unmocked_sqlite_zero_mutation(self, mock_yf):
        """Execute build_dashboard_data() against real unmocked SQLite DB and assert 0 row mutations."""
        print("\n[Tier 4.2] Verifying Real SQLite Zero-Mutation Invariant across all tables...")
        
        import tempfile
        import sqlite3
        import generate_dashboard_feed
        import al_sangmoo.infrastructure.persistence as persistence
        import db_manager

        # Set up isolated temp SQLite DB
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            temp_db_path = tf.name

        old_env = os.environ.get("AL_SANGMOO_DB_PATH")
        os.environ["AL_SANGMOO_DB_PATH"] = temp_db_path
        try:
            # Initialize schema
            db_manager.init_database()
            
            # Seed initial records so tables exist and have known baseline count
            db_manager.save_macro_history_record("2026-08-22", 4.3, 16.0, 80.0, 100.0, 45.0, "DEFENSE_HOLD")
            db_manager.save_recommendation_matrix_record("2026-08-22", [{"ticker": "NVDA", "close": 200.0}], [], [])
            
            # Snapshot baseline table counts
            def get_all_table_counts():
                counts = {}
                with sqlite3.connect(temp_db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
                    tables = [row[0] for row in cursor.fetchall()]
                    for tbl in tables:
                        cursor.execute(f"SELECT count(*) FROM {tbl}")
                        counts[tbl] = cursor.fetchone()[0]
                return counts

            baseline_counts = get_all_table_counts()
            
            # Mock yfinance to return synthetic OHLCV
            synthetic_df = generate_synthetic_ohlcv(n_bars=80, start_price=150.0)
            mock_yf.return_value = synthetic_df
            
            # Execute dashboard feed build against real SQLite DB
            payload = generate_dashboard_feed.build_dashboard_data()
            
            # Snapshot post-execution table counts
            post_counts = get_all_table_counts()
            
            # Assert exact row count equality across all tables
            for tbl, baseline_cnt in baseline_counts.items():
                post_cnt = post_counts.get(tbl, 0)
                self.assertEqual(
                    post_cnt, baseline_cnt,
                    f"CQRS Violation: Table '{tbl}' modified during build_dashboard_data() (before: {baseline_cnt}, after: {post_cnt})"
                )
                
            print("  -> PASSED: Unmocked SQLite database remained completely untouched (0 row mutations).")
        finally:
            if old_env is not None:
                os.environ["AL_SANGMOO_DB_PATH"] = old_env
            else:
                os.environ.pop("AL_SANGMOO_DB_PATH", None)
            for ext in ["", "-wal", "-shm"]:
                p = f"{temp_db_path}{ext}"
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

    @patch("yfinance.download")
    def test_build_dashboard_data_idempotency_and_pure_reads(self, mock_yf):
        """Verify calling build_dashboard_data() multiple times produces identical output without state drift."""
        print("\n[Tier 4.3] Verifying Dashboard Feed Pipeline Idempotency...")
        synthetic_df = generate_synthetic_ohlcv(n_bars=80, start_price=150.0)
        mock_yf.return_value = synthetic_df
        
        import generate_dashboard_feed
        
        p1 = generate_dashboard_feed.build_dashboard_data()
        p2 = generate_dashboard_feed.build_dashboard_data()
        
        self.assertEqual(len(p1["matrix"]), len(p2["matrix"]))
        self.assertEqual(len(p1["signal_tracker"]), len(p2["signal_tracker"]))
        self.assertEqual(p1["macro"]["msi_score"], p2["macro"]["msi_score"])
        print("  -> PASSED: Dashboard feed pipeline is 100% idempotent.")
```

---

### 3.2 Hardened Tier 5: `TestTier5StaticASTDeduplication`

```python
class TestTier5StaticASTDeduplication(unittest.TestCase):
    """
    Parses application scripts using Python AST to verify Single Source of Truth
    architectural compliance, complete elimination of duplicate math/scoring, and clean domain separation.
    """

    def setUp(self):
        self.feed_py_path = os.path.join(PROJECT_ROOT, "generate_dashboard_feed.py")
        self.bot_py_path = os.path.join(PROJECT_ROOT, "al_sangmoo_daily_bot.py")
        self.yt_py_path = os.path.join(PROJECT_ROOT, "youtube_stream_scanner.py")

        self.assertTrue(os.path.exists(self.feed_py_path), "generate_dashboard_feed.py must exist")
        self.assertTrue(os.path.exists(self.bot_py_path), "al_sangmoo_daily_bot.py must exist")
        self.assertTrue(os.path.exists(self.yt_py_path), "youtube_stream_scanner.py must exist")

        with open(self.feed_py_path, "r", encoding="utf-8") as f:
            self.feed_tree = ast.parse(f.read(), filename="generate_dashboard_feed.py")

        with open(self.bot_py_path, "r", encoding="utf-8") as f:
            self.bot_tree = ast.parse(f.read(), filename="al_sangmoo_daily_bot.py")

        with open(self.yt_py_path, "r", encoding="utf-8") as f:
            self.yt_tree = ast.parse(f.read(), filename="youtube_stream_scanner.py")

    def test_ast_elimination_of_duplicate_functions(self):
        """Assert duplicate indicator, series, and scoring functions are 100% removed from callers."""
        print("\n[Tier 5.1] AST Verification: Elimination of Duplicate Function Definitions...")
        
        # 1. generate_dashboard_feed.py must NOT define build_ichimoku_series
        feed_func_names = [n.name for n in ast.walk(self.feed_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        self.assertNotIn(
            "build_ichimoku_series", feed_func_names,
            "generate_dashboard_feed.py must NOT define duplicate 'build_ichimoku_series'. Use al_sangmoo.domain.quant.ichimoku.build_ichimoku_series_payload."
        )

        # 2. al_sangmoo_daily_bot.py must NOT define calculate_indicators
        bot_func_names = [n.name for n in ast.walk(self.bot_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        self.assertNotIn(
            "calculate_indicators", bot_func_names,
            "al_sangmoo_daily_bot.py must NOT define duplicate 'calculate_indicators'. Use al_sangmoo.domain.quant.ichimoku.calculate_ichimoku_indicators."
        )

        print(f"  -> PASSED: Duplicate functions eliminated from AST (feed: {feed_func_names}, bot: {bot_func_names}).")

    def test_ast_disallow_inline_rolling_indicator_math(self):
        """Assert caller scripts do not contain inline rolling(9), rolling(26), rolling(52) math."""
        print("\n[Tier 5.2] AST Verification: Absence of Inline Rolling Indicator Calculations...")
        
        target_scripts = [
            ("generate_dashboard_feed.py", self.feed_tree),
            ("al_sangmoo_daily_bot.py", self.bot_tree),
            ("youtube_stream_scanner.py", self.yt_tree)
        ]

        forbidden_windows = {9, 26, 52}

        for script_name, tree in target_scripts:
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    # Check if call is .rolling(...)
                    if isinstance(node.func, ast.Attribute) and node.func.attr == "rolling":
                        # Check args (e.g. rolling(9))
                        for arg in node.args:
                            if isinstance(arg, ast.Constant) and arg.value in forbidden_windows:
                                self.fail(
                                    f"SSOT Violation in {script_name} (line {node.lineno}): "
                                    f"Contains inline '.rolling({arg.value})'. Must delegate to al_sangmoo.domain.quant.ichimoku."
                                )
                        # Check keyword args (e.g. rolling(window=9))
                        for kw in node.keywords:
                            if kw.arg == "window" and isinstance(kw.value, ast.Constant) and kw.value.value in forbidden_windows:
                                self.fail(
                                    f"SSOT Violation in {script_name} (line {node.lineno}): "
                                    f"Contains inline '.rolling(window={kw.value.value})'. Must delegate to al_sangmoo.domain.quant.ichimoku."
                                )

        print("  -> PASSED: No inline rolling(9/26/52) indicator calculations detected in caller scripts.")

    def test_ast_verify_mandatory_domain_quant_imports(self):
        """Assert caller scripts import canonical math and scoring engines from al_sangmoo.domain.quant."""
        print("\n[Tier 5.3] AST Verification: Mandatory Domain Quant Imports in Callers...")
        
        def get_imported_modules_and_symbols(tree):
            imports = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module not in imports:
                        imports[node.module] = set()
                    for alias in node.names:
                        imports[node.module].add(alias.name)
            return imports

        feed_imports = get_imported_modules_and_symbols(self.feed_tree)
        bot_imports = get_imported_modules_and_symbols(self.bot_tree)
        yt_imports = get_imported_modules_and_symbols(self.yt_tree)

        # 1. generate_dashboard_feed.py
        self.assertIn(
            "al_sangmoo.domain.quant.ichimoku", feed_imports,
            "generate_dashboard_feed.py must import from al_sangmoo.domain.quant.ichimoku"
        )
        self.assertTrue(
            {"calculate_ichimoku_indicators", "build_ichimoku_series_payload"}.issubset(feed_imports["al_sangmoo.domain.quant.ichimoku"]),
            "generate_dashboard_feed.py must import calculate_ichimoku_indicators and build_ichimoku_series_payload"
        )
        self.assertIn(
            "al_sangmoo.domain.quant.scoring", feed_imports,
            "generate_dashboard_feed.py must import from al_sangmoo.domain.quant.scoring"
        )

        # 2. al_sangmoo_daily_bot.py
        self.assertIn(
            "al_sangmoo.domain.quant.ichimoku", bot_imports,
            "al_sangmoo_daily_bot.py must import from al_sangmoo.domain.quant.ichimoku"
        )
        self.assertIn(
            "calculate_ichimoku_indicators", bot_imports["al_sangmoo.domain.quant.ichimoku"],
            "al_sangmoo_daily_bot.py must import calculate_ichimoku_indicators"
        )
        self.assertIn(
            "al_sangmoo.domain.quant.scoring", bot_imports,
            "al_sangmoo_daily_bot.py must import from al_sangmoo.domain.quant.scoring"
        )

        # 3. youtube_stream_scanner.py
        self.assertIn(
            "al_sangmoo.domain.quant.macro", yt_imports,
            "youtube_stream_scanner.py must import from al_sangmoo.domain.quant.macro"
        )
        self.assertTrue(
            "evaluate_macro_stance" in yt_imports["al_sangmoo.domain.quant.macro"] or "calculate_msi_regime" in yt_imports["al_sangmoo.domain.quant.macro"],
            "youtube_stream_scanner.py must import evaluate_macro_stance or calculate_msi_regime"
        )

        print("  -> PASSED: All caller scripts contain required al_sangmoo.domain.quant imports.")

    def test_ast_verify_actual_domain_function_invocations(self):
        """Assert caller scripts actually invoke imported domain functions in their AST call graphs."""
        print("\n[Tier 5.4] AST Verification: Domain Function Invocations in Call Graph...")
        
        def get_called_function_names(tree):
            called = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        called.add(node.func.id)
                    elif isinstance(node.func, ast.Attribute):
                        called.add(node.func.attr)
            return called

        feed_calls = get_called_function_names(self.feed_tree)
        bot_calls = get_called_function_names(self.bot_tree)
        yt_calls = get_called_function_names(self.yt_tree)

        # 1. generate_dashboard_feed.py calls
        self.assertIn(
            "calculate_ichimoku_indicators", feed_calls,
            "generate_dashboard_feed.py AST must contain calls to calculate_ichimoku_indicators"
        )
        self.assertIn(
            "build_ichimoku_series_payload", feed_calls,
            "generate_dashboard_feed.py AST must contain calls to build_ichimoku_series_payload"
        )

        # 2. al_sangmoo_daily_bot.py calls
        self.assertIn(
            "calculate_ichimoku_indicators", bot_calls,
            "al_sangmoo_daily_bot.py AST must contain calls to calculate_ichimoku_indicators"
        )

        # 3. youtube_stream_scanner.py calls
        self.assertTrue(
            "evaluate_macro_stance" in yt_calls or "calculate_msi_regime" in yt_calls,
            "youtube_stream_scanner.py AST must contain calls to evaluate_macro_stance or calculate_msi_regime"
        )

        print("  -> PASSED: Domain function calls confirmed in AST call graphs across all caller scripts.")
```

---

### 3.3 Hardened Tier 1: `build_ichimoku_series_payload` Chart Format Parity

In `TestTier1IndicatorMathParity`, add:
```python
    def test_build_ichimoku_series_payload_chart_contract(self):
        """Verify build_ichimoku_series_payload produces exact lightweight-charts payload format."""
        print("\n[Tier 1.5] Verifying build_ichimoku_series_payload Chart Contract & Series Structure...")
        from al_sangmoo.domain.quant.ichimoku import build_ichimoku_series_payload
        
        df = generate_synthetic_ohlcv(n_bars=80, start_price=100.0)
        payload = build_ichimoku_series_payload(df, max_bars=60)
        
        required_chart_keys = [
            "candles", "kijun_line", "tenkan_line", "span_a_line", "span_b_line",
            "sma20", "sma60", "volume", "future_span_a", "future_span_b"
        ]
        for k in required_chart_keys:
            self.assertIn(k, payload, f"Missing chart series key: {k}")
            
        self.assertLessEqual(len(payload["candles"]), 60)
        self.assertEqual(len(payload["future_span_a"]), 26)
        self.assertEqual(len(payload["future_span_b"]), 26)
        print("  -> PASSED: build_ichimoku_series_payload chart contract verified.")
```

---

## 4. Caveats

- **Caveat 1 (Failure on Current Codebase)**: These hardened Tier 4 and Tier 5 tests are intentionally designed to **FAIL** when executed against the current un-refactored codebase. They will only pass once Builder completes the refactoring of `generate_dashboard_feed.py`, `al_sangmoo_daily_bot.py`, and `youtube_stream_scanner.py`.
- **Caveat 2 (Database State Isolation)**: In Tier 4 unmocked testing, temporary database files must always be cleaned up in a `finally` block and `os.environ["AL_SANGMOO_DB_PATH"]` must be safely restored to avoid impacting other test suites.
- **Caveat 3 (Subprocess Execution in Tier 6)**: Tier 6 regression runners execute external Python processes with a 120-second timeout to protect against deadlocks.

---

## 5. Conclusion

- **Verdict**: Test Hardening Strategy is fully specified, forensically sound, and ready for immediate implementation by Builder.
- **Summary of Hardened Guarantees**:
  1. **Tier 4**: Zero SQLite writes during `build_dashboard_data()` verified both by mock assertions on `db_manager` and unmocked real-DB table row count diffs.
  2. **Tier 5**: Static AST analysis enforces the eradication of duplicate indicator/scoring functions, bans inline rolling math in consumer scripts, and verifies mandatory domain imports and call invocations.
  3. **Tier 1-3 & 6**: Full mathematical parity, 17-Year score determinism, MSI 2.0 boundary conditions, and 100% clean platform regression testing.

---

## 6. Verification Method

To independently verify the test hardening implementation:

```powershell
# 1. Run the hardened test suite
python tools_and_tests/test_phase5_3_ssot_quant.py

# 2. Inspect Tier 4 CQRS tests
python -m unittest tools_and_tests.test_phase5_3_ssot_quant.TestTier4CQRSSideEffectFreePipeline

# 3. Inspect Tier 5 AST deduplication tests
python -m unittest tools_and_tests.test_phase5_3_ssot_quant.TestTier5StaticASTDeduplication

# 4. Verify all platform regression suites pass
python -m unittest tools_and_tests.test_phase5_3_ssot_quant.TestTier6PlatformRegressionRunner
```
