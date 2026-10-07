"""Contract tests for the AI agent's multi-model compare mode (OPT-0076).

Written from docs/ai-agent/02-contracts.md §19–§24 WITHOUT sight of the
implementation: everything is driven through HTTP and asserted on SSE frames,
HTTP status + `detail`, and rows read straight from the tmp SQLite files. The
only names relied on are the ones the contract fixes (table / column names of
the §21 DDL, the audit actions, the `detail` strings, the in-stream error
codes, the `AI_COMPARE_MAX_CONCURRENT` env var) or that already exist
(`open_agent_stream`, `init_ai_usage_db`, `purge_ai_sessions`,
`TURN_CLAIM_STALE_SECONDS`, `TURN_TOTAL_SECONDS`).

What is pinned here and why each one matters:

  * no `compare_models` -> the single-model stream, byte-for-byte: no `run`
    key anywhere, no `compare` event, the audit row keeps its `model` key;
  * `compare_models` is 2–3 distinct known deployments, otherwise 422 and the
    agent is never reached;
  * the fan-out is N calls whose payloads differ ONLY in `model` — so the
    caller's scope and tool gating cannot vary between columns;
  * every agent event is tagged with its `run`; `session_state` /
    `skill_loaded` still never reach the browser; the stream ends with one
    `compare` event and a run-less `done`;
  * while a compare is pending NOTHING is written to `ai_messages` or
    `ai_sessions.blob` — the prod build that predates the two new tables
    shares this file and must see "this turn has not happened";
  * a pending compare blocks the next turn (409 `compare pending`) whether or
    not that turn asks for compare;
  * quota counts one turn per model and refuses before the agent is called;
    cost is the sum, each run priced with its own model's row;
  * `select` writes the chosen blob + two transcript rows in one go, clears
    EVERY candidate blob, is idempotent, and refuses a session that moved on
    (`compare stale`);
  * one `ai.query.submit` row per compare turn on every path, small enough to
    stay parseable under `audit.MAX_VALUE_LEN`.

Harness is the one of test_ai_route.py (AUTH_* env pinned per test, users_db
and ai_usage_db redirected to tmp, the REAL ai router behind the real gates),
copied rather than imported so this file stands alone. The scripted agent
branches on `payload["model"]`, so each run gets its own script, and records
every payload it was called with.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

MANAGER = "boss@kohleservices.com"
STAFF = "staff@kohleservices.com"
OTHER = "other@kohleservices.com"

TERRA = "gpt-5.6-terra"
GROK = "grok-4.7"
DEEPSEEK = "DeepSeek-V4-Pro"
SOL = "gpt-5.6-sol"

# Pinned in the fixture so a change to the built-in price table cannot move
# these tests. USD per MTok: [input, output] or [input, output, cached input];
# without the third value cached input bills at 10% of the input price.
PRICES: dict[str, list[float]] = {
    TERRA: [2.0, 12.0],
    GROK: [2.0, 6.0, 0.5],
    DEEPSEEK: [1.74, 3.48, 0.145],
    SOL: [4.0, 20.0],
    "gpt-6.1-sol": [2.0, 10.0],
}

# (input_tokens, output_tokens, cache_read_input_tokens) each model reports.
# Different per model so a sum or a mis-attributed price is visible.
TOKENS: dict[str, tuple[int, int, int]] = {
    TERRA: (1000, 200, 400),
    GROK: (2000, 300, 0),
    DEEPSEEK: (500, 100, 100),
    SOL: (100, 10, 0),
    "gpt-6.1-sol": (100, 10, 0),
}

# The tool each model's default script calls, so `runs[].tools_called` can be
# told apart per model and the merged list shows de-duplication.
TOOL: dict[str, str] = {
    TERRA: "get_client_overview",
    GROK: "get_trade_activity",
    DEEPSEEK: "get_client_overview",
    SOL: "get_client_overview",
    "gpt-6.1-sol": "get_client_overview",
}

# What `session_state.turns` each model reports; `select` must carry the
# chosen one into ai_sessions.turns.
STATE_TURNS: dict[str, int] = {TERRA: 3, GROK: 5, DEEPSEEK: 4, SOL: 1, "gpt-6.1-sol": 1}

# Event names of one healthy run as the browser must see them (the scripted
# stream minus `session_state`).
OK_RUN_EVENTS = ["text", "tool_use", "tool_done", "text", "usage", "done"]

# §24: the only keys a `runs[]` item may carry (`error_code` on failure only).
RUN_KEYS = {
    "model", "tools_called", "terminal_reason",
    "input_tokens", "output_tokens", "cost_usd", "error_code",
}
# §24: every key the compare-turn audit row may carry.
COMPARE_AUDIT_KEYS = {
    "question", "compare_id", "models", "runs", "tools_called", "skills_loaded",
    "subjects", "sql", "terminal_reason", "input_tokens", "output_tokens",
    "cost_usd", "scope_denied_count", "resumed", "error_code",
}

QUESTION = "how is client 123?"


def _cost(model: str) -> float:
    """Expected USD for one default run of `model`, from PRICES — computed
    here, independently of the code under test."""
    i, o, c = TOKENS[model]
    row = PRICES[model]
    cached_price = row[2] if len(row) > 2 else row[0] * 0.10
    return ((i - c) * row[0] + c * cached_price + o * row[1]) / 1_000_000


def _blob(model: str) -> dict:
    """A distinct framework-session blob per model. The marker string is what
    lets a test assert WHICH blob landed and that none leaked to the browser."""
    return {"marker": f"BLOB-MARKER-{model}", "state": {"messages": [{"role": "assistant", "by": model}]}}


BLOB_BASE = {"marker": "BLOB-MARKER-base", "state": {"messages": [{"role": "user", "text": "hi"}]}}


def _ok(model: str, *, blob: Any = "__default__", skill: str | None = None) -> list[Any]:
    """One healthy run: text, a tool call, more text, usage, state, done."""
    i, o, c = TOKENS[model]
    tool = TOOL[model]
    events: list[Any] = []
    if skill is not None:
        events.append(("skill_loaded", {"skill": skill, "resource": None}))
    events += [
        ("text", {"delta": f"{model} says "}),
        ("tool_use", {"name": tool, "input": {"subject": {"kind": "client_id", "value": "123"}}}),
        ("tool_done", {"name": tool, "ok": True, "source": {"function": tool}, "certified": True}),
        ("text", {"delta": "all good."}),
        ("usage", {"input_tokens": i, "output_tokens": o, "cache_read_input_tokens": c, "cost_usd": None}),
        ("session_state", {"blob": _blob(model) if blob == "__default__" else blob, "turns": STATE_TURNS[model]}),
        ("done", {"terminal_reason": "end_turn", "num_turns": 2}),
    ]
    return events


def _answer(model: str) -> str:
    return f"{model} says all good."


def _failed(code: str = "rate_limited") -> list[Any]:
    """A run that errors with no usable answer: `error`, then `done`."""
    return [
        ("error", {"code": code, "message": "upstream said no"}),
        ("done", {"terminal_reason": "error", "num_turns": 0}),
    ]


def _parse(text: str) -> list[tuple[str, Any]]:
    """Whole SSE body -> [(event, data)], comment / keepalive blocks dropped."""
    out: list[tuple[str, Any]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        event, data_lines = None, []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        if event is not None:
            out.append((event, json.loads("\n".join(data_lines)) if data_lines else None))
    return out


def _by_run(events: list[tuple[str, Any]]) -> dict[str, list[tuple[str, Any]]]:
    """Frames that belong to one column, keyed by their `run`."""
    out: dict[str, list[tuple[str, Any]]] = {}
    for event, data in events:
        if isinstance(data, dict) and "run" in data:
            out.setdefault(data["run"], []).append((event, data))
    return out


def _whole_turn(events: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    """Frames about the turn as a whole: the ones without a `run`."""
    return [(e, d) for e, d in events if not (isinstance(d, dict) and "run" in d)]


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
        monkeypatch.setenv("AI_MODEL_PRICES", json.dumps(PRICES))
        monkeypatch.setenv("AI_COMPARE_MAX_CONCURRENT", "2")
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
def agent(monkeypatch):
    """Replace the httpx seam with a per-model scripted stream.

    `scripts[model]` is a list of `(event, data)` items (a bare number in the
    list is a sleep in seconds) or an exception to raise before the first
    event. `calls` records every payload the seam was opened with, in order.
    """
    from app.services import ai_gateway_service as gateway

    calls: list[dict] = []
    scripts: dict[str, Any] = {model: _ok(model) for model in TOKENS}

    async def _fake(settings, payload, *, token):
        calls.append(payload)
        plan = scripts[payload["model"]]
        if isinstance(plan, BaseException):
            raise plan
        for item in plan:
            if isinstance(item, (int, float)):
                await asyncio.sleep(item)
                continue
            yield item
            # Hand the loop over so concurrent runs really interleave.
            await asyncio.sleep(0)

    monkeypatch.setattr(gateway, "open_agent_stream", _fake)
    return {"calls": calls, "scripts": scripts}


def _bearer(sid: str) -> dict:
    return {"Authorization": f"Bearer {sid}"}


def _mint(email: str, *, allowed_modules: str | None = '["ai"]') -> str:
    from app.core.users_db import get_users_db
    from app.services import auth_service

    sid, user = auth_service.login(email, source="dev")
    with get_users_db() as conn:
        conn.execute("UPDATE users SET allowed_modules = ? WHERE id = ?", (allowed_modules, user.user_id))
    return sid


def _uid(tmp_path, email: str) -> int:
    conn = sqlite3.connect(str(tmp_path / "users_test.db"))
    try:
        return int(conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()[0])
    finally:
        conn.close()


def _audit_rows(tmp_path, action: str | None = None) -> list[dict]:
    conn = sqlite3.connect(str(tmp_path / "users_test.db"))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT action, actor_email, target, old_value, new_value FROM audit_log ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows if action is None or r["action"] == action]


def _db(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "ai_agent_test.db"))
    conn.row_factory = sqlite3.Row
    return conn


def _one(tmp_path, sql: str, params: tuple = ()) -> Any:
    conn = _db(tmp_path)
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _all(tmp_path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = _db(tmp_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _exec(tmp_path, sql: str, params: tuple = ()) -> None:
    conn = _db(tmp_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _iso(delta_seconds: float = 0.0) -> str:
    """UTC ISO8601 with a trailing Z, `delta_seconds` from now."""
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _insert_compare(
    tmp_path,
    *,
    compare_id: str,
    session_id: str,
    user_id: int,
    state: str,
    created_at: str,
    models: list[str] | None = None,
    base_seq: int = 0,
) -> None:
    """A compare-turn row the API cannot be made to produce on demand (another
    worker's turn in flight, a worker that died). Columns are the §21 DDL."""
    _exec(
        tmp_path,
        "INSERT INTO ai_compare_turns "
        "(compare_id, session_id, user_id, question, models_json, base_seq, state, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (compare_id, session_id, user_id, "somebody else's question",
         json.dumps(models or [TERRA, GROK]), base_seq, state, created_at),
    )


def _turn(client: TestClient, sid: str, **body: Any):
    return client.post("/api/v1/ai/turn", json={"message": QUESTION, **body}, headers=_bearer(sid))


def _compare(client: TestClient, sid: str, models: list[str], **body: Any):
    return _turn(client, sid, compare_models=models, **body)


def _select(client: TestClient, sid: str, session_id: str, compare_id: str, model: str, **extra: Any):
    return client.post(
        f"/api/v1/ai/sessions/{session_id}/select",
        json={"compare_id": compare_id, "model": model, **extra},
        headers=_bearer(sid),
    )


def _detail(client: TestClient, sid: str, session_id: str) -> dict:
    r = client.get(f"/api/v1/ai/sessions/{session_id}", headers=_bearer(sid))
    assert r.status_code == 200
    return r.json()


def _list_row(client: TestClient, sid: str, session_id: str) -> dict:
    body = client.get("/api/v1/ai/sessions", headers=_bearer(sid)).json()
    return [s for s in body["data"] if s["session_id"] == session_id][0]


def _usage_today(client: TestClient, sid: str) -> dict:
    return client.get("/api/v1/ai/usage/today", headers=_bearer(sid)).json()


def _pending(client: TestClient, sid: str, session_id: str, models: list[str], **body: Any) -> str:
    """Run a compare turn that must end pending; returns its compare_id."""
    r = _compare(client, sid, models, session_id=session_id, **body)
    assert r.status_code == 200
    events = _parse(r.text)
    compare = [d for e, d in events if e == "compare"]
    assert len(compare) == 1 and compare[0]["state"] == "pending"
    return compare[0]["compare_id"]


