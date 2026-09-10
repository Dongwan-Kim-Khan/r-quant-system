"""
AL-SANGMOO QUANT TERMINAL: C1-M2 CONVICTION ENGINE
Cross-sectional Composite-RS dual-momentum ranking + NAV 50/30/20 slot sizing.
"""

from typing import Any, Dict, List, Optional

from al_sangmoo.core.constants import (
    MAX_SLOTS_BEAR,
    MAX_SLOTS_BULL,
    SLOT_WEIGHTS_BEAR,
    SLOT_WEIGHTS_BULL,
    STOCK_DICT,
    TICKER_SECTORS,
    derive_stop_price,
    derive_target_price,
)
from al_sangmoo.core.feature_flags import use_residual_momentum
from al_sangmoo.domain.quant.scoring import beats_benchmark_composite_rs
from al_sangmoo.domain.risk.position_sizer import calculate_target_shares


def calculate_composite_conviction_score(
    bull_score: float,
    flow_score: float,
    msi_score: float,
    kijun_gap_pct: float,
    is_macro_tailwind: bool,
    vol_ratio: float,
    is_weekly_bull: bool,
    rs_3m: float = 0.0,
    is_breakout: bool = False,
    composite_rs: Optional[float] = None,
) -> float:
    """
    C1-M2 Composite Alpha Conviction Score (0 - 100).
    Weights:
      - 30%: 17-Year Ichimoku Bull Score & Weekly Alignment
      - 25%: Composite / 3M Relative Strength
      - 20%: Institutional Smart Money Flow & OBV Accumulation
      - 15%: Macro Climate Fit (Tailwind Sector + MSI Regime)
      - 10%: Entry Risk-Reward / Breakout Surge Bonus
    """
    weekly_mult = 1.0 if is_weekly_bull else 0.60
    tech_component = min(100.0, bull_score) * weekly_mult

    rs_input = float(composite_rs) if composite_rs is not None else float(rs_3m)
    # Map RS (-20% .. +40%) → 0..100 when values look like percentages;
    # if already a raw ratio near 0, scale the same way.
    rs_pct = rs_input * 100.0 if abs(rs_input) <= 1.5 else rs_input
    rs_normalized = min(100.0, max(0.0, (rs_pct + 20.0) * (100.0 / 60.0)))
    rs_component = rs_normalized

    flow_component = min(100.0, max(0.0, flow_score))

    msi_fit = max(0.0, 100.0 - msi_score)
    tailwind_bonus = 20.0 if is_macro_tailwind else 0.0
    macro_component = min(100.0, msi_fit * 0.8 + tailwind_bonus)

    if is_breakout:
        entry_component = 95.0
    else:
        gap_penalty = min(50.0, abs(kijun_gap_pct) * 12.0)
        vol_bonus = max(0.0, (1.0 - vol_ratio) * 50.0) if vol_ratio < 1.0 else 0.0
        entry_component = min(100.0, max(0.0, 100.0 - gap_penalty + vol_bonus))

    composite = (
        tech_component * 0.30
        + rs_component * 0.25
        + flow_component * 0.20
        + macro_component * 0.15
        + entry_component * 0.10
    )
    return round(max(0.0, min(100.0, composite)), 1)


