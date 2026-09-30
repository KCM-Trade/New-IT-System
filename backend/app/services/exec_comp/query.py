"""Query core of the exec-compensation API (03 §4.1) — FROZEN INTERFACE.

Caller-agnostic: takes a ``Query`` + a ``FillSource``, returns the contract
models of ``app.schemas.exec_compensation``. Never reads the request, the
session or the caller's identity. Every rule (subject resolution, as_of,
date clipping, classification, netting, open-position exclusion, paging,
sorting, caching, thresholds) lives here or below — the routes only
authenticate and build a ``Query``.

Semantics (docs/exec-compensation 01 / 02 / 03):
- MT server day boundaries for date_from / date_to / as_of (01 Q8, D14).
- Default as_of = yesterday (MT server day). as_of later than that ->
  AS_OF_TOO_LATE. If the replica has not reached the end of the default
  as_of, the default steps back one day and coverage says why (04 §4).
- date_to > as_of -> clipped to as_of, ``query.date_to_clipped = True``.
- date_from < EARLIEST_SRV_DATE -> RANGE_BEFORE_COVERAGE.
- counted = eligible (cls == "market") AND srv_date in range AND the
  position is fully closed (volume back to 0) by the end of as_of with no
  anomaly (02 §6.1: lifecycle from ``FillSource.position_legs``, legs after
  as_of are ignored; Entry 2 -> anomaly).
- Fills whose order row is missing are counted in ``coverage.unmatched_deals``,
  never dropped silently, never compensated.
- Thresholds (01 D21): server-wide slots, per-query time budget, deal cap.
- Results are cached per (calc_version, subject, date_from, date_to, as_of);
  orders / export page over the cached result.

Injection points (keyword-only, all optional, for tests and for the route):
- ``cache``: a redis-py client (``decode_responses=True``) or None = no cache.
  Defaults to None so a bare call never touches a shared Redis; the route
  passes ``default_cache()``.
- ``clock``: a datetime, or a zero-arg callable returning one, used as "now".
  Naive values are MT server wall clock; aware values are converted with
  ``MT_SERVER_TZ``. Defaults to the wall clock.
- ``settings``: a ``Settings`` object; defaults to ``get_settings()``.

Not-counted precedence (one reason per row, first match wins):
  1. class   — cls != market -> ``not_eligible_class``; summed in
               ``not_counted_by_class``.
  2. anomaly — the position has Entry 2 / PositionID 0 / an inconsistent
               lifecycle -> ``position_anomaly``; counted in
               ``anomaly_positions``.
  3. open    — the position still has volume at the end of as_of ->
               ``position_open_at_as_of``; summed in ``excluded_open``.
               ``view=excluded_open`` shows only this kind.

``compute_uncached()`` runs the same computation without cache / slots and
returns the internal result, including the 02 §4.1 unit self-check counters
(``unit_checked`` / ``unit_mismatches``) for the reconcile script.

``build_summary()`` is the one aggregation: a pure function over finished row
dicts (with their internal unrounded ``_comp_raw``). ``_compute`` calls it,
and so does the offline export (backend/scripts/exec_comp_offline_export.py)
over the concatenated rows of several date chunks — open-position status
depends only on as_of, never on the range, so chunks are additive.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import math
import re
import time
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

from app.core.config import Settings, get_settings
from app.core.singleflight import SingleFlight
from app.schemas.exec_compensation import (
    CALC_VERSION,
    Basis,
    Coverage,
    Limits,
    OrderRow,
    OrdersResponse,
    OrdersView,
    RawCodes,
    SortBy,
    SortDir,
    Statistics,
    StatusData,
    StatusResponse,
    SummaryData,
    SummaryResponse,
)
from app.services.rule_intraday_return_service import MT_SERVER_TZ

from . import calc
from .classify import classify, is_eligible
from .errors import ExecCompError
from .limits import Deadline, QuerySlot
from .models import Account, FillSource, RawFill

logger = logging.getLogger(__name__)

# First MT5 fill ever (2023-02-20, 01 D15); lower bound for date_from.
EARLIEST_SRV_DATE = dt.date(2023, 2, 20)

_LOGIN_SID_RE = re.compile(r"5-(\d{1,12})")
_SORT_KEYS = ("fill_time_srv", "comp_usd", "delay_ms", "lots", "symbol", "login", "deal_id")
_VIEWS = ("counted", "excluded_open", "all")
_MONEY_DP, _LOTS_DP = 4, 2

Clock = Union[None, dt.datetime, Callable[[], dt.datetime]]

_singleflight = SingleFlight()


@dataclass(frozen=True)
class Query:
    client_id: Optional[int]
    login_sid: Optional[str]      # "5-<login>"
    date_from: dt.date
    date_to: dt.date
    as_of: Optional[dt.date] = None   # None = default (yesterday, MT server day)


# --- public API ------------------------------------------------------------


def default_cache():
    """The shared Redis client (same one the other pages cache in), or None."""
    from app.services.clickhouse_service import clickhouse_service

    return getattr(clickhouse_service, "redis_client", None)


def summary(
    q: Query,
    *,
    source: FillSource,
    cache: Any = None,
    clock: Clock = None,
    settings: Optional[Settings] = None,
) -> SummaryResponse:
    started = time.perf_counter()
    res = _result(q, source, cache, clock, settings or get_settings(), need_rows=False, op="summary")
    return SummaryResponse(
        data=SummaryData.model_validate(res.summary),
        basis=Basis(),
        as_of=res.as_of,
        coverage=Coverage.model_validate(res.coverage),
        statistics=_stats(res.from_cache, started),
    )


def orders(
    q: Query,
    *,
    source: FillSource,
    view: OrdersView = "counted",
    page: int = 1,
    page_size: int = 100,
    sort_by: SortBy = "fill_time_srv",
    sort_dir: SortDir = "asc",
    cache: Any = None,
    clock: Clock = None,
    settings: Optional[Settings] = None,
) -> OrdersResponse:
    started = time.perf_counter()
    settings = settings or get_settings()
    if view not in _VIEWS:
        raise ExecCompError("VALIDATION_ERROR", f"view must be one of {', '.join(_VIEWS)}")
    if sort_by not in _SORT_KEYS:
        raise ExecCompError("SORT_NOT_ALLOWED", f"sort_by must be one of {', '.join(_SORT_KEYS)}")
    if sort_dir not in ("asc", "desc"):
        raise ExecCompError("VALIDATION_ERROR", "sort_dir must be asc or desc")
    max_ps = int(settings.EXEC_COMP_PAGE_SIZE_MAX)
    if not isinstance(page_size, int) or not 1 <= page_size <= max_ps:
        raise ExecCompError("VALIDATION_ERROR", f"page_size must be between 1 and {max_ps}")
    if not isinstance(page, int) or page < 1:
        raise ExecCompError("VALIDATION_ERROR", "page must be >= 1")

    res = _result(q, source, cache, clock, settings, need_rows=True, op="orders")
    rows = _select(res.rows, view)
    rows = _sorted(rows, sort_by, sort_dir)
    total = len(rows)
    start = (page - 1) * page_size
    return OrdersResponse(
        data=[OrderRow.model_validate(r) for r in rows[start : start + page_size]],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(total / page_size) if total else 0,
        basis=Basis(),
        as_of=res.as_of,
        coverage=Coverage.model_validate(res.coverage),
        statistics=_stats(res.from_cache, started),
    )


def export_rows(
    q: Query,
    *,
    source: FillSource,
    cache: Any = None,
    clock: Clock = None,
    settings: Optional[Settings] = None,
) -> tuple[SummaryResponse, list[dict]]:
    """Full summary + every in-range row (view=all) for the xlsx export.

    Rows are the core's plain dicts with the ``OrderRow`` fields (plus
    internal ``_``-prefixed keys), sorted by fill time — not 150k pydantic
    objects: ``export.build_xlsx`` writes straight from them."""
    started = time.perf_counter()
    res = _result(q, source, cache, clock, settings or get_settings(), need_rows=True, op="export")
    resp = summary_response(res.summary, res.as_of, res.coverage, from_cache=res.from_cache, started=started)
    # A new list of the same dicts (8 bytes a row). Never clear ``res.rows``:
    # SingleFlight hands the same result object to concurrent waiters.
    return resp, _sorted(res.rows, "fill_time_srv", "asc")


def summary_response(
    summary_dict: dict, as_of: dt.date, coverage: dict, *, from_cache: bool = False,
    started: Optional[float] = None,
) -> SummaryResponse:
    """The contract envelope around a summary dict (also used offline)."""
    return SummaryResponse(
        data=SummaryData.model_validate(summary_dict),
        basis=Basis(),
        as_of=as_of,
        coverage=Coverage.model_validate(coverage),
        statistics=_stats(from_cache, started if started is not None else time.perf_counter()),
    )


def status(
    *, source: FillSource, clock: Clock = None, settings: Optional[Settings] = None
) -> StatusResponse:
    settings = settings or get_settings()
    default_as_of = _today_srv(clock) - dt.timedelta(days=1)
    try:
        head = source.replica_head()
    finally:
        _close(source)
    return StatusResponse(
        data=StatusData(
            earliest_srv_date=EARLIEST_SRV_DATE,
            default_as_of=default_as_of,
            ready_through_srv_date=_ready_through(head, default_as_of, _now_utc(clock), settings),
            replica_latest_fill_utc=calc.iso_z(head.time_utc) if head else None,
            limits=Limits(
                statement_timeout_ms=settings.EXEC_COMP_STATEMENT_TIMEOUT_MS,
                query_budget_s=settings.EXEC_COMP_QUERY_BUDGET_S,
                max_deals=settings.EXEC_COMP_MAX_DEALS,
                max_concurrent=settings.EXEC_COMP_MAX_CONCURRENT,
                cache_ttl_s=settings.EXEC_COMP_CACHE_TTL_S,
                page_size_max=settings.EXEC_COMP_PAGE_SIZE_MAX,
            ),
        ),
        basis=Basis(),
    )


# --- time / validation -------------------------------------------------------


def _now(clock: Clock) -> dt.datetime:
    if clock is None:
        return dt.datetime.now(dt.timezone.utc)
    return clock() if callable(clock) else clock


def _today_srv(clock: Clock) -> dt.date:
    """Today's MT server date (US-DST GMT+2/+3 calendar — never a fixed +3).
    A naive injected clock is already MT server wall clock."""
    now = _now(clock)
    if now.tzinfo is None:
        return now.date()
    return now.astimezone(MT_SERVER_TZ).date()


def _now_utc(clock: Clock) -> dt.datetime:
    """"Now" as naive UTC (the replica head's ``time_utc`` is naive UTC)."""
    now = _now(clock)
    if now.tzinfo is None:
        return calc.srv_to_utc_calendar(now)
    return now.astimezone(dt.timezone.utc).replace(tzinfo=None)


def _day_start(d: dt.date) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day)


