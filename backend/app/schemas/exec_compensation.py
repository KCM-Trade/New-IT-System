"""Public contract of the execution-compensation API (成交价差补偿).

SSOT: docs/exec-compensation/ (01 decisions, 02 data contract, 03 §4 API rules).

This module is the contract shared by every entry point: the v1 internal
route (/api/v1/exec-compensation/*) today, and the reserved external route
(/api/ext/v1/exec-compensation/*) later. Rules (03 §4.2):

- Fields are only ever added. Renaming / removing / changing meaning = v2.
- Enums are stable strings; raw MT integers are only attached under ``raw``.
- Times are UTC ISO8601 ``...Z``; ``*_srv`` fields are MT server wall clock;
  ``srv_date`` is the MT server day. Date filters use the MT server day.
- Money is USD (CEN already / 100); the account currency is in ``ccy``.

The OpenAPI snapshot test (tests/test_exec_comp_contract.py) fails on any
field change here — that is the point: update the snapshot deliberately.
"""

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

CALC_VERSION = 1

# --- enums -----------------------------------------------------------------

# 02 §3 classification (in evaluation order). Only "market" is compensable.
DealClass = Literal[
    "no_plugin",            # deals.Dealer = 1 (oneZero gateway, 01 D19)
    "plugin_passthrough",   # (group, Dealer) exclusion table (01 D20)
    "close_by",
    "so_first",
    "so_rest",
    "limit",
    "stop",
    "other_type",
    "sl",
    "tp",
    "market",
    "dealer",
    "other_reason",
]
Side = Literal["buy", "sell"]
EntryKind = Literal["open", "close", "inout", "close_by"]
Outcome = Literal["worse", "same", "better"]
NotCountedReason = Literal[
    "not_eligible_class",      # cls != market (01 D12 / D19 / D20)
    "position_open_at_as_of",  # position not fully closed by as_of (01 D13)
    "position_anomaly",        # e.g. Entry = 2 in the position lifecycle (02 §6.1)
]
OrdersView = Literal["counted", "excluded_open", "all"]
SortBy = Literal[
    "fill_time_srv", "comp_usd", "delay_ms", "lots", "symbol", "login", "deal_id"
]
SortDir = Literal["asc", "desc"]

ErrorCode = Literal[
    "SUBJECT_REQUIRED",       # neither client_id nor login_sid
    "SUBJECT_AMBIGUOUS",      # both given
    "INVALID_LOGIN_SID",      # not "5-<login>"
    "SUBJECT_NOT_FOUND",      # login_sid not an MT5 account
    "RANGE_INVALID",          # date_from > date_to
    "RANGE_BEFORE_COVERAGE",  # date_from < earliest_srv_date
    "AS_OF_TOO_LATE",         # as_of later than the default (yesterday)
    "SORT_NOT_ALLOWED",
    "VALIDATION_ERROR",       # any other malformed parameter
    "QUERY_TOO_LARGE",        # over the deal cap / time budget (01 D21)
    "BUSY",                   # all server-wide query slots taken (01 D21)
    "UNKNOWN_CURRENCY",       # account currency outside {CEN, USD}: fail closed
    "UPSTREAM_TIMEOUT",       # replica statement killed by MAX_EXECUTION_TIME
]

# HTTP status per error code — part of the contract (callers must not retry
# 4xx; 503/504 are retryable later, not immediately).
ERROR_STATUS: dict[str, int] = {
    "SUBJECT_REQUIRED": 422, "SUBJECT_AMBIGUOUS": 422, "INVALID_LOGIN_SID": 422,
    "SUBJECT_NOT_FOUND": 404, "RANGE_INVALID": 422, "RANGE_BEFORE_COVERAGE": 422,
    "AS_OF_TOO_LATE": 422, "SORT_NOT_ALLOWED": 422, "VALIDATION_ERROR": 422,
    "QUERY_TOO_LARGE": 422, "BUSY": 503, "UNKNOWN_CURRENCY": 500,
    "UPSTREAM_TIMEOUT": 504,
}


# --- shared blocks ---------------------------------------------------------


class Basis(BaseModel):
    """Machine-readable statement of how every number was computed."""

    reference: Literal["request_price"] = "request_price"
    order_scope: Literal["client_market_open_close"] = "client_market_open_close"
    netting: Literal["net"] = Field(
        "net", description="Headline figure nets better fills against worse ones; "
        "comp_positive_usd is given alongside."
    )
    exclude_open_positions: Literal[True] = True
    day_boundary: Literal["mt_server"] = "mt_server"
    currency: Literal["USD"] = "USD"
    calc_version: int = CALC_VERSION


class Coverage(BaseModel):
    earliest_srv_date: date = Field(..., description="First MT5 fill day; lower bound for date_from")
    ready_through_srv_date: date = Field(
        ..., description="Latest as_of the replica fully covers (normally yesterday)"
    )
    complete: bool = Field(
        ..., description="False = the replica has not caught up to as_of; numbers are partial"
    )
    reason: Optional[str] = None
    unmatched_deals: int = Field(
        0, description="Fills in range whose order row was not found (never dropped silently; "
        "not counted)"
    )
    replica_latest_fill_utc: Optional[str] = None


