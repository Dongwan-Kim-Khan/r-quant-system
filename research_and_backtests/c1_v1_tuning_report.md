# C1 v1 Precision Tuning — Bear 2-Slot Soft & R:R Sensitivity
- 생성: 2026-09-11 01:29 UTC
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
| Baseline | 18.17% | -45.22% | 0.714 | 0.402 | 343 | 24.2 | 1.663 | 15.3 | -0.22 | LOSE_QQQ |
| E2 | 22.94% | -48.13% | 0.840 | 0.477 | 339 | 27.43 | 1.744 | 14.3 | 4.55 | SWEET_SPOT |
| **M1** | 17.73% | -42.31% | 0.688 | 0.419 | 343 | 24.2 | 1.663 | 15.3 | -0.66 | LOSE_QQQ |
| **M2** | 19.17% | -42.54% | 0.736 | 0.451 | 264 | 30.68 | 1.753 | 20.2 | 0.78 | BEAT_BASE_AI_DRAG |
| **M3** | 19.90% | -41.93% | 0.755 | 0.475 | 359 | 25.63 | 1.709 | 14.6 | 1.51 | SWEET_SPOT |
| **M4** | 28.66% | -32.93% | 0.946 | 0.870 | 244 | 25.0 | 2.503 | 21.9 | 10.27 | SWEET_SPOT |
- M1 regime {'bull_lowvol': 1196, 'bull': 376, 'bear_2slot': 436}  occ 67.17%  hard 254 / trail 86
- M2 hard 177 | M3 trail 96 hold 14.6 | M4 trail 63 hold 21.9
- Reasons M1 {'HARD_STOP': {'n': 254, 'avg_pnl': -4.354, 'share_pct': 74.1}, 'TRAILING': {'n': 86, 'avg_pnl': 21.416, 'share_pct': 25.1}, 'EOB': {'n': 3, 'avg_pnl': 0.644, 'share_pct': 0.9}}
## Window 2023-08-31 → 2026-08-28
| Book | CAGR | MDD | Sharpe | Calmar | Trades | Win% | PF | Avg bars | vs QQQ | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| QQQ B&H | 23.93% | -22.88% | 1.152 | 1.046 | — | — | — | — | — | — |
| QLD B&H | 39.92% | -42.34% | 1.028 | 0.943 | — | — | — | — | — | — |
| Baseline | 43.87% | -27.97% | 1.340 | 1.568 | 125 | 31.2 | 2.357 | 16.5 | 19.94 | KEEP_AI_BELOW_BASE |
| E2 | 57.87% | -27.57% | 1.595 | 2.099 | 129 | 31.01 | 2.524 | 15.5 | 33.94 | SWEET_SPOT |
| **M1** | 39.03% | -27.93% | 1.225 | 1.397 | 125 | 31.2 | 2.357 | 16.5 | 15.1 | KEEP_AI_BELOW_BASE |
| **M2** | 40.10% | -28.50% | 1.244 | 1.407 | 103 | 36.89 | 2.288 | 20.2 | 16.17 | KEEP_AI_BELOW_BASE |
| **M3** | 53.14% | -27.93% | 1.526 | 1.903 | 140 | 30.0 | 2.595 | 14.7 | 29.21 | SWEET_SPOT |
| **M4** | 44.71% | -29.90% | 1.308 | 1.495 | 105 | 26.67 | 2.827 | 19.9 | 20.78 | SWEET_SPOT |
- M1 regime {'bull_lowvol': 611, 'bull': 77, 'bear_2slot': 63}  occ 66.47%  hard 83 / trail 39
- M2 hard 62 | M3 trail 42 hold 14.7 | M4 trail 28 hold 19.9
- Reasons M1 {'HARD_STOP': {'n': 83, 'avg_pnl': -4.503, 'share_pct': 66.4}, 'TRAILING': {'n': 39, 'avg_pnl': 22.711, 'share_pct': 31.2}, 'EOB': {'n': 3, 'avg_pnl': 0.644, 'share_pct': 2.4}}
## 진단 (정밀 튜닝 스윗스팟)

- **8년 챔피언: M4** CAGR 28.66% / MDD -32.93% (Baseline 18.17% / E2 22.94%).
- **M1 Bear 2×40%**: 8년 17.73% (vs Baseline -0.44%p, vs E2 -5.21%p). 1슬롯 공백을 메우는지가 핵심.
- **AI 3년**: Baseline 43.87% | E2 57.87% | M1 39.03% | M2 40.10% | M3 53.14% | M4 44.71%.
- **M2 stop −5%**: 8년 19.17% / win 30.68% / hard 177 (M1 254). 노이즈 회피가 승률·PF를 올리면 채택, giveback만 키우면 기각.
- **M3 trail +12%**: 8년 19.90% / hold 14.6d / trail exits 96. 조기 락이 텐배거를 자르면 AI 알파가 먼저 죽는다.
- **M4 wide +18%/ATR3**: 8년 28.66% / hold 21.9d / MDD -32.93%. 대세 추종 vs giveback·MDD 트레이드오프.
- **스윗스팟 후보: M3, M4** — Baseline 8년을 이기면서 AI 알파를 1%p 이내로 보존.

