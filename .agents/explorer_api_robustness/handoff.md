# Handoff Report: Domain 5 API Robustness & Error Handling Audit

**Auditor**: Explorer 5 (API Robustness & Resilience Auditor)  
**Date**: 2026-08-25  
**Working Directory**: `d:\코딩\R\.agents\explorer_api_robustness`  
**Handoff Type**: Hard (Task Complete)

---

## 1. Observation

Direct code observations from read-only audit across `d:\코딩\R`:

1. **KIS TPS Rate Limiting & TR Bursts** (`al_sangmoo/infrastructure/brokers/kis_broker.py`):
   - Lines 272–280: `get_overseas_balance` iterates sequentially over `["NASD", "NYSE", "AMEX"]` with a static `time.sleep(0.35)` retry loop only on `msg_cd == "EGW00201"`. Max attempts is fixed at 2 without exponential backoff or jitter.
   - Lines 367–376: `get_live_price` iterates over `["NYS", "NAS", "AMS"]` with no rate limiter or inter-exchange delay.
   - Lines 515–539: `_place_overseas_order` makes a single `requests.post` call with zero retries on transient network jitter or `EGW00201` rate limiting.
   - Entire file: Zero client-side rate limiting (no Token Bucket / Leaky Bucket limiter).

2. **Broker-Database Dual Write Hazard** (`al_sangmoo/interfaces/api/routers/portfolio.py`):
   - Lines 134–156 (`buy_stock`): External KIS broker order execution (`default_kis_broker.place_order`) occurs *before* database record insertion (`db_manager.add_portfolio_buy`). If SQLite write fails, the broker order remains filled on the live exchange while the local database throws an unhandled 500 error.
   - Lines 199–224 (`sell_stock`): Sell order is submitted to broker *before* updating SQLite holding status.

3. **OAuth Token Renewal Concurrency Race** (`al_sangmoo/infrastructure/brokers/kis_broker.py`):
   - Lines 140–177: `authenticate()` lacks mutex/thread-locking. Simultaneous calls from multiple coroutines (`PortfolioGuardian`, `AutoPilotTrader`, API requests) issue concurrent POST requests to `/oauth2/tokenP`, triggering KIS error `EGW00133` (duplicate token issuance within 1 minute).
   - Lines 272–585: TR handlers do not intercept HTTP 401 or auth error codes to auto-refresh tokens.

4. **Yahoo Finance Concurrency Burst** (`generate_dashboard_feed.py` & `al_sangmoo_daily_bot.py`):
   - Lines 272–274 (`generate_dashboard_feed.py`): `ThreadPoolExecutor(max_workers=12)` fires 12 parallel `yf.download` threads across 60 symbols without inter-request delays, risking HTTP 429 rate limiting.
   - Lines 141–143 (`al_sangmoo_daily_bot.py`): `df.columns = df.columns.get_level_values(0)` blindly extracts level 0 of MultiIndex, breaking when yfinance returns ticker in level 0 and metric in level 1.

5. **24/7 Guardian Polling Without Market Calendar** (`al_sangmoo/domain/risk/portfolio_guardian.py` & `al_sangmoo/domain/risk/autopilot_trader.py`):
   - Lines 74–85 (`portfolio_guardian.py`): Polling loop executes every 10 seconds 24/7/365 without checking if US markets are open, resulting in continuous failed TR calls and spurious order attempts during weekends and holidays.
   - Lines 82–88 (`autopilot_trader.py`): Checks only `hour >= 22 or hour < 6`, missing weekday filtering (`weekday < 5`), DST (EDT 21:30 KST vs EST 22:30 KST), and US market holidays.

6. **Missing Timeouts** (`al_sangmoo_distill/batch_download_50_lives.py:84`, `tools_and_tests/*.py`):
   - `subprocess.run()` in batch scripts and `urllib.request.urlopen()` in helper utilities omit timeout parameters.

7. **Error Schema Inconsistency** (`server.py:130-147`):
   - `HTTPException` returns `{"detail": ...}`, validation returns `{"detail": [...]}`, 500 handler returns `{"status": "error", "message": ...}`, and routers return fragmented schemas (`{"status": "rejected", "reason": ...}`).

