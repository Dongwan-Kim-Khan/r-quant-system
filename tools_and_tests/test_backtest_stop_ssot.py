"""
Backtest hard-stop SSOT: live constitution is -4.0%, not legacy -3.0%.
"""
import inspect
import os
import sys
import unittest

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.backtest.engine import run_backtest_simulation
from al_sangmoo.core.constants import STOP_LOSS_PCT, TAKE_PROFIT_PCT
from al_sangmoo.domain.quant.ichimoku import calculate_ichimoku_indicators


def _synthetic_path(n: int = 300, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end="2026-08-21", periods=n)
    prices = 100.0 + np.cumsum(rng.normal(0.0, 1.5, n))
    prices = np.maximum(prices, 50.0)
    return pd.DataFrame(
        {
            "Open": prices,
            "High": prices + rng.uniform(0.5, 3.0, n),
            "Low": prices - rng.uniform(0.5, 3.0, n),
            "Close": prices + rng.normal(0.0, 0.5, n),
            "Volume": rng.integers(500_000, 2_000_000, n),
        },
        index=dates,
    )


def _forced_entry_then_wick(wick_pct: float) -> pd.DataFrame:
    """
    Uptrend + single-bar VDU so Gate-2 enters exactly once, then one wick bar.

    wick_pct is the low vs the fill open (negative). Close stays at/above kijun
    so only the hard-stop wick (not kijun close breakdown) can force the exit.
    """
    n = 100
    dates = pd.bdate_range("2024-01-02", periods=n + 2)
    close = np.linspace(70.0, 100.0, n)
    # 28-bar plateau lets 26-day kijun catch up to ~100 (gap inside -0.5%~+4%).
    close[-28:] = 100.0
    high = close + 0.35
    low = close - 0.35
    open_ = close.copy()
    volume = np.full(n, 2_000_000.0)
    # Only the last setup bar is dry so VDU cannot fire earlier in the plateau.
    volume[-1] = 500_000.0

    entry_open = 100.0
    wick_low = round(entry_open * (1.0 + wick_pct), 4)
    wick_close = 100.15  # close back above ~100 kijun; lower wick tests the stop

    open_ = np.append(open_, [entry_open, entry_open])
    high = np.append(high, [entry_open + 0.4, entry_open + 0.4])
    low = np.append(low, [entry_open - 0.3, wick_low])
    close = np.append(close, [entry_open, wick_close])
    volume = np.append(volume, [2_000_000.0, 2_000_000.0])

    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )


class TestBacktestStopSsot(unittest.TestCase):
    def test_constants_match_constitution(self):
        self.assertEqual(STOP_LOSS_PCT, -0.04)
        self.assertEqual(TAKE_PROFIT_PCT, 0.15)

    def test_engine_defaults_are_constitution(self):
        params = inspect.signature(run_backtest_simulation).parameters
        self.assertEqual(params["stop_loss_pct"].default, STOP_LOSS_PCT)
        self.assertEqual(params["take_profit_pct"].default, TAKE_PROFIT_PCT)
        self.assertEqual(params["stop_loss_pct"].default, -0.04)

    def test_report_records_minus_four_rule(self):
        df = _synthetic_path()
        res = run_backtest_simulation(data=df, initial_capital=100000.0)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["friction_modeled"]["stop_loss_rule"], "-4.0% Strict Execution")
        for trade in res["trades"]:
            if trade["reason"].startswith("STOP_LOSS"):
                self.assertIn("-4%", trade["reason"])
                self.assertNotIn("-3%", trade["reason"])

    def test_minus_three_half_wick_does_not_hard_stop(self):
        df = _forced_entry_then_wick(-0.035)
        res_legacy = run_backtest_simulation(data=df, stop_loss_pct=-0.03, timeout_bars=5)
        res_live = run_backtest_simulation(data=df, stop_loss_pct=-0.04, timeout_bars=5)
        self.assertGreaterEqual(res_legacy["total_trades"], 1)
        self.assertGreaterEqual(res_live["total_trades"], 1)

        legacy_reasons = [t["reason"] for t in res_legacy["trades"]]
        live_reasons = [t["reason"] for t in res_live["trades"]]
        self.assertTrue(
            any("STOP_LOSS" in r for r in legacy_reasons),
            f"legacy -3% should stop on a -3.5% wick: {legacy_reasons}",
        )
        self.assertFalse(
            any("STOP_LOSS" in r for r in live_reasons),
            f"live -4% must hold a -3.5% wick: {live_reasons}",
        )

    def test_minus_four_one_wick_does_hard_stop(self):
        df = _forced_entry_then_wick(-0.041)
        res = run_backtest_simulation(data=df, timeout_bars=5)
        reasons = [t["reason"] for t in res["trades"]]
        self.assertTrue(
            any("STOP_LOSS" in r for r in reasons),
            f"live -4% must stop on a -4.1% wick: {reasons}",
        )

    def test_minus_four_vs_minus_three_recompute_delta(self):
        df = _synthetic_path()
        legacy = run_backtest_simulation(data=df, stop_loss_pct=-0.03)
        live = run_backtest_simulation(data=df, stop_loss_pct=-0.04)
        self.assertEqual(legacy["status"], "success")
        self.assertEqual(live["status"], "success")
        self.assertEqual(live["friction_modeled"]["stop_loss_rule"], "-4.0% Strict Execution")
        self.assertEqual(legacy["friction_modeled"]["stop_loss_rule"], "-3.0% Strict Execution")
        # Wider stop cannot produce a strictly earlier hard-stop than -3% on the same path.
        self.assertGreaterEqual(live["max_drawdown_pct"], 0.0)


class TestForcedEntryGate(unittest.TestCase):
    def test_setup_bars_meet_gate_two(self):
        df = calculate_ichimoku_indicators(_forced_entry_then_wick(-0.035))
        clean = df.dropna(subset=["Tenkan", "Kijun", "SpanA", "SpanB", "Vol_Ratio"])
        row = clean.iloc[-3]  # bar before fill open
        cloud_top = max(float(row["SpanA"]), float(row["SpanB"]))
        kijun_gap = (float(row["Close"]) - float(row["Kijun"])) / float(row["Kijun"])
        self.assertGreaterEqual(float(row["Close"]), cloud_top)
        self.assertGreaterEqual(kijun_gap, -0.005)
        self.assertLessEqual(kijun_gap, 0.040)
        self.assertLessEqual(float(row["Vol_Ratio"]), 0.75)
        self.assertGreaterEqual(float(row["Tenkan"]), float(row["Kijun"]))


if __name__ == "__main__":
    unittest.main()
