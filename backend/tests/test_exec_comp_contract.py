"""Contract guards of the exec-compensation API (03 §4.2, 04 §4).

(a) OpenAPI snapshot of /api/v1/exec-compensation/* and every schema those
    paths reference. A field change turns this red on purpose; regenerate
    deliberately with ``UPDATE_SNAPSHOTS=1 pytest tests/test_exec_comp_contract.py``.
(b) The route file holds no business logic: it imports only the query core,
    the contract schemas and the error type, and every handler is a plain
    ``def`` (blocking IO — CLAUDE.md).
(c) Every route resolves to the ``data`` module in MODULE_MAP.
(d) Error body shape ``{"error": {"code", "message"}}`` over real HTTP, and
    403 (not 401) without the ``data`` module.
(e) A GET writes no audit row (01 D16).

The query layer's source factory is monkeypatched to a fake: nothing here
reaches MySQL / Redis.
"""

from __future__ import annotations

import ast
import json
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_BACKEND_DIR = Path(__file__).resolve().parents[1]
ROUTE_FILE = _BACKEND_DIR / "app" / "api" / "v1" / "routes" / "exec_compensation.py"
SNAPSHOT = Path(__file__).parent / "snapshots" / "exec_compensation_openapi_v1.json"
# gitignored docs copy in the main workspace; written only on UPDATE_SNAPSHOTS=1
# and only when that directory exists. The tracked SSOT is SNAPSHOT above.
DOCS_COPY = Path("/opt/myproject/New-IT-System/docs/exec-compensation/openapi-v1.json")
PREFIX = "/api/v1/exec-compensation"
TEST_API_KEY = "unit-test-api-key"
MANAGER = "boss@kohleservices.com"
STAFF = "staff@kohleservices.com"

def needs_routes(fn):  # kept as a marker; the route file must exist
    return fn


