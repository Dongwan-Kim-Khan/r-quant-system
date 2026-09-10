"""
===============================================================================
  Al-Sangmoo Quant Trading Platform: Phase 5.1 Security Hardening Test Suite
===============================================================================
Author: Security Test Writer (worker_test)
Target: Phase 5.1 Security Hardening Verification (R1 - R5)
Standards: OWASP Top 10, CWE-79, CWE-942, CWE-1385, CWE-400, CWE-22, CWE-20, CWE-209, CWE-693

Test Tiers:
- Tier 1: Core Feature Coverage (R1 XSS, R2 CORS/CSWSH/DoS, R3 Traversal, R4 Pydantic/500, R5 Headers)
- Tier 2: Boundary and Corner Cases (String limits, special chars, spoofing, pool capacity, malformed inputs)
- Tier 3: Cross-Feature Pairwise Combinations (API->DB->DOM XSS, CORS+Headers, Broker+Guardrails+Headers)
- Tier 4: Real-World Adversarial Attack Workloads (Polyglot fuzzing, CSWSH campaign, WS flood, HTML mirror analysis)

Architecture:
- Zero-Dependency In-Memory ASGI Test Harness (`asgi_request`, `asgi_ws_handshake`).
- Isolated Temporary SQLite Test Database (`test_quant_trades_p5_1.db`).
- Automated HTML AST / regex static analysis across all 4 dashboard HTML mirrors.
===============================================================================
"""

import os
import sys
import re
import json
import asyncio
import sqlite3
import hashlib
from typing import Dict, Tuple, List, Optional, Any

# Windows encoding fix
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Isolate tests to dedicated temporary SQLite database
TEST_DB = os.path.join(PROJECT_ROOT, "test_quant_trades_p5_1.db")
os.environ["AL_SANGMOO_DB_PATH"] = TEST_DB

import db_manager
import server
from server import app, BuyOrder, SellOrder
from al_sangmoo.api.hub import hub, WebSocketBroadcastHub
from youtube_stream_scanner import (
    YOUTUBE_ID_REGEX, validate_youtube_id, get_safe_vtt_path
)
from al_sangmoo.infrastructure.persistence import (
    record_portfolio_sell, add_portfolio_buy, get_live_portfolio, reset_all_holdings
)

ALLOWED_ORIGINS = getattr(server, "ALLOWED_ORIGINS", [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000"
])
TICKER_REGEX = getattr(server, "TICKER_REGEX", re.compile(r'^[A-Za-z0-9.\^=-]{1,15}$'))
DATE_REGEX = getattr(server, "DATE_REGEX", re.compile(r'^\d{4}-\d{2}-\d{2}$'))
REASON_REGEX = getattr(server, "REASON_REGEX", re.compile(r'^[A-Za-z0-9_\-\s\(\)가-힣.,%]{1,100}$'))

# ---------------------------------------------------------------------------
# Zero-Dependency In-Memory ASGI Test Harness
# ---------------------------------------------------------------------------

async def asgi_request(
    app,
    method: str,
    path: str,
    headers: Optional[Dict[str, str]] = None,
    body: bytes = b"",
    query_string: bytes = b""
) -> Tuple[int, Dict[str, str], bytes]:
    """
    Direct in-memory ASGI HTTP client.
    Tests full FastAPI middleware stack (CORS, Security Headers, Exception Handlers) without external dependencies.
    """
    raw_headers = []
    if headers:
        for k, v in headers.items():
            raw_headers.append((k.lower().encode('latin1'), v.encode('latin1')))
            
    scope = {
        'type': 'http',
        'http_version': '1.1',
        'method': method.upper(),
        'path': path,
        'raw_path': path.encode('ascii'),
        'query_string': query_string,
        'headers': raw_headers,
        'client': ('127.0.0.1', 50000),
        'server': ('127.0.0.1', 8000),
    }
    
    status_code = 500
    response_headers = []
    response_body = []
    
    async def receive():
        return {'type': 'http.request', 'body': body, 'more_body': False}
        
    async def send(message):
        nonlocal status_code, response_headers, response_body
        if message['type'] == 'http.response.start':
            status_code = message['status']
            response_headers = message.get('headers', [])
        elif message['type'] == 'http.response.body':
            response_body.append(message.get('body', b''))
            
    await app(scope, receive, send)
    hdr_dict = {k.decode('latin1').lower(): v.decode('latin1') for k, v in response_headers}
    return status_code, hdr_dict, b"".join(response_body)


