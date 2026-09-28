"""Certified tools (docs/ai-agent/02-contracts.md §2–§3) — assembly logic with
the data-access layer monkeypatched. No database, no agent framework.

What is pinned here and why:
  * the envelope ALWAYS carries definition + source.certified — the UI badge
    is rendered from them;
  * errors are structures, never exceptions (a raised tool error is handed to
    the model with wording nobody reviewed);
  * scope: ``None`` passes, ``frozenset()`` refuses, an unresolvable cid
    refuses a restricted caller (fail closed), and the refusal is logged;
  * range 366 ok / 367 refused, demo & employee refused with subject_excluded,
    CEN ÷100, sid=5 closed CMD flipped, alerts capped at 500 with the full
    count kept, verdict None, masked peers counted, IPs masked.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.ai_agent.tools import client_overview as co
from app.ai_agent.tools import common
from app.ai_agent.tools import risk_signals as rs
from app.ai_agent.tools import trade_activity as ta
from app.ai_agent.tools.common import CallerCtx, ResolvedSubject
from app.services import trade_activity_service as tas

RANGE = {"from": "2026-09-01", "to": "2026-09-27"}
CLIENT = {"kind": "client_id", "value": "123456"}
LOGIN = {"kind": "login_sid", "value": "1-8522845"}


def run(coro):
    return asyncio.run(coro)


def ctx(scope=None, email="staff@kohleservices.com") -> CallerCtx:
    return CallerCtx(
        user_id=7, email=email, role="user", allowed_modules=("ai",), scope=scope, trace_id="trace-1",
        settings=SimpleNamespace(),
    )


def resolved(*, cid=1, employee=False, accounts=None, dropped=0) -> ResolvedSubject:
    if accounts is None:
        accounts = [
            {"login_sid": "1-8522845", "sid": 1, "login": 8522845, "group": "real", "currency": "USD",
             "is_cent": False, "balance": 100.0, "equity": 120.0, "credit": 0.0, "opened_at": None,
             "last_trade_at": None},
            {"login_sid": "5-60001", "sid": 5, "login": 60001, "group": "cent", "currency": "USD",
             "is_cent": True, "balance": 10.0, "equity": 9.5, "credit": 0.0, "opened_at": None,
             "last_trade_at": None},
        ]
    return ResolvedSubject(client_id=123456, cid=cid, is_employee=employee, country="TH", registered_at=None,
                           accounts=accounts, excluded_accounts=dropped, crm_row_found=True)


@pytest.fixture
def subject_ok(monkeypatch):
    monkeypatch.setattr(common, "_fetch_subject", lambda settings, subject: resolved())
    return None


@pytest.fixture
def overview_data(monkeypatch):
    monkeypatch.setattr(co, "_fetch_money_pg", lambda s, cid: {"profit_all": 10.0, "rebate_all": 2.0, "floating_pl": -1.0, "net_gain": 11.0})
    monkeypatch.setattr(co, "_fetch_net_deposit_split", lambda s, cid: {"net_deposit_trading": 500.0, "ib_withdrawal": -20.0})
    monkeypatch.setattr(co, "_fetch_last_trades", lambda s, sids, rng: {"1-8522845": "2026-09-20T10:00:00Z"})
    monkeypatch.setattr(co, "_fetch_activity", lambda s, cid, asof: {"activity_status": "active_7d", "crm_tags": ["VIP"], "holding_live": True})


# ── envelope + validation ────────────────────────────────────────────────────


def test_ok_envelope_carries_definition_and_certified_source(subject_ok, overview_data):
    env = run(co.get_client_overview(ctx(), CLIENT, RANGE))
    assert env["ok"] is True
    assert env["source"]["certified"] is True and env["source"]["function"]
    assert env["definition"]["summary"] and env["definition"]["caveats"] and env["definition"]["day_basis"]
    assert env["scope"] == {"cids_applied": "all"}
    assert env["truncated"] is False
    money = env["data"]["money"]
    assert money["net_deposit_trading"] == 500.0 and money["ib_withdrawal"] == -20.0  # two legs, no switch
    assert money["net_gain"] == 11.0 and "STRICT" in money["net_gain_definition"]
    assert env["data"]["accounts"][0]["last_trade_at"] == "2026-09-20T10:00:00Z"
    assert env["data"]["activity_status"] == "active_7d"


@pytest.mark.parametrize(
    "subject",
    [None, {"kind": "email", "value": "a@b"}, {"kind": "client_id", "value": "abc"}, {"kind": "login_sid", "value": "8522845"}],
)
def test_bad_subject_is_a_structured_error(subject, subject_ok, overview_data):
    env = run(co.get_client_overview(ctx(), subject, RANGE))
    assert env["ok"] is False and env["error"]["code"] == "invalid_argument"


def test_range_366_days_ok_367_refused(subject_ok, overview_data):
    ok = run(co.get_client_overview(ctx(), CLIENT, {"from": "2025-09-27", "to": "2026-09-27"}))
    assert ok["ok"] is True  # 366 days inclusive
    bad = run(co.get_client_overview(ctx(), CLIENT, {"from": "2025-09-26", "to": "2026-09-27"}))
    assert bad["ok"] is False and bad["error"]["code"] == "range_too_wide"
    assert bad["error"]["detail"] == {"days": 367, "max_days": 366}


def test_reversed_range_is_invalid(subject_ok, overview_data):
    env = run(co.get_client_overview(ctx(), CLIENT, {"from": "2026-09-27", "to": "2026-09-01"}))
    assert env["error"]["code"] == "invalid_argument"


# ── subject universe ─────────────────────────────────────────────────────────


def test_subject_not_found(monkeypatch, overview_data):
    monkeypatch.setattr(common, "_fetch_subject", lambda s, subj: None)
    env = run(co.get_client_overview(ctx(), CLIENT, RANGE))
    assert env["error"]["code"] == "subject_not_found"


def test_employee_is_subject_excluded(monkeypatch, overview_data):
    monkeypatch.setattr(common, "_fetch_subject", lambda s, subj: resolved(employee=True))
    env = run(co.get_client_overview(ctx(), CLIENT, RANGE))
    assert env["error"]["code"] == "subject_excluded" and env["error"]["detail"]["reason"] == "employee"


def test_all_demo_accounts_is_subject_excluded(monkeypatch, overview_data):
    monkeypatch.setattr(common, "_fetch_subject", lambda s, subj: resolved(accounts=[], dropped=2))
    env = run(co.get_client_overview(ctx(), CLIENT, RANGE))
    assert env["error"]["code"] == "subject_excluded" and env["error"]["detail"]["reason"] == "demo"


def test_unlinked_mt_account_is_subject_excluded(monkeypatch, overview_data):
    """An MT login with no CRM owner is excluded, not "not found" (§2.2)."""
    from app.ai_agent.tools.common import ResolvedSubject

    monkeypatch.setattr(
        common,
        "_fetch_subject",
        lambda s, subj: ResolvedSubject(
            client_id=0, cid=None, is_employee=False, country=None, registered_at=None,
            accounts=[], excluded_accounts=0, crm_row_found=False, unlinked=True,
        ),
    )
    env = run(co.get_client_overview(ctx(), {"kind": "login_sid", "value": "5-60000017"}, RANGE))
    assert env["ok"] is False
    assert env["error"]["code"] == "subject_excluded"
    assert env["error"]["detail"]["reason"] == "no_crm_user"


def test_login_sid_subject_resolves_to_client_and_returns_both(subject_ok, overview_data):
    env = run(co.get_client_overview(ctx(), LOGIN, RANGE))
    assert env["ok"] and env["data"]["client"]["client_id"] == 123456
    assert "1-8522845" in [a["login_sid"] for a in env["data"]["accounts"]]


# ── scope (§2.1) ─────────────────────────────────────────────────────────────


def test_scope_none_passes_and_reports_all(subject_ok, overview_data):
    env = run(co.get_client_overview(ctx(scope=None), CLIENT, RANGE))
    assert env["ok"] and env["scope"]["cids_applied"] == "all"


def test_scope_containing_cid_passes_and_reports_cids(subject_ok, overview_data):
    env = run(co.get_client_overview(ctx(scope=frozenset({0, 1})), CLIENT, RANGE))
    assert env["ok"] and env["scope"]["cids_applied"] == [0, 1]


def test_scope_denied_when_cid_outside(monkeypatch, overview_data):
    monkeypatch.setattr(common, "_fetch_subject", lambda s, subj: resolved(cid=0))
    env = run(co.get_client_overview(ctx(scope=frozenset({1})), CLIENT, RANGE))
    assert env["ok"] is False and env["error"]["code"] == "scope_denied"
    # The auth_events row is NOT written here: the agent container's users.db
    # is on a read-only mount. The main API records it when it sees this
    # tool_done (test_ai_route.py pins that); a write attempt here would only
    # produce a traceback per refusal.
    assert not hasattr(common, "record_auth_event")


def test_empty_scope_is_restricted_not_unrestricted(subject_ok, overview_data):
    # frozenset() and None are opposite answers; `if not scope` would pass this.
    env = run(co.get_client_overview(ctx(scope=frozenset()), CLIENT, RANGE))
    assert env["error"]["code"] == "scope_denied"


def test_unresolvable_cid_refuses_restricted_caller(monkeypatch, overview_data):
    monkeypatch.setattr(common, "_fetch_subject", lambda s, subj: resolved(cid=None))
    assert run(co.get_client_overview(ctx(scope=frozenset({1})), CLIENT, RANGE))["error"]["code"] == "scope_denied"
    assert run(co.get_client_overview(ctx(scope=None), CLIENT, RANGE))["ok"] is True


def test_not_found_reads_as_scope_denied_for_restricted_caller(monkeypatch, overview_data):
    # No oracle: a restricted caller cannot tell "does not exist" from "not yours".
    monkeypatch.setattr(common, "_fetch_subject", lambda s, subj: None)
    env = run(co.get_client_overview(ctx(scope=frozenset({1})), CLIENT, RANGE))
    assert env["error"]["code"] == "scope_denied"


def test_scope_check_before_exclusion(monkeypatch, overview_data):
    monkeypatch.setattr(common, "_fetch_subject", lambda s, subj: resolved(cid=0, employee=True))
    env = run(co.get_client_overview(ctx(scope=frozenset({1})), CLIENT, RANGE))
    assert env["error"]["code"] == "scope_denied"  # not subject_excluded


# ── timeouts / internal errors never raise ───────────────────────────────────


def test_upstream_timeout_is_an_envelope():
    def slow():
        time.sleep(0.5)

    env = run(common.run_sync_with_timeout(slow, ctx=ctx(), seconds=0.05))
    assert env["ok"] is False and env["error"]["code"] == "upstream_timeout"
    assert env["error"]["detail"]["trace_id"] == "trace-1"


def test_internal_error_is_an_envelope_with_trace_id():
    def boom():
        raise RuntimeError("db exploded")

    env = run(common.run_sync_with_timeout(boom, ctx=ctx()))
    assert env["error"]["code"] == "internal" and "exploded" not in env["error"]["message"]
    assert env["error"]["detail"]["trace_id"] == "trace-1"


def test_mysql_statement_timeout_is_upstream_timeout_for_every_tool():
    """2026-09-28: rank_accounts over a month hit MAX_EXECUTION_TIME (errno
    3024) and answered `internal`; §2.6 says a DB timeout is upstream_timeout
    ("narrow the range, retry once") whichever tool raised it."""
    import pymysql

    for errno in (3024, 1317, 2013):
        def killed(errno=errno):
            raise pymysql.err.OperationalError(errno, "maximum statement execution time exceeded")

        env = run(common.run_sync_with_timeout(killed, ctx=ctx()))
        assert env["error"]["code"] == "upstream_timeout", errno
        assert env["error"]["detail"]["mysql_errno"] == errno

    def other():
        raise pymysql.err.OperationalError(1045, "access denied")

    assert run(common.run_sync_with_timeout(other, ctx=ctx()))["error"]["code"] == "internal"


def test_tool_propagates_upstream_timeout(monkeypatch, subject_ok):
    def slow(settings, cid):
        time.sleep(0.3)

    monkeypatch.setattr(co, "_fetch_money_pg", slow)
    monkeypatch.setattr(common, "TOOL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(co, "run_sync_with_timeout", lambda fn, *a, ctx, **k: common.run_sync_with_timeout(fn, *a, ctx=ctx, seconds=0.05, **k))
    env = run(co.get_client_overview(ctx(), CLIENT, RANGE))
    assert env["error"]["code"] == "upstream_timeout"


# ── tool 2: trade activity ───────────────────────────────────────────────────


def _trade(login_sid, sid, symbol, cmd, lots, profit, open_t, close_t, currency="USD", commission=0, swaps=0):
    return {
        "login_sid": login_sid, "sid": sid, "symbol": symbol, "cmd": cmd, "lots": lots, "profit": profit,
        "commission": commission, "swaps": swaps, "total_profit": profit + commission + swaps,
        "open_time": open_t, "close_time": close_t, "close_date": close_t.date() if close_t else None,
        "currency": currency,
    }


@pytest.fixture
def trade_rows(monkeypatch):
    rows = [
        _trade("1-8522845", 1, "XAUUSD", 0, 1.0, 100.0, datetime(2026, 9, 10, 10, 0), datetime(2026, 9, 10, 10, 10)),
        _trade("1-8522845", 1, "XAUUSD", 1, 2.0, -50.0, datetime(2026, 9, 11, 1, 0), datetime(2026, 9, 11, 4, 0)),
        # cent ACCOUNT: money /100, lots untouched
        _trade("5-60001", 5, "XAUUSD", 1, 3.0, 1000.0, datetime(2026, 9, 12, 0, 30), datetime(2026, 9, 12, 0, 40), currency="CEN"),
        # cent SYMBOL: lots and money /100
        _trade("5-60001", 5, "XAUUSD.cent", 0, 100.0, 200.0, datetime(2026, 9, 13, 12, 0), datetime(2026, 9, 13, 12, 5), currency="CEN"),
    ]
    monkeypatch.setattr(tas, "fetch_closed_rows", lambda conn, sids, f, t: [r for r in rows if r["login_sid"] in sids])
    monkeypatch.setattr(tas, "fetch_open_rows", lambda conn, sids: [
        _trade("1-8522845", 1, "EURUSD", 0, 0.5, -3.0, datetime(2026, 9, 27, 9, 0), datetime(1970, 1, 1)),
    ])
    monkeypatch.setattr(ta, "connect_mysql", lambda settings: SimpleNamespace(close=lambda: None))
    return rows


def test_trade_activity_cent_and_direction(subject_ok, trade_rows):
    env = run(ta.get_trade_activity(ctx(), CLIENT, RANGE, "symbol"))
    assert env["ok"] and env["source"]["function"] == "by_subject"
    t = env["data"]["totals"]
    assert t["orders"] == 4
    # 100 - 50 + 1000/100 + 200/100 = 62
    assert t["net_profit"] == 62.0
    # lots: 1 + 2 + 3 (cent account keeps lots) + 100/100 (cent symbol) = 7
    assert t["lots"] == 7.0
    assert t["win_rate"] == 0.75
    rows = {r["key"]: r for r in env["data"]["rows"]}
    assert set(rows) == {"XAUUSD", "XAUUSD.cent"}
    assert env["data"]["open_positions"]["count"] == 1 and env["data"]["open_positions"]["lots"] == 0.5
    assert env["data"]["open_positions"]["oldest_open_at"].endswith("Z")


def test_trade_activity_login_subject_narrows_to_one_account(subject_ok, trade_rows):
    env = run(ta.get_trade_activity(ctx(), LOGIN, RANGE, "day"))
    assert env["data"]["login_sids"] == ["1-8522845"]
    assert env["data"]["totals"]["orders"] == 2
    assert [r["key"] for r in env["data"]["rows"]] == ["2026-09-10", "2026-09-11"]


def test_trade_activity_hold_buckets_and_bad_group_by(subject_ok, trade_rows):
    env = run(ta.get_trade_activity(ctx(), CLIENT, RANGE, "hold_bucket"))
    keys = [r["key"] for r in env["data"]["rows"]]
    assert keys == ["<30min", ">2h"]
    bad = run(ta.get_trade_activity(ctx(), CLIENT, RANGE, "week"))
    assert bad["error"]["code"] == "invalid_argument"


def test_sid5_closed_cmd_is_flipped_in_normalise():
    row = tas.normalise_row(_trade("5-1", 5, "XAUUSD", 1, 1.0, 1.0, datetime(2026, 9, 1), datetime(2026, 9, 1, 1)))
    assert row["direction"] == "buy"  # CMD=1 on a closed MT5 row is a long position
    open_row = tas.normalise_row(_trade("5-1", 5, "XAUUSD", 1, 1.0, 1.0, datetime(2026, 9, 1), datetime(1970, 1, 1)))
    assert open_row["direction"] == "sell" and open_row["hold_bucket"] is None
    mt4 = tas.normalise_row(_trade("1-1", 1, "XAUUSD", 1, 1.0, 1.0, datetime(2026, 9, 1), datetime(2026, 9, 1, 1)))
    assert mt4["direction"] == "sell"


# ── tool 3: risk signals ─────────────────────────────────────────────────────


def _alert(i, rule_id=131, login=8522845, server="MT4_Live"):
    return {"id": i, "rule_id": rule_id, "rule_label": f"Rule {rule_id}", "scanned_at": f"2026-09-{10 + i % 15:02d}T00:00:00Z",
            "server": server, "login": login, "symbol": "XAUUSD", "order_count": 3, "total_lots": 1.5}


@pytest.fixture
def signals_data(monkeypatch):
    alerts = [_alert(i) for i in range(500)]
    monkeypatch.setattr(rs, "_fetch_alerts", lambda s, sids, since, until: {"alerts": alerts, "by_rule": {"131": 700, "71": 3}, "total": 703})
    monkeypatch.setattr(rs, "_fetch_case", lambda s, cid: {"user_id": cid, "state": "watching", "tags": ["gap"], "first_signal_at": "2026-09-01T00:00:00Z",
                                                            "last_signal_at": "2026-09-20T00:00:00Z", "action_at": None, "signal_count": 4, "review_after": None})
    monkeypatch.setattr(rs, "_fetch_crm_risk_tags", lambda s, cid: ["Withdrawal Notice"])
    monkeypatch.setattr(rs, "_fetch_shared_ip", lambda s, cid, f, t: {
        "peers": {111: {"user_id": 111, "accounts": 1}, 222: {"user_id": 222, "accounts": 2}, 333: {"user_id": 333, "accounts": 1}},
        "ip_peer_users": {"1.2.3.4": [111, 222], "5.6.7.8": [333]},
        "ip_counts": {"1.2.3.4": 2, "5.6.7.8": 1},
        "matched": ["user_id"],
    })
    monkeypatch.setattr(rs, "_fetch_peer_cids", lambda s, ids: {111: 1, 222: 0, 333: None})


def test_risk_signals_shape_verdict_and_truncation(subject_ok, signals_data):
    env = run(rs.get_risk_signals(ctx(), CLIENT, RANGE))
    d = env["data"]
    assert env["ok"] and d["verdict"] is None
    assert env["definition"]["summary"].startswith("signal ≠ violation")
    assert len(d["alerts"]) == 500 and d["alerts_total"] == 703 and env["truncated"] is True
    assert d["alerts_by_rule"] == {"131": 700, "71": 3}  # from the full count, not the page
    a = d["alerts"][0]
    assert a["rule_name"] == "intraday_return" and a["login_sid"] == "1-8522845" and a["evidence_ref"].startswith("risk-monitor#intraday_return/")
    assert d["cases"][0]["status"] == "watching" and d["cases"][0]["conclusion_tags"] == ["gap"]
    assert d["crm_risk_tags"] == ["Withdrawal Notice"]
    assert d["shared_ip"]["peer_clients"] == 3 and d["shared_ip"]["peers_masked_by_scope"] == 0
    assert d["shared_ip"]["strongest_link"]["ip"] == "1.2.3.0/24"


def test_risk_signals_masks_peers_outside_scope(subject_ok, signals_data):
    env = run(rs.get_risk_signals(ctx(scope=frozenset({1})), CLIENT, RANGE))
    s = env["data"]["shared_ip"]
    # 222 is CN, 333 unresolvable → both hidden; 111 visible
    assert s["peer_clients"] == 1 and s["peers_masked_by_scope"] == 2
    assert s["strongest_link"]["ip"] == "1.2.3.0/24" and s["strongest_link"]["peer_clients_on_ip"] == 1
    assert env["scope"]["cids_applied"] == [1]


def test_risk_signals_case_unavailable_is_said_not_hidden(subject_ok, signals_data, monkeypatch):
    monkeypatch.setattr(rs, "_fetch_case", lambda s, cid: {"_unavailable": True})
    env = run(rs.get_risk_signals(ctx(), CLIENT, RANGE))
    assert env["data"]["cases"] == [] and any("unavailable" in c for c in env["definition"]["caveats"])


def test_rule_band_names():
    assert rs.rule_band_name(1) == "burst_open" and rs.rule_band_name(85) == "gap_trade_profit"
    assert rs.rule_band_name(140) == "intraday_return" and rs.rule_band_name(999) == "unknown"


def test_mask_ip():
    assert common.mask_ip("203.0.113.77") == "203.0.113.0/24"
    assert common.mask_ip("2001:db8::1") == "2001:db8::/48"
    assert common.mask_ip("not-an-ip") is None


def test_ctx_from_request_keeps_empty_scope_restricted():
    c = common.ctx_from_request({"user_id": 1, "email": "a@b", "role": "user", "allowed_modules": ["ai"]}, [], "t")
    assert c.scope == frozenset() and c.cids_applied == []
    assert common.ctx_from_request({"user_id": 1}, None, "t").scope is None


def test_subject_is_resolved_once_per_turn(monkeypatch, overview_data, signals_data):
    """M7: three tools on the same client share one resolver round trip."""
    calls: list = []

    def _fetch(settings, subject):
        calls.append(subject.value)
        return resolved()

    monkeypatch.setattr(common, "_fetch_subject", _fetch)
    c = ctx()
    assert run(co.get_client_overview(c, CLIENT, RANGE))["ok"]
    assert run(rs.get_risk_signals(c, CLIENT, RANGE))["ok"]
    assert calls == ["123456"]
    # A fresh turn (fresh ctx) resolves again — the memo is per turn, not global.
    assert run(rs.get_risk_signals(ctx(), CLIENT, RANGE))["ok"]
    assert calls == ["123456", "123456"]


def test_resolver_errors_are_not_memoised(monkeypatch, overview_data):
    state = {"n": 0}

    def _fetch(settings, subject):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("replica hiccup")  # first call fails
        return resolved()

    monkeypatch.setattr(common, "_fetch_subject", _fetch)
    c = ctx()
    assert run(co.get_client_overview(c, CLIENT, RANGE))["error"]["code"] == "internal"
    # The failure was not memoised: the retry inside the same turn resolves.
    assert run(co.get_client_overview(c, CLIENT, RANGE))["ok"]
    assert state["n"] == 2


def test_shared_ip_leg_degrades_instead_of_failing_the_tool(subject_ok, signals_data, monkeypatch):
    """H3: the orders DB being unavailable must not hide alerts/case/tags."""

    def _boom(s, cid, f, t):
        raise RuntimeError("orders db unavailable")

    monkeypatch.setattr(rs, "_fetch_shared_ip", _boom)
    env = run(rs.get_risk_signals(ctx(), CLIENT, RANGE))
    assert env["ok"] is True
    d = env["data"]
    assert d["shared_ip"] is None
    assert len(d["alerts"]) == 500 and d["cases"] and d["crm_risk_tags"]
    assert any(c.startswith("shared-IP leg unavailable: internal") for c in env["definition"]["caveats"])
