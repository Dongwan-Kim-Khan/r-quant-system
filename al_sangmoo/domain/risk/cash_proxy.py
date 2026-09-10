"""
AL-SANGMOO QUANT TERMINAL: CASH PROXY SLEEVE SSOT

Single Source of Truth for the QLD/QQQ "cash proxy" sleeve that parks idle cash
between satellite entries. Encodes the anti-whipsaw rules mandated by the
C1-M2 critical patch:

  1. Execution-order SSOT  -> never park cash until the satellite entry search
                              has fully finished (no buy/sell on the same tick).
  2. Sell cooldown         -> a freshly-bought proxy may not be dumped for at
                              least 300s, killing the 20-second buy->sell->buy churn.
  3. Rebalance deadband    -> ignore drift below 3 shares AND below $300 notional.
  4. Single authority      -> Guardian and Autopilot cannot both fire proxy orders
                              concurrently (shared mutex + authority token).

All decision functions here are pure/deterministic and unit-testable; the live
order loop consumes these rules rather than re-implementing them.
"""

import threading
import time
from typing import Dict, List, Optional

PROXY_TICKERS = ("QLD", "QQQ")

# A proxy bought within this window must not be sold to fund a satellite entry.
PROXY_SELL_COOLDOWN_SEC = 300.0

# Rebalancing noise thresholds: an order fires only when BOTH are exceeded.
REBALANCE_DEADBAND_SHARES = 3
REBALANCE_DEADBAND_USD = 300.0

# Shared mutex so only one daemon issues cash-proxy orders at a time.
PROXY_ORDER_MUTEX = threading.RLock()


def is_proxy_ticker(ticker: str) -> bool:
    """True when ``ticker`` is a managed cash-proxy instrument (QLD/QQQ)."""
    return str(ticker or "").upper().strip() in PROXY_TICKERS


def proxy_rebalance_action(
    current_qty: float,
    target_qty: float,
    price: float,
    deadband_shares: int = REBALANCE_DEADBAND_SHARES,
    deadband_usd: float = REBALANCE_DEADBAND_USD,
) -> Optional[Dict[str, object]]:
    """
    Decide a proxy rebalance order, applying the deadband.

    Returns ``{"side", "qty", "notional"}`` or ``None`` when the drift is minor
    noise (fewer than ``deadband_shares`` shares OR less than ``deadband_usd``).
    """
    px = float(price or 0.0)
    if px <= 0:
        return None

    delta = float(target_qty or 0.0) - float(current_qty or 0.0)
    qty = int(round(abs(delta)))
    if qty <= 0:
        return None

    notional = qty * px
    # Suppress minor noise: require the move to clear BOTH thresholds.
    if qty < int(deadband_shares) or notional < float(deadband_usd):
        return None

    side = "BUY" if delta > 0 else "SELL"
    return {"side": side, "qty": qty, "notional": round(notional, 2)}


def should_park_cash(
    satellite_search_done: bool,
    free_cash_usd: float,
    price: float,
    min_notional: float = REBALANCE_DEADBAND_USD,
) -> bool:
    """
    Execution-order SSOT gate for parking idle cash into the proxy.

    Cash is parked ONLY after the satellite entry loop has fully completed and
    only when there is enough idle cash to matter. This prevents the proxy buy
    from racing a satellite buy on the same tick.
    """
    return (
        bool(satellite_search_done)
        and float(free_cash_usd or 0.0) >= float(min_notional)
        and float(price or 0.0) > 0
    )


