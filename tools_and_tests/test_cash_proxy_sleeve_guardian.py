"""
AL-SANGMOO QUANT PLATFORM: C1-M2 CRITICAL PATCH TEST SUITE

Regression coverage for the 2026-09-11 trading-engine disaster:
  1. Autopilot Entry Gate       - never buy below the 26D kijun, low conviction,
                                   or a name inside the re-entry cooldown.
  2. Cash Proxy anti-whipsaw    - deadband, 300s sell cooldown, execution order,
                                   single-authority ownership.
  3. Guardian proxy exemption   - QLD/QQQ are marked-to-market but never
                                   force-exited; forced exits arm the cooldown.
"""

import os
import sys
import json
import asyncio
import tempfile
import hashlib
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest.mock import AsyncMock, patch

from al_sangmoo.domain.risk import cash_proxy, exit_cooldown
from al_sangmoo.domain.risk import autopilot_trader
from al_sangmoo.domain.risk.autopilot_trader import (
    AutoPilotTrader,
    evaluate_daily_entry_schedule,
    evaluate_entry_gate,
    validate_confirmed_eod_feed,
)
from al_sangmoo.domain.risk.portfolio_guardian import PortfolioGuardian
from al_sangmoo.infrastructure.brokers.kis_broker import KISBrokerAdapter
from al_sangmoo.infrastructure.brokers.paper_broker import PaperTradingBroker
from al_sangmoo.infrastructure import persistence


class TempRiskDbMixin:
    """Keep persistent risk-state tests away from the operator's live database."""

    def setUp(self):
        super().setUp()
        self._risk_prev_db = os.environ.get("AL_SANGMOO_DB_PATH")
        self._risk_db_dir = tempfile.TemporaryDirectory(suffix="_risk_db")
        os.environ["AL_SANGMOO_DB_PATH"] = os.path.join(
            self._risk_db_dir.name, "risk.db"
        )
        persistence.init_database()

    def tearDown(self):
        if self._risk_prev_db is None:
            os.environ.pop("AL_SANGMOO_DB_PATH", None)
        else:
            os.environ["AL_SANGMOO_DB_PATH"] = self._risk_prev_db
        self._risk_db_dir.cleanup()
        super().tearDown()


