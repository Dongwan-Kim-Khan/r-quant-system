"""
A/B portfolio backtest: HTML Phase-5 exits vs current live guardian exits.

Same entries (v2 dual-momentum 3-Gate), same 3/2-slot regime, same friction.
Only the pre-trailing exit rule differs:

  HTML   : -4% hard stop OR (+15% peak then trailing floor). Hold otherwise.
  LIVE   : HTML plus 26D kijun close breakdown at any PnL (the rule that sold at -2.91%).

Outputs Sharpe, MDD, and OLS alpha of LIVE vs HTML.
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
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

from al_sangmoo.core.constants import STOP_LOSS_PCT, TAKE_PROFIT_PCT, WATCHLIST
from al_sangmoo.domain.quant.ichimoku import calculate_ichimoku_indicators

START = "2016-01-01"
END = "2026-08-29"
INITIAL = 100_000.0
SLIPPAGE = 0.0010
FEE = 0.0008
ATR_MULT = 2.5
CACHE_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "html_vs_live_ohlcv.pkl")
OUT_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "html_vs_live_results.json")

BENCH = ["SPY", "QQQ", "^VIX"]
UNIVERSE = [t for t in WATCHLIST]


@dataclass
class Position:
    ticker: str
    shares: float
    entry: float
    entry_i: int
    peak_high: float
    entry_date: str


@dataclass
class Book:
    cash: float
    positions: Dict[str, Position] = field(default_factory=dict)
    trades: List[dict] = field(default_factory=list)
    equity: List[float] = field(default_factory=list)
    dates: List[str] = field(default_factory=list)


def _flatten_ticker_frame(raw: pd.DataFrame, ticker: str) -> Optional[pd.DataFrame]:
    if raw is None or raw.empty:
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        if ticker in raw.columns.get_level_values(0):
            df = raw[ticker].copy()
        elif ticker in raw.columns.get_level_values(1):
            df = raw.xs(ticker, axis=1, level=1).copy()
        else:
            df = raw.copy()
            df.columns = df.columns.get_level_values(0)
    else:
        df = raw.copy()
    need = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in df.columns for c in need):
        return None
    df = df[need].dropna(how="all")
    df = df[df["Close"].notna() & (df["Close"] > 0)]
    if len(df) < 80:
        return None
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


def download_all() -> Dict[str, pd.DataFrame]:
    import yfinance as yf

    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    if os.path.exists(CACHE_PATH):
        age_h = (time.time() - os.path.getmtime(CACHE_PATH)) / 3600.0
        if age_h < 24:
            print(f"[cache] loading {CACHE_PATH} ({age_h:.1f}h old)")
            return pd.read_pickle(CACHE_PATH)

    tickers = list(dict.fromkeys(UNIVERSE + BENCH))
    print(f"[download] {len(tickers)} tickers {START} → {END}")
    raw = yf.download(
        tickers,
        start=START,
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
            print(f"  {t}: {len(df)} bars")
        except Exception as exc:
            print(f"  skip {t}: {exc}")
    pd.to_pickle(out, CACHE_PATH)
    print(f"[cache] wrote {CACHE_PATH}")
    return out


def align_panels(raw: Dict[str, pd.DataFrame]) -> Tuple[pd.DatetimeIndex, Dict[str, dict], pd.DataFrame]:
    spy = raw["SPY"].copy()
    calendar = spy.index.sort_values()
    spy = calculate_ichimoku_indicators(spy)
    spy_close = spy["Close"].reindex(calendar)
    spy_sma200 = spy["SMA200"].reindex(calendar)
    qqq = calculate_ichimoku_indicators(raw["QQQ"].reindex(calendar).ffill())
    vix = raw["^VIX"].reindex(calendar).ffill() if "^VIX" in raw else None

    bench = pd.DataFrame(index=calendar)
    bench["spy"] = spy_close
    bench["spy_sma200"] = spy_sma200
    bench["qqq"] = qqq["Close"]
    bench["vix"] = vix["Close"] if vix is not None else np.nan
    bench["bull"] = (bench["spy"] >= bench["spy_sma200"]).astype(float)

    panels: Dict[str, dict] = {}
    for t, df in raw.items():
        if t in BENCH:
            continue
        aligned = df.reindex(calendar)
        # do not ffill across long gaps before listing; drop leading NaN after indicators
        filled = aligned.copy()
        filled[["Open", "High", "Low", "Close"]] = filled[["Open", "High", "Low", "Close"]].ffill(limit=3)
        filled["Volume"] = filled["Volume"].fillna(0.0)
        if filled["Close"].notna().sum() < 80:
            continue
        ind = calculate_ichimoku_indicators(filled)
        panels[t] = {
            "open": ind["Open"].to_numpy(dtype=float),
            "high": ind["High"].to_numpy(dtype=float),
            "low": ind["Low"].to_numpy(dtype=float),
            "close": ind["Close"].to_numpy(dtype=float),
            "kijun": ind["Kijun"].to_numpy(dtype=float),
            "tenkan": ind["Tenkan"].to_numpy(dtype=float),
            "span_a": ind["SpanA"].to_numpy(dtype=float),
            "span_b": ind["SpanB"].to_numpy(dtype=float),
            "vol_ratio": ind["Vol_Ratio"].to_numpy(dtype=float),
            "high20": ind["High_20D"].to_numpy(dtype=float),
            "atr": ind["ATR14"].to_numpy(dtype=float),
            "obv": ind["OBV"].to_numpy(dtype=float),
            "obv_ma": ind["OBV_MA20"].to_numpy(dtype=float),
            "rs": ind["RS_3M"].to_numpy(dtype=float),
            "listed": np.isfinite(ind["Close"].to_numpy(dtype=float))
            & (ind["Close"].to_numpy(dtype=float) > 0)
            & np.isfinite(ind["Kijun"].to_numpy(dtype=float))
            & np.isfinite(ind["SpanA"].to_numpy(dtype=float)),
        }
    return calendar, panels, bench


def entry_signal(p: dict, i: int) -> bool:
    """Evaluate signal on bar i (yesterday) for fill at i+1 open."""
    if i < 1 or not p["listed"][i] or not p["listed"][i - 1]:
        return False
    close = p["close"][i]
    kijun = p["kijun"][i]
    tenkan = p["tenkan"][i]
    span_a = p["span_a"][i]
    span_b = p["span_b"][i]
    if not np.isfinite([close, kijun, tenkan, span_a, span_b]).all():
        return False
    if kijun <= 0 or close <= 0:
        return False
    cloud_top = max(span_a, span_b)
    above_cloud = close >= cloud_top
    tenkan_ok = tenkan >= kijun
    obv_ok = np.isfinite(p["obv"][i]) and np.isfinite(p["obv_ma"][i]) and p["obv"][i] > p["obv_ma"][i]
    high20 = p["high20"][i]
    engine_a = above_cloud and tenkan_ok and obv_ok and np.isfinite(high20) and close > high20
    prev_close = p["close"][i - 1]
    prev_kijun = p["kijun"][i - 1]
    engine_b = (
        above_cloud
        and tenkan_ok
        and obv_ok
        and np.isfinite(prev_close)
        and np.isfinite(prev_kijun)
        and prev_close <= prev_kijun
        and close > kijun
    )
    kijun_gap = (close - kijun) / kijun
    vdu = np.isfinite(p["vol_ratio"][i]) and p["vol_ratio"][i] <= 0.75
    gate_vdu = above_cloud and tenkan_ok and vdu and (-0.005 <= kijun_gap <= 0.040)
    return bool(engine_a or engine_b or gate_vdu)


def _fill_buy(px: float) -> float:
    return px * (1.0 + SLIPPAGE)


def _fill_sell(px: float) -> float:
    return px * (1.0 - SLIPPAGE)


def decide_exit(
    mode: str,
    entry: float,
    peak_high: float,
    close: float,
    low: float,
    kijun: float,
    atr: float,
) -> Optional[str]:
    if entry <= 0 or close <= 0:
        return None
    hard = entry * (1.0 + STOP_LOSS_PCT)
    if np.isfinite(low) and low <= hard:
        return "HARD_STOP"
    peak_gain = (peak_high - entry) / entry if peak_high > 0 else 0.0
    trailing_active = peak_gain >= TAKE_PROFIT_PCT
    if trailing_active:
        cands = []
        if np.isfinite(kijun) and kijun > 0:
            cands.append(kijun)
        if np.isfinite(atr) and atr > 0 and peak_high > 0:
            atr_leg = peak_high - ATR_MULT * atr
            if atr_leg > 0:
                cands.append(atr_leg)
        floor = max(cands) if cands else 0.0
        if floor > 0 and close < floor:
            return "TRAILING"
        return None
    if mode == "LIVE" and np.isfinite(kijun) and kijun > 0 and close < kijun:
        return "KIJUN"
    return None


def mark_equity(book: Book, i: int, panels: Dict[str, dict]) -> float:
    eq = book.cash
    for tk, pos in book.positions.items():
        px = panels[tk]["close"][i]
        if np.isfinite(px) and px > 0:
            eq += pos.shares * px
        else:
            eq += pos.shares * pos.entry
    return eq


def close_position(book: Book, pos: Position, raw_px: float, date: str, reason: str, i: int) -> None:
    fill = _fill_sell(raw_px)
    proceeds = pos.shares * fill * (1.0 - FEE)
    book.cash += proceeds
    pnl_pct = ((fill - pos.entry) / pos.entry) * 100.0
    book.trades.append(
        {
            "ticker": pos.ticker,
            "entry_date": pos.entry_date,
            "exit_date": date,
            "entry": round(pos.entry, 4),
            "exit": round(fill, 4),
            "pnl_pct": round(pnl_pct, 4),
            "bars": i - pos.entry_i,
            "reason": reason,
        }
    )
    del book.positions[pos.ticker]


def run_book(
    mode: str,
    calendar: pd.DatetimeIndex,
    panels: Dict[str, dict],
    bench: pd.DataFrame,
    start_i: int = 220,
    allowed_by_day: Optional[List] = None,
) -> Book:
    n = len(calendar)
    dates = [d.strftime("%Y-%m-%d") for d in calendar]
    bull = bench["bull"].to_numpy(dtype=float)
    book = Book(cash=INITIAL)
    start_i = max(1, int(start_i))

    for i in range(start_i, n):
        # 1) exits on today's bar (intraday hard-stop wick, close-based kijun/trailing)
        for tk in list(book.positions.keys()):
            pos = book.positions[tk]
            p = panels[tk]
            if not p["listed"][i]:
                continue
            high = p["high"][i]
            if np.isfinite(high):
                pos.peak_high = max(pos.peak_high, high)
            reason = decide_exit(
                mode,
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

        # 2) entries at today's open from yesterday's signal, if slots free
        is_bull = bool(bull[i] >= 0.5) if np.isfinite(bull[i]) else True
        max_slots = 3 if is_bull else 2
        slot_frac = 0.333 if is_bull else 0.250
        if len(book.positions) < max_slots and i >= 1:
            scored: List[Tuple[float, str]] = []
            allowed = None if allowed_by_day is None else allowed_by_day[i]
            for tk, p in panels.items():
                if tk in book.positions:
                    continue
                if allowed is not None and tk not in allowed:
                    continue
                if not p["listed"][i] or not p["listed"][i - 1]:
                    continue
                if not entry_signal(p, i - 1):
                    continue
                rs = p["rs"][i - 1]
                if not np.isfinite(rs):
                    rs = -999.0
                scored.append((float(rs), tk))
            scored.sort(reverse=True)
            equity_now = mark_equity(book, i, panels)
            for _, tk in scored:
                if len(book.positions) >= max_slots:
                    break
                p = panels[tk]
                raw_open = p["open"][i]
                if not np.isfinite(raw_open) or raw_open <= 0:
                    continue
                fill = _fill_buy(float(raw_open))
                cap = equity_now * slot_frac
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
                equity_now = mark_equity(book, i, panels)

        eq = mark_equity(book, i, panels)
        book.equity.append(eq)
        book.dates.append(dates[i])

    # flatten remaining
    last_i = n - 1
    for tk in list(book.positions.keys()):
        pos = book.positions[tk]
        px = panels[tk]["close"][last_i]
        if not np.isfinite(px) or px <= 0:
            px = pos.entry
        close_position(book, pos, float(px), dates[last_i], "EOB", last_i)

    return book


def metrics(equity: List[float], dates: List[str], initial: float = INITIAL) -> dict:
    eq = np.array(equity, dtype=float)
    rets = np.diff(eq) / eq[:-1]
    rets = rets[np.isfinite(rets)]
    years = max(1e-9, len(eq) / 252.0)
    final = float(eq[-1])
    total = (final / initial - 1.0) * 100.0
    cagr = ((final / initial) ** (1.0 / years) - 1.0) * 100.0 if final > 0 else 0.0
    vol = float(np.std(rets, ddof=1)) if len(rets) > 5 else 0.0
    sharpe = float((np.mean(rets) / vol) * np.sqrt(252)) if vol > 0 else 0.0
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    mdd = float(dd.min() * 100.0)
    return {
        "final_equity": round(final, 2),
        "total_return_pct": round(total, 2),
        "cagr_pct": round(cagr, 2),
        "sharpe": round(sharpe, 3),
        "mdd_pct": round(mdd, 2),
        "n_days": int(len(eq)),
        "start": dates[0],
        "end": dates[-1],
    }


def trade_stats(trades: List[dict]) -> dict:
    if not trades:
        return {"n": 0, "win_rate": 0.0, "profit_factor": 0.0, "avg_pnl": 0.0, "reasons": {}}
    pnls = np.array([t["pnl_pct"] for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    gp = float(wins.sum()) if len(wins) else 0.0
    gl = float(np.abs(losses.sum())) if len(losses) else 0.0
    reasons = defaultdict(lambda: {"n": 0, "avg_pnl": 0.0, "pnls": []})
    for t in trades:
        reasons[t["reason"]]["n"] += 1
        reasons[t["reason"]]["pnls"].append(t["pnl_pct"])
    reason_out = {}
    for k, v in reasons.items():
        reason_out[k] = {
            "n": v["n"],
            "avg_pnl": round(float(np.mean(v["pnls"])), 3),
            "share_pct": round(100.0 * v["n"] / len(trades), 1),
        }
    return {
        "n": len(trades),
        "win_rate": round(100.0 * len(wins) / len(trades), 2),
        "profit_factor": round(gp / gl, 3) if gl > 0 else 99.0,
        "avg_pnl": round(float(pnls.mean()), 3),
        "median_pnl": round(float(np.median(pnls)), 3),
        "avg_bars": round(float(np.mean([t["bars"] for t in trades])), 1),
        "reasons": reason_out,
    }


def ols(y: np.ndarray, X: np.ndarray) -> dict:
    n, k = X.shape
    beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    sse = float(resid @ resid)
    sigma2 = sse / max(1, n - k)
    xtx = X.T @ X
    try:
        xtx_inv = np.linalg.inv(xtx)
    except np.linalg.LinAlgError:
        xtx_inv = np.linalg.pinv(xtx)
    se = np.sqrt(np.maximum(np.diag(xtx_inv) * sigma2, 0.0))
    tstat = np.divide(beta, se, out=np.zeros_like(beta), where=se > 0)
    # two-sided t p-value without scipy
    # Abramowitz approximation via erfc of normal — good enough at n~2500
    from math import erfc, sqrt

    pvals = []
    for t in tstat:
        p = float(erfc(abs(float(t)) / sqrt(2.0)))  # normal approx
        pvals.append(p)
    r2 = 1.0 - sse / max(1e-18, float(((y - y.mean()) ** 2).sum()))
    return {
        "beta": [round(float(b), 6) for b in beta],
        "se": [round(float(s), 6) for s in se],
        "t": [round(float(t), 3) for t in tstat],
        "p": [round(float(p), 4) for p in pvals],
        "r2": round(float(r2), 4),
        "n": int(n),
    }


def aligned_returns(book: Book, bench: pd.DataFrame) -> pd.DataFrame:
    s = pd.Series(book.equity, index=pd.to_datetime(book.dates), name="eq")
    df = pd.DataFrame({"eq": s})
    df["r"] = df["eq"].pct_change()
    b = bench.copy()
    b.index = pd.to_datetime(b.index)
    df["spy"] = b["spy"].reindex(df.index).pct_change()
    df["qqq"] = b["qqq"].reindex(df.index).pct_change()
    df["vix"] = b["vix"].reindex(df.index)
    df["bull"] = b["bull"].reindex(df.index)
    return df.dropna()


def period_slice(eq: List[float], dates: List[str], start: str) -> Optional[dict]:
    idx = next((i for i, d in enumerate(dates) if d >= start), None)
    if idx is None or idx >= len(eq) - 5:
        return None
    slice_eq = eq[idx:]
    slice_dt = dates[idx:]
    initial = slice_eq[0]
    return metrics(slice_eq, slice_dt, initial=initial)


def downsample_equity(dates: List[str], eq: List[float], every: int = 21) -> List[dict]:
    pts = []
    for i in range(0, len(dates), every):
        pts.append({"date": dates[i][:7], "equity": round(eq[i] / INITIAL * 100.0, 2)})
    if dates[-1] != pts[-1]["date"]:
        pts.append({"date": dates[-1][:7], "equity": round(eq[-1] / INITIAL * 100.0, 2)})
    return pts


def main() -> None:
    t0 = time.time()
    raw = download_all()
    if "SPY" not in raw or "QQQ" not in raw:
        raise SystemExit("SPY/QQQ download failed")
    calendar, panels, bench = align_panels(raw)
    print(f"[panel] {len(panels)} names, {len(calendar)} days")

    html = run_book("HTML", calendar, panels, bench)
    live = run_book("LIVE", calendar, panels, bench)

    m_html = metrics(html.equity, html.dates)
    m_live = metrics(live.equity, live.dates)
    t_html = trade_stats(html.trades)
    t_live = trade_stats(live.trades)

    r_html = aligned_returns(html, bench)
    r_live = aligned_returns(live, bench)
    both = pd.DataFrame({"html": r_html["r"], "live": r_live["r"]}).dropna()
    both["qqq"] = r_html["qqq"].reindex(both.index)
    both["spy"] = r_html["spy"].reindex(both.index)
    both["vix"] = r_html["vix"].reindex(both.index)
    both["bull"] = r_html["bull"].reindex(both.index)
    both = both.dropna()

    y = both["live"].to_numpy()
    x_html = np.column_stack([np.ones(len(both)), both["html"].to_numpy()])
    vs_html = ols(y, x_html)

    x_mkt_live = np.column_stack(
        [
            np.ones(len(both)),
            both["qqq"].to_numpy(),
            both["vix"].to_numpy(),
            both["bull"].to_numpy(),
        ]
    )
    live_mkt = ols(y, x_mkt_live)
    html_mkt = ols(both["html"].to_numpy(), x_mkt_live)

    # annualized alpha (daily intercept * 252)
    def ann_alpha(model: dict) -> dict:
        daily = model["beta"][0]
        return {
            "daily_pct": round(daily * 100.0, 4),
            "annualized_pct": round(daily * 252 * 100.0, 2),
            "t": model["t"][0],
            "p": model["p"][0],
        }

    periods = {
        "full": None,
        "3y": "2023-08-28",
        "ytd": "2026-01-01",
        "2022_bear": None,
    }
    sliced = {}
    for label, start in (("full", html.dates[0]), ("7y", "2019-08-28"), ("3y", "2023-08-28"), ("ytd", "2026-01-01")):
        h = period_slice(html.equity, html.dates, start)
        l = period_slice(live.equity, live.dates, start)
        sliced[label] = {"html": h, "live": l}

    # 2022 calendar year
    def year_slice(eq, dates, year):
        pairs = [(d, e) for d, e in zip(dates, eq) if d.startswith(str(year))]
        if len(pairs) < 10:
            return None
        ds, es = zip(*pairs)
        return metrics(list(es), list(ds), initial=es[0])

    sliced["2022"] = {"html": year_slice(html.equity, html.dates, 2022), "live": year_slice(live.equity, live.dates, 2022)}

    payload = {
        "meta": {
            "start": html.dates[0],
            "end": html.dates[-1],
            "universe_n": len(panels),
            "universe": sorted(panels.keys()),
            "initial": INITIAL,
            "friction": {"slippage_bps": SLIPPAGE * 10000, "fee_bps": FEE * 10000},
            "elapsed_sec": round(time.time() - t0, 1),
            "entry": "Engine A 20D breakout OR Engine B kijun bounce OR VDU pullback; 3-slot bull / 2-slot bear",
            "html_exit": "-4% hard stop OR +15% peak then max(kijun, peak-2.5*ATR). Hold in between.",
            "live_exit": "HTML plus any-time 26D kijun close breakdown (current guardian).",
        },
        "html": {**m_html, **{"trades": t_html}},
        "live": {**m_live, **{"trades": t_live}},
        "ols_live_on_html": {
            **vs_html,
            "alpha": ann_alpha(vs_html),
            "beta_html": vs_html["beta"][1],
            "spec": "R_live = alpha + beta * R_html + e",
        },
        "ols_live_on_qqq_vix_bull": {
            **live_mkt,
            "alpha": ann_alpha(live_mkt),
            "names": ["const", "QQQ", "VIX_level", "Bull"],
            "spec": "R_live = a + b_qqq*R_qqq + b_vix*VIX + b_bull*Bull + e",
        },
        "ols_html_on_qqq_vix_bull": {
            **html_mkt,
            "alpha": ann_alpha(html_mkt),
            "names": ["const", "QQQ", "VIX_level", "Bull"],
            "spec": "R_html = a + b_qqq*R_qqq + b_vix*VIX + b_bull*Bull + e",
        },
        "periods": sliced,
        "equity_monthly": {
            "categories": [p["date"] for p in downsample_equity(html.dates, html.equity)],
            "html": [p["equity"] for p in downsample_equity(html.dates, html.equity)],
            "live": [p["equity"] for p in downsample_equity(live.dates, live.equity)],
        },
        "verdict": None,
    }

    a = payload["ols_live_on_html"]["alpha"]
    if a["annualized_pct"] > 0 and a["p"] < 0.10:
        verdict = "KEEP_LIVE"
        note = "LIVE has positive alpha vs HTML (p<0.10)."
    elif a["annualized_pct"] > 0:
        verdict = "WEAK_KEEP"
        note = "LIVE alpha vs HTML is positive but not statistically significant."
    else:
        verdict = "REVERT_HTML"
        note = "LIVE does not produce alpha vs HTML. Revert to HTML Phase-5 exits."
    payload["verdict"] = {"code": verdict, "note": note, **a}

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("\n========== HTML vs LIVE EXIT ==========")
    print(f"Period {m_html['start']} → {m_html['end']} | N={len(panels)} names")
    print(f"{'':22} {'HTML':>12} {'LIVE':>12}")
    for k in ("total_return_pct", "cagr_pct", "sharpe", "mdd_pct"):
        print(f"{k:22} {m_html[k]:12} {m_live[k]:12}")
    print(f"{'trades':22} {t_html['n']:12} {t_live['n']:12}")
    print(f"{'win_rate':22} {t_html['win_rate']:12} {t_live['win_rate']:12}")
    print(f"{'profit_factor':22} {t_html['profit_factor']:12} {t_live['profit_factor']:12}")
    print("HTML reasons:", t_html["reasons"])
    print("LIVE reasons:", t_live["reasons"])
    print("\nOLS  R_live = a + b * R_html")
    print(f"  daily alpha {a['daily_pct']}%  ann {a['annualized_pct']}%  t={a['t']} p={a['p']}")
    print(f"  beta={vs_html['beta'][1]}  R2={vs_html['r2']}  n={vs_html['n']}")
    print("LIVE vs QQQ/VIX/Bull alpha ann", payload["ols_live_on_qqq_vix_bull"]["alpha"])
    print("HTML vs QQQ/VIX/Bull alpha ann", payload["ols_html_on_qqq_vix_bull"]["alpha"])
    print("VERDICT:", verdict, note)
    print(f"wrote {OUT_PATH} in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
