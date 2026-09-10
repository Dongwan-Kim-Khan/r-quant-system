"""
PEAD Pure-Hold residual hypothesis test.

Tournament A2 failed because SMA20 cut 98% of trades at ~13 bars, killing the
60-day PEAD thesis. This script isolates that contradiction:

  A2_SMA20   original tournament exit (SMA20 / 60d / pre-earnings)
  A2_PURE60  SMA20 OFF; exit only at 60 bars OR pre-next-earnings;
             emergency hard stop -8.0% only
  C1_v1      swing champion baseline

If Pure60 is still inferior to C1 on both windows, PEAD research is closed
and the sleeve is permanently excluded from the book.
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
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
    align_panels,
    close_position,
)
from pit_dynamic_universe_exit_ab import stock_names  # noqa: E402
from pit_universe_exit_ab import download_master, snapshot_index  # noqa: E402
from qqq_beat_alpha_backtester import (  # noqa: E402
    BENCH,
    SNAPSHOTS,
    ProxyPos,
    aligned_ohlc,
    build_daily_universes_composite,
    composite_rs,
    enrich,
    ensure_qld,
    json_safe,
    pack_book,
    proxy_mix,
    run_alpha_book,
    sleeve_stats,
    vs_qqq,
)
from regime_bull_core import bh_metrics  # noqa: E402
from triple_alpha_tournament import (  # noqa: E402
    OverlayStats,
    PEAD_HOLD_BARS,
    PEAD_SLOT_W,
    PEAD_SLOTS,
    _make_proxy_helpers,
    build_extras,
    build_pead_entries,
    load_earnings,
    overlay_pack,
    pack_alpha,
)

OUT_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "pead_pure_hold_results.json")
REPORT_PATH = os.path.join(PROJECT_ROOT, "research_and_backtests", "pead_pure_hold_report.md")
PURE_HARD_STOP = -0.08  # -8.0% emergency only


def run_pead_variant(
    calendar: pd.DatetimeIndex,
    panels: Dict[str, dict],
    extras: Dict[str, dict],
    fills: Dict[int, List[Tuple[float, str, int]]],
    next_earn: Dict[str, List[int]],
    proxy_ohlc: Dict[str, dict],
    start_i: int,
    *,
    use_sma20: bool,
    hard_stop: Optional[float],
) -> Tuple[Book, OverlayStats]:
    n = len(calendar)
    dates = [d.strftime("%Y-%m-%d") for d in calendar]
    book = Book(cash=INITIAL)
    stats = OverlayStats()
    sl_proxy: Dict[str, ProxyPos] = {}
    H = _make_proxy_helpers(book, sl_proxy, proxy_ohlc, panels, stats)
    start_i = max(20, int(start_i))
    consumed: Set[Tuple[str, int]] = set()

    def next_report(tk: str, i: int) -> Optional[int]:
        for si in next_earn.get(tk) or []:
            if si > i:
                return si
        return None

    for i in range(start_i, n):
        for tk in list(book.positions.keys()):
            pos = book.positions[tk]
            p = panels[tk]
            ex = extras.get(tk)
            if not p["listed"][i]:
                continue
            high = p["high"][i]
            low = p["low"][i]
            opn = p["open"][i]
            if np.isfinite(high):
                pos.peak_high = max(pos.peak_high, high)
            reason = None
            raw = None
            if hard_stop is not None:
                hard = pos.entry * (1.0 + hard_stop)
                if np.isfinite(low) and low <= hard:
                    reason = "HARD_STOP"
                    if np.isfinite(opn) and opn > 0 and opn < hard:
                        raw = float(opn)
                    else:
                        raw = hard
            nxt = next_report(tk, i)
            if reason is None and nxt is not None and nxt - i <= 2:
                reason = "PRE_EARNINGS"
                raw = p["close"][i]
            if reason is None and (i - pos.entry_i) >= PEAD_HOLD_BARS:
                reason = "TIME_60D"
                raw = p["close"][i]
            if (
                reason is None
                and use_sma20
                and ex is not None
                and np.isfinite(ex["sma20"][i])
                and np.isfinite(p["close"][i])
                and p["close"][i] < ex["sma20"][i]
            ):
                reason = "SMA20"
                raw = p["close"][i]
            if reason and np.isfinite(raw) and raw > 0:
                close_position(book, pos, float(raw), dates[i], reason, i)

        cands = fills.get(i) or []
        if cands and len(book.positions) < PEAD_SLOTS:
            stats.signal_days += 1
            ranked = sorted(cands, reverse=True)
            n_free = PEAD_SLOTS - len(book.positions)
            needed = H["equity_now"](i, "open") * PEAD_SLOT_W * n_free
            if needed > book.cash:
                H["free_cash"](i, needed)
            for surprise, tk, ev_si in ranked:
                if len(book.positions) >= PEAD_SLOTS:
                    break
                if tk in book.positions or (tk, ev_si) in consumed:
                    continue
                if tk not in panels or not panels[tk]["listed"][i]:
                    continue
                nxt = next_report(tk, i)
                if nxt is not None and nxt - i <= 5:
                    continue
                if H["buy_stock"](tk, i, PEAD_SLOT_W):
                    book.positions[tk].entry_date = dates[i]
                    consumed.add((tk, ev_si))

        eq = H["equity_now"](i, "close")
        stock_w = H["stock_mark"](i, "close") / eq if eq > 0 else 0.0
        qqq_w, qld_w = proxy_mix(stock_w, 1.0, False)
        H["rebalance_proxy"](i, "close", qqq_w, qld_w)
        eq = H["equity_now"](i, "close")
        book.equity.append(eq)
        book.dates.append(dates[i])
        if eq > 0:
            stats.occupancy.append(H["stock_mark"](i, "close") / eq)
            stats.cash_frac.append(book.cash / eq)
            stats.leverage.append((H["stock_mark"](i, "close") + H["proxy_mark"](i, "close")) / eq)

    last_i = n - 1
    for tk in list(book.positions.keys()):
        pos = book.positions[tk]
        px = panels[tk]["close"][last_i]
        if not np.isfinite(px) or px <= 0:
            px = pos.entry
        close_position(book, pos, float(px), dates[last_i], "EOB", last_i)
    for tk in list(sl_proxy.keys()):
        H["sell_proxy"](tk, last_i, "close")
    return book, stats


def score_row(name: str, m: dict, alpha=None, verdict="—") -> str:
    xs = alpha.get("excess_cagr_pct") if alpha else "—"
    t = m.get("trades") or {}
    return (
        f"| {name} | {m['cagr_pct']:.2f}% | {m['mdd_pct']:.2f}% | {m['sharpe']:.3f} | "
        f"{m.get('calmar', 0):.3f} | {t.get('n', '—')} | {t.get('avg_bars', '—')} | "
        f"{t.get('win_rate', '—')} | {t.get('profit_factor', '—')} | {xs} | {verdict} |"
    )


def diagnose(payload: dict) -> List[str]:
    w8 = payload["windows"]["2018-08-31"]
    w3 = payload["windows"].get("2023-08-31")
    pure8 = w8["a2_pure60"]
    c18 = w8["c1_v1"]
    sma8 = w8["a2_sma20"]
    beat_c1_8 = pure8["cagr_pct"] > c18["cagr_pct"]
    beat_c1_3 = True
    if w3:
        beat_c1_3 = w3["a2_pure60"]["cagr_pct"] > w3["c1_v1"]["cagr_pct"]
    kill = not (beat_c1_8 or beat_c1_3)
    lines = [
        "## 잔여 가설 판정",
        "",
        f"- A2_SMA20 8년 {sma8['cagr_pct']:.2f}% / hold {sma8.get('trades', {}).get('avg_bars')}d / "
        f"SMA20 share {((sma8.get('trades') or {}).get('reasons') or {}).get('SMA20', {}).get('share_pct', '?')}%.",
        f"- A2_PURE60 8년 {pure8['cagr_pct']:.2f}% / MDD {pure8['mdd_pct']:.2f}% / hold {pure8.get('trades', {}).get('avg_bars')}d / "
        f"win {pure8.get('trades', {}).get('win_rate')}% / vs C1 {pure8['cagr_pct'] - c18['cagr_pct']:+.2f}%p.",
    ]
    if w3:
        lines.append(
            f"- A2_PURE60 AI3y {w3['a2_pure60']['cagr_pct']:.2f}% vs C1 {w3['c1_v1']['cagr_pct']:.2f}% "
            f"({w3['a2_pure60']['cagr_pct'] - w3['c1_v1']['cagr_pct']:+.2f}%p)."
        )
    if kill:
        lines += [
            "- **판정: PEAD 연구 영구 종료.** Pure 60D Hold가 C1 v1을 어느 윈도우에서도 이기지 못했다. "
            "슬리브 편입 배제. SMA20이 범인이었던 것이 아니라, +15% surprise 갭-안착 진입 자체에 C1급 알파가 없다.",
            "- 다음 연구 예산은 C1 v1 고도화(엔트리 필터 / 약세 슬롯 / 동적 트레일)에만 쓴다.",
        ]
    else:
        winner = "8y" if beat_c1_8 else "AI3y"
        lines += [
            f"- **판정: Pure 60D가 C1을 {winner}에서 이김.** PEAD 슬리브 후보로 유지하되 C1 코어와 상관을 별도 측정한다.",
        ]
    lines.append("")
    return lines


def write_report(payload: dict) -> None:
    meta = payload["meta"]
    lines = [
        "# PEAD Pure-Hold Residual Test",
        "",
        f"- 생성: {meta.get('generated')}",
        f"- 마찰: slippage {meta['friction']['slippage_bps']:.0f}bps / fee {meta['friction']['fee_bps']:.0f}bps / 익일 시가",
        f"- Pure hard stop: {PURE_HARD_STOP * 100:.1f}%  | PEAD fill-days: {meta.get('pead_fill_days')}",
        f"- Verdict flag: **{meta.get('verdict')}**",
        "",
        "## 모델",
        "",
        "- **C1 v1** — 3슬롯 50/30/20 + 유휴 QQQ + 1.5x (챔피언).",
        "- **A2_SMA20** — 토너먼트 원본 (SMA20 / 60d / pre-earnings).",
        "- **A2_PURE60** — SMA20 삭제, 60거래일 OR 다음 실적 직전, −8% 하드스탑만.",
        "",
    ]
    header = (
        "| Book | CAGR | MDD | Sharpe | Calmar | Trades | Avg bars | Win% | PF | vs QQQ | Verdict |"
    )
    sep = "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"
    for asof, block in payload["windows"].items():
        lines += [
            f"## Window {asof} → {block['qqq_bh']['end']}",
            "",
            header,
            sep,
            score_row("QQQ B&H", block["qqq_bh"]),
            score_row("C1 v1", block["c1_v1"], block.get("c1_vs_qqq"), block.get("c1_verdict")),
            score_row("**A2_SMA20**", block["a2_sma20"], block.get("sma_vs_qqq"), block.get("sma_verdict")),
            score_row("**A2_PURE60**", block["a2_pure60"], block.get("pure_vs_qqq"), block.get("pure_verdict")),
            "",
            f"- SMA20 reasons: {block['a2_sma20'].get('trades', {}).get('reasons')}",
            f"- PURE60 reasons: {block['a2_pure60'].get('trades', {}).get('reasons')}",
            f"- PURE60 occupancy {block['a2_pure60'].get('sleeve', {}).get('avg_stock_occupancy_pct')}%",
            "",
        ]
    lines += diagnose(payload)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _line(tag: str, m: dict) -> str:
    t = m.get("trades") or {}
    return (
        f"  {tag:14s} CAGR {m['cagr_pct']:6.2f}%  MDD {m['mdd_pct']:7.2f}%  "
        f"n={t.get('n')}  hold={t.get('avg_bars')}  win={t.get('win_rate')}  pf={t.get('profit_factor')}"
    )


def main() -> None:
    t0 = time.time()
    raw = download_master()
    raw = ensure_qld(raw)
    names = [t for t in stock_names() if t in raw and "." not in t]
    sliced = {k: v for k, v in raw.items() if k in set(names) | set(BENCH)}
    calendar, panels, bench = align_panels(sliced)
    print(f"[panel] {len(panels)} names, {len(calendar)} days")

    proxy_ohlc = {"QQQ": aligned_ohlc(raw, "QQQ", calendar)}
    if "QLD" in raw:
        proxy_ohlc["QLD"] = aligned_ohlc(raw, "QLD", calendar)

    print("[universe] composite RS …")
    universes = build_daily_universes_composite(calendar, raw)
    composite = {t: composite_rs(p["close"]) for t, p in panels.items()}
    extras = build_extras(raw, panels, calendar)
    earnings = load_earnings(names)
    pead_fills, next_earn = build_pead_entries(calendar, panels, earnings)

    qqq_px = bench["qqq"].copy()
    qqq_px.index = pd.to_datetime(qqq_px.index)

    payload = {
        "meta": {
            "method": "A2_SMA20 vs A2_PURE60 (-8% hard / 60d / pre-earnings) vs C1 v1",
            "friction": {"slippage_bps": SLIPPAGE * 10000, "fee_bps": FEE * 10000},
            "pure_hard_stop_pct": PURE_HARD_STOP * 100.0,
            "pead_fill_days": len(pead_fills),
            "generated": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        },
        "windows": {},
    }

    for asof in SNAPSHOTS:
        start_i = max(snapshot_index(calendar, asof), 20)
        print(f"\n===== WINDOW {asof} ({calendar[start_i].date()}) =====")

        c1_book, c1_sl = run_alpha_book(
            calendar, panels, bench, composite, universes, proxy_ohlc, start_i
        )
        sma_book, sma_st = run_pead_variant(
            calendar, panels, extras, pead_fills, next_earn, proxy_ohlc, start_i,
            use_sma20=True, hard_stop=None,
        )
        pure_book, pure_st = run_pead_variant(
            calendar, panels, extras, pead_fills, next_earn, proxy_ohlc, start_i,
            use_sma20=False, hard_stop=PURE_HARD_STOP,
        )

        c1 = pack_alpha(c1_book, bench, extra={"sleeve": sleeve_stats(c1_sl)})
        sma = pack_alpha(sma_book, bench, extra={"sleeve": overlay_pack(sma_st)})
        pure = pack_alpha(pure_book, bench, extra={"sleeve": overlay_pack(pure_st)})
        idx = pd.to_datetime(c1_book.dates)
        qqq_m = enrich(bh_metrics(qqq_px.reindex(idx)))

        c1_vs = vs_qqq(c1.pop("_rets"), qqq_m, c1)
        sma_vs = vs_qqq(sma.pop("_rets"), qqq_m, sma)
        pure_vs = vs_qqq(pure.pop("_rets"), qqq_m, pure)

        def verd(m):
            return "BEAT_QQQ" if m["cagr_pct"] > qqq_m["cagr_pct"] else "LOSE_QQQ"

        block = {
            "qqq_bh": qqq_m,
            "c1_v1": c1,
            "a2_sma20": sma,
            "a2_pure60": pure,
            "c1_vs_qqq": c1_vs,
            "sma_vs_qqq": sma_vs,
            "pure_vs_qqq": pure_vs,
            "c1_verdict": verd(c1),
            "sma_verdict": verd(sma),
            "pure_verdict": verd(pure),
        }
        payload["windows"][asof] = block
        print(_line("QQQ", qqq_m))
        print(_line("C1 v1", c1) + f"  vsQQQ {c1_vs.get('excess_cagr_pct')}")
        print(_line("A2_SMA20", sma) + f"  vsQQQ {sma_vs.get('excess_cagr_pct')}  {sma.get('trades', {}).get('reasons')}")
        print(_line("A2_PURE60", pure) + f"  vsQQQ {pure_vs.get('excess_cagr_pct')}  {pure.get('trades', {}).get('reasons')}")

    w8 = payload["windows"]["2018-08-31"]
    w3 = payload["windows"].get("2023-08-31")
    beat = w8["a2_pure60"]["cagr_pct"] > w8["c1_v1"]["cagr_pct"]
    if w3:
        beat = beat or (w3["a2_pure60"]["cagr_pct"] > w3["c1_v1"]["cagr_pct"])
    payload["meta"]["verdict"] = "KEEP_PEAD_SLEEVE" if beat else "KILL_PEAD_PERMANENT"
    payload["meta"]["elapsed_sec"] = round(time.time() - t0, 1)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(json_safe(payload), f, ensure_ascii=False, indent=2)
    write_report(payload)
    print(f"\nwrote {OUT_PATH}")
    print(f"wrote {REPORT_PATH}")
    print(f"VERDICT: {payload['meta']['verdict']}")
    for line in diagnose(payload):
        print(line)


if __name__ == "__main__":
    main()
