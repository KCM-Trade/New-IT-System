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


# ── OPT-0066 R1–R4: user_id opt-in, servers / user_ids filters, get_alerts_by_ids(conn=), aggregate ──
#
# Seed (all rows inside [SINCE, UNTIL)):
#   id  server    login user  rule  scanned_at   lots  equity_per_lot  profit  detail
#   1   MT4_Live  1     100   101   09-20        1.0   50              -
#   2   MT4_Live  1     100   101   09-21        2.0   30              -
#   3   MT4_Live  2     100   102   09-21        0.5   80              -
#   4   MT5       1     200   101   09-22        3.0   10              -
#   5   MT4_Live  3     NULL  101   09-22        0.1   5               -
#   6   MT4_Live  4     300   131   09-23        0.2   -               -       ir.trading_day 2026-09-23, return_pct 150
#   7   MT5       9     300   61    09-23        0.3   -               500.0


@pytest.fixture
def rm2(tmp_path, monkeypatch):
    from app.core import risk_monitor_db as rmdb_mod

    monkeypatch.setattr(rmdb_mod, "_DB_PATH", tmp_path / "risk_monitor_agg.db")
    rmdb_mod.init_risk_monitor_db()
    keepalive = sqlite3.connect(str(rmdb_mod._DB_PATH))
    keepalive.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
    seed = [
        (1, "MT4_Live", 1, 100, 101, "2026-09-20T01:00:00Z", 1.0, 50.0, None),
        (2, "MT4_Live", 1, 100, 101, "2026-09-21T01:00:00Z", 2.0, 30.0, None),
        (3, "MT4_Live", 2, 100, 102, "2026-09-21T02:00:00Z", 0.5, 80.0, None),
        (4, "MT5", 1, 200, 101, "2026-09-22T01:00:00Z", 3.0, 10.0, None),
        (5, "MT4_Live", 3, None, 101, "2026-09-22T02:00:00Z", 0.1, 5.0, None),
        (6, "MT4_Live", 4, 300, 131, "2026-09-23T01:00:00Z", 0.2, None, None),
        (7, "MT5", 9, 300, 61, "2026-09-23T02:00:00Z", 0.3, None, 500.0),
    ]
    with rmdb_mod.get_risk_monitor_db() as conn:
        for (id_, server, login, uid, rule, at, lots, epl, profit) in seed:
            conn.execute(
                "INSERT INTO alert_events (id, scan_batch_id, scanned_at, rule_id, rule_label, server, login, "
                "symbol, order_count, total_lots, equity_per_lot, total_profit_usd, user_id) "
                "VALUES (?, 1, ?, ?, ?, ?, ?, 'XAUUSD', 1, ?, ?, ?, ?)",
                (id_, at, rule, f"Rule {rule}", server, login, lots, epl, profit, uid),
            )
        conn.execute(
            "INSERT INTO alert_intraday_return_detail (id, trading_day, return_pct) VALUES (6, '2026-09-23', 150.0)"
        )
    yield rmdb_mod
    keepalive.close()


def test_r1_user_id_is_opt_in_and_the_page_shape_is_unchanged(rm2):
    default_rows, _ = rm2.query_alert_events(SINCE, UNTIL)
    off_rows, _ = rm2.query_alert_events(SINCE, UNTIL, include_user_id=False)
    on_rows, _ = rm2.query_alert_events(SINCE, UNTIL, include_user_id=True)
    assert all("user_id" not in r for r in default_rows)
    assert [sorted(r) for r in default_rows] == [sorted(r) for r in off_rows]
    for d, o in zip(default_rows, on_rows):
        assert set(o) - set(d) == {"user_id"}
    assert {r["id"]: r["user_id"] for r in on_rows} == {1: 100, 2: 100, 3: 100, 4: 200, 5: None, 6: 300, 7: 300}


def test_r2_user_ids_filter(rm2):
    rows, total = rm2.query_alert_events(SINCE, UNTIL, user_ids=[100], include_user_id=True)
    assert total == 3 and {r["id"] for r in rows} == {1, 2, 3}
    counts = rm2.count_alert_events_by_rule(SINCE, UNTIL, user_ids=[100])
    assert counts == {101: 2, 102: 1}


