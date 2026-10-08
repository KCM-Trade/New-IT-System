"""Main-API side of the `search_web` tool (OPT-0078).

The agent container reports each search on its own `tool_done`; this process
pairs it to the right call, charges it, audits it and decides what the browser
and the transcript may keep. Pinned here:

  * the internal request says `web_search: true` for a single-model turn
    (the compare side is in test_ai_compare.py);
  * `tool_done` is paired by `call_id`, so two concurrent searches finishing
    in reverse order keep their own citations; no `call_id` = the old rule;
  * `citations` / `queries` reach `ai_messages.tools_json`, absent when empty;
  * a search is billed WHEN ITS `tool_done` ARRIVES — also when the turn never
    reaches `usage` (agent died, browser gone);
  * the inner model's tokens are priced at ITS row and never folded into the
    turn's token counts; an unpriced search model or a missing Bing price is
    never $0;
  * one parseable `ai.web_search.query` audit row per call, `sent: false` for
    a refused one;
  * `search` (the accounting) never reaches the browser.

Harness: the fixtures of test_ai_route.py (AUTH_* pinned, stores in tmp, the
httpx seam scripted).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_ai_route import (  # noqa: F401 — fixtures register on import
    STAFF,
    _audit_rows,
    _bearer,
    _mint,
    _parse,
    make_client,
    scripted_agent,
)

LUNA = "gpt-5.6-luna"
TERRA = "gpt-5.6-terra"
BING_PER_REQUEST = 14.0 / 1000


def _search(query: str, *, requests: int = 2, tokens_in: int = 8000, tokens_out: int = 300, sent: bool = True, model: str = LUNA) -> dict:
    return {
        "model": model,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "num_requests": requests,
        "sent": sent,
        "query": query,
    }


def _use(call_id: str | None, query: str) -> tuple[str, Any]:
    data: dict[str, Any] = {"name": "search_web", "input": {"query": query}}
    if call_id is not None:
        data["call_id"] = call_id
    return ("tool_use", data)


def _done(call_id: str | None, query: str, *, cites: list[dict] | None = None, queries: list[str] | None = None, **search: Any) -> tuple[str, Any]:
    data: dict[str, Any] = {
        "name": "search_web",
        "ok": True,
        "source": {"service": "web", "certified": False},
        "certified": False,
        "search": _search(query, **search),
    }
    if call_id is not None:
        data["call_id"] = call_id
    if cites:
        data["citations"] = cites
    if queries:
        data["queries"] = queries
    return ("tool_done", data)


def _refused(call_id: str, query: str, code: str) -> tuple[str, Any]:
    return (
        "tool_done",
        {
            "name": "search_web",
            "ok": False,
            "source": None,
            "certified": False,
            "error_code": code,
            "call_id": call_id,
            "search": _search(query, requests=0, tokens_in=0, tokens_out=0, sent=False),
        },
    )


USAGE = ("usage", {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 0, "cost_usd": None})
DONE = ("done", {"terminal_reason": "end_turn", "num_turns": 2})

CITE_A = [{"title": "Fed statement", "url": "https://www.federalreserve.gov/a"}]
CITE_B = [{"title": "ECB decision", "url": "https://www.ecb.europa.eu/b"}]


def _turn(client, sid, **body):
    return client.post("/api/v1/ai/turn", json={"message": "what moved gold today?", **body}, headers=_bearer(sid))


def _tools_json(tmp_path) -> list[dict]:
    conn = sqlite3.connect(str(tmp_path / "ai_agent_test.db"))
    try:
        row = conn.execute(
            "SELECT tools_json FROM ai_messages WHERE role = 'assistant' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row[0])


def _usage_today() -> dict:
    from app.core import ai_usage_db

    conn = sqlite3.connect(str(ai_usage_db._DB_PATH))
    try:
        uid = conn.execute("SELECT user_id FROM ai_usage_daily ORDER BY user_id DESC LIMIT 1").fetchone()[0]
    finally:
        conn.close()
    return ai_usage_db.get_usage(uid, ai_usage_db.today_hk())


def _search_rows(tmp_path) -> list[dict]:
    return [json.loads(r["new_value"]) for r in _audit_rows(tmp_path) if r["action"] == "ai.web_search.query"]


def _luna_cost(tokens_in: int, tokens_out: int, requests: int) -> float:
    return round((tokens_in * 0.2 + tokens_out * 1.2) / 1_000_000 + requests * BING_PER_REQUEST, 6)


# ── the internal request ─────────────────────────────────────────────────────


def test_a_single_model_turn_asks_for_web_search(make_client, scripted_agent):
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    assert _turn(client, sid).status_code == 200
    assert scripted_agent["calls"][0]["payload"]["web_search"] is True


# ── pairing ──────────────────────────────────────────────────────────────────


def test_two_concurrent_searches_finishing_in_reverse_order_keep_their_own_results(
    make_client, scripted_agent, tmp_path
):
    scripted_agent["script"]["events"] = [
        _use("c1", "fed decision"),
        _use("c2", "ecb decision"),
        _done("c2", "ecb decision", cites=CITE_B, queries=["ecb rate decision"]),
        _done("c1", "fed decision", cites=CITE_A, queries=["fomc decision"]),
        USAGE,
        DONE,
    ]
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    assert _turn(client, sid).status_code == 200
    first, second = _tools_json(tmp_path)
    assert (first["call_id"], first["input"]["query"]) == ("c1", "fed decision")
    assert first["citations"] == CITE_A and first["queries"] == ["fomc decision"]
    assert (second["call_id"], second["input"]["query"]) == ("c2", "ecb decision")
    assert second["citations"] == CITE_B and second["queries"] == ["ecb rate decision"]
    assert first["ok"] is True and second["ok"] is True
    # The audit rows carry the right query for each call id too.
    by_call = {row["call_id"]: row for row in _search_rows(tmp_path)}
    assert by_call["c1"]["query"] == "fed decision" and by_call["c1"]["queries"] == ["fomc decision"]
    assert by_call["c2"]["query"] == "ecb decision" and by_call["c2"]["queries"] == ["ecb rate decision"]


def test_events_without_a_call_id_fall_back_to_oldest_pending_of_the_same_name():
    from app.api.v1.routes.ai import _resolve_tool_entry

    def _pending(name: str) -> dict:
        return {"name": name, "ok": None, "certified": False, "source": None, "error_code": None, "input": None}

    entries = [_pending("get_client_overview"), _pending("search_web"), _pending("search_web")]
    _resolve_tool_entry(entries, {"name": "search_web", "ok": False, "error_code": "web_search_timeout"})
    assert [e["ok"] for e in entries] == [None, False, None]
    assert entries[1]["error_code"] == "web_search_timeout"
    assert all("call_id" not in e for e in entries)
    _resolve_tool_entry(entries, {"name": "search_web", "ok": True, "source": {"service": "web"}})
    assert [e["ok"] for e in entries] == [None, False, True]


def test_a_done_with_an_unknown_call_id_does_not_resolve_another_calls_entry():
    from app.api.v1.routes.ai import _resolve_tool_entry

    entries = [
        {"name": "search_web", "ok": None, "certified": False, "source": None, "error_code": None, "input": {"query": "a"}, "call_id": "c1"},
    ]
    _resolve_tool_entry(entries, {"name": "search_web", "ok": True, "call_id": "c9", "citations": CITE_B})
    assert entries[0]["ok"] is None and "citations" not in entries[0]
    assert entries[1]["call_id"] == "c9" and entries[1]["citations"] == CITE_B


# ── what is stored ───────────────────────────────────────────────────────────


def test_citations_and_queries_are_absent_when_empty_and_cleaned_when_present(make_client, scripted_agent, tmp_path):
    dirty = [
        {"title": "ok", "url": "https://example.com/a"},
        {"title": "dup", "url": "https://example.com/a"},
        {"title": "script", "url": "javascript:alert(1)"},
        {"title": "data", "url": "data:text/html,x"},
        {"title": "T" * 500, "url": "http://example.com/b"},
        {"title": "long", "url": "https://example.com/" + "x" * 600},
        "not a dict",
    ] + [{"title": str(i), "url": f"https://example.com/{i}"} for i in range(20)]
    scripted_agent["script"]["events"] = [
        _use("c1", "empty one"),
        _done("c1", "empty one"),
        _use("c2", "dirty one"),
        _done("c2", "dirty one", cites=dirty, queries=["q" * 400, "", 7, "fine"]),
        USAGE,
        DONE,
    ]
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    r = _turn(client, sid)
    empty, cleaned = _tools_json(tmp_path)
    assert "citations" not in empty and "queries" not in empty
    assert len(cleaned["citations"]) == 10
    urls = [c["url"] for c in cleaned["citations"]]
    assert urls[:2] == ["https://example.com/a", "http://example.com/b"]
    assert all(u.startswith(("http://", "https://")) and len(u) <= 500 for u in urls)
    assert len(set(urls)) == len(urls)
    assert all(len(c["title"]) <= 200 for c in cleaned["citations"])
    assert cleaned["queries"] == ["q" * 200, "fine"]
    # Refreshing the page reads the same entries back.
    session_id = next(d["session_id"] for e, d in _parse(r.text) if e == "init")
    detail = client.get(f"/api/v1/ai/sessions/{session_id}", headers=_bearer(sid)).json()
    assert "example.com/a" in json.dumps(detail)


def test_search_accounting_is_stripped_from_the_browser_stream(make_client, scripted_agent):
    scripted_agent["script"]["events"] = [_use("c1", "fed"), _done("c1", "fed", cites=CITE_A, queries=["fomc"]), USAGE, DONE]
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    events = _parse(_turn(client, sid).text)
    done = next(d for e, d in events if e == "tool_done")
    assert "search" not in done
    assert done["call_id"] == "c1" and done["citations"] == CITE_A and done["queries"] == ["fomc"]
    assert next(d for e, d in events if e == "tool_use")["call_id"] == "c1"


# ── billing ──────────────────────────────────────────────────────────────────


def test_search_cost_is_charged_at_the_search_models_price_and_tokens_stay_out_of_the_turn(
    make_client, scripted_agent, tmp_path
):
    scripted_agent["script"]["events"] = [
        _use("c1", "fed"),
        _done("c1", "fed", requests=3, tokens_in=10_000, tokens_out=500),
        USAGE,
        DONE,
    ]
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    events = _parse(_turn(client, sid).text)
    search_usd = _luna_cost(10_000, 500, 3)
    main_usd = round((1000 * 2.0 + 200 * 12.0) / 1_000_000, 6)
    assert search_usd == pytest.approx(0.0446)

    usage = _usage_today()
    assert usage["cost_usd"] == pytest.approx(search_usd + main_usd)
    # The inner model's 10,500 tokens are nowhere in the token counters.
    assert usage["input_tokens"] == 1000 and usage["output_tokens"] == 200

    shown = next(d for e, d in events if e == "usage")
    assert shown["input_tokens"] == 1000 and shown["output_tokens"] == 200
    assert shown["cost_usd"] == pytest.approx(search_usd + main_usd)

    turn = next(json.loads(r["new_value"]) for r in _audit_rows(tmp_path) if r["action"] == "ai.query.submit")
    assert turn["input_tokens"] == 1000 and turn["output_tokens"] == 200
    assert turn["cost_usd"] == pytest.approx(search_usd + main_usd)
    assert turn["web_search_requests"] == 3


def test_a_search_is_already_charged_when_the_turn_never_reaches_usage(make_client, scripted_agent, tmp_path):
    """The agent stream dies right after the search (wall clock, crash): no
    `usage`, no `done` — the Bing requests were made all the same."""
    scripted_agent["script"]["events"] = [_use("c1", "fed"), _done("c1", "fed", requests=4, tokens_in=0, tokens_out=0)]
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    events = _parse(_turn(client, sid).text)
    assert ("error" in [e for e, _ in events]) and events[-1][0] == "done"
    assert _usage_today()["cost_usd"] == pytest.approx(4 * BING_PER_REQUEST)
    assert len(_search_rows(tmp_path)) == 1


def test_a_search_is_charged_when_the_user_stops_the_turn(make_client, scripted_agent, tmp_path, monkeypatch):
    """Stop: the relay ends after the first frame, the worker drains. The
    search finished during the drain is charged and audited."""
    from starlette.requests import Request

    scripted_agent["script"]["events"] = [_use("c1", "fed"), _done("c1", "fed", requests=2, tokens_in=0, tokens_out=0)]
    calls = {"n": 0}

    async def _gone_after_first_frame(self):  # noqa: ANN001
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(Request, "is_disconnected", _gone_after_first_frame)
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    with client:
        r = _turn(client, sid, session_id="s-stop")
        assert r.status_code == 200
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            conn = sqlite3.connect(str(tmp_path / "ai_agent_test.db"))
            claim = conn.execute("SELECT turn_started_at FROM ai_sessions WHERE session_id = 's-stop'").fetchone()[0]
            conn.close()
            if claim is None:
                break
            time.sleep(0.05)
    assert _usage_today()["cost_usd"] == pytest.approx(2 * BING_PER_REQUEST)
    rows = _search_rows(tmp_path)
    assert len(rows) == 1 and rows[0]["sent"] is True and rows[0]["num_requests"] == 2


def _settings(**over: Any) -> Any:
    base = {
        "AI_MODEL_PRICES": {LUNA: (0.2, 1.2), TERRA: (2.0, 12.0)},
        "AI_WEB_SEARCH_USD_PER_1K_REQUESTS": 14.0,
        "AI_WEB_SEARCH_FALLBACK_PRICE": (5.0, 30.0),
    }
    return SimpleNamespace(**{**base, **over})


def test_an_unpriced_search_model_is_charged_the_fallback_and_logged(caplog):
    from app.services import ai_gateway_service as gateway

    with caplog.at_level(logging.ERROR):
        usd = gateway.compute_search_cost_usd(_settings(), "some-new-deployment", 10_000, 1_000, 0)
    assert usd == pytest.approx((10_000 * 5.0 + 1_000 * 30.0) / 1_000_000)
    assert usd > gateway.compute_search_cost_usd(_settings(), LUNA, 10_000, 1_000, 0) > 0
    assert any("no price row" in r.getMessage() and r.levelno == logging.ERROR for r in caplog.records)
    # The plain turn pricing still answers 0 for the same deployment — which
    # is exactly why the search path does not go through it.
    assert gateway.compute_cost_usd(_settings(), "some-new-deployment", 10_000, 1_000) == 0.0


@pytest.mark.parametrize("fallback", [None, (), (0.0, 30.0), ("x", "y")])
def test_a_broken_fallback_row_still_is_not_free(fallback):
    from app.services import ai_gateway_service as gateway

    assert gateway.compute_search_cost_usd(_settings(AI_WEB_SEARCH_FALLBACK_PRICE=fallback), "nope", 1_000_000, 0, 0) == pytest.approx(5.0)


@pytest.mark.parametrize("price", [None, 0, 0.0, -3, float("nan"), "abc"])
def test_a_missing_bing_price_is_charged_the_default_and_logged(price, caplog):
    from app.services import ai_gateway_service as gateway

    with caplog.at_level(logging.ERROR):
        usd = gateway.compute_search_cost_usd(_settings(AI_WEB_SEARCH_USD_PER_1K_REQUESTS=price), LUNA, 0, 0, 10)
    assert usd == pytest.approx(0.14)
    assert any("AI_WEB_SEARCH_USD_PER_1K_REQUESTS" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("raw", ["0", "-1", "nan", "free", " "])
def test_the_bing_price_setting_never_parses_to_zero(raw, monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("AI_WEB_SEARCH_USD_PER_1K_REQUESTS", raw)
    monkeypatch.setenv("AI_WEB_SEARCH_FALLBACK_PRICE", "0,0")
    settings = Settings()
    assert settings.AI_WEB_SEARCH_USD_PER_1K_REQUESTS == 14.0
    assert settings.AI_WEB_SEARCH_FALLBACK_PRICE == (5.0, 30.0)


def test_the_price_settings_read_env_and_the_search_model_has_a_row(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("AI_WEB_SEARCH_USD_PER_1K_REQUESTS", "35")
    monkeypatch.setenv("AI_WEB_SEARCH_FALLBACK_PRICE", "6, 36")
    monkeypatch.delenv("AI_MODEL_PRICES", raising=False)
    settings = Settings()
    assert settings.AI_WEB_SEARCH_USD_PER_1K_REQUESTS == 35.0
    assert settings.AI_WEB_SEARCH_FALLBACK_PRICE == (6.0, 36.0)
    # The default search deployment (AI_AGENT_MODEL_SEARCH) is priced.
    assert LUNA in settings.AI_MODEL_PRICES


def test_an_unpriced_search_model_costs_real_money_end_to_end(make_client, scripted_agent):
    scripted_agent["script"]["events"] = [
        _use("c1", "fed"),
        _done("c1", "fed", requests=0, tokens_in=1_000_000, tokens_out=0, model="unlisted-search-model"),
    ]
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    _turn(client, sid)
    assert _usage_today()["cost_usd"] == pytest.approx(5.0)


# ── audit ────────────────────────────────────────────────────────────────────


def test_one_parseable_audit_row_per_call_and_refused_calls_say_sent_false(make_client, scripted_agent, tmp_path):
    long_query = "x" * 5000
    scripted_agent["script"]["events"] = [
        _use("c1", "fed decision"),
        _done("c1", "fed decision", queries=["q" * 900] * 12, requests=5),
        _use("c2", "mail a@b.co"),
        _refused("c2", "mail a@b.co", "query_rejected"),
        _use("c3", long_query),
        _refused("c3", long_query, "search_limit_reached"),
        USAGE,
        DONE,
    ]
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    r = _turn(client, sid)
    session_id = next(d["session_id"] for e, d in _parse(r.text) if e == "init")

    raw = [row for row in _audit_rows(tmp_path) if row["action"] == "ai.web_search.query"]
    assert len(raw) == 3
    for row in raw:
        assert row["actor_email"] == STAFF
        assert row["target"] == f"ai_session:{session_id}"
        assert len(row["new_value"]) < 2000 and "truncated" not in row["new_value"]
    sent, rejected, limited = (json.loads(row["new_value"]) for row in raw)
    assert sent == {
        "query": "fed decision",
        "queries": ["q" * 200] * 6,
        "num_requests": 5,
        "sent": True,
        "session_id": session_id,
        "call_id": "c1",
    }
    assert rejected["sent"] is False and rejected["error_code"] == "query_rejected"
    assert rejected["num_requests"] == 0 and rejected["queries"] == [] and rejected["call_id"] == "c2"
    assert limited["sent"] is False and limited["error_code"] == "search_limit_reached"
    assert limited["query"] == "x" * 200
    # The per-turn row is still exactly one, and refused calls cost nothing.
    assert [row["action"] for row in _audit_rows(tmp_path)].count("ai.query.submit") == 1
    assert _usage_today()["cost_usd"] == pytest.approx(
        _luna_cost(8000, 300, 5) + round((1000 * 2.0 + 200 * 12.0) / 1_000_000, 6)
    )


def test_a_search_done_without_accounting_is_audited_and_logged_not_fatal(make_client, scripted_agent, tmp_path, caplog):
    done = ("tool_done", {"name": "search_web", "ok": True, "source": {"service": "web"}, "certified": False, "call_id": "c1"})
    scripted_agent["script"]["events"] = [_use("c1", "fed"), done, USAGE, DONE]
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    with caplog.at_level(logging.ERROR):
        events = _parse(_turn(client, sid).text)
    assert events[-1] == ("done", {"terminal_reason": "end_turn", "num_turns": 2})
    (row,) = _search_rows(tmp_path)
    assert row["query"] == "fed" and row["sent"] is False and row["num_requests"] == 0
    assert any("without `search` accounting" in r.getMessage() for r in caplog.records)


# ── a search that never reports back (cold review #4) ────────────────────────


def _submit_row(tmp_path) -> dict:
    return [json.loads(r["new_value"]) for r in _audit_rows(tmp_path) if r["action"] == "ai.query.submit"][-1]


def test_a_search_with_no_tool_done_is_still_audited_and_charged(make_client, scripted_agent, tmp_path, caplog):
    """The agent dies mid-search: `tool_use` was seen, `tool_done` never
    comes. The query may already be at Bing, so it gets its audit row
    (sent = unknown) and an assumed charge."""
    from app.api.v1.routes import ai as ai_route

    scripted_agent["script"]["events"] = [_use("c1", "why did gold move")]
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    with caplog.at_level(logging.WARNING, logger=ai_route.logger.name):
        events = _parse(_turn(client, sid).text)
    assert events[-1][0] == "done"

    rows = _search_rows(tmp_path)
    assert rows == [
        {
            "query": "why did gold move",
            "queries": [],
            "num_requests": ai_route.UNSETTLED_SEARCH_ASSUMED_REQUESTS,
            "sent": None,
            "session_id": rows[0]["session_id"],
            "call_id": "c1",
            "error_code": "no_result",
        }
    ]
    assumed = ai_route.UNSETTLED_SEARCH_ASSUMED_REQUESTS * BING_PER_REQUEST
    assert assumed > 0
    assert _usage_today()["cost_usd"] == pytest.approx(assumed)
    submit = _submit_row(tmp_path)
    assert submit["web_search_requests"] == ai_route.UNSETTLED_SEARCH_ASSUMED_REQUESTS
    assert submit["cost_usd"] == pytest.approx(assumed)
    warned = [r for r in caplog.records if r.levelno == logging.WARNING and "ended without a result" in r.getMessage()]
    assert len(warned) == 1 and "c1" in warned[0].getMessage()


def test_exactly_one_audit_row_per_call_when_one_search_finishes_and_one_does_not(
    make_client, scripted_agent, tmp_path
):
    scripted_agent["script"]["events"] = [
        _use("c1", "fed"),
        _use("c2", "ecb"),
        _done("c2", "ecb", requests=2, tokens_in=0, tokens_out=0),
    ]
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    _turn(client, sid)
    rows = {r["call_id"]: r for r in _search_rows(tmp_path)}
    assert len(_search_rows(tmp_path)) == 2 and set(rows) == {"c1", "c2"}
    assert rows["c2"]["sent"] is True and "error_code" not in rows["c2"]
    assert rows["c1"]["sent"] is None and rows["c1"]["error_code"] == "no_result"
    from app.api.v1.routes.ai import UNSETTLED_SEARCH_ASSUMED_REQUESTS

    assert _usage_today()["cost_usd"] == pytest.approx((2 + UNSETTLED_SEARCH_ASSUMED_REQUESTS) * BING_PER_REQUEST)


def test_a_finished_or_refused_search_is_never_swept_a_second_time(make_client, scripted_agent, tmp_path):
    scripted_agent["script"]["events"] = [
        _use("c1", "fed"),
        _done("c1", "fed", requests=1, tokens_in=0, tokens_out=0),
        _use("c2", "acct 1-8522845"),
        _refused("c2", "acct 1-8522845", "query_rejected"),
        USAGE,
        DONE,
    ]
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    _turn(client, sid)
    rows = _search_rows(tmp_path)
    assert [r["call_id"] for r in rows] == ["c1", "c2"]
    assert all(r.get("error_code") != "no_result" for r in rows)


def test_a_search_in_flight_when_the_stream_raises_is_audited(make_client, monkeypatch, tmp_path):
    from app.services import ai_gateway_service as gateway

    async def _boom(settings, payload, *, token):
        yield _use("c1", "fed")
        raise RuntimeError("connection reset")

    monkeypatch.setattr(gateway, "open_agent_stream", _boom)
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    events = _parse(_turn(client, sid).text)
    assert [d.get("code") for e, d in events if e == "error"] == ["internal"]
    rows = _search_rows(tmp_path)
    assert len(rows) == 1 and rows[0]["sent"] is None and rows[0]["error_code"] == "no_result"


def test_a_search_in_flight_when_the_drain_deadline_passes_is_audited(make_client, monkeypatch, tmp_path):
    """Stop, then the agent stays silent past the drain window: this side
    closes the stream, so the agent can never deliver the `tool_done`."""
    import asyncio

    from starlette.requests import Request

    from app.api.v1.routes import ai as ai_route
    from app.services import ai_gateway_service as gateway

    async def _hangs(settings, payload, *, token):
        yield _use("c1", "fed")
        await asyncio.sleep(30)

    monkeypatch.setattr(gateway, "open_agent_stream", _hangs)
    monkeypatch.setattr(ai_route, "KEEPALIVE_SECONDS", 0.05)
    monkeypatch.setattr(ai_route, "DISCONNECT_DRAIN_SECONDS", 0.2)
    calls = {"n": 0}

    async def _gone_after_first_frame(self):  # noqa: ANN001
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(Request, "is_disconnected", _gone_after_first_frame)
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    with client:
        assert _turn(client, sid, session_id="s-drain").status_code == 200
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            conn = sqlite3.connect(str(tmp_path / "ai_agent_test.db"))
            claim = conn.execute("SELECT turn_started_at FROM ai_sessions WHERE session_id = 's-drain'").fetchone()[0]
            conn.close()
            if claim is None:
                break
            time.sleep(0.05)
    rows = _search_rows(tmp_path)
    assert len(rows) == 1 and rows[0]["call_id"] == "c1" and rows[0]["sent"] is None
    assert _usage_today()["cost_usd"] == pytest.approx(ai_route.UNSETTLED_SEARCH_ASSUMED_REQUESTS * BING_PER_REQUEST)


def test_a_compare_run_sweeps_its_own_unfinished_search(make_client, scripted_agent, tmp_path):
    """Compare runs never have the tool; if one ever does, each run's
    unfinished search still gets exactly one row."""
    scripted_agent["script"]["events"] = [_use("c1", "fed")]
    client = make_client()
    sid = _mint(STAFF, allowed_modules='["ai"]')
    r = _turn(client, sid, compare_models=[TERRA, "gpt-5.6-sol"])
    assert r.status_code == 200
    rows = _search_rows(tmp_path)
    assert len(rows) == 2 and all(r["error_code"] == "no_result" and r["sent"] is None for r in rows)


# ── the daily cost limit is re-checked after each search (cold review #8) ────


def test_the_turn_stops_with_quota_exceeded_when_a_search_crosses_the_cost_limit(
    make_client, scripted_agent, tmp_path
):
    scripted_agent["script"]["events"] = [
        ("text", {"delta": "Looking it up. "}),
        _use("c1", "fed"),
        _use("c2", "ecb"),
        _done("c1", "fed", cites=CITE_A, requests=4, tokens_in=0, tokens_out=0),  # 0.056 USD
        ("text", {"delta": "must never be read"}),
        _done("c2", "ecb", requests=4, tokens_in=0, tokens_out=0),
        USAGE,
        DONE,
    ]
    client = make_client(AI_DAILY_COST_LIMIT_USD="0.05")
    sid = _mint(STAFF, allowed_modules='["ai"]')
    events = _parse(_turn(client, sid, session_id="s-quota").text)
    names = [e for e, _ in events]

    # the search that crossed the line is still shown, then the same refusal
    # a pre-turn check gives, then the turn is over
    assert names == ["init", "text", "tool_use", "tool_use", "tool_done", "error", "done"]
    error = next(d for e, d in events if e == "error")
    assert error["code"] == "quota_exceeded" and error["message"].startswith("Daily quota reached (")
    assert "Resets at midnight Hong Kong time." in error["message"]
    assert events[-1][1]["terminal_reason"] == "error"

    # bookkeeping still ran: the finished search, the swept one, the turn row
    from app.api.v1.routes.ai import UNSETTLED_SEARCH_ASSUMED_REQUESTS

    rows = {r["call_id"]: r for r in _search_rows(tmp_path)}
    assert set(rows) == {"c1", "c2"} and len(_search_rows(tmp_path)) == 2
    assert rows["c1"]["sent"] is True and rows["c2"]["error_code"] == "no_result"
    assert _usage_today()["cost_usd"] == pytest.approx((4 + UNSETTLED_SEARCH_ASSUMED_REQUESTS) * BING_PER_REQUEST)
    submit = _submit_row(tmp_path)
    assert submit["error_code"] == "quota_exceeded" and submit["terminal_reason"] == "error"
    assert submit["tools_called"] == ["search_web", "search_web"]

    # transcript keeps the partial turn with its error; memory is not advanced
    conn = sqlite3.connect(str(tmp_path / "ai_agent_test.db"))
    try:
        answer, error_code = conn.execute(
            "SELECT text, error_code FROM ai_messages WHERE role = 'assistant' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        blob, claim = conn.execute(
            "SELECT blob, turn_started_at FROM ai_sessions WHERE session_id = 's-quota'"
        ).fetchone()
    finally:
        conn.close()
    assert answer == "Looking it up. " and error_code == "quota_exceeded"
    assert blob is None and claim is None

    # and the next turn is refused before it starts
    again = _parse(_turn(client, sid, session_id="s-quota").text)
    assert [d.get("code") for e, d in again if e == "error"] == ["quota_exceeded"]


def test_a_search_below_the_cost_limit_does_not_stop_the_turn(make_client, scripted_agent, tmp_path):
    scripted_agent["script"]["events"] = [
        _use("c1", "fed"),
        _done("c1", "fed", requests=2, tokens_in=0, tokens_out=0),
        ("text", {"delta": "answer"}),
        USAGE,
        DONE,
    ]
    client = make_client(AI_DAILY_COST_LIMIT_USD="0.05")
    sid = _mint(STAFF, allowed_modules='["ai"]')
    events = _parse(_turn(client, sid).text)
    assert "error" not in [e for e, _ in events]
    assert events[-1][1]["terminal_reason"] == "end_turn"


def test_a_failed_quota_reread_does_not_end_the_turn(make_client, scripted_agent, monkeypatch):
    import asyncio

    from app.api.v1.routes import ai as ai_route

    def _broken(uid, day):
        raise sqlite3.OperationalError("database is locked")

    client = make_client()
    monkeypatch.setattr(ai_route.ai_usage_db, "get_usage", _broken)
    assert asyncio.run(ai_route._cost_quota_message(ai_route.get_settings(), 1, "2026-10-08", "t")) is None


def test_the_bing_only_price_is_never_zero(caplog):
    from app.services import ai_gateway_service as gateway

    assert gateway.compute_search_requests_cost_usd(_settings(), 4) == pytest.approx(4 * BING_PER_REQUEST)
    with caplog.at_level(logging.ERROR, logger=gateway.logger.name):
        usd = gateway.compute_search_requests_cost_usd(_settings(AI_WEB_SEARCH_USD_PER_1K_REQUESTS=0), 4)
    assert usd == pytest.approx(4 * BING_PER_REQUEST)
    assert any(r.levelno == logging.ERROR for r in caplog.records)
