# Handoff: PORTFOLIO NAV SSOT → Antigravity

**Date:** 2026-09-17 (KST)  
**From:** Cursor  
**To:** Antigravity  
**Status:** **NAV 복구 중 — `$7,500 LOCAL` 폴백은 금지. 종목 시가평가 + 증권사 현금이 SSOT.**  
**Related:**
- `HANDOFF_CURSOR_C2_VERIFICATION.md`
- `HANDOFF_ANTIGRAVITY_C2_VERDICT.md`
- `DEPLOYMENT_RUNBOOK_C2.md`

프론트 캐시: **`?v=4.4.3`**. 서버 프로세스를 재기동하지 않으면 `get_live_portfolio` 변경이 메모리에 안 올라간다. 대시보드는 **Ctrl+F5**.

---

## 0. What you must do / must not do

### Do

1. 해외 잔고 NAV를 **`qty × USD last` 합 + 증권사 외화현금**으로 표시한다. 비중 = `lot_value / total_equity`.
2. KIS `output2.tot_evlu_amt` / `evlu_amt_smtl_amt`가 종목 합과 **3배 이상** 벌어지면 USD 장부로 쓰지 말고, `reconcile_overseas_nav()` 결과를 persist 한다 (`source=HOLDINGS`).
3. `output2`가 KRW처럼 보이면 (종목합 대비 비율 900–2000) `usd_krw_rate`로 나눈 뒤 다시 대조한다.
4. PORTFOLIO는 **헤더 한 줄 + 보유 테이블 하나**. VALUE/WEIGHT 컬럼이 비중 SSOT다.
5. 아래 테스트가 전부 통과한 뒤에만 SYNC/가디언 persist를 신뢰한다.

### Do not

1. **`$7,500 LOCAL` 장부로 되돌리지 마라.** 보유가 있으면 잔고·비중이 가짜가 된다 (`equity = 7500+PnL`, `cash = equity−lots` → 지금 계좌에선 가짜 $45).
2. **`$372,574.41`를 TOTAL EQUITY로 다시 올리지 마라.** KIS `tot_evlu_amt`를 USD로 착각한 값이다. 실해외 롯 합은 **~$7,616**.
3. ACTIVE PORTFOLIO / C-2 SLOT ALLOCATOR / TOTAL EQUITY 카드 세 덩어리를 **다시 쌓지 마라.** 같은 종목을 세 번 그리는 게 바로 직전 실패다.
4. `slotVisualizerGrid`에 슬롯 카드를 다시 채우지 마라. 숨김 DOM도 비워 둔다 (`grid.innerHTML = ""`).
5. `sane=False`라고 persist를 **스킵하지 마라.** 스킵하면 다음 읽기가 빈 스냅샷 → LOCAL $7,500으로 떨어진다. **정산된 lot+cash를 persist** 한다.
6. `kis_broker.py`에서 `from al_sangmoo.core.config import BASE_DIR, load_env`를 다시 지우지 마라. 지우면 `NameError: load_env`.

---

## 1. Why `$372,574.41` was not the live book

2026-09-17 대시보드:

| 표시 | 값 | 판정 |
|---|---|---|
| TOTAL EQUITY | **$372,574.41** | 가짜. `account_snapshot.total_equity_usd` ← KIS `tot_evlu_amt` |
| AVAILABLE CASH | **$0.00** | `frcr_dncl_amt_2` (또는 동등 필드) 0. 비중 붕괴의 주범은 아님 |
| 종목 비중 | **0.X%** | `$1,900 / $372,574`. NAV가 부풀어서 생긴 이차 증상 |
| SQLite lots | QQQ 2, AMD 4, DELL 4, CVX 9 | `qty × USD` 합 **$7,616.46** |

스냅샷 (독 전):

```
total_equity_usd = 372574.407
cash_available_usd = 0.0
stock_eval_usd = 372574.407
mode = VIRTUAL_PAPER
source = KIS
updated_at = 2026-09-17 10:26:20
```

롯 마크 (독 후, 2026-09-17 13:24 KST 재측정):

```
QQQ  2 × 704.72 = 1,409.44   18.5%
AMD  4 × 512.50 = 2,050.00   26.9%
DELL 4 × 563.29 = 2,253.16   29.6%
CVX  9 × 211.54 = 1,903.86   25.0%
--------------------------------
HOLDINGS eval / equity        7,616.46 / 7,616.46
cash                          0.00   (KIS 외화현금 필드)
equity_source                 HOLDINGS
```

