# Al-Sangmoo Phase 5.2 Concurrency & Backend Architecture Analysis Report

**Target Scope**: R1 (Non-Blocking Background Scanning & Event-Loop Protection) & R2 (Parallelized WebSocket Broadcasting & Slow-Client Shielding)  
**Author**: Survey Explorer 1 (Backend & Concurrency Focus)  
**Date**: 2026-08-23  
**Working Directory**: `d:\코딩\Playground\al_sangmoo_project\.agents\explorer_survey_1`  

---

## 1. Executive Summary

During the Phase 5.2 survey investigation, the backend architecture (`server.py`) and real-time WebSocket communication layer (`al_sangmoo/api/hub.py`) were thoroughly inspected to evaluate event-loop responsiveness, concurrency protection, slow-client shielding, and real-time broadcasting mechanics.

### Key Findings:
1. **R1 / CONC-01 / SEC-V09 (Event-Loop Starvation & Scan Storms in `server.py`)**:
   - The `/api/scan_now` endpoint in `server.py` (lines 364–383) runs synchronous, heavy network and CPU pipelines (`al_sangmoo_daily_bot.scan_and_select_2x2x2` and `generate_dashboard_feed.build_dashboard_data`) directly within the async route handler on the main asyncio event loop thread.
   - This completely blocks the single-threaded asyncio event loop for 10 to 30+ seconds.
   - During this freeze, all other HTTP requests (`/api/portfolio`, `/api/dashboard`, `/api/chart/{ticker}`), WebSocket connections, and WebSocket `ping`/`pong` heartbeats are starved and unresponsive.
   - There is no concurrency locking or atomic `is_scanning` state flag. Rapid or concurrent hits to `POST /api/scan_now` spawn overlapping scan storms, leading to resource exhaustion, Yahoo Finance rate limiting, and SQLite transaction lock contention.

2. **R2 / CONC-02 / SEC-V03 (WebSocket Hub Concurrency & Slow-Client Shielding in `al_sangmoo/api/hub.py`)**:
   - `WebSocketBroadcastHub` currently releases the internal lock during network I/O (`sockets = list(self.active_connections)`) and uses `asyncio.gather` with `asyncio.wait_for(..., timeout=2.0)`.
   - While the basic timeout and gather structure exists from Phase 5.1, several critical edge cases require hardening:
     - Dead/slow connection identification must cleanly handle all exceptions and BaseExceptions via index/zip mapping.
     - Timed-out/dead client sockets should be closed gracefully in the background to prevent OS-level TCP handle / socket leaks.
     - Integration with `scan_status` progress and completion event types is required for end-to-end real-time UI synchronization.

3. **Test Infrastructure Readiness**:
   - All 26 existing tests across `test_phase1_hardening.py`, `test_phase2_modular.py`, `test_phase3_backtester.py`, `test_phase4_execution.py`, and `test_phase5_1_security.py` pass 100% GREEN (10.75s).
   - A dedicated 4-tier verification test suite `tools_and_tests/test_phase5_2_concurrency.py` will be constructed to validate sub-200ms non-blocking responses, zero event-loop lag during scans, slow-client isolation (<2.5s broadcast under stalled clients), and automatic socket reclamation.

---

## 2. In-Depth Investigation: Requirement 1 (R1) - Non-Blocking Background Scanning & Event-Loop Protection

### 2.1 Code Location & Current Implementation
- **File**: `server.py`
- **Lines**: 364–383

