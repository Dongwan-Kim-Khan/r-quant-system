"""
Unit & Integration Tests for Phase 1 P0 Critical Fixes:
1. Dynamic C1-M2 pre-trade guardrail sizing (Slot 1 up to 55%, Cash Proxy up to 100%/150%, Slot 2/3 buffers).
2. MSI Polarity bugfix in dashboard router (msi < 50 is bull, msi >= 50 is defense/bear).
3. Portfolio & Broker API router order schemas and pre-trade guardrail integration.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.domain.risk.order_guardrail import (
    validate_pre_trade_guardrail,
    resolve_guardrail_max_allocation_pct,
)
from fastapi import HTTPException
from al_sangmoo.interfaces.api.routers.dashboard import get_dashboard_data
from al_sangmoo.interfaces.api.routers.portfolio import BuyOrder, buy_stock
from al_sangmoo.interfaces.api.routers.broker import BrokerOrderRequest

MOCK_BULL_FEED = {
    "slot_allocation_summary": {
        "is_bull_regime": True,
        "leverage": {"leverage_mode": False},
    },
    "macro": {
        "is_bull_regime": True,
        "msi_score": 40.0,
    },
    "top_conviction_pick": {
        "ticker": "DELL",
        "name": "Dell Technologies",
        "price": 533.88,
        "sizing": {"slot_rank": 1},
    },
    "top_conviction_runner_up": {
        "ticker": "CRWD",
        "name": "CrowdStrike",
        "price": 210.02,
        "sizing": {"slot_rank": 2},
    },
    "ranked_conviction_list": [
        {
            "ticker": "REGN",
            "name": "Regeneron",
            "price": 100.0,
            "sizing": {"slot_rank": 3},
        }
    ],
}


class TestPreTradeGuardrailDynamicSizing(unittest.TestCase):
    def setUp(self):
        self.equity = 100_000.0
        self.feed_patcher = patch("al_sangmoo.domain.risk.order_guardrail.load_feed_snapshot", return_value=MOCK_BULL_FEED)
        self.feed_patcher.start()
        self.addCleanup(self.feed_patcher.stop)

    def test_cash_proxy_qqq_unleveraged_allows_100_percent(self):
        """Cash proxy QQQ in 1.0x mode allows up to 100% of equity."""
        limit = resolve_guardrail_max_allocation_pct("QQQ", leverage_mode=False)
        self.assertEqual(limit, 1.00)

        # 95% order should pass
        res_ok = validate_pre_trade_guardrail("QQQ", 500.0, 190.0, self.equity, [], leverage_mode=False, msi_score=40.0)
        self.assertTrue(res_ok["allowed"])

        # 105% order should fail
        res_fail = validate_pre_trade_guardrail("QQQ", 500.0, 210.0, self.equity, [], leverage_mode=False, msi_score=40.0)
        self.assertFalse(res_fail["allowed"])
        self.assertIn("초과", res_fail["reason"])

    def test_cash_proxy_qld_and_leveraged_mode_allows_150_percent(self):
        """Cash proxy in leverage mode (or QLD) allows up to 150% gross target."""
        limit_qld = resolve_guardrail_max_allocation_pct("QLD", leverage_mode=False)
        self.assertEqual(limit_qld, 1.50)

        limit_lev_qqq = resolve_guardrail_max_allocation_pct("QQQ", leverage_mode=True)
        self.assertEqual(limit_lev_qqq, 1.50)

        # 140% order on QLD should pass
        res_ok = validate_pre_trade_guardrail("QLD", 100.0, 1400.0, self.equity, [], leverage_mode=True, msi_score=40.0)
        self.assertTrue(res_ok["allowed"])

        # 155% order should fail
        res_fail = validate_pre_trade_guardrail("QLD", 100.0, 1550.0, self.equity, [], leverage_mode=True, msi_score=40.0)
        self.assertFalse(res_fail["allowed"])
        self.assertIn("초과", res_fail["reason"])

    def test_c1_m2_bull_slot_1_allows_up_to_55_percent(self):
        """Slot 1 in Bull regime (50% target) allows up to 55% buffer."""
        limit = resolve_guardrail_max_allocation_pct("NVDA", active_holdings=[], slot_rank=1, is_bull=True)
        self.assertEqual(limit, 0.55)

        # 50% order ($50,000) - Previously REJECTED with 25% hardcap - MUST NOW PASS
        res_50 = validate_pre_trade_guardrail("NVDA", 100.0, 500.0, self.equity, [], slot_rank=1, is_bull=True, msi_score=40.0)
        self.assertTrue(res_50["allowed"], f"50% Slot 1 buy must be allowed: {res_50.get('reason')}")

        # 54% order ($54,000) - Buffer range - MUST PASS
        res_54 = validate_pre_trade_guardrail("NVDA", 100.0, 540.0, self.equity, [], slot_rank=1, is_bull=True, msi_score=40.0)
        self.assertTrue(res_54["allowed"])

        # 56% order ($56,000) - Exceeds 55% cap - MUST BE REJECTED
        res_56 = validate_pre_trade_guardrail("NVDA", 100.0, 560.0, self.equity, [], slot_rank=1, is_bull=True, msi_score=40.0)
        self.assertFalse(res_56["allowed"])
        self.assertIn("초과", res_56["reason"])

    def test_c1_m2_bull_slot_2_and_3_buffer_limits(self):
        """Slot 2 allows up to 35%, Slot 3 allows up to 25%."""
        limit_s2 = resolve_guardrail_max_allocation_pct("AMZN", slot_rank=2, is_bull=True)
        self.assertEqual(limit_s2, 0.35)

        limit_s3 = resolve_guardrail_max_allocation_pct("AAPL", slot_rank=3, is_bull=True)
        self.assertEqual(limit_s3, 0.25)

        # Slot 2: 32% passes, 36% fails
        res_s2_ok = validate_pre_trade_guardrail("AMZN", 100.0, 320.0, self.equity, [], slot_rank=2, is_bull=True, msi_score=40.0)
        self.assertTrue(res_s2_ok["allowed"])
        res_s2_fail = validate_pre_trade_guardrail("AMZN", 100.0, 360.0, self.equity, [], slot_rank=2, is_bull=True, msi_score=40.0)
        self.assertFalse(res_s2_fail["allowed"])

        # Slot 3: 22% passes, 26% fails
        res_s3_ok = validate_pre_trade_guardrail("AAPL", 100.0, 220.0, self.equity, [], slot_rank=3, is_bull=True, msi_score=40.0)
        self.assertTrue(res_s3_ok["allowed"])
        res_s3_fail = validate_pre_trade_guardrail("AAPL", 100.0, 260.0, self.equity, [], slot_rank=3, is_bull=True, msi_score=40.0)
        self.assertFalse(res_s3_fail["allowed"])

    def test_c1_m2_bear_regime_limits(self):
        """Bear regime allows max 30% for Slot 1 and Slot 2."""
        limit_b1 = resolve_guardrail_max_allocation_pct("NVDA", slot_rank=1, is_bull=False)
        self.assertEqual(limit_b1, 0.30)
        limit_b2 = resolve_guardrail_max_allocation_pct("AMZN", slot_rank=2, is_bull=False)
        self.assertEqual(limit_b2, 0.30)

        # 28% passes, 32% fails in bear regime
        res_b_ok = validate_pre_trade_guardrail("NVDA", 100.0, 280.0, self.equity, [], is_bull=False, slot_rank=1, msi_score=55.0)
        self.assertTrue(res_b_ok["allowed"])
        res_b_fail = validate_pre_trade_guardrail("NVDA", 100.0, 320.0, self.equity, [], is_bull=False, slot_rank=1, msi_score=55.0)
        self.assertFalse(res_b_fail["allowed"])

    def test_explicit_max_single_asset_pct_override(self):
        """If caller explicitly passes max_single_asset_pct, it is strictly respected."""
        res_strict = validate_pre_trade_guardrail("NVDA", 100.0, 200.0, self.equity, [], max_single_asset_pct=0.15, msi_score=40.0)
        self.assertFalse(res_strict["allowed"])
        self.assertIn("15%", res_strict["reason"])

    def test_automatic_slot_rank_detection_from_holdings(self):
        """If slot_rank is None, detects slot rank from existing active satellite holdings."""
        # 0 satellites -> Slot 1 (55%)
        res_slot1 = validate_pre_trade_guardrail("NVDA", 100.0, 500.0, self.equity, [], is_bull=True, msi_score=40.0)
        self.assertTrue(res_slot1["allowed"])

        # 1 existing satellite -> Slot 2 (35%)
        holdings_1 = [{"ticker": "NVDA", "current_value": 50000.0}]
        res_slot2 = validate_pre_trade_guardrail("AMZN", 100.0, 320.0, self.equity, holdings_1, is_bull=True, msi_score=40.0)
        self.assertTrue(res_slot2["allowed"])

        res_slot2_over = validate_pre_trade_guardrail("AMZN", 100.0, 370.0, self.equity, holdings_1, is_bull=True, msi_score=40.0)
        self.assertFalse(res_slot2_over["allowed"])

    def test_quick_buy_slot_1_auto_detect_from_feed_without_explicit_params(self):
        """
        Critical P0 Bug Repro: Quick Buy of Slot 1 directly from dashboard
        called without explicit is_bull, slot_rank, or max_pct must NOT be rejected.
        DELL is Slot 1 (50% target in feed). Sizing: 7 shares * 533.88 = $3,737.16 on $7,477.18 equity (49.98%).
        """
        res = validate_pre_trade_guardrail("DELL", 533.88, 7.0, 7477.18, [])
        self.assertTrue(res["allowed"], f"Quick buy of Slot 1 must pass guardrail: {res.get('reason')}")
        self.assertEqual(res.get("max_single_asset_pct"), 0.55)

    def test_runner_up_and_ranked_conviction_candidates_auto_detect(self):
        """Runner-up (CRWD) gets 35% buffer, ranked candidate (REGN) gets 25% buffer."""
        # Runner-up (CRWD)
        res_crwd = validate_pre_trade_guardrail("CRWD", 210.02, 10.0, 7477.18, [])
        self.assertTrue(res_crwd["allowed"])
        self.assertEqual(res_crwd.get("max_single_asset_pct"), 0.35)

        # Ranked list Slot 3 candidate (REGN)
        res_regn = validate_pre_trade_guardrail("REGN", 100.0, 15.0, 7477.18, [])
        self.assertTrue(res_regn["allowed"])
        self.assertEqual(res_regn.get("max_single_asset_pct"), 0.25)

    def test_deduplicate_holdings_preserves_slot_rank(self):
        """Multiple lots of the same ticker in active_holdings must not skew slot ranking."""
        multi_lot_nvda = [
            {"ticker": "NVDA", "current_value": 15000.0},
            {"ticker": "NVDA", "current_value": 15000.0},
        ]
        # A new ticker should take Slot 2 (35% in bull regime), NOT Slot 3 (25%)
        pct = resolve_guardrail_max_allocation_pct("UNKNOWN_CO", active_holdings=multi_lot_nvda, is_bull=True)
        self.assertEqual(pct, 0.35)


class TestDashboardMsiPolarityFix(unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_msi_polarity_not_inverted(self):
        """MSI is a RISK index. MSI >= 50 must NOT set is_bull_regime to True."""
        # 1. High risk (MSI 70.0) without SPY -> is_bull_regime MUST be False
        mock_feed_high_msi = {
            "macro": {
                "macro_climate": {"msi_score": 70.0}
            },
            "holdings": [],
            "kpis": {}
        }
        with patch("al_sangmoo.interfaces.api.routers.dashboard.get_feed_cache", return_value=mock_feed_high_msi):
            data = await get_dashboard_data()
            slot_sum = data.get("slot_allocation_summary", {})
            self.assertFalse(slot_sum.get("is_bull_regime"), "MSI 70.0 must NOT be classified as bull regime!")

        # 2. Low risk (MSI 25.0) without SPY -> is_bull_regime MUST be True
        mock_feed_low_msi = {
            "macro": {
                "macro_climate": {"msi_score": 25.0}
            },
            "holdings": [],
            "kpis": {}
        }
        with patch("al_sangmoo.interfaces.api.routers.dashboard.get_feed_cache", return_value=mock_feed_low_msi):
            data = await get_dashboard_data()
            slot_sum = data.get("slot_allocation_summary", {})
            self.assertTrue(slot_sum.get("is_bull_regime"), "MSI 25.0 must be classified as bull regime!")

        # 3. Explicit is_bull_regime in macro -> strictly respected
        mock_feed_explicit_bear = {
            "macro": {
                "is_bull_regime": False,
                "macro_climate": {"msi_score": 20.0}
            },
            "holdings": [],
            "kpis": {}
        }
        with patch("al_sangmoo.interfaces.api.routers.dashboard.get_feed_cache", return_value=mock_feed_explicit_bear):
            data = await get_dashboard_data()
            slot_sum = data.get("slot_allocation_summary", {})
            self.assertFalse(slot_sum.get("is_bull_regime"))


class TestOrderSchemasAndBuyTopPick(unittest.IsolatedAsyncioTestCase):
    def test_buy_order_schema_accepts_slot_rank_and_max_pct(self):
        """BuyOrder accepts optional slot_rank and max_single_asset_pct."""
        bo = BuyOrder(ticker="NVDA", buy_price=100.0, qty=5.0, slot_rank=1, max_single_asset_pct=0.55)
        self.assertEqual(bo.slot_rank, 1)
        self.assertEqual(bo.max_single_asset_pct, 0.55)

    def test_broker_order_request_schema_accepts_slot_rank_and_max_pct(self):
        """BrokerOrderRequest accepts optional slot_rank and max_single_asset_pct."""
        bor = BrokerOrderRequest(ticker="NVDA", price=100.0, qty=5, slot_rank=1, max_single_asset_pct=0.55)
        self.assertEqual(bor.slot_rank, 1)
        self.assertEqual(bor.max_single_asset_pct, 0.55)

    @patch("al_sangmoo.domain.risk.order_guardrail.load_feed_snapshot", return_value=MOCK_BULL_FEED)
    async def test_buy_stock_slot1_without_explicit_params_succeeds(self, mock_feed):
        """POST /api/portfolio/buy for Slot 1 stock without explicit params passes guardrail."""
        order = BuyOrder(
            ticker="DELL",
            buy_price=533.88,
            quantity=7.0,
            buy_date="2026-09-10"
        )
        mock_port = {"total_equity_usd": 7477.18, "holdings": []}
        with patch("al_sangmoo.interfaces.api.routers.portfolio.add_portfolio_buy", return_value=999), \
             patch("al_sangmoo.interfaces.api.routers.portfolio.record_execution_log"), \
             patch("al_sangmoo.interfaces.api.routers.portfolio.hub.broadcast"), \
             patch("al_sangmoo.interfaces.api.routers.portfolio.get_live_portfolio", return_value=mock_port), \
             patch("al_sangmoo.interfaces.api.routers.portfolio.default_kis_broker.get_overseas_balance", return_value={"status": "success", "mode": "SIMULATED", "total_equity_usd": 7477.18, "holdings": []}), \
             patch("al_sangmoo.interfaces.api.routers.portfolio.default_kis_broker.place_order", return_value={"status": "success", "order_id": "TEST-123", "message": "OK"}):
            res = await buy_stock(order)
            self.assertEqual(res.get("status"), "success")
            self.assertEqual(res.get("id"), 999)

    @patch("al_sangmoo.domain.risk.order_guardrail.load_feed_snapshot", return_value=MOCK_BULL_FEED)
    async def test_buy_stock_slot1_combined_exposure_exceeds_cap_rejected(self, mock_feed):
        """If existing holding + new buy exceeds 55% cap, buy_stock correctly rejects with HTTP 400."""
        order = BuyOrder(
            ticker="DELL",
            buy_price=533.88,
            quantity=7.0,
            buy_date="2026-09-10"
        )
        # Existing $2,500 + new $3,737 = $6,237 on $7,477 equity (83.4% > 55%)
        mock_port = {
            "total_equity_usd": 7477.18,
            "holdings": [{"ticker": "DELL", "quantity": 5.0, "current_value": 2500.0}]
        }
        with patch("al_sangmoo.interfaces.api.routers.portfolio.get_live_portfolio", return_value=mock_port), \
             patch("al_sangmoo.interfaces.api.routers.portfolio.default_kis_broker.get_overseas_balance", return_value={"status": "success", "mode": "SIMULATED", "total_equity_usd": 7477.18, "holdings": []}):
            with self.assertRaises(HTTPException) as cm:
                await buy_stock(order)
            self.assertEqual(cm.exception.status_code, 400)
            self.assertIn("사전 리스크 한도 초과", cm.exception.detail)


if __name__ == "__main__":
    unittest.main()
