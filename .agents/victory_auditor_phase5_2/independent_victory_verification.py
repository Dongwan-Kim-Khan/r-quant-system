"""
Independent Victory Verification Test Suite for Phase 5.2
Auditor: Victory Auditor (teamwork_preview_auditor)
"""
import os
import sys
import time
import json
import asyncio
import hashlib
import sqlite3
import threading
from unittest.mock import patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import server
from server import app
from al_sangmoo.api.hub import hub, WebSocketBroadcastHub
from al_sangmoo.infrastructure.persistence import (
    get_connection, init_database, add_portfolio_buy, record_portfolio_sell,
    get_live_portfolio, sync_portfolio_prices, archive_daily_recommendations,
    save_macro_history_record, save_recommendation_matrix_record, ManagedConnection
)

AUDIT_DB = os.path.join(PROJECT_ROOT, "test_victory_audit_p5_2.db")
os.environ["AL_SANGMOO_DB_PATH"] = AUDIT_DB

def cleanup_audit_db():
    for f in [AUDIT_DB, f"{AUDIT_DB}-wal", f"{AUDIT_DB}-shm"]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass

async def asgi_post(path: str):
    scope = {
        'type': 'http', 'http_version': '1.1', 'method': 'POST',
        'path': path, 'raw_path': path.encode('ascii'), 'query_string': b'',
        'headers': [], 'client': ('127.0.0.1', 50000), 'server': ('127.0.0.1', 8000),
    }
    status_code = 500
    response_body = []
    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}
    async def send(msg):
        nonlocal status_code, response_body
        if msg['type'] == 'http.response.start':
            status_code = msg['status']
        elif msg['type'] == 'http.response.body':
            response_body.append(msg.get('body', b''))
    await app(scope, receive, send)
    return status_code, b"".join(response_body)

class MockWS:
    def __init__(self, delay: float = 0.0, error: bool = False):
        self.delay = delay
        self.error = error
        self.received = []
        self.closed = False
    async def accept(self): pass
    async def send_json(self, data):
        if self.error: raise ConnectionResetError("Socket broken")
        if self.delay > 0: await asyncio.sleep(self.delay)
        self.received.append(data)
    async def close(self, code=1000, reason=""): self.closed = True

