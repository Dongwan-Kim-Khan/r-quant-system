# S&P 500 Leverage/Inverse Timing vs C1 v1
- 생성: 2026-09-08 13:40 UTC
- 마찰: slippage 10bps / fee 8bps / 익일 시가
- ETF 가용: {'SPY': True, 'SSO': True, 'UPRO': True, 'SH': True, 'BIL': True}
## 전략
- **T1** — SPY≥SMA200 → SSO 100%; else BIL 100%.
- **T2** — bull+VIX<18 → UPRO; bull+VIX<24 → SSO; bull+VIX≥24 → SPY; bear → BIL.
- **T3** — SPY 60d mom ≥0 → SSO; else SH (−1x).
- **C1 v1** — 개별 주도주 50/30/20 + 유휴 QQQ + 1.5x (참조 챔피언).
## Window 2018-08-31 → 2026-08-28
| Book | CAGR | MDD | Sharpe | Calmar | Trades | Trades/yr | vs SPY | vs QQQ | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SPY B&H | 13.01% | -34.10% | 0.727 | 0.382 | — | — | — | — | — |
| QQQ B&H | 18.39% | -35.62% | 0.819 | 0.516 | — | — | — | — | — |
| SSO B&H | 20.68% | -59.34% | 0.681 | 0.349 | — | — | — | — | — |
| C1 v1 | 18.47% | -41.71% | 0.725 | 0.443 | 325 | 40.8 | 5.46 | 0.08 | BASELINE |
| **T1** | 10.65% | -43.61% | 0.537 | 0.244 | 110 | 13.8 | -2.36 | -7.74 | LOSE_C1 |
| **T2** | -0.11% | -48.45% | 0.134 | -0.002 | 512 | 64.3 | -13.12 | -18.5 | LOSE_C1 |
| **T3** | 9.11% | -45.09% | 0.452 | 0.202 | 150 | 18.8 | -3.9 | -9.28 | LOSE_C1 |
- T1 hold share: {'SSO': 78.3, 'BIL': 21.7}  regime {'bull_sso': 1572, 'bear_cash': 436}
- T2 hold share: {'UPRO': 48.4, 'SSO': 22.0, 'BIL': 21.7, 'SPY': 7.9}  regime {'bull_upro': 972, 'bull_sso': 442, 'bear_cash': 436, 'bull_spy': 158}
- T3 hold share: {'SSO': 74.3, 'SH': 25.7}  regime {'mom_long': 1491, 'mom_short': 517}
## Window 2023-08-31 → 2026-08-28
| Book | CAGR | MDD | Sharpe | Calmar | Trades | Trades/yr | vs SPY | vs QQQ | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SPY B&H | 19.69% | -19.00% | 1.249 | 1.036 | — | — | — | — | — |
| QQQ B&H | 23.93% | -22.88% | 1.152 | 1.046 | — | — | — | — | — |
| SSO B&H | 33.95% | -35.34% | 1.128 | 0.961 | — | — | — | — | — |
| C1 v1 | 31.81% | -26.96% | 1.043 | 1.180 | 129 | 43.3 | 12.12 | 7.88 | BASELINE |
| **T1** | 22.63% | -21.94% | 1.002 | 1.031 | 22 | 7.4 | 2.94 | -1.3 | LOSE_C1 |
| **T2** | 11.90% | -29.92% | 0.532 | 0.398 | 196 | 65.8 | -7.79 | -12.03 | LOSE_C1 |
| **T3** | 24.09% | -17.84% | 1.039 | 1.350 | 30 | 10.1 | 4.4 | 0.16 | LOSE_C1 |
- T1 hold share: {'SSO': 91.6, 'BIL': 8.4}  regime {'bull_sso': 688, 'bear_cash': 63}
- T2 hold share: {'UPRO': 67.5, 'SSO': 22.0, 'BIL': 8.4, 'SPY': 2.1}  regime {'bull_upro': 507, 'bull_sso': 165, 'bear_cash': 63, 'bull_spy': 16}
- T3 hold share: {'SSO': 82.6, 'SH': 17.4}  regime {'mom_long': 620, 'mom_short': 131}
## 진단

- **8년 챔피언: C1** CAGR 18.47% / MDD -41.71% / Sharpe 0.725.
- **C1 v1** 8년 18.47% / MDD -41.71% / Sharpe 0.725 | AI 3년 31.81% (vs QQQ 7.88%p).
- **T1** 8년 10.65% / MDD -43.61% / Sharpe 0.537 / trades/yr 13.8 / vs C1 CAGR -7.82%p / 국면 {'bull_sso': 1572, 'bear_cash': 436}. AI3y 22.63%.
- **T2** 8년 -0.11% / MDD -48.45% / Sharpe 0.134 / trades/yr 64.3 / vs C1 CAGR -18.58%p / 국면 {'bull_upro': 972, 'bull_sso': 442, 'bear_cash': 436, 'bull_spy': 158}. AI3y 11.90%.
- **T3** 8년 9.11% / MDD -45.09% / Sharpe 0.452 / trades/yr 18.8 / vs C1 CAGR -9.36%p / 국면 {'mom_long': 1491, 'mom_short': 517}. AI3y 24.09%.
- **SSO B&H** 8년 20.68% / MDD -59.34% — 타이밍 없이 2x를 들고만 있어도 C1 CAGR(18.47%)을 이긴다. 다만 MDD -59.34%는 C1 -41.71%보다 깊다.
- AI 3년 리스크 측면 우위: T1, T3 (예: T3 MDD -17.84% / Sharpe 1.039 vs C1 -26.96% / 1.043). CAGR은 여전히 C1이 앞선다.
- **T2 실패 원인:** bull+VIX<18에서 UPRO 상시 노출(972일) — 3x 변동성 부패 + SMA200 래그 현금화가 V 반등을 놓쳐 8년 CAGR ≈ 0%.
- **순수 S&P 타이밍 3종 모두 8년 CAGR에서 C1을 못 이김.** C1의 개별 주도주 알파(+유휴 QQQ/1.5x)가 단순 레버리지 스위치보다 우월하다. SSO B&H식 상시 2x는 CAGR만 보면 매력적이나 MDD −59%를 감당해야 한다.

