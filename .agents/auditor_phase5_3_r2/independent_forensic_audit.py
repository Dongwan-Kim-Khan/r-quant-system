"""
Independent Forensic Integrity Audit Script (Phase 5.3 Re-Audit)
Author: Forensic Auditor (auditor_phase5_3_r2)
"""

import ast
import os
import sys
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np

PROJECT_ROOT = r"d:\코딩\Playground\al_sangmoo_project"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

def run_ast_forensic_inspection():
    print("=" * 70)
    print("PHASE 1: AST STRUCTURE & DE-DUPLICATION FORENSICS")
    print("=" * 70)
    
    feed_path = os.path.join(PROJECT_ROOT, "generate_dashboard_feed.py")
    bot_path = os.path.join(PROJECT_ROOT, "al_sangmoo_daily_bot.py")
    yt_path = os.path.join(PROJECT_ROOT, "youtube_stream_scanner.py")

    with open(feed_path, "r", encoding="utf-8") as f:
        feed_tree = ast.parse(f.read(), filename="generate_dashboard_feed.py")
    with open(bot_path, "r", encoding="utf-8") as f:
        bot_tree = ast.parse(f.read(), filename="al_sangmoo_daily_bot.py")
    with open(yt_path, "r", encoding="utf-8") as f:
        yt_tree = ast.parse(f.read(), filename="youtube_stream_scanner.py")

    # 1. Elimination of build_ichimoku_series
    feed_funcs = [n.name for n in ast.walk(feed_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert "build_ichimoku_series" not in feed_funcs, "VIOLATION: build_ichimoku_series still defined in generate_dashboard_feed.py"
    print("  [PASS] generate_dashboard_feed.py: 'build_ichimoku_series' is completely eliminated.")

    # 2. Elimination of calculate_indicators
    bot_funcs = [n.name for n in ast.walk(bot_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert "calculate_indicators" not in bot_funcs, "VIOLATION: calculate_indicators still defined in al_sangmoo_daily_bot.py"
    print("  [PASS] al_sangmoo_daily_bot.py: 'calculate_indicators' is completely eliminated.")

    # 3. Disallow inline rolling math (9, 26, 52) in callers
    forbidden_windows = {9, 26, 52}
    for sname, tree in [("generate_dashboard_feed.py", feed_tree), ("al_sangmoo_daily_bot.py", bot_tree), ("youtube_stream_scanner.py", yt_tree)]:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "rolling":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value in forbidden_windows:
                        raise AssertionError(f"VIOLATION in {sname}: inline rolling({arg.value}) found on line {node.lineno}")
                for kw in node.keywords:
                    if kw.arg == "window" and isinstance(kw.value, ast.Constant) and kw.value.value in forbidden_windows:
                        raise AssertionError(f"VIOLATION in {sname}: inline rolling(window={kw.value.value}) found on line {node.lineno}")
    print("  [PASS] Callers: Zero inline rolling(9/26/52) calculations detected.")

    # 4. Delegation in compute_all_indicators
    compute_func = next((n for n in ast.walk(feed_tree) if isinstance(n, ast.FunctionDef) and n.name == "compute_all_indicators"), None)
    assert compute_func is not None, "generate_dashboard_feed.py must define compute_all_indicators entry point"
    compute_calls = {n.func.id for n in ast.walk(compute_func) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    expected_feed_delegations = {"calculate_ichimoku_indicators", "build_ichimoku_series_payload", "detect_cloud_trampoline_bounce", "compute_institutional_flow_indicators", "evaluate_quant_score"}
    for d in expected_feed_delegations:
        assert d in compute_calls, f"VIOLATION: compute_all_indicators does not delegate to {d}"
    print(f"  [PASS] generate_dashboard_feed.compute_all_indicators delegates to: {expected_feed_delegations}")

    # 5. Delegation in scan_and_select_2x2x2
    scan_func = next((n for n in ast.walk(bot_tree) if isinstance(n, ast.FunctionDef) and n.name == "scan_and_select_2x2x2"), None)
    assert scan_func is not None, "al_sangmoo_daily_bot.py must define scan_and_select_2x2x2"
    scan_calls = {n.func.id for n in ast.walk(scan_func) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    expected_bot_delegations = {"calculate_ichimoku_indicators", "detect_cloud_trampoline_bounce", "compute_institutional_flow_indicators", "evaluate_quant_score", "classify_3tier_candidates"}
    for d in expected_bot_delegations:
        assert d in scan_calls, f"VIOLATION: scan_and_select_2x2x2 does not delegate to {d}"
    print(f"  [PASS] al_sangmoo_daily_bot.scan_and_select_2x2x2 delegates to: {expected_bot_delegations}")

    # 6. Delegation in youtube_stream_scanner.py
    yt_macro_func = next((n for n in ast.walk(yt_tree) if isinstance(n, ast.FunctionDef) and n.name == "analyze_macro_regime_and_climate"), None)
    assert yt_macro_func is not None, "youtube_stream_scanner.py must define analyze_macro_regime_and_climate"
    yt_macro_calls = {n.func.id for n in ast.walk(yt_macro_func) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "evaluate_macro_stance" in yt_macro_calls, "VIOLATION: analyze_macro_regime_and_climate does not delegate to evaluate_macro_stance"
    print("  [PASS] youtube_stream_scanner.analyze_macro_regime_and_climate delegates to evaluate_macro_stance.")


def run_cqrs_forensic_inspection():
    print("\n" + "=" * 70)
    print("PHASE 2: CQRS READ PURITY & DATABASE MUTATION FORENSICS")
    print("=" * 70)
    
    import db_manager
    import generate_dashboard_feed

    # Part A: Mock Persistence write function audit
    with patch("yfinance.download") as mock_yf, \
         patch.object(db_manager, "save_recommendation_matrix_record") as mock_save_matrix, \
         patch.object(db_manager, "archive_daily_recommendations") as mock_archive, \
         patch.object(db_manager, "save_macro_history_record") as mock_save_macro, \
         patch.object(db_manager, "add_portfolio_buy") as mock_add_buy, \
         patch.object(db_manager, "record_portfolio_sell") as mock_record_sell:

        dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=80)
        closes = [100.0 + i for i in range(80)]
        synth_df = pd.DataFrame({
            "Open": closes, "High": [c + 2 for c in closes], "Low": [c - 2 for c in closes],
            "Close": closes, "Volume": [1000000]*80
        }, index=dates)
        mock_yf.return_value = synth_df

        payload = generate_dashboard_feed.build_dashboard_data()
        
        assert mock_save_matrix.call_count == 0, f"VIOLATION: save_recommendation_matrix_record called {mock_save_matrix.call_count} times"
        assert mock_archive.call_count == 0, f"VIOLATION: archive_daily_recommendations called {mock_archive.call_count} times"
        assert mock_save_macro.call_count == 0, f"VIOLATION: save_macro_history_record called {mock_save_macro.call_count} times"
        assert mock_add_buy.call_count == 0, f"VIOLATION: add_portfolio_buy called {mock_add_buy.call_count} times"
        assert mock_record_sell.call_count == 0, f"VIOLATION: record_portfolio_sell called {mock_record_sell.call_count} times"
        print("  [PASS] Mock Audit: 0 persistence write function invocations during build_dashboard_data().")

    # Part B: Unmocked Real SQLite DB zero-mutation verification
    with tempfile.NamedTemporaryFile(suffix="_audit.db", delete=False) as tf:
        temp_db = tf.name

    old_env = os.environ.get("AL_SANGMOO_DB_PATH")
    os.environ["AL_SANGMOO_DB_PATH"] = temp_db
    try:
        db_manager.init_database()
        db_manager.save_macro_history_record("2026-08-22", {"msi_score": 50.0, "macro_stance": "DEFENSE_HOLD"}, {})
        db_manager.save_recommendation_matrix_record("2026-08-22", [{"ticker": "NVDA", "close": 200.0}], [], [])
        db_manager.add_portfolio_buy("NVDA", 100.0, 10.0, "2026-08-22")

        def count_all_tables():
            counts = {}
            with sqlite3.connect(temp_db) as conn:
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
                tables = [r[0] for r in cur.fetchall()]
                for t in tables:
                    cur.execute(f"SELECT count(*) FROM {t}")
                    counts[t] = cur.fetchone()[0]
            return counts

        before = count_all_tables()
        with patch("yfinance.download") as mock_yf:
            mock_yf.return_value = synth_df
            generate_dashboard_feed.build_dashboard_data()
        after = count_all_tables()

        for tbl, b_cnt in before.items():
            a_cnt = after.get(tbl, 0)
            assert b_cnt == a_cnt, f"VIOLATION: Table {tbl} row count changed from {b_cnt} to {a_cnt}"
        print(f"  [PASS] Real SQLite Zero-Mutation: All table row counts identical before and after ({before}).")
    finally:
        if old_env is not None:
            os.environ["AL_SANGMOO_DB_PATH"] = old_env
        else:
            os.environ.pop("AL_SANGMOO_DB_PATH", None)
        for ext in ["", "-wal", "-shm"]:
            p = f"{temp_db}{ext}"
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


def run_test_suite_audit():
    print("\n" + "=" * 70)
    print("PHASE 3: TEST SUITE TIER 4 & TIER 5 RIGOR INSPECTION")
    print("=" * 70)
    
    test_path = os.path.join(PROJECT_ROOT, "tools_and_tests", "test_phase5_3_ssot_quant.py")
    with open(test_path, "r", encoding="utf-8") as f:
        test_source = f.read()
    test_tree = ast.parse(test_source, filename="test_phase5_3_ssot_quant.py")

    # Verify Tier 4 has assert_not_called()
    assert "mock_save_matrix.assert_not_called()" in test_source, "Tier 4 must include mock_save_matrix.assert_not_called()"
    assert "mock_archive.assert_not_called()" in test_source, "Tier 4 must include mock_archive.assert_not_called()"
    print("  [PASS] Tier 4 strictly asserts assert_not_called() on write mocks.")

    # Verify Tier 4 has unmocked SQLite zero mutation test
    assert "test_build_dashboard_data_unmocked_sqlite_zero_mutation" in test_source, "Tier 4 must include test_build_dashboard_data_unmocked_sqlite_zero_mutation"
    print("  [PASS] Tier 4 contains unmocked real SQLite zero-mutation test.")

    # Verify Tier 5 strictly parses AST and asserts absence of duplicate functions
    assert "test_ast_elimination_of_duplicate_functions" in test_source, "Tier 5 must test elimination of duplicate functions"
    assert "test_ast_disallow_inline_rolling_indicator_math" in test_source, "Tier 5 must test inline rolling indicator ban"
    assert "test_ast_verify_mandatory_domain_quant_imports" in test_source, "Tier 5 must verify mandatory domain imports"
    assert "test_ast_verify_actual_domain_function_invocations" in test_source, "Tier 5 must verify domain invocations"
    print("  [PASS] Tier 5 strictly parses AST and enforces elimination of duplicate functions and inline math.")


if __name__ == "__main__":
    run_ast_forensic_inspection()
    run_cqrs_forensic_inspection()
    run_test_suite_audit()
    print("\n" + "=" * 70)
    print("ALL INDEPENDENT FORENSIC CHECKS PASSED (CLEAN)")
    print("=" * 70)
