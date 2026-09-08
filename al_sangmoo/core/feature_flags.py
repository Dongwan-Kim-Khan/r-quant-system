"""Runtime feature flags for optional open-source integrations. Default OFF."""
from __future__ import annotations

import os


def is_enabled(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return bool(default)
    return raw in ("1", "true", "yes", "on")


def use_riskfolio_hrp() -> bool:
    return is_enabled("USE_RISKFOLIO_HRP")


def use_nautilus_order_fsm() -> bool:
    return is_enabled("USE_NAUTILUS_ORDER_FSM")


def use_residual_momentum() -> bool:
    return is_enabled("USE_RESIDUAL_MOMENTUM")


def use_webhook_router() -> bool:
    return is_enabled("USE_WEBHOOK_ROUTER")


def use_stream_sentiment() -> bool:
    return is_enabled("USE_STREAM_SENTIMENT")
