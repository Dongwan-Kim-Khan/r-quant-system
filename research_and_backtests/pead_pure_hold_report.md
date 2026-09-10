# PEAD Pure-Hold Residual Test

- 생성: 2026-09-08 13:24 UTC
- 마찰: slippage 10bps / fee 8bps / 익일 시가
- Pure hard stop: -8.0%  | PEAD fill-days: 496
- Verdict flag: **KILL_PEAD_PERMANENT**

## 모델

- **C1 v1** — 3슬롯 50/30/20 + 유휴 QQQ + 1.5x (챔피언).
- **A2_SMA20** — 토너먼트 원본 (SMA20 / 60d / pre-earnings).
- **A2_PURE60** — SMA20 삭제, 60거래일 OR 다음 실적 직전, −8% 하드스탑만.

## Window 2018-08-31 → 2026-08-28

| Book | CAGR | MDD | Sharpe | Calmar | Trades | Avg bars | Win% | PF | vs QQQ | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| QQQ B&H | 18.39% | -35.62% | 0.819 | 0.516 | — | — | — | — | — | — |
| C1 v1 | 18.47% | -41.71% | 0.725 | 0.443 | 325 | 17.2 | 24.0 | 1.489 | 0.08 | BEAT_QQQ |
| **A2_SMA20** | 11.29% | -35.17% | 0.552 | 0.321 | 321 | 13.4 | 38.63 | 1.379 | -7.1 | LOSE_QQQ |
| **A2_PURE60** | 12.33% | -36.26% | 0.613 | 0.340 | 174 | 37.3 | 43.68 | 1.611 | -6.06 | LOSE_QQQ |

- SMA20 reasons: {'SMA20': {'n': 316, 'avg_pnl': 0.563, 'share_pct': 98.4}, 'PRE_EARNINGS': {'n': 3, 'avg_pnl': 41.168, 'share_pct': 0.9}, 'EOB': {'n': 2, 'avg_pnl': 0.234, 'share_pct': 0.6}}
- PURE60 reasons: {'HARD_STOP': {'n': 87, 'avg_pnl': -8.33, 'share_pct': 50.0}, 'PRE_EARNINGS': {'n': 68, 'avg_pnl': 13.949, 'share_pct': 39.1}, 'TIME_60D': {'n': 17, 'avg_pnl': 13.869, 'share_pct': 9.8}, 'EOB': {'n': 2, 'avg_pnl': 1.611, 'share_pct': 1.1}}
- PURE60 occupancy 80.73%

## Window 2023-08-31 → 2026-08-28

| Book | CAGR | MDD | Sharpe | Calmar | Trades | Avg bars | Win% | PF | vs QQQ | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| QQQ B&H | 23.93% | -22.88% | 1.152 | 1.046 | — | — | — | — | — | — |
| C1 v1 | 31.81% | -26.96% | 1.043 | 1.180 | 129 | 16.9 | 25.58 | 1.76 | 7.88 | BEAT_QQQ |
| **A2_SMA20** | 21.42% | -22.91% | 0.944 | 0.935 | 115 | 14.1 | 39.13 | 1.923 | -2.51 | LOSE_QQQ |
| **A2_PURE60** | 23.96% | -29.79% | 1.070 | 0.804 | 64 | 40.0 | 56.25 | 2.578 | 0.03 | BEAT_QQQ |

- SMA20 reasons: {'SMA20': {'n': 112, 'avg_pnl': 1.541, 'share_pct': 97.4}, 'PRE_EARNINGS': {'n': 1, 'avg_pnl': 52.232, 'share_pct': 0.9}, 'EOB': {'n': 2, 'avg_pnl': 0.234, 'share_pct': 1.7}}
- PURE60 reasons: {'HARD_STOP': {'n': 23, 'avg_pnl': -8.256, 'share_pct': 35.9}, 'PRE_EARNINGS': {'n': 32, 'avg_pnl': 13.383, 'share_pct': 50.0}, 'TIME_60D': {'n': 7, 'avg_pnl': 10.909, 'share_pct': 10.9}, 'EOB': {'n': 2, 'avg_pnl': 1.611, 'share_pct': 3.1}}
- PURE60 occupancy 84.91%

## 잔여 가설 판정

- A2_SMA20 8년 11.29% / hold 13.4d / SMA20 share 98.4%.
- A2_PURE60 8년 12.33% / MDD -36.26% / hold 37.3d / win 43.68% / vs C1 -6.14%p.
- A2_PURE60 AI3y 23.96% vs C1 31.81% (-7.85%p).
- **판정: PEAD 연구 영구 종료.** Pure 60D Hold가 C1 v1을 어느 윈도우에서도 이기지 못했다. 슬리브 편입 배제. SMA20이 범인이었던 것이 아니라, +15% surprise 갭-안착 진입 자체에 C1급 알파가 없다.
- 다음 연구 예산은 C1 v1 고도화(엔트리 필터 / 약세 슬롯 / 동적 트레일)에만 쓴다.

