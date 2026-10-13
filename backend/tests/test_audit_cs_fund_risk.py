"""Audit-trail tests of the CS fund risk-control ledger write endpoints.

What is guarded: create / update / delete each leave the right ``audit_log``
rows attributed to the SESSION user; an update that changes nothing leaves
none; a non-manager cannot delete; a failed write leaves no row.

Harness habits copied from test_audit_risk_cases.py: every AUTH_* switch is
pinned per test, and ``users_db._DB_PATH`` points at a tmp file (the real
users.db is shared by dev and prod). PostgreSQL is the in-memory FakePG and the
replica is scripted — no real database is reached.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.routes import cs_fund_risk as route
from app.services.cs_ledger import incidents, order_lookup
from tests.test_cs_fund_risk_service import FakePG, make_lookup

MANAGER = "boss@kohleservices.com"
STAFF = "staff@kohleservices.com"
OFFICE_IP = "10.6.20.55"
BASE = "/api/v1/cs/fund-risk"
TXN = 700450


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("ALERT_MAIL_ALLOWED_DOMAINS", "kohleservices.com")
    monkeypatch.setenv("AUTH_ALLOWED_EMAIL_DOMAINS", "kohleservices.com")
    monkeypatch.setenv("AUTH_MANAGER_EMAILS", MANAGER)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_COOKIE_ENABLED", "false")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.delenv("AUTH_DEV_LOGIN_EMAIL", raising=False)

    from app.core.config import get_settings

    get_settings.cache_clear()

    from app.core import users_db

    monkeypatch.setattr(users_db, "_DB_PATH", tmp_path / "users_test.db")
    users_db.reset_connection_cache()
    users_db.init_users_db()

    pg = FakePG()
    monkeypatch.setattr(incidents, "risk_cases_conn", pg.conn)
    orders = {TXN: make_lookup(TXN), TXN + 1: make_lookup(TXN + 1)}
    monkeypatch.setattr(order_lookup, "fetch_orders",
                        lambda ids, settings=None: {i: orders[i] for i in ids if i in orders})
    monkeypatch.setattr(order_lookup, "fetch_order", lambda i, settings=None: orders.get(i))

    from app.core.auth_middleware import AuthMiddleware
    from app.core.trace_middleware import TraceIDMiddleware

    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    # The last middleware added is the outermost (Trace outside Auth, as in prod).
    app.add_middleware(AuthMiddleware)
    app.add_middleware(TraceIDMiddleware)
    yield type("World", (), {"http": TestClient(app), "pg": pg})
    users_db.reset_connection_cache()


def _bearer(email: str) -> dict:
    from app.services import auth_service

    sid, _ = auth_service.login(email, source="dev")
    return {"Authorization": f"Bearer {sid}", "X-Forwarded-For": OFFICE_IP}


def _audit_rows(action: str | None = None) -> list[dict]:
    from app.core.users_db import get_users_db

    sql, params = "SELECT * FROM audit_log", ()
    if action is not None:
        sql, params = sql + " WHERE action = ?", (action,)
    with get_users_db() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY id", params)]


def _create(world, headers, **over) -> dict:
    body = {"transaction_id": TXN, "reported_date": "2026-10-13", "authority": "bank", **over}
    r = world.http.post(f"{BASE}/incidents", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_create_writes_one_row_attributed_to_the_session(world):
    inc = _create(world, _bearer(STAFF), event_text="卡 6222021234567890123 被封")
    rows = _audit_rows()
    assert [r["action"] for r in rows] == ["fund_risk.incident.create"]
    row = rows[0]
    assert row["target"] == f"transaction:{TXN}"
    assert row["actor_email"] == STAFF and row["ip"] == OFFICE_IP
    assert row["old_value"] is None
    new = json.loads(row["new_value"])
    assert new["id"] == inc["id"] and new["channel"] == "Exlink" and new["authority"] == "bank"
    assert new["event_text"] == "卡 [卡号] 被封"            # the masked text, never the raw one
    assert "6222021234567890123" not in row["new_value"]
    assert "San Zhang" not in row["new_value"]             # client name is not stored anywhere


def test_the_actor_never_comes_from_the_request_body(world):
    inc = _create(world, _bearer(STAFF), created_by="mallory@evil.example",
                  updated_by="mallory@evil.example", author="Mallory")
    assert inc["created_by"] == STAFF and inc["updated_by"] == STAFF
    assert _audit_rows()[0]["actor_email"] == STAFF
    assert world.pg.history[0]["actor"] == STAFF


def test_failed_creates_write_no_audit_row(world):
    h = _bearer(STAFF)
    _create(world, h)
    assert world.http.post(f"{BASE}/incidents", headers=h, json={"transaction_id": TXN}).status_code == 409
    assert world.http.post(f"{BASE}/incidents", headers=h, json={"transaction_id": 404}).status_code == 404
    assert len(_audit_rows()) == 1


def test_update_writes_one_row_per_changed_field(world):
    h = _bearer(STAFF)
    inc = _create(world, h)
    r = world.http.put(f"{BASE}/incidents/{inc['id']}", headers=_bearer(MANAGER), json={
        "version": 1, "reported_date": "2026-10-13", "authority": "alipay", "freeze_days": 7,
    })
    assert r.status_code == 200, r.text
    rows = _audit_rows("fund_risk.incident.update")
    assert {r["target"]: (r["old_value"], r["new_value"]) for r in rows} == {
        f"transaction:{TXN}.authority": ("bank", "alipay"),
        f"transaction:{TXN}.freeze_days": (None, "7"),
    }
    assert {r["actor_email"] for r in rows} == {MANAGER}


def test_a_noop_update_writes_no_audit_row_and_keeps_the_version(world):
    h = _bearer(STAFF)
    inc = _create(world, h, result_text="处理中")
    same = {"version": 1, "reported_date": "2026-10-13", "authority": "bank", "result_text": " 处理中 "}
    r = world.http.put(f"{BASE}/incidents/{inc['id']}", headers=h, json=same)
    assert r.status_code == 200 and r.json()["version"] == 1
    assert _audit_rows("fund_risk.incident.update") == []
    assert [h_["action"] for h_ in world.pg.history] == ["create"]


def test_a_stale_update_writes_no_audit_row(world):
    h = _bearer(STAFF)
    inc = _create(world, h)
    r = world.http.put(f"{BASE}/incidents/{inc['id']}", headers=h, json={"version": 9, "authority": "alipay"})
    assert r.status_code == 409 and r.json()["current_version"] == 1
    assert _audit_rows("fund_risk.incident.update") == []


def test_apply_to_group_audits_every_row_it_changed(world):
    h = _bearer(STAFF)
    first = _create(world, h)
    second = _create(world, h, transaction_id=TXN + 1, incident_group_id=first["incident_group_id"])
    assert second["group_size"] == 2
    r = world.http.put(f"{BASE}/incidents/{first['id']}", headers=h, json={
        "version": 1, "reported_date": "2026-10-13", "authority": "bank",
        "result_text": "已解封", "apply_to_group": True,
    })
    assert r.status_code == 200, r.text
    targets = sorted(r["target"] for r in _audit_rows("fund_risk.incident.update"))
    assert targets == [f"transaction:{TXN}.result_text", f"transaction:{TXN + 1}.result_text"]


def test_delete_by_a_manager_records_the_whole_old_row(world):
    inc = _create(world, _bearer(STAFF), result_text="已解封")
    r = world.http.delete(f"{BASE}/incidents/{inc['id']}", headers=_bearer(MANAGER))
    assert r.status_code == 204
    rows = _audit_rows("fund_risk.incident.delete")
    assert len(rows) == 1
    row = rows[0]
    assert row["target"] == f"transaction:{TXN}" and row["actor_email"] == MANAGER
    assert row["new_value"] is None
    old = json.loads(row["old_value"])
    assert old["id"] == inc["id"] and old["result_text"] == "已解封" and old["created_by"] == STAFF
    assert old["transaction_id"] == TXN and old["deleted_at"] is None
    assert world.pg.rows[inc["id"]]["deleted_by"] == MANAGER      # soft delete: the row is kept


def test_delete_by_a_non_manager_is_403_and_changes_nothing(world):
    h = _bearer(STAFF)
    inc = _create(world, h)
    r = world.http.delete(f"{BASE}/incidents/{inc['id']}", headers=h)
    assert r.status_code == 403
    assert _audit_rows("fund_risk.incident.delete") == []
    assert world.pg.rows[inc["id"]]["deleted_at"] is None
    assert [h_["action"] for h_ in world.pg.history] == ["create"]


def test_delete_is_refused_while_the_kill_switch_is_off(world, monkeypatch):
    inc = _create(world, _bearer(STAFF))
    monkeypatch.setenv("AUTH_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    # With auth off there is no subject: require_manager refuses writes (403).
    assert world.http.delete(f"{BASE}/incidents/{inc['id']}").status_code == 403
    assert world.pg.rows[inc["id"]]["deleted_at"] is None


def test_reads_and_missing_deletes_write_no_audit_row(world):
    h = _bearer(MANAGER)
    inc = _create(world, h)
    before = len(_audit_rows())
    assert world.http.get(f"{BASE}/order/{TXN}", headers=h).status_code == 200
    assert world.http.get(f"{BASE}/incidents/{inc['id']}", headers=h).status_code == 200
    assert world.http.delete(f"{BASE}/incidents/999", headers=h).status_code == 404
    assert len(_audit_rows()) == before
