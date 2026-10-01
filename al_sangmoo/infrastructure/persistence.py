"""
SQLite Persistence Repository Layer with WAL Mode, Atomic Transactions & CQRS Decoupling.
"""
import os
import re
import sqlite3
import threading
import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import yfinance as yf
from al_sangmoo.core.config import DB_FILE, CHARTS_DIR
from al_sangmoo.core.constants import (
    HARD_STOP_PCT,
    TRAILING_ACTIVATE_PCT,
    derive_partial_tp_price,
    derive_stop_price,
    derive_target_price,
    DEFAULT_BASE_ACCOUNT_USD,
)

REASON_REGEX = re.compile(r'^[A-Za-z0-9_\-\s\(\)가-힣.,%]{1,100}$')
DEFAULT_USD_KRW_RATE = 1380.0

class ManagedConnection(sqlite3.Connection):
    """
    Hardened SQLite connection that automatically manages transactions
    AND guarantees handle closure upon context manager exit.
    """
    def __enter__(self):
        super().__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()

def get_connection(timeout: float = 30.0, db_path: str = None) -> sqlite3.Connection:
    """Returns an isolated SQLite connection configured with WAL mode, pragmas, and auto-closing factory."""
    target_db = db_path or os.environ.get("AL_SANGMOO_DB_PATH") or str(DB_FILE)
    if str(target_db) != ":memory:":
        parent_dir = os.path.dirname(os.path.abspath(str(target_db)))
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
    conn = sqlite3.connect(str(target_db), timeout=timeout, factory=ManagedConnection)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
    except Exception:
        pass
    return conn

_INITIALIZED_DBS = set()
_INIT_LOCK = threading.Lock()

def init_database(force: bool = False) -> None:
    """Ensures all required tables exist."""
    target_db = os.environ.get("AL_SANGMOO_DB_PATH") or str(DB_FILE)
    if not force and target_db in _INITIALIZED_DBS and (target_db == ":memory:" or os.path.exists(target_db)):
        return
    with _INIT_LOCK:
        if not force and target_db in _INITIALIZED_DBS and (target_db == ":memory:" or os.path.exists(target_db)):
            return
        _do_init_database(target_db)
        _INITIALIZED_DBS.add(target_db)

