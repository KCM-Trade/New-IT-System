"""
OPT-0072 Gap Trade scheduling on the MT clock (US DST: UTC+3 summer / UTC+2
winter) instead of a fixed HKT 07:20 / fixed ``+3h``.

Frozen clocks only: ``burst_open_scheduler.datetime`` is swapped for a
subclass whose ``now()`` returns a fixed instant, and every DB / CRM / MySQL
touchpoint of the two scan functions is replaced by a recorder. No real DB.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.core import burst_open_scheduler as bos

UTC = timezone.utc


# ── Frozen clock + stubbed collaborators ──────────────────────

def freeze(monkeypatch, instant_utc: datetime) -> None:
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant_utc if tz is None else instant_utc.astimezone(tz)

    monkeypatch.setattr(bos, "datetime", FrozenDatetime)


@pytest.fixture
def calls(monkeypatch):
    """Stub everything the final / intraday scans touch; record the calls."""
    import app.core.config as config_mod
    import app.core.risk_monitor_db as rmdb
    import app.services.gap_trade_crm_tag_service as crm
    import app.services.rule_gap_trade_gap_service as gp
    import app.services.rule_gap_trade_so_service as so

    rec: dict[str, list] = {"so": [], "gp": [], "append": [], "crm": []}

    monkeypatch.setattr(config_mod, "get_settings", lambda: object())
    monkeypatch.setattr(rmdb, "load_gap_trade_config", lambda: {})
    monkeypatch.setattr(rmdb, "get_crm_tag_rows_for_window", lambda _d: [])
    monkeypatch.setattr(
        rmdb, "append_scan_and_events", lambda **kw: rec["append"].append(kw)
    )

    def fake_so(_settings, **kw):
        rec["so"].append(kw)
        return {"alerts": [], "scan_time_ms": 1}

    def fake_gp(_settings, **kw):
        rec["gp"].append(kw)
        return {"alerts": [], "scan_time_ms": 1}

    monkeypatch.setattr(so, "detect_gap_trade_so", fake_so)
    monkeypatch.setattr(gp, "detect_gap_trade_gap_profit", fake_gp)
    monkeypatch.setattr(
        crm, "process_gap_trade_crm_tags",
        lambda _settings, **kw: rec["crm"].append(kw),
    )
    monkeypatch.setattr(bos, "_backfill_alert_user_ids", lambda *_a: None)
    return rec


# ── _mt_now: DST-aware MT wall clock ──────────────────────────

@pytest.mark.parametrize("utc,expected_mt", [
    # Summer (UTC+3): HKT 07:20 == MT 02:20.
    (datetime(2026, 10, 5, 23, 20, tzinfo=UTC), datetime(2026, 10, 6, 2, 20)),
    # Last summer trading day before the switch (Sat 10-31).
    (datetime(2026, 10, 30, 23, 20, tzinfo=UTC), datetime(2026, 10, 31, 2, 20)),
    # First winter trading day (Mon 11-02, UTC+2): HKT 08:20 == MT 02:20 ...
    (datetime(2026, 11, 2, 0, 20, tzinfo=UTC), datetime(2026, 11, 2, 2, 20)),
    # ... and HKT 07:20 is only MT 01:20 — the bug this OPT fixes.
    (datetime(2026, 11, 2, 23, 20, tzinfo=UTC), datetime(2026, 11, 3, 1, 20)),
    # Spring switch 2027-03-14 (Sun): Sat 03-13 still winter, Mon 03-15 summer.
    (datetime(2027, 3, 13, 0, 20, tzinfo=UTC), datetime(2027, 3, 13, 2, 20)),
    (datetime(2027, 3, 14, 23, 20, tzinfo=UTC), datetime(2027, 3, 15, 2, 20)),
])
def test_mt_now_follows_us_dst(utc, expected_mt):
    assert bos._mt_now(utc) == expected_mt


# ── Final scan: frozen-clock behaviour ────────────────────────

def test_final_scan_summer_hkt_0720_runs_current_mt_day(monkeypatch, calls):
    # 2026-10-05 23:20 UTC = HKT Tue 10-06 07:20 = MT 10-06 02:20.
    freeze(monkeypatch, datetime(2026, 10, 5, 23, 20, tzinfo=UTC))
    bos._run_gap_trade_scan()

    assert len(calls["so"]) == 1 and len(calls["gp"]) == 1
    assert calls["gp"][0]["start_mt"] == datetime(2026, 10, 6, 0, 0)
    assert calls["gp"][0]["end_mt"] == datetime(2026, 10, 6, 2, 0)
    assert len(calls["append"]) == 1
    assert calls["crm"][0]["window_date"] == "2026-10-06"
    assert calls["crm"][0]["scan_label"] == "final MT 02:20 (HKT 07:20)"


def test_final_scan_winter_hkt_0720_is_refused(monkeypatch, calls):
    # 2026-11-02 23:20 UTC = HKT Tue 11-03 07:20 = MT 11-03 01:20: the MT
    # window still has 40 min to go → no detect, no persist, no CRM tag.
    freeze(monkeypatch, datetime(2026, 11, 2, 23, 20, tzinfo=UTC))
    bos._run_gap_trade_scan()

    assert calls == {"so": [], "gp": [], "append": [], "crm": []}


def test_final_scan_winter_hkt_0820_runs_full_window(monkeypatch, calls):
    # 2026-11-03 00:20 UTC = HKT Tue 11-03 08:20 = MT 11-03 02:20.
    freeze(monkeypatch, datetime(2026, 11, 3, 0, 20, tzinfo=UTC))
    bos._run_gap_trade_scan()

    assert calls["so"][0]["start_mt"] == datetime(2026, 11, 3, 0, 0)
    assert calls["so"][0]["end_mt"] == datetime(2026, 11, 3, 2, 0)
    assert calls["crm"][0]["window_date"] == "2026-11-03"
    assert calls["crm"][0]["scan_label"] == "final MT 02:20 (HKT 08:20)"


def test_final_scan_first_winter_monday(monkeypatch, calls):
    # First winter scan after the 2026-11-01 (Sun) switch: Mon 11-02 MT 02:20
    # = 2026-11-02 00:20 UTC = HKT 08:20.
    freeze(monkeypatch, datetime(2026, 11, 2, 0, 20, tzinfo=UTC))
    bos._run_gap_trade_scan()
    assert calls["gp"][0]["start_mt"] == datetime(2026, 11, 2, 0, 0)
    assert calls["gp"][0]["end_mt"] == datetime(2026, 11, 2, 2, 0)


def test_final_scan_config_window_past_0220_still_scans(monkeypatch, calls):
    # A UI-configured window ending after MT 02:20 keeps the old partial-scan
    # behaviour (warned) instead of silently never scanning.
    import app.core.risk_monitor_db as rmdb
    monkeypatch.setattr(rmdb, "load_gap_trade_config",
                        lambda: {"window_end_hour_mt": 3})
    freeze(monkeypatch, datetime(2026, 11, 3, 0, 20, tzinfo=UTC))
    bos._run_gap_trade_scan()
    assert calls["gp"][0]["end_mt"] == datetime(2026, 11, 3, 3, 0)


# ── Final trigger: fires exactly once per MT trading day at MT 02:20 ──

def _fire_times(trigger, start_utc: datetime, end_utc: datetime) -> list[datetime]:
    out: list[datetime] = []
    prev = None
    now = start_utc
    while True:
        nxt = trigger.get_next_fire_time(prev, now)
        if nxt is None or nxt >= end_utc:
            return out
        out.append(nxt)
        prev = now = nxt


@pytest.mark.parametrize("start,end", [
    (datetime(2026, 10, 25, tzinfo=UTC), datetime(2026, 11, 15, tzinfo=UTC)),
    (datetime(2027, 3, 7, tzinfo=UTC), datetime(2027, 3, 21, tzinfo=UTC)),
])
def test_final_trigger_exactly_once_per_mt_trading_day(start, end):
    fires = _fire_times(bos._gap_trade_final_trigger(), start, end)
    mt = [bos._mt_now(f.astimezone(UTC)) for f in fires]

    # Every fire is MT 02:20 wall clock, whatever the season.
    assert all((t.hour, t.minute) == (2, 20) for t in mt)
    # Exactly one fire per MT Mon–Sat date in the range, none on Sunday.
    fire_days = [t.date() for t in mt]
    assert len(fire_days) == len(set(fire_days))
    first = bos._mt_now(start).date()
    last = bos._mt_now(end - timedelta(seconds=1)).date()
    expected = {
        first + timedelta(days=i)
        for i in range((last - first).days + 1)
        if (first + timedelta(days=i)).weekday() != 6
    }
    # The range edges may cut off the 02:20 fire of the first/last MT day.
    assert set(fire_days) <= expected
    assert expected - set(fire_days) <= {first, last}


def test_final_trigger_dst_boundary_instants():
    trig = bos._gap_trade_final_trigger()
    fires = [f.astimezone(UTC) for f in _fire_times(
        trig, datetime(2026, 10, 30, tzinfo=UTC), datetime(2026, 11, 4, tzinfo=UTC))]
    assert fires == [
        datetime(2026, 10, 30, 23, 20, tzinfo=UTC),  # Sat 10-31, summer, HKT 07:20
        datetime(2026, 11, 2, 0, 20, tzinfo=UTC),    # Mon 11-02, winter, HKT 08:20
        datetime(2026, 11, 3, 0, 20, tzinfo=UTC),    # Tue 11-03, winter, HKT 08:20
    ]
    fires = [f.astimezone(UTC) for f in _fire_times(
        trig, datetime(2027, 3, 12, tzinfo=UTC), datetime(2027, 3, 15, 12, tzinfo=UTC))]
    assert fires == [
        datetime(2027, 3, 12, 0, 20, tzinfo=UTC),    # Fri 03-12, winter
        datetime(2027, 3, 13, 0, 20, tzinfo=UTC),    # Sat 03-13, winter
        datetime(2027, 3, 14, 23, 20, tzinfo=UTC),   # Mon 03-15, summer
    ]


def test_final_trigger_skips_winter_hkt_0720():
    # Standing at winter HKT 07:20 (MT 01:20), the next fire is HKT 08:20.
    trig = bos._gap_trade_final_trigger()
    nxt = trig.get_next_fire_time(None, datetime(2026, 11, 2, 23, 20, tzinfo=UTC))
    assert nxt.astimezone(UTC) == datetime(2026, 11, 3, 0, 20, tzinfo=UTC)


# ── Intraday tier: MT guard + HKT coarse fence ────────────────

def _hkt(y, mo, d, h, mi) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=timezone(timedelta(hours=8))).astimezone(UTC)


@pytest.mark.parametrize("instant,runs,end_mt", [
    # Summer (HKT Tue 2026-10-06): window HKT 05:55–07:05.
    (_hkt(2026, 10, 6, 5, 54), False, None),
    (_hkt(2026, 10, 6, 5, 55), True, datetime(2026, 10, 6, 0, 55)),
    (_hkt(2026, 10, 6, 7, 5), True, datetime(2026, 10, 6, 2, 0)),
    (_hkt(2026, 10, 6, 7, 6), False, None),
    # Winter (HKT Tue 2026-11-03): window HKT 06:55–08:05.
    (_hkt(2026, 11, 3, 5, 55), False, None),   # MT Mon 23:55
    (_hkt(2026, 11, 3, 6, 54), False, None),
    (_hkt(2026, 11, 3, 6, 55), True, datetime(2026, 11, 3, 0, 55)),
    (_hkt(2026, 11, 3, 7, 30), True, datetime(2026, 11, 3, 1, 30)),
    (_hkt(2026, 11, 3, 8, 5), True, datetime(2026, 11, 3, 2, 0)),
    (_hkt(2026, 11, 3, 8, 6), False, None),
])
def test_intraday_guard_on_mt_clock(monkeypatch, calls, instant, runs, end_mt):
    freeze(monkeypatch, instant)
    bos._run_gap_trade_intraday_scan()
    if not runs:
        assert calls["gp"] == [] and calls["crm"] == []
        return
    assert len(calls["gp"]) == 1
    assert calls["gp"][0]["start_mt"] == end_mt.replace(hour=0, minute=0)
    assert calls["gp"][0]["end_mt"] == end_mt
    assert calls["crm"][0]["window_date"] == end_mt.date().isoformat()


@pytest.mark.parametrize("day", [date(2026, 10, 6), date(2026, 11, 3)])
def test_intraday_fence_covers_mt_window_both_seasons(day):
    trig = bos._gap_trade_intraday_trigger()
    start = datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(hours=8)
    fires = _fire_times(trig, start, start + timedelta(days=1))
    in_window = [
        f for f in fires
        if bos._in_intraday_window_mt(*(lambda t: (t.hour, t.minute))(
            bos._mt_now(f.astimezone(UTC))))
    ]
    mt_slots = [bos._mt_now(f.astimezone(UTC)) for f in in_window]
    # MT 00:55, 01:00 … 02:05 → 15 ticks, all on the same MT day.
    assert [(t.hour, t.minute) for t in mt_slots] == (
        [(0, 55)] + [(1, m) for m in range(0, 60, 5)] + [(2, 0), (2, 5)]
    )
    assert {t.date() for t in mt_slots} == {day}
