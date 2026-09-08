"""Time-series, profit-factor, and OLS diagnostics for leader80 monthly."""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
from math import erfc, sqrt
from typing import Dict, List, Tuple

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

from al_sangmoo.domain.quant.dynamic_universe import SECTOR_ETF_MAP  # noqa: E402
from compare_html_vs_live_exit import BENCH, align_panels  # noqa: E402
from pit_dynamic_universe_exit_ab import aligned_close, rolling_rs, stock_names  # noqa: E402
from pit_universe_exit_ab import download_master, snapshot_index  # noqa: E402
from regime_bull_core import aligned_open  # noqa: E402
from regime_leader80 import SNAPSHOTS, SLOPE_BARS, run_book  # noqa: E402
from regime_slope_html import add_dvol  # noqa: E402

OUT = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "leader80_stats.json")


def _p_from_t(t: float) -> float:
    return float(erfc(abs(float(t)) / sqrt(2.0)))


def ols_hac(y: np.ndarray, X: np.ndarray, lags: int = 0) -> dict:
    n, k = X.shape
    beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    xtx = X.T @ X
    try:
        xtx_inv = np.linalg.inv(xtx)
    except np.linalg.LinAlgError:
        xtx_inv = np.linalg.pinv(xtx)
    xe = X * resid[:, None]
    meat = xe.T @ xe
    if lags > 0:
        for lag in range(1, lags + 1):
            w = 1.0 - lag / (lags + 1.0)
            g = xe[lag:].T @ xe[:-lag]
            meat = meat + w * (g + g.T)
    cov = xtx_inv @ meat @ xtx_inv
    se = np.sqrt(np.maximum(np.diag(cov), 0.0))
    tstat = np.divide(beta, se, out=np.zeros_like(beta), where=se > 0)
    sse = float(resid @ resid)
    r2 = 1.0 - sse / max(1e-18, float(((y - y.mean()) ** 2).sum()))
    return {
        "beta": [round(float(b), 6) for b in beta],
        "se": [round(float(s), 6) for s in se],
        "t": [round(float(t), 3) for t in tstat],
        "p": [round(_p_from_t(t), 4) for t in tstat],
        "r2": round(float(r2), 4),
        "n": int(n),
        "lags": int(lags),
    }


def pack_reg(model: dict, names: List[str], ann: bool, freq: int) -> dict:
    out = {"r2": model["r2"], "n": model["n"], "lags": model["lags"], "coef": {}}
    for i, name in enumerate(names):
        b = model["beta"][i]
        row = {"beta": round(b, 4), "t": model["t"][i], "p": model["p"][i], "se": model["se"][i]}
        if ann and i == 0:
            row["ann_pct"] = round(b * freq * 100.0, 2)
        out["coef"][name] = row
    return out


def profit_factor(vals: np.ndarray) -> dict:
    vals = np.asarray(vals, dtype=float)
    vals = vals[np.isfinite(vals)]
    if len(vals) == 0:
        return {"n": 0, "pf": None, "win_rate": None, "avg": None, "expectancy": None}
    wins = vals[vals > 0]
    losses = vals[vals < 0]
    gp = float(wins.sum()) if len(wins) else 0.0
    gl = float(np.abs(losses.sum())) if len(losses) else 0.0
    pf = round(gp / gl, 3) if gl > 0 else None
    return {
        "n": int(len(vals)),
        "n_win": int(len(wins)),
        "n_loss": int(len(losses)),
        "win_rate": round(100.0 * len(wins) / len(vals), 2),
        "pf": pf,
        "avg": round(float(vals.mean()), 4),
        "median": round(float(np.median(vals)), 4),
        "expectancy": round(float(vals.mean()), 4),
        "gross_win": round(gp, 4),
        "gross_loss": round(gl, 4),
        "avg_win": round(float(wins.mean()), 4) if len(wins) else None,
        "avg_loss": round(float(losses.mean()), 4) if len(losses) else None,
        "payoff": round(float(wins.mean() / abs(losses.mean())), 3) if len(wins) and len(losses) else None,
    }


