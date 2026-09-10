"""
Refined Institutional Backtest:
Comparing Chase on Breakout vs Al-Sangmoo 2-Phase Pullback Entry (Waiting for VDU & Kijun Support).
"""
import os
import sys
import numpy as np
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor

# Windows encoding fix
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

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

def fetch_stock_data(ticker):
    try:
        df_daily = yf.download(ticker, period="3y", interval="1d", progress=False)
        df_weekly = yf.download(ticker, period="3y", interval="1wk", progress=False)
        
        if df_daily.empty or df_weekly.empty:
            return None
            
        if isinstance(df_daily.columns, pd.MultiIndex):
            df_daily.columns = df_daily.columns.get_level_values(0)
        if isinstance(df_weekly.columns, pd.MultiIndex):
            df_weekly.columns = df_weekly.columns.get_level_values(0)
            
        df_daily = calculate_ichimoku_indicators(df_daily.dropna(subset=['Close', 'High', 'Low', 'Volume']))
        df_weekly = calculate_ichimoku_indicators(df_weekly.dropna(subset=['Close', 'High', 'Low', 'Volume']))
        
        return {"ticker": ticker, "daily": df_daily, "weekly": df_weekly}
    except Exception:
        return None

def simulate_alsangmoo_refined(data_dict, mode="CHASE_BREAKOUT"):
    """
    mode:
    - 'CHASE_BREAKOUT': Enter immediately on the day after the big breakout candle.
    - 'ALSANGMOO_PULLBACK': Flag breakout radar -> Wait for 2-5 day pullback to Kijun (-0.5% ~ +4%) & VDU (<=0.80) to enter!
    """
    trades = []
    
    for ticker, bundle in data_dict.items():
        df_d = bundle["daily"].copy()
        df_w = bundle["weekly"].copy()
        
        if len(df_d) < 60 or len(df_w) < 30:
            continue
            
        df_w['W_Cloud_Top'] = np.maximum(df_w['SpanA'], df_w['SpanB'])
        df_w['W_Bull_Trend'] = (df_w['Close'] >= df_w['W_Cloud_Top']) & (df_w['Tenkan'] >= df_w['Kijun'])
        w_stance = df_w[['W_Bull_Trend']].reindex(df_d.index, method='ffill')
        df_d['W_Bull_Trend'] = w_stance['W_Bull_Trend'].fillna(False)
        
        df_d['Cloud_Top'] = np.maximum(df_d['SpanA'], df_d['SpanB'])
        df_d['Cloud_Bottom'] = np.minimum(df_d['SpanA'], df_d['SpanB'])
        
        n = len(df_d)
        dates = [d.strftime("%Y-%m-%d") for d in df_d.index]
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
        radar_active = False
        radar_bar = 0

        for i in range(5, n):
            # Check if Weekly Bullish Stance is Active
            w_ok = w_bulls[i - 1]
            p_cloud_top = cloud_tops[i - 1]
            if np.isnan(p_cloud_top) or p_cloud_top <= 0 or not w_ok:
                radar_active = False
                continue

            # 1. Detect Cloud Bounce Signal
            cloud_touch_gap = (lows[i - 1] - p_cloud_top) / p_cloud_top
            body_pct = (closes[i - 1] - opens[i - 1]) / opens[i - 1]
            c_bounce = (-0.015 <= cloud_touch_gap <= 0.030) and (body_pct >= 0.025)
            
            if c_bounce and not in_pos:
                radar_active = True
                radar_bar = i - 1

            if not in_pos:
                if mode == "CHASE_BREAKOUT":
                    if c_bounce:
                        entry_p = opens[i] * 1.0010 # Enter immediately next morning
                        entry_idx = i
                        in_pos = True
                        radar_active = False
                elif mode == "ALSANGMOO_PULLBACK":
                    # Wait up to 6 bars after radar for the sweet-spot pullback:
                    if radar_active and (1 <= (i - 1 - radar_bar) <= 6):
                        cur_close = closes[i - 1]
                        cur_kijun = kijuns[i - 1]
                        cur_vol_ratio = vol_ratios[i - 1]
                        kijun_gap = (cur_close - cur_kijun) / cur_kijun if cur_kijun > 0 else 0
                        
                        # Al-Sangmoo Entry Criteria: Kijun Support (-0.5% ~ +4.0%) AND VDU (<= 0.85)
                        if (-0.005 <= kijun_gap <= 0.040) and (cur_vol_ratio <= 0.85):
                            entry_p = opens[i] * 1.0010
                            entry_idx = i
                            in_pos = True
                            radar_active = False
                    elif radar_active and (i - 1 - radar_bar) > 6:
                        radar_active = False # Expired without optimal pullback
            else:
                # Active Position Management
                cur_low = lows[i]
                cur_high = highs[i]
                cur_close = closes[i]
                cur_kijun = kijuns[i]

                tp_price = entry_p * 1.15      # Target +15%
                sl_price = entry_p * 0.96      # -4.0% Protection
                kijun_break = cur_close < cur_kijun # 26-Day Kijun (생명선) 이탈

                hit_tp = cur_high >= tp_price
                hit_sl = cur_low <= sl_price or kijun_break
                hit_timeout = (i - entry_idx) >= 50
                is_last = (i == n - 1)

                if hit_tp or hit_sl or hit_timeout or is_last:
                    if hit_tp:
                        raw_exit = max(opens[i], tp_price)
                        reason = "TAKE_PROFIT (+15%)"
                    elif hit_sl:
                        raw_exit = min(opens[i], sl_price) if cur_low <= sl_price else cur_close
                        reason = "STOP_LOSS (Kijun Break / -5%)"
                    elif hit_timeout:
                        raw_exit = cur_close
                        reason = "TIME_EXIT"
                    else:
                        raw_exit = cur_close
                        reason = "END"

                    exit_p = raw_exit * 0.9990
                    pnl_pct = (((exit_p * 0.9992) - entry_p) / entry_p) * 100

                    trades.append({
                        "ticker": ticker,
                        "entry_date": dates[entry_idx],
                        "exit_date": dates[i],
                        "entry_price": round(entry_p, 2),
                        "exit_price": round(exit_p, 2),
                        "pnl_pct": round(pnl_pct, 2),
                        "holding_days": i - entry_idx,
                        "reason": reason
                    })
                    
                    in_pos = False
                    entry_p = 0.0

    total_trades = len(trades)
    if total_trades == 0:
        return {"total_trades": 0, "win_rate": 0, "profit_factor": 0, "trades": []}

    wins = [t for t in trades if t['pnl_pct'] > 0]
    losses = [t for t in trades if t['pnl_pct'] <= 0]
    win_rate = (len(wins) / total_trades) * 100
    
    total_gain = sum(t['pnl_pct'] for t in wins)
    total_loss = abs(sum(t['pnl_pct'] for t in losses))
    profit_factor = (total_gain / total_loss) if total_loss > 0 else 99.0
    
    avg_win = (total_gain / len(wins)) if wins else 0.0
    avg_loss = (total_loss / len(losses)) if losses else 0.0
    avg_holding = sum(t['holding_days'] for t in trades) / total_trades
    total_cum_pnl = sum(t['pnl_pct'] for t in trades)
    
    equity = 100.0
    peaks = [equity]
    drawdowns = [0.0]
    for t in trades:
        equity *= (1.0 + (t['pnl_pct'] / 100.0))
        peaks.append(max(peaks[-1], equity))
        dd = ((peaks[-1] - equity) / peaks[-1]) * 100
        drawdowns.append(dd)
    mdd = max(drawdowns) if drawdowns else 0.0

    return {
        "total_trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "avg_holding_days": round(avg_holding, 1),
        "total_cum_pnl": round(total_cum_pnl, 1),
        "max_drawdown_pct": round(mdd, 1),
        "trades": trades
    }

