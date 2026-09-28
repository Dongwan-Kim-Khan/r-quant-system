import os
import sys
import unittest
from unittest.mock import ANY, MagicMock, patch

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import tempfile

_TEMP_DIR = tempfile.TemporaryDirectory()
TEST_DB = os.path.join(_TEMP_DIR.name, "test_quant_trades_router.db")
_PREV_DB_PATH = os.environ.get("AL_SANGMOO_DB_PATH")
os.environ["AL_SANGMOO_DB_PATH"] = TEST_DB


def tearDownModule():
    try:
        _TEMP_DIR.cleanup()
    except Exception:
        pass
    if _PREV_DB_PATH is None:
        os.environ.pop("AL_SANGMOO_DB_PATH", None)
    else:
        os.environ["AL_SANGMOO_DB_PATH"] = _PREV_DB_PATH


from al_sangmoo.domain.risk.exchange_router import (
    AMEX_EXCHANGE_TICKERS,
    NYSE_EXCHANGE_TICKERS,
    resolve_order_exchange,
)
from al_sangmoo.interfaces.api.routers.portfolio import BuyOrder, SellOrder, buy_stock, sell_stock


class TestExchangeRouter(unittest.TestCase):
    def test_amex_tickers(self):
        expected_amex = {"QLD", "SPY", "ITA", "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLV", "XLY"}
        self.assertEqual(AMEX_EXCHANGE_TICKERS, expected_amex)
        self.assertEqual(len(AMEX_EXCHANGE_TICKERS), 11)
        for ticker in expected_amex:
            self.assertEqual(resolve_order_exchange(ticker), "AMEX")
            self.assertEqual(resolve_order_exchange(ticker.lower()), "AMEX")
            self.assertEqual(resolve_order_exchange(f"  {ticker}  "), "AMEX")

    def test_nyse_tickers(self):
        expected_nyse = {
            "ABBV", "ABT", "ALB", "APD", "AXP", "BA", "BAC", "BAH", "BLK", "BMY",
            "BX", "C", "CAT", "CB", "CCJ", "CMG", "CMI", "COP", "CRM", "CTVA",
            "CVX", "DD", "DE", "DELL", "DHR", "DIS", "DOW", "DUK", "ECL", "EMR",
            "EOG", "ETN", "FCX", "FDX", "GD", "GE", "GEV", "GS", "GWW", "HD",
            "HEI", "HII", "HPQ", "HWM", "IBM", "IONQ", "ITW", "JNJ", "JPM", "KKR",
            "KO", "LHX", "LLY", "LMT", "LOW", "MA", "MCD", "MDT", "MO", "MPC",
            "MRK", "MS", "NEE", "NEM", "NET", "NKE", "NOC", "NOW", "OKLO", "ORCL",
            "OXY", "PFE", "PG", "PH", "PM", "PSX", "RBLX", "ROK", "RTX", "SCCO",
            "SCHW", "SHW", "SLB", "SNOW", "SO", "SPOT", "SRE", "SYK", "T", "TDG",
            "TGT", "TJX", "TMO", "TSM", "TT", "TXT", "UNH", "UNP", "UPS", "V",
            "VALE", "VLO", "VST", "VZ", "WFC", "XOM", "ZTS"
        }
        self.assertEqual(NYSE_EXCHANGE_TICKERS, expected_nyse)
        self.assertEqual(len(NYSE_EXCHANGE_TICKERS), 107)
        for ticker in expected_nyse:
            self.assertEqual(resolve_order_exchange(ticker), "NYSE")
            self.assertEqual(resolve_order_exchange(ticker.lower()), "NYSE")
            self.assertEqual(resolve_order_exchange(f"  {ticker}  "), "NYSE")

    def test_nasd_default(self):
        nasd_tickers = ["NVDA", "AAPL", "MSFT", "AMZN", "QQQ", "PLTR", "APP", "CEG", "GOOGL", "META", "TSLA"]
        for ticker in nasd_tickers:
            self.assertEqual(resolve_order_exchange(ticker), "NASD")
        self.assertEqual(resolve_order_exchange(""), "NASD")
        self.assertEqual(resolve_order_exchange(None), "NASD")
        self.assertEqual(resolve_order_exchange("UNKNOWN_TICKER"), "NASD")
        self.assertEqual(resolve_order_exchange(123), "NASD")
        self.assertEqual(resolve_order_exchange(0), "NASD")


