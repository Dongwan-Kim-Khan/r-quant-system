"""
Macro Stance Index 2.0 (MSI 2.0) Regime Calculation Engine.
Single Source of Truth (SSOT) for Macro Gauges, NLP Sentiment, and Stance Classification.
"""
from typing import Dict, Any, List, Optional
import re
from al_sangmoo.core.constants import MACRO_DEFENSE_KEYWORDS, EXTERNAL_SHOCK_KEYWORDS


def evaluate_macro_stance(
    gauges: Optional[Dict[str, Any]] = None,
    defense_count: int = 0,
    buy_count: int = 0,
    matched_shocks: Optional[List[str]] = None,
    transcript: str = "",
    title: str = ""
) -> Dict[str, Any]:
    """
    Single Authoritative Source of Truth for Macro Stance Index 2.0 (MSI 2.0).
    MSI 2.0 Formula (0.0 ~ 100.0 pt):
      MSI = M_hard (max 60.0 pt) + M_nlp (max 25.0 pt) + M_shock (max 15.0 pt)
    """
    if gauges is None:
        gauges = {}

    combined_text = (title + " " + transcript).upper() if (title or transcript) else ""

    # 1. Financial Macro Hard Gauges (M_hard: max 60.0 pts)
    us10y_val = float(gauges.get("us10y", {}).get("val", 4.0)) if isinstance(gauges.get("us10y"), dict) else 4.0
    vix_val = float(gauges.get("vix", {}).get("val", 15.0)) if isinstance(gauges.get("vix"), dict) else 15.0
    wti_val = float(gauges.get("wti", {}).get("val", 75.0)) if isinstance(gauges.get("wti"), dict) else 75.0
    dxy_val = float(gauges.get("dxy", {}).get("val", 100.0)) if isinstance(gauges.get("dxy"), dict) else 100.0

    # 1.1 US 10Y Treasury Yield (^TNX, Max 25.0 pts)
    if us10y_val >= 4.50:
        us10y_pts = 25.0
    elif us10y_val >= 4.30:
        us10y_pts = 18.0
    elif us10y_val >= 4.10:
        us10y_pts = 10.0
    elif us10y_val >= 3.90:
        us10y_pts = 4.0
    else:
        us10y_pts = 0.0

    # 1.2 VIX Fear & Volatility Index (^VIX, Max 15.0 pts)
    if vix_val >= 25.0:
        vix_pts = 15.0
    elif vix_val >= 20.0:
        vix_pts = 10.0
    elif vix_val >= 16.0:
        vix_pts = 5.0
    else:
        vix_pts = 0.0

    # 1.3 WTI Crude Oil (CL=F, Max 10.0 pts)
    if wti_val >= 85.0:
        wti_pts = 10.0
    elif wti_val >= 80.0:
        wti_pts = 6.0
    elif wti_val >= 75.0:
        wti_pts = 3.0
    else:
        wti_pts = 0.0

    # 1.4 Dollar Index DXY (DX-Y.NYB, Max 10.0 pts)
    if dxy_val >= 105.0:
        dxy_pts = 10.0
    elif dxy_val >= 103.0:
        dxy_pts = 6.0
    elif dxy_val >= 100.0:
        dxy_pts = 2.0
    else:
        dxy_pts = 0.0

    m_hard = round(us10y_pts + vix_pts + wti_pts + dxy_pts, 1)

    # 2. Host Spoken NLP Directive Sentiment (M_nlp: max 25.0 pts)
    if transcript:
        calc_def_count = sum(transcript.count(kw) for kw in MACRO_DEFENSE_KEYWORDS)
        calc_buy_count = sum(transcript.count(kw) for kw in ['눌림목', '매수 기회', '담아야', '모아가야', '분할 매수', '순환매 진입', '우상향'])
        defense_count = calc_def_count
        buy_count = calc_buy_count
        
        if len(transcript) > 500:
            m_nlp = round(25.0 * (defense_count / (defense_count + buy_count + 0.1)), 1)
            m_nlp = min(25.0, max(0.0, m_nlp))
        else:
            title_defense = any(k in title for k in ['쫄아있는', '금리', '하락', '위기', '붕괴', '경고', '리스크', '조심', '전쟁', '부채'])
            m_nlp = 16.0 if title_defense else 8.0
    else:
        total_tokens = defense_count + buy_count
        if total_tokens > 0:
            def_ratio = defense_count / (total_tokens + 0.1)
            m_nlp = min(25.0, max(0.0, round(def_ratio * 25.0, 1)))
        else:
            m_nlp = 12.5

    # 3. Geopolitical & External Shock Factor (M_shock: max 15.0 pts)
    external_shocks: List[str] = []
    m_shock = 0.0

    if matched_shocks is not None:
        external_shocks = list(matched_shocks)
        # Category specific evaluation if named shocks
        shock_text = " ".join(matched_shocks)
        has_war = any(k in shock_text for k in ["전쟁", "지정학", "중동", "우크라", "이란", "대만", "WAR", "CONFLICT"])
        has_trade = any(k in shock_text for k in ["관세", "무역", "트럼프", "보복", "TARIFF", "TRADE"])
        has_rate = any(k in shock_text for k in ["금리", "연준", "FOMC", "파월", "국채", "INTEREST", "FED", "RATE", "인플레이션"])
        
        if has_war or has_trade or has_rate:
            if has_war:
                m_shock += 6.0
            if has_trade:
                m_shock += 4.0
            if has_rate:
                m_shock += 5.0
        else:
            m_shock = len(matched_shocks) * 5.0
    elif combined_text:
        if any(k in combined_text for k in ["전쟁", "지정학", "중동", "우크라", "이란", "대만", "WAR", "CONFLICT"]):
            external_shocks.append("지정학적 분쟁 및 전쟁 리스크")
            m_shock += 6.0
        if any(k in combined_text for k in ["관세", "무역", "트럼프", "보복", "TARIFF", "TRADE"]):
            external_shocks.append("무역 분쟁 및 관세 불확실성")
            m_shock += 4.0
        if any(k in combined_text for k in ["금리", "연준", "FOMC", "파월", "국채", "INTEREST", "FED", "RATE"]):
            external_shocks.append("금리 경로 및 통화정책 영향권")
            m_shock += 5.0

    m_shock = min(15.0, round(m_shock, 1))

    # Total MSI Calculation (0.0 ~ 100.0 pt)
    total_msi = round(min(100.0, max(0.0, m_hard + m_nlp + m_shock)), 1)

    if total_msi >= 75.0:
        macro_stance = "CASH_EXIT"
        stance_kr = "현금화 / 숏 헤지 주간 (Red 75~100점)"
        headline = f"[거시 게이트 0단계: 위험 경보 (MSI {total_msi}점) / 신규 매수 전면 중단 및 현금 확보]"
        directive = f"거시 위험 지수(MSI {total_msi}점: 10년물 금리 {us10y_val}%, 유가 ${wti_val})가 한계치를 초과했습니다. 신규 매수를 전면 금지하고 비중 축소 및 현금 확보에 집중하십시오."
    elif total_msi >= 50.0:
        macro_stance = "DEFENSE_HOLD"
        stance_kr = "신규 매수 보류 / 관망·현금 유지 주간 (Orange 50~74점)"
        headline = f"[거시 게이트 0단계: 거시 위험 지수 {total_msi}점 / 신규 매수 보류 및 관망 권고]"
        directive = f"거시 위험 지수(MSI {total_msi}점: 10년물 금리 {us10y_val}%, 유가 ${wti_val}) 및 방송 지침상, 현재 장세는 신규 매수를 쉬어가고 관망해야 하는 장세입니다. 다만 거시 리스크 진정 시 즉시 공략할 최우선 1순위 후보 종목을 사전 선별합니다."
    elif total_msi >= 30.0:
        macro_stance = "SELECTIVE_BUY"
        stance_kr = "선별적 눌림목 분할 매수 주간 (Yellow 30~49점)"
        headline = f"[거시 게이트 0단계: 거시 중립 (MSI {total_msi}점) / 선별적 눌림목 매수 유효]"
        directive = f"거시 위험 지수(MSI {total_msi}점: 10년물 금리 {us10y_val}%, VIX {vix_val})가 중립 범위에 위치합니다. 26일 기준선 지지가 확인된 주도 종목에 한하여 소액 분할 매수가 유효합니다."
    else:
        macro_stance = "ACTIVE_BUY"
        stance_kr = "최적 매수 기후 / 적극 분할 매수 주간 (Green 0~29점)"
        headline = f"[거시 게이트 0단계: 최적 매수 기후 (MSI {total_msi}점) / 적극 분할 매수 가능]"
        directive = f"거시 지표가 매우 우호적입니다. 17년 퀀트 합격 종목을 적극적으로 포트폴리오에 편입하십시오."

    return {
        "msi_score": total_msi,
        "msi_breakdown": {
            "m_hard": m_hard,
            "m_hard_max": 60,
            "us10y_pts": us10y_pts,
            "dxy_pts": dxy_pts,
            "vix_pts": vix_pts,
            "wti_pts": wti_pts,
            "m_nlp": m_nlp,
            "m_nlp_max": 25,
            "def_count": defense_count,
            "buy_count": buy_count,
            "m_shock": m_shock,
            "m_shock_max": 15
        },
        "macro_stance": macro_stance,
        "macro_stance_kr": stance_kr,
        "macro_headline": headline,
        "macro_action_directive": directive,
        "external_shocks": external_shocks if external_shocks else ["금리 경로 및 통화정책 영향권", "지정학적 리스크"],
        "defense_keyword_count": defense_count
    }


def calculate_msi_regime(
    gauges: Optional[Dict[str, Any]] = None,
    defense_count: int = 0,
    buy_count: int = 0,
    matched_shocks: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Legacy backward-compatible adapter for calculate_msi_regime().
    Delegates directly to evaluate_macro_stance().
    """
    return evaluate_macro_stance(
        gauges=gauges,
        defense_count=defense_count,
        buy_count=buy_count,
        matched_shocks=matched_shocks
    )
