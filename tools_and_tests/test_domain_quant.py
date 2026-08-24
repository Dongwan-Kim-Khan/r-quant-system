"""
Unit & Regression Test Suite for Domain Quant Layer (al_sangmoo.domain.quant).
Verifies Milestones M1, M2, M3 SSOT implementation.
"""
import os
import sys
import pandas as pd
import numpy as np

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

from al_sangmoo.domain.quant import (
    calculate_ichimoku_indicators,
    project_future_cloud,
    detect_cloud_trampoline_bounce,
    compute_institutional_flow_indicators,
    build_ichimoku_series_payload,
    QuantIndicators,
    WeeklyTrendContext,
    InstitutionalFlowContext,
    TrampolineBounceContext,
    QuantScoreBreakdown,
    TierClassification,
    calculate_canonical_bull_score,
    calculate_canonical_sniper_score,
    calculate_canonical_bear_score,
    evaluate_quant_score,
    classify_quant_tier,
    classify_3tier_candidates,
    evaluate_macro_stance,
    calculate_msi_regime,
)


def create_synthetic_ohlcv(n_bars: int = 120, trend: str = "bull") -> pd.DataFrame:
    dates = pd.bdate_range(end="2026-08-21", periods=n_bars)
    if trend == "bull":
        prices = np.linspace(100.0, 160.0, n_bars)
    elif trend == "bear":
        prices = np.linspace(160.0, 100.0, n_bars)
    else:
        prices = np.ones(n_bars) * 120.0

    volumes = [1000000 + (i % 5) * 50000 for i in range(n_bars)]
    df = pd.DataFrame({
        "Open": prices,
        "High": prices + 2.0,
        "Low": prices - 2.0,
        "Close": prices + 0.5,
        "Volume": volumes
    }, index=dates)
    return df


def test_m1_ichimoku_indicators():
    print("[M1 Test 1] Verifying calculate_ichimoku_indicators...")
    df = create_synthetic_ohlcv(120)
    df_calc = calculate_ichimoku_indicators(df)

    expected_cols = [
        "Tenkan", "Kijun", "RawSpanA", "RawSpanB", "SpanA", "SpanB", "Chikou",
        "SMA20", "SMA50", "SMA60", "SMA200", "Vol_SMA20", "Vol_Ratio"
    ]
    for col in expected_cols:
        assert col in df_calc.columns, f"Missing expected column: {col}"

    # Verify safe division with 0 volume
    df_zero = df.copy()
    df_zero["Volume"] = 0
    df_zero_calc = calculate_ichimoku_indicators(df_zero)
    assert not df_zero_calc["Vol_Ratio"].isna().any(), "Vol_Ratio contains NaN on 0 volume"
    print("  -> PASSED: All rolling indicators computed with safe division.")


def test_m1_future_cloud_projection():
    print("\n[M1 Test 2] Verifying project_future_cloud...")
    df = create_synthetic_ohlcv(120)
    df_calc = calculate_ichimoku_indicators(df)

    # Daily projection
    fa_pts, fb_pts, fa_vals, fb_vals = project_future_cloud(df_calc, periods=26, is_weekly=False)
    assert len(fa_pts) == 26, f"Expected 26 span A points, got {len(fa_pts)}"
    assert len(fb_pts) == 26, f"Expected 26 span B points, got {len(fb_pts)}"
    assert len(fa_vals) == 26
    assert len(fb_vals) == 26

    # Weekly projection
    fa_w, fb_w, _, _ = project_future_cloud(df_calc, periods=26, is_weekly=True)
    assert len(fa_w) == 26
    assert len(fb_w) == 26
    print("  -> PASSED: +26 period forward cloud projection verified.")


