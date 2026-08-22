"""
SQLite Persistence Repository Layer with WAL Mode & Atomic Transactions.
"""
import os
import re
import sqlite3
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import yfinance as yf
from al_sangmoo.core.config import DB_FILE, CHARTS_DIR

REASON_REGEX = re.compile(r'^[A-Za-z0-9_\-\s\(\)가-힣.,%]{1,100}$')

def get_connection(timeout: float = 30.0, db_path: str = None) -> sqlite3.Connection:
    """Returns an isolated SQLite connection configured with WAL mode and pragmas."""
    target_db = db_path or os.environ.get("AL_SANGMOO_DB_PATH") or str(DB_FILE)
    conn = sqlite3.connect(str(target_db), timeout=timeout)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
    except Exception:
        pass
    return conn

def init_database() -> None:
    """Ensures all required tables exist."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS my_portfolio (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        buy_date TEXT NOT NULL,
        buy_price REAL NOT NULL,
        quantity REAL NOT NULL,
        current_price REAL DEFAULT 0,
        total_cost REAL NOT NULL,
        current_value REAL DEFAULT 0,
        pnl_pct REAL DEFAULT 0,
        pnl_amount REAL DEFAULT 0,
        target_price REAL DEFAULT 0,
        stop_loss_price REAL DEFAULT 0,
        partial_tp_price REAL DEFAULT 0,
        status TEXT DEFAULT 'HOLDING',
        sell_date TEXT,
        sell_price REAL,
        exit_advice TEXT DEFAULT '보유 지속',
        created_at TEXT
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS recommendation_matrix (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT UNIQUE NOT NULL,
        bull_1 TEXT,
        bull_1_price REAL,
        bull_2 TEXT,
        bull_2_price REAL,
        neutral_1 TEXT,
        neutral_1_price REAL,
        neutral_2 TEXT,
        neutral_2_price REAL,
        bear_1 TEXT,
        bear_1_price REAL,
        bear_2 TEXT,
        bear_2_price REAL,
        created_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        ticker TEXT NOT NULL,
        type TEXT NOT NULL,
        entry_price REAL NOT NULL,
        current_price REAL DEFAULT 0,
        target_price REAL NOT NULL,
        partial_tp_price REAL NOT NULL,
        stop_loss_price REAL NOT NULL,
        pnl_pct REAL DEFAULT 0,
        max_gain_pct REAL DEFAULT 0,
        status TEXT DEFAULT 'OPEN',
        days_active INTEGER DEFAULT 0,
        exit_advice TEXT DEFAULT '보유 지속',
        updated_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS macro_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT UNIQUE NOT NULL,
        vix_val REAL,
        vix_status TEXT,
        us10y_val REAL,
        us10y_status TEXT,
        wti_val REAL,
        wti_status TEXT,
        macro_stance TEXT,
        macro_headline TEXT,
        macro_directive TEXT,
        external_shocks TEXT,
        created_at TEXT
    )
    """)
    conn.commit()
    conn.close()

