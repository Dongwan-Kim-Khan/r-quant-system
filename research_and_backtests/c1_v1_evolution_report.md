# C1 v1 Evolution — Entry Filter / Bear 1-Slot / Dynamic Trail
- 생성: 2026-09-08 13:25 UTC
- 마찰: slippage 10bps / fee 8bps / 익일 시가
- 목표: 8년 CAGR ≥ 22%, MDD ≥ −33%, AI 3년 QQQ 초과 알파 보존
## 모델
- **Baseline** — C1 v1 (3슬롯 50/30/20, 약세 2슬롯 25/25, HTML −4%/2.5·ATR).
- **E1** — + Entry Squeeze (kijun gap≤3.5%, ATR/Close ≤ 60d mean).
- **E2** — + Bear max 1 slot @ 25%, 나머지 QQQ.
- **E3** — E1+E2 + Dynamic Profit-Lock (peak≥+35% → ATR×1.8).
## Window 2018-08-31 → 2026-08-28
| Book | CAGR | MDD | Sharpe | Calmar | Trades | Win% | PF | vs QQQ | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| QQQ B&H | 18.39% | -35.62% | 0.819 | 0.516 | — | — | — | — | — |
| QLD B&H | 27.90% | -63.79% | 0.752 | 0.437 | — | — | — | — | — |
| Baseline | 18.47% | -41.71% | 0.725 | 0.443 | 325 | 24.0 | 1.489 | 0.08 | BEAT_BASE_AND_QQQ |
| **E1** | 4.36% | -36.16% | 0.297 | 0.121 | 219 | 24.2 | 1.285 | -14.03 | LOSE_QQQ |
| **E2** | 10.33% | -44.80% | 0.478 | 0.231 | 358 | 25.7 | 1.202 | -8.06 | LOSE_QQQ |
| **E3** | 3.82% | -34.06% | 0.275 | 0.112 | 221 | 27.15 | 1.144 | -14.57 | LOSE_QQQ |
- E1 filter reject/pass: 4337 / 524  hard_stops 165 (Baseline 246)
- E2 regime days: {'bull_lowvol': 1196, 'bull': 376, 'bear_1slot': 436}
- E3 lock-trail hits: 6  occ 55.22%  lev 1.187
- Trade reasons E3: {'HARD_STOP': {'n': 150, 'avg_pnl': -4.427, 'share_pct': 67.9}, 'TRAILING': {'n': 38, 'avg_pnl': 12.254, 'share_pct': 17.2}, 'SLOT_TRIM': {'n': 24, 'avg_pnl': 2.664, 'share_pct': 10.9}, 'TRAIL_LOCK': {'n': 6, 'avg_pnl': 38.22, 'share_pct': 2.7}, 'EOB': {'n': 3, 'avg_pnl': 1.354, 'share_pct': 1.4}}
## Window 2023-08-31 → 2026-08-28
| Book | CAGR | MDD | Sharpe | Calmar | Trades | Win% | PF | vs QQQ | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| QQQ B&H | 23.93% | -22.88% | 1.152 | 1.046 | — | — | — | — | — |
| QLD B&H | 39.92% | -42.34% | 1.028 | 0.943 | — | — | — | — | — |
| Baseline | 31.81% | -26.96% | 1.043 | 1.180 | 129 | 25.58 | 1.76 | 7.88 | TARGET_HIT |
| **E1** | 6.70% | -32.00% | 0.427 | 0.209 | 86 | 23.26 | 1.186 | -17.23 | LOSE_QQQ |
| **E2** | 37.32% | -26.30% | 1.143 | 1.419 | 136 | 27.21 | 1.761 | 13.39 | TARGET_HIT |
| **E3** | 5.39% | -31.08% | 0.369 | 0.173 | 95 | 24.21 | 1.073 | -18.54 | LOSE_QQQ |
- E1 filter reject/pass: 1865 / 181  hard_stops 65 (Baseline 95)
- E2 regime days: {'bull_lowvol': 611, 'bull': 77, 'bear_1slot': 63}
- E3 lock-trail hits: 3  occ 62.01%  lev 1.246
- Trade reasons E3: {'HARD_STOP': {'n': 68, 'avg_pnl': -4.471, 'share_pct': 71.6}, 'SLOT_TRIM': {'n': 4, 'avg_pnl': 1.597, 'share_pct': 4.2}, 'TRAILING': {'n': 17, 'avg_pnl': 11.749, 'share_pct': 17.9}, 'TRAIL_LOCK': {'n': 3, 'avg_pnl': 38.847, 'share_pct': 3.2}, 'EOB': {'n': 3, 'avg_pnl': 1.354, 'share_pct': 3.2}}
## 진단 (스윗스팟)

- **8년 챔피언: Baseline** CAGR 18.47% / MDD -41.71% (목표 22%+ / MDD ≥ −33%; QQQ 18.39%).
- **E2 Bear 1-Slot**: 8년 10.33% / MDD -44.80% / 국면 {'bull_lowvol': 1196, 'bull': 376, 'bear_1slot': 436}. AI 3년은 37.32%로 Baseline을 앞질렀다(vs QQQ 13.39%p) — 약세 1슬롯이 메가사이클에서는 도움이지만, 8년 전체에서는 2020/2022 이후 복리 슬롯을 비워 CAGR이 꺾인다.
- **E1 Entry Filter는 과필터.** reject 4337 / pass 524 — kijun gap≤3.5% + ATR 수축이 폭주 대장주 진입을 같이 잘라 8년·AI 3년 CAGR을 붕괴시킨다. hard stop은 줄었으나 승률은 그대로(~24%).
- **E3 Integrated**: 8년 3.82% / MDD -34.06% / TRAIL_LOCK hits 6. E1 독성이 E2·트레일 이득을 덮어쓴다.
- **AI 3년 알파 보존 체크**: Baseline 31.81% (vs QQQ 7.88%p) | E1 6.70% | E2 37.32% | E3 5.39% (vs QQQ -18.54%p).
- AI 3년 +5%p 이상 알파 훼손 — 통합 E3가 메가사이클을 과필터.
- **목표 판정**: E3 8년 22%+ & MDD≥−33% → **MISS** — 모듈 독립 기여를 보고 다음 레버를 고른다.

