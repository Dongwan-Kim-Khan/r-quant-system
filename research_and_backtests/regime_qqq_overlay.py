"""
QQQ-core overlay. Mandate: stay close to QQQ, cheap crash sleeve, no 2026-or-bust.

  - Default risk-on: ~100% QQQ.
  - Overlay 30% into the 20d-slope sector leader only if that slope > QQQ 20d slope.
    Re-picked on month-open (and on bear→bull).
  - Bear entry: 5 closes below SMA200 AND slope<=0, OR 3% below SMA200.
    Fast exit: SMA200 reclaim OR SPY>SMA50 OR 20d slope>0 (no instant 5-day re-entry
    while slope stays positive).
  - Bear: 50% QQQ floor, 50% cash. No Engine B.
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

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

from al_sangmoo.domain.quant.dynamic_universe import SECTOR_ETF_MAP

sys.path.insert(0, os.path.join(PROJECT_ROOT, "research_and_backtests"))
from compare_html_vs_live_exit import (  # noqa: E402
    BENCH,
    FEE,
    INITIAL,
    Book,
    _fill_buy,
    _fill_sell,
    align_panels,
    metrics,
    ols,
    trade_stats,
)
from pit_dynamic_universe_exit_ab import aligned_close, rolling_rs  # noqa: E402
from pit_universe_exit_ab import download_master, snapshot_index  # noqa: E402
from regime_bull_core import aligned_open, bh_metrics, jensen, weekly_curve  # noqa: E402
from regime_leader80 import (  # noqa: E402
    BEAR_DAYS,
    BEAR_DEEP,
    ETF_NAME,
    SLOPE_BARS,
    SNAPSHOTS,
    EtfPos,
    Sleeve,
    days_below_sma,
    is_month_open,
    ranked_leaders,
)

OUT_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "regime_qqq_overlay.json")
OVERLAY_FRAC = 0.30
QQQ_ON = 0.99
QQQ_OVERLAY = 0.70
QQQ_BEAR = 0.50


def next_regime(prev: str, spy: float, sma200: float, sma50: float, slope20: float, n_below: int) -> str:
    if not np.isfinite(spy) or not np.isfinite(sma200) or sma200 <= 0:
        return prev if prev in ("bull", "bear") else "bull"
    slope = float(slope20) if np.isfinite(slope20) else 0.0
    deep = spy < sma200 * (1.0 - BEAR_DEEP)
    above50 = np.isfinite(sma50) and sma50 > 0 and spy > sma50
    if prev == "bear":
        if spy >= sma200 or above50 or (slope > 0.0 and not deep):
            return "bull"
        return "bear"
    if deep or (n_below >= BEAR_DAYS and slope <= 0.0):
        return "bear"
    return "bull"


def overlay_pick(
    asof: int, etf_slope: Dict[str, np.ndarray], qqq_slope: np.ndarray
) -> Tuple[str, float, float]:
    ranked = ranked_leaders(asof, etf_slope)
    q = float(qqq_slope[asof]) if asof < len(qqq_slope) and np.isfinite(qqq_slope[asof]) else -1e9
    if not ranked:
        return "", -1e9, q
    top_s, top = ranked[0]
    if top_s > q:
        return top, top_s, q
    return "", top_s, q


def run_book(
    calendar: pd.DatetimeIndex,
    panels: Dict[str, dict],
    bench: pd.DataFrame,
    etf_open: Dict[str, np.ndarray],
    etf_close: Dict[str, np.ndarray],
    etf_slope: Dict[str, np.ndarray],
    qqq_slope: np.ndarray,
    spy_slope: np.ndarray,
    sma50: np.ndarray,
    start_i: int,
) -> Tuple[Book, Sleeve, List[str]]:
    n = len(calendar)
    dates = [d.strftime("%Y-%m-%d") for d in calendar]
    spy = bench["spy"].to_numpy(dtype=float)
    sma200 = bench["spy_sma200"].to_numpy(dtype=float)
    book = Book(cash=INITIAL)
    sl = Sleeve()
    sl.overlay_days = 0  # type: ignore[attr-defined]
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
        eq = book.cash
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

    def set_weight(ticker: str, i: int, target_frac: float, reason: str) -> None:
        if target_frac <= 0:
            sell_etf(ticker, i, reason)
            return
        pos = sl.etfs.setdefault(ticker, EtfPos(ticker=ticker))
        eq = equity_now(i)
        mark = etf_px(ticker, i, "close")
        if mark <= 0:
            mark = etf_px(ticker, i, "open")
        if mark <= 0:
            return
        cur_val = pos.shares * mark if pos.shares > 0 else 0.0
        target_val = eq * target_frac
        if cur_val > target_val * 1.02:
            sell_shares = min(pos.shares, (cur_val - target_val) / mark)
            fill = _fill_sell(mark)
            book.cash += sell_shares * fill * (1.0 - FEE)
            pos.shares -= sell_shares
            sl.etf_trades += 1
            if pos.shares <= 1e-9:
                pos.shares = 0.0
                pos.entry = 0.0
            return
        need = target_val - cur_val
        raw = etf_px(ticker, i, "open")
        if raw <= 0 or need < raw:
            return
        fill = _fill_buy(raw)
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

    def apply_alloc(i: int, qqq_w: float, lead: str, lead_w: float, reason: str) -> None:
        nonlocal leader
        for tk in list(sl.etfs.keys()):
            if tk not in ("QQQ", lead):
                sell_etf(tk, i, reason)
        if not lead or lead_w <= 0:
            for tk in list(sl.etfs.keys()):
                if tk != "QQQ":
                    sell_etf(tk, i, reason)
            leader = ""
            set_weight("QQQ", i, qqq_w, reason)
            return
        q_mark = etf_px("QQQ", i, "close")
        q_pos = sl.etfs.get("QQQ")
        q_val = q_pos.shares * q_mark if q_pos and q_pos.shares > 0 and q_mark > 0 else 0.0
        eq = equity_now(i)
        if q_val > eq * qqq_w * 1.02:
            set_weight("QQQ", i, qqq_w, reason)
            set_weight(lead, i, lead_w, reason)
        else:
            set_weight(lead, i, lead_w, reason)
            set_weight("QQQ", i, qqq_w, reason)
        leader = lead

    def desired(asof: int, new_reg: str) -> Tuple[float, str, float]:
        if new_reg == "bear":
            return QQQ_BEAR, "", 0.0
        top, _, _ = overlay_pick(asof, etf_slope, qqq_slope)
        if top:
            return QQQ_OVERLAY, top, OVERLAY_FRAC
        return QQQ_ON, "", 0.0

    for i in range(start_i, n):
        asof = i - 1
        n_below = days_below_sma(spy, sma200, asof)
        new_regime = next_regime(
            regime, spy[asof], sma200[asof], sma50[asof], spy_slope[asof], n_below
        )
        month_open = is_month_open(dates, i, start_i)
        q_w, lead, l_w = desired(asof, new_regime)
        need = False
        reason = "REB"
        if i == start_i:
            need, reason = True, "START"
        elif new_regime != regime:
            sl.transitions += 1
            need, reason = True, "BEAR_EXIT" if new_regime == "bear" else "LEAVE_BEAR"
        elif new_regime == "bull" and month_open and lead != leader:
            sl.leader_switches += 1
            need, reason = True, "OVERLAY_ON" if lead else "OVERLAY_OFF"

        if need:
            apply_alloc(i, q_w, lead, l_w, reason)
            if lead:
                sl.leaders.append(f"{dates[i]} {lead} {ETF_NAME.get(lead, lead)}")
            elif reason in ("OVERLAY_OFF", "START", "LEAVE_BEAR"):
                sl.leaders.append(f"{dates[i]} QQQ_ONLY")

        regime = new_regime
        sl.regime_days[regime] += 1
        if regime == "bull" and leader:
            sl.overlay_days += 1  # type: ignore[attr-defined]

        if dates[i] in SNAPSHOTS or dates[i].endswith("-08-31") or dates[i].endswith("-02-28"):
            top, ts, qs = overlay_pick(asof, etf_slope, qqq_slope)
            held = ",".join(p.ticker for p in sl.etfs.values() if p.shares > 0)
            slp = spy_slope[asof] if np.isfinite(spy_slope[asof]) else 0.0
            snaps.append(
                f"{dates[i]} {regime} below={n_below}d slope={slp:+.2f} "
                f"held={held or '-'} ov={top or 'off'}({ts:+.1f} vs QQQ {qs:+.1f})"
            )

        book.equity.append(equity_now(i))
        book.dates.append(dates[i])

    last = n - 1
    for tk in list(sl.etfs.keys()):
        sell_etf(tk, last, "EOB")
    return book, sl, snaps


def yearly_excess(book: Book, qqq: pd.Series) -> List[dict]:
    idx = pd.to_datetime(book.dates)
    r = pd.Series(book.equity, index=idx).pct_change()
    rq = qqq.reindex(idx).pct_change()
    df = pd.DataFrame({"r": r, "qqq": rq}).dropna()
    y = df.resample("YE").apply(lambda s: (1.0 + s).prod() - 1.0).dropna()
    rows = []
    for dt, row in y.iterrows():
        rows.append(
            {
                "year": int(dt.year),
                "strat_pct": round(float(row["r"]) * 100.0, 2),
                "qqq_pct": round(float(row["qqq"]) * 100.0, 2),
                "excess_pct": round(float(row["r"] - row["qqq"]) * 100.0, 2),
            }
        )
    return rows


def main() -> None:
    t0 = time.time()
    raw = download_master()
    extra = set(BENCH) | set(SECTOR_ETF_MAP.values()) | {"QQQ"}
    sliced = {k: v for k, v in raw.items() if k in extra}
    calendar, panels, bench = align_panels(sliced)

    spy_c = aligned_close(raw, "SPY", calendar)
    qqq_c = aligned_close(raw, "QQQ", calendar)
    spy_slope = rolling_rs(spy_c, SLOPE_BARS)
    qqq_slope = rolling_rs(qqq_c, SLOPE_BARS)
    etf_slope = {etf: rolling_rs(aligned_close(raw, etf, calendar), SLOPE_BARS) for etf in SECTOR_ETF_MAP.values()}
    sma50 = pd.Series(spy_c).rolling(50, min_periods=15).mean().to_numpy(dtype=float)
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
                "QQQ core; 30% sector overlay iff leader 20d slope > QQQ; "
                "monthly; bear 50% QQQ after 5d&slope<=0 or 3% below 200; "
                "fast exit slope>0 or SMA50; no Engine B"
            ),
            "overlay_frac": OVERLAY_FRAC,
            "qqq_bear": QQQ_BEAR,
        },
        "windows": {},
    }

    print(f"{'':18} {'ret':>8} {'CAGR':>8} {'Sharpe':>8} {'MDD':>8} {'αQQQ':>8} {'p':>7} {'IR':>7}")
    for asof in SNAPSHOTS:
        start_i = snapshot_index(calendar, asof)
        book, sl, snaps = run_book(
            calendar, panels, bench, etf_open, etf_close, etf_slope, qqq_slope, spy_slope, sma50, start_i
        )
        st = metrics(book.equity, book.dates)
        ts = trade_stats(book.trades)
        idx = pd.to_datetime(book.dates)
        spy_bh = bh_metrics(spy_s.reindex(idx))
        qqq_bh = bh_metrics(qqq_s.reindex(idx))
        r = pd.Series(book.equity, index=idx).pct_change()
        rq = qqq_s.reindex(idx).pct_change()
        both = pd.DataFrame({"s": r, "qqq": rq}).dropna()
        jq = jensen(both["s"].to_numpy(), both["qqq"].to_numpy())
        excess = both["s"] - both["qqq"]
        ir = float(excess.mean() / excess.std() * np.sqrt(252)) if excess.std() > 0 else 0.0
        corr = float(both["s"].corr(both["qqq"]))
        n_reg = sum(sl.regime_days.values()) or 1
        ov_share = round(100.0 * getattr(sl, "overlay_days", 0) / n_reg, 1)
        years = yearly_excess(book, qqq_s)
        payload["windows"][asof] = {
            "strategy": st,
            "trades": ts,
            "spy": spy_bh,
            "qqq": qqq_bh,
            "jensen_qqq": jq,
            "corr_qqq": round(corr, 3),
            "ir_qqq": round(ir, 3),
            "regime_share": {k: round(100.0 * v / n_reg, 1) for k, v in sl.regime_days.items()},
            "overlay_share_pct": ov_share,
            "etf_roundtrips": sl.etf_trades,
            "regime_transitions": sl.transitions,
            "leader_switches": sl.leader_switches,
            "leaders": sl.leaders[:16],
            "snapshots": snaps[:8],
            "yearly": years,
            "weekly": weekly_curve(book, qqq_s),
        }
        print(f"\n===== {book.dates[0]} → {book.dates[-1]} =====")
        print(
            f"{'qqq_overlay':18} {st['total_return_pct']:+8.1f} {st['cagr_pct']:8.1f} "
            f"{st['sharpe']:8.3f} {st['mdd_pct']:8.2f} {jq['ann_alpha_pct']:+8.2f} {jq['p']:7.3f} {ir:7.3f}"
        )
        print(
            f"{'QQQ BH':18} {qqq_bh['total_return_pct']:+8.1f} {qqq_bh['cagr_pct']:8.1f} "
            f"{qqq_bh['sharpe']:8.3f} {qqq_bh['mdd_pct']:8.2f}"
        )
        print(
            f"  excess CAGR {st['cagr_pct']-qqq_bh['cagr_pct']:+.1f}%p  β={jq['beta']}  corr={corr:.3f}  "
            f"flips={sl.transitions}  ov days={ov_share}%  sw={sl.leader_switches}  "
            f"bear={dict(sl.regime_days)}"
        )
        print(f"  trades n={ts['n']} PF={ts.get('profit_factor')} win={ts.get('win_rate')}  {ts.get('reasons')}")
        for row in years:
            print(f"    {row['year']}  {row['strat_pct']:+7.2f}  {row['qqq_pct']:+7.2f}  {row['excess_pct']:+7.2f}")
        for line in snaps[:6]:
            print("   ", line)
        print("  overlay log:", sl.leaders[:8])

    payload["meta"]["elapsed_sec"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {OUT_PATH} in {payload['meta']['elapsed_sec']}s")


if __name__ == "__main__":
    main()
