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
MIN_DAILY_BARS = 250


def _daily_bar_count(data) -> int:
    if not isinstance(data, dict):
        return 0
    candles = data.get("candles")
    if isinstance(candles, list) and candles:
        return len(candles)
    tf = data.get("timeframes")
    daily = tf.get("daily") if isinstance(tf, dict) else None
    dc = daily.get("candles") if isinstance(daily, dict) else None
    return len(dc) if isinstance(dc, list) else 0


CHART_CACHE = {}
CACHE_TTL_SECONDS = 3.0


def _harmonize_live_price(live: float, ref: float | None) -> float | None:
    """
    Drop or rescale a live quote that would turn the last daily bar into a
    fake 장대양봉 (KIS integer ticks, KRW, allocation amounts, volume).
    """
    try:
        live = float(live)
    except (TypeError, ValueError):
        return None
    if live <= 0:
        return None
    try:
        ref = float(ref or 0)
    except (TypeError, ValueError):
        ref = 0.0
    if ref <= 0:
        return round(live, 2)
    ratio = live / ref
    if 0.85 <= ratio <= 1.15:
        return round(live, 2)
    if ratio > 1.15:
        for div in (10, 100, 1000, 10000):
            cand = live / div
            if cand > 0 and 0.85 <= (cand / ref) <= 1.15:
                return round(cand, 2)
    return None


def get_market_session_date(ticker_sym: str) -> str:
    """
    Determines active financial trading session date (Trade Date):
    - US stocks (NYSE/NASDAQ): New session begins at 09:30 America/New_York (DST-aware).
      Before 09:30 ET (overnight, pre-market), the active trade date is the previous completed session.
      After 09:30 ET, the active trade date is today.
      Prevents after-market quotes from prematurely creating tomorrow's empty candle bar!
    - Korean stocks (.KS/.KQ): Rolls over at 09:00 KST.
    """
    from al_sangmoo.core.market_time import session_date_for_ticker
    return session_date_for_ticker(ticker_sym)


