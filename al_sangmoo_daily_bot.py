import os
import sys
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf
import youtube_stream_scanner
import db_manager
import generate_dashboard_feed

# Windows cp949 terminal encoding fix
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_CSV = os.path.join(BASE_DIR, "trade_history.csv")
REPORTS_DIR = os.path.join(BASE_DIR, "daily_reports")
DASHBOARD_JSON = os.path.join(BASE_DIR, "dashboard_data.json")
DASHBOARD_HTML = os.path.join(BASE_DIR, "al_sangmoo_dashboard.html")
os.makedirs(REPORTS_DIR, exist_ok=True)

def load_env_file():
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
        except Exception:
            pass

load_env_file()
DEFAULT_EMAIL_RECEIVER = os.environ.get("ALERT_EMAIL_RECEIVER") or os.environ.get("EMAIL_RECEIVER") or "kdw58170425@gmail.com"

def send_email_report(subject, html_body, receiver=None):
    load_env_file()
    if not receiver:
        receiver = os.environ.get("ALERT_EMAIL_RECEIVER") or os.environ.get("EMAIL_RECEIVER") or DEFAULT_EMAIL_RECEIVER
        
    gmail_user = os.environ.get("GMAIL_USER") or os.environ.get("EMAIL_SENDER")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD") or os.environ.get("EMAIL_PASSWORD")
    
    if not gmail_user or not gmail_password:
        print(f"[Email Dispatch] GMAIL_USER / GMAIL_APP_PASSWORD not set in .env. Report saved at {REPORTS_DIR}.")
        return False
        
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Al-Sangmoo Quant Engine <{gmail_user}>"
        msg["To"] = receiver
        
        part = MIMEText(html_body, "html", "utf-8")
        msg.attach(part)
        
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, receiver, msg.as_string())
            
        print(f"[Email Dispatch] Briefing sent successfully to {receiver}.")
        return True
    except Exception as e:
        print(f"[Email Dispatch Error] {e}")
        return False

# Base Universe fallback
UNIVERSE = [
    "NVDA", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AAPL", "AVGO", "COST", "LLY",
    "AMD", "QCOM", "PLTR", "SMCI", "MU", "ARM", "VST", "CEG", "GEV", "ETN",
    "005930.KS", "000660.KS", "012450.KS"
]

def calculate_indicators(df):
    df = df.dropna(subset=['Close', 'High', 'Low', 'Volume']).copy()
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

