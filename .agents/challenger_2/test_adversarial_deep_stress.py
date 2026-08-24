"""
Deep Adversarial Stress & Edge Case Harness for R4 & R5 Hardening
"""

import sys
import os
import re
import json
import asyncio
import sqlite3
from typing import Dict, Tuple, List, Optional, Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

TEST_DB = os.path.join(PROJECT_ROOT, "test_deep_stress_p5_1.db")
os.environ["AL_SANGMOO_DB_PATH"] = TEST_DB

import db_manager
import server
from server import app, BuyOrder, SellOrder


async def asgi_request(
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


def test_deep_edge_cases():
    print("\n" + "=" * 70)
    print("  [DEEP STRESS 1] Malformed JSON Syntax & Unhandled Body Streams")
    print("=" * 70)
    
    async def run_malformed_syntax():
        malformed_raw_bodies = [
            (b"{unquoted_key: 123}", "Unquoted JSON keys"),
            (b"{\"ticker\": 'single_quoted'}", "Single-quoted string values"),
            (b"{\"ticker\": \"NVDA\", \"buy_price\": 100,", "Unclosed JSON object"),
            (b"[\"array_not_object\"]", "Top-level array"),
            (b"42", "Top-level integer"),
            (b"\x00\x01\x02\x03\xff\xfe", "Binary garbage data"),
            (b"", "Empty body on POST requiring JSON"),
            (b"   \n\t  ", "Whitespace only body"),
        ]
        
        for raw_body, desc in malformed_raw_bodies:
            status, headers, body = await asgi_request(
                app, "POST", "/api/portfolio/buy",
                headers={"Content-Type": "application/json", "Origin": "http://localhost:8000"},
                body=raw_body
            )
            # FastAPI returns 422 for unparseable JSON bodies
            assert status == 422, f"Expected 422 for malformed JSON syntax ({desc}), got {status}. Body: {body}"
            
            # Check OWASP security headers on 422 responses
            assert headers.get("x-content-type-options") == "nosniff"
            assert headers.get("x-frame-options") == "DENY"
            assert headers.get("referrer-policy") == "strict-origin-when-cross-origin"
            assert headers.get("x-xss-protection") == "1; mode=block"

            # Check that no traceback is leaked
            body_text = body.decode("utf-8", errors="ignore").lower()
            assert "traceback" not in body_text
            assert "most recent call last" not in body_text

        print(f"  [+] Handled {len(malformed_raw_bodies)} malformed JSON syntax streams cleanly (422 + OWASP headers).")

    asyncio.run(run_malformed_syntax())


def test_http_method_not_allowed():
    print("\n" + "=" * 70)
    print("  [DEEP STRESS 2] HTTP Method Not Allowed (405) & Unsupported Verbs")
    print("=" * 70)
    
    async def run_method_tests():
        unsupported_calls = [
            ("PUT", "/api/portfolio/buy"),
            ("DELETE", "/api/portfolio/buy"),
            ("PATCH", "/api/portfolio"),
            ("PUT", "/api/chart/NVDA"),
            ("DELETE", "/"),
        ]
        
        for verb, route in unsupported_calls:
            status, headers, body = await asgi_request(
                app, verb, route,
                headers={"Origin": "http://localhost:8000"}
            )
            assert status == 405, f"Expected 405 for {verb} {route}, got {status}"
            assert headers.get("x-content-type-options") == "nosniff"
            assert headers.get("x-frame-options") == "DENY"
            assert headers.get("referrer-policy") == "strict-origin-when-cross-origin"
            assert headers.get("x-xss-protection") == "1; mode=block"
            
        print(f"  [+] Verified {len(unsupported_calls)} 405 Method Not Allowed responses with 100% OWASP header attachment.")

    asyncio.run(run_method_tests())


def test_concurrent_load_and_fuzzing():
    print("\n" + "=" * 70)
    print("  [DEEP STRESS 3] Concurrent Mixed Workload (Fuzzing + Valid Trades)")
    print("=" * 70)
    
    async def run_concurrent():
        for f in [TEST_DB, f"{TEST_DB}-wal", f"{TEST_DB}-shm"]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass
        db_manager.init_db()
        
        tasks = []
        
        # 25 valid requests
        for i in range(25):
            tasks.append(asgi_request(
                app, "POST", "/api/portfolio/buy",
                headers={"Content-Type": "application/json", "Origin": "http://localhost:8000"},
                body=json.dumps({"ticker": "NVDA", "buy_price": 100.0 + i, "quantity": 1.0}).encode("utf-8")
            ))
            
        # 25 malformed / adversarial requests
        for i in range(25):
            tasks.append(asgi_request(
                app, "POST", "/api/portfolio/buy",
                headers={"Content-Type": "application/json", "Origin": "http://localhost:8000"},
                body=json.dumps({"ticker": f"BAD_{i}!", "buy_price": -50.0, "quantity": 0.0}).encode("utf-8")
            ))
            
        # 25 requests for 404 routes
        for i in range(25):
            tasks.append(asgi_request(
                app, "GET", f"/api/nonexistent_{i}",
                headers={"Origin": "http://localhost:8000"}
            ))

        results = await asyncio.gather(*tasks)
        
        valid_count = sum(1 for status, _, _ in results[:25] if status == 200)
        fuzz_count = sum(1 for status, _, _ in results[25:50] if status == 422)
        nf_count = sum(1 for status, _, _ in results[50:] if status == 404)
        
        assert valid_count == 25, f"Expected 25 valid 200s, got {valid_count}"
        assert fuzz_count == 25, f"Expected 25 422s, got {fuzz_count}"
        assert nf_count == 25, f"Expected 25 404s, got {nf_count}"

        # Verify all 75 responses have OWASP headers
        for status, headers, body in results:
            assert headers.get("x-content-type-options") == "nosniff"
            assert headers.get("x-frame-options") == "DENY"
            assert headers.get("referrer-policy") == "strict-origin-when-cross-origin"
            assert headers.get("x-xss-protection") == "1; mode=block"

        print(f"  [+] Concurrent Mixed Workload: 75/75 requests processed safely under concurrency with 100% header integrity.")

    asyncio.run(run_concurrent())


if __name__ == "__main__":
    test_deep_edge_cases()
    test_http_method_not_allowed()
    test_concurrent_load_and_fuzzing()
    print("\n" + "=" * 75)
    print("  ALL DEEP ADVERSARIAL STRESS TESTS COMPLETED SUCCESSFULLY! (100% GREEN)")
    print("=" * 75)
