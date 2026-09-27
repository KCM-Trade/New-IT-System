"""econ_calendar_service + the cache tables in ai_usage_db (OPT-0065 §12).

Pinned: the FOMC parser on a trimmed REAL snippet of the Fed page (two-day
meetings dated on the second day, `*` = projections, cross-month "Jan/Feb"
+ "31-1" → Feb 1, "(notation vote)" skipped, DST-aware 14:00 ET → UTC); the
FRED name→importance map and JSON parse; refresh semantics — a failing source
keeps its previous rows and records `failed` with `ok_at` untouched; no key →
`no_key` status and no FRED rows; the api_key never reaches the status text.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.core import ai_usage_db
from app.services import econ_calendar_service as ecs

FIXTURE = Path(__file__).parent / "fixtures" / "fomc_calendar_snippet.html"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_usage_db, "_DB_PATH", tmp_path / "ai_agent_cal.db")
    ai_usage_db.init_ai_usage_db()
    return ai_usage_db


# ── FOMC ─────────────────────────────────────────────────────────────────────

def test_fomc_parser_on_the_real_snippet():
    rows = ecs.parse_fomc_html(FIXTURE.read_text(), years={2026})
    dates = [r.event_date for r in rows]
    assert dates == ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16",
                     "2026-10-28", "2026-12-09"]
    proj = [r.event_date for r in rows if "projections" in r.event]
    assert proj == ["2026-03-18", "2026-06-17", "2026-09-16", "2026-12-09"]
    assert all(r.importance == "high" and r.country == "US" and r.source == "fomc" for r in rows)
    assert all(r.source_url == ecs.FOMC_URL for r in rows)


def test_fomc_statement_time_is_14_00_et_dst_aware():
    rows = {r.event_date: r for r in ecs.parse_fomc_html(FIXTURE.read_text(), years={2026})}
    assert rows["2026-01-28"].time_utc == "2026-01-28T19:00:00Z"  # EST
    assert rows["2026-07-29"].time_utc == "2026-07-29T18:00:00Z"  # EDT


def test_fomc_cross_month_meeting_and_notation_vote():
    rows = ecs.parse_fomc_html(FIXTURE.read_text(), years={2023, 2025})
    dates = [r.event_date for r in rows]
    assert "2023-02-01" in dates            # "Jan/Feb" + "31-1"
    assert "2023-11-01" in dates            # "Oct/Nov" + "31-1"
    assert not any(d.startswith("2025-08") for d in dates)  # "22 (notation vote)" skipped
    assert len([d for d in dates if d.startswith("2025")]) == 8


def test_fomc_parse_entry_edge_cases():
    assert ecs._parse_fomc_entry(2026, "March", "17-18*") == (date(2026, 3, 18), True)
    assert ecs._parse_fomc_entry(2026, "May", "6") == (date(2026, 5, 6), False)
    assert ecs._parse_fomc_entry(2026, "Nonmonth", "6-7") is None
    assert ecs._parse_fomc_entry(2026, "May", "n/a") is None


def test_fetch_fomc_raises_on_zero_meetings():
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text="<html></html>")))
    with pytest.raises(ValueError):
        ecs.fetch_fomc(client, today=date(2026, 9, 27))


# ── FRED ─────────────────────────────────────────────────────────────────────

FRED_PAYLOAD = {
    "release_dates": [
        {"release_id": 50, "release_name": "Employment Situation", "date": "2026-10-02"},
        {"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-10-14"},
        {"release_id": 53, "release_name": "Gross Domestic Product (Advance Estimate)", "date": "2026-10-29"},
        {"release_id": 54, "release_name": "Personal Income and Outlays", "date": "2026-10-30"},
        {"release_id": 192, "release_name": "Job Openings and Labor Turnover Survey", "date": "2026-10-06"},
        {"release_id": 999, "release_name": "Weekly Natural Gas Storage", "date": "2026-10-01"},
        {"release_id": 50, "release_name": "Employment Situation", "date": "2026-10-02"},  # duplicate
        {"release_id": 10, "release_name": "Consumer Price Index", "date": "not-a-date"},
    ]
}


def test_fred_map_and_parse():
    rows = ecs.parse_fred_json(FRED_PAYLOAD)
    by = {(r.event_date, r.event): r for r in rows}
    assert ("2026-10-02", "Non-farm Payrolls (NFP) / Employment Situation") in by
    assert by[("2026-10-29", "GDP")].importance == "high"       # prefix match despite "(Advance Estimate)"
    assert by[("2026-10-06", "JOLTS")].importance == "medium"
    assert not any("Natural Gas" in r.event for r in rows)     # unmapped → dropped
    assert len([r for r in rows if r.event.startswith("Non-farm")]) == 1  # de-duplicated
    assert len(rows) == 5
    assert by[("2026-10-02", "Non-farm Payrolls (NFP) / Employment Situation")].time_utc == "2026-10-02T12:30:00Z"  # EDT
    assert all(r.source == "fred" and r.source_url.startswith("https://fred.stlouisfed.org/") for r in rows)


def test_fred_url_carries_key_window_and_no_data_flag():
    url = ecs.build_fred_url("abc", today=date(2026, 9, 27), lookahead_days=10)
    assert url.startswith(ecs.FRED_RELEASES_DATES_URL + "?api_key=abc&file_type=json")
    assert "include_release_dates_with_no_data=true" in url
    assert "realtime_start=2026-09-27&realtime_end=2026-10-07" in url


def test_fetch_fred_bad_key_raises_like_a_network_error():
    def handler(req):
        return httpx.Response(400, json={"error_code": 400, "error_message": "Bad Request. The value for variable api_key ..."})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        ecs.fetch_fred(client, "test", today=date(2026, 9, 27))


# ── refresh + cache ──────────────────────────────────────────────────────────

def _client_factory(fomc_status=200, fred_status=200, fomc_text=None, fred_json=None):
    def handler(req: httpx.Request):
        if "federalreserve.gov" in req.url.host:
            return httpx.Response(fomc_status, text=fomc_text if fomc_text is not None else FIXTURE.read_text())
        if "stlouisfed.org" in req.url.host:
            return httpx.Response(fred_status, json=fred_json if fred_json is not None else FRED_PAYLOAD)
        return httpx.Response(404)

    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


def test_refresh_without_key_loads_fomc_only_and_records_no_key(db):
    summary = ecs.refresh_calendar(SimpleNamespace(FRED_API_KEY=""), today=date(2026, 9, 27), client_factory=_client_factory())
    assert summary["fomc"]["status"] == "ok" and summary["fomc"]["rows"] == 8  # 2026 panel only (fixture has no 2027)
    assert summary["fred"] == {"status": "no_key"}
    status = db.calendar_status()
    assert status["fomc"]["status"] == "ok" and status["fomc"]["ok_at"] is not None
    assert status["fred"]["status"] == "no_key" and status["fred"]["ok_at"] is None
    rows = db.read_calendar("2026-09-27", "2026-12-31", countries=["US"], importance="high")
    assert [r["event_date"] for r in rows] == ["2026-10-28", "2026-12-09"]
    assert db.calendar_is_empty() is False


def test_refresh_with_key_loads_both_and_filters_by_importance(db):
    ecs.refresh_calendar(SimpleNamespace(FRED_API_KEY="k" * 32), today=date(2026, 9, 27), client_factory=_client_factory())
    high = db.read_calendar("2026-10-01", "2026-10-31", importance="high")
    allr = db.read_calendar("2026-10-01", "2026-10-31", importance="all")
    assert {r["event"] for r in high} >= {"CPI", "GDP", "FOMC rate decision"}
    assert "JOLTS" not in {r["event"] for r in high}
    assert "JOLTS" in {r["event"] for r in allr}
    assert high == sorted(high, key=lambda r: (r["event_date"], r["time_utc"] or "", r["event"]))


def test_a_failing_source_keeps_its_previous_rows_and_ok_at(db):
    ecs.refresh_calendar(SimpleNamespace(FRED_API_KEY="k" * 32), today=date(2026, 9, 27), client_factory=_client_factory())
    first = db.calendar_status()
    before = db.read_calendar("2026-01-01", "2027-12-31", importance="all")
    # second run: FOMC page 503, FRED 400 (bad key shape)
    summary = ecs.refresh_calendar(
        SimpleNamespace(FRED_API_KEY="k" * 32), today=date(2026, 9, 28),
        client_factory=_client_factory(fomc_status=503, fred_status=400, fred_json={"error_code": 400}),
    )
    assert summary["fomc"]["status"] == "failed" and summary["fred"]["status"] == "failed"
    after = db.read_calendar("2026-01-01", "2027-12-31", importance="all")
    assert after == before                                   # nothing lost
    status = db.calendar_status()
    assert status["fomc"]["status"] == "failed" and status["fomc"]["ok_at"] == first["fomc"]["ok_at"]
    assert status["fred"]["ok_at"] == first["fred"]["ok_at"]
    assert status["fred"]["fetched_at"] >= first["fred"]["fetched_at"]


def test_status_detail_never_contains_the_api_key(db):
    key = "s3cr3tkey" + "x" * 23

    def handler(req):
        raise httpx.ConnectError(f"boom {req.url}", request=req)

    ecs.refresh_calendar(SimpleNamespace(FRED_API_KEY=key), today=date(2026, 9, 27),
                         client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    detail = json.dumps(db.calendar_status())
    assert key not in detail and "api_key=<redacted>" in detail


def test_replace_is_per_source_and_replaces_rather_than_appends(db):
    db.replace_calendar_source("fomc", [{"event_date": "2026-10-28", "event": "FOMC rate decision", "importance": "high",
                                          "source_url": "u", "time_utc": None}])
    db.replace_calendar_source("fred", [{"event_date": "2026-10-02", "event": "CPI", "importance": "high",
                                          "source_url": "v", "time_utc": None}])
    db.replace_calendar_source("fomc", [{"event_date": "2026-12-09", "event": "FOMC rate decision", "importance": "high",
                                          "source_url": "u", "time_utc": None}])
    rows = db.read_calendar("2026-01-01", "2026-12-31", importance="all")
    assert [(r["event_date"], r["source"]) for r in rows] == [("2026-10-02", "fred"), ("2026-12-09", "fomc")]
