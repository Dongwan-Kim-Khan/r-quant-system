"""
Point-in-time universe freeze to cut look-ahead/survivorship from today's watchlist.

At 2018-08-31 and 2023-08-31:
  1. Rank the 11 sector ETFs by 3M RS (same quota curve as live dynamic-60).
  2. Inside each sector, keep only names that already had a listed close that day.
  3. Take the quota of highest 3M-RS names. Freeze that set.
  4. Run HTML vs LIVE exits from that date forward on the frozen universe.

This is still not CRSP-delisting complete (acquired/failed names absent from
today's candidate book stay missing), but it stops trading 2024-26 IPOs and
today's AI-power names as if they existed in 2018.
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
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

from al_sangmoo.domain.quant.dynamic_universe import (
    DYNAMIC_SLOT_QUOTAS,
    SECTOR_ETF_MAP,
    SECTOR_MASTER_CANDIDATES,
)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "research_and_backtests"))
from compare_html_vs_live_exit import (
    FEE,
    INITIAL,
    SLIPPAGE,
    _flatten_ticker_frame,
    align_panels,
    aligned_returns,
    metrics,
    ols,
    run_book,
    trade_stats,
)

END = "2026-08-29"
DL_START = "2017-01-01"
CACHE_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "pit_master_ohlcv.pkl")
OUT_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "pit_universe_exit_ab.json")
SNAPSHOTS = ["2018-08-31", "2023-08-31"]
BENCH = ["SPY", "QQQ", "^VIX"]


def all_master_tickers() -> List[str]:
    names: List[str] = []
    for pool in SECTOR_MASTER_CANDIDATES.values():
        names.extend(pool)
    names.extend(SECTOR_ETF_MAP.values())
    names.extend(BENCH)
    return list(dict.fromkeys(names))


def download_master() -> Dict[str, pd.DataFrame]:
    import yfinance as yf

    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    if os.path.exists(CACHE_PATH):
        age_h = (time.time() - os.path.getmtime(CACHE_PATH)) / 3600.0
        if age_h < 24:
            print(f"[cache] {CACHE_PATH} ({age_h:.1f}h)")
            return pd.read_pickle(CACHE_PATH)

    tickers = all_master_tickers()
    print(f"[download] {len(tickers)} tickers {DL_START} → {END}")
    raw = yf.download(
        tickers,
        start=DL_START,
        end=END,
        interval="1d",
        auto_adjust=False,
        progress=True,
        threads=True,
        group_by="ticker",
    )
    out: Dict[str, pd.DataFrame] = {}
    for t in tickers:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if t not in raw.columns.get_level_values(0):
                    print(f"  skip missing {t}")
                    continue
                piece = raw[t]
            else:
                piece = raw
            df = _flatten_ticker_frame(piece, t)
            if df is None:
                print(f"  skip thin {t}")
                continue
            out[t] = df
        except Exception as exc:
            print(f"  skip {t}: {exc}")
    print(f"[download] kept {len(out)}")
    pd.to_pickle(out, CACHE_PATH)
    return out


def close_on(df: pd.DataFrame, asof: pd.Timestamp) -> Optional[pd.Series]:
    if df is None or df.empty or "Close" not in df.columns:
        return None
    s = df["Close"].copy()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    s = s[s.index <= asof].dropna()
    return s if len(s) else None


def rs_to_asof(series: pd.Series, lookback: int = 63) -> Optional[float]:
    if series is None or len(series) < 20:
        return None
    end = float(series.iloc[-1])
    start = float(series.iloc[max(0, len(series) - lookback)])
    if start <= 0 or not np.isfinite(end) or not np.isfinite(start):
        return None
    return ((end - start) / start) * 100.0


def listed_on(df: pd.DataFrame, asof: pd.Timestamp, min_bars: int = 20) -> bool:
    s = close_on(df, asof)
    return s is not None and len(s) >= min_bars


def build_pit_universe(raw: Dict[str, pd.DataFrame], asof_str: str) -> dict:
    asof = pd.Timestamp(asof_str)
    sector_rows = []
    for sector, etf in SECTOR_ETF_MAP.items():
        s = close_on(raw.get(etf), asof) if etf in raw else None
        rs = rs_to_asof(s) if s is not None else None
        sector_rows.append(
            {
                "sector": sector,
                "etf": etf,
                "rs_3m": round(rs, 2) if rs is not None else -999.0,
                "etf_listed": rs is not None,
            }
        )
    sector_rows.sort(key=lambda x: x["rs_3m"], reverse=True)
    for i, row in enumerate(sector_rows):
        row["rank"] = i + 1
        row["quota"] = DYNAMIC_SLOT_QUOTAS[i] if i < len(DYNAMIC_SLOT_QUOTAS) else 1

    watchlist: List[str] = []
    breakdown = []
    skipped_unlisted: Dict[str, List[str]] = {}

    for row in sector_rows:
        sector = row["sector"]
        quota = row["quota"]
        scored: List[Tuple[str, float]] = []
        unlisted: List[str] = []
        for t in SECTOR_MASTER_CANDIDATES.get(sector, []):
            df = raw.get(t)
            if df is None or not listed_on(df, asof, min_bars=20):
                unlisted.append(t)
                continue
            s = close_on(df, asof)
            rs = rs_to_asof(s)
            if rs is None:
                unlisted.append(t)
                continue
            scored.append((t, rs))
        scored.sort(key=lambda x: x[1], reverse=True)
        chosen = []
        for t, rs in scored:
            if t in watchlist:
                continue
            chosen.append({"ticker": t, "rs_3m": round(rs, 2)})
            watchlist.append(t)
            if len(chosen) >= quota or len(watchlist) >= 60:
                break
        skipped_unlisted[sector] = unlisted
        breakdown.append({**row, "selected": chosen, "unlisted_skipped": unlisted})
        if len(watchlist) >= 60:
            break

    return {
        "asof": asof_str,
        "watchlist": watchlist[:60],
        "n": len(watchlist[:60]),
        "sectors": [
            {
                "rank": b["rank"],
                "sector": b["sector"],
                "etf": b["etf"],
                "rs_3m": b["rs_3m"],
                "quota": b["quota"],
                "selected": [x["ticker"] for x in b["selected"]],
                "selected_rs": b["selected"],
            }
            for b in breakdown
        ],
        "unlisted_count": sum(len(v) for v in skipped_unlisted.values()),
    }


def slice_raw(raw: Dict[str, pd.DataFrame], names: List[str]) -> Dict[str, pd.DataFrame]:
    keep = set(names) | set(BENCH)
    return {k: v for k, v in raw.items() if k in keep}


def snapshot_index(calendar: pd.DatetimeIndex, asof_str: str) -> int:
    asof = pd.Timestamp(asof_str)
    idx = calendar.get_indexer([asof], method="pad")[0]
    if idx < 0:
        raise ValueError(f"no calendar date on/before {asof_str}")
    return int(idx)


def summarize_book(html, live, bench, pit: dict) -> dict:
    m_html = metrics(html.equity, html.dates)
    m_live = metrics(live.equity, live.dates)
    t_html = trade_stats(html.trades)
    t_live = trade_stats(live.trades)
    r_html = aligned_returns(html, bench)
    r_live = aligned_returns(live, bench)
    both = pd.DataFrame({"html": r_html["r"], "live": r_live["r"]}).dropna()
    both["qqq"] = r_html["qqq"].reindex(both.index)
    both["spy"] = r_html["spy"].reindex(both.index)
    both = both.dropna()
    y = both["live"].to_numpy()
    x_html = np.column_stack([np.ones(len(both)), both["html"].to_numpy()])
    vs_html = ols(y, x_html)
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
    qqq_rets = both["qqq"].to_numpy()
    live_mkt = ols(y, np.column_stack([np.ones(len(both)), qqq_rets]))
    html_mkt = ols(both["html"].to_numpy(), np.column_stack([np.ones(len(both)), qqq_rets]))

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

    return {
        "pit": {
            "asof": pit["asof"],
            "n": pit["n"],
            "watchlist": pit["watchlist"],
            "sectors": pit["sectors"],
            "unlisted_count": pit["unlisted_count"],
        },
        "html": {**m_html, "trades": t_html},
        "live": {**m_live, "trades": t_live},
        "ols_live_on_html": {**vs_html, "alpha": alpha, "spec": "R_live = a + b*R_html + e"},
        "ols_vs_qqq": {"html": ann(html_mkt), "live": ann(live_mkt)},
        "verdict": verdict,
    }


def main() -> None:
    t0 = time.time()
    raw = download_master()
    if "SPY" not in raw or "QQQ" not in raw:
        raise SystemExit("missing SPY/QQQ")

    payload = {
        "meta": {
            "method": (
                "Freeze dynamic-60 at snapshot using sector 3M RS quotas; "
                "drop names not listed yet; HTML vs LIVE exits from snapshot to 2026-08-28."
            ),
            "caveat": (
                "Does not restore delisted/acquired names missing from today's master book. "
                "Removes future IPOs and 2026-watchlist look-ahead."
            ),
            "initial": INITIAL,
            "friction": {"slippage_bps": SLIPPAGE * 10000, "fee_bps": FEE * 10000},
        },
        "windows": {},
    }

    for asof in SNAPSHOTS:
        pit = build_pit_universe(raw, asof)
        print(f"\n===== PIT {asof} n={pit['n']} unlisted_skipped={pit['unlisted_count']} =====")
        for s in pit["sectors"]:
            if s["selected"]:
                print(
                    f"  #{s['rank']} {s['sector']:16} ETF {s['etf']} RS {s['rs_3m']:+6.1f} "
                    f"quota {s['quota']} -> {', '.join(s['selected'])}"
                )
        print("  universe:", ", ".join(pit["watchlist"]))

        sliced = slice_raw(raw, pit["watchlist"])
        calendar, panels, bench = align_panels(sliced)
        start_i = snapshot_index(calendar, asof)
        # need one prior bar for entry_signal(i-1)
        start_i = max(start_i, 2)
        print(f"  trade from {calendar[start_i].date()} idx={start_i}/{len(calendar)} names={len(panels)}")

        html = run_book("HTML", calendar, panels, bench, start_i=start_i)
        live = run_book("LIVE", calendar, panels, bench, start_i=start_i)
        block = summarize_book(html, live, bench, pit)
        payload["windows"][asof] = block

        h, l = block["html"], block["live"]
        a = block["ols_live_on_html"]["alpha"]
        print(f"  HTML  ret {h['total_return_pct']:+.1f}%  CAGR {h['cagr_pct']:.1f}%  Sharpe {h['sharpe']}  MDD {h['mdd_pct']}%  n={h['trades']['n']}")
        print(f"  LIVE  ret {l['total_return_pct']:+.1f}%  CAGR {l['cagr_pct']:.1f}%  Sharpe {l['sharpe']}  MDD {l['mdd_pct']}%  n={l['trades']['n']}")
        print(f"  LIVE vs HTML alpha ann {a['annualized_pct']}%  t={a['t']} p={a['p']}  verdict={block['verdict']}")
        print(f"  LIVE kijun exits: {l['trades']['reasons'].get('KIJUN', {})}")

    payload["meta"]["elapsed_sec"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {OUT_PATH} in {payload['meta']['elapsed_sec']}s")


if __name__ == "__main__":
    main()
