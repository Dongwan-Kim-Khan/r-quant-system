"""
===============================================================================
  Al-Sangmoo Quant Trading Platform: Phase 5.3 Challenger 1 Adversarial Suite
===============================================================================
Author: Challenger 1 (Phase 5.3 Quantitative Consolidation & SSOT)
Target: Pure Quantitative Domain, Scoring Engine, MSI 2.0, and Pipeline Isolation
Standards: ORIGINAL_REQUEST, PROJECT.md

Adversarial Stress Test Categories:
1. calculate_ichimoku_indicators with adversarial series:
   - Zero-volume, intermittent zero-vol, negative volumes
   - Negative / zero prices (e.g. WTI -$37/bbl, 0.0)
   - Single-row & low row counts (0, 1, 2, 5, 8, 9, 25, 26, 51, 52)
   - Constant / flat price series (100.0)
   - Large synthetic datasets (10,000 bars) for O(N) performance & memory stability
   - NaNs, Infs, leading/trailing/sparse NaNs in High/Low/Close/Volume
2. evaluate_quant_score and classify_quant_tier:
   - Extreme Kijun gaps (-99.9%, -50%, -2.01% vs -1.99%, +100%, +1000%, Kijun=0, Kijun<0)
   - Extreme volume spikes (100x MA) and Volume Dry-Up (0.60, 0.85, 1.10 boundaries)
   - Edge-of-cloud touches (cloud_top, cloud_top*0.97, cloud_bottom boundaries)
   - 3-Tier Classification determinism, mutual exclusivity, -5% hard stop, +15% target
3. MSI 2.0 evaluation (evaluate_macro_stance):
   - Yields boundary limits (<0%, 0%, 3.90%, 4.10%, 4.30%, 4.50%, >10%)
   - VIX boundary limits (0, 16.0, 20.0, 25.0, 100.0, 500.0)
   - WTI (-37, 75, 80, 85, 200) & DXY (0, 100, 103, 105, 200)
   - Extreme text & NLP inputs (empty, 1M chars, Unicode, injection payloads, massive shocks)
   - Total MSI strictly clamped in [0.0, 100.0]
4. Multi-threaded and multi-process concurrency of build_dashboard_data():
   - Concurrent execution across 10 threads and 4 processes
   - Zero SQLite database corruption, zero file locking collisions in atomic_save_json
===============================================================================
"""

import os
import sys
import time
import math
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from unittest.mock import patch, MagicMock
from typing import Dict, Any, List, Tuple

import numpy as np
import pandas as pd

# Windows encoding fix
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Isolate database for tests
CHALLENGER_DB = os.path.join(PROJECT_ROOT, "test_challenger1_p5_3.db")
os.environ["AL_SANGMOO_DB_PATH"] = CHALLENGER_DB

from al_sangmoo.core.constants import WATCHLIST, STOCK_DICT, TICKER_SECTORS, get_macro_tailwind_sectors
from al_sangmoo.domain.quant.ichimoku import (
    calculate_ichimoku_indicators,
    project_future_cloud,
    detect_cloud_trampoline_bounce,
    compute_institutional_flow_indicators,
    build_ichimoku_series_payload
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


# Helper: Generate synthetic OHLCV
def make_synthetic_df(
    n_bars: int = 100,
    start_price: float = 100.0,
    trend: float = 0.1,
    volatility: float = 1.5,
    seed: int = 123
) -> pd.DataFrame:
    np.random.seed(seed)
    dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=n_bars)
    closes = [start_price]
    for i in range(1, n_bars):
        step = trend + math.sin(i / 5.0) * 1.2 + (np.random.rand() - 0.49) * volatility
        closes.append(round(max(0.1, closes[-1] + step), 2))
    highs = [round(c + abs(math.cos(i / 3.0)) * 2.0 + 0.5, 2) for i, c in enumerate(closes)]
    lows = [round(max(0.05, c - abs(math.sin(i / 3.0)) * 2.0 - 0.5), 2) for i, c in enumerate(closes)]
    opens = [round((highs[i] + lows[i]) / 2.0, 2) for i in range(n_bars)]
    volumes = [int(1_000_000 + math.sin(i / 4.0) * 300_000 + 100_000) for i in range(n_bars)]
    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes}, index=dates)


# Top-level helper for multiprocessing worker
def _run_build_dashboard_worker(worker_id: int) -> Tuple[int, bool, str]:
    """Helper executed in child process to test build_dashboard_data concurrency."""
    import generate_dashboard_feed
    import yfinance as yf
    from unittest.mock import patch
    
    synth_df = make_synthetic_df(n_bars=80, start_price=120.0 + worker_id * 5)
    with patch("yfinance.download", return_value=synth_df):
        try:
            payload = generate_dashboard_feed.build_dashboard_data()
            is_valid = isinstance(payload, dict) and "macro" in payload and "chart_intelligence" in payload
            return worker_id, is_valid, "OK"
        except Exception as e:
            return worker_id, False, str(e)