# ── single-model path: unchanged (these pass before the feature exists) ──────


@pytest.mark.parametrize("body", [{}, {"compare_models": None}], ids=["absent", "null"])
def test_without_compare_models_no_frame_carries_a_run(make_client, agent, tmp_path, body):
    """§19: absent / null `compare_models` is the single-model turn, and its
    stream is what it was before — `run` appears nowhere, there is no
    `compare` event, `init` has exactly its two keys."""
    client = make_client()
    sid = _mint(STAFF)
    r = _turn(client, sid, session_id="single", **body)
    assert r.status_code == 200
    events = _parse(r.text)
    names = [e for e, _ in events]
    assert names == ["init", *OK_RUN_EVENTS]
    assert "compare" not in names
    assert all("run" not in d for _, d in events)
    assert events[0][1] == {"session_id": "single", "model": TERRA}
    assert "session_state" not in names

    assert len(agent["calls"]) == 1
    assert agent["calls"][0]["model"] == TERRA


def test_the_single_model_audit_row_keeps_its_shape(make_client, agent, tmp_path):
    """§24: "单模型轮次的行形状不变" — top-level `model`, none of the compare keys."""
    client = make_client()
    sid = _mint(STAFF)
    assert _turn(client, sid, session_id="single").status_code == 200
    rows = _audit_rows(tmp_path)
    assert [r["action"] for r in rows] == ["ai.query.submit"]
    value = json.loads(rows[0]["new_value"])
    assert value["model"] == TERRA
    assert not {"models", "runs", "compare_id"} & set(value)
    assert value["terminal_reason"] == "end_turn"


def test_the_single_model_turn_stores_the_blob_and_two_rows_at_once(make_client, agent, tmp_path):
    """The deferral to `select` is a compare-only rule: a plain turn still
    writes its blob and transcript when the stream ends."""
    client = make_client()
    sid = _mint(STAFF)
    assert _turn(client, sid, session_id="single").status_code == 200
    row = _one(tmp_path, "SELECT blob, turns FROM ai_sessions WHERE session_id = 'single'")
    assert json.loads(row["blob"]) == _blob(TERRA)
    assert row["turns"] == STATE_TURNS[TERRA]
    assert _one(tmp_path, "SELECT COUNT(*) FROM ai_messages WHERE session_id = 'single'")[0] == 2


def test_only_the_turn_route_is_session_only():
    """§22: the select route takes the ordinary API key; nothing new joins
    SESSION_ONLY_PATHS (which would also need an nginx location)."""
    from app.core.api_key_middleware import SESSION_ONLY_PATHS

    assert SESSION_ONLY_PATHS == frozenset({"/api/v1/ai/turn"})


# ── single-model path: what OPT-0076 adds to it ──────────────────────────────


def test_a_single_model_turn_records_the_model_on_the_assistant_row(make_client, agent, tmp_path):
    """§21: `ai_messages.model` is written for single-model turns too — a
    conversation that switches model between turns must stay readable."""
    client = make_client()
    sid = _mint(STAFF)
    assert _turn(client, sid, session_id="mix").status_code == 200
    assert _turn(client, sid, session_id="mix", model=GROK).status_code == 200

    rows = _all(tmp_path, "SELECT seq, role, model, compare_id FROM ai_messages WHERE session_id = 'mix' ORDER BY seq")
    assistants = [r for r in rows if r["role"] == "assistant"]
    assert [r["model"] for r in assistants] == [TERRA, GROK]
    assert all(r["compare_id"] is None for r in rows)

    msgs = _detail(client, sid, "mix")["messages"]
    assert [m.get("model") for m in msgs if m["role"] == "assistant"] == [TERRA, GROK]
    assert all(m.get("model") is None for m in msgs if m["role"] == "user")
    assert all(m.get("compare") is None for m in msgs)


def test_a_session_without_a_compare_reports_none_pending(make_client, agent):
    client = make_client()
    sid = _mint(STAFF)
    assert _turn(client, sid, session_id="plain").status_code == 200
    assert _detail(client, sid, "plain").get("pending_compare") is None
    assert not _list_row(client, sid, "plain").get("pending_compare")


def test_the_claim_outlives_the_longest_turn():
    """§21: a claim that goes stale before a turn may legitimately end lets a
    second tab take the session mid-turn (it was 360 s against 560 s)."""
    from app.core.ai_usage_db import TURN_CLAIM_STALE_SECONDS
    from app.services.ai_gateway_service import TURN_TOTAL_SECONDS

    assert TURN_CLAIM_STALE_SECONDS > TURN_TOTAL_SECONDS


def test_the_compare_concurrency_setting_defaults_to_two_and_reads_env(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.delenv("AI_COMPARE_MAX_CONCURRENT", raising=False)
    get_settings.cache_clear()
    assert get_settings().AI_COMPARE_MAX_CONCURRENT == 2
    monkeypatch.setenv("AI_COMPARE_MAX_CONCURRENT", "5")
    get_settings.cache_clear()
    assert get_settings().AI_COMPARE_MAX_CONCURRENT == 5
    get_settings.cache_clear()


# ── request validation (§19) ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "models",
    [
        [],
        [TERRA],
        [TERRA, GROK, DEEPSEEK, SOL],
        [TERRA, TERRA],
        [TERRA, GROK, GROK],
        [TERRA, "gpt-4o"],
        [TERRA, "gpt-5.6-luna"],  # a real deployment, deliberately not selectable
        "grok-4.7",
    ],
    ids=["empty", "one", "four", "duplicate", "duplicate-of-three", "unknown", "luna", "not-a-list"],
)
def test_compare_models_must_be_two_or_three_distinct_known_models(make_client, agent, tmp_path, models):
    client = make_client()
    sid = _mint(STAFF)
    r = _turn(client, sid, session_id="bad", compare_models=models)
    assert r.status_code == 422
    assert agent["calls"] == []
    # Refused before anything was created or counted.
    assert _one(tmp_path, "SELECT COUNT(*) FROM ai_sessions")[0] == 0
    assert _audit_rows(tmp_path) == []
    assert _usage_today(client, sid)["turns"] == 0


def test_model_is_ignored_when_compare_models_is_given(make_client, agent):
    """§19: with `compare_models` present `model` carries no meaning — it must
    not add a run, and `init.model` is the first column."""
    client = make_client()
    sid = _mint(STAFF)
    r = _compare(client, sid, [TERRA, GROK], model=SOL, session_id="ign")
    assert r.status_code == 200
    assert sorted(c["model"] for c in agent["calls"]) == sorted([TERRA, GROK])
    assert dict(_parse(r.text))["init"]["model"] == TERRA


def test_compare_is_behind_the_ai_module_gate(make_client, agent):
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["cs"]')
    assert _compare(client, sid, [TERRA, GROK]).status_code == 403
    assert _select(client, sid, "x", "y", TERRA).status_code == 403
    assert agent["calls"] == []


# ── the fan-out (§19, §4.2 untouched) ────────────────────────────────────────


def test_two_models_mean_two_agent_calls_differing_only_in_model(make_client, agent):
    client = make_client()
    sid = _mint(STAFF)
    assert _compare(client, sid, [TERRA, GROK], session_id="fan").status_code == 200

    calls = agent["calls"]
    assert len(calls) == 2
    assert sorted(c["model"] for c in calls) == sorted([TERRA, GROK])
    # The internal interface is unchanged: same keys as a single-model turn.
    for payload in calls:
        assert set(payload) == {"caller", "scope", "session_id", "message", "model", "trace_id", "session_blob"}
    first, second = ({k: v for k, v in c.items() if k != "model"} for c in calls)
    assert first == second
    assert first["session_id"] == "fan"
    assert first["message"] == QUESTION
    assert first["caller"]["email"] == STAFF
    assert first["caller"]["allowed_modules"] == ["ai"]
    assert first["scope"] is None
    assert first["session_blob"] is None


def test_three_models_mean_three_calls(make_client, agent):
    client = make_client()
    sid = _mint(STAFF)
    assert _compare(client, sid, [TERRA, GROK, DEEPSEEK]).status_code == 200
    assert sorted(c["model"] for c in agent["calls"]) == sorted([TERRA, GROK, DEEPSEEK])


def test_every_run_starts_from_the_same_stored_blob(make_client, agent):
    """The parent blob is the session's, handed to every run alike."""
    agent["scripts"][TERRA] = _ok(TERRA, blob=BLOB_BASE)
    client = make_client()
    sid = _mint(STAFF)
    assert _turn(client, sid, session_id="base").status_code == 200
    agent["scripts"][TERRA] = _ok(TERRA)

    assert _compare(client, sid, [TERRA, GROK], session_id="base").status_code == 200
    compare_calls = agent["calls"][1:]
    assert len(compare_calls) == 2
    assert all(c["session_blob"] == BLOB_BASE for c in compare_calls)


@pytest.mark.parametrize("cids,expected", [(frozenset({1}), [1]), (frozenset(), [])], ids=["global-only", "sees-nothing"])
def test_a_restricted_callers_scope_reaches_every_run_as_a_list(make_client, agent, monkeypatch, cids, expected):
    """§19: restricted colleagues may compare, and the scope that filters
    inside the agent must be on EVERY run — a list, never null, never
    collapsed (`[]` and `None` are opposite grants and both falsy)."""
    from app.core import data_scope

    monkeypatch.setitem(data_scope.DATA_SCOPE_OVERRIDES, STAFF, cids)
    client = make_client()
    sid = _mint(STAFF)
    r = _compare(client, sid, [TERRA, GROK, DEEPSEEK])
    assert r.status_code == 200
    assert len(agent["calls"]) == 3
    assert [c["scope"] for c in agent["calls"]] == [expected, expected, expected]


# ── the stream (§20) ─────────────────────────────────────────────────────────


def test_init_announces_the_compare_and_its_column_order(make_client, agent):
    client = make_client()
    sid = _mint(STAFF)
    r = _compare(client, sid, [GROK, TERRA], session_id="ord")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _parse(r.text)
    assert events[0][0] == "init"
    init = events[0][1]
    assert set(init) == {"session_id", "model", "compare"}
    assert init["session_id"] == "ord"
    assert init["model"] == GROK  # models[0], not the default
    assert set(init["compare"]) == {"compare_id", "models"}
    assert init["compare"]["models"] == [GROK, TERRA]
    assert isinstance(init["compare"]["compare_id"], str) and init["compare"]["compare_id"]


def test_every_agent_event_carries_its_run(make_client, agent):
    client = make_client()
    sid = _mint(STAFF)
    r = _compare(client, sid, [TERRA, GROK])
    events = _parse(r.text)
    runs = _by_run(events)
    assert set(runs) == {TERRA, GROK}
    for model in (TERRA, GROK):
        frames = runs[model]
        # Each column is its own script, in order, whatever the interleaving.
        assert [e for e, _ in frames] == OK_RUN_EVENTS
        text = "".join(d["delta"] for e, d in frames if e == "text")
        assert text == _answer(model)
        assert [d["name"] for e, d in frames if e == "tool_use"] == [TOOL[model]]
        tool_done = [d for e, d in frames if e == "tool_done"][0]
        assert tool_done["ok"] is True and tool_done["certified"] is True
        usage = [d for e, d in frames if e == "usage"][0]
        assert usage["input_tokens"] == TOKENS[model][0]
        assert usage["output_tokens"] == TOKENS[model][1]
        done = frames[-1][1]
        assert done["terminal_reason"] == "end_turn"
        assert done["num_turns"] == 2
        assert isinstance(done["elapsed_ms"], int) and done["elapsed_ms"] >= 0
    # Nothing an agent sent is left untagged: the run-less frames are only the
    # three the main API produces itself.
    assert [e for e, _ in _whole_turn(events)] == ["init", "compare", "done"]


def test_the_stream_ends_with_compare_then_a_run_less_done(make_client, agent):
    client = make_client()
    sid = _mint(STAFF)
    events = _parse(_compare(client, sid, [TERRA, GROK]).text)
    (compare_event, compare), (done_event, done) = events[-2:]
    assert compare_event == "compare" and done_event == "done"
    assert set(compare) == {"compare_id", "state", "selectable"}
    assert compare["compare_id"] == events[0][1]["compare"]["compare_id"]
    assert compare["state"] == "pending"
    assert sorted(compare["selectable"]) == sorted([TERRA, GROK])
    assert "run" not in done
    assert done["terminal_reason"] == "compare_pending"
    assert done["num_turns"] == 0
    assert [e for e, _ in events].count("compare") == 1