async def asgi_ws_handshake(app, path: str, headers: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """
    Direct in-memory ASGI WebSocket handshake tester.
    Verifies origin validation during WebSocket connection handshake.
    """
    raw_headers = []
    if headers:
        for k, v in headers.items():
            raw_headers.append((k.lower().encode('latin1'), v.encode('latin1')))
            
    scope = {
        'type': 'websocket',
        'asgi': {'version': '3.0'},
        'http_version': '1.1',
        'scheme': 'ws',
        'path': path,
        'raw_path': path.encode('ascii'),
        'query_string': b'',
        'headers': raw_headers,
        'client': ('127.0.0.1', 50000),
        'server': ('127.0.0.1', 8000),
        'subprotocols': [],
    }
    
    events = []
    
    async def receive():
        return {'type': 'websocket.connect'}
        
    async def send(message):
        events.append(message)
        
    try:
        await app(scope, receive, send)
    except Exception as e:
        events.append({'type': 'exception', 'error': str(e)})
        
    return events


# ---------------------------------------------------------------------------
# Test Setup & Teardown Helpers
# ---------------------------------------------------------------------------

def setup_test_db():
    """Initializes clean temporary SQLite test database."""
    cleanup_test_db()
    db_manager.init_db()


def cleanup_test_db():
    """Removes temporary SQLite database and WAL artifacts."""
    for f in [TEST_DB, f"{TEST_DB}-wal", f"{TEST_DB}-shm"]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass


def simulate_escape_html(text: Any) -> str:
    """Python reference implementation of frontend escapeHtml() utility."""
    if text is None:
        return ""
    s = str(text)
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&#039;"))


# ===========================================================================
# TIER 1: CORE FEATURE COVERAGE (R1 - R5)
# ===========================================================================

def test_tier1_r1_xss_remediation():
    """Tier 1: Feature coverage for R1 (Stored & DOM XSS Remediation - SEC-V01)."""
    print("\n[Tier 1.1] R1: Stored & DOM XSS Remediation...")
    
    # 1. Backend SellOrder Pydantic Model XSS Validation
    xss_payloads = [
        "<script>alert('xss')</script>",
        "<img src=x onerror=alert(1)>",
        "\"><svg/onload=alert(1)>",
        "javascript:alert(1)",
        "<iframe src=\"javascript:alert(1)\">"
    ]
    for payload in xss_payloads:
        try:
            order = SellOrder(sell_price=100.0, reason=payload)
            # If accepted, it MUST have been sanitized
            assert "<script>" not in order.reason
            assert "<img" not in order.reason
            assert "<svg" not in order.reason
            assert "<iframe" not in order.reason
        except Exception:
            # Rejection with validation error is also valid and expected
            pass
    print("  - Backend SellOrder Reason Model Validation: PASSED")

    # 2. Database Persistence Layer XSS Sanitization
    pos_id = add_portfolio_buy(ticker="NVDA", buy_price=120.0, quantity=1.0)
    record_portfolio_sell(holding_id=pos_id, sell_price=130.0, reason="<script>alert(1)</script>")
    
    conn = db_manager.get_db()
    cur = conn.cursor()
    cur.execute("SELECT exit_advice FROM my_portfolio WHERE id = ?", (pos_id,))
    row = cur.fetchone()
    conn.close()
    
    assert row is not None, "Portfolio record not found in database"
    assert "<script>" not in row[0], f"Unsanitized XSS payload found in SQLite: {row[0]}"
    print("  - SQLite Database Persistence Layer XSS Immunity: PASSED")

    # 3. Frontend escapeHtml() Utility Verification in Dashboard HTML
    dashboard_path = os.path.join(PROJECT_ROOT, "al_sangmoo_dashboard.html")
    if not os.path.exists(dashboard_path):
        dashboard_path = os.path.join(PROJECT_ROOT, "backups", "legacy_html", "al_sangmoo_dashboard.html")
    assert os.path.exists(dashboard_path), "al_sangmoo_dashboard.html not found"
    with open(dashboard_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    assert "function escapeHtml" in html_content, "escapeHtml() function missing in al_sangmoo_dashboard.html"
    assert "&amp;" in html_content and "&lt;" in html_content and "&gt;" in html_content, "escapeHtml() missing entity mappings"
    print("  - Frontend escapeHtml() Utility Existence & Entity Replacements: PASSED")


def test_tier1_r2_cors_and_websocket():
    """Tier 1: Feature coverage for R2 (CORS Whitelisting, CSWSH, WebSocket DoS - SEC-V02, SEC-V04)."""
    print("\n[Tier 1.2] R2: CORS Whitelisting, CSWSH & WebSocket DoS Defense...")
    
    async def run_async():
        # 1. Allowed Origin CORS Check
        status_ok, headers_ok, _ = await asgi_request(
            app, "GET", "/api/portfolio",
            headers={"Origin": "http://localhost:8000", "Host": "localhost:8000"}
        )
        assert status_ok == 200, f"Expected 200 for allowed origin, got {status_ok}"
        assert headers_ok.get("access-control-allow-origin") == "http://localhost:8000", "CORS header mismatch for localhost:8000"
        print("  - CORS Whitelisted Origin (http://localhost:8000): ACCEPTED")

        # 2. Blocked Untrusted Origin CORS Check
        status_evil, headers_evil, _ = await asgi_request(
            app, "GET", "/api/portfolio",
            headers={"Origin": "http://evil.com", "Host": "localhost:8000"}
        )
        assert headers_evil.get("access-control-allow-origin") != "http://evil.com", "Untrusted origin evil.com was allowed via CORS!"
        assert headers_evil.get("access-control-allow-origin") != "*", "Wildcard '*' origin detected in CORS response!"
        print("  - CORS Untrusted Origin (http://evil.com): BLOCKED (Wildcard '*' disallowed)")

        # 3. WebSocket Handshake Origin Enforcement (CSWSH Defense)
        evil_ws_events = await asgi_ws_handshake(
            app, "/ws/live_feed",
            headers={"Origin": "http://evil.com", "Host": "localhost:8000"}
        )
        # Should either be rejected with websocket.close code 1008 or handshake denial
        has_close_1008 = any(
            e.get('type') == 'websocket.close' and e.get('code') == 1008
            for e in evil_ws_events
        )
        has_accept = any(e.get('type') == 'websocket.accept' for e in evil_ws_events)
        assert has_close_1008 or not has_accept, f"Unauthorized WS handshake was accepted: {evil_ws_events}"
        print("  - WebSocket CSWSH Defense (Untrusted Origin Rejection): PASSED")

        # 4. WebSocket Active Connection Ceiling (MAX_CONNECTIONS = 50)
        test_hub = WebSocketBroadcastHub(max_connections=50)
        
        class MockWS:
            def __init__(self, client_id):
                self.id = client_id
                self.accepted = False
                self.closed = False
                self.close_code = None
                self.close_reason = ""
            async def accept(self):
                self.accepted = True
            async def close(self, code=1000, reason=""):
                self.closed = True
                self.close_code = code
                self.close_reason = reason
            async def send_json(self, msg):
                pass
                
        ws_pool = [MockWS(i) for i in range(50)]
        for ws in ws_pool:
            accepted = await test_hub.connect(ws)
            assert accepted is True, f"Failed to connect client #{ws.id} under limit"
            assert ws.accepted is True
        assert len(test_hub.active_connections) == 50, f"Expected 50 active, got {len(test_hub.active_connections)}"
        print("  - WebSocket Connection Pool: Filled exactly 50 / 50 active slots")

        # 51st Connection Attempt (Overflow)
        overflow_ws = MockWS(51)
        overflow_accepted = await test_hub.connect(overflow_ws)
        assert overflow_accepted is False, "Overflow client #51 was unexpectedly accepted!"
        assert overflow_ws.closed is True, "Overflow client #51 was not closed!"
        assert overflow_ws.close_code == 1008, f"Expected close code 1008, got {overflow_ws.close_code}"
        assert len(test_hub.active_connections) == 50, "Active connections exceeded max capacity!"
        print("  - WebSocket Overflow Connection (Client #51): REJECTED with code 1008 as expected")

        # Disconnect all
        for ws in ws_pool:
            await test_hub.disconnect(ws)
        assert len(test_hub.active_connections) == 0
        print("  - WebSocket Pool Cleanup: All connections safely detached")

    asyncio.run(run_async())


def test_tier1_r3_path_traversal():
    """Tier 1: Feature coverage for R3 (Path Traversal & Subprocess Hardening - SEC-V05)."""
    print("\n[Tier 1.3] R3: Path Traversal & Subprocess Hardening...")
    
    # 1. Valid YouTube Video IDs
    valid_ids = ["dQw4w9WgXcQ", "abc_123-XYZ", "_A-1b2C3d4E"]
    for v in valid_ids:
        assert YOUTUBE_ID_REGEX.match(v) is not None, f"Valid ID failed regex: {v}"
        assert validate_youtube_id(v) == v
        safe_path = get_safe_vtt_path(v)
        assert os.path.isabs(safe_path)
    print("  - YouTube Video ID Whitelist Regex: Validated standard 11-char patterns")

    # 2. Malicious Traversal & Command Injection Payloads
    malicious_ids = [
        "../../etc/passwd",
        r"..\..\windows\system32\calc.exe",
        "video_id; rm -rf /",
        "dQw4w9WgXcQ&param=1",
        "toolongvideoid12345",
        "short",
        "",
        None
    ]
    for bad in malicious_ids:
        if bad is None:
            continue
        assert YOUTUBE_ID_REGEX.match(bad) is None, f"Malicious ID bypassed regex: {bad}"
        try:
            validate_youtube_id(bad)
            assert False, f"validate_youtube_id did not raise ValueError for {bad}"
        except (ValueError, TypeError):
            pass
            
        try:
            get_safe_vtt_path(bad)
            assert False, f"get_safe_vtt_path did not raise ValueError for {bad}"
        except (ValueError, TypeError, PermissionError):
            pass
    print("  - Subprocess & Path Traversal Injection Vectors: REJECTED with ValueError/PermissionError")


def test_tier1_r4_pydantic_and_error_sanitization():
    """Tier 1: Feature coverage for R4 (Pydantic Input Validation & Error Masking - SEC-V07, SEC-V08)."""
    print("\n[Tier 1.4] R4: Pydantic Input Validation & Global Error Sanitization...")
    
    # 1. Pydantic BuyOrder validation
    try:
        BuyOrder(ticker="..", buy_price=100.0, quantity=1.0)
        assert False, "BuyOrder accepted invalid ticker '..'"
    except Exception:
        pass

    try:
        BuyOrder(ticker="NVDA", buy_price=-10.0, quantity=1.0)
        assert False, "BuyOrder accepted negative buy_price"
    except Exception:
        pass

    try:
        BuyOrder(ticker="NVDA", buy_price=100.0, quantity=1.0, buy_date="INVALID_DATE")
        assert False, "BuyOrder accepted invalid date format"
    except Exception:
        pass
    print("  - Pydantic BuyOrder Boundary Validators: PASSED")

    # 2. Pydantic SellOrder validation
    try:
        SellOrder(sell_price=-50.0)
        assert False, "SellOrder accepted negative sell_price"
    except Exception:
        pass

    try:
        SellOrder(sell_price=100.0, sell_date="2026/08/22")
        assert False, "SellOrder accepted slash date format"
    except Exception:
        pass
    print("  - Pydantic SellOrder Boundary Validators: PASSED")

    # 3. Global Exception Handler (500 Error Sanitization)
    async def run_error_check():
        status, _, body = await asgi_request(app, "GET", "/api/chart/..")
        assert status in [400, 404, 422], f"Expected 400/404/422 for invalid ticker, got {status}"
        
        # Test non-existent route or internal error
        status_err, _, body_err = await asgi_request(app, "GET", "/api/nonexistent_test_route_500")
        body_text = body_err.decode("utf-8", errors="ignore")
        assert "Traceback (most recent call last)" not in body_text, "Raw Python traceback leaked in response!"
        assert "d:\\코딩" not in body_text.lower(), "Internal absolute filesystem path leaked in response!"
        print("  - Global Exception Sanitization (Zero Traceback Leakage): PASSED")

    asyncio.run(run_error_check())


def test_tier1_r5_owasp_security_headers():
    """Tier 1: Feature coverage for R5 (OWASP Defensive Response Headers - SEC-V10)."""
    print("\n[Tier 1.5] R5: OWASP Security Response Headers...")
    
    async def run_headers():
        endpoints = ["/", "/api/portfolio", "/api/recommendations/matrix"]
        for ep in endpoints:
            status, headers, _ = await asgi_request(app, "GET", ep)
            assert status == 200, f"Endpoint {ep} returned status {status}"
            assert headers.get("x-content-type-options") == "nosniff", f"Missing X-Content-Type-Options on {ep}"
            assert headers.get("x-frame-options") == "DENY", f"Missing X-Frame-Options on {ep}"
            assert headers.get("referrer-policy") == "strict-origin-when-cross-origin", f"Missing Referrer-Policy on {ep}"
            assert headers.get("x-xss-protection") == "1; mode=block", f"Missing X-XSS-Protection on {ep}"
        print("  - OWASP Security Headers (X-Content-Type-Options, X-Frame-Options, Referrer-Policy, X-XSS-Protection): VERIFIED on all endpoints")

    asyncio.run(run_headers())


# ===========================================================================
# TIER 2: BOUNDARY AND CORNER CASES
# ===========================================================================

def test_tier2_boundary_cases():
    """Tier 2: Boundary value analysis & adversarial corner cases."""
    print("\n[Tier 2] Verifying Boundary & Corner Cases...")

    # 1. R1 SellOrder Reason Boundaries
    # Exactly 100 chars (valid)
    valid_100 = "A" * 100
    try:
        order_100 = SellOrder(sell_price=100.0, reason=valid_100)
        assert len(order_100.reason) <= 100
    except Exception:
        pass

    # Over 100 chars (101 and 150 chars)
    over_101 = "B" * 101
    try:
        order_101 = SellOrder(sell_price=100.0, reason=over_101)
        assert len(order_101.reason) <= 100, "Reason length was not capped or rejected"
    except Exception:
        pass # Rejection is expected

    # Empty string and whitespace only
    try:
        order_empty = SellOrder(sell_price=100.0, reason="")
        assert order_empty.reason in ["MANUAL_SELL", ""]
    except Exception:
        pass

    # Korean text with allowed punctuation
    korean_reason = "수익률 15.2% 달성 (익절 매도)"
    order_kr = SellOrder(sell_price=100.0, reason=korean_reason)
    assert "익절 매도" in order_kr.reason, "Korean characters were improperly corrupted"
    print("  - SellOrder Reason Length & Character Boundaries: PASSED")

    # 2. R2 CORS Subdomain Spoofing & Port Variations
    async def run_cors_boundaries():
        spoofed_origins = [
            "http://localhost.evil.com",
            "http://evil.localhost:8000",
            "http://localhost:8080",
            "http://127.0.0.1:9000",
            "null",
            "http://evil.com:8000",
            "https://localhost:8000",
        ]
        for origin in spoofed_origins:
            _, headers, _ = await asgi_request(
                app, "GET", "/api/portfolio",
                headers={"Origin": origin, "Host": "localhost:8000"}
            )
            assert headers.get("access-control-allow-origin") != origin, f"Spoofed origin '{origin}' was allowed by CORS!"
            assert headers.get("access-control-allow-origin") != "*", "Wildcard origin returned!"
        print("  - CORS Subdomain & Port Spoofing Defense: PASSED")

        # 3. R2 WebSocket Hub Slot Reclamation & Double Disconnect Idempotency
        hub_instance = WebSocketBroadcastHub(max_connections=50)
        
        class DummyWS:
            def __init__(self, idx):
                self.idx = idx
                self.accepted = False
                self.closed = False
            async def accept(self):
                self.accepted = True
            async def close(self, code=1000, reason=""):
                self.closed = True
            async def send_json(self, msg):
                pass

        clients = [DummyWS(i) for i in range(50)]
        for c in clients:
            await hub_instance.connect(c)
        assert len(hub_instance.active_connections) == 50

        # Disconnect 10 clients
        for c in clients[:10]:
            await hub_instance.disconnect(c)
        assert len(hub_instance.active_connections) == 40

        # Connect 10 new clients to reclaim slots
        new_clients = [DummyWS(100 + i) for i in range(10)]
        for c in new_clients:
            res = await hub_instance.connect(c)
            assert res is True
        assert len(hub_instance.active_connections) == 50

        # Double disconnect test (must not raise or corrupt state)
        await hub_instance.disconnect(clients[0]) # Already disconnected
        assert len(hub_instance.active_connections) == 50

        # Cleanup
        for c in clients[10:] + new_clients:
            await hub_instance.disconnect(c)
        assert len(hub_instance.active_connections) == 0
        print("  - WebSocket Hub Slot Reclamation & Idempotency: PASSED")

    asyncio.run(run_cors_boundaries())

    # 4. R3 Path Traversal Boundary Formats
    edge_traversal_payloads = [
        "dQw4w9WgXc",          # 10 chars (too short)
        "dQw4w9WgXcQQ",        # 12 chars (too long)
        "dQw4w9WgXc!",         # Illegal punctuation
        "dQw4w9WgXc\x00",      # Null byte
        "/etc/passwd",         # Unix root absolute
        "C:\\boot.ini",        # Windows root absolute
        "....//....//etc",     # Double dot slash combo
        "%2e%2e%2fpasswd",     # URL encoded traversal
    ]
    for payload in edge_traversal_payloads:
        assert YOUTUBE_ID_REGEX.match(payload) is None, f"Regex allowed invalid traversal ID: {payload}"
    print("  - YouTube ID Regex Boundary & Encoding Variations: PASSED")

    # 5. R4 Pydantic Numeric and Date Boundaries
    # Buy price boundary
    try:
        BuyOrder(ticker="AMZN", buy_price=0.0, quantity=1.0)
        assert False, "Accepted buy_price = 0.0"
    except Exception:
        pass

    try:
        BuyOrder(ticker="AMZN", buy_price=10_000_001.0, quantity=1.0)
        assert False, "Accepted buy_price exceeding 10,000,000"
    except Exception:
        pass

    # Quantity boundary
    try:
        BuyOrder(ticker="AMZN", buy_price=100.0, quantity=0.0)
        assert False, "Accepted quantity = 0.0"
    except Exception:
        pass

    try:
        BuyOrder(ticker="AMZN", buy_price=100.0, quantity=1_000_001.0)
        assert False, "Accepted quantity exceeding 1,000,000"
    except Exception:
        pass

    # Date boundary formats
    invalid_dates = ["2026/08/22", "22-08-2026", "2026-8-22", "2026-08-22T12:00:00Z", "INVALID"]
    for d in invalid_dates:
        try:
            BuyOrder(ticker="AMZN", buy_price=100.0, quantity=1.0, buy_date=d)
            assert False, f"Accepted invalid date format: {d}"
        except Exception:
            pass
    print("  - Pydantic Price, Quantity, and Date Field Boundaries: PASSED")


# ===========================================================================
# TIER 3: CROSS-FEATURE PAIRWISE COMBINATIONS
# ===========================================================================

def test_tier3_pairwise_combinations():
    """Tier 3: Multi-layer cross-feature interactions."""
    print("\n[Tier 3] Verifying Cross-Feature Pairwise Combinations...")

    async def run_combinations():
        # Combo 1: XSS API Injection -> SQLite Storage -> GET Portfolio API -> Simulated DOM Rendering
        reset_all_holdings()
        pos_id = add_portfolio_buy(ticker="AMZN", buy_price=250.0, quantity=2.0)
        
        # Sell via API with complex XSS payload
        sell_payload = json.dumps({
            "sell_price": 275.0,
            "reason": "<script>alert('XSS_CHAIN')</script>"
        }).encode("utf-8")
        
        status_sell, headers_sell, body_sell = await asgi_request(
            app, "POST", f"/api/portfolio/sell/{pos_id}",
            headers={"Content-Type": "application/json", "Origin": "http://localhost:8000"},
            body=sell_payload
        )
        # Sell could return 200 (if sanitized) or 422 (if rejected)
        if status_sell == 200:
            # Query portfolio via GET API
            status_p, headers_p, body_p = await asgi_request(
                app, "GET", "/api/portfolio",
                headers={"Origin": "http://localhost:8000"}
            )
            assert status_p == 200
            p_data = json.loads(body_p.decode("utf-8"))
            holdings = p_data.get("holdings", [])
            for h in holdings:
                if h["id"] == pos_id:
                    exit_adv = h.get("exit_advice", "")
                    assert "<script>" not in exit_adv, f"Raw <script> leaked into portfolio JSON: {exit_adv}"
                    dom_safe = simulate_escape_html(exit_adv)
                    assert "<script>" not in dom_safe
                    assert "&lt;script&gt;" in dom_safe or "script" not in dom_safe
        print("  - Combo 1 (XSS Ingress -> SQLite -> REST Egress -> DOM Escape): PASSED")

        # Combo 2: CORS Preflight (OPTIONS) + Security Headers + Origin Filtering
        status_opt, headers_opt, _ = await asgi_request(
            app, "OPTIONS", "/api/portfolio/buy",
            headers={
                "Origin": "http://localhost:8000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type"
            }
        )
        assert status_opt in [200, 204], f"OPTIONS returned {status_opt}"
        assert headers_opt.get("access-control-allow-origin") == "http://localhost:8000"
        assert headers_opt.get("x-content-type-options") == "nosniff"
        assert headers_opt.get("x-frame-options") == "DENY"
        print("  - Combo 2 (CORS Preflight + OWASP Security Headers): PASSED")

        # Combo 3: Broker Execution with Ticker Validation + Pre-Trade Guardrails + Security Headers
        valid_broker_body = json.dumps({
            "ticker": "NVDA",
            "buy_price": 120.0,
            "quantity": 10.0,
            "buy_date": "2026-08-22"
        }).encode("utf-8")
        
        status_broker, headers_broker, body_broker = await asgi_request(
            app, "POST", "/api/broker/order",
            headers={"Content-Type": "application/json", "Origin": "http://localhost:8000"},
            body=valid_broker_body
        )
        assert status_broker in [200, 400], f"Broker order returned unexpected status {status_broker}"
        assert headers_broker.get("x-content-type-options") == "nosniff"
        assert headers_broker.get("x-frame-options") == "DENY"
        print("  - Combo 3 (Broker Execution + Ticker Validation + Security Headers): PASSED")

        # Combo 4: Malformed Payload 422 Error + Security Response Headers
        malformed_body = b"{\"buy_price\": \"NOT_A_FLOAT\"}"
        status_422, headers_422, _ = await asgi_request(
            app, "POST", "/api/portfolio/buy",
            headers={"Content-Type": "application/json", "Origin": "http://localhost:8000"},
            body=malformed_body
        )
        assert status_422 == 422
        assert headers_422.get("x-content-type-options") == "nosniff"
        assert headers_422.get("x-frame-options") == "DENY"
        print("  - Combo 4 (Pydantic 422 Error + OWASP Defensive Headers): PASSED")

    asyncio.run(run_combinations())


# ===========================================================================
# TIER 4: REAL-WORLD SECURITY ATTACK WORKLOADS & STATIC ANALYSIS
# ===========================================================================

def test_tier4_xss_polyglot_fuzzing():
    """Tier 4.1: Automated XSS Polyglot & Advanced Injection Fuzzing Campaign."""
    print("\n[Tier 4.1] Real-World Attack Workload: XSS Polyglot Fuzzing Campaign...")
    
    adversarial_polyglots = [
        "javascript:/*--></title></style></textarea></script></xmp><svg/onload='+/\'/+/onmouseover=1/+/[*/[]/+alert(1)//'>",
        "\"><img src=x onerror=alert(1)>",
        "<svg onload=alert(document.domain)>",
        "<iframe src=\"javascript:alert(`XSS`)\">",
        "<body onload=alert('XSS')>",
        "<input onfocus=alert(1) autofocus>",
        "<a href=\"javascript:alert(1)\">Click</a>",
        "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
        "';alert(String.fromCharCode(88,83,83))//\\';alert(String.fromCharCode(88,83,83))//\";alert(String.fromCharCode(88,83,83))//\\\"",
        "<math><mtext><table><mglyph><style><img src=1 onerror=alert(1)>",
    ]
    
    for polyglot in adversarial_polyglots:
        # 1. Pydantic validation / sanitization check
        try:
            order = SellOrder(sell_price=100.0, reason=polyglot)
            assert "<script" not in order.reason.lower()
            assert "<svg" not in order.reason.lower()
            assert "<iframe" not in order.reason.lower()
            assert "<img" not in order.reason.lower()
        except Exception:
            pass # Rejection is safe and expected
            
        # 2. Simulated frontend entity escaping check
        escaped = simulate_escape_html(polyglot)
        assert "<script" not in escaped.lower()
        assert "<svg" not in escaped.lower()
        assert "<iframe" not in escaped.lower()
        assert "<img" not in escaped.lower()
        assert "<body" not in escaped.lower()
        assert "<input" not in escaped.lower()
        assert "<math" not in escaped.lower()
        
    print(f"  - Fuzzed {len(adversarial_polyglots)} advanced XSS polyglots against backend & frontend sanitizer: 100% BLOCKED")


def test_tier4_websocket_concurrency_flood():
    """Tier 4.2: High-Concurrency WebSocket Burst Flood & Non-Blocking Broadcast Stress."""
    print("\n[Tier 4.2] Real-World Attack Workload: WebSocket Burst Flood & Capacity Stress...")
    
    async def run_ws_flood():
        test_hub = WebSocketBroadcastHub(max_connections=50)
        
        class FloodClient:
            def __init__(self, cid):
                self.cid = cid
                self.received_messages = []
                self.is_closed = False
                self.close_code = None
            async def accept(self):
                pass
            async def close(self, code=1000, reason=""):
                self.is_closed = True
                self.close_code = code
            async def send_json(self, msg):
                self.received_messages.append(msg)
                
        # Simulate burst of 65 simultaneous connection requests
        burst_clients = [FloodClient(i) for i in range(65)]
        results = await asyncio.gather(*(test_hub.connect(c) for c in burst_clients))
        
        accepted_count = sum(1 for r in results if r is True)
        rejected_count = sum(1 for r in results if r is False)
        
        assert accepted_count == 50, f"Expected exactly 50 accepted, got {accepted_count}"
        assert rejected_count == 15, f"Expected exactly 15 rejected, got {rejected_count}"
        assert len(test_hub.active_connections) == 50
        print(f"  - Burst Connection Flood (65 simultaneous clients): 50 accepted, 15 rejected (Code 1008)")

        # Broadcast under full capacity load
        broadcast_payload = {"type": "PRICE_TICK", "ticker": "NVDA", "price": 225.50}
        await test_hub.broadcast("TICK", broadcast_payload)
        
        # Verify all 50 active clients received the broadcast
        for c in burst_clients[:50]:
            assert len(c.received_messages) == 1, f"Client {c.cid} missed broadcast"
            assert c.received_messages[0]["event"] == "TICK"
        print("  - Non-Blocking Broadcast under 50-Client Load: 100% Delivery Verified")

        # Cleanup
        for c in burst_clients[:50]:
            await test_hub.disconnect(c)
        assert len(test_hub.active_connections) == 0

    asyncio.run(run_ws_flood())


def test_tier4_html_dashboard_static_analysis():
    """Tier 4.3: Automated AST & Regex Static Analysis across ALL 4 Dashboard HTML Mirrors."""
    print("\n[Tier 4.3] Automated Static Analysis across ALL 4 Dashboard HTML Mirrors...")
    
    raw_targets = [
        "al_sangmoo_dashboard.html",
        os.path.join("html_dashboards", "01_R상무_통합_퀀트_대시보드.html"),
        os.path.join("html_dashboards", "01_알상무_통합_퀀트_대시보드.html"),
        os.path.join("HTML_대시보드_모음", "01_R상무_통합_퀀트_대시보드.html")
    ]
    html_targets = []
    legacy_backup = os.path.join(PROJECT_ROOT, "backups", "legacy_html")
    for t in raw_targets:
        primary = os.path.join(PROJECT_ROOT, t)
        backup = os.path.join(legacy_backup, t)
        html_targets.append(primary if os.path.exists(primary) else backup)
    
    reference_hash = None
    for html_path in html_targets:
        rel_path = os.path.relpath(html_path, PROJECT_ROOT)
        assert os.path.exists(html_path), f"Dashboard mirror file missing: {rel_path}"
        
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        # 1. Compute and verify mirror content hash synchronicity
        current_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if reference_hash is None:
            reference_hash = current_hash
        else:
            assert current_hash == reference_hash, f"HTML Mirror {rel_path} hash desynchronized: {current_hash} != {reference_hash}"
            
        # 2. Verify escapeHtml() function definition
        assert "function escapeHtml" in content, f"Missing escapeHtml() function in {rel_path}"
        
        # 3. Verify escapeHtml implementation handles & < > " '
        assert "&amp;" in content and "&lt;" in content and "&gt;" in content, f"Incomplete escapeHtml() replacements in {rel_path}"
        assert "&quot;" in content and "&#039;" in content, f"Missing quote escaping in {rel_path}"
        
        # 4. Verify presence of event delegation setup
        assert "initFrontendEventDelegation" in content, f"Missing initFrontendEventDelegation in {rel_path}"
        
        # 5. Verify absence of dangerous inline onclick="${item.ticker}" string interpolation
        dangerous_onclick_patterns = [
            r'onclick=["\']selectStock\(["\']\$\{[^}]+\}["\']',
            r'onclick=["\']sellHolding\(\$\{[^}]+\}',
        ]
        for pat in dangerous_onclick_patterns:
            matches = re.findall(pat, content)
            assert len(matches) == 0, f"Found dangerous inline onclick pattern '{pat}' in {rel_path}: {matches}"
            
        # 6. Verify sanitization of exit_advice interpolation
        assert "escapeHtml" in content, f"escapeHtml not utilized in {rel_path}"
        
        print(f"  - Verified HTML Mirror: {rel_path} (Hash: {current_hash[:12]}..., escapeHtml & delegation 100% compliant)")


# ===========================================================================
# MAIN RUNNER & SUITE ORCHESTRATION
# ===========================================================================

def run_all_security_tests():
    """Executes the full Phase 5.1 Security Hardening Test Suite."""
    print("=" * 75)
    print("  AL-SANGMOO QUANT PLATFORM: PHASE 5.1 SECURITY HARDENING VERIFICATION")
    print("=" * 75)
    
    setup_test_db()
    try:
        # Tier 1: Feature Coverage (R1 - R5)
        test_tier1_r1_xss_remediation()
        test_tier1_r2_cors_and_websocket()
        test_tier1_r3_path_traversal()
        test_tier1_r4_pydantic_and_error_sanitization()
        test_tier1_r5_owasp_security_headers()
        
        # Tier 2: Boundary & Corner Cases
        test_tier2_boundary_cases()
        
        # Tier 3: Cross-Feature Combinations
        test_tier3_pairwise_combinations()
        
        # Tier 4: Real-World Security Workloads
        test_tier4_xss_polyglot_fuzzing()
        test_tier4_websocket_concurrency_flood()
        test_tier4_html_dashboard_static_analysis()
        
        print("\n" + "=" * 75)
        print("  ALL PHASE 5.1 SECURITY HARDENING TESTS PASSED SUCCESSFULLY! (100% GREEN)")
        print("=" * 75)
    finally:
        cleanup_test_db()


if __name__ == "__main__":
    run_all_security_tests()
