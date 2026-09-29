"""Execution-price compensation (成交价差补偿) — entry A, /api/v1/exec-compensation.

Thin by design (03 §4.1): authenticate (session + the ``data`` module gate on
the parent router), turn HTTP params into a ``Query``, call the query core.
Every rule — subject resolution, as_of, clipping, classification, netting,
open-position exclusion, paging, thresholds, caching — lives in
``services/exec_comp/query.py`` so the future external entry (03 §4.4) cannot
grow a second copy of it.

All handlers are plain ``def``: the core does blocking replica / Redis IO.
No audit rows: pure reads (01 D16). Errors are ``ExecCompError`` rendered by
the handler in ``main.py`` as ``{"error": {"code", "message"}}``.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Query as Q
from fastapi.responses import Response

from app.core.config import get_settings
from app.schemas.exec_compensation import (
    ErrorResponse,
    OrdersResponse,
    OrdersView,
    SortBy,
    SortDir,
    StatusResponse,
    SummaryResponse,
)
from app.services.exec_comp import export, query
from app.services.exec_comp.source import ReplicaFillSource

router = APIRouter(prefix="/exec-compensation")

_ERRORS = {
    404: {"model": ErrorResponse, "description": "SUBJECT_NOT_FOUND"},
    422: {"model": ErrorResponse, "description": "Invalid parameters / QUERY_TOO_LARGE (deal cap)"},
    500: {"model": ErrorResponse, "description": "UNKNOWN_CURRENCY"},
    503: {
        "model": ErrorResponse,
        "description": "BUSY (all query slots taken) / UPSTREAM_UNAVAILABLE (replica unreachable) / "
        "QUERY_BUDGET_EXCEEDED (time budget ran out under load); carries Retry-After",
    },
    504: {"model": ErrorResponse, "description": "UPSTREAM_TIMEOUT"},
}

_CLIENT_ID = Q(None, description="CRM client id (fxbackoffice.users.id); exclusive with login_sid")
_LOGIN_SID = Q(None, description="One MT5 account, '5-<login>'; exclusive with client_id")
_DATE_FROM = Q(..., description="First MT server day (inclusive)")
_DATE_TO = Q(..., description="Last MT server day (inclusive); clipped to as_of")
_AS_OF = Q(None, description="Data cut-off MT server day; default = yesterday")


def _query(client_id, login_sid, date_from, date_to, as_of) -> query.Query:
    return query.Query(
        client_id=client_id, login_sid=login_sid, date_from=date_from, date_to=date_to, as_of=as_of
    )


def _source() -> ReplicaFillSource:
    return ReplicaFillSource(get_settings())


@router.get("/summary", response_model=SummaryResponse, responses=_ERRORS)
def get_summary(
    client_id: Optional[int] = _CLIENT_ID,
    login_sid: Optional[str] = _LOGIN_SID,
    date_from: date = _DATE_FROM,
    date_to: date = _DATE_TO,
    as_of: Optional[date] = _AS_OF,
) -> SummaryResponse:
    return query.summary(
        _query(client_id, login_sid, date_from, date_to, as_of),
        source=_source(),
        cache=query.default_cache(),
    )


@router.get("/orders", response_model=OrdersResponse, responses=_ERRORS)
def get_orders(
    client_id: Optional[int] = _CLIENT_ID,
    login_sid: Optional[str] = _LOGIN_SID,
    date_from: date = _DATE_FROM,
    date_to: date = _DATE_TO,
    as_of: Optional[date] = _AS_OF,
    view: OrdersView = Q("counted"),
    page: int = Q(1),
    page_size: int = Q(100),
    sort_by: SortBy = Q("fill_time_srv"),
    sort_dir: SortDir = Q("asc"),
) -> OrdersResponse:
    return query.orders(
        _query(client_id, login_sid, date_from, date_to, as_of),
        source=_source(),
        cache=query.default_cache(),
        view=view,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


@router.get(
    "/export",
    response_class=Response,
    responses={
        200: {"content": {export.XLSX_MEDIA_TYPE: {}}, "description": "xlsx: 汇总 + 明细"},
        **_ERRORS,
    },
)
def get_export(
    client_id: Optional[int] = _CLIENT_ID,
    login_sid: Optional[str] = _LOGIN_SID,
    date_from: date = _DATE_FROM,
    date_to: date = _DATE_TO,
    as_of: Optional[date] = _AS_OF,
) -> Response:
    summary, rows = query.export_rows(
        _query(client_id, login_sid, date_from, date_to, as_of),
        source=_source(),
        cache=query.default_cache(),
    )
    return Response(
        content=export.build_xlsx(summary, rows),
        media_type=export.XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": export.content_disposition(export.filename(summary))},
    )


@router.get("/status", response_model=StatusResponse, responses=_ERRORS)
def get_status() -> StatusResponse:
    return query.status(source=_source())
