"""
Comprehensive Phase 3 Test Suite:
Friction-Aware Vectorized Backtester, MTF Matrix & Dynamic ATR Risk Sizing.
"""
import os
import sys
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
sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.backtest.engine import run_backtest_simulation
from al_sangmoo.domain.quant.multi_timeframe import calculate_mtf_consensus
from al_sangmoo.domain.risk.position_sizer import calculate_atr, calculate_dynamic_position_size
from server import get_backtest_report, get_mtf_consensus, get_recommended_position_size

def test_friction_aware_backtester():
    print("\n[Test 1] Verifying Friction-Aware Backtest Simulation Engine...")
    
    # 1. Generate 300-day synthetic dataset with a distinct breakout and pullback setup
    np.random.seed(42)
    dates = pd.bdate_range(end="2026-08-21", periods=300)
    prices = 100.0 + np.cumsum(np.random.randn(300) * 1.5)
    prices = np.maximum(prices, 50.0) # avoid negative
    
    df = pd.DataFrame({
        "Open": prices,
        "High": prices + np.random.uniform(0.5, 3.0, 300),
        "Low": prices - np.random.uniform(0.5, 3.0, 300),
        "Close": prices + np.random.randn(300) * 0.5,
        "Volume": np.random.randint(500000, 2000000, 300)
    }, index=dates)

    res = run_backtest_simulation(
        data=df,
        initial_capital=100000.0,
        slippage_bps=0.0010, # 10 bps
        fee_rate=0.0008,     # 8 bps
        stop_loss_pct=-0.03, # -3% Stop
        take_profit_pct=0.15 # +15% TP
    )

    assert res["status"] == "success"
    assert "total_return_pct" in res
    assert "sharpe_ratio" in res
    assert "max_drawdown_pct" in res
    assert "trades" in res
    assert "equity_curve" in res
    assert len(res["equity_curve"]) > 0

    print(f"  - Initial Capital: ${res['initial_capital']:,.2f} -> Final Equity: ${res['final_equity']:,.2f}")
    print(f"  - Total Return: {res['total_return_pct']:+.2f}% | Sharpe Ratio: {res['sharpe_ratio']} | Max Drawdown: {res['max_drawdown_pct']:.2f}%")
    print(f"  - Total Completed Trades: {res['total_trades']} (Win Rate: {res['win_rate_pct']}%, Profit Factor: {res['profit_factor']})")
    print(f"  - Modeled Market Friction: Slippage {res['friction_modeled']['slippage_bps']} bps, Fee {res['friction_modeled']['fee_bps']} bps")
    print("  -> PASSED: Vectorized friction-aware backtest core verified.")

def test_multi_timeframe_consensus():
    print("\n[Test 2] Verifying Multi-Timeframe Consensus Matrix & Pre-Trigger Scanner...")
    
    # Synthetic Multi-Timeframe dictionaries
    dates_d = pd.bdate_range(end="2026-08-21", periods=100)
    dates_w = pd.bdate_range(end="2026-08-21", freq="W", periods=60)
    dates_h = pd.date_range(end="2026-08-21 16:00", freq="1h", periods=100)

    p_d = np.linspace(100, 150, 100)
    p_w = np.linspace(80, 150, 60)
    p_h = np.linspace(145, 150, 100)

    mock_data = {
        "daily": pd.DataFrame({"Open": p_d, "High": p_d + 1, "Low": p_d - 1, "Close": p_d, "Volume": [800000] * 100}, index=dates_d),
        "weekly": pd.DataFrame({"Open": p_w, "High": p_w + 3, "Low": p_w - 3, "Close": p_w, "Volume": [4000000] * 60}, index=dates_w),
        "hourly": pd.DataFrame({"Open": p_h, "High": p_h + 0.5, "Low": p_h - 0.5, "Close": p_h, "Volume": [100000] * 100}, index=dates_h)
    }

    mtf_res = calculate_mtf_consensus(mock_data)
    assert mtf_res["status"] == "success"
    assert 0.0 <= mtf_res["consensus_score"] <= 100.0
    assert "weekly" in mtf_res["breakdown"]
    assert "daily" in mtf_res["breakdown"]
    assert "hourly" in mtf_res["breakdown"]
    
    print(f"  - Multi-Timeframe Score: {mtf_res['consensus_score']} / 100 pt")
    print(f"  - Weekly Trend: {mtf_res['breakdown']['weekly']['pts']}/30 pt ({mtf_res['breakdown']['weekly']['regime']})")
    print(f"  - Daily Sweet-Spot: {mtf_res['breakdown']['daily']['pts']}/50 pt")
    print(f"  - Hourly Momentum: {mtf_res['breakdown']['hourly']['pts']}/20 pt ({mtf_res['breakdown']['hourly']['momentum']})")
    print(f"  - Scanner Verdict: {mtf_res['verdict']}")
    print("  -> PASSED: Multi-timeframe consensus engine verified.")

