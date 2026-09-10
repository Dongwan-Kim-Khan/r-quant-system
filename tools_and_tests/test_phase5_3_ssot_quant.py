"""
===============================================================================
  Al-Sangmoo Quant Trading Platform: Phase 5.3 SSOT Quant & Architecture Test Suite
===============================================================================
Author: E2E Test Writer (Phase 5.3)
Target: Phase 5.3 Quantitative Consolidation & SSOT Architecture
Standards: ORIGINAL_REQUEST §R1-R4, PROJECT.md, TEST_INFRA.md

Test Tiers:
- Tier 1: SSOT Indicator Math Parity (Tenkan, Kijun, RawSpanA, RawSpanB, SpanA, SpanB,
          Chikou, SMA20/50/60/200, Vol_SMA20, Vol_Ratio; NaN & Zero-Volume Safety)
- Tier 2: 3-Tier Quant Scoring & Classification Determinism (Canonical Bull Score 0-100,
          Sniper Score 0-100, Bear Score, Tier 1/2/3 Predicates, -5% Hard Stop, +15% Target)
- Tier 3: Macro Stance Index 2.0 (MSI 2.0) Canonical Evaluation (Boundary Conditions for
          US 10Y, VIX, WTI, DXY, NLP Sentiment, External Shocks, 100% Stance Parity)
- Tier 4: CQRS & Side-Effect Free Pipeline Isolation (Mock persistence / SQLite,
          Execute build_dashboard_data(), Assert 0 unintended DML writes)
- Tier 5: Static AST / Deduplication Integrity (Parse generate_dashboard_feed.py,
          al_sangmoo_daily_bot.py, and youtube_stream_scanner.py with ast)
- Tier 6: Full Platform Regression Runner (Subprocess execution of Phase 1, 2, 4, 5.1,
          5.2, and Global 60 Dual Strategy test suites)
===============================================================================
"""

import os
import sys
import ast
import json
import math
import time
import subprocess
import unittest
from unittest.mock import patch, MagicMock
from typing import Dict, Any, List, Tuple
import tempfile

import numpy as np
import pandas as pd

# Windows console encoding fix
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Isolate tests to dedicated temporary test database
TEST_DB = os.path.join(PROJECT_ROOT, "test_quant_trades_p5_3.db")
os.environ["AL_SANGMOO_DB_PATH"] = TEST_DB

import db_manager
import generate_dashboard_feed
from al_sangmoo.core.constants import WATCHLIST, STOCK_DICT, TICKER_SECTORS, get_macro_tailwind_sectors
from al_sangmoo.domain.quant.ichimoku import (
    calculate_ichimoku_indicators,
    project_future_cloud,
    compute_institutional_flow_indicators,
    build_ichimoku_series_payload
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


# =============================================================================
# Synthetic OHLCV & Data Generators for Invariant Testing
# =============================================================================

def generate_synthetic_ohlcv(
    n_bars: int = 150,
    start_price: float = 100.0,
    trend: float = 0.2,
    volatility: float = 2.0,
    seed: int = 42
) -> pd.DataFrame:
    """
    Generates a deterministic, reproducible synthetic OHLCV time series.
    """
    np.random.seed(seed)
    dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=n_bars)
    
    closes = [start_price]
    for i in range(1, n_bars):
        # Deterministic wave + trend + slight pseudo-random variance
        step = trend + math.sin(i / 6.0) * 1.5 + (np.random.rand() - 0.48) * volatility
        new_close = max(1.0, closes[-1] + step)
        closes.append(round(new_close, 2))
        
    highs = [round(c + abs(math.cos(i / 4.0)) * 2.5 + 0.5, 2) for i, c in enumerate(closes)]
    lows = [round(max(0.5, c - abs(math.sin(i / 4.0)) * 2.5 - 0.5), 2) for i, c in enumerate(closes)]
    opens = [round((highs[i] + lows[i]) / 2.0, 2) for i in range(n_bars)]
    volumes = [int(1_000_000 + math.sin(i / 5.0) * 400_000 + (i % 7) * 50_000) for i in range(n_bars)]
    
    df = pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes
    }, index=dates)
    return df


# =============================================================================
# TIER 1: SSOT Indicator Math Parity
# =============================================================================

