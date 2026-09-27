"""Conversation memory on the main-API side (OPT-0065 item ①, 02 §8.2–§8.4).

What is pinned here:

  * a first turn creates the ai_sessions row (title = the question) and the
    agent is handed `session_blob: null`;
  * the agent's `session_state` event is CONSUMED (blob written back) and never
    reaches the browser;
  * the next turn on that session hands the stored blob back to the agent and
    the audit row says `resumed: true`;
  * a session that is not the caller's — on POST /turn, GET, DELETE, PATCH — is
    a plain 404, never 403 (the module gate's word) or 401 (bounces the SPA);
  * list / detail shapes; the blob is never in a browser payload;
  * DELETE soft-deletes (gone from the list, then 404) with an audit row;
  * PATCH renames with a diff audit row and none for a no-op rename;
  * two transcript rows per turn, on the error path too;
  * retention: 0 = keep forever; only soft-deleted rows past the cutoff go.

Reuses the harness of test_ai_route.py (real router behind the real gates,
users_db and ai_usage_db redirected to tmp, the httpx seam scripted).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from tests.test_ai_route import (  # noqa: F401 — fixtures register on import
    MANAGER,
    SCRIPTED_OK,
    STAFF,
    _audit_rows,
    _bearer,
    _mint,
    _parse,
    make_client,
    scripted_agent,
)

OTHER = "other@kohleservices.com"

BLOB_V1 = {"session_id": "s1", "state": {"messages": [{"role": "user", "text": "hi"}]}}
BLOB_V2 = {"session_id": "s1", "state": {"messages": [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "yo"}]}}

WITH_STATE: list[tuple[str, Any]] = list(SCRIPTED_OK[:-1]) + [
    ("session_state", {"blob": BLOB_V1, "turns": 1}),
    SCRIPTED_OK[-1],
]


def _turn(client, sid, **body):
    payload = {"message": "how is client 123?", **body}
    return client.post("/api/v1/ai/turn", json=payload, headers=_bearer(sid))


def _sessions_db(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "ai_agent_test.db"))
    conn.row_factory = sqlite3.Row
    return conn


# ── the turn: create / resume / write-back ───────────────────────────────────


def test_first_turn_creates_the_row_and_hands_the_agent_no_blob(
    make_client, scripted_agent, tmp_path
):
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    r = _turn(client, sid, message="客户 146530 最近怎么样？这是一个很长的问题" + "x" * 80)
    assert r.status_code == 200
    session_id = dict(_parse(r.text))["init"]["session_id"]

    sent = scripted_agent["calls"][0]["payload"]
    assert "session_blob" in sent and sent["session_blob"] is None

    conn = _sessions_db(tmp_path)
    row = conn.execute("SELECT * FROM ai_sessions WHERE session_id = ?", (session_id,)).fetchone()
    assert row is not None
    assert len(row["title"]) == 60  # first 60 chars of the question
    assert row["model"] == "gpt-5.6-terra"
    assert row["deleted_at"] is None
    assert row["created_at"].endswith("Z") and row["updated_at"].endswith("Z")
    value = json.loads(_audit_rows(tmp_path)[0]["new_value"])
    assert value["resumed"] is False


def test_session_state_is_written_back_and_not_forwarded(make_client, scripted_agent, tmp_path):
    scripted_agent["script"]["events"] = WITH_STATE
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    r = _turn(client, sid, session_id="sess-mem")
    events = _parse(r.text)
    assert "session_state" not in [e for e, _ in events]
    assert [e for e, _ in events][-1] == "done"

    conn = _sessions_db(tmp_path)
    row = conn.execute("SELECT blob, turns FROM ai_sessions WHERE session_id = 'sess-mem'").fetchone()
    assert json.loads(row["blob"]) == BLOB_V1
    assert row["turns"] == 1


def test_second_turn_resumes_with_the_stored_blob(make_client, scripted_agent, tmp_path):
    scripted_agent["script"]["events"] = WITH_STATE
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    assert _turn(client, sid, session_id="sess-mem").status_code == 200

    scripted_agent["script"]["events"] = list(SCRIPTED_OK[:-1]) + [
        ("session_state", {"blob": BLOB_V2, "turns": 2}),
        SCRIPTED_OK[-1],
    ]
    r = _turn(client, sid, session_id="sess-mem", message="他最近 7 天呢？")
    assert r.status_code == 200
    second = scripted_agent["calls"][1]["payload"]
    assert second["session_blob"] == BLOB_V1
    assert second["session_id"] == "sess-mem"

    rows = _audit_rows(tmp_path)
    assert [json.loads(r["new_value"])["resumed"] for r in rows] == [False, True]

    conn = _sessions_db(tmp_path)
    row = conn.execute("SELECT blob, turns FROM ai_sessions WHERE session_id = 'sess-mem'").fetchone()
    assert json.loads(row["blob"]) == BLOB_V2
    assert row["turns"] == 2


def test_turns_only_ever_grows(make_client, scripted_agent, tmp_path):
    scripted_agent["script"]["events"] = list(SCRIPTED_OK[:-1]) + [
        ("session_state", {"blob": BLOB_V1, "turns": 5}),
        SCRIPTED_OK[-1],
    ]
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    _turn(client, sid, session_id="s-grow")
    scripted_agent["script"]["events"] = list(SCRIPTED_OK[:-1]) + [
        ("session_state", {"blob": BLOB_V2, "turns": 1}),
        SCRIPTED_OK[-1],
    ]
    _turn(client, sid, session_id="s-grow")
    conn = _sessions_db(tmp_path)
    assert conn.execute("SELECT turns FROM ai_sessions WHERE session_id = 's-grow'").fetchone()[0] == 5


def test_a_client_supplied_id_that_does_not_exist_starts_a_session_under_it(
    make_client, scripted_agent, tmp_path
):
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    r = _turn(client, sid, session_id="browser-made-id")
    assert r.status_code == 200
    conn = _sessions_db(tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM ai_sessions WHERE session_id = 'browser-made-id'").fetchone()[0] == 1


# ── ownership: 404, never 401/403 ────────────────────────────────────────────


def test_another_users_session_is_404_on_turn_and_never_forwarded(
    make_client, scripted_agent, tmp_path
):
    client = make_client()
    owner = _mint(STAFF, allowed_modules='["ai"]')
    assert _turn(client, owner, session_id="mine").status_code == 200
    assert len(scripted_agent["calls"]) == 1

    intruder = _mint(OTHER, allowed_modules='["ai"]')
    r = _turn(client, intruder, session_id="mine")
    assert r.status_code == 404
    assert r.json() == {"detail": "session not found"}
    assert len(scripted_agent["calls"]) == 1  # not forwarded

    # No ai.query.submit row for a turn that never reached the agent, but a
    # throttled permission_denied auth event so an enumeration attempt shows.
    assert len(_audit_rows(tmp_path)) == 1
    conn = sqlite3.connect(str(tmp_path / "users_test.db"))
    try:
        rows = conn.execute(
            "SELECT email, detail FROM auth_events "
            "WHERE event = 'permission_denied' AND detail = 'ai_session_owner'"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [(OTHER, "ai_session_owner")]


def test_a_manager_is_not_exempt_from_ownership(make_client, scripted_agent):
    client = make_client()
    owner = _mint(STAFF, allowed_modules='["ai"]')
    assert _turn(client, owner, session_id="staff-own").status_code == 200
    boss = _mint(MANAGER)
    assert _turn(client, boss, session_id="staff-own").status_code == 404
    assert client.get("/api/v1/ai/sessions/staff-own", headers=_bearer(boss)).status_code == 404


@pytest.mark.parametrize("method", ["GET", "DELETE", "PATCH"])
def test_another_users_session_is_404_on_the_session_endpoints(
    make_client, scripted_agent, method
):
    client = make_client()
    owner = _mint(STAFF, allowed_modules='["ai"]')
    assert _turn(client, owner, session_id="mine").status_code == 200
    intruder = _mint(OTHER, allowed_modules='["ai"]')
    kwargs: dict[str, Any] = {"headers": _bearer(intruder)}
    if method == "PATCH":
        kwargs["json"] = {"title": "stolen"}
    r = client.request(method, "/api/v1/ai/sessions/mine", **kwargs)
    assert r.status_code == 404
    # and the owner's row is untouched
    detail = client.get("/api/v1/ai/sessions/mine", headers=_bearer(owner)).json()
    assert detail["session"]["title"] == "how is client 123?"


def test_the_sessions_endpoints_are_behind_the_ai_module_gate(make_client, scripted_agent):
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["cs"]')
    assert client.get("/api/v1/ai/sessions", headers=_bearer(sid)).status_code == 403
    assert client.get("/api/v1/ai/sessions/x", headers=_bearer(sid)).status_code == 403
    assert client.delete("/api/v1/ai/sessions/x", headers=_bearer(sid)).status_code == 403
    assert client.patch("/api/v1/ai/sessions/x", json={"title": "t"}, headers=_bearer(sid)).status_code == 403


# ── list / detail ────────────────────────────────────────────────────────────


def test_list_is_own_sessions_newest_first_with_the_contract_shape(make_client, scripted_agent):
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    other = _mint(OTHER, allowed_modules='["ai"]')
    assert _turn(client, sid, session_id="a", message="first question").status_code == 200
    assert _turn(client, sid, session_id="b", message="second question").status_code == 200
    assert _turn(client, other, session_id="theirs").status_code == 200

    body = client.get("/api/v1/ai/sessions", headers=_bearer(sid)).json()
    assert body["total"] == 2
    assert [s["session_id"] for s in body["data"]] == ["b", "a"]
    assert set(body["data"][0]) == {"session_id", "title", "model", "turns", "created_at", "updated_at"}
    assert body["data"][0]["title"] == "second question"

    assert client.get("/api/v1/ai/sessions?limit=1", headers=_bearer(sid)).json()["data"] == body["data"][:1]
    assert client.get("/api/v1/ai/sessions?limit=0", headers=_bearer(sid)).status_code == 422
    assert client.get("/api/v1/ai/sessions?limit=201", headers=_bearer(sid)).status_code == 422


def test_detail_returns_the_transcript_but_never_the_blob(make_client, scripted_agent):
    scripted_agent["script"]["events"] = WITH_STATE
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    assert _turn(client, sid, session_id="d1").status_code == 200

    body = client.get("/api/v1/ai/sessions/d1", headers=_bearer(sid)).json()
    assert set(body) == {"session", "messages"}
    assert "blob" not in json.dumps(body)
    assert body["session"]["session_id"] == "d1" and body["session"]["turns"] == 1

    msgs = body["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert [m["seq"] for m in msgs] == [1, 2]
    assert set(msgs[0]) == {"seq", "role", "text", "tools", "usage", "error_code", "at"}
    assert msgs[0]["text"] == "how is client 123?"
    assert msgs[0]["tools"] is None and msgs[0]["usage"] is None
    assert msgs[1]["text"] == "Client 123 looks fine."
    assert msgs[1]["error_code"] is None
    assert msgs[1]["usage"] == {
        "input_tokens": 1000, "output_tokens": 200,
        "cache_read_input_tokens": 400, "cost_usd": pytest.approx(0.0046),
    }
    tools = msgs[1]["tools"]
    assert [t["name"] for t in tools] == ["get_client_overview", "get_risk_signals"]
    assert tools[0]["ok"] is True and tools[0]["certified"] is True
    assert tools[0]["source"] == {"function": "net_gain_by_ids"}
    assert tools[0]["input"] == {"subject": {"kind": "client_id", "value": "123"}}
    assert tools[1]["ok"] is False and tools[1]["error_code"] == "scope_denied"
    assert tools[1]["source"] is None


def test_a_failed_turn_still_leaves_two_transcript_rows(make_client, scripted_agent):
    from app.services.ai_gateway_service import AgentUnavailable

    scripted_agent["script"]["raise"] = AgentUnavailable("agent unreachable")
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    assert _turn(client, sid, session_id="err").status_code == 200
    msgs = client.get("/api/v1/ai/sessions/err", headers=_bearer(sid)).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["text"] == ""
    assert msgs[1]["error_code"] == "agent_unavailable"


def test_a_quota_refused_turn_is_in_the_transcript_too(make_client, scripted_agent):
    client = make_client(AI_DAILY_TURNS_LIMIT="1")
    sid = _mint(STAFF, allowed_modules='["ai"]')
    assert _turn(client, sid, session_id="q").status_code == 200
    assert _turn(client, sid, session_id="q").status_code == 200
    msgs = client.get("/api/v1/ai/sessions/q", headers=_bearer(sid)).json()["messages"]
    assert [m["seq"] for m in msgs] == [1, 2, 3, 4]
    assert msgs[3]["error_code"] == "quota_exceeded"


def test_seq_keeps_counting_across_turns(make_client, scripted_agent):
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    for _ in range(3):
        assert _turn(client, sid, session_id="seq").status_code == 200
    msgs = client.get("/api/v1/ai/sessions/seq", headers=_bearer(sid)).json()["messages"]
    assert [m["seq"] for m in msgs] == [1, 2, 3, 4, 5, 6]


# ── delete / rename ──────────────────────────────────────────────────────────


def test_delete_soft_deletes_hides_from_list_and_then_404s(make_client, scripted_agent, tmp_path):
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    assert _turn(client, sid, session_id="del").status_code == 200

    r = client.delete("/api/v1/ai/sessions/del", headers=_bearer(sid))
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert client.get("/api/v1/ai/sessions", headers=_bearer(sid)).json()["total"] == 0
    assert client.get("/api/v1/ai/sessions/del", headers=_bearer(sid)).status_code == 404
    assert client.delete("/api/v1/ai/sessions/del", headers=_bearer(sid)).status_code == 404
    # a deleted session cannot be resumed either
    assert _turn(client, sid, session_id="del").status_code == 404

    conn = _sessions_db(tmp_path)
    row = conn.execute("SELECT deleted_at FROM ai_sessions WHERE session_id = 'del'").fetchone()
    assert row["deleted_at"] is not None  # soft, not hard
    assert conn.execute("SELECT COUNT(*) FROM ai_messages WHERE session_id = 'del'").fetchone()[0] == 2

    rows = _audit_rows(tmp_path)
    assert [r["action"] for r in rows] == ["ai.query.submit", "ai.session.delete"]
    assert rows[1]["target"] == "ai_session:del"
    assert rows[1]["actor_email"] == STAFF


def test_rename_writes_one_diff_row_and_none_for_a_noop(make_client, scripted_agent, tmp_path):
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    assert _turn(client, sid, session_id="rn").status_code == 200

    r = client.patch("/api/v1/ai/sessions/rn", json={"title": "  146530 调查  "}, headers=_bearer(sid))
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert client.get("/api/v1/ai/sessions/rn", headers=_bearer(sid)).json()["session"]["title"] == "146530 调查"

    rows = _audit_rows(tmp_path)
    assert [r["action"] for r in rows] == ["ai.query.submit", "ai.session.rename"]
    assert rows[1]["target"] == "ai_session:rn.title"
    assert rows[1]["new_value"] == "146530 调查"

    # Same title again: nothing changed, nothing recorded.
    assert client.patch("/api/v1/ai/sessions/rn", json={"title": "146530 调查"}, headers=_bearer(sid)).status_code == 200
    assert len(_audit_rows(tmp_path)) == 2


@pytest.mark.parametrize("title", ["", "   ", "x" * 121])
def test_rename_validates_the_title(make_client, scripted_agent, title):
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    assert _turn(client, sid, session_id="v").status_code == 200
    assert client.patch("/api/v1/ai/sessions/v", json={"title": title}, headers=_bearer(sid)).status_code == 422


# ── retention ────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path, monkeypatch):
    from app.core import ai_usage_db

    monkeypatch.setattr(ai_usage_db, "_DB_PATH", tmp_path / "ai_agent_ret.db")
    ai_usage_db.init_ai_usage_db()
    return ai_usage_db


def _seed(store, session_id: str, *, deleted_days_ago: int | None) -> None:
    store.create_session(session_id, 7, title="t", model="m")
    store.append_turn_messages(session_id, question="q", answer="a", tools=[], usage={}, error_code=None)
    if deleted_days_ago is not None:
        when = (datetime.now(timezone.utc) - timedelta(days=deleted_days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with store._connect() as conn:
            conn.execute("UPDATE ai_sessions SET deleted_at = ? WHERE session_id = ?", (when, session_id))


def _count(store, table: str) -> int:
    with store._connect() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_retention_zero_keeps_everything(store):
    _seed(store, "old-deleted", deleted_days_ago=400)
    assert store.purge_ai_sessions(0) == 0
    assert _count(store, "ai_sessions") == 1 and _count(store, "ai_messages") == 2


def test_retention_purges_only_soft_deleted_rows_past_the_cutoff(store):
    _seed(store, "old-deleted", deleted_days_ago=91)
    _seed(store, "recent-deleted", deleted_days_ago=1)
    _seed(store, "old-live", deleted_days_ago=None)
    with store._connect() as conn:  # an ancient but never-deleted session
        conn.execute("UPDATE ai_sessions SET updated_at = '2020-01-01T00:00:00Z' WHERE session_id = 'old-live'")

    assert store.purge_ai_sessions(90) == 1
    with store._connect() as conn:
        left = sorted(r[0] for r in conn.execute("SELECT session_id FROM ai_sessions").fetchall())
        msgs = sorted(r[0] for r in conn.execute("SELECT DISTINCT session_id FROM ai_messages").fetchall())
    assert left == ["old-live", "recent-deleted"]
    assert msgs == ["old-live", "recent-deleted"]


def test_retention_never_touches_live_rows_even_at_one_day(store):
    _seed(store, "live", deleted_days_ago=None)
    with store._connect() as conn:
        conn.execute("UPDATE ai_sessions SET updated_at = '2020-01-01T00:00:00Z', created_at = '2020-01-01T00:00:00Z'")
    assert store.purge_ai_sessions(1) == 0
    assert _count(store, "ai_sessions") == 1


def test_the_setting_defaults_to_90_and_reads_env(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.delenv("AI_SESSION_RETENTION_DAYS", raising=False)
    get_settings.cache_clear()
    assert get_settings().AI_SESSION_RETENTION_DAYS == 90
    monkeypatch.setenv("AI_SESSION_RETENTION_DAYS", "0")
    get_settings.cache_clear()
    assert get_settings().AI_SESSION_RETENTION_DAYS == 0
    get_settings.cache_clear()


def test_the_retention_job_is_on_the_scheduler_at_0410_hkt(monkeypatch):
    from app.core import scheduler as sched
    from app.services import ib_financial_service

    monkeypatch.setattr(sched, "_scheduler", None)
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setattr(ib_financial_service, "get_report_config", lambda: {"schedule_time": "17:00"})
    monkeypatch.setattr(sched, "_dispatch_digest_mails_job", lambda: None)
    monkeypatch.setattr(sched, "_send_daily_report", lambda: None)
    sched.start_scheduler()
    try:
        job = sched._scheduler.get_job(sched.AI_SESSIONS_RETENTION_JOB_ID)
        assert job is not None and job.func is sched._ai_sessions_retention_job
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert fields["hour"] == "4" and fields["minute"] == "10"
        assert str(job.trigger.timezone) == "Asia/Hong_Kong"
    finally:
        sched.stop_scheduler()


def test_the_job_calls_purge_with_the_configured_days(store, monkeypatch):
    from app.core import scheduler as sched
    from app.core.config import get_settings

    monkeypatch.setenv("AI_SESSION_RETENTION_DAYS", "30")
    get_settings.cache_clear()
    seen: list[int] = []
    monkeypatch.setattr(store, "purge_ai_sessions", lambda days: seen.append(days) or 3)
    sched._ai_sessions_retention_job()
    assert seen == [30]
    get_settings.cache_clear()


def test_the_lifespan_complement_is_wired():
    """The startup pass covers a box that was off across the 04:10 window."""
    import inspect

    import app.main as app_main

    assert "purge_ai_sessions(" in inspect.getsource(app_main.lifespan)
