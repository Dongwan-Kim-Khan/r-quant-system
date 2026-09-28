# 사모발행설명서 / 핵심투자자정보서 (PPM / KIID)

## Al-Sangmoo Dynamic Alpha Hedge Strategy
### *(C1–M2 Quantitative Engine)*

| 항목 | 규격 |
|---|---|
| **문서 유형** | Prospectus / PPM & KIID-style Key Facts |
| **전략 코드** | `C1-M2` |
| **분류** | Quantitative Systematic — Trend-Following & Dynamic Regime Overlay |
| **기준통화** | USD |
| **문서일** | 2026-09-08 |
| **백테스트 컷오프** | 2026-08-28 |

> 본 문서는 시스템 트레이딩 전략 및 감리된 역사 시뮬레이션을 설명합니다. 공모 펀드 등록 설명서가 아니며, **적격/전문투자자** 한정 배포를 전제로 합니다.

---

## I. 펀드 개요 (Executive Summary)

| 항목 | 내용 |
|---|---|
| **명칭** | Al-Sangmoo Dynamic Alpha Hedge Strategy (C1–M2 Engine) |
| **전략 유형** | Quantitative Systematic Trend-Following & Dynamic Regime Overlay |
| **벤치마크** | NASDAQ-100 (`QQQ`), S&P 500 (`SPY`) |
| **목표 투자자** | 장기 자본 증식 및 시장 초과 알파를 추구하는 적격 투자자 |
| **권장 자본 계획** | 초기 **USD 8,000** + 매월 첫 거래일 **USD 400** 적립 (DCA / MWRR) |

**핵심 성과(마찰 반영):** 슬리피지 10bps · 수수료 8bps · 익일 시가 · PIT Dynamic-60

| 구분 | 창 | 성과 | MDD |
|---|---|---:|---:|
| M2 거치식 | 2018-08-31→2026-08-28 | CAGR **22.00%** | **−51.13%** |
| M2 적립식 ($8k+$400) | 동일 | ROI **+276.5%** / XIRR **27.48%** / 최종 **$174,683** | **−48.15%** |
| QQQ 동일 적립 | 동일 | 최종 $118,705 | −28.90% |

---

## II. 운용 철학과 전략 구조

1. **Core Philosophy**: 17년 프랍(오동석) 일목균형표(26일 기준선) + VDU를 계량화한 3-Gate. 소액 칼손절을 감수하고 텐배거를 추종 (*Asymmetric Payoff / Let Winners Run*).
2. **Dual-Alpha**:
   - Base Beta: 유휴 현금 → **QQQ Cash Proxy** 상시 파킹
   - Satellite Alpha: PIT 11섹터 유니버스 중 Composite RS 상위 1~3 스위칭 (QQQ CRS 상회만)
3. **Dynamic Leverage**: `SPY ≥ SMA200` AND `VIX < 20` → **1.5x (QQQ+QLD)** / 이탈 시 **즉시 1.0x**

**Composite RS:**

\\[
\\text{CompositeRS}_t = 0.40 \\cdot RS_{21} + 0.35 \\cdot RS_{63} + 0.25 \\cdot RS_{126}
\\]

---

## III. 자본 배치 (Portfolio Construction)

| 슬롯 | 강세 (`SPY≥SMA200`) | 약세 (`SPY<SMA200`) |
|---|---:|---:|
| Slot 1 | **50%** | **25%** |
| Slot 2 | **30%** | **25%** |
| Slot 3 | **20%** | (미사용) |

**리밸런스:** 15:30 ET 스캐너 판정 → 익일 Open 기계 체결 (연구 백테스트는 prior-close / next-open).

---

## IV. 리스크·청산 규율

- **Hard Stop (M2):** 진입가 × **0.95 (−5.0%)** — 이유 불문 전량 청산
- **Trailing TP:** 고점 수익률 **+15%** 돌파 후 `max(26일 기준선, 고점 − 2.5×ATR(14))` 종가 이탈 시 전량
- **Order Guard:** SQLite WAL + `ORDER_MUTEX` 이중발주·초과슬롯 방지

---

## V. 실측 트랙레코드

### 거치식 (초기 $100k 연구 NAV)

| Book | CAGR | MDD | Sharpe | Calmar | Win% | PF |
|---|---:|---:|---:|---:|---:|---:|
| C1 Baseline | 18.47% | −41.71% | 0.725 | 0.443 | 24.0% | 1.489 |
| **M2** | **22.00%** | **−51.13%** | **0.761** | **0.430** | **28.3%** | **1.825** |

AI 3년(2023-08-31→2026-08-28): C1 31.81% / M2 30.23% (vs QQQ +7.88%p / +6.30%p).

### 적립식 ($46,400 납입)

| Book | 최종 | 순이익 | ROI | XIRR | MDD | vs QQQ |
|---|---:|---:|---:|---:|---:|---:|
| **M2** | **$174,683** | **+$128,283** | **+276.5%** | **27.48%** | **−48.15%** | **≈+$56,000** |

### 자본구조 민감도

| 시나리오 | M2 최종 |
|---|---:|
| Heavy DCA ($3k+$600) | **$246,778** |
| Heavy Lump ($30k+$300) | **$282,133** |
| Step-up ($8k; $300→$800) | **$219,158** |

> §5.2 DCA M2 = bear 2×40% + −5%. §민감도 M2 = C1 슬롯 + −5% only. 두 감사 모두 최종자산은 M2 우위.

---

## VI. 위험 고지

- **시장위험**: 나스닥 급락에 따른 원금 손실 (−40%~−51% 경로 MDD 관측)
- **레버리지 Decay**: 1.5x 횡보 시 음의 복리 감쇄
- **갭/체결위험**: 어닝 갭으로 −5% 초과 슬리피지 가능
- **과거성과 Disclaimer**: 백테스트는 미래 수익을 보장하지 않음

---

## VII. 기술 인프라

Python 3.11+ / FastAPI / SQLite 3 WAL / KIS OpenAPI+WebSocket / `PortfolioGuardian` 24/7  
SSOT: `al_sangmoo/core/constants.py` (`STOP_LOSS_PCT=-0.05`, `SLOT_WEIGHTS_BULL/BEAR`, `ATR_MULTIPLIER=2.5`)

**High-Water Mark:** \\(HWM_t = \\max_{0\\le s\\le t} NAV_s\\), \\(DD_t = NAV_t/HWM_t - 1\\)

---

## 문서 통제

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-09-08 | C1-M2 국문 PPM — EN Prospectus와 SSOT 동기 |

**End of Memorandum**