def test_session_state_and_skill_loaded_never_reach_the_browser(make_client, agent, tmp_path):
    agent["scripts"][GROK] = _ok(GROK, skill="margin-and-stopout")
    client = make_client()
    sid = _mint(STAFF)
    r = _compare(client, sid, [TERRA, GROK])
    names = [e for e, _ in _parse(r.text)]
    assert "session_state" not in names
    assert "skill_loaded" not in names
    # The blobs are the framework's private format with raw tool results.
    assert "BLOB-MARKER" not in r.text
    # ...but the skill load still lands in the audit row, merged across runs.
    value = json.loads(_audit_rows(tmp_path, "ai.query.submit")[0]["new_value"])
    assert value["skills_loaded"] == ["margin-and-stopout"]


def test_each_runs_usage_is_priced_with_its_own_model(make_client, agent):
    """§20: `usage.cost_usd` per run uses that run's price row — three models
    with three different rows (one with an explicit cached-input price)."""
    client = make_client()
    sid = _mint(STAFF)
    runs = _by_run(_parse(_compare(client, sid, [TERRA, GROK, DEEPSEEK]).text))
    for model in (TERRA, GROK, DEEPSEEK):
        usage = [d for e, d in runs[model] if e == "usage"][0]
        assert usage["cost_usd"] == pytest.approx(_cost(model), abs=1e-6)
    assert _cost(TERRA) == pytest.approx(0.00368)  # the figure test_ai_route.py pins
    assert len({round(_cost(m), 6) for m in (TERRA, GROK, DEEPSEEK)}) == 3


# ── partial and total failure (§20) ──────────────────────────────────────────


def test_one_failed_run_leaves_the_turn_pending_with_only_the_good_one_selectable(make_client, agent, tmp_path):
    agent["scripts"][GROK] = _failed("rate_limited")
    client = make_client()
    sid = _mint(STAFF)
    r = _compare(client, sid, [TERRA, GROK], session_id="part")
    assert r.status_code == 200
    events = _parse(r.text)
    runs = _by_run(events)

    # The failed column: its error and its done, both tagged.
    assert [e for e, _ in runs[GROK]] == ["error", "done"]
    error = runs[GROK][0][1]
    assert error["code"] == "rate_limited"
    assert error["trace_id"]
    assert runs[GROK][1][1]["terminal_reason"] == "error"
    # The other column is untouched by it.
    assert [e for e, _ in runs[TERRA]] == OK_RUN_EVENTS

    compare = [d for e, d in events if e == "compare"][0]
    assert compare["state"] == "pending"
    assert compare["selectable"] == [TERRA]
    assert events[-1] == ("done", {"terminal_reason": "compare_pending", "num_turns": 0})
    # No whole-turn error: one column failing is that column's business.
    assert [e for e, _ in _whole_turn(events)] == ["init", "compare", "done"]

    pending = _detail(client, sid, "part")["pending_compare"]
    by_model = {c["model"]: c for c in pending["candidates"]}
    assert by_model[TERRA]["selectable"] is True and by_model[TERRA]["error_code"] is None
    assert by_model[GROK]["selectable"] is False and by_model[GROK]["error_code"] == "rate_limited"

    failed = _one(tmp_path, "SELECT blob, error_code FROM ai_turn_candidates WHERE model = ?", (GROK,))
    assert failed["blob"] is None and failed["error_code"] == "rate_limited"


def test_a_run_whose_agent_is_unreachable_fails_alone(make_client, agent):
    """The seam raising for one model (connection refused, 5xx) is that run's
    `error` + `done`; the other run carries on and the stream ends cleanly."""
    from app.services.ai_gateway_service import AgentUnavailable

    agent["scripts"][GROK] = AgentUnavailable("agent unreachable")
    client = make_client()
    sid = _mint(STAFF)
    r = _compare(client, sid, [TERRA, GROK])
    assert r.status_code == 200
    events = _parse(r.text)
    runs = _by_run(events)
    errors = [d for e, d in runs[GROK] if e == "error"]
    assert [d["code"] for d in errors] == ["agent_unavailable"]
    assert runs[GROK][-1][0] == "done" and runs[GROK][-1][1]["terminal_reason"] == "error"
    assert [e for e, _ in runs[TERRA]] == OK_RUN_EVENTS
    compare = [d for e, d in events if e == "compare"][0]
    assert compare["state"] == "pending" and compare["selectable"] == [TERRA]


def test_a_run_without_a_blob_or_without_text_is_not_selectable(make_client, agent):
    """§20: selectable = has a blob AND no error AND non-empty text. A run that
    answered but never sent `session_state` cannot be continued from; a run
    that sent state but said nothing has nothing to choose."""
    agent["scripts"][GROK] = [e for e in _ok(GROK) if e[0] != "session_state"]
    agent["scripts"][DEEPSEEK] = [e for e in _ok(DEEPSEEK) if e[0] != "text"]
    client = make_client()
    sid = _mint(STAFF)
    session_id = "sel"
    r = _compare(client, sid, [TERRA, GROK, DEEPSEEK], session_id=session_id)
    compare = [d for e, d in _parse(r.text) if e == "compare"][0]
    assert compare["state"] == "pending"
    assert compare["selectable"] == [TERRA]
    by_model = {c["model"]: c for c in _detail(client, sid, session_id)["pending_compare"]["candidates"]}
    assert [m for m, c in by_model.items() if c["selectable"]] == [TERRA]
    for model in (GROK, DEEPSEEK):
        assert _select(client, sid, session_id, compare["compare_id"], model).status_code == 422


def test_all_runs_failing_voids_the_turn(make_client, agent, tmp_path):
    """§20: nothing selectable -> `void`, handled as a failed turn: the two
    transcript rows are written now (`compare_failed`), the session is NOT
    pending, and the person can simply ask again."""
    agent["scripts"][TERRA] = _failed("rate_limited")
    agent["scripts"][GROK] = _failed("internal")
    client = make_client()
    sid = _mint(STAFF)
    r = _compare(client, sid, [TERRA, GROK], session_id="void")
    assert r.status_code == 200
    events = _parse(r.text)

    compare = [d for e, d in events if e == "compare"][0]
    assert compare["state"] == "void"
    assert compare["selectable"] == []
    assert events[-1][0] == "done"
    assert "run" not in events[-1][1]
    assert events[-1][1]["terminal_reason"] == "error"
    # If the whole-turn failure is also announced as an `error` frame, it is
    # this code and no other.
    whole_errors = [d for e, d in _whole_turn(events) if e == "error"]
    assert all(d["code"] == "compare_failed" for d in whole_errors)

    rows = _all(tmp_path, "SELECT seq, role, text, error_code FROM ai_messages WHERE session_id = 'void' ORDER BY seq")
    assert [(r["seq"], r["role"]) for r in rows] == [(1, "user"), (2, "assistant")]
    assert rows[0]["text"] == QUESTION
    assert rows[1]["error_code"] == "compare_failed"

    turn = _one(tmp_path, "SELECT state FROM ai_compare_turns WHERE compare_id = ?", (compare["compare_id"],))
    assert turn["state"] == "void"

    detail = _detail(client, sid, "void")
    assert detail.get("pending_compare") is None
    assert not _list_row(client, sid, "void").get("pending_compare")
    # §23: the failed assistant row still shows every run that was tried.
    failed_row = detail["messages"][-1]
    assert failed_row["error_code"] == "compare_failed"
    assert failed_row["compare"]["compare_id"] == compare["compare_id"]
    alternatives = {a["model"]: a for a in failed_row["compare"]["alternatives"]}
    assert set(alternatives) == {TERRA, GROK}
    assert alternatives[TERRA]["error_code"] == "rate_limited"
    assert alternatives[GROK]["error_code"] == "internal"

    # Not pending: the next turn is served.
    agent["scripts"][TERRA] = _ok(TERRA)
    calls_before = len(agent["calls"])
    assert _turn(client, sid, session_id="void").status_code == 200
    assert len(agent["calls"]) == calls_before + 1


# ── what a pending compare leaves behind (§21, §23) ──────────────────────────


def test_a_pending_compare_touches_neither_the_blob_nor_the_transcript(make_client, agent, tmp_path):
    """The rule that makes the shared dev/prod file safe: until `select`, the
    session row's blob and `ai_messages` are exactly as the previous turn left
    them, and the question lives only in `ai_compare_turns`."""
    agent["scripts"][TERRA] = _ok(TERRA, blob=BLOB_BASE)
    client = make_client()
    sid = _mint(STAFF)
    assert _turn(client, sid, session_id="keep").status_code == 200
    agent["scripts"][TERRA] = _ok(TERRA)
    before = dict(_one(tmp_path, "SELECT blob, turns, model FROM ai_sessions WHERE session_id = 'keep'"))
    assert json.loads(before["blob"]) == BLOB_BASE

    compare_id = _pending(client, sid, "keep", [TERRA, GROK], message="and the last 7 days?")

    after = _one(tmp_path, "SELECT blob, turns, model, turn_started_at FROM ai_sessions WHERE session_id = 'keep'")
    assert after["blob"] == before["blob"]
    assert after["turns"] == before["turns"]
    assert after["turn_started_at"] is None  # the claim is released; `pending` is what blocks now
    msgs = _all(tmp_path, "SELECT seq, text FROM ai_messages WHERE session_id = 'keep' ORDER BY seq")
    assert [m["seq"] for m in msgs] == [1, 2]
    assert all("last 7 days" not in m["text"] for m in msgs)

    turn = _one(tmp_path, "SELECT * FROM ai_compare_turns WHERE compare_id = ?", (compare_id,))
    assert turn["session_id"] == "keep"
    assert turn["user_id"] == _uid(tmp_path, STAFF)
    assert turn["question"] == "and the last 7 days?"
    assert json.loads(turn["models_json"]) == [TERRA, GROK]
    assert turn["base_seq"] == 2
    assert turn["state"] == "pending"
    assert turn["created_at"] and turn["finished_at"]
    assert turn["selected_model"] is None and turn["selected_at"] is None and turn["reason"] is None


def test_the_candidates_table_holds_one_row_per_run_with_its_own_blob(make_client, agent, tmp_path):
    client = make_client()
    sid = _mint(STAFF)
    compare_id = _pending(client, sid, "cand", [GROK, TERRA, DEEPSEEK])

    rows = _all(tmp_path, "SELECT * FROM ai_turn_candidates WHERE compare_id = ? ORDER BY position", (compare_id,))
    assert [r["model"] for r in rows] == [GROK, TERRA, DEEPSEEK]  # position = column order
    assert len({r["position"] for r in rows}) == 3
    for row in rows:
        model = row["model"]
        assert row["text"] == _answer(model)
        assert json.loads(row["blob"]) == _blob(model)
        assert row["turns"] == STATE_TURNS[model]
        assert row["error_code"] is None
        assert row["selected"] == 0
        assert isinstance(row["elapsed_ms"], int) and row["elapsed_ms"] >= 0
        assert [t["name"] for t in json.loads(row["tools_json"])] == [TOOL[model]]
        usage = json.loads(row["usage_json"])
        assert usage["input_tokens"] == TOKENS[model][0]
        assert usage["output_tokens"] == TOKENS[model][1]
        assert usage["cost_usd"] == pytest.approx(_cost(model), abs=1e-6)


def test_detail_and_list_show_the_pending_compare_without_any_blob(make_client, agent):
    client = make_client()
    sid = _mint(STAFF)
    compare_id = _pending(client, sid, "show", [TERRA, GROK])

    r = client.get("/api/v1/ai/sessions/show", headers=_bearer(sid))
    assert r.status_code == 200
    assert "BLOB-MARKER" not in r.text
    body = r.json()
    assert body["messages"] == []  # the turn has not happened yet, transcript-wise

    pending = body["pending_compare"]
    assert set(pending) == {"compare_id", "state", "question", "models", "created_at", "candidates"}
    assert pending["compare_id"] == compare_id
    assert pending["state"] == "pending"
    assert pending["question"] == QUESTION
    assert pending["models"] == [TERRA, GROK]
    assert pending["created_at"].endswith("Z")
    assert [c["model"] for c in pending["candidates"]] == [TERRA, GROK]
    for cand in pending["candidates"]:
        model = cand["model"]
        assert set(cand) == {"model", "text", "tools", "usage", "error_code", "elapsed_ms", "selectable"}
        assert cand["text"] == _answer(model)
        assert [t["name"] for t in cand["tools"]] == [TOOL[model]]
        assert cand["usage"]["cost_usd"] == pytest.approx(_cost(model), abs=1e-6)
        assert cand["error_code"] is None
        assert cand["selectable"] is True

    assert _list_row(client, sid, "show")["pending_compare"] is True
    listing = client.get("/api/v1/ai/sessions", headers=_bearer(sid))
    assert "BLOB-MARKER" not in listing.text


