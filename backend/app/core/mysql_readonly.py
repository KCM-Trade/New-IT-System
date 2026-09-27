"""The one way to open a MySQL replica connection from request-path code.

Built from the golden template in the ``db-timeout-guard`` skill — the three
defences that every connection to the fxbackoffice replica must carry:

1. ``connect_timeout``  — a replica that will not accept the socket in 5s is
   already in trouble; queueing on it holds an MDL slot open for nothing.
2. ``read_timeout``     — the client-side backstop. On its own it is NOT
   enough: a read timeout abandons the SOCKET, not the query, and the server
   thread keeps waiting behind whatever metadata lock it was queued on, as a
   zombie holding that same lock (2,637 of them in 14.5h on 2026-08-09).
3. ``MAX_EXECUTION_TIME`` — the server-side statement kill switch, the only
   one of the three that makes the SERVER stop. Always strictly below
   ``read_timeout`` so the server gives up first.

``autocommit=True`` is not decoration either: with the DB-API default of
``False`` the first SELECT opens a transaction that holds metadata locks until
COMMIT, while showing up in PROCESSLIST as a harmless-looking ``Sleep``. That is
verbatim the kcm-risk-pipeline failure of 2026-08-09.

Before OPT-0064 this shape lived in ``core/data_scope._connect`` and was being
copied wherever a different statement budget was needed (the AI tools needed
15s where data_scope's point lookups need 5s). Copies drift, and the direction
they drift in is "forgot one of the three" — so the budget is a parameter here
and the shape is not copied anywhere.
"""

from __future__ import annotations

import pymysql
import pymysql.cursors

from app.core.config import Settings

DEFAULT_MAX_EXECUTION_MS = 5000
DEFAULT_CONNECT_TIMEOUT_S = 5
DEFAULT_READ_TIMEOUT_S = 20


def connect_readonly(
    settings: Settings,
    *,
    max_execution_ms: int = DEFAULT_MAX_EXECUTION_MS,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_S,
    read_timeout: int = DEFAULT_READ_TIMEOUT_S,
) -> pymysql.connections.Connection:
    """Open a DictCursor connection to the fxbackoffice replica with all three
    timeouts set. Closes the socket if the session setup fails so a half-open
    connection never leaks. "Read-only" is a statement about how it is used —
    every caller runs SELECTs — not a server-side grant; the grant is the
    account's (see the ``ai_agent_ro`` TODO in the compose files)."""
    if max_execution_ms >= read_timeout * 1000:
        raise ValueError(
            "MAX_EXECUTION_TIME must stay below read_timeout so the server "
            "kills the statement before the client abandons the socket"
        )
    conn = pymysql.connect(
        host=settings.DB_HOST,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.FXBACK_DB_NAME,
        port=int(settings.DB_PORT),
        charset=settings.DB_CHARSET,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {int(max_execution_ms)}")
    except Exception:
        conn.close()
        raise
    return conn
