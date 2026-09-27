"""Request/response schemas for the AI analyst agent (OPT-0064).

Contract: docs/ai-agent/02-contracts.md §4.1 and §6.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# The two deployments the UI may pick between. Values are Azure OpenAI
# DEPLOYMENT names on kcm-ai-agent-east-us, not model families: `terra` is the
# default analyst, `sol` is the "deep analysis" toggle (01 C13). `luna` exists
# for background jobs and is deliberately not selectable from the page.
AiModel = Literal["gpt-5.6-terra", "gpt-5.6-sol"]

DEFAULT_MODEL: AiModel = "gpt-5.6-terra"

# Hard cap on a single question. Long enough for a paragraph of context,
# short enough that a pasted spreadsheet does not become an input-token bill.
MESSAGE_MAX_CHARS = 4000


class TurnRequest(BaseModel):
    session_id: str | None = Field(
        default=None,
        max_length=64,
        description="Opaque id grouping turns for audit; null starts a new one.",
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