def test_a_compare_on_a_new_conversation_creates_its_row(make_client, agent, tmp_path):
    client = make_client()
    sid = _mint(STAFF)
    r = _compare(client, sid, [TERRA, GROK], message="first ever question")
    session_id = dict(_parse(r.text))["init"]["session_id"]
    assert len(session_id) == 32
    assert all(c["session_id"] == session_id for c in agent["calls"])
    row = _one(tmp_path, "SELECT user_id, title, blob, deleted_at FROM ai_sessions WHERE session_id = ?", (session_id,))
    assert row["user_id"] == _uid(tmp_path, STAFF)
    assert row["title"] == "first ever question"
    assert row["blob"] is None
    assert _one(tmp_path, "SELECT base_seq FROM ai_compare_turns WHERE session_id = ?", (session_id,))[0] == 0
    assert _list_row(client, sid, session_id)["pending_compare"] is True


def test_a_running_compare_is_reported_as_running_with_no_candidates(make_client, agent, tmp_path):
    """§23: candidates land when the whole turn ends, so a turn still in
    flight (seen from another tab, or after Stop) has an empty list."""
    client = make_client()
    sid = _mint(STAFF)
    assert _turn(client, sid, session_id="run").status_code == 200
    _insert_compare(
        tmp_path, compare_id="cmp-running", session_id="run", user_id=_uid(tmp_path, STAFF),
        state="running", created_at=_iso(), base_seq=2,
    )
    _exec(tmp_path, "UPDATE ai_sessions SET turn_started_at = ? WHERE session_id = 'run'", (_iso(),))

    pending = _detail(client, sid, "run")["pending_compare"]
    assert pending["compare_id"] == "cmp-running"
    assert pending["state"] == "running"
    assert pending["candidates"] == []
    assert pending["models"] == [TERRA, GROK]

    # A turn in flight is `session busy`, not `compare pending`.
    r = _turn(client, sid, session_id="run")
    assert r.status_code == 409 and r.json()["detail"] == "session busy"
    r = _select(client, sid, "run", "cmp-running", TERRA)
    assert r.status_code == 409 and r.json()["detail"] == "session busy"


# ── 409 compare pending (§19) ────────────────────────────────────────────────


@pytest.mark.parametrize("body", [{}, {"compare_models": [TERRA, GROK]}], ids=["plain-turn", "compare-turn"])
def test_a_pending_compare_blocks_the_next_turn_either_way(make_client, agent, tmp_path, body):
    """The switch being off does not lift it: an unanswered compare is the
    session's state, not the request's."""
    client = make_client()
    sid = _mint(STAFF)
    _pending(client, sid, "block", [TERRA, GROK])
    calls_before = len(agent["calls"])
    audits_before = len(_audit_rows(tmp_path))
    turns_before = _usage_today(client, sid)["turns"]

    r = _turn(client, sid, session_id="block", **body)
    assert r.status_code == 409
    assert r.json() == {"detail": "compare pending"}
    assert len(agent["calls"]) == calls_before
    # Refused before the stream: nothing spent, nothing recorded.
    assert len(_audit_rows(tmp_path)) == audits_before
    assert _usage_today(client, sid)["turns"] == turns_before
    assert _one(tmp_path, "SELECT COUNT(*) FROM ai_messages WHERE session_id = 'block'")[0] == 0
    assert _one(tmp_path, "SELECT COUNT(*) FROM ai_compare_turns WHERE session_id = 'block'")[0] == 1


def test_ownership_is_decided_before_the_pending_check(make_client, agent):
    """§19 order: 404 first — a stranger must not learn that a session exists
    by being told it has a compare pending."""
    client = make_client()
    owner = _mint(STAFF)
    _pending(client, owner, "mine", [TERRA, GROK])
    intruder = _mint(OTHER)
    r = _turn(client, intruder, session_id="mine")
    assert r.status_code == 404
    assert r.json() == {"detail": "session not found"}


def test_a_pending_compare_on_one_session_does_not_block_another(make_client, agent):
    client = make_client()
    sid = _mint(STAFF)
    _pending(client, sid, "a", [TERRA, GROK])
    assert _turn(client, sid, session_id="b").status_code == 200
    _pending(client, sid, "c", [TERRA, GROK])


# ── quota (§19, §24) ─────────────────────────────────────────────────────────


def test_a_compare_counts_one_turn_per_model_and_sums_the_cost(make_client, agent, tmp_path):
    client = make_client()
    sid = _mint(STAFF)
    assert _compare(client, sid, [TERRA, GROK, DEEPSEEK]).status_code == 200
    today = _usage_today(client, sid)
    assert today["turns"] == 3
    assert today["input_tokens"] == sum(TOKENS[m][0] for m in (TERRA, GROK, DEEPSEEK))
    assert today["output_tokens"] == sum(TOKENS[m][1] for m in (TERRA, GROK, DEEPSEEK))
    assert today["cost_usd"] == pytest.approx(sum(_cost(m) for m in (TERRA, GROK, DEEPSEEK)), abs=1e-4)
    row = _one(tmp_path, "SELECT turns, cost_usd FROM ai_usage_daily WHERE user_id = ?", (_uid(tmp_path, STAFF),))
    assert row["turns"] == 3
    assert row["cost_usd"] == pytest.approx(sum(_cost(m) for m in (TERRA, GROK, DEEPSEEK)), abs=1e-6)


def test_a_compare_that_would_exceed_the_turn_limit_is_refused_unforwarded(make_client, agent, tmp_path):
    """`turns + N > limit` refuses; `turns + N == limit` is the last one let
    through. At a limit of 3 with one turn used: 3 models no, 2 models yes."""
    client = make_client(AI_DAILY_TURNS_LIMIT="3")
    sid = _mint(STAFF)
    assert _turn(client, sid, session_id="one").status_code == 200
    assert len(agent["calls"]) == 1

    r = _compare(client, sid, [TERRA, GROK, DEEPSEEK], session_id="three")
    assert r.status_code == 200  # the refusal is an event, not an HTTP status
    events = _parse(r.text)
    # Same shape as today's quota refusal: init, a run-less error, a run-less done.
    assert [e for e, _ in events] == ["init", "error", "done"]
    assert all("run" not in d for _, d in events)
    error = events[1][1]
    assert error["code"] == "quota_exceeded"
    assert error["trace_id"]
    assert events[2][1]["terminal_reason"] == "error"
    assert len(agent["calls"]) == 1  # not forwarded, not even for one model
    assert _usage_today(client, sid)["turns"] == 1  # and not counted

    # §19: the refusal is in the transcript and the session is not left pending.
    rows = _all(tmp_path, "SELECT role, error_code FROM ai_messages WHERE session_id = 'three' ORDER BY seq")
    assert [(r["role"], r["error_code"]) for r in rows] == [("user", None), ("assistant", "quota_exceeded")]
    assert _detail(client, sid, "three").get("pending_compare") is None
    assert _one(
        tmp_path,
        "SELECT COUNT(*) FROM ai_compare_turns WHERE session_id = 'three' AND state IN ('running', 'pending')",
    )[0] == 0

    # Exactly reaching the limit is allowed.
    r = _compare(client, sid, [TERRA, GROK], session_id="two")
    assert [d for e, d in _parse(r.text) if e == "compare"][0]["state"] == "pending"
    assert len(agent["calls"]) == 3
    assert _usage_today(client, sid)["turns"] == 3

    # And now even two more is over.
    r = _compare(client, sid, [TERRA, GROK], session_id="over")
    assert dict(_parse(r.text))["error"]["code"] == "quota_exceeded"
    assert len(agent["calls"]) == 3


def test_a_reached_cost_limit_refuses_a_compare_too(make_client, agent):
    client = make_client(AI_DAILY_COST_LIMIT_USD="0.003")
    sid = _mint(STAFF)
    assert _turn(client, sid, session_id="spend").status_code == 200  # spends 0.00368
    r = _compare(client, sid, [TERRA, GROK], session_id="broke")
    assert dict(_parse(r.text))["error"]["code"] == "quota_exceeded"
    assert len(agent["calls"]) == 1


# ── server-wide concurrency cap (§19, §21) ───────────────────────────────────


def _two_running_elsewhere(tmp_path, *, created_at: str) -> None:
    for n in (1, 2):
        _insert_compare(
            tmp_path, compare_id=f"cmp-else-{n}", session_id=f"else-{n}", user_id=9000 + n,
            state="running", created_at=created_at,
        )


def test_a_third_concurrent_compare_is_refused_as_compare_busy(make_client, agent, tmp_path):
    client = make_client()
    sid = _mint(STAFF)
    _two_running_elsewhere(tmp_path, created_at=_iso())

    r = _compare(client, sid, [TERRA, GROK], session_id="busy")
    assert r.status_code == 200
    events = _parse(r.text)
    assert [e for e, _ in events] == ["init", "error", "done"]
    assert all("run" not in d for _, d in events)
    assert events[1][1]["code"] == "compare_busy"
    assert events[2][1]["terminal_reason"] == "error"
    assert agent["calls"] == []
    assert _usage_today(client, sid)["turns"] == 0  # §19: not counted

    rows = _all(tmp_path, "SELECT role, error_code FROM ai_messages WHERE session_id = 'busy' ORDER BY seq")
    assert [(r["role"], r["error_code"]) for r in rows] == [("user", None), ("assistant", "compare_busy")]
    assert _detail(client, sid, "busy").get("pending_compare") is None
    # The session is free: the same question can be asked single-model at once.
    assert _turn(client, sid, session_id="busy").status_code == 200
    # The other workers' rows were not disturbed.
    assert _one(tmp_path, "SELECT COUNT(*) FROM ai_compare_turns WHERE state = 'running'")[0] == 2


def test_the_cap_does_not_apply_to_single_model_turns(make_client, agent, tmp_path):
    client = make_client()
    sid = _mint(STAFF)
    _two_running_elsewhere(tmp_path, created_at=_iso())
    r = _turn(client, sid, session_id="plain")
    assert [e for e, _ in _parse(r.text)] == ["init", *OK_RUN_EVENTS]


def test_one_running_compare_leaves_room_for_a_second(make_client, agent, tmp_path):
    client = make_client()
    sid = _mint(STAFF)
    _insert_compare(tmp_path, compare_id="cmp-else-1", session_id="else-1", user_id=9001, state="running", created_at=_iso())
    _pending(client, sid, "room", [TERRA, GROK])


def test_the_cap_is_the_setting_not_a_constant(make_client, agent, tmp_path):
    client = make_client(AI_COMPARE_MAX_CONCURRENT="1")
    sid = _mint(STAFF)
    _insert_compare(tmp_path, compare_id="cmp-else-1", session_id="else-1", user_id=9001, state="running", created_at=_iso())
    r = _compare(client, sid, [TERRA, GROK], session_id="capped")
    assert dict(_parse(r.text))["error"]["code"] == "compare_busy"
    assert agent["calls"] == []


def test_pending_and_finished_compares_do_not_occupy_a_slot(make_client, agent, tmp_path):
    """Only `running` counts: a compare waiting days for its human is not load."""
    client = make_client()
    sid = _mint(STAFF)
    for n, state in enumerate(["pending", "pending", "selected", "void"]):
        _insert_compare(
            tmp_path, compare_id=f"cmp-idle-{n}", session_id=f"idle-{n}", user_id=9000 + n,
            state=state, created_at=_iso(),
        )
    _pending(client, sid, "free", [TERRA, GROK])


def test_stale_running_rows_do_not_occupy_a_slot(make_client, agent, tmp_path):
    """§21: a `running` row older than the claim's stale window belongs to a
    worker that died; it must not hold the server-wide cap forever."""
    from app.core.ai_usage_db import TURN_CLAIM_STALE_SECONDS

    client = make_client()
    sid = _mint(STAFF)
    _two_running_elsewhere(tmp_path, created_at=_iso(-(TURN_CLAIM_STALE_SECONDS + 5)))
    _pending(client, sid, "after-crash", [TERRA, GROK])
    assert len(agent["calls"]) == 2
    assert _usage_today(client, sid)["turns"] == 2


