"""Behaviour of the AI analyst agent's browser-facing route (OPT-0064).

What is pinned here and why each one matters:

  * no `ai` grant -> 403, never 401 (401 bounces the SPA to /login);
  * `[]` refuses, `["*"]` passes, kill switch passes (the gate's three
    invariants, re-asserted on this path because it is a NEW module key);
  * a country-restricted colleague passes the coverage gate (02 §9 — `ai` is
    in SCOPED_MODULES since slice 2) and their scope is forwarded WHOLE;
  * quota is enforced BEFORE the agent is contacted;
  * every turn — succeeded, quota-refused, agent-unreachable — leaves exactly
    one `ai.query.submit` audit row, because the route sets `audit_deferred`
    and thereby switches the AUDIT_MISSING alarm off for itself;
  * the agent container being down produces `agent_unavailable` on the
    stream and nothing else (no exception, no 500);
  * cost is computed here from the price table, not taken from the agent.

Harness follows test_module_gate.py: AUTH_* env pinned per test, users_db and
ai_usage_db redirected to tmp, the REAL ai router mounted behind the real
gates, and the one seam to the agent (`open_agent_stream`) replaced by a
scripted async generator.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

MANAGER = "boss@kohleservices.com"
STAFF = "staff@kohleservices.com"

SCRIPTED_OK: list[tuple[str, Any]] = [
    ("text", {"delta": "Client 123 "}),
    (
        "tool_use",
        {
            "name": "get_client_overview",
            "input": {"subject": {"kind": "client_id", "value": "123"}},
        },
    ),
    (
        "tool_done",
        {
            "name": "get_client_overview",
            "ok": True,
            "source": {"function": "net_gain_by_ids"},
            "certified": True,
        },
    ),
    (
        "tool_use",
        {
            "name": "get_risk_signals",
            "input": {"subject": {"kind": "login_sid", "value": "1-8522845"}},
        },
    ),
    (
        "tool_done",
        {"name": "get_risk_signals", "ok": False, "error_code": "scope_denied"},
    ),
    ("text", {"delta": "looks fine."}),
    (
        "usage",
        {
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_read_input_tokens": 400,
            "cost_usd": None,
        },
    ),
    ("done", {"terminal_reason": "end_turn", "num_turns": 2}),
]


def _parse(text: str) -> list[tuple[str, Any]]:
    """Whole SSE body -> [(event, data)], comments dropped."""
    from app.services.ai_gateway_service import _parse_sse_block

    out = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        parsed = _parse_sse_block(block)
        if parsed is not None:
            out.append(parsed)
    return out


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    def _build(**env: str) -> TestClient:
        monkeypatch.setenv("ALERT_MAIL_ALLOWED_DOMAINS", "kohleservices.com")
        monkeypatch.setenv("AUTH_ALLOWED_EMAIL_DOMAINS", "kohleservices.com")
        monkeypatch.setenv("AUTH_MANAGER_EMAILS", MANAGER)
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_COOKIE_ENABLED", "false")
        monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
        monkeypatch.delenv("AUTH_DEV_LOGIN_EMAIL", raising=False)
        monkeypatch.setenv("AI_AGENT_INTERNAL_TOKEN", "t" * 40)
        monkeypatch.setenv("AI_DAILY_TURNS_LIMIT", "100")
        monkeypatch.setenv("AI_DAILY_COST_LIMIT_USD", "20")
        monkeypatch.delenv("AI_MODEL_PRICES", raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        from app.core.config import get_settings

        get_settings.cache_clear()

        from app.core import ai_usage_db, users_db

        monkeypatch.setattr(users_db, "_DB_PATH", tmp_path / "users_test.db")
        users_db.reset_connection_cache()
        users_db.init_users_db()
        monkeypatch.setattr(ai_usage_db, "_DB_PATH", tmp_path / "ai_agent_test.db")
        ai_usage_db.init_ai_usage_db()

        from app.api.v1.routes.ai import router as ai_router
        from app.core.auth_deps import enforce_module_access
        from app.core.auth_middleware import AuthMiddleware
        from app.core.data_scope import enforce_data_scope_coverage

        parent = APIRouter(
            dependencies=[
                Depends(enforce_module_access),
                Depends(enforce_data_scope_coverage),
            ]
        )
        parent.include_router(ai_router)
        app = FastAPI()
        app.include_router(parent, prefix="/api/v1")
        app.add_middleware(AuthMiddleware)
        return TestClient(app)

    yield _build

    from app.core import users_db

    users_db.reset_connection_cache()


@pytest.fixture
def scripted_agent(monkeypatch):
    """Replace the httpx seam with a scripted stream; records what was sent."""
    from app.services import ai_gateway_service as gateway

    calls: list[dict] = []
    script: dict[str, Any] = {"events": list(SCRIPTED_OK), "raise": None}

    async def _fake(settings, payload, *, token):
        calls.append({"payload": payload, "token": token})
        if script["raise"] is not None:
            raise script["raise"]
        for item in script["events"]:
            yield item

    monkeypatch.setattr(gateway, "open_agent_stream", _fake)
    return {"calls": calls, "script": script}


def _bearer(sid: str) -> dict:
    return {"Authorization": f"Bearer {sid}"}


def _mint(email: str, *, allowed_modules: str | None = "__unset__") -> str:
    from app.core.users_db import get_users_db
    from app.services import auth_service

    sid, user = auth_service.login(email, source="dev")
    if allowed_modules != "__unset__":
        with get_users_db() as conn:
            conn.execute(
                "UPDATE users SET allowed_modules = ? WHERE id = ?",
                (allowed_modules, user.user_id),
            )
    return sid


def _audit_rows(tmp_path) -> list[dict]:
    conn = sqlite3.connect(str(tmp_path / "users_test.db"))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT action, actor_email, target, new_value FROM audit_log ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _turn(client: TestClient, sid: str | None, **body: Any):
    payload = {"message": "how is client 123?", **body}
    headers = _bearer(sid) if sid else {}
    return client.post("/api/v1/ai/turn", json=payload, headers=headers)


# ── authorization ────────────────────────────────────────────────────────────


def test_without_the_ai_module_the_turn_is_403_not_401(make_client, scripted_agent):
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["cs"]')
    r = _turn(client, sid)
    assert r.status_code == 403
    assert scripted_agent["calls"] == []  # never forwarded


def test_an_empty_grant_is_refused(make_client, scripted_agent):
    client = make_client()
    sid = _mint(STAFF, allowed_modules="[]")
    assert _turn(client, sid).status_code == 403
    assert client.get("/api/v1/ai/usage/today", headers=_bearer(sid)).status_code == 403


def test_the_ai_grant_opens_the_stream(make_client, scripted_agent):
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    r = _turn(client, sid)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers.get("x-accel-buffering") == "no"
    events = _parse(r.text)
    names = [e for e, _ in events]
    assert names[0] == "init"
    assert names[-1] == "done"
    assert "text" in names and "tool_use" in names and "usage" in names


def test_the_all_sentinel_grants_ai_too(make_client, scripted_agent):
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["*"]')
    assert _turn(client, sid).status_code == 200


def test_a_manager_passes_without_the_grant(make_client, scripted_agent):
    client = make_client()
    sid = _mint(MANAGER, allowed_modules="[]")
    assert _turn(client, sid).status_code == 200


def test_kill_switch_passes_and_charges_the_anonymous_bucket(make_client, scripted_agent):
    client = make_client(AUTH_ENABLED="false")
    r = _turn(client, None)
    assert r.status_code == 200
    sent = scripted_agent["calls"][0]["payload"]
    assert sent["caller"]["user_id"] is None
    assert sent["scope"] is None
    from app.core import ai_usage_db

    assert ai_usage_db.get_usage(0, ai_usage_db.today_hk())["turns"] == 1


def test_a_country_restricted_caller_is_served_with_their_scope_forwarded(
    make_client, scripted_agent, monkeypatch
):
    """02 §9 (slice 2): `ai` is in SCOPED_MODULES, so the coverage gate lets a
    restricted colleague through and `/ai/turn` is a FILTER route — the
    filtering happens inside the agent, keyed off the scope forwarded here.
    The scope must arrive as the LIST (never None, never collapsed)."""
    from app.core import data_scope

    monkeypatch.setitem(data_scope.DATA_SCOPE_OVERRIDES, STAFF, frozenset({1}))
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    r = _turn(client, sid)
    assert r.status_code == 200
    assert len(scripted_agent["calls"]) == 1
    assert scripted_agent["calls"][0]["payload"]["scope"] == [1]


def test_a_restricted_caller_with_an_empty_scope_is_still_forwarded_as_a_list(
    make_client, scripted_agent, monkeypatch
):
    """`frozenset()` (sees nothing) and `None` (sees everything) are opposite
    and both falsy; the payload must carry `[]`, not `null`."""
    from app.core import data_scope

    monkeypatch.setitem(data_scope.DATA_SCOPE_OVERRIDES, STAFF, frozenset())
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    r = _turn(client, sid)
    assert r.status_code == 200
    assert scripted_agent["calls"][0]["payload"]["scope"] == []


def test_a_model_outside_the_two_deployments_is_422(make_client, scripted_agent):
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    r = _turn(client, sid, model="gpt-4o")
    assert r.status_code == 422
    assert scripted_agent["calls"] == []


# ── the internal hop ─────────────────────────────────────────────────────────


def test_run_sql_text_lands_in_the_audit_row(make_client, scripted_agent):
    """02 §10.1 ⑦: the shared MySQL account cannot be attributed DB-side, so the
    audit row must carry every ad-hoc SQL verbatim. Other tools' inputs do not
    produce the field at all (a row without run_sql has no `sql` key)."""
    from app.core.users_db import get_users_db

    scripted_agent["script"]["events"] = [
        ("tool_use", {"name": "run_sql", "input": {"db": "fxbackoffice", "sql": "SELECT 1", "limit": 200}}),
        ("tool_done", {"name": "run_sql", "ok": True, "source": {"function": "run_sql", "certified": False}, "certified": False}),
        ("tool_use", {"name": "run_sql", "input": {"db": "fxbackoffice", "sql": "SELECT COUNT(*) FROM tags"}}),
        ("tool_done", {"name": "run_sql", "ok": False, "error_code": "invalid_argument"}),
        ("text", {"delta": "uncertified answer"}),
        ("usage", {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0, "cost_usd": None}),
        ("done", {"terminal_reason": "end_turn", "num_turns": 2}),
    ]
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    r = _turn(client, sid)
    assert r.status_code == 200
    with get_users_db() as conn:
        row = conn.execute("SELECT new_value FROM audit_log WHERE action = 'ai.query.submit' ORDER BY id DESC LIMIT 1").fetchone()
    new_value = json.loads(row["new_value"])
    assert new_value["sql"] == ["SELECT 1", "SELECT COUNT(*) FROM tags"]
    assert new_value["tools_called"] == ["run_sql", "run_sql"]

    # A certified-only turn leaves no `sql` key.
    scripted_agent["script"]["events"] = list(SCRIPTED_OK)
    r = _turn(client, sid)
    assert r.status_code == 200
    with get_users_db() as conn:
        row = conn.execute("SELECT new_value FROM audit_log WHERE action = 'ai.query.submit' ORDER BY id DESC LIMIT 1").fetchone()
    assert "sql" not in json.loads(row["new_value"])


def test_identity_and_scope_are_forwarded_whole(make_client, scripted_agent):
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    r = _turn(client, sid, session_id="abc123", model="gpt-5.6-sol")
    assert r.status_code == 200
    call = scripted_agent["calls"][0]
    assert call["token"] == "t" * 40
    p = call["payload"]
    assert p["caller"]["email"] == STAFF
    assert p["caller"]["allowed_modules"] == ["ai"]
    assert p["scope"] is None
    assert p["session_id"] == "abc123"
    assert p["model"] == "gpt-5.6-sol"
    assert p["message"] == "how is client 123?"
    init = dict(_parse(r.text))["init"]
    assert init == {"session_id": "abc123", "model": "gpt-5.6-sol"}


def test_a_missing_session_id_is_minted_and_echoed(make_client, scripted_agent):
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    r = _turn(client, sid)
    init = dict(_parse(r.text))["init"]
    assert len(init["session_id"]) == 32
    assert scripted_agent["calls"][0]["payload"]["session_id"] == init["session_id"]


def test_usage_cost_is_priced_here_not_by_the_agent(make_client, scripted_agent):
    """1000 in (400 cached) + 200 out on terra = 600*2.5 + 400*0.25 + 200*15
    per MTok = 0.0015 + 0.0001 + 0.003 = 0.0046 USD."""
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    r = _turn(client, sid)
    usage = [d for e, d in _parse(r.text) if e == "usage"][0]
    assert usage["cost_usd"] == pytest.approx(0.0046)
    today = client.get("/api/v1/ai/usage/today", headers=_bearer(sid)).json()
    assert today["turns"] == 1
    assert today["input_tokens"] == 1000
    assert today["output_tokens"] == 200
    assert today["cost_usd"] == pytest.approx(0.0046, abs=1e-4)
    assert today["turns_limit"] == 100
    assert today["cost_limit_usd"] == 20.0
    assert set(today) == {
        "day_hk", "turns", "turns_limit", "cost_usd", "cost_limit_usd",
        "input_tokens", "output_tokens",
    }


def test_an_unknown_deployment_prices_at_zero_not_an_exception(make_client):
    from app.core.config import get_settings
    from app.services.ai_gateway_service import compute_cost_usd

    make_client()
    assert compute_cost_usd(get_settings(), "nope", 1000, 1000) == 0.0


# ── quota ────────────────────────────────────────────────────────────────────


def test_the_third_turn_is_refused_at_a_limit_of_two_and_never_forwarded(
    make_client, scripted_agent, tmp_path
):
    client = make_client(AI_DAILY_TURNS_LIMIT="2")
    sid = _mint(STAFF, allowed_modules='["ai"]')
    for _ in range(2):
        assert _turn(client, sid).status_code == 200
    assert len(scripted_agent["calls"]) == 2

    r = _turn(client, sid)
    assert r.status_code == 200  # the refusal is an event, not an HTTP status
    events = _parse(r.text)
    names = [e for e, _ in events]
    assert names == ["init", "error", "done"]
    err = dict(events)["error"]
    assert err["code"] == "quota_exceeded"
    assert "trace_id" in err
    assert len(scripted_agent["calls"]) == 2  # not forwarded

    rows = _audit_rows(tmp_path)
    assert [r["action"] for r in rows] == ["ai.query.submit"] * 3
    assert json.loads(rows[2]["new_value"])["error_code"] == "quota_exceeded"


def test_cost_limit_is_enforced_too(make_client, scripted_agent):
    client = make_client(AI_DAILY_COST_LIMIT_USD="0.004")
    sid = _mint(STAFF, allowed_modules='["ai"]')
    assert _turn(client, sid).status_code == 200  # spends 0.0046
    r = _turn(client, sid)
    assert dict(_parse(r.text))["error"]["code"] == "quota_exceeded"
    assert len(scripted_agent["calls"]) == 1


# ── audit ────────────────────────────────────────────────────────────────────


def test_every_turn_leaves_exactly_one_audit_row_with_the_contract_fields(
    make_client, scripted_agent, tmp_path
):
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    r = _turn(client, sid, session_id="sess-1")
    assert r.status_code == 200

    rows = _audit_rows(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "ai.query.submit"
    assert row["actor_email"] == STAFF  # from the session, never the body
    assert row["target"] == "ai_session:sess-1"
    value = json.loads(row["new_value"])
    assert value["question"] == "how is client 123?"
    assert value["model"] == "gpt-5.6-terra"
    assert value["tools_called"] == ["get_client_overview", "get_risk_signals"]
    assert value["subjects"] == ["client:123", "login:1-8522845"]
    assert value["terminal_reason"] == "end_turn"
    assert value["input_tokens"] == 1000
    assert value["output_tokens"] == 200
    assert value["cost_usd"] == pytest.approx(0.0046)
    assert value["scope_denied_count"] == 1
    assert "error_code" not in value


def test_a_scope_denied_tool_call_is_recorded_as_an_auth_event_by_the_main_api(
    make_client, scripted_agent, tmp_path
):
    """02 §2.1 wants refused tool calls in auth_events. The agent container
    cannot write users.db (its data mount is read-only), so the row is written
    HERE when the refusal arrives as `tool_done ok=false error_code=scope_denied`
    — exactly one row per refusal, throttled per person like every other
    permission_denied."""
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    assert _turn(client, sid, session_id="sess-scope").status_code == 200

    conn = sqlite3.connect(str(tmp_path / "users_test.db"))
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT event, email, detail FROM auth_events WHERE event = 'permission_denied' ORDER BY id"
            ).fetchall()
        ]
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["email"] == STAFF
    assert rows[0]["detail"].startswith("ai_tool_scope:get_risk_signals")
    value = json.loads(_audit_rows(tmp_path)[0]["new_value"])
    assert value["scope_denied_count"] == 1


def test_the_question_is_truncated_in_the_audit_row(make_client, scripted_agent, tmp_path):
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    _turn(client, sid, message="x" * 3000)
    value = json.loads(_audit_rows(tmp_path)[0]["new_value"])
    assert len(value["question"]) == 500


def test_the_route_defers_the_audit_missing_check(make_client, scripted_agent):
    """The flag is what keeps AuditMissing quiet on a streamed response; a
    route that stopped setting it would log AUDIT_MISSING on every turn."""
    from starlette.requests import Request

    from app.core.audit_missing_middleware import AuditMissingMiddleware
    from starlette.responses import Response

    scope = {
        "type": "http", "method": "POST", "path": "/api/v1/ai/turn",
        "headers": [], "query_string": b"", "route": None,
    }
    request = Request(scope)
    request.state.audit_deferred = True
    # Must not raise and must not warn: a plain call proves the early return
    # (the warning path reads request.scope["route"] which is None here and
    # would still produce a WARNING line if reached).
    make_client()
    AuditMissingMiddleware._check(request, Response(status_code=200))


# ── the agent being down ─────────────────────────────────────────────────────


def test_agent_unreachable_is_an_error_event_not_a_crash(
    make_client, scripted_agent, tmp_path
):
    scripted_agent["script"]["raise"] = httpx.ConnectError("boom")
    # `open_agent_stream` normally converts httpx errors itself; the fake
    # raises raw to prove the route also survives an unconverted exception.
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    r = _turn(client, sid)
    assert r.status_code == 200
    events = _parse(r.text)
    assert [e for e, _ in events] == ["init", "error", "done"]
    err = dict(events)["error"]
    assert err["code"] == "internal"  # raw exception -> internal
    rows = _audit_rows(tmp_path)
    assert len(rows) == 1
    assert json.loads(rows[0]["new_value"])["error_code"] == "internal"


def test_agent_unavailable_is_reported_as_such(make_client, scripted_agent, tmp_path):
    from app.services.ai_gateway_service import AgentUnavailable

    scripted_agent["script"]["raise"] = AgentUnavailable("agent unreachable")
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    r = _turn(client, sid)
    events = dict(_parse(r.text))
    assert events["error"]["code"] == "agent_unavailable"
    assert events["done"]["terminal_reason"] == "error"
    value = json.loads(_audit_rows(tmp_path)[0]["new_value"])
    assert value["error_code"] == "agent_unavailable"
    assert value["terminal_reason"] == "error"


def test_the_real_seam_converts_a_refused_connection(make_client, monkeypatch):
    """The un-faked open_agent_stream against a closed port."""
    import asyncio

    from app.core.config import get_settings
    from app.services.ai_gateway_service import AgentUnavailable, open_agent_stream

    make_client(AI_AGENT_URL="http://127.0.0.1:9")

    async def _drain():
        async for _ in open_agent_stream(get_settings(), {"x": 1}, token="t" * 40):
            pass

    with pytest.raises(AgentUnavailable):
        asyncio.run(_drain())


def test_a_stream_without_done_gets_a_synthetic_terminal(make_client, scripted_agent):
    scripted_agent["script"]["events"] = [("text", {"delta": "hi"})]
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    events = _parse(_turn(client, sid).text)
    assert [e for e, _ in events] == ["init", "text", "error", "done"]
    assert dict(events)["error"]["code"] == "internal"


def test_an_unconfigured_token_refuses_every_turn(make_client, scripted_agent, tmp_path):
    client = make_client(AI_AGENT_INTERNAL_TOKEN="short")
    sid = _mint(STAFF, allowed_modules='["ai"]')
    events = dict(_parse(_turn(client, sid).text))
    assert events["error"]["code"] == "agent_unavailable"
    assert scripted_agent["calls"] == []
    assert len(_audit_rows(tmp_path)) == 1


# ── the module key itself ────────────────────────────────────────────────────


def test_ai_is_a_grantable_module_with_bilingual_labels():
    from app.core.auth_deps import MODULE_MAP, classify_path
    from app.schemas.admin import MODULE_CATALOGUE, MODULE_KEYS

    assert "ai" in MODULE_KEYS
    entry = [m for m in MODULE_CATALOGUE if m.key == "ai"]
    assert len(entry) == 1 and entry[0].label_en and entry[0].label_zh
    assert MODULE_MAP[("ai",)] == "ai"
    assert classify_path("/api/v1/ai/turn") == "ai"
    assert classify_path("/api/v1/ai/usage/today") == "ai"


def test_ai_is_scoped_since_slice_two():
    """02 §9: every live /ai route is classified and `ai` is a covered module,
    so restricted colleagues may now be granted it (still not backfilled)."""
    from app.core.data_scope import FILTER, OPEN, ROUTE_SCOPE, SCOPED_MODULES

    assert "ai" in SCOPED_MODULES
    assert ROUTE_SCOPE["/ai/turn"] == FILTER
    assert ROUTE_SCOPE["/ai/usage/today"] == OPEN
    assert ROUTE_SCOPE["/ai/sessions"] == OPEN
    assert ROUTE_SCOPE["/ai/sessions/{session_id}"] == OPEN


def test_subject_labels_cover_group_tool_id_lists():
    from app.api.v1.routes.ai import _subject_labels

    assert _subject_labels({"subject": {"kind": "client_id", "value": "146530"}}) == ["client:146530"]
    assert _subject_labels({"subject": {"kind": "login_sid", "value": "1-8522845"}}) == ["login:1-8522845"]
    # get_client_overview's batch form: every subject becomes a label.
    assert _subject_labels(
        {"subjects": [{"kind": "client_id", "value": "1"}, {"kind": "login_sid", "value": "5-60001"}]}
    ) == ["client:1", "login:5-60001"]
    assert _subject_labels({"subjects": "nope"}) == []
    assert _subject_labels({"tab": "intraday-return", "client_ids": [1, 2]}) == ["client:1", "client:2"]
    assert _subject_labels({"alert_ids": [484606, 484753]}) == ["alert:484606", "alert:484753"]
    assert _subject_labels({"tab": "gap-trade", "client_ids": None}) == []
    assert _subject_labels(None) == []


def test_drill_down_subjects_include_the_resolved_clients(make_client, scripted_agent):
    """Cold review #5: `alert:<id>` stops resolving after the 30-day alert
    retention, so the clients the agent resolved land in the audit row too.
    Malformed labels from the agent are ignored."""
    from app.core.users_db import get_users_db

    scripted_agent["script"]["events"] = [
        ("tool_use", {"name": "get_alert_orders", "input": {"alert_ids": [484606, 484753], "max_orders_per_alert": 60}}),
        ("tool_done", {"name": "get_alert_orders", "ok": True, "source": {"function": "fetch_orders", "certified": True},
                       "certified": True, "subjects": ["client:166916", "client:162462", "email:x@y", "client:1;DROP"]}),
        ("text", {"delta": "orders"}),
        ("usage", {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0, "cost_usd": None}),
        ("done", {"terminal_reason": "end_turn", "num_turns": 1}),
    ]
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    assert _turn(client, sid).status_code == 200
    with get_users_db() as conn:
        row = conn.execute("SELECT new_value FROM audit_log WHERE action = 'ai.query.submit' ORDER BY id DESC LIMIT 1").fetchone()
    assert json.loads(row["new_value"])["subjects"] == [
        "alert:484606", "alert:484753", "client:166916", "client:162462"]


def test_a_quiet_agent_longer_than_the_keepalive_does_not_kill_the_turn(make_client, scripted_agent, monkeypatch):
    """2026-09-28 prod (trace req-4e221b39): a tool silent for > KEEPALIVE_SECONDS
    (rank_accounts over a month) ended the turn as "agent stream ended
    unexpectedly" — asyncio.wait_for cancelled the pending __anext__ of the
    agent stream, which finalises the async generator. The read must survive
    idle waits."""
    import asyncio

    from app.api.v1.routes import ai as ai_route
    from app.core.users_db import get_users_db
    from app.services import ai_gateway_service as gateway

    monkeypatch.setattr(ai_route, "KEEPALIVE_SECONDS", 0.05)

    async def _slow(settings, payload, *, token):
        yield ("tool_use", {"name": "rank_accounts", "input": {"metric": "win_rate"}})
        await asyncio.sleep(0.4)  # 8 keepalive periods of silence, like a 15s+ query
        yield ("tool_done", {"name": "rank_accounts", "ok": False, "error_code": "upstream_timeout"})
        yield ("text", {"delta": "narrow the window"})
        yield ("usage", {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0, "cost_usd": None})
        yield ("done", {"terminal_reason": "end_turn", "num_turns": 2})

    monkeypatch.setattr(gateway, "open_agent_stream", _slow)
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    r = _turn(client, sid)
    assert r.status_code == 200
    assert "ended unexpectedly" not in r.text
    assert "narrow the window" in r.text
    with get_users_db() as conn:
        row = conn.execute("SELECT new_value FROM audit_log WHERE action = 'ai.query.submit' ORDER BY id DESC LIMIT 1").fetchone()
    v = json.loads(row["new_value"])
    assert v["tools_called"] == ["rank_accounts"] and v.get("error_code") is None
