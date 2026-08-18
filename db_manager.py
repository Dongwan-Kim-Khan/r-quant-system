import os
import sqlite3
import pandas as pd
from datetime import datetime
import yfinance as yf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "quant_trades.db")

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. User's Real Portfolio (실제 매수한 내 포트폴리오)
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
        status TEXT DEFAULT 'HOLDING', -- 'HOLDING' or 'SOLD'
        sell_date TEXT,
        sell_price REAL,
        exit_advice TEXT DEFAULT '보유 지속',
        created_at TEXT
    )
    """)
    
    # Safe migration: Add target/stop columns if table already existed
    try:
        cursor.execute("ALTER TABLE my_portfolio ADD COLUMN target_price REAL DEFAULT 0")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE my_portfolio ADD COLUMN stop_loss_price REAL DEFAULT 0")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE my_portfolio ADD COLUMN partial_tp_price REAL DEFAULT 0")
    except Exception:
        pass
    
    # 2. Daily Recommendation Matrix (날짜별 추천 2+2+2 히스토리)
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

    # 3. Automated Recommended Trades Tracker (추천 종목별 목표가/손절가/수익률 전수 추적)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        ticker TEXT NOT NULL,
        type TEXT NOT NULL,           -- 'BULL', 'NEUTRAL', 'BEAR'
        entry_price REAL NOT NULL,    -- 추천 진입가
        current_price REAL DEFAULT 0, -- 현재가
        target_price REAL NOT NULL,   -- +15% 1차 목표 익절가
        partial_tp_price REAL NOT NULL,-- +8% 50% 분할 익절가
        stop_loss_price REAL NOT NULL,-- -3% 칼손절가
        pnl_pct REAL DEFAULT 0,       -- 현재 수익률 %
        max_gain_pct REAL DEFAULT 0,  -- 최고 도달 수익률 %
        status TEXT DEFAULT 'OPEN',   -- 'OPEN', 'CLOSED_PROFIT', 'CLOSED_STOP', 'CLOSED_EXPIRED'
        days_active INTEGER DEFAULT 0,
        exit_advice TEXT DEFAULT '보유 지속',
        updated_at TEXT
    )
    """)
    
    try:
        cursor.execute("ALTER TABLE trades ADD COLUMN target_price REAL DEFAULT 0")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE trades ADD COLUMN partial_tp_price REAL DEFAULT 0")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE trades ADD COLUMN stop_loss_price REAL DEFAULT 0")
    except Exception:
        pass

    conn.commit()
    conn.close()

