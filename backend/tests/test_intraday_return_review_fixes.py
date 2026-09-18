"""OPT-0062 cold-review fixes (2026-09-18) — each test pins one finding.

R1  tier decay: an account on file at a higher tier must not spawn a lower-
    tier row (and mail) while it gives profit back
R2  stale rows: rows already on file are refreshed even when the account no
    longer matches any rule
R3  DST: MT5 FILETIME windows follow the MT wall clock (GMT+2 in winter)
R4  cross-process dedup: persist_intraday_return_tick re-checks the day keys
    inside the write transaction and demotes duplicates to updates
R5  stale day boundary → tick skipped, nothing written
R6  partial close: the remainder in the positions snapshot is not counted
    twice
R7  currency lookup failure → tick skipped (never evaluate CEN as USD)
R8  one server failing → status "partial" + servers_failed, others evaluated
R9  /alerts on this tab selects by MT trading day, not first-hit scanned_at

Every timestamp derives from datetime.now() (OPT-0041).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.core import risk_monitor_db as rm_db
from app.services import rule_intraday_return_service as svc
from app.services.rule_intraday_return_service import (
    MT_SERVER_TZ,
    assemble_mt5_accounts,
    normalize_rules,
    rule_intraday_return_detect,
)

NOW_UTC = datetime.now(timezone.utc).replace(microsecond=0)
NOW_LOCAL = NOW_UTC.astimezone(MT_SERVER_TZ).replace(tzinfo=None)
DAY_START = NOW_LOCAL.replace(hour=0, minute=0, second=0)
SCANNED_AT = NOW_UTC.isoformat(timespec="seconds").replace("+00:00", "Z")
TRADING_DAY = DAY_START.date().isoformat()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "risk_monitor.db"
    monkeypatch.setattr(rm_db, "_DB_PATH", db_path)
    rm_db.init_risk_monitor_db()
    return db_path


def _rules(*pcts):
    return normalize_rules([{
        "name": f"≥{p}%", "enabled": True, "min_return_pct": p,
        "min_initial_equity_usd": 50.0, "min_profit_usd": 30.0,
        "min_net_7d_usd": 0.0, "net_window_days": 7,
        "include_deposits_in_base": True, "lock_ratio_min": 0.5,
    } for p in pcts])


def _account(*, same_day_pnl: float, login: int = 60011522) -> dict[str, Any]:
    raw = {
        "prev_eq": 100.0, "prev_bal": 100.0, "prev_credit": 0.0,
        "dep_in": 0.0, "cred_in": 0.0, "withdrawals_out": 0.0, "adj_excluded": 0.0,
        "same_day_pnl": same_day_pnl, "carried_now": 0.0, "realized_7d": same_day_pnl,
        "floating_all_now": 0.0, "balance_now": 100.0 + same_day_pnl, "credit_now": 0.0,
    }
    return {
        "server": "MT5", "login": login, "currency": "USD", "group": "g", "zipcode": None,
        "raw": raw, "raw_realized_7d_by_window": {7: same_day_pnl},
        "positions": [{
            "symbol": "XAUUSD", "direction": "B", "lots": 1.0,
            "open_time": DAY_START + timedelta(hours=1),
            "close_time": DAY_START + timedelta(hours=2),
        }],
        "day_start": DAY_START, "now_local": NOW_LOCAL,
    }


def _detect(accounts, rules, alerted=None):
    return rule_intraday_return_detect(
        accounts, rules, alerted=alerted, scanned_at=SCANNED_AT, trading_day=TRADING_DAY,
    )


# ── R1 tier decay ───────────────────────────────────────────────────────

def test_decay_from_higher_tier_does_not_open_lower_tier_row():
    rules = _rules(100, 300)
    # 350% earlier today → row 132 on file; now back to 150%.
    alerts, updates = _detect(
        [_account(same_day_pnl=150.0)], rules, alerted={("MT5", 60011522): {132: 77}},
    )
    assert alerts == []
    assert [u["alert_id"] for u in updates] == [77]
    assert updates[0]["return_pct"] == pytest.approx(150.0)


def test_escalation_still_opens_the_higher_tier():
    rules = _rules(100, 300)
    alerts, updates = _detect(
        [_account(same_day_pnl=500.0)], rules, alerted={("MT5", 60011522): {131: 55}},
    )
    assert [a["rule_id"] for a in alerts] == [132]
    assert [u["alert_id"] for u in updates] == [55]


# ── R2 stale rows refreshed ──────────────────────────────────────────────

def test_row_on_file_is_refreshed_when_account_no_longer_matches():
    rules = _rules(100)
    alerts, updates = _detect(
        [_account(same_day_pnl=-40.0)], rules, alerted={("MT5", 60011522): {131: 9}},
    )
    assert alerts == []
    assert len(updates) == 1 and updates[0]["alert_id"] == 9
    assert updates[0]["return_pct"] == pytest.approx(-40.0)
    assert updates[0]["intraday_profit"] == pytest.approx(-40.0)


# ── R3 DST-aware FILETIME ────────────────────────────────────────────────

def test_filetime_follows_mt_wall_clock_dst():
    """Winter wall clock is GMT+2, summer GMT+3 (probed on mt5_deals rows)."""
    winter = datetime(NOW_UTC.year, 1, 15, 0, 0, 0)
    summer = datetime(NOW_UTC.year, 7, 15, 0, 0, 0)
    ft = svc._filetime
    winter_utc = (ft(winter) // 10_000_000) - 11644473600
    summer_utc = (ft(summer) // 10_000_000) - 11644473600
    assert winter_utc == int(datetime(NOW_UTC.year, 1, 14, 22, 0, tzinfo=timezone.utc).timestamp())
    assert summer_utc == int(datetime(NOW_UTC.year, 7, 14, 21, 0, tzinfo=timezone.utc).timestamp())
    # round trip through the helper pair
    assert svc._utc_to_local(svc._local_to_utc(winter)) == winter


# ── R4 cross-process dedup in the persist layer ─────────────────────────

def _alert(rule_id: int = 131, login: int = 67044208, return_pct: float = 957.0) -> dict[str, Any]:
    return {
        "rule_id": rule_id, "rule_label": "Rule 1 — tier", "server": "MT5", "login": login,
        "symbol": "XAUUSD", "order_count": 3, "total_lots": 0.3, "orders": [],
        "first_open": _iso(NOW_UTC - timedelta(hours=3)), "last_open": _iso(NOW_UTC),
        "equity": 528.0, "balance": 528.0, "group": "g", "currency": "USD", "zipcode": None,
        "scanned_at": SCANNED_AT, "trading_day": TRADING_DAY,
        "prev_day_equity": 0.0, "deposits_in": 50.0, "credit_in": 0.0, "withdrawals_out": 0.0,
        "adj_excluded": 0.0, "initial_equity": 50.0, "equity_now": 528.0, "same_day_pnl": 478.0,
        "carried_float0": 0.0, "carried_now": 0.0, "carried_gain": 0.0, "intraday_profit": 478.0,
        "return_pct": return_pct, "peak_return_pct": return_pct, "net_7d": 478.0,
        "realized_7d": 478.0, "floating_all_now": 0.0, "flag_withdraw_gt_half_deposit": 0,
        "trades_today": 3, "lots_today": 0.3, "median_hold_sec": 60, "lock_pct": 0.0,
        "top_symbol": "XAUUSD",
    }


def test_persist_tick_demotes_duplicate_alert_to_update(temp_db):
    kw = dict(scanned_at=SCANNED_AT, scan_interval_min=5, accounts_scanned=1, scan_time_ms=1)
    first = rm_db.persist_intraday_return_tick(alerts=[_alert()], updates=[], **kw)
    assert first == {"inserted": 1, "refreshed": 0, "demoted": 0}
    # A second writer (scan-now on another worker) computed the same alert
    # from the same stale snapshot — it must become an update, not a row.
    second = rm_db.persist_intraday_return_tick(
        alerts=[_alert(return_pct=1200.0)], updates=[], **kw,
    )
    assert second == {"inserted": 0, "refreshed": 1, "demoted": 1}
    rows = rm_db.fetch_recent_intraday_return_alerts()
    assert len(rows) == 1
    assert rows[0]["return_pct"] == pytest.approx(1200.0)
    assert rows[0]["peak_return_pct"] == pytest.approx(1200.0)
    assert rm_db.get_intraday_return_alerted_keys(TRADING_DAY) == {("MT5", 67044208): {131: rows[0]["id"]}}


# ── R5 stale day boundary → skipped ─────────────────────────────────────

class _FakeConn:
    def close(self):
        pass


def test_stale_mt5_daily_skips_the_tick(monkeypatch):
    monkeypatch.setattr(svc, "_get_connection", lambda settings: _FakeConn())
    monkeypatch.setattr(svc, "_query_trading_day_start", lambda conn: DAY_START - timedelta(days=2))
    called = []
    monkeypatch.setattr(svc, "_prepare_server", lambda *a, **k: called.append(1) or [])
    res = svc.scan_intraday_return(object(), rules=_rules(100), now=NOW_UTC)
    assert res["status"] == "skipped"
    assert "stale" in res["skipped_reason"]
    assert res["alerts"] == [] and res["updates"] == []
    assert called == []


# ── R6 partial close not double counted ─────────────────────────────────

def test_partial_close_counts_the_position_once():
    t = DAY_START + timedelta(hours=1)
    deals = [
        {"login": 1, "deal": 1, "position_id": 10, "action": 0, "entry": 0, "symbol": "XAUUSD",
         "lots": 1.0, "profit": 0.0, "storage": 0.0, "commission": 0.0, "comment": "", "time_local": t},
        {"login": 1, "deal": 2, "position_id": 10, "action": 1, "entry": 1, "symbol": "XAUUSD",
         "lots": 0.5, "profit": 20.0, "storage": 0.0, "commission": 0.0, "comment": "",
         "time_local": t + timedelta(minutes=30)},
    ]
    positions = [{"login": 1, "position_id": 10, "symbol": "XAUUSD", "action": 0, "lots": 0.5,
                  "profit": 15.0, "storage": 0.0, "open_time_local": t}]
    acc = assemble_mt5_accounts(deals=deals, positions=positions, day_start=DAY_START, now_local=NOW_LOCAL)
    assert len(acc[1]["positions"]) == 1
    assert acc[1]["raw"]["same_day_pnl"] == pytest.approx(35.0)


# ── R7 currency lookup failure → skipped ────────────────────────────────

def test_empty_currency_map_skips_the_tick(monkeypatch):
    monkeypatch.setattr(svc, "_get_connection", lambda settings: _FakeConn())
    monkeypatch.setattr(svc, "_query_trading_day_start", lambda conn: DAY_START)
    monkeypatch.setattr(svc, "_prepare_server", lambda conn, srv, **k: [_account(same_day_pnl=500.0)] if srv["type"] == "mt5" else [])
    monkeypatch.setattr(svc, "get_account_info_map", lambda conn, rows: {})
    res = svc.scan_intraday_return(object(), rules=_rules(100), now=NOW_UTC)
    assert res["status"] == "skipped"
    assert "currency" in res["skipped_reason"]
    assert res["alerts"] == []


# ── R8 one server failing → partial ─────────────────────────────────────

def test_one_server_failure_is_reported_as_partial(monkeypatch):
    monkeypatch.setattr(svc, "_get_connection", lambda settings: _FakeConn())
    monkeypatch.setattr(svc, "_query_trading_day_start", lambda conn: DAY_START)

    def prepare(conn, srv, **k):
        if srv["label"] == "MT4_Live2":
            raise RuntimeError("boom")
        return [_account(same_day_pnl=500.0)] if srv["type"] == "mt5" else []
    monkeypatch.setattr(svc, "_prepare_server", prepare)
    monkeypatch.setattr(svc, "get_account_info_map", lambda conn, rows: {"5-60011522": {"currency": "USD"}})
    monkeypatch.setattr(svc, "get_net_deposit_hist_map", lambda conn, alerts: {})
    res = svc.scan_intraday_return(object(), rules=_rules(100), now=NOW_UTC)
    assert res["status"] == "partial"
    assert res["servers_failed"] == ["MT4_Live2"]
    assert [a["login"] for a in res["alerts"]] == [60011522]


# ── R9 time filter by MT trading day ────────────────────────────────────

def test_alerts_filter_by_trading_day_not_first_hit(temp_db):
    old_hit = _iso(NOW_UTC - timedelta(hours=9))
    a = _alert()
    a["scanned_at"] = old_hit
    rm_db.append_scan_and_events(
        scanned_at=old_hit, scan_interval_min=5, accounts_scanned=1,
        suspicious_count=1, scan_time_ms=1, alerts=[a],
    )
    since, until = _iso(NOW_UTC - timedelta(hours=4)), _iso(NOW_UTC + timedelta(minutes=1))
    # by scanned_at the 9h-old first hit is outside "last 4h"
    _, by_scan = rm_db.query_alert_events(since=since, until=until, rule_id_min=131, rule_id_max=140)
    assert by_scan == 0
    # by trading_day it is today's row and stays visible
    _, by_day = rm_db.query_alert_events(
        since=since, until=until, rule_id_min=131, rule_id_max=140, time_field="trading_day",
    )
    assert by_day == 1
    assert rm_db.intraday_return_stats_extras(since, until)["max_peak_return_pct"] == pytest.approx(957.0)