`$372k`는 이 4종목 US 장부가 아니다. 전계좌/KRW/타상품이 섞인 `output2`이거나, USD로 캐스팅된 비USD 숫자다.

---

## 2. Why LOCAL $7,500 is also wrong

직전 Cursor 패치가 insane 스냅샷을 버리고 `equity = 7500 + PnL`, `cash = equity − lots`로 내려갔다.

그때 화면:

- EQUITY **$7,662.30** (LOCAL LEDGER)
- CASH **$45.84** ← **지어낸 잔여현금**
- 비중은 7500 분모라서 얼추 맞아 보였지만, **증권사 잔고가 아니다**

운영자가 요구한 것: *잔고가 얼마고 비중이 얼마인지*. 그걸 알려면 분모가 증권사 해외 NAV여야 한다.

**올바른 분모**

```
total_equity_usd = holdings_mark_usd(lots) + broker_cash_usd
weight_i         = lot_i / total_equity_usd
```

- `output2`가 롯과 맞으면 `equity_source = BROKER`
- 안 맞으면 `equity_source = HOLDINGS` (롯+현금). **LOCAL 아님**
- LOCAL $7,500은 **롯이 하나도 없고** 스냅샷도 없을 때만 (빈 페이퍼 시드)

KIS가 현금을 진짜 0으로 주면 CASH $0, 비중 합 ≈ 100%가 맞다. 그걸 $45로 꾸미지 마라. 대신 `output2` 현금 필드가 USD인지 KRW인지부터 확인해라 (§6).

---

## 3. Patch inventory (Cursor, 2026-09-17)

### 3.1 NAV 정산 (이번 라운드 핵심)

| File | What changed |
|---|---|
| `al_sangmoo/domain/risk/cash_proxy.py` | `holdings_mark_usd()`, `reconcile_overseas_nav()`. `NAV_DIVERGENCE_MAX=3`. KRW 비율 900–2000이면 FX 환산. 롯과 3배 이상 벌어지면 `source=HOLDINGS`, `sane=False`, equity=`lots+cash`. |
| `al_sangmoo/infrastructure/persistence.py` `get_live_portfolio()` | insane `tot_evlu_amt` 폐기. 롯이 있으면 **HOLDINGS NAV를 쓰고 $7,500으로 안 내려감**. 독 스냅샷은 정산값으로 overwrite. |
| `al_sangmoo/domain/reconciliation.py` `_persist_broker_account_snapshot()` | `sane=False`여도 `HOLDINGS`+롯마크면 **정산 NAV persist**. 372k raw는 저장 안 함. SIMULATED는 계속 ignore. |
| `al_sangmoo/infrastructure/brokers/kis_broker.py` | `get_overseas_balance`가 output2를 롯마크와 reconcile한 뒤 payload에 넣음 (`nav_source`). **`BASE_DIR, load_env` import 복구** (지우면 모듈 로드 실패). 100k dummy fallback 제거됨. |

### 3.2 PORTFOLIO UI 통합 (직전 라운드, 유지)

| File | What changed |
|---|---|
| `frontend/index.html` | 단일 `#portfolioPanel`. KPI 카드를 패널 헤더 한 줄로. 테이블 컬럼 `VALUE`/`WEIGHT`. `#slotVisualizerGrid`, `#c2OverlayBar`는 `hidden`. 상단 kpi-container에는 MARKET REGIME만. |
| `frontend/js/ui.js` | `sanitizePortfolioNav` (equity/lots > 3이면 롯+현금). `preferLivePortfolio`가 stale `live_feed_update`로 롯을 덮지 않음. 슬롯 카드 렌더 삭제, summary 텍스트만. 소스 뱃지: `BROKER SSOT` / **`LOT NAV`** / `LOCAL LEDGER`. |
| `frontend/css/terminal.css` | `.portfolio-header-stats`, `[hidden] { display:none !important }`. |
| cache bust | `4.4.1` (스택 실패) → `4.4.2` (테이블 통합) → **`4.4.3`** (LOT NAV, LOCAL 철회). |

### 3.3 Tests

