"""Internal surface of the ai-agent container (docs/ai-agent/02-contracts.md §4.2).

The harness is monkeypatched with a scripted turn so no model, no key and no
network are involved; what is pinned is the door (token), the model
allow-list, and the SSE wire format the main API relays verbatim.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# The agent harness needs agent-framework-openai, which lives in
# requirements-ai-agent.txt (the ai-agent image), NOT in requirements.txt. A
# venv built from the main file must skip these tests at collection instead of
# erroring the whole run — the same trap as the backend/public worktree case.
pytest.importorskip("agent_framework")

TOKEN = "x" * 40
BODY = {
    "caller": {"user_id": 7, "email": "staff@kohleservices.com", "role": "user", "allowed_modules": ["ai"]},
    "scope": None,
    "session_id": "sess-1",
    "message": "客户 123456 怎么样",
    "model": "gpt-5.6-terra",
    "trace_id": "trace-1",
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AI_AGENT_INTERNAL_TOKEN", TOKEN)
    monkeypatch.delenv("AZURE_OPENAI_CHAT_MODEL", raising=False)
    monkeypatch.delenv("AI_AGENT_MODEL_DEEP", raising=False)
    from app.ai_agent import server

    with TestClient(server.app) as c:
        yield c


@pytest.fixture
def scripted_turn(monkeypatch):
    from app.ai_agent import harness

    seen: dict = {}

    async def fake_run_turn(ctx, message, model):
        seen["ctx"] = ctx
        seen["message"] = message
        seen["model"] = model
        yield ("tool_use", {"name": "get_client_overview", "input": {"subject": {"kind": "client_id", "value": "123456"}}})
        yield ("tool_done", {"name": "get_client_overview", "ok": True, "source": {"function": "f", "certified": True}, "certified": True})
        yield ("text", {"delta": "Client 123456 "})
        yield ("text", {"delta": "looks fine."})
        yield ("usage", {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0, "cost_usd": None})
        yield ("done", {"terminal_reason": "end_turn", "num_turns": 2})

    monkeypatch.setattr(harness, "run_turn", fake_run_turn)
    return seen


def test_health_needs_no_token(client):
    assert client.get("/health").json() == {"ok": True}


def test_turn_refuses_missing_or_wrong_token(client, scripted_turn):
    assert client.post("/v1/turn", json=BODY).status_code == 401
    assert client.post("/v1/turn", json=BODY, headers={"X-Internal-Token": "y" * 40}).status_code == 401
    assert not scripted_turn  # the harness was never reached


def test_turn_refuses_unknown_model(client, scripted_turn):
    r = client.post("/v1/turn", json={**BODY, "model": "gpt-4o"}, headers={"X-Internal-Token": TOKEN})
    assert r.status_code == 400


def test_turn_streams_scripted_events(client, scripted_turn):
    r = client.post("/v1/turn", json=BODY, headers={"X-Internal-Token": TOKEN})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    text = r.text
    assert "event: tool_use\ndata: {\"name\": \"get_client_overview\"" in text
    assert "event: text\ndata: {\"delta\": \"Client 123456 \"}" in text
    assert "event: usage\n" in text and "event: done\n" in text
    assert "event: init" not in text  # the main API emits init, not the agent
    assert scripted_turn["model"] == "gpt-5.6-terra" and scripted_turn["message"] == BODY["message"]
    assert scripted_turn["ctx"].user_id == 7 and scripted_turn["ctx"].scope is None


def test_turn_passes_restricted_scope_through(client, scripted_turn):
    client.post("/v1/turn", json={**BODY, "scope": [1]}, headers={"X-Internal-Token": TOKEN})
    assert scripted_turn["ctx"].scope == frozenset({1})


def test_turn_crash_ends_with_error_and_done(client, monkeypatch):
    from app.ai_agent import harness

    async def boom(ctx, message, model):
        yield ("text", {"delta": "partial"})
        raise RuntimeError("secret connection string")

    monkeypatch.setattr(harness, "run_turn", boom)
    r = client.post("/v1/turn", json=BODY, headers={"X-Internal-Token": TOKEN})
    assert r.status_code == 200
    assert "event: error\n" in r.text and "\"code\": \"internal\"" in r.text
    assert "secret connection string" not in r.text
    assert r.text.rstrip().endswith("\"terminal_reason\": \"error\", \"num_turns\": 0}")


def test_turn_503_when_token_not_configured(monkeypatch):
    monkeypatch.setenv("AI_AGENT_INTERNAL_TOKEN", "short")
    from app.ai_agent import server

    with TestClient(server.app) as c:
        assert c.post("/v1/turn", json=BODY, headers={"X-Internal-Token": "short"}).status_code == 503
