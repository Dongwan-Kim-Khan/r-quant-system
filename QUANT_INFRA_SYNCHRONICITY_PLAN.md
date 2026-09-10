# 알상무 퀀트 트레이딩 시스템 v2: 실시간 동기성·대시보드·연결성 인프라 보완 계획서
> **문서 버전**: 1.0.0  
> **대상 시스템**: `al-sangmoo-quant-bot` (FastAPI + SQLite WAL + KIS OpenAPI + WebSocket Hub + Frontend Terminal)  
> **연계 마일스톤**: M1(보안/세션), M2(브로커 복원력), M3(동기성), M4(서버 동시성), M6(회귀 검증)  
> **작성일**: 2026-08-27  

---

## 📌 1. Executive Summary (개요)

본 계획서는 알상무 퀀트 트레이딩 시스템의 **실시간 데이터 동기성(Real-time Synchronicity)**, **프론트엔드 대시보드 렌더링 성능(Dashboard Performance)**, **백엔드-증권사 API 연결 복원력(Backend-Broker Connectivity)**을 기관급 수준으로 고도화하기 위한 상세 인프라 개선 계획을 정의합니다.

글로벌 오픈소스 플랫폼(**Nautilus Trader, StockSharp, Howtrader, EliteQuant, QuantMuse**)의 검증된 인프라 아키텍처 패턴을 현재 시스템 코드베이스에 즉시 이식할 수 있도록 구체적인 코드와 함께 구성하였습니다.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        시스템 인프라 및 동기화 고도화 3대 핵심축                              │
├────────────────────────┬──────────────────────────────────┬────────────────────────────┤
│  1. 실시간 동기성      │  2. 대시보드 성능 & 시각화       │  3. 백엔드 연결성 & 복원력 │
│  (State Synchronicity) │  (Dashboard & Realtime UI)       │  (Connectivity Resilience) │
│  - 델타 이벤트 스트리밍│  - LWC 증분 캔들 업데이트        │  - 5단계 연결 상태 머신    │
│  - 자가 치유형 원장 대사│  - UI 링 버퍼 (메모리 누수 방지) │  - 멱등 요청 큐 & 오프라인 버퍼│
└────────────────────────┴──────────────────────────────────┴────────────────────────────┘
```

---

## 2. ⚡ 실시간 동기성 (Real-time Synchronicity) 보완 계획

### 2.1 델타 이벤트 스트리밍 (Delta Event Streaming)
* **벤치마크**: `Nautilus Trader` / `Howtrader`
* **현황 및 문제점**: 포지션 변동이나 가격 틱 발생 시 대시보드 전체 데이터(`/api/dashboard`)를 HTTP로 통째로 재조회하여 불필요한 백엔드/DB/네트워크 부하가 발생함 (SYNC-03).
* **개선 방안**: 상태가 변경된 필드만 웹소켓(`al_sangmoo/api/hub.py`)을 통해 전송하고, 프론트엔드가 해당 DOM 요소만 타겟 업데이트하도록 변경.

```python
# al_sangmoo/api/hub.py (델타 브로드캐스트 모듈)
from enum import Enum
from typing import Any, Dict
import json
from fastapi import WebSocket

class EventType(str, Enum):
    POSITION_DELTA = "POSITION_DELTA"
    GUARDIAN_ALERT = "GUARDIAN_ALERT"
    ORDER_STATUS = "ORDER_STATUS"
    SYSTEM_STATUS = "SYSTEM_STATUS"

class WebSocketBroadcastHub:
    def __init__(self):
        self._active_connections: list[WebSocket] = []

    async def broadcast_delta(self, event_type: EventType, data: Dict[str, Any]):
        """전체 재조회 없이 변경된 델타만 전송"""
        payload = json.dumps({
            "type": event_type.value,
            "data": data
        })
        for connection in list(self._active_connections):
            try:
                await connection.send_text(payload)
            except Exception:
                self._active_connections.remove(connection)
