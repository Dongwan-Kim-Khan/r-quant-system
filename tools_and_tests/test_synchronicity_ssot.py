"""
Synchronicity SSOT: delta WS events, 5-stage FSM, log ring buffer,
no HTTP polling while WS is connected, no window.alert().
"""
import os
import re
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.api.hub import EventType, WebSocketBroadcastHub


class MockWs:
    def __init__(self):
        self.received = []
        self.closed = False

    async def accept(self):
        return None

    async def send_json(self, data):
        self.received.append(data)

    async def close(self, code=1000, reason=""):
        self.closed = True


def _read(rel):
    with open(os.path.join(PROJECT_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class TestHubDeltaEvents(unittest.IsolatedAsyncioTestCase):
    async def test_broadcast_includes_monotonic_seq(self):
        hub = WebSocketBroadcastHub()
        ws = MockWs()
        await hub.connect(ws)
        await hub.broadcast("portfolio_update", {"holdings": []})
        await hub.broadcast_delta(EventType.POSITION_DELTA, {"ticker": "NVDA", "action": "EXIT"})
        self.assertEqual(len(ws.received), 2)
        self.assertEqual(ws.received[0]["event"], "portfolio_update")
        self.assertEqual(ws.received[0]["seq"], 1)
        self.assertEqual(ws.received[1]["type"], "POSITION_DELTA")
        self.assertEqual(ws.received[1]["seq"], 2)
        self.assertEqual(ws.received[1]["data"]["ticker"], "NVDA")

    async def test_eventtype_values_match_legacy_frontend(self):
        self.assertEqual(EventType.PORTFOLIO_UPDATE.value, "portfolio_update")
        self.assertEqual(EventType.GUARDIAN_ALERT.value, "guardian_alert")
        self.assertEqual(EventType.AUTOPILOT_BUY.value, "autopilot_buy_alert")
        self.assertEqual(EventType.POSITION_DELTA.value, "POSITION_DELTA")


class TestFrontendSynchronicity(unittest.TestCase):
    def test_no_window_alert_in_terminal_js(self):
        for rel in ("frontend/js/websocket.js", "frontend/js/ui.js"):
            src = _read(rel)
            hits = [
                f"{rel}:{i}: {line.strip()}"
                for i, line in enumerate(src.splitlines(), 1)
                if re.search(r"\balert\s*\(", line)
            ]
            self.assertEqual(hits, [], "alert() remains:\n" + "\n".join(hits))

    def test_five_stage_connection_fsm(self):
        src = _read("frontend/js/websocket.js")
        for state in ("DISCONNECTED", "CONNECTING", "CONNECTED", "REAUTHENTICATING", "ERROR"):
            self.assertIn(state, src)
        self.assertIn("connectionState", src)
        self.assertIn("_setConnectionState", src)

    def test_http_polling_killed_while_ws_connected(self):
        src = _read("frontend/js/websocket.js")
        self.assertIn("ConnectionState.CONNECTED", src)
        self.assertIn("_stopHttpPolling", src)
        self.assertIn("preferLivePortfolio", src)
        self.assertIn("live_feed_update", src)
        self.assertIn("_hydrateFromHttp", src)
        # Visibility/focus must not HTTP-refresh while the hub is live.
        self.assertRegex(
            src,
            r"wsOpen && !isStale && this\.connectionState === ConnectionState\.CONNECTED",
        )
        self.assertIn("_startHttpPolling()", src)

    def test_log_ring_buffer_cap(self):
        src = _read("frontend/js/ui.js")
        self.assertIn("MAX_LOGS: 300", src)
        self.assertIn("childElementCount > this.MAX_LOGS", src)
        self.assertIn("slice(0, this.MAX_LOGS)", src)
        self.assertNotIn("logHidden", src)
        self.assertIn("formatExecutionTs", src)
        self.assertIn("[접수]", src)
        self.assertNotIn("rawTs.split(' ')[1]", src)
        self.assertIn("formatExecutionTs", src)
        self.assertIn("[접수]", src)

    def test_chart_request_seq_guard(self):
        src = _read("frontend/js/chart.js")
        self.assertIn("_requestSeq", src)
        self.assertIn("requestSeq !== this._requestSeq", src)
        self.assertIn("_inflightTicker", src)
        self.assertIn("Math.max(0, totalCandles - barsToShow)", src)
        self.assertIn("_clearSeries", src)
        self.assertIn("_isCurrentPayload", src)
        self.assertIn("payloadTk !== reqTicker", src)
        self.assertIn("_renderSeq", src)
        decoder = _read("frontend/js/decoder.js")
        self.assertIn("liveChartData.ticker", decoder)
        self.assertIn("_repairExplodedLast", src)
        self.assertIn("_sanePrice", src)
        ui = _read("frontend/js/ui.js")
        self.assertIn("defaultChartTarget", ui)
        self.assertIn("isMarketTicker", ui)
        self.assertIn("preferLivePortfolio", ui)
        self.assertIn("latestPortfolioData", ui)

    def test_chart_api_rejects_mismatched_payload(self):
        api = _read("frontend/js/api.js")
        self.assertIn('cache: "no-store"', api)
        self.assertIn("payloadTk !== cleanTicker", api)
        charts = _read("al_sangmoo/interfaces/api/routers/charts.py")
        self.assertIn("cached_tk == ticker_clean", charts)
        server = _read("server.py")
        self.assertIn('path.startswith("/api/")', server)

    def test_delta_handler_present(self):
        src = _read("frontend/js/websocket.js")
        self.assertIn("POSITION_DELTA", src)
        self.assertIn("_applyPositionDelta", src)
        self.assertIn("UI.toast", src)


class TestChartCacheTickerGuard(unittest.IsolatedAsyncioTestCase):
    async def test_memory_cache_does_not_return_foreign_ticker(self):
        import time
        from unittest.mock import patch
        from al_sangmoo.interfaces.api.routers import charts as charts_mod

        charts_mod.CHART_CACHE["AMD"] = {
            "timestamp": time.time(),
            "data": {
                "ticker": "NVDA",
                "candles": [{"time": "2026-01-02", "open": 1, "high": 2, "low": 1, "close": 1.5}],
            },
        }
        with patch.object(charts_mod, "_enrich_chart_with_realtime_price", side_effect=lambda d, t: d):
            data = await charts_mod.get_chart_data("AMD")
        self.assertIsInstance(data, dict)
        self.assertEqual(str(data.get("ticker", "")).upper(), "AMD")
        charts_mod.CHART_CACHE.pop("AMD", None)


class TestLivePriceHarmonize(unittest.TestCase):
    def test_rejects_exploded_quotes_and_rescales_zdiv(self):
        from al_sangmoo.interfaces.api.routers.charts import _harmonize_live_price
        self.assertEqual(_harmonize_live_price(226.12, 226.0), 226.12)
        self.assertEqual(_harmonize_live_price(22612, 226.12), 226.12)
        self.assertIsNone(_harmonize_live_price(3358782, 128.1))
        self.assertIsNone(_harmonize_live_price(1653000, 226.12))
        self.assertIsNone(_harmonize_live_price(200.03, 102.18))
        self.assertIsNone(_harmonize_live_price(156.45, 98.26))
        payload = {
            "ticker": "NVDA",
            "candles": [
                {"time": "2026-08-27", "open": 220, "high": 228, "low": 219, "close": 226.12},
                {"time": "2026-08-28", "open": 226.6, "high": 227.98, "low": 225.2, "close": 226.12},
            ],
        }
        from al_sangmoo.interfaces.api.routers.charts import _enrich_chart_with_realtime_price
        from unittest.mock import patch
        with patch("al_sangmoo.infrastructure.persistence.get_live_portfolio", return_value={"holdings": []}), \
             patch("db_manager.get_live_portfolio", return_value={"holdings": []}):
            with patch("al_sangmoo.infrastructure.brokers.kis_broker.default_kis_broker") as broker:
                broker.get_live_price.return_value = 22612.0
                out = _enrich_chart_with_realtime_price(payload, "NVDA")
                broker.get_live_price.assert_not_called()
        self.assertEqual(out["candles"][-1]["close"], 226.12)
        self.assertLess(out["candles"][-1]["high"], 300)

        cvx = {
            "ticker": "CVX",
            "candles": [
                {"time": "2026-08-26", "open": 102.85, "high": 104.84, "low": 100.85, "close": 102.54},
                {"time": "2026-08-27", "open": 102.19, "high": 103.53, "low": 100.85, "close": 102.18},
            ],
        }
        with patch("db_manager.get_live_portfolio", return_value={
            "holdings": [{"ticker": "CVX", "current_price": 200.03}]
        }):
            cvx_out = _enrich_chart_with_realtime_price(cvx, "CVX")
        self.assertAlmostEqual(float(cvx_out["candles"][-1]["close"]), 102.18)
        self.assertLess(float(cvx_out["candles"][-1]["high"]), 110)


if __name__ == "__main__":
    unittest.main()