def test_a_dead_compare_on_this_session_is_voided_by_the_next_claim(make_client, agent, tmp_path):
    """§21: the next claim on a session whose compare died mid-flight turns
    that row `void` — otherwise the session would read as busy or pending for
    good."""
    from app.core.ai_usage_db import TURN_CLAIM_STALE_SECONDS

    client = make_client()
    sid = _mint(STAFF)
    assert _turn(client, sid, session_id="dead").status_code == 200
    stale = _iso(-(TURN_CLAIM_STALE_SECONDS + 5))
    _insert_compare(
        tmp_path, compare_id="cmp-dead", session_id="dead", user_id=_uid(tmp_path, STAFF),
        state="running", created_at=stale, base_seq=2,
    )
    _exec(tmp_path, "UPDATE ai_sessions SET turn_started_at = ? WHERE session_id = 'dead'", (stale,))

    assert _turn(client, sid, session_id="dead").status_code == 200
    assert _one(tmp_path, "SELECT state FROM ai_compare_turns WHERE compare_id = 'cmp-dead'")["state"] == "void"
    assert _detail(client, sid, "dead").get("pending_compare") is None


# ── select: the happy path (§22) ─────────────────────────────────────────────


def test_select_writes_the_chosen_blob_and_the_two_transcript_rows(make_client, agent, tmp_path):
    agent["scripts"][TERRA] = _ok(TERRA, blob=BLOB_BASE)
    client = make_client()
    sid = _mint(STAFF)
    assert _turn(client, sid, session_id="pick").status_code == 200
    agent["scripts"][TERRA] = _ok(TERRA)
    compare_id = _pending(client, sid, "pick", [TERRA, GROK], message="and the last 7 days?")

    r = _select(client, sid, "pick", compare_id, GROK)
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    session = _one(tmp_path, "SELECT blob, turns, model, turn_started_at FROM ai_sessions WHERE session_id = 'pick'")
    assert json.loads(session["blob"]) == _blob(GROK)  # the chosen run's, not terra's, not the base
    assert session["turns"] == STATE_TURNS[GROK]
    assert session["model"] == GROK
    assert session["turn_started_at"] is None

    rows = _all(tmp_path, "SELECT * FROM ai_messages WHERE session_id = 'pick' ORDER BY seq")
    assert [(r["seq"], r["role"]) for r in rows] == [(1, "user"), (2, "assistant"), (3, "user"), (4, "assistant")]
    user_row, assistant_row = rows[2], rows[3]
    assert user_row["text"] == "and the last 7 days?"
    assert assistant_row["text"] == _answer(GROK)
    assert assistant_row["model"] == GROK
    assert assistant_row["compare_id"] == compare_id
    assert assistant_row["error_code"] is None
    assert [t["name"] for t in json.loads(assistant_row["tools_json"])] == [TOOL[GROK]]
    assert json.loads(assistant_row["usage_json"])["cost_usd"] == pytest.approx(_cost(GROK), abs=1e-6)
    # The earlier single-model rows are not rewritten.
    assert rows[1]["compare_id"] is None

    candidates = _all(tmp_path, "SELECT model, blob, selected, text FROM ai_turn_candidates WHERE compare_id = ?", (compare_id,))
    assert len(candidates) == 2
    assert all(c["blob"] is None for c in candidates)  # ALL of them, the chosen one included
    assert {c["model"]: c["selected"] for c in candidates} == {TERRA: 0, GROK: 1}
    assert {c["model"]: c["text"] for c in candidates} == {TERRA: _answer(TERRA), GROK: _answer(GROK)}  # text is kept

    turn = _one(tmp_path, "SELECT state, selected_model, selected_at, reason FROM ai_compare_turns WHERE compare_id = ?", (compare_id,))
    assert turn["state"] == "selected"
    assert turn["selected_model"] == GROK
    assert turn["selected_at"]
    assert turn["reason"] is None


def test_after_select_the_conversation_continues_from_the_chosen_blob(make_client, agent, tmp_path):
    client = make_client()
    sid = _mint(STAFF)
    compare_id = _pending(client, sid, "go", [TERRA, GROK])
    assert _select(client, sid, "go", compare_id, GROK).status_code == 200

    calls_before = len(agent["calls"])
    r = _turn(client, sid, session_id="go", message="what about #2?")
    assert r.status_code == 200
    events = _parse(r.text)
    assert [e for e, _ in events] == ["init", *OK_RUN_EVENTS]  # an ordinary single-model stream again
    assert all("run" not in d for _, d in events)
    follow_up = agent["calls"][calls_before]
    assert follow_up["session_blob"] == _blob(GROK)
    assert follow_up["model"] == TERRA

    rows = _all(tmp_path, "SELECT seq FROM ai_messages WHERE session_id = 'go' ORDER BY seq")
    assert [r["seq"] for r in rows] == [1, 2, 3, 4]
    assert json.loads(_audit_rows(tmp_path, "ai.query.submit")[-1]["new_value"])["resumed"] is True
    # ...and a new compare may start on it.
    _pending(client, sid, "go", [TERRA, GROK])


def test_detail_after_select_shows_the_answer_with_its_alternatives(make_client, agent):
    client = make_client()
    sid = _mint(STAFF)
    compare_id = _pending(client, sid, "hist", [TERRA, GROK, DEEPSEEK])
    assert _select(client, sid, "hist", compare_id, TERRA, reason="clearer").status_code == 200

    r = client.get("/api/v1/ai/sessions/hist", headers=_bearer(sid))
    assert "BLOB-MARKER" not in r.text
    body = r.json()
    assert body.get("pending_compare") is None
    assert not _list_row(client, sid, "hist").get("pending_compare")

    user_msg, assistant_msg = body["messages"]
    assert user_msg["role"] == "user" and user_msg["text"] == QUESTION
    assert user_msg.get("compare") is None
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["text"] == _answer(TERRA)
    assert assistant_msg["model"] == TERRA
    compare = assistant_msg["compare"]
    assert set(compare) == {"compare_id", "reason", "alternatives"}
    assert compare["compare_id"] == compare_id
    assert compare["reason"] == "clearer"
    # The ones NOT chosen, without their blobs.
    assert sorted(a["model"] for a in compare["alternatives"]) == sorted([GROK, DEEPSEEK])
    for alt in compare["alternatives"]:
        assert set(alt) == {"model", "text", "tools", "usage", "error_code", "elapsed_ms"}
        assert alt["text"] == _answer(alt["model"])
        assert [t["name"] for t in alt["tools"]] == [TOOL[alt["model"]]]
        assert alt["error_code"] is None


@pytest.mark.parametrize("reason", ["numbers", "clearer", "faster", "other", None])
def test_every_reason_code_and_none_are_accepted(make_client, agent, tmp_path, reason):
    client = make_client()
    sid = _mint(STAFF)
    compare_id = _pending(client, sid, "why", [TERRA, GROK])
    assert _select(client, sid, "why", compare_id, TERRA, reason=reason).status_code == 200
    assert _one(tmp_path, "SELECT reason FROM ai_compare_turns WHERE compare_id = ?", (compare_id,))["reason"] == reason


def test_the_only_selectable_run_still_has_to_be_selected(make_client, agent, tmp_path):
    """No auto-select, even with one survivor: the session stays pending until
    the person clicks, and then the survivor's blob is what lands."""
    agent["scripts"][GROK] = _failed()
    client = make_client()
    sid = _mint(STAFF)
    compare_id = _pending(client, sid, "solo", [TERRA, GROK])
    assert _one(tmp_path, "SELECT blob FROM ai_sessions WHERE session_id = 'solo'")["blob"] is None
    assert _turn(client, sid, session_id="solo").status_code == 409

    assert _select(client, sid, "solo", compare_id, TERRA).status_code == 200
    assert json.loads(_one(tmp_path, "SELECT blob FROM ai_sessions WHERE session_id = 'solo'")["blob"]) == _blob(TERRA)
    alternatives = _detail(client, sid, "solo")["messages"][-1]["compare"]["alternatives"]
    assert [(a["model"], a["error_code"]) for a in alternatives] == [(GROK, "rate_limited")]


# ── select: refusals (§22) ───────────────────────────────────────────────────


def test_select_on_another_users_session_is_404_and_changes_nothing(make_client, agent, tmp_path):
    client = make_client()
    owner = _mint(STAFF)
    compare_id = _pending(client, owner, "mine", [TERRA, GROK])

    for intruder in (_mint(OTHER), _mint(MANAGER)):  # a manager is not exempt from ownership
        r = _select(client, intruder, "mine", compare_id, TERRA)
        assert r.status_code == 404
        assert r.json() == {"detail": "session not found"}
    assert _one(tmp_path, "SELECT state FROM ai_compare_turns WHERE compare_id = ?", (compare_id,))["state"] == "pending"
    assert _one(tmp_path, "SELECT COUNT(*) FROM ai_messages WHERE session_id = 'mine'")[0] == 0
    assert _audit_rows(tmp_path, "ai.compare.select") == []


def test_select_with_an_unknown_or_foreign_compare_id_is_404(make_client, agent, tmp_path):
    client = make_client()
    sid = _mint(STAFF)
    compare_a = _pending(client, sid, "a", [TERRA, GROK])
    compare_b = _pending(client, sid, "b", [TERRA, GROK])

    r = _select(client, sid, "a", "no-such-compare", TERRA)
    assert r.status_code == 404 and r.json() == {"detail": "session not found"}
    # A real compare_id of the caller's OTHER session does not belong to this one.
    r = _select(client, sid, "a", compare_b, TERRA)
    assert r.status_code == 404 and r.json() == {"detail": "session not found"}
    r = _select(client, sid, "no-such-session", compare_a, TERRA)
    assert r.status_code == 404 and r.json() == {"detail": "session not found"}

    states = _all(tmp_path, "SELECT state FROM ai_compare_turns")
    assert [s["state"] for s in states] == ["pending", "pending"]


def test_select_on_a_deleted_session_is_404(make_client, agent):
    client = make_client()
    sid = _mint(STAFF)
    compare_id = _pending(client, sid, "gone", [TERRA, GROK])
    assert client.delete("/api/v1/ai/sessions/gone", headers=_bearer(sid)).status_code == 200
    r = _select(client, sid, "gone", compare_id, TERRA)
    assert r.status_code == 404 and r.json() == {"detail": "session not found"}


@pytest.mark.parametrize(
    "model",
    [GROK, DEEPSEEK, "gpt-4o"],
    ids=["failed-run", "not-in-this-compare", "unknown-model"],
)
def test_select_of_a_model_that_is_not_selectable_is_422(make_client, agent, tmp_path, model):
    agent["scripts"][GROK] = _failed()
    client = make_client()
    sid = _mint(STAFF)
    compare_id = _pending(client, sid, "nope", [TERRA, GROK])
    assert _select(client, sid, "nope", compare_id, model).status_code == 422
    assert _one(tmp_path, "SELECT state FROM ai_compare_turns WHERE compare_id = ?", (compare_id,))["state"] == "pending"
    assert _one(tmp_path, "SELECT blob FROM ai_sessions WHERE session_id = 'nope'")["blob"] is None


@pytest.mark.parametrize("reason", ["because", "", "NUMBERS", 3])
def test_select_with_a_reason_outside_the_enum_is_422(make_client, agent, tmp_path, reason):
    client = make_client()
    sid = _mint(STAFF)
    compare_id = _pending(client, sid, "why", [TERRA, GROK])
    assert _select(client, sid, "why", compare_id, TERRA, reason=reason).status_code == 422
    assert _one(tmp_path, "SELECT state FROM ai_compare_turns WHERE compare_id = ?", (compare_id,))["state"] == "pending"


def test_select_requires_compare_id_and_model(make_client, agent):
    client = make_client()
    sid = _mint(STAFF)
    compare_id = _pending(client, sid, "req", [TERRA, GROK])
    url = "/api/v1/ai/sessions/req/select"
    assert client.post(url, json={"model": TERRA}, headers=_bearer(sid)).status_code == 422
    assert client.post(url, json={"compare_id": compare_id}, headers=_bearer(sid)).status_code == 422


def test_a_different_model_cannot_be_chosen_after_the_selection(make_client, agent, tmp_path):
    """The other blobs are gone the moment one is chosen; changing one's mind
    would continue from a context that no longer exists."""
    client = make_client()
    sid = _mint(STAFF)
    compare_id = _pending(client, sid, "once", [TERRA, GROK])
    assert _select(client, sid, "once", compare_id, GROK).status_code == 200

    r = _select(client, sid, "once", compare_id, TERRA)
    assert r.status_code == 409
    assert r.json() == {"detail": "compare already selected"}
    assert _one(tmp_path, "SELECT selected_model FROM ai_compare_turns WHERE compare_id = ?", (compare_id,))[0] == GROK
    assert json.loads(_one(tmp_path, "SELECT blob FROM ai_sessions WHERE session_id = 'once'")["blob"]) == _blob(GROK)
    assert _one(tmp_path, "SELECT COUNT(*) FROM ai_messages WHERE session_id = 'once'")[0] == 2
    assert len(_audit_rows(tmp_path, "ai.compare.select")) == 1