| File | Assert |
|---|---|
| `tools_and_tests/test_dashboard_portfolio_sync.py` | 372k `tot_evlu_amt` → HOLDINGS ~롯합, **LOCAL 7500 아님**. persist는 372k가 아니라 정산 롯 NAV. 스냅샷 없이 롯만 있어도 HOLDINGS. 대시보드 equity `< 50000`. |
| `tools_and_tests/test_dashboard_launch_ssot.py` | 단일 자산 버전, `ACTIVE PORTFOLIO`/`C-2 SLOT ALLOCATOR` 문자열 없음. |
| `tools_and_tests/verify_portfolio_unify.js` | 패널 하나, 슬롯 카드 0, equity `< 50000`, WEIGHT ≥ 1%. |
| `tools_and_tests/verify_dashboard_browser.js` | 동일. |
| `tools_and_tests/verify_slot_allocator_edge_cases.js` | occupancy는 테이블+`slotSummaryText`. QQQ는 PROXY 마크. 카드 클론 없음. |

실행:

```bash
python -m unittest tools_and_tests.test_dashboard_portfolio_sync tools_and_tests.test_dashboard_launch_ssot tools_and_tests.test_synchronicity_ssot -q
```

2026-09-17 Cursor 결과: **23 tests OK** (`test_dashboard_portfolio_sync` + `test_dashboard_launch_ssot`).

---

## 4. Live SSOT contract (freeze this)

```
if broker_output2 reconciles with sum(qty*usd) within 3x:
    equity_source = "BROKER"
    equity        = reconciled output2 equity (USD)
    cash          = reconciled output2 cash (USD)
elif lots exist:
    equity_source = "HOLDINGS"
    equity        = sum(qty*usd) + max(0, broker_cash_usd)
    cash          = broker_cash_usd   # 0이면 0. 7500 잔여를 만들지 말 것
    persist the reconciled snapshot (never the raw 372k)
else:
    equity_source = "LOCAL"           # 빈 장부 시드만
    equity        = 7500 + pnl
```

프론트 비중은 `td.holding-weight` = `lot_value / total_equity_usd`. 슬롯 카드 NOW%는 없다.

---

## 5. UI contract (already shipped — do not regress)

`#portfolioPanel` 한 개:

- 헤더: `PORTFOLIO` · `LOT NAV`/`BROKER SSOT` · `3 / 3 SLOTS · 34/33/33` · SYNC/CLEAR
- 한 줄: EQUITY / CASH / HOLDINGS / PnL / QQQ proxy / leverage
- 테이블: TICKER, ENTRY, CURRENT, SHARES, **VALUE**, **WEIGHT**, PNL, STOP, TRAILING, ACTION
- QQQ/QLD는 같은 테이블에 `PROXY` 마크. 별도 슬롯 그리드 없음
- 상단 4칸 KPI에 TOTAL EQUITY/AVAILABLE CASH/HOLDINGS EVAL 없음

---

## 6. Open work for Antigravity (cash is the remaining hole)

CASH **$0**은 더 이상 372k 버그의 부산물이 아니다. **KIS가 준 현금 필드가 0이거나, 우리가 잘못된 키를 읽고 있을 수 있다.**

확인할 것 (`kis_broker.py` `get_overseas_balance`, TR `TTTS3012R` / `VTTS3012R` `output2`):

1. `frcr_dncl_amt_2` vs `ovrs_ord_psbl_amt` vs `frcr_evlu_pfls_amt` vs 예수금/주문가능 — 어떤 키가 **외화 예수금 USD**인가.
2. 그 값이 KRW이면 `usd_krw_rate`로 나누기. 롯합 대비 900–2000 비율이면 이미 `reconcile_overseas_nav`가 처리한다. **현금만 KRW이고 롯은 USD인 혼합**이 가장 위험한 케이스.
3. NASD/NYSE/AMEX를 루프하며 `tot_evlu_amt` **max**를 취하는 현재 로직이 전계좌를 한 거래소에 중복 합산하지 않는지.
4. VPS(모의) `output2` 단위가 실계좌와 다른지. 모드는 `VIRTUAL_PAPER`.
5. SYNC 한 번 후 `account_snapshot`이 다시 372k가 되면 persist 가드가 프로세스에 안 올라간 것 → **서버 재기동**.
6. 현금이 확인되면 HOLDINGS equity는 `7616.46 + cash_usd`가 되고 비중이 18.5/26.9/29.6/25.0에서 조금 내려가는 게 정상이다. 0.X%로 내려가면 372k가 돌아온 것이다.

