"""
Pure Quantitative Ichimoku, Kijun-sen, and Volume Dry-Up (VDU) Computation Engine.
"""
import pandas as pd
import numpy as np

def calculate_ichimoku_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes all standard Ichimoku parameters, rolling moving averages, and volume ratios.
    """
    df = df.copy()
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
    return df

def project_future_cloud(df_clean: pd.DataFrame, periods: int = 26) -> tuple:
    """
    Generates 26 forward trading days for the future Ichimoku cloud.
    """
    last_date = df_clean.index[-1]
    future_dates = pd.bdate_range(start=last_date + pd.Timedelta(days=1), periods=periods)
    
    future_span_a_pts = []
    future_span_b_pts = []
    future_a_vals = []
    future_b_vals = []
    
    for k in range(len(future_dates)):
        f_time_str = future_dates[k].strftime("%Y-%m-%d")
        lookback_idx = len(df_clean) - 26 + k
        if 0 <= lookback_idx < len(df_clean):
            val_a = float(df_clean['RawSpanA'].iloc[lookback_idx])
            val_b = float(df_clean['RawSpanB'].iloc[lookback_idx])
            if not pd.isna(val_a):
                future_span_a_pts.append({"time": f_time_str, "value": round(val_a, 2)})
                future_a_vals.append(val_a)
            if not pd.isna(val_b):
                future_span_b_pts.append({"time": f_time_str, "value": round(val_b, 2)})
                future_b_vals.append(val_b)
                
    return future_span_a_pts, future_span_b_pts, future_a_vals, future_b_vals

def evaluate_quant_score(close: float, kijun: float, tenkan: float, span_a: float, span_b: float, vol_ratio: float) -> dict:
    """
    Pure 17-Year Proprietary Quant Scoring Matrix.
    - Cloud clearance (+35 pt)
    - 26-Day Kijun sweet-spot support (-0.5% ~ +4.0%) (+35 pt)
    - 20-Day Volume Dry-Up (VDU <= 0.75) (+20 pt)
    - 9-Day Tenkan-sen momentum (+10 pt)
    """
    cloud_top = max(span_a, span_b)
    cloud_bottom = min(span_a, span_b)
    kijun_gap = ((close - kijun) / kijun) * 100 if kijun > 0 else 0.0

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

    kijun_status = "status-bull" if -0.5 <= kijun_gap <= 4.0 else ("status-bear" if kijun_gap < -0.5 else "status-neutral")
    kijun_badge = "SUPPORTED" if -0.5 <= kijun_gap <= 4.0 else ("BREAKDOWN" if kijun_gap < -0.5 else "OVERHEATED")
    
    tenkan_status = "status-bull" if tenkan >= kijun else "status-bear"
    tenkan_badge = "GOLDEN CROSS" if tenkan >= kijun else "DEAD CROSS"
    
    cloud_status = "status-bull" if close >= cloud_top else ("status-bear" if close < cloud_bottom else "status-neutral")
    cloud_badge = "ABOVE CLOUD" if close >= cloud_top else ("BELOW CLOUD" if close < cloud_bottom else "INSIDE CLOUD")
    
    vol_status = "status-bull" if vol_ratio <= 0.75 else ("status-neutral" if vol_ratio <= 1.2 else "status-bear")
    vol_badge = "VOLUME DRY" if vol_ratio <= 0.75 else ("NORMAL VOL" if vol_ratio <= 1.2 else "HIGH VOL")

    return {
        "bull_score": bull_score,
        "bear_score": bear_score,
        "quant_type": quant_type,
        "quant_verdict": quant_verdict,
        "quant_score_text": quant_score_text,
        "action_directive": action_directive,
        "intelligence": {
            "verdict": quant_verdict,
            "score": quant_score_text,
            "bull_score": bull_score,
            "bear_score": bear_score,
            "type": quant_type,
            "kijun": {"val": f"${kijun:,.2f} ({kijun_gap:+.1f}%)", "status": kijun_status, "badge": kijun_badge, "desc": f"현재가 ${close:,.2f} / 26일선 ${kijun:,.2f} (이격 {kijun_gap:+.1f}%)"},
            "tenkan": {"val": f"${tenkan:,.2f}", "status": tenkan_status, "badge": tenkan_badge, "desc": f"9일 전환선 ${tenkan:,.2f} {'상단 정배열' if tenkan >= kijun else '하단 역배열'}"},
            "cloud": {"val": f"${cloud_top:,.2f}", "status": cloud_status, "badge": cloud_badge, "desc": f"일목 구름대({round(cloud_bottom,1)}~{round(cloud_top,1)}) {'상단 안착' if close >= cloud_top else ('하단 붕괴' if close < cloud_bottom else '내부 횡보')}"},
            "vol": {"val": f"{round(vol_ratio*100)}% (20D)", "status": vol_status, "badge": vol_badge, "desc": f"20일 평균 거래량 대비 {round(vol_ratio*100)}% ({'매도세 고갈 완벽' if vol_ratio <= 0.75 else '통상 거래량'})"},
            "action": action_directive
        }
    }
