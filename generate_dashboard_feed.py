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

WATCHLIST = [
    "QQQ", "NVDA", "AMZN", "LLY", "AAPL", "MSFT", "TSLA", "META",
    "AVGO", "COST", "AMD", "QCOM", "PLTR", "VST", "CEG", "ETN", "GEV",
    "SMCI", "ARM", "MU", "005930.KS", "000660.KS", "012450.KS"
]

def compute_all_indicators(ticker):
    try:
        df = yf.download(ticker, period="2y", interval="1d", progress=False)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        df = df.dropna(subset=['Close', 'High', 'Low', 'Volume']).copy()
        if len(df) < 60:
            return None
            
        high_9 = df['High'].rolling(window=9).max()
        low_9 = df['Low'].rolling(window=9).min()
        df['Tenkan'] = (high_9 + low_9) / 2

        high_26 = df['High'].rolling(window=26).max()
        low_26 = df['Low'].rolling(window=26).min()
        df['Kijun'] = (high_26 + low_26) / 2

        high_52 = df['High'].rolling(window=52).max()
        low_52 = df['Low'].rolling(window=52).min()

        # Raw Senkou Spans before 26-day forward shift
        df['RawSpanA'] = (df['Tenkan'] + df['Kijun']) / 2
        df['RawSpanB'] = (high_52 + low_52) / 2

        # Historical shifted spans for today's candle alignment
        df['SpanA'] = df['RawSpanA'].shift(26)
        df['SpanB'] = df['RawSpanB'].shift(26)

        df['SMA20'] = df['Close'].rolling(window=20).mean()
        df['SMA60'] = df['Close'].rolling(window=60).mean()
        df['Vol_SMA20'] = df['Volume'].rolling(window=20).mean()
        df['Vol_Ratio'] = df['Volume'] / df['Vol_SMA20']
        
        candles = []
        tenkan_pts = []
        kijun_pts = []
        span_a_pts = []
        span_b_pts = []
        sma20_pts = []
        sma60_pts = []
        vol_pts = []
        
        # 500 daily trading days (2 full years) for seamless zoom without clipping
        df_clean = df.dropna(subset=['Close', 'High', 'Low', 'Kijun', 'Tenkan']).tail(500)
        
        # 1. Historical Data Points
        for idx, row in df_clean.iterrows():
            time_str = idx.strftime("%Y-%m-%d")
            candles.append({
                "time": time_str,
                "open": round(float(row['Open']), 2),
                "high": round(float(row['High']), 2),
                "low": round(float(row['Low']), 2),
                "close": round(float(row['Close']), 2)
            })
            if not pd.isna(row['Tenkan']):
                tenkan_pts.append({"time": time_str, "value": round(float(row['Tenkan']), 2)})
            if not pd.isna(row['Kijun']):
                kijun_pts.append({"time": time_str, "value": round(float(row['Kijun']), 2)})
            if not pd.isna(row['SpanA']):
                span_a_pts.append({"time": time_str, "value": round(float(row['SpanA']), 2)})
            if not pd.isna(row['SpanB']):
                span_b_pts.append({"time": time_str, "value": round(float(row['SpanB']), 2)})
            if not pd.isna(row['SMA20']):
                sma20_pts.append({"time": time_str, "value": round(float(row['SMA20']), 2)})
            if not pd.isna(row['SMA60']):
                sma60_pts.append({"time": time_str, "value": round(float(row['SMA60']), 2)})
            if not pd.isna(row['Volume']):
                vol_pts.append({
                    "time": time_str,
                    "value": float(row['Volume']),
                    "color": "#059669" if row['Close'] >= row['Open'] else "#dc2626"
                })
                
        # 2. Future 26-Trading-Day Ichimoku Cloud Projection (향후 26일 미래 구름대 확장)
        last_date = df_clean.index[-1]
        future_dates = pd.bdate_range(start=last_date + pd.Timedelta(days=1), periods=26)
        raw_a_series = df_clean['RawSpanA'].dropna()
        raw_b_series = df_clean['RawSpanB'].dropna()
        
        future_span_a_vals = []
        future_span_b_vals = []
        
        for k in range(len(future_dates)):
            f_time_str = future_dates[k].strftime("%Y-%m-%d")
            # Pull the 26-day forward projections from raw spans
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
            
        last = df_clean.iloc[-1]
        close = float(last['Close'])
        kijun = float(last['Kijun'])
        tenkan = float(last['Tenkan'])
        vol_ratio = float(last['Vol_Ratio'])
        span_a = float(last['SpanA']) if not pd.isna(last['SpanA']) else close
        span_b = float(last['SpanB']) if not pd.isna(last['SpanB']) else close
        
        cloud_top = max(span_a, span_b)
        cloud_bottom = min(span_a, span_b)
        kijun_gap = ((close - kijun) / kijun) * 100
        
        # Future cloud characteristics (+26 days ahead)
        future_a_latest = future_span_a_vals[-1] if future_span_a_vals else span_a
        future_b_latest = future_span_b_vals[-1] if future_span_b_vals else span_b
        is_future_bull_cloud = future_a_latest >= future_b_latest
        future_cloud_type = "양운 (상승 지지 구름대)" if is_future_bull_cloud else "음운 (하락 저항 구름대)"
        future_cloud_gap = abs(future_a_latest - future_b_latest)
        
        # 17-Year Quant Exact Scoring
        bull_score = 0
        if close >= cloud_top: bull_score += 35
        if -0.5 <= kijun_gap <= 4.0: bull_score += 35
        if vol_ratio <= 0.75: bull_score += 20
        if tenkan >= kijun: bull_score += 10
        
        bear_score = 0
        if close < kijun: bear_score += 40
        if close < cloud_bottom: bear_score += 35
        if kijun_gap < -2.0: bear_score += 15
        
        if bull_score >= 70:
            quant_type = "BULL"
            quant_verdict = "Bull Accumulation (1차 분할 매수 적합)"
            quant_score_text = f"{bull_score} / 100 pt (BULL_BUY)"
            action_directive = "26일 기준선 및 일목 구름대 상단 안착 확인. 거시 변동성 진정 시 1차 분할 매수 적합."
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
            
        # Indicator Detail Cards
        kijun_status = "status-bull" if -0.5 <= kijun_gap <= 4.0 else ("status-bear" if kijun_gap < -0.5 else "status-neutral")
        kijun_badge = "SUPPORTED" if -0.5 <= kijun_gap <= 4.0 else ("BREAKDOWN" if kijun_gap < -0.5 else "OVERHEATED")
        kijun_desc = f"현재가 ${close:,.2f} / 26일선 ${kijun:,.2f} (이격 {kijun_gap:+.1f}%)"
        
        tenkan_status = "status-bull" if tenkan >= kijun else "status-bear"
        tenkan_badge = "GOLDEN CROSS" if tenkan >= kijun else "DEAD CROSS"
        tenkan_desc = f"9일 전환선 ${tenkan:,.2f} {'상단 정배열' if tenkan >= kijun else '하단 역배열'}"
        
        cloud_status = "status-bull" if close >= cloud_top else ("status-bear" if close < cloud_bottom else "status-neutral")
        cloud_badge = "ABOVE CLOUD" if close >= cloud_top else ("BELOW CLOUD" if close < cloud_bottom else "INSIDE CLOUD")
        cloud_desc = f"일목 구름대({round(cloud_bottom,1)}~{round(cloud_top,1)}) {'상단 안착' if close >= cloud_top else ('하단 붕괴' if close < cloud_bottom else '내부 횡보')}"
        
        vol_status = "status-bull" if vol_ratio <= 0.75 else ("status-neutral" if vol_ratio <= 1.2 else "status-bear")
        vol_badge = "VOLUME DRY" if vol_ratio <= 0.75 else ("NORMAL VOL" if vol_ratio <= 1.2 else "HIGH VOL")
        vol_desc = f"20일 평균 거래량 대비 {round(vol_ratio*100)}% ({'매도세 고갈 완벽' if vol_ratio <= 0.75 else '통상 거래량'})"
        
        intelligence = {
            "verdict": quant_verdict,
            "score": quant_score_text,
            "bull_score": bull_score,
            "bear_score": bear_score,
            "type": quant_type,
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
            "intelligence": intelligence,
            "status_tag": quant_type,
            "status_text": quant_verdict,
            "timeframe": "1D",
            "period": "2Y",
            "candles": candles,
            "kijun_line": kijun_pts,
            "tenkan_line": tenkan_pts,
            "span_a_line": span_a_pts,
            "span_b_line": span_b_pts,
            "sma20": sma20_pts,
            "sma60": sma60_pts,
            "volume": vol_pts
        }
    except Exception as e:
        print(f"Error computing {ticker}: {e}")
        return None

def build_dashboard_data():
    print("Building full dashboard data feed...")
    
    trades = []
    if os.path.exists(HISTORY_CSV):
        try:
            df_hist = pd.read_csv(HISTORY_CSV)
            trades = df_hist.to_dict(orient="records")
        except Exception:
            pass
            
    # Macro context from YouTube stream cache
    macro_info = {}
    if os.path.exists(STREAM_CACHE):
        try:
            with open(STREAM_CACHE, "r", encoding="utf-8") as f:
                macro_info = json.load(f)
        except Exception:
            pass
            
    chart_data = {}
    for t in WATCHLIST:
        data = compute_all_indicators(t)
        if data:
            chart_data[t] = data
            
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

    payload = {
        "macro": macro_info,
        "kpis": kpis,
        "trades": trades,
        "matrix": matrix,
        "portfolio": portfolio,
        "charts": chart_data
    }
    
    out_path = os.path.join(BASE_DIR, OUTPUT_JSON)
    atomic_save_json(out_path, payload)
        
    print(f"Successfully generated dashboard feed: {out_path}")
    return payload

if __name__ == "__main__":
    build_dashboard_data()
