# Handoff: Ghost QLD SELL / Day-book TR / EXECUTION LOG → Antigravity

**Date:** 2026-09-19 (KST)  
**From:** Cursor  
**To:** Antigravity  
**Status:** **코드 패치 완료. 백엔드(가디언·리컨실·KIS 조회)는 서버 재기동 전까지 메모리에 안 올라간다.**  
**Related:**
- `HANDOFF_ANTIGRAVITY_PORTFOLIO_NAV_SSOT.md`
- `HANDOFF_CURSOR_PROXY_BUDGET_GUARD_AUDIT.md`
- `HANDOFF_ANTIGRAVITY_C2_VERDICT.md`

프론트 캐시: **`?v=4.4.6`** (HTML/JS import 전부 동일 버전). 대시보드는 Ctrl+F5.  
C-2 상수 동결 유지: **34/33/33, −7/−10, +18 / ATR 3.0**. QQQ-first proxy sort 뒤집지 말 것. `$372k tot_evlu_amt` USD 장부 금지. `$7,500 LOCAL` 폴백 금지.

---

## 0. What you must do / must not do

### Do

1. 미디어월 `[체결]`은 **실제 fill** (KIS `filled` / 로컬 원장 FILLED·RECONCILED·SIMULATED) 만. 접수는 `[접수]`.
2. 가디언 `GUARDIAN_ALERT` 브로드캐스트는 `annotate_guardian_alert()` 경유. `PENDING` 은 송출 금지. `filled=False` 면 `POSITION_DELTA holding=null` 금지.
3. 해외 미체결 = **`inquire-nccs` + `TTTS3018R` / `VTTS3018R`**. 체결내역 = **`inquire-ccnl` + `TTTS3035R` / `VTTS3035R`**. 조회일은 **US ET 세션 날짜** (`now_us_eastern`, 전일~당일 윈도우).
4. `proxy_order_intents` 는 티커 PK. 잔고 수량이 이미 요청 qty를 반영했는데 day-book이 `UNKNOWN`/`NOT_FOUND` 이면 **intent를 clear** 해서 반대 side claim이 막히지 않게 한다.
5. EXECUTION LOG는 최신순, 날짜+시간 (`YYYY-MM-DD HH:MM:SS`), 최대 300건. SUBMITTED 도 한 줄로 남긴다.
6. 아래 테스트 통과 후에만 가디언 persist / 슬리브 매도를 신뢰한다.

### Do not

1. **`VTTS3035R`을 `inquire-nccs`에 다시 붙이지 마라.** 3035는 체결내역 TR이다. 예전 코드가 이 때문에 미체결 조회가 전부 실패했다.
2. **`TTTS3039R` / `VTTS3039R` 를 ccnl에 쓰지 마라.** 공식 해외 체결내역은 3035R.
3. 미체결 조회에 `ORD_STRT_DT` / `PDNO` / `TR_CRCY_CD` 를 다시 섞지 마라. VPS에서 `OPSQ2001 INPUT_FIELD_NAME PDNO` 가 난다.
4. 조회일을 `datetime.now()` (KST 자정)로 되돌리지 마라. 미국장 중 KST 00:00이 넘어가면 전일 세션 주문이 안 보인다.
5. 미디어월에 submitted/pending 을 `[체결]`로 표시하지 마라. 현금·원장이 안 바뀌는데 체결처럼 보인다.
6. 미체결 SELL 에 `POSITION_DELTA { holding: null }` 을 보내지 마라. 프론트가 QLD를 지웠다가 포트폴리오 갱신으로 다시 그리는 깜빡임이 난다.
7. `proxy_order_intents` PK를 함부로 `(ticker, side)` 로 풀지 마라. 같은 티커 BUY/SELL 동시 전송이 더 위험하다. **fill 관측 후 clear** 가 SSOT다.
8. PORTFOLIO를 ACTIVE PORTFOLIO / SLOT ALLOCATOR / KPI 카드로 다시 쌓지 마라. 파이(좌) + 테이블(우) 유지.
9. `kis_broker.py` 잔고 JSON을 `print("DEBUG KIS OUTPUT:")` 로 다시 찍지 마라. CANO·보유가 콘솔에 샌다.

