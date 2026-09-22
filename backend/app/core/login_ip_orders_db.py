"""
SQLite database for per-order open IP capture (OPT-0063 Phase 1).

Stores:
- `order_ip`            — one row per (server, order ticket): the client IPv4
                          the order was placed from, parsed out of the daily MT
                          journal by `login_ip_analyzer_service._match_order_event`
- `order_ip_parse_runs` — one row per (MT day, server) parse audit: how many
                          lines were scanned and how many order rows landed.
                          Lets the Phase 2 coverage endpoint list "which
                          (date, server) log was incomplete" weeks later, when
                          the gap would otherwise look like "low profit that day".

The DB file lives at `backend/data/login_ip_orders.db` — deliberately NOT in
`login_ip.db`: at ~120k rows per weekday this table dwarfs the six-tab
watchlist DB, and a separate file keeps VACUUM / backup / WAL growth off the
hot path shared by the Login IP UI.

Schema notes
------------
- `UNIQUE(server_name, order_ref)` — one row per order ticket; re-running the
  same day INSERT OR REPLACEs. Known risk (tracked in the OPT item): ticket
  spaces are only verified unique per server per day, not across 120 days —
  watch for cross-day overwrite during the first week after go-live.
- NO index on `ip_address`: this table is only ever joined by
  (server_name, order_ref). The IP index lives on the Phase 2 `trade_ip_pnl`
  table, which is what the rankings read.
- `_SCHEMA_SQL` is CREATE TABLE IF NOT EXISTS only. Indexes here may only
  reference columns created with the table (same pitfall as
  `users_db._SCHEMA` — an index on a later ALTERed column crashes startup on
  old live DBs).

Retention (decided 2026-09-22):
- `order_ip`: 120 days (raw evidence; rankings read the Phase 2 result table).
- `order_ip_parse_runs`: 400 days, aligned with the Phase 2 `trade_ip_pnl`
  window so coverage can answer "log incomplete" vs "before go-live" for any
  day a ranking can show. 3 rows/day — size is a non-issue.

Uses Python built-in sqlite3 — no extra dependencies.
"""

from __future__ import annotations

import datetime as _dt
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

# Resolve to <repo>/backend/data/login_ip_orders.db regardless of CWD.
# __file__ = backend/app/core/login_ip_orders_db.py → parents[2] = backend/
_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "login_ip_orders.db"

# Retention for the raw per-order IP evidence table. Decided 2026-09-22:
# 120 days (the OPT v1 draft said 400 — superseded; 400 days is for the
# Phase 2 reconciled `trade_ip_pnl` table, which the rankings read).
DEFAULT_ORDER_IP_RETENTION_DAYS = 120

# Retention for the parse audit trail. Matches the Phase 2 ranking window so
# the coverage endpoint can tell "that day's log was incomplete" apart from
# "that day predates go-live" for any day a ranking can display.
DEFAULT_PARSE_RUN_RETENTION_DAYS = 400


_SCHEMA_SQL = """
-- One row per order ticket observed in the journal with a client IPv4.
-- order_ref is the MT4 TICKET (join mt4_trades.ticketSid = '{sid}-{ref}')
-- or the MT5 Order ticket (open OR close order — open/close is resolved by
-- the nightly reconciliation in Phase 2, NOT at parse time).
CREATE TABLE IF NOT EXISTS order_ip (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date    TEXT    NOT NULL,   -- YYYYMMDD (MT day of the journal file)
    server_name   TEXT    NOT NULL,   -- MT4 | MT5 | MT4_Live2
    account_id    INTEGER NOT NULL,
    order_ref     INTEGER NOT NULL,   -- MT4 TICKET / MT5 Order ticket
    ip_address    TEXT    NOT NULL,
    event_time_mt TEXT    NOT NULL,   -- HH:MM:SS.mmm, MT server local time
    event_kind    TEXT    NOT NULL,   -- MT4: 'order' | MT5: 'performed' | 'placed'
    cmd           TEXT    NOT NULL,   -- buy | sell | buy limit | sell stop ...
    lots          REAL,
    symbol        TEXT,
    UNIQUE (server_name, order_ref)   -- re-run of the same day overwrites
);

CREATE INDEX IF NOT EXISTS idx_order_ip_date ON order_ip(trade_date);

-- Parse audit: one row per (MT day, server) actually parsed. The Phase 2
-- coverage endpoint diffs this against the expected 3 servers/day.
CREATE TABLE IF NOT EXISTS order_ip_parse_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date    TEXT    NOT NULL,   -- YYYYMMDD (MT day)
    server_name   TEXT    NOT NULL,
    lines_scanned INTEGER NOT NULL,
    rows_written  INTEGER NOT NULL,
    parsed_at     TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours')),
    UNIQUE (trade_date, server_name)  -- re-run of the same day overwrites
);
"""


