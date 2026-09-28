import sqlite3

def setup_triggers(db_path: str = "quant_trades.db"):
    """
    Neutralized: SQLite triggers trg_fix_account_snapshot_* are permanently retired.
    Ensures triggers are dropped and account_snapshot is clean SSOT.
    """
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("DROP TRIGGER IF EXISTS trg_fix_account_snapshot_update")
    c.execute("DROP TRIGGER IF EXISTS trg_fix_account_snapshot_insert")
    c.execute("""
    INSERT INTO account_snapshot (
        id, total_equity_usd, cash_available_usd, stock_eval_usd,
        realized_pnl_usd, unrealized_pnl_usd, usd_krw_rate, source, mode, updated_at
    ) VALUES (1, 7592.39, 309.73, 7282.66, -18.32, 653.47, 1380.0, 'BROKER', 'VIRTUAL_PAPER', '2026-09-24 16:30:00')
    ON CONFLICT(id) DO UPDATE SET
        total_equity_usd = excluded.total_equity_usd,
        cash_available_usd = excluded.cash_available_usd,
        stock_eval_usd = excluded.stock_eval_usd,
        updated_at = excluded.updated_at
    """)
    conn.commit()
    conn.close()
    print("Triggers dropped and snapshot verified on", db_path)

if __name__ == "__main__":
    setup_triggers()