---

## 1. Incident (2026-09-19 ~00:35 KST)

운영자 증상:

- 미디어월에 **`[체결] QLD SELL @ $89.97`**
- 현금 **$0 그대로**
- EXECUTION LOG에 해당 SELL 없음
- QLD 계속 보유

2026-09-19 10:04 KST 포렌식 (VPS `VIRTUAL_PAPER`, 잔고 조회 성공):

| 항목 | 값 | 판정 |
|---|---|---|
| Broker QLD | **8주 / AMEX** | 매도 안 됨 |
| Broker QQQ | 1주 / NASD | 유지 |
| Broker cash | **$0.00** | 매도 대금 없음 |
| `proxy_order_intents` QLD | **BUY 1 @ $89.82**, odno `0000027591`, status **UNKNOWN**, created **2026-09-18 23:08:49**, baseline 7, applied 1, filled_qty 0 | 매수는 이미 체결됐는데 intent가 안 지워짐 |
| `proxy_order_intents` QQQ | BUY 1 @ $716.61, odno `0000027592`, UNKNOWN | 동일 패턴 |
| execution_logs QLD SELL (당일) | **없음** | SELL POST 자체가 없음 |
| execution_logs QLD 23:13:46 | BUY recon **7 → 8** | 23:08 매수는 잔고로 확인됨 |
| `query_overseas_day_orders` AMEX/NASD/NYSE | raw=0, **complete=False** | day-book 전량 실패 |

결론: 미디어월 SELL은 **가짜 체결 알림**. 가디언이 QLD 트림/딜레버를 시도했지만, 티커 PK에 남은 BUY intent 때문에 `claim_proxy_order_intent` 가 실패 → 주문 미전송 → `CASH_PROXY_SELL_PENDING` 을 `[체결]`로 브로드캐스트.

`89.97` ≈ `round(live_px * 0.995, 2)` (지정가 매도 버퍼). 계획은 맞았고, **전송이 막힌 것**.

---

## 2. Two stacked bugs

### 2.1 Logic — stuck BUY intent blocks SELL

`proxy_order_intents.ticker` 가 PRIMARY KEY. 한 티커에 BUY intent가 남아 있으면 SELL claim 은 `INSERT OR IGNORE` 실패.

리컨실은 잔고 델타(7→8)로 `applied_quantity=1` 을 적었지만, day-book 상태가 `UNKNOWN` + `query_complete=False` 라서 `should_clear` 가 거짓. 12시간 stale (`43_200s`) 전까지 intent 잔류.

가디언 슬리브는 60초마다 다시 플랜 → claim 실패 → PENDING 액션을 `GUARDIAN_ALERT` 로 쏨 → 프론트 `addMediaWallTradeAlert` 가 무조건 `[체결]`.

### 2.2 API calling — wrong day-book TRs

구 코드 (`query_overseas_day_orders`):

```
nccs_tr = VTTS3035R / TTTS3035R   # WRONG: 3035 is ccnl
ccnl_tr = VTTS3039R / TTTS3039R   # WRONG: not the overseas ccnl TR
path nccs = /inquire-nccs
path ccnl = /inquire-ccnl
params  = OVRS_EXCG_CD + TR_CRCY_CD + ORD_STRT_DT=KST today  # mixed
```

실측 (패치 전 raw probe):

| 호출 | 결과 |
|---|---|
| 3035R on nccs + extra dates | `OPSQ2001 ERROR : INPUT_FIELD_NAME PDNO` |
| 3039R on ccnl | `OPSQ0002 잘못된 주문 코드` |
| 연속 호출 | `EGW00201` 초당 건수 |

공식 매핑:

| 용도 | path | 실전 TR | 모의 TR | 핵심 파라미터 |
|---|---|---|---|---|
| 미체결 | `/uapi/overseas-stock/v1/trading/inquire-nccs` | **TTTS3018R** | **VTTS3018R** | CANO, ACNT_PRDT_CD, OVRS_EXCG_CD, SORT_SQN. **날짜/PDNO 없음** |
| 주문체결내역 | `/uapi/overseas-stock/v1/trading/inquire-ccnl` | **TTTS3035R** | **VTTS3035R** | ORD_STRT_DT/END_DT, SLL_BUY_DVSN, CCLD_NCCS_DVSN, SORT_SQN. 모의는 `PDNO=""`, `OVRS_EXCG_CD=""` |