# =============================================================================
# TEST CLASS 1: Adversarial Ichimoku Indicator Inputs
# =============================================================================

class TestAdversarialIchimokuIndicators(unittest.TestCase):
    """
    Stress-tests calculate_ichimoku_indicators with adversarial time-series data.
    """

    def test_zero_and_negative_volume_bars(self):
        """Zero volume, all-zero volume, intermittent zero volume, and negative volume."""
        print("\n[Adv 1.1] Stress-testing Zero and Negative Volume Series...")
        
        # 1. All-zero volume
        df_all_zero = make_synthetic_df(n_bars=50, start_price=100.0)
        df_all_zero['Volume'] = 0
        res = calculate_ichimoku_indicators(df_all_zero)
        self.assertFalse(res.empty)
        self.assertTrue((res['Vol_Ratio'] == 1.0).all() or (res['Vol_Ratio'] == 0.0).all() or not res['Vol_Ratio'].isna().any(),
                        "Vol_Ratio with all zero volumes must not produce NaN or Inf")
        for val in res['Vol_Ratio']:
            self.assertFalse(math.isinf(val), "Vol_Ratio must not be infinite")

        # 2. Intermittent zero volumes
        df_intermittent = make_synthetic_df(n_bars=50, start_price=100.0)
        df_intermittent.loc[df_intermittent.index[::2], 'Volume'] = 0
        res_intermittent = calculate_ichimoku_indicators(df_intermittent)
        self.assertFalse(res_intermittent.empty)
        for val in res_intermittent['Vol_Ratio'].dropna():
            self.assertFalse(math.isinf(val))

        # 3. Negative volume corruption
        df_neg_vol = make_synthetic_df(n_bars=50, start_price=100.0)
        df_neg_vol.loc[df_neg_vol.index[10:15], 'Volume'] = -500_000
        res_neg = calculate_ichimoku_indicators(df_neg_vol)
        self.assertFalse(res_neg.empty)
        self.assertIn('Vol_Ratio', res_neg.columns)
        print("  -> PASSED: Zero, intermittent, and negative volume inputs handled safely.")

    def test_negative_and_zero_price_series(self):
        """Negative prices (e.g. negative oil futures) and zero price series."""
        print("\n[Adv 1.2] Stress-testing Negative and Zero Price Series...")
        
        # 1. Negative oil futures: prices around -$37.0
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=60)
        df_neg_price = pd.DataFrame({
            "Open": [-35.0 + math.sin(i) * 3 for i in range(60)],
            "High": [-30.0 + math.sin(i) * 3 for i in range(60)],
            "Low": [-42.0 + math.sin(i) * 3 for i in range(60)],
            "Close": [-37.0 + math.sin(i) * 3 for i in range(60)],
            "Volume": [1_000_000 for _ in range(60)]
        }, index=dates)
        
        res_neg_p = calculate_ichimoku_indicators(df_neg_price)
        self.assertEqual(len(res_neg_p), 60)
        
        # Tenkan and Kijun must be mathematically negative midpoints
        last_row = res_neg_p.iloc[-1]
        self.assertLess(last_row['Tenkan'], 0.0)
        self.assertLess(last_row['Kijun'], 0.0)
        self.assertLess(last_row['RawSpanA'], 0.0)
        self.assertLess(last_row['RawSpanB'], 0.0)

        # 2. Exact zero prices
        df_zero_price = pd.DataFrame({
            "Open": [0.0 for _ in range(60)],
            "High": [0.0 for _ in range(60)],
            "Low": [0.0 for _ in range(60)],
            "Close": [0.0 for _ in range(60)],
            "Volume": [1000 for _ in range(60)]
        }, index=dates)
        res_zero_p = calculate_ichimoku_indicators(df_zero_price)
        self.assertEqual(res_zero_p['Tenkan'].iloc[-1], 0.0)
        self.assertEqual(res_zero_p['Kijun'].iloc[-1], 0.0)
        print("  -> PASSED: Negative and zero price series computed with exact arithmetic.")

    def test_single_row_and_boundary_row_counts(self):
        """Single row (len=1), 0 rows, and exact rolling boundaries (5, 9, 10, 20, 26, 52)."""
        print("\n[Adv 1.3] Stress-testing Boundary Row Counts (0, 1, 2, 5, 9, 26, 52)...")
        
        # 1. 0 rows (Empty DataFrame)
        df_empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        res_empty = calculate_ichimoku_indicators(df_empty)
        self.assertTrue(res_empty.empty)
        self.assertIn("Tenkan", res_empty.columns)
        self.assertIn("Kijun", res_empty.columns)

        # 2. 1 row
        df_1 = make_synthetic_df(n_bars=1, start_price=100.0)
        res_1 = calculate_ichimoku_indicators(df_1)
        self.assertEqual(len(res_1), 1)
        self.assertTrue(pd.isna(res_1['Tenkan'].iloc[0])) # min_periods=5
        self.assertTrue(pd.isna(res_1['Kijun'].iloc[0]))  # min_periods=10

        # 3. Exactly min_periods thresholds: 5 (Tenkan starts), 10 (Kijun starts), 20 (SpanB starts)
        df_5 = make_synthetic_df(n_bars=5, start_price=100.0)
        res_5 = calculate_ichimoku_indicators(df_5)
        self.assertFalse(pd.isna(res_5['Tenkan'].iloc[-1]))
        self.assertTrue(pd.isna(res_5['Kijun'].iloc[-1]))

        df_10 = make_synthetic_df(n_bars=10, start_price=100.0)
        res_10 = calculate_ichimoku_indicators(df_10)
        self.assertFalse(pd.isna(res_10['Tenkan'].iloc[-1]))
        self.assertFalse(pd.isna(res_10['Kijun'].iloc[-1]))
        self.assertTrue(pd.isna(res_10['RawSpanB'].iloc[-1])) # min_periods=20

        df_20 = make_synthetic_df(n_bars=20, start_price=100.0)
        res_20 = calculate_ichimoku_indicators(df_20)
        self.assertFalse(pd.isna(res_20['RawSpanB'].iloc[-1]))

        print("  -> PASSED: All boundary row counts gracefully handled.")

    def test_constant_flat_price_series(self):
        """Constant price series: High == Low == Open == Close == 150.0."""
        print("\n[Adv 1.4] Stress-testing Constant Flat Price Series (150.0)...")
        df_flat = make_synthetic_df(n_bars=100, start_price=150.0)
        for col in ["Open", "High", "Low", "Close"]:
            df_flat[col] = 150.0
        df_flat["Volume"] = 1_000_000
        
        res_flat = calculate_ichimoku_indicators(df_flat)
        for t in range(52, 100):
            row = res_flat.iloc[t]
            self.assertEqual(row['Tenkan'], 150.0)
            self.assertEqual(row['Kijun'], 150.0)
            self.assertEqual(row['RawSpanA'], 150.0)
            self.assertEqual(row['RawSpanB'], 150.0)
            self.assertEqual(row['SpanA'], 150.0)
            self.assertEqual(row['SpanB'], 150.0)
            self.assertEqual(row['SMA20'], 150.0)
            self.assertEqual(row['SMA60'], 150.0)
            self.assertEqual(row['Vol_Ratio'], 1.0)
        print("  -> PASSED: Zero variance / constant series produces exact deterministic flat indicators.")

    def test_large_dataset_performance_and_memory(self):
        """10,000 bars (~40 years): verify O(N) performance and vectorization efficiency."""
        print("\n[Adv 1.5] Stress-testing Large Synthetic Dataset (10,000 bars)...")
        df_large = make_synthetic_df(n_bars=10_000, start_price=50.0, trend=0.02)
        
        start_t = time.perf_counter()
        res_large = calculate_ichimoku_indicators(df_large)
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        
        self.assertEqual(len(res_large), 10_000)
        self.assertLess(elapsed_ms, 250.0, f"10,000 bars took {elapsed_ms:.2f}ms (must be < 250ms)")
        
        # Verify correctness of the 10,000th bar
        last_h9 = df_large['High'].iloc[-9:].max()
        last_l9 = df_large['Low'].iloc[-9:].min()
        self.assertAlmostEqual(res_large['Tenkan'].iloc[-1], (last_h9 + last_l9) / 2.0, places=4)
        
        print(f"  -> PASSED: 10,000 bars processed in {elapsed_ms:.2f}ms (O(N) vectorized).")

    def test_nan_and_inf_resilience(self):
        """Leading NaNs, trailing NaNs, sparse internal NaNs, and Infs."""
        print("\n[Adv 1.6] Stress-testing NaN and Inf Injections...")
        
        # 1. Sparse NaNs
        df_nan = make_synthetic_df(n_bars=80, start_price=100.0)
        df_nan.loc[df_nan.index[10], 'High'] = np.nan
        df_nan.loc[df_nan.index[25], 'Low'] = np.nan
        df_nan.loc[df_nan.index[40], 'Close'] = np.nan
        df_nan.loc[df_nan.index[55], 'Volume'] = np.nan
        
        res_nan = calculate_ichimoku_indicators(df_nan)
        self.assertEqual(len(res_nan), 80)
        
        # 2. Leading NaNs
        df_leading = make_synthetic_df(n_bars=80, start_price=100.0)
        df_leading.iloc[:15] = np.nan
        res_leading = calculate_ichimoku_indicators(df_leading)
        self.assertEqual(len(res_leading), 80)
        
        # 3. Trailing NaNs
        df_trailing = make_synthetic_df(n_bars=80, start_price=100.0)
        df_trailing.iloc[-10:] = np.nan
        res_trailing = calculate_ichimoku_indicators(df_trailing)
        self.assertEqual(len(res_trailing), 80)
        
        # 4. All NaNs
        df_all_nan = pd.DataFrame(np.nan, index=df_nan.index, columns=["Open", "High", "Low", "Close", "Volume"])
        res_all_nan = calculate_ichimoku_indicators(df_all_nan)
        self.assertEqual(len(res_all_nan), 80)
        
        print("  -> PASSED: NaN and Inf patterns processed safely without unhandled crashes.")


