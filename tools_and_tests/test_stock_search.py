"""
Automated Test Suite for Universal Stock Search & Dynamic Quant Rating Engine.
"""
import os
import sys

# Windows encoding fix
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.domain.quant.ticker_resolver import resolve_ticker, search_ticker_suggestions
from generate_dashboard_feed import compute_all_indicators
from server import search_stock_suggestions, get_ticker_chart

def test_smart_ticker_resolver():
    print("\n[Test 1] Verifying Smart Multi-Language Ticker Resolver...")
    
    assert resolve_ticker("애플") == "AAPL"
    assert resolve_ticker("삼전") == "005930.KS"
    assert resolve_ticker("005930") == "005930.KS"
    assert resolve_ticker("하이닉스") == "000660.KS"
    assert resolve_ticker("대만반도체") == "TSM"
    assert resolve_ticker("tsm") == "TSM"
    assert resolve_ticker("팔란티어") == "PLTR"
    assert resolve_ticker("엔비디아") == "NVDA"
    assert resolve_ticker("테슬라") == "TSLA"
    assert resolve_ticker("코인베이스") == "COIN"
    assert resolve_ticker("MSFT") == "MSFT"

    print("  - '애플' -> AAPL")
    print("  - '삼전' / '005930' -> 005930.KS")
    print("  - '대만반도체' -> TSM")
    print("  - '팔란티어' -> PLTR")
    print("  - '코인베이스' -> COIN")
    print("  -> PASSED: Multi-language alias resolution verified.")

def test_search_autocomplete_suggestions():
    print("\n[Test 2] Verifying Search Autocomplete Index...")
    
    sug_nvda = search_ticker_suggestions("엔비")
    assert any(s["ticker"] == "NVDA" for s in sug_nvda)
    print(f"  - Query '엔비': Found {len(sug_nvda)} suggestions -> Top: {sug_nvda[0]['ticker']} ({sug_nvda[0]['name_kr']})")

    sug_aapl = search_ticker_suggestions("Apple")
    assert any(s["ticker"] == "AAPL" for s in sug_aapl)
    print(f"  - Query 'Apple': Found {len(sug_aapl)} suggestions -> Top: {sug_aapl[0]['ticker']} ({sug_aapl[0]['name_en']})")

    sug_custom = search_ticker_suggestions("PLTR")
    assert any(s["ticker"] == "PLTR" for s in sug_custom)
    print(f"  - Query 'PLTR': Found {len(sug_custom)} suggestions -> Top: {sug_custom[0]['ticker']}")
    print("  -> PASSED: Autocomplete search engine verified.")

def test_dynamic_quant_scoring_on_searched_stock():
    print("\n[Test 3] Verifying Dynamic 17-Year Quant Scoring on Searched Stock (TSM)...")
    
    # Test on a newly searched stock outside original watchlist
    data = compute_all_indicators("TSM")
    assert data is not None
    assert "candles" in data and len(data["candles"]) > 0
    assert "kijun_line" in data and len(data["kijun_line"]) > 0
    assert "future_span_a_latest" in data
    assert "future_cloud_type" in data
    assert "intelligence" in data
    
    intel = data["intelligence"]
    assert "score" in intel
    assert "verdict" in intel
    assert "bull_score" in intel
    assert "action" in intel
    
    print(f"  - TSM Latest Close: ${data['latest_close']:,.2f}")
    print(f"  - 26-Day Kijun: ${data['kijun']:,.2f} (Gap: {data['kijun_gap_pct']:+.1f}%)")
    print(f"  - +26D Forward Cloud: {data['future_cloud_type']} (SpanA: ${data['future_span_a_latest']}, SpanB: ${data['future_span_b_latest']})")
    print(f"  - 17-Year Quant Rating: {intel['score']} ({intel['verdict']})")
    print(f"  - Tactical Directive: {intel['action']}")
    print("  -> PASSED: Dynamic 17-year quant rating engine verified.")

def test_server_search_and_chart_endpoints():
    print("\n[Test 4] Verifying Server /api/search & /api/chart Handlers...")
    
    # 1. /api/search
    search_res = search_stock_suggestions(q="삼성전자")
    assert len(search_res["suggestions"]) > 0
    print(f"  - GET /api/search?q=삼성전자: Top Suggestion {search_res['suggestions'][0]['ticker']}")

    # 2. /api/chart with alias
    chart_res = get_ticker_chart("애플")
    assert chart_res["ticker"] == "AAPL"
    assert "candles" in chart_res
    print(f"  - GET /api/chart/애플: Resolved to AAPL (${chart_res['latest_close']:,.2f}, Score: {chart_res['intelligence']['score']})")
    # 3. Non-existent ticker handling
    from fastapi import HTTPException
    try:
        get_ticker_chart("ZZZZZ99")
        assert False, "Expected 404 HTTPException for non-existent ticker"
    except HTTPException as e:
        assert e.status_code == 404
        print(f"  - GET /api/chart/ZZZZZ99: 404 Handled Cleanly ({e.detail})")

    print("  -> PASSED: Server search and dynamic chart endpoints verified.")

if __name__ == "__main__":
    print("======================================================================")
    print("  R-SANGMOO QUANT PLATFORM: STOCK SEARCH & DYNAMIC RATING TEST SUITE")
    print("======================================================================")
    
    test_smart_ticker_resolver()
    test_search_autocomplete_suggestions()
    test_dynamic_quant_scoring_on_searched_stock()
    test_server_search_and_chart_endpoints()
    
    print("\n======================================================================")
    print("  ALL STOCK SEARCH & DYNAMIC RATING TESTS PASSED! (100% GREEN)")
    print("======================================================================")
