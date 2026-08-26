"""
AL-SANGMOO QUANT TERMINAL: AUTOPILOT APIRouter
REST endpoints for inspecting and controlling the 100% Full-Auto Pilot Quant Trader.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from al_sangmoo.domain.risk.autopilot_trader import default_autopilot

router = APIRouter(prefix="/api/autopilot", tags=["AutoPilot"])


class AutoPilotToggleRequest(BaseModel):
    enabled: bool = Field(..., description="True to enable full-auto buying, False to pause")


@router.get("/status")
async def get_autopilot_status():
    """Returns the operational status, current schedule, and recent autonomous trades."""
    return default_autopilot.get_status()


@router.post("/toggle")
async def toggle_autopilot(req: AutoPilotToggleRequest):
    """Enables or pauses the full-auto buying engine."""
    default_autopilot.set_enabled(req.enabled)
    return {
        "status": "success",
        "is_enabled": default_autopilot.is_enabled,
        "message": f"전자동 매수(Auto-Buy) 기능이 {'활성화(ON)' if req.enabled else '비활성화(PAUSED)'}되었습니다."
    }


@router.post("/trigger_now")
async def trigger_autopilot_now():
    """Manually triggers a full-auto cycle immediately (scan -> conviction -> slot buy)."""
    res = await default_autopilot.run_autopilot_cycle(force_scan=True)
    return res