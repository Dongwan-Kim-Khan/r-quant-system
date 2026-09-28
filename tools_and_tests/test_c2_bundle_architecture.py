"""
Comprehensive Unit & Integration Test Suite for Track 2: C-2 Bundle Architecture
Adhering strictly to Cursor's verdict and Track 1 validation results.

Tests cover:
1. SSOT Constants & Sizing (34/33/33 bull, 25/25 bear, -7% EOD / -10% Emerg, +18% ATR 3.0).
2. Dual-Clock Stop Loss Mechanics (intraday emergency stop vs. EOD closing window).
3. US Market Time & EOD Window Detection (regular, half-days, EDT/EST).
4. Uncapped Trailing Stop (+18% activation, 3.0 ATR, Kijun floor, disabled standalone Kijun exit).
5. Autopilot Entry Gates (Trend Gate: QQQ >= 20MA; Alpha Gate: Stock RS >= QQQ RS).
6. Order Guardrail 0.39 / 0.38 / 0.38 Caps & Cash Proxy Overlay.
7. Backward Compatibility: Preserving Legacy C1 Lots.
"""
import os
import sys
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.core.constants import (
    ATR_MULTIPLIER,
    CASH_PROXY_TICKER,
    EMERGENCY_STOP_LOSS_MULT,
    EMERGENCY_STOP_LOSS_PCT,
    EMERGENCY_STOP_PCT,
    EOD_HARD_STOP_PCT,
    EOD_STOP_LOSS_MULT,
    EOD_STOP_LOSS_PCT,
    HARD_STOP_PCT,
    MAX_SLOTS_BEAR,
    MAX_SLOTS_BULL,
    SLOT_WEIGHTS_BEAR,
    SLOT_WEIGHTS_BULL,
    STANDALONE_KIJUN_EXIT_ENABLED,
    STOP_LOSS_MULT,
    STOP_LOSS_PCT,
    TAKE_PROFIT_MULT,
    TAKE_PROFIT_PCT,
    TRAILING_ACTIVATE_PCT,
    derive_emergency_stop_price,
    derive_eod_stop_price,
    derive_stop_price,
    derive_target_price,
)
from al_sangmoo.core.market_time import (
    get_us_market_close_minute,
    is_eod_window_for_ticker,
    is_us_eod_window,
    is_us_half_day,
    is_us_regular_hours,
    now_us_eastern,
)
from al_sangmoo.domain.risk.trailing_stop import (
    ATR_TRAIL_MULT,
    compute_trailing_floor,
    evaluate_guardian_exit,
    latch_peak_gain,
)
from al_sangmoo.domain.risk.order_guardrail import (
    resolve_guardrail_max_allocation_pct,
    validate_pre_trade_guardrail,
)
from al_sangmoo.domain.risk.autopilot_trader import AutoPilotTrader, evaluate_entry_gate


class TestC2ConstantsAndSizingSSOT(unittest.TestCase):
    """1. Constants & Sizing SSOT verification."""

    def test_c2_slot_weights(self):
        self.assertEqual(SLOT_WEIGHTS_BULL, [0.34, 0.33, 0.33])
        self.assertEqual(SLOT_WEIGHTS_BEAR, [0.25, 0.25])
        self.assertEqual(MAX_SLOTS_BULL, 3)
        self.assertEqual(MAX_SLOTS_BEAR, 2)

    def test_c2_stop_and_take_profit_thresholds(self):
        self.assertEqual(STOP_LOSS_PCT, -0.07)
        self.assertEqual(EOD_STOP_LOSS_PCT, -0.07)
        self.assertEqual(EMERGENCY_STOP_LOSS_PCT, -0.10)
        self.assertEqual(TAKE_PROFIT_PCT, 0.18)
        self.assertEqual(ATR_MULTIPLIER, 3.0)
        self.assertEqual(ATR_TRAIL_MULT, 3.0)
        self.assertFalse(STANDALONE_KIJUN_EXIT_ENABLED)

    def test_c2_derived_prices(self):
        # Entry $100
        self.assertEqual(derive_eod_stop_price(100.0), 93.0)
        self.assertEqual(derive_emergency_stop_price(100.0), 90.0)
        self.assertEqual(derive_stop_price(100.0), 93.0)
        self.assertEqual(derive_target_price(100.0), 118.0)

        # Entry $250
        self.assertEqual(derive_eod_stop_price(250.0), 232.50)
        self.assertEqual(derive_emergency_stop_price(250.0), 225.0)
        self.assertEqual(derive_target_price(250.0), 295.0)


