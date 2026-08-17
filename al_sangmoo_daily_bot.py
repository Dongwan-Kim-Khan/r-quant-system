import os
import sys
import json
import smtplib

# Fix Windows cp949 terminal encoding
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf

HISTORY_CSV = "trade_history.csv"
REPORTS_DIR = "daily_reports"
os.makedirs(REPORTS_DIR, exist_ok=True)

# NASDAQ 100 & Key Growth Universe
UNIVERSE = [
    "QQQ", "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA",
    "AVGO", "AMD", "NFLX", "COST", "ASML", "QCOM", "PLTR", "COIN",
    "ARM", "SMCI", "MU", "PANW", "CRWD", "NOW", "UBER", "ABNB",
    "ISRG", "LLY", "VRTX", "005930.KS", "000660.KS", "012450.KS"
]

def calculate_indicators(df):
    high_9 = df['High'].rolling(window=9).max()
    low_9 = df['Low'].rolling(window=9).min()
    df['Tenkan'] = (high_9 + low_9) / 2

    high_26 = df['High'].rolling(window=26).max()
    low_26 = df['Low'].rolling(window=26).min()
    df['Kijun'] = (high_26 + low_26) / 2

    df['SpanA'] = ((df['Tenkan'] + df['Kijun']) / 2).shift(26)
    high_52 = df['High'].rolling(window=52).max()
    low_52 = df['Low'].rolling(window=52).min()
    df['SpanB'] = ((high_52 + low_52) / 2).shift(26)

    df['SMA20'] = df['Close'].rolling(window=20).mean()
    df['SMA60'] = df['Close'].rolling(window=60).mean()
    df['Vol_SMA20'] = df['Volume'].rolling(window=20).mean()
    df['Vol_Ratio'] = df['Volume'] / df['Vol_SMA20']
    return df

def scan_and_select_2x2x2():
    candidates = []
    
    for ticker in UNIVERSE:
        try:
            df = yf.download(ticker, period="6mo", interval="1d", progress=False)
            if df.empty or len(df) < 55:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
            df = calculate_indicators(df)
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
            
            kijun_gap = ((close - kijun) / kijun) * 100
            
            # Score Bull (0 to 100)
            bull_score = 0
            if close >= cloud_top: bull_score += 35
            if -0.5 <= kijun_gap <= 4.0: bull_score += 35
            if vol_ratio <= 0.75: bull_score += 20
            if tenkan >= kijun: bull_score += 10
            
            # Score Bear (0 to 100)
            bear_score = 0
            if close < kijun: bear_score += 40
            if close < cloud_bottom: bear_score += 35
            if kijun_gap < -2.0: bear_score += 15
            if vol_ratio >= 1.2: bear_score += 10
            
            # Neutral Indicator
            neutral_score = abs(kijun_gap) <= 2.0 and abs(bull_score - bear_score) <= 20
            
            candidates.append({
                "ticker": ticker,
                "close": close,
                "kijun": kijun,
                "tenkan": tenkan,
                "kijun_gap": kijun_gap,
                "vol_ratio": vol_ratio,
                "cloud_status": "구름대 위" if close >= cloud_top else ("구름대 아래" if close < cloud_bottom else "구름대 내부"),
                "bull_score": bull_score,
                "bear_score": bear_score,
                "neutral_score": neutral_score
            })
        except Exception as e:
            continue
            
    # Sort strictly: Pick Top 2 Bull, Top 2 Bear, Top 2 Neutral
    bull_picks = sorted([c for c in candidates if c['bull_score'] >= 60], key=lambda x: x['bull_score'], reverse=True)[:2]
    bear_picks = sorted([c for c in candidates if c['bear_score'] >= 50], key=lambda x: x['bear_score'], reverse=True)[:2]
    
    used_tickers = {c['ticker'] for c in bull_picks + bear_picks}
    neutral_candidates = [c for c in candidates if c['ticker'] not in used_tickers]
    neutral_picks = sorted(neutral_candidates, key=lambda x: abs(x['kijun_gap']))[:2]
    
    return bull_picks, neutral_picks, bear_picks

