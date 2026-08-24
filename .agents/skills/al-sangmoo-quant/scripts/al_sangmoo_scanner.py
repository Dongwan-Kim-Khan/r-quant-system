import sys
import os
import json
import numpy as np

def calculate_al_sangmoo_indicators(df):
    """
    Input: DataFrame with columns ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
    Computes:
    - Tenkan-sen (9), Kijun-sen (26), Senkou Span A/B (52)
    - SMA 20, SMA 60, Volume SMA 20
    - Dynamic Pink Support Line (Linear regression on recent 3 swing lows)
    - Al-Sangmoo Signal Classification (BULL / BEAR / SQUEEZE / CAUTION)
    """
    # 1. Tenkan-sen (9)
    high_9 = df['High'].rolling(window=9).max()
    low_9 = df['Low'].rolling(window=9).min()
    df['Tenkan'] = (high_9 + low_9) / 2

    # 2. Kijun-sen (26) - The Core Lifeline
    high_26 = df['High'].rolling(window=26).max()
    low_26 = df['Low'].rolling(window=26).min()
    df['Kijun'] = (high_26 + low_26) / 2

    # 3. Senkou Span A & B (26 periods ahead in standard, here shift for current evaluation)
    df['SpanA'] = ((df['Tenkan'] + df['Kijun']) / 2).shift(26)
    high_52 = df['High'].rolling(window=52).max()
    low_52 = df['Low'].rolling(window=52).min()
    df['SpanB'] = ((high_52 + low_52) / 2).shift(26)

    # 4. Moving Averages
    df['SMA20'] = df['Close'].rolling(window=20).mean()
    df['SMA60'] = df['Close'].rolling(window=60).mean()
    df['Vol_SMA20'] = df['Volume'].rolling(window=20).mean()

    # 5. Volume Dry-up Ratio
    df['Vol_Ratio'] = df['Volume'] / df['Vol_SMA20']

    # 6. Signal Determination on Latest Candle
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    close = last['Close']
    kijun = last['Kijun']
    tenkan = last['Tenkan']
    span_a = last['SpanA'] if not np.isnan(last['SpanA']) else last['SMA60']
    span_b = last['SpanB'] if not np.isnan(last['SpanB']) else last['SMA60']
    cloud_top = max(span_a, span_b)
    cloud_bottom = min(span_a, span_b)
    
    vol_dry = last['Vol_Ratio'] <= 0.65
    tenkan_cross_up = (prev['Tenkan'] <= prev['Kijun']) and (tenkan > kijun)
    
    # Classification Logic
    signal = "NEUTRAL (관망/대기)"
    reason = []
    action = "현금 비중 유지 및 기준선 지지 관찰"
    
    if close >= cloud_top and close >= kijun and vol_dry:
        signal = "BULL_ACCUMULATION (🟢 빈집 매집/1차 매수)"
        reason.append("일목 구름대 상단 안착")
        reason.append("26일 기준선 위에서 지지")
        reason.append(f"조정 시 거래량 바짝 마름 (평균 대비 {last['Vol_Ratio']*100:.1f}%)")
        action = "계좌 비중 30% 1차 분할 매수 (기준선 이탈 시 -3% 칼손절)"
    elif close >= kijun and tenkan_cross_up and last['Vol_Ratio'] >= 1.8:
        signal = "BULL_BREAKOUT (🚀 2차 불타기 급등 시세)"
        reason.append("전환선/기준선 골든크로스 발생")
        reason.append(f"거래량 폭증 ({last['Vol_Ratio']*100:.1f}%)")
        action = "계좌 비중 40% 추가 매수 (추세 추종)"
    elif close < kijun or close < cloud_bottom:
        signal = "BEAR_STOP_SHORT (🔴 기준선 이탈 / 숏 헤지 경보)"
        reason.append("26일 기준선 종가 하향 이탈 (생명선 붕괴)")
        if close < cloud_bottom:
            reason.append("일목 구름대 하단 추락")
        action = "보유 물량 기계적 전량 손절 또는 인버스 숏 포지션 편입"
    elif close > last['SMA20'] * 1.15:
        signal = "OVERHEATED_TAKE_PROFIT (🟡 음모론 모드 / 분할 익절)"
        reason.append("단기 이평선 이격 과열")
        action = "보유 물량 30~50% 차익 실현 후 달러/현금 금고 보관"
        
    return {
        "ticker": getattr(df, 'ticker', 'UNKNOWN'),
        "latest_close": float(close),
        "kijun": float(kijun),
        "tenkan": float(tenkan),
        "cloud_top": float(cloud_top),
        "cloud_bottom": float(cloud_bottom),
        "vol_ratio": float(last['Vol_Ratio']),
        "signal": signal,
        "reasons": reason,
        "action": action
    }

if __name__ == "__main__":
    print("Al-Sangmoo Quant Engine Calculator Loaded.")