def drawdown_stats(eq: np.ndarray) -> dict:
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    underwater = dd < -1e-12
    runs, cur = [], 0
    for x in underwater:
        if x:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    trough = int(np.argmin(dd))
    rec = next((i for i in range(trough + 1, len(eq)) if dd[i] >= -1e-12), None)
    return {
        "mdd_pct": round(float(dd.min()) * 100.0, 2),
        "avg_dd_pct": round(float(dd[dd < 0].mean()) * 100.0, 2) if np.any(dd < 0) else 0.0,
        "time_underwater_pct": round(100.0 * float(underwater.mean()), 1),
        "max_underwater_days": int(max(runs) if runs else 0),
        "n_drawdowns": int(len(runs)),
        "mdd_recover_days": None if rec is None else int(rec - trough),
    }


def acf(x: np.ndarray, lag: int) -> float:
    if len(x) <= lag + 2:
        return 0.0
    a, b = x[lag:], x[:-lag]
    if a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def capture(y: np.ndarray, x: np.ndarray) -> dict:
    up = x > 0
    dn = x < 0
    up_c = float(y[up].mean() / x[up].mean()) if up.any() and x[up].mean() != 0 else None
    dn_c = float(y[dn].mean() / x[dn].mean()) if dn.any() and x[dn].mean() != 0 else None
    return {
        "up": None if up_c is None else round(up_c, 3),
        "down": None if dn_c is None else round(dn_c, 3),
        "up_days": int(up.sum()),
        "down_days": int(dn.sum()),
    }


def fmt_reg(label: str, model: dict) -> None:
    a = model["coef"]["alpha"]
    extra = " ".join(
        f"{k}={v['beta']:+.3f} t={v['t']}" for k, v in model["coef"].items() if k != "alpha"
    )
    ann = f" αann={a.get('ann_pct', 0):+.2f}%" if "ann_pct" in a else ""
    print(f"  {label:28} {ann} t={a['t']} p={a['p']}  {extra}  R2={model['r2']}")