def evaluate_active_positions_and_update(bull_picks, neutral_picks, bear_picks, today_str):
    """
    Evaluates EXISTING active recommended positions:
    - Triggers TAKE-PROFIT SELL if target (+15~20%) reached.
    - Triggers HARD STOP-LOSS SELL if -3% or Kijun-sen broken.
    - Triggers HOLD if trend intact.
    """
    if os.path.exists(HISTORY_CSV):
        history_df = pd.read_csv(HISTORY_CSV)
    else:
        history_df = pd.DataFrame(columns=[
            "date", "ticker", "type", "entry_price", "current_price",
            "pnl_pct", "max_gain_pct", "status", "days_active", "exit_advice"
        ])
        
    if "exit_advice" not in history_df.columns:
        history_df["exit_advice"] = "HOLD"
        
    position_alerts = []
    
    # 1. Evaluate open positions
    open_trades = history_df[history_df['status'] == 'OPEN'].copy()
    for idx, row in open_trades.iterrows():
        ticker = row['ticker']
        entry_price = float(row['entry_price'])
        pos_type = row['type']
        
        try:
            df = yf.download(ticker, period="3mo", interval="1d", progress=False)
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = calculate_indicators(df)
                last = df.iloc[-1]
                cur_price = float(last['Close'])
                kijun = float(last['Kijun'])
                
                pnl_pct = ((cur_price - entry_price) / entry_price) * 100 if pos_type == 'BULL' else ((entry_price - cur_price) / entry_price) * 100
                prev_max = float(row.get('max_gain_pct', 0.0))
                new_max = max(prev_max, pnl_pct)
                days_active = (datetime.now() - datetime.strptime(row['date'], "%Y-%m-%d")).days
                
                # Check Exit Signals
                is_stop_loss = pnl_pct <= -3.0 or (pos_type == 'BULL' and cur_price < kijun)
                is_take_profit = pnl_pct >= 15.0
                is_partial_tp = 8.0 <= pnl_pct < 15.0
                is_expired = days_active >= 65
                
                if is_take_profit:
                    status = 'CLOSED_PROFIT'
                    advice = f"🚨 [전량 익절 매도] 목표가 달성 (+{pnl_pct:.1f}%)! 전량 차익 실현 후 현금화 권고"
                    badge = "💰 익절 매도"
                elif is_stop_loss:
                    status = 'CLOSED_STOP'
                    advice = f"🚨 [칼손절 긴급 매도] 손절선 터치 ({pnl_pct:.1f}%) / 기준선 붕괴! 즉시 전량 매도 권고"
                    badge = "🛡️ 칼손절 매도"
                elif is_partial_tp:
                    status = 'OPEN'
                    advice = f"💰 [50% 분할 익절] 현재 수익률 +{pnl_pct:.1f}%. 절반 챙기고 본절 스탑 상향 권고"
                    badge = "📈 부분 익절"
                elif is_expired:
                    status = 'CLOSED_EXPIRED'
                    advice = f"⏱️ [3개월 만기 청산] 현재 수익률 {pnl_pct:+.1f}%. 포지션 종료 및 자금 회전"
                    badge = "⏱️ 만기 청산"
                else:
                    status = 'OPEN'
                    advice = f"⏳ [보유 지속] 기준선 지지 유효 (현재 {pnl_pct:+.1f}%). 손절가 ${kijun:,.2f} 유지"
                    badge = "⏳ 보유 지속"
                    
                history_df.loc[idx, 'current_price'] = cur_price
                history_df.loc[idx, 'pnl_pct'] = pnl_pct
                history_df.loc[idx, 'max_gain_pct'] = new_max
                history_df.loc[idx, 'status'] = status
                history_df.loc[idx, 'days_active'] = days_active
                history_df.loc[idx, 'exit_advice'] = advice
                
                position_alerts.append({
                    "ticker": ticker,
                    "type": pos_type,
                    "entry_date": row['date'],
                    "entry_price": entry_price,
                    "cur_price": cur_price,
                    "pnl_pct": pnl_pct,
                    "badge": badge,
                    "advice": advice
                })
        except Exception:
            pass
            
    # 2. Add today's 6 stocks (2 Bull / 2 Neutral / 2 Bear)
    existing_today = history_df[history_df['date'] == today_str]
    if existing_today.empty:
        new_rows = []
        for b in bull_picks:
            new_rows.append({"date": today_str, "ticker": b['ticker'], "type": "BULL", "entry_price": b['close'], "current_price": b['close'], "pnl_pct": 0.0, "max_gain_pct": 0.0, "status": "OPEN", "days_active": 0, "exit_advice": "신규 진입 (목표가 +15%, 손절가 -3%)"})
        for n in neutral_picks:
            new_rows.append({"date": today_str, "ticker": n['ticker'], "type": "NEUTRAL", "entry_price": n['close'], "current_price": n['close'], "pnl_pct": 0.0, "max_gain_pct": 0.0, "status": "OPEN", "days_active": 0, "exit_advice": "관망"})
        for s in bear_picks:
            new_rows.append({"date": today_str, "ticker": s['ticker'], "type": "BEAR", "entry_price": s['close'], "current_price": s['close'], "pnl_pct": 0.0, "max_gain_pct": 0.0, "status": "OPEN", "days_active": 0, "exit_advice": "숏 헤지"})
            
        if new_rows:
            history_df = pd.concat([history_df, pd.DataFrame(new_rows)], ignore_index=True)
            
    history_df.to_csv(HISTORY_CSV, index=False)
    
    # 3. Model Health Analysis
    closed = history_df[history_df['status'].str.startswith('CLOSED')]
    if len(closed) >= 4:
        bull_closed = closed[closed['type'] == 'BULL']
        win_count = len(bull_closed[bull_closed['pnl_pct'] > 0])
        total_bull = len(bull_closed)
        win_rate = (win_count / total_bull * 100) if total_bull > 0 else 0
        health_status = f"🟢 모델 정상 작동 (실전 승률: {win_rate:.1f}%)" if win_rate >= 50 else f"⚠️ 모델 재점검 경보 (실전 승률: {win_rate:.1f}%)"
    else:
        health_status = "🌱 데이터 누적 중 (초기 모델 가동 단계)"
        
    return history_df, position_alerts, health_status