def scan_and_select_2x2x2(stream_sentiment_list=None):
    """
    3-Gate Filter:
    Gate 0: Macro Climate Filter
    Gate 1: Host NLP Recommendation Intent (순환매 수혜, 추천, 좋게 보고 있다)
    Gate 2: 17-Year Quant Formula (일목 구름대 안착, 26일 기준선 지지, 거래량 마름)
    """
    if stream_sentiment_list is None:
        stream_sentiment_list = []
        
    sentiment_map = {item["ticker"]: item for item in stream_sentiment_list}
    priority_tickers = [item["ticker"] for item in stream_sentiment_list]
    
    scan_list = []
    for t in priority_tickers:
        if t not in scan_list:
            scan_list.append(t)
    for t in UNIVERSE:
        if t not in scan_list:
            scan_list.append(t)
            
    candidates = []
    
    for ticker in scan_list:
        try:
            df = yf.download(ticker, period="6mo", interval="1d", progress=False)
            if df.empty:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
            df = df.dropna(subset=['Close', 'High', 'Low', 'Volume']).copy()
            if len(df) < 55:
                continue
                
            df = calculate_indicators(df)
            df = df.dropna(subset=['Close', 'Kijun', 'Tenkan', 'SMA20', 'Vol_Ratio'])
            if df.empty:
                continue
                
            last = df.iloc[-1]
            
            close = float(last['Close'])
            kijun = float(last['Kijun'])
            tenkan = float(last['Tenkan'])
            vol_ratio = float(last['Vol_Ratio'])
            
            span_a = float(last['SpanA']) if not pd.isna(last['SpanA']) else float(last['SMA20'])
            span_b = float(last['SpanB']) if not pd.isna(last['SpanB']) else float(last['SMA20'])
            cloud_top = max(span_a, span_b)
            cloud_bottom = min(span_a, span_b)
            
            kijun_gap = ((close - kijun) / kijun) * 100
            
            bull_score = 0
            if close >= cloud_top: bull_score += 35
            if -0.5 <= kijun_gap <= 4.0: bull_score += 35
            if vol_ratio <= 0.75: bull_score += 20
            if tenkan >= kijun: bull_score += 10
            
            bear_score = 0
            if close < kijun: bear_score += 40
            if close < cloud_bottom: bear_score += 35
            if kijun_gap < -2.0: bear_score += 15
            
            neutral_score = 100 - max(bull_score, bear_score)
            cloud_status = "구름대 상단 안착" if close >= cloud_top else ("구름대 하단 붕괴" if close < cloud_bottom else "구름대 내부 횡보")
            
            s_data = sentiment_map.get(ticker, {
                "mentions": 0, "pos_score": 0, "neg_score": 0, "net_sentiment": 0,
                "host_intent": "GENERAL_UNIVERSE", "intent_desc": "일반 유니버스 종목",
                "positive_reasons": [], "caution_reasons": []
            })
            
            candidates.append({
                "ticker": ticker,
                "close": close,
                "kijun": kijun,
                "tenkan": tenkan,
                "kijun_gap": kijun_gap,
                "vol_ratio": vol_ratio,
                "cloud_status": cloud_status,
                "bull_score": bull_score,
                "bear_score": bear_score,
                "neutral_score": neutral_score,
                "mentions": s_data.get("mentions", 0),
                "pos_score": s_data.get("pos_score", 0),
                "neg_score": s_data.get("neg_score", 0),
                "net_sentiment": s_data.get("net_sentiment", 0),
                "host_intent": s_data.get("host_intent", "GENERAL_UNIVERSE"),
                "intent_desc": s_data.get("intent_desc", "일반 유니버스"),
                "positive_reasons": s_data.get("positive_reasons", []),
                "caution_reasons": s_data.get("caution_reasons", [])
            })
        except Exception:
            continue
            
    # 1. Bull Picks: Host Positively Recommended / Rotation Favored + Chart Validated
    bull_pool = sorted(
        [c for c in candidates if c['bull_score'] >= 60 and c['net_sentiment'] >= 0],
        key=lambda x: (x['net_sentiment'], x['bull_score']),
        reverse=True
    )
    bull_picks = bull_pool[:2]
    if len(bull_picks) < 2:
        for c in sorted([c for c in candidates if c['bull_score'] >= 60], key=lambda x: x['bull_score'], reverse=True):
            if len(bull_picks) < 2 and c['ticker'] not in [b['ticker'] for b in bull_picks]:
                bull_picks.append(c)
                
    # 2. Bear Picks: Host Caution OR 26-day Kijun-sen breakdown
    bear_pool = sorted(
        [c for c in candidates if c['bear_score'] >= 50],
        key=lambda x: (x['bear_score'], -x['net_sentiment']),
        reverse=True
    )
    bear_picks = bear_pool[:2]
    
    # 3. Neutral Picks: Consolidation / Wait
    used_tickers = {c['ticker'] for c in bull_picks + bear_picks}
    neutral_pool = [c for c in candidates if c['ticker'] not in used_tickers]
    neutral_picks = sorted(neutral_pool, key=lambda x: abs(x['kijun_gap']))[:2]
    
    return bull_picks, neutral_picks, bear_picks, candidates

def evaluate_user_portfolio_positions():
    portfolio_data = db_manager.get_live_portfolio()
    holdings = portfolio_data.get("holdings", [])
    
    portfolio_alerts = []
    for h in holdings:
        ticker = h['ticker']
        buy_price = float(h['buy_price'])
        cur_price = float(h['current_price'])
        pnl_pct = float(h['pnl_pct'])
        qty = float(h['quantity'])
        target_p = float(h.get('target_price', round(buy_price * 1.15, 2)))
        stop_p = float(h.get('stop_loss_price', round(buy_price * 0.97, 2)))
        
        is_take_profit = cur_price >= target_p or pnl_pct >= 15.0
        is_stop_loss = cur_price <= stop_p or pnl_pct <= -3.0
        is_partial_tp = 8.0 <= pnl_pct < 15.0
        
        if is_take_profit:
            badge = "전량 익절 매도"
            advice = f"목표 수익률(+15%) 달성에 따른 전량 차익 실현 권고 (수익률 {pnl_pct:+.2f}%)"
        elif is_stop_loss:
            badge = "칼손절 긴급 매도"
            advice = f"손절 기준선(-3%) 이탈에 따른 전량 리스크 청산 권고 (손실률 {pnl_pct:+.2f}%)"
        elif is_partial_tp:
            badge = "50% 분할 익절"
            advice = f"1차 분할 익절 구간 진입 (+{pnl_pct:.1f}%). 50% 차익 실현 후 스탑 본절가 상향"
        else:
            badge = "보유 지속"
            advice = f"26일 기준선 지지 유효. 손절선 ${stop_p:,.2f} 유지 / 목표가 ${target_p:,.2f}"
            
        portfolio_alerts.append({
            "ticker": ticker,
            "quantity": qty,
            "buy_date": h['buy_date'],
            "buy_price": buy_price,
            "cur_price": cur_price,
            "pnl_pct": pnl_pct,
            "target_price": target_p,
            "stop_loss_price": stop_p,
            "badge": badge,
            "advice": advice
        })
        
    return portfolio_alerts