def _ready_through(head, cap: dt.date, now_utc: dt.datetime, settings: Settings) -> dt.date:
    """Latest MT day the replica fully covers, capped at the default as_of.

    Primary rule: the newest fill is at/after the next day's 00:00 server
    time. Secondary rule (quiet periods have no fill after MT midnight): the
    head's true-UTC time is at most ``EXEC_COMP_READY_LAG_S`` behind now — the
    replica is taken as current, so everything before today is there.
    ``EXEC_COMP_READY_LAG_S=0`` disables the secondary rule."""
    if head is None:
        return EARLIEST_SRV_DATE - dt.timedelta(days=1)
    ready = head.time_srv.date() - dt.timedelta(days=1)
    lag_s = max(0, int(settings.EXEC_COMP_READY_LAG_S))
    if ready < cap and lag_s and (now_utc - head.time_utc).total_seconds() <= lag_s:
        ready = cap
    return min(cap, ready)


@dataclass(frozen=True)
class _Subject:
    key: str                       # cache-key fragment
    client_id: Optional[int]
    login: Optional[int]
    login_sid: Optional[str]


def _parse_subject(q: Query) -> _Subject:
    has_client = q.client_id is not None
    sid = (q.login_sid or "").strip()
    if not has_client and not sid:
        raise ExecCompError("SUBJECT_REQUIRED", "give exactly one of client_id or login_sid")
    if has_client and sid:
        raise ExecCompError("SUBJECT_AMBIGUOUS", "give client_id or login_sid, not both")
    if has_client:
        if not isinstance(q.client_id, int) or q.client_id <= 0:
            raise ExecCompError("VALIDATION_ERROR", "client_id must be a positive integer")
        return _Subject(f"client-{q.client_id}", q.client_id, None, None)
    m = _LOGIN_SID_RE.fullmatch(sid)
    if not m:
        raise ExecCompError(
            "INVALID_LOGIN_SID", "login_sid must be an MT5 account in the form 5-<login>"
        )
    login = int(m.group(1))
    return _Subject(f"login-5-{login}", None, login, f"5-{login}")


