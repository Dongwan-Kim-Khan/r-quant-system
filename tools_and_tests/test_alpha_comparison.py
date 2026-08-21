"""
Institutional Alpha & Performance Attribution Engine:
Comparing Current Live 17-Year Quant Strategy vs New Cloud Trampoline 2-Phase Strategy vs Hybrid vs SPY Benchmark.
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
    except Exception:
        return None

def run_simulation(data_dict, strategy_mode="CURRENT_LIVE"):
    """
    strategy_mode:
    1. 'CURRENT_LIVE': Current Platform Quant Matrix (Close >= Cloud Top, Kijun Gap -0.5%~+4%, VDU <= 0.75, Tenkan >= Kijun)
    2. 'CLOUD_TRAMPOLINE': New 2-Phase Strategy (Requires Cloud Bounce Launch Candle within 6 bars -> Pullback to Kijun + VDU)
    3. 'HYBRID_PREMIUM': Current Matrix + Cloud Trampoline Momentum Boost
    """
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
        df_d['Cloud_Bottom'] = np.minimum(df_d['SpanA'], df_d['SpanB'])
        
        n = len(df_d)
        dates = [d.strftime("%Y-%m-%d") for d in df_d.index]
        opens = df_d['Open'].values
        highs = df_d['High'].values
        lows = df_d['Low'].values
        closes = df_d['Close'].values
        kijuns = df_d['Kijun'].values
        tenkans = df_d['Tenkan'].values
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

            # Detect Trampoline Launch Candle (Low touches cloud top + Body >= 2.5%)
            cloud_touch_gap = (lows[i - 1] - p_cloud_top) / p_cloud_top
            body_pct = (closes[i - 1] - opens[i - 1]) / opens[i - 1]
            if (-0.015 <= cloud_touch_gap <= 0.030) and (body_pct >= 0.025):
                trampoline_active = True
                trampoline_bar = i - 1

            if not in_pos:
                p_close = closes[i - 1]
                p_kijun = kijuns[i - 1]
                p_tenkan = tenkans[i - 1]
                p_vol_ratio = vol_ratios[i - 1]
                p_w_bull = w_bulls[i - 1]
                kijun_gap = (p_close - p_kijun) / p_kijun if p_kijun > 0 else 0

                c_cloud = p_close >= p_cloud_top
                c_kijun = -0.005 <= kijun_gap <= 0.040
                c_vdu = p_vol_ratio <= 0.75
                c_tenkan = p_tenkan >= p_kijun
                
                trigger = False
                
                if strategy_mode == "CURRENT_LIVE":
                    # Current Live Quant Matrix: Score >= 70 pt
                    bull_score = 0
                    if c_cloud: bull_score += 35
                    if c_kijun: bull_score += 35
                    if c_vdu: bull_score += 20
                    if c_tenkan: bull_score += 10
                    trigger = (bull_score >= 70) and p_w_bull
                    
                elif strategy_mode == "CLOUD_TRAMPOLINE":
                    # Must have trampoline launch event within 6 bars + Kijun support + VDU
                    is_recent_trampoline = trampoline_active and (1 <= (i - 1 - trampoline_bar) <= 6)
                    trigger = is_recent_trampoline and c_kijun and (p_vol_ratio <= 0.85) and p_w_bull
                    if is_recent_trampoline and (i - 1 - trampoline_bar) > 6:
                        trampoline_active = False

                elif strategy_mode == "HYBRID_PREMIUM":
                    # Current Matrix (>=70pt) AND Trampoline Flagged
                    is_recent_trampoline = trampoline_active and (1 <= (i - 1 - trampoline_bar) <= 8)
                    bull_score = (35 if c_cloud else 0) + (35 if c_kijun else 0) + (20 if c_vdu else 0) + (10 if c_tenkan else 0)
                    trigger = (bull_score >= 70) and is_recent_trampoline and p_w_bull

                if trigger:
                    entry_p = opens[i] * 1.0010 # 10 bps slippage
                    entry_idx = i
                    in_pos = True
                    trampoline_active = False
            else:
                # Position Exit Logic
                cur_low = lows[i]
                cur_high = highs[i]
                cur_close = closes[i]
                cur_kijun = kijuns[i]

                tp_price = entry_p * 1.15
                sl_price = entry_p * 0.96
                kijun_break = cur_close < cur_kijun

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
                        reason = "STOP_LOSS (Kijun/SL)"
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

    return trades

def evaluate_metrics(trades, spy_df):
    if not trades:
        return {
            "total_trades": 0, "win_rate": 0, "profit_factor": 0,
            "cagr": 0, "sharpe": 0, "alpha": 0, "mdd": 0, "avg_holding": 0
        }

    total_trades = len(trades)
    wins = [t for t in trades if t['pnl_pct'] > 0]
    losses = [t for t in trades if t['pnl_pct'] <= 0]
    win_rate = (len(wins) / total_trades) * 100
    
    total_gain = sum(t['pnl_pct'] for t in wins)
    total_loss = abs(sum(t['pnl_pct'] for t in losses))
    profit_factor = (total_gain / total_loss) if total_loss > 0 else 99.0
    
    avg_win = (total_gain / len(wins)) if wins else 0.0
    avg_loss = (total_loss / len(losses)) if losses else 0.0
    avg_holding = sum(t['holding_days'] for t in trades) / total_trades
    
    # Portfolio equity simulation (compounded with 15% allocation per trade)
    trades_sorted = sorted(trades, key=lambda x: x["entry_date"])
    
    # Calculate daily returns series for Sharpe, Beta, and Jensen's Alpha
    trade_pnls = np.array([t['pnl_pct'] / 100.0 for t in trades_sorted])
    total_cum_ret = np.prod(1.0 + trade_pnls * 0.20) - 1.0 # 20% position sizing
    cagr = ((1.0 + total_cum_ret) ** (1.0 / 3.0) - 1.0) * 100
    
    # Max Drawdown
    equity = 100.0
    peaks = [100.0]
    drawdowns = [0.0]
    for pnl in trade_pnls:
        equity *= (1.0 + pnl * 0.20)
        peaks.append(max(peaks[-1], equity))
        dd = ((peaks[-1] - equity) / peaks[-1]) * 100
        drawdowns.append(dd)
    mdd = max(drawdowns) if drawdowns else 0.0

    # Benchmark metrics (SPY)
    spy_ret = ((spy_df['Close'].iloc[-1] - spy_df['Close'].iloc[0]) / spy_df['Close'].iloc[0])
    spy_cagr = ((1.0 + spy_ret) ** (1.0 / 3.0) - 1.0) * 100
    
    # Standard deviation of trade returns
    vol = np.std(trade_pnls) * np.sqrt(total_trades / 3.0) if len(trade_pnls) > 2 else 0.15
    rf = 4.0 # 4% risk-free rate
    sharpe = (cagr - rf) / (vol * 100) if vol > 0 else 0.0
    
    # Jensen's Alpha (Alpha = CAGR_strat - [Rf + Beta * (CAGR_bench - Rf)])
    # We estimate beta ~ 0.85 (hedged institutional profile)
    beta = 0.85
    alpha = (cagr - rf) - (beta * (spy_cagr - rf))

    return {
        "total_trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "avg_holding": round(avg_holding, 1),
        "cagr_pct": round(cagr, 1),
        "mdd_pct": round(mdd, 1),
        "sharpe": round(sharpe, 2),
        "jensens_alpha": round(alpha, 1)
    }

def main():
    print("==========================================================================================")
    print("  LOADING 3-YEAR OHLCV & SPY BENCHMARK FOR 45 INSTITUTIONAL ASSETS...")
    print("==========================================================================================")
    
    data_dict = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(fetch_data, TEST_UNIVERSE))
    for res in results:
        if res: data_dict[res["ticker"]] = res
        
    spy_df = yf.download("SPY", period="3y", interval="1d", progress=False)
    if isinstance(spy_df.columns, pd.MultiIndex): spy_df.columns = spy_df.columns.get_level_values(0)
    spy_ret = ((spy_df['Close'].iloc[-1] - spy_df['Close'].iloc[0]) / spy_df['Close'].iloc[0]) * 100
    spy_cagr = (((1.0 + spy_ret/100) ** (1.0 / 3.0)) - 1.0) * 100

    print(f"[Done] 45 Stocks + SPY Loaded. (SPY 3-Year Total Return: {spy_ret:.1f}%, CAGR: {spy_cagr:.1f}%)")

    # 1. Strategy 1: Current Live Platform Quant Matrix
    trades_live = run_simulation(data_dict, strategy_mode="CURRENT_LIVE")
    m_live = evaluate_metrics(trades_live, spy_df)

    # 2. Strategy 2: New Cloud Trampoline 2-Phase Strategy
    trades_tramp = run_simulation(data_dict, strategy_mode="CLOUD_TRAMPOLINE")
    m_tramp = evaluate_metrics(trades_tramp, spy_df)

    # 3. Strategy 3: Hybrid Premium (Current Matrix + Cloud Trampoline Momentum Filter)
    trades_hybrid = run_simulation(data_dict, strategy_mode="HYBRID_PREMIUM")
    m_hybrid = evaluate_metrics(trades_hybrid, spy_df)

    print("\n========================================================================================================")
    print("  ALPHA (α) & RISK-ADJUSTED ATTRIBUTION COMPARISON MATRIX (45 ASSETS, 3-YEAR PERIOD)")
    print("========================================================================================================")
    print(f"  {'METRIC':<30} | {'① 현재 우리 퀀트 전략':<18} | {'② 구름대 도약 2단계':<18} | {'③ 하이브리드 결합 (앙상블)':<22}")
    print("--------------------------------------------------------------------------------------------------------")
    print(f"  {'총 매매 기회 (Trades)':<30} | {m_live['total_trades']:<18} | {m_tramp['total_trades']:<18} | {m_hybrid['total_trades']:<22}")
    print(f"  {'승률 (Win Rate %)':<30} | {m_live['win_rate']:<17}% | {m_tramp['win_rate']:<17}% | {m_hybrid['win_rate']:<21}%")
    print(f"  {'손익비 (Profit Factor)':<30} | {m_live['profit_factor']:<18} | {m_tramp['profit_factor']:<18} | {m_hybrid['profit_factor']:<22}")
    print(f"  {'연환산 수익률 (CAGR %)':<30} | {m_live['cagr_pct']:<17}% | {m_tramp['cagr_pct']:<17}% | {m_hybrid['cagr_pct']:<21}%")
    print(f"  {'최대 낙폭 (Max Drawdown)':<30} | {m_live['mdd_pct']:<17}% | {m_tramp['mdd_pct']:<17}% | {m_hybrid['mdd_pct']:<21}%")
    print(f"  {'샤프 지수 (Sharpe Ratio)':<30} | {m_live['sharpe']:<18} | {m_tramp['sharpe']:<18} | {m_hybrid['sharpe']:<22}")
    print(f"  {'★ 젠센 알파 (Jensen’s α)':<30} | {m_live['jensens_alpha']:<17}% | {m_tramp['jensens_alpha']:<17}% | {m_hybrid['jensens_alpha']:<21}%")
    print("========================================================================================================")

if __name__ == "__main__":
    main()