# --------------------------------------------------------------------------- #
# 작업 1: Autopilot Entry Gate
# --------------------------------------------------------------------------- #
class TestEntryGate(TempRiskDbMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        exit_cooldown.clear()

    def test_below_kijun_is_skipped(self):
        """The DIS disaster: price 104.95 < kijun 105.78 must be BLOCKED."""
        gate = evaluate_entry_gate(
            {"ticker": "DIS", "price": 104.95, "conviction_score": 85.0,
             "sizing": {"eligible": True}},
            kijun_26=105.78,
        )
        self.assertFalse(gate["eligible"])
        self.assertEqual(gate["reason"], "BELOW_KIJUN")

    def test_above_kijun_passes(self):
        gate = evaluate_entry_gate(
            {"ticker": "NVDA", "price": 120.0, "conviction_score": 82.0,
             "sizing": {"eligible": True}},
            kijun_26=110.0,
        )
        self.assertTrue(gate["eligible"])
        self.assertEqual(gate["reason"], "PASS")

    def test_low_conviction_is_skipped(self):
        gate = evaluate_entry_gate(
            {"ticker": "XYZ", "price": 50.0, "conviction_score": 40.0,
             "bull_score": 45.0, "sizing": {"eligible": False}},
            kijun_26=45.0,
        )
        self.assertFalse(gate["eligible"])
        self.assertEqual(gate["reason"], "LOW_CONVICTION")

    def test_eligible_sizing_flag_satisfies_conviction(self):
        gate = evaluate_entry_gate(
            {"ticker": "ABC", "price": 50.0, "conviction_score": 10.0,
             "bull_score": 10.0, "sizing": {"eligible": True}},
            kijun_26=45.0,
        )
        self.assertTrue(gate["eligible"])

    def test_reentry_cooldown_is_skipped(self):
        gate = evaluate_entry_gate(
            {"ticker": "DELL", "price": 520.0, "conviction_score": 90.0,
             "sizing": {"eligible": True}},
            kijun_26=500.0,
            cooldown_tickers=["DELL"],
        )
        self.assertFalse(gate["eligible"])
        self.assertEqual(gate["reason"], "RE_ENTRY_COOLDOWN")

    def test_unknown_kijun_does_not_hard_block(self):
        """A transient market-data failure (kijun<=0) must not block every entry."""
        gate = evaluate_entry_gate(
            {"ticker": "NVDA", "price": 120.0, "conviction_score": 82.0,
             "sizing": {"eligible": True}},
            kijun_26=0.0,
        )
        self.assertTrue(gate["eligible"])


class TestNextUnheldCandidate(TempRiskDbMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        exit_cooldown.clear()
        self.pilot = AutoPilotTrader()

    def test_skips_held_and_below_kijun_returns_first_eligible(self):
        feed = {
            "ranked_conviction_list": [
                {"ticker": "AAPL", "price": 200.0, "kijun": 190.0,
                 "conviction_score": 88.0, "sizing": {"eligible": True, "shares": 3}},
                {"ticker": "DIS", "price": 104.95, "kijun": 105.78,
                 "conviction_score": 80.0, "sizing": {"eligible": True, "shares": 5}},
                {"ticker": "MSFT", "price": 400.0, "kijun": 390.0,
                 "conviction_score": 91.0, "sizing": {"eligible": True, "shares": 2}},
            ]
        }
        # AAPL already held -> should skip to next eligible (DIS is below kijun) -> MSFT.
        cand, gate = self.pilot._next_unheld_candidate(
            feed, active_tickers=["AAPL"], broker_tickers=[], cooldown_tickers=[]
        )
        self.assertIsNotNone(cand)
        self.assertEqual(cand["ticker"], "MSFT")
        self.assertTrue(gate["eligible"])

    def test_returns_none_when_all_ineligible(self):
        feed = {
            "ranked_conviction_list": [
                {"ticker": "DIS", "price": 104.95, "kijun": 105.78,
                 "conviction_score": 80.0, "sizing": {"eligible": True, "shares": 5}},
            ]
        }
        cand, gate = self.pilot._next_unheld_candidate(
            feed, active_tickers=[], broker_tickers=[], cooldown_tickers=[]
        )
        self.assertIsNone(cand)
        self.assertEqual(gate["reason"], "NO_ELIGIBLE_CANDIDATE")


# --------------------------------------------------------------------------- #
# 재진입 쿨다운 레지스트리
# --------------------------------------------------------------------------- #
class TestExitCooldown(TempRiskDbMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        exit_cooldown.clear()

    def test_record_and_active_within_window(self):
        exit_cooldown.record_exit("DIS", "AUTO_KIJUN_EXIT", ts=1000.0)
        self.assertTrue(exit_cooldown.is_in_cooldown("DIS", now=1000.0 + 60.0))
        self.assertIn("DIS", exit_cooldown.cooldown_tickers(now=1000.0 + 60.0))

    def test_expires_after_window(self):
        exit_cooldown.record_exit("DIS", "AUTO_STOP_LOSS", ts=1000.0)
        later = 1000.0 + exit_cooldown.RE_ENTRY_COOLDOWN_SEC + 1.0
        self.assertFalse(exit_cooldown.is_in_cooldown("DIS", now=later))
        self.assertNotIn("DIS", exit_cooldown.cooldown_tickers(now=later))

    def test_default_lock_is_24_hours_and_persisted_in_sqlite(self):
        self.assertEqual(exit_cooldown.RE_ENTRY_COOLDOWN_SEC, 24 * 60 * 60)
        exit_cooldown.record_exit("DELL", "AUTO_STOP_LOSS", ts=1000.0)
        with persistence.get_connection() as conn:
            row = conn.cursor().execute(
                "SELECT exited_at, lock_until, reason FROM exit_cooldowns WHERE ticker = ?",
                ("DELL",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(float(row["lock_until"]) - float(row["exited_at"]), 86400.0)
        self.assertEqual(row["reason"], "AUTO_STOP_LOSS")
        # A fresh DB read (with no process-memory registry) still blocks re-entry.
        self.assertTrue(exit_cooldown.is_in_cooldown("DELL", now=1001.0))


class TestDailyEntrySchedule(TempRiskDbMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        exit_cooldown.clear()

    def test_previous_eod_entry_window_is_open_once_at_0935_et(self):
        now = datetime(2026, 9, 11, 13, 35, tzinfo=timezone.utc)  # 09:35 EDT
        gate = evaluate_daily_entry_schedule(now)
        self.assertTrue(gate["eligible"])
        self.assertEqual(gate["session_date"], "2026-09-11")

        self.assertTrue(
            persistence.claim_autopilot_entry_session(
                gate["session_date"], claimed_at="2026-09-11 09:35:00"
            )
        )
        self.assertFalse(
            persistence.claim_autopilot_entry_session(
                gate["session_date"], claimed_at="2026-09-11 09:36:00"
            )
        )
        second_gate = evaluate_daily_entry_schedule(now)
        self.assertFalse(second_gate["eligible"])
        self.assertEqual(second_gate["reason"], "DAILY_ENTRY_ALREADY_CLAIMED")

    def test_intraday_exit_blocks_all_same_session_slot_refills(self):
        exit_ts = datetime(
            2026, 9, 11, 13, 32, tzinfo=timezone.utc
        ).timestamp()
        exit_cooldown.record_exit("DELL", "AUTO_STOP_LOSS", ts=exit_ts)
        now = datetime(2026, 9, 11, 13, 35, tzinfo=timezone.utc)
        gate = evaluate_daily_entry_schedule(now)
        self.assertFalse(gate["eligible"])
        self.assertEqual(gate["reason"], "SESSION_EXIT_LOCKOUT")

    def test_entries_are_blocked_after_opening_window(self):
        now = datetime(2026, 9, 11, 14, 30, tzinfo=timezone.utc)  # 10:30 EDT
        gate = evaluate_daily_entry_schedule(now)
        self.assertFalse(gate["eligible"])
        self.assertEqual(gate["reason"], "OUTSIDE_DAILY_ENTRY_WINDOW")

    def test_only_previous_session_eod_feed_is_eligible(self):
        source_rows = [{
            "ticker": "DELL",
            "bar_date": "2026-09-10",
            "close": 100.0,
        }]
        source_hash = hashlib.sha256(
            json.dumps(
                source_rows,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        intraday_rows = [{
            "ticker": "DELL",
            "bar_date": "2026-09-11",
            "close": 100.0,
        }]
        intraday_hash = hashlib.sha256(
            json.dumps(
                intraday_rows,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        previous = validate_confirmed_eod_feed(
            {
                "signal_session_date": "2026-09-10",
                "signal_is_eod_confirmed": True,
                "signal_source_hash": source_hash,
                "signal_source_rows": source_rows,
            },
            "2026-09-11",
        )
        intraday = validate_confirmed_eod_feed(
            {
                "signal_session_date": "2026-09-11",
                "signal_is_eod_confirmed": True,
                "signal_source_hash": intraday_hash,
                "signal_source_rows": intraday_rows,
            },
            "2026-09-11",
        )
        unconfirmed = validate_confirmed_eod_feed(
            {
                "signal_session_date": "2026-09-10",
                "signal_is_eod_confirmed": False,
            },
            "2026-09-11",
        )
        unverified = validate_confirmed_eod_feed({}, "2026-09-11")
        self.assertTrue(previous["eligible"])
        self.assertEqual(intraday["reason"], "INTRADAY_DAILY_BAR_NOT_CONFIRMED")
        self.assertEqual(unconfirmed["reason"], "EOD_FEED_UNCONFIRMED")
        self.assertEqual(unverified["reason"], "EOD_FEED_UNVERIFIED")


class TestBacktestEventChronology(unittest.TestCase):
    def test_entries_precede_same_day_exit_evaluation(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("c1_v1_tuning.py", "c1_v1_evolution.py"):
            source = (root / "research_and_backtests" / name).read_text(
                encoding="utf-8"
            )
            entry_event = source.index(
                "if len(book.positions) < max_slots and i >= 1:"
            )
            exit_event = source.index(
                "# Intraday hard stops / EOD trailing exits"
            )
            self.assertLess(
                entry_event,
                exit_event,
                f"{name} leaks same-day exit information into the open entry",
            )


class TestSignalProvenance(unittest.TestCase):
    def test_provenance_comes_from_ranked_source_bars(self):
        from generate_dashboard_feed import derive_signal_provenance

        result = derive_signal_provenance(
            chart_data={
                "DELL": {"candles": [{"time": "2026-09-10", "close": 100.0}]},
                "NVDA": {"candles": [{"time": "2026-09-10", "close": 200.0}]},
            },
            ranked_candidates=[{"ticker": "DELL"}, {"ticker": "NVDA"}],
            signal_clock_et=datetime(
                2026, 9, 11, 13, 0, tzinfo=timezone.utc
            ),
        )
        self.assertTrue(result["is_confirmed"])
        self.assertEqual(result["session_date"], "2026-09-10")
        self.assertEqual(len(result["source_hash"]), 64)

    def test_intraday_or_mixed_source_bars_are_not_confirmed(self):
        from generate_dashboard_feed import derive_signal_provenance

        intraday = derive_signal_provenance(
            chart_data={
                "DELL": {"candles": [{"time": "2026-09-11", "close": 100.0}]},
            },
            ranked_candidates=[{"ticker": "DELL"}],
            signal_clock_et=datetime(
                2026, 9, 11, 14, 0, tzinfo=timezone.utc
            ),
        )
        mixed = derive_signal_provenance(
            chart_data={
                "DELL": {"candles": [{"time": "2026-09-10", "close": 100.0}]},
                "NVDA": {"candles": [{"time": "2026-09-09", "close": 200.0}]},
            },
            ranked_candidates=[{"ticker": "DELL"}, {"ticker": "NVDA"}],
            signal_clock_et=datetime(
                2026, 9, 11, 13, 0, tzinfo=timezone.utc
            ),
        )
        self.assertFalse(intraday["is_confirmed"])
        self.assertFalse(mixed["is_confirmed"])


# --------------------------------------------------------------------------- #
# 작업 2: Cash Proxy anti-whipsaw SSOT
# --------------------------------------------------------------------------- #
class TestProxyRebalanceDeadband(unittest.TestCase):
    def test_small_share_drift_is_ignored(self):
        # 2 shares drift (< 3) -> no order.
        self.assertIsNone(
            cash_proxy.proxy_rebalance_action(current_qty=10, target_qty=12, price=90.0)
        )

    def test_small_notional_is_ignored(self):
        # 4 shares * $50 = $200 (< $300) -> no order.
        self.assertIsNone(
            cash_proxy.proxy_rebalance_action(current_qty=10, target_qty=14, price=50.0)
        )

    def test_material_move_fires_order(self):
        action = cash_proxy.proxy_rebalance_action(
            current_qty=0, target_qty=12, price=90.0
        )
        self.assertIsNotNone(action)
        self.assertEqual(action["side"], "BUY")
        self.assertEqual(action["qty"], 12)

    def test_sell_side_detected(self):
        action = cash_proxy.proxy_rebalance_action(
            current_qty=20, target_qty=5, price=90.0
        )
        self.assertIsNotNone(action)
        self.assertEqual(action["side"], "SELL")
        self.assertEqual(action["qty"], 15)


class TestProxySellCooldown(unittest.TestCase):
    def setUp(self):
        self.sleeve = cash_proxy.CashProxySleeve()

    def test_freshly_bought_proxy_cannot_be_dumped_in_20s(self):
        """The exact whipsaw: bought QLD, then tried to sell 20s later."""
        self.sleeve.record_buy("QLD", ts=1000.0)
        decision = self.sleeve.free_proxy_cash_for_entry(
            "QLD", needed_usd=1500.0, current_qty=19, price=90.0,
            satellite_search_done=True, now=1020.0,  # +20s
        )
        self.assertEqual(decision["action"], "HOLD")
        self.assertEqual(decision["reason"], "PROXY_SELL_COOLDOWN")

    def test_proxy_can_be_sold_after_cooldown(self):
        self.sleeve.record_buy("QLD", ts=1000.0)
        decision = self.sleeve.free_proxy_cash_for_entry(
            "QLD", needed_usd=1500.0, current_qty=19, price=90.0,
            satellite_search_done=True, now=1000.0 + 301.0,  # +5m1s
        )
        self.assertEqual(decision["action"], "SELL")
        self.assertGreater(decision["qty"], 0)

    def test_satellite_search_must_finish_first(self):
        self.sleeve.record_buy("QLD", ts=1000.0)
        decision = self.sleeve.free_proxy_cash_for_entry(
            "QLD", needed_usd=1500.0, current_qty=19, price=90.0,
            satellite_search_done=False, now=1000.0 + 400.0,
        )
        self.assertEqual(decision["action"], "HOLD")
        self.assertEqual(decision["reason"], "SATELLITE_SEARCH_PENDING")


class TestProxyExecutionOrderAndAuthority(unittest.TestCase):
    def setUp(self):
        self.sleeve = cash_proxy.CashProxySleeve()

    def test_should_park_cash_requires_search_done(self):
        self.assertFalse(cash_proxy.should_park_cash(False, 5000.0, 90.0))
        self.assertTrue(cash_proxy.should_park_cash(True, 5000.0, 90.0))

    def test_should_park_cash_ignores_tiny_cash(self):
        self.assertFalse(cash_proxy.should_park_cash(True, 100.0, 90.0))

    def test_single_authority_blocks_second_owner(self):
        self.assertTrue(self.sleeve.acquire_authority("AUTOPILOT"))
        # Guardian cannot grab it concurrently.
        self.assertFalse(self.sleeve.acquire_authority("GUARDIAN"))
        self.sleeve.release_authority("AUTOPILOT")
        self.assertTrue(self.sleeve.acquire_authority("GUARDIAN"))


class TestPersistentProxyOrderAuthority(TempRiskDbMixin, unittest.TestCase):
    def test_pending_intent_survives_new_owner_and_blocks_duplicate(self):
        self.assertTrue(
            persistence.claim_proxy_order_intent(
                "QQQ", "BUY", 3.0, 500.0, "AUTOPILOT"
            )
        )
        persistence.mark_proxy_order_submitted("QQQ", "ORDER-1")

        # A different daemon/process sees the same SQLite reservation.
        self.assertFalse(
            persistence.claim_proxy_order_intent(
                "QQQ", "BUY", 3.0, 500.0, "GUARDIAN"
            )
        )
        with persistence.get_connection() as conn:
            row = conn.cursor().execute(
                "SELECT status, order_id FROM proxy_order_intents WHERE ticker = 'QQQ'"
            ).fetchone()
        self.assertEqual(row["status"], "SUBMITTED")
        self.assertEqual(row["order_id"], "ORDER-1")

        persistence.clear_proxy_order_intent("QQQ")
        self.assertTrue(
            persistence.claim_proxy_order_intent(
                "QQQ", "BUY", 3.0, 500.0, "GUARDIAN"
            )
        )


class TestAutomaticExecutionAudit(TempRiskDbMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.pilot = AutoPilotTrader()

    def test_autopilot_simulated_satellite_buy_is_logged(self):
        pick = {
            "ticker": "DELL",
            "name": "Dell",
            "price": 100.0,
            "sizing": {"shares": 5, "slot_weight": 0.5},
        }
        portfolio = {
            "holdings": [],
            "free_cash_usd": 1000.0,
            "total_equity_usd": 7500.0,
        }
        with patch(
            "al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured",
            return_value=False,
        ), patch(
            "al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio",
            return_value=portfolio,
        ), patch(
            "al_sangmoo.domain.risk.autopilot_trader.sync_portfolio_prices",
            return_value=portfolio,
        ), patch(
            "al_sangmoo.domain.risk.autopilot_trader.hub.broadcast",
            new=AsyncMock(),
        ), patch(
            "al_sangmoo.domain.risk.autopilot_trader.hub.broadcast_delta",
            new=AsyncMock(),
        ):
            result = asyncio.run(
                self.pilot._execute_satellite_entry(
                    pick=pick,
                    slot_rank=1,
                    holdings=[],
                    now_str="2026-09-11 09:35:00",
                )
            )

        self.assertEqual(result["status"], "success")
        logs = persistence.get_execution_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["ticker"], "DELL")
        self.assertEqual(logs[0]["side"], "BUY")
        self.assertEqual(logs[0]["status"], "SIMULATED")
        self.assertEqual(logs[0]["order_type"], "AUTOPILOT_SATELLITE_ENTRY")

    def test_autopilot_simulated_cash_proxy_rebalance_is_logged(self):
        self.pilot.is_enabled = True
        plan = {
            "actions": [
                {"ticker": "QQQ", "side": "BUY", "qty": 3, "reason": "PARK_IDLE_NAV"}
            ]
        }
        with patch(
            "al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio",
            return_value={
                "holdings": [],
                "free_cash_usd": 1000.0,
                "total_equity_usd": 7500.0,
            },
        ), patch.object(
            self.pilot, "_leverage_mode_now", return_value=False
        ), patch.object(
            self.pilot, "_etf_price", side_effect=lambda ticker: 100.0
        ), patch(
            "al_sangmoo.domain.risk.autopilot_trader.build_cash_proxy_plan",
            return_value=plan,
        ), patch(
            "al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured",
            return_value=False,
        ):
            result = self.pilot._ensure_cash_proxy_parked()

        self.assertEqual(len(result["executed"]), 1)
        logs = persistence.get_execution_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["ticker"], "QQQ")
        self.assertEqual(logs[0]["order_type"], "AUTOPILOT_CASH_PROXY_REBALANCE")

    def test_paper_broker_buy_and_sell_fills_are_logged(self):
        broker = PaperTradingBroker()
        buy = broker.submit_buy_order("DELL", price=100.0, quantity=2.0)
        sell = broker.submit_sell_order(
            buy["position_id"],
            price=105.0,
            reason="TEST_EXIT",
        )
        self.assertEqual(buy["status"], "FILLED")
        self.assertEqual(sell["status"], "FILLED")
        logs = persistence.get_execution_logs()
        self.assertEqual([row["side"] for row in logs], ["SELL", "BUY"])
        self.assertTrue(all(row["status"] == "SIMULATED" for row in logs))


class TestAtomicExecutionAudit(TempRiskDbMixin, unittest.TestCase):
    def test_buy_rolls_back_when_execution_audit_insert_fails(self):
        with patch(
            "al_sangmoo.infrastructure.persistence.record_execution_log",
            side_effect=RuntimeError("audit failure"),
        ):
            with self.assertRaises(RuntimeError):
                persistence.add_portfolio_buy(
                    ticker="DELL",
                    buy_price=100.0,
                    quantity=2.0,
                    execution_log={
                        "order_type": "TEST",
                        "status": "SIMULATED",
                    },
                )
        self.assertEqual(persistence.get_live_portfolio()["holdings"], [])

    def test_sell_rolls_back_when_execution_audit_insert_fails(self):
        hid = persistence.add_portfolio_buy(
            ticker="DELL",
            buy_price=100.0,
            quantity=2.0,
        )
        with patch(
            "al_sangmoo.infrastructure.persistence.record_execution_log",
            side_effect=RuntimeError("audit failure"),
        ):
            with self.assertRaises(RuntimeError):
                persistence.record_portfolio_sell(
                    holding_id=hid,
                    sell_price=90.0,
                    execution_log={
                        "order_type": "TEST",
                        "status": "SIMULATED",
                    },
                )
        holding = persistence.get_live_portfolio()["holdings"][0]
        self.assertEqual(holding["status"], "HOLDING")
        self.assertEqual(
            persistence.get_trade_history_records(),
            [],
        )


class TestPartialProxySellLedger(TempRiskDbMixin, unittest.TestCase):
    def test_proxy_cash_raise_keeps_unsold_shares_and_realizes_partial_pnl(self):
        holding_id = persistence.add_portfolio_buy(
            ticker="QQQ",
            buy_price=100.0,
            quantity=10.0,
            buy_date="2026-09-10",
        )
        sold = persistence.record_portfolio_sell_quantity(
            holding_id=holding_id,
            sell_price=110.0,
            quantity=3.0,
            reason="TEST_PARTIAL_PROXY_SELL",
        )
        self.assertTrue(sold)

        portfolio = persistence.get_live_portfolio()
        self.assertEqual(len(portfolio["holdings"]), 1)
        self.assertEqual(portfolio["holdings"][0]["quantity"], 7.0)
        self.assertAlmostEqual(portfolio["realized_pnl_amount"], 30.0, places=2)
        history = persistence.get_trade_history_records()
        self.assertEqual(history[0]["quantity"], 3.0)
        self.assertAlmostEqual(history[0]["pnl_amount"], 30.0, places=2)

    def test_proxy_sell_quantity_spans_multiple_lots(self):
        persistence.add_portfolio_buy("QQQ", 100.0, 10.0, "2026-09-09")
        persistence.add_portfolio_buy("QQQ", 105.0, 5.0, "2026-09-10")
        sold = persistence.record_portfolio_ticker_sell_quantity(
            ticker="QQQ",
            sell_price=110.0,
            quantity=12.0,
            reason="TEST_MULTI_LOT_PROXY_SELL",
        )
        self.assertEqual(sold, 12.0)
        holdings = persistence.get_live_portfolio()["holdings"]
        self.assertEqual(len(holdings), 1)
        self.assertEqual(holdings[0]["quantity"], 3.0)


# --------------------------------------------------------------------------- #
# 작업 2/3: Guardian proxy exemption + cooldown arming
# --------------------------------------------------------------------------- #
class TestGuardianProxyExemption(TempRiskDbMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        exit_cooldown.clear()
        self.guardian = PortfolioGuardian()
        self.guardian.is_enabled = True

    def _run_guardian(self, holdings):
        price_map = {"QLD": 80.0, "DIS": 100.0}  # both deeply underwater vs buy

        def fake_atomic_sell(**kwargs):
            holding = next(
                h for h in holdings
                if int(h["id"]) == int(kwargs["holding_id"])
            )
            audit = dict(kwargs.get("execution_log") or {})
            persistence.record_execution_log(
                ticker=holding["ticker"],
                side="SELL",
                quantity=float(holding["quantity"]),
                price=float(kwargs["sell_price"]),
                **audit,
            )
            return True

        with patch("al_sangmoo.domain.risk.portfolio_guardian.get_live_portfolio",
                   return_value={"holdings": holdings}), \
             patch("al_sangmoo.domain.risk.portfolio_guardian.fetch_trailing_snapshot",
                   return_value={}), \
             patch(
                 "al_sangmoo.domain.risk.portfolio_guardian.record_portfolio_sell",
                 side_effect=fake_atomic_sell,
             ) as mock_sell, \
             patch.object(PortfolioGuardian, "_persist_mark") as mock_mark, \
             patch.object(PortfolioGuardian, "_fetch_live_price",
                          new=lambda _self, ticker, fallback: price_map.get(str(ticker).upper(), fallback)), \
             patch("al_sangmoo.domain.risk.portfolio_guardian.default_kis_broker") as mock_broker:
            mock_broker.is_configured.return_value = False
            res = self.guardian._sync_check_and_execute_guardian_rules()
        return res, mock_sell, mock_mark

    def test_empty_book_still_aligns_exit_cash_into_proxy(self):
        parked = [{
            "ticker": "QQQ",
            "action": "CASH_PROXY_BUY",
            "qty": 2,
        }]
        with patch(
            "al_sangmoo.domain.risk.portfolio_guardian.get_live_portfolio",
            return_value={"holdings": []},
        ), patch(
            "al_sangmoo.domain.risk.portfolio_guardian.default_kis_broker.is_configured",
            return_value=False,
        ), patch.object(
            self.guardian,
            "_align_cash_proxy_sleeve",
            return_value=parked,
        ) as align:
            result = self.guardian._sync_check_and_execute_guardian_rules()

        align.assert_called_once()
        self.assertEqual(result["actions"], parked)

    def test_submitted_satellite_sell_survives_guardian_restart(self):
        holding = {
            "id": 77,
            "ticker": "DIS",
            "buy_price": 190.0,
            "current_price": 100.0,
            "quantity": 2.0,
            "buy_date": "2026-09-10",
        }
        second_guardian = PortfolioGuardian()
        second_guardian.is_enabled = True
        with patch(
            "al_sangmoo.domain.risk.portfolio_guardian.get_live_portfolio",
            return_value={"holdings": [holding]},
        ), patch(
            "al_sangmoo.domain.risk.portfolio_guardian.fetch_trailing_snapshot",
            return_value={},
        ), patch.object(
            PortfolioGuardian,
            "_persist_mark",
        ), patch.object(
            PortfolioGuardian,
            "_fetch_live_price",
            return_value=100.0,
        ), patch(
            "al_sangmoo.domain.risk.portfolio_guardian.is_market_open_for_orders",
            return_value=True,
        ), patch(
            "al_sangmoo.domain.risk.portfolio_guardian.default_kis_broker.is_configured",
            return_value=True,
        ), patch(
            "al_sangmoo.domain.risk.portfolio_guardian.default_kis_broker.get_overseas_balance",
            return_value={"status": "error", "message": "skip test reconcile"},
        ), patch(
            "al_sangmoo.domain.risk.portfolio_guardian.default_kis_broker.place_order",
            return_value={
                "status": "submitted",
                "order_id": "SELL-OPEN-1",
                "client_order_id": "SELL-CLIENT-1",
            },
        ) as place_order:
            self.guardian._sync_check_and_execute_guardian_rules()
            second_guardian._sync_check_and_execute_guardian_rules()

        self.assertEqual(place_order.call_count, 1)
        intents = persistence.get_satellite_order_intents()
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0]["order_id"], "SELL-OPEN-1")

    def test_cash_proxy_marked_but_never_exited(self):
        holdings = [{"id": 1, "ticker": "QLD", "buy_price": 100.0, "quantity": 19.0,
                     "current_price": 100.0}]
        res, mock_sell, mock_mark = self._run_guardian(holdings)
        # Marked to market...
        self.assertTrue(mock_mark.called)
        # ...but NEVER force-sold.
        self.assertFalse(mock_sell.called)
        self.assertEqual(res["actions"], [])

    def test_normal_stock_is_exited_and_arms_cooldown(self):
        holdings = [{"id": 2, "ticker": "DIS", "buy_price": 200.0, "quantity": 5.0,
                     "current_price": 200.0}]
        res, mock_sell, _ = self._run_guardian(holdings)
        self.assertTrue(mock_sell.called)
        self.assertEqual(len(res["actions"]), 1)
        self.assertEqual(res["actions"][0]["ticker"], "DIS")
        # Cooldown armed so Autopilot cannot instantly re-buy DIS.
        self.assertTrue(exit_cooldown.is_in_cooldown("DIS"))
        logs = [
            row for row in persistence.get_execution_logs()
            if row["ticker"] == "DIS"
            and row["order_type"] == "GUARDIAN_FORCED_EXIT"
        ]
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["side"], "SELL")
        self.assertEqual(logs[0]["status"], "SIMULATED")


# --------------------------------------------------------------------------- #
# 작업 1: end-to-end run_autopilot_cycle Entry Gate integration
# --------------------------------------------------------------------------- #
class TestAutopilotCycleEntryGate(TempRiskDbMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        exit_cooldown.clear()
        self.pilot = AutoPilotTrader()
        self.pilot.is_enabled = False  # stop right after selection (no real order)
        self._tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix="_feed.json", delete=False, encoding="utf-8"
        )
        self._tmp.close()

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass
        super().tearDown()

    def _write_feed(self, feed):
        with open(self._tmp.name, "w", encoding="utf-8") as f:
            json.dump(feed, f)

    def _run(self, feed):
        self._write_feed(feed)
        with patch.object(autopilot_trader, "DASHBOARD_JSON", self._tmp.name), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio",
                   return_value={"holdings": []}), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker") as mock_broker:
            mock_broker.is_configured.return_value = False
            return asyncio.run(self.pilot.run_autopilot_cycle(
                force_scan=False,
                enforce_entry_schedule=False,
            ))

    def test_below_kijun_top_pick_is_skipped_for_eligible_runner(self):
        """DIS is #1 but below kijun -> Autopilot must select the eligible NVDA."""
        feed = {
            "top_conviction_pick": {
                "ticker": "DIS", "name": "Disney", "price": 104.95, "kijun": 105.78,
                "conviction_score": 88.0, "sizing": {"eligible": True, "shares": 5,
                                                      "is_bull_regime": True},
            },
            "ranked_conviction_list": [
                {"ticker": "DIS", "name": "Disney", "price": 104.95, "kijun": 105.78,
                 "conviction_score": 88.0, "sizing": {"eligible": True, "shares": 5,
                                                      "is_bull_regime": True}},
                {"ticker": "NVDA", "name": "Nvidia", "price": 180.0, "kijun": 170.0,
                 "conviction_score": 91.0, "sizing": {"eligible": True, "shares": 2,
                                                      "is_bull_regime": True}},
            ],
        }
        res = self._run(feed)
        # Auto-buy disabled, but the selected candidate must be the eligible NVDA.
        self.assertEqual(res["reason"], "AUTO_BUY_DISABLED")
        self.assertEqual(res["ticker"], "NVDA")

    def test_all_below_kijun_yields_no_eligible_candidate(self):
        feed = {
            "top_conviction_pick": {
                "ticker": "DIS", "name": "Disney", "price": 104.95, "kijun": 105.78,
                "conviction_score": 88.0, "sizing": {"eligible": True, "shares": 5,
                                                      "is_bull_regime": True},
            },
            "ranked_conviction_list": [
                {"ticker": "DIS", "name": "Disney", "price": 104.95, "kijun": 105.78,
                 "conviction_score": 88.0, "sizing": {"eligible": True, "shares": 5,
                                                      "is_bull_regime": True}},
            ],
        }
        res = self._run(feed)
        self.assertEqual(res["reason"], "NO_ELIGIBLE_CANDIDATE")


# --------------------------------------------------------------------------- #
# 작업 3: reconciliation refreshes local current_price to the broker's real price
# --------------------------------------------------------------------------- #
class TestReconciliationCurrentPriceSync(unittest.TestCase):
    def setUp(self):
        self._prev_db = os.environ.get("AL_SANGMOO_DB_PATH")
        self._db_dir = tempfile.mkdtemp(suffix="_recon")
        os.environ["AL_SANGMOO_DB_PATH"] = os.path.join(self._db_dir, "recon.db")
        import db_manager
        db_manager.init_database()

    def tearDown(self):
        if self._prev_db is None:
            os.environ.pop("AL_SANGMOO_DB_PATH", None)
        else:
            os.environ["AL_SANGMOO_DB_PATH"] = self._prev_db

    def test_matched_holding_current_price_updated_to_broker_real_price(self):
        from al_sangmoo.infrastructure import persistence
        from al_sangmoo.domain import reconciliation

        # Local holding entered at $500; current price frozen at the stale entry.
        hid = persistence.add_portfolio_buy(
            ticker="DELL", buy_price=500.0, quantity=4.0, buy_date="2026-09-10"
        )

        # Broker reports the SAME qty & avg (matched), but a live price of $524.
        broker_balance = {
            "status": "success",
            "mode": "LIVE_API",
            "total_equity_usd": 2096.0,
            "cash_available_usd": 0.0,
            "holdings": [
                {"ticker": "DELL", "quantity": 4.0, "avg_price": 500.0,
                 "current_price": 524.0},
            ],
        }

        with patch.object(reconciliation.default_kis_broker, "get_overseas_balance",
                          return_value=broker_balance):
            res = reconciliation.check_sync(auto_calibrate=True)

        self.assertEqual(res["status"], "success")

        with persistence.get_connection() as conn:
            row = conn.cursor().execute(
                "SELECT current_price, current_value, pnl_amount FROM my_portfolio WHERE id = ?",
                (hid,),
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertAlmostEqual(float(row[0]), 524.0, places=2)          # current_price
        self.assertAlmostEqual(float(row[1]), 524.0 * 4.0, places=2)    # current_value
        self.assertAlmostEqual(float(row[2]), (524.0 - 500.0) * 4.0, places=2)  # pnl_amount

    def test_broker_only_import_writes_reconciled_buy_log(self):
        from al_sangmoo.domain import reconciliation

        broker_balance = {
            "status": "success",
            "mode": "LIVE_API",
            "holdings": [
                {
                    "ticker": "DELL",
                    "quantity": 4.0,
                    "avg_price": 500.0,
                    "current_price": 510.0,
                }
            ],
        }
        with patch.object(
            reconciliation.default_kis_broker,
            "get_overseas_balance",
            return_value=broker_balance,
        ):
            res = reconciliation.check_sync(auto_calibrate=True)

        self.assertEqual(res["calibrations_count"], 1)
        logs = persistence.get_execution_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["side"], "BUY")
        self.assertEqual(logs[0]["status"], "RECONCILED")
        self.assertEqual(logs[0]["order_type"], "BROKER_RECONCILIATION")

    def test_reconciliation_aggregates_multiple_local_proxy_lots(self):
        from al_sangmoo.domain import reconciliation

        persistence.add_portfolio_buy("QQQ", 100.0, 10.0, "2026-09-09")
        persistence.add_portfolio_buy("QQQ", 110.0, 5.0, "2026-09-10")
        broker_balance = {
            "status": "success",
            "mode": "LIVE_API",
            "cash_available_usd": 1000.0,
            "holdings": [{
                "ticker": "QQQ",
                "quantity": 15.0,
                "avg_price": (100.0 * 10.0 + 110.0 * 5.0) / 15.0,
                "current_price": 120.0,
            }],
        }
        with patch.object(
            reconciliation.default_kis_broker,
            "get_overseas_balance",
            return_value=broker_balance,
        ):
            res = reconciliation.check_sync(auto_calibrate=True)

        self.assertEqual(res["status"], "success")
        holdings = persistence.get_live_portfolio()["holdings"]
        self.assertEqual(len(holdings), 1)
        self.assertEqual(holdings[0]["ticker"], "QQQ")
        self.assertEqual(holdings[0]["quantity"], 15.0)
        # Lot consolidation and quote refresh are not executions.
        self.assertEqual(persistence.get_execution_logs(), [])

    def test_reconciliation_preserves_matching_satellite_lots(self):
        from al_sangmoo.domain import reconciliation

        first = persistence.add_portfolio_buy(
            "DELL", 100.0, 2.0, "2026-09-09"
        )
        second = persistence.add_portfolio_buy(
            "DELL", 110.0, 3.0, "2026-09-10"
        )
        broker_balance = {
            "status": "success",
            "mode": "LIVE_API",
            "snapshot_complete": True,
            "cash_available_usd": 0.0,
            "holdings": [{
                "ticker": "DELL",
                "quantity": 5.0,
                "avg_price": 106.0,
                "current_price": 120.0,
            }],
        }
        with patch.object(
            reconciliation.default_kis_broker,
            "get_overseas_balance",
            return_value=broker_balance,
        ):
            reconciliation.check_sync(auto_calibrate=True)

        holdings = persistence.get_live_portfolio()["holdings"]
        self.assertEqual({int(h["id"]) for h in holdings}, {first, second})
        self.assertEqual(sum(float(h["quantity"]) for h in holdings), 5.0)
        self.assertTrue(all(float(h["current_price"]) == 120.0 for h in holdings))

    def test_satellite_multilot_partial_sell_reduces_fifo_without_duplication(self):
        from al_sangmoo.domain import reconciliation

        persistence.add_portfolio_buy("DELL", 100.0, 2.0, "2026-09-09")
        persistence.add_portfolio_buy("DELL", 100.0, 3.0, "2026-09-10")
        broker_balance = {
            "status": "success",
            "mode": "LIVE_API",
            "snapshot_complete": True,
            "cash_available_usd": 100.0,
            "holdings": [{
                "ticker": "DELL",
                "quantity": 4.0,
                "avg_price": 100.0,
                "current_price": 99.0,
            }],
        }
        with patch.object(
            reconciliation.default_kis_broker,
            "get_overseas_balance",
            return_value=broker_balance,
        ):
            reconciliation.check_sync(auto_calibrate=True)

        holdings = persistence.get_live_portfolio()["holdings"]
        self.assertEqual(len(holdings), 2)
        self.assertEqual(sum(float(h["quantity"]) for h in holdings), 4.0)
        self.assertEqual(
            sorted(float(h["quantity"]) for h in holdings),
            [1.0, 3.0],
        )
        # No broker fill record means no fabricated realized P&L.
        self.assertEqual(persistence.get_trade_history_records(), [])

    def test_forward_split_scales_satellite_lots_without_false_buy(self):
        from al_sangmoo.domain import reconciliation

        persistence.add_portfolio_buy("DELL", 100.0, 2.0, "2026-09-09")
        persistence.add_portfolio_buy("DELL", 110.0, 3.0, "2026-09-10")
        broker_balance = {
            "status": "success",
            "mode": "LIVE_API",
            "snapshot_complete": True,
            "cash_available_usd": 0.0,
            "holdings": [{
                "ticker": "DELL",
                "quantity": 10.0,
                "avg_price": 53.0,
                "current_price": 60.0,
            }],
        }
        with patch.object(
            reconciliation.default_kis_broker,
            "get_overseas_balance",
            return_value=broker_balance,
        ):
            result = reconciliation.check_sync(auto_calibrate=True)

        holdings = persistence.get_live_portfolio()["holdings"]
        self.assertEqual(len(holdings), 2)
        self.assertEqual(sum(float(h["quantity"]) for h in holdings), 10.0)
        self.assertAlmostEqual(
            sum(float(h["total_cost"]) for h in holdings),
            530.0,
        )
        self.assertEqual(
            result["calibrations"][0]["action"],
            "ADJUST_SATELLITE_CORPORATE_ACTION",
        )
        self.assertEqual(persistence.get_execution_logs()[0]["side"], "SYNC")

    def test_reconciled_full_exit_writes_sell_log_and_persistent_lock(self):
        from al_sangmoo.domain import reconciliation

        persistence.add_portfolio_buy(
            ticker="DELL",
            buy_price=500.0,
            quantity=4.0,
            buy_date="2026-09-10",
        )
        persistence.claim_satellite_order_intent(
            "DELL",
            "SELL",
            4.0,
            490.0,
            "GUARDIAN",
            "NYSE",
            baseline_quantity=4.0,
        )
        persistence.mark_satellite_order_submitted(
            "DELL", "SELL", "SELL-1", "CLIENT-1"
        )
        broker_balance = {
            "status": "success",
            "mode": "LIVE_API",
            "snapshot_complete": True,
            "cash_available_usd": 2000.0,
            # A final account-wide exit legitimately leaves no holdings.
            "holdings": [],
        }
        with patch.object(
            reconciliation.default_kis_broker,
            "get_overseas_balance",
            return_value=broker_balance,
        ), patch.object(
            reconciliation.default_kis_broker,
            "get_overseas_order_state",
            return_value={
                "status": "FILLED",
                "terminal": True,
                "filled_quantity": 4.0,
                "remaining_quantity": 0.0,
                "fill_price": 490.0,
            },
        ):
            res = reconciliation.check_sync(auto_calibrate=True)

        self.assertEqual(res["status"], "success")
        dell_logs = [
            row for row in persistence.get_execution_logs()
            if row["ticker"] == "DELL"
        ]
        self.assertEqual(len(dell_logs), 1)
        self.assertEqual(dell_logs[0]["side"], "SELL")
        self.assertEqual(dell_logs[0]["status"], "RECONCILED")
        self.assertTrue(exit_cooldown.is_in_cooldown("DELL"))

    def test_incomplete_empty_snapshot_cannot_close_local_holding(self):
        from al_sangmoo.domain import reconciliation

        hid = persistence.add_portfolio_buy(
            ticker="DELL",
            buy_price=100.0,
            quantity=5.0,
            buy_date="2026-09-10",
        )
        broker_balance = {
            "status": "success",
            "mode": "LIVE_API",
            "snapshot_complete": False,
            "cash_available_usd": 1000.0,
            "holdings": [],
        }
        with patch.object(
            reconciliation.default_kis_broker,
            "get_overseas_balance",
            return_value=broker_balance,
        ):
            result = reconciliation.check_sync(auto_calibrate=True)

        self.assertEqual(result["calibrations_count"], 0)
        holding = next(
            h for h in persistence.get_live_portfolio()["holdings"]
            if int(h["id"]) == hid
        )
        self.assertEqual(holding["status"], "HOLDING")
        self.assertEqual(persistence.get_execution_logs(), [])

    def test_partial_sell_reconciliation_books_realized_pnl(self):
        from al_sangmoo.domain import reconciliation

        persistence.add_portfolio_buy(
            ticker="DELL",
            buy_price=100.0,
            quantity=10.0,
            buy_date="2026-09-10",
        )
        persistence.claim_satellite_order_intent(
            "DELL",
            "SELL",
            4.0,
            90.0,
            "GUARDIAN",
            "NYSE",
            baseline_quantity=10.0,
        )
        persistence.mark_satellite_order_submitted(
            "DELL", "SELL", "SELL-PARTIAL", "CLIENT-PARTIAL"
        )
        broker_balance = {
            "status": "success",
            "mode": "LIVE_API",
            "snapshot_complete": True,
            "cash_available_usd": 400.0,
            "holdings": [{
                "ticker": "DELL",
                "quantity": 6.0,
                "avg_price": 100.0,
                "current_price": 90.0,
            }],
        }
        with patch.object(
            reconciliation.default_kis_broker,
            "get_overseas_balance",
            return_value=broker_balance,
        ), patch.object(
            reconciliation.default_kis_broker,
            "get_overseas_order_state",
            return_value={
                "status": "FILLED",
                "terminal": True,
                "filled_quantity": 4.0,
                "remaining_quantity": 0.0,
                "fill_price": 90.0,
            },
        ):
            result = reconciliation.check_sync(auto_calibrate=True)

        self.assertEqual(result["calibrations_count"], 1)
        history = persistence.get_trade_history_records()
        self.assertEqual(len(history), 1)
        self.assertEqual(
            history[0]["reason"],
            "RECONCILED_CONFIRMED_SELL_FILL",
        )
        self.assertAlmostEqual(float(history[0]["quantity"]), 4.0)
        self.assertAlmostEqual(float(history[0]["pnl_amount"]), -40.0)

    def test_proxy_partial_fill_keeps_intent_until_terminal_cancel(self):
        from al_sangmoo.domain import reconciliation

        persistence.add_portfolio_buy(
            ticker="QQQ",
            buy_price=100.0,
            quantity=10.0,
            buy_date="2026-09-10",
        )
        persistence.claim_proxy_order_intent(
            "QQQ",
            "SELL",
            4.0,
            99.0,
            "AUTOPILOT",
            "NASD",
            baseline_quantity=10.0,
        )
        persistence.mark_proxy_order_submitted(
            "QQQ", "PROXY-SELL-1", "PROXY-CLIENT-1"
        )
        partial_balance = {
            "status": "success",
            "mode": "LIVE_API",
            "snapshot_complete": True,
            "cash_available_usd": 200.0,
            "holdings": [{
                "ticker": "QQQ",
                "quantity": 8.0,
                "avg_price": 100.0,
                "current_price": 99.0,
            }],
        }
        with patch.object(
            reconciliation.default_kis_broker,
            "get_overseas_balance",
            return_value=partial_balance,
        ), patch.object(
            reconciliation.default_kis_broker,
            "get_overseas_order_state",
            return_value={
                "status": "PARTIALLY_FILLED",
                "terminal": False,
                "filled_quantity": 2.0,
                "remaining_quantity": 2.0,
                "fill_price": 99.0,
            },
        ):
            reconciliation.check_sync(auto_calibrate=True)

        intents = persistence.get_proxy_order_intents()
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0]["status"], "PARTIALLY_FILLED")
        self.assertEqual(float(intents[0]["filled_quantity"]), 2.0)
        self.assertEqual(float(intents[0]["applied_quantity"]), 2.0)
        proxy_history = persistence.get_trade_history_records()
        self.assertEqual(len(proxy_history), 1)
        self.assertEqual(float(proxy_history[0]["quantity"]), 2.0)
        self.assertAlmostEqual(float(proxy_history[0]["pnl_amount"]), -2.0)

        with patch.object(
            reconciliation.default_kis_broker,
            "get_overseas_balance",
            return_value=partial_balance,
        ), patch.object(
            reconciliation.default_kis_broker,
            "get_overseas_order_state",
            return_value={
                "status": "CANCELLED",
                "terminal": True,
                "filled_quantity": 2.0,
                "remaining_quantity": 0.0,
                "fill_price": 99.0,
            },
        ):
            reconciliation.check_sync(auto_calibrate=True)
        self.assertEqual(persistence.get_proxy_order_intents(), [])

    def test_proxy_intent_clears_after_late_terminal_state_without_new_delta(self):
        from al_sangmoo.domain import reconciliation

        persistence.add_portfolio_buy("QQQ", 100.0, 10.0, "2026-09-10")
        persistence.claim_proxy_order_intent(
            "QQQ",
            "SELL",
            4.0,
            99.0,
            "AUTOPILOT",
            "NASD",
            baseline_quantity=10.0,
        )
        persistence.mark_proxy_order_submitted("QQQ", "SELL-LATE", "CID-LATE")
        balance_after_fill = {
            "status": "success",
            "mode": "LIVE_API",
            "snapshot_complete": True,
            "cash_available_usd": 400.0,
            "holdings": [{
                "ticker": "QQQ",
                "quantity": 6.0,
                "avg_price": 100.0,
                "current_price": 99.0,
            }],
        }
        with patch.object(
            reconciliation.default_kis_broker,
            "get_overseas_balance",
            return_value=balance_after_fill,
        ), patch.object(
            reconciliation.default_kis_broker,
            "get_overseas_order_state",
            return_value={
                "status": "PARTIALLY_FILLED",
                "terminal": False,
                "filled_quantity": 4.0,
                "remaining_quantity": 0.0,
                "fill_price": 99.0,
            },
        ):
            reconciliation.check_sync(auto_calibrate=True)
        self.assertEqual(
            persistence.get_proxy_order_intents()[0]["status"],
            "PARTIALLY_FILLED",
        )

        offset_balance = {
            **balance_after_fill,
            "holdings": [{
                **balance_after_fill["holdings"][0],
                # An unrelated offsetting buy must not regress applied sell qty.
                "quantity": 8.0,
            }],
        }
        with patch.object(
            reconciliation.default_kis_broker,
            "get_overseas_balance",
            return_value=offset_balance,
        ), patch.object(
            reconciliation.default_kis_broker,
            "get_overseas_order_state",
            return_value={
                "status": "FILLED",
                "terminal": True,
                "filled_quantity": 4.0,
                "remaining_quantity": 0.0,
                "fill_price": 99.0,
            },
        ):
            reconciliation.check_sync(auto_calibrate=True)
        self.assertEqual(persistence.get_proxy_order_intents(), [])


# --------------------------------------------------------------------------- #
# 작업 3: DELL quotes NYSE first (fixes empty-NASDAQ-query delay)
# --------------------------------------------------------------------------- #
class TestKISDellExchangeOrder(unittest.TestCase):
    def test_dell_quotes_nyse_first(self):
        adapter = KISBrokerAdapter(app_key="", app_secret="", account_no="")
        order = adapter._quote_exchanges("DELL")
        self.assertEqual(order[0], "NYS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
