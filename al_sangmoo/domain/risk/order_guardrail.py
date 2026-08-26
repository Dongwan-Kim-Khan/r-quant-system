"""
Pre-Trade Risk Guardrails & Sanity Validation Engine.
"""
from typing import Dict, Any, List
import re
import asyncio

TICKER_REGEX = re.compile(r'^[A-Za-z0-9.\^=-]{1,15}$')
ORDER_MUTEX = asyncio.Lock()

def validate_pre_trade_guardrail(
    ticker: str,
    price: float,
    quantity: float,
    total_equity: float,
    active_holdings: List[Dict[str, Any]],
    msi_score: float = 50.0,
    max_single_asset_pct: float = 0.25,
    min_order_dollar: float = 10.0
) -> Dict[str, Any]:
    """
    Executes mandatory pre-trade sanity & risk boundary checks.
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

    # 2. Systemic Macro Guardrail (CASH_EXIT Rule)
    if msi_score >= 75.0:
        return {
            "allowed": False,
            "reason": f"[CASH_EXIT] 거시 위험 지수 임계치 초과(MSI {msi_score:.1f}점)로 신규 매수가 전면 차단되었습니다."
        }

    # 3. Single-Asset Max Allocation Cap (25% Equity Rule)
    existing_holding_val = sum(float(h.get('current_value', h.get('total_cost', 0))) for h in active_holdings if h.get('ticker') == ticker_clean)
    combined_val = existing_holding_val + order_value
    
    if total_equity > 0:
        allocation_fraction = combined_val / total_equity
        if allocation_fraction > max_single_asset_pct:
            max_allowed_val = total_equity * max_single_asset_pct
            return {
                "allowed": False,
                "reason": f"단일 종목 최대 한도({max_single_asset_pct*100:.0f}%, ${max_allowed_val:,.2f})를 초과합니다 (요청금액: ${combined_val:,.2f})."
            }

    return {
        "allowed": True,
        "ticker": ticker_clean,
        "order_value": order_value,
        "reason": "Pre-trade risk guardrails passed."
    }
