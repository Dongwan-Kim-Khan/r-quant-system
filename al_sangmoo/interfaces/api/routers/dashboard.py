import os
import json
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from al_sangmoo.core.constants import derive_stop_price, derive_target_price, is_market_ticker

logger = logging.getLogger(__name__)

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
    except Exception as e:
        logger.exception("Failed to fetch realtime macro gauges: %s", e)
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
        except Exception as e:
            logger.exception("Failed to read or parse dashboard_data.json: %s", e)
    return FEED_CACHE

def load_feed_cache():
    return get_feed_cache()


@router.get("/api/execution-logs")
async def get_dashboard_execution_logs(limit: int = 50):
    """Lightweight real-time audit feed used while WebSocket mode is active."""
    from al_sangmoo.infrastructure.persistence import get_execution_logs

    safe_limit = max(1, min(int(limit), 300))
    return {"execution_logs": get_execution_logs(limit=safe_limit)}


@router.get("/api/dashboard")
async def get_dashboard_data():
    """Returns the full executive dashboard data payload enriched with real-time portfolio & macro in < 1ms."""
    feed = get_feed_cache()
    if not feed:
        if os.path.exists(DASHBOARD_JSON):
            try:
                with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
                    feed = json.load(f)
            except Exception as e:
                logger.exception("Failed to load dashboard_data.json fallback: %s", e)
                feed = None
    if not feed:
        raise HTTPException(status_code=503, detail="대시보드 피드 데이터가 아직 생성되지 않았습니다.")
        
    # Shallow copy dictionary for high-performance non-mutating response
    feed_out = dict(feed)

    try:
        from al_sangmoo.domain.quant.dynamic_universe import load_universe_sectors
        live_sectors = load_universe_sectors()
        if live_sectors:
            feed_out["universe_sectors"] = live_sectors
        else:
            feed_out.setdefault("universe_sectors", [])
    except Exception as e:
        logger.exception("Failed to load universe sectors: %s", e)
        feed_out["universe_sectors_error"] = "섹터 데이터를 불러오지 못했습니다"
        feed_out.pop("universe_sectors", None)
    
    # Inject real-time macro gauges & dynamically re-evaluate macro climate (30s TTL cache)
    try:
        live_gauges = get_live_macro_gauges()
        if live_gauges and isinstance(live_gauges, dict):
            macro_obj = dict(feed_out["macro"]) if isinstance(feed_out.get("macro"), dict) else {}
            macro_obj["macro_gauges"] = live_gauges

            # Dynamically re-evaluate live MSI score & headline using real-time market gauges
            from al_sangmoo.domain.quant.macro import evaluate_macro_stance
            current_climate = macro_obj.get("macro_climate") or {}
            breakdown = current_climate.get("msi_breakdown") or {}
            if breakdown:
                live_climate = evaluate_macro_stance(
                    gauges=live_gauges,
                    defense_count=int(breakdown.get("def_count", 0)),
                    buy_count=int(breakdown.get("buy_count", 0)),
                    matched_shocks=current_climate.get("external_shocks") or ["금리 경로 및 통화정책 영향권"],
                    title=macro_obj.get("title", "")
                )
                if live_climate and isinstance(live_climate, dict):
                    macro_obj["macro_climate"] = live_climate
                    macro_obj["msi_score"] = live_climate.get("msi_score")
                    macro_obj["msi_stance"] = live_climate.get("macro_stance")

            feed_out["macro"] = macro_obj
            feed_out.pop("macro_error", None)
        else:
            feed_out["macro_error"] = "매크로 게이지를 불러오지 못했습니다"
    except Exception as e:
        logger.exception("Failed to evaluate live macro gauges: %s", e)
        feed_out["macro_error"] = "매크로 게이지 분석 실패"

    # Inject real-time live portfolio from SQLite SSOT (pure CQRS fast read < 0.5ms)
    try:
        from al_sangmoo.infrastructure.persistence import get_execution_logs, get_live_portfolio
        live_portfolio = get_live_portfolio()
        if isinstance(live_portfolio, dict) and isinstance(live_portfolio.get("holdings"), list):
            live_portfolio = dict(live_portfolio)
            live_portfolio["holdings"] = [
                h for h in live_portfolio["holdings"]
                if isinstance(h, dict) and is_market_ticker(h.get("ticker"))
            ]
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
                    item_copy["target_price"] = derive_target_price(cur_p)
                    item_copy["stop_price"] = derive_stop_price(cur_p)
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

        # 3. Synchronize slot_allocation_summary with live portfolio SSOT
        slot_sum = dict(feed_out.get("slot_allocation_summary") or {})
        from al_sangmoo.domain.risk.cash_proxy import satellite_holdings, proxy_holdings
        sats = satellite_holdings(holdings)
        proxies = proxy_holdings(holdings)
        slot_sum["satellite_count"] = len(sats)
        slot_sum["proxy_holdings"] = [
            {"ticker": h.get("ticker"), "quantity": h.get("quantity"), "current_price": h.get("current_price")}
            for h in proxies
        ]
        if live_portfolio.get("total_equity_usd"):
            slot_sum["equity_usd"] = float(live_portfolio["total_equity_usd"])
        macro = feed_out.get("macro", {})
        if "is_bull_regime" in macro:
            slot_sum["is_bull_regime"] = bool(macro["is_bull_regime"])
        elif "is_bull_regime" not in slot_sum:
            from al_sangmoo.domain.quant.macro import extract_msi_score, resolve_capital_regime
            msi = extract_msi_score(macro, default=50.0)
            spy_c = macro.get("spy_close")
            spy_s = macro.get("spy_sma200")
            regime = resolve_capital_regime(
                msi_score=msi,
                spy_close=float(spy_c) if spy_c is not None else None,
                spy_sma200=float(spy_s) if spy_s is not None else None,
                fetch_spy=False,
            )
            slot_sum["is_bull_regime"] = bool(regime["is_bull_regime"])
        feed_out["slot_allocation_summary"] = slot_sum

        feed_out["portfolio_source"] = "sqlite"
        feed_out.pop("portfolio_error", None)
    except Exception as e:
        logger.exception("Failed to inject live portfolio: %s", e)
        feed_out["portfolio_source"] = "feed_cache"
        feed_out["portfolio_error"] = "실시간 원장을 읽지 못했습니다"

    # 4. Inject Execution Audit Logs
    try:
        from al_sangmoo.infrastructure.persistence import get_execution_logs
        feed_out["execution_logs"] = get_execution_logs(limit=300)
        feed_out.pop("execution_logs_error", None)
    except Exception as e:
        logger.exception("Failed to load execution logs for dashboard: %s", e)
        feed_out["execution_logs_error"] = True
        feed_out.pop("execution_logs", None)

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
    feed_ok = False
    db_ok = False

    try:
        if os.path.exists(DASHBOARD_JSON):
            with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
                json.load(f)
            feed_ok = True
    except Exception as e:
        logger.exception("Health check: dashboard feed file corrupt or unreadable: %s", e)

    try:
        from al_sangmoo.infrastructure.persistence import get_live_portfolio
        get_live_portfolio()
        db_ok = True
    except Exception as e:
        logger.exception("Health check: database query failed: %s", e)

    if not (feed_ok and db_ok):
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "detail": "시스템 상태가 비정상입니다.",
                "feed": "ok" if feed_ok else "error",
                "database": "ok" if db_ok else "error",
            }
        )

    from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
    return {
        "status": "ok",
        "feed": "ok",
        "database": "ok",
        "broker_configured": bool(default_kis_broker.is_configured())
    }
