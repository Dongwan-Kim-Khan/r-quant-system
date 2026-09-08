# Triple-Alpha Tournament — Weekly / PEAD / MSI-C1

- 생성: 2026-09-08 13:15 UTC
- 마찰: slippage 10bps / fee 8bps / 익일 시가 체결
- PIT 유니버스: composite RS Dynamic 60. QLD=yes
- PEAD 이벤트: surprise>+15% & (rev YoY>0 when Yahoo has the quarter; older prints are surprise+gap only), settled 496 fill-days
- MSI 8y 국면: {'green': 1742, 'yellow': 665, 'red': 20}
- NLP: 8년 YouTube PIT 아카이브 없음 → HYG−IEF 20d 크레딧을 `evaluate_macro_stance` defense/buy 토큰으로 투입

## 엔진

- **C1 v1** — 3슬롯 50/30/20 + 유휴 QQQ + SPY≥200 & VIX<20 → 1.5x. 스윙 챔피언 베이스라인.
- **A1 Weekly** — 2음봉 후 SMA5 상향 + RSI(14) 40–55. +4%/−2.5%, 3거래일 또는 금요일 종가. 유휴는 현금.
- **A2 PEAD** — 실적 갭 3–5일 안착 후 진입, 60거래일 또는 SMA20 이탈, 다음 실적 2일 전 청산. 유휴 QQQ.
- **A3 MSI-C1** — MSI<35 C1 풀가동 / 35–65 QQQ 100% (레버리지 OFF, 주식 청산) / ≥65 현금 50%.

## Window 2018-08-31 → 2026-08-28

| Book | CAGR | MDD | Sharpe | Calmar | Trades | Trades/yr | Avg bars | Win% | vs QQQ | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| QQQ B&H | 18.39% | -35.62% | 0.819 | 0.516 | — | — | — | — | — | — |
| QLD B&H | 27.90% | -63.79% | 0.752 | 0.437 | — | — | — | — | — | — |
| C1 v1 | 18.47% | -41.71% | 0.725 | 0.443 | 325 | 40.8 | 17.2 | 24.0 | 0.08 | BEAT_QQQ_MDD_HEAVY |
| **A1 Weekly** | -8.62% | -53.42% | -0.763 | -0.161 | 1157 | 145.2 | 1.6 | 46.33 | -27.01 | LOSE_QQQ |
| **A2 PEAD** | 11.29% | -35.17% | 0.552 | 0.321 | 321 | 40.3 | 13.4 | 38.63 | -7.1 | LOSE_QQQ |
| **A3 MSI-C1** | 2.22% | -48.18% | 0.227 | 0.046 | 433 | 54.3 | 8.8 | 30.48 | -16.17 | LOSE_QQQ |

- A1 occupancy 18.55%  cash 81.45%  signal-days 618  reasons {'HARD_STOP': {'n': 475, 'avg_pnl': -3.349, 'share_pct': 41.1}, 'FRIDAY': {'n': 268, 'avg_pnl': 0.458, 'share_pct': 23.2}, 'TIME_3D': {'n': 172, 'avg_pnl': 0.802, 'share_pct': 14.9}, 'TAKE_PROFIT': {'n': 242, 'avg_pnl': 4.481, 'share_pct': 20.9}}
- A2 occupancy 53.81%  reasons {'SMA20': {'n': 316, 'avg_pnl': 0.563, 'share_pct': 98.4}, 'PRE_EARNINGS': {'n': 3, 'avg_pnl': 41.168, 'share_pct': 0.9}, 'EOB': {'n': 2, 'avg_pnl': 0.234, 'share_pct': 0.6}}
- A3 occupancy 46.0%  cash 0.79%  MSI days {'green': 1324, 'yellow': 664, 'red': 20}  lev 1.105

## Window 2023-08-31 → 2026-08-28

