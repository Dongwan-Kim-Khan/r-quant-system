import os
import sys
import pandas as pd
import yfinance as yf
from datetime import datetime

# Primary NASDAQ Watchlist + Key KOSPI
WATCHLIST_NASDAQ = [
    "QQQ", "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA",
    "AVGO", "AMD", "NFLX", "COST", "ASML", "QCOM", "PLTR", "COIN"
]
WATCHLIST_KOSPI = [
    "005930.KS", # 삼성전자
    "000660.KS", # SK하이닉스
    "012450.KS", # 한화에어로스페이스 (방산)
    "005380.KS", # 현대차
    "105560.KS"  # KB금융
]

def scan_tickers():
    all_tickers = WATCHLIST_NASDAQ + WATCHLIST_KOSPI
    print(f"Starting Al-Sangmoo Daily Quant Scan on {len(all_tickers)} key tickers...")
    
    bull_picks = []
    bear_shorts = []
    neutral_list = []
    
    for ticker in all_tickers:
        try:
            df = yf.download(ticker, period="6mo", interval="1d", progress=False)
            if df.empty or len(df) < 55:
                continue
                
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
            # 1. Tenkan & Kijun
            high_9 = df['High'].rolling(window=9).max()
            low_9 = df['Low'].rolling(window=9).min()
            df['Tenkan'] = (high_9 + low_9) / 2

            high_26 = df['High'].rolling(window=26).max()
            low_26 = df['Low'].rolling(window=26).min()
            df['Kijun'] = (high_26 + low_26) / 2

            # 2. Spans (Shifted for today's evaluation)
            df['SpanA'] = ((df['Tenkan'] + df['Kijun']) / 2).shift(26)
            high_52 = df['High'].rolling(window=52).max()
            low_52 = df['Low'].rolling(window=52).min()
            df['SpanB'] = ((high_52 + low_52) / 2).shift(26)

            df['SMA20'] = df['Close'].rolling(window=20).mean()
            df['Vol_SMA20'] = df['Volume'].rolling(window=20).mean()
            df['Vol_Ratio'] = df['Volume'] / df['Vol_SMA20']

            last = df.iloc[-1]
            prev = df.iloc[-2]
            
            close = float(last['Close'])
            kijun = float(last['Kijun'])
            tenkan = float(last['Tenkan'])
            sma20 = float(last['SMA20'])
            vol_ratio = float(last['Vol_Ratio'])
            
            span_a = float(last['SpanA']) if not pd.isna(last['SpanA']) else sma20
            span_b = float(last['SpanB']) if not pd.isna(last['SpanB']) else sma20
            cloud_top = max(span_a, span_b)
            cloud_bottom = min(span_a, span_b)
            
            is_dry = vol_ratio <= 0.80
            
            # Distance from Kijun
            kijun_dist = ((close - kijun) / kijun) * 100
            
            item = {
                "ticker": ticker,
                "close": close,
                "kijun": kijun,
                "tenkan": tenkan,
                "kijun_dist": f"{kijun_dist:+.1f}%",
                "vol_ratio": f"{vol_ratio*100:.0f}%",
                "cloud_status": "구름대 위" if close >= cloud_top else ("구름대 아래" if close < cloud_bottom else "구름대 내부")
            }
            
            # Classification
            if close >= cloud_top and close >= kijun and is_dry and kijun_dist < 6.0:
                item["comment"] = "구름대 상단 안착 + 기준선 지지 + 거래량 급감 (최적 눌림목 매수 타점)"
                bull_picks.append(item)
            elif close < kijun or close < cloud_bottom:
                item["comment"] = "26일 기준선 이탈 (생명선 붕괴) -> 숏 헤지 및 관망"
                bear_shorts.append(item)
            else:
                item["comment"] = "박스권 횡보 중 (추세 돌파 대기)"
                neutral_list.append(item)
                
        except Exception as e:
            print(f"Error scanning {ticker}: {e}")
            
    print(f"Scan complete! Bull: {len(bull_picks)}, Bear/Short: {len(bear_shorts)}, Neutral: {len(neutral_list)}")
    
    # Save Report
    today_str = datetime.now().strftime("%Y-%m-%d")
    report_path = os.path.join(r"D:\코딩\Playground\al_sangmoo_project", f"daily_scan_{today_str}.md")
    
    md = f"""# 🏛️ 알상무 퀀트 일일 나스닥/코스피 스캔 리포트 ({today_str})

> **스캔 기준**: 나스닥 핵심 빅테크 16종목 + 코스피 대표주 5종목 (총 21종목)  
> **판정 모델**: 알상무 일목균형표(기준선 26, 전환선 9, 구름대) + 20일 거래량 마름(VDU) 필터

---

## 🟢 1. 알상무 Pick (상승 유력 / 빈집 선점 / 눌림목 매수 30%)
*구름대 위에 안착해 있으면서 기준선 지지를 받고 거래량이 마른 종목군입니다.*

| 종목코드 | 현재가 | 26일 기준선 | 기준선 이격도 | 당일 거래량 비율 | 상태 & 알상무 코멘트 |
| :--- | :---: | :---: | :---: | :---: | :--- |
"""
    if bull_picks:
        for b in bull_picks:
            md += f"| **{b['ticker']}** | `${b['close']:,.2f}` | `${b['kijun']:,.2f}` | `{b['kijun_dist']}` | `{b['vol_ratio']}` | {b['comment']} |\n"
    else:
        md += "| - | - | - | - | - | *현재 조건에 완벽히 부합하는 눌림목 종목 없음 (현금 대기)* |\n"

    md += """
---

## 🔴 2. 알상무 숏/손절 경보 (하락 유력 / 기준선 이탈)
*26일 기준선(생명선)을 깨고 내려갔거나 구름대 하단으로 추락하여 숏 헤지가 필요한 종목군입니다.*

| 종목코드 | 현재가 | 26일 기준선 | 기준선 이격도 | 상태 & 알상무 코멘트 |
| :--- | :---: | :---: | :---: | :--- |
"""
    if bear_shorts:
        for b in bear_shorts:
            md += f"| **{b['ticker']}** | `${b['close']:,.2f}` | `${b['kijun']:,.2f}` | `{b['kijun_dist']}` | {b['comment']} |\n"
    else:
        md += "| - | - | - | - | *기준선 이탈 종목 없음* |\n"

    md += """
---

## 🟡 3. 중립 / 관망 종목 (애매한 것)
*방향성이 확정되지 않고 박스권에서 에너지를 응축 중인 종목군입니다.*

| 종목코드 | 현재가 | 26일 기준선 | 구름대 위치 | 상태 & 알상무 코멘트 |
| :--- | :---: | :---: | :---: | :--- |
"""
    for n in neutral_list:
        md += f"| **{n['ticker']}** | `${n['close']:,.2f}` | `${n['kijun']:,.2f}` | `{n['cloud_status']}` | {n['comment']} |\n"

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(md)
        
    print(f"Saved daily scan report to: {report_path}")
    return report_path

if __name__ == "__main__":
    scan_tickers()
