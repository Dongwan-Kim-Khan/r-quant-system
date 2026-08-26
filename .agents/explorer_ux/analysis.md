# Comprehensive Usability & Real-Time UX Audit Report (Domain 6)
**Target System**: Al-Sangmoo Quant Trading Cockpit & Bloomberg Dark Terminal Frontend  
**Auditor**: Explorer 6 (Dashboard Usability & UX Auditor)  
**Date**: 2026-08-25  
**Workspace Root**: `d:\코딩\R`  
**Mode**: STRICTLY READ-ONLY AUDIT (Zero Source Code Modified)  

---

## Executive Summary & UX Health Verdict

### Overall UX Health Score: **68 / 100 (GRADE: C+ / CONDITIONAL OPERATIONAL)**

The Al-Sangmoo Quant Terminal frontend exhibits a strong aesthetic foundation inspired by institutional Bloomberg Dark design principles (monochrome slate palettes, high information density, tabular structures, and zero emoji clutter). However, our rigorous usability and client-side code audit across `frontend/index.html`, `frontend/css/terminal.css`, `frontend/js/*.js`, and legacy mirrors (`al_sangmoo_dashboard.html`, `al_sangmoo_chart_system.html`) identified **10 critical usability defects, runtime desynchronizations, and trading safety risks**.

### Key Findings Summary:
1. **CRITICAL Runtime & DOM Desynchronization in Legacy Dashboard (`al_sangmoo_dashboard.html`)**: The legacy entry point (served at `/legacy` and root fallback) imports modular scripts that reference nonexistent DOM elements and call undefined functions (`UI.initDelegation`, `UI.initSearch`), triggering unhandled JavaScript runtime exceptions and leaving tactical 3-tier quant recommendations permanently frozen in loading states.
2. **Inverted WebSocket/HTTP Polling Lifecycle**: The client-side WebSocket client fails to terminate background HTTP polling upon socket connection, causing permanent duplicate background network traffic every 15 seconds while disabling rapid fallback upon socket failure.
3. **Misleading Connection Status Indicator**: Disconnected or errored WebSocket connections display an identical glowing green status pill (`"API ACTIVE (HTTP)"`), concealing socket failures from the trader.
4. **Unsafe 1-Click Order Execution & Blocking UI Modals**: The console quick buy button provides zero confirmation guardrails or double-click debounce protection, while other buy/sell actions utilize native browser `alert()` and `confirm()` modals that freeze the JavaScript event loop, halting WebSocket ping/pong heartbeats and chart updates.
5. **Disconnected Multi-Pane Crosshairs & Missing Intraday Timeframes**: Price and volume charts lack crosshair movement synchronization and hovering tooltips, while chart timeframes are restricted strictly to Daily and Weekly, lacking the 60m and 15m intraday intervals required for tactical swing entries.
6. **Market Color Convention Dissonance**: US color conventions (Green=Up/Red=Down) are hardcoded across all Korean and US equities without a user-selectable toggle, conflicting with standard Korean trading psychology (Red=Up/Blue=Down).

---

## Issue Severity Matrix

| Issue ID | Focus Area | Title | Severity | Impact Surface |
| :--- | :--- | :--- | :--- | :--- |
| **UX-01** | Legacy Compatibility | Total UI/DOM Desync & Runtime Exceptions in `al_sangmoo_dashboard.html` | **CRITICAL** | Legacy `/legacy` & fallback dashboard rendering |
| **UX-02** | WebSocket / Net | Inverted HTTP Polling Lifecycle Defeating WebSocket Push Architecture | **HIGH** | Client network I/O, server request load |
| **UX-03** | Status Indicators | Misleading Glowing Green Status Pill During WebSocket Disconnection | **HIGH** | Trader situational awareness & latency awareness |
| **UX-04** | Order Execution | Unsafe 1-Click Console Buy & Event-Loop-Freezing Native Browser Modals | **HIGH** | Trade execution safety, duplicate orders, socket heartbeat freeze |
| **UX-05** | Chart Interaction | Disconnected Volume Crosshairs, Missing Legends & Lack of Intraday (60m/15m) Timeframes | **HIGH** | Technical chart analysis, price inspection |
| **UX-06** | Visual Hierarchy | Hardcoded US Green/Red Color Palette Conflicting with Korean KRX Conventions | **MEDIUM** | Trader cognitive load & cross-market trade decisions |
| **UX-07** | Information Density | Left-Aligned Tabular Numerals Causing Ragged Decimal Scanning in Portfolio Tables | **MEDIUM** | Rapid risk scanning & portfolio readability |
| **UX-08** | 3-Slot Visualizer | Hardcoded Slot Capital Allocations ($2,500) & Severe Layout Shifts (CLS) | **MEDIUM** | 3-Slot portfolio visualization & tactical universe discoverability |
| **UX-09** | Responsiveness | Header Element Wrapping & Search Bar Distortion on 1080p / Laptop Viewports | **MEDIUM** | Header navigation, search usability on standard displays |
| **UX-10** | Session Persistence | Stale Candlestick History Retention Following Extended Browser Hibernation | **LOW** | Post-sleep data currency on active charts |

