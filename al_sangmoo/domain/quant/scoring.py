"""
Canonical 17-Year Proprietary Quant Scoring and 3-Tier Classification Engine.
Single Source of Truth (SSOT) for Bull Score, Sniper Score, Bear Score, and Tier 1/2/3 Portfolios.
"""
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple, Optional, Set, Literal
import pandas as pd
import numpy as np

from al_sangmoo.core.constants import STOCK_DICT, TICKER_SECTORS


@dataclass(frozen=True)
class QuantIndicators:
    """Immutable snapshot of computed rolling technical indicators."""
    close: float
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: float = 0.0
    tenkan: float = 0.0
    kijun: float = 0.0
    span_a: float = 0.0
    span_b: float = 0.0
    raw_span_a: float = 0.0
    raw_span_b: float = 0.0
    sma20: float = 0.0
    sma50: float = 0.0
    sma60: float = 0.0
    sma200: float = 0.0
    vol_sma20: float = 0.0
    vol_ratio: float = 1.0
    kijun_gap_pct: float = 0.0
    cloud_top: float = 0.0
    cloud_bottom: float = 0.0

    @classmethod
    def from_values(
        cls,
        close: float,
        kijun: float,
        tenkan: float,
        span_a: float,
        span_b: float,
        vol_ratio: float,
        open_p: float = 0.0,
        high_p: float = 0.0,
        low_p: float = 0.0,
        volume: float = 0.0,
        sma20: float = 0.0,
        sma50: float = 0.0,
        sma60: float = 0.0,
        sma200: float = 0.0,
        vol_sma20: float = 0.0,
        raw_span_a: float = 0.0,
        raw_span_b: float = 0.0
    ) -> "QuantIndicators":
        cloud_top = max(span_a, span_b)
        cloud_bottom = min(span_a, span_b)
        kijun_gap = ((close - kijun) / kijun) * 100.0 if kijun > 0 else 0.0
        return cls(
            close=close,
            open=open_p or close,
            high=high_p or close,
            low=low_p or close,
            volume=volume,
            tenkan=tenkan,
            kijun=kijun,
            span_a=span_a,
            span_b=span_b,
            raw_span_a=raw_span_a,
            raw_span_b=raw_span_b,
            sma20=sma20,
            sma50=sma50,
            sma60=sma60,
            sma200=sma200,
            vol_sma20=vol_sma20,
            vol_ratio=vol_ratio,
            kijun_gap_pct=kijun_gap,
            cloud_top=cloud_top,
            cloud_bottom=cloud_bottom
        )


@dataclass(frozen=True)
class WeeklyTrendContext:
    """Weekly timeframe trend alignment context."""
    is_weekly_bull: bool = True
    weekly_close: float = 0.0
    weekly_cloud_top: float = 0.0
    weekly_kijun: float = 0.0
    weekly_tenkan: float = 0.0
    trend_regime: Literal["BULLISH_TREND", "CONSOLIDATING", "BEARISH_TREND"] = "BULLISH_TREND"


@dataclass(frozen=True)
class InstitutionalFlowContext:
    """OBV and 14-day volume flow accumulation signatures."""
    obv_status: Literal["STEALTH_ACCUM", "BULL_FLOW", "NEUTRAL"] = "NEUTRAL"
    obv_label: str = "[NEUTRAL]"
    flow_ratio: float = 1.0
    flow_label: str = "1.0x"
    flow_score: int = 50
    is_stealth_accum: bool = False


@dataclass(frozen=True)
class TrampolineBounceContext:
    """14-day Ichimoku cloud trampoline bounce detection context."""
    detected: bool = False
    days_ago: int = 0
    touch_gap_pct: float = 0.0
    close_gap_pct: float = 0.0


@dataclass(frozen=True)
class QuantScoreBreakdown:
    """Detailed point breakdown for quantitative auditing."""
    cloud_pts: int
    kijun_pts: int
    vdu_pts: int
    tenkan_pts: int
    total_bull_score: int
    bear_score: int
    sniper_score: int
    composite_score: float


@dataclass(frozen=True)
class TierClassification:
    """3-Tier Quant Classification output."""
    tier: Literal["TIER_1", "TIER_2", "TIER_3", "UNCLASSIFIED"]
    tier_name_kr: str
    strategy_code: str
    score: int
    composite_score: float
    entry_price: float
    target_price: float         # +15.0%
    stop_price: float           # -4.0% Hard Stop
    partial_tp_price: float     # +8.0% (50% Take Profit)
    is_tier1_qualified: bool
    is_tier2_qualified: bool
    is_tier3_qualified: bool
    rationale: str


