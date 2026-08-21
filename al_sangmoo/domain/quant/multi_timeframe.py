"""
Multi-Timeframe Consensus Matrix & Pre-Trigger Scanner (Weekly + Daily + Hourly).
"""
import pandas as pd
import yfinance as yf
from typing import Dict, Any, Union
from al_sangmoo.domain.quant.ichimoku import calculate_ichimoku_indicators

def calculate_mtf_consensus(ticker_or_data: Union[str, Dict[str, pd.DataFrame]]) -> Dict[str, Any]:
    """
    Computes 3-tier timeframe consensus score (0 ~ 100 pt):
    - Weekly Trend (30%): Primary Macro Bull Regime (Price > Weekly Cloud)
    - Daily Sweet-Spot (50%): 26D Kijun Support (30%) + Volume Dry-Up (20%)
    - Hourly Momentum (20%): Intraday Tenkan >= Kijun Alignment (20%)
    """
    if isinstance(ticker_or_data, str):
        ticker = ticker_or_data.strip().upper()
        df_daily = yf.download(ticker, period="1y", interval="1d", progress=False)
        df_weekly = yf.download(ticker, period="2y", interval="1wk", progress=False)
        df_hourly = yf.download(ticker, period="1mo", interval="1h", progress=False)
        
        for d in [df_daily, df_weekly, df_hourly]:
            if isinstance(d.columns, pd.MultiIndex):
                d.columns = d.columns.get_level_values(0)
    else:
        ticker = "CUSTOM"
        df_daily = ticker_or_data.get("daily", pd.DataFrame())
        df_weekly = ticker_or_data.get("weekly", pd.DataFrame())
        df_hourly = ticker_or_data.get("hourly", pd.DataFrame())

    if df_daily.empty or len(df_daily) < 30:
        return {"status": "error", "message": "Insufficient daily data"}

    df_d = calculate_ichimoku_indicators(df_daily)
    
    # 1. Daily Evaluation (50 pts max)
    cur_close = float(df_d['Close'].iloc[-1])
    d_kijun = float(df_d['Kijun'].iloc[-1])
    d_tenkan = float(df_d['Tenkan'].iloc[-1])
    d_span_a = float(df_d['SpanA'].iloc[-1])
    d_span_b = float(df_d['SpanB'].iloc[-1])
    d_cloud_top = max(d_span_a, d_span_b) if not pd.isna(d_span_a) else cur_close
    d_vol_ratio = float(df_d['Vol_Ratio'].iloc[-1]) if not pd.isna(df_d['Vol_Ratio'].iloc[-1]) else 1.0
    d_kijun_gap = ((cur_close - d_kijun) / d_kijun) * 100 if d_kijun > 0 else 0.0

    daily_kijun_pts = 30.0 if (-0.5 <= d_kijun_gap <= 4.0) else (15.0 if (-2.0 <= d_kijun_gap <= 6.0) else 0.0)
    daily_vdu_pts = 20.0 if d_vol_ratio <= 0.75 else (10.0 if d_vol_ratio <= 1.0 else 0.0)
    daily_score = daily_kijun_pts + daily_vdu_pts

    # 2. Weekly Trend Evaluation (30 pts max)
    weekly_pts = 0.0
    weekly_regime = "NEUTRAL"
    if not df_weekly.empty and len(df_weekly) >= 26:
        df_w = calculate_ichimoku_indicators(df_weekly)
        w_close = float(df_w['Close'].iloc[-1])
        w_span_a = float(df_w['SpanA'].iloc[-1]) if not pd.isna(df_w['SpanA'].iloc[-1]) else w_close
        w_span_b = float(df_w['SpanB'].iloc[-1]) if not pd.isna(df_w['SpanB'].iloc[-1]) else w_close
        w_cloud_top = max(w_span_a, w_span_b)
        if w_close >= w_cloud_top:
            weekly_pts = 30.0
            weekly_regime = "BULLISH_TREND"
        elif w_close >= min(w_span_a, w_span_b):
            weekly_pts = 15.0
            weekly_regime = "CONSOLIDATING"
        else:
            weekly_pts = 0.0
            weekly_regime = "BEARISH_TREND"
    else:
        # Fallback if weekly not available
        weekly_pts = 20.0 if cur_close >= d_cloud_top else 0.0

    # 3. Hourly Momentum Evaluation (20 pts max)
    hourly_pts = 0.0
    hourly_momentum = "NEUTRAL"
    if not df_hourly.empty and len(df_hourly) >= 10:
        df_h = calculate_ichimoku_indicators(df_hourly)
        h_tenkan = float(df_h['Tenkan'].iloc[-1])
        h_kijun = float(df_h['Kijun'].iloc[-1])
        if h_tenkan >= h_kijun:
            hourly_pts = 20.0
            hourly_momentum = "GOLDEN_MOMENTUM"
        else:
            hourly_pts = 5.0
            hourly_momentum = "DEAD_CROSS"
    else:
        hourly_pts = 10.0 if d_tenkan >= d_kijun else 0.0

    total_consensus_score = round(daily_score + weekly_pts + hourly_pts, 1)
    is_pre_trigger = total_consensus_score >= 80.0

    return {
        "status": "success",
        "ticker": ticker,
        "consensus_score": total_consensus_score,
        "is_pre_trigger_ready": is_pre_trigger,
        "breakdown": {
            "weekly": {"pts": weekly_pts, "max": 30, "regime": weekly_regime},
            "daily": {"pts": daily_score, "max": 50, "kijun_gap_pct": round(d_kijun_gap, 2), "vol_ratio_pct": round(d_vol_ratio * 100, 1)},
            "hourly": {"pts": hourly_pts, "max": 20, "momentum": hourly_momentum}
        },
        "verdict": "🔥 PRE-TRIGGER ACCUMULATION (Breakout Imminent)" if is_pre_trigger else "CONSOLIDATION_WATCH"
    }
