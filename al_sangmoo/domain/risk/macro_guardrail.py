"""
Automated Macro Circuit Breaker & Defensive Trailing Stop Guardrail.
"""
from typing import Dict, Any, List

def evaluate_macro_circuit_breaker(msi_score: float, holdings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Evaluates active holdings against the MSI macro climate and generates automated risk actions.
    """
    emergency_actions = []
    
    if msi_score >= 75.0:
        # Systemic Crisis: Mandatory Cash Preservation
        for h in holdings:
            emergency_actions.append({
                "holding_id": h.get("id"),
                "ticker": h.get("ticker"),
                "action": "FORCE_LIQUIDATE",
                "urgency": "CRITICAL",
                "reason": f"[CRITICAL] MSI 2.0 거시 위험도 위험 단계(MSI={msi_score:.1f}pt)로 전량 긴급 현금화 권고"
            })
        stance = "CASH_EXIT"
        directive = "시스템 위기 감지로 전 포지션 긴급 청산 및 현금 100% 확보 가동."
    elif msi_score >= 50.0:
        # Defensive Mode: Tighten Stops to lock in profits
        for h in holdings:
            cur_price = float(h.get("current_price", h.get("buy_price", 0)))
            orig_stop = float(h.get("stop_loss_price", cur_price * 0.96))
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
        stance = "DEFENSE_HOLD"
        directive = "방어 장세로 보유 포지션 손절선 상향 조정 및 이익 보전 활성화."
    else:
        for h in holdings:
            cur_price = float(h.get("current_price", h.get("buy_price", 0)))
            emergency_actions.append({
                "holding_id": h.get("id"),
                "ticker": h.get("ticker"),
                "action": "HOLD_POSITION",
                "urgency": "NORMAL",
                "stop_price": h.get("stop_loss_price", round(cur_price * 0.96, 2)),
                "target_price": h.get("target_price", round(cur_price * 1.15, 2)),
                "reason": "정상 매매 기후 유지 (15% 목표가 / -4% 손절선)"
            })
        stance = "ACTIVE_BUY" if msi_score < 30.0 else "SELECTIVE_BUY"
        directive = "거시 환경 안정적. 17년 퀀트 표준 목표가 및 손절선 유지."

    return {
        "msi_score": msi_score,
        "macro_stance": stance,
        "circuit_breaker_active": msi_score >= 50.0,
        "directive": directive,
        "actions": emergency_actions
    }
