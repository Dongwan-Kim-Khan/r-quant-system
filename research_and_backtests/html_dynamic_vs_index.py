"""HTML dynamic-60 vs SPY/QQQ on matched windows. Jensen alpha + excess CAGR."""
from __future__ import annotations

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
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

sys.path.insert(0, os.path.join(PROJECT_ROOT, "research_and_backtests"))
from compare_html_vs_live_exit import (  # noqa: E402
    align_panels,
    aligned_returns,
    metrics,
    ols,
    run_book,
)
from pit_dynamic_universe_exit_ab import build_daily_universes, stock_names  # noqa: E402
from pit_universe_exit_ab import download_master, snapshot_index  # noqa: E402

SNAPSHOTS = ["2018-08-31", "2023-08-31"]
BENCH = ["SPY", "QQQ", "^VIX"]


def bh_metrics(px: pd.Series) -> dict:
    px = px.dropna()
    if len(px) < 5:
        return {}
    eq = px.to_numpy(dtype=float)
    eq = eq / eq[0] * 100_000.0
    dates = [d.strftime("%Y-%m-%d") for d in px.index]
    return metrics(list(eq), dates, initial=100_000.0)


def jensen(y: np.ndarray, x: np.ndarray) -> dict:
    X = np.column_stack([np.ones(len(y)), x])
    m = ols(y, X)
    a = m["beta"][0]
    return {
        "ann_alpha_pct": round(a * 252 * 100.0, 2),
        "t": m["t"][0],
        "p": m["p"][0],
        "beta": round(m["beta"][1], 3),
        "r2": m["r2"],
        "n": m["n"],
    }


def main() -> None:
    raw = download_master()
    names = [t for t in stock_names() if t in raw]
    sliced = {k: v for k, v in raw.items() if k in set(names) | set(BENCH)}
    calendar, panels, bench = align_panels(sliced)
    universes, _ = build_daily_universes(calendar, raw)
    spy = bench["spy"]
    qqq = bench["qqq"]
    spy.index = pd.to_datetime(spy.index)
    qqq.index = pd.to_datetime(qqq.index)

    for asof in SNAPSHOTS:
        start_i = max(snapshot_index(calendar, asof), 2)
        book = run_book("HTML", calendar, panels, bench, start_i=start_i, allowed_by_day=universes)
        st = metrics(book.equity, book.dates)
        rets = aligned_returns(book, bench)
        idx = pd.to_datetime(book.dates)
        spy_bh = bh_metrics(spy.reindex(idx))
        qqq_bh = bh_metrics(qqq.reindex(idx))
        js = jensen(rets["r"].to_numpy(), rets["spy"].to_numpy())
        jq = jensen(rets["r"].to_numpy(), rets["qqq"].to_numpy())
        print(f"\n===== {book.dates[0]} → {book.dates[-1]}  n={st['n_days']}d =====")
        print(f"{'':12} {'ret':>8} {'CAGR':>8} {'Sharpe':>8} {'MDD':>8}")
        print(f"{'HTML dyn60':12} {st['total_return_pct']:+8.1f} {st['cagr_pct']:8.1f} {st['sharpe']:8.3f} {st['mdd_pct']:8.2f}")
        print(f"{'SPY B&H':12} {spy_bh['total_return_pct']:+8.1f} {spy_bh['cagr_pct']:8.1f} {spy_bh['sharpe']:8.3f} {spy_bh['mdd_pct']:8.2f}")
        print(f"{'QQQ B&H':12} {qqq_bh['total_return_pct']:+8.1f} {qqq_bh['cagr_pct']:8.1f} {qqq_bh['sharpe']:8.3f} {qqq_bh['mdd_pct']:8.2f}")
        print(f"  excess CAGR vs SPY {st['cagr_pct']-spy_bh['cagr_pct']:+.1f}%p   vs QQQ {st['cagr_pct']-qqq_bh['cagr_pct']:+.1f}%p")
        print(f"  Jensen vs SPY  α={js['ann_alpha_pct']:+.2f}%  β={js['beta']}  t={js['t']} p={js['p']}  R2={js['r2']}")
        print(f"  Jensen vs QQQ  α={jq['ann_alpha_pct']:+.2f}%  β={jq['beta']}  t={jq['t']} p={jq['p']}  R2={jq['r2']}")


if __name__ == "__main__":
    main()