def calculate_canonical_bull_score(ind: QuantIndicators) -> Tuple[int, Dict[str, int]]:
    """
    Evaluates canonical 17-Year Quant Formula with graduated sweet-spots (Max 100 pt):
    - Cloud Clearance (Max 35 pt):
        close >= cloud_top: +35 pt
        close >= cloud_top * 0.97: +25 pt
        close >= cloud_bottom: +15 pt
    - Kijun-sen Sweet-Spot Support (Max 35 pt):
        -0.5% <= kijun_gap <= +3.5%: +35 pt
        -0.8% <= kijun_gap <= +4.8%: +25 pt
        -1.5% <= kijun_gap <= +7.0%: +15 pt
    - 20-Day Volume Dry-Up (Max 20 pt):
        vol_ratio <= 0.60: +20 pt
        vol_ratio <= 0.85: +15 pt
        vol_ratio <= 1.10: +10 pt
    - 9-Day Tenkan Momentum (Max 10 pt):
        tenkan >= kijun: +10 pt
        close >= tenkan: +5 pt
    """
    cloud_pts = 0
    if ind.close >= ind.cloud_top:
        cloud_pts = 35
    elif ind.close >= ind.cloud_top * 0.97:
        cloud_pts = 25
    elif ind.close >= ind.cloud_bottom:
        cloud_pts = 15

    kijun_pts = 0
    kgap = ind.kijun_gap_pct
    if -0.5 <= kgap <= 3.5:
        kijun_pts = 35
    elif -0.8 <= kgap <= 4.8:
        kijun_pts = 25
    elif -1.5 <= kgap <= 7.0:
        kijun_pts = 15

    vdu_pts = 0
    if ind.vol_ratio <= 0.60:
        vdu_pts = 20
    elif ind.vol_ratio <= 0.85:
        vdu_pts = 15
    elif ind.vol_ratio <= 1.10:
        vdu_pts = 10

    tenkan_pts = 0
    if ind.tenkan >= ind.kijun:
        tenkan_pts = 10
    elif ind.close >= ind.tenkan:
        tenkan_pts = 5

    total = min(100, cloud_pts + kijun_pts + vdu_pts + tenkan_pts)
    breakdown = {
        "cloud_pts": cloud_pts,
        "kijun_pts": kijun_pts,
        "vdu_pts": vdu_pts,
        "tenkan_pts": tenkan_pts
    }
    return total, breakdown


def calculate_canonical_sniper_score(ind: QuantIndicators, trampoline: TrampolineBounceContext) -> Tuple[int, Dict[str, int]]:
    """
    Evaluates Strategy 2 (Cloud Trampoline Bounce Sniper, Max 100 pt):
    - Trampoline Bounce Detected within 14 bars: +40 pt
    - Price Clearance above Cloud: close >= cloud_top (+30 pt) or >= cloud_top * 0.98 (+20 pt)
    - Kijun Position: kijun_gap >= 0 (+15 pt) or >= -1.0 (+10 pt)
    - Tenkan Alignment: tenkan >= kijun (+15 pt) or close >= tenkan (+10 pt)
    """
    tramp_pts = 40 if trampoline.detected else 0

    cloud_pts = 0
    if ind.close >= ind.cloud_top:
        cloud_pts = 30
    elif ind.close >= ind.cloud_top * 0.98:
        cloud_pts = 20

    kijun_pts = 0
    if ind.kijun_gap_pct >= 0:
        kijun_pts = 15
    elif ind.kijun_gap_pct >= -1.0:
        kijun_pts = 10

    tenkan_pts = 0
    if ind.tenkan >= ind.kijun:
        tenkan_pts = 15
    elif ind.close >= ind.tenkan:
        tenkan_pts = 10

    total = min(100, tramp_pts + cloud_pts + kijun_pts + tenkan_pts)
    breakdown = {
        "trampoline_pts": tramp_pts,
        "cloud_pts": cloud_pts,
        "kijun_pts": kijun_pts,
        "tenkan_pts": tenkan_pts
    }
    return total, breakdown


