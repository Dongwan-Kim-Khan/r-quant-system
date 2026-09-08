"""Howtrader-inspired TradingView webhook gateway. Gated by USE_WEBHOOK_ROUTER."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request

from al_sangmoo.core.feature_flags import use_webhook_router

router = APIRouter(prefix="/api/v2/webhook", tags=["Webhook"])


def verify_tradingview_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


@router.post("/tradingview")
async def handle_tradingview_signal(
    request: Request,
    x_signature: Optional[str] = Header(default=None),
):
    if not use_webhook_router():
        raise HTTPException(status_code=404, detail="Webhook router disabled")

    secret = os.environ.get("WEBHOOK_SECRET", "").strip()
    body = await request.body()
    if not secret:
        raise HTTPException(status_code=503, detail="WEBHOOK_SECRET is not configured")
    if not x_signature or not verify_tradingview_signature(body, x_signature, secret):
        raise HTTPException(status_code=401, detail="Invalid Webhook Signature")

    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    ticker = str(payload.get("ticker") or "").strip().upper()
    action = str(payload.get("action") or "").strip().upper()
    if not ticker or action not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="ticker and action=BUY|SELL required")

    raise HTTPException(
        status_code=501,
        detail="Webhook order execution is not implemented; signal accepted but not queued",
    )