# The statements the build that predates OPT-0076 runs against ai_agent.db,
# copied verbatim from core/ai_usage_db.py at d91606d. backend/data is a bind
# mount shared by dev and prod, so that build keeps running these against a
# file the new code has already migrated.
OLD_CLAIM_TURN_SQL = (
    "UPDATE ai_sessions SET turn_started_at = ? "
    "WHERE session_id = ? AND user_id = ? AND deleted_at IS NULL "
    "AND (turn_started_at IS NULL OR turn_started_at < ?)"
)
OLD_MAX_SEQ_SQL = "SELECT COALESCE(MAX(seq), 0) FROM ai_messages WHERE session_id = ?"
OLD_INSERT_USER_SQL = (
    "INSERT INTO ai_messages (session_id, seq, role, text, tools_json, usage_json, error_code, at) "
    "VALUES (?, ?, 'user', ?, NULL, NULL, NULL, ?)"
)
OLD_INSERT_ASSISTANT_SQL = (
    "INSERT INTO ai_messages (session_id, seq, role, text, tools_json, usage_json, error_code, at) "
    "VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?)"
)
OLD_SAVE_STATE_SQL = (
    "UPDATE ai_sessions SET blob = ?, turns = MAX(turns, ?), model = ?, updated_at = ? "
    "WHERE session_id = ? AND user_id = ? AND deleted_at IS NULL"
)
OLD_RELEASE_TURN_SQL = "UPDATE ai_sessions SET turn_started_at = NULL WHERE session_id = ? AND user_id = ?"
OLD_DETAIL_SESSION_SQL = (
    "SELECT session_id, user_id, title, model, turns, created_at, updated_at FROM ai_sessions "
    "WHERE session_id = ? AND user_id = ? AND deleted_at IS NULL"
)
OLD_DETAIL_MESSAGES_SQL = (
    "SELECT seq, role, text, tools_json, usage_json, error_code, at "
    "FROM ai_messages WHERE session_id = ? ORDER BY seq"
)
OLD_LIST_SQL = (
    "SELECT session_id, user_id, title, model, turns, created_at, updated_at FROM ai_sessions "
    "WHERE user_id = ? AND deleted_at IS NULL "
    "ORDER BY updated_at DESC, created_at DESC LIMIT ?"
)


def _old_build_runs_a_turn(conn: sqlite3.Connection, session_id: str, user_id: int, *, question: str, answer: str) -> None:
    """One whole turn the way the pre-OPT-0076 build performs it: claim, write
    the blob back, append the two transcript rows, release."""
    now = _iso()
    claimed = conn.execute(OLD_CLAIM_TURN_SQL, (now, session_id, user_id, _iso(-360)))
    assert claimed.rowcount == 1  # the old build sees a perfectly usable session
    conn.execute(OLD_SAVE_STATE_SQL, (json.dumps({"marker": "old-build"}), 1, TERRA, now, session_id, user_id))
    conn.execute("BEGIN IMMEDIATE")
    seq = int(conn.execute(OLD_MAX_SEQ_SQL, (session_id,)).fetchone()[0]) + 1
    conn.execute(OLD_INSERT_USER_SQL, (session_id, seq, question, now))
    conn.execute(OLD_INSERT_ASSISTANT_SQL, (session_id, seq + 1, answer, None, None, None, now))
    conn.execute(OLD_RELEASE_TURN_SQL, (session_id, user_id))
    conn.commit()


def test_select_after_the_session_moved_on_is_409_stale_and_voids_the_compare(make_client, agent, tmp_path):
    """§22 `compare stale`: the build that does not know the compare tables is
    not blocked by `pending`, so it may advance the session meanwhile. `select`
    notices through `base_seq`, refuses, and voids the compare — writing the
    chosen blob would silently erase the turn that happened in between."""
    client = make_client()
    sid = _mint(STAFF)
    user_id = _uid(tmp_path, STAFF)
    compare_id = _pending(client, sid, "moved", [TERRA, GROK])

    conn = _db(tmp_path)
    conn.isolation_level = None
    _old_build_runs_a_turn(conn, "moved", user_id, question="asked from the old build", answer="answered")
    conn.close()

    r = _select(client, sid, "moved", compare_id, TERRA)
    assert r.status_code == 409
    assert r.json() == {"detail": "compare stale"}

    assert _one(tmp_path, "SELECT state FROM ai_compare_turns WHERE compare_id = ?", (compare_id,))["state"] == "void"
    # What the old build wrote is intact; nothing of the compare was added.
    session = _one(tmp_path, "SELECT blob FROM ai_sessions WHERE session_id = 'moved'")
    assert json.loads(session["blob"]) == {"marker": "old-build"}
    rows = _all(tmp_path, "SELECT seq, text FROM ai_messages WHERE session_id = 'moved' ORDER BY seq")
    assert [(r["seq"], r["text"]) for r in rows] == [(1, "asked from the old build"), (2, "answered")]
    assert _audit_rows(tmp_path, "ai.compare.select") == []

    # The session is no longer pending and carries on from the old build's turn.
    assert _detail(client, sid, "moved").get("pending_compare") is None
    calls_before = len(agent["calls"])
    assert _turn(client, sid, session_id="moved").status_code == 200
    assert agent["calls"][calls_before]["session_blob"] == {"marker": "old-build"}
    # A second attempt at the voided compare does not resurrect it: nothing in
    # a void compare is selectable, so it is refused as 422 (02 §22).
    assert _select(client, sid, "moved", compare_id, TERRA).status_code == 422
    assert _one(tmp_path, "SELECT state FROM ai_compare_turns WHERE compare_id = ?", (compare_id,))["state"] == "void"


# ── select: idempotency and the late reason (§22) ────────────────────────────


def test_select_is_idempotent_for_the_same_model(make_client, agent, tmp_path):
    client = make_client()
    sid = _mint(STAFF)
    compare_id = _pending(client, sid, "twice", [TERRA, GROK])
    assert _select(client, sid, "twice", compare_id, GROK, reason="numbers").status_code == 200
    selected_at = _one(tmp_path, "SELECT selected_at FROM ai_compare_turns WHERE compare_id = ?", (compare_id,))[0]

    for body in ({"reason": "numbers"}, {}):
        r = _select(client, sid, "twice", compare_id, GROK, **body)
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    # Nothing was written a second time.
    assert _one(tmp_path, "SELECT COUNT(*) FROM ai_messages WHERE session_id = 'twice'")[0] == 2
    turn = _one(tmp_path, "SELECT state, selected_model, selected_at FROM ai_compare_turns WHERE compare_id = ?", (compare_id,))
    assert (turn["state"], turn["selected_model"], turn["selected_at"]) == ("selected", GROK, selected_at)
    assert json.loads(_one(tmp_path, "SELECT blob FROM ai_sessions WHERE session_id = 'twice'")["blob"]) == _blob(GROK)
    assert len(_audit_rows(tmp_path, "ai.compare.select")) == 1


def test_the_reason_can_be_added_and_changed_after_the_selection(make_client, agent, tmp_path):
    """"选完再补理由" has no endpoint of its own: the same select, repeated with
    a reason, updates ONLY the reason."""
    client = make_client()
    sid = _mint(STAFF)
    compare_id = _pending(client, sid, "late", [TERRA, GROK])
    assert _select(client, sid, "late", compare_id, GROK).status_code == 200
    selected_at = _one(tmp_path, "SELECT selected_at FROM ai_compare_turns WHERE compare_id = ?", (compare_id,))[0]
    assert len(_audit_rows(tmp_path, "ai.compare.select")) == 1

    assert _select(client, sid, "late", compare_id, GROK, reason="numbers").status_code == 200
    turn = _one(tmp_path, "SELECT state, selected_model, selected_at, reason FROM ai_compare_turns WHERE compare_id = ?", (compare_id,))
    assert (turn["state"], turn["selected_model"], turn["selected_at"], turn["reason"]) == ("selected", GROK, selected_at, "numbers")
    assert _one(tmp_path, "SELECT COUNT(*) FROM ai_messages WHERE session_id = 'late'")[0] == 2
    assert _detail(client, sid, "late")["messages"][-1]["compare"]["reason"] == "numbers"

    # The change is audited as a diff of the one field (§24).
    rows = _audit_rows(tmp_path, "ai.compare.select")
    assert len(rows) == 2
    assert rows[1]["target"] == "ai_session:late.reason"
    assert rows[1]["old_value"] is None
    assert rows[1]["new_value"] == "numbers"

    assert _select(client, sid, "late", compare_id, GROK, reason="faster").status_code == 200
    assert _one(tmp_path, "SELECT reason FROM ai_compare_turns WHERE compare_id = ?", (compare_id,))[0] == "faster"
    rows = _audit_rows(tmp_path, "ai.compare.select")
    assert len(rows) == 3
    assert (rows[2]["old_value"], rows[2]["new_value"]) == ("numbers", "faster")

    # Same reason again: nothing moved, nothing recorded.
    assert _select(client, sid, "late", compare_id, GROK, reason="faster").status_code == 200
    assert len(_audit_rows(tmp_path, "ai.compare.select")) == 3


# ── audit (§24) ──────────────────────────────────────────────────────────────


def _submit_value(tmp_path) -> dict:
    """The new_value of the ONE ai.query.submit row the test expects."""
    rows = _audit_rows(tmp_path, "ai.query.submit")
    assert len(rows) == 1
    return json.loads(rows[0]["new_value"])


def _assert_run_items(value: dict, models: list[str], *, failed: dict[str, str] | None = None) -> dict[str, dict]:
    """`runs[]` is one item per model carrying only the §24 keys; `error_code`
    appears on the failed ones and on no other."""
    failed = failed or {}
    runs = {run["model"]: run for run in value["runs"]}
    assert len(value["runs"]) == len(models)
    assert set(runs) == set(models)
    for model, run in runs.items():
        assert set(run) <= RUN_KEYS
        assert set(run) >= RUN_KEYS - {"error_code"}
        if model in failed:
            assert run["error_code"] == failed[model]
        else:
            assert "error_code" not in run
    return runs


def test_a_compare_turn_leaves_exactly_one_submit_row_with_the_contract_shape(make_client, agent, tmp_path):
    client = make_client()
    sid = _mint(STAFF)
    r = _compare(client, sid, [TERRA, GROK], session_id="aud")
    compare_id = dict(_parse(r.text))["init"]["compare"]["compare_id"]

    rows = _audit_rows(tmp_path)
    assert [row["action"] for row in rows] == ["ai.query.submit"]
    assert rows[0]["actor_email"] == STAFF  # from the session, never the body
    assert rows[0]["target"] == "ai_session:aud"
    value = json.loads(rows[0]["new_value"])

    assert set(value) <= COMPARE_AUDIT_KEYS
    assert "model" not in value  # meaningless with several models
    assert value["question"] == QUESTION
    assert value["compare_id"] == compare_id
    assert value["models"] == [TERRA, GROK]
    assert value["terminal_reason"] == "compare_pending"
    assert "error_code" not in value
    assert value["resumed"] is False
    assert value["scope_denied_count"] == 0
    assert value["skills_loaded"] == []
    assert "sql" not in value
    # Sums over the runs.
    assert value["input_tokens"] == TOKENS[TERRA][0] + TOKENS[GROK][0]
    assert value["output_tokens"] == TOKENS[TERRA][1] + TOKENS[GROK][1]
    assert value["cost_usd"] == pytest.approx(_cost(TERRA) + _cost(GROK), abs=2e-6)
    # Merged across runs.
    assert sorted(value["tools_called"]) == sorted([TOOL[TERRA], TOOL[GROK]])
    assert value["subjects"] == ["client:123"]  # both runs looked at the same client: once

    runs = _assert_run_items(value, [TERRA, GROK])
    for model in (TERRA, GROK):
        assert runs[model]["tools_called"] == [TOOL[model]]
        assert runs[model]["terminal_reason"] == "end_turn"
        assert runs[model]["input_tokens"] == TOKENS[model][0]
        assert runs[model]["output_tokens"] == TOKENS[model][1]
        assert runs[model]["cost_usd"] == pytest.approx(_cost(model), abs=1e-6)