class TestC2DualClockStopMechanics(unittest.TestCase):
    """2. Dual-Clock Stop Loss Mechanics: Intraday Emergency (-10%) vs EOD Hard Stop (-7%)."""

    def test_intraday_dip_between_minus_7_and_minus_10_holds_outside_eod(self):
        """Intraday dip of -8.0% outside EOD window must NOT stop out."""
        res = evaluate_guardian_exit(
            buy_price=100.0,
            current_price=92.0,  # -8.0%
            is_eod_window=False,
        )
        self.assertIsNone(res["action"], "Intraday -8% dip outside EOD window must hold")
        self.assertEqual(res["hard_stop_price"], 93.0)
        self.assertEqual(res["emergency_stop_price"], 90.0)

    def test_intraday_emergency_stop_triggers_at_minus_10(self):
        """Intraday dip of -10.5% outside EOD window MUST trigger emergency stop."""
        res = evaluate_guardian_exit(
            buy_price=100.0,
            current_price=89.5,  # -10.5%
            is_eod_window=False,
        )
        self.assertEqual(res["action"], "AUTO_EMERGENCY_STOP")
        self.assertTrue(res["is_full_exit"])
        self.assertIn("비상", res["reason"])

    def test_eod_hard_stop_triggers_at_minus_7_during_eod_window(self):
        """During EOD window, a -7.5% close MUST trigger EOD hard stop."""
        res = evaluate_guardian_exit(
            buy_price=100.0,
            current_price=92.5,  # -7.5%
            is_eod_window=True,
        )
        self.assertEqual(res["action"], "AUTO_STOP_LOSS")
        self.assertTrue(res["is_full_exit"])
        self.assertIn("EOD", res["reason"])

    def test_eod_hard_stop_holds_at_minus_6_during_eod_window(self):
        """During EOD window, a -6.0% close must NOT trigger EOD hard stop."""
        res = evaluate_guardian_exit(
            buy_price=100.0,
            current_price=94.0,  # -6.0%
            is_eod_window=True,
        )
        self.assertIsNone(res["action"])

    def test_low_priced_asset_rounding_does_not_falsely_trigger_legacy_stop(self):
        """A $0.50 asset rounded to $0.47 stop has ratio 0.94. Without explicit stop_loss_price, it must NOT trigger legacy -5% stop outside EOD."""
        res = evaluate_guardian_exit(
            buy_price=0.50,
            current_price=0.48,  # -4.0% dip
            stop_loss_price=None,  # New C-2 lot without explicit legacy stop
            is_eod_window=False,
        )
        self.assertIsNone(res["action"], "Low priced asset dip of -4% outside EOD must NOT be stopped as legacy C1")


