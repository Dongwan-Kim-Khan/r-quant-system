"""
AL-SANGMOO QUANT TERMINAL: 7-YEAR SURVIVORSHIP-BIAS-FREE BACKTESTER (2019-2026)
Evaluates C1-M2 on authentic SEC Form N-PORT Point-in-Time Universe across 11 Sectors.

Compares:
1. Benchmark: QQQ Buy & Hold
2. Static Biased Baseline: M2 on 2026 static candidate pool (survivorship bias present)
3. PIT Mode A: M2 (Intraday Low -5% Stop) on authentic SEC N-PORT PIT universe
4. PIT Mode B: User Idea (EOD Close -5% Stop + -10% Emergency) on SEC N-PORT PIT universe
5. PIT Mode C: User+Proposals (EOD Close -7.5% Stop + QQQ 20MA Gate) on SEC N-PORT PIT universe
"""
from __future__ import annotations

import os
import sys
import time
import copy
from typing import Dict, List, Optional, Set, Tuple, Any
import numpy as np
import pandas as pd

PROJECT_ROOT = "d:\\코딩\\R"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "research_and_backtests"))

SCRATCH_DIR = "C:\\Users\\kdw58\\.gemini\\antigravity\\brain\\8b3fcff7-bc56-4aa4-93f2-efc7577272b7\\scratch"
if SCRATCH_DIR not in sys.path:
    sys.path.insert(0, SCRATCH_DIR)

from c1_v1_tuning import (
    TuneSpec, TuneSleeve, Book, Position,
    download_master, ensure_qld, stock_names, align_panels, aligned_ohlc,
    composite_rs, snapshot_index, pack_tune,
    FEE, SLIPPAGE, INITIAL, MAX_SLOTS, CONVICTION, DRIFT_BAND, BENCH
)
from backtest_dynamic_universe_eod_vs_intraday import (
    CustomTuneSpec, run_custom_book, decide_custom_exit
)
from pit_sec_nport_universe import SECNportUniverseProvider, build_pit_daily_universes

WINDOW_START = "2019-09-30"  # Start of SEC N-PORT mandatory reporting regime