def calculate_canonical_bear_score(ind: QuantIndicators) -> int:
    """
    Evaluates Risk Breakdown Score (Max 90 pt):
    - Close < Kijun-sen (Vital Support Collapse): +40 pt
    - Close < Cloud Bottom (Total Cloud Collapse): +35 pt
    - Kijun Gap < -2.0% (Structural Breakdown): +15 pt
    """
    bear_score = 0
    if ind.close < ind.kijun:
        bear_score += 40
    if ind.close < ind.cloud_bottom:
        bear_score += 35
    if ind.kijun_gap_pct < -2.0:
        bear_score += 15
    return min(100, bear_score)


def evaluate_quant_score(
    close: float,
    kijun: float,
    tenkan: float,
    span_a: float,
    span_b: float,
    vol_ratio: float,
    trampoline_detected: bool = False,
    is_weekly_bull: bool = True,
    days_ago: int = 0
) -> Dict[str, Any]:
    """
    Canonical 17-Year Scoring Matrix Evaluator.
    Produces comprehensive quantitative intelligence and verdict dictionary.
    """
    ind = QuantIndicators.from_values(
        close=close,
        kijun=kijun,
        tenkan=tenkan,
        span_a=span_a,
        span_b=span_b,
        vol_ratio=vol_ratio
    )
    tramp = TrampolineBounceContext(detected=trampoline_detected, days_ago=days_ago)

    bull_score, b_breakdown = calculate_canonical_bull_score(ind)
    sniper_score, s_breakdown = calculate_canonical_sniper_score(ind, tramp)
    bear_score = calculate_canonical_bear_score(ind)

    # Strategy Activation Checks
    is_strat1_active = is_weekly_bull and (bull_score >= 80) and (-0.8 <= ind.kijun_gap_pct <= 4.8)
    is_sniper_active = is_weekly_bull and trampoline_detected and (sniper_score >= 80) and (close >= ind.cloud_top * 0.97)

    if is_strat1_active and is_sniper_active:
        quant_type = "BULL"
        quant_verdict = "3-Gate Triple Alpha (3-Gate 만점 특급 주도주)"
        quant_score_text = "100 / 100 pt (TRIPLE_ALPHA)"
        action_directive = f"[3-Gate 만점] 주봉 정배열 + 구름대 도약({days_ago}일 전) + 26일 기준선({ind.kijun_gap_pct:+.1f}%) 안착."
    elif is_sniper_active:
        quant_type = "BULL"
        quant_verdict = "Cloud Trampoline (구름대 지지 도약 진입)"
        quant_score_text = f"{sniper_score} / 100 pt (TRAMPOLINE_BUY)"
        action_directive = f"[트램펄린 반등] 주봉 상승장 + {days_ago}일 전 구름대 지지 도약 후 상방 시세 분출(기준선 대비 {ind.kijun_gap_pct:+.1f}%). 목표 +15% / 손절 -4%."
    elif is_strat1_active:
        quant_type = "BULL"
        quant_verdict = "3-Gate Trend Leader (추세 주도주 집중 진입)"
        quant_score_text = f"{bull_score} / 100 pt (BULL_BUY)"
        action_directive = "주봉 상승장 + 26일 기준선 및 일목 구름대 상단 안착 확인. 3-Slot 균등 진입 적합."
    elif bull_score >= 70:
        quant_type = "BULL"
        quant_verdict = "3-Gate Sweet Spot (기준선 눌림목 진입)"
        quant_score_text = f"{bull_score} / 100 pt (BULL_BUY)"
        action_directive = "26일 기준선 및 일목 구름대 상단 안착 확인. 3-Slot 분할 진입 적합."
    elif bear_score >= 50:
        quant_type = "BEAR"
        quant_verdict = "Risk Breakdown (생명선 붕괴 / 매수 금지)"
        quant_score_text = f"{bull_score} / 100 pt (BEAR_EXIT)"
        action_directive = "26일 기준선(생명선) 및 구름대 붕괴. 물타기 금지 및 숏 헤지 우위 구간."
    else:
        quant_type = "NEUTRAL"
        quant_verdict = "Neutral Consolidation (박스권 수렴)"
        quant_score_text = f"{bull_score} / 100 pt (HOLD)"
        action_directive = "구름대 내부 또는 기준선 수렴 구간. 방향성 돌파 확인 전까지 관망 유지."

    kgap = ind.kijun_gap_pct
    kijun_status = "status-bull" if -0.5 <= kgap <= 3.5 else ("status-bear" if kgap < -0.5 else "status-neutral")
    kijun_badge = "SUPPORTED" if -0.5 <= kgap <= 3.5 else ("BREAKDOWN" if kgap < -0.5 else "OVERHEATED")
    
    tenkan_status = "status-bull" if tenkan >= kijun else "status-bear"
    tenkan_badge = "GOLDEN CROSS" if tenkan >= kijun else "DEAD CROSS"
    
    cloud_status = "status-bull" if close >= ind.cloud_top else ("status-bear" if close < ind.cloud_bottom else "status-neutral")
    cloud_badge = "ABOVE CLOUD" if close >= ind.cloud_top else ("BELOW CLOUD" if close < ind.cloud_bottom else "INSIDE CLOUD")
    
    vol_status = "status-bull" if vol_ratio <= 0.60 else ("status-neutral" if vol_ratio <= 1.10 else "status-bear")
    vol_badge = "VOLUME DRY" if vol_ratio <= 0.60 else ("NORMAL VOL" if vol_ratio <= 1.10 else "HIGH VOL")

    return {
        "bull_score": bull_score,
        "sniper_score": sniper_score,
        "bear_score": bear_score,
        "composite_score": round(bull_score * 0.45 + 50 * 0.35 + max(0, 100 - abs(kgap) * 15) * 0.20, 1),
        "cloud_pts": b_breakdown["cloud_pts"],
        "kijun_pts": b_breakdown["kijun_pts"],
        "vdu_pts": b_breakdown["vdu_pts"],
        "tenkan_pts": b_breakdown["tenkan_pts"],
        "quant_type": quant_type,
        "quant_verdict": quant_verdict,
        "quant_score_text": quant_score_text,
        "action_directive": action_directive,
        "is_strat1_active": is_strat1_active,
        "is_sniper_active": is_sniper_active,
        "intelligence": {
            "verdict": quant_verdict,
            "score": quant_score_text,
            "bull_score": bull_score,
            "sniper_score": sniper_score,
            "bear_score": bear_score,
            "type": quant_type,
            "kijun": {
                "val": f"${kijun:,.2f} ({kgap:+.1f}%)",
                "status": kijun_status,
                "badge": kijun_badge,
                "desc": f"현재가 ${close:,.2f} / 26일선 ${kijun:,.2f} (이격 {kgap:+.1f}%)"
            },
            "tenkan": {
                "val": f"${tenkan:,.2f}",
                "status": tenkan_status,
                "badge": tenkan_badge,
                "desc": f"9일 전환선 ${tenkan:,.2f} {'상단 정배열' if tenkan >= kijun else '하단 역배열'}"
            },
            "cloud": {
                "val": f"${ind.cloud_top:,.2f}",
                "status": cloud_status,
                "badge": cloud_badge,
                "desc": f"일목 구름대({round(ind.cloud_bottom,1)}~{round(ind.cloud_top,1)}) {'상단 안착' if close >= ind.cloud_top else ('하단 붕괴' if close < ind.cloud_bottom else '내부 횡보')}"
            },
            "vol": {
                "val": f"{round(vol_ratio*100)}% (20D)",
                "status": vol_status,
                "badge": vol_badge,
                "desc": f"20일 평균 거래량 대비 {round(vol_ratio*100)}% ({'매도세 고갈 완벽' if vol_ratio <= 0.60 else '통상 거래량'})"
            },
            "action": action_directive
        }
    }


