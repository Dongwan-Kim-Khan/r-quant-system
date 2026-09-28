"""
Tests for API visibility and error contracts specified in HANDOFF_ANTIGRAVITY_API_VISIBILITY_AND_ERRORS.md.
Validates:
1. Reconcile failure returns 502 with detail and report
2. GET /api/portfolio?reconcile=true returns 200 with root holdings and reconcile status
3. Global 500 handler contract
4. /api/health behavior (no KIS network call, 503 on missing feed)
5. Dashboard feed error fields (portfolio_error, universe_sectors_error, execution_logs_error)
"""
import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from server import app, global_exception_handler
from al_sangmoo.interfaces.api.routers.dashboard import DASHBOARD_JSON


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_reconcile_failure_returns_502_with_detail(client):
    """check_sync returning status: error must produce 502 with detail and report."""
    err_report = {
        "status": "error",
        "timestamp": "2026-09-22 12:00:00",
        "message": "Broker balance inquiry failed: 500 Internal Error",
        "discrepancies": []
    }
    with patch("al_sangmoo.interfaces.api.routers.broker.check_sync", return_value=err_report):
        res = client.post("/api/broker/reconcile")
        assert res.status_code == 502
        body = res.json()
        assert body.get("detail") == "Broker balance inquiry failed: 500 Internal Error"
        assert body.get("report") == err_report


def test_reconcile_success_returns_200(client):
    """check_sync returning status: success must produce 200 with report."""
    ok_report = {
        "status": "success",
        "timestamp": "2026-09-22 12:00:00",
        "message": "Reconciliation completed",
        "discrepancies": []
    }
    with patch("al_sangmoo.interfaces.api.routers.broker.check_sync", return_value=ok_report):
        res = client.post("/api/broker/reconcile")
        assert res.status_code == 200
        body = res.json()
        assert body.get("status") == "success"


def test_portfolio_reconcile_true_error_returns_200_with_reconcile_key(client):
    """GET /api/portfolio?reconcile=true when check_sync fails must return 200 with reconcile.status = error."""
    err_report = {
        "status": "error",
        "message": "Broker balance inquiry failed: timeout"
    }
    with patch("al_sangmoo.interfaces.api.routers.portfolio.default_kis_broker.is_configured", return_value=True), \
         patch("al_sangmoo.interfaces.api.routers.portfolio.check_sync", return_value=err_report):
        res = client.get("/api/portfolio?reconcile=true")
        assert res.status_code == 200
        body = res.json()
        assert "holdings" in body
        assert "total_equity_usd" in body
        assert "reconcile" in body
        assert body["reconcile"]["status"] == "error"
        assert body["reconcile"]["message"] == "Broker balance inquiry failed: timeout"


def test_portfolio_reconcile_false_has_no_reconcile_key(client):
    """GET /api/portfolio?reconcile=false must return ledger without reconcile key."""
    res = client.get("/api/portfolio?reconcile=false")
    assert res.status_code == 200
    body = res.json()
    assert "holdings" in body
    assert "reconcile" not in body


def test_portfolio_reconcile_skipped_when_unconfigured(client):
    """GET /api/portfolio?reconcile=true when broker unconfigured returns skipped."""
    with patch("al_sangmoo.interfaces.api.routers.portfolio.default_kis_broker.is_configured", return_value=False):
        res = client.get("/api/portfolio?reconcile=true")
        assert res.status_code == 200
        body = res.json()
        assert "reconcile" in body
        assert body["reconcile"]["status"] == "skipped"
        assert body["reconcile"]["message"] == "브로커 미설정"


def test_portfolio_reconcile_exception_handled_gracefully(client):
    """GET /api/portfolio?reconcile=true when check_sync raises must return 200 with error."""
    with patch("al_sangmoo.interfaces.api.routers.portfolio.default_kis_broker.is_configured", return_value=True), \
         patch("al_sangmoo.interfaces.api.routers.portfolio.check_sync", side_effect=RuntimeError("Reconciliation crash")):
        res = client.get("/api/portfolio?reconcile=true")
        assert res.status_code == 200
        body = res.json()
        assert "holdings" in body
        assert body["reconcile"]["status"] == "error"
        assert body["reconcile"]["message"] == "대조 중 오류"