---

## 2. Logic Chain

1. **Rate Limiting (Obs 1) $\rightarrow$ API Dropped Orders**: KIS OpenAPI enforces 2–5 TPS for overseas trading. Firing multi-exchange balance queries and multi-asset price checks in rapid loops exceeds this quota, returning `EGW00201`. Because `place_order` does not retry, stop-loss and entry orders fail during heavy market activity.
2. **Dual-Write Ordering (Obs 2) $\rightarrow$ Financial Desynchronization**: Performing external broker network I/O before acquiring database persistence guarantees that any subsequent database locking error produces an untracked real-money position, encouraging duplicate user buy clicks.
3. **Token Lock Absence (Obs 3) $\rightarrow$ System Authentication Failure**: Simultaneous token expiration across daemons causes simultaneous token requests, tripping KIS 1-minute duplicate issue limits (`EGW00133`) and incapacitating the broker gateway.
4. **Unthrottled ThreadPool (Obs 4) $\rightarrow$ Scanner Incompleteness**: High concurrent scraping of Yahoo Finance leads to HTTP 429 throttling and missing rows in quant dashboard feeds.
5. **Missing Calendar Checks (Obs 5) $\rightarrow$ Off-Hours Noise & Ineffective Execution**: Unaware daemons attempting orders during weekend closures accumulate error logs and fail to execute on daylight saving schedule shifts.

---

## 3. Caveats

- **No Live Trading Execution Under Stress**: This was a static code audit. TPS limit behaviors were evaluated against official Korea Investment & Securities OpenAPI specifications and error codes (`EGW00201`, `EGW00133`, `EGW00205`).
- **Paper Broker vs Real KIS Broker**: `PaperTradingBroker` uses in-memory/SQLite persistence and does not exhibit external rate-limiting flaws, but `KISBrokerAdapter` (used in production and VPS modes) is vulnerable to all documented rate-limiting and token lifecycle risks.
- **External Dependencies**: `yfinance` internal rate-limiting depends on Yahoo Finance server-side heuristics.

---

## 4. Conclusion

Domain 5 (API Calling Robustness & Error Handling) receives a health score of **68 / 100**. While fundamental REST connections, basic HTTP timeouts, and OWASP security headers are present, the external integration layer lacks production-grade distributed resilience mechanisms.

The system requires:
1. **Critical**: Client-side Token Bucket Rate Limiter (3.5 TPS cap) and Exponential Backoff with Jitter in `kis_broker.py`.
2. **Critical**: Pre-allocation / Outbox pattern in `portfolio.py` to ensure transactional atomicity between broker fills and SQLite records.
3. **High**: Re-entrant mutex on `authenticate()` and 401 re-auth hooks in `kis_broker.py`.
4. **High**: Throttling `yfinance` concurrency to 4 workers and standardizing DataFrame column unwrapping.
5. **High**: US Trading Calendar & Holiday provider in `portfolio_guardian.py` and `autopilot_trader.py`.

---

## 5. Verification Method

To independently verify the observations:

1. **Verify Token Collision Risk**:
   ```python
   # Run concurrent authenticate calls in python:
   import concurrent.futures
   from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
   with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
       results = list(ex.map(lambda _: default_kis_broker.authenticate(force_refresh=True), range(5)))
   ```
2. **Verify TPS Limit Under Price Loop**:
   - Inspect `kis_broker.py:354-385` and trace calls from `portfolio_guardian.py:115` across 10 positions (30 TR calls back-to-back with zero rate limiting delay).
3. **Verify Dual-Write Ordering**:
   - Inspect `al_sangmoo/interfaces/api/routers/portfolio.py:134-156` confirming `default_kis_broker.place_order` executes on line 134, while `db_manager.add_portfolio_buy` executes on line 150.
4. **Verify Market Calendar Absence**:
   - Inspect `portfolio_guardian.py:74-85` confirming infinite `while self.is_running:` loop sleeps only for `self.interval` (10s) with zero day-of-week or holiday checks.
