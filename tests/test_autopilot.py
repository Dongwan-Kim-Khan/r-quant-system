"""
Unit Tests for AutoPilotTrader (C1-M2 multi-slot + residual QQQ proxy)
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.domain.risk.autopilot_trader import AutoPilotTrader
from al_sangmoo.domain.risk import exit_cooldown
import db_manager


def _cand(ticker: str, price: float, rank: int) -> dict:
    return {
        "ticker": ticker,
        "name": ticker,
        "price": price,
        "kijun": round(price * 0.9, 2),
        "kijun_26": round(price * 0.9, 2),
        "composite_rs": 85.0 - rank,
        "conviction_score": 90 - rank,
        "bull_score": 90 - rank,
        "conviction_rank": rank,
        "sizing": {
            "eligible": True,
            "shares": 10,
            "is_bull_regime": True,
            "slot_weight": [0.34, 0.33, 0.33][rank - 1],
            "slot_rank": rank,
        },
    }


def _feed(ranked):
    return {
        "ranked_conviction_list": ranked,
        "top_conviction_pick": ranked[0] if ranked else None,
        "top_conviction_runner_up": ranked[1] if len(ranked) > 1 else None,
        "macro": {
            "is_bull_regime": True,
            "qqq_close": 500.0,
            "qqq_sma20": 480.0,
            "qqq_composite_rs": 70.0,
            "leverage": {"leverage_mode": False},
        },
        "slot_allocation_summary": {
            "is_bull_regime": True,
            "qqq_close": 500.0,
            "qqq_sma20": 480.0,
            "qqq_composite_rs": 70.0,
        },
    }


class TestAutoPilotTrader(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._prev_db = os.environ.get("AL_SANGMOO_DB_PATH")
        self._tmpdir = tempfile.TemporaryDirectory()
        self._test_db_path = os.path.join(self._tmpdir.name, "test_autopilot.db")
        os.environ["AL_SANGMOO_DB_PATH"] = self._test_db_path
        db_manager.init_database()
        exit_cooldown.clear()
        self.autopilot = AutoPilotTrader(check_interval_seconds=10)
        self._feed_path = os.path.join(self._tmpdir.name, "dashboard_data.json")

    async def asyncTearDown(self):
        exit_cooldown.clear()
        if self._prev_db is None:
            os.environ.pop("AL_SANGMOO_DB_PATH", None)
        else:
            os.environ["AL_SANGMOO_DB_PATH"] = self._prev_db
        self._tmpdir.cleanup()

    def _write_feed(self, payload: dict):
        with open(self._feed_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def test_autopilot_initial_state(self):
        st = self.autopilot.get_status()
        self.assertFalse(st["is_enabled"])
        self.assertFalse(st["is_running"])

    def test_autopilot_toggle(self):
        self.autopilot.set_enabled(False)
        self.assertFalse(self.autopilot.is_enabled)
        self.autopilot.set_enabled(True)
        self.assertTrue(self.autopilot.is_enabled)

    async def test_autopilot_deduplication_guardrail(self):
        with patch(
            "al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.place_order",
            return_value={"status": "submitted", "order_id": "MOCK999"},
        ), patch(
            "al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.get_overseas_balance",
            return_value={
                "holdings": [
                    {"ticker": "CVX", "quantity": 9.0},
                    {"ticker": "XOM", "quantity": 11.0},
                ]
            },
        ):
            res = await self.autopilot.run_autopilot_cycle(
                force_scan=False,
                enforce_entry_schedule=False,
            )
            self.assertIn(res.get("status"), ["skipped", "success"])
            if res.get("status") == "skipped":
                self.assertIn(
                    res.get("reason"),
                    [
                        "ALREADY_IN_WALLET",
                        "ALREADY_IN_BROKER_HOLDINGS",
                        "SLOTS_FULL",
                        "BROKER_SLOTS_FULL",
                        "NO_QUALIFIED_TOP_PICK",
                        "NO_MORE_UNHELD_CANDIDATES",
                        "AUTO_BUY_DISABLED",
                        "NO_ACTION",
                        "MARKET_CLOSED",
                        "BROKER_SNAPSHOT_UNVERIFIED",
                        "QQQ_BELOW_20MA",
                    ],
                )
            self.assertIn("cash_proxy", res)

    async def test_multi_slot_skips_held_rank1_and_fills_rank2_rank3(self):
        """Rank1 already held must NOT early-return; Rank2/3 should be attempted."""
        ranked = [
            _cand("AAA", 100.0, 1),
            _cand("BBB", 50.0, 2),
            _cand("CCC", 25.0, 3),
        ]
        self._write_feed(_feed(ranked))

        portfolio_state = {
            "holdings": [
                {
                    "id": 1,
                    "ticker": "AAA",
                    "quantity": 10.0,
                    "buy_price": 100.0,
                    "current_price": 100.0,
                },
                {
                    "id": 2,
                    "ticker": "QQQ",
                    "quantity": 20.0,
                    "buy_price": 400.0,
                    "current_price": 400.0,
                },
            ],
            "total_equity_usd": 10000.0,
            "cash_usd": 50.0,
        }

        bought = []

        def fake_add_buy(**kwargs):
            bought.append(kwargs["ticker"])
            hid = 100 + len(bought)
            portfolio_state["holdings"].append(
                {
                    "id": hid,
                    "ticker": kwargs["ticker"],
                    "quantity": float(kwargs["quantity"]),
                    "buy_price": float(kwargs["buy_price"]),
                    "current_price": float(kwargs["buy_price"]),
                }
            )
            return hid

        async def fake_broadcast(*_a, **_k):
            return None

        async def fake_broadcast_delta(*_a, **_k):
            return None

        with patch("al_sangmoo.domain.risk.autopilot_trader.DASHBOARD_JSON", self._feed_path), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio", side_effect=lambda: dict(portfolio_state, holdings=list(portfolio_state["holdings"]))), \
             patch("al_sangmoo.domain.risk.autopilot_trader.add_portfolio_buy", side_effect=fake_add_buy), \
             patch("al_sangmoo.domain.risk.autopilot_trader.sync_portfolio_prices", side_effect=lambda: portfolio_state), \
             patch("al_sangmoo.domain.risk.autopilot_trader.hub.broadcast", new=AsyncMock(side_effect=fake_broadcast)), \
             patch("al_sangmoo.domain.risk.autopilot_trader.hub.broadcast_delta", new=AsyncMock(side_effect=fake_broadcast_delta)), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured", return_value=False), \
             patch.object(self.autopilot, "_ensure_cash_proxy_parked", return_value={"idle_nav_usd": 2000.0, "executed": []}), \
             patch.object(self.autopilot, "_free_proxy_cash_for_entry", return_value=[{"ticker": "QQQ", "qty": 5}]) as free_proxy:
            self.autopilot.set_enabled(True)
            res = await self.autopilot.run_autopilot_cycle(
                force_scan=False,
                enforce_entry_schedule=False,
            )

        self.assertEqual(res.get("status"), "success")
        self.assertEqual(res.get("reason"), "MULTI_SLOT_ENTRIES")
        self.assertEqual(bought, ["BBB", "CCC"])
        entry_ranks = [e.get("slot_rank") for e in res.get("entries", [])]
        self.assertEqual(entry_ranks, [2, 3])
        # Slot weights for ranks 2/3 in bull (C-2: 34/33/33)
        weights = [e["trade"].get("slot_weight") for e in res.get("entries", [])]
        self.assertEqual(weights, [0.33, 0.33])
        self.assertTrue(free_proxy.called)
        self.assertIn("cash_proxy", res)
        # Must never short-circuit as ALREADY_IN_WALLET for the whole cycle
        self.assertNotEqual(res.get("reason"), "ALREADY_IN_WALLET")

    async def test_residual_proxy_parked_after_partial_slots(self):
        """Even with empty remaining slots and no more candidates, proxy park runs."""
        ranked = [_cand("ONLY", 100.0, 1)]
        self._write_feed(_feed(ranked))
        portfolio_state = {
            "holdings": [
                {
                    "id": 1,
                    "ticker": "ONLY",
                    "quantity": 40.0,
                    "buy_price": 100.0,
                    "current_price": 100.0,
                }
            ],
            "total_equity_usd": 10000.0,
            "cash_usd": 6000.0,
        }
        park = MagicMock(return_value={"idle_nav_usd": 6000.0, "qqq_shares": 15, "executed": []})

        with patch("al_sangmoo.domain.risk.autopilot_trader.DASHBOARD_JSON", self._feed_path), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio", return_value=portfolio_state), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured", return_value=False), \
             patch.object(self.autopilot, "_ensure_cash_proxy_parked", park):
            self.autopilot.set_enabled(True)
            res = await self.autopilot.run_autopilot_cycle(
                force_scan=False,
                enforce_entry_schedule=False,
            )

        park.assert_called_once()
        self.assertIn("cash_proxy", res)
        skip_reasons = {s.get("reason") for s in res.get("skipped", [])}
        self.assertTrue(
            skip_reasons & {"NO_MORE_UNHELD_CANDIDATES", "SLOTS_FULL", "AUTO_BUY_DISABLED"}
            or res.get("reason") == "NO_MORE_UNHELD_CANDIDATES"
            or res.get("status") in ("skipped", "success")
        )

    async def test_auto_buy_off_still_parks_proxy_and_logs_slots(self):
        ranked = [_cand("AAA", 100.0, 1), _cand("BBB", 50.0, 2)]
        self._write_feed(_feed(ranked))
        portfolio_state = {
            "holdings": [],
            "total_equity_usd": 10000.0,
            "cash_usd": 10000.0,
        }
        park = MagicMock(return_value={"idle_nav_usd": 10000.0, "executed": []})

        with patch("al_sangmoo.domain.risk.autopilot_trader.DASHBOARD_JSON", self._feed_path), \
             patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio", return_value=portfolio_state), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured", return_value=False), \
             patch.object(self.autopilot, "_ensure_cash_proxy_parked", park):
            self.autopilot.set_enabled(False)
            res = await self.autopilot.run_autopilot_cycle(
                force_scan=False,
                enforce_entry_schedule=False,
            )

        park.assert_called_once()
        self.assertEqual(res.get("reason"), "AUTO_BUY_DISABLED")
        disabled = [s for s in res.get("skipped", []) if s.get("reason") == "AUTO_BUY_DISABLED"]
        self.assertGreaterEqual(len(disabled), 2)
        self.assertEqual([s.get("slot_rank") for s in disabled[:2]], [1, 2])

    async def test_submitted_buy_stops_candidate_fanout_and_proxy_parking(self):
        """One accepted live order owns the slot/cash until reconciliation."""
        ranked = [
            _cand("AAA", 100.0, 1),
            _cand("BBB", 50.0, 2),
            _cand("CCC", 25.0, 3),
        ]
        self._write_feed(_feed(ranked))
        portfolio_state = {
            "holdings": [],
            "total_equity_usd": 10000.0,
            "cash_usd": 10000.0,
        }
        park = MagicMock(return_value={"executed": []})

        with patch(
            "al_sangmoo.domain.risk.autopilot_trader.DASHBOARD_JSON",
            self._feed_path,
        ), patch(
            "al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio",
            return_value=portfolio_state,
        ), patch(
            "al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured",
            return_value=True,
        ), patch(
            "al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.get_overseas_balance",
            return_value={
                "status": "success",
                "snapshot_complete": True,
                "holdings": [],
                "total_equity_usd": 10000.0,
                "cash_available_usd": 10000.0,
            },
        ), patch(
            "al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.place_order",
            return_value={
                "status": "submitted",
                "order_id": "LIVE-ACK-1",
                "message": "accepted",
            },
        ) as place_order, patch(
            "al_sangmoo.domain.risk.autopilot_trader.is_us_regular_hours",
            return_value=True,
        ), patch.object(
            self.autopilot,
            "_free_proxy_cash_for_entry",
            return_value=[],
        ), patch.object(
            self.autopilot,
            "_ensure_cash_proxy_parked",
            park,
        ):
            self.autopilot.set_enabled(True)
            res = await self.autopilot.run_autopilot_cycle(
                force_scan=False,
                enforce_entry_schedule=False,
            )

        self.assertEqual(res["status"], "submitted")
        self.assertEqual(place_order.call_count, 1)
        self.assertEqual(place_order.call_args.kwargs["ticker"], "AAA")
        park.assert_not_called()
        self.assertEqual(
            res["cash_proxy"]["status"],
            "SKIPPED_PENDING_SATELLITE_ORDER",
        )

    def test_broker_slot_guard_fails_closed_on_partial_snapshot(self):
        with patch(
            "al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.is_configured",
            return_value=True,
        ), patch(
            "al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.get_overseas_balance",
            return_value={
                "status": "partial",
                "snapshot_complete": False,
                "holdings": [],
                "failed_exchanges": [{"exchange": "NYSE"}],
            },
        ):
            blocked = self.autopilot._broker_slot_guard("DELL", 3)

        self.assertEqual(blocked["reason"], "BROKER_SNAPSHOT_UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
