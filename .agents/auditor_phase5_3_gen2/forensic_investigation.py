import os
import sys
import ast
import inspect
import sqlite3
import unittest
import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock

BASE_DIR = r"d:\코딩\Playground\al_sangmoo_project"
sys.path.insert(0, BASE_DIR)

print("=" * 80)
print("AL-SANGMOO QUANT PLATFORM: PHASE 5.3 FORENSIC INTEGRITY AUDIT")
print("=" * 80)

# ---------------------------------------------------------
# 1. AST STATIC ANALYSIS: DUPLICATION & INLINE MATH CHECK
# ---------------------------------------------------------
print("\n[CHECK 1] AST STATIC ANALYSIS FOR CODE DUPLICATION & INLINE MATH")

files_to_check = {
    "generate_dashboard_feed.py": os.path.join(BASE_DIR, "generate_dashboard_feed.py"),
    "al_sangmoo_daily_bot.py": os.path.join(BASE_DIR, "al_sangmoo_daily_bot.py"),
    "youtube_stream_scanner.py": os.path.join(BASE_DIR, "youtube_stream_scanner.py"),
    "server.py": os.path.join(BASE_DIR, "server.py"),
}

forbidden_func_names = {
    "build_ichimoku_series",
    "calculate_indicators",
    "calculate_ichimoku",
    "compute_ichimoku",
}

forbidden_rolling_windows = {9, 26, 52}

ast_violations = []

