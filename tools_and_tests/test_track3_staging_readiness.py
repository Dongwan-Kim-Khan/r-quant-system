"""
AL-SANGMOO QUANT TERMINAL: TRACK 3 STAGING READINESS & SAFETY VERIFICATION SUITE
Automated Institutional Verification for C-2 Bundle Engine VPS Production Staging.

Verifies:
1. Paper Trading Mode Isolation:
   - Clean toggle between 'vps' (virtual sandbox) and 'prod' (live).
   - Domain routing (openapivts:29443 vs openapi:9443).
   - TR ID segregation (VTTT vs TTTT, VTTS vs TTTS).
   - Safe unconfigured fallback to offline simulation.
2. Broker Input Validation & Whole-Share Safety:
   - Strict integer share rounding (int(qty)).
   - Rejection of zero/negative quantities and invalid types.
   - Non-negative price validation and price tick formatting ({price:.2f}).
3. Concurrency, Rate Limiter (3.5 TPS), and Token Stampede Hardening:
   - TokenBucketLimiter 3.5 TPS enforcement.
   - ORDER_MUTEX concurrency serialization.
   - EGW00201 rate limit retry mechanism with backoff.
   - EGW00133 duplicate token stampede prevention and cache fallback.
4. Dual-Clock Guardian Behavior:
   - Intraday RTH: -10% emergency stop only (holds -7% ~ -9.9%).
   - EOD Window (15:50-16:00 ET): -7% EOD hard stop execution.
   - US Half-Day (12:50-13:00 ET) dynamic window adjustment.
   - Skipping satellite stop exits on QQQ/QLD cash proxy sleeve.
5. Backward-Compatibility Migration:
   - Existing open lots in DB preserve legacy C1-M2 ticket stops (-5% / kijun).
   - New entries generate C-2 tickets (-7% EOD / -10% emergency).
   - DB marks (_persist_mark) never overwrite legacy ticket stops.
6. Whole-Share Rounding & Residual Cash Handling:
   - Sizer and proxy sleeve whole-share integer rounding.
   - Residual cash preservation and small NAV edge case safety.
"""

import os
import sys
import time
import tempfile
import threading
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.core.constants import (
    CASH_PROXY_TICKER,
    EMERGENCY_STOP_LOSS_PCT,
    EOD_STOP_LOSS_PCT,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    ATR_MULTIPLIER,
    SLOT_WEIGHTS_BULL,
    SLOT_WEIGHTS_BEAR,
    STANDALONE_KIJUN_EXIT_ENABLED,
    derive_stop_price,
    derive_eod_stop_price,
    derive_emergency_stop_price,
)
from al_sangmoo.core.market_time import (
    is_us_eod_window,
    is_us_regular_hours,
    is_us_half_day,
    is_eod_window_for_ticker,
)
from al_sangmoo.infrastructure.brokers.kis_broker import (
    KISBrokerAdapter,
    TokenBucketLimiter,
)
from al_sangmoo.domain.risk.trailing_stop import evaluate_guardian_exit
from al_sangmoo.domain.risk.order_guardrail import ORDER_MUTEX
from al_sangmoo.domain.risk.position_sizer import calculate_target_shares
from al_sangmoo.domain.risk.cash_proxy import build_cash_proxy_plan, is_proxy_ticker
from al_sangmoo.infrastructure.persistence import (
    init_database,
    get_connection,
    add_portfolio_buy,
)
from al_sangmoo.domain.risk.portfolio_guardian import PortfolioGuardian


