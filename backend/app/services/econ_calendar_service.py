"""US economic-release calendar: fetch from deterministic official sources and
cache in ``ai_agent.db`` (OPT-0065 item 4, docs/ai-agent/02-contracts.md §12).

Why a cache and not a live fetch inside the agent tool: the agent container
mounts ``backend/data`` read-only and, by design, has no outbound network
tool. The MAIN API refreshes the table once a day (06:00 HKT scheduler job)
and the tool only reads it. A failed refresh keeps the previous rows and is
reported to the tool as a status row, so the model can say "stale since …"
instead of inventing a date.

Sources (2026-09-27):

* **FOMC** — https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
  Reachable from this host. Parsed from the "<year> FOMC Meetings" panels:
  two-day meetings are dated on the SECOND day (the statement day, 14:00 ET);
  a ``*`` marks meetings with a Summary of Economic Projections.
* **FRED** ``/fred/releases/dates`` — the St. Louis Fed's release calendar,
  which carries the BLS/BEA/Census releases (Employment Situation = NFP, CPI,
  PPI, GDP, Personal Income and Outlays = PCE, Retail Sales, ...). Needs a
  free ``FRED_API_KEY``; without one the source is skipped and the tool is
  told so (``fred_api_key_missing``). FRED publishes DATES only, so the
  conventional 08:30 ET release time is attached and declared as a caveat.
* **BLS** own iCal (``bls.ics``) was the contract's first choice and is NOT
  used: www.bls.gov answers 403 "Access Denied" from this host for every path
  and user agent (probed 2026-09-27). FRED covers the same releases.

Times are converted from America/New_York with ``zoneinfo`` at fetch time, so
US DST is handled per date, not per today.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Iterable, Optional
from zoneinfo import ZoneInfo

import httpx

from app.core import ai_usage_db
from app.core.config import Settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FRED_RELEASES_DATES_URL = "https://api.stlouisfed.org/fred/releases/dates"

SOURCE_FOMC = "fomc"
SOURCE_FRED = "fred"
SOURCES = (SOURCE_FOMC, SOURCE_FRED)

# Status values stored per source (ai_usage_db.record_calendar_source_status).
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_NO_KEY = "no_key"

HTTP_TIMEOUT_S = 15.0
USER_AGENT = "Mozilla/5.0 (compatible; kcm-new-it-econ-calendar/1.0)"
# How far ahead FRED is asked for. The tool serves at most 60 days; 120 gives
# a daily refresh plenty of slack for a missed day or two.
FRED_LOOKAHEAD_DAYS = 120

_ET = ZoneInfo("America/New_York")
_UTC = timezone.utc

# Conventional release clock times (ET). FRED gives dates only.
FRED_RELEASE_TIME_ET = time(8, 30)
FOMC_STATEMENT_TIME_ET = time(14, 0)

# Eight scheduled meetings a year since 1981; the bounds leave room for an
# unscheduled meeting listed in the same panel (2020 had two) without letting
# a half-parsed page through.
FOMC_MEETINGS_PER_YEAR = (6, 10)

# FRED pages: `sort_order=asc` so the NEAREST dates come first — the API's
# default is descending, so with one page of 1000 across ~300 releases (many
# weekly) the far end of the window filled the page and next week's NFP was
# the row that fell off (cold review #10). Paginate with `offset` until
# `count` is exhausted; the cap is a guard against a runaway loop, not a
# budget (the 120-day window is ~3-4 pages).
FRED_PAGE_LIMIT = 1000
FRED_MAX_PAGES = 10

# FRED release name → (event label, importance). Names are matched
# case-insensitively on their start so a suffix like " (Advance Estimate)"
# does not break the mapping. Anything not listed is dropped: the tool exists
# for "which days move the market", not the full 300-release feed.
FRED_RELEASE_MAP: tuple[tuple[str, str, str], ...] = (
    ("employment situation", "Non-farm Payrolls (NFP) / Employment Situation", "high"),
    ("consumer price index", "CPI", "high"),
    ("producer price index", "PPI", "high"),
    ("gross domestic product", "GDP", "high"),
    ("personal income and outlays", "PCE / Personal Income and Outlays", "high"),
    ("advance monthly sales for retail and food services", "Retail Sales (advance)", "high"),
    ("job openings and labor turnover survey", "JOLTS", "medium"),
    ("employment cost index", "Employment Cost Index", "medium"),
    ("u.s. import and export price indexes", "Import / Export Price Indexes", "medium"),
    ("new residential construction", "Housing Starts / Building Permits", "medium"),
    ("advance report on durable goods", "Durable Goods (advance)", "medium"),
)


@dataclass(frozen=True)
class CalendarRow:
    event_date: str  # YYYY-MM-DD (local US release day)
    time_utc: Optional[str]  # ISO8601 Z, or None when unknown
    country: str
    event: str
    importance: str  # high | medium | low
    source: str  # SOURCE_FOMC | SOURCE_FRED
    source_url: str

    def as_dict(self) -> dict:
        return {
            "event_date": self.event_date,
            "time_utc": self.time_utc,
            "country": self.country,
            "event": self.event,
            "importance": self.importance,
            "source": self.source,
            "source_url": self.source_url,
        }


def _et_to_utc_iso(day: date, clock: time) -> str:
    local = datetime.combine(day, clock, tzinfo=_ET)
    return local.astimezone(_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── FOMC ─────────────────────────────────────────────────────────────────────

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

_PANEL_RE = re.compile(r"<a id=\"\d+\">(\d{4}) FOMC Meetings</a>", re.IGNORECASE)
_MONTH_RE = re.compile(r"fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>", re.IGNORECASE)
_DATE_RE = re.compile(r"fomc-meeting__date[^>]*>([^<]+)<", re.IGNORECASE)


def _parse_fomc_entry(year: int, month_label: str, date_text: str) -> Optional[tuple[date, bool]]:
    """One (month label, date cell) pair → (statement day, has_projections).

    ``"Jan/Feb"`` + ``"31-1"`` → Feb 1. ``"March"`` + ``"17-18*"`` → Mar 18
    with projections. ``"22 (notation vote)"`` → None (not a scheduled meeting).
    """
    text = date_text.strip()
    if "(" in text:
        return None
    projections = "*" in text
    text = text.replace("*", "").strip()
    months = [_MONTHS.get(m.strip().lower()) for m in month_label.split("/")]
    if not months or any(m is None for m in months):
        return None
    days = [d for d in re.split(r"[-–]", text) if d.strip()]
    try:
        day_nums = [int(d.strip()) for d in days]
    except ValueError:
        return None
    if not day_nums:
        return None
    # The statement day is the LAST day; for "Jan/Feb" + "31-1" it falls in
    # the last month named, for a single month the two are the same.
    try:
        return date(year, months[-1], day_nums[-1]), projections
    except ValueError:
        return None


def parse_fomc_html(html: str, *, years: Optional[Iterable[int]] = None) -> list[CalendarRow]:
    """Every scheduled meeting in the requested years (default: all panels)."""
    panels = list(_PANEL_RE.finditer(html))
    wanted = set(years) if years is not None else None
    rows: list[CalendarRow] = []
    for i, m in enumerate(panels):
        year = int(m.group(1))
        if wanted is not None and year not in wanted:
            continue
        start = m.end()
        end = panels[i + 1].start() if i + 1 < len(panels) else len(html)
        chunk = html[start:end]
        months = [x.group(1) for x in _MONTH_RE.finditer(chunk)]
        dates = [x.group(1) for x in _DATE_RE.finditer(chunk)]
        # `zip` would silently pair every month cell with the wrong date the
        # moment the Fed adds a cell the other regex does not see (cold review
        # #10). A mismatch, or a meeting count no FOMC year has ever had, is a
        # parse failure: the caller keeps the previous rows instead.
        if len(months) != len(dates):
            raise ValueError(
                f"FOMC {year} panel: {len(months)} month cells vs {len(dates)} date cells — page layout changed"
            )
        if not FOMC_MEETINGS_PER_YEAR[0] <= len(months) <= FOMC_MEETINGS_PER_YEAR[1]:
            raise ValueError(
                f"FOMC {year} panel: {len(months)} meetings parsed, expected "
                f"{FOMC_MEETINGS_PER_YEAR[0]}-{FOMC_MEETINGS_PER_YEAR[1]} — page layout changed"
            )
        for month_label, date_text in zip(months, dates):
            parsed = _parse_fomc_entry(year, month_label, date_text)
            if parsed is None:
                continue
            day, projections = parsed
            label = "FOMC rate decision" + (" (with economic projections)" if projections else "")
            rows.append(
                CalendarRow(
                    event_date=day.isoformat(),
                    time_utc=_et_to_utc_iso(day, FOMC_STATEMENT_TIME_ET),
                    country="US",
                    event=label,
                    importance="high",
                    source=SOURCE_FOMC,
                    source_url=FOMC_URL,
                )
            )
    return rows


def fetch_fomc(client: httpx.Client, *, today: Optional[date] = None) -> list[CalendarRow]:
    today = today or date.today()
    resp = client.get(FOMC_URL)
    resp.raise_for_status()
    rows = parse_fomc_html(resp.text, years={today.year, today.year + 1})
    if not rows:
        raise ValueError("FOMC page parsed to zero meetings — layout changed?")
    return rows


# ── FRED ─────────────────────────────────────────────────────────────────────


def map_fred_release(name: str) -> Optional[tuple[str, str]]:
    key = (name or "").strip().lower()
    for prefix, label, importance in FRED_RELEASE_MAP:
        if key.startswith(prefix):
            return label, importance
    return None


def build_fred_url(
    api_key: str,
    *,
    today: date,
    lookahead_days: int = FRED_LOOKAHEAD_DAYS,
    offset: int = 0,
    limit: int = FRED_PAGE_LIMIT,
) -> str:
    end = today + timedelta(days=lookahead_days)
    return (
        f"{FRED_RELEASES_DATES_URL}?api_key={api_key}&file_type=json"
        f"&include_release_dates_with_no_data=true&limit={int(limit)}&offset={int(offset)}"
        f"&sort_order=asc"
        f"&realtime_start={today.isoformat()}&realtime_end={end.isoformat()}"
    )


def parse_fred_json(payload: dict) -> list[CalendarRow]:
    rows: list[CalendarRow] = []
    seen: set[tuple[str, str]] = set()
    for item in payload.get("release_dates") or []:
        mapped = map_fred_release(str(item.get("release_name") or ""))
        if mapped is None:
            continue
        label, importance = mapped
        raw_date = str(item.get("date") or "")
        try:
            day = date.fromisoformat(raw_date)
        except ValueError:
            continue
        key = (day.isoformat(), label)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            CalendarRow(
                event_date=day.isoformat(),
                time_utc=_et_to_utc_iso(day, FRED_RELEASE_TIME_ET),
                country="US",
                event=label,
                importance=importance,
                source=SOURCE_FRED,
                source_url=f"https://fred.stlouisfed.org/releases/calendar#{day.isoformat()}",
            )
        )
    return rows


def fetch_fred(client: httpx.Client, api_key: str, *, today: Optional[date] = None) -> list[CalendarRow]:
    """Every release date in the window, across as many pages as FRED needs.

    Stops when the page is short, when ``offset`` reaches the reported
    ``count``, or at FRED_MAX_PAGES. A page that fails raises — the whole
    source is then "failed" and the previous rows stay (no half-window cache).
    """
    today = today or date.today()
    items: list[dict] = []
    offset = 0
    for _ in range(FRED_MAX_PAGES):
        resp = client.get(build_fred_url(api_key, today=today, offset=offset))
        # FRED answers 400 with {"error_code":400,"error_message":...} for a
        # bad key; raise_for_status turns that into the same "source failed"
        # path as a network error, so old rows are kept.
        resp.raise_for_status()
        payload = resp.json()
        page = payload.get("release_dates") or []
        items.extend(page)
        offset += len(page)
        count = payload.get("count")
        if len(page) < FRED_PAGE_LIMIT or (isinstance(count, int) and offset >= count):
            break
    return parse_fred_json({"release_dates": items})


# ── refresh ──────────────────────────────────────────────────────────────────


def _redact_error(exc: BaseException) -> str:
    """Error text safe for the status table: an httpx URL error would include
    the FRED api_key in the query string."""
    text = f"{type(exc).__name__}: {exc}"
    return re.sub(r"api_key=[^&\s]+", "api_key=<redacted>", text)[:300]


def refresh_calendar(
    settings: Settings,
    *,
    today: Optional[date] = None,
    client_factory: Callable[[], httpx.Client] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Fetch every source and replace its rows in the cache.

    Per source, all-or-nothing: a fetch/parse failure records a ``failed``
    status and leaves that source's previous rows untouched. Returns a
    per-source summary for the log line / tests.
    """
    today = today or date.today()
    now_iso = ai_usage_db.utc_now_iso()
    summary: dict[str, Any] = {}

    def _client() -> httpx.Client:
        return httpx.Client(timeout=HTTP_TIMEOUT_S, headers={"User-Agent": USER_AGENT}, follow_redirects=True)

    make_client = client_factory or _client
    with make_client() as client:
        # FOMC
        try:
            rows = fetch_fomc(client, today=today)
            ai_usage_db.replace_calendar_source(SOURCE_FOMC, [r.as_dict() for r in rows], fetched_at=now_iso)
            ai_usage_db.record_calendar_source_status(SOURCE_FOMC, STATUS_OK, now=now_iso)
            summary[SOURCE_FOMC] = {"status": STATUS_OK, "rows": len(rows)}
        except Exception as exc:  # noqa: BLE001 — one source must not stop the other
            detail = _redact_error(exc)
            logger.warning("econ calendar: FOMC refresh failed, keeping previous rows: %s", detail)
            ai_usage_db.record_calendar_source_status(SOURCE_FOMC, STATUS_FAILED, now=now_iso, detail=detail)
            summary[SOURCE_FOMC] = {"status": STATUS_FAILED, "detail": detail}

        # FRED
        api_key = (getattr(settings, "FRED_API_KEY", "") or "").strip()
        if not api_key:
            ai_usage_db.record_calendar_source_status(
                SOURCE_FRED, STATUS_NO_KEY, now=now_iso, detail="FRED_API_KEY is not configured"
            )
            summary[SOURCE_FRED] = {"status": STATUS_NO_KEY}
        else:
            try:
                rows = fetch_fred(client, api_key, today=today)
                ai_usage_db.replace_calendar_source(SOURCE_FRED, [r.as_dict() for r in rows], fetched_at=now_iso)
                ai_usage_db.record_calendar_source_status(SOURCE_FRED, STATUS_OK, now=now_iso)
                summary[SOURCE_FRED] = {"status": STATUS_OK, "rows": len(rows)}
            except Exception as exc:  # noqa: BLE001
                detail = _redact_error(exc)
                logger.warning("econ calendar: FRED refresh failed, keeping previous rows: %s", detail)
                ai_usage_db.record_calendar_source_status(SOURCE_FRED, STATUS_FAILED, now=now_iso, detail=detail)
                summary[SOURCE_FRED] = {"status": STATUS_FAILED, "detail": detail}

    logger.info(
        "econ calendar refreshed: %s",
        ", ".join(f"{k}={v.get('status')}{'/' + str(v['rows']) if 'rows' in v else ''}" for k, v in summary.items()),
    )
    return summary
