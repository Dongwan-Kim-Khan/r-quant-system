"""
Triple-Alpha Tournament — weekly swing vs PEAD vs MSI-gated C1.

Shared friction with QQQ Beat Alpha: next-open fills, 10bps slippage, 8bps fee,
PIT composite-60 universe. Windows 2018-08-31 and 2023-08-31 → 2026-08-28.

  Alpha 1  3–5 day mean-reversion/breakout on high-dollar-volume names.
  Alpha 2  Post-earnings announcement drift (EPS surprise > +15%, rev YoY > 0).
  Alpha 3  C1 v1 slots/leverage gated by daily MSI 2.0 (hard gauges + credit NLP proxy).

C1 v1 is the swing champion baseline (AI-3y 31.81% / vs QQQ +7.88%p).
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
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

sys.path.insert(0, os.path.join(PROJECT_ROOT, "research_and_backtests"))
from al_sangmoo.core.constants import STOP_LOSS_PCT  # noqa: E402
from al_sangmoo.domain.quant.macro import evaluate_macro_stance  # noqa: E402
from compare_html_vs_live_exit import (  # noqa: E402
    FEE,
    INITIAL,
    SLIPPAGE,
    Book,
    Position,
    _fill_buy,
    _fill_sell,
    _flatten_ticker_frame,
    align_panels,
    close_position,
    decide_exit,
    entry_signal,
)
from pit_dynamic_universe_exit_ab import aligned_close, stock_names  # noqa: E402
from pit_universe_exit_ab import download_master, snapshot_index  # noqa: E402
from qqq_beat_alpha_backtester import (  # noqa: E402
    BENCH,
    CONVICTION,
    DRIFT_BAND,
    MAX_SLOTS,
    SNAPSHOTS,
    ProxyPos,
    Sleeve,
    _px,
    aligned_ohlc,
    build_daily_universes_composite,
    composite_rs,
    enrich,
    ensure_qld,
    json_safe,
    pack_book,
    proxy_mix,
    run_alpha_book,
    sleeve_stats,
    target_leverage,
    vs_qqq,
)
from qqq_beat_alpha_v3 import proxy_mix_capped  # noqa: E402
from regime_bull_core import bh_metrics  # noqa: E402

OUT_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "triple_alpha_tournament_results.json")
REPORT_PATH = os.path.join(PROJECT_ROOT, "research_and_backtests", "triple_alpha_tournament_report.md")
MACRO_CACHE = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "macro_msi_ohlcv.pkl")
EARNINGS_CACHE = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "pead_earnings.pkl")

MACRO_TICKERS = ["^TNX", "CL=F", "DX-Y.NYB", "DX=F", "HYG", "IEF"]
WEEKLY_SLOTS = 5
WEEKLY_SLOT_W = 0.20
WEEKLY_TP = 0.04
WEEKLY_SL = 0.025
WEEKLY_MAX_BARS = 3
PEAD_SLOTS = 4
PEAD_SLOT_W = 0.25
PEAD_SURPRISE = 15.0
PEAD_HOLD_BARS = 60
PEAD_SETTLE = (3, 5)  # inclusive session counts after first post-print bar
MSI_GREEN = 35.0
MSI_RED = 65.0
DVOL_TOP = 20


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------
def aligned_volume(raw: Dict[str, pd.DataFrame], ticker: str, calendar: pd.DatetimeIndex) -> np.ndarray:
    df = raw.get(ticker)
    if df is None or df.empty or "Volume" not in df.columns:
        return np.full(len(calendar), np.nan)
    s = df["Volume"].copy()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    return s.reindex(calendar).to_numpy(dtype=float)


def ensure_macro(raw: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    need = [t for t in MACRO_TICKERS if t not in raw or raw[t] is None or getattr(raw[t], "empty", True)]
    os.makedirs(os.path.dirname(MACRO_CACHE), exist_ok=True)
    cached = {}
    if os.path.exists(MACRO_CACHE):
        age_h = (time.time() - os.path.getmtime(MACRO_CACHE)) / 3600.0
        if age_h < 24:
            cached = pd.read_pickle(MACRO_CACHE)
            if isinstance(cached, dict):
                for k, v in cached.items():
                    if k not in raw and isinstance(v, pd.DataFrame) and not v.empty:
                        raw[k] = v
                need = [t for t in MACRO_TICKERS if t not in raw]
                print(f"[cache] macro {MACRO_CACHE} ({age_h:.1f}h) missing={need}")
    if not need:
        return raw
    import yfinance as yf

    print(f"[download] macro {need}")
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
    pd.to_pickle(cached if cached else {k: raw[k] for k in MACRO_TICKERS if k in raw}, MACRO_CACHE)
    return raw


def _tnx_yield(val: float) -> float:
    if not np.isfinite(val):
        return 4.0
    v = float(val)
    if v > 20.0:
        v = v / 10.0
    return v


def build_msi_series(calendar: pd.DatetimeIndex, raw: Dict[str, pd.DataFrame], bench: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, dict]:
    """Daily MSI 2.0 from evaluate_macro_stance.

    M_hard: ^TNX / ^VIX / CL=F / DXY (DX-Y.NYB, fallback DX=F).
    M_nlp: no 8y YouTube PIT archive — HYG vs IEF 20d credit spread as headline-climate proxy
    fed as defense/buy token counts into evaluate_macro_stance.
    M_shock: VIX panic, 20d rate spike, 20d dollar spike, 5d SPY crash.
    """
    n = len(calendar)
    tnx = aligned_close(raw, "^TNX", calendar)
    vix = bench["vix"].to_numpy(dtype=float)
    wti = aligned_close(raw, "CL=F", calendar)
    dxy = aligned_close(raw, "DX-Y.NYB", calendar)
    if not np.isfinite(dxy).any():
        dxy = aligned_close(raw, "DX=F", calendar)
    hyg = aligned_close(raw, "HYG", calendar)
    ief = aligned_close(raw, "IEF", calendar)
    spy = bench["spy"].to_numpy(dtype=float)

    def ret_n(arr, i, lb):
        if i < lb or not np.isfinite(arr[i]) or not np.isfinite(arr[i - lb]) or arr[i - lb] == 0:
            return np.nan
        return float(arr[i] / arr[i - lb] - 1.0)

    msi = np.full(n, 50.0)
    buckets = np.empty(n, dtype=object)
    counts = defaultdict(int)
    for i in range(n):
        gauges = {
            "us10y": {"val": _tnx_yield(tnx[i])},
            "vix": {"val": float(vix[i]) if np.isfinite(vix[i]) else 15.0},
            "wti": {"val": float(wti[i]) if np.isfinite(wti[i]) else 75.0},
            "dxy": {"val": float(dxy[i]) if np.isfinite(dxy[i]) else 100.0},
        }
        credit = ret_n(hyg, i, 20)
        treas = ret_n(ief, i, 20)
        spread = credit - treas if np.isfinite(credit) and np.isfinite(treas) else np.nan
        if not np.isfinite(spread):
            defense, buy = 8, 8
        elif spread < -0.06:
            defense, buy = 22, 2
        elif spread < -0.02:
            defense, buy = 16, 6
        elif spread > 0.03:
            defense, buy = 3, 20
        else:
            defense, buy = 10, 10

        shocks: List[str] = []
        vx = float(vix[i]) if np.isfinite(vix[i]) else 15.0
        if vx >= 30.0:
            shocks.append("지정학적 분쟁 및 전쟁 리스크")
        tnx_chg = tnx[i] - tnx[i - 20] if i >= 20 and np.isfinite(tnx[i]) and np.isfinite(tnx[i - 20]) else 0.0
        tnx_chg = _tnx_yield(tnx[i]) - _tnx_yield(tnx[i - 20]) if i >= 20 else 0.0
        if tnx_chg >= 0.40:
            shocks.append("금리 경로 및 통화정책 영향권")
        dxy_20 = ret_n(dxy, i, 20)
        if np.isfinite(dxy_20) and dxy_20 >= 0.025:
            shocks.append("무역 분쟁 및 관세 불확실성")
        spy_5 = ret_n(spy, i, 5)
        if np.isfinite(spy_5) and spy_5 <= -0.08:
            shocks.append("지정학적 분쟁 및 전쟁 리스크")

        res = evaluate_macro_stance(
            gauges=gauges,
            defense_count=defense,
            buy_count=buy,
            matched_shocks=shocks or None,
        )
        score = float(res["msi_score"])
        msi[i] = score
        if score < MSI_GREEN:
            tag = "green"
        elif score < MSI_RED:
            tag = "yellow"
        else:
            tag = "red"
        buckets[i] = tag
        counts[tag] += 1
    return msi, buckets, dict(counts)


def build_extras(raw: Dict[str, pd.DataFrame], panels: Dict[str, dict], calendar: pd.DatetimeIndex) -> Dict[str, dict]:
    extras: Dict[str, dict] = {}
    for tk, p in panels.items():
        close = p["close"]
        vol = aligned_volume(raw, tk, calendar)
        dvol = close * vol
        sma5 = pd.Series(close).rolling(5, min_periods=5).mean().to_numpy(dtype=float)
        sma20 = pd.Series(close).rolling(20, min_periods=20).mean().to_numpy(dtype=float)
        dvol20 = pd.Series(dvol).rolling(20, min_periods=10).mean().to_numpy(dtype=float)
        delta = pd.Series(close).diff()
        up = delta.clip(lower=0.0)
        dn = (-delta).clip(lower=0.0)
        au = up.ewm(alpha=1.0 / 14.0, min_periods=14, adjust=False).mean()
        ad = dn.ewm(alpha=1.0 / 14.0, min_periods=14, adjust=False).mean()
        rs = au / ad.replace(0.0, np.nan)
        rsi = (100.0 - 100.0 / (1.0 + rs)).to_numpy(dtype=float)
        extras[tk] = {"sma5": sma5, "sma20": sma20, "rsi": rsi, "dvol20": dvol20}
    return extras


# ---------------------------------------------------------------------------
# Earnings / PEAD
# ---------------------------------------------------------------------------
def _fetch_one_earnings(ticker: str) -> dict:
    import yfinance as yf

    out = {"ticker": ticker, "events": []}
    try:
        t = yf.Ticker(ticker)
        ed = t.get_earnings_dates(limit=48)
        inc = t.quarterly_income_stmt
    except Exception:
        return out
    rev = None
    if inc is not None and not inc.empty:
        for row in ("Total Revenue", "Operating Revenue", "Revenue"):
            if row in inc.index:
                rev = inc.loc[row].dropna()
                rev.index = pd.to_datetime(rev.index).tz_localize(None)
                rev = rev.sort_index()
                break
    if ed is None or ed.empty:
        return out
    for ts, row in ed.iterrows():
        surprise = row.get("Surprise(%)")
        reported = row.get("Reported EPS")
        if surprise is None or (isinstance(surprise, float) and not np.isfinite(surprise)):
            continue
        if reported is None or (isinstance(reported, float) and not np.isfinite(reported)):
            continue
        ts = pd.Timestamp(ts)
        yoy = np.nan
        if rev is not None and len(rev) >= 5:
            asof = pd.Timestamp(ts.tz_localize(None) if ts.tzinfo else ts).normalize()
            prior = rev[rev.index <= asof]
            if len(prior) >= 5:
                curr = float(prior.iloc[-1])
                base = float(prior.iloc[-5])
                if base > 0:
                    yoy = curr / base - 1.0
        out["events"].append(
            {
                "ts": ts.isoformat(),
                "surprise": float(surprise),
                "rev_yoy": None if not np.isfinite(yoy) else round(float(yoy), 4),
            }
        )
    return out


def load_earnings(tickers: List[str]) -> Dict[str, dict]:
    os.makedirs(os.path.dirname(EARNINGS_CACHE), exist_ok=True)
    cached = {}
    if os.path.exists(EARNINGS_CACHE):
        age_h = (time.time() - os.path.getmtime(EARNINGS_CACHE)) / 3600.0
        try:
            cached = pd.read_pickle(EARNINGS_CACHE)
        except Exception:
            cached = {}
        if isinstance(cached, dict) and age_h < 72 and cached.get("_tickers") == sorted(tickers):
            print(f"[cache] earnings {EARNINGS_CACHE} ({age_h:.1f}h) n={len(cached.get('names', {}))}")
            return cached.get("names", {})
        if isinstance(cached, dict) and "names" in cached:
            have = cached["names"]
        else:
            have = cached if isinstance(cached, dict) else {}
    else:
        have = {}

    missing = [t for t in tickers if t not in have]
    print(f"[earnings] fetch {len(missing)} / {len(tickers)}")
    if missing:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {pool.submit(_fetch_one_earnings, t): t for t in missing}
            done = 0
            for fut in as_completed(futs):
                rec = fut.result()
                have[rec["ticker"]] = rec
                done += 1
                if done % 20 == 0 or done == len(missing):
                    print(f"  earnings {done}/{len(missing)}")
        pd.to_pickle(
            {"_tickers": sorted(tickers), "names": have, "generated": pd.Timestamp.utcnow().isoformat()},
            EARNINGS_CACHE,
        )
    return have


def session_index(calendar: pd.DatetimeIndex, ts_iso: str) -> Optional[int]:
    ts = pd.Timestamp(ts_iso)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("America/New_York")
    day = pd.Timestamp(ts.strftime("%Y-%m-%d"))
    hour = int(ts.hour) if ts.tzinfo is not None or ts.hour else 0
    # Missing/midnight stamps are treated as after-close (cannot trade that session).
    if hour >= 16 or hour == 0:
        target = day + pd.Timedelta(days=1)
    else:
        target = day
    loc = int(calendar.searchsorted(target))
    if loc >= len(calendar):
        return None
    return loc


def build_pead_entries(
    calendar: pd.DatetimeIndex,
    panels: Dict[str, dict],
    earnings: Dict[str, dict],
) -> Tuple[Dict[int, List[Tuple[float, str, int]]], Dict[str, List[int]]]:
    """Map qualifying earnings to first fill-day after 3–5 session gap settlement."""
    fills: Dict[int, List[Tuple[float, str, int]]] = defaultdict(list)
    next_earn: Dict[str, List[int]] = defaultdict(list)
    n_qual = 0
    n_settle = 0
    for tk, rec in earnings.items():
        if tk not in panels:
            continue
        p = panels[tk]
        sessions: List[Tuple[int, float, Optional[float]]] = []
        for ev in rec.get("events") or []:
            si = session_index(calendar, ev["ts"])
            if si is None:
                continue
            next_earn[tk].append(si)
            sessions.append((si, float(ev["surprise"]), ev.get("rev_yoy")))
        next_earn[tk] = sorted(set(next_earn[tk]))
        for si, surprise, yoy in sessions:
            if surprise < PEAD_SURPRISE:
                continue
            if yoy is None or not np.isfinite(yoy) or yoy <= 0:
                continue
            if si < 2 or si >= len(calendar) - 6:
                continue
            if not p["listed"][si] or not p["listed"][si - 1]:
                continue
            n_qual += 1
            pre = p["close"][si - 1]
            post = p["close"][si]
            if not np.isfinite(pre) or not np.isfinite(post) or pre <= 0 or post <= pre:
                continue  # require gap-up print
            lo, hi = PEAD_SETTLE
            for sess in range(lo, hi + 1):
                asof = si + sess - 1  # session 3 → si+2
                fill_i = asof + 1
                if asof >= len(calendar) or fill_i >= len(calendar):
                    break
                if not p["listed"][asof]:
                    continue
                px = p["close"][asof]
                if not np.isfinite(px) or px <= 0:
                    continue
                if px >= post * 0.98 and px > pre:
                    fills[fill_i].append((surprise, tk, si))
                    n_settle += 1
                    break
    print(f"[pead] surprise+rev events={n_qual} settled gap-holds={n_settle} fill-days={len(fills)}")
    return fills, dict(next_earn)


# ---------------------------------------------------------------------------
# Shared overlay book
# ---------------------------------------------------------------------------
@dataclass
class OverlayStats:
    occupancy: List[float] = field(default_factory=list)
    cash_frac: List[float] = field(default_factory=list)
    leverage: List[float] = field(default_factory=list)
    regime_days: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    proxy_trades: int = 0
    signal_days: int = 0
    fills: int = 0


def _make_proxy_helpers(book: Book, sl_proxy: Dict[str, ProxyPos], proxy_ohlc: Dict[str, dict], panels: Dict[str, dict], stats: OverlayStats):
    has_qld = "QLD" in proxy_ohlc and bool(np.nanmax(proxy_ohlc["QLD"]["ok"]))

    def stock_mark(i: int, which: str) -> float:
        tot = 0.0
        for tk, pos in book.positions.items():
            p = panels[tk]
            arr = p["open"] if which == "open" else p["close"]
            px = arr[i]
            tot += pos.shares * (float(px) if np.isfinite(px) and px > 0 else pos.entry)
        return tot

    def proxy_mark(i: int, which: str) -> float:
        tot = 0.0
        for tk, pos in sl_proxy.items():
            if pos.shares <= 0:
                continue
            px = _px(proxy_ohlc[tk], i, which)
            tot += pos.shares * (px if px > 0 else pos.entry)
        return tot

    def equity_now(i: int, which: str) -> float:
        return book.cash + stock_mark(i, which) + proxy_mark(i, which)

    def sell_proxy(ticker: str, i: int, which: str, shares: Optional[float] = None) -> None:
        pos = sl_proxy.get(ticker)
        if pos is None or pos.shares <= 0:
            return
        qty = pos.shares if shares is None else min(pos.shares, shares)
        if qty <= 1e-12:
            return
        px = _px(proxy_ohlc[ticker], i, which)
        if px <= 0:
            px = pos.entry
        fill = _fill_sell(px)
        book.cash += qty * fill * (1.0 - FEE)
        stats.proxy_trades += 1
        pos.shares -= qty
        if pos.shares <= 1e-9:
            pos.shares = 0.0
            pos.entry = 0.0

    def buy_proxy(ticker: str, i: int, which: str, target_val: float) -> None:
        px_raw = _px(proxy_ohlc[ticker], i, which)
        if px_raw <= 0 or target_val < px_raw:
            return
        fill = _fill_buy(px_raw)
        budget = min(book.cash * 0.995, target_val)
        if budget < fill:
            return
        shares = (budget * (1.0 - FEE)) / fill
        cost = shares * fill
        if cost > book.cash or shares <= 0:
            return
        book.cash -= cost
        pos = sl_proxy.setdefault(ticker, ProxyPos(ticker=ticker))
        if pos.shares > 0 and pos.entry > 0:
            pos.entry = (pos.entry * pos.shares + fill * shares) / (pos.shares + shares)
        else:
            pos.entry = fill
        pos.shares += shares
        stats.proxy_trades += 1

    def set_proxy_weight(ticker: str, i: int, which: str, target_frac: float, eq: float) -> None:
        if ticker not in proxy_ohlc:
            return
        px = _px(proxy_ohlc[ticker], i, which)
        if px <= 0:
            if target_frac <= 0:
                sell_proxy(ticker, i, which)
            return
        pos = sl_proxy.setdefault(ticker, ProxyPos(ticker=ticker))
        cur_val = pos.shares * px if pos.shares > 0 else 0.0
        target_val = max(0.0, eq * target_frac)
        if target_frac <= 1e-9 or target_val < px:
            sell_proxy(ticker, i, which)
            return
        if cur_val > target_val * (1.0 + DRIFT_BAND):
            sell_shares = min(pos.shares, (cur_val - target_val) / px)
            sell_proxy(ticker, i, which, shares=sell_shares)
            return
        if cur_val < target_val * (1.0 - DRIFT_BAND):
            buy_proxy(ticker, i, which, target_val - cur_val)

    def free_cash(i: int, needed: float) -> None:
        for ticker in ("QLD", "QQQ"):
            if book.cash >= needed:
                return
            pos = sl_proxy.get(ticker)
            if pos is None or pos.shares <= 0:
                continue
            px = _px(proxy_ohlc[ticker], i, "open")
            if px <= 0:
                continue
            gap = needed - book.cash
            shares = min(pos.shares, gap / (px * (1.0 - SLIPPAGE) * (1.0 - FEE) + 1e-12))
            sell_proxy(ticker, i, "open", shares=shares)

    def rebalance_proxy(i: int, which: str, qqq_w: float, qld_w: float, force: bool = False) -> None:
        eq = equity_now(i, which)
        if eq <= 0:
            return
        if not force:
            def cur_w(tk: str) -> float:
                pos = sl_proxy.get(tk)
                if pos is None or pos.shares <= 0:
                    return 0.0
                px = _px(proxy_ohlc[tk], i, which)
                return (pos.shares * px / eq) if px > 0 else 0.0

            if abs(cur_w("QQQ") - qqq_w) + abs(cur_w("QLD") - qld_w) < DRIFT_BAND:
                return
        cur_qqq = sl_proxy.get("QQQ")
        px_q = _px(proxy_ohlc["QQQ"], i, which) if "QQQ" in proxy_ohlc else 0.0
        q_val = cur_qqq.shares * px_q if cur_qqq and px_q > 0 else 0.0
        if q_val > eq * qqq_w:
            set_proxy_weight("QQQ", i, which, qqq_w, eq)
            set_proxy_weight("QLD", i, which, qld_w, equity_now(i, which))
        else:
            set_proxy_weight("QLD", i, which, qld_w, eq)
            set_proxy_weight("QQQ", i, which, qqq_w, equity_now(i, which))

    def buy_stock(tk: str, i: int, frac: float) -> bool:
        p = panels[tk]
        raw_open = p["open"][i]
        if not np.isfinite(raw_open) or raw_open <= 0:
            return False
        fill = _fill_buy(float(raw_open))
        eq_open = equity_now(i, "open")
        cap = eq_open * frac
        if fill <= 0 or cap < fill:
            return False
        if cap > book.cash:
            free_cash(i, cap)
        budget = min(cap, book.cash * 0.995)
        shares = (budget * (1.0 - FEE)) / fill
        if shares <= 0:
            return False
        cost = shares * fill
        if cost > book.cash:
            return False
        book.cash -= cost
        book.positions[tk] = Position(
            ticker=tk,
            shares=shares,
            entry=fill,
            entry_i=i,
            peak_high=max(fill, p["high"][i] if np.isfinite(p["high"][i]) else fill),
            entry_date="",
        )
        stats.fills += 1
        return True

    return {
        "stock_mark": stock_mark,
        "proxy_mark": proxy_mark,
        "equity_now": equity_now,
        "sell_proxy": sell_proxy,
        "free_cash": free_cash,
        "rebalance_proxy": rebalance_proxy,
        "buy_stock": buy_stock,
        "has_qld": has_qld,
    }


def run_weekly_book(
    calendar: pd.DatetimeIndex,
    panels: Dict[str, dict],
    extras: Dict[str, dict],
    allowed_by_day: List[Optional[Set[str]]],
    proxy_ohlc: Dict[str, dict],
    start_i: int,
) -> Tuple[Book, OverlayStats]:
    n = len(calendar)
    dates = [d.strftime("%Y-%m-%d") for d in calendar]
    book = Book(cash=INITIAL)
    stats = OverlayStats()
    sl_proxy: Dict[str, ProxyPos] = {}
    H = _make_proxy_helpers(book, sl_proxy, proxy_ohlc, panels, stats)
    start_i = max(20, int(start_i))

    def weekly_signal(tk: str, asof: int) -> Optional[float]:
        p = panels[tk]
        ex = extras.get(tk)
        if ex is None or asof < 16:
            return None
        if not p["listed"][asof] or not p["listed"][asof - 1] or not p["listed"][asof - 2]:
            return None
        o, c = p["open"], p["close"]
        red_a = np.isfinite(o[asof - 2]) and np.isfinite(c[asof - 2]) and c[asof - 2] < o[asof - 2]
        red_b = np.isfinite(o[asof - 1]) and np.isfinite(c[asof - 1]) and c[asof - 1] < o[asof - 1]
        bounce = (
            np.isfinite(c[asof])
            and np.isfinite(o[asof])
            and np.isfinite(ex["sma5"][asof])
            and c[asof] > o[asof]
            and c[asof] > ex["sma5"][asof]
        )
        rsi = ex["rsi"][asof]
        rsi_ok = np.isfinite(rsi) and 40.0 <= float(rsi) <= 55.0
        if not (red_a and red_b and bounce and rsi_ok):
            return None
        dvol = ex["dvol20"][asof]
        return float(dvol) if np.isfinite(dvol) else 0.0

    for i in range(start_i, n):
        asof = i - 1
        wd = int(calendar[i].weekday())
        stats.regime_days["fri" if wd == 4 else "wk"] += 1

        for tk in list(book.positions.keys()):
            pos = book.positions[tk]
            p = panels[tk]
            if not p["listed"][i]:
                continue
            high = p["high"][i]
            low = p["low"][i]
            opn = p["open"][i]
            if np.isfinite(high):
                pos.peak_high = max(pos.peak_high, high)
            hard = pos.entry * (1.0 - WEEKLY_SL)
            tp = pos.entry * (1.0 + WEEKLY_TP)
            reason = None
            raw = None
            if np.isfinite(low) and low <= hard:
                reason = "HARD_STOP"
                raw = float(opn) if np.isfinite(opn) and opn > 0 and opn < hard else hard
            elif np.isfinite(high) and high >= tp:
                reason = "TAKE_PROFIT"
                raw = float(opn) if np.isfinite(opn) and opn > 0 and opn > tp else tp
            elif (i - pos.entry_i) >= WEEKLY_MAX_BARS:
                reason = "TIME_3D"
                raw = p["close"][i]
            elif wd == 4:
                reason = "FRIDAY"
                raw = p["close"][i]
            if reason and np.isfinite(raw) and raw > 0:
                close_position(book, pos, float(raw), dates[i], reason, i)

        if wd != 4 and len(book.positions) < WEEKLY_SLOTS:
            scored: List[Tuple[float, str]] = []
            allowed = allowed_by_day[i] if allowed_by_day is not None else None
            for tk, p in panels.items():
                if tk in book.positions:
                    continue
                if "." in tk:
                    continue
                if allowed is not None and tk not in allowed:
                    continue
                if not p["listed"][i]:
                    continue
                sc = weekly_signal(tk, asof)
                if sc is None:
                    continue
                scored.append((sc, tk))
            scored.sort(reverse=True)
            scored = scored[:DVOL_TOP]
            if scored:
                stats.signal_days += 1
            n_free = WEEKLY_SLOTS - len(book.positions)
            needed = H["equity_now"](i, "open") * WEEKLY_SLOT_W * n_free
            if needed > book.cash:
                H["free_cash"](i, needed)
            for _, tk in scored:
                if len(book.positions) >= WEEKLY_SLOTS:
                    break
                if H["buy_stock"](tk, i, WEEKLY_SLOT_W):
                    book.positions[tk].entry_date = dates[i]

        # weekly thesis is cash turnover — idle stays cash (no QQQ parking)
        eq = H["equity_now"](i, "close")
        book.equity.append(eq)
        book.dates.append(dates[i])
        if eq > 0:
            stats.occupancy.append(H["stock_mark"](i, "close") / eq)
            stats.cash_frac.append(book.cash / eq)
            stats.leverage.append(H["stock_mark"](i, "close") / eq)

    last_i = n - 1
    for tk in list(book.positions.keys()):
        pos = book.positions[tk]
        px = panels[tk]["close"][last_i]
        if not np.isfinite(px) or px <= 0:
            px = pos.entry
        close_position(book, pos, float(px), dates[last_i], "EOB", last_i)
    return book, stats


def run_pead_book(
    calendar: pd.DatetimeIndex,
    panels: Dict[str, dict],
    extras: Dict[str, dict],
    fills: Dict[int, List[Tuple[float, str, int]]],
    next_earn: Dict[str, List[int]],
    proxy_ohlc: Dict[str, dict],
    start_i: int,
) -> Tuple[Book, OverlayStats]:
    n = len(calendar)
    dates = [d.strftime("%Y-%m-%d") for d in calendar]
    book = Book(cash=INITIAL)
    stats = OverlayStats()
    sl_proxy: Dict[str, ProxyPos] = {}
    H = _make_proxy_helpers(book, sl_proxy, proxy_ohlc, panels, stats)
    start_i = max(20, int(start_i))
    consumed: Set[Tuple[str, int]] = set()

    def next_report(tk: str, i: int) -> Optional[int]:
        arr = next_earn.get(tk) or []
        for si in arr:
            if si > i:
                return si
        return None

    for i in range(start_i, n):
        for tk in list(book.positions.keys()):
            pos = book.positions[tk]
            p = panels[tk]
            ex = extras.get(tk)
            if not p["listed"][i]:
                continue
            high = p["high"][i]
            if np.isfinite(high):
                pos.peak_high = max(pos.peak_high, high)
            reason = None
            raw = None
            nxt = next_report(tk, i)
            if nxt is not None and nxt - i <= 2:
                reason = "PRE_EARNINGS"
                raw = p["close"][i]
            elif (i - pos.entry_i) >= PEAD_HOLD_BARS:
                reason = "TIME_60D"
                raw = p["close"][i]
            elif ex is not None and np.isfinite(ex["sma20"][i]) and np.isfinite(p["close"][i]) and p["close"][i] < ex["sma20"][i]:
                reason = "SMA20"
                raw = p["close"][i]
            if reason and np.isfinite(raw) and raw > 0:
                close_position(book, pos, float(raw), dates[i], reason, i)

        cands = fills.get(i) or []
        if cands and len(book.positions) < PEAD_SLOTS:
            stats.signal_days += 1
            ranked = sorted(cands, reverse=True)
            n_free = PEAD_SLOTS - len(book.positions)
            needed = H["equity_now"](i, "open") * PEAD_SLOT_W * n_free
            if needed > book.cash:
                H["free_cash"](i, needed)
            for surprise, tk, ev_si in ranked:
                if len(book.positions) >= PEAD_SLOTS:
                    break
                if tk in book.positions:
                    continue
                if (tk, ev_si) in consumed:
                    continue
                if tk not in panels or not panels[tk]["listed"][i]:
                    continue
                nxt = next_report(tk, i)
                if nxt is not None and nxt - i <= 5:
                    continue  # too close to the following print
                if H["buy_stock"](tk, i, PEAD_SLOT_W):
                    book.positions[tk].entry_date = dates[i]
                    consumed.add((tk, ev_si))

        eq_open_idle = H["equity_now"](i, "close")
        stock_w = H["stock_mark"](i, "close") / eq_open_idle if eq_open_idle > 0 else 0.0
        qqq_w, qld_w = proxy_mix(stock_w, 1.0, False)
        H["rebalance_proxy"](i, "close", qqq_w, qld_w)

        eq = H["equity_now"](i, "close")
        book.equity.append(eq)
        book.dates.append(dates[i])
        if eq > 0:
            stats.occupancy.append(H["stock_mark"](i, "close") / eq)
            stats.cash_frac.append(book.cash / eq)
            stats.leverage.append((H["stock_mark"](i, "close") + H["proxy_mark"](i, "close")) / eq)

    last_i = n - 1
    for tk in list(book.positions.keys()):
        pos = book.positions[tk]
        px = panels[tk]["close"][last_i]
        if not np.isfinite(px) or px <= 0:
            px = pos.entry
        close_position(book, pos, float(px), dates[last_i], "EOB", last_i)
    for tk in list(sl_proxy.keys()):
        H["sell_proxy"](tk, last_i, "close")
    return book, stats


def run_msi_c1(
    calendar: pd.DatetimeIndex,
    panels: Dict[str, dict],
    bench: pd.DataFrame,
    composite: Dict[str, np.ndarray],
    allowed_by_day: List[Optional[Set[str]]],
    proxy_ohlc: Dict[str, dict],
    msi: np.ndarray,
    start_i: int,
) -> Tuple[Book, Sleeve, OverlayStats]:
    """C1 v1 entries/exits, overlay sized by MSI 2.0 climate gate."""
    n = len(calendar)
    dates = [d.strftime("%Y-%m-%d") for d in calendar]
    spy = bench["spy"].to_numpy(dtype=float)
    sma200 = bench["spy_sma200"].to_numpy(dtype=float)
    vix = bench["vix"].to_numpy(dtype=float)
    book = Book(cash=INITIAL)
    sl = Sleeve()
    stats = OverlayStats()
    has_qld = "QLD" in proxy_ohlc and bool(np.nanmax(proxy_ohlc["QLD"]["ok"]))
    qqq_crs = composite_rs(proxy_ohlc["QQQ"]["close"]) if "QQQ" in proxy_ohlc else np.full(n, np.nan)
    start_i = max(1, int(start_i))
    prev_lev = None

    def stock_mark(i: int, which: str) -> float:
        tot = 0.0
        for tk, pos in book.positions.items():
            p = panels[tk]
            arr = p["open"] if which == "open" else p["close"]
            px = arr[i]
            tot += pos.shares * (float(px) if np.isfinite(px) and px > 0 else pos.entry)
        return tot

    def proxy_mark(i: int, which: str) -> float:
        tot = 0.0
        for tk, pos in sl.proxy.items():
            if pos.shares <= 0:
                continue
            px = _px(proxy_ohlc[tk], i, which)
            tot += pos.shares * (px if px > 0 else pos.entry)
        return tot

    def equity_now(i: int, which: str) -> float:
        return book.cash + stock_mark(i, which) + proxy_mark(i, which)

    def sell_proxy(ticker: str, i: int, which: str, shares: Optional[float] = None) -> None:
        pos = sl.proxy.get(ticker)
        if pos is None or pos.shares <= 0:
            return
        qty = pos.shares if shares is None else min(pos.shares, shares)
        if qty <= 1e-12:
            return
        px = _px(proxy_ohlc[ticker], i, which)
        if px <= 0:
            px = pos.entry
        fill = _fill_sell(px)
        book.cash += qty * fill * (1.0 - FEE)
        sl.proxy_trades += 1
        pos.shares -= qty
        if pos.shares <= 1e-9:
            pos.shares = 0.0
            pos.entry = 0.0

    def buy_proxy(ticker: str, i: int, which: str, target_val: float) -> None:
        px_raw = _px(proxy_ohlc[ticker], i, which)
        if px_raw <= 0 or target_val < px_raw:
            return
        fill = _fill_buy(px_raw)
        budget = min(book.cash * 0.995, target_val)
        if budget < fill:
            return
        shares = (budget * (1.0 - FEE)) / fill
        cost = shares * fill
        if cost > book.cash or shares <= 0:
            return
        book.cash -= cost
        pos = sl.proxy.setdefault(ticker, ProxyPos(ticker=ticker))
        if pos.shares > 0 and pos.entry > 0:
            pos.entry = (pos.entry * pos.shares + fill * shares) / (pos.shares + shares)
        else:
            pos.entry = fill
        pos.shares += shares
        sl.proxy_trades += 1

    def set_proxy_weight(ticker: str, i: int, which: str, target_frac: float, eq: float) -> None:
        if ticker not in proxy_ohlc:
            return
        px = _px(proxy_ohlc[ticker], i, which)
        if px <= 0:
            if target_frac <= 0:
                sell_proxy(ticker, i, which)
            return
        pos = sl.proxy.setdefault(ticker, ProxyPos(ticker=ticker))
        cur_val = pos.shares * px if pos.shares > 0 else 0.0
        target_val = max(0.0, eq * target_frac)
        if target_frac <= 1e-9 or target_val < px:
            sell_proxy(ticker, i, which)
            return
        if cur_val > target_val * (1.0 + DRIFT_BAND):
            sell_proxy(ticker, i, which, shares=min(pos.shares, (cur_val - target_val) / px))
            return
        if cur_val < target_val * (1.0 - DRIFT_BAND):
            buy_proxy(ticker, i, which, target_val - cur_val)

    def free_cash(i: int, needed: float) -> None:
        for ticker in ("QLD", "QQQ"):
            if book.cash >= needed:
                return
            pos = sl.proxy.get(ticker)
            if pos is None or pos.shares <= 0:
                continue
            px = _px(proxy_ohlc[ticker], i, "open")
            if px <= 0:
                continue
            gap = needed - book.cash
            shares = min(pos.shares, gap / (px * (1.0 - SLIPPAGE) * (1.0 - FEE) + 1e-12))
            sell_proxy(ticker, i, "open", shares=shares)

    def rebalance(i: int, which: str, qqq_w: float, qld_w: float, force: bool = False) -> None:
        eq = equity_now(i, which)
        if eq <= 0:
            return
        if not force:
            def cur_w(tk: str) -> float:
                pos = sl.proxy.get(tk)
                if pos is None or pos.shares <= 0:
                    return 0.0
                px = _px(proxy_ohlc[tk], i, which)
                return (pos.shares * px / eq) if px > 0 else 0.0

            if abs(cur_w("QQQ") - qqq_w) + abs(cur_w("QLD") - qld_w) < DRIFT_BAND:
                return
        cur_qqq = sl.proxy.get("QQQ")
        px_q = _px(proxy_ohlc["QQQ"], i, which) if "QQQ" in proxy_ohlc else 0.0
        q_val = cur_qqq.shares * px_q if cur_qqq and px_q > 0 else 0.0
        if q_val > eq * qqq_w:
            set_proxy_weight("QQQ", i, which, qqq_w, eq)
            set_proxy_weight("QLD", i, which, qld_w, equity_now(i, which))
        else:
            set_proxy_weight("QLD", i, which, qld_w, eq)
            set_proxy_weight("QQQ", i, which, qqq_w, equity_now(i, which))

    for i in range(start_i, n):
        asof = i - 1
        score = float(msi[asof]) if asof < len(msi) else 50.0
        if score < MSI_GREEN:
            gate = "green"
            lev = target_leverage(spy[asof], sma200[asof], vix[asof])
            investable = 1.0
            allow_stocks = True
        elif score < MSI_RED:
            gate = "yellow"
            lev = 1.0
            investable = 1.0
            allow_stocks = False
        else:
            gate = "red"
            lev = 1.0
            investable = 0.50
            allow_stocks = False
        sl.regime_days[gate] += 1
        stats.regime_days[gate] += 1
        force_proxy = prev_lev is None or abs((prev_lev or 0.0) - lev) > 1e-9 or i == start_i

        if not allow_stocks:
            for tk in list(book.positions.keys()):
                pos = book.positions[tk]
                px = panels[tk]["open"][i]
                if not np.isfinite(px) or px <= 0:
                    px = pos.entry
                close_position(book, pos, float(px), dates[i], f"MSI_{gate.upper()}", i)
        else:
            is_bear = np.isfinite(spy[asof]) and np.isfinite(sma200[asof]) and sma200[asof] > 0 and spy[asof] < sma200[asof]
            max_slots = 2 if is_bear else MAX_SLOTS
            slot_w = (0.25, 0.25) if is_bear else CONVICTION
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
                if not np.isfinite(raw) or raw <= 0:
                    continue
                close_position(book, pos, float(raw), dates[i], reason, i)

            if len(book.positions) < max_slots:
                scored: List[Tuple[float, str]] = []
                allowed = allowed_by_day[i] if allowed_by_day is not None else None
                for tk, p in panels.items():
                    if tk in book.positions:
                        continue
                    if allowed is not None and tk not in allowed:
                        continue
                    if not p["listed"][i] or not p["listed"][i - 1]:
                        continue
                    if not entry_signal(p, i - 1):
                        continue
                    rs = composite[tk][i - 1] if tk in composite else np.nan
                    if not np.isfinite(rs):
                        continue
                    qrs = qqq_crs[i - 1]
                    if np.isfinite(qrs) and rs < qrs:
                        continue
                    scored.append((float(rs), tk))
                scored.sort(reverse=True)
                n_free = max_slots - len(book.positions)
                needed = equity_now(i, "open") * float(sum(slot_w[len(book.positions) : len(book.positions) + n_free]))
                if needed > book.cash:
                    free_cash(i, needed)
                for _, tk in scored:
                    if len(book.positions) >= max_slots:
                        break
                    p = panels[tk]
                    raw_open = p["open"][i]
                    if not np.isfinite(raw_open) or raw_open <= 0:
                        continue
                    fill = _fill_buy(float(raw_open))
                    slot_frac = slot_w[min(len(book.positions), len(slot_w) - 1)]
                    eq_open = equity_now(i, "open")
                    cap = eq_open * slot_frac
                    if fill <= 0 or cap < fill:
                        continue
                    if cap > book.cash:
                        free_cash(i, cap)
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

        eq = equity_now(i, "close")
        stock_w = stock_mark(i, "close") / eq if eq > 0 else 0.0
        qqq_w, qld_w = proxy_mix_capped(stock_w, lev, investable, has_qld)
        rebalance(i, "close", qqq_w, qld_w, force=force_proxy or not allow_stocks)

        eq = equity_now(i, "close")
        book.equity.append(eq)
        book.dates.append(dates[i])
        if eq > 0:
            sl.occupancy.append(stock_mark(i, "close") / eq)
            stats.occupancy.append(stock_mark(i, "close") / eq)
            stats.cash_frac.append(book.cash / eq)
            q_val = 0.0
            l_val = 0.0
            qp = sl.proxy.get("QQQ")
            lp = sl.proxy.get("QLD")
            qpx = _px(proxy_ohlc["QQQ"], i, "close") if "QQQ" in proxy_ohlc else 0.0
            lpx = _px(proxy_ohlc["QLD"], i, "close") if has_qld else 0.0
            if qp and qp.shares > 0 and qpx > 0:
                q_val = qp.shares * qpx
            if lp and lp.shares > 0 and lpx > 0:
                l_val = lp.shares * lpx
                sl.qld_days += 1
            sl.leverage.append((stock_mark(i, "close") + q_val + 2.0 * l_val) / eq)
            stats.leverage.append(sl.leverage[-1])
        prev_lev = lev

    last_i = n - 1
    for tk in list(book.positions.keys()):
        pos = book.positions[tk]
        px = panels[tk]["close"][last_i]
        if not np.isfinite(px) or px <= 0:
            px = pos.entry
        close_position(book, pos, float(px), dates[last_i], "EOB", last_i)
    for tk in list(sl.proxy.keys()):
        sell_proxy(tk, last_i, "close")
    return book, sl, stats


def overlay_pack(stats: OverlayStats) -> dict:
    occ = np.array(stats.occupancy, dtype=float)
    cash = np.array(stats.cash_frac, dtype=float)
    lev = np.array(stats.leverage, dtype=float)
    return {
        "avg_stock_occupancy_pct": round(float(np.nanmean(occ) * 100.0), 2) if len(occ) else 0.0,
        "avg_cash_pct": round(float(np.nanmean(cash) * 100.0), 2) if len(cash) else 0.0,
        "avg_gross_leverage": round(float(np.nanmean(lev)), 3) if len(lev) else 0.0,
        "signal_days": int(stats.signal_days),
        "fills": int(stats.fills),
        "regime_days": dict(stats.regime_days),
        "proxy_trades": int(stats.proxy_trades),
    }


def trades_per_year(m: dict) -> float:
    n = int(m.get("trades", {}).get("n") or 0)
    days = int(m.get("n_days") or 1)
    return round(n * 252.0 / max(1, days), 1)


def pack_alpha(book: Book, bench: pd.DataFrame, extra: dict) -> dict:
    out = pack_book(book, bench, extra=extra)
    t = out.get("trades") or {}
    t["trades_per_year"] = trades_per_year(out)
    out["trades"] = t
    return out


def score_row(name: str, m: dict, alpha=None, verdict="—") -> str:
    xs = alpha.get("excess_cagr_pct") if alpha else "—"
    t = m.get("trades") or {}
    wr = t.get("win_rate", "—")
    nt = t.get("n", "—")
    hold = t.get("avg_bars", "—")
    tpy = t.get("trades_per_year", "—")
    return (
        f"| {name} | {m['cagr_pct']:.2f}% | {m['mdd_pct']:.2f}% | {m['sharpe']:.3f} | "
        f"{m.get('calmar', 0):.3f} | {nt} | {tpy} | {hold} | {wr} | {xs} | {verdict} |"
    )


def diagnose(payload: dict) -> List[str]:
    w8 = payload["windows"].get("2018-08-31") or next(iter(payload["windows"].values()))
    w3 = payload["windows"].get("2023-08-31")
    q8 = w8["qqq_bh"]["cagr_pct"]
    c1 = w8["c1_v1"]
    a1, a2, a3 = w8["alpha1"], w8["alpha2"], w8["alpha3"]

    champ = max(
        [("C1 v1", c1), ("A1 Weekly", a1), ("A2 PEAD", a2), ("A3 MSI-C1", a3)],
        key=lambda kv: kv[1]["cagr_pct"],
    )
    lines = [
        "## 종합 평가 (스윗스팟)",
        "",
        f"- **8년 챔피언: {champ[0]}** CAGR {champ[1]['cagr_pct']:.2f}% / MDD {champ[1]['mdd_pct']:.2f}% "
        f"(QQQ {q8:.2f}% / {w8['qqq_bh']['mdd_pct']:.2f}%).",
        f"- **C1 v1** 8년 {c1['cagr_pct']:.2f}% (vs QQQ {w8['c1_vs_qqq'].get('excess_cagr_pct')}%p) / MDD {c1['mdd_pct']:.2f}%."
        + (
            f" AI 3년 {w3['c1_v1']['cagr_pct']:.2f}% (vs QQQ {w3['c1_vs_qqq'].get('excess_cagr_pct')}%p)."
            if w3
            else ""
        ),
        f"- **A1 위클리** 8년 {a1['cagr_pct']:.2f}% / MDD {a1['mdd_pct']:.2f}% / 승률 {a1.get('trades', {}).get('win_rate')}% / "
        f"연 {a1.get('trades', {}).get('trades_per_year')}회 / 평균보유 {a1.get('trades', {}).get('avg_bars')}일. "
        "현금 유휴(QQQ 파킹 없음)가 회전율 전략의 본전이다. 승률 55–65%와 연 80–120회가 동시에 안 나오면 단기 스윙 허들은 미달.",
        f"- **A2 PEAD** 8년 {a2['cagr_pct']:.2f}% / MDD {a2['mdd_pct']:.2f}% / 거래 {a2.get('trades', {}).get('n')}건. "
        "유휴는 QQQ 파킹. +15% 서프라이즈는 메가캡을 자주 걸러 슬롯이 비면 QQQ B&H에 수렴한다.",
        f"- **A3 MSI 게이트** 8년 {a3['cagr_pct']:.2f}% / MDD {a3['mdd_pct']:.2f}% "
        f"(목표 MDD −15% {'달성' if a3['mdd_pct'] >= -15.0 else '미달'}). "
        f"국면 {a3.get('sleeve', {}).get('regime_days')}. "
        "Yellow에서 주도주를 QQQ로 접으면 2020/2023 V자 복리를 다시 잘라 C1보다 CAGR이 낮아질 수 있다.",
        "- **해석:** 세 알파가 C1을 8년과 AI 3년 모두에서 이기지 못하면, 단기 회전·PEAD·매크로 현금 게이트는 "
        "**C1 3슬롯 스윙의 대체재가 아니라 별도 슬리브 후보**다. 번들은 상관 낮은 슬리브일 때만 synthetic 알파가 된다.",
        "",
    ]
    return lines


def write_report(payload: dict) -> None:
    meta = payload["meta"]
    lines = [
        "# Triple-Alpha Tournament — Weekly / PEAD / MSI-C1",
        "",
        f"- 생성: {meta.get('generated')}",
        f"- 마찰: slippage {meta['friction']['slippage_bps']:.0f}bps / fee {meta['friction']['fee_bps']:.0f}bps / 익일 시가 체결",
        f"- PIT 유니버스: composite RS Dynamic 60. QLD={'yes' if meta.get('qld_available') else 'no'}",
        f"- PEAD 이벤트: surprise>+{PEAD_SURPRISE:.0f}% & rev YoY>0, settled {meta.get('pead_fill_days')} fill-days",
        f"- MSI 8y 국면: {meta.get('msi_counts')}",
        "- NLP: 8년 YouTube PIT 아카이브 없음 → HYG−IEF 20d 크레딧을 `evaluate_macro_stance` defense/buy 토큰으로 투입",
        "",
        "## 엔진",
        "",
        "- **C1 v1** — 3슬롯 50/30/20 + 유휴 QQQ + SPY≥200 & VIX<20 → 1.5x. 스윙 챔피언 베이스라인.",
        "- **A1 Weekly** — 2음봉 후 SMA5 상향 + RSI(14) 40–55. +4%/−2.5%, 3거래일 또는 금요일 종가. 유휴는 현금.",
        "- **A2 PEAD** — 실적 갭 3–5일 안착 후 진입, 60거래일 또는 SMA20 이탈, 다음 실적 2일 전 청산. 유휴 QQQ.",
        "- **A3 MSI-C1** — MSI<35 C1 풀가동 / 35–65 QQQ 100% (레버리지 OFF, 주식 청산) / ≥65 현금 50%.",
        "",
    ]
    header = (
        "| Book | CAGR | MDD | Sharpe | Calmar | Trades | Trades/yr | Avg bars | Win% | vs QQQ | Verdict |"
    )
    sep = "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"
    for asof, block in payload["windows"].items():
        end = block["qqq_bh"]["end"]
        lines += [
            f"## Window {asof} → {end}",
            "",
            header,
            sep,
            score_row("QQQ B&H", block["qqq_bh"]),
            score_row("QLD B&H", block["qld_bh"]) if block.get("qld_bh") else "",
            score_row("C1 v1", block["c1_v1"], block.get("c1_vs_qqq"), block.get("c1_verdict")),
            score_row("**A1 Weekly**", block["alpha1"], block.get("a1_vs_qqq"), block.get("a1_verdict")),
            score_row("**A2 PEAD**", block["alpha2"], block.get("a2_vs_qqq"), block.get("a2_verdict")),
            score_row("**A3 MSI-C1**", block["alpha3"], block.get("a3_vs_qqq"), block.get("a3_verdict")),
            "",
            f"- A1 occupancy {block['alpha1'].get('sleeve', {}).get('avg_stock_occupancy_pct')}%  "
            f"cash {block['alpha1'].get('sleeve', {}).get('avg_cash_pct')}%  "
            f"signal-days {block['alpha1'].get('sleeve', {}).get('signal_days')}  "
            f"reasons {block['alpha1'].get('trades', {}).get('reasons')}",
            f"- A2 occupancy {block['alpha2'].get('sleeve', {}).get('avg_stock_occupancy_pct')}%  "
            f"reasons {block['alpha2'].get('trades', {}).get('reasons')}",
            f"- A3 occupancy {block['alpha3'].get('sleeve', {}).get('avg_stock_occupancy_pct')}%  "
            f"cash {block['alpha3'].get('sleeve', {}).get('avg_cash_pct')}%  "
            f"MSI days {block['alpha3'].get('sleeve', {}).get('regime_days')}  "
            f"lev {block['alpha3'].get('sleeve', {}).get('avg_gross_leverage')}",
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
        f"  {tag:16s} CAGR {m['cagr_pct']:6.2f}%  MDD {m['mdd_pct']:7.2f}%  "
        f"Sharpe {m['sharpe']:.3f}  Calmar {m.get('calmar', 0):.3f}  "
        f"n={t.get('n')}  tpy={t.get('trades_per_year')}  hold={t.get('avg_bars')}  win={t.get('win_rate')}"
    )


def verdict_of(cagr: float, mdd: float, qqq_cagr: float) -> str:
    if cagr > qqq_cagr and mdd >= -15.0:
        return "BEAT_QQQ_MDD_TIGHT"
    if cagr > qqq_cagr and mdd >= -38.0:
        return "BEAT_QQQ_MDD_OK"
    if cagr > qqq_cagr:
        return "BEAT_QQQ_MDD_HEAVY"
    return "LOSE_QQQ"


def main() -> None:
    t0 = time.time()
    raw = download_master()
    raw = ensure_qld(raw)
    raw = ensure_macro(raw)
    names = [t for t in stock_names() if t in raw and "." not in t]
    sliced = {k: v for k, v in raw.items() if k in set(names) | set(BENCH)}
    calendar, panels, bench = align_panels(sliced)
    print(f"[panel] {len(panels)} names, {len(calendar)} days")

    proxy_ohlc = {"QQQ": aligned_ohlc(raw, "QQQ", calendar)}
    if "QLD" in raw:
        proxy_ohlc["QLD"] = aligned_ohlc(raw, "QLD", calendar)
        print(f"[proxy] QLD ok={int(proxy_ohlc['QLD']['ok'].sum())}")

    print("[universe] composite RS …")
    universes = build_daily_universes_composite(calendar, raw)
    composite = {t: composite_rs(p["close"]) for t, p in panels.items()}
    extras = build_extras(raw, panels, calendar)
    msi, _msi_tags, msi_counts = build_msi_series(calendar, raw, bench)
    print(f"[msi] counts={msi_counts} mean={float(np.nanmean(msi)):.1f} max={float(np.nanmax(msi)):.1f}")

    print("[earnings] …")
    earnings = load_earnings(names)
    pead_fills, next_earn = build_pead_entries(calendar, panels, earnings)

    qqq_px = bench["qqq"].copy()
    qqq_px.index = pd.to_datetime(qqq_px.index)
    qld_px = pd.Series(proxy_ohlc["QLD"]["close"], index=calendar) if "QLD" in proxy_ohlc else None

    payload = {
        "meta": {
            "method": (
                "A1 weekly 2-red+SMA5+RSI40-55, +4/-2.5, 3d or Friday, cash idle. "
                "A2 PEAD surprise>15 & revYoY>0, 3-5d gap settle, 60d/SMA20/pre-print, idle QQQ. "
                "A3 C1 gated by MSI2.0 <35 full / <65 QQQ100 / else 50% cash."
            ),
            "initial": INITIAL,
            "friction": {"slippage_bps": SLIPPAGE * 10000, "fee_bps": FEE * 10000},
            "qld_available": "QLD" in proxy_ohlc,
            "msi_counts": msi_counts,
            "pead_fill_days": len(pead_fills),
            "generated": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        },
        "windows": {},
    }

    for asof in SNAPSHOTS:
        start_i = max(snapshot_index(calendar, asof), 20)
        print(f"\n===== WINDOW {asof} ({calendar[start_i].date()}) =====")

        c1_book, c1_sl = run_alpha_book(
            calendar, panels, bench, composite, universes, proxy_ohlc, start_i
        )
        a1_book, a1_st = run_weekly_book(calendar, panels, extras, universes, proxy_ohlc, start_i)
        a2_book, a2_st = run_pead_book(calendar, panels, extras, pead_fills, next_earn, proxy_ohlc, start_i)
        a3_book, a3_sl, a3_st = run_msi_c1(
            calendar, panels, bench, composite, universes, proxy_ohlc, msi, start_i
        )

        c1 = pack_alpha(c1_book, bench, extra={"sleeve": sleeve_stats(c1_sl)})
        a1 = pack_alpha(a1_book, bench, extra={"sleeve": overlay_pack(a1_st)})
        a2 = pack_alpha(a2_book, bench, extra={"sleeve": overlay_pack(a2_st)})
        a3_extra = overlay_pack(a3_st)
        a3_extra.update(sleeve_stats(a3_sl))
        a3_extra["regime_days"] = dict(a3_sl.regime_days)
        a3 = pack_alpha(a3_book, bench, extra={"sleeve": a3_extra})

        idx = pd.to_datetime(c1_book.dates)
        qqq_m = enrich(bh_metrics(qqq_px.reindex(idx)))
        qld_m = enrich(bh_metrics(qld_px.reindex(idx))) if qld_px is not None else None

        c1_vs = vs_qqq(c1.pop("_rets"), qqq_m, c1)
        a1_vs = vs_qqq(a1.pop("_rets"), qqq_m, a1)
        a2_vs = vs_qqq(a2.pop("_rets"), qqq_m, a2)
        a3_vs = vs_qqq(a3.pop("_rets"), qqq_m, a3)

        def verd(m):
            return verdict_of(m["cagr_pct"], m["mdd_pct"], qqq_m["cagr_pct"])

        block = {
            "qqq_bh": qqq_m,
            "qld_bh": qld_m,
            "c1_v1": c1,
            "alpha1": a1,
            "alpha2": a2,
            "alpha3": a3,
            "c1_vs_qqq": c1_vs,
            "a1_vs_qqq": a1_vs,
            "a2_vs_qqq": a2_vs,
            "a3_vs_qqq": a3_vs,
            "c1_verdict": verd(c1),
            "a1_verdict": verd(a1),
            "a2_verdict": verd(a2),
            "a3_verdict": verd(a3),
        }
        payload["windows"][asof] = block

        print(_line("QQQ B&H", qqq_m))
        if qld_m:
            print(_line("QLD B&H", qld_m))
        print(_line("C1 v1", c1) + f"  vsQQQ {c1_vs.get('excess_cagr_pct')}  {block['c1_verdict']}")
        print(_line("A1 Weekly", a1) + f"  vsQQQ {a1_vs.get('excess_cagr_pct')}  {block['a1_verdict']}")
        print(_line("A2 PEAD", a2) + f"  vsQQQ {a2_vs.get('excess_cagr_pct')}  {block['a2_verdict']}")
        print(_line("A3 MSI-C1", a3) + f"  vsQQQ {a3_vs.get('excess_cagr_pct')}  {block['a3_verdict']}")
        print(f"  A1 reasons {a1.get('trades', {}).get('reasons')}")
        print(f"  A2 reasons {a2.get('trades', {}).get('reasons')}")
        print(f"  A3 MSI days {a3.get('sleeve', {}).get('regime_days')}")

    payload["meta"]["elapsed_sec"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(json_safe(payload), f, ensure_ascii=False, indent=2)
    write_report(payload)
    print(f"\nwrote {OUT_PATH}")
    print(f"wrote {REPORT_PATH}")
    print(f"elapsed {payload['meta']['elapsed_sec']}s")
    print()
    for line in diagnose(payload):
        print(line)


if __name__ == "__main__":
    main()
