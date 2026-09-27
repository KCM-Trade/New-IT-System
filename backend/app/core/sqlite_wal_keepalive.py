"""Keep the WAL sidecar files of shared SQLite databases alive (OPT-0064).

The ai-agent container reads ``risk_monitor.db`` and ``login_ip_orders.db``
through a ``:ro`` bind mount of ``backend/data`` (docs/ai-agent/02-contracts.md
§7: the agent must hold no write path). Both files are in WAL mode, and a WAL
reader needs the ``-wal`` / ``-shm`` sidecars: it can OPEN them read-only, but
it cannot CREATE them on a read-only mount. SQLite deletes both sidecars when
the last connection to the file closes, so the moment this process has no
connection open the agent's read fails with "unable to open database file" —
measured on 2026-09-27 right after a dev restart, and fixed the moment one
host-side connection was held open.

So this process — which already owns the write side of both files — holds one
long-lived, read-only connection per database for its whole lifetime. That is
the entire mechanism: the connection is never used for queries after the
initial read, it just exists. With ``uvicorn --workers 4`` each worker holds
its own, which is harmless (four readers, no transaction left open, so
checkpoints are not blocked).

⚠ Do not "tidy" this into a connection that is opened and closed around a
query. Its only purpose is to stay open.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from app.core.logging_config import get_logger

logger = get_logger(__name__)

_held: list[sqlite3.Connection] = []


def hold_wal_sidecars(paths: Iterable[Path]) -> None:
    """Open (and keep) one read-only connection per existing database file.

    A single SELECT is issued so SQLite actually materialises the sidecars —
    ``connect()`` alone is lazy and creates nothing. ``query_only`` makes the
    held connection incapable of writing even by accident. Missing files are
    skipped (dev checkouts may not have every database yet); failures are
    logged, never raised — a missing keepalive degrades one AI tool, it must
    not stop the API from starting.
    """
    for path in paths:
        if not path.exists():
            logger.info("WAL keepalive skipped, file absent: %s", path)
            continue
        try:
            conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
            conn.execute("PRAGMA query_only = 1")
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
            _held.append(conn)
            logger.info("WAL keepalive holding %s", path.name)
        except sqlite3.Error:
            logger.warning("WAL keepalive could not open %s", path, exc_info=True)
