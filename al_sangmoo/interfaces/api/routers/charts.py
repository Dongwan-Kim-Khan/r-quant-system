import os
import json
from fastapi import APIRouter, HTTPException
from al_sangmoo.domain.quant.ticker_resolver import resolve_ticker
from generate_dashboard_feed import compute_all_indicators, atomic_save_json

router = APIRouter(tags=["Charts"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
CHARTS_DIR = os.path.join(BASE_DIR, "data", "charts")

CHART_CACHE = {}

@router.get("/api/chart/{ticker}")
async def get_chart_data(ticker: str):
    """
    Returns high-performance modular chart data for a specific ticker.
    1. In-memory fast cache
    2. Individual JSON file cache (data/charts/{ticker}.json)
    3. On-demand compute fallback
    """
    global CHART_CACHE
    ticker_resolved = resolve_ticker(ticker)
    chart_file = os.path.abspath(os.path.join(CHARTS_DIR, f"{ticker_resolved}.json"))
    charts_dir_abs = os.path.abspath(CHARTS_DIR)

    # Path traversal protection
    if not chart_file.startswith(charts_dir_abs):
        raise HTTPException(status_code=400, detail="유효하지 않은 차트 파일 경로입니다.")

    # 1. In-memory fast cache
    if ticker_resolved in CHART_CACHE:
        return CHART_CACHE[ticker_resolved]

    # 2. Modular JSON file cache
    if os.path.exists(chart_file):
        try:
            with open(chart_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                CHART_CACHE[ticker_resolved] = data
                return data
        except Exception:
            pass

    # 3. Live On-Demand Compute Fallback
    data = compute_all_indicators(ticker_resolved)
    if not data:
        raw_upper = ticker.strip().upper()
        if raw_upper != ticker_resolved:
            data = compute_all_indicators(raw_upper)
            if data:
                ticker_resolved = raw_upper

    if not data:
        raise HTTPException(status_code=404, detail=f"'{ticker}' ({ticker_resolved}) 종목의 시세 데이터를 불러올 수 없습니다.")

    # Cache locally
    try:
        os.makedirs(CHARTS_DIR, exist_ok=True)
        atomic_save_json(chart_file, data)
    except Exception:
        pass

    # LRU eviction (max 150 items)
    if len(CHART_CACHE) > 150:
        first_key = next(iter(CHART_CACHE))
        del CHART_CACHE[first_key]

    CHART_CACHE[ticker_resolved] = data
    return data