def test_global_500_handler_returns_standard_contract():
    """Unhandled exceptions must produce 500 JSON with detail and message in Korean."""
    import asyncio
    from starlette.requests import Request
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/test-crash",
        "headers": []
    }
    req = Request(scope)
    resp = asyncio.run(global_exception_handler(req, RuntimeError("secret internal state")))
    assert resp.status_code == 500
    body = json.loads(resp.body.decode("utf-8"))
    assert body["status"] == "error"
    assert body["detail"] == "내부 오류가 발생했습니다."
    assert body["message"] == "내부 오류가 발생했습니다."
    assert "secret" not in json.dumps(body)


def test_health_check_missing_feed_returns_503(client):
    """/api/health returns 503 when feed is missing or corrupted."""
    with patch("os.path.exists", return_value=False):
        res = client.get("/api/health")
        assert res.status_code == 503
        body = res.json()
        assert body["status"] == "error"
        assert body["detail"] == "시스템 상태가 비정상입니다."
        assert body["feed"] == "error"


def test_health_check_ok_does_not_call_kis(client):
    """/api/health returns 200 when feed and DB exist, without calling KIS TR."""
    with patch("al_sangmoo.infrastructure.brokers.kis_broker.default_kis_broker.get_overseas_balance") as mock_balance:
        res = client.get("/api/health")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ok"
        assert body["feed"] == "ok"
        assert body["database"] == "ok"
        assert "broker_configured" in body
        # Ensure KIS network call was never made
        mock_balance.assert_not_called()


def test_dashboard_portfolio_error_field(client):
    """When live portfolio injection raises, response is 200 with portfolio_error."""
    with patch("al_sangmoo.infrastructure.persistence.get_live_portfolio", side_effect=RuntimeError("DB disconnected")):
        res = client.get("/api/dashboard")
        assert res.status_code == 200
        body = res.json()
        assert body.get("portfolio_source") == "feed_cache"
        assert body.get("portfolio_error") == "실시간 원장을 읽지 못했습니다"


def test_dashboard_universe_sectors_error_field(client):
    """When universe sectors fails, response has universe_sectors_error and no empty array disguise."""
    with patch("al_sangmoo.domain.quant.dynamic_universe.load_universe_sectors", side_effect=RuntimeError("Sector error")):
        res = client.get("/api/dashboard")
        assert res.status_code == 200
        body = res.json()
        assert body.get("universe_sectors_error") == "섹터 데이터를 불러오지 못했습니다"
        assert "universe_sectors" not in body


def test_execution_logs_endpoint_raises_500_on_failure(client):
    """GET /api/execution-logs must return 500 if persistence query fails, not 200 with empty list."""
    with patch("al_sangmoo.infrastructure.persistence.get_execution_logs", side_effect=RuntimeError("Query error")):
        res = client.get("/api/execution-logs")
        assert res.status_code == 500
        body = res.json()
        assert body.get("status") == "error"
        assert body.get("detail") == "내부 오류가 발생했습니다."


def test_health_check_db_failure_returns_503(client):
    """/api/health returns 503 when DB query fails."""
    with patch("al_sangmoo.infrastructure.persistence.get_live_portfolio", side_effect=RuntimeError("DB query error")):
        res = client.get("/api/health")
        assert res.status_code == 503
        body = res.json()
        assert body["status"] == "error"
        assert body["detail"] == "시스템 상태가 비정상입니다."
        assert body["database"] == "error"


def test_dashboard_execution_logs_error_field(client):
    """When get_execution_logs raises, dashboard response has execution_logs_error: True and no execution_logs list."""
    with patch("al_sangmoo.infrastructure.persistence.get_execution_logs", side_effect=RuntimeError("Audit error")):
        res = client.get("/api/dashboard")
        assert res.status_code == 200
        body = res.json()
        assert body.get("execution_logs_error") is True
        assert "execution_logs" not in body


