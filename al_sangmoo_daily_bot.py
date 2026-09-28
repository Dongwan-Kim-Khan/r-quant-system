import os
import sys
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf
import youtube_stream_scanner
import generate_dashboard_feed
from al_sangmoo.infrastructure.persistence import (
    archive_daily_recommendations,
    get_connection,
    get_live_portfolio,
    init_db,
    save_macro_history_record,
    save_recommendation_matrix_record,
)
from al_sangmoo.core.constants import DEFAULT_BASE_ACCOUNT_USD

# Windows cp949 terminal encoding fix
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, "daily_reports")
DASHBOARD_JSON = os.path.join(BASE_DIR, "dashboard_data.json")
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

from al_sangmoo.core.constants import (
    WATCHLIST, STOCK_DICT, TICKER_SECTORS, get_active_watchlist, get_macro_tailwind_sectors,
    HARD_STOP_PCT, TRAILING_ACTIVATE_PCT, CASH_PROXY_TICKER,
    derive_partial_tp_price, derive_stop_price, derive_target_price,
)
from al_sangmoo.domain.risk.position_sizer import calculate_target_shares
from al_sangmoo.domain.risk.cash_proxy import satellite_holdings, proxy_holdings

# Global 60 Universe SSOT (Dynamic Sector-Weighted)
UNIVERSE = get_active_watchlist()
from al_sangmoo.domain.quant.ichimoku import (
    calculate_ichimoku_indicators,
    detect_cloud_trampoline_bounce,
    compute_institutional_flow_indicators
)
from al_sangmoo.domain.risk.trailing_stop import compute_trailing_floor
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
    calculate_msi_regime,
    extract_msi_score,
    resolve_capital_regime,
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
        macro_climate = stream_info.get("macro_climate", {}) or {}
        macro_gauges = stream_info.get("macro_gauges", {}) or {}
    except Exception:
        macro_climate = evaluate_macro_stance() or {}
        macro_gauges = {}

    msi_score = extract_msi_score(macro_climate, default=50.0)
    us10y_val = float(macro_gauges.get("us10y", {}).get("val", 4.4)) if isinstance(macro_gauges.get("us10y"), dict) else 4.4
    wti_val = float(macro_gauges.get("wti", {}).get("val", 78.0)) if isinstance(macro_gauges.get("wti"), dict) else 78.0
    vix_val = float(macro_gauges.get("vix", {}).get("val", 16.0)) if isinstance(macro_gauges.get("vix"), dict) else 16.0
    tailwind_sectors = get_macro_tailwind_sectors(msi_score, us10y_val, wti_val, vix_val)
    
    chart_data = {}
    for ticker in scan_list:
        try:
            df = yf.download(ticker, period="3y", interval="1d", progress=False)
            if df.empty or len(df) < 250:
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
    
    # v2 3-Gate: Tier-1 leaders, Tier-2 pullbacks. Snipers are not bear picks.
    bull_picks = tier1_picks[:2]
    neutral_picks = tier2_picks[:2]
    bear_picks = []
    
    return bull_picks, neutral_picks, bear_picks, macro_climate

