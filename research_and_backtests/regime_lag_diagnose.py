"""Diagnose where the 3-regime classifier leaks QQQ compounding."""
from __future__ import annotations

import json
import os
import sys
import warnings

import numpy as np
import pandas as pd

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
warnings.filterwarnings("ignore")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "research_and_backtests"))

from compare_html_vs_live_exit import BENCH, align_panels  # noqa: E402
from pit_dynamic_universe_exit_ab import aligned_close, rolling_rs, stock_names  # noqa: E402
from pit_universe_exit_ab import download_master, snapshot_index  # noqa: E402
from regime_slope_html import HYST_ENTER_BULL, HYST_EXIT_BULL, next_regime  # noqa: E402

OUT = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "regime_lag_diagnose.json")
START = "2018-08-31"
SLOPE = 20


def episodes(dates, labels):
    out = []
    i = 0
    n = len(labels)
    while i < n:
        j = i + 1
        while j < n and labels[j] == labels[i]:
            j += 1
        out.append((labels[i], i, j - 1, j - i))
        i = j
    return out


def ret_between(qqq, a, b):
    pa, pb = qqq[a], qqq[b]
    if not (np.isfinite(pa) and np.isfinite(pb) and pa > 0):
        return None
    return (pb / pa - 1.0) * 100.0


def main():
    raw = download_master()
    names = [t for t in stock_names() if t in raw]
    sliced = {k: v for k, v in raw.items() if k in set(names) | set(BENCH)}
    calendar, _, bench = align_panels(sliced)
    start_i = snapshot_index(calendar, START)
    spy = bench["spy"].to_numpy(dtype=float)
    sma = bench["spy_sma200"].to_numpy(dtype=float)
    qqq = bench["qqq"].to_numpy(dtype=float)
    spy_slope = rolling_rs(aligned_close(raw, "SPY", calendar), SLOPE)
    dates = [d.strftime("%Y-%m-%d") for d in calendar]

    labels = []
    regime = "chop"
    for i in range(start_i, len(calendar)):
        asof = i - 1 if i > 0 else i
        regime = next_regime(regime, spy[asof], sma[asof], spy_slope[asof])
        labels.append(regime)
    d = dates[start_i:]
    q = qqq[start_i:]
    sp = spy[start_i:]
    sm = sma[start_i:]
    sl = spy_slope[start_i:]
    n = len(labels)

    qret = np.zeros(n)
    qret[1:] = np.diff(q) / q[:-1]
    qret[~np.isfinite(qret)] = 0.0

    by = {}
    for name in ("bull", "chop", "bear"):
        m = np.array(labels) == name
        by[name] = {
            "days": int(m.sum()),
            "share_pct": round(100.0 * m.mean(), 1),
            "qqq_sum_pct": round(float(qret[m].sum()) * 100.0, 2),
            "qqq_ann_pct": round(float(qret[m].mean()) * 252 * 100.0, 2) if m.sum() else 0.0,
        }

    below = (sp < sm) & np.isfinite(sp) & np.isfinite(sm)
    recov = below & np.isfinite(sl) & (sl > 0)
    by["bear_but_slope_up"] = {
        "days": int(recov.sum()),
        "share_of_bear_pct": round(100.0 * recov.sum() / max(1, below.sum()), 1),
        "qqq_sum_pct": round(float(qret[recov].sum()) * 100.0, 2),
    }

    eps = episodes(d, labels)
    bear_eps, chop_eps, trans = [], [], 0
    prev = None
    for lab, a, b, dur in eps:
        if prev is not None and lab != prev:
            trans += 1
        prev = lab
        r = ret_between(q, a, b)
        after20 = ret_between(q, b, min(n - 1, b + 20)) if b + 1 < n else None
        after60 = ret_between(q, b, min(n - 1, b + 60)) if b + 1 < n else None
        row = {
            "start": d[a],
            "end": d[b],
            "days": dur,
            "qqq_pct": None if r is None else round(r, 2),
            "qqq_next_20d_pct": None if after20 is None else round(after20, 2),
            "qqq_next_60d_pct": None if after60 is None else round(after60, 2),
        }
        if lab == "bear":
            bear_eps.append(row)
        elif lab == "chop":
            chop_eps.append(row)

    short_bear = [e for e in bear_eps if e["days"] <= 10]
    short_chop = [e for e in chop_eps if e["days"] <= 10]

    # 200-line crosses (raw, no slope)
    above = sp >= sm
    crosses = int(np.sum(above[1:] != above[:-1]))

    payload = {
        "window": {"start": d[0], "end": d[-1], "n": n, "transitions": trans, "sma200_crosses": crosses},
        "qqq_by_regime": by,
        "bear_episodes": bear_eps,
        "chop_episodes": chop_eps,
        "short": {
            "bear_le_10d": len(short_bear),
            "chop_le_10d": len(short_chop),
            "bear_median_days": int(np.median([e["days"] for e in bear_eps])) if bear_eps else 0,
            "chop_median_days": int(np.median([e["days"] for e in chop_eps])) if chop_eps else 0,
            "bear_n": len(bear_eps),
            "chop_n": len(chop_eps),
        },
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"{d[0]} → {d[-1]}  n={n}  transitions={trans}  SMA200 crosses={crosses}")
    print("QQQ additive daily-sum by regime (not compounded):")
    for k, v in by.items():
        print(f"  {k:20} {v}")
    print(f"\nbear episodes {len(bear_eps)}  median {payload['short']['bear_median_days']}d  <=10d {len(short_bear)}")
    for e in bear_eps:
        print(f"  BEAR {e['start']} → {e['end']}  {e['days']:4}d  QQQ {e['qqq_pct']:+7.2f}%  "
              f"next20 {e['qqq_next_20d_pct']}  next60 {e['qqq_next_60d_pct']}")
    print(f"\nchop episodes {len(chop_eps)}  median {payload['short']['chop_median_days']}d  <=10d {len(short_chop)}")
    for e in chop_eps:
        flag = " *" if e["days"] <= 10 else ""
        print(f"  CHOP {e['start']} → {e['end']}  {e['days']:4}d  QQQ {e['qqq_pct']:+7.2f}%{flag}")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
