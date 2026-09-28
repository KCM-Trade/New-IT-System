"""``get_alert_orders`` (docs/ai-agent/11 §2.2) with the stored alerts and the
MySQL order fetchers monkeypatched.

Pinned: argument caps (1..3 ids, ≤100 per alert, ≤200 per call), not-found
and retention wording, per-band order range (ticket / window / trading day /
gap legs), the MT5 closed-position fallback (ticket miss → open-window +
symbol/second/lots match), restricted scope (own client → scope_denied; gap
71 C leg of an out-of-scope client removed whole with legs_masked_by_scope=1),
descriptive features (1-2-4-8 → 3 escalation steps, ratio 2.0), verdict None.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

ao = importlib.import_module("app.ai_agent.tools.alert_orders")
from app.ai_agent.tools.common import CallerCtx
from app.services import alert_orders_service as aos
from app.services.rule_intraday_return_service import MT_SERVER_TZ

CIDS = {100: 0, 200: 1}
T0 = datetime(2026, 9, 23, 7, 0, 0, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


def ctx(scope=None) -> CallerCtx:
    return CallerCtx(user_id=7, email="staff@kohleservices.com", role="user", allowed_modules=("ai", "risk"),
                     scope=scope, trace_id="t-1", settings=SimpleNamespace())


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def order(ticket, *, sid=1, login=11, symbol="XAUUSD", direction="buy", lots=1.0, t=T0, hold=30, profit=10.0,
          is_open=False):
    return {
        "ticket_sid": f"{sid}-{ticket}", "ticket": ticket, "sid": sid, "login": login, "login_sid": f"{sid}-{login}",
        "symbol": symbol, "direction": direction, "lots": lots, "open_price": 2650.0,
        "close_price": None if is_open else 2651.0, "open_time": iso(t),
        "close_time": None if is_open else iso(t + timedelta(seconds=hold)),
        # the real MT wall clock (DST-aware) — what the service derives from OPEN_TIME
        "open_time_mt": t.astimezone(MT_SERVER_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "hold_sec": hold, "profit_usd": profit, "swap_usd": 0.0, "commission_usd": -1.0, "is_cent": False,
        "open": is_open,
    }


def alert(id_, rule_id, *, server="MT4_Live", login=11, uid=200, **kw):
    a = {"id": id_, "rule_id": rule_id, "rule_label": f"Rule {rule_id}", "server": server, "login": login,
         "user_id": uid, "symbol": "XAUUSD", "scanned_at": "2026-09-23T08:00:00Z", "orders": [],
         "first_open": None, "last_open": None}
    a.update(kw)
    return a


@pytest.fixture
def world(monkeypatch):
    state = {"alerts": {}, "calls": [], "tickets": {}, "window": [], "day": []}

    monkeypatch.setattr(ao, "_fetch_alerts", lambda s, ids: [state["alerts"][i] for i in ids if i in state["alerts"]])
    monkeypatch.setattr(ao, "_fetch_cids", lambda s, ids: {u: CIDS.get(u) for u in ids})

    def by_tickets(settings, *, sid, tickets, connect=None, as_of_utc=None):
        state["calls"].append(("tickets", sid, list(tickets)))
        return [o for t in tickets for o in state["tickets"].get((sid, int(t)), [])]

    def by_window(settings, *, login_sids, mt_from, mt_to, symbol=None, limit=100, connect=None, as_of_utc=None):
        state["calls"].append(("window", list(login_sids), mt_from, mt_to, symbol))
        rows = [o for o in state["window"] if o["login_sid"] in login_sids]
        return rows[:limit], len(rows)

    def by_day(settings, *, login_sids, trading_day, limit=100, connect=None, as_of_utc=None):
        state["calls"].append(("day", list(login_sids), trading_day))
        rows = [o for o in state["day"] if o["login_sid"] in login_sids]
        return rows[:limit], len(rows)

    def by_seconds(settings, *, login_sids, mt_seconds, symbol=None, limit=200, connect=None, as_of_utc=None):
        wanted = {d.strftime("%Y-%m-%d %H:%M:%S") for d in mt_seconds}
        state["calls"].append(("seconds", list(login_sids), sorted(wanted), symbol))
        rows = [o for o in state["window"] if o["login_sid"] in login_sids and o["open_time_mt"] in wanted
                and (symbol is None or o["symbol"] == symbol)]
        return rows[:limit], len(rows)

    monkeypatch.setattr(aos, "fetch_orders_at_open_seconds", by_seconds)
    monkeypatch.setattr(aos, "fetch_orders_by_tickets", by_tickets)
    monkeypatch.setattr(aos, "fetch_orders_by_open_window", by_window)
    monkeypatch.setattr(aos, "fetch_orders_for_trading_day", by_day)
    return state


# ── arguments ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ids", [[], None, [1, 2, 3, 4], ["x"], "12"])
def test_alert_ids_must_be_1_to_3_integers(world, ids):
    env = run(ao.get_alert_orders(ctx(), ids))
    assert env["ok"] is False and env["error"]["code"] == "invalid_argument"


@pytest.mark.parametrize("n", [0, 101, "x"])
def test_max_orders_per_alert_bounds(world, n):
    world["alerts"][1] = alert(1, 91)
    env = run(ao.get_alert_orders(ctx(), [1], n))
    assert env["ok"] is False and env["error"]["code"] == "invalid_argument"


def test_unknown_ids_are_subject_not_found_with_retention_hint(world):
    env = run(ao.get_alert_orders(ctx(), [42]))
    assert env["ok"] is False and env["error"]["code"] == "subject_not_found"
    assert "30 days" in env["error"]["message"]


def test_partly_unknown_ids_are_reported(world):
    world["alerts"][1] = alert(1, 131, trading_day="2026-09-23")
    env = run(ao.get_alert_orders(ctx(), [1, 42]))
    assert env["ok"] is True and env["data"]["alerts_not_found"] == [42]


# ── per-band order ranges ────────────────────────────────────────────────────


def test_hedge_by_stored_tickets(world):
    world["alerts"][1] = alert(1, 91, orders=[{"ticket": 501, "symbol": "XAUUSD", "lots": 1.0, "open_time": iso(T0)},
                                               {"ticket": 502, "symbol": "XAUUSD", "lots": 1.0, "open_time": iso(T0)}])
    world["tickets"][(1, 501)] = [order(501)]
    world["tickets"][(1, 502)] = [order(502, direction="sell")]
    env = run(ao.get_alert_orders(ctx(), [1]))
    a = env["data"]["alerts"][0]
    assert {o["ticket_sid"] for o in a["orders"]} >= {"1-501", "1-502"}
    assert ("tickets", 1, [501, 502]) in world["calls"]
    assert not [c for c in world["calls"] if c[0] == "window"]
    assert env["data"]["verdict"] is None
    assert env["definition"]["summary"].startswith("signal ≠ violation")


def test_mt5_closed_ticket_miss_falls_back_to_the_open_window(world):
    # sid 5: the alert's ticket is the position id; the closed row is keyed by
    # the exit deal, so the ticket lookup misses and the tool must align by
    # symbol + open second + lots instead.
    world["alerts"][1] = alert(1, 101, server="MT5", login=13,
                               orders=[{"ticket": 37239458, "symbol": "XAUUSD", "lots": 2.0, "open_time": iso(T0)}])
    world["window"] = [order(37239474, sid=5, login=13, lots=2.0),
                       order(37239999, sid=5, login=13, lots=2.0, t=T0 + timedelta(seconds=40))]
    env = run(ao.get_alert_orders(ctx(), [1]))
    a = env["data"]["alerts"][0]
    assert [o["ticket_sid"] for o in a["orders"]] == ["5-37239474"]
    assert [c[0] for c in world["calls"]] == ["tickets", "seconds"]
    assert any("exit deal" in n for n in a["notes"])


def test_burst_uses_the_alert_window_and_returns_its_own_orders(world):
    stored = [{"symbol": "XAUUSD", "lots": 1.0, "open_time": iso(T0 + timedelta(seconds=i))} for i in range(3)]
    world["alerts"][1] = alert(1, 5, first_open=iso(T0), last_open=iso(T0 + timedelta(seconds=2)), orders=stored)
    world["window"] = [order(600 + i, t=T0 + timedelta(seconds=i)) for i in range(3)] + [
        order(700, t=T0 + timedelta(seconds=1), symbol="XAUUSD", lots=5.0)
    ]
    env = run(ao.get_alert_orders(ctx(), [1]))
    a = env["data"]["alerts"][0]
    assert len(a["orders"]) == 3 == a["alignment"]["orders_in_alert"] == a["alignment"]["matched"]
    assert a["orders_total"] == 3
    kind, lss, secs, symbol = world["calls"][0]
    # the alert's OWN seconds ±1s (stored + 3h fixed = MT wall clock), not the whole window
    assert kind == "seconds" and lss == ["1-11"] and symbol == "XAUUSD"
    assert secs[0] == "2026-09-23 09:59:59" and secs[-1] == "2026-09-23 10:00:03" and len(secs) == 5


def test_window_is_only_the_fallback_when_the_alert_stored_no_orders(world):
    world["alerts"][1] = alert(1, 5, first_open=iso(T0), last_open=iso(T0 + timedelta(seconds=2)), orders=[])
    world["window"] = [order(600 + i, t=T0 + timedelta(seconds=i)) for i in range(3)]
    env = run(ao.get_alert_orders(ctx(), [1]))
    kind, lss, mt_from, mt_to, symbol = world["calls"][0]
    assert kind == "window"
    assert mt_from == datetime(2026, 9, 23, 9, 59, 59) and mt_to == datetime(2026, 9, 23, 10, 0, 3)
    assert env["data"]["alerts"][0]["orders_total"] == 3


def test_weeks_wide_martingale_still_gets_its_newest_add(world):
    """Cold review #4: [first_open, last_open] spans weeks; the newest add must
    be fetched even when hundreds of unrelated orders sit in between."""
    anchor, add = T0 - timedelta(days=20), T0
    stored = [{"symbol": "XAUUSD", "lots": 1.0, "open_time": iso(anchor)},
              {"symbol": "XAUUSD", "lots": 2.0, "open_time": iso(add)}]
    world["alerts"][1] = alert(1, 111, first_open=iso(anchor), last_open=iso(add), orders=stored)
    filler = [order(1000 + i, t=anchor + timedelta(minutes=5 * i + 3)) for i in range(300)]
    world["window"] = [order(900, t=anchor, lots=1.0)] + filler + [order(901, t=add, lots=2.0)]
    a = run(ao.get_alert_orders(ctx(), [1]))["data"]["alerts"][0]
    assert [o["ticket_sid"] for o in a["orders"]] == ["1-900", "1-901"]
    assert a["features"]["lot_escalation_steps"] == 1


def test_intraday_uses_positions_opened_on_trading_day(world):
    world["alerts"][1] = alert(1, 131, trading_day="2026-09-23", trades_today=2)
    world["day"] = [order(1), order(2)]
    env = run(ao.get_alert_orders(ctx(), [1]))
    a = env["data"]["alerts"][0]
    assert a["orders_total"] == 2 == len(a["orders"])
    assert world["calls"][0][0] == "day" and str(world["calls"][0][2]) == "2026-09-23"


def test_gap81_uses_contributing_accounts_on_window_date(world):
    world["alerts"][1] = alert(1, 81, contributing_login_sids="1-41,1-42", window_date="2026-09-22")
    world["day"] = [order(1, login=41), order(2, login=42), order(3, login=99)]
    env = run(ao.get_alert_orders(ctx(), [1]))
    assert env["data"]["alerts"][0]["orders_total"] == 2
    assert world["calls"][0][1] == ["1-41", "1-42"]


def _gap71(world):
    world["alerts"][1] = alert(1, 71, uid=200, l_login_sid="1-21", l_ticket=501, l_open_time=iso(T0), l_lots=1.0,
                               c_login_sid="1-22", c_userid=100, c_ticket=502, c_open_time=iso(T0), c_lots=1.0,
                               l_name="Alice Leg", c_name="Bob Leg", shared_ips="10.1.2.3")
    world["tickets"][(1, 501)] = [order(501, login=21)]
    world["tickets"][(1, 502)] = [order(502, login=22, direction="sell")]


def test_gap71_returns_both_legs_unrestricted(world):
    _gap71(world)
    env = run(ao.get_alert_orders(ctx(), [1]))
    a = env["data"]["alerts"][0]
    assert sorted(o["leg"] for o in a["orders"]) == ["C", "L"]
    assert a["legs_masked_by_scope"] == 0
    blob = json.dumps(env)
    assert "Alice Leg" not in blob and "Bob Leg" not in blob and "10.1.2.3" not in blob


def test_gap71_out_of_scope_c_leg_is_removed_whole(world):
    _gap71(world)
    env = run(ao.get_alert_orders(ctx(frozenset({1})), [1]))
    a = env["data"]["alerts"][0]
    assert [o["leg"] for o in a["orders"]] == ["L"]
    assert a["legs_masked_by_scope"] == 1
    assert "1-22" not in json.dumps(env["data"])
    assert not [c for c in world["calls"] if c[0] == "tickets" and 502 in c[2]]  # never even fetched


def test_restricted_caller_is_denied_an_out_of_scope_alert(world):
    world["alerts"][1] = alert(1, 131, uid=100, trading_day="2026-09-23")
    env = run(ao.get_alert_orders(ctx(frozenset({1})), [1]))
    assert env["ok"] is False and env["error"]["code"] == "scope_denied"
    assert world["calls"] == []


def test_restricted_caller_is_denied_an_alert_without_client_id(world):
    world["alerts"][1] = alert(1, 131, uid=None, trading_day="2026-09-23")
    env = run(ao.get_alert_orders(ctx(frozenset({1})), [1]))
    assert env["ok"] is False and env["error"]["code"] == "scope_denied"


def test_empty_scope_denies_everything(world):
    world["alerts"][1] = alert(1, 131, uid=200, trading_day="2026-09-23")
    env = run(ao.get_alert_orders(ctx(frozenset()), [1]))
    assert env["error"]["code"] == "scope_denied"


# ── caps ─────────────────────────────────────────────────────────────────────


def test_per_alert_and_per_call_caps(world):
    for i in (1, 2, 3):
        world["alerts"][i] = alert(i, 131, login=10 + i, trading_day="2026-09-23")
    world["day"] = [order(1000 * i + k, login=10 + i) for i in (1, 2, 3) for k in range(100)]
    env = run(ao.get_alert_orders(ctx(), [1, 2, 3], 100))
    counts = [len(a["orders"]) for a in env["data"]["alerts"]]
    assert all(c <= 100 for c in counts) and sum(counts) <= 200
    assert env["truncated"] is True
    assert [a["orders_total"] for a in env["data"]["alerts"]][:2] == [100, 100]


def test_default_is_60_per_alert(world):
    world["alerts"][1] = alert(1, 131, trading_day="2026-09-23")
    world["day"] = [order(k) for k in range(80)]
    a = run(ao.get_alert_orders(ctx(), [1]))["data"]["alerts"][0]
    assert len(a["orders"]) == 60 and a["orders_total"] == 80


def test_order_rows_are_projected(world):
    world["alerts"][1] = alert(1, 131, trading_day="2026-09-23")
    world["day"] = [order(1)]
    o = run(ao.get_alert_orders(ctx(), [1]))["data"]["alerts"][0]["orders"][0]
    assert set(o) == {"ticket_sid", "login_sid", "symbol", "direction", "lots", "open_price", "close_price",
                      "open_time", "close_time", "hold_sec", "profit_usd", "swap_usd", "commission_usd", "is_cent", "open"}


# ── features ─────────────────────────────────────────────────────────────────


def test_martingale_1_2_4_8_fixture():
    orders = [order(i, lots=l, t=T0 + timedelta(minutes=i), hold=600) for i, l in enumerate([1.0, 2.0, 4.0, 8.0])]
    f = ao.compute_features(orders, now=T0 + timedelta(hours=1))
    assert f["lot_escalation_steps"] == 3
    assert f["max_consecutive_lot_ratio"] == 2.0
    assert f["orders"] == 4 and f["symbols"] == ["XAUUSD"]


def test_escalation_is_per_symbol_and_direction():
    orders = [order(1, lots=1.0), order(2, lots=2.0, direction="sell", t=T0 + timedelta(minutes=1)),
              order(3, lots=1.0, symbol="EURUSD", t=T0 + timedelta(minutes=2))]
    f = ao.compute_features(orders, now=T0 + timedelta(hours=1))
    assert f["lot_escalation_steps"] == 0


def test_hold_same_second_overlap_and_win_rate():
    orders = [
        order(1, t=T0, hold=10, profit=5.0),
        order(2, t=T0, hold=20, profit=-3.0, direction="sell"),
        order(3, t=T0 + timedelta(seconds=100), hold=200, profit=4.0),
    ]
    f = ao.compute_features(orders, now=T0 + timedelta(hours=1))
    assert f["median_hold_sec"] == 20
    assert f["pct_hold_lt_60s"] == pytest.approx(66.7, abs=0.1)
    assert f["same_second_open_groups"] == 1
    assert f["win_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert f["net_profit_usd"] == pytest.approx(5.0 - 3.0 + 4.0 - 3.0)
    # buy [0,10] ∪ [100,300], sell [0,20]: both-sides 10s of 220s union
    assert f["opposite_side_overlap_pct"] == pytest.approx(100 * 10 / 220, abs=0.1)


def test_open_orders_do_not_count_in_hold_or_win_stats():
    f = ao.compute_features([order(1, is_open=True, profit=50.0)], now=T0 + timedelta(hours=1))
    assert f["median_hold_sec"] is None and f["win_rate"] is None
    assert f["open_orders"] == 1 and f["floating_profit_usd"] == 50.0


def test_mysql_timeout_becomes_upstream_timeout(world, monkeypatch):
    import pymysql

    world["alerts"][1] = alert(1, 131, trading_day="2026-09-23")

    def boom(*a, **k):
        raise pymysql.err.OperationalError(3024, "max statement time exceeded")

    monkeypatch.setattr(aos, "fetch_orders_for_trading_day", boom)
    env = run(ao.get_alert_orders(ctx(), [1]))
    assert env["ok"] is False and env["error"]["code"] == "upstream_timeout"


def test_winter_stored_times_still_match_fetched_orders():
    """Cold review #1: detectors store alert times with a FIXED +03:00, the
    service reports DST-aware UTC. In winter (MT = UTC+2) the two UTC strings
    differ by an hour; matching must happen on the MT wall-clock second."""
    real_utc = datetime(2026, 11, 10, 8, 0, 0, tzinfo=timezone.utc)   # MT 10:00 in winter (UTC+2)
    stored_utc = "2026-11-10T07:00:00Z"                                # what the detector wrote (MT − 3h)
    fetched = order(701, t=real_utc)
    assert fetched["open_time_mt"] == "2026-11-10 10:00:00"
    matched = ao.match_stored([{"symbol": "XAUUSD", "open_time": stored_utc, "lots": 1.0}], [fetched])
    assert [m["ticket_sid"] for m in matched] == ["1-701"]
