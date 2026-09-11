"""Idempotent KIS order: client_order_id + no duplicate POST on timeout."""
import os
import sys
import time
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Isolate ALL persistence writes from production quant_trades.db before broker import.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="kis_idempotent_")
os.environ["AL_SANGMOO_DB_PATH"] = os.path.join(_TEST_DB_DIR, "quant_trades.db")

from al_sangmoo.infrastructure.idempotent_order import (
    is_broker_order_ack,
    make_client_order_id,
    match_day_order,
    normalize_kis_day_order,
)
from al_sangmoo.infrastructure.brokers.kis_broker import KISBrokerAdapter


def _adapter() -> KISBrokerAdapter:
    a = KISBrokerAdapter(app_key="key", app_secret="secret", account_no="12345678", mode="vps")
    a.token = "tok"
    a.token_expiry = time.time() + 86_400
    a._limiter.acquire = lambda *args, **kwargs: None
    return a


class TestMatchHelpers(unittest.TestCase):
    def test_client_order_id_length(self):
        oid = make_client_order_id()
        self.assertEqual(len(oid), 12)
        self.assertEqual(oid, oid.upper())

    def test_normalize_kis_row(self):
        row = normalize_kis_day_order({
            "pdno": "nvda",
            "sll_buy_dvsn_cd": "02",
            "ft_ord_qty": "10",
            "ft_ord_unpr3": "100.50",
            "odno": "OD99",
            "mgco_aptm_odno": "abc123abc123",
        })
        self.assertEqual(row["ticker"], "NVDA")
        self.assertEqual(row["side"], "BUY")
        self.assertEqual(row["qty"], 10.0)
        self.assertEqual(row["order_id"], "OD99")

    def test_match_by_client_id_and_by_qty(self):
        book = [{
            "ticker": "NVDA", "side": "BUY", "qty": 10, "price": 100.0,
            "order_id": "OD1", "client_order_id": "CIDCIDCIDCID",
        }]
        hit = match_day_order(book, "NVDA", "BUY", 10, 100.0, client_order_id="CIDCIDCIDCID")
        self.assertEqual(hit["order_id"], "OD1")
        hit2 = match_day_order(book, "NVDA", "BUY", 10, 101.0, client_order_id=None)
        self.assertEqual(hit2["order_id"], "OD1")
        miss = match_day_order(book, "NVDA", "BUY", 99, 100.0)
        self.assertIsNone(miss)

    def test_ack_helper(self):
        self.assertTrue(is_broker_order_ack("submitted"))
        self.assertTrue(is_broker_order_ack("SUCCESS_VIA_RECHECK"))
        self.assertFalse(is_broker_order_ack("ORDER_TIMEOUT_UNCONFIRMED"))