---

## Detailed Audit Findings Across 6 Focus Areas

```
================================================================================
AREA 1: BLOOMBERG DARK LAYOUT & VISUAL HIERARCHY
================================================================================
```

### UX-06: Hardcoded US Green/Red Color Palette Conflicting with Korean KRX Conventions
- **Severity**: Medium
- **Target Files**:
  - `d:\코딩\R\frontend\css\terminal.css` (Lines 11–16, 193–195)
  - `d:\코딩\R\frontend\js\chart.js` (Lines 59–62)
  - `d:\코딩\R\frontend\js\ui.js` (Lines 109–112, 147, 279)
- **Problematic Code Snippet**:
  ```css
  /* frontend/css/terminal.css:11-16 */
  --accent-green: #10b981;
  --accent-green-bg: rgba(16, 185, 129, 0.1);
  --accent-red: #ef4444;
  --accent-red-bg: rgba(239, 68, 68, 0.1);
  ```
  ```javascript
  /* frontend/js/chart.js:59-62 */
  this.candleSeries = this.mainChart.addCandlestickSeries({
      upColor: '#10b981', downColor: '#ef4444',
      borderUpColor: '#10b981', borderDownColor: '#ef4444',
      wickUpColor: '#10b981', wickDownColor: '#ef4444'
  });
  ```
  ```javascript
  /* frontend/js/ui.js:279 */
  const pnlColor = isPos ? 'var(--accent-green)' : 'var(--accent-red)';
  ```
- **UX Explanation & Trader Impact**:
  The Al-Sangmoo terminal scans a hybrid universe of US tech giants (NVDA, TSLA, PLTR) and Korean blue chips (`005930.KS` Samsung Electronics, `000660.KS` SK Hynix).
  In Korean financial markets (KRX), Red (`#ef4444` / `#f87171`) universally signifies positive price appreciation (양봉 / 상승) and Blue (`#3b82f6` / `#60a5fa`) signifies negative loss (음봉 / 하락). Conversely, US markets use Green for gains and Red for losses.
  By hardcoding US Green-Up/Red-Down across all panels, Korean traders monitoring domestic holdings (e.g. `005930.KS`) suffer cognitive dissonance: a red PnL badge is interpreted instinctively as a gain by domestic traders, while a green badge is interpreted as a loss. No color convention toggle or auto-detection by exchange suffix (`.KS` vs US) exists.
- **Remediation Strategy**:
  Implement a `MarketColorMode` system in CSS and JS (supporting `US_CONVENTION: Green=Up, Red=Down` and `KR_CONVENTION: Red=Up, Blue=Down`), switchable via a header toggle or auto-selected based on active ticker suffix:
  ```javascript
  // Proposed helper in ui.js / chart.js
  export function getThemeColors(isKoreanConvention = false) {
      if (isKoreanConvention) {
          return { up: '#ef4444', down: '#3b82f6', upBg: 'rgba(239,68,68,0.15)', downBg: 'rgba(59,130,246,0.15)' };
      }
      return { up: '#10b981', down: '#ef4444', upBg: 'rgba(16,185,129,0.15)', downBg: 'rgba(239,68,68,0.15)' };
  }
  ```

---

