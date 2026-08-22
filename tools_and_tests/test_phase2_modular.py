import os
import sys
import time
import asyncio
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
sys.path.insert(0, PROJECT_ROOT)

def test_core_constants_and_config():
    print("\n[Test 1] Verifying Centralized Constants & Config (al_sangmoo.core)...")
    from al_sangmoo.core.constants import WATCHLIST, STOCK_DICT, POS_KEYWORDS, NEG_KEYWORDS, MACRO_DEFENSE_KEYWORDS
    from al_sangmoo.core.config import BASE_DIR, DB_FILE, OUTPUT_JSON
    
    assert len(WATCHLIST) >= 20, f"Expected >= 20 watchlist items, got {len(WATCHLIST)}"
    assert "AMZN" in STOCK_DICT and "NVDA" in STOCK_DICT, "Stock dictionary incomplete"
    assert len(POS_KEYWORDS) > 10 and len(NEG_KEYWORDS) > 10, "Keywords incomplete"
    assert os.path.exists(str(BASE_DIR)), f"Base dir {BASE_DIR} does not exist"
    print(f"  - Unified Watchlist: {len(WATCHLIST)} symbols registered.")
    print(f"  - Stock Dictionary: {len(STOCK_DICT)} alias maps.")
    print(f"  - Base Directory: {BASE_DIR}")
    print("  -> PASSED: Core constants and config unified as Single Source of Truth.")

def test_pure_quant_domain():
    print("\n[Test 2] Verifying Pure Quant Domain Engine (al_sangmoo.domain.quant)...")
    from al_sangmoo.domain.quant.ichimoku import calculate_ichimoku_indicators, project_future_cloud, evaluate_quant_score
    from al_sangmoo.domain.quant.macro import calculate_msi_regime
    
    # 1. Create synthetic 100-day OHLCV DataFrame
    dates = pd.bdate_range(end="2026-08-21", periods=100)
    prices = np.linspace(100, 150, 100)
    df = pd.DataFrame({
        "Open": prices,
        "High": prices + 2,
        "Low": prices - 2,
        "Close": prices + 1,
        "Volume": [1000000] * 100
    }, index=dates)
    
    df_calc = calculate_ichimoku_indicators(df)
    assert "Tenkan" in df_calc.columns
    assert "Kijun" in df_calc.columns
    assert "SpanA" in df_calc.columns
    assert "SpanB" in df_calc.columns
    assert "Vol_Ratio" in df_calc.columns
    
    # 2. Forward cloud projection
    fa_pts, fb_pts, fa_vals, fb_vals = project_future_cloud(df_calc, periods=26)
    assert len(fa_pts) == 26, f"Expected 26 forward span A points, got {len(fa_pts)}"
    assert len(fb_pts) == 26, f"Expected 26 forward span B points, got {len(fb_pts)}"
    print(f"  - Ichimoku Indicators & +26D Forward Cloud: Generated {len(fa_pts)} projected candles.")
    
    # 3. Quant scoring evaluation
    q_res = evaluate_quant_score(close=151.0, kijun=150.0, tenkan=151.0, span_a=140.0, span_b=135.0, vol_ratio=0.60)
    assert q_res["bull_score"] == 100, f"Expected 100 pt, got {q_res['bull_score']}"
    assert q_res["quant_type"] == "BULL"
    print(f"  - 17-Year Quant Scoring: {q_res['quant_score_text']} ({q_res['quant_verdict']})")
    
    # 4. MSI 2.0 Macro Regime calculation
    gauges = {
        "us10y": {"val": 4.65, "status": "BURDEN"},
        "dxy": {"val": 105.2, "status": "NEUTRAL"},
        "vix": {"val": 16.5, "status": "NORMAL"},
        "wti": {"val": 84.5, "status": "BURDEN"}
    }
    msi_res = calculate_msi_regime(gauges, defense_count=35, buy_count=5, matched_shocks=["지정학적 분쟁 및 전쟁 리스크", "인플레이션 및 원자재 변동성"])
    assert msi_res["macro_stance"] in ["DEFENSE_HOLD", "CASH_EXIT"], f"Unexpected stance: {msi_res['macro_stance']}"
    print(f"  - MSI 2.0 Macro Stance: {msi_res['macro_stance']} ({msi_res['msi_score']} pt)")
    print("  -> PASSED: Pure quantitative business domain decoupled from I/O.")