# --- result (cache + compute) --------------------------------------------------


@dataclass
class ComputeResult:
    """Internal result of one query run. Not part of the public contract."""

    as_of: dt.date
    summary: dict
    coverage: dict
    rows: list[dict]
    from_cache: bool
    date_to_eff: Optional[dt.date] = None
    ids: int = 0                    # deal numbers located for the range
    # Query log (one INFO line per query): per-phase wall time in ms, the
    # server-wide slot wait, and the position lookup size.
    timings: dict[str, int] = field(default_factory=dict)
    positions: int = 0
    # 02 §4.1 unit self-check over in-range close fills (04 §1.4).
    unit_checked: int = 0
    unit_mismatches: dict[str, int] = field(default_factory=dict)  # by symbol


_Result = ComputeResult


def _cache_key(subject: _Subject, date_from: dt.date, date_to: dt.date, as_of: dt.date) -> str:
    return (
        f"app:exec_comp:v{CALC_VERSION}:{subject.key}:"
        f"{date_from.isoformat()}:{date_to.isoformat()}:{as_of.isoformat()}"
    )


def _pack(obj: Any) -> str:
    # base64 because the shared client is decode_responses=True.
    raw = json.dumps(obj, separators=(",", ":"), default=str).encode()
    return base64.b64encode(zlib.compress(raw, 6)).decode("ascii")


def _unpack(s: str) -> Any:
    return json.loads(zlib.decompress(base64.b64decode(s)))


# Rows are cached as column arrays (one list per field) rather than a list of
# dicts: the keys are not repeated 100k times, and same-typed runs compress
# far better. Prod Redis is 256MB allkeys-lru shared by every page.
_ROW_COLS = tuple(OrderRow.model_fields)
_RAW_COLS = tuple(RawCodes.model_fields)


def _rows_to_columns(rows: list[dict]) -> dict:
    cols: dict[str, list] = {c: [r[c] for r in rows] for c in _ROW_COLS if c != "raw"}
    for c in _RAW_COLS:
        cols["raw." + c] = [r["raw"][c] for r in rows]
    return cols


def _columns_to_rows(cols: dict) -> list[dict]:
    names = [c for c in _ROW_COLS if c != "raw"]
    n = len(cols[names[0]]) if names else 0
    out = []
    for i in range(n):
        r = {c: cols[c][i] for c in names}
        r["raw"] = {c: cols["raw." + c][i] for c in _RAW_COLS}
        out.append(r)
    return out