def _do_init_database(target_db: str) -> None:
    with get_connection(db_path=target_db) as conn:
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
            msi_score REAL DEFAULT 50.0,
            created_at TEXT
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS trade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            holding_id INTEGER,
            ticker TEXT NOT NULL,
            buy_date TEXT,
            sell_date TEXT,
            buy_price REAL,
            sell_price REAL,
            quantity REAL,
            pnl_pct REAL,
            pnl_amount REAL,
            reason TEXT,
            created_at TEXT
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS execution_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            total_amount REAL NOT NULL,
            order_type TEXT DEFAULT 'MARKETABLE',
            status TEXT DEFAULT 'FILLED',
            message TEXT,
            order_id TEXT
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS exit_cooldowns (
            ticker TEXT PRIMARY KEY,
            exited_at REAL NOT NULL,
            lock_until REAL NOT NULL,
            trading_day TEXT NOT NULL,
            reason TEXT,
            source TEXT DEFAULT 'GUARDIAN',
            order_id TEXT,
            updated_at TEXT NOT NULL
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS autopilot_entry_sessions (
            session_date TEXT PRIMARY KEY,
            claimed_at TEXT NOT NULL,
            source TEXT DEFAULT 'SCHEDULER'
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS proxy_order_intents (
            ticker TEXT PRIMARY KEY,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            owner TEXT NOT NULL,
            status TEXT DEFAULT 'CLAIMED',
            order_id TEXT,
            client_order_id TEXT DEFAULT '',
            exchange TEXT DEFAULT 'NASD',
            filled_quantity REAL DEFAULT 0,
            baseline_quantity REAL DEFAULT 0,
            applied_quantity REAL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS satellite_order_intents (
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            owner TEXT NOT NULL,
            status TEXT DEFAULT 'CLAIMED',
            order_id TEXT DEFAULT '',
            client_order_id TEXT DEFAULT '',
            exchange TEXT DEFAULT 'NASD',
            filled_quantity REAL DEFAULT 0,
            baseline_quantity REAL DEFAULT 0,
            applied_quantity REAL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (ticker, side)
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS account_snapshot (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            total_equity_usd REAL NOT NULL,
            cash_available_usd REAL NOT NULL,
            stock_eval_usd REAL NOT NULL,
            realized_pnl_usd REAL DEFAULT 0,
            unrealized_pnl_usd REAL DEFAULT 0,
            unrealized_pnl_pct REAL DEFAULT NULL,
            unrealized_pnl_krw REAL DEFAULT NULL,
            total_pnl_usd REAL DEFAULT NULL,
            total_pnl_pct REAL DEFAULT NULL,
            usd_krw_rate REAL DEFAULT 1380,
            source TEXT,
            mode TEXT,
            updated_at TEXT NOT NULL
        )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_logs_ts ON execution_logs(timestamp DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trade_history_ticker ON trade_history(ticker);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_exit_cooldowns_until ON exit_cooldowns(lock_until);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_exit_cooldowns_day ON exit_cooldowns(trading_day);")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_date_ticker ON trades(date, ticker);")
        
        # Schema migration for existing databases
        try:
            cursor.execute("ALTER TABLE macro_history ADD COLUMN msi_score REAL DEFAULT 50.0")
        except Exception:
            pass
        for col_sql in (
            "ALTER TABLE account_snapshot ADD COLUMN unrealized_pnl_pct REAL DEFAULT NULL",
            "ALTER TABLE account_snapshot ADD COLUMN unrealized_pnl_krw REAL DEFAULT NULL",
            "ALTER TABLE account_snapshot ADD COLUMN total_pnl_usd REAL DEFAULT NULL",
            "ALTER TABLE account_snapshot ADD COLUMN total_pnl_pct REAL DEFAULT NULL",
            "ALTER TABLE my_portfolio ADD COLUMN max_gain_pct REAL DEFAULT 0",
            "ALTER TABLE my_portfolio ADD COLUMN peak_high REAL DEFAULT 0",
            "ALTER TABLE my_portfolio ADD COLUMN kijun_26 REAL DEFAULT 0",
            "ALTER TABLE my_portfolio ADD COLUMN atr_14 REAL DEFAULT 0",
            "ALTER TABLE my_portfolio ADD COLUMN trailing_floor REAL DEFAULT 0",
            "ALTER TABLE proxy_order_intents ADD COLUMN client_order_id TEXT DEFAULT ''",
            "ALTER TABLE proxy_order_intents ADD COLUMN exchange TEXT DEFAULT 'NASD'",
            "ALTER TABLE proxy_order_intents ADD COLUMN filled_quantity REAL DEFAULT 0",
            "ALTER TABLE proxy_order_intents ADD COLUMN baseline_quantity REAL DEFAULT 0",
            "ALTER TABLE proxy_order_intents ADD COLUMN applied_quantity REAL DEFAULT 0",
            "ALTER TABLE satellite_order_intents ADD COLUMN baseline_quantity REAL DEFAULT 0",
            "ALTER TABLE satellite_order_intents ADD COLUMN applied_quantity REAL DEFAULT 0",
        ):
            try:
                cursor.execute(col_sql)
            except Exception:
                pass
        conn.commit()
    _INITIALIZED_DBS.add(target_db)


def save_account_snapshot(snapshot: dict) -> None:
    """Persist last successful broker NAV/cash snapshot for live portfolio SSOT."""
    if not isinstance(snapshot, dict):
        return
    equity = float(snapshot.get("total_equity_usd") or 0.0)
    cash = float(snapshot.get("cash_available_usd") or 0.0)
    stock = float(snapshot.get("stock_eval_usd") or 0.0)
    if equity <= 0:
        return
    realized_val = float(snapshot.get("realized_pnl_usd") or 0.0)
    unrealized_val = float(snapshot.get("unrealized_pnl_usd") or 0.0)
    unrealized_pct = snapshot.get("unrealized_pnl_pct")
    if unrealized_pct is not None:
        try:
            unrealized_pct = round(float(unrealized_pct), 2)
        except Exception:
            unrealized_pct = None
    unrealized_krw = snapshot.get("unrealized_pnl_krw")
    if unrealized_krw is not None:
        try:
            unrealized_krw = round(float(unrealized_krw), 0)
        except Exception:
            unrealized_krw = None
    total_pnl_val = snapshot.get("total_pnl_usd")
    if total_pnl_val is not None:
        try:
            total_pnl_val = round(float(total_pnl_val), 2)
        except Exception:
            total_pnl_val = None
    total_pnl_pct = snapshot.get("total_pnl_pct")
    if total_pnl_pct is not None:
        try:
            total_pnl_pct = round(float(total_pnl_pct), 2)
        except Exception:
            total_pnl_pct = None

    fx_rate = float(snapshot.get("usd_krw_rate") or DEFAULT_USD_KRW_RATE) or DEFAULT_USD_KRW_RATE
    if abs(realized_val) > 5000.0:
        realized_val = round(realized_val / fx_rate, 2)
    if abs(unrealized_val) > 5000.0:
        unrealized_val = round(unrealized_val / fx_rate, 2)
    if total_pnl_val is not None and abs(total_pnl_val) > 5000.0:
        total_pnl_val = round(total_pnl_val / fx_rate, 2)
    init_database()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO account_snapshot (
                id, total_equity_usd, cash_available_usd, stock_eval_usd,
                realized_pnl_usd, unrealized_pnl_usd, unrealized_pnl_pct, unrealized_pnl_krw,
                total_pnl_usd, total_pnl_pct,
                usd_krw_rate, source, mode, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                total_equity_usd = excluded.total_equity_usd,
                cash_available_usd = excluded.cash_available_usd,
                stock_eval_usd = excluded.stock_eval_usd,
                realized_pnl_usd = excluded.realized_pnl_usd,
                unrealized_pnl_usd = excluded.unrealized_pnl_usd,
                unrealized_pnl_pct = excluded.unrealized_pnl_pct,
                unrealized_pnl_krw = excluded.unrealized_pnl_krw,
                total_pnl_usd = excluded.total_pnl_usd,
                total_pnl_pct = excluded.total_pnl_pct,
                usd_krw_rate = excluded.usd_krw_rate,
                source = excluded.source,
                mode = excluded.mode,
                updated_at = excluded.updated_at
            """,
            (
                equity,
                cash,
                stock,
                realized_val,
                unrealized_val,
                unrealized_pct,
                unrealized_krw,
                total_pnl_val,
                total_pnl_pct,
                fx_rate,
                str(snapshot.get("source") or "KIS"),
                str(snapshot.get("mode") or ""),
                str(snapshot.get("updated_at") or now_str),
            ),
        )
        conn.commit()


def load_account_snapshot() -> dict:
    init_database()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM account_snapshot WHERE id = 1").fetchone()
        if not row:
            return {}
        d = dict(row)
        fx_rate = float(d.get("usd_krw_rate") or DEFAULT_USD_KRW_RATE) or DEFAULT_USD_KRW_RATE
        if abs(float(d.get("realized_pnl_usd") or 0.0)) > 5000.0:
            d["realized_pnl_usd"] = round(float(d["realized_pnl_usd"]) / fx_rate, 2)
        if abs(float(d.get("unrealized_pnl_usd") or 0.0)) > 5000.0:
            d["unrealized_pnl_usd"] = round(float(d["unrealized_pnl_usd"]) / fx_rate, 2)
        if d.get("total_pnl_usd") is not None and abs(float(d.get("total_pnl_usd") or 0.0)) > 5000.0:
            d["total_pnl_usd"] = round(float(d["total_pnl_usd"]) / fx_rate, 2)
        return d


def clear_account_snapshot() -> None:
    init_database()
    with get_connection() as conn:
        conn.execute("DELETE FROM account_snapshot WHERE id = 1")
        conn.commit()


def save_macro_history_record(date_str: str, macro_climate: dict, macro_gauges: dict) -> None:
    init_database()
    vix = macro_gauges.get("vix", {})
    us10y = macro_gauges.get("us10y", {})
    wti = macro_gauges.get("wti", {})
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    shocks_str = ", ".join(macro_climate.get("external_shocks", []))
    msi_score = float(macro_climate.get("msi_score", 50.0)) if macro_climate.get("msi_score") is not None else 50.0
    
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT OR REPLACE INTO macro_history (
            date, vix_val, vix_status, us10y_val, us10y_status,
            wti_val, wti_status, macro_stance, macro_headline,
            macro_directive, external_shocks, msi_score, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            date_str,
            float(vix.get("val", 0)), str(vix.get("status", "NORMAL")),
            float(us10y.get("val", 0)), str(us10y.get("status", "NORMAL")),
            float(wti.get("val", 0)), str(wti.get("status", "NORMAL")),
            str(macro_climate.get("macro_stance", "DEFENSE_HOLD")),
            str(macro_climate.get("macro_headline", "")),
            str(macro_climate.get("macro_action_directive", "")),
            shocks_str, msi_score, now_str
        ))
        conn.commit()

def get_latest_macro_record() -> dict:
    init_database()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM macro_history ORDER BY date DESC LIMIT 1")
        row = cursor.fetchone()
        return dict(row) if row else None

def add_portfolio_buy(
    ticker: str,
    buy_price: float,
    quantity: float,
    buy_date: str = None,
    target_price: float = None,
    stop_loss_price: float = None,
    partial_tp_price: float = None,
    execution_log: dict = None,
) -> int:
    init_database()
    if not buy_date:
        buy_date = datetime.now().strftime("%Y-%m-%d")
        
    total_cost = buy_price * quantity
    eff_target_price = target_price if target_price is not None else derive_target_price(buy_price)
    eff_stop_loss_price = stop_loss_price if stop_loss_price is not None else derive_stop_price(buy_price)
    eff_partial_tp_price = partial_tp_price if partial_tp_price is not None else derive_partial_tp_price(buy_price)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with get_connection() as conn:
        cursor = conn.cursor()
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
            eff_target_price, eff_stop_loss_price, eff_partial_tp_price, now_str
        ))
        inserted_id = cursor.lastrowid
        if execution_log:
            record_execution_log(
                ticker=ticker,
                side="BUY",
                quantity=quantity,
                price=buy_price,
                connection=conn,
                **execution_log,
            )
        conn.commit()
        return inserted_id

def record_portfolio_sell(
    holding_id: int,
    sell_price: float,
    sell_date: str = None,
    reason: str = "MANUAL_SELL",
    execution_log: dict = None,
) -> bool:
    """Close the full remaining quantity of a portfolio lot."""
    return record_portfolio_sell_quantity(
        holding_id=holding_id,
        sell_price=sell_price,
        quantity=None,
        sell_date=sell_date,
        reason=reason,
        execution_log=execution_log,
    )


def _clean_trade_reason(reason: str) -> str:
    clean_reason = str(reason).strip()[:100] if reason else "MANUAL_SELL"
    if not REASON_REGEX.match(clean_reason):
        clean_reason = re.sub(
            r'[^A-Za-z0-9_\-\s\(\)가-힣.,%]',
            '',
            clean_reason,
        ).strip() or "MANUAL_SELL"
    return clean_reason[:100]


def _record_portfolio_sell_quantity_in_connection(
    conn: sqlite3.Connection,
    holding_id: int,
    sell_price: float,
    quantity: float = None,
    sell_date: str = "",
    reason: str = "MANUAL_SELL",
) -> float:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM my_portfolio WHERE id = ? AND status = 'HOLDING'",
        (holding_id,),
    )
    row = cursor.fetchone()
    if not row:
        return 0.0

    clean_reason = _clean_trade_reason(reason)
    buy_price = float(row['buy_price'])
    held_quantity = float(row['quantity'])
    sold_quantity = (
        held_quantity
        if quantity is None
        else min(held_quantity, max(0.0, float(quantity)))
    )
    if sold_quantity <= 0:
        return 0.0
    remaining_quantity = max(0.0, held_quantity - sold_quantity)
    is_full_exit = remaining_quantity <= 1e-9
    pnl_pct = ((sell_price - buy_price) / buy_price) * 100
    realized_pnl_amt = (sell_price - buy_price) * sold_quantity

    if is_full_exit:
        cursor.execute("""
        UPDATE my_portfolio
        SET status = 'SOLD', sell_date = ?, sell_price = ?, current_price = ?,
            current_value = ?, pnl_pct = ?, pnl_amount = ?, exit_advice = ?
        WHERE id = ?
        """, (
            sell_date, sell_price, sell_price,
            sell_price * sold_quantity, pnl_pct, realized_pnl_amt,
            f"청산 완료 ({pnl_pct:+.2f}%) - {clean_reason}", holding_id
        ))
    else:
        remaining_cost = buy_price * remaining_quantity
        remaining_value = sell_price * remaining_quantity
        remaining_pnl = (sell_price - buy_price) * remaining_quantity
        cursor.execute("""
        UPDATE my_portfolio
        SET quantity = ?, total_cost = ?, current_price = ?,
            current_value = ?, pnl_pct = ?, pnl_amount = ?,
            exit_advice = ?, status = 'HOLDING'
        WHERE id = ?
        """, (
            remaining_quantity, remaining_cost, sell_price,
            remaining_value, pnl_pct, remaining_pnl,
            (
                f"부분 매도 {sold_quantity:g}주 / 잔여 {remaining_quantity:g}주 "
                f"({pnl_pct:+.2f}%) - {clean_reason}"
            ),
            holding_id,
        ))

    cursor.execute("""
    INSERT INTO trade_history (
        holding_id, ticker, buy_date, sell_date, buy_price,
        sell_price, quantity, pnl_pct, pnl_amount, reason, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        holding_id, str(row['ticker']), str(row['buy_date']), sell_date, buy_price,
        sell_price, sold_quantity, pnl_pct, realized_pnl_amt, clean_reason,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    return sold_quantity


def record_portfolio_sell_quantity(
    holding_id: int,
    sell_price: float,
    quantity: float = None,
    sell_date: str = None,
    reason: str = "MANUAL_SELL",
    execution_log: dict = None,
) -> bool:
    """Atomically record a full/partial sell and optional execution audit."""
    init_database()
    effective_sell_date = sell_date or datetime.now().strftime("%Y-%m-%d")
    with get_connection() as conn:
        sold_quantity = _record_portfolio_sell_quantity_in_connection(
            conn=conn,
            holding_id=holding_id,
            sell_price=sell_price,
            quantity=quantity,
            sell_date=effective_sell_date,
            reason=reason,
        )
        if sold_quantity <= 0:
            return False
        if execution_log:
            ticker_row = conn.cursor().execute(
                "SELECT ticker FROM my_portfolio WHERE id = ?",
                (holding_id,),
            ).fetchone()
            record_execution_log(
                ticker=str(ticker_row["ticker"]),
                side="SELL",
                quantity=sold_quantity,
                price=sell_price,
                connection=conn,
                **execution_log,
            )
        conn.commit()
        return True


def record_portfolio_ticker_sell_quantity(
    ticker: str,
    sell_price: float,
    quantity: float,
    sell_date: str = None,
    reason: str = "PORTFOLIO_REBALANCE",
    execution_log: dict = None,
) -> float:
    """Atomically sell across active lots with one optional execution audit."""
    sym = str(ticker or "").upper().strip()
    remaining = max(0.0, float(quantity or 0.0))
    if not sym or remaining <= 0:
        return 0.0
    init_database()
    with get_connection() as conn:
        cursor = conn.cursor()
        lots = cursor.execute(
            """
            SELECT id, quantity
            FROM my_portfolio
            WHERE UPPER(ticker) = ? AND status = 'HOLDING'
            ORDER BY id ASC
            """,
            (sym,),
        ).fetchall()
        sold_total = 0.0
        effective_sell_date = sell_date or datetime.now().strftime("%Y-%m-%d")
        for lot in lots:
            if remaining <= 1e-9:
                break
            lot_qty = max(0.0, float(lot["quantity"] or 0.0))
            sell_qty = min(remaining, lot_qty)
            if sell_qty <= 0:
                continue
            sold = _record_portfolio_sell_quantity_in_connection(
                conn=conn,
                holding_id=int(lot["id"]),
                sell_price=sell_price,
                quantity=sell_qty,
                sell_date=effective_sell_date,
                reason=reason,
            )
            if sold <= 0:
                raise RuntimeError(
                    f"Failed to update local sell lot {lot['id']} for {sym}"
                )
            sold_total += sold
            remaining -= sold
        if execution_log and sold_total > 0:
            record_execution_log(
                ticker=sym,
                side="SELL",
                quantity=sold_total,
                price=sell_price,
                connection=conn,
                **execution_log,
            )
        conn.commit()
        return sold_total

def reset_all_holdings() -> bool:
    init_database()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM my_portfolio")
        conn.commit()
        return True

def get_live_portfolio() -> dict:
    """
    CQRS Read Query: Returns active portfolio holdings directly from SQLite.
    Strictly non-blocking: Zero network calls (no yf.download) and zero SQL write operations (no UPDATE).
    """
    from al_sangmoo.domain.risk.trailing_stop import (
        compute_trailing_floor,
        latch_peak_gain,
    )
    init_database()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM my_portfolio WHERE status = 'HOLDING' ORDER BY buy_date DESC, id DESC")
        rows = cursor.fetchall()
        holdings = [dict(r) for r in rows]
        
    total_invested = 0.0
    total_eval = 0.0
    
    for h in holdings:
        buy_price = float(h.get('buy_price', 0.0))
        quantity = float(h.get('quantity', 0.0))
        total_cost = float(h.get('total_cost', buy_price * quantity))
        cur_price = float(h.get('current_price', 0.0)) or buy_price
        cur_val = float(h.get('current_value', 0.0)) or (cur_price * quantity)
        pnl_pct = float(h.get('pnl_pct', 0.0))
        pnl_amt = float(h.get('pnl_amount', 0.0))
        
        h['buy_price'] = buy_price
        h['quantity'] = quantity
        h['total_cost'] = total_cost
        h['current_price'] = cur_price
        # C-2 Hard Stop (-7% EOD / -10% Emergency) and uncapped trailing floor (SSOT)
        stop_p = float(h.get('stop_loss_price') or derive_stop_price(buy_price))
        h['stop_loss_price'] = stop_p

        latched = latch_peak_gain(
            buy_price,
            cur_price,
            peak_high=float(h.get('peak_high') or 0.0),
            max_gain_pct=float(h.get('max_gain_pct') or 0.0),
        )
        h['peak_high'] = latched['peak_high']
        h['max_gain_pct'] = latched['max_gain_pct']
        h['is_trailing_active'] = latched['trailing_active']
        stored_floor = float(h.get('trailing_floor') or 0.0)
        if latched['trailing_active']:
            computed_floor = compute_trailing_floor(
                float(h.get('kijun_26') or 0.0),
                latched['peak_high'],
                float(h.get('atr_14') or 0.0),
            )
            h['trailing_floor'] = round(stored_floor or computed_floor or derive_target_price(buy_price), 2)
        else:
            h['trailing_floor'] = derive_target_price(buy_price)

        h['target_price'] = float(h.get('target_price', derive_target_price(buy_price)))
        h['partial_tp_price'] = float(h.get('partial_tp_price', derive_partial_tp_price(buy_price)))
        h['exit_advice'] = h.get('exit_advice') or "보유 지속 (v2 가디언 감시 중)"
        
        total_invested += total_cost
        total_eval += cur_val
        
    unrealized_pnl_usd = total_eval - total_invested
    unrealized_pnl_pct = ((unrealized_pnl_usd) / total_invested * 100) if total_invested > 0 else 0.0

    # Financial Cash & Equity Accounting
    # KIS output2 tot_evlu_amt is sometimes KRW / whole-account. Never treat it
    # as USD book, and never fall back to the $7,500 local ledger while live
    # lots exist — weights must be lot / (lots + broker cash).
    from al_sangmoo.domain.risk.cash_proxy import holdings_mark_usd, reconcile_overseas_nav

    usd_krw_rate = DEFAULT_USD_KRW_RATE
    holdings_eval = holdings_mark_usd(holdings) if holdings else total_eval
    if holdings_eval > 0:
        total_eval = holdings_eval
        unrealized_pnl_usd = total_eval - total_invested
        unrealized_pnl_pct = ((unrealized_pnl_usd) / total_invested * 100) if total_invested > 0 else 0.0

    snap = load_account_snapshot()
    broker_synced_at = str(snap.get("updated_at") or "") if snap else ""
    rec = reconcile_overseas_nav(
        holdings_eval=total_eval,
        equity=float(snap.get("total_equity_usd") or 0.0) if snap else 0.0,
        cash=float(snap.get("cash_available_usd") or 0.0) if snap else 0.0,
        stock_eval=float(snap.get("stock_eval_usd") or 0.0) if snap else 0.0,
        usd_krw_rate=usd_krw_rate,
    )

    rec_equity = float(rec.get("total_equity_usd") or 0.0)
    rec_source = str(rec.get("source") or "")
    use_rec = False
    equity_source = "LOCAL"
    if snap and rec_equity > 0 and rec.get("sane"):
        use_rec = True
        equity_source = "BROKER"
    elif snap and rec_equity > 0 and rec_source == "HOLDINGS" and holdings_eval > 1:
        use_rec = True
        equity_source = "HOLDINGS"
        if snap and not rec.get("sane"):
            try:
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                save_account_snapshot({
                    "total_equity_usd": rec["total_equity_usd"],
                    "cash_available_usd": rec["cash_available_usd"],
                    "stock_eval_usd": rec["stock_eval_usd"],
                    "realized_pnl_usd": float(snap.get("realized_pnl_usd") or 0.0),
                    "unrealized_pnl_usd": unrealized_pnl_usd,
                    "source": "HOLDINGS",
                    "mode": str(snap.get("mode") or "VIRTUAL_PAPER"),
                    "updated_at": now_str,
                })
                broker_synced_at = now_str
            except Exception:
                pass

    if snap and not use_rec:
        try:
            clear_account_snapshot()
        except Exception:
            pass
        broker_synced_at = ""

    realized_pnl_usd = 0.0
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(pnl_amount) FROM trade_history")
        row = cursor.fetchone()
        if row and row[0] is not None:
            realized_pnl_usd = float(row[0])

    total_pnl_usd = realized_pnl_usd + unrealized_pnl_usd
    unrealized_pnl_krw = 0.0
    base_account_usd = DEFAULT_BASE_ACCOUNT_USD
    # Sleeve Ledger Cash (Canonical SSOT for $7,500.00 / 10,000,000 KRW account):
    # Free cash = Initial Capital - Total Cost Basis of active lots + Cumulative Realized PnL
    sleeve_cash_usd = max(0.0, round(base_account_usd - total_invested + realized_pnl_usd, 2))

    delta_eval = 0.0
    if use_rec:
        free_cash_usd = max(0.0, float(rec.get("cash_available_usd") or 0.0))
        snap_stock_eval = float(rec.get("stock_eval_usd") or 0.0)
        snap_equity = float(rec.get("total_equity_usd") or 0.0)

        # When live holdings exist and evaluate > 0, live marked prices are the SSOT.
        # Fall back to snapshot evaluation only if live holdings are absent.
        if holdings and holdings_eval > 0:
            total_eval = holdings_eval
        elif snap_stock_eval > 0:
            total_eval = snap_stock_eval

        delta_eval = round(total_eval - snap_stock_eval, 2) if snap_stock_eval > 0 else 0.0

        # Exact baseline when delta_eval == 0.0 (e.g. at snapshot reconciliation time or in baseline tests);
        # Otherwise recalculate live unrealized PnL from live evaluation.
        if delta_eval == 0.0 and snap and snap.get("unrealized_pnl_pct") is not None and float(snap.get("unrealized_pnl_pct") or 0.0) != 0.0:
            unrealized_pnl_pct = float(snap["unrealized_pnl_pct"])
            if snap.get("unrealized_pnl_usd") is not None and float(snap.get("unrealized_pnl_usd") or 0.0) != 0.0:
                unrealized_pnl_usd = float(snap["unrealized_pnl_usd"])
            else:
                unrealized_pnl_usd = round(total_eval - total_invested, 2)
        else:
            unrealized_pnl_usd = round(total_eval - total_invested, 2)
            unrealized_pnl_pct = round(((unrealized_pnl_usd) / total_invested * 100.0), 2) if total_invested > 0 else 0.0

        if delta_eval == 0.0 and snap and snap.get("unrealized_pnl_krw") is not None and float(snap.get("unrealized_pnl_krw") or 0.0) != 0.0:
            unrealized_pnl_krw = float(snap["unrealized_pnl_krw"])
        else:
            unrealized_pnl_krw = round(unrealized_pnl_usd * usd_krw_rate, 2)

        # Broker/HOLDINGS snapshot is the cash + NAV SSOT.
        # Total equity updates dynamically with real-time stock valuation delta.
        if snap_equity > 0:
            total_equity_usd = round(snap_equity + delta_eval, 2)
        else:
            total_equity_usd = round(total_eval + free_cash_usd, 2)
        total_pnl_usd = realized_pnl_usd + unrealized_pnl_usd
    else:
        equity_source = "LOCAL"
        free_cash_usd = sleeve_cash_usd
        total_equity_usd = round(total_eval + free_cash_usd, 2)
        total_pnl_usd = realized_pnl_usd + unrealized_pnl_usd
        unrealized_pnl_krw = round(unrealized_pnl_usd * usd_krw_rate, 2)

    if equity_source == "BROKER" and snap:
        lot_pnl = round(float(total_eval) - float(total_invested), 2)
        # KIS MTS tot_pftrt / tot_evlu_pfls is persisted as total_pnl_* when
        # available, otherwise as unrealized_pnl_* from the broker adapter.
        snap_pnl_val = snap.get("total_pnl_usd")
        if snap_pnl_val is not None:
            snap_pnl = float(snap_pnl_val)
        elif snap.get("unrealized_pnl_usd") is not None:
            snap_pnl = float(snap["unrealized_pnl_usd"])
        else:
            snap_pnl = lot_pnl

        snap_eq = float(snap.get("total_equity_usd") or (snap_stock_eval + free_cash_usd if snap_stock_eval > 0 else total_equity_usd))
        base_account_usd = round(snap_eq - snap_pnl, 2)
        if base_account_usd <= 0:
            base_account_usd = DEFAULT_BASE_ACCOUNT_USD

        snap_pct_val = snap.get("total_pnl_pct")
        if snap_pct_val is not None:
            snap_pct = float(snap_pct_val)
        elif snap.get("unrealized_pnl_pct") is not None:
            snap_pct = float(snap["unrealized_pnl_pct"])
        elif base_account_usd > 0:
            snap_pct = snap_pnl / base_account_usd * 100.0
        else:
            snap_pct = 0.0

        if delta_eval == 0.0:
            cumulative_pnl_usd = snap_pnl
            total_cumulative_pnl_pct = snap_pct
        else:
            cumulative_pnl_usd = round(snap_pnl + delta_eval, 2)
            delta_pct = (delta_eval / base_account_usd * 100.0) if base_account_usd > 0 else 0.0
            total_cumulative_pnl_pct = round(snap_pct + delta_pct, 2)
    else:
        cumulative_pnl_usd = round(total_equity_usd - base_account_usd, 2)
        total_cumulative_pnl_pct = (
            (cumulative_pnl_usd / base_account_usd * 100.0)
            if base_account_usd > 0 else 0.0
        )
    realized_pnl_pct = (realized_pnl_usd / base_account_usd * 100.0) if base_account_usd > 0 else 0.0
    free_cash_krw = int(free_cash_usd * usd_krw_rate)
    total_equity_krw = int(total_equity_usd * usd_krw_rate)
    cash_ratio_pct = (free_cash_usd / total_equity_usd * 100.0) if total_equity_usd > 0 else 100.0

    return {
        "holdings": holdings,
        "total_invested": round(total_invested, 2),
        "total_eval": round(total_eval, 2),
        "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
        "unrealized_pnl_amount": round(unrealized_pnl_usd, 2),
        "unrealized_pnl_krw": int(round(unrealized_pnl_krw)),
        "realized_pnl_pct": round(realized_pnl_pct, 2),
        "realized_pnl_amount": round(realized_pnl_usd, 2),
        "overall_pnl_pct": round(total_cumulative_pnl_pct, 2),
        "overall_pnl_amount": round(cumulative_pnl_usd, 2),
        "total_equity_usd": round(total_equity_usd, 2),
        "total_equity_krw": total_equity_krw,
        "free_cash_usd": round(free_cash_usd, 2),
        "free_cash_krw": free_cash_krw,
        "cash_ratio_pct": round(cash_ratio_pct, 1),
        "usd_krw_rate": usd_krw_rate,
        "initial_capital_usd": round(base_account_usd, 2),
        "base_account_usd": round(base_account_usd, 2),
        "equity_source": equity_source,
        "broker_synced_at": broker_synced_at or None,
        "active_slot_count": len(holdings),
        "available_slots": max(0, 3 - len(holdings))
    }

def sync_portfolio_prices() -> dict:
    """
    CQRS Command / Sync Worker: Updates market prices and recalculates PnL for active holding positions.
    Reads local chart cache and falls back to yf.download, then performs batch UPDATE on my_portfolio.
    Returns updated portfolio dict.
    """
    init_database()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM my_portfolio WHERE status = 'HOLDING' ORDER BY buy_date DESC, id DESC")
        holdings = [dict(r) for r in cursor.fetchall()]
        
    if not holdings:
        return get_live_portfolio()
        
    unique_tickers = list(set(h['ticker'] for h in holdings))
    prices_map = {}
    missing_tickers = []
    
    # 1. Primary Source: KIS Live Price (Ultra-fast 0.1s real-time quote via TR HHDFS00000300)
    try:
        from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
        if default_kis_broker.is_configured():
            for tk in unique_tickers:
                live_p = default_kis_broker.get_live_price(tk)
                if live_p is not None and live_p > 0:
                    prices_map[tk] = live_p
    except Exception as e:
        pass

    # 2. Secondary Fallback: Local chart cache
    for tk in unique_tickers:
        if tk in prices_map:
            continue
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
            
    # 3. Tertiary Fallback: Yahoo Finance
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

                    
    update_rows = []
    for h in holdings:
        ticker = h['ticker']
        buy_price = float(h['buy_price'])
        quantity = float(h['quantity'])
        total_cost = buy_price * quantity
        cur_price = prices_map.get(ticker, float(h.get('current_price') or buy_price))
        
        cur_val = cur_price * quantity
        pnl_pct = ((cur_price - buy_price) / buy_price) * 100 if buy_price > 0 else 0.0
        pnl_amt = cur_val - total_cost
        
        if pnl_pct <= -HARD_STOP_PCT:
            advice = f"칼손절 긴급 매도 권고 (손절선 이탈 {pnl_pct:+.2f}%)"
        elif pnl_pct >= TRAILING_ACTIVATE_PCT:
            advice = f"무제한 트레일링 익절 홀딩 (Let Winners Run, {pnl_pct:+.2f}%)"
        else:
            advice = (
                f"보유 지속 (손절선 ${derive_stop_price(buy_price):,.2f} / "
                f"+{TRAILING_ACTIVATE_PCT:.0f}% 이후 무제한 트레일링)"
            )
            
        update_rows.append((cur_price, cur_val, pnl_pct, pnl_amt, advice, h['id']))
        
    if update_rows:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany("""
            UPDATE my_portfolio
            SET current_price = ?, current_value = ?, pnl_pct = ?, pnl_amount = ?, exit_advice = ?
            WHERE id = ? AND status = 'HOLDING'
            """, update_rows)
            conn.commit()
            
    return get_live_portfolio()

def get_trade_history_records(limit: int = 100) -> list:
    """Returns closed trade history and realized returns from SQLite SSOT."""
    init_database()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT id, holding_id, ticker, buy_date, sell_date, buy_price,
               sell_price, quantity, pnl_pct, pnl_amount, reason, created_at
        FROM trade_history
        ORDER BY id DESC
        LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        return [
            {
                "id": r[0],
                "holding_id": r[1],
                "ticker": r[2],
                "buy_date": r[3],
                "sell_date": r[4],
                "buy_price": r[5],
                "sell_price": r[6],
                "quantity": r[7],
                "pnl_pct": r[8],
                "pnl_amount": r[9],
                "reason": r[10],
                "created_at": r[11]
            }
            for r in rows
        ]

def _pick_px(p) -> float:
    if not isinstance(p, dict):
        return 0.0
    for key in ("close", "price", "latest_close"):
        val = p.get(key)
        if val is None or val == "":
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return 0.0


def save_recommendation_matrix_record(date_str: str, bull_picks: list, neutral_picks: list, bear_picks: list) -> None:
    init_database()
    b1 = bull_picks[0] if len(bull_picks) > 0 else {"ticker": "-", "close": 0}
    b2 = bull_picks[1] if len(bull_picks) > 1 else {"ticker": "-", "close": 0}
    n1 = neutral_picks[0] if len(neutral_picks) > 0 else {"ticker": "-", "close": 0}
    n2 = neutral_picks[1] if len(neutral_picks) > 1 else {"ticker": "-", "close": 0}
    s1 = bear_picks[0] if len(bear_picks) > 0 else {"ticker": "-", "close": 0}
    s2 = bear_picks[1] if len(bear_picks) > 1 else {"ticker": "-", "close": 0}
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT OR REPLACE INTO recommendation_matrix (
            date, bull_1, bull_1_price, bull_2, bull_2_price,
            neutral_1, neutral_1_price, neutral_2, neutral_2_price,
            bear_1, bear_1_price, bear_2, bear_2_price, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            date_str, b1.get('ticker', '-'), _pick_px(b1), b2.get('ticker', '-'), _pick_px(b2),
            n1.get('ticker', '-'), _pick_px(n1), n2.get('ticker', '-'), _pick_px(n2),
            s1.get('ticker', '-'), _pick_px(s1), s2.get('ticker', '-'), _pick_px(s2), now_str
        ))
        conn.commit()

def get_recommendations_matrix() -> list:
    init_database()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM recommendation_matrix ORDER BY date DESC")
        rows = [dict(r) for r in cursor.fetchall()]
        return rows

def archive_daily_recommendations(today_str: str, dual_consensus: list, strat1_exclusive: list, strat2_exclusive: list) -> int:
    """
    Atomically archives daily recommendations into the trades table.
    Enforces context management with guaranteed commit, close, and returned record count.
    """
    init_database()
    saved_count = 0
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    all_recs = (
        [(d, "DUAL_5_STAR") for d in dual_consensus] +
        [(p, "STRAT1_PULLBACK") for p in strat1_exclusive] +
        [(s, "STRAT2_SNIPER") for s in strat2_exclusive]
    )
    
    with get_connection() as conn:
        cursor = conn.cursor()
        for item, rec_type in all_recs:
            tk = item["ticker"]
            price = float(item["price"])
            tgt_p = float(item.get("target_price", derive_target_price(price)))
            stop_p = float(item.get("stop_price", derive_stop_price(price)))
            part_p = derive_partial_tp_price(price)
            
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
        conn.commit()
    return saved_count

def get_recommendation_streaks() -> dict:
    """
    Computes consecutive active recommendation days (streaks) for each ticker.
    Returns a dict mapping ticker -> integer streak count (>= 1).
    """
    init_database()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT date, ticker FROM trades ORDER BY date DESC")
        rows = cursor.fetchall()
    
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
    m_stances = {}
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT date, type, ticker, entry_price FROM trades ORDER BY date DESC, id ASC")
        rows = cursor.fetchall()
        
        # Fetch macro stances from macro_history if available
        try:
            cursor.execute("SELECT date, macro_stance, msi_score FROM macro_history")
            for r in cursor.fetchall():
                msi_val = float(r['msi_score']) if r['msi_score'] is not None else 50.0
                m_stances[r['date']] = f"{r['macro_stance']} ({msi_val:.1f}pt)"
        except Exception:
            pass
    
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

def record_execution_log(
    ticker: str,
    side: str,
    quantity: float,
    price: float,
    order_type: str = "MARKETABLE_LIMIT",
    status: str = "FILLED",
    message: str = "",
    order_id: str = "",
    timestamp: str = None,
    connection: sqlite3.Connection = None,
) -> int:
    """Records an executed broker or terminal trade log for the real-time audit trail."""
    ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tot_amt = round(float(price) * float(quantity), 2)
    params = (
        ts, ticker.upper(), side.upper(), float(quantity), float(price),
        tot_amt, str(order_type), str(status), str(message), str(order_id)
    )

    def _insert(conn: sqlite3.Connection) -> int:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO execution_logs (
            timestamp, ticker, side, quantity, price, total_amount,
            order_type, status, message, order_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, params)
        return int(cursor.lastrowid)

    if connection is not None:
        return _insert(connection)

    init_database()
    with get_connection() as conn:
        inserted_id = _insert(conn)
        conn.commit()
        return inserted_id

def get_execution_logs(limit: int = 300) -> list:
    """Returns the most recent execution logs, newest first."""
    init_database()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM execution_logs ORDER BY timestamp DESC, id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def claim_autopilot_entry_session(
    session_date: str,
    source: str = "SCHEDULER",
    claimed_at: str = None,
) -> bool:
    """
    Atomically claim the one allowed satellite-entry cycle for a US session.

    The primary key makes this safe across process restarts and multiple workers.
    A crash after claiming intentionally fails closed: missing one entry is safer
    than replaying a full multi-slot buy cycle.
    """
    init_database()
    day = str(session_date or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("session_date must use YYYY-MM-DD")
    ts = claimed_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO autopilot_entry_sessions (
                session_date, claimed_at, source
            ) VALUES (?, ?, ?)
            """,
            (day, ts, str(source or "SCHEDULER")),
        )
        claimed = cursor.rowcount == 1
        conn.commit()
        return claimed


def has_autopilot_entry_session(session_date: str) -> bool:
    """Return whether the daily satellite-entry cycle was already claimed."""
    init_database()
    with get_connection() as conn:
        row = conn.cursor().execute(
            "SELECT 1 FROM autopilot_entry_sessions WHERE session_date = ? LIMIT 1",
            (str(session_date or "").strip(),),
        ).fetchone()
        return row is not None


def claim_proxy_order_intent(
    ticker: str,
    side: str,
    quantity: float,
    price: float,
    owner: str,
    exchange: str = "NASD",
    client_order_id: str = "",
    baseline_quantity: float = 0.0,
) -> bool:
    """Atomically reserve one proxy ticker across daemons and processes."""
    init_database()
    sym = str(ticker or "").upper().strip()
    direction = str(side or "").upper().strip()
    if not sym or direction not in {"BUY", "SELL"}:
        return False
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cutoff_12h = (datetime.now() - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_300s = (datetime.now() - timedelta(seconds=300)).strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            DELETE FROM proxy_order_intents
            WHERE ticker = ?
              AND (
                created_at <= ?
                OR status IN ('CANCELLED', 'REJECTED', 'NOT_FOUND', 'FILLED')
                OR (status = 'CLAIMED' AND (order_id IS NULL OR order_id = '') AND created_at <= ?)
              )
            """,
            (sym, cutoff_12h, cutoff_300s),
        )
        cursor.execute(
            """
            INSERT OR IGNORE INTO proxy_order_intents (
                ticker, side, quantity, price, owner, status,
                order_id, client_order_id, exchange, filled_quantity,
                baseline_quantity, applied_quantity, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'CLAIMED', '', ?, ?, 0, ?, 0, ?, ?)
            """,
            (
                sym,
                direction,
                float(quantity),
                float(price),
                str(owner or "UNKNOWN"),
                str(client_order_id or ""),
                str(exchange or "NASD"),
                max(0.0, float(baseline_quantity or 0.0)),
                now_str,
                now_str,
            ),
        )
        claimed = cursor.rowcount == 1
        conn.commit()
        return claimed


def mark_proxy_order_submitted(
    ticker: str,
    order_id: str = "",
    client_order_id: str = "",
) -> None:
    """Persist broker acceptance until reconciliation observes the fill."""
    init_database()
    with get_connection() as conn:
        conn.cursor().execute(
            """
            UPDATE proxy_order_intents
            SET status = 'SUBMITTED', order_id = ?, client_order_id = ?,
                updated_at = ?
            WHERE ticker = ?
            """,
            (
                str(order_id or ""),
                str(client_order_id or ""),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                str(ticker or "").upper().strip(),
            ),
        )
        conn.commit()


def get_proxy_order_intents() -> list:
    init_database()
    with get_connection() as conn:
        return [
            dict(row)
            for row in conn.cursor().execute(
                "SELECT * FROM proxy_order_intents ORDER BY created_at"
            ).fetchall()
        ]


def update_proxy_order_intent_state(
    ticker: str,
    status: str,
    filled_quantity: float,
    applied_quantity: float = 0.0,
    connection: sqlite3.Connection = None,
) -> None:
    params = (
        str(status or "UNKNOWN"),
        max(0.0, float(filled_quantity or 0.0)),
        max(0.0, float(applied_quantity or 0.0)),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        str(ticker or "").upper().strip(),
    )
    sql = """
        UPDATE proxy_order_intents
        SET status = ?, filled_quantity = ?, applied_quantity = ?,
            updated_at = ?
        WHERE ticker = ?
    """
    if connection is not None:
        connection.cursor().execute(sql, params)
        return
    init_database()
    with get_connection() as conn:
        conn.cursor().execute(sql, params)
        conn.commit()


def clear_proxy_order_intent(
    ticker: str,
    connection: sqlite3.Connection = None,
) -> None:
    """Release a proxy reservation after fill, rejection, or reconciliation."""
    sym = str(ticker or "").upper().strip()
    if connection is not None:
        connection.cursor().execute(
            "DELETE FROM proxy_order_intents WHERE ticker = ?",
            (sym,),
        )
        return
    init_database()
    with get_connection() as conn:
        conn.cursor().execute(
            "DELETE FROM proxy_order_intents WHERE ticker = ?",
            (sym,),
        )
        conn.commit()


def claim_satellite_order_intent(
    ticker: str,
    side: str,
    quantity: float,
    price: float,
    owner: str,
    exchange: str = "NASD",
    client_order_id: str = "",
    baseline_quantity: float = 0.0,
) -> bool:
    """Durable reservation for Guardian/manual satellite broker orders."""
    init_database()
    sym = str(ticker or "").upper().strip()
    direction = str(side or "").upper().strip()
    if not sym or direction not in {"BUY", "SELL"}:
        return False
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cutoff_12h = (datetime.now() - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_300s = (datetime.now() - timedelta(seconds=300)).strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            DELETE FROM satellite_order_intents
            WHERE ticker = ? AND side = ?
              AND (
                created_at <= ?
                OR status IN ('CANCELLED', 'REJECTED', 'NOT_FOUND', 'FILLED')
                OR (status = 'CLAIMED' AND (order_id IS NULL OR order_id = '') AND created_at <= ?)
              )
            """,
            (sym, direction, cutoff_12h, cutoff_300s),
        )
        cursor.execute(
            """
            INSERT OR IGNORE INTO satellite_order_intents (
                ticker, side, quantity, price, owner, status, order_id,
                client_order_id, exchange, filled_quantity,
                baseline_quantity, applied_quantity, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'CLAIMED', '', ?, ?, 0, ?, 0, ?, ?)
            """,
            (
                sym,
                direction,
                float(quantity),
                float(price),
                str(owner or "UNKNOWN"),
                str(client_order_id or ""),
                str(exchange or "NASD"),
                max(0.0, float(baseline_quantity or 0.0)),
                now_str,
                now_str,
            ),
        )
        claimed = cursor.rowcount == 1
        conn.commit()
        return claimed


def mark_satellite_order_submitted(
    ticker: str,
    side: str,
    order_id: str = "",
    client_order_id: str = "",
) -> None:
    init_database()
    with get_connection() as conn:
        conn.cursor().execute(
            """
            UPDATE satellite_order_intents
            SET status = 'SUBMITTED', order_id = ?, client_order_id = ?,
                updated_at = ?
            WHERE ticker = ? AND side = ?
            """,
            (
                str(order_id or ""),
                str(client_order_id or ""),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                str(ticker or "").upper().strip(),
                str(side or "").upper().strip(),
            ),
        )
        conn.commit()


def get_satellite_order_intents() -> list:
    init_database()
    with get_connection() as conn:
        return [
            dict(row)
            for row in conn.cursor().execute(
                "SELECT * FROM satellite_order_intents ORDER BY created_at"
            ).fetchall()
        ]


def update_satellite_order_intent_state(
    ticker: str,
    side: str,
    status: str,
    filled_quantity: float,
    applied_quantity: float = 0.0,
    connection: sqlite3.Connection = None,
) -> None:
    params = (
        str(status or "UNKNOWN"),
        max(0.0, float(filled_quantity or 0.0)),
        max(0.0, float(applied_quantity or 0.0)),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        str(ticker or "").upper().strip(),
        str(side or "").upper().strip(),
    )
    sql = """
        UPDATE satellite_order_intents
        SET status = ?, filled_quantity = ?, applied_quantity = ?,
            updated_at = ?
        WHERE ticker = ? AND side = ?
    """
    if connection is not None:
        connection.cursor().execute(sql, params)
        return
    init_database()
    with get_connection() as conn:
        conn.cursor().execute(sql, params)
        conn.commit()


def clear_satellite_order_intent(
    ticker: str,
    side: str,
    connection: sqlite3.Connection = None,
) -> None:
    params = (
        str(ticker or "").upper().strip(),
        str(side or "").upper().strip(),
    )
    sql = "DELETE FROM satellite_order_intents WHERE ticker = ? AND side = ?"
    if connection is not None:
        connection.cursor().execute(sql, params)
        return
    init_database()
    with get_connection() as conn:
        conn.cursor().execute(sql, params)
        conn.commit()

# Aliases
get_db = get_connection
init_db = init_database
close_portfolio_position = record_portfolio_sell
clear_portfolio = reset_all_holdings

