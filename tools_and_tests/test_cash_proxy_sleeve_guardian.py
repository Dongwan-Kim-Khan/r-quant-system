"""
AL-SANGMOO QUANT PLATFORM: C1-M2 CRITICAL PATCH TEST SUITE

Regression coverage for the 2026-09-11 trading-engine disaster:
  1. Autopilot Entry Gate       - never buy below the 26D kijun, low conviction,
                                   or a name inside the re-entry cooldown.
  2. Cash Proxy anti-whipsaw    - deadband, 300s sell cooldown, execution order,
                                   single-authority ownership.
  3. Guardian proxy exemption   - QLD/QQQ are marked-to-market but never
                                   force-exited; forced exits arm the cooldown.
"""

import os
import sys
import json
import asyncio
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest.mock import patch

from al_sangmoo.domain.risk import cash_proxy, exit_cooldown
from al_sangmoo.domain.risk import autopilot_trader
from al_sangmoo.domain.risk.autopilot_trader import (
    AutoPilotTrader,
    evaluate_entry_gate,
)
from al_sangmoo.domain.risk.portfolio_guardian import PortfolioGuardian
from al_sangmoo.infrastructure.brokers.kis_broker import KISBrokerAdapter


# --------------------------------------------------------------------------- #
# 작업 1: Autopilot Entry Gate
# --------------------------------------------------------------------------- #
class TestEntryGate(unittest.TestCase):
    def setUp(self):
        exit_cooldown.clear()

    def test_below_kijun_is_skipped(self):
        """The DIS disaster: price 104.95 < kijun 105.78 must be BLOCKED."""
        gate = evaluate_entry_gate(
            {"ticker": "DIS", "price": 104.95, "conviction_score": 85.0,
             "sizing": {"eligible": True}},
            kijun_26=105.78,
        )
        self.assertFalse(gate["eligible"])
        self.assertEqual(gate["reason"], "BELOW_KIJUN")

    def test_above_kijun_passes(self):
        gate = evaluate_entry_gate(
            {"ticker": "NVDA", "price": 120.0, "conviction_score": 82.0,
             "sizing": {"eligible": True}},
            kijun_26=110.0,
        )
        self.assertTrue(gate["eligible"])
        self.assertEqual(gate["reason"], "PASS")

    def test_low_conviction_is_skipped(self):
        gate = evaluate_entry_gate(
            {"ticker": "XYZ", "price": 50.0, "conviction_score": 40.0,
             "bull_score": 45.0, "sizing": {"eligible": False}},
            kijun_26=45.0,
        )
        self.assertFalse(gate["eligible"])
        self.assertEqual(gate["reason"], "LOW_CONVICTION")

    def test_eligible_sizing_flag_satisfies_conviction(self):
        gate = evaluate_entry_gate(
            {"ticker": "ABC", "price": 50.0, "conviction_score": 10.0,
             "bull_score": 10.0, "sizing": {"eligible": True}},
            kijun_26=45.0,
        )
        self.assertTrue(gate["eligible"])

    def test_reentry_cooldown_is_skipped(self):
        gate = evaluate_entry_gate(
            {"ticker": "DELL", "price": 520.0, "conviction_score": 90.0,
             "sizing": {"eligible": True}},
            kijun_26=500.0,
            cooldown_tickers=["DELL"],
        )
        self.assertFalse(gate["eligible"])
        self.assertEqual(gate["reason"], "RE_ENTRY_COOLDOWN")

    def test_unknown_kijun_does_not_hard_block(self):
        """A transient market-data failure (kijun<=0) must not block every entry."""
        gate = evaluate_entry_gate(
            {"ticker": "NVDA", "price": 120.0, "conviction_score": 82.0,
             "sizing": {"eligible": True}},
            kijun_26=0.0,
        )
        self.assertTrue(gate["eligible"])