def analyze_window(
    book,
    sleeve,
    qqq: pd.Series,
    spy: pd.Series,
    smh: pd.Series,
    label: str,
) -> dict:
    idx = pd.to_datetime(book.dates)
    eq = pd.Series(book.equity, index=idx, dtype=float)
    r = eq.pct_change()
    rq = qqq.reindex(idx).pct_change()
    rs = spy.reindex(idx).pct_change()
    rm = smh.reindex(idx).pct_change()
    df = pd.DataFrame({"r": r, "qqq": rq, "spy": rs, "smh": rm}).dropna()
    y = df["r"].to_numpy()
    n = len(y)
    lags_d = max(1, int(4 * (n / 100.0) ** (2.0 / 9.0)))
    eq_np = eq.to_numpy(dtype=float)
    rets = np.diff(eq_np) / eq_np[:-1]
    rets = rets[np.isfinite(rets)]
    vol = float(np.std(rets, ddof=1))
    mean = float(np.mean(rets))
    downside = rets[rets < 0]
    dvol = float(np.std(downside, ddof=1)) if len(downside) > 2 else 0.0
    years = len(eq_np) / 252.0
    cagr = (eq_np[-1] / eq_np[0]) ** (1.0 / years) - 1.0
    mdd = abs(drawdown_stats(eq_np)["mdd_pct"] / 100.0)
    excess = (df["r"] - df["qqq"]).to_numpy()

    monthly = df.resample("ME").apply(lambda s: (1.0 + s).prod() - 1.0)
    monthly = monthly.dropna()
    yearly = df.resample("YE").apply(lambda s: (1.0 + s).prod() - 1.0).dropna()
    lags_m = max(1, int(4 * (len(monthly) / 100.0) ** (2.0 / 9.0)))

    def X(*cols, data=df):
        parts = [np.ones(len(data))]
        for c in cols:
            parts.append(data[c].to_numpy())
        return np.column_stack(parts)

    # up/down beta vs QQQ
    q_up = np.where(df["qqq"] > 0, df["qqq"], 0.0)
    q_dn = np.where(df["qqq"] < 0, df["qqq"], 0.0)
    Xud = np.column_stack([np.ones(n), q_up, q_dn])

    regs = {
        "daily_qqq": pack_reg(ols_hac(y, X("qqq"), lags_d), ["alpha", "qqq"], True, 252),
        "daily_spy": pack_reg(ols_hac(y, X("spy"), lags_d), ["alpha", "spy"], True, 252),
        "daily_qqq_smh": pack_reg(ols_hac(y, X("qqq", "smh"), lags_d), ["alpha", "qqq", "smh"], True, 252),
        "daily_updown": pack_reg(ols_hac(y, Xud, lags_d), ["alpha", "beta_up", "beta_dn"], True, 252),
        "daily_excess": pack_reg(
            ols_hac(excess, np.ones((n, 1)), lags_d), ["alpha"], True, 252
        ),
        "monthly_qqq": pack_reg(
            ols_hac(monthly["r"].to_numpy(), X("qqq", data=monthly), lags_m),
            ["alpha", "qqq"],
            True,
            12,
        ),
        "monthly_spy": pack_reg(
            ols_hac(monthly["r"].to_numpy(), X("spy", data=monthly), lags_m),
            ["alpha", "spy"],
            True,
            12,
        ),
    }

    trades = book.trades
    pnls = np.array([t["pnl_pct"] for t in trades], dtype=float) if trades else np.array([])
    by_reason: Dict[str, list] = {}
    by_ticker: Dict[str, list] = {}
    for t in trades:
        by_reason.setdefault(t["reason"], []).append(t["pnl_pct"])
        by_ticker.setdefault(t["ticker"], []).append(t["pnl_pct"])
    etf_set = set(SECTOR_ETF_MAP.values()) | {"QQQ"}
    etf_pnls = [t["pnl_pct"] for t in trades if t["ticker"] in etf_set]
    stk_pnls = [t["pnl_pct"] for t in trades if t["ticker"] not in etf_set]

    year_rows = []
    for dt, row in yearly.iterrows():
        year_rows.append(
            {
                "year": int(dt.year),
                "strat_pct": round(float(row["r"]) * 100.0, 2),
                "qqq_pct": round(float(row["qqq"]) * 100.0, 2),
                "excess_pct": round(float(row["r"] - row["qqq"]) * 100.0, 2),
            }
        )

    dd = drawdown_stats(eq_np)
    ts = {
        "n_days": int(len(eq_np)),
        "cagr_pct": round(cagr * 100.0, 2),
        "vol_ann_pct": round(vol * np.sqrt(252) * 100.0, 2),
        "sharpe": round((mean / vol) * np.sqrt(252), 3) if vol > 0 else 0.0,
        "sortino": round((mean / dvol) * np.sqrt(252), 3) if dvol > 0 else 0.0,
        "calmar": round(cagr / mdd, 3) if mdd > 0 else 0.0,
        "skew": round(float(pd.Series(rets).skew()), 3),
        "excess_kurtosis": round(float(pd.Series(rets).kurt()), 3),
        "acf1": round(acf(rets, 1), 3),
        "acf5": round(acf(rets, 5), 3),
        "acf20": round(acf(rets, 20), 3),
        "corr_qqq": round(float(df["r"].corr(df["qqq"])), 3),
        "corr_spy": round(float(df["r"].corr(df["spy"])), 3),
        "corr_smh": round(float(df["r"].corr(df["smh"])), 3),
        "capture_qqq": capture(df["r"].to_numpy(), df["qqq"].to_numpy()),
        "info_ratio_qqq": round(float(excess.mean() / excess.std() * np.sqrt(252)), 3)
        if excess.std() > 0
        else 0.0,
        "hit_rate_month_pct": round(100.0 * float((monthly["r"] > 0).mean()), 1),
        "hit_rate_month_vs_qqq_pct": round(100.0 * float((monthly["r"] > monthly["qqq"]).mean()), 1),
        "best_day_pct": round(float(rets.max()) * 100.0, 2),
        "worst_day_pct": round(float(rets.min()) * 100.0, 2),
        "drawdown": dd,
        "yearly": year_rows,
    }

    pf = {
        "daily_return": profit_factor(y * 100.0),
        "monthly_return": profit_factor(monthly["r"].to_numpy() * 100.0),
        "trades_all": profit_factor(pnls),
        "trades_etf": profit_factor(np.array(etf_pnls, dtype=float)),
        "trades_stock": profit_factor(np.array(stk_pnls, dtype=float)),
        "by_reason": {k: profit_factor(np.array(v, dtype=float)) for k, v in sorted(by_reason.items())},
        "by_ticker_top": {},
    }
    ticker_pf = {k: profit_factor(np.array(v, dtype=float)) for k, v in by_ticker.items()}
    top = sorted(ticker_pf.items(), key=lambda kv: kv[1]["n"], reverse=True)[:8]
    pf["by_ticker_top"] = {k: v for k, v in top}

    print(f"\n===== {label}  {eq.index[0].date()} → {eq.index[-1].date()} =====")
    print(
        f"  CAGR {ts['cagr_pct']}%  vol {ts['vol_ann_pct']}%  Sharpe {ts['sharpe']}  "
        f"Sortino {ts['sortino']}  Calmar {ts['calmar']}  IR vs QQQ {ts['info_ratio_qqq']}"
    )
    print(
        f"  skew {ts['skew']}  xkurt {ts['excess_kurtosis']}  ACF1/5/20 "
        f"{ts['acf1']}/{ts['acf5']}/{ts['acf20']}  corr QQQ/SPY/SMH "
        f"{ts['corr_qqq']}/{ts['corr_spy']}/{ts['corr_smh']}"
    )
    print(
        f"  capture up/down {ts['capture_qqq']['up']}/{ts['capture_qqq']['down']}  "
        f"MDD {dd['mdd_pct']}%  underwater {dd['time_underwater_pct']}%  "
        f"max UW {dd['max_underwater_days']}d  recover {dd['mdd_recover_days']}"
    )
    print(
        f"  month hit {ts['hit_rate_month_pct']}%  beat QQQ months {ts['hit_rate_month_vs_qqq_pct']}%  "
        f"best/worst day {ts['best_day_pct']}/{ts['worst_day_pct']}"
    )
    print("  yearly strat / QQQ / excess:")
    for row in year_rows:
        print(f"    {row['year']}  {row['strat_pct']:+7.2f}  {row['qqq_pct']:+7.2f}  {row['excess_pct']:+7.2f}")
    print("  profit factor")
    for k in ("daily_return", "monthly_return", "trades_all", "trades_etf", "trades_stock"):
        v = pf[k]
        print(f"    {k:16} n={v['n']:4}  PF={v['pf']}  win={v['win_rate']}%  payoff={v['payoff']}  avg={v['avg']}")
    for k, v in pf["by_reason"].items():
        print(f"    reason {k:16} n={v['n']:3}  PF={v['pf']}  win={v['win_rate']}%  avg={v['avg']}")
    print("  OLS HAC")
    for k, v in regs.items():
        fmt_reg(k, v)

    return {
        "ts": ts,
        "pf": pf,
        "reg": regs,
        "regime_share": {k: v for k, v in sleeve.regime_days.items()},
        "leader_switches": sleeve.leader_switches,
        "transitions": sleeve.transitions,
    }


