import re
import asyncio
from typing import Optional, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from al_sangmoo.core.auth import require_mutating_auth
from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
from al_sangmoo.infrastructure.idempotent_order import is_broker_order_ack
from al_sangmoo.domain.risk import exit_cooldown
from al_sangmoo.domain.risk.cash_proxy import is_proxy_ticker
from al_sangmoo.domain.risk.order_guardrail import validate_pre_trade_guardrail, ORDER_MUTEX
from al_sangmoo.domain.reconciliation import check_sync
from al_sangmoo.infrastructure.persistence import (
    add_portfolio_buy,
    get_live_portfolio,
    record_execution_log,
    record_portfolio_ticker_sell_quantity,
)

router = APIRouter(prefix="/api/broker", tags=["Broker Execution"])

TICKER_REGEX = re.compile(r'^[A-Za-z0-9.\^=-]{1,15}$')

class BrokerOrderRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=15)
    side: Optional[Literal["BUY", "SELL", "buy", "sell"]] = Field(default="BUY", description="'BUY' or 'SELL'")
    qty: Optional[int] = Field(default=None, gt=0, le=100_000)
    quantity: Optional[float] = Field(default=None, gt=0, le=100_000.0)
    price: Optional[float] = Field(default=0.0, ge=0.0)
    buy_price: Optional[float] = Field(default=None, ge=0.0)
    buy_date: Optional[str] = Field(default=None)
    order_type: Optional[str] = Field(default="00", description="'00': Limit (지정가), '01': Market")
    exchange: Optional[str] = Field(default="NASD", description="'NASD', 'NYSE', 'AMEX'")
    slot_rank: Optional[int] = Field(default=None, ge=1, le=5)
    max_single_asset_pct: Optional[float] = Field(default=None, gt=0.0, le=2.0)

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, v: str) -> str:
        clean = v.strip().upper()
        if not TICKER_REGEX.match(clean):
            raise ValueError(f"유효하지 않은 티커 심볼 형식입니다: '{v}'")
        return clean

    @field_validator("side")
    @classmethod
    def validate_side(cls, v: Optional[str]) -> str:
        if not v:
            return "BUY"
        clean = v.strip().upper()
        if clean not in ("BUY", "SELL"):
            raise ValueError("주문 방향은 'BUY' 또는 'SELL' 이어야 합니다.")
        return clean

@router.get("/status")
def get_broker_status():
    """Returns KIS broker adapter configuration, mode, and token validity."""
    return default_kis_broker.get_status()

@router.get("/balance")
def get_broker_balance():
    """Queries live US equity cash balance and portfolio equity from KIS."""
    return default_kis_broker.get_account_balance()


@router.post("/reconcile", dependencies=[Depends(require_mutating_auth)])
def run_reconciliation(auto_calibrate: bool = True):
    """
    Executes a 1-time daily reconciliation audit between KIS Broker and local SQLite DB.
    Calibrates corporate actions (splits, dividend shares) and manual trades.
    """
    return check_sync(auto_calibrate=auto_calibrate)

