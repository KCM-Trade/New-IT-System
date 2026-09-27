"""``get_economic_calendar`` tool (02 §12) — reads the cache, never fetches.

Pinned: window and importance filtering; days_ahead bounds; HK / MT / UTC
time triplets incl. US DST; `stale_since` when the newest good fetch is older
than 36h and "never" when the cache was never filled; `fred_api_key_missing`
when FRED has no key; certified envelope with source_url on every row.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.ai_agent.tools import economic_calendar as ec
from app.ai_agent.tools.common import CallerCtx
from app.core import ai_usage_db

NOW = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)
TODAY = date(2026, 9, 27)


def run(coro):
    return asyncio.run(coro)


def ctx(scope=None) -> CallerCtx:
    return CallerCtx(user_id=7, email="staff@kohleservices.com", role="user", allowed_modules=("ai",),
                     scope=scope, trace_id="t-1", settings=SimpleNamespace())


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_usage_db, "_DB_PATH", tmp_path / "ai_agent_cal_tool.db")
    ai_usage_db.init_ai_usage_db()
    ai_usage_db.replace_calendar_source("fomc", [
        {"event_date": "2026-10-28", "time_utc": "2026-10-28T18:00:00Z", "event": "FOMC rate decision", "importance": "high", "source_url": "https://fed/x"},
        {"event_date": "2026-12-09", "time_utc": "2026-12-09T19:00:00Z", "event": "FOMC rate decision (with economic projections)", "importance": "high", "source_url": "https://fed/x"},
    ], fetched_at="2026-09-27T06:00:00Z")
    ai_usage_db.replace_calendar_source("fred", [
        {"event_date": "2026-10-02", "time_utc": "2026-10-02T12:30:00Z", "event": "Non-farm Payrolls (NFP) / Employment Situation", "importance": "high", "source_url": "https://fred/1"},
        {"event_date": "2026-10-06", "time_utc": "2026-10-06T12:30:00Z", "event": "JOLTS", "importance": "medium", "source_url": "https://fred/2"},
        {"event_date": "2026-11-06", "time_utc": "2026-11-06T13:30:00Z", "event": "Non-farm Payrolls (NFP) / Employment Situation", "importance": "high", "source_url": "https://fred/3"},
    ], fetched_at="2026-09-27T06:00:00Z")
    ai_usage_db.record_calendar_source_status("fomc", "ok", now="2026-09-27T06:00:00Z")
    ai_usage_db.record_calendar_source_status("fred", "ok", now="2026-09-27T06:00:00Z")
    return ai_usage_db


def test_default_window_high_only(db):
    env = run(ec.get_economic_calendar(ctx(), today=TODAY, now_utc=NOW))
    assert env["ok"] is True and env["source"]["certified"] is True
    d = env["data"]
    assert d["from"] == "2026-09-27" and d["to"] == "2026-10-27"
    assert [r["event"][:4] for r in d["rows"]] == ["Non-"]       # NFP 10-02 only; JOLTS medium; FOMC 10-28 outside
    assert all(r["source_url"] for r in d["rows"])
    assert not any("stale_since" in c or "fred_api_key_missing" in c for c in env["definition"]["caveats"])
    assert env["source"]["as_of"] == "2026-09-27T06:00:00Z"


def test_importance_all_and_60_days(db):
    env = run(ec.get_economic_calendar(ctx(), days_ahead=60, importance="all", today=TODAY, now_utc=NOW))
    events = [r["event"] for r in env["data"]["rows"]]
    assert events[:3] == ["Non-farm Payrolls (NFP) / Employment Situation", "JOLTS", "FOMC rate decision"]
    assert "Non-farm Payrolls (NFP) / Employment Situation" == events[-1]  # 11-06, inside 60 days
    assert env["data"]["row_count"] == 4


def test_time_triplets_are_dst_aware(db):
    env = run(ec.get_economic_calendar(ctx(), days_ahead=60, importance="all", today=TODAY, now_utc=NOW))
    rows = {(r["date"], r["event"][:5]): r for r in env["data"]["rows"]}
    oct_nfp = rows[("2026-10-02", "Non-f")]
    assert oct_nfp["time_utc"] == "2026-10-02T12:30:00Z"
    assert oct_nfp["time_hk"] == "2026-10-02 20:30"
    assert oct_nfp["time_mt"] == "2026-10-02 15:30"   # MT = UTC+3 in summer
    nov_nfp = rows[("2026-11-06", "Non-f")]
    assert nov_nfp["time_utc"] == "2026-11-06T13:30:00Z"  # 08:30 EST
    assert nov_nfp["time_mt"] == "2026-11-06 15:30"       # MT = UTC+2 after the first Sunday of November
    assert nov_nfp["time_hk"] == "2026-11-06 21:30"


@pytest.mark.parametrize("days", [0, 61, "x"])
def test_days_ahead_bounds(db, days):
    env = run(ec.get_economic_calendar(ctx(), days_ahead=days, today=TODAY, now_utc=NOW))
    assert env["ok"] is False and env["error"]["code"] == "invalid_argument"


def test_bad_importance_and_countries(db):
    assert run(ec.get_economic_calendar(ctx(), importance="urgent", today=TODAY, now_utc=NOW))["error"]["code"] == "invalid_argument"
    assert run(ec.get_economic_calendar(ctx(), countries=[], today=TODAY, now_utc=NOW))["error"]["code"] == "invalid_argument"
    env = run(ec.get_economic_calendar(ctx(), countries=["us", "GB"], today=TODAY, now_utc=NOW))
    assert env["ok"] is True and env["data"]["countries"] == ["GB", "US"]
    assert any("['GB'] are not covered" in c for c in env["definition"]["caveats"])


def test_stale_since_after_36_hours(db):
    late = datetime(2026, 9, 29, 0, 0, tzinfo=timezone.utc)   # 42h after the 06:00Z fetch
    env = run(ec.get_economic_calendar(ctx(), today=date(2026, 9, 29), now_utc=late))
    assert any(c.startswith("stale_since: 2026-09-27T06:00:00Z") for c in env["definition"]["caveats"])


def test_fred_missing_key_caveat(db):
    db.record_calendar_source_status("fred", "no_key", now="2026-09-27T06:00:00Z", detail="FRED_API_KEY is not configured")
    env = run(ec.get_economic_calendar(ctx(), today=TODAY, now_utc=NOW))
    assert any(c.startswith("fred_api_key_missing") for c in env["definition"]["caveats"])
    assert env["data"]["sources"]["fred"]["status"] == "no_key"


def test_fred_failed_caveat_names_last_good_fetch(db):
    db.record_calendar_source_status("fred", "failed", now="2026-09-28T06:00:00Z", detail="HTTPStatusError: 400")
    env = run(ec.get_economic_calendar(ctx(), today=TODAY, now_utc=NOW))
    assert any(c.startswith("fred_unavailable") and "2026-09-27T06:00:00Z" in c for c in env["definition"]["caveats"])


def test_empty_cache_says_never(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_usage_db, "_DB_PATH", tmp_path / "empty.db")
    ai_usage_db.init_ai_usage_db()
    env = run(ec.get_economic_calendar(ctx(), today=TODAY, now_utc=NOW))
    assert env["ok"] is True and env["data"]["rows"] == []
    caveats = env["definition"]["caveats"]
    assert any(c.startswith("stale_since: never") for c in caveats)
    assert any(c.startswith("fred_api_key_missing") for c in caveats)


def test_scope_does_not_affect_the_calendar(db):
    env = run(ec.get_economic_calendar(ctx(scope=frozenset()), today=TODAY, now_utc=NOW))
    assert env["ok"] is True and env["data"]["row_count"] == 1
    assert env["scope"]["cids_applied"] == []