### UX-07: Left-Aligned Tabular Numerals Causing Ragged Decimal Scanning in Portfolio Tables
- **Severity**: Medium
- **Target Files**:
  - `d:\코딩\R\frontend\css\terminal.css` (Lines 184–188)
  - `d:\코딩\R\frontend\index.html` (Lines 174–189)
  - `d:\코딩\R\frontend\js\ui.js` (Lines 292–307)
- **Problematic Code Snippet**:
  ```css
  /* frontend/css/terminal.css:184-188 */
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th { background: #182234; color: #9ca3af; font-weight: 600; text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); font-size: 11px; text-transform: uppercase; }
  td { padding: 9px 12px; border-bottom: 1px solid var(--border); color: #e5e7eb; }
  ```
  ```javascript
  /* frontend/js/ui.js:294-306 */
  <tr>
      <td><strong class="ticker-pill ${isPos ? 'bull' : 'bear'}">${safeTicker}</strong></td>
      <td style="font-family:'JetBrains Mono';">$${safeBuyPrice.toFixed(2)}</td>
      <td style="font-family:'JetBrains Mono'; font-weight:700; color:${pnlColor};">$${safeCurrentPrice.toFixed(2)}</td>
      <td style="font-family:'JetBrains Mono'; font-weight:600; color:#38bdf8;">${safeQty}</td>
      <td style="color:${pnlColor}; font-weight:700; font-family:'JetBrains Mono';">${isPos ? '+' : ''}${pnlVal.toFixed(2)}%</td>
      <td style="color:#f87171; font-weight:700; font-family:'JetBrains Mono';">$${stopLossP.toFixed(2)}</td>
      <td style="color:#fbbf24; font-weight:700; font-family:'JetBrains Mono';">$${trailingP.toFixed(2)}</td>
      <td><button class="btn-exit-holding">EXIT</button></td>
  </tr>
  ```
- **UX Explanation & Trader Impact**:
  In professional financial terminals, all numeric quantitative metrics (Entry Price, Current Price, Shares, PnL %, Stop Loss, Trailing Floor) MUST be aligned strictly to the right (`text-align: right; font-variant-numeric: tabular-nums;`).
  Currently, `terminal.css` applies `text-align: left` to all `th` and `td` elements. This results in ragged decimal points (e.g. `$208.48` vs `$1,218.54` vs `$18.50`), forcing traders to visually zigzag to align dollar magnitudes and decimal fractions during fast market swings.
- **Remediation Strategy**:
  Define specific numeric column utility classes `.num-col` with `text-align: right` and `font-variant-numeric: tabular-nums lining-nums;` in `terminal.css` and align table headers accordingly:
  ```css
  .th-num, .td-num {
      text-align: right !important;
      font-family: 'JetBrains Mono', monospace;
      font-variant-numeric: tabular-nums;
  }
  ```

---

```
================================================================================
AREA 2: 3-SLOT VISUALIZER & TACTICAL RECOMMENDATIONS
================================================================================
```

### UX-08: Hardcoded Slot Capital Allocations ($2,500) & Severe Layout Shifts (CLS)
- **Severity**: Medium
- **Target Files**:
  - `d:\코딩\R\frontend\js\ui.js` (Lines 126–189, 191–248)
  - `d:\코딩\R\frontend\css\terminal.css` (Lines 315–330)
- **Problematic Code Snippet**:
  ```javascript
  /* frontend/js/ui.js:174 */
  // Inside renderSlotVisualizer() empty slot template:
  <div style="font-size:9px; color:#6ee7b7; margin-top:2px; font-family:'JetBrains Mono';">
      $2,500 (33.3%) ALLOCATED
  </div>
  ```
  ```javascript
  /* frontend/js/ui.js:141-187 */
  // When holdings.length = 3 and isBullRegime = false (maxSlots = 2):
  for (let i = 0; i < 3; i++) {
      if (i < holdings.length) {
          // All 3 render as occupied!
      } else if (i < maxSlots) { ... }
      else { ... }
  }
  ```