class TestC2MarketTimeAndEODWindows(unittest.TestCase):
    """3. Market Time & EOD Closing Window Detection."""

    def test_regular_session_eod_window(self):
        eastern = ZoneInfo("America/New_York")
        # Regular session: close is 16:00 ET. EOD window is 15:50 - 16:00 ET.
        t_before = datetime(2026, 4, 15, 15, 45, tzinfo=eastern)
        t_inside_start = datetime(2026, 4, 15, 15, 50, tzinfo=eastern)
        t_inside_mid = datetime(2026, 4, 15, 15, 55, tzinfo=eastern)
        t_after = datetime(2026, 4, 15, 16, 5, tzinfo=eastern)

        self.assertFalse(is_us_eod_window(t_before, window_minutes=10))
        self.assertTrue(is_us_eod_window(t_inside_start, window_minutes=10))
        self.assertTrue(is_us_eod_window(t_inside_mid, window_minutes=10))
        self.assertFalse(is_us_eod_window(t_after, window_minutes=10))

    def test_second_level_boundary_at_160000_and_160005(self):
        eastern = ZoneInfo("America/New_York")
        # 16:00:00 is regular session close (inside regular hours & EOD window)
        t_close_exact = datetime(2026, 4, 15, 16, 0, 0, tzinfo=eastern)
        self.assertTrue(is_us_regular_hours(t_close_exact))
        self.assertTrue(is_us_eod_window(t_close_exact, window_minutes=10))

        # 16:00:05 is post-close (outside regular session & outside EOD window)
        t_post_close = datetime(2026, 4, 15, 16, 0, 5, tzinfo=eastern)
        self.assertFalse(is_us_regular_hours(t_post_close))
        self.assertFalse(is_us_eod_window(t_post_close, window_minutes=10))

    def test_us_half_day_detection_and_early_close(self):
        eastern = ZoneInfo("America/New_York")
        # Christmas Eve (Dec 24) is a half day closing at 13:00 ET (780 min).
        xmas_eve = datetime(2026, 12, 24, 10, 0, tzinfo=eastern)
        self.assertTrue(is_us_half_day(xmas_eve))
        self.assertEqual(get_us_market_close_minute(xmas_eve), 780)

        # Half-day EOD window is 12:50 - 13:00 ET.
        t_before = datetime(2026, 12, 24, 12, 40, tzinfo=eastern)
        t_inside = datetime(2026, 12, 24, 12, 55, tzinfo=eastern)
        t_after = datetime(2026, 12, 24, 13, 5, tzinfo=eastern)

        self.assertFalse(is_us_eod_window(t_before, window_minutes=10))
        self.assertTrue(is_us_eod_window(t_inside, window_minutes=10))
        self.assertFalse(is_us_eod_window(t_after, window_minutes=10))

    def test_ticker_eod_window_routing(self):
        eastern = ZoneInfo("America/New_York")
        t_mid = datetime(2026, 4, 15, 15, 55, tzinfo=eastern)
        self.assertTrue(is_eod_window_for_ticker("NVDA", now=t_mid))
        self.assertTrue(is_eod_window_for_ticker("QQQ", now=t_mid))


class TestC2UncappedTrailingStop(unittest.TestCase):
    """4. Uncapped Trailing Stop (+18% activation, 3.0 ATR, disabled standalone Kijun)."""

    def test_activation_threshold_strictly_at_18_percent(self):
        # +17.0% gain does NOT activate trailing
        res_17 = evaluate_guardian_exit(
            buy_price=100.0,
            current_price=117.0,
            kijun_26=105.0,
            atr_14=2.0,
            peak_high=117.0,
        )
        self.assertFalse(res_17["trailing_active"])
        self.assertIsNone(res_17["action"])

        # +18.5% gain DOES activate trailing
        res_18 = evaluate_guardian_exit(
            buy_price=100.0,
            current_price=118.5,
            kijun_26=105.0,
            atr_14=2.0,
            peak_high=118.5,
        )
        self.assertTrue(res_18["trailing_active"])
        self.assertIsNone(res_18["action"])
        # Floor = max(105, 118.5 - 3.0 * 2.0) = max(105, 112.5) = 112.5
        self.assertEqual(res_18["trailing_floor"], 112.5)

    def test_standalone_kijun_exit_permanently_disabled_when_not_trailing(self):
        """When not armed for trailing (+18%), price dipping below Kijun must NOT trigger exit."""
        res = evaluate_guardian_exit(
            buy_price=100.0,
            current_price=102.0,
            kijun_26=104.0,  # price is below Kijun
            atr_14=2.0,
            peak_high=108.0,
        )
        self.assertFalse(res["trailing_active"])
        self.assertIsNone(res["action"], "Standalone Kijun breakdown must NOT trigger exit in C-2")

    def test_trailing_floor_uses_kijun_when_higher_than_atr_channel(self):
        # Peak=130, ATR=5 -> ATR leg = 130 - 3*5 = 115. Kijun=118 -> Floor = max(118, 115) = 118.
        floor = compute_trailing_floor(kijun_26=118.0, peak_high=130.0, atr_14=5.0)
        self.assertEqual(floor, 118.0)

        # If price drops to 117.0 (below floor 118.0), trigger AUTO_TRAILING_TP
        res = evaluate_guardian_exit(
            buy_price=100.0,
            current_price=117.0,
            kijun_26=118.0,
            atr_14=5.0,
            peak_high=130.0,
            max_gain_pct=30.0,
        )
        self.assertTrue(res["trailing_active"])
        self.assertEqual(res["action"], "AUTO_TRAILING_TP")
        self.assertEqual(res["trailing_floor"], 118.0)

    def test_legacy_vs_c2_atr_multiplier_and_activation_isolation(self):
        """Legacy lots use +15% / 2.5 ATR; C-2 lots use +18% / 3.0 ATR."""
        # Legacy lot with explicit stop_loss_price=95.0 (+16% gain -> armed at 15%)
        res_legacy = evaluate_guardian_exit(
            buy_price=100.0,
            current_price=116.0,
            stop_loss_price=95.0,
            kijun_26=100.0,
            atr_14=2.0,
            peak_high=116.0,
        )
        self.assertTrue(res_legacy["trailing_active"])
        # Legacy uses 2.5 ATR: 116.0 - 2.5 * 2.0 = 111.0
        self.assertEqual(res_legacy["trailing_floor"], 111.0)

        # C-2 lot with +16% gain: NOT active (requires +18%)
        res_c2_16 = evaluate_guardian_exit(
            buy_price=100.0,
            current_price=116.0,
            stop_loss_price=None,
            kijun_26=100.0,
            atr_14=2.0,
            peak_high=116.0,
        )
        self.assertFalse(res_c2_16["trailing_active"])

        # C-2 lot with +18.5% gain: active, uses 3.0 ATR (118.5 - 3.0 * 2.0 = 112.5)
        res_c2_18 = evaluate_guardian_exit(
            buy_price=100.0,
            current_price=118.5,
            stop_loss_price=None,
            kijun_26=100.0,
            atr_14=2.0,
            peak_high=118.5,
        )
        self.assertTrue(res_c2_18["trailing_active"])
        self.assertEqual(res_c2_18["trailing_floor"], 112.5)


