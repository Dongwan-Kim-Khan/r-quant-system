"""Open-source integration flags: HRP, Nautilus FSM, residual momentum, webhook, sentiment."""
import os
import sys
import unittest

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.core.feature_flags import is_enabled
from al_sangmoo.domain.quant.factor_extensions import calculate_residual_momentum_score
from al_sangmoo.domain.quant.stream_sentiment_pipeline import (
    StreamSentimentPipeline,
    get_stream_sentiment_offset,
    reset_stream_sentiment,
    update_stream_sentiment,
)
from al_sangmoo.domain.risk.order_state_machine import (
    NautilusInspiredOrderGuardrail,
    OrderStatus,
    default_order_fsm,
)
from al_sangmoo.domain.risk.riskfolio_sizer import RiskfolioPositionSizer
from al_sangmoo.interfaces.api.routers.webhook import verify_tradingview_signature


class TestFeatureFlags(unittest.TestCase):
    def test_default_off(self):
        os.environ.pop("USE_RISKFOLIO_HRP", None)
        self.assertFalse(is_enabled("USE_RISKFOLIO_HRP"))

    def test_true_values(self):
        os.environ["USE_WEBHOOK_ROUTER"] = "true"
        self.assertTrue(is_enabled("USE_WEBHOOK_ROUTER"))
        os.environ.pop("USE_WEBHOOK_ROUTER", None)


class TestHrpSizer(unittest.TestCase):
    def test_two_asset_weights_capped_and_positive(self):
        idx = pd.bdate_range("2024-01-02", periods=80)
        a = 100 + np.cumsum(np.random.default_rng(0).normal(0, 1, 80))
        b = 50 + np.cumsum(np.random.default_rng(1).normal(0, 2, 80))
        df = pd.DataFrame({"AAA": a, "BBB": b}, index=idx)
        alloc = RiskfolioPositionSizer(10000.0, max_weight_per_asset=0.40).calculate_hrp_weights(df)
        self.assertEqual(set(alloc), {"AAA", "BBB"})
        self.assertTrue(all(v >= 0 for v in alloc.values()))
        self.assertLessEqual(max(alloc.values()), 4000.0 + 1e-6)


class TestOrderFsm(unittest.TestCase):
    def setUp(self):
        default_order_fsm._inflight_orders.clear()

    def test_duplicate_rejected(self):
        fsm = NautilusInspiredOrderGuardrail()
        self.assertTrue(fsm.pre_trade_risk_check("NVDA"))
        with self.assertRaises(RuntimeError):
            fsm.pre_trade_risk_check("NVDA")
        fsm.transition_state("NVDA", OrderStatus.FILLED)
        self.assertTrue(fsm.pre_trade_risk_check("NVDA"))

    def test_wide_spread_rejected(self):
        fsm = NautilusInspiredOrderGuardrail(max_spread_bps=20.0)
        with self.assertRaises(ValueError):
            fsm.pre_trade_risk_check("NVDA", ask_price=101.0, bid_price=100.0)


class TestResidualMomentum(unittest.TestCase):
    def test_window_too_short_is_zero(self):
        s = pd.Series([1, 2, 3])
        self.assertEqual(calculate_residual_momentum_score(s, s, window=60), 0.0)

    def test_score_bounded(self):
        rng = np.random.default_rng(2)
        idx = pd.RangeIndex(120)
        bench = pd.Series(100 + np.cumsum(rng.normal(0, 1, 120)), index=idx)
        asset = bench * 1.01 + pd.Series(np.cumsum(rng.normal(0, 0.3, 120)), index=idx)
        score = calculate_residual_momentum_score(asset, bench, window=60)
        self.assertGreaterEqual(score, -100.0)
        self.assertLessEqual(score, 100.0)


class TestWebhookHmac(unittest.TestCase):
    def test_signature_roundtrip(self):
        secret = "unit-secret"
        body = b'{"ticker":"NVDA","action":"BUY"}'
        import hashlib, hmac
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_tradingview_signature(body, sig, secret))
        self.assertFalse(verify_tradingview_signature(body, "deadbeef", secret))

    def test_disabled_returns_404(self):
        os.environ.pop("USE_WEBHOOK_ROUTER", None)
        from server import app
        client = TestClient(app)
        res = client.post("/api/v2/webhook/tradingview", json={"ticker": "NVDA", "action": "BUY"})
        self.assertEqual(res.status_code, 404)


class TestSentimentPipeline(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_stream_sentiment()

    async def test_keyword_delta(self):
        pipe = StreamSentimentPipeline()
        await pipe.ingest_subtitles_stream("주도주 돌파 반등")
        delta = await pipe.drain_once()
        self.assertGreater(delta, 0)
        off = update_stream_sentiment(delta)
        self.assertEqual(off, get_stream_sentiment_offset())


if __name__ == "__main__":
    unittest.main()
