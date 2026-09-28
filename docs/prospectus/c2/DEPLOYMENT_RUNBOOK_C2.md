# AL-SANGMOO QUANT TERMINAL: C-2 PRODUCTION DEPLOYMENT & OPERATIONAL RUNBOOK

**Target Engine:** C-2 Bundle Architecture (Equal Sizing 34/33/33, Dual-Clock Stops -7% EOD / -10% Emergency, +18% Uncapped Trailing ATR 3.0, QQQ Cash-Proxy Sleeve)  
**Classification:** Institutional Operations Manual / Production Runbook  
**Audience:** Quantitative Traders, DevOps Engineers, System Administrators  
**Last Updated:** 2026-09-14  

---

## 1. Executive Summary & Architectural Overview

The **C-2 Bundle Engine** is the frozen quantitative champion designed to deliver compounding alpha while systematically defending against the whipsaws that degraded the legacy C1-M2 system.

### Key Strategy Specifications (Single Source of Truth)

| Component | C-2 Production Specification | Implementation File |
| :--- | :--- | :--- |
| **Bull Slots** | **3 Slots (34% / 33% / 33%)** equal distribution | [`al_sangmoo/core/constants.py`](file:///d:/코딩/R/al_sangmoo/core/constants.py) |
| **Bear Slots** | **2 Slots (25% / 25%)** defensive distribution | [`al_sangmoo/core/constants.py`](file:///d:/코딩/R/al_sangmoo/core/constants.py) |
| **Intraday Stop** | **Emergency Stop ONLY: -10.0%** (`last <= entry * 0.90`) | [`al_sangmoo/domain/risk/portfolio_guardian.py`](file:///d:/코딩/R/al_sangmoo/domain/risk/portfolio_guardian.py) |
| **EOD Stop** | **Closing Window Stop: -7.0%** (`close/last <= entry * 0.93` during 15:50-16:00 ET) | [`al_sangmoo/domain/risk/portfolio_guardian.py`](file:///d:/코딩/R/al_sangmoo/domain/risk/portfolio_guardian.py) |
| **Trailing Stop** | **Armed at +18.0% peak gain**, floor: `max(Kijun-26, peak - 3.0 * ATR14)` | [`al_sangmoo/domain/risk/trailing_stop.py`](file:///d:/코딩/R/al_sangmoo/domain/risk/trailing_stop.py) |
| **Kijun Exit** | **Standalone Kijun exit permanently DISABLED** (used only as trailing floor) | [`al_sangmoo/domain/risk/trailing_stop.py`](file:///d:/코딩/R/al_sangmoo/domain/risk/trailing_stop.py) |
| **Trend Gate** | **QQQ Close >= 20-day SMA** (prior day EOD confirmed bar) | [`al_sangmoo/domain/risk/autopilot_trader.py`](file:///d:/코딩/R/al_sangmoo/domain/risk/autopilot_trader.py) |
| **Alpha Gate** | **Stock Composite RS >= QQQ Composite RS** | [`al_sangmoo/domain/risk/autopilot_trader.py`](file:///d:/코딩/R/al_sangmoo/domain/risk/autopilot_trader.py) |
| **Idle Cash Proxy**| **1.0x Core QQQ Overlay** (strict dominance over QLD; optional 1.5x QLD overlay) | [`al_sangmoo/domain/risk/cash_proxy.py`](file:///d:/코딩/R/al_sangmoo/domain/risk/cash_proxy.py) |
| **Guardrail Caps** | Slot 1: **39%**, Slot 2: **38%**, Slot 3: **38%** (target + 5%p execution buffer) | [`al_sangmoo/domain/risk/order_guardrail.py`](file:///d:/코딩/R/al_sangmoo/domain/risk/order_guardrail.py) |

> **CRITICAL OPERATIONAL PRINCIPLE**:  
> C-2 is an inseparable **bundle**. Partial deployment (e.g. changing slot weights without dual-clock stops, or retaining the legacy -5% intraday tick stop) invalidates backtest performance and re-introduces severe friction whipsaws.

---

## 2. Staging & Production Cutover Checklist

Before switching `KIS_MODE` from `vps` to `prod`, the operator MUST complete each of the following 5 verification stages:

```
[ ] Stage 1: Paper Trading / VPS Staging (Minimum 30 calendar days or 1 earnings season)
    [ ] Active KIS Virtual account configured (CANO 8 digits + 01).
    [ ] Verified automated login and OAuth token generation in data/.kis_token_vps.json.
    [ ] Verified at least 10 dry-run orders executed without unhandled exceptions.

[ ] Stage 2: Concurrency & Rate Limit Hardening
    [ ] TokenBucketLimiter confirmed at 3.5 TPS.
    [ ] ORDER_MUTEX properly synchronizes guardian polling and sleeve rebalancing.
    [ ] Automated recovery verified on transient EGW00201 rate limit spikes.
    [ ] Token stampede protection verified on EGW00133 (reuse of valid cached token).

[ ] Stage 3: Dual-Clock Stop Verification
    [ ] Confirmed intraday -7% dip does NOT exit position during regular hours (10:00-15:45 ET).
    [ ] Confirmed intraday -10% drop immediately fires AUTO_EMERGENCY_STOP.
    [ ] Confirmed closing window (15:50-16:00 ET) evaluates positions and exits names <= -7.0%.
    [ ] Confirmed Guardian skips stop loss force-exits on QQQ / QLD cash proxy holdings.

[ ] Stage 4: Backward-Compatibility State Migration
    [ ] Existing legacy C1-M2 positions in quant_trades.db retain original ticket stops (95.0).
    [ ] New positions created with C-2 tickets (93.0 EOD / 90.0 emergency).
    [ ] _persist_mark marks-to-market without overwriting legacy stop_loss_price values.

[ ] Stage 5: Full Regression Test Suite Clean Pass
    [ ] python -m pytest tools_and_tests/test_track3_staging_readiness.py -v (29/29 PASSED)
    [ ] python -m pytest tools_and_tests/test_c2_bundle_architecture.py -v (24/24 PASSED)
    [ ] python -m pytest tools_and_tests/test_phase5_4_kis_modular.py -v (11/11 PASSED)
    [ ] Frontend modules verified < 600 lines.
```

---

## 3. Environment Setup & API Credential Checklist

### 3.1 Host System Prerequisites
* **Operating System**: Ubuntu 22.04 LTS (recommended for VPS) or Windows 10/11 / Windows Server 2022.
* **Python Runtime**: Python 3.11, 3.12, or 3.13.
* **System Packages**: `sqlite3`, `curl`, `git`, `cron` or `systemd`.
* **Network**: Outbound HTTPS (port 443, 9443, 29443) access to:
  * `openapi.koreainvestment.com:9443` (KIS Real Production)
  * `openapivts.koreainvestment.com:29443` (KIS Virtual Sandbox)
  * `query1.finance.yahoo.com` (Fallback price & benchmark feed)

### 3.2 Configuration Checklist (`.env`)
Copy `.env.example` to `.env` in the repository root:
```bash
cp .env.example .env
chmod 600 .env  # Restrict credential file read permissions
```

Verify the following variables in `.env`:
```ini
# --- KIS OpenAPI Credentials ---
KIS_APP_KEY="YOUR_ACTUAL_APP_KEY"
KIS_APP_SECRET="YOUR_ACTUAL_APP_SECRET"
KIS_CANO="12345678"          # 8-digit CANO
KIS_ACNT_PRDT_CD="01"        # 2-digit Product Code

# --- Environment Mode ---
# For Staging: 'vps' (Virtual Sandbox)
# For Production: 'prod' (Real Live Trading)
KIS_MODE="vps"
KIS_PAPER_TRADING="true"

# --- Database & Persistence ---
AL_SANGMOO_DB_PATH="quant_trades.db"

# --- Strategy & Engine ---
AL_SANGMOO_ENGINE="C-2"
AL_SANGMOO_CASH_PROXY="QQQ"
AL_SANGMOO_LEVERAGE_MODE="false"   # 1.0x QQQ overlay (recommended based on Track 1 ablation)

# --- Timing & Scheduling ---
PORTFOLIO_GUARDIAN_INTERVAL="10"
MARKET_TIMEZONE="America/New_York"
EOD_WINDOW_MINUTES="10"
```

---

## 4. Timezone & Daily Execution Schedule (DST-Aware)

> **CRITICAL RULE**:  
> **NEVER hardcode KST cron times for US market events.** The US observes Daylight Saving Time (DST); South Korea does NOT. All automated job dispatching must evaluate against the `America/New_York` clock.

### 4.1 Master Execution Schedule

| US Eastern Time (ET) | Summer Time (EDT) in KST | Winter Time (EST) in KST | System Action / Pipeline Phase |
| :--- | :--- | :--- | :--- |
| **08:30 ET** | 21:30 KST | 22:30 KST | Pre-market universe ingestion & SEC N-PORT candidate ranking (`al_sangmoo_daily_bot.py`) |
| **09:30 ET** | 22:30 KST | 23:30 KST | **US Market Open.** Regular Trading Hours (RTH) start. |
| **09:30 - 10:00 ET** | 22:30 - 23:00 KST | 23:30 - 00:00 KST | **Autopilot Entry Window**: Evaluates QQQ 20MA trend gate and RS alpha gate; places next-open buys. |
| **09:30 - 15:50 ET** | 22:30 - 04:50 KST | 23:30 - 05:50 KST | **Intraday Guardian Loop**: 10s poll checking **-10.0% Emergency Stop** and +18% trailing profit stops. |
| **15:50 - 16:00 ET** | **04:50 - 05:00 KST** | **05:50 - 06:00 KST** | **EOD Closing Window**: Guardian checks **-7.0% EOD Hard Stop**; exits non-viable leadership lots. |
| **16:00:00 ET** | 05:00:00 KST | 06:00:00 KST | **US Market Close.** Order placement blocked (`is_us_regular_hours` returns False). |
| **16:05 ET** | 05:05 KST | 06:05 KST | Post-market reconciliation (`reconciliation.py`), mark-to-market DB mark, dashboard JSON feed generation. |

### 4.2 Special Session: US Half-Days
On designated US early-close sessions (13:00 ET close):
* Christmas Eve (December 24)
* Day after Thanksgiving (Black Friday)
* Day before Independence Day (July 3)

**Behavior:**
* Closing window activates dynamically at **12:50 - 13:00 ET** (01:50-02:00 or 02:50-03:00 KST).
* The regular 15:50 ET closing job is automatically suppressed because the session closed at 13:00 ET.

---

## 5. Service Orchestration & Process Architecture

### 5.1 Process Architecture Diagram
```
                     +---------------------------------------+
                     |         Web Terminal & UI             |
                     |  (HTML5 / ES6 Modules / WebSocket)    |
                     +-------------------+-------------------+
                                         |
                                  HTTP / WebSocket
                                         v
+--------------------+       +-------------------------------+       +---------------------+
| Scheduled Bot      |       |     FastAPI Server Engine     |       | Portfolio Guardian  |
| al_sangmoo_daily_  |       |        (server.py)            |       | Background Daemon   |
| bot.py             |       +---------------+---------------+       | (portfolio_guardian)|
+---------+----------+                       |                       +----------+----------+
          |                                  |                                  |
          +------------------+---------------+----------------------------------+
                             |
                   SQLite WAL Database (`quant_trades.db`)
                             |
                             v
           +----------------------------------+
           |       KIS Broker Adapter         |
           | - 3.5 TPS TokenBucketLimiter     |
           | - ORDER_MUTEX Serialization      |
           | - OAuth Token Persistent Cache   |
           +-----------------+----------------+
                             |
             OpenAPI REST Gateway (HTTP 443/9443)
                             v
           +----------------------------------+
           | Korea Investment & Securities    |
           | (한국투자증권 OpenAPI Endpoint) |
           +----------------------------------+
```

### 5.2 Systemd Service Deployment (Linux VPS)

#### Service 1: Web Terminal & REST API Server (`/etc/systemd/system/al-sangmoo-api.service`)
```ini
[Unit]
Description=Al-Sangmoo Quant Terminal API Server
After=network.target

[Service]
Type=simple
User=quant
WorkingDirectory=/opt/al-sangmoo
EnvironmentFile=/opt/al-sangmoo/.env
ExecStart=/opt/al-sangmoo/venv/bin/uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

#### Service 2: Portfolio Guardian Daemon (`/etc/systemd/system/al-sangmoo-guardian.service`)
```ini
[Unit]
Description=Al-Sangmoo C-2 Portfolio Guardian Background Daemon
After=network.target al-sangmoo-api.service

[Service]
Type=simple
User=quant
WorkingDirectory=/opt/al-sangmoo
EnvironmentFile=/opt/al-sangmoo/.env
ExecStart=/opt/al-sangmoo/venv/bin/python -m al_sangmoo.domain.risk.portfolio_guardian
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### Service Commands:
```bash
sudo systemctl daemon-reload
sudo systemctl enable al-sangmoo-api al-sangmoo-guardian
sudo systemctl start al-sangmoo-api al-sangmoo-guardian
sudo systemctl status al-sangmoo-api al-sangmoo-guardian
```

---

## 6. Backward-Compatibility State Migration Protocol

When cutting over from legacy C1-M2 to C-2, existing portfolio lots must be handled with surgical precision:

1. **Existing Open Lots (C1-M2)**:
   - Preserved intact in `my_portfolio` SQLite table.
   - Identified by `stop_loss_price / buy_price >= 0.945` (e.g. 95.0 on a 100.0 purchase).
   - Exit rule: **Retains legacy -5.0% ticket stop and Kijun exit**. Guardian will NOT migrate legacy ticket stops to -7%.
2. **New Entries (C-2 Engine)**:
   - Generated via `add_portfolio_buy` or Autopilot.
   - Automatically derive `stop_loss_price = round(buy_price * 0.93, 2)` (-7.0% EOD hard stop) and emergency stop at -10.0%.
   - Exit rule: **Dual-Clock stopping** (holds intraday down to -10%, exits at -7% during 15:50-16:00 ET window).
3. **Database Guard**:
   - `PortfolioGuardian._persist_mark` checks `existing_stop_price` and guarantees that the database mark update never overwrites an existing lot's ticket stop.

---

## 7. Emergency Procedures & Incident Response Runbook

### 7.1 Emergency Kill Switch (Halting Auto-Execution)
If erratic market behavior, exchange outage, or anomalous order behavior occurs:

* **Method 1: Instant Web GUI / API Toggle (Zero Downtime)**
  Send an HTTP POST to disable automated order placement while keeping monitoring active:
  ```bash
  curl -X POST "http://localhost:8000/api/portfolio/guardian/toggle?enabled=false"
  ```
* **Method 2: Immediate Daemon Stop (CLI)**
  ```bash
  sudo systemctl stop al-sangmoo-guardian
  ```
* **Method 3: File-based Emergency Halt**
  Touch an emergency lock file in the root directory:
  ```bash
  touch /opt/al-sangmoo/EMERGENCY_HALT
  ```

### 7.2 Manual Emergency Full Exit (Liquidation)
If a severe black-swan event requires dumping a specific ticker or the entire portfolio:

* **Step 1: Via Web Terminal UI**
  Navigate to the Holdings table on `http://YOUR_SERVER_IP:8000` and click the red **[긴급 전량 매도]** button next to the position.
* **Step 2: Via KIS Broker Python Gateway**
  Run an emergency market order execution directly:
  ```python
  from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
  # Market sell order (ORD_DVSN="01")
  res = default_kis_broker.place_order(
      ticker="NVDA",
      side="SELL",
      qty=10,
      price=0.0,
      order_type="01",  # Market order
  )
  print(res)
  ```

### 7.3 Unconfirmed Order Timeout & Reconciliation
If an order placement HTTP request times out or returns HTTP 5xx:
* **DO NOT BLINDLY RE-SEND A BUY/SELL POST REQUEST.** A blind re-POST risks duplicate fills.
* **Automatic Protection**: `KISBrokerAdapter` records the order key in `_unconfirmed` and queries the KIS day order book (`VTTS3035R` / `TTTS3035R` and `VTTS3039R` / `TTTS3039R`).
* **Manual Reconciliation**:
  Run the reconciliation script to synchronize local SQLite holdings with actual broker positions:
  ```bash
  python -c "
  from al_sangmoo.domain.reconciliation import reconcile_portfolio_with_broker
  reconcile_portfolio_with_broker()
  "
  ```

### 7.4 KIS OpenAPI Error Code Reference

| Error Code | Broker Message | Root Cause & Automatic System Recovery |
| :--- | :--- | :--- |
| **`EGW00201`** | 초당 거래건수를 초과하였습니다 | Rate limit exceeded (> 3.5 req/sec). **Recovered automatically**: adapter backs off 0.35s and retries up to 3 times under `TokenBucketLimiter`. |
| **`EGW00133`** | 유효한 토큰이 이미 발급되었습니다 | Token requested > 1 time per minute. **Recovered automatically**: adapter falls back to the existing valid token stored in memory or disk cache. |
| **`EGW00121`** | 토큰이 만료되었습니다 | OAuth 2.0 access token expired (24h). **Recovered automatically**: adapter calls `authenticate(force_refresh=True)` and retries request. |
| **`EGW00123`** | 유효하지 않은 토큰입니다 | Access token invalid or corrupted. Token cache is purged and re-acquired atomically under `_auth_lock`. |
| **`APBK0917`** | 매수가능금액 부족 | Insufficient purchasing power. Autopilot logs warning and adjusts order sizing. |

---

## 8. Whole-Share Rounding & Residual Cash Guidance

* **Whole-Share Rounding**: KIS overseas equities only support integer whole shares (`qty: int`).
* **Sizing Calculation**: Both the satellite sizer (`calculate_target_shares`) and the proxy sleeve (`build_cash_proxy_plan`) use floor division (`int(allocation_usd // price)`).
* **Residual Cash Allocation**:
  * Because whole-share rounding leaves unallocated cash (e.g. $120 unspent on a $4,000 sleeve buying $485 QQQ), residual cash stays in the account USD cash balance.
  * In small accounts ($5,000 - $15,000 NAV), a 34% slot may not perfectly divide into high-priced stocks ($500+). Any residual cash naturally rolls into the next day's idle cash pool.

---

## 9. Verification Sign-Off Protocol

Before marking Track 3 complete:
1. Ensure all 29 tests in `tools_and_tests/test_track3_staging_readiness.py` pass.
2. Ensure all 24 tests in `tools_and_tests/test_c2_bundle_architecture.py` pass.
3. Ensure all 11 tests in `tools_and_tests/test_phase5_4_kis_modular.py` pass.
4. Ensure frontend modular files (`ui.js`, `websocket.js`, `chart.js`) remain strictly `< 600 lines`.
5. Store `.env.example` in repo root and verify `.gitignore` contains `.env` and `.kis_token_*.json`.
