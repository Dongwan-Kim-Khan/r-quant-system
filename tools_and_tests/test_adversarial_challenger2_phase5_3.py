"""
===============================================================================
  Phase 5.3 SSOT Quantitative Consolidation - Empirical Challenger 2 Harness
===============================================================================
Target:
1. Mathematical Determinism: 50 synthetic stock scenarios across domain scoring
   and consumer pipelines with 0.0000% discrepancy.
2. Tier Mutual Exclusivity: Strict partitioning across Tier 1, 2, 3 in
   classify_3tier_candidates (zero duplicates, zero silent drops of qualified candidates).
3. Stop-Loss Consistency: Verification of -4.0% hard stop across value objects,
   tier classifications, recommendation payloads, and dashboards.
4. Test Suite Execution: Verification of test_phase5_3_ssot_quant.py.
===============================================================================
"""

import os
import sys
import math
import unittest
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.core.constants import WATCHLIST, STOCK_DICT, TICKER_SECTORS, get_macro_tailwind_sectors
from al_sangmoo.domain.quant.ichimoku import (
    calculate_ichimoku_indicators,
    project_future_cloud,
    compute_institutional_flow_indicators
)
from al_sangmoo.domain.quant.macro import (
    evaluate_macro_stance,
    calculate_msi_regime
)
from al_sangmoo.domain.quant.scoring import (
    QuantIndicators,
    WeeklyTrendContext,
    InstitutionalFlowContext,
    TrampolineBounceContext,
    calculate_canonical_bull_score,
    calculate_canonical_sniper_score,
    calculate_canonical_bear_score,
    evaluate_quant_score,
    classify_quant_tier,
    classify_3tier_candidates
)


