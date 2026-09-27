"""Internal HTTP surface of the ai-agent container (02 §4.2).

Reachable only on the compose network (no host port), and only by the main
API, which proves itself with ``X-Internal-Token``. The request carries the
caller's identity in full; this process never opens users.db.

Run: ``uvicorn app.ai_agent.server:app --host 0.0.0.0 --port 8010``
"""

from __future__ import annotations

import asyncio
import hmac
import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from app.core.sse import SSE_HEADERS, SSE_KEEPALIVE_SECONDS, SSE_PING, sse

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.core.logging_config import get_logger

from . import harness
from .tools import CallerCtx, ctx_from_request

logger = get_logger(__name__)

MIN_TOKEN_LEN = 32
KEEPALIVE_SECONDS = SSE_KEEPALIVE_SECONDS

def _token() -> Optional[str]:
    value = os.environ.get("AI_AGENT_INTERNAL_TOKEN") or ""
    return value if len(value) >= MIN_TOKEN_LEN else None


@asynccontextmanager
async def _lifespan(_: FastAPI):
    if _token() is None:
        logger.critical(
            "AI_AGENT_INTERNAL_TOKEN is missing or shorter than %d characters; every /v1/turn will answer 503.",
            MIN_TOKEN_LEN,
        )
    yield


app = FastAPI(title="kcm-ai-agent (internal)", docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)


class Caller(BaseModel):
    user_id: int
    email: str = ""
    role: str = ""
    allowed_modules: list[str] = Field(default_factory=list)


class TurnRequest(BaseModel):
    caller: Caller
    scope: Optional[list[int]] = None  # null = unrestricted; [] = restricted to nothing
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    model: str = Field(min_length=1, max_length=64)
    trace_id: str = ""


# One serialiser for every SSE hop (core/sse.py); the main API relays these
# frames verbatim, so both sides must agree on the encoding.
_sse = sse


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.post("/v1/turn")
async def turn(
    body: TurnRequest,
    request: Request,
    x_internal_token: Optional[str] = Header(default=None, alias="X-Internal-Token"),
):
    expected = _token()
    if expected is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "internal token not configured")
    if not x_internal_token or not hmac.compare_digest(x_internal_token, expected):
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "bad internal token"})
    if body.model not in harness.allowed_models():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"model must be one of {list(harness.allowed_models())}")

    ctx: CallerCtx = ctx_from_request(body.caller.model_dump(), body.scope, body.trace_id)

    async def gen() -> AsyncIterator[bytes]:
        started = time.monotonic()
        tools_called: list[str] = []
        usage: dict[str, Any] = {}
        reason = "error"
        queue: asyncio.Queue = asyncio.Queue()
        end = object()

        async def pump() -> None:
            try:
                async for event, data in harness.run_turn(ctx, body.message, body.model):
                    await queue.put((event, data))
            except Exception as exc:  # noqa: BLE001 — the stream must end cleanly
                logger.error("AI agent turn crashed trace=%s: %s", ctx.trace_id, type(exc).__name__, exc_info=True)
                await queue.put(("error", {"code": "internal", "message": "The agent failed unexpectedly.", "trace_id": ctx.trace_id}))
                await queue.put(("done", {"terminal_reason": "error", "num_turns": 0}))
            finally:
                await queue.put(end)

        task = asyncio.create_task(pump())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield SSE_PING
                    continue
                if item is end:
                    break
                event, data = item
                if event == "tool_use":
                    tools_called.append(str(data.get("name")))
                elif event == "usage":
                    usage = data
                elif event == "done":
                    reason = str(data.get("terminal_reason"))
                yield _sse(event, data)
        finally:
            if not task.done():
                task.cancel()
            logger.info(
                "AI agent turn: user=%s model=%s tools=%s in=%s out=%s reason=%s %.1fs trace=%s",
                ctx.user_id,
                body.model,
                ",".join(tools_called) or "-",
                usage.get("input_tokens"),
                usage.get("output_tokens"),
                reason,
                time.monotonic() - started,
                ctx.trace_id,
            )
            logger.debug("AI agent question trace=%s: %s", ctx.trace_id, body.message[:200])

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