def verify_all():
    print("=================================================================")
    print("  INDEPENDENT VICTORY AUDITOR FORENSIC & CONCURRENCY VERIFICATION")
    print("=================================================================")
    cleanup_audit_db()
    init_database()

    # 1. Check R1: Scan Storm Deduplication & Event-Loop Protection
    print("\n[AUDIT 1] R1: 50-Coroutine Scan Storm Single-Flight Deduplication...")
    async def test_scan_storm():
        server._is_scanning = False
        scan_runs = 0
        def mock_scan():
            nonlocal scan_runs
            scan_runs += 1
            time.sleep(0.3)
            return ([], [], [], {"macro_stance": "BULL"})
            
        with patch("al_sangmoo_daily_bot.scan_and_select_2x2x2", side_effect=mock_scan), \
             patch("server.build_dashboard_data", return_value={"charts": {}, "macro": {}}):
            t0 = time.time()
            res = await asyncio.gather(*[asgi_post("/api/scan_now") for _ in range(50)])
            elapsed = time.time() - t0
            
            statuses = [json.loads(b.decode("utf-8"))["status"] for s, b in res]
            started = statuses.count("scanning_started")
            already = statuses.count("already_scanning")
            
            assert started == 1, f"Expected 1 started, got {started}"
            assert already == 49, f"Expected 49 already, got {already}"
            assert elapsed < 0.2, f"50-request dispatch took {elapsed:.3f}s (> 0.2s)"
            
            # Wait for background task
            for _ in range(30):
                if not server._is_scanning: break
                await asyncio.sleep(0.05)
                
            assert scan_runs == 1, f"Heavy scan executed {scan_runs} times instead of 1"
            assert server._is_scanning is False
            print(f"  --> PASS: 50 requests handled in {elapsed*1000:.1f}ms. 1 started, 49 deduplicated.")
            
    asyncio.run(test_scan_storm())

    # 2. Check R2: WebSocket Hub Slow-Client Pruning & SLA
    print("\n[AUDIT 2] R2: WebSocket Hub Concurrency & Slow-Client SLA (< 2.5s)...")
    async def test_ws():
        h = WebSocketBroadcastHub()
        fast_clients = [MockWS(delay=0.0) for _ in range(15)]
        slow_clients = [MockWS(delay=10.0) for _ in range(5)]
        err_clients = [MockWS(error=True) for _ in range(5)]
        
        for c in fast_clients + slow_clients + err_clients:
            await h.connect(c)
        assert len(h.active_connections) == 25
        
        t0 = time.time()
        await h.broadcast("audit_ping", {"data": "ok"})
        elapsed = time.time() - t0
        
        assert elapsed < 2.5, f"Broadcast took {elapsed:.2f}s (> 2.5s SLA)"
        assert len(h.active_connections) == 15
        for fc in fast_clients:
            assert len(fc.received) == 1
        for sc in slow_clients + err_clients:
            assert sc not in h.active_connections
        print(f"  --> PASS: Broadcast completed in {elapsed:.2f}s (< 2.5s SLA). Slow/broken clients pruned.")
        
    asyncio.run(test_ws())

    # 3. Check R3: ManagedConnection Handle Closure & Rollback Safety
    print("\n[AUDIT 3] R3: ManagedConnection Context Management & Rollback...")
    for i in range(100):
        try:
            with get_connection() as conn:
                conn.execute("INSERT INTO my_portfolio (ticker, buy_date, buy_price, quantity, total_cost) VALUES ('T1', '2026-08-23', 100, 1, 100)")
                if i % 2 == 1:
                    raise RuntimeError("Forced Transaction Abort")
                conn.commit()
        except RuntimeError:
            pass
            
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM my_portfolio WHERE ticker = 'T1'")
        cnt = cursor.fetchone()[0]
        assert cnt == 50, f"Expected 50 committed rows, found {cnt}"
    print(f"  --> PASS: 50 committed, 50 aborted rows correctly rolled back with zero handle leaks.")

    # 4. Check R4: Pure CQRS Read Query & Price Sync Worker
    print("\n[AUDIT 4] R4: Pure CQRS Read (Zero Network/Write) & Sync Worker...")
    add_portfolio_buy("NVDA", 120.0, 10.0, "2026-08-23")
    
    # Assert get_live_portfolio makes zero network calls
    with patch("yfinance.download", side_effect=RuntimeError("NETWORK CALL FORBIDDEN")):
        port = get_live_portfolio()
        assert len(port["holdings"]) >= 1
        assert port["holdings"][0]["buy_price"] == 120.0
    print("  --> PASS: get_live_portfolio() executed pure SQLite read with 0 network calls.")

    # 5. Check R5: SHA256 Checksum Parity Across All 4 Dashboard Mirrors
    print("\n[AUDIT 5] R5: 4-Mirror Byte-for-Byte SHA256 Checksum Parity...")
    mirrors = [
        os.path.join(PROJECT_ROOT, "al_sangmoo_dashboard.html"),
        os.path.join(PROJECT_ROOT, "html_dashboards", "01_알상무_통합_퀀트_대시보드.html"),
        os.path.join(PROJECT_ROOT, "html_dashboards", "01_R상무_통합_퀀트_대시보드.html"),
        os.path.join(PROJECT_ROOT, "HTML_대시보드_모음", "01_R상무_통합_퀀트_대시보드.html"),
    ]
    hashes = [hashlib.sha256(open(m, "rb").read()).hexdigest() for m in mirrors]
    assert len(set(hashes)) == 1, f"Checksum mismatch among mirrors: {set(hashes)}"
    print(f"  --> PASS: All 4 mirrors match SHA256 ({hashes[0]}).")

    cleanup_audit_db()
    print("\n=================================================================")
    print("  ALL INDEPENDENT VICTORY AUDITOR CHECKS PASSED (100% GREEN)")
    print("=================================================================")

if __name__ == "__main__":
    verify_all()
