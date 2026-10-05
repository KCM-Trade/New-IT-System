"""Session memory wiring in the agent harness (docs/ai-agent/02-contracts.md §8.3 / §8.5).

No model, no key, no network: ``Agent`` and ``get_client`` are replaced by
fakes. What is pinned:

  * ``store: False`` reaches the Agent's default options (the Responses client
    stores transcripts server-side by default — that is the whole failure
    02 §8.5 rules out);
  * the summariser's client is wrapped so ITS calls carry ``store: False`` too;
  * a blob is rehydrated with ``AgentSession.from_dict``; garbage falls back to
    a fresh session and says so (``rehydrated: false``) instead of failing;
  * ``session_state`` is emitted before ``usage``/``done`` on the success path
    AND on the model-error path, with ``turns`` = user messages in history;
  * the system prompt is not stored in the history (instructions are a per-call
    option), so the dated "## Today" tail cannot accumulate in the blob.
"""

from __future__ import annotations

import pytest

pytest.importorskip("agent_framework")

from agent_framework import AgentSession, InMemoryHistoryProvider, Message  # noqa: E402

from app.ai_agent import harness  # noqa: E402
from app.ai_agent.tools import CallerCtx  # noqa: E402

@pytest.fixture
def anyio_backend():
    # asyncio only: trio is not installed in either venv and the code under
    # test uses asyncio.Queue / create_task directly.
    return "asyncio"


CTX = CallerCtx(user_id=7, email="staff@kohleservices.com", role="user", allowed_modules=("ai",), scope=None, trace_id="t-1")


class _Update:
    def __init__(self, contents):
        self.contents = contents


class _Text:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Usage:
    type = "usage"

    def __init__(self, i, o):
        self.usage_details = {"input_token_count": i, "output_token_count": o, "cache_read_input_token_count": 0}


class FakeAgent:
    """Stands in for agent_framework.Agent: records its kwargs and, like the
    real InMemoryHistoryProvider, appends the turn to session.state."""

    last_kwargs: dict = {}
    fail = False

    def __init__(self, **kwargs):
        FakeAgent.last_kwargs = kwargs

    def run(self, message, *, session, stream):
        assert stream is True

        async def gen():
            history = session.state.setdefault(harness.HISTORY_SOURCE_ID, {}).setdefault("messages", [])
            history.append(Message(role="user", contents=[message]))
            yield _Update([_Text("hello "), _Usage(100, 10)])
            if FakeAgent.fail:
                raise RuntimeError("boom")
            history.append(Message(role="assistant", contents=["hello world"]))
            yield _Update([_Text("world")])

        return gen()


@pytest.fixture
def fake_agent(monkeypatch):
    FakeAgent.fail = False
    FakeAgent.last_kwargs = {}
    monkeypatch.setattr(harness, "Agent", FakeAgent)
    monkeypatch.setattr(harness, "get_client", lambda model: object())
    # The real strategies need a chat client; the wiring under test is the
    # provider list and the options, not the compaction maths.
    monkeypatch.setattr(harness, "build_compaction_strategy", lambda: (lambda messages: False))
    return FakeAgent


async def _collect(gen):
    return [item async for item in gen]


@pytest.mark.anyio
async def test_store_false_is_an_agent_default_and_history_provider_is_wired(fake_agent):
    await _collect(harness.run_turn(CTX, "hi", "gpt-5.6-terra"))
    kw = fake_agent.last_kwargs
    assert kw["default_options"] == {"store": False}
    providers = kw["context_providers"]
    assert any(isinstance(p, InMemoryHistoryProvider) for p in providers)
    assert providers[0].source_id == harness.HISTORY_SOURCE_ID
    # instructions ride as an option, not as a stored message (see module doc)
    assert "## Today" in kw["instructions"]


@pytest.mark.anyio
async def test_new_session_emits_session_state_before_usage_and_done(fake_agent):
    events = await _collect(harness.run_turn(CTX, "hi", "gpt-5.6-terra"))
    names = [e for e, _ in events]
    assert names.index("session_state") < names.index("usage") < names.index("done")
    state = dict(events)["session_state"]
    assert set(state) == {"blob", "turns", "rehydrated"}
    assert state["rehydrated"] is False
    assert state["turns"] == 1
    # the blob round-trips and carries the messages, never the system prompt
    restored = AgentSession.from_dict(state["blob"])
    roles = [harness._message_role(m) for m in restored.state[harness.HISTORY_SOURCE_ID]["messages"]]
    assert roles == ["user", "assistant"]
    assert "## Today" not in str(state["blob"])


@pytest.mark.anyio
async def test_blob_is_rehydrated_and_turns_count_user_messages(fake_agent):
    first = dict(await _collect(harness.run_turn(CTX, "q1", "gpt-5.6-terra")))["session_state"]
    second = dict(await _collect(harness.run_turn(CTX, "q2", "gpt-5.6-terra", session_blob=first["blob"])))["session_state"]
    assert second["rehydrated"] is True
    assert second["turns"] == 2
    texts = str(second["blob"])
    assert "q1" in texts and "q2" in texts


