"""
===============================================================================
  Al-Sangmoo Quant Trading Platform: Challenger 2 Empirical Test Suite
  Focus: CQRS Read Purity, Extreme SQLite Concurrency & Resource Leak Verification
===============================================================================
Author: Challenger 2 (Empirical Challenger - CQRS & Persistence Leak Specialist)
Targets:
  1. CQRS Read Query Purity (Zero yf.download, Zero UPDATE/Write DML, Latency < 25ms)
  2. Extreme SQLite Concurrency Stress (50 Readers + 10 Multi-Writers, Zero Lock Timeouts)
  3. archive_daily_recommendations Durability & Transaction Atomicity
  4. Native Windows OS Handle Leak Verification (ManagedConnection vs SQLite Context)
  5. ASGI Web Server Live End-to-End Concurrency & Latency SLAs
===============================================================================
"""

import os
import sys
import time
import json
import sqlite3
import threading
import ctypes
import ctypes.wintypes
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import pandas as pd

# Windows UTF-8 stdout configuration
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

CHALLENGER_DB = os.path.join(PROJECT_ROOT, "test_challenger2_empirical.db")

import db_manager
import server
from server import app
from al_sangmoo.infrastructure.persistence import (
    get_connection, init_database, add_portfolio_buy, record_portfolio_sell,
    reset_all_holdings, get_live_portfolio, sync_portfolio_prices,
    archive_daily_recommendations, save_macro_history_record,
    save_recommendation_matrix_record, get_recommendations_matrix,
    get_daily_recommendation_history, get_recommendation_streaks
)

# ---------------------------------------------------------------------------
# Windows Native Handle Counter
# ---------------------------------------------------------------------------
def get_native_process_handle_count() -> int:
    """Returns current process handle count via Win32 API."""
    if sys.platform.startswith('win'):
        try:
            k32 = ctypes.windll.kernel32
            k32.GetProcessHandleCount.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(ctypes.wintypes.DWORD)]
            k32.GetProcessHandleCount.restype = ctypes.wintypes.BOOL
            k32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
            cnt = ctypes.wintypes.DWORD()
            if k32.GetProcessHandleCount(k32.GetCurrentProcess(), ctypes.byref(cnt)):
                return cnt.value
        except Exception as e:
            print(f"[WARN] Failed to get native handle count: {e}")
    return 0

# ---------------------------------------------------------------------------
# DB Test Isolation Fixtures
# ---------------------------------------------------------------------------
def setup_challenger_db():
    os.environ["AL_SANGMOO_DB_PATH"] = CHALLENGER_DB
    cleanup_challenger_db()
    init_database()

def cleanup_challenger_db():
    for f in [CHALLENGER_DB, f"{CHALLENGER_DB}-wal", f"{CHALLENGER_DB}-shm"]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass

# ---------------------------------------------------------------------------
# In-Memory ASGI Test Client Helper
# ---------------------------------------------------------------------------
async def asgi_request(app, method: str, path: str, body: bytes = b""):
    scope = {
        'type': 'http',
        'http_version': '1.1',
        'method': method.upper(),
        'path': path,
        'raw_path': path.encode('ascii'),
        'query_string': b'',
        'headers': [],
        'client': ('127.0.0.1', 50000),
        'server': ('127.0.0.1', 8000),
    }
    status_code = 500
    response_body = []
    async def receive():
        return {'type': 'http.request', 'body': body, 'more_body': False}
    async def send(message):
        nonlocal status_code, response_body
        if message['type'] == 'http.response.start':
            status_code = message['status']
        elif message['type'] == 'http.response.body':
            response_body.append(message.get('body', b''))
    await app(scope, receive, send)
    return status_code, b"".join(response_body)


# ===========================================================================
# TEST SUITE 1: CQRS READ QUERY PURITY & PERFORMANCE
# ===========================================================================

