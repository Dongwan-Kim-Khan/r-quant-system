"""
Research-only: Core 3M + Explore (C-rule) sector ETF backtest.

Does NOT touch live guardian / dashboard / dynamic_universe production paths.
Outputs JSON under data/backtest_cache/.

Plans:
  1) 3M-only vs 21d-only
  2) Core+Explore (ignition/confirm/empty checklist)
  3) Explore size sweep: max8 / max12 / force10

Bar modes:
  - daily: full story windows (2020 V, 2022 energy, 2023-24 mega, 2026 soft->hard)
  - 1h: max yfinance depth (~730d); covers mega tail + 2026 rotation
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
from dataclasses import dataclass
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

from al_sangmoo.domain.quant.dynamic_universe import (  # noqa: E402
    DYNAMIC_SLOT_QUOTAS,
    SECTOR_ETF_MAP,
)

OUT_DIR = os.path.join(PROJECT_ROOT, "data", "backtest_cache")
OUT_PATH = os.path.join(OUT_DIR, "sector_explore_core_ab.json")

SECTOR_TO_ETF = dict(SECTOR_ETF_MAP)
ETF_TO_SECTOR = {v: k for k, v in SECTOR_TO_ETF.items()}
ETFS = list(SECTOR_TO_ETF.values())
BENCH = ["SPY", "^VIX"]

# Quota curve sums to 60
QUOTAS = list(DYNAMIC_SLOT_QUOTAS)

REGIMES = {
    "2020_V": ("2020-03-01", "2020-08-31"),
    "2022_energy": ("2022-01-01", "2022-12-31"),
    "2023_24_mega": ("2023-01-01", "2024-06-30"),
    "2026_soft_hard": ("2026-06-01", "2026-09-18"),
    "full_available": None,  # filled per mode
}

# Lookbacks in bars
LB = {
    "1h": {"rs_3m": 400, "rs_21": 135, "accel_fast": 65, "accel_slow": 130, "bars_per_day": 7},
    "daily": {"rs_3m": 63, "rs_21": 21, "accel_fast": 21, "accel_slow": 42, "bars_per_day": 1},
}

COST_BPS = 2.0  # one-way; applied on turnover
GAP13_LARGE = 8.0
GAP13_MID = 4.0
VIX_PANIC = 25.0


def _flat_close(raw: pd.DataFrame, tickers: List[str]) -> pd.DataFrame:
    if isinstance(raw.columns, pd.MultiIndex):
        if "Close" in raw.columns.get_level_values(0):
            closes = raw["Close"].copy()
        else:
            closes = raw.xs("Close", axis=1, level=0, drop_level=True)
    else:
        closes = raw[["Close"]].copy() if "Close" in raw.columns else raw.copy()
        if len(tickers) == 1 and closes.shape[1] == 1:
            closes.columns = tickers
    closes = closes.reindex(columns=[t for t in tickers if t in closes.columns])
    return closes.dropna(how="all").sort_index()


def download_panel(interval: str) -> Tuple[pd.DataFrame, pd.Series, pd.Series]:
    tickers = ETFS + BENCH
    if interval == "1h":
        raw = __import__("yfinance").download(
            tickers, interval="1h", period="730d", auto_adjust=True, progress=False, threads=True
        )
    else:
        raw = __import__("yfinance").download(
            tickers,
            start="2019-01-01",
            end="2026-09-19",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    closes = _flat_close(raw, tickers)
    # RTH filter for hourly (US)
    if interval == "1h":
        idx = closes.index
        if getattr(idx, "tz", None) is not None:
            et = idx.tz_convert("America/New_York")
        else:
            et = idx
        hours = pd.Series(et.hour + et.minute / 60.0, index=closes.index)
        # keep 9:30-15:30 inclusive bars
        mask = (hours >= 9.5) & (hours <= 15.5)
        closes = closes.loc[mask]
    spy = closes["SPY"].dropna() if "SPY" in closes.columns else pd.Series(dtype=float)
    vix = closes["^VIX"].dropna() if "^VIX" in closes.columns else pd.Series(dtype=float)
    sector = closes[[c for c in ETFS if c in closes.columns]].dropna(how="any")
    # align spy/vix
    spy = spy.reindex(sector.index).ffill()
    vix = vix.reindex(sector.index).ffill()
    return sector, spy, vix


def rs_pct(series: pd.Series, lookback: int) -> float:
    if len(series) <= lookback:
        return np.nan
    a = float(series.iloc[-lookback - 1])
    b = float(series.iloc[-1])
    if a <= 0:
        return np.nan
    return (b / a - 1.0) * 100.0


def accel(series: pd.Series, fast: int, slow: int) -> float:
    """Recent fast-window return minus prior slow-window return (percentage points)."""
    need = fast + slow
    if len(series) <= need:
        return np.nan
    recent = rs_pct(series.iloc[-(fast + 1) :], fast)
    # prior window ending where recent starts
    prior_slice = series.iloc[-(fast + slow + 1) : -(fast)]
    if len(prior_slice) <= slow:
        return np.nan
    a = float(prior_slice.iloc[0])
    b = float(prior_slice.iloc[-1])
    if a <= 0:
        return np.nan
    prior = (b / a - 1.0) * 100.0
    if np.isnan(recent) or np.isnan(prior):
        return np.nan
    return recent - prior


def rank_map(scores: Dict[str, float], descending: bool = True) -> Dict[str, int]:
    items = [(k, v) for k, v in scores.items() if v is not None and not np.isnan(v)]
    items.sort(key=lambda x: x[1], reverse=descending)
    return {k: i + 1 for i, (k, _) in enumerate(items)}


def weights_from_ranks(ranks: Dict[str, int], quotas: List[int] = QUOTAS) -> Dict[str, float]:
    """Map rank->quota weight; missing ranks get 0. Sum normalized to 1."""
    inv = {r: etf for etf, r in ranks.items()}
    raw = {}
    for r, q in enumerate(quotas, start=1):
        etf = inv.get(r)
        if etf:
            raw[etf] = float(q)
    s = sum(raw.values())
    if s <= 0:
        n = max(1, len(ranks))
        return {k: 1.0 / n for k in ranks}
    return {k: v / s for k, v in raw.items()}


@dataclass
class ExploreDecision:
    size: int
    sectors: List[str]
    reason: str
    disagree: bool
    gap13: float


def decide_explore(
    rs3: Dict[str, float],
    rs21: Dict[str, float],
    acc: Dict[str, float],
    spy_rs21: float,
    vix: float,
    mode: str,
    max_explore: int = 10,
    force_fill: bool = False,
) -> ExploreDecision:
    """
    mode:
      - off: size 0
      - c_rule: checklist C
      - force: always fill max_explore from accel/21 leaders
    """
    if mode == "off":
        return ExploreDecision(0, [], "off", False, 0.0)

    r3 = rank_map(rs3)
    r21 = rank_map(rs21)
    ra = rank_map(acc)
    if not r3 or not ra:
        return ExploreDecision(0, [], "insufficient", False, 0.0)

    # gap13 in score space
    by_rank = sorted(r3.items(), key=lambda x: x[1])
    top = [etf for etf, _ in by_rank[:3]]
    gap13 = float(rs3.get(top[0], 0) - rs3.get(top[2], 0)) if len(top) >= 3 else 0.0

    accel_leader = min(ra.items(), key=lambda x: x[1])[0]
    m3_leader = min(r3.items(), key=lambda x: x[1])[0]
    disagree = accel_leader != m3_leader

    if mode == "force" or force_fill:
        # fill from 21d leaders excluding 3M top2
        exclude = {etf for etf, rk in r3.items() if rk <= 2}
        ordered = [etf for etf, _ in sorted(r21.items(), key=lambda x: x[1]) if etf not in exclude]
        picked = ordered[:max_explore]
        # pad with accel if needed
        for etf, _ in sorted(ra.items(), key=lambda x: x[1]):
            if len(picked) >= max_explore:
                break
            if etf not in picked and etf not in exclude:
                picked.append(etf)
        return ExploreDecision(max_explore, picked[:max_explore], "force_fill", disagree, gap13)

    # --- C-rule ignition ---
    if not disagree:
        return ExploreDecision(0, [], "no_disagree", False, gap13)

    # accel leader must be 3M rank >= 5 and accel > 0
    if r3.get(accel_leader, 99) < 5:
        return ExploreDecision(0, [], "accel_already_core", True, gap13)
    if acc.get(accel_leader, -999) <= 0:
        return ExploreDecision(0, [], "accel_nonpositive", True, gap13)

    # confirm: 21d rank 1 or 2 is accel leader, and beats SPY 21d
    confirm = r21.get(accel_leader, 99) <= 2 and rs21.get(accel_leader, -999) > (
        spy_rs21 if not np.isnan(spy_rs21) else 0.0
    )
    if not confirm:
        return ExploreDecision(0, [], "confirm_fail", True, gap13)

    # volume knob
    if gap13 >= GAP13_LARGE:
        cap = min(2, max_explore)
        reason = "gap_large"
    elif gap13 >= GAP13_MID:
        cap = min(6, max_explore)
        reason = "gap_mid"
    else:
        cap = max_explore
        reason = "gap_small"

    if not np.isnan(vix) and vix >= VIX_PANIC:
        cap = min(cap, max(1, cap // 2), 6)
        reason += "+vix_panic"

    # pick explore sectors: accel/21 aligned, exclude 3M top2, sector cap later as ETF list
    exclude = {etf for etf, rk in r3.items() if rk <= 2}
    candidates = []
    for etf, _ in sorted(ra.items(), key=lambda x: x[1]):
        if etf in exclude:
            continue
        if r21.get(etf, 99) <= 4 and acc.get(etf, -999) > 0:
            candidates.append(etf)
    # ensure accel leader first
    if accel_leader in candidates:
        candidates = [accel_leader] + [c for c in candidates if c != accel_leader]
    elif accel_leader not in exclude:
        candidates = [accel_leader] + candidates

    picked = candidates[:cap]
    if not picked:
        return ExploreDecision(0, [], "no_candidates", True, gap13)
    return ExploreDecision(len(picked), picked, reason, True, gap13)


def blend_weights(
    ranks_3m: Dict[str, int],
    explore: ExploreDecision,
    core_slots: int = 50,
    total_slots: int = 60,
) -> Dict[str, float]:
    core_w = weights_from_ranks(ranks_3m)
    explore_n = max(0, min(explore.size, total_slots))
    if explore_n <= 0 or not explore.sectors:
        return core_w

    core_frac = core_slots / total_slots
    exp_frac = explore_n / total_slots
    # renormalize so core_frac + exp_frac == 1 when explore_n < 10
    scale = core_frac + exp_frac
    core_frac /= scale
    exp_frac /= scale

    exp_raw = {etf: 1.0 for etf in explore.sectors}
    # sector cap: max 4 slots equivalent -> max weight share within explore
    # equal among explore names, but clamp per-name to 4/explore_n of explore sleeve
    n = len(exp_raw)
    per = 1.0 / n
    cap = min(1.0, 4.0 / max(1, explore_n))
    # if equal per > cap, redistribute (simple equal with cap)
    capped = {k: min(per, cap) for k in exp_raw}
    s = sum(capped.values())
    exp_w = {k: (v / s) * exp_frac for k, v in capped.items()}

    out = {k: v * core_frac for k, v in core_w.items()}
    for k, v in exp_w.items():
        out[k] = out.get(k, 0.0) + v
    # normalize
    tot = sum(out.values())
    return {k: v / tot for k, v in out.items()} if tot > 0 else core_w


def session_end_positions(index: pd.DatetimeIndex, interval: str) -> List[int]:
    """Return integer positions of last bar in each session day."""
    if interval == "daily":
        return list(range(len(index)))
    if getattr(index, "tz", None) is not None:
        days = pd.Index(index.tz_convert("America/New_York").date)
    else:
        days = pd.Index(index.date)
    # last index per day
    tmp = pd.Series(np.arange(len(index)), index=days)
    return list(tmp.groupby(level=0).max().astype(int).tolist())


def run_strategy(
    sector: pd.DataFrame,
    spy: pd.Series,
    vix: pd.Series,
    interval: str,
    strategy: str,
    max_explore: int = 10,
    force_fill: bool = False,
    eval_start: Optional[pd.Timestamp] = None,
) -> dict:
    """
    strategy:
      - equal
      - rs3m
      - rs21
      - core_explore

    If eval_start is set, bars before it are warmup only (no PnL / no metrics window).
    """
    lb = LB[interval]
    end_pos = session_end_positions(sector.index, interval)

    equity = 1.0
    eq_curve = [1.0]
    dates = [str(sector.index[end_pos[0]])] if end_pos else []
    weights = {c: 0.0 for c in sector.columns}
    turnover_sum = 0.0
    rebalances = 0
    explore_on_days = 0
    explore_sizes = []
    reasons = {}
    measuring = False

    min_hist = lb["rs_3m"] + 5
    eval_i = None
    if eval_start is not None:
        # first index >= eval_start
        for j, ts in enumerate(sector.index):
            if ts >= eval_start:
                eval_i = j
                break

    for di, i in enumerate(end_pos):
        if i < min_hist:
            continue
        ts = sector.index[i]
        in_eval = eval_i is None or i >= eval_i
        if in_eval and not measuring:
            measuring = True
            equity = 1.0
            eq_curve = [1.0]
            dates = [str(ts)]
            # keep warmup weights so day-1 turnover is not a full rebuild from cash
            turnover_sum = 0.0
            rebalances = 0
            explore_on_days = 0
            explore_sizes = []
            reasons = {}

        window = sector.iloc[: i + 1]
        spy_w = spy.iloc[: i + 1]
        vix_now = float(vix.iloc[i]) if i < len(vix) and not np.isnan(vix.iloc[i]) else np.nan

        rs3 = {c: rs_pct(window[c], lb["rs_3m"]) for c in sector.columns}
        rs21 = {c: rs_pct(window[c], lb["rs_21"]) for c in sector.columns}
        accs = {c: accel(window[c], lb["accel_fast"], lb["accel_slow"]) for c in sector.columns}
        spy_rs21 = rs_pct(spy_w, lb["rs_21"]) if len(spy_w) > lb["rs_21"] else np.nan

        if strategy == "equal":
            new_w = {c: 1.0 / len(sector.columns) for c in sector.columns}
            dec = ExploreDecision(0, [], "equal", False, 0.0)
        elif strategy == "rs3m":
            new_w = weights_from_ranks(rank_map(rs3))
            dec = ExploreDecision(0, [], "rs3m", False, 0.0)
        elif strategy == "rs21":
            new_w = weights_from_ranks(rank_map(rs21))
            dec = ExploreDecision(0, [], "rs21", False, 0.0)
        else:
            mode = "force" if force_fill else "c_rule"
            dec = decide_explore(
                rs3, rs21, accs, spy_rs21, vix_now, mode=mode, max_explore=max_explore, force_fill=force_fill
            )
            new_w = blend_weights(rank_map(rs3), dec, core_slots=60 - max_explore if force_fill else 50)

        if not measuring:
            weights = {c: new_w.get(c, 0.0) for c in sector.columns}
            continue

        all_keys = set(weights) | set(new_w)
        turnover = 0.5 * sum(abs(new_w.get(k, 0.0) - weights.get(k, 0.0)) for k in all_keys)
        cost = turnover * (COST_BPS / 10000.0) * 2.0
        equity *= max(1e-12, 1.0 - cost)
        turnover_sum += turnover
        rebalances += 1
        weights = {c: new_w.get(c, 0.0) for c in sector.columns}

        if dec.size > 0:
            explore_on_days += 1
        explore_sizes.append(dec.size)
        reasons[dec.reason] = reasons.get(dec.reason, 0) + 1

        if di + 1 < len(end_pos):
            ni = end_pos[di + 1]
            day_ret = 0.0
            for c in sector.columns:
                day_ret += weights.get(c, 0.0) * float(sector[c].iloc[ni] / sector[c].iloc[i] - 1.0)
            equity *= 1.0 + day_ret
            eq_curve.append(equity)
            dates.append(str(sector.index[ni]))

    arr = np.array(eq_curve, dtype=float) if eq_curve else np.array([1.0])
    return {
        "final_equity": float(arr[-1]) if len(arr) else 1.0,
        "n_rebalances": rebalances,
        "avg_turnover": float(turnover_sum / max(1, rebalances)),
        "explore_on_frac": float(explore_on_days / max(1, rebalances)),
        "explore_size_mean": float(np.mean(explore_sizes)) if explore_sizes else 0.0,
        "reasons": reasons,
        "equity": arr,
        "dates": dates,
    }


def metrics_from_curve(equity: np.ndarray, dates: List[str], bars_per_year: float) -> dict:
    if len(equity) < 5:
        return {"cagr_pct": None, "mdd_pct": None, "vol_pct": None, "sharpe": None}
    rets = np.diff(equity) / equity[:-1]
    total = float(equity[-1] / equity[0])
    years = max(1e-9, len(rets) / bars_per_year)
    cagr = total ** (1 / years) - 1.0
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    mdd = float(dd.min())
    vol = float(np.std(rets) * np.sqrt(bars_per_year)) if len(rets) else None
    sharpe = float((np.mean(rets) * bars_per_year) / vol) if vol and vol > 0 else None
    return {
        "cagr_pct": round(cagr * 100, 2),
        "mdd_pct": round(mdd * 100, 2),
        "vol_pct": round(vol * 100, 2) if vol is not None else None,
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "total_return_pct": round((total - 1.0) * 100, 2),
    }


def slice_panel_with_warmup(
    sector: pd.DataFrame,
    spy: pd.Series,
    vix: pd.Series,
    start: str,
    end: str,
    interval: str,
) -> Tuple[pd.DataFrame, pd.Series, pd.Series, Optional[pd.Timestamp], int]:
    """
    Slice [start, end] but prepend lookback bars from the full panel so RS/accel
    are defined on day-1 of the regime. Returns eval_start timestamp.
    """
    idx = sector.index
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if getattr(idx, "tz", None) is not None:
        if start_ts.tzinfo is None:
            start_ts = start_ts.tz_localize(idx.tz)
        if end_ts.tzinfo is None:
            end_ts = end_ts.tz_localize(idx.tz)

    # bars inside the evaluation window
    in_win = idx[(idx >= start_ts) & (idx <= end_ts)]
    if len(in_win) == 0:
        empty = sector.iloc[0:0]
        return empty, spy.iloc[0:0], vix.iloc[0:0], None, 0

    eval_start = in_win[0]
    need = LB[interval]["rs_3m"] + LB[interval]["accel_slow"] + 10
    # locate first eval index in full panel, then back up `need` bars
    pos = int(idx.get_indexer([eval_start])[0])
    warm_pos = max(0, pos - need)
    panel = sector.iloc[warm_pos:]
    panel = panel.loc[:end_ts]
    return (
        panel,
        spy.reindex(panel.index).ffill(),
        vix.reindex(panel.index).ffill(),
        eval_start,
        len(in_win),
    )


def pack_run(result: dict, interval: str) -> dict:
    # equity points are daily session-end rebalances even on 1h
    bpy_pts = 252.0
    m = metrics_from_curve(result["equity"], result["dates"], bpy_pts)
    return {
        **m,
        "final_equity": round(result["final_equity"], 4),
        "avg_turnover": round(result["avg_turnover"], 4),
        "explore_on_frac": round(result["explore_on_frac"], 4),
        "explore_size_mean": round(result["explore_size_mean"], 3),
        "n_rebalances": result["n_rebalances"],
        "reasons": result["reasons"],
    }


def run_mode(interval: str) -> dict:
    print(f"\n=== Downloading {interval} panel ===")
    sector, spy, vix = download_panel(interval)
    print(f"sector shape={sector.shape} start={sector.index.min()} end={sector.index.max()}")

    regimes = dict(REGIMES)
    regimes["full_available"] = (
        str(sector.index.min().date()),
        str(sector.index.max().date()),
    )

    strategies = {
        "equal": dict(strategy="equal"),
        "rs3m": dict(strategy="rs3m"),
        "rs21": dict(strategy="rs21"),
        "core_explore_10": dict(strategy="core_explore", max_explore=10, force_fill=False),
        "explore_max8": dict(strategy="core_explore", max_explore=8, force_fill=False),
        "explore_max12": dict(strategy="core_explore", max_explore=12, force_fill=False),
        "explore_force10": dict(strategy="core_explore", max_explore=10, force_fill=True),
    }

    out = {"interval": interval, "range": regimes["full_available"], "regimes": {}}
    for reg_name, span in regimes.items():
        if span is None:
            continue
        start, end = span
        try:
            sec_s, spy_s, vix_s, eval_start, eval_bars = slice_panel_with_warmup(
                sector, spy, vix, start, end, interval
            )
        except Exception as exc:
            out["regimes"][reg_name] = {"skipped": True, "reason": f"slice_error:{exc}"}
            continue
        if eval_bars < 20:
            out["regimes"][reg_name] = {
                "skipped": True,
                "reason": "insufficient_eval_bars",
                "eval_bars": eval_bars,
                "panel_bars": len(sec_s),
            }
            print(f"  skip {reg_name}: eval_bars={eval_bars} panel={len(sec_s)}")
            continue
        print(f"  regime {reg_name}: eval_bars={eval_bars} panel={len(sec_s)} warmup_to={eval_start}")
        block = {"eval_bars": eval_bars, "panel_bars": len(sec_s)}
        for name, kwargs in strategies.items():
            t0 = time.time()
            res = run_strategy(sec_s, spy_s, vix_s, interval, eval_start=eval_start, **kwargs)
            block[name] = pack_run(res, interval)
            block[name]["seconds"] = round(time.time() - t0, 2)
            print(
                f"    {name}: ret={block[name].get('total_return_pct')}% "
                f"mdd={block[name].get('mdd_pct')}% explore_on={block[name].get('explore_on_frac')} "
                f"size_mean={block[name].get('explore_size_mean')}"
            )
        base = block.get("rs3m", {}).get("total_return_pct")
        if base is not None:
            for name, row in block.items():
                if isinstance(row, dict) and row.get("total_return_pct") is not None:
                    row["vs_rs3m_pct"] = round(row["total_return_pct"] - base, 2)
        out["regimes"][reg_name] = block
    return out


def main() -> None:
    t0 = time.time()
    os.makedirs(OUT_DIR, exist_ok=True)
    payload = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cost_bps_one_way": COST_BPS,
        "gap13_large": GAP13_LARGE,
        "gap13_mid": GAP13_MID,
        "vix_panic": VIX_PANIC,
        "note": (
            "1h history from yfinance is limited (~730d). "
            "2020/2022 story regimes require daily mode. "
            "Research-only; not wired to live C-2."
        ),
        "modes": {},
    }
    for interval in ("daily", "1h"):
        try:
            payload["modes"][interval] = run_mode(interval)
        except Exception as exc:
            payload["modes"][interval] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"ERROR {interval}: {exc}")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {OUT_PATH} in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
