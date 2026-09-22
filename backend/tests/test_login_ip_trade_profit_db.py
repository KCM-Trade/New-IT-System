"""OPT-0063 Phase 2 — trade_ip_pnl DB layer (login_ip_orders.db).

Pins the two behaviors the nightly reconcile relies on:
- replace_trade_ip_pnl_for_date is a true rewrite: a re-run also removes rows
  the new run no longer produces (bare INSERT OR REPLACE would leave them);
- the 400-day cleanup sweep touches only rows past the retention window.

Same ``_DB_PATH`` monkeypatch pattern as test_login_ip_orders_db.py.
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


def _row(server="MT5", deal_ref="36606946", close_date="2026-09-18", account=67043240,
         position="40926216", open_ip="58.10.224.247", close_ip="58.10.224.247",
         user_id=169139, ib_id=1001, symbol="XAUUSD", lots=0.02, profit=12.5,
         hold_sec=390, open_date="2026-09-18", reason=16, no_ip_cause=None):
    # Column order = login_ip_orders_db._TRADE_IP_PNL_COLUMNS.
    return (server, deal_ref, close_date, account, position, open_ip, close_ip,
            user_id, ib_id, symbol, lots, profit, hold_sec, open_date, reason,
            no_ip_cause)


def test_replace_insert_and_read_back(db):
    assert db.replace_trade_ip_pnl_for_date("2026-09-18", [_row(), _row(deal_ref="36606947")]) == 2
    rows = db.get_trade_ip_pnl_for_date("2026-09-18")
    assert len(rows) == 2
    assert rows[0]["open_ip"] == "58.10.224.247"
    assert rows[0]["reconciled_at"]  # filled by the DB default


def test_replace_rerun_rewrites_and_drops_stale_rows(db):
    """A re-run that produces FEWER rows must delete the stale remainder —
    otherwise a fixed filter would double-count the day forever."""
    db.replace_trade_ip_pnl_for_date("2026-09-18", [_row(), _row(deal_ref="1")])
    db.replace_trade_ip_pnl_for_date("2026-09-18", [_row(deal_ref="2")])
    rows = db.get_trade_ip_pnl_for_date("2026-09-18")
    assert [r["deal_ref"] for r in rows] == ["2"]


def test_replace_does_not_touch_other_days(db):
    db.replace_trade_ip_pnl_for_date("2026-09-17", [_row(deal_ref="d17", close_date="2026-09-17")])
    db.replace_trade_ip_pnl_for_date("2026-09-18", [_row(deal_ref="d18", close_date="2026-09-18")])
    db.replace_trade_ip_pnl_for_date("2026-09-18", [])
    assert db.get_trade_ip_pnl_for_date("2026-09-18") == []
    assert len(db.get_trade_ip_pnl_for_date("2026-09-17")) == 1


def test_get_trade_ip_pnl_dates(db):
    db.replace_trade_ip_pnl_for_date("2026-09-17", [_row(deal_ref="d17", close_date="2026-09-17")])
    db.replace_trade_ip_pnl_for_date("2026-09-18", [_row(deal_ref="d18", close_date="2026-09-18")])
    assert db.get_trade_ip_pnl_dates() == {"2026-09-17", "2026-09-18"}


def test_cleanup_old_trade_ip_pnl_respects_window(db):
    today = dt.datetime.now().strftime("%Y-%m-%d")
    old = (dt.datetime.now() - dt.timedelta(days=401)).strftime("%Y-%m-%d")
    edge = (dt.datetime.now() - dt.timedelta(days=399)).strftime("%Y-%m-%d")
    db.replace_trade_ip_pnl_for_date(today, [_row(deal_ref="t", close_date=today)])
    db.replace_trade_ip_pnl_for_date(old, [_row(deal_ref="o", close_date=old)])
    db.replace_trade_ip_pnl_for_date(edge, [_row(deal_ref="e", close_date=edge)])
    assert db.cleanup_old_trade_ip_pnl(days=400) == 1
    assert db.get_trade_ip_pnl_for_date(old) == []
    assert len(db.get_trade_ip_pnl_for_date(edge)) == 1


def test_get_order_ips_by_refs_chunks_and_matches(db):
    db.upsert_order_ips([
        ("20260918", "MT5", 67043240, 40659198, "58.10.224.247", "00:05:09.218", "performed", "buy", 0.02, "BTCUSD"),
        ("20260918", "MT4", 8520962, 23106530, "39.144.59.59", "01:01:16.424", "order", "buy", 0.6, "XAUUSD"),
    ])
    out = db.get_order_ips_by_refs({"MT5": [40659198, 1], "MT4": [23106530]})
    assert out == {("MT5", 40659198): "58.10.224.247", ("MT4", 23106530): "39.144.59.59"}


def test_get_parse_run_server_days(db):
    db.record_parse_runs([("20260918", "MT5", 100, 5), ("20260918", "MT4", 200, 6)])
    assert db.get_parse_run_server_days() == {("20260918", "MT5"), ("20260918", "MT4")}
