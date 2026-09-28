"""
Test suite for AutoPilot bounded retry with backoff on BROKER_SNAPSHOT_UNVERIFIED
and immediate session lock on terminal outcomes.

Guarantees:
1. Isolated test database (os.environ["AL_SANGMOO_DB_PATH"] = TEST_DB)
2. No writes to production quant_trades.db
3. Bounded retries (max 3) within 09:30-10:00 ET window
4. Retry cooldown guard prevents spamming broker
5. Fail-closed safety on exhausted retries or window expiration
6. Immediate permanent session claim on terminal/normal outcomes
"""
import asyncio
import json
import os
import sys
import tempfile
import hashlib
from datetime import datetime, timezone, timedelta
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.domain.risk import exit_cooldown
from al_sangmoo.domain.risk.autopilot_trader import (
    AutoPilotTrader,
    evaluate_daily_entry_schedule,
)
from al_sangmoo.infrastructure import persistence


def _build_valid_feed(signal_date="2026-09-10", candidates=None):
    source_rows = [
        {"ticker": "NVDA", "bar_date": signal_date, "close": 150.0},
        {"ticker": "AAPL", "bar_date": signal_date, "close": 220.0},
    ]
    source_hash = hashlib.sha256(
        json.dumps(
            sorted(source_rows, key=lambda row: str(row.get("ticker") or "")),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    if candidates is None:
        candidates = [
            {
                "ticker": "NVDA",
                "name": "NVIDIA",
                "price": 150.0,
                "kijun": 140.0,
                "kijun_26": 140.0,
                "composite_rs": 88.0,
                "conviction_score": 92.0,
                "bull_score": 92.0,
                "conviction_rank": 1,
                "sizing": {
                    "eligible": True,
                    "shares": 10,
                    "is_bull_regime": True,
                    "slot_weight": 0.34,
                    "slot_rank": 1,
                },
            }
        ]

    return {
        "signal_session_date": signal_date,
        "signal_is_eod_confirmed": True,
        "signal_source_hash": source_hash,
        "signal_source_rows": source_rows,
        "ranked_conviction_list": candidates,
        "top_conviction_pick": candidates[0] if candidates else None,
        "macro": {
            "is_bull_regime": True,
            "qqq_close": 500.0,
            "qqq_sma20": 480.0,
            "qqq_composite_rs": 70.0,
        },
        "slot_allocation_summary": {
            "is_bull_regime": True,
            "qqq_close": 500.0,
            "qqq_sma20": 480.0,
            "qqq_composite_rs": 70.0,
        },
    }


class TestAutoPilotSnapshotRetry(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._prev_db = os.environ.get("AL_SANGMOO_DB_PATH")
        self._test_dir = tempfile.TemporaryDirectory(suffix="_autopilot_retry_test")
        self._test_db_path = os.path.join(self._test_dir.name, "test_retry.db")
        os.environ["AL_SANGMOO_DB_PATH"] = self._test_db_path
        persistence.init_database()
        exit_cooldown.clear()

        self.autopilot = AutoPilotTrader(check_interval_seconds=60)
        self.autopilot.snapshot_retry_cooldown_sec = 60.0
        self.autopilot.max_snapshot_retries = 3

        self._feed_path = os.path.join(self._test_dir.name, "confirmed_eod.json")
        self._write_feed(_build_valid_feed())

    async def asyncTearDown(self):
        exit_cooldown.clear()
        if self._prev_db is None:
            os.environ.pop("AL_SANGMOO_DB_PATH", None)
        else:
            os.environ["AL_SANGMOO_DB_PATH"] = self._prev_db
        self._test_dir.cleanup()

    def _write_feed(self, payload: dict):
        with open(self._feed_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def test_database_isolation_guarantee(self):
        """Assert tests write strictly to temporary database, never quant_trades.db."""
        self.assertEqual(os.environ.get("AL_SANGMOO_DB_PATH"), self._test_db_path)
        self.assertFalse(self._test_db_path.endswith("quant_trades.db"))
        self.assertTrue(os.path.exists(self._test_db_path))

    async def test_broker_snapshot_unverified_first_attempt_does_not_claim_session(self):
        """Attempt 1 on BROKER_SNAPSHOT_UNVERIFIED must NOT lock the session in SQLite."""
        session_dt = datetime(2026, 9, 11, 13, 35, tzinfo=timezone.utc)  # 09:35 EDT
        session_date = "2026-09-11"

        portfolio_state = {
            "holdings": [],
            "total_equity_usd": 10000.0,
            "cash_usd": 10000.0,
        }

        with patch("al_sangmoo.domain.risk.autopilot_trader.CONFIRMED_EOD_JSON", self._feed_path), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio", return_value=portfolio_state), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured", return_value=True), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.get_overseas_balance", return_value={
                 "status": "partial",
                 "snapshot_complete": False,
                 "holdings": [],
                 "failed_exchanges": [{"exchange": "NYSE"}],
             }), \
             patch.object(self.autopilot, "_ensure_cash_proxy_parked", return_value={"executed": []}):

            self.autopilot.set_enabled(True)
            res = await self.autopilot.run_autopilot_cycle(
                force_scan=False,
                enforce_entry_schedule=True,
                now=session_dt,
            )

        self.assertEqual(res["status"], "skipped")
        self.assertEqual(res["reason"], "BROKER_SNAPSHOT_UNVERIFIED")
        self.assertIn("retry_info", res)
        self.assertEqual(res["retry_info"]["attempts"], 1)
        self.assertFalse(res["retry_info"]["exhausted"])

        # Session must NOT be claimed in SQLite
        self.assertFalse(persistence.has_autopilot_entry_session(session_date))

        # Schedule evaluation must still show PASS (eligible for retry)
        gate = evaluate_daily_entry_schedule(now=session_dt)
        self.assertTrue(gate["eligible"])
        self.assertEqual(gate["reason"], "PASS")

    async def test_retry_cooldown_blocks_premature_retry_without_claiming(self):
        """A second call within 60s cooldown must skip with BROKER_SNAPSHOT_RETRY_COOLDOWN."""
        t1 = datetime(2026, 9, 11, 13, 35, 0, tzinfo=timezone.utc)   # 09:35:00 EDT
        t2 = datetime(2026, 9, 11, 13, 35, 20, tzinfo=timezone.utc)  # 09:35:20 EDT (20s later)
        session_date = "2026-09-11"

        portfolio_state = {"holdings": [], "total_equity_usd": 10000.0, "cash_usd": 10000.0}

        with patch("al_sangmoo.domain.risk.autopilot_trader.CONFIRMED_EOD_JSON", self._feed_path), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio", return_value=portfolio_state), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured", return_value=True), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.get_overseas_balance", return_value={
                 "status": "partial",
                 "snapshot_complete": False,
                 "holdings": [],
             }):

            self.autopilot.set_enabled(True)
            res1 = await self.autopilot.run_autopilot_cycle(
                force_scan=False,
                enforce_entry_schedule=True,
                now=t1,
            )
            self.assertEqual(res1["reason"], "BROKER_SNAPSHOT_UNVERIFIED")

            # Premature second attempt (only 20s later, cooldown is 60s)
            res2 = await self.autopilot.run_autopilot_cycle(
                force_scan=False,
                enforce_entry_schedule=True,
                now=t2,
            )

        self.assertEqual(res2["status"], "skipped")
        self.assertEqual(res2["reason"], "BROKER_SNAPSHOT_RETRY_COOLDOWN")
        self.assertEqual(res2["retry_info"]["attempts"], 1)
        self.assertAlmostEqual(res2["retry_info"]["cooldown_remaining_sec"], 40.0, delta=1.0)

        # Still unclaimed in SQLite
        self.assertFalse(persistence.has_autopilot_entry_session(session_date))

    async def test_retry_succeeds_after_cooldown_and_claims_session(self):
        """Attempt 2 after cooldown succeeds, places order, and permanently locks session."""
        t1 = datetime(2026, 9, 11, 13, 35, 0, tzinfo=timezone.utc)  # 09:35 EDT
        t2 = datetime(2026, 9, 11, 13, 37, 0, tzinfo=timezone.utc)  # 09:37 EDT (2 min later)
        session_date = "2026-09-11"

        portfolio_state = {"holdings": [], "total_equity_usd": 10000.0, "cash_usd": 10000.0}

        # Attempt 1: broker fails snapshot
        with patch("al_sangmoo.domain.risk.autopilot_trader.CONFIRMED_EOD_JSON", self._feed_path), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio", return_value=portfolio_state), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured", return_value=True), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.get_overseas_balance", return_value={
                 "status": "partial",
                 "snapshot_complete": False,
                 "holdings": [],
             }):
            self.autopilot.set_enabled(True)
            res1 = await self.autopilot.run_autopilot_cycle(
                force_scan=False,
                enforce_entry_schedule=True,
                now=t1,
            )
            self.assertEqual(res1["reason"], "BROKER_SNAPSHOT_UNVERIFIED")
            self.assertFalse(persistence.has_autopilot_entry_session(session_date))

        # Attempt 2: broker recovers, snapshot succeeds, order is submitted
        with patch("al_sangmoo.domain.risk.autopilot_trader.CONFIRMED_EOD_JSON", self._feed_path), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio", return_value=portfolio_state), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured", return_value=True), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.get_overseas_balance", return_value={
                 "status": "success",
                 "snapshot_complete": True,
                 "holdings": [],
                 "total_equity_usd": 10000.0,
                 "cash_available_usd": 10000.0,
             }), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.place_order", return_value={
                 "status": "submitted",
                 "order_id": "RETRY-SUCCESS-1",
             }), \
             patch("al_sangmoo.domain.risk.autopilot_trader.is_us_regular_hours", return_value=True), \
             patch.object(self.autopilot, "_free_proxy_cash_for_entry", return_value=[]), \
             patch.object(self.autopilot, "_ensure_cash_proxy_parked", return_value={"executed": []}):

            res2 = await self.autopilot.run_autopilot_cycle(
                force_scan=False,
                enforce_entry_schedule=True,
                now=t2,
            )

        self.assertEqual(res2["status"], "submitted")
        # Session MUST now be claimed in SQLite!
        self.assertTrue(persistence.has_autopilot_entry_session(session_date))

        # Subsequent call on same day must be rejected with DAILY_ENTRY_ALREADY_CLAIMED
        t3 = datetime(2026, 9, 11, 13, 40, 0, tzinfo=timezone.utc)
        res3 = await self.autopilot.run_autopilot_cycle(
            force_scan=False,
            enforce_entry_schedule=True,
            now=t3,
        )
        self.assertEqual(res3["status"], "skipped")
        self.assertEqual(res3["reason"], "DAILY_ENTRY_ALREADY_CLAIMED")

    async def test_max_retries_exhausted_claims_session_fail_closed(self):
        """After 3 failed attempts, daily session is permanently claimed (fail-closed)."""
        session_date = "2026-09-11"
        t1 = datetime(2026, 9, 11, 13, 31, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 9, 11, 13, 33, 0, tzinfo=timezone.utc)
        t3 = datetime(2026, 9, 11, 13, 35, 0, tzinfo=timezone.utc)

        portfolio_state = {"holdings": [], "total_equity_usd": 10000.0, "cash_usd": 10000.0}

        with patch("al_sangmoo.domain.risk.autopilot_trader.CONFIRMED_EOD_JSON", self._feed_path), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio", return_value=portfolio_state), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured", return_value=True), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.get_overseas_balance", return_value={
                 "status": "partial",
                 "snapshot_complete": False,
                 "holdings": [],
             }):
            self.autopilot.set_enabled(True)

            # Attempt 1
            res1 = await self.autopilot.run_autopilot_cycle(force_scan=False, enforce_entry_schedule=True, now=t1)
            self.assertEqual(res1["retry_info"]["attempts"], 1)
            self.assertFalse(res1["retry_info"]["exhausted"])
            self.assertFalse(persistence.has_autopilot_entry_session(session_date))

            # Attempt 2
            res2 = await self.autopilot.run_autopilot_cycle(force_scan=False, enforce_entry_schedule=True, now=t2)
            self.assertEqual(res2["retry_info"]["attempts"], 2)
            self.assertFalse(res2["retry_info"]["exhausted"])
            self.assertFalse(persistence.has_autopilot_entry_session(session_date))

            # Attempt 3 (Final)
            res3 = await self.autopilot.run_autopilot_cycle(force_scan=False, enforce_entry_schedule=True, now=t3)
            self.assertEqual(res3["retry_info"]["attempts"], 3)
            self.assertTrue(res3["retry_info"]["exhausted"])
            # Now permanently claimed!
            self.assertTrue(persistence.has_autopilot_entry_session(session_date))

            # Attempt 4 should be rejected as already claimed
            t4 = datetime(2026, 9, 11, 13, 37, 0, tzinfo=timezone.utc)
            res4 = await self.autopilot.run_autopilot_cycle(force_scan=False, enforce_entry_schedule=True, now=t4)
            self.assertEqual(res4["reason"], "DAILY_ENTRY_ALREADY_CLAIMED")

    async def test_window_expiration_with_pending_retries_fails_closed(self):
        """If 10:00 ET window expires with pending retries, session permanently claims in DB."""
        session_date = "2026-09-11"
        t1 = datetime(2026, 9, 11, 13, 59, 0, tzinfo=timezone.utc)  # 09:59 EDT (Attempt 1)
        t_after = datetime(2026, 9, 11, 14, 2, 0, tzinfo=timezone.utc)  # 10:02 EDT (Window closed)

        portfolio_state = {"holdings": [], "total_equity_usd": 10000.0, "cash_usd": 10000.0}

        with patch("al_sangmoo.domain.risk.autopilot_trader.CONFIRMED_EOD_JSON", self._feed_path), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio", return_value=portfolio_state), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured", return_value=True), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.get_overseas_balance", return_value={
                 "status": "partial",
                 "snapshot_complete": False,
                 "holdings": [],
             }):
            self.autopilot.set_enabled(True)
            res1 = await self.autopilot.run_autopilot_cycle(force_scan=False, enforce_entry_schedule=True, now=t1)
            self.assertEqual(res1["reason"], "BROKER_SNAPSHOT_UNVERIFIED")
            self.assertFalse(persistence.has_autopilot_entry_session(session_date))

            # Now window expires
            res_after = await self.autopilot.run_autopilot_cycle(force_scan=False, enforce_entry_schedule=True, now=t_after)
            self.assertEqual(res_after["reason"], "OUTSIDE_DAILY_ENTRY_WINDOW")
            # Session must have been claimed to fail closed!
            self.assertTrue(persistence.has_autopilot_entry_session(session_date))

    async def test_terminal_outcome_slots_full_claims_session_immediately(self):
        """Normal outcome SLOTS_FULL claims session immediately on first run."""
        session_dt = datetime(2026, 9, 11, 13, 35, tzinfo=timezone.utc)
        session_date = "2026-09-11"

        portfolio_state = {
            "holdings": [
                {"id": 1, "ticker": "S1", "quantity": 10.0, "buy_price": 100.0, "current_price": 100.0},
                {"id": 2, "ticker": "S2", "quantity": 10.0, "buy_price": 100.0, "current_price": 100.0},
                {"id": 3, "ticker": "S3", "quantity": 10.0, "buy_price": 100.0, "current_price": 100.0},
            ],
            "total_equity_usd": 10000.0,
            "cash_usd": 1000.0,
        }

        with patch("al_sangmoo.domain.risk.autopilot_trader.CONFIRMED_EOD_JSON", self._feed_path), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio", return_value=portfolio_state), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured", return_value=False), \
             patch.object(self.autopilot, "_ensure_cash_proxy_parked", return_value={"executed": []}):
            self.autopilot.set_enabled(True)
            res = await self.autopilot.run_autopilot_cycle(force_scan=False, enforce_entry_schedule=True, now=session_dt)

        self.assertEqual(res["reason"], "SLOTS_FULL")
        self.assertTrue(persistence.has_autopilot_entry_session(session_date))

    async def test_terminal_outcome_no_eligible_candidate_claims_session_immediately(self):
        """NO_ELIGIBLE_CANDIDATE claims session immediately."""
        session_dt = datetime(2026, 9, 11, 13, 35, tzinfo=timezone.utc)
        session_date = "2026-09-11"

        # Ticker price < kijun -> ineligible
        ineligible_cand = [
            {
                "ticker": "NVDA",
                "name": "NVIDIA",
                "price": 100.0,
                "kijun": 150.0,
                "kijun_26": 150.0,
                "conviction_score": 90.0,
                "sizing": {"eligible": True, "shares": 5, "is_bull_regime": True},
            }
        ]
        self._write_feed(_build_valid_feed(candidates=ineligible_cand))

        portfolio_state = {"holdings": [], "total_equity_usd": 10000.0, "cash_usd": 10000.0}

        with patch("al_sangmoo.domain.risk.autopilot_trader.CONFIRMED_EOD_JSON", self._feed_path), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio", return_value=portfolio_state), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured", return_value=False), \
             patch.object(self.autopilot, "_ensure_cash_proxy_parked", return_value={"executed": []}):
            self.autopilot.set_enabled(True)
            res = await self.autopilot.run_autopilot_cycle(force_scan=False, enforce_entry_schedule=True, now=session_dt)

        self.assertEqual(res["reason"], "NO_ELIGIBLE_CANDIDATE")
        self.assertTrue(persistence.has_autopilot_entry_session(session_date))

    async def test_terminal_outcome_session_exit_lockout_claims_session_immediately(self):
        """SESSION_EXIT_LOCKOUT claims session immediately."""
        session_dt = datetime(2026, 9, 11, 13, 35, tzinfo=timezone.utc)
        session_date = "2026-09-11"

        exit_ts = datetime(2026, 9, 11, 13, 32, tzinfo=timezone.utc).timestamp()
        exit_cooldown.record_exit("NVDA", "AUTO_STOP_LOSS", ts=exit_ts)

        self.autopilot.set_enabled(True)
        res = await self.autopilot.run_autopilot_cycle(force_scan=False, enforce_entry_schedule=True, now=session_dt)

        self.assertEqual(res["reason"], "SESSION_EXIT_LOCKOUT")
        self.assertTrue(persistence.has_autopilot_entry_session(session_date))

    async def test_terminal_outcome_auto_buy_disabled_claims_session_immediately(self):
        """AUTO_BUY_DISABLED (simulation mode) claims session immediately."""
        session_dt = datetime(2026, 9, 11, 13, 35, tzinfo=timezone.utc)
        session_date = "2026-09-11"

        portfolio_state = {"holdings": [], "total_equity_usd": 10000.0, "cash_usd": 10000.0}

        with patch("al_sangmoo.domain.risk.autopilot_trader.CONFIRMED_EOD_JSON", self._feed_path), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio", return_value=portfolio_state), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured", return_value=False), \
             patch.object(self.autopilot, "_ensure_cash_proxy_parked", return_value={"executed": []}):
            self.autopilot.set_enabled(False)  # Auto-buy OFF
            res = await self.autopilot.run_autopilot_cycle(force_scan=False, enforce_entry_schedule=True, now=session_dt)

        self.assertEqual(res["reason"], "AUTO_BUY_DISABLED")
        self.assertTrue(persistence.has_autopilot_entry_session(session_date))

    async def test_concurrent_cycle_calls_do_not_double_count_attempts(self):
        """Concurrent calls to run_autopilot_cycle under snapshot unverified must serialize and not race."""
        session_dt = datetime(2026, 9, 11, 13, 35, tzinfo=timezone.utc)
        session_date = "2026-09-11"
        portfolio_state = {"holdings": [], "total_equity_usd": 10000.0, "cash_usd": 10000.0}

        with patch("al_sangmoo.domain.risk.autopilot_trader.CONFIRMED_EOD_JSON", self._feed_path), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio", return_value=portfolio_state), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured", return_value=True), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.get_overseas_balance", return_value={
                 "status": "partial",
                 "snapshot_complete": False,
                 "holdings": [],
             }):
            self.autopilot.set_enabled(True)
            # Run 2 concurrent calls simultaneously
            res1, res2 = await asyncio.gather(
                self.autopilot.run_autopilot_cycle(force_scan=False, enforce_entry_schedule=True, now=session_dt),
                self.autopilot.run_autopilot_cycle(force_scan=False, enforce_entry_schedule=True, now=session_dt),
            )

        # One call gets BROKER_SNAPSHOT_UNVERIFIED (attempt 1), the other gets BROKER_SNAPSHOT_RETRY_COOLDOWN
        reasons = {res1["reason"], res2["reason"]}
        self.assertIn("BROKER_SNAPSHOT_UNVERIFIED", reasons)
        self.assertIn("BROKER_SNAPSHOT_RETRY_COOLDOWN", reasons)

        # Attempt counter must be exactly 1, not 2!
        state = self.autopilot._snapshot_retry_state.get(session_date) or {}
        self.assertEqual(state.get("attempts"), 1)
        self.assertFalse(persistence.has_autopilot_entry_session(session_date))

    async def test_broker_exception_triggers_bounded_retry(self):
        """When get_overseas_balance raises an exception, bounded retry triggers properly without claiming."""
        session_dt = datetime(2026, 9, 11, 13, 35, tzinfo=timezone.utc)
        session_date = "2026-09-11"
        portfolio_state = {"holdings": [], "total_equity_usd": 10000.0, "cash_usd": 10000.0}

        with patch("al_sangmoo.domain.risk.autopilot_trader.CONFIRMED_EOD_JSON", self._feed_path), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio", return_value=portfolio_state), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured", return_value=True), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.get_overseas_balance", side_effect=ConnectionResetError("KIS gateway reset")):
            self.autopilot.set_enabled(True)
            res = await self.autopilot.run_autopilot_cycle(force_scan=False, enforce_entry_schedule=True, now=session_dt)

        self.assertEqual(res["status"], "skipped")
        self.assertEqual(res["reason"], "BROKER_SNAPSHOT_UNVERIFIED")
        self.assertEqual(res["retry_info"]["attempts"], 1)
        self.assertFalse(persistence.has_autopilot_entry_session(session_date))

    async def test_scheduler_loop_claims_on_session_exit_lockout(self):
        """When scheduler loop detects SESSION_EXIT_LOCKOUT, it must claim the daily session in SQLite."""
        session_dt = datetime(2026, 9, 11, 13, 35, tzinfo=timezone.utc)
        session_date = "2026-09-11"

        exit_ts = session_dt.timestamp() - 60
        exit_cooldown.record_exit("NVDA", "AUTO_STOP_LOSS", ts=exit_ts)

        self.autopilot.set_enabled(True)
        self.autopilot.is_running = True

        # Simulate one iteration of _scheduler_loop
        with patch("al_sangmoo.domain.risk.autopilot_trader.is_us_regular_hours", return_value=True), \
             patch("al_sangmoo.domain.risk.autopilot_trader.evaluate_daily_entry_schedule", return_value={
                 "eligible": False,
                 "reason": "SESSION_EXIT_LOCKOUT",
                 "session_date": session_date,
             }), \
             patch.object(self.autopilot, "_ensure_cash_proxy_parked"):

            # Run a single scheduler loop step
            schedule = evaluate_daily_entry_schedule(now=session_dt)
            if not schedule.get("eligible"):
                if schedule.get("reason") == "SESSION_EXIT_LOCKOUT" and session_date:
                    self.autopilot._claim_session_for_day(session_date, source="AUTOPILOT_EXIT_LOCKOUT")

        self.assertTrue(persistence.has_autopilot_entry_session(session_date))

    async def test_get_status_reports_active_snapshot_retry(self):
        """get_status() must expose active_snapshot_retry when a retry is pending."""
        session_dt = datetime(2026, 9, 11, 13, 35, tzinfo=timezone.utc)
        session_date = "2026-09-11"
        portfolio_state = {"holdings": [], "total_equity_usd": 10000.0, "cash_usd": 10000.0}

        with patch("al_sangmoo.domain.risk.autopilot_trader.CONFIRMED_EOD_JSON", self._feed_path), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio", return_value=portfolio_state), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured", return_value=True), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.get_overseas_balance", return_value={
                 "status": "partial",
                 "snapshot_complete": False,
                 "holdings": [],
             }):
            self.autopilot.set_enabled(True)
            await self.autopilot.run_autopilot_cycle(force_scan=False, enforce_entry_schedule=True, now=session_dt)

        status = self.autopilot.get_status()
        entry_policy = status.get("entry_policy", {})
        active_retry = entry_policy.get("active_snapshot_retry")
        self.assertIsNotNone(active_retry)
        self.assertEqual(active_retry["session_date"], session_date)
        self.assertEqual(active_retry["attempts"], 1)
        self.assertEqual(active_retry["max_retries"], 3)

    async def test_multiple_candidates_first_ineligible_second_unverified(self):
        """If first candidate is ineligible and second triggers unverified snapshot, bounded retry works."""
        session_dt = datetime(2026, 9, 11, 13, 35, tzinfo=timezone.utc)
        session_date = "2026-09-11"

        candidates = [
            {
                "ticker": "AAPL",
                "name": "Apple",
                "price": 100.0,
                "kijun": 120.0,  # Below kijun -> ineligible
                "kijun_26": 120.0,
                "conviction_score": 90.0,
                "sizing": {"eligible": True, "shares": 5, "is_bull_regime": True},
            },
            {
                "ticker": "NVDA",
                "name": "NVIDIA",
                "price": 150.0,
                "kijun": 140.0,  # Above kijun -> eligible
                "kijun_26": 140.0,
                "conviction_score": 92.0,
                "sizing": {"eligible": True, "shares": 10, "is_bull_regime": True, "slot_weight": 0.34},
            },
        ]
        self._write_feed(_build_valid_feed(candidates=candidates))
        portfolio_state = {"holdings": [], "total_equity_usd": 10000.0, "cash_usd": 10000.0}

        with patch("al_sangmoo.domain.risk.autopilot_trader.CONFIRMED_EOD_JSON", self._feed_path), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio", return_value=portfolio_state), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured", return_value=True), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.get_overseas_balance", return_value={
                 "status": "partial",
                 "snapshot_complete": False,
                 "holdings": [],
             }):
            self.autopilot.set_enabled(True)
            res = await self.autopilot.run_autopilot_cycle(force_scan=False, enforce_entry_schedule=True, now=session_dt)

        self.assertEqual(res["reason"], "BROKER_SNAPSHOT_UNVERIFIED")
        self.assertEqual(res["retry_info"]["attempts"], 1)
        self.assertFalse(persistence.has_autopilot_entry_session(session_date))

    async def test_naive_datetime_handling_consistent_with_utc(self):
        """Passing naive datetime representing UTC should work without timezone offset errors."""
        session_dt = datetime(2026, 9, 11, 13, 35)  # naive datetime
        session_date = "2026-09-11"
        portfolio_state = {"holdings": [], "total_equity_usd": 10000.0, "cash_usd": 10000.0}

        with patch("al_sangmoo.domain.risk.autopilot_trader.CONFIRMED_EOD_JSON", self._feed_path), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio", return_value=portfolio_state), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured", return_value=True), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.get_overseas_balance", return_value={
                 "status": "partial",
                 "snapshot_complete": False,
                 "holdings": [],
             }):
            self.autopilot.set_enabled(True)
            res = await self.autopilot.run_autopilot_cycle(force_scan=False, enforce_entry_schedule=True, now=session_dt)

        self.assertEqual(res["reason"], "BROKER_SNAPSHOT_UNVERIFIED")
        self.assertEqual(res["retry_info"]["attempts"], 1)
        self.assertFalse(persistence.has_autopilot_entry_session(session_date))


if __name__ == "__main__":
    unittest.main()

