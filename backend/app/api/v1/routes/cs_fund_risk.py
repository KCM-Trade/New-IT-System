"""CS fund risk-control ledger endpoints (出入金风控台账).

SSOT: docs/features/fund-risk-ledger.md §4. HTTP only: parameters in, service
calls, status-code mapping, audit rows. Every handler is a plain ``def`` — the
service layer does blocking PG / MySQL IO.

Module gate: ``/cs`` -> ``cs`` (MODULE_MAP). Data scope: every path is OPEN
(user decision 2026-10-13, see core/data_scope.ROUTE_SCOPE). DELETE is
manager-only.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import JSONResponse, Response

from app.core.audit import Auditor, get_auditor
from app.core.auth_deps import require_manager
from app.core.risk_cases_pg import RiskCasesUnavailable
from app.schemas.cs_fund_risk import (
    Authority,
    Direction,
    FrozenScope,
    Incident,
    IncidentConflict,
    IncidentCreate,
    IncidentListResponse,
    IncidentStatistics,
    IncidentUpdate,
    OrderPreviewResponse,
)
from app.services.cs_ledger import export, incidents, order_lookup
from app.services.cs_ledger.order_lookup import ReplicaUnavailable

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cs/fund-risk")

MAX_PAGE_SIZE = 200
ANONYMOUS_ACTOR = "unknown"   # only reachable with AUTH_ENABLED=false

_CONFLICT = {409: {"model": IncidentConflict, "description": "Duplicate transaction or stale version"}}
_UNAVAILABLE = {503: {"description": "Ledger database or MySQL replica unavailable"}}
_NOT_FOUND = {404: {"description": "Not found"}}

_DATE_FROM = Query(default=None, description="reported_date >= (inclusive)")
_DATE_TO = Query(default=None, description="reported_date <= (inclusive)")
_CHANNEL = Query(default=None, max_length=120, description="Exact channel label")
_PENDING = Query(default=None, description="true = result_text is empty")
_Q = Query(default=None, pattern=r"^\d{1,18}$", description="Transaction id or client id (exact)")
_SORT = Query(default="reported_date", pattern="^(reported_date|txn_processed_at|id)$")
_ORDER = Query(default="desc", pattern="^(asc|desc)$")


def _actor(audit: Auditor) -> str:
    """The session subject; never taken from the request body."""
    return audit.actor_email or ANONYMOUS_ACTOR


def _pg_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"ledger database unavailable: {exc}",
    )


def _conflict(detail: str, *, existing_id: Optional[int] = None,
              current_version: Optional[int] = None) -> JSONResponse:
    body = IncidentConflict(detail=detail, existing_id=existing_id, current_version=current_version)
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=body.model_dump())


def _filters(date_from, date_to, channel, direction, authority, frozen_scope,
             pending_result, q) -> incidents.ListFilters:
    return incidents.ListFilters(
        date_from=date_from, date_to=date_to, channel=channel, direction=direction,
        authority=authority, frozen_scope=frozen_scope, pending_result=pending_result, q=q,
    )


@router.get(
    "/order/{transaction_id}",
    response_model=OrderPreviewResponse,
    responses={**_NOT_FOUND, **_UNAVAILABLE},
)
def preview_order(transaction_id: int = Path(..., gt=0)) -> OrderPreviewResponse:
    """Read one CRM order for staff to check before recording it."""
    try:
        lookup = order_lookup.fetch_order(transaction_id)
    except ReplicaUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    if lookup is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"transaction {transaction_id} not found")
    if not lookup.recordable:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=lookup.reject_reason)
    # The replica connection is closed by now; only then touch PG.
    try:
        existing_id = incidents.find_existing_id(transaction_id)
    except RiskCasesUnavailable as exc:
        raise _pg_unavailable(exc) from exc
    return OrderPreviewResponse(order=lookup.snapshot, existing_id=existing_id, warnings=lookup.warnings)


@router.get("/incidents", response_model=IncidentListResponse, responses=_UNAVAILABLE)
def list_incidents(
    date_from: Optional[date] = _DATE_FROM,
    date_to: Optional[date] = _DATE_TO,
    channel: Optional[str] = _CHANNEL,
    direction: Optional[Direction] = Query(default=None),
    authority: Optional[Authority] = Query(default=None),
    frozen_scope: Optional[FrozenScope] = Query(default=None),
    pending_result: Optional[bool] = _PENDING,
    q: Optional[str] = _Q,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    sort: str = _SORT,
    order: str = _ORDER,
) -> IncidentListResponse:
    t0 = time.perf_counter()
    try:
        rows, total = incidents.list_rows(
            _filters(date_from, date_to, channel, direction, authority, frozen_scope, pending_result, q),
            page=page, page_size=page_size, sort=sort, order=order,
        )
    except RiskCasesUnavailable as exc:
        raise _pg_unavailable(exc) from exc
    # PG connection released above; the replica is queried on its own.
    data, replica_live = incidents.hydrate(rows)
    return IncidentListResponse(
        data=data,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=incidents.total_pages(total, page_size),
        statistics=IncidentStatistics(
            query_time_ms=int((time.perf_counter() - t0) * 1000),
            replica_live=replica_live,
        ),
    )


@router.post(
    "/incidents",
    response_model=Incident,
    responses={**_NOT_FOUND, **_CONFLICT, **_UNAVAILABLE},
)
def create_incident(body: IncidentCreate, audit: Auditor = Depends(get_auditor)):
    """Record one order. The order fields are re-read server-side."""
    try:
        result = incidents.create_incident(body, _actor(audit))
    except incidents.OrderNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (incidents.OrderNotRecordable, incidents.InvalidProvince, incidents.GroupNotFound) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ReplicaUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except incidents.DuplicateTransaction as exc:
        return _conflict(str(exc), existing_id=exc.existing_id)
    except RiskCasesUnavailable as exc:
        raise _pg_unavailable(exc) from exc
    row = result.row
    # After the write succeeded, never before.
    audit.record(
        "fund_risk.incident.create",
        target=incidents.audit_target(row),
        new_value=incidents.plain_row({k: v for k, v in row.items() if k != "import_raw"}),
    )
    return incidents.to_incident(row, result.lookup)


@router.get(
    "/incidents/{incident_id}",
    response_model=Incident,
    responses={**_NOT_FOUND, **_UNAVAILABLE},
)
def get_incident(incident_id: int = Path(..., gt=0)) -> Incident:
    try:
        row = incidents.get_row(incident_id)
    except incidents.IncidentNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="incident not found") from exc
    except RiskCasesUnavailable as exc:
        raise _pg_unavailable(exc) from exc
    return incidents.hydrate([row])[0][0]


@router.put(
    "/incidents/{incident_id}",
    response_model=Incident,
    responses={**_NOT_FOUND, **_CONFLICT, **_UNAVAILABLE},
)
def update_incident(
    body: IncidentUpdate,
    incident_id: int = Path(..., gt=0),
    audit: Auditor = Depends(get_auditor),
):
    """Replace the staff-entered fields. A request that changes nothing is a 200 no-op."""
    try:
        result = incidents.update_incident(incident_id, body, _actor(audit))
    except incidents.IncidentNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="incident not found") from exc
    except incidents.InvalidProvince as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except incidents.VersionConflict as exc:
        return _conflict(str(exc), current_version=exc.current_version)
    except RiskCasesUnavailable as exc:
        raise _pg_unavailable(exc) from exc
    if result.changes:
        for change in result.changes:
            audit.record_diff(
                "fund_risk.incident.update", target=change.target, old=change.old, new=change.new
            )
    else:
        # An unchanged save is not an event: no audit row, no version bump.
        # Same handling as the client-remark route's no-op path.
        logger.info(
            "No-op save: action=%s target=%s (nothing changed)",
            "fund_risk.incident.update",
            incidents.audit_target(result.row),
        )
    return incidents.hydrate([result.row])[0][0]


@router.delete(
    "/incidents/{incident_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={**_NOT_FOUND, **_UNAVAILABLE, 403: {"description": "Manager role required"}},
    dependencies=[Depends(require_manager)],
)
def delete_incident(
    incident_id: int = Path(..., gt=0),
    audit: Auditor = Depends(get_auditor),
) -> Response:
    """Soft-delete one row (manager only)."""
    try:
        old_row = incidents.soft_delete_incident(incident_id, _actor(audit))
    except incidents.IncidentNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="incident not found") from exc
    except RiskCasesUnavailable as exc:
        raise _pg_unavailable(exc) from exc
    audit.record(
        "fund_risk.incident.delete",
        target=incidents.audit_target(old_row),
        old_value=incidents.plain_row({k: v for k, v in old_row.items() if k != "import_raw"}),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {export.XLSX_MEDIA_TYPE: {}}, "description": "xlsx: 台账"}, **_UNAVAILABLE},
)
def export_incidents(
    date_from: Optional[date] = _DATE_FROM,
    date_to: Optional[date] = _DATE_TO,
    channel: Optional[str] = _CHANNEL,
    direction: Optional[Direction] = Query(default=None),
    authority: Optional[Authority] = Query(default=None),
    frozen_scope: Optional[FrozenScope] = Query(default=None),
    pending_result: Optional[bool] = _PENDING,
    q: Optional[str] = _Q,
    sort: str = _SORT,
    order: str = _ORDER,
) -> Response:
    """Every filtered row as xlsx (stored snapshot values, no replica queries)."""
    try:
        rows = incidents.export_rows(
            _filters(date_from, date_to, channel, direction, authority, frozen_scope, pending_result, q),
            sort=sort, order=order,
        )
    except RiskCasesUnavailable as exc:
        raise _pg_unavailable(exc) from exc
    return Response(
        content=export.build_xlsx(rows),
        media_type=export.XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": export.content_disposition(export.filename(date.today()))},
    )