class TestC2AutopilotEntryGates(unittest.TestCase):
    """5. Autopilot Dual Gates: Trend Gate (QQQ >= 20MA) & Alpha Gate (Stock RS >= QQQ RS)."""

    def test_trend_gate_blocks_entry_when_qqq_below_20ma(self):
        """Trend Gate: QQQ close < 20MA blocks new entries."""
        cand = {"ticker": "NVDA", "price": 100.0, "composite_rs": 85.0, "conviction_score": 75.0}
        gate = evaluate_entry_gate(
            candidate=cand,
            qqq_close=480.0,
            qqq_sma20=490.0,  # QQQ below 20MA
            qqq_composite_rs=70.0,
        )
        self.assertFalse(gate["eligible"])
        self.assertEqual(gate["reason"], "QQQ_BELOW_20MA")

    def test_alpha_gate_blocks_entry_when_stock_rs_below_qqq_rs(self):
        """Alpha Gate: Stock RS < QQQ RS blocks entry even if QQQ >= 20MA."""
        cand = {"ticker": "INTC", "price": 100.0, "composite_rs": 60.0, "conviction_score": 75.0}
        gate = evaluate_entry_gate(
            candidate=cand,
            qqq_close=500.0,
            qqq_sma20=490.0,  # QQQ above 20MA
            qqq_composite_rs=75.0,  # Stock RS (60) < QQQ RS (75)
        )
        self.assertFalse(gate["eligible"])
        self.assertEqual(gate["reason"], "RS_BELOW_QQQ")

    def test_dual_gates_pass_when_both_criteria_met(self):
        """Both Trend Gate and Alpha Gate passed."""
        cand = {"ticker": "NVDA", "price": 120.0, "composite_rs": 85.0, "conviction_score": 75.0, "kijun": 110.0}
        gate = evaluate_entry_gate(
            candidate=cand,
            qqq_close=500.0,
            qqq_sma20=490.0,
            qqq_composite_rs=75.0,
        )
        self.assertTrue(gate["eligible"])
        self.assertEqual(gate["reason"], "PASS")

    def test_entry_gate_direct_boolean_overrides(self):
        """Verify candidate with beats_qqq_crs=False or qqq_trend_gate_pass=False is rejected."""
        cand_alpha_fail = {"ticker": "XYZ", "price": 50.0, "beats_qqq_crs": False, "qqq_trend_gate_pass": True}
        gate1 = evaluate_entry_gate(candidate=cand_alpha_fail, qqq_close=500.0, qqq_sma20=490.0, qqq_composite_rs=70.0)
        self.assertFalse(gate1["eligible"])
        self.assertEqual(gate1["reason"], "RS_BELOW_QQQ")

        cand_trend_fail = {"ticker": "XYZ", "price": 50.0, "beats_qqq_crs": True, "qqq_trend_gate_pass": False}
        gate2 = evaluate_entry_gate(candidate=cand_trend_fail, qqq_close=500.0, qqq_sma20=490.0, qqq_composite_rs=70.0)
        self.assertFalse(gate2["eligible"])
        self.assertEqual(gate2["reason"], "QQQ_BELOW_20MA")


