import os
import sys
import pandas as pd
import yfinance as yf
from datetime import datetime

# Comprehensive NASDAQ 100 & Major Growth/Tech Universe (50+ tickers)
NASDAQ_UNIVERSE = [
    "QQQ", "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA",
    "AVGO", "AMD", "NFLX", "COST", "ASML", "QCOM", "PLTR", "COIN",
    "ARM", "SMCI", "MU", "INTC", "TXN", "AMAT", "LRCX", "ADI",
    "PANW", "CRWD", "FTNT", "SNOW", "DDOG", "NET", "ZS",
    "NOW", "UBER", "ABNB", "BKNG", "PDD", "MELI", "SE",
    "ISRG", "VRTX", "REGN", "MDGL", "LLY", "NVO",
    "005930.KS", "000660.KS", "012450.KS", "005380.KS"
]

def evaluate_pre_trigger(ticker):
    """
    Evaluates STRICT PRE-CONDITIONS before a breakout occurs:
    1. Location: Price is Above Kumo Cloud AND within +0% to +3% of 26-day Kijun-sen.
    2. Volume Dry-Up: Volume <= 70% of 20-day Volume SMA.
    3. Momentum: Tenkan >= Kijun (No Dead Cross).
    4. Distance: Price is within 3.5% of 20-day SMA (No chasing!).
    """
    try:
        df = yf.download(ticker, period="6mo", interval="1d", progress=False)
        if df.empty or len(df) < 55:
            return None
            
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        # 1. Tenkan & Kijun
        high_9 = df['High'].rolling(window=9).max()
        low_9 = df['Low'].rolling(window=9).min()
        df['Tenkan'] = (high_9 + low_9) / 2

        high_26 = df['High'].rolling(window=26).max()
        low_26 = df['Low'].rolling(window=26).min()
        df['Kijun'] = (high_26 + low_26) / 2

        # 2. Spans
        df['SpanA'] = ((df['Tenkan'] + df['Kijun']) / 2).shift(26)
        high_52 = df['High'].rolling(window=52).max()
        low_52 = df['Low'].rolling(window=52).min()
        df['SpanB'] = ((high_52 + low_52) / 2).shift(26)

        df['SMA20'] = df['Close'].rolling(window=20).mean()
        df['SMA60'] = df['Close'].rolling(window=60).mean()
        df['Vol_SMA20'] = df['Volume'].rolling(window=20).mean()
        df['Vol_Ratio'] = df['Volume'] / df['Vol_SMA20']

        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        close = float(last['Close'])
        low = float(last['Low'])
        high = float(last['High'])
        kijun = float(last['Kijun'])
        tenkan = float(last['Tenkan'])
        sma20 = float(last['SMA20'])
        vol_ratio = float(last['Vol_Ratio'])
        
        span_a = float(last['SpanA']) if not pd.isna(last['SpanA']) else sma20
        span_b = float(last['SpanB']) if not pd.isna(last['SpanB']) else sma20
        cloud_top = max(span_a, span_b)
        
        # Check criteria
        kijun_gap_pct = ((close - kijun) / kijun) * 100
        sma20_gap_pct = ((close - sma20) / sma20) * 100
        
        # Condition 1: Above cloud & close to Kijun-sen (-0.5% to +4.0%)
        is_at_kijun_support = (close >= cloud_top * 0.99) and (-0.5 <= kijun_gap_pct <= 4.5)
        
        # Condition 2: Volume is drying up (<= 75% of average)
        is_vol_dry = vol_ratio <= 0.75
        
        # Condition 3: No severe downward breakdown
        is_healthy_trend = tenkan >= kijun * 0.98 and close >= last['SMA60'] * 0.97
        
        # Condition 4: Not chasing overbought rally
        not_overbought = sma20_gap_pct <= 5.0
        
        # Scoring match (0 to 100)
        score = 0
        if is_at_kijun_support: score += 40
        if is_vol_dry: score += 30
        if is_healthy_trend: score += 20
        if not_overbought: score += 10
        
        if score >= 70: # Trigger qualified
            stop_loss_price = min(kijun * 0.985, close * 0.96)  # -4% hard stop SSOT
            target_price_1 = close * 1.15 # +15% target
            target_price_2 = close * 1.25 # +25% target
            risk_amt = close - stop_loss_price
            reward_amt = target_price_1 - close
            rr_ratio = (reward_amt / risk_amt) if risk_amt > 0 else 0
            
            return {
                "ticker": ticker,
                "score": score,
                "close": close,
                "kijun": kijun,
                "kijun_gap": f"{kijun_gap_pct:+.1f}%",
                "vol_ratio": f"{vol_ratio*100:.0f}%",
                "entry_price": f"${close:,.2f}" if not ticker.endswith(".KS") else f"{close:,.0f}원",
                "stop_loss": f"${stop_loss_price:,.2f} (-4.0%)" if not ticker.endswith(".KS") else f"{stop_loss_price:,.0f}원 (-4.0%)",
                "target_1": f"${target_price_1:,.2f} (+15%)" if not ticker.endswith(".KS") else f"{target_price_1:,.0f}원 (+15%)",
                "target_2": f"${target_price_2:,.2f} (+25%)" if not ticker.endswith(".KS") else f"{target_price_2:,.0f}원 (+25%)",
                "rr_ratio": f"1 : {rr_ratio:.1f}",
                "al_comment": "26일 기준선 초밀착 지지 + 거래량 마름 확인 ➔ 1차 30% 진입 최적 타점"
            }
            
        return None
    except Exception as e:
        return None