def save_macro_history_record(date_str: str, macro_climate: dict, macro_gauges: dict) -> None:
    init_database()
    conn = get_connection()
    cursor = conn.cursor()
    
    vix = macro_gauges.get("vix", {})
    us10y = macro_gauges.get("us10y", {})
    wti = macro_gauges.get("wti", {})
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    shocks_str = ", ".join(macro_climate.get("external_shocks", []))
    
    cursor.execute("""
    INSERT OR REPLACE INTO macro_history (
        date, vix_val, vix_status, us10y_val, us10y_status,
        wti_val, wti_status, macro_stance, macro_headline,
        macro_directive, external_shocks, created_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        date_str,
        float(vix.get("val", 0)), str(vix.get("status", "NORMAL")),
        float(us10y.get("val", 0)), str(us10y.get("status", "NORMAL")),
        float(wti.get("val", 0)), str(wti.get("status", "NORMAL")),
        str(macro_climate.get("macro_stance", "DEFENSE_HOLD")),
        str(macro_climate.get("macro_headline", "")),
        str(macro_climate.get("macro_action_directive", "")),
        shocks_str, now_str
    ))
    conn.commit()
    conn.close()

def get_latest_macro_record() -> dict:
    init_database()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM macro_history ORDER BY date DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def add_portfolio_buy(ticker: str, buy_price: float, quantity: float, buy_date: str = None) -> int:
    init_database()
    conn = get_connection()
    cursor = conn.cursor()
    
    if not buy_date:
        buy_date = datetime.now().strftime("%Y-%m-%d")
        
    total_cost = buy_price * quantity
    target_price = round(buy_price * 1.15, 2)
    stop_loss_price = round(buy_price * 0.97, 2)
    partial_tp_price = round(buy_price * 1.08, 2)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute("""
    INSERT INTO my_portfolio (
        ticker, buy_date, buy_price, quantity, current_price,
        total_cost, current_value, pnl_pct, pnl_amount,
        target_price, stop_loss_price, partial_tp_price,
        status, exit_advice, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'HOLDING', '보유 지속', ?)
    """, (
        ticker, buy_date, buy_price, quantity, buy_price,
        total_cost, total_cost, 0.0, 0.0,
        target_price, stop_loss_price, partial_tp_price, now_str
    ))
    
    inserted_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return inserted_id

def record_portfolio_sell(holding_id: int, sell_price: float, sell_date: str = None, reason: str = "MANUAL_SELL") -> bool:
    init_database()
    conn = get_connection()
    cursor = conn.cursor()
    
    if not sell_date:
        sell_date = datetime.now().strftime("%Y-%m-%d")
        
    # Defensive sanitization & length constraint
    clean_reason = str(reason).strip()[:100] if reason else "MANUAL_SELL"
    if not REASON_REGEX.match(clean_reason):
        clean_reason = re.sub(r'[^A-Za-z0-9_\-\s\(\)가-힣.,%]', '', clean_reason).strip() or "MANUAL_SELL"
        clean_reason = clean_reason[:100]
        
    cursor.execute("SELECT * FROM my_portfolio WHERE id = ?", (holding_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False
        
    buy_price = float(row['buy_price'])
    quantity = float(row['quantity'])
    pnl_pct = ((sell_price - buy_price) / buy_price) * 100
    pnl_amt = (sell_price - buy_price) * quantity
    
    cursor.execute("""
    UPDATE my_portfolio
    SET status = 'SOLD', sell_date = ?, sell_price = ?, current_price = ?,
        current_value = ?, pnl_pct = ?, pnl_amount = ?, exit_advice = ?
    WHERE id = ?
    """, (
        sell_date, sell_price, sell_price,
        sell_price * quantity, pnl_pct, pnl_amt,
        f"청산 완료 ({pnl_pct:+.2f}%) - {clean_reason}", holding_id
    ))
    conn.commit()
    conn.close()
    return True

def reset_all_holdings() -> bool:
    init_database()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM my_portfolio")
    conn.commit()
    conn.close()
    return True

def get_live_portfolio() -> dict:
    init_database()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM my_portfolio WHERE status = 'HOLDING' ORDER BY buy_date DESC, id DESC")
    holdings = [dict(r) for r in cursor.fetchall()]
    conn.close()
    
    total_invested = 0.0
    total_eval = 0.0
    update_rows = []
    
    # Fast Local Cache Quote Lookup with ThreadPool Network Fallback
    unique_tickers = list(set(h['ticker'] for h in holdings))
    prices_map = {}
    missing_tickers = []
    
    for tk in unique_tickers:
        chart_file = os.path.join(str(CHARTS_DIR), f"{tk}.json")
        if os.path.exists(chart_file):
            try:
                import json
                with open(chart_file, 'r', encoding='utf-8') as f:
                    c_data = json.load(f)
                    if "latest_close" in c_data and c_data["latest_close"] is not None:
                        prices_map[tk] = float(c_data["latest_close"])
                    elif "candles" in c_data and len(c_data["candles"]) > 0:
                        prices_map[tk] = float(c_data["candles"][-1]["close"])
            except Exception:
                missing_tickers.append(tk)
        else:
            missing_tickers.append(tk)
            
    if missing_tickers:
        def fetch_price(tk):
            try:
                cur_data = yf.download(tk, period="5d", interval="1d", progress=False)
                if not cur_data.empty:
                    if isinstance(cur_data.columns, pd.MultiIndex):
                        cur_data.columns = cur_data.columns.get_level_values(0)
                    return tk, float(cur_data.iloc[-1]['Close'])
            except Exception:
                pass
            return tk, None

        with ThreadPoolExecutor(max_workers=min(12, len(missing_tickers))) as executor:
            for tk, pr in executor.map(fetch_price, missing_tickers):
                if pr is not None:
                    prices_map[tk] = pr

    for h in holdings:
        ticker = h['ticker']
        buy_price = float(h['buy_price'])
        quantity = float(h['quantity'])
        total_cost = buy_price * quantity
        cur_price = prices_map.get(ticker, buy_price)
        
        cur_val = cur_price * quantity
        pnl_pct = ((cur_price - buy_price) / buy_price) * 100 if buy_price > 0 else 0.0
        pnl_amt = cur_val - total_cost
        
        if pnl_pct >= 15.0:
            advice = f"전량 익절 매도 권고 (목표가 달성 {pnl_pct:+.2f}%)"
        elif pnl_pct <= -3.0:
            advice = f"칼손절 긴급 매도 권고 (손절선 이탈 {pnl_pct:+.2f}%)"
        elif 8.0 <= pnl_pct < 15.0:
            advice = f"50% 분할 익절 권고 (수익률 {pnl_pct:+.1f}%)"
        else:
            advice = f"보유 지속 (손절선 ${buy_price * 0.97:,.2f} 유지)"
            
        h['current_price'] = cur_price
        h['current_value'] = cur_val
        h['pnl_pct'] = pnl_pct
        h['pnl_amount'] = pnl_amt
        h['exit_advice'] = advice
        
        update_rows.append((cur_price, cur_val, pnl_pct, pnl_amt, advice, h['id']))
        total_invested += total_cost
        total_eval += cur_val
        
    if update_rows:
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.executemany("""
            UPDATE my_portfolio SET current_price = ?, current_value = ?, pnl_pct = ?, pnl_amount = ?, exit_advice = ?
            WHERE id = ?
            """, update_rows)
            conn.commit()
            conn.close()
        except Exception:
            pass
            
    overall_pnl_pct = ((total_eval - total_invested) / total_invested * 100) if total_invested > 0 else 0.0
    overall_pnl_amt = total_eval - total_invested
    
    return {
        "holdings": holdings,
        "total_invested": total_invested,
        "total_eval": total_eval,
        "overall_pnl_pct": overall_pnl_pct,
        "overall_pnl_amount": overall_pnl_amt
    }

def save_recommendation_matrix_record(date_str: str, bull_picks: list, neutral_picks: list, bear_picks: list) -> None:
    init_database()
    conn = get_connection()
    cursor = conn.cursor()
    
    b1 = bull_picks[0] if len(bull_picks) > 0 else {"ticker": "-", "close": 0}
    b2 = bull_picks[1] if len(bull_picks) > 1 else {"ticker": "-", "close": 0}
    n1 = neutral_picks[0] if len(neutral_picks) > 0 else {"ticker": "-", "close": 0}
    n2 = neutral_picks[1] if len(neutral_picks) > 1 else {"ticker": "-", "close": 0}
    s1 = bear_picks[0] if len(bear_picks) > 0 else {"ticker": "-", "close": 0}
    s2 = bear_picks[1] if len(bear_picks) > 1 else {"ticker": "-", "close": 0}
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute("""
    INSERT OR REPLACE INTO recommendation_matrix (
        date, bull_1, bull_1_price, bull_2, bull_2_price,
        neutral_1, neutral_1_price, neutral_2, neutral_2_price,
        bear_1, bear_1_price, bear_2, bear_2_price, created_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        date_str, b1.get('ticker', '-'), b1.get('close', 0), b2.get('ticker', '-'), b2.get('close', 0),
        n1.get('ticker', '-'), n1.get('close', 0), n2.get('ticker', '-'), n2.get('close', 0),
        s1.get('ticker', '-'), s1.get('close', 0), s2.get('ticker', '-'), s2.get('close', 0), now_str
    ))
    conn.commit()
    conn.close()

