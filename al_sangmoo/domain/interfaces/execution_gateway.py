"""
Polymorphic Broker Adapter Gateway Interface for Automated Order Execution.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, List

class IExecutionGateway(ABC):
    
    @abstractmethod
    def submit_buy_order(self, ticker: str, price: float, quantity: float, stop_loss: float = None, target_price: float = None) -> Dict[str, Any]:
        """Submits a buy order to the execution venue."""
        pass

    @abstractmethod
    def submit_sell_order(self, position_id: int, price: float, reason: str = "MANUAL_SELL") -> Dict[str, Any]:
        """Submits a sell order / liquidation to the execution venue."""
        pass

    @abstractmethod
    def get_positions(self) -> List[Dict[str, Any]]:
        """Retrieves active holdings from the execution venue."""
        pass

    @abstractmethod
    def get_account_balance(self) -> Dict[str, Any]:
        """Retrieves cash, equity, and buying power."""
        pass