def _cache_get(cache, key: str, need_rows: bool) -> Optional[ComputeResult]:
    if cache is None:
        return None
    try:
        head = cache.get(key + ":summary")
        if not head:
            return None
        rows = None
        if need_rows:
            packed = cache.get(key + ":rows")
            if not packed:
                return None   # rows too big to cache (or evicted): recompute
            rows = _columns_to_rows(_unpack(packed))
        meta = _unpack(head)
    except Exception as exc:  # noqa: BLE001 - Redis down = compute, never fail
        logger.warning("exec_comp cache read failed: %s", exc)
        return None
    return ComputeResult(
        as_of=dt.date.fromisoformat(meta["as_of"]),
        summary=meta["summary"],
        coverage=meta["coverage"],
        rows=rows or [],
        from_cache=True,
    )


def _cache_put(cache, key: str, res: ComputeResult, settings: Settings) -> None:
    ttl = int(settings.EXEC_COMP_CACHE_TTL_S)
    if cache is None or ttl <= 0:
        return
    try:
        packed_rows = _pack(_rows_to_columns(res.rows))
        max_bytes = int(settings.EXEC_COMP_CACHE_MAX_BYTES)
        # Rows first: a reader that finds the summary may go on to need rows.
        if len(packed_rows) <= max_bytes:
            cache.setex(key + ":rows", ttl, packed_rows)
        else:
            logger.debug(
                "exec_comp rows not cached key=%s size=%d > %d", key, len(packed_rows), max_bytes
            )
        cache.setex(
            key + ":summary",
            ttl,
            _pack({"as_of": res.as_of.isoformat(), "summary": res.summary, "coverage": res.coverage}),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("exec_comp cache write failed: %s", exc)


def _validate(q: Query, clock: Clock) -> tuple[_Subject, dt.date]:
    """Everything checkable without the replica. Returns (subject, default as_of)."""
    subject = _parse_subject(q)
    if q.date_from > q.date_to:
        raise ExecCompError("RANGE_INVALID", "date_from is after date_to")
    if q.date_from < EARLIEST_SRV_DATE:
        raise ExecCompError(
            "RANGE_BEFORE_COVERAGE",
            f"date_from must be on or after {EARLIEST_SRV_DATE.isoformat()} (first MT5 fill)",
        )
    default_as_of = _today_srv(clock) - dt.timedelta(days=1)
    if q.as_of is not None and q.as_of > default_as_of:
        raise ExecCompError(
            "AS_OF_TOO_LATE",
            f"as_of must be on or before {default_as_of.isoformat()} "
            "(the day before today, MT server day)",
        )
    _check_range(q, q.as_of or default_as_of)
    return subject, default_as_of


def _check_range(q: Query, as_of: dt.date) -> None:
    if q.date_from > min(q.date_to, as_of):
        raise ExecCompError(
            "RANGE_INVALID",
            f"data is only available through {as_of.isoformat()} (MT server day); "
            "choose a date_from on or before it",
        )


def _run_bounded(
    q: Query, subject: _Subject, source: FillSource, settings: Settings, default_as_of: dt.date,
    now_utc: dt.datetime, head: Any = None,
) -> ComputeResult:
    """One replica run inside a server-wide slot and the time budget."""
    waited = time.perf_counter()
    with QuerySlot(settings.EXEC_COMP_SLOT_DIR, settings.EXEC_COMP_MAX_CONCURRENT):
        slot_wait_ms = _ms(waited)
        deadline = Deadline(settings.EXEC_COMP_QUERY_BUDGET_S)
        bind = getattr(source, "with_deadline", None)
        src = bind(deadline) if callable(bind) else source
        try:
            res = _compute(q, subject, src, settings, deadline, default_as_of, now_utc, head)
            res.timings["slot_wait"] = slot_wait_ms
            return res
        finally:
            _close(src)
            if src is not source:
                _close(source)


def compute_uncached(
    q: Query,
    *,
    source: FillSource,
    clock: Clock = None,
    settings: Optional[Settings] = None,
) -> ComputeResult:
    """Same computation as ``summary`` without cache or query slot — for
    offline reconciliation (backend/scripts/exec_comp_reconcile.py). Exposes
    ``unit_checked`` / ``unit_mismatches``; ``rows`` are plain OrderRow dicts."""
    settings = settings or get_settings()
    subject, default_as_of = _validate(q, clock)
    deadline = Deadline(settings.EXEC_COMP_QUERY_BUDGET_S)
    bind = getattr(source, "with_deadline", None)
    src = bind(deadline) if callable(bind) else source
    try:
        return _compute(q, subject, src, settings, deadline, default_as_of, _now_utc(clock))
    finally:
        _close(src)
        if src is not source:
            _close(source)


def _result(
    q: Query, source: FillSource, cache, clock: Clock, settings: Settings, *, need_rows: bool,
    op: str = "summary",
) -> ComputeResult:
    """Cached result of one query, with exactly one ``exec_comp query`` log
    line per call (logging-system.md §2.2.1: user-driven, one line per action,
    like the AI assistant's ``AI turn`` line). The user and trace_id are added
    by the log format itself; this module never reads the caller's identity."""
    started = time.perf_counter()
    ran = {"compute": False}
    try:
        res = _result_inner(q, source, cache, clock, settings, need_rows=need_rows, ran=ran)
    except ExecCompError as exc:
        _log_query(op, q, None, started, "error", exc.code, exc.status)
        raise
    except Exception as exc:  # noqa: BLE001 - logged and re-raised unchanged
        _log_query(op, q, None, started, "error", type(exc).__name__, 500)
        raise
    cache_state = "miss" if ran["compute"] else ("hit" if res.from_cache else "coalesced")
    _log_query(op, q, res, started, cache_state, "ok", 200)
    return res


def _result_inner(
    q: Query, source: FillSource, cache, clock: Clock, settings: Settings, *, need_rows: bool,
    ran: dict,
) -> ComputeResult:
    subject, default_as_of = _validate(q, clock)
    now_utc = _now_utc(clock)
    candidate = q.as_of or default_as_of
    key = _cache_key(subject, q.date_from, min(q.date_to, candidate), candidate)
    hit = _cache_get(cache, key, need_rows)
    if hit is not None:
        return hit

    head = None
    if q.as_of is None and cache is not None:
        # Default as_of may step back (replica not past MT midnight yet). The
        # result is cached under the as_of it actually used, so look it up —
        # and coalesce concurrent runs — under that key too; otherwise every
        # request in that window recomputes. The head read is two PK/index
        # probes; it is handed to the run so it is not read twice.
        try:
            head = source.replica_head()
        except BaseException:
            _close(source)
            raise
        stepped = _stepped_as_of(head, default_as_of, now_utc, settings)
        if stepped != default_as_of:
            _check_range(q, stepped)
            key = _cache_key(subject, q.date_from, min(q.date_to, stepped), stepped)
            hit = _cache_get(cache, key, need_rows)
            if hit is not None:
                _close(source)
                return hit

    def compute() -> ComputeResult:
        ran["compute"] = True
        res = _run_bounded(q, subject, source, settings, default_as_of, now_utc, head)
        # Only complete results are reproducible (deals are write-once); a
        # partial one must be recomputed once the replica catches up.
        if res.coverage["complete"]:
            _cache_put(cache, _cache_key(subject, q.date_from, res.date_to_eff, res.as_of), res, settings)
        return res

    try:
        return _singleflight.do(key, compute)
    finally:
        # A waiter never runs ``compute`` — close the connection the head
        # probe may have opened (closing twice is a no-op).
        _close(source)


def _stepped_as_of(head, default_as_of: dt.date, now_utc: dt.datetime, settings: Settings) -> dt.date:
    """The as_of a default-as_of query uses: one day back while the replica
    has not finished the default day (01 D14, 04 §4)."""
    if _ready_through(head, default_as_of, now_utc, settings) < default_as_of:
        return default_as_of - dt.timedelta(days=1)
    return default_as_of


def _close(src) -> None:
    close = getattr(src, "close", None)
    if callable(close):
        close()


def _stats(from_cache: bool, started: float) -> Statistics:
    return Statistics(from_cache=from_cache, query_time_ms=int((time.perf_counter() - started) * 1000))


def _ms(since: float) -> int:
    return int((time.perf_counter() - since) * 1000)


# Codes that are an expected answer to the request (bad input, over the cap,
# server busy) stay INFO; a replica / data failure is WARNING
# (logging-system.md §2.2.1 rule 3: expected rejections are not failures).
_EXPECTED_REJECTIONS = frozenset({
    "SUBJECT_REQUIRED", "SUBJECT_AMBIGUOUS", "INVALID_LOGIN_SID", "SUBJECT_NOT_FOUND",
    "RANGE_INVALID", "RANGE_BEFORE_COVERAGE", "AS_OF_TOO_LATE", "SORT_NOT_ALLOWED",
    "VALIDATION_ERROR", "QUERY_TOO_LARGE", "BUSY",
})
_PHASES = ("slot_wait", "accounts", "ids", "fills", "rows", "legs", "summary")


def _log_query(
    op: str, q: Query, res: Optional[ComputeResult], started: float, cache_state: str,
    outcome: str, status: int,
) -> None:
    """``exec_comp query:`` — one line per query call; grep this token to see
    who queried what and where the time went. Subject and dates are logged
    (they are the investigation record); no money figures."""
    subject = (
        f"client:{q.client_id}" if q.client_id is not None else f"login:{(q.login_sid or '').strip()}"
    )
    parts = [
        f"op={op}",
        f"subject={subject}",
        f"range={q.date_from.isoformat()}..{q.date_to.isoformat()}",
        f"as_of={res.as_of.isoformat() if res else (q.as_of.isoformat() if q.as_of else 'default')}",
        f"cache={cache_state}",
        f"outcome={outcome}",
        f"total_ms={_ms(started)}",
    ]
    if res is not None:
        parts.append(f"rows={len(res.rows) if res.rows else res.summary.get('deals', 0)}")
        if cache_state == "miss":
            s = res.summary
            parts += [
                f"logins={len(s.get('subject', {}).get('logins', []))}",
                f"deal_ids={res.ids}",
                f"counted={s.get('deals', 0)}",
                f"positions={res.positions}",
                f"complete={res.coverage.get('complete')}",
            ]
            parts += [f"{k}_ms={res.timings[k]}" for k in _PHASES if k in res.timings]
    level = logging.INFO
    if outcome != "ok" and outcome not in _EXPECTED_REJECTIONS:
        level = logging.WARNING
    logger.log(level, "exec_comp query: %s", " ".join(parts))


# --- the computation -------------------------------------------------------


def _resolve_accounts(subject: _Subject, source: FillSource) -> list[Account]:
    if subject.client_id is not None:
        return list(source.accounts_for_client(subject.client_id))
    acct = source.account(subject.login)
    if acct is None:
        raise ExecCompError("SUBJECT_NOT_FOUND", f"{subject.login_sid} is not an MT5 account")
    return [acct]


def _compute(
    q: Query,
    subject: _Subject,
    source: FillSource,
    settings: Settings,
    deadline: Deadline,
    default_as_of: dt.date,
    now_utc: dt.datetime,
    head: Any = None,
) -> ComputeResult:
    timings: dict[str, int] = {}
    t = time.perf_counter()
    accounts = _resolve_accounts(subject, source)
    if head is None:
        head = source.replica_head()
    timings["accounts"] = _ms(t)
    ready = _ready_through(head, default_as_of, now_utc, settings)

    # as_of + readiness (01 D14, 04 §4).
    reason: Optional[str] = None
    if q.as_of is not None:
        as_of = q.as_of
    else:
        as_of = _stepped_as_of(head, default_as_of, now_utc, settings)
        if as_of != default_as_of:
            reason = (
                f"the replica has not finished {default_as_of.isoformat()} yet; "
                f"data cut-off stepped back to {as_of.isoformat()}"
            )
            _check_range(q, as_of)
    complete = head is not None and ready >= as_of
    if not complete:
        reason = (
            f"the replica only covers through {ready.isoformat()}; "
            f"results for days after that up to {as_of.isoformat()} are partial"
        )

    date_to = min(q.date_to, as_of)
    srv_from = _day_start(q.date_from)
    srv_to = _day_start(date_to + dt.timedelta(days=1))
    cutoff = _day_start(as_of + dt.timedelta(days=1))
    acct_by_login = {a.login: a for a in accounts}

    deadline.check("accounts")
    t = time.perf_counter()
    ids = source.deal_ids(sorted(acct_by_login), srv_from, srv_to) if accounts else []
    timings["ids"] = _ms(t)
    max_deals = int(settings.EXEC_COMP_MAX_DEALS)
    n_ids = len(ids)
    if n_ids > max_deals:
        raise ExecCompError(
            "QUERY_TOO_LARGE",
            f"本次查询涉及 {n_ids:,} 笔成交，超过单次上限 {max_deals:,} 笔。"
            "请缩小日期范围；如确需整段数据，请联系 IT 手动处理。",
        )
    deadline.check("deal ids")
    t = time.perf_counter()
    fills = source.fills(ids) if ids else []
    timings["fills"] = _ms(t)
    del ids
    deadline.check("fills")
    t = time.perf_counter()
    fills.sort(key=lambda x: x.deal_id)

    rows: list[dict] = []
    unmatched = 0
    unit_checked = 0
    unit_bad: dict[str, int] = defaultdict(int)
    odd: dict[str, int] = defaultdict(int)      # other_type / other_reason
    positions: set[tuple[int, int]] = set()
    for f in fills:
        if not srv_from <= f.time_msc < srv_to:
            continue
        acct = acct_by_login.get(f.login)
        if acct is None:
            continue
        if not f.order_found:
            unmatched += 1
            continue
        ok = calc.unit_check_ok(f)
        if ok is not None:
            unit_checked += 1
            if not ok:
                unit_bad[f.symbol] += 1
        cls = classify(f, acct)
        if cls in ("other_type", "other_reason"):
            odd[f"{cls}:{f.symbol}:type={f.order_type}:reason={f.order_reason}"] += 1
        comp = calc.comp_usd(f, acct.ccy)   # unknown currency raises: fail closed
        rows.append(_row(f, acct, cls, comp))
        if is_eligible(cls):
            positions.add((f.login, f.position_id))
    # The RawFill list is the largest object of a run; the rows carry
    # everything from here on, so let it go before position legs / summary.
    del fills
    timings["rows"] = _ms(t)

    # One aggregated line per query, never per row (CLAUDE.md log rule).
    if unit_bad:
        logger.warning(
            "exec_comp unit self-check mismatches (formula vs deals.Profit) subject=%s: %s",
            subject.key,
            ", ".join(f"{s}={n}" for s, n in sorted(unit_bad.items())),
        )
    if odd:
        logger.warning(
            "exec_comp unseen order classes subject=%s: %s",
            subject.key,
            ", ".join(f"{k}={n}" for k, n in sorted(odd.items())),
        )

    # 02 §6.1: fully closed by as_of? Lifecycle legs strictly before the cutoff.
    lookup = sorted(p for p in positions if p[1] != 0)
    t = time.perf_counter()
    legs = source.position_legs(lookup) if lookup else []
    timings["legs"] = _ms(t)
    deadline.check("position legs")
    t = time.perf_counter()
    remaining: dict[tuple[int, int], int] = defaultdict(int)
    seen: set[tuple[int, int]] = set()
    anomalies: set[tuple[int, int]] = {p for p in positions if p[1] == 0}
    for leg in legs:
        if leg.time_msc >= cutoff:
            continue
        p = (leg.login, leg.position_id)
        seen.add(p)
        if leg.entry == 0:
            remaining[p] += leg.volume
        elif leg.entry in (1, 3):
            remaining[p] -= leg.volume
        else:
            anomalies.add(p)
    for p in positions:
        if p not in seen or remaining[p] < 0:
            anomalies.add(p)
    open_positions = {p for p in positions if p not in anomalies and remaining[p] > 0}

    for r in rows:
        p = (r["login"], r["position_id"])
        if not r["eligible"]:
            r["not_counted_reason"] = "not_eligible_class"
        elif p in anomalies:
            r["not_counted_reason"] = "position_anomaly"
        elif p in open_positions:
            r["not_counted_reason"] = "position_open_at_as_of"
        else:
            r["counted"] = True

    summary_dict = build_summary(
        rows,
        logins=[a.login for a in accounts],
        client_id=subject.client_id,
        login_sid=subject.login_sid,
        date_from=q.date_from,
        date_to=date_to,
        date_to_requested=q.date_to,
        as_of=as_of,
    )
    coverage = Coverage(
        earliest_srv_date=EARLIEST_SRV_DATE,
        ready_through_srv_date=ready,
        complete=complete,
        reason=reason,
        unmatched_deals=unmatched,
        replica_latest_fill_utc=calc.iso_z(head.time_utc) if head else None,
    ).model_dump(mode="json")
    timings["summary"] = _ms(t)
    logger.debug(
        "exec_comp computed subject=%s %s..%s as_of=%s ids=%d rows=%d counted=%d unmatched=%d",
        subject.key, q.date_from, date_to, as_of, n_ids, len(rows),
        summary_dict["deals"], unmatched,
    )
    return ComputeResult(
        as_of=as_of,
        summary=summary_dict,
        coverage=coverage,
        rows=rows,
        from_cache=False,
        date_to_eff=date_to,
        ids=n_ids,
        unit_checked=unit_checked,
        unit_mismatches=dict(unit_bad),
        timings=timings,
        positions=len(positions),
    )


def _row(f: RawFill, acct: Account, cls: str, comp: Optional[float]) -> dict:
    offset = calc.server_offset(f)
    wpx = calc.worse_px(f)
    ref = calc.ref_price(f)
    return {
        "deal_id": f.deal_id,
        "order_id": f.order_id,
        "position_id": f.position_id,
        "login": f.login,
        "login_sid": f"5-{f.login}",
        "account_group": acct.group,
        "ccy": acct.ccy,
        "symbol": f.symbol,
        "side": calc.side(f),
        "entry": calc.ENTRY_KIND.get(f.entry, "inout"),
        "cls": cls,
        "eligible": is_eligible(cls),
        "counted": False,
        "not_counted_reason": None,
        "lots": round(f.volume / 10000.0, 4),
        "req_time_utc": calc.iso_z(f.time_setup_msc - offset) if f.time_setup_msc else None,
        "req_time_srv": calc.srv_str(f.time_setup_msc) if f.time_setup_msc else None,
        "fill_time_utc": calc.iso_z(f.time_msc - offset),
        "fill_time_srv": calc.srv_str(f.time_msc),
        "srv_date": f.time_msc.date().isoformat(),
        "delay_ms": calc.delay_ms(f),
        "ref_price": ref,
        "fill_price": f.price,
        "worse_px": wpx,
        "outcome": calc.outcome(wpx),
        "comp_usd": round(comp, _MONEY_DP) if comp is not None else None,
        # Internal: the unrounded amount the sums use. Not an OrderRow field,
        # never cached, never serialised.
        "_comp_raw": comp,
        "raw": {
            "action": f.action,
            "entry": f.entry,
            "order_type": f.order_type,
            "order_reason": f.order_reason,
            "dealer": f.dealer,
        },
    }


class _Acc:
    """Running Totals: deals / lots / net / positive-only."""

    __slots__ = ("deals", "volume", "net", "pos")

    def __init__(self) -> None:
        self.deals, self.volume, self.net, self.pos = 0, 0, 0.0, 0.0

    def add(self, volume: int, comp: Optional[float]) -> None:
        self.deals += 1
        self.volume += volume
        c = comp or 0.0
        self.net += c
        self.pos += max(0.0, c)

    def dump(self, **extra) -> dict:
        return {
            **extra,
            "deals": self.deals,
            "lots": round(self.volume / 10000.0, _LOTS_DP),
            "comp_net_usd": round(self.net, _MONEY_DP),
            "comp_positive_usd": round(self.pos, _MONEY_DP),
        }


def _percentile(sorted_vals: list[int], p: float) -> float:
    """numpy's default ('linear') percentile on pre-sorted values."""
    k = (len(sorted_vals) - 1) * p
    lo = math.floor(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo))