def test_infrastructure_and_persistence():
    print("\n[Test 3] Verifying Infrastructure & Persistence Layer (al_sangmoo.infrastructure)...")
    from al_sangmoo.infrastructure.persistence import add_portfolio_buy, record_portfolio_sell, reset_all_holdings, get_live_portfolio, get_db
    from al_sangmoo.infrastructure.atomic_io import atomic_save_json, atomic_read_json
    
    # Backup user holdings
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM my_portfolio")
    user_backup = cur.fetchall()
    conn.close()
    
    try:
        reset_all_holdings()
        pos_id = add_portfolio_buy(ticker="NVDA", buy_price=125.50, quantity=2.5)
        assert pos_id > 0
        
        p = get_live_portfolio()
        assert len(p["holdings"]) == 1
        assert p["holdings"][0]["ticker"] == "NVDA"
        assert p["holdings"][0]["quantity"] == 2.5
        
        sell_ok = record_portfolio_sell(holding_id=pos_id, sell_price=135.00, reason="TEST_PHASE2")
        assert sell_ok is True
        
        reset_all_holdings()
        print("  - SQLite Repository: Buy, Live Portfolio, Sell, and Reset verified.")
    finally:
        # Restore user holdings
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM my_portfolio")
        if user_backup:
            placeholders = ",".join(["?"] * len(user_backup[0]))
            cur.executemany(f"INSERT INTO my_portfolio VALUES ({placeholders})", user_backup)
        conn.commit()
        conn.close()
    
    # Test atomic io
    t_file = os.path.join(PROJECT_ROOT, "test_p2_atomic.json")
    atomic_save_json(t_file, {"phase": 2, "ready": True})
    data = atomic_read_json(t_file)
    assert data.get("phase") == 2
    if os.path.exists(t_file):
        os.remove(t_file)
    print("  - Atomic I/O Engine: NamedTemporaryFile swap verified.")
    print("  -> PASSED: Infrastructure layer operational.")

def test_websocket_hub():
    print("\n[Test 4] Verifying Real-Time WebSocket Hub (al_sangmoo.api.hub)...")
    from al_sangmoo.api.hub import hub
    
    received_messages = []
    
    class MockWebSocket:
        def __init__(self):
            self.accepted = False
        async def accept(self):
            self.accepted = True
        async def send_json(self, msg):
            received_messages.append(msg)
            
    async def run_ws_test():
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        
        await hub.connect(ws1)
        await hub.connect(ws2)
        assert len(hub.active_connections) == 2
        
        # Broadcast event
        t_start = time.perf_counter()
        await hub.broadcast("portfolio_update", {"ticker": "AMZN", "quantity": 10})
        t_elapsed = (time.perf_counter() - t_start) * 1000 # in ms
        
        assert len(received_messages) == 2
        assert received_messages[0]["event"] == "portfolio_update"
        assert received_messages[0]["data"]["ticker"] == "AMZN"
        
        print(f"  - WebSocket Broadcast Event Delivery: {t_elapsed:.2f} ms (Target < 50ms)")
        
        await hub.disconnect(ws1)
        await hub.disconnect(ws2)
        assert len(hub.active_connections) == 0

    asyncio.run(run_ws_test())
    print("  -> PASSED: WebSocket Hub bi-directional broadcast operational.")

if __name__ == "__main__":
    print("======================================================================")
    print("  R-SANGMOO QUANT PLATFORM: PHASE 2 MODULAR & WEBSOCKET TEST SUITE")
    print("======================================================================")
    
    test_core_constants_and_config()
    test_pure_quant_domain()
    test_infrastructure_and_persistence()
    test_websocket_hub()
    
    print("\n======================================================================")
    print("  ALL PHASE 2 MODULAR & WEBSOCKET TESTS PASSED! (100% GREEN)")
    print("======================================================================")
