"""
Idempotent order helpers for KIS OpenAPI.

KIS may accept an order even when the HTTP client times out (504 / socket timeout).
Blind POST retries then create duplicate fills. Match a day-book row instead.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

ACCEPTED_ORDER_STATUSES = frozenset({"submitted", "filled", "SUCCESS_VIA_RECHECK", "success"})
CLIENT_ORDER_ID_LEN = 12


def is_broker_order_ack(status: Optional[str]) -> bool:
    """True if the broker confirmed the order exists (including post-timeout day-book recheck)."""
    return str(status or "") in ACCEPTED_ORDER_STATUSES

BUY_SIDE_CODES = {"02", "2", "BUY", "매수"}
SELL_SIDE_CODES = {"01", "1", "SELL", "매도"}


def make_client_order_id() -> str:
    """12-char member order id for KIS MGCO_APTM_ODNO."""
    return uuid.uuid4().hex[:CLIENT_ORDER_ID_LEN].upper()


def normalize_kis_day_order(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten KIS nccs/ccnl row into a comparable dict."""
    if not isinstance(raw, dict):
        return {}
    ticker = str(raw.get("pdno") or raw.get("PDNO") or raw.get("ticker") or "").strip().upper()
    side_raw = str(
        raw.get("sll_buy_dvsn_cd")
        or raw.get("SLL_BUY_DVSN_CD")
        or raw.get("side")
        or ""
    ).strip().upper()
    if side_raw in BUY_SIDE_CODES:
        side = "BUY"
    elif side_raw in SELL_SIDE_CODES:
        side = "SELL"
    else:
        side = side_raw

    def _num(*keys: str) -> float:
        for k in keys:
            v = raw.get(k)
            if v is None or v == "":
                continue
            try:
                return float(str(v).replace(",", ""))
            except (TypeError, ValueError):
                continue
        return 0.0

    qty = _num("ft_ord_qty", "ord_qty", "nccs_qty", "qty", "FT_ORD_QTY", "ORD_QTY")
    filled = _num("ft_ccld_qty", "ccld_qty", "FT_CCLD_QTY")
    remaining = _num("nccs_qty", "ft_nccs_qty", "NCCS_QTY", "FT_NCCS_QTY")
    if qty <= 0 and filled > 0:
        qty = filled
    price = _num("ft_ord_unpr3", "ft_ccld_unpr3", "ovrs_ord_unpr", "price", "FT_ORD_UNPR3")
    fill_price = _num(
        "ft_ccld_unpr3",
        "ccld_unpr",
        "avg_prvs",
        "FT_CCLD_UNPR3",
        "CCLD_UNPR",
    )
    order_id = str(raw.get("odno") or raw.get("ODNO") or raw.get("order_id") or "").strip()
    client_oid = str(
        raw.get("mgco_aptm_odno") or raw.get("MGCO_APTM_ODNO") or raw.get("client_order_id") or ""
    ).strip().upper()
    status_text = str(
        raw.get("ord_stat_name")
        or raw.get("ord_stat")
        or raw.get("status")
        or raw.get("rjct_rson_name")
        or ""
    ).strip().upper()
    cancel_text = str(
        raw.get("cncl_yn")
        or raw.get("rvse_cncl_dvsn_name")
        or ""
    ).strip().upper()
    is_cancelled = (
        cancel_text in {"Y", "CANCEL", "CANCELLED", "취소"}
        or "취소" in status_text
        or "CANCEL" in status_text
        or "REJECT" in status_text
        or "거부" in status_text
    )
    return {
        "ticker": ticker,
        "side": side,
        "qty": qty,
        "price": price,
        "fill_price": fill_price,
        "filled_qty": filled,
        "remaining_qty": remaining,
        "is_cancelled": is_cancelled,
        "status_text": status_text,
        "order_id": order_id,
        "client_order_id": client_oid,
        "raw": raw,
    }


def match_day_order(
    orders: List[Dict[str, Any]],
    ticker: str,
    side: str,
    qty: int,
    price: float,
    client_order_id: Optional[str] = None,
    price_tolerance: float = 0.02,
) -> Optional[Dict[str, Any]]:
    """
    Find a broker day-book row that corresponds to our attempted order.
    Prefer exact client_order_id; otherwise ticker + side + qty + price band.
    """
    want_ticker = str(ticker).strip().upper()
    want_side = str(side).strip().upper()
    want_qty = int(qty)
    want_cid = str(client_order_id or "").strip().upper()

    normalized: List[Dict[str, Any]] = []
    for o in orders or []:
        if not isinstance(o, dict):
            continue
        if o.get("pdno") or o.get("PDNO") or o.get("sll_buy_dvsn_cd") or o.get("SLL_BUY_DVSN_CD"):
            normalized.append(normalize_kis_day_order(o))
        else:
            normalized.append({
                "ticker": str(o.get("ticker") or "").upper(),
                "side": str(o.get("side") or "").upper(),
                "qty": float(o.get("qty") or 0),
                "price": float(o.get("price") or 0),
                "order_id": str(o.get("order_id") or ""),
                "client_order_id": str(o.get("client_order_id") or "").upper(),
                "raw": o,
            })

    if want_cid:
        for row in normalized:
            if row.get("client_order_id") == want_cid and row.get("ticker") == want_ticker:
                return row

    for row in normalized:
        if row.get("ticker") != want_ticker:
            continue
        if row.get("side") and row.get("side") != want_side:
            continue
        if int(row.get("qty") or 0) != want_qty:
            continue
        row_px = float(row.get("price") or 0.0)
        if price > 0 and row_px > 0:
            if abs(row_px - float(price)) / float(price) > price_tolerance:
                continue
        return row
    return None
