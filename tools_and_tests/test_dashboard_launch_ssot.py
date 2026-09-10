"""Dashboard launch SSOT: bat cache-bust, single ES-module graph, KIS quote TR pairing."""
import os
import re
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.infrastructure.brokers.kis_broker import KISBrokerAdapter


def _read(rel):
    with open(os.path.join(PROJECT_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class TestFrontendAssetGraph(unittest.TestCase):
    def test_all_es_imports_share_one_asset_version(self):
        files = [
            "frontend/index.html",
            "frontend/js/api.js",
            "frontend/js/chart.js",
            "frontend/js/ui.js",
            "frontend/js/websocket.js",
        ]
        versions = set()
        chart_versions = set()
        for rel in files:
            src = _read(rel)
            versions.update(re.findall(r"\?v=([0-9.]+)", src))
            chart_versions.update(re.findall(r"chart\.js\?v=([0-9.]+)", src))
        self.assertTrue(versions, "expected cache-bust query strings")
        self.assertEqual(len(versions), 1, f"split asset versions would duplicate ChartEngine: {versions}")
        self.assertLessEqual(len(chart_versions), 1, f"split ChartEngine instances: {chart_versions}")

    def test_index_disables_html_cache_and_uses_modular_shell(self):
        src = _read("frontend/index.html")
        self.assertIn("no-store", src)
        self.assertIn("toastHost", src)
        self.assertNotIn("NVDA, PLTR, AAPL", src)
        self.assertIn("티커 또는 종목명", src)
        self.assertIn("btnExecuteBuy", src)
        self.assertIn("기준선/2.5×ATR", src)
        self.assertNotIn("50% 절반익절", src)

    def test_idempotent_broker_tests_mock_execution_log(self):
        src = _read("tools_and_tests/test_idempotent_kis_order.py")
        self.assertIn("record_execution_log", src)
        self.assertIn("AL_SANGMOO_DB_PATH", src)
        self.assertIn("kis_idempotent_", src)

    def test_bat_waits_for_listen_and_cache_busts_browser(self):
        src = _read("알상무_퀀트_터미널_실행.bat")
        self.assertIn("LISTENING", src)
        self.assertIn("?boot=", src)
        self.assertIn("server.py", src)
        self.assertIn("localhost:8000", src)


class TestKisQuoteTrPairing(unittest.TestCase):
    def test_live_price_hits_quotations_price_with_matching_tr(self):
        adapter = KISBrokerAdapter(app_key="k", app_secret="s", account_no="12345678", mode="vps")
        adapter.token = "tok"
        adapter.token_expiry = time.time() + 3600
        captured = []

        def fake_get(url, headers=None, params=None, timeout=None):
            captured.append((url, (headers or {}).get("tr_id"), timeout, params))
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"rt_cd": "0", "output": {"last": "173.25"}}
            return resp

        with patch("al_sangmoo.infrastructure.brokers.kis_broker.requests.get", side_effect=fake_get):
            px = adapter.get_live_price("NVDA", fallback_yahoo=False)

        self.assertEqual(px, 173.25)
        self.assertTrue(captured)
        url, tr_id, timeout, params = captured[0]
        self.assertIn("/quotations/price", url)
        self.assertNotIn("price-detail", url)
        self.assertEqual(tr_id, "HHDFS00000300")
        self.assertGreaterEqual(timeout, 2.0)
        self.assertEqual(params.get("SYMB"), "NVDA")
        self.assertEqual(params.get("EXCD"), "NAS")

    def test_kis_price_uses_zdiv_and_ignores_krw_fields(self):
        from al_sangmoo.infrastructure.brokers.kis_broker import KISBrokerAdapter
        px = KISBrokerAdapter._price_from_kis_output({"last": "22612", "zdiv": "2", "stck_prpr": "1653000"})
        self.assertEqual(px, 226.12)
        dotted = KISBrokerAdapter._price_from_kis_output({"last": "128.10", "zdiv": "2"})
        self.assertEqual(dotted, 128.10)
        krw_only = KISBrokerAdapter._price_from_kis_output({"stck_prpr": "1653000", "base": "1380"})
        self.assertIsNone(krw_only)

    def test_live_price_skips_yahoo_when_disabled(self):
        adapter = KISBrokerAdapter(app_key="k", app_secret="s", account_no="12345678", mode="vps")
        adapter.token = "tok"
        adapter.token_expiry = time.time() + 3600

        def fake_get(url, headers=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"rt_cd": "1", "msg1": "fail"}
            return resp

        with patch("al_sangmoo.infrastructure.brokers.kis_broker.requests.get", side_effect=fake_get):
            with patch.dict("sys.modules", {"yfinance": MagicMock()}):
                px = adapter.get_live_price("NVDA", fallback_yahoo=False)
        self.assertIsNone(px)


class TestDashboardCacheHeaders(unittest.IsolatedAsyncioTestCase):
    async def test_root_html_is_not_cached(self):
        from server import app
        from tools_and_tests.test_phase5_2_concurrency import asgi_request

        status, headers, body = await asgi_request(app, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("no-store", headers.get("cache-control", ""))
        html = body.decode("utf-8")
        self.assertIn("R QUANT TERMINAL", html)
        self.assertIn("toastHost", html)

    async def test_api_health_is_not_cached(self):
        from server import app
        from tools_and_tests.test_phase5_2_concurrency import asgi_request

        status, headers, body = await asgi_request(app, "GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertIn("no-store", headers.get("cache-control", ""))
        self.assertIn(b'"status"', body)


if __name__ == "__main__":
    unittest.main()