class Statistics(BaseModel):
    from_cache: bool = False
    query_time_ms: int = 0


class Subject(BaseModel):
    client_id: Optional[int] = None
    login_sid: Optional[str] = None
    logins: List[int] = Field(default_factory=list, description="MT5 logins covered")


class QueryEcho(BaseModel):
    """The effective query after defaults / clipping."""

    date_from: date
    date_to: date
    date_to_requested: date
    date_to_clipped: bool = Field(..., description="True when date_to was cut to as_of")
    as_of: date


# --- summary ---------------------------------------------------------------


class Totals(BaseModel):
    deals: int = 0
    lots: float = 0.0
    comp_net_usd: float = 0.0
    comp_positive_usd: float = 0.0


class GroupRow(Totals):
    key: str


class OutcomeCounts(BaseModel):
    worse: int = 0
    same: int = 0
    better: int = 0


class DelayStats(BaseModel):
    """Over counted fills only."""

    n: int = 0
    median_ms: Optional[float] = None
    p95_ms: Optional[float] = None
    max_ms: Optional[int] = None
    min_ms: Optional[int] = None


class ExcludedOpen(BaseModel):
    """Eligible fills left out because their position was still open at as_of (01 D13)."""

    positions: int = 0
    deals: int = 0
    lots: float = 0.0
    comp_net_usd_if_counted: float = 0.0


class ClassRow(Totals):
    cls: DealClass


class SummaryData(Totals):
    subject: Subject
    query: QueryEcho
    outcomes: OutcomeCounts
    delay: DelayStats
    max_single_comp_usd: Optional[float] = Field(None, description="Largest comp_usd among counted fills")
    by_account: List[GroupRow] = Field(default_factory=list, description="key = login_sid")
    by_symbol: List[GroupRow] = Field(default_factory=list)
    by_entry: List[GroupRow] = Field(default_factory=list, description="key = open | close")
    by_day: List[GroupRow] = Field(default_factory=list, description="key = MT server day YYYY-MM-DD")
    excluded_open: ExcludedOpen
    anomaly_positions: int = 0
    not_counted_by_class: List[ClassRow] = Field(
        default_factory=list, description="In-range fills of non-compensable classes"
    )


class SummaryResponse(BaseModel):
    data: SummaryData
    basis: Basis
    as_of: date
    coverage: Coverage
    statistics: Statistics


# --- orders ----------------------------------------------------------------


class RawCodes(BaseModel):
    action: int
    entry: int
    order_type: Optional[int] = None
    order_reason: Optional[int] = None
    dealer: Optional[int] = None


class OrderRow(BaseModel):
    """One in-range fill that has its order row. Fills whose order row is
    missing are not rows; they only appear as ``coverage.unmatched_deals``."""

    deal_id: int
    order_id: int
    position_id: int
    login: int
    login_sid: str
    account_group: Optional[str] = Field(
        None, description="mt4_users.GROUP; explains plugin_passthrough rows (01 D20)"
    )
    ccy: Literal["CEN", "USD"]
    symbol: str
    side: Side
    entry: EntryKind
    cls: DealClass
    eligible: bool
    counted: bool
    not_counted_reason: Optional[NotCountedReason] = None
    lots: float
    req_time_utc: Optional[str] = None
    req_time_srv: Optional[str] = Field(None, description="MT server wall clock of the request")
    fill_time_utc: str
    fill_time_srv: str = Field(..., description="MT server wall clock, 'YYYY-MM-DD HH:MM:SS.fff'")
    srv_date: date
    delay_ms: Optional[int] = None
    ref_price: Optional[float] = None
    fill_price: float
    worse_px: Optional[float] = Field(None, description="> 0 = worse for the client")
    outcome: Optional[Outcome] = None
    comp_usd: Optional[float] = Field(None, description="Not truncated; negative = better fill")
    raw: RawCodes


class OrdersResponse(BaseModel):
    data: List[OrderRow]
    total: int
    page: int
    page_size: int
    total_pages: int
    basis: Basis
    as_of: date
    coverage: Coverage
    statistics: Statistics


# --- status ----------------------------------------------------------------


class Limits(BaseModel):
    statement_timeout_ms: int
    query_budget_s: int
    max_deals: int
    max_concurrent: int
    cache_ttl_s: int
    page_size_max: int


class StatusData(BaseModel):
    calc_version: int = CALC_VERSION
    earliest_srv_date: date
    default_as_of: date
    ready_through_srv_date: date
    replica_latest_fill_utc: Optional[str] = None
    limits: Limits


class StatusResponse(BaseModel):
    data: StatusData
    basis: Basis


# --- errors ----------------------------------------------------------------


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
