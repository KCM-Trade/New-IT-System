"""OPT-0063 Option A — /login-ip/trade-profit/lookup point lookup."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.routes.login_ip_trade_profit import router
from app.services import login_ip_trade_profit_service as svc


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from app.core import login_ip_orders_db

    monkeypatch.setattr(login_ip_orders_db, "_DB_PATH", tmp_path / "login_ip_orders.db")
    login_ip_orders_db.init_login_ip_orders_db()
    monkeypatch.setattr(svc, "_get_redis", lambda: None)
    return login_ip_orders_db


def _pnl(server, account, ip, close_date, user_id, ib_id=None, profit=10.0, deal_ref=None):
    return (
        server,
        deal_ref or f"{server}-{account}-{close_date}-{ip}",
        close_date,
        account,
        str(account),
        ip,
        None,
        user_id,
        ib_id if ib_id is not None else user_id + 9000,
        "XAUUSD",
        1.0,
        profit,
        3600,
        close_date,
        0,
        None,
    )


def _seed_five_client_group(db):
    """Five clients on one IP — forms a ranked group under page thresholds."""
    rows = [
        _pnl("MT5", 1001 + i, "1.1.1.1", "2026-09-15", user_id=101 + i, deal_ref=f"g{i}")
        for i in range(5)
    ]
    db.replace_trade_ip_pnl_for_date("2026-09-15", rows)
    return rows


def _params():
    return "from=2026-09-15&to=2026-09-21"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


# ── service: id mode ───────────────────────────────────────────────────────


def test_lookup_id_matches_account_id(db):
    _seed_five_client_group(db)
    result = svc.lookup("2026-09-15", "2026-09-21", "1003", kind="id")
    assert result["query_kind"] == "id"
    assert result["matched_as"] == ["account_id"]
    assert len(result["seed_accounts"]) == 1
    assert result["seed_accounts"][0]["account_id"] == 1003
    assert result["seed_accounts"][0]["is_seed"] is True
    assert len(result["peer_accounts"]) == 5
    assert len(result["group_ids"]) == 1
    assert result["below_cluster_threshold"] is False


def test_lookup_id_matches_user_id(db):
    _seed_five_client_group(db)
    result = svc.lookup("2026-09-15", "2026-09-21", "103", kind="id")
    assert result["query_kind"] == "id"
    assert result["matched_as"] == ["user_id"]
    assert result["seed_accounts"][0]["user_id"] == 103
    assert len(result["peer_accounts"]) == 5
    assert len(result["group_ids"]) == 1


def test_lookup_id_below_threshold_shows_peers(db):
    """Two clients on one IP — peers visible, no ranked group."""
    db.replace_trade_ip_pnl_for_date("2026-09-15", [
        _pnl("MT5", 1001, "2.2.2.2", "2026-09-15", user_id=201),
        _pnl("MT5", 1002, "2.2.2.2", "2026-09-15", user_id=202, deal_ref="b"),
    ])
    result = svc.lookup("2026-09-15", "2026-09-21", "1001", kind="id")
    assert result["group_ids"] == []
    assert result["below_cluster_threshold"] is True
    assert len(result["peer_accounts"]) == 2
    assert {a["account_id"] for a in result["peer_accounts"]} == {1001, 1002}


def test_lookup_id_expands_via_seed_ips(db):
    """Seed account also used a second IP shared with a third account."""
    db.replace_trade_ip_pnl_for_date("2026-09-15", [
        _pnl("MT5", 1001, "3.3.3.3", "2026-09-15", user_id=301),
        _pnl("MT5", 1001, "4.4.4.4", "2026-09-15", user_id=301, deal_ref="a2"),
        _pnl("MT5", 1002, "4.4.4.4", "2026-09-15", user_id=302, deal_ref="b"),
    ])
    result = svc.lookup("2026-09-15", "2026-09-21", "301", kind="id")
    assert result["seed_ips"] == ["3.3.3.3", "4.4.4.4"]
    assert len(result["peer_accounts"]) == 2


def test_lookup_id_empty(db):
    result = svc.lookup("2026-09-15", "2026-09-21", "99999", kind="id")
    assert result["seed_accounts"] == []
    assert result["peer_accounts"] == []
    assert result["below_cluster_threshold"] is True


# ── service: ip mode ───────────────────────────────────────────────────────


def test_lookup_ip_mode(db):
    _seed_five_client_group(db)
    result = svc.lookup("2026-09-15", "2026-09-21", "1.1.1.1", kind="ip")
    assert result["query_kind"] == "ip"
    assert result["matched_as"] is None
    assert result["seed_ips"] == ["1.1.1.1"]
    assert len(result["peer_accounts"]) == 5
    assert all(a["is_seed"] for a in result["peer_accounts"])
    assert len(result["group_ids"]) == 1


def test_lookup_ip_empty(db):
    result = svc.lookup("2026-09-15", "2026-09-21", "8.8.8.8", kind="ip")
    assert result["peer_accounts"] == []


def test_lookup_invalid_q(db):
    with pytest.raises(ValueError, match="numeric client"):
        svc.lookup("2026-09-15", "2026-09-21", "not-valid", kind="auto")
    with pytest.raises(ValueError, match="valid IPv4"):
        svc.lookup("2026-09-15", "2026-09-21", "999.999.1.1", kind="ip")


# ── HTTP contract ────────────────────────────────────────────────────────────


def test_lookup_api_happy_path(client, monkeypatch):
    payload = {
        "query": "1001",
        "query_kind": "id",
        "matched_as": ["account_id"],
        "window": {"from": "2026-09-15", "to": "2026-09-21"},
        "group_ids": ["abc123"],
        "seed_accounts": [{
            "account_key": "MT5-1001",
            "server": "MT5",
            "account_id": 1001,
            "user_id": 101,
            "ib_id": 9101,
            "trades": 1,
            "profit_usd": 10.0,
            "lots": 1.0,
            "active_days": 1,
            "avg_hold_min": 60.0,
            "dominant_symbol": "XAUUSD",
            "open_ips": ["1.1.1.1"],
            "is_seed": True,
        }],
        "peer_accounts": [],
        "seed_ips": ["1.1.1.1"],
        "below_cluster_threshold": False,
    }
    monkeypatch.setattr(svc, "lookup", lambda f, t, q, kind="auto": payload)
    r = client.get(f"/api/v1/login-ip/trade-profit/lookup?{_params()}&q=1001")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["group_ids"] == ["abc123"]
    assert body["seed_accounts"][0]["open_ips"] == ["1.1.1.1"]


def test_lookup_api_invalid_q(client):
    r = client.get(f"/api/v1/login-ip/trade-profit/lookup?{_params()}&q=abc")
    assert r.status_code == 400
    assert "IPv4" in r.json()["detail"] or "numeric" in r.json()["detail"]


def test_lookup_api_integration(db, client):
    _seed_five_client_group(db)
    r = client.get(f"/api/v1/login-ip/trade-profit/lookup?{_params()}&q=1001")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query_kind"] == "id"
    assert len(body["group_ids"]) == 1
