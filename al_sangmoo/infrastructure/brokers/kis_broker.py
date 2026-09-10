"""
AL-SANGMOO QUANT TERMINAL: KOREA INVESTMENT & SECURITIES (KIS) REST API GATEWAY
Official OpenAPI integration compliant with koreainvestment/open-trading-api standards.
Supports US Equity (NASD/NYSE/AMEX) & Domestic (KRX) trading in both Virtual (VPS) and Real (PROD) modes.
"""

import os
import json
import time
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
import requests

from al_sangmoo.core.config import BASE_DIR, load_env
from al_sangmoo.infrastructure.atomic_io import atomic_save_json
from al_sangmoo.infrastructure.idempotent_order import make_client_order_id, match_day_order

# Ensure latest .env values are loaded
load_env()

logger = logging.getLogger(__name__)

# Token cache directory
TOKEN_CACHE_DIR = BASE_DIR / "data"


class TokenBucketLimiter:
    """
    Thread-safe token bucket rate limiter for Korea Investment OpenAPI (max 3.5 requests/sec).
    Prevents EGW00201 (Transaction Rate Limit Exceeded).
    """
    def __init__(self, rate: float = 3.5, capacity: float = 3.5):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.time()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> None:
        while True:
            with self._lock:
                now = time.time()
                elapsed = now - self.last_update
                self.last_update = now
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return
                wait_time = (tokens - self.tokens) / self.rate
            time.sleep(max(0.01, wait_time))