class TestPortfolioApiExchangeResolution(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from al_sangmoo.domain.risk import exit_cooldown
        exit_cooldown.clear()

    @patch("al_sangmoo.interfaces.api.routers.portfolio.add_portfolio_buy", return_value=1)
    @patch("al_sangmoo.interfaces.api.routers.portfolio.default_kis_broker")
    @patch("al_sangmoo.interfaces.api.routers.portfolio.validate_pre_trade_guardrail")
    @patch("al_sangmoo.interfaces.api.routers.portfolio.get_live_portfolio")
    async def test_buy_order_exchange_resolution(self, mock_get_live, mock_validate, mock_broker, mock_add_buy):
        mock_get_live.return_value = {"total_equity_usd": 100000.0, "holdings": []}
        mock_validate.return_value = {"allowed": True}
        mock_broker.is_configured.return_value = True
        mock_broker.place_order.return_value = {"status": "submitted", "order_id": "ORD-1"}

        # 1. Omitted exchange for NYSE stock -> resolves to NYSE
        order_nyse = BuyOrder(ticker="CAT", buy_price=300.0, quantity=1.0)
        self.assertIsNone(order_nyse.exchange)
        res = await buy_stock(order_nyse)
        self.assertEqual(res["status"], "submitted")
        mock_broker.place_order.assert_called_with(
            ticker="CAT",
            side="BUY",
            qty=1,
            price=300.0,
            order_type="00",
            exchange="NYSE"
        )

        # 2. Omitted exchange for AMEX stock -> resolves to AMEX
        order_amex = BuyOrder(ticker="QLD", buy_price=95.0, quantity=10.0)
        res = await buy_stock(order_amex)
        mock_broker.place_order.assert_called_with(
            ticker="QLD",
            side="BUY",
            qty=10,
            price=95.0,
            order_type="00",
            exchange="AMEX"
        )

        # 3. Omitted exchange for NASD stock -> resolves to NASD
        order_nasd = BuyOrder(ticker="NVDA", buy_price=120.0, quantity=5.0)
        res = await buy_stock(order_nasd)
        mock_broker.place_order.assert_called_with(
            ticker="NVDA",
            side="BUY",
            qty=5,
            price=120.0,
            order_type="00",
            exchange="NASD"
        )

        # 4. Invalid exchange specified -> resolves to correct exchange
        order_invalid = BuyOrder(ticker="DELL", buy_price=130.0, quantity=2.0, exchange="INVALID_EX")
        res = await buy_stock(order_invalid)
        mock_broker.place_order.assert_called_with(
            ticker="DELL",
            side="BUY",
            qty=2,
            price=130.0,
            order_type="00",
            exchange="NYSE"
        )

        # 5. Explicit valid exchange -> respected
        order_explicit = BuyOrder(ticker="DELL", buy_price=130.0, quantity=2.0, exchange="NYSE")
        res = await buy_stock(order_explicit)
        mock_broker.place_order.assert_called_with(
            ticker="DELL",
            side="BUY",
            qty=2,
            price=130.0,
            order_type="00",
            exchange="NYSE"
        )

    @patch("al_sangmoo.interfaces.api.routers.portfolio.default_kis_broker")
    @patch("al_sangmoo.interfaces.api.routers.portfolio.record_portfolio_sell", return_value=True)
    @patch("al_sangmoo.interfaces.api.routers.portfolio.get_live_portfolio")
    async def test_sell_order_exchange_resolution(self, mock_get_live, mock_record_sell, mock_broker):
        mock_broker.is_configured.return_value = True
        mock_broker.place_order.return_value = {"status": "submitted", "order_id": "ORD-SELL-1"}

        # Holding with SSOT exchange in holding dict
        mock_get_live.return_value = {
            "holdings": [
                {"id": 1, "ticker": "XOM", "quantity": 10.0, "exchange": "NYSE"},
                {"id": 2, "ticker": "SPY", "quantity": 5.0, "ovrs_excg_cd": "AMEX"},
                {"id": 3, "ticker": "CAT", "quantity": 2.0},  # missing exchange in holding
            ]
        }

        # 1. Sell XOM using holding exchange "NYSE"
        order_xom = SellOrder(sell_price=110.0)
        res = await sell_stock(position_id=1, order=order_xom)
        mock_broker.place_order.assert_called_with(
            ticker="XOM",
            side="SELL",
            qty=10,
            price=110.0,
            order_type="00",
            exchange="NYSE"
        )

        # 2. Sell SPY using holding ovrs_excg_cd "AMEX"
        order_spy = SellOrder(sell_price=550.0)
        res = await sell_stock(position_id=2, order=order_spy)
        mock_broker.place_order.assert_called_with(
            ticker="SPY",
            side="SELL",
            qty=5,
            price=550.0,
            order_type="00",
            exchange="AMEX"
        )

        # 3. Sell CAT missing exchange in holding -> resolves via resolve_order_exchange to "NYSE"
        order_cat = SellOrder(sell_price=350.0)
        res = await sell_stock(position_id=3, order=order_cat)
        mock_broker.place_order.assert_called_with(
            ticker="CAT",
            side="SELL",
            qty=2,
            price=350.0,
            order_type="00",
            exchange="NYSE"
        )


class TestGuardianAndAutopilotExchangeResolution(unittest.TestCase):
    def setUp(self):
        from al_sangmoo.domain.risk import exit_cooldown
        exit_cooldown.clear()

    @patch("al_sangmoo.domain.risk.portfolio_guardian.fetch_trailing_snapshot", return_value={"kijun_26": 100.0, "atr_14": 2.0})
    @patch("al_sangmoo.domain.risk.portfolio_guardian.record_portfolio_sell", return_value=True)
    @patch("al_sangmoo.domain.risk.portfolio_guardian.default_kis_broker")
    @patch("al_sangmoo.domain.risk.portfolio_guardian.is_market_open_for_orders", return_value=True)
    @patch("al_sangmoo.domain.risk.portfolio_guardian.claim_satellite_order_intent", return_value=True)
    @patch("al_sangmoo.domain.risk.portfolio_guardian.get_live_portfolio")
    @patch("al_sangmoo.domain.risk.portfolio_guardian.evaluate_guardian_exit")
    def test_guardian_sell_exchange_resolution(
        self, mock_eval_exit, mock_get_live, mock_claim, mock_is_open, mock_broker, mock_rec_sell, mock_snap
    ):
        import time
        from al_sangmoo.domain.risk.portfolio_guardian import PortfolioGuardian

        mock_broker.is_configured.return_value = True
        mock_broker.place_order.return_value = {"status": "filled", "order_id": "TEST-G-1"}
        mock_eval_exit.return_value = {
            "action": "SELL_HARD_STOP",
            "reason": "HARD_STOP_HIT",
            "pnl_pct": -8.0,
            "target_price": 100.0,
            "stop_loss_price": 90.0,
            "partial_tp_price": 110.0,
            "exit_advice": "손절 청산",
        }

        guardian = PortfolioGuardian()
        guardian.last_sync_time = time.time()
        guardian._fetch_live_price = lambda ticker, fallback: fallback
        guardian._persist_mark = lambda *args, **kwargs: None
        guardian._align_cash_proxy_sleeve = lambda: []
        guardian._redeploy_exit_proceeds_to_cash_proxy = lambda *args, **kwargs: []
        guardian._repark_exit_proceeds = lambda *args, **kwargs: []

        # Case 1: Holding has SSOT exchange="NYSE"
        mock_get_live.return_value = {
            "holdings": [
                {"id": 101, "ticker": "XOM", "buy_price": 120.0, "current_price": 100.0, "quantity": 5.0, "exchange": "NYSE"}
            ]
        }
        guardian._sync_check_and_execute_guardian_rules()
        mock_broker.place_order.assert_any_call(
            ticker="XOM",
            side="SELL",
            qty=5,
            price=99.5,
            order_type="00",
            exchange="NYSE",
            client_order_id=unittest.mock.ANY,
        )

        # Case 2: Holding has ovrs_excg_cd="AMEX"
        mock_get_live.return_value = {
            "holdings": [
                {"id": 102, "ticker": "ITA", "buy_price": 150.0, "current_price": 130.0, "quantity": 10.0, "ovrs_excg_cd": "AMEX"}
            ]
        }
        guardian._sync_check_and_execute_guardian_rules()
        mock_broker.place_order.assert_any_call(
            ticker="ITA",
            side="SELL",
            qty=10,
            price=129.35,
            order_type="00",
            exchange="AMEX",
            client_order_id=unittest.mock.ANY,
        )

        # Case 3: Holding missing exchange -> resolved to NYSE via resolve_order_exchange
        mock_get_live.return_value = {
            "holdings": [
                {"id": 103, "ticker": "CAT", "buy_price": 350.0, "current_price": 300.0, "quantity": 2.0}
            ]
        }
        guardian._sync_check_and_execute_guardian_rules()
        mock_broker.place_order.assert_any_call(
            ticker="CAT",
            side="SELL",
            qty=2,
            price=298.5,
            order_type="00",
            exchange="NYSE",
            client_order_id=unittest.mock.ANY,
        )

        # Case 4: Holding missing exchange -> resolved to NASD for tech stock
        mock_get_live.return_value = {
            "holdings": [
                {"id": 104, "ticker": "NVDA", "buy_price": 130.0, "current_price": 115.0, "quantity": 4.0}
            ]
        }
        guardian._sync_check_and_execute_guardian_rules()
        mock_broker.place_order.assert_any_call(
            ticker="NVDA",
            side="SELL",
            qty=4,
            price=114.42,
            order_type="00",
            exchange="NASD",
            client_order_id=unittest.mock.ANY,
        )

    @patch("al_sangmoo.domain.risk.autopilot_trader.add_portfolio_buy", return_value=1)
    @patch("al_sangmoo.domain.risk.autopilot_trader.default_kis_broker")
    @patch("al_sangmoo.domain.risk.autopilot_trader.is_us_regular_hours", return_value=True)
    @patch("al_sangmoo.domain.risk.autopilot_trader.get_live_portfolio")
    @patch("al_sangmoo.domain.risk.autopilot_trader.validate_pre_trade_guardrail", return_value={"allowed": True})
    def test_autopilot_buy_exchange_resolution(
        self, mock_validate, mock_get_live, mock_market_open, mock_broker, mock_add_buy
    ):
        from al_sangmoo.domain.risk.autopilot_trader import AutoPilotTrader
        import asyncio

        mock_broker.is_configured.return_value = True
        mock_broker.place_order.return_value = {"status": "filled", "order_id": "TEST-AP-1"}
        mock_broker.get_live_price.return_value = 0.0
        mock_get_live.return_value = {
            "holdings": [],
            "total_equity_usd": 100000.0,
            "cash_usd": 100000.0,
        }

        trader = AutoPilotTrader()

        # Buy CAT (NYSE)
        pick_cat = {"ticker": "CAT", "price": 300.0, "sizing": {"shares": 10}}
        asyncio.run(trader._execute_satellite_entry(pick_cat, slot_rank=1, holdings=[], now_str="2026-09-22 10:00:00"))
        mock_broker.place_order.assert_called_with(
            ticker="CAT",
            side="BUY",
            qty=10,
            price=301.5,
            order_type="00",
            exchange="NYSE",
            client_order_id=ANY,
        )

        # Buy NVDA (NASD)
        pick_nvda = {"ticker": "NVDA", "price": 120.0, "sizing": {"shares": 20}}
        asyncio.run(trader._execute_satellite_entry(pick_nvda, slot_rank=2, holdings=[], now_str="2026-09-22 10:00:00"))
        mock_broker.place_order.assert_called_with(
            ticker="NVDA",
            side="BUY",
            qty=20,
            price=120.6,
            order_type="00",
            exchange="NASD",
            client_order_id=ANY,
        )


if __name__ == "__main__":
    unittest.main()

