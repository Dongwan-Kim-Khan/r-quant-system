"""
Macro Stance Index 2.0 (MSI 2.0) Regime Calculation Engine.
"""

def calculate_msi_regime(gauges: dict, defense_count: int, buy_count: int, matched_shocks: list) -> dict:
    """
    Computes MSI 2.0 (0.0 ~ 100.0 pt):
    - M_hard (max 60 pt): 10Y Yield, DXY, VIX, WTI Oil, Gold
    - M_nlp (max 25 pt): Defense vs Bullish Keyword Ratio in Stream
    - M_shock (max 15 pt): External Geopolitical / Inflation / Monetary Shocks
    """
    m_hard = 0.0
    
    # 1. 10Y US Yield (Max 25 pts)
    us10y_val = gauges.get("us10y", {}).get("val", 4.0)
    if us10y_val >= 4.50:
        us10y_pts = 25.0
    elif us10y_val >= 4.30:
        us10y_pts = 15.0
    elif us10y_val >= 4.10:
        us10y_pts = 8.0
    else:
        us10y_pts = 0.0
    m_hard += us10y_pts

    # 2. Dollar Index DXY (Max 10 pts)
    dxy_val = gauges.get("dxy", {}).get("val", 100.0)
    if dxy_val >= 106.0:
        dxy_pts = 10.0
    elif dxy_val >= 104.0:
        dxy_pts = 5.0
    else:
        dxy_pts = 0.0
    m_hard += dxy_pts

    # 3. VIX Fear Index (Max 15 pts)
    vix_val = gauges.get("vix", {}).get("val", 15.0)
    if vix_val >= 25.0:
        vix_pts = 15.0
    elif vix_val >= 20.0:
        vix_pts = 8.0
    else:
        vix_pts = 0.0
    m_hard += vix_pts

    # 4. WTI Crude Oil (Max 10 pts)
    wti_val = gauges.get("wti", {}).get("val", 75.0)
    if wti_val >= 85.0:
        wti_pts = 10.0
    elif wti_val >= 80.0:
        wti_pts = 5.0
    else:
        wti_pts = 0.0
    m_hard += wti_pts

    # 5. NLP Broadcast Sentiment (Max 25 pts)
    total_tokens = defense_count + buy_count
    if total_tokens > 0:
        def_ratio = defense_count / total_tokens
        m_nlp = min(25.0, round(def_ratio * 25.0, 1))
    else:
        m_nlp = 12.5

    # 6. External Shock Shifter (Max 15 pts)
    m_shock = min(15.0, len(matched_shocks) * 5.0)

    total_msi = round(m_hard + m_nlp + m_shock, 1)

    if total_msi >= 75.0:
        macro_stance = "CASH_EXIT"
        stance_kr = "현금화 / 적극 리스크 회피 주간 (Red 75점 이상)"
        headline = f"[거시 게이트 0단계: 거시 위험 지수 {total_msi}점 / 긴급 현금 확보 및 포트폴리오 리스크 헷지 권고]"
        directive = f"거시 위험 지수(MSI {total_msi}점: 10년물 금리 {us10y_val}%, 유가 ${wti_val})가 한계치를 초과했습니다. 신규 매수를 전면 금지하고 비중 축소 및 현금 확보에 집중하십시오."
    elif total_msi >= 50.0:
        macro_stance = "DEFENSE_HOLD"
        stance_kr = "신규 매수 보류 / 관망·현금 유지 주간 (Orange 50~74점)"
        headline = f"[거시 게이트 0단계: 거시 위험 지수 {total_msi}점 / 신규 매수 보류 및 관망 권고]"
        directive = f"거시 위험 지수(MSI {total_msi}점: 10년물 금리 {us10y_val}%, 유가 ${wti_val}) 및 방송 지침상, 현재 장세는 신규 매수를 쉬어가고 관망해야 하는 장세입니다. 다만 거시 리스크 진정 시 즉시 공략할 최우선 1순위 후보 종목을 사전 선별합니다."
    elif total_msi >= 30.0:
        macro_stance = "SELECTIVE_BUY"
        stance_kr = "선별적 눌림목 분할 매수 기후 (Yellow 30~49점)"
        headline = f"[거시 게이트 0단계: 거시 기후 지수 {total_msi}점 / 선별적 눌림목 매수 유효]"
        directive = f"거시 지표(금리 {us10y_val}%, VIX {vix_val})가 안정권에 근접했습니다. 퀀트 지표가 충족된 주도주에 한해 26일 기준선 지지 부근에서 1차 분할 매수가 유효합니다."
    else:
        macro_stance = "ACTIVE_BUY"
        stance_kr = "최적 매수 기후 / 적극 비중 확대 주간 (Green 30점 미만)"
        headline = f"[거시 게이트 0단계: 거시 쾌청 지수 {total_msi}점 / 적극 매수 기후]"
        directive = f"거시 시스템 리스크가 완전히 해소되었습니다. 17년 퀀트 합격 종목을 적극적으로 포트폴리오에 편입하십시오."

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
        "external_shocks": matched_shocks,
        "defense_keyword_count": defense_count
    }