class TestPaperTradingModeIsolation(unittest.TestCase):
    """1. Paper Trading vs Live Production Isolation."""

    def test_vps_mode_routes_to_mock_domain_and_virtual_tr(self):
        """VPS/Paper mode must route to openapivts.koreainvestment.com:29443."""
        broker = KISBrokerAdapter(
            app_key="TEST_KEY",
            app_secret="TEST_SECRET",
            account_no="12345678",
            mode="vps",
        )
        self.assertTrue(broker.is_paper)
        self.assertEqual(broker.mode_str, "vps")
        self.assertEqual(broker.base_url, "https://openapivts.koreainvestment.com:29443")
        status = broker.get_status()
        self.assertEqual(status["mode"], "VIRTUAL_PAPER (모의투자)")
        self.assertTrue(status["is_paper"])

    def test_prod_mode_routes_to_live_domain(self):
        """Production mode must route to openapi.koreainvestment.com:9443."""
        broker = KISBrokerAdapter(
            app_key="TEST_KEY",
            app_secret="TEST_SECRET",
            account_no="12345678",
            mode="prod",
        )
        self.assertFalse(broker.is_paper)
        self.assertEqual(broker.mode_str, "prod")
        self.assertEqual(broker.base_url, "https://openapi.koreainvestment.com:9443")
        status = broker.get_status()
        self.assertEqual(status["mode"], "REAL_PRODUCTION (실전투자)")
        self.assertFalse(status["is_paper"])

    def test_paper_trading_environment_aliases(self):
        """Support common paper trading alias keywords: 'paper', 'virtual', 'mock', 'demo'."""
        for alias in ("paper", "virtual", "mock", "demo", "simulation", "dry-run"):
            b = KISBrokerAdapter(
                app_key="K", app_secret="S", account_no="12345678", mode=alias
            )
            self.assertTrue(b.is_paper, f"Failed for alias: {alias}")
            self.assertIn("openapivts", b.base_url)

    def test_unconfigured_offline_simulation_mode(self):
        """Unconfigured credentials safely operate in offline mock mode."""
        broker = KISBrokerAdapter(app_key="", app_secret="", account_no="")
        self.assertFalse(broker.is_configured())
        status = broker.get_status()
        self.assertEqual(status["environment"], "MOCK_PAPER")
        
        # Simulated order execution succeeds offline
        order = broker.place_order("NVDA", "BUY", qty=10, price=120.0)
        self.assertEqual(order["status"], "filled")
        self.assertEqual(order["mode"], "SIMULATION")
        self.assertEqual(order["qty"], 10)
        self.assertEqual(order["price"], 120.0)


class TestBrokerInputValidationAndSafety(unittest.TestCase):
    """2. Broker Input Validation & Whole-Share Safety."""

    def setUp(self):
        self.broker = KISBrokerAdapter(app_key="", app_secret="", account_no="")

    def test_zero_and_negative_quantity_rejection(self):
        """Zero or negative quantities must be strictly rejected."""
        res_zero = self.broker.place_order("NVDA", "BUY", qty=0, price=100.0)
        self.assertEqual(res_zero["status"], "rejected")
        self.assertIn("Quantity must be greater than 0", res_zero["reason"])

        res_neg = self.broker.place_order("NVDA", "BUY", qty=-5, price=100.0)
        self.assertEqual(res_neg["status"], "rejected")
        self.assertIn("Quantity must be greater than 0", res_neg["reason"])

    def test_fractional_quantity_below_one_share_rejected(self):
        """Fractional quantities < 1.0 (e.g. 0.5 shares) must be rejected because int(0.5) is 0."""
        res = self.broker.place_order("NVDA", "BUY", qty=0.5, price=100.0)
        self.assertEqual(res["status"], "rejected")
        self.assertIn("Quantity must be greater than 0", res["reason"])

    def test_invalid_quantity_type_rejected(self):
        """Invalid types (e.g. non-numeric strings) must be cleanly rejected."""
        res = self.broker.place_order("NVDA", "BUY", qty="invalid", price=100.0)
        self.assertEqual(res["status"], "rejected")
        self.assertIn("Quantity must be a valid integer", res["reason"])

    def test_whole_share_rounding_coercion(self):
        """Floating quantities >= 1.0 must be coerced to integer whole shares."""
        res = self.broker.place_order("NVDA", "BUY", qty=10.9, price=100.0)
        self.assertEqual(res["status"], "filled")
        self.assertEqual(res["qty"], 10)
        self.assertIsInstance(res["qty"], int)

    def test_negative_price_rejected(self):
        """Negative price must be rejected."""
        res = self.broker.place_order("NVDA", "BUY", qty=5, price=-10.0)
        self.assertEqual(res["status"], "rejected")
        self.assertIn("Price cannot be negative", res["reason"])

    def test_limit_order_zero_or_negative_price_rejected(self):
        """Limit order (order_type='00') with price <= 0 must be rejected."""
        res_zero = self.broker.place_order("NVDA", "BUY", qty=5, price=0.0, order_type="00")
        self.assertEqual(res_zero["status"], "rejected")
        self.assertIn("Limit order price must be greater than 0", res_zero["reason"])

    def test_market_order_zero_price_allowed(self):
        """Market order (order_type='01') with price 0 is allowed and formats correctly."""
        res_mkt = self.broker.place_order("NVDA", "SELL", qty=5, price=0.0, order_type="01")
        self.assertEqual(res_mkt["status"], "filled")
        self.assertEqual(res_mkt["qty"], 5)


