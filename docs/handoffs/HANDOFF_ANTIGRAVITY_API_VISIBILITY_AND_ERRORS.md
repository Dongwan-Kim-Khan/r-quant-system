# Handoff: API 가시성·오류 표시 → Antigravity

**Date:** 2026-09-22 (KST)  
**From:** Cursor  
**To:** Antigravity  
**Status:** **계획만. 코드는 아직 안 고쳤다. 이 문서대로 패치한다.**  
**Related:**
- `HANDOFF_ANTIGRAVITY_FALSE_FILL_AND_DAYBOOK.md` (체결/접수 구분. 이 작업과 겹치면 그 문서가 우선)
- `HANDOFF_ANTIGRAVITY_PORTFOLIO_NAV_SSOT.md` (NAV 산식. 건드리지 말 것)

프론트 캐시: 지금 **`?v=4.4.6`**. JS를 고치면 `index.html`과 `frontend/js/*.js` import의 쿼리를 **같은 버전으로 한 단계만** 올린다 (`4.4.7`). 대시보드는 Ctrl+F5.  
서버 프로세스를 재기동하지 않으면 라우터·예외 핸들러 변경은 메모리에 안 올라간다.

퀀트 판정, 슬롯 비중, 손절/익절, KIS TR, 원장 NAV 산식은 이 작업의 범위가 아니다.

---

## 0. What you must do / must not do

### Do

1. 실패는 **HTTP 상태, 응답 필드, 서버 로그, 화면 문구** 네 곳 중 최소 화면+로그에 남긴다. `except: pass` 로 200을 성공처럼 돌려주지 마라.
2. 조회 실패와 “정상적으로 비어 있음 / 모의투자 / 기능 OFF” 를 **다른 문구**로 나눠라.
3. 주문·스캔·대조는 **접수 / 체결 / 실패** 를 각각 토스트한다. `res.ok` 만으로 완료 토스트를 띄우지 마라.
4. 500 본문에 사람이 읽는 `detail` 문자열을 넣어라. 422 `detail` 배열은 토스트에서 `msg`를 이어 붙여라.
5. 아래 수용 기준이 맞는 테스트를 추가한 뒤 기존 가드 테스트를 깨지 마라.

### Do not

1. **C-2 상수를 바꾸지 마라.** 34/33/33, −7/−10, +18 / ATR 3.0, QQQ-first proxy sort.
2. **`$7,500 LOCAL` 폴백, `$372k tot_evlu_amt` USD 장부를 되돌리지 마라.** NAV는 `HANDOFF_ANTIGRAVITY_PORTFOLIO_NAV_SSOT.md` 가 SSOT다.
3. **미디어월 `[체결]`에 submitted 를 올리지 마라.** 접수는 `[접수]`. `HANDOFF_ANTIGRAVITY_FALSE_FILL_AND_DAYBOOK.md` 를 깨지 마라.
4. 클라이언트에 스택트레이스, KIS 키, 계좌번호를 넣지 마라. 500 문구는 고정 한글 한 줄 + 서버 stderr/logger 에만 예외를 남긴다.
5. `GET /api/portfolio` 최상위를 `{portfolio, reconcile}` 로 감싸지 마라. `renderPortfolio` 는 `holdings` / `total_equity_usd` 가 루트에 있어야 한다. 대조 결과는 **같은 객체에 `reconcile` 키를 추가**한다.
6. 웹소켓 Origin 을 `*` 로 열지 마라. 아래 6.2의 same-host 허용만 한다.
7. 대시보드 폴링을 끄고 웹소켓만 믿지 마라. 연결 중에도 느린 HTTP 재조회로 브로드캐스트 유실을 메운다.
8. `dashboard_data.json` 을 정적 폴백으로 다시 두지 마라. 그 경로는 서버에 라우트가 없다 (`/static` 만 마운트).

---

## 1. 증상 (2026-09-22 코드 검수)

전략 로직은 안 봤다. 호출·응답·화면만 봤다.