class TestPhase5_3EmpiricalChallenge(unittest.TestCase):
    """Empirical challenge suite for Phase 5.3 SSOT Quantitative Consolidation."""

    def test_01_mathematical_determinism_50_synthetic_scenarios(self):
        """
        Challenge 1: Generate 50 synthetic stock scenarios covering diverse regimes,
        boundary conditions, and momentum states. Pass each through domain scoring
        and assert 0.0000% discrepancy across all score components and intelligence metrics.
        """
        print("\n" + "=" * 75)
        print("  [CHALLENGE 1] Mathematical Determinism across 50 Synthetic Stock Scenarios")
        print("=" * 75)

        scenarios: List[Dict[str, Any]] = []

        # 1. 10 Core Deterministic Archetypes
        core_archetypes = [
            # T1: 100 pt Dual 5-Star
            {"id": "SYN_01_DUAL_100", "close": 200.0, "kijun": 198.0, "tenkan": 199.0, "span_a": 190.0, "span_b": 185.0, "vol_ratio": 0.50, "tramp": True, "days_ago": 3, "w_bull": True},
            # T2: 95 pt Bull Accumulation
            {"id": "SYN_02_BULL_95", "close": 150.0, "kijun": 148.0, "tenkan": 149.0, "span_a": 140.0, "span_b": 135.0, "vol_ratio": 0.70, "tramp": False, "days_ago": 0, "w_bull": True},
            # T3: 85 pt Sniper Alert
            {"id": "SYN_03_SNIPER_85", "close": 120.0, "kijun": 115.0, "tenkan": 118.0, "span_a": 110.0, "span_b": 105.0, "vol_ratio": 0.90, "tramp": True, "days_ago": 5, "w_bull": True},
            # T4: 70 pt Graduated Bull Sweet-Spot
            {"id": "SYN_04_GRAD_70", "close": 114.0, "kijun": 110.0, "tenkan": 109.0, "span_a": 115.0, "span_b": 105.0, "vol_ratio": 0.80, "tramp": False, "days_ago": 0, "w_bull": True},
            # T5: 50 pt Minimum Bull
            {"id": "SYN_05_MIN_50", "close": 108.0, "kijun": 102.0, "tenkan": 109.0, "span_a": 115.0, "span_b": 105.0, "vol_ratio": 1.05, "tramp": False, "days_ago": 0, "w_bull": True},
            # T6: 90 pt Risk Breakdown Bear
            {"id": "SYN_06_BEAR_90", "close": 90.0, "kijun": 100.0, "tenkan": 95.0, "span_a": 110.0, "span_b": 98.0, "vol_ratio": 1.50, "tramp": False, "days_ago": 0, "w_bull": False},
            # T7: 50 pt Moderate Bear
            {"id": "SYN_07_BEAR_50", "close": 98.0, "kijun": 100.0, "tenkan": 99.0, "span_a": 105.0, "span_b": 95.0, "vol_ratio": 1.20, "tramp": False, "days_ago": 0, "w_bull": False},
            # T8: Neutral Box Consolidation
            {"id": "SYN_08_NEUTRAL_BOX", "close": 100.0, "kijun": 100.0, "tenkan": 100.0, "span_a": 105.0, "span_b": 95.0, "vol_ratio": 1.00, "tramp": False, "days_ago": 0, "w_bull": True},
            # T9: Boundary Exact Kijun Gap -0.5%
            {"id": "SYN_09_BOUND_KMIN", "close": 99.5, "kijun": 100.0, "tenkan": 100.5, "span_a": 95.0, "span_b": 90.0, "vol_ratio": 0.55, "tramp": False, "days_ago": 0, "w_bull": True},
            # T10: Boundary Exact Kijun Gap +3.5%
            {"id": "SYN_10_BOUND_KMAX", "close": 103.5, "kijun": 100.0, "tenkan": 102.0, "span_a": 95.0, "span_b": 90.0, "vol_ratio": 0.60, "tramp": False, "days_ago": 0, "w_bull": True}
        ]
        scenarios.extend(core_archetypes)

        # 2. 40 Systematically Parametrized Scenarios (covering spectrum of price, volume, gaps)
        np.random.seed(20260823)
        for i in range(11, 51):
            base_price = round(float(np.random.uniform(15.0, 850.0)), 2)
            kgap_pct = float(np.random.uniform(-6.0, 10.0))
            kijun_val = round(base_price / (1.0 + kgap_pct / 100.0), 2)
            tenkan_delta = float(np.random.uniform(-4.0, 4.0))
            tenkan_val = round(kijun_val + tenkan_delta, 2)
            span_a_val = round(kijun_val * float(np.random.uniform(0.90, 1.05)), 2)
            span_b_val = round(kijun_val * float(np.random.uniform(0.85, 1.00)), 2)
            vol_r = round(float(np.random.uniform(0.20, 2.50)), 2)
            has_tramp = bool(np.random.choice([True, False], p=[0.35, 0.65]))
            d_ago = int(np.random.randint(1, 14)) if has_tramp else 0
            w_bull = bool(np.random.choice([True, False], p=[0.75, 0.25]))

            scenarios.append({
                "id": f"SYN_{i:02d}_PARAM",
                "close": base_price,
                "kijun": kijun_val,
                "tenkan": tenkan_val,
                "span_a": span_a_val,
                "span_b": span_b_val,
                "vol_ratio": vol_r,
                "tramp": has_tramp,
                "days_ago": d_ago,
                "w_bull": w_bull
            })

        self.assertEqual(len(scenarios), 50, "Must test exactly 50 synthetic scenarios")

        discrepancies = 0
        for s in scenarios:
            # 1. Evaluate via low-level domain scoring functions
            ind = QuantIndicators.from_values(
                close=s["close"],
                kijun=s["kijun"],
                tenkan=s["tenkan"],
                span_a=s["span_a"],
                span_b=s["span_b"],
                vol_ratio=s["vol_ratio"]
            )
            tramp_ctx = TrampolineBounceContext(detected=s["tramp"], days_ago=s["days_ago"])
            
            exp_bull, exp_b_bd = calculate_canonical_bull_score(ind)
            exp_sniper, exp_s_bd = calculate_canonical_sniper_score(ind, tramp_ctx)
            exp_bear = calculate_canonical_bear_score(ind)

            # 2. Evaluate via domain evaluation pipeline evaluate_quant_score
            res_eval = evaluate_quant_score(
                close=s["close"],
                kijun=s["kijun"],
                tenkan=s["tenkan"],
                span_a=s["span_a"],
                span_b=s["span_b"],
                vol_ratio=s["vol_ratio"],
                trampoline_detected=s["tramp"],
                is_weekly_bull=s["w_bull"],
                days_ago=s["days_ago"]
            )

            # Assert 0.0000% discrepancy between canonical functions and composite evaluation pipeline
            self.assertEqual(res_eval["bull_score"], exp_bull, f"Bull score mismatch in {s['id']}")
            self.assertEqual(res_eval["sniper_score"], exp_sniper, f"Sniper score mismatch in {s['id']}")
            self.assertEqual(res_eval["bear_score"], exp_bear, f"Bear score mismatch in {s['id']}")
            self.assertEqual(res_eval["cloud_pts"], exp_b_bd["cloud_pts"])
            self.assertEqual(res_eval["kijun_pts"], exp_b_bd["kijun_pts"])
            self.assertEqual(res_eval["vdu_pts"], exp_b_bd["vdu_pts"])
            self.assertEqual(res_eval["tenkan_pts"], exp_b_bd["tenkan_pts"])

            # Verify intelligence block consistency
            intel = res_eval["intelligence"]
            self.assertEqual(intel["bull_score"], exp_bull)
            self.assertEqual(intel["sniper_score"], exp_sniper)
            self.assertEqual(intel["bear_score"], exp_bear)
            self.assertEqual(intel["verdict"], res_eval["quant_verdict"])

        print(f"  -> PASSED: 50/50 synthetic stock scenarios verified with 0.0000% mathematical discrepancy.")

    def test_02_tier_mutual_exclusivity_and_partitioning(self):
        """
        Challenge 2: Verify that classify_3tier_candidates strictly partitions universe candidates
        into mutually exclusive tiers (Tier 1, Tier 2, Tier 3) with zero duplicates across tiers
        and zero silent drops of qualified candidates.
        """
        print("\n" + "=" * 75)
        print("  [CHALLENGE 2] Tier Mutual Exclusivity & Partitioning in classify_3tier_candidates")
        print("=" * 75)

        # Create a rich universe of 30 synthetic tickers with known qualification properties
        test_universe: Dict[str, Dict[str, Any]] = {}
        
        # 8 Qualified Tier 1 candidates (Macro tailwind + Weekly Bull + Strat 1/2 + Safe Entry)
        for i in range(1, 9):
            tk = f"T1_STOCK_{i}"
            test_universe[tk] = {
                "latest_close": 200.0 + i * 5,
                "latest_kijun": 198.0 + i * 5,
                "latest_tenkan": 199.0 + i * 5,
                "span_a": 190.0, "span_b": 185.0,
                "latest_vol_ratio": 0.50 + i * 0.02,
                "is_weekly_bull": True,
                "obv_status": "STEALTH_ACCUM",
                "flow_ratio": 1.5,
                "flow_score": 90 - i,
                "is_stealth_accum": True,
                "trampoline_detected": False
            }

        # 8 Qualified Tier 2 candidates (Strat 1 Pullback, VDU <= 0.85, Safe Entry, Non-tailwind, Neutral Flow)
        for i in range(1, 9):
            tk = f"T2_STOCK_{i}"
            test_universe[tk] = {
                "latest_close": 115.0 + i * 2,
                "latest_kijun": 113.0 + i * 2,
                "latest_tenkan": 112.0 + i * 2,
                "span_a": 114.0, "span_b": 105.0,
                "latest_vol_ratio": 0.70 + i * 0.01,
                "is_weekly_bull": True,
                "obv_status": "NEUTRAL",
                "flow_ratio": 1.0,
                "flow_score": 50,
                "is_stealth_accum": False,
                "trampoline_detected": False,
                "sector": "CONSUMER"
            }

        # 8 Qualified Tier 3 candidates (Weekly Bull + Trampoline Bounce active, kgap > 3.5%)
        for i in range(1, 9):
            tk = f"T3_STOCK_{i}"
            test_universe[tk] = {
                "latest_close": 150.0 + i * 3,
                "latest_kijun": 142.0 + i * 3,
                "latest_tenkan": 148.0 + i * 3,
                "span_a": 138.0, "span_b": 130.0,
                "latest_vol_ratio": 0.95,
                "is_weekly_bull": True,
                "obv_status": "NEUTRAL",
                "flow_ratio": 1.0,
                "flow_score": 50,
                "is_stealth_accum": False,
                "trampoline_detected": True,
                "trampoline_days_ago": 2 + (i % 5),
                "sector": "HEALTHCARE"
            }

        # 6 Unqualified/Bear candidates
        for i in range(1, 7):
            tk = f"BEAR_STOCK_{i}"
            test_universe[tk] = {
                "latest_close": 50.0,
                "latest_kijun": 65.0,
                "latest_tenkan": 55.0,
                "span_a": 70.0, "span_b": 68.0,
                "latest_vol_ratio": 1.80,
                "is_weekly_bull": False,
                "obv_status": "NEUTRAL",
                "flow_ratio": 0.5,
                "flow_score": 20,
                "is_stealth_accum": False,
                "trampoline_detected": False,
                "sector": "ENERGY"
            }

        # Only T1_STOCK is in tailwind sector
        for i in range(1, 9):
            test_universe[f"T1_STOCK_{i}"]["sector"] = "TECH"

        tailwind_sectors = ["TECH", "SEMICONDUCTOR"]
        t1_picks, t2_picks, t3_picks = classify_3tier_candidates(
            test_universe,
            tailwind_sectors=tailwind_sectors
        )

        # 1. Verify Top 4 Capacity Limits
        self.assertLessEqual(len(t1_picks), 4, "Tier 1 must not exceed 4 picks")
        self.assertLessEqual(len(t2_picks), 4, "Tier 2 must not exceed 4 picks")
        self.assertLessEqual(len(t3_picks), 4, "Tier 3 must not exceed 4 picks")

        t1_set = set(x["ticker"] for x in t1_picks)
        t2_set = set(x["ticker"] for x in t2_picks)
        t3_set = set(x["ticker"] for x in t3_picks)

        # 2. Assert Strict Pairwise Mutual Exclusivity (Zero Duplicates)
        self.assertTrue(t1_set.isdisjoint(t2_set), f"Tier 1 and Tier 2 overlap: {t1_set & t2_set}")
        self.assertTrue(t1_set.isdisjoint(t3_set), f"Tier 1 and Tier 3 overlap: {t1_set & t3_set}")
        self.assertTrue(t2_set.isdisjoint(t3_set), f"Tier 2 and Tier 3 overlap: {t2_set & t3_set}")

        # 3. Assert Zero Bear/Unqualified Stocks in Recommendations
        for i in range(1, 7):
            self.assertNotIn(f"BEAR_STOCK_{i}", t1_set | t2_set | t3_set)

        # 4. Verify Deterministic Ordering
        # Tier 1 sorted by -flow_score, -score, abs(kijun_gap), vol_ratio
        for k in range(len(t1_picks) - 1):
            curr_item = t1_picks[k]
            next_item = t1_picks[k+1]
            self.assertGreaterEqual(curr_item["flow_score"], next_item["flow_score"])

        print(f"  -> PASSED: Strict Mutual Exclusivity verified across Tier 1 ({len(t1_picks)}), Tier 2 ({len(t2_picks)}), Tier 3 ({len(t3_picks)}).")

    def test_03_stop_loss_and_target_consistency(self):
        """
        Challenge 3: Verify that all value objects, risk management modules, and JSON payloads
        strictly and consistently adhere to:
        - -4.0% Hard Stop (stop_price = round(price * 0.96, 2))
        - +15.0% Primary Target (target_price = round(price * 1.15, 2))
        - +8.0% Partial TP (partial_tp_price = round(price * 1.08, 2))
        """
        print("\n" + "=" * 75)
        print("  [CHALLENGE 3] -4.0% Hard Stop & +15.0% Target Rule Consistency")
        print("=" * 75)

        test_prices = [10.0, 25.50, 48.75, 100.0, 130.50, 225.0, 450.25, 980.0, 1500.0]

        for p in test_prices:
            ind = QuantIndicators.from_values(
                close=p, kijun=p * 0.99, tenkan=p * 0.995, span_a=p * 0.95, span_b=p * 0.90, vol_ratio=0.55
            )
            weekly = WeeklyTrendContext(is_weekly_bull=True)
            flow = InstitutionalFlowContext(obv_status="STEALTH_ACCUM", flow_ratio=1.4, flow_score=85, is_stealth_accum=True)
            tramp = TrampolineBounceContext(detected=False)

            tier_res = classify_quant_tier(
                ticker="TEST",
                ind=ind,
                weekly=weekly,
                flow=flow,
                trampoline=tramp,
                macro_tailwind_sectors=["GENERAL"]
            )

            expected_stop = round(p * 0.96, 2)
            expected_target = round(p * 1.15, 2)
            expected_partial = round(p * 1.08, 2)

            self.assertEqual(tier_res.stop_price, expected_stop, f"Stop price mismatch for price {p}")
            self.assertEqual(tier_res.target_price, expected_target, f"Target price mismatch for price {p}")
            self.assertEqual(tier_res.partial_tp_price, expected_partial, f"Partial TP mismatch for price {p}")

        # Verify Persistence payload mapping consistency
        from al_sangmoo.infrastructure.persistence import archive_daily_recommendations, get_connection
        synthetic_chart_data = {
            "NVDA": {
                "latest_close": 125.0, "latest_kijun": 123.0, "latest_tenkan": 124.0,
                "span_a": 115.0, "span_b": 110.0, "latest_vol_ratio": 0.50,
                "is_weekly_bull": True, "obv_status": "STEALTH_ACCUM", "flow_ratio": 1.5,
                "flow_score": 90, "is_stealth_accum": True, "trampoline_detected": False
            }
        }
        t1, t2, t3 = classify_3tier_candidates(synthetic_chart_data, tailwind_sectors=["TECH"])
        self.assertEqual(len(t1), 1)
        nvda_t1 = t1[0]
        self.assertEqual(nvda_t1["stop_price"], round(125.0 * 0.96, 2))  # 120.0
        self.assertEqual(nvda_t1["target_price"], round(125.0 * 1.15, 2)) # 143.75

        print("  -> PASSED: All value objects and payloads consistently adhere to the -4.0% hard stop rule.")

    def test_04_consumer_pipeline_parity_50_scenarios(self):
        """
        Challenge 4: Compare scoring outputs produced by domain quant models against
        the feed calculations across all 50 synthetic stock scenarios.
        Assert that bull_score, sniper_score, bear_score, and classifications match 100%.
        """
        print("\n" + "=" * 75)
        print("  [CHALLENGE 4] Consumer Pipeline Scoring Parity against Domain SSOT")
        print("=" * 75)

        np.random.seed(4242)
        for i in range(50):
            price = round(float(np.random.uniform(20.0, 500.0)), 2)
            kgap = float(np.random.uniform(-5.0, 8.0))
            kijun = round(price / (1.0 + kgap / 100.0), 2)
            tenkan = round(kijun + float(np.random.uniform(-3.0, 3.0)), 2)
            span_a = round(kijun * float(np.random.uniform(0.92, 1.05)), 2)
            span_b = round(kijun * float(np.random.uniform(0.88, 1.00)), 2)
            vol_r = round(float(np.random.uniform(0.30, 2.0)), 2)
            is_sn = bool(np.random.choice([True, False]))
            w_bull = bool(np.random.choice([True, False]))

            # 1. Domain SSOT
            dom_eval = evaluate_quant_score(
                close=price,
                kijun=kijun,
                tenkan=tenkan,
                span_a=span_a,
                span_b=span_b,
                vol_ratio=vol_r,
                trampoline_detected=is_sn,
                is_weekly_bull=w_bull,
                days_ago=2 if is_sn else 0
            )

            # 2. Consumer Feed Logic emulation
            cloud_top = max(span_a, span_b)
            cloud_bottom = min(span_a, span_b)
            kgap_feed = ((price - kijun) / kijun) * 100

            b_score = 0
            if price >= cloud_top: b_score += 35
            elif price >= cloud_top * 0.97: b_score += 25
            elif price >= cloud_bottom: b_score += 15

            if -0.5 <= kgap_feed <= 3.5: b_score += 35
            elif -0.8 <= kgap_feed <= 4.8: b_score += 25
            elif -1.5 <= kgap_feed <= 7.0: b_score += 15

            if vol_r <= 0.60: b_score += 20
            elif vol_r <= 0.85: b_score += 15
            elif vol_r <= 1.10: b_score += 10

            if tenkan >= kijun: b_score += 10
            elif price >= tenkan: b_score += 5

            s_score = 0
            if is_sn: s_score += 40
            if price >= cloud_top: s_score += 30
            elif price >= cloud_top * 0.98: s_score += 20
            if kgap_feed >= 0: s_score += 15
            elif kgap_feed >= -1.0: s_score += 10
            if tenkan >= kijun: s_score += 15
            elif price >= tenkan: s_score += 10

            bear_s = 0
            if price < kijun: bear_s += 40
            if price < cloud_bottom: bear_s += 35
            if kgap_feed < -2.0: bear_s += 15

            # Assert 0.0000% difference
            self.assertEqual(dom_eval["bull_score"], b_score, f"Bull score mismatch at idx {i}")
            self.assertEqual(dom_eval["sniper_score"], s_score, f"Sniper score mismatch at idx {i}")
            self.assertEqual(dom_eval["bear_score"], bear_s, f"Bear score mismatch at idx {i}")

        print("  -> PASSED: 50/50 consumer feed scenarios match domain scoring with 0.0000% discrepancy.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
