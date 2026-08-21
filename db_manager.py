import os
import sqlite3
import pandas as pd
from datetime import datetime
import yfinance as yf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "quant_trades.db")

def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
    except Exception:
        pass
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. User's Real Portfolio
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
    
    # 2. Daily Recommendation Matrix (2+2+2)
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

    # 3. Recommended Trades Tracker
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
    
    # 4. Gate-0 Macro History Table
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

def save_macro_history_record(date_str, macro_climate, macro_gauges):
    init_db()
    conn = get_db()
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

def get_latest_macro_record():
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM macro_history ORDER BY date DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def add_portfolio_buy(ticker, buy_price, quantity, buy_date=None):
    init_db()
    conn = get_db()
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

def record_portfolio_sell(holding_id, sell_price, sell_date=None, reason="MANUAL_SELL"):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    
    if not sell_date:
        sell_date = datetime.now().strftime("%Y-%m-%d")
        
    cursor.execute("SELECT * FROM my_portfolio WHERE id = ?", (holding_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None
        
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
        f"청산 완료 ({pnl_pct:+.2f}%) - {reason}", holding_id
    ))
    conn.commit()
    conn.close()
    return True

def reset_all_holdings():
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM my_portfolio")
    conn.commit()
    conn.close()
    return True

def save_recommendation_matrix_record(date_str, bull_picks, neutral_picks, bear_picks):
    init_db()
    conn = get_db()
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
        date_str, b1['ticker'], b1['close'], b2['ticker'], b2['close'],
        n1['ticker'], n1['close'], n2['ticker'], n2['close'],
        s1['ticker'], s1['close'], s2['ticker'], s2['close'], now_str
    ))
    conn.commit()
    conn.close()

def get_live_portfolio():
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM my_portfolio WHERE status = 'HOLDING' ORDER BY buy_date DESC, id DESC")
    holdings = [dict(r) for r in cursor.fetchall()]
    conn.close()
    
    total_invested = 0.0
    total_eval = 0.0
    update_rows = []
    
    for h in holdings:
        ticker = h['ticker']
        buy_price = float(h['buy_price'])
        quantity = float(h['quantity'])
        total_cost = buy_price * quantity
        cur_price = buy_price
        
        try:
            cur_data = yf.download(ticker, period="5d", interval="1d", progress=False)
            if not cur_data.empty:
                if isinstance(cur_data.columns, pd.MultiIndex):
                    cur_data.columns = cur_data.columns.get_level_values(0)
                cur_price = float(cur_data.iloc[-1]['Close'])
        except Exception:
            pass
            
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
        
    # Short atomic batch write
    if update_rows:
        try:
            conn = get_db()
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

def get_recommendations_matrix():
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM recommendation_matrix ORDER BY date DESC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

# Function aliases for backward compatibility and API stability
close_portfolio_position = record_portfolio_sell
clear_portfolio = reset_all_holdings
