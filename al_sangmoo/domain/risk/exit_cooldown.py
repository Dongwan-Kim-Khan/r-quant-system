"""
AL-SANGMOO QUANT TERMINAL: RE-ENTRY COOLDOWN REGISTRY (SSOT)

SQLite-backed registry that records forced/reconciled exits so Autopilot will
not re-buy a ticker after a process restart or server crash.

The lock is deliberately 24 hours. Daily satellite scheduling separately limits
new entries to one cycle near the next US regular-session open.
"""

import threading
import time
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from typing import List, Optional

from al_sangmoo.core.market_time import us_equity_session_date
from al_sangmoo.infrastructure.persistence import get_connection, init_database

# A wall-clock backstop in addition to the same-session/account-level lockout.
RE_ENTRY_COOLDOWN_SEC = 24.0 * 60.0 * 60.0

# Guardian exit actions that arm a re-entry cooldown.
COOLDOWN_EXIT_ACTIONS = ("AUTO_STOP_LOSS", "AUTO_KIJUN_EXIT", "AUTO_TRAILING_TP")

_lock = threading.RLock()
SATELLITE_TRANSITION_MUTEX = threading.RLock()


def serialized_satellite_transition(func):
    """Serialize synchronous entry/exit/reconciliation state transitions."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        with SATELLITE_TRANSITION_MUTEX:
            return func(*args, **kwargs)
    return wrapper


def serialized_async_satellite_transition(func):
    """Async counterpart used by the Autopilot entry command."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        with SATELLITE_TRANSITION_MUTEX:
            return await func(*args, **kwargs)
    return wrapper


def record_exit(
    ticker: str,
    reason: str = "",
    ts: Optional[float] = None,
    source: str = "GUARDIAN",
    order_id: str = "",
    window_sec: float = RE_ENTRY_COOLDOWN_SEC,
    connection: Optional[sqlite3.Connection] = None,
) -> None:
    """Persist a ticker exit and its same-day/24-hour re-entry lock."""
    sym = str(ticker or "").upper().strip()
    if not sym:
        return
    stamp = float(ts if ts is not None else time.time())
    lock_until = stamp + max(0.0, float(window_sec))
    trading_day = us_equity_session_date(
        datetime.fromtimestamp(stamp, tz=timezone.utc)
    )
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    params = (
        sym,
        stamp,
        lock_until,
        trading_day,
        str(reason or ""),
        str(source or "GUARDIAN"),
        str(order_id or ""),
        updated_at,
    )

    def _upsert(conn: sqlite3.Connection) -> None:
        conn.cursor().execute(
            """
            INSERT INTO exit_cooldowns (
                ticker, exited_at, lock_until, trading_day,
                reason, source, order_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                exited_at = excluded.exited_at,
                lock_until = excluded.lock_until,
                trading_day = excluded.trading_day,
                reason = excluded.reason,
                source = excluded.source,
                order_id = excluded.order_id,
                updated_at = excluded.updated_at
            WHERE excluded.exited_at >= exit_cooldowns.exited_at
            """,
            params,
        )

    with _lock:
        if connection is not None:
            _upsert(connection)
            return
        init_database()
        with get_connection() as conn:
            _upsert(conn)
            conn.commit()


def _get_exit(ticker: str):
    init_database()
    with get_connection() as conn:
        return conn.cursor().execute(
            """
            SELECT ticker, exited_at, lock_until, trading_day, reason, source, order_id
            FROM exit_cooldowns
            WHERE ticker = ?
            """,
            (ticker,),
        ).fetchone()


def seconds_remaining(
    ticker: str,
    now: Optional[float] = None,
    window_sec: float = RE_ENTRY_COOLDOWN_SEC,
) -> float:
    """Seconds left on ``ticker``'s cooldown (0.0 if not cooling down)."""
    sym = str(ticker or "").upper().strip()
    if not sym:
        return 0.0
    now_ts = float(now if now is not None else time.time())
    with _lock:
        rec = _get_exit(sym)
    if not rec:
        return 0.0
    if float(window_sec) == RE_ENTRY_COOLDOWN_SEC:
        deadline = float(rec["lock_until"])
    else:
        deadline = float(rec["exited_at"]) + max(0.0, float(window_sec))
    remaining = deadline - now_ts
    return remaining if remaining > 0.0 else 0.0


def is_in_cooldown(
    ticker: str,
    now: Optional[float] = None,
    window_sec: float = RE_ENTRY_COOLDOWN_SEC,
) -> bool:
    """True when ``ticker`` was force-exited within the cooldown window."""
    return seconds_remaining(ticker, now=now, window_sec=window_sec) > 0.0


def cooldown_tickers(
    now: Optional[float] = None,
    window_sec: float = RE_ENTRY_COOLDOWN_SEC,
) -> List[str]:
    """All tickers currently inside the re-entry cooldown window."""
    now_ts = float(now if now is not None else time.time())
    with _lock:
        init_database()
        with get_connection() as conn:
            if float(window_sec) == RE_ENTRY_COOLDOWN_SEC:
                rows = conn.cursor().execute(
                    "SELECT ticker FROM exit_cooldowns WHERE lock_until > ? ORDER BY ticker",
                    (now_ts,),
                ).fetchall()
            else:
                rows = conn.cursor().execute(
                    """
                    SELECT ticker
                    FROM exit_cooldowns
                    WHERE (exited_at + ?) > ?
                    ORDER BY ticker
                    """,
                    (max(0.0, float(window_sec)), now_ts),
                ).fetchall()
    return [str(row["ticker"]) for row in rows]


def has_session_exit(session_date: str) -> bool:
    """True when any satellite exit was recorded for the US trading session."""
    day = str(session_date or "").strip()
    if not day:
        return False
    with _lock:
        init_database()
        with get_connection() as conn:
            row = conn.cursor().execute(
                "SELECT 1 FROM exit_cooldowns WHERE trading_day = ? LIMIT 1",
                (day,),
            ).fetchone()
            return row is not None


def clear() -> None:
    """Delete the persistent registry. Intended for isolated test databases."""
    with _lock:
        init_database()
        with get_connection() as conn:
            conn.cursor().execute("DELETE FROM exit_cooldowns")
            conn.commit()
