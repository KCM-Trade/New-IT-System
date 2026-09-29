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
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

from app.schemas.exec_compensation import (
    OrderRow,
    OrdersResponse,
    OrdersView,
    SortBy,
    SortDir,
    StatusResponse,
    SummaryResponse,
)

from .models import FillSource

# First MT5 fill ever (2023-02-20, 01 D15); lower bound for date_from.
EARLIEST_SRV_DATE = dt.date(2023, 2, 20)


@dataclass(frozen=True)
class Query:
    client_id: Optional[int]
    login_sid: Optional[str]      # "5-<login>"
    date_from: dt.date
    date_to: dt.date
    as_of: Optional[dt.date] = None   # None = default (yesterday, MT server day)


def summary(q: Query, *, source: FillSource) -> SummaryResponse:
    raise NotImplementedError


def orders(
    q: Query,
    *,
    source: FillSource,
    view: OrdersView = "counted",
    page: int = 1,
    page_size: int = 100,
    sort_by: SortBy = "fill_time_srv",
    sort_dir: SortDir = "asc",
) -> OrdersResponse:
    raise NotImplementedError


def export_rows(
    q: Query, *, source: FillSource
) -> tuple[SummaryResponse, list[OrderRow]]:
    """Full summary + every in-range row (view=all) for the xlsx export."""
    raise NotImplementedError


def status(*, source: FillSource) -> StatusResponse:
    raise NotImplementedError