| # | 사용자가 보는 것 | 실제 |
|---|---|---|
| 1 | 첫 화면 포트폴리오가 그냥 그려짐 | `GET /api/portfolio?reconcile=true` 가 `check_sync` 의 `status:"error"` 를 버리고 200 |
| 2 | SYNC 토스트 `Sync & Refresh Complete` | `POST /api/broker/reconcile` 가 잔고 실패도 200. 스캔은 큐 접수만 하고 본 작업은 뒤에서 실패 가능 |
| 3 | 매수/매도 후 설명 없음. 매도 보유가 그대로 | `status:"submitted"` 를 프론트가 안 읽음. 원장은 체결 전이라 안 바뀜 |
| 4 | 헤더 `LIVE` 인데 피드가 옛 값 | WS 연결 시 대시보드 HTTP 폴링 중단. 브로드캐스트 실패는 삼킴 |
| 5 | 빈 화면, 콘솔도 없음 | `GET /api/dashboard` 503/500 은 `fetch`가 throw 하지 않음. `api.js` 는 `res.ok` 아니면 로그 없이 `null` |
| 6 | `KIS API: SIMULATION` | 상태 API 네트워크 실패와 `is_configured:false` 가 같은 분기 |
| 7 | 가디언/오토파일럿 `STANDBY` | 상태 조회 실패(`null`)와 실제 OFF 가 같음. 그 상태에서 클릭하면 **켜기**를 보냄 |
| 8 | 토스트 `[object Object]` 또는 `Buy order failed` | 422 `detail` 은 배열. 500 본문은 `{message}` 뿐이고 프론트는 `detail` 만 읽음 |
| 9 | 종목 전환 후 차트만 비고 `N/A`도 아님 | 8초 타이머가 **현재** AbortController 를 abort. 취소 응답은 `renderNotFound` 로 안 감 |
| 10 | `REAUTH` | localhost 가 아닌 Origin 은 1008. 인증 실패가 아님 |
| 11 | `NO SECTOR DATA` / 체결 로그 공백 / 차트 `N/A` | 백엔드가 예외를 빈 배열·404·직전 파일로 바꿈 |

---

## 2. 공통 계약 (먼저 이것만 고정)

다른 패치보다 먼저 넣는다. 이후 화면은 이 계약만 믿는다.

### 2.1 서버 오류 본문

`server.py` `global_exception_handler`:

- `HTTPException`: 지금처럼 `{detail}` (문자열이 아니면 문자열로 바꿔서).
- `RequestValidationError`: 422 `{detail: errors()}` 유지. 프론트가 배열을 풀어야 한다.
- 그 외: **500** `{ "status": "error", "detail": "내부 오류가 발생했습니다.", "message": "내부 오류가 발생했습니다." }`.  
  `print`/`logger.exception` 에 method, path, 예외 타입을 남긴다. 본문에 `str(exc)` 를 넣지 마라.

### 2.2 프론트 `errorDetail`

`frontend/js/api.js` 에 한 함수.

- `detail` 이 문자열이면 그 문자열.
- `detail` 이 배열이면 각 원소의 `msg` (없으면 `JSON.stringify`) 를 `"; "` 로 잇는다.
- 없으면 `message` 문자열.
- 그래도 없으면 호출자가 준 fallback.
- `ApiError` 에 `status`, `kind` (`http` | `network` | `timeout` | `aborted` | `parse`) 를 붙인다.

변경·토글·리셋·스캔·대조는 전부 이 함수로 throw 한다. `resetPortfolio` / `toggleGuardian` / `toggleAutoPilot` 의 고정 영문만 던지는 분기를 없앤다.

조회(`getDashboardData`, `getPortfolioData`, `getExecutionLogs`, `getChartData`, `searchStocks`, `getBrokerStatus`, `getGuardianStatus`, `getAutoPilotStatus`)는 **`!res.ok` 와 네트워크 오류를 구분해서 로그**한다. `console.warn` 에 status 와 detail. HTTP 오류를 성공 데이터처럼 `null`/`[]` 로만 반환하지 마라 (3장에서 호출부 처리).

---

## 3. 작업 순서

### 3.1 대조 — 실패를 200 성공으로 두지 않기

**파일:** `al_sangmoo/interfaces/api/routers/portfolio.py`, `al_sangmoo/interfaces/api/routers/broker.py`, `frontend/js/api.js`, `frontend/js/websocket.js`

`check_sync()` 가 이미 `{status:"error", message}` 를 반환한다 (`al_sangmoo/domain/reconciliation.py` 잔고 조회 실패). 예외가 아니면 라우터는 그 dict 를 무시한다.

- `POST /api/broker/reconcile`: `report["status"] != "success"` 이면 **502** `{detail: report["message"], report}`. 예외는 500 공통 계약.
- `GET /api/portfolio`:
  - `reconcile=false`: 지금처럼 원장만. `reconcile` 키 없음.
  - `reconcile=true`: 원장 루트에 `reconcile: {status, message}` 를 붙인다.  
    - 브로커 미설정: `{status:"skipped", message:"브로커 미설정"}`.  
    - `check_sync` error dict: `{status:"error", message}` 를 **응답에 넣고** `logger.warning`. HTTP는 200 (화면은 마지막 원장을 유지).  
    - 예외: `{status:"error", message:"대조 중 오류"}` + `logger.exception`. 원장은 그대로 반환.
