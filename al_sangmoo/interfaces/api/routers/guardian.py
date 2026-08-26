"""
FastAPI Router for Autonomous Portfolio Guardian Daemon.
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from al_sangmoo.domain.risk.portfolio_guardian import default_guardian

router = APIRouter(prefix="/api/guardian", tags=["Portfolio Guardian"])

class ToggleGuardianRequest(BaseModel):
    enabled: bool

@router.get("/status")
def get_guardian_status():
    """Returns guardian operational status, rules, and recent auto-exit records."""
    return default_guardian.get_status()

@router.post("/toggle")
def toggle_guardian(req: ToggleGuardianRequest):
    """Enables or disables automated broker order execution."""
    default_guardian.set_enabled(req.enabled)
    return {"status": "success", "is_enabled": default_guardian.is_enabled}

@router.post("/check_now")
async def trigger_guardian_check():
    """Triggers an on-demand audit and executes any triggered exit rules."""
    return await default_guardian.check_and_execute_guardian_rules()