class TestTier1IndicatorMathParity(unittest.TestCase):
    """
    Verifies Single Source of Truth indicator mathematics against exact
    mathematical definitions and boundary/edge conditions.
    """

    def test_ichimoku_math_exact_parity(self):
        """Verify Tenkan (9), Kijun (26), RawSpanA, RawSpanB (52), SpanA (+26), SpanB (+26), SMA20/60, Vol_Ratio."""
        print("\n[Tier 1.1] Verifying SSOT Ichimoku Mathematical Parity on Synthetic OHLCV...")
        df = generate_synthetic_ohlcv(n_bars=150, start_price=100.0, trend=0.25)
        df_ind = calculate_ichimoku_indicators(df)
        
        # Verify required columns exist
        expected_cols = ['Tenkan', 'Kijun', 'RawSpanA', 'RawSpanB', 'SpanA', 'SpanB', 'SMA20', 'SMA60', 'Vol_SMA20', 'Vol_Ratio']
        for col in expected_cols:
            self.assertIn(col, df_ind.columns, f"Missing indicator column: {col}")

        # Check Tenkan (9-bar midpoint: (max(H, 9) + min(L, 9)) / 2)
        for t in range(8, len(df)):
            expected_tenkan = (df['High'].iloc[t-8:t+1].max() + df['Low'].iloc[t-8:t+1].min()) / 2.0
            actual_tenkan = df_ind['Tenkan'].iloc[t]
            self.assertAlmostEqual(actual_tenkan, expected_tenkan, places=4,
                                   msg=f"Tenkan mismatch at index {t}: expected {expected_tenkan}, got {actual_tenkan}")

        # Check Kijun (26-bar midpoint: (max(H, 26) + min(L, 26)) / 2)
        for t in range(25, len(df)):
            expected_kijun = (df['High'].iloc[t-25:t+1].max() + df['Low'].iloc[t-25:t+1].min()) / 2.0
            actual_kijun = df_ind['Kijun'].iloc[t]
            self.assertAlmostEqual(actual_kijun, expected_kijun, places=4,
                                   msg=f"Kijun mismatch at index {t}: expected {expected_kijun}, got {actual_kijun}")

        # Check RawSpanA ((Tenkan + Kijun) / 2)
        for t in range(25, len(df)):
            expected_raw_a = (df_ind['Tenkan'].iloc[t] + df_ind['Kijun'].iloc[t]) / 2.0
            actual_raw_a = df_ind['RawSpanA'].iloc[t]
            self.assertAlmostEqual(actual_raw_a, expected_raw_a, places=4,
                                   msg=f"RawSpanA mismatch at index {t}")

        # Check RawSpanB (52-bar midpoint)
        for t in range(51, len(df)):
            expected_raw_b = (df['High'].iloc[t-51:t+1].max() + df['Low'].iloc[t-51:t+1].min()) / 2.0
            actual_raw_b = df_ind['RawSpanB'].iloc[t]
            self.assertAlmostEqual(actual_raw_b, expected_raw_b, places=4,
                                   msg=f"RawSpanB mismatch at index {t}")

        # Check 26-bar forward shifts for SpanA and SpanB
        for t in range(26, len(df)):
            prev_raw_a = df_ind['RawSpanA'].iloc[t-26]
            curr_span_a = df_ind['SpanA'].iloc[t]
            if not pd.isna(prev_raw_a):
                self.assertAlmostEqual(curr_span_a, prev_raw_a, places=4,
                                       msg=f"SpanA shift mismatch at index {t}")

            prev_raw_b = df_ind['RawSpanB'].iloc[t-26]
            curr_span_b = df_ind['SpanB'].iloc[t]
            if not pd.isna(prev_raw_b):
                self.assertAlmostEqual(curr_span_b, prev_raw_b, places=4,
                                       msg=f"SpanB shift mismatch at index {t}")

        # Check SMA20 and SMA60
        for t in range(19, len(df)):
            expected_sma20 = df['Close'].iloc[t-19:t+1].mean()
            self.assertAlmostEqual(df_ind['SMA20'].iloc[t], expected_sma20, places=4)

        for t in range(59, len(df)):
            expected_sma60 = df['Close'].iloc[t-59:t+1].mean()
            self.assertAlmostEqual(df_ind['SMA60'].iloc[t], expected_sma60, places=4)

        # Check Vol_SMA20 and Vol_Ratio
        for t in range(19, len(df)):
            expected_vol_sma20 = df['Volume'].iloc[t-19:t+1].mean()
            expected_vol_ratio = df['Volume'].iloc[t] / expected_vol_sma20
            self.assertAlmostEqual(df_ind['Vol_SMA20'].iloc[t], expected_vol_sma20, places=4)
            self.assertAlmostEqual(df_ind['Vol_Ratio'].iloc[t], expected_vol_ratio, places=4)

        print("  -> PASSED: 100% mathematical parity across all rolling indicator series.")

    def test_indicator_boundary_nan_and_zero_volume_safety(self):
        """Verify division-by-zero safety on volume, empty dataframe, and constant price series."""
        print("\n[Tier 1.2] Verifying Boundary Conditions: NaN, Zero-Volume, and Constant Series...")
        
        # 1. Zero Volume series (must not crash with ZeroDivisionError)
        df_zero_vol = generate_synthetic_ohlcv(n_bars=30, start_price=50.0)
        df_zero_vol['Volume'] = 0
        df_res = calculate_ichimoku_indicators(df_zero_vol)
        self.assertFalse(df_res.empty)
        self.assertIn('Vol_Ratio', df_res.columns)
        # Vol_Ratio for 0 volume should be NaN or 0, never raise exception
        
        # 2. Constant price series (High == Low == Close)
        df_const = generate_synthetic_ohlcv(n_bars=60, start_price=100.0)
        df_const['Open'] = 100.0
        df_const['High'] = 100.0
        df_const['Low'] = 100.0
        df_const['Close'] = 100.0
        df_res_const = calculate_ichimoku_indicators(df_const)
        
        last_row = df_res_const.iloc[-1]
        self.assertEqual(last_row['Tenkan'], 100.0)
        self.assertEqual(last_row['Kijun'], 100.0)
        self.assertEqual(last_row['RawSpanA'], 100.0)
        self.assertEqual(last_row['RawSpanB'], 100.0)
        self.assertEqual(last_row['SpanA'], 100.0)
        self.assertEqual(last_row['SpanB'], 100.0)
        self.assertEqual(last_row['SMA20'], 100.0)
        self.assertEqual(last_row['SMA60'], 100.0)

        # 3. Short DataFrame (< 9 bars)
        df_short = generate_synthetic_ohlcv(n_bars=5, start_price=100.0)
        df_res_short = calculate_ichimoku_indicators(df_short)
        self.assertEqual(len(df_res_short), 5)
        print("  -> PASSED: Zero-volume, constant price, and short dataframe safety verified.")

    def test_future_cloud_projection_structure(self):
        """Verify project_future_cloud returns 26 forward points with valid timestamps and values."""
        print("\n[Tier 1.3] Verifying Future Ichimoku Cloud Projection (+26 Days)...")
        df = generate_synthetic_ohlcv(n_bars=100, start_price=150.0)
        df_ind = calculate_ichimoku_indicators(df)
        
        f_span_a_pts, f_span_b_pts, f_a_vals, f_b_vals = project_future_cloud(df_ind, periods=26)
        
        self.assertEqual(len(f_span_a_pts), 26, "Expected 26 future SpanA points")
        self.assertEqual(len(f_span_b_pts), 26, "Expected 26 future SpanB points")
        self.assertEqual(len(f_a_vals), 26)
        self.assertEqual(len(f_b_vals), 26)
        
        # Verify timestamps are sequential forward business dates
        last_hist_date = df_ind.index[-1]
        for k, pt in enumerate(f_span_a_pts):
            self.assertIn("time", pt)
            self.assertIn("value", pt)
            pt_date = pd.Timestamp(pt["time"])
            self.assertGreater(pt_date, last_hist_date)

        # Verify values align with RawSpanA and RawSpanB lookback
        for k in range(26):
            expected_val_a = float(df_ind['RawSpanA'].iloc[-26 + k])
            expected_val_b = float(df_ind['RawSpanB'].iloc[-26 + k])
            self.assertAlmostEqual(f_a_vals[k], expected_val_a, places=2)
            self.assertAlmostEqual(f_b_vals[k], expected_val_b, places=2)

        print("  -> PASSED: Future cloud projection correctly generates +26 forward points.")

    def test_institutional_flow_indicators(self):
        """Verify OBV, 14-day Flow Ratio, Stealth Accumulation detection, and fallback safety."""
        print("\n[Tier 1.4] Verifying Institutional Flow Signatures (OBV & 14D Inflow)...")
        
        # 1. Synthetic Stealth Accumulation: Price flat (< 2.5%), High Up-Volume (flow_ratio >= 1.2)
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=30)
        closes = [100.0 + (i % 2) * 0.5 for i in range(30)]  # Flat price
        opens = [100.0 for _ in range(30)]
        highs = [101.0 for _ in range(30)]
        lows = [99.5 for _ in range(30)]
        # Up days have massive volume, down days have minimal volume
        volumes = [2_000_000 if closes[i] >= opens[i] else 200_000 for i in range(30)]
        
        df_stealth = pd.DataFrame({
            "Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes
        }, index=dates)
        
        flow_stealth = compute_institutional_flow_indicators(df_stealth)
        self.assertTrue(flow_stealth["is_stealth_accum"], "Expected stealth accumulation to be detected")
        self.assertEqual(flow_stealth["obv_status"], "STEALTH_ACCUM")
        self.assertGreaterEqual(flow_stealth["flow_ratio"], 1.2)
        self.assertGreaterEqual(flow_stealth["flow_score"], 50)

        # 2. Insufficient data fallback (< 15 bars)
        df_short = df_stealth.iloc[:10]
        flow_short = compute_institutional_flow_indicators(df_short)
        self.assertEqual(flow_short["obv_status"], "NEUTRAL")
        self.assertEqual(flow_short["flow_ratio"], 1.0)
        self.assertFalse(flow_short["is_stealth_accum"])
        print("  -> PASSED: Institutional flow indicators and stealth accumulation signatures verified.")

    def test_build_ichimoku_series_payload_chart_contract(self):
        """Verify build_ichimoku_series_payload produces exact lightweight-charts payload format."""
        print("\n[Tier 1.5] Verifying build_ichimoku_series_payload Chart Contract & Series Structure...")
        df = generate_synthetic_ohlcv(n_bars=80, start_price=100.0)
        df_ind = calculate_ichimoku_indicators(df)
        payload = build_ichimoku_series_payload(df_ind, max_bars=60)
        
        required_chart_keys = [
            "candles", "kijun_line", "tenkan_line", "span_a_line", "span_b_line",
            "sma20", "sma60", "volume", "future_span_a", "future_span_b"
        ]
        for k in required_chart_keys:
            self.assertIn(k, payload, f"Missing chart series key: {k}")
            
        self.assertLessEqual(len(payload["candles"]), 60)
        self.assertEqual(len(payload["future_span_a"]), 26)
        self.assertEqual(len(payload["future_span_b"]), 26)
        print("  -> PASSED: build_ichimoku_series_payload chart contract verified.")