class KISBrokerAdapter:
    """
    Production-grade Korea Investment & Securities (한국투자증권) REST API Broker Adapter.
    Features:
      - 24-hour OAuth 2.0 token persistent caching (prevents EGW00133 duplicate issue limit).
      - Official KIS TR IDs for US equities (Buy: TTTT1002U/VTTT1002U, Sell: TTTT1006U/VTTT1001U).
      - Overseas balance inquiry (TTTS3012R/VTTS3012R) & purchasing power (TTTS3007R/VTTS3007R).
      - Pre-trade risk validation.
    """

    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        account_no: Optional[str] = None,
        account_code: Optional[str] = None,
        mode: Optional[str] = None
    ):
        # Explicit arguments (even if empty string) take precedence over env
        self.app_key = app_key.strip() if app_key is not None else os.environ.get("KIS_APP_KEY", "").strip()
        self.app_secret = app_secret.strip() if app_secret is not None else os.environ.get("KIS_APP_SECRET", "").strip()
        
        # Account Number (8 digits) & Product Code (2 digits)
        self.account_no = account_no.strip() if account_no is not None else (os.environ.get("KIS_CANO") or os.environ.get("KIS_ACCOUNT_NO", "")).strip()
        self.account_code = account_code.strip() if account_code is not None else (os.environ.get("KIS_ACNT_PRDT_CD") or os.environ.get("KIS_ACCOUNT_CODE", "01")).strip()
        
        # Mode: 'vps' (Virtual Sandbox) or 'prod' (Real Production)
        env_mode = (mode or os.environ.get("KIS_MODE") or os.environ.get("KIS_ENV", "vps")).strip().lower()
        self.is_paper = env_mode in ("vps", "paper", "virtual", "demo")
        self.mode_str = "vps" if self.is_paper else "prod"

        # Domain Configuration
        if self.is_paper:
            self.base_url = "https://openapivts.koreainvestment.com:29443"
        else:
            self.base_url = "https://openapi.koreainvestment.com:9443"

        self.token: Optional[str] = None
        self.token_expiry: float = 0.0
        self.cache_file = TOKEN_CACHE_DIR / f".kis_token_{self.mode_str}.json"
        self._auth_lock = threading.RLock()
        self._balance_lock = threading.Lock()
        self._last_overseas_balance: Optional[Dict[str, Any]] = None
        self._last_balance_time: float = 0.0
        self._limiter = TokenBucketLimiter(rate=3.5, capacity=3.5)
        self._order_lock = threading.Lock()
        self._inflight_keys = set()
        self._unconfirmed: Dict[str, Dict[str, Any]] = {}
        self._price_cache: Dict[str, Tuple[float, float]] = {}
        self._price_cache_lock = threading.Lock()

        # Load cached token on initialization
        self._load_cached_token()

    def is_configured(self) -> bool:
        """Checks whether KIS API credentials have been provided."""
        return bool(self.app_key and self.app_secret and self.account_no)

    def get_status(self) -> Dict[str, Any]:
        """Returns the current connection and mode status of the KIS broker adapter."""
        masked_acc = (
            f"{self.account_no[:4]}****{self.account_no[-2:]}"
            if len(self.account_no) >= 8
            else "NOT_CONFIGURED"
        )
        is_token_valid = (self.token is not None and time.time() < self.token_expiry - 60)
        is_mock_paper = self.is_paper or not self.is_configured()
        return {
            "broker": "Korea Investment & Securities (한국투자증권)",
            "environment": "MOCK_PAPER" if is_mock_paper else "REAL_PRODUCTION",
            "mode": "VIRTUAL_PAPER (모의투자)" if is_mock_paper else "REAL_PRODUCTION (실전투자)",
            "env_code": "vps" if is_mock_paper else self.mode_str,
            "base_url": self.base_url,
            "is_configured": self.is_configured(),
            "account_no_masked": f"{masked_acc}-{self.account_code}",
            "token_valid": is_token_valid,
            "token_expires_at": datetime.fromtimestamp(self.token_expiry).strftime("%Y-%m-%d %H:%M:%S") if self.token_expiry > 0 else "N/A"
        }


    # =========================================================================
    # TOKEN LIFECYCLE & AUTHENTICATION
    # =========================================================================

    def _load_cached_token(self) -> None:
        """Loads valid token from local file cache if not expired."""
        if not self.cache_file.exists():
            return
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                cached_token = data.get("access_token")
                expiry_ts = float(data.get("expires_at_timestamp", 0.0))
                # If token has at least 5 minutes remaining
                if cached_token and expiry_ts > (time.time() + 300):
                    self.token = cached_token
                    self.token_expiry = expiry_ts
                    logger.info(f"Loaded valid KIS OAuth token from cache. Expires at {data.get('expires_at')}")
        except Exception as exc:
            logger.warning(f"Failed to read KIS token cache: {exc}")

    def _save_cached_token(self, token: str, expires_in_seconds: int, expired_str: Optional[str] = None) -> None:
        """Saves access token to local file cache."""
        try:
            TOKEN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self.token = token
            self.token_expiry = time.time() + expires_in_seconds
            
            if not expired_str:
                expired_str = (datetime.now() + timedelta(seconds=expires_in_seconds)).strftime("%Y-%m-%d %H:%M:%S")
                
            payload = {
                "access_token": token,
                "expires_at": expired_str,
                "expires_at_timestamp": self.token_expiry,
                "mode": self.mode_str,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            atomic_save_json(str(self.cache_file), payload)
            logger.info(f"Saved KIS OAuth token to cache. Valid until {expired_str}")
        except Exception as exc:
            logger.error(f"Failed to save KIS token cache: {exc}")

    def authenticate(self, force_refresh: bool = False) -> bool:
        """
        Obtains or validates OAuth 2.0 access token with mutex protection.
        If credentials are not configured, runs in simulated offline mode.
        """
        with self._auth_lock:
            if not self.is_configured():
                self.token = "MOCK_KIS_SESSION_TOKEN"
                self.token_expiry = time.time() + 86400
                return True

            # If existing token is still valid for > 5 minutes and not force refresh
            if not force_refresh and self.token and (time.time() < self.token_expiry - 300):
                return True

            self._limiter.acquire(1.0)
            url = f"{self.base_url}/oauth2/tokenP"
            payload = {
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret
            }
            headers = {"Content-Type": "application/json; charset=UTF-8"}

            try:
                res = requests.post(url, json=payload, headers=headers, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    token = data.get("access_token")
                    expires_in = int(data.get("expires_in", 86400))
                    expired_str = data.get("access_token_token_expired")
                    self._save_cached_token(token, expires_in, expired_str)
                    return True
                else:
                    logger.error(f"[KIS Auth Error] {res.status_code}: {res.text}")
                    return False
            except Exception as exc:
                logger.error(f"[KIS Auth Exception] {exc}")
                return False

    def _get_headers(self, tr_id: str) -> Dict[str, str]:
        """Generates standard KIS HTTP headers."""
        return {
            "Content-Type": "application/json; charset=UTF-8",
            "authorization": f"Bearer {self.token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
            "tr_cont": ""
        }

    # =========================================================================
    # OVERSEAS (US) EQUITY ACCOUNT & BALANCE INQUIRY
    # =========================================================================

    def get_account_balance(self) -> Dict[str, Any]:
        """
        Unified account balance inquiry (domestic / overseas).
        Returns compatible fields: total_equity, cash_available, stock_evaluation.
        """
        res = self.get_overseas_balance()
        if res.get("status") == "success":
            tot = res.get("total_equity_usd", 100_000.0)
            cash = res.get("cash_available_usd", 75_000.0)
            stock = res.get("stock_eval_usd", 25_000.0)
            return {
                "status": "success",
                "mode": "SIMULATED" if not self.is_configured() else res.get("mode", "LIVE_API"),
                "total_equity": tot,
                "total_equity_usd": tot,
                "cash_available": cash,
                "cash_available_usd": cash,
                "stock_evaluation": stock,
                "stock_eval_usd": stock,
                "holdings": res.get("holdings", []),
                "raw_output": res.get("raw_summary", {})
            }
        # Resilient fallback if broker returns temporary gateway error
        return {
            "status": "success",
            "mode": "SIMULATED",
            "total_equity": 100_000.0,
            "total_equity_usd": 100_000.0,
            "cash_available": 100_000.0,
            "cash_available_usd": 100_000.0,
            "stock_evaluation": 0.0,
            "stock_eval_usd": 0.0,
            "holdings": [],
            "raw_output": {},
            "broker_error": res.get("message")
        }


    def get_overseas_balance(self, exchange: str = "ALL", currency: str = "USD") -> Dict[str, Any]:
        """
        Queries US equity holding positions across ALL major US exchanges (NASD, NYSE, AMEX).
        Official TR: TTTS3012R (Real) / VTTS3012R (Virtual Paper).
        """
        if not self.is_configured():
            return {
                "status": "success",
                "mode": "SIMULATED",
                "total_equity_usd": 100_000.0,
                "cash_available_usd": 75_000.0,
                "stock_eval_usd": 25_000.0,
                "holdings": []
            }

        # 0. Check Short-term Balance Cache (3.0s window to avoid EGW00201 rate limiting)
        with self._balance_lock:
            now_ts = time.time()
            if self._last_overseas_balance and (now_ts - self._last_balance_time < 3.0):
                return dict(self._last_overseas_balance)

        if not self.authenticate():
            return {"status": "error", "message": "Authentication failed"}

        tr_id = "VTTS3012R" if self.is_paper else "TTTS3012R"
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-balance"
        headers = self._get_headers(tr_id)
        
        target_exchanges = ["NASD", "NYSE", "AMEX"] if exchange == "ALL" else [exchange]
        all_holdings = []
        seen_tickers = set()
        latest_summary = {}
        total_eval = 0.0
        cash_avail = 0.0
        total_equity = 0.0

        for ex in target_exchanges:
            params = {
                "CANO": self.account_no,
                "ACNT_PRDT_CD": self.account_code,
                "OVRS_EXCG_CD": ex,
                "TR_CRCY_CD": currency,
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": ""
            }

            for attempt in range(2):
                try:
                    self._limiter.acquire(1.0)
                    res = requests.get(url, headers=headers, params=params, timeout=3.0)
                    if res.status_code == 200:
                        data = res.json()
                        if data.get("msg_cd") == "EGW00201" and attempt == 0:
                            time.sleep(0.35)
                            continue

                        if data.get("rt_cd") == "0":
                            raw_holdings = data.get("output1", [])
                            if isinstance(raw_holdings, dict):
                                raw_holdings = [raw_holdings]
                            elif not isinstance(raw_holdings, list):
                                raw_holdings = []

                            output2 = data.get("output2", {})
                            if isinstance(output2, list) and len(output2) > 0:
                                output2 = output2[0]
                            if output2:
                                latest_summary = output2
                                ex_tot_eval = float(output2.get("evlu_amt_smtl_amt") or output2.get("tot_evlu_pfls_amt") or 0.0)
                                ex_cash_avail = float(output2.get("frcr_dncl_amt_2") or output2.get("ovrs_ord_psbl_amt") or 0.0)
                                ex_total_equity = float(output2.get("tot_evlu_amt") or (ex_tot_eval + ex_cash_avail))
                                if ex_total_equity > total_equity:
                                    total_equity = ex_total_equity
                                    cash_avail = ex_cash_avail
                                    total_eval = ex_tot_eval

                            for item in raw_holdings:
                                ticker = str(item.get("ovrs_pdno") or item.get("ovrs_pd_no") or item.get("pdno") or "").strip().upper()
                                if not ticker or ticker in seen_tickers:
                                    continue
                                qty = float(item.get("ord_psbl_qty") or item.get("ovrs_cblc_qty") or item.get("cblc_qty13") or 0.0)
                                avg_price = float(item.get("pchs_avg_pric") or 0.0)
                                cur_price = float(item.get("now_pric2") or item.get("ovrs_now_pric1") or avg_price)
                                eval_amt = float(item.get("ovrs_stck_evlu_amt") or item.get("evlu_amt2") or (qty * cur_price))
                                pnl_pct = float(item.get("evlu_pfls_rt") or 0.0)
                                pnl_amt = float(item.get("frcr_evlu_pfls_amt") or 0.0)
                                
                                clean_item = {
                                    "ticker": ticker,
                                    "name": item.get("ovrs_item_name", ticker),
                                    "exchange": item.get("ovrs_excg_cd", ex),
                                    "quantity": qty,
                                    "avg_price": avg_price,
                                    "current_price": cur_price,
                                    "eval_amount": eval_amt,
                                    "pnl_pct": pnl_pct,
                                    "pnl_amount": pnl_amt
                                }
                                all_holdings.append(clean_item)
                                seen_tickers.add(ticker)
                            break
                        else:
                            if attempt == 0 and "EGW00201" in str(data):
                                time.sleep(0.35)
                                continue
                            break
                    else:
                        if attempt == 0:
                            time.sleep(0.35)
                            continue
                        break
                except Exception as exc:
                    if attempt == 0:
                        time.sleep(0.35)
                        continue
                    break

        # Fallback to last known summary if throttled
        if not latest_summary and self._last_overseas_balance and "raw_summary" in self._last_overseas_balance:
            latest_summary = self._last_overseas_balance.get("raw_summary", {})

        # Parse PnL and return metrics directly from KIS Summary (SSOT)
        rlzt_rt = float(latest_summary.get("rlzt_erng_rt") or 0.0)
        raw_rlzt_amt = float(latest_summary.get("ovrs_rlzt_pfls_amt") or 0.0)
        rlzt_amt = round(raw_rlzt_amt, 2)
        
        raw_evlu_amt = float(latest_summary.get("tot_evlu_pfls_amt") or 0.0)
        evlu_amt = round(raw_evlu_amt, 2)
        evlu_rt = float(latest_summary.get("tot_pftrt") or 0.0)

        tot_pnl_usd = round(rlzt_amt + evlu_amt, 2)
        tot_pnl_pct = round(rlzt_rt + evlu_rt, 2)

        res_dict = {
            "status": "success",
            "mode": "VIRTUAL_PAPER" if self.is_paper else "REAL_PRODUCTION",
            "account_no": f"{self.account_no}-{self.account_code}",
            "total_equity_usd": total_equity if total_equity > 0 else 100_000.0,
            "cash_available_usd": cash_avail,
            "stock_eval_usd": total_eval,
            "realized_pnl_usd": rlzt_amt,
            "realized_pnl_pct": rlzt_rt,
            "unrealized_pnl_usd": evlu_amt,
            "unrealized_pnl_pct": evlu_rt,
            "total_pnl_usd": tot_pnl_usd,
            "total_pnl_pct": tot_pnl_pct,
            "holdings_count": len(all_holdings),
            "holdings": all_holdings,
            "raw_summary": latest_summary
        }

        with self._balance_lock:
            if len(all_holdings) > 0 or latest_summary:
                self._last_overseas_balance = dict(res_dict)
                self._last_balance_time = time.time()

        return res_dict

    @staticmethod
    def _parse_positive_price(raw: Any) -> Optional[float]:
        if raw is None or raw == "":
            return None
        try:
            val = float(str(raw).replace(",", "").strip())
        except (ValueError, TypeError):
            return None
        if val > 0:
            return round(val, 2)
        return None

    @classmethod
    def _price_from_kis_output(cls, output: Any) -> Optional[float]:
        """
        Overseas last price. KIS sometimes sends `last` as a dotted dollar
        string and sometimes as an integer scaled by `zdiv` decimal places.
        Never fall back to domestic `stck_prpr` / `base` — those fields can
        be leftover KRW prints and explode the last daily candle.
        """
        if not isinstance(output, dict):
            return None
        try:
            zdiv = int(str(output.get("zdiv") or "0").strip() or "0")
        except (ValueError, TypeError):
            zdiv = 0
        for key in ("last", "last_price", "ovrs_nmix_prpr"):
            raw = output.get(key)
            if raw is None or raw == "":
                continue
            text = str(raw).replace(",", "").strip()
            try:
                val = float(text)
            except (ValueError, TypeError):
                continue
            if val <= 0:
                continue
            if "." not in text and zdiv > 0:
                val = val / (10 ** zdiv)
            if val > 0:
                return round(val, 2)
        return None

    def _quote_exchanges(self, ticker: str) -> List[str]:
        nyse_tickers = {
            "XOM", "CVX", "TSM", "ORCL", "CRM", "NOW", "JNJ", "UNH", "LLY", "DIS",
            "JPM", "V", "MA", "LMT", "RTX", "NOC", "GE", "GEV", "ETN", "CCJ",
            "OKLO", "VST", "CEG", "SNOW", "NET", "APP", "DELL",
        }
        primary = "NYS" if ticker in nyse_tickers else "NAS"
        secondary = "NAS" if primary == "NYS" else "NYS"
        ordered = [primary, secondary]
        if "AMS" not in ordered:
            ordered.append("AMS")
        return ordered

    def _kis_get_json(self, url: str, tr_id: str, params: Dict[str, str], timeout: float) -> Optional[dict]:
        def _parse(resp):
            try:
                body = resp.json()
            except Exception:
                return None
            return body if isinstance(body, dict) else None

        headers = self._get_headers(tr_id)
        self._limiter.acquire(0.25)
        res = requests.get(url, headers=headers, params=params, timeout=timeout)
        data = _parse(res)
        token_bad = res.status_code == 401 or (data and data.get("msg_cd") in ("EGW00121", "EGW00122", "EGW00123"))
        if token_bad and self.authenticate(force_refresh=True):
            headers = self._get_headers(tr_id)
            self._limiter.acquire(0.25)
            res = requests.get(url, headers=headers, params=params, timeout=timeout)
            data = _parse(res)
        if res.status_code != 200 or not data or data.get("rt_cd") != "0":
            return None
        return data

    def get_live_price(self, ticker: str, fallback_yahoo: bool = True) -> Optional[float]:
        """
        Official overseas last price.
        Primary: HHDFS00000300 /quotations/price (TR must match URL).
        Secondary: HHDFS76200200 /quotations/price-detail.
        Yahoo fallback is skipped on the chart request path so the UI does not stall.
        """
        sym_clean = ticker.upper().strip()

        with self._price_cache_lock:
            cached = self._price_cache.get(sym_clean)
            if cached and (time.time() - cached[1] < 2.0) and cached[0] > 0:
                return cached[0]

        if self.is_configured() and self.authenticate():
            quote_url = f"{self.base_url}/uapi/overseas-price/v1/quotations/price"
            detail_url = f"{self.base_url}/uapi/overseas-price/v1/quotations/price-detail"
            endpoints = (
                (quote_url, "HHDFS00000300"),
                (detail_url, "HHDFS76200200"),
            )
            for excd in self._quote_exchanges(sym_clean):
                params = {"AUTH": "", "EXCD": excd, "SYMB": sym_clean}
                for url, tr_id in endpoints:
                    try:
                        data = self._kis_get_json(url, tr_id, params, timeout=3.0)
                        if not data:
                            continue
                        px = self._price_from_kis_output(data.get("output") or {})
                        if px:
                            with self._price_cache_lock:
                                self._price_cache[sym_clean] = (px, time.time())
                            return px
                    except Exception as exc:
                        logger.debug("[KIS Live Price] %s %s %s failed: %s", tr_id, excd, sym_clean, exc)

        if not fallback_yahoo:
            return None

        try:
            import yfinance as yf
            import logging as _logging
            _logging.getLogger("yfinance").setLevel(_logging.CRITICAL)
            t_obj = yf.Ticker(sym_clean)
            try:
                fi = getattr(t_obj, "fast_info", None)
                if fi:
                    fast_p = getattr(fi, "last_price", None) or getattr(fi, "regular_market_price", None) or getattr(fi, "previous_close", None)
                    parsed = self._parse_positive_price(fast_p)
                    if parsed:
                        return parsed
            except Exception:
                pass
            try:
                hist = t_obj.history(period="5d", raise_errors=False)
                if not hist.empty and "Close" in hist.columns:
                    parsed = self._parse_positive_price(hist["Close"].dropna().iloc[-1])
                    if parsed:
                        return parsed
            except Exception:
                pass
        except Exception:
            pass

        return None

    def get_daily_ohlcv(self, ticker: str, min_bars: int = 250):
        """
        Overseas daily bars via HHDFS76240000 /quotations/dailyprice.
        Used when Yahoo Finance returns too few bars for an on-demand chart.
        """
        import pandas as pd

        sym_clean = ticker.upper().strip()
        if not self.is_configured() or not self.authenticate():
            return None

        url = f"{self.base_url}/uapi/overseas-price/v1/quotations/dailyprice"
        rows: List[dict] = []
        for excd in self._quote_exchanges(sym_clean):
            rows = []
            bymd = ""
            for _ in range(6):
                params = {
                    "AUTH": "",
                    "EXCD": excd,
                    "SYMB": sym_clean,
                    "GUBN": "0",
                    "BYMD": bymd,
                    "MODP": "1",
                }
                try:
                    data = self._kis_get_json(url, "HHDFS76240000", params, timeout=8.0)
                except Exception as exc:
                    logger.debug("[KIS Daily] %s %s failed: %s", excd, sym_clean, exc)
                    break
                if not data:
                    break
                chunk = data.get("output2") or data.get("output") or []
                if isinstance(chunk, dict):
                    chunk = [chunk]
                if not chunk:
                    break
                added = 0
                oldest = None
                for item in chunk:
                    if not isinstance(item, dict):
                        continue
                    xymd = str(item.get("xymd") or item.get("date") or "").replace("-", "")
                    close = self._parse_positive_price(item.get("clos") or item.get("close") or item.get("ovrs_nmix_prpr"))
                    if not xymd or not close:
                        continue
                    open_px = self._parse_positive_price(item.get("open") or item.get("ovrs_nmix_oprc")) or close
                    high_px = self._parse_positive_price(item.get("high") or item.get("ovrs_nmix_hgpr")) or close
                    low_px = self._parse_positive_price(item.get("low") or item.get("ovrs_nmix_lwpr")) or close
                    vol = self._parse_positive_price(item.get("tvol") or item.get("acml_vol") or item.get("volume")) or 0.0
                    rows.append({
                        "Date": xymd,
                        "Open": open_px,
                        "High": high_px,
                        "Low": low_px,
                        "Close": close,
                        "Volume": vol,
                    })
                    added += 1
                    if oldest is None or xymd < oldest:
                        oldest = xymd
                if added == 0 or not oldest or len(rows) >= min_bars:
                    break
                try:
                    prev = datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)
                    bymd = prev.strftime("%Y%m%d")
                except Exception:
                    break
            if len(rows) >= 60:
                break

        if len(rows) < 60:
            return None
        df = pd.DataFrame(rows)
        df["Date"] = pd.to_datetime(df["Date"], format="%Y%m%d", errors="coerce")
        df = df.dropna(subset=["Date", "Close"]).drop_duplicates(subset=["Date"]).sort_values("Date")
        df = df.set_index("Date")
        if len(df) < 60:
            return None
        return df[["Open", "High", "Low", "Close", "Volume"]]

    def get_purchasable_amount(self, ticker: str, price: float, exchange: str = "NASD") -> Dict[str, Any]:

        """
        Inquires maximum purchasable quantity and cash for US stock.
        Official TR: TTTS3007R (Real) / VTTS3007R (Virtual Paper).
        """
        if not self.is_configured():
            return {"status": "success", "mode": "SIMULATION", "max_buy_qty": 100, "ord_psbl_cash": 50000.0}

        if not self.authenticate():
            return {"status": "error", "message": "Authentication failed"}

        tr_id = "VTTS3007R" if self.is_paper else "TTTS3007R"
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-psamount"
        headers = self._get_headers(tr_id)
        
        params = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.account_code,
            "OVRS_EXCG_CD": exchange,
            "OVRS_ORD_UNPR": f"{price:.2f}",
            "ITEM_CD": ticker.upper()
        }

        try:
            self._limiter.acquire(1.0)
            res = requests.get(url, headers=headers, params=params, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if data.get("rt_cd") == "0":
                    out = data.get("output", {})
                    return {
                        "status": "success",
                        "max_buy_qty": float(out.get("ord_psbl_qty", 0)),
                        "ord_psbl_cash": float(out.get("ovrs_ord_psbl_amt", 0)),
                        "max_order_amt": float(out.get("max_ord_psbl_amt", 0))
                    }
                else:
                    return {"status": "error", "message": data.get("msg1")}
            return {"status": "error", "message": res.text}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    # =========================================================================
    # OVERSEAS (US) ORDER EXECUTION
    # =========================================================================

    def place_order(
        self,
        ticker: str,
        side: str,  # "BUY" or "SELL"
        qty: int,
        price: float = 0.0,
        order_type: str = "00",  # "00": Limit (지정가), "01": Market (시장가)
        exchange: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Unified order execution entrypoint for US and Domestic equities.
        """
        ticker_clean = ticker.strip().upper()
        is_domestic = (
            ticker_clean.endswith(".KS")
            or ticker_clean.endswith(".KQ")
            or (len(ticker_clean) == 6 and ticker_clean.isdigit())
        )

        # Pre-Trade Safety Check
        if qty <= 0:
            return {"status": "rejected", "reason": "Quantity must be greater than 0"}

        if not self.is_configured():
            order_id = f"SIM-{int(time.time() * 1000)}"
            client_oid = make_client_order_id()
            return {
                "status": "filled",
                "mode": "SIMULATION",
                "order_id": order_id,
                "client_order_id": client_oid,
                "ticker": ticker_clean,
                "side": side.upper(),
                "qty": qty,
                "price": price,
                "timestamp": datetime.now().isoformat()
            }

        if not self.authenticate():
            return {"status": "error", "reason": "Broker authentication failed"}

        inflight_key = f"{side.upper()}:{ticker_clean}"
        with self._order_lock:
            if inflight_key in self._inflight_keys:
                return {
                    "status": "rejected",
                    "reason": "INFLIGHT_DUPLICATE",
                    "ticker": ticker_clean,
                    "side": side.upper(),
                    "message": f"{ticker_clean} {side.upper()} 주문이 이미 전송 중입니다."
                }
            self._inflight_keys.add(inflight_key)

        try:
            pending = self._unconfirmed.get(inflight_key)
            if pending and (time.time() - float(pending.get("ts") or 0)) < 90:
                rec = self._recheck_submitted_order(
                    ticker=ticker_clean,
                    side=side,
                    qty=qty,
                    price=price,
                    client_order_id=pending.get("client_order_id"),
                    is_domestic=is_domestic,
                    exchange=exchange or "NASD",
                )
                if rec:
                    self._unconfirmed.pop(inflight_key, None)
                    return rec
                return {
                    "status": "error",
                    "reason": "ORDER_TIMEOUT_UNCONFIRMED",
                    "client_order_id": pending.get("client_order_id"),
                    "ticker": ticker_clean,
                    "side": side.upper(),
                    "message": "이전 주문 HTTP 타임아웃이 원장에서 아직 확인되지 않았습니다. 중복 POST를 차단했습니다."
                }

            if is_domestic:
                result = self._place_domestic_order(ticker_clean, side, qty, price, order_type)
            else:
                result = self._place_overseas_order(ticker_clean, side, qty, price, exchange or "NASD", order_type)

            if result.get("status") in ("submitted", "filled", "SUCCESS_VIA_RECHECK"):
                self._unconfirmed.pop(inflight_key, None)
            elif "TIMEOUT" in str(result.get("reason") or "").upper():
                self._unconfirmed[inflight_key] = {
                    "ts": time.time(),
                    "client_order_id": result.get("client_order_id"),
                    "qty": qty,
                    "price": price,
                    "side": side.upper(),
                }
            return result
        finally:
            with self._order_lock:
                self._inflight_keys.discard(inflight_key)

    def query_overseas_day_orders(self, exchange: str = "NASD") -> List[Dict[str, Any]]:
        """Today's overseas unfilled (nccs) + filled (ccnl) rows. Failures return []."""
        rows: List[Dict[str, Any]] = []
        nccs_tr = "VTTS3035R" if self.is_paper else "TTTS3035R"
        ccnl_tr = "VTTS3039R" if self.is_paper else "TTTS3039R"
        specs = [
            (nccs_tr, "/uapi/overseas-stock/v1/trading/inquire-nccs"),
            (ccnl_tr, "/uapi/overseas-stock/v1/trading/inquire-ccnl"),
        ]
        today = datetime.now().strftime("%Y%m%d")
        for tr_id, path in specs:
            try:
                url = f"{self.base_url}{path}"
                headers = self._get_headers(tr_id)
                params = {
                    "CANO": self.account_no,
                    "ACNT_PRDT_CD": self.account_code,
                    "OVRS_EXCG_CD": exchange,
                    "TR_CRCY_CD": "USD",
                    "CTX_AREA_FK200": "",
                    "CTX_AREA_NK200": "",
                    "ORD_STRT_DT": today,
                    "ORD_END_DT": today,
                }
                self._limiter.acquire(1.0)
                res = requests.get(url, headers=headers, params=params, timeout=8)
                if res.status_code != 200:
                    continue
                data = res.json()
                if data.get("rt_cd") not in ("0", 0, None) and data.get("rt_cd") not in ("0",):
                    # still collect output if present
                    pass
                payload = data.get("output") or data.get("output1") or data.get("output2") or []
                if isinstance(payload, dict):
                    payload = [payload]
                if isinstance(payload, list):
                    rows.extend([p for p in payload if isinstance(p, dict)])
            except Exception as exc:
                logger.debug(f"[KIS] day-order inquiry skipped ({tr_id}): {exc}")
        return rows

    def _recheck_submitted_order(
        self,
        ticker: str,
        side: str,
        qty: int,
        price: float,
        client_order_id: Optional[str],
        is_domestic: bool = False,
        exchange: str = "NASD",
    ) -> Optional[Dict[str, Any]]:
        """After HTTP timeout: look up the day book. Never implies a new POST."""
        if is_domestic:
            return None
        try:
            book = self.query_overseas_day_orders(exchange=exchange)
        except Exception:
            return None
        matched = match_day_order(book, ticker, side, qty, price, client_order_id=client_order_id)
        if not matched:
            return None
        odno = matched.get("order_id") or client_order_id or f"RECHECK-{int(time.time())}"
        return {
            "status": "SUCCESS_VIA_RECHECK",
            "order_id": odno,
            "client_order_id": client_order_id or matched.get("client_order_id"),
            "ticker": ticker,
            "side": side.upper(),
            "qty": qty,
            "price": price,
            "message": "HTTP 타임아웃 후 당일 원장에서 주문을 확인했습니다. 재전송하지 않았습니다."
        }

    def _place_overseas_order(
        self,
        ticker: str,
        side: str,
        qty: int,
        price: float,
        exchange: str = "NASD",
        order_type: str = "00"
    ) -> Dict[str, Any]:
        """
        Submits US equity cash order via official KIS endpoint.
        Timeout / 5xx: inquire day book, never blindly re-POST (duplicate-fill guard).
        Rate-limit EGW00201: POST retry is safe because the broker rejected the request.
        """
        is_buy = side.upper() == "BUY"
        if is_buy:
            tr_id = "VTTT1002U" if self.is_paper else "TTTT1002U"
            sll_type = ""
        else:
            tr_id = "VTTT1001U" if self.is_paper else "TTTT1006U"
            sll_type = "00"

        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"
        headers = self._get_headers(tr_id)
        unpr_str = f"{price:.2f}" if price > 0 else "0"
        client_oid = make_client_order_id()

        body = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.account_code,
            "OVRS_EXCG_CD": exchange,
            "PDNO": ticker,
            "ORD_QTY": str(int(qty)),
            "OVRS_ORD_UNPR": unpr_str,
            "CTAC_TLNO": "",
            "MGCO_APTM_ODNO": client_oid,
            "SLL_TYPE": sll_type,
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": order_type or "00"
        }

        last_rate_limit = False
        for attempt in range(3):
            try:
                self._limiter.acquire(1.0)
                res = requests.post(url, json=body, headers=headers, timeout=8)
            except (requests.Timeout, requests.ConnectionError) as exc:
                rec = self._recheck_submitted_order(
                    ticker, side, qty, price, client_oid, is_domestic=False, exchange=exchange
                )
                if rec:
                    return rec
                logger.error(f"[KIS US Order Timeout] {exc}")
                return {
                    "status": "error",
                    "reason": "ORDER_TIMEOUT_UNCONFIRMED",
                    "client_order_id": client_oid,
                    "ticker": ticker,
                    "side": side.upper(),
                    "message": "주문 HTTP 타임아웃. 원장에서 확인되지 않아 재전송하지 않았습니다."
                }
            except Exception as exc:
                rec = self._recheck_submitted_order(
                    ticker, side, qty, price, client_oid, is_domestic=False, exchange=exchange
                )
                if rec:
                    return rec
                logger.error(f"[KIS US Order Exception] {exc}")
                return {"status": "error", "reason": str(exc), "client_order_id": client_oid}

            if res.status_code >= 500:
                rec = self._recheck_submitted_order(
                    ticker, side, qty, price, client_oid, is_domestic=False, exchange=exchange
                )
                if rec:
                    return rec
                return {
                    "status": "error",
                    "reason": "ORDER_TIMEOUT_UNCONFIRMED",
                    "client_order_id": client_oid,
                    "message": f"증권사 HTTP {res.status_code}. 재전송 없이 원장 확인 필요."
                }

            try:
                data = res.json()
            except Exception:
                rec = self._recheck_submitted_order(
                    ticker, side, qty, price, client_oid, is_domestic=False, exchange=exchange
                )
                if rec:
                    return rec
                return {
                    "status": "error",
                    "reason": "ORDER_TIMEOUT_UNCONFIRMED",
                    "client_order_id": client_oid,
                    "message": "주문 응답 JSON 파싱 실패. 재전송하지 않았습니다."
                }

            msg_cd = data.get("msg_cd", "")
            msg_txt = data.get("msg1", "")
            if (msg_cd == "EGW00201" or "초당 거래건수" in str(msg_txt)) and attempt < 2:
                last_rate_limit = True
                time.sleep(1.2)
                continue
            last_rate_limit = False

            if res.status_code == 200 and data.get("rt_cd") == "0":
                out = data.get("output", {})
                odno = out.get("ODNO") or out.get("odno") or f"OD-{int(time.time())}"
                msg_ok = data.get("msg1", "Order submitted successfully")
                try:
                    from al_sangmoo.infrastructure.persistence import record_execution_log
                    record_execution_log(
                        ticker=ticker,
                        side=side.upper(),
                        quantity=qty,
                        price=price,
                        order_type="MARKETABLE_LIMIT" if order_type == "00" else "MARKET",
                        status="FILLED",
                        message=f"{msg_ok} (체결단가 ${price:,.2f})",
                        order_id=odno
                    )
                except Exception:
                    pass
                return {
                    "status": "submitted",
                    "order_id": odno,
                    "client_order_id": client_oid,
                    "ticker": ticker,
                    "side": side.upper(),
                    "qty": qty,
                    "price": price,
                    "message": msg_ok
                }

            logger.error(f"[KIS US Order Error] {msg_cd}: {msg_txt}")
            return {
                "status": "rejected",
                "reason": f"{msg_txt} ({msg_cd})",
                "client_order_id": client_oid,
                "data": data
            }

        if last_rate_limit:
            return {"status": "rejected", "reason": "EGW00201 초당 거래건수 초과 (Max retries)", "client_order_id": client_oid}
        return {"status": "error", "reason": "Max retries exceeded", "client_order_id": client_oid}

    def _place_domestic_order(
        self,
        ticker: str,
        side: str,
        qty: int,
        price: float,
        order_type: str = "01"
    ) -> Dict[str, Any]:
        """Domestic (KRX) equity order. Timeout does not re-POST."""
        raw_code = ticker.replace(".KS", "").replace(".KQ", "").strip()
        is_buy = side.upper() == "BUY"
        tr_id = ("VTTC0802U" if is_buy else "VTTC0801U") if self.is_paper else ("TTTC0802U" if is_buy else "TTTC0801U")
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        headers = self._get_headers(tr_id)
        client_oid = make_client_order_id()

        body = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.account_code,
            "PDNO": raw_code,
            "ORD_DVSN": order_type,
            "ORD_QTY": str(int(qty)),
            "ORD_UNPR": str(int(price)) if order_type == "00" else "0"
        }

        try:
            self._limiter.acquire(1.0)
            res = requests.post(url, json=body, headers=headers, timeout=8)
        except (requests.Timeout, requests.ConnectionError) as exc:
            logger.error(f"[KIS KR Order Timeout] {exc}")
            return {
                "status": "error",
                "reason": "ORDER_TIMEOUT_UNCONFIRMED",
                "client_order_id": client_oid,
                "message": "국내주식 주문 HTTP 타임아웃. 재전송하지 않았습니다."
            }
        except Exception as exc:
            return {"status": "error", "reason": str(exc), "client_order_id": client_oid}

        try:
            data = res.json()
        except Exception:
            return {
                "status": "error",
                "reason": "ORDER_TIMEOUT_UNCONFIRMED",
                "client_order_id": client_oid,
                "message": "국내주식 주문 응답 파싱 실패. 재전송하지 않았습니다."
            }

        if res.status_code == 200 and data.get("rt_cd") == "0":
            return {
                "status": "submitted",
                "order_id": data.get("output", {}).get("ODNO"),
                "client_order_id": client_oid,
                "ticker": ticker,
                "side": side.upper(),
                "qty": qty,
                "price": price,
                "message": data.get("msg1")
            }
        return {"status": "rejected", "reason": data.get("msg1", res.text), "client_order_id": client_oid}


# Global singleton instance
default_kis_broker = KISBrokerAdapter()
