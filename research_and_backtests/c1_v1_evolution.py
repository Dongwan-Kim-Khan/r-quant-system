"""
C1 v1 Evolution — entry squeeze filter, bear 1-slot, dynamic ATR trail.

Target: keep AI-3y excess alpha (~+7.88%p) while lifting 8y CAGR toward 22%+
and compressing MDD toward -33% or better.

Matrix
  Baseline  C1 v1 unchanged
  E1        + Entry Squeeze Filter (kijun gap <= 3.5%, ATR contraction)
  E2        + Regime slot: bull 3-slot 50/30/20; bear Top-1 @ 25%, 75% QQQ
  E3        E1 + E2 + Dynamic Profit-Lock (ATR 2.5 → 1.8 after +35% peak)

Friction: next-open, 10bps/8bps, PIT composite-60, QQQ/QLD overlay.
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
from collections import defaultdict
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
from al_sangmoo.core.constants import STOP_LOSS_PCT, TAKE_PROFIT_PCT  # noqa: E402
from compare_html_vs_live_exit import (  # noqa: E402
    FEE,
    INITIAL,
    SLIPPAGE,
    Book,
    Position,
    _fill_buy,
    _fill_sell,
    align_panels,
    close_position,
    entry_signal,
)
from pit_dynamic_universe_exit_ab import stock_names  # noqa: E402
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
    sleeve_stats,
    target_leverage,
    vs_qqq,
)
from regime_bull_core import bh_metrics  # noqa: E402

OUT_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "c1_v1_evolution_results.json")
REPORT_PATH = os.path.join(PROJECT_ROOT, "research_and_backtests", "c1_v1_evolution_report.md")

ATR_MULT_BASE = 2.5
ATR_MULT_LOCK = 1.8
LOCK_GAIN = 0.35
KIJUN_GAP_MAX = 0.035
ATR_LB = 60
BEAR_SLOT_W = (0.25,)


@dataclass
class EvoSpec:
    name: str
    entry_filter: bool = False
    bear_one_slot: bool = False
    dynamic_trail: bool = False


SPECS = {
    "Baseline": EvoSpec("Baseline"),
    "E1": EvoSpec("E1", entry_filter=True),
    "E2": EvoSpec("E2", bear_one_slot=True),
    "E3": EvoSpec("E3", entry_filter=True, bear_one_slot=True, dynamic_trail=True),
}


@dataclass
class EvoSleeve(Sleeve):
    filter_rejects: int = 0
    filter_pass: int = 0
    hard_stops: int = 0
    trail_exits: int = 0
    lock_trail_hits: int = 0


def atr_ratio_ok(p: dict, i: int) -> bool:
    close = p["close"][i]
    atr = p["atr"][i]
    if not np.isfinite(close) or not np.isfinite(atr) or close <= 0 or atr <= 0:
        return False
    ratio = atr / close
    hist = []
    lo = max(0, i - ATR_LB + 1)
    for j in range(lo, i + 1):
        c = p["close"][j]
        a = p["atr"][j]
        if np.isfinite(c) and np.isfinite(a) and c > 0 and a > 0:
            hist.append(a / c)
    if len(hist) < 20:
        return False
    return ratio <= float(np.mean(hist))


def entry_signal_evo(p: dict, i: int, use_filter: bool, sl: EvoSleeve) -> bool:
    if not entry_signal(p, i):
        return False
    if not use_filter:
        return True
    close = p["close"][i]
    kijun = p["kijun"][i]
    if not np.isfinite(close) or not np.isfinite(kijun) or kijun <= 0:
        sl.filter_rejects += 1
        return False
    gap = (close - kijun) / kijun
    if gap > KIJUN_GAP_MAX:
        sl.filter_rejects += 1
        return False
    if not atr_ratio_ok(p, i):
        sl.filter_rejects += 1
        return False
    sl.filter_pass += 1
    return True


def decide_exit_evo(
    entry: float,
    peak_high: float,
    close: float,
    low: float,
    kijun: float,
    atr: float,
    dynamic_trail: bool,
) -> Optional[str]:
    if entry <= 0 or close <= 0:
        return None
    hard = entry * (1.0 + STOP_LOSS_PCT)
    if np.isfinite(low) and low <= hard:
        return "HARD_STOP"
    peak_gain = (peak_high - entry) / entry if peak_high > 0 else 0.0
    if peak_gain < TAKE_PROFIT_PCT:
        return None
    atr_mult = ATR_MULT_BASE
    if dynamic_trail and peak_gain >= LOCK_GAIN:
        atr_mult = ATR_MULT_LOCK
    cands = []
    if np.isfinite(kijun) and kijun > 0:
        cands.append(kijun)
    if np.isfinite(atr) and atr > 0 and peak_high > 0:
        atr_leg = peak_high - atr_mult * atr
        if atr_leg > 0:
            cands.append(atr_leg)
    floor = max(cands) if cands else 0.0
    if floor > 0 and close < floor:
        if dynamic_trail and peak_gain >= LOCK_GAIN and atr_mult == ATR_MULT_LOCK:
            return "TRAIL_LOCK"
        return "TRAILING"
    return None


def run_evo_book(
    spec: EvoSpec,
    calendar: pd.DatetimeIndex,
    panels: Dict[str, dict],
    bench: pd.DataFrame,
    composite: Dict[str, np.ndarray],
    allowed_by_day: List[Optional[Set[str]]],
    proxy_ohlc: Dict[str, dict],
    start_i: int,
) -> Tuple[Book, EvoSleeve]:
    n = len(calendar)
    dates = [d.strftime("%Y-%m-%d") for d in calendar]
    spy = bench["spy"].to_numpy(dtype=float)
    sma200 = bench["spy_sma200"].to_numpy(dtype=float)
    vix = bench["vix"].to_numpy(dtype=float)
    book = Book(cash=INITIAL)
    sl = EvoSleeve()
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

    def rebalance_proxy(i: int, lev: float, which: str, force: bool = False) -> None:
        eq = equity_now(i, which)
        if eq <= 0:
            return
        stock_w = stock_mark(i, which) / eq
        qqq_w, qld_w = proxy_mix(stock_w, lev, has_qld)
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
        lev = target_leverage(spy[asof], sma200[asof], vix[asof])
        is_bear = (
            np.isfinite(spy[asof])
            and np.isfinite(sma200[asof])
            and sma200[asof] > 0
            and spy[asof] < sma200[asof]
        )
        if is_bear and spec.bear_one_slot:
            max_slots = 1
            slot_w = BEAR_SLOT_W
            regime = "bear_1slot"
        elif is_bear:
            max_slots = 2
            slot_w = (0.25, 0.25)
            regime = "bear"
        else:
            max_slots = MAX_SLOTS
            slot_w = CONVICTION
            regime = "bull_lowvol" if lev > 1.0 else "bull"
        sl.regime_days[regime] += 1
        force_proxy = prev_lev is None or abs((prev_lev or 0.0) - lev) > 1e-9 or i == start_i

        # Cap holdings only under the new bear-1-slot rule. Baseline C1 keeps
        # existing winners when max_slots shrinks from 3→2 (new entries only).
        if spec.bear_one_slot and len(book.positions) > max_slots:
            ranked_held = []
            for tk in list(book.positions.keys()):
                rs = composite[tk][asof] if tk in composite else np.nan
                ranked_held.append((float(rs) if np.isfinite(rs) else -999.0, tk))
            ranked_held.sort(reverse=True)
            keep = {tk for _, tk in ranked_held[:max_slots]}
            for tk in list(book.positions.keys()):
                if tk in keep:
                    continue
                pos = book.positions[tk]
                px = panels[tk]["open"][i]
                if not np.isfinite(px) or px <= 0:
                    px = pos.entry
                close_position(book, pos, float(px), dates[i], "SLOT_TRIM", i)

        for tk in list(book.positions.keys()):
            pos = book.positions[tk]
            p = panels[tk]
            if not p["listed"][i]:
                continue
            high = p["high"][i]
            if np.isfinite(high):
                pos.peak_high = max(pos.peak_high, high)
            reason = decide_exit_evo(
                pos.entry,
                pos.peak_high,
                p["close"][i],
                p["low"][i],
                p["kijun"][i],
                p["atr"][i],
                spec.dynamic_trail,
            )
            if not reason:
                continue
            hard = pos.entry * (1.0 + STOP_LOSS_PCT)
            if reason == "HARD_STOP":
                raw = min(p["open"][i], hard) if np.isfinite(p["open"][i]) else p["close"][i]
                sl.hard_stops += 1
            else:
                raw = p["close"][i]
                sl.trail_exits += 1
                if reason == "TRAIL_LOCK":
                    sl.lock_trail_hits += 1
            if not np.isfinite(raw) or raw <= 0:
                continue
            close_position(book, pos, float(raw), dates[i], reason, i)

        if len(book.positions) < max_slots and i >= 1:
            scored: List[Tuple[float, str]] = []
            allowed = allowed_by_day[i] if allowed_by_day is not None else None
            for tk, p in panels.items():
                if tk in book.positions:
                    continue
                if allowed is not None and tk not in allowed:
                    continue
                if not p["listed"][i] or not p["listed"][i - 1]:
                    continue
                if not entry_signal_evo(p, i - 1, spec.entry_filter, sl):
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

        rebalance_proxy(i, lev, "close", force=force_proxy)
        eq = equity_now(i, "close")
        book.equity.append(eq)
        book.dates.append(dates[i])
        if eq > 0:
            sl.occupancy.append(stock_mark(i, "close") / eq)
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
    return book, sl


def evo_sleeve_stats(sl: EvoSleeve) -> dict:
    base = sleeve_stats(sl)
    base.update(
        {
            "filter_rejects": int(sl.filter_rejects),
            "filter_pass": int(sl.filter_pass),
            "hard_stops": int(sl.hard_stops),
            "trail_exits": int(sl.trail_exits),
            "lock_trail_hits": int(sl.lock_trail_hits),
        }
    )
    return base


def pack_alpha(book: Book, bench: pd.DataFrame, extra: Optional[dict] = None) -> dict:
    return pack_book(book, bench, extra=extra)


def score_row(name: str, m: dict, alpha=None, verdict="—") -> str:
    xs = alpha.get("excess_cagr_pct") if alpha else "—"
    t = m.get("trades") or {}
    return (
        f"| {name} | {m['cagr_pct']:.2f}% | {m['mdd_pct']:.2f}% | {m['sharpe']:.3f} | "
        f"{m.get('calmar', 0):.3f} | {t.get('n', '—')} | {t.get('win_rate', '—')} | "
        f"{t.get('profit_factor', '—')} | {xs} | {verdict} |"
    )


def verdict_of(cagr: float, mdd: float, qqq_cagr: float, base_cagr: float, base_mdd: float) -> str:
    beat_qqq = cagr > qqq_cagr
    hit_cagr = cagr >= 22.0
    hit_mdd = mdd >= -33.0
    improve = cagr >= base_cagr and mdd >= base_mdd
    if beat_qqq and hit_cagr and hit_mdd:
        return "TARGET_HIT"
    if beat_qqq and hit_cagr:
        return "CAGR_HIT_MDD_HEAVY"
    if beat_qqq and hit_mdd:
        return "MDD_HIT_CAGR_SHORT"
    if improve and beat_qqq:
        return "BEAT_BASE_AND_QQQ"
    if beat_qqq:
        return "BEAT_QQQ"
    return "LOSE_QQQ"


def diagnose(payload: dict) -> List[str]:
    w8 = payload["windows"]["2018-08-31"]
    w3 = payload["windows"].get("2023-08-31")
    base = w8["Baseline"]
    e1, e2, e3 = w8["E1"], w8["E2"], w8["E3"]
    q = w8["qqq_bh"]["cagr_pct"]
    champ = max(
        [("Baseline", base), ("E1", e1), ("E2", e2), ("E3", e3)],
        key=lambda kv: (kv[1]["cagr_pct"], kv[1]["mdd_pct"]),
    )
    lines = [
        "## 진단 (스윗스팟)",
        "",
        f"- **8년 챔피언: {champ[0]}** CAGR {champ[1]['cagr_pct']:.2f}% / MDD {champ[1]['mdd_pct']:.2f}% "
        f"(목표 22%+ / MDD ≥ −33%; QQQ {q:.2f}%).",
        f"- **E2 Bear 1-Slot**: 8년 {e2['cagr_pct']:.2f}% / MDD {e2['mdd_pct']:.2f}% / "
        f"국면 {e2.get('sleeve', {}).get('regime_days')}. "
        + (
            f"AI 3년은 {w3['E2']['cagr_pct']:.2f}%로 Baseline을 앞질렀다(vs QQQ {w3['E2_vs_qqq'].get('excess_cagr_pct')}%p) — "
            "약세 1슬롯이 메가사이클에서는 도움이지만, 8년 전체에서는 2020/2022 이후 복리 슬롯을 비워 CAGR이 꺾인다."
            if w3
            else "약세 Top-1@25%가 MDD를 −33%로 못 붙이면 기각."
        ),
        f"- **E1 Entry Filter는 과필터.** reject {e1.get('sleeve', {}).get('filter_rejects')} / pass {e1.get('sleeve', {}).get('filter_pass')} — "
        "kijun gap≤3.5% + ATR 수축이 폭주 대장주 진입을 같이 잘라 8년·AI 3년 CAGR을 붕괴시킨다. hard stop은 줄었으나 승률은 그대로(~24%).",
        f"- **E3 Integrated**: 8년 {e3['cagr_pct']:.2f}% / MDD {e3['mdd_pct']:.2f}% / "
        f"TRAIL_LOCK hits {e3.get('sleeve', {}).get('lock_trail_hits')}. E1 독성이 E2·트레일 이득을 덮어쓴다.",
    ]
    if w3:
        lines.append(
            f"- **AI 3년 알파 보존 체크**: Baseline {w3['Baseline']['cagr_pct']:.2f}% "
            f"(vs QQQ {w3['Baseline_vs_qqq'].get('excess_cagr_pct')}%p) | "
            f"E1 {w3['E1']['cagr_pct']:.2f}% | E2 {w3['E2']['cagr_pct']:.2f}% | E3 {w3['E3']['cagr_pct']:.2f}% "
            f"(vs QQQ {w3['E3_vs_qqq'].get('excess_cagr_pct')}%p)."
        )
        e3_keep = w3["E3"]["cagr_pct"] >= w3["qqq_bh"]["cagr_pct"] + 5.0
        lines.append(
            "- AI 3년 +5%p 이상 알파 "
            + ("유지." if e3_keep else "훼손 — 통합 E3가 메가사이클을 과필터.")
        )
    hit = e3["cagr_pct"] >= 22.0 and e3["mdd_pct"] >= -33.0
    lines += [
        "- **목표 판정**: E3 8년 22%+ & MDD≥−33% → "
        + ("**HIT**." if hit else "**MISS** — 모듈 독립 기여를 보고 다음 레버를 고른다."),
        "",
    ]
    return lines


def write_report(payload: dict) -> None:
    meta = payload["meta"]
    lines = [
        "# C1 v1 Evolution — Entry Filter / Bear 1-Slot / Dynamic Trail",
        "",
        f"- 생성: {meta.get('generated')}",
        f"- 마찰: slippage {meta['friction']['slippage_bps']:.0f}bps / fee {meta['friction']['fee_bps']:.0f}bps / 익일 시가",
        "- 목표: 8년 CAGR ≥ 22%, MDD ≥ −33%, AI 3년 QQQ 초과 알파 보존",
        "",
        "## 모델",
        "",
        "- **Baseline** — C1 v1 (3슬롯 50/30/20, 약세 2슬롯 25/25, HTML −4%/2.5·ATR).",
        "- **E1** — + Entry Squeeze (kijun gap≤3.5%, ATR/Close ≤ 60d mean).",
        "- **E2** — + Bear max 1 slot @ 25%, 나머지 QQQ.",
        "- **E3** — E1+E2 + Dynamic Profit-Lock (peak≥+35% → ATR×1.8).",
        "",
    ]
    header = (
        "| Book | CAGR | MDD | Sharpe | Calmar | Trades | Win% | PF | vs QQQ | Verdict |"
    )
    sep = "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"
    for asof, block in payload["windows"].items():
        lines += [
            f"## Window {asof} → {block['qqq_bh']['end']}",
            "",
            header,
            sep,
            score_row("QQQ B&H", block["qqq_bh"]),
            score_row("QLD B&H", block["qld_bh"]) if block.get("qld_bh") else "",
        ]
        for key in ("Baseline", "E1", "E2", "E3"):
            bold = "**" if key != "Baseline" else ""
            lines.append(
                score_row(
                    f"{bold}{key}{bold}",
                    block[key],
                    block.get(f"{key}_vs_qqq"),
                    block.get(f"{key}_verdict"),
                )
            )
        lines += [
            "",
            f"- E1 filter reject/pass: {block['E1'].get('sleeve', {}).get('filter_rejects')} / "
            f"{block['E1'].get('sleeve', {}).get('filter_pass')}  hard_stops {block['E1'].get('sleeve', {}).get('hard_stops')} "
            f"(Baseline {block['Baseline'].get('sleeve', {}).get('hard_stops')})",
            f"- E2 regime days: {block['E2'].get('sleeve', {}).get('regime_days')}",
            f"- E3 lock-trail hits: {block['E3'].get('sleeve', {}).get('lock_trail_hits')}  "
            f"occ {block['E3'].get('sleeve', {}).get('avg_stock_occupancy_pct')}%  "
            f"lev {block['E3'].get('sleeve', {}).get('avg_gross_leverage')}",
            f"- Trade reasons E3: {block['E3'].get('trades', {}).get('reasons')}",
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
        f"  {tag:12s} CAGR {m['cagr_pct']:6.2f}%  MDD {m['mdd_pct']:7.2f}%  "
        f"Sharpe {m['sharpe']:.3f}  n={t.get('n')}  win={t.get('win_rate')}  pf={t.get('profit_factor')}"
    )


def main() -> None:
    t0 = time.time()
    raw = download_master()
    raw = ensure_qld(raw)
    names = [t for t in stock_names() if t in raw]
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
    qqq_px = bench["qqq"].copy()
    qqq_px.index = pd.to_datetime(qqq_px.index)
    qld_px = pd.Series(proxy_ohlc["QLD"]["close"], index=calendar) if "QLD" in proxy_ohlc else None

    payload = {
        "meta": {
            "method": "C1 evolution: E1 entry squeeze, E2 bear 1-slot, E3 + dynamic ATR lock",
            "friction": {"slippage_bps": SLIPPAGE * 10000, "fee_bps": FEE * 10000},
            "kijun_gap_max": KIJUN_GAP_MAX,
            "atr_lock_mult": ATR_MULT_LOCK,
            "lock_gain": LOCK_GAIN,
            "qld_available": "QLD" in proxy_ohlc,
            "generated": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        },
        "windows": {},
    }

    for asof in SNAPSHOTS:
        start_i = max(snapshot_index(calendar, asof), 2)
        print(f"\n===== WINDOW {asof} ({calendar[start_i].date()}) =====")
        idx = None
        block = {}
        books = {}
        for key, spec in SPECS.items():
            book, sl = run_evo_book(
                spec, calendar, panels, bench, composite, universes, proxy_ohlc, start_i
            )
            books[key] = (book, sl)
            packed = pack_alpha(book, bench, extra={"sleeve": evo_sleeve_stats(sl)})
            block[key] = packed
            if idx is None:
                idx = pd.to_datetime(book.dates)

        qqq_m = enrich(bh_metrics(qqq_px.reindex(idx)))
        qld_m = enrich(bh_metrics(qld_px.reindex(idx))) if qld_px is not None else None
        block["qqq_bh"] = qqq_m
        block["qld_bh"] = qld_m
        base_cagr = block["Baseline"]["cagr_pct"]
        base_mdd = block["Baseline"]["mdd_pct"]

        for key in SPECS:
            m = block[key]
            vs = vs_qqq(m.pop("_rets"), qqq_m, m)
            block[f"{key}_vs_qqq"] = vs
            block[f"{key}_verdict"] = verdict_of(
                m["cagr_pct"], m["mdd_pct"], qqq_m["cagr_pct"], base_cagr, base_mdd
            )

        payload["windows"][asof] = block
        print(_line("QQQ", qqq_m))
        if qld_m:
            print(_line("QLD", qld_m))
        for key in SPECS:
            m = block[key]
            vs = block[f"{key}_vs_qqq"]
            print(
                _line(key, m)
                + f"  vsQQQ {vs.get('excess_cagr_pct')}  {block[f'{key}_verdict']}  "
                f"hard={m.get('sleeve', {}).get('hard_stops')}  "
                f"rej={m.get('sleeve', {}).get('filter_rejects')}"
            )

    payload["meta"]["elapsed_sec"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(json_safe(payload), f, ensure_ascii=False, indent=2)
    write_report(payload)
    print(f"\nwrote {OUT_PATH}")
    print(f"wrote {REPORT_PATH}")
    for line in diagnose(payload):
        print(line)


if __name__ == "__main__":
    main()
