"""
Automated Macro Circuit Breaker, Defensive Trailing Guardrail,
and C-2 Dynamic Leverage Overlay (QQQ / QLD).
"""
from typing import Any, Dict, List, Optional

from al_sangmoo.core.constants import (
    HARD_STOP_PCT,
    TRAILING_ACTIVATE_PCT,
    derive_stop_price,
    derive_target_price,
)
from al_sangmoo.domain.quant.macro import MSI_SELECTIVE_BUY_MAX, classify_msi_stance
from al_sangmoo.domain.risk.cash_proxy import resolve_leverage_overlay


def evaluate_macro_circuit_breaker(
    msi_score: float, holdings: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Evaluates active holdings against the MSI risk index and generates automated risk actions.
    High MSI = danger (CASH_EXIT / DEFENSE), low MSI = buy climate.
    """
    emergency_actions = []
    stance = classify_msi_stance(msi_score)

    if stance == "CASH_EXIT":
        for h in holdings:
            emergency_actions.append({
                "holding_id": h.get("id"),
                "ticker": h.get("ticker"),
                "action": "FORCE_LIQUIDATE",
                "urgency": "CRITICAL",
                "reason": (
                    f"[CRITICAL] MSI 2.0 거시 위험도 위험 단계(MSI={msi_score:.1f}pt)로 "
                    "전량 긴급 현금화 권고"
                ),
            })
        directive = "시스템 위기 감지로 전 포지션 긴급 청산 및 현금 100% 확보 가동."
    elif stance == "DEFENSE_HOLD":
        for h in holdings:
            cur_price = float(h.get("current_price", h.get("buy_price", 0)))
            orig_stop = float(h.get("stop_loss_price", derive_stop_price(cur_price)))
            tight_stop = round(max(orig_stop, cur_price * 0.985), 2)

            emergency_actions.append({
                "holding_id": h.get("id"),
                "ticker": h.get("ticker"),
                "action": "TIGHTEN_TRAILING_STOP",
                "urgency": "ELEVATED",
                "original_stop_price": orig_stop,
                "adjusted_stop_price": tight_stop,
                "reason": (
                    f"⚠️ MSI 2.0 방어 모드(MSI={msi_score:.1f}pt)로 "
                    f"타이트 트레일링 스탑(${tight_stop:,.2f}) 적용"
                ),
            })
        directive = "방어 장세로 보유 포지션 손절선 상향 조정 및 이익 보전 활성화."
    else:
        for h in holdings:
            cur_price = float(h.get("current_price", h.get("buy_price", 0)))
            emergency_actions.append({
                "holding_id": h.get("id"),
                "ticker": h.get("ticker"),
                "action": "HOLD_POSITION",
                "urgency": "NORMAL",
                "stop_price": h.get("stop_loss_price", derive_stop_price(cur_price)),
                "target_price": h.get("target_price", derive_target_price(cur_price)),
                "reason": (
                    f"정상 매매 기후 유지 "
                    f"(+{TRAILING_ACTIVATE_PCT:.0f}% 트레일 / -{HARD_STOP_PCT:.1f}% 손절선)"
                ),
            })
        directive = (
            f"거시 환경 안정적. C-2 표준 목표가(+{TRAILING_ACTIVATE_PCT:.0f}%) "
            f"및 손절선(-{HARD_STOP_PCT:.1f}%) 유지."
        )

    return {
        "msi_score": msi_score,
        "macro_stance": stance,
        "circuit_breaker_active": msi_score >= MSI_SELECTIVE_BUY_MAX,
        "directive": directive,
        "actions": emergency_actions,
    }


def evaluate_dynamic_leverage(
    spy_close: Optional[float] = None,
    spy_sma200: Optional[float] = None,
    vix: Optional[float] = None,
) -> Dict[str, Any]:
    """
    C-2 leverage module:
      SPY >= SMA200 AND VIX < 20 → 1.5x (QLD mix allowed)
      else → immediate return to 1.0x QQQ core
    """
    overlay = resolve_leverage_overlay(spy_close, spy_sma200, vix)
    return {
        **overlay,
        "return_to_1x": not overlay["leverage_mode"],
        "flag": "LEVERAGE_1_5X" if overlay["leverage_mode"] else "CORE_1_0X",
    }