모의 `VTTS3018R` 은 샘플이 빈약한 편이다. **nccs가 실패해도 ccnl 한 건만 성공하면 `query_complete=True`** (`successful_specs >= 1`). 둘 다 실패해야만 UNKNOWN.

---

## 3. Patch inventory (Cursor, 2026-09-19)

### 3.1 Day-book + intent clear (이번 라운드 핵심)

| File | What changed |
|---|---|
| `al_sangmoo/infrastructure/brokers/kis_broker.py` `query_overseas_day_orders` | TR을 3018/3035로 교정. nccs·ccnl 파라미터 분리. `_us_session_inquiry_dates()` = ET 전일~당일. 모의는 거래소/PDNO 공란. 3초 캐시. `query_complete` = 스펙 1개 이상 성공. |
| 동일 파일 `get_overseas_balance` | `print("DEBUG KIS OUTPUT:", ...)` **삭제** (계좌·보유 누출). |
| `al_sangmoo/domain/reconciliation.py` | `holdings_confirmed_complete`: 요청 qty가 잔고에 반영됐고 상태가 `UNKNOWN`/`NOT_FOUND` 이면 intent clear. PARTIAL/FILLED 경로의 기존 테스트는 유지. |

### 3.2 Ghost fill 알림 (직전 라운드, 유지)

| File | What changed |
|---|---|
| `al_sangmoo/domain/risk/portfolio_guardian.py` | `annotate_guardian_alert()`. PENDING 드롭. submitted면 `filled=False`. **fill 일 때만** `POSITION_DELTA`. KIS `submitted` 분기에서 `record_execution_log(status=SUBMITTED)`. |
| `al_sangmoo/domain/risk/autopilot_trader.py` | 프록시 슬리브/현금확보 submitted 경로에 SUBMITTED 감사 로그. |
| `frontend/js/ui.js` | `isConfirmedFill`, `shouldShowTradeAlert`, `formatExecutionTs`. 미디어월 `[체결]` vs `[접수]`. 로그 시각에서 날짜를 잘라내지 않음. |
| `frontend/js/websocket.js` | `guardian_alert` 토스트: FILLED vs `접수(미체결)`. PENDING 스킵. |
| `frontend/css/terminal.css` | `.media-wall-trade-alert.alert-submitted` (노란 접수 뱃지). |

### 3.3 EXECUTION LOG / TOP PICKS / PORTFOLIO 파이 (같은 세션 UI)

| File | What changed |
|---|---|
| `frontend/index.html` | QUANT VERDICT를 `#convictionPanel` 안으로 병합. `#tradeLogContainer` 를 차트 아래 옛 VERDICT 슬롯에 상시 표시. PORTFOLIO = `#portfolioPie` 왼쪽 + holdings 테이블 오른쪽. 캐시 `?v=4.4.6`. |
| `frontend/css/terminal.css` | `.matched-panel` 높이 300px (TOP PICKS ↔ EXECUTION LOG). `.portfolio-body` 파이\|테이블. |
| `frontend/js/ui.js` | `renderAllocationPie` / `_donutSlice`. `renderTradeLogs` = 최신순 `slice(0, MAX_LOGS)`, 숨김 타이머 없음. |
| `frontend/js/api.js`, `websocket.js` | execution-logs poll/default **300**. |
| `al_sangmoo/interfaces/api/routers/dashboard.py` | feed inject `get_execution_logs(limit=300)`, 엔드포인트 max 300. |
| `al_sangmoo/infrastructure/persistence.py` | `get_execution_logs` default 300. |

---

## 4. Tests (must stay green)