def test_r2_servers_filter_and_server_plus_servers_is_an_error(rm2):
    rows, total = rm2.query_alert_events(SINCE, UNTIL, servers=["MT5"])
    assert total == 2 and {r["id"] for r in rows} == {4, 7}
    rows, total = rm2.query_alert_events(SINCE, UNTIL, servers=["MT5", "MT4_Live"])
    assert total == 7
    with pytest.raises(ValueError):
        rm2.query_alert_events(SINCE, UNTIL, server="MT5", servers=["MT5"])


def test_r2_count_by_rule_takes_band_symbol_and_servers(rm2):
    assert rm2.count_alert_events_by_rule(SINCE, UNTIL, rule_id_min=101, rule_id_max=110) == {101: 4, 102: 1}
    assert rm2.count_alert_events_by_rule(SINCE, UNTIL, servers=["MT5"], rule_id_min=101, rule_id_max=110) == {101: 1}
    assert rm2.count_alert_events_by_rule(SINCE, UNTIL, symbol="EURUSD") == {}
    # Same filter as the page query → same total.
    _, total = rm2.query_alert_events(SINCE, UNTIL, rule_id_min=101, rule_id_max=110, limit=1)
    assert total == 5


def test_r3_get_alerts_by_ids_on_a_readonly_connection(rm2):
    conn = rm2.open_readonly()
    try:
        rows = rm2.get_alerts_by_ids([2, 5], conn=conn, include_user_id=True)
        assert {r["id"]: r["user_id"] for r in rows} == {2: 100, 5: None}
        plain = rm2.get_alerts_by_ids([2], conn=conn)
        assert "user_id" not in plain[0]
        assert rm2.get_alerts_by_ids([], conn=conn) == []
    finally:
        conn.close()


def _agg(rm, **kw):
    kw.setdefault("rule_id_min", 101)
    kw.setdefault("rule_id_max", 110)
    return rm.aggregate_alert_events(SINCE, UNTIL, **kw)


def test_r4_account_grouping_matches_a_hand_written_group_by(rm2):
    out = _agg(rm2, group_by="account", sort="alerts", limit=50)
    assert out["groups_total"] == 4
    with rm2.get_risk_monitor_db() as c:
        expected = {
            (r[0], r[1]): (r[2], r[3])
            for r in c.execute(
                "SELECT server, login, COUNT(*), SUM(total_lots) FROM alert_events "
                "WHERE rule_id BETWEEN 101 AND 110 GROUP BY server, login"
            ).fetchall()
        }
    got = {(g["server"], g["login"]): (g["alerts"], g["lots"]) for g in out["groups"]}
    assert set(got) == set(expected)
    for k, (n, lots) in expected.items():
        assert got[k][0] == n and got[k][1] == pytest.approx(lots)
    top = out["groups"][0]
    assert (top["server"], top["login"]) == ("MT4_Live", 1)
    assert top["user_id"] == 100
    assert top["rule_ids"] == [101]
    assert top["first_fired_at"] == "2026-09-20T01:00:00Z"
    assert top["last_fired_at"] == "2026-09-21T01:00:00Z"
    assert top["profit"] is None  # every total_profit_usd is NULL
    assert set(top["sample_alert_ids"]) == {1, 2} and len(top["sample_alert_ids"]) <= 3
    assert top["sample_alert_ids"][0] == 2  # newest first


def test_r4_metric_sort_follows_the_metric_direction(rm2):
    out = _agg(rm2, group_by="account", sort="metric", metric="equity_per_lot", limit=50)
    assert [(g["server"], g["login"]) for g in out["groups"]] == [
        ("MT4_Live", 3), ("MT5", 1), ("MT4_Live", 1), ("MT4_Live", 2)
    ]
    assert [g["metric"] for g in out["groups"]] == [5.0, 10.0, 30.0, 80.0]


