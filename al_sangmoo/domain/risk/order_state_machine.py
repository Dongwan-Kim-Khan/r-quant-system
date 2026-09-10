"""
Nautilus Trader inspired order finite-state machine.
Gated by USE_NAUTILUS_ORDER_FSM.
"""
from __future__ import annotations

from enum import Enum, auto
from typing import Dict, Optional


class OrderStatus(Enum):
    CREATED = auto()
    PRE_CHECK_PASSED = auto()
    SUBMITTING = auto()
    ACCEPTED = auto()
    FILLED = auto()
    REJECTED = auto()
    CANCELED = auto()
    EXPIRED = auto()


TERMINAL_STATES = {
    OrderStatus.FILLED,
    OrderStatus.REJECTED,
    OrderStatus.CANCELED,
    OrderStatus.EXPIRED,
}


class NautilusInspiredOrderGuardrail:
    def __init__(self, max_spread_bps: float = 50.0):
        self.max_spread_bps = float(max_spread_bps)
        self._inflight_orders: Dict[str, OrderStatus] = {}

    def pre_trade_risk_check(
        self,
        ticker: str,
        side: str = "BUY",
        ask_price: Optional[float] = None,
        bid_price: Optional[float] = None,
    ) -> bool:
        key = ticker.strip().upper()
        if key in self._inflight_orders:
            raise RuntimeError(
                f"중복 주문 거부: {key}에 대해 이미 진행 중인 주문({self._inflight_orders[key].name})이 존재합니다."
            )
        if ask_price is not None and bid_price is not None and ask_price > 0 and bid_price > 0:
            mid = (float(ask_price) + float(bid_price)) / 2.0
            spread_bps = ((float(ask_price) - float(bid_price)) / mid) * 10000.0
            if spread_bps > self.max_spread_bps:
                raise ValueError(
                    f"리스크 거부: {key} 스프레드({spread_bps:.1f} bps)가 허용치({self.max_spread_bps} bps)를 초과했습니다."
                )
        self._inflight_orders[key] = OrderStatus.PRE_CHECK_PASSED
        return True

    def transition_state(self, ticker: str, next_state: OrderStatus) -> None:
        key = ticker.strip().upper()
        if next_state in TERMINAL_STATES:
            self._inflight_orders.pop(key, None)
        else:
            self._inflight_orders[key] = next_state

    def is_inflight(self, ticker: str) -> bool:
        return ticker.strip().upper() in self._inflight_orders


default_order_fsm = NautilusInspiredOrderGuardrail()
