import os
import sqlite3
import pandas as pd
from datetime import datetime

DB_FILE = "quant_trades.db"

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. Trades Table (Forward Tracking)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        ticker TEXT,
        type TEXT,
        entry_price REAL,
        current_price REAL,
        pnl_pct REAL,
        max_gain_pct REAL,
        status TEXT,
        days_active INTEGER,
        exit_advice TEXT,
        updated_at TEXT
    )
    """)
    
    # 2. Daily Scans Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS daily_scans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_date TEXT,
        ticker TEXT,
        score INTEGER,
        kijun_gap REAL,
        vol_ratio REAL,
        category TEXT,
        comment TEXT
    )
    """)
    
    # 3. Model Health & Metrics Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS model_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        win_rate REAL,
        total_closed INTEGER,
        health_status TEXT
    )
    """)
    
    conn.commit()
    conn.close()

def sync_from_csv(csv_path="trade_history.csv"):
    if not os.path.exists(csv_path):
        return
    init_db()
    df = pd.read_csv(csv_path)
    conn = get_db()
    cursor = conn.cursor()
    
    # Clear existing and import
    cursor.execute("DELETE FROM trades")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for _, row in df.iterrows():
        cursor.execute("""
        INSERT INTO trades (date, ticker, type, entry_price, current_price, pnl_pct, max_gain_pct, status, days_active, exit_advice, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row.get('date'), row.get('ticker'), row.get('type'),
            float(row.get('entry_price', 0)), float(row.get('current_price', 0)),
            float(row.get('pnl_pct', 0)), float(row.get('max_gain_pct', 0)),
            row.get('status', 'OPEN'), int(row.get('days_active', 0)),
            row.get('exit_advice', '보유 지속'), now_str
        ))
    conn.commit()
    conn.close()
    print("Synchronized trade history into SQLite quant_trades.db successfully.")

if __name__ == "__main__":
    init_db()
    sync_from_csv()
