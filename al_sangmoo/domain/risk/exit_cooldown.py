"""
AL-SANGMOO QUANT TERMINAL: RE-ENTRY COOLDOWN REGISTRY (SSOT)

Thread-safe shared registry that records Guardian-forced exits
(hard stop-loss / 26D kijun breakdown / trailing take-profit) so the Autopilot
will NOT immediately re-buy a ticker that was just force-liquidated.

This directly prevents the "buy -> instant Guardian exit -> re-buy" whipsaw that
liquidated DIS 1m52s after entry on 2026-09-11.
"""

import threading
import time
from typing import Dict, List, Optional

# Re-entry is blocked for 30 minutes after a forced exit.
RE_ENTRY_COOLDOWN_SEC = 1800.0

# Guardian exit actions that arm a re-entry cooldown.
COOLDOWN_EXIT_ACTIONS = ("AUTO_STOP_LOSS", "AUTO_KIJUN_EXIT", "AUTO_TRAILING_TP")

_lock = threading.Lock()
_exits: Dict[str, Dict[str, object]] = {}


def record_exit(ticker: str, reason: str = "", ts: Optional[float] = None) -> None:
    """Register a forced exit for ``ticker`` so re-entry is blocked for the window."""
    sym = str(ticker or "").upper().strip()
    if not sym:
        return
    stamp = float(ts if ts is not None else time.time())
    with _lock:
        _exits[sym] = {"ts": stamp, "reason": str(reason or "")}


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
        rec = _exits.get(sym)
    if not rec:
        return 0.0
    elapsed = now_ts - float(rec["ts"])
    remaining = float(window_sec) - elapsed
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
        items = list(_exits.items())
    active = [
        sym
        for sym, rec in items
        if (now_ts - float(rec["ts"])) < float(window_sec)
    ]
    return sorted(active)


def clear() -> None:
    """Reset the registry (test isolation)."""
    with _lock:
        _exits.clear()
