import os
import sys
import json
import smtplib
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

def update_forward_tracker(bull_picks, neutral_picks, bear_picks, today_str):
    """
    Updates trade_history.csv and calculates rolling forward returns & model validity score.
    """
    if os.path.exists(HISTORY_CSV):
        history_df = pd.read_csv(HISTORY_CSV)
    else:
        history_df = pd.DataFrame(columns=[
            "date", "ticker", "type", "entry_price", "current_price",
            "pnl_pct", "max_gain_pct", "status", "days_active"
        ])
        
    # 1. Update existing open positions
    open_trades = history_df[history_df['status'] == 'OPEN'].copy()
    for idx, row in open_trades.iterrows():
        ticker = row['ticker']
        entry_price = float(row['entry_price'])
        try:
            cur_data = yf.download(ticker, period="5d", interval="1d", progress=False)
            if not cur_data.empty:
                if isinstance(cur_data.columns, pd.MultiIndex):
                    cur_data.columns = cur_data.columns.get_level_values(0)
                cur_price = float(cur_data.iloc[-1]['Close'])
                pnl_pct = ((cur_price - entry_price) / entry_price) * 100 if row['type'] == 'BULL' else ((entry_price - cur_price) / entry_price) * 100
                
                prev_max = float(row['max_gain_pct'])
                new_max = max(prev_max, pnl_pct)
                
                # Check exit
                status = 'OPEN'
                if pnl_pct <= -3.0:
                    status = 'STOP_LOSS (-3%)'
                elif pnl_pct >= 15.0:
                    status = 'TAKE_PROFIT (+15%)'
                elif (datetime.now() - datetime.strptime(row['date'], "%Y-%m-%d")).days >= 65:
                    status = 'EXPIRED (3M)'
                    
                history_df.loc[idx, 'current_price'] = cur_price
                history_df.loc[idx, 'pnl_pct'] = pnl_pct
                history_df.loc[idx, 'max_gain_pct'] = new_max
                history_df.loc[idx, 'status'] = status
                history_df.loc[idx, 'days_active'] = (datetime.now() - datetime.strptime(row['date'], "%Y-%m-%d")).days
        except Exception:
            pass
            
    # 2. Add today's 6 stocks (2 Bull / 2 Neutral / 2 Bear)
    new_rows = []
    for b in bull_picks:
        new_rows.append({"date": today_str, "ticker": b['ticker'], "type": "BULL", "entry_price": b['close'], "current_price": b['close'], "pnl_pct": 0.0, "max_gain_pct": 0.0, "status": "OPEN", "days_active": 0})
    for n in neutral_picks:
        new_rows.append({"date": today_str, "ticker": n['ticker'], "type": "NEUTRAL", "entry_price": n['close'], "current_price": n['close'], "pnl_pct": 0.0, "max_gain_pct": 0.0, "status": "OPEN", "days_active": 0})
    for s in bear_picks:
        new_rows.append({"date": today_str, "ticker": s['ticker'], "type": "BEAR", "entry_price": s['close'], "current_price": s['close'], "pnl_pct": 0.0, "max_gain_pct": 0.0, "status": "OPEN", "days_active": 0})
        
    if new_rows:
        history_df = pd.concat([history_df, pd.DataFrame(new_rows)], ignore_index=True)
        
    history_df.to_csv(HISTORY_CSV, index=False)
    
    # 3. Model Health Analysis
    closed = history_df[history_df['status'] != 'OPEN']
    if len(closed) >= 5:
        bull_closed = closed[closed['type'] == 'BULL']
        win_count = len(bull_closed[bull_closed['pnl_pct'] > 0])
        total_bull = len(bull_closed)
        win_rate = (win_count / total_bull * 100) if total_bull > 0 else 0
        health_status = f"🟢 모델 정상 작동 (실전 승률: {win_rate:.1f}%)" if win_rate >= 50 else f"⚠️ 모델 재점검 경보 (실전 승률: {win_rate:.1f}%)"
    else:
        health_status = "🌱 데이터 누적 중 (초기 모델 가동 단계)"
        
    return history_df, health_status

def generate_email_content(today_str, bull_picks, neutral_picks, bear_picks, health_status):
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
                <h1>🏛️ 알상무 퀀트 모닝 브리핑 (2+2+2)</h1>
                <div class="sub-title">{today_str} (오전 8:30 KST 기준 분석 리포트)</div>
            </div>
            
            <!-- Model Health Status -->
            <div class="section">
                <div class="health-box">
                    📊 실전 모델 유효성 검증 상태: {health_status}
                </div>
            </div>

            <!-- 1. 추천 2선 -->
            <div class="section">
                <div class="section-title" style="color:#10b981;">🟢 1. 추천 2선 (알상무 Pick / 빈집 선점 눌림목)</div>
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
                <div class="section-title" style="color:#f59e0b;">🟡 2. 애매 2선 (중립 / 박스권 에너지 응축)</div>
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
                <div class="section-title" style="color:#ef4444;">🔴 3. 반대 2선 (숏돌이 경보 / 기준선 붕괴)</div>
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

def send_email(subject, html_content):
    sender = os.environ.get("EMAIL_SENDER")
    password = os.environ.get("EMAIL_PASSWORD") # App Password
    receiver = os.environ.get("EMAIL_RECEIVER")
    
    if not sender or not password or not receiver:
        print("[INFO] Email credentials not set in environment. Saved HTML report locally.")
        return False
        
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"알상무 퀀트 봇 <{sender}>"
        msg["To"] = receiver
        
        part = MIMEText(html_content, "html", "utf-8")
        msg.attach(part)
        
        # Gmail SMTP
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())
            
        print(f"Successfully sent daily briefing email to {receiver}!")
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False

def main():
    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"=== Running Al-Sangmoo Daily Bot ({today_str}) ===")
    
    bull_picks, neutral_picks, bear_picks = scan_and_select_2x2x2()
    history_df, health_status = update_forward_tracker(bull_picks, neutral_picks, bear_picks, today_str)
    
    html_content = generate_email_content(today_str, bull_picks, neutral_picks, bear_picks, health_status)
    
    # Save local copy
    out_html_path = os.path.join(REPORTS_DIR, f"briefing_{today_str}.html")
    with open(out_html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"Saved local HTML report: {out_html_path}")
    
    # Send email
    subject = f"🏛️ [알상무 퀀트 2+2+2] {today_str} 오늘의 나스닥 모닝 브리핑"
    send_email(subject, html_content)
    print("=== Daily Bot Execution Complete! ===")

if __name__ == "__main__":
    main()
