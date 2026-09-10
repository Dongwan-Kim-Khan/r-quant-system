"""
3-regime book where BULL actually applies methods 1+2:

  1) Do not cut winners: ETF sleeves held through the whole bull, no -4% stop.
  2) Narrower than equal-weight stock rotation: QQQ core + theme overweight.

Chop / bear keep Al-Sangmoo Engine B with HTML stock exits.

Variants (bull sleeve only; chop/bear identical):
  qqq100        99% QQQ hold
  qqq80_smh20   80% QQQ + 20% SMH hold
  qqq70_topetf  70% QQQ + 30% highest-20d-slope sector ETF at bull *entry*

Hysteresis: enter bull if 20d SPY slope > 0; leave bull for chop only if < -2%.
Bear: SPY < 200 SMA.
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

from al_sangmoo.core.constants import STOP_LOSS_PCT
from al_sangmoo.domain.quant.dynamic_universe import SECTOR_ETF_MAP

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
from regime_slope_html import (  # noqa: E402
    HYST_ENTER_BULL,
    HYST_EXIT_BULL,
    add_dvol,
    engine_b,
    liquid_universe,
    next_regime,
)

OUT_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "regime_bull_core.json")
SNAPSHOTS = ["2018-08-31", "2023-08-31"]
SLOPE_BARS = 20

VARIANTS = {
    "qqq100": {"qqq": 0.99, "sat": 0.0, "sat_mode": "none"},
    "qqq80_smh20": {"qqq": 0.80, "sat": 0.20, "sat_mode": "smh"},
    "qqq70_topetf": {"qqq": 0.70, "sat": 0.30, "sat_mode": "top"},
}


def aligned_open(raw: Dict[str, pd.DataFrame], ticker: str, calendar: pd.DatetimeIndex) -> np.ndarray:
    df = raw.get(ticker)
    if df is None or df.empty or "Open" not in df.columns:
        return np.full(len(calendar), np.nan)
    s = df["Open"].copy()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    return s.reindex(calendar).to_numpy(dtype=float)


def pick_top_etf(asof: int, etf_slope: Dict[str, np.ndarray]) -> str:
    best, best_s = "SMH", -1e9
    for etf, arr in etf_slope.items():
        if asof >= len(arr):
            continue
        val = float(arr[asof]) if np.isfinite(arr[asof]) else -1e9
        if val > best_s:
            best, best_s = etf, val
    return best


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
class EtfPos:
    ticker: str
    shares: float = 0.0
    entry: float = 0.0


@dataclass
class Sleeve:
    etfs: Dict[str, EtfPos] = field(default_factory=dict)
    regime_days: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    etf_trades: int = 0
    transitions: int = 0
    bull_sats: List[str] = field(default_factory=list)


def run_variant(
    variant: str,
    calendar: pd.DatetimeIndex,
    panels: Dict[str, dict],
    bench: pd.DataFrame,
    etf_open: Dict[str, np.ndarray],
    etf_close: Dict[str, np.ndarray],
    etf_slope: Dict[str, np.ndarray],
    spy_slope: np.ndarray,
    start_i: int,
) -> Tuple[Book, Sleeve, List[str]]:
    spec = VARIANTS[variant]
    n = len(calendar)
    dates = [d.strftime("%Y-%m-%d") for d in calendar]
    spy = bench["spy"].to_numpy(dtype=float)
    sma = bench["spy_sma200"].to_numpy(dtype=float)
    book = Book(cash=INITIAL)
    sl = Sleeve()
    snap_log: List[str] = []
    start_i = max(start_i, SLOPE_BARS + 2)
    regime = "chop"
    sat_held = ""

    def etf_px(ticker: str, i: int, which: str) -> float:
        arr = etf_open[ticker] if which == "open" else etf_close[ticker]
        px = arr[i] if i < len(arr) else np.nan
        if np.isfinite(px) and px > 0:
            return float(px)
        alt = etf_close[ticker][i] if i < len(etf_close[ticker]) else np.nan
        return float(alt) if np.isfinite(alt) and alt > 0 else 0.0

    def equity_now(i: int) -> float:
        eq = mark_equity(book, i, panels)
        for pos in sl.etfs.values():
            if pos.shares <= 0:
                continue
            px = etf_px(pos.ticker, i, "close")
            eq += pos.shares * (px if px > 0 else pos.entry)
        return eq

    def sell_etf(ticker: str, i: int, reason: str) -> None:
        pos = sl.etfs.get(ticker)
        if pos is None or pos.shares <= 0:
            return
        px = etf_px(ticker, i, "close")
        if px <= 0:
            px = pos.entry
        fill = _fill_sell(px)
        book.cash += pos.shares * fill * (1.0 - FEE)
        sl.etf_trades += 1
        book.trades.append(
            {
                "ticker": ticker,
                "entry_date": dates[max(0, i)],
                "exit_date": dates[i],
                "entry": round(pos.entry, 4),
                "exit": round(fill, 4),
                "pnl_pct": round(((fill - pos.entry) / pos.entry) * 100.0, 4) if pos.entry else 0.0,
                "bars": 0,
                "reason": reason,
            }
        )
        pos.shares = 0.0
        pos.entry = 0.0

    def buy_etf(ticker: str, i: int, target_frac: float) -> None:
        if target_frac <= 0:
            return
        pos = sl.etfs.setdefault(ticker, EtfPos(ticker=ticker))
        raw = etf_px(ticker, i, "open")
        if raw <= 0:
            return
        fill = _fill_buy(raw)
        eq = equity_now(i)
        mark = etf_px(ticker, i, "close") or fill
        cur_val = pos.shares * mark if pos.shares > 0 else 0.0
        need = eq * target_frac - cur_val
        if need < fill:
            return
        budget = min(book.cash * 0.995, need)
        if budget < fill:
            return
        shares = (budget * (1.0 - FEE)) / fill
        cost = shares * fill
        if cost > book.cash:
            return
        book.cash -= cost
        new_shares = pos.shares + shares
        if pos.shares > 0 and pos.entry > 0:
            pos.entry = (pos.entry * pos.shares + fill * shares) / new_shares
        else:
            pos.entry = fill
        pos.shares = new_shares
        sl.etf_trades += 1

    def flatten_stocks(i: int, reason: str) -> None:
        for tk in list(book.positions.keys()):
            pos = book.positions[tk]
            p = panels[tk]
            raw = p["open"][i] if np.isfinite(p["open"][i]) and p["open"][i] > 0 else p["close"][i]
            if not np.isfinite(raw) or raw <= 0:
                raw = pos.entry
            close_position(book, pos, float(raw), dates[i], reason, i)

    def trim_qqq_to(i: int, target_frac: float) -> None:
        pos = sl.etfs.get("QQQ")
        if pos is None or pos.shares <= 0:
            buy_etf("QQQ", i, target_frac)
            return
        eq = equity_now(i)
        px = etf_px("QQQ", i, "close")
        if px <= 0:
            return
        target_val = eq * target_frac
        cur_val = pos.shares * px
        if cur_val <= target_val * 1.02:
            return
        sell_shares = (cur_val - target_val) / px
        sell_shares = min(sell_shares, pos.shares)
        fill = _fill_sell(px)
        book.cash += sell_shares * fill * (1.0 - FEE)
        pos.shares -= sell_shares
        sl.etf_trades += 1

    def enter_bull(i: int, asof: int) -> None:
        nonlocal sat_held
        flatten_stocks(i, "BULL_FLATTEN")
        for tk in list(sl.etfs.keys()):
            if tk != "QQQ":
                sell_etf(tk, i, "BULL_ROTATE")
        sat = ""
        if spec["sat_mode"] == "smh":
            sat = "SMH"
        elif spec["sat_mode"] == "top":
            sat = pick_top_etf(asof, etf_slope)
        sat_held = sat
        if sat:
            sl.bull_sats.append(f"{dates[i]} {sat}")
        buy_etf("QQQ", i, spec["qqq"])
        if sat:
            buy_etf(sat, i, spec["sat"])

    def leave_bull(i: int, new_reg: str) -> None:
        nonlocal sat_held
        for tk in list(sl.etfs.keys()):
            if tk != "QQQ":
                sell_etf(tk, i, "LEAVE_BULL")
        sat_held = ""
        if new_reg == "chop":
            trim_qqq_to(i, 0.50)
            if sl.etfs.get("QQQ") is None or sl.etfs["QQQ"].shares <= 0:
                buy_etf("QQQ", i, 0.50)
        else:
            sell_etf("QQQ", i, "BEAR_EXIT")

    def enter_chop(i: int) -> None:
        buy_etf("QQQ", i, 0.50)

    def enter_bear(i: int) -> None:
        sell_etf("QQQ", i, "BEAR_EXIT")
        for tk in list(sl.etfs.keys()):
            sell_etf(tk, i, "BEAR_EXIT")

    def try_engine_b(i: int, max_slots: int, slot_frac: float) -> None:
        if len(book.positions) >= max_slots or i < 1:
            return
        allowed = liquid_universe(i - 1, panels)
        scored: List[Tuple[float, str]] = []
        for tk in allowed:
            if tk in book.positions:
                continue
            p = panels.get(tk)
            if p is None or not p["listed"][i] or not p["listed"][i - 1]:
                continue
            if not engine_b(p, i - 1):
                continue
            dvol = p.get("dvol20", np.zeros(1))[i - 1]
            scored.append((float(dvol) if np.isfinite(dvol) else 0.0, tk))
        scored.sort(reverse=True)
        eq = equity_now(i)
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
            eq = equity_now(i)

    for i in range(start_i, n):
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
            sl.transitions += 1
            if regime == "bull":
                leave_bull(i, new_regime)
            elif new_regime == "bull":
                enter_bull(i, asof)
            elif new_regime == "chop":
                enter_chop(i)
            else:
                enter_bear(i)
        elif i == start_i:
            if new_regime == "bull":
                enter_bull(i, asof)
            elif new_regime == "chop":
                enter_chop(i)
        regime = new_regime
        sl.regime_days[regime] += 1

        if regime == "chop":
            try_engine_b(i, max_slots=1, slot_frac=0.45)
        elif regime == "bear":
            try_engine_b(i, max_slots=2, slot_frac=0.25)

        if dates[i] in SNAPSHOTS or dates[i].endswith("-08-31") or dates[i].endswith("-02-28"):
            top = pick_top_etf(asof, etf_slope)
            slp = spy_slope[asof] if np.isfinite(spy_slope[asof]) else 0.0
            held = ",".join(f"{p.ticker}:{p.shares:.1f}" for p in sl.etfs.values() if p.shares > 0)
            snap_log.append(f"{dates[i]} {regime} slope={slp:.2f} top={top} sat={sat_held or '-'} etf={held or '-'}")

        book.equity.append(equity_now(i))
        book.dates.append(dates[i])

    last = n - 1
    for tk in list(sl.etfs.keys()):
        sell_etf(tk, last, "EOB")
    for tk in list(book.positions.keys()):
        pos = book.positions[tk]
        px = panels[tk]["close"][last]
        if not np.isfinite(px) or px <= 0:
            px = pos.entry
        close_position(book, pos, float(px), dates[last], "EOB", last)
    return book, sl, snap_log


def weekly_curve(book: Book, qqq: pd.Series) -> List[dict]:
    idx = pd.to_datetime(book.dates)
    eq = pd.Series(book.equity, index=idx)
    q = qqq.reindex(idx).ffill()
    both = pd.DataFrame({"s": eq, "q": q}).dropna()
    both = both.resample("W-FRI").last().dropna()
    if both.empty:
        return []
    s0, q0 = float(both["s"].iloc[0]), float(both["q"].iloc[0])
    out = []
    for dt, row in both.iterrows():
        out.append(
            {
                "date": dt.strftime("%Y-%m-%d"),
                "strategy": round(100.0 * float(row["s"]) / s0, 2),
                "qqq": round(100.0 * float(row["q"]) / q0, 2),
            }
        )
    return out


def main() -> None:
    t0 = time.time()
    raw = download_master()
    names = [t for t in stock_names() if t in raw]
    extra = set(BENCH) | set(SECTOR_ETF_MAP.values()) | {"QQQ", "SMH"}
    sliced = {k: v for k, v in raw.items() if k in set(names) | extra}
    calendar, panels, bench = align_panels(sliced)
    add_dvol(panels, raw, calendar)

    spy_c = aligned_close(raw, "SPY", calendar)
    spy_slope = rolling_rs(spy_c, SLOPE_BARS)
    etf_slope = {etf: rolling_rs(aligned_close(raw, etf, calendar), SLOPE_BARS) for etf in SECTOR_ETF_MAP.values()}
    etf_tickers = list(dict.fromkeys(["QQQ", "SMH"] + list(SECTOR_ETF_MAP.values())))
    etf_open = {t: aligned_open(raw, t, calendar) for t in etf_tickers}
    etf_close = {t: aligned_close(raw, t, calendar) for t in etf_tickers}

    spy_s = bench["spy"].copy()
    spy_s.index = pd.to_datetime(spy_s.index)
    qqq_s = bench["qqq"].copy()
    qqq_s.index = pd.to_datetime(qqq_s.index)

    payload = {
        "meta": {
            "elapsed_sec": 0,
            "hysteresis": {"enter_bull_pct": HYST_ENTER_BULL, "exit_bull_pct": HYST_EXIT_BULL},
            "rules": (
                "bull: hold QQQ(+SMH or top sector ETF), no stock -4% stop; "
                "chop: 50% QQQ + Engine B HTML; bear: cash + Engine B HTML"
            ),
        },
        "windows": {},
    }

    for asof in SNAPSHOTS:
        start_i = snapshot_index(calendar, asof)
        payload["windows"][asof] = {}
        print(f"\n===== start {asof} =====")
        print(f"{'':16} {'ret':>8} {'CAGR':>8} {'Sharpe':>8} {'MDD':>8} {'αQQQ':>8} {'p':>7}")
        first_book = None
        for vname in VARIANTS:
            book, sl, snaps = run_variant(
                vname, calendar, panels, bench, etf_open, etf_close, etf_slope, spy_slope, start_i
            )
            if first_book is None:
                first_book = book
            st = metrics(book.equity, book.dates)
            ts = trade_stats(book.trades)
            idx = pd.to_datetime(book.dates)
            spy_bh = bh_metrics(spy_s.reindex(idx))
            qqq_bh = bh_metrics(qqq_s.reindex(idx))
            r = pd.Series(book.equity, index=idx).pct_change()
            rq = qqq_s.reindex(idx).pct_change()
            both = pd.DataFrame({"s": r, "qqq": rq}).dropna()
            jq = jensen(both["s"].to_numpy(), both["qqq"].to_numpy())
            n_reg = sum(v for k, v in sl.regime_days.items() if k in ("bull", "chop", "bear")) or 1
            payload["windows"][asof][vname] = {
                "strategy": st,
                "trades": ts,
                "spy": spy_bh,
                "qqq": qqq_bh,
                "jensen_qqq": jq,
                "regime_share": {
                    k: round(100.0 * v / n_reg, 1) for k, v in sl.regime_days.items() if k in ("bull", "chop", "bear")
                },
                "etf_roundtrips": sl.etf_trades,
                "regime_transitions": sl.transitions,
                "bull_sats": sl.bull_sats[:12],
                "snapshots": snaps[:8],
                "weekly": weekly_curve(book, qqq_s),
            }
            print(
                f"{vname:16} {st['total_return_pct']:+8.1f} {st['cagr_pct']:8.1f} "
                f"{st['sharpe']:8.3f} {st['mdd_pct']:8.2f} {jq['ann_alpha_pct']:+8.2f} {jq['p']:7.3f}"
            )
            if vname == "qqq80_smh20":
                print(f"  vs QQQ excess CAGR {st['cagr_pct']-qqq_bh['cagr_pct']:+.1f}%p  "
                      f"etf trips={sl.etf_trades}  trans={sl.transitions}  sats={sl.bull_sats[:4]}")
                for line in snaps[:5]:
                    print("   ", line)
        if first_book is not None:
            idx = pd.to_datetime(first_book.dates)
            qqq_bh = bh_metrics(qqq_s.reindex(idx))
            spy_bh = bh_metrics(spy_s.reindex(idx))
            print(f"{'QQQ BH':16} {qqq_bh['total_return_pct']:+8.1f} {qqq_bh['cagr_pct']:8.1f} "
                  f"{qqq_bh['sharpe']:8.3f} {qqq_bh['mdd_pct']:8.2f}")
            print(f"{'SPY BH':16} {spy_bh['total_return_pct']:+8.1f} {spy_bh['cagr_pct']:8.1f} "
                  f"{spy_bh['sharpe']:8.3f} {spy_bh['mdd_pct']:8.2f}")
            payload["windows"][asof]["benchmarks"] = {"qqq": qqq_bh, "spy": spy_bh}

    payload["meta"]["elapsed_sec"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {OUT_PATH} in {payload['meta']['elapsed_sec']}s")


if __name__ == "__main__":
    main()
