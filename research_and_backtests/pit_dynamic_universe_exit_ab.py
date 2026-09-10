"""
PIT start + live-style dynamic sector universe thereafter.

Starts at 2018-08-31 and 2023-08-31 (no trading before).
Each session's tradable 60 is rebuilt from 11-sector 3M RS quotas using
only names already listed as of the prior close — IPOs enter when they
exist, not before. Holdings are not force-sold on universe drop; new
entries must be in that day's 60.

HTML vs LIVE exits, same as the other A/B tests.
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
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

from al_sangmoo.domain.quant.dynamic_universe import (
    DYNAMIC_SLOT_QUOTAS,
    SECTOR_ETF_MAP,
    SECTOR_MASTER_CANDIDATES,
)

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
from pit_universe_exit_ab import download_master, snapshot_index  # noqa: E402

OUT_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "pit_dynamic_universe_exit_ab.json")
SNAPSHOTS = ["2018-08-31", "2023-08-31"]
BENCH = ["SPY", "QQQ", "^VIX"]
RS_BARS = 63
MIN_LISTED = 20


def stock_names() -> List[str]:
    names: List[str] = []
    for pool in SECTOR_MASTER_CANDIDATES.values():
        names.extend(pool)
    return list(dict.fromkeys(names))


def aligned_close(raw: Dict[str, pd.DataFrame], ticker: str, calendar: pd.DatetimeIndex) -> np.ndarray:
    df = raw.get(ticker)
    if df is None or df.empty:
        return np.full(len(calendar), np.nan)
    s = df["Close"].copy()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    s = s.reindex(calendar)
    return s.to_numpy(dtype=float)


def rolling_rs(close: np.ndarray, lookback: int = RS_BARS) -> np.ndarray:
    n = len(close)
    out = np.full(n, np.nan)
    if n <= lookback:
        return out
    start = close[:-lookback]
    end = close[lookback:]
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = (end - start) / start * 100.0
    rs[~np.isfinite(start) | (start <= 0) | ~np.isfinite(end)] = np.nan
    out[lookback:] = rs
    return out


def listed_mask(close: np.ndarray, min_bars: int = MIN_LISTED) -> np.ndarray:
    finite = np.isfinite(close) & (close > 0)
    cs = np.cumsum(finite.astype(np.int32))
    count = cs.copy()
    if len(close) > min_bars:
        count[min_bars:] = cs[min_bars:] - cs[:-min_bars]
    return finite & (count >= min_bars)


def build_daily_universes(
    calendar: pd.DatetimeIndex,
    raw: Dict[str, pd.DataFrame],
) -> Tuple[List[Optional[Set[str]]], List[dict]]:
    """Universe for day i uses RS through i-1 (known at the open of i)."""
    n = len(calendar)
    etf_rs = {}
    etf_ok = {}
    for etf in SECTOR_ETF_MAP.values():
        c = aligned_close(raw, etf, calendar)
        etf_rs[etf] = rolling_rs(c)
        etf_ok[etf] = listed_mask(c, min_bars=15)

    tickers = [t for t in stock_names() if t in raw]
    stock_rs = {t: rolling_rs(aligned_close(raw, t, calendar)) for t in tickers}
    stock_ok = {t: listed_mask(aligned_close(raw, t, calendar)) for t in tickers}

    universes: List[Optional[Set[str]]] = [None] * n
    snapshots: List[dict] = []

    for i in range(1, n):
        asof = i - 1
        ranked = []
        for sector, etf in SECTOR_ETF_MAP.items():
            rs = etf_rs[etf][asof] if etf_ok[etf][asof] else np.nan
            ranked.append((float(rs) if np.isfinite(rs) else -999.0, sector, etf))
        ranked.sort(reverse=True)

        picked: List[str] = []
        seen: Set[str] = set()
        sector_sel = []
        for rank, (rs, sector, etf) in enumerate(ranked):
            quota = DYNAMIC_SLOT_QUOTAS[rank] if rank < len(DYNAMIC_SLOT_QUOTAS) else 1
            scored = []
            for t in SECTOR_MASTER_CANDIDATES.get(sector, []):
                if t not in stock_ok or not stock_ok[t][asof]:
                    continue
                srs = stock_rs[t][asof]
                scored.append((float(srs) if np.isfinite(srs) else -999.0, t))
            scored.sort(reverse=True)
            chosen = []
            for _, t in scored:
                if t in seen:
                    continue
                chosen.append(t)
                seen.add(t)
                picked.append(t)
                if len(chosen) >= quota:
                    break
                if len(picked) >= 60:
                    break
            sector_sel.append(
                {
                    "rank": rank + 1,
                    "sector": sector,
                    "etf": etf,
                    "rs_3m": round(rs, 2),
                    "quota": quota,
                    "selected": chosen,
                }
            )
            if len(picked) >= 60:
                break

        universes[i] = set(picked[:60])
        d = calendar[i].strftime("%Y-%m-%d")
        if d in SNAPSHOTS or d.endswith("-08-31") or d.endswith("-02-28") or d.endswith("-02-29"):
            snapshots.append({"date": d, "n": len(universes[i]), "sectors": sector_sel, "watchlist": picked[:60]})

    return universes, snapshots


def summarize(html, live, bench, extra: dict) -> dict:
    m_html = metrics(html.equity, html.dates)
    m_live = metrics(live.equity, live.dates)
    t_html = trade_stats(html.trades)
    t_live = trade_stats(live.trades)
    r_html = aligned_returns(html, bench)
    r_live = aligned_returns(live, bench)
    both = pd.DataFrame({"html": r_html["r"], "live": r_live["r"]}).dropna()
    both["qqq"] = r_html["qqq"].reindex(both.index)
    both = both.dropna()
    y = both["live"].to_numpy()
    vs_html = ols(y, np.column_stack([np.ones(len(both)), both["html"].to_numpy()]))
    daily_a = vs_html["beta"][0]
    alpha = {
        "daily_pct": round(daily_a * 100.0, 4),
        "annualized_pct": round(daily_a * 252 * 100.0, 2),
        "t": vs_html["t"][0],
        "p": vs_html["p"][0],
    }
    if alpha["annualized_pct"] > 0 and alpha["p"] < 0.10:
        verdict = "KEEP_LIVE"
    elif alpha["annualized_pct"] > 0:
        verdict = "WEAK_KEEP"
    else:
        verdict = "REVERT_HTML"
    qqq = both["qqq"].to_numpy()
    xq = np.column_stack([np.ones(len(both)), qqq])

    def ann(m):
        d = m["beta"][0]
        return {
            "daily_pct": round(d * 100.0, 4),
            "annualized_pct": round(d * 252 * 100.0, 2),
            "t": m["t"][0],
            "p": m["p"][0],
            "beta_qqq": m["beta"][1],
            "r2": m["r2"],
        }

    html_names = sorted({t["ticker"] for t in html.trades})
    live_names = sorted({t["ticker"] for t in live.trades})
    return {
        **extra,
        "html": {**m_html, "trades": t_html, "traded_names": html_names},
        "live": {**m_live, "trades": t_live, "traded_names": live_names},
        "ols_live_on_html": {**vs_html, "alpha": alpha, "spec": "R_live = a + b*R_html + e"},
        "ols_vs_qqq": {"html": ann(ols(both["html"].to_numpy(), xq)), "live": ann(ols(y, xq))},
        "verdict": verdict,
    }


def sector_line(snap: dict) -> str:
    parts = []
    for s in snap.get("sectors", [])[:6]:
        names = ",".join(s.get("selected") or [])
        parts.append(f"#{s['rank']} {s['sector']}({s['rs_3m']:+.1f} n={len(s.get('selected') or [])}): {names}")
    return " | ".join(parts)


def main() -> None:
    t0 = time.time()
    raw = download_master()
    names = [t for t in stock_names() if t in raw]
    sliced = {k: v for k, v in raw.items() if k in set(names) | set(BENCH)}
    calendar, panels, bench = align_panels(sliced)
    print(f"[panel] {len(panels)} names, {len(calendar)} days")
    universes, snaps = build_daily_universes(calendar, raw)
    print(f"[universe] daily 60 ready, checkpoint snaps={len(snaps)}")

    payload = {
        "meta": {
            "method": (
                "Start at snapshot. Each session rebuilds dynamic-60 from prior-close "
                "sector 3M RS quotas. Unlisted names cannot enter until listed. "
                "HTML vs LIVE exits."
            ),
            "initial": INITIAL,
            "friction": {"slippage_bps": SLIPPAGE * 10000, "fee_bps": FEE * 10000},
        },
        "windows": {},
    }

    for asof in SNAPSHOTS:
        start_i = max(snapshot_index(calendar, asof), 2)
        u0 = universes[start_i] or set()
        mid_i = min(start_i + 252, len(calendar) - 1)
        end_i = len(calendar) - 1
        print(f"\n===== DYNAMIC from {asof} ({calendar[start_i].date()}) n0={len(u0)} =====")
        print("  start:", ", ".join(sorted(u0)[:20]), "...")
        if universes[mid_i]:
            print("  +1y  :", ", ".join(sorted(universes[mid_i])[:20]), "...")
        print("  end  :", ", ".join(sorted(universes[end_i] or set())[:20]), "...")

        html = run_book("HTML", calendar, panels, bench, start_i=start_i, allowed_by_day=universes)
        live = run_book("LIVE", calendar, panels, bench, start_i=start_i, allowed_by_day=universes)

        def pack_snap(i):
            d = calendar[i].strftime("%Y-%m-%d")
            u = universes[i] or set()
            hit = next((s for s in snaps if s["date"] == d), None)
            return {
                "date": d,
                "n": len(u),
                "watchlist": sorted(u),
                "sectors": (hit or {}).get("sectors"),
            }

        ever = set()
        for i in range(start_i, len(calendar)):
            if universes[i]:
                ever |= universes[i]

        block = summarize(
            html,
            live,
            bench,
            {
                "start": pack_snap(start_i),
                "plus_1y": pack_snap(mid_i),
                "end": pack_snap(end_i),
                "unique_universe_names": len(ever),
            },
        )
        payload["windows"][asof] = block
        h, l = block["html"], block["live"]
        a = block["ols_live_on_html"]["alpha"]
        print(
            f"  HTML ret {h['total_return_pct']:+.1f}% CAGR {h['cagr_pct']:.1f}% "
            f"Sharpe {h['sharpe']} MDD {h['mdd_pct']}% n={h['trades']['n']}"
        )
        print(
            f"  LIVE ret {l['total_return_pct']:+.1f}% CAGR {l['cagr_pct']:.1f}% "
            f"Sharpe {l['sharpe']} MDD {l['mdd_pct']}% n={l['trades']['n']}"
        )
        print(f"  LIVE vs HTML alpha ann {a['annualized_pct']}% t={a['t']} p={a['p']} verdict={block['verdict']}")
        print(f"  unique names in rotating 60: {len(ever)}; HTML traded {len(h['traded_names'])} LIVE {len(l['traded_names'])}")
        print("  LIVE kijun", l["trades"]["reasons"].get("KIJUN", {}))

    payload["meta"]["elapsed_sec"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {OUT_PATH} in {payload['meta']['elapsed_sec']}s")


if __name__ == "__main__":
    main()
