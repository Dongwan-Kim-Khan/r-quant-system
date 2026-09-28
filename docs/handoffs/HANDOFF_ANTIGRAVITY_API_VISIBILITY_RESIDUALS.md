# Handoff: API 가시성 잔여 결함 3건 → Antigravity

**Date:** 2026-09-22 (KST)  
**From:** Cursor (독립 검수)  
**To:** Antigravity  
**Status:** **이 세 가지만 고친다. 이미 통과한 가시성 패치는 다시 열지 마라.**  
**Related:** `HANDOFF_ANTIGRAVITY_API_VISIBILITY_AND_ERRORS.md`

검수 결과 수용 기준 1~11과 회귀 130개는 통과했다. 아래 세 경로는 그 보고에 빠져 있고, 아직 실패를 정상처럼 둔다.  
프론트를 고치면 `index.html`과 `frontend/js/*.js` import 쿼리를 **`?v=4.4.8`** 로 한 단계만 올린다. 서버는 실행하지 마라.

---

## 0. What you must do / must not do

### Do

1. 가디언·오토파일럿 **조회 실패**가 헤더를 `LIVE`로 되돌리지 않게 한다.
2. 매크로 게이지 조회 예외를 로그하고, 붙일 게이지가 없으면 `macro_error`를 응답에 남긴다.
3. `POST /api/portfolio/sync` 가 `check_sync` 실패를 `status:"success"` 로 감싸지 않게 한다.

### Do not

1. C-2 상수, NAV 산식, 체결/접수 구분, KIS TR 을 바꾸지 마라.
2. `POST /api/broker/reconcile` 의 502, `GET /api/portfolio?reconcile=true` 의 200+`reconcile` 키를 되돌리거나 다시 감싸지 마라. 그 계약은 이미 맞다.
3. 가디언 토글이 조회 실패 때 POST 를 안 보내는 분기를 느슨하게 하지 마라. 배지 문구 `UNKNOWN` / 팝오버 `상태 조회 실패` 도 유지한다.
4. 500 본문에 `str(exc)` 를 넣지 마라. 클라이언트에 스택·키·계좌번호를 넣지 마라.
5. 이 작업 밖으로 리팩터하지 마라.

---

## 1. 헤더가 조회 실패를 LIVE 로 지운다

**파일:** `frontend/js/ui.js` `updateSystemHealth`

조회 실패 시 `websocket.js` `setBadgeState("unreachable")` 가 `systemHealthState.guardian` / `autopilot` 에 `"unreachable"` 을 넣는다. 배지는 else 라 `UNKNOWN`, 팝오버 else 라 `상태 조회 실패` 가 맞다.

전체 사인은 실패를 `"unknown"` 만 본다.

```587:591:frontend/js/ui.js
const isGuardianFail = st.guardian === "unknown";
const isAutopilotFail = st.autopilot === "unknown";
```

KIS 는 이미 `unreachable` 과 `unknown` 을 실패로 친다. 가디언·오토파일럿만 빠진다.  
웹소켓이 `CONNECTED` 이고 KIS 가 `live` 이면, 가디언 상태 API 가 죽은 뒤에도 헤더가 `LIVE` / 초록이 된다.

### 고칠 것

`isGuardianFail` / `isAutopilotFail` 에 `"unreachable"` 을 포함한다. KIS 와 같은 규칙이다.

- `"unknown"` 또는 `"unreachable"` → 전체 사인 YELLOW, 라벨 `WARN`.
- `"standby"` / `false` 는 지금처럼 기능 OFF (DISABLED / STANDBY). 실패와 섞지 마라.
- `"active"` / `true` 는 ON.
- 팝오버 문구는 그대로: unknown → `확인 중`, unreachable → `상태 조회 실패`, standby → OFF, active → ON.
- `websocket.js` 의 토글 차단은 수정하지 마라.

---

## 2. 매크로 게이지 예외가 조용히 사라진다

**파일:** `al_sangmoo/interfaces/api/routers/dashboard.py` `get_live_macro_gauges`, `get_dashboard_data` 의 매크로 블록

`get_live_macro_gauges()` 는 예외를 `pass` 하고 캐시(없으면 `None`)만 반환한다. 바깥 `try` 는 이 함수가 예외를 다시 던지지 않으므로 `macro_error` 도 로그도 없다.

```33:35:al_sangmoo/interfaces/api/routers/dashboard.py
    except Exception:
        pass
    return CACHED_MACRO_GAUGES
```

### 고칠 것

