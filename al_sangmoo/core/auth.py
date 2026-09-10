"""Mutating-route API-key gate (X-API-Key).

Auth is required when:
  - REQUIRE_AUTH is truthy, or
  - AL_SANGMOO_API_KEY / API_KEY is set, or
  - KIS live (non-paper) credentials are configured (fail-closed),
    unless REQUIRE_AUTH is explicitly false or the process is using a test DB.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

from fastapi import Header, HTTPException


def configured_api_key() -> str:
    return (os.environ.get("AL_SANGMOO_API_KEY") or os.environ.get("API_KEY") or "").strip()


def _running_isolated_test() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    db = os.environ.get("AL_SANGMOO_DB_PATH", "").replace("\\", "/").lower()
    base = db.rsplit("/", 1)[-1]
    return "test_" in base or base.startswith("test") or "/tests/" in db


def _kis_live_trading_configured() -> bool:
    app_key = os.environ.get("KIS_APP_KEY", "").strip()
    app_secret = os.environ.get("KIS_APP_SECRET", "").strip()
    account = (os.environ.get("KIS_CANO") or os.environ.get("KIS_ACCOUNT_NO") or "").strip()
    if not (app_key and app_secret and account):
        return False
    mode = (os.environ.get("KIS_MODE") or os.environ.get("KIS_ENV") or "vps").strip().lower()
    return mode not in ("vps", "paper", "virtual", "demo")


def auth_is_required() -> bool:
    explicit = os.environ.get("REQUIRE_AUTH", "").strip().lower()
    if explicit in ("0", "false", "no", "off"):
        return False
    if explicit in ("1", "true", "yes", "on"):
        return True
    if _running_isolated_test():
        return False
    if configured_api_key():
        return True
    return _kis_live_trading_configured()


def _keys_match(provided: str, expected: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(provided.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


def require_mutating_auth(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> None:
    """FastAPI dependency: reject unauthenticated mutating requests when auth is armed."""
    if not auth_is_required():
        return
    expected = configured_api_key()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="REQUIRE_AUTH is enabled but AL_SANGMOO_API_KEY / API_KEY is not configured",
        )
    provided = (x_api_key or "").strip()
    if not provided or not _keys_match(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
