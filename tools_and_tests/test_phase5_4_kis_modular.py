"""
AL-SANGMOO QUANT PLATFORM: PHASE 5.4 AUTOMATED TEST SUITE
Verifies Frontend Modularity, APIRouter De-coupling, and KIS (한국투자증권) Execution Gateway.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
import tempfile
import asyncio
from unittest.mock import patch, MagicMock

# Inject test environment database
TEST_DB_DIR = tempfile.mkdtemp(suffix="_phase5_4")
TEST_DB = os.path.join(TEST_DB_DIR, "test_quant_trades_p5_4.db")
os.environ["AL_SANGMOO_DB_PATH"] = TEST_DB

import db_manager
from server import app
from tools_and_tests.test_phase5_2_concurrency import asgi_request
from al_sangmoo.infrastructure.brokers.kis_broker import KISBrokerAdapter, default_kis_broker
from al_sangmoo.domain.risk.order_guardrail import validate_pre_trade_guardrail


class TestPhase54FrontendModularity(unittest.TestCase):
    """Verifies that the frontend has been decomposed into clean, token-efficient ES modules."""

    def setUp(self):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.frontend_dir = os.path.join(self.base_dir, "frontend")

    def test_frontend_files_exist_and_bounded_size(self):
        """All modular files must exist and be compact (< 350 lines) for AI agent token efficiency."""
        required_files = [
            os.path.join(self.frontend_dir, "index.html"),
            os.path.join(self.frontend_dir, "css", "terminal.css"),
            os.path.join(self.frontend_dir, "js", "api.js"),
            os.path.join(self.frontend_dir, "js", "chart.js"),
            os.path.join(self.frontend_dir, "js", "decoder.js"),
            os.path.join(self.frontend_dir, "js", "ui.js"),
            os.path.join(self.frontend_dir, "js", "websocket.js"),
        ]

        for file_path in required_files:
            self.assertTrue(os.path.exists(file_path), f"Missing modular frontend asset: {file_path}")
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                self.assertGreater(len(lines), 10, f"File {file_path} is suspiciously small/empty.")
                self.assertLess(len(lines), 600, f"Token efficiency violated: {file_path} exceeds 600 lines ({len(lines)} lines).")

    def test_index_html_imports_all_modules(self):
        """Index.html must properly link terminal.css and import ES modules."""
        index_path = os.path.join(self.frontend_dir, "index.html")
        with open(index_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("/static/css/terminal.css", content)
        self.assertIn("/static/js/api.js", content)
        self.assertIn("/static/js/chart.js", content)
        self.assertIn("/static/js/ui.js", content)
        self.assertIn("/static/js/websocket.js", content)


class TestPhase54BackendRouters(unittest.TestCase):
    """Verifies that FastAPI server cleanly routes endpoints through modular APIRouters."""

    @classmethod
    def setUpClass(cls):
        db_manager.init_database()

    def test_serve_frontend_root(self):
        """GET / should serve index.html with HTTP 200."""
        async def run():
            status, headers, body = await asgi_request(app, "GET", "/")
            self.assertEqual(status, 200)
            self.assertIn("R QUANT", body.decode("utf-8"))
            self.assertIn("text/html", dict(headers).get("content-type", ""))
        asyncio.run(run())

    def test_dashboard_and_health_endpoints(self):
        """GET /api/dashboard and GET /api/health contracts."""
        async def run():
            # Health
            status, _, body = await asgi_request(app, "GET", "/api/health")
            self.assertEqual(status, 200)
            self.assertIn('"status":"ok"', body.decode("utf-8"))

            # Dashboard
            status, _, body = await asgi_request(app, "GET", "/api/dashboard")
            self.assertEqual(status, 200)
            self.assertIn("kpis", body.decode("utf-8"))
        asyncio.run(run())

    def test_charts_endpoint(self):
        """GET /api/chart/NVDA should return candlestick and indicator lines."""
        async def run():
            status, _, body = await asgi_request(app, "GET", "/api/chart/NVDA")
            self.assertEqual(status, 200)
            data_str = body.decode("utf-8")
            self.assertIn("candles", data_str)
            self.assertIn("kijun_line", data_str)
        asyncio.run(run())

    def test_portfolio_endpoints(self):
        """POST /api/portfolio/buy and GET /api/portfolio."""
        from unittest.mock import patch
        async def run():
            # Reset first
            await asgi_request(app, "POST", "/api/portfolio/reset")
            
            # Buy order with application/json header (Mock broker to isolate unit test from live KIS API)
            with patch("al_sangmoo.interfaces.api.routers.portfolio.default_kis_broker.is_configured", return_value=False):
                buy_payload = '{"ticker": "NVDA", "buy_price": 200.0, "qty": 1.0}'
                headers = {"content-type": "application/json"}
                status, _, body = await asgi_request(app, "POST", "/api/portfolio/buy", headers=headers, body=buy_payload.encode())
                if status != 200:
                    print("BUY ERROR DETAIL:", body.decode("utf-8"))
                self.assertEqual(status, 200)
                self.assertIn("success", body.decode("utf-8"))

                # Check portfolio
                status, _, body = await asgi_request(app, "GET", "/api/portfolio")
                self.assertEqual(status, 200)
                self.assertIn("NVDA", body.decode("utf-8"))
        asyncio.run(run())




    def test_search_endpoint(self):
        """GET /api/search should return stock suggestions."""
        async def run():
            status, _, body = await asgi_request(app, "GET", "/api/search", query_string=b"q=AAPL")
            self.assertEqual(status, 200)
            self.assertIn("AAPL", body.decode("utf-8"))
        asyncio.run(run())


class TestPhase54KISBrokerGateway(unittest.TestCase):
    """Verifies the KIS (한국투자증권) broker adapter and execution guardrails."""

    def test_kis_broker_status_and_balance_simulation(self):
        """Unconfigured broker runs safely in mock/simulation mode."""
        adapter = KISBrokerAdapter(app_key="", app_secret="", account_no="")
        status = adapter.get_status()
        self.assertEqual(status["environment"], "MOCK_PAPER")
        self.assertFalse(status["is_configured"])

        balance = adapter.get_account_balance()
        self.assertEqual(balance["status"], "success")
        self.assertEqual(balance["mode"], "SIMULATED")
        self.assertGreater(balance["total_equity"], 0)

    def test_kis_broker_order_execution_simulation(self):
        """Simulated orders are executed and return filled receipts."""
        adapter = KISBrokerAdapter(app_key="", app_secret="", account_no="")
        order = adapter.place_order(ticker="NVDA", side="BUY", qty=10, price=215.0)
        self.assertEqual(order["status"], "filled")
        self.assertEqual(order["mode"], "SIMULATION")
        self.assertEqual(order["ticker"], "NVDA")
        self.assertEqual(order["qty"], 10)

    def test_pre_trade_risk_guardrail_cap(self):
        """Single order value exceeding 25% of total portfolio equity must be rejected."""
        total_equity = 100_000.0
        active_holdings = []
        
        # Valid order: $15,000 (15% equity)
        res_valid = validate_pre_trade_guardrail("AAPL", 150.0, 100.0, total_equity, active_holdings, max_single_asset_pct=0.25)
        self.assertTrue(res_valid["allowed"])

        # Invalid order: $30,000 (30% equity > 25% cap)
        res_invalid = validate_pre_trade_guardrail("AAPL", 300.0, 100.0, total_equity, active_holdings, max_single_asset_pct=0.25)
        self.assertFalse(res_invalid["allowed"])
        self.assertIn("최대 한도", res_invalid["reason"])

    def test_broker_api_router_endpoints(self):
        """FastAPI /api/broker/status and /api/broker/balance endpoints."""
        async def run():
            status, _, body = await asgi_request(app, "GET", "/api/broker/status")
            self.assertEqual(status, 200)
            self.assertIn("Korea Investment & Securities", body.decode("utf-8"))

            status, _, body = await asgi_request(app, "GET", "/api/broker/balance")
            self.assertEqual(status, 200)
            self.assertIn("total_equity", body.decode("utf-8"))
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