```python
@app.post("/api/scan_now")
async def trigger_scan_now():
    try:
        import al_sangmoo_daily_bot
        bull_picks, neutral_picks, bear_picks, macro_climate = al_sangmoo_daily_bot.scan_and_select_2x2x2()
        today_str = datetime.now().strftime("%Y-%m-%d")
        db_manager.save_recommendation_matrix_record(today_str, bull_picks, neutral_picks, bear_picks)
        data = build_dashboard_data()
        global CHART_CACHE
        CHART_CACHE = data.get("charts", {})
        await hub.broadcast("live_feed_update", data)
        return {
            "status": "success",
            "message": f"{today_str} 실시간 3-Gate 스캔 & 대시보드 갱신 완료!",
            "macro_stance": macro_climate.get("macro_stance") if macro_climate else "NORMAL"
        }
    except Exception as e:
        print(f"[Scan Error] {e}", file=sys.stderr)
        return {"status": "error", "message": "스캔 중 내부 오류가 발생했습니다."}
```

### 2.2 Call Chain & Execution Bottlenecks
1. **`al_sangmoo_daily_bot.scan_and_select_2x2x2()`** (`al_sangmoo_daily_bot.py:103-240`):
   - Iterates through `scan_list` (priority tickers + 23 `UNIVERSE` tickers).
   - Sequentially calls `yf.download(ticker, period="6mo", interval="1d", progress=False)` for each ticker.
   - Performs heavy rolling indicator calculations (`calculate_indicators`: 9-day Tenkan, 26-day Kijun, 52-day SpanB, SMA20, SMA60, Vol_SMA20).
   - **Cost**: 23+ synchronous blocking HTTP calls + pandas computation = ~5–15 seconds.
2. **`generate_dashboard_feed.build_dashboard_data()`** (`generate_dashboard_feed.py:430-520`):
   - Spawns `ThreadPoolExecutor(max_workers=12)` on 60 watchlist tickers.
   - Synchronously waits on `list(executor.map(compute_all_indicators, WATCHLIST))`.
   - **Cost**: ~5–15 seconds of blocking wait on the main event loop thread.
3. **Impact on Event Loop**:
   - Total blocking duration: **10–30+ seconds**.
   - Because `trigger_scan_now()` is an async coroutine running on the main event loop, running blocking synchronous code blocks the entire thread.
   - During these 30 seconds:
     - `GET /api/portfolio` hangs.
     - `GET /api/dashboard` hangs.
     - `GET /api/chart/{ticker}` hangs.
     - WebSocket `{"event": "ping"}` messages cannot receive `pong` responses, causing client timeouts.
     - New incoming WebSocket handshakes are stalled.

### 2.3 Concurrency Flaws & Security Risks (CONC-01, SEC-V09)
- **Zero Concurrency Lock**: There is no guard against overlapping scan invocations.
- **Scan Storm Vulnerability**: If a user clicks "스캔 시작" multiple times or multiple connected clients send `POST /api/scan_now` concurrently, multiple 30-second heavy scanning pipelines run simultaneously.
- **Cascading Failure**: Multiple parallel scans trigger Yahoo Finance rate limiting (HTTP 429), CPU 100% saturation, memory bloat, and SQLite database write locks (`sqlite3.OperationalError: database is locked`).
- **Violated SLA**: The client request waits 30+ seconds before receiving an HTTP response instead of the required non-blocking `< 200ms` SLA.

### 2.4 Proposed Architecture & Technical Solution for R1

```
+------------------------+
| POST /api/scan_now     |
+-----------+------------+
            |
            v
+------------------------+        _is_scanning == True
| Atomic Lock Check      |-------------------------------------> Return HTTP 200
| async with _scan_lock  |                                       {"status": "already_scanning", ...}
+-----------+------------+
            | _is_scanning == False -> Set True
            v
+------------------------------------+
| 1. Return HTTP 200 (<200ms)        |
|    {"status": "scanning_started"}  |
+------------------------------------+
            |
            v (Asynchronous Background Task)
+------------------------------------+
| 2. Broadcast WebSocket Event:      |
|    hub.broadcast("scan_status",    |
|      {"status": "started"})        |
+------------------------------------+
            |
            v
+------------------------------------+
| 3. await asyncio.to_thread(        |
|      execute_sync_scan_pipeline    |
|    )                               |
|    - scan_and_select_2x2x2()       |
|    - save_recommendation_matrix()  |
|    - build_dashboard_data()        |
+------------------------------------+
            |
            v
+------------------------------------+
| 4. Update Memory Caches:           |
|    CHART_CACHE, load_feed_cache()  |
+------------------------------------+
            |
            v
+------------------------------------+
| 5. Broadcast Completion Events:    |
|    - hub.broadcast(                |
|        "live_feed_update", data)   |
|    - hub.broadcast("scan_status",  |
|        {"status": "completed"})    |
+------------------------------------+
            | (finally)
            v
+------------------------------------+
| 6. async with _scan_lock:          |
|      _is_scanning = False          |
+------------------------------------+
```

