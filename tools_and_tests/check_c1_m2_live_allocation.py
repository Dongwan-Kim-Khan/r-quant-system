#!/usr/bin/env python3
"""
C-2 live allocation diagnostic.

Prints satellite vs QQQ/QLD proxy occupancy, empty-slot targets (34/33/33 or 25/25),
unheld ranked candidates with target shares, and idle-NAV proxy gap.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from al_sangmoo.core.constants import (  # noqa: E402
    CASH_PROXY_TICKER,
    LEVERAGE_TICKER,
    MAX_SLOTS_BEAR,
    MAX_SLOTS_BULL,
    SLOT_WEIGHTS_BEAR,
    SLOT_WEIGHTS_BULL,
)
from al_sangmoo.domain.risk.cash_proxy import (  # noqa: E402
    build_cash_proxy_plan,
    idle_nav_usd,
    is_proxy_ticker,
    proxy_holdings,
    satellite_holdings,
)
from al_sangmoo.domain.risk.position_sizer import calculate_target_shares  # noqa: E402
from al_sangmoo.infrastructure.persistence import get_live_portfolio  # noqa: E402


DASHBOARD_JSON = os.path.join(BASE_DIR, "dashboard_data.json")


def _load_feed() -> Dict[str, Any]:
    if not os.path.exists(DASHBOARD_JSON):
        return {}
    with open(DASHBOARD_JSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _qty(holdings: List[Dict[str, Any]], ticker: str) -> float:
    t = ticker.upper()
    return sum(
        float(h.get("quantity") or 0.0)
        for h in holdings
        if str(h.get("ticker", "")).upper() == t
    )


def _broker_holdings() -> Optional[List[Dict[str, Any]]]:
    try:
        from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker

        if not default_kis_broker.is_configured():
            return None
        bal = default_kis_broker.get_overseas_balance()
        return list(bal.get("holdings") or [])
    except Exception as exc:
        print(f"[warn] broker balance unavailable: {exc}")
        return None


def _resolve_bull(feed: Dict[str, Any]) -> bool:
    slot = feed.get("slot_allocation_summary") or {}
    if "is_bull_regime" in slot:
        return bool(slot["is_bull_regime"])
    macro = feed.get("macro") or {}
    if "is_bull_regime" in macro:
        return bool(macro["is_bull_regime"])
    top = feed.get("top_conviction_pick") or {}
    sizing = top.get("sizing") or {}
    if "is_bull_regime" in sizing:
        return bool(sizing["is_bull_regime"])
    return True


def _leverage_mode(feed: Dict[str, Any]) -> bool:
    macro = feed.get("macro") or {}
    lev = macro.get("leverage") or {}
    if "leverage_mode" in lev:
        return bool(lev["leverage_mode"])
    slot = feed.get("slot_allocation_summary") or {}
    lev2 = slot.get("leverage") or {}
    if "leverage_mode" in lev2:
        return bool(lev2["leverage_mode"])
    return False


def _fmt_holding(h: Dict[str, Any]) -> str:
    t = str(h.get("ticker", "")).upper()
    qty = float(h.get("quantity") or 0.0)
    px = float(h.get("current_price") or h.get("buy_price") or 0.0)
    return f"  - {t:6s} qty={qty:8.2f}  px=${px:,.2f}  mv=${qty * px:,.2f}"


def main() -> int:
    feed = _load_feed()
    port = get_live_portfolio()
    holdings = list(port.get("holdings") or [])
    equity = float(port.get("total_equity_usd") or port.get("total_value") or 0.0)
    cash = float(port.get("cash_usd") or port.get("cash") or 0.0)

    sats = satellite_holdings(holdings)
    proxies = proxy_holdings(holdings)
    held = {str(h.get("ticker", "")).upper() for h in holdings}

    is_bull = _resolve_bull(feed)
    lev_on = _leverage_mode(feed)
    weights = list(SLOT_WEIGHTS_BULL if is_bull else SLOT_WEIGHTS_BEAR)
    max_slots = MAX_SLOTS_BULL if is_bull else MAX_SLOTS_BEAR
    empty_slots = max(0, max_slots - len(sats))

    print("=" * 72)
    print("C-2 LIVE ALLOCATION DIAGNOSTIC")
    print("=" * 72)
    print(f"Equity NAV     : ${equity:,.2f}")
    print(f"Cash (ledger)  : ${cash:,.2f}")
    print(
        f"Regime         : "
        f"{'1.5x Bull 34/33/33' if is_bull and lev_on else ('1.0x Bull 34/33/33' if is_bull else '1.0x Bear 25/25')}"
        f"  (bull={is_bull}, leverage_mode={lev_on})"
    )
    print(f"Max slots      : {max_slots}  | occupied={len(sats)}  | empty={empty_slots}")
    print(f"Slot weights   : {[f'{w*100:.0f}%' for w in weights]}")
    print()

    print("--- Satellite (leadership) holdings ---")
    if not sats:
        print("  (none)")
    else:
        for h in sats:
            print(_fmt_holding(h))

    print("--- Cash Proxy holdings (QQQ / QLD) ---")
    if not proxies:
        print("  (none)")
    else:
        for h in proxies:
            print(_fmt_holding(h))

    broker = _broker_holdings()
    if broker is not None:
        print("--- Broker overseas holdings ---")
        if not broker:
            print("  (none)")
        else:
            for h in broker:
                print(_fmt_holding(h))
        b_sats = [h for h in broker if not is_proxy_ticker(str(h.get("ticker", "")))]
        print(f"  broker satellite count: {len(b_sats)}")
    else:
        print("--- Broker ---")
        print("  (not configured / unavailable)")

    ranked = feed.get("ranked_conviction_list") or []
    if not ranked:
        ranked = [
            x for x in (feed.get("top_conviction_pick"), feed.get("top_conviction_runner_up"))
            if isinstance(x, dict) and x.get("ticker")
        ]

    print()
    print("--- Empty-slot fill plan (unheld ranked candidates) ---")
    next_rank = len(sats) + 1
    planned = 0
    for cand in ranked:
        if next_rank > max_slots:
            break
        ticker = str(cand.get("ticker", "")).upper()
        if not ticker or ticker in held or is_proxy_ticker(ticker):
            continue
        px = float(cand.get("price") or 0.0)
        sizing = calculate_target_shares(
            portfolio_nav=equity if equity > 0 else 7500.0,
            current_price=px,
            slot_rank=next_rank,
            is_bull=is_bull,
        )
        print(
            f"  Slot#{next_rank} ({float(sizing.get('slot_weight') or 0)*100:.0f}%) → "
            f"{ticker}  target={sizing.get('shares')} sh  "
            f"cap=${sizing.get('slot_cap_usd')}  eligible={sizing.get('eligible')}  "
            f"score={cand.get('conviction_score')}"
        )
        next_rank += 1
        planned += 1
    if planned == 0:
        print("  (no unheld deployable candidates for empty slots)")

    idle = idle_nav_usd(equity, holdings, is_bull=is_bull, max_slots=max_slots)
    qqq_px = float(
        next(
            (
                float(h.get("current_price") or h.get("buy_price") or 0.0)
                for h in holdings
                if str(h.get("ticker", "")).upper() == CASH_PROXY_TICKER
            ),
            0.0,
        )
    )
    qld_px = float(
        next(
            (
                float(h.get("current_price") or h.get("buy_price") or 0.0)
                for h in holdings
                if str(h.get("ticker", "")).upper() == LEVERAGE_TICKER
            ),
            0.0,
        )
    )
    # Fallback prices from feed macro / ranked QQQ if present
    if qqq_px <= 0:
        for c in ranked:
            if str(c.get("ticker", "")).upper() == "QQQ" and c.get("price"):
                qqq_px = float(c["price"])
                break
    plan = build_cash_proxy_plan(
        total_equity_usd=equity if equity > 0 else idle,
        holdings=holdings,
        cash_usd=cash,
        leverage_mode=lev_on,
        qqq_price=qqq_px or 400.0,
        qld_price=qld_px or 80.0,
        is_bull=is_bull,
        max_slots=max_slots,
    )
    cur_qqq = int(_qty(holdings, CASH_PROXY_TICKER))
    cur_qld = int(_qty(holdings, LEVERAGE_TICKER))
    tgt_qqq = int(plan.get("qqq_shares") or 0)
    tgt_qld = int(plan.get("qld_shares") or 0)

    print()
    print("--- Residual idle NAV / Cash Proxy gap ---")
    print(f"  idle_nav_usd     : ${idle:,.2f}")
    print(f"  deployable_usd   : ${plan.get('deployable_usd')}")
    print(f"  QQQ target/actual: {tgt_qqq} / {cur_qqq}  (gap={tgt_qqq - cur_qqq:+d})")
    print(f"  QLD target/actual: {tgt_qld} / {cur_qld}  (gap={tgt_qld - cur_qld:+d})")
    print(f"  leverage_mode    : {plan.get('leverage_mode')}  gross_target={plan.get('gross_target')}")
    actions = plan.get("actions") or []
    if actions:
        print("  rebalance actions:")
        for act in actions:
            print(f"    - {act.get('side')} {act.get('ticker')} x{act.get('qty')} ({act.get('reason')})")
    else:
        print("  rebalance actions: (none -- proxy already at target)")

    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