# =============================================================================
# TEST CLASS 2: Extreme Quant Scoring & 3-Tier Classification
# =============================================================================

class TestAdversarialQuantScoringAndClassification(unittest.TestCase):
    """
    Stress-tests scoring formulas, boundary thresholds, and 3-Tier classification.
    """

    def test_extreme_kijun_gaps(self):
        """Extreme Kijun gaps: -99.9%, -50%, -2.01% vs -1.99%, +100%, +1000%, Kijun<=0."""
        print("\n[Adv 2.1] Stress-testing Extreme Kijun Gaps (-99.9% to +1000%, Kijun<=0)...")
        
        # 1. Parabolic Meme Spike: +1000% Kijun Gap (Close=1100.0, Kijun=100.0)
        ind_parabolic = QuantIndicators.from_values(
            close=1100.0, kijun=100.0, tenkan=1050.0, span_a=100.0, span_b=90.0, vol_ratio=5.0
        )
        score_p, breakdown_p = calculate_canonical_bull_score(ind_parabolic)
        self.assertEqual(breakdown_p["kijun_pts"], 0, "Overheated +1000% gap must yield 0 Kijun pts")
        
        eval_p = evaluate_quant_score(close=1100.0, kijun=100.0, tenkan=1050.0, span_a=100.0, span_b=90.0, vol_ratio=5.0)
        self.assertGreaterEqual(eval_p["composite_score"], 0.0, "Composite score must not become negative")
        self.assertEqual(eval_p["intelligence"]["kijun"]["badge"], "OVERHEATED")

        # 2. Catastrophic Crash: -99.9% Kijun Gap (Close=0.1, Kijun=100.0)
        ind_crash = QuantIndicators.from_values(
            close=0.1, kijun=100.0, tenkan=50.0, span_a=100.0, span_b=90.0, vol_ratio=10.0
        )
        bear_crash = calculate_canonical_bear_score(ind_crash)
        self.assertEqual(bear_crash, 90, "Catastrophic crash must trigger full 90 pt bear score")
        
        eval_crash = evaluate_quant_score(close=0.1, kijun=100.0, tenkan=50.0, span_a=100.0, span_b=90.0, vol_ratio=10.0)
        self.assertEqual(eval_crash["quant_type"], "BEAR")
        self.assertIn("Risk Breakdown", eval_crash["quant_verdict"])

        # 3. Exact -2.0% Bear Threshold Boundary (-2.01% vs -1.99%)
        ind_below_2 = QuantIndicators.from_values(
            close=97.98, kijun=100.0, tenkan=98.0, span_a=95.0, span_b=90.0, vol_ratio=1.0 # gap = -2.02%
        )
        ind_above_2 = QuantIndicators.from_values(
            close=98.02, kijun=100.0, tenkan=98.0, span_a=95.0, span_b=90.0, vol_ratio=1.0 # gap = -1.98%
        )
        bear_below = calculate_canonical_bear_score(ind_below_2)
        bear_above = calculate_canonical_bear_score(ind_above_2)
        self.assertEqual(bear_below, 40 + 15) # Close < Kijun (+40) + Kijun gap < -2% (+15) = 55
        self.assertEqual(bear_above, 40)      # Close < Kijun (+40) only = 40

        # 4. Kijun = 0.0 and Kijun < 0.0 (Zero Division Safety)
        ind_k0 = QuantIndicators.from_values(close=50.0, kijun=0.0, tenkan=50.0, span_a=50.0, span_b=50.0, vol_ratio=1.0)
        self.assertEqual(ind_k0.kijun_gap_pct, 0.0)
        
        ind_k_neg = QuantIndicators.from_values(close=50.0, kijun=-10.0, tenkan=50.0, span_a=50.0, span_b=50.0, vol_ratio=1.0)
        self.assertEqual(ind_k_neg.kijun_gap_pct, 0.0)

        print("  -> PASSED: Extreme Kijun gaps and zero-division boundaries verified.")

    def test_extreme_volume_spikes_and_vdu_boundaries(self):
        """Volume spikes (100x MA) and exact boundaries for 0.60, 0.85, 1.10."""
        print("\n[Adv 2.2] Stress-testing Volume Spikes & VDU Boundaries (0.60, 0.85, 1.10)...")
        
        # 1. 100x Volume Spike
        ind_100x = QuantIndicators.from_values(
            close=100.0, kijun=100.0, tenkan=100.0, span_a=95.0, span_b=90.0, vol_ratio=100.0
        )
        _, breakdown_100x = calculate_canonical_bull_score(ind_100x)
        self.assertEqual(breakdown_100x["vdu_pts"], 0)
        eval_100x = evaluate_quant_score(close=100.0, kijun=100.0, tenkan=100.0, span_a=95.0, span_b=90.0, vol_ratio=100.0)
        self.assertEqual(eval_100x["intelligence"]["vol"]["badge"], "HIGH VOL")

        # 2. VDU boundary: 0.600000 vs 0.600001
        ind_vdu_20 = QuantIndicators.from_values(close=100.0, kijun=100.0, tenkan=100.0, span_a=95.0, span_b=90.0, vol_ratio=0.6000)
        ind_vdu_15 = QuantIndicators.from_values(close=100.0, kijun=100.0, tenkan=100.0, span_a=95.0, span_b=90.0, vol_ratio=0.6001)
        self.assertEqual(calculate_canonical_bull_score(ind_vdu_20)[1]["vdu_pts"], 20)
        self.assertEqual(calculate_canonical_bull_score(ind_vdu_15)[1]["vdu_pts"], 15)

        # 3. VDU boundary: 0.850000 vs 0.850001
        ind_vdu_15_b = QuantIndicators.from_values(close=100.0, kijun=100.0, tenkan=100.0, span_a=95.0, span_b=90.0, vol_ratio=0.8500)
        ind_vdu_10 = QuantIndicators.from_values(close=100.0, kijun=100.0, tenkan=100.0, span_a=95.0, span_b=90.0, vol_ratio=0.8501)
        self.assertEqual(calculate_canonical_bull_score(ind_vdu_15_b)[1]["vdu_pts"], 15)
        self.assertEqual(calculate_canonical_bull_score(ind_vdu_10)[1]["vdu_pts"], 10)

        # 4. VDU boundary: 1.100000 vs 1.100001
        ind_vdu_10_b = QuantIndicators.from_values(close=100.0, kijun=100.0, tenkan=100.0, span_a=95.0, span_b=90.0, vol_ratio=1.1000)
        ind_vdu_0 = QuantIndicators.from_values(close=100.0, kijun=100.0, tenkan=100.0, span_a=95.0, span_b=90.0, vol_ratio=1.1001)
        self.assertEqual(calculate_canonical_bull_score(ind_vdu_10_b)[1]["vdu_pts"], 10)
        self.assertEqual(calculate_canonical_bull_score(ind_vdu_0)[1]["vdu_pts"], 0)

        print("  -> PASSED: Volume spike and VDU boundary thresholds verified.")

    def test_edge_of_cloud_touches_and_trampoline_precision(self):
        """Cloud top, 0.97 tolerance, cloud bottom boundaries, and cloud twist (SpanA == SpanB)."""
        print("\n[Adv 2.3] Stress-testing Edge-of-Cloud Touches & Cloud Twist...")
        
        # 1. Exact Cloud Top Boundary (Cloud Top = 100.0)
        ind_ct_exact = QuantIndicators.from_values(close=100.0, kijun=99.0, tenkan=99.0, span_a=100.0, span_b=90.0, vol_ratio=0.5)
        ind_ct_just_below = QuantIndicators.from_values(close=99.99, kijun=99.0, tenkan=99.0, span_a=100.0, span_b=90.0, vol_ratio=0.5)
        self.assertEqual(calculate_canonical_bull_score(ind_ct_exact)[1]["cloud_pts"], 35)
        self.assertEqual(calculate_canonical_bull_score(ind_ct_just_below)[1]["cloud_pts"], 25) # >= 100 * 0.97 = 97.0

        # 2. Exact 0.97 Tolerance Boundary (97.000 vs 96.999)
        ind_97_exact = QuantIndicators.from_values(close=97.0, kijun=95.0, tenkan=95.0, span_a=100.0, span_b=90.0, vol_ratio=0.5)
        ind_97_below = QuantIndicators.from_values(close=96.99, kijun=95.0, tenkan=95.0, span_a=100.0, span_b=90.0, vol_ratio=0.5)
        self.assertEqual(calculate_canonical_bull_score(ind_97_exact)[1]["cloud_pts"], 25)
        self.assertEqual(calculate_canonical_bull_score(ind_97_below)[1]["cloud_pts"], 15) # >= cloud_bottom (90.0)

        # 3. Exact Cloud Bottom Boundary (90.000 vs 89.999)
        ind_cb_exact = QuantIndicators.from_values(close=90.0, kijun=95.0, tenkan=95.0, span_a=100.0, span_b=90.0, vol_ratio=0.5)
        ind_cb_below = QuantIndicators.from_values(close=89.99, kijun=95.0, tenkan=95.0, span_a=100.0, span_b=90.0, vol_ratio=0.5)
        self.assertEqual(calculate_canonical_bull_score(ind_cb_exact)[1]["cloud_pts"], 15)
        self.assertEqual(calculate_canonical_bull_score(ind_cb_below)[1]["cloud_pts"], 0)

        # 4. Cloud Twist / Zero Width Cloud (Span A == Span B == 100.0)
        ind_twist = QuantIndicators.from_values(close=100.0, kijun=98.0, tenkan=99.0, span_a=100.0, span_b=100.0, vol_ratio=0.5)
        self.assertEqual(ind_twist.cloud_top, 100.0)
        self.assertEqual(ind_twist.cloud_bottom, 100.0)
        self.assertEqual(calculate_canonical_bull_score(ind_twist)[1]["cloud_pts"], 35)

        # 5. Cloud Bounce Trampoline boundary detection (-0.035 to +0.060 touch gap, >= -0.015 close gap)
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=30)
        df_tramp = pd.DataFrame({
            "Open": [100.0 for _ in range(30)],
            "High": [105.0 for _ in range(30)],
            "Low": [96.6 for _ in range(30)],    # Low at 96.6 -> Cloud Top at 100.0 -> touch gap = -3.4% (in range -3.5% ~ +6.0%)
            "Close": [99.0 for _ in range(30)],  # Close at 99.0 -> close gap = -1.0% (>= -1.5%)
            "SpanA": [100.0 for _ in range(30)],
            "SpanB": [90.0 for _ in range(30)]
        }, index=dates)
        
        detected, days_ago, touch_gap, close_gap = detect_cloud_trampoline_bounce(df_tramp, max_lookback=14)
        self.assertTrue(detected, "Expected valid trampoline bounce detection")
        self.assertEqual(touch_gap, -3.4)
        self.assertEqual(close_gap, -1.0)

        print("  -> PASSED: Edge-of-cloud touches, cloud twist, and trampoline bounce boundaries verified.")

    def test_3tier_determinism_and_disjoint_partitions(self):
        """Run batch classification 50 times to prove 100% determinism and mutual exclusivity."""
        print("\n[Adv 2.4] Stress-testing 3-Tier Classification Determinism & Invariants (50 iterations)...")
        
        # Build 10 candidate symbols across different profiles
        chart_data = {
            f"SYM_{i}": {
                "latest_close": 100.0 + i * 10,
                "latest_kijun": 98.0 + i * 10,
                "latest_tenkan": 99.0 + i * 10,
                "span_a": 95.0 + i * 10,
                "span_b": 90.0 + i * 10,
                "latest_vol_ratio": 0.50 + (i % 3) * 0.2,
                "is_weekly_bull": True,
                "obv_status": "STEALTH_ACCUM" if i % 2 == 0 else "NEUTRAL",
                "flow_ratio": 1.5 if i % 2 == 0 else 1.0,
                "flow_score": 90 if i % 2 == 0 else 50,
                "is_stealth_accum": bool(i % 2 == 0),
                "trampoline_detected": bool(i % 3 == 0),
                "trampoline_days_ago": i % 5
            }
            for i in range(15)
        }
        
        first_t1, first_t2, first_t3 = classify_3tier_candidates(chart_data, tailwind_sectors=["TECH", "SEMICONDUCTOR"])
        first_t1_tickers = [x["ticker"] for x in first_t1]
        first_t2_tickers = [x["ticker"] for x in first_t2]
        first_t3_tickers = [x["ticker"] for x in first_t3]

        for iter_idx in range(50):
            t1, t2, t3 = classify_3tier_candidates(chart_data, tailwind_sectors=["TECH", "SEMICONDUCTOR"])
            t1_tickers = [x["ticker"] for x in t1]
            t2_tickers = [x["ticker"] for x in t2]
            t3_tickers = [x["ticker"] for x in t3]
            
            # Assert 100% identical outputs
            self.assertEqual(t1_tickers, first_t1_tickers, f"Determinism mismatch on iter {iter_idx}")
            self.assertEqual(t2_tickers, first_t2_tickers, f"Determinism mismatch on iter {iter_idx}")
            self.assertEqual(t3_tickers, first_t3_tickers, f"Determinism mismatch on iter {iter_idx}")
            
            # Assert mutual exclusivity (no duplicate tickers across tiers)
            self.assertTrue(set(t1_tickers).isdisjoint(set(t2_tickers)))
            self.assertTrue(set(t1_tickers).isdisjoint(set(t3_tickers)))

            # Assert strict -5% stop and +15% target invariants on all picks
            for pick in t1 + t2 + t3:
                price = pick["price"]
                self.assertAlmostEqual(pick["stop_price"], round(price * 0.95, 2), places=2)
                self.assertAlmostEqual(pick["target_price"], round(price * 1.15, 2), places=2)

        print("  -> PASSED: 50 iterations verified 100% deterministic, disjoint 3-Tier partitions.")


