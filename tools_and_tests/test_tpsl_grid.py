"""
Sensitivity Matrix: TP/SL Grid Parameter Optimization on 45 Institutional Tickers.
"""
import os
import sys
import numpy as np
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor

# Windows encoding fix
if sys.platform.startswith('win'):
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.domain.quant.ichimoku import calculate_ichimoku_indicators

TEST_UNIVERSE = [
    "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "TSM", "AMD", 
    "QCOM", "MU", "INTC", "ARM", "ASML", "PLTR", "ORCL", "CRM", "NOW", "PANW",
    "VST", "CEG", "GEV", "ETN", "COIN", "MSTR", "CRWD", "HOOD", "RKLB", "IONQ", "NFLX",
    "AMAT", "LRCX", "KLAC", "SMCI", "TXN",
    "LLY", "COST", "WMT", "UNH", "JNJ", "V", "MA", "JPM", "XOM"
]

def fetch_data(ticker):
    try:
        df_d = yf.download(ticker, period="3y", interval="1d", progress=False)
        df_w = yf.download(ticker, period="3y", interval="1wk", progress=False)
        if df_d.empty or df_w.empty: return None
        if isinstance(df_d.columns, pd.MultiIndex): df_d.columns = df_d.columns.get_level_values(0)
        if isinstance(df_w.columns, pd.MultiIndex): df_w.columns = df_w.columns.get_level_values(0)
        df_d = calculate_ichimoku_indicators(df_d.dropna(subset=['Close', 'High', 'Low', 'Volume']))
        df_w = calculate_ichimoku_indicators(df_w.dropna(subset=['Close', 'High', 'Low', 'Volume']))
        return {"ticker": ticker, "daily": df_d, "weekly": df_w}
    except Exception: return None

def test_tpsl_grid(data_dict, tp_pct, sl_pct):
    trades = []
    for ticker, bundle in data_dict.items():
        df_d = bundle["daily"].copy()
        df_w = bundle["weekly"].copy()
        if len(df_d) < 60 or len(df_w) < 30: continue
        
        df_w['W_Cloud_Top'] = np.maximum(df_w['SpanA'], df_w['SpanB'])
        df_w['W_Bull_Trend'] = (df_w['Close'] >= df_w['W_Cloud_Top']) & (df_w['Tenkan'] >= df_w['Kijun'])
        w_stance = df_w[['W_Bull_Trend']].reindex(df_d.index, method='ffill')
        df_d['W_Bull_Trend'] = w_stance['W_Bull_Trend'].fillna(False)
        
        df_d['Cloud_Top'] = np.maximum(df_d['SpanA'], df_d['SpanB'])
        n = len(df_d)
        opens = df_d['Open'].values
        highs = df_d['High'].values
        lows = df_d['Low'].values
        closes = df_d['Close'].values
        kijuns = df_d['Kijun'].values
        vol_ratios = df_d['Vol_Ratio'].values
        cloud_tops = df_d['Cloud_Top'].values
        w_bulls = df_d['W_Bull_Trend'].values

        in_pos = False
        entry_p = 0.0
        entry_idx = 0
        trampoline_active = False
        trampoline_bar = 0

        for i in range(5, n):
            p_cloud_top = cloud_tops[i - 1]
            if np.isnan(p_cloud_top) or p_cloud_top <= 0: continue

            cloud_touch_gap = (lows[i - 1] - p_cloud_top) / p_cloud_top
            body_pct = (closes[i - 1] - opens[i - 1]) / opens[i - 1]
            if (-0.015 <= cloud_touch_gap <= 0.030) and (body_pct >= 0.025):
                trampoline_active = True
                trampoline_bar = i - 1

            if not in_pos:
                p_close = closes[i - 1]
                p_kijun = kijuns[i - 1]
                p_vol_ratio = vol_ratios[i - 1]
                p_w_bull = w_bulls[i - 1]
                kijun_gap = (p_close - p_kijun) / p_kijun if p_kijun > 0 else 0

                is_recent_trampoline = trampoline_active and (1 <= (i - 1 - trampoline_bar) <= 6)
                if is_recent_trampoline and (-0.005 <= kijun_gap <= 0.040) and (p_vol_ratio <= 0.85) and p_w_bull:
                    entry_p = opens[i] * 1.0010
                    entry_idx = i
                    in_pos = True
                    trampoline_active = False
            else:
                cur_low = lows[i]
                cur_high = highs[i]
                cur_close = closes[i]
                cur_kijun = kijuns[i]

                tp_price = entry_p * (1.0 + tp_pct / 100.0)
                sl_price = entry_p * (1.0 - sl_pct / 100.0)
                kijun_break = cur_close < cur_kijun

                hit_tp = cur_high >= tp_price
                hit_sl = cur_low <= sl_price or kijun_break
                hit_timeout = (i - entry_idx) >= 50
                is_last = (i == n - 1)

                if hit_tp or hit_sl or hit_timeout or is_last:
                    raw_exit = max(opens[i], tp_price) if hit_tp else (min(opens[i], sl_price) if cur_low <= sl_price else cur_close)
                    exit_p = raw_exit * 0.9990
                    pnl_pct = (((exit_p * 0.9992) - entry_p) / entry_p) * 100
                    trades.append(pnl_pct)
                    in_pos = False
                    entry_p = 0.0

    if not trades: return {"win_rate": 0, "profit_factor": 0, "total_pnl": 0}
    wins = [p for p in trades if p > 0]
    losses = [p for p in trades if p <= 0]
    win_rate = (len(wins) / len(trades)) * 100
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else 99.0
    return {
        "trades": len(trades),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(pf, 2),
        "total_pnl": round(sum(trades), 1)
    }

def main():
    data_dict = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(fetch_data, TEST_UNIVERSE))
    for res in results:
        if res: data_dict[res["ticker"]] = res

    tp_options = [8.0, 10.0, 12.0, 15.0, 20.0]
    sl_options = [2.5, 3.0, 4.0, 5.0]

    print("==========================================================================================")
    print("  TP (익절) & SL (손절) 민감도 분석 그리드 매트릭스 (45개 종목, 3개년)")
    print("==========================================================================================")
    print(f"  {'익절 목표 (TP)':<14} | {'손절선 (SL)':<12} | {'승률 (Win Rate %)':<18} | {'손익비 (PF)':<14} | {'누적 합산 수익':<16}")
    print("------------------------------------------------------------------------------------------")
    for tp in tp_options:
        for sl in sl_options:
            res = test_tpsl_grid(data_dict, tp, sl)
            tp_str = f"+{tp:.1f}%"
            sl_str = f"-{sl:.1f}%"
            wr_str = f"{res['win_rate']:.1f}%"
            pf_str = f"{res['profit_factor']:.2f}"
            pnl_str = f"+{res['total_pnl']:.1f}%" if res['total_pnl'] > 0 else f"{res['total_pnl']:.1f}%"
            print(f"  {tp_str:<14} | {sl_str:<12} | {wr_str:<18} | {pf_str:<14} | {pnl_str:<16}")
        print("  " + "-"*80)

if __name__ == "__main__":
    main()
