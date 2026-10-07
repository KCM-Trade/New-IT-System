"""SQLite store for the AI analyst agent (OPT-0064 quota, OPT-0065 sessions).

``backend/data/ai_agent.db`` — a NEW file rather than more tables in
``users.db`` (docs/ai-agent/02-contracts.md §6 / §8.2). The tables:

  * ``ai_usage_daily`` — per-person daily quota counters (OPT-0064).
  * ``ai_sessions``    — one row per conversation; ``blob`` is the serialised
    agent-framework ``AgentSession`` and is the ONLY source of the model's
    context on the next turn. Written by the main API alone: the agent
    container mounts this directory read-only and stays stateless.
  * ``ai_messages``    — the human-readable transcript (user / assistant text,
    tool summaries, usage) for the history list and replay. Never fed to the
    model.
  * ``ai_compare_turns`` / ``ai_turn_candidates`` — compare mode (OPT-0076,
    02 §21): one question answered by 2–3 models, held OUTSIDE the two tables
    above until the user picks one.

Two reasons for keeping all of this out of ``users.db``:

  * ``users.db`` is the session store and sits on EVERY request's hot path
    through a thread-local long-lived connection pool. A quota counter that is
    bumped once per AI turn has no business contending for that file's write
    lock, and keeping it out means a runaway agent loop can never slow login.
  * The two files have different lifetimes. Sessions and audit rows are kept
    for a year and backed up as a unit; a daily usage counter is disposable
    (drop the file, quotas reset — nothing else changes).

Connection strategy: connect-per-call. This is deliberately NOT the
``users_db`` thread-local pool. That pool exists because ``resolve_session``
runs on every request and the ~5 PRAGMA round-trips per connect measured 3.4x
the lookup itself; here a connect costs less than the model call it precedes
by four orders of magnitude, and a connection that is opened and closed cannot
be left holding the WAL open for days (the ``users.db`` stale-read trap).

Both writes are single-statement UPSERTs, so they are atomic across the four
prod uvicorn workers without any application-level lock: SQLite serialises
writers on the file, and ``turns + 1`` is evaluated inside that serialisation.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

from app.core.logging_config import get_logger

logger = get_logger(__name__)

# backend/app/core/ai_usage_db.py -> parents[2] == backend/
_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "ai_agent.db"

_HK = ZoneInfo("Asia/Hong_Kong")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_usage_daily (
    user_id       INTEGER NOT NULL,
    day_hk        TEXT    NOT NULL,            -- YYYY-MM-DD in Asia/Hong_Kong
    turns         INTEGER NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day_hk)
);

-- OPT-0065 §8.2. `blob` is JSON text of AgentSession.to_dict(); NULL until the
-- first turn has completed. `deleted_at` is the soft-delete marker: the row
-- vanishes from the owner's list at once and is hard-deleted by the retention
-- sweep later, so a mis-click is recoverable for the retention window.
CREATE TABLE IF NOT EXISTS ai_sessions (
    session_id  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    title       TEXT,
    model       TEXT,
    blob        TEXT,
    turns       INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    deleted_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_sessions_user ON ai_sessions(user_id, updated_at);

CREATE TABLE IF NOT EXISTS ai_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    role        TEXT NOT NULL,
    text        TEXT NOT NULL,
    tools_json  TEXT,
    usage_json  TEXT,
    error_code  TEXT,
    at          TEXT NOT NULL,
    UNIQUE(session_id, seq)
);

-- OPT-0076 (02 §21). A compare turn: one question sent to 2–3 models at once.
-- While it waits for the user's choice NOTHING is written to ai_messages and
-- ai_sessions.blob does not move — the question lives here and each model's
-- answer + context in ai_turn_candidates. backend/data is one bind mount
-- shared by dev and prod, so code that predates these tables may be running
-- against this file; to it a pending compare is simply "that turn has not
-- happened yet" and the conversation stays fully usable. `base_seq` is how
-- the select step notices that such code moved the conversation on meanwhile.
-- state: running | pending | selected | void.
CREATE TABLE IF NOT EXISTS ai_compare_turns (
    compare_id     TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    user_id        INTEGER NOT NULL,
    question       TEXT NOT NULL,
    models_json    TEXT NOT NULL,
    base_seq       INTEGER NOT NULL,
    state          TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    finished_at    TEXT,
    selected_model TEXT,
    selected_at    TEXT,
    reason         TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_compare_session ON ai_compare_turns(session_id, state);

-- `blob` is one model's AgentSession.to_dict(); NULL for a failed run, and
-- set to NULL on EVERY row of the turn once one candidate has been selected.
CREATE TABLE IF NOT EXISTS ai_turn_candidates (
    compare_id  TEXT NOT NULL,
    model       TEXT NOT NULL,
    position    INTEGER NOT NULL,
    text        TEXT NOT NULL,
    tools_json  TEXT,
    usage_json  TEXT,
    error_code  TEXT,
    elapsed_ms  INTEGER,
    blob        TEXT,
    turns       INTEGER NOT NULL DEFAULT 0,
    selected    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (compare_id, model)
);

-- OPT-0065 §12. Daily-refreshed US economic-release calendar (FOMC page +
-- FRED release dates), read by the agent's get_economic_calendar tool through
-- the read-only mount. `source` groups rows so one source can be replaced
-- while the other keeps its last good rows; `econ_calendar_meta` records per
-- source when it last succeeded so the tool can report staleness honestly.
CREATE TABLE IF NOT EXISTS econ_calendar_cache (
    event_date  TEXT NOT NULL,
    time_utc    TEXT,
    country     TEXT NOT NULL,
    event       TEXT NOT NULL,
    importance  TEXT NOT NULL,
    source      TEXT NOT NULL,
    source_url  TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (event_date, country, event)
);
CREATE INDEX IF NOT EXISTS idx_econ_calendar_source ON econ_calendar_cache(source);
CREATE TABLE IF NOT EXISTS econ_calendar_meta (
    source      TEXT PRIMARY KEY,
    status      TEXT NOT NULL,             -- ok | failed | no_key
    fetched_at  TEXT NOT NULL,             -- last attempt
    ok_at       TEXT,                      -- last SUCCESSFUL fetch
    detail      TEXT
);
"""

