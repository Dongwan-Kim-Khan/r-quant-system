"""
3-regime Al-Sangmoo HTML backtest vs SPY/QQQ.

Bull  : SPY>=200SMA and SPY 20d slope>0
        -> top 2 sector ETFs by 20d slope, largest $volume names, Engine A, 2 slots ~47.5%
Chop  : SPY>=200SMA and slope<=0
        -> 50% QQQ core + Engine B satellite (1 name)
Bear  : SPY<200SMA
        -> 50% cash + Engine B only, 2x25%, no QQQ

HTML exits on stocks (-4% or trailing after +15%). No kijun-anytime.
PIT: listed names only. Starts 2018-08-31 and 2023-08-31.
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
from collections import defaultdict
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

from al_sangmoo.core.constants import STOP_LOSS_PCT, TAKE_PROFIT_PCT
from al_sangmoo.domain.quant.dynamic_universe import SECTOR_ETF_MAP, SECTOR_MASTER_CANDIDATES

sys.path.insert(0, os.path.join(PROJECT_ROOT, "research_and_backtests"))
from compare_html_vs_live_exit import (  # noqa: E402
    BENCH,
    FEE,
    INITIAL,
    SLIPPAGE,
    Book,
    Position,
    _fill_buy,
    _fill_sell,
    align_panels,
    close_position,
    decide_exit,
    mark_equity,
    metrics,
    ols,
    trade_stats,
)
from pit_dynamic_universe_exit_ab import aligned_close, rolling_rs, stock_names  # noqa: E402
from pit_universe_exit_ab import download_master, snapshot_index  # noqa: E402

OUT_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "regime_slope_html.json")
SNAPSHOTS = ["2018-08-31", "2023-08-31"]
SLOPE_BARS = 20
TOP_SECTORS = 2
NAMES_PER_SECTOR = 5
HYST_ENTER_BULL = 0.0  # chop → bull if 20d slope % > 0 (easy enter)
HYST_EXIT_BULL = -2.0  # bull → chop only if 20d slope % < -2 (sticky bull)


def engine_a(p: dict, i: int) -> bool:
    if i < 1 or not p["listed"][i]:
        return False
    close, kijun, tenkan = p["close"][i], p["kijun"][i], p["tenkan"][i]
    span_a, span_b, high20 = p["span_a"][i], p["span_b"][i], p["high20"][i]
    if not np.isfinite([close, kijun, tenkan, span_a, span_b, high20]).all():
        return False
    if kijun <= 0 or close <= 0:
        return False
    cloud_top = max(span_a, span_b)
    obv_ok = np.isfinite(p["obv"][i]) and np.isfinite(p["obv_ma"][i]) and p["obv"][i] > p["obv_ma"][i]
    return close >= cloud_top and tenkan >= kijun and obv_ok and close > high20


def engine_b(p: dict, i: int) -> bool:
    if i < 1 or not p["listed"][i] or not p["listed"][i - 1]:
        return False
    close, kijun, tenkan = p["close"][i], p["kijun"][i], p["tenkan"][i]
    span_a, span_b = p["span_a"][i], p["span_b"][i]
    if not np.isfinite([close, kijun, tenkan, span_a, span_b]).all():
        return False
    if kijun <= 0 or close <= 0:
        return False
    cloud_top = max(span_a, span_b)
    obv_ok = np.isfinite(p["obv"][i]) and np.isfinite(p["obv_ma"][i]) and p["obv"][i] > p["obv_ma"][i]
    prev_close, prev_kijun = p["close"][i - 1], p["kijun"][i - 1]
    if not np.isfinite([prev_close, prev_kijun]).all():
        return False
    return (
        close >= cloud_top
        and tenkan >= kijun
        and obv_ok
        and prev_close <= prev_kijun
        and close > kijun
    )


def add_dvol(panels: Dict[str, dict], raw: Dict[str, pd.DataFrame], calendar: pd.DatetimeIndex) -> None:
    n = len(calendar)
    for t, p in panels.items():
        df = raw.get(t)
        if df is None or "Volume" not in df.columns:
            p["dvol20"] = np.zeros(n)
            continue
        vol = df["Volume"].copy()
        vol.index = pd.to_datetime(vol.index).tz_localize(None)
        v = vol.reindex(calendar).fillna(0.0).to_numpy(dtype=float)
        dvol = p["close"] * v
        p["dvol20"] = pd.Series(dvol).rolling(20, min_periods=8).mean().to_numpy()


def next_regime(prev: str, spy: float, sma: float, slope20: float) -> str:
    """Bear is hard (price vs 200 SMA). Bull is sticky: enter on slope>0, leave only if slope<-2%."""
    if not np.isfinite(spy) or not np.isfinite(sma) or sma <= 0:
        return prev if prev in ("bull", "chop", "bear") else "chop"
    if spy < sma:
        return "bear"
    slope = float(slope20) if np.isfinite(slope20) else 0.0
    if prev == "bear":
        return "bull" if slope > HYST_ENTER_BULL else "chop"
    if prev == "bull":
        return "chop" if slope < HYST_EXIT_BULL else "bull"
    return "bull" if slope > HYST_ENTER_BULL else "chop"


def slope_universe(
    asof: int,
    etf_slope: Dict[str, np.ndarray],
    panels: Dict[str, dict],
) -> Tuple[Set[str], List[dict]]:
    ranked = []
    for sector, etf in SECTOR_ETF_MAP.items():
        s = etf_slope.get(etf)
        val = float(s[asof]) if s is not None and asof < len(s) and np.isfinite(s[asof]) else -999.0
        ranked.append((val, sector, etf))
    ranked.sort(reverse=True)
    picked: List[str] = []
    detail = []
    for slope, sector, etf in ranked[:TOP_SECTORS]:
        scored = []
        for t in SECTOR_MASTER_CANDIDATES.get(sector, []):
            p = panels.get(t)
            if p is None or not p["listed"][asof]:
                continue
            dvol = p.get("dvol20", np.zeros(1))[asof]
            if not np.isfinite(dvol):
                dvol = 0.0
            scored.append((float(dvol), t))
        scored.sort(reverse=True)
        names = [t for _, t in scored[:NAMES_PER_SECTOR]]
        picked.extend(names)
        detail.append({"sector": sector, "etf": etf, "slope20": round(slope, 2), "names": names})
    return set(picked), detail


def liquid_universe(asof: int, panels: Dict[str, dict], top_n: int = 40) -> Set[str]:
    scored = []
    for t, p in panels.items():
        if not p["listed"][asof]:
            continue
        dvol = p.get("dvol20", np.zeros(1))[asof]
        if np.isfinite(dvol) and dvol > 0:
            scored.append((float(dvol), t))
    scored.sort(reverse=True)
    return {t for _, t in scored[:top_n]}


def bh_metrics(px: pd.Series) -> dict:
    px = px.dropna()
    eq = px.to_numpy(dtype=float)
    eq = eq / eq[0] * INITIAL
    dates = [d.strftime("%Y-%m-%d") for d in px.index]
    return metrics(list(eq), dates, initial=INITIAL)


def jensen(y: np.ndarray, x: np.ndarray) -> dict:
    m = ols(y, np.column_stack([np.ones(len(y)), x]))
    a = m["beta"][0]
    return {
        "ann_alpha_pct": round(a * 252 * 100.0, 2),
        "t": m["t"][0],
        "p": m["p"][0],
        "beta": round(m["beta"][1], 3),
        "r2": m["r2"],
        "n": m["n"],
    }


@dataclass
class Sleeve:
    qqq_shares: float = 0.0
    qqq_entry: float = 0.0
    regime_days: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    qqq_trades: int = 0
    transitions: int = 0


def run_regime(
    calendar: pd.DatetimeIndex,
    panels: Dict[str, dict],
    bench: pd.DataFrame,
    qqq_open: np.ndarray,
    etf_slope: Dict[str, np.ndarray],
    spy_slope: np.ndarray,
    start_i: int,
) -> Tuple[Book, Sleeve, List[str]]:
    n = len(calendar)
    dates = [d.strftime("%Y-%m-%d") for d in calendar]
    spy = bench["spy"].to_numpy(dtype=float)
    sma = bench["spy_sma200"].to_numpy(dtype=float)
    qqq_c = bench["qqq"].to_numpy(dtype=float)
    book = Book(cash=INITIAL)
    sl = Sleeve()
    snap_log: List[str] = []
    start_i = max(start_i, SLOPE_BARS + 2)
    regime = "chop"
    transitions = 0

    def equity_with_qqq(i: int) -> float:
        eq = mark_equity(book, i, panels)
        qc = qqq_c[i]
        if sl.qqq_shares > 0 and np.isfinite(qc):
            eq += sl.qqq_shares * qc
        return eq

    def sell_qqq(i: int, reason: str) -> None:
        if sl.qqq_shares <= 0:
            return
        px = qqq_c[i] if np.isfinite(qqq_c[i]) else sl.qqq_entry
        fill = _fill_sell(float(px))
        book.cash += sl.qqq_shares * fill * (1.0 - FEE)
        sl.qqq_trades += 1
        sl.qqq_shares = 0.0
        sl.qqq_entry = 0.0

    def buy_qqq(i: int, target_frac: float) -> None:
        if sl.qqq_shares > 0:
            return
        raw = qqq_open[i] if np.isfinite(qqq_open[i]) and qqq_open[i] > 0 else qqq_c[i]
        if not np.isfinite(raw) or raw <= 0:
            return
        fill = _fill_buy(float(raw))
        eq = equity_with_qqq(i)
        budget = min(book.cash * 0.995, eq * target_frac)
        if budget < fill:
            return
        shares = (budget * (1.0 - FEE)) / fill
        cost = shares * fill
        if cost > book.cash:
            return
        book.cash -= cost
        sl.qqq_shares = shares
        sl.qqq_entry = fill
        sl.qqq_trades += 1

    def try_entries(i: int, allowed: Set[str], signal_fn, max_slots: int, slot_frac: float) -> None:
        if len(book.positions) >= max_slots or i < 1:
            return
        scored: List[Tuple[float, str]] = []
        for tk in allowed:
            if tk in book.positions:
                continue
            p = panels.get(tk)
            if p is None or not p["listed"][i] or not p["listed"][i - 1]:
                continue
            if not signal_fn(p, i - 1):
                continue
            dvol = p.get("dvol20", np.zeros(1))[i - 1]
            scored.append((float(dvol) if np.isfinite(dvol) else 0.0, tk))
        scored.sort(reverse=True)
        eq = equity_with_qqq(i)
        for _, tk in scored:
            if len(book.positions) >= max_slots:
                break
            p = panels[tk]
            raw_open = p["open"][i]
            if not np.isfinite(raw_open) or raw_open <= 0:
                continue
            fill = _fill_buy(float(raw_open))
            cap = eq * slot_frac
            if fill <= 0 or cap < fill:
                continue
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
            eq = equity_with_qqq(i)

    for i in range(start_i, n):
        # stock exits (HTML)
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
            if np.isfinite(raw) and raw > 0:
                close_position(book, pos, float(raw), dates[i], reason, i)

        asof = i - 1
        new_regime = next_regime(regime, spy[asof], sma[asof], spy_slope[asof])
        if new_regime != regime:
            transitions += 1
        regime = new_regime
        sl.regime_days[regime] += 1

        if regime != "chop" and sl.qqq_shares > 0:
            sell_qqq(i, "regime_exit")
        if regime == "chop":
            buy_qqq(i, 0.50)

        uni, detail = slope_universe(asof, etf_slope, panels)
        liq = liquid_universe(asof, panels)
        if dates[i] in SNAPSHOTS or dates[i].endswith("-08-31") or dates[i].endswith("-02-28"):
            snap_log.append(
                f"{dates[i]} {regime} slope={spy_slope[asof]:.2f} "
                + " | ".join(f"{d['sector']}({d['slope20']:+.1f}) {d['names']}" for d in detail)
            )

        if regime == "bull":
            try_entries(i, uni, engine_a, max_slots=2, slot_frac=0.475)
        elif regime == "chop":
            try_entries(i, liq, engine_b, max_slots=1, slot_frac=0.45)
        else:
            try_entries(i, liq, engine_b, max_slots=2, slot_frac=0.25)

        book.equity.append(equity_with_qqq(i))
        book.dates.append(dates[i])

    sl.transitions = transitions
    last = n - 1
    sell_qqq(last, "eob")
    for tk in list(book.positions.keys()):
        pos = book.positions[tk]
        px = panels[tk]["close"][last]
        if not np.isfinite(px) or px <= 0:
            px = pos.entry
        close_position(book, pos, float(px), dates[last], "EOB", last)
    return book, sl, snap_log


def main() -> None:
    t0 = time.time()
    raw = download_master()
    names = [t for t in stock_names() if t in raw]
    sliced = {k: v for k, v in raw.items() if k in set(names) | set(BENCH)}
    calendar, panels, bench = align_panels(sliced)
    add_dvol(panels, raw, calendar)

    spy_c = aligned_close(raw, "SPY", calendar)
    spy_slope = rolling_rs(spy_c, SLOPE_BARS)
    etf_slope = {etf: rolling_rs(aligned_close(raw, etf, calendar), SLOPE_BARS) for etf in SECTOR_ETF_MAP.values()}
    qqq_o = raw["QQQ"]["Open"].copy()
    qqq_o.index = pd.to_datetime(qqq_o.index).tz_localize(None)
    qqq_open = qqq_o.reindex(calendar).ffill().to_numpy(dtype=float)

    spy_s = bench["spy"].copy()
    spy_s.index = pd.to_datetime(spy_s.index)
    qqq_s = bench["qqq"].copy()
    qqq_s.index = pd.to_datetime(qqq_s.index)

    payload = {
        "meta": {
            "elapsed_sec": 0,
            "hysteresis": {"enter_bull_pct": 0.0, "exit_bull_pct": -2.0},
            "rules": "bull slope top2 large-cap EngineA; chop 50% QQQ+EngineB; bear cash+EngineB; HTML exits; asymmetric 20d slope (enter >0, exit <-2)",
        },
        "windows": {},
    }

    for asof in SNAPSHOTS:
        start_i = snapshot_index(calendar, asof)
        book, sl, snaps = run_regime(calendar, panels, bench, qqq_open, etf_slope, spy_slope, start_i)
        st = metrics(book.equity, book.dates)
        ts = trade_stats(book.trades)
        idx = pd.to_datetime(book.dates)
        spy_bh = bh_metrics(spy_s.reindex(idx))
        qqq_bh = bh_metrics(qqq_s.reindex(idx))
        r = pd.Series(book.equity, index=idx).pct_change()
        rs = spy_s.reindex(idx).pct_change()
        rq = qqq_s.reindex(idx).pct_change()
        both = pd.DataFrame({"s": r, "spy": rs, "qqq": rq}).dropna()
        js = jensen(both["s"].to_numpy(), both["spy"].to_numpy())
        jq = jensen(both["s"].to_numpy(), both["qqq"].to_numpy())
        n_reg = sum(v for k, v in sl.regime_days.items() if k in ("bull", "chop", "bear")) or 1
        payload["windows"][asof] = {
            "strategy": st,
            "trades": ts,
            "spy": spy_bh,
            "qqq": qqq_bh,
            "jensen_spy": js,
            "jensen_qqq": jq,
            "regime_share": {k: round(100.0 * v / n_reg, 1) for k, v in sl.regime_days.items() if k in ("bull", "chop", "bear")},
            "qqq_roundtrips": sl.qqq_trades,
            "regime_transitions": sl.transitions,
            "snapshots": snaps[:12],
        }
        print(f"\n===== {book.dates[0]} → {book.dates[-1]} =====")
        print(f"  regime days {dict(sl.regime_days)}  transitions={sl.transitions}  QQQ sleeves={sl.qqq_trades}")
        print(f"{'':12} {'ret':>8} {'CAGR':>8} {'Sharpe':>8} {'MDD':>8}")
        print(f"{'3-regime':12} {st['total_return_pct']:+8.1f} {st['cagr_pct']:8.1f} {st['sharpe']:8.3f} {st['mdd_pct']:8.2f}")
        print(f"{'SPY':12} {spy_bh['total_return_pct']:+8.1f} {spy_bh['cagr_pct']:8.1f} {spy_bh['sharpe']:8.3f} {spy_bh['mdd_pct']:8.2f}")
        print(f"{'QQQ':12} {qqq_bh['total_return_pct']:+8.1f} {qqq_bh['cagr_pct']:8.1f} {qqq_bh['sharpe']:8.3f} {qqq_bh['mdd_pct']:8.2f}")
        print(f"  excess CAGR vs SPY {st['cagr_pct']-spy_bh['cagr_pct']:+.1f}%p  vs QQQ {st['cagr_pct']-qqq_bh['cagr_pct']:+.1f}%p")
        print(f"  Jensen vs SPY α={js['ann_alpha_pct']:+.2f}% β={js['beta']} t={js['t']} p={js['p']}")
        print(f"  Jensen vs QQQ α={jq['ann_alpha_pct']:+.2f}% β={jq['beta']} t={jq['t']} p={jq['p']}")
        print(f"  stock trades {ts['n']} win {ts['win_rate']}% PF {ts['profit_factor']}  QQQ sleeves {sl.qqq_trades}")
        print(f"  reasons {ts['reasons']}")
        for line in snaps[:6]:
            print("   ", line)

    payload["meta"]["elapsed_sec"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {OUT_PATH} in {payload['meta']['elapsed_sec']}s")


if __name__ == "__main__":
    main()