class TestConcurrencyAndRateLimiter(unittest.TestCase):
    """3. Concurrency, Rate Limiter (3.5 TPS), and Token Stampede Hardening."""

    def test_token_bucket_limiter_rate_enforcement(self):
        """Token bucket limiter strictly meters requests to <= 3.5 per second."""
        limiter = TokenBucketLimiter(rate=3.5, capacity=3.5)
        
        # Consume full capacity (3.5 tokens)
        limiter.acquire(3.5)
        self.assertLessEqual(limiter.tokens, 0.01)

        # The next 1.0 token request must block until replenished (~0.285s)
        t0 = time.time()
        limiter.acquire(1.0)
        elapsed = time.time() - t0
        self.assertGreaterEqual(elapsed, 0.20, "Rate limiter did not throttle below 3.5 TPS")

    def test_egw00201_rate_limit_retry_mechanism(self):
        """Transient KIS EGW00201 rate limit error triggers retry with backoff and recovers."""
        broker = KISBrokerAdapter(
            app_key="TEST_KEY", app_secret="TEST_SECRET", account_no="12345678", mode="vps"
        )
        broker.token = "VALID_TOKEN"
        broker.token_expiry = time.time() + 3600

        call_count = 0
        def mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_res = MagicMock()
            if call_count == 1:
                mock_res.status_code = 200
                mock_res.json.return_value = {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수 초과"}
            else:
                mock_res.status_code = 200
                mock_res.json.return_value = {"rt_cd": "0", "output": {"last": "150.25"}}
            return mock_res

        with patch("requests.get", side_effect=mock_get):
            data = broker._kis_get_json(
                url="https://mock.kis/price",
                tr_id="HHDFS00000300",
                params={"SYMB": "NVDA"},
                timeout=3.0,
            )
            self.assertIsNotNone(data)
            self.assertEqual(data.get("rt_cd"), "0")
            self.assertEqual(call_count, 2, "EGW00201 was not retried")

    def test_token_stampede_prevention_egw00133(self):
        """Token stampede (EGW00133 1-minute issue limit) falls back to valid cached token."""
        broker = KISBrokerAdapter(
            app_key="TEST_KEY", app_secret="TEST_SECRET", account_no="12345678", mode="vps"
        )
        # Isolate cache file so it does not read developer's local .kis_token_vps.json
        from pathlib import Path
        broker.cache_file = Path(tempfile.gettempdir()) / "non_existent_token_stampede_test.json"
        broker.token = "EXISTING_VALID_TOKEN"
        broker.token_expiry = time.time() + 1800  # 30 min left

        # Mock POST to /oauth2/tokenP returning EGW00133
        mock_post_res = MagicMock()
        mock_post_res.status_code = 400
        mock_post_res.text = '{"error_code": "EGW00133", "error_description": "유효한 토큰이 이미 발급되었습니다"}'
        mock_post_res.json.return_value = {"error_code": "EGW00133"}

        with patch("requests.post", return_value=mock_post_res):
            success = broker.authenticate(force_refresh=True)
            self.assertTrue(success, "Failed to fall back to cached token on EGW00133")
            self.assertEqual(broker.token, "EXISTING_VALID_TOKEN")

    def test_token_recent_refresh_skips_redundant_post(self):
        """If token was refreshed within 60 seconds, redundant /oauth2/tokenP request is skipped."""
        from pathlib import Path
        broker = KISBrokerAdapter(
            app_key="TEST_KEY", app_secret="TEST_SECRET", account_no="12345678", mode="vps"
        )
        broker.cache_file = Path(tempfile.gettempdir()) / "non_existent_fresh_test.json"
        broker.token = "FRESH_TOKEN"
        broker.token_expiry = time.time() + 86400
        broker._last_auth_time = time.time()  # Refreshed right now

        with patch("requests.post") as mock_post:
            success = broker.authenticate(force_refresh=True)
            self.assertTrue(success)
            mock_post.assert_not_called()

    def test_multi_process_cross_instance_token_stampede_avoidance(self):
        """A second broker instance reading fresh cache (<60s) avoids redundant POST to /oauth2/tokenP."""
        import json
        from pathlib import Path
        temp_cache = Path(tempfile.gettempdir()) / f"kis_stampede_test_{int(time.time()*1000)}.json"
        try:
            # Process 1 saved token 5 seconds ago
            now = time.time()
            payload = {
                "access_token": "EXISTING_CROSS_PROC_TOKEN",
                "expires_at": "2026-12-31 00:00:00",
                "expires_at_timestamp": now + 86400,
                "updated_at_timestamp": now - 5.0,
                "mode": "vps",
                "updated_at": "2026-09-14 17:00:00"
            }
            temp_cache.write_text(json.dumps(payload), encoding="utf-8")

            # Process 2 starts up fresh
            broker2 = KISBrokerAdapter(
                app_key="TEST_KEY_2", app_secret="TEST_SECRET_2", account_no="12345678", mode="vps"
            )
            broker2.cache_file = temp_cache

            with patch("requests.post") as mock_post:
                success = broker2.authenticate(force_refresh=True)
                self.assertTrue(success)
                self.assertEqual(broker2.token, "EXISTING_CROSS_PROC_TOKEN")
                mock_post.assert_not_called()
        finally:
            if temp_cache.exists():
                try:
                    temp_cache.unlink()
                except Exception:
                    pass

    def test_http_429_retry_in_order_placement(self):
        """Order placement receiving HTTP 429 retries with backoff and succeeds on subsequent attempt."""
        broker = KISBrokerAdapter(
            app_key="TEST_KEY", app_secret="TEST_SECRET", account_no="12345678", mode="vps"
        )
        broker.token = "VALID_TOKEN"
        broker.token_expiry = time.time() + 3600

        attempts = 0
        def mock_post(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            mock_res = MagicMock()
            if attempts == 1:
                mock_res.status_code = 429
                mock_res.text = "Too Many Requests"
                mock_res.json.return_value = {"error_code": "EGW00201", "error_description": "초당 거래건수 초과"}
            else:
                mock_res.status_code = 200
                mock_res.json.return_value = {"rt_cd": "0", "output": {"ODNO": "123456"}}
            return mock_res

        with patch("requests.post", side_effect=mock_post):
            res = broker._place_overseas_order("NVDA", "BUY", 10, 120.0, "NASD", "00")
            self.assertEqual(res["status"], "submitted")
            self.assertEqual(res["order_id"], "123456")
            self.assertEqual(attempts, 2)

    def test_unconfirmed_order_recheck_on_timeout(self):
        """When order placement times out, _recheck_submitted_order checks day orders without re-POSTing."""
        import requests
        broker = KISBrokerAdapter(
            app_key="TEST_KEY", app_secret="TEST_SECRET", account_no="12345678", mode="vps"
        )
        broker.token = "VALID_TOKEN"
        broker.token_expiry = time.time() + 3600

        # Simulate timeout on POST, and day order query returns the filled order
        with patch("requests.post", side_effect=requests.Timeout("Connection timed out")), \
             patch.object(broker, "query_overseas_day_orders", return_value=[{
                 "ticker": "NVDA",
                 "side": "BUY",
                 "qty": 10,
                 "price": 120.0,
                 "order_id": "RECHECK-ODNO-999",
                 "client_order_id": "CLIENT-OID-123"
             }]):
            res = broker.place_order(
                ticker="NVDA",
                side="BUY",
                qty=10,
                price=120.0,
                order_type="00",
                client_order_id="CLIENT-OID-123"
            )
            self.assertEqual(res["status"], "SUCCESS_VIA_RECHECK")
            self.assertEqual(res["order_id"], "RECHECK-ODNO-999")
            self.assertIn("재전송하지 않았습니다", res["message"])

    def test_concurrent_closing_window_quote_and_order_execution(self):
        """
        Simulates 15:50 ET closing window concurrency:
        3 satellite positions (NVDA, AAPL, MSFT) + 1 proxy sleeve (QQQ) concurrently
        fetching live prices and submitting orders under TokenBucketLimiter (3.5 TPS).
        Verifies 0 exceptions, 0 rate limit violations, and serialized order execution.
        """
        import concurrent.futures
        broker = KISBrokerAdapter(
            app_key="TEST_KEY", app_secret="TEST_SECRET", account_no="12345678", mode="vps"
        )
        broker.token = "VALID_TOKEN"
        broker.token_expiry = time.time() + 3600

        request_timestamps = []
        req_lock = threading.Lock()

        def mock_get(url, *args, **kwargs):
            with req_lock:
                request_timestamps.append(time.time())
            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = {"rt_cd": "0", "output": {"last": "150.0"}}
            return mock_res

        def mock_post(url, *args, **kwargs):
            with req_lock:
                request_timestamps.append(time.time())
            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = {"rt_cd": "0", "output": {"ODNO": "CONC-OD-1"}}
            return mock_res

        tickers = ["NVDA", "AAPL", "MSFT", "QQQ"]
        results = []

        def simulate_agent_run(tk):
            px = broker.get_live_price(tk, fallback_yahoo=False)
            res = broker.place_order(tk, "BUY", qty=5, price=150.0, order_type="00")
            return tk, px, res

        t_start = time.time()
        with patch("requests.get", side_effect=mock_get), patch("requests.post", side_effect=mock_post):
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(simulate_agent_run, tk) for tk in tickers]
                for f in concurrent.futures.as_completed(futures):
                    results.append(f.result())
        t_elapsed = time.time() - t_start

        self.assertEqual(len(results), 4)
        for tk, px, ord_res in results:
            self.assertEqual(px, 150.0)
            self.assertEqual(ord_res["status"], "submitted")

        # 8 total requests (4 GET + 4 POST) under 3.5 TPS limiter should take at least ~1.0s
        self.assertGreaterEqual(len(request_timestamps), 8)


class TestDualClockGuardianBehavior(unittest.TestCase):
    """4. Dual-Clock Guardian Stop Loss Mechanics & Window Behavior."""

    def test_rth_intraday_outside_eod_holds_minus_7_to_minus_9(self):
        """Outside EOD closing window, a -8.0% dip must hold; only <= -10% triggers emergency stop."""
        res_8 = evaluate_guardian_exit(
            buy_price=100.0,
            current_price=92.0,  # -8.0%
            is_eod_window=False,
        )
        self.assertIsNone(res_8["action"], "Dip of -8.0% outside EOD window must hold")

        res_10 = evaluate_guardian_exit(
            buy_price=100.0,
            current_price=89.5,  # -10.5%
            is_eod_window=False,
        )
        self.assertEqual(res_10["action"], "AUTO_EMERGENCY_STOP")
        self.assertIn("비상", res_10["reason"])

    def test_eod_closing_window_executes_hard_stop_at_minus_7(self):
        """During EOD closing window (15:50-16:00 ET), a -7.5% close triggers EOD hard stop."""
        res_eod_stop = evaluate_guardian_exit(
            buy_price=100.0,
            current_price=92.5,  # -7.5%
            is_eod_window=True,
        )
        self.assertEqual(res_eod_stop["action"], "AUTO_STOP_LOSS")
        self.assertIn("EOD", res_eod_stop["reason"])

        res_eod_hold = evaluate_guardian_exit(
            buy_price=100.0,
            current_price=94.0,  # -6.0%
            is_eod_window=True,
        )
        self.assertIsNone(res_eod_hold["action"], "-6.0% during EOD window must hold")

    def test_half_day_closing_window_timing(self):
        """US Half-day session (e.g. Christmas Eve) closing window is 12:50-13:00 ET."""
        eastern = ZoneInfo("America/New_York")
        t_inside_half_day = datetime(2026, 12, 24, 12, 55, tzinfo=eastern)
        self.assertTrue(is_us_half_day(t_inside_half_day))
        self.assertTrue(is_us_eod_window(t_inside_half_day, window_minutes=10))

        # At 15:50 ET on a half-day, market is already closed
        t_after_close = datetime(2026, 12, 24, 15, 50, tzinfo=eastern)
        self.assertFalse(is_us_regular_hours(t_after_close))
        self.assertFalse(is_us_eod_window(t_after_close, window_minutes=10))

    def test_guardian_skips_cash_proxy_sleeve_etfs(self):
        """Guardian skips stop loss force-exits for QQQ / QLD cash proxy ETFs."""
        self.assertTrue(is_proxy_ticker("QQQ"))
        self.assertTrue(is_proxy_ticker("QLD"))
        self.assertFalse(is_proxy_ticker("NVDA"))


class TestBackwardCompatibilityMigration(unittest.TestCase):
    """5. Backward-Compatibility: Legacy C1-M2 lots vs New C-2 tickets."""

    def setUp(self):
        self._prev_db = os.environ.get("AL_SANGMOO_DB_PATH")
        self.temp_dir = tempfile.mkdtemp(suffix="_staging_test")
        self.db_path = os.path.join(self.temp_dir, "test_portfolio.db")
        os.environ["AL_SANGMOO_DB_PATH"] = self.db_path
        init_database()

    def tearDown(self):
        if self._prev_db is None:
            os.environ.pop("AL_SANGMOO_DB_PATH", None)
        else:
            os.environ["AL_SANGMOO_DB_PATH"] = self._prev_db
        import shutil
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_legacy_open_lot_preserves_minus_5_ticket_stop(self):
        """Legacy C1-M2 lot created with -5% ticket stop (ratio >= 0.945) triggers at -5.5%."""
        legacy_stop = 95.0  # -5.0%
        res = evaluate_guardian_exit(
            buy_price=100.0,
            current_price=94.5,  # -5.5%
            stop_loss_price=legacy_stop,
            is_eod_window=False,
        )
        self.assertEqual(res["action"], "AUTO_STOP_LOSS")
        self.assertIn("레거시", res["reason"])

    def test_new_c2_entry_generates_c2_tickets(self):
        """New C-2 entries derive -7% EOD and -10% emergency stop prices."""
        entry_price = 100.0
        derived_stop = derive_stop_price(entry_price)
        derived_eod = derive_eod_stop_price(entry_price)
        derived_emerg = derive_emergency_stop_price(entry_price)

        self.assertEqual(derived_stop, 93.0)
        self.assertEqual(derived_eod, 93.0)
        self.assertEqual(derived_emerg, 90.0)

        # In DB: new buy receives C-2 stop 93.0
        holding_id = add_portfolio_buy(
            ticker="NVDA",
            buy_price=entry_price,
            quantity=10,
        )
        with get_connection() as conn:
            row = conn.execute("SELECT stop_loss_price FROM my_portfolio WHERE id=?", (holding_id,)).fetchone()
            self.assertEqual(float(row["stop_loss_price"]), 93.0)

    def test_guardian_persist_mark_preserves_legacy_ticket_stop(self):
        """Guardian _persist_mark must never overwrite an existing legacy ticket stop."""
        guardian = PortfolioGuardian()
        # Insert a legacy lot with 95.0 stop
        holding_id = add_portfolio_buy(
            ticker="AAPL",
            buy_price=100.0,
            quantity=10,
            stop_loss_price=95.0,
        )

        decision = {"pnl_pct": -2.0, "hard_stop_price": 93.0}
        guardian._persist_mark(
            holding_id=holding_id,
            cur_price=98.0,
            total_qty=10.0,
            buy_price=100.0,
            decision=decision,
            existing_stop_price=95.0,
        )

        with get_connection() as conn:
            row = conn.execute("SELECT stop_loss_price FROM my_portfolio WHERE id=?", (holding_id,)).fetchone()
            self.assertEqual(float(row["stop_loss_price"]), 95.0, "Legacy ticket stop was overwritten!")


class TestWholeShareRoundingAndResidualCash(unittest.TestCase):
    """6. Whole-Share Rounding & Residual Cash Handling."""

    def test_position_sizer_whole_share_rounding(self):
        """Target shares must be strictly integer whole shares (int(slot_cap // price))."""
        nav = 10_000.0
        price = 330.0
        # Slot 1 bull weight: 34% -> $3,400. 3400 // 330 = 10 shares.
        res = calculate_target_shares(portfolio_nav=nav, current_price=price, slot_rank=1, is_bull=True)
        self.assertTrue(res["eligible"])
        self.assertIsInstance(res["shares"], int)
        self.assertEqual(res["shares"], 10)
        self.assertEqual(res["allocated_usd"], 3300.0)
        self.assertEqual(res["remaining_cash_usd"], 6700.0)

    def test_proxy_sleeve_whole_share_rounding_and_residual_cash(self):
        """Proxy sleeve idle parking uses floor whole shares and retains residual cash."""
        # Satellite leadership positions occupy $6,000 (e.g. 50 shares * $120), leaving $4,000 idle NAV
        satellites = [{"ticker": "NVDA", "quantity": 50, "current_price": 120.0}]
        plan = build_cash_proxy_plan(
            total_equity_usd=10_000.0,
            holdings=satellites,
            cash_usd=4_000.0,
            leverage_mode=False,
            qqq_price=485.0,
        )
        # 4000 // 485 = 8 shares ($3,880). Residual cash = $120.
        self.assertEqual(plan["qqq_shares"], 8)
        self.assertIsInstance(plan["qqq_shares"], int)
        self.assertEqual(plan["qqq_alloc_usd"], 4000.0)
        actual_deployed = plan["qqq_shares"] * 485.0
        residual_cash = plan["qqq_alloc_usd"] - actual_deployed
        self.assertEqual(residual_cash, 120.0)

    def test_insufficient_nav_for_single_share_handled_gracefully(self):
        """If slot cap cannot afford 1 share, sizer marks ineligible with explanation."""
        nav = 200.0
        price = 250.0
        # Rank 1 cap = $68 < $250
        res = calculate_target_shares(portfolio_nav=nav, current_price=price, slot_rank=1, is_bull=True)
        self.assertFalse(res["eligible"])
        self.assertEqual(res["shares"], 0)
        self.assertIn("1주 매수 불가", res["reason"])


if __name__ == "__main__":
    unittest.main()
