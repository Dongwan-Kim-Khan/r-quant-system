"""
Al-Sangmoo v2 uncapped trailing-stop SSOT.

Constitution:
  - Hard stop: entry -4.0%
  - Kijun close breakdown: 26-day baseline exit
  - After peak gain >= +15%: trailing floor = max(Kijun-26, peak_high - 2.5 * ATR(14))
  - Full exit only (no 50% partial take-profit)
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

import pandas as pd

from al_sangmoo.core.constants import HARD_STOP_PCT, TRAILING_ACTIVATE_PCT, derive_stop_price

ATR_TRAIL_MULT = 2.5
MARKET_CACHE_TTL_SEC = 300.0

_MARKET_CACHE: Dict[str, Dict[str, Any]] = {}


def compute_trailing_floor(kijun_26: float, peak_high: float, atr_14: float) -> float:
    """max(26-day kijun, peak high - 2.5 * ATR(14)). Returns 0.0 if both legs are unusable."""
    candidates = []
    kijun = float(kijun_26 or 0.0)
    if kijun > 0:
        candidates.append(kijun)

    peak = float(peak_high or 0.0)
    atr = float(atr_14 or 0.0)
    if peak > 0 and atr > 0:
        atr_leg = peak - ATR_TRAIL_MULT * atr
        if atr_leg > 0:
            candidates.append(atr_leg)

    if not candidates:
        return 0.0
    return round(max(candidates), 2)


def latch_peak_gain(buy_price: float, current_price: float, peak_high: float = 0.0, max_gain_pct: float = 0.0) -> Dict[str, float]:
    """Running peak price / gain since entry. Trailing stays armed after +15% even if PnL pulls back."""
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
) -> Dict[str, Any]:
    """
    Decide a full-exit action (or HOLD) for one position.

    Priority:
      1. Hard stop (STOP_LOSS_PCT / derive_stop_price)
      2. Uncapped trailing after TRAILING_ACTIVATE_PCT peak gain
      3. 26-day kijun close breakdown
    """
    buy = float(buy_price or 0.0)
    cur = float(current_price or 0.0)
    if buy <= 0 or cur <= 0:
        return {"action": None, "reason": "invalid_price", "trailing_active": False, "trailing_floor": 0.0}

    latched = latch_peak_gain(buy, cur, peak_high, max_gain_pct)
    hard_stop_price = derive_stop_price(buy)
    pnl_pct = latched["pnl_pct"]
    trailing_active = latched["trailing_active"]
    kijun = float(kijun_26 or 0.0)
    floor = compute_trailing_floor(kijun, latched["peak_high"], atr_14) if trailing_active else 0.0

    result = {
        "action": None,
        "reason": None,
        "pnl_pct": pnl_pct,
        "hard_stop_price": hard_stop_price,
        "trailing_active": trailing_active,
        "trailing_floor": floor,
        "peak_high": latched["peak_high"],
        "max_gain_pct": latched["max_gain_pct"],
        "kijun_26": round(kijun, 2) if kijun > 0 else 0.0,
        "atr_14": round(float(atr_14 or 0.0), 4),
        "is_full_exit": True,
    }

    if pnl_pct <= -HARD_STOP_PCT or cur <= hard_stop_price:
        result["action"] = "AUTO_STOP_LOSS"
        result["reason"] = (
            f"[가디언 칼손절] 손절선(-{HARD_STOP_PCT:.1f}% / ${hard_stop_price:,.2f}) 도달 "
            f"(수익률: {pnl_pct:+.2f}%, 현재가: ${cur:,.2f})"
        )
        return result

    if trailing_active:
        if floor > 0 and cur < floor:
            result["action"] = "AUTO_TRAILING_TP"
            result["reason"] = (
                f"[가디언 트레일링 익절] floor=${floor:,.2f} "
                f"(max(기준선 ${kijun:,.2f}, 고점 ${latched['peak_high']:,.2f} - {ATR_TRAIL_MULT}*ATR)) "
                f"이탈 (수익률: {pnl_pct:+.2f}%, 현재가: ${cur:,.2f})"
            )
        return result

    if kijun > 0 and cur < kijun:
        result["action"] = "AUTO_KIJUN_EXIT"
        result["reason"] = (
            f"[가디언 기준선 이탈] 26일 기준선(${kijun:,.2f}) 종가 하회 "
            f"(수익률: {pnl_pct:+.2f}%)"
        )
        return result

    return result


def format_holding_advice(eval_result: Dict[str, Any], buy_price: float) -> str:
    """Human-readable exit_advice for the portfolio row (no 50% partial language)."""
    action = eval_result.get("action")
    pnl = float(eval_result.get("pnl_pct") or 0.0)
    if action == "AUTO_STOP_LOSS":
        return f"칼손절 긴급 매도 권고 (손절선 이탈 {pnl:+.2f}%)"
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
    hard = derive_stop_price(buy_price)
    return f"보유 지속 (손절선 ${hard:,.2f} / +{TRAILING_ACTIVATE_PCT:.0f}% 이후 무제한 트레일링)"


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
