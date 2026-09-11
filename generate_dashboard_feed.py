import os
import sys
import time
import json
import hashlib
import tempfile
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta, timezone
from al_sangmoo.infrastructure.persistence import (
    get_connection,
    get_daily_recommendation_history,
    get_live_portfolio,
    get_recommendation_streaks,
    init_database,
)
from al_sangmoo.infrastructure.atomic_io import atomic_save_json, atomic_read_json
from al_sangmoo.core.market_time import now_us_eastern

# Windows encoding fix
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_JSON = os.path.join(BASE_DIR, "dashboard_data.json")
CONFIRMED_EOD_JSON = os.path.join(BASE_DIR, "dashboard_data.eod.json")
STREAM_CACHE = os.path.join(BASE_DIR, "wepoll_latest_stream.json")
CHARTS_DIR = os.path.join(BASE_DIR, "data", "charts")

from concurrent.futures import ThreadPoolExecutor
from al_sangmoo.core.constants import (
    CASH_PROXY_TICKER,
    HARD_STOP_PCT,
    SLOT_WEIGHTS_BEAR,
    SLOT_WEIGHTS_BULL,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TRAILING_ACTIVATE_PCT,
    WATCHLIST,
    STOCK_DICT,
    TICKER_SECTORS,
    get_active_watchlist,
    get_macro_tailwind_sectors,
)
from al_sangmoo.domain.quant.ichimoku import (
    calculate_ichimoku_indicators,
    detect_cloud_trampoline_bounce,
    compute_institutional_flow_indicators,
    build_ichimoku_series_payload
)
from al_sangmoo.domain.quant.scoring import (
    evaluate_quant_score,
    classify_3tier_candidates,
    latest_composite_rs,
)
from al_sangmoo.domain.quant.conviction_engine import rank_and_select_top_picks
from al_sangmoo.domain.quant.macro import extract_msi_score, resolve_capital_regime, fetch_spy_trend_regime
from al_sangmoo.domain.quant.dynamic_universe import load_universe_sectors
from al_sangmoo.domain.risk.cash_proxy import proxy_holdings, satellite_holdings
from al_sangmoo.domain.risk.macro_guardrail import evaluate_dynamic_leverage


def derive_signal_provenance(
    chart_data,
    ranked_candidates,
    signal_clock_et=None,
):
    """Prove that every ranked signal was computed from one closed daily bar."""
    clock = now_us_eastern(signal_clock_et)
    source_rows = []
    seen = set()
    candidates = list(ranked_candidates or [])
    strict_ranked_sources = bool(candidates)
    if not candidates:
        candidates = [{"ticker": ticker} for ticker in sorted(chart_data)]
    for candidate in candidates:
        ticker = str((candidate or {}).get("ticker") or "").upper().strip()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        candles = (chart_data.get(ticker) or {}).get("candles") or []
        if not candles:
            if strict_ranked_sources:
                return {
                    "session_date": "",
                    "is_confirmed": False,
                    "source_hash": "",
                    "source_tickers": sorted(seen),
                    "source_rows": [],
                }
            continue
        dict_candles = [bar for bar in candles if isinstance(bar, dict)]
        last_bar = max(
            dict_candles,
            key=lambda bar: str(bar.get("time") or bar.get("date") or ""),
            default={},
        )
        bar_date = str(
            last_bar.get("time") or last_bar.get("date") or ""
        )[:10]
        if len(bar_date) != 10:
            return {
                "session_date": "",
                "is_confirmed": False,
                "source_hash": "",
                "source_tickers": sorted(seen),
                "source_rows": [],
            }
        source_rows.append({
            "ticker": ticker,
            "bar_date": bar_date,
            "close": float(last_bar.get("close") or 0.0),
        })

    source_dates = [row["bar_date"] for row in source_rows]
    if strict_ranked_sources:
        distinct_dates = set(source_dates)
        source_session = (
            next(iter(distinct_dates)) if len(distinct_dates) == 1 else ""
        )
    else:
        source_session = (
            max(set(source_dates), key=source_dates.count)
            if source_dates
            else ""
        )
        agreeing = sum(
            1 for row in source_rows
            if row["bar_date"] == source_session
        )
        if not source_rows or agreeing / len(source_rows) < 0.8:
            source_session = ""
        source_rows = [
            row for row in source_rows
            if row["bar_date"] == source_session
        ]
    minute = clock.hour * 60 + clock.minute
    current_date = clock.date().isoformat()
    if source_session and source_session < current_date:
        clock_confirms_close = True
    elif source_session and source_session == current_date and minute >= (16 * 60 + 15):
        clock_confirms_close = True
    else:
        clock_confirms_close = False
    is_confirmed = bool(source_rows and source_session and clock_confirms_close)
    canonical = json.dumps(
        sorted(source_rows, key=lambda row: row["ticker"]),
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "session_date": source_session,
        "is_confirmed": is_confirmed,
        "source_hash": (
            hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if source_rows
            else ""
        ),
        "source_tickers": sorted(seen),
        "source_rows": sorted(source_rows, key=lambda row: row["ticker"]),
    }


