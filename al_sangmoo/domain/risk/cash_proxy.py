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
"""

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from al_sangmoo.core.constants import (
    CASH_PROXY_TICKER,
    LEVERAGE_TICKER,
    LEVERAGE_GROSS_TARGET,
    LEVERAGE_VIX_MAX,
)

PROXY_TICKERS = (CASH_PROXY_TICKER, LEVERAGE_TICKER)

# A proxy bought within this window must not be sold to fund a satellite entry.
PROXY_SELL_COOLDOWN_SEC = 300.0

# Rebalancing noise thresholds: an order fires only when BOTH are exceeded.
REBALANCE_DEADBAND_SHARES = 3
REBALANCE_DEADBAND_USD = 300.0

# Shared mutex so only one daemon issues cash-proxy orders at a time.
PROXY_ORDER_MUTEX = threading.RLock()


def is_proxy_ticker(ticker: str) -> bool:
    """True when ``ticker`` is a managed cash-proxy instrument (QLD/QQQ)."""
    t = str(ticker or "").upper().strip()
    return t in {CASH_PROXY_TICKER, LEVERAGE_TICKER}


def satellite_holdings(holdings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Non-proxy (leadership) positions only."""
    out = []
    for h in holdings or []:
        if not is_proxy_ticker(str(h.get("ticker") or "")):
            out.append(h)
    return out


