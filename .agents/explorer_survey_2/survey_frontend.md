# Phase 5.1 Security Hardening Survey: Frontend & UI Codebase

**Target Architecture**: Al-Sangmoo Institutional Quant Trading Terminal  
**Investigation Date**: 2026-08-22  
**Investigator**: Explorer 2 (Frontend & UI Security Specialist)  
**Survey Document**: `survey_frontend.md`  

---

## 1. Executive Summary

This survey provides an exhaustive security investigation of the frontend and UI layer of the Al-Sangmoo Quant Trading Platform for **Phase 5.1 Security Hardening**. The primary focus is **Requirement 1 (R1: Stored & DOM XSS Remediation - SEC-V01)** along with associated client-side vulnerabilities identified in the master security audit.

### Key Survey Findings:
1. **Absence of HTML Sanitization**: No `escapeHtml()` or DOM sanitization utility currently exists across `al_sangmoo_dashboard.html` or any mirror template.
2. **Critical Stored & DOM XSS Attack Surfaces**:
   - `h.exit_advice` in `portfolioTableBody`: User input from `POST /api/portfolio/sell/{id}` (`SellOrder.reason`) flows unvalidated into SQLite and is concatenated directly into `.innerHTML` across client dashboards and real-time WebSocket feeds.
   - `item.ticker`, `item.name`, `item.sector`, `item.score`, `item.streak_days` in `renderItemCard()`: Interpolated into `.innerHTML` across 3 recommendation columns (Tier 1, Tier 2, Tier 3).
   - `dailyRecHistoryBody`: Daily historical recommendation tables dynamically inject tickers and stance strings into `.innerHTML`.
   - `searchDropdown`: Stock search autocomplete directly injects `item.ticker`, `item.name_kr`, `item.name_en`, and `item.market` into `.innerHTML`.
   - `macroTailwindSectors`: Injected into `.innerHTML` via string template concatenation.
3. **Inline `onclick` JavaScript Injection Vectors**: Multiple dynamic UI elements construct inline `onclick="selectStock('${item.ticker}', ...)"` and `onclick="sellHolding(${h.id}, ...)"` via template strings. If any data field contains quotes (e.g. `AAPL');alert(1);//`), arbitrary JavaScript executes immediately.
4. **Unvalidated URI Protocols**: `sLink.href = data.macro.url` lacks protocol validation, allowing `javascript:` pseudo-protocols if upstream macro data is compromised.
5. **Missing Subresource Integrity (SRI)**: Lightweight Charts library is loaded from `unpkg.com` without cryptographic hash verification (`integrity` attribute) or `crossorigin="anonymous"`.

---

## 2. Frontend File Inventory & Mirror Map

An audit of the repository identified **10 `.html` files**, categorized below:

| File Path | Size (Bytes) | SHA-256 (Prefix) | Role / Classification | Security Status |
|---|---|---|---|---|
| `al_sangmoo_dashboard.html` | 133,323 | `f38773479379` | **Primary Production Terminal** (Served by FastAPI `GET /`) | **Vulnerable to XSS / Inline Onclick** |
| `html_dashboards/01_R상무_통합_퀀트_대시보드.html` | 133,323 | `f38773479379` | **Exact Mirror Copy** of `al_sangmoo_dashboard.html` | **Vulnerable to XSS / Inline Onclick** |
| `html_dashboards/01_알상무_통합_퀀트_대시보드.html` | 133,323 | `f38773479379` | **Exact Mirror Copy** of `al_sangmoo_dashboard.html` | **Vulnerable to XSS / Inline Onclick** |
| `HTML_대시보드_모음/01_R상무_통합_퀀트_대시보드.html` | 133,323 | `f38773479379` | **Exact Mirror Copy** of `al_sangmoo_dashboard.html` | **Vulnerable to XSS / Inline Onclick** |
| `al_sangmoo_chart_system.html` | 21,143 | `e25de48bc272` | Standalone Mock Chart Prototype (No live backend fetch) | Low Risk (Static mock) |
| `html_dashboards/02_알상무_차트_시스템.html` | 21,731 | `cf1150c24c36` | Exact Mirror Copy of `al_sangmoo_chart_system.html` (CRLF line endings) | Low Risk (Static mock) |
| `daily_reports/briefing_2026-08-18.html` | 14,334 | `3349c788d1b0` | Static Daily Email Report Archive (No scripts) | Static HTML |
| `daily_reports/briefing_2026-08-19.html` | 14,107 | `4b9ae76100e8` | Static Daily Email Report Archive (No scripts) | Static HTML |
| `daily_reports/briefing_2026-08-20.html` | 13,742 | `0bbc9e36d99c` | Static Daily Email Report Archive (No scripts) | Static HTML |
| `daily_reports/briefing_2026-08-21.html` | 14,733 | `dfd04f6450f8` | Static Daily Email Report Archive (No scripts) | Static HTML |

