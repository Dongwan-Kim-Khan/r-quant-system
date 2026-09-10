"""
AL-SANGMOO QUANT TERMINAL: GOLDMAN SACHS-STYLE CONVICTION ENGINE
Cross-sectional multi-factor alpha ranking and capital-constrained Top-Pick selector.
Tailored for constrained capital environments (e.g. 10,000,000 KRW / $7,500).
"""

from typing import Dict, Any, List, Optional
import math
from al_sangmoo.domain.risk.position_sizer import calculate_slot_position_size
from al_sangmoo.core.constants import STOCK_DICT, TICKER_SECTORS, derive_stop_price, derive_target_price
from al_sangmoo.core.feature_flags import use_residual_momentum


def calculate_composite_conviction_score(
    bull_score: float,
    flow_score: float,
    msi_score: float,
    kijun_gap_pct: float,
    is_macro_tailwind: bool,
    vol_ratio: float,
    is_weekly_bull: bool,
    rs_3m: float = 0.0,
    is_breakout: bool = False
) -> float:
    """
    Computes Goldman Sachs-style normalized Composite Alpha Conviction Score (0 - 100).
    v2 Weights:
      - 30%: 17-Year Ichimoku Bull Score & Weekly Alignment
      - 25%: 3-Month Cross-Sectional Relative Strength (RS Momentum Factor)
      - 20%: Institutional Smart Money Flow & OBV Accumulation
      - 15%: Macro Climate Fit (Tailwind Sector + MSI Regime)
      - 10%: Entry Risk-Reward / Breakout Surge Bonus
    """
    # 1. Technical Score (30%)
    weekly_mult = 1.0 if is_weekly_bull else 0.60
    tech_component = min(100.0, bull_score) * weekly_mult

    # 2. RS Momentum Factor Score (25%) - Goldman Sachs Factor Model
    # Normalize 3-month RS (-20% to +40% mapped to 0-100)
    rs_normalized = min(100.0, max(0.0, (rs_3m + 20.0) * (100.0 / 60.0)))
    rs_component = rs_normalized

    # 3. Flow Score (20%)
    flow_component = min(100.0, max(0.0, flow_score))

    # 4. Macro Fit Score (15%)
    msi_fit = max(0.0, 100.0 - msi_score)  # Lower MSI = better market climate
    tailwind_bonus = 20.0 if is_macro_tailwind else 0.0
    macro_component = min(100.0, msi_fit * 0.8 + tailwind_bonus)

    # 5. Entry Timing / Breakout Score (10%)
    if is_breakout:
        entry_component = 95.0  # High conviction on 20D momentum breakout
    else:
        gap_penalty = min(50.0, abs(kijun_gap_pct) * 12.0)
        vol_bonus = max(0.0, (1.0 - vol_ratio) * 50.0) if vol_ratio < 1.0 else 0.0
        entry_component = min(100.0, max(0.0, 100.0 - gap_penalty + vol_bonus))

    # Weighted Sum
    composite = (
        tech_component * 0.30 +
        rs_component * 0.25 +
        flow_component * 0.20 +
        macro_component * 0.15 +
        entry_component * 0.10
    )
    return round(max(0.0, min(100.0, composite)), 1)


