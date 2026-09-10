"""
C1 v1 Precision Tuning — soft bear 2-slot + stop/trail sensitivity.

E1 entry filter permanently discarded (CAGR collapse).
E2 bear 1-slot won AI-3y (37.3%) but gutted 8y (10.3%).
This matrix keeps C1 entries and relaxes only bear sizing + R:R knobs.

  Baseline  C1 v1 (bear 2×25%, stop -4%, trail +15% / ATR 2.5)
  E2        bear Top-1 @ 25% (prior evolution reference)
  M1        bear 2×40% (idle 20% → QQQ), stop/trail = C1
  M2        M1 + stop -5.0%
  M3        M1 + trail trigger +12%
  M4        M1 + trail trigger +18% / ATR 3.0
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
from dataclasses import dataclass
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

OUT_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "c1_v1_tuning_results.json")
REPORT_PATH = os.path.join(PROJECT_ROOT, "research_and_backtests", "c1_v1_tuning_report.md")


@dataclass
class TuneSpec:
    name: str
    bear_slots: int = 2
    bear_weights: Tuple[float, ...] = (0.25, 0.25)
    stop_pct: float = -0.04
    trail_trigger: float = 0.15
    atr_mult: float = 2.5
    force_trim: bool = False  # True only for E2 1-slot reference


SPECS: Dict[str, TuneSpec] = {
    "Baseline": TuneSpec("Baseline"),
    "E2": TuneSpec("E2", bear_slots=1, bear_weights=(0.25,), force_trim=True),
    "M1": TuneSpec("M1", bear_slots=2, bear_weights=(0.40, 0.40)),
    "M2": TuneSpec("M2", bear_slots=2, bear_weights=(0.40, 0.40), stop_pct=-0.05),
    "M3": TuneSpec("M3", bear_slots=2, bear_weights=(0.40, 0.40), trail_trigger=0.12),
    "M4": TuneSpec(
        "M4",
        bear_slots=2,
        bear_weights=(0.40, 0.40),
        trail_trigger=0.18,
        atr_mult=3.0,
    ),
}


@dataclass
class TuneSleeve(Sleeve):
    hard_stops: int = 0
    trail_exits: int = 0


def decide_exit_tune(
    entry: float,
    peak_high: float,
    close: float,
    low: float,
    kijun: float,
    atr: float,
    stop_pct: float,
    trail_trigger: float,
    atr_mult: float,
) -> Optional[str]:
    if entry <= 0 or close <= 0:
        return None
    hard = entry * (1.0 + stop_pct)
    if np.isfinite(low) and low <= hard:
        return "HARD_STOP"
    peak_gain = (peak_high - entry) / entry if peak_high > 0 else 0.0
    if peak_gain < trail_trigger:
        return None
    cands = []
    if np.isfinite(kijun) and kijun > 0:
        cands.append(kijun)
    if np.isfinite(atr) and atr > 0 and peak_high > 0:
        atr_leg = peak_high - atr_mult * atr
        if atr_leg > 0:
            cands.append(atr_leg)
    floor = max(cands) if cands else 0.0
    if floor > 0 and close < floor:
        return "TRAILING"
    return None


def run_tune_book(
    spec: TuneSpec,
    calendar: pd.DatetimeIndex,
    panels: Dict[str, dict],
    bench: pd.DataFrame,
    composite: Dict[str, np.ndarray],
    allowed_by_day: List[Optional[Set[str]]],
    proxy_ohlc: Dict[str, dict],
    start_i: int,
) -> Tuple[Book, TuneSleeve]:
    n = len(calendar)
    dates = [d.strftime("%Y-%m-%d") for d in calendar]
    spy = bench["spy"].to_numpy(dtype=float)
    sma200 = bench["spy_sma200"].to_numpy(dtype=float)
    vix = bench["vix"].to_numpy(dtype=float)
    book = Book(cash=INITIAL)
    sl = TuneSleeve()
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
        if is_bear:
            max_slots = int(spec.bear_slots)
            slot_w = tuple(spec.bear_weights)
            regime = f"bear_{max_slots}slot"
        else:
            max_slots = MAX_SLOTS
            slot_w = CONVICTION
            regime = "bull_lowvol" if lev > 1.0 else "bull"
        sl.regime_days[regime] += 1
        force_proxy = prev_lev is None or abs((prev_lev or 0.0) - lev) > 1e-9 or i == start_i

        if spec.force_trim and len(book.positions) > max_slots:
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
            reason = decide_exit_tune(
                pos.entry,
                pos.peak_high,
                p["close"][i],
                p["low"][i],
                p["kijun"][i],
                p["atr"][i],
                spec.stop_pct,
                spec.trail_trigger,
                spec.atr_mult,
            )
            if not reason:
                continue
            hard = pos.entry * (1.0 + spec.stop_pct)
            if reason == "HARD_STOP":
                raw = min(p["open"][i], hard) if np.isfinite(p["open"][i]) else p["close"][i]
                sl.hard_stops += 1
            else:
                raw = p["close"][i]
                sl.trail_exits += 1
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
                if not entry_signal(p, i - 1):
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
            taken = len(book.positions)
            needed = equity_now(i, "open") * float(sum(slot_w[taken : taken + n_free]))
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


def pack_tune(book: Book, bench: pd.DataFrame, sl: TuneSleeve) -> dict:
    extra = sleeve_stats(sl)
    extra["hard_stops"] = int(sl.hard_stops)
    extra["trail_exits"] = int(sl.trail_exits)
    return pack_book(book, bench, extra={"sleeve": extra})


def score_row(name: str, m: dict, alpha=None, verdict="—") -> str:
    xs = alpha.get("excess_cagr_pct") if alpha else "—"
    t = m.get("trades") or {}
    return (
        f"| {name} | {m['cagr_pct']:.2f}% | {m['mdd_pct']:.2f}% | {m['sharpe']:.3f} | "
        f"{m.get('calmar', 0):.3f} | {t.get('n', '—')} | {t.get('win_rate', '—')} | "
        f"{t.get('profit_factor', '—')} | {t.get('avg_bars', '—')} | {xs} | {verdict} |"
    )


def verdict_of(cagr: float, mdd: float, qqq_cagr: float, base_cagr: float, base_ai_cagr: Optional[float], ai_cagr: Optional[float]) -> str:
    beat_base_8 = cagr > base_cagr
    beat_qqq = cagr > qqq_cagr
    keep_ai = True
    if base_ai_cagr is not None and ai_cagr is not None:
        keep_ai = ai_cagr >= base_ai_cagr - 1.0  # within 1%p of Baseline AI
    if beat_base_8 and beat_qqq and keep_ai:
        return "SWEET_SPOT"
    if beat_base_8 and beat_qqq:
        return "BEAT_BASE_AI_DRAG"
    if beat_qqq and keep_ai:
        return "KEEP_AI_BELOW_BASE"
    if beat_qqq:
        return "BEAT_QQQ"
    return "LOSE_QQQ"


def diagnose(payload: dict) -> List[str]:
    w8 = payload["windows"]["2018-08-31"]
    w3 = payload["windows"].get("2023-08-31")
    base = w8["Baseline"]
    models = [(k, w8[k]) for k in ("M1", "M2", "M3", "M4")]
    champ = max([("Baseline", base), ("E2", w8["E2"]), *models], key=lambda kv: kv[1]["cagr_pct"])
    lines = [
        "## 진단 (정밀 튜닝 스윗스팟)",
        "",
        f"- **8년 챔피언: {champ[0]}** CAGR {champ[1]['cagr_pct']:.2f}% / MDD {champ[1]['mdd_pct']:.2f}% "
        f"(Baseline {base['cagr_pct']:.2f}% / E2 {w8['E2']['cagr_pct']:.2f}%).",
        f"- **M1 Bear 2×40%**: 8년 {w8['M1']['cagr_pct']:.2f}% (vs Baseline {w8['M1']['cagr_pct'] - base['cagr_pct']:+.2f}%p, "
        f"vs E2 {w8['M1']['cagr_pct'] - w8['E2']['cagr_pct']:+.2f}%p). "
        "1슬롯 공백을 메우는지가 핵심.",
    ]
    if w3:
        lines.append(
            f"- **AI 3년**: Baseline {w3['Baseline']['cagr_pct']:.2f}% | E2 {w3['E2']['cagr_pct']:.2f}% | "
            f"M1 {w3['M1']['cagr_pct']:.2f}% | M2 {w3['M2']['cagr_pct']:.2f}% | "
            f"M3 {w3['M3']['cagr_pct']:.2f}% | M4 {w3['M4']['cagr_pct']:.2f}%."
        )
    lines += [
        f"- **M2 stop −5%**: 8년 {w8['M2']['cagr_pct']:.2f}% / win {w8['M2'].get('trades', {}).get('win_rate')}% / "
        f"hard {w8['M2'].get('sleeve', {}).get('hard_stops')} (M1 {w8['M1'].get('sleeve', {}).get('hard_stops')}). "
        "노이즈 회피가 승률·PF를 올리면 채택, giveback만 키우면 기각.",
        f"- **M3 trail +12%**: 8년 {w8['M3']['cagr_pct']:.2f}% / hold {w8['M3'].get('trades', {}).get('avg_bars')}d / "
        f"trail exits {w8['M3'].get('sleeve', {}).get('trail_exits')}. 조기 락이 텐배거를 자르면 AI 알파가 먼저 죽는다.",
        f"- **M4 wide +18%/ATR3**: 8년 {w8['M4']['cagr_pct']:.2f}% / hold {w8['M4'].get('trades', {}).get('avg_bars')}d / "
        f"MDD {w8['M4']['mdd_pct']:.2f}%. 대세 추종 vs giveback·MDD 트레이드오프.",
    ]
    sweet = [
        k
        for k in ("M1", "M2", "M3", "M4")
        if w8[k]["cagr_pct"] >= base["cagr_pct"]
        and (not w3 or w3[k]["cagr_pct"] >= w3["Baseline"]["cagr_pct"] - 1.0)
    ]
    cagr22 = [k for k in ("M1", "M2", "M3", "M4") if w8[k]["cagr_pct"] >= 22.0]
    if sweet:
        lines.append(f"- **스윗스팟 후보: {', '.join(sweet)}** — Baseline 8년을 이기면서 AI 알파를 1%p 이내로 보존.")
    elif cagr22:
        k = cagr22[0]
        ai_note = ""
        if w3:
            ai_note = f" AI 3년 {w3[k]['cagr_pct']:.2f}% (Baseline 대비 {w3[k]['cagr_pct'] - w3['Baseline']['cagr_pct']:+.2f}%p)."
        lines.append(
            f"- **CAGR 22%는 {k}가 달성** ({w8[k]['cagr_pct']:.2f}%)하나 MDD {w8[k]['mdd_pct']:.2f}%로 "
            f"Baseline {base['mdd_pct']:.2f}%보다 깊다.{ai_note} "
            "손절 완화는 승률·PF를 올리지만 크래시 베타를 키운다 — 채택 시 MDD 예산 재정의 필요."
        )
        lines.append(
            "- **스윗스팟(8년↑ + AI 보존 + MDD 비악화)은 미결.** Baseline C1을 기본 운용으로 두고, "
            "M2는 ‘CAGR 우선 변형’으로만 기록한다."
        )
    else:
        best_m = max(models, key=lambda kv: kv[1]["cagr_pct"])
        lines.append(
            f"- **스윗스팟 미결.** 튜닝군 최고는 {best_m[0]} ({best_m[1]['cagr_pct']:.2f}%). "
            "Baseline C1을 유지하고, 다음 레버는 슬롯/손절이 아닌 다른 축(레버리지·유니버스)으로 옮긴다."
        )
    lines.append("")
    return lines


def write_report(payload: dict) -> None:
    meta = payload["meta"]
    lines = [
        "# C1 v1 Precision Tuning — Bear 2-Slot Soft & R:R Sensitivity",
        "",
        f"- 생성: {meta.get('generated')}",
        f"- 마찰: slippage {meta['friction']['slippage_bps']:.0f}bps / fee {meta['friction']['fee_bps']:.0f}bps / 익일 시가",
        "- E1 진입 필터: **영구 폐기**. E2 1-슬롯은 참조만.",
        "",
        "## 모델",
        "",
        "- **Baseline** — C1 v1 (bull 50/30/20, bear 25/25, stop −4%, trail +15%/ATR 2.5).",
        "- **E2** — bear Top-1 @ 25% + force trim (진화 실험 참조).",
        "- **M1** — bear 2×40% (유휴 20% QQQ), 손절/트레일 = C1.",
        "- **M2** — M1 + hard stop −5.0%.",
        "- **M3** — M1 + trail trigger +12%.",
        "- **M4** — M1 + trail +18% / ATR×3.0.",
        "",
    ]
    header = (
        "| Book | CAGR | MDD | Sharpe | Calmar | Trades | Win% | PF | Avg bars | vs QQQ | Verdict |"
    )
    sep = "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"
    for asof, block in payload["windows"].items():
        lines += [
            f"## Window {asof} → {block['qqq_bh']['end']}",
            "",
            header,
            sep,
            score_row("QQQ B&H", block["qqq_bh"]),
            score_row("QLD B&H", block["qld_bh"]) if block.get("qld_bh") else "",
        ]
        for key in ("Baseline", "E2", "M1", "M2", "M3", "M4"):
            bold = "**" if key.startswith("M") else ""
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
            f"- M1 regime {block['M1'].get('sleeve', {}).get('regime_days')}  "
            f"occ {block['M1'].get('sleeve', {}).get('avg_stock_occupancy_pct')}%  "
            f"hard {block['M1'].get('sleeve', {}).get('hard_stops')} / trail {block['M1'].get('sleeve', {}).get('trail_exits')}",
            f"- M2 hard {block['M2'].get('sleeve', {}).get('hard_stops')} | "
            f"M3 trail {block['M3'].get('sleeve', {}).get('trail_exits')} hold {block['M3'].get('trades', {}).get('avg_bars')} | "
            f"M4 trail {block['M4'].get('sleeve', {}).get('trail_exits')} hold {block['M4'].get('trades', {}).get('avg_bars')}",
            f"- Reasons M1 {block['M1'].get('trades', {}).get('reasons')}",
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
        f"  {tag:10s} CAGR {m['cagr_pct']:6.2f}%  MDD {m['mdd_pct']:7.2f}%  "
        f"n={t.get('n')}  win={t.get('win_rate')}  pf={t.get('profit_factor')}  hold={t.get('avg_bars')}"
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

    print("[universe] composite RS …")
    universes = build_daily_universes_composite(calendar, raw)
    composite = {t: composite_rs(p["close"]) for t, p in panels.items()}
    qqq_px = bench["qqq"].copy()
    qqq_px.index = pd.to_datetime(qqq_px.index)
    qld_px = pd.Series(proxy_ohlc["QLD"]["close"], index=calendar) if "QLD" in proxy_ohlc else None

    payload = {
        "meta": {
            "method": "M1 bear 2x40%; M2 stop-5%; M3 trail+12%; M4 trail+18%/ATR3; refs Baseline+E2",
            "friction": {"slippage_bps": SLIPPAGE * 10000, "fee_bps": FEE * 10000},
            "e1_status": "PERMANENTLY_DISCARDED",
            "qld_available": "QLD" in proxy_ohlc,
            "generated": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        },
        "windows": {},
    }

    order = ("Baseline", "E2", "M1", "M2", "M3", "M4")
    for asof in SNAPSHOTS:
        start_i = max(snapshot_index(calendar, asof), 2)
        print(f"\n===== WINDOW {asof} ({calendar[start_i].date()}) =====")
        block = {}
        idx = None
        for key in order:
            book, sl = run_tune_book(
                SPECS[key], calendar, panels, bench, composite, universes, proxy_ohlc, start_i
            )
            packed = pack_tune(book, bench, sl)
            block[key] = packed
            if idx is None:
                idx = pd.to_datetime(book.dates)

        qqq_m = enrich(bh_metrics(qqq_px.reindex(idx)))
        qld_m = enrich(bh_metrics(qld_px.reindex(idx))) if qld_px is not None else None
        block["qqq_bh"] = qqq_m
        block["qld_bh"] = qld_m
        payload["windows"][asof] = block

        print(_line("QQQ", qqq_m))
        for key in order:
            m = block[key]
            vs = vs_qqq(m.pop("_rets"), qqq_m, m)
            block[f"{key}_vs_qqq"] = vs
            print(_line(key, m) + f"  vsQQQ {vs.get('excess_cagr_pct')}  hard={m.get('sleeve', {}).get('hard_stops')}")

    # Verdicts: same-window Baseline; AI-drag check only on the 8y scorecard.
    w3 = payload["windows"].get("2023-08-31")
    for asof, block in payload["windows"].items():
        base_cagr = block["Baseline"]["cagr_pct"]
        base_ai = w3["Baseline"]["cagr_pct"] if w3 else None
        for key in order:
            ai_c = w3[key]["cagr_pct"] if (w3 and asof.startswith("2018")) else None
            # On AI window skip AI-drag self-check (ai_c None → keep_ai True)
            block[f"{key}_verdict"] = verdict_of(
                block[key]["cagr_pct"],
                block[key]["mdd_pct"],
                block["qqq_bh"]["cagr_pct"],
                base_cagr,
                base_ai if asof.startswith("2018") else None,
                ai_c,
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
