"""
Pre-Trade Risk Guardrails & Sanity Validation Engine.
"""
from typing import Dict, Any, List, Optional
import os
import json
import re
import asyncio

from al_sangmoo.core.feature_flags import use_nautilus_order_fsm
from al_sangmoo.domain.risk.order_state_machine import default_order_fsm
from al_sangmoo.domain.risk.cash_proxy import is_proxy_ticker

TICKER_REGEX = re.compile(r'^[A-Za-z0-9.\^=-]{1,15}$')
ORDER_MUTEX = asyncio.Lock()


def load_feed_snapshot() -> Dict[str, Any]:
    """Load latest dashboard feed snapshot for macro regime and top conviction context."""
    try:
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        feed_path = os.path.join(base_dir, "dashboard_data.json")
        if os.path.exists(feed_path):
            with open(feed_path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception:
        pass
    return {}


def resolve_guardrail_max_allocation_pct(
    ticker: str,
    active_holdings: Optional[List[Dict[str, Any]]] = None,
    slot_rank: Optional[int] = None,
    is_bull: Optional[bool] = None,
    leverage_mode: Optional[bool] = None,
    msi_score: Optional[float] = None,
    explicit_max_pct: Optional[float] = None,
    feed_data: Optional[Dict[str, Any]] = None,
) -> float:
    """
    Resolves the maximum equity allocation percentage allowed for a given pre-trade order.
    
    1. If explicit_max_pct is provided (> 0), it is respected directly.
    2. Cash Proxy (QQQ, QLD):
       - Core / unleveraged: up to 100% (1.00)
       - Leveraged mode or QLD: up to 150% (1.50)
    3. C1-M2 Satellite Slots:
       - Bull regime (SPY >= SMA200 or MSI < 50):
         Slot 1: 50% target + 5% buffer -> 55% (0.55)
         Slot 2: 30% target + 5% buffer -> 35% (0.35)
         Slot 3: 20% target + 5% buffer -> 25% (0.25)
       - Bear regime (MSI >= 50 or SPY < SMA200):
         Slot 1: 25% target + 5% buffer -> 30% (0.30)
         Slot 2: 25% target + 5% buffer -> 30% (0.30)
       - Other / fallback / unranked: 25% (0.25)
    """
    if explicit_max_pct is not None and explicit_max_pct > 0.0:
        return float(explicit_max_pct)

    ticker_clean = ticker.strip().upper()

    # Load dashboard feed for regime & top picks context if available
    feed: Dict[str, Any] = feed_data if feed_data is not None else load_feed_snapshot()

    slot_sum = feed.get("slot_allocation_summary") or {}
    macro_info = feed.get("macro") or {}

    # 1. Cash Proxy Check (QQQ, QLD)
    if is_proxy_ticker(ticker_clean):
        if leverage_mode is None:
            feed_lev = (slot_sum.get("leverage") or {}).get("leverage_mode")
            if feed_lev is not None:
                leverage_mode = bool(feed_lev)
            else:
                try:
                    from al_sangmoo.domain.risk.portfolio_guardian import default_guardian
                    leverage_mode = default_guardian._current_leverage_mode()
                except Exception:
                    leverage_mode = False
        return 1.50 if (leverage_mode or ticker_clean == "QLD") else 1.00

    # 2. Macro Regime (Bull vs Bear)
    if is_bull is None:
        if "is_bull_regime" in slot_sum:
            is_bull = bool(slot_sum["is_bull_regime"])
        elif "is_bull_regime" in macro_info:
            is_bull = bool(macro_info["is_bull_regime"])
        else:
            try:
                from al_sangmoo.domain.quant.macro import extract_msi_score, resolve_capital_regime
                effective_msi = msi_score if msi_score is not None else extract_msi_score(macro_info, default=50.0)
                spy_c = macro_info.get("spy_close")
                spy_s = macro_info.get("spy_sma200")
                cap = resolve_capital_regime(
                    msi_score=effective_msi,
                    spy_close=float(spy_c) if spy_c is not None else None,
                    spy_sma200=float(spy_s) if spy_s is not None else None,
                    fetch_spy=False,
                )
                is_bull = bool(cap.get("is_bull_regime"))
            except Exception:
                effective_msi = msi_score if msi_score is not None else 50.0
                is_bull = (effective_msi < 50.0)

    # 3. Slot Rank Detection
    target_rank = slot_rank
    if target_rank is None:
        # 3a. Check top conviction pick & runner-up & ranked candidates
        top_pick = feed.get("top_conviction_pick") or {}
        runner_up = feed.get("top_conviction_runner_up") or {}
        if ticker_clean == str(top_pick.get("ticker", "")).strip().upper():
            target_rank = int(top_pick.get("sizing", {}).get("slot_rank", 1))
        elif ticker_clean == str(runner_up.get("ticker", "")).strip().upper():
            target_rank = int(runner_up.get("sizing", {}).get("slot_rank", 2))
        else:
            ranked_list = feed.get("ranked_conviction_list") or []
            for item in ranked_list:
                if isinstance(item, dict) and str(item.get("ticker", "")).strip().upper() == ticker_clean:
                    target_rank = int(item.get("sizing", {}).get("slot_rank", 3))
                    break

    if target_rank is None:
        # 3b. Derive from active satellite holdings (deduplicating multiple lots/executions)
        sat_tickers = [
            str(h.get("ticker", "")).strip().upper()
            for h in (active_holdings or [])
            if str(h.get("ticker", "")).strip() and not is_proxy_ticker(str(h.get("ticker", "")))
        ]
        distinct_sats = list(dict.fromkeys(sat_tickers))
        if ticker_clean in distinct_sats:
            target_rank = distinct_sats.index(ticker_clean) + 1
        else:
            target_rank = len(distinct_sats) + 1

    # 4. Apply C1-M2 Limits with buffer
    if is_bull:
        if target_rank == 1:
            return 0.55  # 50% + 5% buffer
        elif target_rank == 2:
            return 0.35  # 30% + 5% buffer
        elif target_rank == 3:
            return 0.25  # 20% + 5% buffer
        else:
            return 0.25
    else:
        if target_rank in (1, 2):
            return 0.30  # 25% + 5% buffer
        else:
            return 0.25


def validate_pre_trade_guardrail(
    ticker: str,
    price: float,
    quantity: float,
    total_equity: float,
    active_holdings: List[Dict[str, Any]],
    msi_score: Optional[float] = None,
    max_single_asset_pct: Optional[float] = None,
    min_order_dollar: float = 10.0,
    ask_price: float = None,
    bid_price: float = None,
    slot_rank: Optional[int] = None,
    is_bull: Optional[bool] = None,
    leverage_mode: Optional[bool] = None,
    feed_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Executes mandatory pre-trade sanity & risk boundary checks.
    Supports C1-M2 dynamic conviction slot sizing and Cash Proxy (QQQ/QLD) limits.
    """
    ticker_clean = ticker.strip().upper()
    
    # 1. Format & Input Sanity
    if not TICKER_REGEX.match(ticker_clean):
        return {"allowed": False, "reason": f"유효하지 않은 티커 심볼 형식입니다: {ticker}"}
        
    if price <= 0.0 or quantity <= 0.0:
        return {"allowed": False, "reason": "가격과 수량은 0보다 커야 합니다."}

    order_value = price * quantity
    if order_value < min_order_dollar:
        return {"allowed": False, "reason": f"최소 주문 금액(${min_order_dollar}) 미만입니다."}

    # 2. Systemic Macro Guardrail (CASH_EXIT Rule) — MSI is a risk index
    if msi_score is None:
        try:
            _f = feed_data if feed_data is not None else load_feed_snapshot()
            from al_sangmoo.domain.quant.macro import extract_msi_score
            msi_score = extract_msi_score(_f.get("macro"))
        except Exception:
            msi_score = 50.0
    if msi_score is None:
        msi_score = 50.0

    from al_sangmoo.domain.quant.macro import is_new_buy_blocked
    if is_new_buy_blocked(msi_score):
        return {
            "allowed": False,
            "reason": f"[CASH_EXIT] 거시 위험 지수 임계치 초과(MSI {msi_score:.1f}점)로 신규 매수가 전면 차단되었습니다."
        }

    # 3. Single-Asset Max Allocation Cap (Dynamic C1-M2 / Cash Proxy Sizing)
    effective_max_pct = resolve_guardrail_max_allocation_pct(
        ticker=ticker_clean,
        active_holdings=active_holdings,
        slot_rank=slot_rank,
        is_bull=is_bull,
        leverage_mode=leverage_mode,
        msi_score=msi_score,
        explicit_max_pct=max_single_asset_pct,
        feed_data=feed_data,
    )

    existing_holding_val = sum(
        float(h.get('current_value', h.get('total_cost', 0)))
        for h in (active_holdings or [])
        if str(h.get('ticker', '')).strip().upper() == ticker_clean
    )
    combined_val = existing_holding_val + order_value
    
    if total_equity > 0:
        allocation_fraction = combined_val / total_equity
        if allocation_fraction > effective_max_pct:
            max_allowed_val = total_equity * effective_max_pct
            return {
                "allowed": False,
                "reason": f"단일 종목 최대 한도({effective_max_pct*100:.0f}%, ${max_allowed_val:,.2f})를 초과합니다 (요청금액: ${combined_val:,.2f})."
            }

    if use_nautilus_order_fsm():
        try:
            default_order_fsm.pre_trade_risk_check(
                ticker_clean, "BUY", ask_price=ask_price, bid_price=bid_price
            )
        except (RuntimeError, ValueError) as exc:
            return {"allowed": False, "reason": str(exc)}

    return {
        "allowed": True,
        "ticker": ticker_clean,
        "order_value": order_value,
        "max_single_asset_pct": effective_max_pct,
        "reason": "Pre-trade risk guardrails passed."
    }