- **UX Explanation & Trader Impact**:
  1. **Hardcoded Sizing Values**: The empty slot card displays `$2,500 (33.3%) ALLOCATED` as a static string. If a trader's account equity is $15,000 or $5,000, the card shows misleading sizing data that disagrees with the KPI card balance above.
  2. **Over-Allocation Concealment in Bear Regimes**: In a Bear regime (`maxSlots = 2`), if the portfolio already holds 3 stocks from a previous Bull session, the visualizer renders all 3 as normal occupied slots without warning the trader that the account is violating the Bear regime 50% Cash Defense rule (holding 3 slots instead of the 2-slot cap).
  3. **Cumulative Layout Shift (CLS)**: Top Picks cards jump in height from ~45px (in empty/loading state) to ~135px when populated, pushing the active portfolio table down and shifting clickable elements.
  4. **Dropped Universe Candidates**: The backend feed contains full lists of Tier 1 Macro Leaders (`dual_consensus`), Tier 2 Pullbacks (`strat1_exclusive`), and Tier 3 Cloud Snipers (`strat2_exclusive`), but `frontend/index.html` only renders the top 2 items, completely hiding the rest of the 60-asset universe signals.
- **Remediation Strategy**:
  1. Dynamically calculate slot capital: `const slotCapUsd = (totalEquityUsd / maxSlots).toFixed(0);`
  2. Flag over-allocated slots in Bear mode with an amber warning border and `[OVER-ALLOCATED]` badge.
  3. Add a collapsible "View All Tactical Quant Candidates" drawer to inspect the full 60-stock tier rankings.

---

```
================================================================================
AREA 3: CHART TIMEFRAME SWITCHING & SYNCHRONIZATION
================================================================================
```

### UX-05: Disconnected Volume Crosshairs, Missing Hover Tooltips & Missing Intraday Timeframes
- **Severity**: High
- **Target Files**:
  - `d:\코딩\R\frontend\js\chart.js` (Lines 42–96, 136–157)
  - `d:\코딩\R\frontend\index.html` (Lines 231–241)
- **Problematic Code Snippet**:
  ```javascript
  /* frontend/js/chart.js:87-90 */
  // Synchronize timescale between price chart and volume chart
  this.mainChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (this.volumeChart && range) this.volumeChart.timeScale().setVisibleLogicalRange(range);
  });
  // MISSING: subscribeCrosshairMove synchronization between mainChart and volumeChart!
  ```
  ```html
  <!-- frontend/index.html:233-234 -->
  <button class="tf-btn active" id="tfDaily">1D DAILY</button>
  <button class="tf-btn" id="tfWeekly">1W WEEKLY</button>
  <!-- MISSING: 60m Intraday, 15m Intraday Timeframe Selectors -->
  ```
- **UX Explanation & Trader Impact**:
  1. **Crosshair Desynchronization**: Moving the mouse over the candlestick chart displays a vertical cursor line on the main chart, but the volume chart below remains completely stationary without a tracking crosshair. Traders inspecting volume bars at specific pivot candles cannot visually correlate volume spikes with price action.
  2. **Zero Historical Hover Readout**: There is no floating legend or OHLCV tooltip. Hovering over past bars gives zero feedback on historical Open, High, Low, Close, Kijun price, or Tenkan price at that point in time. The price in the header remains frozen at the current latest close.
  3. **Lack of Intraday Timeframes**: The Al-Sangmoo quant framework mandates checking 60m and 15m intraday charts for precise Kijun-sen bounce timing and trailing stop execution. The UI only provides 1D Daily and 1W Weekly buttons.
- **Remediation Strategy**:
  Implement crosshair syncing and a dynamic OHLCV legend bar:
  ```javascript
  // Synchronize crosshair movements across price and volume
  this.mainChart.subscribeCrosshairMove(param => {
      if (!param || !param.time) return;
      // Update floating legend with candle OHLC + Kijun + Tenkan
      const priceData = param.seriesData.get(this.candleSeries);
      if (priceData) {
          document.getElementById("chartIndicatorLegend").innerHTML = 
              `O: <span class="text-white">$${priceData.open.toFixed(2)}</span> ` +
              `H: <span class="text-white">$${priceData.high.toFixed(2)}</span> ` +
              `L: <span class="text-white">$${priceData.low.toFixed(2)}</span> ` +
              `C: <span class="text-white">$${priceData.close.toFixed(2)}</span>`;
      }
  });
  ```

---

