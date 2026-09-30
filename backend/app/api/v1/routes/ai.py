"""AI analyst agent — browser-facing endpoints (OPT-0064, sessions OPT-0065).

    POST   /api/v1/ai/turn            one question -> Server-Sent Events
    GET    /api/v1/ai/usage/today     the caller's quota position for the status bar
    GET    /api/v1/ai/sessions        the caller's conversations, newest first
    GET    /api/v1/ai/sessions/{id}   one conversation's transcript (never the blob)
    DELETE /api/v1/ai/sessions/{id}   soft delete            (audit ai.session.delete)
    PATCH  /api/v1/ai/sessions/{id}   rename                 (audit ai.session.rename)

Contract: docs/ai-agent/02-contracts.md §4.1 (request/response), §4.3 (events),
§5 (audit), §6 (quota), §8.3–§8.4 (sessions). Decisions: docs/ai-agent/01.

Memory (OPT-0065 §8): the agent container is stateless. The model's context
for a conversation is the serialised framework session (`blob`) that THIS
process stores in ai_agent.db, hands to the agent in the request body
(`session_blob`) and writes back from the agent's `session_state` event —
which is consumed here and never forwarded to the browser. Ownership is
checked before the stream opens: a session that is not the caller's answers a
plain 404 (403 is the module gate's word; 404 also does not confirm the id
exists), and the four `/sessions*` endpoints match on the owner inside the SQL
so there is no code path that can forget the check.

Why these routes live on the main API and not on the agent container: the
module gate is mounted ONCE on ``api_v1_router`` (core/auth_deps.py), so a
route registered here is gated by ``ai`` automatically, audited by the same
``Auditor``, and quota'd against the same ``users.id`` — none of which the
agent container can see (it holds no session store). The agent gets the
caller's identity in the request body and trusts it because only this process
knows the shared token (01 C5).

Two things about this handler are unusual for this codebase and are on
purpose:

  * It is ``async def`` — the one exception to the "blocking IO routes must be
    ``def``" rule — because it AWAITS a long-lived HTTP stream. The SQLite
    quota/audit calls are sub-millisecond but are still pushed through
    ``anyio.to_thread.run_sync`` so nothing synchronous ever sits on the event
    loop while forty other requests share it.
  * The audit row is written at the END of the stream (turn end, per 02 §5),
    which is after the response headers have gone out. ``AuditMissing``
    would therefore see "a POST returned 200 and recorded nothing" and log a
    false AUDIT_MISSING on every turn, so the route marks the request
    ``audit_deferred`` before returning — see the comment on that middleware.
    The deferral is a PROMISE: the worker task's ``finally`` block must record
    exactly one row on every path — success, failure, or browser gone — and
    ``tests/test_ai_route.py`` / ``test_ai_sessions.py`` hold it to that.
    The turn runs in a worker task and the response generator only relays
    its frames, so a Stop / tab close does not cut the turn short: the worker
    keeps draining the agent stream (bounded) and still writes the session
    blob, the usage and the audit row.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
import uuid
from typing import Any, AsyncIterator, Optional

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.core import ai_usage_db
from app.core.audit import Auditor, get_auditor
from app.core.auth_middleware import client_ip
from app.core.config import get_settings
from app.core.data_scope import caller_cids
from app.core.logging_config import get_logger, trace_id_var
from app.schemas.ai import (
    OkResponse,
    SessionDetail,
    SessionList,
    SessionRename,
    TurnRequest,
    UsageToday,
)
from app.services import ai_gateway_service as gateway
from app.services.ai_gateway_service import (
    KEEPALIVE_SECONDS,
    TURN_TOTAL_SECONDS,
    AgentUnavailable,
    sse,
    sse_keepalive,
)
from app.services.auth_service import SessionUser, record_auth_event

logger = get_logger(__name__)

router = APIRouter(prefix="/ai")

AUDIT_ACTION = "ai.query.submit"
AUDIT_SESSION_DELETE = "ai.session.delete"
AUDIT_SESSION_RENAME = "ai.session.rename"

SESSION_NOT_FOUND = "session not found"

# How much of the question the audit row keeps. Enough to see what was asked,
# not so much that a pasted document lands in users.db (02 §5).
AUDIT_QUESTION_CHARS = 500

# run_sql (02 §10 ⑦): the audit row keeps each ad-hoc SQL verbatim up to this
# many characters — the guard already refuses anything over 4000.
RUN_SQL_TOOL = "run_sql"
AUDIT_SQL_CHARS = 2000

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # nginx must not buffer this response, or every event arrives at once when
    # the stream closes. frontend/nginx.conf also turns proxy_buffering off for
    # this exact path; the header is what protects the dev/vite path.
    "X-Accel-Buffering": "no",
}


def _quota_user_id(user: SessionUser | None) -> int:
    """The quota row's key. ``0`` is the anonymous bucket that exists only when
    the auth kill switch is off — everyone then shares one daily allowance,
    which is the conservative reading of "no identity to charge"."""
    return int(user.user_id) if user is not None else 0


# Group-level tools (OPT-0066) name who they look at through id LISTS instead
# of one `subject`; each id becomes its own audit label. Alert ids are kept as
# `alert:<id>` — the tool resolves them to clients server-side, and the id is
# what makes the drill-down reproducible from the audit row alone.
_SUBJECT_LIST_KEYS = (("client_ids", "client"), ("alert_ids", "alert"))
_MAX_LABELS_PER_CALL = 50
_CLIENT_LABEL_RE = re.compile(r"^client:\d{1,12}$")


def _subject_labels(tool_input: Any) -> list[str]:
    """What the model asked a tool to look at, as audit labels.

    ``{"subject": {"kind": "client_id", "value": "123"}}`` -> ``["client:123"]``;
    ``{"subjects": [{"kind": "client_id", "value": "1"}, ...]}`` -> ``["client:1", ...]``;
    ``{"client_ids": [1, 2]}`` -> ``["client:1", "client:2"]``;
    ``{"alert_ids": [9]}`` -> ``["alert:9"]``.

    Read from the agent's ``tool_use`` event, i.e. what the model actually
    asked the tool for — the audit wants "who was looked at", not the words in
    the question.
    """
    if not isinstance(tool_input, dict):
        return []
    labels: list[str] = []
    # `subjects` (a list of subject objects) is get_client_overview's batch
    # form since 2026-09-28 — without this branch a batch call would audit
    # as "looked at nobody".
    many = tool_input.get("subjects")
    singles = [tool_input.get("subject")] + (many[:_MAX_LABELS_PER_CALL] if isinstance(many, list) else [])
    for subject in singles:
        if not (isinstance(subject, dict) and subject.get("value") is not None):
            continue
        kind = subject.get("kind")
        value = subject.get("value")
        if kind == "client_id":
            labels.append(f"client:{value}")
        elif kind == "login_sid":
            labels.append(f"login:{value}")
        else:
            labels.append(f"{kind}:{value}")
    for key, prefix in _SUBJECT_LIST_KEYS:
        ids = tool_input.get(key)
        if isinstance(ids, list):
            labels.extend(f"{prefix}:{v}" for v in ids[:_MAX_LABELS_PER_CALL] if v is not None)
    return labels


def _resolve_tool_entry(entries: list[dict[str, Any]], done: dict) -> None:
    """Fold a ``tool_done`` event into the oldest still-pending entry of the
    same name (the agent finishes tools in the order it started them — the
    same rule the browser applies)."""
    name = str(done.get("name") or "")
    for entry in entries:
        if entry["name"] == name and entry["ok"] is None:
            entry["ok"] = bool(done.get("ok"))
            entry["certified"] = bool(done.get("certified", False))
            entry["source"] = done.get("source") if done.get("ok") else None
            entry["error_code"] = None if done.get("ok") else str(done.get("error_code") or "error")
            return
    if name:
        entries.append(
            {
                "name": name,
                "ok": bool(done.get("ok")),
                "certified": bool(done.get("certified", False)),
                "source": done.get("source") if done.get("ok") else None,
                "error_code": None if done.get("ok") else str(done.get("error_code") or "error"),
                "input": None,
            }
        )


SESSION_BUSY = "session busy"

# How long a turn keeps running AFTER the browser has gone (Stop pressed, tab
# closed, network drop). The agent has usually already spent the model call;
# what is still owed is the `session_state` write-back — without it the turn
# vanishes from the model's memory while the transcript keeps the partial
# answer (cold review #7). Capped so a wedged agent cannot pin a task forever;
# also capped by what is left of TURN_TOTAL_SECONDS.
DISCONNECT_DRAIN_SECONDS = 120.0

# Strong references to detached drain tasks: asyncio keeps only weak refs to
# running tasks, so a task nobody holds can be garbage-collected mid-flight.
_DRAIN_TASKS: set[asyncio.Task] = set()

_END = object()


@router.post("/turn")
async def turn(
    body: TurnRequest,
    request: Request,
    audit: Auditor = Depends(get_auditor),
) -> StreamingResponse:
    settings = get_settings()
    user: SessionUser | None = getattr(request.state, "user", None)
    scope = caller_cids(request)
    session_id = body.session_id or uuid.uuid4().hex
    trace_id = trace_id_var.get() or "-"
    owner_uid = _quota_user_id(user)

    # ── the conversation row: resume, refuse, or create (02 §8.4) ────────────
    # Decided BEFORE any SSE byte goes out so a refusal is a real HTTP 404 the
    # browser can act on, not an event inside a 200 stream.
    session_blob: dict | None = None
    resumed = False
    try:
        existing = (
            await anyio.to_thread.run_sync(ai_usage_db.get_session_for_turn, session_id)
            if body.session_id
            else None
        )
    except sqlite3.Error:
        # ai_agent.db unreadable/unwritable is a deployment fault (ownership,
        # disk), not a user fault: say so with a 503 before any model call is
        # spent, and log at ERROR every time — this makes the feature dead.
        logger.error("AI turn refused: ai_agent.db is not accessible (trace_id=%s)", trace_id, exc_info=True)
        raise HTTPException(status_code=503, detail="conversation store unavailable")
    if existing is not None:
        if existing["deleted"] or existing["user_id"] != owner_uid:
            # Somebody else's conversation (or a deleted one). Throttled
            # auth_events row like every other permission_denied — this is
            # the id-enumeration attempt the 404 is there to not confirm.
            await anyio.to_thread.run_sync(
                lambda: record_auth_event(
                    "permission_denied",
                    email=user.email if user else None,
                    detail="ai_session_owner",
                    ip=client_ip(request),
                    ua=request.headers.get("User-Agent"),
                )
            )
            raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND)
        session_blob = existing["blob"]
        resumed = session_blob is not None
    else:
        try:
            await anyio.to_thread.run_sync(
                lambda: ai_usage_db.create_session(
                    session_id, owner_uid, title=body.message.strip(), model=body.model
                )
            )
        except sqlite3.Error:
            logger.error("AI turn refused: ai_agent.db is not writable (trace_id=%s)", trace_id, exc_info=True)
            raise HTTPException(status_code=503, detail="conversation store unavailable")

    # ── one turn at a time per session ───────────────────────────────────────
    # Two tabs on the same conversation would both read the same blob and the
    # later write-back would silently discard the earlier turn. The claim is
    # a single atomic UPDATE; it is released in the worker's finally on every
    # path, and a claim older than TURN_CLAIM_STALE_SECONDS is taken over.
    try:
        claimed = await anyio.to_thread.run_sync(ai_usage_db.claim_turn, session_id, owner_uid)
    except sqlite3.Error:
        logger.error("AI turn refused: ai_agent.db is not writable (trace_id=%s)", trace_id, exc_info=True)
        raise HTTPException(status_code=503, detail="conversation store unavailable")
    if not claimed:
        raise HTTPException(status_code=409, detail=SESSION_BUSY)

    # See the module docstring: the audit row is written when the stream ends.
    request.state.audit_deferred = True

    # The turn runs in a WORKER TASK; the response generator only relays its
    # frames. Splitting the two is what lets the worker outlive the browser:
    # when the client disconnects the generator stops, sets `disconnected`,
    # and the worker keeps consuming the agent stream (bounded) so the
    # `session_state`, usage and audit still land.
    frames: asyncio.Queue = asyncio.Queue()
    disconnected = asyncio.Event()

    def emit(frame: bytes) -> None:
        if not disconnected.is_set():
            frames.put_nowait(frame)

    async def _run_turn() -> None:
        # ── per-turn accounting, all of it lands in the audit row ────────────
        tools_called: list[str] = []
        # Skills the model loaded this turn (OPT-0069), unique, in order.
        skills_loaded: list[str] = []
        subjects: list[str] = []
        scope_denied = 0
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        cost_usd = 0.0
        terminal_reason = "error"
        num_turns = 0
        error_code: str | None = None
        started = time.monotonic()
        quota_uid = owner_uid
        day = ai_usage_db.today_hk()
        # Transcript of this turn for ai_messages (02 §8.2): the answer text
        # and one summary per tool call. `input` is kept so a later run_sql
        # call can show its SQL in the history view.
        answer_parts: list[str] = []
        tool_entries: list[dict[str, Any]] = []
        # 02 §10.1 ⑦: every ad-hoc SQL the model ran, verbatim (capped). The
        # shared MySQL account cannot be attributed DB-side, so this list on the
        # audit row is the only "who ran what" record for run_sql.
        sql_texts: list[str] = []
        state_saved = False
        drain_deadline: float | None = None

        def _fail(code: str, message: str) -> None:
            nonlocal error_code, terminal_reason
            error_code = code
            terminal_reason = "error"
            emit(sse("error", {"code": code, "message": message, "trace_id": trace_id}))
            emit(sse("done", {"terminal_reason": "error", "num_turns": num_turns}))

        try:
            emit(sse("init", {"session_id": session_id, "model": body.model}))

            token = settings.AI_AGENT_INTERNAL_TOKEN
            if token is None:
                # Logged at ERROR every time on purpose: this is a deployment
                # fault (compose did not inject the token, or injected a
                # placeholder) and it makes the whole feature dead.
                logger.error(
                    "AI turn refused: AI_AGENT_INTERNAL_TOKEN is not configured "
                    "(missing or shorter than the minimum) — check compose "
                    "environment for both api and ai-agent"
                )
                _fail("agent_unavailable", "AI agent is not configured")
                return

            # ── quota, BEFORE the turn is forwarded (02 §6) ──────────────────
            usage = await anyio.to_thread.run_sync(ai_usage_db.get_usage, quota_uid, day)
            if (
                usage["turns"] >= settings.AI_DAILY_TURNS_LIMIT
                or usage["cost_usd"] >= settings.AI_DAILY_COST_LIMIT_USD
            ):
                _fail(
                    "quota_exceeded",
                    f"Daily quota reached ({usage['turns']}/{settings.AI_DAILY_TURNS_LIMIT} "
                    f"turns, ${usage['cost_usd']:.2f}/${settings.AI_DAILY_COST_LIMIT_USD:.2f}). "
                    "Resets at midnight Hong Kong time.",
                )
                return
            await anyio.to_thread.run_sync(ai_usage_db.increment_turn, quota_uid, day)

            payload = {
                "caller": {
                    "user_id": user.user_id if user else None,
                    "email": user.email if user else None,
                    "role": user.role if user else "anonymous",
                    "allowed_modules": list(user.allowed_modules) if user else ["*"],
                },
                # None = unrestricted; a list (possibly empty) = restricted.
                # The distinction is the whole point (02 §2.1), so it is
                # passed through as-is and never collapsed with `or`.
                "scope": None if scope is None else sorted(scope),
                "session_id": session_id,
                "message": body.message,
                "model": body.model,
                "trace_id": trace_id,
                # The stored framework session, or null for a fresh one
                # (02 §8.3). The agent restores it, runs the turn, and sends
                # the new state back as `session_state`.
                "session_blob": session_blob,
            }

            stream = gateway.open_agent_stream(settings, payload, token=token)
            iterator = stream.__aiter__()
            saw_done = False
            # ONE pending read, kept across idle waits. asyncio.wait_for() on
            # iterator.__anext__() CANCELS the read on timeout, and cancelling
            # an async generator mid-await finalises it: the httpx stream is
            # closed and the next __anext__() is StopAsyncIteration → "agent
            # stream ended unexpectedly". The agent's SSE pings are dropped by
            # the gateway, so any tool quieter than KEEPALIVE_SECONDS (15s:
            # rank_accounts over a month, cold starts) killed the turn
            # (2026-09-28, trace req-4e221b39). asyncio.wait() never cancels.
            pending: Optional[asyncio.Task] = None
            try:
                while True:
                    now = time.monotonic()
                    if disconnected.is_set() and error_code is None:
                        # The browser is gone; keep going for what the store
                        # is still owed, but not forever.
                        error_code = "client_disconnected"
                        drain_deadline = now + min(
                            DISCONNECT_DRAIN_SECONDS, max(0.0, TURN_TOTAL_SECONDS - (now - started))
                        )
                        logger.info(
                            "AI turn: client disconnected, draining the agent stream for up to %.0fs (trace_id=%s)",
                            drain_deadline - now,
                            trace_id,
                        )
                    if drain_deadline is not None and now > drain_deadline:
                        break
                    if now - started > TURN_TOTAL_SECONDS:
                        _fail("agent_unavailable", "turn exceeded the time limit")
                        break
                    if pending is None:
                        pending = asyncio.ensure_future(iterator.__anext__())
                    finished, _ = await asyncio.wait({pending}, timeout=KEEPALIVE_SECONDS)
                    if not finished:
                        # Keepalives to the browser are the relay's job; here
                        # the wait just loops so the deadlines above are checked.
                        # The read stays pending — never cancelled.
                        continue
                    read, pending = pending, None
                    try:
                        event, data = read.result()
                    except StopAsyncIteration:
                        break

                    if event == "text" and isinstance(data, dict):
                        delta = data.get("delta")
                        if isinstance(delta, str) and delta:
                            answer_parts.append(delta)
                    elif event == "tool_use" and isinstance(data, dict):
                        name = str(data.get("name") or "")
                        if name:
                            tools_called.append(name)
                            tool_entries.append(
                                {
                                    "name": name,
                                    "ok": None,
                                    "certified": False,
                                    "source": None,
                                    "error_code": None,
                                    "input": data.get("input"),
                                }
                            )
                        for label in _subject_labels(data.get("input")):
                            if label not in subjects:
                                subjects.append(label)
                        if name == RUN_SQL_TOOL:
                            sql_text = (data.get("input") or {}).get("sql") if isinstance(data.get("input"), dict) else None
                            if isinstance(sql_text, str) and sql_text.strip():
                                sql_texts.append(sql_text[:AUDIT_SQL_CHARS])
                    elif event == "skill_loaded" and isinstance(data, dict):
                        # Audit-only (OPT-0069): which skill bodies / reference
                        # files the model read. Not forwarded — the browser has
                        # no UI for it and the event is internal to the hop.
                        skill = data.get("skill")
                        if isinstance(skill, str) and skill and skill not in skills_loaded:
                            skills_loaded.append(skill[:64])
                        continue
                    elif event == "session_state" and isinstance(data, dict):
                        # Consumed here, never forwarded (02 §8.3): the blob
                        # is the framework's private format and carries raw
                        # tool results the browser has no business holding.
                        blob = data.get("blob")
                        if isinstance(blob, dict):
                            state_saved = await anyio.to_thread.run_sync(
                                lambda: ai_usage_db.save_session_state(
                                    session_id,
                                    owner_uid,
                                    blob=blob,
                                    turns=int(data.get("turns") or 0),
                                    model=body.model,
                                )
                            )
                        continue
                    elif event == "tool_done" and isinstance(data, dict):
                        _resolve_tool_entry(tool_entries, data)
                        # Subjects the agent resolved server-side (get_alert_orders:
                        # alert → client). Only well-formed `client:<int>` labels.
                        for label in (data.get("subjects") or [])[:_MAX_LABELS_PER_CALL]:
                            if isinstance(label, str) and _CLIENT_LABEL_RE.match(label) and label not in subjects:
                                subjects.append(label)
                        if data.get("ok") is False and data.get("error_code") == "scope_denied":
                            scope_denied += 1
                            # The agent container cannot write users.db (its
                            # data mount is read-only), so the auth_events row
                            # the contract asks for (02 §2.1) is written HERE,
                            # where the refusal surfaces as a tool_done event.
                            # record_auth_event throttles per person and never
                            # raises; it runs in a thread like every other
                            # SQLite write on this async path.
                            tool_name = str(data.get("name") or "-")
                            await anyio.to_thread.run_sync(
                                lambda: record_auth_event(
                                    "permission_denied",
                                    email=user.email if user else None,
                                    detail=f"ai_tool_scope:{tool_name}"[:200],
                                    ip=client_ip(request),
                                    ua=request.headers.get("User-Agent"),
                                )
                            )
                    elif event == "usage" and isinstance(data, dict):
                        # The agent reports raw counts; the price table lives
                        # here and only here (02 §6 "not trusting the browser"
                        # extends to not trusting a second price table).
                        i = int(data.get("input_tokens") or 0)
                        o = int(data.get("output_tokens") or 0)
                        c = int(data.get("cache_read_input_tokens") or 0)
                        usd = gateway.compute_cost_usd(settings, body.model, i, o, c)
                        data = {**data, "cost_usd": usd}
                        input_tokens += i
                        output_tokens += o
                        cache_read_tokens += c
                        cost_usd += usd
                        await anyio.to_thread.run_sync(
                            ai_usage_db.add_usage, quota_uid, day, i, o, usd
                        )
                    elif event == "done" and isinstance(data, dict):
                        saw_done = True
                        terminal_reason = str(data.get("terminal_reason") or "end_turn")
                        num_turns = int(data.get("num_turns") or 0)
                    elif event == "error" and isinstance(data, dict):
                        error_code = str(data.get("code") or "internal")
                        data = {**data, "trace_id": data.get("trace_id") or trace_id}

                    emit(sse(event, data))
                    if saw_done:
                        break
            finally:
                # A read still in flight (deadline / drain exit) must be
                # cancelled and awaited first: aclose() on a generator that is
                # mid-__anext__ raises "asynchronous generator is already running".
                if pending is not None:
                    pending.cancel()
                    try:
                        await pending
                    except (asyncio.CancelledError, StopAsyncIteration, Exception):  # noqa: BLE001
                        pass
                await stream.aclose()

            if not saw_done and error_code is None:
                # The agent closed the stream without a terminal event. That is
                # a contract violation on its side; the browser still needs a
                # `done` to stop spinning.
                _fail("internal", "agent stream ended unexpectedly")

        except AgentUnavailable as exc:
            _fail("agent_unavailable", str(exc))
        except Exception:  # noqa: BLE001 — the stream must end cleanly
            logger.exception("AI turn failed (trace_id=%s)", trace_id)
            _fail("internal", "internal error")
        finally:
            # The relay is released only AFTER the accounting below has been
            # written (inner finally), so when the browser sees the body close
            # the audit row and transcript rows already exist — the ordering
            # tests and the health check both rely on it.
            try:
                # One audit row per turn, on EVERY path (02 §5). Auditor.record
                # never raises; the actor comes from request.state.user only.
                # Written here — and only here — whether the browser is still
                # listening or the worker is draining after a disconnect.
                new_value: dict[str, Any] = {
                    "question": body.message[:AUDIT_QUESTION_CHARS],
                    "model": body.model,
                    "tools_called": tools_called,
                    "skills_loaded": skills_loaded,
                    "subjects": subjects,
                    "terminal_reason": terminal_reason,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": round(cost_usd, 6),
                    "scope_denied_count": scope_denied,
                    # True when the agent was handed an earlier turn's context.
                    "resumed": resumed,
                }
                if error_code is not None:
                    new_value["error_code"] = error_code
                if sql_texts:
                    new_value["sql"] = sql_texts
                await anyio.to_thread.run_sync(
                    lambda: audit.record(
                        AUDIT_ACTION, target=f"ai_session:{session_id}", new_value=new_value
                    )
                )
                # The transcript rows (02 §8.2), on every path past the ownership
                # check — a refused or failed turn is still part of the
                # conversation the person sees. Never raises past here: the
                # stream is already closing and the audit row is written.
                try:
                    await anyio.to_thread.run_sync(
                        lambda: ai_usage_db.append_turn_messages(
                            session_id,
                            question=body.message,
                            answer="".join(answer_parts),
                            tools=tool_entries,
                            usage={
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "cache_read_input_tokens": cache_read_tokens,
                                "cost_usd": round(cost_usd, 6),
                            },
                            error_code=error_code,
                        )
                    )
                    if not state_saved:
                        await anyio.to_thread.run_sync(
                            ai_usage_db.touch_session, session_id, owner_uid
                        )
                except Exception:  # noqa: BLE001
                    logger.error(
                        "AI turn: could not write ai_messages for session %s (trace_id=%s)",
                        session_id,
                        trace_id,
                        exc_info=True,
                    )
                # Free the one-turn-at-a-time slot (409 path above). Last, so a
                # second tab cannot start reading the blob before it is written.
                try:
                    await anyio.to_thread.run_sync(ai_usage_db.release_turn, session_id, owner_uid)
                except Exception:  # noqa: BLE001
                    logger.error("AI turn: could not release the turn claim for session %s", session_id, exc_info=True)
                # The one INFO line per turn (OPT-0058: never one per event).
                logger.info(
                    "AI turn: user=%s model=%s tools=%s subjects=%d tokens=%d/%d cost=%.4f "
                    "reason=%s error=%s elapsed=%.1fs",
                    user.email if user else "-",
                    body.model,
                    ",".join(tools_called) or "-",
                    len(subjects),
                    input_tokens,
                    output_tokens,
                    cost_usd,
                    terminal_reason,
                    error_code or "-",
                    time.monotonic() - started,
                )
            finally:
                frames.put_nowait(_END)

    worker = asyncio.create_task(_run_turn())
    _DRAIN_TASKS.add(worker)
    worker.add_done_callback(_DRAIN_TASKS.discard)

    async def _events() -> AsyncIterator[bytes]:
        """Relay the worker's frames to the browser; keepalives while idle.

        When the browser goes away this generator simply stops (Starlette
        closes it); `disconnected` tells the worker to keep consuming the
        agent stream without queueing frames nobody will read.
        """
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(frames.get(), timeout=KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield sse_keepalive()
                    continue
                if item is _END:
                    break
                yield item
        finally:
            if not worker.done():
                disconnected.set()

    return StreamingResponse(_events(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/usage/today", response_model=UsageToday)
def usage_today(request: Request) -> UsageToday:
    """The caller's quota position for the page's status bar."""
    settings = get_settings()
    user: SessionUser | None = getattr(request.state, "user", None)
    day = ai_usage_db.today_hk()
    usage = ai_usage_db.get_usage(_quota_user_id(user), day)
    return UsageToday(
        day_hk=day,
        turns=usage["turns"],
        turns_limit=settings.AI_DAILY_TURNS_LIMIT,
        cost_usd=round(usage["cost_usd"], 4),
        cost_limit_usd=settings.AI_DAILY_COST_LIMIT_USD,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
    )


# ── sessions (OPT-0065 §8.4) ─────────────────────────────────────────────────
#
# Plain `def`: SQLite reads and writes, sub-millisecond, no awaiting. Behind
# the same API key + `ai` module gate as everything else on this router; not
# added to SESSION_ONLY_PATHS (that list is for the SSE POST the browser must
# reach without a header, and apiFetch sends the key on these anyway).
#
# Ownership: every store call takes the caller's user_id and matches on it in
# SQL. A foreign, unknown or deleted session is indistinguishable from the
# outside — 404 in all three cases, never 403 (that is the module gate's word).


@router.get("/sessions", response_model=SessionList)
def list_sessions(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> SessionList:
    user: SessionUser | None = getattr(request.state, "user", None)
    data, total = ai_usage_db.list_sessions(_quota_user_id(user), limit=limit)
    return SessionList(data=data, total=total)


@router.get("/sessions/{session_id}", response_model=SessionDetail)
def get_session(session_id: str, request: Request) -> SessionDetail:
    user: SessionUser | None = getattr(request.state, "user", None)
    detail = ai_usage_db.get_session_detail(session_id, _quota_user_id(user))
    if detail is None:
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND)
    return SessionDetail(**detail)


@router.delete("/sessions/{session_id}", response_model=OkResponse)
def delete_session(
    session_id: str,
    request: Request,
    audit: Auditor = Depends(get_auditor),
) -> OkResponse:
    user: SessionUser | None = getattr(request.state, "user", None)
    before = ai_usage_db.soft_delete_session(session_id, _quota_user_id(user))
    if before is None:
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND)
    audit.record(
        AUDIT_SESSION_DELETE,
        target=f"ai_session:{session_id}",
        old_value={"title": before["title"], "turns": before["turns"]},
    )
    return OkResponse()


@router.patch("/sessions/{session_id}", response_model=OkResponse)
def rename_session(
    session_id: str,
    body: SessionRename,
    request: Request,
    audit: Auditor = Depends(get_auditor),
) -> OkResponse:
    user: SessionUser | None = getattr(request.state, "user", None)
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title must not be blank")
    old = ai_usage_db.rename_session(session_id, _quota_user_id(user), title=title)
    if old is None:
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND)
    # record_diff writes nothing when the title did not change — a no-op
    # rename is not an event worth a row.
    audit.record_diff(
        AUDIT_SESSION_RENAME,
        target=f"ai_session:{session_id}",
        old={"title": old},
        new={"title": title},
    )
    return OkResponse()