#### Detailed Implementation Blueprint for `server.py`:
```python
# Concurrency State Guard
_is_scanning: bool = False
_scan_lock = asyncio.Lock()

async def _run_background_scan_pipeline():
    global _is_scanning, CHART_CACHE
    try:
        await hub.broadcast("scan_status", {
            "status": "started",
            "message": "백그라운드 3-Gate 스캔이 시작되었습니다."
        })
        
        def _sync_worker():
            import al_sangmoo_daily_bot
            bull_picks, neutral_picks, bear_picks, macro_climate = al_sangmoo_daily_bot.scan_and_select_2x2x2()
            today_str = datetime.now().strftime("%Y-%m-%d")
            db_manager.save_recommendation_matrix_record(today_str, bull_picks, neutral_picks, bear_picks)
            data = build_dashboard_data()
            return today_str, data, macro_climate

        today_str, data, macro_climate = await asyncio.to_thread(_sync_worker)
        CHART_CACHE = data.get("charts", {})
        load_feed_cache()
        
        # Broadcast full dashboard refresh & completion status
        await hub.broadcast("live_feed_update", data)
        await hub.broadcast("scan_status", {
            "status": "completed",
            "message": f"{today_str} 실시간 3-Gate 스캔 & 대시보드 갱신 완료!",
            "macro_stance": macro_climate.get("macro_stance") if macro_climate else "NORMAL"
        })
    except Exception as exc:
        print(f"[Background Scan Error] {exc}", file=sys.stderr)
        await hub.broadcast("scan_status", {
            "status": "error",
            "message": "스캔 실행 중 내부 오류가 발생했습니다."
        })
    finally:
        async with _scan_lock:
            _is_scanning = False

@app.post("/api/scan_now")
async def trigger_scan_now():
    global _is_scanning
    async with _scan_lock:
        if _is_scanning:
            return JSONResponse(
                status_code=200,
                content={
                    "status": "already_scanning",
                    "message": "백그라운드 스캔이 이미 진행 중입니다.",
                    "is_scanning": True
                }
            )
        _is_scanning = True
        
    asyncio.create_task(_run_background_scan_pipeline())
    return {
        "status": "scanning_started",
        "message": "백그라운드 스캔이 시작되었습니다.",
        "is_scanning": True
    }
```

---

## 3. In-Depth Investigation: Requirement 2 (R2) - Parallelized WebSocket Broadcasting & Slow-Client Shielding

### 3.1 Code Location & Current Implementation
- **File**: `al_sangmoo/api/hub.py`
- **Lines**: 11–76

