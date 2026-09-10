"""
MSI 2.0 polarity SSOT: MSI is a RISK index (high = danger).
Slot regime follows SPY >= 200 SMA, never `msi < 65`.
"""
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.domain.quant.macro import (
    classify_msi_stance,
    extract_msi_score,
    is_defense_or_worse,
    is_new_buy_blocked,
    resolve_capital_regime,
    evaluate_macro_stance,
)
from al_sangmoo.domain.risk.order_guardrail import validate_pre_trade_guardrail
from al_sangmoo.domain.risk.position_sizer import calculate_dynamic_position_size
from al_sangmoo.domain.risk.macro_guardrail import evaluate_macro_circuit_breaker


class TestMsiPolarityBands(unittest.TestCase):
    def test_bands_high_is_cash_low_is_buy(self):
        self.assertEqual(classify_msi_stance(0.0), "ACTIVE_BUY")
        self.assertEqual(classify_msi_stance(29.9), "ACTIVE_BUY")
        self.assertEqual(classify_msi_stance(30.0), "SELECTIVE_BUY")
        self.assertEqual(classify_msi_stance(49.9), "SELECTIVE_BUY")
        self.assertEqual(classify_msi_stance(50.0), "DEFENSE_HOLD")
        self.assertEqual(classify_msi_stance(74.9), "DEFENSE_HOLD")
        self.assertEqual(classify_msi_stance(75.0), "CASH_EXIT")
        self.assertEqual(classify_msi_stance(100.0), "CASH_EXIT")

    def test_evaluate_macro_stance_matches_classifier(self):
        green = evaluate_macro_stance(
            gauges={"us10y": {"val": 3.5}, "vix": {"val": 12.0}, "wti": {"val": 60.0}, "dxy": {"val": 95.0}},
            defense_count=0, buy_count=20, matched_shocks=[],
        )
        self.assertTrue(green["msi_is_risk_index"])
        self.assertEqual(green["macro_stance"], classify_msi_stance(green["msi_score"]))
        self.assertEqual(green["macro_stance"], "ACTIVE_BUY")

    def test_not_the_inverted_project_md_bands(self):
        # Old PROJECT.md wrongly said CASH_EXIT < 30 and ACTIVE_BUY >= 70
        self.assertNotEqual(classify_msi_stance(20.0), "CASH_EXIT")
        self.assertNotEqual(classify_msi_stance(80.0), "ACTIVE_BUY")


class TestExtractMsiScore(unittest.TestCase):
    def test_nested_climate_key(self):
        payload = {"macro_climate": {"msi_score": 72.8}}
        self.assertAlmostEqual(extract_msi_score(payload), 72.8)

    def test_top_level_wins(self):
        payload = {"msi_score": 40.0, "macro_climate": {"msi_score": 72.8}}
        self.assertAlmostEqual(extract_msi_score(payload), 40.0)

    def test_legacy_msi_key(self):
        self.assertAlmostEqual(extract_msi_score({"msi": 33.0}), 33.0)

    def test_missing_defaults_50_not_bullish_65(self):
        self.assertAlmostEqual(extract_msi_score({}), 50.0)
        self.assertTrue(is_defense_or_worse(extract_msi_score({})))


class TestCapitalRegimeNotMsi65(unittest.TestCase):
    def test_spy_above_200_is_bull_even_if_msi_high(self):
        cap = resolve_capital_regime(msi_score=80.0, spy_close=500.0, spy_sma200=480.0)
        self.assertTrue(cap["is_bull_regime"])
        self.assertEqual(cap["max_slots"], 3)
        self.assertEqual(cap["msi_stance"], "CASH_EXIT")

    def test_spy_below_200_is_bear_even_if_msi_low(self):
        cap = resolve_capital_regime(msi_score=20.0, spy_close=400.0, spy_sma200=420.0)
        self.assertFalse(cap["is_bull_regime"])
        self.assertEqual(cap["max_slots"], 2)

    def test_fallback_defense_hold_is_not_bull(self):
        """Legacy bug: msi 64 < 65 → bull. Correct: 64 is DEFENSE_HOLD → 2-slot."""
        cap = resolve_capital_regime(msi_score=64.0, fetch_spy=False)
        self.assertFalse(cap["is_bull_regime"])
        self.assertEqual(cap["max_slots"], 2)
        self.assertEqual(cap["regime_source"], "msi_fallback_lt_50")

    def test_fallback_active_buy_is_bull(self):
        cap = resolve_capital_regime(msi_score=20.0, fetch_spy=False)
        self.assertTrue(cap["is_bull_regime"])
        self.assertEqual(cap["max_slots"], 3)


class TestDownstreamConsumers(unittest.TestCase):
    def test_order_guardrail_blocks_high_msi_not_low(self):
        blocked = validate_pre_trade_guardrail("NVDA", 100.0, 10.0, 10000.0, [], msi_score=80.0)
        self.assertFalse(blocked["allowed"])
        allowed = validate_pre_trade_guardrail("NVDA", 100.0, 10.0, 10000.0, [], msi_score=20.0)
        self.assertTrue(allowed["allowed"])

    def test_position_sizer_zero_on_cash_exit(self):
        red = calculate_dynamic_position_size(100000.0, 100.0, 3.0, msi_score=85.0)
        green = calculate_dynamic_position_size(100000.0, 100.0, 3.0, msi_score=20.0)
        self.assertEqual(red["macro_multiplier"], 0.0)
        self.assertEqual(green["macro_multiplier"], 1.0)
        self.assertGreater(green["allocated_cash"], red["allocated_cash"])

    def test_circuit_breaker_high_msi_liquidates(self):
        holdings = [{"id": 1, "ticker": "NVDA", "current_price": 100.0, "buy_price": 90.0, "stop_loss_price": 86.4}]
        crisis = evaluate_macro_circuit_breaker(82.0, holdings)
        self.assertEqual(crisis["macro_stance"], "CASH_EXIT")
        self.assertTrue(crisis["circuit_breaker_active"])
        calm = evaluate_macro_circuit_breaker(20.0, holdings)
        self.assertEqual(calm["macro_stance"], "ACTIVE_BUY")
        self.assertFalse(calm["circuit_breaker_active"])

    def test_is_new_buy_blocked(self):
        self.assertTrue(is_new_buy_blocked(75.0))
        self.assertFalse(is_new_buy_blocked(74.9))


if __name__ == "__main__":
    unittest.main()
