"""
Pure Quantitative Ichimoku, Kijun-sen, Volume Dry-Up (VDU), and Series Payload Engine.
Single Source of Truth (SSOT) for all mathematical indicator rolling calculations.
"""
from typing import Dict, Any, List, Tuple, Optional
import pandas as pd
import numpy as np


def calculate_ichimoku_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes all standard Ichimoku parameters, rolling moving averages, and volume ratios.
    SSOT Indicator Math Engine:
    - 9-day Tenkan-sen, 26-day Kijun-sen, 52-day Senkou Span B base
    - RawSpanA ((Tenkan+Kijun)/2), RawSpanB, SpanA (shift +26), SpanB (shift +26)
    - Chikou Span (shift -26)
    - SMA20, SMA50, SMA60, SMA200
    - Vol_SMA20, Vol_Ratio (safe division against 0/NaN)
    """
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        if 'Close' in df.columns.get_level_values(0):
            df.columns = df.columns.get_level_values(0)
        elif 'Close' in df.columns.get_level_values(1):
            df.columns = df.columns.get_level_values(1)
    
    # 1. 9-period Tenkan-sen (Conversion Line)
    high_9 = df['High'].rolling(window=9, min_periods=5).max()
    low_9 = df['Low'].rolling(window=9, min_periods=5).min()
    df['Tenkan'] = (high_9 + low_9) / 2

    # 2. 26-period Kijun-sen (Base Line - Vital Support)
    high_26 = df['High'].rolling(window=26, min_periods=10).max()
    low_26 = df['Low'].rolling(window=26, min_periods=10).min()
    df['Kijun'] = (high_26 + low_26) / 2

    # 3. 52-period High/Low for Span B
    high_52 = df['High'].rolling(window=52, min_periods=20).max()
    low_52 = df['Low'].rolling(window=52, min_periods=20).min()

    # 4. Raw Senkou Spans before 26-period forward shift
    df['RawSpanA'] = (df['Tenkan'] + df['Kijun']) / 2
    df['RawSpanB'] = (high_52 + low_52) / 2

    # 5. Historical shifted spans aligned with today's candle
    df['SpanA'] = df['RawSpanA'].shift(26)
    df['SpanB'] = df['RawSpanB'].shift(26)

    # 6. Chikou Span (Lagging Span shifted backward 26 bars)
    df['Chikou'] = df['Close'].shift(-26)

    # 7. Standard Moving Averages
    df['SMA20'] = df['Close'].rolling(window=20, min_periods=10).mean()
    df['SMA50'] = df['Close'].rolling(window=50, min_periods=15).mean()
    df['SMA60'] = df['Close'].rolling(window=60, min_periods=20).mean()
    df['SMA200'] = df['Close'].rolling(window=200, min_periods=30).mean()

    # 8. Volume Dry-Up (VDU) Indicators
    df['Vol_SMA20'] = df['Volume'].rolling(window=20, min_periods=5).mean()
    vol_sma = df['Vol_SMA20'].fillna(0)
    df['Vol_Ratio'] = np.where(vol_sma > 0, df['Volume'] / vol_sma, 1.0)

    # 9. Goldman Sachs / Al-Sangmoo v2 Upgraded Quantitative Features
    # 20D Highest High (Momentum Breakout Engine)
    df['High_20D'] = df['High'].rolling(window=20, min_periods=10).max().shift(1)

    # ATR(14) Volatility Channel (Uncapped Trailing Stop Engine)
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - df['Close'].shift(1)).abs(),
        (df['Low'] - df['Close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    df['ATR14'] = tr.rolling(window=14, min_periods=5).mean()

    # 3-Month Cross-Sectional Relative Strength (RS_3M)
    df['RS_3M'] = df['Close'].pct_change(periods=63).fillna(0) * 100.0

    # On-Balance Volume (OBV) & 20D MA
    direction = np.where(df['Close'].diff() > 0, 1, np.where(df['Close'].diff() < 0, -1, 0))
    df['OBV'] = (direction * df['Volume']).fillna(0).cumsum()
    df['OBV_MA20'] = df['OBV'].rolling(window=20, min_periods=5).mean()

    return df


def project_future_cloud(df_clean: pd.DataFrame, periods: int = 26, is_weekly: bool = False) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[float], List[float]]:
    """
    Generates forward trading periods (default +26) for the future Ichimoku cloud visualization.
    Returns: (future_span_a_pts, future_span_b_pts, future_span_a_vals, future_span_b_vals)
    """
    if df_clean is None or len(df_clean) == 0:
        return [], [], [], []
        
    last_date = df_clean.index[-1]
    if is_weekly:
        future_dates = pd.date_range(start=last_date + pd.Timedelta(days=7), periods=periods, freq='W-FRI')
    else:
        future_dates = pd.bdate_range(start=last_date + pd.Timedelta(days=1), periods=periods)
    
    future_span_a_pts: List[Dict[str, Any]] = []
    future_span_b_pts: List[Dict[str, Any]] = []
    future_a_vals: List[float] = []
    future_b_vals: List[float] = []
    
    n_clean = len(df_clean)
    for k in range(len(future_dates)):
        f_time_str = future_dates[k].strftime("%Y-%m-%d")
        lookback_idx = n_clean - 26 + k
        if 0 <= lookback_idx < n_clean:
            val_a = float(df_clean['RawSpanA'].iloc[lookback_idx]) if 'RawSpanA' in df_clean.columns else np.nan
            val_b = float(df_clean['RawSpanB'].iloc[lookback_idx]) if 'RawSpanB' in df_clean.columns else np.nan
            if not pd.isna(val_a):
                future_span_a_pts.append({"time": f_time_str, "value": round(val_a, 2)})
                future_a_vals.append(val_a)
            if not pd.isna(val_b):
                future_span_b_pts.append({"time": f_time_str, "value": round(val_b, 2)})
                future_b_vals.append(val_b)
                
    return future_span_a_pts, future_span_b_pts, future_a_vals, future_b_vals


def detect_cloud_trampoline_bounce(df_clean: pd.DataFrame, max_lookback: int = 14) -> Tuple[bool, int, float, float]:
    """
    Scans historical bars within max_lookback to identify a valid Cloud Trampoline Launch:
    - Candle Low touched within -3.5% to +6.0% of Span A/B Cloud Top
    - Candle Close held >= -1.5% of Cloud Top
    Returns: (detected: bool, days_ago: int, touch_gap_pct: float, close_gap_pct: float)
    """
    if df_clean is None or len(df_clean) < 2:
        return False, 0, 0.0, 0.0
        
    n_bars = len(df_clean)
    for b_offset in range(1, min(max_lookback + 1, n_bars)):
        hist_bar = df_clean.iloc[-b_offset]
        h_close = float(hist_bar['Close'])
        h_low = float(hist_bar['Low'])
        h_sp_a = float(hist_bar['SpanA']) if ('SpanA' in hist_bar and not pd.isna(hist_bar['SpanA'])) else h_close
        h_sp_b = float(hist_bar['SpanB']) if ('SpanB' in hist_bar and not pd.isna(hist_bar['SpanB'])) else h_close
        h_cloud_top = max(h_sp_a, h_sp_b)
        if h_cloud_top > 0:
            h_touch_gap = (h_low - h_cloud_top) / h_cloud_top
            h_close_gap = (h_close - h_cloud_top) / h_cloud_top
            if (-0.035 <= h_touch_gap <= 0.060) and (h_close_gap >= -0.015):
                days_ago = b_offset - 1
                return True, days_ago, round(h_touch_gap * 100, 2), round(h_close_gap * 100, 2)
                
    return False, 0, 0.0, 0.0


def compute_institutional_flow_indicators(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Computes institutional flow signatures:
    1. On-Balance Volume (OBV) and Stealth Accumulation Divergence
    2. 14-day Up/Down Volume Flow Ratio (flow_ratio)
    3. Institutional Flow Score (0-100pt)
    """
    if df is None or len(df) < 15:
        return {
            "obv_status": "NEUTRAL",
            "obv_label": "[NEUTRAL]",
            "flow_ratio": 1.0,
            "flow_label": "1.0x",
            "flow_score": 50,
            "is_stealth_accum": False
        }
        
    df = df.copy()
    close_diff = df['Close'].diff()
    direction = np.where(close_diff > 0, 1, np.where(close_diff < 0, -1, 0))
    df['OBV'] = (direction * df['Volume']).fillna(0).cumsum()
    
    # 14-day window metrics
    w_df = df.iloc[-14:]
    up_vol = w_df[w_df['Close'] >= w_df['Open']]['Volume'].sum()
    down_vol = w_df[w_df['Close'] < w_df['Open']]['Volume'].sum()
    
    if down_vol > 0:
        flow_ratio = round(float(up_vol / down_vol), 2)
    else:
        flow_ratio = 2.5
        
    price_change_14d = float((df['Close'].iloc[-1] - df['Close'].iloc[-14]) / df['Close'].iloc[-14] * 100)
    obv_change_14d = float(df['OBV'].iloc[-1] - df['OBV'].iloc[-14])
    
    # Check for Stealth Accumulation Divergence: Price consolidating (-3.0% ~ +2.5%) while OBV rising & flow >= 1.2
    is_stealth_accum = bool(price_change_14d <= 2.5 and obv_change_14d > 0 and flow_ratio >= 1.2)
    
    if is_stealth_accum:
        obv_status = "STEALTH_ACCUM"
        obv_label = "[STEALTH ACCUM]"
    elif obv_change_14d > 0:
        obv_status = "BULL_FLOW"
        obv_label = "[BULL FLOW]"
    else:
        obv_status = "NEUTRAL"
        obv_label = "[NEUTRAL]"
        
    flow_score = 0
    if is_stealth_accum:
        flow_score += 50
    elif obv_status == "BULL_FLOW":
        flow_score += 30
        
    if flow_ratio >= 1.8:
        flow_score += 50
    elif flow_ratio >= 1.3:
        flow_score += 35
    elif flow_ratio >= 1.0:
        flow_score += 20
        
    return {
        "obv_status": obv_status,
        "obv_label": obv_label,
        "flow_ratio": float(flow_ratio),
        "flow_label": f"{flow_ratio:.1f}x",
        "flow_score": int(min(100, flow_score)),
        "is_stealth_accum": bool(is_stealth_accum)
    }