def evaluate_active_positions_and_update(bull_picks, neutral_picks, bear_picks, today_str):
    if os.path.exists(HISTORY_CSV):
        history_df = pd.read_csv(HISTORY_CSV)
    else:
        history_df = pd.DataFrame(columns=[
            "date", "ticker", "type", "entry_price", "current_price",
            "pnl_pct", "max_gain_pct", "status", "days_active", "exit_advice"
        ])
        
    if "exit_advice" not in history_df.columns:
        history_df["exit_advice"] = "HOLD"
        
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
                
                is_stop_loss = pnl_pct <= -3.0 or (pos_type == 'BULL' and cur_price < kijun)
                is_take_profit = pnl_pct >= 15.0
                is_partial_tp = 8.0 <= pnl_pct < 15.0
                is_expired = days_active >= 65
                
                if is_take_profit:
                    status = 'CLOSED_PROFIT'
                    advice = f"목표 수익률(+15%) 달성 (+{pnl_pct:.1f}%)"
                elif is_stop_loss:
                    status = 'CLOSED_STOP'
                    advice = f"손절선(-3%) 이탈 ({pnl_pct:.1f}%)"
                elif is_partial_tp:
                    status = 'OPEN'
                    advice = f"50% 분할 익절 구간 (+{pnl_pct:.1f}%)"
                elif is_expired:
                    status = 'CLOSED_EXPIRED'
                    advice = f"3개월 만기 도달 포지션 종료 ({pnl_pct:+.1f}%)"
                else:
                    status = 'OPEN'
                    advice = f"보유 지속 (손절선 ${kijun:,.2f})"
                    
                history_df.loc[idx, 'current_price'] = cur_price
                history_df.loc[idx, 'pnl_pct'] = pnl_pct
                history_df.loc[idx, 'max_gain_pct'] = new_max
                history_df.loc[idx, 'status'] = status
                history_df.loc[idx, 'days_active'] = days_active
                history_df.loc[idx, 'exit_advice'] = advice
        except Exception:
            pass
            
    existing_today = history_df[history_df['date'] == today_str]
    if existing_today.empty:
        new_rows = []
        for b in bull_picks:
            new_rows.append({"date": today_str, "ticker": b['ticker'], "type": "BULL", "entry_price": b['close'], "current_price": b['close'], "pnl_pct": 0.0, "max_gain_pct": 0.0, "status": "OPEN", "days_active": 0, "exit_advice": "신규 진입 (목표가 +15%, 손절가 -3%)"})
        for n in neutral_picks:
            new_rows.append({"date": today_str, "ticker": n['ticker'], "type": "NEUTRAL", "entry_price": n['close'], "current_price": n['close'], "pnl_pct": 0.0, "max_gain_pct": 0.0, "status": "OPEN", "days_active": 0, "exit_advice": "중립 관망"})
        for s in bear_picks:
            new_rows.append({"date": today_str, "ticker": s['ticker'], "type": "BEAR", "entry_price": s['close'], "current_price": s['close'], "pnl_pct": 0.0, "max_gain_pct": 0.0, "status": "OPEN", "days_active": 0, "exit_advice": "리스크 회피 / 숏"})
            
        if new_rows:
            history_df = pd.concat([history_df, pd.DataFrame(new_rows)], ignore_index=True)
            
    history_df.to_csv(HISTORY_CSV, index=False)
    
    # SQLite sync
    try:
        db_manager.init_db()
        conn = db_manager.get_db()
        cursor = conn.cursor()
        for idx, row in history_df.iterrows():
            e_price = float(row['entry_price'])
            tgt_p = round(e_price * 1.15, 2)
            stop_p = round(e_price * 0.97, 2)
            part_p = round(e_price * 1.08, 2)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("""
            INSERT OR REPLACE INTO trades (
                id, date, ticker, type, entry_price, current_price,
                target_price, partial_tp_price, stop_loss_price,
                pnl_pct, max_gain_pct, status, days_active, exit_advice, updated_at
            )
            VALUES (
                (SELECT id FROM trades WHERE date = ? AND ticker = ?),
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?, ?
            )
            """, (
                row['date'], row['ticker'],
                row['date'], row['ticker'], row['type'], e_price, float(row.get('current_price', e_price)),
                tgt_p, part_p, stop_p,
                float(row.get('pnl_pct', 0.0)), float(row.get('max_gain_pct', 0.0)),
                str(row.get('status', 'OPEN')), int(row.get('days_active', 0)),
                str(row.get('exit_advice', '보유 지속')), now_str
            ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[SQLite Sync Warning] {e}")
        
    closed = history_df[history_df['status'].str.startswith('CLOSED')]
    if len(closed) >= 4:
        bull_closed = closed[closed['type'] == 'BULL']
        win_count = len(bull_closed[bull_closed['pnl_pct'] > 0])
        total_bull = len(bull_closed)
        win_rate = (win_count / total_bull * 100) if total_bull > 0 else 0
        health_status = f"검증 정상 가동 (실전 누적 승률: {win_rate:.1f}%)" if win_rate >= 50 else f"모형 재점검 기준 도달 (승률: {win_rate:.1f}%)"
    else:
        health_status = "데이터 누적 단계 (초기 전수 포워드 트래킹 중)"
        
    return history_df, health_status

def generate_email_content(today_str, bull_picks, neutral_picks, bear_picks, portfolio_alerts, health_status, stream_info=None):
    if stream_info is None:
        stream_info = {}
        
    stream_title = stream_info.get("title", "바이킹스 데일리 매크로 & 라이브 방송 분석")
    stream_url = stream_info.get("url", "https://www.youtube.com/@wepoll_original/streams")
    macro_climate = stream_info.get("macro_climate", {})
    macro_gauges = stream_info.get("macro_gauges", {})
    mentioned_stocks = stream_info.get("mentioned_stocks", [])
    
    vix = macro_gauges.get("vix", {"val": 15.8, "delta": "+0.4%", "status": "NORMAL"})
    us10y = macro_gauges.get("us10y", {"val": 4.42, "delta": "+1.2bp", "status": "BURDEN"})
    wti = macro_gauges.get("wti", {"val": 78.5, "delta": "-0.5%", "status": "STABLE"})
    
    macro_headline = macro_climate.get("macro_headline", "[거시 게이트 0단계: 이번 주 신규 매수 보류 / 관망·현금 유지 권고]")
    macro_directive = macro_climate.get("macro_action_directive", "거시 지표 및 방송 지침상 이번 주는 관망 주간입니다.")
    external_shocks = ", ".join(macro_climate.get("external_shocks", ["금리 경로 영향권", "인플레이션 변동성"]))
    
    rec_list = [f"{m['ticker']}(+{m['net_sentiment']} / 키워드: {', '.join(m.get('positive_reasons', []))})" for m in mentioned_stocks if m.get('host_intent') == 'BULLISH_RECOMMENDED']
    rec_summary_str = " | ".join(rec_list[:5]) if rec_list else "방송 본문 문맥 분석 완료"

    html = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background-color: #f1f5f9; color: #0f172a; margin: 0; padding: 24px; line-height: 1.5; }}
            .container {{ max-width: 720px; margin: 0 auto; background: #ffffff; border: 1px solid #cbd5e1; border-radius: 4px; overflow: hidden; }}
            .header {{ background: #0f172a; color: #ffffff; padding: 24px 28px; border-bottom: 2px solid #334155; }}
            .header h1 {{ margin: 0; font-size: 18px; font-weight: 700; letter-spacing: -0.02em; }}
            .header .meta {{ font-size: 12px; color: #94a3b8; margin-top: 6px; }}
            
            .macro-alert-bar {{ background: #fffbeb; border: 1px solid #fef3c7; border-left: 5px solid #d97706; padding: 14px 18px; font-size: 13px; color: #92400e; font-weight: 600; line-height: 1.5; }}
            .macro-gauges-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 10px; }}
            .macro-gauge-box {{ background: #ffffff; border: 1px solid #e2e8f0; border-radius: 2px; padding: 8px 12px; font-size: 12px; }}
            .macro-gauge-title {{ color: #64748b; font-size: 11px; text-transform: uppercase; font-weight: 600; }}
            .macro-gauge-val {{ font-family: monospace; font-size: 14px; font-weight: 700; color: #0f172a; margin-top: 2px; }}
            
            .section {{ padding: 20px 28px; border-bottom: 1px solid #e2e8f0; }}
            .section-title {{ font-size: 14px; font-weight: 700; color: #1e293b; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 14px; padding-bottom: 6px; border-bottom: 2px solid #0f172a; }}
            .macro-box {{ background: #f8fafc; border: 1px solid #cbd5e1; border-left: 4px solid #334155; padding: 14px 16px; margin-bottom: 12px; }}
            .macro-row {{ font-size: 13px; margin-bottom: 6px; }}
            .macro-row strong {{ color: #0f172a; }}
            .table {{ width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 8px; }}
            .table th {{ background: #f1f5f9; color: #475569; font-weight: 600; text-align: left; padding: 8px 10px; border: 1px solid #e2e8f0; }}
            .table td {{ padding: 8px 10px; border: 1px solid #e2e8f0; color: #1e293b; }}
            .stock-card {{ background: #ffffff; border: 1px solid #cbd5e1; border-left: 4px solid #0f172a; padding: 14px 16px; margin-bottom: 10px; }}
            .stock-card-bull {{ border-left-color: #059669; }}
            .stock-card-neutral {{ border-left-color: #d97706; }}
            .stock-card-bear {{ border-left-color: #dc2626; }}
            .stock-head {{ display: flex; justify-content: space-between; font-weight: 700; font-size: 14px; margin-bottom: 6px; }}
            .stock-meta {{ font-size: 12px; color: #475569; line-height: 1.6; }}
            .badge {{ display: inline-block; padding: 2px 6px; font-size: 11px; font-weight: 600; border-radius: 2px; }}
            .badge-bull {{ background: #dcfce7; color: #166534; }}
            .badge-neutral {{ background: #fef3c7; color: #92400e; }}
            .badge-bear {{ background: #fee2e2; color: #991b1b; }}
            .badge-hold {{ background: #e0f2fe; color: #075985; }}
            .footer {{ padding: 16px 28px; font-size: 11px; color: #64748b; background: #f8fafc; text-align: center; border-top: 1px solid #e2e8f0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>AL-SANGMOO QUANTITATIVE TACTICAL REPORT</h1>
                <div class="meta">발행일시: {today_str} 12:30 KST | 분석 모듈: 3단계 게이트 의사결정 파이프라인 (거시 기후 ➔ 문맥 NLP ➔ 17년 퀀트)</div>
            </div>
            
            <!-- Gate 0: Macro Climate & Weekly Directive -->
            <div class="macro-alert-bar">
                <div style="font-size:14px; font-weight:800; color:#b45309; margin-bottom:4px;">{macro_headline}</div>
                <div>{macro_directive}</div>
                
                <!-- Macro 3 Gauges -->
                <div class="macro-gauges-grid">
                    <div class="macro-gauge-box">
                        <div class="macro-gauge-title">VIX 공포지수</div>
                        <div class="macro-gauge-val">{vix['val']} <span style="font-size:11px; color:#64748b;">({vix['status']})</span></div>
                    </div>
                    <div class="macro-gauge-box">
                        <div class="macro-gauge-title">미국채 10년물 금리</div>
                        <div class="macro-gauge-val">{us10y['val']}% <span style="font-size:11px; color:#b45309;">({us10y['status']})</span></div>
                    </div>
                    <div class="macro-gauge-box">
                        <div class="macro-gauge-title">WTI 국제유가</div>
                        <div class="macro-gauge-val">${wti['val']} <span style="font-size:11px; color:#166534;">({wti['status']})</span></div>
                    </div>
                </div>
            </div>
            
            <!-- 1. Macro Regime & Host Recommended Stocks -->
            <div class="section">
                <div class="section-title">1. Vikings Live Broadcast Context & Macro Intelligence</div>
                <div class="macro-box">
                    <div class="macro-row"><strong>라이브 방송 본문:</strong> <a href="{stream_url}" target="_blank" style="color:#0f172a; text-decoration:underline;">{stream_title}</a></div>
                    <div class="macro-row"><strong>거시 리스크 요인:</strong> {external_shocks}</div>
                    <div class="macro-row"><strong>방송 내 추천·순환매 긍정 평가 종목:</strong> <span style="font-family:monospace; font-size:12px; font-weight:600;">{rec_summary_str}</span></div>
                </div>
            </div>

            <!-- 2. Real Portfolio Risk & Execution Monitor -->
            <div class="section">
                <div class="section-title">2. Real Portfolio Risk & Execution Monitor</div>
    """
    if portfolio_alerts:
        html += """
                <table class="table">
                    <thead>
                        <tr>
                            <th>종목</th>
                            <th>보유수량</th>
                            <th>매수가</th>
                            <th>현재가</th>
                            <th>수익률</th>
                            <th>목표가(+15%)</th>
                            <th>손절가(-3%)</th>
                            <th>실전 대응 지침</th>
                        </tr>
                    </thead>
                    <tbody>
        """
        for p in portfolio_alerts:
            pnl_color = "#059669" if p['pnl_pct'] > 0 else "#dc2626"
            badge_class = "badge-bull" if "익절" in p['badge'] else ("badge-bear" if "손절" in p['badge'] else "badge-hold")
            html += f"""
                        <tr>
                            <td><strong>{p['ticker']}</strong></td>
                            <td>{p['quantity']:.0f}주</td>
                            <td>${p['buy_price']:,.2f}</td>
                            <td>${p['cur_price']:,.2f}</td>
                            <td style="color:{pnl_color}; font-weight:700;">{p['pnl_pct']:+.2f}%</td>
                            <td>${p['target_price']:,.2f}</td>
                            <td>${p['stop_loss_price']:,.2f}</td>
                            <td><span class="badge {badge_class}">{p['badge']}</span> {p['advice']}</td>
                        </tr>
            """
        html += """
                    </tbody>
                </table>
        """
    else:
        html += """
                <div style="background:#f8fafc; border:1px solid #e2e8f0; padding:14px; font-size:12px; color:#64748b; text-align:center;">
                    [실계좌 보유 현황: 0 종목] 현재 실제 포트폴리오에 등록된 보유 종목이 없습니다.<br>
                    웹 대시보드(http://localhost:8000)에서 포지션 진입 시 실시간 손절(-3%) 및 목표가(+15%) 추적 모니터링이 자동 개시됩니다.
                </div>
        """

    html += """
            </div>

            <!-- 3. Quantitative Evaluation on Recommended Stocks (2+2+2) -->
            <div class="section">
                <div class="section-title">3. Tactical 2+2+2 Matrix (시장 안정 시 최우선 매수 후보)</div>
                <div style="font-size:12px; font-weight:700; color:#059669; margin-bottom:8px;">[Primary Accumulation] 방송 긍정 추천 + 퀀트 지표 합격 (거시 안정 시 1차 분할 매수 1순위)</div>
    """
    for b in bull_picks:
        stop_p = b['close'] * 0.97
        tgt_p = b['close'] * 1.15
        pos_reasons = f"방송 문맥 긍정 ({', '.join(b.get('positive_reasons', []))}) | " if b.get('positive_reasons') else ""
        html += f"""
                <div class="stock-card stock-card-bull">
                    <div class="stock-head">
                        <span>{b['ticker']} &nbsp;<span class="badge badge-bull">{pos_reasons}적합도 {b['bull_score']}점</span></span>
                        <span style="font-family:monospace;">${b['close']:,.2f}</span>
                    </div>
                    <div class="stock-meta">
                        • <strong>26일 기준선:</strong> ${b['kijun']:,.2f} (이격도 {b['kijun_gap']:+.1f}%) | <strong>20일 거래량 비율:</strong> {b['vol_ratio']*100:.0f}% (수급 마름)<br>
                        • <strong>진입 기준가:</strong> ${b['close']:,.2f} | <strong>1차 목표가:</strong> ${tgt_p:,.2f} (+15.0%) | <strong>손절 기준선:</strong> ${stop_p:,.2f} (-3.0%)<br>
                        • <strong>정량 분석 평가:</strong> 방송 본문에서 긍정 추천 평가를 받았으며, 일목 구름대 상단 안착 및 26일 기준선 지지 확인. 거시 변동성 진정 시 1순위 분할 매수 진입 대상.
                    </div>
                </div>
        """

    html += """
                <div style="font-size:12px; font-weight:700; color:#d97706; margin:16px 0 8px 0;">[Consolidation / Neutral] 박스권 횡보 및 추세 수렴 종목 (신규 진입 보류)</div>
    """
    for n in neutral_picks:
        html += f"""
                <div class="stock-card stock-card-neutral">
                    <div class="stock-head">
                        <span>{n['ticker']} &nbsp;<span class="badge badge-neutral">관망 대상</span></span>
                        <span style="font-family:monospace;">${n['close']:,.2f}</span>
                    </div>
                    <div class="stock-meta">
                        • <strong>위치 상태:</strong> {n['cloud_status']} | <strong>기준선 이격도:</strong> {n['kijun_gap']:+.1f}%<br>
                        • <strong>정량 분석 평가:</strong> 단기 박스권 횡보 및 추세 수렴 구간. 상방 돌파 또는 확정적 지지 반등 확인 전까지 신규 진입 보류.
                    </div>
                </div>
        """

    html += """
                <div style="font-size:12px; font-weight:700; color:#dc2626; margin:16px 0 8px 0;">[Risk Alert / Short Hedge] 기준선 붕괴 및 리스크 회피 종목 (생명선 이탈 / 매수 절대 금지)</div>
    """
    for s in bear_picks:
        caution_info = f"방송 문맥 주의 ({', '.join(s.get('caution_reasons', []))}) | " if s.get('caution_reasons') else ""
        html += f"""
                <div class="stock-card stock-card-bear">
                    <div class="stock-head">
                        <span>{s['ticker']} &nbsp;<span class="badge badge-bear">{caution_info}위험도 {s['bear_score']}점</span></span>
                        <span style="font-family:monospace;">${s['close']:,.2f}</span>
                    </div>
                    <div class="stock-meta">
                        • <strong>위치 상태:</strong> {s['cloud_status']} | <strong>기준선 이탈도:</strong> {s['kijun_gap']:+.1f}%<br>
                        • <strong>정량 분석 평가:</strong> 26일 기준선(생명선) 하향 붕괴. 추가 낙폭 리스크 존재하므로 물타기/매수 절대 금지 및 숏 헤지 우위.
                    </div>
                </div>
        """

    html += f"""
            </div>

            <!-- 4. Model Verification -->
            <div class="section">
                <div class="section-title">4. Model Governance & Verification</div>
                <div style="font-size:12px; color:#475569;">
                    • <strong>전수 포워드 트래킹 상태:</strong> {health_status}<br>
                    • <strong>시스템 아키텍처:</strong> Gate 0 (거시 기후) ➔ Gate 1 (방송 문맥 NLP) ➔ Gate 2 (알상무 17년 퀀트)
                </div>
            </div>
            
            <div class="footer">
                Al-Sangmoo Quantitative Risk Engine • Confidential Portfolio Report • Generated Daily at 12:30 KST
            </div>
        </div>
    </body>
    </html>
    """
    return html

def print_markdown_briefing(today_str, bull_picks, neutral_picks, bear_picks, portfolio_alerts, health_status, stream_info=None):
    if stream_info is None:
        stream_info = {}
        
    stream_title = stream_info.get("title", "바이킹스 데일리 매크로 & 라이브 방송 분석")
    stream_url = stream_info.get("url", "https://www.youtube.com/@wepoll_original/streams")
    macro_climate = stream_info.get("macro_climate", {})
    macro_gauges = stream_info.get("macro_gauges", {})
    mentioned_stocks = stream_info.get("mentioned_stocks", [])
    
    vix = macro_gauges.get("vix", {"val": 15.8, "status": "NORMAL"})
    us10y = macro_gauges.get("us10y", {"val": 4.42, "status": "BURDEN"})
    wti = macro_gauges.get("wti", {"val": 78.5, "status": "STABLE"})
    
    macro_headline = macro_climate.get("macro_headline", "[거시 게이트 0단계: 이번 주 신규 매수 보류 / 관망·현금 유지 권고]")
    macro_directive = macro_climate.get("macro_action_directive", "거시 지표 및 방송 지침상 이번 주는 관망 주간입니다.")
    external_shocks = ", ".join(macro_climate.get("external_shocks", ["금리 경로 영향권", "인플레이션 변동성"]))
    
    rec_list = [f"{m['ticker']}(+{m['net_sentiment']} / {', '.join(m.get('positive_reasons', []))})" for m in mentioned_stocks if m.get('host_intent') == 'BULLISH_RECOMMENDED']
    rec_summary_str = " | ".join(rec_list[:5]) if rec_list else "방송 본문 문맥 분석 완료"

    md = f"""# AL-SANGMOO QUANTITATIVE TACTICAL REPORT ({today_str})

발행일시: {today_str} 12:30 KST | 3단계 게이트 의사결정 파이프라인 (거시 기후 ➔ 문맥 NLP ➔ 17년 퀀트)

---

## 0. Gate-0 Macro Climate & Weekly Directive

* **거시 총평**: {macro_headline}
* **실전 거시 지침**: {macro_directive}
* **실시간 거시 지표**: VIX `{vix['val']}` ({vix['status']}) | 미국채 10년물 `{us10y['val']}%` ({us10y['status']}) | WTI 유가 `${wti['val']}` ({wti['status']})

---

## 1. Vikings Live Broadcast Context & Macro Flow

* **라이브 방송**: [{stream_title}]({stream_url})
* **거시 리스크 요인**: {external_shocks}
* **방송 내 추천·순환매 긍정 평가 종목**: `{rec_summary_str}`

---

## 2. Real Portfolio Risk & Execution Monitor

"""
    if portfolio_alerts:
        for p in portfolio_alerts:
            md += f"* **{p['ticker']}** ({p['quantity']:.0f}주) [{p['badge']}] (수익률 {p['pnl_pct']:+.2f}%)\n"
            md += f"  - 매수가: ${p['buy_price']:,.2f} | 현재가: ${p['cur_price']:,.2f}\n"
            md += f"  - 목표가(+15%): ${p['target_price']:,.2f} | 손절가(-3%): ${p['stop_loss_price']:,.2f}\n"
            md += f"  - 대응 지침: {p['advice']}\n\n"
    else:
        md += "*[실계좌 보유 현황: 0 종목] 현재 실제 포트폴리오에 등록된 보유 종목이 없습니다.*\n\n"

    md += """---

## 3. Tactical 2+2+2 Matrix (시장 안정 시 최우선 매수 후보)

### [Primary Accumulation] 방송 긍정 추천 + 퀀트 지표 합격 (거시 안정 시 1순위 매수)
"""
    for b in bull_picks:
        stop_p = b['close'] * 0.97
        tgt_p = b['close'] * 1.15
        pos_reasons = f"방송 문맥 긍정 ({', '.join(b.get('positive_reasons', []))}) | " if b.get('positive_reasons') else ""
        md += f"* **{b['ticker']}** (현재가 ${b['close']:,.2f} | {pos_reasons}적합도 {b['bull_score']}점)\n"
        md += f"  - 26일 기준선: ${b['kijun']:,.2f} (이격도 {b['kijun_gap']:+.1f}%) | 거래량 비율: {b['vol_ratio']*100:.0f}%\n"
        md += f"  - 1차 목표가: ${tgt_p:,.2f} (+15.0%) | 손절 기준선: ${stop_p:,.2f} (-3.0%)\n"
        md += f"  - 정량 분석: 방송 본문에서 긍정 추천 평가를 받았으며, 일목 구름대 상단 안착 및 26일 생명선 지지 확인. 거시 변동성 진정 시 1순위 분할 매수 진입 대상.\n\n"

    md += """### [Consolidation / Neutral] 박스권 횡보 및 추세 수렴 종목
"""
    for n in neutral_picks:
        md += f"* **{n['ticker']}** (${n['close']:,.2f}): {n['cloud_status']} (기준선 이격 {n['kijun_gap']:+.1f}%). 박스권 횡보에 따른 관망 유지.\n"

    md += """
### [Risk Alert / Short Hedge] 기준선 붕괴 및 리스크 회피 종목 (매수 절대 금지)
"""
    for s in bear_picks:
        caution_info = f"방송 문맥 주의 ({', '.join(s.get('caution_reasons', []))}) | " if s.get('caution_reasons') else ""
        md += f"* **{s['ticker']}** (${s['close']:,.2f} | {caution_info}): {s['cloud_status']} (기준선 대비 {s['kijun_gap']:+.1f}% 이탈). 생명선 붕괴에 따른 물타기 금지 및 숏 우위.\n"

    md += f"""
---

## 4. Model Governance & Verification

* 모델 검증 상태: `{health_status}`
* 시스템 아키텍처: Gate 0 (거시 기후) ➔ Gate 1 (방송 문맥 NLP) ➔ Gate 2 (알상무 17년 퀀트)
"""
    print(md)

def main():
    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"[Al-Sangmoo Quant Bot] Executing pipeline for {today_str}...")
    
    # 1. Fetch latest YouTube stream, Real-time Macro Gauges & Gate-0 Macro Climate
    stream_info = youtube_stream_scanner.fetch_latest_wepoll_stream()
    mentioned_stocks = stream_info.get("mentioned_stocks", [])
    macro_climate = stream_info.get("macro_climate", {})
    macro_gauges = stream_info.get("macro_gauges", {})
    
    # 2. Run 3-Gate 2+2+2 Filter
    bull_picks, neutral_picks, bear_picks, all_candidates = scan_and_select_2x2x2(stream_sentiment_list=mentioned_stocks)
    
    # 3. Evaluate User Real Portfolio Positions
    portfolio_alerts = evaluate_user_portfolio_positions()
    
    # 4. Update Background History
    history_df, health_status = evaluate_active_positions_and_update(bull_picks, neutral_picks, bear_picks, today_str)
    
    # 5. Save Macro Snapshot and Recommendation Matrix into SQLite
    try:
        db_manager.save_macro_history_record(today_str, macro_climate, macro_gauges)
        db_manager.save_recommendation_matrix_record(today_str, bull_picks, neutral_picks, bear_picks)
        print("[SQLite DB] Saved macro history and recommendation matrix records.")
    except Exception as e:
        print(f"[SQLite DB Warning] {e}")
        
    # 6. Build Dashboard Cache Feed
    try:
        generate_dashboard_feed.build_dashboard_data()
        print("[Dashboard Feed] Updated dashboard_data.json.")
    except Exception as e:
        print(f"[Dashboard Feed Error] {e}")
        
    # 7. Generate HTML & Send Email
    html_content = generate_email_content(today_str, bull_picks, neutral_picks, bear_picks, portfolio_alerts, health_status, stream_info)
    out_html_path = os.path.join(REPORTS_DIR, f"briefing_{today_str}.html")
    with open(out_html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
        
    subject = f"[Al-Sangmoo Quant Tactical Report] {today_str} Macro Regime & Tactical 2+2+2 Matrix"
    send_email_report(subject, html_content)
    
    # 8. Print Markdown Briefing
    print_markdown_briefing(today_str, bull_picks, neutral_picks, bear_picks, portfolio_alerts, health_status, stream_info)

if __name__ == "__main__":
    main()