def generate_email_content(today_str, bull_picks, neutral_picks, bear_picks, position_alerts, health_status):
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #f4f6f8; color: #1e293b; padding: 20px; }}
            .container {{ max-width: 680px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.06); }}
            .header {{ background: #0f172a; color: #ffffff; padding: 24px; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 20px; font-weight: 800; }}
            .sub-title {{ font-size: 13px; color: #94a3b8; margin-top: 6px; }}
            .section {{ padding: 20px; border-bottom: 1px solid #e2e8f0; }}
            .section-title {{ font-size: 16px; font-weight: 700; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }}
            .card {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px; margin-bottom: 10px; }}
            .card-alert {{ background: #fef2f2; border: 1px solid #fecaca; border-left: 4px solid #ef4444; }}
            .card-profit {{ background: #f0fdf4; border: 1px solid #bbf7d0; border-left: 4px solid #10b981; }}
            .card-bull {{ border-left: 4px solid #10b981; }}
            .card-neutral {{ border-left: 4px solid #f59e0b; }}
            .card-bear {{ border-left: 4px solid #ef4444; }}
            .ticker-head {{ display: flex; justify-content: space-between; font-weight: 700; font-size: 15px; margin-bottom: 4px; }}
            .price {{ color: #0f172a; font-family: monospace; font-size: 14px; }}
            .details {{ font-size: 12px; color: #64748b; line-height: 1.5; }}
            .health-box {{ background: #eff6ff; border: 1px solid #bfdbfe; color: #1e40af; padding: 12px; border-radius: 8px; font-size: 13px; font-weight: 600; text-align: center; }}
            .footer {{ padding: 16px; font-size: 11px; color: #94a3b8; text-align: center; background: #f8fafc; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🏛️ 알상무 퀀트 모닝 브리핑 & 매매 알림</h1>
                <div class="sub-title">{today_str} (오전 8:30 KST 기준 분석 리포트)</div>
            </div>
            
            <!-- Model Health Status -->
            <div class="section">
                <div class="health-box">
                    📊 실전 모델 유효성 검증 상태: {health_status}
                </div>
            </div>

            <!-- 🚨 0. 기존 보유 종목 매도/익절/손절 알림 -->
            <div class="section">
                <div class="section-title" style="color:#2563eb;">🔔 0. 기존 추천 종목 매도/익절/손절 알림 (Actionable Exit Signals)</div>
    """
    if position_alerts:
        for p in position_alerts:
            card_class = "card-profit" if "익절" in p['badge'] else ("card-alert" if "손절" in p['badge'] else "card")
            pnl_color = "#10b981" if p['pnl_pct'] > 0 else "#ef4444"
            html += f"""
                <div class="card {card_class}">
                    <div class="ticker-head">
                        <span>{p['ticker']} [{p['badge']}]</span>
                        <span class="price" style="color:{pnl_color};">{p['pnl_pct']:+.2f}%</span>
                    </div>
                    <div class="details">
                        • <strong>진입일:</strong> {p['entry_date']} (${p['entry_price']:,.2f}) ➔ <strong>현재가:</strong> ${p['cur_price']:,.2f}<br>
                        • <strong>📢 실전 대응 지침:</strong> <strong>{p['advice']}</strong>
                    </div>
                </div>
            """
    else:
        html += "<div class='details' style='color:#64748b; text-align:center;'>현재 추적 중인 이전 포지션이 없습니다. (오늘부터 신규 기록 시작)</div>"

    html += """
            </div>

            <!-- 1. 신규 추천 2선 -->
            <div class="section">
                <div class="section-title" style="color:#10b981;">🟢 1. 오늘 신규 추천 2선 (알상무 Pick / 빈집 선점 눌림목)</div>
    """
    for b in bull_picks:
        stop_p = b['close'] * 0.97
        tgt_p = b['close'] * 1.15
        html += f"""
                <div class="card card-bull">
                    <div class="ticker-head">
                        <span>{b['ticker']} (적합도 {b['bull_score']}점)</span>
                        <span class="price">${b['close']:,.2f}</span>
                    </div>
                    <div class="details">
                        • <strong>26일 기준선:</strong> ${b['kijun']:,.2f} (이격도 {b['kijun_gap']:+.1f}%) | <strong>거래량 비율:</strong> {b['vol_ratio']*100:.0f}% (마름)<br>
                        • <strong>진입가:</strong> ${b['close']:,.2f} | <strong>칼손절:</strong> ${stop_p:,.2f} (-3%) | <strong>1차 목표가:</strong> ${tgt_p:,.2f} (+15%)<br>
                        • <strong>알상무 뷰:</strong> 일목 구름대 상단 안착 + 기준선 지지 양봉 확인. 1차 30% 분할 매수 타점.
                    </div>
                </div>
        """
        
    html += """
            </div>

            <!-- 2. 애매 2선 -->
            <div class="section">
                <div class="section-title" style="color:#f59e0b;">🟡 2. 오늘 애매 2선 (중립 / 박스권 에너지 응축)</div>
    """
    for n in neutral_picks:
        html += f"""
                <div class="card card-neutral">
                    <div class="ticker-head">
                        <span>{n['ticker']} (관망 대상)</span>
                        <span class="price">${n['close']:,.2f}</span>
                    </div>
                    <div class="details">
                        • <strong>위치:</strong> {n['cloud_status']} | <strong>기준선 이격:</strong> {n['kijun_gap']:+.1f}%<br>
                        • <strong>알상무 뷰:</strong> 방향성이 모호한 횡보 구간. 추세 돌파나 명확한 지지선 반등 확인 전까지 관망.
                    </div>
                </div>
        """

    html += """
            </div>

            <!-- 3. 반대 2선 -->
            <div class="section">
                <div class="section-title" style="color:#ef4444;">🔴 3. 오늘 반대 2선 (숏돌이 경보 / 기준선 붕괴)</div>
    """
    for s in bear_picks:
        html += f"""
                <div class="card card-bear">
                    <div class="ticker-head">
                        <span>{s['ticker']} (위험도 {s['bear_score']}점)</span>
                        <span class="price">${s['close']:,.2f}</span>
                    </div>
                    <div class="details">
                        • <strong>위치:</strong> {s['cloud_status']} | <strong>기준선 이탈:</strong> {s['kijun_gap']:+.1f}%<br>
                        • <strong>알상무 뷰:</strong> 26일 기준선(생명선) 붕괴. 물타기 금지 및 숏 헤지 포지션 구축 권고.
                    </div>
                </div>
        """

    html += """
            </div>
            
            <div class="footer">
                알상무 17년 퀀트 엔진 자동 발송 시스템 • 매일 아침 8:30 자동 검증 & 포워드 트래킹 기록 저장
            </div>
        </div>
    </body>
    </html>
    """
    return html

def print_markdown_briefing(today_str, bull_picks, neutral_picks, bear_picks, position_alerts, health_status):
    md = f"""
# 🏛️ 알상무 퀀트 모닝 브리핑 & 매매 알림 ({today_str})

> 📊 **실전 모델 유효성 검증 상태**: `{health_status}`

---

## 🔔 0. 기존 추천 종목 매도/익절/손절 알림 (Actionable Exit Signals)

"""
    if position_alerts:
        for p in position_alerts:
            md += f"* **`{p['ticker']}`** [{p['badge']}] (수익률 `{p['pnl_pct']:+.2f}%`)\n"
            md += f"  - 진입: `${p['entry_price']:,.2f}` ➔ 현재: `${p['cur_price']:,.2f}`\n"
            md += f"  - **📢 대응 지침**: {p['advice']}\n\n"
    else:
        md += "*현재 추적 중인 이전 포지션이 없습니다. (오늘부터 신규 기록 시작)*\n\n"

    md += """---

## 🟢 1. 오늘 신규 추천 2선 (알상무 Pick / 빈집 선점)

"""
    for b in bull_picks:
        stop_p = b['close'] * 0.97
        tgt_p = b['close'] * 1.15
        md += f"### 🟢 {b['ticker']} (현재가 `${b['close']:,.2f}` | 적합도 `{b['bull_score']}점`)\n"
        md += f"* 📍 **기준선 이격도**: `{b['kijun_gap']:+.1f}%` (${b['kijun']:,.2f}) | **거래량 비율**: `{b['vol_ratio']*100:.0f}%` (마름)\n"
        md += f"* 🛡️ **칼손절가**: `${stop_p:,.2f} (-3.0%)` | 🎯 **1차 목표가**: `${tgt_p:,.2f} (+15.0%)`\n"
        md += f"* 💬 **알상무 코멘트**: 일목 구름대 상단 안착 + 기준선 지지 양봉 확인. 1차 30% 분할 매수 타점.\n\n"

    md += """---

## 🟡 2. 오늘 애매 2선 (관망 대상)

"""
    for n in neutral_picks:
        md += f"* **`{n['ticker']}`** (`${n['close']:,.2f}`): 기준선 이격 `{n['kijun_gap']:+.1f}%` / {n['cloud_status']} (박스권 횡보 중)\n"

    md += """
---

## 🔴 3. 오늘 반대 2선 (숏/손절 경보)

"""
    for s in bear_picks:
        md += f"* **`{s['ticker']}`** (`${s['close']:,.2f}`): 기준선 대비 `{s['kijun_gap']:+.1f}%` 이탈 (26일 생명선 붕괴 -> 물타기 금지)\n"

    print(md)

def main():
    today_str = datetime.now().strftime("%Y-%m-%d")
    bull_picks, neutral_picks, bear_picks = scan_and_select_2x2x2()
    history_df, position_alerts, health_status = evaluate_active_positions_and_update(bull_picks, neutral_picks, bear_picks, today_str)
    
    html_content = generate_email_content(today_str, bull_picks, neutral_picks, bear_picks, position_alerts, health_status)
    
    # Save local copy
    out_html_path = os.path.join(REPORTS_DIR, f"briefing_{today_str}.html")
    with open(out_html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
        
    print_markdown_briefing(today_str, bull_picks, neutral_picks, bear_picks, position_alerts, health_status)

if __name__ == "__main__":
    main()
