"""REST API for trade-IP profit attribution (OPT-0063 Phase 2).

Mounted under `/api/v1/login-ip/trade-profit/*`. The /login-ips page is a `cs`
module page, but THIS prefix is carved out to the `risk` module in
`core/auth_deps.MODULE_MAP` (longest tuple wins) — the boss-facing mule-cluster
view is risk-only.

All four endpoints are read-only GETs over the precomputed `trade_ip_pnl`
SQLite table (the nightly 08:30 reconcile owns the MySQL slave reads), so
they are plain `def` — FastAPI runs them in the threadpool and a slow window
scan cannot stall the event loop. Query-type GETs are never audited (the
audit rule: a human, changing state, succeeding — all three or nothing).
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date

from fastapi import APIRouter, HTTPException, Query

from ....schemas.login_ip_trade_profit import (
    TradeProfitCoverageResponse,
    TradeProfitGroupDetailResponse,
    TradeProfitGroupIp,
    TradeProfitGroupRow,
    TradeProfitGroupsResponse,
    TradeProfitIpRow,
    TradeProfitIpsResponse,
    TradeProfitStatistics,
)
from ....services import login_ip_trade_profit_service as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/login-ip/trade-profit")

# The tables hold at most the retention window (trade_ip_pnl: 400 days), so a
# wider requested window can only ever read air.
_MAX_WINDOW_DAYS = 400


_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _parse_day(value: str, name: str) -> str:
    """Validate a YYYY-MM-DD query param; return it normalized.

    Strict about the SHAPE, not just the value: since 3.11 fromisoformat also
    accepts the compact 'YYYYMMDD', and silently taking a second format is how
    an undocumented caller contract gets baked in.
    """
    if not _DAY_RE.fullmatch(value):
        raise HTTPException(400, f"{name} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise HTTPException(400, f"{name} must be YYYY-MM-DD") from None


def _window(date_from: str, date_to: str) -> tuple[str, str]:
    date_from = _parse_day(date_from, "from")
    date_to = _parse_day(date_to, "to")
    if date_from > date_to:
        raise HTTPException(400, "from must be <= to")
    span = (date.fromisoformat(date_to) - date.fromisoformat(date_from)).days
    if span > _MAX_WINDOW_DAYS:
        raise HTTPException(400, f"window too wide (>{_MAX_WINDOW_DAYS} days)")
    return date_from, date_to


@router.get("/groups", response_model=TradeProfitGroupsResponse)
def list_trade_profit_groups(
    date_from: str = Query(alias="from"),
    date_to: str = Query(alias="to"),
    min_clients: int = Query(default=2, ge=1, le=100),
    public_ip_clients: int = Query(default=10, ge=2, le=1000),
    include_same_client: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    """Account groups (union-find over shared private IPs), profit desc."""
    date_from, date_to = _window(date_from, date_to)
    t0 = time.perf_counter()
    params = svc.GroupParams(
        date_from=date_from,
        date_to=date_to,
        min_clients=min_clients,
        public_ip_clients=public_ip_clients,
        include_same_client=include_same_client,
    )
    groups, from_cache = svc.get_groups(params)
    total = len(groups)
    page_rows = groups[(page - 1) * page_size : page * page_size]

    # Window context for the coverage cross-check (cheap aggregate).
    coverage = svc.get_coverage(date_from, date_to)
    return TradeProfitGroupsResponse(
        data=[TradeProfitGroupRow(**g) for g in page_rows],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max((total + page_size - 1) // page_size, 1),
        statistics=TradeProfitStatistics(
            from_cache=from_cache,
            query_time_ms=int((time.perf_counter() - t0) * 1000),
            window_with_ip_trades=coverage["with_ip_trades"],
            groups_trades=sum(g["trades"] for g in groups),
        ),
    )


@router.get("/groups/{group_id}", response_model=TradeProfitGroupDetailResponse)
def get_trade_profit_group(
    group_id: str,
    date_from: str = Query(alias="from"),
    date_to: str = Query(alias="to"),
    min_clients: int = Query(default=2, ge=1, le=100),
    public_ip_clients: int = Query(default=10, ge=2, le=1000),
    include_same_client: bool = Query(default=False),
):
    """One group: account detail + member IPs (geo, window clients, bridge).

    The group_id is a hash of (window, thresholds, account list), so the same
    parameter set that produced the list MUST be echoed here — a different
    window or threshold produces different groups and this id 404s.
    """
    date_from, date_to = _window(date_from, date_to)
    params = svc.GroupParams(
        date_from=date_from,
        date_to=date_to,
        min_clients=min_clients,
        public_ip_clients=public_ip_clients,
        include_same_client=include_same_client,
    )
    detail = svc.get_group_detail(group_id, params)
    if detail is None:
        raise HTTPException(
            404,
            "group not found under these parameters — group_id encodes the "
            "window and thresholds; echo the list call's parameters",
        )
    return TradeProfitGroupDetailResponse(
        group=detail["group"],
        member_ips=[TradeProfitGroupIp(**ip) for ip in detail["member_ips"]],
        params=detail["params"],
        statistics=detail["statistics"],
    )


@router.get("/ips", response_model=TradeProfitIpsResponse)
def list_trade_profit_ips(
    date_from: str = Query(alias="from"),
    date_to: str = Query(alias="to"),
    public_ip_clients: int = Query(default=10, ge=2, le=1000),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    """Per-IP profit ranking. Kept alongside the group view because an
    IP-level finding (a VPS like 45.32.124.94) would be hidden inside its
    group otherwise."""
    date_from, date_to = _window(date_from, date_to)
    t0 = time.perf_counter()
    rows, from_cache = svc.get_ip_ranking(date_from, date_to, public_ip_clients)
    total = len(rows)
    page_rows = rows[(page - 1) * page_size : page * page_size]

    # Cache-only geo for the page slice — never bill MaxMind from a list view.
    countries: dict[str, str] = {}
    try:
        from ....core import login_ip_db

        countries = login_ip_db.get_cached_countries([r["ip"] for r in page_rows])
    except Exception as exc:  # noqa: BLE001 — geo is decoration
        logger.warning("trade-profit geo cache unavailable: %s", exc)

    return TradeProfitIpsResponse(
        data=[TradeProfitIpRow(country=countries.get(r["ip"]), **r) for r in page_rows],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max((total + page_size - 1) // page_size, 1),
        statistics=TradeProfitStatistics(
            from_cache=from_cache,
            query_time_ms=int((time.perf_counter() - t0) * 1000),
        ),
    )


@router.get("/coverage", response_model=TradeProfitCoverageResponse)
def get_trade_profit_coverage(
    date_from: str = Query(alias="from"),
    date_to: str = Query(alias="to"),
):
    """How much of the window carries an open IP, and why the rest doesn't."""
    date_from, date_to = _window(date_from, date_to)
    return TradeProfitCoverageResponse(**svc.get_coverage(date_from, date_to))
