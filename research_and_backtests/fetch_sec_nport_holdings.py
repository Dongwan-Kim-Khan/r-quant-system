"""
AL-SANGMOO QUANT TERMINAL: SEC EDGAR Form N-PORT PIT Holdings Pipeline
Fetches historical Point-in-Time portfolio holdings for 11 major Sector ETFs
from SEC EDGAR Form NPORT-P filings (2019-09 to 2026-08).

Saved to: d:/코딩/R/data/universe/sec_nport_pit_holdings.pkl
"""
from __future__ import annotations

import os
import sys
import time
import re
from datetime import datetime
from typing import Dict, List, Any, Optional
import pandas as pd

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_ROOT = "d:\\코딩\\R"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.domain.quant.dynamic_universe import SECTOR_ETF_MAP

DATA_DIR = os.path.join(PROJECT_ROOT, "data", "universe")
CACHE_FILE = os.path.join(DATA_DIR, "sec_nport_pit_holdings.pkl")
SEC_IDENTITY = "AlSangmooQuant quant@alsangmoo.internal"

def fetch_etf_nport_series(ticker: str, max_filings: int = 35) -> List[Dict[str, Any]]:
    from edgar import set_identity, Fund
    set_identity(SEC_IDENTITY)
    
    print(f"\n[SEC EDGAR] Fetching NPORT-P filings for ETF: {ticker} ...")
    try:
        fund = Fund(ticker)
        filings = fund.get_filings(
            form=["NPORT-P", "NPORT-P/A"],
            series_only=bool(getattr(fund, "_target_series_id", None))
        )
    except Exception as exc:
        print(f"[ERROR] Failed to query Fund({ticker}): {exc}")
        return []

    print(f"[{ticker}] Found {len(filings)} total NPORT-P filings.")
    records: List[Dict[str, Any]] = []
    seen_periods = set()

    for idx, filing in enumerate(filings[:max_filings]):
        try:
            report_period = getattr(filing, "period_of_report", None)
            filing_date = str(filing.filing_date)
            
            # Avoid duplicate periods (prefer newest filing for the same period)
            period_str = str(report_period) if report_period else filing_date
            if period_str in seen_periods:
                continue
                
            rep = filing.obj()
            if rep is None or not hasattr(rep, "investment_data"):
                continue
                
            inv = rep.investment_data()
            if inv is None or inv.empty:
                continue
                
            if "ticker" not in inv.columns:
                continue

            # Filter valid ticker symbols
            clean_df = inv.dropna(subset=["ticker"]).copy()
            clean_df["ticker"] = clean_df["ticker"].astype(str).str.strip().str.upper()
            clean_df = clean_df[clean_df["ticker"].str.match(r"^[A-Z]{1,5}$")]

            cols_to_keep = [c for c in ["ticker", "name", "cusip", "balance", "value_usd", "pct_value"] if c in clean_df.columns]
            clean_df = clean_df[cols_to_keep].copy()
            clean_df["pct_value"] = pd.to_numeric(clean_df.get("pct_value", 0.0), errors="coerce").fillna(0.0)
            clean_df["value_usd"] = pd.to_numeric(clean_df.get("value_usd", 0.0), errors="coerce").fillna(0.0)
            
            # Normalize total pct if expressed as 0-100 or 0-1
            tot_pct = clean_df["pct_value"].sum()
            if tot_pct > 1.5:  # e.g. 95.0%
                clean_df["weight"] = clean_df["pct_value"] / 100.0
            elif tot_pct > 0.1:
                clean_df["weight"] = clean_df["pct_value"]
            else:
                tot_val = clean_df["value_usd"].sum()
                clean_df["weight"] = clean_df["value_usd"] / tot_val if tot_val > 0 else 0.0

            clean_df.sort_values(by="weight", ascending=False, inplace=True)
            clean_df.reset_index(drop=True, inplace=True)

            entry = {
                "etf": ticker,
                "report_period": period_str,
                "filing_date": filing_date,
                "num_holdings": len(clean_df),
                "holdings": clean_df,
            }
            records.append(entry)
            seen_periods.add(period_str)
            print(f"  [{ticker}] Period {period_str} (filed {filing_date}): {len(clean_df)} equities (Top 3: {', '.join(clean_df['ticker'].iloc[:3])})")
            
            time.sleep(0.1)
        except Exception as exc:
            print(f"  [{ticker}] Warning: Failed to parse filing {idx} ({filing.filing_date}): {exc}")
            continue

    return records


def download_all_sectors(force_refresh: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not force_refresh and os.path.exists(CACHE_FILE):
        age_h = (time.time() - os.path.getmtime(CACHE_FILE)) / 3600.0
        if age_h < 168:
            print(f"[CACHE] Loaded {CACHE_FILE} (age: {age_h:.1f}h)")
            return pd.read_pickle(CACHE_FILE)

    all_data: Dict[str, List[Dict[str, Any]]] = {}
    total_start = time.time()

    for sector, etf in SECTOR_ETF_MAP.items():
        recs = fetch_etf_nport_series(etf)
        all_data[etf] = recs
        print(f"[{sector} / {etf}] Successfully fetched {len(recs)} historical snapshots.")

    pd.to_pickle(all_data, CACHE_FILE)
    print(f"\n[DONE] Saved complete PIT holdings to {CACHE_FILE} in {time.time() - total_start:.1f}s")
    return all_data


if __name__ == "__main__":
    download_all_sectors()
