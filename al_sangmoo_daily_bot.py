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
        msg["From"] = f"R-Sangmoo Quant Engine <{gmail_user}>"
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

from al_sangmoo.core.constants import WATCHLIST, STOCK_DICT, TICKER_SECTORS, get_macro_tailwind_sectors
from al_sangmoo.domain.quant.ichimoku import (
    calculate_ichimoku_indicators,
    detect_cloud_trampoline_bounce,
    compute_institutional_flow_indicators
)
from al_sangmoo.domain.quant.scoring import (
    QuantIndicators,
    WeeklyTrendContext,
    InstitutionalFlowContext,
    TrampolineBounceContext,
    calculate_canonical_bull_score,
    calculate_canonical_sniper_score,
    calculate_canonical_bear_score,
    evaluate_quant_score,
    classify_quant_tier,
    classify_3tier_candidates
)
from al_sangmoo.domain.quant.macro import (
    evaluate_macro_stance,
    calculate_msi_regime
)

def scan_and_select_2x2x2(stream_sentiment_list=None):
    """
    3-Gate Quantitative Filtering & 3-Tier Recommendation Engine.
    SSOT Integration: Delegates indicator math to al_sangmoo.domain.quant.ichimoku
    and scoring to al_sangmoo.domain.quant.scoring.
    """
    if stream_sentiment_list is None:
        stream_sentiment_list = []
        
    stream_tickers = {item["ticker"] for item in stream_sentiment_list if isinstance(item, dict) and "ticker" in item}
    priority_tickers = [item["ticker"] for item in stream_sentiment_list if isinstance(item, dict) and "ticker" in item]
    
    scan_list = []
    for t in priority_tickers:
        if t not in scan_list:
            scan_list.append(t)
    for t in UNIVERSE:
        if t not in scan_list:
            scan_list.append(t)
            
    # 1. Macro Climate & Sector Tailwind Gate
    try:
        stream_info = youtube_stream_scanner.fetch_latest_wepoll_stream()
        macro_climate = stream_info.get("macro_climate", {})
    except Exception:
        macro_climate = evaluate_macro_stance()
        
    macro_stance = macro_climate.get("macro_stance", "DEFENSE_HOLD")
    tailwind_sectors = get_macro_tailwind_sectors(macro_stance)
    
    chart_data = {}
    for ticker in scan_list:
        try:
            df = yf.download(ticker, period="6mo", interval="1d", progress=False)
            if df.empty or len(df) < 55:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
            df = calculate_ichimoku_indicators(df)
            df_clean = df.dropna(subset=['Close', 'Kijun', 'Tenkan', 'SMA20', 'Vol_Ratio'])
            if df_clean.empty:
                continue
                
            last = df_clean.iloc[-1]
            close = float(last['Close'])
            kijun = float(last['Kijun'])
            tenkan = float(last['Tenkan'])
            vol_ratio = float(last['Vol_Ratio'])
            span_a = float(last['SpanA']) if not pd.isna(last['SpanA']) else close
            span_b = float(last['SpanB']) if not pd.isna(last['SpanB']) else close
            
            # Resample weekly for weekly trend alignment
            df_w = df[['Open', 'High', 'Low', 'Close', 'Volume']].resample('W-FRI').agg({
                'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
            }).dropna()
            if len(df_w) >= 26:
                df_w = calculate_ichimoku_indicators(df_w)
                w_clean = df_w.dropna(subset=['Close', 'Kijun', 'Tenkan'])
                if not w_clean.empty:
                    w_last = w_clean.iloc[-1]
                    w_close = float(w_last['Close'])
                    w_sp_a = float(w_last['SpanA']) if not pd.isna(w_last['SpanA']) else w_close
                    w_sp_b = float(w_last['SpanB']) if not pd.isna(w_last['SpanB']) else w_close
                    is_weekly_bull = (w_close >= max(w_sp_a, w_sp_b) * 0.98)
                else:
                    is_weekly_bull = True
            else:
                is_weekly_bull = (close >= max(span_a, span_b) * 0.97)
                
            tramp_detected, tramp_days, touch_gap, close_gap = detect_cloud_trampoline_bounce(df_clean)
            flow_data = compute_institutional_flow_indicators(df)
            
            q_eval = evaluate_quant_score(
                close=close,
                kijun=kijun,
                tenkan=tenkan,
                span_a=span_a,
                span_b=span_b,
                vol_ratio=vol_ratio,
                trampoline_detected=tramp_detected,
                is_weekly_bull=is_weekly_bull,
                days_ago=tramp_days
            )
            
            chart_data[ticker] = {
                "ticker": ticker,
                "price": close,
                "close": close,
                "latest_close": close,
                "kijun": kijun,
                "latest_kijun": kijun,
                "tenkan": tenkan,
                "latest_tenkan": tenkan,
                "span_a": span_a,
                "span_b": span_b,
                "vol_ratio": vol_ratio,
                "latest_vol_ratio": vol_ratio,
                "is_weekly_bull": is_weekly_bull,
                "trampoline_detected": tramp_detected,
                "trampoline_days_ago": tramp_days,
                "touch_gap_pct": touch_gap,
                "close_gap_pct": close_gap,
                "obv_status": flow_data["obv_status"],
                "obv_label": flow_data["obv_label"],
                "flow_ratio": flow_data["flow_ratio"],
                "flow_label": flow_data["flow_label"],
                "flow_score": flow_data["flow_score"],
                "is_stealth_accum": flow_data["is_stealth_accum"],
                "action": q_eval["action_directive"]
            }
        except Exception:
            continue
            
    tier1_picks, tier2_picks, tier3_picks = classify_3tier_candidates(
        chart_data=chart_data,
        tailwind_sectors=tailwind_sectors,
        stream_mentioned_tickers=stream_tickers
    )
    
    bull_picks = (tier1_picks + tier2_picks)[:2]
    neutral_picks = (tier2_picks + tier1_picks)[2:4]
    bear_picks = tier3_picks[:2]
    
    return bull_picks, neutral_picks, bear_picks, macro_climate

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
                df = calculate_ichimoku_indicators(df)
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

