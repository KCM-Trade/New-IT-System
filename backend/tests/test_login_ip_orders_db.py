"""OPT-0063 Phase 1 — login_ip_orders.db layer.

Pins the three behaviors the daily job relies on:
- upsert is idempotent per (server_name, order_ref): re-running the same day
  REPLACES instead of duplicating;
- the same ticket on a different server is a different row (ticket spaces are
  per-server);
- cleanup sweeps rows past the retention window only.

Same ``_DB_PATH`` monkeypatch pattern as the other login-ip tests.
"""

from __future__ import annotations

import datetime as dt

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from app.core import login_ip_orders_db

    monkeypatch.setattr(login_ip_orders_db, "_DB_PATH", tmp_path / "login_ip_orders.db")
    login_ip_orders_db.init_login_ip_orders_db()
    return login_ip_orders_db


def _row(trade_date="20260918", server="MT5", account=67043240, ref=40659198,
         ip="58.10.224.247", ts="00:05:09.218", kind="performed", cmd="buy",
         lots=0.02, symbol="BTCUSD"):
    return (trade_date, server, account, ref, ip, ts, kind, cmd, lots, symbol)


# ---------------------------------------------------------------------------
# upsert_order_ips
# ---------------------------------------------------------------------------


def test_upsert_insert_and_read_back(db):
    assert db.upsert_order_ips([_row(), _row(ref=40659199, cmd="sell")]) == 2
    rows = db.get_order_ips_for_date("20260918")
    assert len(rows) == 2
    by_ref = {r["order_ref"]: r for r in rows}
    assert by_ref[40659198]["ip_address"] == "58.10.224.247"
    assert by_ref[40659198]["event_kind"] == "performed"
    assert by_ref[40659198]["lots"] == 0.02
    assert by_ref[40659199]["cmd"] == "sell"


def test_upsert_rerun_same_day_replaces_not_duplicates(db):
    """The UNIQUE(server_name, order_ref) contract: a re-parse that read a
    fuller log wins over the stored row."""
    db.upsert_order_ips([_row(ip="1.1.1.1")])
    db.upsert_order_ips([_row(ip="2.2.2.2")])
    rows = db.get_order_ips_for_date("20260918")
    assert len(rows) == 1
    assert rows[0]["ip_address"] == "2.2.2.2"


def test_same_ticket_on_different_servers_coexists(db):
    """Ticket spaces are per-server: MT4 ticket 100 and MT5 order 100 are
    unrelated rows."""
    db.upsert_order_ips([_row(server="MT4", ref=100), _row(server="MT5", ref=100)])
    rows = db.get_order_ips_for_date("20260918")
    assert {r["server_name"] for r in rows} == {"MT4", "MT5"}


def test_upsert_empty_list_is_noop(db):
    assert db.upsert_order_ips([]) == 0


def test_get_order_ips_for_date_filters_server(db):
    db.upsert_order_ips([_row(server="MT4", ref=1), _row(server="MT5", ref=2)])
    rows = db.get_order_ips_for_date("20260918", server_name="MT5")
    assert [r["order_ref"] for r in rows] == [2]


# ---------------------------------------------------------------------------
# order_ip_parse_runs
# ---------------------------------------------------------------------------


def test_record_parse_runs_upserts_and_reads_back(db):
    db.record_parse_runs([("20260918", "MT5", 8665788, 100443)])
    db.record_parse_runs([("20260918", "MT4", 900000, 9518)])
    rows = db.get_parse_runs("20260918")
    assert [(r["server_name"], r["rows_written"]) for r in rows] == [
        ("MT4", 9518),
        ("MT5", 100443),
    ]
    assert rows[0]["lines_scanned"] == 900000
    assert rows[0]["parsed_at"]  # audit timestamp filled by the DB default


def test_record_parse_runs_rerun_overwrites(db):
    """A truncated-log re-run must replace the first (incomplete) audit row."""
    db.record_parse_runs([("20260918", "MT5", 100, 5)])
    db.record_parse_runs([("20260918", "MT5", 8665788, 100443)])
    rows = db.get_parse_runs("20260918")
    assert len(rows) == 1
    assert rows[0]["lines_scanned"] == 8665788
    assert rows[0]["rows_written"] == 100443


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


def test_cleanup_old_order_ip_respects_window(db):
    today = dt.datetime.now().strftime("%Y%m%d")
    old = (dt.datetime.now() - dt.timedelta(days=121)).strftime("%Y%m%d")
    edge = (dt.datetime.now() - dt.timedelta(days=119)).strftime("%Y%m%d")
    db.upsert_order_ips(
        [_row(trade_date=today, ref=1), _row(trade_date=old, ref=2), _row(trade_date=edge, ref=3)]
    )
    assert db.cleanup_old_order_ip(days=120) == 1
    remaining = {r["trade_date"] for r in db.get_order_ips_for_date(today)}
    assert remaining == {today}
    assert db.get_order_ips_for_date(old) == []
    assert len(db.get_order_ips_for_date(edge)) == 1


def test_cleanup_old_parse_runs_respects_window(db):
    recent = dt.datetime.now().strftime("%Y%m%d")
    old = (dt.datetime.now() - dt.timedelta(days=401)).strftime("%Y%m%d")
    db.record_parse_runs([(recent, "MT5", 10, 1), (old, "MT5", 10, 1)])
    assert db.cleanup_old_parse_runs(days=400) == 1
    assert [r["trade_date"] for r in db.get_parse_runs(recent)] == [recent]
    assert db.get_parse_runs(old) == []