def test_dashboard_portfolio_error_does_not_block_execution_logs(client):
    """When get_live_portfolio raises, execution logs is still retrieved and injected independently."""
    fake_logs = [{"ticker": "AAPL", "side": "BUY", "quantity": 10, "price": 150.0}]
    with patch("al_sangmoo.infrastructure.persistence.get_live_portfolio", side_effect=RuntimeError("DB error")), \
         patch("al_sangmoo.infrastructure.persistence.get_execution_logs", return_value=fake_logs):
        res = client.get("/api/dashboard")
        assert res.status_code == 200
        body = res.json()
        assert body.get("portfolio_source") == "feed_cache"
        assert body.get("portfolio_error") == "실시간 원장을 읽지 못했습니다"
        assert body.get("execution_logs") == fake_logs
        assert "execution_logs_error" not in body


def test_websocket_origin_allowed_same_host(client):
    """WebSocket accepts connection when Origin host matches request Host header."""
    with client.websocket_connect("/ws", headers={"Origin": "http://myquant.internal:8000", "Host": "myquant.internal:8000"}) as ws:
        msg = ws.receive_json()
        assert msg.get("type") == "connected"


def test_websocket_origin_forbidden_mismatched(client):
    """WebSocket closes with 1008 Forbidden Origin when Origin does not match Host and is not in ALLOWED_ORIGINS."""
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws", headers={"Origin": "http://evil-site.com", "Host": "localhost:8000"}):
            pass
    assert excinfo.value.code == 1008


def test_portfolio_sync_failure_returns_502(client):
    """check_sync returning status: error must produce 502 with detail, report, portfolio, and no status: success."""
    err_report = {
        "status": "error",
        "message": "sync failed",
        "discrepancies": []
    }
    with patch("al_sangmoo.interfaces.api.routers.portfolio.check_sync", return_value=err_report):
        res = client.post("/api/portfolio/sync")
        assert res.status_code == 502
        body = res.json()
        assert body.get("detail") == "sync failed"
        assert body.get("report") == err_report
        assert "portfolio" in body
        assert body.get("status") != "success"


def test_portfolio_sync_missing_message_returns_default_detail(client):
    """check_sync returning status: error without message must produce 502 with default '대조 실패' detail."""
    err_report = {"status": "error"}
    with patch("al_sangmoo.interfaces.api.routers.portfolio.check_sync", return_value=err_report):
        res = client.post("/api/portfolio/sync")
        assert res.status_code == 502
        body = res.json()
        assert body.get("detail") == "대조 실패"
        assert body.get("report") == err_report
        assert "portfolio" in body
        assert body.get("status") != "success"


def test_portfolio_sync_non_dict_returns_502(client):
    """check_sync returning non-dict (e.g. None) must produce 502, not 200 success."""
    with patch("al_sangmoo.interfaces.api.routers.portfolio.check_sync", return_value=None):
        res = client.post("/api/portfolio/sync")
        assert res.status_code == 502
        body = res.json()
        assert body.get("detail") == "대조 실패"
        assert body.get("report") is None
        assert "portfolio" in body
        assert body.get("status") != "success"


def test_portfolio_sync_success_returns_200(client):
    """check_sync returning status: success must produce 200 with status: success."""
    ok_report = {
        "status": "success",
        "message": "sync ok",
        "discrepancies": []
    }
    with patch("al_sangmoo.interfaces.api.routers.portfolio.check_sync", return_value=ok_report):
        res = client.post("/api/portfolio/sync")
        assert res.status_code == 200
        body = res.json()
        assert body.get("status") == "success"
        assert body.get("report") == ok_report
        assert "portfolio" in body


def test_dashboard_macro_error_when_gauges_none(client):
    """When get_live_macro_gauges returns None, dashboard has macro_error and 200 OK."""
    with patch("al_sangmoo.interfaces.api.routers.dashboard.get_live_macro_gauges", return_value=None), \
         patch("al_sangmoo.infrastructure.brokers.kis_broker.default_kis_broker.get_overseas_balance") as mock_kis:
        res = client.get("/api/dashboard")
        assert res.status_code == 200
        body = res.json()
        assert body.get("macro_error") == "매크로 게이지를 불러오지 못했습니다"
        mock_kis.assert_not_called()