### Mirror Synchronization Architecture:
- `server.py:45` binds `DASHBOARD_HTML = os.path.join(BASE_DIR, "al_sangmoo_dashboard.html")`.
- `tools_and_tests/build_standalone_dashboard.py` modifies `al_sangmoo_dashboard.html` by baking `window.EMBEDDED_DASHBOARD_DATA` into the `<head>` section.
- **Requirement**: Any security remediation applied to `al_sangmoo_dashboard.html` must be replicated across the 3 mirror files in `html_dashboards/` and `HTML_대시보드_모음/`.

---

## 3. Comprehensive Analysis of R1: Stored & DOM XSS Remediation

### 3.1. Missing `escapeHtml()` Sanitization Utility
Currently, `al_sangmoo_dashboard.html` contains **no** HTML escape function.
All dynamic strings from JSON responses and WebSocket payloads are directly evaluated inside ES6 template literals `${...}` and assigned to `element.innerHTML`.

#### Proposed Robust `escapeHtml()` Utility:
```javascript
/**
 * Strict HTML character escaping utility to neutralize XSS payloads.
 * Converts special HTML characters into standard XML/HTML entities.
 */
function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
```

---

### 3.2. Detailed Catalog of Dynamic DOM Interpolation Points

#### Vector 1: Portfolio Holdings Table (`h.exit_advice` & `h.ticker`)
- **Location**: `al_sangmoo_dashboard.html` Lines 1705–1723 (`renderDashboardData`) and Lines 1948–1966 (`renderPortfolioOnly`).
- **Mechanism**:
  ```javascript
  pBody.innerHTML = holdings.map(h => {
      const pnlColor = h.pnl_pct >= 0 ? '#10b981' : '#ef4444';
      return `
          <tr>
              <td><strong style="font-family:'JetBrains Mono'; color:#f8fafc;">${h.ticker}</strong></td>
              <td style="font-family:'JetBrains Mono';">$${Number(h.buy_price).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})}</td>
              <td style="font-family:'JetBrains Mono'; font-weight:600; color:#38bdf8;">${Number(h.quantity).toLocaleString('en-US', {maximumFractionDigits:4})}</td>
              <td style="font-family:'JetBrains Mono';">$${Number(h.current_price).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})}</td>
              <td style="color:${pnlColor}; font-weight:700; font-family:'JetBrains Mono';">${h.pnl_pct >= 0 ? '+' : ''}${h.pnl_pct.toFixed(2)}%</td>
              <td>
                  <div style="display:flex; justify-content:space-between; align-items:center;">
                      <span style="font-size:11px; color:#9ca3af;">${h.exit_advice || 'Holding'}</span>
                      <button onclick="sellHolding(${h.id}, ${h.current_price})" style="background:#dc2626; color:#fff; border:none; padding:2px 8px; border-radius:2px; font-size:10px; cursor:pointer;">Exit</button>
                  </div>
              </td>
          </tr>
      `;
  }).join('');
  ```
- **Vulnerability**:
  - `h.exit_advice` contains data persisted via `record_portfolio_sell()` in `persistence.py:212`: `f"청산 완료 ({pnl_pct:+.2f}%) - {reason}"`.
  - When an attacker submits `POST /api/portfolio/sell/{id}` with payload `{"sell_price": 100, "reason": "<script>alert(1)</script>"}`, this string is saved to SQLite and broadcast to all connected dashboards via WebSocket (`portfolio_update`) or returned on `/api/dashboard`.
  - The unescaped payload executes immediately in the victim's browser session.
