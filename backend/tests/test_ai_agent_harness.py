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

import re
from pathlib import Path

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
    from app.schemas.ai import DEFAULT_MODEL, AiModel

    for env_name, _ in harness.SELECTABLE_MODELS:
        monkeypatch.delenv(env_name, raising=False)
    assert harness.allowed_models() == get_args(AiModel)
    assert harness.allowed_models() == (
        "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6.1-sol", "grok-4.7", "DeepSeek-V4-Pro",
    )
    assert harness.default_model() == DEFAULT_MODEL == "gpt-5.6-terra"
    assert set(get_args(AiModel)) <= set(_DEFAULT_MODEL_PRICES)

    # The page's own list, read from source: there is no shared artefact
    # between the Python and TypeScript sides, so this is the only place the
    # three lists can be compared.
    frontend = Path(__file__).resolve().parents[2] / "frontend" / "src"
    if not frontend.is_dir():
        pytest.skip("frontend sources not present next to backend/")
    hook = (frontend / "hooks" / "useAiTurn.ts").read_text(encoding="utf-8")
    declared = re.search(r"AI_MODELS: readonly AiModel\[\] = \[([^\]]*)\]", hook)
    assert declared, "AI_MODELS declaration not found in useAiTurn.ts"
    assert tuple(re.findall(r'"([^"]+)"', declared.group(1))) == get_args(AiModel)
    page = (frontend / "pages" / "AiAssistant.tsx").read_text(encoding="utf-8")
    assert tuple(re.findall(r'<SelectItem value="([^"]+)"', page)) == get_args(AiModel)


def test_model_env_overrides_keep_their_names(monkeypatch):
    """Deployments are renamed by env, not by code: the three names already in
    use stay valid, and the two new models have their own."""
    overrides = {
        "AZURE_OPENAI_CHAT_MODEL": "a", "AI_AGENT_MODEL_DEEP": "b", "AI_AGENT_MODEL_FRONTIER": "c",
        "AI_AGENT_MODEL_GROK": "d", "AI_AGENT_MODEL_DEEPSEEK": "e",
    }
    assert [env_name for env_name, _ in harness.SELECTABLE_MODELS] == list(overrides)
    for env_name, value in overrides.items():
        monkeypatch.setenv(env_name, value)
    assert harness.allowed_models() == ("a", "b", "c", "d", "e")
    assert harness.default_model() == "a"
    # two entries pointed at one deployment collapse instead of duplicating
    monkeypatch.setenv("AI_AGENT_MODEL_GROK", "a")
    assert harness.allowed_models() == ("a", "b", "c", "e")