def add_portfolio_buy(ticker, buy_price, quantity, buy_date=None):
    if not buy_date:
        buy_date = datetime.now().strftime("%Y-%m-%d")
    b_price = float(buy_price)
    qty = float(quantity)
    total_cost = b_price * qty
    target_p = round(b_price * 1.15, 2)     # +15% 1차 목표 익절가
    stop_p = round(b_price * 0.97, 2)       # -3% 칼손절가
    partial_p = round(b_price * 1.08, 2)    # +8% 50% 분할 익절가
    
    conn = get_db()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
    INSERT INTO my_portfolio (
        ticker, buy_date, buy_price, quantity, current_price,
        total_cost, current_value, pnl_pct, pnl_amount,
        target_price, stop_loss_price, partial_tp_price,
        status, exit_advice, created_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'HOLDING', ?, ?)
    """, (
        ticker.upper(), buy_date, b_price, qty,
        b_price, total_cost, total_cost, 0.0, 0.0,
        target_p, stop_p, partial_p,
        f"⏳ [보유 지속] 손절선 ${stop_p:,.2f} 유지 / 목표가 ${target_p:,.2f}", now_str
    ))
    conn.commit()
    inserted_id = cursor.lastrowid
    conn.close()
    return inserted_id

def close_portfolio_position(position_id, sell_price, sell_date=None, reason="MANUAL_SELL"):
    if not sell_date:
        sell_date = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM my_portfolio WHERE id = ?", (position_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False
        
    buy_price = float(row['buy_price'])
    quantity = float(row['quantity'])
    total_cost = float(row['total_cost'])
    current_value = float(sell_price) * quantity
    pnl_pct = ((float(sell_price) - buy_price) / buy_price) * 100
    pnl_amount = current_value - total_cost
    
    cursor.execute("""
    UPDATE my_portfolio
    SET status = 'SOLD', sell_date = ?, sell_price = ?, current_price = ?, current_value = ?, pnl_pct = ?, pnl_amount = ?, exit_advice = ?
    WHERE id = ?
    """, (
        sell_date, float(sell_price), float(sell_price), current_value, pnl_pct, pnl_amount, f"매도 완료 ({reason})", position_id
    ))
    conn.commit()
    conn.close()
    return True

def clear_portfolio():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM my_portfolio")
    conn.commit()
    conn.close()
    return True

def save_recommendation_matrix_record(date_str, bull_picks, neutral_picks, bear_picks):
    conn = get_db()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    b1 = bull_picks[0] if len(bull_picks) > 0 else {"ticker": "-", "close": 0}
    b2 = bull_picks[1] if len(bull_picks) > 1 else {"ticker": "-", "close": 0}
    n1 = neutral_picks[0] if len(neutral_picks) > 0 else {"ticker": "-", "close": 0}
    n2 = neutral_picks[1] if len(neutral_picks) > 1 else {"ticker": "-", "close": 0}
    s1 = bear_picks[0] if len(bear_picks) > 0 else {"ticker": "-", "close": 0}
    s2 = bear_picks[1] if len(bear_picks) > 1 else {"ticker": "-", "close": 0}
    
    cursor.execute("""
    INSERT OR REPLACE INTO recommendation_matrix 
    (date, bull_1, bull_1_price, bull_2, bull_2_price, neutral_1, neutral_1_price, neutral_2, neutral_2_price, bear_1, bear_1_price, bear_2, bear_2_price, created_at)
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
    
    total_invested = 0.0
    total_eval = 0.0
    
    # Update live prices for holdings
    for h in holdings:
        ticker = h['ticker']
        try:
            cur_data = yf.download(ticker, period="5d", interval="1d", progress=False)
            if not cur_data.empty:
                if isinstance(cur_data.columns, pd.MultiIndex):
                    cur_data.columns = cur_data.columns.get_level_values(0)
                cur_price = float(cur_data.iloc[-1]['Close'])
                buy_price = float(h['buy_price'])
                quantity = float(h['quantity'])
                total_cost = buy_price * quantity
                cur_val = cur_price * quantity
                pnl_pct = ((cur_price - buy_price) / buy_price) * 100
                pnl_amt = cur_val - total_cost
                
                # Check exit advice
                if pnl_pct >= 15.0:
                    advice = "🚨 [전량 익절 매도] +15% 목표가 달성! 전량 차익 실현 권고"
                elif pnl_pct <= -3.0:
                    advice = "🚨 [칼손절 긴급 매도] -3% 손절선 터치! 즉시 전량 매도 권고"
                elif 8.0 <= pnl_pct < 15.0:
                    advice = "💰 [50% 분할 익절] 절반 챙기고 본절 스탑 상향 권고"
                else:
                    advice = f"⏳ [보유 지속] 손절선 ${buy_price * 0.97:,.2f} 유지"
                    
                h['current_price'] = cur_price
                h['current_value'] = cur_val
                h['pnl_pct'] = pnl_pct
                h['pnl_amount'] = pnl_amt
                h['exit_advice'] = advice
                
                # Update DB
                cursor.execute("""
                UPDATE my_portfolio SET current_price = ?, current_value = ?, pnl_pct = ?, pnl_amount = ?, exit_advice = ?
                WHERE id = ?
                """, (cur_price, cur_val, pnl_pct, pnl_amt, advice, h['id']))
        except Exception:
            pass
            
        total_invested += float(h['total_cost'])
        total_eval += float(h['current_value'])
        
    conn.commit()
    conn.close()
    
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

if __name__ == "__main__":
    init_db()
    # Sample initial recommendation record for 2026-08-18
    save_recommendation_matrix_record(
        "2026-08-18",
        [{"ticker": "AMZN", "close": 261.05}, {"ticker": "LLY", "close": 1197.58}],
        [{"ticker": "AVGO", "close": 394.27}, {"ticker": "COST", "close": 950.77}],
        [{"ticker": "META", "close": 573.88}, {"ticker": "TSLA", "close": 339.58}]
    )
    print("Matrix record initialized.")