- **Remediation**:
  - Escape `h.ticker`: `escapeHtml(h.ticker)`
  - Escape `h.exit_advice`: `escapeHtml(h.exit_advice || 'Holding')`
  - Remove inline `onclick="sellHolding(...)"` and replace with data attributes `data-holding-id="${Number(h.id)}" data-current-price="${Number(h.current_price)}"` with a class `btn-exit-holding`.

---

#### Vector 2: 3-Tier Quant Matrix Cards (`renderItemCard`)
- **Location**: `al_sangmoo_dashboard.html` Lines 1587–1649.
- **Mechanism**:
  ```javascript
  return `
      <div class="rec-item ${isActive}" data-ticker="${item.ticker}" style="${walletCardStyle}" onclick="selectStock('${item.ticker}', ${item.price})">
          <div style="flex:1;">
              <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:2px;">
                  <div style="display:flex; align-items:center; gap:5px; flex-wrap:wrap;">
                      <span class="rec-item-title" style="color:${colorTheme};">${item.ticker}</span>
                      <span class="rec-item-sub" style="font-size:11px; font-weight:600;">${item.name}</span>
                      ${sectorTag}
                      <span class="origin-badge ${originClass}" style="font-size:9px; padding:1px 5px;">${originText}</span>
                      ${streakBadge}
                      ${walletBadge}
                  </div>
                  <div class="rec-item-price" style="color:${colorTheme};">$${Number(item.price).toFixed(2)}</div>
              </div>
              <div class="rec-item-sub" style="font-size:10px; display:flex; justify-content:space-between; align-items:center; margin-top:2px; font-family:'JetBrains Mono';">
                  <span>SCORE: <strong style="color:#f8fafc;">${item.score}pt</strong> | 26D: <strong style="color:#38bdf8;">${item.kijun_gap >= 0 ? '+' : ''}${Number(item.kijun_gap).toFixed(1)}%</strong> | VOL: <strong>${item.vol_ratio}%</strong></span>
                  <span>${obvBadge}${flowRatioText}</span>
              </div>
              <div style="display:flex; justify-content:space-between; align-items:center; font-size:10px; font-family:'JetBrains Mono'; margin-top:4px; padding-top:4px; border-top:1px dashed #1e293b;">
                  <span style="color:#94a3b8;">TP(+15%): <strong style="color:#34d399;">$${targetPrice}</strong></span>
                  <span style="color:#94a3b8;">SL(-4%): <strong style="color:#f87171;">$${stopPrice}</strong></span>
              </div>
          </div>
      </div>
  `;
  ```
- **Vulnerabilities**:
  - `item.ticker`, `item.name`, `item.sector`, `item.score`, `item.vol_ratio`, `item.streak_days` are interpolated into HTML without escaping.
  - `onclick="selectStock('${item.ticker}', ${item.price})"` is vulnerable to attribute breakout and JS execution if `item.ticker` contains single quotes or quotes.
- **Remediation**:
  - Escape all dynamic text values using `escapeHtml()`.
  - Replace `onclick` with `data-ticker="${escapeHtml(item.ticker)}"` and `data-price="${Number(item.price || 0)}"`, handling clicks via event delegation on parent containers (`strat1ExclusiveCards`, `dualConsensusCards`, `strat2ExclusiveCards`).

---

#### Vector 3: Daily Recommendations History Table (`dailyRecBody`)
- **Location**: `al_sangmoo_dashboard.html` Lines 1727–1763.
- **Mechanism**:
  ```javascript
  const t1 = (h.tier1 || []).map(x => `<span class="ticker-pill" style="..." onclick="selectStock('${x.ticker}', ${x.price || 0})">${x.ticker}</span>`).join(' ');
  // ...
  return `
      <tr>
          <td style="font-family:'JetBrains Mono'; font-weight:700; color:#cbd5e1;">${h.date}</td>
          <td><div style="display:flex; gap:6px; flex-wrap:wrap;">${t1 || '<span style="color:#64748b; font-size:11px;">-</span>'}</div></td>
          <td><div style="display:flex; gap:6px; flex-wrap:wrap;">${t2 || '<span style="color:#64748b; font-size:11px;">-</span>'}</div></td>
          <td><div style="display:flex; gap:6px; flex-wrap:wrap;">${t3 || '<span style="color:#64748b; font-size:11px;">-</span>'}</div></td>
          <td><span style="font-family:'JetBrains Mono'; font-size:11px; color:#fbbf24; font-weight:600;">${macroStance}</span></td>
      </tr>
  `;
  ```