- 그 `except` 에서 `logger.exception` 으로 남긴다. 응답 본문에 예외 문자열을 넣지 마라.
- 예외 뒤 캐시가 있으면 그 캐시를 반환한다. `macro_error` 는 붙이지 마라. 30초 TTL 안의 캐시 적중은 실패가 아니다.
- 반환값이 비어 있으면 (`None` 또는 dict 가 아님) `get_dashboard_data` 가 `feed_out["macro_error"] = "매크로 게이지를 불러오지 못했습니다"` 를 넣는다.
- 게이지를 실제로 붙였으면 `feed_out.pop("macro_error", None)`.
- 프론트 `renderDashboard` 는 이미 `macro_error` 를 한 번만 토스트한다. UI 는 고치지 마라.

---

## 3. `POST /api/portfolio/sync` 가 실패를 성공으로 감싼다

**파일:** `al_sangmoo/interfaces/api/routers/portfolio.py` `sync_portfolio_with_broker`

터미널 SYNC 버튼은 `/api/broker/reconcile` 을 쓴다. 이 엔드포인트는 화면이 안 부르지만, `check_sync` 가 `{status:"error"}` 를 줘도 항상 이렇게 반환한다.

```122:129:al_sangmoo/interfaces/api/routers/portfolio.py
    audit_report = check_sync(auto_calibrate=auto_calibrate)
    p_data = get_live_portfolio()
    await hub.broadcast("portfolio_update", p_data)
    return {
        "status": "success",
        "report": audit_report,
        "portfolio": p_data
    }
```

`report.status` 가 error 여도 최상위 `status` 는 `"success"` 이고 HTTP 는 200 이다.

### 고칠 것

`/api/broker/reconcile` 과 같은 판정이다. `check_sync` 반환이 dict 이고 `status != "success"` 이면:

- HTTP **502**
- 본문 `{ "detail": <report.message 또는 "대조 실패">, "report": <report>, "portfolio": <현재 원장> }`
- `logger.warning` 에 message
- 원장 브로드캐스트는 해도 된다. 실패 HTTP 를 200 으로 바꾸지만 마라.
- `check_sync` 가 예외를 던지면 잡지 마라. 전역 500 계약(`detail` 고정 한글)으로 나간다.
- 성공일 때만 200 `{ "status": "success", "report", "portfolio" }`.

`GET /api/portfolio` 는 이 함수와 따로다. 거기 200 + `reconcile` 키는 유지한다.

---

## 4. 테스트

`tools_and_tests/test_api_visibility_errors.py` 에 추가한다.

1. `check_sync` 가 `{status:"error", message:"sync failed"}` 이면 `POST /api/portfolio/sync` 는 502, `detail` 은 그 message, 본문에 `portfolio` 가 있다. 최상위 `status` 가 `"success"` 이면 실패다.
2. `check_sync` 가 `{status:"success"}` 이면 200 이고 최상위 `status` 는 `"success"`.
3. 대시보드: `get_live_macro_gauges` 가 `None` 을 반환하면 200 본문에 `macro_error` 가 있다. 게이지 dict 를 반환하면 `macro_error` 가 없다. KIS 잔고 조회는 호출하지 마라.

가디언 헤더 색은 JS 라 이 파일로 안 돈다. `isGuardianFail` / `isAutopilotFail` 조건에 `"unreachable"` 이 들어가는지만 코드로 맞으면 된다.

기존 `test_api_visibility_errors.py` 와 아래를 깨지 마라.

- `tools_and_tests/test_dashboard_portfolio_sync.py`
- `tools_and_tests/test_dashboard_launch_ssot.py`
- `tools_and_tests/test_synchronicity_ssot.py`
- `tools_and_tests/test_cash_proxy_sleeve_guardian.py`
- `tools_and_tests/test_idempotent_kis_order.py`

---

## 5. 손댈 파일

| 파일 | 변경 |
|---|---|
| `frontend/js/ui.js` | guardian/autopilot fail 에 `unreachable` 포함 |
| `frontend/index.html` 및 js import | `?v=4.4.8` (ui.js 를 고친 경우에만) |
| `al_sangmoo/interfaces/api/routers/dashboard.py` | 게이지 예외 로그 + `macro_error` |
| `al_sangmoo/interfaces/api/routers/portfolio.py` | `/sync` 만 502. buy/sell/GET 은 그대로 |
| `tools_and_tests/test_api_visibility_errors.py` | 위 두 백엔드 케이스 |

`websocket.js`, `api.js`, `broker.py`, `server.py` 는 열지 마라.