def _enrich_chart_with_realtime_price(data: dict, ticker: str) -> dict:
    """
    Enriches modular chart data with official real-time price from KIS OpenAPI / live portfolio SSOT.
    Updates latest_close, active session candle (matching market trading session date), volume, and indicators.
    """
    if not isinstance(data, dict):
        return data

    session_date_str = get_market_session_date(ticker)

    live_price = None
    # 1. Fast in-memory check from SQLite live portfolio SSOT (< 0.1ms)
    try:
        from al_sangmoo.infrastructure.persistence import get_live_portfolio
        p = get_live_portfolio()
        for h in p.get("holdings", []):
            if isinstance(h, dict) and h.get("ticker", "").upper() == ticker:
                val = float(h.get("current_price", 0))
                if val > 0:
                    live_price = val
                    break
    except Exception:
        pass

    # Do not call KIS on this request path — 3 exchanges × 2 TRs × 3s stalls the chart.

    if (live_price is None or live_price <= 0) and isinstance(data.get("candles"), list) and data["candles"]:
        try:
            live_price = float(data["candles"][-1].get("close") or 0)
        except (TypeError, ValueError):
            live_price = None

    if live_price and live_price > 0:
        ref_close = None
        if isinstance(data.get("candles"), list) and data["candles"]:
            try:
                ref_close = float(data["candles"][-1].get("close") or 0)
            except (TypeError, ValueError):
                ref_close = None
        live_price = _harmonize_live_price(live_price, ref_close)

    if live_price and live_price > 0:
        live_price = round(float(live_price), 2)
        data["latest_close"] = live_price

        # Prune any mistakenly appended future candles beyond current session_date_str
        if "candles" in data and isinstance(data["candles"], list) and len(data["candles"]) > 0:
            data["candles"] = [c for c in data["candles"] if str(c.get("time", "")) <= session_date_str]
            if data["candles"]:
                last_c = dict(data["candles"][-1])
                last_date = str(last_c.get("time", ""))
                if last_date == session_date_str:
                    last_c["close"] = live_price
                    last_c["high"] = max(float(last_c.get("high", live_price)), live_price)
                    last_c["low"] = min(float(last_c.get("low", live_price)), live_price)
                    data["candles"][-1] = last_c
                elif last_date < session_date_str:
                    prev_close = float(last_c.get("close", live_price))
                    new_candle = {
                        "time": session_date_str,
                        "open": prev_close,
                        "high": max(prev_close, live_price),
                        "low": min(prev_close, live_price),
                        "close": live_price
                    }
                    data["candles"].append(new_candle)

        # Update and synchronize volume series
        if "volume" in data and isinstance(data["volume"], list) and len(data["volume"]) > 0:
            data["volume"] = [v for v in data["volume"] if str(v.get("time", "")) <= session_date_str]
            if data["volume"] and len(data.get("candles", [])) > 0:
                last_v = dict(data["volume"][-1])
                last_c = data["candles"][-1]
                last_v_date = str(last_v.get("time", ""))
                if last_v_date < session_date_str and str(last_c.get("time", "")) == session_date_str:
                    prev_open = float(last_c.get("open", live_price))
                    data["volume"].append({
                        "time": session_date_str,
                        "value": float(last_v.get("value", 1000000.0)),
                        "color": "#059669" if live_price >= prev_open else "#dc2626"
                    })

        # Update indicator lines to extend to session date (exclude forward cloud spans)
        for line_key in ["kijun_line", "tenkan_line", "sma20", "sma60"]:
            if line_key in data and isinstance(data[line_key], list) and len(data[line_key]) > 0:
                data[line_key] = [pt for pt in data[line_key] if str(pt.get("time", "")) <= session_date_str]
                if data[line_key]:
                    last_pt = dict(data[line_key][-1])
                    if str(last_pt.get("time", "")) < session_date_str:
                        data[line_key].append({
                            "time": session_date_str,
                            "value": last_pt.get("value", 0)
                        })

        # Update multi-timeframe candles and volume
        if "timeframes" in data and isinstance(data["timeframes"], dict):
            for tf_key, tf_data in data["timeframes"].items():
                if isinstance(tf_data, dict):
                    if "candles" in tf_data and len(tf_data["candles"]) > 0:
                        if tf_key == "daily":
                            tf_data["candles"] = [c for c in tf_data["candles"] if str(c.get("time", "")) <= session_date_str]
                            if tf_data["candles"]:
                                last_tf = dict(tf_data["candles"][-1])
                                last_tf_date = str(last_tf.get("time", ""))
                                if last_tf_date == session_date_str:
                                    last_tf["close"] = live_price
                                    last_tf["high"] = max(float(last_tf.get("high", live_price)), live_price)
                                    last_tf["low"] = min(float(last_tf.get("low", live_price)), live_price)
                                    tf_data["candles"][-1] = last_tf
                                elif last_tf_date < session_date_str:
                                    prev_c = float(last_tf.get("close", live_price))
                                    tf_data["candles"].append({
                                        "time": session_date_str,
                                        "open": prev_c,
                                        "high": max(prev_c, live_price),
                                        "low": min(prev_c, live_price),
                                        "close": live_price
                                    })
                        else:
                            last_tf = dict(tf_data["candles"][-1])
                            last_tf["close"] = live_price
                            last_tf["high"] = max(float(last_tf.get("high", live_price)), live_price)
                            last_tf["low"] = min(float(last_tf.get("low", live_price)), live_price)
                            tf_data["candles"][-1] = last_tf

                    if "volume" in tf_data and isinstance(tf_data["volume"], list) and len(tf_data["volume"]) > 0:
                        if tf_key == "daily":
                            tf_data["volume"] = [v for v in tf_data["volume"] if str(v.get("time", "")) <= session_date_str]
                            if tf_data["volume"] and len(tf_data.get("candles", [])) > 0:
                                last_v = dict(tf_data["volume"][-1])
                                last_c = tf_data["candles"][-1]
                                if str(last_v.get("time", "")) < session_date_str and str(last_c.get("time", "")) == session_date_str:
                                    prev_open = float(last_c.get("open", live_price))
                                    tf_data["volume"].append({
                                        "time": session_date_str,
                                        "value": float(last_v.get("value", 1000000.0)),
                                        "color": "#059669" if live_price >= prev_open else "#dc2626"
                                    })

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
    from al_sangmoo.core.constants import is_market_ticker
    if not is_market_ticker(ticker_clean):
        raise HTTPException(status_code=404, detail=f"'{ticker}' 종목의 시세 데이터를 불러올 수 없습니다.")

    now = time.time()
    # 1. In-memory fast cache with TTL — reject if the cached payload is a different ticker
    for cache_key in (ticker_clean, ticker_resolved):
        if cache_key in CHART_CACHE:
            entry = CHART_CACHE[cache_key]
            if isinstance(entry, dict) and "data" in entry and (now - entry.get("timestamp", 0) < CACHE_TTL_SECONDS):
                cached = entry["data"]
                cached_tk = str((cached or {}).get("ticker", "")).upper()
                if cached and (not cached_tk or cached_tk == ticker_clean):
                    return cached

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
                cached_data = json.load(f)
                if cached_data and cached_data.get("ticker", "").upper() == ticker_clean:
                    # Check for historical date gap (ensure previous completed market day exists)
                    candles = cached_data.get("candles", [])
                    if candles:
                        last_c_date = str(candles[-1].get("time", ""))
                        session_date = get_market_session_date(ticker_clean)
                        # If active session is today (e.g. 2026-08-27), last completed candle should be at least 2026-08-26
                        # If last candle is 2026-08-25 or older, there is a missing completed day gap -> recompute
                        from datetime import datetime, timedelta
                        try:
                            s_dt = datetime.strptime(session_date, "%Y-%m-%d")
                            prev_s_dt = s_dt - timedelta(days=1)
                            while prev_s_dt.weekday() >= 5:
                                prev_s_dt -= timedelta(days=1)
                            prev_session_str = prev_s_dt.strftime("%Y-%m-%d")
                            if last_c_date < prev_session_str:
                                cached_data = None  # Cache has missing completed day, trigger fresh compute
                        except Exception:
                            pass
                    if cached_data is not None and _daily_bar_count(cached_data) < MIN_DAILY_BARS:
                        cached_data = None
                    data = cached_data
        except Exception:
            data = None

    # Try resolved ticker file if different
    if not data and ticker_resolved != ticker_clean:
        alt_file = os.path.abspath(os.path.join(CHARTS_DIR, f"{ticker_resolved}.json"))
        if os.path.exists(alt_file) and alt_file.startswith(charts_dir_abs + os.sep):
            try:
                with open(alt_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                loaded_tk = str((loaded or {}).get("ticker", "")).upper()
                if loaded and (not loaded_tk or loaded_tk in (ticker_clean, str(ticker_resolved).upper())):
                    if _daily_bar_count(loaded) < MIN_DAILY_BARS:
                        data = None
                    else:
                        data = loaded
                else:
                    data = None
            except Exception:
                data = None

    # 3. Live On-Demand Compute Fallback (for newly searched stocks not in pre-computed files)
    if not data:
        data = await asyncio.to_thread(compute_all_indicators, ticker_clean)
        if not data and ticker_resolved != ticker_clean:
            data = await asyncio.to_thread(compute_all_indicators, ticker_resolved)
        if not data:
            def _kis_chart(sym: str):
                try:
                    from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
                    ohlcv = default_kis_broker.get_daily_ohlcv(sym)
                    if ohlcv is None:
                        return None
                    return compute_all_indicators(sym, df=ohlcv)
                except Exception:
                    return None
            data = await asyncio.to_thread(_kis_chart, ticker_clean)
            if not data and ticker_resolved != ticker_clean:
                data = await asyncio.to_thread(_kis_chart, ticker_resolved)
        if data:
            new_n = _daily_bar_count(data)
            old_n = 0
            old_payload = None
            if os.path.exists(chart_file):
                try:
                    with open(chart_file, "r", encoding="utf-8") as f:
                        old_payload = json.load(f)
                    old_n = _daily_bar_count(old_payload)
                except Exception:
                    old_payload = None
            if new_n >= MIN_DAILY_BARS and new_n >= old_n:
                try:
                    atomic_save_json(chart_file, data)
                except Exception:
                    pass
            elif old_n >= MIN_DAILY_BARS and new_n < old_n and old_payload:
                data = old_payload

    if not data:
        raise HTTPException(status_code=404, detail=f"'{ticker}' ({ticker_resolved}) 종목의 시세 데이터를 불러올 수 없습니다.")

    # 4. Enrich with official real-time price from KIS OpenAPI (off the event loop)
    try:
        data = await asyncio.to_thread(_enrich_chart_with_realtime_price, data, ticker_clean)
    except Exception:
        pass

    # LRU eviction (max 150 items)
    if len(CHART_CACHE) > 150:
        first_key = next(iter(CHART_CACHE))
        del CHART_CACHE[first_key]

    cache_payload = {"data": data, "timestamp": now}
    CHART_CACHE[ticker_clean] = cache_payload
    if ticker_resolved != ticker_clean:
        CHART_CACHE[ticker_resolved] = {"data": data, "timestamp": now}
    return data