def test_cqrs_purity_zero_yf_download():
    """Verify get_live_portfolio() strictly never invokes yfinance network queries."""
    print("\n[CHALLENGER 1.1] Testing CQRS Read Query Purity (Zero yf.download)...")
    setup_challenger_db()
    
    # Populate portfolio with 10 test positions
    for i in range(10):
        add_portfolio_buy(f"TICKER_{i}", 100.0 + i, 5.0, "2026-08-20")
        
    def poison_yf_download(*args, **kwargs):
        raise RuntimeError("VIOLATION: yf.download called inside CQRS read query!")
        
    def poison_yf_ticker(*args, **kwargs):
        raise RuntimeError("VIOLATION: yf.Ticker called inside CQRS read query!")

    with patch("yfinance.download", side_effect=poison_yf_download), \
         patch("yfinance.Ticker", side_effect=poison_yf_ticker):
        # Execute read query
        portfolio = get_live_portfolio()
        
    assert "holdings" in portfolio
    assert len(portfolio["holdings"]) == 10
    assert portfolio["total_invested"] == sum((100.0 + i) * 5.0 for i in range(10))
    print("  [PASS] get_live_portfolio() executed without calling any yfinance methods.")


def test_cqrs_purity_zero_write_dml_statements():
    """Verify get_live_portfolio() executes ONLY SELECT queries, zero UPDATE/INSERT/DELETE."""
    print("\n[CHALLENGER 1.2] Testing CQRS Read Query SQL Trace (Zero Write DML)...")
    setup_challenger_db()
    
    for i in range(5):
        add_portfolio_buy(f"TK_{i}", 50.0 + i * 10, 10.0, "2026-08-20")
        
    captured_statements = []
    
    # Trace hook to capture raw SQL statements
    orig_get_connection = db_manager.get_connection
    def tracing_get_connection(*args, **kwargs):
        conn = orig_get_connection(*args, **kwargs)
        def trace_callback(sql):
            clean_sql = sql.strip().upper()
            captured_statements.append(clean_sql)
        conn.set_trace_callback(trace_callback)
        return conn

    with patch("al_sangmoo.infrastructure.persistence.get_connection", side_effect=tracing_get_connection):
        portfolio = get_live_portfolio()
        
    assert len(captured_statements) > 0, "Expected SQL statements to be captured"
    
    write_verbs = ["UPDATE ", "INSERT ", "DELETE ", "REPLACE ", "DROP ", "ALTER ", "CREATE "]
    disallowed = []
    for stmt in captured_statements:
        # Ignore table/index creation in init_database if called, but focus on the portfolio query
        for verb in write_verbs:
            if (
                stmt.startswith(verb)
                and "CREATE TABLE IF NOT EXISTS" not in stmt
                and "CREATE UNIQUE INDEX IF NOT EXISTS" not in stmt
                and "CREATE INDEX IF NOT EXISTS" not in stmt
            ):
                disallowed.append(stmt)
                
    print(f"  --> Captured SQL trace count: {len(captured_statements)} statements.")
    for s in captured_statements:
        print(f"      SQL: {s[:80]}...")
        
    assert len(disallowed) == 0, f"VIOLATION: get_live_portfolio() executed write DML statements: {disallowed}"
    print("  [PASS] get_live_portfolio() issued 0 UPDATE/INSERT/DELETE statements during read.")


def test_cqrs_read_latency_sla():
    """Verify get_live_portfolio() executes consistently in < 25ms under realistic data load."""
    print("\n[CHALLENGER 1.3] Testing CQRS Read Query Latency SLA (< 25ms)...")
    setup_challenger_db()
    
    # Populate with 50 holdings
    for i in range(50):
        add_portfolio_buy(f"STK_{i:03d}", 100.0 + i, float(i + 1), "2026-08-20")
        
    # Warmup read to load schema & connection pools
    get_live_portfolio()

    latencies_ms = []
    # Execute 500 reads
    for _ in range(500):
        t0 = time.perf_counter()
        port = get_live_portfolio()
        t1 = time.perf_counter()
        latencies_ms.append((t1 - t0) * 1000.0)
        assert len(port["holdings"]) == 50
        
    latencies_sorted = sorted(latencies_ms)
    avg_lat = sum(latencies_ms) / len(latencies_ms)
    p50_lat = latencies_sorted[int(len(latencies_ms) * 0.50)]
    p95_lat = latencies_sorted[int(len(latencies_ms) * 0.95)]
    p99_lat = latencies_sorted[int(len(latencies_ms) * 0.99)]
    max_lat = max(latencies_ms)
    
    print(f"  --> Latency benchmark (50 holdings, 500 runs):")
    print(f"      Avg: {avg_lat:.2f}ms | p50: {p50_lat:.2f}ms | p95: {p95_lat:.2f}ms | p99: {p99_lat:.2f}ms | Max: {max_lat:.2f}ms")
    
    assert avg_lat < 15.0, f"Average latency {avg_lat:.2f}ms exceeded 15ms threshold!"
    assert p95_lat < 25.0, f"p95 latency {p95_lat:.2f}ms exceeded 25ms SLA!"
    assert p99_lat < 40.0, f"p99 latency {p99_lat:.2f}ms exceeded 40ms SLA!"
    print("  [PASS] CQRS read query latency is ultra-fast and easily satisfies < 25ms SLA.")


