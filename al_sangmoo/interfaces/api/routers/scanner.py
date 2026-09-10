import sys
import asyncio
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from al_sangmoo.api.hub import hub
from al_sangmoo.core.auth import require_mutating_auth
from al_sangmoo.domain.quant.ticker_resolver import search_ticker_suggestions
from al_sangmoo.infrastructure.persistence import (
    get_recommendations_matrix,
    save_recommendation_matrix_record,
)
import generate_dashboard_feed

router = APIRouter(tags=["Scanner"])

_is_scanning: bool = False
_scan_lock = asyncio.Lock()

def get_is_scanning() -> bool:
    global _is_scanning
    if "server" in sys.modules:
        s = sys.modules["server"]
        if hasattr(s, "_is_scanning"):
            return getattr(s, "_is_scanning")
    return _is_scanning

def set_is_scanning(val: bool):
    global _is_scanning
    _is_scanning = val
    if "server" in sys.modules:
        s = sys.modules["server"]
        setattr(s, "_is_scanning", val)


def _picks_from_feed(data: dict, *keys: str) -> list:
    if not isinstance(data, dict):
        return []
    for key in keys:
        val = data.get(key)
        if isinstance(val, list) and val:
            return val
    return []


def persist_v2_recommendation_matrix(today_str: str, data: dict) -> None:
    """Store Tier-1 / Tier-2 in the historical 2+2 matrix. Snipers are not bears."""
    tier1 = _picks_from_feed(data, "tier1", "dual_consensus")
    tier2 = _picks_from_feed(data, "tier2", "strat1_exclusive")
    save_recommendation_matrix_record(today_str, tier1[:2], tier2[:2], [])


async def _run_background_scan_pipeline():
    try:
        set_is_scanning(True)
        await hub.broadcast("scan_status", {
            "status": "started",
            "message": "백그라운드 3-Gate 스캔이 시작되었습니다."
        })
        
        def _sync_worker():
            # Single v2 pipeline: 3y Ichimoku + 3-Gate conviction. Do not call v1 6mo scan.
            server_mod = sys.modules.get("server")
            build_fn = getattr(server_mod, "build_dashboard_data", generate_dashboard_feed.build_dashboard_data)
            data = build_fn() or {}
            today_str = datetime.now().strftime("%Y-%m-%d")
            persist_v2_recommendation_matrix(today_str, data)
            macro = data.get("macro") if isinstance(data.get("macro"), dict) else {}
            macro_climate = macro.get("macro_climate") if isinstance(macro.get("macro_climate"), dict) else macro
            return today_str, data, macro_climate or {}

        today_str, data, macro_climate = await asyncio.to_thread(_sync_worker)
        
        await hub.broadcast("live_feed_update", data)
        await hub.broadcast("scan_status", {
            "status": "completed",
            "message": f"{today_str} 실시간 3-Gate 스캔 & 대시보드 갱신 완료!",
            "macro_stance": macro_climate.get("macro_stance") if macro_climate else "NORMAL"
        })
    except Exception as exc:
        print(f"[Background Scan Error] {exc}", file=sys.stderr)
        await hub.broadcast("scan_status", {
            "status": "error",
            "message": "스캔 실행 중 내부 오류가 발생했습니다."
        })
    finally:
        async with _scan_lock:
            set_is_scanning(False)

@router.post("/api/scan_now", dependencies=[Depends(require_mutating_auth)])
async def trigger_scan_now():
    """Triggers an on-demand live 3-Gate market scan asynchronously."""
    async with _scan_lock:
        if get_is_scanning():
            return {
                "status": "already_scanning",
                "is_scanning": True,
                "message": "이미 시장 스캔 작업이 백그라운드에서 진행 중입니다. 완료 후 다시 시도해주세요."
            }
        set_is_scanning(True)

    asyncio.create_task(_run_background_scan_pipeline())
    return {
        "status": "scanning_started",
        "is_scanning": True,
        "message": "백그라운드 스캔 태스크가 정상적으로 접수되었습니다. 완료 시 실시간 브로드캐스트됩니다."
    }

@router.get("/api/search")
def search_stock_suggestions(q: str = ""):
    """Universal universe search returning ticker and Korean stock name suggestions."""
    suggestions = search_ticker_suggestions(q, limit=8)
    return {"query": q, "results": suggestions, "suggestions": suggestions}

@router.get("/api/recommendations/matrix")
def get_recommendation_matrix():
    """Returns today's 2+2+2 quantitative recommendation matrix."""
    matrix = get_recommendations_matrix()
    if not isinstance(matrix, list):
        matrix = [matrix] if matrix else []
    return matrix


@router.get("/api/universe/dynamic")
def get_dynamic_universe_status():
    """Returns the current dynamic sector-weighted universe snapshot and sector momentum."""
    try:
        from al_sangmoo.domain.quant.dynamic_universe import build_dynamic_60_watchlist
        snapshot = build_dynamic_60_watchlist(force_refresh=False)
        return {"status": "success", "data": snapshot}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"동적 유니버스 조회 실패: {exc}")