- 프론트 `getPortfolioData(true)`: 200이면 원장을 그리되 `reconcile.status==="error"` 이면 토스트(error)에 `message`. 502면 `errorDetail` 로 throw 하고, 본문에 `portfolio` 가 있으면 그것도 그린 뒤 토스트.
- `TerminalApp.init` / `refreshData` / SYNC 의 `syncBroker()`: 위 결과를 삼키지 마라. SYNC 완료 토스트는 **대조가 success 또는 skipped** 일 때만.

### 3.2 주문 — 접수와 체결

**파일:** `frontend/js/ui.js` (`executeQuickBuy`, EXIT 핸들러), `frontend/js/api.js` (`buyHolding`, `sellHolding`)

백엔드 `portfolio.py` 의 `submitted` 조기 return 은 유지한다 (원장에 미체결을 넣지 마라. false-fill 핸드오프).

- 응답 `status` 가 `submitted` / `SUCCESS_VIA_RECHECK` 가 아니고 `filled`/`success` 도 아니면 기존 HTTP 오류 경로.
- `submitted`: 토스트 **info**, 서버 `message` 그대로. 문구에 체결 완료를 쓰지 마라. 매수 버튼은 2초 후 되돌려도 된다. 매도 버튼은 `CLOSING...` 에 두지 말고 `EXIT` 로 복구한다 (보유가 남는 게 맞다).
- `success` / `filled`: 토스트 success, 서버 `message`. 그 다음 `refreshData()`.
- `refreshData()` 의 조회 실패는 주문 성공을 덮어쓰는 실패 토스트로 바꾸지 마라. 별도 warn 한 줄.

### 3.3 스캔 버튼 — 접수와 완료

**파일:** `frontend/js/websocket.js` (`scan_status`, `btnSyncAll`), `al_sangmoo/interfaces/api/routers/scanner.py` (동작 유지, 메시지 필드 유지)

`POST /api/scan_now` 는 계속 즉시 200 + `scanning_started` / `already_scanning` 이다. 백그라운드 실패는 `scan_status.status="error"` 다.

- 클릭 핸들러는 `triggerScan()` 의 `message` 를 info 토스트로 보여 준다.
- `already_scanning` 이면 버튼 문구를 다시 `SYNC / REFRESH` 로 두고, 이어서 대조(3.1)와 `refreshData()` 만 한다. “스캔 완료”라고 쓰지 마라.
- `scanning_started` 이면 버튼은 `SYNCING...` + disabled. 대조와 원장 갱신은 **그때 해도 된다** (스캔과 별개). 완료 토스트 문구는 `대조 완료` / `대조 실패` 만. 스캔 완료 토스트는 WS `completed` 에서만.
- `handleMessage` 의 `scan_status`:
  - `completed`: 버튼 복구 + success 토스트에 `message`.
  - `error`: 버튼 복구 + error 토스트에 `message`.
  - `started`: 버튼 `SYNCING...`.
- 120초 안에 `completed`/`error` 가 없으면 버튼을 복구하고 warn 토스트 `스캔 결과를 받지 못했습니다`. 타이머는 다음 클릭에서 지운다.
- `scanner.py` 의 `except` 는 이미 stderr + `status:error` 브로드캐스트를 한다. 유지. `message` 에 `str(exc)` 를 넣지 마라 (지금 고정 한글이면 그대로).

### 3.4 대시보드 피드 — 예외를 정상 페이로드로 위장하지 않기

**파일:** `al_sangmoo/interfaces/api/routers/dashboard.py`, `server.py` lifespan, `frontend/js/api.js`, `frontend/js/websocket.js`, `frontend/js/ui.js`

