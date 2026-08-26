import os
import json
import re
import time
import asyncio
from fastapi import APIRouter, HTTPException
from al_sangmoo.domain.quant.ticker_resolver import resolve_ticker
from generate_dashboard_feed import compute_all_indicators, atomic_save_json

router = APIRouter(tags=["Charts"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
CHARTS_DIR = os.path.join(BASE_DIR, "data", "charts")

CHART_CACHE = {}
CACHE_TTL_SECONDS = 3.0


def _enrich_chart_with_realtime_price(data: dict, ticker: str) -> dict:
    """
    Enriches modular chart data with official real-time price from KIS OpenAPI / live portfolio SSOT.
    Updates latest_close, the most recent candle's close/high/low, Kijun gap %, and signal intelligence.
    """
    if not isinstance(data, dict):
        return data

    live_price = None
    # 1. Fast in-memory check from SQLite live portfolio SSOT (< 0.1ms)
    try:
        import db_manager
        p = db_manager.get_live_portfolio()
        for h in p.get("holdings", []):
            if isinstance(h, dict) and h.get("ticker", "").upper() == ticker:
                val = float(h.get("current_price", 0))
                if val > 0:
                    live_price = val
                    break
    except Exception:
        pass

    # 2. Query official KIS Broker Live Price TR / fast-info quote
    if live_price is None or live_price <= 0:
        try:
            from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
            live_price = default_kis_broker.get_live_price(ticker)
        except Exception:
            pass

    if live_price and live_price > 0:
        live_price = round(float(live_price), 2)
        data["latest_close"] = live_price

        # Update daily candles
        if "candles" in data and isinstance(data["candles"], list) and len(data["candles"]) > 0:
            last_c = dict(data["candles"][-1])
            last_c["close"] = live_price
            last_c["high"] = max(float(last_c.get("high", live_price)), live_price)
            last_c["low"] = min(float(last_c.get("low", live_price)), live_price)
            data["candles"][-1] = last_c

        # Update multi-timeframe candles
        if "timeframes" in data and isinstance(data["timeframes"], dict):
            for tf_key, tf_data in data["timeframes"].items():
                if isinstance(tf_data, dict) and "candles" in tf_data and len(tf_data["candles"]) > 0:
                    last_tf = dict(tf_data["candles"][-1])
                    last_tf["close"] = live_price
                    last_tf["high"] = max(float(last_tf.get("high", live_price)), live_price)
                    last_tf["low"] = min(float(last_tf.get("low", live_price)), live_price)
                    tf_data["candles"][-1] = last_tf

        # Update Kijun line gap & narrative
        kijun_val = float(data.get("kijun", 0))
        if kijun_val > 0:
            gap = round(((live_price - kijun_val) / kijun_val) * 100, 2)
            data["kijun_gap_pct"] = gap
            if "intelligence" in data and isinstance(data["intelligence"], dict):
                kijun_info = data["intelligence"].get("kijun")
                if isinstance(kijun_info, dict):
                    sign = "+" if gap >= 0 else ""
                    kijun_info["desc"] = f"현재가 ${live_price:.2f} / 26일선 ${kijun_val:.2f} (이격 {sign}{gap:.1f}%)"
                    kijun_info["val"] = f"${kijun_val:.2f} ({sign}{gap:.1f}%)"

    return data


@router.get("/api/chart/{ticker}")
@router.get("/api/charts/{ticker}")
async def get_chart_data(ticker: str):
    """
    Returns high-performance modular chart data for a specific ticker.
    1. In-memory fast cache with 3s TTL
    2. Individual JSON file cache (data/charts/{ticker}.json)
    3. Real-time price enrichment from KIS OpenAPI
    4. On-demand async compute fallback (via asyncio.to_thread)
    """
    global CHART_CACHE
    raw_query = ticker.strip()
    if not raw_query or len(raw_query) > 15 or ".." in raw_query or "/" in raw_query or "\\" in raw_query:
        raise HTTPException(status_code=400, detail="유효하지 않은 종목 티커입니다.")
    
    if not re.match(r'^[A-Za-z0-9가-힣.\^=-]{1,15}$', raw_query):
        raise HTTPException(status_code=400, detail="유효하지 않은 종목 티커입니다.")
    
    ticker_resolved = resolve_ticker(raw_query)
    ticker_clean = re.sub(r'[^A-Za-z0-9.\^=-]', '', ticker_resolved).strip().upper()
    if not ticker_clean:
        raise HTTPException(status_code=400, detail="유효하지 않은 종목 티커입니다.")

    now = time.time()
    # 1. In-memory fast cache with TTL
    if ticker_clean in CHART_CACHE:
        entry = CHART_CACHE[ticker_clean]
        if isinstance(entry, dict) and "data" in entry and (now - entry.get("timestamp", 0) < CACHE_TTL_SECONDS):
            return entry["data"]
    if ticker_resolved in CHART_CACHE:
        entry = CHART_CACHE[ticker_resolved]
        if isinstance(entry, dict) and "data" in entry and (now - entry.get("timestamp", 0) < CACHE_TTL_SECONDS):
            return entry["data"]

    chart_file = os.path.abspath(os.path.join(CHARTS_DIR, f"{ticker_clean}.json"))
    charts_dir_abs = os.path.abspath(CHARTS_DIR)

    # Path traversal protection
    if not chart_file.startswith(charts_dir_abs + os.sep) and chart_file != charts_dir_abs:
        raise HTTPException(status_code=400, detail="유효하지 않은 차트 파일 경로입니다.")

    data = None
    # 2. Modular JSON file cache (< 1ms read)
    if os.path.exists(chart_file):
        try:
            with open(chart_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data and data.get("ticker", "").upper() != ticker_clean:
                    data = None
        except Exception:
            data = None

    # Try resolved ticker file if different
    if not data and ticker_resolved != ticker_clean:
        alt_file = os.path.abspath(os.path.join(CHARTS_DIR, f"{ticker_resolved}.json"))
        if os.path.exists(alt_file) and alt_file.startswith(charts_dir_abs + os.sep):
            try:
                with open(alt_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = None

    # 3. Live On-Demand Compute Fallback (for newly searched stocks not in pre-computed files)
    if not data:
        data = await asyncio.to_thread(compute_all_indicators, ticker_clean)
        if not data and ticker_resolved != ticker_clean:
            data = await asyncio.to_thread(compute_all_indicators, ticker_resolved)
        if data:
            try:
                atomic_save_json(chart_file, data)
            except Exception:
                pass

    if not data:
        raise HTTPException(status_code=404, detail=f"'{ticker}' ({ticker_resolved}) 종목의 시세 데이터를 불러올 수 없습니다.")

    # 4. Enrich with official real-time price from KIS OpenAPI
    try:
        data = _enrich_chart_with_realtime_price(data, ticker_clean)
    except Exception:
        pass

    # LRU eviction (max 150 items)
    if len(CHART_CACHE) > 150:
        first_key = next(iter(CHART_CACHE))
        del CHART_CACHE[first_key]

    cache_payload = {"data": data, "timestamp": now}
    CHART_CACHE[ticker_clean] = cache_payload
    CHART_CACHE[ticker_resolved] = cache_payload
    return data