# ---------------------------------------------------------------------------
# Connection & initialization
# ---------------------------------------------------------------------------


@contextmanager
def get_connection():
    """Yield a sqlite3 Connection with Row factory and WAL mode.

    Same pattern as login_ip_db.get_connection: connection-per-call keeps the
    code thread-safe; WAL lets API reads overlap the daily batch write.
    """
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    # WAL journal: safe multi-reader + single-writer, better than default DELETE mode.
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
    finally:
        conn.close()


def init_login_ip_orders_db() -> None:
    """Create tables + indexes on first run. Safe to call repeatedly."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    logger.info("Login IP orders SQLite database initialized at %s", _DB_PATH)


# ---------------------------------------------------------------------------
# order_ip CRUD
# ---------------------------------------------------------------------------


def upsert_order_ips(
    records: Iterable[tuple[str, str, int, int, str, str, str, str, float | None, str | None]],
) -> int:
    """Batch-upsert per-order IP rows. Returns the number written.

    Each tuple: (trade_date, server_name, account_id, order_ref, ip_address,
                 event_time_mt, event_kind, cmd, lots, symbol).

    INSERT OR REPLACE (not IGNORE): re-running the same day must WIN over the
    stored row — a re-parse can legitimately read a more complete log than a
    first run that raced a partial download. Same convention as
    login_ip_db.upsert_last_trade_ips.
    """
    records = list(records)
    if not records:
        return 0
    with get_connection() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO order_ip "
            "(trade_date, server_name, account_id, order_ref, ip_address, "
            " event_time_mt, event_kind, cmd, lots, symbol) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            records,
        )
        conn.commit()
    logger.info("order_ip: upserted %d rows", len(records))
    return len(records)


def get_order_ips_for_date(trade_date: str, server_name: str | None = None) -> list[dict]:
    """Every order_ip row for one MT day (optionally one server). No limit —
    a weekday is ~120k rows; this serves batch consumers (Phase 2 reconcile),
    not the UI.
    """
    sql = (
        "SELECT trade_date, server_name, account_id, order_ref, ip_address, "
        "       event_time_mt, event_kind, cmd, lots, symbol "
        "FROM order_ip WHERE trade_date = ?"
    )
    params: list = [trade_date]
    if server_name:
        sql += " AND server_name = ?"
        params.append(server_name)
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def cleanup_old_order_ip(days: int = DEFAULT_ORDER_IP_RETENTION_DAYS) -> int:
    """Delete order_ip rows older than `days`. Returns count deleted.

    Runs nightly via the report job's housekeeping (same slot as the other
    login-ip retention sweeps).
    """
    cutoff = (_dt.datetime.now() - _dt.timedelta(days=days)).strftime("%Y%m%d")

    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM order_ip WHERE trade_date < ?", (cutoff,))
        deleted = cursor.rowcount
        conn.commit()

    if deleted:
        logger.info("cleanup_old_order_ip: removed %d rows older than %s", deleted, cutoff)
    return deleted


# ---------------------------------------------------------------------------
# order_ip_parse_runs CRUD
# ---------------------------------------------------------------------------


def record_parse_runs(
    records: Iterable[tuple[str, str, int, int]],
) -> int:
    """Batch-upsert parse audit rows. Returns the number written.

    Each tuple: (trade_date, server_name, lines_scanned, rows_written).
    REPLACE so a re-run of the same day overwrites its own audit row.
    """
    records = list(records)
    if not records:
        return 0
    with get_connection() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO order_ip_parse_runs "
            "(trade_date, server_name, lines_scanned, rows_written) "
            "VALUES (?, ?, ?, ?)",
            records,
        )
        conn.commit()
    logger.info("order_ip_parse_runs: recorded %d rows", len(records))
    return len(records)


def get_parse_runs(trade_date: str) -> list[dict]:
    """All parse audit rows for one MT day (coverage diff + ops inspection)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT trade_date, server_name, lines_scanned, rows_written, parsed_at "
            "FROM order_ip_parse_runs WHERE trade_date = ? ORDER BY server_name",
            (trade_date,),
        ).fetchall()
    return [dict(r) for r in rows]


def cleanup_old_parse_runs(days: int = DEFAULT_PARSE_RUN_RETENTION_DAYS) -> int:
    """Delete parse audit rows older than `days`. Returns count deleted."""
    cutoff = (_dt.datetime.now() - _dt.timedelta(days=days)).strftime("%Y%m%d")

    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM order_ip_parse_runs WHERE trade_date < ?", (cutoff,)
        )
        deleted = cursor.rowcount
        conn.commit()

    if deleted:
        logger.info("cleanup_old_parse_runs: removed %d rows older than %s", deleted, cutoff)
    return deleted
