"""Contract guards of the CS fund risk-control ledger API (spec §4).

(a) OpenAPI snapshot of /api/v1/cs/fund-risk/* and every schema those paths
    reference. Regenerate deliberately with
    ``UPDATE_SNAPSHOTS=1 pytest tests/test_cs_fund_risk_contract.py``.
(b) Every handler is a plain ``def`` (blocking IO) and the route file holds no SQL.
(c) Every route resolves to the ``cs`` module and is OPEN in ROUTE_SCOPE.
(d) Status codes over real HTTP through the assembled app (module gate,
    404 / 422 / 409 / 503, 204 on delete).

PostgreSQL is replaced by the in-memory FakePG and the replica by a scripted
fake: nothing here reaches a real database.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_cs_fund_risk_service import FakePG, make_lookup

_BACKEND_DIR = Path(__file__).resolve().parents[1]
ROUTE_FILE = _BACKEND_DIR / "app" / "api" / "v1" / "routes" / "cs_fund_risk.py"
SNAPSHOT = Path(__file__).parent / "snapshots" / "cs_fund_risk_openapi_v1.json"
PREFIX = "/api/v1/cs/fund-risk"
TEST_API_KEY = "unit-test-api-key"
MANAGER = "boss@kohleservices.com"
STAFF = "staff@kohleservices.com"

EXPECTED = {
    "/order/{transaction_id}": {"get"},
    "/incidents": {"get", "post"},
    "/incidents/{incident_id}": {"get", "put", "delete"},
    "/export": {"get"},
}


@pytest.fixture
def app_main(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_FILE_DIR", str(tmp_path / "logs"))
    monkeypatch.chdir(_BACKEND_DIR)
    import app.main as app_main_module

    return app_main_module


# ── (a) OpenAPI snapshot ─────────────────────────────────────────────────────

def _refs(node, out: set[str]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            out.add(ref.rsplit("/", 1)[-1])
        for v in node.values():
            _refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _refs(v, out)


def _contract_slice(spec: dict) -> dict:
    paths = {p: v for p, v in spec.get("paths", {}).items() if p.startswith(PREFIX)}
    schemas = spec.get("components", {}).get("schemas", {})
    seen: set[str] = set()
    todo: set[str] = set()
    _refs(paths, todo)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        found: set[str] = set()
        _refs(schemas.get(name, {}), found)
        todo |= found - seen
    return {"paths": paths, "schemas": {k: schemas[k] for k in sorted(seen) if k in schemas}}


def _dump(obj) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def test_openapi_snapshot(app_main, monkeypatch):
    monkeypatch.delenv("API_DOCS_ENABLED", raising=False)
    current = _contract_slice(app_main.create_app().openapi())
    assert current["paths"], f"no paths under {PREFIX} in the OpenAPI document"
    text = _dump(current)
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(text, encoding="utf-8")
        return
    # A missing snapshot must fail, not silently regenerate.
    assert SNAPSHOT.exists(), f"{SNAPSHOT} missing; run with UPDATE_SNAPSHOTS=1 and commit it"
    assert text == SNAPSHOT.read_text(encoding="utf-8"), (
        "fund-risk ledger OpenAPI contract changed. If this is deliberate, "
        "rerun with UPDATE_SNAPSHOTS=1."
    )


def test_snapshot_covers_exactly_the_spec_endpoints(app_main):
    paths = _contract_slice(app_main.create_app().openapi())["paths"]
    assert {p[len(PREFIX):]: set(v) for p, v in paths.items()} == EXPECTED
    ok = lambda path, verb, code: paths[PREFIX + path][verb]["responses"][code]  # noqa: E731
    schema = lambda node: node["content"]["application/json"]["schema"]["$ref"]  # noqa: E731
    assert schema(ok("/order/{transaction_id}", "get", "200")).endswith("/OrderPreviewResponse")
    assert schema(ok("/incidents", "get", "200")).endswith("/IncidentListResponse")
    assert schema(ok("/incidents", "post", "200")).endswith("/Incident")
    assert schema(ok("/incidents/{incident_id}", "get", "200")).endswith("/Incident")
    assert schema(ok("/incidents/{incident_id}", "put", "200")).endswith("/Incident")
    assert schema(ok("/incidents", "post", "409")).endswith("/IncidentConflict")
    assert schema(ok("/incidents/{incident_id}", "put", "409")).endswith("/IncidentConflict")
    assert "204" in paths[PREFIX + "/incidents/{incident_id}"]["delete"]["responses"]


# ── (b) route layer ──────────────────────────────────────────────────────────

def test_route_handlers_are_plain_def():
    tree = ast.parse(ROUTE_FILE.read_text(encoding="utf-8"))
    handlers = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
            and d.func.attr in {"get", "post", "put", "patch", "delete", "api_route"}
            for d in node.decorator_list
        )
    ]
    assert len(handlers) == 7, [h.name for h in handlers]
    asyncs = [h.name for h in handlers if isinstance(h, ast.AsyncFunctionDef)]
    assert not asyncs, f"blocking-IO routes must be plain def: {asyncs}"


def test_route_file_holds_no_sql_and_never_reads_identity_from_the_body():
    src = ROUTE_FILE.read_text(encoding="utf-8")
    for needle in ("SELECT ", "INSERT ", "UPDATE ", "psycopg2", "pymysql", "X-Device-ID", "body.author"):
        assert needle not in src, f"route file contains {needle!r}"
    assert "app.schemas.cs_fund_risk" in src


def test_delete_is_the_only_manager_gated_route(app_main):
    from app.core.auth_deps import require_manager

    gated = set()
    for route in app_main.create_app().routes:
        path = getattr(route, "path", "")
        if not path.startswith(PREFIX):
            continue
        calls = {d.call for d in route.dependant.dependencies}
        if require_manager in calls:
            gated.add((path[len(PREFIX):], tuple(sorted(route.methods))))
    assert gated == {("/incidents/{incident_id}", ("DELETE",))}


# ── (c) module gate / data scope ─────────────────────────────────────────────

def test_every_route_is_in_the_cs_module_and_open_in_route_scope(app_main):
    from app.core.auth_deps import classify_path, module_names
    from app.core.data_scope import OPEN, ROUTE_SCOPE

    paths = _contract_slice(app_main.create_app().openapi())["paths"]
    assert paths
    for p in paths:
        short = p[len("/api/v1"):]
        policy = classify_path(short)
        assert policy is not None, f"{p} is not classified in MODULE_MAP"
        assert module_names(policy) == ("cs",), (p, policy)
        assert ROUTE_SCOPE.get(short) == OPEN, short


# ── (d) HTTP through the assembled app ───────────────────────────────────────

@pytest.fixture
def world(app_main, tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY", TEST_API_KEY)
    monkeypatch.setenv("ALERT_MAIL_ALLOWED_DOMAINS", "kohleservices.com")
    monkeypatch.setenv("AUTH_ALLOWED_EMAIL_DOMAINS", "kohleservices.com")
    monkeypatch.setenv("AUTH_MANAGER_EMAILS", MANAGER)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_COOKIE_ENABLED", "false")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.delenv("AUTH_DEV_LOGIN_EMAIL", raising=False)
    monkeypatch.delenv("AUTH_BREAK_GLASS_ENABLED", raising=False)

    from app.core.config import get_settings

    get_settings.cache_clear()

    from app.core import users_db

    monkeypatch.setattr(users_db, "_DB_PATH", tmp_path / "users_test.db")
    users_db.reset_connection_cache()
    users_db.init_users_db()

    from app.services.cs_ledger import incidents, order_lookup
    from app.services.cs_ledger.order_lookup import ReplicaUnavailable

    pg = FakePG()
    monkeypatch.setattr(incidents, "risk_cases_conn", pg.conn)
    orders: dict = {}
    state = {"down": False}

    def fetch_orders(ids, settings=None):
        if state["down"]:
            raise ReplicaUnavailable("MySQL replica unavailable: test")
        return {i: orders[i] for i in ids if i in orders}

    monkeypatch.setattr(order_lookup, "fetch_orders", fetch_orders)
    monkeypatch.setattr(order_lookup, "fetch_order", lambda i, settings=None: fetch_orders([i]).get(i))

    # No `with`: lifespan (schedulers, DB init) must not run.
    client = TestClient(app_main.create_app())
    yield type("World", (), {"http": client, "pg": pg, "orders": orders, "state": state, "incidents": incidents})
    users_db.reset_connection_cache()


def _mint(email: str, allowed_modules: str = '["cs"]') -> dict:
    from app.core.users_db import get_users_db
    from app.services import auth_service

    sid, user = auth_service.login(email, source="dev")
    with get_users_db() as conn:
        conn.execute("UPDATE users SET allowed_modules = ? WHERE id = ?", (allowed_modules, user.user_id))
    return {"Authorization": f"Bearer {sid}", "X-API-Key": TEST_API_KEY}


def test_module_gate_refuses_with_403_not_401(world):
    h = _mint(STAFF, '["data"]')
    for method, path in (("get", "/incidents"), ("get", "/order/1"), ("get", "/export"),
                         ("post", "/incidents"), ("delete", "/incidents/1")):
        r = getattr(world.http, method)(PREFIX + path, headers=h)
        assert r.status_code == 403, (method, path, r.status_code)
    assert world.pg.statements == []


def test_order_preview_status_codes(world):
    h = _mint(STAFF)
    world.orders[700450] = make_lookup(pspId=9999, psp_name="New PSP")
    world.orders[5] = make_lookup(5, type="transfer in")
    r = world.http.get(f"{PREFIX}/order/700450", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["existing_id"] is None and body["warnings"] == ["psp_unmapped"]
    assert body["order"]["channel"] == "其他: New PSP" and body["order"]["client_name"] == "San Zhang"
    assert world.http.get(f"{PREFIX}/order/404404", headers=h).status_code == 404
    r = world.http.get(f"{PREFIX}/order/5", headers=h)
    assert r.status_code == 422 and "transfer in" in r.json()["detail"]
    assert world.http.get(f"{PREFIX}/order/0", headers=h).status_code == 422
    world.state["down"] = True
    assert world.http.get(f"{PREFIX}/order/700450", headers=h).status_code == 503


def test_create_list_get_update_delete_round_trip(world):
    staff, boss = _mint(STAFF), _mint(MANAGER, '["*"]')
    world.orders[700450] = make_lookup()
    r = world.http.post(f"{PREFIX}/incidents", headers=staff, json={
        "transaction_id": 700450, "reported_date": "2026-10-13", "authority": "bank",
        # Order fields sent by a client are ignored: the server re-reads the order.
        "channel": "Jpay", "amount_cny": 1, "created_by": "mallory@evil.example",
    })
    assert r.status_code == 200, r.text
    inc = r.json()
    assert inc["order"]["channel"] == "Exlink" and inc["order"]["amount_cny"] == 10000.0
    assert inc["created_by"] == STAFF and inc["order_is_live"] is True and inc["version"] == 1

    # Preview now reports the existing row; a second create is a 409 with its id.
    assert world.http.get(f"{PREFIX}/order/700450", headers=staff).json()["existing_id"] == inc["id"]
    r = world.http.post(f"{PREFIX}/incidents", headers=staff, json={"transaction_id": 700450})
    assert r.status_code == 409
    assert r.json()["existing_id"] == inc["id"] and r.json()["current_version"] is None

    r = world.http.get(f"{PREFIX}/incidents/{inc['id']}", headers=staff)
    assert r.status_code == 200 and r.json()["group_size"] == 1
    assert world.http.get(f"{PREFIX}/incidents/999", headers=staff).status_code == 404

    r = world.http.put(f"{PREFIX}/incidents/{inc['id']}", headers=staff,
                       json={"version": 1, "authority": "bank", "reported_date": "2026-10-13",
                             "result_text": "已解封"})
    assert r.status_code == 200 and r.json()["version"] == 2
    r = world.http.put(f"{PREFIX}/incidents/{inc['id']}", headers=staff,
                       json={"version": 1, "authority": "alipay"})
    assert r.status_code == 409
    assert r.json()["current_version"] == 2 and r.json()["existing_id"] is None
    assert world.http.put(f"{PREFIX}/incidents/999", headers=staff, json={"version": 1}).status_code == 404

    assert world.http.delete(f"{PREFIX}/incidents/{inc['id']}", headers=staff).status_code == 403
    r = world.http.delete(f"{PREFIX}/incidents/{inc['id']}", headers=boss)
    assert r.status_code == 204 and r.content == b""
    assert world.http.delete(f"{PREFIX}/incidents/{inc['id']}", headers=boss).status_code == 404
    assert world.http.get(f"{PREFIX}/incidents/{inc['id']}", headers=staff).status_code == 404


def test_create_validation_and_upstream_errors(world):
    h = _mint(STAFF)
    world.orders[5] = make_lookup(5, is_fee=1)
    world.orders[6] = make_lookup(6)
    post = lambda body: world.http.post(f"{PREFIX}/incidents", headers=h, json=body)  # noqa: E731
    assert post({"transaction_id": 404404}).status_code == 404
    assert post({"transaction_id": 5}).status_code == 422
    assert post({"transaction_id": 6, "authority": "nobody"}).status_code == 422
    assert post({"transaction_id": 6, "id_province": "广东省"}).status_code == 422
    assert post({"transaction_id": 6, "incident_group_id": 424242}).status_code == 422
    assert post({}).status_code == 422
    world.state["down"] = True
    assert post({"transaction_id": 6}).status_code == 503
    assert world.pg.live() == []


def test_list_parameters_response_shape_and_snapshot_fallback(world, monkeypatch):
    h = _mint(STAFF)
    seen: dict = {}

    def fake_list(filters, *, page, page_size, sort, order):
        seen.update(filters=filters, page=page, page_size=page_size, sort=sort, order=order)
        return [world.incidents.get_row(world.pg.seed(transaction_id=42, client_id=7)["id"])], 101

    monkeypatch.setattr(world.incidents, "list_rows", fake_list)
    r = world.http.get(f"{PREFIX}/incidents", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"data", "total", "page", "page_size", "total_pages", "statistics"}
    assert (body["total"], body["page"], body["page_size"], body["total_pages"]) == (101, 1, 50, 3)
    assert body["statistics"]["replica_live"] is True
    assert body["data"][0]["order_is_live"] is False          # order 42 is not on the replica
    assert (seen["sort"], seen["order"]) == ("reported_date", "desc")

    world.state["down"] = True
    r = world.http.get(f"{PREFIX}/incidents", headers=h, params={
        "date_from": "2026-01-01", "date_to": "2026-01-31", "channel": "Jpay", "direction": "deposit",
        "authority": "bank", "frozen_scope": "one_card", "pending_result": "true", "q": "1279140",
        "page": 2, "page_size": 200, "sort": "txn_processed_at", "order": "asc",
    })
    assert r.status_code == 200, r.text                        # replica down -> snapshot, not 503
    assert r.json()["statistics"]["replica_live"] is False
    f = seen["filters"]
    assert (f.channel, f.direction, f.authority, f.frozen_scope, f.pending_result, f.q) == (
        "Jpay", "deposit", "bank", "one_card", True, "1279140")
    assert (seen["page"], seen["page_size"], seen["sort"], seen["order"]) == (2, 200, "txn_processed_at", "asc")

    for bad in ({"page_size": 501}, {"page": 0}, {"sort": "amount_cny"}, {"order": "up"},
                {"q": "abc"}, {"q": "1 OR 1=1"}, {"direction": "sideways"}, {"date_from": "yesterday"}):
        assert world.http.get(f"{PREFIX}/incidents", headers=h, params=bad).status_code == 422, bad


def test_pg_unavailable_is_503_and_export_returns_xlsx(world, monkeypatch):
    from app.core.risk_cases_pg import RiskCasesUnavailable

    h = _mint(STAFF)
    monkeypatch.setattr(world.incidents, "export_rows",
                        lambda filters, *, sort, order: [world.pg.seed(transaction_id=9)])
    r = world.http.get(f"{PREFIX}/export", headers=h, params={"channel": "Jpay"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert "attachment" in r.headers["content-disposition"] and r.content[:2] == b"PK"

    def down(*a, **kw):
        raise RiskCasesUnavailable("pool exhausted")

    for name in ("list_rows", "export_rows", "get_row", "find_existing_id", "update_incident"):
        monkeypatch.setattr(world.incidents, name, down)
    world.orders[1] = make_lookup(1)
    assert world.http.get(f"{PREFIX}/incidents", headers=h).status_code == 503
    assert world.http.get(f"{PREFIX}/export", headers=h).status_code == 503
    assert world.http.get(f"{PREFIX}/incidents/1", headers=h).status_code == 503
    assert world.http.get(f"{PREFIX}/order/1", headers=h).status_code == 503
    assert world.http.put(f"{PREFIX}/incidents/1", headers=h, json={"version": 1}).status_code == 503
