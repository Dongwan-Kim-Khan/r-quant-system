# Handoff Report: Domain 1 Web & API Security Vulnerability Audit

- **Agent**: Explorer 1 (Security Vulnerability Auditor)
- **Target Folder**: `d:\코딩\R\.agents\explorer_security`
- **Handoff Type**: Hard Handoff (Investigation & Static Code Audit Complete)
- **Date**: 2026-08-25T17:59:00+09:00

---

## 1. Observation

Direct static code observations with exact line numbers and quotations:

1. **CSWSH / WebSocket Security (`server.py:218-221`, `al_sangmoo/api/hub.py:17-35`)**:
   - `server.py:218-221`:
     ```python
     origin = websocket.headers.get("origin")
     if origin and origin not in ALLOWED_ORIGINS and not origin.startswith("http://localhost:") and not origin.startswith("http://127.0.0.1:"):
         await websocket.close(code=1008, reason="Forbidden Origin")
         return
     ```
   - Omission of `Origin` header skips validation (`if origin and ...`).
   - `startswith("http://localhost:")` allows any local port (e.g. 8888, 9999) and lacks WSS support.
   - Handshake has no authentication/session token, immediately sending full portfolio holdings to connected clients.
   - `hub.connect()` limits connections to `MAX_CONNECTIONS = 50`.

2. **CORS Configuration (`server.py:115-128`)**:
   - `allow_origins = ["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:3000", "http://127.0.0.1:3000"]`
   - Wildcard `"*"` is NOT used in origins; `allow_credentials=True` is properly restricted to explicit origin list.

3. **Credential & Secret Protection (`kis_broker.py:66, 127-136, 345`, `.gitignore:1-25`)**:
   - `kis_broker.py:66` saves token to `data/.kis_token_prod.json` and `data/.kis_token_vps.json`.
   - `.gitignore` does not ignore `data/` or `.kis_token_*.json`, creating risk of committing live 24h OAuth tokens.
   - `kis_broker.py:345`: `get_overseas_balance()` returns unmasked `f"{self.account_no}-{self.account_code}"` over `GET /api/broker/balance`.
   - `al_sangmoo_daily_bot.py:42` contained hardcoded email `developer@example.com`.

4. **SQL Injection Vectors (`persistence.py`, `reconciliation.py`, `portfolio_guardian.py`)**:
   - All dynamic SQL statements across `persistence.py`, `reconciliation.py`, and `portfolio_guardian.py` use parameterized `?` bindings. Zero string formatting/f-string injections detected.
   - Schema bug: `portfolio_guardian.py:241` inserts into `trade_history` table which does not exist in `persistence.py:init_database()`.
   - API bug: `portfolio.py:284` calls `db_manager.get_trade_history_records()` which is undefined.

5. **API Input Validation & Pydantic Constraints (`broker.py:11-20`, `portfolio.py:24-73`)**:
   - `portfolio.py` enforces regex validators on `BuyOrder` and `SellOrder`.
   - `broker.py:11-20` (`BrokerOrderRequest`) lacks `TICKER_REGEX`, `buy_date` regex, and `side` validation, allowing guardrail bypass on non-`BUY` string values.

6. **Path Traversal & Subprocess Command Injection (`charts.py:24-29`, `youtube_stream_scanner.py:70-85, 375-382`)**:
   - `charts.py:28` uses `not chart_file.startswith(charts_dir_abs)` without trailing separator, allowing sibling directory prefix matching.
   - `youtube_stream_scanner.py` strictly validates `v_id` via `^[a-zA-Z0-9_-]{11}$` and executes `yt-dlp` via argument lists without shell invocation.

7. **OWASP Response Headers (`server.py:149-160`)**:
   - Sets `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `X-XSS-Protection`.
   - Missing `Content-Security-Policy` (CSP), `Strict-Transport-Security` (HSTS), and `Permissions-Policy`.
   - HTTP middleware does not cover WebSocket handshake responses.

8. **DOM / Reflected XSS (`frontend/js/websocket.js:420-428`, `frontend/js/ui.js:153, 216, 236, 294`)**:
   - `websocket.js:420-428` renders search autocomplete into `dropdown.innerHTML` with unescaped `${r.ticker}` and `${r.name}`.
   - `ui.js` uses string interpolation inside inline `onclick="..."` attributes.

---

## 2. Logic Chain

1. **CSWSH Vulnerability (SEC-01)**:
   - Observation: `server.py:219` allows connections if `origin` is omitted or starts with `http://localhost:`.
   - Logic: Any malicious script on any local port (e.g. 8888) can establish a WebSocket connection and receive the initial broadcast containing full portfolio state.
   - Conclusion: High severity CSWSH / unauthorized feed exposure.

