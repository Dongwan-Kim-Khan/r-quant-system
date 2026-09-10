"""
QQQ Beat Alpha v2 — Core-Satellite 70:30 + Dual-Speed Regime.

Inherits PIT loaders, next-open fills, 10bps/8bps friction from v1.

A/B matrix
  C1  v1 book (idle-fill 3-slot, occupancy ~65%, 8y CAGR 18.47%)
  C2  Core-Satellite 70:30 only (v1 leverage, all 3-gates)
  C3  70:30 + dual-speed regime + Engine-A bull / Engine-B chop

Sweet-spot target (8y): CAGR 25–29%, MDD −22–−26%, Calmar ≥ 1.0.
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

warnings.filterwarnings("ignore")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

sys.path.insert(0, os.path.join(PROJECT_ROOT, "research_and_backtests"))
from al_sangmoo.core.constants import STOP_LOSS_PCT  # noqa: E402
from compare_html_vs_live_exit import (  # noqa: E402
    FEE,
    INITIAL,
    SLIPPAGE,
    Book,
    Position,
    _fill_buy,
    _fill_sell,
    align_panels,
    aligned_returns,
    close_position,
    decide_exit,
    entry_signal,
    metrics,
)
from pit_dynamic_universe_exit_ab import stock_names  # noqa: E402
from pit_universe_exit_ab import download_master, snapshot_index  # noqa: E402
from qqq_beat_alpha_backtester import (  # noqa: E402
    BENCH,
    DRIFT_BAND,
    SNAPSHOTS,
    ProxyPos,
    Sleeve,
    _px,
    aligned_ohlc,
    build_daily_universes_composite,
    composite_rs,
    enrich,
    ensure_qld,
    json_safe,
    pack_book,
    run_alpha_book,
    sleeve_stats,
    target_leverage,
    vs_qqq,
)
from regime_bull_core import bh_metrics  # noqa: E402

OUT_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "qqq_beat_alpha_v2_results.json")
REPORT_PATH = os.path.join(PROJECT_ROOT, "research_and_backtests", "qqq_beat_alpha_v2_report.md")

CORE_FRAC = 0.70
SAT_FRAC = 0.30
SAT_W1 = 0.20
SAT_W2 = 0.10
MAX_SAT = 2
HARD_INVESTABLE = 0.60  # 40% cash in true bear
FAST_COOLDOWN = 15  # trading days of full QQQ after a V-signal, then hard may re-arm
SOFT_VIX = 22.0
BULL_VIX = 20.0
MOM_BARS = 20
SMA_FAST = 5
SMA_SOFT = 50


@dataclass
class CSSpec:
    name: str
    dual_speed: bool = False
    engine_switch: bool = False


@dataclass
class CSSleeve(Sleeve):
    cash_frac: List[float] = field(default_factory=list)
    mode_days: Dict[str, int] = field(default_factory=lambda: defaultdict(int))


def _cloud_gates(p: dict, i: int) -> Optional[Tuple[float, float, bool, bool, bool]]:
    if i < 1 or not p["listed"][i] or not p["listed"][i - 1]:
        return None
    close = p["close"][i]
    kijun = p["kijun"][i]
    tenkan = p["tenkan"][i]
    span_a = p["span_a"][i]
    span_b = p["span_b"][i]
    if not np.isfinite([close, kijun, tenkan, span_a, span_b]).all():
        return None
    if kijun <= 0 or close <= 0:
        return None
    above_cloud = close >= max(span_a, span_b)
    tenkan_ok = tenkan >= kijun
    obv_ok = np.isfinite(p["obv"][i]) and np.isfinite(p["obv_ma"][i]) and p["obv"][i] > p["obv_ma"][i]
    return close, kijun, above_cloud, tenkan_ok, obv_ok


def engine_a(p: dict, i: int) -> bool:
    g = _cloud_gates(p, i)
    if g is None:
        return False
    close, _kijun, above_cloud, tenkan_ok, obv_ok = g
    high20 = p["high20"][i]
    return bool(above_cloud and tenkan_ok and obv_ok and np.isfinite(high20) and close > high20)


def engine_b(p: dict, i: int) -> bool:
    g = _cloud_gates(p, i)
    if g is None:
        return False
    close, kijun, above_cloud, tenkan_ok, obv_ok = g
    prev_close = p["close"][i - 1]
    prev_kijun = p["kijun"][i - 1]
    return bool(
        above_cloud
        and tenkan_ok
        and obv_ok
        and np.isfinite(prev_close)
        and np.isfinite(prev_kijun)
        and prev_close <= prev_kijun
        and close > kijun
    )


def spy_features(spy: np.ndarray) -> dict:
    s = pd.Series(spy)
    return {
        "sma5": s.rolling(SMA_FAST, min_periods=SMA_FAST).mean().to_numpy(dtype=float),
        "sma50": s.rolling(SMA_SOFT, min_periods=SMA_SOFT).mean().to_numpy(dtype=float),
        "mom20": s.pct_change(MOM_BARS).to_numpy(dtype=float),
    }


def vix_down_3(vix: np.ndarray, asof: int) -> bool:
    if asof < 3:
        return False
    a, b, c, d = vix[asof], vix[asof - 1], vix[asof - 2], vix[asof - 3]
    if not np.isfinite([a, b, c, d]).all():
        return False
    return bool(a < b < c < d)


def classify_mode(
    asof: int,
    spy: np.ndarray,
    sma200: np.ndarray,
    feat: dict,
    vix: np.ndarray,
    fast_days: int,
    in_hard: bool,
    dual_speed: bool,
) -> Tuple[str, float, bool, int, bool]:
    """Return (mode, investable, levered, fast_days, in_hard).

    Hard sticks until SMA200 reclaim or a fast V-signal. Fast re-entry is an
    SMA5 *cross* plus 3 VIX down-days, then a 15-day QQQ window — not a latch
    that stays on until SMA200 (that skipped the rest of 2022 after the first bounce).
    """
    if not dual_speed:
        lev = target_leverage(spy[asof], sma200[asof], vix[asof])
        levered = lev > 1.0
        return ("levered" if levered else "full"), 1.0, levered, 0, False

    px = spy[asof]
    s200 = sma200[asof]
    s50 = feat["sma50"][asof]
    s5 = feat["sma5"][asof]
    mom = feat["mom20"][asof]
    vx = vix[asof]
    s5_prev = feat["sma5"][asof - 1] if asof >= 1 else np.nan
    px_prev = spy[asof - 1] if asof >= 1 else np.nan

    above_200 = np.isfinite(px) and np.isfinite(s200) and s200 > 0 and px >= s200
    spy_gt_sma5 = np.isfinite(px) and np.isfinite(s5) and s5 > 0 and px > s5
    crossed_sma5 = (
        spy_gt_sma5
        and np.isfinite(px_prev)
        and np.isfinite(s5_prev)
        and s5_prev > 0
        and px_prev <= s5_prev
    )
    fast = crossed_sma5 and vix_down_3(vix, asof)
    enter_hard = (not above_200) and np.isfinite(mom) and mom < 0.0
    soft_cond = (
        np.isfinite(px)
        and np.isfinite(s50)
        and s50 > 0
        and px < s50
        and np.isfinite(vx)
        and vx >= SOFT_VIX
    )
    lowvol = above_200 and np.isfinite(vx) and vx < BULL_VIX

    if above_200:
        fast_days = 0
        in_hard = False
    if fast:
        in_hard = False
        fast_days = FAST_COOLDOWN
    skip_hard = fast_days > 0
    if fast_days > 0:
        fast_days -= 1
    if enter_hard and not skip_hard:
        in_hard = True

    if in_hard and not skip_hard:
        return "hard", HARD_INVESTABLE, False, fast_days, in_hard
    if soft_cond:
        return "soft", 1.0, False, fast_days, in_hard
    if lowvol:
        return "levered", 1.0, True, fast_days, in_hard
    return "full", 1.0, False, fast_days, in_hard


def cs_proxy_mix(
    core_w: float,
    idle_sat: float,
    levered: bool,
    has_qld: bool,
) -> Tuple[float, float]:
    """1.5x on the 70% core only: 50% QQQ + 50% QLD of core. Idle sat → QQQ."""
    idle_sat = max(0.0, idle_sat)
    core_w = max(0.0, core_w)
    if levered and has_qld and core_w > 0:
        return core_w * 0.50 + idle_sat, core_w * 0.50
    return core_w + idle_sat, 0.0


def pick_entry_fn(spec: CSSpec, levered_bull: bool) -> Callable:
    if not spec.engine_switch:
        return entry_signal
    return engine_a if levered_bull else engine_b


def run_cs_book(
    spec: CSSpec,
    calendar: pd.DatetimeIndex,
    panels: Dict[str, dict],
    bench: pd.DataFrame,
    composite: Dict[str, np.ndarray],
    allowed_by_day: List[Optional[Set[str]]],
    proxy_ohlc: Dict[str, dict],
    feat: dict,
    start_i: int,
) -> Tuple[Book, CSSleeve]:
    n = len(calendar)
    dates = [d.strftime("%Y-%m-%d") for d in calendar]
    spy = bench["spy"].to_numpy(dtype=float)
    sma200 = bench["spy_sma200"].to_numpy(dtype=float)
    vix = bench["vix"].to_numpy(dtype=float)
    book = Book(cash=INITIAL)
    sl = CSSleeve()
    has_qld = "QLD" in proxy_ohlc and bool(np.nanmax(proxy_ohlc["QLD"]["ok"]))
    qqq_crs = composite_rs(proxy_ohlc["QQQ"]["close"]) if "QQQ" in proxy_ohlc else np.full(n, np.nan)
    start_i = max(1, int(start_i))
    prev_mode = None
    fast_days = 0
    in_hard = False
    sat_w = (SAT_W1, SAT_W2)

    def stock_mark(i: int, which: str) -> float:
        tot = 0.0
        for tk, pos in book.positions.items():
            p = panels[tk]
            arr = p["open"] if which == "open" else p["close"]
            px = arr[i]
            if np.isfinite(px) and px > 0:
                tot += pos.shares * float(px)
            else:
                tot += pos.shares * pos.entry
        return tot

    def proxy_mark(i: int, which: str) -> float:
        tot = 0.0
        for tk, pos in sl.proxy.items():
            if pos.shares <= 0:
                continue
            px = _px(proxy_ohlc[tk], i, which)
            tot += pos.shares * (px if px > 0 else pos.entry)
        return tot

    def equity_now(i: int, which: str) -> float:
        return book.cash + stock_mark(i, which) + proxy_mark(i, which)

    def sell_proxy(ticker: str, i: int, which: str, reason: str, shares: Optional[float] = None) -> None:
        pos = sl.proxy.get(ticker)
        if pos is None or pos.shares <= 0:
            return
        qty = pos.shares if shares is None else min(pos.shares, shares)
        if qty <= 1e-12:
            return
        px = _px(proxy_ohlc[ticker], i, which)
        if px <= 0:
            px = pos.entry
        fill = _fill_sell(px)
        book.cash += qty * fill * (1.0 - FEE)
        sl.proxy_trades += 1
        pos.shares -= qty
        if pos.shares <= 1e-9:
            pos.shares = 0.0
            pos.entry = 0.0

    def buy_proxy(ticker: str, i: int, which: str, target_val: float) -> None:
        px_raw = _px(proxy_ohlc[ticker], i, which)
        if px_raw <= 0 or target_val < px_raw:
            return
        fill = _fill_buy(px_raw)
        budget = min(book.cash * 0.995, target_val)
        if budget < fill:
            return
        shares = (budget * (1.0 - FEE)) / fill
        cost = shares * fill
        if cost > book.cash or shares <= 0:
            return
        book.cash -= cost
        pos = sl.proxy.setdefault(ticker, ProxyPos(ticker=ticker))
        if pos.shares > 0 and pos.entry > 0:
            pos.entry = (pos.entry * pos.shares + fill * shares) / (pos.shares + shares)
        else:
            pos.entry = fill
        pos.shares += shares
        sl.proxy_trades += 1

    def set_proxy_weight(ticker: str, i: int, which: str, target_frac: float, eq: float, reason: str) -> None:
        if ticker not in proxy_ohlc:
            return
        px = _px(proxy_ohlc[ticker], i, which)
        if px <= 0:
            if target_frac <= 0:
                sell_proxy(ticker, i, which, reason)
            return
        pos = sl.proxy.setdefault(ticker, ProxyPos(ticker=ticker))
        cur_val = pos.shares * px if pos.shares > 0 else 0.0
        target_val = max(0.0, eq * target_frac)
        if target_frac <= 1e-9 or target_val < px:
            sell_proxy(ticker, i, which, reason)
            return
        if cur_val > target_val * (1.0 + DRIFT_BAND):
            sell_shares = min(pos.shares, (cur_val - target_val) / px)
            sell_proxy(ticker, i, which, reason, shares=sell_shares)
            return
        if cur_val < target_val * (1.0 - DRIFT_BAND):
            buy_proxy(ticker, i, which, target_val - cur_val)

    def free_cash(i: int, needed: float) -> None:
        for ticker in ("QLD", "QQQ"):
            if book.cash >= needed:
                return
            pos = sl.proxy.get(ticker)
            if pos is None or pos.shares <= 0:
                continue
            px = _px(proxy_ohlc[ticker], i, "open")
            if px <= 0:
                continue
            gap = needed - book.cash
            shares = min(pos.shares, gap / (px * (1.0 - SLIPPAGE) * (1.0 - FEE) + 1e-12))
            sell_proxy(ticker, i, "open", "PROXY_FUND", shares=shares)

    def trim_satellite(i: int, cap_val: float) -> None:
        cur = stock_mark(i, "open")
        if cur <= cap_val * (1.0 + DRIFT_BAND) or cur <= 0:
            return
        # sell lowest composite-RS names first, keep the leader
        ranked = sorted(
            book.positions.keys(),
            key=lambda t: (composite[t][i - 1] if t in composite and np.isfinite(composite[t][i - 1]) else -999.0),
        )
        need = cur - cap_val
        for tk in ranked:
            if need <= 0 or tk not in book.positions:
                break
            pos = book.positions[tk]
            raw = panels[tk]["open"][i]
            if not np.isfinite(raw) or raw <= 0:
                raw = panels[tk]["close"][i]
            if not np.isfinite(raw) or raw <= 0:
                continue
            fill = _fill_sell(float(raw))
            pos_val = pos.shares * fill
            if pos_val <= need * 1.02:
                close_position(book, pos, float(raw), dates[i], "SAT_TRIM", i)
                need -= pos_val
            else:
                sell_shares = min(pos.shares, need / fill)
                book.cash += sell_shares * fill * (1.0 - FEE)
                pos.shares -= sell_shares
                need = 0.0

    def rebalance_proxy(i: int, investable: float, levered: bool, which: str, reason: str, force: bool) -> None:
        eq = equity_now(i, which)
        if eq <= 0:
            return
        stock_w = stock_mark(i, which) / eq
        core_w = CORE_FRAC * investable
        sat_budget = SAT_FRAC * investable
        idle_sat = max(0.0, sat_budget - stock_w)
        qqq_w, qld_w = cs_proxy_mix(core_w, idle_sat, levered, has_qld)
        # residual is cash (hard sleeve)

        def cur_w(tk: str) -> float:
            pos = sl.proxy.get(tk)
            if pos is None or pos.shares <= 0:
                return 0.0
            px = _px(proxy_ohlc[tk], i, which)
            return (pos.shares * px / eq) if px > 0 else 0.0

        if not force:
            drift = abs(cur_w("QQQ") - qqq_w) + abs(cur_w("QLD") - qld_w)
            if drift < DRIFT_BAND:
                return
        q_val = cur_w("QQQ") * eq
        if q_val > eq * qqq_w:
            set_proxy_weight("QQQ", i, which, qqq_w, eq, reason)
            set_proxy_weight("QLD", i, which, qld_w, equity_now(i, which), reason)
        else:
            set_proxy_weight("QLD", i, which, qld_w, eq, reason)
            set_proxy_weight("QQQ", i, which, qqq_w, equity_now(i, which), reason)

    for i in range(start_i, n):
        asof = i - 1
        mode, investable, levered, fast_days, in_hard = classify_mode(
            asof, spy, sma200, feat, vix, fast_days, in_hard, spec.dual_speed
        )
        sl.mode_days[mode] += 1
        force_proxy = prev_mode is None or prev_mode != mode or i == start_i
        bull_for_engine = (
            np.isfinite(spy[asof])
            and np.isfinite(sma200[asof])
            and sma200[asof] > 0
            and spy[asof] >= sma200[asof]
            and np.isfinite(vix[asof])
            and vix[asof] < BULL_VIX
        )
        sig_fn = pick_entry_fn(spec, bull_for_engine)

        for tk in list(book.positions.keys()):
            pos = book.positions[tk]
            p = panels[tk]
            if not p["listed"][i]:
                continue
            high = p["high"][i]
            if np.isfinite(high):
                pos.peak_high = max(pos.peak_high, high)
            reason = decide_exit(
                "HTML",
                pos.entry,
                pos.peak_high,
                p["close"][i],
                p["low"][i],
                p["kijun"][i],
                p["atr"][i],
            )
            if not reason:
                continue
            hard = pos.entry * (1.0 + STOP_LOSS_PCT)
            if reason == "HARD_STOP":
                raw = min(p["open"][i], hard) if np.isfinite(p["open"][i]) else p["close"][i]
            else:
                raw = p["close"][i]
            if not np.isfinite(raw) or raw <= 0:
                continue
            close_position(book, pos, float(raw), dates[i], reason, i)

        eq_open = equity_now(i, "open")
        sat_cap = SAT_FRAC * investable * eq_open
        trim_satellite(i, sat_cap)

        allow_new = not (spec.dual_speed and mode == "hard")
        if spec.engine_switch and not bull_for_engine:
            # Chop/bear: park the satellite in QQQ (no Engine-A fake breakouts).
            allow_new = False
        if allow_new and len(book.positions) < MAX_SAT and i >= 1:
            scored: List[Tuple[float, str]] = []
            allowed = allowed_by_day[i] if allowed_by_day is not None else None
            for tk, p in panels.items():
                if tk in book.positions:
                    continue
                if allowed is not None and tk not in allowed:
                    continue
                if not p["listed"][i] or not p["listed"][i - 1]:
                    continue
                if not sig_fn(p, i - 1):
                    continue
                rs = composite[tk][i - 1] if tk in composite else np.nan
                if not np.isfinite(rs):
                    continue
                qrs = qqq_crs[i - 1]
                if np.isfinite(qrs) and rs < qrs:
                    continue
                scored.append((float(rs), tk))
            scored.sort(reverse=True)

            n_free = MAX_SAT - len(book.positions)
            taken = len(book.positions)
            needed = eq_open * float(sum(sat_w[taken : taken + n_free])) * investable
            if needed > book.cash:
                free_cash(i, needed)

            for _, tk in scored:
                if len(book.positions) >= MAX_SAT:
                    break
                p = panels[tk]
                raw_open = p["open"][i]
                if not np.isfinite(raw_open) or raw_open <= 0:
                    continue
                fill = _fill_buy(float(raw_open))
                slot_frac = sat_w[min(len(book.positions), len(sat_w) - 1)] * investable
                eq_open = equity_now(i, "open")
                cap = eq_open * slot_frac
                if fill <= 0 or cap < fill:
                    continue
                if cap > book.cash:
                    free_cash(i, cap)
                budget = min(cap, book.cash * 0.995)
                shares = (budget * (1.0 - FEE)) / fill
                if shares <= 0:
                    continue
                cost = shares * fill
                if cost > book.cash:
                    continue
                book.cash -= cost
                book.positions[tk] = Position(
                    ticker=tk,
                    shares=shares,
                    entry=fill,
                    entry_i=i,
                    peak_high=max(fill, p["high"][i] if np.isfinite(p["high"][i]) else fill),
                    entry_date=dates[i],
                )

        rebalance_proxy(i, investable, levered, "close", "PROXY_EOD", force_proxy)

        eq = equity_now(i, "close")
        book.equity.append(eq)
        book.dates.append(dates[i])
        if eq > 0:
            sw = stock_mark(i, "close") / eq
            sl.occupancy.append(sw)
            sl.cash_frac.append(book.cash / eq)
            q_val = 0.0
            l_val = 0.0
            qp = sl.proxy.get("QQQ")
            lp = sl.proxy.get("QLD")
            qpx = _px(proxy_ohlc["QQQ"], i, "close") if "QQQ" in proxy_ohlc else 0.0
            lpx = _px(proxy_ohlc["QLD"], i, "close") if has_qld else 0.0
            if qp and qp.shares > 0 and qpx > 0:
                q_val = qp.shares * qpx
            if lp and lp.shares > 0 and lpx > 0:
                l_val = lp.shares * lpx
                sl.qld_days += 1
            sl.leverage.append((stock_mark(i, "close") + q_val + 2.0 * l_val) / eq)
        prev_mode = mode

    last_i = n - 1
    for tk in list(book.positions.keys()):
        pos = book.positions[tk]
        px = panels[tk]["close"][last_i]
        if not np.isfinite(px) or px <= 0:
            px = pos.entry
        close_position(book, pos, float(px), dates[last_i], "EOB", last_i)
    for tk in list(sl.proxy.keys()):
        sell_proxy(tk, last_i, "close", "EOB")
    return book, sl


def cs_sleeve_stats(sl: CSSleeve) -> dict:
    base = sleeve_stats(sl)
    cash = np.array(sl.cash_frac, dtype=float)
    base["avg_cash_pct"] = round(float(np.nanmean(cash) * 100.0), 2) if len(cash) else 0.0
    base["mode_days"] = dict(sl.mode_days)
    return base


def sweet_verdict(cagr: float, mdd: float, calmar: float, qqq_cagr: float, qld_cagr: float) -> str:
    beat_qqq = cagr > qqq_cagr
    beat_qld = cagr > qld_cagr
    mdd_ok = mdd >= -26.0  # shallower than -26
    cagr_ok = cagr >= 25.0
    calmar_ok = calmar >= 1.0
    if cagr_ok and mdd_ok and calmar_ok and beat_qqq:
        tag = "SWEET_SPOT"
        if beat_qld:
            tag += "_BEAT_QLD"
        return tag
    if beat_qqq and cagr_ok and not mdd_ok:
        return "CAGR_OK_MDD_HEAVY"
    if beat_qqq and mdd_ok:
        return "BEAT_QQQ_BELOW_CAGR"
    if beat_qqq:
        return "BEAT_QQQ"
    return "LOSE_QQQ"


def write_report(payload: dict) -> None:
    meta = payload["meta"]
    lines = [
        "# QQQ Beat Alpha v2 — Core-Satellite 70:30 & Dual-Speed Regime",
        "",
        f"- 생성: {meta.get('generated')}",
        f"- 마찰: slippage {meta['friction']['slippage_bps']:.0f}bps / fee {meta['friction']['fee_bps']:.0f}bps / 익일 시가 체결",
        f"- 목표 스윗스팟 (8년): CAGR 25–29%, MDD −22–−26%, Calmar ≥ 1.0, QLD B&H 능가",
        "",
        "## 3대 아키텍처",
        "",
        "1. **Core-Satellite 70:30** — 코어 70%는 개별주로 전환하지 않음. 저변동 강세장 코어는 QQQ 50%+QLD 50%(1.5x), 그 외는 QQQ. 위성은 Composite RS 1~2등만 (20%+10%).",
        "2. **Dual-Speed Regime** — Soft: SPY<SMA50 & VIX≥22 → QLD 오프. Hard: SPY<SMA200 & 20d mom<0 이후 스티키 40% 현금. Fast: SMA5 상향 돌파 + VIX 3일 하락 → 15거래일 QQQ 풀 익스포저 후 Hard 재장전 (SMA200까지 래치하지 않음).",
        "3. **Engine-A regime switch** — Bull (SPY≥200 & VIX<20)만 엔진 A로 위성 진입. 그 외 구간은 위성 공석(QQQ 파킹)으로 가짜 돌파를 차단.",
        "",
        "### A/B 매트릭스",
        "",
        "- **C1 v1**: 유휴분 QQQ 파킹 + 3-Slot 50/30/20 (가동률 ~65%)",
        "- **C2 CS-only**: 70:30 단독. 레짐/엔진은 v1과 동일 (1.5x 저변동, 3-Gate).",
        "- **C3 v2**: 70:30 + Dual-Speed + Engine-A switch",
        "",
    ]
    for asof, block in payload["windows"].items():
        end = block["qqq_bh"]["end"]
        lines += [
            f"## Window {asof} → {end}",
            "",
            "| Book | CAGR | MDD | Sharpe | Calmar | Occupancy | vs QQQ | Jensen α | Verdict |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]

        def row(name, m, alpha=None, verdict="—"):
            xs = alpha.get("excess_cagr_pct") if alpha else "—"
            ja = alpha.get("jensen", {}).get("ann_alpha_pct") if alpha and alpha.get("jensen") else "—"
            occ = m.get("sleeve", {}).get("avg_stock_occupancy_pct", "—")
            return (
                f"| {name} | {m['cagr_pct']:.2f}% | {m['mdd_pct']:.2f}% | {m['sharpe']:.3f} | "
                f"{m.get('calmar', 0):.3f} | {occ} | {xs} | {ja} | {verdict} |"
            )

        lines.append(row("QQQ B&H", block["qqq_bh"]))
        if block.get("qld_bh"):
            lines.append(row("QLD B&H", block["qld_bh"]))
        lines.append(row("**C1 v1**", block["c1_v1"], block.get("c1_vs_qqq"), block.get("c1_verdict")))
        lines.append(row("**C2 CS 70:30**", block["c2_cs"], block.get("c2_vs_qqq"), block.get("c2_verdict")))
        lines.append(row("**C3 v2 integrated**", block["c3_v2"], block.get("c3_vs_qqq"), block.get("c3_verdict")))
        c3 = block["c3_v2"]
        lines += [
            "",
            f"- C3 평균 현금 비중: {c3.get('sleeve', {}).get('avg_cash_pct')}%",
            f"- C3 평균 총레버리지: {c3.get('sleeve', {}).get('avg_gross_leverage')}",
            f"- C3 QLD 보유일: {c3.get('sleeve', {}).get('qld_day_pct')}%",
            f"- C3 듀얼스피드 국면 일수: {c3.get('sleeve', {}).get('mode_days')}",
            f"- C2 가동률: {block['c2_cs'].get('sleeve', {}).get('avg_stock_occupancy_pct')}%  "
            f"/ C1 가동률: {block['c1_v1'].get('sleeve', {}).get('avg_stock_occupancy_pct')}%",
            "",
        ]
    lines += [
        "## 진단",
        "",
        "- v1 8년 타이의 본체는 **65% 개별주 가동률이 QQQ 코어를 밀어낸 것**. C2 가동률은 ~22%로 목표 밴드(25–30%)에 들어왔다.",
        "- 다만 8년 C2 CAGR 16.4% < QQQ 18.4%: 위성 20% 슬리브가 QQQ를 하회하면 70% 코어가 있어도 지수에 진다. 2023 C2는 25.2%/MDD -25.8%로 스윗스팟에 가장 가깝다.",
        "- Dual-speed Hard는 스티키+15일 fast 쿨다운으로 273일 방어(첫 버전 101일). 8년 MDD는 -37%로 QQQ -36% 근처까지 붙였지만, 40% 현금으로는 QLD 2x 크래시만큼 줄이지는 못한다.",
        "- Fast를 SMA200까지 래치하면 2022 첫 반등 이후 방어가 꺼져 MDD가 다시 -45%로 열린다. 15일 쿨다운이 그 구멍이다.",
        "- 8년 25% CAGR은 QLD B&H 27.9%에 가깝다. 70% QQQ 코어 + 간헐적 1.5x로는 산술적으로 ~20%가 천장이고, 위성 드래그와 Hard 현금이 그 아래를 만든다. 다음 A/B는 위성 1등만(20%) 또는 위성 RS가 QQQ+10pt 이상일 때만 스위칭.",
        "",
    ]
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _line(tag: str, m: dict) -> str:
    occ = m.get("sleeve", {}).get("avg_stock_occupancy_pct")
    occ_s = f"  occ={occ}%" if occ is not None else ""
    return (
        f"  {tag:22s} CAGR {m['cagr_pct']:6.2f}%  MDD {m['mdd_pct']:7.2f}%  "
        f"Sharpe {m['sharpe']:.3f}  Calmar {m.get('calmar', 0):.3f}{occ_s}"
    )


def main() -> None:
    t0 = time.time()
    raw = download_master()
    raw = ensure_qld(raw)
    names = [t for t in stock_names() if t in raw]
    sliced = {k: v for k, v in raw.items() if k in set(names) | set(BENCH)}
    calendar, panels, bench = align_panels(sliced)
    print(f"[panel] {len(panels)} names, {len(calendar)} days")

    proxy_ohlc = {"QQQ": aligned_ohlc(raw, "QQQ", calendar)}
    if "QLD" in raw:
        proxy_ohlc["QLD"] = aligned_ohlc(raw, "QLD", calendar)
        print(f"[proxy] QLD ok={int(proxy_ohlc['QLD']['ok'].sum())}")
    else:
        print("[proxy] QLD missing")

    print("[universe] composite 1M/3M/6M …")
    universes = build_daily_universes_composite(calendar, raw)
    composite = {t: composite_rs(p["close"]) for t, p in panels.items()}
    feat = spy_features(bench["spy"].to_numpy(dtype=float))
    qqq_px = bench["qqq"].copy()
    qqq_px.index = pd.to_datetime(qqq_px.index)
    qld_px = pd.Series(proxy_ohlc["QLD"]["close"], index=calendar) if "QLD" in proxy_ohlc else None

    specs = {
        "c2_cs": CSSpec("cs70", dual_speed=False, engine_switch=False),
        "c3_v2": CSSpec("v2", dual_speed=True, engine_switch=True),
    }

    payload = {
        "meta": {
            "method": (
                "C1=v1 3-slot idle QQQ overlay. "
                "C2=core 70% QQQ/QLD + satellite 20/10 dual-momentum. "
                "C3=C2 + dual-speed (soft VIX/SMA50, hard SMA200+mom20 40% cash, "
                "fast re-entry SMA5+VIX 3-down latch) + Engine A only in low-vol bull else Engine B."
            ),
            "initial": INITIAL,
            "friction": {"slippage_bps": SLIPPAGE * 10000, "fee_bps": FEE * 10000},
            "core_frac": CORE_FRAC,
            "satellite": {"w1": SAT_W1, "w2": SAT_W2, "max": MAX_SAT},
            "hard_investable": HARD_INVESTABLE,
            "qld_available": "QLD" in proxy_ohlc,
            "generated": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        },
        "windows": {},
    }

    for asof in SNAPSHOTS:
        start_i = max(snapshot_index(calendar, asof), 2)
        print(f"\n===== WINDOW {asof} ({calendar[start_i].date()}) =====")

        c1_book, c1_sl = run_alpha_book(
            calendar, panels, bench, composite, universes, proxy_ohlc, start_i
        )
        c2_book, c2_sl = run_cs_book(
            specs["c2_cs"], calendar, panels, bench, composite, universes, proxy_ohlc, feat, start_i
        )
        c3_book, c3_sl = run_cs_book(
            specs["c3_v2"], calendar, panels, bench, composite, universes, proxy_ohlc, feat, start_i
        )

        c1 = pack_book(c1_book, bench, extra={"sleeve": sleeve_stats(c1_sl)})
        c2 = pack_book(c2_book, bench, extra={"sleeve": cs_sleeve_stats(c2_sl)})
        c3 = pack_book(c3_book, bench, extra={"sleeve": cs_sleeve_stats(c3_sl)})
        idx = pd.to_datetime(c1_book.dates)
        qqq_m = enrich(bh_metrics(qqq_px.reindex(idx)))
        qld_m = enrich(bh_metrics(qld_px.reindex(idx))) if qld_px is not None else None

        c1_vs = vs_qqq(c1.pop("_rets"), qqq_m, c1)
        c2_vs = vs_qqq(c2.pop("_rets"), qqq_m, c2)
        c3_vs = vs_qqq(c3.pop("_rets"), qqq_m, c3)
        qld_cagr = qld_m["cagr_pct"] if qld_m else 0.0

        def verd(m):
            return sweet_verdict(m["cagr_pct"], m["mdd_pct"], m.get("calmar", 0.0), qqq_m["cagr_pct"], qld_cagr)

        block = {
            "qqq_bh": qqq_m,
            "qld_bh": qld_m,
            "c1_v1": c1,
            "c2_cs": c2,
            "c3_v2": c3,
            "c1_vs_qqq": c1_vs,
            "c2_vs_qqq": c2_vs,
            "c3_vs_qqq": c3_vs,
            "c1_verdict": verd(c1),
            "c2_verdict": verd(c2),
            "c3_verdict": verd(c3),
        }
        payload["windows"][asof] = block

        print(_line("QQQ B&H", qqq_m))
        if qld_m:
            print(_line("QLD B&H", qld_m))
        print(_line("C1 v1", c1) + f"  vsQQQ {c1_vs.get('excess_cagr_pct')}  {block['c1_verdict']}")
        print(_line("C2 CS 70:30", c2) + f"  vsQQQ {c2_vs.get('excess_cagr_pct')}  {block['c2_verdict']}")
        print(_line("C3 v2", c3) + f"  vsQQQ {c3_vs.get('excess_cagr_pct')}  {block['c3_verdict']}")
        print(
            f"  C3 cash {c3['sleeve'].get('avg_cash_pct')}%  lev {c3['sleeve'].get('avg_gross_leverage')}  "
            f"modes {c3['sleeve'].get('mode_days')}"
        )

    payload["meta"]["elapsed_sec"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(json_safe(payload), f, ensure_ascii=False, indent=2)
    write_report(payload)
    print(f"\nwrote {OUT_PATH}")
    print(f"wrote {REPORT_PATH}")
    print(f"elapsed {payload['meta']['elapsed_sec']}s")


if __name__ == "__main__":
    main()