def evaluate_user_portfolio_positions():
    portfolio_data = get_live_portfolio()
    holdings = portfolio_data.get("holdings", [])
    
    portfolio_alerts = []
    for h in holdings:
        ticker = h['ticker']
        buy_price = float(h['buy_price'])
        cur_price = float(h['current_price'])
        pnl_pct = float(h['pnl_pct'])
        qty = float(h['quantity'])
        target_p = float(h.get('target_price', derive_target_price(buy_price)))
        stop_p = float(h.get('stop_loss_price', derive_stop_price(buy_price)))
        
        is_stop_loss = cur_price <= stop_p or pnl_pct <= -HARD_STOP_PCT
        is_trailing = pnl_pct >= TRAILING_ACTIVATE_PCT or float(h.get("max_gain_pct") or 0.0) >= TRAILING_ACTIVATE_PCT
        trailing_floor = float(h.get("trailing_floor") or 0.0)
        
        if is_stop_loss:
            badge = "칼손절 긴급 매도"
            advice = f"손절 기준선(-{HARD_STOP_PCT:.0f}%) 이탈에 따른 전량 리스크 청산 권고 (손실률 {pnl_pct:+.2f}%)"
        elif is_trailing:
            badge = "무제한 트레일링"
            floor_txt = f" floor ${trailing_floor:,.2f}" if trailing_floor > 0 else ""
            advice = f"+{TRAILING_ACTIVATE_PCT:.0f}% 고점 돌파 후 무제한 트레일링 홀딩 (Let Winners Run,{floor_txt} 수익률 {pnl_pct:+.2f}%)"
        else:
            badge = "보유 지속"
            advice = f"26일 기준선 지지 유효. 손절선 ${stop_p:,.2f} 유지 / +{TRAILING_ACTIVATE_PCT:.0f}% 이후 트레일링 익절"
            
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
    init_db()
    try:
        with get_connection() as conn:
            history_df = pd.read_sql_query("SELECT * FROM trades", conn)
    except Exception:
        history_df = pd.DataFrame()
    if history_df.empty:
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
                
                is_stop_loss = pnl_pct <= -HARD_STOP_PCT or (pos_type == 'BULL' and cur_price < kijun and new_max < TRAILING_ACTIVATE_PCT)
                atr_14 = float(last['ATR14']) if 'ATR14' in last.index and pd.notna(last['ATR14']) else 0.0
                peak_high = entry_price * (1.0 + new_max / 100.0)
                trail_floor = compute_trailing_floor(kijun, peak_high, atr_14) if new_max >= TRAILING_ACTIVATE_PCT else 0.0
                is_trailing_exit = pos_type == 'BULL' and new_max >= TRAILING_ACTIVATE_PCT and trail_floor > 0 and cur_price < trail_floor
                is_expired = days_active >= 65
                
                if is_stop_loss:
                    status = 'CLOSED_STOP'
                    advice = f"손절선(-{HARD_STOP_PCT:.0f}%) 또는 기준선 이탈 ({pnl_pct:.1f}%)"
                elif is_trailing_exit:
                    status = 'CLOSED_PROFIT'
                    advice = f"무제한 트레일링 익절 (floor ${trail_floor:,.2f}, +{pnl_pct:.1f}%)"
                elif new_max >= TRAILING_ACTIVATE_PCT:
                    status = 'OPEN'
                    advice = f"트레일링 홀딩 (Let Winners Run, floor ${trail_floor:,.2f}, +{pnl_pct:.1f}%)"
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
            b_p = float(b.get('price') or b.get('close') or 100.0)
            new_rows.append({"date": today_str, "ticker": b['ticker'], "type": "BULL", "entry_price": b_p, "current_price": b_p, "pnl_pct": 0.0, "max_gain_pct": 0.0, "status": "OPEN", "days_active": 0, "exit_advice": f"신규 진입 (목표가 +{TRAILING_ACTIVATE_PCT:.0f}%, 손절가 -{HARD_STOP_PCT:.0f}%)"})
        for n in neutral_picks:
            n_p = float(n.get('price') or n.get('close') or 100.0)
            new_rows.append({"date": today_str, "ticker": n['ticker'], "type": "NEUTRAL", "entry_price": n_p, "current_price": n_p, "pnl_pct": 0.0, "max_gain_pct": 0.0, "status": "OPEN", "days_active": 0, "exit_advice": "중립 관망"})
        for s in bear_picks:
            s_p = float(s.get('price') or s.get('close') or 100.0)
            new_rows.append({"date": today_str, "ticker": s['ticker'], "type": "BEAR", "entry_price": s_p, "current_price": s_p, "pnl_pct": 0.0, "max_gain_pct": 0.0, "status": "OPEN", "days_active": 0, "exit_advice": "리스크 회피 / 숏"})
            
        if new_rows:
            history_df = pd.concat([history_df, pd.DataFrame(new_rows)], ignore_index=True)

    # SQLite sync
    try:
        init_db()
        with get_connection() as conn:
            cursor = conn.cursor()
            for idx, row in history_df.iterrows():
                e_price = float(row['entry_price'])
                tgt_p = derive_target_price(e_price)
                stop_p = derive_stop_price(e_price)
                part_p = derive_partial_tp_price(e_price)
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
        
    portfolio = feed_data.get("portfolio", {})
    conviction = feed_data.get("conviction", {})
    macro_info = feed_data.get("macro", {})
    macro_climate = macro_info.get("macro_climate", stream_info.get("macro_climate", {}))
    macro_gauges = macro_info.get("macro_gauges", stream_info.get("macro_gauges", {}))
    
    # 1. Financial & Portfolio Metrics
    holdings = portfolio.get("holdings", [])
    tot_equity_usd = float(portfolio.get("total_equity_usd", DEFAULT_BASE_ACCOUNT_USD))
    tot_equity_krw = int(portfolio.get("total_equity_krw", 10_000_000))
    tot_invested = float(portfolio.get("total_invested", 0.0))
    overall_pnl_pct = float(portfolio.get("overall_pnl_pct", 0.0) or portfolio.get("unrealized_pnl_pct", 0.0))
    overall_pnl_amt = float(portfolio.get("overall_pnl_amount", 0.0) or portfolio.get("unrealized_pnl_amount", 0.0))
    free_cash_usd = float(portfolio.get("free_cash_usd", tot_equity_usd - tot_invested))
    cash_ratio_pct = float(portfolio.get("cash_ratio_pct", 50.0))
    active_slots = int(portfolio.get("active_slot_count", len(holdings)))
    
    pnl_sign = "+" if overall_pnl_pct >= 0 else ""
    pnl_color = "#10b981" if overall_pnl_pct >= 0 else "#ef4444"
    pnl_bg = "rgba(16, 185, 129, 0.12)" if overall_pnl_pct >= 0 else "rgba(239, 68, 68, 0.12)"

    # 2. Macro & Regime Stance (MSI = risk index; slots = SPY 200 SMA / SSOT fallback)
    msi_score = extract_msi_score(macro_info)
    slot_summary = (feed_data or {}).get("slot_allocation_summary") or {}
    if "is_bull_regime" in macro_info:
        is_bull = bool(macro_info["is_bull_regime"])
    elif "is_bull_regime" in slot_summary:
        is_bull = bool(slot_summary["is_bull_regime"])
    else:
        is_bull = bool(resolve_capital_regime(msi_score=msi_score, fetch_spy=False)["is_bull_regime"])
    max_slots = 3 if is_bull else 2
    regime_title = "BULL REGIME (강세 국면)" if is_bull else "BEAR REGIME (약세 방어 국면)"
    regime_desc = "3-Slot 34/33/33 + QQQ Cash Proxy / 1.5x 레버리지 조건부" if is_bull else "2-Slot 25/25 + QQQ 코어 (레버리지 OFF)"
    regime_badge_color = "#10b981" if is_bull else "#ef4444"
    regime_badge_bg = "rgba(16, 185, 129, 0.15)" if is_bull else "rgba(239, 68, 68, 0.15)"
    
    macro_headline = macro_climate.get("macro_headline", f"[{regime_title}: MSI {msi_score:.1f}pt - {regime_desc}]")
    macro_directive = macro_climate.get("macro_action_directive", "C-2 3-Slot 컨빅션 + QQQ Cash Proxy 자동 자금 관리 가동 중")
    tailwind_sectors = ", ".join(macro_info.get("tailwind_sectors", ["에너지", "반도체"]))
    
    vix = macro_gauges.get("vix", {"val": 15.8, "status": "NORMAL"})
    us10y = macro_gauges.get("us10y", {"val": 4.42, "status": "BURDEN"})
    dxy = macro_gauges.get("dxy", {"val": 99.5, "status": "NEUTRAL"})
    wti = macro_gauges.get("wti", {"val": 78.5, "status": "STABLE"})

    # 3. Top Conviction Picks
    top_pick = conviction.get("top_pick")
    runner_up = conviction.get("runner_up")
    ranked_candidates = conviction.get("ranked_candidates", [])
    
    if not top_pick and dual_consensus:
        d = dual_consensus[0]
        p_val = float(d.get("price", 100.0))
        eq = float(portfolio.get("total_equity_usd") or portfolio.get("total_value") or DEFAULT_BASE_ACCOUNT_USD)
        sz1 = calculate_target_shares(eq, p_val, slot_rank=1, is_bull=is_bull)
        top_pick = {
            "ticker": d["ticker"], "name": d.get("name", d["ticker"]),
            "sector": d.get("sector", "주도 섹터"), "conviction_score": float(d.get("score", 95.0)),
            "rs_3m": float(d.get("rs_3m", 15.0)), "price": p_val,
            "target_price": derive_target_price(p_val), "stop_price": derive_stop_price(p_val),
            "sizing": {
                "shares": sz1["shares"],
                "allocated_usd": sz1["allocated_usd"],
                "weight_pct": round(float(sz1.get("slot_weight") or 0.34) * 100, 1),
                "slot_weight": sz1.get("slot_weight"),
                "eligible": sz1.get("eligible", True),
                "is_bull_regime": is_bull,
            },
            "rationale": "26일 기준선 생명선 지지 및 기관 스마트머니 잠행 매집(OBV) 확인. 최우선 집중 진입 대상 (C-2 Slot#1 34%)."
        }
    if not runner_up and len(dual_consensus) > 1:
        d2 = dual_consensus[1]
        p_val2 = float(d2.get("price", 100.0))
        eq = float(portfolio.get("total_equity_usd") or portfolio.get("total_value") or DEFAULT_BASE_ACCOUNT_USD)
        sz2 = calculate_target_shares(eq, p_val2, slot_rank=2, is_bull=is_bull)
        runner_up = {
            "ticker": d2["ticker"], "name": d2.get("name", d2["ticker"]),
            "sector": d2.get("sector", "주도 섹터"), "conviction_score": float(d2.get("score", 90.0)),
            "rs_3m": float(d2.get("rs_3m", 12.0)), "price": p_val2,
            "target_price": derive_target_price(p_val2), "stop_price": derive_stop_price(p_val2),
            "sizing": {
                "shares": sz2["shares"],
                "allocated_usd": sz2["allocated_usd"],
                "weight_pct": round(float(sz2.get("slot_weight") or 0.33) * 100, 1),
                "slot_weight": sz2.get("slot_weight"),
                "eligible": sz2.get("eligible", True),
                "is_bull_regime": is_bull,
            },
            "rationale": "주봉 대세 상승 안착 및 주도 섹터 모멘텀 후속 주자 (C-2 Slot#2 33%)."
        }

    # Build HTML
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>알상무 퀀트 데일리 브리핑 ({today_str})</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0b0f19; color: #e2e8f0; margin: 0; padding: 16px; line-height: 1.5; }}
        .container {{ max-width: 760px; margin: 0 auto; background: #111827; border: 1px solid #1f2937; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }}
        .header {{ background: #0f172a; padding: 24px 28px; border-bottom: 2px solid #38bdf8; }}
        .header-title {{ font-size: 18px; font-weight: 800; color: #ffffff; letter-spacing: -0.02em; margin: 0; }}
        .header-meta {{ font-size: 12px; color: #94a3b8; margin-top: 6px; font-family: monospace; }}
        
        .kpi-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; padding: 18px 24px; background: #131d31; border-bottom: 1px solid #1f2937; }}
        .kpi-card {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 12px 14px; }}
        .kpi-label {{ font-size: 10.5px; font-weight: 700; color: #94a3b8; text-transform: uppercase; }}
        .kpi-val {{ font-size: 15px; font-weight: 800; color: #ffffff; margin-top: 4px; font-family: monospace; }}
        
        .section {{ padding: 22px 28px; border-bottom: 1px solid #1f2937; }}
        .section-header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }}
        .section-title {{ font-size: 13px; font-weight: 800; color: #38bdf8; text-transform: uppercase; letter-spacing: 0.05em; display: flex; align-items: center; gap: 8px; }}
        
        .table {{ width: 100%; border-collapse: collapse; font-size: 11.5px; }}
        .table th {{ background: #1e293b; color: #94a3b8; font-weight: 700; text-align: left; padding: 9px 12px; border-bottom: 1px solid #334155; font-family: monospace; font-size: 10.5px; }}
        .table td {{ padding: 10px 12px; border-bottom: 1px solid #1f2937; color: #e2e8f0; vertical-align: middle; }}
        
        .badge {{ display: inline-block; padding: 2px 7px; font-size: 10px; font-weight: 800; border-radius: 4px; font-family: monospace; }}
        
        .macro-box {{ background: #1e293b; border: 1px solid #334155; border-left: 4px solid {regime_badge_color}; border-radius: 8px; padding: 14px 18px; margin-bottom: 14px; }}
        .macro-gauges {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-top: 12px; }}
        .gauge-item {{ background: #0f172a; border: 1px solid #334155; border-radius: 6px; padding: 8px 10px; }}
        .gauge-label {{ font-size: 10px; font-weight: 700; color: #94a3b8; }}
        .gauge-val {{ font-size: 12.5px; font-weight: 800; color: #ffffff; font-family: monospace; margin-top: 2px; }}
        
        .pick-card {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 16px 18px; margin-bottom: 12px; }}
        .pick-card-top {{ border-left: 4px solid #f59e0b; background: linear-gradient(135deg, rgba(245, 158, 11, 0.06) 0%, rgba(30, 41, 59, 1) 100%); }}
        .pick-card-runner {{ border-left: 4px solid #38bdf8; background: linear-gradient(135deg, rgba(56, 189, 248, 0.06) 0%, rgba(30, 41, 59, 1) 100%); }}
        .pick-head {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
        .pick-title {{ font-size: 14px; font-weight: 800; color: #ffffff; font-family: monospace; }}
        .pick-body {{ font-size: 12px; color: #cbd5e1; line-height: 1.6; }}
        
        .footer {{ padding: 20px 28px; font-size: 11px; color: #64748b; background: #0f172a; text-align: center; line-height: 1.6; }}
        
        @media only screen and (max-width: 600px) {{
            body {{ padding: 6px; }}
            .kpi-grid, .macro-gauges {{ grid-template-columns: repeat(2, 1fr); }}
            .section {{ padding: 16px 16px; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <div class="header">
            <h1 class="header-title">AL-SANGMOO QUANTITATIVE DAILY COCKPIT</h1>
            <div class="header-meta">발행일시: {today_str} 12:30 KST | 17년 기관 퀀트 프레임워크 | Global 60 Universe</div>
        </div>

        <!-- 4 KPI Cockpit Cards -->
        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-label">내 계좌 총 평가액</div>
                <div class="kpi-val">${tot_equity_usd:,.2f}</div>
                <div style="font-size:10px; color:#94a3b8; font-family:monospace; margin-top:2px;">약 ₩{tot_equity_krw:,.0f}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">계좌 전체 수익률</div>
                <div class="kpi-val" style="color:{pnl_color};">{pnl_sign}{overall_pnl_pct:.2f}%</div>
                <div style="font-size:10px; color:{pnl_color}; font-family:monospace; margin-top:2px;">{pnl_sign}${overall_pnl_amt:,.2f}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">오늘의 거시 국면</div>
                <div class="kpi-val" style="color:{regime_badge_color}; font-size:12.5px;">{regime_title.split(' ')[0]}</div>
                <div style="font-size:10px; color:#94a3b8; font-family:monospace; margin-top:2px;">MSI {msi_score:.1f}pt</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">3-SLOT 포트폴리오</div>
                <div class="kpi-val" style="color:#38bdf8;">{active_slots} / {max_slots} Slots</div>
                <div style="font-size:10px; color:#94a3b8; font-family:monospace; margin-top:2px;">현금 비중 {cash_ratio_pct:.1f}%</div>
            </div>
        </div>

        <!-- Section 1: Live Portfolio Risk & Exit Monitor -->
        <div class="section">
            <div class="section-header">
                <div class="section-title">
                    <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:#10b981;"></span>
                    <span>1. LIVE PORTFOLIO HOLDINGS &amp; RISK MONITOR (내 계좌 보유 현황)</span>
                </div>
                <span class="badge" style="background:#1e293b; color:#94a3b8;">{len(holdings)} HOLDINGS ACTIVE</span>
            </div>
    """

    if holdings:
        html += """
            <div style="overflow-x:auto;">
                <table class="table">
                    <thead>
                        <tr>
                            <th>TICKER</th>
                            <th style="text-align:right;">QTY</th>
                            <th style="text-align:right;">BUY</th>
                            <th style="text-align:right;">CURRENT</th>
                            <th style="text-align:right;">PNL %</th>
                            <th style="text-align:right;">STOP (-7%/-10%)</th>
                            <th style="text-align:right;">TARGET (+18%)</th>
                            <th>GUARDIAN ACTION</th>
                        </tr>
                    </thead>
                    <tbody>
        """
        for h in holdings:
            t = h.get("ticker", "")
            qty_val = float(h.get("quantity", 0))
            buy_p = float(h.get("buy_price", 0))
            cur_p = float(h.get("current_price", buy_p))
            pnl_val = float(h.get("pnl_pct", 0.0))
            stop_p = float(h.get("stop_loss_price", derive_stop_price(buy_p)))
            tgt_p = float(h.get("target_price", derive_target_price(buy_p)))
            advice = h.get("exit_advice", "보유 지속 (가디언 감시 중)")

            item_color = "#10b981" if pnl_val >= 0 else "#ef4444"
            item_sign = "+" if pnl_val >= 0 else ""
            badge_bg = "rgba(16, 185, 129, 0.15)" if pnl_val >= 0 else "rgba(239, 68, 68, 0.15)"

            html += f"""
                        <tr>
                            <td><strong style="color:#ffffff; font-family:monospace; font-size:12px;">{t}</strong></td>
                            <td style="text-align:right; font-family:monospace; color:#38bdf8; font-weight:700;">{qty_val:,.0f}주</td>
                            <td style="text-align:right; font-family:monospace;">${buy_p:,.2f}</td>
                            <td style="text-align:right; font-family:monospace; color:#ffffff; font-weight:700;">${cur_p:,.2f}</td>
                            <td style="text-align:right; font-family:monospace; font-weight:800; color:{item_color};"><span style="background:{badge_bg}; padding:2px 6px; border-radius:4px;">{item_sign}{pnl_val:.2f}%</span></td>
                            <td style="text-align:right; font-family:monospace; color:#ef4444;">${stop_p:,.2f}</td>
                            <td style="text-align:right; font-family:monospace; color:#10b981;">${tgt_p:,.2f}</td>
                            <td style="font-size:11px; color:#cbd5e1;">{advice}</td>
                        </tr>
            """
        html += """
                    </tbody>
                </table>
            </div>
        """
    else:
        html += """
            <div style="background:#1e293b; border:1px dashed #334155; border-radius:8px; padding:20px; text-align:center; color:#94a3b8; font-size:12px;">
                [보유 종목 없음] 현재 100% 현금 대기 상태입니다.<br>
                미국장 개장 시 오토파일럿 트레이더가 최우선 확신도 종목으로 3-Slot 분할 진입을 준비합니다.
            </div>
        """

    html += f"""
        </div>

        <!-- Section 2: Macro Regime & Climate Diagnosis -->
        <div class="section">
            <div class="section-title">
                <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:{regime_badge_color};"></span>
                <span>2. MACRO CLIMATE &amp; MSI REGIME (거시 시장 국면 진단)</span>
            </div>

            <div class="macro-box">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                    <span style="font-size:13px; font-weight:800; color:#ffffff;">{macro_headline}</span>
                    <span class="badge" style="background:{regime_badge_bg}; color:{regime_badge_color}; border:1px solid {regime_badge_color};">{regime_title}</span>
                </div>
                <div style="font-size:12px; color:#cbd5e1; line-height:1.6;">
                    • <strong>거시 운용 지침:</strong> {macro_directive}<br>
                    • <strong>주도 순풍 섹터:</strong> <span style="color:#38bdf8; font-weight:700;">{tailwind_sectors}</span>
                </div>

                <div class="macro-gauges">
                    <div class="gauge-item">
                        <div class="gauge-label">10년물 국채금리</div>
                        <div class="gauge-val">{us10y.get('val', 4.42)}% <span style="font-size:10px; color:#94a3b8;">({us10y.get('status', 'BURDEN')})</span></div>
                    </div>
                    <div class="gauge-item">
                        <div class="gauge-label">달러 인덱스</div>
                        <div class="gauge-val">{dxy.get('val', 99.5)} <span style="font-size:10px; color:#94a3b8;">({dxy.get('status', 'NEUTRAL')})</span></div>
                    </div>
                    <div class="gauge-item">
                        <div class="gauge-label">VIX 변동성</div>
                        <div class="gauge-val">{vix.get('val', 15.8)} <span style="font-size:10px; color:#94a3b8;">({vix.get('status', 'NORMAL')})</span></div>
                    </div>
                    <div class="gauge-item">
                        <div class="gauge-label">WTI 국제유가</div>
                        <div class="gauge-val">${wti.get('val', 78.5)} <span style="font-size:10px; color:#94a3b8;">({wti.get('status', 'STABLE')})</span></div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Section 3: Today's Top Conviction Alpha Picks -->
        <div class="section">
            <div class="section-title">
                <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:#f59e0b;"></span>
                <span>3. TODAY'S TOP CONVICTION ALPHA PICKS (오늘 발굴된 최상위 퀀트 추천주)</span>
            </div>
    """

    # Top Pick Card
    if top_pick:
        tp_t = top_pick.get("ticker", "")
        tp_n = top_pick.get("name", tp_t)
        tp_score = float(top_pick.get("conviction_score", 90.0))
        tp_rs = float(top_pick.get("rs_3m", 0.0))
        tp_p = float(top_pick.get("price", 0.0))
        tp_tgt = float(top_pick.get("target_price", derive_target_price(tp_p)))
        tp_stp = float(top_pick.get("stop_price", derive_stop_price(tp_p)))
        tp_sec = top_pick.get("sector", "LEADER")
        tp_size = top_pick.get("sizing", {})
        tp_shares = tp_size.get("shares", calculate_target_shares(
            float(portfolio.get("total_equity_usd") or DEFAULT_BASE_ACCOUNT_USD), max(1.0, tp_p), slot_rank=1, is_bull=is_bull
        ).get("shares", 1))
        tp_usd = tp_size.get("allocated_usd", round(tp_p * tp_shares, 2))
        tp_rat = top_pick.get("rationale", "일목균형표 26일 기준선 지지 및 기관 잠행 매집 확인 완료.")

        html += f"""
            <div class="pick-card pick-card-top">
                <div class="pick-head">
                    <div>
                        <span class="badge" style="background:#f59e0b; color:#0f172a; font-weight:900; margin-right:6px;">🥇 RANK #1 TOP CONVICTION</span>
                        <span class="pick-title">{tp_t} <span style="font-size:12px; color:#94a3b8; font-weight:normal;">({tp_n})</span></span>
                        <span class="badge" style="background:#1e293b; color:#cbd5e1; border:1px solid #475569; margin-left:6px;">[{tp_sec}]</span>
                    </div>
                    <div style="text-align:right;">
                        <span style="font-family:monospace; font-size:16px; font-weight:800; color:#f59e0b;">{tp_score:.1f}점</span>
                        <span style="font-size:10px; color:#94a3b8; display:block; font-family:monospace;">3M RS: {tp_rs:+.1f}%</span>
                    </div>
                </div>
                <div class="pick-body">
                    • <strong>진입 권장가:</strong> <span style="font-family:monospace; font-weight:700; color:#ffffff;">${tp_p:,.2f}</span> &nbsp;|&nbsp; <strong>목표가(+15%):</strong> <span style="font-family:monospace; font-weight:700; color:#10b981;">${tp_tgt:,.2f}</span> &nbsp;|&nbsp; <strong>손절선(-5%):</strong> <span style="font-family:monospace; font-weight:700; color:#ef4444;">${tp_stp:,.2f}</span><br>
                    • <strong>1,000만원 기준 권장 수량:</strong> <span style="font-family:monospace; font-weight:700; color:#38bdf8;">{tp_shares}주 (${tp_usd:,.2f} / 포트폴리오 약 33%)</span><br>
                    • <strong>퀀트 분석 사유:</strong> {tp_rat}
                </div>
            </div>
        """

    # Runner Up Card
    if runner_up:
        ru_t = runner_up.get("ticker", "")
        ru_n = runner_up.get("name", ru_t)
        ru_score = float(runner_up.get("conviction_score", 85.0))
        ru_rs = float(runner_up.get("rs_3m", 0.0))
        ru_p = float(runner_up.get("price", 0.0))
        ru_tgt = float(runner_up.get("target_price", derive_target_price(ru_p)))
        ru_stp = float(runner_up.get("stop_price", derive_stop_price(ru_p)))
        ru_sec = runner_up.get("sector", "RUNNER")
        ru_size = runner_up.get("sizing", {})
        ru_shares = ru_size.get("shares", calculate_target_shares(
            float(portfolio.get("total_equity_usd") or DEFAULT_BASE_ACCOUNT_USD), max(1.0, ru_p), slot_rank=2, is_bull=is_bull
        ).get("shares", 1))
        ru_usd = ru_size.get("allocated_usd", round(ru_p * ru_shares, 2))
        ru_rat = runner_up.get("rationale", "주봉 대세 상승 안착 및 주도 섹터 2위 모멘텀 후보.")

        html += f"""
            <div class="pick-card pick-card-runner">
                <div class="pick-head">
                    <div>
                        <span class="badge" style="background:#38bdf8; color:#0f172a; font-weight:900; margin-right:6px;">🥈 RANK #2 RUNNER UP</span>
                        <span class="pick-title">{ru_t} <span style="font-size:12px; color:#94a3b8; font-weight:normal;">({ru_n})</span></span>
                        <span class="badge" style="background:#1e293b; color:#cbd5e1; border:1px solid #475569; margin-left:6px;">[{ru_sec}]</span>
                    </div>
                    <div style="text-align:right;">
                        <span style="font-family:monospace; font-size:16px; font-weight:800; color:#38bdf8;">{ru_score:.1f}점</span>
                        <span style="font-size:10px; color:#94a3b8; display:block; font-family:monospace;">3M RS: {ru_rs:+.1f}%</span>
                    </div>
                </div>
                <div class="pick-body">
                    • <strong>진입 권장가:</strong> <span style="font-family:monospace; font-weight:700; color:#ffffff;">${ru_p:,.2f}</span> &nbsp;|&nbsp; <strong>목표가(+15%):</strong> <span style="font-family:monospace; font-weight:700; color:#10b981;">${ru_tgt:,.2f}</span> &nbsp;|&nbsp; <strong>손절선(-5%):</strong> <span style="font-family:monospace; font-weight:700; color:#ef4444;">${ru_stp:,.2f}</span><br>
                    • <strong>1,000만원 기준 권장 수량:</strong> <span style="font-family:monospace; font-weight:700; color:#38bdf8;">{ru_shares}주 (${ru_usd:,.2f} / 포트폴리오 약 33%)</span><br>
                    • <strong>퀀트 분석 사유:</strong> {ru_rat}
                </div>
            </div>
        """

    # Top 5 Ranked Candidates Table
    if ranked_candidates:
        html += """
            <div style="font-size:12px; font-weight:800; color:#cbd5e1; margin:16px 0 8px 0; font-family:monospace;">
                📊 GLOBAL 60 UNIVERSE TOP 5 RANK MATRIX
            </div>
            <div style="overflow-x:auto;">
                <table class="table">
                    <thead>
                        <tr>
                            <th style="width:40px;">순위</th>
                            <th>TICKER</th>
                            <th>SECTOR</th>
                            <th style="text-align:right;">SCORE</th>
                            <th style="text-align:right;">3M RS</th>
                            <th style="text-align:right;">PRICE</th>
                            <th>FLOW / OBV</th>
                        </tr>
                    </thead>
                    <tbody>
        """
        for c in ranked_candidates[:5]:
            rk = c.get("conviction_rank", 1)
            tk = c.get("ticker", "")
            nm = c.get("name", tk)
            sc = float(c.get("conviction_score", 0.0))
            rs = float(c.get("rs_3m", 0.0))
            pr = float(c.get("price", 0.0))
            sec = c.get("sector", "GENERAL")
            obv = c.get("obv_status", "NORMAL")
            obv_label = "[잠행매집]" if obv == "STEALTH_ACCUM" else "[수급유입]" if obv == "BULL_FLOW" else "[중립]"
            obv_color = "#10b981" if obv in ("STEALTH_ACCUM", "BULL_FLOW") else "#94a3b8"

            html += f"""
                        <tr>
                            <td style="font-family:monospace; font-weight:800; color:#38bdf8;">#{rk}</td>
                            <td><strong style="color:#ffffff; font-family:monospace;">{tk}</strong> <span style="font-size:10.5px; color:#94a3b8;">({nm})</span></td>
                            <td style="font-size:11px; color:#cbd5e1;">{sec}</td>
                            <td style="text-align:right; font-family:monospace; font-weight:800; color:#f59e0b;">{sc:.1f}</td>
                            <td style="text-align:right; font-family:monospace; color:{'#10b981' if rs>=0 else '#ef4444'};">{rs:+.1f}%</td>
                            <td style="text-align:right; font-family:monospace; color:#ffffff;">${pr:,.2f}</td>
                            <td style="font-size:10.5px; color:{obv_color}; font-family:monospace;">{obv_label}</td>
                        </tr>
            """
        html += """
                    </tbody>
                </table>
            </div>
        """

    html += f"""
        </div>

        <!-- Section 4: System Governance -->
        <div class="section">
            <div class="section-title">
                <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:#38bdf8;"></span>
                <span>4. 24/7 SYSTEM GOVERNANCE &amp; TERMINAL LINK</span>
            </div>
            <div style="font-size:12px; color:#94a3b8; line-height:1.6;">
                • <strong>포워드 트래킹 상태:</strong> {health_status}<br>
                • <strong>포트폴리오 가디언:</strong> 10초 주기 실시간 칼손절(-5%) 및 무제한 트레일링 익절(+15%) 무인 감시 가동 중<br>
                • <strong>오토파일럿 트레이더:</strong> 미국장 개장 시(22:30 KST) 슬롯 여유 확인 후 자동 매수 대기 중<br>
                • <strong>실시간 터미널 대시보드:</strong> <a href="http://localhost:8000" target="_blank" style="color:#38bdf8; font-weight:700; text-decoration:underline;">http://localhost:8000 (알상무 퀀트 통합 터미널)</a>
            </div>
        </div>

        <!-- Footer -->
        <div class="footer">
            AL-SANGMOO QUANTITATIVE RISK ENGINE • 17-YEAR INSTITUTIONAL ALPHA SYSTEM<br>
            자동 발행: 매일 12:30 KST (GitHub Actions Forward Pipeline) • Confidential Portfolio Audit
        </div>
    </div>
</body>
</html>
"""
    return html

def print_markdown_briefing(today_str, dual_consensus, strat1_exclusive, strat2_exclusive, portfolio_alerts, health_status, stream_info=None, feed_data=None):
    if stream_info is None:
        stream_info = {}
    if feed_data is None:
        feed_data = {}
        
    portfolio = feed_data.get("portfolio", {})
    conviction = feed_data.get("conviction", {})
    macro_info = feed_data.get("macro", {})
    macro_climate = macro_info.get("macro_climate", stream_info.get("macro_climate", {}))
    macro_gauges = macro_info.get("macro_gauges", stream_info.get("macro_gauges", {}))
    
    tot_equity_usd = float(portfolio.get("total_equity_usd", 7500.0))
    overall_pnl_pct = float(portfolio.get("overall_pnl_pct", 0.0) or portfolio.get("unrealized_pnl_pct", 0.0))
    msi_score = extract_msi_score(macro_info)
    slot_summary = feed_data.get("slot_allocation_summary") or {}
    if "is_bull_regime" in macro_info:
        is_bull = bool(macro_info["is_bull_regime"])
    elif "is_bull_regime" in slot_summary:
        is_bull = bool(slot_summary["is_bull_regime"])
    else:
        is_bull = bool(resolve_capital_regime(msi_score=msi_score, fetch_spy=False)["is_bull_regime"])
    
    top_pick = conviction.get("top_pick", {})
    runner_up = conviction.get("runner_up", {})
    
    vix = macro_gauges.get("vix", {"val": 15.8, "status": "NORMAL"})
    us10y = macro_gauges.get("us10y", {"val": 4.42, "status": "BURDEN"})
    wti = macro_gauges.get("wti", {"val": 78.5, "status": "STABLE"})
    
    md = f"""# 🏛️ AL-SANGMOO QUANT DAILY BRIEFING ({today_str})

* **발행일시**: {today_str} 12:30 KST
* **계좌 총 평가액**: ${tot_equity_usd:,.2f} | **계좌 수익률**: {overall_pnl_pct:+.2f}%
* **시장 국면**: {'[BULL REGIME 강세장 (3-Slot 100%)]' if is_bull else '[BEAR REGIME 약세장 (2-Slot 50% 현금)]'} (MSI: {msi_score:.1f}pt)
* **거시 4대 지표**: 10Y `{us10y.get('val', 4.42)}%` | VIX `{vix.get('val', 15.8)}` | WTI `${wti.get('val', 78.5)}`

---

## 💼 1. Live Portfolio Holdings
"""
    holdings = portfolio.get("holdings", [])
    if holdings:
        for h in holdings:
            md += f"* **{h['ticker']}** ({h.get('quantity', 0):,.0f}주) | 매수가: ${h.get('buy_price', 0):,.2f} | 현재가: ${h.get('current_price', 0):,.2f} | 수익률: **{h.get('pnl_pct', 0):+.2f}%** | 손절: ${h.get('stop_loss_price', 0):,.2f} | 목표: ${h.get('target_price', 0):,.2f}\n"
    else:
        md += "*현재 보유 중인 포지션이 없습니다. (100% 현금 대기)*\n"

    md += """
---

## 🥇 2. Today's Top Conviction Picks
"""
    if top_pick:
        md += f"* **🥇 #1 Top Pick: {top_pick.get('ticker')} ({top_pick.get('name')})** | 점수: **{top_pick.get('conviction_score', 0):.1f}점** (3M RS: {top_pick.get('rs_3m', 0):+.1f}%) | 가격: ${top_pick.get('price', 0):,.2f} | 목표가: ${top_pick.get('target_price', 0):,.2f} | 손절가: ${top_pick.get('stop_price', 0):,.2f}\n"
        md += f"  - 권장 배정: {top_pick.get('sizing', {}).get('shares', 0)}주 (${top_pick.get('sizing', {}).get('allocated_usd', 0):,.2f})\n"
    if runner_up:
        md += f"* **🥈 #2 Runner Up: {runner_up.get('ticker')} ({runner_up.get('name')})** | 점수: **{runner_up.get('conviction_score', 0):.1f}점** (3M RS: {runner_up.get('rs_3m', 0):+.1f}%) | 가격: ${runner_up.get('price', 0):,.2f} | 목표가: ${runner_up.get('target_price', 0):,.2f} | 손절가: ${runner_up.get('stop_price', 0):,.2f}\n"

    md += f"""
---

## 🛠️ 3. System Governance
* 모델 검증 상태: `{health_status}`
* 웹 대시보드: http://localhost:8000
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
        
    dual_consensus = feed_data.get("tier1") or feed_data.get("dual_consensus") or []
    strat1_exclusive = feed_data.get("tier2") or feed_data.get("strat1_exclusive") or []
    strat2_exclusive = feed_data.get("tier3") or feed_data.get("strat2_exclusive") or []
    conviction = feed_data.get("conviction", {})
    portfolio = feed_data.get("portfolio", {})
    macro_info = feed_data.get("macro", {})
    
    # 3. Evaluate User Real Portfolio Positions
    portfolio_alerts = evaluate_user_portfolio_positions()
    
    # 4. Update Background History (Tier-1 / Tier-2 only; snipers are not bears)
    bull_picks = dual_consensus[:2]
    neutral_picks = strat1_exclusive[:2]
    bear_picks = []
    history_df, health_status = evaluate_active_positions_and_update(bull_picks, neutral_picks, bear_picks, today_str)
    
    # 5. Save Macro Snapshot, Recommendation Matrix, and Archive Daily Recommendations into SQLite
    try:
        save_macro_history_record(today_str, macro_climate, macro_gauges)
        save_recommendation_matrix_record(today_str, bull_picks, neutral_picks, bear_picks)
        archive_daily_recommendations(today_str, dual_consensus, strat1_exclusive, strat2_exclusive)
        print("[SQLite DB] Saved macro history, recommendation matrix, and daily recommendation archive.")
    except Exception as e:
        print(f"[SQLite DB Warning] {e}")
        
    # 6. Generate Modern Responsive HTML & Send Email
    html_content = generate_email_content(today_str, dual_consensus, strat1_exclusive, strat2_exclusive, portfolio_alerts, health_status, stream_info, feed_data)
    out_html_path = os.path.join(REPORTS_DIR, f"briefing_{today_str}.html")
    with open(out_html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
        
    overall_pnl = float(portfolio.get("overall_pnl_pct", 0.0) or portfolio.get("unrealized_pnl_pct", 0.0))
    pnl_str = f"{'+' if overall_pnl >= 0 else ''}{overall_pnl:.2f}%"
    is_bull = bool(macro_info.get("is_bull_regime", resolve_capital_regime(
        msi_score=extract_msi_score(macro_info), fetch_spy=False
    )["is_bull_regime"]))
    regime_str = "BULL REGIME (강세장)" if is_bull else "BEAR REGIME (약세장)"
    top_t = conviction.get("top_pick", {}).get("ticker") if conviction.get("top_pick") else (dual_consensus[0]["ticker"] if dual_consensus else "MARKET_RADAR")
    
    print(f"[Report Archive] Daily briefing HTML archived at: {out_html_path}")
    
    # 7. Print Markdown Briefing
    print_markdown_briefing(today_str, dual_consensus, strat1_exclusive, strat2_exclusive, portfolio_alerts, health_status, stream_info, feed_data)

if __name__ == "__main__":
    main()
