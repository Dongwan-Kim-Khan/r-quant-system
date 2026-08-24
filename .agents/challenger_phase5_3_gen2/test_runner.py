"""
===============================================================================
  Adversarial Empirical Stress Test Suite for Phase 5.3 SSOT Quant Platform
===============================================================================
Author: Empirical Challenger
Target:
  - Stress Test 1: Indicator & Math Determinism (Noisy/extreme series, NaNs, 0-vol)
  - Stress Test 2: CQRS Concurrency & Zero-Mutation Hardening (Multi-threaded build_dashboard_data)
  - Stress Test 3: Scoring Equivalence & 3-Tier Boundary Stress (Exact float precision boundaries)
===============================================================================
"""

import os
import sys
import math
import json
import sqlite3
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch, MagicMock
from typing import Dict, Any, List

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.core.constants import WATCHLIST, STOCK_DICT, TICKER_SECTORS, get_macro_tailwind_sectors
from al_sangmoo.domain.quant.ichimoku import (
    calculate_ichimoku_indicators,
    project_future_cloud,
    detect_cloud_trampoline_bounce,
    compute_institutional_flow_indicators,
    build_ichimoku_series_payload,
    evaluate_quant_score as ichimoku_eval_adapter
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
import al_sangmoo.infrastructure.persistence as persistence
import generate_dashboard_feed
import al_sangmoo_daily_bot


class StressTest1IndicatorMathDeterminism(unittest.TestCase):
    """
    Stress Test 1: Mathematical Invariant, Extreme Volatility, NaN/Gap, and Zero-Volume Resilience.
    """

    def test_randomized_brownian_noise_invariants(self):
        """Test 1.1: 50 different noisy random-walk price series to verify mathematical invariants."""
        print("\n[STRESS 1.1] Executing 50 Monte Carlo Brownian Motion Time Series...")
        for seed in range(50):
            np.random.seed(seed + 100)
            n_bars = 250
            dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=n_bars)
            
            # Geometric Brownian Motion with drift and random jump shocks
            returns = np.random.normal(0.0005, 0.03, n_bars)
            # Add occasional +25% or -20% jump shocks
            jump_indices = np.random.choice(n_bars, size=5, replace=False)
            returns[jump_indices] += np.random.choice([0.25, -0.20, 0.35, -0.30], size=5)
            
            price_series = 100.0 * np.exp(np.cumsum(returns))
            closes = np.round(price_series, 2)
            highs = np.round(closes * (1 + np.abs(np.random.normal(0.01, 0.015, n_bars))), 2)
            lows = np.round(closes * (1 - np.abs(np.random.normal(0.01, 0.015, n_bars))), 2)
            opens = np.round((highs + lows) / 2, 2)
            volumes = np.random.randint(100, 50_000_000, n_bars)

            df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes}, index=dates)
            df_ind = calculate_ichimoku_indicators(df)

            # Invariant 1: Tenkan is midpoint of 9-period High/Low
            for idx in range(8, n_bars):
                exp_tenkan = (df['High'].iloc[idx-8:idx+1].max() + df['Low'].iloc[idx-8:idx+1].min()) / 2.0
                self.assertAlmostEqual(df_ind['Tenkan'].iloc[idx], exp_tenkan, places=4)

            # Invariant 2: Kijun is midpoint of 26-period High/Low
            for idx in range(25, n_bars):
                exp_kijun = (df['High'].iloc[idx-25:idx+1].max() + df['Low'].iloc[idx-25:idx+1].min()) / 2.0
                self.assertAlmostEqual(df_ind['Kijun'].iloc[idx], exp_kijun, places=4)

            # Invariant 3: RawSpanA is arithmetic mean of Tenkan and Kijun
            for idx in range(25, n_bars):
                t_val = df_ind['Tenkan'].iloc[idx]
                k_val = df_ind['Kijun'].iloc[idx]
                exp_rsa = (t_val + k_val) / 2.0
                self.assertAlmostEqual(df_ind['RawSpanA'].iloc[idx], exp_rsa, places=4)

            # Invariant 4: SpanA is exactly RawSpanA shifted +26
            for idx in range(51, n_bars):
                exp_sa = df_ind['RawSpanA'].iloc[idx - 26]
                self.assertAlmostEqual(df_ind['SpanA'].iloc[idx], exp_sa, places=4)

            # Invariant 5: Vol_Ratio is finite and >= 0
            valid_vol_ratios = df_ind['Vol_Ratio'].dropna()
            self.assertTrue((valid_vol_ratios >= 0).all())
            self.assertFalse(np.isinf(valid_vol_ratios).any())

    def test_extreme_boundary_spikes_and_micropenny(self):
        """Test 1.2: Micro-penny ($0.000001) and Hyper-Stock ($1,000,000,000) boundary values."""
        print("[STRESS 1.2] Testing Extreme Micro-Penny and Hyper-Spike Price series...")
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=100)
        
        # Scenario A: Micro-penny stock with sub-cent movements
        df_micro = pd.DataFrame({
            "Open": [0.00005] * 100,
            "High": [0.00009] * 100,
            "Low": [0.00001] * 100,
            "Close": [0.00006] * 100,
            "Volume": [1000] * 100
        }, index=dates)
        ind_micro = calculate_ichimoku_indicators(df_micro)
        self.assertFalse(ind_micro['Tenkan'].isna().iloc[-1])
        self.assertAlmostEqual(ind_micro['Tenkan'].iloc[-1], 0.00005, places=6)
        
        # Test scoring on micro-penny
        q_eval_micro = evaluate_quant_score(
            close=0.00006,
            kijun=0.00005,
            tenkan=0.00005,
            span_a=0.00005,
            span_b=0.00004,
            vol_ratio=0.5
        )
        self.assertIsInstance(q_eval_micro["bull_score"], int)
        self.assertTrue(0 <= q_eval_micro["bull_score"] <= 100)

        # Scenario B: Hyper-valued stock ($10,000,000/share)
        df_hyper = pd.DataFrame({
            "Open": [10_000_000.0] * 100,
            "High": [10_500_000.0] * 100,
            "Low": [9_500_000.0] * 100,
            "Close": [10_100_000.0] * 100,
            "Volume": [10_000_000_000] * 100
        }, index=dates)
        ind_hyper = calculate_ichimoku_indicators(df_hyper)
        self.assertAlmostEqual(ind_hyper['Kijun'].iloc[-1], 10_000_000.0, places=1)

    def test_zero_flat_volume_and_nan_gaps(self):
        """Test 1.3: Flat zero-volume series, sudden volume bursts, and NaN data gaps."""
        print("[STRESS 1.3] Testing Flat Zero-Volume and Intermittent NaN Gaps...")
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=100)
        
        # Scenario A: All zero volumes
        df_zero_vol = pd.DataFrame({
            "Open": [100.0] * 100,
            "High": [105.0] * 100,
            "Low": [95.0] * 100,
            "Close": [102.0] * 100,
            "Volume": [0] * 100
        }, index=dates)
        ind_zero = calculate_ichimoku_indicators(df_zero_vol)
        # Vol_Ratio should default safely to 1.0 (no division by zero error)
        self.assertTrue((ind_zero['Vol_Ratio'] == 1.0).all())

        # Flow indicators on zero volume
        flow_zero = compute_institutional_flow_indicators(df_zero_vol)
        self.assertEqual(flow_zero["flow_ratio"], 2.5) # When down_vol is 0, defaults to 2.5
        self.assertFalse(np.isnan(flow_zero["flow_score"]))

        # Scenario B: Random NaNs in High/Low/Close/Volume
        df_nans = df_zero_vol.copy()
        df_nans.loc[dates[10:15], "Volume"] = np.nan
        df_nans.loc[dates[30:35], "High"] = np.nan
        df_nans.loc[dates[50:55], "Close"] = np.nan
        ind_nans = calculate_ichimoku_indicators(df_nans)
        self.assertIn("Tenkan", ind_nans.columns)
        self.assertIn("Vol_Ratio", ind_nans.columns)

    def test_short_and_empty_series_resilience(self):
        """Test 1.4: Degenerate lengths (0 bars, 1 bar, 4 bars, 8 bars, 25 bars, 51 bars)."""
        print("[STRESS 1.4] Testing Degenerate Short Lengths (0 to 51 bars)...")
        for length in [0, 1, 2, 4, 8, 15, 25, 51]:
            if length == 0:
                df_short = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
            else:
                dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=length)
                df_short = pd.DataFrame({
                    "Open": [100.0] * length,
                    "High": [105.0] * length,
                    "Low": [95.0] * length,
                    "Close": [102.0] * length,
                    "Volume": [1000] * length
                }, index=dates)
            
            # None of these should crash
            ind_short = calculate_ichimoku_indicators(df_short)
            self.assertEqual(len(ind_short), length)
            
            flow_short = compute_institutional_flow_indicators(df_short)
            self.assertIn("flow_score", flow_short)
            
            payload = build_ichimoku_series_payload(ind_short)
            self.assertIsInstance(payload, dict)

    def test_macro_stance_adversarial_inputs(self):
        """Test 1.5: Adversarial inputs to evaluate_macro_stance (huge text, extreme gauges)."""
        print("[STRESS 1.5] Testing Macro Stance Adversarial Inputs...")
        # Extreme negative/positive financial gauges
        gauges = {
            "us10y": {"val": -2.5},
            "vix": {"val": 150.0},
            "wti": {"val": -37.0}, # Negative oil price like 2020
            "dxy": {"val": 250.0}
        }
        res = evaluate_macro_stance(gauges=gauges)
        self.assertTrue(0.0 <= res["msi_score"] <= 100.0)
        self.assertIn(res["macro_stance"], ["ACTIVE_BUY", "SELECTIVE_BUY", "DEFENSE_HOLD", "CASH_EXIT"])

        # High crisis gauges + full defense NLP transcript -> CASH_EXIT
        crisis_gauges = {
            "us10y": {"val": 4.65},  # +25 pt
            "vix": {"val": 28.0},    # +15 pt
            "wti": {"val": 88.0},    # +10 pt
            "dxy": {"val": 106.0}    # +10 pt (M_hard = 60 pt)
        }
        big_transcript = "이번 주는 사지 말자 현금 확보 쉬어가자 전쟁 유가 급등 리스크 관리 관망 " * 200
        res_crisis = evaluate_macro_stance(gauges=crisis_gauges, transcript=big_transcript, title="긴급 위기 경고")
        self.assertEqual(res_crisis["macro_stance"], "CASH_EXIT")
        self.assertGreaterEqual(res_crisis["msi_score"], 75.0)

    def test_detect_cloud_trampoline_bounce_edge_cases(self):
        """Test 1.6: detect_cloud_trampoline_bounce with various boundary distances."""
        print("[STRESS 1.6] Testing Cloud Trampoline Bounce Boundary Distances...")
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=30)
        
        # Valid bounce: Low touches cloud top within -3.5% ~ +6.0%, Close holds >= -1.5%
        df_bounce = pd.DataFrame({
            "Open": [100.0] * 30,
            "High": [105.0] * 30,
            "Low": [98.0] * 30,       # (98 - 100) / 100 = -2.0% (within -3.5% ~ +6.0%)
            "Close": [99.0] * 30,     # (99 - 100) / 100 = -1.0% (>= -1.5%)
            "SpanA": [100.0] * 30,
            "SpanB": [95.0] * 30,
            "Volume": [1000] * 30
        }, index=dates)
        detected, days_ago, touch_gap, close_gap = detect_cloud_trampoline_bounce(df_bounce, max_lookback=14)
        self.assertTrue(detected)
        self.assertEqual(touch_gap, -2.0)
        self.assertEqual(close_gap, -1.0)


