import os
import sys
import time
import json
import tempfile
import pandas as pd
import yfinance as yf
from datetime import datetime
import db_manager

def atomic_save_json(file_path, data, indent=2, max_retries=10):
    dir_name = os.path.dirname(os.path.abspath(file_path))
    os.makedirs(dir_name, exist_ok=True)
    temp_name = None
    with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
        temp_name = tf.name
        json.dump(data, tf, ensure_ascii=False, indent=indent)
        tf.flush()
        os.fsync(tf.fileno())
        
    for attempt in range(max_retries):
        try:
            os.replace(temp_name, file_path)
            return
        except (PermissionError, OSError):
            if attempt == max_retries - 1:
                try:
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=indent)
                    if temp_name and os.path.exists(temp_name):
                        os.remove(temp_name)
                    return
                except Exception:
                    pass
                raise
            time.sleep(0.01 * (1.5 ** attempt))
            
    if temp_name and os.path.exists(temp_name):
        try:
            os.remove(temp_name)
        except Exception:
            pass

def atomic_read_json(file_path, default=None, max_retries=5):
    if not os.path.exists(file_path):
        return default if default is not None else {}
    for attempt in range(max_retries):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (PermissionError, json.JSONDecodeError, OSError):
            if attempt == max_retries - 1:
                return default if default is not None else {}
            time.sleep(0.01 * (attempt + 1))
    return default if default is not None else {}

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
from al_sangmoo.core.constants import WATCHLIST, STOCK_DICT

def build_ichimoku_series(df_in, max_bars=500, is_weekly=False):
    df_clean = df_in.dropna(subset=['Close', 'High', 'Low', 'Kijun', 'Tenkan']).tail(max_bars)
    if df_clean.empty:
        return {}
        
    candles = []
    tenkan_pts = []
    kijun_pts = []
    span_a_pts = []
    span_b_pts = []
    sma20_pts = []
    sma60_pts = []
    vol_pts = []
    
    for idx, row in df_clean.iterrows():
        time_str = idx.strftime("%Y-%m-%d")
        candles.append({
            "time": time_str,
            "open": round(float(row['Open']), 2),
            "high": round(float(row['High']), 2),
            "low": round(float(row['Low']), 2),
            "close": round(float(row['Close']), 2)
        })
        if not pd.isna(row.get('Tenkan')):
            tenkan_pts.append({"time": time_str, "value": round(float(row['Tenkan']), 2)})
        if not pd.isna(row.get('Kijun')):
            kijun_pts.append({"time": time_str, "value": round(float(row['Kijun']), 2)})
        if not pd.isna(row.get('SpanA')):
            span_a_pts.append({"time": time_str, "value": round(float(row['SpanA']), 2)})
        if not pd.isna(row.get('SpanB')):
            span_b_pts.append({"time": time_str, "value": round(float(row['SpanB']), 2)})
        if not pd.isna(row.get('SMA20')):
            sma20_pts.append({"time": time_str, "value": round(float(row['SMA20']), 2)})
        if not pd.isna(row.get('SMA60')):
            sma60_pts.append({"time": time_str, "value": round(float(row['SMA60']), 2)})
        if not pd.isna(row.get('Volume')):
            vol_pts.append({
                "time": time_str,
                "value": float(row['Volume']),
                "color": "#059669" if row['Close'] >= row['Open'] else "#dc2626"
            })
            
    # Future 26 forward cloud projection
    last_date = df_clean.index[-1]
    if is_weekly:
        future_dates = pd.date_range(start=last_date + pd.Timedelta(days=7), periods=26, freq='W-FRI')
    else:
        future_dates = pd.bdate_range(start=last_date + pd.Timedelta(days=1), periods=26)
        
    future_span_a_vals = []
    future_span_b_vals = []
    for k in range(len(future_dates)):
        f_time_str = future_dates[k].strftime("%Y-%m-%d")
        lookback_idx = len(df_clean) - 26 + k
        if 0 <= lookback_idx < len(df_clean):
            val_a = float(df_clean['RawSpanA'].iloc[lookback_idx])
            val_b = float(df_clean['RawSpanB'].iloc[lookback_idx])
            if not pd.isna(val_a):
                span_a_pts.append({"time": f_time_str, "value": round(val_a, 2)})
                future_span_a_vals.append(val_a)
            if not pd.isna(val_b):
                span_b_pts.append({"time": f_time_str, "value": round(val_b, 2)})
                future_span_b_vals.append(val_b)
                
    return {
        "candles": candles,
        "kijun_line": kijun_pts,
        "tenkan_line": tenkan_pts,
        "span_a_line": span_a_pts,
        "span_b_line": span_b_pts,
        "sma20": sma20_pts,
        "sma60": sma60_pts,
        "volume": vol_pts,
        "future_span_a": future_span_a_vals,
        "future_span_b": future_span_b_vals
    }