for label, fpath in files_to_check.items():
    with open(fpath, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=label)
    
    # Check duplicate functions
    defined_funcs = [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for fn in defined_funcs:
        if fn in forbidden_func_names:
            ast_violations.append(f"Duplicate function '{fn}' defined in {label}")
            
    # Check inline rolling math
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "rolling":
            # args
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value in forbidden_rolling_windows:
                    ast_violations.append(f"Inline .rolling({arg.value}) found in {label}:{node.lineno}")
            # kwargs
            for kw in node.keywords:
                if kw.arg == "window" and isinstance(kw.value, ast.Constant) and kw.value.value in forbidden_rolling_windows:
                    ast_violations.append(f"Inline .rolling(window={kw.value.value}) found in {label}:{node.lineno}")

if ast_violations:
    print(f"FAILED: AST Violations found: {ast_violations}")
else:
    print("PASS: AST Static Analysis clean. Zero duplicate functions and zero inline rolling indicator calls.")

# ---------------------------------------------------------
# 2. ANTI-CHEATING & FACADE DETECTION
# ---------------------------------------------------------
print("\n[CHECK 2] ANTI-CHEATING & FACADE DETECTION IN DOMAIN & TESTS")

domain_files = [
    os.path.join(BASE_DIR, "al_sangmoo", "domain", "quant", "ichimoku.py"),
    os.path.join(BASE_DIR, "al_sangmoo", "domain", "quant", "scoring.py"),
    os.path.join(BASE_DIR, "al_sangmoo", "domain", "quant", "macro.py"),
]

facade_violations = []

for df_path in domain_files:
    fname = os.path.basename(df_path)
    with open(df_path, "r", encoding="utf-8") as f:
        src = f.read()
        tree = ast.parse(src, filename=fname)
        
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            # Check if function body is just `pass`, `return <constant>`, or raises NotImplementedError
            if len(node.body) == 1:
                stmt = node.body[0]
                if isinstance(stmt, ast.Pass):
                    facade_violations.append(f"Dummy pass in {fname}:{node.name}")
                elif isinstance(stmt, ast.Return) and isinstance(stmt.value, (ast.Constant, ast.Dict, ast.List)):
                    facade_violations.append(f"Hardcoded constant return in {fname}:{node.name}")
                elif isinstance(stmt, ast.Raise) and isinstance(stmt.exc, ast.Call) and getattr(stmt.exc.func, "id", "") == "NotImplementedError":
                    facade_violations.append(f"NotImplementedError placeholder in {fname}:{node.name}")

if facade_violations:
    print(f"FAILED: Facade implementations detected: {facade_violations}")
else:
    print("PASS: Zero dummy/facade implementations detected in domain modules.")

# ---------------------------------------------------------
# 3. TEST AUTHENTICITY ANALYSIS (test_phase5_3_ssot_quant.py)
# ---------------------------------------------------------
print("\n[CHECK 3] TEST AUTHENTICITY & ASSERTION RIGOR ANALYSIS")

test_path = os.path.join(BASE_DIR, "tools_and_tests", "test_phase5_3_ssot_quant.py")
with open(test_path, "r", encoding="utf-8") as f:
    test_src = f.read()
    test_tree = ast.parse(test_src, filename="test_phase5_3_ssot_quant.py")

test_classes = [n for n in test_tree.body if isinstance(n, ast.ClassDef) and "Test" in n.name]
total_test_methods = 0
assertion_count = 0
tautological_asserts = []
skipped_tests = []

for cls_node in test_classes:
    for item in cls_node.body:
        if isinstance(item, ast.FunctionDef) and item.name.startswith("test_"):
            total_test_methods += 1
            # Check decorators
            for dec in item.decorator_list:
                if (isinstance(dec, ast.Attribute) and "skip" in dec.attr) or (isinstance(dec, ast.Call) and "skip" in getattr(dec.func, "attr", "")):
                    skipped_tests.append(f"{cls_node.name}.{item.name}")
                    
            # Check assert statements & self.assert* calls
            for sub in ast.walk(item):
                if isinstance(sub, ast.Assert):
                    assertion_count += 1
                    if isinstance(sub.test, ast.Constant) and sub.test.value is True:
                        tautological_asserts.append(f"assert True in {cls_node.name}.{item.name}")
                elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                    if sub.func.attr.startswith("assert"):
                        assertion_count += 1
                        if sub.func.attr == "assertTrue" and len(sub.args) >= 1 and isinstance(sub.args[0], ast.Constant) and sub.args[0].value is True:
                            tautological_asserts.append(f"self.assertTrue(True) in {cls_node.name}.{item.name}")

print(f"Total Test Classes: {len(test_classes)}")
print(f"Total Test Methods: {total_test_methods}")
print(f"Total Assertions: {assertion_count}")
print(f"Tautological Assertions: {len(tautological_asserts)}")
print(f"Skipped Tests: {len(skipped_tests)}")

if tautological_asserts or skipped_tests:
    print(f"FAILED: Tautologies: {tautological_asserts}, Skips: {skipped_tests}")
else:
    print("PASS: Test suite authenticity confirmed (zero tautologies, zero skips).")

# ---------------------------------------------------------
# 4. RUNTIME TRACING & CQRS VERIFICATION
# ---------------------------------------------------------
print("\n[CHECK 4] RUNTIME TRACE ON build_dashboard_data() FOR ZERO DB WRITES")

import generate_dashboard_feed
import db_manager

# Create isolated in-memory or temp SQLite DB
temp_test_db = os.path.join(BASE_DIR, "audit_temp_trace.db")
os.environ["AL_SANGMOO_DB_PATH"] = temp_test_db
db_manager.init_database()

# Wrap db_manager.get_connection to track all SQL statements executed
executed_queries = []
original_get_conn = db_manager.get_connection

class TrackingCursor:
    def __init__(self, cursor):
        self._cursor = cursor
    def execute(self, sql, *args, **kwargs):
        executed_queries.append(sql.strip().upper())
        return self._cursor.execute(sql, *args, **kwargs)
    def executemany(self, sql, *args, **kwargs):
        executed_queries.append(sql.strip().upper())
        return self._cursor.executemany(sql, *args, **kwargs)
    def __getattr__(self, name):
        return getattr(self._cursor, name)

class TrackingConnection:
    def __init__(self, conn):
        self._conn = conn
    def cursor(self):
        return TrackingCursor(self._conn.cursor())
    def execute(self, sql, *args, **kwargs):
        executed_queries.append(sql.strip().upper())
        return self._conn.execute(sql, *args, **kwargs)
    def __enter__(self):
        self._conn.__enter__()
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._conn.__exit__(exc_type, exc_val, exc_tb)
    def __getattr__(self, name):
        return getattr(self._conn, name)

def tracking_get_conn():
    conn = original_get_conn()
    return TrackingConnection(conn)

db_manager.get_connection = tracking_get_conn

# Synthetic download patch to avoid network dependencies during trace
synthetic_df = pd.DataFrame({
    "Open": [100.0] * 80,
    "High": [105.0] * 80,
    "Low": [95.0] * 80,
    "Close": [102.0] * 80,
    "Volume": [1000000] * 80
}, index=pd.bdate_range(end=pd.Timestamp.now(), periods=80))

with patch("yfinance.download", return_value=synthetic_df):
    executed_queries.clear()
    payload = generate_dashboard_feed.build_dashboard_data()

db_manager.get_connection = original_get_conn

write_keywords = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE"]
forbidden_write_queries = [q for q in executed_queries if any(q.startswith(kw) for kw in write_keywords)]

print(f"Total SQLite Queries Executed during build_dashboard_data(): {len(executed_queries)}")
print(f"Write Queries Executed: {len(forbidden_write_queries)}")

if forbidden_write_queries:
    print(f"FAILED: CQRS Violation! Found write queries: {forbidden_write_queries}")
else:
    print("PASS: Zero SQLite write queries executed during build_dashboard_data(). CQRS Read-Purity Confirmed.")

# Cleanup temp db
for ext in ["", "-wal", "-shm"]:
    p = f"{temp_test_db}{ext}"
    if os.path.exists(p):
        try: os.remove(p)
        except: pass

print("\n" + "=" * 80)
print("FORENSIC CHECKS COMPLETE")
print("=" * 80)
