"""
Risk-on book: 80% leading sector ETF + 20% QQQ.

Classifier knobs from the lag discussion:
  1) No chop as a size state. SPY not in confirmed bear → stay 80/20.
     20d slope only picks WHICH sector is the 80%, not whether to invest.
  2) Harder bear entry: 5 consecutive SPY closes below SMA200, OR 3% below.
     Leave bear on 1 close back above SMA200.

Leader stickiness: re-pick the 80% sleeve on the first session of each
calendar month (and on bear→bull). Intra-month ranking changes are ignored.

Bear: flatten ETFs, Engine B 2x25% with HTML -4% stops.
No stock -4% on the ETF sleeves.
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

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
from regime_bull_core import aligned_open, bh_metrics, jensen, weekly_curve  # noqa: E402
from regime_slope_html import add_dvol, engine_b, liquid_universe  # noqa: E402

OUT_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "regime_leader80.json")
SNAPSHOTS = ["2018-08-31", "2023-08-31"]
SLOPE_BARS = 20
BEAR_DAYS = 5
BEAR_DEEP = 0.03
LEADER_FRAC = 0.80
QQQ_FRAC = 0.20
ETF_NAME = {v: k for k, v in SECTOR_ETF_MAP.items()}


def days_below_sma(spy: np.ndarray, sma: np.ndarray, i: int) -> int:
    n = 0
    while i - n >= 0:
        px, m = spy[i - n], sma[i - n]
        if not np.isfinite(px) or not np.isfinite(m) or m <= 0 or px >= m:
            break
        n += 1
    return n


def next_regime(prev: str, spy: float, sma: float, n_below: int) -> str:
    if not np.isfinite(spy) or not np.isfinite(sma) or sma <= 0:
        return prev if prev in ("bull", "bear") else "bull"
    deep = spy < sma * (1.0 - BEAR_DEEP)
    confirmed = n_below >= BEAR_DAYS or deep
    if prev == "bear":
        return "bull" if spy >= sma else "bear"
    return "bear" if confirmed else "bull"


def ranked_leaders(asof: int, etf_slope: Dict[str, np.ndarray]) -> List[Tuple[float, str]]:
    rows = []
    for etf, arr in etf_slope.items():
        if asof >= len(arr):
            continue
        val = float(arr[asof]) if np.isfinite(arr[asof]) else -1e9
        rows.append((val, etf))
    rows.sort(reverse=True)
    return rows


def pick_leader(asof: int, etf_slope: Dict[str, np.ndarray], held: str) -> str:
    ranked = ranked_leaders(asof, etf_slope)
    if not ranked:
        return held or "XLK"
    return ranked[0][1]


def is_month_open(dates: List[str], i: int, start_i: int) -> bool:
    if i <= start_i:
        return True
    return dates[i][:7] != dates[i - 1][:7]


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
    leader_switches: int = 0
    leaders: List[str] = field(default_factory=list)


def run_book(
    calendar: pd.DatetimeIndex,
    panels: Dict[str, dict],
    bench: pd.DataFrame,
    etf_open: Dict[str, np.ndarray],
    etf_close: Dict[str, np.ndarray],
    etf_slope: Dict[str, np.ndarray],
    start_i: int,
) -> Tuple[Book, Sleeve, List[str]]:
    n = len(calendar)
    dates = [d.strftime("%Y-%m-%d") for d in calendar]
    spy = bench["spy"].to_numpy(dtype=float)
    sma = bench["spy_sma200"].to_numpy(dtype=float)
    book = Book(cash=INITIAL)
    sl = Sleeve()
    snaps: List[str] = []
    start_i = max(start_i, SLOPE_BARS + 2)
    regime = "bull"
    leader = ""

    def etf_px(ticker: str, i: int, which: str) -> float:
        arr = etf_open.get(ticker) if which == "open" else etf_close.get(ticker)
        if arr is None or i >= len(arr):
            return 0.0
        px = arr[i]
        if np.isfinite(px) and px > 0:
            return float(px)
        alt = etf_close.get(ticker)
        if alt is not None and i < len(alt) and np.isfinite(alt[i]) and alt[i] > 0:
            return float(alt[i])
        return 0.0

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
        px = etf_px(ticker, i, "close") or pos.entry
        fill = _fill_sell(px)
        book.cash += pos.shares * fill * (1.0 - FEE)
        sl.etf_trades += 1
        book.trades.append(
            {
                "ticker": ticker,
                "entry_date": dates[i],
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

    def sell_all_etfs(i: int, reason: str) -> None:
        for tk in list(sl.etfs.keys()):
            sell_etf(tk, i, reason)

    def allocate_risk_on(i: int, asof: int, new_leader: str) -> None:
        nonlocal leader
        flatten_stocks(i, "BULL_FLATTEN")
        if leader and leader != new_leader:
            sell_etf(leader, i, "LEADER_SWITCH")
        leader = new_leader
        buy_etf(leader, i, LEADER_FRAC)
        buy_etf("QQQ", i, QQQ_FRAC)

    def try_engine_b(i: int) -> None:
        if len(book.positions) >= 2 or i < 1:
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
            if len(book.positions) >= 2:
                break
            p = panels[tk]
            raw_open = p["open"][i]
            if not np.isfinite(raw_open) or raw_open <= 0:
                continue
            fill = _fill_buy(float(raw_open))
            cap = eq * 0.25
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
        n_below = days_below_sma(spy, sma, asof)
        new_regime = next_regime(regime, spy[asof], sma[asof], n_below)
        month_open = is_month_open(dates, i, start_i)
        if new_regime == "bull" and (not leader or month_open or new_regime != regime):
            want = pick_leader(asof, etf_slope, leader)
        else:
            want = leader

        if i == start_i and new_regime == "bull":
            allocate_risk_on(i, asof, want)
            sl.leaders.append(f"{dates[i]} {want} {ETF_NAME.get(want, want)}")
        elif new_regime != regime:
            sl.transitions += 1
            if new_regime == "bear":
                sell_all_etfs(i, "BEAR_EXIT")
                leader = ""
            else:
                allocate_risk_on(i, asof, want)
                sl.leaders.append(f"{dates[i]} {want} {ETF_NAME.get(want, want)}")
        elif new_regime == "bull" and month_open and want != leader:
            sl.leader_switches += 1
            allocate_risk_on(i, asof, want)
            sl.leaders.append(f"{dates[i]} {want} {ETF_NAME.get(want, want)}")

        regime = new_regime
        sl.regime_days[regime] += 1

        if regime == "bear":
            try_engine_b(i)

        if dates[i] in SNAPSHOTS or dates[i].endswith("-08-31") or dates[i].endswith("-02-28"):
            ranked = ranked_leaders(asof, etf_slope)[:3]
            top_s = " ".join(f"{etf}:{slp:+.1f}" for slp, etf in ranked)
            held = ",".join(f"{p.ticker}" for p in sl.etfs.values() if p.shares > 0)
            snaps.append(
                f"{dates[i]} {regime} below={n_below}d leader={leader or '-'} "
                f"held={held or '-'} top={top_s}"
            )

        book.equity.append(equity_now(i))
        book.dates.append(dates[i])

    last = n - 1
    sell_all_etfs(last, "EOB")
    for tk in list(book.positions.keys()):
        pos = book.positions[tk]
        px = panels[tk]["close"][last]
        if not np.isfinite(px) or px <= 0:
            px = pos.entry
        close_position(book, pos, float(px), dates[last], "EOB", last)
    return book, sl, snaps


def main() -> None:
    t0 = time.time()
    raw = download_master()
    names = [t for t in stock_names() if t in raw]
    extra = set(BENCH) | set(SECTOR_ETF_MAP.values()) | {"QQQ"}
    sliced = {k: v for k, v in raw.items() if k in set(names) | extra}
    calendar, panels, bench = align_panels(sliced)
    add_dvol(panels, raw, calendar)

    etf_slope = {etf: rolling_rs(aligned_close(raw, etf, calendar), SLOPE_BARS) for etf in SECTOR_ETF_MAP.values()}
    etf_tickers = list(dict.fromkeys(["QQQ"] + list(SECTOR_ETF_MAP.values())))
    etf_open = {t: aligned_open(raw, t, calendar) for t in etf_tickers}
    etf_close = {t: aligned_close(raw, t, calendar) for t in etf_tickers}

    spy_s = bench["spy"].copy()
    spy_s.index = pd.to_datetime(spy_s.index)
    qqq_s = bench["qqq"].copy()
    qqq_s.index = pd.to_datetime(qqq_s.index)

    payload = {
        "meta": {
            "elapsed_sec": 0,
            "rules": (
                "risk-on: 80% monthly 20d-slope sector ETF + 20% QQQ, no stop; "
                "no chop sleeve; bear after 5d or 3% below SMA200; Engine B in bear"
            ),
            "bear_days": BEAR_DAYS,
            "bear_deep_pct": BEAR_DEEP * 100,
            "leader_rebalance": "monthly",
        },
        "windows": {},
    }

    print(f"{'':16} {'ret':>8} {'CAGR':>8} {'Sharpe':>8} {'MDD':>8} {'αQQQ':>8} {'p':>7}")
    for asof in SNAPSHOTS:
        start_i = snapshot_index(calendar, asof)
        book, sl, snaps = run_book(calendar, panels, bench, etf_open, etf_close, etf_slope, start_i)
        st = metrics(book.equity, book.dates)
        ts = trade_stats(book.trades)
        idx = pd.to_datetime(book.dates)
        spy_bh = bh_metrics(spy_s.reindex(idx))
        qqq_bh = bh_metrics(qqq_s.reindex(idx))
        r = pd.Series(book.equity, index=idx).pct_change()
        rq = qqq_s.reindex(idx).pct_change()
        both = pd.DataFrame({"s": r, "qqq": rq}).dropna()
        jq = jensen(both["s"].to_numpy(), both["qqq"].to_numpy())
        corr = float(both["s"].corr(both["qqq"])) if len(both) > 5 else 0.0
        n_reg = sum(sl.regime_days.values()) or 1
        payload["windows"][asof] = {
            "strategy": st,
            "trades": ts,
            "spy": spy_bh,
            "qqq": qqq_bh,
            "jensen_qqq": jq,
            "corr_qqq": round(corr, 3),
            "regime_share": {k: round(100.0 * v / n_reg, 1) for k, v in sl.regime_days.items()},
            "etf_roundtrips": sl.etf_trades,
            "regime_transitions": sl.transitions,
            "leader_switches": sl.leader_switches,
            "leaders": sl.leaders[:20],
            "snapshots": snaps[:10],
            "weekly": weekly_curve(book, qqq_s),
        }
        print(f"\n===== {book.dates[0]} → {book.dates[-1]} =====")
        print(
            f"{'leader80':16} {st['total_return_pct']:+8.1f} {st['cagr_pct']:8.1f} "
            f"{st['sharpe']:8.3f} {st['mdd_pct']:8.2f} {jq['ann_alpha_pct']:+8.2f} {jq['p']:7.3f}"
        )
        print(
            f"{'QQQ BH':16} {qqq_bh['total_return_pct']:+8.1f} {qqq_bh['cagr_pct']:8.1f} "
            f"{qqq_bh['sharpe']:8.3f} {qqq_bh['mdd_pct']:8.2f}"
        )
        print(
            f"{'SPY BH':16} {spy_bh['total_return_pct']:+8.1f} {spy_bh['cagr_pct']:8.1f} "
            f"{spy_bh['sharpe']:8.3f} {spy_bh['mdd_pct']:8.2f}"
        )
        print(
            f"  excess CAGR vs QQQ {st['cagr_pct']-qqq_bh['cagr_pct']:+.1f}%p  "
            f"β={jq['beta']}  corr={corr:.3f}  flips={sl.transitions}  leader sw={sl.leader_switches}  "
            f"etf trips={sl.etf_trades}  days {dict(sl.regime_days)}"
        )
        print(f"  stock/etf reasons {ts.get('reasons', {})}")
        for line in snaps[:8]:
            print("   ", line)
        print("  leaders:", sl.leaders[:8])

    payload["meta"]["elapsed_sec"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {OUT_PATH} in {payload['meta']['elapsed_sec']}s")


if __name__ == "__main__":
    main()