def main() -> None:
    t0 = time.time()
    raw = download_master()
    names = [t for t in stock_names() if t in raw]
    extra = set(BENCH) | set(SECTOR_ETF_MAP.values()) | {"QQQ", "SMH"}
    sliced = {k: v for k, v in raw.items() if k in set(names) | extra}
    calendar, panels, bench = align_panels(sliced)
    add_dvol(panels, raw, calendar)
    etf_slope = {etf: rolling_rs(aligned_close(raw, etf, calendar), SLOPE_BARS) for etf in SECTOR_ETF_MAP.values()}
    etf_tickers = list(dict.fromkeys(["QQQ"] + list(SECTOR_ETF_MAP.values())))
    etf_open = {t: aligned_open(raw, t, calendar) for t in etf_tickers}
    etf_close = {t: aligned_close(raw, t, calendar) for t in etf_tickers}
    spy_s = bench["spy"].copy()
    spy_s.index = pd.to_datetime(spy_s.index)
    qqq_s = bench["qqq"].copy()
    qqq_s.index = pd.to_datetime(qqq_s.index)
    smh_s = pd.Series(aligned_close(raw, "SMH", calendar), index=calendar)

    payload = {"meta": {"elapsed_sec": 0, "hac": "Newey-West", "strategy": "leader80 monthly + knobs 1+2"}, "windows": {}}
    for asof in SNAPSHOTS:
        start_i = snapshot_index(calendar, asof)
        book, sl, _ = run_book(calendar, panels, bench, etf_open, etf_close, etf_slope, start_i)
        payload["windows"][asof] = analyze_window(book, sl, qqq_s, spy_s, smh_s, asof)

    payload["meta"]["elapsed_sec"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {OUT} in {payload['meta']['elapsed_sec']}s")


if __name__ == "__main__":
    main()