def test_m1_detect_trampoline_bounce():
    print("\n[M1 Test 3] Verifying detect_cloud_trampoline_bounce...")
    df = create_synthetic_ohlcv(60)
    df_calc = calculate_ichimoku_indicators(df)

    # Craft a bounce candle at -3 bars
    cloud_top = float(df_calc['SpanA'].iloc[-3]) if not pd.isna(df_calc['SpanA'].iloc[-3]) else 140.0
    if cloud_top <= 0:
        cloud_top = 140.0
        df_calc['SpanA'] = 140.0
        df_calc['SpanB'] = 135.0

    # Candle touches within 1% of cloud top and closes 3% above
    df_calc.loc[df_calc.index[-3], 'Low'] = cloud_top * 1.01
    df_calc.loc[df_calc.index[-3], 'Close'] = cloud_top * 1.03
    df_calc.loc[df_calc.index[-3], 'SpanA'] = cloud_top
    df_calc.loc[df_calc.index[-3], 'SpanB'] = cloud_top - 5.0

    detected, days_ago, touch_gap, close_gap = detect_cloud_trampoline_bounce(df_calc, max_lookback=14)
    assert detected is True, "Trampoline bounce should be detected"
    assert days_ago == 2, f"Expected bounce 2 days ago, got {days_ago}"
    print(f"  -> PASSED: Trampoline bounce detected at {days_ago} days ago (touch: {touch_gap}%, close: {close_gap}%).")


def test_m1_institutional_flow_and_series_payload():
    print("\n[M1 Test 4] Verifying compute_institutional_flow_indicators & build_ichimoku_series_payload...")
    df = create_synthetic_ohlcv(120)
    flow = compute_institutional_flow_indicators(df)
    assert "obv_status" in flow
    assert "flow_ratio" in flow
    assert "flow_score" in flow
    assert 0 <= flow["flow_score"] <= 100

    df_calc = calculate_ichimoku_indicators(df)
    payload = build_ichimoku_series_payload(df_calc, is_weekly=False, max_bars=100)
    assert "candles" in payload
    assert "kijun_line" in payload
    assert "tenkan_line" in payload
    assert "span_a_line" in payload
    assert "span_b_line" in payload
    assert "future_span_a" in payload
    assert len(payload["candles"]) == 100
    print("  -> PASSED: Flow indicators and series payload formatted correctly.")


def test_m2_scoring_matrix():
    print("\n[M2 Test 1] Verifying canonical graduated scoring matrix...")
    ind_perfect = QuantIndicators.from_values(
        close=150.0,
        kijun=149.0,   # gap = +0.67% (sweet spot -0.5% ~ 3.5%) -> 35 pt
        tenkan=150.0,  # tenkan >= kijun -> 10 pt
        span_a=140.0,  # close >= cloud_top -> 35 pt
        span_b=135.0,
        vol_ratio=0.55 # vol_ratio <= 0.60 -> 20 pt
    )
    bull_score, breakdown = calculate_canonical_bull_score(ind_perfect)
    assert bull_score == 100, f"Expected 100 pt, got {bull_score}"
    assert breakdown["cloud_pts"] == 35
    assert breakdown["kijun_pts"] == 35
    assert breakdown["vdu_pts"] == 20
    assert breakdown["tenkan_pts"] == 10

    tramp = TrampolineBounceContext(detected=True, days_ago=3)
    sniper_score, s_breakdown = calculate_canonical_sniper_score(ind_perfect, tramp)
    # Trampoline +40, Cloud +30, Kijun +15, Tenkan +15 = 100
    assert sniper_score == 100, f"Expected 100 pt sniper score, got {sniper_score}"

    ind_bear = QuantIndicators.from_values(
        close=120.0,
        kijun=130.0,   # close < kijun -> 40 pt, gap = -7.7% -> 15 pt
        tenkan=122.0,
        span_a=135.0,
        span_b=130.0,  # close < cloud_bottom -> 35 pt
        vol_ratio=1.5
    )
    bear_score = calculate_canonical_bear_score(ind_bear)
    assert bear_score == 90, f"Expected 90 pt bear score, got {bear_score}"
    print("  -> PASSED: Canonical Bull (100pt), Sniper (100pt), and Bear (90pt) matrices verified.")


