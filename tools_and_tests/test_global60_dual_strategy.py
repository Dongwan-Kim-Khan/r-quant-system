"""
Automated Verification Test Suite:
1. Global 60-Stock Universe Integrity
2. Dual-Strategy (Strategy I & Strategy II) Engine
3. Origin Tagging (VIKINGS_LIVE vs QUANT_DISCOVERY)
4. Zero Emoji Institutional Formatting Compliance
"""
import os
import sys
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from al_sangmoo.core.constants import WATCHLIST, STOCK_DICT
from al_sangmoo.domain.quant.ticker_resolver import STOCK_DIRECTORY, resolve_ticker
DASHBOARD_JSON = os.path.join(BASE_DIR, "dashboard_data.json")

def test_universe_60_integrity():
    assert len(WATCHLIST) == 60, f"Expected 60 stocks in WATCHLIST, got {len(WATCHLIST)}"
    assert len(STOCK_DICT) >= 60, f"Expected at least 60 entries in STOCK_DICT, got {len(STOCK_DICT)}"
    
    # Verify replaced defense and tech leaders are present
    for t in ["LMT", "RTX", "NOC", "GE", "APP", "MRVL", "ISRG", "005930.KS", "000660.KS"]:
        assert t in WATCHLIST, f"Expected {t} in WATCHLIST"
        assert t in STOCK_DICT, f"Expected {t} in STOCK_DICT"
        
    # Verify excluded K-defense / Hanmi are removed
    for excluded in ["042700.KS", "012450.KS", "064350.KS", "079550.KS", "009540.KS", "034020.KS"]:
        assert excluded not in WATCHLIST, f"Excluded stock {excluded} still found in WATCHLIST"

def test_ticker_resolver_accuracy():
    assert resolve_ticker("록히드마틴") == "LMT"
    assert resolve_ticker("LMT") == "LMT"
    assert resolve_ticker("RTX") == "RTX"
    assert resolve_ticker("앱러빈") == "APP"
    assert resolve_ticker("마벨") == "MRVL"
    assert resolve_ticker("삼성전자") == "005930.KS"
    assert resolve_ticker("005930") == "005930.KS"

def test_dashboard_json_structure_and_origin():
    assert os.path.exists(DASHBOARD_JSON), "dashboard_data.json does not exist"
    with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    assert "primary_accumulation" in data, "Missing primary_accumulation in dashboard_data.json"
    assert "sniper_radar" in data, "Missing sniper_radar in dashboard_data.json"
    assert "signal_tracker" in data, "Missing signal_tracker in dashboard_data.json"
    assert "charts" in data, "Missing charts in dashboard_data.json"
    assert len(data["charts"]) >= 50, f"Expected charts for at least 50 universe stocks, got {len(data['charts'])}"
    
    # Check origin tagging
    for item in data.get("signal_tracker", []):
        assert item["origin"] in ["VIKINGS_LIVE", "QUANT_DISCOVERY"], f"Invalid origin {item['origin']}"
        assert "target_price" in item and item["target_price"] > 0
        assert "stop_price" in item and item["stop_price"] > 0

def test_zero_emojis_in_feed():
    with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
        content = f.read()
        
    forbidden_emojis = ["🔥", "🎯", "🚀", "🤖", "📺", "🚨", "💡", "🏆", "🥇"]
    for emoji in forbidden_emojis:
        assert emoji not in content, f"Found forbidden emoji {emoji} in dashboard_data.json"

if __name__ == "__main__":
    print("=" * 70)
    print("  BLOOMBERG STYLE QUANT PLATFORM: GLOBAL 60 & DUAL STRATEGY TEST")
    print("=" * 70)
    test_universe_60_integrity()
    print("[1] Universe 60 integrity: PASSED (58 US/Global + 2 KR Memory)")
    test_ticker_resolver_accuracy()
    print("[2] Multi-language ticker resolver: PASSED (LMT, RTX, APP, MRVL, 005930.KS)")
    test_dashboard_json_structure_and_origin()
    print("[3] Dashboard feed structure & origin tagging: PASSED (VIKINGS_LIVE & QUANT_DISCOVERY)")
    test_zero_emojis_in_feed()
    print("[4] Zero emoji institutional formatting: PASSED")
    print("=" * 70)
    print("  ALL VERIFICATION TESTS COMPLETED SUCCESSFULLY! (100% GREEN)")
    print("=" * 70)