```python
class WebSocketBroadcastHub:
    def __init__(self, max_connections: int = MAX_CONNECTIONS):
        self.max_connections = max_connections
        self.active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> bool:
        async with self._lock:
            if len(self.active_connections) >= self.max_connections:
                if hasattr(websocket, "close"):
                    try:
                        await websocket.close(code=1008, reason="Connection limit exceeded")
                    except Exception:
                        pass
                return False

            if hasattr(websocket, "accept"):
                try:
                    await websocket.accept()
                except Exception:
                    return False
            self.active_connections.append(websocket)
            print(f"[WebSocket Hub] Client connected. Active clients: {len(self.active_connections)}")
            return True

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        print(f"[WebSocket Hub] Client disconnected. Active clients: {len(self.active_connections)}")

    async def broadcast(self, event_type: str, data: Any = None) -> None:
        """
        Broadcasts an event message to all connected clients using non-blocking dispatch with timeouts.
        Automatically prunes disconnected clients.
        """
        message = {
            "event": event_type,
            "data": data or {},
            "timestamp": time.time()
        }
        
        async with self._lock:
            sockets = list(self.active_connections)
            
        if not sockets:
            return

        async def send_to_client(ws: WebSocket):
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=2.0)
                return None
            except Exception:
                return ws

        results = await asyncio.gather(*(send_to_client(ws) for ws in sockets), return_exceptions=True)
        dead_connections = [ws for ws in results if ws is not None and not isinstance(ws, Exception)]

        if dead_connections:
            async with self._lock:
                for dead in dead_connections:
                    if dead in self.active_connections:
                        self.active_connections.remove(dead)
```

### 3.2 Concurrency & Performance Analysis (CONC-02, SEC-V03)

| Mechanism | Current Implementation | Evaluation | Hardening Recommendation |
| :--- | :--- | :--- | :--- |
| **Lock Holding During I/O** | `sockets = list(self.active_connections)` taken under `_lock`, released before `gather`. | **Compliant** | Maintain this pattern. |
| **Concurrent Client Dispatch** | `asyncio.gather(*(send_to_client(ws) for ws in sockets), return_exceptions=True)` | **Compliant** | All clients dispatched simultaneously in parallel. |
| **Slow-Client Timeout (HoL Blocking)** | `asyncio.wait_for(ws.send_json(message), timeout=2.0)` | **Compliant** | Caps any stalled client send at 2.0s maximum. |
| **Dead Connection Filtering** | `[ws for ws in results if ws is not None and not isinstance(ws, Exception)]` | **Minor Flaw** | If `send_to_client` raises an uncaught BaseException or `gather` captures an exception, the dead socket could be skipped. Use `zip(sockets, results)` for deterministic mapping. |
| **Socket Cleanup on Timeout / Error** | Dead connections removed from `self.active_connections` only. | **Resource Leak Risk** | Stalled/timed-out WebSockets should have `asyncio.create_task(self._safe_close(ws))` invoked to close TCP socket handles and prevent descriptor leaks. |
| **Reconnection & Slot Reclamation** | In `connect()` and `disconnect()`, slots are reclaimed accurately under `_lock`. | **Compliant** | Verified up to 50 concurrent connections in Phase 5.1 test suite. |

### 3.3 Hardened Implementation Blueprint for `hub.py`
```python
    async def _safe_close(self, ws: WebSocket, code: int = 1000, reason: str = "") -> None:
        if hasattr(ws, "close"):
            try:
                await ws.close(code=code, reason=reason)
            except Exception:
                pass

    async def broadcast(self, event_type: str, data: Any = None) -> None:
        message = {
            "event": event_type,
            "data": data or {},
            "timestamp": time.time()
        }
        
        async with self._lock:
            sockets = list(self.active_connections)
            
        if not sockets:
            return

        async def send_to_client(ws: WebSocket) -> bool:
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=2.0)
                return True
            except Exception:
                return False

        results = await asyncio.gather(*(send_to_client(ws) for ws in sockets), return_exceptions=True)
        
        dead_connections = [ws for ws, success in zip(sockets, results) if success is not True]
        if dead_connections:
            async with self._lock:
                for dead in dead_connections:
                    if dead in self.active_connections:
                        self.active_connections.remove(dead)
                        
            # Cleanly close dead sockets asynchronously in background
            for dead in dead_connections:
                asyncio.create_task(self._safe_close(dead, code=1011, reason="Broadcast timeout/error"))
```

---

## 4. Existing Test Suite Status & Phase 5.2 Test Strategy

