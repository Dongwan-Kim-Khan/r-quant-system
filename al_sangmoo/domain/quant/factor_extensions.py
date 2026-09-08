"""Sunday Quant Scientist inspired residual-momentum / vol-adjusted breakout helpers."""
from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_residual_momentum_score(
    asset_prices: pd.Series,
    benchmark_prices: pd.Series,
    window: int = 60,
) -> float:
    if asset_prices is None or benchmark_prices is None:
        return 0.0
    if len(asset_prices) < window or len(benchmark_prices) < window:
        return 0.0

    asset_ret = asset_prices.pct_change().tail(window).dropna()
    bench_ret = benchmark_prices.pct_change().tail(window).dropna()
    aligned = pd.concat([asset_ret, bench_ret], axis=1, join="inner").dropna()
    if aligned.shape[0] < 10:
        return 0.0
    y = aligned.iloc[:, 0].to_numpy(dtype=float)
    x = aligned.iloc[:, 1].to_numpy(dtype=float)
    A = np.column_stack([np.ones(len(x)), x])
    try:
        beta, _, _, _ = np.linalg.lstsq(A, y, rcond=None)
        resid = y - A @ beta
    except Exception:
        return 0.0
    denom = float(np.std(resid) + 1e-8)
    res_score = float(np.mean(resid) / denom)
    normalized = float(np.tanh(res_score * 15.0) * 100.0)
    return round(normalized, 2)


def vol_adjusted_breakout_z(close: pd.Series, lookback: int = 20) -> float:
    if close is None or len(close) < lookback + 1:
        return 0.0
    rets = close.pct_change().dropna()
    if len(rets) < lookback:
        return 0.0
    last = float(rets.iloc[-1])
    mu = float(rets.iloc[-lookback:].mean())
    sd = float(rets.iloc[-lookback:].std(ddof=0) + 1e-8)
    return round((last - mu) / sd, 4)
