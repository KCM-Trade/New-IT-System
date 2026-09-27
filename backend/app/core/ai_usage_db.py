"""SQLite store for the AI analyst agent (OPT-0064 quota, OPT-0065 sessions).

``backend/data/ai_agent.db`` — a NEW file rather than more tables in
``users.db`` (docs/ai-agent/02-contracts.md §6 / §8.2). Three tables:

  * ``ai_usage_daily`` — per-person daily quota counters (OPT-0064).
  * ``ai_sessions``    — one row per conversation; ``blob`` is the serialised
    agent-framework ``AgentSession`` and is the ONLY source of the model's
    context on the next turn. Written by the main API alone: the agent
    container mounts this directory read-only and stays stateless.
  * ``ai_messages``    — the human-readable transcript (user / assistant text,
    tool summaries, usage) for the history list and replay. Never fed to the
    model.

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


def init_ai_usage_db() -> None:
    """Create the file and table if missing. Idempotent; called from lifespan."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(_SCHEMA)
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


def increment_turn(user_id: int, day_hk: str) -> None:
    """Count one turn.

    Called at interception time, i.e. the moment the route decides the turn
    may proceed — not when it finishes. A turn that is forwarded and then dies
    half-way still spent a model call, so it still counts. The route already
    holds the pre-increment snapshot from ``get_usage``, so nothing is read
    back here.
    """
    with _connect() as conn:
        conn.execute(
            "INSERT INTO ai_usage_daily (user_id, day_hk, turns) VALUES (?, ?, 1) "
            "ON CONFLICT(user_id, day_hk) DO UPDATE SET turns = turns + 1",
            (int(user_id), day_hk),
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


def _session_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "session_id": row["session_id"],
        "title": row["title"],
        "model": row["model"],
        "turns": int(row["turns"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


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
) -> None:
    """Append the two transcript rows of one turn (user, then assistant).

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
            "INSERT INTO ai_messages (session_id, seq, role, text, tools_json, usage_json, error_code, at) "
            "VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?)",
            (session_id, seq + 1, answer or "", tools_json, usage_json, error_code, now),
        )


def list_sessions(user_id: int, *, limit: int = 50) -> tuple[list[dict], int]:
    """The caller's live sessions, newest activity first, plus the total count."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_SESSION_COLUMNS} FROM ai_sessions "
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
            f"SELECT {_SESSION_COLUMNS} FROM ai_sessions "
            "WHERE session_id = ? AND user_id = ? AND deleted_at IS NULL",
            (session_id, int(user_id)),
        ).fetchone()
        if row is None:
            return None
        msgs = conn.execute(
            "SELECT seq, role, text, tools_json, usage_json, error_code, at "
            "FROM ai_messages WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
    return {
        "session": _session_row_to_dict(row),
        "messages": [
            {
                "seq": int(m["seq"]),
                "role": m["role"],
                "text": m["text"],
                "tools": _loads_or_none(m["tools_json"]),
                "usage": _loads_or_none(m["usage_json"]),
                "error_code": m["error_code"],
                "at": m["at"],
            }
            for m in msgs
        ],
    }


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
        conn.execute(f"DELETE FROM ai_messages WHERE session_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM ai_sessions WHERE session_id IN ({marks})", ids)
    return len(ids)
