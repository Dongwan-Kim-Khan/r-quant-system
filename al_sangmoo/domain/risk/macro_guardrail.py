"""
Automated Macro Circuit Breaker & Defensive Trailing Stop Guardrail.
"""
from typing import Dict, Any, List
from al_sangmoo.core.constants import derive_stop_price, derive_target_price
from al_sangmoo.domain.quant.macro import classify_msi_stance, MSI_SELECTIVE_BUY_MAX

def evaluate_macro_circuit_breaker(msi_score: float, holdings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Evaluates active holdings against the MSI risk index and generates automated risk actions.
    High MSI = danger (CASH_EXIT / DEFENSE), low MSI = buy climate.
    """
    emergency_actions = []
    stance = classify_msi_stance(msi_score)
    
    if stance == "CASH_EXIT":
        # Systemic Crisis: Mandatory Cash Preservation
        for h in holdings:
            emergency_actions.append({
                "holding_id": h.get("id"),
                "ticker": h.get("ticker"),
                "action": "FORCE_LIQUIDATE",
                "urgency": "CRITICAL",
                "reason": f"[CRITICAL] MSI 2.0 거시 위험도 위험 단계(MSI={msi_score:.1f}pt)로 전량 긴급 현금화 권고"
            })
        directive = "시스템 위기 감지로 전 포지션 긴급 청산 및 현금 100% 확보 가동."
    elif stance == "DEFENSE_HOLD":
        # Defensive Mode: Tighten Stops to lock in profits
        for h in holdings:
            cur_price = float(h.get("current_price", h.get("buy_price", 0)))
            orig_stop = float(h.get("stop_loss_price", derive_stop_price(cur_price)))
            tight_stop = round(max(orig_stop, cur_price * 0.985), 2) # Tight 1.5% trailing stop
            
            emergency_actions.append({
                "holding_id": h.get("id"),
                "ticker": h.get("ticker"),
                "action": "TIGHTEN_TRAILING_STOP",
                "urgency": "ELEVATED",
                "original_stop_price": orig_stop,
                "adjusted_stop_price": tight_stop,
                "reason": f"⚠️ MSI 2.0 방어 모드(MSI={msi_score:.1f}pt)로 타이트 트레일링 스탑(${tight_stop:,.2f}) 적용"
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
                "reason": "정상 매매 기후 유지 (15% 목표가 / -4% 손절선)"
            })
        directive = "거시 환경 안정적. 17년 퀀트 표준 목표가 및 손절선 유지."

    return {
        "msi_score": msi_score,
        "macro_stance": stance,
        "circuit_breaker_active": msi_score >= MSI_SELECTIVE_BUY_MAX,
        "directive": directive,
        "actions": emergency_actions
    }
