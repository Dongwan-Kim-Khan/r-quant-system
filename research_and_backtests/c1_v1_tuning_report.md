# C1 v1 Precision Tuning — Bear 2-Slot Soft & R:R Sensitivity
- 생성: 2026-09-08 13:31 UTC
- 마찰: slippage 10bps / fee 8bps / 익일 시가
- E1 진입 필터: **영구 폐기**. E2 1-슬롯은 참조만.
## 모델
- **Baseline** — C1 v1 (bull 50/30/20, bear 25/25, stop −4%, trail +15%/ATR 2.5).
- **E2** — bear Top-1 @ 25% + force trim (진화 실험 참조).
- **M1** — bear 2×40% (유휴 20% QQQ), 손절/트레일 = C1.
- **M2** — M1 + hard stop −5.0%.
- **M3** — M1 + trail trigger +12%.
- **M4** — M1 + trail +18% / ATR×3.0.
## Window 2018-08-31 → 2026-08-28
| Book | CAGR | MDD | Sharpe | Calmar | Trades | Win% | PF | Avg bars | vs QQQ | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| QQQ B&H | 18.39% | -35.62% | 0.819 | 0.516 | — | — | — | — | — | — |
| QLD B&H | 27.90% | -63.79% | 0.752 | 0.437 | — | — | — | — | — | — |
| Baseline | 18.47% | -41.71% | 0.725 | 0.443 | 325 | 24.0 | 1.489 | 17.2 | 0.08 | KEEP_AI_BELOW_BASE |
| E2 | 10.33% | -44.80% | 0.478 | 0.231 | 358 | 25.7 | 1.202 | 14.3 | -8.06 | LOSE_QQQ |
| **M1** | 15.39% | -46.11% | 0.625 | 0.334 | 325 | 24.0 | 1.489 | 17.2 | -3.0 | LOSE_QQQ |
| **M2** | 22.00% | -51.13% | 0.761 | 0.430 | 286 | 28.32 | 1.825 | 19.6 | 3.61 | BEAT_BASE_AI_DRAG |
| **M3** | 5.69% | -52.39% | 0.336 | 0.109 | 399 | 24.56 | 1.166 | 13.9 | -12.7 | LOSE_QQQ |
| **M4** | 11.76% | -47.93% | 0.522 | 0.245 | 278 | 21.94 | 1.473 | 20.1 | -6.63 | LOSE_QQQ |
- M1 regime {'bull_lowvol': 1196, 'bull': 376, 'bear_2slot': 436}  occ 73.01%  hard 246 / trail 76
- M2 hard 202 | M3 trail 101 hold 13.9 | M4 trail 59 hold 20.1
- Reasons M1 {'HARD_STOP': {'n': 246, 'avg_pnl': -4.916, 'share_pct': 75.7}, 'TRAILING': {'n': 76, 'avg_pnl': 22.452, 'share_pct': 23.4}, 'EOB': {'n': 3, 'avg_pnl': 31.527, 'share_pct': 0.9}}
## Window 2023-08-31 → 2026-08-28
| Book | CAGR | MDD | Sharpe | Calmar | Trades | Win% | PF | Avg bars | vs QQQ | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| QQQ B&H | 23.93% | -22.88% | 1.152 | 1.046 | — | — | — | — | — | — |
| QLD B&H | 39.92% | -42.34% | 1.028 | 0.943 | — | — | — | — | — | — |
| Baseline | 31.81% | -26.96% | 1.043 | 1.180 | 129 | 25.58 | 1.76 | 16.9 | 7.88 | KEEP_AI_BELOW_BASE |
| E2 | 37.32% | -26.30% | 1.143 | 1.419 | 136 | 27.21 | 1.761 | 15.6 | 13.39 | SWEET_SPOT |
| **M1** | 24.90% | -32.31% | 0.867 | 0.771 | 129 | 25.58 | 1.76 | 16.9 | 0.97 | KEEP_AI_BELOW_BASE |
| **M2** | 30.23% | -39.50% | 0.875 | 0.765 | 128 | 30.47 | 1.931 | 17.1 | 6.3 | KEEP_AI_BELOW_BASE |
| **M3** | 20.82% | -43.09% | 0.747 | 0.483 | 168 | 25.6 | 1.396 | 13.0 | -3.11 | LOSE_QQQ |
| **M4** | 23.79% | -33.97% | 0.891 | 0.700 | 106 | 22.64 | 1.717 | 20.6 | -0.14 | LOSE_QQQ |
- M1 regime {'bull_lowvol': 611, 'bull': 77, 'bear_2slot': 63}  occ 68.68%  hard 95 / trail 31
- M2 hard 88 | M3 trail 44 hold 13.0 | M4 trail 21 hold 20.6
- Reasons M1 {'HARD_STOP': {'n': 95, 'avg_pnl': -4.986, 'share_pct': 73.6}, 'TRAILING': {'n': 31, 'avg_pnl': 23.852, 'share_pct': 24.0}, 'EOB': {'n': 3, 'avg_pnl': 31.527, 'share_pct': 2.3}}
## 진단 (정밀 튜닝 스윗스팟)

- **8년 챔피언: M2** CAGR 22.00% / MDD -51.13% (Baseline 18.47% / E2 10.33%).
- **M1 Bear 2×40%**: 8년 15.39% (vs Baseline -3.08%p, vs E2 +5.06%p). 1슬롯 공백을 메우는지가 핵심.
- **AI 3년**: Baseline 31.81% | E2 37.32% | M1 24.90% | M2 30.23% | M3 20.82% | M4 23.79%.
- **M2 stop −5%**: 8년 22.00% / win 28.32% / hard 202 (M1 246). 노이즈 회피가 승률·PF를 올리면 채택, giveback만 키우면 기각.
- **M3 trail +12%**: 8년 5.69% / hold 13.9d / trail exits 101. 조기 락이 텐배거를 자르면 AI 알파가 먼저 죽는다.
- **M4 wide +18%/ATR3**: 8년 11.76% / hold 20.1d / MDD -47.93%. 대세 추종 vs giveback·MDD 트레이드오프.
- **CAGR 22%는 M2가 달성** (22.00%)하나 MDD -51.13%로 Baseline -41.71%보다 깊다. AI 3년 30.23% (Baseline 대비 -1.58%p). 손절 완화는 승률·PF를 올리지만 크래시 베타를 키운다 — 채택 시 MDD 예산 재정의 필요.
- **스윗스팟(8년↑ + AI 보존 + MDD 비악화)은 미결.** Baseline C1을 기본 운용으로 두고, M2는 ‘CAGR 우선 변형’으로만 기록한다.

