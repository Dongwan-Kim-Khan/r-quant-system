"""Diagnose why tasty static-60 HTML != realistic dynamic-60 HTML."""
from __future__ import annotations

import os
import sys
import warnings
from collections import Counter, defaultdict

warnings.filterwarnings("ignore")
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "research_and_backtests"))

from al_sangmoo.core.constants import WATCHLIST
from compare_html_vs_live_exit import align_panels, run_book
from pit_dynamic_universe_exit_ab import build_daily_universes, stock_names
from pit_universe_exit_ab import download_master, snapshot_index

BENCH = ["SPY", "QQQ", "^VIX"]
WL = set(WATCHLIST)


def main() -> None:
    raw = download_master()
    names = [t for t in stock_names() if t in raw]
    sliced = {k: v for k, v in raw.items() if k in set(names) | set(BENCH)}
    calendar, panels, bench = align_panels(sliced)
    universes, snaps = build_daily_universes(calendar, raw)

    for asof in ("2018-08-31", "2023-08-31"):
        start_i = max(snapshot_index(calendar, asof), 2)
        book = run_book("HTML", calendar, panels, bench, start_i=start_i, allowed_by_day=universes)
        n_days = len(calendar) - start_i
        nvda_days = sum(1 for i in range(start_i, len(calendar)) if universes[i] and "NVDA" in universes[i])
        tickers = [t["ticker"] for t in book.trades]
        c = Counter(tickers)
        reasons = defaultdict(int)
        for t in book.trades:
            reasons[t["reason"]] += 1
        bars = sum(t["bars"] for t in book.trades)
        util = bars / (3.0 * n_days)
        in_wl = sum(1 for t in tickers if t in WL)
        print(f"\n{asof} -> {calendar[-1].date()} days={n_days}")
        print(f"  NVDA in dynamic-60: {100.0 * nvda_days / n_days:.1f}% of sessions")
        print(f"  slot-days used / 3 slots: {100.0 * util:.1f}%  (rest is cash/empty slot)")
        print(f"  trades={len(book.trades)} unique={len(c)}  in today's WATCHLIST {in_wl}/{len(book.trades)}")
        print(f"  reasons={dict(reasons)}")
        print(f"  most traded: {c.most_common(10)}")

    print("\n2023-08-31 sector snapshot (dynamic-60 start of AI window):")
    hit = next(s for s in snaps if s["date"] >= "2023-08-31")
    for row in hit["sectors"]:
        print(
            f"  #{row['rank']} {row['sector']:16} RS {row['rs_3m']:+6.1f} "
            f"quota {row['quota']:2} {row['selected']}"
        )


if __name__ == "__main__":
    main()