- **Vulnerabilities**:
  - `x.ticker`, `h.date`, `macroStance`, `msiScore` are unescaped.
  - `onclick="selectStock('${x.ticker}', ...)"` inline event handlers.
- **Remediation**:
  - Sanitize all strings with `escapeHtml()`.
  - Use `data-ticker="${escapeHtml(x.ticker)}" data-price="${Number(x.price || 0)}"` and event delegation on `dailyRecHistoryBody`.

---

#### Vector 4: Search Autocomplete Suggestions Dropdown (`searchDropdown`)
- **Location**: `al_sangmoo_dashboard.html` Lines 2061–2088 (`renderSearchSuggestions`).
- **Mechanism**:
  ```javascript
  suggestions.forEach((item) => {
      html += `
          <div class="search-suggestion-item" 
               onclick="selectSearchedStock('${item.ticker}')"
               style="padding:8px 12px; border-bottom:1px solid #1e293b; display:flex; justify-content:space-between; align-items:center; cursor:pointer; transition:background 0.15s;">
              <div>
                  <span style="font-family:'JetBrains Mono'; font-weight:700; color:#38bdf8; font-size:12px;">${item.ticker}</span>
                  <span style="font-size:12px; color:#f8fafc; margin-left:8px;">${item.name_kr || item.name_en}</span>
              </div>
              <div style="font-size:10px; color:#94a3b8; background:#1e293b; padding:2px 6px; border-radius:2px; font-family:'JetBrains Mono';">
                  ${item.market || 'STOCK'}
              </div>
          </div>
      `;
  });
  ```
- **Vulnerabilities**:
  - `item.ticker`, `item.name_kr`, `item.name_en`, and `item.market` are interpolated directly into HTML.
  - `onclick="selectSearchedStock('${item.ticker}')"` enables DOM XSS via search response poisoning.
- **Remediation**:
  - Sanitize all fields with `escapeHtml()`.
  - Add `data-ticker="${escapeHtml(item.ticker)}"`.
  - Attach a single click event listener on `searchDropdown` to delegate item clicks to `selectSearchedStock()`.

---

#### Vector 5: Macro Tailwind Sectors & Live Stream URLs
- **Location**: Lines 1565–1579.
- **Mechanism**:
  - Line 1569: `if (data.macro.url) sLink.href = data.macro.url;`
  - Line 1578: `tailwindEl.innerHTML = sectors.map(s => `[${s}]`).join(' ');`
- **Vulnerabilities**:
  - Setting `.href` to an untrusted URL string can trigger `javascript:` scheme execution.
  - `tailwindEl.innerHTML` interpolates sector strings without escaping.
- **Remediation**:
  - Sector tags: `tailwindEl.innerHTML = sectors.map(s => `[${escapeHtml(s)}]`).join(' ');` or `tailwindEl.textContent = sectors.map(s => `[${s}]`).join(' ');`.
  - URL safety check:
    ```javascript
    if (data.macro.url && /^https?:\/\//i.test(data.macro.url)) {
        sLink.href = data.macro.url;
    } else {
        sLink.href = '#';
    }
    ```

---

## 4. Inline `onclick` Handler Refactoring Plan

