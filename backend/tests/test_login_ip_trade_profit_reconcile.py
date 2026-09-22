"""OPT-0063 Phase 2 — the nightly reconcile, with the MySQL slave faked.

The five slave reads are injected via ``Pullers`` so no test here needs MySQL;
``order_ip`` / ``order_ip_parse_runs`` / ``trade_ip_pnl`` live in a tmp SQLite
(the usual ``_DB_PATH`` monkeypatch).

What is pinned:
- MT4 direct ticket match, and the 'from #N' chain walk (depth 1, depth 2 via
  the comments table, and the exhausted-chain -> partial_remainder fallthrough);
- MT5: open IP via PositionID, close IP via the close order ticket, and the
  multi-deal close — each deal lands its own row and profits sum exactly;
- every no_ip_cause class;
- re-running the same day rewrites instead of duplicating.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.services import login_ip_trade_profit_service as svc


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from app.core import login_ip_orders_db

    monkeypatch.setattr(login_ip_orders_db, "_DB_PATH", tmp_path / "login_ip_orders.db")
    login_ip_orders_db.init_login_ip_orders_db()
    # A full parse-run set for the days the fixtures open on, so only the
    # journal_incomplete test sees a missing day.
    login_ip_orders_db.record_parse_runs(
        (day, server, 1000, 10)
        for day in ("20260916", "20260917", "20260918")
        for server in ("MT4", "MT5", "MT4_Live2")
    )
    return login_ip_orders_db


def _mt4_row(ticket, sid=1, login=8520962, user_id=101, comment="",
             open_date="2026-09-17", profit=100.0, grp="KCM\\4SD_L10"):
    return {
        "deal_ref": f"{sid}-{ticket}", "sid": sid, "account_id": login,
        "ticket": ticket, "symbol": "XAUUSD", "lots": 0.6, "profit_usd": profit,
        "open_date": open_date, "hold_sec": 3600, "comment": comment,
        "user_id": user_id, "grp": grp,
    }


def _mt5_row(deal, login=67043240, position=40659198, order=None, profit=10.0,
             close_time=dt.datetime(2026, 9, 18, 3, 0), user_id=201,
             grp="KCM\\S5_L10"):
    return {
        "deal_ref": deal, "account_id": login,
        "close_order": order if order is not None else deal + 100,
        "position_id": position, "entry": 1, "symbol": "BTCUSD", "lots": 0.02,
        "profit_usd": profit, "close_time": close_time, "user_id": user_id,
        "grp": grp,
    }


def _opens(rows, open_time=dt.datetime(2026, 9, 17, 10, 0), reason=0):
    """Fake mt5 open-deal map: {(login, position_id): info}."""
    return {
        (r["account_id"], r["position_id"]): {
            "open_time": open_time, "reason": reason, "open_order": r["position_id"],
        }
        for r in rows
    }


def _pullers(mt4_rows=(), mt5_rows=(), comments=None, ib_map=None):
    mt5_rows = list(mt5_rows)
    return svc.Pullers(
        mt4_closed=lambda conn, day_iso: list(mt4_rows),
        mt5_closes=lambda conn, day: mt5_rows,
        mt5_opens=lambda conn, keys: _opens(mt5_rows),
        mt4_comments=lambda conn, ticksids: {t: (comments or {}).get(t, "") for t in ticksids},
        ib_map=lambda conn, user_ids: ib_map or {},
    )


def _order_row(server, account, ref, ip, trade_date="20260917"):
    return (trade_date, server, account, ref, ip, "10:00:00.000", "performed", "buy", 0.02, "XAUUSD")


# ---------------------------------------------------------------------------
# MT4
# ---------------------------------------------------------------------------


def test_mt4_direct_ticket_match(db):
    db.upsert_order_ips([_order_row("MT4", 8520962, 23106530, "39.144.59.59")])
    out = svc.reconcile_trade_ip_pnl(
        "20260918", pullers=_pullers(mt4_rows=[_mt4_row(23106530)])
    )
    rows = db.get_trade_ip_pnl_for_date("2026-09-18")
    assert len(rows) == 1
    assert rows[0]["server"] == "MT4"
    assert rows[0]["deal_ref"] == "1-23106530"
    assert rows[0]["open_ip"] == "39.144.59.59"
    assert rows[0]["no_ip_cause"] is None
    assert out["with_ip"] == 1


def test_mt4_from_chain_depth1(db):
    """Remainder ticket 100 (comment 'from #99') inherits ticket 99's IP."""
    db.upsert_order_ips([_order_row("MT4", 8520962, 99, "1.2.3.4")])
    svc.reconcile_trade_ip_pnl(
        "20260918",
        pullers=_pullers(mt4_rows=[_mt4_row(100, comment="from #99")]),
    )
    row = db.get_trade_ip_pnl_for_date("2026-09-18")[0]
    assert row["open_ip"] == "1.2.3.4"
    assert row["no_ip_cause"] is None


