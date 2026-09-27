"""Tool 5 — ``get_economic_calendar`` (docs/ai-agent/02-contracts.md §12).

Reads the ``econ_calendar_cache`` table the MAIN API refreshes daily
(``services/econ_calendar_service.refresh_calendar``); this container never
fetches anything itself (read-only data mount, no network tool by design).
Every row carries the official ``source_url``, which is what makes the answer
``certified: true`` — the date came from the Fed / FRED, not from the model.

Staleness is reported, never hidden: the newest successful fetch older than
``STALE_AFTER_HOURS`` adds ``stale_since``; a source that never loaded (no
FRED key) adds its own caveat so the model can say "FOMC only" honestly.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.core import ai_usage_db
from app.services.rule_intraday_return_service import MT_SERVER_TZ

from .common import CallerCtx, error_envelope, is_error, ok_envelope, run_sync_with_timeout, utc_now_iso

TOOL_NAME = "get_economic_calendar"

MAX_DAYS_AHEAD = 60
DEFAULT_DAYS_AHEAD = 30
IMPORTANCE_VALUES = ("high", "all")
STALE_AFTER_HOURS = 36
_HK = ZoneInfo("Asia/Hong_Kong")


def _parse_utc(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _times(time_utc: Optional[str]) -> dict:
    dt = _parse_utc(time_utc)
    if dt is None:
        return {"time_utc": None, "time_hk": None, "time_mt": None}
    return {
        "time_utc": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "time_hk": dt.astimezone(_HK).strftime("%Y-%m-%d %H:%M"),
        "time_mt": dt.astimezone(MT_SERVER_TZ).strftime("%Y-%m-%d %H:%M"),
    }


def _read(day_from: str, day_to: str, countries: list[str], importance: str) -> dict:
    return {
        "rows": ai_usage_db.read_calendar(day_from, day_to, countries=countries, importance=importance),
        "status": ai_usage_db.calendar_status(),
    }


async def get_economic_calendar(
    ctx: CallerCtx,
    days_ahead: Any = DEFAULT_DAYS_AHEAD,
    countries: Any = None,
    importance: Any = "high",
    *,
    today: Optional[date] = None,
    now_utc: Optional[datetime] = None,
) -> dict:
    try:
        days = int(days_ahead if days_ahead is not None else DEFAULT_DAYS_AHEAD)
    except (TypeError, ValueError):
        return error_envelope("invalid_argument", "days_ahead must be an integer")
    if days < 1 or days > MAX_DAYS_AHEAD:
        return error_envelope(
            "invalid_argument", f"days_ahead must be between 1 and {MAX_DAYS_AHEAD}", {"days_ahead": days}
        )
    importance = str(importance or "high").strip().lower()
    if importance not in IMPORTANCE_VALUES:
        return error_envelope("invalid_argument", f"importance must be one of {list(IMPORTANCE_VALUES)}")
    if countries is None:
        country_list = ["US"]
    elif isinstance(countries, (list, tuple)) and countries:
        country_list = sorted({str(c).strip().upper() for c in countries if str(c).strip()})
    else:
        return error_envelope("invalid_argument", "countries must be a non-empty list of ISO codes or null")
    unsupported = [c for c in country_list if c != "US"]

    now = now_utc or datetime.now(timezone.utc)
    today = today or now.astimezone(MT_SERVER_TZ).date()
    day_to = today + timedelta(days=days)

    result = await run_sync_with_timeout(
        _read, today.isoformat(), day_to.isoformat(), country_list, importance, ctx=ctx
    )
    if is_error(result):
        return result

    rows_out = []
    for r in result["rows"]:
        rows_out.append(
            {
                "date": r["event_date"],
                **_times(r.get("time_utc")),
                "country": r["country"],
                "event": r["event"],
                "importance": r["importance"],
                "source_url": r["source_url"],
            }
        )

    status: dict[str, dict] = result["status"] or {}
    caveats = [
        "Dates come from official calendars cached daily by the main API (Fed FOMC page; FRED release calendar "
        "for BLS/BEA/Census releases). Each row's source_url is the authority.",
        "FRED publishes release DATES only; the 08:30 ET clock time on those rows is the conventional release "
        "time, not confirmed per release. FOMC statements are at 14:00 ET on the meeting's second day.",
        "time_hk = Asia/Hong_Kong; time_mt = MT server clock (UTC+3 summer / UTC+2 winter, US DST calendar).",
        "Only US releases are covered in this version.",
    ]
    if unsupported:
        caveats.append(f"Countries {unsupported} are not covered; only US rows are returned.")
    ok_times = [
        _parse_utc(s.get("ok_at")) for s in status.values() if s.get("ok_at")
    ]
    newest_ok = max([t for t in ok_times if t is not None], default=None)
    if newest_ok is None:
        caveats.append("stale_since: never — the calendar cache has not been populated yet; rows may be missing.")
    elif now - newest_ok > timedelta(hours=STALE_AFTER_HOURS):
        caveats.append(
            f"stale_since: {newest_ok.strftime('%Y-%m-%dT%H:%M:%SZ')} — the last successful refresh is older than "
            f"{STALE_AFTER_HOURS}h; dates may be outdated."
        )
    fred = status.get("fred") or {}
    if fred.get("status") in (None, "no_key"):
        caveats.append(
            "fred_api_key_missing: FRED_API_KEY is not configured, so BLS/BEA releases (NFP, CPI, PPI, GDP, PCE, "
            "Retail Sales) are NOT in this calendar — only FOMC dates are. Say so explicitly."
        )
    elif fred.get("status") == "failed":
        caveats.append(
            f"fred_unavailable: the last FRED fetch failed ({(fred.get('detail') or '')[:120]}); "
            f"BLS/BEA rows are from the last good fetch at {fred.get('ok_at') or 'never'}."
        )
    fomc = status.get("fomc") or {}
    if fomc.get("status") == "failed":
        caveats.append(
            f"fomc_unavailable: the last Fed page fetch failed; FOMC rows are from {fomc.get('ok_at') or 'never'}."
        )

    data = {
        "from": today.isoformat(),
        "to": day_to.isoformat(),
        "days_ahead": days,
        "countries": country_list,
        "importance": importance,
        "rows": rows_out,
        "row_count": len(rows_out),
        "sources": {k: {"status": v.get("status"), "ok_at": v.get("ok_at")} for k, v in status.items()},
    }
    definition = {
        "summary": f"Scheduled US economic releases and FOMC decisions from {today.isoformat()} to "
        f"{day_to.isoformat()} ({'high importance only' if importance == 'high' else 'all cached importances'}), "
        "from official calendars cached daily.",
        "caveats": caveats,
        "doc": "docs/ai-agent/02-contracts.md §12; app/services/econ_calendar_service.py",
    }
    source = {
        "service": "app.services.econ_calendar_service",
        "function": "read_calendar",
        "as_of": newest_ok.strftime("%Y-%m-%dT%H:%M:%SZ") if newest_ok else utc_now_iso(),
    }
    return ok_envelope(data, definition=definition, source=source, ctx=ctx, truncated=False)
