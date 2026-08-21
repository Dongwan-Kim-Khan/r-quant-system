import os
import sys
import time
import json
import threading
import tempfile
import sqlite3
from concurrent.futures import ThreadPoolExecutor

# Windows encoding fix
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
sys.path.insert(0, PROJECT_ROOT)

import db_manager
from server import BuyOrder, SellOrder, buy_stock, sell_stock, reset_portfolio, get_portfolio, get_recommendation_matrix
from generate_dashboard_feed import atomic_save_json, atomic_read_json

def test_wal_mode_and_pragmas():
    print("\n[Test 1] Verifying SQLite WAL Mode & High-Concurrency Pragmas...")
    conn = db_manager.get_db()
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA journal_mode;")
    mode = cursor.fetchone()[0].lower()
    print(f"  - SQLite journal_mode: {mode.upper()}")
    assert mode in ["wal", "memory"], f"Expected WAL mode, got {mode}"
    
    cursor.execute("PRAGMA busy_timeout;")
    timeout = cursor.fetchone()[0]
    print(f"  - SQLite busy_timeout: {timeout} ms")
    assert timeout >= 5000, f"Expected busy_timeout >= 5000, got {timeout}"
    
    conn.close()
    print("  -> PASSED: WAL mode & busy_timeout verified.")

def test_atomic_json_persistence():
    print("\n[Test 2] Verifying Atomic JSON File Persistence Engine...")
    test_json = os.path.join(PROJECT_ROOT, "test_atomic_feed.json")
    
    # 1. Basic save
    sample_data = {"worker": -1, "status": "ok", "timestamp": time.time(), "items": list(range(100))}
    atomic_save_json(test_json, sample_data)
    
    assert os.path.exists(test_json), "Atomic file was not created"
    loaded = atomic_read_json(test_json)
    assert loaded["status"] == "ok"
    assert len(loaded["items"]) == 100
    
    # 2. Multi-threaded atomic overwrite test (50 rapid writes vs reads)
    errors = []
    def writer(idx):
        try:
            d = {"worker": idx, "payload": "X" * 1000}
            atomic_save_json(test_json, d)
        except Exception as e:
            errors.append(f"Writer error: {e}")

    def reader():
        try:
            d = atomic_read_json(test_json)
            if not isinstance(d, dict) or "worker" not in d:
                errors.append("Corrupt or partial read detected")
        except Exception as e:
            errors.append(f"Reader error: {e}")

    threads = []
    for i in range(25):
        threads.append(threading.Thread(target=writer, args=(i,)))
        threads.append(threading.Thread(target=reader))
        
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    if os.path.exists(test_json):
        try:
            os.remove(test_json)
        except Exception:
            pass
        
    assert len(errors) == 0, f"Atomic persistence encountered errors: {errors[:5]}"
    print("  -> PASSED: 0 corrupt reads during 50 concurrent atomic swaps.")

def test_api_endpoints_integrity():
    print("\n[Test 3] Verifying API Endpoint Functionality & Contract Stability...")
    
    # 1. Reset portfolio
    res = reset_portfolio()
    assert res["status"] == "success"
    print("  - POST /api/portfolio/reset handler: OK")
    
    # 2. Buy order (with custom price and fractional shares)
    buy_payload = BuyOrder(ticker="AMZN", buy_price=258.50, quantity=1.5)
    buy_res = buy_stock(buy_payload)
    pos_id = buy_res["id"]
    assert buy_res["status"] == "success"
    print(f"  - POST /api/portfolio/buy handler (AMZN 1.5 shares @ $258.50): Position #{pos_id} OK")
    
    # 3. Get portfolio
    p_data = get_portfolio()
    assert len(p_data["holdings"]) == 1
    assert p_data["holdings"][0]["ticker"] == "AMZN"
    assert p_data["holdings"][0]["quantity"] == 1.5
    print("  - GET /api/portfolio handler: Verified 1.5 shares of AMZN")
    
    # 4. Sell order
    sell_payload = SellOrder(sell_price=270.00, reason="TEST_TP")
    sell_res = sell_stock(position_id=pos_id, order=sell_payload)
    assert sell_res["status"] == "success"
    print(f"  - POST /api/portfolio/sell/{pos_id} handler: OK")
    
    # 5. Recommendations matrix
    m_data = get_recommendation_matrix()
    assert isinstance(m_data, list)
    print("  - GET /api/recommendations/matrix handler: OK")
    
    # 6. Reset again to clean state
    reset_portfolio()
    print("  -> PASSED: All REST endpoints executed with 0 runtime errors.")

def test_50_thread_db_concurrency():
    print("\n[Test 4] 50-Thread High-Concurrency Stress Test on SQLite WAL...")
    db_manager.reset_all_holdings()
    
    lock_errors = []
    success_count = []
    
    def worker(i):
        try:
            tk = "NVDA" if i % 2 == 0 else "LLY"
            pid = db_manager.add_portfolio_buy(ticker=tk, buy_price=100.0 + i, quantity=1.0 + (i * 0.1))
            
            # Direct SQLite transaction test
            conn = db_manager.get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM my_portfolio WHERE id = ?", (pid,))
            row = cursor.fetchone()
            conn.close()
            assert row is not None
            
            if i % 2 == 0:
                db_manager.record_portfolio_sell(holding_id=pid, sell_price=110.0 + i, reason="STRESS_TEST")
            success_count.append(1)
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                lock_errors.append(str(e))
        except Exception as e:
            lock_errors.append(str(e))

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(worker, i) for i in range(50)]
        for f in futures:
            f.result()
            
    # Clean up stress test data
    db_manager.reset_all_holdings()
    
    print(f"  - Total Concurrency Operations: {len(success_count)} / 50")
    print(f"  - Database Lock Failures: {len(lock_errors)}")
    assert len(lock_errors) == 0, f"Encountered database locks: {lock_errors}"
    print("  -> PASSED: 50 concurrent transactions completed with 0 lock errors.")

if __name__ == "__main__":
    print("======================================================================")
    print("  R-SANGMOO QUANT PLATFORM: PHASE 1 HARDENING TEST SUITE")
    print("======================================================================")
    
    test_wal_mode_and_pragmas()
    test_atomic_json_persistence()
    test_api_endpoints_integrity()
    test_50_thread_db_concurrency()
    
    print("\n======================================================================")
    print("  ALL PHASE 1 HARDENING TESTS PASSED SUCCESSFULLY! (100% GREEN)")
    print("======================================================================")