@router.post("/order", dependencies=[Depends(require_mutating_auth)])
async def execute_broker_order(order: BrokerOrderRequest):
    """
    Submits a live/virtual order to Korea Investment & Securities (KIS).
    Enforces pre-trade risk guardrails before sending to the exchange and
    records the trade to local SQLite immediately upon execution.
    Guaranteed atomic via ORDER_MUTEX.
    """
    async with ORDER_MUTEX:
        final_qty = int(order.qty if order.qty is not None else (order.quantity or 1))
        final_price = float(order.price if order.price > 0 else (order.buy_price or 0.0))
        final_side = (order.side or "BUY").upper()
        ticker_clean = order.ticker.upper()
        if (
            final_side == "BUY"
            and not is_proxy_ticker(ticker_clean)
            and exit_cooldown.is_in_cooldown(ticker_clean)
        ):
            raise HTTPException(
                status_code=409,
                detail=f"{ticker_clean} is inside the persistent 24-hour exit lockout",
            )

        # 1. Pre-Trade Guardrail Validation
        balance_info = await asyncio.to_thread(default_kis_broker.get_overseas_balance)
        total_equity = float(balance_info.get("total_equity_usd", 0.0))
        active_holdings = balance_info.get("holdings", [])
        if not active_holdings:
            live_port = get_live_portfolio()
            active_holdings = live_port.get("holdings", [])
        if total_equity <= 0 or balance_info.get("mode") == "SIMULATED":
            live_port = get_live_portfolio()
            total_equity = float(live_port.get("total_equity_usd") or 100_000.0)
        
        order_price = final_price if final_price > 0 else 100.0
        eval_res = validate_pre_trade_guardrail(
            ticker=order.ticker,
            price=order_price,
            quantity=float(final_qty),
            total_equity=total_equity,
            active_holdings=active_holdings,
            max_single_asset_pct=order.max_single_asset_pct,
            slot_rank=order.slot_rank
        )
        if not eval_res["allowed"] and final_side == "BUY":
            raise HTTPException(status_code=400, detail=f"Pre-Trade Risk Violation: {eval_res['reason']}")

        # 2. Execute Order via KIS Broker Gateway
        result = await asyncio.to_thread(
            default_kis_broker.place_order,
            ticker=order.ticker,
            side=final_side,
            qty=final_qty,
            price=final_price,
            order_type=order.order_type,
            exchange=order.exchange
        )

        # 3. If Order Submitted / Filled, sync to SQLite
        if is_broker_order_ack(result.get("status")):
            if result.get("status") != "filled":
                # Order acceptance is not a fill. Reconciliation will update the
                # position ledger and execution log after the broker book changes.
                return result
            execution_log = {
                "order_type": (
                    "MARKETABLE_LIMIT" if order.order_type == "00" else "MARKET"
                ),
                "status": (
                    "SIMULATED"
                    if str(result.get("mode") or "").upper() == "SIMULATION"
                    else "FILLED"
                ),
                "message": f"Broker {final_side} order executed",
                "order_id": str(result.get("order_id", "")),
            }
            if final_side == "BUY":
                add_portfolio_buy(
                    ticker=order.ticker.upper(),
                    buy_price=final_price if final_price > 0 else 100.0,
                    quantity=float(final_qty),
                    buy_date=order.buy_date,
                    execution_log=execution_log,
                )
            elif final_side == "SELL":
                live_port = get_live_portfolio()
                ticker_holdings = [
                    h for h in live_port.get("holdings", [])
                    if h["ticker"].upper() == ticker_clean
                ]
                local_qty = sum(
                    float(h.get("quantity") or 0.0) for h in ticker_holdings
                )
                fallback_price = (
                    float(ticker_holdings[0].get("current_price") or 0.0)
                    if ticker_holdings
                    else 0.0
                )
                sold_local = record_portfolio_ticker_sell_quantity(
                    ticker=ticker_clean,
                    sell_price=final_price if final_price > 0 else fallback_price,
                    quantity=float(final_qty),
                    reason="BROKER_LIVE_SELL",
                    execution_log=execution_log,
                )
                if sold_local <= 0:
                    record_execution_log(
                        ticker=order.ticker.upper(),
                        side=final_side,
                        quantity=float(final_qty),
                        price=final_price if final_price > 0 else 100.0,
                        **execution_log,
                    )
                if (
                    local_qty > 0
                    and final_qty + 1e-9 >= local_qty
                    and not is_proxy_ticker(ticker_clean)
                ):
                    exit_cooldown.record_exit(
                        ticker_clean,
                        reason="BROKER_LIVE_SELL",
                        source="MANUAL_BROKER",
                        order_id=str(result.get("order_id") or ""),
                    )

            # Broadcast updated portfolio to all connected WebSocket clients
            try:
                from al_sangmoo.api.hub import hub
                await hub.broadcast("portfolio_update", get_live_portfolio())
            except Exception:
                pass

        return result

