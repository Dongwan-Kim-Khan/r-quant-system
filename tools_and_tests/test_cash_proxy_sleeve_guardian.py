"""
Cash Proxy sleeve + Guardian satellite-exit separation tests.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

TEST_DB_DIR = tempfile.mkdtemp(suffix="_proxy_sleeve")
os.environ["AL_SANGMOO_DB_PATH"] = os.path.join(TEST_DB_DIR, "test.db")

import db_manager
from al_sangmoo.domain.risk.cash_proxy import build_cash_proxy_plan, is_proxy_ticker
from al_sangmoo.domain.risk.portfolio_guardian import PortfolioGuardian
from al_sangmoo.domain.risk.autopilot_trader import AutoPilotTrader


class TestCashProxyDeleverPlan(unittest.TestCase):
    def test_leverage_off_plans_qld_sell_then_qqq_park(self):
        holdings = [
            {"ticker": "NVDA", "quantity": 5, "current_price": 100, "buy_price": 100},
            {"ticker": "QLD", "quantity": 8, "current_price": 90, "buy_price": 90},
        ]
        # equity 5000; sat=500; idle=4500 → QQQ target shares at $450
        plan = build_cash_proxy_plan(
            total_equity_usd=5000.0,
            holdings=holdings,
            cash_usd=0.0,
            leverage_mode=False,
            qqq_price=450.0,
            qld_price=90.0,
        )
        reasons = [a["reason"] for a in plan["actions"]]
        self.assertIn("DELEVERAGE_TO_1X_QQQ_CORE", reasons)
        sell_qld = next(a for a in plan["actions"] if a["ticker"] == "QLD")
        self.assertEqual(sell_qld["side"], "SELL")
        self.assertEqual(sell_qld["qty"], 8)

    def test_leverage_on_keeps_qld_mix(self):
        holdings = [{"ticker": "NVDA", "quantity": 5, "current_price": 100, "buy_price": 100}]
        plan = build_cash_proxy_plan(
            total_equity_usd=5000.0,
            holdings=holdings,
            cash_usd=4500.0,
            leverage_mode=True,
            qqq_price=450.0,
            qld_price=90.0,
        )
        self.assertTrue(plan["leverage_mode"])
        self.assertAlmostEqual(plan["qqq_weight"], 0.5)
        self.assertAlmostEqual(plan["qld_weight"], 0.5)


class TestGuardianSkipsProxyExits(unittest.TestCase):
    def setUp(self):
        db_manager.init_database()
        self.g = PortfolioGuardian(check_interval_seconds=10)
        self.g.is_enabled = True
        self.g.last_sleeve_align_time = 10**12  # block non-delever sleeve thrash in this unit

    def test_proxy_holdings_are_not_stop_exited(self):
        holdings = [
            {
                "id": 1,
                "ticker": "QLD",
                "buy_price": 100.0,
                "current_price": 90.0,
                "quantity": 8,
                "buy_date": "2026-09-09",
                "peak_high": 100.0,
                "max_gain_pct": 0.0,
            },
            {
                "id": 2,
                "ticker": "QQQ",
                "buy_price": 700.0,
                "current_price": 680.0,
                "quantity": 1,
                "buy_date": "2026-09-09",
                "peak_high": 700.0,
                "max_gain_pct": 0.0,
            },
        ]
        with patch(
            "al_sangmoo.domain.risk.portfolio_guardian.get_live_portfolio",
            return_value={"holdings": holdings, "total_equity_usd": 7500.0, "cash_usd": 0.0},
        ), patch.object(self.g, "_fetch_live_price", side_effect=lambda t, fb: fb), \
             patch.object(self.g, "_align_cash_proxy_sleeve", return_value=[]), \
             patch(
                 "al_sangmoo.domain.risk.portfolio_guardian.fetch_trailing_snapshot",
                 return_value={"kijun_26": 9999.0, "atr_14": 1.0, "peak_from_hist": 0, "last_high": 0},
             ), \
             patch("al_sangmoo.domain.risk.portfolio_guardian.record_portfolio_sell") as sell_mock:
            res = self.g._sync_check_and_execute_guardian_rules()
        self.assertEqual(res["checked_count"], 0)
        self.assertEqual(res["actions_count"], 0)
        sell_mock.assert_not_called()

    def test_satellite_still_exits_on_hard_stop(self):
        holdings = [
            {
                "id": 9,
                "ticker": "NVDA",
                "buy_price": 100.0,
                "current_price": 90.0,
                "quantity": 3,
                "buy_date": "2026-09-09",
                "peak_high": 100.0,
                "max_gain_pct": 0.0,
            },
            {
                "id": 1,
                "ticker": "QLD",
                "buy_price": 100.0,
                "current_price": 90.0,
                "quantity": 8,
                "buy_date": "2026-09-09",
                "peak_high": 100.0,
                "max_gain_pct": 0.0,
            },
        ]
        with patch(
            "al_sangmoo.domain.risk.portfolio_guardian.get_live_portfolio",
            return_value={"holdings": holdings, "total_equity_usd": 7500.0, "cash_usd": 0.0},
        ), patch.object(self.g, "_fetch_live_price", side_effect=lambda t, fb: 90.0 if t == "NVDA" else fb), \
             patch.object(self.g, "_align_cash_proxy_sleeve", return_value=[]), \
             patch.object(self.g, "_repark_exit_proceeds", return_value=[]), \
             patch(
                 "al_sangmoo.domain.risk.portfolio_guardian.fetch_trailing_snapshot",
                 return_value={"kijun_26": 80.0, "atr_14": 1.0, "peak_from_hist": 0, "last_high": 0},
             ), \
             patch(
                 "al_sangmoo.domain.risk.portfolio_guardian.default_kis_broker.is_configured",
                 return_value=False,
             ), \
             patch("al_sangmoo.domain.risk.portfolio_guardian.record_portfolio_sell") as sell_mock:
            res = self.g._sync_check_and_execute_guardian_rules()
        self.assertEqual(res["checked_count"], 1)
        self.assertEqual(res["actions_count"], 1)
        self.assertEqual(res["actions"][0]["ticker"], "NVDA")
        self.assertEqual(res["actions"][0]["action"], "AUTO_STOP_LOSS")
        sell_mock.assert_called_once()


class TestAutopilotExecutesDeleverSell(unittest.TestCase):
    def setUp(self):
        db_manager.init_database()
        self.ap = AutoPilotTrader(check_interval_seconds=60)
        self.ap.is_enabled = True

    def test_ensure_cash_proxy_executes_qld_sell_when_1x(self):
        holdings = [
            {"id": 11, "ticker": "QLD", "quantity": 8, "current_price": 90.0, "buy_price": 90.0},
            {"id": 12, "ticker": "DELL", "quantity": 2, "current_price": 100.0, "buy_price": 100.0},
        ]
        with patch(
            "al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio",
            return_value={"holdings": holdings, "total_equity_usd": 5000.0, "cash_usd": 500.0},
        ), patch.object(self.ap, "_leverage_mode_now", return_value=False), \
             patch.object(self.ap, "_etf_price", side_effect=lambda t: 450.0 if t == "QQQ" else 90.0), \
             patch(
                 "al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured",
                 return_value=False,
             ), \
             patch("al_sangmoo.domain.risk.autopilot_trader.record_portfolio_sell") as sell_mock, \
             patch("al_sangmoo.domain.risk.autopilot_trader.add_portfolio_buy") as buy_mock:
            plan = self.ap._ensure_cash_proxy_parked()
        executed_sides = {(e["ticker"], e["side"]) for e in plan.get("executed") or []}
        self.assertIn(("QLD", "SELL"), executed_sides)
        sell_mock.assert_called()
        # QQQ park may also buy after delever
        self.assertTrue(any(e.get("side") == "SELL" for e in plan["executed"]))


if __name__ == "__main__":
    unittest.main()
