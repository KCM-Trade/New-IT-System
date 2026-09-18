"""Wiring tests for OPT-0062 Intraday Return (rule_id 131-140).

Covers the layers around the detection service (which has its own suite in
test_rule_intraday_return_service.py): SQLite round trip + UPSERT + dedup
seed, stats extras, sort whitelist, seeded rules / subscriptions, config
CRUD, the /intraday-return/* routes, the mail source, and the scheduler job.

⚠ Every seeded timestamp derives from datetime.now() (OPT-0041 date rot:
append_scan_and_events purges scanned_at older than 30 days on every write).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import burst_open_scheduler as bs
from app.core import risk_monitor_db as rm_db
from app.services.alert_mail import intraday_return as ir_mail
from app.services.alert_mail import registry
from app.services import alert_mail_dispatcher as amd

NOW = datetime.now(timezone.utc).replace(microsecond=0)
TODAY = NOW.strftime("%Y-%m-%d")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "risk_monitor.db"
    monkeypatch.setattr(rm_db, "_DB_PATH", db_path)
    rm_db.init_risk_monitor_db()
    return db_path


@pytest.fixture
def client(temp_db) -> TestClient:
    app = FastAPI()
    from app.api.v1.routes.risk_monitor import router as risk_monitor_router
    app.include_router(risk_monitor_router, prefix="/api/v1")
    return TestClient(app)


def _alert(
    *, rule_id: int = 131, server: str = "MT5", login: int = 67044208,
    return_pct: float = 957.0, profit: float = 478.27, net_7d: float = 478.27,
    scanned_at: str = _iso(NOW),
) -> dict[str, Any]:
    return {
        "rule_id": rule_id, "rule_label": f"Rule {rule_id - 130} — tier",
        "server": server, "login": login, "symbol": "XAUUSD",
        "order_count": 88, "total_lots": 4.4, "orders": [],
        "first_open": _iso(NOW - timedelta(hours=5)), "last_open": _iso(NOW - timedelta(minutes=10)),
        "equity": 528.27, "balance": 528.27, "group": "KCM_VN_L4",
        "currency": "USD", "zipcode": "700000", "net_deposit_hist": 50.0,
        "scanned_at": scanned_at,
        "trading_day": TODAY, "prev_day_equity": 0.0, "deposits_in": 50.0,
        "credit_in": 0.0, "withdrawals_out": 0.0, "adj_excluded": 0.0,
        "initial_equity": 50.0, "equity_now": 528.27, "same_day_pnl": profit,
        "carried_float0": 0.0, "carried_now": 0.0, "carried_gain": 0.0,
        "intraday_profit": profit, "return_pct": return_pct,
        "peak_return_pct": return_pct, "net_7d": net_7d, "realized_7d": net_7d,
        "floating_all_now": 0.0, "flag_withdraw_gt_half_deposit": 0,
        "trades_today": 88, "lots_today": 4.4, "median_hold_sec": 630,
        "lock_pct": 88.0, "top_symbol": "XAUUSD",
    }


def _persist(alerts: list[dict]) -> None:
    rm_db.append_scan_and_events(
        scanned_at=_iso(NOW), scan_interval_min=5, accounts_scanned=len(alerts),
        suspicious_count=len(alerts), scan_time_ms=1, alerts=alerts,
    )


_RANGE = dict(since=_iso(NOW - timedelta(days=7)), until=_iso(NOW + timedelta(days=1)))


# ── (a) DB round trip + dedup seed ─────────────────────────────────────────

def test_db_round_trip_and_alerted_keys(temp_db):
    _persist([_alert()])

    entries, total = rm_db.query_alert_events(rule_id_min=131, rule_id_max=140, **_RANGE)
    assert total == 1
    e = entries[0]
    assert e["trading_day"] == TODAY
    assert e["initial_equity"] == pytest.approx(50.0)
    assert e["return_pct"] == pytest.approx(957.0)
    assert e["peak_return_pct"] == pytest.approx(957.0)
    assert e["net_7d"] == pytest.approx(478.27)
    assert e["trades_today"] == 88 and e["top_symbol"] == "XAUUSD"
    assert e["detail_updated_at"] == _iso(NOW)
    assert e["group"] == "KCM_VN_L4"

    keys = rm_db.get_intraday_return_alerted_keys(TODAY)
    assert keys == {("MT5", 67044208): {131: e["id"]}}
    assert rm_db.get_intraday_return_alerted_keys("1999-01-01") == {}

    after = rm_db.fetch_intraday_return_alerts_after(0)
    assert [x["id"] for x in after] == [e["id"]]
    assert after[0]["group"] == "KCM_VN_L4"
    assert rm_db.fetch_intraday_return_alerts_by_ids([e["id"]])[0]["login"] == 67044208
    assert len(rm_db.fetch_intraday_return_alerts_for_day(TODAY)) == 1
    assert rm_db.fetch_recent_intraday_return_alerts(limit=5)[0]["id"] == e["id"]
    # Other bands do not leak through the intraday fetchers.
    assert rm_db.fetch_recent_intraday_return_alerts(limit=5)[0]["rule_id"] == 131


# ── (b) UPSERT keeps peak as a high-water mark ─────────────────────────────

def test_update_refreshes_return_and_keeps_peak(temp_db):
    _persist([_alert(return_pct=400.0)])
    alert_id = rm_db.get_intraday_return_alerted_keys(TODAY)[("MT5", 67044208)][131]

    def _update(ret: float, equity: float) -> dict:
        u = _alert(return_pct=ret)
        u["peak_return_pct"] = None  # writer never sets it; MAX() in SQL does
        u["equity_now"] = equity
        u["equity"] = equity
        return {"alert_id": alert_id, **u}

    later = _iso(NOW + timedelta(minutes=5))
    assert rm_db.update_intraday_return_details([_update(300.0, 400.0)], updated_at=later) == 1
    row = rm_db.fetch_intraday_return_alerts_by_ids([alert_id])[0]
    assert row["return_pct"] == pytest.approx(300.0)
    assert row["peak_return_pct"] == pytest.approx(400.0)   # lower tick → peak kept
    assert row["equity"] == pytest.approx(400.0)            # ae.equity refreshed
    assert row["detail_updated_at"] == later

    rm_db.update_intraday_return_details([_update(650.0, 750.0)], updated_at=later)
    row = rm_db.fetch_intraday_return_alerts_by_ids([alert_id])[0]
    assert row["return_pct"] == pytest.approx(650.0)
    assert row["peak_return_pct"] == pytest.approx(650.0)   # higher tick → peak grows
    # Still exactly one alert row for the account/day.
    _, total = rm_db.query_alert_events(rule_id_min=131, rule_id_max=140, **_RANGE)
    assert total == 1
    assert rm_db.update_intraday_return_details([], updated_at=later) == 0


# ── (c) stats extras + (d) server-side sort ────────────────────────────────

def test_stats_extras_and_sort(temp_db):
    _persist([
        _alert(login=1, return_pct=120.0, profit=60.0, net_7d=10.0),
        _alert(login=2, return_pct=957.0, profit=478.27, net_7d=478.27),
        _alert(login=3, rule_id=132, return_pct=400.0, profit=200.0, net_7d=-5.0),
    ])
    extras = rm_db.intraday_return_stats_extras(**_RANGE)
    assert extras["max_peak_return_pct"] == pytest.approx(957.0)
    assert extras["sum_intraday_profit"] == pytest.approx(738.27)
    assert rm_db.intraday_return_stats_extras(server="MT4_Live", **_RANGE) == {
        "max_peak_return_pct": None, "sum_intraday_profit": None,
    }

    entries, _ = rm_db.query_alert_events(
        rule_id_min=131, rule_id_max=140, sort_by="return_pct", sort_order="desc", **_RANGE,
    )
    assert [e["login"] for e in entries] == [2, 3, 1]
    entries, _ = rm_db.query_alert_events(
        rule_id_min=131, rule_id_max=140, sort_by="net_7d", sort_order="asc", **_RANGE,
    )
    assert [e["login"] for e in entries] == [3, 1, 2]
    for col in ("return_pct", "net_7d", "peak_return_pct", "intraday_profit",
                "initial_equity", "trades_today", "lock_pct", "trading_day"):
        assert col in rm_db.SORTABLE_ALERT_COLS
        assert rm_db._SORT_COL_DB_NAME[col].startswith("ir.")


# ── (e) seeds ──────────────────────────────────────────────────────────────

def test_init_seeds_two_tiers_and_two_subscriptions(temp_db):
    cfg = rm_db.load_intraday_return_config()
    assert cfg["enabled"] is True
    assert [r["min_return_pct"] for r in cfg["rules"]] == [100.0, 300.0]
    assert all(r["min_initial_equity_usd"] == 50.0 and r["min_profit_usd"] == 30.0
               and r["net_window_days"] == 7 and r["include_deposits_in_base"] is True
               and r["min_lock_pct"] is None and r["max_median_hold_min"] is None
               for r in cfg["rules"])

    subs = rm_db.load_mail_subscriptions(module="intraday_return")
    assert [s["rule_ids"] for s in subs] == [[131], [132]]
    for s in subs:
        assert s["mail_to"] == "risk@kcmtrade.com"
        assert s["mail_cc"] == "kieran.xiang@kohleservices.com,lawrence.li@kohleservices.com"
        assert s["mode"] == "realtime" and s["enabled"]
        assert s["cooldown_min"] == 0
        # Cursor initialised at the current high-water mark (no replay).
        assert rm_db.get_mail_dispatch_cursor(int(s["id"])) == 0
    # The hedge seed (id=1) is untouched and still first.
    assert rm_db.load_mail_subscriptions(module="hedge_open")[0]["id"] == 1
    # Idempotent: a second init does not duplicate either seed.
    rm_db.init_risk_monitor_db()
    assert len(rm_db.load_mail_subscriptions(module="intraday_return")) == 2
    assert len(rm_db.load_intraday_return_config()["rules"]) == 2


# ── (f) config CRUD ────────────────────────────────────────────────────────

def test_config_save_load_round_trip(temp_db):
    rm_db.save_intraday_return_config(False, [
        {"name": "宽松档", "enabled": True, "min_return_pct": 80, "min_initial_equity_usd": 20,
         "min_profit_usd": 10, "min_net_7d_usd": -50, "net_window_days": 3,
         "include_deposits_in_base": False, "min_lock_pct": 30, "max_median_hold_min": 15,
         "lock_ratio_min": 0.8},
        {"name": "严格档", "enabled": False, "min_return_pct": 500,
         "min_lock_pct": None, "max_median_hold_min": ""},
    ])
    cfg = rm_db.load_intraday_return_config()
    assert cfg["enabled"] is False
    a, b = cfg["rules"]
    assert a["name"] == "宽松档" and a["min_return_pct"] == 80.0
    assert a["include_deposits_in_base"] is False and a["net_window_days"] == 3
    assert a["min_lock_pct"] == 30.0 and a["max_median_hold_min"] == 15.0
    assert a["lock_ratio_min"] == 0.8 and a["min_net_7d_usd"] == -50.0
    assert b["enabled"] is False and b["min_return_pct"] == 500.0
    assert b["min_lock_pct"] is None and b["max_median_hold_min"] is None
    # Defaults fill the knobs the second rule left out.
    assert b["min_initial_equity_usd"] == 50.0 and b["min_profit_usd"] == 30.0


# ── (g) HTTP routes ────────────────────────────────────────────────────────

def _rule(**over) -> dict:
    base = {"name": "档位", "enabled": True, "min_return_pct": 100,
            "min_initial_equity_usd": 50, "min_profit_usd": 30, "min_net_7d_usd": 0,
            "net_window_days": 7, "include_deposits_in_base": True,
            "min_lock_pct": None, "max_median_hold_min": None, "lock_ratio_min": 0.5}
    base.update(over)
    return base


def test_routes_config_get_post_validation(client):
    r = client.get("/api/v1/risk-monitor/intraday-return/config")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True and len(body["rules"]) == 2
    assert body["rules"][1]["min_return_pct"] == 300.0

    r = client.post("/api/v1/risk-monitor/intraday-return/config",
                    json={"enabled": True, "rules": []})
    assert r.status_code == 400
    r = client.post("/api/v1/risk-monitor/intraday-return/config",
                    json={"enabled": True, "rules": [_rule(name=f"r{i}") for i in range(11)]})
    assert r.status_code == 400
    # Pydantic bounds: min_return_pct below 10 is rejected.
    r = client.post("/api/v1/risk-monitor/intraday-return/config",
                    json={"enabled": True, "rules": [_rule(min_return_pct=5)]})
    assert r.status_code == 422

    r = client.post("/api/v1/risk-monitor/intraday-return/config", json={
        "enabled": False,
        "rules": [_rule(name="only", min_return_pct=250, min_lock_pct=40)],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["rules"][0]["min_return_pct"] == 250.0
    assert body["rules"][0]["min_lock_pct"] == 40.0
    assert body["rules"][0]["max_median_hold_min"] is None
    assert len(rm_db.load_intraday_return_config()["rules"]) == 1


def test_routes_alerts_stats_export(client):
    _persist([_alert(login=1, return_pct=120.0, profit=60.0),
              _alert(login=2, return_pct=957.0, profit=478.27)])
    # A neighbouring band must not leak in.
    _persist([{**_alert(login=3), "rule_id": 121, "rule_label": "rebate"}])

    r = client.get("/api/v1/risk-monitor/intraday-return/alerts",
                   params={**_RANGE, "sort_by": "return_pct", "sort_order": "desc"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert [e["login"] for e in body["entries"]] == [2, 1]
    assert body["entries"][0]["trading_day"] == TODAY
    assert body["entries"][0]["carried_gain"] == 0.0

    r = client.get("/api/v1/risk-monitor/intraday-return/alerts/stats", params=_RANGE)
    assert r.status_code == 200
    stats = r.json()
    assert stats["suspicious_count"] == 2 and stats["event_count"] == 2
    assert stats["max_peak_return_pct"] == pytest.approx(957.0)
    assert stats["sum_intraday_profit"] == pytest.approx(538.27)
    assert stats["by_rule"][0]["rule_id"] == 131

    r = client.get("/api/v1/risk-monitor/intraday-return/alerts/export", params=_RANGE)
    assert r.status_code == 200
    lines = r.text.lstrip("﻿").splitlines()
    assert lines[0].startswith("rule_label,scanned_at,trading_day,server")
    assert len(lines) == 3
    assert "957.0" in lines[1] or "957.0" in lines[2]


def test_route_scan_now(client, monkeypatch):
    from app.api.v1.routes import risk_monitor as routes
    calls: list[int] = []

    def fake_trigger():
        calls.append(1)
        return {"alerts": [{}, {}], "updates": [{}], "accounts_evaluated": 1500,
                "trading_day": TODAY, "scan_time_ms": 42, "scanned_at": _iso(NOW)}
    monkeypatch.setattr(routes, "trigger_intraday_return_scan_now", fake_trigger)
    r = client.post("/api/v1/risk-monitor/intraday-return/scan-now")
    assert r.status_code == 200
    assert r.json() == {"alerts": 2, "updates": 1, "accounts_evaluated": 1500,
                        "trading_day": TODAY, "scan_time_ms": 42, "scanned_at": _iso(NOW),
                        "status": "ok", "servers_failed": [], "skipped_reason": None}
    assert calls == [1]

    monkeypatch.setattr(routes, "trigger_intraday_return_scan_now", lambda: None)
    assert client.post("/api/v1/risk-monitor/intraday-return/scan-now").status_code == 409


# ── (h) mail source ────────────────────────────────────────────────────────

def test_mail_source_registered_and_match_context():
    src = registry.get_source("intraday_return")
    assert src is not None and src["rule_id_range"] == (131, 140)
    registry.validate_rule_ids("intraday_return", [131, 140])
    with pytest.raises(ValueError):
        registry.validate_rule_ids("intraday_return", [130])
    assert ir_mail.match_context({"return_pct": 120.0, "initial_equity": 50.0}) == {
        "return_pct": 120.0, "initial_equity": 50.0,
    }
    assert ir_mail.match_context({"login": 1}) is None          # detail row missing
    assert ir_mail.FIELD_GETTERS["trades_today"]({"trades_today": "88"}) == 88
    assert ir_mail.FIELD_GETTERS["return_pct"]({}) is None


def test_mail_template_and_rules_loader(temp_db):
    rules = ir_mail.rules_loader()
    assert [r["id"] for r in rules] == [131, 132]
    assert rules[1]["params"]["min_return_pct"] == 300.0

    alert = dict(ir_mail.TEST_SEND_SAMPLE_ALERT)
    alert["flag_withdraw_gt_half_deposit"] = 1
    alert["withdrawals_out"] = 40.0
    alert["return_pct"] = 500.0  # below the stored peak → "(peak today 957%)"
    subject, body = ir_mail.build_intraday_return_digest_email(
        [(alert, {"labels": ["return_pct >= 300"]})],
        subscription={"name": "即日高收益 ≥300%", "updated_at": "2026-09-18"},
        sibling_map={alert["id"]: ["5-67044209"]},
        test=True,
    )
    assert subject == "[TEST] [风控告警] 即日高收益 Intraday Return — 1 个账户"
    assert "67044208" in body and "Intraday Return · 即日高收益" in body
    assert "peak today 957%" in body
    assert "Withdrawal flag" in body and "40.00" in body
    assert "5-67044209" in body
    assert "478.27" in body


def test_mail_test_send_uses_fallback_sample(temp_db):
    sub = rm_db.load_mail_subscriptions(module="intraday_return")[0]
    sent: list[dict] = []

    def capture(*, subject, body, to, cc=None):
        sent.append({"subject": subject, "body": body, "to": to, "cc": cc})
    result = amd.send_test_email_for_subscription(int(sub["id"]), send_fn=capture)
    assert result["used_fallback"] is True
    assert result["alert_id"] == ir_mail.TEST_SEND_SAMPLE_ALERT["id"]
    assert len(sent) == 1
    assert sent[0]["subject"].startswith("[TEST] [风控告警] 即日高收益 Intraday Return")
    assert "67044208" in sent[0]["body"]
    assert ir_mail.TEST_SEND_SAMPLE_ALERT["return_pct"] == 957.0  # sample not mutated


def test_mail_dispatch_matches_persisted_alert(temp_db):
    """End to end: a persisted 131 alert lands in the 131 subscription's digest
    and NOT in the 132 one (rule_ids narrowing)."""
    _persist([_alert()])
    sent: list[dict] = []

    def capture(subject, body, to, cc=None):
        sent.append({"subject": subject, "body": body, "to": to, "cc": cc})
    summary = amd.dispatch_alert_mails(now=NOW + timedelta(minutes=1), send_fn=capture)
    assert summary["composed"] == 1
    assert len(sent) == 1
    assert sent[0]["to"] == "risk@kcmtrade.com"
    assert "lawrence.li@kohleservices.com" in (sent[0]["cc"] or "")
    assert "67044208" in sent[0]["body"]
    assert sent[0]["subject"].startswith("[风控告警] 即日高收益 Intraday Return")


# ── (i) scheduler job ──────────────────────────────────────────────────────

def test_scheduler_job_persists_alerts_and_applies_updates(temp_db, monkeypatch):
    from app.services import rule_intraday_return_service as svc

    # Existing row from an earlier tick, which this tick refreshes.
    _persist([_alert(login=1, return_pct=150.0)])
    existing_id = rm_db.get_intraday_return_alerted_keys(TODAY)[("MT5", 1)][131]

    seen: dict[str, Any] = {}

    def fake_scan(settings, *, rules, alerted_keys_fetcher=None, now=None):
        seen["rules"] = rules
        seen["alerted"] = alerted_keys_fetcher(TODAY) if alerted_keys_fetcher else None
        update = _alert(login=1, return_pct=220.0)
        update["peak_return_pct"] = None
        return {
            "alerts": [_alert(login=2, rule_id=132, return_pct=420.0)],
            "updates": [{"alert_id": existing_id, **update}],
            "trading_day": TODAY, "accounts_evaluated": 3,
            "scan_time_ms": 7, "scanned_at": _iso(NOW + timedelta(minutes=5)),
        }
    monkeypatch.setattr(svc, "scan_intraday_return", fake_scan)
    monkeypatch.setattr(bs, "_backfill_alert_user_ids", lambda settings, alerts: None)

    result = bs._run_intraday_return_scan()
    assert result is not None and len(result["alerts"]) == 1
    assert [r["min_return_pct"] for r in seen["rules"]] == [100.0, 300.0]
    assert seen["alerted"] == {("MT5", 1): {131: existing_id}}

    keys = rm_db.get_intraday_return_alerted_keys(TODAY)
    assert keys[("MT5", 2)] == {132: keys[("MT5", 2)][132]}
    refreshed = rm_db.fetch_intraday_return_alerts_by_ids([existing_id])[0]
    assert refreshed["return_pct"] == pytest.approx(220.0)
    assert refreshed["peak_return_pct"] == pytest.approx(220.0)
    _, total = rm_db.query_alert_events(rule_id_min=131, rule_id_max=140, **_RANGE)
    assert total == 2

    # Disabled config → the service is never called.
    rm_db.save_intraday_return_config(False, [{"name": "x", "min_return_pct": 100}])
    calls: list[int] = []
    monkeypatch.setattr(svc, "scan_intraday_return", lambda *a, **k: calls.append(1))
    assert bs._run_intraday_return_scan() is None
    assert calls == []

    # trigger_* returns the result through the lock; a held lock skips the
    # scheduled variant with a DEBUG line only.
    monkeypatch.setattr(bs, "_run_intraday_return_scan", lambda: {"alerts": []})
    assert bs.trigger_intraday_return_scan_now() == {"alerts": []}
    assert bs._intraday_return_lock.acquire(blocking=False)
    try:
        bs._locked_intraday_return_scan()  # must not block or raise
    finally:
        bs._intraday_return_lock.release()


def test_scheduler_interval_env_and_job_registration(temp_db, monkeypatch):
    monkeypatch.setenv("INTRADAY_RETURN_INTERVAL_MIN", "garbage")
    assert bs._intraday_return_interval_min() == 5
    monkeypatch.setenv("INTRADAY_RETURN_INTERVAL_MIN", "0")
    assert bs._intraday_return_interval_min() == 5
    monkeypatch.setenv("INTRADAY_RETURN_INTERVAL_MIN", "3")
    assert bs._intraday_return_interval_min() == 3

    from unittest.mock import MagicMock, patch
    for flag, expect in (("false", False), ("true", True), (None, True), ("FALSE", False)):
        if flag is None:
            monkeypatch.delenv("INTRADAY_RETURN_SCAN_ENABLED", raising=False)
        else:
            monkeypatch.setenv("INTRADAY_RETURN_SCAN_ENABLED", flag)
        monkeypatch.setenv("BURST_SCAN_ENABLED", "true")
        monkeypatch.setenv("REBATE_ARB_SCAN_ENABLED", "false")
        monkeypatch.setenv("GAP_TRADE_SCAN_ENABLED", "false")
        monkeypatch.setattr(bs, "_scheduler", None)
        monkeypatch.setattr(bs, "_startup_scan_thread", None)
        with patch.object(bs, "BackgroundScheduler") as sched_cls, \
                patch.object(bs.threading, "Thread") as thread_cls:
            sched_cls.return_value = MagicMock()
            thread_cls.return_value = MagicMock()
            bs.start_burst_scheduler()
            ids = [c.kwargs.get("id") for c in sched_cls.return_value.add_job.call_args_list]
        assert (bs.INTRADAY_RETURN_JOB_ID in ids) is expect, (flag, ids)
        if expect:
            call = next(c for c in sched_cls.return_value.add_job.call_args_list
                        if c.kwargs.get("id") == bs.INTRADAY_RETURN_JOB_ID)
            assert call.kwargs["max_instances"] == 1 and call.kwargs["coalesce"] is True
            assert call.kwargs["misfire_grace_time"] == 60