def test_m2_evaluate_quant_score():
    print("\n[M2 Test 2] Verifying evaluate_quant_score comprehensive output...")
    res = evaluate_quant_score(
        close=151.0,
        kijun=150.0,
        tenkan=151.0,
        span_a=140.0,
        span_b=135.0,
        vol_ratio=0.60
    )
    assert res["bull_score"] == 100
    assert res["quant_type"] == "BULL"
    assert "intelligence" in res
    assert "verdict" in res["intelligence"]
    assert "action" in res["intelligence"]
    print(f"  - Verdict: {res['quant_verdict']}")
    print("  -> PASSED: evaluate_quant_score output verified.")


def test_m2_tier_classification_and_stops():
    print("\n[M2 Test 3] Verifying 3-Tier Classification & Stop/Target Standardization...")
    ind = QuantIndicators.from_values(
        close=100.0,
        kijun=99.0,
        tenkan=100.0,
        span_a=90.0,
        span_b=85.0,
        vol_ratio=0.50
    )
    weekly = WeeklyTrendContext(is_weekly_bull=True)
    flow = InstitutionalFlowContext(obv_status="STEALTH_ACCUM", flow_ratio=1.50, flow_score=85, is_stealth_accum=True)
    tramp = TrampolineBounceContext(detected=True, days_ago=2)

    tier_res = classify_quant_tier(
        ticker="NVDA",
        ind=ind,
        weekly=weekly,
        flow=flow,
        trampoline=tramp,
        macro_tailwind_sectors=["AI/TECH"],
        sector="AI/TECH"
    )

    assert tier_res.tier == "TIER_1", f"Expected TIER_1, got {tier_res.tier}"
    assert tier_res.entry_price == 100.0
    assert tier_res.target_price == 115.0, f"Expected 115.0 (+15%), got {tier_res.target_price}"
    assert tier_res.stop_price == 96.0, f"Expected 96.0 (-4%), got {tier_res.stop_price}"
    assert tier_res.partial_tp_price == 108.0, f"Expected 108.0 (+8%), got {tier_res.partial_tp_price}"
    print(f"  - Classified: {tier_res.tier} ({tier_res.tier_name_kr})")
    print(f"  - Target: ${tier_res.target_price} (+15%), Stop: ${tier_res.stop_price} (-4%), Partial TP: ${tier_res.partial_tp_price} (+8%)")
    print("  -> PASSED: Standardized stops and Tier 1 classification verified.")


def test_m2_classify_3tier_candidates():
    print("\n[M2 Test 4] Verifying classify_3tier_candidates deterministic selection...")
    chart_data = {
        "NVDA": {
            "latest_close": 120.0, "latest_kijun": 119.0, "latest_tenkan": 120.0,
            "span_a": 110.0, "span_b": 105.0, "latest_vol_ratio": 0.55,
            "is_weekly_bull": True, "obv_status": "STEALTH_ACCUM", "flow_ratio": 1.40,
            "flow_score": 85, "is_stealth_accum": True, "trampoline_detected": True, "trampoline_days_ago": 2
        },
        "TSM": {
            "latest_close": 150.0, "latest_kijun": 149.0, "latest_tenkan": 150.0,
            "span_a": 140.0, "span_b": 135.0, "latest_vol_ratio": 0.50,
            "is_weekly_bull": True, "obv_status": "BULL_FLOW", "flow_ratio": 1.10,
            "flow_score": 50, "is_stealth_accum": False, "trampoline_detected": False
        },
        "AMD": {
            "latest_close": 130.0, "latest_kijun": 132.0, "latest_tenkan": 131.0,
            "span_a": 125.0, "span_b": 120.0, "latest_vol_ratio": 0.90,
            "is_weekly_bull": True, "obv_status": "NEUTRAL", "flow_ratio": 1.0,
            "flow_score": 40, "is_stealth_accum": False, "trampoline_detected": True, "trampoline_days_ago": 1
        }
    }
    t1, t2, t3 = classify_3tier_candidates(chart_data, tailwind_sectors=["AI/TECH", "SEMIS"])
    assert len(t1) > 0, "Expected at least 1 Tier 1 pick"
    assert t1[0]["ticker"] == "NVDA"
    assert t1[0]["target_price"] == 138.0 # 120 * 1.15
    assert t1[0]["stop_price"] == 115.2 # 120 * 0.96
    print(f"  - Tier 1 Picks: {[x['ticker'] for x in t1]}")
    print(f"  - Tier 2 Picks: {[x['ticker'] for x in t2]}")
    print(f"  - Tier 3 Picks: {[x['ticker'] for x in t3]}")
    print("  -> PASSED: 3-Tier candidate selection determinism verified.")