def compute_all_indicators(ticker, df=None):
    try:
        if df is None:
            end = datetime.now()
            start = end - timedelta(days=365 * 3 + 45)
            df = yf.download(
                ticker,
                start=start.strftime("%Y-%m-%d"),
                end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=False,
            )
        else:
            df = df.copy()
        if isinstance(df.columns, pd.MultiIndex):
            if 'Close' in df.columns.get_level_values(0):
                df.columns = df.columns.get_level_values(0)
            elif 'Close' in df.columns.get_level_values(1):
                df.columns = df.columns.get_level_values(1)
            
        df = df.dropna(subset=['Close', 'High', 'Low', 'Volume']).copy()
        if df is None or len(df) < 60:
            try:
                from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
                kis_df = default_kis_broker.get_daily_ohlcv(ticker, min_bars=500)
                if kis_df is not None and len(kis_df) >= 60:
                    df = kis_df.dropna(subset=['Close', 'High', 'Low', 'Volume']).copy()
            except Exception:
                pass
        if df is None or len(df) < 60:
            return None
            
        # 1. Calculate Daily Ichimoku via SSOT Domain Engine
        df = calculate_ichimoku_indicators(df)
        
        # 2. Calculate Weekly Ichimoku via SSOT Domain Engine
        df_w = df[['Open', 'High', 'Low', 'Close', 'Volume']].resample('W-FRI').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
        }).dropna()
        df_w = calculate_ichimoku_indicators(df_w)

        # Build Daily & Weekly Series Payloads via SSOT Domain Engine
        daily_series = build_ichimoku_series_payload(df, is_weekly=False, max_bars=500)
        weekly_series = build_ichimoku_series_payload(df_w, is_weekly=True, max_bars=150)
        
        df_clean = df.dropna(subset=['Close', 'High', 'Low', 'Kijun', 'Tenkan']).tail(500)
        last = df_clean.iloc[-1]
        close = float(last['Close'])
        kijun = float(last['Kijun'])
        tenkan = float(last['Tenkan'])
        vol_ratio = float(last['Vol_Ratio']) if not pd.isna(last['Vol_Ratio']) else 1.0
        span_a = float(last['SpanA']) if not pd.isna(last['SpanA']) else close
        span_b = float(last['SpanB']) if not pd.isna(last['SpanB']) else close
        
        cloud_top = max(span_a, span_b)
        cloud_bottom = min(span_a, span_b)
        kijun_gap = ((close - kijun) / kijun) * 100
        
        # Weekly Macro Stance Evaluation
        w_clean = df_w.dropna(subset=['Close', 'High', 'Low', 'Kijun', 'Tenkan'])
        if not w_clean.empty:
            w_last = w_clean.iloc[-1]
            w_close = float(w_last['Close'])
            w_span_a = float(w_last['SpanA']) if not pd.isna(w_last['SpanA']) else w_close
            w_span_b = float(w_last['SpanB']) if not pd.isna(w_last['SpanB']) else w_close
            w_cloud_top = max(w_span_a, w_span_b)
            is_weekly_bull = (w_close >= w_cloud_top * 0.98)
        else:
            is_weekly_bull = (close >= cloud_top * 0.97)
        
        future_span_a_vals = daily_series.get("future_span_a", [])
        future_span_b_vals = daily_series.get("future_span_b", [])
        future_a_latest = future_span_a_vals[-1] if future_span_a_vals else span_a
        future_b_latest = future_span_b_vals[-1] if future_span_b_vals else span_b
        is_future_bull_cloud = future_a_latest >= future_b_latest
        future_cloud_type = "양운 (상승 지지 구름대)" if is_future_bull_cloud else "음운 (하락 저항 구름대)"
        future_cloud_gap = abs(future_a_latest - future_b_latest)
        
        # Detect Cloud Trampoline Bounce via SSOT Domain Function
        trampoline_detected, trampoline_days_ago, touch_gap_pct, close_gap_pct = detect_cloud_trampoline_bounce(df_clean, max_lookback=14)
        
        # Institutional Flow Signature & Sector Mapping via SSOT Domain Function
        sector = TICKER_SECTORS.get(ticker, "GENERAL")
        flow_data = compute_institutional_flow_indicators(df_clean)
        
        # Canonical 17-Year Quant Scoring via SSOT Domain Engine
        quant_eval = evaluate_quant_score(
            close=close,
            kijun=kijun,
            tenkan=tenkan,
            span_a=span_a,
            span_b=span_b,
            vol_ratio=vol_ratio,
            trampoline_detected=trampoline_detected,
            is_weekly_bull=is_weekly_bull,
            days_ago=trampoline_days_ago
        )
        
        bull_score = quant_eval["bull_score"]
        bear_score = quant_eval["bear_score"]
        sniper_score = quant_eval["sniper_score"]
        is_sniper_active = quant_eval["is_sniper_active"]
        quant_type = quant_eval["quant_type"]
        quant_verdict = quant_eval["quant_verdict"]
        
        # 3-Month Momentum / RS Calculation & 20D Breakout Detection
        if len(df_clean) >= 60:
            close_60d_ago = float(df_clean.iloc[-60]['Close'])
            rs_3m = ((close - close_60d_ago) / close_60d_ago) * 100.0
        elif len(df_clean) > 1:
            close_first = float(df_clean.iloc[0]['Close'])
            rs_3m = ((close - close_first) / close_first) * 100.0
        else:
            rs_3m = 0.0

        if len(df_clean) >= 21:
            high_20d_max = float(df_clean.iloc[-21:-1]['High'].max())
            is_breakout = bool(close >= high_20d_max)
        else:
            is_breakout = False

        composite_rs = None
        try:
            composite_rs = round(float(latest_composite_rs(df_clean["Close"].values)), 6)
        except Exception:
            composite_rs = None

        # Construct Intelligence Metadata
        intelligence = dict(quant_eval["intelligence"])
        intelligence.update({
            "is_sniper": is_sniper_active,
            "is_weekly_bull": is_weekly_bull,
            "trampoline_detected": trampoline_detected,
            "sector": sector,
            "obv_status": flow_data["obv_status"],
            "obv_label": flow_data["obv_label"],
            "flow_ratio": flow_data["flow_ratio"],
            "flow_label": flow_data["flow_label"],
            "flow_score": flow_data["flow_score"],
            "is_stealth_accum": flow_data["is_stealth_accum"],
            "rs_3m": round(rs_3m, 2),
            "momentum_3m": round(rs_3m, 2),
            "composite_rs": composite_rs,
            "is_breakout": is_breakout
        })
            
        return {
            "ticker": ticker,
            "sector": sector,
            "obv_status": flow_data["obv_status"],
            "obv_label": flow_data["obv_label"],
            "flow_ratio": flow_data["flow_ratio"],
            "flow_label": flow_data["flow_label"],
            "flow_score": flow_data["flow_score"],
            "is_stealth_accum": flow_data["is_stealth_accum"],
            "latest_close": round(close, 2),
            "kijun": round(kijun, 2),
            "tenkan": round(tenkan, 2),
            "span_a": round(span_a, 2),
            "span_b": round(span_b, 2),
            "future_span_a_latest": round(future_a_latest, 2),
            "future_span_b_latest": round(future_b_latest, 2),
            "future_cloud_type": future_cloud_type,
            "future_cloud_gap": round(future_cloud_gap, 2),
            "kijun_gap_pct": round(kijun_gap, 2),
            "vol_ratio": round(vol_ratio, 2),
            "bull_score": bull_score,
            "bear_score": bear_score,
            "sniper_score": sniper_score,
            "is_sniper": is_sniper_active,
            "is_weekly_bull": is_weekly_bull,
            "trampoline_detected": trampoline_detected,
            "trampoline_days_ago": trampoline_days_ago,
            "touch_gap_pct": touch_gap_pct,
            "close_gap_pct": close_gap_pct,
            "rs_3m": round(rs_3m, 2),
            "momentum_3m": round(rs_3m, 2),
            "composite_rs": composite_rs,
            "is_breakout": is_breakout,
            "intelligence": intelligence,
            "status_tag": quant_type,
            "status_text": quant_verdict,
            "timeframes": {
                "daily": daily_series,
                "weekly": weekly_series
            },
            "candles": daily_series.get("candles", []),
            "kijun_line": daily_series.get("kijun_line", []),
            "tenkan_line": daily_series.get("tenkan_line", []),
            "span_a_line": daily_series.get("span_a_line", []),
            "span_b_line": daily_series.get("span_b_line", []),
            "sma20": daily_series.get("sma20", []),
            "sma60": daily_series.get("sma60", []),
            "volume": daily_series.get("volume", [])
        }
    except Exception as e:
        print(f"Error computing {ticker}: {e}")
        return None