# ===========================================================================
# TEST SUITE 2: MASSIVE SQLITE CONCURRENCY STRESS (50 READERS + 10 WRITERS)
# ===========================================================================

def test_extreme_sqlite_concurrency_contention():
    """
    Stress-test SQLite WAL mode under massive multi-threaded contention:
    50 reader threads + 10 writer threads executing concurrently for 3 seconds.
    Verifies 0 database lock errors, 0 data corruption, and perfect transaction isolation.
    """
    print("\n[CHALLENGER 2.1] Testing Extreme SQLite Concurrency (50 Readers + 10 Writers)...")
    setup_challenger_db()
    
    # Prepopulate seed data
    for i in range(10):
        add_portfolio_buy(f"SEED_{i}", 100.0, 10.0, "2026-08-20")
    archive_daily_recommendations("2026-08-20", [{"ticker": "NVDA", "price": 120.0}], [], [])
    
    stop_event = threading.Event()
    read_stats = {"count": 0, "errors": []}
    write_stats = {"count": 0, "errors": []}
    lock = threading.Lock()
    
    def reader_task(reader_id: int):
        local_count = 0
        while not stop_event.is_set():
            try:
                op = local_count % 5
                if op == 0:
                    res = get_live_portfolio()
                    assert "holdings" in res
                elif op == 1:
                    res = get_recommendations_matrix()
                    assert isinstance(res, list)
                elif op == 2:
                    res = get_daily_recommendation_history()
                    assert isinstance(res, list)
                elif op == 3:
                    res = get_recommendation_streaks()
                    assert isinstance(res, dict)
                elif op == 4:
                    with get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT COUNT(*) FROM my_portfolio")
                        cursor.fetchone()
                local_count += 1
            except Exception as e:
                with lock:
                    read_stats["errors"].append((reader_id, str(e)))
            time.sleep(0.001)
        with lock:
            read_stats["count"] += local_count

    def writer_task(writer_id: int):
        local_count = 0
        while not stop_event.is_set():
            try:
                w_type = writer_id % 4
                if w_type == 0:
                    # Writer adds positions
                    buy_id = add_portfolio_buy(f"W_{writer_id}_{local_count}", 100.0 + local_count, 1.0, "2026-08-23")
                    assert buy_id > 0
                elif w_type == 1:
                    # Writer sells positions
                    record_portfolio_sell(1, 105.0, "2026-08-23", f"Writer {writer_id} sell {local_count}")
                elif w_type == 2:
                    # Writer archives recommendations
                    d_str = f"2026-08-{(writer_id + local_count) % 28 + 1:02d}"
                    archive_daily_recommendations(
                        d_str,
                        [{"ticker": f"REC_{writer_id}", "price": 150.0}],
                        [{"ticker": f"STRAT1_{writer_id}", "price": 200.0}],
                        []
                    )
                elif w_type == 3:
                    # Writer syncs prices (mocking yfinance)
                    with patch("yfinance.download", return_value=pd.DataFrame({"Close": [125.0]})):
                        sync_portfolio_prices()
                local_count += 1
            except Exception as e:
                with lock:
                    write_stats["errors"].append((writer_id, str(e)))
            time.sleep(0.003)
        with lock:
            write_stats["count"] += local_count

    # 50 Reader Threads + 10 Writer Threads = 60 Concurrent Threads
    readers = [threading.Thread(target=reader_task, args=(i,)) for i in range(50)]
    writers = [threading.Thread(target=writer_task, args=(i,)) for i in range(10)]
    
    t_start = time.time()
    with patch("yfinance.download", return_value=pd.DataFrame({"Close": [125.0]})):
        for t in readers + writers:
            t.start()
            
        time.sleep(3.0)
        stop_event.set()
        
        for t in readers + writers:
            t.join()
        
    duration = time.time() - t_start
    
    total_reads = read_stats["count"]
    total_writes = write_stats["count"]
    total_read_errs = len(read_stats["errors"])
    total_write_errs = len(write_stats["errors"])
    
    print(f"  --> Concurrency Results over {duration:.2f}s:")
    print(f"      Total Successful Reads:  {total_reads:,} ({total_reads / duration:.0f} ops/sec)")
    print(f"      Total Successful Writes: {total_writes:,} ({total_writes / duration:.0f} ops/sec)")
    print(f"      Read Errors:  {total_read_errs} {read_stats['errors'][:3] if total_read_errs else ''}")
    print(f"      Write Errors: {total_write_errs} {write_stats['errors'][:3] if total_write_errs else ''}")
    
    assert total_read_errs == 0, f"Encountered {total_read_errs} read errors during concurrency stress!"
    assert total_write_errs == 0, f"Encountered {total_write_errs} write errors during concurrency stress!"
    assert total_reads > 500, f"Expected > 500 reads, got {total_reads}"
    assert total_writes > 100, f"Expected > 100 writes, got {total_writes}"
    
    # Verify SQLite DB integrity
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        res = cursor.fetchone()[0]
        assert res == "ok", f"Integrity check failed: {res}"
        
    print("  [PASS] 60 concurrent threads (50 readers + 10 writers) completed with 0 lock timeouts and 100% DB integrity.")