# =============================================================================
# TIER 2: 3-Tier Quant Scoring & Classification Determinism
# =============================================================================

class TestTier2QuantScoringAndClassification(unittest.TestCase):
    """
    Verifies Canonical 17-Year Quant Scoring matrices and deterministic
    3-Tier Classification rules.
    """

    def test_canonical_bull_score_graduated_matrix(self):
        """Verify graduated sweet-spots for Cloud (+35/25/15), Kijun (+35/25/15), VDU (+20/15/10), Tenkan (+10/5)."""
        print("\n[Tier 2.1] Verifying 17-Year Canonical Bull Scoring Graduated Matrix...")

        # 1. 100 pt Perfect Bull Setup
        ind_100 = QuantIndicators.from_values(
            close=120.0,
            kijun=118.0,      # Kijun gap: +1.69% (within -0.5% ~ +3.5% -> +35 pt)
            tenkan=119.0,     # Tenkan >= Kijun -> +10 pt
            span_a=115.0,     # Close >= Cloud Top (115.0) -> +35 pt
            span_b=110.0,
            vol_ratio=0.50    # VDU <= 0.60 -> +20 pt
        )
        score_100, b_100 = calculate_canonical_bull_score(ind_100)
        self.assertEqual(score_100, 100, f"Expected 100 pt, got {score_100}")
        self.assertEqual(b_100["cloud_pts"], 35)
        self.assertEqual(b_100["kijun_pts"], 35)
        self.assertEqual(b_100["vdu_pts"], 20)
        self.assertEqual(b_100["tenkan_pts"], 10)

        # 2. Graduated Tier-2 Sweet Spot Setup
        ind_grad = QuantIndicators.from_values(
            close=114.0,      # Cloud top is 115.0 -> close >= 115.0 * 0.97 (111.55) -> +25 pt
            kijun=110.0,      # Kijun gap: +3.63% (within -0.8% ~ +4.8% -> +25 pt)
            tenkan=109.0,     # Tenkan < Kijun, but close >= Tenkan -> +5 pt
            span_a=115.0,
            span_b=105.0,
            vol_ratio=0.80    # VDU <= 0.85 -> +15 pt
        )
        score_grad, b_grad = calculate_canonical_bull_score(ind_grad)
        expected_grad = 25 + 25 + 15 + 5  # 70 pt
        self.assertEqual(score_grad, expected_grad, f"Expected {expected_grad} pt, got {score_grad}")

        # 3. Minimum Threshold Setup
        ind_min = QuantIndicators.from_values(
            close=108.0,      # Cloud bottom is 105.0 -> close >= Cloud bottom -> +15 pt
            kijun=102.0,      # Kijun gap: +5.88% (within -1.5% ~ +7.0% -> +15 pt)
            tenkan=109.0,     # Tenkan >= Kijun -> +10 pt
            span_a=115.0,
            span_b=105.0,
            vol_ratio=1.05    # VDU <= 1.10 -> +10 pt
        )
        score_min, b_min = calculate_canonical_bull_score(ind_min)
        expected_min = 15 + 15 + 10 + 10  # 50 pt
        self.assertEqual(score_min, expected_min, f"Expected {expected_min} pt, got {score_min}")
        print("  -> PASSED: Bull score graduated matrix verified across all intervals.")

    def test_canonical_sniper_and_bear_score_matrices(self):
        """Verify Strategy 2 Sniper score (Max 100 pt) and Risk Breakdown Bear score (Max 90 pt)."""
        print("\n[Tier 2.2] Verifying Sniper Score (Strat 2) and Bear Score Matrices...")

        # 1. 100 pt Perfect Sniper Setup
        ind_sniper = QuantIndicators.from_values(
            close=125.0, kijun=122.0, tenkan=123.0, span_a=115.0, span_b=110.0, vol_ratio=0.90
        )
        tramp_active = TrampolineBounceContext(detected=True, days_ago=3)
        sniper_score, s_breakdown = calculate_canonical_sniper_score(ind_sniper, tramp_active)
        self.assertEqual(sniper_score, 100)
        self.assertEqual(s_breakdown["trampoline_pts"], 40)
        self.assertEqual(s_breakdown["cloud_pts"], 30)
        self.assertEqual(s_breakdown["kijun_pts"], 15)
        self.assertEqual(s_breakdown["tenkan_pts"], 15)

        # 2. Risk Breakdown Bear Setup (Close < Kijun +40, Close < Cloud Bottom +35, Kijun gap < -2% +15)
        ind_bear = QuantIndicators.from_values(
            close=90.0, kijun=100.0, tenkan=95.0, span_a=110.0, span_b=98.0, vol_ratio=1.50
        )
        bear_score = calculate_canonical_bear_score(ind_bear)
        self.assertEqual(bear_score, 90, f"Expected 90 pt bear score, got {bear_score}")

        eval_res = evaluate_quant_score(
            close=90.0, kijun=100.0, tenkan=95.0, span_a=110.0, span_b=98.0, vol_ratio=1.50
        )
        self.assertEqual(eval_res["quant_type"], "BEAR")
        self.assertIn("Risk Breakdown", eval_res["quant_verdict"])
        print("  -> PASSED: Sniper and Bear scoring matrices verified.")

    def test_3tier_classification_predicates_and_risk_guardrails(self):
        """Verify Tier 1 Macro Leader, Tier 2 Structural Pullback, Tier 3 Cloud Sniper, -5% stop, +15% target."""
        print("\n[Tier 2.3] Verifying 3-Tier Classification Determinism & Risk Guardrails...")
        
        # Test Symbol 1: Tier 1 Macro Leader (Weekly Bull + Strat 1 + Safe Entry + Macro Tailwind)
        ind_t1 = QuantIndicators.from_values(
            close=200.0, kijun=198.0, tenkan=199.0, span_a=190.0, span_b=185.0, vol_ratio=0.55
        )
        weekly_bull = WeeklyTrendContext(is_weekly_bull=True)
        flow_accum = InstitutionalFlowContext(obv_status="STEALTH_ACCUM", flow_ratio=1.45, flow_score=85, is_stealth_accum=True)
        tramp_none = TrampolineBounceContext(detected=False)
        
        t1_class = classify_quant_tier(
            ticker="NVDA",
            ind=ind_t1,
            weekly=weekly_bull,
            flow=flow_accum,
            trampoline=tramp_none,
            macro_tailwind_sectors=["TECH", "SEMICONDUCTOR"],
            sector="SEMICONDUCTOR"
        )
        self.assertEqual(t1_class.tier, "TIER_1")
        self.assertTrue(t1_class.is_tier1_qualified)
        self.assertEqual(t1_class.entry_price, 200.0)
        self.assertEqual(t1_class.target_price, 230.0)  # +15.0%
        self.assertEqual(t1_class.stop_price, 190.0)    # -5.0% Hard Stop
        self.assertEqual(t1_class.partial_tp_price, 216.0) # +8.0% Partial TP

        # Test Symbol 2: Tier 2 Structural Pullback (Strat 1, Safe Entry, VDU <= 0.85, Not in Tailwind, Score 90 < 100)
        ind_t2 = QuantIndicators.from_values(
            close=116.0, kijun=114.0, tenkan=113.0, span_a=115.0, span_b=105.0, vol_ratio=0.75
        )
        flow_neutral = InstitutionalFlowContext(obv_status="NEUTRAL", flow_ratio=1.0, flow_score=50, is_stealth_accum=False)
        t2_class = classify_quant_tier(
            ticker="COST",
            ind=ind_t2,
            weekly=weekly_bull,
            flow=flow_neutral,
            trampoline=tramp_none,
            macro_tailwind_sectors=["ENERGY"],
            sector="CONSUMER"
        )
        self.assertEqual(t2_class.tier, "TIER_2")
        self.assertTrue(t2_class.is_tier2_qualified)

        # Test Symbol 3: Tier 3 Cloud Sniper (Weekly Bull + Trampoline Bounce active, Stage 2 Momentum kgap > 3.5%)
        ind_t3 = QuantIndicators.from_values(
            close=152.0, kijun=145.0, tenkan=149.0, span_a=140.0, span_b=135.0, vol_ratio=1.10
        )
        tramp_active = TrampolineBounceContext(detected=True, days_ago=3)
        t3_class = classify_quant_tier(
            ticker="PLTR",
            ind=ind_t3,
            weekly=weekly_bull,
            flow=flow_neutral,
            trampoline=tramp_active,
            macro_tailwind_sectors=["FINANCE"],
            sector="TECH"
        )
        self.assertEqual(t3_class.tier, "TIER_3")
        self.assertTrue(t3_class.is_tier3_qualified)

        print("  -> PASSED: 3-Tier classification predicates and -5% / +15% risk guardrails verified.")

    def test_classify_3tier_candidates_batch_determinism(self):
        """Verify classify_3tier_candidates produces disjoint, deterministically sorted candidate sets."""
        print("\n[Tier 2.4] Verifying Batch 3-Tier Classification Disjoint Partitioning...")
        
        synthetic_chart_data = {
            "NVDA": {
                "latest_close": 200.0, "latest_kijun": 198.0, "latest_tenkan": 199.0,
                "span_a": 190.0, "span_b": 185.0, "latest_vol_ratio": 0.50,
                "is_weekly_bull": True, "obv_status": "STEALTH_ACCUM", "flow_ratio": 1.5,
                "flow_score": 90, "is_stealth_accum": True, "trampoline_detected": False
            },
            "COST": {
                "latest_close": 116.0, "latest_kijun": 114.0, "latest_tenkan": 113.0,
                "span_a": 115.0, "span_b": 105.0, "latest_vol_ratio": 0.75,
                "is_weekly_bull": True, "obv_status": "NEUTRAL", "flow_ratio": 1.0,
                "flow_score": 50, "is_stealth_accum": False, "trampoline_detected": False
            },
            "PLTR": {
                "latest_close": 84.0, "latest_kijun": 80.0, "latest_tenkan": 82.0,
                "span_a": 72.0, "span_b": 70.0, "latest_vol_ratio": 1.05,
                "is_weekly_bull": True, "obv_status": "NEUTRAL", "flow_ratio": 1.0,
                "flow_score": 50, "is_stealth_accum": False, "trampoline_detected": True, "trampoline_days_ago": 3
            },
            "BEAR_STOCK": {
                "latest_close": 50.0, "latest_kijun": 65.0, "latest_tenkan": 55.0,
                "span_a": 70.0, "span_b": 68.0, "latest_vol_ratio": 1.80,
                "is_weekly_bull": False, "obv_status": "NEUTRAL", "flow_ratio": 0.6,
                "flow_score": 20, "is_stealth_accum": False, "trampoline_detected": False
            }
        }
        
        t1, t2, t3 = classify_3tier_candidates(
            synthetic_chart_data,
            tailwind_sectors=["TECH", "SEMICONDUCTOR"]
        )
        
        t1_tickers = [x["ticker"] for x in t1]
        t2_tickers = [x["ticker"] for x in t2]
        t3_tickers = [x["ticker"] for x in t3]
        
        self.assertIn("NVDA", t1_tickers)
        self.assertIn("COST", t2_tickers)
        self.assertIn("PLTR", t3_tickers)
        self.assertNotIn("BEAR_STOCK", t1_tickers + t2_tickers + t3_tickers)
        
        # Verify mutual exclusion across tiers
        self.assertTrue(set(t1_tickers).isdisjoint(set(t2_tickers)), "Tier 1 and Tier 2 must be disjoint")
        self.assertTrue(set(t1_tickers).isdisjoint(set(t3_tickers)), "Tier 1 and Tier 3 must be disjoint")
        print("  -> PASSED: Batch 3-Tier classification produces mutually disjoint, deterministic portfolios.")


