import os
import sys
import json
import pandas as pd
import yfinance as yf
from datetime import datetime

# Windows encoding fix
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

HISTORY_CSV = "trade_history.csv"
OUTPUT_JSON = "dashboard_data.json"

WATCHLIST = [
    "QQQ", "NVDA", "AMZN", "LLY", "AAPL", "MSFT", "TSLA", "META",
    "AVGO", "COST", "AMD", "QCOM", "PLTR", "005930.KS", "000660.KS"
]

def compute_all_indicators(ticker):
    try:
        df = yf.download(ticker, period="1y", interval="1d", progress=False)
        if df.empty or len(df) < 60:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        high_9 = df['High'].rolling(window=9).max()
        low_9 = df['Low'].rolling(window=9).min()
        df['Tenkan'] = (high_9 + low_9) / 2

        high_26 = df['High'].rolling(window=26).max()
        low_26 = df['Low'].rolling(window=26).min()
        df['Kijun'] = (high_26 + low_26) / 2

        df['SpanA'] = ((df['Tenkan'] + df['Kijun']) / 2).shift(26)
        high_52 = df['High'].rolling(window=52).max()
        low_52 = df['Low'].rolling(window=52).min()
        df['SpanB'] = ((high_52 + low_52) / 2).shift(26)

        df['SMA20'] = df['Close'].rolling(window=20).mean()
        df['SMA60'] = df['Close'].rolling(window=60).mean()
        df['Vol_SMA20'] = df['Volume'].rolling(window=20).mean()
        df['Vol_Ratio'] = df['Volume'] / df['Vol_SMA20']
        
        # Format candlestick data for Lightweight Charts
        candles = []
        tenkan_pts = []
        kijun_pts = []
        span_a_pts = []
        span_b_pts = []
        sma20_pts = []
        sma60_pts = []
        vol_pts = []
        
        df_clean = df.dropna().tail(120) # last 120 trading days
        for idx, row in df_clean.iterrows():
            time_str = idx.strftime("%Y-%m-%d")
            candles.append({
                "time": time_str,
                "open": round(float(row['Open']), 2),
                "high": round(float(row['High']), 2),
                "low": round(float(row['Low']), 2),
                "close": round(float(row['Close']), 2)
            })
            tenkan_pts.append({"time": time_str, "value": round(float(row['Tenkan']), 2)})
            kijun_pts.append({"time": time_str, "value": round(float(row['Kijun']), 2)})
            if not pd.isna(row['SpanA']):
                span_a_pts.append({"time": time_str, "value": round(float(row['SpanA']), 2)})
            if not pd.isna(row['SpanB']):
                span_b_pts.append({"time": time_str, "value": round(float(row['SpanB']), 2)})
            sma20_pts.append({"time": time_str, "value": round(float(row['SMA20']), 2)})
            sma60_pts.append({"time": time_str, "value": round(float(row['SMA60']), 2)})
            vol_pts.append({
                "time": time_str,
                "value": int(row['Volume']),
                "color": "#ef4444" if row['Close'] < row['Open'] else "#10b981"
            })
            
        last = df_clean.iloc[-1]
        prev = df_clean.iloc[-2]
        close = float(last['Close'])
        kijun = float(last['Kijun'])
        tenkan = float(last['Tenkan'])
        vol_ratio = float(last['Vol_Ratio'])
        
        # Diagnosis
        cloud_top = max(float(last['SpanA']), float(last['SpanB'])) if not pd.isna(last['SpanA']) else float(last['SMA60'])
        is_dry = vol_ratio <= 0.75
        kijun_gap = ((close - kijun) / kijun) * 100
        
        if close >= cloud_top and close >= kijun and is_dry and -0.5 <= kijun_gap <= 4.0:
            status_tag = "BULL_ACCUMULATION"
            status_text = "🟢 알상무 1차 매수 타점 (구름대 위 + 기준선 지지 + 거래량 마름)"
        elif close < kijun:
            status_tag = "BEAR_BREAKDOWN"
            status_text = "🔴 26일 기준선(생명선) 이탈 붕괴 (숏 경보 / 물타기 금지)"
        else:
            status_tag = "NEUTRAL"
            status_text = "🟡 박스권 에너지 응축 중 (추세 돌파 대기)"
            
        return {
            "ticker": ticker,
            "latest_close": round(close, 2),
            "kijun": round(kijun, 2),
            "tenkan": round(tenkan, 2),
            "kijun_gap": f"{kijun_gap:+.1f}%",
            "vol_ratio": f"{vol_ratio*100:.0f}%",
            "status_tag": status_tag,
            "status_text": status_text,
            "candles": candles,
            "tenkan_line": tenkan_pts,
            "kijun_line": kijun_pts,
            "span_a": span_a_pts,
            "span_b": span_b_pts,
            "sma20": sma20_pts,
            "sma60": sma60_pts,
            "volume": vol_pts
        }
    except Exception as e:
        print(f"Error computing {ticker}: {e}")
        return None

def build_dashboard_data():
    print("Building full dashboard data feed...")
    
    # 1. Read trade history
    trades = []
    if os.path.exists(HISTORY_CSV):
        try:
            df_hist = pd.read_csv(HISTORY_CSV)
            trades = df_hist.to_dict(orient="records")
        except Exception:
            pass
            
    # 2. Build Ticker Charts
    chart_data = {}
    for t in WATCHLIST:
        data = compute_all_indicators(t)
        if data:
            chart_data[t] = data
            
    # 3. Calculate Performance Metrics
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
    
    payload = {
        "kpis": kpis,
        "trades": trades,
        "charts": chart_data
    }
    
    out_path = os.path.join(r"D:\코딩\Playground\al_sangmoo_project", OUTPUT_JSON)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        
    print(f"Successfully generated dashboard feed: {out_path}")
    return payload

if __name__ == "__main__":
    build_dashboard_data()