def test_dashboard_no_macro_error_when_gauges_present(client):
    """When get_live_macro_gauges returns valid dict, dashboard does not have macro_error."""
    fake_gauges = {"yield_10y": 4.25, "vix": 15.5}
    with patch("al_sangmoo.interfaces.api.routers.dashboard.get_live_macro_gauges", return_value=fake_gauges), \
         patch("al_sangmoo.infrastructure.brokers.kis_broker.default_kis_broker.get_overseas_balance") as mock_kis:
        res = client.get("/api/dashboard")
        assert res.status_code == 200
        body = res.json()
        assert "macro_error" not in body
        assert body.get("macro", {}).get("macro_gauges") == fake_gauges
        mock_kis.assert_not_called()


def test_get_live_macro_gauges_logs_exception():
    """get_live_macro_gauges logs exception when fetch raises."""
    import al_sangmoo.interfaces.api.routers.dashboard as dash_mod
    with patch.object(dash_mod, "CACHED_MACRO_GAUGES", None), \
         patch.object(dash_mod, "LAST_MACRO_TIME", 0.0), \
         patch("youtube_stream_scanner.fetch_realtime_macro_gauges", side_effect=RuntimeError("Stream offline")), \
         patch.object(dash_mod.logger, "exception") as mock_log:
        result = dash_mod.get_live_macro_gauges()
        assert result is None
        mock_log.assert_called_once()


def test_get_live_macro_gauges_returns_cached_on_exception():
    """get_live_macro_gauges logs exception and returns cache when fetch raises."""
    import al_sangmoo.interfaces.api.routers.dashboard as dash_mod
    cached_data = {"yield_10y": 4.1}
    with patch.object(dash_mod, "CACHED_MACRO_GAUGES", cached_data), \
         patch.object(dash_mod, "LAST_MACRO_TIME", 0.0), \
         patch("youtube_stream_scanner.fetch_realtime_macro_gauges", side_effect=RuntimeError("Stream offline")), \
         patch.object(dash_mod.logger, "exception") as mock_log:
        result = dash_mod.get_live_macro_gauges()
        assert result == cached_data
        mock_log.assert_called_once()


def test_dashboard_macro_error_when_gauges_empty_dict(client):
    """When get_live_macro_gauges returns empty dict, dashboard has macro_error."""
    with patch("al_sangmoo.interfaces.api.routers.dashboard.get_live_macro_gauges", return_value={}), \
         patch("al_sangmoo.infrastructure.brokers.kis_broker.default_kis_broker.get_overseas_balance") as mock_kis:
        res = client.get("/api/dashboard")
        assert res.status_code == 200
        body = res.json()
        assert body.get("macro_error") == "매크로 게이지를 불러오지 못했습니다"
        mock_kis.assert_not_called()


def test_dashboard_macro_error_when_gauges_not_dict(client):
    """When get_live_macro_gauges returns non-dict, dashboard has macro_error."""
    with patch("al_sangmoo.interfaces.api.routers.dashboard.get_live_macro_gauges", return_value="invalid_gauges"), \
         patch("al_sangmoo.infrastructure.brokers.kis_broker.default_kis_broker.get_overseas_balance") as mock_kis:
        res = client.get("/api/dashboard")
        assert res.status_code == 200
        body = res.json()
        assert body.get("macro_error") == "매크로 게이지를 불러오지 못했습니다"
        mock_kis.assert_not_called()


def test_portfolio_sync_unhandled_exception_returns_500(client):
    """When check_sync raises unhandled exception, global 500 handler returns standard error."""
    with patch("al_sangmoo.interfaces.api.routers.portfolio.check_sync", side_effect=RuntimeError("unexpected crash")):
        res = client.post("/api/portfolio/sync")
        assert res.status_code == 500
        body = res.json()
        assert body.get("status") == "error"
        assert body.get("detail") == "내부 오류가 발생했습니다."
        assert "crash" not in str(body)