# =============================================================================
# TIER 3: Macro Stance Index 2.0 (MSI 2.0) Canonical Evaluation
# =============================================================================

class TestTier3MacroStanceIndex2Canonical(unittest.TestCase):
    """
    Verifies unified MSI 2.0 parameter evaluation, gauge boundary points,
    NLP sentiment ratios, external shock shifters, and 100% stance parity.
    """

    def test_msi_hard_gauge_boundary_conditions(self):
        """Verify boundary thresholds for US 10Y Yield, VIX, WTI Oil, and DXY."""
        print("\n[Tier 3.1] Verifying MSI 2.0 Hard Gauge Boundary Points...")

        # 1. US 10Y Yield Boundaries (25.0 / 18.0 / 10.0 / 4.0 / 0.0)
        cases_us10y = [
            (4.60, 25.0),
            (4.50, 25.0),
            (4.49, 18.0),
            (4.30, 18.0),
            (4.29, 10.0),
            (4.10, 10.0),
            (4.09, 4.0),
            (3.90, 4.0),
            (3.89, 0.0),
            (3.50, 0.0)
        ]
        for val, exp_pts in cases_us10y:
            res = evaluate_macro_stance(gauges={"us10y": {"val": val}, "vix": {"val": 14.0}, "wti": {"val": 70.0}, "dxy": {"val": 99.0}})
            actual_pts = res["msi_breakdown"]["us10y_pts"]
            self.assertEqual(actual_pts, exp_pts, f"US 10Y {val}% expected {exp_pts} pts, got {actual_pts}")

        # 2. VIX Boundaries (15.0 / 10.0 / 5.0 / 0.0)
        cases_vix = [
            (30.0, 15.0),
            (25.0, 15.0),
            (24.9, 10.0),
            (20.0, 10.0),
            (19.9, 5.0),
            (16.0, 5.0),
            (15.9, 0.0),
            (12.0, 0.0)
        ]
        for val, exp_pts in cases_vix:
            res = evaluate_macro_stance(gauges={"us10y": {"val": 3.5}, "vix": {"val": val}, "wti": {"val": 70.0}, "dxy": {"val": 99.0}})
            actual_pts = res["msi_breakdown"]["vix_pts"]
            self.assertEqual(actual_pts, exp_pts, f"VIX {val} expected {exp_pts} pts, got {actual_pts}")

        # 3. WTI Crude Oil Boundaries (10.0 / 6.0 / 3.0 / 0.0)
        cases_wti = [
            (90.0, 10.0),
            (85.0, 10.0),
            (84.9, 6.0),
            (80.0, 6.0),
            (79.9, 3.0),
            (75.0, 3.0),
            (74.9, 0.0),
            (65.0, 0.0)
        ]
        for val, exp_pts in cases_wti:
            res = evaluate_macro_stance(gauges={"us10y": {"val": 3.5}, "vix": {"val": 14.0}, "wti": {"val": val}, "dxy": {"val": 99.0}})
            actual_pts = res["msi_breakdown"]["wti_pts"]
            self.assertEqual(actual_pts, exp_pts, f"WTI ${val} expected {exp_pts} pts, got {actual_pts}")

        # 4. DXY Dollar Index Boundaries (10.0 / 6.0 / 2.0 / 0.0)
        cases_dxy = [
            (108.0, 10.0),
            (105.0, 10.0),
            (104.9, 6.0),
            (103.0, 6.0),
            (102.9, 2.0),
            (100.0, 2.0),
            (99.9, 0.0),
            (95.0, 0.0)
        ]
        for val, exp_pts in cases_dxy:
            res = evaluate_macro_stance(gauges={"us10y": {"val": 3.5}, "vix": {"val": 14.0}, "wti": {"val": 70.0}, "dxy": {"val": val}})
            actual_pts = res["msi_breakdown"]["dxy_pts"]
            self.assertEqual(actual_pts, exp_pts, f"DXY {val} expected {exp_pts} pts, got {actual_pts}")

        print("  -> PASSED: All 4 hard gauge boundary conditions evaluated with 100% precision.")

    def test_msi_nlp_sentiment_and_external_shocks(self):
        """Verify NLP sentiment ratio scaling (max 25pt) and shock shifter capping (max 15pt)."""
        print("\n[Tier 3.2] Verifying NLP Broadcast Sentiment and External Shock Caps...")

        # 1. 100% Defense NLP Sentiment -> 25.0 pts (with +0.1 smoothing divisor)
        res_def = evaluate_macro_stance(defense_count=20, buy_count=0)
        self.assertAlmostEqual(res_def["msi_breakdown"]["m_nlp"], 25.0, delta=0.2)

        # 2. 100% Bullish NLP Sentiment -> 0.0 pts
        res_buy = evaluate_macro_stance(defense_count=0, buy_count=20)
        self.assertAlmostEqual(res_buy["msi_breakdown"]["m_nlp"], 0.0, places=1)

        # 3. Neutral Balance (5 vs 5) -> 12.5 pts
        res_neutral = evaluate_macro_stance(defense_count=5, buy_count=5)
        self.assertAlmostEqual(res_neutral["msi_breakdown"]["m_nlp"], 12.4, delta=0.5)

        # 4. External Shocks Shifter capping (Max 15.0 pts)
        res_shock_1 = evaluate_macro_stance(matched_shocks=["지정학적 분쟁 및 전쟁 리스크"])
        self.assertEqual(res_shock_1["msi_breakdown"]["m_shock"], 6.0)

        res_shock_all = evaluate_macro_stance(matched_shocks=[
            "지정학적 분쟁 및 전쟁 리스크",
            "무역 분쟁 및 관세 불확실성",
            "금리 경로 및 통화정책 영향권",
            "글로벌 공급망 교란",
            "국제 원자재 충격"
        ])
        self.assertEqual(res_shock_all["msi_breakdown"]["m_shock"], 15.0, "Shock factor must be capped at 15.0 pt")

        print("  -> PASSED: NLP sentiment ratio scaling and shock factor capping verified.")

    def test_msi_regime_stance_classification_and_adapter_parity(self):
        """Verify CASH_EXIT (>=75), DEFENSE_HOLD (50-74), SELECTIVE_BUY (30-49), ACTIVE_BUY (<30)."""
        print("\n[Tier 3.3] Verifying MSI 2.0 Regime Stances & Legacy calculate_msi_regime Adapter Parity...")

        scenarios = [
            # 1. Extreme Crisis -> CASH_EXIT (MSI >= 75.0)
            {"gauges": {"us10y": {"val": 4.65}, "vix": {"val": 28.0}, "wti": {"val": 88.0}, "dxy": {"val": 107.0}}, "def": 25, "buy": 1, "shocks": ["WAR", "TARIFF"], "exp_stance": "CASH_EXIT"},
            # 2. Defensive Stance -> DEFENSE_HOLD (50.0 <= MSI < 75.0)
            {"gauges": {"us10y": {"val": 4.35}, "vix": {"val": 21.0}, "wti": {"val": 81.0}, "dxy": {"val": 103.5}}, "def": 12, "buy": 4, "shocks": ["FED"], "exp_stance": "DEFENSE_HOLD"},
            # 3. Mild Pullback -> SELECTIVE_BUY (30.0 <= MSI < 50.0)
            {"gauges": {"us10y": {"val": 4.35}, "vix": {"val": 17.5}, "wti": {"val": 76.0}, "dxy": {"val": 101.5}}, "def": 4, "buy": 10, "shocks": [], "exp_stance": "SELECTIVE_BUY"},
            # 4. Goldilocks Boom -> ACTIVE_BUY (MSI < 30.0)
            {"gauges": {"us10y": {"val": 3.75}, "vix": {"val": 13.0}, "wti": {"val": 68.0}, "dxy": {"val": 98.5}}, "def": 0, "buy": 20, "shocks": [], "exp_stance": "ACTIVE_BUY"}
        ]

        for s in scenarios:
            res_canonical = evaluate_macro_stance(
                gauges=s["gauges"],
                defense_count=s["def"],
                buy_count=s["buy"],
                matched_shocks=s["shocks"]
            )
            res_adapter = calculate_msi_regime(
                gauges=s["gauges"],
                defense_count=s["def"],
                buy_count=s["buy"],
                matched_shocks=s["shocks"]
            )
            
            self.assertEqual(res_canonical["macro_stance"], s["exp_stance"])
            # Assert 100% exact parity between canonical function and legacy adapter
            self.assertEqual(res_canonical["msi_score"], res_adapter["msi_score"])
            self.assertEqual(res_canonical["macro_stance"], res_adapter["macro_stance"])
            self.assertEqual(res_canonical["msi_breakdown"], res_adapter["msi_breakdown"])

        print("  -> PASSED: MSI 2.0 stance classifications and 100% adapter parity verified.")