```
================================================================================
AREA 4: 1-CLICK ORDER EXECUTION & GUARDRAILS UX
================================================================================
```

### UX-04: Unsafe 1-Click Console Buy & Event-Loop-Freezing Native Browser Modals
- **Severity**: High
- **Target Files**:
  - `d:\코딩\R\frontend\js\websocket.js` (Lines 338–354)
  - `d:\코딩\R\frontend\js\ui.js` (Lines 250–262, 311–327)
- **Problematic Code Snippet**:
  ```javascript
  /* frontend/js/websocket.js:338-354 */
  const btnExecBuy = document.getElementById("btnExecuteBuy");
  if (btnExecBuy) {
      btnExecBuy.onclick = async () => {
          const ticker = (document.getElementById("qbTicker")?.textContent || "NVDA").trim();
          const price = parseFloat(document.getElementById("qbBuyPrice")?.value || 0);
          const qty = parseFloat(document.getElementById("qbQty")?.value || 1);
          // ZERO CONFIRMATION GUARDRAIL!
          // ZERO BUTTON DISABLE / LOADING STATE!
          try {
              await ApiClient.buyStock(ticker, price, qty);
              alert(`[ORDER COMPLETED] ${ticker} ${qty} SH`); // BLOCKING MODAL!
              this.refreshData();
          } catch (e) {
              alert("Buy Failed: " + e.message); // BLOCKING MODAL!
          }
      };
  }
  ```
  ```javascript
  /* frontend/js/ui.js:251, 315 */
  if (!confirm(`[AL-SANGMOO v2 QUICK ORDER EXECUTION]\n...`)) return; // BLOCKING MODAL!
  if (!confirm(`Execute full position liquidation? (Price: $${price})`)) return; // BLOCKING MODAL!
  ```
- **UX Explanation & Trader Impact**:
  1. **Zero Guardrail on Manual Order Console**: Clicking the bottom "BUY SLOT" button immediately submits a live broker trade with zero confirmation prompt. An accidental click or touch tap will instantly execute an order.
  2. **No Double-Click Debounce / Loading State**: `btnExecBuy` is never disabled upon click. A trader rapidly double-clicking the button will fire two parallel HTTP requests, placing duplicate orders and exceeding capital slot limits.
  3. **Event Loop Starvation via Native `alert()` / `confirm()`**: Native browser dialogs (`window.alert`, `window.confirm`) synchronously block the single JavaScript thread. While the dialog is open:
     - The WebSocket 15-second heartbeat ping loop freezes.
     - Live market price updates and chart redraws are completely blocked.
     - Portfolio Guardian auto-stop alerts cannot be processed or rendered.
  4. **No Slippage / Price Drift Warning**: If market price deviates significantly from the input price while entering the order, no slippage warning is presented.
- **Remediation Strategy**:
  1. Replace all native `alert()` and `confirm()` calls with a non-blocking modern UI Modal & Toast notification system.
  2. Implement strict button disabling (`btn.disabled = true; btn.textContent = 'EXECUTING...'`) with automatic 2-second debounce protection.
  3. Include a double-confirmation modal displaying Order Value (USD/KRW), Estimated Fees, Stop-Loss Level (-4.0%), and Trailing Take-Profit Floor.

---

```
================================================================================
AREA 5: LONG-SESSION WEBSOCKET CONNECTION & RECONNECTION RESILIENCE
================================================================================
```

### UX-02: Inverted HTTP Polling Lifecycle Defeating WebSocket Push Architecture
- **Severity**: High
- **Target Files**:
  - `d:\코딩\R\frontend\js\websocket.js` (Lines 193–215)
- **Problematic Code Snippet**:
  ```javascript
  /* frontend/js/websocket.js:193-215 */
  _startHttpPolling() {
      if (this.pollingInterval) return;
      this.pollingInterval = setInterval(async () => {
          try {
              const fresh = await ApiClient.getDashboardData();
              if (fresh) UI.renderDashboard(fresh);
          } catch (e) {}
      }, 10000);
  },

  _stopHttpPolling() {
      if (this.pollingInterval) {
          clearInterval(this.pollingInterval);
          this.pollingInterval = null;
      }
      // BUG: Immediately re-creates polling interval during live WebSocket connection!
      this.pollingInterval = setInterval(async () => {
          try {
              const fresh = await ApiClient.getDashboardData();
              if (fresh) UI.renderDashboard(fresh);
          } catch (e) {}
      }, 15000);
  }
  ```
