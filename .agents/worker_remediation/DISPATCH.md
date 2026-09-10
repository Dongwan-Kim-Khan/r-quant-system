## 2026-08-26T07:16:44Z
You are a Worker subagent assigned to implement the end-to-end security, architectural, logic, and code quality remediations for the Al-Sangmoo Institutional Quant Trading Platform.

Your working directory is: d:\코딩\R\.agents\worker_remediation
Workspace directory: d:\코딩\R
Original user request file: d:\코딩\R\.agents\ORIGINAL_REQUEST.md
Project plan file: d:\코딩\R\PROJECT.md

Read the survey reports for complete context:
- `d:\코딩\R\.agents\explorer_survey_security\handoff.md`
- `d:\코딩\R\.agents\explorer_survey_sync_realtime\handoff.md`
- `d:\코딩\R\.agents\explorer_survey_concurrency_quant\handoff.md`

Detailed Tasks to Implement:
1. `server.py`:
   - Add `import json` to module-level imports.
   - Enforce proper Origin validation if Origin header is present.
2. `al_sangmoo/domain/risk/order_guardrail.py`:
   - Export shared singleton `ORDER_MUTEX = asyncio.Lock()`.
3. `al_sangmoo/interfaces/api/routers/broker.py`:
   - Import `ORDER_MUTEX` from `al_sangmoo.domain.risk.order_guardrail`.
   - Wrap lines 72-132 completely within `async with ORDER_MUTEX:` (so pre-trade guardrails, broker order placement, and SQLite persistence are atomic).
   - Offload synchronous broker calls (`default_kis_broker.get_overseas_balance()`, `default_kis_broker.place_order(...)`) via `await asyncio.to_thread(...)`.
   - Add `db_manager.record_execution_log(...)` on successful order execution for real-time audit log parity.
4. `al_sangmoo/interfaces/api/routers/portfolio.py`:
   - Import `ORDER_MUTEX` from `al_sangmoo.domain.risk.order_guardrail`.
   - Offload synchronous broker calls via `await asyncio.to_thread(...)`.
   - In `buy_stock`, `sell_stock`, and `buy_top_pick`, add `db_manager.record_execution_log(...)` on successful executions.
5. `al_sangmoo/infrastructure/persistence.py`:
   - In `record_portfolio_sell`, ensure the closed position is inserted into `trade_history` table so `/api/portfolio/history` is populated.
   - Update legacy -3.0% / 0.97 stop-loss calculations (lines 469, 474) to -4.0% / 0.96.
6. Quant SSOT Stop-Loss Alignment (-4.0% hard stop / 0.96):
   - `al_sangmoo/domain/reconciliation.py`: Lines 94, 140, 181 -> replace `b_avg * 0.97` with `b_avg * 0.96`.
   - `al_sangmoo/domain/risk/macro_guardrail.py`: Lines 28, 50, 52 -> replace `0.97` / `-3%` with `0.96` / `-4%`.
   - `al_sangmoo/infrastructure/brokers/paper_broker.py`: Line 36 -> replace `fill_price * 0.97` with `fill_price * 0.96`.
   - `al_sangmoo/domain/risk/position_sizer.py`: Line 66 -> replace `0.03` with `0.04`.
   - `al_sangmoo_daily_bot.py`: Lines 243, 246, 254, 311 -> replace `0.97` / `-3.0%` with `0.96` / `-4.0%`.
7. `al_sangmoo/infrastructure/brokers/kis_broker.py`:
   - Use `atomic_save_json` from `al_sangmoo.infrastructure.atomic_io` for `.kis_token_*.json` OAuth token caching.
8. Frontend Synchronicity:
   - `frontend/js/websocket.js`: Update portfolio directly from `msg.data` on `portfolio_update` without triggering redundant full HTTP dashboard reloads.
   - `frontend/js/chart.js`: Check `if (this.currentTicker !== ticker) return;` before updating DOM on chart response.
9. Deduplication & Cleanup:
   - `al_sangmoo/interfaces/api/routers/charts.py`: Delete duplicate lines 51-56.
   - `youtube_stream_scanner.py` & `generate_dashboard_feed.py`: Use centralized `constants.py` and `atomic_io.py`.
   - `tools_and_tests/test_strategy1_tpsl_grid.py` & `tools_and_tests/test_tpsl_grid.py`: Add `__test__ = False` so pytest ignores research parameter matrices.
10. Automated Test Suite Execution & Verification:
   - Run:
     ```powershell
     python -m pytest tools_and_tests/test_phase5_1_security.py tools_and_tests/test_phase5_2_concurrency.py tools_and_tests/test_phase5_3_ssot_quant.py tools_and_tests/test_phase5_4_kis_modular.py tests/test_autopilot.py -v
     ```
   - Ensure all tests pass 100% with zero errors.