- 실시간 포트폴리오 주입 실패 (`dashboard.py` 하단 `except Exception as e: pass`): `logger.exception`. 파일 포트폴리오를 쓸 거면 `portfolio_source:"feed_cache"` 와 `portfolio_error:"실시간 원장을 읽지 못했습니다"` 를 붙인다. 성공 시 `portfolio_source:"sqlite"`, `portfolio_error` 없음.
- 매크로 게이지 실패: `logger.exception`. 게이지를 못 넣었으면 `macro_error` 한 줄. 직전 캐시가 있으면 그 캐시를 쓰되 필드로 구분할 필요는 없다 (30초 캐시는 정상).
- `load_universe_sectors` 실패: `universe_sectors_error` 문자열. 실패 시 `universe_sectors` 를 `[]` 로 덮어 “데이터 없음”과 섞지 마라. 키가 없으면 프론트는 실패로 본다.
- `get_execution_logs` 실패: `execution_logs_error: true`. `execution_logs: []` 로 체결이 없는 척하지 마라. 전용 `GET /api/execution-logs` 는 예외 시 500 (공통 계약). 200 + 빈 배열로 숨기지 마라.
- `get_feed_cache` JSON 파손: `logger.exception`. 캐시가 비면 지금처럼 503. 503 `detail` 유지.
- `server.py` lifespan 의 기동 스캔 `except Exception: pass` → `logger.exception`.
- 프론트 `renderDashboard`: `portfolio_error` / `universe_sectors_error` / `execution_logs_error` / `macro_error` 가 있으면 **한 번** error 토스트. 같은 문구 반복은 막는다 (마지막 문구 기억).
- 섹터 컨테이너: `universe_sectors_error` 이면 `SECTOR LOAD FAILED`. 배열이 정말 비어 있고 에러 키가 없을 때만 `NO SECTOR DATA`.
- `getDashboardData`: `!res.ok` 이면 `ApiError`. **`dashboard_data.json` 상대 경로 폴백 삭제.** `TerminalApp.init` 의 catch 에서 토스트 `대시보드를 불러오지 못했습니다: …`. 빈 화면만 두지 마라.

### 3.5 상태 배지 — 실패 / OFF / 모의

**파일:** `frontend/js/ui.js` (`systemHealthState`, `updateSystemHealth`), `frontend/js/websocket.js` (브로커 상태, 토글)

초기값:

```js
kis: "unknown",   // "live" | "simulation" | "unreachable" | "unknown"
guardian: "unknown",
autopilot: "unknown",
ws: "CONNECTING"
```

`true` 로 시작하지 마라. 팝오버는 응답 전에 `확인 중`.

- `getBrokerStatus` 가 throw/null (네트워크·HTTP 오류): `kis="unreachable"`, 헤더 `KIS API: UNREACHABLE`, 팝오버 `상태 조회 실패`. 모의투자 색으로 치지 마라.
- 200 이고 `is_configured` 거짓: `simulation`, 지금 문구 `KIS API: SIMULATION` / `SIMULATION (모의)`.
- 200 이고 `is_configured` 참: `live`, `KIS API: CONNECTED` / `LIVE (실계좌)`.
- 가디언·오토파일럿 조회 실패: 배지 `UNKNOWN`, 팝오버 `상태 조회 실패`. `set_enabled` 를 호출하지 마라.
- 조회 성공: 배지 `ACTIVE` / `STANDBY` 는 실제 `is_enabled`/`enabled` 만.
- 토글 클릭: `getFn()` 이 실패하면 토스트 후 **return**. `null` 을 꺼짐으로 보고 `enabled:true` 를 보내지 마라.
- 전체 사인: `unknown` 또는 `unreachable` 이면 YELLOW `WARN`. WS `ERROR`/`DISCONNECTED` 만 RED. 기능 OFF(조회는 성공)는 YELLOW 여도 문구는 `DISABLED`/`STANDBY` 로 두고, 조회 실패 문구와 겹치지 마라.

### 3.6 차트

**파일:** `frontend/js/api.js` `getChartData`, `frontend/js/chart.js` `loadChart` / `renderNotFound`

- `setTimeout` 콜백은 그 요청의 `AbortController` 지역 변수를 abort 한다. `this._chartAbortController` 를 나중에 끊지 마라.
- 종목 변경으로 이전 요청을 abort 한 경우: `{aborted:true, reason:"superseded"}`. 화면은 새 요청 결과를 기다린다. `N/A` 로 덮지 마라.
- 8초 타임아웃: `{aborted:true, reason:"timeout"}`. `loadChart` 는 캔들을 비우고 가격 자리에 `TIMEOUT`. 토스트 warn `차트 응답이 지연되었습니다`.
- HTTP 404: 지금처럼 `renderNotFound` → `N/A`.
- HTTP 5xx/네트워크: `N/A` 가 아니라 `LOAD FAILED` + error 토스트에 `errorDetail`.
- `charts.py` 의 `_enrich_chart_with_realtime_price` / `atomic_save_json` / KIS 폴백 `except` 는 `logger.exception` 만 추가한다. 차트 JSON 자체는 그대로 반환해도 된다 (시세 지연이지 차트 부재가 아님). 404로 바꾸지 마라.