- **UX Explanation & Trader Impact**:
  When the WebSocket connects successfully (`socket.onopen`), it calls `_stopHttpPolling()`.
  However, `_stopHttpPolling()` clears the 10s timer and **immediately creates a new 15-second polling interval**!
  As a result:
  - HTTP polling is NEVER deactivated. Full `/api/dashboard` payloads (180KB+ JSON) are fetched every 15 seconds even while live WebSocket frames are streaming.
  - When the WebSocket disconnects, `_startHttpPolling()` is invoked, but since `this.pollingInterval` is already active (created by `_stopHttpPolling`), it returns early without switching to the aggressive 10-second reconnection fallback mode.
  - This wastes client and server CPU/network bandwidth, causes redundant DOM re-renders, and defeats the real-time event-driven architecture.
- **Remediation Strategy**:
  Refactor `_stopHttpPolling()` to purely clear and nullify `this.pollingInterval`:
  ```javascript
  _stopHttpPolling() {
      if (this.pollingInterval) {
          clearInterval(this.pollingInterval);
          this.pollingInterval = null;
      }
  },
  _startHttpPolling(intervalMs = 10000) {
      if (this.pollingInterval) return;
      this.pollingInterval = setInterval(async () => {
          try {
              const fresh = await ApiClient.getDashboardData();
              if (fresh) UI.renderDashboard(fresh);
          } catch (e) {
              console.warn("[HTTP Polling] Failed:", e);
          }
      }, intervalMs);
  }
  ```

---

### UX-03: Misleading Glowing Green Status Pill During WebSocket Disconnection
- **Severity**: High
- **Target Files**:
  - `d:\코딩\R\frontend\js\websocket.js` (Lines 98–110, 217–225)
  - `d:\코딩\R\frontend\index.html` (Lines 50–53)
- **Problematic Code Snippet**:
  ```javascript
  /* frontend/js/websocket.js:98-110 */
  this.socket.onclose = (e) => {
      console.warn("[WS] Connection closed. Code:", e.code, "Reason:", e.reason);
      this.isSocketConnected = false;
      this._stopHeartbeat();
      this._setStatus("API ACTIVE (HTTP)", "#10b981"); // GREEN PILL ON DISCONNECT!
      this._startHttpPolling();
      this._scheduleReconnect();
  };

  this.socket.onerror = (err) => {
      console.warn("[WS] Socket error:", err);
      this._setStatus("API ACTIVE (HTTP)", "#10b981"); // GREEN PILL ON ERROR!
  };
  ```
- **UX Explanation & Trader Impact**:
  When the real-time WebSocket connection drops (e.g. backend restart, network blip, CSWSH rejection), the status pill in the top-right header remains **bright green** (`#10b981`) and displays `"API ACTIVE (HTTP)"`.
  Green universally communicates healthy real-time streaming. A trader glancing at the header assumes sub-second live tick streaming is active, unaware that WebSocket push has failed and data is now lagged by 15-second polling intervals.
- **Remediation Strategy**:
  Implement clear, color-coded 4-state indicator UX:
  1. `LIVE WS` (Glowing Emerald `#10b981`): WebSocket active, sub-second latency.
  2. `RECONNECTING (#N)` (Pulsing Orange `#f59e0b`): Socket dropped, exponential backoff active.
  3. `POLLING (HTTP)` (Static Amber `#d97706`): Socket unavailable, running on 10s HTTP polling fallback.
  4. `DISCONNECTED / OFFLINE` (Red `#ef4444`): Network unreachable.

---

### UX-10: Stale Candlestick History Retention Following Extended Browser Hibernation
- **Severity**: Low
- **Target Files**:
  - `d:\코딩\R\frontend\js\websocket.js` (Lines 23–46)
  - `d:\코딩\R\frontend\js\chart.js` (Lines 99–130)