def proxy_holdings(holdings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [h for h in (holdings or []) if is_proxy_ticker(str(h.get("ticker") or ""))]


def idle_nav_usd(total_equity_usd: float, holdings: List[Dict[str, Any]]) -> float:
    """NAV not currently allocated to satellite leadership names."""
    sat_val = 0.0
    for h in satellite_holdings(holdings):
        px = float(h.get("current_price") or h.get("buy_price") or 0.0)
        qty = float(h.get("quantity") or 0.0)
        sat_val += max(0.0, px * qty)
    return max(0.0, float(total_equity_usd or 0.0) - sat_val)


def proxy_mix_weights(leverage_mode: bool) -> Tuple[float, float]:
    """
    Residual idle capital mix: (QQQ weight, QLD weight) of idle sleeve.
    1.0x -> 100% QQQ; 1.5x -> mix so gross ≈ 1.5 via QQQ(1x)+QLD(2x).
    Solve: q + l = 1, 1*q + 2*l = 1.5 -> l = 0.5, q = 0.5.
    """
    if leverage_mode:
        return 0.50, 0.50
    return 1.0, 0.0


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
    only when there is enough idle cash to matter.
    """
    return (
        bool(satellite_search_done)
        and float(free_cash_usd or 0.0) >= float(min_notional)
        and float(price or 0.0) > 0
    )


def build_cash_proxy_plan(
    total_equity_usd: float,
    holdings: List[Dict[str, Any]],
    cash_usd: float = 0.0,
    leverage_mode: bool = False,
    qqq_price: float = 0.0,
    qld_price: float = 0.0,
) -> Dict[str, Any]:
    """
    Plan idle-capital parking into QQQ (+ optional QLD).
    Does not place orders - callers (autopilot / guardian) execute via broker.
    """
    idle = idle_nav_usd(total_equity_usd, holdings)
    # Prefer explicit cash when provided; otherwise treat idle sleeve as deployable.
    deployable = max(float(cash_usd or 0.0), idle) if cash_usd else idle
    q_w, l_w = proxy_mix_weights(bool(leverage_mode))
    qqq_alloc = deployable * q_w
    qld_alloc = deployable * l_w

    def _shares(alloc: float, px: float) -> int:
        if alloc <= 0 or px <= 0:
            return 0
        return int(alloc // px)

    return {
        "proxy_ticker": CASH_PROXY_TICKER,
        "leverage_ticker": LEVERAGE_TICKER,
        "leverage_mode": bool(leverage_mode),
        "gross_target": LEVERAGE_GROSS_TARGET if leverage_mode else 1.0,
        "idle_nav_usd": round(idle, 2),
        "deployable_usd": round(deployable, 2),
        "qqq_weight": q_w,
        "qld_weight": l_w,
        "qqq_alloc_usd": round(qqq_alloc, 2),
        "qld_alloc_usd": round(qld_alloc, 2),
        "qqq_shares": _shares(qqq_alloc, float(qqq_price or 0.0)),
        "qld_shares": _shares(qld_alloc, float(qld_price or 0.0)),
        "actions": _proxy_rebalance_actions(
            holdings=holdings,
            qqq_target_shares=_shares(qqq_alloc, float(qqq_price or 0.0)),
            qld_target_shares=_shares(qld_alloc, float(qld_price or 0.0)),
            leverage_mode=bool(leverage_mode),
        ),
    }


def _holding_qty(holdings: List[Dict[str, Any]], ticker: str) -> float:
    t = ticker.upper()
    total = 0.0
    for h in holdings or []:
        if str(h.get("ticker") or "").upper() == t:
            total += float(h.get("quantity") or 0.0)
    return total


def _proxy_rebalance_actions(
    holdings: List[Dict[str, Any]],
    qqq_target_shares: int,
    qld_target_shares: int,
    leverage_mode: bool,
) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    cur_q = int(_holding_qty(holdings, CASH_PROXY_TICKER))
    cur_l = int(_holding_qty(holdings, LEVERAGE_TICKER))

    # Delever first: cut QLD when leverage mode off
    if not leverage_mode and cur_l > 0:
        actions.append({
            "ticker": LEVERAGE_TICKER,
            "side": "SELL",
            "qty": cur_l,
            "reason": "DELEVERAGE_TO_1X_QQQ_CORE",
        })
        cur_l = 0

    if leverage_mode:
        if qld_target_shares > cur_l:
            actions.append({
                "ticker": LEVERAGE_TICKER,
                "side": "BUY",
                "qty": qld_target_shares - cur_l,
                "reason": "LEVERAGE_BOOST_QLD",
            })
        elif cur_l > qld_target_shares:
            actions.append({
                "ticker": LEVERAGE_TICKER,
                "side": "SELL",
                "qty": cur_l - qld_target_shares,
                "reason": "TRIM_QLD_TO_TARGET",
            })

    if qqq_target_shares > cur_q:
        actions.append({
            "ticker": CASH_PROXY_TICKER,
            "side": "BUY",
            "qty": qqq_target_shares - cur_q,
            "reason": "PARK_IDLE_IN_QQQ",
        })
    elif cur_q > qqq_target_shares:
        actions.append({
            "ticker": CASH_PROXY_TICKER,
            "side": "SELL",
            "qty": cur_q - qqq_target_shares,
            "reason": "FREE_CASH_FROM_QQQ_FOR_SATELLITE",
        })
    return actions


def raise_cash_from_proxy(
    needed_usd: float,
    holdings: List[Dict[str, Any]],
    qqq_price: float = 0.0,
    qld_price: float = 0.0,
) -> List[Dict[str, Any]]:
    """
    Sell QLD first, then QQQ, to free `needed_usd` for a leadership entry.
    """
    need = max(0.0, float(needed_usd or 0.0))
    if need <= 0:
        return []

    actions: List[Dict[str, Any]] = []
    for ticker, px in ((LEVERAGE_TICKER, qld_price), (CASH_PROXY_TICKER, qqq_price)):
        if need <= 0:
            break
        qty = int(_holding_qty(holdings, ticker))
        price = float(px or 0.0)
        if qty <= 0 or price <= 0:
            continue
        shares_needed = int((need / price) + 0.9999)  # ceil
        sell_qty = min(qty, max(1, shares_needed))
        actions.append({
            "ticker": ticker,
            "side": "SELL",
            "qty": sell_qty,
            "reason": "FREE_CASH_FOR_LEADERSHIP_ENTRY",
            "est_proceeds_usd": round(sell_qty * price, 2),
        })
        need -= sell_qty * price
    return actions


def park_proceeds_after_exit(
    proceeds_usd: float,
    leverage_mode: bool = False,
    qqq_price: float = 0.0,
    qld_price: float = 0.0,
) -> List[Dict[str, Any]]:
    """Immediately redeploy satellite exit proceeds into QQQ (+ QLD if leverage on)."""
    proceeds = max(0.0, float(proceeds_usd or 0.0))
    if proceeds <= 0:
        return []
    q_w, l_w = proxy_mix_weights(leverage_mode)
    actions: List[Dict[str, Any]] = []
    for ticker, weight, px in (
        (CASH_PROXY_TICKER, q_w, qqq_price),
        (LEVERAGE_TICKER, l_w, qld_price),
    ):
        alloc = proceeds * weight
        price = float(px or 0.0)
        if alloc <= 0 or price <= 0:
            continue
        shares = int(alloc // price)
        if shares <= 0:
            continue
        actions.append({
            "ticker": ticker,
            "side": "BUY",
            "qty": shares,
            "reason": "REPARK_EXIT_PROCEEDS_IN_PROXY",
            "alloc_usd": round(shares * price, 2),
        })
    return actions


def resolve_leverage_overlay(
    spy_close: Optional[float],
    spy_sma200: Optional[float],
    vix: Optional[float],
) -> Dict[str, Any]:
    """
    `SPY >= SMA200` AND `VIX < 20` -> 1.5x QLD mix allowed; else 1.0x QQQ core.
    """
    has_spy = (
        spy_close is not None
        and spy_sma200 is not None
        and float(spy_close) > 0
        and float(spy_sma200) > 0
    )
    has_vix = vix is not None and float(vix) > 0
    spy_ok = bool(has_spy and float(spy_close) >= float(spy_sma200))
    vix_ok = bool(has_vix and float(vix) < LEVERAGE_VIX_MAX)
    leverage_on = spy_ok and vix_ok
    return {
        "leverage_mode": leverage_on,
        "qld_allowed": leverage_on,
        "gross_target": LEVERAGE_GROSS_TARGET if leverage_on else 1.0,
        "spy_above_sma200": spy_ok if has_spy else None,
        "vix_below_20": vix_ok if has_vix else None,
        "spy_close": float(spy_close) if has_spy else None,
        "spy_sma200": float(spy_sma200) if has_spy else None,
        "vix": float(vix) if has_vix else None,
        "directive": (
            "1.5x BOOST (QQQ+QLD)" if leverage_on else "1.0x QQQ CORE (delever)"
        ),
    }


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

    def acquire_authority(self, owner: str) -> bool:
        """Claim exclusive proxy-order authority for ``owner``."""
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


default_cash_proxy_sleeve = CashProxySleeve()