def classify_quant_tier(
    ticker: str,
    ind: QuantIndicators,
    weekly: WeeklyTrendContext,
    flow: InstitutionalFlowContext,
    trampoline: TrampolineBounceContext,
    macro_tailwind_sectors: List[str],
    sector: str = "GENERAL"
) -> TierClassification:
    """
    Evaluates canonical 3-Tier Classification rules for a single symbol.
    """
    bull_score, _ = calculate_canonical_bull_score(ind)
    sniper_score, _ = calculate_canonical_sniper_score(ind, trampoline)
    
    kgap = ind.kijun_gap_pct
    is_safe_entry = (-0.8 <= kgap <= 3.5)
    is_macro_tailwind = sector in macro_tailwind_sectors
    
    # Strategy 1 (Classic Pullback): Weekly Bull, Bull Score >= 80, Kijun gap in range
    is_strat1 = weekly.is_weekly_bull and (bull_score >= 80) and (-0.8 <= kgap <= 4.8)
    # Strategy 2 (Cloud Bounce Sniper): Weekly Bull, Trampoline bounce, Sniper Score >= 80
    is_strat2 = weekly.is_weekly_bull and trampoline.detected and (sniper_score >= 80) and (ind.close >= ind.cloud_top * 0.97)
    
    item_score = 100 if (is_strat1 and is_strat2) else (bull_score if is_strat1 else sniper_score if is_strat2 else bull_score)
    composite_score = round(item_score * 0.45 + flow.flow_score * 0.35 + max(0, 100 - abs(kgap) * 15) * 0.20, 1)

    # Standard Target & Stops
    entry_price = ind.close
    target_price = round(entry_price * 1.15, 2)       # +15.0%
    stop_price = round(entry_price * 0.96, 2)         # -4.0% Hard Stop
    partial_tp_price = round(entry_price * 1.08, 2)   # +8.0% Partial TP

    # Tier Qualification Predicates
    is_tier1_qualified = (
        weekly.is_weekly_bull and
        (is_strat1 or is_strat2) and
        is_safe_entry and
        (is_macro_tailwind or flow.is_stealth_accum or flow.flow_ratio >= 1.20 or item_score == 100 or flow.obv_status == "STEALTH_ACCUM")
    )
    is_tier2_qualified = (
        weekly.is_weekly_bull and
        is_strat1 and
        is_safe_entry and
        ind.vol_ratio <= 0.85
    )
    is_tier3_qualified = (
        weekly.is_weekly_bull and
        is_strat2
    )

    if is_tier1_qualified:
        tier = "TIER_1"
        tier_name_kr = "Tier 1 거시 주도주 / 스마트머니 매집"
        strategy_code = "DUAL_5_STAR" if (is_strat1 and is_strat2) else "MACRO_LEADER"
        rationale = f"거시 수혜({sector}) + 스마트머니 수급({flow.flow_label}) + 17년 퀀트({item_score}점) 동시 충족"
    elif is_tier2_qualified:
        tier = "TIER_2"
        tier_name_kr = "Tier 2 구조적 눌림목 지지"
        strategy_code = "STRAT1_PULLBACK"
        rationale = f"주봉 상승장 + 26일 기준선({kgap:+.1f}%) 안착 + 거래량 마름({round(ind.vol_ratio*100)}%)"
    elif is_tier3_qualified:
        tier = "TIER_3"
        tier_name_kr = "Tier 3 구름대 도약 스나이퍼"
        strategy_code = "STRAT2_SNIPER"
        rationale = f"주봉 상승장 + {trampoline.days_ago}일 전 구름대 지지 도약 후 상방 시세 분출"
    else:
        tier = "UNCLASSIFIED"
        tier_name_kr = "미분류 / 관망"
        strategy_code = "UNCLASSIFIED"
        rationale = "3-Tier 진입 조건 미달 (관망)"

    return TierClassification(
        tier=tier,
        tier_name_kr=tier_name_kr,
        strategy_code=strategy_code,
        score=item_score,
        composite_score=composite_score,
        entry_price=entry_price,
        target_price=target_price,
        stop_price=stop_price,
        partial_tp_price=partial_tp_price,
        is_tier1_qualified=is_tier1_qualified,
        is_tier2_qualified=is_tier2_qualified,
        is_tier3_qualified=is_tier3_qualified,
        rationale=rationale
    )