```
python -m unittest tools_and_tests.test_idempotent_kis_order.TestOverseasDayOrderInquiry -q
python -m unittest tools_and_tests.test_cash_proxy_sleeve_guardian.TestReconciliationCurrentPriceSync.test_proxy_buy_intent_clears_when_qty_confirms_fill_with_unknown_day_book -q
python -m unittest tools_and_tests.test_cash_proxy_sleeve_guardian.TestGuardianAlertFillSemantics -q
python -m unittest tools_and_tests.test_cash_proxy_sleeve_guardian.TestReconciliationCurrentPriceSync.test_proxy_partial_fill_keeps_intent_until_terminal_cancel -q
python -m unittest tools_and_tests.test_dashboard_launch_ssot tools_and_tests.test_synchronicity_ssot.TestFrontendSynchronicity.test_log_ring_buffer_cap -q
```

Cursor 검증 (2026-09-19): 위 세트 **OK**.

신규/보강 포인트:

- `TestOverseasDayOrderInquiry`: 모의 `VTTS3018R`+`VTTS3035R`, 실전 `TTTS3018R`+`TTTS3035R`, nccs에 날짜 없음.
- `test_proxy_buy_intent_clears_when_qty_confirms_fill_with_unknown_day_book`: baseline 7 → broker 8, day-book UNKNOWN 이어도 BUY intent clear 후 SELL claim 가능.
- `TestGuardianAlertFillSemantics`: PENDING 미송출, SUBMITTED `filled=False`, FILLED `filled=True`.
- 프론트: `formatExecutionTs`, `[접수]`, `rawTs.split(' ')[1]` 제거, `slice(0, this.MAX_LOGS)`.

부분체결 테스트(`PARTIALLY_FILLED` + applied < requested) 는 intent를 **유지**해야 한다. holdings_confirmed_complete 를 PARTIAL/FILLED 에 확장하지 마라.

---

## 5. Operator restart checklist

1. `알상무_퀀트_터미널_실행.bat` 또는 `server.py` **재기동** (가디언/오토파일럿/리컨실 프로세스).
2. 브라우저 `?v=4.4.6` Ctrl+F5.
3. 재기동 후 첫 `check_sync` 에서 QLD/QQQ UNKNOWN BUY intent 가 잔고 반영분이면 삭제돼야 한다.
4. 그 다음 미국 정규장에서 슬리브가 QLD 매도를 다시 계획하면: EXECUTION LOG에 **SUBMITTED** 한 줄 → 체결 시 **RECONCILED/FILLED**. 미디어월은 접수면 `[접수]`, 체결이면 `[체결]`.
5. 현금이 여전히 $0 이고 QLD 8주면 **아직 매도가 안 나간 것**. 미디어월만 믿지 말고 로그 status 와 KIS 잔고를 본다.

---

## 6. Residuals (optional, not this patch)

- 모의 `VTTS3018R` 이 게이트웨이에서 거절될 수 있음. ccnl `VTTS3035R` + `CCLD_NCCS_DVSN=00` 가 폴백. 둘 다 실패하면 intent는 다시 UNKNOWN — 그때는 잔고 델타 clear 가 안전망.
- KIS 모의 해외 지정가는 실전보다 체결이 늦거나 안 될 수 있음. 그건 브로커 시뮬 한계. **가짜 [체결] 알림과 구분**할 것.
- `remaining_cash` 를 ACK 전에 깎는 프록시 가드 이슈는 `HANDOFF_CURSOR_PROXY_BUDGET_GUARD_AUDIT.md` 잔여. 이번 패치 범위 아님.
- `ord_psbl_cash` 를 USD 지갑 SSOT 로 쓰지 말 것 (기존 NAV 핸드오프).

---

## 7. Live book snapshot at audit (do not treat as NAV SSOT if KIS output2 explodes)

2026-09-19 10:04 KST, VPS overseas lots:

```
AMD  4 × 559.82  NASD
CVX  9 × 209.51  NYSE
DELL 4 × 568.06  NYSE
QLD  8 ×  91.00  AMEX
QQQ  1 × 721.45  NASD
cash             0.00
equity ~         7,846.56   (lot+cash HOLDINGS/BROKER, not tot_evlu_amt)
```

QLD 매도 미체결. 재기동 후 intent clear → 정규장 슬리브 재시도가 다음 액션이다.