```

### 2.2 자가 치유형 원장 대사 엔진 (Self-Healing Reconciliation)
* **벤치마크**: `Nautilus Trader`
* **현황 및 문제점**: 네트워크 일시 단절이나 미체결 취소 시 로컬 SQLite 포지션과 KIS 실제 잔고 간의 불일치 가능성 존재.
* **개선 방안**: 주기적으로(60초마다 또는 장 시작 직후) 브로커 원장과 로컬 DB를 자동 대사하여 차이점을 동기화하고 감사 로그를 기록.

```python
# al_sangmoo/domain/reconciliation.py (원장 대사 엔진)
import asyncio
from typing import Dict, Any

class SelfHealingReconciler:
    def __init__(self, broker_client, persistence_manager, broadcast_hub):
        self.broker = broker_client
        self.db = persistence_manager
        self.hub = broadcast_hub

    async def reconcile_once(self) -> Dict[str, Any]:
        """브로커 잔고와 로컬 DB 잔고를 비교하여 불일치 시 자동 보정"""
        broker_positions = await asyncio.to_thread(self.broker.get_balance)
        local_positions = await self.db.get_active_positions_async()
        
        diffs = []
        for ticker, b_pos in broker_positions.items():
            l_pos = local_positions.get(ticker)
            if not l_pos or l_pos.qty != b_pos.qty or abs(l_pos.buy_price - b_pos.buy_price) > 1e-4:
                diffs.append({"ticker": ticker, "broker": b_pos, "local": l_pos})
                # 로컬 DB 원장 자동 교정 (Self-Healing)
                await self.db.sync_position_from_broker(ticker, b_pos)

        if diffs:
            await self.hub.broadcast_delta(EventType.POSITION_DELTA, {"reconciled": diffs})
        return {"status": "SUCCESS", "diff_count": len(diffs)}
```

---

## 3. 📊 대시보드 및 UI 시각화 (Dashboard & Realtime UI) 보완 계획

### 3.1 Lightweight Charts (LWC) 증분 캔들 업데이트
* **벤치마크**: `StockSharp` / `QuantMuse`
* **현황 및 문제점**: 새로운 봉이나 틱이 수신될 때 Chart.js 전체 인스턴스를 재렌더링하여 화면 깜빡임과 UI 프리징이 발생함.
* **개선 방안**: TradingView Lightweight Charts 라이브러리를 도입하여 변경된 캔들 1개만 `series.update()`로 증분 렌더링.

```javascript
// frontend/js/chart.js (LWC 증분 갱신 모듈)
let candleSeries = null;