### Summary of Inline `onclick` Handlers:
| Code Location | Current Implementation | Proposed Event Delegation / Listener Refactor |
|---|---|---|
| `al_sangmoo_dashboard.html:1625` | `onclick="selectStock('${item.ticker}', ${item.price})"` | Add `data-ticker` & `data-price` to `.rec-item`. Parent container event delegation. |
| `al_sangmoo_dashboard.html:1717` | `onclick="sellHolding(${h.id}, ${h.current_price})"` | Add `data-holding-id` & `data-current-price` to `.btn-exit-holding`. Table body delegation. |
| `al_sangmoo_dashboard.html:1960` | `onclick="sellHolding(${h.id}, ${h.current_price})"` | Identical to above in `renderPortfolioOnly()`. |
| `al_sangmoo_dashboard.html:1732–1751` | `onclick="selectStock('${x.ticker}', ${x.price || 0})"` | Add `data-ticker` & `data-price` to `.ticker-pill`. `dailyRecHistoryBody` delegation. |
| `al_sangmoo_dashboard.html:2074` | `onclick="selectSearchedStock('${item.ticker}')"` | Add `data-ticker` to `.search-suggestion-item`. `searchDropdown` delegation. |
| `al_sangmoo_dashboard.html:383` | `<button ... onclick="triggerScanNow()">` | Replace with `document.getElementById("btnScanNow").addEventListener("click", triggerScanNow);` |
| `al_sangmoo_dashboard.html:384` | `<button ... onclick="loadDashboard()">` | Replace with `document.getElementById("btnRefresh").addEventListener("click", loadDashboard);` |
| `al_sangmoo_dashboard.html:617` | `<button ... onclick="submitQuickBuy()">` | Replace with `document.getElementById("btnQuickBuy").addEventListener("click", submitQuickBuy);` |
| `al_sangmoo_dashboard.html:625` | `<button ... onclick="resetUserPortfolio()">` | Replace with `document.getElementById("btnClearAccount").addEventListener("click", resetUserPortfolio);` |
| `al_sangmoo_dashboard.html:689–690` | `<button ... onclick="switchTimeframe('daily')">` | Bind via `addEventListener` in `initCharts()` or DOMContentLoaded. |

---

## 5. Subresource Integrity (SRI) Findings

In `al_sangmoo_dashboard.html` Line 12 (and `al_sangmoo_chart_system.html` Line 8):
```html
<script src="https://unpkg.com/lightweight-charts@4.1.1/dist/lightweight-charts.standalone.production.js"></script>
```
- **Risk**: If the `unpkg.com` CDN is compromised, arbitrary malicious scripts could be injected into the terminal.
- **Recommendation**:
  Add `crossorigin="anonymous"` and standard SHA-384 `integrity` hash, or bundle `lightweight-charts.standalone.production.js` locally in a static assets directory.

---

## 6. Implementation Blueprint: Before vs. After Code Specifications

### 6.1. Sanitization Utility (`escapeHtml`)
```javascript
// Add at top of <script> (Line ~779)
function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
```

