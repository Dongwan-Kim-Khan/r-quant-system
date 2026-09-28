"""
Macro Stance Index 2.0 (MSI 2.0) Regime Calculation Engine.
Single Source of Truth (SSOT) for Macro Gauges, NLP Sentiment, and Stance Classification.
"""
from typing import Dict, Any, List, Optional
import re
import time
from al_sangmoo.core.constants import MACRO_DEFENSE_KEYWORDS, EXTERNAL_SHOCK_KEYWORDS
from al_sangmoo.core.feature_flags import use_stream_sentiment

# MSI 2.0 is a RISK index (0-100): higher score = more danger, fewer new buys.
# Never invert this polarity (do not treat high MSI as bullish).
MSI_ACTIVE_BUY_MAX = 30.0       # [0, 30)  ACTIVE_BUY
MSI_SELECTIVE_BUY_MAX = 50.0    # [30, 50) SELECTIVE_BUY
MSI_DEFENSE_HOLD_MAX = 75.0     # [50, 75) DEFENSE_HOLD
# >= 75.0 CASH_EXIT
MSI_DEFAULT_SCORE = 50.0

_SPY_REGIME_CACHE: Dict[str, Any] = {}
_SPY_REGIME_TTL_SEC = 3600.0


def classify_msi_stance(msi_score: float) -> str:
    """Map MSI risk score to Gate-0 stance. High MSI = cash / defense."""
    score = float(msi_score)
    if score >= MSI_DEFENSE_HOLD_MAX:
        return "CASH_EXIT"
    if score >= MSI_SELECTIVE_BUY_MAX:
        return "DEFENSE_HOLD"
    if score >= MSI_ACTIVE_BUY_MAX:
        return "SELECTIVE_BUY"
    return "ACTIVE_BUY"


def is_new_buy_blocked(msi_score: float) -> bool:
    """CASH_EXIT (MSI >= 75): block all new entries."""
    return classify_msi_stance(msi_score) == "CASH_EXIT"


def is_defense_or_worse(msi_score: float) -> bool:
    """DEFENSE_HOLD or CASH_EXIT (MSI >= 50)."""
    return float(msi_score) >= MSI_SELECTIVE_BUY_MAX


def _with_sentiment(score: float) -> float:
    if not use_stream_sentiment():
        return float(score)
    from al_sangmoo.domain.quant.stream_sentiment_pipeline import get_stream_sentiment_offset
    return max(0.0, min(100.0, float(score) + get_stream_sentiment_offset()))


def extract_msi_score(macro_info: Optional[Dict[str, Any]] = None, default: float = MSI_DEFAULT_SCORE) -> float:
    """Read MSI from nested dashboard / stream payloads. First valid key wins."""
    if not isinstance(macro_info, dict):
        return _with_sentiment(default)
    climate = macro_info.get("macro_climate") if isinstance(macro_info.get("macro_climate"), dict) else {}
    candidates = [
        macro_info.get("msi_score"),
        climate.get("msi_score") if climate else None,
        macro_info.get("msi"),
        climate.get("msi") if climate else None,
    ]
    for raw in candidates:
        if raw is None or raw == "":
            continue
        try:
            return _with_sentiment(raw)
        except (TypeError, ValueError):
            continue
    return _with_sentiment(default)


def fetch_spy_trend_regime() -> Dict[str, Any]:
    """Cached SPY vs SMA200 snapshot. Network failure returns {}."""
    now = time.time()
    cached = _SPY_REGIME_CACHE.get("SPY")
    if cached and (now - cached.get("ts", 0)) < _SPY_REGIME_TTL_SEC:
        return dict(cached.get("data") or {})
    try:
        import pandas as pd
        import yfinance as yf
        raw = yf.download("SPY", period="18mo", interval="1d", progress=False, auto_adjust=False)
        if raw is None or raw.empty:
            return {}
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        close = raw["Close"].dropna()
        if len(close) < 30:
            return {}
        sma_series = close.rolling(window=200, min_periods=30).mean()
        sma200 = sma_series.iloc[-1]
        last = float(close.iloc[-1])
        sma = float(sma200) if sma200 == sma200 else 0.0
        if last <= 0 or sma <= 0:
            return {}
        history = []
        for dt, c, s in zip(close.index[-60:], close.values[-60:], sma_series.values[-60:]):
            d_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]
            history.append({"date": d_str, "close": round(float(c), 2), "sma200": round(float(s), 2) if s == s else None})
        data = {
            "spy_close": round(last, 4),
            "spy_sma200": round(sma, 4),
            "is_bull_regime": last >= sma,
            "spy_history": history,
            "source": "yahoo",
        }
        _SPY_REGIME_CACHE["SPY"] = {"ts": now, "data": data}
        return dict(data)
    except Exception:
        return {}


_QQQ_REGIME_CACHE: Dict[str, Any] = {}
_QQQ_REGIME_TTL_SEC = 3600.0


