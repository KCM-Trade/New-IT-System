"""OPT-0063 Phase 2 — /login-ip/trade-profit/* HTTP contract.

Handler-level tests: the router is mounted BARE (no auth, no module gate —
the gate lives on the parent api_v1_router and is pinned for this prefix in
test_module_gate.py), with the service layer monkeypatched. What is pinned
here is the HTTP contract: window validation, pagination slicing, the 404
contract of a parameter-hashed group_id. The SQL is pinned in
test_login_ip_trade_profit_groups.py.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.routes.login_ip_trade_profit import router
from app.services import login_ip_trade_profit_service as svc


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def _group_row(gid: str, profit: float) -> dict:
    return {
        "group_id": gid,
        "profit_usd": profit,
        "trades": 10,
        "lots": 1.5,
        "accounts": 2,
        "clients": 2,
        "ibs": 1,
        "same_client": False,
        "shared_ips": 3,
        "active_days": 4,
        "profitable_days": 3,
        "dominant_symbol": "XAUUSD",
        "dominant_symbol_share": 0.8,
        "avg_hold_min": 12.0,
        "daily": [{"date": "2026-09-18", "profit_usd": profit}],
        "account_keys": ["MT5-1", "MT5-2"],
        "accounts_detail": [],
        "member_ips": [],
    }


_COVERAGE = {
    "date_from": "2026-09-15",
    "date_to": "2026-09-21",
    "data_from": "2026-09-15",
    "total_trades": 100,
    "total_profit_usd": 500.0,
    "with_ip_trades": 80,
    "with_ip_profit_usd": 400.0,
    "no_ip_trades": 20,
    "no_ip_profit_usd": 100.0,
    "no_ip_by_cause": [{"cause": "bridge_group", "trades": 20, "profit_usd": 100.0}],
    "incomplete_logs": [],
    "unreconciled_dates": [],
}

_Q = "from=2026-09-15&to=2026-09-21"


# ── /groups ──────────────────────────────────────────────────────────────────

def test_groups_happy_path_and_pagination(client, monkeypatch):
    monkeypatch.setattr(
        svc, "get_groups",
        lambda p: ([_group_row("g1", 100.0), _group_row("g2", 50.0)], False),
    )
    monkeypatch.setattr(svc, "get_coverage", lambda f, t: dict(_COVERAGE))

    r = client.get(f"/api/v1/login-ip/trade-profit/groups?{_Q}&page_size=1&page=2")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2 and body["total_pages"] == 2
    assert [row["group_id"] for row in body["data"]] == ["g2"]  # page 2 slice
    assert body["statistics"]["window_with_ip_trades"] == 80
    assert body["statistics"]["groups_trades"] == 20  # 10 trades per fake group


def test_groups_rejects_bad_window(client):
    # Not a date.
    assert client.get(
        "/api/v1/login-ip/trade-profit/groups?from=20260915&to=2026-09-21"
    ).status_code == 400
    # from > to.
    assert client.get(
        "/api/v1/login-ip/trade-profit/groups?from=2026-09-21&to=2026-09-15"
    ).status_code == 400
    # Wider than the retention window can ever serve.
    assert client.get(
        "/api/v1/login-ip/trade-profit/groups?from=2025-01-01&to=2026-09-21"
    ).status_code == 400


# ── /groups/{group_id} ───────────────────────────────────────────────────────

def test_group_detail_404s_for_an_unknown_id(client, monkeypatch):
    """group_id encodes (window, thresholds, account list): an id minted under
    other parameters must 404, not silently show a different group."""
    monkeypatch.setattr(svc, "get_group_detail", lambda gid, p: None)
    r = client.get(f"/api/v1/login-ip/trade-profit/groups/deadbeef1234?{_Q}")
    assert r.status_code == 404
    assert "parameters" in r.json()["detail"]


def test_group_detail_happy_path(client, monkeypatch):
    detail = {
        "group": {k: v for k, v in _group_row("g1", 100.0).items() if k != "member_ips"},
        "member_ips": [
            {"ip": "1.2.3.4", "accounts": 2, "bridge": True,
             "country": "Hong Kong", "window_clients": 2, "window_active_days": 3}
        ],
        "params": {"from": "2026-09-15", "to": "2026-09-21", "min_clients": 2,
                   "public_ip_clients": 10, "include_same_client": False},
        "statistics": {"from_cache": True},
    }
    monkeypatch.setattr(svc, "get_group_detail", lambda gid, p: detail)
    r = client.get(f"/api/v1/login-ip/trade-profit/groups/g1?{_Q}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["group"]["group_id"] == "g1"
    assert body["member_ips"][0]["ip"] == "1.2.3.4"
    assert body["statistics"]["from_cache"] is True


# ── /ips ─────────────────────────────────────────────────────────────────────

def test_ips_happy_path(client, monkeypatch):
    row = {
        "ip": "58.10.224.247", "profit_usd": 999.0, "trades": 42, "lots": 3.0,
        "accounts": 3, "clients": 2, "ibs": 1, "active_days": 5,
        "dominant_symbol": "XAUUSD", "dominant_symbol_share": 0.9,
        "avg_hold_min": 8.0, "shared_exit": False,
    }
    monkeypatch.setattr(svc, "get_ip_ranking", lambda f, t, n: ([row], True))
    # Geo is cache-only decoration; the test env has no geo cache, which must
    # not fail the request.
    r = client.get(f"/api/v1/login-ip/trade-profit/ips?{_Q}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["data"][0]["ip"] == "58.10.224.247"
    assert body["data"][0]["country"] is None
    assert body["statistics"]["from_cache"] is True


# ── /coverage ────────────────────────────────────────────────────────────────

def test_coverage_happy_path(client, monkeypatch):
    monkeypatch.setattr(svc, "get_coverage", lambda f, t: dict(_COVERAGE))
    r = client.get(f"/api/v1/login-ip/trade-profit/coverage?{_Q}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["with_ip_trades"] == 80
    assert body["no_ip_by_cause"][0]["cause"] == "bridge_group"
    assert body["data_from"] == "2026-09-15"
