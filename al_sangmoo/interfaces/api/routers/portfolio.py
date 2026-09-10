import os
import json
import re
import logging
import asyncio
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator


from al_sangmoo.api.hub import hub
from al_sangmoo.core.auth import require_mutating_auth
from al_sangmoo.infrastructure.persistence import (
    add_portfolio_buy,
    get_live_portfolio,
    get_trade_history_records,
    record_execution_log,
    record_portfolio_sell,
    reset_all_holdings,
)
from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
from al_sangmoo.domain.risk.order_guardrail import validate_pre_trade_guardrail, ORDER_MUTEX
from al_sangmoo.domain.reconciliation import check_sync
from al_sangmoo.infrastructure.idempotent_order import is_broker_order_ack

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio"])

TICKER_REGEX = re.compile(r'^[A-Za-z0-9.\^=-]{1,15}$')
DATE_REGEX = re.compile(r'^\d{4}-\d{2}-\d{2}$')
REASON_REGEX = re.compile(r'^[A-Za-z0-9_\-\s\(\)가-힣.,%]{1,100}$')

class BuyOrder(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=15)
    buy_price: float = Field(..., gt=0, le=10_000_000.0)
    qty: Optional[float] = Field(default=1.0, gt=0, le=1_000_000.0)
    quantity: Optional[float] = Field(default=None, gt=0, le=1_000_000.0)
    buy_date: Optional[str] = Field(default=None)
    exchange: Optional[str] = Field(default="NASD")
    slot_rank: Optional[int] = Field(default=None, ge=1, le=5)
    max_single_asset_pct: Optional[float] = Field(default=None, gt=0.0, le=2.0)

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, v: str) -> str:
        clean = v.strip().upper()
        if not TICKER_REGEX.match(clean):
            raise ValueError(f"유효하지 않은 티커 심볼 형식입니다: '{v}'")
        return clean

    @field_validator("buy_date")
    @classmethod
    def validate_buy_date(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v.strip():
            clean = v.strip()
            if not DATE_REGEX.match(clean):
                raise ValueError("날짜 형식은 YYYY-MM-DD 이어야 합니다.")
            return clean
        return None

class SellOrder(BaseModel):
    sell_price: float = Field(..., gt=0, le=10_000_000.0)
    sell_date: Optional[str] = Field(default=None)
    reason: str = Field(default="MANUAL_SELL", max_length=100)
    exchange: Optional[str] = Field(default="NASD")

    @field_validator("sell_date")
    @classmethod
    def validate_sell_date(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v.strip():
            clean = v.strip()
            if not DATE_REGEX.match(clean):
                raise ValueError("날짜 형식은 YYYY-MM-DD 이어야 합니다.")
            return clean
        return None

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        clean = v.strip()
        if not REASON_REGEX.match(clean):
            raise ValueError("유효하지 않은 사유 형식입니다. 특수문자가 제한됩니다.")
        return clean

@router.get("")
def get_portfolio(reconcile: bool = False):
    """Returns active portfolio holdings and total account equity directly from SQLite SSOT (CQRS Pure Read)."""
    if reconcile and default_kis_broker.is_configured():
        try:
            check_sync(auto_calibrate=True)
        except Exception as e:
            logger.warning(f"Reconciliation during get_portfolio failed: {e}")
    return get_live_portfolio()

@router.post("/sync", dependencies=[Depends(require_mutating_auth)])
async def sync_portfolio_with_broker(auto_calibrate: bool = True):
    """
    Synchronizes and calibrates local SQLite portfolio against live/virtual KIS broker account.
    Calibrates exact fill prices (pchs_avg_pric), quantities, and splits.
    """
    audit_report = check_sync(auto_calibrate=auto_calibrate)
    p_data = get_live_portfolio()
    await hub.broadcast("portfolio_update", p_data)
    return {
        "status": "success",
        "report": audit_report,
        "portfolio": p_data
    }

@router.post("/buy", dependencies=[Depends(require_mutating_auth)])
async def buy_stock(order: BuyOrder):
    """
    Executes buy order via KIS Broker Gateway (if configured) and updates local SQLite portfolio.
    Enforces pre-trade risk guardrails before submitting to the broker.
    Guaranteed atomic via ORDER_MUTEX.
    """
    async with ORDER_MUTEX:
        ticker_clean = order.ticker.strip().upper()
        qty = float(order.quantity if order.quantity is not None else (order.qty or 1.0))
        price = float(order.buy_price)

        broker_status = default_kis_broker.get_status()
        order_id = None
        broker_msg = None

        # 1. Pre-Trade Guardrail Validation
        live_port = get_live_portfolio()
        if default_kis_broker.is_configured():
            balance_info = await asyncio.to_thread(default_kis_broker.get_overseas_balance)
            total_equity = float(balance_info.get("total_equity_usd", 0.0))
            active_holdings = balance_info.get("holdings", [])
            if not active_holdings:
                active_holdings = live_port.get("holdings", [])
            if total_equity <= 0 or balance_info.get("mode") == "SIMULATED":
                total_equity = float(live_port.get("total_equity_usd") or 100_000.0)
        else:
            total_equity = float(live_port.get("total_equity_usd") or 100_000.0)
            active_holdings = live_port.get("holdings", [])

        eval_res = validate_pre_trade_guardrail(
            ticker=ticker_clean,
            price=price,
            quantity=qty,
            total_equity=total_equity,
            active_holdings=active_holdings,
            max_single_asset_pct=order.max_single_asset_pct,
            slot_rank=order.slot_rank
        )
        if not eval_res["allowed"]:
            raise HTTPException(status_code=400, detail=f"사전 리스크 한도 초과: {eval_res['reason']}")

        # 2. If KIS Broker is configured, submit real/paper order to broker
        if default_kis_broker.is_configured():
            # Submit order to KIS
            broker_res = await asyncio.to_thread(
                default_kis_broker.place_order,
                ticker=ticker_clean,
                side="BUY",
                qty=int(qty) if qty >= 1 else 1,
                price=price,
                order_type="00",
                exchange=order.exchange or "NASD"
            )
            if not is_broker_order_ack(broker_res.get("status")):
                reason = broker_res.get("reason", "증권사 주문 전송 실패")
                if any(kw in str(reason) for kw in ["초당 거래건수", "EGW00201", "장종료", "모의투자", "40580000", "ORDER_TIMEOUT_UNCONFIRMED", "TIMEOUT", "주문시간", "장시작전"]):
                    logger.warning(f"Broker buy warning ({reason}). Proceeding with local SQLite order.")
                    broker_msg = f"증권사 경고: {reason}"
                else:
                    raise HTTPException(status_code=400, detail=f"증권사 주문 거부: {reason}")
            else:
                order_id = broker_res.get("order_id")
                broker_msg = broker_res.get("message")

        # 2. Record to local SQLite SSOT portfolio
        inserted_id = add_portfolio_buy(
            ticker=ticker_clean,
            buy_price=price,
            quantity=qty,
            buy_date=order.buy_date
        )

        p_data = get_live_portfolio()
        await hub.broadcast("portfolio_update", p_data)

        mode_label = broker_status.get("mode", "SIMULATOR") if default_kis_broker.is_configured() else "SIMULATOR"
        msg = f"[{mode_label}] {ticker_clean} {qty}주 매수 완료"
        if order_id:
            msg += f" (주문번호: {order_id})"

        # Record execution audit log
        try:
            record_execution_log(
                ticker=ticker_clean,
                side="BUY",
                quantity=qty,
                price=price,
                order_type="MARKETABLE_LIMIT",
                status="FILLED",
                message=msg,
                order_id=str(order_id or f"MANUAL-{int(datetime.now().timestamp())}")
            )
        except Exception:
            pass

        return {
            "status": "success",
            "id": inserted_id,
            "order_id": order_id,
            "message": msg,
            "broker_message": broker_msg
        }

@router.post("/sell/{position_id}", dependencies=[Depends(require_mutating_auth)])
async def sell_stock(position_id: int, order: SellOrder):
    """
    Executes sell order via KIS Broker Gateway (if configured) and updates local SQLite portfolio.
    Guaranteed atomic via ORDER_MUTEX.
    """
    async with ORDER_MUTEX:
        if position_id <= 0:
            raise HTTPException(status_code=400, detail="유효하지 않은 포지션 ID입니다.")

        # 1. Retrieve holding details from SQLite
        live_port = get_live_portfolio()
        holding_to_sell = None
        for h in live_port.get("holdings", []):
            if int(h["id"]) == int(position_id):
                holding_to_sell = h
                break

        if not holding_to_sell:
            raise HTTPException(status_code=404, detail="해당 보유 포지션을 찾을 수 없습니다.")

        ticker_clean = holding_to_sell["ticker"].upper()
        qty = int(holding_to_sell.get("quantity", 1))
        price = float(order.sell_price)
        order_id = None
        broker_msg = None

        # 2. If KIS Broker is configured, submit sell order to broker
        if default_kis_broker.is_configured():
            broker_res = await asyncio.to_thread(
                default_kis_broker.place_order,
                ticker=ticker_clean,
                side="SELL",
                qty=qty,
                price=price,
                order_type="00",
                exchange=order.exchange or "NASD"
            )
            if not is_broker_order_ack(broker_res.get("status")):
                reason = broker_res.get("reason", "증권사 매도 주문 전송 실패")
                # If broker reports no broker balance or rate limit glitch (e.g. unfilled limit order or paper discrepancy),
                # allow local ledger liquidation to prevent frozen UI positions
                if any(kw in str(reason) for kw in ["잔고", "40240000", "초당 거래건수", "EGW00201", "장종료", "모의투자", "40580000", "ORDER_TIMEOUT_UNCONFIRMED", "TIMEOUT", "주문시간", "장시작전"]):
                    logger.warning(f"Broker sell rejected due to broker warning ({reason}). Proceeding with local liquidation.")
                    broker_msg = f"증권사 경고: {reason}"
                else:
                    raise HTTPException(status_code=400, detail=f"증권사 매도 거부: {reason}")
            else:
                order_id = broker_res.get("order_id")
                broker_msg = broker_res.get("message")

        # 3. Update SQLite portfolio to SOLD
        success = record_portfolio_sell(
            holding_id=position_id,
            sell_price=price,
            sell_date=order.sell_date,
            reason=order.reason
        )
        if not success:
            raise HTTPException(status_code=404, detail="포지션 청산 기록에 실패했습니다.")

        p_data = get_live_portfolio()
        await hub.broadcast("portfolio_update", p_data)

        msg = f"포지션 #{position_id} ({ticker_clean} {qty}주) 매도 청산 완료"
        if order_id:
            msg += f" (주문번호: {order_id})"

        # Record execution audit log
        try:
            record_execution_log(
                ticker=ticker_clean,
                side="SELL",
                quantity=float(qty),
                price=price,
                order_type="MARKETABLE_LIMIT",
                status="FILLED",
                message=f"Manual liquidation: {order.reason}",
                order_id=str(order_id or f"MANUAL-{int(datetime.now().timestamp())}")
            )
        except Exception:
            pass

        return {
            "status": "success",
            "order_id": order_id,
            "message": msg,
            "broker_message": broker_msg
        }

@router.post("/buy_top_pick", dependencies=[Depends(require_mutating_auth)])
async def buy_top_pick():
    """
    Directly buys the Goldman Sachs-style #1 Top Conviction Pick
    with 3-Slot calibrated integer share sizing ($7,500 / 10M KRW).
    """
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
    feed_path = os.path.join(base_dir, "dashboard_data.json")
    if not os.path.exists(feed_path):
        raise HTTPException(status_code=404, detail="대시보드 분석 데이터가 존재하지 않습니다. 먼저 스캔을 실행하세요.")
    
    with open(feed_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    
    top_pick = data.get("top_conviction_pick")
    if not top_pick:
        raise HTTPException(status_code=400, detail="현재 조건에 부합하는 #1 Top Conviction 종목이 없습니다.")
    
    sizing = top_pick.get("sizing", {})
    if not sizing.get("eligible", False) or sizing.get("shares", 0) <= 0:
        reason = sizing.get("reason", "슬롯 자본금 부족")
        raise HTTPException(status_code=400, detail=f"1위 종목 매수 불가: {reason}")
    
    order = BuyOrder(
        ticker=top_pick["ticker"],
        buy_price=float(top_pick["price"]),
        quantity=float(sizing["shares"]),
        buy_date=datetime.now().strftime("%Y-%m-%d"),
        slot_rank=int(sizing.get("slot_rank", 1)),
        max_single_asset_pct=0.55
    )
    return await buy_stock(order)



@router.post("/reset", dependencies=[Depends(require_mutating_auth)])
async def reset_portfolio():
    """Resets simulated portfolio holdings."""
    reset_all_holdings()
    p_data = get_live_portfolio()
    await hub.broadcast("portfolio_update", p_data)
    return {"status": "success", "message": "포트폴리오 계좌가 성공적으로 초기화(비우기)되었습니다."}

@router.get("/history")
def get_portfolio_history():
    """Returns all past closed trades and realized returns."""
    return get_trade_history_records()