- **Problematic Code Snippet**:
  ```javascript
  /* frontend/js/websocket.js:40-46 */
  checkAndReconnect() {
      if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
          console.log("[WS] Tab focused/resumed - ensuring active connection...");
          this.reconnectAttempts = 0;
          this.connect();
          // MISSING: ChartEngine.loadChart(UI.currentSelectedTicker) is NOT triggered!
      }
  }
  ```
- **UX Explanation & Trader Impact**:
  When a trader wakes a laptop from sleep or un-hibernates a background browser tab after 2 hours, `checkAndReconnect()` re-establishes the WebSocket connection and fetches the general dashboard JSON.
  However, the active TradingView chart series is NOT reloaded. The candlestick chart continues displaying stale historical bars from hours prior until the trader manually clicks on a different ticker in the watchlist.
- **Remediation Strategy**:
  In `checkAndReconnect()`, trigger `ChartEngine.loadChart(UI.currentSelectedTicker)` to refresh the active ticker's OHLCV series immediately upon tab focus/wake-up.

---

```
================================================================================
AREA 6: MOBILE & VIEWPORT RESPONSIVENESS
================================================================================
```

### UX-09: Header Element Wrapping & Search Bar Distortion on 1080p / Laptop Viewports
- **Severity**: Medium
- **Target Files**:
  - `d:\코딩\R\frontend\index.html` (Lines 19–57)
  - `d:\코딩\R\frontend\css\terminal.css` (Lines 34–43, 332–356)
- **Problematic Code Snippet**:
  ```html
  <!-- frontend/index.html:19-57 -->
  <header>
      <div class="brand">...</div> <!-- ~300px -->
      <div class="search-wrapper" style="position:relative; flex:1; max-width:440px; margin:0 16px;">...</div> <!-- ~440px -->
      <div class="header-actions"> <!-- ~840px minimum content width -->
          <div id="kisBrokerStatusBox">...</div>
          <span id="guardianBadge">...</span>
          <span id="autopilotBadge">...</span>
          <div class="server-status-pill">...</div>
          <button class="btn-action scan">RUN SCAN</button>
          <button class="btn-action">REFRESH</button>
      </div>
  </header>
  ```
- **UX Explanation & Trader Impact**:
  The total linear width required by header elements without wrapping is `300px + 440px + 840px = 1580px`.
  On standard laptops (1366x768, 1440x900) or 1080p displays with split-screen windows, the header wraps into 2 or 3 chaotic rows.
  The search bar is compressed awkwardly, daemon badges overlap, and the "RUN SCAN" / "REFRESH" buttons are pushed below the visual fold of the header bar.
- **Remediation Strategy**:
  1. Group Guardian and Autopilot badges into a compact single-pill toggle dropdown (`DAEMONS: 2/2 ON`).
  2. Abbreviate KIS status to an icon dot + `KIS` on viewports < 1400px.
  3. Utilize CSS media queries to convert action buttons into icon buttons on compact widths.

---

```
================================================================================
CRITICAL COMPATIBILITY: LEGACY DASHBOARD (al_sangmoo_dashboard.html)
================================================================================
```

### UX-01: Total UI/DOM Desync & Runtime Exceptions in Legacy Dashboard (`al_sangmoo_dashboard.html`)
- **Severity**: Critical
- **Target Files**:
  - `d:\코딩\R\al_sangmoo_dashboard.html` (Lines 199–250, 260–283, 340–369, 539–540)
  - `d:\코딩\R\frontend\js\ui.js` (Lines 25–42)
  - `d:\코딩\R\server.py` (Lines 196–198, 207–212)
