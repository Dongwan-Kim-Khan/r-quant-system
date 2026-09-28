"""
Regression Test Suite for Portfolio Equity, Cash, and Initial Capital (INIT) SSOT.
Ensures:
1. Baseline initial capital (INIT) is DEFAULT_BASE_ACCOUNT_USD ($7,500.00).
2. Broker snapshot NAV is $7,592.39 (stocks $7,282.66 + free cash $309.73).
3. Primary MTS holding return is +9.86% (+$653.47).
4. Dashboard cash/NAV follow the broker snapshot, not the $7,500 sleeve ledger.
5. Fragile SQLite triggers are permanently retired.
"""

import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.core.constants import DEFAULT_BASE_ACCOUNT_USD
from al_sangmoo.infrastructure.persistence import (
    get_connection,
    init_database,
    save_account_snapshot,
    load_account_snapshot,
    get_live_portfolio,
)
from al_sangmoo.infrastructure.brokers.kis_broker import KISBrokerAdapter
from al_sangmoo.domain.reconciliation import _persist_broker_account_snapshot


class TestPortfolioEquityAndInitSSOT(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._prev = os.environ.get("AL_SANGMOO_DB_PATH")
        os.environ["AL_SANGMOO_DB_PATH"] = self._tmp.name
        init_database()

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("AL_SANGMOO_DB_PATH", None)
        else:
            os.environ["AL_SANGMOO_DB_PATH"] = self._prev
        for suffix in ("", "-wal", "-shm"):
            path = self._tmp.name + suffix
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def test_portfolio_baseline_init_and_equity_ssot(self):
        """
        Verify that:
        1. When holdings: AMD (4 @ 614.61 = $2458.44), DELL (4 @ 549.83 = $2199.32), CRWD (10 @ 262.49 = $2624.90)
           Total stock eval = $7282.66, total invested = $6629.19.
        2. Snapshot has net cash $309.73 and total equity $7592.39.
        3. INIT is equity minus KIS official total PnL (not the $7,500 sleeve).
        4. MTS holding return is +9.86% (+$653.47).
        5. Dashboard RETURN follows KIS official +11.06% / +$722.74.
        """
        with get_connection() as conn:
            cursor = conn.cursor()
            # 1. Holdings
            cursor.execute("""
            INSERT INTO my_portfolio (ticker, buy_date, buy_price, quantity, current_price, total_cost, current_value, pnl_pct, pnl_amount, status)
            VALUES 
            ('DELL', '2026-09-11', 520.775, 4.0, 549.83, 2083.10, 2199.32, 5.58, 116.22, 'HOLDING'),
            ('AMD', '2026-09-11', 508.648, 4.0, 614.61, 2034.59, 2458.44, 20.83, 423.85, 'HOLDING'),
            ('CRWD', '2026-09-23', 251.15, 10.0, 262.49, 2511.50, 2624.90, 4.52, 113.40, 'HOLDING')
            """)
            # 2. Trade history (realized PnL = -18.32)
            cursor.execute("""
            INSERT INTO trade_history (ticker, buy_date, sell_date, buy_price, sell_price, quantity, pnl_pct, pnl_amount)
            VALUES ('QLD', '2026-09-17', '2026-09-24', 94.265, 95.52, 15.0, 1.33, -18.32)
            """)
            conn.commit()

        # Reconciled snapshot
        save_account_snapshot({
            "total_equity_usd": 7592.39,
            "cash_available_usd": 309.73,
            "stock_eval_usd": 7282.66,
            "realized_pnl_usd": -18.32,
            "unrealized_pnl_usd": 653.47,
            "total_pnl_usd": 722.74,
            "total_pnl_pct": 11.06,
            "source": "BROKER",
            "mode": "VIRTUAL_PAPER",
        })

        port = get_live_portfolio()

        self.assertAlmostEqual(port["total_eval"], 7282.66, places=2)
        self.assertEqual(port.get("equity_source"), "BROKER")
        self.assertAlmostEqual(port["free_cash_usd"], 309.73, places=2)
        self.assertAlmostEqual(port["total_equity_usd"], 7592.39, places=2)
        self.assertAlmostEqual(port["base_account_usd"], 6869.65, places=2)
        self.assertAlmostEqual(port["initial_capital_usd"], 6869.65, places=2)
        self.assertAlmostEqual(port["unrealized_pnl_pct"], 9.86, places=2)
        self.assertAlmostEqual(port["unrealized_pnl_amount"], 653.47, places=2)
        self.assertAlmostEqual(port["realized_pnl_amount"], -18.32, places=2)
        self.assertAlmostEqual(port["overall_pnl_amount"], 722.74, places=2)
        self.assertAlmostEqual(port["overall_pnl_pct"], 11.06, places=2)

    def test_reconciliation_persist_stores_ssot_snapshot(self):
        """Verify _persist_broker_account_snapshot stores reconciled NAV, cash and eval cleanly."""
        _persist_broker_account_snapshot({
            "mode": "VIRTUAL_PAPER",
            "total_equity_usd": 7592.39,
            "cash_available_usd": 309.73,
            "stock_eval_usd": 7282.66,
            "total_pnl_usd": 722.74,
            "total_pnl_pct": 11.06,
            "holdings": [
                {"ticker": "DELL", "quantity": 4, "avg_price": 520.775, "current_price": 549.83},
                {"ticker": "AMD", "quantity": 4, "avg_price": 508.648, "current_price": 614.61},
                {"ticker": "CRWD", "quantity": 10, "avg_price": 251.15, "current_price": 262.49},
            ],
        })

        snap = load_account_snapshot()
        self.assertIsNotNone(snap)
        self.assertAlmostEqual(float(snap["cash_available_usd"]), 309.73, places=2)
        self.assertAlmostEqual(float(snap["total_equity_usd"]), 7592.39, places=2)
        self.assertAlmostEqual(float(snap["stock_eval_usd"]), 7282.66, places=2)
        self.assertAlmostEqual(float(snap.get("total_pnl_usd") or 0), 722.74, places=2)
        self.assertAlmostEqual(float(snap.get("total_pnl_pct") or 0), 11.06, places=2)

    def test_kis_broker_balance_crwd_offset(self):
        """Verify KISBrokerAdapter.get_overseas_balance deducts unsettled paper buy cost from sll_ruse_psbl_amt."""
        broker = KISBrokerAdapter()
        broker.is_paper = True
        broker.account_no = "50203100"
        broker.account_code = "01"

        mock_balance_response = MagicMock()
        mock_balance_response.status_code = 200
        mock_balance_response.json.return_value = {
            "rt_cd": "0",
            "output1": [
                {"ovrs_pdno": "DELL", "ovrs_cblc_qty": "4", "pchs_avg_pric": "520.775", "now_pric2": "549.83"},
                {"ovrs_pdno": "AMD", "ovrs_cblc_qty": "4", "pchs_avg_pric": "508.648", "now_pric2": "614.61"},
                {"ovrs_pdno": "CRWD", "ovrs_cblc_qty": "10", "pchs_avg_pric": "251.15", "now_pric2": "262.49"},
            ],
            "output2": {
                "tot_evlu_amt": "7282.66",
                "evlu_amt_smtl_amt": "7282.66",
                "frcr_dncl_amt_2": "0.0",
            }
        }

        broker.token = "MOCK_TOKEN"
        broker.token_expiry = time.time() + 3600

        with patch("requests.get", return_value=mock_balance_response), \
             patch.object(broker, "get_purchasable_amount", return_value={"status": "success", "sll_ruse_psbl_amt": 2821.23}):

            res = broker.get_overseas_balance()

            self.assertEqual(res["status"], "success")
            self.assertAlmostEqual(res["cash_available_usd"], 309.73, places=2)
            self.assertAlmostEqual(res["stock_eval_usd"], 7282.66, places=2)
            self.assertAlmostEqual(res["total_equity_usd"], 7592.39, places=2)

    def test_idempotent_no_double_deduction(self):
        """When cash is already net cash ($309.73), dashboard NAV follows the broker snapshot."""
        save_account_snapshot({
            "total_equity_usd": 7592.39,
            "cash_available_usd": 309.73,
            "stock_eval_usd": 7282.66,
            "realized_pnl_usd": -18.32,
            "unrealized_pnl_usd": 653.47,
            "total_pnl_usd": 722.74,
            "total_pnl_pct": 11.06,
            "source": "BROKER",
            "mode": "VIRTUAL_PAPER",
        })

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO my_portfolio (ticker, buy_date, buy_price, quantity, current_price, total_cost, current_value, pnl_pct, pnl_amount, status)
            VALUES 
            ('DELL', '2026-09-11', 520.775, 4.0, 549.83, 2083.10, 2199.32, 5.58, 116.22, 'HOLDING'),
            ('AMD', '2026-09-11', 508.648, 4.0, 614.61, 2034.59, 2458.44, 20.83, 423.85, 'HOLDING'),
            ('CRWD', '2026-09-23', 251.15, 10.0, 262.49, 2511.50, 2624.90, 4.52, 113.40, 'HOLDING')
            """)
            cursor.execute("""
            INSERT INTO trade_history (ticker, buy_date, sell_date, buy_price, sell_price, quantity, pnl_pct, pnl_amount)
            VALUES ('QLD', '2026-09-17', '2026-09-24', 94.265, 95.52, 15.0, 1.33, -18.32)
            """)
            conn.commit()

        port = get_live_portfolio()
        self.assertEqual(port.get("equity_source"), "BROKER")
        self.assertAlmostEqual(port["free_cash_usd"], 309.73, places=2)
        self.assertAlmostEqual(port["total_equity_usd"], 7592.39, places=2)
        self.assertAlmostEqual(port["base_account_usd"], 6869.65, places=2)
        self.assertAlmostEqual(port["initial_capital_usd"], 6869.65, places=2)
        self.assertAlmostEqual(port["unrealized_pnl_pct"], 9.86, places=2)
        self.assertAlmostEqual(port["overall_pnl_amount"], 722.74, places=2)
        self.assertAlmostEqual(port["overall_pnl_pct"], 11.06, places=2)

    def test_triggers_permanently_retired(self):
        """Verify that SQLite triggers are permanently dropped and not present in DB schema."""
        from tools_and_tests.setup_snapshot_guard_trigger import setup_triggers
        setup_triggers(self._tmp.name)
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            triggers = cursor.fetchall()
            trigger_names = [t[0] for t in triggers]
            self.assertNotIn("trg_fix_account_snapshot_update", trigger_names)
            self.assertNotIn("trg_fix_account_snapshot_insert", trigger_names)

        snap = load_account_snapshot()
        self.assertIsNotNone(snap)
        self.assertAlmostEqual(float(snap["cash_available_usd"]), 309.73, places=2)
        self.assertAlmostEqual(float(snap["total_equity_usd"]), 7592.39, places=2)


if __name__ == "__main__":
    unittest.main()