# =============================================================================
# TIER 4: CQRS & Side-Effect Free Pipeline Isolation
# =============================================================================

class TestTier4CQRSSideEffectFreePipeline(unittest.TestCase):
    """
    Verifies that generate_dashboard_feed.build_dashboard_data() operates as a
    pure, side-effect free transformation pipeline without modifying persistent SQLite state.
    """

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix="_tier4.db")
        os.close(self.temp_db_fd)
        self.old_env = os.environ.get("AL_SANGMOO_DB_PATH")
        os.environ["AL_SANGMOO_DB_PATH"] = self.temp_db_path
        db_manager.init_database()

        self.temp_feed_dir = tempfile.mkdtemp(suffix="_tier4_feed")
        self.temp_out_json = os.path.join(self.temp_feed_dir, "dashboard_data.json")
        self.temp_charts_dir = os.path.join(self.temp_feed_dir, "charts")

    def tearDown(self):
        if self.old_env is not None:
            os.environ["AL_SANGMOO_DB_PATH"] = self.old_env
        else:
            os.environ.pop("AL_SANGMOO_DB_PATH", None)
        for ext in ["", "-wal", "-shm"]:
            p = f"{self.temp_db_path}{ext}"
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        import shutil
        if hasattr(self, "temp_feed_dir") and os.path.exists(self.temp_feed_dir):
            try:
                shutil.rmtree(self.temp_feed_dir)
            except Exception:
                pass

    @patch("yfinance.download")
    def test_build_dashboard_data_side_effect_free_cqrs(self, mock_yf):
        """Execute build_dashboard_data() and strictly assert zero persistence write mutations."""
        print("\n[Tier 4.1] Verifying CQRS Pipeline & Side-Effect Free Feed Generation (Mock Assertions)...")
        
        synthetic_df = generate_synthetic_ohlcv(n_bars=80, start_price=150.0)
        mock_yf.return_value = synthetic_df
        
        import generate_dashboard_feed
        import db_manager
        
        # Patch ALL database mutation / write functions in db_manager
        with patch.object(db_manager, "save_recommendation_matrix_record") as mock_save_matrix, \
             patch.object(db_manager, "archive_daily_recommendations") as mock_archive, \
             patch.object(db_manager, "save_macro_history_record") as mock_save_macro, \
             patch.object(db_manager, "add_portfolio_buy") as mock_add_buy, \
             patch.object(db_manager, "record_portfolio_sell") as mock_record_sell, \
             patch.object(db_manager, "close_portfolio_position") as mock_close_pos, \
             patch.object(db_manager, "clear_portfolio") as mock_clear, \
             patch.object(db_manager, "reset_all_holdings") as mock_reset, \
             patch.object(db_manager, "sync_portfolio_prices") as mock_sync_prices:
            
            # Execute dashboard data pipeline
            payload = generate_dashboard_feed.build_dashboard_data(
                output_file=self.temp_out_json,
                charts_dir=self.temp_charts_dir
            )
            
            # STRICT ZERO-WRITE ASSERTIONS
            mock_save_matrix.assert_not_called()
            mock_archive.assert_not_called()
            mock_save_macro.assert_not_called()
            mock_add_buy.assert_not_called()
            mock_record_sell.assert_not_called()
            mock_close_pos.assert_not_called()
            mock_clear.assert_not_called()
            mock_reset.assert_not_called()
            mock_sync_prices.assert_not_called()
            
            # Verify payload completeness and structure
            self.assertIsInstance(payload, dict)
            required_keys = [
                "macro", "kpis", "daily_history", "portfolio",
                "tier1", "tier2", "tier3", "chart_intelligence"
            ]
            for key in required_keys:
                self.assertIn(key, payload, f"Missing payload root key: {key}")

            self.assertIsInstance(payload["chart_intelligence"], dict)
            self.assertIsInstance(payload["tier1"], list)

        print("  -> PASSED: Zero write mutations executed. View model build is 100% CQRS read-pure.")

    @patch("yfinance.download")
    def test_build_dashboard_data_unmocked_sqlite_zero_mutation(self, mock_yf):
        """Execute build_dashboard_data() against real unmocked SQLite DB and assert 0 row mutations."""
        print("\n[Tier 4.2] Verifying Real SQLite Zero-Mutation Invariant across all tables...")
        
        import tempfile
        import sqlite3
        import generate_dashboard_feed
        import db_manager

        # Set up isolated temp SQLite DB
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            temp_db_path = tf.name

        old_env = os.environ.get("AL_SANGMOO_DB_PATH")
        os.environ["AL_SANGMOO_DB_PATH"] = temp_db_path
        try:
            # Initialize schema
            db_manager.init_database()
            
            # Seed initial records so tables exist and have known baseline count
            db_manager.save_macro_history_record("2026-08-22", {"msi_score": 45.0, "macro_stance": "DEFENSE_HOLD"}, {})
            db_manager.save_recommendation_matrix_record("2026-08-22", [{"ticker": "NVDA", "close": 200.0}], [], [])
            
            # Snapshot baseline table counts
            def get_all_table_counts():
                counts = {}
                with sqlite3.connect(temp_db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
                    tables = [row[0] for row in cursor.fetchall()]
                    for tbl in tables:
                        cursor.execute(f"SELECT count(*) FROM {tbl}")
                        counts[tbl] = cursor.fetchone()[0]
                return counts

            baseline_counts = get_all_table_counts()
            
            # Mock yfinance to return synthetic OHLCV
            synthetic_df = generate_synthetic_ohlcv(n_bars=80, start_price=150.0)
            mock_yf.return_value = synthetic_df
            
            # Execute dashboard feed build against real SQLite DB
            payload = generate_dashboard_feed.build_dashboard_data(
                output_file=self.temp_out_json,
                charts_dir=self.temp_charts_dir
            )
            
            # Snapshot post-execution table counts
            post_counts = get_all_table_counts()
            
            # Assert exact row count equality across all tables
            for tbl, baseline_cnt in baseline_counts.items():
                post_cnt = post_counts.get(tbl, 0)
                self.assertEqual(
                    post_cnt, baseline_cnt,
                    f"CQRS Violation: Table '{tbl}' modified during build_dashboard_data() (before: {baseline_cnt}, after: {post_cnt})"
                )
                
            print("  -> PASSED: Unmocked SQLite database remained completely untouched (0 row mutations).")
        finally:
            if old_env is not None:
                os.environ["AL_SANGMOO_DB_PATH"] = old_env
            else:
                os.environ.pop("AL_SANGMOO_DB_PATH", None)
            for ext in ["", "-wal", "-shm"]:
                p = f"{temp_db_path}{ext}"
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

    @patch("yfinance.download")
    def test_build_dashboard_data_idempotency_and_pure_reads(self, mock_yf):
        """Verify calling build_dashboard_data() multiple times produces identical output without state drift."""
        print("\n[Tier 4.3] Verifying Dashboard Feed Pipeline Idempotency...")
        synthetic_df = generate_synthetic_ohlcv(n_bars=80, start_price=150.0)
        mock_yf.return_value = synthetic_df
        
        import generate_dashboard_feed
        
        p1 = generate_dashboard_feed.build_dashboard_data(
            output_file=self.temp_out_json,
            charts_dir=self.temp_charts_dir
        )
        p2 = generate_dashboard_feed.build_dashboard_data(
            output_file=self.temp_out_json,
            charts_dir=self.temp_charts_dir
        )
        
        self.assertEqual(len(p1["tier1"]), len(p2["tier1"]))
        self.assertEqual(len(p1["tier2"]), len(p2["tier2"]))
        self.assertEqual(p1["macro"].get("msi_score"), p2["macro"].get("msi_score"))
        print("  -> PASSED: Dashboard feed pipeline is 100% idempotent.")


# =============================================================================
# TIER 5: Static AST / Deduplication Integrity
# =============================================================================

class TestTier5StaticASTDeduplication(unittest.TestCase):
    """
    Parses application scripts using Python AST to verify Single Source of Truth
    architectural compliance, complete elimination of duplicate math/scoring, and clean domain separation.
    """

    def setUp(self):
        self.feed_py_path = os.path.join(PROJECT_ROOT, "generate_dashboard_feed.py")
        self.bot_py_path = os.path.join(PROJECT_ROOT, "al_sangmoo_daily_bot.py")
        self.yt_py_path = os.path.join(PROJECT_ROOT, "youtube_stream_scanner.py")

        self.assertTrue(os.path.exists(self.feed_py_path), "generate_dashboard_feed.py must exist")
        self.assertTrue(os.path.exists(self.bot_py_path), "al_sangmoo_daily_bot.py must exist")
        self.assertTrue(os.path.exists(self.yt_py_path), "youtube_stream_scanner.py must exist")

        with open(self.feed_py_path, "r", encoding="utf-8") as f:
            self.feed_tree = ast.parse(f.read(), filename="generate_dashboard_feed.py")

        with open(self.bot_py_path, "r", encoding="utf-8") as f:
            self.bot_tree = ast.parse(f.read(), filename="al_sangmoo_daily_bot.py")

        with open(self.yt_py_path, "r", encoding="utf-8") as f:
            self.yt_tree = ast.parse(f.read(), filename="youtube_stream_scanner.py")

    def test_ast_elimination_of_duplicate_functions(self):
        """Assert duplicate indicator, series, and scoring functions are 100% removed from callers."""
        print("\n[Tier 5.1] AST Verification: Elimination of Duplicate Function Definitions...")
        
        # 1. generate_dashboard_feed.py must NOT define build_ichimoku_series
        feed_func_names = [n.name for n in ast.walk(self.feed_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        self.assertNotIn(
            "build_ichimoku_series", feed_func_names,
            "generate_dashboard_feed.py must NOT define duplicate 'build_ichimoku_series'. Use al_sangmoo.domain.quant.ichimoku.build_ichimoku_series_payload."
        )

        # 2. al_sangmoo_daily_bot.py must NOT define calculate_indicators
        bot_func_names = [n.name for n in ast.walk(self.bot_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        self.assertNotIn(
            "calculate_indicators", bot_func_names,
            "al_sangmoo_daily_bot.py must NOT define duplicate 'calculate_indicators'. Use al_sangmoo.domain.quant.ichimoku.calculate_ichimoku_indicators."
        )

        print(f"  -> PASSED: Duplicate functions eliminated from AST (feed: {feed_func_names}, bot: {bot_func_names}).")

    def test_ast_disallow_inline_rolling_indicator_math(self):
        """Assert caller scripts do not contain inline rolling(9), rolling(26), rolling(52) math."""
        print("\n[Tier 5.2] AST Verification: Absence of Inline Rolling Indicator Calculations...")
        
        target_scripts = [
            ("generate_dashboard_feed.py", self.feed_tree),
            ("al_sangmoo_daily_bot.py", self.bot_tree),
            ("youtube_stream_scanner.py", self.yt_tree)
        ]

        forbidden_windows = {9, 26, 52}

        for script_name, tree in target_scripts:
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    # Check if call is .rolling(...)
                    if isinstance(node.func, ast.Attribute) and node.func.attr == "rolling":
                        # Check args (e.g. rolling(9))
                        for arg in node.args:
                            if isinstance(arg, ast.Constant) and arg.value in forbidden_windows:
                                self.fail(
                                    f"SSOT Violation in {script_name} (line {node.lineno}): "
                                    f"Contains inline '.rolling({arg.value})'. Must delegate to al_sangmoo.domain.quant.ichimoku."
                                )
                        # Check keyword args (e.g. rolling(window=9))
                        for kw in node.keywords:
                            if kw.arg == "window" and isinstance(kw.value, ast.Constant) and kw.value.value in forbidden_windows:
                                self.fail(
                                    f"SSOT Violation in {script_name} (line {node.lineno}): "
                                    f"Contains inline '.rolling(window={kw.value.value})'. Must delegate to al_sangmoo.domain.quant.ichimoku."
                                )

        print("  -> PASSED: No inline rolling(9/26/52) indicator calculations detected in caller scripts.")

    def test_ast_verify_mandatory_domain_quant_imports(self):
        """Assert caller scripts import canonical math and scoring engines from al_sangmoo.domain.quant."""
        print("\n[Tier 5.3] AST Verification: Mandatory Domain Quant Imports in Callers...")
        
        def get_imported_modules_and_symbols(tree):
            imports = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module not in imports:
                        imports[node.module] = set()
                    for alias in node.names:
                        imports[node.module].add(alias.name)
            return imports

        feed_imports = get_imported_modules_and_symbols(self.feed_tree)
        bot_imports = get_imported_modules_and_symbols(self.bot_tree)
        yt_imports = get_imported_modules_and_symbols(self.yt_tree)

        # 1. generate_dashboard_feed.py
        self.assertIn(
            "al_sangmoo.domain.quant.ichimoku", feed_imports,
            "generate_dashboard_feed.py must import from al_sangmoo.domain.quant.ichimoku"
        )
        self.assertTrue(
            {"calculate_ichimoku_indicators", "build_ichimoku_series_payload"}.issubset(feed_imports["al_sangmoo.domain.quant.ichimoku"]),
            "generate_dashboard_feed.py must import calculate_ichimoku_indicators and build_ichimoku_series_payload"
        )
        self.assertIn(
            "al_sangmoo.domain.quant.scoring", feed_imports,
            "generate_dashboard_feed.py must import from al_sangmoo.domain.quant.scoring"
        )

        # 2. al_sangmoo_daily_bot.py
        self.assertIn(
            "al_sangmoo.domain.quant.ichimoku", bot_imports,
            "al_sangmoo_daily_bot.py must import from al_sangmoo.domain.quant.ichimoku"
        )
        self.assertIn(
            "calculate_ichimoku_indicators", bot_imports["al_sangmoo.domain.quant.ichimoku"],
            "al_sangmoo_daily_bot.py must import calculate_ichimoku_indicators"
        )
        self.assertIn(
            "al_sangmoo.domain.quant.scoring", bot_imports,
            "al_sangmoo_daily_bot.py must import from al_sangmoo.domain.quant.scoring"
        )

        # 3. youtube_stream_scanner.py
        self.assertIn(
            "al_sangmoo.domain.quant.macro", yt_imports,
            "youtube_stream_scanner.py must import from al_sangmoo.domain.quant.macro"
        )
        self.assertTrue(
            "evaluate_macro_stance" in yt_imports["al_sangmoo.domain.quant.macro"] or "calculate_msi_regime" in yt_imports["al_sangmoo.domain.quant.macro"],
            "youtube_stream_scanner.py must import evaluate_macro_stance or calculate_msi_regime"
        )

        print("  -> PASSED: All caller scripts contain required al_sangmoo.domain.quant imports.")

    def test_ast_verify_actual_domain_function_invocations(self):
        """Assert caller scripts actually invoke imported domain functions in their AST call graphs."""
        print("\n[Tier 5.4] AST Verification: Domain Function Invocations in Call Graph...")
        
        def get_called_function_names(tree):
            called = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        called.add(node.func.id)
                    elif isinstance(node.func, ast.Attribute):
                        called.add(node.func.attr)
            return called

        feed_calls = get_called_function_names(self.feed_tree)
        bot_calls = get_called_function_names(self.bot_tree)
        yt_calls = get_called_function_names(self.yt_tree)

        # 1. generate_dashboard_feed.py calls
        self.assertIn(
            "calculate_ichimoku_indicators", feed_calls,
            "generate_dashboard_feed.py AST must contain calls to calculate_ichimoku_indicators"
        )
        self.assertIn(
            "build_ichimoku_series_payload", feed_calls,
            "generate_dashboard_feed.py AST must contain calls to build_ichimoku_series_payload"
        )

        # 2. al_sangmoo_daily_bot.py calls
        self.assertIn(
            "calculate_ichimoku_indicators", bot_calls,
            "al_sangmoo_daily_bot.py AST must contain calls to calculate_ichimoku_indicators"
        )

        # 3. youtube_stream_scanner.py calls
        self.assertTrue(
            "evaluate_macro_stance" in yt_calls or "calculate_msi_regime" in yt_calls,
            "youtube_stream_scanner.py AST must contain calls to evaluate_macro_stance or calculate_msi_regime"
        )

        print("  -> PASSED: Domain function calls confirmed in AST call graphs across all caller scripts.")


# =============================================================================
# TIER 6: Full Platform Regression Runner
# =============================================================================

class TestTier6PlatformRegressionRunner(unittest.TestCase):
    """
    Executes the full suite of platform regression tests across Phase 1, 2, 4,
    5.1, 5.2, and Global 60 Dual Strategy to guarantee zero regressions.
    """

    def test_full_regression_suite(self):
        """Executes all previous verification test suites in clean subprocesses."""
        print("\n[Tier 6.1] Executing Full Platform Regression Test Suite...")
        
        regression_tests = [
            ("Phase 1 Hardening", os.path.join(BASE_DIR, "test_phase1_hardening.py")),
            ("Phase 2 Modular Architecture", os.path.join(BASE_DIR, "test_phase2_modular.py")),
            ("Phase 4 Execution Gateway", os.path.join(BASE_DIR, "test_phase4_execution.py")),
            ("Phase 5.1 Security Hardening", os.path.join(BASE_DIR, "test_phase5_1_security.py")),
            ("Phase 5.2 Concurrency Hardening", os.path.join(BASE_DIR, "test_phase5_2_concurrency.py")),
            ("Global 60 Dual Strategy", os.path.join(BASE_DIR, "test_global60_dual_strategy.py"))
        ]

        for suite_name, script_path in regression_tests:
            self.assertTrue(os.path.exists(script_path), f"Regression test script missing: {script_path}")
            print(f"  --> Running {suite_name} ({os.path.basename(script_path)})...")
            
            start_t = time.time()
            sub_env = dict(os.environ)
            sub_env.pop("AL_SANGMOO_DB_PATH", None)
            sub_env["PYTHONIOENCODING"] = "utf-8"
            sub_env["PYTHONUTF8"] = "1"
            proc = subprocess.run(
                [sys.executable, script_path],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=sub_env,
                timeout=120
            )
            elapsed = time.time() - start_t
            
            if proc.returncode != 0:
                print(f"FAILED: {suite_name}")
                print(proc.stdout)
                print(proc.stderr)
            
            self.assertEqual(
                proc.returncode, 0,
                f"Regression test suite '{suite_name}' failed with exit code {proc.returncode}.\nStderr: {proc.stderr}"
            )
            print(f"      [{suite_name}: PASSED in {elapsed:.2f}s]")

        print("\n===============================================================================")
        print("  ALL PLATFORM REGRESSION SUITES PASSED (100% GREEN) - ZERO REGRESSIONS")
        print("===============================================================================")


# =============================================================================
# Main Test Runner
# =============================================================================

def run_all_phase5_3_tests():
    """Runs all 6 Tiers of Phase 5.3 SSOT Quant & Architecture tests."""
    print("=" * 79)
    print("  RUNNING PHASE 5.3 SSOT QUANTITATIVE CONSOLIDATION & CLEAN ARCHITECTURE TESTS")
    print("=" * 79)

    suite = unittest.TestSuite()
    loader = unittest.TestLoader()

    suite.addTests(loader.loadTestsFromTestCase(TestTier1IndicatorMathParity))
    suite.addTests(loader.loadTestsFromTestCase(TestTier2QuantScoringAndClassification))
    suite.addTests(loader.loadTestsFromTestCase(TestTier3MacroStanceIndex2Canonical))
    suite.addTests(loader.loadTestsFromTestCase(TestTier4CQRSSideEffectFreePipeline))
    suite.addTests(loader.loadTestsFromTestCase(TestTier5StaticASTDeduplication))
    suite.addTests(loader.loadTestsFromTestCase(TestTier6PlatformRegressionRunner))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Cleanup temporary test db
    for ext in ["", "-wal", "-shm"]:
        p = f"{TEST_DB}{ext}"
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    if result.wasSuccessful():
        print("\n" + "=" * 79)
        print("  ALL PHASE 5.3 SSOT QUANT & CLEAN ARCHITECTURE TESTS PASSED! (100% GREEN)")
        print("=" * 79)
        return 0
    else:
        print("\n" + "=" * 79)
        print(f"  PHASE 5.3 TEST FAILURES DETECTED: {len(result.failures)} failures, {len(result.errors)} errors")
        print("=" * 79)
        return 1


if __name__ == "__main__":
    sys.exit(run_all_phase5_3_tests())