### 6.2. Refactored `renderItemCard`
```javascript
const renderItemCard = (item, colorTheme) => {
    const originClass = (item.origin === 'VIKINGS_LIVE') ? 'origin-vikings' : 'origin-quant';
    const originText = (item.origin === 'VIKINGS_LIVE') ? '[VIKINGS LIVE]' : '[UNIVERSE SCAN]';
    const safeTicker = escapeHtml(item.ticker);
    const safeName = escapeHtml(item.name || item.ticker);
    const safePrice = Number(item.price || 0).toFixed(2);
    const isActive = (currentSelectedTicker === item.ticker) ? 'active' : '';
    const targetPrice = item.target_price ? Number(item.target_price).toFixed(2) : (Number(item.price) * 1.15).toFixed(2);
    const stopPrice = item.stop_price ? Number(item.stop_price).toFixed(2) : (Number(item.price) * 0.96).toFixed(2);
    
    // IN WALLET Badge & Highlight
    let walletBadge = '';
    let walletCardStyle = '';
    if (item.in_wallet) {
        const pnlSign = (item.holding_pnl >= 0) ? '+' : '';
        walletBadge = `<span style="font-size:9px; font-weight:800; font-family:'JetBrains Mono'; color:#38bdf8; background:rgba(56,189,248,0.15); border:1px solid #0284c7; padding:1px 5px; border-radius:3px; letter-spacing:0.5px;">[IN WALLET: ${pnlSign}${Number(item.holding_pnl).toFixed(1)}%]</span>`;
        walletCardStyle = 'border-left:3px solid #38bdf8; background:rgba(15,23,42,0.95);';
    }
    
    // STREAK Badge
    let streakBadge = '';
    if (item.streak_days && item.streak_days >= 2) {
        streakBadge = `<span style="font-size:9px; font-weight:800; font-family:'JetBrains Mono'; color:#fbbf24; background:rgba(251,191,36,0.15); border:1px solid #d97706; padding:1px 5px; border-radius:3px; letter-spacing:0.5px;">[${escapeHtml(item.streak_days)}D STREAK]</span>`;
    }

    // Sector Badge
    const sectorTag = item.sector ? `<span style="font-size:9px; font-weight:700; font-family:'JetBrains Mono'; color:#94a3b8; background:#1e293b; border:1px solid #334155; padding:1px 4px; border-radius:2px;">[${escapeHtml(item.sector)}]</span>` : '';

    // OBV Status Badge
    let obvBadge = '';
    if (item.obv_status === 'STEALTH_ACCUM') {
        obvBadge = `<span style="color:#34d399; font-weight:700;">OBV: STEALTH ACCUM</span>`;
    } else if (item.obv_status === 'BULL_FLOW') {
        obvBadge = `<span style="color:#38bdf8; font-weight:700;">OBV: INFLOW</span>`;
    } else {
        obvBadge = `<span style="color:#64748b;">OBV: NEUTRAL</span>`;
    }

    const flowRatioText = item.flow_ratio ? ` | FLOW: <strong style="color:${item.flow_ratio >= 1.4 ? '#34d399' : '#94a3b8'};">${Number(item.flow_ratio).toFixed(1)}x</strong>` : '';

    return `
        <div class="rec-item ${isActive}" data-ticker="${safeTicker}" data-price="${safePrice}" style="${walletCardStyle}">
            <div style="flex:1; pointer-events:none;">
                <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:2px;">
                    <div style="display:flex; align-items:center; gap:5px; flex-wrap:wrap;">
                        <span class="rec-item-title" style="color:${colorTheme};">${safeTicker}</span>
                        <span class="rec-item-sub" style="font-size:11px; font-weight:600;">${safeName}</span>
                        ${sectorTag}
                        <span class="origin-badge ${originClass}" style="font-size:9px; padding:1px 5px;">${originText}</span>
                        ${streakBadge}
                        ${walletBadge}
                    </div>
                    <div class="rec-item-price" style="color:${colorTheme};">$${safePrice}</div>
                </div>
                <div class="rec-item-sub" style="font-size:10px; display:flex; justify-content:space-between; align-items:center; margin-top:2px; font-family:'JetBrains Mono';">
                    <span>SCORE: <strong style="color:#f8fafc;">${escapeHtml(item.score)}pt</strong> | 26D: <strong style="color:#38bdf8;">${item.kijun_gap >= 0 ? '+' : ''}${Number(item.kijun_gap).toFixed(1)}%</strong> | VOL: <strong>${escapeHtml(item.vol_ratio)}%</strong></span>
                    <span>${obvBadge}${flowRatioText}</span>
                </div>
                <div style="display:flex; justify-content:space-between; align-items:center; font-size:10px; font-family:'JetBrains Mono'; margin-top:4px; padding-top:4px; border-top:1px dashed #1e293b;">
                    <span style="color:#94a3b8;">TP(+15%): <strong style="color:#34d399;">$${targetPrice}</strong></span>
                    <span style="color:#94a3b8;">SL(-4%): <strong style="color:#f87171;">$${stopPrice}</strong></span>
                </div>
            </div>
        </div>
    `;
};
```

### 6.3. Refactored Portfolio Table Rendering (`renderPortfolioOnly` & `renderDashboardData`)
```javascript
function renderPortfolioRows(holdings) {
    if (!holdings || holdings.length === 0) {
        return `<tr><td colspan="6" style="text-align:center; color:var(--text-secondary); padding:20px;">No open positions in active portfolio. Select a ticker above to enter a position.</td></tr>`;
    }
    return holdings.map(h => {
        const pnlColor = h.pnl_pct >= 0 ? '#10b981' : '#ef4444';
        const safeTicker = escapeHtml(h.ticker);
        const safeAdvice = escapeHtml(h.exit_advice || 'Holding');
        const safeId = Number(h.id);
        const safeCurrentPrice = Number(h.current_price || 0);

        return `
            <tr>
                <td><strong style="font-family:'JetBrains Mono'; color:#f8fafc;">${safeTicker}</strong></td>
                <td style="font-family:'JetBrains Mono';">$${Number(h.buy_price).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})}</td>
                <td style="font-family:'JetBrains Mono'; font-weight:600; color:#38bdf8;">${Number(h.quantity).toLocaleString('en-US', {maximumFractionDigits:4})}</td>
                <td style="font-family:'JetBrains Mono';">$${safeCurrentPrice.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})}</td>
                <td style="color:${pnlColor}; font-weight:700; font-family:'JetBrains Mono';">${h.pnl_pct >= 0 ? '+' : ''}${Number(h.pnl_pct).toFixed(2)}%</td>
                <td>
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <span style="font-size:11px; color:#9ca3af;">${safeAdvice}</span>
                        <button class="btn-exit-holding" data-holding-id="${safeId}" data-current-price="${safeCurrentPrice}" style="background:#dc2626; color:#fff; border:none; padding:2px 8px; border-radius:2px; font-size:10px; cursor:pointer;">Exit</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}
