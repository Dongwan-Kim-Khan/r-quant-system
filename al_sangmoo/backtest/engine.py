"""
Institutional Friction-Aware Vectorized Quantitative Backtest Engine.
"""
import os
import sys
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime
from typing import Dict, Any, List, Union

from al_sangmoo.core.constants import STOP_LOSS_PCT, TAKE_PROFIT_PCT
from al_sangmoo.domain.quant.ichimoku import calculate_ichimoku_indicators

def run_backtest_simulation(
    data: Union[pd.DataFrame, str],
    period: str = "2y",
    initial_capital: float = 100000.0,
    slippage_bps: float = 0.0010,       # 10 bps (0.10%) bid-ask spread
    fee_rate: float = 0.0008,           # 8 bps (0.08%) commission + regulatory fees
    stop_loss_pct: float = STOP_LOSS_PCT,     # -5.0% C1-M2 hard stop
    take_profit_pct: float = TAKE_PROFIT_PCT,  # +15.0% trailing latch / primary target
    timeout_bars: int = 60              # 60-bar max swing horizon
) -> Dict[str, Any]:
    """
    Executes a high-fidelity friction-aware quantitative simulation.
    """
    ticker_name = "CUSTOM"
    if isinstance(data, str):
        ticker_name = data.strip().upper()
        df = yf.download(ticker_name, period=period, interval="1d", progress=False)
        if df.empty:
            raise ValueError(f"No price data found for ticker: {ticker_name}")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
    else:
        df = data.copy()

    df = calculate_ichimoku_indicators(df)
    df_clean = df.dropna(subset=['Tenkan', 'Kijun', 'SpanA', 'SpanB', 'Vol_Ratio']).copy()
    
    if len(df_clean) < 30:
        return {
            "status": "error",
            "message": "Insufficient historical data for backtesting."
        }

    n = len(df_clean)
    dates = [d.strftime("%Y-%m-%d") for d in df_clean.index]
    opens = df_clean['Open'].values
    highs = df_clean['High'].values
    lows = df_clean['Low'].values
    closes = df_clean['Close'].values
    volumes = df_clean['Volume'].values
    kijuns = df_clean['Kijun'].values
    tenkans = df_clean['Tenkan'].values
    span_as = df_clean['SpanA'].values
    span_bs = df_clean['SpanB'].values
    vol_ratios = df_clean['Vol_Ratio'].values

    cash = initial_capital
    position_shares = 0.0
    entry_price = 0.0
    entry_idx = 0
    in_position = False

    trades: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = []
    daily_equities = np.zeros(n)

    peak_equity = initial_capital
    max_drawdown = 0.0

    for i in range(1, n):
        cur_date = dates[i]
        
        if not in_position:
            # Gate-2 Entry Evaluation at Bar i-1
            prev_close = closes[i - 1]
            prev_kijun = kijuns[i - 1]
            prev_tenkan = tenkans[i - 1]
            prev_cloud_top = max(span_as[i - 1], span_bs[i - 1])
            prev_vol_ratio = vol_ratios[i - 1]
            prev_kijun_gap = (prev_close - prev_kijun) / prev_kijun if prev_kijun > 0 else 0.0

            c_cloud = prev_close >= prev_cloud_top
            c_kijun = -0.005 <= prev_kijun_gap <= 0.040
            c_vdu = prev_vol_ratio <= 0.75
            c_tenkan = prev_tenkan >= prev_kijun

            if c_cloud and c_kijun and c_vdu and c_tenkan:
                # Enter at Bar i Open (with slippage)
                raw_fill = opens[i]
                fill_price = raw_fill * (1.0 + slippage_bps)
                alloc = cash * 0.95  # Allocate 95% of available cash
                shares = (alloc * (1.0 - fee_rate)) / fill_price
                cash -= alloc
                position_shares = shares
                entry_price = fill_price
                entry_idx = i
                in_position = True
        else:
            # Exit Evaluation at Bar i
            cur_low = lows[i]
            cur_high = highs[i]
            cur_close = closes[i]
            cur_kijun = kijuns[i]

            stop_price = entry_price * (1.0 + stop_loss_pct)
            tp_price = entry_price * (1.0 + take_profit_pct)

            hit_stop = cur_low <= stop_price or cur_close < cur_kijun
            hit_tp = cur_high >= tp_price
            hit_timeout = (i - entry_idx) >= timeout_bars
            is_last_bar = (i == n - 1)

            if hit_stop or hit_tp or hit_timeout or is_last_bar:
                if hit_tp:
                    raw_exit = max(opens[i], tp_price)
                    reason = f"TAKE_PROFIT ({take_profit_pct:+.0%})"
                elif hit_stop:
                    raw_exit = min(opens[i], stop_price) if cur_low <= stop_price else cur_close
                    sl_label = f"{stop_loss_pct:.0%}"
                    reason = f"STOP_LOSS ({sl_label} / Kijun Breakdown)"
                elif hit_timeout:
                    raw_exit = cur_close
                    reason = "TIME_EXIT (60-Bar Timeout)"
                else:
                    raw_exit = cur_close
                    reason = "END_OF_BACKTEST"

                exit_price = raw_exit * (1.0 - slippage_bps)
                gross_proceeds = position_shares * exit_price
                net_proceeds = gross_proceeds * (1.0 - fee_rate)
                cash += net_proceeds
                
                trade_pnl_amt = net_proceeds - (position_shares * entry_price)
                trade_pnl_pct = ((exit_price - entry_price) / entry_price) * 100

                trades.append({
                    "entry_date": dates[entry_idx],
                    "entry_price": round(entry_price, 2),
                    "exit_date": cur_date,
                    "exit_price": round(exit_price, 2),
                    "shares": round(position_shares, 4),
                    "pnl_amount": round(trade_pnl_amt, 2),
                    "pnl_pct": round(trade_pnl_pct, 2),
                    "holding_bars": i - entry_idx,
                    "reason": reason
                })

                position_shares = 0.0
                entry_price = 0.0
                in_position = False

        # Compute Daily Equity
        current_equity = cash + (position_shares * closes[i])
        daily_equities[i] = current_equity
        if current_equity > peak_equity:
            peak_equity = current_equity
        drawdown = ((peak_equity - current_equity) / peak_equity) * 100 if peak_equity > 0 else 0.0
        if drawdown > max_drawdown:
            max_drawdown = drawdown

        equity_curve.append({
            "date": cur_date,
            "equity": round(current_equity, 2),
            "drawdown_pct": round(drawdown, 2)
        })

    # Summary Performance Metrics
    final_equity = daily_equities[-1] if daily_equities[-1] > 0 else cash
    total_return_pct = ((final_equity - initial_capital) / initial_capital) * 100
    
    # Daily returns for Sharpe Ratio
    daily_returns = np.diff(daily_equities[daily_equities > 0]) / daily_equities[daily_equities > 0][:-1]
    sharpe_ratio = 0.0
    if len(daily_returns) > 5 and np.std(daily_returns) > 0:
        sharpe_ratio = float((np.mean(daily_returns) / np.std(daily_returns)) * np.sqrt(252))

    total_trades = len(trades)
    wins = [t for t in trades if t['pnl_pct'] > 0]
    losses = [t for t in trades if t['pnl_pct'] <= 0]
    win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0.0

    gross_profit = sum(t['pnl_amount'] for t in wins)
    gross_loss = abs(sum(t['pnl_amount'] for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    # Approximate CAGR
    years = max(1.0, n / 252.0)
    cagr_pct = (((final_equity / initial_capital) ** (1.0 / years)) - 1.0) * 100 if final_equity > 0 else 0.0
    calmar_ratio = (cagr_pct / max_drawdown) if max_drawdown > 0 else 0.0

    return {
        "status": "success",
        "ticker": ticker_name,
        "period": period,
        "initial_capital": initial_capital,
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return_pct, 2),
        "cagr_pct": round(cagr_pct, 2),
        "sharpe_ratio": round(sharpe_ratio, 2),
        "calmar_ratio": round(calmar_ratio, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "friction_modeled": {
            "slippage_bps": slippage_bps * 10000,
            "fee_bps": fee_rate * 10000,
            "stop_loss_rule": f"{stop_loss_pct:.1%} Strict Execution"
        },
        "trades": trades,
        "equity_curve": equity_curve
    }
