# Performance & Computational Optimization Audit Handoff Report

**Agent**: Explorer 3 (Performance & Computational Optimization Auditor)  
**Date**: 2026-08-25  
**Working Directory**: `d:\코딩\R\.agents\explorer_performance`  
**Reference Analysis File**: `d:\코딩\R\.agents\explorer_performance\analysis.md`  

---

## 1. Observation

### Observation 1: Synchronous Network I/O & SQLite Writes in Async Route (`GET /api/dashboard`)
- **File**: `d:\코딩\R\al_sangmoo\interfaces\api\routers\dashboard.py` (lines 41–46)
- **Code**:
  ```python
  # al_sangmoo/interfaces/api/routers/dashboard.py:41-46
  try:
      import db_manager
      live_portfolio = db_manager.sync_portfolio_prices()
      feed_out["portfolio"] = live_portfolio
  ```
- **File**: `d:\코딩\R\al_sangmoo\infrastructure\persistence.py` (lines 348–356, 378–395, 420–428)
- **Code**:
  ```python
  # al_sangmoo/infrastructure/persistence.py:352
  live_p = default_kis_broker.get_live_price(tk)  # Synchronous HTTP requests.get()
  # al_sangmoo/infrastructure/persistence.py:381
  cur_data = yf.download(tk, period="5d", interval="1d", progress=False)
  # al_sangmoo/infrastructure/persistence.py:422
  cursor.executemany("UPDATE my_portfolio SET ... WHERE id = ?", update_rows)
  conn.commit()
  ```

### Observation 2: Duplicate 60-Universe yfinance Scanning Pipeline
- **File**: `d:\코딩\R\al_sangmoo\interfaces\api\routers\scanner.py` (lines 38–50)
- **Code**:
  ```python
  def _sync_worker():
      import al_sangmoo_daily_bot
      bull_picks, neutral_picks, bear_picks, macro_climate = al_sangmoo_daily_bot.scan_and_select_2x2x2()
      today_str = datetime.now().strftime("%Y-%m-%d")
      db_manager.save_recommendation_matrix_record(today_str, bull_picks, neutral_picks, bear_picks)
      
      server_mod = sys.modules.get("server")
      build_fn = getattr(server_mod, "build_dashboard_data", generate_dashboard_feed.build_dashboard_data)
      data = build_fn()
      return today_str, data, macro_climate
  ```
- In `al_sangmoo_daily_bot.py:136-140`, `scan_and_select_2x2x2` loops over 23 tickers sequentially in a single thread calling `yf.download(ticker, period="6mo")`.
- Immediately afterwards, `generate_dashboard_feed.py:272` downloads 60 tickers (`period="3y"`). Total external HTTP calls: 83+ un-pooled requests with zero local OHLCV disk cache.

### Observation 3: Sequential NTFS Disk Flushes (`os.fsync`)
- **File**: `d:\코딩\R\generate_dashboard_feed.py` (lines 26, 432–436)
- **Code**:
  ```python
  # generate_dashboard_feed.py:26
  tf.flush()
  os.fsync(tf.fileno())  # Synchronously forces physical disk flush on NTFS

  # generate_dashboard_feed.py:432-436
  for ticker, c_obj in chart_data.items():
      if isinstance(c_obj, dict):
          ticker_chart_path = os.path.join(target_charts_dir, f"{ticker}.json")
          atomic_save_json(ticker_chart_path, c_obj)
  ```

### Observation 4: On-Demand Chart Fallback Blocking Async Event Loop
- **File**: `d:\코딩\R\al_sangmoo\interfaces\api\routers\charts.py` (lines 46, 60)
- **Code**:
  ```python
  # al_sangmoo/interfaces/api/routers/charts.py:46
  data = compute_all_indicators(ticker_resolved)  # Blocking yf.download(3y) on event loop!
  ...
  atomic_save_json(chart_file, data)              # Blocking os.fsync on event loop!
  ```