### 4.1 Verification of Existing Tests
All 26 existing tests were executed via `pytest tools_and_tests/`:
- `test_phase1_hardening.py` (4 tests) - **PASSED**
- `test_phase2_modular.py` (4 tests) - **PASSED**
- `test_phase3_backtester.py` (4 tests) - **PASSED**
- `test_phase4_execution.py` (4 tests) - **PASSED**
- `test_phase5_1_security.py` (10 tests) - **PASSED**
- **Total: 26 passed, 0 failures in 10.75s.**

### 4.2 Test Plan for `test_phase5_2_concurrency.py`

| Test Category | Target Functionality | Verification Criteria |
| :--- | :--- | :--- |
| **Tier 1.1: Non-Blocking Scan SLA** | `POST /api/scan_now` | Response status 200, latency `< 200ms`, payload contains `"status": "scanning_started"`. |
| **Tier 1.2: Scan Storm Prevention** | `POST /api/scan_now` (concurrent double-trigger) | 1st request initiates scan; 2nd request immediately returns `"status": "already_scanning"`. Only 1 background scan worker runs. |
| **Tier 1.3: Event-Loop Liveness** | Event loop responsiveness during scan | While background scan is running, 10 concurrent requests to `/api/portfolio` and WebSocket pings respond in `< 50ms`. |
| **Tier 2.1: WebSocket Parallelism** | `hub.broadcast()` with 5 slow clients (sleep 10s) and 10 fast clients | Broadcast completes in `< 2.5s` total. All 10 fast clients receive messages within `< 10ms`. |
| **Tier 2.2: Stalled Client Pruning** | `hub.broadcast()` | All 5 slow clients are cleanly removed from `active_connections`. Active connection count drops from 15 to 10. |
| **Tier 3.1: End-to-End WebSocket Events** | `scan_status` & `live_feed_update` | Connected WebSocket client receives sequence: `{"event": "scan_status", "data": {"status": "started"}}` -> `{"event": "live_feed_update"}` -> `{"event": "scan_status", "data": {"status": "completed"}}`. |
| **Tier 3.2: Error Recovery & State Reset** | Background scan failure recovery | If backend scan throws exception, `_is_scanning` state is guaranteed to reset to `False` (`finally:` clause), allowing subsequent scans to proceed normally. |
| **Tier 4.1: Capacity & Stress Broadcast** | 50 concurrent active WebSockets | Rapid burst broadcast of 20 messages completes with 0 deadlocks and 100% delivery across all 50 clients. |

---

## 5. Risk Assessment & Recommendations

1. **State Leak / Lock Deadlock Risk**:
   - *Risk*: If an unhandled exception occurs inside `_run_background_scan_pipeline()`, `_is_scanning` might stay `True` forever, permanently disabling the scan button.
   - *Mitigation*: Ensure `_is_scanning = False` is strictly wrapped inside a `finally:` block with `async with _scan_lock:`.

2. **Database Concurrency during Background Scan**:
   - *Risk*: When `db_manager.save_recommendation_matrix_record` runs in `asyncio.to_thread`, concurrent portfolio reads (`get_live_portfolio`) might access SQLite.
   - *Mitigation*: SQLite WAL mode (`PRAGMA journal_mode = WAL;`) and `PRAGMA busy_timeout = 30000;` are already enabled in `persistence.py`, allowing concurrent readers and single writer without blocking.

3. **Memory Cache Consistency**:
   - *Risk*: `CHART_CACHE` and `FEED_CACHE` updates in the background worker must be visible to subsequent `GET /api/dashboard` and `GET /api/chart/{ticker}` requests.
   - *Mitigation*: Update `CHART_CACHE` and invoke `load_feed_cache()` in the background worker immediately after computation completes, before broadcasting `live_feed_update`.

---
**Report status**: Investigation complete. Ready for implementation planning and verification test authoring.