# Above this the blob is still stored, but a WARNING is logged: a session whose
# serialised context is this large means compaction on the agent side is not
# doing its job, and that is worth knowing before the row hits SQLite's limits.
BLOB_WARN_CHARS = 4_000_000

# `title` is the first question, cut here (02 §8.2); the user can rename it.
TITLE_FROM_QUESTION_CHARS = 60


def utc_now_iso() -> str:
    """ISO8601 UTC with a trailing ``Z`` — the backend-wide timestamp shape."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_hk() -> str:
    """The quota day. Hong Kong calendar day, because that is the day the
    people using the page live in — a UTC boundary would reset everybody's
    quota at 08:00 in the office."""
    return datetime.now(_HK).strftime("%Y-%m-%d")


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(_DB_PATH), timeout=5.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Columns added after a table first shipped. ``CREATE TABLE IF NOT EXISTS`` is
# a no-op on the live file, so every later column needs its own guarded ALTER
# here — same rule as users_db._migrate_add_column, same reason (backend/data
# is a bind mount shared by dev and prod; dev restarts migrate prod's file).
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    # (table, column, declaration)
    # Turn claim for one-turn-at-a-time per session (cold review #6). ISO
    # timestamp while a turn is in flight, NULL when idle; a stale claim is
    # taken over by the next turn.
    ("ai_sessions", "turn_started_at", "TEXT"),
    # OPT-0076 (02 §21). Which model gave an assistant answer (single-model
    # turns write it too), and which compare turn it was selected from. Both
    # nullable and deliberately NOT indexed: an index in _SCHEMA on a column
    # that only exists after these ALTERs would fail on the live file.
    ("ai_messages", "model", "TEXT"),
    ("ai_messages", "compare_id", "TEXT"),
)


def _migrate_add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> bool:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column in cols:
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return True


def init_ai_usage_db() -> None:
    """Create the file and tables if missing, then add later columns. Idempotent;
    called from lifespan."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        for table, column, decl in _MIGRATIONS:
            if _migrate_add_column(conn, table, column, decl):
                logger.info("ai_agent.db: added %s.%s", table, column)
    logger.info("AI usage DB ready at %s", _DB_PATH)


def get_usage(user_id: int, day_hk: str) -> dict:
    """The row for (user, day), zeroed when absent. Never raises on a missing row."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT turns, input_tokens, output_tokens, cost_usd "
            "FROM ai_usage_daily WHERE user_id = ? AND day_hk = ?",
            (int(user_id), day_hk),
        ).fetchone()
    if row is None:
        return {"turns": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    return {
        "turns": int(row["turns"]),
        "input_tokens": int(row["input_tokens"]),
        "output_tokens": int(row["output_tokens"]),
        "cost_usd": float(row["cost_usd"]),
    }


def increment_turn(user_id: int, day_hk: str, n: int = 1) -> None:
    """Count one turn — or ``n`` of them: a compare turn is charged one turn
    per model it fans out to (02 §19).

    Called at interception time, i.e. the moment the route decides the turn
    may proceed — not when it finishes. A turn that is forwarded and then dies
    half-way still spent a model call, so it still counts. The route already
    holds the pre-increment snapshot from ``get_usage``, so nothing is read
    back here.
    """
    with _connect() as conn:
        conn.execute(
            "INSERT INTO ai_usage_daily (user_id, day_hk, turns) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, day_hk) DO UPDATE SET turns = turns + excluded.turns",
            (int(user_id), day_hk, int(n)),
        )


def add_usage(
    user_id: int,
    day_hk: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> None:
    """Accumulate tokens and cost for the day. Values come from the agent's
    ``usage`` event, priced by the main API — never from the browser."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO ai_usage_daily "
            "(user_id, day_hk, turns, input_tokens, output_tokens, cost_usd) "
            "VALUES (?, ?, 0, ?, ?, ?) "
            "ON CONFLICT(user_id, day_hk) DO UPDATE SET "
            "input_tokens = input_tokens + excluded.input_tokens, "
            "output_tokens = output_tokens + excluded.output_tokens, "
            "cost_usd = cost_usd + excluded.cost_usd",
            (
                int(user_id),
                day_hk,
                int(input_tokens or 0),
                int(output_tokens or 0),
                float(cost_usd or 0.0),
            ),
        )


# ── sessions (OPT-0065 §8.2 / §8.4) ──────────────────────────────────────────
#
# Every reader here takes the caller's ``user_id`` and matches on it INSIDE the
# SQL. There is deliberately no "get session by id" without an owner: the route
# answers 404 for a foreign or deleted session, and the cleanest way to make
# sure no code path forgets the check is to make the check impossible to skip.


_SESSION_COLUMNS = "session_id, user_id, title, model, turns, created_at, updated_at"

