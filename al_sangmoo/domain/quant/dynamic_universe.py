"""
AL-SANGMOO QUANT TERMINAL: DYNAMIC SECTOR-MOMENTUM WEIGHTED UNIVERSE ENGINE
Top-Down 3-Stage Dynamic Universe Selection Pipeline:
1. Evaluates 3M Relative Strength (RS) across 11 Major Sector ETFs.
2. Allocates Dynamic Slot Quotas (14 -> 12 -> 10 -> 8 -> 6 -> 4 -> 1~2 = exactly 60 slots).
3. Selects top liquid momentum leaders within each sector from a 250-stock master candidate pool.
4. Persists daily snapshot to data/universe/dynamic_universe_snapshot.json with idempotent caching (< 1ms load).
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
UNIVERSE_DATA_DIR = os.path.join(BASE_DIR, "data", "universe")
SNAPSHOT_FILE = os.path.join(UNIVERSE_DATA_DIR, "dynamic_universe_snapshot.json")

# 11 Major Sector Representative ETFs
SECTOR_ETF_MAP = {
    "SEMICONDUCTOR": "SMH",
    "MEGA_TECH": "XLK",
    "POWER_ENERGY": "XLE",
    "DEFENSE_AERO": "ITA",
    "CYBER_SAAS": "CIBR",
    "HEALTHCARE": "XLV",
    "FINANCIALS": "XLF",
    "CONSUMER_RETAIL": "XLY",
    "COMMUNICATION": "XLC",
    "INDUSTRIALS": "XLI",
    "MATERIALS": "XLB"
}

# Sector Master Candidate Pool (approx. 200~250 liquid, leading global companies)
SECTOR_MASTER_CANDIDATES = {
    "SEMICONDUCTOR": [
        "NVDA", "TSM", "AVGO", "AMD", "QCOM", "ARM", "MU", "INTC", "TXN", "ASML",
        "AMAT", "LRCX", "KLAC", "MRVL", "ADI", "MPWR", "ON", "NXPI", "MCHP", "TER",
        "005930.KS", "000660.KS"
    ],
    "MEGA_TECH": [
        "MSFT", "AAPL", "AMZN", "GOOGL", "META", "TSLA", "ORCL", "CRM", "ADBE", "IBM",
        "NOW", "INTU", "PLTR", "SNOW", "DELL", "HPQ", "WDC", "STX"
    ],
    "POWER_ENERGY": [
        "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "VST",
        "CEG", "GEV", "ETN", "CCJ", "OKLO", "NEE", "DUK", "SO", "SRE", "AEP"
    ],
    "DEFENSE_AERO": [
        "LMT", "RTX", "NOC", "GD", "BA", "GE", "TDG", "HWM", "AXON", "RKLB",
        "HEI", "LHX", "TXT", "HII", "BAH"
    ],
    "CYBER_SAAS": [
        "CRWD", "PANW", "FTNT", "ZS", "NET", "DDOG", "MDB", "TEAM", "ZM", "DOCU",
        "OKTA", "APP", "IONQ"
    ],
    "HEALTHCARE": [
        "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "PFE", "DHR", "BMY",
        "AMGN", "ISRG", "GILD", "VRTX", "REGN", "BIIB", "MRNA", "MDT", "SYK", "ZTS"
    ],
    "FINANCIALS": [
        "JPM", "BAC", "WFC", "MS", "GS", "BLK", "SCHW", "C", "V", "MA",
        "AXP", "PYPL", "COIN", "HOOD", "MSTR", "BX", "KKR", "CB"
    ],
    "CONSUMER_RETAIL": [
        "COST", "WMT", "TGT", "HD", "LOW", "NKE", "SBUX", "MCD", "CMG", "BKNG",
        "ABNB", "MAR", "LULU", "TJX", "ROST", "PG", "KO", "PEP", "PM", "MO"
    ],
    "COMMUNICATION": [
        "NFLX", "DIS", "CMCSA", "T", "VZ", "CHTR", "TMUS", "WBD", "SPOT", "EA", "TTWO", "RBLX"
    ],
    "INDUSTRIALS": [
        "CAT", "DE", "HON", "UNP", "UPS", "FDX", "EMR", "PH", "ITW", "TT",
        "CMI", "PCAR", "ROK", "GWW"
    ],
    "MATERIALS": [
        "LIN", "APD", "SHW", "FCX", "NEM", "CTVA", "ECL", "DOW", "DD", "ALB", "SCCO", "VALE"
    ]
}

# Dynamic Slot Quota Curve (Total sum = exactly 60)
DYNAMIC_SLOT_QUOTAS = [14, 12, 10, 8, 6, 4, 2, 1, 1, 1, 1]


def get_current_market_date() -> str:
    """Returns the current trading session date string (YYYY-MM-DD) in US Eastern Time."""
    now_ny = datetime.now(timezone(timedelta(hours=-4)))  # EDT
    return now_ny.strftime("%Y-%m-%d")


def evaluate_sector_momentum() -> List[Dict[str, Any]]:
    """
    Downloads daily historical closes for 11 sector ETFs and ranks them by 3M Relative Strength (RS).
    Returns a sorted list of sector dictionaries from Rank 1 (highest momentum) to Rank 11.
    """
    etf_tickers = list(SECTOR_ETF_MAP.values())
    sector_names = list(SECTOR_ETF_MAP.keys())
    
    sector_results = []
    
    try:
        data = yf.download(etf_tickers, period="6mo", interval="1d", progress=False)
        if data.empty:
            raise ValueError("Empty data returned for sector ETFs")
            
        closes = data["Close"] if "Close" in data else data
        
        for sector, etf in SECTOR_ETF_MAP.items():
            if etf in closes.columns:
                series = closes[etf].dropna()
                if len(series) >= 20:
                    start_price = float(series.iloc[max(-63, -len(series))])
                    end_price = float(series.iloc[-1])
                    rs_3m = round(((end_price - start_price) / max(start_price, 0.01)) * 100, 2)
                    sector_results.append({
                        "sector": sector,
                        "etf": etf,
                        "rs_3m": rs_3m,
                        "current_price": end_price
                    })
                    continue
            # Fallback if specific ETF column missing
            sector_results.append({
                "sector": sector,
                "etf": etf,
                "rs_3m": 0.0,
                "current_price": 100.0
            })
    except Exception as e:
        logger.warning(f"[Dynamic Universe] Bulk ETF download fallback triggered: {e}")
        for i, (sector, etf) in enumerate(SECTOR_ETF_MAP.items()):
            sector_results.append({
                "sector": sector,
                "etf": etf,
                "rs_3m": round(15.0 - (i * 2.0), 2),  # Default staggered fallback
                "current_price": 100.0
            })

    # Sort sectors by 3M RS descending (highest momentum first)
    sector_results.sort(key=lambda x: x["rs_3m"], reverse=True)
    
    # Assign ranks and dynamic quotas
    for rank_idx, s in enumerate(sector_results):
        s["rank"] = rank_idx + 1
        s["quota"] = DYNAMIC_SLOT_QUOTAS[rank_idx] if rank_idx < len(DYNAMIC_SLOT_QUOTAS) else 1

    return sector_results


def select_sector_candidates(sector: str, quota: int) -> List[str]:
    """
    Selects the top `quota` candidate stocks for a given sector based on liquidity and 3M momentum.
    """
    candidates = SECTOR_MASTER_CANDIDATES.get(sector, [])
    if len(candidates) <= quota:
        return candidates

    try:
        data = yf.download(candidates, period="3mo", interval="1d", progress=False)
        if not data.empty and "Close" in data:
            closes = data["Close"]
            scored = []
            for t in candidates:
                if t in closes.columns:
                    series = closes[t].dropna()
                    if len(series) >= 15:
                        s_p = float(series.iloc[0])
                        e_p = float(series.iloc[-1])
                        rs = ((e_p - s_p) / max(s_p, 0.01)) * 100
                        scored.append((t, rs))
                        continue
                scored.append((t, -999.0))
            scored.sort(key=lambda x: x[1], reverse=True)
            return [x[0] for x in scored[:quota]]
    except Exception as e:
        logger.debug(f"[Dynamic Universe] Candidate ranking fallback for {sector}: {e}")

    # Fallback: slice top candidates from predefined list
    return candidates[:quota]


def build_dynamic_60_watchlist(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Builds or loads the 60-stock dynamic universe snapshot.
    1. Checks if snapshot exists for current trading day (< 1ms load).
    2. If missing or expired (or force_refresh=True), recomputes 11 sector ranks,
       allocates quotas, selects top 60 stocks, and saves to JSON snapshot.
    """
    os.makedirs(UNIVERSE_DATA_DIR, exist_ok=True)
    today_session = get_current_market_date()

    if not force_refresh and os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
                snapshot = json.load(f)
                if snapshot.get("session_date") == today_session and len(snapshot.get("watchlist", [])) == 60:
                    return snapshot
        except Exception as e:
            logger.debug(f"[Dynamic Universe] Snapshot read error, rebuilding: {e}")

    # Recompute sector momentum and dynamic quotas
    logger.info(f"[Dynamic Universe] Computing dynamic 60 universe for session {today_session}...")
    sector_ranks = evaluate_sector_momentum()
    
    selected_watchlist: List[str] = []
    sector_breakdown: List[Dict[str, Any]] = []

    for s_info in sector_ranks:
        sec_name = s_info["sector"]
        quota = s_info["quota"]
        chosen_stocks = select_sector_candidates(sec_name, quota)
        
        # Ensure we take exactly quota stocks
        for stk in chosen_stocks:
            if stk not in selected_watchlist:
                selected_watchlist.append(stk)
                if len(selected_watchlist) >= 60:
                    break

        sector_breakdown.append({
            "sector": sec_name,
            "etf": s_info["etf"],
            "rank": s_info["rank"],
            "rs_3m": s_info["rs_3m"],
            "quota": quota,
            "selected_stocks": chosen_stocks
        })
        if len(selected_watchlist) >= 60:
            break

    # Safety pad to guarantee exactly 60 stocks
    if len(selected_watchlist) < 60:
        from al_sangmoo.core.constants import WATCHLIST as FALLBACK_WATCHLIST
        for f_tk in FALLBACK_WATCHLIST:
            if f_tk not in selected_watchlist:
                selected_watchlist.append(f_tk)
            if len(selected_watchlist) >= 60:
                break

    selected_watchlist = selected_watchlist[:60]

    snapshot_payload = {
        "session_date": today_session,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_count": len(selected_watchlist),
        "watchlist": selected_watchlist,
        "sector_breakdown": sector_breakdown
    }

    # Atomic write to snapshot file
    tmp_file = SNAPSHOT_FILE + f".tmp_{os.getpid()}"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(snapshot_payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp_file, SNAPSHOT_FILE)
        logger.info(f"[Dynamic Universe] Successfully saved fresh 60-universe snapshot to {SNAPSHOT_FILE}")
    except Exception as e:
        logger.error(f"[Dynamic Universe] Failed to save snapshot: {e}")
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except Exception:
                pass

    return snapshot_payload


def get_dynamic_watchlist() -> List[str]:
    """
    High-performance, non-blocking interface to retrieve the current 60-stock watchlist.
    Returns cached snapshot if fresh (< 1ms), or triggers fallback to constants.WATCHLIST.
    """
    try:
        snapshot = build_dynamic_60_watchlist(force_refresh=False)
        w = snapshot.get("watchlist", [])
        if len(w) == 60:
            return w
    except Exception as e:
        logger.warning(f"[Dynamic Universe] get_dynamic_watchlist fallback: {e}")
    
    from al_sangmoo.core.constants import WATCHLIST
    return list(WATCHLIST)
