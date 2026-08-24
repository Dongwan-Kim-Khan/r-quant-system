import os
import json
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["Dashboard"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
DASHBOARD_JSON = os.path.join(BASE_DIR, "dashboard_data.json")

FEED_CACHE = {}
LAST_FEED_MTIME = 0

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
def load_feed_cache():
    return get_feed_cache()

@router.get("/api/dashboard")
async def get_dashboard_data():
    """Returns the full executive dashboard data payload (sub-millisecond from memory/disk)."""
    feed = get_feed_cache()
    if not feed:
        if os.path.exists(DASHBOARD_JSON):
            with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
                feed = json.load(f)
    if not feed:
        raise HTTPException(status_code=503, detail="대시보드 피드 데이터가 아직 생성되지 않았습니다.")
    return feed

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