# ===========================================================================
# TEST SUITE 3: archive_daily_recommendations DURABILITY & ATOMICITY
# ===========================================================================

def test_archive_daily_recommendations_immediate_durability():
    """Verify archive_daily_recommendations() commits immediately with cross-connection visibility."""
    print("\n[CHALLENGER 3.1] Testing archive_daily_recommendations Durability & Visibility...")
    setup_challenger_db()
    
    today = "2026-08-23"
    dual_picks = [{"ticker": "NVDA", "price": 130.50, "target_price": 150.0, "stop_price": 125.0}]
    strat1_picks = [{"ticker": "AAPL", "price": 225.00, "target_price": 250.0, "stop_price": 215.0}]
    strat2_picks = [
        {"ticker": "MSFT", "price": 420.00, "target_price": 460.0, "stop_price": 405.0},
        {"ticker": "AMZN", "price": 180.00, "target_price": 200.0, "stop_price": 172.0}
    ]
    
    saved_count = archive_daily_recommendations(today, dual_picks, strat1_picks, strat2_picks)
    assert saved_count == 4, f"Expected return count 4, got {saved_count}"
    
    # Open completely separate raw SQLite connection without using persistence module
    raw_conn = sqlite3.connect(CHALLENGER_DB)
    raw_conn.row_factory = sqlite3.Row
    cursor = raw_conn.cursor()
    cursor.execute("SELECT ticker, type, entry_price, target_price, stop_loss_price, partial_tp_price FROM trades WHERE date = ?", (today,))
    rows = cursor.fetchall()
    raw_conn.close()
    
    assert len(rows) == 4, f"Immediate cross-connection read expected 4 rows, found {len(rows)}"
    
    by_ticker = {r["ticker"]: dict(r) for r in rows}
    assert by_ticker["NVDA"]["type"] == "DUAL_5_STAR"
    assert by_ticker["NVDA"]["entry_price"] == 130.50
    assert by_ticker["AAPL"]["type"] == "STRAT1_PULLBACK"
    assert by_ticker["MSFT"]["type"] == "STRAT2_SNIPER"
    assert by_ticker["AMZN"]["type"] == "STRAT2_SNIPER"
    
    # Test Upsert / Idempotence (re-archiving same day with updated prices)
    updated_dual = [{"ticker": "NVDA", "price": 135.00, "target_price": 155.0, "stop_price": 128.0}]
    saved_count_2 = archive_daily_recommendations(today, updated_dual, [], [])
    assert saved_count_2 == 1
    
    raw_conn = sqlite3.connect(CHALLENGER_DB)
    raw_conn.row_factory = sqlite3.Row
    cursor = raw_conn.cursor()
    cursor.execute("SELECT ticker, entry_price FROM trades WHERE date = ? AND ticker = 'NVDA'", (today,))
    nvda_row = cursor.fetchone()
    raw_conn.close()
    
    assert nvda_row["entry_price"] == 135.00, "Upsert failed to update entry_price on duplicate date+ticker"
    print("  [PASS] archive_daily_recommendations commits immediately and supports idempotent upserts.")


