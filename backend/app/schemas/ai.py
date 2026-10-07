"""Request/response schemas for the AI analyst agent (OPT-0064).

Contract: docs/ai-agent/02-contracts.md §4.1 and §6.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_serializer
from typing import Any

# The deployments the UI may pick between. Values are Azure OpenAI
# DEPLOYMENT names on kcm-ai-agent-east-us, not model families: `terra` is the
# default analyst, `sol` is the "deep analysis" toggle (01 C13), `gpt-6.1-sol`
# is the newest-generation option (added 2026-10-05), `grok-4.7` (xAI) and
# `DeepSeek-V4-Pro` are the non-OpenAI options (added 2026-10-07, OPT-0075).
# `luna` exists for background jobs and is deliberately not selectable from
# the page.
# Keep in sync with ai_agent.harness.allowed_models() and the frontend AI_MODELS.
AiModel = Literal["gpt-5.6-terra", "gpt-5.6-sol", "gpt-6.1-sol", "grok-4.7", "DeepSeek-V4-Pro"]

DEFAULT_MODEL: AiModel = "gpt-5.6-terra"

# Compare mode (OPT-0076): how many models one question may be sent to.
COMPARE_MIN_MODELS = 2
COMPARE_MAX_MODELS = 3

# Why the user picked the answer they picked (optional, 02 §22).
CompareReason = Literal["numbers", "clearer", "faster", "other"]

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
    # Compare mode (OPT-0076, 02 §19): 2–3 distinct deployments answer the same
    # question side by side and the user picks one. Absent / null = the
    # ordinary single-model turn, untouched. When present, `model` is ignored.
    # List order is the column order on the page.
    compare_models: list[AiModel] | None = Field(
        default=None, min_length=COMPARE_MIN_MODELS, max_length=COMPARE_MAX_MODELS
    )

    @field_validator("compare_models")
    @classmethod
    def _compare_models_are_distinct(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("compare_models must not repeat a model")
        return value


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


def _drop_unset(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Leave the compare-mode additions (OPT-0076) out of a payload when they
    carry nothing: ``None`` or ``False``.

    The pre-compare response shapes are pinned key-for-key by
    tests/test_ai_sessions.py, and "a request without compare_models behaves
    exactly as before" is this feature's acceptance test. So a session that has
    never seen a compare turn serialises exactly as it always did; the new keys
    appear only when they say something. Readers treat a missing key as
    null / false.
    """
    for key in keys:
        if data.get(key) is None or data.get(key) is False:
            data.pop(key, None)
    return data


class SessionSummary(BaseModel):
    session_id: str
    title: str | None
    model: str | None
    turns: int
    created_at: str
    updated_at: str
    # True while a compare turn on this session waits for the user's choice
    # (02 §23). Omitted when false.
    pending_compare: bool = False

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> dict[str, Any]:
        return _drop_unset(handler(self), "pending_compare")


class SessionList(BaseModel):
    data: list[SessionSummary]
    total: int


class CompareAnswer(BaseModel):
    """One model's answer inside a compare turn — transcript fields only."""

    model: str
    text: str
    tools: list[dict[str, Any]] | None = None
    usage: dict[str, Any] | None = None
    error_code: str | None = None
    elapsed_ms: int | None = None


class CompareCandidate(CompareAnswer):
    # Has a stored context, no error and a non-empty answer (02 §20).
    selectable: bool


class CompareOutcome(BaseModel):
    compare_id: str
    reason: str | None = None
    alternatives: list[CompareAnswer]


class PendingCompare(BaseModel):
    compare_id: str
    state: Literal["running", "pending"]
    question: str
    models: list[str]
    created_at: str
    # Empty while `running`: candidates are stored when the whole turn ends.
    candidates: list[CompareCandidate]


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
    # Which model gave this answer (assistant rows written since OPT-0076;
    # omitted on user rows and on older rows).
    model: str | None = None
    # Present on an assistant row that came out of a compare turn: the answers
    # that were NOT chosen (or, for a turn where every model failed, all of
    # them). Never carries a blob.
    compare: CompareOutcome | None = None

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> dict[str, Any]:
        return _drop_unset(handler(self), "model", "compare")


class SessionDetail(BaseModel):
    session: SessionSummary
    messages: list[SessionMessage]
    # The compare turn still running or waiting for a choice, if any (02 §23).
    # Omitted when there is none.
    pending_compare: PendingCompare | None = None

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> dict[str, Any]:
        return _drop_unset(handler(self), "pending_compare")


class SelectRequest(BaseModel):
    """Body of POST /ai/sessions/{id}/select (02 §22)."""

    compare_id: str = Field(min_length=1, max_length=64)
    # Not `AiModel`: the store decides whether this is a selectable candidate
    # of THIS compare turn (422 otherwise), and a deployment retired from the
    # picker must stay selectable in a turn that already ran it.
    model: str = Field(min_length=1, max_length=64)
    reason: CompareReason | None = None


class SessionRename(BaseModel):
    title: str = Field(min_length=1, max_length=SESSION_TITLE_MAX_CHARS)


class OkResponse(BaseModel):
    ok: bool = True
