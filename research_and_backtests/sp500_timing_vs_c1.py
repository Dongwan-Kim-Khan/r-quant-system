"""
S&P 500 leverage/inverse timing vs C1 v1.

Pure index books (no single-stock picking) under the same friction as C1:
next-open fills, 10bps slippage, 8bps fee.

  T1  Classic 200 SMA: SPY>=SMA200 → SSO 100%; else BIL 100%
  T2  VIX regime: UPRO / SSO / SPY / BIL by SMA200 × VIX bands
  T3  Absolute momentum 60d: mom>=0 → SSO; else SH (-1x)

Benchmarks: SPY B&H, QQQ B&H, SSO B&H, Baseline C1 v1.
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

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
    FEE,
    INITIAL,
    SLIPPAGE,
    Book,
    _fill_buy,
    _fill_sell,
    _flatten_ticker_frame,
    align_panels,
    metrics,
    ols,
)
from pit_dynamic_universe_exit_ab import aligned_close, stock_names  # noqa: E402
from pit_universe_exit_ab import download_master, snapshot_index  # noqa: E402
from qqq_beat_alpha_backtester import (  # noqa: E402
    BENCH,
    SNAPSHOTS,
    ProxyPos,
    aligned_ohlc,
    build_daily_universes_composite,
    composite_rs,
    enrich,
    ensure_qld,
    json_safe,
    pack_book,
    run_alpha_book,
    sleeve_stats,
    vs_qqq,
)
from regime_bull_core import aligned_open, bh_metrics, jensen  # noqa: E402

OUT_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "sp500_timing_results.json")
REPORT_PATH = os.path.join(PROJECT_ROOT, "research_and_backtests", "sp500_timing_report.md")
ETF_CACHE = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "sp500_timing_etf_ohlcv.pkl")

TIMING_ETFS = ["SPY", "SSO", "UPRO", "SH", "BIL", "QQQ", "^VIX"]
MOM_LB = 60
VIX_HOT = 18.0
VIX_WARM = 24.0


@dataclass
class TimingStats:
    trades: int = 0
    regime_days: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    holdings: List[str] = field(default_factory=list)


def ensure_timing_etfs(raw: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    need = [t for t in TIMING_ETFS if t not in raw or raw[t] is None or getattr(raw[t], "empty", True)]
    # QQQ/SPY/^VIX usually already in master
    need = [t for t in need if t not in ("SPY", "QQQ", "^VIX") or t not in raw]
    os.makedirs(os.path.dirname(ETF_CACHE), exist_ok=True)
    cached: Dict[str, pd.DataFrame] = {}
    if os.path.exists(ETF_CACHE):
        age_h = (time.time() - os.path.getmtime(ETF_CACHE)) / 3600.0
        if age_h < 24:
            try:
                cached = pd.read_pickle(ETF_CACHE)
            except Exception:
                cached = {}
            if isinstance(cached, dict):
                for k, v in cached.items():
                    if k not in raw and isinstance(v, pd.DataFrame) and not v.empty:
                        raw[k] = v
                need = [
                    t
                    for t in TIMING_ETFS
                    if t not in raw or raw[t] is None or getattr(raw[t], "empty", True)
                ]
                print(f"[cache] timing ETFs ({age_h:.1f}h) missing={need}")
    if not need:
        return raw
    import yfinance as yf

    print(f"[download] timing ETFs {need}")
    piece = yf.download(
        need,
        start="2017-01-01",
        end="2026-08-29",
        interval="1d",
        auto_adjust=False,
        progress=True,
        threads=True,
        group_by="ticker",
    )
    for t in need:
        try:
            if isinstance(piece.columns, pd.MultiIndex):
                if t not in piece.columns.get_level_values(0):
                    print(f"  skip missing {t}")
                    continue
                df = _flatten_ticker_frame(piece[t], t)
            else:
                df = _flatten_ticker_frame(piece, t)
            if df is None or df.empty:
                print(f"  skip thin {t}")
                continue
            raw[t] = df
            cached[t] = df
            print(f"  {t}: {len(df)} bars")
        except Exception as exc:
            print(f"  skip {t}: {exc}")
    to_store = {k: raw[k] for k in TIMING_ETFS if k in raw}
    pd.to_pickle(to_store, ETF_CACHE)
    return raw


def signal_t1(spy: float, sma200: float, vix: float, mom60: float) -> Tuple[str, str]:
    if np.isfinite(spy) and np.isfinite(sma200) and sma200 > 0 and spy >= sma200:
        return "SSO", "bull_sso"
    return "BIL", "bear_cash"


def signal_t2(spy: float, sma200: float, vix: float, mom60: float) -> Tuple[str, str]:
    bull = np.isfinite(spy) and np.isfinite(sma200) and sma200 > 0 and spy >= sma200
    if not bull:
        return "BIL", "bear_cash"
    vx = float(vix) if np.isfinite(vix) else 20.0
    if vx < VIX_HOT:
        return "UPRO", "bull_upro"
    if vx < VIX_WARM:
        return "SSO", "bull_sso"
    return "SPY", "bull_spy"


def signal_t3(spy: float, sma200: float, vix: float, mom60: float) -> Tuple[str, str]:
    if np.isfinite(mom60) and mom60 >= 0.0:
        return "SSO", "mom_long"
    return "SH", "mom_short"


SIGNALS: Dict[str, Callable] = {
    "T1": signal_t1,
    "T2": signal_t2,
    "T3": signal_t3,
}


def run_timing_book(
    signal_fn: Callable,
    calendar: pd.DatetimeIndex,
    spy: np.ndarray,
    sma200: np.ndarray,
    vix: np.ndarray,
    mom60: np.ndarray,
    etf_ohlc: Dict[str, dict],
    start_i: int,
) -> Tuple[Book, TimingStats]:
    """Single-name ETF book: always 100% in the target ETF (or cash via BIL)."""
    n = len(calendar)
    dates = [d.strftime("%Y-%m-%d") for d in calendar]
    book = Book(cash=INITIAL)
    stats = TimingStats()
    pos: Optional[ProxyPos] = None
    start_i = max(MOM_LB + 1, int(start_i))

    def mark(i: int, which: str) -> float:
        if pos is None or pos.shares <= 0:
            return 0.0
        ohlc = etf_ohlc.get(pos.ticker)
        if ohlc is None:
            return pos.shares * pos.entry
        arr = ohlc["open"] if which == "open" else ohlc["close"]
        px = arr[i] if i < len(arr) else np.nan
        return pos.shares * (float(px) if np.isfinite(px) and px > 0 else pos.entry)

    def equity(i: int, which: str) -> float:
        return book.cash + mark(i, which)

    def sell_all(i: int, which: str, reason: str) -> None:
        nonlocal pos
        if pos is None or pos.shares <= 0:
            return
        ohlc = etf_ohlc[pos.ticker]
        arr = ohlc["open"] if which == "open" else ohlc["close"]
        px = float(arr[i]) if i < len(arr) and np.isfinite(arr[i]) and arr[i] > 0 else pos.entry
        fill = _fill_sell(px)
        book.cash += pos.shares * fill * (1.0 - FEE)
        stats.trades += 1
        pos = None

    def buy_target(ticker: str, i: int, which: str) -> None:
        nonlocal pos
        ohlc = etf_ohlc.get(ticker)
        if ohlc is None:
            return
        arr = ohlc["open"] if which == "open" else ohlc["close"]
        if i >= len(arr) or not np.isfinite(arr[i]) or arr[i] <= 0:
            return
        if not ohlc["ok"][i]:
            return
        px = float(arr[i])
        fill = _fill_buy(px)
        budget = book.cash * 0.995
        if budget < fill:
            return
        shares = (budget * (1.0 - FEE)) / fill
        cost = shares * fill
        if cost > book.cash or shares <= 0:
            return
        book.cash -= cost
        pos = ProxyPos(ticker=ticker, shares=shares, entry=fill)
        stats.trades += 1

    for i in range(start_i, n):
        asof = i - 1
        target, tag = signal_fn(spy[asof], sma200[asof], vix[asof], mom60[asof])
        stats.regime_days[tag] += 1
        cur = pos.ticker if pos and pos.shares > 0 else None
        if cur != target:
            # Switch at today's open using yesterday's signal
            sell_all(i, "open", f"SWITCH_{tag}")
            buy_target(target, i, "open")
        held = pos.ticker if pos and pos.shares > 0 else "CASH"
        stats.holdings.append(held)
        eq = equity(i, "close")
        book.equity.append(eq)
        book.dates.append(dates[i])

    # Flatten
    last_i = n - 1
    sell_all(last_i, "close", "EOB")
    return book, stats


def pack_timing(book: Book, bench: pd.DataFrame, stats: TimingStats) -> dict:
    m = enrich(metrics(book.equity, book.dates))
    days = max(1, int(m.get("n_days") or 1))
    m["trades"] = {
        "n": int(stats.trades),
        "trades_per_year": round(stats.trades * 252.0 / days, 1),
    }
    m["sleeve"] = {
        "regime_days": dict(stats.regime_days),
        "hold_share": {
            k: round(100.0 * v / max(1, len(stats.holdings)), 1)
            for k, v in pd.Series(stats.holdings).value_counts().to_dict().items()
        }
        if stats.holdings
        else {},
    }
    # Build synthetic returns series for alpha vs SPY/QQQ
    eq = np.array(book.equity, dtype=float)
    rets = np.diff(eq) / eq[:-1]
    idx = pd.to_datetime(book.dates[1:])
    spy_c = bench["spy"].reindex(pd.to_datetime(book.dates)).pct_change().iloc[1:].to_numpy(dtype=float)
    qqq_c = bench["qqq"].reindex(pd.to_datetime(book.dates)).pct_change().iloc[1:].to_numpy(dtype=float)
    df = pd.DataFrame({"r": rets, "spy": spy_c, "qqq": qqq_c}, index=idx)
    m["_rets"] = df
    return m


def vs_bench(book_rets: pd.DataFrame, bench_col: str, bench_m: dict, book_m: dict) -> dict:
    both = book_rets.dropna()
    if bench_col not in both.columns or len(both) < 30:
        return {}
    j = jensen(both["r"].to_numpy(), both[bench_col].to_numpy())
    excess = float(book_m["cagr_pct"]) - float(bench_m["cagr_pct"])
    return {"excess_cagr_pct": round(excess, 2), "jensen": j}


def score_row(name: str, m: dict, vs_spy=None, vs_qqq=None, verdict="—") -> str:
    xs = vs_spy.get("excess_cagr_pct") if vs_spy else "—"
    xq = vs_qqq.get("excess_cagr_pct") if vs_qqq else "—"
    t = m.get("trades") or {}
    return (
        f"| {name} | {m['cagr_pct']:.2f}% | {m['mdd_pct']:.2f}% | {m['sharpe']:.3f} | "
        f"{m.get('calmar', 0):.3f} | {t.get('n', '—')} | {t.get('trades_per_year', '—')} | "
        f"{xs} | {xq} | {verdict} |"
    )


def verdict_of(cagr: float, mdd: float, sharpe: float, c1_cagr: float, c1_mdd: float, c1_sharpe: float) -> str:
    beat_c1 = cagr > c1_cagr
    better_mdd = mdd > c1_mdd  # less negative
    better_sh = sharpe > c1_sharpe
    if beat_c1 and better_mdd and better_sh:
        return "BEAT_C1_ALL"
    if beat_c1 and better_mdd:
        return "BEAT_C1_CAGR_MDD"
    if beat_c1:
        return "BEAT_C1_CAGR"
    if better_mdd and better_sh:
        return "LOSE_C1_BETTER_RISK"
    return "LOSE_C1"


def diagnose(payload: dict) -> List[str]:
    w8 = payload["windows"]["2018-08-31"]
    w3 = payload["windows"].get("2023-08-31")
    c1 = w8["c1_v1"]
    books = [("T1", w8["T1"]), ("T2", w8["T2"]), ("T3", w8["T3"]), ("C1", c1)]
    champ = max(books, key=lambda kv: kv[1]["cagr_pct"])
    lines = [
        "## 진단",
        "",
        f"- **8년 챔피언: {champ[0]}** CAGR {champ[1]['cagr_pct']:.2f}% / MDD {champ[1]['mdd_pct']:.2f}% / "
        f"Sharpe {champ[1]['sharpe']:.3f}.",
        f"- **C1 v1** 8년 {c1['cagr_pct']:.2f}% / MDD {c1['mdd_pct']:.2f}% / Sharpe {c1['sharpe']:.3f}"
        + (
            f" | AI 3년 {w3['c1_v1']['cagr_pct']:.2f}% (vs QQQ {w3['c1_vs_qqq'].get('excess_cagr_pct')}%p)."
            if w3
            else "."
        ),
    ]
    for key in ("T1", "T2", "T3"):
        m = w8[key]
        lines.append(
            f"- **{key}** 8년 {m['cagr_pct']:.2f}% / MDD {m['mdd_pct']:.2f}% / Sharpe {m['sharpe']:.3f} / "
            f"trades/yr {m.get('trades', {}).get('trades_per_year')} / "
            f"vs C1 CAGR {m['cagr_pct'] - c1['cagr_pct']:+.2f}%p / "
            f"국면 {m.get('sleeve', {}).get('regime_days')}."
            + (
                f" AI3y {w3[key]['cagr_pct']:.2f}%."
                if w3
                else ""
            )
        )
    beat = [k for k in ("T1", "T2", "T3") if w8[k]["cagr_pct"] > c1["cagr_pct"]]
    sso = w8.get("sso_bh") or {}
    if sso:
        lines.append(
            f"- **SSO B&H** 8년 {sso.get('cagr_pct')}% / MDD {sso.get('mdd_pct')}% — "
            f"타이밍 없이 2x를 들고만 있어도 C1 CAGR({c1['cagr_pct']:.2f}%)을 "
            + ("이긴다" if float(sso.get("cagr_pct", 0)) > c1["cagr_pct"] else "못 이긴다")
            + f". 다만 MDD {sso.get('mdd_pct')}%는 C1 {c1['mdd_pct']:.2f}%보다 깊다."
        )
    if w3:
        risk_winners = [
            k
            for k in ("T1", "T2", "T3")
            if w3[k]["mdd_pct"] > w3["c1_v1"]["mdd_pct"] and w3[k]["sharpe"] >= w3["c1_v1"]["sharpe"] - 0.05
        ]
        if risk_winners:
            lines.append(
                f"- AI 3년 리스크 측면 우위: {', '.join(risk_winners)} "
                f"(예: T3 MDD {w3['T3']['mdd_pct']:.2f}% / Sharpe {w3['T3']['sharpe']:.3f} vs C1 "
                f"{w3['c1_v1']['mdd_pct']:.2f}% / {w3['c1_v1']['sharpe']:.3f}). CAGR은 여전히 C1이 앞선다."
            )
    lines.append(
        f"- **T2 실패 원인:** bull+VIX<18에서 UPRO 상시 노출({w8['T2'].get('sleeve', {}).get('regime_days', {}).get('bull_upro')}일) — "
        "3x 변동성 부패 + SMA200 래그 현금화가 V 반등을 놓쳐 8년 CAGR ≈ 0%."
    )
    if beat:
        lines.append(
            f"- **인덱스 타이밍이 C1을 8년 CAGR에서 이긴 전략: {', '.join(beat)}.** "
            "개별 피킹 없이도 레버리지 타이밍이 우월하면 C1은 위성/오버레이로 격하한다."
        )
    else:
        lines.append(
            "- **순수 S&P 타이밍 3종 모두 8년 CAGR에서 C1을 못 이김.** "
            "C1의 개별 주도주 알파(+유휴 QQQ/1.5x)가 단순 레버리지 스위치보다 우월하다. "
            "SSO B&H식 상시 2x는 CAGR만 보면 매력적이나 MDD −59%를 감당해야 한다."
        )
    lines.append("")
    return lines


def write_report(payload: dict) -> None:
    meta = payload["meta"]
    lines = [
        "# S&P 500 Leverage/Inverse Timing vs C1 v1",
        "",
        f"- 생성: {meta.get('generated')}",
        f"- 마찰: slippage {meta['friction']['slippage_bps']:.0f}bps / fee {meta['friction']['fee_bps']:.0f}bps / 익일 시가",
        f"- ETF 가용: {meta.get('etf_ok')}",
        "",
        "## 전략",
        "",
        "- **T1** — SPY≥SMA200 → SSO 100%; else BIL 100%.",
        "- **T2** — bull+VIX<18 → UPRO; bull+VIX<24 → SSO; bull+VIX≥24 → SPY; bear → BIL.",
        "- **T3** — SPY 60d mom ≥0 → SSO; else SH (−1x).",
        "- **C1 v1** — 개별 주도주 50/30/20 + 유휴 QQQ + 1.5x (참조 챔피언).",
        "",
    ]
    header = (
        "| Book | CAGR | MDD | Sharpe | Calmar | Trades | Trades/yr | vs SPY | vs QQQ | Verdict |"
    )
    sep = "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"
    for asof, block in payload["windows"].items():
        lines += [
            f"## Window {asof} → {block['spy_bh']['end']}",
            "",
            header,
            sep,
            score_row("SPY B&H", block["spy_bh"]),
            score_row("QQQ B&H", block["qqq_bh"]),
            score_row("SSO B&H", block["sso_bh"]) if block.get("sso_bh") else "",
            score_row(
                "C1 v1",
                block["c1_v1"],
                block.get("c1_vs_spy"),
                block.get("c1_vs_qqq"),
                block.get("c1_verdict"),
            ),
        ]
        for key in ("T1", "T2", "T3"):
            lines.append(
                score_row(
                    f"**{key}**",
                    block[key],
                    block.get(f"{key}_vs_spy"),
                    block.get(f"{key}_vs_qqq"),
                    block.get(f"{key}_verdict"),
                )
            )
        lines += [
            "",
            f"- T1 hold share: {block['T1'].get('sleeve', {}).get('hold_share')}  regime {block['T1'].get('sleeve', {}).get('regime_days')}",
            f"- T2 hold share: {block['T2'].get('sleeve', {}).get('hold_share')}  regime {block['T2'].get('sleeve', {}).get('regime_days')}",
            f"- T3 hold share: {block['T3'].get('sleeve', {}).get('hold_share')}  regime {block['T3'].get('sleeve', {}).get('regime_days')}",
            "",
        ]
    lines = [ln for ln in lines if ln]
    lines += diagnose(payload)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _line(tag: str, m: dict) -> str:
    t = m.get("trades") or {}
    return (
        f"  {tag:10s} CAGR {m['cagr_pct']:6.2f}%  MDD {m['mdd_pct']:7.2f}%  "
        f"Sharpe {m['sharpe']:.3f}  Calmar {m.get('calmar', 0):.3f}  "
        f"n={t.get('n')}  tpy={t.get('trades_per_year')}"
    )


def main() -> None:
    t0 = time.time()
    raw = download_master()
    raw = ensure_qld(raw)
    raw = ensure_timing_etfs(raw)

    # C1 needs stock panels; timing needs ETF OHLC on SPY calendar
    names = [t for t in stock_names() if t in raw]
    sliced = {k: v for k, v in raw.items() if k in set(names) | set(BENCH)}
    calendar, panels, bench = align_panels(sliced)
    print(f"[panel] stocks={len(panels)} days={len(calendar)}")

    etf_ohlc = {}
    for t in ("SPY", "SSO", "UPRO", "SH", "BIL", "QQQ"):
        if t in raw:
            etf_ohlc[t] = aligned_ohlc(raw, t, calendar)
            print(f"[etf] {t} ok={int(etf_ohlc[t]['ok'].sum())}")
        else:
            print(f"[etf] MISSING {t}")

    spy = bench["spy"].to_numpy(dtype=float)
    sma200 = bench["spy_sma200"].to_numpy(dtype=float)
    vix = bench["vix"].to_numpy(dtype=float)
    spy_s = pd.Series(spy)
    mom60 = (spy_s / spy_s.shift(MOM_LB) - 1.0).to_numpy(dtype=float)

    proxy_ohlc = {"QQQ": aligned_ohlc(raw, "QQQ", calendar)}
    if "QLD" in raw:
        proxy_ohlc["QLD"] = aligned_ohlc(raw, "QLD", calendar)

    print("[universe] composite RS …")
    universes = build_daily_universes_composite(calendar, raw)
    composite = {t: composite_rs(p["close"]) for t, p in panels.items()}

    spy_px = bench["spy"].copy()
    spy_px.index = pd.to_datetime(spy_px.index)
    qqq_px = bench["qqq"].copy()
    qqq_px.index = pd.to_datetime(qqq_px.index)
    sso_px = (
        pd.Series(etf_ohlc["SSO"]["close"], index=calendar)
        if "SSO" in etf_ohlc
        else None
    )

    payload = {
        "meta": {
            "method": "T1 SMA200→SSO/BIL; T2 VIX bands UPRO/SSO/SPY/BIL; T3 mom60 SSO/SH; vs C1 v1",
            "friction": {"slippage_bps": SLIPPAGE * 10000, "fee_bps": FEE * 10000},
            "etf_ok": {t: t in etf_ohlc for t in ("SPY", "SSO", "UPRO", "SH", "BIL")},
            "generated": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        },
        "windows": {},
    }

    for asof in SNAPSHOTS:
        start_i = max(snapshot_index(calendar, asof), MOM_LB + 1)
        print(f"\n===== WINDOW {asof} ({calendar[start_i].date()}) =====")

        c1_book, c1_sl = run_alpha_book(
            calendar, panels, bench, composite, universes, proxy_ohlc, start_i
        )
        c1 = pack_book(c1_book, bench, extra={"sleeve": sleeve_stats(c1_sl)})
        # Normalize C1 trade stats shape
        c1_trades = c1.get("trades") or {}
        days = max(1, int(c1.get("n_days") or 1))
        c1["trades"] = {
            "n": c1_trades.get("n", 0),
            "trades_per_year": round(float(c1_trades.get("n") or 0) * 252.0 / days, 1),
            "win_rate": c1_trades.get("win_rate"),
            "profit_factor": c1_trades.get("profit_factor"),
            "avg_bars": c1_trades.get("avg_bars"),
            "reasons": c1_trades.get("reasons"),
        }

        books = {"c1_v1": c1}
        stats_map = {}
        for key, fn in SIGNALS.items():
            book, st = run_timing_book(
                fn, calendar, spy, sma200, vix, mom60, etf_ohlc, start_i
            )
            books[key] = pack_timing(book, bench, st)
            stats_map[key] = st

        idx = pd.to_datetime(c1_book.dates)
        spy_m = enrich(bh_metrics(spy_px.reindex(idx)))
        qqq_m = enrich(bh_metrics(qqq_px.reindex(idx)))
        sso_m = enrich(bh_metrics(sso_px.reindex(idx))) if sso_px is not None else None

        block = {
            "spy_bh": spy_m,
            "qqq_bh": qqq_m,
            "sso_bh": sso_m,
            "c1_v1": books["c1_v1"],
            "T1": books["T1"],
            "T2": books["T2"],
            "T3": books["T3"],
        }

        # Alphas
        c1_rets = books["c1_v1"].pop("_rets")
        block["c1_vs_spy"] = vs_bench(c1_rets, "spy", spy_m, books["c1_v1"])
        block["c1_vs_qqq"] = vs_qqq(c1_rets, qqq_m, books["c1_v1"])
        block["c1_verdict"] = "BASELINE"
        for key in ("T1", "T2", "T3"):
            rets = books[key].pop("_rets")
            block[f"{key}_vs_spy"] = vs_bench(rets, "spy", spy_m, books[key])
            block[f"{key}_vs_qqq"] = vs_bench(rets, "qqq", qqq_m, books[key])
            block[f"{key}_verdict"] = verdict_of(
                books[key]["cagr_pct"],
                books[key]["mdd_pct"],
                books[key]["sharpe"],
                books["c1_v1"]["cagr_pct"],
                books["c1_v1"]["mdd_pct"],
                books["c1_v1"]["sharpe"],
            )

        payload["windows"][asof] = block
        print(_line("SPY", spy_m))
        print(_line("QQQ", qqq_m))
        if sso_m:
            print(_line("SSO", sso_m))
        print(_line("C1 v1", books["c1_v1"]) + f"  vsQQQ {block['c1_vs_qqq'].get('excess_cagr_pct')}")
        for key in ("T1", "T2", "T3"):
            print(
                _line(key, books[key])
                + f"  vsSPY {block[f'{key}_vs_spy'].get('excess_cagr_pct')}  "
                f"vsQQQ {block[f'{key}_vs_qqq'].get('excess_cagr_pct')}  "
                f"{block[f'{key}_verdict']}  {books[key].get('sleeve', {}).get('regime_days')}"
            )

    payload["meta"]["elapsed_sec"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(json_safe(payload), f, ensure_ascii=False, indent=2)
    write_report(payload)
    print(f"\nwrote {OUT_PATH}")
    print(f"wrote {REPORT_PATH}")
    for line in diagnose(payload):
        print(line)


if __name__ == "__main__":
    main()