class TestNextUnheldCandidate(unittest.TestCase):
    def setUp(self):
        exit_cooldown.clear()
        self.pilot = AutoPilotTrader()

    def test_skips_held_and_below_kijun_returns_first_eligible(self):
        feed = {
            "ranked_conviction_list": [
                {"ticker": "AAPL", "price": 200.0, "kijun": 190.0,
                 "conviction_score": 88.0, "sizing": {"eligible": True, "shares": 3}},
                {"ticker": "DIS", "price": 104.95, "kijun": 105.78,
                 "conviction_score": 80.0, "sizing": {"eligible": True, "shares": 5}},
                {"ticker": "MSFT", "price": 400.0, "kijun": 390.0,
                 "conviction_score": 91.0, "sizing": {"eligible": True, "shares": 2}},
            ]
        }
        # AAPL already held -> should skip to next eligible (DIS is below kijun) -> MSFT.
        cand, gate = self.pilot._next_unheld_candidate(
            feed, active_tickers=["AAPL"], broker_tickers=[], cooldown_tickers=[]
        )
        self.assertIsNotNone(cand)
        self.assertEqual(cand["ticker"], "MSFT")
        self.assertTrue(gate["eligible"])

    def test_returns_none_when_all_ineligible(self):
        feed = {
            "ranked_conviction_list": [
                {"ticker": "DIS", "price": 104.95, "kijun": 105.78,
                 "conviction_score": 80.0, "sizing": {"eligible": True, "shares": 5}},
            ]
        }
        cand, gate = self.pilot._next_unheld_candidate(
            feed, active_tickers=[], broker_tickers=[], cooldown_tickers=[]
        )
        self.assertIsNone(cand)
        self.assertEqual(gate["reason"], "NO_ELIGIBLE_CANDIDATE")


# --------------------------------------------------------------------------- #
# 재진입 쿨다운 레지스트리
# --------------------------------------------------------------------------- #
class TestExitCooldown(unittest.TestCase):
    def setUp(self):
        exit_cooldown.clear()

    def test_record_and_active_within_window(self):
        exit_cooldown.record_exit("DIS", "AUTO_KIJUN_EXIT", ts=1000.0)
        self.assertTrue(exit_cooldown.is_in_cooldown("DIS", now=1000.0 + 60.0))
        self.assertIn("DIS", exit_cooldown.cooldown_tickers(now=1000.0 + 60.0))

    def test_expires_after_window(self):
        exit_cooldown.record_exit("DIS", "AUTO_STOP_LOSS", ts=1000.0)
        later = 1000.0 + exit_cooldown.RE_ENTRY_COOLDOWN_SEC + 1.0
        self.assertFalse(exit_cooldown.is_in_cooldown("DIS", now=later))
        self.assertNotIn("DIS", exit_cooldown.cooldown_tickers(now=later))


# --------------------------------------------------------------------------- #
# 작업 2: Cash Proxy anti-whipsaw SSOT
# --------------------------------------------------------------------------- #
class TestProxyRebalanceDeadband(unittest.TestCase):
    def test_small_share_drift_is_ignored(self):
        # 2 shares drift (< 3) -> no order.
        self.assertIsNone(
            cash_proxy.proxy_rebalance_action(current_qty=10, target_qty=12, price=90.0)
        )

    def test_small_notional_is_ignored(self):
        # 4 shares * $50 = $200 (< $300) -> no order.
        self.assertIsNone(
            cash_proxy.proxy_rebalance_action(current_qty=10, target_qty=14, price=50.0)
        )

    def test_material_move_fires_order(self):
        action = cash_proxy.proxy_rebalance_action(
            current_qty=0, target_qty=12, price=90.0
        )
        self.assertIsNotNone(action)
        self.assertEqual(action["side"], "BUY")
        self.assertEqual(action["qty"], 12)

    def test_sell_side_detected(self):
        action = cash_proxy.proxy_rebalance_action(
            current_qty=20, target_qty=5, price=90.0
        )
        self.assertIsNotNone(action)
        self.assertEqual(action["side"], "SELL")
        self.assertEqual(action["qty"], 15)


class TestProxySellCooldown(unittest.TestCase):
    def setUp(self):
        self.sleeve = cash_proxy.CashProxySleeve()

    def test_freshly_bought_proxy_cannot_be_dumped_in_20s(self):
        """The exact whipsaw: bought QLD, then tried to sell 20s later."""
        self.sleeve.record_buy("QLD", ts=1000.0)
        decision = self.sleeve.free_proxy_cash_for_entry(
            "QLD", needed_usd=1500.0, current_qty=19, price=90.0,
            satellite_search_done=True, now=1020.0,  # +20s
        )
        self.assertEqual(decision["action"], "HOLD")
        self.assertEqual(decision["reason"], "PROXY_SELL_COOLDOWN")

    def test_proxy_can_be_sold_after_cooldown(self):
        self.sleeve.record_buy("QLD", ts=1000.0)
        decision = self.sleeve.free_proxy_cash_for_entry(
            "QLD", needed_usd=1500.0, current_qty=19, price=90.0,
            satellite_search_done=True, now=1000.0 + 301.0,  # +5m1s
        )
        self.assertEqual(decision["action"], "SELL")
        self.assertGreater(decision["qty"], 0)

    def test_satellite_search_must_finish_first(self):
        self.sleeve.record_buy("QLD", ts=1000.0)
        decision = self.sleeve.free_proxy_cash_for_entry(
            "QLD", needed_usd=1500.0, current_qty=19, price=90.0,
            satellite_search_done=False, now=1000.0 + 400.0,
        )
        self.assertEqual(decision["action"], "HOLD")
        self.assertEqual(decision["reason"], "SATELLITE_SEARCH_PENDING")