class TestPlaceOrderIdempotency(unittest.TestCase):
    def setUp(self):
        self._exec_log_patch = patch("al_sangmoo.infrastructure.persistence.record_execution_log")
        self._exec_log_patch.start()
        self.addCleanup(self._exec_log_patch.stop)

    def test_timeout_rechecks_day_book_without_second_post(self):
        adapter = _adapter()
        with patch.object(adapter, "authenticate", return_value=True), \
             patch("al_sangmoo.infrastructure.brokers.kis_broker.requests.post", side_effect=requests.Timeout("504")) as mock_post, \
             patch.object(adapter, "query_overseas_day_orders", return_value=[{
                 "ticker": "NVDA", "side": "BUY", "qty": 10, "price": 100.0, "order_id": "OD-FOUND",
             }]):
            res = adapter.place_order("NVDA", "BUY", 10, 100.0)
        self.assertEqual(res["status"], "SUCCESS_VIA_RECHECK")
        self.assertEqual(res["order_id"], "OD-FOUND")
        self.assertEqual(mock_post.call_count, 1)
        self.assertTrue(is_broker_order_ack(res["status"]))
        body = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        self.assertEqual(len(body["MGCO_APTM_ODNO"]), 12)

    def test_timeout_without_book_match_does_not_repost(self):
        adapter = _adapter()
        with patch.object(adapter, "authenticate", return_value=True), \
             patch("al_sangmoo.infrastructure.brokers.kis_broker.requests.post", side_effect=requests.Timeout("504")) as mock_post, \
             patch.object(adapter, "query_overseas_day_orders", return_value=[]):
            first = adapter.place_order("NVDA", "BUY", 10, 100.0)
            second = adapter.place_order("NVDA", "BUY", 10, 100.0)
        self.assertEqual(first["reason"], "ORDER_TIMEOUT_UNCONFIRMED")
        self.assertEqual(second["reason"], "ORDER_TIMEOUT_UNCONFIRMED")
        self.assertEqual(mock_post.call_count, 1)

    def test_rate_limit_rejection_may_retry_post(self):
        adapter = _adapter()
        limited = MagicMock(status_code=200)
        limited.json.return_value = {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수"}
        ok = MagicMock(status_code=200)
        ok.json.return_value = {"rt_cd": "0", "msg1": "ok", "output": {"ODNO": "OD-OK"}}
        with patch.object(adapter, "authenticate", return_value=True), \
             patch("al_sangmoo.infrastructure.brokers.kis_broker.requests.post", side_effect=[limited, ok]) as mock_post, \
             patch("al_sangmoo.infrastructure.brokers.kis_broker.time.sleep"):
            res = adapter.place_order("AAPL", "BUY", 1, 200.0)
        self.assertEqual(res["status"], "submitted")
        self.assertEqual(res["order_id"], "OD-OK")
        self.assertEqual(mock_post.call_count, 2)

    def test_inflight_duplicate_rejected(self):
        adapter = _adapter()
        started = threading.Event()
        release = threading.Event()

        def slow_post(*args, **kwargs):
            started.set()
            release.wait(timeout=2)
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"rt_cd": "0", "msg1": "ok", "output": {"ODNO": "OD-SLOW"}}
            return resp

        results = []

        def first():
            with patch.object(adapter, "authenticate", return_value=True), \
                 patch("al_sangmoo.infrastructure.brokers.kis_broker.requests.post", side_effect=slow_post):
                results.append(adapter.place_order("MSFT", "BUY", 2, 400.0))

        t = threading.Thread(target=first)
        t.start()
        self.assertTrue(started.wait(timeout=2))
        with patch.object(adapter, "authenticate", return_value=True):
            dup = adapter.place_order("MSFT", "BUY", 2, 400.0)
        release.set()
        t.join(timeout=3)
        self.assertEqual(dup["reason"], "INFLIGHT_DUPLICATE")
        self.assertEqual(results[0]["status"], "submitted")

    def test_order_state_distinguishes_partial_and_terminal_fill(self):
        adapter = _adapter()
        partial_row = {
            "pdno": "QQQ",
            "sll_buy_dvsn_cd": "01",
            "ft_ord_qty": "4",
            "ft_ccld_qty": "2",
            "nccs_qty": "2",
            "ft_ccld_unpr3": "99.25",
            "odno": "SELL-1",
        }
        with patch.object(
            adapter,
            "query_overseas_day_orders",
            return_value=[partial_row],
        ):
            partial = adapter.get_overseas_order_state(
                "QQQ", order_id="SELL-1"
            )
        self.assertEqual(partial["status"], "PARTIALLY_FILLED")
        self.assertFalse(partial["terminal"])
        self.assertEqual(partial["filled_quantity"], 2.0)

        filled_row = dict(
            partial_row,
            ft_ccld_qty="4",
            nccs_qty="0",
        )
        with patch.object(
            adapter,
            "query_overseas_day_orders",
            return_value=[filled_row],
        ):
            filled = adapter.get_overseas_order_state(
                "QQQ", order_id="SELL-1"
            )
        self.assertEqual(filled["status"], "FILLED")
        self.assertTrue(filled["terminal"])
        self.assertEqual(filled["fill_price"], 99.25)


class TestBalanceSnapshotSafety(unittest.TestCase):
    @staticmethod
    def _success_response(holdings=None):
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "rt_cd": "0",
            "msg1": "ok",
            "output1": holdings or [],
            "output2": {
                "tot_evlu_amt": "10000",
                "evlu_amt_smtl_amt": "500",
                "frcr_dncl_amt_2": "9500",
            },
        }
        return response

    def test_actual_balance_quantity_wins_over_zero_orderable_quantity(self):
        adapter = _adapter()
        nasd = self._success_response([{
            "ovrs_pdno": "DELL",
            "ord_psbl_qty": "0",
            "ovrs_cblc_qty": "7",
            "pchs_avg_pric": "100",
            "now_pric2": "99",
        }])
        empty = self._success_response()
        with patch.object(adapter, "authenticate", return_value=True), patch(
            "al_sangmoo.infrastructure.brokers.kis_broker.requests.get",
            side_effect=[nasd, empty, empty],
        ):
            result = adapter.get_overseas_balance()

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["snapshot_complete"])
        self.assertEqual(result["holdings"][0]["quantity"], 7.0)

    def test_failed_exchange_marks_snapshot_partial_not_empty_success(self):
        adapter = _adapter()
        ok = self._success_response()

        def by_exchange(*_args, **kwargs):
            if kwargs["params"]["OVRS_EXCG_CD"] == "NASD":
                return ok
            raise requests.Timeout("exchange inquiry timeout")

        with patch.object(adapter, "authenticate", return_value=True), patch(
            "al_sangmoo.infrastructure.brokers.kis_broker.requests.get",
            side_effect=by_exchange,
        ), patch("al_sangmoo.infrastructure.brokers.kis_broker.time.sleep"):
            result = adapter.get_overseas_balance()

        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["snapshot_complete"])
        self.assertEqual(result["successful_exchanges"], ["NASD"])
        self.assertEqual(
            {item["exchange"] for item in result["failed_exchanges"]},
            {"NYSE", "AMEX"},
        )


if __name__ == "__main__":
    unittest.main()
