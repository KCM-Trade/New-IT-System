"""Main-API side of the browser -> api -> ai-agent relay (OPT-0064).

The route in ``routes/ai.py`` owns the policy (module gate, quota, audit); this
module owns the plumbing: opening the internal SSE stream to the agent
container, parsing it back into ``(event, data)`` pairs, pricing the ``usage``
event, and re-serialising events for the browser.

Why the agent is reached over HTTP and not imported in-process: the harness
(Microsoft Agent Framework + the openai SDK) and the read-only DB account live
in a separate container that publishes no host port — docs/ai-agent/01
D1/C5/C6. The main API therefore never holds the Azure credentials and a
compromised agent process cannot reach ``users.db`` for writing.

Contract for the internal hop: docs/ai-agent/02-contracts.md §4.2 / §4.3.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from app.core.sse import SSE_KEEPALIVE_SECONDS, SSE_PING, sse as _sse

import httpx

from app.core.config import Settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# httpx timeouts for the internal hop. `read` is the gap BETWEEN events, not
# the whole turn: the agent streams tokens continuously while the model talks
# and goes quiet only while a tool query runs, which is bounded at 25s per call
# by the tool contract (02 §2.7). The whole turn is capped separately by the
# route (TURN_TOTAL_SECONDS) so a stuck stream cannot hold a worker forever.
AGENT_CONNECT_TIMEOUT_S = 5.0
AGENT_READ_TIMEOUT_S = 120.0
TURN_TOTAL_SECONDS = 300.0

# SSE keepalive while waiting on the agent — the shared figure in core/sse.py.
KEEPALIVE_SECONDS = SSE_KEEPALIVE_SECONDS

INTERNAL_TOKEN_HEADER = "X-Internal-Token"


class AgentUnavailable(Exception):
    """The agent container could not be reached or answered outside the contract.

    Raised by ``open_agent_stream`` and turned into ``event: error /
    agent_unavailable`` by the route. Carries a short, browser-safe message —
    never the exception text of the underlying network error, which can
    include the internal hostname.
    """


# Re-exported so the route keeps importing them from here; the single
# implementation is core/sse.py (shared with the agent container's server).
sse = _sse


def sse_keepalive() -> bytes:
    return SSE_PING


def compute_cost_usd(
    settings: Settings,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int = 0,
) -> float:
    """Price a turn from raw token counts.

    ``cache_read_input_tokens`` is a SUBSET of ``input_tokens`` (that is how
    both OpenAI and the agent framework report it), so the cached portion is
    subtracted from the full-price input before being billed at 10%. An
    unknown deployment prices at 0 — visibly, since the token counts next to it
    are not zero — rather than raising inside a stream.
    """
    prices = settings.AI_MODEL_PRICES.get(model)
    if prices is None:
        return 0.0
    price_in, price_out = prices
    cached = max(0, min(int(cache_read_input_tokens or 0), int(input_tokens or 0)))
    full_in = max(0, int(input_tokens or 0) - cached)
    usd = (
        full_in * price_in
        + cached * price_in * 0.10
        + max(0, int(output_tokens or 0)) * price_out
    ) / 1_000_000
    return round(usd, 6)


def _parse_sse_block(block: str) -> tuple[str, Any] | None:
    """Turn one blank-line-delimited SSE block into ``(event, data)``.

    Comment lines (``: ping``) and blocks without data are dropped. A block
    without an explicit ``event:`` is reported as ``message`` per the SSE
    spec, which the route treats as unknown and forwards untouched.
    """
    event = "message"
    data_lines: list[str] = []
    for line in block.split("\n"):
        if not line or line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return None
    raw = "\n".join(data_lines)
    try:
        return event, json.loads(raw)
    except json.JSONDecodeError:
        return event, {"raw": raw}


async def open_agent_stream(
    settings: Settings, payload: dict, *, token: str
) -> AsyncIterator[tuple[str, Any]]:
    """POST the turn to the agent and yield its SSE events as they arrive.

    This is the single seam the tests replace: everything above it is policy
    that must be exercised against a scripted stream, everything inside it is
    httpx. Raises ``AgentUnavailable`` for connect/timeout/non-200; the route
    converts that into the browser-facing error event.
    """
    url = f"{settings.AI_AGENT_URL}/v1/turn"
    timeout = httpx.Timeout(
        connect=AGENT_CONNECT_TIMEOUT_S,
        read=AGENT_READ_TIMEOUT_S,
        write=10.0,
        pool=5.0,
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                url,
                json=payload,
                headers={INTERNAL_TOKEN_HEADER: token, "Accept": "text/event-stream"},
            ) as resp:
                if resp.status_code != 200:
                    # 401 = token mismatch (compose injected two different
                    # values), 400 = model the agent does not know. Both are
                    # deployment faults, not user faults, hence one code.
                    logger.error(
                        "ai-agent answered HTTP %s to %s", resp.status_code, url
                    )
                    raise AgentUnavailable(
                        f"agent answered HTTP {resp.status_code}"
                    )
                buffer = ""
                async for chunk in resp.aiter_text():
                    buffer += chunk
                    while "\n\n" in buffer:
                        block, buffer = buffer.split("\n\n", 1)
                        parsed = _parse_sse_block(block.replace("\r\n", "\n"))
                        if parsed is not None:
                            yield parsed
                # A trailing block without the final blank line.
                if buffer.strip():
                    parsed = _parse_sse_block(buffer.replace("\r\n", "\n"))
                    if parsed is not None:
                        yield parsed
    except AgentUnavailable:
        raise
    except httpx.TimeoutException as exc:
        logger.error("ai-agent timed out: %s", type(exc).__name__)
        raise AgentUnavailable("agent timed out") from exc
    except httpx.HTTPError as exc:
        logger.error("ai-agent unreachable: %s", type(exc).__name__)
        raise AgentUnavailable("agent unreachable") from exc
