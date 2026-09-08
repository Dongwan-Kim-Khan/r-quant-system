"""Regression tests for the 2026-08-28 code-review remediation list."""
from __future__ import annotations

import ast
import hashlib
import hmac
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("AL_SANGMOO_DB_PATH", os.path.join(PROJECT_ROOT, "test_quant_trades_review_fix.db"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from al_sangmoo.core.auth import require_mutating_auth
from al_sangmoo.core.market_time import us_equity_session_date
from al_sangmoo.interfaces.api.routers.webhook import router as webhook_router


class TestSec01Auth(unittest.TestCase):
    def setUp(self):
        self._saved = {
            k: os.environ.get(k)
            for k in ("REQUIRE_AUTH", "API_KEY", "AL_SANGMOO_API_KEY")
        }

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_require_auth_rejects_missing_key(self):
        os.environ["REQUIRE_AUTH"] = "true"
        os.environ["API_KEY"] = "review-secret"
        os.environ.pop("AL_SANGMOO_API_KEY", None)
        from server import app

        client = TestClient(app)
        res = client.post("/api/scan_now")
        self.assertEqual(res.status_code, 401)

    def test_require_auth_accepts_matching_header(self):
        os.environ["REQUIRE_AUTH"] = "true"
        os.environ["API_KEY"] = "review-secret"
        os.environ.pop("AL_SANGMOO_API_KEY", None)
        from unittest.mock import patch
        from server import app

        client = TestClient(app)
        with patch("server.build_dashboard_data", return_value={"macro": {}, "tier1": []}):
            res = client.post("/api/scan_now", headers={"X-API-Key": "review-secret"})
        self.assertEqual(res.status_code, 200)

    def test_mutating_routes_declare_auth_dependency(self):
        from al_sangmoo.interfaces.api.routers import autopilot, broker, guardian, portfolio, scanner

        for router in (portfolio.router, broker.router, guardian.router, autopilot.router, scanner.router):
            posts = [r for r in router.routes if getattr(r, "methods", None) and "POST" in r.methods]
            self.assertTrue(posts, f"expected POST routes on {router}")
            for route in posts:
                dep_fns = [getattr(d, "dependency", None) for d in getattr(route, "dependencies", [])]
                if require_mutating_auth not in dep_fns:
                    nested = [getattr(d, "call", None) for d in getattr(getattr(route, "dependant", None), "dependencies", []) or []]
                    self.assertIn(require_mutating_auth, nested, f"{route.path} missing auth dependency")


class TestScanAndV1(unittest.TestCase):
    def test_scanner_does_not_call_v1_scan(self):
        src = Path(PROJECT_ROOT, "al_sangmoo", "interfaces", "api", "routers", "scanner.py").read_text(encoding="utf-8")
        self.assertNotIn("scan_and_select_2x2x2", src)
        self.assertIn("build_dashboard_data", src)

    def test_scan_function_does_not_map_snipers_to_bear(self):
        src = Path(PROJECT_ROOT, "al_sangmoo_daily_bot.py").read_text(encoding="utf-8")
        self.assertNotIn("bear_picks = tier3_picks", src)
        self.assertNotIn("bear_picks = strat2_exclusive", src)
        tree = ast.parse(src)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "get_macro_tailwind_sectors"
        ]
        self.assertTrue(calls)
        first = calls[0]
        self.assertTrue(first.args, "get_macro_tailwind_sectors must receive numeric MSI, not a stance string")
        arg0 = first.args[0]
        self.assertFalse(isinstance(arg0, ast.Constant) and isinstance(arg0.value, str))
        self.assertNotEqual(getattr(arg0, "id", None), "macro_stance")


class TestV103FeedContract(unittest.TestCase):
    def test_feed_emits_tier_keys(self):
        src = Path(PROJECT_ROOT, "generate_dashboard_feed.py").read_text(encoding="utf-8")
        self.assertIn('"tier1": dual_consensus_picks', src)
        self.assertIn('"tier2": strat1_exclusive', src)
        self.assertIn('"tier3": strat2_exclusive', src)

    def test_ui_prefers_tier1(self):
        src = Path(PROJECT_ROOT, "frontend", "js", "ui.js").read_text(encoding="utf-8")
        self.assertIn("data.tier1", src)


class TestSec02Webhook(unittest.TestCase):
    def test_valid_signature_does_not_claim_queued(self):
        os.environ["USE_WEBHOOK_ROUTER"] = "true"
        os.environ["WEBHOOK_SECRET"] = "hook-secret"
        app = FastAPI()
        app.include_router(webhook_router)
        body = b'{"ticker":"NVDA","action":"BUY"}'
        sig = hmac.new(b"hook-secret", body, hashlib.sha256).hexdigest()
        client = TestClient(app)
        res = client.post(
            "/api/v2/webhook/tradingview",
            content=body,
            headers={"X-Signature": sig, "Content-Type": "application/json"},
        )
        self.assertEqual(res.status_code, 501)
        payload = res.json()
        self.assertNotEqual(payload.get("queued"), True)
        os.environ.pop("USE_WEBHOOK_ROUTER", None)
        os.environ.pop("WEBHOOK_SECRET", None)


class TestLeg01Arch01(unittest.TestCase):
    def test_server_has_no_legacy_html_fallback(self):
        src = Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
        self.assertNotIn("LEGACY_DASHBOARD", src)
        self.assertNotIn("al_sangmoo_dashboard.html", src)

    def test_live_packages_do_not_import_db_manager_facade(self):
        roots = [
            Path(PROJECT_ROOT, "al_sangmoo"),
            Path(PROJECT_ROOT, "server.py"),
            Path(PROJECT_ROOT, "generate_dashboard_feed.py"),
            Path(PROJECT_ROOT, "al_sangmoo_daily_bot.py"),
        ]
        offenders = []
        for root in roots:
            files = [root] if root.is_file() else list(root.rglob("*.py"))
            for path in files:
                text = path.read_text(encoding="utf-8")
                if "import db_manager" in text or "from db_manager" in text:
                    offenders.append(str(path.relative_to(PROJECT_ROOT)))
        self.assertEqual(offenders, [])


class TestRes01Tz01(unittest.TestCase):
    def test_adversarial_reason_uses_four_percent_stop(self):
        src = Path(PROJECT_ROOT, "tools_and_tests", "test_adversarial_challenger1.py").read_text(encoding="utf-8")
        self.assertIn("손절선 (-4.0%)", src)
        self.assertNotIn("손절선 (-3.0%)", src)

    def test_us_session_date_uses_est_in_january(self):
        winter = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)  # 09:00 EST
        self.assertEqual(us_equity_session_date(winter), "2026-01-14")
        winter_open = datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc)  # 10:00 EST
        self.assertEqual(us_equity_session_date(winter_open), "2026-01-15")

    def test_us_session_date_uses_edt_in_july(self):
        summer = datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc)  # 09:00 EDT
        self.assertEqual(us_equity_session_date(summer), "2026-07-14")
        summer_open = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)  # 10:00 EDT
        self.assertEqual(us_equity_session_date(summer_open), "2026-07-15")


if __name__ == "__main__":
    unittest.main()