def build_summary(
    rows: list[dict],
    *,
    logins: list[int],
    client_id: Optional[int],
    login_sid: Optional[str],
    date_from: dt.date,
    date_to: dt.date,
    date_to_requested: dt.date,
    as_of: dt.date,
) -> dict:
    """The summary dict (``SummaryData`` shape) from finished row dicts.

    Pure: everything comes from the rows (``counted`` / ``not_counted_reason``
    already decided, unrounded ``_comp_raw``); ``logins`` fixes the
    ``by_account`` order and lists accounts that had no fill. Rows are summed
    in deal order, so the rows of several date chunks concatenated give the
    same result as one run over the whole range; delay percentiles are taken
    over all rows, never averaged."""
    total = _Acc()
    by_account = {f"5-{login}": _Acc() for login in logins}
    by_symbol: dict[str, _Acc] = defaultdict(_Acc)
    by_entry: dict[str, _Acc] = {}
    by_day: dict[str, _Acc] = defaultdict(_Acc)
    by_class: dict[str, _Acc] = defaultdict(_Acc)
    excluded = _Acc()
    excluded_pos: set = set()
    anomaly_pos: set = set()
    outcomes = {"worse": 0, "same": 0, "better": 0}
    delays: list[int] = []
    max_single: Optional[float] = None

    for r in sorted(rows, key=lambda x: x["deal_id"]):
        vol = int(round(r["lots"] * 10000))   # lots = volume / 10000, exact at 4 dp
        comp = r["_comp_raw"]
        if not r["eligible"]:
            by_class[r["cls"]].add(vol, comp)
            continue
        reason = r["not_counted_reason"]
        if reason == "position_open_at_as_of":
            excluded.add(vol, comp)
            excluded_pos.add((r["login"], r["position_id"]))
            continue
        if reason == "position_anomaly":
            anomaly_pos.add((r["login"], r["position_id"]))
            continue
        if not r["counted"]:
            continue
        total.add(vol, comp)
        by_account.setdefault(r["login_sid"], _Acc()).add(vol, comp)
        by_symbol[r["symbol"]].add(vol, comp)
        by_entry.setdefault(r["entry"], _Acc()).add(vol, comp)
        by_day[r["srv_date"]].add(vol, comp)
        if r["outcome"]:
            outcomes[r["outcome"]] += 1
        if r["delay_ms"] is not None:
            delays.append(int(r["delay_ms"]))
        if comp is not None and (max_single is None or comp > max_single):
            max_single = comp

    delays.sort()
    entry_order = {"open": 0, "close": 1}
    return {
        **total.dump(),
        "subject": {
            "client_id": client_id,
            "login_sid": login_sid,
            "logins": sorted(logins),
        },
        "query": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "date_to_requested": date_to_requested.isoformat(),
            "date_to_clipped": date_to < date_to_requested,
            "as_of": as_of.isoformat(),
        },
        "outcomes": outcomes,
        "delay": {
            "n": len(delays),
            "median_ms": _percentile(delays, 0.5) if delays else None,
            "p95_ms": _percentile(delays, 0.95) if delays else None,
            "max_ms": delays[-1] if delays else None,
            "min_ms": delays[0] if delays else None,
        },
        "max_single_comp_usd": round(max_single, _MONEY_DP) if max_single is not None else None,
        "by_account": [by_account[k].dump(key=k) for k in by_account],
        "by_symbol": [
            v.dump(key=k) for k, v in sorted(by_symbol.items(), key=lambda kv: (-kv[1].net, kv[0]))
        ],
        "by_entry": [
            v.dump(key=k) for k, v in sorted(by_entry.items(), key=lambda kv: entry_order.get(kv[0], 9))
        ],
        "by_day": [v.dump(key=k) for k, v in sorted(by_day.items())],
        "excluded_open": {
            "positions": len(excluded_pos),
            "deals": excluded.deals,
            "lots": round(excluded.volume / 10000.0, _LOTS_DP),
            "comp_net_usd_if_counted": round(excluded.net, _MONEY_DP),
        },
        "anomaly_positions": len(anomaly_pos),
        "not_counted_by_class": [
            v.dump(cls=k) for k, v in sorted(by_class.items(), key=lambda kv: (-kv[1].deals, kv[0]))
        ],
    }


# --- paging -----------------------------------------------------------------


def _select(rows: list[dict], view: str) -> list[dict]:
    if view == "all":
        return rows
    if view == "excluded_open":
        return [r for r in rows if r["not_counted_reason"] == "position_open_at_as_of"]
    return [r for r in rows if r["counted"]]


def _sorted(rows: list[dict], sort_by: str, sort_dir: str) -> list[dict]:
    """Whitelisted key, ties broken by deal_id ascending, None values last."""
    base = sorted(rows, key=lambda r: r["deal_id"])
    if sort_by == "deal_id":
        return base if sort_dir == "asc" else base[::-1]
    present = [r for r in base if r.get(sort_by) is not None]
    missing = [r for r in base if r.get(sort_by) is None]
    present.sort(key=lambda r: r[sort_by], reverse=(sort_dir == "desc"))
    return present + missing
