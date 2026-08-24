## 2026-08-22T04:58:56Z
You are Backend Security Worker implementing Backend Security Hardening (R1, R2, R3, R4, R5) for the Al-Sangmoo Quant Trading Platform.

Your working directory is: d:\코딩\Playground\al_sangmoo_project\.agents\worker_backend
Project root directory: d:\코딩\Playground\al_sangmoo_project
Original User Request is located at: d:\코딩\Playground\al_sangmoo_project\.agents\ORIGINAL_REQUEST.md
Survey Blueprint: d:\코딩\Playground\al_sangmoo_project\.agents\explorer_survey_1\survey_backend.md
Project plan: d:\코딩\Playground\al_sangmoo_project\PROJECT.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Your Exclusively Owned Files:
- `server.py`
- `al_sangmoo/api/hub.py`
- `al_sangmoo/infrastructure/persistence.py`
- `youtube_stream_scanner.py`

Instructions:
1. Read `ORIGINAL_REQUEST.md` and `survey_backend.md`.
2. Implement R1 (Stored XSS Backend Remediation):
   - In `server.py`: Enforce `REASON_REGEX` (`^[A-Za-z0-9_\-\s\(\)가-힣.,%]{1,100}$`) and max length (100 chars) on `SellOrder.reason`.
   - In `al_sangmoo/infrastructure/persistence.py`: Enforce regex validation & defensive sanitization in `record_portfolio_sell()`.
3. Implement R2 (CORS Whitelisting, CSWSH & DoS Prevention):
   - In `server.py`: Remove `"*"` wildcard from `CORSMiddleware`. Restrict `allow_origins` strictly to `["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:3000", "http://127.0.0.1:3000"]`.
   - In `server.py`: Enforce WebSocket handshake origin checking in `/ws/live_feed` (reject unauthorized origins with close code 1008).
   - In `al_sangmoo/api/hub.py`: Enforce `MAX_CONNECTIONS = 50` ceiling in `WebSocketBroadcastHub.connect()`, rejecting excess connections with close code 1008; use non-blocking broadcast dispatch with timeouts.
4. Implement R3 (Path Traversal & Subprocess Hardening):
   - In `youtube_stream_scanner.py`: Validate YouTube video IDs with strict regex `^[a-zA-Z0-9_-]{11}$` before filesystem path construction or `yt-dlp` execution; validate that resolved paths reside within `BASE_DIR` using `os.path.abspath`.
   - In `server.py`: Enforce `os.path.abspath(chart_file).startswith(os.path.abspath(CHARTS_DIR))` in `get_ticker_chart`.
5. Implement R4 (Pydantic Validation & Global Error Masking):
   - In `server.py`: Add validators on `BuyOrder` and `SellOrder` for date regex (`^\d{4}-\d{2}-\d{2}$`), price (`> 0`, `<= 10_000_000`), quantity (`> 0`, `<= 1_000_000`), ticker regex (`^[A-Za-z0-9.\^=-]{1,15}$`).
   - In `server.py`: Add global exception handler for `Exception` returning `{"status": "error", "message": "An internal server error occurred"}` with HTTP 500 without leaking stack traces.
6. Implement R5 (OWASP Security Headers):
   - In `server.py`: Add HTTP middleware attaching `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, and `X-XSS-Protection: 1; mode=block`.
7. Execute existing test suites (`python tools_and_tests/test_phase1_hardening.py`, `python tools_and_tests/test_phase2_modular.py`, `python tools_and_tests/test_phase4_execution.py`) to verify zero regressions.
8. Write comprehensive report in `handoff.md` and send completion message via `send_message`.