### 3.7 웹소켓 · 폴링

**파일:** `frontend/js/websocket.js`, `al_sangmoo/api/hub.py` (로그만), `al_sangmoo/interfaces/api/routers/broker.py`, `server.py` Origin

- `CONNECTED` 여도 대시보드+포트폴리오 HTTP 폴링을 **15초**로 유지한다. 5초 장애 폴링(연결 끊김)은 그대로 둔다. `last_updated` 가 같으면 `renderDashboard` 를 통째로 다시 그리지 않아도 된다. 포트폴리오는 `reconcile=false` 로 읽는다 (15초마다 자동 대조 금지).
- `_hydrateFromHttp` / 폴링의 빈 `catch` 를 없앤다. 실패마다 토스트하지 말고, 연속 실패 시 상태 문구 `FEED STALE` (점 색 amber). 한 번 성공하면 해제.
- `broker.py` 주문 후 `hub.broadcast` 의 `except: pass` → `logger.exception`. 주문 HTTP 응답은 그대로 둔다 (브로드캐스트 실패로 주문을 실패로 뒤집지 마라).
- `hub.connect` 가 False (한도 50, accept 실패): 서버는 이미 1008 + reason. 프론트 `onclose`:
  - code 1008 이고 reason 에 `limit` : 라벨 `WS LIMIT`. `reconnectAttempts` 를 0으로 리셋하지 마라. `checkAndReconnect` 가 attempts 를 0으로 지우지 마라.
  - code 1008 이고 reason 에 `Origin` : 라벨 `ORIGIN BLOCKED`. `REAUTH` 로 쓰지 마라.
- `checkAndReconnect`: 소켓이 OPEN 이고 pong 이 30초 초과면 **close 한 다음** `connect()`. OPEN 인 채 `connect()` 가 return 하게 두지 마라.
- Origin (`server.py` `websocket_live_hub`): `ALLOWED_ORIGINS` 에 있거나, Origin 의 host 가 요청 `Host` 헤더와 같으면 허용 (scheme 은 http→ws, https→wss 대응). Origin 없음·다른 호스트는 지금처럼 1008 `Forbidden Origin`. `*` 금지.
- `file://` : 지금처럼 WS를 열지 않는다. 라벨 `LOCAL FILE`. 대시보드 조회가 실패하면 3.4 토스트. 상대 경로 `dashboard_data.json` 은 호출하지 마라.

### 3.8 `/api/health`

**파일:** `al_sangmoo/interfaces/api/routers/dashboard.py`

KIS 네트워크는 치지 마라.

- 피드 파일 존재 + JSON 파싱 가능, SQLite `get_live_portfolio()` 가 예외 없이 반환 → 200 `{status:"ok", feed:"ok", database:"ok", broker_configured: bool}`.
- 피드 없음/파손 또는 DB 예외 → **503** `{status:"error", detail, feed, database}`. `detail` 은 고정 한글. 원인은 로그.
- 프론트는 health 를 주기 호출하지 않아도 된다. 운영 확인용.

### 3.9 검색

**파일:** `frontend/js/websocket.js` `_setupSearchInput`

`searchStocks` 가 HTTP/네트워크로 실패하면 드롭다운은 `SEARCH FAILED`. `NO RESULTS FOUND` 는 200 이고 배열이 비었을 때만.

---

## 4. 수용 기준

구현 후 아래가 코드 또는 테스트로 확인돼야 한다.