def test_mt4_from_chain_depth2_via_comments(db):
    """The chain continues through mt4_trades comments: 100 -> 99 -> 98."""
    db.upsert_order_ips([_order_row("MT4", 8520962, 98, "5.6.7.8")])
    svc.reconcile_trade_ip_pnl(
        "20260918",
        pullers=_pullers(
            mt4_rows=[_mt4_row(100, comment="from #99")],
            comments={"1-99": "from #98[Expiration]"},
        ),
    )
    row = db.get_trade_ip_pnl_for_date("2026-09-18")[0]
    assert row["open_ip"] == "5.6.7.8"


def test_mt4_chain_exhausted_is_partial_remainder(db):
    svc.reconcile_trade_ip_pnl(
        "20260918",
        pullers=_pullers(mt4_rows=[_mt4_row(100, comment="from #99")]),
    )
    row = db.get_trade_ip_pnl_for_date("2026-09-18")[0]
    assert row["open_ip"] is None
    assert row["no_ip_cause"] == "partial_remainder"


# ---------------------------------------------------------------------------
# MT5
# ---------------------------------------------------------------------------


def test_mt5_open_ip_via_position_id_and_close_ip_via_close_order(db):
    """The open order ticket IS the PositionID; the close deal's own Order
    ticket yields close_ip."""
    db.upsert_order_ips([
        _order_row("MT5", 67043240, 40659198, "10.0.0.1"),   # open (performed)
        _order_row("MT5", 67043240, 36607046, "10.0.0.2"),   # close order
    ])
    svc.reconcile_trade_ip_pnl(
        "20260918",
        pullers=_pullers(mt5_rows=[_mt5_row(36606946, position=40659198, order=36607046)]),
    )
    row = db.get_trade_ip_pnl_for_date("2026-09-18")[0]
    assert row["open_ip"] == "10.0.0.1"
    assert row["close_ip"] == "10.0.0.2"
    assert row["position_ref"] == "40659198"
    assert row["reason"] == 0
    assert row["hold_sec"] == int(dt.timedelta(hours=17).total_seconds())


def test_mt5_multi_deal_close_lands_one_row_per_deal(db):
    """A position closed in two deals on two days: each deal its own row,
    and the two profits sum to the position total (no double count, no loss)."""
    db.upsert_order_ips([_order_row("MT5", 67043240, 40659198, "10.0.0.1")])
    pullers_day1 = _pullers(mt5_rows=[_mt5_row(36606946, profit=7.0)])
    pullers_day2 = _pullers(mt5_rows=[
        _mt5_row(36608001, profit=3.0, close_time=dt.datetime(2026, 9, 19, 3, 0))
    ])
    svc.reconcile_trade_ip_pnl("20260918", pullers=pullers_day1)
    svc.reconcile_trade_ip_pnl("20260919", pullers=pullers_day2)

    day1 = db.get_trade_ip_pnl_for_date("2026-09-18")
    day2 = db.get_trade_ip_pnl_for_date("2026-09-19")
    assert [r["deal_ref"] for r in day1] == ["36606946"]
    assert [r["deal_ref"] for r in day2] == ["36608001"]
    assert day1[0]["position_ref"] == day2[0]["position_ref"] == "40659198"
    assert day1[0]["open_ip"] == day2[0]["open_ip"] == "10.0.0.1"
    assert day1[0]["profit_usd"] + day2[0]["profit_usd"] == 10.0


