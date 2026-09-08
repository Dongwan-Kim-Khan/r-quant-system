"""
Riskfolio-Lib inspired HRP sizer.

Uses riskfolio when installed; otherwise inverse-volatility weights with a 40% cap.
Gated by USE_RISKFOLIO_HRP.
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd


class RiskfolioPositionSizer:
    def __init__(self, total_capital: float, max_weight_per_asset: float = 0.40):
        self.total_capital = float(total_capital)
        self.max_weight_per_asset = float(max_weight_per_asset)

    def calculate_hrp_weights(self, price_history_df: pd.DataFrame) -> Dict[str, float]:
        if price_history_df is None or price_history_df.empty:
            return {}
        cols = [str(c) for c in price_history_df.columns]
        n = len(cols)
        if n == 0:
            return {}
        if n == 1:
            return {cols[0]: round(self.total_capital, 2)}

        returns = price_history_df.pct_change().replace([np.inf, -np.inf], np.nan).dropna(how="all")
        if returns.empty or returns.shape[1] < 2:
            even = self.total_capital / n
            return {t: round(even, 2) for t in cols}

        weights = self._optimize_weights(returns[cols] if set(cols) <= set(returns.columns) else returns)
        allocated: Dict[str, float] = {}
        for ticker in cols:
            w = float(weights.get(ticker, 0.0))
            capped = min(max(w, 0.0), self.max_weight_per_asset)
            allocated[ticker] = round(self.total_capital * capped, 2)
        return allocated

    def _optimize_weights(self, returns: pd.DataFrame) -> Dict[str, float]:
        try:
            import riskfolio as rp  # type: ignore

            port = rp.HCPortfolio(returns=returns)
            raw = port.optimization(
                model="HRP",
                codependence="pearson",
                rm="MV",
                rf=0.035 / 252,
                linkage="ward",
                max_k=10,
                leaf_order=True,
            )
            series = raw["weights"] if "weights" in raw.columns else raw.iloc[:, 0]
            return {str(k): float(v) for k, v in series.to_dict().items()}
        except Exception:
            return self._inverse_vol_weights(returns)

    def _inverse_vol_weights(self, returns: pd.DataFrame) -> Dict[str, float]:
        vol = returns.std(ddof=0).replace(0.0, np.nan)
        inv = 1.0 / vol
        inv = inv.fillna(inv.mean() if inv.notna().any() else 1.0)
        total = float(inv.sum()) or 1.0
        return {str(k): float(v) / total for k, v in inv.items()}