class StressTest2CQRSConcurrencyZeroMutation(unittest.TestCase):
    """
    Stress Test 2: Concurrency & CQRS Zero-Mutation Hardening for generate_dashboard_feed.
    """

    def setUp(self):
        self.test_db = os.path.join(PROJECT_ROOT, "test_cqrs_stress.db")
        os.environ["AL_SANGMOO_DB_PATH"] = self.test_db
        if os.path.exists(self.test_db):
            try:
                os.remove(self.test_db)
            except Exception:
                pass
        persistence.init_database()
        
        # Populate initial test records
        with persistence.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO my_portfolio (ticker, buy_date, buy_price, quantity, total_cost, status)
            VALUES ('NVDA', '2026-08-01', 120.0, 10, 1200.0, 'HOLDING'),
                   ('AAPL', '2026-08-05', 220.0, 5, 1100.0, 'HOLDING')
            """)
            cursor.execute("""
            INSERT INTO recommendation_matrix (date, bull_1, bull_1_price, bull_2, bull_2_price, created_at)
            VALUES ('2026-08-20', 'NVDA', 125.0, 'AAPL', 225.0, '2026-08-20 09:00:00')
            """)
            cursor.execute("""
            INSERT INTO trades (date, ticker, type, entry_price, target_price, partial_tp_price, stop_loss_price, status)
            VALUES ('2026-08-20', 'NVDA', 'BULL', 125.0, 143.75, 135.0, 120.0, 'OPEN')
            """)
            cursor.execute("""
            INSERT INTO macro_history (date, vix_val, us10y_val, wti_val, macro_stance, msi_score, created_at)
            VALUES ('2026-08-20', 15.0, 4.2, 75.0, 'SELECTIVE_BUY', 45.0, '2026-08-20 09:00:00')
            """)
            conn.commit()

    def tearDown(self):
        if os.path.exists(self.test_db):
            try:
                os.remove(self.test_db)
            except Exception:
                pass

    def _get_db_snapshot(self) -> Dict[str, List[Dict[str, Any]]]:
        snapshot = {}
        with persistence.get_connection() as conn:
            for table in ["my_portfolio", "recommendation_matrix", "trades", "macro_history"]:
                df = pd.read_sql_query(f"SELECT * FROM {table} ORDER BY id ASC", conn)
                snapshot[table] = df.to_dict(orient="records")
        return snapshot

    @patch("yfinance.download")
    def test_concurrent_build_dashboard_data_zero_mutation(self, mock_yf):
        """Test 2.1: 10 concurrent threads invoking build_dashboard_data() -> 0 SQL mutations."""
        print("\n[STRESS 2.1] Executing 10 Concurrent Threads of build_dashboard_data()...")
        # Mock yf.download to return realistic synthetic DataFrame fast
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=100)
        df_mock = pd.DataFrame({
            "Open": [100.0] * 100,
            "High": [105.0] * 100,
            "Low": [95.0] * 100,
            "Close": [102.0] * 100,
            "Volume": [1_000_000] * 100
        }, index=dates)
        mock_yf.return_value = df_mock

        initial_snapshot = self._get_db_snapshot()
        initial_counts = {t: len(rows) for t, rows in initial_snapshot.items()}

        errors = []
        payloads = []

        def worker_task(thread_id):
            try:
                res = generate_dashboard_feed.build_dashboard_data()
                return res
            except Exception as e:
                errors.append((thread_id, str(e)))
                return None

        # Run 10 concurrent workers
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker_task, i) for i in range(10)]
            for fut in as_completed(futures):
                res = fut.result()
                if res is not None:
                    payloads.append(res)

        self.assertEqual(len(errors), 0, f"Concurrent workers failed with errors: {errors}")
        self.assertEqual(len(payloads), 10, "Not all 10 workers produced payloads")

        # Verify DB Snapshot after concurrent executions
        final_snapshot = self._get_db_snapshot()
        final_counts = {t: len(rows) for t, rows in final_snapshot.items()}

        for table in initial_counts:
            self.assertEqual(
                initial_counts[table],
                final_counts[table],
                f"Row count mutation detected on table '{table}': Initial={initial_counts[table]}, Final={final_counts[table]}"
            )
            self.assertEqual(
                initial_snapshot[table],
                final_snapshot[table],
                f"Row content mutation detected on table '{table}'!"
            )

        # Verify output JSON payload is valid and readable
        out_json_path = os.path.join(PROJECT_ROOT, "dashboard_data.json")
        self.assertTrue(os.path.exists(out_json_path))
        with open(out_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertIn("macro", data)
            self.assertIn("dual_consensus", data)
            self.assertIn("signal_tracker", data)


class StressTest3ScoringEquivalenceBoundaryPrecision(unittest.TestCase):
    """
    Stress Test 3: Precision Float Boundary Conditions for 3-Tier Classification & Scoring.
    """

    def test_bull_score_kijun_gap_boundaries(self):
        """Test 3.1: Kijun-sen Gap exact float boundaries (with documented IEEE-754 precision awareness)."""
        print("\n[STRESS 3.1] Testing Kijun Gap Graduated Sweet-Spot Float Boundaries...")
        # Step: +35 pt for -0.5 <= kgap <= +3.5
        # Test clear interior points
        ind_in_35a = QuantIndicators.from_values(close=102.0, kijun=100.0, tenkan=100.0, span_a=100.0, span_b=100.0, vol_ratio=1.0)
        ind_in_35b = QuantIndicators.from_values(close=99.8, kijun=100.0, tenkan=100.0, span_a=100.0, span_b=100.0, vol_ratio=1.0)
        _, bd_35a = calculate_canonical_bull_score(ind_in_35a)
        _, bd_35b = calculate_canonical_bull_score(ind_in_35b)
        self.assertEqual(bd_35a["kijun_pts"], 35)
        self.assertEqual(bd_35b["kijun_pts"], 35)

        # Test outer boundaries
        ind_out_25a = QuantIndicators.from_values(close=104.0, kijun=100.0, tenkan=100.0, span_a=100.0, span_b=100.0, vol_ratio=1.0)
        ind_out_25b = QuantIndicators.from_values(close=99.3, kijun=100.0, tenkan=100.0, span_a=100.0, span_b=100.0, vol_ratio=1.0)
        _, bd_25a = calculate_canonical_bull_score(ind_out_25a)
        _, bd_25b = calculate_canonical_bull_score(ind_out_25b)
        self.assertEqual(bd_25a["kijun_pts"], 25)
        self.assertEqual(bd_25b["kijun_pts"], 25)

        # Test collapse edge (+6.0 -> 15 pt vs +7.5 -> 0 pt)
        ind_edge_15 = QuantIndicators.from_values(close=106.0, kijun=100.0, tenkan=100.0, span_a=100.0, span_b=100.0, vol_ratio=1.0)
        ind_edge_0 = QuantIndicators.from_values(close=107.5, kijun=100.0, tenkan=100.0, span_a=100.0, span_b=100.0, vol_ratio=1.0)
        _, bd_15 = calculate_canonical_bull_score(ind_edge_15)
        _, bd_0 = calculate_canonical_bull_score(ind_edge_0)
        self.assertEqual(bd_15["kijun_pts"], 15)
        self.assertEqual(bd_0["kijun_pts"], 0)

    def test_volume_dry_up_ratio_boundaries(self):
        """Test 3.2: Volume Dry-Up (VDU) exact float boundaries (0.60, 0.85, 1.10)."""
        print("[STRESS 3.2] Testing Volume Dry-Up Ratio Graduated Boundaries...")
        # 0.60: 20 pt vs 0.6001: 15 pt
        ind_vdu_20 = QuantIndicators.from_values(close=100.0, kijun=100.0, tenkan=100.0, span_a=100.0, span_b=100.0, vol_ratio=0.60)
        ind_vdu_15 = QuantIndicators.from_values(close=100.0, kijun=100.0, tenkan=100.0, span_a=100.0, span_b=100.0, vol_ratio=0.6001)
        _, bd_v20 = calculate_canonical_bull_score(ind_vdu_20)
        _, bd_v15 = calculate_canonical_bull_score(ind_vdu_15)
        self.assertEqual(bd_v20["vdu_pts"], 20)
        self.assertEqual(bd_v15["vdu_pts"], 15)

        # 0.85: 15 pt vs 0.8501: 10 pt
        ind_vdu_85 = QuantIndicators.from_values(close=100.0, kijun=100.0, tenkan=100.0, span_a=100.0, span_b=100.0, vol_ratio=0.85)
        ind_vdu_10 = QuantIndicators.from_values(close=100.0, kijun=100.0, tenkan=100.0, span_a=100.0, span_b=100.0, vol_ratio=0.8501)
        _, bd_v85 = calculate_canonical_bull_score(ind_vdu_85)
        _, bd_v10 = calculate_canonical_bull_score(ind_vdu_10)
        self.assertEqual(bd_v85["vdu_pts"], 15)
        self.assertEqual(bd_v10["vdu_pts"], 10)

        # 1.10: 10 pt vs 1.1001: 0 pt
        ind_vdu_110 = QuantIndicators.from_values(close=100.0, kijun=100.0, tenkan=100.0, span_a=100.0, span_b=100.0, vol_ratio=1.10)
        ind_vdu_0 = QuantIndicators.from_values(close=100.0, kijun=100.0, tenkan=100.0, span_a=100.0, span_b=100.0, vol_ratio=1.1001)
        _, bd_v110 = calculate_canonical_bull_score(ind_vdu_110)
        _, bd_v0 = calculate_canonical_bull_score(ind_vdu_0)
        self.assertEqual(bd_v110["vdu_pts"], 10)
        self.assertEqual(bd_v0["vdu_pts"], 0)

    def test_classify_3tier_candidates_deterministic_tie_breaking(self):
        """Test 3.3: Deterministic tie-breaking and cross-tier deduplication."""
        print("[STRESS 3.3] Testing 3-Tier Classification Deterministic Tie-Breaking & Deduplication...")
        chart_data = {
            # Candidate A: Tier 1 qualifier (high flow_score=100, score=80, kgap=+1.0%, vol_ratio=0.5)
            "TICK_A": {
                "latest_close": 101.0, "latest_kijun": 100.0, "latest_tenkan": 100.0,
                "span_a": 100.0, "span_b": 95.0, "latest_vol_ratio": 0.5,
                "flow_score": 100, "flow_ratio": 2.0, "flow_label": "2.0x",
                "obv_status": "STEALTH_ACCUM", "is_stealth_accum": True,
                "is_weekly_bull": True
            },
            # Candidate B: Tier 1 qualifier (flow_score=85, score=80, kgap=+0.5%, vol_ratio=0.5)
            "TICK_B": {
                "latest_close": 100.5, "latest_kijun": 100.0, "latest_tenkan": 100.0,
                "span_a": 100.0, "span_b": 95.0, "latest_vol_ratio": 0.5,
                "flow_score": 85, "flow_ratio": 1.5, "flow_label": "1.5x",
                "obv_status": "STEALTH_ACCUM", "is_stealth_accum": True,
                "is_weekly_bull": True
            },
            # Candidate C: Tier 2 structural pullback (score=80, kgap=+1.0%, vol_ratio=0.7)
            "TICK_C": {
                "latest_close": 101.0, "latest_kijun": 100.0, "latest_tenkan": 100.0,
                "span_a": 100.0, "span_b": 95.0, "latest_vol_ratio": 0.7,
                "flow_score": 50, "flow_ratio": 1.0, "flow_label": "1.0x",
                "obv_status": "NEUTRAL", "is_stealth_accum": False,
                "is_weekly_bull": True
            },
            # Candidate D: Tier 3 sniper (trampoline detected, sniper_score=100)
            "TICK_D": {
                "latest_close": 105.0, "latest_kijun": 100.0, "latest_tenkan": 102.0,
                "span_a": 100.0, "span_b": 95.0, "latest_vol_ratio": 1.0,
                "trampoline_detected": True, "trampoline_days_ago": 3,
                "touch_gap_pct": 1.0, "close_gap_pct": 2.0,
                "flow_score": 50, "flow_ratio": 1.0, "is_weekly_bull": True
            }
        }

        t1, t2, t3 = classify_3tier_candidates(
            chart_data=chart_data,
            tailwind_sectors=["AI_INFRA", "SEMICONDUCTOR"],
            stream_mentioned_tickers={"TICK_A"}
        )

        # TICK_A and TICK_B qualify for Tier 1
        t1_tickers = [x["ticker"] for x in t1]
        self.assertIn("TICK_A", t1_tickers)
        self.assertIn("TICK_B", t1_tickers)
        # TICK_A has higher flow_score (100 > 85), must be first
        self.assertEqual(t1[0]["ticker"], "TICK_A")
        self.assertEqual(t1[1]["ticker"], "TICK_B")

        # TICK_C qualifies for Tier 2
        t2_tickers = [x["ticker"] for x in t2]
        self.assertIn("TICK_C", t2_tickers)

        # TICK_D qualifies for Tier 3
        t3_tickers = [x["ticker"] for x in t3]
        self.assertIn("TICK_D", t3_tickers)

        # Deduplication: No ticker in Tier 1 should appear in Tier 2 or Tier 3
        all_t1 = set(t1_tickers)
        for tk in t2_tickers:
            self.assertNotIn(tk, all_t1)
        for tk in t3_tickers:
            self.assertNotIn(tk, all_t1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