def test_merged_tools_are_deduplicated_across_runs(make_client, agent, tmp_path):
    """TERRA and DEEPSEEK call the same tool: once in the merged list, once in
    each run's own list."""
    client = make_client()
    sid = _mint(STAFF)
    assert _compare(client, sid, [TERRA, DEEPSEEK]).status_code == 200
    value = _submit_value(tmp_path)
    assert value["tools_called"] == ["get_client_overview"]
    assert [run["tools_called"] for run in value["runs"]] == [["get_client_overview"], ["get_client_overview"]]


def test_a_partly_failed_compare_is_one_row_with_the_failed_run_marked(make_client, agent, tmp_path):
    agent["scripts"][GROK] = _failed("rate_limited")
    client = make_client()
    sid = _mint(STAFF)
    assert _compare(client, sid, [TERRA, GROK]).status_code == 200
    value = _submit_value(tmp_path)
    assert value["terminal_reason"] == "compare_pending"
    assert "error_code" not in value  # the TURN did not fail
    runs = _assert_run_items(value, [TERRA, GROK], failed={GROK: "rate_limited"})
    assert runs[GROK]["terminal_reason"] == "error"
    assert runs[GROK]["tools_called"] == []
    assert runs[GROK]["input_tokens"] == 0 and runs[GROK]["cost_usd"] == 0
    assert value["input_tokens"] == TOKENS[TERRA][0]


def test_a_voided_compare_is_one_row_marked_compare_failed(make_client, agent, tmp_path):
    agent["scripts"][TERRA] = _failed("rate_limited")
    agent["scripts"][GROK] = _failed("internal")
    client = make_client()
    sid = _mint(STAFF)
    assert _compare(client, sid, [TERRA, GROK]).status_code == 200
    value = _submit_value(tmp_path)
    assert value["terminal_reason"] == "error"
    assert value["error_code"] == "compare_failed"
    assert value["models"] == [TERRA, GROK]
    _assert_run_items(value, [TERRA, GROK], failed={TERRA: "rate_limited", GROK: "internal"})


def test_a_quota_refused_compare_is_one_row(make_client, agent, tmp_path):
    client = make_client(AI_DAILY_TURNS_LIMIT="1")
    sid = _mint(STAFF)
    assert _compare(client, sid, [TERRA, GROK], session_id="q").status_code == 200
    rows = _audit_rows(tmp_path)
    assert [row["action"] for row in rows] == ["ai.query.submit"]
    value = json.loads(rows[0]["new_value"])
    assert set(value) <= COMPARE_AUDIT_KEYS
    assert "model" not in value
    assert value["models"] == [TERRA, GROK]
    assert value["question"] == QUESTION
    assert value["terminal_reason"] == "error"
    assert value["error_code"] == "quota_exceeded"
    assert value["cost_usd"] == 0 and value["input_tokens"] == 0
    for run in value.get("runs", []):
        assert set(run) <= RUN_KEYS


def test_a_compare_busy_refusal_is_one_row(make_client, agent, tmp_path):
    client = make_client()
    sid = _mint(STAFF)
    _two_running_elsewhere(tmp_path, created_at=_iso())
    assert _compare(client, sid, [TERRA, GROK]).status_code == 200
    value = _submit_value(tmp_path)
    assert value["models"] == [TERRA, GROK]
    assert value["terminal_reason"] == "error"
    assert value["error_code"] == "compare_busy"


def test_run_sql_text_is_prefixed_with_its_model_and_refusals_are_summed(make_client, agent, tmp_path):
    """§24: `sql` is merged with a "<model>: " prefix (which column ran what on
    the shared DB account); `scope_denied_count` is the sum over runs."""

    def _script(model: str, sql: str) -> list[Any]:
        return [
            ("tool_use", {"name": "run_sql", "input": {"db": "fxbackoffice", "sql": sql}}),
            ("tool_done", {"name": "run_sql", "ok": True, "source": {"function": "run_sql"}, "certified": False}),
            ("tool_use", {"name": "get_risk_signals", "input": {"subject": {"kind": "login_sid", "value": "1-8522845"}}}),
            ("tool_done", {"name": "get_risk_signals", "ok": False, "error_code": "scope_denied"}),
            ("text", {"delta": "uncertified answer"}),
            ("usage", {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0, "cost_usd": None}),
            ("session_state", {"blob": _blob(model), "turns": 1}),
            ("done", {"terminal_reason": "end_turn", "num_turns": 2}),
        ]

    agent["scripts"][TERRA] = _script(TERRA, "SELECT 1")
    agent["scripts"][GROK] = _script(GROK, "SELECT COUNT(*) FROM tags")
    client = make_client()
    sid = _mint(STAFF)
    assert _compare(client, sid, [TERRA, GROK]).status_code == 200
    value = _submit_value(tmp_path)
    assert sorted(value["sql"]) == sorted([f"{TERRA}: SELECT 1", f"{GROK}: SELECT COUNT(*) FROM tags"])
    assert value["scope_denied_count"] == 2
    assert value["subjects"] == ["login:1-8522845"]
    # Not repeated inside the runs (the 2000-char budget).
    for run in value["runs"]:
        assert "sql" not in run and "subjects" not in run


def test_three_runs_of_six_tools_and_a_500_char_question_stay_parseable(make_client, agent, tmp_path):
    """`audit.MAX_VALUE_LEN` truncates from the tail and the JSON stops
    parsing. The contract's worst ordinary case must fit: 3 runs x 6 tool
    calls each + a question at the 500-char cap."""
    from app.core.audit import MAX_VALUE_LEN

    tools = [
        "get_client_overview", "get_trade_activity", "get_risk_signals",
        "rank_accounts", "get_economic_calendar", "rank_open_positions",
    ]

    def _script(model: str) -> list[Any]:
        events: list[Any] = []
        for name in tools:
            events.append(("tool_use", {"name": name, "input": {"subject": {"kind": "client_id", "value": "146530"}}}))
            events.append(("tool_done", {"name": name, "ok": True, "source": {"function": name}, "certified": True}))
        i, o, c = TOKENS[model]
        events += [
            ("text", {"delta": _answer(model)}),
            ("usage", {"input_tokens": i * 37, "output_tokens": o * 37, "cache_read_input_tokens": c, "cost_usd": None}),
            ("session_state", {"blob": _blob(model), "turns": 7}),
            ("done", {"terminal_reason": "end_turn", "num_turns": 7}),
        ]
        return events

    models = [TERRA, GROK, DEEPSEEK]
    for model in models:
        agent["scripts"][model] = _script(model)
    client = make_client()
    sid = _mint(STAFF)
    question = ("Compare the last 30 days of client 146530 against the previous 30. " * 20)[:700]
    assert len(question) > 500
    assert _compare(client, sid, models, session_id="big", message=question).status_code == 200

    rows = _audit_rows(tmp_path, "ai.query.submit")
    assert len(rows) == 1
    raw = rows[0]["new_value"]
    assert len(raw) <= MAX_VALUE_LEN
    assert "truncated" not in raw
    value = json.loads(raw)  # the point: still JSON
    assert value["question"] == question[:500]
    assert len(value["runs"]) == 3
    assert all(len(run["tools_called"]) == 6 for run in value["runs"])
    assert sorted(value["tools_called"]) == sorted(tools)


def test_select_leaves_one_audit_row_with_the_contract_fields(make_client, agent, tmp_path):
    client = make_client()
    sid = _mint(STAFF)
    compare_id = _pending(client, sid, "sel-aud", [TERRA, GROK, DEEPSEEK])
    assert _select(client, sid, "sel-aud", compare_id, GROK).status_code == 200

    rows = _audit_rows(tmp_path)
    assert [row["action"] for row in rows] == ["ai.query.submit", "ai.compare.select"]
    row = rows[1]
    assert row["actor_email"] == STAFF
    assert row["target"] == "ai_session:sel-aud"
    value = json.loads(row["new_value"])
    assert set(value) == {"compare_id", "model", "reason", "others", "selectable"}
    assert value["compare_id"] == compare_id
    assert value["model"] == GROK
    assert value["reason"] is None
    assert sorted(value["others"]) == sorted([TERRA, DEEPSEEK])
    assert sorted(value["selectable"]) == sorted([TERRA, GROK, DEEPSEEK])


def test_a_refused_select_leaves_no_audit_row(make_client, agent, tmp_path):
    """The audit rule: done by a person + changed state + SUCCEEDED."""
    agent["scripts"][GROK] = _failed()
    client = make_client()
    sid = _mint(STAFF)
    compare_id = _pending(client, sid, "ref", [TERRA, GROK])
    assert _select(client, sid, "ref", compare_id, GROK).status_code == 422
    assert _select(client, sid, "ref", "no-such", TERRA).status_code == 404
    assert _select(client, sid, "ref", compare_id, TERRA, reason="because").status_code == 422
    assert _audit_rows(tmp_path, "ai.compare.select") == []


# ── the browser goes away mid-compare (§20) ──────────────────────────────────


def _gone_after_first_frame(monkeypatch) -> None:
    """Make the relay see a disconnected client from its second poll on — the
    same simulation test_ai_sessions.py uses for Stop / tab close."""
    from starlette.requests import Request

    calls = {"n": 0}

    async def _is_disconnected(self):  # noqa: ANN001
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(Request, "is_disconnected", _is_disconnected)