def test_archive_daily_recommendations_atomicity():
    """Verify archive_daily_recommendations atomicity: all rows commit or entire batch rolls back."""
    print("\n[CHALLENGER 3.2] Testing archive_daily_recommendations Transaction Atomicity...")
    setup_challenger_db()
    
    date_str = "2026-08-25"
    malformed_picks = [
        {"ticker": "VALID1", "price": 100.0},
        {"ticker": None, "price": "INVALID_PRICE_TYPE_NOT_A_FLOAT"}, # Trigger TypeError/ValueError
        {"ticker": "VALID2", "price": 200.0}
    ]
    
    try:
        archive_daily_recommendations(date_str, malformed_picks, [], [])
        assert False, "Expected ValueError / TypeError on invalid price"
    except Exception:
        pass
        
    # Verify 0 rows inserted for this date due to atomic rollback
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM trades WHERE date = ?", (date_str,))
        count = cursor.fetchone()[0]
        assert count == 0, f"Atomicity violation: found {count} partial rows committed!"
        
    print("  [PASS] Transaction atomicity verified: partial failure rolls back cleanly with 0 dangling rows.")


# ===========================================================================
# TEST SUITE 4: NATIVE OS HANDLE LEAK RESILIENCE
# ===========================================================================

def test_zero_handle_leaks_high_frequency_persistence():
    """
    Stress-test OS file handle allocation across 500 persistence transactions.
    Verifies ManagedConnection ensures exact 0 OS handle leaks.
    """
    print("\n[CHALLENGER 4.1] Testing High-Frequency OS Handle Leaks...")
    setup_challenger_db()
    
    # Warm up runtime
    for _ in range(20):
        with get_connection() as conn:
            conn.execute("SELECT 1").fetchone()
            
    initial_handles = get_native_process_handle_count()
    print(f"  --> Baseline OS Handle Count: {initial_handles}")
    
    # Execute 500 operations across all persistence endpoints
    for i in range(100):
        add_portfolio_buy(f"LEAK_TK_{i}", 100.0 + i, 1.0, "2026-08-23")
        get_live_portfolio()
        archive_daily_recommendations(f"2026-08-{(i%28)+1:02d}", [{"ticker": f"LEAK_TK_{i}", "price": 100.0}], [], [])
        record_portfolio_sell(i + 1, 110.0, "2026-08-23", "Handle test exit")
        with patch("yfinance.download", return_value=pd.DataFrame({"Close": [115.0]})):
            sync_portfolio_prices()
            
    final_handles = get_native_process_handle_count()
    handle_delta = final_handles - initial_handles
    print(f"  --> Final OS Handle Count: {final_handles} (Delta: {handle_delta:+d} handles after 500 DB ops)")
    
    # On Windows, threadpool workers or small internal OS runtime tables might allocate 0-2 handles,
    # but unclosed SQLite handles would allocate > 500 handles.
    assert handle_delta <= 3, f"CRITICAL RESOURCE LEAK: Handle count grew by {handle_delta} handles!"
    print("  [PASS] Zero OS file handle leaks verified under 500 high-frequency persistence calls.")