def build_ichimoku_series_payload(df_in: pd.DataFrame, is_weekly: bool = False, max_bars: int = 500) -> Dict[str, Any]:
    """
    Transforms computed DataFrame into chart-ready JSON series dictionary with forward cloud projection.
    """
    if df_in is None or df_in.empty:
        return {}
        
    df_clean = df_in.dropna(subset=['Close', 'High', 'Low', 'Kijun', 'Tenkan']).tail(max_bars)
    if df_clean.empty:
        return {}
        
    candles = []
    tenkan_pts = []
    kijun_pts = []
    span_a_pts = []
    span_b_pts = []
    sma20_pts = []
    sma50_pts = []
    sma60_pts = []
    sma200_pts = []
    vol_pts = []
    
    for idx, row in df_clean.iterrows():
        time_str = idx.strftime("%Y-%m-%d") if hasattr(idx, 'strftime') else str(idx)
        candles.append({
            "time": time_str,
            "open": round(float(row['Open']), 2),
            "high": round(float(row['High']), 2),
            "low": round(float(row['Low']), 2),
            "close": round(float(row['Close']), 2)
        })
        if 'Tenkan' in row and not pd.isna(row['Tenkan']):
            tenkan_pts.append({"time": time_str, "value": round(float(row['Tenkan']), 2)})
        if 'Kijun' in row and not pd.isna(row['Kijun']):
            kijun_pts.append({"time": time_str, "value": round(float(row['Kijun']), 2)})
        if 'SpanA' in row and not pd.isna(row['SpanA']):
            span_a_pts.append({"time": time_str, "value": round(float(row['SpanA']), 2)})
        if 'SpanB' in row and not pd.isna(row['SpanB']):
            span_b_pts.append({"time": time_str, "value": round(float(row['SpanB']), 2)})
        if 'SMA20' in row and not pd.isna(row['SMA20']):
            sma20_pts.append({"time": time_str, "value": round(float(row['SMA20']), 2)})
        if 'SMA50' in row and not pd.isna(row['SMA50']):
            sma50_pts.append({"time": time_str, "value": round(float(row['SMA50']), 2)})
        if 'SMA60' in row and not pd.isna(row['SMA60']):
            sma60_pts.append({"time": time_str, "value": round(float(row['SMA60']), 2)})
        if 'SMA200' in row and not pd.isna(row['SMA200']):
            sma200_pts.append({"time": time_str, "value": round(float(row['SMA200']), 2)})
        if 'Volume' in row and not pd.isna(row['Volume']):
            vol_pts.append({
                "time": time_str,
                "value": float(row['Volume']),
                "color": "#059669" if row['Close'] >= row['Open'] else "#dc2626"
            })
            
    # Future 26-bar forward cloud projection
    fa_pts, fb_pts, future_span_a_vals, future_span_b_vals = project_future_cloud(df_clean, periods=26, is_weekly=is_weekly)
    span_a_pts.extend(fa_pts)
    span_b_pts.extend(fb_pts)
    
    return {
        "candles": candles,
        "kijun_line": kijun_pts,
        "tenkan_line": tenkan_pts,
        "span_a_line": span_a_pts,
        "span_b_line": span_b_pts,
        "sma20": sma20_pts,
        "sma50": sma50_pts,
        "sma60": sma60_pts,
        "sma200": sma200_pts,
        "volume": vol_pts,
        "future_span_a": future_span_a_vals,
        "future_span_b": future_span_b_vals
    }


# Backwards compatibility export
def evaluate_quant_score(close: float, kijun: float, tenkan: float, span_a: float, span_b: float, vol_ratio: float, trampoline_detected: bool = False, is_weekly_bull: bool = True) -> Dict[str, Any]:
    """
    Adapter delegating to canonical scoring engine in al_sangmoo.domain.quant.scoring.
    """
    from al_sangmoo.domain.quant.scoring import evaluate_quant_score as _eval_quant_score
    return _eval_quant_score(
        close=close,
        kijun=kijun,
        tenkan=tenkan,
        span_a=span_a,
        span_b=span_b,
        vol_ratio=vol_ratio,
        trampoline_detected=trampoline_detected,
        is_weekly_bull=is_weekly_bull
    )