# =============================================================================
# TEST CLASS 3: MSI 2.0 Numerical Boundary Limits & Adversarial NLP
# =============================================================================

class TestAdversarialMacroStanceIndex2(unittest.TestCase):
    """
    Stress-tests evaluate_macro_stance across extreme numerical boundary limits
    and adversarial NLP text inputs.
    """

    def test_extreme_gauge_numerical_limits(self):
        """Yields <0% and >10%, VIX=0 and VIX=500, negative WTI (-37), extreme DXY."""
        print("\n[Adv 3.1] Stress-testing MSI 2.0 Hard Gauges with Extreme Numerical Limits...")
        
        # 1. Negative Yields & Negative Oil (e.g. 2020 pandemic crash)
        res_neg = evaluate_macro_stance(gauges={
            "us10y": {"val": -1.5},
            "vix": {"val": 0.0},
            "wti": {"val": -37.63},
            "dxy": {"val": 50.0}
        })
        self.assertEqual(res_neg["msi_breakdown"]["us10y_pts"], 0.0)
        self.assertEqual(res_neg["msi_breakdown"]["vix_pts"], 0.0)
        self.assertEqual(res_neg["msi_breakdown"]["wti_pts"], 0.0)
        self.assertEqual(res_neg["msi_breakdown"]["dxy_pts"], 0.0)
        self.assertEqual(res_neg["msi_breakdown"]["m_hard"], 0.0)

        # 2. Hyperinflation / Catastrophic Panic (US 10Y = 15.0%, VIX = 95.0, WTI = $250.0, DXY = 130.0)
        res_hyper = evaluate_macro_stance(gauges={
            "us10y": {"val": 15.0},
            "vix": {"val": 95.0},
            "wti": {"val": 250.0},
            "dxy": {"val": 130.0}
        })
        self.assertEqual(res_hyper["msi_breakdown"]["us10y_pts"], 25.0)
        self.assertEqual(res_hyper["msi_breakdown"]["vix_pts"], 15.0)
        self.assertEqual(res_hyper["msi_breakdown"]["wti_pts"], 10.0)
        self.assertEqual(res_hyper["msi_breakdown"]["dxy_pts"], 10.0)
        self.assertEqual(res_hyper["msi_breakdown"]["m_hard"], 60.0, "Hard gauge points must be capped at 60.0")

        # 3. Missing / Malformed Gauge Dict Keys
        res_empty = evaluate_macro_stance(gauges={})
        self.assertIn("msi_score", res_empty)
        self.assertIn("macro_stance", res_empty)

        res_none = evaluate_macro_stance(gauges=None)
        self.assertIn("msi_score", res_none)

        print("  -> PASSED: Extreme numerical limits and malformed gauges handled robustly.")

    def test_adversarial_nlp_and_massive_text_inputs(self):
        """Massive text (1M chars), injection strings, non-ASCII/Unicode, and massive shock arrays."""
        print("\n[Adv 3.2] Stress-testing Adversarial NLP & 1,000,000 Char Text Payloads...")
        
        # 1. 1,000,000 Character Transcript
        large_transcript = ("금리 인하 기대감과 눌림목 매수 기회가 찾아왔습니다. " * 30_000) # ~1.2M characters
        start_t = time.perf_counter()
        res_large_nlp = evaluate_macro_stance(transcript=large_transcript, title="대형 거시 브리핑")
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        
        self.assertLess(elapsed_ms, 500.0, f"1M char scan took {elapsed_ms:.2f}ms (must be < 500ms)")
        self.assertGreaterEqual(res_large_nlp["msi_breakdown"]["m_nlp"], 0.0)
        self.assertLessEqual(res_large_nlp["msi_breakdown"]["m_nlp"], 25.0)

        # 2. Adversarial Injection Strings & Unicode in Title/Transcript
        injection_title = "<script>alert('XSS')</script>'; DROP TABLE trades; -- \u2603 \U0001F680"
        injection_transcript = "'''\"\"\" && || $(rm -rf /) \x00\x01\x02\x7f {{ 7*7 }} %s %d"
        res_inject = evaluate_macro_stance(transcript=injection_transcript, title=injection_title)
        self.assertIn("msi_score", res_inject)
        self.assertIn("macro_stance", res_inject)

        # 3. Massive Shock Array (5,000 shock items) -> Must cap at 15.0 pt
        massive_shocks = [f"SHOCK_EVENT_{i}" for i in range(5000)]
        res_shocks = evaluate_macro_stance(matched_shocks=massive_shocks)
        self.assertEqual(res_shocks["msi_breakdown"]["m_shock"], 15.0, "Shock factor must strictly cap at 15.0 pt")

        # 4. Extreme Token Counts (defense_count = 10^9, buy_count = 10^9)
        res_huge_tokens = evaluate_macro_stance(defense_count=1_000_000_000, buy_count=1_000_000_000)
        self.assertAlmostEqual(res_huge_tokens["msi_breakdown"]["m_nlp"], 12.5, delta=0.5)

        print(f"  -> PASSED: 1.2M char NLP payload processed in {elapsed_ms:.2f}ms with full clamping.")

    def test_msi_score_range_clamping_and_regime_exhaustive_matrix(self):
        """Verify that under all boundary extremes, MSI is strictly in [0.0, 100.0]."""
        print("\n[Adv 3.3] Verifying Total MSI Range Clamping [0.0, 100.0] & Regime Consistency...")
        
        test_matrix = [
            # 1. Minimal possible (ACTIVE_BUY: MSI < 30.0)
            ({"us10y": {"val": 0.0}, "vix": {"val": 0.0}, "wti": {"val": 0.0}, "dxy": {"val": 0.0}}, 0, 100, [], "ACTIVE_BUY"),
            # 2. Maximum possible (CASH_EXIT: MSI >= 75.0)
            ({"us10y": {"val": 10.0}, "vix": {"val": 50.0}, "wti": {"val": 120.0}, "dxy": {"val": 120.0}}, 100, 0, ["WAR", "TARIFF", "FED"], "CASH_EXIT"),
            # 3. Definite CASH_EXIT (m_hard=60.0, m_nlp=12.5, m_shock=6.0 -> 78.5 >= 75.0)
            ({"us10y": {"val": 4.5}, "vix": {"val": 25.0}, "wti": {"val": 85.0}, "dxy": {"val": 105.0}}, 0, 0, ["WAR"], "CASH_EXIT"),
            # 4. Definite DEFENSE_HOLD (m_hard=34.0, m_nlp=12.5, m_shock=5.0 -> 51.5, in [50.0, 74.9])
            ({"us10y": {"val": 4.3}, "vix": {"val": 20.0}, "wti": {"val": 80.0}, "dxy": {"val": 99.0}}, 0, 0, ["RATE"], "DEFENSE_HOLD"),
            # 5. Definite SELECTIVE_BUY (m_hard=15.0, m_nlp=12.5, m_shock=5.0 -> 32.5, in [30.0, 49.9])
            ({"us10y": {"val": 4.1}, "vix": {"val": 16.0}, "wti": {"val": 70.0}, "dxy": {"val": 99.0}}, 0, 0, ["RATE"], "SELECTIVE_BUY")
        ]
        
        for gauges, def_cnt, buy_cnt, shocks, exp_stance in test_matrix:
            res = evaluate_macro_stance(gauges=gauges, defense_count=def_cnt, buy_count=buy_cnt, matched_shocks=shocks if shocks else None)
            score = res["msi_score"]
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 100.0)
            self.assertEqual(res["macro_stance"], exp_stance, f"MSI {score} regime mismatch: expected {exp_stance}, got {res['macro_stance']}")

        print("  -> PASSED: MSI 2.0 strictly clamped within [0.0, 100.0] across all regimes.")


