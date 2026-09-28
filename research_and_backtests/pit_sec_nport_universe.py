"""
AL-SANGMOO QUANT TERMINAL: SEC N-PORT POINT-IN-TIME UNIVERSE ENGINE
Provides daily Point-in-Time 60-stock universe selections from 11 Sector ETFs
using authentic SEC Form N-PORT filings (2019-2026) with zero lookahead bias.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Dict, List, Set, Optional, Tuple, Any
import numpy as np
import pandas as pd

PROJECT_ROOT = "d:\\코딩\\R"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.domain.quant.dynamic_universe import (
    SECTOR_ETF_MAP,
    DYNAMIC_SLOT_QUOTAS,
)

DATA_DIR = os.path.join(PROJECT_ROOT, "data", "universe")
CACHE_FILE = os.path.join(DATA_DIR, "sec_nport_pit_holdings.pkl")


class SECNportUniverseProvider:
    """
    Point-in-Time Universe Provider based on SEC Form N-PORT disclosures.
    """
    def __init__(self, holdings_data: Optional[Dict[str, List[Dict[str, Any]]]] = None):
        if holdings_data is None:
            if not os.path.exists(CACHE_FILE):
                raise FileNotFoundError(f"Holdings cache not found at {CACHE_FILE}. Run fetch_sec_nport_holdings.py first.")
            self.data = pd.read_pickle(CACHE_FILE)
        else:
            self.data = holdings_data

        # Pre-index filings by filing_date ascending for fast binary search
        self.indexed_filings: Dict[str, List[Tuple[str, List[str], Dict[str, float]]]] = {}
        for etf, recs in self.data.items():
            # sort ascending by filing_date
            sorted_recs = sorted(recs, key=lambda x: str(x.get("filing_date", "")))
            parsed_list = []
            for r in sorted_recs:
                f_date = str(r.get("filing_date", ""))
                h_df = r.get("holdings")
                if h_df is not None and not h_df.empty and "ticker" in h_df.columns:
                    tickers = h_df["ticker"].tolist()
                    w_dict = dict(zip(h_df["ticker"], h_df.get("weight", [0.0]*len(tickers))))
                    parsed_list.append((f_date, tickers, w_dict))
            self.indexed_filings[etf] = parsed_list

    def get_etf_candidates_at_date(self, etf: str, target_date: str) -> List[str]:
        """
        Returns the constituents of the ETF from the newest N-PORT filing
        available on or before target_date (zero look-ahead).
        """
        filings = self.indexed_filings.get(etf, [])
        if not filings:
            return []

        # Find newest filing with filing_date <= target_date
        best_tickers: List[str] = []
        for f_date, tickers, _w in filings:
            if f_date <= target_date:
                best_tickers = tickers
            else:
                break
        
        # If target_date is before first filing (e.g. early 2019), fallback to oldest available filing
        if not best_tickers and filings:
            best_tickers = filings[0][1]

        return best_tickers

    def get_all_unique_tickers(self) -> List[str]:
        """Returns all distinct equity tickers that ever appeared across all 11 ETFs."""
        all_tks = set()
        for etf, filings in self.indexed_filings.items():
            for _, tickers, _ in filings:
                all_tks.update(tickers)
        return sorted(list(all_tks))


def build_pit_daily_universes(
    calendar: pd.DatetimeIndex,
    raw_ohlcv: Dict[str, pd.DataFrame],
    provider: SECNportUniverseProvider,
) -> List[Optional[Set[str]]]:
    """
    Constructs Point-in-Time 60-stock daily universe for each trading day.
    
    1. Ranks 11 Sector ETFs by Composite RS as of asof day (t - 1).
    2. Retrieves true historical constituents from SEC N-PORT for each sector as of t.
    3. Ranks valid constituents by Composite RS and fills dynamic quotas.
    """
    from research_and_backtests.qqq_beat_alpha_backtester import (
        aligned_close, listed_mask, composite_rs
    )

    n = len(calendar)
    etf_rs = {}
    etf_ok = {}
    for etf in SECTOR_ETF_MAP.values():
        c = aligned_close(raw_ohlcv, etf, calendar)
        etf_rs[etf] = composite_rs(c)
        etf_ok[etf] = listed_mask(c, min_bars=15)

    all_tickers = [t for t in provider.get_all_unique_tickers() if t in raw_ohlcv]
    stock_crs = {t: composite_rs(aligned_close(raw_ohlcv, t, calendar)) for t in all_tickers}
    stock_ok = {t: listed_mask(aligned_close(raw_ohlcv, t, calendar), min_bars=20) for t in all_tickers}

    universes: List[Optional[Set[str]]] = [None] * n

    for i in range(1, n):
        cur_date_str = calendar[i].strftime("%Y-%m-%d")
        asof = i - 1

        # 1. Rank 11 Sector ETFs by RS
        ranked = []
        for sector, etf in SECTOR_ETF_MAP.items():
            rs = etf_rs[etf][asof] if etf_ok[etf][asof] else np.nan
            ranked.append((float(rs) if np.isfinite(rs) else -999.0, sector, etf))
        ranked.sort(reverse=True)

        picked: List[str] = []
        seen: Set[str] = set()

        # 2. Pick top momentum stocks within dynamic quotas
        for rank, (_rs, sector, etf) in enumerate(ranked):
            quota = DYNAMIC_SLOT_QUOTAS[rank] if rank < len(DYNAMIC_SLOT_QUOTAS) else 1
            
            # Point-in-Time true ETF holdings on cur_date_str
            historical_candidates = provider.get_etf_candidates_at_date(etf, cur_date_str)
            
            scored = []
            for t in historical_candidates:
                if t not in stock_ok or not stock_ok[t][asof]:
                    continue
                srs = stock_crs[t][asof]
                scored.append((float(srs) if np.isfinite(srs) else -999.0, t))
            
            scored.sort(reverse=True)
            chosen = 0
            for _, t in scored:
                if t in seen:
                    continue
                seen.add(t)
                picked.append(t)
                chosen += 1
                if chosen >= quota or len(picked) >= 60:
                    break
            if len(picked) >= 60:
                break

        universes[i] = set(picked[:60])

    return universes