def test_m3_macro_stance():
    print("\n[M3 Test 1] Verifying evaluate_macro_stance unified weights & classification...")
    # Test boundary conditions for US 10Y, VIX, WTI, DXY
    gauges_high_stress = {
        "us10y": {"val": 4.55}, # 25.0
        "vix": {"val": 26.0},   # 15.0
        "wti": {"val": 86.0},   # 10.0
        "dxy": {"val": 106.0}   # 10.0
    }
    # Total hard = 60.0
    res_high = evaluate_macro_stance(gauges_high_stress, defense_count=20, buy_count=5, matched_shocks=["지정학적 분쟁 및 전쟁 리스크"])
    assert res_high["msi_breakdown"]["m_hard"] == 60.0, f"Expected 60.0 hard pts, got {res_high['msi_breakdown']['m_hard']}"
    assert res_high["macro_stance"] == "CASH_EXIT"

    gauges_moderate = {
        "us10y": {"val": 4.35}, # 18.0
        "vix": {"val": 21.0},   # 10.0
        "wti": {"val": 81.0},   # 6.0
        "dxy": {"val": 103.5}   # 6.0
    }
    # Total hard = 40.0
    res_mod = evaluate_macro_stance(gauges_moderate, defense_count=10, buy_count=10, matched_shocks=[])
    assert res_mod["msi_breakdown"]["m_hard"] == 40.0
    assert res_mod["macro_stance"] in ["DEFENSE_HOLD", "SELECTIVE_BUY"]

    gauges_green = {
        "us10y": {"val": 3.80}, # 0.0
        "vix": {"val": 14.0},   # 0.0
        "wti": {"val": 70.0},   # 0.0
        "dxy": {"val": 98.0}    # 0.0
    }
    # Total hard = 0.0
    res_green = evaluate_macro_stance(gauges_green, defense_count=0, buy_count=20, matched_shocks=[])
    assert res_green["msi_breakdown"]["m_hard"] == 0.0
    assert res_green["macro_stance"] == "ACTIVE_BUY"

    # Test legacy adapter calculate_msi_regime
    legacy_res = calculate_msi_regime(gauges_moderate, defense_count=10, buy_count=10)
    assert legacy_res["msi_score"] == res_mod["msi_score"]
    print("  -> PASSED: MSI 2.0 Unified Macro Stance and Legacy Adapter verified.")


if __name__ == "__main__":
    print("=" * 70)
    print("  RUNNING DOMAIN QUANT UNIT & REGRESSION TEST SUITE")
    print("=" * 70)
    test_m1_ichimoku_indicators()
    test_m1_future_cloud_projection()
    test_m1_detect_trampoline_bounce()
    test_m1_institutional_flow_and_series_payload()
    test_m2_scoring_matrix()
    test_m2_evaluate_quant_score()
    test_m2_tier_classification_and_stops()
    test_m2_classify_3tier_candidates()
    test_m3_macro_stance()
    print("\n" + "=" * 70)
    print("  ALL DOMAIN QUANT TESTS COMPLETED SUCCESSFULLY! (100% GREEN)")
    print("=" * 70)