def main():
    data_dict = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(fetch_stock_data, TEST_UNIVERSE))
    for res in results:
        if res: data_dict[res["ticker"]] = res

    # Simulation 1: Chase immediately after breakout
    res_chase = simulate_alsangmoo_refined(data_dict, mode="CHASE_BREAKOUT")
    
    # Simulation 2: Al-Sangmoo 2-Phase (Wait for Pullback to Kijun & VDU)
    res_pullback = simulate_alsangmoo_refined(data_dict, mode="ALSANGMOO_PULLBACK")

    print("==========================================================================================")
    print(f"  45-TICKER S&P500/NASDAQ 3-YEAR QUANT BACKTEST: CHASE vs AL-SANGMOO PULLBACK")
    print("==========================================================================================")
    print(f"  {'METRIC':<32} | {'방식 A: 장대양봉 추격 매수':<24} | {'방식 B: 알상무 2단계 눌림목(VDU)':<26}")
    print("------------------------------------------------------------------------------------------")
    print(f"  {'총 매매 진입 횟수 (Trades)':<32} | {res_chase['total_trades']:<24} | {res_pullback['total_trades']:<26}")
    print(f"  {'승률 (Win Rate %)':<32} | {res_chase['win_rate']:<23}% | {res_pullback['win_rate']:<25}%")
    print(f"  {'손익비 (Profit Factor)':<32} | {res_chase['profit_factor']:<24} | {res_pullback['profit_factor']:<26}")
    print(f"  {'평균 익절 수익률 (Avg Win)':<32} | +{res_chase['avg_win_pct']:<22}% | +{res_pullback['avg_win_pct']:<24}%")
    print(f"  {'평균 손절 손실률 (Avg Loss)':<32} | -{res_chase['avg_loss_pct']:<22}% | -{res_pullback['avg_loss_pct']:<24}%")
    print(f"  {'누적 합산 수익률 (Total Return)':<32} | {res_chase['total_cum_pnl']:<23}% | {res_pullback['total_cum_pnl']:<25}%")
    print(f"  {'최대 계좌 낙폭 (Max Drawdown)':<32} | {res_chase['max_drawdown_pct']:<23}% | {res_pullback['max_drawdown_pct']:<25}%")
    print("==========================================================================================")

    print("\n[방식 B (알상무 2단계 눌림목) 성공 타점 샘플]:")
    sample_wins = [t for t in res_pullback['trades'] if t['pnl_pct'] > 10.0]
    for st in sample_wins[:8]:
        print(f"  • [{st['ticker']}] 진입: {st['entry_date']} (${st['entry_price']}) -> 청산: {st['exit_date']} (${st['exit_price']}) | 수익: +{st['pnl_pct']}% ({st['holding_days']}일 보유)")

if __name__ == "__main__":
    main()
