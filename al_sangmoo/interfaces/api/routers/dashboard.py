import os
import json
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["Dashboard"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
DASHBOARD_JSON = os.path.join(BASE_DIR, "dashboard_data.json")

FEED_CACHE = {}
LAST_FEED_MTIME = 0
LAST_MACRO_TIME = 0.0
CACHED_MACRO_GAUGES = None

def get_live_macro_gauges():
    global LAST_MACRO_TIME, CACHED_MACRO_GAUGES
    import time
    now = time.time()
    if CACHED_MACRO_GAUGES and (now - LAST_MACRO_TIME < 30.0):
        return CACHED_MACRO_GAUGES
    try:
        import youtube_stream_scanner
        gauges = youtube_stream_scanner.fetch_realtime_macro_gauges()
        if gauges and isinstance(gauges, dict):
            CACHED_MACRO_GAUGES = gauges
            LAST_MACRO_TIME = now
            return CACHED_MACRO_GAUGES
    except Exception:
        pass
    return CACHED_MACRO_GAUGES

def get_feed_cache():
    global FEED_CACHE, LAST_FEED_MTIME
    if os.path.exists(DASHBOARD_JSON):
        try:
            mtime = os.path.getmtime(DASHBOARD_JSON)
            if mtime != LAST_FEED_MTIME or not FEED_CACHE:
                with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
                    FEED_CACHE = json.load(f)
                LAST_FEED_MTIME = mtime
        except Exception:
            pass
    return FEED_CACHE

def load_feed_cache():
    return get_feed_cache()

@router.get("/api/dashboard")
async def get_dashboard_data():
    """Returns the full executive dashboard data payload enriched with real-time portfolio & macro in < 1ms."""
    feed = get_feed_cache()
    if not feed:
        if os.path.exists(DASHBOARD_JSON):
            try:
                with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
                    feed = json.load(f)
            except Exception:
                feed = None
    if not feed:
        raise HTTPException(status_code=503, detail="대시보드 피드 데이터가 아직 생성되지 않았습니다.")
        
    # Shallow copy dictionary for high-performance non-mutating response
    feed_out = dict(feed)
    
    # Inject real-time macro gauges (30s TTL cache)
    try:
        live_gauges = get_live_macro_gauges()
        if live_gauges and isinstance(feed_out.get("macro"), dict):
            feed_out["macro"] = dict(feed_out["macro"])
            feed_out["macro"]["macro_gauges"] = live_gauges
    except Exception:
        pass

    # Inject real-time live portfolio from SQLite SSOT (pure CQRS fast read < 0.5ms)
    try:
        import db_manager
        live_portfolio = db_manager.get_live_portfolio()
        feed_out["portfolio"] = live_portfolio

        # Build unified live price map across holdings and broker
        holdings = live_portfolio.get("holdings", [])
        wallet_map = {h["ticker"].upper(): h for h in holdings if isinstance(h, dict) and "ticker" in h}
        live_price_map = {h["ticker"].upper(): float(h["current_price"]) for h in holdings if isinstance(h, dict) and "ticker" in h and h.get("current_price")}
        
        def update_item_live_price(item):
            if not isinstance(item, dict):
                return item
            tk = str(item.get("ticker", "")).upper()
            if not tk:
                return item
            
            item_copy = dict(item)
            # 1. Enrich wallet status
            if tk in wallet_map:
                h = wallet_map[tk]
                item_copy["in_wallet"] = True
                item_copy["holding_pnl"] = float(h.get("pnl_pct", 0.0))
                item_copy["holding_qty"] = float(h.get("quantity", 0.0))
                item_copy["holding_price"] = float(h.get("buy_price", 0.0))
                if h.get("current_price"):
                    cur_p = float(h["current_price"])
                    item_copy["price"] = cur_p
                    item_copy["target_price"] = round(cur_p * 1.15, 2)
                    item_copy["stop_price"] = round(cur_p * 0.96, 2)
            else:
                item_copy["in_wallet"] = False
                item_copy["holding_pnl"] = 0.0
                item_copy["holding_qty"] = 0.0
                item_copy["holding_price"] = 0.0
                
            return item_copy

        # 2. Enrich Top Conviction Picks
        if "top_conviction_pick" in feed_out and feed_out["top_conviction_pick"]:
            feed_out["top_conviction_pick"] = update_item_live_price(feed_out["top_conviction_pick"])
        if "top_conviction_runner_up" in feed_out and feed_out["top_conviction_runner_up"]:
            feed_out["top_conviction_runner_up"] = update_item_live_price(feed_out["top_conviction_runner_up"])

        # 4. Inject Execution Audit Logs
        try:
            feed_out["execution_logs"] = db_manager.get_execution_logs(limit=50)
        except Exception:
            feed_out["execution_logs"] = []
    except Exception as e:
        # Fallback gracefully to feed cached portfolio if DB query fails
        pass
        
    return feed_out

@router.get("/api/summary")
async def get_dashboard_summary():
    """Returns a lightweight summary of KPIs and active macro stance."""
    feed = get_feed_cache()
    return {
        "kpis": feed.get("kpis", {}),
        "macro": feed.get("macro", {}),
        "active_signals_count": len(feed.get("signal_tracker", [])),
        "last_updated": feed.get("last_updated")
    }

@router.get("/api/health")
async def health_check():
    """System health check endpoint."""
    return {"status": "ok", "service": "al_sangmoo_quant_terminal", "version": "2.6"}