| Book | CAGR | MDD | Sharpe | Calmar | Trades | Trades/yr | Avg bars | Win% | vs QQQ | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| QQQ B&H | 23.93% | -22.88% | 1.152 | 1.046 | — | — | — | — | — | — |
| QLD B&H | 39.92% | -42.34% | 1.028 | 0.943 | — | — | — | — | — | — |
| C1 v1 | 31.81% | -26.96% | 1.043 | 1.180 | 129 | 43.3 | 16.9 | 25.58 | 7.88 | BEAT_QQQ_MDD_OK |
| **A1 Weekly** | -16.37% | -47.67% | -1.421 | -0.343 | 446 | 149.7 | 1.6 | 42.6 | -40.3 | LOSE_QQQ |
| **A2 PEAD** | 21.42% | -22.91% | 0.944 | 0.935 | 115 | 38.6 | 14.1 | 39.13 | -2.51 | LOSE_QQQ |
| **A3 MSI-C1** | 12.45% | -33.45% | 0.528 | 0.372 | 176 | 59.1 | 5.5 | 32.39 | -11.48 | LOSE_QQQ |

- A1 occupancy 19.25%  cash 80.75%  signal-days 254  reasons {'HARD_STOP': {'n': 198, 'avg_pnl': -3.714, 'share_pct': 44.4}, 'TIME_3D': {'n': 68, 'avg_pnl': 0.512, 'share_pct': 15.2}, 'TAKE_PROFIT': {'n': 103, 'avg_pnl': 4.533, 'share_pct': 23.1}, 'FRIDAY': {'n': 77, 'avg_pnl': 0.171, 'share_pct': 17.3}}
- A2 occupancy 54.68%  reasons {'SMA20': {'n': 112, 'avg_pnl': 1.541, 'share_pct': 97.4}, 'PRE_EARNINGS': {'n': 1, 'avg_pnl': 52.232, 'share_pct': 0.9}, 'EOB': {'n': 2, 'avg_pnl': 0.234, 'share_pct': 1.7}}
- A3 occupancy 34.75%  cash 1.38%  MSI days {'green': 324, 'yellow': 411, 'red': 16}  lev 1.054

## 종합 평가 (스윗스팟)

- **8년 챔피언: C1 v1** CAGR 18.47% / MDD -41.71% (QQQ 18.39% / -35.62%).
- **C1 v1** 8년 18.47% (vs QQQ 0.08%p) / MDD -41.71%. AI 3년 31.81% (vs QQQ 7.88%p).
- **A1 위클리** 8년 -8.62% / MDD -53.42% / 승률 46.33% / 연 145.2회 / 평균보유 1.6일. 현금 유휴(QQQ 파킹 없음)가 회전율 전략의 본전이다. 승률 55–65%와 연 80–120회가 동시에 안 나오면 단기 스윙 허들은 미달.
- **A2 PEAD** 8년 11.29% / MDD -35.17% / 거래 321건. 유휴는 QQQ 파킹. Yahoo 분기 손익은 ~5분기만 제공되어 2018–22는 surprise+갭만 적용. SMA20이 거래의 98.4%를 평균 0.563%에 절단; 다음 실적 직전 청산은 3건에 평균 41.168% — 60일 드리프트는 SMA20이 막는다.
- **A3 MSI 게이트** 8년 2.22% / MDD -48.18% (목표 MDD −15% 미달). 국면 {'green': 1324, 'yellow': 664, 'red': 20}. Yellow에서 주도주를 QQQ로 접으면 2020/2023 V자 복리를 다시 잘라 C1보다 CAGR이 낮아질 수 있다.
- **해석:** 세 알파가 C1을 8년과 AI 3년 모두에서 이기지 못하면, 단기 회전·PEAD·매크로 현금 게이트는 **C1 3슬롯 스윙의 대체재가 아니라 별도 슬리브 후보**다. 번들은 상관 낮은 슬리브일 때만 synthetic 알파가 된다.

