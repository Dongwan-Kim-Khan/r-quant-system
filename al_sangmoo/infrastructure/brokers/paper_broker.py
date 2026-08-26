"""
High-Fidelity Paper Trading Broker Adapter.
Synchronizes with SQLite portfolio persistence.
"""
from typing import Dict, Any, List
from datetime import datetime
from al_sangmoo.domain.interfaces.execution_gateway import IExecutionGateway
from al_sangmoo.infrastructure.persistence import add_portfolio_buy, record_portfolio_sell, get_live_portfolio

class PaperTradingBroker(IExecutionGateway):
    def __init__(self, initial_cash: float = 100000.0, slippage_bps: float = 0.0010):
        self.initial_cash = initial_cash
        self.slippage_bps = slippage_bps

    def submit_buy_order(self, ticker: str, price: float, quantity: float, stop_loss: float = None, target_price: float = None) -> Dict[str, Any]:
        fill_price = round(price * (1.0 + self.slippage_bps), 2)
        total_cost = round(fill_price * quantity, 2)
        
        pos_id = add_portfolio_buy(
            ticker=ticker.strip().upper(),
            buy_price=fill_price,
            quantity=quantity,
            buy_date=datetime.now().strftime("%Y-%m-%d")
        )
        
        return {
            "status": "FILLED",
            "order_type": "BUY",
            "position_id": pos_id,
            "ticker": ticker.upper(),
            "requested_price": price,
            "fill_price": fill_price,
            "quantity": quantity,
            "total_cost": total_cost,
            "target_price": target_price or round(fill_price * 1.15, 2),
            "stop_loss_price": stop_loss or round(fill_price * 0.96, 2),
            "execution_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    def submit_sell_order(self, position_id: int, price: float, reason: str = "MANUAL_SELL") -> Dict[str, Any]:
        fill_price = round(price * (1.0 - self.slippage_bps), 2)
        success = record_portfolio_sell(
            holding_id=position_id,
            sell_price=fill_price,
            sell_date=datetime.now().strftime("%Y-%m-%d"),
            reason=reason
        )
        if not success:
            return {"status": "REJECTED", "message": f"Position #{position_id} not found."}
            
        return {
            "status": "FILLED",
            "order_type": "SELL",
            "position_id": position_id,
            "fill_price": fill_price,
            "reason": reason,
            "execution_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    def get_positions(self) -> List[Dict[str, Any]]:
        portfolio = get_live_portfolio()
        return portfolio.get("holdings", [])

    def get_account_balance(self) -> Dict[str, Any]:
        portfolio = get_live_portfolio()
        total_invested = portfolio.get("total_invested", 0.0)
        total_eval = portfolio.get("total_eval", 0.0)
        cash = max(0.0, self.initial_cash - total_invested)
        total_equity = cash + total_eval
        
        return {
            "cash_available": round(cash, 2),
            "total_invested": round(total_invested, 2),
            "total_eval": round(total_eval, 2),
            "total_equity": round(total_equity, 2),
            "overall_pnl_pct": round(portfolio.get("overall_pnl_pct", 0.0), 2)
        }

default_broker = PaperTradingBroker()