class TestC2OrderGuardrailLimits(unittest.TestCase):
    """6. Order Guardrail 0.39 / 0.38 / 0.38 Caps & Cash Proxy Overlay."""

    def test_bull_regime_caps(self):
        self.assertEqual(resolve_guardrail_max_allocation_pct("NVDA", slot_rank=1, is_bull=True), 0.39)
        self.assertEqual(resolve_guardrail_max_allocation_pct("AAPL", slot_rank=2, is_bull=True), 0.38)
        self.assertEqual(resolve_guardrail_max_allocation_pct("MSFT", slot_rank=3, is_bull=True), 0.38)
        self.assertEqual(resolve_guardrail_max_allocation_pct("GOOGL", slot_rank=4, is_bull=True), 0.38)

    def test_cash_proxy_sizing_limits(self):
        # 1.0x Core QQQ
        self.assertEqual(resolve_guardrail_max_allocation_pct("QQQ", leverage_mode=False), 1.00)
        # 1.5x Leveraged QLD or QQQ
        self.assertEqual(resolve_guardrail_max_allocation_pct("QLD", leverage_mode=False), 1.50)
        self.assertEqual(resolve_guardrail_max_allocation_pct("QQQ", leverage_mode=True), 1.50)


class TestC2BackwardCompatibility(unittest.TestCase):
    """7. Backward Compatibility: Legacy C1 lots retain ticket stops and behavior."""

    def test_legacy_c1_lot_preserves_ticket_stop_and_kijun_exit(self):
        """Existing lots created with -5% ticket stop retain -5% stop and Kijun exit."""
        res = evaluate_guardian_exit(
            buy_price=100.0,
            current_price=94.5,  # -5.5% (below 95.0 ticket stop)
            stop_loss_price=95.0,
            is_eod_window=False,
        )
        self.assertEqual(res["action"], "AUTO_STOP_LOSS")
        self.assertIn("레거시", res["reason"])

    def test_guardian_persist_mark_does_not_overwrite_existing_ticket_stop(self):
        """_persist_mark preserves existing stop_loss_price for open positions."""
        from al_sangmoo.domain.risk.portfolio_guardian import PortfolioGuardian
        g = PortfolioGuardian()

        with patch("al_sangmoo.domain.risk.portfolio_guardian.get_connection") as mock_conn:
            cursor = MagicMock()
            mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

            # Case A: Existing ticket stop 95.0 is preserved (not overwritten to 93.0)
            decision = {"pnl_pct": -2.0, "hard_stop_price": 93.0}
            g._persist_mark(
                holding_id=1,
                cur_price=98.0,
                total_qty=10.0,
                buy_price=100.0,
                decision=decision,
                existing_stop_price=95.0,
            )
            # Inspect UPDATE arguments: stop_loss_price is 5th parameter in the UPDATE query
            args = cursor.execute.call_args[0][1]
            self.assertEqual(args[4], 95.0, "Existing ticket stop must NOT be overwritten")

            # Case B: New position (existing_stop_price None) gets C-2 derived stop 93.0
            g._persist_mark(
                holding_id=2,
                cur_price=100.0,
                total_qty=10.0,
                buy_price=100.0,
                decision={"pnl_pct": 0.0, "hard_stop_price": 93.0},
                existing_stop_price=None,
            )
            args_new = cursor.execute.call_args[0][1]
            self.assertEqual(args_new[4], 93.0, "New position must receive C-2 stop price")


if __name__ == "__main__":
    unittest.main()