def rank_and_select_top_picks(
    candidates: List[Dict[str, Any]],
    portfolio_equity_usd: float = 7500.0,  # ~10M KRW
    msi_score: float = 50.0,
    slot_fraction: Optional[float] = None,
    is_bull_regime: bool = True
) -> Dict[str, Any]:
    """
    Ranks all candidate symbols using Goldman Sachs v2 Momentum & Relative Strength
    and extracts the definitive #1 Top Conviction Pick and #2 Runner-Up.
    """
    max_slots = 3 if is_bull_regime else 2
    default_fraction = 0.333 if is_bull_regime else 0.250
    eff_slot_fraction = slot_fraction if slot_fraction is not None else default_fraction

    if not candidates:
        return {
            "top_pick": None,
            "runner_up": None,
            "ranked_candidates": [],
            "slot_summary": {
                "equity_usd": portfolio_equity_usd,
                "slot_cap_usd": round(portfolio_equity_usd * eff_slot_fraction, 2),
                "max_slots": max_slots,
                "is_bull_regime": is_bull_regime,
                "cash_reserve_usd": round(portfolio_equity_usd * (0.01 if is_bull_regime else 0.50), 2)
            }
        }

    scored_items = []

    for c in candidates:
        ticker = c.get("ticker", "").upper()
        name = c.get("name") or STOCK_DICT.get(ticker, ticker)
        price = float(c.get("entry_price") or c.get("price") or 0.0)
        if price <= 0:
            continue

        score = float(c.get("score") or 0.0)
        flow_ratio = float(c.get("flow_ratio") or 1.0)
        obv_status = c.get("obv_status", "NEUTRAL")
        flow_score = 95.0 if obv_status == "STEALTH_ACCUM" else (85.0 if flow_ratio >= 1.2 else 60.0)
        kijun_gap = float(c.get("kijun_gap_pct") or 1.0)
        # Resolve the 26D kijun so downstream consumers (Autopilot Entry Gate) can
        # verify price >= baseline without a fresh network call.
        kijun_val = float(c.get("kijun") or c.get("kijun_26") or 0.0)
        if kijun_val <= 0 and price > 0 and (1.0 + kijun_gap / 100.0) > 0:
            kijun_val = round(price / (1.0 + kijun_gap / 100.0), 4)
        is_tailwind = c.get("is_macro_tailwind", False)
        vol_ratio = float(c.get("vol_ratio") or 0.80)
        is_weekly_bull = c.get("is_weekly_bull", True)
        rs_3m = float(c.get("rs_3m") or c.get("momentum_3m") or 0.0)
        is_breakout = bool(c.get("is_breakout", False))

        conviction_score = calculate_composite_conviction_score(
            bull_score=score,
            flow_score=flow_score,
            msi_score=msi_score,
            kijun_gap_pct=kijun_gap,
            is_macro_tailwind=is_tailwind,
            vol_ratio=vol_ratio,
            is_weekly_bull=is_weekly_bull,
            rs_3m=rs_3m,
            is_breakout=is_breakout
        )

        if use_residual_momentum():
            residual = float(c.get("residual_momentum") or 0.0)
            conviction_score = round(max(0.0, min(100.0, conviction_score + residual * 0.05)), 1)

        # Calculate Dynamic Regime position sizing for 10M KRW ($7,500)
        sizing = calculate_slot_position_size(
            portfolio_equity=portfolio_equity_usd,
            current_price=price,
            slot_fraction=eff_slot_fraction,
            msi_score=msi_score,
            is_bull_regime=is_bull_regime
        )

        scored_items.append({
            "ticker": ticker,
            "name": name,
            "sector": c.get("sector") or TICKER_SECTORS.get(ticker, "GENERAL"),
            "strategy": c.get("strategy", "Tier 1 (거시 주도주)"),
            "strategy_code": c.get("strategy_code", "TIER_1_LEADER"),
            "price": price,
            "kijun": kijun_val,
            "kijun_gap_pct": round(kijun_gap, 4),
            "target_price": derive_target_price(price),
            "stop_price": derive_stop_price(price),
            "conviction_score": conviction_score,
            "bull_score": score,
            "flow_ratio": flow_ratio,
            "obv_status": obv_status,
            "rs_3m": rs_3m,
            "is_breakout": is_breakout,
            "sizing": sizing,
            "in_wallet": c.get("in_wallet", False),
            "streak_days": c.get("streak_days", 1),
            "rationale": f"확신도 {conviction_score}점 (RS: {rs_3m:+.1f}%) | {name} ({ticker}) - 1,000만원 기준 {sizing['shares']}주(${sizing['allocated_usd']:,.2f}) 집중 진입"
        })

    # Sort descending by Conviction Score, then by price suitability
    scored_items.sort(key=lambda x: (x["sizing"]["eligible"], x["conviction_score"]), reverse=True)

    # Assign Rank 1..N
    for idx, item in enumerate(scored_items, start=1):
        item["conviction_rank"] = idx

    top_pick = scored_items[0] if scored_items else None
    runner_up = scored_items[1] if len(scored_items) > 1 else None

    if top_pick:
        top_pick["badge_label"] = "RANK #1 TOP CONVICTION"
    if runner_up:
        runner_up["badge_label"] = "RANK #2 RUNNER UP"

    return {
        "top_pick": top_pick,
        "runner_up": runner_up,
        "ranked_candidates": scored_items,
        "slot_summary": {
            "equity_usd": portfolio_equity_usd,
            "slot_cap_usd": round(portfolio_equity_usd * eff_slot_fraction, 2),
            "max_slots": max_slots,
            "is_bull_regime": is_bull_regime,
            "cash_reserve_usd": round(portfolio_equity_usd * (0.01 if is_bull_regime else 0.50), 2)
        }
    }