# 02 §23: does a compare turn on this session wait for the user's choice.
_PENDING_COMPARE_FLAG = (
    "EXISTS (SELECT 1 FROM ai_compare_turns c "
    "WHERE c.session_id = ai_sessions.session_id AND c.state = 'pending') AS pending_compare"
)


def _session_row_to_dict(row: sqlite3.Row) -> dict:
    out = {
        "session_id": row["session_id"],
        "title": row["title"],
        "model": row["model"],
        "turns": int(row["turns"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if "pending_compare" in row.keys():
        out["pending_compare"] = bool(row["pending_compare"])
    return out


def get_session_for_turn(session_id: str) -> Optional[dict]:
    """The row a turn needs to decide between resume / refuse / create.

    Returns ``None`` when no row exists (the route then creates one with the
    caller's id). Otherwise ``{"user_id", "deleted", "blob"}`` — ``blob`` is
    the PARSED dict or ``None``. The ownership decision is the route's: it
    holds the caller, this module holds the row.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_id, deleted_at, blob FROM ai_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    blob = None
    if row["blob"]:
        try:
            blob = json.loads(row["blob"])
        except (TypeError, ValueError):
            # A blob the agent framework wrote and we cannot read back is not
            # worth a 500 on the turn: the conversation continues without its
            # context and the transcript (ai_messages) is intact. Logged so a
            # framework upgrade that changed the format is visible.
            logger.warning("ai_sessions.blob is not valid JSON for session %s; resuming without context", session_id)
    return {"user_id": int(row["user_id"]), "deleted": row["deleted_at"] is not None, "blob": blob}


def create_session(session_id: str, user_id: int, *, title: str, model: str) -> None:
    """Insert the row for a brand-new conversation. Idempotent on a race:
    two requests minting the same id lose nothing (INSERT OR IGNORE)."""
    now = utc_now_iso()
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO ai_sessions "
            "(session_id, user_id, title, model, turns, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 0, ?, ?)",
            (session_id, int(user_id), title[:TITLE_FROM_QUESTION_CHARS], model, now, now),
        )


def save_session_state(session_id: str, user_id: int, *, blob: Any, turns: int, model: str) -> bool:
    """Write the agent's ``session_state`` event back (02 §8.3).

    Matches on ``user_id`` as well as ``session_id``: the event arrives on a
    stream the route already authorised, but the check costs nothing and means
    a bug upstream cannot write one person's context into another's row.
    ``turns`` only ever grows. Returns whether a row was updated.
    """
    text = json.dumps(blob, ensure_ascii=False, default=str)
    if len(text) > BLOB_WARN_CHARS:
        logger.warning(
            "ai_sessions.blob for session %s is %d chars (> %d): compaction is not keeping the context small",
            session_id, len(text), BLOB_WARN_CHARS,
        )
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE ai_sessions SET blob = ?, turns = MAX(turns, ?), model = ?, updated_at = ? "
            "WHERE session_id = ? AND user_id = ? AND deleted_at IS NULL",
            (text, int(turns or 0), model, utc_now_iso(), session_id, int(user_id)),
        )
        return cur.rowcount > 0


# A claim older than this is treated as abandoned: the worker that held it
# died (deploy, crash) before the finally that clears it. It MUST exceed the
# longest a turn can legitimately run — ai_gateway_service.TURN_TOTAL_SECONDS
# (560 s; the disconnect drain is capped by what is left of that same budget)
# — or a second tab could take the claim from a turn that is still running.
# It was 360 until OPT-0076, i.e. below the turn limit. The same figure ages
# out a compare turn left in `running` by a dead worker.
TURN_CLAIM_STALE_SECONDS = 12 * 60

# Why claim_turn() refused, as told by turn_refusal(). The values are the
# route's 409 `detail` strings (02 §19).
REFUSAL_COMPARE_PENDING = "compare pending"
REFUSAL_SESSION_BUSY = "session busy"


def claim_turn(session_id: str, user_id: int, *, now: Optional[datetime] = None) -> bool:
    """Take the session's single turn slot; False when another turn holds it.

    One UPDATE, so the check-and-set is atomic under SQLite's writer lock
    across the four uvicorn workers. Two tabs firing on the same session
    used to race: both turns ran against the same blob and the second
    write-back silently dropped the first turn from the model's memory while
    the transcript kept it (cold review #6). Now the second caller gets 409.

    Also False while a compare turn on this session waits for the user's
    choice (OPT-0076): the next question has to build on ONE of the candidate
    contexts, and which one is not decided yet. ``turn_refusal`` tells the two
    refusals apart.
    """
    now = now or datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    stale_iso = (now - timedelta(seconds=TURN_CLAIM_STALE_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _connect() as conn:
        # A compare turn whose worker died stays `running` forever; void it
        # here, lazily, so it neither blocks nor lingers. One transaction with
        # the claim below.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE ai_compare_turns SET state = 'void', finished_at = ? "
            "WHERE session_id = ? AND state = 'running' AND created_at < ?",
            (now_iso, session_id, stale_iso),
        )
        cur = conn.execute(
            "UPDATE ai_sessions SET turn_started_at = ? "
            "WHERE session_id = ? AND user_id = ? AND deleted_at IS NULL "
            "AND (turn_started_at IS NULL OR turn_started_at < ?) "
            "AND NOT EXISTS (SELECT 1 FROM ai_compare_turns c "
            "WHERE c.session_id = ai_sessions.session_id AND c.state = 'pending')",
            (now_iso, session_id, int(user_id), stale_iso),
        )
        return cur.rowcount > 0


def turn_refusal(session_id: str) -> str:
    """Why ``claim_turn`` just said no: ``REFUSAL_COMPARE_PENDING`` when a
    compare turn waits for a choice, otherwise ``REFUSAL_SESSION_BUSY``.

    A separate read after the failed claim rather than a richer return value,
    so the claim itself stays one atomic UPDATE. Pending wins when both hold
    (possible only if code that predates compare mode took the claim).
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM ai_compare_turns WHERE session_id = ? AND state = 'pending' LIMIT 1",
            (session_id,),
        ).fetchone()
    return REFUSAL_COMPARE_PENDING if row is not None else REFUSAL_SESSION_BUSY


def release_turn(session_id: str, user_id: int) -> None:
    """Free the turn slot. Called from the turn's finally on every path."""
    with _connect() as conn:
        conn.execute(
            "UPDATE ai_sessions SET turn_started_at = NULL WHERE session_id = ? AND user_id = ?",
            (session_id, int(user_id)),
        )


def touch_session(session_id: str, user_id: int) -> None:
    """Bump ``updated_at`` so a turn that produced no ``session_state`` (an
    error before the agent answered) still moves the session to the top."""
    with _connect() as conn:
        conn.execute(
            "UPDATE ai_sessions SET updated_at = ? WHERE session_id = ? AND user_id = ?",
            (utc_now_iso(), session_id, int(user_id)),
        )


def append_turn_messages(
    session_id: str,
    *,
    question: str,
    answer: str,
    tools: list[dict],
    usage: dict,
    error_code: Optional[str],
    model: Optional[str] = None,
) -> None:
    """Append the two transcript rows of one turn (user, then assistant).
    ``model`` is recorded on the assistant row.

    One transaction, ``seq`` read and written inside it, so two concurrent
    turns on the same session (two tabs) cannot collide on the UNIQUE
    constraint — SQLite serialises writers on the file. Written on every path
    that reached the agent, including errors: a failed turn is still part of
    the conversation the person sees.
    """
    now = utc_now_iso()
    tools_json = json.dumps(tools, ensure_ascii=False, default=str) if tools else None
    usage_json = json.dumps(usage, ensure_ascii=False, default=str) if usage else None
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM ai_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        seq = int(row[0]) + 1
        conn.execute(
            "INSERT INTO ai_messages (session_id, seq, role, text, tools_json, usage_json, error_code, at) "
            "VALUES (?, ?, 'user', ?, NULL, NULL, NULL, ?)",
            (session_id, seq, question, now),
        )
        conn.execute(
            "INSERT INTO ai_messages (session_id, seq, role, text, tools_json, usage_json, error_code, at, model) "
            "VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?, ?)",
            (session_id, seq + 1, answer or "", tools_json, usage_json, error_code, now, model),
        )


def list_sessions(user_id: int, *, limit: int = 50) -> tuple[list[dict], int]:
    """The caller's live sessions, newest activity first, plus the total count."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_SESSION_COLUMNS}, {_PENDING_COMPARE_FLAG} FROM ai_sessions "
            "WHERE user_id = ? AND deleted_at IS NULL "
            "ORDER BY updated_at DESC, created_at DESC LIMIT ?",
            (int(user_id), int(limit)),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM ai_sessions WHERE user_id = ? AND deleted_at IS NULL",
            (int(user_id),),
        ).fetchone()[0]
    return [_session_row_to_dict(r) for r in rows], int(total)


def get_session_detail(session_id: str, user_id: int) -> Optional[dict]:
    """``{"session": {...}, "messages": [...]}`` for the owner; ``None`` for
    anyone else or a deleted row. The blob is never in this payload: it is the
    framework's private format and carries raw tool results."""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_SESSION_COLUMNS}, {_PENDING_COMPARE_FLAG} FROM ai_sessions "
            "WHERE session_id = ? AND user_id = ? AND deleted_at IS NULL",
            (session_id, int(user_id)),
        ).fetchone()
        if row is None:
            return None
        msgs = conn.execute(
            "SELECT seq, role, text, tools_json, usage_json, error_code, at, model, compare_id "
            "FROM ai_messages WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
        # Compare turns behind this transcript (02 §23): the answers that were
        # not chosen ride along on the assistant row they lost to.
        outcomes: dict[str, dict] = {}
        for compare_id in {m["compare_id"] for m in msgs if m["compare_id"]}:
            turn = conn.execute(
                "SELECT reason FROM ai_compare_turns WHERE compare_id = ? AND session_id = ?",
                (compare_id, session_id),
            ).fetchone()
            if turn is None:
                continue
            outcomes[compare_id] = {
                "compare_id": compare_id,
                "reason": turn["reason"],
                "alternatives": [
                    _candidate_to_dict(c)
                    for c in _candidate_rows(conn, compare_id)
                    if not c["selected"]
                ],
            }
        pending = _pending_compare(conn, session_id, int(user_id))
    messages = []
    for m in msgs:
        message = {
            "seq": int(m["seq"]),
            "role": m["role"],
            "text": m["text"],
            "tools": _loads_or_none(m["tools_json"]),
            "usage": _loads_or_none(m["usage_json"]),
            "error_code": m["error_code"],
            "at": m["at"],
            "model": m["model"],
        }
        if m["compare_id"] in outcomes:
            message["compare"] = outcomes[m["compare_id"]]
        messages.append(message)
    return {"session": _session_row_to_dict(row), "messages": messages, "pending_compare": pending}


def _loads_or_none(text: Optional[str]) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def soft_delete_session(session_id: str, user_id: int) -> Optional[dict]:
    """Mark the owner's session deleted. Returns the pre-delete summary (for
    the audit row's ``old_value``) or ``None`` when there was nothing to
    delete for this caller."""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_SESSION_COLUMNS} FROM ai_sessions "
            "WHERE session_id = ? AND user_id = ? AND deleted_at IS NULL",
            (session_id, int(user_id)),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE ai_sessions SET deleted_at = ? WHERE session_id = ?",
            (utc_now_iso(), session_id),
        )
    return _session_row_to_dict(row)


def rename_session(session_id: str, user_id: int, *, title: str) -> Optional[str]:
    """Set the owner's session title. Returns the OLD title (for the audit
    diff) or ``None`` when the session is not this caller's."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT title FROM ai_sessions WHERE session_id = ? AND user_id = ? AND deleted_at IS NULL",
            (session_id, int(user_id)),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE ai_sessions SET title = ?, updated_at = ? WHERE session_id = ?",
            (title, utc_now_iso(), session_id),
        )
    return row["title"]


def purge_ai_sessions(retention_days: int, *, now: Optional[datetime] = None) -> int:
    """Hard-delete sessions soft-deleted more than ``retention_days`` ago,
    with their messages. Returns how many sessions went.

    ``0`` means KEEP FOREVER, not "delete everything" — same convention as the
    users.db purges. Rows with ``deleted_at IS NULL`` are never touched here:
    an investigation the analyst has not closed is theirs to keep, however
    old (05 §6.3 acceptance).
    """
    days = int(retention_days or 0)
    if days <= 0:
        return 0
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _connect() as conn:
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT session_id FROM ai_sessions WHERE deleted_at IS NOT NULL AND deleted_at < ?",
                (cutoff,),
            ).fetchall()
        ]
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        # Compare turns go with their session (02 §21) — candidates first,
        # they are keyed by the turn.
        conn.execute(
            "DELETE FROM ai_turn_candidates WHERE compare_id IN "
            f"(SELECT compare_id FROM ai_compare_turns WHERE session_id IN ({marks}))",
            ids,
        )
        conn.execute(f"DELETE FROM ai_compare_turns WHERE session_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM ai_messages WHERE session_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM ai_sessions WHERE session_id IN ({marks})", ids)
    return len(ids)


# ── compare mode (OPT-0076, 02 §19–§23) ──────────────────────────────────────
#
# Lifecycle of one compare turn:
#
#   begin_compare    running   the route holds the session's turn claim
#   finish_compare   pending   >= 1 selectable candidate; waits for the user
#                    void      nobody answered; written to ai_messages as a
#                              failed turn right away
#   select_candidate selected  the chosen blob becomes the session's context
#                              and the transcript rows are written, in ONE
#                              transaction; every candidate blob is dropped
#
# Until `selected` (or `void` from finish) ai_messages and ai_sessions.blob are
# not touched — see the comment on the tables in _SCHEMA for why.

COMPARE_RUNNING = "running"
COMPARE_PENDING = "pending"
COMPARE_SELECTED = "selected"
COMPARE_VOID = "void"

# error_code on the assistant row of a compare turn in which no model produced
# a selectable answer, and on a run the worker stopped waiting for.
ERROR_COMPARE_FAILED = "compare_failed"
ERROR_RUN_INCOMPLETE = "incomplete"

# select_candidate() outcomes.
SELECT_OK = "selected"
SELECT_UNCHANGED = "unchanged"
SELECT_NOT_FOUND = "not_found"
SELECT_BUSY = "busy"
SELECT_ALREADY = "already_selected"
SELECT_STALE = "stale"
SELECT_NOT_SELECTABLE = "not_selectable"


def _stale_iso(now: datetime) -> str:
    return (now - timedelta(seconds=TURN_CLAIM_STALE_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _candidate_rows(conn: sqlite3.Connection, compare_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT model, position, text, tools_json, usage_json, error_code, elapsed_ms, "
        "blob IS NOT NULL AS has_blob, turns, selected "
        "FROM ai_turn_candidates WHERE compare_id = ? ORDER BY position",
        (compare_id,),
    ).fetchall()


def _candidate_to_dict(row: sqlite3.Row) -> dict:
    """A candidate as the browser may see it — never the blob."""
    return {
        "model": row["model"],
        "text": row["text"],
        "tools": _loads_or_none(row["tools_json"]),
        "usage": _loads_or_none(row["usage_json"]),
        "error_code": row["error_code"],
        "elapsed_ms": row["elapsed_ms"],
    }


def is_selectable(*, has_blob: bool, error_code: Optional[str], text: Optional[str]) -> bool:
    """02 §20: a candidate can be chosen when the run left a context to carry
    on from, did not fail, and actually said something."""
    return bool(has_blob) and not error_code and bool((text or "").strip())


def _row_selectable(row: sqlite3.Row) -> bool:
    return is_selectable(has_blob=row["has_blob"], error_code=row["error_code"], text=row["text"])


def count_running_compares(*, now: Optional[datetime] = None) -> int:
    """Compare turns in flight across ALL workers. A `running` row older than
    the claim-stale window belongs to a dead worker and is not counted."""
    now = now or datetime.now(timezone.utc)
    with _connect() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM ai_compare_turns WHERE state = 'running' AND created_at >= ?",
                (_stale_iso(now),),
            ).fetchone()[0]
        )


def begin_compare(
    compare_id: str,
    session_id: str,
    user_id: int,
    *,
    question: str,
    models: list[str],
    max_concurrent: int,
    now: Optional[datetime] = None,
) -> bool:
    """Open a compare turn in `running`; False when ``max_concurrent`` compare
    turns are already in flight server-wide (the route answers `compare_busy`).

    The count and the insert are ONE statement, so the limit holds across the
    four uvicorn workers without an in-process semaphore: SQLite serialises
    writers on the file and the subquery is evaluated inside that
    serialisation. ``base_seq`` is the session's last transcript seq right
    now — select_candidate() refuses if it has moved by then.
    """
    now = now or datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO ai_compare_turns "
            "(compare_id, session_id, user_id, question, models_json, base_seq, state, created_at) "
            "SELECT ?, ?, ?, ?, ?, "
            "(SELECT COALESCE(MAX(seq), 0) FROM ai_messages WHERE session_id = ?), 'running', ? "
            "WHERE (SELECT COUNT(*) FROM ai_compare_turns "
            "WHERE state = 'running' AND created_at >= ?) < ?",
            (
                compare_id,
                session_id,
                int(user_id),
                question,
                json.dumps(list(models), ensure_ascii=False),
                session_id,
                now_iso,
                _stale_iso(now),
                int(max_concurrent),
            ),
        )
        return cur.rowcount > 0


def finish_compare(
    compare_id: str,
    session_id: str,
    user_id: int,
    *,
    candidates: list[dict],
) -> dict:
    """Close a `running` compare turn: store every run and decide the state.

    ``candidates`` — one dict per model, in column order:
    ``{"model", "text", "tools", "usage", "error_code", "elapsed_ms", "blob",
    "turns"}`` (``blob`` a dict or None).

    Returns ``{"state": "pending" | "void", "selectable": [model, ...]}``.

      * at least one selectable run -> `pending`. ai_messages and
        ai_sessions.blob are NOT touched; only `updated_at` moves so the
        conversation surfaces at the top of the list.
      * none -> `void`, and the turn is written to ai_messages as a failed
        turn at once (question + empty answer with `compare_failed`), so the
        conversation is free for the next question.

    One transaction. A turn that is no longer `running` (voided as stale by a
    later claim) is left alone and reported `void`.
    """
    now = utc_now_iso()
    prepared = []
    for position, cand in enumerate(candidates):
        blob = cand.get("blob")
        blob_text = json.dumps(blob, ensure_ascii=False, default=str) if isinstance(blob, dict) else None
        if blob_text is not None and len(blob_text) > BLOB_WARN_CHARS:
            logger.warning(
                "ai_turn_candidates.blob for compare %s / %s is %d chars (> %d)",
                compare_id, cand.get("model"), len(blob_text), BLOB_WARN_CHARS,
            )
        tools = cand.get("tools")
        usage = cand.get("usage")
        prepared.append(
            {
                "model": str(cand["model"]),
                "position": position,
                "text": cand.get("text") or "",
                "tools_json": json.dumps(tools, ensure_ascii=False, default=str) if tools else None,
                "usage_json": json.dumps(usage, ensure_ascii=False, default=str) if usage else None,
                "usage": usage or {},
                "error_code": cand.get("error_code"),
                "elapsed_ms": cand.get("elapsed_ms"),
                "blob": blob_text,
                "turns": int(cand.get("turns") or 0),
            }
        )
    selectable = [
        c["model"]
        for c in prepared
        if is_selectable(has_blob=c["blob"] is not None, error_code=c["error_code"], text=c["text"])
    ]
    state = COMPARE_PENDING if selectable else COMPARE_VOID
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        turn = conn.execute(
            "SELECT question, state FROM ai_compare_turns "
            "WHERE compare_id = ? AND session_id = ? AND user_id = ?",
            (compare_id, session_id, int(user_id)),
        ).fetchone()
        if turn is None or turn["state"] != COMPARE_RUNNING:
            return {"state": COMPARE_VOID, "selectable": []}
        conn.executemany(
            "INSERT OR REPLACE INTO ai_turn_candidates "
            "(compare_id, model, position, text, tools_json, usage_json, error_code, elapsed_ms, blob, turns, selected) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            [
                (
                    compare_id,
                    c["model"],
                    c["position"],
                    c["text"],
                    c["tools_json"],
                    c["usage_json"],
                    c["error_code"],
                    c["elapsed_ms"],
                    # Nothing can be chosen from a void turn, so no context is kept.
                    c["blob"] if state == COMPARE_PENDING else None,
                    c["turns"],
                )
                for c in prepared
            ],
        )
        conn.execute(
            "UPDATE ai_compare_turns SET state = ?, finished_at = ? WHERE compare_id = ?",
            (state, now, compare_id),
        )
        if state == COMPARE_VOID:
            totals = {
                key: sum((c["usage"].get(key) or 0) for c in prepared)
                for key in ("input_tokens", "output_tokens", "cache_read_input_tokens")
            }
            totals["cost_usd"] = round(sum(float(c["usage"].get("cost_usd") or 0.0) for c in prepared), 6)
            seq = int(
                conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM ai_messages WHERE session_id = ?", (session_id,)
                ).fetchone()[0]
            ) + 1
            conn.execute(
                "INSERT INTO ai_messages (session_id, seq, role, text, tools_json, usage_json, error_code, at) "
                "VALUES (?, ?, 'user', ?, NULL, NULL, NULL, ?)",
                (session_id, seq, turn["question"], now),
            )
            conn.execute(
                "INSERT INTO ai_messages "
                "(session_id, seq, role, text, tools_json, usage_json, error_code, at, model, compare_id) "
                "VALUES (?, ?, 'assistant', '', NULL, ?, ?, ?, NULL, ?)",
                (session_id, seq + 1, json.dumps(totals), ERROR_COMPARE_FAILED, now, compare_id),
            )
        conn.execute(
            "UPDATE ai_sessions SET updated_at = ? WHERE session_id = ? AND user_id = ?",
            (now, session_id, int(user_id)),
        )
    return {"state": state, "selectable": selectable}


def void_compare(compare_id: str) -> None:
    """Give up on a `running` compare turn whose results could not be stored.
    Without this the row would hold a concurrency slot until it ages out."""
    with _connect() as conn:
        conn.execute(
            "UPDATE ai_compare_turns SET state = 'void', finished_at = ? "
            "WHERE compare_id = ? AND state = 'running'",
            (utc_now_iso(), compare_id),
        )


def _pending_compare(
    conn: sqlite3.Connection, session_id: str, user_id: int, *, now: Optional[datetime] = None
) -> Optional[dict]:
    now = now or datetime.now(timezone.utc)
    turn = conn.execute(
        "SELECT compare_id, state, question, models_json, created_at FROM ai_compare_turns "
        "WHERE session_id = ? AND user_id = ? "
        "AND (state = 'pending' OR (state = 'running' AND created_at >= ?)) "
        # A pending turn outranks a running one; there is at most one of each.
        "ORDER BY state = 'pending' DESC, created_at DESC LIMIT 1",
        (session_id, int(user_id), _stale_iso(now)),
    ).fetchone()
    if turn is None:
        return None
    candidates = []
    for row in _candidate_rows(conn, turn["compare_id"]):
        candidate = _candidate_to_dict(row)
        candidate["selectable"] = _row_selectable(row)
        candidates.append(candidate)
    return {
        "compare_id": turn["compare_id"],
        "state": turn["state"],
        "question": turn["question"],
        "models": _loads_or_none(turn["models_json"]) or [],
        "created_at": turn["created_at"],
        "candidates": candidates,
    }


def get_pending_compare(session_id: str, user_id: int) -> Optional[dict]:
    """The owner's compare turn that is still `running` or waits for a choice
    (02 §23), with its candidates (empty while running; never a blob). ``None``
    when there is none, or the session is not this caller's."""
    with _connect() as conn:
        return _pending_compare(conn, session_id, int(user_id))


def select_candidate(
    session_id: str,
    user_id: int,
    *,
    compare_id: str,
    model: str,
    reason: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Make one candidate of a pending compare turn the conversation's answer.

    Returns ``{"status": SELECT_*, ...}``; the route maps the status to HTTP
    (02 §22). On ``SELECT_OK`` and ``SELECT_UNCHANGED`` the dict also carries
    ``others`` / ``selectable`` (model lists, column order) and
    ``old_reason`` / ``reason`` for the audit row.

    Everything happens in ONE ``BEGIN IMMEDIATE`` transaction: ownership and
    state checks, then ``ai_sessions.blob / turns / model`` <- the candidate's,
    the two ai_messages rows (the question; the chosen answer, tagged with
    ``model`` and ``compare_id``), ``selected = 1`` on that candidate,
    ``blob = NULL`` on ALL candidates, and the turn -> `selected`.

    Idempotent: the same model again is ``SELECT_UNCHANGED``; if this call
    brings a reason the turn does not have yet (or a different one) only the
    reason is updated. A different model after a selection is refused — the
    other contexts are gone.
    """
    now = now or datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        session = conn.execute(
            "SELECT turns, turn_started_at FROM ai_sessions "
            "WHERE session_id = ? AND user_id = ? AND deleted_at IS NULL",
            (session_id, int(user_id)),
        ).fetchone()
        turn = (
            conn.execute(
                "SELECT question, base_seq, state, created_at, finished_at, selected_model, reason "
                "FROM ai_compare_turns WHERE compare_id = ? AND session_id = ? AND user_id = ?",
                (compare_id, session_id, int(user_id)),
            ).fetchone()
            if session is not None
            else None
        )
        if session is None or turn is None:
            return {"status": SELECT_NOT_FOUND}

        rows = _candidate_rows(conn, compare_id)
        by_model = {r["model"]: r for r in rows}
        info = {
            "others": [r["model"] for r in rows if r["model"] != model],
            "old_reason": turn["reason"],
            "reason": turn["reason"],
        }

        if turn["state"] == COMPARE_SELECTED:
            if turn["selected_model"] != model:
                return {"status": SELECT_ALREADY}
            if reason is not None and reason != turn["reason"]:
                conn.execute(
                    "UPDATE ai_compare_turns SET reason = ? WHERE compare_id = ?", (reason, compare_id)
                )
                info["reason"] = reason
            # The blobs are gone, so "selectable" can no longer be derived.
            return {"status": SELECT_UNCHANGED, "selectable": [], **info}

        if turn["state"] == COMPARE_RUNNING:
            return {"status": SELECT_BUSY}
        if turn["state"] != COMPARE_PENDING:
            # void: nothing in it can be chosen.
            return {"status": SELECT_NOT_SELECTABLE}

        selectable = [r["model"] for r in rows if _row_selectable(r)]
        if model not in selectable:
            return {"status": SELECT_NOT_SELECTABLE}
        claim = session["turn_started_at"]
        if claim is not None and claim >= _stale_iso(now):
            # Only code that predates compare mode can hold the claim while a
            # compare is pending; its turn is about to move the conversation.
            return {"status": SELECT_BUSY}

        max_seq = int(
            conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM ai_messages WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
        )
        if max_seq != int(turn["base_seq"]):
            # The conversation moved on while this turn waited (older code on
            # the shared file, or a rolled-back image). None of the candidate
            # contexts contains that newer turn, so none may replace the blob.
            conn.execute(
                "UPDATE ai_compare_turns SET state = 'void', finished_at = COALESCE(finished_at, ?) "
                "WHERE compare_id = ?",
                (now_iso, compare_id),
            )
            conn.execute("UPDATE ai_turn_candidates SET blob = NULL WHERE compare_id = ?", (compare_id,))
            return {"status": SELECT_STALE}

        chosen = by_model[model]
        blob_text = conn.execute(
            "SELECT blob FROM ai_turn_candidates WHERE compare_id = ? AND model = ?", (compare_id, model)
        ).fetchone()["blob"]
        conn.execute(
            "UPDATE ai_sessions SET blob = ?, turns = MAX(turns, ?), model = ?, updated_at = ? "
            "WHERE session_id = ? AND user_id = ?",
            (blob_text, int(chosen["turns"] or 0), model, now_iso, session_id, int(user_id)),
        )
        conn.execute(
            "INSERT INTO ai_messages (session_id, seq, role, text, tools_json, usage_json, error_code, at) "
            "VALUES (?, ?, 'user', ?, NULL, NULL, NULL, ?)",
            (session_id, max_seq + 1, turn["question"], turn["created_at"]),
        )
        conn.execute(
            "INSERT INTO ai_messages "
            "(session_id, seq, role, text, tools_json, usage_json, error_code, at, model, compare_id) "
            "VALUES (?, ?, 'assistant', ?, ?, ?, NULL, ?, ?, ?)",
            (
                session_id,
                max_seq + 2,
                chosen["text"],
                chosen["tools_json"],
                chosen["usage_json"],
                turn["finished_at"] or now_iso,
                model,
                compare_id,
            ),
        )
        conn.execute(
            "UPDATE ai_turn_candidates SET selected = (model = ?), blob = NULL WHERE compare_id = ?",
            (model, compare_id),
        )
        conn.execute(
            "UPDATE ai_compare_turns SET state = 'selected', selected_model = ?, selected_at = ?, reason = ? "
            "WHERE compare_id = ?",
            (model, now_iso, reason, compare_id),
        )
        info["reason"] = reason
        info["old_reason"] = None
        return {"status": SELECT_OK, "selectable": selectable, **info}


# ── econ calendar cache (OPT-0065 §12) ───────────────────────────────────────


def replace_calendar_source(source: str, rows: list[dict], *, fetched_at: Optional[str] = None) -> int:
    """Replace every cached row of ONE source in a single transaction.

    Called only after the fetch AND parse succeeded, so a failing source can
    never leave the table half-empty. Rows are keyed (event_date, country,
    event); a row of another source with the same key is overwritten by the
    later writer, which is fine — both are official dates for the same event.
    """
    fetched_at = fetched_at or utc_now_iso()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM econ_calendar_cache WHERE source = ?", (source,))
        conn.executemany(
            "INSERT OR REPLACE INTO econ_calendar_cache "
            "(event_date, time_utc, country, event, importance, source, source_url, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    str(r["event_date"]),
                    r.get("time_utc"),
                    str(r.get("country") or "US"),
                    str(r["event"]),
                    str(r.get("importance") or "low"),
                    source,
                    str(r["source_url"]),
                    fetched_at,
                )
                for r in rows
            ],
        )
    return len(rows)


def record_calendar_source_status(
    source: str, status: str, *, now: Optional[str] = None, detail: Optional[str] = None
) -> None:
    """Per-source fetch outcome. ``ok_at`` moves only on success, so after a
    failure the tool can still say when the rows it is serving were good."""
    now = now or utc_now_iso()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO econ_calendar_meta (source, status, fetched_at, ok_at, detail) "
            "VALUES (?, ?, ?, CASE WHEN ? = 'ok' THEN ? ELSE NULL END, ?) "
            "ON CONFLICT(source) DO UPDATE SET "
            "status = excluded.status, fetched_at = excluded.fetched_at, detail = excluded.detail, "
            "ok_at = CASE WHEN excluded.status = 'ok' THEN excluded.fetched_at ELSE econ_calendar_meta.ok_at END",
            (source, status, now, status, now, detail),
        )


def calendar_status() -> dict[str, dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT source, status, fetched_at, ok_at, detail FROM econ_calendar_meta").fetchall()
    return {
        r["source"]: {"status": r["status"], "fetched_at": r["fetched_at"], "ok_at": r["ok_at"], "detail": r["detail"]}
        for r in rows
    }


def calendar_is_empty() -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM econ_calendar_cache").fetchone()
    return int(row["n"] if row is not None else 0) == 0


def read_calendar(
    day_from: str, day_to: str, *, countries: Optional[list[str]] = None, importance: str = "high"
) -> list[dict]:
    """Rows in the closed date window, ordered by date then time. ``importance``
    is ``"high"`` (high only) or ``"all"``."""
    sql = (
        "SELECT event_date, time_utc, country, event, importance, source, source_url, fetched_at "
        "FROM econ_calendar_cache WHERE event_date BETWEEN ? AND ?"
    )
    params: list[Any] = [day_from, day_to]
    if countries:
        sql += " AND country IN (%s)" % ", ".join("?" * len(countries))
        params.extend(str(c).upper() for c in countries)
    if importance == "high":
        sql += " AND importance = 'high'"
    sql += " ORDER BY event_date, time_utc, event"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]
