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
from al_sangmoo.core.constants import (
    DEFAULT_BASE_ACCOUNT_USD,
    is_market_ticker,
    SLOT_WEIGHTS_BULL,
    SLOT_WEIGHTS_BEAR,
)


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
        self.assertLess(
            float(live_port.get("total_equity_usd") or 0),
            50000.0,
            "KIS tot_evlu_amt must not be treated as USD book NAV",
        )

        # Check slot_allocation_summary counts
        slot_summary = dash_data["slot_allocation_summary"]
        from al_sangmoo.domain.risk.cash_proxy import satellite_holdings, proxy_holdings
        h_list = [h for h in live_port.get("holdings", []) if is_market_ticker(h.get("ticker"))]
        expected_sat_count = len(satellite_holdings(h_list))
        self.assertEqual(slot_summary.get("satellite_count"), expected_sat_count)

    def test_guardian_status_schema_contains_is_enabled(self):
        """Frontend checks st.is_enabled and st.enabled; ensure backend provides is_enabled."""
        status = default_guardian.get_status()
        self.assertIn("is_enabled", status)
        self.assertIsInstance(status["is_enabled"], bool)
        self.assertIn("rules", status)
        self.assertEqual(status["rules"].get("stop_loss_pct"), -7.0)
        self.assertEqual(status["rules"].get("emergency_stop_loss_pct"), -10.0)

    def test_autopilot_status_schema_contains_is_enabled(self):
        """Frontend checks st.is_enabled; ensure backend provides is_enabled."""
        pilot = AutoPilotTrader()
        status = pilot.get_status()
        self.assertIn("is_enabled", status)
        self.assertIsInstance(status["is_enabled"], bool)

    def test_slot_weights_constants_match(self):
        """Ensure 34/33/33 Bull and 25/25 Bear constants are uniform."""
        self.assertEqual(SLOT_WEIGHTS_BULL, [0.34, 0.33, 0.33])
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

    def test_index_unifies_portfolio_div(self):
        html_path = os.path.join(PROJECT_ROOT, "frontend", "index.html")
        with open(html_path, encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn('id="portfolioPanel"', html)
        self.assertEqual(html.count('id="kpiTotalEquityUsd"'), 1)
        self.assertEqual(html.count('id="kpiFreeCashUsd"'), 1)
        self.assertEqual(html.count('id="kpiHoldingsEvalUsd"'), 1)
        self.assertEqual(html.count('id="portfolioTableBody"'), 1)
        self.assertLess(html.find('id="portfolioPanel"'), html.find('id="kpiTotalEquityUsd"'))
        self.assertLess(html.find('id="kpiTotalEquityUsd"'), html.find('id="portfolioTableBody"'))
        self.assertIn("<th>VALUE</th>", html)
        self.assertIn("<th>WEIGHT</th>", html)
        self.assertIn('id="portfolioPie"', html)
        self.assertIn('id="convictionPanel"', html)
        self.assertIn('id="tradeLogContainer"', html)
        self.assertIn('id="verdictBanner"', html)
        self.assertLess(html.find('id="convictionPanel"'), html.find('id="tradeLogContainer"'))
        self.assertEqual(html.count('id="tradeLogContainer"'), 1)
        self.assertIn('id="slotVisualizerGrid" hidden', html)
        self.assertNotIn("ACTIVE PORTFOLIO", html)
        self.assertNotIn("C-2 SLOT ALLOCATOR", html)
        ui_path = os.path.join(PROJECT_ROOT, "frontend", "js", "ui.js")
        with open(ui_path, encoding="utf-8") as fh:
            ui = fh.read()
        self.assertIn("preferLivePortfolio", ui)
        self.assertIn("sanitizePortfolioNav", ui)
        self.assertIn("holding-weight", ui)
        self.assertIn("renderAllocationPie", ui)
        self.assertIn("slice(0, this.MAX_LOGS)", ui)
        self.assertNotIn("logHidden", ui)
        self.assertIn('34/33/33', ui)
        self.assertIn('grid.innerHTML = ""', ui)
        self.assertIn("formatExecutionTs", ui)
        self.assertIn("[접수]", ui)
        self.assertIn("isConfirmedFill", ui)
        self.assertNotIn("rawTs.split(' ')[1]", ui)


class TestBrokerAccountSnapshotSSOT(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._prev = os.environ.get("AL_SANGMOO_DB_PATH")
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        os.environ["AL_SANGMOO_DB_PATH"] = self._tmp.name

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("AL_SANGMOO_DB_PATH", None)
        else:
            os.environ["AL_SANGMOO_DB_PATH"] = self._prev
        for suffix in ("", "-wal", "-shm"):
            path = self._tmp.name + suffix
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def test_live_portfolio_uses_broker_snapshot_nav(self):
        from al_sangmoo.infrastructure.persistence import (
            get_live_portfolio,
            save_account_snapshot,
        )
        local = get_live_portfolio()
        self.assertEqual(local.get("equity_source"), "LOCAL")
        self.assertAlmostEqual(
            float(local.get("total_equity_usd") or 0),
            DEFAULT_BASE_ACCOUNT_USD,
            places=2,
        )

        save_account_snapshot({
            "total_equity_usd": 12345.67,
            "cash_available_usd": 2345.67,
            "stock_eval_usd": 10000.0,
            "realized_pnl_usd": 12.0,
            "total_pnl_usd": 722.74,
            "total_pnl_pct": 11.06,
            "mode": "VIRTUAL_PAPER",
            "source": "KIS",
        })
        live = get_live_portfolio()
        self.assertEqual(live.get("equity_source"), "BROKER")
        self.assertAlmostEqual(float(live["total_equity_usd"]), 12345.67, places=2)
        self.assertAlmostEqual(float(live["free_cash_usd"]), 2345.67, places=2)
        self.assertAlmostEqual(float(live["overall_pnl_amount"]), 722.74, places=2)
        self.assertAlmostEqual(float(live["overall_pnl_pct"]), 11.06, places=2)
        self.assertAlmostEqual(float(live["initial_capital_usd"]), 11622.93, places=2)
        self.assertAlmostEqual(float(live["base_account_usd"]), 11622.93, places=2)

    def test_simulated_broker_snapshot_is_ignored(self):
        from al_sangmoo.domain.reconciliation import _persist_broker_account_snapshot
        from al_sangmoo.infrastructure.persistence import get_live_portfolio
        _persist_broker_account_snapshot({
            "mode": "SIMULATED",
            "total_equity_usd": 100000.0,
            "cash_available_usd": 75000.0,
            "stock_eval_usd": 25000.0,
        })
        live = get_live_portfolio()
        self.assertEqual(live.get("equity_source"), "LOCAL")
        self.assertAlmostEqual(
            float(live.get("total_equity_usd") or 0),
            DEFAULT_BASE_ACCOUNT_USD,
            places=2,
        )


class TestOverseasNavReconcile(unittest.TestCase):
    def test_kis_tot_evlu_amt_372k_is_not_usd_book(self):
        from al_sangmoo.domain.risk.cash_proxy import reconcile_overseas_nav
        rec = reconcile_overseas_nav(
            holdings_eval=7616.46,
            equity=372574.41,
            cash=0.0,
            stock_eval=372574.41,
        )
        self.assertFalse(rec["sane"])
        self.assertEqual(rec["source"], "HOLDINGS")
        self.assertAlmostEqual(float(rec["total_equity_usd"]), 7616.46, places=2)
        self.assertAlmostEqual(float(rec["stock_eval_usd"]), 7616.46, places=2)
        self.assertAlmostEqual(float(rec["cash_available_usd"]), 0.0, places=2)

    def test_krw_whole_account_converts_when_fx_ratio_matches(self):
        from al_sangmoo.domain.risk.cash_proxy import reconcile_overseas_nav
        rec = reconcile_overseas_nav(
            holdings_eval=7616.46,
            equity=7616.46 * 1380.0,
            cash=0.0,
            stock_eval=7616.46 * 1380.0,
            usd_krw_rate=1380.0,
        )
        self.assertTrue(rec["sane"])
        self.assertAlmostEqual(float(rec["total_equity_usd"]), 7616.46, places=0)


class TestInsaneBrokerSnapshotRejected(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._prev = os.environ.get("AL_SANGMOO_DB_PATH")
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        os.environ["AL_SANGMOO_DB_PATH"] = self._tmp.name

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("AL_SANGMOO_DB_PATH", None)
        else:
            os.environ["AL_SANGMOO_DB_PATH"] = self._prev
        for suffix in ("", "-wal", "-shm"):
            path = self._tmp.name + suffix
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def test_372k_snapshot_uses_lot_nav_not_local_7500(self):
        from al_sangmoo.infrastructure.persistence import (
            add_portfolio_buy,
            get_live_portfolio,
            load_account_snapshot,
            save_account_snapshot,
        )
        add_portfolio_buy("CVX", 150.0, 10.0)
        add_portfolio_buy("AMD", 160.0, 20.0)
        save_account_snapshot({
            "total_equity_usd": 372574.41,
            "cash_available_usd": 0.0,
            "stock_eval_usd": 372574.41,
            "mode": "VIRTUAL_PAPER",
            "source": "KIS",
        })
        live = get_live_portfolio()
        self.assertEqual(live.get("equity_source"), "HOLDINGS")
        self.assertAlmostEqual(float(live["total_eval"]), 4700.0, places=2)
        self.assertAlmostEqual(float(live["total_equity_usd"]), 4700.0, places=2)
        self.assertAlmostEqual(float(live["free_cash_usd"]), 0.0, places=2)
        weight = float(live["total_eval"]) / float(live["total_equity_usd"]) * 100.0
        self.assertGreater(weight, 90.0)
        snap = load_account_snapshot()
        self.assertTrue(bool(snap))
        self.assertLess(float(snap["total_equity_usd"]), 20000.0)
        self.assertAlmostEqual(float(snap["total_equity_usd"]), 4700.0, places=2)

    def test_lots_without_snapshot_use_local_ledger(self):
        from al_sangmoo.infrastructure.persistence import add_portfolio_buy, get_live_portfolio
        add_portfolio_buy("CVX", 150.0, 10.0)
        live = get_live_portfolio()
        self.assertEqual(live.get("equity_source"), "LOCAL")
        self.assertAlmostEqual(float(live["total_equity_usd"]), DEFAULT_BASE_ACCOUNT_USD, places=2)
        self.assertAlmostEqual(
            float(live["free_cash_usd"]),
            DEFAULT_BASE_ACCOUNT_USD - 1500.0,
            places=2,
        )
        self.assertAlmostEqual(float(live["overall_pnl_pct"]), 0.0, places=2)

    def test_persist_writes_reconciled_lot_nav_not_372k(self):
        from al_sangmoo.domain.reconciliation import _persist_broker_account_snapshot
        from al_sangmoo.infrastructure.persistence import load_account_snapshot
        _persist_broker_account_snapshot({
            "mode": "VIRTUAL_PAPER",
            "total_equity_usd": 372574.41,
            "cash_available_usd": 0.0,
            "stock_eval_usd": 372574.41,
            "holdings": [
                {"ticker": "CVX", "quantity": 10, "current_price": 160.0},
                {"ticker": "AMD", "quantity": 20, "current_price": 160.0},
            ],
        })
        snap = load_account_snapshot()
        self.assertTrue(bool(snap))
        self.assertEqual(snap.get("source"), "HOLDINGS")
        self.assertAlmostEqual(float(snap["total_equity_usd"]), 4800.0, places=2)
        self.assertLess(float(snap["total_equity_usd"]), 20000.0)


if __name__ == "__main__":
    unittest.main()
