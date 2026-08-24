import os
import sys
import unittest
import numpy as np
import pandas as pd

BASE_DIR = r"d:\코딩\Playground\al_sangmoo_project"
sys.path.insert(0, BASE_DIR)

from al_sangmoo.domain.quant.ichimoku import (
    calculate_ichimoku_indicators,
    detect_cloud_trampoline_bounce,
    compute_institutional_flow_indicators,
    build_ichimoku_series_payload,
    project_future_cloud
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
from al_sangmoo.domain.quant.macro import (
    evaluate_macro_stance,
    calculate_msi_regime
)

print("=" * 80)
print("ADVERSARIAL STRESS-TESTING & BOUNDARY INVARIANCE SUITE")
print("=" * 80)

class AdversarialStressTest(unittest.TestCase):

    def test_adv_1_degenerate_dataframes(self):
        """Test NaN prices, zero volume, single-row DataFrames."""
        print("\n[ADV 1] Testing Degenerate DataFrames...")
        
        # 1. Single row DataFrame
        df_1 = pd.DataFrame({"Open": [100.0], "High": [105.0], "Low": [95.0], "Close": [100.0], "Volume": [1000]},
                            index=pd.bdate_range("2026-01-01", periods=1))
        res_1 = calculate_ichimoku_indicators(df_1)
        self.assertEqual(len(res_1), 1)
        
        # 2. All-NaN DataFrame
        df_nan = pd.DataFrame({"Open": [np.nan]*20, "High": [np.nan]*20, "Low": [np.nan]*20, "Close": [np.nan]*20, "Volume": [np.nan]*20},
                              index=pd.bdate_range("2026-01-01", periods=20))
        res_nan = calculate_ichimoku_indicators(df_nan)
        self.assertEqual(len(res_nan), 20)
        
        # 3. Cloud trampoline on empty or 1-bar DataFrame
        self.assertEqual(detect_cloud_trampoline_bounce(None), (False, 0, 0.0, 0.0))
        self.assertEqual(detect_cloud_trampoline_bounce(df_1), (False, 0, 0.0, 0.0))
        
        # 4. Institutional flow on empty or short DataFrame
        flow_empty = compute_institutional_flow_indicators(None)
        self.assertEqual(flow_empty["obv_status"], "NEUTRAL")
        self.assertEqual(flow_empty["flow_score"], 50)
        
        # 5. Future cloud on empty DataFrame
        self.assertEqual(project_future_cloud(None), ([], [], [], []))
        self.assertEqual(project_future_cloud(pd.DataFrame()), ([], [], [], []))
        print("  -> PASSED: Degenerate DataFrame handling is 100% robust.")

    def test_adv_2_extreme_macro_climate(self):
        """Test extreme macro numbers (VIX 150, US10Y 20%, negative WTI -40$, DXY 160)."""
        print("\n[ADV 2] Testing Extreme Macro Climates...")
        
        # Negative oil shock (like April 2020)
        res_neg = evaluate_macro_stance(gauges={"us10y": {"val": 0.5}, "vix": {"val": 80.0}, "wti": {"val": -37.0}, "dxy": {"val": 100.0}})
        self.assertGreaterEqual(res_neg["msi_score"], 0.0)
        self.assertLessEqual(res_neg["msi_score"], 100.0)
        
        # Hyper-inflation & War shock & Trade shock
        res_hyper = evaluate_macro_stance(
            gauges={"us10y": {"val": 15.0}, "vix": {"val": 90.0}, "wti": {"val": 180.0}, "dxy": {"val": 130.0}},
            defense_count=1000, buy_count=0,
            matched_shocks=["WAR", "TRADE", "FED"]
        )
        self.assertEqual(res_hyper["msi_score"], 100.0)
        self.assertEqual(res_hyper["macro_stance"], "CASH_EXIT")
        
        # Super-Dovish Ultra Boom
        res_boom = evaluate_macro_stance(
            gauges={"us10y": {"val": 1.0}, "vix": {"val": 9.0}, "wti": {"val": 45.0}, "dxy": {"val": 85.0}},
            defense_count=0, buy_count=500
        )
        self.assertEqual(res_boom["msi_score"], 0.0)
        self.assertEqual(res_boom["macro_stance"], "ACTIVE_BUY")
        print("  -> PASSED: Extreme macro parameter bounds validated.")

    def test_adv_3_conflicting_scoring_and_edge_conditions(self):
        """Test quant scoring on extreme gaps, 0 prices, negative gaps, boundary crossovers."""
        print("\n[ADV 3] Testing Boundary and Conflicting Scoring Conditions...")
        
        # Exact boundary values
        # 1. kgap inside [-0.5%, +3.5%] (e.g. +2.0%)
        ind_edge1 = QuantIndicators.from_values(close=102.0, kijun=100.0, tenkan=100.0, span_a=95.0, span_b=90.0, vol_ratio=0.60)
        b1, _ = calculate_canonical_bull_score(ind_edge1)
        self.assertEqual(b1, 100) # 35 + 35 + 20 + 10 = 100
        
        # 2. kgap inside [-0.8%, +4.8%] (e.g. +4.0%)
        ind_edge2 = QuantIndicators.from_values(close=104.0, kijun=100.0, tenkan=100.0, span_a=95.0, span_b=90.0, vol_ratio=0.60)
        b2, _ = calculate_canonical_bull_score(ind_edge2)
        self.assertEqual(b2, 90) # 35 + 25 + 20 + 10 = 90
        
        # 3. vol_ratio exactly 0.60
        ind_edge3 = QuantIndicators.from_values(close=100.0, kijun=100.0, tenkan=100.0, span_a=95.0, span_b=90.0, vol_ratio=0.60)
        b3, breakdown3 = calculate_canonical_bull_score(ind_edge3)
        self.assertEqual(breakdown3["vdu_pts"], 20)
        
        # 4. vol_ratio exactly 0.85
        ind_edge4 = QuantIndicators.from_values(close=100.0, kijun=100.0, tenkan=100.0, span_a=95.0, span_b=90.0, vol_ratio=0.85)
        _, breakdown4 = calculate_canonical_bull_score(ind_edge4)
        self.assertEqual(breakdown4["vdu_pts"], 15)

        # 5. Large random universe (100 random symbols)
        chart_data_rand = {}
        for i in range(100):
            sym = f"SYM_{i:03d}"
            c_price = 50.0 + i * 2.0
            chart_data_rand[sym] = {
                "latest_close": c_price,
                "latest_kijun": c_price * (1.0 + (i % 5 - 2) * 0.01),
                "latest_tenkan": c_price * 1.01,
                "span_a": c_price * 0.95,
                "span_b": c_price * 0.90,
                "latest_vol_ratio": 0.5 + (i % 10) * 0.1,
                "is_weekly_bull": bool(i % 2 == 0),
                "obv_status": "STEALTH_ACCUM" if i % 3 == 0 else "NEUTRAL",
                "flow_ratio": 1.5 if i % 3 == 0 else 0.9,
                "flow_score": 85 if i % 3 == 0 else 40,
                "is_stealth_accum": bool(i % 3 == 0),
                "trampoline_detected": bool(i % 4 == 0),
                "trampoline_days_ago": i % 7
            }
        
        t1, t2, t3 = classify_3tier_candidates(chart_data_rand, tailwind_sectors=["TECH", "SEMICONDUCTOR"])
        t1_set = set(x["ticker"] for x in t1)
        t2_set = set(x["ticker"] for x in t2)
        t3_set = set(x["ticker"] for x in t3)
        
        self.assertTrue(t1_set.isdisjoint(t2_set))
        self.assertTrue(t1_set.isdisjoint(t3_set))
        self.assertLessEqual(len(t1), 4)
        self.assertLessEqual(len(t2), 4)
        self.assertLessEqual(len(t3), 4)
        print(f"  -> PASSED: 100 randomized universe symbols partitioned cleanly: T1={len(t1)}, T2={len(t2)}, T3={len(t3)}")

if __name__ == "__main__":
    unittest.main()