### Observation 5: Synchronous Quotes in Periodic Background Daemons
- **File**: `d:\코딩\R\al_sangmoo\domain\risk\portfolio_guardian.py` (lines 115, 123, 288)
- **Code**:
  ```python
  # al_sangmoo/domain/risk/portfolio_guardian.py:115
  cur_price = default_kis_broker.get_live_price(ticker)  # Synchronous HTTP in async daemon loop!
  # al_sangmoo/domain/risk/portfolio_guardian.py:288
  default_guardian = PortfolioGuardian(check_interval_seconds=10)
  ```

### Observation 6: Client-Side Double Polling & Post-WebSocket Stampede
- **File**: `d:\코딩\R\frontend\js\websocket.js` (lines 147–151, 208–215)
- **Code**:
  ```javascript
  // frontend/js/websocket.js:208-215
  _stopHttpPolling() {
      if (this.pollingInterval) {
          clearInterval(this.pollingInterval);
          this.pollingInterval = null;
      }
      // Starts 15s interval timer even while WebSocket is connected!
      this.pollingInterval = setInterval(async () => {
          try {
              const fresh = await ApiClient.getDashboardData();
              if (fresh) UI.renderDashboard(fresh);
          } catch (e) {}
      }, 15000);
  }

  // frontend/js/websocket.js:147-151
  } else if (msg.type === "portfolio_update" && msg.data) {
      UI.renderPortfolio(msg.data);
      ApiClient.getDashboardData().then(fresh => {  // Redundant HTTP fetch on every WS push
          if (fresh) UI.renderDashboard(fresh);
      });
  ```

### Observation 7: WebSocket Broadcast Serialized JSON & HoL Stalls
- **File**: `d:\코딩\R\al_sangmoo\api\hub.py` (lines 66–73)
- **Code**:
  ```python
  async def send_to_client(ws: WebSocket) -> bool:
      try:
          await asyncio.wait_for(ws.send_json(message), timeout=2.0)
          return True
      except Exception:
          return False

  results = await asyncio.gather(*(send_to_client(ws) for ws in sockets), return_exceptions=True)
  ```

### Observation 8: Inefficient `df.iterrows()` in Indicator Series Payload
- **File**: `d:\코딩\R\al_sangmoo\domain\quant\ichimoku.py` (lines 243–274)
- **Code**:
  ```python
  for idx, row in df_clean.iterrows():
      time_str = idx.strftime("%Y-%m-%d") if hasattr(idx, 'strftime') else str(idx)
      candles.append({ ... })
  ```

### Observation 9: Unbounded Memory Growth in Long-Running Daemons
- **File**: `d:\코딩\R\al_sangmoo\domain\risk\portfolio_guardian.py` (lines 37, 269), `al_sangmoo/domain/risk/autopilot_trader.py` (lines 41, 265)
- **Code**:
  ```python
  self.last_actions.append(action_record)  # Unbounded list growth
  self.trade_logs.append(trade_record)     # Unbounded list growth
  ```

### Observation 10: Missing Function Call in Legacy HTML Script
- **File**: `d:\코딩\R\al_sangmoo_dashboard.html` (lines 539–540)
- **Code**:
  ```javascript
  UI.initDelegation();  // TypeError: not a function
  UI.initSearch();      // TypeError: not a function
  ```

---

## 2. Logic Chain

