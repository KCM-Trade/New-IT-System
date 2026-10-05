"""Request/response schemas for the AI analyst agent (OPT-0064).

Contract: docs/ai-agent/02-contracts.md §4.1 and §6.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from typing import Any

# The deployments the UI may pick between. Values are Azure OpenAI
# DEPLOYMENT names on kcm-ai-agent-east-us, not model families: `terra` is the
# default analyst, `sol` is the "deep analysis" toggle (01 C13), `gpt-6.1-sol`
# is the newest-generation option (added 2026-10-05). `luna` exists for
# background jobs and is deliberately not selectable from the page.
# Keep in sync with ai_agent.harness.allowed_models() and the frontend AI_MODELS.
AiModel = Literal["gpt-5.6-terra", "gpt-5.6-sol", "gpt-6.1-sol"]

DEFAULT_MODEL: AiModel = "gpt-5.6-terra"

# Hard cap on a single question. Long enough for a paragraph of context,
# short enough that a pasted spreadsheet does not become an input-token bill.
MESSAGE_MAX_CHARS = 4000


class TurnRequest(BaseModel):
    session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description=(
            "Conversation id. An existing id owned by the caller resumes that "
            "conversation (OPT-0065 §8.4); an unknown id starts one under that "
            "id; null mints a new id."
        ),
    )
    message: str = Field(min_length=1, max_length=MESSAGE_MAX_CHARS)
    model: AiModel = DEFAULT_MODEL


class UsageToday(BaseModel):
    day_hk: str
    turns: int
    turns_limit: int
    cost_usd: float
    cost_limit_usd: float
    input_tokens: int
    output_tokens: int


# ── sessions (OPT-0065 §8.4) ─────────────────────────────────────────────────

SESSION_TITLE_MAX_CHARS = 120


class SessionSummary(BaseModel):
    session_id: str
    title: str | None
    model: str | None
    turns: int
    created_at: str
    updated_at: str


class SessionList(BaseModel):
    data: list[SessionSummary]
    total: int


class SessionMessage(BaseModel):
    seq: int
    role: str
    text: str
    # Parsed JSON columns: a list of {name, ok, certified, source, error_code,
    # input} tool summaries, and the turn's token/cost figures. Null when the
    # row has none (every user row; an assistant row that called no tool).
    tools: list[dict[str, Any]] | None = None
    usage: dict[str, Any] | None = None
    error_code: str | None = None
    at: str


class SessionDetail(BaseModel):
    session: SessionSummary
    messages: list[SessionMessage]


class SessionRename(BaseModel):
    title: str = Field(min_length=1, max_length=SESSION_TITLE_MAX_CHARS)


class OkResponse(BaseModel):
    ok: bool = True
