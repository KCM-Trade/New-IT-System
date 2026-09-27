"""Microsoft Agent Framework wiring — the ONLY module that imports it.

One ``OpenAIChatClient`` per deployment name, created lazily and reused
across requests (the client is concurrency-safe). One ``Agent`` +
``AgentSession`` per turn, built with tools that are CLOSURES over the
caller's identity (docs/ai-agent/02-contracts.md §2.1): the model never sees
a "who am I" parameter and no tool reads process-level identity.

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

from agent_framework import Agent, AgentSession, tool
from agent_framework.openai import OpenAIChatClient

from app.core.logging_config import get_logger

from .prompt import TOOL_DOCSTRINGS, system_prompt
from .tools import TOOL_IMPLS, CallerCtx

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

    return [get_client_overview, get_trade_activity, get_risk_signals]


# ── one turn ─────────────────────────────────────────────────────────────────

_END = object()


async def run_turn(ctx: CallerCtx, message: str, model: str) -> AsyncIterator[tuple[str, dict]]:
    """Yield ``(event, data)`` pairs for one turn: text / tool_use / tool_done /
    usage, then exactly one ``done`` (or ``error`` + ``done``)."""
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event: str, data: dict) -> None:
        await queue.put((event, data))

    agent = Agent(
        client=get_client(model),
        name="risk-analyst",
        instructions=system_prompt(),
        tools=build_tools(ctx, emit),
    )
    session = AgentSession()
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

    yield ("usage", {**usage_total, "cost_usd": None})
    if failure is not None:
        code, msg = failure
        yield ("error", {"code": code, "message": msg, "trace_id": ctx.trace_id})
        yield ("done", {"terminal_reason": "error", "num_turns": model_calls})
    else:
        reason = "max_turns" if model_calls >= MAX_MODEL_ITERATIONS else "end_turn"
        yield ("done", {"terminal_reason": reason, "num_turns": model_calls})