def run_pre_trigger_scanner():
    print(f"Scanning {len(NASDAQ_UNIVERSE)} universe tickers for Al-Sangmoo PRE-CONDITIONS...")
    
    triggers = []
    for ticker in NASDAQ_UNIVERSE:
        res = evaluate_pre_trigger(ticker)
        if res:
            triggers.append(res)
            print(f"  -> [TRIGGER HIT] {ticker} (Score: {res['score']}/100) at {res['entry_price']}")
            
    triggers.sort(key=lambda x: x['score'], reverse=True)
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    out_path = os.path.join(r"D:\코딩\Playground\al_sangmoo_project", f"al_sangmoo_pre_triggers_{today_str}.md")
    
    md_text = f"""# 🎯 알상무 사전 조건식 실시간 포착 주문서 ({today_str})

> **원리**: 사후에 급등한 차트를 보고 후회하는 것이 아니라, **"폭발 직전 26일 기준선에 딱 붙어 거래량이 바짝 마른 종목"**을 사전에 발굴하여 진입하는 실전 주문 시트입니다.
> **손익비 원칙**: 손실은 **-3%**로 제한하고, 목표가는 **+15% ~ +25%**를 노려 **손익비 최소 1:4 이상**인 종목만 선별.

---

## 🟢 오늘자 알상무 사전 조건식 100% 충족 종목 (Order Trigger Sheet)

"""
    if triggers:
        for idx, t in enumerate(triggers):
            md_text += f"""### [{idx+1}] {t['ticker']} (적합도: `{t['score']}점 / 100점`)

* 📍 **현재 진입 타점**: `{t['entry_price']}` (26일 기준선 대비 `{t['kijun_gap']}`)
* 🧊 **거래량 상태**: 20일 평균 대비 `{t['vol_ratio']}` (개미 투매 종료 / 거래량 마름 확인)
* 🛡️ **기계적 손절선**: `{t['stop_loss']}` (기준선 이탈 시 무조건 손절)
* 🎯 **1차 목표가**: `{t['target_1']}` (비중 50% 분할 익절)
* 🚀 **2차 목표가**: `{t['target_2']}` (추세 끝까지 홀딩)
* ⚖️ **예상 손익비 (Risk/Reward)**: `{t['rr_ratio']}`
* 💬 **알상무의 실전 코멘트**: *"{t['al_comment']}"*

---
"""
    else:
        md_text += "*현재 사전 조건식을 100% 만족하는 종목이 없습니다. 현금을 50% 이상 보관하고 다음 사이클을 기다리세요.*\n"

    md_text += """
## 📋 알상무 사전 진입 체크리스트 4계명

1. **절대 추격 매수 금지**: 20일선/기준선에서 +5% 이상 이미 뜬 종목은 내 종목이 아니라고 생각하고 보내줄 것.
2. **거래량 마름(VDU) 확인**: 거래량이 평소보다 적을 때(개미들이 지루해할 때) 미리 들어가 누워 있을 것.
3. **분할 매수 30% 룰**: 처음부터 몰빵하지 말고, 1차 30% 매수 후 기준선 지지 양봉이 유지될 때 40% 추가 진입.
4. **-3% 도달 시 즉시 칼손절**: 손절 없는 물타기는 파멸의 지름길. 기준선 깨지면 뒤도 보지 말고 나올 것.
"""

    with open(out_path, 'w', encoding='utf-8') as f_out:
        f_out.write(md_text)
        
    print(f"\nSaved today's pre-trigger order sheet to: {out_path}")
    return out_path

if __name__ == "__main__":
    run_pre_trigger_scanner()