def rank_and_select_top_picks(
    candidates: List[Dict[str, Any]],
    portfolio_equity_usd: float = 7500.0,
    msi_score: float = 50.0,
    slot_fraction: Optional[float] = None,
    is_bull_regime: bool = True,
    qqq_composite_rs: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Rank candidates by conviction; size Slot#1/#2/#3 at 50/30/20 (or bear 25/25).
    Dual-momentum: when `qqq_composite_rs` is provided, only names with
    Composite RS strictly above QQQ remain satellite-eligible.
    """
    del slot_fraction  # legacy equal-weight override removed; C1-M2 uses rank weights
    max_slots = MAX_SLOTS_BULL if is_bull_regime else MAX_SLOTS_BEAR
    weights = list(SLOT_WEIGHTS_BULL if is_bull_regime else SLOT_WEIGHTS_BEAR)

    empty_summary = {
        "equity_usd": portfolio_equity_usd,
        "slot_weights": weights,
        "slot_cap_usd": round(portfolio_equity_usd * (weights[0] if weights else 0.0), 2),
        "max_slots": max_slots,
        "is_bull_regime": is_bull_regime,
        "cash_proxy": "QQQ",
        "cash_reserve_usd": 0.0,
        "qqq_composite_rs": qqq_composite_rs,
    }

    if not candidates:
        return {
            "top_pick": None,
            "runner_up": None,
            "ranked_candidates": [],
            "slot_summary": empty_summary,
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
        kijun_gap = float(c.get("kijun_gap_pct") or c.get("kijun_gap") or 1.0)
        # Resolve the 26D kijun so downstream consumers (Autopilot Entry Gate) can
        # verify price >= baseline without a fresh network call.
        kijun_val = float(c.get("kijun") or c.get("kijun_26") or 0.0)
        if kijun_val <= 0 and price > 0 and (1.0 + kijun_gap / 100.0) > 0:
            kijun_val = round(price / (1.0 + kijun_gap / 100.0), 4)
        is_tailwind = c.get("is_macro_tailwind", False)
        vol_ratio = float(c.get("vol_ratio") or 0.80)
        # vol_ratio in feed may already be percentage (55) vs fraction (0.55)
        if vol_ratio > 3.0:
            vol_ratio = vol_ratio / 100.0
        is_weekly_bull = c.get("is_weekly_bull", True)
        rs_3m = float(c.get("rs_3m") or c.get("momentum_3m") or 0.0)
        composite_rs = c.get("composite_rs")
        if composite_rs is not None:
            composite_rs = float(composite_rs)
        is_breakout = bool(c.get("is_breakout", False))

        # Dual-momentum gate vs QQQ Composite RS (when benchmark available)
        dual_mom_ok = True
        if qqq_composite_rs is not None and composite_rs is not None:
            dual_mom_ok = beats_benchmark_composite_rs(composite_rs, float(qqq_composite_rs))
        elif qqq_composite_rs is not None and c.get("rs_3m") is not None:
            # Fallback: compare 3M RS when full composite unavailable
            dual_mom_ok = float(rs_3m) > float(qqq_composite_rs)

        if not dual_mom_ok:
            continue

        conviction_score = calculate_composite_conviction_score(
            bull_score=score,
            flow_score=flow_score,
            msi_score=msi_score,
            kijun_gap_pct=kijun_gap,
            is_macro_tailwind=is_tailwind,
            vol_ratio=vol_ratio,
            is_weekly_bull=is_weekly_bull,
            rs_3m=rs_3m,
            is_breakout=is_breakout,
            composite_rs=composite_rs,
        )

        if use_residual_momentum():
            residual = float(c.get("residual_momentum") or 0.0)
            conviction_score = round(max(0.0, min(100.0, conviction_score + residual * 0.05)), 1)

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
            "composite_rs": composite_rs,
            "beats_qqq_crs": True,
            "is_breakout": is_breakout,
            "in_wallet": c.get("in_wallet", False),
            "streak_days": c.get("streak_days", 1),
        })

    scored_items.sort(key=lambda x: x["conviction_score"], reverse=True)

    for idx, item in enumerate(scored_items, start=1):
        item["conviction_rank"] = idx
        sizing = calculate_target_shares(
            portfolio_nav=portfolio_equity_usd,
            current_price=item["price"],
            slot_rank=idx if idx <= max_slots else max_slots,
            is_bull=is_bull_regime,
        )
        # Ranks beyond max_slots stay scored but are not deployable this cycle
        if idx > max_slots:
            sizing = dict(sizing)
            sizing["eligible"] = False
            sizing["reason"] = f"Rank #{idx} exceeds regime max slots ({max_slots})"
        item["sizing"] = sizing
        item["slot_weight"] = sizing.get("slot_weight", 0.0)
        item["rationale"] = (
            f"확신도 {item['conviction_score']}점 (CRS/RS: {item.get('composite_rs') or item['rs_3m']}) | "
            f"{item['name']} ({item['ticker']}) - 슬롯#{idx} "
            f"{sizing.get('shares', 0)}주(${sizing.get('allocated_usd', 0):,.2f})"
        )

    top_pick = scored_items[0] if scored_items else None
    runner_up = scored_items[1] if len(scored_items) > 1 else None

    if top_pick:
        top_pick["badge_label"] = "RANK #1 TOP CONVICTION (50%)"
    if runner_up:
        runner_up["badge_label"] = "RANK #2 RUNNER UP (30%)"

    return {
        "top_pick": top_pick,
        "runner_up": runner_up,
        "ranked_candidates": scored_items,
        "slot_summary": empty_summary,
    }