export function initLightweightChart(containerElement) {
    const chart = LightweightCharts.createChart(containerElement, {
        width: containerElement.clientWidth,
        height: 400,
        layout: { background: { color: '#131722' }, textColor: '#d1d4dc' },
        grid: { vertLines: { color: '#242732' }, horzLines: { color: '#242732' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    });
    candleSeries = chart.addCandlestickSeries({
        upColor: '#ef5350', downColor: '#26a69a',
        borderUpColor: '#ef5350', borderDownColor: '#26a69a',
        wickUpColor: '#ef5350', wickDownColor: '#26a69a',
    });
    return chart;
}

export function onRealtimeBarUpdate(bar) {
    if (!candleSeries) return;
    // 차트 전체 렌더링 없이 단일 캔들만 증분 반영 (0.1ms 소요)
    candleSeries.update({
        time: bar.time, // Unix Timestamp
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close
    });
}
```

### 3.2 UI 링 버퍼(Ring Buffer) 기반 로그 렌더러
* **벤치마크**: `Howtrader`
* **현황 및 문제점**: 봇의 체결/스캔/가디언 로그가 DOM에 무제한 누적되어 브라우저 메모리 점유율 급증 및 탭 크래시 유발.
* **개선 방안**: 최대 300개의 고정 크기 링 버퍼를 프론트엔드에 두고, 초과 시 가장 오래된 노드를 DOM에서 즉시 제거.

```javascript
// frontend/js/ui_ring_buffer.js
export class UIRingBufferLogViewer {
    constructor(containerElement, maxLogs = 300) {
        this.container = containerElement;
        this.maxLogs = maxLogs;
    }

    appendLog(logEntry) {
        const logNode = document.createElement('div');
        logNode.className = `log-entry log-${logEntry.level.toLowerCase()}`;
        logNode.textContent = `[${logEntry.timestamp}] ${logEntry.message}`;

        this.container.appendChild(logNode);

        // 최대 개수 초과 시 가장 오래된 첫 번째 자식 노드 제거 (DOM 누수 방지)
        while (this.container.childElementCount > this.maxLogs) {
            this.container.removeChild(this.container.firstElementChild);
        }

        // 최하단으로 자동 스크롤
        this.container.scrollTop = this.container.scrollHeight;
    }
}
```

### 3.3 티커 요청 버전 관리 (Request Versioning Guard)
* **벤치마크**: `EliteQuant`
* **현황 및 문제점**: 종목을 빠르게 연속 클릭했을 때 비동기 네트워크 응답 순서가 뒤바뀌어 이전 종목 차트가 화면에 표시되는 레이스 컨디션(SYNC-04).
* **개선 방안**: 요청 시 증가하는 `request_seq_id`를 발급하고, 수신된 응답의 ID가 최신 ID와 일치할 때만 렌더링.

---

## 4. 🌐 백엔드-브로커 연결성 및 복원력 (Connectivity & Resilience) 보완 계획

### 4.1 5단계 연결 상태 머신 (Connection Lifecycle Hub)
* **벤치마크**: `Howtrader` / `Nautilus Trader`
* **현황 및 문제점**: KIS 토큰 만료나 웹소켓 세션 종료 시 사용자가 현재 봇의 정상 동작 여부를 즉시 파악하기 어려움.
* **개선 방안**: 5단계 상태(`DISCONNECTED`, `CONNECTING`, `CONNECTED`, `REAUTHENTICATING`, `ERROR`)를 정의하고 상태 변화 시 프론트엔드 헤더 뱃지(🟢/🟡/🔴)로 실시간 푸시.

```
┌────────────────────────────────────────────────────────┐
│               5-Stage Connection FSM                   │
│                                                        │
│  [DISCONNECTED] ──▶ [CONNECTING] ──▶ [CONNECTED]       │
│         ▲                                   │          │
│         │                                   ▼          │
│     [ERROR]    ◀───────────────── [REAUTHENTICATING]   │
└────────────────────────────────────────────────────────┘
```

### 4.2 멱등성 보장 주문 큐 (Idempotent Request Queue)
* **벤치마크**: `Nautilus Trader`
* **현황 및 문제점**: KIS 증권사 서버 504 Gateway Timeout 시 단순 재시도를 하면 실제로는 체결되었는데 중복 매수 주문이 나갈 위험(BRK-03).
* **개선 방안**: 모든 주문에 고유 `client_order_id` (UUIDv4)를 부여하고, 재시도 전 미체결/체결 내역 조회를 먼저 수행하는 멱등성 큐 적용.

```python
# al_sangmoo/infrastructure/idempotent_order_queue.py
import uuid
import asyncio
from typing import Dict, Any

class IdempotentOrderExecutor:
    def __init__(self, kis_broker):
        self.broker = kis_broker
        self._active_req_ids = set()

    async def execute_order_safely(self, ticker: str, side: str, qty: int, price: float) -> Dict[str, Any]:
        client_order_id = f"AL_{ticker}_{int(asyncio.get_event_loop().time()*1000)}_{uuid.uuid4().hex[:6]}"
        self._active_req_ids.add(client_order_id)
        
        try:
            # 1. KIS 주문 발송 (타임아웃 감시)
            result = await asyncio.wait_for(
                asyncio.to_thread(self.broker.place_order, ticker, side, qty, price, client_order_id),
                timeout=5.0
            )
            return result
        except asyncio.TimeoutError:
            # 2. 타임아웃 발생 시 맹목적 재발송 금지 -> 당일 체결/미체결 원장 조회 후 확인
            recheck = await asyncio.to_thread(self.broker.query_order_by_client_id, client_order_id)
            if recheck.get("filled") or recheck.get("pending"):
                return {"status": "SUCCESS_VIA_RECHECK", "order_id": client_order_id}
            raise RuntimeError(f"주문 응답 타임아웃: {ticker} 안전 취소 처리됨")
        finally:
            self._active_req_ids.discard(client_order_id)
```

### 4.3 비동기 스레드 풀 격리 (`asyncio.to_thread`)
* **벤치마크**: `EliteQuant` / `Nautilus Trader`
* **현황 및 문제점**: KIS 증권사 API와의 동기식 HTTP 통신이나 SQLite 디스크 I/O가 FastAPI의 단일 이벤트 루프를 블로킹하여 웹소켓 핑(Keepalive)이 끊어짐 (SEC-02, BRK-02).
* **개선 방안**: 모든 동기식 I/O 및 브로커 TR 호출을 독립된 워커 스레드로 격리하여 이벤트 루프 지연율(Loop Lag)을 0ms 수준으로 유지.

---

## 5. 📋 종합 보완 기능 매트릭스 (Summary Matrix)

| 영역 | 보완 기능 | 벤치마크 레포 | 적용 대상 파일 | 해결되는 이슈 및 기대 효과 |
| :--- | :--- | :--- | :--- | :--- |
| **동기성** | **델타 이벤트 스트리밍** | Nautilus Trader | `al_sangmoo/api/hub.py`, `server.py` | 불필요한 전체 대시보드 재요청 제거 (SYNC-03) |
| **동기성** | **자가 치유형 원장 대사** | Nautilus Trader | `al_sangmoo/domain/reconciliation.py` | 로컬 DB와 증권사 실제 잔고 간 100% 무결성 유지 |
| **대시보드** | **Lightweight Charts 캔들 증분 갱신** | StockSharp / QuantMuse | `frontend/js/chart.js` | 차트 재렌더링 깜빡임 제거 및 초저지연 시각화 |
| **대시보드** | **UI 링 버퍼 DOM 최적화** | Howtrader | `frontend/js/ui.js`, `websocket.js` | 장시간 실행 시 브라우저 메모리 누수 원천 차단 |
| **대시보드** | **티커 요청 버전 관리** | EliteQuant | `frontend/js/chart.js` | 빠른 종목 전환 시 비동기 레이스 컨디션 방지 (SYNC-04) |
| **연결성** | **5단계 연결 상태 머신 & 뱃지** | Howtrader | `server.py`, `kis_broker.py` | KIS 토큰 만료/웹소켓 단절 시 UI 실시간 가시성 확보 |
| **연결성** | **멱등성 보장 주문 큐** | Nautilus Trader | `al_sangmoo/infrastructure/kis_broker.py` | 504 Gateway 타임아웃 시 중복 주문 방지 (BRK-03) |
| **연결성** | **비동기 스레드 풀 격리** | EliteQuant | `al_sangmoo/interfaces/api/routers/` | 이벤트 루프 블로킹 제거로 WS 핑 끊김 방지 (SEC-02) |

---

## 6. 🚀 단계별 구현 로드맵 (Milestones)

```
[Phase 1: 브로커 복원력 & 멱등성] 
  └─ KIS Idempotent Order Queue + asyncio.to_thread 스레드 풀 격리 (M2 마일스톤 완수)

[Phase 2: 백엔드-프론트 델타 동기화] 
  └─ WebSocket Broadcast Hub 델타 이벤트 + 자가 치유형 Reconciler 엔진 (M3 마일스톤 완수)

[Phase 3: 대시보드 UI 고도화] 
  └─ TradingView Lightweight Charts + UI 링 버퍼 + 티커 버전 관리 적용
```

---

## 7. 🧪 검증 및 테스트 계획 (Verification Plan)

1. **동시성 및 멱등성 검증 (`test_phase5_2_concurrency.py`)**:
   * 타임아웃 주입(Mock Timeout) 환경에서 중복 주문이 발생하지 않는지 검증.
2. **웹소켓 델타 브로드캐스트 검증**:
   * 포지션 변동 시 전체 대시보드 재조회 없이 델타 메시지만 수신하여 UI가 정상 갱신되는지 확인.
3. **브라우저 메모리 프로파일링**:
   * 10,000개 이상의 로그 수신 후에도 브라우저 DOM 노드 수와 메모리 점유율이 일정하게 유지되는지 확인.

---
*본 계획서는 알상무 퀀트 봇 v2 프로젝트 표준 규격을 준수하여 작성되었습니다.*