def generate_email_content(today_str, dual_consensus, strat1_exclusive, strat2_exclusive, portfolio_alerts, health_status, stream_info=None, feed_data=None):
    if stream_info is None:
        stream_info = {}
    if feed_data is None:
        feed_data = {}
        
    macro_info = feed_data.get("macro", {})
    macro_climate = macro_info.get("macro_climate", stream_info.get("macro_climate", {}))
    macro_gauges = macro_info.get("macro_gauges", stream_info.get("macro_gauges", {}))
    mentioned_stocks = stream_info.get("mentioned_stocks", [])
    
    stream_title = stream_info.get("title", "바이킹스 데일리 매크로 & 라이브 방송 분석")
    stream_url = stream_info.get("url", "https://www.youtube.com/@wepoll_original/streams")
    
    vix = macro_gauges.get("vix", {"val": 15.8, "delta": "+0.4%", "status": "NORMAL"})
    us10y = macro_gauges.get("us10y", {"val": 4.69, "delta": "+0.04%p", "status": "CRITICAL_BURDEN"})
    dxy = macro_gauges.get("dxy", {"val": 99.5, "delta": "+0.1%", "status": "NEUTRAL"})
    wti = macro_gauges.get("wti", {"val": 86.2, "delta": "-0.5%", "status": "INFLATION_SHOCK"})
    
    macro_stance = macro_climate.get("macro_stance", "DEFENSE_HOLD").replace('_', ' ')
    msi_score = macro_climate.get("msi_score", 66.9)
    macro_headline = macro_climate.get("macro_headline", "[거시 게이트 0단계: 거시 위험 지수 경보 / 신규 매수 보류 권고]")
    macro_directive = macro_climate.get("macro_action_directive", "거시 지표 및 방송 지침상 이번 주는 관망 주간입니다.")
    external_shocks = ", ".join(macro_climate.get("external_shocks", ["금리 경로 영향권", "인플레이션 변동성"]))
    
    rec_list = [f"{m['ticker']}(+{m['net_sentiment']} / 키워드: {', '.join(m.get('positive_reasons', []))})" for m in mentioned_stocks if m.get('host_intent') == 'BULLISH_RECOMMENDED']
    rec_summary_str = " | ".join(rec_list[:5]) if rec_list else "바이킹스 정규 방송 문맥 분석 완료"

    html = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background-color: #f1f5f9; color: #0f172a; margin: 0; padding: 24px; line-height: 1.5; }}
            .container {{ max-width: 740px; margin: 0 auto; background: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }}
            .header {{ background: #0f172a; color: #ffffff; padding: 24px 28px; border-bottom: 3px solid #38bdf8; }}
            .header h1 {{ margin: 0; font-size: 18px; font-weight: 800; letter-spacing: -0.02em; }}
            .header .meta {{ font-size: 12px; color: #94a3b8; margin-top: 6px; }}
            
            .macro-alert-bar {{ background: #fffbeb; border-left: 5px solid #d97706; padding: 14px 18px; font-size: 13px; color: #92400e; font-weight: 600; line-height: 1.5; }}
            .macro-gauges-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-top: 10px; }}
            .macro-gauge-box {{ background: #ffffff; border: 1px solid #e2e8f0; border-radius: 4px; padding: 8px 10px; font-size: 11px; }}
            .macro-gauge-title {{ color: #64748b; font-size: 10px; text-transform: uppercase; font-weight: 700; }}
            .macro-gauge-val {{ font-family: monospace; font-size: 13px; font-weight: 800; color: #0f172a; margin-top: 2px; }}
            
            .section {{ padding: 20px 28px; border-bottom: 1px solid #e2e8f0; }}
            .section-title {{ font-size: 13px; font-weight: 800; color: #1e293b; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 12px; padding-bottom: 6px; border-bottom: 2px solid #0f172a; }}
            .macro-box {{ background: #f8fafc; border: 1px solid #cbd5e1; border-left: 4px solid #334155; padding: 12px 16px; margin-bottom: 12px; }}
            .macro-row {{ font-size: 13px; margin-bottom: 6px; }}
            .macro-row strong {{ color: #0f172a; }}
            .table {{ width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 8px; }}
            .table th {{ background: #f1f5f9; color: #475569; font-weight: 700; text-align: left; padding: 8px 10px; border: 1px solid #e2e8f0; font-family: monospace; font-size: 11px; }}
            .table td {{ padding: 8px 10px; border: 1px solid #e2e8f0; color: #1e293b; }}
            
            .stock-card {{ background: #ffffff; border: 1px solid #cbd5e1; border-left: 4px solid #0f172a; padding: 12px 16px; margin-bottom: 10px; border-radius: 4px; }}
            .stock-card-dual {{ border-left-color: #f59e0b; background: #fffdf5; border-color: #fef08a; }}
            .stock-card-strat1 {{ border-left-color: #059669; }}
            .stock-card-strat2 {{ border-left-color: #dc2626; }}
            .stock-head {{ display: flex; justify-content: space-between; align-items: center; font-weight: 700; font-size: 14px; margin-bottom: 6px; }}
            .stock-meta {{ font-size: 12px; color: #475569; line-height: 1.6; }}
            .badge {{ display: inline-block; padding: 2px 6px; font-size: 10px; font-weight: 700; border-radius: 2px; font-family: monospace; }}
            .badge-dual {{ background: #fef08a; color: #854d0e; }}
            .badge-strat1 {{ background: #dcfce7; color: #166534; }}
            .badge-strat2 {{ background: #fee2e2; color: #991b1b; }}
            .badge-hold {{ background: #e0f2fe; color: #075985; }}
            .footer {{ padding: 16px 28px; font-size: 11px; color: #64748b; background: #f8fafc; text-align: center; border-top: 1px solid #e2e8f0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>R-SANGMOO QUANTITATIVE DAILY BRIEFING</h1>
                <div class="meta">발행일시: {today_str} 12:30 KST | 거시 위험 태세: <strong style="color:#fbbf24;">{macro_stance} (MSI {msi_score:.1f}pt)</strong> | 17년 퀀트 프레임워크</div>
            </div>
            
            <!-- Gate 0: Macro Climate & Weekly Directive -->
            <div class="macro-alert-bar">
                <div style="font-size:14px; font-weight:800; color:#b45309; margin-bottom:4px;">{macro_headline}</div>
                <div>{macro_directive}</div>
                
                <!-- Macro 4 Gauges -->
                <div class="macro-gauges-grid">
                    <div class="macro-gauge-box">
                        <div class="macro-gauge-title">10년물 국채금리</div>
                        <div class="macro-gauge-val">{us10y.get('val', 4.42)}% <span style="font-size:10px; color:#b45309;">({us10y.get('status', 'BURDEN')})</span></div>
                    </div>
                    <div class="macro-gauge-box">
                        <div class="macro-gauge-title">달러 인덱스</div>
                        <div class="macro-gauge-val">{dxy.get('val', 99.5)} <span style="font-size:10px; color:#64748b;">({dxy.get('status', 'NEUTRAL')})</span></div>
                    </div>
                    <div class="macro-gauge-box">
                        <div class="macro-gauge-title">VIX 공포지수</div>
                        <div class="macro-gauge-val">{vix.get('val', 15.8)} <span style="font-size:10px; color:#64748b;">({vix.get('status', 'NORMAL')})</span></div>
                    </div>
                    <div class="macro-gauge-box">
                        <div class="macro-gauge-title">WTI 국제유가</div>
                        <div class="macro-gauge-val">${wti.get('val', 78.5)} <span style="font-size:10px; color:#b45309;">({wti.get('status', 'STABLE')})</span></div>
                    </div>
                </div>
            </div>
            
            <!-- 1. Vikings Live Broadcast Context -->
            <div class="section">
                <div class="section-title">1. Vikings Live Broadcast & Macro Intelligence</div>
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
                            <th>TICKER</th>
                            <th>보유수량</th>
                            <th>매수가</th>
                            <th>현재가</th>
                            <th>수익률</th>
                            <th>목표가(+15%)</th>
                            <th>손절가(-4%)</th>
                            <th>실전 대응 지침</th>
                        </tr>
                    </thead>
                    <tbody>
        """
        for p in portfolio_alerts:
            pnl_color = "#059669" if p['pnl_pct'] > 0 else "#dc2626"
            badge_class = "badge-strat1" if "익절" in p['badge'] else ("badge-strat2" if "손절" in p['badge'] else "badge-hold")
            html += f"""
                        <tr>
                            <td><strong>{p['ticker']}</strong></td>
                            <td>{f"{p['quantity']:.4f}".rstrip('0').rstrip('.')}주</td>
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
                    웹 대시보드(http://localhost:8000)에서 포지션 진입 시 실시간 손절(-4%) 및 목표가(+15%) 추적 모니터링이 자동 개시됩니다.
                </div>
        """

    html += """
            </div>

            <!-- 3. Tactical 3-Column Quant Portfolio -->
            <div class="section">
                <div class="section-title">3. Tactical 3-Tier Institutional Quant Signals (거시 로테이션 & 기관 수급 잠행 매집 추천주)</div>
                <div style="font-size:11px; color:#475569; margin:-6px 0 12px 0; font-family:monospace; background:#f1f5f9; padding:6px 10px; border-radius:4px; border:1px solid #cbd5e1;">
                    • <strong>자산 배분 나침반:</strong> <span style="color:#b45309; font-weight:700;">[Tier 1 집중형 60~70% CORE]</span> | <span style="color:#059669; font-weight:700;">[Tier 2 안정형 20% BASE]</span> | <span style="color:#dc2626; font-weight:700;">[Tier 3 스나이퍼 10% TACTICAL]</span>
                </div>
    """
    
    # Helper for item badges
    def format_item_badges(item):
        badges_str = ""
        # Sector badge
        if item.get("sector"):
            badges_str += f""" &nbsp;<span style="background:#f1f5f9; color:#475569; border:1px solid #94a3b8; padding:1px 5px; font-size:10px; font-weight:700; font-family:monospace; border-radius:3px;">[{item['sector']}]</span>"""
        # OBV Status
        if item.get("obv_status") == "STEALTH_ACCUM":
            badges_str += f""" &nbsp;<span style="background:#dcfce7; color:#15803d; border:1px solid #16a34a; padding:1px 5px; font-size:10px; font-weight:700; font-family:monospace; border-radius:3px;">[OBV: STEALTH ACCUM]</span>"""
        elif item.get("obv_status") == "BULL_FLOW":
            badges_str += f""" &nbsp;<span style="background:#e0f2fe; color:#0369a1; border:1px solid #0284c7; padding:1px 5px; font-size:10px; font-weight:700; font-family:monospace; border-radius:3px;">[OBV: INFLOW]</span>"""
        # Flow Ratio
        if item.get("flow_ratio") and float(item.get("flow_ratio", 1.0)) >= 1.3:
            badges_str += f""" &nbsp;<span style="background:#fef3c7; color:#92400e; border:1px solid #d97706; padding:1px 5px; font-size:10px; font-weight:700; font-family:monospace; border-radius:3px;">[FLOW: {item['flow_ratio']:.1f}x]</span>"""
        # Streak badge
        if item.get("streak_days", 1) >= 2:
            badges_str += f""" &nbsp;<span style="background:#fef3c7; color:#92400e; border:1px solid #d97706; padding:1px 5px; font-size:10px; font-weight:700; font-family:monospace; border-radius:3px;">[{item['streak_days']}D STREAK]</span>"""
        # In Wallet badge
        if item.get("in_wallet"):
            pnl = item.get("holding_pnl", 0.0)
            pnl_sign = "+" if pnl >= 0 else ""
            badges_str += f""" &nbsp;<span style="background:#e0f2fe; color:#0369a1; border:1px solid #0284c7; padding:1px 5px; font-size:10px; font-weight:700; font-family:monospace; border-radius:3px;">[IN WALLET: {pnl_sign}{pnl:.1f}%]</span>"""
        return badges_str

    # 3.1. Tier 1: Macro Leader & Smart Money Accumulation
    if dual_consensus:
        html += """
                <div style="font-size:13px; font-weight:800; color:#b45309; margin-bottom:8px; display:flex; align-items:center; gap:6px;">
                    <span>[TIER 1 최우선 주도주] MACRO & SMART MONEY ACCUMULATION (거시 순풍 + 기관 잠행 매집 4선)</span>
                </div>
        """
        for d in dual_consensus:
            ext_badges = format_item_badges(d)
            wallet_style = "border-left: 4px solid #0284c7; background: #f0f9ff;" if d.get("in_wallet") else ""
            html += f"""
                <div class="stock-card stock-card-dual" style="{wallet_style}">
                    <div class="stock-head">
                        <span><strong>{d['ticker']}</strong> &nbsp;<span style="font-size:12px; color:#64748b;">{d.get('name','')}</span> &nbsp;<span class="badge badge-dual">TIER 1 주도주 {d['score']}점</span>{ext_badges}</span>
                        <span style="font-family:monospace; font-weight:800;">${d['price']:,.2f}</span>
                    </div>
                    <div class="stock-meta">
                        • <strong>26일 기준선 이격:</strong> {d['kijun_gap']:+.2f}% | <strong>20일 거래량 비율:</strong> {d['vol_ratio']}% (수급 마름 확인)<br>
                        • <strong>1차 목표가(+15%):</strong> <span style="color:#059669; font-weight:700;">${d['target_price']:,.2f}</span> | <strong>칼손절 기준선(-4%):</strong> <span style="color:#dc2626; font-weight:700;">${d['stop_price']:,.2f}</span><br>
                        • <strong>기관 퀀트 분석:</strong> 거시 순풍 섹터 부합 및 26일 기준선 안전 지지 구역 내 기관 잠행 매집(OBV/양봉수급) 확인 완료. 최우선 집중 공략 대상.
                    </div>
                </div>
            """

    # 3.2. Tier 2: Structural 26D Pullback
    if strat1_exclusive:
        html += """
                <div style="font-size:13px; font-weight:800; color:#059669; margin:16px 0 8px 0;">
                    [TIER 2 정석 안정주] STRUCTURAL 26D PULLBACK (26일 기준선 지지 1차 분할 매수 적합주)
                </div>
        """
        for p in strat1_exclusive:
            ext_badges = format_item_badges(p)
            wallet_style = "border-left: 4px solid #0284c7; background: #f0f9ff;" if p.get("in_wallet") else ""
            html += f"""
                <div class="stock-card stock-card-strat1" style="{wallet_style}">
                    <div class="stock-head">
                        <span><strong>{p['ticker']}</strong> &nbsp;<span style="font-size:12px; color:#64748b;">{p.get('name','')}</span> &nbsp;<span class="badge badge-strat1">TIER 2 적합도 {p['score']}점</span>{ext_badges}</span>
                        <span style="font-family:monospace; font-weight:800;">${p['price']:,.2f}</span>
                    </div>
                    <div class="stock-meta">
                        • <strong>26일 기준선 이격:</strong> {p['kijun_gap']:+.2f}% | <strong>20일 거래량 비율:</strong> {p['vol_ratio']}%<br>
                        • <strong>1차 목표가(+15%):</strong> <span style="color:#059669; font-weight:700;">${p['target_price']:,.2f}</span> | <strong>손절 기준선(-4%):</strong> <span style="color:#dc2626; font-weight:700;">${p['stop_price']:,.2f}</span><br>
                        • <strong>기관 퀀트 분석:</strong> 주봉 대세 상승 안착 및 26일 기준선 생명선 지지 확인. 거시 변동성 진정 시 1차 분할 매수 진입 대상.
                    </div>
                </div>
            """

    # 3.3. Tier 3: Cloud Bounce Sniper Radar
    if strat2_exclusive:
        html += """
                <div style="font-size:13px; font-weight:800; color:#dc2626; margin:16px 0 8px 0;">
                    [TIER 3 스나이퍼] CLOUD BOUNCE SNIPER RADAR (일목 구름대 지지 도약 2단계 발사대 모멘텀주)
                </div>
        """
        for s in strat2_exclusive:
            ext_badges = format_item_badges(s)
            wallet_style = "border-left: 4px solid #0284c7; background: #f0f9ff;" if s.get("in_wallet") else ""
            html += f"""
                <div class="stock-card stock-card-strat2" style="{wallet_style}">
                    <div class="stock-head">
                        <span><strong>{s['ticker']}</strong> &nbsp;<span style="font-size:12px; color:#64748b;">{s.get('name','')}</span> &nbsp;<span class="badge badge-strat2">TIER 3 스나이퍼 {s['score']}점</span>{ext_badges}</span>
                        <span style="font-family:monospace; font-weight:800;">${s['price']:,.2f}</span>
                    </div>
                    <div class="stock-meta">
                        • <strong>26일 기준선 이격:</strong> {s['kijun_gap']:+.2f}% | <strong>20일 거래량 비율:</strong> {s['vol_ratio']}%<br>
                        • <strong>1차 목표가(+15%):</strong> <span style="color:#059669; font-weight:700;">${s['target_price']:,.2f}</span> | <strong>손절 기준선(-4%):</strong> <span style="color:#dc2626; font-weight:700;">${s['stop_price']:,.2f}</span><br>
                        • <strong>기관 퀀트 분석:</strong> 일목 구름대 하단 트램펄린 반등 완료 후 상방 탄력 가속 구간. 단기 스윙 공략 대상.
                    </div>
                </div>
            """

    html += f"""
            </div>

            <!-- 4. Model Governance & Verification -->
            <div class="section">
                <div class="section-title">4. Model Governance & Verification</div>
                <div style="font-size:12px; color:#475569;">
                    • <strong>전수 포워드 트래킹 상태:</strong> {health_status}<br>
                    • <strong>시스템 아키텍처:</strong> Gate 0 (거시 기후) ➔ Gate 1 (방송 문맥 NLP) ➔ Gate 2 (R상무 17년 퀀트: 주봉 대세 + 일봉 3단 그리드)<br>
                    • <strong>웹 대시보드 링크:</strong> <a href="http://localhost:8000" target="_blank" style="color:#0284c7; font-weight:700; text-decoration:underline;">http://localhost:8000 (R상무 퀀트 통합 대시보드)</a>
                </div>
            </div>
            
            <div class="footer">
                R-Sangmoo Quantitative Risk Engine • Confidential Portfolio Report • Generated Daily at 12:30 KST
            </div>
        </div>
    </body>
    </html>
    """
    return html

def print_markdown_briefing(today_str, dual_consensus, strat1_exclusive, strat2_exclusive, portfolio_alerts, health_status, stream_info=None):
    if stream_info is None:
        stream_info = {}
        
    stream_title = stream_info.get("title", "바이킹스 데일리 매크로 & 라이브 방송 분석")
    stream_url = stream_info.get("url", "https://www.youtube.com/@wepoll_original/streams")
    macro_climate = stream_info.get("macro_climate", {})
    macro_gauges = stream_info.get("macro_gauges", {})
    
    vix = macro_gauges.get("vix", {"val": 15.8, "status": "NORMAL"})
    us10y = macro_gauges.get("us10y", {"val": 4.42, "status": "BURDEN"})
    wti = macro_gauges.get("wti", {"val": 78.5, "status": "STABLE"})
    
    macro_headline = macro_climate.get("macro_headline", "[거시 게이트 0단계: 이번 주 신규 매수 보류 / 관망·현금 유지 권고]")
    macro_directive = macro_climate.get("macro_action_directive", "거시 지표 및 방송 지침상 이번 주는 관망 주간입니다.")

    md = f"""# R-SANGMOO QUANTITATIVE TACTICAL REPORT ({today_str})

발행일시: {today_str} 12:30 KST | 3단계 게이트 의사결정 파이프라인 (거시 기후 ➔ 문맥 NLP ➔ 17년 퀀트)

---

## 0. Gate-0 Macro Climate & Weekly Directive

* **거시 총평**: {macro_headline}
* **실전 거시 지침**: {macro_directive}
* **실시간 거시 지표**: VIX `{vix.get('val', 15.8)}` ({vix.get('status', 'NORMAL')}) | 미국채 10년물 `{us10y.get('val', 4.42)}%` ({us10y.get('status', 'BURDEN')}) | WTI 유가 `${wti.get('val', 78.5)}` ({wti.get('status', 'STABLE')})

---

## 1. Vikings Live Broadcast Context & Macro Flow

* **라이브 방송**: [{stream_title}]({stream_url})

---

## 2. Real Portfolio Risk & Execution Monitor

"""
    if portfolio_alerts:
        for p in portfolio_alerts:
            qty_str = f"{p['quantity']:.4f}".rstrip('0').rstrip('.')
            md += f"* **{p['ticker']}** ({qty_str}주) [{p['badge']}] (수익률 {p['pnl_pct']:+.2f}%)\n"
            md += f"  - 매수가: ${p['buy_price']:,.2f} | 현재가: ${p['cur_price']:,.2f}\n"
            md += f"  - 목표가(+15%): ${p['target_price']:,.2f} | 손절가(-4%): ${p['stop_loss_price']:,.2f}\n"
            md += f"  - 대응 지침: {p['advice']}\n\n"
    else:
        md += "*[실계좌 보유 현황: 0 종목] 현재 실제 포트폴리오에 등록된 보유 종목이 없습니다.*\n\n"

    md += """---

## 3. Tactical 3-Column Quant Recommendations

### [5-Star Alpha ∩] Dual Consensus Alpha (황금 교집합 100점 만점)
"""
    for d in dual_consensus:
        md += f"* **{d['ticker']}** (${d['price']:,.2f} | 100점 만점) | 26일선 이격: {d['kijun_gap']:+.2f}% | TP(+15%): ${d['target_price']:,.2f} | SL(-4%): ${d['stop_price']:,.2f}\n"

    md += """
### [Strategy I] Primary Accumulation (기준선 눌림목 1차 분할 매수)
"""
    for p in strat1_exclusive:
        md += f"* **{p['ticker']}** (${p['price']:,.2f} | {p['score']}점) | 26일선 이격: {p['kijun_gap']:+.2f}% | TP(+15%): ${p['target_price']:,.2f} | SL(-4%): ${p['stop_price']:,.2f}\n"

    md += """
### [Strategy II] Cloud Bounce Sniper Radar (구름대 도약 2단계 발사대)
"""
    for s in strat2_exclusive:
        md += f"* **{s['ticker']}** (${s['price']:,.2f} | {s['score']}점) | 26일선 이격: {s['kijun_gap']:+.2f}% | TP(+15%): ${s['target_price']:,.2f} | SL(-4%): ${s['stop_price']:,.2f}\n"

    md += f"""
---

## 4. Model Governance & Verification

* 모델 검증 상태: `{health_status}`
* 시스템 아키텍처: Gate 0 (거시 기후) ➔ Gate 1 (방송 문맥 NLP) ➔ Gate 2 (R상무 17년 퀀트: 주봉 대세 + 일봉 3단 그리드)
"""
    print(md)

def main():
    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"[R-Sangmoo Quant Bot] Executing pipeline for {today_str}...")
    
    # 1. Fetch latest YouTube stream, Real-time Macro Gauges & Gate-0 Macro Climate
    stream_info = youtube_stream_scanner.fetch_latest_wepoll_stream()
    macro_climate = stream_info.get("macro_climate", {})
    macro_gauges = stream_info.get("macro_gauges", {})
    
    # 2. Build Dashboard Cache Feed & 3-Column Tactical Quant Portfolio
    try:
        feed_data = generate_dashboard_feed.build_dashboard_data()
        print("[Dashboard Feed] Updated dashboard_data.json.")
    except Exception as e:
        print(f"[Dashboard Feed Error] {e}")
        feed_data = {}
        
    dual_consensus = feed_data.get("dual_consensus", [])
    strat1_exclusive = feed_data.get("strat1_exclusive", [])
    strat2_exclusive = feed_data.get("strat2_exclusive", [])
    
    # 3. Evaluate User Real Portfolio Positions
    portfolio_alerts = evaluate_user_portfolio_positions()
    
    # 4. Update Background History
    bull_picks = (dual_consensus + strat1_exclusive)[:2]
    neutral_picks = (strat1_exclusive + dual_consensus)[2:4]
    bear_picks = strat2_exclusive[:2]
    history_df, health_status = evaluate_active_positions_and_update(bull_picks, neutral_picks, bear_picks, today_str)
    
    # 5. Save Macro Snapshot, Recommendation Matrix, and Archive Daily Recommendations into SQLite
    try:
        db_manager.save_macro_history_record(today_str, macro_climate, macro_gauges)
        db_manager.save_recommendation_matrix_record(today_str, bull_picks, neutral_picks, bear_picks)
        db_manager.archive_daily_recommendations(today_str, dual_consensus, strat1_exclusive, strat2_exclusive)
        print("[SQLite DB] Saved macro history, recommendation matrix, and daily recommendation archive.")
    except Exception as e:
        print(f"[SQLite DB Warning] {e}")
        
    # 6. Generate Modern Responsive HTML & Send Email
    html_content = generate_email_content(today_str, dual_consensus, strat1_exclusive, strat2_exclusive, portfolio_alerts, health_status, stream_info, feed_data)
    out_html_path = os.path.join(REPORTS_DIR, f"briefing_{today_str}.html")
    with open(out_html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
        
    subject = f"[R-Sangmoo Quant Report] {today_str} Tactical 3-Column Quant & Macro Briefing"
    send_email_report(subject, html_content)
    
    # 7. Print Markdown Briefing
    print_markdown_briefing(today_str, dual_consensus, strat1_exclusive, strat2_exclusive, portfolio_alerts, health_status, stream_info)

if __name__ == "__main__":
    main()
