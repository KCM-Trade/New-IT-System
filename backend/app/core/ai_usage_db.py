"""SQLite store for the AI analyst agent's per-person daily quota (OPT-0064).

One table, ``ai_usage_daily``, in ``backend/data/ai_agent.db`` — a NEW file
rather than a sixth table in ``users.db`` (docs/ai-agent/02-contracts.md §6).
Two reasons for the separation:

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

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator
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
"""


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
