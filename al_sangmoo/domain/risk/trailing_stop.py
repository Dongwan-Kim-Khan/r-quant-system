"""
Al-Sangmoo C-2 uncapped trailing-stop SSOT.

Constitution:
  - Dual-clock stop: EOD Hard Stop (-7.0%) and Intraday Emergency Stop (-10.0%)
  - Standalone kijun close breakdown: permanently disabled (trail floor candidate only)
  - After peak gain >= +18%: trailing floor = max(Kijun-26, peak_high - 3.0 * ATR(14))
  - Full exit only (no partial take-profit)
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

import pandas as pd

from al_sangmoo.core.constants import (
    ATR_MULTIPLIER,
    EOD_HARD_STOP_PCT,
    EMERGENCY_STOP_PCT,
    HARD_STOP_PCT,
    STANDALONE_KIJUN_EXIT_ENABLED,
    TRAILING_ACTIVATE_PCT,
    derive_eod_stop_price,
    derive_emergency_stop_price,
    derive_stop_price,
)

ATR_TRAIL_MULT = ATR_MULTIPLIER  # C-2 SSOT: 3.0
MARKET_CACHE_TTL_SEC = 300.0

_MARKET_CACHE: Dict[str, Dict[str, Any]] = {}


def compute_trailing_floor(
    kijun_26: float,
    peak_high: float,
    atr_14: float,
    atr_multiplier: Optional[float] = None,
) -> float:
    """max(26-day kijun, peak high - multiplier * ATR(14)). Returns 0.0 if both legs are unusable."""
    candidates = []
    kijun = float(kijun_26 or 0.0)
    if kijun > 0:
        candidates.append(kijun)

    peak = float(peak_high or 0.0)
    atr = float(atr_14 or 0.0)
    mult = float(atr_multiplier if atr_multiplier is not None and atr_multiplier > 0 else ATR_TRAIL_MULT)
    if peak > 0 and atr > 0:
        atr_leg = peak - mult * atr
        if atr_leg > 0:
            candidates.append(atr_leg)

    if not candidates:
        return 0.0
    return round(max(candidates), 2)


def latch_peak_gain(buy_price: float, current_price: float, peak_high: float = 0.0, max_gain_pct: float = 0.0) -> Dict[str, float]:
    """Running peak price / gain since entry. Trailing stays armed after +18% even if PnL pulls back."""
    buy = float(buy_price or 0.0)
    cur = float(current_price or 0.0)
    peak = max(buy, float(peak_high or 0.0), cur)
    pnl_pct = ((cur - buy) / buy) * 100.0 if buy > 0 and cur > 0 else 0.0
    peak_gain_pct = ((peak - buy) / buy) * 100.0 if buy > 0 and peak > 0 else 0.0
    max_gain = max(float(max_gain_pct or 0.0), pnl_pct, peak_gain_pct)
    return {
        "peak_high": round(peak, 4),
        "pnl_pct": round(pnl_pct, 4),
        "max_gain_pct": round(max_gain, 4),
        "trailing_active": max_gain >= TRAILING_ACTIVATE_PCT,
    }


def evaluate_guardian_exit(
    buy_price: float,
    current_price: float,
    kijun_26: float = 0.0,
    atr_14: float = 0.0,
    peak_high: float = 0.0,
    max_gain_pct: float = 0.0,
    is_eod_window: bool = False,
    stop_loss_price: Optional[float] = None,
    allow_standalone_kijun: bool = STANDALONE_KIJUN_EXIT_ENABLED,
) -> Dict[str, Any]:
    """
    Decide a full-exit action (or HOLD) for one position under C-2 dual-clock rules.

    Priority:
      1. Intraday Emergency Stop (-10.0% / derive_emergency_stop_price) — fires anytime during RTH.
      2. EOD Hard Stop (-7.0% / derive_eod_stop_price) — fires ONLY during EOD closing window.
      3. Uncapped trailing after TRAILING_ACTIVATE_PCT (+18%) peak gain: max(Kijun-26, peak - 3.0*ATR14).
      4. Legacy lot ticket-stop / kijun breakdown compatibility if lot was created under C1-M2.
    """
    buy = float(buy_price or 0.0)
    cur = float(current_price or 0.0)
    if buy <= 0 or cur <= 0:
        return {"action": None, "reason": "invalid_price", "trailing_active": False, "trailing_floor": 0.0}

    latched = latch_peak_gain(buy, cur, peak_high, max_gain_pct)
    pnl_pct = latched["pnl_pct"]
    kijun = float(kijun_26 or 0.0)

    eod_stop = derive_eod_stop_price(buy)
    emerg_stop = derive_emergency_stop_price(buy)
    ticket_stop = float(stop_loss_price or 0.0) if stop_loss_price is not None and float(stop_loss_price or 0.0) > 0 else eod_stop

    # Check for legacy lot compatibility (C1-M2 ticket stop ~0.95 vs C-2 0.93)
    # Must have an explicit stop_loss_price from an older open position and ratio >= 0.945
    is_legacy_c1 = bool(
        stop_loss_price is not None
        and float(stop_loss_price or 0.0) > 0
        and buy > 0
        and (float(stop_loss_price) / buy) >= 0.945
    )

    arm_threshold = 15.0 if is_legacy_c1 else TRAILING_ACTIVATE_PCT
    trailing_active = latched["max_gain_pct"] >= arm_threshold
    effective_atr_mult = 2.5 if is_legacy_c1 else ATR_TRAIL_MULT
    floor = compute_trailing_floor(kijun, latched["peak_high"], atr_14, atr_multiplier=effective_atr_mult) if trailing_active else 0.0

    result = {
        "action": None,
        "reason": None,
        "pnl_pct": pnl_pct,
        "hard_stop_price": ticket_stop if is_legacy_c1 else eod_stop,
        "emergency_stop_price": emerg_stop,
        "eod_stop_price": eod_stop,
        "trailing_active": trailing_active,
        "trailing_floor": floor,
        "peak_high": latched["peak_high"],
        "max_gain_pct": latched["max_gain_pct"],
        "kijun_26": round(kijun, 2) if kijun > 0 else 0.0,
        "atr_14": round(float(atr_14 or 0.0), 4),
        "is_full_exit": True,
    }

    # 1. Intraday Emergency Stop (-10.0%) — ALWAYS active during market session
    if pnl_pct <= -EMERGENCY_STOP_PCT or cur <= emerg_stop:
        result["action"] = "AUTO_EMERGENCY_STOP"
        result["reason"] = (
            f"[가디언 비상손절] 비상 손절선(-{EMERGENCY_STOP_PCT:.1f}% / ${emerg_stop:,.2f}) 이탈 "
            f"(수익률: {pnl_pct:+.2f}%, 현재가: ${cur:,.2f})"
        )
        return result

    # 2. Legacy lot ticket-stop (-5.0%) handling
    if is_legacy_c1 and (cur <= ticket_stop or pnl_pct <= -5.0):
        result["action"] = "AUTO_STOP_LOSS"
        result["reason"] = (
            f"[가디언 레거시 칼손절] 손절선(-5.0% / ${ticket_stop:,.2f}) 도달 "
            f"(수익률: {pnl_pct:+.2f}%, 현재가: ${cur:,.2f})"
        )
        return result

    # 3. EOD Hard Stop (-7.0%) — active ONLY during closing window
    if is_eod_window:
        effective_eod_stop = ticket_stop if (ticket_stop > 0 and not is_legacy_c1) else eod_stop
        if pnl_pct <= -EOD_HARD_STOP_PCT or cur <= effective_eod_stop:
            result["action"] = "AUTO_STOP_LOSS"
            result["reason"] = (
                f"[가디언 EOD 종가 손절] 종가 손절선(-{EOD_HARD_STOP_PCT:.1f}% / ${effective_eod_stop:,.2f}) 도달 "
                f"(수익률: {pnl_pct:+.2f}%, 현재가: ${cur:,.2f})"
            )
            return result

    # 4. Trailing Stop (Arms at +18.0%, floor = max(kijun, peak - 3.0*ATR))
    if trailing_active:
        if floor > 0 and cur < floor:
            result["action"] = "AUTO_TRAILING_TP"
            result["reason"] = (
                f"[가디언 트레일링 익절] floor=${floor:,.2f} "
                f"(max(기준선 ${kijun:,.2f}, 고점 ${latched['peak_high']:,.2f} - {ATR_TRAIL_MULT}*ATR)) "
                f"이탈 (수익률: {pnl_pct:+.2f}%, 현재가: ${cur:,.2f})"
            )
        return result

    # 5. Standalone Kijun Exit (Disabled in C-2; enabled only for legacy C1 lots or explicit opt-in)
    if (allow_standalone_kijun or is_legacy_c1) and kijun > 0 and cur < kijun:
        result["action"] = "AUTO_KIJUN_EXIT"
        result["reason"] = (
            f"[가디언 기준선 이탈] 26일 기준선(${kijun:,.2f}) 종가 하회 "
            f"(수익률: {pnl_pct:+.2f}%)"
        )
        return result

    return result


def format_holding_advice(eval_result: Dict[str, Any], buy_price: float) -> str:
    """Human-readable exit_advice for the portfolio row."""
    action = eval_result.get("action")
    pnl = float(eval_result.get("pnl_pct") or 0.0)
    if action == "AUTO_EMERGENCY_STOP":
        emerg_stop = derive_emergency_stop_price(buy_price)
        return f"비상 손절 긴급 매도 권고 (비상손절선 ${emerg_stop:,.2f} 이탈 {pnl:+.2f}%)"
    if action == "AUTO_STOP_LOSS":
        eod_stop = derive_eod_stop_price(buy_price)
        return f"EOD 종가 손절 매도 권고 (손절선 ${eod_stop:,.2f} 이탈 {pnl:+.2f}%)"
    if action == "AUTO_TRAILING_TP":
        floor = float(eval_result.get("trailing_floor") or 0.0)
        return f"트레일링 익절 전량 청산 (floor ${floor:,.2f}, {pnl:+.2f}%)"
    if action == "AUTO_KIJUN_EXIT":
        kijun = float(eval_result.get("kijun_26") or 0.0)
        return f"26일 기준선(${kijun:,.2f}) 이탈 전량 청산 ({pnl:+.2f}%)"
    if eval_result.get("trailing_active"):
        floor = float(eval_result.get("trailing_floor") or 0.0)
        if floor > 0:
            return f"무제한 트레일링 익절 홀딩 (Let Winners Run, floor ${floor:,.2f}, {pnl:+.2f}%)"
        return f"무제한 트레일링 익절 홀딩 (Let Winners Run, {pnl:+.2f}%)"
    eod_stop = derive_eod_stop_price(buy_price)
    emerg_stop = derive_emergency_stop_price(buy_price)
    return f"보유 지속 (종가손절 ${eod_stop:,.2f} / 비상손절 ${emerg_stop:,.2f} / +{TRAILING_ACTIVATE_PCT:.0f}% 트레일링)"


def snapshot_from_indicator_df(df: pd.DataFrame, buy_date: Optional[str] = None) -> Dict[str, float]:
    """Extract kijun / ATR / peak high from an ichimoku-enriched OHLCV frame."""
    if df is None or df.empty:
        return {}
    work = df.copy()
    if "Kijun" not in work.columns or "ATR14" not in work.columns:
        from al_sangmoo.domain.quant.ichimoku import calculate_ichimoku_indicators
        work = calculate_ichimoku_indicators(work)
    last = work.iloc[-1]
    kijun = float(last["Kijun"]) if "Kijun" in work.columns and pd.notna(last.get("Kijun")) else 0.0
    atr = float(last["ATR14"]) if "ATR14" in work.columns and pd.notna(last.get("ATR14")) else 0.0
    last_high = float(last["High"]) if "High" in work.columns and pd.notna(last.get("High")) else 0.0

    peak_from_hist = last_high
    if buy_date and "High" in work.columns:
        try:
            subset = work
            idx = work.index
            ts = pd.Timestamp(buy_date)
            if getattr(idx, "tz", None) is not None:
                ts = ts.tz_localize(idx.tz) if ts.tzinfo is None else ts.tz_convert(idx.tz)
            subset = work[idx >= ts]
            if not subset.empty:
                peak_from_hist = float(subset["High"].max())
        except Exception:
            peak_from_hist = float(work["High"].max()) if "High" in work.columns else last_high

    return {
        "kijun_26": round(kijun, 4) if kijun > 0 else 0.0,
        "atr_14": round(atr, 4) if atr > 0 else 0.0,
        "last_high": round(last_high, 4) if last_high > 0 else 0.0,
        "peak_from_hist": round(peak_from_hist, 4) if peak_from_hist > 0 else 0.0,
    }


def fetch_trailing_snapshot(ticker: str, buy_date: Optional[str] = None) -> Dict[str, float]:
    """Daily OHLCV → kijun/ATR snapshot. Cached 5 minutes. Network failures return {}."""
    key = f"{str(ticker).upper()}|{buy_date or ''}"
    now = time.time()
    cached = _MARKET_CACHE.get(key)
    if cached and (now - cached.get("ts", 0)) < MARKET_CACHE_TTL_SEC:
        return dict(cached.get("data") or {})

    try:
        import yfinance as yf
        from al_sangmoo.domain.quant.ichimoku import calculate_ichimoku_indicators

        raw = yf.download(str(ticker).upper(), period="6mo", interval="1d", progress=False, auto_adjust=False)
        if raw is None or raw.empty:
            return {}
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        ind = calculate_ichimoku_indicators(raw)
        data = snapshot_from_indicator_df(ind, buy_date=buy_date)
        _MARKET_CACHE[key] = {"ts": now, "data": data}
        return dict(data)
    except Exception:
        return {}


def clear_trailing_market_cache() -> None:
    _MARKET_CACHE.clear()
