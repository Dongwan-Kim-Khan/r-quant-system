# QQQ Beat Alpha Engine — 성적표

- 생성: 2026-09-08 12:37 UTC
- 마찰: slippage 10bps / fee 8bps / 익일 시가 체결
- 레버: Cash-proxy overlay + vol-target 1.5x/1.0x/1.0x + Composite RS + Conviction 50/30/20
- QLD 사용: yes

## 4대 개조 규격

1. **Cash Proxy Overlay** — 빈 슬롯을 현금으로 놀리지 않고 QQQ(저변동 강세장에서는 QLD 믹스)에 파킹. 주도주 시그널 시에만 프록시를 팔아 스위칭.
2. **Regime-Adaptive Leverage** — SPY≥200SMA & VIX<20 → 총노출 1.5x (QQQ+QLD); 그 외(VIX≥20 또는 SPY<200SMA) → QLD 끄고 QQQ 코어 1.0x. 약세장은 2-Slot. QQQ 코어를 50% 현금화하지 않는다 (SMA 래그 현금 슬리브는 QQQ B&H에 진다).
3. **Composite Multi-RS** — `0.40*RS_21d + 0.35*RS_63d + 0.25*RS_126d`로 유니버스·진입 랭킹. QQQ Composite RS를 못 이기는 종목으로는 프록시를 갈아타지 않음 (dual momentum).
4. **Conviction Sizing** — 1등 50% / 2등 30% / 3등 20%. 진입은 알상무 3-Gate (엔진 A 신고가 / B 기준선 눌림 / VDU).

## Window 2018-08-31 → 2026-08-28

| Book | CAGR | MDD | Sharpe | Calmar | Win% | Trades | vs QQQ excess CAGR | Jensen α |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| QQQ Buy & Hold | 18.39% | -35.62% | 0.819 | 0.516 | — | — | — | — |
| QLD Buy & Hold (2x ceiling) | 27.90% | -63.79% | 0.752 | 0.437 | — | — | — | — |
| PIT Dynamic 60 (Base) | 19.07% | -40.36% | 0.725 | 0.472 | 27.8 | 313 | 0.68 | 12.5 |
| **QQQ Beat Alpha (Challenger)** | 18.47% | -41.71% | 0.725 | 0.443 | 24.0 | 325 | 0.08 | 3.88 |

- Challenger 가동률(주식 비중 평균): 65.39%
- Challenger 평균 총레버리지: 1.178
- QLD 보유일 비중: 59.31%
- 국면 일수: {'bull_lowvol': 1196, 'bull': 376, 'bear': 436}
- Verdict: **BEAT_QQQ_BELOW_35**

## Window 2023-08-31 → 2026-08-28

| Book | CAGR | MDD | Sharpe | Calmar | Win% | Trades | vs QQQ excess CAGR | Jensen α |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| QQQ Buy & Hold | 23.93% | -22.88% | 1.152 | 1.046 | — | — | — | — |
| QLD Buy & Hold (2x ceiling) | 39.92% | -42.34% | 1.028 | 0.943 | — | — | — | — |
| PIT Dynamic 60 (Base) | 10.52% | -36.12% | 0.473 | 0.291 | 23.26 | 129 | -13.41 | 0.81 |
| **QQQ Beat Alpha (Challenger)** | 31.81% | -26.96% | 1.043 | 1.180 | 25.58 | 129 | 7.88 | 6.15 |

- Challenger 가동률(주식 비중 평균): 66.05%
- Challenger 평균 총레버리지: 1.26
- QLD 보유일 비중: 81.23%
- 국면 일수: {'bull_lowvol': 611, 'bull': 77, 'bear': 63}
- Verdict: **BEAT_QQQ_BELOW_35**

## 해석 메모

- Base PIT Dynamic 60은 현금 방치(Cash Drag) + 균등 3-Slot + 단일 63일 RS. 챌린저는 QQQ 파킹 + Composite RS + 50/30/20 + 저변동 1.5x QLD로 그 네 구멍을 막는다.
- SMA200 아래 50% 현금(긴급 대피)은 통제 진단에서 QQQ B&H를 진다 (timed proxy 15.3% vs 18.4%). QQQ 코어는 유지하고 QLD만 끈다.
- 주도주 전량 청산도 200일선 래그로 V-반등을 놓친다. 개별주 청산은 HTML -4%/트레일만.
- 2018-2026 QLD B&H CAGR ≈ 28%, MDD ≈ -64%. 마찰 이후 8년 35%와 MDD -16%는 동시에 성립하지 않는다 (2x 천장 vs 크래시 현금화).
- Window B(2023- )가 AI 사이클 본경기다. 8년 구간은 QQQ 대비 타이/소폭 우위로 읽는다.

