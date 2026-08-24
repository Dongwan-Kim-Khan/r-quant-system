"""
Independent Forensic Verification & Stress-Test Script for Phase 5.2.
Author: Forensic Auditor (auditor_phase5_2_1)
"""
import asyncio
import time
import os
import sys
import hashlib
import sqlite3
from unittest.mock import patch, MagicMock

# Fix path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

TEST_DB = os.path.join(BASE_DIR, "test_auditor_forensic.db")
os.environ["AL_SANGMOO_DB_PATH"] = TEST_DB

from al_sangmoo.infrastructure.persistence import (
    get_connection, init_database, add_portfolio_buy, get_live_portfolio,
    sync_portfolio_prices, archive_daily_recommendations, ManagedConnection
)
from al_sangmoo.api.hub import WebSocketBroadcastHub
import server

def clean_db():
    for f in [TEST_DB, f"{TEST_DB}-wal", f"{TEST_DB}-shm"]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass

async def test_websocket_hung_client_isolation():
    print("[AUDIT CHECK 1] WebSocket Hung Client Isolation & Fast Path...")
    hub = WebSocketBroadcastHub(max_connections=10)
    
    received_fast = []
    
    class FastClient:
        async def accept(self): pass
        async def send_json(self, data):
            received_fast.append((time.time(), data))
        async def close(self, code=1000, reason=""): pass

    class HungClient:
        async def accept(self): pass
        async def send_json(self, data):
            # Hang forever until cancelled
            await asyncio.sleep(100.0)
        async def close(self, code=1000, reason=""): pass

    fast = FastClient()
    hung = HungClient()
    await hub.connect(fast)
    await hub.connect(hung)
    
    t0 = time.time()
    await hub.broadcast("test_ping", {"value": 42})
    elapsed = time.time() - t0
    
    assert len(received_fast) == 1, "Fast client did not receive message!"
    assert elapsed >= 2.0 and elapsed < 2.5, f"Broadcast should time out hung client around 2.0s, elapsed: {elapsed:.2f}s"
    assert hung not in hub.active_connections, "Hung client must be pruned!"
    assert fast in hub.active_connections, "Fast client must remain!"
    print(f"  --> PASS: Broadcast completed in {elapsed:.2f}s, hung client pruned, fast client preserved.")

async def test_scan_storm_and_exception_recovery():
    print("[AUDIT CHECK 2] Scan Storm Locking & Uncaught Exception Recovery...")
    server._is_scanning = False
    
    # 1. Test lock under exception
    with patch("al_sangmoo_daily_bot.scan_and_select_2x2x2", side_effect=ValueError("Simulated crash")):
        await server._run_background_scan_pipeline()
        assert server._is_scanning is False, "_is_scanning must be False even after exception in worker"
        
    print("  --> PASS: Exception recovery verified, _is_scanning is False.")

def test_managed_connection_exception_safety():
    print("[AUDIT CHECK 3] ManagedConnection Exception & Rollback Handle Closure...")
    clean_db()
    init_database()
    
    for i in range(50):
        try:
            with get_connection() as conn:
                conn.execute("INSERT INTO my_portfolio (ticker, buy_date, buy_price, quantity, total_cost) VALUES ('FAIL', '2026-08-23', 10, 1, 10)")
                if i % 2 == 0:
                    raise RuntimeError("Forced transaction failure")
        except RuntimeError:
            pass
            
    # Check that surviving rows are exactly 25
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM my_portfolio WHERE ticker = 'FAIL'")
        count = cursor.fetchone()[0]
        assert count == 25, f"Expected 25 committed rows, got {count}"
        
    print("  --> PASS: 50 transactions with 25 exceptions correctly rolled back and released.")

def test_sha256_mirror_parity():
    print("[AUDIT CHECK 4] HTML Dashboard SHA256 Exact Checksum Parity...")
    files = [
        os.path.join(BASE_DIR, "al_sangmoo_dashboard.html"),
        os.path.join(BASE_DIR, "html_dashboards", "01_알상무_통합_퀀트_대시보드.html"),
        os.path.join(BASE_DIR, "html_dashboards", "01_R상무_통합_퀀트_대시보드.html"),
        os.path.join(BASE_DIR, "HTML_대시보드_모음", "01_R상무_통합_퀀트_대시보드.html"),
    ]
    hashes = [hashlib.sha256(open(f, "rb").read()).hexdigest() for f in files]
    assert len(set(hashes)) == 1, f"Checksum divergence detected: {hashes}"
    print(f"  --> PASS: All 4 dashboard HTML files match SHA256: {hashes[0]}")

if __name__ == "__main__":
    clean_db()
    asyncio.run(test_websocket_hung_client_isolation())
    asyncio.run(test_scan_storm_and_exception_recovery())
    test_managed_connection_exception_safety()
    test_sha256_mirror_parity()
    clean_db()
    print("\n=======================================================")
    print("  ALL INDEPENDENT AUDITOR FORENSIC CHECKS PASSED (100%)")
    print("=======================================================")
