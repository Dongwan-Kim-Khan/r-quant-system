"""
Al-Sangmoo v2 uncapped trailing stop SSOT tests.
floor = max(26-day kijun, peak_high - 2.5 * ATR(14))
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.domain.risk.trailing_stop import (
    compute_trailing_floor,
    evaluate_guardian_exit,
    format_holding_advice,
    latch_peak_gain,
    snapshot_from_indicator_df,
)
from al_sangmoo.domain.quant.ichimoku import calculate_ichimoku_indicators
import pandas as pd
import numpy as np


class TestTrailingFloorMath(unittest.TestCase):
    def test_floor_takes_max_of_kijun_and_atr_channel(self):
        # kijun=100, peak=120, ATR=4 → atr_leg=120-10=110 → max(100, 110)=110
        self.assertEqual(compute_trailing_floor(100.0, 120.0, 4.0), 110.0)

    def test_floor_uses_kijun_when_atr_channel_is_tighter(self):
        # kijun=115, peak=120, ATR=10 → atr_leg=95 → max(115, 95)=115
        self.assertEqual(compute_trailing_floor(115.0, 120.0, 10.0), 115.0)

    def test_floor_zero_when_inputs_missing(self):
        self.assertEqual(compute_trailing_floor(0.0, 0.0, 0.0), 0.0)
        self.assertEqual(compute_trailing_floor(0.0, 120.0, 0.0), 0.0)


class TestLatchAndEvaluate(unittest.TestCase):
    def test_hard_stop_at_minus_five(self):
        res = evaluate_guardian_exit(buy_price=100.0, current_price=95.0)
        self.assertEqual(res["action"], "AUTO_STOP_LOSS")
        self.assertEqual(res["hard_stop_price"], 95.0)
        self.assertTrue(res["is_full_exit"])

    def test_hard_stop_beats_kijun(self):
        res = evaluate_guardian_exit(
            buy_price=100.0, current_price=95.0, kijun_26=90.0, atr_14=2.0
        )
        self.assertEqual(res["action"], "AUTO_STOP_LOSS")

    def test_kijun_breakdown_before_trailing_arm(self):
        res = evaluate_guardian_exit(
            buy_price=100.0, current_price=102.0, kijun_26=103.0, atr_14=2.0
        )
        self.assertEqual(res["action"], "AUTO_KIJUN_EXIT")
        self.assertFalse(res["trailing_active"])

    def test_plus_fifteen_does_not_force_exit(self):
        res = evaluate_guardian_exit(
            buy_price=100.0, current_price=116.0, kijun_26=105.0, atr_14=2.0, peak_high=116.0
        )
        self.assertTrue(res["trailing_active"])
        self.assertEqual(res["action"], None)
        self.assertEqual(res["trailing_floor"], 111.0)  # max(105, 116-5)=111

    def test_trailing_exit_when_price_breaks_floor(self):
        res = evaluate_guardian_exit(
            buy_price=100.0,
            current_price=110.5,
            kijun_26=105.0,
            atr_14=2.0,
            peak_high=120.0,
            max_gain_pct=20.0,
        )
        self.assertTrue(res["trailing_active"])
        self.assertEqual(res["trailing_floor"], 115.0)  # max(105, 120-5)=115
        self.assertEqual(res["action"], "AUTO_TRAILING_TP")

    def test_trailing_stays_armed_after_pullback(self):
        latched = latch_peak_gain(100.0, 112.0, peak_high=118.0, max_gain_pct=18.0)
        self.assertTrue(latched["trailing_active"])
        self.assertGreaterEqual(latched["peak_high"], 118.0)
        res = evaluate_guardian_exit(
            buy_price=100.0, current_price=112.0, kijun_26=100.0, atr_14=2.0,
            peak_high=118.0, max_gain_pct=18.0
        )
        self.assertTrue(res["trailing_active"])
        self.assertEqual(res["trailing_floor"], 113.0)  # max(100, 118-5)
        self.assertEqual(res["action"], "AUTO_TRAILING_TP")

    def test_no_partial_language_in_advice(self):
        hold = evaluate_guardian_exit(buy_price=100.0, current_price=105.0, kijun_26=98.0)
        text = format_holding_advice(hold, 100.0)
        self.assertNotIn("50%", text)
        self.assertNotIn("분할", text)
        trail = evaluate_guardian_exit(
            buy_price=100.0, current_price=116.0, kijun_26=105.0, atr_14=2.0, peak_high=116.0
        )
        text2 = format_holding_advice(trail, 100.0)
        self.assertIn("트레일링", text2)
        self.assertNotIn("분할", text2)


class TestSnapshotFromDf(unittest.TestCase):
    def test_snapshot_reads_kijun_and_atr(self):
        n = 80
        idx = pd.date_range("2026-01-01", periods=n, freq="B")
        close = np.linspace(100, 140, n)
        df = pd.DataFrame({
            "Open": close,
            "High": close + 2,
            "Low": close - 2,
            "Close": close,
            "Volume": np.full(n, 1_000_000),
        }, index=idx)
        ind = calculate_ichimoku_indicators(df)
        snap = snapshot_from_indicator_df(ind, buy_date="2026-03-01")
        self.assertGreater(snap["kijun_26"], 0)
        self.assertGreater(snap["atr_14"], 0)
        self.assertGreater(snap["peak_from_hist"], 0)


class TestGuardianUsesSSot(unittest.TestCase):
    def test_guardian_status_has_no_partial_ratio(self):
        from al_sangmoo.domain.risk.portfolio_guardian import PortfolioGuardian
        g = PortfolioGuardian(check_interval_seconds=10)
        rules = g.get_status()["rules"]
        self.assertEqual(rules["partial_tp_ratio"], 0.0)
        self.assertTrue(rules["uncapped_trailing"])
        self.assertEqual(g.interval, 10)
        self.assertEqual(rules["atr_multiplier"], 2.5)

    def test_guardian_full_exit_on_trailing_without_partial_branch(self):
        from al_sangmoo.domain.risk.portfolio_guardian import PortfolioGuardian

        holding = {
            "id": 1,
            "ticker": "NVDA",
            "buy_price": 100.0,
            "quantity": 10,
            "current_price": 110.5,
            "buy_date": "2026-01-01",
            "peak_high": 120.0,
            "max_gain_pct": 20.0,
            "kijun_26": 105.0,
            "atr_14": 2.0,
        }
        g = PortfolioGuardian()
        g.is_enabled = True

        with patch("al_sangmoo.domain.risk.portfolio_guardian.db_manager") as db, \
             patch("al_sangmoo.domain.risk.portfolio_guardian.default_kis_broker") as broker, \
             patch("al_sangmoo.domain.risk.portfolio_guardian.fetch_trailing_snapshot") as snap:
            db.get_live_portfolio.return_value = {"holdings": [holding]}
            db.record_portfolio_sell.return_value = True
            db.get_connection.return_value.__enter__.return_value.cursor.return_value = MagicMock()
            broker.is_configured.return_value = False
            snap.return_value = {"kijun_26": 105.0, "atr_14": 2.0, "peak_from_hist": 120.0, "last_high": 111.0}
            g._fetch_live_price = MagicMock(return_value=110.5)

            res = g._sync_check_and_execute_guardian_rules()
            self.assertEqual(res["actions_count"], 1)
            self.assertEqual(res["actions"][0]["action"], "AUTO_TRAILING_TP")
            self.assertTrue(res["actions"][0]["is_full_exit"])
            self.assertEqual(res["actions"][0]["remaining_quantity"], 0.0)
            db.record_portfolio_sell.assert_called_once()
            self.assertIn("AUTO_TRAILING_TP", db.record_portfolio_sell.call_args.kwargs["reason"])


if __name__ == "__main__":
    unittest.main()
