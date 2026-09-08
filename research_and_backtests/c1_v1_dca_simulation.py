"""
C1 v1 DCA simulation — $8,000 seed + $400 on each month's first session.

Same friction as C1: next-open, 10bps slippage, 8bps fee, PIT composite-60.

Books (all under identical cash-flow schedule):
  SPY / QQQ / QLD   buy-and-hold DCA
  C1 v1             3-slot 50/30/20, bear 25/25, stop -4%, idle QQQ + 1.5x
  M2                bear 2×40% + stop -5% (lump-sum 8y 22% variant)

Deposits land as cash before that day's exits/entries/proxy rebalance, so
idle capital parks into QQQ/QLD immediately when slots are full.
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

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
from compare_html_vs_live_exit import (  # noqa: E402
    FEE,
    SLIPPAGE,
    Book,
    Position,
    _fill_buy,
    _fill_sell,
    align_panels,
    close_position,
    entry_signal,
)
from pit_dynamic_universe_exit_ab import stock_names  # noqa: E402
from pit_universe_exit_ab import download_master, snapshot_index  # noqa: E402
from qqq_beat_alpha_backtester import (  # noqa: E402
    BENCH,
    CONVICTION,
    DRIFT_BAND,
    MAX_SLOTS,
    SNAPSHOTS,
    ProxyPos,
    _px,
    aligned_ohlc,
    build_daily_universes_composite,
    composite_rs,
    ensure_qld,
    json_safe,
    proxy_mix,
    target_leverage,
)
from c1_v1_tuning import TuneSpec, TuneSleeve, decide_exit_tune  # noqa: E402

OUT_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "c1_v1_dca_results.json")
REPORT_PATH = os.path.join(PROJECT_ROOT, "research_and_backtests", "c1_v1_dca_report.md")

SEED = 8_000.0
MONTHLY = 400.0

SPECS = {
    "C1_v1": TuneSpec("C1_v1"),  # bear 25/25, stop -4%
    "M2": TuneSpec("M2", bear_slots=2, bear_weights=(0.40, 0.40), stop_pct=-0.05),
}


@dataclass
class FlowLedger:
    dates: List[str] = field(default_factory=list)
    amounts: List[float] = field(default_factory=list)  # negative = deposit
    invested: List[float] = field(default_factory=list)  # cumulative capital in
    equity: List[float] = field(default_factory=list)


def is_month_first(calendar: pd.DatetimeIndex, i: int, start_i: int) -> bool:
    if i == start_i:
        return False  # seed already counted at T0
    if i <= 0:
        return True
    return calendar[i].month != calendar[i - 1].month or calendar[i].year != calendar[i - 1].year


def xirr(dates: List[pd.Timestamp], cashflows: List[float], guess: float = 0.1) -> Optional[float]:
    """Annualized IRR for irregular cash flows (Newton + bisection fallback)."""
    if len(dates) != len(cashflows) or len(dates) < 2:
        return None
    t0 = dates[0]
    years = np.array([(d - t0).days / 365.25 for d in dates], dtype=float)
    cf = np.array(cashflows, dtype=float)
    if not (np.any(cf < 0) and np.any(cf > 0)):
        return None

    def npv(r: float) -> float:
        return float(np.sum(cf / np.power(1.0 + r, years)))

    def dnpv(r: float) -> float:
        return float(np.sum(-years * cf / np.power(1.0 + r, years + 1.0)))

    r = guess
    for _ in range(64):
        f = npv(r)
        df = dnpv(r)
        if abs(df) < 1e-12:
            break
        r_next = r - f / df
        if not np.isfinite(r_next) or r_next <= -0.999999:
            break
        if abs(r_next - r) < 1e-10:
            r = r_next
            break
        r = r_next
    if np.isfinite(r) and abs(npv(r)) < 1e-4:
        return float(r)

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return float(r) if np.isfinite(r) else None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        f_mid = npv(mid)
        if abs(f_mid) < 1e-8 or (hi - lo) < 1e-12:
            return float(mid)
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return float(0.5 * (lo + hi))


def mdd_pct(equity: List[float]) -> float:
    eq = np.array(equity, dtype=float)
    if len(eq) < 2:
        return 0.0
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / np.maximum(peak, 1e-12)
    return float(dd.min() * 100.0)


def invested_relative_mdd(equity: List[float], invested: List[float]) -> float:
    """Drawdown of equity / cum_invested (DCA-aware wealth ratio)."""
    eq = np.array(equity, dtype=float)
    inv = np.array(invested, dtype=float)
    ratio = eq / np.maximum(inv, 1e-12)
    peak = np.maximum.accumulate(ratio)
    dd = (ratio - peak) / np.maximum(peak, 1e-12)
    return float(dd.min() * 100.0)


def pack_dca(ledger: FlowLedger, label: str, end_date: str) -> dict:
    final = float(ledger.equity[-1]) if ledger.equity else 0.0
    total_in = float(ledger.invested[-1]) if ledger.invested else 0.0
    profit = final - total_in
    roi = (profit / total_in * 100.0) if total_in > 0 else 0.0
    x_dates = [pd.Timestamp(d) for d in ledger.dates]
    x_amts = list(ledger.amounts)
    x_dates.append(pd.Timestamp(end_date))
    x_amts.append(final)
    irr = xirr(x_dates, x_amts)
    return {
        "label": label,
        "total_invested": round(total_in, 2),
        "final_equity": round(final, 2),
        "net_profit": round(profit, 2),
        "roi_pct": round(roi, 2),
        "xirr_pct": round(irr * 100.0, 2) if irr is not None else None,
        "dca_mdd_pct": round(mdd_pct(ledger.equity), 2),
        "invested_ratio_mdd_pct": round(invested_relative_mdd(ledger.equity, ledger.invested), 2),
        "n_deposits": int(sum(1 for a in ledger.amounts if a < 0)),
        "n_days": len(ledger.equity),
        "end": end_date,
    }


def run_bh_dca(
    ticker: str,
    calendar: pd.DatetimeIndex,
    ohlc: dict,
    start_i: int,
    seed: float = SEED,
    monthly: float = MONTHLY,
) -> FlowLedger:
    n = len(calendar)
    dates = [d.strftime("%Y-%m-%d") for d in calendar]
    cash = float(seed)
    shares = 0.0
    entry = 0.0
    ledger = FlowLedger()
    ledger.dates.append(dates[start_i])
    ledger.amounts.append(-seed)
    invested = seed
    start_i = max(1, int(start_i))

    def px(i: int, which: str) -> float:
        arr = ohlc["open"] if which == "open" else ohlc["close"]
        p = arr[i] if i < len(arr) else np.nan
        if np.isfinite(p) and p > 0 and ohlc["ok"][i]:
            return float(p)
        alt = ohlc["close"][i]
        return float(alt) if np.isfinite(alt) and alt > 0 else 0.0

    def buy_all(i: int, which: str) -> None:
        nonlocal cash, shares, entry
        raw = px(i, which)
        if raw <= 0 or cash < raw:
            return
        fill = _fill_buy(raw)
        budget = cash * 0.995
        if budget < fill:
            return
        qty = (budget * (1.0 - FEE)) / fill
        cost = qty * fill
        if cost > cash or qty <= 0:
            return
        cash -= cost
        if shares > 0 and entry > 0:
            entry = (entry * shares + fill * qty) / (shares + qty)
        else:
            entry = fill
        shares += qty

    # Seed deploy at start open
    buy_all(start_i, "open")

    for i in range(start_i, n):
        if is_month_first(calendar, i, start_i):
            cash += monthly
            invested += monthly
            ledger.dates.append(dates[i])
            ledger.amounts.append(-monthly)
            buy_all(i, "open")
        elif i == start_i and shares <= 0:
            buy_all(i, "open")

        mark = shares * (px(i, "close") or entry)
        eq = cash + mark
        ledger.equity.append(eq)
        ledger.invested.append(invested)

    return ledger


def run_c1_dca(
    spec: TuneSpec,
    calendar: pd.DatetimeIndex,
    panels: Dict[str, dict],
    bench: pd.DataFrame,
    composite: Dict[str, np.ndarray],
    allowed_by_day: List[Optional[Set[str]]],
    proxy_ohlc: Dict[str, dict],
    start_i: int,
    seed: float = SEED,
    monthly: float = MONTHLY,
) -> Tuple[Book, TuneSleeve, FlowLedger]:
    n = len(calendar)
    dates = [d.strftime("%Y-%m-%d") for d in calendar]
    spy = bench["spy"].to_numpy(dtype=float)
    sma200 = bench["spy_sma200"].to_numpy(dtype=float)
    vix = bench["vix"].to_numpy(dtype=float)
    book = Book(cash=float(seed))
    sl = TuneSleeve()
    has_qld = "QLD" in proxy_ohlc and bool(np.nanmax(proxy_ohlc["QLD"]["ok"]))
    qqq_crs = composite_rs(proxy_ohlc["QQQ"]["close"]) if "QQQ" in proxy_ohlc else np.full(n, np.nan)
    start_i = max(1, int(start_i))
    prev_lev = None
    ledger = FlowLedger()
    ledger.dates.append(dates[start_i])
    ledger.amounts.append(-seed)
    invested = float(seed)

    def stock_mark(i: int, which: str) -> float:
        tot = 0.0
        for tk, pos in book.positions.items():
            p = panels[tk]
            arr = p["open"] if which == "open" else p["close"]
            pxv = arr[i]
            tot += pos.shares * (float(pxv) if np.isfinite(pxv) and pxv > 0 else pos.entry)
        return tot

    def proxy_mark(i: int, which: str) -> float:
        tot = 0.0
        for tk, pos in sl.proxy.items():
            if pos.shares <= 0:
                continue
            pxv = _px(proxy_ohlc[tk], i, which)
            tot += pos.shares * (pxv if pxv > 0 else pos.entry)
        return tot

    def equity_now(i: int, which: str) -> float:
        return book.cash + stock_mark(i, which) + proxy_mark(i, which)

    def sell_proxy(ticker: str, i: int, which: str, shares: Optional[float] = None) -> None:
        pos = sl.proxy.get(ticker)
        if pos is None or pos.shares <= 0:
            return
        qty = pos.shares if shares is None else min(pos.shares, shares)
        if qty <= 1e-12:
            return
        pxv = _px(proxy_ohlc[ticker], i, which)
        if pxv <= 0:
            pxv = pos.entry
        fill = _fill_sell(pxv)
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
        sh = (budget * (1.0 - FEE)) / fill
        cost = sh * fill
        if cost > book.cash or sh <= 0:
            return
        book.cash -= cost
        pos = sl.proxy.setdefault(ticker, ProxyPos(ticker=ticker))
        if pos.shares > 0 and pos.entry > 0:
            pos.entry = (pos.entry * pos.shares + fill * sh) / (pos.shares + sh)
        else:
            pos.entry = fill
        pos.shares += sh
        sl.proxy_trades += 1

    def set_proxy_weight(ticker: str, i: int, which: str, target_frac: float, eq: float) -> None:
        if ticker not in proxy_ohlc:
            return
        pxv = _px(proxy_ohlc[ticker], i, which)
        if pxv <= 0:
            if target_frac <= 0:
                sell_proxy(ticker, i, which)
            return
        pos = sl.proxy.setdefault(ticker, ProxyPos(ticker=ticker))
        cur_val = pos.shares * pxv if pos.shares > 0 else 0.0
        target_val = max(0.0, eq * target_frac)
        if target_frac <= 1e-9 or target_val < pxv:
            sell_proxy(ticker, i, which)
            return
        if cur_val > target_val * (1.0 + DRIFT_BAND):
            sell_proxy(ticker, i, which, shares=min(pos.shares, (cur_val - target_val) / pxv))
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
            pxv = _px(proxy_ohlc[ticker], i, "open")
            if pxv <= 0:
                continue
            gap = needed - book.cash
            sh = min(pos.shares, gap / (pxv * (1.0 - SLIPPAGE) * (1.0 - FEE) + 1e-12))
            sell_proxy(ticker, i, "open", shares=sh)

    def rebalance_proxy(i: int, lev: float, which: str, force: bool = False) -> None:
        eq = equity_now(i, which)
        if eq <= 0:
            return
        stock_w = stock_mark(i, which) / eq
        qqq_w, qld_w = proxy_mix(stock_w, lev, has_qld)
        if not force:
            def cur_w(tk: str) -> float:
                pos = sl.proxy.get(tk)
                if pos is None or pos.shares <= 0:
                    return 0.0
                pxv = _px(proxy_ohlc[tk], i, which)
                return (pos.shares * pxv / eq) if pxv > 0 else 0.0

            if abs(cur_w("QQQ") - qqq_w) + abs(cur_w("QLD") - qld_w) < DRIFT_BAND:
                return
        cur_qqq = sl.proxy.get("QQQ")
        px_q = _px(proxy_ohlc["QQQ"], i, which) if "QQQ" in proxy_ohlc else 0.0
        q_val = cur_qqq.shares * px_q if cur_qqq and px_q > 0 else 0.0
        if q_val > eq * qqq_w:
            set_proxy_weight("QQQ", i, which, qqq_w, eq)
            set_proxy_weight("QLD", i, which, qld_w, equity_now(i, which))
        else:
            set_proxy_weight("QLD", i, which, qld_w, eq)
            set_proxy_weight("QQQ", i, which, qqq_w, equity_now(i, which))

    for i in range(start_i, n):
        # --- DCA inject before any trading ---
        if is_month_first(calendar, i, start_i):
            book.cash += monthly
            invested += monthly
            ledger.dates.append(dates[i])
            ledger.amounts.append(-monthly)

        asof = i - 1
        lev = target_leverage(spy[asof], sma200[asof], vix[asof])
        is_bear = (
            np.isfinite(spy[asof])
            and np.isfinite(sma200[asof])
            and sma200[asof] > 0
            and spy[asof] < sma200[asof]
        )
        if is_bear:
            max_slots = int(spec.bear_slots)
            slot_w = tuple(spec.bear_weights)
            regime = f"bear_{max_slots}slot"
        else:
            max_slots = MAX_SLOTS
            slot_w = CONVICTION
            regime = "bull_lowvol" if lev > 1.0 else "bull"
        sl.regime_days[regime] += 1
        force_proxy = prev_lev is None or abs((prev_lev or 0.0) - lev) > 1e-9 or i == start_i
        # Force proxy rebalance on deposit days so new cash parks into QQQ/QLD
        if is_month_first(calendar, i, start_i):
            force_proxy = True

        for tk in list(book.positions.keys()):
            pos = book.positions[tk]
            p = panels[tk]
            if not p["listed"][i]:
                continue
            high = p["high"][i]
            if np.isfinite(high):
                pos.peak_high = max(pos.peak_high, high)
            reason = decide_exit_tune(
                pos.entry,
                pos.peak_high,
                p["close"][i],
                p["low"][i],
                p["kijun"][i],
                p["atr"][i],
                spec.stop_pct,
                spec.trail_trigger,
                spec.atr_mult,
            )
            if not reason:
                continue
            hard = pos.entry * (1.0 + spec.stop_pct)
            if reason == "HARD_STOP":
                raw = min(p["open"][i], hard) if np.isfinite(p["open"][i]) else p["close"][i]
                sl.hard_stops += 1
            else:
                raw = p["close"][i]
                sl.trail_exits += 1
            if not np.isfinite(raw) or raw <= 0:
                continue
            close_position(book, pos, float(raw), dates[i], reason, i)

        if len(book.positions) < max_slots and i >= 1:
            scored: List[Tuple[float, str]] = []
            allowed = allowed_by_day[i] if allowed_by_day is not None else None
            for tk, p in panels.items():
                if tk in book.positions:
                    continue
                if allowed is not None and tk not in allowed:
                    continue
                if not p["listed"][i] or not p["listed"][i - 1]:
                    continue
                if not entry_signal(p, i - 1):
                    continue
                rs = composite[tk][i - 1] if tk in composite else np.nan
                if not np.isfinite(rs):
                    continue
                qrs = qqq_crs[i - 1]
                if np.isfinite(qrs) and rs < qrs:
                    continue
                scored.append((float(rs), tk))
            scored.sort(reverse=True)

            n_free = max_slots - len(book.positions)
            taken = len(book.positions)
            needed = equity_now(i, "open") * float(sum(slot_w[taken : taken + n_free]))
            if needed > book.cash:
                free_cash(i, needed)

            for _, tk in scored:
                if len(book.positions) >= max_slots:
                    break
                p = panels[tk]
                raw_open = p["open"][i]
                if not np.isfinite(raw_open) or raw_open <= 0:
                    continue
                fill = _fill_buy(float(raw_open))
                slot_frac = slot_w[min(len(book.positions), len(slot_w) - 1)]
                eq_open = equity_now(i, "open")
                cap = eq_open * slot_frac
                if fill <= 0 or cap < fill:
                    continue
                if cap > book.cash:
                    free_cash(i, cap)
                budget = min(cap, book.cash * 0.995)
                sh = (budget * (1.0 - FEE)) / fill
                if sh <= 0:
                    continue
                cost = sh * fill
                if cost > book.cash:
                    continue
                book.cash -= cost
                book.positions[tk] = Position(
                    ticker=tk,
                    shares=sh,
                    entry=fill,
                    entry_i=i,
                    peak_high=max(fill, p["high"][i] if np.isfinite(p["high"][i]) else fill),
                    entry_date=dates[i],
                )

        rebalance_proxy(i, lev, "close", force=force_proxy)
        eq = equity_now(i, "close")
        book.equity.append(eq)
        book.dates.append(dates[i])
        ledger.equity.append(eq)
        ledger.invested.append(invested)
        if eq > 0:
            sl.occupancy.append(stock_mark(i, "close") / eq)
            q_val = l_val = 0.0
            qp, lp = sl.proxy.get("QQQ"), sl.proxy.get("QLD")
            qpx = _px(proxy_ohlc["QQQ"], i, "close") if "QQQ" in proxy_ohlc else 0.0
            lpx = _px(proxy_ohlc["QLD"], i, "close") if has_qld else 0.0
            if qp and qp.shares > 0 and qpx > 0:
                q_val = qp.shares * qpx
            if lp and lp.shares > 0 and lpx > 0:
                l_val = lp.shares * lpx
                sl.qld_days += 1
            sl.leverage.append((stock_mark(i, "close") + q_val + 2.0 * l_val) / eq)
        prev_lev = lev

    last_i = n - 1
    for tk in list(book.positions.keys()):
        pos = book.positions[tk]
        pxv = panels[tk]["close"][last_i]
        if not np.isfinite(pxv) or pxv <= 0:
            pxv = pos.entry
        close_position(book, pos, float(pxv), dates[last_i], "EOB", last_i)
    for tk in list(sl.proxy.keys()):
        sell_proxy(tk, last_i, "close")
    # Refresh terminal equity after flatten (cash = final)
    if ledger.equity:
        ledger.equity[-1] = book.cash
    return book, sl, ledger


def vs_qqq_dca(m: dict, qqq: dict) -> dict:
    return {
        "excess_profit_usd": round(float(m["net_profit"]) - float(qqq["net_profit"]), 2),
        "excess_roi_pp": round(float(m["roi_pct"]) - float(qqq["roi_pct"]), 2),
        "excess_xirr_pp": round(
            (float(m["xirr_pct"]) if m.get("xirr_pct") is not None else 0.0)
            - (float(qqq["xirr_pct"]) if qqq.get("xirr_pct") is not None else 0.0),
            2,
        ),
    }


def score_row(name: str, m: dict, vs=None) -> str:
    vs = vs or {}
    xirr = m.get("xirr_pct")
    xirr_s = f"{xirr:.2f}%" if xirr is not None else "—"
    return (
        f"| {name} | ${m['total_invested']:,.0f} | ${m['final_equity']:,.2f} | "
        f"${m['net_profit']:,.2f} | {m['roi_pct']:.2f}% | {xirr_s} | "
        f"{m['dca_mdd_pct']:.2f}% | ${vs.get('excess_profit_usd', '—')} | "
        f"{vs.get('excess_roi_pp', '—')}%p |"
    )


def expected_invested(calendar: pd.DatetimeIndex, start_i: int, seed: float = SEED, monthly: float = MONTHLY) -> float:
    total = seed
    for i in range(start_i + 1, len(calendar)):
        if is_month_first(calendar, i, start_i):
            total += monthly
    return total


def diagnose(payload: dict) -> List[str]:
    w8 = payload["windows"]["2018-08-31"]
    w3 = payload["windows"].get("2023-08-31")
    c1, m2, qqq = w8["C1_v1"], w8["M2"], w8["QQQ"]
    champ = max(
        [("SPY", w8["SPY"]), ("QQQ", qqq), ("QLD", w8["QLD"]), ("C1_v1", c1), ("M2", m2)],
        key=lambda kv: kv[1]["final_equity"],
    )
    lines = [
        "## 진단 (적립식)",
        "",
        f"- **8년 최종자산 챔피언: {champ[0]}** ${champ[1]['final_equity']:,.2f} "
        f"(원금 ${champ[1]['total_invested']:,.0f}, ROI {champ[1]['roi_pct']:.2f}%, XIRR {champ[1].get('xirr_pct')}%).",
        f"- **C1 v1 DCA** 최종 ${c1['final_equity']:,.2f} / ROI {c1['roi_pct']:.2f}% / XIRR {c1.get('xirr_pct')}% / "
        f"MDD {c1['dca_mdd_pct']:.2f}% — vs QQQ ${w8['C1_vs_QQQ']['excess_profit_usd']:,.2f} "
        f"({w8['C1_vs_QQQ']['excess_roi_pp']:+.2f}%p ROI).",
        f"- **M2 DCA (−5% stop, bear 40/40)** 최종 ${m2['final_equity']:,.2f} / ROI {m2['roi_pct']:.2f}% / "
        f"XIRR {m2.get('xirr_pct')}% / MDD {m2['dca_mdd_pct']:.2f}% — "
        f"거치식 22% CAGR의 적립식 전이 여부: "
        + (
            "QQQ·C1 대비 최종자산 우위."
            if m2["final_equity"] > max(c1["final_equity"], qqq["final_equity"])
            else "거치식 우위가 적립식에서 희석되거나 역전."
        ),
    ]
    if w3:
        lines.append(
            f"- **AI 3년**: C1 ${w3['C1_v1']['final_equity']:,.2f} (ROI {w3['C1_v1']['roi_pct']:.2f}%) | "
            f"M2 ${w3['M2']['final_equity']:,.2f} | QQQ ${w3['QQQ']['final_equity']:,.2f} | "
            f"QLD ${w3['QLD']['final_equity']:,.2f}."
        )
    lines += [
        "- 적립식에서는 후반 강세(AI 사이클) 가중치가 커져, 거치식 8년 순위와 달라질 수 있다. "
        "XIRR(MWRR)이 실질 기회비용 기준 연환산이다.",
        "",
    ]
    return lines


def write_report(payload: dict) -> None:
    meta = payload["meta"]
    lines = [
        "# C1 v1 DCA Simulation — $8,000 + $400/mo",
        "",
        f"- 생성: {meta.get('generated')}",
        f"- 마찰: slippage {meta['friction']['slippage_bps']:.0f}bps / fee {meta['friction']['fee_bps']:.0f}bps / 익일 시가",
        f"- 시드 ${meta['seed']:,.0f} + 매월 첫 거래일 ${meta['monthly']:,.0f}",
        f"- 8년 예상 원금 ≈ ${meta.get('expected_invested_8y'):,} | AI3y ≈ ${meta.get('expected_invested_3y'):,}",
        "",
        "## 모델",
        "",
        "- **SPY / QQQ / QLD B&H (DCA)** — 입금 즉시 해당 ETF 전량 매수.",
        "- **C1 v1 (DCA)** — 50/30/20 + 유휴 QQQ + 1.5x, stop −4%. 입금은 당일 리밸런스에 반영.",
        "- **M2 (DCA)** — bear 2×40% + stop −5% (거치식 8년 22% 변형).",
        "",
    ]
    header = (
        "| Book | Total Invested | Final Equity | Net Profit | ROI % | XIRR % | DCA MDD | "
        "vs QQQ $ | vs QQQ ROI |"
    )
    sep = "|---|---:|---:|---:|---:|---:|---:|---:|---:|"
    for asof, block in payload["windows"].items():
        lines += [
            f"## Window {asof} → {block['end']}",
            "",
            f"- 실제 총납입: ${block['QQQ']['total_invested']:,.2f} ({block['QQQ']['n_deposits']} cash-flows)",
            "",
            header,
            sep,
        ]
        for key in ("SPY", "QQQ", "QLD", "C1_v1", "M2"):
            bold = "**" if key in ("C1_v1", "M2") else ""
            vs = block.get(f"{key}_vs_QQQ") if key != "QQQ" else None
            lines.append(score_row(f"{bold}{key}{bold}", block[key], vs))
        lines += [
            "",
            f"- C1 invested-ratio MDD: {block['C1_v1'].get('invested_ratio_mdd_pct')}%  "
            f"(equity MDD {block['C1_v1']['dca_mdd_pct']}%)",
            f"- M2 invested-ratio MDD: {block['M2'].get('invested_ratio_mdd_pct')}%",
            "",
        ]
    lines += diagnose(payload)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _line(tag: str, m: dict) -> str:
    return (
        f"  {tag:8s} invested ${m['total_invested']:9,.0f}  final ${m['final_equity']:10,.2f}  "
        f"profit ${m['net_profit']:10,.2f}  ROI {m['roi_pct']:6.2f}%  "
        f"XIRR {m.get('xirr_pct')}%  MDD {m['dca_mdd_pct']:6.2f}%"
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
    spy_ohlc = aligned_ohlc(raw, "SPY", calendar)
    qqq_ohlc = proxy_ohlc["QQQ"]
    qld_ohlc = proxy_ohlc.get("QLD")

    print("[universe] composite RS …")
    universes = build_daily_universes_composite(calendar, raw)
    composite = {t: composite_rs(p["close"]) for t, p in panels.items()}

    exp8 = expected_invested(calendar, max(snapshot_index(calendar, "2018-08-31"), 2))
    exp3 = expected_invested(calendar, max(snapshot_index(calendar, "2023-08-31"), 2))
    print(f"[dca] seed={SEED} monthly={MONTHLY} expected_8y≈{exp8:,.0f} expected_3y≈{exp3:,.0f}")

    payload = {
        "meta": {
            "seed": SEED,
            "monthly": MONTHLY,
            "friction": {"slippage_bps": SLIPPAGE * 10000, "fee_bps": FEE * 10000},
            "expected_invested_8y": round(exp8, 2),
            "expected_invested_3y": round(exp3, 2),
            "qld_available": qld_ohlc is not None,
            "generated": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        },
        "windows": {},
    }

    for asof in SNAPSHOTS:
        start_i = max(snapshot_index(calendar, asof), 2)
        print(f"\n===== WINDOW {asof} ({calendar[start_i].date()}) =====")
        print(f"  expected invested ${expected_invested(calendar, start_i):,.0f}")

        spy_led = run_bh_dca("SPY", calendar, spy_ohlc, start_i)
        qqq_led = run_bh_dca("QQQ", calendar, qqq_ohlc, start_i)
        qld_led = run_bh_dca("QLD", calendar, qld_ohlc, start_i) if qld_ohlc else None

        c1_book, c1_sl, c1_led = run_c1_dca(
            SPECS["C1_v1"], calendar, panels, bench, composite, universes, proxy_ohlc, start_i
        )
        m2_book, m2_sl, m2_led = run_c1_dca(
            SPECS["M2"], calendar, panels, bench, composite, universes, proxy_ohlc, start_i
        )

        end = calendar[-1].strftime("%Y-%m-%d")
        spy_m = pack_dca(spy_led, "SPY", end)
        qqq_m = pack_dca(qqq_led, "QQQ", end)
        qld_m = pack_dca(qld_led, "QLD", end) if qld_led else None
        c1_m = pack_dca(c1_led, "C1_v1", end)
        m2_m = pack_dca(m2_led, "M2", end)
        c1_m["trades_n"] = len(c1_book.trades)
        m2_m["trades_n"] = len(m2_book.trades)
        c1_m["regime_days"] = dict(c1_sl.regime_days)
        m2_m["regime_days"] = dict(m2_sl.regime_days)

        block = {
            "end": calendar[-1].strftime("%Y-%m-%d"),
            "SPY": spy_m,
            "QQQ": qqq_m,
            "QLD": qld_m,
            "C1_v1": c1_m,
            "M2": m2_m,
            "SPY_vs_QQQ": vs_qqq_dca(spy_m, qqq_m),
            "QLD_vs_QQQ": vs_qqq_dca(qld_m, qqq_m) if qld_m else None,
            "C1_vs_QQQ": vs_qqq_dca(c1_m, qqq_m),
            "M2_vs_QQQ": vs_qqq_dca(m2_m, qqq_m),
        }
        payload["windows"][asof] = block

        for tag, m in (("SPY", spy_m), ("QQQ", qqq_m), ("QLD", qld_m), ("C1", c1_m), ("M2", m2_m)):
            if m is None:
                continue
            print(_line(tag, m))
        print(
            f"  C1 vs QQQ: ${block['C1_vs_QQQ']['excess_profit_usd']:,.2f}  "
            f"ROI {block['C1_vs_QQQ']['excess_roi_pp']:+.2f}%p  "
            f"XIRR {block['C1_vs_QQQ']['excess_xirr_pp']:+.2f}%p"
        )
        print(
            f"  M2 vs QQQ: ${block['M2_vs_QQQ']['excess_profit_usd']:,.2f}  "
            f"ROI {block['M2_vs_QQQ']['excess_roi_pp']:+.2f}%p"
        )

    payload["meta"]["elapsed_sec"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(json_safe(payload), f, ensure_ascii=False, indent=2)
    write_report(payload)
    print(f"\nwrote {OUT_PATH}")
    print(f"wrote {REPORT_PATH}")
    for line in diagnose(payload):
        print(line)


if __name__ == "__main__":
    main()