def test_r4_lots_sort_ties_break_on_alert_count(rm2):
    out = _agg(rm2, group_by="account", sort="lots", limit=2)
    # MT4_Live-1 (2 alerts, 3.0 lots) and MT5-1 (1 alert, 3.0 lots) tie on lots.
    assert [(g["server"], g["login"]) for g in out["groups"]] == [("MT4_Live", 1), ("MT5", 1)]
    assert out["groups_total"] == 4  # limit does not change the total


def test_r4_client_grouping_excludes_null_user_id_and_counts_it(rm2):
    out = _agg(rm2, group_by="client", sort="alerts")
    assert out["groups_total"] == 2
    assert out["alerts_without_user_id"] == 1
    by_uid = {g["user_id"]: g for g in out["groups"]}
    assert set(by_uid) == {100, 200}
    assert by_uid[100]["alerts"] == 3
    assert by_uid[100]["rule_ids"] == [101, 102]
    assert by_uid[100]["accounts"] == [{"server": "MT4_Live", "login": 1}, {"server": "MT4_Live", "login": 2}]
    assert out["groups"][0]["user_id"] == 100


def test_r4_rule_grouping(rm2):
    out = _agg(rm2, group_by="rule")
    rows = {g["rule_id"]: g for g in out["groups"]}
    assert rows[101]["alerts"] == 4 and rows[101]["accounts"] == 3 and rows[101]["clients"] == 2
    assert rows[102]["alerts"] == 1 and rows[102]["accounts"] == 1 and rows[102]["clients"] == 1
    assert rows[101]["rule_label"] == "Rule 101"


def test_r4_filters_are_the_page_filters(rm2):
    out = _agg(rm2, group_by="account", servers=["MT5"])
    assert [(g["server"], g["login"]) for g in out["groups"]] == [("MT5", 1)]
    out = _agg(rm2, group_by="client", user_ids=[200])
    assert [g["user_id"] for g in out["groups"]] == [200]


def test_r4_trading_day_time_field_and_return_pct_metric(rm2):
    out = rm2.aggregate_alert_events(
        "2026-09-23T00:00:00Z", "2026-09-23T00:00:00Z", group_by="account",
        rule_id_min=131, rule_id_max=140, time_field="trading_day", sort="metric", metric="return_pct",
    )
    assert [(g["server"], g["login"], g["metric"]) for g in out["groups"]] == [("MT4_Live", 4, 150.0)]


def test_r4_profit_sort_puts_nulls_last(rm2):
    out = rm2.aggregate_alert_events(SINCE, UNTIL, group_by="account", sort="profit")
    assert (out["groups"][0]["server"], out["groups"][0]["login"]) == ("MT5", 9)
    assert out["groups"][0]["profit"] == 500.0


@pytest.mark.parametrize(
    "kw",
    [
        {"group_by": "symbol"},
        {"group_by": "account", "sort": "nope"},
        {"group_by": "account", "sort": "metric"},
        {"group_by": "account", "sort": "metric", "metric": "DROP TABLE"},
    ],
)
def test_r4_rejects_anything_outside_the_whitelists(rm2, kw):
    with pytest.raises(ValueError):
        rm2.aggregate_alert_events(SINCE, UNTIL, **kw)


def test_r4_runs_on_a_readonly_connection(rm2):
    conn = rm2.open_readonly()
    try:
        out = _agg(rm2, group_by="rule", conn=conn)
        assert {g["rule_id"] for g in out["groups"]} == {101, 102}
    finally:
        conn.close()


def test_r4_agg_constants_are_the_contract():
    from app.core import risk_monitor_db as rm

    assert set(rm.AGG_GROUP_BY) == {"account", "client", "rule"}
    assert set(rm.AGG_SORTS) == {"alerts", "lots", "profit", "metric"}
    assert set(rm.AGG_METRICS) >= {
        "order_count", "min_hold_sec", "total_profit_usd", "net_usd", "total_lots",
        "equity_per_lot", "lot_ratio_mg", "return_pct",
    }
    assert rm.AGG_METRICS["equity_per_lot"][1] == "asc"
    assert rm.AGG_METRICS["min_hold_sec"][1] == "asc"
    assert rm.AGG_METRICS["return_pct"][1] == "desc"