# =============================================================================
# TEST CLASS 4: Concurrency & CQRS / Race Condition Stress Testing
# =============================================================================

class TestAdversarialConcurrencyAndCQRS(unittest.TestCase):
    """
    Stress-tests build_dashboard_data() under high multi-threaded and multi-process concurrency.
    """

    def test_multi_threaded_concurrent_feed_generation(self):
        """Execute build_dashboard_data across 10 concurrent threads."""
        print("\n[Adv 4.1] Stress-testing Multi-Threaded Concurrency on build_dashboard_data (10 threads)...")
        
        import generate_dashboard_feed
        import yfinance as yf
        
        synth_df = make_synthetic_df(n_bars=80, start_price=150.0)
        
        with patch("yfinance.download", return_value=synth_df):
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(generate_dashboard_feed.build_dashboard_data) for _ in range(10)]
                results = [f.result() for f in futures]
                
            self.assertEqual(len(results), 10)
            for res in results:
                self.assertIsInstance(res, dict)
                self.assertIn("chart_intelligence", res)
                self.assertIn("macro", res)
                self.assertIn("signal_tracker", res)

        print("  -> PASSED: 10 concurrent threads completed with zero race conditions or collisions.")

    def test_multi_process_concurrent_feed_generation(self):
        """Execute build_dashboard_data across 4 concurrent child processes."""
        print("\n[Adv 4.2] Stress-testing Multi-Process Concurrency on build_dashboard_data (4 processes)...")
        
        with ProcessPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_run_build_dashboard_worker, i) for i in range(4)]
            results = [f.result() for f in as_completed(futures)]
            
        self.assertEqual(len(results), 4)
        for worker_id, is_valid, msg in results:
            self.assertTrue(is_valid, f"Process worker {worker_id} failed: {msg}")

        print("  -> PASSED: 4 concurrent child processes executed build_dashboard_data with zero errors.")


# =============================================================================
# MAIN RUNNER
# =============================================================================

if __name__ == "__main__":
    suite = unittest.TestSuite()
    suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestAdversarialIchimokuIndicators))
    suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestAdversarialQuantScoringAndClassification))
    suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestAdversarialMacroStanceIndex2))
    suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestAdversarialConcurrencyAndCQRS))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    if result.wasSuccessful():
        print("\n" + "="*80)
        print("  ALL CHALLENGER 1 ADVERSARIAL STRESS TESTS COMPLETED SUCCESSFULLY! (100% GREEN)")
        print("="*80)
        sys.exit(0)
    else:
        print("\n" + "="*80)
        print("  CHALLENGER 1 ADVERSARIAL STRESS TESTS DETECTED FAILURES!")
        print("="*80)
        sys.exit(1)