# ---------------------------------------------------------------------------
# no_ip_cause classes
# ---------------------------------------------------------------------------


def _single_unmatched_row(db, **overrides):
    svc.reconcile_trade_ip_pnl("20260918", pullers=_pullers(mt4_rows=[_mt4_row(100, **overrides)]))
    return db.get_trade_ip_pnl_for_date("2026-09-18")[0]


def test_no_ip_pre_golive(db):
    row = _single_unmatched_row(db, open_date="2026-09-10")
    assert row["no_ip_cause"] == "pre_golive"


def test_no_ip_journal_incomplete(db):
    """Opened on a day with no parse run for that server (log never parsed)."""
    row = _single_unmatched_row(db, open_date="2026-09-15")  # no parse run fixture
    assert row["no_ip_cause"] == "journal_incomplete"


def test_no_ip_bridge_group(db):
    row = _single_unmatched_row(db, grp="KCM\\5LS_L1")
    assert row["no_ip_cause"] == "bridge_group"


def test_no_ip_server_initiated_residual(db):
    row = _single_unmatched_row(db)
    assert row["no_ip_cause"] == "server_initiated"


# ---------------------------------------------------------------------------
# idempotency + summary
# ---------------------------------------------------------------------------


def test_rerun_rewrites_the_day(db):
    svc.reconcile_trade_ip_pnl(
        "20260918", pullers=_pullers(mt4_rows=[_mt4_row(100), _mt4_row(101)])
    )
    svc.reconcile_trade_ip_pnl("20260918", pullers=_pullers(mt4_rows=[_mt4_row(100)]))
    rows = db.get_trade_ip_pnl_for_date("2026-09-18")
    assert [r["deal_ref"] for r in rows] == ["1-100"]


def test_summary_counts(db):
    db.upsert_order_ips([_order_row("MT4", 8520962, 100, "1.2.3.4")])
    out = svc.reconcile_trade_ip_pnl(
        "20260918",
        pullers=_pullers(mt4_rows=[_mt4_row(100), _mt4_row(101)], ib_map={101: 555}),
    )
    assert out["close_date"] == "2026-09-18"
    assert out["mt4_rows"] == 2
    assert out["rows_written"] == 2
    assert out["with_ip"] == 1
    assert out["no_ip_by_cause"] == {"server_initiated": 1}
    rows = {r["deal_ref"]: r for r in db.get_trade_ip_pnl_for_date("2026-09-18")}
    assert rows["1-101"]["ib_id"] == 555


def test_decimal_profit_and_lots_are_coerced(db):
    """MySQL DECIMAL arrives as decimal.Decimal, which sqlite3 cannot bind.

    Real-slave regression (2026-09-22 backfill): every fake in this file used
    floats, so the first real run died on 'Error binding parameter 11'.
    """
    from decimal import Decimal

    row = _mt4_row(100)
    row["lots"] = Decimal("0.60")
    row["profit_usd"] = Decimal("-123.45")
    svc.reconcile_trade_ip_pnl("20260918", pullers=_pullers(mt4_rows=[row]))
    got = db.get_trade_ip_pnl_for_date("2026-09-18")[0]
    assert got["lots"] == 0.6 and got["profit_usd"] == -123.45
    assert isinstance(got["profit_usd"], float)