1. `check_sync` 가 `{status:"error"}` 를 반환하면 `POST /api/broker/reconcile` 은 502 이고 `detail` 에 그 `message` 가 있다.
2. `GET /api/portfolio?reconcile=true` 는 그때도 200 이고, 루트에 `holdings` 와 `reconcile.status=="error"` 가 같이 있다. 프론트 초기 로드는 표를 그리면서 에러 토스트를 낸다.
3. 매수 응답 `status:"submitted"` 는 success 토스트가 아니고 info 토스트다. EXIT 는 `CLOSING...` 에 고정되지 않는다.
4. `scan_status` `error` 는 버튼을 `SYNC / REFRESH` 로 되돌리고 error 토스트를 낸다. `scanning_started` 직후 `Sync & Refresh Complete` 는 없다.
5. `GET /api/dashboard` 가 503이면 콘솔에 status 가 남고, 화면에 실패 토스트가 있다. `dashboard_data.json` 상대 fetch 는 없다.
6. 브로커 상태 요청이 reject 되면 헤더가 `UNREACHABLE` 이다. `SIMULATION` 이 아니다.
7. 가디언 상태 요청이 실패하면 토글 POST 가 나가지 않는다.
8. 422 본문 `detail:[{msg:"수량이 필요합니다"}]` 토스트에 `수량이 필요합니다` 가 있고 `[object Object]` 가 없다.
9. 차트를 A로 요청한 뒤 1초 안에 B로 바꾸면, A의 8초 타이머가 B 요청을 abort 하지 않는다.
10. WS close code 1008 reason `Forbidden Origin` 의 라벨은 `ORIGIN BLOCKED` 이다.
11. 기존 `tools_and_tests/test_false_fill` 계열, NAV/가드 테스트, `test_idempotent_kis_order` 가 이 패치 때문에 깨지지 않는다. 깨지면 가시성 쪽이 양보한다.

---

## 5. 테스트로 남길 것

새 파일 하나면 충분하다. 예: `tools_and_tests/test_api_visibility_errors.py`.

- TestClient 로 reconcile 502 / portfolio 200+`reconcile` 키. `check_sync` 는 monkeypatch.
- 500 핸들러 JSON 에 `detail` 문자열. (라우트에 고의 raise 가 없으면 핸들러 함수를 직접 호출해도 된다.)
- `/api/health` 피드 파일 부재 시 503. 있으면 200. KIS 를 mock 하지 말고 호출도 안 하는지 본다 (`default_kis_broker.get_overseas_balance` 가 안 불림).
- 대시보드 포트폴리오 주입이 raise 하면 응답에 `portfolio_error` 가 있고 200.

프론트는 단위 러너가 없다. `api.js` 의 `errorDetail` 과 차트 abort 캡처는 작은 node 단언을 새로 만들기보다, 위 백엔드 테스트 + 수동 체크리스트로 닫는다.

수동 (서버 재기동 후 Ctrl+F5):

1. 브로커 잔고가 실패하는 상태에서 SYNC → 완료 토스트가 아니라 실패 토스트. 표는 남음.
2. 대시보드 프로세스만 끄고 정적 페이지만 열 수 없으면, `/api/dashboard` 를 잠시 503으로 만들어 빈 화면+무로그가 아닌지 확인.
3. 종목을 8초 안에 두 번 눌러 두 번째 차트가 비지 않는지.
4. 가디언 배지가 UNKNOWN 인 동안 클릭해도 네트워크에 toggle POST 가 없는지.

---

## 6. 손댈 파일

| 파일 | 변경 |
|---|---|
| `server.py` | 500 `detail`, lifespan 로그, WS same-host Origin |
| `al_sangmoo/interfaces/api/routers/portfolio.py` | reconcile 필드. POST 동작은 submitted 유지 |
| `al_sangmoo/interfaces/api/routers/broker.py` | reconcile 502, broadcast 로그 |
| `al_sangmoo/interfaces/api/routers/dashboard.py` | 오류 필드, health, execution-logs 는 500 |
| `al_sangmoo/interfaces/api/routers/charts.py` | enrich/저장 실패 로그만 |
| `frontend/js/api.js` | `ApiError`, `errorDetail`, 폴백 삭제, 차트 타이머 |
| `frontend/js/ui.js` | 주문 토스트, 헬스 초기값, 섹터/피드 오류 문구 |
| `frontend/js/websocket.js` | SYNC, scan_status, 폴링, 배지, 검색, WS 라벨 |
| `frontend/js/chart.js` | timeout / 5xx / 404 표시 분리 |
| `frontend/index.html` 및 js import 쿼리 | `?v=4.4.7` |
| `tools_and_tests/test_api_visibility_errors.py` | 신규 |

`scanner.py` 메시지 계약은 유지. 퀀트 모듈, `kis_broker.py` TR, `reconciliation.py` 산식은 열지 마라. `check_sync` 의 error dict 모양만 소비한다.

---

## 7. 일부러 안 하는 것

- `ORDER_STATUS` 브로드캐스트 신설. 주문 가시성은 HTTP `message` + 토스트로 닫는다.
- 웹소켓 연결 중 5초 폴링으로 되돌리기. 15초면 된다.
- `/api/summary`, `/api/portfolio/history`, `/api/broker/order`, webhook 501. 터미널 UI가 안 부른다.
- 전략·스캔 알고리즘·가드레일 한도 변경.
