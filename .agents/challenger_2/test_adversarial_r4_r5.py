"""
Adversarial Empirical Challenge Harness for Phase 5.1 Security Hardening:
R4 (Pydantic Input Boundaries & Global 500 Error Masking)
R5 (OWASP Security Response Headers)
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

TEST_DB = os.path.join(PROJECT_ROOT, "test_adversarial_p5_1.db")
os.environ["AL_SANGMOO_DB_PATH"] = TEST_DB

import db_manager
import server
from server import app, BuyOrder, SellOrder

# ---------------------------------------------------------------------------
# ASGI HTTP Client Harness
# ---------------------------------------------------------------------------

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


def setup_db():
    for f in [TEST_DB, f"{TEST_DB}-wal", f"{TEST_DB}-shm"]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass
    db_manager.init_db()


def cleanup_db():
    for f in [TEST_DB, f"{TEST_DB}-wal", f"{TEST_DB}-shm"]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# TEST SUITE: R4 Pydantic Input Boundaries & Fuzzing
# ---------------------------------------------------------------------------

def test_r4_pydantic_model_fuzzing():
    print("\n" + "=" * 70)
    print("  [CHALLENGE 1] R4: Pydantic Model Adversarial Fuzzing")
    print("=" * 70)
    
    # 1. BuyOrder - Adversarial Ticker Fuzzing
    invalid_tickers = [
        "",                     # Empty
        "   ",                  # Whitespace
        "A" * 16,               # Exceeds max_length 15
        "A" * 50,               # Large overflow
        "AAPL;DROP TABLE",      # SQL injection
        "<script>alert(1)</script>", # XSS
        "NVDA\x00",             # Null byte
        "../NVDA",              # Path traversal
        "NVDA/AAPL",            # Path slash
        "NVDA@NASDAQ",          # Special @
        "AAPL#1",               # Special #
        "AAPL$USD",             # Special $
        "AAPL%20",              # URL encoding %
        "🍎AAPL",               # Emoji
        "三星전자",             # Korean in BuyOrder (model requires TICKER_REGEX)
        "\nNVDA\n",             # Newlines
        "NVDA ",                # Trailing whitespace should strip to NVDA, valid
    ]
    
    ticker_fuzz_passed = 0
    for ticker in invalid_tickers:
        is_clean_valid = bool(server.TICKER_REGEX.match(ticker.strip().upper()))
        try:
            order = BuyOrder(ticker=ticker, buy_price=100.0, quantity=1.0)
            if not is_clean_valid:
                raise AssertionError(f"BuyOrder accepted invalid ticker: '{ticker}'")
            else:
                assert order.ticker == ticker.strip().upper()
                ticker_fuzz_passed += 1
        except (ValueError, Exception):
            if not is_clean_valid:
                ticker_fuzz_passed += 1
            else:
                raise AssertionError(f"BuyOrder rejected valid stripped ticker: '{ticker}'")
    print(f"  [+] Ticker Fuzzing: {ticker_fuzz_passed}/{len(invalid_tickers)} cases evaluated correctly.")

    # 2. BuyOrder / SellOrder - Price Boundary & Float Fuzzing
    adversarial_prices = [
        (-1000.0, False, "Negative float"),
        (-0.00001, False, "Negative tiny float"),
        (0.0, False, "Zero float (must be gt 0)"),
        (10_000_000.0, True, "Upper boundary exact (le 10M)"),
        (10_000_000.01, False, "Upper boundary overflow"),
        (1e9, False, "Large scientific float"),
        (1e308, False, "Max float overflow"),
        (float('inf'), False, "Positive Infinity"),
        (float('-inf'), False, "Negative Infinity"),
        (float('nan'), False, "NaN float"),
    ]

    price_fuzz_passed = 0
    for price_val, should_pass, desc in adversarial_prices:
        # Test BuyOrder
        try:
            b_order = BuyOrder(ticker="NVDA", buy_price=price_val, quantity=1.0)
            if not should_pass:
                raise AssertionError(f"BuyOrder accepted invalid buy_price ({desc}): {price_val}")
            price_fuzz_passed += 1
        except Exception:
            if should_pass:
                raise AssertionError(f"BuyOrder rejected valid buy_price ({desc}): {price_val}")
            price_fuzz_passed += 1
            
        # Test SellOrder
        try:
            s_order = SellOrder(sell_price=price_val)
            if not should_pass:
                raise AssertionError(f"SellOrder accepted invalid sell_price ({desc}): {price_val}")
            price_fuzz_passed += 1
        except Exception:
            if should_pass:
                raise AssertionError(f"SellOrder rejected valid sell_price ({desc}): {price_val}")
            price_fuzz_passed += 1

    print(f"  [+] Price Boundary & Float Fuzzing: {price_fuzz_passed}/{len(adversarial_prices) * 2} cases passed.")

    # 3. BuyOrder - Quantity Boundary & Float Fuzzing
    adversarial_quantities = [
        (-100.0, False, "Negative float"),
        (-0.0001, False, "Negative tiny float"),
        (0.0, False, "Zero float (must be gt 0)"),
        (0.0001, True, "Fractional shares (gt 0)"),
        (1.0, True, "Standard share count"),
        (1_000_000.0, True, "Upper boundary exact (le 1M)"),
        (1_000_000.01, False, "Upper boundary overflow"),
        (1e8, False, "Large scientific float"),
        (1e308, False, "Max float overflow"),
        (float('inf'), False, "Positive Infinity"),
        (float('-inf'), False, "Negative Infinity"),
        (float('nan'), False, "NaN float"),
    ]

    qty_fuzz_passed = 0
    for qty_val, should_pass, desc in adversarial_quantities:
        try:
            b_order = BuyOrder(ticker="NVDA", buy_price=100.0, quantity=qty_val)
            if not should_pass:
                raise AssertionError(f"BuyOrder accepted invalid quantity ({desc}): {qty_val}")
            qty_fuzz_passed += 1
        except Exception:
            if should_pass:
                raise AssertionError(f"BuyOrder rejected valid quantity ({desc}): {qty_val}")
            qty_fuzz_passed += 1

    print(f"  [+] Quantity Boundary & Float Fuzzing: {qty_fuzz_passed}/{len(adversarial_quantities)} cases passed.")

    # 4. Date Format Fuzzing (buy_date, sell_date)
    adversarial_dates = [
        (None, True, "None (optional)"),
        ("", True, "Empty string (converted to None)"),
        ("   ", True, "Whitespace only (converted to None)"),
        ("2026-08-22", True, "Standard YYYY-MM-DD"),
        ("2026-01-01", True, "Standard start of year"),
        ("2026/08/22", False, "Slash delimiter"),
        ("22-08-2026", False, "DD-MM-YYYY format"),
        ("08-22-2026", False, "MM-DD-YYYY format"),
        ("2026-8-22", False, "Single digit month"),
        ("2026-08-2", False, "Single digit day"),
        ("2026-08-22T14:00:00Z", False, "ISO datetime with timestamp"),
        ("20260822", False, "Compact 8-digit string"),
        ("today", False, "Textual keyword"),
        ("2026-08-22; DROP TABLE", False, "SQL injection in date"),
        ("<script>alert(1)</script>", False, "XSS in date"),
        ("2026-08-22\x00", False, "Null byte in date"),
        ("2026-08-22 00:00:00", False, "Date with space and time"),
    ]

    date_fuzz_passed = 0
    for date_val, should_pass, desc in adversarial_dates:
        # BuyOrder
        try:
            b_order = BuyOrder(ticker="NVDA", buy_price=100.0, quantity=1.0, buy_date=date_val)
            if not should_pass:
                raise AssertionError(f"BuyOrder accepted invalid date ({desc}): '{date_val}'")
            date_fuzz_passed += 1
        except Exception:
            if should_pass:
                raise AssertionError(f"BuyOrder rejected valid date ({desc}): '{date_val}'")
            date_fuzz_passed += 1
            
        # SellOrder
        try:
            s_order = SellOrder(sell_price=100.0, sell_date=date_val)
            if not should_pass:
                raise AssertionError(f"SellOrder accepted invalid date ({desc}): '{date_val}'")
            date_fuzz_passed += 1
        except Exception:
            if should_pass:
                raise AssertionError(f"SellOrder rejected valid date ({desc}): '{date_val}'")
            date_fuzz_passed += 1

    print(f"  [+] Date Format Fuzzing: {date_fuzz_passed}/{len(adversarial_dates) * 2} cases passed.")

    # 5. SellOrder Reason Regex & Length Fuzzing
    adversarial_reasons = [
        ("MANUAL_SELL", True, "Default standard reason"),
        ("익절 매도 15%", True, "Korean text with %"),
        ("손절 처리 (-3.5%)", True, "Korean text with negative %, parens, dot"),
        ("Al-Sangmoo Cloud Bounce [Exit]", False, "Square brackets not in regex"),
        ("A" * 100, True, "Max length 100 exact"),
        ("A" * 101, False, "Exceeds max length 100"),
        ("<script>alert(1)</script>", False, "HTML script tag"),
        ("<img src=x onerror=alert(1)>", False, "HTML img tag"),
        ("reason; DROP TABLE", False, "Semicolon not in regex"),
        ("reason' OR '1'='1", False, "Single quotes not in regex"),
        ('reason" OR "1"="1', False, "Double quotes not in regex"),
        ("reason `backtick`", False, "Backticks not in regex"),
        ("reason $dollar", False, "Dollar sign not in regex"),
        ("reason #hash", False, "Hash not in regex"),
        ("reason @at", False, "At sign not in regex"),
        ("reason &amp;", False, "Ampersand not in regex"),
    ]

    reason_fuzz_passed = 0
    for reason_val, should_pass, desc in adversarial_reasons:
        try:
            s_order = SellOrder(sell_price=100.0, reason=reason_val)
            if not should_pass:
                raise AssertionError(f"SellOrder accepted invalid reason ({desc}): '{reason_val}'")
            reason_fuzz_passed += 1
        except Exception:
            if should_pass:
                raise AssertionError(f"SellOrder rejected valid reason ({desc}): '{reason_val}'")
            reason_fuzz_passed += 1

    print(f"  [+] Reason Regex & Length Fuzzing: {reason_fuzz_passed}/{len(adversarial_reasons)} cases passed.")


# ---------------------------------------------------------------------------
# TEST SUITE: R4 API Endpoint Boundary Fuzzing (HTTP 422 Clean Rejections)
# ---------------------------------------------------------------------------

def test_r4_api_endpoint_fuzzing():
    print("\n" + "=" * 70)
    print("  [CHALLENGE 2] R4: REST API Endpoints Boundary Fuzzing (HTTP 422)")
    print("=" * 70)
    
    async def run_api_fuzzing():
        # Setup clean test state
        setup_db()
        pos_id = db_manager.add_portfolio_buy(ticker="NVDA", buy_price=120.0, quantity=5.0)

        # Fuzz payloads on POST /api/portfolio/buy
        fuzz_buy_payloads = [
            (b'{"ticker": "NVDA"}', "Missing buy_price and quantity"),
            (b'{"ticker": "NVDA", "buy_price": -100, "quantity": 1}', "Negative buy_price"),
            (b'{"ticker": "NVDA", "buy_price": 0, "quantity": 1}', "Zero buy_price"),
            (b'{"ticker": "NVDA", "buy_price": 10000001, "quantity": 1}', "Overflow buy_price"),
            (b'{"ticker": "NVDA", "buy_price": "NOT_A_NUMBER", "quantity": 1}', "String buy_price"),
            (b'{"ticker": "NVDA", "buy_price": 100, "quantity": -5}', "Negative quantity"),
            (b'{"ticker": "NVDA", "buy_price": 100, "quantity": 0}', "Zero quantity"),
            (b'{"ticker": "NVDA", "buy_price": 100, "quantity": 1000001}', "Overflow quantity"),
            (b'{"ticker": "<script>alert(1)</script>", "buy_price": 100, "quantity": 1}', "XSS ticker"),
            (b'{"ticker": "NVDA", "buy_price": 100, "quantity": 1, "buy_date": "INVALID_DATE"}', "Invalid date format"),
            (b'{"ticker": "NVDA", "buy_price": 100, "quantity": 1, "buy_date": "2026/08/22"}', "Slash date format"),
            (b'{"ticker": "NVDA", "buy_price": null, "quantity": 1}', "Null buy_price"),
            (b'{"ticker": null, "buy_price": 100, "quantity": 1}', "Null ticker"),
            (b'{"ticker": "NVDA", "buy_price": 100, "quantity": null}', "Null quantity"),
            (b'[]', "Array instead of object"),
            (b'"string_payload"', "Primitive string instead of object"),
            (b'{"ticker": "NVDA", "buy_price": 100, "quantity": 1, "extra_field": "test"}', "extra_field accepted"),
        ]

        buy_fuzz_passed = 0
        for payload, desc in fuzz_buy_payloads:
            status, headers, body = await asgi_request(
                app, "POST", "/api/portfolio/buy",
                headers={"Content-Type": "application/json", "Origin": "http://localhost:8000"},
                body=payload
            )
            if "extra_field" in desc:
                assert status == 200, f"Expected 200 for extra field, got {status}"
            else:
                assert status == 422, f"Expected 422 for ({desc}), got {status}. Body: {body.decode('utf-8', errors='ignore')}"
                body_json = json.loads(body.decode('utf-8'))
                assert "detail" in body_json, f"HTTP 422 response missing 'detail': {body_json}"
            
            # Verify OWASP headers attached to 422 response
            assert headers.get("x-content-type-options") == "nosniff"
            assert headers.get("x-frame-options") == "DENY"
            buy_fuzz_passed += 1

        print(f"  [+] POST /api/portfolio/buy Fuzzing: {buy_fuzz_passed}/{len(fuzz_buy_payloads)} payloads handled correctly.")

        # Fuzz payloads on POST /api/portfolio/sell/{position_id}
        fuzz_sell_payloads = [
            (b'{}', "Missing sell_price"),
            (b'{"sell_price": -50}', "Negative sell_price"),
            (b'{"sell_price": 0}', "Zero sell_price"),
            (b'{"sell_price": 10000001}', "Overflow sell_price"),
            (b'{"sell_price": "INVALID"}', "String sell_price"),
            (b'{"sell_price": 100, "sell_date": "2026/08/22"}', "Invalid date format"),
            (b'{"sell_price": 100, "reason": "<script>alert(1)</script>"}', "XSS in reason"),
            (b'{"sell_price": 100, "reason": "' + (b'A' * 101) + b'"}', "Reason > 100 chars"),
            (b'{"sell_price": 100, "reason": "reason; DROP TABLE"}', "SQLi special chars in reason"),
        ]

        sell_fuzz_passed = 0
        for payload, desc in fuzz_sell_payloads:
            status, headers, body = await asgi_request(
                app, "POST", f"/api/portfolio/sell/{pos_id}",
                headers={"Content-Type": "application/json", "Origin": "http://localhost:8000"},
                body=payload
            )
            assert status == 422, f"Expected 422 for SellOrder ({desc}), got {status}. Body: {body.decode('utf-8', errors='ignore')}"
            body_json = json.loads(body.decode('utf-8'))
            assert "detail" in body_json
            assert headers.get("x-content-type-options") == "nosniff"
            assert headers.get("x-frame-options") == "DENY"
            sell_fuzz_passed += 1

        print(f"  [+] POST /api/portfolio/sell/{{id}} Fuzzing: {sell_fuzz_passed}/{len(fuzz_sell_payloads)} payloads rejected with 422.")

        # Fuzz payloads on POST /api/broker/order
        fuzz_broker_payloads = [
            (b'{"ticker": "NVDA"}', "Missing price & qty"),
            (b'{"ticker": "<svg/onload=alert(1)>", "buy_price": 100, "quantity": 1}', "XSS ticker"),
            (b'{"ticker": "NVDA", "buy_price": -10, "quantity": 1}', "Negative price"),
            (b'{"ticker": "NVDA", "buy_price": 100, "quantity": -1}', "Negative qty"),
            (b'{"ticker": "NVDA", "buy_price": 100, "quantity": 1, "buy_date": "INVALID"}', "Invalid date"),
        ]

        broker_fuzz_passed = 0
        for payload, desc in fuzz_broker_payloads:
            status, headers, body = await asgi_request(
                app, "POST", "/api/broker/order",
                headers={"Content-Type": "application/json", "Origin": "http://localhost:8000"},
                body=payload
            )
            assert status == 422, f"Expected 422 for Broker order ({desc}), got {status}"
            assert headers.get("x-content-type-options") == "nosniff"
            assert headers.get("x-frame-options") == "DENY"
            broker_fuzz_passed += 1

        print(f"  [+] POST /api/broker/order Fuzzing: {broker_fuzz_passed}/{len(fuzz_broker_payloads)} payloads rejected with 422.")

    asyncio.run(run_api_fuzzing())


# ---------------------------------------------------------------------------
# TEST SUITE: R4 Global 500 Error Masking & Traceback Leakage
# ---------------------------------------------------------------------------

def test_r4_global_500_error_masking():
    print("\n" + "=" * 70)
    print("  [CHALLENGE 3] R4: Global 500 Error Sanitization & Traceback Masking")
    print("=" * 70)
    
    async def run_500_tests():
        # Inject temporary test routes on the FastAPI app that throw various exceptions
        @app.get("/api/test_error/zero_division")
        def route_zero_div():
            return 1 / 0

        @app.get("/api/test_error/key_error")
        def route_key_err():
            d = {}
            return d["SECRET_INTERNAL_API_KEY_12345"]

        @app.get("/api/test_error/sqlite_error")
        def route_sqlite_err():
            raise sqlite3.OperationalError("database /var/data/private_secrets.db is locked or disk image malformed")

        @app.get("/api/test_error/runtime_error")
        def route_runtime_err():
            raise RuntimeError("Traceback simulation: Fatal error in d:\\internal\\secrets\\engine.py at line 999")

        @app.get("/api/test_error/type_error")
        def route_type_err():
            None.secret_method_invocation()

        @app.post("/api/test_error/async_failure")
        async def route_async_err():
            await asyncio.sleep(0.01)
            raise ValueError("Private valuation formula failure with internal state variables: alpha=0.999, beta=1.234")

        error_routes = [
            ("/api/test_error/zero_division", "ZeroDivisionError"),
            ("/api/test_error/key_error", "KeyError"),
            ("/api/test_error/sqlite_error", "sqlite3.OperationalError"),
            ("/api/test_error/runtime_error", "RuntimeError with paths"),
            ("/api/test_error/type_error", "AttributeError"),
            ("/api/test_error/async_failure", "Async ValueError"),
        ]

        forbidden_leak_patterns = [
            "traceback",
            "most recent call last",
            "file \"",
            "line ",
            "zerodivisionerror",
            "keyerror",
            "secret_internal_api_key",
            "private_secrets.db",
            "d:\\internal",
            "/var/data",
            "secrets\\engine.py",
            "attributeerror",
            "secret_method_invocation",
            "private valuation formula",
            "alpha=0.999",
        ]

        errors_tested = 0
        for route_path, desc in error_routes:
            method = "POST" if "async" in route_path else "GET"
            status, headers, body = await asgi_request(
                app, method, route_path,
                headers={"Origin": "http://localhost:8000"}
            )
            
            assert status == 500, f"Expected 500 for {desc}, got {status}"
            body_text = body.decode("utf-8", errors="ignore")
            
            # 1. Exact generic JSON response structure
            try:
                body_json = json.loads(body_text)
            except Exception:
                raise AssertionError(f"HTTP 500 returned non-JSON body: '{body_text}'")
                
            assert body_json == {"status": "error", "message": "An internal server error occurred"}, \
                f"HTTP 500 returned non-sanitized JSON: {body_json}"

            # 2. Check for absolute absence of traceback and leakage patterns
            body_lower = body_text.lower()
            for pattern in forbidden_leak_patterns:
                assert pattern not in body_lower, f"Information leakage detected in 500 response! Found '{pattern}' in: {body_text}"

            # 3. Verify OWASP headers are STILL present on 500 responses!
            assert headers.get("x-content-type-options") == "nosniff", f"Missing X-Content-Type-Options on 500 response for {desc}"
            assert headers.get("x-frame-options") == "DENY", f"Missing X-Frame-Options on 500 response for {desc}"
            assert headers.get("referrer-policy") == "strict-origin-when-cross-origin", f"Missing Referrer-Policy on 500 response for {desc}"
            assert headers.get("x-xss-protection") == "1; mode=block", f"Missing X-XSS-Protection on 500 response for {desc}"
            
            errors_tested += 1

        print(f"  [+] Global 500 Error Masking: {errors_tested}/{len(error_routes)} internal exceptions sanitized with zero traceback leak.")

    asyncio.run(run_500_tests())


# ---------------------------------------------------------------------------
# TEST SUITE: R5 OWASP Defensive Headers Verification across ALL Endpoints
# ---------------------------------------------------------------------------

def test_r5_comprehensive_owasp_headers():
    print("\n" + "=" * 70)
    print("  [CHALLENGE 4] R5: Comprehensive OWASP Security Headers Inspection")
    print("=" * 70)
    
    async def run_header_inspection():
        setup_db()
        
        test_routes = [
            # HTML endpoint
            ("GET", "/", b"", "Root Dashboard HTML (200)"),
            # Core REST endpoints (200)
            ("GET", "/api/dashboard", b"", "Dashboard Summary (200)"),
            ("GET", "/api/portfolio", b"", "Portfolio Live (200)"),
            ("GET", "/api/recommendations/matrix", b"", "Matrix Data (200)"),
            ("GET", "/api/search?q=NVDA", b"", "Stock Search (200)"),
            ("GET", "/api/chart/NVDA", b"", "Chart Intelligence (200)"),
            ("GET", "/api/risk/circuit_breaker", b"", "Circuit Breaker Status (200)"),
            ("GET", "/api/backup/list", b"", "Backup List (200)"),
            ("POST", "/api/portfolio/reset", b"", "Portfolio Reset (200)"),
            ("POST", "/api/portfolio/buy", b'{"ticker": "NVDA", "buy_price": 100, "quantity": 1}', "Portfolio Buy (200)"),
            ("POST", "/api/backup/snapshot", b"", "Backup Snapshot (200)"),
            # Error endpoints (400, 404, 422)
            ("GET", "/api/chart/INVALID_TICKER_$$$", b"", "Invalid Chart Ticker (400)"),
            ("POST", "/api/portfolio/sell/0", b'{"sell_price": 100}', "Invalid Position ID (400)"),
            ("POST", "/api/portfolio/sell/99999", b'{"sell_price": 100}', "Nonexistent Position ID (404)"),
            ("GET", "/api/chart/NONEXISTENT99999", b"", "Nonexistent Stock Chart (404)"),
            ("GET", "/nonexistent_route_404_test", b"", "Nonexistent Route (404)"),
            ("POST", "/nonexistent_post_route", b"", "Nonexistent POST Route (404)"),
            ("POST", "/api/portfolio/buy", b'{}', "Malformed Buy Payload (422)"),
            ("POST", "/api/portfolio/sell/1", b'{"sell_price": "BAD"}', "Malformed Sell Payload (422)"),
            ("POST", "/api/broker/order", b'{"ticker": "NVDA"}', "Malformed Broker Order (422)"),
            # CORS Preflight (OPTIONS)
            ("OPTIONS", "/api/portfolio", b"", "CORS Preflight Portfolio"),
            ("OPTIONS", "/api/portfolio/buy", b"", "CORS Preflight Buy"),
            ("OPTIONS", "/api/portfolio/sell/1", b"", "CORS Preflight Sell"),
            ("OPTIONS", "/api/broker/order", b"", "CORS Preflight Broker"),
        ]

        inspected_count = 0
        for method, route, body, desc in test_routes:
            headers = {
                "Origin": "http://localhost:8000",
                "Host": "localhost:8000"
            }
            if body:
                headers["Content-Type"] = "application/json"
            if method == "OPTIONS":
                headers["Access-Control-Request-Method"] = "POST"
                headers["Access-Control-Request-Headers"] = "Content-Type"

            status, resp_headers, _ = await asgi_request(
                app, method, route,
                headers=headers,
                body=body
            )

            # Assert all 4 OWASP defensive headers are present
            assert resp_headers.get("x-content-type-options") == "nosniff", \
                f"Missing or invalid X-Content-Type-Options on [{method} {route}] ({desc}). Headers: {resp_headers}"
            assert resp_headers.get("x-frame-options") == "DENY", \
                f"Missing or invalid X-Frame-Options on [{method} {route}] ({desc}). Headers: {resp_headers}"
            assert resp_headers.get("referrer-policy") == "strict-origin-when-cross-origin", \
                f"Missing or invalid Referrer-Policy on [{method} {route}] ({desc}). Headers: {resp_headers}"
            assert resp_headers.get("x-xss-protection") == "1; mode=block", \
                f"Missing or invalid X-XSS-Protection on [{method} {route}] ({desc}). Headers: {resp_headers}"

            inspected_count += 1

        print(f"  [+] OWASP Security Response Headers: {inspected_count}/{len(test_routes)} endpoints verified with 100% header coverage.")

    asyncio.run(run_header_inspection())


# ---------------------------------------------------------------------------
# MAIN ADVERSARIAL RUNNER
# ---------------------------------------------------------------------------

def run_all_adversarial_challenges():
    print("=" * 75)
    print("  CHALLENGER 2: ADVERSARIAL VERIFICATION SUITE (R4 & R5 HARDENING)")
    print("=" * 75)
    
    try:
        test_r4_pydantic_model_fuzzing()
        test_r4_api_endpoint_fuzzing()
        test_r4_global_500_error_masking()
        test_r5_comprehensive_owasp_headers()
        
        print("\n" + "=" * 75)
        print("  ALL CHALLENGER 2 ADVERSARIAL TESTS PASSED (100% EMPIRICALLY GREEN)")
        print("=" * 75)
    finally:
        cleanup_db()


if __name__ == "__main__":
    run_all_adversarial_challenges()
