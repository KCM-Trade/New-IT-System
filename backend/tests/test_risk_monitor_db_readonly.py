"""The read-only entry points OPT-0064 added to risk_monitor_db / login_ip_orders_db.

  * ``query_alert_events(logins=[...])`` is the IN-list form of ``login`` and
    refuses both at once;
  * ``conn=`` lets a caller run the SAME query on a connection it owns —
    the ai-agent container passes ``open_readonly()``;
  * ``count_alert_events_by_rule`` counts over the same filter, so a capped
    page and its per-rule totals cannot disagree;
  * ``open_readonly()`` cannot write.

The read-only connection needs the WAL sidecars to exist: in production the
main API keeps them alive (core/sqlite_wal_keepalive.py); here a writer
connection is held open for the same reason.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def rmdb(tmp_path, monkeypatch):
    from app.core import risk_monitor_db as rmdb_mod

    monkeypatch.setattr(rmdb_mod, "_DB_PATH", tmp_path / "risk_monitor_test.db")
    rmdb_mod.init_risk_monitor_db()
    keepalive = sqlite3.connect(str(rmdb_mod._DB_PATH))
    keepalive.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
    with rmdb_mod.get_risk_monitor_db() as conn:
        for i, (server, login, rule_id) in enumerate(
            [("MT4_Live", 1, 131), ("MT4_Live", 1, 131), ("MT4_Live", 2, 71), ("MT5", 1, 131), ("MT4_Live", 3, 131)]
        ):
            conn.execute(
                "INSERT INTO alert_events (scan_batch_id, scanned_at, rule_id, rule_label, server, login, "
                "symbol, order_count, total_lots) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, f"2026-09-2{i}T00:00:00Z", rule_id, "Rule", server, login, "XAUUSD", 1, 0.1),
            )
    yield rmdb_mod
    keepalive.close()


SINCE, UNTIL = "2026-09-01T00:00:00Z", "2026-10-01T00:00:00Z"


def test_logins_in_list_matches_only_those_accounts(rmdb):
    rows, total = rmdb.query_alert_events(SINCE, UNTIL, server="MT4_Live", logins=[1, 2])
    assert total == 3 and {r["login"] for r in rows} == {1, 2}
    # Single-login form still works and agrees with the list form.
    _, single = rmdb.query_alert_events(SINCE, UNTIL, server="MT4_Live", login=1)
    assert single == 2


def test_login_and_logins_together_is_an_error(rmdb):
    with pytest.raises(ValueError):
        rmdb.query_alert_events(SINCE, UNTIL, server="MT4_Live", login=1, logins=[2])


def test_count_by_rule_agrees_with_the_page_filter(rmdb):
    counts = rmdb.count_alert_events_by_rule(SINCE, UNTIL, "MT4_Live", logins=[1, 2, 3])
    assert counts == {131: 3, 71: 1}
    _, total = rmdb.query_alert_events(SINCE, UNTIL, server="MT4_Live", logins=[1, 2, 3], limit=1)
    assert total == sum(counts.values())


def test_readonly_connection_runs_the_same_query_and_cannot_write(rmdb):
    conn = rmdb.open_readonly()
    try:
        rows, total = rmdb.query_alert_events(SINCE, UNTIL, server="MT5", logins=[1], conn=conn)
        assert total == 1 and rows[0]["server"] == "MT5"
        assert rmdb.count_alert_events_by_rule(SINCE, UNTIL, "MT5", logins=[1], conn=conn) == {131: 1}
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM alert_events")
    finally:
        conn.close()


def test_login_ip_orders_open_readonly_cannot_write(tmp_path, monkeypatch):
    from app.core import login_ip_orders_db as lio

    monkeypatch.setattr(lio, "_DB_PATH", tmp_path / "orders_test.db")
    lio.init_login_ip_orders_db()
    keepalive = sqlite3.connect(str(lio._DB_PATH))
    keepalive.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
    try:
        conn = lio.open_readonly()
        try:
            assert conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] > 0
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE x (a)")
        finally:
            conn.close()
    finally:
        keepalive.close()