def compute_all_indicators(ticker):
    try:
        df = yf.download(ticker, period="3y", interval="1d", progress=False)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        df = df.dropna(subset=['Close', 'High', 'Low', 'Volume']).copy()
        if len(df) < 60:
            return None
            
        # 1. Calculate Daily Ichimoku
        df['Tenkan'] = (df['High'].rolling(9).max() + df['Low'].rolling(9).min()) / 2
        df['Kijun'] = (df['High'].rolling(26).max() + df['Low'].rolling(26).min()) / 2
        df['RawSpanA'] = (df['Tenkan'] + df['Kijun']) / 2
        df['RawSpanB'] = (df['High'].rolling(52).max() + df['Low'].rolling(52).min()) / 2
        df['SpanA'] = df['RawSpanA'].shift(26)
        df['SpanB'] = df['RawSpanB'].shift(26)
        df['SMA20'] = df['Close'].rolling(20).mean()
        df['SMA60'] = df['Close'].rolling(60).mean()
        df['Vol_SMA20'] = df['Volume'].rolling(20).mean()
        df['Vol_Ratio'] = df['Volume'] / df['Vol_SMA20']
        
        # 2. Calculate Weekly Ichimoku (Resampled to Weekly)
        df_w = df[['Open', 'High', 'Low', 'Close', 'Volume']].resample('W-FRI').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
        }).dropna()
        df_w['Tenkan'] = (df_w['High'].rolling(9).max() + df_w['Low'].rolling(9).min()) / 2
        df_w['Kijun'] = (df_w['High'].rolling(26).max() + df_w['Low'].rolling(26).min()) / 2
        df_w['RawSpanA'] = (df_w['Tenkan'] + df_w['Kijun']) / 2
        df_w['RawSpanB'] = (df_w['High'].rolling(52).max() + df_w['Low'].rolling(52).min()) / 2
        df_w['SpanA'] = df_w['RawSpanA'].shift(26)
        df_w['SpanB'] = df_w['RawSpanB'].shift(26)
        df_w['SMA20'] = df_w['Close'].rolling(20).mean()
        df_w['SMA60'] = df_w['Close'].rolling(60).mean()
        df_w['Vol_SMA20'] = df_w['Volume'].rolling(20).mean()
        df_w['Vol_Ratio'] = df_w['Volume'] / df_w['Vol_SMA20']

        # Build Daily & Weekly Series Payloads
        daily_series = build_ichimoku_series(df, max_bars=500, is_weekly=False)
        weekly_series = build_ichimoku_series(df_w, max_bars=150, is_weekly=True)
        
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
            w_kijun = float(w_last['Kijun'])
            w_span_a = float(w_last['SpanA']) if not pd.isna(w_last['SpanA']) else w_close
            w_span_b = float(w_last['SpanB']) if not pd.isna(w_last['SpanB']) else w_close
            w_cloud_top = max(w_span_a, w_span_b)
            # Strict Weekly Bull: Price must be at or above Weekly Cloud Top (max 2% tolerance)
            is_weekly_bull = (w_close >= w_cloud_top * 0.98)
        else:
            is_weekly_bull = (close >= cloud_top * 0.97)
            w_cloud_top = cloud_top
            w_kijun = kijun
        
        future_span_a_vals = daily_series.get("future_span_a", [])
        future_span_b_vals = daily_series.get("future_span_b", [])
        future_a_latest = future_span_a_vals[-1] if future_span_a_vals else span_a
        future_b_latest = future_span_b_vals[-1] if future_span_b_vals else span_b
        is_future_bull_cloud = future_a_latest >= future_b_latest
        future_cloud_type = "양운 (상승 지지 구름대)" if is_future_bull_cloud else "음운 (하락 저항 구름대)"
        future_cloud_gap = abs(future_a_latest - future_b_latest)
        
        # 1. Strategy 1 (Primary Accumulation - Graduated 17-Year Quant Scoring, Max 100 pt)
        bull_score = 0
        if close >= cloud_top:
            bull_score += 35
        elif close >= cloud_top * 0.97:
            bull_score += 25
        elif close >= cloud_bottom:
            bull_score += 15
            
        if -0.5 <= kijun_gap <= 3.5:
            bull_score += 35
        elif -0.8 <= kijun_gap <= 4.8:
            bull_score += 25
        elif -1.5 <= kijun_gap <= 7.0:
            bull_score += 15
            
        if vol_ratio <= 0.60:
            bull_score += 20
        elif vol_ratio <= 0.85:
            bull_score += 15
        elif vol_ratio <= 1.10:
            bull_score += 10
            
        if tenkan >= kijun:
            bull_score += 10
        elif close >= tenkan:
            bull_score += 5
        
        bear_score = 0
        if close < kijun: bear_score += 40
        if close < cloud_bottom: bear_score += 35
        if kijun_gap < -2.0: bear_score += 15
        
        # 2. Strategy 2 (Cloud Trampoline Bounce Sniper - 4 Distinct Factors, Max 100 pt)
        # Lookback 14 bars to check if a Cloud Bounce Launch happened
        trampoline_detected = False
        trampoline_days_ago = 0
        n_bars = len(df_clean)
        for b_offset in range(1, min(15, n_bars)):
            hist_bar = df_clean.iloc[-b_offset]
            h_open = float(hist_bar['Open'])
            h_close = float(hist_bar['Close'])
            h_low = float(hist_bar['Low'])
            h_sp_a = float(hist_bar['SpanA']) if not pd.isna(hist_bar['SpanA']) else h_close
            h_sp_b = float(hist_bar['SpanB']) if not pd.isna(hist_bar['SpanB']) else h_close
            h_cloud_top = max(h_sp_a, h_sp_b)
            if h_cloud_top > 0:
                h_touch_gap = (h_low - h_cloud_top) / h_cloud_top
                h_close_gap = (h_close - h_cloud_top) / h_cloud_top
                if (-0.035 <= h_touch_gap <= 0.060) and (h_close_gap >= -0.015):
                    trampoline_detected = True
                    trampoline_days_ago = b_offset - 1
                    break
        
        sniper_score = 0
        if trampoline_detected: sniper_score += 40
        if close >= cloud_top: sniper_score += 30
        elif close >= cloud_top * 0.98: sniper_score += 20
        if kijun_gap >= 0: sniper_score += 15
        elif kijun_gap >= -1.0: sniper_score += 10
        if tenkan >= kijun: sniper_score += 15
        elif close >= tenkan: sniper_score += 10
        
        # Apply Weekly Bull Stance Requirement
        is_sniper_active = is_weekly_bull and trampoline_detected and (sniper_score >= 80) and (close >= cloud_top * 0.97)
        is_strat1_active = is_weekly_bull and (bull_score >= 80) and (-0.8 <= kijun_gap <= 4.8)
        
        if is_strat1_active and is_sniper_active:
            quant_type = "BULL"
            quant_verdict = "Dual 5-Star (양대 전략 동시 충족 특급 매수)"
            quant_score_text = "100 / 100 pt (DUAL_5_STAR)"
            action_directive = f"[황금 교집합] 주봉 정배열 + 구름대 도약({trampoline_days_ago}일 전) + 26일 기준선({kijun_gap:+.1f}%) 안착."
        elif is_sniper_active:
            quant_type = "BULL"
            quant_verdict = "Sniper Alert (구름대 도약 2단계 특급 매수)"
            quant_score_text = f"{sniper_score} / 100 pt (SNIPER_BUY)"
            action_directive = f"[전략 2 스나이퍼] 주봉 상승장 + {trampoline_days_ago}일 전 구름대 지지 도약 후 상방 시세 분출(기준선 대비 {kijun_gap:+.1f}%). 목표 +15% / 손절 -4%."
        elif is_strat1_active:
            quant_type = "BULL"
            quant_verdict = "Bull Accumulation (1차 분할 매수 적합)"
            quant_score_text = f"{bull_score} / 100 pt (BULL_BUY)"
            action_directive = "주봉 상승장 + 26일 기준선 및 일목 구름대 상단 안착 확인. 1차 분할 매수 적합."
        elif bear_score >= 50:
            quant_type = "BEAR"
            quant_verdict = "Risk Breakdown (생명선 붕괴 / 매수 금지)"
            quant_score_text = f"{bull_score} / 100 pt (BEAR_EXIT)"
            action_directive = "26일 기준선(생명선) 및 구름대 붕괴. 물타기 금지 및 숏 헤지 우위 구간."
        else:
            quant_type = "NEUTRAL"
            quant_verdict = "Neutral Consolidation (박스권 수렴)"
            quant_score_text = f"{bull_score} / 100 pt (HOLD)"
            action_directive = "구름대 내부 또는 기준선 수렴 구간. 방향성 돌파 확인 전까지 관망 유지."
            
        # Indicator Detail Cards (Customized for Strategy 1 vs Strategy 2)
        if is_sniper_active:
            kijun_status = "status-bull" if kijun_gap >= -0.5 else "status-neutral"
            kijun_badge = "STAGE 2 MOMENTUM" if kijun_gap > 4.0 else "SUPPORTED"
            kijun_desc = f"26일 기준선(${kijun:,.2f}) 대비 {kijun_gap:+.1f}% 상방 도약 가속 구간"
            
            cloud_status = "status-bull"
            cloud_badge = "TRAMPOLINE BOUNCE"
            cloud_desc = f"일목 구름대({round(cloud_bottom,1)}~{round(cloud_top,1)}) {trampoline_days_ago}일 전 지지 반등 확인 완료"
        else:
            kijun_status = "status-bull" if -0.5 <= kijun_gap <= 4.0 else ("status-bear" if kijun_gap < -0.5 else "status-neutral")
            kijun_badge = "SUPPORTED" if -0.5 <= kijun_gap <= 4.0 else ("BREAKDOWN" if kijun_gap < -0.5 else "OVERHEATED")
            kijun_desc = f"현재가 ${close:,.2f} / 26일선 ${kijun:,.2f} (이격 {kijun_gap:+.1f}%)"
            
            cloud_status = "status-bull" if close >= cloud_top else ("status-bear" if close < cloud_bottom else "status-neutral")
            cloud_badge = "ABOVE CLOUD" if close >= cloud_top else ("BELOW CLOUD" if close < cloud_bottom else "INSIDE CLOUD")
            cloud_desc = f"일목 구름대({round(cloud_bottom,1)}~{round(cloud_top,1)}) {'상단 안착' if close >= cloud_top else ('하단 붕괴' if close < cloud_bottom else '내부 횡보')}"
        
        tenkan_status = "status-bull" if tenkan >= kijun else "status-bear"
        tenkan_badge = "GOLDEN CROSS" if tenkan >= kijun else "DEAD CROSS"
        tenkan_desc = f"9일 전환선 ${tenkan:,.2f} {'상단 정배열' if tenkan >= kijun else '하단 역배열'}"
        
        vol_status = "status-bull" if vol_ratio <= 0.75 else ("status-neutral" if vol_ratio <= 1.2 else "status-bear")
        vol_badge = "VOLUME DRY" if vol_ratio <= 0.75 else ("NORMAL VOL" if vol_ratio <= 1.2 else "HIGH VOL")
        vol_desc = f"20일 평균 거래량 대비 {round(vol_ratio*100)}% ({'매도세 고갈 완벽' if vol_ratio <= 0.75 else '통상 거래량'})"
        
        intelligence = {
            "verdict": quant_verdict,
            "score": quant_score_text,
            "bull_score": bull_score,
            "bear_score": bear_score,
            "sniper_score": sniper_score,
            "type": quant_type,
            "is_sniper": is_sniper_active,
            "is_weekly_bull": is_weekly_bull,
            "trampoline_detected": trampoline_detected,
            "kijun": {"val": f"${kijun:,.2f} ({kijun_gap:+.1f}%)", "status": kijun_status, "badge": kijun_badge, "desc": kijun_desc},
            "tenkan": {"val": f"${tenkan:,.2f}", "status": tenkan_status, "badge": tenkan_badge, "desc": tenkan_desc},
            "cloud": {"val": f"${cloud_top:,.2f}", "status": cloud_status, "badge": cloud_badge, "desc": cloud_desc},
            "vol": {"val": f"{round(vol_ratio*100)}% (20D)", "status": vol_status, "badge": vol_badge, "desc": vol_desc},
            "action": action_directive
        }
            
        return {
            "ticker": ticker,
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

def build_dashboard_data():
    print(f"Building full dashboard data feed for {len(WATCHLIST)} universe tickers...")
    
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
    
    # Fast Parallel Batch Computation for 60 tickers
    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(compute_all_indicators, WATCHLIST))
        
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
    
    # Rank and Categorize Strategy 1, Dual Consensus (Intersection), and Strategy 2
    strat1_exclusive = []
    strat2_exclusive = []
    dual_consensus_picks = []
    
    for t, c in chart_data.items():
        is_stream = t in stream_mentioned_tickers
        origin_tag = "VIKINGS_LIVE" if is_stream else "QUANT_DISCOVERY"
        origin_label = "바이킹스 라이브" if is_stream else "60종목 퀀트 발굴"
        
        kgap = c["kijun_gap_pct"]
        v_ratio = c["vol_ratio"]
        b_score = c["bull_score"]
        is_sn = c["is_sniper"]
        s_score = c.get("sniper_score", 95 if is_sn else 0)
        
        # Strategy 1 (Classic Pullback Accumulation): Weekly Bull, Bull Score >= 80, Kijun gap -0.8% ~ +4.8%
        is_strat1 = c.get("is_weekly_bull", False) and (b_score >= 80) and (-0.8 <= kgap <= 4.8)
        # Strategy 2 (Cloud Bounce Sniper): Weekly Bull, Cloud trampoline launch detected, Sniper Score >= 80
        is_strat2 = c.get("is_weekly_bull", False) and bool(is_sn) and (s_score >= 80)
        
        item_score = 100 if (is_strat1 and is_strat2) else (b_score if is_strat1 else s_score)
        
        item = {
            "ticker": t,
            "name": STOCK_DICT.get(t, [t])[0],
            "price": c["latest_close"],
            "score": item_score,
            "kijun_gap": kgap,
            "vol_ratio": round(v_ratio * 100),
            "origin": origin_tag,
            "origin_label": origin_label,
            "is_sniper": is_strat2,
            "is_strat1": is_strat1,
            "action": c["intelligence"]["action"],
            "target_price": round(c["latest_close"] * 1.15, 2),
            "stop_price": round(c["latest_close"] * 0.96, 2)
        }
        
        if is_strat1 and is_strat2:
            dual_consensus_picks.append(item)
        elif is_strat1:
            strat1_exclusive.append(item)
        elif is_strat2:
            strat2_exclusive.append(item)
            
    # Sort each category by highest conviction (Score -> Nearest Kijun Gap -> Lowest Volume Ratio)
    dual_consensus_picks.sort(key=lambda x: (-x["score"], abs(x["kijun_gap"]), x["vol_ratio"]))
    strat1_exclusive.sort(key=lambda x: (-x["score"], abs(x["kijun_gap"]), x["vol_ratio"]))
    strat2_exclusive.sort(key=lambda x: (-x["score"], abs(x["kijun_gap"]), x["vol_ratio"]))

    # Concentrate to Top Elite Recommendations (Top 4 Dual, Top 4 Strat 1, Top 4 Strat 2)
    dual_consensus_picks = dual_consensus_picks[:4]
    strat1_exclusive = strat1_exclusive[:4]
    strat2_exclusive = strat2_exclusive[:4]

    # Combined full sets for backwards compatibility
    all_strat1 = dual_consensus_picks + strat1_exclusive
    all_strat2 = dual_consensus_picks + strat2_exclusive

    # Build Unified Signal Tracker (Dual Consensus -> Strategy 2 -> Strategy 1)
    today_str = datetime.now().strftime("%Y-%m-%d")
    signal_tracker = []
    
    # 1. Dual Consensus signals first (Top 5-Star conviction)
    for d in dual_consensus_picks:
        signal_tracker.append({
            "date": today_str,
            "ticker": d["ticker"],
            "name": d["name"],
            "origin": d["origin"],
            "origin_label": d["origin_label"],
            "strategy": "양대 전략 공통 (황금 교집합)",
            "strategy_code": "STRATEGY_DUAL_CONSENSUS",
            "entry_price": d["price"],
            "target_price": d["target_price"],
            "stop_price": d["stop_price"],
            "score": 100,
            "status": "DUAL_5_STAR",
            "status_label": "DUAL_5_STAR"
        })
        
    # 2. Strategy 2 Sniper Radar signals
    for s in strat2_exclusive:
        signal_tracker.append({
            "date": today_str,
            "ticker": s["ticker"],
            "name": s["name"],
            "origin": s["origin"],
            "origin_label": s["origin_label"],
            "strategy": "전략 2 (스나이퍼)",
            "strategy_code": "STRATEGY_2_SNIPER",
            "entry_price": s["price"],
            "target_price": s["target_price"],
            "stop_price": s["stop_price"],
            "score": 95,
            "status": "ACTIVE_SNIPER",
            "status_label": "ACTIVE_SNIPER"
        })
        
    # 3. Strategy 1 Primary Accumulation signals
    for p in strat1_exclusive:
        signal_tracker.append({
            "date": today_str,
            "ticker": p["ticker"],
            "name": p["name"],
            "origin": p["origin"],
            "origin_label": p["origin_label"],
            "strategy": "전략 1 (정석 눌림목)",
            "strategy_code": "STRATEGY_1_PULLBACK",
            "entry_price": p["price"],
            "target_price": p["target_price"],
            "stop_price": p["stop_price"],
            "score": p["score"],
            "status": "ACTIVE_BUY",
            "status_label": "ACTIVE_BUY"
        })
    
    # Refresh today's 2+2+2 Recommendation Matrix & Permanent Trade Tracking Archive in SQLite DB
    if dual_consensus_picks or strat1_exclusive or strat2_exclusive:
        top_bulls = (dual_consensus_picks + strat1_exclusive)[:2]
        top_neutrals = (strat1_exclusive + dual_consensus_picks)[2:4]
        top_snipers = strat2_exclusive[:2]
        
        b_picks = [{"ticker": x["ticker"], "close": x["price"]} for x in top_bulls]
        n_picks = [{"ticker": x["ticker"], "close": x["price"]} for x in top_neutrals]
        s_picks = [{"ticker": x["ticker"], "close": x["price"]} for x in top_snipers]
        
        try:
            db_manager.save_recommendation_matrix_record(today_str, b_picks, n_picks, s_picks)
            db_manager.archive_daily_recommendations(today_str, dual_consensus_picks, strat1_exclusive, strat2_exclusive)
        except Exception as e:
            print(f"[DB Archiving Warning] {e}")

    # Load 2+2+2 Matrix and Portfolio from DB
    matrix = db_manager.get_recommendations_matrix()
    if not isinstance(matrix, list):
        matrix = [matrix] if matrix else []
        
    portfolio = db_manager.get_live_portfolio()
    
    # Ensure any ticker present in matrix is computed in chart_data
    for m in matrix:
        for key in ["bull_1", "bull_2", "neutral_1", "neutral_2", "bear_1", "bear_2"]:
            tk = m.get(key)
            if tk and tk not in chart_data:
                d = compute_all_indicators(tk)
                if d:
                    chart_data[tk] = d

    # 1. Save individual modular chart files into data/charts/{ticker}.json
    os.makedirs(CHARTS_DIR, exist_ok=True)
    chart_intelligence = {}
    for ticker, c_obj in chart_data.items():
        if isinstance(c_obj, dict):
            # Save individual ticker chart cache
            ticker_chart_path = os.path.join(CHARTS_DIR, f"{ticker}.json")
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
    payload = {
        "macro": macro_info,
        "kpis": kpis,
        "trades": trades,
        "matrix": matrix,
        "portfolio": portfolio,
        "dual_consensus": dual_consensus_picks,
        "strat1_exclusive": strat1_exclusive,
        "strat2_exclusive": strat2_exclusive,
        "primary_accumulation": all_strat1,
        "sniper_radar": all_strat2,
        "signal_tracker": signal_tracker,
        "chart_intelligence": chart_intelligence,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    out_path = os.path.join(BASE_DIR, OUTPUT_JSON)
    atomic_save_json(out_path, payload)
        
    print(f"Successfully generated modular feed: {out_path} (Size: {os.path.getsize(out_path)/1024:.1f} KB, Charts: {len(chart_data)} saved in {CHARTS_DIR})")
    return payload

if __name__ == "__main__":
    build_dashboard_data()
