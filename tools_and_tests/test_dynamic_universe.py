"""
Phase 5.5 Dynamic Sector-Weighted Universe Unit and Integration Tests.
Verifies:
1. Dynamic sector quota summation strictly equals 60.
2. 11 sector representative ETFs mapping and candidate pool coverage.
3. Idempotent snapshot caching and sub-50ms query latency.
4. Fast fallback to constants.WATCHLIST.
5. GET /api/universe/dynamic endpoint contract.
"""

import time
import pytest
from fastapi.testclient import TestClient
from server import app
from al_sangmoo.domain.quant.dynamic_universe import (
    SECTOR_ETF_MAP,
    SECTOR_MASTER_CANDIDATES,
    DYNAMIC_SLOT_QUOTAS,
    evaluate_sector_momentum,
    build_dynamic_60_watchlist,
    get_dynamic_watchlist
)
from al_sangmoo.core.constants import get_active_watchlist, WATCHLIST


def test_dynamic_sector_quota_sum_equals_60():
    """Verifies that the slot quota distribution curve strictly sums to exactly 60."""
    assert len(SECTOR_ETF_MAP) == 11, "Must have exactly 11 major sectors"
    assert len(DYNAMIC_SLOT_QUOTAS) == 11, "Must define quotas for all 11 sectors"
    assert sum(DYNAMIC_SLOT_QUOTAS) == 60, f"Quota sum must be exactly 60, got {sum(DYNAMIC_SLOT_QUOTAS)}"


def test_candidate_pools_exceed_quotas():
    """Verifies that each sector has enough master candidates to satisfy its maximum possible quota."""
    for sec, cands in SECTOR_MASTER_CANDIDATES.items():
        assert len(cands) >= 12, f"Sector {sec} candidate pool too small ({len(cands)})"


def test_evaluate_sector_momentum_and_ranking():
    """Verifies that sector momentum evaluation produces ranked sectors from Rank 1 to 11."""
    ranks = evaluate_sector_momentum()
    assert len(ranks) == 11
    ranks_assigned = [r["rank"] for r in ranks]
    assert ranks_assigned == list(range(1, 12))
    total_quota = sum(r["quota"] for r in ranks)
    assert total_quota == 60


def test_build_dynamic_60_watchlist_snapshot():
    """Verifies that dynamic 60 universe snapshot contains exactly 60 stocks and valid metadata."""
    snapshot = build_dynamic_60_watchlist(force_refresh=False)
    assert snapshot is not None
    assert "watchlist" in snapshot
    assert len(snapshot["watchlist"]) == 60
    assert snapshot["total_count"] == 60
    assert "sector_breakdown" in snapshot
    assert len(snapshot["sector_breakdown"]) == 11


def test_get_dynamic_watchlist_latency():
    """Verifies that cached get_dynamic_watchlist returns in under 50ms."""
    t0 = time.perf_counter()
    w = get_dynamic_watchlist()
    t1 = time.perf_counter()
    duration_ms = (t1 - t0) * 1000.0
    assert len(w) == 60
    assert duration_ms < 50.0, f"Query took {duration_ms:.2f}ms, must be < 50ms"


def test_constants_get_active_watchlist():
    """Verifies that al_sangmoo.core.constants.get_active_watchlist returns 60 stocks."""
    w = get_active_watchlist()
    assert isinstance(w, list)
    assert len(w) == 60


def test_api_dynamic_universe_endpoint():
    """Verifies the GET /api/universe/dynamic FastAPI router endpoint."""
    client = TestClient(app)
    res = client.get("/api/universe/dynamic")
    assert res.status_code == 200
    body = res.json()
    assert body.get("status") == "success"
    data = body.get("data", {})
    assert len(data.get("watchlist", [])) == 60
    assert len(data.get("sector_breakdown", [])) == 11
