import re
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
import db_manager
from al_sangmoo.api.hub import hub

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
def get_portfolio():
    """Returns active portfolio holdings and total account equity."""
    return db_manager.get_live_portfolio()

@router.post("/buy")
async def buy_stock(order: BuyOrder):
    """Executes simulated paper buy and broadcasts portfolio update."""
    ticker_clean = order.ticker.strip().upper()
    qty = order.quantity if order.quantity is not None else (order.qty or 1.0)
    
    inserted_id = db_manager.add_portfolio_buy(
        ticker=ticker_clean,
        buy_price=order.buy_price,
        quantity=qty,
        buy_date=order.buy_date
    )
    p_data = db_manager.get_live_portfolio()
    await hub.broadcast("portfolio_update", p_data)
    return {"status": "success", "id": inserted_id, "message": f"{ticker_clean} {qty}주 매수 등록 완료!"}

@router.post("/sell/{position_id}")
async def sell_stock(position_id: int, order: SellOrder):
    """Executes simulated paper sell/exit and broadcasts portfolio update."""
    if position_id <= 0:
        raise HTTPException(status_code=400, detail="유효하지 않은 포지션 ID입니다.")
        
    success = db_manager.record_portfolio_sell(
        holding_id=position_id,
        sell_price=order.sell_price,
        sell_date=order.sell_date,
        reason=order.reason
    )
    if not success:
        raise HTTPException(status_code=404, detail="포지션을 찾을 수 없습니다.")
    p_data = db_manager.get_live_portfolio()
    await hub.broadcast("portfolio_update", p_data)
    return {"status": "success", "message": f"포지션 #{position_id} 매도 완료 처리되었습니다."}

@router.post("/reset")
async def reset_portfolio():
    """Resets simulated portfolio holdings."""
    db_manager.reset_all_holdings()
    p_data = db_manager.get_live_portfolio()
    await hub.broadcast("portfolio_update", p_data)
    return {"status": "success", "message": "포트폴리오 계좌가 성공적으로 초기화(비우기)되었습니다."}

@router.get("/history")
def get_portfolio_history():
    """Returns all past closed trades and realized returns."""
    return db_manager.get_trade_history_records()
