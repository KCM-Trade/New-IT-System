"""Microsoft Agent Framework wiring — the ONLY module that imports it.

One ``OpenAIChatClient`` per deployment name, created lazily and reused
across requests (the client is concurrency-safe). One ``Agent`` per turn,
built with tools that are CLOSURES over the caller's identity
(docs/ai-agent/02-contracts.md §2.1): the model never sees a "who am I"
parameter and no tool reads process-level identity.

Memory (02 §8, slice 2): the container stays stateless. The main API hands in
the previous ``AgentSession.to_dict()`` blob, this module rehydrates it,
runs the turn, and hands the new blob back as a ``session_state`` event
before ``done``. Nothing is written to disk here. Two framework facts decide
the wiring and are pinned by tests:

  * ``OpenAIChatClient`` speaks the Responses API and ``STORES_BY_DEFAULT``
    is True — without ``store: False`` the transcript would live on the
    Azure side, the session would hold only a ``resp_*`` id, and compaction
    would never see the messages. ``store`` is an Agent-level option (the
    client constructor has no ``default_options``).
  * The agent-level ``compaction_strategy`` only shrinks what is SENT to the
    model; the persisted history in ``session.state`` is compacted by a
    ``CompactionProvider.after_strategy``. The blob would otherwise grow by
    every tool result forever, so the provider path is the one used here.

Streaming shape: the framework yields ``AgentResponseUpdate`` objects whose
``contents`` are typed ``Content`` items. Text deltas and usage come from
that stream; ``tool_use`` / ``tool_done`` are emitted by the tool wrappers
themselves (through a per-turn queue) rather than parsed out of streamed
``function_call`` fragments, which arrive split across updates and would
make the UI's "querying…" indicator flicker or misfire.

Azure OpenAI is reached through the v1 surface: ``base_url = endpoint +
"/openai/v1"`` with an ``api_key`` — no ``api_version``, no
``azure_endpoint`` (04 §0.1.2). ``OPENAI_API_KEY`` must NOT be set in this
process: the framework would silently keep the client on api.openai.com.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Annotated, Any, AsyncIterator, Awaitable, Callable, Literal, Optional

# typing_extensions, not typing: pydantic (which MAF uses to build the tool
# schema) refuses typing.TypedDict on Python < 3.12, and the container image is
# python:3.11-slim while the host venv is 3.12 — the tests would not catch it.
from typing_extensions import TypedDict

from agent_framework import (
    Agent,
    AgentSession,
    CompactionProvider,
    InMemoryHistoryProvider,
    SlidingWindowStrategy,
    SummarizationStrategy,
    TokenBudgetComposedStrategy,
    ToolResultCompactionStrategy,
    tool,
)
from agent_framework.openai import OpenAIChatClient

from app.core.logging_config import get_logger

from .prompt import TOOL_DOCSTRINGS, system_prompt
from .tools import TOOL_IMPLS, CallerCtx
from .tools.run_sql import DATABASES as RUN_SQL_DATABASES, MAX_LIMIT as RUN_SQL_MAX_LIMIT
from .tools.run_sql import run_sql as run_sql_impl

logger = get_logger(__name__)

MAX_MODEL_ITERATIONS = 8
TURN_WALL_CLOCK_SECONDS = 280.0

if "OPENAI_API_KEY" in os.environ:
    # Not an assertion: the container must still come up so the operator can
    # read this line. Every turn will then fail loudly on the wrong endpoint.
    logger.critical(
        "OPENAI_API_KEY is set in the ai-agent environment. The agent framework routes to "
        "api.openai.com when it is present; remove it (docs/ai-agent/04-api-access.md §0.1.3)."
    )


def default_model() -> str:
    return os.environ.get("AZURE_OPENAI_CHAT_MODEL", "gpt-5.6-terra")


def deep_model() -> str:
    return os.environ.get("AI_AGENT_MODEL_DEEP", "gpt-5.6-sol")


def summary_model() -> str:
    """Deployment used for compaction summaries (02 §8.5: ``gpt-5.6-luna``).

    Same tenant as the analyst models on purpose: the summary INPUT is the
    conversation, i.e. client data, so it must not leave the Azure boundary.
    ``AI_AGENT_MODEL_SMALL`` is the name the compose files already carry for
    the same deployment; ``AI_AGENT_MODEL_SUMMARY`` wins when both are set.
    """
    return (
        os.environ.get("AI_AGENT_MODEL_SUMMARY")
        or os.environ.get("AI_AGENT_MODEL_SMALL")
        or "gpt-5.6-luna"
    )


def allowed_models() -> tuple[str, ...]:
    return tuple(dict.fromkeys((default_model(), deep_model())))


_clients: dict[str, OpenAIChatClient] = {}


def get_client(model: str) -> OpenAIChatClient:
    """Process-level client per deployment. Reads the Azure env lazily so an
    import in a test process without credentials does not fail."""
    client = _clients.get(model)
    if client is None:
        endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
        client = OpenAIChatClient(
            model=model,
            base_url=f"{endpoint}/openai/v1",
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            function_invocation_configuration={
                "max_iterations": MAX_MODEL_ITERATIONS,
                "max_duration_seconds": TURN_WALL_CLOCK_SECONDS - 20,
                # Exception text never goes back to the model: tools return
                # envelopes, and anything that still raises is a bug whose
                # message may carry connection details.
                "include_detailed_errors": False,
            },
        )
        _clients[model] = client
    return client


# ── argument schemas (what the model sees) ───────────────────────────────────


class SubjectArg(TypedDict):
    kind: Literal["client_id", "login_sid"]
    value: str


# `from` is a Python keyword, so the schema is declared as a plain dict with
# a description; the tools validate the keys themselves (common.parse_date_range).
DateRange = Annotated[dict, "{'from': 'YYYY-MM-DD', 'to': 'YYYY-MM-DD'} — closed interval of MT server days, max 366"]
Subject = Annotated[SubjectArg, "{'kind': 'client_id'|'login_sid', 'value': str} — exact id only"]

Emit = Callable[[str, dict], Awaitable[None]]


def build_tools(ctx: CallerCtx, emit: Emit) -> list:
    """Per-request tool closures. ``emit`` publishes tool_use / tool_done."""

    async def _run(name: str, impl, **kwargs: Any) -> dict:
        await emit("tool_use", {"name": name, "input": kwargs})
        envelope = await impl(ctx, **kwargs)
        done: dict[str, Any] = {
            "name": name,
            "ok": bool(envelope.get("ok")),
            "source": envelope.get("source") if envelope.get("ok") else None,
            "certified": bool((envelope.get("source") or {}).get("certified", False)),
        }
        if not envelope.get("ok"):
            done["error_code"] = (envelope.get("error") or {}).get("code", "internal")
        await emit("tool_done", done)
        return envelope

    @tool(name="get_client_overview", description=TOOL_DOCSTRINGS["get_client_overview"])
    async def get_client_overview(subject: Subject, date_range: DateRange) -> dict:
        return await _run("get_client_overview", TOOL_IMPLS["get_client_overview"], subject=dict(subject), date_range=dict(date_range))

    @tool(name="get_trade_activity", description=TOOL_DOCSTRINGS["get_trade_activity"])
    async def get_trade_activity(
        subject: Subject,
        date_range: DateRange,
        group_by: Annotated[Literal["symbol", "day", "hold_bucket"], "row grouping"] = "symbol",
    ) -> dict:
        return await _run(
            "get_trade_activity", TOOL_IMPLS["get_trade_activity"], subject=dict(subject), date_range=dict(date_range), group_by=group_by
        )

    @tool(name="get_risk_signals", description=TOOL_DOCSTRINGS["get_risk_signals"])
    async def get_risk_signals(subject: Subject, date_range: DateRange) -> dict:
        return await _run("get_risk_signals", TOOL_IMPLS["get_risk_signals"], subject=dict(subject), date_range=dict(date_range))

    tools = [get_client_overview, get_trade_activity, get_risk_signals]

    # run_sql (02 §10, gate ⑥): free SQL cannot be filtered by country, so for a
    # caller with a restricted scope the tool is not "refused" — it does not
    # exist in the model's context at all (05 §6.4). The impl repeats the check
    # so a future edit here cannot open it by accident.
    if ctx.scope is None:

        @tool(name="run_sql", description=TOOL_DOCSTRINGS["run_sql"])
        async def run_sql(
            db: Annotated[Literal["fxbackoffice", "risk_cases"], "which database: fxbackoffice (MySQL replica) or risk_cases (PostgreSQL)"],
            sql: Annotated[str, "ONE read-only SELECT (or UNION of SELECTs). Whitelisted tables only. Shown to the user verbatim."],
            limit: Annotated[int, f"max rows to return, 1..{RUN_SQL_MAX_LIMIT} (clamped)"] = RUN_SQL_MAX_LIMIT,
        ) -> dict:
            return await _run("run_sql", run_sql_impl, db=db, sql=sql, limit=limit)

        assert db_choices_match_impl(), "run_sql db choices drifted from tools.run_sql.DATABASES"
        tools.append(run_sql)

    return tools


def db_choices_match_impl() -> bool:
    """The Literal on the framework tool and the impl's DATABASES must agree,
    or the model could name a database the guard refuses (or vice versa)."""
    return set(RUN_SQL_DATABASES) == {"fxbackoffice", "risk_cases"}



# ── session memory + compaction (02 §8.5) ────────────────────────────────────

# The history provider's source id; CompactionProvider.after_strategy finds the
# persisted messages under session.state[HISTORY_SOURCE_ID]["messages"].
HISTORY_SOURCE_ID = "in_memory"

# Compaction knobs — the "起手式" from 02 §8.5, kept as constants so the live
# numbers written into 05 §6 are traceable to one place.
SESSION_TOKEN_BUDGET = 32_000
# Chars per token for the budget estimate. The framework's
# CharacterEstimatorTokenizer assumes 4 (English prose); this history is JSON
# tool results with numbers and CJK text and measured ~3.2 bytes/token against
# the billed input (2026-09-27: 151 KB of stored history billed as ~47k tokens
# while the 4-char estimate said 17k — under budget, so compaction never ran).
# 3 errs on the side of compacting slightly early rather than never.
ESTIMATOR_CHARS_PER_TOKEN = 3
KEEP_LAST_TOOL_CALL_GROUPS = 2      # a 20k tool result is the main growth source: fold it first
SUMMARY_TARGET_GROUPS = 8
SUMMARY_THRESHOLD_GROUPS = 4
SLIDING_WINDOW_GROUPS = 30

# Options every model call made on behalf of a session must carry. `store` is
# the one that matters: the Responses API keeps transcripts server-side by
# default (STORES_BY_DEFAULT is True on this client), which is exactly the
# "history lives in Azure, 30-day TTL, compaction blind" failure 02 §8.5 rules
# out. It is applied at the Agent level AND injected into the summariser's
# calls, because the summariser talks to its client directly.
SESSION_CHAT_OPTIONS: dict[str, Any] = {"store": False}


class SessionTokenizer:
    """``TokenizerProtocol`` with a chars-per-token ratio calibrated for this
    history (see ESTIMATOR_CHARS_PER_TOKEN). Same shape as the framework's
    CharacterEstimatorTokenizer, different constant."""

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // ESTIMATOR_CHARS_PER_TOKEN)


class _StoreOffClient:
    """Duck-typed ``SupportsChatGetResponse`` that forces ``store: False``.

    ``SummarizationStrategy`` calls ``client.get_response(messages,
    stream=False)`` with no options, so the summary request — whose input is
    the conversation, i.e. client data — would be stored on the Azure side
    under the service default. This wrapper is the only way to reach that call
    without forking the strategy.
    """

    def __init__(self, inner: OpenAIChatClient) -> None:
        self._inner = inner

    async def get_response(self, messages: Any, *, stream: bool = False, options: Any = None, **kwargs: Any):
        merged: dict[str, Any] = dict(options or {})
        merged.update(SESSION_CHAT_OPTIONS)
        return await self._inner.get_response(messages, stream=stream, options=merged, **kwargs)

    def __getattr__(self, name: str) -> Any:  # anything else the framework may probe
        return getattr(self._inner, name)


def build_compaction_strategy() -> TokenBudgetComposedStrategy:
    tokenizer = SessionTokenizer()
    return TokenBudgetComposedStrategy(
        token_budget=SESSION_TOKEN_BUDGET,
        tokenizer=tokenizer,
        strategies=[
            ToolResultCompactionStrategy(keep_last_tool_call_groups=KEEP_LAST_TOOL_CALL_GROUPS),
            SummarizationStrategy(
                client=_StoreOffClient(get_client(summary_model())),
                target_count=SUMMARY_TARGET_GROUPS,
                threshold=SUMMARY_THRESHOLD_GROUPS,
                tokenizer=tokenizer,
            ),
            SlidingWindowStrategy(keep_last_groups=SLIDING_WINDOW_GROUPS),
        ],
    )


def build_context_providers() -> list:
    """History + compaction, per turn (providers are cheap; the state is in the session).

    The same composed strategy runs twice: ``before_strategy`` on the history
    loaded from the blob (a guard against a blob that was written before the
    budget changed) and ``after_strategy`` on the history about to be
    persisted (what actually keeps the blob from growing turn over turn).
    Both are no-ops while the history is under budget.
    """
    strategy = build_compaction_strategy()
    return [
        # skip_excluded: compaction marks old messages `_excluded` in the stored
        # state (they are kept so the annotations survive); without this flag
        # the provider loads them anyway and the model is billed for them.
        InMemoryHistoryProvider(HISTORY_SOURCE_ID, load_messages=True, skip_excluded=True),
        CompactionProvider(
            before_strategy=strategy,
            after_strategy=strategy,
            tokenizer=SessionTokenizer(),
            history_source_id=HISTORY_SOURCE_ID,
        ),
    ]


def restore_session(blob: Optional[dict], trace_id: str) -> tuple[AgentSession, bool]:
    """``AgentSession.from_dict(blob)`` or a fresh session.

    Returns ``(session, rehydrated)``. A blob that no longer deserialises
    (framework upgrade, hand-edited row) must not kill the turn: the user gets
    a fresh session and the main API is told via ``rehydrated: false`` so the
    row can be marked read-only (05 §6.4).
    """
    if not blob:
        return AgentSession(), False
    try:
        return AgentSession.from_dict(blob), True
    except Exception as exc:  # noqa: BLE001 — any decode failure is "start fresh"
        logger.warning(
            "AI agent session blob could not be restored trace=%s (%s); starting a fresh session",
            trace_id,
            type(exc).__name__,
        )
        return AgentSession(), False


def _message_role(message: Any) -> str:
    if isinstance(message, dict):
        role = message.get("role")
    else:
        role = getattr(message, "role", None)
    role = getattr(role, "value", role)
    return str(role or "")


def session_turns(session: AgentSession) -> int:
    """Number of user messages in the persisted history = completed turns.

    Counted from the live state, not the serialised blob, so it is the same
    number whether or not compaction has replaced early messages with a
    summary (a summary is not a user message, so this can go DOWN — the main
    API keeps MAX(existing, turns), 02 §8.3).
    """
    history = session.state.get(HISTORY_SOURCE_ID) if isinstance(session.state, dict) else None
    messages = history.get("messages") if isinstance(history, dict) else None
    if not isinstance(messages, list):
        return 0
    return sum(1 for m in messages if _message_role(m) == "user")


def session_state_event(session: AgentSession, *, rehydrated: bool) -> dict:
    """The ``session_state`` payload (02 §8.3): the blob the main API stores."""
    return {"blob": session.to_dict(), "turns": session_turns(session), "rehydrated": rehydrated}


# ── one turn ─────────────────────────────────────────────────────────────────

_END = object()


async def run_turn(
    ctx: CallerCtx,
    message: str,
    model: str,
    session_blob: Optional[dict] = None,
) -> AsyncIterator[tuple[str, dict]]:
    """Yield ``(event, data)`` pairs for one turn: text / tool_use / tool_done,
    then ``session_state`` (always — a failed turn must not lose the history
    it was given), ``usage``, and exactly one ``done`` (or ``error`` + ``done``).

    ``session_blob`` is the previous turn's ``AgentSession.to_dict()`` from the
    main API's store, or None for a new conversation (02 §8.3).
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event: str, data: dict) -> None:
        await queue.put((event, data))

    agent = Agent(
        client=get_client(model),
        name="risk-analyst",
        # Instructions travel as a per-call option, not as a stored message:
        # the "## Today" tail changes daily and must not accumulate in the blob.
        instructions=system_prompt(),
        tools=build_tools(ctx, emit),
        default_options=dict(SESSION_CHAT_OPTIONS),
        context_providers=build_context_providers(),
    )
    session, rehydrated = restore_session(session_blob, ctx.trace_id)
    started = time.monotonic()
    model_calls = 0
    usage_total = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
    failure: Optional[tuple[str, str]] = None

    async def drive() -> None:
        nonlocal model_calls, failure
        try:
            async for update in agent.run(message, session=session, stream=True):
                delta = ""
                for c in update.contents or ():
                    ctype = getattr(c, "type", None)
                    if ctype == "text" and getattr(c, "text", None):
                        delta += c.text
                    elif ctype == "usage":
                        u = getattr(c, "usage_details", None) or {}
                        model_calls += 1
                        usage_total["input_tokens"] += int(u.get("input_token_count") or 0)
                        usage_total["output_tokens"] += int(u.get("output_token_count") or 0)
                        usage_total["cache_read_input_tokens"] += int(u.get("cache_read_input_token_count") or 0)
                if delta:
                    await queue.put(("text", {"delta": delta}))
        except Exception as exc:  # noqa: BLE001 — surfaced as an error event
            status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
            logger.error("AI agent model error trace=%s model=%s: %s", ctx.trace_id, model, type(exc).__name__, exc_info=True)
            failure = ("model_error", f"The model service failed ({type(exc).__name__}{f', status {status}' if status else ''}).")
        finally:
            await queue.put(_END)

    task = asyncio.create_task(drive())
    try:
        while True:
            remaining = TURN_WALL_CLOCK_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                failure = ("internal", f"Turn exceeded {int(TURN_WALL_CLOCK_SECONDS)}s and was stopped.")
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                failure = ("internal", f"Turn exceeded {int(TURN_WALL_CLOCK_SECONDS)}s and was stopped.")
                break
            if item is _END:
                break
            yield item
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    # The blob goes back BEFORE usage/done so the main API always has it when
    # the terminal event arrives, on the error path too (02 §8.3).
    try:
        yield ("session_state", session_state_event(session, rehydrated=rehydrated))
    except Exception:  # noqa: BLE001 — a serialisation bug must not eat the turn
        logger.error("AI agent session could not be serialised trace=%s", ctx.trace_id, exc_info=True)

    yield ("usage", {**usage_total, "cost_usd": None})
    if failure is not None:
        code, msg = failure
        yield ("error", {"code": code, "message": msg, "trace_id": ctx.trace_id})
        yield ("done", {"terminal_reason": "error", "num_turns": model_calls})
    else:
        reason = "max_turns" if model_calls >= MAX_MODEL_ITERATIONS else "end_turn"
        yield ("done", {"terminal_reason": reason, "num_turns": model_calls})
