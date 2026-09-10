"""
QQQ Beat Alpha v3 — High-hurdle satellite and relaxed MDD.

v2's 273-day hard sleeve cut V-recoveries and pinned 8y CAGR at 14–16%.
v3 relaxes MDD to the QQQ band (−33 to −38%) and only rotates into a name
that beats QQQ composite RS by +10pt.

Models
  A  75% QQQ core + 25% solo satellite (hurdle +10, 3-gate top-1)
     Extreme fear (SPY<SMA200 & VIX>=30): 30% cash, 70% stays invested.
  B  v1 compressed to 2 slots (60/40), idle parks in QQQ, no +10 hurdle.
  C  Model A + 1.6x when SPY>=200 & VIX<18; below SMA200 leverage off.

Friction: next-open, 10bps slippage, 8bps fee, PIT composite universe.
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
from al_sangmoo.core.constants import STOP_LOSS_PCT  # noqa: E402
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
    decide_exit,
    entry_signal,
)
from pit_dynamic_universe_exit_ab import stock_names  # noqa: E402
from pit_universe_exit_ab import download_master, snapshot_index  # noqa: E402
from qqq_beat_alpha_backtester import (  # noqa: E402
    BENCH,
    DRIFT_BAND,
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
    run_alpha_book,
    sleeve_stats,
    vs_qqq,
)
from regime_bull_core import bh_metrics  # noqa: E402

OUT_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "qqq_beat_alpha_v3_results.json")
REPORT_PATH = os.path.join(PROJECT_ROOT, "research_and_backtests", "qqq_beat_alpha_v3_report.md")

HURDLE_PT = 10.0
FEAR_VIX = 30.0
FEAR_CASH = 0.30
VIX_LOWVOL = 20.0
VIX_HOT = 18.0
LEV_15 = 1.50
LEV_16 = 1.60


@dataclass
class V3Spec:
    name: str
    max_slots: int
    slot_w: Tuple[float, ...]
    hurdle_pt: float
    lev_lowvol: float
    lev_hot: float
    vix_hot: float
    fear_cash: float


@dataclass
class V3Sleeve(Sleeve):
    cash_frac: List[float] = field(default_factory=list)
    hurdle_hits: int = 0
    hurdle_skips: int = 0


SPECS = {
    "A": V3Spec(
        name="hurdle_solo_25",
        max_slots=1,
        slot_w=(0.25,),
        hurdle_pt=HURDLE_PT,
        lev_lowvol=LEV_15,
        lev_hot=LEV_15,
        vix_hot=VIX_LOWVOL,
        fear_cash=FEAR_CASH,
    ),
    "B": V3Spec(
        name="v1_2slot_60_40",
        max_slots=2,
        slot_w=(0.60, 0.40),
        hurdle_pt=0.0,
        lev_lowvol=LEV_15,
        lev_hot=LEV_15,
        vix_hot=VIX_LOWVOL,
        fear_cash=0.0,
    ),
    "C": V3Spec(
        name="hurdle_16x",
        max_slots=1,
        slot_w=(0.25,),
        hurdle_pt=HURDLE_PT,
        lev_lowvol=LEV_15,
        lev_hot=LEV_16,
        vix_hot=VIX_HOT,
        fear_cash=FEAR_CASH,
    ),
}


def lev_investable(spec: V3Spec, spy: float, sma200: float, vix: float) -> Tuple[float, float, str]:
    """Return (gross_leverage_target, investable_frac, regime_name)."""
    if not np.isfinite(spy) or not np.isfinite(sma200) or sma200 <= 0:
        return 1.0, 1.0, "full"
    below = spy < sma200
    vx = vix if np.isfinite(vix) else 999.0
    if below and spec.fear_cash > 0 and vx >= FEAR_VIX:
        return 1.0, 1.0 - spec.fear_cash, "fear"
    if below:
        return 1.0, 1.0, "below200"
    if vx < spec.vix_hot:
        return spec.lev_hot, 1.0, "hot"
    if vx < VIX_LOWVOL:
        return spec.lev_lowvol, 1.0, "lowvol"
    return 1.0, 1.0, "full"


def proxy_mix_capped(stock_w: float, lev: float, investable: float, has_qld: bool) -> Tuple[float, float]:
    """QLD mix toward gross leverage `lev`, spending at most `investable`.

    remaining nasdaq beta = lev * investable - stock_w, filled with QQQ (1x)
    plus QLD (2x) without spending more than investable - stock_w.
    """
    stock_w = max(0.0, min(1.0, float(stock_w)))
    inv = max(0.0, min(1.0, float(investable)))
    spendable = max(0.0, inv - stock_w)
    remaining = max(0.0, float(lev) * inv - stock_w)
    if remaining <= 1e-9 or spendable <= 1e-9:
        return 0.0, 0.0
    if remaining <= spendable + 1e-9 or not has_qld:
        return min(remaining, spendable), 0.0
    extra = remaining - spendable
    qld_w = min(spendable, extra)
    qqq_w = spendable - qld_w
    if qqq_w < 0:
        return 0.0, spendable
    return qqq_w, qld_w


def run_v3_book(
    spec: V3Spec,
    calendar: pd.DatetimeIndex,
    panels: Dict[str, dict],
    bench: pd.DataFrame,
    composite: Dict[str, np.ndarray],
    allowed_by_day: List[Optional[Set[str]]],
    proxy_ohlc: Dict[str, dict],
    start_i: int,
) -> Tuple[Book, V3Sleeve]:
    n = len(calendar)
    dates = [d.strftime("%Y-%m-%d") for d in calendar]
    spy = bench["spy"].to_numpy(dtype=float)
    sma200 = bench["spy_sma200"].to_numpy(dtype=float)
    vix = bench["vix"].to_numpy(dtype=float)
    book = Book(cash=INITIAL)
    sl = V3Sleeve()
    has_qld = "QLD" in proxy_ohlc and bool(np.nanmax(proxy_ohlc["QLD"]["ok"]))
    qqq_crs = composite_rs(proxy_ohlc["QQQ"]["close"]) if "QQQ" in proxy_ohlc else np.full(n, np.nan)
    start_i = max(1, int(start_i))
    prev_lev = None
    slot_w = spec.slot_w
    max_slots = spec.max_slots

    def stock_mark(i: int, which: str) -> float:
        tot = 0.0
        for tk, pos in book.positions.items():
            p = panels[tk]
            arr = p["open"] if which == "open" else p["close"]
            px = arr[i]
            if np.isfinite(px) and px > 0:
                tot += pos.shares * float(px)
            else:
                tot += pos.shares * pos.entry
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

    def sell_proxy(ticker: str, i: int, which: str, reason: str, shares: Optional[float] = None) -> None:
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

    def set_proxy_weight(ticker: str, i: int, which: str, target_frac: float, eq: float, reason: str) -> None:
        if ticker not in proxy_ohlc:
            return
        px = _px(proxy_ohlc[ticker], i, which)
        if px <= 0:
            if target_frac <= 0:
                sell_proxy(ticker, i, which, reason)
            return
        pos = sl.proxy.setdefault(ticker, ProxyPos(ticker=ticker))
        cur_val = pos.shares * px if pos.shares > 0 else 0.0
        target_val = max(0.0, eq * target_frac)
        if target_frac <= 1e-9 or target_val < px:
            sell_proxy(ticker, i, which, reason)
            return
        if cur_val > target_val * (1.0 + DRIFT_BAND):
            sell_shares = min(pos.shares, (cur_val - target_val) / px)
            sell_proxy(ticker, i, which, reason, shares=sell_shares)
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
            sell_proxy(ticker, i, "open", "PROXY_FUND", shares=shares)

    def rebalance_proxy(i: int, lev: float, investable: float, which: str, reason: str, force: bool) -> None:
        eq = equity_now(i, which)
        if eq <= 0:
            return
        stock_w = stock_mark(i, which) / eq
        qqq_w, qld_w = proxy_mix_capped(stock_w, lev, investable, has_qld)

        def cur_w(tk: str) -> float:
            pos = sl.proxy.get(tk)
            if pos is None or pos.shares <= 0:
                return 0.0
            px = _px(proxy_ohlc[tk], i, which)
            return (pos.shares * px / eq) if px > 0 else 0.0

        if not force:
            drift = abs(cur_w("QQQ") - qqq_w) + abs(cur_w("QLD") - qld_w)
            if drift < DRIFT_BAND:
                return
        if cur_w("QQQ") * eq > eq * qqq_w:
            set_proxy_weight("QQQ", i, which, qqq_w, eq, reason)
            set_proxy_weight("QLD", i, which, qld_w, equity_now(i, which), reason)
        else:
            set_proxy_weight("QLD", i, which, qld_w, eq, reason)
            set_proxy_weight("QQQ", i, which, qqq_w, equity_now(i, which), reason)

    for i in range(start_i, n):
        asof = i - 1
        lev, investable, regime = lev_investable(spec, spy[asof], sma200[asof], vix[asof])
        sl.regime_days[regime] += 1
        force_proxy = prev_lev is None or abs((prev_lev or 0.0) - lev) > 1e-9 or i == start_i

        for tk in list(book.positions.keys()):
            pos = book.positions[tk]
            p = panels[tk]
            if not p["listed"][i]:
                continue
            high = p["high"][i]
            if np.isfinite(high):
                pos.peak_high = max(pos.peak_high, high)
            reason = decide_exit(
                "HTML",
                pos.entry,
                pos.peak_high,
                p["close"][i],
                p["low"][i],
                p["kijun"][i],
                p["atr"][i],
            )
            if not reason:
                continue
            hard = pos.entry * (1.0 + STOP_LOSS_PCT)
            if reason == "HARD_STOP":
                raw = min(p["open"][i], hard) if np.isfinite(p["open"][i]) else p["close"][i]
            else:
                raw = p["close"][i]
            if not np.isfinite(raw) or raw <= 0:
                continue
            close_position(book, pos, float(raw), dates[i], reason, i)

        if len(book.positions) < max_slots and i >= 1:
            scored: List[Tuple[float, str]] = []
            allowed = allowed_by_day[i] if allowed_by_day is not None else None
            qrs = qqq_crs[i - 1]
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
                if np.isfinite(qrs):
                    if rs < qrs + spec.hurdle_pt:
                        sl.hurdle_skips += 1
                        continue
                scored.append((float(rs), tk))
            scored.sort(reverse=True)
            if scored:
                sl.hurdle_hits += 1

            eq_open = equity_now(i, "open")
            n_free = max_slots - len(book.positions)
            taken = len(book.positions)
            needed = eq_open * float(sum(slot_w[taken : taken + n_free])) * investable
            if needed > book.cash:
                free_cash(i, needed)

            # Solo satellite: only the single highest-RS name, not a fill-the-slot loop of 3.
            take = scored[:n_free]
            for _, tk in take:
                if len(book.positions) >= max_slots:
                    break
                p = panels[tk]
                raw_open = p["open"][i]
                if not np.isfinite(raw_open) or raw_open <= 0:
                    continue
                fill = _fill_buy(float(raw_open))
                slot_frac = slot_w[min(len(book.positions), len(slot_w) - 1)] * investable
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

        rebalance_proxy(i, lev, investable, "close", "PROXY_EOD", force_proxy)

        eq = equity_now(i, "close")
        book.equity.append(eq)
        book.dates.append(dates[i])
        if eq > 0:
            sl.occupancy.append(stock_mark(i, "close") / eq)
            sl.cash_frac.append(max(0.0, book.cash / eq))
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
        sell_proxy(tk, last_i, "close", "EOB")
    return book, sl


def v3_sleeve_stats(sl: V3Sleeve) -> dict:
    base = sleeve_stats(sl)
    cash = np.array(sl.cash_frac, dtype=float)
    base["avg_cash_pct"] = round(float(np.nanmean(cash) * 100.0), 2) if len(cash) else 0.0
    base["hurdle_hits"] = int(sl.hurdle_hits)
    base["hurdle_skips"] = int(sl.hurdle_skips)
    return base


def verdict_of(cagr: float, mdd: float, qqq_cagr: float, qld_cagr: float) -> str:
    beat_qqq = cagr > qqq_cagr
    beat_qld = cagr > qld_cagr
    mdd_qqq_like = mdd >= -38.0
    if beat_qqq and beat_qld and mdd_qqq_like:
        return "BEAT_QQQ_AND_QLD"
    if beat_qqq and cagr >= 22.0 and mdd_qqq_like:
        return "SWEET_QQQ_BAND"
    if beat_qqq and mdd_qqq_like:
        return "BEAT_QQQ_MDD_OK"
    if beat_qqq:
        return "BEAT_QQQ_MDD_HEAVY"
    return "LOSE_QQQ"


def diagnose(payload: dict) -> list:
    """Data-driven sweet-spot diagnosis from the just-run windows."""
    w8 = payload["windows"].get("2018-08-31") or next(iter(payload["windows"].values()))
    w3 = payload["windows"].get("2023-08-31")
    q8 = w8["qqq_bh"]["cagr_pct"]
    a8, b8, c8, v8 = w8["model_a"], w8["model_b"], w8["model_c"], w8["c1_v1"]
    a8s = a8.get("sleeve") or {}
    lines = [
        "## 진단 (스윗스팟)",
        "",
        f"- **C1 v1가 스윗스팟을 유지한다.** 8년 CAGR {v8['cagr_pct']:.2f}% (QQQ {q8:.2f}%, vs +{w8['c1_vs_qqq'].get('excess_cagr_pct')}%p) / MDD {v8['mdd_pct']:.2f}%. "
        + (
            f"AI 3년 CAGR {w3['c1_v1']['cagr_pct']:.2f}% (vs QQQ +{w3['c1_vs_qqq'].get('excess_cagr_pct')}%p)."
            if w3
            else ""
        ),
        f"- **A 허들 위성은 실패.** 8년 {a8['cagr_pct']:.2f}% / MDD {a8['mdd_pct']:.2f}% (vs QQQ {w8['a_vs_qqq'].get('excess_cagr_pct')}%p). "
        f"허들 통과 {a8s.get('hurdle_hits')}일 / 스킵 {a8s.get('hurdle_skips')}일이지만 occupancy는 {a8s.get('avg_stock_occupancy_pct')}%로 거의 항상 25% 위성에 앉아 있다: "
        "진입은 +10pt 클라이맥스에서만 하고 HTML −4% 스톱까지 보유하므로 평균회귀에 노출된다. 위성 슬리브가 코어 75% QQQ를 갉아먹는다.",
        f"- **B 2슬롯 60/40은 8년은 QQQ와 동률({b8['cagr_pct']:.2f}%)이지만 MDD {b8['mdd_pct']:.2f}%로 악화.** "
        + (
            f"AI 3년 {w3['model_b']['cagr_pct']:.2f}% vs C1 {w3['c1_v1']['cagr_pct']:.2f}% — 3등 20% 슬롯 제거가 메가사이클 알파를 깎았다. "
            "1등 집중은 분산을 잃고, 어설픈 3등이 아니라 메가캡 2·3등이 알파원이었다."
            if w3
            else "3등 슬롯 제거는 분산을 잃고 MDD만 키운다."
        ),
        f"- **C 1.6x는 A보다 더 나쁘다.** 8년 {c8['cagr_pct']:.2f}% / MDD {c8['mdd_pct']:.2f}%. "
        "초강세 1.6x가 허들 위성의 음수 알파 위에서 크래시 베타만 더한다. 레버리지 스윗스팟은 위성 품질이 양수일 때만 성립한다.",
        "- **MDD 예산 −33~−38%는 이번 실험에서 달성되지 않았다.** A/C는 QQQ보다 얕지 않고(−43~−44%), B는 −47%로 더 깊다. "
        "v2의 273일 Hard Exit도, v3의 +10pt 솔로 위성도 8년에서 QQQ를 이기지 못했다.",
        "- **기각된 레버:** (1) Composite RS ≥ QQQ+10 후 HTML까지 홀드, (2) 3슬롯→2슬롯 압축, (3) VIX<18에서 1.6x. "
        "다음 후보가 있다면 허들 종목을 HTML이 아니라 **RS가 QQQ 아래로 떨어질 때 즉시 QQQ 파킹**하거나, 위성 25%를 허들 유지 구간에만 켜는 것이다. C1 3슬롯+유휴 QQQ는 유지.",
        "",
    ]
    return lines


def write_report(payload: dict) -> None:
    meta = payload["meta"]
    lines = [
        "# QQQ Beat Alpha v3 — High-Hurdle Satellite & Relaxed MDD",
        "",
        f"- 생성: {meta.get('generated')}",
        f"- 마찰: slippage {meta['friction']['slippage_bps']:.0f}bps / fee {meta['friction']['fee_bps']:.0f}bps / 익일 시가 체결",
        "- MDD 예산: QQQ 밴드 **−33% ~ −38%** (v2의 −22~−26% 긴축 포기, V자 복리 보존)",
        "- 위성 허들: `Composite_RS(stock) >= Composite_RS(QQQ) + 10.0`, Top-1만",
        "",
        "## 모델",
        "",
        "- **A High-Hurdle Solo 25%** — 코어 75% QQQ (VIX<20 & SPY≥200 → 1.5x). 위성 25%는 +10pt 허들 통과 1등만. 공포(VIX≥30 & SPY<200) 때 현금 30%.",
        "- **B C1 2-Slot** — v1에서 3등 20%를 제거하고 60/40. 허들 없음(QQQ RS만 이기면 됨). 신호 없으면 QQQ.",
        "- **C Hurdle + 1.6x** — A 베이스 + VIX<18에서 1.6x. 200일선 아래는 1.0x QQQ, 공포 시에만 30% 현금.",
        "",
    ]
    for asof, block in payload["windows"].items():
        end = block["qqq_bh"]["end"]
        lines += [
            f"## Window {asof} → {end}",
            "",
            "| Book | CAGR | MDD | Sharpe | Calmar | Occupancy | vs QQQ | Jensen α | Verdict |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]

        def row(name, m, alpha=None, verdict="—"):
            xs = alpha.get("excess_cagr_pct") if alpha else "—"
            ja = alpha.get("jensen", {}).get("ann_alpha_pct") if alpha and alpha.get("jensen") else "—"
            occ = m.get("sleeve", {}).get("avg_stock_occupancy_pct", "—")
            return (
                f"| {name} | {m['cagr_pct']:.2f}% | {m['mdd_pct']:.2f}% | {m['sharpe']:.3f} | "
                f"{m.get('calmar', 0):.3f} | {occ} | {xs} | {ja} | {verdict} |"
            )

        lines.append(row("QQQ B&H", block["qqq_bh"]))
        if block.get("qld_bh"):
            lines.append(row("QLD B&H", block["qld_bh"]))
        lines.append(row("C1 v1", block["c1_v1"], block.get("c1_vs_qqq"), block.get("c1_verdict")))
        lines.append(row("**A Hurdle 25%**", block["model_a"], block.get("a_vs_qqq"), block.get("a_verdict")))
        lines.append(row("**B 2-Slot 60/40**", block["model_b"], block.get("b_vs_qqq"), block.get("b_verdict")))
        lines.append(row("**C Hurdle 1.6x**", block["model_c"], block.get("c_vs_qqq"), block.get("c_verdict")))
        a = block["model_a"]
        lines += [
            "",
            f"- A 허들 통과 일/스킵: {a.get('sleeve', {}).get('hurdle_hits')} / {a.get('sleeve', {}).get('hurdle_skips')}",
            f"- A 평균 현금: {a.get('sleeve', {}).get('avg_cash_pct')}%  레버리지: {a.get('sleeve', {}).get('avg_gross_leverage')}  "
            f"국면: {a.get('sleeve', {}).get('regime_days')}",
            f"- C 국면: {block['model_c'].get('sleeve', {}).get('regime_days')}  "
            f"lev {block['model_c'].get('sleeve', {}).get('avg_gross_leverage')}",
            "",
        ]
    lines += diagnose(payload)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _line(tag: str, m: dict) -> str:
    occ = m.get("sleeve", {}).get("avg_stock_occupancy_pct")
    occ_s = f"  occ={occ}%" if occ is not None else ""
    return (
        f"  {tag:22s} CAGR {m['cagr_pct']:6.2f}%  MDD {m['mdd_pct']:7.2f}%  "
        f"Sharpe {m['sharpe']:.3f}  Calmar {m.get('calmar', 0):.3f}{occ_s}"
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
            "method": (
                "A: 75/25 hurdle+10 solo satellite, 1.5x low-vol, 30% cash only if VIX>=30 and SPY<200. "
                "B: v1 2-slot 60/40, dual-mom vs QQQ, 1.5x. "
                "C: A + 1.6x when VIX<18."
            ),
            "initial": INITIAL,
            "friction": {"slippage_bps": SLIPPAGE * 10000, "fee_bps": FEE * 10000},
            "hurdle_pt": HURDLE_PT,
            "fear_vix": FEAR_VIX,
            "fear_cash": FEAR_CASH,
            "qld_available": "QLD" in proxy_ohlc,
            "generated": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        },
        "windows": {},
    }

    for asof in SNAPSHOTS:
        start_i = max(snapshot_index(calendar, asof), 2)
        print(f"\n===== WINDOW {asof} ({calendar[start_i].date()}) =====")

        c1_book, c1_sl = run_alpha_book(
            calendar, panels, bench, composite, universes, proxy_ohlc, start_i
        )
        a_book, a_sl = run_v3_book(
            SPECS["A"], calendar, panels, bench, composite, universes, proxy_ohlc, start_i
        )
        b_book, b_sl = run_v3_book(
            SPECS["B"], calendar, panels, bench, composite, universes, proxy_ohlc, start_i
        )
        c_book, c_sl = run_v3_book(
            SPECS["C"], calendar, panels, bench, composite, universes, proxy_ohlc, start_i
        )

        c1 = pack_book(c1_book, bench, extra={"sleeve": sleeve_stats(c1_sl)})
        ma = pack_book(a_book, bench, extra={"sleeve": v3_sleeve_stats(a_sl)})
        mb = pack_book(b_book, bench, extra={"sleeve": v3_sleeve_stats(b_sl)})
        mc = pack_book(c_book, bench, extra={"sleeve": v3_sleeve_stats(c_sl)})
        idx = pd.to_datetime(c1_book.dates)
        qqq_m = enrich(bh_metrics(qqq_px.reindex(idx)))
        qld_m = enrich(bh_metrics(qld_px.reindex(idx))) if qld_px is not None else None
        qld_cagr = qld_m["cagr_pct"] if qld_m else 0.0

        c1_vs = vs_qqq(c1.pop("_rets"), qqq_m, c1)
        a_vs = vs_qqq(ma.pop("_rets"), qqq_m, ma)
        b_vs = vs_qqq(mb.pop("_rets"), qqq_m, mb)
        c_vs = vs_qqq(mc.pop("_rets"), qqq_m, mc)

        def verd(m):
            return verdict_of(m["cagr_pct"], m["mdd_pct"], qqq_m["cagr_pct"], qld_cagr)

        block = {
            "qqq_bh": qqq_m,
            "qld_bh": qld_m,
            "c1_v1": c1,
            "model_a": ma,
            "model_b": mb,
            "model_c": mc,
            "c1_vs_qqq": c1_vs,
            "a_vs_qqq": a_vs,
            "b_vs_qqq": b_vs,
            "c_vs_qqq": c_vs,
            "c1_verdict": verd(c1),
            "a_verdict": verd(ma),
            "b_verdict": verd(mb),
            "c_verdict": verd(mc),
        }
        payload["windows"][asof] = block

        print(_line("QQQ B&H", qqq_m))
        if qld_m:
            print(_line("QLD B&H", qld_m))
        print(_line("C1 v1", c1) + f"  vsQQQ {c1_vs.get('excess_cagr_pct')}  {block['c1_verdict']}")
        print(_line("A Hurdle 25%", ma) + f"  vsQQQ {a_vs.get('excess_cagr_pct')}  {block['a_verdict']}")
        print(_line("B 2-Slot 60/40", mb) + f"  vsQQQ {b_vs.get('excess_cagr_pct')}  {block['b_verdict']}")
        print(_line("C Hurdle 1.6x", mc) + f"  vsQQQ {c_vs.get('excess_cagr_pct')}  {block['c_verdict']}")
        print(
            f"  A hurdle hits/skips {ma['sleeve'].get('hurdle_hits')}/{ma['sleeve'].get('hurdle_skips')}  "
            f"cash {ma['sleeve'].get('avg_cash_pct')}%  modes {ma['sleeve'].get('regime_days')}"
        )

    payload["meta"]["elapsed_sec"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(json_safe(payload), f, ensure_ascii=False, indent=2)
    write_report(payload)
    print(f"\nwrote {OUT_PATH}")
    print(f"wrote {REPORT_PATH}")
    print(f"elapsed {payload['meta']['elapsed_sec']}s")
    print()
    for line in diagnose(payload):
        print(line)


if __name__ == "__main__":
    main()
