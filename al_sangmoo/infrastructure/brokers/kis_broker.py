"""
AL-SANGMOO QUANT TERMINAL: KOREA INVESTMENT & SECURITIES (KIS) REST API ADAPTER
Provides automated domestic (KRX) and overseas (US) live order execution and account balance queries.
"""

import os
import json
import time
import requests
from typing import Dict, Any, Optional
from datetime import datetime

class KISBrokerAdapter:
    """
    Broker adapter for Korea Investment & Securities (한국투자증권) OpenAPI.
    Supports both Virtual (모의투자) and Real (실전계좌) trading environments.
    """

    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        account_no: Optional[str] = None,
        account_code: str = "01",
        is_paper: bool = True
    ):
        self.app_key = app_key or os.environ.get("KIS_APP_KEY", "")
        self.app_secret = app_secret or os.environ.get("KIS_APP_SECRET", "")
        self.account_no = account_no or os.environ.get("KIS_ACCOUNT_NO", "")
        self.account_code = account_code or os.environ.get("KIS_ACCOUNT_CODE", "01")
        self.is_paper = is_paper or (os.environ.get("KIS_ENV", "PAPER").upper() == "PAPER")

        # Base URLs for Real vs Mock Paper Trading
        if self.is_paper:
            self.base_url = "https://openapivts.koreainvestment.com:29443"
        else:
            self.base_url = "https://openapi.koreainvestment.com:9443"

        self.token: Optional[str] = None
        self.token_expiry: float = 0.0

    def is_configured(self) -> bool:
        """Checks whether KIS API credentials have been provided in environment or constructor."""
        return bool(self.app_key and self.app_secret and self.account_no)

    def get_status(self) -> Dict[str, Any]:
        """Returns the current connection and mode status of the KIS broker adapter."""
        masked_acc = f"{self.account_no[:4]}****{self.account_no[-2:]}" if len(self.account_no) >= 8 else "NOT_CONFIGURED"
        return {
            "broker": "Korea Investment & Securities (한국투자증권)",
            "environment": "MOCK_PAPER" if self.is_paper else "REAL_PRODUCTION",
            "is_configured": self.is_configured(),
            "account_no_masked": masked_acc,
            "token_valid": (self.token is not None and time.time() < self.token_expiry)
        }

    def authenticate(self) -> bool:
        """
        Obtains or refreshes OAuth 2.0 access token from KIS API.
        If credentials are not configured, runs in simulated paper mode.
        """
        if not self.is_configured():
            # In simulation mode, generate a mock valid session
            self.token = "MOCK_KIS_SESSION_TOKEN"
            self.token_expiry = time.time() + 86400
            return True

        if self.token and time.time() < self.token_expiry - 300:
            return True

        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret
        }
        headers = {"content-type": "application/json"}

        try:
            res = requests.post(url, json=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                self.token = data.get("access_token")
                expires_in = int(data.get("expires_in", 86400))
                self.token_expiry = time.time() + expires_in
                return True
            else:
                print(f"[KIS Auth Error] Status {res.status_code}: {res.text}")
                return False
        except Exception as exc:
            print(f"[KIS Auth Exception] {exc}")
            return False

    def get_account_balance(self) -> Dict[str, Any]:
        """Queries cash balance and portfolio valuation."""
        if not self.is_configured():
            # Return simulation balance
            return {
                "status": "success",
                "mode": "SIMULATED",
                "total_equity": 100_000.0,
                "cash_available": 65_000.0,
                "stock_evaluation": 35_000.0,
                "holdings_count": 0
            }

        self.authenticate()
        # Tr_id for domestic balance inquiry: TTTC8434R (Real) / VTTC8434R (Paper)
        tr_id = "VTTC8434R" if self.is_paper else "TTTC8434R"
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id
        }
        params = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.account_code,
            "AFHR_FLG": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }

        try:
            res = requests.get(url, headers=headers, params=params, timeout=10)
            if res.status_code == 200:
                data = res.json()
                output2 = data.get("output2", [{}])[0]
                return {
                    "status": "success",
                    "mode": "LIVE_API",
                    "total_equity": float(output2.get("tot_evlu_amt", 0)),
                    "cash_available": float(output2.get("dnca_tot_amt", 0)),
                    "stock_evaluation": float(output2.get("scts_evlu_amt", 0)),
                    "raw_output": data.get("output1", [])
                }
            else:
                return {"status": "error", "message": res.text}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    def place_order(
        self,
        ticker: str,
        side: str,  # "BUY" or "SELL"
        qty: int,
        price: float = 0.0,
        order_type: str = "01"  # "01": Market, "00": Limit
    ) -> Dict[str, Any]:
        """
        Submits domestic (KRX) or overseas (US) cash order.
        Executes in simulation mode if KIS credentials are not provided.
        """
        ticker_clean = ticker.strip().upper()
        is_domestic = ticker_clean.endswith(".KS") or (len(ticker_clean) == 6 and ticker_clean.isdigit())
        
        # Pre-Trade Safety Check: Order Qty & Price
        if qty <= 0:
            return {"status": "rejected", "reason": "Quantity must be greater than 0"}

        if not self.is_configured():
            # Return Simulated Execution Receipt
            order_id = f"SIM-{int(time.time() * 1000)}"
            return {
                "status": "filled",
                "mode": "SIMULATION",
                "order_id": order_id,
                "ticker": ticker_clean,
                "side": side.upper(),
                "qty": qty,
                "price": price,
                "timestamp": datetime.now().isoformat()
            }

        self.authenticate()
        if is_domestic:
            return self._place_domestic_order(ticker_clean, side, qty, price, order_type)
        else:
            return self._place_overseas_order(ticker_clean, side, qty, price)

    def _place_domestic_order(self, ticker: str, side: str, qty: int, price: float, order_type: str) -> Dict[str, Any]:
        raw_code = ticker.replace(".KS", "")
        is_buy = side.upper() == "BUY"
        tr_id = ("VTTC0802U" if is_buy else "VTTC0801U") if self.is_paper else ("TTTC0802U" if is_buy else "TTTC0801U")
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"

        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id
        }
        body = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.account_code,
            "PDNO": raw_code,
            "ORD_DVSN": order_type,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(int(price)) if order_type == "00" else "0"
        }

        try:
            res = requests.post(url, json=body, headers=headers, timeout=10)
            data = res.json()
            if res.status_code == 200 and data.get("rt_cd") == "0":
                return {
                    "status": "submitted",
                    "order_id": data.get("output", {}).get("ODNO"),
                    "ticker": ticker,
                    "side": side.upper(),
                    "qty": qty,
                    "message": data.get("msg1")
                }
            else:
                return {"status": "rejected", "reason": data.get("msg1", res.text)}
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}

    def _place_overseas_order(self, ticker: str, side: str, qty: int, price: float) -> Dict[str, Any]:
        is_buy = side.upper() == "BUY"
        tr_id = ("VTTT1002U" if is_buy else "VTTT1006U") if self.is_paper else ("JTTT1002U" if is_buy else "JTTT1006U")
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"

        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id
        }
        body = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.account_code,
            "OVRS_EXCG_CD": "NASD",
            "PDNO": ticker,
            "ORD_QTY": str(qty),
            "OVRS_ORD_UNPR": f"{price:.2f}",
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00"
        }

        try:
            res = requests.post(url, json=body, headers=headers, timeout=10)
            data = res.json()
            if res.status_code == 200 and data.get("rt_cd") == "0":
                return {
                    "status": "submitted",
                    "order_id": data.get("output", {}).get("ODNO"),
                    "ticker": ticker,
                    "side": side.upper(),
                    "qty": qty,
                    "message": data.get("msg1")
                }
            else:
                return {"status": "rejected", "reason": data.get("msg1", res.text)}
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}

# Singleton broker instance
default_kis_broker = KISBrokerAdapter()
