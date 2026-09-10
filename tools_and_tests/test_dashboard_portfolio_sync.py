"""
Test verifying SSOT synchronization between active portfolio and slot visualizer,
and ensuring guardian/autopilot daemon status formats match frontend expectations.
"""
import asyncio
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.infrastructure.persistence import get_live_portfolio
from al_sangmoo.domain.risk.portfolio_guardian import default_guardian
from al_sangmoo.domain.risk.autopilot_trader import AutoPilotTrader
from al_sangmoo.interfaces.api.routers.dashboard import get_dashboard_data
from al_sangmoo.core.constants import is_market_ticker, SLOT_WEIGHTS_BULL, SLOT_WEIGHTS_BEAR


class TestDashboardPortfolioSynchronicity(unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_portfolio_matches_live_portfolio_ssot(self):
        """Verify /api/dashboard portfolio holdings match get_live_portfolio()."""
        dash_data = await get_dashboard_data()
        self.assertIn("portfolio", dash_data)
        self.assertIn("slot_allocation_summary", dash_data)

        live_port = get_live_portfolio()
        live_tickers = [h["ticker"] for h in live_port.get("holdings", []) if is_market_ticker(h.get("ticker"))]
        dash_tickers = [h["ticker"] for h in dash_data["portfolio"].get("holdings", []) if is_market_ticker(h.get("ticker"))]

        self.assertEqual(live_tickers, dash_tickers, "Dashboard portfolio must be in lockstep with get_live_portfolio SSOT")

        # Check slot_allocation_summary counts
        slot_summary = dash_data["slot_allocation_summary"]
        from al_sangmoo.domain.risk.cash_proxy import satellite_holdings, proxy_holdings
        h_list = live_port.get("holdings", [])
        expected_sat_count = len(satellite_holdings(h_list))
        self.assertEqual(slot_summary.get("satellite_count"), expected_sat_count)

    def test_guardian_status_schema_contains_is_enabled(self):
        """Frontend checks st.is_enabled and st.enabled; ensure backend provides is_enabled."""
        status = default_guardian.get_status()
        self.assertIn("is_enabled", status)
        self.assertIsInstance(status["is_enabled"], bool)
        self.assertIn("rules", status)
        self.assertEqual(status["rules"].get("stop_loss_pct"), -5.0)

    def test_autopilot_status_schema_contains_is_enabled(self):
        """Frontend checks st.is_enabled; ensure backend provides is_enabled."""
        pilot = AutoPilotTrader()
        status = pilot.get_status()
        self.assertIn("is_enabled", status)
        self.assertIsInstance(status["is_enabled"], bool)

    def test_slot_weights_constants_match(self):
        """Ensure 50/30/20 Bull and 25/25 Bear constants are uniform."""
        self.assertEqual(SLOT_WEIGHTS_BULL, [0.50, 0.30, 0.20])
        self.assertEqual(SLOT_WEIGHTS_BEAR, [0.25, 0.25])

    def test_cash_proxy_tickers_recognition(self):
        """Verify QQQ and QLD are recognized as cash proxy tickers in domain."""
        from al_sangmoo.domain.risk.cash_proxy import is_proxy_ticker
        self.assertTrue(is_proxy_ticker("QQQ"))
        self.assertTrue(is_proxy_ticker("QLD"))
        self.assertFalse(is_proxy_ticker("NVDA"))
        self.assertFalse(is_proxy_ticker("DELL"))

    async def test_slot_allocation_summary_resilience(self):
        """Verify slot_allocation_summary is always present even if feed lacks it."""
        from unittest.mock import patch
        with patch("al_sangmoo.interfaces.api.routers.dashboard.get_feed_cache", return_value={}):
            dash_data = await get_dashboard_data()
            self.assertIn("slot_allocation_summary", dash_data)
            slot_sum = dash_data["slot_allocation_summary"]
            self.assertIn("satellite_count", slot_sum)
            self.assertIn("proxy_holdings", slot_sum)
            self.assertIn("is_bull_regime", slot_sum)


if __name__ == "__main__":
    unittest.main()