```

### 6.4. Unified Event Delegation Initializer
```javascript
function initFrontendEventDelegation() {
    // 1. Matrix Card Click Delegation
    const handleMatrixCardClick = (e) => {
        const card = e.target.closest(".rec-item");
        if (card) {
            const ticker = card.getAttribute("data-ticker");
            const price = parseFloat(card.getAttribute("data-price"));
            if (ticker) selectStock(ticker, isNaN(price) ? null : price);
        }
    };
    ["strat1ExclusiveCards", "dualConsensusCards", "strat2ExclusiveCards"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener("click", handleMatrixCardClick);
    });

    // 2. Portfolio Table Exit Button Delegation
    const pBody = document.getElementById("portfolioTableBody");
    if (pBody) {
        pBody.addEventListener("click", (e) => {
            const btn = e.target.closest(".btn-exit-holding");
            if (btn) {
                const id = parseInt(btn.getAttribute("data-holding-id"), 10);
                const price = parseFloat(btn.getAttribute("data-current-price"));
                if (!isNaN(id) && !isNaN(price)) {
                    sellHolding(id, price);
                }
            }
        });
    }

    // 3. Daily History Recommendation Pill Click Delegation
    const dailyRecBody = document.getElementById("dailyRecHistoryBody");
    if (dailyRecBody) {
        dailyRecBody.addEventListener("click", (e) => {
            const pill = e.target.closest(".ticker-pill");
            if (pill) {
                const ticker = pill.getAttribute("data-ticker");
                const price = parseFloat(pill.getAttribute("data-price"));
                if (ticker) selectStock(ticker, isNaN(price) ? null : price);
            }
        });
    }

    // 4. Search Dropdown Suggestion Click Delegation
    const searchDropdown = document.getElementById("searchDropdown");
    if (searchDropdown) {
        searchDropdown.addEventListener("click", (e) => {
            const item = e.target.closest(".search-suggestion-item");
            if (item) {
                const ticker = item.getAttribute("data-ticker");
                if (ticker) selectSearchedStock(ticker);
            }
        });
    }
}
```

---

## 7. Mirror Synchronization Strategy

To guarantee that all dashboard mirror files stay 100% in sync:
1. Update `al_sangmoo_dashboard.html` as the canonical source.
2. Copy the sanitized content to:
   - `html_dashboards/01_R상무_통합_퀀트_대시보드.html`
   - `html_dashboards/01_알상무_통합_퀀트_대시보드.html`
   - `HTML_대시보드_모음/01_R상무_통합_퀀트_대시보드.html`
3. Verify SHA-256 integrity across all 4 files using a verification script.

---

## 8. Verification & Acceptance Criteria for Frontend Hardening

| Acceptance Criterion | Verification Method | Expected Result |
|---|---|---|
| **XSS Payload Neutralization in Portfolio Table** | Inject `{"reason": "<script>alert(1)</script>"}` via `POST /api/portfolio/sell/{id}` and render dashboard | Characters rendered as `&lt;script&gt;alert(1)&lt;/script&gt;`; 0 script execution. |
| **XSS Payload Neutralization in Search Dropdown** | Query `/api/search` with simulated mock containing `<img src=x onerror=alert(1)>` | Suggestion HTML entity encoded; no script trigger. |
| **XSS Payload Neutralization in Matrix Cards** | Send WebSocket feed with ticker `TEST<b onmouseover=alert(1)>` | Ticker rendered safely in DOM with escaped markup. |
| **No Inline Onclick Code Execution** | Audit DOM tree for `onclick="selectStock..."` or `onclick="sellHolding..."` | All replaced with `data-*` attributes and event delegation. |
| **Safe Live Stream Links** | Pass `{"url": "javascript:alert(1)"}` in macro payload | Anchor link href sets to `#` or is discarded. |
