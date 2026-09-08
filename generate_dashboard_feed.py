import os
import sys
import time
import json
import tempfile
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from al_sangmoo.infrastructure.persistence import (
    get_daily_recommendation_history,
    get_live_portfolio,
    get_recommendation_streaks,
    get_recommendations_matrix,
)
from al_sangmoo.infrastructure.atomic_io import atomic_save_json, atomic_read_json

# Windows encoding fix
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_CSV = os.path.join(BASE_DIR, "trade_history.csv")
OUTPUT_JSON = os.path.join(BASE_DIR, "dashboard_data.json")
STREAM_CACHE = os.path.join(BASE_DIR, "wepoll_latest_stream.json")
CHARTS_DIR = os.path.join(BASE_DIR, "data", "charts")

from concurrent.futures import ThreadPoolExecutor
from al_sangmoo.core.constants import WATCHLIST, STOCK_DICT, TICKER_SECTORS, get_active_watchlist, get_macro_tailwind_sectors
from al_sangmoo.domain.quant.ichimoku import (
    calculate_ichimoku_indicators,
    detect_cloud_trampoline_bounce,
    compute_institutional_flow_indicators,
    build_ichimoku_series_payload
)
from al_sangmoo.domain.quant.scoring import (
    evaluate_quant_score,
    classify_3tier_candidates
)
from al_sangmoo.domain.quant.conviction_engine import rank_and_select_top_picks
from al_sangmoo.domain.quant.macro import extract_msi_score, resolve_capital_regime
from al_sangmoo.domain.quant.dynamic_universe import load_universe_sectors


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
        if df is None or len(df) < 200:
            try:
                from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
                kis_df = default_kis_broker.get_daily_ohlcv(ticker, min_bars=500)
                if kis_df is not None and len(kis_df) > (0 if df is None else len(df)):
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
    
    trades = []
    if os.path.exists(HISTORY_CSV):
        try:
            df_hist = pd.read_csv(HISTORY_CSV)
            trades = df_hist.to_dict(orient="records")
        except Exception:
            pass
            
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
            
    total_trades = len(trades)
    closed = [t for t in trades if str(t.get('status', '')).startswith('CLOSED')]
    wins = [t for t in closed if float(t.get('pnl_pct', 0)) > 0]
    win_rate = (len(wins) / len(closed) * 100) if closed else 0.0
    
    kpis = {
        "total_recommendations": total_trades,
        "active_positions": len([t for t in trades if t.get('status') == 'OPEN']),
        "closed_trades": len(closed),
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

    # Combined full sets for backwards compatibility
    all_strat1 = dual_consensus_picks + strat1_exclusive
    all_strat2 = dual_consensus_picks + strat2_exclusive

    # Build Unified Signal Tracker (Tier 1 -> Tier 3 -> Tier 2)
    today_str = datetime.now().strftime("%Y-%m-%d")
    signal_tracker = []
    
    # 1. Tier 1 Macro Leaders
    for d in dual_consensus_picks:
        signal_tracker.append({
            "date": today_str,
            "ticker": d["ticker"],
            "name": d["name"],
            "sector": d.get("sector", "GENERAL"),
            "origin": d["origin"],
            "origin_label": d["origin_label"],
            "strategy": "Tier 1 (매크로 순풍+잠행매집)",
            "strategy_code": "TIER_1_LEADER",
            "entry_price": d["price"],
            "target_price": d["target_price"],
            "stop_price": d["stop_price"],
            "score": d["score"],
            "flow_ratio": d.get("flow_ratio", 1.0),
            "obv_status": d.get("obv_status", "NEUTRAL"),
            "in_wallet": d.get("in_wallet", False),
            "streak_days": d.get("streak_days", 1),
            "streak_label": d.get("streak_label", ""),
            "status": "TIER_1_LEADER",
            "status_label": "TIER_1_LEADER"
        })
        
    # 2. Tier 3 Sniper Radar
    for s in strat2_exclusive:
        signal_tracker.append({
            "date": today_str,
            "ticker": s["ticker"],
            "name": s["name"],
            "sector": s.get("sector", "GENERAL"),
            "origin": s["origin"],
            "origin_label": s["origin_label"],
            "strategy": "Tier 3 (구름대 스나이퍼)",
            "strategy_code": "TIER_3_SNIPER",
            "entry_price": s["price"],
            "target_price": s["target_price"],
            "stop_price": s["stop_price"],
            "score": s["score"],
            "flow_ratio": s.get("flow_ratio", 1.0),
            "obv_status": s.get("obv_status", "NEUTRAL"),
            "in_wallet": s.get("in_wallet", False),
            "streak_days": s.get("streak_days", 1),
            "streak_label": s.get("streak_label", ""),
            "status": "ACTIVE_SNIPER",
            "status_label": "ACTIVE_SNIPER"
        })
        
    # 3. Tier 2 Structural Pullbacks
    for p in strat1_exclusive:
        signal_tracker.append({
            "date": today_str,
            "ticker": p["ticker"],
            "name": p["name"],
            "sector": p.get("sector", "GENERAL"),
            "origin": p["origin"],
            "origin_label": p["origin_label"],
            "strategy": "Tier 2 (정석 기준선 눌림목)",
            "strategy_code": "TIER_2_PULLBACK",
            "entry_price": p["price"],
            "target_price": p["target_price"],
            "stop_price": p["stop_price"],
            "score": p["score"],
            "flow_ratio": p.get("flow_ratio", 1.0),
            "obv_status": p.get("obv_status", "NEUTRAL"),
            "in_wallet": p.get("in_wallet", False),
            "streak_days": p.get("streak_days", 1),
            "streak_label": p.get("streak_label", ""),
            "status": "ACTIVE_BUY",
            "status_label": "ACTIVE_BUY"
        })

    # Load 2+2+2 Matrix and Portfolio from DB
    matrix = get_recommendations_matrix()
    if not isinstance(matrix, list):
        matrix = [matrix] if matrix else []
        
    portfolio = get_live_portfolio()
    
    # Ensure any ticker present in matrix is computed in chart_data
    for m in matrix:
        for key in ["bull_1", "bull_2", "neutral_1", "neutral_2", "bear_1", "bear_2"]:
            tk = m.get(key)
            if tk and tk not in chart_data:
                d = compute_all_indicators(tk)
                if d:
                    chart_data[tk] = d

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
            
            # Extract lightweight intelligence metadata for instant dashboard loading
            chart_intelligence[ticker] = {
                "intelligence": c_obj.get("intelligence", {}),
                "latest_close": c_obj.get("latest_close"),
                "kijun": c_obj.get("kijun"),
                "tenkan": c_obj.get("tenkan"),
                "kijun_gap_pct": c_obj.get("kijun_gap_pct"),
                "vol_ratio": c_obj.get("vol_ratio"),
                "future_cloud_type": c_obj.get("future_cloud_type"),
                "future_cloud_gap": c_obj.get("future_cloud_gap"),
                "future_span_a_latest": c_obj.get("future_span_a_latest"),
                "future_span_b_latest": c_obj.get("future_span_b_latest"),
                "status_text": c_obj.get("status_text"),
                "status_tag": c_obj.get("status_tag"),
                "bull_score": c_obj.get("bull_score"),
                "bear_score": c_obj.get("bear_score"),
                "is_sniper": c_obj.get("is_sniper", False)
            }

    # 2. Build lightweight executive summary payload (under 50KB)
    daily_history = get_daily_recommendation_history()
    
    # Goldman Sachs-Style Conviction Alpha Ranking & Dynamic Regime Slot Allocator (v2)
    all_candidates = dual_consensus_picks + strat1_exclusive + strat2_exclusive
    msi_val = extract_msi_score(macro_info, default=50.0)
    capital = resolve_capital_regime(msi_score=msi_val, fetch_spy=True)
    is_bull_regime = bool(capital["is_bull_regime"])
    macro_info["msi_score"] = msi_val
    macro_info["msi_stance"] = capital["msi_stance"]
    macro_info["is_bull_regime"] = is_bull_regime
    macro_info["capital_regime_source"] = capital["regime_source"]
    
    conviction_res = rank_and_select_top_picks(
        candidates=all_candidates,
        portfolio_equity_usd=float(portfolio.get("total_equity_usd", 7500.0)),
        msi_score=msi_val,
        is_bull_regime=is_bull_regime
    )

    payload = {
        "macro": macro_info,
        "kpis": kpis,
        "trades": trades,
        "matrix": matrix,
        "daily_history": daily_history,
        "portfolio": portfolio,
        "top_conviction_pick": conviction_res.get("top_pick"),
        "top_conviction_runner_up": conviction_res.get("runner_up"),
        "ranked_conviction_list": conviction_res.get("ranked_candidates", []),
        "slot_allocation_summary": conviction_res.get("slot_summary", {}),
        "tier1": dual_consensus_picks,
        "tier2": strat1_exclusive,
        "tier3": strat2_exclusive,
        "dual_consensus": dual_consensus_picks,
        "strat1_exclusive": strat1_exclusive,
        "strat2_exclusive": strat2_exclusive,
        "primary_accumulation": all_strat1,
        "sniper_radar": all_strat2,
        "signal_tracker": signal_tracker,
        "chart_intelligence": chart_intelligence,
        "universe_sectors": load_universe_sectors(),
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    out_path = target_out_path
    atomic_save_json(out_path, payload)
        
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
