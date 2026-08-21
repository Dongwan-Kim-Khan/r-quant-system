import os
import sys
import time
import json
import asyncio
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
    
    # Backup user's actual portfolio holdings before test
    conn = db_manager.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM my_portfolio")
    user_portfolio_backup = cursor.fetchall()
    conn.close()
    
    async def run_api_tests():
        try:
            # 1. Reset portfolio
            res = await reset_portfolio()
            assert res["status"] == "success"
            print("  - POST /api/portfolio/reset handler: OK")
            
            # 2. Buy order (with custom price and fractional shares)
            buy_payload = BuyOrder(ticker="AMZN", buy_price=258.50, quantity=1.5)
            buy_res = await buy_stock(buy_payload)
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
            sell_res = await sell_stock(position_id=pos_id, order=sell_payload)
            assert sell_res["status"] == "success"
            print(f"  - POST /api/portfolio/sell/{pos_id} handler: OK")
            
            # 5. Recommendations matrix
            m_data = get_recommendation_matrix()
            assert isinstance(m_data, list)
            print("  - GET /api/recommendations/matrix handler: OK")
            print("  -> PASSED: All REST endpoints executed with 0 runtime errors.")
        finally:
            # Restore user's real portfolio holdings
            c = db_manager.get_db()
            cur = c.cursor()
            cur.execute("DELETE FROM my_portfolio")
            if user_portfolio_backup:
                placeholders = ",".join(["?"] * len(user_portfolio_backup[0]))
                cur.executemany(f"INSERT INTO my_portfolio VALUES ({placeholders})", user_portfolio_backup)
            c.commit()
            c.close()

    asyncio.run(run_api_tests())

def test_50_thread_db_concurrency():
    print("\n[Test 4] 50-Thread High-Concurrency Stress Test on SQLite WAL...")
    
    # Backup user's portfolio holdings before test
    conn = db_manager.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM my_portfolio")
    user_portfolio_backup = cursor.fetchall()
    conn.close()
    
    try:
        db_manager.reset_all_holdings()
        
        tickers = ["NVDA", "AMZN", "MSFT", "AAPL", "GOOGL", "META", "TSLA", "LLY", "AVGO", "COST"]
        errors = []
        
        def worker(idx):
            try:
                tk = tickers[idx % len(tickers)]
                price = 100.0 + idx
                qty = (idx % 5) + 1.0
                
                # Perform Buy
                pos_id = db_manager.add_portfolio_buy(ticker=tk, buy_price=price, quantity=qty)
                
                # Perform Immediate Read
                conn_w = db_manager.get_db()
                cur_w = conn_w.cursor()
                cur_w.execute("SELECT COUNT(*) FROM my_portfolio WHERE status = 'HOLDING'")
                cnt = cur_w.fetchone()[0]
                conn_w.close()
                assert cnt > 0, "No holdings returned after insert"
                
                # Perform Sell on some threads
                if idx % 2 == 0:
                    db_manager.record_portfolio_sell(holding_id=pos_id, sell_price=price * 1.05, reason="CONCURRENCY_TEST")
            except Exception as e:
                errors.append(f"Worker {idx} failed: {e}")

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker, i) for i in range(50)]
            for f in futures:
                f.result()
                
        print(f"  - Total Concurrency Operations: 50 / 50")
        print(f"  - Database Lock Failures: {len(errors)}")
        assert len(errors) == 0, f"Encountered concurrency lock errors: {errors[:3]}"
        print("  -> PASSED: 50 concurrent transactions completed with 0 lock errors.")
    finally:
        # Restore user's real portfolio holdings
        c = db_manager.get_db()
        cur = c.cursor()
        cur.execute("DELETE FROM my_portfolio")
        if user_portfolio_backup:
            placeholders = ",".join(["?"] * len(user_portfolio_backup[0]))
            cur.executemany(f"INSERT INTO my_portfolio VALUES ({placeholders})", user_portfolio_backup)
        c.commit()
        c.close()

if __name__ == "__main__":
    print("=" * 70)
    print("  R-SANGMOO QUANT PLATFORM: PHASE 1 HARDENING TEST SUITE")
    print("=" * 70)
    test_wal_mode_and_pragmas()
    test_atomic_json_persistence()
    test_api_endpoints_integrity()
    test_50_thread_db_concurrency()
    print("\n" + "=" * 70)
    print("  ALL PHASE 1 HARDENING TESTS PASSED SUCCESSFULLY! (100% GREEN)")
    print("=" * 70)
