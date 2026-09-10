"""
HTML-spec exits only. Compare original static 60 (WATCHLIST) vs live-style
dynamic sector 60, starting 2018-08-31 and 2023-08-31.

No LIVE/kijun-anytime variant. Question: does dynamic-universe HTML have
alpha versus the original HTML backtest universe.
"""
from __future__ import annotations

import json
import os
import sys
import time
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

from al_sangmoo.core.constants import WATCHLIST

sys.path.insert(0, os.path.join(PROJECT_ROOT, "research_and_backtests"))
from compare_html_vs_live_exit import (  # noqa: E402
    FEE,
    INITIAL,
    SLIPPAGE,
    align_panels,
    aligned_returns,
    metrics,
    ols,
    run_book,
    trade_stats,
)
from pit_dynamic_universe_exit_ab import build_daily_universes, stock_names  # noqa: E402
from pit_universe_exit_ab import download_master, snapshot_index  # noqa: E402

OUT_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "html_static_vs_dynamic.json")
SNAPSHOTS = ["2018-08-31", "2023-08-31"]
BENCH = ["SPY", "QQQ", "^VIX"]


def pack(book, bench):
    m = metrics(book.equity, book.dates)
    t = trade_stats(book.trades)
    r = aligned_returns(book, bench)
    return m, t, r


def vs_ols(y_dyn: pd.Series, y_html: pd.Series) -> dict:
    both = pd.DataFrame({"dyn": y_dyn, "html": y_html}).dropna()
    y = both["dyn"].to_numpy()
    x = np.column_stack([np.ones(len(both)), both["html"].to_numpy()])
    model = ols(y, x)
    a = model["beta"][0]
    return {
        **model,
        "alpha": {
            "daily_pct": round(a * 100.0, 4),
            "annualized_pct": round(a * 252 * 100.0, 2),
            "t": model["t"][0],
            "p": model["p"][0],
        },
        "beta_html": model["beta"][1],
        "spec": "R_dynamic = a + b * R_html_static + e",
    }


def main() -> None:
    t0 = time.time()
    raw = download_master()
    names = [t for t in stock_names() if t in raw]
    all_raw = {k: v for k, v in raw.items() if k in set(names) | set(BENCH)}
    calendar, panels_all, bench = align_panels(all_raw)
    static_names = [t for t in WATCHLIST if t in panels_all]
    panels_static = {t: panels_all[t] for t in static_names}
    universes, _ = build_daily_universes(calendar, raw)
    print(f"static n={len(panels_static)}  master n={len(panels_all)}  days={len(calendar)}")

    payload = {
        "meta": {
            "html_exit": "-4% hard stop OR +15% peak then trailing. Hold otherwise. No kijun-anytime.",
            "baseline": "Original HTML backtest universe = today's static WATCHLIST 60",
            "challenger": "Daily dynamic-60 from prior-close sector 3M RS (listed names only)",
            "initial": INITIAL,
            "friction": {"slippage_bps": SLIPPAGE * 10000, "fee_bps": FEE * 10000},
        },
        "windows": {},
    }

    for asof in SNAPSHOTS:
        start_i = max(snapshot_index(calendar, asof), 2)
        html_static = run_book("HTML", calendar, panels_static, bench, start_i=start_i)
        html_dyn = run_book(
            "HTML", calendar, panels_all, bench, start_i=start_i, allowed_by_day=universes
        )
        ms, ts, rs = pack(html_static, bench)
        md, td, rd = pack(html_dyn, bench)
        cmp_ = vs_ols(rd["r"], rs["r"])
        a = cmp_["alpha"]
        if a["annualized_pct"] > 0 and a["p"] < 0.10:
            verdict = "DYNAMIC_HAS_ALPHA"
        elif a["annualized_pct"] > 0:
            verdict = "DYNAMIC_POSITIVE_NS"
        else:
            verdict = "STATIC_HTML_AHEAD"
        payload["windows"][asof] = {
            "html_static": {**ms, "trades": ts, "universe": static_names},
            "html_dynamic": {**md, "trades": td},
            "ols_dynamic_on_static": cmp_,
            "verdict": verdict,
        }
        print(f"\n===== HTML-only from {asof} =====")
        print(f"  static60  ret {ms['total_return_pct']:+.1f}% CAGR {ms['cagr_pct']:.1f}% Sharpe {ms['sharpe']} MDD {ms['mdd_pct']}% n={ts['n']}")
        print(f"  dynamic60 ret {md['total_return_pct']:+.1f}% CAGR {md['cagr_pct']:.1f}% Sharpe {md['sharpe']} MDD {md['mdd_pct']}% n={td['n']}")
        print(f"  dyn vs static α ann {a['annualized_pct']}% t={a['t']} p={a['p']} β={cmp_['beta_html']} R2={cmp_['r2']}  {verdict}")

    payload["meta"]["elapsed_sec"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {OUT_PATH} in {payload['meta']['elapsed_sec']}s")


if __name__ == "__main__":
    main()
