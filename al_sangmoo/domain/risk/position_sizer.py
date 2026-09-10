"""
C1-M2 Dynamic Position Sizer — NAV-based 50/30/20 (bull) and 25/25 (bear).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from al_sangmoo.core.constants import (
    MAX_SLOTS_BEAR,
    MAX_SLOTS_BULL,
    SLOT_WEIGHTS_BEAR,
    SLOT_WEIGHTS_BULL,
    STOP_LOSS_PCT,
)
from al_sangmoo.core.feature_flags import use_riskfolio_hrp
from al_sangmoo.domain.quant.macro import classify_msi_stance


def calculate_atr(df: pd.DataFrame, window: int = 14) -> float:
    """Computes 14-day Average True Range (ATR)."""
    if len(df) < window + 1:
        return 0.0
    high = df["High"].values
    low = df["Low"].values
    close = df["Close"].values

    tr1 = high[1:] - low[1:]
    tr2 = np.abs(high[1:] - close[:-1])
    tr3 = np.abs(low[1:] - close[:-1])
    tr = np.maximum(tr1, np.maximum(tr2, tr3))

    atr = float(np.mean(tr[-window:]))
    return round(atr, 2)


def get_slot_weights(is_bull: bool = True) -> List[float]:
    """Return C1-M2 slot weight vector for the active regime."""
    return list(SLOT_WEIGHTS_BULL if is_bull else SLOT_WEIGHTS_BEAR)


def get_slot_weight(slot_rank: int, is_bull: bool = True) -> Optional[float]:
    """1-indexed slot rank → NAV weight. None if rank exceeds regime slot count."""
    weights = get_slot_weights(is_bull)
    if slot_rank < 1 or slot_rank > len(weights):
        return None
    return float(weights[slot_rank - 1])


def calculate_target_shares(
    portfolio_nav: float,
    current_price: float,
    slot_rank: int = 1,
    is_bull: bool = True,
    min_shares: int = 1,
    slot_weights: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """
    C1-M2 NAV-based share calculator.

    Bull: rank1=50%, rank2=30%, rank3=20%.
    Bear (`is_bull=False`): max 2 slots at 25% / 25%.
    """
    nav = float(portfolio_nav or 0.0)
    price = float(current_price or 0.0)
    weights = list(slot_weights) if slot_weights is not None else get_slot_weights(is_bull)
    max_slots = len(weights)

    empty = {
        "eligible": False,
        "shares": 0,
        "allocated_usd": 0.0,
        "allocated_krw": 0,
        "slot_cap_usd": 0.0,
        "allocation_pct": 0.0,
        "slot_weight": 0.0,
        "slot_rank": int(slot_rank),
        "remaining_cash_usd": round(nav, 2),
        "is_bull_regime": bool(is_bull),
        "max_slots": max_slots,
        "reason": "Invalid price, NAV, or slot rank",
    }
    if nav <= 0 or price <= 0:
        return empty

    weight = None
    if 1 <= int(slot_rank) <= len(weights):
        weight = float(weights[int(slot_rank) - 1])
    if weight is None or weight <= 0:
        out = dict(empty)
        out["reason"] = f"Slot rank {slot_rank} exceeds regime max ({max_slots})"
        return out

    slot_cap_usd = nav * weight
    usd_krw_rate = 1380.0

    if slot_cap_usd < price:
        return {
            "eligible": False,
            "shares": 0,
            "allocated_usd": 0.0,
            "allocated_krw": 0,
            "slot_cap_usd": round(slot_cap_usd, 2),
            "allocation_pct": round(weight * 100.0, 2),
            "slot_weight": weight,
            "slot_rank": int(slot_rank),
            "remaining_cash_usd": round(nav, 2),
            "usd_krw_rate": usd_krw_rate,
            "is_bull_regime": bool(is_bull),
            "max_slots": max_slots,
            "reason": (
                f"주당 가격(${price:,.2f})이 슬롯 한도(${slot_cap_usd:,.2f})를 "
                f"초과하여 1주 매수 불가"
            ),
        }

    shares = int(slot_cap_usd // price)
    if shares < min_shares:
        shares = min_shares

    allocated_usd = round(shares * price, 2)
    allocated_krw = int(allocated_usd * usd_krw_rate)
    allocation_pct = round((allocated_usd / nav) * 100, 2) if nav > 0 else 0.0
    remaining_cash = round(nav - allocated_usd, 2)
    regime_label = (
        f"상승장 {max_slots}-Slot {int(weight * 100)}% 컨빅션"
        if is_bull
        else f"하락장 {max_slots}-Slot {int(weight * 100)}% 방어"
    )

    return {
        "eligible": True,
        "shares": shares,
        "allocated_usd": allocated_usd,
        "allocated_krw": allocated_krw,
        "slot_cap_usd": round(slot_cap_usd, 2),
        "allocation_pct": allocation_pct,
        "slot_weight": weight,
        "slot_rank": int(slot_rank),
        "remaining_cash_usd": remaining_cash,
        "usd_krw_rate": usd_krw_rate,
        "is_bull_regime": bool(is_bull),
        "max_slots": max_slots,
        "reason": (
            f"{shares}주 집중 매수 (약 {allocated_krw:,}원 / "
            f"슬롯#{slot_rank} 목표 {weight * 100:.0f}% / {regime_label})"
        ),
    }


def calculate_dynamic_position_size(
    portfolio_equity: float,
    current_price: float,
    atr_14: float,
    msi_score: float = 50.0,
    target_risk_fraction: float = 0.015,
    max_position_fraction: float = 0.50,
) -> Dict[str, Any]:
    """
    Volatility-targeted dynamic position sizing adjusted for MSI macro climate.
    Cap defaults to C1-M2 Slot-1 max (50%).
    """
    if current_price <= 0 or portfolio_equity <= 0:
        return {
            "shares": 0.0,
            "allocated_cash": 0.0,
            "allocation_pct": 0.0,
            "macro_multiplier": 0.0,
            "dollar_risk": 0.0,
        }

    stance = classify_msi_stance(msi_score)
    if stance == "CASH_EXIT":
        macro_mult = 0.0
    elif stance == "DEFENSE_HOLD":
        macro_mult = 0.35
    elif stance == "SELECTIVE_BUY":
        macro_mult = 0.75
    else:
        macro_mult = 1.0

    effective_atr = atr_14 if atr_14 > 0 else (current_price * 0.03)
    volatility_risk = effective_atr / current_price
    target_dollar_risk = portfolio_equity * target_risk_fraction

    computed_allocation = (target_dollar_risk / volatility_risk) * macro_mult
    max_allowed_allocation = portfolio_equity * max_position_fraction
    final_allocation = min(computed_allocation, max_allowed_allocation)

    shares = round(final_allocation / current_price, 4)
    allocated_cash = round(shares * current_price, 2)
    allocation_pct = (
        round((allocated_cash / portfolio_equity) * 100, 2) if portfolio_equity > 0 else 0.0
    )
    dollar_risk = round(allocated_cash * abs(STOP_LOSS_PCT), 2)

    return {
        "ticker_price": current_price,
        "atr_14": effective_atr,
        "msi_score": msi_score,
        "macro_multiplier": macro_mult,
        "recommended_shares": shares,
        "allocated_cash": allocated_cash,
        "allocation_pct": allocation_pct,
        "dollar_risk_at_stop": dollar_risk,
        "max_cap_reached": computed_allocation > max_allowed_allocation,
    }


def calculate_slot_position_size(
    portfolio_equity: float = 7500.0,
    current_price: float = 0.0,
    slot_fraction: Optional[float] = None,
    msi_score: float = 50.0,
    min_shares: int = 1,
    is_bull_regime: bool = True,
    slot_rank: int = 1,
) -> Dict[str, Any]:
    """
    C1-M2 integer share sizing.

    Prefer rank-based weights (50/30/20 or 25/25). Optional `slot_fraction`
    overrides the rank weight for legacy callers.
    """
    del msi_score  # retained for API compatibility; MSI gates live in macro overlay
    if slot_fraction is not None:
        weights = [float(slot_fraction)]
        rank = 1
        return calculate_target_shares(
            portfolio_nav=portfolio_equity,
            current_price=current_price,
            slot_rank=rank,
            is_bull=is_bull_regime,
            min_shares=min_shares,
            slot_weights=weights,
        )
    return calculate_target_shares(
        portfolio_nav=portfolio_equity,
        current_price=current_price,
        slot_rank=slot_rank,
        is_bull=is_bull_regime,
        min_shares=min_shares,
    )


def hrp_slot_allocations(price_history_df: pd.DataFrame, total_capital: float) -> Dict[str, float]:
    """Equal-weight unless USE_RISKFOLIO_HRP is on."""
    from al_sangmoo.domain.risk.riskfolio_sizer import RiskfolioPositionSizer

    sizer = RiskfolioPositionSizer(total_capital)
    if not use_riskfolio_hrp():
        cols = list(price_history_df.columns) if price_history_df is not None else []
        n = max(1, len(cols))
        even = total_capital / n
        return {str(c): round(even, 2) for c in cols}
    return sizer.calculate_hrp_weights(price_history_df)


__all__ = [
    "calculate_atr",
    "calculate_dynamic_position_size",
    "calculate_slot_position_size",
    "calculate_target_shares",
    "get_slot_weight",
    "get_slot_weights",
    "hrp_slot_allocations",
    "MAX_SLOTS_BEAR",
    "MAX_SLOTS_BULL",
]
