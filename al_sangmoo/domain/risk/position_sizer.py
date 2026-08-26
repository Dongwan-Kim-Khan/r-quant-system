"""
Dynamic ATR & Volatility Risk Parity Position Sizer.
"""
import numpy as np
import pandas as pd
from typing import Dict, Any, Optional

def calculate_atr(df: pd.DataFrame, window: int = 14) -> float:
    """Computes 14-day Average True Range (ATR)."""
    if len(df) < window + 1:
        return 0.0
    high = df['High'].values
    low = df['Low'].values
    close = df['Close'].values

    tr1 = high[1:] - low[1:]
    tr2 = np.abs(high[1:] - close[:-1])
    tr3 = np.abs(low[1:] - close[:-1])
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    
    atr = float(np.mean(tr[-window:]))
    return round(atr, 2)

def calculate_dynamic_position_size(
    portfolio_equity: float,
    current_price: float,
    atr_14: float,
    msi_score: float = 50.0,
    target_risk_fraction: float = 0.015, # Risk 1.5% of total portfolio per position
    max_position_fraction: float = 0.25   # Maximum 25% single-asset cap
) -> Dict[str, Any]:
    """
    Computes volatility-targeted dynamic position sizing adjusted for MSI macro climate.
    """
    if current_price <= 0 or portfolio_equity <= 0:
        return {
            "shares": 0.0,
            "allocated_cash": 0.0,
            "allocation_pct": 0.0,
            "macro_multiplier": 0.0,
            "dollar_risk": 0.0
        }

    # 1. Determine Macro Multiplier based on MSI 2.0
    if msi_score >= 75.0:
        macro_mult = 0.0       # CASH_EXIT: 0% new buys
    elif msi_score >= 50.0:
        macro_mult = 0.35      # DEFENSE_HOLD: Conservative 35% sizing
    elif msi_score >= 30.0:
        macro_mult = 0.75      # SELECTIVE_BUY: 75% sizing
    else:
        macro_mult = 1.0       # ACTIVE_BUY: 100% full sizing

    # 2. ATR Volatility Risk Scaling
    effective_atr = atr_14 if atr_14 > 0 else (current_price * 0.03)
    volatility_risk = effective_atr / current_price
    target_dollar_risk = portfolio_equity * target_risk_fraction
    
    computed_allocation = (target_dollar_risk / volatility_risk) * macro_mult
    max_allowed_allocation = portfolio_equity * max_position_fraction
    final_allocation = min(computed_allocation, max_allowed_allocation)

    shares = round(final_allocation / current_price, 4)
    allocated_cash = round(shares * current_price, 2)
    allocation_pct = round((allocated_cash / portfolio_equity) * 100, 2) if portfolio_equity > 0 else 0.0
    dollar_risk = round(allocated_cash * 0.04, 2) # Strict 4% stop risk

    return {
        "ticker_price": current_price,
        "atr_14": effective_atr,
        "msi_score": msi_score,
        "macro_multiplier": macro_mult,
        "recommended_shares": shares,
        "allocated_cash": allocated_cash,
        "allocation_pct": allocation_pct,
        "dollar_risk_at_stop": dollar_risk,
        "max_cap_reached": computed_allocation > max_allowed_allocation
    }


def calculate_slot_position_size(
    portfolio_equity: float = 7500.0,  # ~10,000,000 KRW
    current_price: float = 0.0,
    slot_fraction: Optional[float] = None,
    msi_score: float = 50.0,
    min_shares: int = 1,
    is_bull_regime: bool = True
) -> Dict[str, Any]:
    """
    Computes integer share sizing for Goldman Sachs / Al-Sangmoo v2 Dynamic Regime Architecture ($7,500 / 10M KRW).
    - Bull Regime (SPY >= 200 SMA): 3-Slot Full Deployment (33.3% per slot, 1% cash buffer)
    - Bear Regime (SPY < 200 SMA): 2-Slot Defensive Deployment (25.0% per slot, 50% cash buffer)
    """
    if current_price <= 0 or portfolio_equity <= 0:
        return {
            "eligible": False,
            "shares": 0,
            "allocated_usd": 0.0,
            "allocated_krw": 0,
            "slot_cap_usd": 0.0,
            "allocation_pct": 0.0,
            "remaining_cash_usd": portfolio_equity,
            "reason": "Invalid price or equity"
        }

    # Determine default slot fraction based on regime if not explicitly passed
    if slot_fraction is None:
        slot_fraction = 0.333 if is_bull_regime else 0.250

    # Strict GS-Quant v2 Dynamic Regime Allocator:
    # - Bull Regime (SPY >= 200 SMA): 3-Slot Full Deployment (33.3% = $2,500 per slot)
    # - Bear Regime (SPY < 200 SMA): 2-Slot Defensive Deployment (25.0% = $1,875 per slot, 50% Cash Shield)
    slot_cap_usd = portfolio_equity * (slot_fraction or (0.333 if is_bull_regime else 0.250))
    usd_krw_rate = 1380.0  # Reference exchange rate

    if slot_cap_usd < current_price:
        # Cannot even buy 1 share under current slot budget
        return {
            "eligible": False,
            "shares": 0,
            "allocated_usd": 0.0,
            "allocated_krw": 0,
            "slot_cap_usd": round(slot_cap_usd, 2),
            "allocation_pct": 0.0,
            "remaining_cash_usd": round(portfolio_equity, 2),
            "reason": f"주당 가격(${current_price:,.2f})이 슬롯 한도(${slot_cap_usd:,.2f})를 초과하여 1주 매수 불가"
        }

    shares = int(slot_cap_usd // current_price)
    if shares < min_shares:
        shares = min_shares

    allocated_usd = round(shares * current_price, 2)
    allocated_krw = int(allocated_usd * usd_krw_rate)
    allocation_pct = round((allocated_usd / portfolio_equity) * 100, 2) if portfolio_equity > 0 else 0.0
    remaining_cash = round(portfolio_equity - allocated_usd, 2)

    return {
        "eligible": True,
        "shares": shares,
        "allocated_usd": allocated_usd,
        "allocated_krw": allocated_krw,
        "slot_cap_usd": round(slot_cap_usd, 2),
        "allocation_pct": allocation_pct,
        "remaining_cash_usd": remaining_cash,
        "usd_krw_rate": usd_krw_rate,
        "is_bull_regime": is_bull_regime,
        "max_slots": 3 if is_bull_regime else 2,
        "reason": f"{shares}주 집중 매수 (약 {allocated_krw:,}원 / 슬롯 점유율 {allocation_pct}% / {'상승장 3-Slot 100% 풀가동' if is_bull_regime else '하락장 2-Slot 50% 현금 대피'})"
    }