def run_pit_backtest():
    print("="*95)
    print("PHASE 1: SEC EDGAR FORM N-PORT 7-YEAR SURVIVORSHIP-BIAS-FREE BACKTEST (2019-2026)")
    print("="*95)

    # 1. Load SEC N-PORT Universe Provider
    print("\n[1] Initializing SEC N-PORT Universe Provider...")
    provider = SECNportUniverseProvider()
    unique_tickers = provider.get_all_unique_tickers()
    print(f"  -> Total distinct historical equities in 11 ETFs (2019-2026): {len(unique_tickers)}")

    # 2. Load OHLCV data
    print("\n[2] Aligning OHLCV data...")
    raw = download_master()
    raw = ensure_qld(raw)
    
    names = [t for t in stock_names() if t in raw]
    sliced = {k: v for k, v in raw.items() if k in set(names) | set(BENCH)}
    calendar, panels, bench = align_panels(sliced)
    print(f"  -> Master calendar: {len(calendar)} trading days, {len(panels)} stocks active in cache.")

    proxy_ohlc = {"QQQ": aligned_ohlc(raw, "QQQ", calendar)}
    if "QLD" in raw:
        proxy_ohlc["QLD"] = aligned_ohlc(raw, "QLD", calendar)

    # 3. Build Point-in-Time Universes
    print("\n[3] Building Point-in-Time Daily Universes from SEC N-PORT...")
    t0 = time.time()
    pit_universes = build_pit_daily_universes(calendar, raw, provider)
    print(f"  -> PIT Universes generated in {time.time() - t0:.1f}s.")

    # Also build static biased universe for comparison
    from research_and_backtests.qqq_beat_alpha_backtester import build_daily_universes_composite
    static_universes = build_daily_universes_composite(calendar, raw)

    composite = {t: composite_rs(p["close"]) for t, p in panels.items()}

    # Slice Window 2019-09-30 to 2026-08-28 (7.0 Years)
    start_i = max(snapshot_index(calendar, WINDOW_START), 2)
    start_dt = calendar[start_i].date()
    end_dt = calendar[-1].date()
    n_days = len(calendar) - start_i
    yrs = n_days / 252.0

    # Benchmark: QQQ Buy & Hold
    q_close = bench["qqq"].iloc[start_i:].to_numpy()
    q_ret = (q_close[-1] / q_close[0] - 1.0) * 100.0
    q_cagr = ((1.0 + q_ret / 100.0) ** (1.0 / yrs) - 1.0) * 100.0
    q_pk = np.maximum.accumulate(q_close)
    q_mdd = np.min((q_close - q_pk) / q_pk) * 100.0

    print("\n" + "="*95)
    print(f"WINDOW: {start_dt} ~ {end_dt} ({yrs:.1f} Years) — SURVIVORSHIP-BIAS AUDIT MATRIX")
    print("="*95)
    print(f"Benchmark [QQQ Buy & Hold]: Total Return: {q_ret:+.1f}% | CAGR: {q_cagr:.2f}% | MDD: {q_mdd:.2f}%\n" + "-"*95)

    specs = {
        "1. Static Biased Baseline (Current M2 on 2026 static pool)": (
            CustomTuneSpec("Static Biased", bear_slots=2, bear_weights=(0.40, 0.40), stop_pct=-0.05,
                           trail_trigger=0.15, atr_mult=2.5, exit_mode="INTRADAY_LOW"),
            static_universes
        ),
        "2. Authentic PIT Mode A (Current M2: Intraday Low -5% Stop)": (
            CustomTuneSpec("PIT Mode A", bear_slots=2, bear_weights=(0.40, 0.40), stop_pct=-0.05,
                           trail_trigger=0.15, atr_mult=2.5, exit_mode="INTRADAY_LOW"),
            pit_universes
        ),
        "3. Authentic PIT Mode B (User Idea: EOD Close -5% + -10% Emerg)": (
            CustomTuneSpec("PIT Mode B", bear_slots=2, bear_weights=(0.40, 0.40), stop_pct=-0.05,
                           trail_trigger=0.15, atr_mult=2.5, exit_mode="EOD_CLOSE", emergency_stop_pct=-0.10),
            pit_universes
        ),
        "4. Authentic PIT Mode C (User+Proposals: EOD -7.5% + QQQ 20MA Gate)": (
            CustomTuneSpec("PIT Mode C", bear_slots=2, bear_weights=(0.40, 0.40), stop_pct=-0.075,
                           trail_trigger=0.15, atr_mult=2.5, exit_mode="EOD_CLOSE_WIDER", emergency_stop_pct=-0.10, qqq_20ma_gate=True),
            pit_universes
        ),
    }

    results = {}
    for name, (sp, u_pool) in specs.items():
        bk, sl = run_custom_book(sp, calendar, panels, bench, composite, u_pool, proxy_ohlc, start_i)
        packed = pack_tune(bk, bench, sl)
        cagr = packed["cagr_pct"]
        mdd = packed["mdd_pct"]
        tr = packed["trades"]
        hard = tr["reasons"].get("HARD_STOP", {}).get("n", 0) + tr["reasons"].get("EOD_STOP", {}).get("n", 0) + tr["reasons"].get("EMERGENCY_STOP", {}).get("n", 0)
        trail = tr["reasons"].get("TRAILING", {}).get("n", 0)
        alpha = cagr - q_cagr
        
        results[name] = packed
        print(f"[{name}]")
        print(f"  * CAGR: {cagr:5.2f}% (vs QQQ Alpha: {alpha:+5.2f}%p) | MDD: {mdd:6.2f}% (vs QQQ: {mdd - q_mdd:+5.2f}%p)")
        print(f"  * Total Trades: {tr['n']:3} | Win Rate: {tr['win_rate']:5.2f}% | Profit Factor: {tr['profit_factor']:5.3f} | Avg Hold: {tr['avg_bars']:4.1f}d")
        print(f"  * Stops Hit: {hard:3} times | Trailing Exits: {trail:3} times")
        print("-" * 95)


if __name__ == "__main__":
    run_pit_backtest()