- **Problematic Code Snippet**:
  ```javascript
  /* al_sangmoo_dashboard.html:539-540 */
  // Inside initApp():
  UI.initDelegation(); // CRASH: TypeError: UI.initDelegation is not a function!
  UI.initSearch();     // CRASH: TypeError: UI.initSearch is not a function!
  ```
  ```javascript
  /* frontend/js/ui.js:37-41 */
  // UI.renderDashboard() expects frontend/index.html IDs:
  this.renderMacro(data.macro, isBullRegime); // Looks for #macroStanceBadge
  this.renderKPIs(data.kpis, data.portfolio, isBullRegime); // Looks for #kpiTotalEquityUsd (Missing in legacy!)
  this.renderSlotVisualizer(data.portfolio, isBullRegime); // Looks for #slotVisualizerGrid (Missing in legacy!)
  this.renderTopPicks(data.top_conviction_pick); // Looks for #topPicksContainer (Missing in legacy!)
  ```
  ```html
  <!-- al_sangmoo_dashboard.html contains legacy elements that are NEVER populated: -->
  <div id="strat1ExclusiveCards"></div> <!-- Remains empty forever -->
  <div id="dualConsensusCards"></div>   <!-- Remains empty forever -->
  <div id="strat2ExclusiveCards"></div> <!-- Remains empty forever -->
  <tbody id="dailyRecHistoryBody"></tbody> <!-- Shows "기록 로딩 중..." forever -->
  <div id="kpiCount">0</div> <!-- Shows 0 forever -->
  <div id="kpiInvested">$0.00</div> <!-- Shows $0.00 forever -->
  ```
- **UX Explanation & Trader Impact**:
  When users navigate to `/legacy` or when `server.py` falls back to `al_sangmoo_dashboard.html`:
  1. `initApp()` crashes immediately because `UI.initDelegation()` and `UI.initSearch()` do not exist in `frontend/js/ui.js` (they were refactored into `websocket.js:TerminalApp`).
  2. `ui.js:renderDashboard()` attempts to update DOM element IDs (`slotVisualizerGrid`, `topPicksContainer`, `kpiTotalEquityUsd`) that do not exist in `al_sangmoo_dashboard.html`.
  3. Conversely, the elements that DO exist in `al_sangmoo_dashboard.html` (`strat1ExclusiveCards`, `dualConsensusCards`, `strat2ExclusiveCards`, `dailyRecHistoryBody`, `kpiCount`, `kpiInvested`, `kpiEval`, `kpiPnl`, `msiScoreBadge`, `msiNeedle`) are never populated by `ui.js`.
  4. The result is a completely broken, frozen zombie dashboard where search does not respond, click handlers fail, and all quant data displays as "Loading...".
- **Remediation Strategy**:
  1. Deprecate the divergent monolithic HTML or update `al_sangmoo_dashboard.html` to share the exact canonical DOM structure of `frontend/index.html`.
  2. Redirect `/legacy` permanently (`HTTP 301 / 307`) to `/` to ensure all users access the verified, modular Bloomberg dark frontend.

---

## Prioritized Remediation Plan

```
+-----------------------------------------------------------------------------------+
| PRIORITY 1: IMMEDIATE TRADING SAFETY & CRITICAL COMPATIBILITY (0 - 24 Hours)       |
+-----------------------------------------------------------------------------------+
| 1. Fix UX-01: Update al_sangmoo_dashboard.html bootstrapper or redirect /legacy    |
|    permanently to frontend/index.html to eliminate runtime JS exceptions.          |
| 2. Fix UX-04: Implement non-blocking confirmation modal and 2s debounce on         |
|    btnExecuteBuy to prevent accidental/duplicate live orders.                      |
| 3. Fix UX-02: Remove rogue 15s polling instantiation in websocket.js:_stopHttpPoll |
+-----------------------------------------------------------------------------------+
| PRIORITY 2: REAL-TIME UX & CONNECTION CLARITY (24 - 72 Hours)                     |
+-----------------------------------------------------------------------------------+
| 4. Fix UX-03: Implement 4-state status indicators (Green/Orange/Amber/Red).        |
| 5. Fix UX-05: Add crosshair synchronization across price & volume charts + legend. |
| 6. Fix UX-08: Dynamically compute slot capital allocations in 3-Slot Visualizer.   |
| 7. Fix UX-10: Invalidate and reload active chart on tab focus/wake-up.             |
+-----------------------------------------------------------------------------------+
| PRIORITY 3: POLISH & CROSS-MARKET USABILITY (1 - 2 Weeks)                         |
+-----------------------------------------------------------------------------------+
| 8. Fix UX-06: Implement Market Color Convention toggle (KRX Red/Blue vs US Grn/Red)|
| 9. Fix UX-07: Standardize numeric table alignment (text-align: right, tabular-nums)|
| 10. Fix UX-09: Responsive header badge compacting for < 1440px viewports.          |
+-----------------------------------------------------------------------------------+
```