def build_dashboard_data(output_file=None, charts_dir=None):
    target_charts_dir = charts_dir or CHARTS_DIR
    target_out_path = output_file or OUTPUT_JSON
    active_watchlist = get_active_watchlist()
    print(f"Building full dashboard data feed for {len(active_watchlist)} universe tickers...")
    
    # Macro context from YouTube stream cache
    macro_info = {}
    stream_mentioned_tickers = set()
    if os.path.exists(STREAM_CACHE):
        try:
            with open(STREAM_CACHE, "r", encoding="utf-8") as f:
                macro_info = json.load(f)
                if "mentioned_stocks" in macro_info:
                    for s in macro_info["mentioned_stocks"]:
                        if isinstance(s, dict) and "ticker" in s:
                            stream_mentioned_tickers.add(s["ticker"])
        except Exception:
            pass
            
    chart_data = {}
    
    # Parallel Batch Computation for 60 tickers (throttled to 6 workers to avoid HTTP 429)
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(compute_all_indicators, active_watchlist))
        
    for res in results:
        if res:
            chart_data[res["ticker"]] = res
            
    # Executive KPI metrics from SQLite trades
    total_trades = 0
    active_count = 0
    closed_count = 0
    win_rate = 0.0
    try:
        init_database()
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, pnl_pct FROM trades")
            trade_rows = cursor.fetchall()
            total_trades = len(trade_rows)
            closed = [r for r in trade_rows if str(r['status'] or '').startswith('CLOSED')]
            wins = [r for r in closed if float(r['pnl_pct'] or 0) > 0]
            win_rate = (len(wins) / len(closed) * 100) if closed else 0.0
            active_count = len([r for r in trade_rows if str(r['status'] or '') == 'OPEN'])
            closed_count = len(closed)
    except Exception:
        pass

    kpis = {
        "total_recommendations": total_trades,
        "active_positions": active_count,
        "closed_trades": closed_count,
        "win_rate": f"{win_rate:.1f}%",
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    # Calculate Macro Tailwind Sectors from Gate-0 Climate
    macro_climate = macro_info.get("macro_climate", {}) if isinstance(macro_info, dict) else {}
    macro_gauges = macro_info.get("macro_gauges", {}) if isinstance(macro_info, dict) else {}
    msi_score = extract_msi_score(macro_info, default=50.0)
    us10y_val = float(macro_gauges.get("us10y", {}).get("val", 4.4)) if isinstance(macro_gauges.get("us10y"), dict) else 4.4
    wti_val = float(macro_gauges.get("wti", {}).get("val", 78.0)) if isinstance(macro_gauges.get("wti"), dict) else 78.0
    vix_val = float(macro_gauges.get("vix", {}).get("val", 16.0)) if isinstance(macro_gauges.get("vix"), dict) else 16.0
    tailwind_sectors = get_macro_tailwind_sectors(msi_score, us10y_val, wti_val, vix_val)
    macro_info["tailwind_sectors"] = tailwind_sectors
    
    # 3-Tier Categorization via SSOT Domain Engine
    dual_consensus_picks, strat1_exclusive, strat2_exclusive = classify_3tier_candidates(
        chart_data=chart_data,
        tailwind_sectors=tailwind_sectors,
        stream_mentioned_tickers=stream_mentioned_tickers
    )

    # Load current portfolio & recommendation streaks
    portfolio = get_live_portfolio()
    wallet_map = {h['ticker'].upper(): h for h in portfolio.get('holdings', [])}
    streaks = get_recommendation_streaks()

    # Tag IN WALLET & STREAK properties onto each candidate
    for group in [dual_consensus_picks, strat1_exclusive, strat2_exclusive]:
        for item in group:
            tk_upper = item["ticker"].upper()
            if tk_upper in wallet_map:
                item["in_wallet"] = True
                item["holding_pnl"] = float(wallet_map[tk_upper].get("pnl_pct", 0.0))
                item["holding_qty"] = float(wallet_map[tk_upper].get("quantity", 0.0))
                item["holding_price"] = float(wallet_map[tk_upper].get("buy_price", 0.0))
            else:
                item["in_wallet"] = False
                item["holding_pnl"] = 0.0
                item["holding_qty"] = 0.0
                item["holding_price"] = 0.0
                
            streak_days = streaks.get(item["ticker"], 1)
            item["streak_days"] = streak_days
            item["streak_label"] = f"[{streak_days}D STREAK]" if streak_days >= 2 else ""

    portfolio = get_live_portfolio()

    # 1. Save individual modular chart files into data/charts/{ticker}.json
    os.makedirs(target_charts_dir, exist_ok=True)
    chart_intelligence = {}
    for ticker, c_obj in chart_data.items():
        if isinstance(c_obj, dict):
            # Save individual ticker chart cache
            ticker_chart_path = os.path.join(target_charts_dir, f"{ticker}.json")
            prev = atomic_read_json(ticker_chart_path) or {}
            prev_n = len(prev.get("candles") or []) if isinstance(prev, dict) else 0
            new_n = len(c_obj.get("candles") or [])
            if prev_n >= 250 and new_n < prev_n:
                c_obj = prev
            else:
                atomic_save_json(ticker_chart_path, c_obj)
            
            # Extract lightweight intelligence metadata for instant dashboard loading (<50KB target)
            bull_score = c_obj.get("bull_score", 0)
            flow_ratio = round(c_obj.get("flow_ratio", 1.0), 2)
            obv_status = c_obj.get("obv_status", "NEUTRAL")
            k_gap = round(c_obj.get("kijun_gap_pct", 0.0), 2)
            chart_intelligence[ticker] = {
                "gate_score": bull_score,
                "score": bull_score,
                "flow_ratio": flow_ratio,
                "obv_status": obv_status,
                "kijun_gap": k_gap,
                "kijun_gap_pct": k_gap,
                "intelligence": {
                    "score": bull_score,
                    "gate_score": bull_score,
                    "flow_ratio": flow_ratio,
                    "obv_status": obv_status,
                    "kijun_gap": k_gap,
                }
            }

    # 2. Build lightweight executive summary payload (under 50KB)
    daily_history = get_daily_recommendation_history()
    
    # Goldman Sachs-Style Conviction Alpha Ranking & C1-M2 Slot Allocator (50/30/20)
    all_candidates = dual_consensus_picks + strat1_exclusive + strat2_exclusive
    msi_val = extract_msi_score(macro_info, default=50.0)
    capital = resolve_capital_regime(msi_score=msi_val, fetch_spy=True)
    is_bull_regime = bool(capital["is_bull_regime"])
    macro_info["msi_score"] = msi_val
    macro_info["msi_stance"] = capital["msi_stance"]
    macro_info["is_bull_regime"] = is_bull_regime
    macro_info["capital_regime_source"] = capital["regime_source"]
    macro_info["slot_weights"] = list(
        capital.get("slot_weights") or (SLOT_WEIGHTS_BULL if is_bull_regime else SLOT_WEIGHTS_BEAR)
    )

    # Dynamic leverage overlay (SPY>=SMA200 & VIX<20 → 1.5x)
    spy_snap = fetch_spy_trend_regime()
    vix_val = None
    try:
        vix_raw = (macro_info.get("gauges") or {}).get("vix", {})
        if isinstance(vix_raw, dict):
            vix_val = float(vix_raw.get("val") or 0) or None
    except Exception:
        vix_val = None
    if vix_val is None:
        try:
            hist = yf.Ticker("^VIX").history(period="5d")
            if hist is not None and not hist.empty:
                vix_val = float(hist["Close"].iloc[-1])
        except Exception:
            vix_val = None
    leverage = evaluate_dynamic_leverage(
        spy_close=spy_snap.get("spy_close") or capital.get("spy_close"),
        spy_sma200=spy_snap.get("spy_sma200") or capital.get("spy_sma200"),
        vix=vix_val,
    )
    macro_info["leverage"] = leverage

    # QQQ Composite RS dual-momentum benchmark
    qqq_crs = None
    try:
        qqq_df = yf.download("QQQ", period="1y", interval="1d", progress=False)
        if qqq_df is not None and not qqq_df.empty:
            if isinstance(qqq_df.columns, pd.MultiIndex):
                qqq_df.columns = qqq_df.columns.get_level_values(0)
            qqq_crs = latest_composite_rs(qqq_df["Close"].values)
    except Exception:
        qqq_crs = None

    conviction_res = rank_and_select_top_picks(
        candidates=all_candidates,
        portfolio_equity_usd=float(portfolio.get("total_equity_usd", 7500.0)),
        msi_score=msi_val,
        is_bull_regime=is_bull_regime,
        qqq_composite_rs=qqq_crs,
    )
    slot_summary = conviction_res.get("slot_summary", {})
    slot_summary["qqq_composite_rs"] = qqq_crs
    slot_summary["leverage"] = leverage
    slot_summary["cash_proxy_ticker"] = CASH_PROXY_TICKER
    slot_summary["satellite_count"] = len(satellite_holdings(portfolio.get("holdings") or []))
    slot_summary["proxy_holdings"] = [
        {"ticker": h.get("ticker"), "quantity": h.get("quantity"), "current_price": h.get("current_price")}
        for h in proxy_holdings(portfolio.get("holdings") or [])
    ]

    signal_clock_et = now_us_eastern()
    signal_provenance = derive_signal_provenance(
        chart_data=chart_data,
        ranked_candidates=conviction_res.get("ranked_candidates", []),
        signal_clock_et=signal_clock_et,
    )

    payload = {
        "engine": "C1-M2",
        # Autopilot accepts this feed only on a later US session, preventing a
        # partially formed intraday daily candle from driving entries.
        "signal_session_date": signal_provenance["session_date"],
        "signal_is_eod_confirmed": signal_provenance["is_confirmed"],
        "signal_source_hash": signal_provenance["source_hash"],
        "signal_source_tickers": signal_provenance["source_tickers"],
        "signal_source_rows": signal_provenance["source_rows"],
        "signal_generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "risk_constitution": {
            "stop_loss_pct": STOP_LOSS_PCT,
            "hard_stop_pct": -HARD_STOP_PCT,
            "take_profit_activate_pct": TRAILING_ACTIVATE_PCT,
            "take_profit_pct": TAKE_PROFIT_PCT,
            "slot_weights_bull": list(SLOT_WEIGHTS_BULL),
            "slot_weights_bear": list(SLOT_WEIGHTS_BEAR),
            "cash_proxy": CASH_PROXY_TICKER,
        },
        "macro": macro_info,
        "kpis": kpis,
        "daily_history": daily_history,
        "portfolio": portfolio,
        "top_conviction_pick": conviction_res.get("top_pick"),
        "top_conviction_runner_up": conviction_res.get("runner_up"),
        "ranked_conviction_list": conviction_res.get("ranked_candidates", []),
        "slot_allocation_summary": slot_summary,
        "tier1": dual_consensus_picks,
        "tier2": strat1_exclusive,
        "tier3": strat2_exclusive,
        "chart_intelligence": chart_intelligence,
        "universe_sectors": load_universe_sectors(),
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    out_path = target_out_path
    atomic_save_json(out_path, payload, indent=None)
    if (
        signal_provenance["is_confirmed"]
        and os.path.abspath(out_path) == os.path.abspath(OUTPUT_JSON)
    ):
        # Intraday dashboard refreshes may update dashboard_data.json, but can
        # never overwrite the last immutable confirmed-EOD entry artifact.
        atomic_save_json(CONFIRMED_EOD_JSON, payload, indent=None)
        
    bar_counts = [len((c or {}).get("candles") or []) for c in chart_data.values()]
    bar_min = min(bar_counts) if bar_counts else 0
    bar_max = max(bar_counts) if bar_counts else 0
    print(
        f"Successfully generated modular feed: {out_path} "
        f"(Size: {os.path.getsize(out_path)/1024:.1f} KB, Charts: {len(chart_data)} saved in {target_charts_dir}, "
        f"daily bars min={bar_min} max={bar_max})"
    )
    return payload


if __name__ == "__main__":
    build_dashboard_data()