def get_recommendations_matrix() -> list:
    init_database()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM recommendation_matrix ORDER BY date DESC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def archive_daily_recommendations(today_str: str, dual_consensus: list, strat1_exclusive: list, strat2_exclusive: list) -> int:
    init_database()
    conn = get_connection()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    saved_count = 0
    
    all_recs = []
    for d in dual_consensus:
        all_recs.append((d, "DUAL_5_STAR"))
    for p in strat1_exclusive:
        all_recs.append((p, "STRAT1_PULLBACK"))
    for s in strat2_exclusive:
        all_recs.append((s, "STRAT2_SNIPER"))
        
    for item, rec_type in all_recs:
        tk = item["ticker"]
        price = float(item["price"])
        tgt_p = float(item.get("target_price", round(price * 1.15, 2)))
        stop_p = float(item.get("stop_price", round(price * 0.96, 2)))
        part_p = round(price * 1.08, 2)
        
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
            0.0, 0.0, 'OPEN', 0, ?, ?
        )
        """, (
            today_str, tk,
            today_str, tk, rec_type, price, price,
            tgt_p, part_p, stop_p,
            f"신규 추천 진입 ({rec_type})", now_str
        ))
        saved_count += 1
        
def get_recommendation_streaks() -> dict:
    """
    Computes consecutive active recommendation days (streaks) for each ticker.
    Returns a dict mapping ticker -> integer streak count (>= 1).
    """
    init_database()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT date, ticker FROM trades ORDER BY date DESC")
    rows = cursor.fetchall()
    conn.close()
    
    ticker_dates = {}
    for r in rows:
        d_str, tk = r[0], r[1]
        if tk not in ticker_dates:
            ticker_dates[tk] = set()
        ticker_dates[tk].add(d_str)
        
    streaks = {}
    for tk, dates in ticker_dates.items():
        sorted_dates = sorted(list(dates), reverse=True)
        streak = 1
        for i in range(len(sorted_dates) - 1):
            try:
                d1 = datetime.strptime(sorted_dates[i], "%Y-%m-%d")
                d2 = datetime.strptime(sorted_dates[i+1], "%Y-%m-%d")
                delta = (d1 - d2).days
                # Continuous calendar days or over-the-weekend gap (Friday to Monday: 3 days)
                if delta == 1 or (d1.weekday() == 0 and delta <= 3):
                    streak += 1
                else:
                    break
            except Exception:
                break
        streaks[tk] = streak
    return streaks

def get_daily_recommendation_history() -> list:
    """
    Groups historical trades table entries by date and returns a 3-Tier list for the dashboard history table.
    """
    init_database()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT date, type, ticker, entry_price FROM trades ORDER BY date DESC, id ASC")
    rows = cursor.fetchall()
    
    # Fetch macro stances from macro_history if available
    m_stances = {}
    try:
        cursor.execute("SELECT date, macro_stance, msi_score FROM macro_history")
        for r in cursor.fetchall():
            m_stances[r['date']] = f"{r['macro_stance']} ({float(r['msi_score']):.1f}pt)"
    except Exception:
        pass
    conn.close()
    
    history_by_date = {}
    for r in rows:
        d_str = r['date']
        rec_type = r['type']
        tk = r['ticker']
        p = float(r['entry_price']) if r['entry_price'] else 0.0
        
        if d_str not in history_by_date:
            history_by_date[d_str] = {
                "date": d_str,
                "tier1": [],
                "tier2": [],
                "tier3": [],
                "macro_stance": m_stances.get(d_str, "DEFENSE_HOLD (66.9pt)")
            }
            
        item = {"ticker": tk, "price": p}
        if rec_type in ["DUAL_5_STAR"]:
            history_by_date[d_str]["tier1"].append(item)
        elif rec_type in ["STRAT1_PULLBACK", "BULL"]:
            history_by_date[d_str]["tier2"].append(item)
        elif rec_type in ["STRAT2_SNIPER", "BEAR", "ACTIVE_SNIPER"]:
            history_by_date[d_str]["tier3"].append(item)
        else:
            history_by_date[d_str]["tier1"].append(item)
            
    # Sort by date descending
    result = [history_by_date[d] for d in sorted(history_by_date.keys(), reverse=True)]
    return result

# Aliases
get_db = get_connection
init_db = init_database
close_portfolio_position = record_portfolio_sell
clear_portfolio = reset_all_holdings