class TestProxyExecutionOrderAndAuthority(unittest.TestCase):
    def setUp(self):
        self.sleeve = cash_proxy.CashProxySleeve()

    def test_should_park_cash_requires_search_done(self):
        self.assertFalse(cash_proxy.should_park_cash(False, 5000.0, 90.0))
        self.assertTrue(cash_proxy.should_park_cash(True, 5000.0, 90.0))

    def test_should_park_cash_ignores_tiny_cash(self):
        self.assertFalse(cash_proxy.should_park_cash(True, 100.0, 90.0))

    def test_single_authority_blocks_second_owner(self):
        self.assertTrue(self.sleeve.acquire_authority("AUTOPILOT"))
        # Guardian cannot grab it concurrently.
        self.assertFalse(self.sleeve.acquire_authority("GUARDIAN"))
        self.sleeve.release_authority("AUTOPILOT")
        self.assertTrue(self.sleeve.acquire_authority("GUARDIAN"))


# --------------------------------------------------------------------------- #
# 작업 2/3: Guardian proxy exemption + cooldown arming
# --------------------------------------------------------------------------- #
class TestGuardianProxyExemption(unittest.TestCase):
    def setUp(self):
        exit_cooldown.clear()
        self.guardian = PortfolioGuardian()
        self.guardian.is_enabled = True

    def _run_guardian(self, holdings):
        price_map = {"QLD": 80.0, "DIS": 100.0}  # both deeply underwater vs buy

        with patch("al_sangmoo.domain.risk.portfolio_guardian.get_live_portfolio",
                   return_value={"holdings": holdings}), \
             patch("al_sangmoo.domain.risk.portfolio_guardian.fetch_trailing_snapshot",
                   return_value={}), \
             patch("al_sangmoo.domain.risk.portfolio_guardian.record_portfolio_sell") as mock_sell, \
             patch.object(PortfolioGuardian, "_persist_mark") as mock_mark, \
             patch.object(PortfolioGuardian, "_fetch_live_price",
                          new=lambda _self, ticker, fallback: price_map.get(str(ticker).upper(), fallback)), \
             patch("al_sangmoo.domain.risk.portfolio_guardian.default_kis_broker") as mock_broker:
            mock_broker.is_configured.return_value = False
            res = self.guardian._sync_check_and_execute_guardian_rules()
        return res, mock_sell, mock_mark

    def test_cash_proxy_marked_but_never_exited(self):
        holdings = [{"id": 1, "ticker": "QLD", "buy_price": 100.0, "quantity": 19.0,
                     "current_price": 100.0}]
        res, mock_sell, mock_mark = self._run_guardian(holdings)
        # Marked to market...
        self.assertTrue(mock_mark.called)
        # ...but NEVER force-sold.
        self.assertFalse(mock_sell.called)
        self.assertEqual(res["actions"], [])

    def test_normal_stock_is_exited_and_arms_cooldown(self):
        holdings = [{"id": 2, "ticker": "DIS", "buy_price": 200.0, "quantity": 5.0,
                     "current_price": 200.0}]
        res, mock_sell, _ = self._run_guardian(holdings)
        self.assertTrue(mock_sell.called)
        self.assertEqual(len(res["actions"]), 1)
        self.assertEqual(res["actions"][0]["ticker"], "DIS")
        # Cooldown armed so Autopilot cannot instantly re-buy DIS.
        self.assertTrue(exit_cooldown.is_in_cooldown("DIS"))