class CashProxySleeve:
    """
    Stateful guard tracking the last proxy buy per ticker. Enforces the sell
    cooldown and single-authority ownership between Guardian and Autopilot.
    """

    def __init__(self, cooldown_sec: float = PROXY_SELL_COOLDOWN_SEC):
        self._cooldown_sec = float(cooldown_sec)
        self._last_buy_ts: Dict[str, float] = {}
        self._authority_owner: Optional[str] = None
        self._lock = threading.Lock()

    # -- buy tracking / sell cooldown -------------------------------------

    def record_buy(self, ticker: str, ts: Optional[float] = None) -> None:
        """Record a proxy buy so the sell cooldown starts ticking."""
        sym = str(ticker or "").upper().strip()
        if not sym:
            return
        stamp = float(ts if ts is not None else time.time())
        with self._lock:
            self._last_buy_ts[sym] = stamp

    def seconds_until_free(self, ticker: str, now: Optional[float] = None) -> float:
        """Seconds remaining before ``ticker`` may be sold (0.0 if already free)."""
        sym = str(ticker or "").upper().strip()
        now_ts = float(now if now is not None else time.time())
        with self._lock:
            last = self._last_buy_ts.get(sym)
        if last is None:
            return 0.0
        remaining = self._cooldown_sec - (now_ts - last)
        return remaining if remaining > 0.0 else 0.0

    def can_free_proxy(self, ticker: str, now: Optional[float] = None) -> bool:
        """True when the proxy sell cooldown has elapsed for ``ticker``."""
        return self.seconds_until_free(ticker, now=now) <= 0.0

    def free_proxy_cash_for_entry(
        self,
        ticker: str,
        needed_usd: float,
        current_qty: float,
        price: float,
        satellite_search_done: bool,
        now: Optional[float] = None,
    ) -> Dict[str, object]:
        """
        SSOT for ``_free_proxy_cash_for_entry``: decide whether to liquidate part
        of the proxy to fund a satellite entry.

        Returns ``{"action": "SELL"|"HOLD", "qty": int, "reason": str}``.
        A HOLD is returned when the satellite search is still running, the sell
        cooldown is active, there is nothing to sell, or the amount is negligible.
        """
        sym = str(ticker or "").upper().strip()
        px = float(price or 0.0)
        held = float(current_qty or 0.0)
        need = float(needed_usd or 0.0)

        if not satellite_search_done:
            return {"action": "HOLD", "qty": 0, "reason": "SATELLITE_SEARCH_PENDING"}
        if px <= 0 or held <= 0:
            return {"action": "HOLD", "qty": 0, "reason": "NO_PROXY_INVENTORY"}
        if need <= 0:
            return {"action": "HOLD", "qty": 0, "reason": "NO_CASH_NEEDED"}

        remaining = self.seconds_until_free(sym, now=now)
        if remaining > 0.0:
            return {
                "action": "HOLD",
                "qty": 0,
                "reason": "PROXY_SELL_COOLDOWN",
                "cooldown_remaining_sec": round(remaining, 1),
            }

        qty_needed = int(-(-need // px))  # ceil(need / px)
        qty = min(int(held), max(0, qty_needed))
        if qty <= 0:
            return {"action": "HOLD", "qty": 0, "reason": "NEGLIGIBLE_AMOUNT"}

        return {"action": "SELL", "qty": qty, "reason": "FUND_SATELLITE_ENTRY"}

    # -- single-authority ownership ---------------------------------------

    def acquire_authority(self, owner: str) -> bool:
        """
        Claim exclusive proxy-order authority for ``owner`` (e.g. "GUARDIAN" or
        "AUTOPILOT"). Returns False if another owner already holds it.
        """
        with self._lock:
            if self._authority_owner in (None, owner):
                self._authority_owner = owner
                return True
            return False

    def release_authority(self, owner: str) -> None:
        """Release authority if ``owner`` currently holds it."""
        with self._lock:
            if self._authority_owner == owner:
                self._authority_owner = None

    def authority_owner(self) -> Optional[str]:
        with self._lock:
            return self._authority_owner

    def reset(self) -> None:
        """Clear all state (test isolation)."""
        with self._lock:
            self._last_buy_ts.clear()
            self._authority_owner = None


# Global singleton sleeve shared across Guardian and Autopilot daemons.
default_cash_proxy_sleeve = CashProxySleeve()
