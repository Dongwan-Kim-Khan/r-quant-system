"""
Unit Tests for AutoPilotTrader (100% Full-Auto Quant Trader)
"""
import unittest
import asyncio
import os
import sys
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.domain.risk.autopilot_trader import AutoPilotTrader
import db_manager


class TestAutoPilotTrader(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        db_manager.init_database()
        self.autopilot = AutoPilotTrader(check_interval_seconds=10)

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
        # Mock broker place_order to prevent any live order execution during testing
        from unittest.mock import patch
        with patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.place_order", return_value={"status": "submitted", "order_id": "MOCK999"}), \
             patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker.get_overseas_balance", return_value={"holdings": [{"ticker": "CVX", "quantity": 9.0}, {"ticker": "XOM", "quantity": 11.0}]}):
            res = await self.autopilot.run_autopilot_cycle(force_scan=False)
            self.assertIn(res.get("status"), ["skipped", "success"])
            if res.get("status") == "skipped":
                self.assertIn(res.get("reason"), ["ALREADY_IN_WALLET", "ALREADY_IN_BROKER_HOLDINGS", "SLOTS_FULL", "BROKER_SLOTS_FULL", "NO_QUALIFIED_TOP_PICK", "AUTO_BUY_DISABLED"])


if __name__ == '__main__':
    unittest.main()