# --------------------------------------------------------------------------- #
# 작업 1: end-to-end run_autopilot_cycle Entry Gate integration
# --------------------------------------------------------------------------- #
class TestAutopilotCycleEntryGate(unittest.TestCase):
    def setUp(self):
        exit_cooldown.clear()
        self.pilot = AutoPilotTrader()
        self.pilot.is_enabled = False  # stop right after selection (no real order)
        self._tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix="_feed.json", delete=False, encoding="utf-8"
        )
        self._tmp.close()

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    def _write_feed(self, feed):
        with open(self._tmp.name, "w", encoding="utf-8") as f:
            json.dump(feed, f)

    def _run(self, feed):
        self._write_feed(feed)
        with patch.object(autopilot_trader, "DASHBOARD_JSON", self._tmp.name), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio",
                   return_value={"holdings": []}), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker") as mock_broker:
            mock_broker.is_configured.return_value = False
            return asyncio.run(self.pilot.run_autopilot_cycle(force_scan=False))

    def test_below_kijun_top_pick_is_skipped_for_eligible_runner(self):
        """DIS is #1 but below kijun -> Autopilot must select the eligible NVDA."""
        feed = {
            "top_conviction_pick": {
                "ticker": "DIS", "name": "Disney", "price": 104.95, "kijun": 105.78,
                "conviction_score": 88.0, "sizing": {"eligible": True, "shares": 5,
                                                      "is_bull_regime": True},
            },
            "ranked_conviction_list": [
                {"ticker": "DIS", "name": "Disney", "price": 104.95, "kijun": 105.78,
                 "conviction_score": 88.0, "sizing": {"eligible": True, "shares": 5,
                                                      "is_bull_regime": True}},
                {"ticker": "NVDA", "name": "Nvidia", "price": 180.0, "kijun": 170.0,
                 "conviction_score": 91.0, "sizing": {"eligible": True, "shares": 2,
                                                      "is_bull_regime": True}},
            ],
        }
        res = self._run(feed)
        # Auto-buy disabled, but the selected candidate must be the eligible NVDA.
        self.assertEqual(res["reason"], "AUTO_BUY_DISABLED")
        self.assertEqual(res["ticker"], "NVDA")

    def test_all_below_kijun_yields_no_eligible_candidate(self):
        feed = {
            "top_conviction_pick": {
                "ticker": "DIS", "name": "Disney", "price": 104.95, "kijun": 105.78,
                "conviction_score": 88.0, "sizing": {"eligible": True, "shares": 5,
                                                      "is_bull_regime": True},
            },
            "ranked_conviction_list": [
                {"ticker": "DIS", "name": "Disney", "price": 104.95, "kijun": 105.78,
                 "conviction_score": 88.0, "sizing": {"eligible": True, "shares": 5,
                                                      "is_bull_regime": True}},
            ],
        }
        res = self._run(feed)
        self.assertEqual(res["reason"], "NO_ELIGIBLE_CANDIDATE")


# --------------------------------------------------------------------------- #
# 작업 3: reconciliation refreshes local current_price to the broker's real price
# --------------------------------------------------------------------------- #
class TestReconciliationCurrentPriceSync(unittest.TestCase):
    def setUp(self):
        self._prev_db = os.environ.get("AL_SANGMOO_DB_PATH")
        self._db_dir = tempfile.mkdtemp(suffix="_recon")
        os.environ["AL_SANGMOO_DB_PATH"] = os.path.join(self._db_dir, "recon.db")
        import db_manager
        db_manager.init_database()

    def tearDown(self):
        if self._prev_db is None:
            os.environ.pop("AL_SANGMOO_DB_PATH", None)
        else:
            os.environ["AL_SANGMOO_DB_PATH"] = self._prev_db

    def test_matched_holding_current_price_updated_to_broker_real_price(self):
        from al_sangmoo.infrastructure import persistence
        from al_sangmoo.domain import reconciliation

        # Local holding entered at $500; current price frozen at the stale entry.
        hid = persistence.add_portfolio_buy(
            ticker="DELL", buy_price=500.0, quantity=4.0, buy_date="2026-09-10"
        )

        # Broker reports the SAME qty & avg (matched), but a live price of $524.
        broker_balance = {
            "status": "success",
            "mode": "LIVE_API",
            "total_equity_usd": 2096.0,
            "cash_available_usd": 0.0,
            "holdings": [
                {"ticker": "DELL", "quantity": 4.0, "avg_price": 500.0,
                 "current_price": 524.0},
            ],
        }

        with patch.object(reconciliation.default_kis_broker, "get_overseas_balance",
                          return_value=broker_balance):
            res = reconciliation.check_sync(auto_calibrate=True)

        self.assertEqual(res["status"], "success")

        with persistence.get_connection() as conn:
            row = conn.cursor().execute(
                "SELECT current_price, current_value, pnl_amount FROM my_portfolio WHERE id = ?",
                (hid,),
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertAlmostEqual(float(row[0]), 524.0, places=2)          # current_price
        self.assertAlmostEqual(float(row[1]), 524.0 * 4.0, places=2)    # current_value
        self.assertAlmostEqual(float(row[2]), (524.0 - 500.0) * 4.0, places=2)  # pnl_amount


# --------------------------------------------------------------------------- #
# 작업 3: DELL quotes NYSE first (fixes empty-NASDAQ-query delay)
# --------------------------------------------------------------------------- #
class TestKISDellExchangeOrder(unittest.TestCase):
    def test_dell_quotes_nyse_first(self):
        adapter = KISBrokerAdapter(app_key="", app_secret="", account_no="")
        order = adapter._quote_exchanges("DELL")
        self.assertEqual(order[0], "NYS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
