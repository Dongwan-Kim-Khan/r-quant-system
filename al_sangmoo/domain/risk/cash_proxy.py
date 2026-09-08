"""
C1-M2 Cash Proxy Overlay — idle NAV parks in QQQ; leadership entries/exits rebalance.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from al_sangmoo.core.constants import (
    CASH_PROXY_TICKER,
    LEVERAGE_GROSS_TARGET,
    LEVERAGE_TICKER,
    LEVERAGE_VIX_MAX,
)

logger = logging.getLogger(__name__)


def is_proxy_ticker(ticker: str) -> bool:
    t = str(ticker or "").strip().upper()
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
    1.0x → 100% QQQ; 1.5x → mix so gross ≈ 1.5 via QQQ(1x)+QLD(2x).
    Solve: q + l = 1, 1*q + 2*l = 1.5 → l = 0.5, q = 0.5.
    """
    if leverage_mode:
        return 0.50, 0.50
    return 1.0, 0.0


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
    Does not place orders — callers (autopilot / guardian) execute via broker.
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
    `SPY >= SMA200` AND `VIX < 20` → 1.5x QLD mix allowed; else 1.0x QQQ core.
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