@pytest.fixture
def app_main(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_FILE_DIR", str(tmp_path / "logs"))
    monkeypatch.chdir(_BACKEND_DIR)
    import app.main as app_main_module

    return app_main_module


# ---------------------------------------------------------------------------
# (a) OpenAPI snapshot
# ---------------------------------------------------------------------------


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


@needs_routes
def test_openapi_snapshot(app_main, monkeypatch):
    monkeypatch.delenv("API_DOCS_ENABLED", raising=False)
    spec = app_main.create_app().openapi()
    current = _contract_slice(spec)
    assert current["paths"], f"no paths under {PREFIX} in the OpenAPI document"
    text = _dump(current)
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(text, encoding="utf-8")
        if DOCS_COPY.parent.is_dir():
            DOCS_COPY.write_text(text, encoding="utf-8")
        return
    # A missing snapshot must fail, not silently regenerate: on a clean clone
    # that would turn the contract guard into a no-op.
    assert SNAPSHOT.exists(), f"{SNAPSHOT} missing; run with UPDATE_SNAPSHOTS=1 and commit it"
    assert text == SNAPSHOT.read_text(encoding="utf-8"), (
        "exec-compensation OpenAPI contract changed. Fields are only ever added "
        "(03 §4.2); if this change is deliberate, rerun with UPDATE_SNAPSHOTS=1."
    )


@needs_routes
def test_snapshot_covers_the_four_endpoints(app_main):
    paths = _contract_slice(app_main.create_app().openapi())["paths"]
    for suffix in ("/summary", "/orders", "/export", "/status"):
        assert PREFIX + suffix in paths, sorted(paths)
        assert set(paths[PREFIX + suffix]) == {"get"}


# ---------------------------------------------------------------------------
# (b) route layer is thin
# ---------------------------------------------------------------------------

_ALLOWED_EXEC_COMP_IMPORTS = {"query", "errors", "export", "source"}


def _route_tree() -> ast.Module:
    return ast.parse(ROUTE_FILE.read_text(encoding="utf-8"))


@needs_routes
def test_route_imports_no_calculation_internals():
    """Classification / money / time conversion live below the route."""
    bad: list[str] = []
    for node in ast.walk(_route_tree()):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names = {a.name for a in node.names}
            if mod.startswith("app.services.exec_comp"):
                leaf = mod[len("app.services.exec_comp"):].lstrip(".")
                if leaf in ("classify", "calc", "models", "limits"):
                    bad.append(mod)
                if leaf == "" and names & {"classify", "calc", "limits"}:
                    bad.append(f"{mod}: {sorted(names)}")
            elif node.level and mod.split(".")[-1] in ("classify", "calc"):
                bad.append(mod)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("app.services.exec_comp.") and a.name.rsplit(".", 1)[-1] in (
                    "classify", "calc", "limits",
                ):
                    bad.append(a.name)
    assert not bad, f"route imports business internals: {bad}"


@needs_routes
def test_route_uses_the_query_core_and_contract():
    mods = {
        node.module
        for node in ast.walk(_route_tree())
        if isinstance(node, ast.ImportFrom) and node.module
    }
    joined = " ".join(sorted(mods))
    assert "exec_comp" in joined
    assert "app.schemas.exec_compensation" in mods


@needs_routes
def test_route_handlers_are_plain_def():
    tree = _route_tree()
    handlers = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decorated = any(
                isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr in {"get", "post", "put", "patch", "delete", "api_route"}
                for d in node.decorator_list
            )
            if decorated:
                handlers.append(node)
    assert handlers, "no route handlers found"
    asyncs = [h.name for h in handlers if isinstance(h, ast.AsyncFunctionDef)]
    assert not asyncs, f"blocking-IO routes must be plain def: {asyncs}"


@needs_routes
def test_route_has_no_sql_or_arithmetic_on_money():
    src = ROUTE_FILE.read_text(encoding="utf-8")
    for needle in ("SELECT ", "mt5_deals", "orders_history", "/ 100", "/100", "PriceCurrent"):
        assert needle not in src, f"route file contains {needle!r}"


# ---------------------------------------------------------------------------
# (c) module gate
# ---------------------------------------------------------------------------


@needs_routes
def test_every_route_is_in_the_data_module(app_main):
    from app.core.auth_deps import classify_path, module_names

    paths = _contract_slice(app_main.create_app().openapi())["paths"]
    assert paths
    for p in paths:
        policy = classify_path(p[len("/api/v1"):])
        assert policy is not None, f"{p} is not classified in MODULE_MAP"
        assert module_names(policy) == ("data",), (p, policy)


# ---------------------------------------------------------------------------
# (d) HTTP: error shape, 403 not 401  /  (e) no audit row
# ---------------------------------------------------------------------------


@pytest.fixture
def http(app_main, tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY", TEST_API_KEY)
    monkeypatch.setenv("ALERT_MAIL_ALLOWED_DOMAINS", "kohleservices.com")
    monkeypatch.setenv("AUTH_ALLOWED_EMAIL_DOMAINS", "kohleservices.com")
    monkeypatch.setenv("AUTH_MANAGER_EMAILS", MANAGER)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_COOKIE_ENABLED", "false")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.delenv("AUTH_DEV_LOGIN_EMAIL", raising=False)
    monkeypatch.delenv("AUTH_BREAK_GLASS_ENABLED", raising=False)
    monkeypatch.setenv("EXEC_COMP_SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setenv("EXEC_COMP_CACHE_TTL_S", "0")
    monkeypatch.setenv("REDIS_HOST", "127.0.0.1")
    monkeypatch.setenv("REDIS_PORT", "1")

    from app.core.config import get_settings

    get_settings.cache_clear()

    from app.core import users_db

    monkeypatch.setattr(users_db, "_DB_PATH", tmp_path / "users_test.db")
    users_db.reset_connection_cache()
    users_db.init_users_db()

    _patch_source_factory(monkeypatch)

    # No `with`: lifespan (schedulers, DB warmups) must not run.
    client = TestClient(app_main.create_app())
    yield client
    users_db.reset_connection_cache()


def _patch_source_factory(monkeypatch) -> None:
    """Replace whatever builds a ReplicaFillSource with the fake below."""
    from tests.test_exec_comp_query import FakeSource, FILLS  # noqa: WPS433

    fake = FakeSource(FILLS)

    from app.services.exec_comp import source as source_mod

    class _FakeReplica(FakeSource):
        def __init__(self, *a, **kw):
            super().__init__(FILLS)

        def with_deadline(self, deadline):
            return self

        def close(self):
            pass

    monkeypatch.setattr(source_mod, "ReplicaFillSource", _FakeReplica)
    import importlib

    for mod_name in (
        "app.api.v1.routes.exec_compensation",
        "app.services.exec_comp.query",
    ):
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        if hasattr(mod, "ReplicaFillSource"):
            monkeypatch.setattr(mod, "ReplicaFillSource", _FakeReplica)
        if callable(getattr(mod, "default_cache", None)):
            monkeypatch.setattr(mod, "default_cache", lambda *a, **k: None)
        for attr in ("default_source", "make_source", "get_source", "_source", "source_factory"):
            if callable(getattr(mod, attr, None)):
                monkeypatch.setattr(mod, attr, lambda *a, **k: fake)


def _mint(email: str, allowed_modules: str) -> dict:
    from app.core.users_db import get_users_db
    from app.services import auth_service

    sid, user = auth_service.login(email, source="dev")
    with get_users_db() as conn:
        conn.execute(
            "UPDATE users SET allowed_modules = ? WHERE id = ?", (allowed_modules, user.user_id)
        )
    return {"Authorization": f"Bearer {sid}", "X-API-Key": TEST_API_KEY}


def _audit_count(tmp_path) -> int:
    conn = sqlite3.connect(str(tmp_path / "users_test.db"))
    try:
        return conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    finally:
        conn.close()


RANGE = {"date_from": "2026-09-01", "date_to": "2026-09-10"}


def _assert_error(resp, code: str) -> None:
    from app.schemas.exec_compensation import ERROR_STATUS

    assert resp.status_code == ERROR_STATUS[code], resp.text
    body = resp.json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) == {"code", "message"}, body
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


@needs_routes
def test_missing_subject_is_422_subject_required(http):
    h = _mint(STAFF, '["data"]')
    _assert_error(http.get(f"{PREFIX}/summary", params=RANGE, headers=h), "SUBJECT_REQUIRED")


@needs_routes
def test_both_subjects_is_422_subject_ambiguous(http):
    h = _mint(STAFF, '["data"]')
    r = http.get(
        f"{PREFIX}/summary",
        params={**RANGE, "client_id": 1001, "login_sid": "5-60000001"},
        headers=h,
    )
    _assert_error(r, "SUBJECT_AMBIGUOUS")


@needs_routes
def test_bad_sort_is_422_sort_not_allowed(http):
    h = _mint(STAFF, '["data"]')
    r = http.get(
        f"{PREFIX}/orders",
        params={**RANGE, "client_id": 1001, "sort_by": "price; drop table x"},
        headers=h,
    )
    _assert_error(r, "SORT_NOT_ALLOWED")


@needs_routes
def test_malformed_date_is_422_in_the_same_shape(http):
    h = _mint(STAFF, '["data"]')
    r = http.get(
        f"{PREFIX}/summary",
        params={"client_id": 1001, "date_from": "yesterday", "date_to": "2026-09-10"},
        headers=h,
    )
    _assert_error(r, "VALIDATION_ERROR")


@needs_routes
def test_before_coverage_is_422(http):
    h = _mint(STAFF, '["data"]')
    r = http.get(
        f"{PREFIX}/summary",
        params={"client_id": 1001, "date_from": "2023-01-01", "date_to": "2023-03-01"},
        headers=h,
    )
    _assert_error(r, "RANGE_BEFORE_COVERAGE")


@needs_routes
@pytest.mark.parametrize("grant", ['["cs"]', "[]", '["risk","ai"]'])
def test_without_data_module_is_403_not_401(http, grant):
    h = _mint(STAFF, grant)
    for suffix in ("/summary", "/orders", "/status", "/export"):
        r = http.get(f"{PREFIX}{suffix}", params={**RANGE, "client_id": 1001}, headers=h)
        assert r.status_code == 403, (suffix, r.status_code, r.text)


@needs_routes
def test_without_session_is_401(http):
    r = http.get(
        f"{PREFIX}/summary",
        params={**RANGE, "client_id": 1001},
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert r.status_code == 401


@needs_routes
def test_data_module_holder_gets_the_envelope_and_no_audit_row(http, tmp_path):
    h = _mint(STAFF, '["data"]')
    before = _audit_count(tmp_path)
    r = http.get(
        f"{PREFIX}/summary",
        params={"client_id": 1001, "date_from": "2026-09-01", "date_to": "2026-09-01"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"data", "basis", "as_of", "coverage", "statistics"} <= set(body)
    assert body["basis"]["reference"] == "request_price"
    assert body["basis"]["exclude_open_positions"] is True
    r2 = http.get(
        f"{PREFIX}/orders",
        params={"client_id": 1001, "date_from": "2026-09-01", "date_to": "2026-09-01"},
        headers=h,
    )
    assert r2.status_code == 200, r2.text
    assert {"data", "total", "page", "page_size", "total_pages"} <= set(r2.json())
    assert _audit_count(tmp_path) == before


@needs_routes
def test_get_logs_no_audit_missing(http, tmp_path, monkeypatch):
    """01 D16: queries are not audited, and AuditMissing must not flag them."""
    from app.core import audit_missing_middleware as amm

    warned: list[str] = []
    monkeypatch.setattr(
        amm.logger, "warning", lambda msg, *a, **k: warned.append(msg % a if a else msg)
    )
    h = _mint(STAFF, '["data"]')
    before = _audit_count(tmp_path)
    for suffix in ("/summary", "/orders", "/status"):
        r = http.get(
            f"{PREFIX}{suffix}",
            params={"client_id": 1001, "date_from": "2026-09-01", "date_to": "2026-09-01"},
            headers=h,
        )
        assert r.status_code == 200, (suffix, r.text)
    assert _audit_count(tmp_path) == before
    assert not [w for w in warned if "AUDIT_MISSING" in w]
