"""
DCA capital-structure sensitivity: Heavy DCA / Heavy Lump-sum / Step-up.

Stress-tests whether C1 v1 vs M2 final-equity and MDD rankings flip when the
seed/contribution mix changes. 8y window only (2018-08-31 → 2026-08-28).

Books per scenario: QQQ DCA, QLD DCA, C1 v1 DCA, M2 DCA.
  C1  bear 25/25, stop -4%, idle QQQ + 1.5x
  M2  C1 structure identical + stop -5.0%  (isolates stop under capital stress)

Friction: next-open, 10bps/8bps, PIT composite-60.
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

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
from compare_html_vs_live_exit import SLIPPAGE, FEE, align_panels  # noqa: E402
from pit_dynamic_universe_exit_ab import stock_names  # noqa: E402
from pit_universe_exit_ab import download_master, snapshot_index  # noqa: E402
from qqq_beat_alpha_backtester import (  # noqa: E402
    BENCH,
    aligned_ohlc,
    build_daily_universes_composite,
    composite_rs,
    ensure_qld,
    json_safe,
)
from c1_v1_tuning import TuneSpec  # noqa: E402
from c1_v1_dca_simulation import (  # noqa: E402
    is_month_first,
    pack_dca,
    resolve_monthly,
    run_bh_dca,
    run_c1_dca,
)

OUT_PATH = os.path.join(PROJECT_ROOT, "data", "backtest_cache", "dca_capital_sensitivity_results.json")
REPORT_PATH = os.path.join(PROJECT_ROOT, "research_and_backtests", "dca_capital_sensitivity_report.md")

ASOF = "2018-08-31"
STEP_UP_CUTOFF = pd.Timestamp("2022-09-01")

SPECS = {
    "C1_v1": TuneSpec("C1_v1"),  # bear 25/25, stop -4%
    "M2": TuneSpec("M2", stop_pct=-0.05),  # C1 structure + stop -5%
}


@dataclass
class Scenario:
    key: str
    name: str
    seed: float
    monthly: Callable[[pd.Timestamp], float]
    expected_invested: float
    seed_share_pct: float
    note: str


def flat_monthly(amount: float) -> Callable[[pd.Timestamp], float]:
    return lambda _ts, a=amount: float(a)


def step_up_monthly(early: float, late: float, cutoff: pd.Timestamp) -> Callable[[pd.Timestamp], float]:
    def fn(ts: pd.Timestamp, e=early, l=late, c=cutoff) -> float:
        return float(e if pd.Timestamp(ts) < c else l)

    return fn


SCENARIOS: List[Scenario] = [
    Scenario(
        key="S1_heavy_dca",
        name="Heavy DCA (소액 시드 / 고적립)",
        seed=3_000.0,
        monthly=flat_monthly(600.0),
        expected_invested=60_600.0,
        seed_share_pct=5.0,
        note="$3,000 + 96×$600",
    ),
    Scenario(
        key="S2_heavy_lump",
        name="Heavy Lump-sum (거액 시드 / 저적립)",
        seed=30_000.0,
        monthly=flat_monthly(300.0),
        expected_invested=58_800.0,
        seed_share_pct=51.0,
        note="$30,000 + 96×$300",
    ),
    Scenario(
        key="S3_step_up",
        name="Step-up DCA (소득 증가)",
        seed=8_000.0,
        monthly=step_up_monthly(300.0, 800.0, STEP_UP_CUTOFF),
        expected_invested=60_800.0,
        seed_share_pct=13.2,
        note="$8,000 + 48×$300 then 48×$800 from 2022-09",
    ),
]


def count_expected(calendar: pd.DatetimeIndex, start_i: int, seed: float, monthly_fn) -> float:
    total = float(seed)
    n_m = 0
    for i in range(start_i + 1, len(calendar)):
        if is_month_first(calendar, i, start_i):
            total += resolve_monthly(monthly_fn, calendar[i])
            n_m += 1
    return total, n_m


def score_row(scen_name: str, book: str, m: dict, c1_m2_gap: Optional[float] = None) -> str:
    xirr = m.get("xirr_pct")
    xirr_s = f"{xirr:.2f}%" if xirr is not None else "—"
    gap_s = f"${c1_m2_gap:,.2f}" if c1_m2_gap is not None else "—"
    return (
        f"| {scen_name} | {book} | ${m['total_invested']:,.0f} | ${m['final_equity']:,.2f} | "
        f"{m['roi_pct']:.2f}% | {xirr_s} | {m['dca_mdd_pct']:.2f}% | {gap_s} |"
    )


def diagnose(payload: dict) -> List[str]:
    lines = ["## 진단 (자본 구조 민감도)", ""]
    flips = []
    for key, block in payload["scenarios"].items():
        c1 = block["C1_v1"]
        m2 = block["M2"]
        qqq = block["QQQ"]
        gap = m2["final_equity"] - c1["final_equity"]
        winner = "M2" if gap > 0 else ("C1" if gap < 0 else "TIE")
        mdd_winner = "C1" if c1["dca_mdd_pct"] > m2["dca_mdd_pct"] else "M2"  # less negative wins
        lines.append(
            f"- **{block['name']}**: 최종 {winner} "
            f"(M2−C1 ${gap:,.2f}) | MDD 우위 {mdd_winner} "
            f"(C1 {c1['dca_mdd_pct']:.2f}% / M2 {m2['dca_mdd_pct']:.2f}%) | "
            f"vs QQQ C1 ${c1['final_equity'] - qqq['final_equity']:,.2f} / "
            f"M2 ${m2['final_equity'] - qqq['final_equity']:,.2f}."
        )
        flips.append((key, winner, mdd_winner, gap))

    equity_winners = {w for _, w, _, _ in flips}
    mdd_winners = {m for _, _, m, _ in flips}
    if len(equity_winners) > 1:
        lines.append(
            "- **최종자산 순위가 시나리오마다 뒤바뀜.** 자본 구조(시드 vs 적립)가 C1/M2 선택을 좌우한다."
        )
    else:
        w = next(iter(equity_winners))
        lines.append(
            f"- **최종자산 순위는 전 시나리오 {w} 고정.** 시드/적립 비율·Step-up으로도 C1↔M2 달러 승자가 안 바뀐다."
        )
    if len(mdd_winners) > 1:
        lines.append("- **MDD 우위는 시나리오별 변동.** 위험 선호도에 따라 선택이 갈린다.")
    else:
        lines.append(
            f"- **MDD 우위는 전 시나리오 {next(iter(mdd_winners))}.** −5% 손절의 낙폭 비용/이득이 자본 구조와 무관하게 일관적"
            + ("이다." if next(iter(mdd_winners)) == "C1" else "이다 (M2가 더 얕음 — 이례).")
        )
    lines += [
        "- M2 정의(본 실험): **C1과 동일 슬롯/레버리지 + 하드스탑만 −5%.** "
        "이전 DCA의 bear 2×40% M2와는 다름 — 본 테스트는 손절 폭의 자본구조 민감도만 분리한다.",
        "- QLD는 달러 천장 후보이나 MDD ≈ −50~−60%대면 실전 채택은 C1/M2 vs QQQ 비교가 핵심.",
        "",
    ]
    return lines


def write_report(payload: dict) -> None:
    meta = payload["meta"]
    lines = [
        "# DCA Capital Sensitivity — Heavy DCA / Lump-sum / Step-up",
        "",
        f"- 생성: {meta.get('generated')}",
        f"- 윈도우: {meta['asof']} → {meta['end']}",
        f"- 마찰: slippage {meta['friction']['slippage_bps']:.0f}bps / fee {meta['friction']['fee_bps']:.0f}bps / 익일 시가",
        "- M2 = C1 동일 구조 + stop −5% (슬롯/레버리지 동일)",
        "",
        "## 시나리오",
        "",
    ]
    for sc in SCENARIOS:
        block = payload["scenarios"][sc.key]
        lines.append(
            f"- **{sc.name}**: {sc.note} → 실측 원금 ${block['QQQ']['total_invested']:,.0f} "
            f"(시드 비중 {sc.seed_share_pct:.1f}%, 월납입 {block['n_monthly']}회)"
        )
    lines += [
        "",
        "## 성적표",
        "",
        "| Scenario | Book | Total Invested | Final Equity | ROI % | XIRR % | DCA MDD | C1 vs M2 gap (M2−C1) |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for sc in SCENARIOS:
        block = payload["scenarios"][sc.key]
        gap = block["M2"]["final_equity"] - block["C1_v1"]["final_equity"]
        for book in ("QQQ", "QLD", "C1_v1", "M2"):
            g = gap if book in ("C1_v1", "M2") else None
            # show gap once on C1 row and M2 row for readability — user asked C1 vs M2 gap
            if book == "C1_v1":
                g = gap
            elif book == "M2":
                g = gap
            else:
                g = None
            bold = "**" if book in ("C1_v1", "M2") else ""
            lines.append(score_row(sc.name if book == "QQQ" else "", f"{bold}{book}{bold}", block[book], g))
        lines.append("| | | | | | | | |")

    # Compact summary table per scenario
    lines += [
        "",
        "## 시나리오별 요약 (C1 vs M2)",
        "",
        "| Scenario | Total Invested | C1 Final | M2 Final | Gap (M2−C1) | C1 ROI | M2 ROI | C1 MDD | M2 MDD | QQQ Final |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for sc in SCENARIOS:
        b = payload["scenarios"][sc.key]
        gap = b["M2"]["final_equity"] - b["C1_v1"]["final_equity"]
        lines.append(
            f"| {sc.name} | ${b['QQQ']['total_invested']:,.0f} | "
            f"${b['C1_v1']['final_equity']:,.2f} | ${b['M2']['final_equity']:,.2f} | "
            f"${gap:,.2f} | {b['C1_v1']['roi_pct']:.2f}% | {b['M2']['roi_pct']:.2f}% | "
            f"{b['C1_v1']['dca_mdd_pct']:.2f}% | {b['M2']['dca_mdd_pct']:.2f}% | "
            f"${b['QQQ']['final_equity']:,.2f} |"
        )
    lines.append("")
    lines += diagnose(payload)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _line(tag: str, m: dict) -> str:
    return (
        f"    {tag:6s} invested ${m['total_invested']:9,.0f}  final ${m['final_equity']:11,.2f}  "
        f"ROI {m['roi_pct']:6.2f}%  XIRR {m.get('xirr_pct')}%  MDD {m['dca_mdd_pct']:7.2f}%"
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
    qqq_ohlc = proxy_ohlc["QQQ"]
    qld_ohlc = proxy_ohlc.get("QLD")

    print("[universe] composite RS …")
    universes = build_daily_universes_composite(calendar, raw)
    composite = {t: composite_rs(p["close"]) for t, p in panels.items()}

    start_i = max(snapshot_index(calendar, ASOF), 2)
    end = calendar[-1].strftime("%Y-%m-%d")
    print(f"[window] {calendar[start_i].date()} → {end}")

    payload = {
        "meta": {
            "asof": ASOF,
            "end": end,
            "friction": {"slippage_bps": SLIPPAGE * 10000, "fee_bps": FEE * 10000},
            "m2_definition": "C1 identical slots/leverage + hard stop -5%",
            "generated": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        },
        "scenarios": {},
    }

    for sc in SCENARIOS:
        actual, n_m = count_expected(calendar, start_i, sc.seed, sc.monthly)
        print(f"\n===== {sc.name} =====")
        print(f"  seed ${sc.seed:,.0f}  expected ${sc.expected_invested:,.0f}  actual≈${actual:,.0f}  months={n_m}")

        qqq_led = run_bh_dca("QQQ", calendar, qqq_ohlc, start_i, seed=sc.seed, monthly=sc.monthly)
        qld_led = (
            run_bh_dca("QLD", calendar, qld_ohlc, start_i, seed=sc.seed, monthly=sc.monthly)
            if qld_ohlc
            else None
        )
        _, _, c1_led = run_c1_dca(
            SPECS["C1_v1"],
            calendar,
            panels,
            bench,
            composite,
            universes,
            proxy_ohlc,
            start_i,
            seed=sc.seed,
            monthly=sc.monthly,
        )
        _, _, m2_led = run_c1_dca(
            SPECS["M2"],
            calendar,
            panels,
            bench,
            composite,
            universes,
            proxy_ohlc,
            start_i,
            seed=sc.seed,
            monthly=sc.monthly,
        )

        qqq_m = pack_dca(qqq_led, "QQQ", end)
        qld_m = pack_dca(qld_led, "QLD", end) if qld_led else None
        c1_m = pack_dca(c1_led, "C1_v1", end)
        m2_m = pack_dca(m2_led, "M2", end)
        gap = m2_m["final_equity"] - c1_m["final_equity"]

        block = {
            "name": sc.name,
            "note": sc.note,
            "seed": sc.seed,
            "seed_share_pct": sc.seed_share_pct,
            "expected_invested": sc.expected_invested,
            "n_monthly": n_m,
            "QQQ": qqq_m,
            "QLD": qld_m,
            "C1_v1": c1_m,
            "M2": m2_m,
            "m2_minus_c1": round(gap, 2),
        }
        payload["scenarios"][sc.key] = block

        for tag, m in (("QQQ", qqq_m), ("QLD", qld_m), ("C1", c1_m), ("M2", m2_m)):
            if m:
                print(_line(tag, m))
        print(f"  M2 − C1 = ${gap:,.2f}")

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