@pytest.mark.anyio
async def test_garbage_blob_starts_fresh_and_says_so(fake_agent, caplog):
    events = dict(await _collect(harness.run_turn(CTX, "q", "gpt-5.6-terra", session_blob={"state": "not-a-dict", "x": object})))
    assert events["session_state"]["rehydrated"] is False
    assert events["session_state"]["turns"] == 1
    assert events["done"]["terminal_reason"] == "end_turn"
    assert "could not be restored" in caplog.text


@pytest.mark.anyio
async def test_model_error_still_returns_the_session_state(fake_agent):
    fake_agent.fail = True
    events = await _collect(harness.run_turn(CTX, "q", "gpt-5.6-terra"))
    names = [e for e, _ in events]
    assert names[-3:] == ["usage", "error", "done"]
    assert "session_state" in names and names.index("session_state") < names.index("usage")
    assert dict(events)["error"]["code"] == "model_error"


def test_restore_session_helper_contract():
    session, ok = harness.restore_session(None, "t")
    assert ok is False and isinstance(session, AgentSession)
    blob = AgentSession().to_dict()
    session, ok = harness.restore_session(blob, "t")
    assert ok is True and isinstance(session, AgentSession)


def test_session_turns_ignores_non_user_roles():
    s = AgentSession()
    s.state[harness.HISTORY_SOURCE_ID] = {
        "messages": [
            Message(role="user", contents=["a"]),
            Message(role="assistant", contents=["b"]),
            {"role": "user"},          # serialised form must count too
            {"role": "system"},
        ]
    }
    assert harness.session_turns(s) == 2
    assert harness.session_turns(AgentSession()) == 0


@pytest.mark.anyio
async def test_summariser_client_wrapper_forces_store_false():
    seen = {}

    class Inner:
        async def get_response(self, messages, *, stream=False, options=None, **kw):
            seen["options"] = options
            seen["stream"] = stream
            return "resp"

    wrapped = harness._StoreOffClient(Inner())
    assert await wrapped.get_response(["m"], stream=False) == "resp"
    assert seen["options"] == {"store": False} and seen["stream"] is False
    # an explicit caller option survives, store is still forced off
    await wrapped.get_response(["m"], options={"store": True, "temperature": 0})
    assert seen["options"] == {"store": False, "temperature": 0}


def test_summary_model_env_precedence(monkeypatch):
    monkeypatch.delenv("AI_AGENT_MODEL_SUMMARY", raising=False)
    monkeypatch.delenv("AI_AGENT_MODEL_SMALL", raising=False)
    assert harness.summary_model() == "gpt-5.6-luna"
    monkeypatch.setenv("AI_AGENT_MODEL_SMALL", "small-dep")
    assert harness.summary_model() == "small-dep"
    monkeypatch.setenv("AI_AGENT_MODEL_SUMMARY", "summary-dep")
    assert harness.summary_model() == "summary-dep"


def test_session_tokenizer_is_calibrated_for_json_history():
    """3 chars/token, not the framework's 4: measured against billed input on
    2026-09-27 (see ESTIMATOR_CHARS_PER_TOKEN). A 4-char estimate sat under the
    budget while the model was already billed past it, so compaction never ran."""
    tok = harness.SessionTokenizer()
    assert tok.count_tokens("") == 1
    assert tok.count_tokens("x" * 3000) == 1000
    assert harness.ESTIMATOR_CHARS_PER_TOKEN == 3
    assert harness.SESSION_TOKEN_BUDGET == 32_000


def test_history_provider_skips_excluded_messages(fake_agent):
    """Compaction leaves excluded originals in the stored state (their
    annotations must survive); the provider must not load them back into the
    model's context or the budget is meaningless."""
    import asyncio

    asyncio.run(_collect(harness.run_turn(CTX, "hi", "gpt-5.6-terra")))
    provider = fake_agent.last_kwargs["context_providers"][0]
    assert isinstance(provider, InMemoryHistoryProvider)
    assert provider.skip_excluded is True


def test_selectable_models_agree_across_layers(monkeypatch):
    """The page schema, the agent's allow-list and the price table must name
    the same deployments: a model the schema accepts but the agent rejects is
    a 400 mid-stream, and one without a price bills at $0 against the quota."""
    from typing import get_args

    from app.core.config import _DEFAULT_MODEL_PRICES
    from app.schemas.ai import AiModel

    for name in ("AZURE_OPENAI_CHAT_MODEL", "AI_AGENT_MODEL_DEEP", "AI_AGENT_MODEL_FRONTIER"):
        monkeypatch.delenv(name, raising=False)
    assert set(harness.allowed_models()) == set(get_args(AiModel))
    assert "gpt-6.1-sol" in harness.allowed_models()
    assert set(get_args(AiModel)) <= set(_DEFAULT_MODEL_PRICES)