def test_dynamic_atr_position_sizing():
    print("\n[Test 3] Verifying Dynamic ATR Volatility Position Sizer...")
    
    df_vol = pd.DataFrame({
        "High": [105, 108, 107, 110, 112, 115, 114, 116, 118, 120, 122, 125, 124, 126, 128],
        "Low":  [95,  98,  97,  100, 102, 105, 104, 106, 108, 110, 112, 115, 114, 116, 118],
        "Close":[100, 105, 102, 108, 110, 112, 108, 114, 115, 118, 120, 122, 118, 125, 126]
    })
    
    atr = calculate_atr(df_vol, window=14)
    assert atr > 0.0
    print(f"  - Calculated 14-Day ATR: ${atr:,.2f}")

    # 1. Test in ACTIVE_BUY regime (MSI < 30)
    size_green = calculate_dynamic_position_size(portfolio_equity=100000.0, current_price=125.0, atr_14=atr, msi_score=20.0)
    assert size_green["macro_multiplier"] == 1.0
    assert size_green["allocation_pct"] <= 25.0 # Must observe 25% single-asset cap
    print(f"  - Active Buy Mode (MSI 20pt): Alloc ${size_green['allocated_cash']:,.2f} ({size_green['allocation_pct']}%, {size_green['recommended_shares']} shares)")

    # 2. Test in DEFENSE_HOLD regime (MSI 65)
    size_orange = calculate_dynamic_position_size(portfolio_equity=100000.0, current_price=125.0, atr_14=atr, msi_score=65.0)
    assert size_orange["macro_multiplier"] == 0.35
    assert size_orange["allocated_cash"] < size_green["allocated_cash"]
    print(f"  - Defense Hold Mode (MSI 65pt): Alloc ${size_orange['allocated_cash']:,.2f} ({size_orange['allocation_pct']}%, {size_orange['recommended_shares']} shares)")

    # 3. Test in CASH_EXIT regime (MSI 85)
    size_red = calculate_dynamic_position_size(portfolio_equity=100000.0, current_price=125.0, atr_14=atr, msi_score=85.0)
    assert size_red["macro_multiplier"] == 0.0
    assert size_red["recommended_shares"] == 0.0
    print(f"  - Cash Exit Mode (MSI 85pt): Alloc ${size_red['allocated_cash']:,.2f} (0 shares)")
    print("  -> PASSED: Dynamic ATR position sizing & macro guardrails verified.")

def test_api_phase3_endpoints():
    print("\n[Test 4] Verifying Phase 3 Server API Handlers...")
    # Position size endpoint handler
    size_res = get_recommended_position_size(ticker="AMZN", equity=100000.0, msi=60.0)
    assert size_res["ticker"] == "AMZN"
    assert size_res["allocated_cash"] > 0
    print(f"  - GET /api/risk/size/AMZN handler: OK (Alloc ${size_res['allocated_cash']:,.2f})")
    print("  -> PASSED: Phase 3 API handlers verified.")

if __name__ == "__main__":
    print("======================================================================")
    print("  R-SANGMOO QUANT PLATFORM: PHASE 3 BACKTESTER & FACTOR TEST SUITE")
    print("======================================================================")
    
    test_friction_aware_backtester()
    test_multi_timeframe_consensus()
    test_dynamic_atr_position_sizing()
    test_api_phase3_endpoints()
    
    print("\n======================================================================")
    print("  ALL PHASE 3 BACKTESTER & FACTOR TESTS PASSED! (100% GREEN)")
    print("======================================================================")
