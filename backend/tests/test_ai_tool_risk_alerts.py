"""``get_risk_alerts`` (docs/ai-agent/11 §2.1) against a real temp risk_monitor.db.

Only the MySQL cid lookup is stubbed; the SQLite side runs the page's own
filter builder + aggregate on a read-only connection, as in the container.

Pinned: tab → band + time column (intraday-return by trading_day); group_by
alert / account / client / rule; totals independent of row caps; allow-list
projection (no names / IP lists / comments; gap 71 C leg scoped on its own);
restricted scope recomputes everything over in-scope clients, masks NULL
user_id, applies top_n after the filter, and frozenset() masks all; argument
limits; verdict is None.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

ra = importlib.import_module("app.ai_agent.tools.risk_alerts")
from app.ai_agent.tools.common import CallerCtx
from app.ai_agent.tools.risk_bands import BAND_FIELDS

CIDS = {100: 0, 200: 1, 300: 1}  # 100 = CN, 200/300 = Global
DAY = {"from": "2026-09-23", "to": "2026-09-23"}
WEEK = {"from": "2026-09-17", "to": "2026-09-23"}

ALERT_ROW_KEYS = {"alert_id", "rule_id", "rule_name", "rule_label", "tab", "fired_at", "login_sid", "client_id",
                  "symbol", "summary", "metrics", "trading_day", "c_leg_masked_by_scope"}
PII_STRINGS = ("Alice Leg", "Bob Leg", "10.1.2.3", "10.9.9.9", "secret remark", "Carol Profit", "Z1234")


def run(coro):
    return asyncio.run(coro)


def ctx(scope=None) -> CallerCtx:
    return CallerCtx(user_id=7, email="staff@kohleservices.com", role="user", allowed_modules=("ai", "risk"),
                     scope=scope, trace_id="t-1", settings=SimpleNamespace())


def _ins(conn, id_, server, login, uid, rule, at, **kw):
    cols = {"id": id_, "scan_batch_id": 1, "scanned_at": at, "rule_id": rule, "rule_label": f"Rule {rule}",
            "server": server, "login": login, "symbol": kw.pop("symbol", "XAUUSD"), "order_count": kw.pop("order_count", 3),
            "total_lots": kw.pop("total_lots", 1.0), "user_id": uid, "zipcode": "Z1234", "account_group": "real\\grp"}
    cols.update(kw)
    conn.execute(f"INSERT INTO alert_events ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", list(cols.values()))


@pytest.fixture
def db(tmp_path, monkeypatch):
    from app.core import risk_monitor_db as rmdb

    monkeypatch.setattr(rmdb, "_DB_PATH", tmp_path / "rm.db")
    rmdb.init_risk_monitor_db()
    keepalive = sqlite3.connect(str(rmdb._DB_PATH))
    keepalive.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
    with rmdb.get_risk_monitor_db() as c:
        # intraday-return (131): three on MT day 09-23, one on 09-22
        for id_, server, login, uid, ret, day, at in [
            (10, "MT4_Live", 11, 100, 200.0, "2026-09-23", "2026-09-23T01:00:00Z"),
            (11, "MT4_Live", 12, 200, 150.0, "2026-09-23", "2026-09-23T01:00:00Z"),
            (12, "MT5", 13, None, 120.0, "2026-09-23", "2026-09-23T01:00:00Z"),
            (13, "MT4_Live", 14, 300, 90.0, "2026-09-22", "2026-09-22T01:00:00Z"),
        ]:
            _ins(c, id_, server, login, uid, 131, at)
            c.execute(
                "INSERT INTO alert_intraday_return_detail (id, trading_day, return_pct, peak_return_pct, initial_equity, "
                "trades_today, lots_today) VALUES (?, ?, ?, ?, 1000.0, 5, 2.5)", (id_, day, ret, ret + 10),
            )
        # leverage-abuse (101): 100 fires twice on one account, 200 once, 300 once
        for id_, login, uid, epl in [(20, 31, 100, 10.0), (21, 31, 100, 20.0), (22, 32, 200, 40.0), (23, 33, 300, 5.0)]:
            _ins(c, id_, "MT4_Live", login, uid, 101, f"2026-09-2{id_ - 20}T03:00:00Z", equity_per_lot=epl,
                 orders_json=json.dumps([{"ticket": 900 + id_, "symbol": "XAUUSD", "lots": 1.0}]))
        # gap-trade SO+AB (71): L = client 200, C = client 100 (CN)
        _ins(c, 30, "MT4_Live", 21, 200, 71, "2026-09-23T02:00:00Z")
        c.execute(
            "INSERT INTO alert_gap_so_detail (id, l_login_sid, l_userid, l_name, l_ticket, l_lots, l_profit_usd, "
            "c_login_sid, c_userid, c_name, c_ticket, c_lots, c_profit_usd, open_diff_sec, lot_ratio, net_usd, "
            "so_comment, shared_ips, shared_ip_count, window_date) VALUES (30, '1-21', 200, 'Alice Leg', 501, 1.0, "
            "-900.0, '1-22', 100, 'Bob Leg', 502, 1.0, 950.0, 2, 1.0, 50.0, 'secret remark', '10.1.2.3,10.9.9.9', 2, "
            "'2026-09-22')"
        )
        # gap-trade excess profit (81)
        _ins(c, 31, "MT4_Live", 41, 300, 81, "2026-09-23T02:00:00Z", total_profit_usd=3000.0)
        c.execute(
            "INSERT INTO alert_gap_profit_detail (id, client_userid, client_name, contributing_login_sids, "
            "contributing_account_count, symbols, window_date) VALUES (31, 300, 'Carol Profit', '1-41,1-42', 2, "
            "'XAUUSD', '2026-09-22')"
        )
    monkeypatch.setattr(ra, "_fetch_cids", lambda settings, ids: {u: CIDS.get(u) for u in ids})
    yield rmdb
    keepalive.close()


def call(c=None, **kw):
    kw.setdefault("date_range", DAY)
    return run(ra.get_risk_alerts(c or ctx(), **kw))


def _no_pii(env):
    blob = json.dumps(env, ensure_ascii=False)
    for s in PII_STRINGS:
        assert s not in blob, s
    for key in ("l_name", "c_name", "client_name", "zipcode", "shared_ips", "so_comment", "group", "account_group"):
        assert f'"{key}"' not in blob, key


# ── tab / time column ────────────────────────────────────────────────────────


def test_intraday_alerts_are_selected_by_trading_day(db):
    env = call(tab="intraday-return", group_by="alert")
    assert env["ok"] is True
    d = env["data"]
    assert {r["alert_id"] for r in d["rows"]} == {10, 11, 12}  # 13 is trading_day 09-22
    assert d["time_field"] == "trading_day"
    assert d["alerts_total"] == 3 and d["alerts_by_rule"] == {"131": 3}
    row = next(r for r in d["rows"] if r["alert_id"] == 10)
    assert row["trading_day"] == "2026-09-23"
    assert row["login_sid"] == "1-11" and row["client_id"] == 100
    assert row["metrics"]["return_pct"] == 200.0 and row["metrics"]["initial_equity"] == 1000.0
    assert d["verdict"] is None
    assert env["definition"]["summary"].startswith("signal ≠ violation")
    assert env["source"]["function"] == "query_alert_events" and env["source"]["certified"] is True


def test_alert_rows_are_allow_listed(db):
    env = call(tab="gap-trade", group_by="alert")
    assert {r["alert_id"] for r in env["data"]["rows"]} == {30, 31}
    for r in env["data"]["rows"]:
        assert set(r) <= ALERT_ROW_KEYS
        allowed = set(BAND_FIELDS[r["rule_name"]]["fields"]) | {"orders_in_alert"}
        assert set(r["metrics"]) <= allowed
    so = next(r for r in env["data"]["rows"] if r["alert_id"] == 30)
    assert so["metrics"]["shared_ip_count"] == 2 and so["metrics"]["net_usd"] == 50.0
    assert so["metrics"]["c_login_sid"] == "1-22"
    gp = next(r for r in env["data"]["rows"] if r["alert_id"] == 31)
    assert gp["metrics"]["contributing_login_sids"] == ["1-41", "1-42"]
    _no_pii(env)


# ── grouping ─────────────────────────────────────────────────────────────────


def test_client_grouping_counts_null_user_alerts_separately(db):
    env = call(tab="intraday-return", group_by="client")
    d = env["data"]
    assert {r["client_id"] for r in d["rows"]} == {100, 200}
    assert d["alerts_without_client_id"] == 1
    assert d["alerts_total"] == 3  # totals still count every alert
    assert env["source"]["function"] == "aggregate_alert_events"
    for r in d["rows"]:
        assert r["sample_alert_ids"] and len(r["sample_alert_ids"]) <= 3
        assert r["top_metric"] is not None  # single band


def test_account_grouping_top_by_metric(db):
    env = call(tab="leverage-abuse", group_by="account", sort="metric", date_range=WEEK, top_n=2)
    d = env["data"]
    assert d["groups_total"] == 3 and d["rows_returned"] == 2
    assert env["truncated"] is True
    assert d["metric"]["key"] == BAND_FIELDS["leverage_abuse"]["metric"] == "margin_level"
    assert d["rows"][0]["login_sid"] == "1-33" or d["rows"][0]["login_sid"] == "1-31"
    top = {r["login_sid"]: r for r in d["rows"]}
    if "1-31" in top:
        assert top["1-31"]["alerts"] == 2 and set(top["1-31"]["sample_alert_ids"]) == {20, 21}


def test_rule_grouping(db):
    env = call(tab="gap-trade", group_by="rule")
    rows = {r["rule_id"]: r for r in env["data"]["rows"]}
    assert set(rows) == {71, 81}
    assert rows[71]["alerts"] == 1 and rows[71]["tab"] == "gap-trade"


def test_client_ids_filter(db):
    env = call(tab="leverage-abuse", group_by="alert", date_range=WEEK, client_ids=[200])
    assert {r["alert_id"] for r in env["data"]["rows"]} == {22}
    assert env["data"]["alerts_total"] == 1


def test_sids_filter(db):
    env = call(tab="intraday-return", group_by="alert", sids=[5])
    assert {r["alert_id"] for r in env["data"]["rows"]} == {12}


def test_totals_do_not_depend_on_top_n(db):
    env = call(tab="leverage-abuse", group_by="account", date_range=WEEK, top_n=1)
    assert env["data"]["alerts_total"] == 4 and env["data"]["alerts_by_rule"] == {"101": 4}


# ── scope (impl-level; registration keeps restricted callers out) ────────────


def test_restricted_scope_masks_cn_and_null_clients(db):
    env = call(ctx(frozenset({1})), tab="intraday-return", group_by="alert")
    d = env["data"]
    assert {r["alert_id"] for r in d["rows"]} == {11}
    assert d["rows_masked_by_scope"] == 2  # CN client's alert + NULL user_id alert
    assert d["alerts_total"] == 1 and d["alerts_by_rule"] == {"131": 1}  # never an unfiltered total
    assert env["scope"]["cids_applied"] == [1]


def test_restricted_client_grouping(db):
    env = call(ctx(frozenset({1})), tab="intraday-return", group_by="client")
    d = env["data"]
    assert [r["client_id"] for r in d["rows"]] == [200]
    assert d["rows_masked_by_scope"] >= 1
    assert d["groups_total"] == 1


def test_top_n_is_taken_after_the_scope_filter(db):
    # By alerts, CN client 100's account (2 alerts) would be first; restricted
    # caller with top_n=1 must still get one in-scope row.
    env = call(ctx(frozenset({1})), tab="leverage-abuse", group_by="account", date_range=WEEK, top_n=1)
    d = env["data"]
    assert d["rows_returned"] == 1
    assert d["rows"][0]["client_id"] in (200, 300)
    assert d["rows_masked_by_scope"] > 0


def test_empty_scope_masks_everything(db):
    env = call(ctx(frozenset()), tab="intraday-return", group_by="alert")
    d = env["data"]
    assert env["ok"] is True
    assert d["rows"] == [] and d["alerts_total"] == 0
    assert d["rows_masked_by_scope"] == 3


def test_gap_c_leg_of_another_out_of_scope_client_is_removed(db):
    env = call(ctx(frozenset({1})), tab="gap-trade", group_by="alert")
    so = next(r for r in env["data"]["rows"] if r["alert_id"] == 30)
    assert so["c_leg_masked_by_scope"] is True
    assert not [k for k in so["metrics"] if k.startswith("c_")]
    assert "1-22" not in json.dumps(env)
    _no_pii(env)


# ── arguments ────────────────────────────────────────────────────────────────


def test_range_limit_is_31_days(db):
    ok = call(tab="martingale", date_range={"from": "2026-08-24", "to": "2026-09-23"})
    assert ok["ok"] is True
    wide = call(tab="martingale", date_range={"from": "2026-08-23", "to": "2026-09-23"})
    assert wide["ok"] is False and wide["error"]["code"] == "range_too_wide"


@pytest.mark.parametrize(
    "kw",
    [
        {"tab": "nope"},
        {},  # neither tab nor rule_ids
        {"rule_ids": [125]},  # retired band, no tab
        {"rule_ids": [999]},
        {"tab": "martingale", "rule_ids": [131]},  # empty intersection
        {"rule_ids": [101, 131]},  # two tabs
        {"tab": "gap-trade", "sort": "metric", "group_by": "account"},  # spans two bands
        {"tab": "martingale", "group_by": "symbol"},
        {"tab": "martingale", "sort": "size"},
        {"tab": "martingale", "top_n": 51},
        {"tab": "martingale", "top_n": 0},
        {"tab": "martingale", "sids": [2]},
        {"tab": "martingale", "client_ids": list(range(1, 52))},
    ],
)
def test_bad_arguments_are_invalid_argument(db, kw):
    env = call(**kw)
    assert env["ok"] is False and env["error"]["code"] == "invalid_argument", kw


def test_rule_ids_alone_resolve_to_their_tab(db):
    env = call(rule_ids=[131], group_by="alert")
    assert env["ok"] is True and env["data"]["tab"] == "intraday-return"
    assert env["data"]["time_field"] == "trading_day"


def test_caveats_carry_the_contract_lines(db):
    env = call(tab="intraday-return", group_by="client")
    text = " ".join(env["definition"]["caveats"])
    for token in ("30 days", "FIRINGS", "trading_day", "NULL", "121-130", "signal ≠ violation"):
        assert token in text, token


def test_accounts_in_rows_counts_distinct_accounts_for_every_grouping(db):
    # leverage week: client 100 has one account fired twice, 200 and 300 one each → 3 accounts
    by_client = call(tab="leverage-abuse", group_by="client", date_range=WEEK)["data"]
    assert by_client["accounts_in_rows"] == 3
    by_alert = call(tab="leverage-abuse", group_by="alert", date_range=WEEK)["data"]
    assert by_alert["alerts_total"] == 4 and by_alert["accounts_in_rows"] == 3


def test_intraday_metric_is_the_peak_that_fired_not_the_latest_tick(db):
    env = call(tab="intraday-return", group_by="account", sort="metric", date_range=DAY, top_n=1)
    d = env["data"]
    assert d["metric"]["key"] == "peak_return_pct"
    # fixture: account 1-11 return_pct 200, peak 210
    assert d["rows"][0]["login_sid"] == "1-11" and d["rows"][0]["top_metric"] == 210.0
