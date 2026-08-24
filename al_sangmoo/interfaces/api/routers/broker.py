from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
from al_sangmoo.domain.risk.order_guardrail import validate_pre_trade_guardrail

router = APIRouter(prefix="/api/broker", tags=["Broker Execution"])

class BrokerOrderRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=15)
    side: Optional[str] = Field(default="BUY", description="'BUY' or 'SELL'")
    qty: Optional[int] = Field(default=None, gt=0, le=100_000)
    quantity: Optional[float] = Field(default=None, gt=0, le=100_000.0)
    price: Optional[float] = Field(default=0.0, ge=0.0)
    buy_price: Optional[float] = Field(default=None, ge=0.0)
    buy_date: Optional[str] = Field(default=None)
    order_type: str = Field(default="01", description="'01': Market, '00': Limit")

@router.get("/status")
def get_broker_status():
    """Returns KIS broker adapter configuration, mode, and token validity."""
    return default_kis_broker.get_status()

@router.get("/balance")
def get_broker_balance():
    """Queries live or simulated cash balance and portfolio equity from KIS."""
    return default_kis_broker.get_account_balance()

@router.post("/order")
def execute_broker_order(order: BrokerOrderRequest):
    """
    Submits a live or simulated order to Korea Investment & Securities.
    Enforces pre-trade risk guardrails before sending to the exchange.
    """
    final_qty = int(order.qty if order.qty is not None else (order.quantity or 1))
    final_price = float(order.price if order.price > 0 else (order.buy_price or 0.0))
    final_side = (order.side or "BUY").upper()

    # 1. Pre-Trade Guardrail Validation
    balance_info = default_kis_broker.get_account_balance()
    total_equity = float(balance_info.get("total_equity", 100_000.0))
    active_holdings = balance_info.get("holdings", [])
    
    order_price = final_price if final_price > 0 else 100.0
    eval_res = validate_pre_trade_guardrail(
        ticker=order.ticker,
        price=order_price,
        quantity=float(final_qty),
        total_equity=total_equity,
        active_holdings=active_holdings,
        max_single_asset_pct=0.25
    )
    if not eval_res["allowed"] and final_side == "BUY":
        raise HTTPException(status_code=400, detail=f"Pre-Trade Risk Violation: {eval_res['reason']}")

    # 2. Execute Order via Broker Adapter
    result = default_kis_broker.place_order(
        ticker=order.ticker,
        side=final_side,
        qty=final_qty,
        price=final_price,
        order_type=order.order_type
    )
    return result