def classify_3tier_candidates(
    chart_data: Dict[str, Dict[str, Any]],
    tailwind_sectors: List[str],
    stream_mentioned_tickers: Optional[Set[str]] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    SSOT 3-Tier Classification Engine.
    Processes universe chart intelligence and returns deterministic:
    (tier1_picks, tier2_picks, tier3_picks)
    """
    if stream_mentioned_tickers is None:
        stream_mentioned_tickers = set()

    tier1_candidates: List[Dict[str, Any]] = []
    tier2_candidates: List[Dict[str, Any]] = []
    tier3_candidates: List[Dict[str, Any]] = []

    for ticker, c in chart_data.items():
        try:
            sector = TICKER_SECTORS.get(ticker, "GENERAL")
            is_macro_tailwind = sector in tailwind_sectors
            
            # Origin determination
            is_mentioned = ticker in stream_mentioned_tickers or c.get("origin") == "VIKINGS_LIVE"
            origin_tag = "VIKINGS_LIVE" if is_mentioned else "QUANT_DISCOVERY"
            origin_label = "1.0 방송 언급" if is_mentioned else "2.0 퀀트 발굴"
            
            close = float(c.get("latest_close", c.get("price", c.get("close", 0.0))))
            if close <= 0:
                continue
                
            kijun = float(c.get("latest_kijun", c.get("kijun", close)))
            tenkan = float(c.get("latest_tenkan", c.get("tenkan", close)))
            span_a = float(c.get("span_a", close))
            span_b = float(c.get("span_b", close))
            vol_ratio = float(c.get("latest_vol_ratio", c.get("vol_ratio", 1.0)))
            
            # Contexts
            ind = QuantIndicators.from_values(
                close=close,
                kijun=kijun,
                tenkan=tenkan,
                span_a=span_a,
                span_b=span_b,
                vol_ratio=vol_ratio
            )
            
            is_weekly_bull = bool(c.get("is_weekly_bull", True))
            weekly = WeeklyTrendContext(is_weekly_bull=is_weekly_bull)
            
            flow = InstitutionalFlowContext(
                obv_status=c.get("obv_status", "NEUTRAL"),
                obv_label=c.get("obv_label", "[NEUTRAL]"),
                flow_ratio=float(c.get("flow_ratio", 1.0)),
                flow_label=c.get("flow_label", "1.0x"),
                flow_score=int(c.get("flow_score", 50)),
                is_stealth_accum=bool(c.get("is_stealth_accum", False))
            )
            
            trampoline = TrampolineBounceContext(
                detected=bool(c.get("trampoline_detected", c.get("is_sniper", False))),
                days_ago=int(c.get("trampoline_days_ago", 0)),
                touch_gap_pct=float(c.get("touch_gap_pct", 0.0)),
                close_gap_pct=float(c.get("close_gap_pct", 0.0))
            )
            
            tier_eval = classify_quant_tier(
                ticker=ticker,
                ind=ind,
                weekly=weekly,
                flow=flow,
                trampoline=trampoline,
                macro_tailwind_sectors=tailwind_sectors,
                sector=sector
            )
            
            action_desc = c.get("intelligence", {}).get("action", tier_eval.rationale) if isinstance(c.get("intelligence"), dict) else c.get("action", tier_eval.rationale)
            
            item = {
                "ticker": ticker,
                "name": STOCK_DICT.get(ticker, [ticker])[0],
                "price": close,
                "score": tier_eval.score,
                "composite_score": tier_eval.composite_score,
                "sector": sector,
                "is_macro_tailwind": is_macro_tailwind,
                "obv_status": flow.obv_status,
                "obv_label": flow.obv_label,
                "flow_ratio": flow.flow_ratio,
                "flow_label": flow.flow_label,
                "flow_score": flow.flow_score,
                "is_stealth_accum": flow.is_stealth_accum,
                "kijun_gap": ind.kijun_gap_pct,
                "vol_ratio": round(ind.vol_ratio * 100),
                "origin": origin_tag,
                "origin_label": origin_label,
                "is_sniper": trampoline.detected,
                "is_strat1": tier_eval.is_tier2_qualified or (tier_eval.is_tier1_qualified and tier_eval.strategy_code in ["DUAL_5_STAR", "STRAT1_PULLBACK"]),
                "action": action_desc,
                "target_price": tier_eval.target_price,
                "stop_price": tier_eval.stop_price,
                "rs_3m": float(c.get("rs_3m", 0.0)),
                "momentum_3m": float(c.get("rs_3m", 0.0)),
                "is_breakout": bool(c.get("is_breakout", False))
            }
            
            if tier_eval.is_tier1_qualified:
                tier1_candidates.append(item)
            elif tier_eval.is_tier2_qualified:
                tier2_candidates.append(item)
            elif tier_eval.is_tier3_qualified:
                tier3_candidates.append(item)
                
        except Exception:
            continue

    # Deterministic sorting per tier
    tier1_candidates.sort(key=lambda x: (-x["flow_score"], -x["score"], abs(x["kijun_gap"]), x["vol_ratio"]))
    tier2_candidates.sort(key=lambda x: (-x["score"], abs(x["kijun_gap"]), x["vol_ratio"]))
    tier3_candidates.sort(key=lambda x: (-x["score"], -x["flow_score"], abs(x["kijun_gap"])))

    # Prevent duplicates across tiers
    tier1_picks = tier1_candidates[:4]
    tier1_tickers = set(x["ticker"] for x in tier1_picks)
    tier2_picks = [x for x in tier2_candidates if x["ticker"] not in tier1_tickers][:4]
    tier3_picks = [x for x in tier3_candidates if x["ticker"] not in tier1_tickers][:4]

    return tier1_picks, tier2_picks, tier3_picks