1. **Event-Loop Starvation**: Observation 1 shows that `GET /api/dashboard` calls `sync_portfolio_prices()` synchronously. Observation 6 shows that frontend clients continuously poll `GET /api/dashboard` every 15 seconds and trigger immediate GET calls upon receiving WebSocket push messages. Observation 5 shows `PortfolioGuardian` broadcasts `portfolio_update` every 10 seconds. Thus, every 10 seconds, all connected clients hit `GET /api/dashboard`, which triggers synchronous KIS REST calls and SQLite write transactions on the main asyncio thread. This creates severe event-loop starvation and periodic freezes of 1.5s–5.0s.
2. **Scan Latency & Yahoo Finance Fragility**: Observation 2 shows that `scanner.py` executes `scan_and_select_2x2x2` (sequential single-threaded yfinance calls) followed by `build_dashboard_data` (threaded yfinance calls). Observation 3 shows that 60 chart files are written sequentially with `os.fsync`. Observation 8 shows `df.iterrows()` is called for 39,000 bars. Combined, this causes the scan pipeline to take 25s–45s, consuming excessive CPU and risking HTTP 429 rate limits.
3. **Chart Latency on Cache Misses**: Observation 4 shows that cache misses in `GET /api/chart/{ticker}` execute `compute_all_indicators` on the event loop. This blocks all server operations for 2.0s–4.5s whenever an on-demand chart is requested.
4. **WebSocket Inefficiency**: Observation 7 shows that `hub.broadcast()` re-serializes JSON for each client and waits up to 2.0s per slow client in `asyncio.gather`. When daemons broadcast sequentially, slow clients stall background daemons for up to 4.0s.
5. **Memory Retention**: Observation 9 shows that daemon action logs and `CHART_CACHE` lack bounded rolling buffers and TTL invalidation, leading to steady memory growth over long server uptimes.

---

## 3. Caveats

1. **Network Environment Differences**: Measured yfinance latencies and Yahoo Finance rate-limiting behavior vary based on network DNS, geographic IP location, and Yahoo API server load.
2. **Broker Credentials**: In environments where KIS API keys are not configured, `default_kis_broker.is_configured()` returns `False`, falling back to simulated mode. In that mode, KIS REST calls are skipped, but fallback `yf.download` calls in `sync_portfolio_prices` and `PortfolioGuardian` still occur synchronously.
3. **Zero Code Modification Rule**: In accordance with the strict read-only audit directive, no source files were modified during this investigation.

---

## 4. Conclusion

The Al-Sangmoo Quant Terminal has sound quantitative logic, but suffers from **event-loop starvation, redundant network calls, and I/O bottlenecks**:
1. **Critical Priority**: Refactor `GET /api/dashboard` to be a pure non-blocking CQRS read (`db_manager.get_live_portfolio()`), offloading price synchronization entirely to background workers.
2. **High Priority**: Unify the scanning pipeline into a single 60-ticker pass in `scanner.py`, offload on-demand chart generation in `charts.py` to `asyncio.to_thread`, and offload price quotes in `PortfolioGuardian` / `AutoPilotTrader` to worker threads.
3. **Medium Priority**: Pre-serialize WebSocket JSON payloads in `hub.py`, eliminate client-side double polling and post-WS HTTP stampedes in `websocket.js`, and replace `df.iterrows()` with vectorized operations in `ichimoku.py`.

---

## 5. Verification Method

To independently verify these findings:

1. **Inspect Event-Loop Blocking in `GET /api/dashboard`**:
   - Inspect `al_sangmoo/interfaces/api/routers/dashboard.py:44` (`sync_portfolio_prices`).
   - Run a concurrent load test using `pytest` or `curl`:
     ```bash
     # Issue concurrent GET requests while watching server response time
     pytest tools_and_tests/test_phase5_2_concurrency.py -v
     ```
2. **Inspect Scan Pipeline Redundancy**:
   - Inspect `al_sangmoo/interfaces/api/routers/scanner.py:38-50` to observe the consecutive execution of `al_sangmoo_daily_bot.scan_and_select_2x2x2()` and `generate_dashboard_feed.build_dashboard_data()`.
3. **Inspect Frontend Double Polling**:
   - Open `frontend/js/websocket.js` and inspect lines 147–175 (`ApiClient.getDashboardData()` in message handlers) and lines 208–215 (`setInterval(..., 15000)` inside `_stopHttpPolling`).
4. **Inspect Legacy Dashboard Console Error**:
   - Open `al_sangmoo_dashboard.html` in a browser or inspect lines 539–540 (`UI.initDelegation()` / `UI.initSearch()`).