2. **Plaintext Token Git Leak Risk (SEC-02)**:
   - Observation: `kis_broker.py` writes tokens to `data/.kis_token_*.json`, and `.gitignore` lacks entries for `data/` or `.kis_token_*.json`.
   - Logic: Staging all files (`git add .`) stages active 24-hour OAuth trading tokens.
   - Conclusion: High severity credential leak vector.

3. **DOM XSS via Autocomplete (SEC-04)**:
   - Observation: `websocket.js:420` uses template literals directly inside `dropdown.innerHTML` without `UI.escapeHtml()`.
   - Logic: When `ApiClient.searchStocks` returns matching stocks or custom queries, unsanitized strings execute in the browser DOM.
   - Conclusion: High severity DOM XSS vulnerability.

4. **Broker Request Input Validation Flaw (SEC-05)**:
   - Observation: `BrokerOrderRequest` does not enforce `TICKER_REGEX`, date regex, or `side` validation, while `broker.py:66` checks `if not eval_res["allowed"] and final_side == "BUY"`.
   - Logic: Manipulated payloads bypass guardrails or send malformed data to KIS OpenAPI.
   - Conclusion: High severity input validation defect.

---

## 3. Caveats

1. **Read-Only Scope**: This audit was strictly observational and static. No live exploit payloads were transmitted to active external broker production servers.
2. **Local Environment Context**: Antigravity / Windows local single-user deployment is the primary current runtime. However, if deployed on a shared network or exposed via reverse proxy, vulnerabilities like SEC-01 and SEC-03 become immediately exploitable across the LAN/WAN.
3. **No Code Changes Made**: In accordance with the Explorer protocol, zero workspace code files were modified.

---

## 4. Conclusion

The Al-Sangmoo Quant Terminal possesses solid foundations in SQL injection defense (100% parameterized queries) and basic HTTP header encapsulation. However, **10 security vulnerabilities** require remediation:
- **3 High-Severity Vulnerabilities**: CSWSH weak origin validation (SEC-01), OAuth token exposure risk in gitignore (SEC-02), DOM XSS in search autocomplete (SEC-04), and Broker input validation/guardrail bypass (SEC-05).
- **4 Medium-Severity Vulnerabilities**: Account number disclosure in balance API (SEC-03), chart path traversal prefix flaw (SEC-06), missing CSP/HSTS headers (SEC-07), and database schema table/method disconnects (SEC-08).
- **2 Low-Severity Vulnerabilities**: Hardcoded email PII (SEC-09) and inline onclick string interpolation (SEC-10).

Full details, exploit scenarios, and line-by-line remediation code are available in `d:\코딩\R\.agents\explorer_security\analysis.md`.

---

## 5. Verification Method

To independently verify these findings:
1. **SEC-01 (CSWSH)**: Inspect `server.py:218-221` and simulate WebSocket handshake with omitted `Origin` or `Origin: http://localhost:9999`.
2. **SEC-02 (Token in Git)**: Run `git status --ignored` or check `git check-ignore data/.kis_token_prod.json` -> returns not ignored.
3. **SEC-03 (Account Number)**: View `kis_broker.py:345` and send `GET /api/broker/balance` -> returns unmasked `account_no`.
4. **SEC-04 (DOM XSS)**: Inspect `frontend/js/websocket.js:420-428` for unescaped `dropdown.innerHTML` interpolation.
5. **SEC-05 (Broker Input)**: View `al_sangmoo/interfaces/api/routers/broker.py:11-20, 66`.
6. **SEC-06 (Path Traversal)**: Inspect `al_sangmoo/interfaces/api/routers/charts.py:24-29`.
7. **SEC-07 (Security Headers)**: Inspect `server.py:149-160` and verify missing CSP header on HTTP responses.
8. **SEC-08 (Database Schema)**: Inspect `portfolio_guardian.py:241` vs `persistence.py:48-131`, and call `GET /api/portfolio/history`.
