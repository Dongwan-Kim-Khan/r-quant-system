"""
Dynamic ATR & Volatility Risk Parity Position Sizer.
"""
import numpy as np
import pandas as pd
from typing import Dict, Any

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
    dollar_risk = round(allocated_cash * 0.03, 2) # Strict 3% stop risk

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