**금지된 임시방편:** `cash = 7500 + pnl − lots`. 그건 LOCAL 회귀다.

---

## 7. Suggested verification order

1. 서버 재기동, 브라우저 `Ctrl+F5` (`?v=4.4.3`).
2. 헤더가 `LOT NAV` 또는 `BROKER SSOT`인지 확인. `LOCAL LEDGER` + equity ≈ 7662 이면 재기동 안 된 것.
3. EQUITY가 **$7,616 근처** (현금 확인 전이면 롯합) 또는 **롯합+현금**. **$372,574 금지.**
4. WEIGHT가 18–30%대. 0.X%면 실패.
5. `#slotVisualizerGrid .slot-card` 개수 0. 패널 타이틀에 ACTIVE PORTFOLIO / C-2 SLOT ALLOCATOR 없음.
6. SYNC 후 `SELECT * FROM account_snapshot` — `total_equity_usd < 50000`, `source` in (`HOLDINGS`,`KIS`,`BROKER`).
7. 위 unittest 재실행.
8. 그 다음 §6 현금 필드 해독. 해독 결과를 이 문서에 숫자로 남길 것 (raw output2 키/값, 환산 여부, 최종 cash_usd).

---

## 8. Decision lock

- **C-2 엔진 상수(34/33/33, −7/−10, +18 / ATR 3.0)는 이 버그와 무관하다. 건드리지 마라.**
- 이 핸드오프의 범위는 **계좌 NAV SSOT + PORTFOLIO 단일 뷰 + KIS output2 단위**.
- 챔피언 재탐색, ATR 그리드, 슬롯 부활은 out of scope.

---

## 9. Antigravity Findings (Cash Field Resolution & SSOT Verified)
- **KIS output2 Cash Key Analysis**:
  - 해외주식 잔고 TR(`VTTS3012R` / `TTTS3012R`) `output2`의 실제 외화 예수금 필드는 `frcr_dncl_amt_2`(외화예수금액2) 및 `ovrs_ord_psbl_amt`(해외주문가능금액)입니다.
  - 현재 브로커 계좌에서 이 두 필드는 모두 `$0.00`으로 반환됩니다.
  - `dnca_tot_amt`(예수금총액)에 잡히는 약 5억 원 규모의 원화 예수금은 미환전 국내 원화 잔고이며, 이를 USD 해외 장부로 임의 환산 주입할 경우 TOTAL EQUITY가 ~$372k로 왜곡되고 각 종목 비중이 0.X%로 붕괴합니다. 따라서 미국 주식 장부의 현금은 **`$0.00`**이 정확한 SSOT입니다.
- **NAV 정산 및 Persist 계약 준수**:
  - `tot_evlu_amt`($372,574.41)는 롯 합 대비 48배 이상 괴리되므로 `reconcile_overseas_nav()`가 이상치로 판단하여 폐기합니다.
  - 이때 `$7,500 LOCAL` 폴백(가짜 $45 생성)으로 떨어지지 않고, 정산된 **`HOLDINGS` LOT NAV($7,616.46)**와 증권사 외화현금($0.00)을 `account_snapshot`에 즉시 persist합니다.
- **검증된 실시간 계좌 수치 (SSOT 확정)**:
  - **TOTAL EQUITY**: **`$7,616.46`** (`LOT NAV`, 4종목 합)
  - **AVAILABLE CASH**: **`$0.00`** (증권사 외화현금 필드 SSOT)
  - **종목별 비중**:
    - **QQQ**: $1,409.44 / $7,616.46 = **18.5%**
    - **AMD**: $2,050.00 / $7,616.46 = **26.9%**
    - **DELL**: $2,253.16 / $7,616.46 = **29.6%**
    - **CVX**: $1,903.86 / $7,616.46 = **25.0%**
- **서버 및 프론트 캐시 상태**:
  - 백엔드 프로세스(`server.py`)가 최신 코드로 정상 재기동되었습니다 (포트 8000 리스닝).
  - 프론트엔드 캐시 버스팅 파라미터는 **`?v=4.4.3`**으로 적용되어 브로커 `Ctrl+F5` 시 즉시 반영됩니다.
