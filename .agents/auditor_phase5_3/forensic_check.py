import ast
import os
import sys

PROJECT_ROOT = r"d:\코딩\Playground\al_sangmoo_project"

def audit_ast():
    files = {
        'generate_dashboard_feed.py': ['build_ichimoku_series', 'compute_all_indicators'],
        'al_sangmoo_daily_bot.py': ['calculate_indicators', 'scan_and_select_2x2x2'],
        'youtube_stream_scanner.py': ['analyze_macro_regime_and_climate']
    }

    print("=================================================================")
    print("FORENSIC AST DUPLICATION CHECK")
    print("=================================================================")
    for fname, funcs in files.items():
        fpath = os.path.join(PROJECT_ROOT, fname)
        with open(fpath, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=fname)
        found_funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        print(f"=== {fname} ===")
        for target in funcs:
            present = target in found_funcs
            print(f"  Function {target}: {'DUPLICATE DETECTED' if present else 'REMOVED/CLEAN'}")

def audit_cqrs():
    print("\n=================================================================")
    print("FORENSIC CQRS SIDE-EFFECT CHECK (build_dashboard_data)")
    print("=================================================================")
    import unittest.mock as mock
    sys.path.insert(0, PROJECT_ROOT)
    
    import db_manager
    import generate_dashboard_feed
    
    with mock.patch("yfinance.download") as mock_yf, \
         mock.patch.object(db_manager, "save_recommendation_matrix_record") as mock_save_matrix, \
         mock.patch.object(db_manager, "archive_daily_recommendations") as mock_archive:
        
        import pandas as pd
        import numpy as np
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=70)
        closes = [100.0 + i for i in range(70)]
        synthetic_df = pd.DataFrame({
            "Open": closes, "High": [c + 1 for c in closes], "Low": [c - 1 for c in closes],
            "Close": closes, "Volume": [1000000]*70
        }, index=dates)
        mock_yf.return_value = synthetic_df
        
        generate_dashboard_feed.build_dashboard_data()
        
        save_matrix_calls = mock_save_matrix.call_count
        archive_calls = mock_archive.call_count
        print(f"  save_recommendation_matrix_record call count during read query: {save_matrix_calls}")
        print(f"  archive_daily_recommendations call count during read query: {archive_calls}")
        if save_matrix_calls > 0 or archive_calls > 0:
            print("  -> CQRS VIOLATION: build_dashboard_data executes database writes!")
        else:
            print("  -> CQRS CLEAN: 0 database writes.")

if __name__ == '__main__':
    audit_ast()
    audit_cqrs()