def fetch_qqq_trend_regime() -> Dict[str, Any]:
    """Cached QQQ vs SMA20 snapshot for C-2 trend gate. Network failure returns {}."""
    now = time.time()
    cached = _QQQ_REGIME_CACHE.get("QQQ")
    if cached and (now - cached.get("ts", 0)) < _QQQ_REGIME_TTL_SEC:
        return dict(cached.get("data") or {})
    try:
        import pandas as pd
        import yfinance as yf
        raw = yf.download("QQQ", period="6mo", interval="1d", progress=False, auto_adjust=False)
        if raw is None or raw.empty:
            return {}
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        close = raw["Close"].dropna()
        if len(close) < 20:
            return {}
        sma20 = close.rolling(window=20, min_periods=20).mean().iloc[-1]
        last = float(close.iloc[-1])
        sma = float(sma20) if sma20 == sma20 else 0.0
        if last <= 0 or sma <= 0:
            return {}
        data = {
            "qqq_close": round(last, 4),
            "qqq_sma20": round(sma, 4),
            "qqq_trend_pass": last >= sma,
            "source": "yahoo",
        }
        _QQQ_REGIME_CACHE["QQQ"] = {"ts": now, "data": data}
        return dict(data)
    except Exception:
        return {}


def resolve_capital_regime(
    msi_score: Optional[float] = None,
    spy_close: Optional[float] = None,
    spy_sma200: Optional[float] = None,
    fetch_spy: bool = False,
) -> Dict[str, Any]:
    """
    3-slot vs 2-slot capital regime.

    Primary (v2 constitution): SPY close >= 200-day SMA → bull (3-slot).
    Fallback if SPY unavailable: risk-on slots only when MSI < 50 (ACTIVE/SELECTIVE).
    Never use `msi < 65` — that treated DEFENSE_HOLD (50-74) as bull.
    """
    spy_source = "injected"
    if (spy_close is None or spy_sma200 is None or float(spy_close or 0) <= 0 or float(spy_sma200 or 0) <= 0) and fetch_spy:
        snap = fetch_spy_trend_regime()
        spy_close = snap.get("spy_close") or spy_close
        spy_sma200 = snap.get("spy_sma200") or spy_sma200
        spy_source = snap.get("source", "fetch")

    has_spy = spy_close is not None and spy_sma200 is not None and float(spy_close) > 0 and float(spy_sma200) > 0
    score = MSI_DEFAULT_SCORE if msi_score is None else float(msi_score)
    if has_spy:
        is_bull = float(spy_close) >= float(spy_sma200)
        source = f"spy_200sma:{spy_source}"
    else:
        is_bull = not is_defense_or_worse(score)
        source = "msi_fallback_lt_50"

    from al_sangmoo.core.constants import (
        MAX_SLOTS_BEAR,
        MAX_SLOTS_BULL,
        SLOT_WEIGHTS_BEAR,
        SLOT_WEIGHTS_BULL,
    )

    weights = list(SLOT_WEIGHTS_BULL if is_bull else SLOT_WEIGHTS_BEAR)
    return {
        "is_bull_regime": bool(is_bull),
        "max_slots": MAX_SLOTS_BULL if is_bull else MAX_SLOTS_BEAR,
        "slot_weights": weights,
        "slot_fraction": weights[0] if weights else (0.50 if is_bull else 0.25),
        "cash_reserve_fraction": 0.0 if is_bull else 0.50,  # idle → QQQ proxy in bull
        "msi_score": score,
        "msi_stance": classify_msi_stance(score),
        "spy_close": float(spy_close) if has_spy else None,
        "spy_sma200": float(spy_sma200) if has_spy else None,
        "regime_source": source,
    }


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

    # Total MSI Calculation (0.0 ~ 100.0 pt) — RISK index, high = danger
    total_msi = round(min(100.0, max(0.0, m_hard + m_nlp + m_shock)), 1)
    macro_stance = classify_msi_stance(total_msi)

    if macro_stance == "CASH_EXIT":
        stance_kr = "현금화 / 숏 헤지 주간 (Red 75~100점)"
        headline = f"[거시 게이트 0단계: 위험 경보 (MSI {total_msi}점) / 신규 매수 전면 중단 및 현금 확보]"
        directive = f"거시 위험 지수(MSI {total_msi}점: 10년물 금리 {us10y_val}%, 유가 ${wti_val})가 한계치를 초과했습니다. 신규 매수를 전면 금지하고 비중 축소 및 현금 확보에 집중하십시오."
    elif macro_stance == "DEFENSE_HOLD":
        stance_kr = "신규 매수 보류 / 관망·현금 유지 주간 (Orange 50~74점)"
        headline = f"[거시 게이트 0단계: 거시 위험 지수 {total_msi}점 / 신규 매수 보류 및 관망 권고]"
        directive = f"거시 위험 지수(MSI {total_msi}점: 10년물 금리 {us10y_val}%, 유가 ${wti_val}) 및 방송 지침상, 현재 장세는 신규 매수를 쉬어가고 관망해야 하는 장세입니다. 다만 거시 리스크 진정 시 즉시 공략할 최우선 1순위 후보 종목을 사전 선별합니다."
    elif macro_stance == "SELECTIVE_BUY":
        stance_kr = "선별적 눌림목 분할 매수 주간 (Yellow 30~49점)"
        headline = f"[거시 게이트 0단계: 거시 중립 (MSI {total_msi}점) / 선별적 눌림목 매수 유효]"
        directive = f"거시 위험 지수(MSI {total_msi}점: 10년물 금리 {us10y_val}%, VIX {vix_val})가 중립 범위에 위치합니다. 26일 기준선 지지가 확인된 주도 종목에 한하여 소액 분할 매수가 유효합니다."
    else:
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
        "msi_is_risk_index": True,
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