def test_every_selectable_model_is_priced_above_zero(monkeypatch):
    """A selectable model without a price row bills at $0, i.e. it does not
    count against the daily cost quota. Checked through the real pricing
    function and the real settings, not just the table's keys."""
    from typing import get_args

    from app.core.config import get_settings
    from app.schemas.ai import AiModel
    from app.services.ai_gateway_service import compute_cost_usd

    monkeypatch.delenv("AI_MODEL_PRICES", raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    for model in get_args(AiModel):
        assert compute_cost_usd(settings, model, 1000, 0) > 0, model
        assert compute_cost_usd(settings, model, 0, 1000) > 0, model
        # cached input is never free and never dearer than fresh input
        assert 0 < compute_cost_usd(settings, model, 1000, 0, 1000) < compute_cost_usd(settings, model, 1000, 0), model


def test_cached_input_uses_the_models_own_price_when_it_has_one(monkeypatch):
    """1 MTok in, all of it cache-read: the row's third value when present
    (DeepSeek 0.145, not 10% of 1.74), else 10% of the input price."""
    from app.core.config import get_settings
    from app.services.ai_gateway_service import compute_cost_usd

    monkeypatch.delenv("AI_MODEL_PRICES", raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    assert compute_cost_usd(settings, "DeepSeek-V4-Pro", 1_000_000, 0, 1_000_000) == pytest.approx(0.145)
    assert compute_cost_usd(settings, "grok-4.7", 1_000_000, 0, 1_000_000) == pytest.approx(0.5)
    assert compute_cost_usd(settings, "gpt-5.6-terra", 1_000_000, 0, 1_000_000) == pytest.approx(0.2)
    # 600k fresh + 400k cached + 200k out on DeepSeek
    assert compute_cost_usd(settings, "DeepSeek-V4-Pro", 1_000_000, 200_000, 400_000) == pytest.approx(
        0.6 * 1.74 + 0.4 * 0.145 + 0.2 * 3.48
    )


def test_model_prices_env_accepts_two_or_three_values(monkeypatch):
    from app.core.config import _DEFAULT_MODEL_PRICES, _parse_model_prices

    assert _parse_model_prices('{"m": [1, 2], "n": [1, 2, 0.5]}') == {"m": (1.0, 2.0), "n": (1.0, 2.0, 0.5)}
    # a malformed row degrades to the defaults instead of raising
    assert _parse_model_prices('{"m": [1]}') == _DEFAULT_MODEL_PRICES
    assert _parse_model_prices('{"m": [1, 2, 3, 4]}') == _DEFAULT_MODEL_PRICES


# ── tool schemas (OPT-0075) ──────────────────────────────────────────────────

FULL_CTX = CallerCtx(user_id=7, email="mgr@kohleservices.com", role="manager", allowed_modules=("ai", "risk"), scope=None, trace_id="t-2")
RESTRICTED_CTX = CallerCtx(user_id=8, email="cs@kohleservices.com", role="user", allowed_modules=("ai",), scope=frozenset({1}), trace_id="t-3")


async def _noop_emit(event, data):
    return None


def _schema_keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _schema_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _schema_keys(item)


@pytest.mark.parametrize("ctx", [FULL_CTX, CTX, RESTRICTED_CTX], ids=["ai+risk", "ai", "restricted"])
def test_no_tool_schema_carries_ref_or_defs(ctx):
    """grok-4.7 rejects the WHOLE request with a bare 400 when any tool schema
    contains `$ref` / `$defs` (probed 2026-10-07). pydantic emits both for the
    SubjectArg TypedDict, so build_tools must hand out inlined schemas."""
    tools = harness.build_tools(ctx, _noop_emit)
    assert tools
    for t in tools:
        keys = set(_schema_keys(t.parameters()))
        assert not keys & {"$ref", "$defs"}, t.name
        # and what the framework serialises from it
        assert "$ref" not in str(t.to_json_schema_spec()) and "$defs" not in str(t.to_json_schema_spec()), t.name


def test_the_full_caller_gets_every_tool_and_inlining_keeps_the_subject_shape():
    tools = {t.name: t for t in harness.build_tools(FULL_CTX, _noop_emit)}
    assert set(tools) == {
        "get_client_overview", "get_trade_activity", "get_risk_signals", "rank_accounts",
        "get_economic_calendar", "rank_open_positions", "run_sql",
        "get_risk_alerts", "get_alert_orders", "get_window_scan",
    }
    subject = tools["get_trade_activity"].parameters()["properties"]["subject"]
    assert subject["type"] == "object"
    assert subject["required"] == ["kind", "value"]
    assert subject["properties"]["kind"]["enum"] == ["client_id", "login_sid"]
    assert "exact id only" in subject["description"]  # the key next to the $ref survives
    items = tools["get_client_overview"].parameters()["properties"]["subjects"]["items"]
    assert items["properties"]["kind"]["enum"] == ["client_id", "login_sid"]


@pytest.mark.anyio
async def test_an_inlined_tool_still_validates_and_runs(monkeypatch):
    """The rewrite touches the advertised schema only: a well-formed call
    reaches the impl, a subject with a bad `kind` is still refused."""
    seen = {}

    async def impl(ctx, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "source": {"certified": True}, "data": {}}

    monkeypatch.setitem(harness.TOOL_IMPLS, "get_risk_signals", impl)
    tool = {t.name: t for t in harness.build_tools(FULL_CTX, _noop_emit)}["get_risk_signals"]
    args = {"subject": {"kind": "client_id", "value": "1"}, "date_range": {"from": "2026-01-01", "to": "2026-01-02"}}
    await tool.invoke(arguments=args)
    assert seen["subject"] == {"kind": "client_id", "value": "1"}
    with pytest.raises(Exception):
        await tool.invoke(arguments={**args, "subject": {"kind": "email", "value": "1"}})


def test_skill_tools_carry_no_ref_either():
    provider = harness.build_skills_provider(FULL_CTX)
    for t in provider._create_tools([]):
        assert not set(_schema_keys(t.parameters())) & {"$ref", "$defs"}, t.name


def test_inline_schema_refs_unit():
    schema = {
        "$defs": {"A": {"type": "object", "properties": {"b": {"$ref": "#/$defs/B"}}}, "B": {"type": "string", "title": "B"}},
        "properties": {"x": {"$ref": "#/$defs/A", "description": "kept"}, "y": {"items": {"$ref": "#/$defs/B"}, "type": "array"}},
        "type": "object",
    }
    out = harness.inline_schema_refs(schema)
    assert out == {
        "properties": {
            "x": {"type": "object", "properties": {"b": {"type": "string", "title": "B"}}, "description": "kept"},
            "y": {"items": {"type": "string", "title": "B"}, "type": "array"},
        },
        "type": "object",
    }
    assert "$defs" in schema  # the input is not mutated
    assert harness.inline_schema_refs({"type": "object"}) == {"type": "object"}
    with pytest.raises(ValueError):
        harness.inline_schema_refs({"properties": {"x": {"$ref": "#/$defs/Missing"}}})
    with pytest.raises(ValueError):
        harness.inline_schema_refs({"$defs": {"A": {"properties": {"a": {"$ref": "#/$defs/A"}}}}, "properties": {"x": {"$ref": "#/$defs/A"}}})