def _wait_for(predicate, *, seconds: float = 8.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_runs_that_finish_during_the_drain_become_candidates(make_client, agent, tmp_path, monkeypatch):
    """Stop / tab close does not throw the answers away: the worker keeps
    draining, the finished runs become candidates, the session ends up
    pending, and there is exactly one audit row saying the client left."""
    # Slow enough that neither run is done when the relay notices the client left.
    for model in (TERRA, GROK):
        script = _ok(model)
        agent["scripts"][model] = [script[0], 0.3, *script[1:]]
    _gone_after_first_frame(monkeypatch)
    client = make_client()
    sid = _mint(STAFF)
    # Enter the client so its event loop outlives the request: the drain runs
    # as a detached task on that loop after the response body has closed.
    with client:
        r = _compare(client, sid, [TERRA, GROK], session_id="gone")
        assert r.status_code == 200
        events = _parse(r.text)
        assert events[0][0] == "init"
        assert "compare" not in [e for e, _ in events]  # the relay stopped early

        def _settled() -> bool:
            turn = _one(tmp_path, "SELECT state FROM ai_compare_turns WHERE session_id = 'gone'")
            claim = _one(tmp_path, "SELECT turn_started_at FROM ai_sessions WHERE session_id = 'gone'")
            return (
                turn is not None and turn["state"] != "running"
                and claim is not None and claim[0] is None
                and len(_audit_rows(tmp_path, "ai.query.submit")) >= 1
            )

        assert _wait_for(_settled)

    turn = _one(tmp_path, "SELECT compare_id, state FROM ai_compare_turns WHERE session_id = 'gone'")
    assert turn["state"] == "pending"
    candidates = _all(tmp_path, "SELECT model, blob, error_code FROM ai_turn_candidates WHERE compare_id = ?", (turn["compare_id"],))
    assert {c["model"] for c in candidates} == {TERRA, GROK}
    for cand in candidates:
        assert cand["error_code"] is None
        assert json.loads(cand["blob"]) == _blob(cand["model"])

    value = _submit_value(tmp_path)
    assert value["error_code"] == "client_disconnected"
    assert value["models"] == [TERRA, GROK]
    assert value["input_tokens"] == TOKENS[TERRA][0] + TOKENS[GROK][0]  # still billed
    _assert_run_items(value, [TERRA, GROK])

    # §20: the front end learns the outcome from the detail endpoint.
    pending = _detail(client, sid, "gone")["pending_compare"]
    assert pending["state"] == "pending"
    assert all(c["selectable"] for c in pending["candidates"])
    assert _select(client, sid, "gone", turn["compare_id"], TERRA).status_code == 200


def test_a_run_still_going_when_the_drain_ends_is_incomplete(make_client, agent, tmp_path, monkeypatch):
    """§20: the drain is bounded. A run that outlives it is recorded as
    `incomplete` (not selectable); the one that made it is still a candidate."""
    from app.api.v1.routes import ai as ai_route

    # Both names exist today in routes/ai.py; the loop re-checks its deadlines
    # every KEEPALIVE_SECONDS, so that has to shrink with the drain cap.
    monkeypatch.setattr(ai_route, "DISCONNECT_DRAIN_SECONDS", 0.4)
    monkeypatch.setattr(ai_route, "KEEPALIVE_SECONDS", 0.05)
    script = _ok(TERRA)
    agent["scripts"][TERRA] = [script[0], 0.15, *script[1:]]
    agent["scripts"][GROK] = [("text", {"delta": "still thinking "}), 20.0, *_ok(GROK)]
    _gone_after_first_frame(monkeypatch)
    client = make_client()
    sid = _mint(STAFF)
    with client:
        r = _compare(client, sid, [TERRA, GROK], session_id="slow")
        assert r.status_code == 200

        def _settled() -> bool:
            turn = _one(tmp_path, "SELECT state FROM ai_compare_turns WHERE session_id = 'slow'")
            return turn is not None and turn["state"] != "running" and len(_audit_rows(tmp_path, "ai.query.submit")) >= 1

        assert _wait_for(_settled)

        turn = _one(tmp_path, "SELECT compare_id, state FROM ai_compare_turns WHERE session_id = 'slow'")
        assert turn["state"] == "pending"
        by_model = {
            c["model"]: c
            for c in _all(tmp_path, "SELECT model, blob, error_code FROM ai_turn_candidates WHERE compare_id = ?", (turn["compare_id"],))
        }
        assert by_model[TERRA]["error_code"] is None and by_model[TERRA]["blob"] is not None
        assert by_model[GROK]["error_code"] == "incomplete"
        assert by_model[GROK]["blob"] is None

        pending = _detail(client, sid, "slow")["pending_compare"]
        assert [c["model"] for c in pending["candidates"] if c["selectable"]] == [TERRA]
        value = _submit_value(tmp_path)
        assert value["error_code"] == "client_disconnected"
        _assert_run_items(value, [TERRA, GROK], failed={GROK: "incomplete"})


# ── schema, migration and the build that predates the feature (§21) ──────────

COMPARE_TURN_COLUMNS = {
    "compare_id", "session_id", "user_id", "question", "models_json", "base_seq",
    "state", "created_at", "finished_at", "selected_model", "selected_at", "reason",
}
CANDIDATE_COLUMNS = {
    "compare_id", "model", "position", "text", "tools_json", "usage_json",
    "error_code", "elapsed_ms", "blob", "turns", "selected",
}

# The ai_agent.db schema as it is on prod today (core/ai_usage_db.py at
# d91606d: `_SCHEMA` for these three tables plus the one migrated column).
OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_usage_daily (
    user_id       INTEGER NOT NULL,
    day_hk        TEXT    NOT NULL,
    turns         INTEGER NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day_hk)
);
CREATE TABLE IF NOT EXISTS ai_sessions (
    session_id  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    title       TEXT,
    model       TEXT,
    blob        TEXT,
    turns       INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    deleted_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_sessions_user ON ai_sessions(user_id, updated_at);
CREATE TABLE IF NOT EXISTS ai_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    role        TEXT NOT NULL,
    text        TEXT NOT NULL,
    tools_json  TEXT,
    usage_json  TEXT,
    error_code  TEXT,
    at          TEXT NOT NULL,
    UNIQUE(session_id, seq)
);
ALTER TABLE ai_sessions ADD COLUMN turn_started_at TEXT;
"""


def _columns(path, table: str) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def test_a_fresh_file_has_the_two_tables_and_the_two_columns(make_client, tmp_path):
    make_client()
    path = tmp_path / "ai_agent_test.db"
    assert _columns(path, "ai_compare_turns") >= COMPARE_TURN_COLUMNS
    assert _columns(path, "ai_turn_candidates") >= CANDIDATE_COLUMNS
    assert {"model", "compare_id"} <= _columns(path, "ai_messages")
    conn = sqlite3.connect(str(path))
    try:
        pk = [row[1] for row in conn.execute("PRAGMA table_info(ai_turn_candidates)").fetchall() if row[5]]
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(ai_compare_turns)").fetchall()}
    finally:
        conn.close()
    assert sorted(pk) == ["compare_id", "model"]  # one candidate per model per compare
    assert "idx_ai_compare_session" in indexes


def test_the_live_file_is_migrated_and_the_old_builds_sql_still_works(tmp_path, monkeypatch):
    """backend/data is shared by dev and prod: the first dev restart migrates
    prod's file. On a file with the OLD schema, init must add the columns and
    tables (idempotently) and every statement the old build runs — copied
    verbatim above — must keep working against the result."""
    from app.core import ai_usage_db

    path = tmp_path / "old.db"
    old = sqlite3.connect(str(path))
    old.executescript(OLD_SCHEMA)
    old.execute(
        "INSERT INTO ai_sessions (session_id, user_id, title, model, blob, turns, created_at, updated_at) "
        "VALUES ('legacy', 7, 't', ?, NULL, 0, '2026-09-27T00:00:00Z', '2026-09-27T00:00:00Z')",
        (TERRA,),
    )
    old.execute(OLD_INSERT_USER_SQL, ("legacy", 1, "an old question", "2026-09-27T00:00:00Z"))
    old.execute(OLD_INSERT_ASSISTANT_SQL, ("legacy", 2, "an old answer", None, None, None, "2026-09-27T00:00:00Z"))
    old.commit()
    old.close()
    assert "model" not in _columns(path, "ai_messages")

    monkeypatch.setattr(ai_usage_db, "_DB_PATH", path)
    ai_usage_db.init_ai_usage_db()
    ai_usage_db.init_ai_usage_db()  # idempotent on the second boot

    assert {"model", "compare_id"} <= _columns(path, "ai_messages")
    assert _columns(path, "ai_compare_turns") >= COMPARE_TURN_COLUMNS
    assert _columns(path, "ai_turn_candidates") >= CANDIDATE_COLUMNS

    conn = sqlite3.connect(str(path))
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    try:
        # The rows written before the migration are intact, new columns NULL.
        legacy = conn.execute("SELECT seq, text, model, compare_id FROM ai_messages WHERE session_id = 'legacy' ORDER BY seq").fetchall()
        assert [(r["seq"], r["text"], r["model"], r["compare_id"]) for r in legacy] == [
            (1, "an old question", None, None), (2, "an old answer", None, None),
        ]
        # A whole turn by the old build: claim, blob, two rows, release.
        _old_build_runs_a_turn(conn, "legacy", 7, question="asked after the migration", answer="still works")
        session = conn.execute(OLD_DETAIL_SESSION_SQL, ("legacy", 7)).fetchone()
        assert session["session_id"] == "legacy" and session["model"] == TERRA
        msgs = conn.execute(OLD_DETAIL_MESSAGES_SQL, ("legacy",)).fetchall()
        assert [(m["seq"], m["role"], m["text"]) for m in msgs] == [
            (1, "user", "an old question"), (2, "assistant", "an old answer"),
            (3, "user", "asked after the migration"), (4, "assistant", "still works"),
        ]
        assert [r["session_id"] for r in conn.execute(OLD_LIST_SQL, (7, 50)).fetchall()] == ["legacy"]
        assert conn.execute("SELECT model, compare_id FROM ai_messages WHERE seq = 4").fetchone()[:] == (None, None)
    finally:
        conn.close()

    # ...and the new code reads what the old build wrote.
    detail = ai_usage_db.get_session_detail("legacy", 7)
    assert [m["text"] for m in detail["messages"]] == [
        "an old question", "an old answer", "asked after the migration", "still works",
    ]


def test_a_pending_compare_is_invisible_to_the_old_build(make_client, agent, tmp_path):
    """What the pre-OPT-0076 build sees while a compare waits for its human:
    a session whose transcript does not contain that turn, whose blob is the
    previous one, and which it can claim. "This turn has not happened yet.\""""
    agent["scripts"][TERRA] = _ok(TERRA, blob=BLOB_BASE)
    client = make_client()
    sid = _mint(STAFF)
    user_id = _uid(tmp_path, STAFF)
    assert _turn(client, sid, session_id="old-eyes", message="the first question").status_code == 200
    agent["scripts"][TERRA] = _ok(TERRA)
    _pending(client, sid, "old-eyes", [TERRA, GROK], message="the compared question")

    conn = _db(tmp_path)
    conn.isolation_level = None
    try:
        msgs = conn.execute(OLD_DETAIL_MESSAGES_SQL, ("old-eyes",)).fetchall()
        assert [(m["seq"], m["role"]) for m in msgs] == [(1, "user"), (2, "assistant")]
        assert all("compared question" not in m["text"] for m in msgs)
        assert conn.execute(OLD_DETAIL_SESSION_SQL, ("old-eyes", user_id)).fetchone() is not None
        blob = conn.execute("SELECT user_id, deleted_at, blob FROM ai_sessions WHERE session_id = ?", ("old-eyes",)).fetchone()
        assert json.loads(blob["blob"]) == BLOB_BASE
        claimed = conn.execute(OLD_CLAIM_TURN_SQL, (_iso(), "old-eyes", user_id, _iso(-360)))
        assert claimed.rowcount == 1
        conn.execute(OLD_RELEASE_TURN_SQL, ("old-eyes", user_id))
    finally:
        conn.close()


def test_the_retention_purge_takes_the_compare_rows_with_the_session(make_client, agent, tmp_path):
    """§21: hard-deleting a session deletes its compare turns and candidates —
    candidate blobs hold raw tool results and must not outlive the session."""
    from app.core import ai_usage_db

    client = make_client()
    sid = _mint(STAFF)
    _pending(client, sid, "purge-me", [TERRA, GROK])
    kept = _pending(client, sid, "keep-me", [TERRA, GROK])
    assert client.delete("/api/v1/ai/sessions/purge-me", headers=_bearer(sid)).status_code == 200
    _exec(tmp_path, "UPDATE ai_sessions SET deleted_at = ? WHERE session_id = 'purge-me'", (_iso(-91 * 86400),))

    assert ai_usage_db.purge_ai_sessions(90) == 1

    assert _one(tmp_path, "SELECT COUNT(*) FROM ai_sessions WHERE session_id = 'purge-me'")[0] == 0
    turns = _all(tmp_path, "SELECT compare_id, session_id FROM ai_compare_turns")
    assert [(t["compare_id"], t["session_id"]) for t in turns] == [(kept, "keep-me")]
    candidates = _all(tmp_path, "SELECT DISTINCT compare_id FROM ai_turn_candidates")
    assert [c["compare_id"] for c in candidates] == [kept]


def test_a_pending_compare_never_expires(make_client, agent, tmp_path):
    """User decision #1: no auto-select, no timeout. A compare that has waited
    longer than any stale window is still pending, still selectable."""
    client = make_client()
    sid = _mint(STAFF)
    compare_id = _pending(client, sid, "patient", [TERRA, GROK])
    long_ago = _iso(-30 * 86400)
    _exec(tmp_path, "UPDATE ai_compare_turns SET created_at = ?, finished_at = ? WHERE compare_id = ?", (long_ago, long_ago, compare_id))

    assert _turn(client, sid, session_id="patient").status_code == 409
    assert _detail(client, sid, "patient")["pending_compare"]["state"] == "pending"
    assert _one(tmp_path, "SELECT COUNT(*) FROM ai_turn_candidates WHERE compare_id = ? AND blob IS NOT NULL", (compare_id,))[0] == 2
    assert _select(client, sid, "patient", compare_id, GROK).status_code == 200


# ── where the new route sits (§22) ───────────────────────────────────────────


def test_the_select_route_is_an_ai_route_classified_open():
    """`ai` module gate by path; OPEN for the data-scope axis (it touches only
    the caller's own session). The anti-drift tests in test_data_scope.py and
    test_module_gate.py cover "every live route is classified"; this names
    the classification the contract chose."""
    from app.core.auth_deps import classify_path
    from app.core.data_scope import OPEN, ROUTE_SCOPE

    assert classify_path("/api/v1/ai/sessions/abc/select") == "ai"
    assert ROUTE_SCOPE["/ai/sessions/{session_id}/select"] == OPEN


def test_a_restricted_caller_can_select(make_client, agent, monkeypatch):
    """Decision #6: restricted colleagues use compare end to end — the
    coverage gate must let the select route through for them too."""
    from app.core import data_scope

    monkeypatch.setitem(data_scope.DATA_SCOPE_OVERRIDES, STAFF, frozenset({1}))
    client = make_client()
    sid = _mint(STAFF)
    compare_id = _pending(client, sid, "scoped", [TERRA, GROK])
    assert _select(client, sid, "scoped", compare_id, GROK).status_code == 200
    assert _turn(client, sid, session_id="scoped").status_code == 200
    assert agent["calls"][-1]["scope"] == [1]