def test_zero_handle_leaks_on_exceptions():
    """Verify ManagedConnection releases handles even when database operations raise exceptions."""
    print("\n[CHALLENGER 4.2] Testing Handle Cleanup on Transaction Exceptions...")
    setup_challenger_db()
    
    initial_handles = get_native_process_handle_count()
    
    for i in range(100):
        try:
            with get_connection() as conn:
                conn.execute("SELECT * FROM nonexistent_table_for_error_test").fetchall()
        except sqlite3.OperationalError:
            pass
            
    final_handles = get_native_process_handle_count()
    handle_delta = final_handles - initial_handles
    print(f"  --> Exception Handle Count Delta: {handle_delta:+d} handles after 100 caught exceptions")
    
    assert handle_delta <= 2, f"Exception cleanup failure: Leaked {handle_delta} handles on error paths!"
    print("  [PASS] ManagedConnection guaranteed handle closure on exception paths.")


# ===========================================================================
# TEST SUITE 5: ASGI SERVER CONCURRENT END-TO-END VERIFICATION
# ===========================================================================

def test_server_portfolio_endpoint_concurrency_and_latency():
    """Verify ASGI GET /api/portfolio endpoint responds in < 25ms during background scan."""
    print("\n[CHALLENGER 5.1] Testing ASGI Portfolio Endpoint Latency Under Background Scan...")
    setup_challenger_db()
    
    for i in range(20):
        add_portfolio_buy(f"SRV_TK_{i}", 150.0, 10.0, "2026-08-20")
        
    import asyncio
    
    async def run_asgi_stress():
        server._is_scanning = False
        
        def fake_heavy_scan():
            time.sleep(0.5)
            return {"charts": {}, "macro": {}, "tier1": [], "tier2": []}
            
        with patch("server.build_dashboard_data", side_effect=fake_heavy_scan):
            
            # Start background scan
            status, _ = await asgi_request(app, "POST", "/api/scan_now")
            assert status == 200
            assert server._is_scanning is True
            
            # Test 10 consecutive reads during background scan
            latencies = []
            for _ in range(10):
                t0 = time.perf_counter()
                st, body = await asgi_request(app, "GET", "/api/portfolio")
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                assert st == 200
                data = json.loads(body.decode("utf-8"))
                assert "holdings" in data
                assert len(data["holdings"]) == 20
                latencies.append(elapsed_ms)

            # Wait for background scan to finish
            for _ in range(50):
                if not server._is_scanning:
                    break
                await asyncio.sleep(0.05)
                
            return latencies

    latencies = asyncio.run(run_asgi_stress())
    avg_lat = sum(latencies) / len(latencies)
    max_lat = max(latencies)
    print(f"  --> ASGI Endpoint Latency (10 calls during scan): Avg {avg_lat:.2f}ms | Max {max_lat:.2f}ms")
    
    assert avg_lat < 25.0, f"Average endpoint latency {avg_lat:.2f}ms exceeded 25ms SLA!"
    assert max_lat < 50.0, f"Max endpoint latency {max_lat:.2f}ms exceeded 50ms SLA!"
    print("  [PASS] ASGI /api/portfolio endpoint latency SLA (< 25ms avg) verified during background scan.")


# ===========================================================================
# MAIN RUNNER
# ===========================================================================

if __name__ == "__main__":
    print("===============================================================================")
    print("  CHALLENGER 2: EMPIRICAL CQRS PURITY & PERSISTENCE STRESS TEST RUNNER")
    print("===============================================================================")
    
    t_suite_start = time.time()
    
    # 1. CQRS Read Purity
    test_cqrs_purity_zero_yf_download()
    test_cqrs_purity_zero_write_dml_statements()
    test_cqrs_read_latency_sla()
    
    # 2. Extreme Concurrency Stress
    test_extreme_sqlite_concurrency_contention()
    
    # 3. Durability & Atomicity
    test_archive_daily_recommendations_immediate_durability()
    test_archive_daily_recommendations_atomicity()
    
    # 4. Handle Leak Resilience
    test_zero_handle_leaks_high_frequency_persistence()
    test_zero_handle_leaks_on_exceptions()
    
    # 5. End-to-End Server Latency
    test_server_portfolio_endpoint_concurrency_and_latency()
    
    cleanup_challenger_db()
    
    total_elapsed = time.time() - t_suite_start
    print("\n===============================================================================")
    print(f"  ALL CHALLENGER 2 ADVERSARIAL TESTS PASSED (100% GREEN in {total_elapsed:.2f}s)")
    print("===============================================================================")
